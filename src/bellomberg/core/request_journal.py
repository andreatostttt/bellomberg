"""Durable request receipts for the ordinary committee execution path.

The preparer's authorization journal and Trade Idea's budget store remain their
own accounting owners. This journal reuses their integer nanoUSD reservation
contract; it never grants a budget, guesses a cost, or retries an uncertain POST.
"""
from contextlib import contextmanager, closing
from contextvars import ContextVar
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4


_REQUEST_SCOPE = ContextVar("bellomberg_request_scope", default=None)


@contextmanager
def request_scope(journal, *, phase, agent=None, round_n=None):
    """Bind a run in this thread/task. ``None`` means externally journaled.

Explicit scopes always disable transport retries, including an external owner.
The only exception (KA 04/10, PM decision, same rule as Trade Idea): with an own
journal, ONE new attempt after a provably unbilled failure (connection never
established, OpenRouter 402 admission), whose row is closed as ``released``.
Workers must bind their blackboard's journal; ContextVar is not thread-global.
"""
    parent = _REQUEST_SCOPE.get() or {}
    observer = (parent.get("journal") or parent.get("external_observer")) if journal is None else None
    token = _REQUEST_SCOPE.set({"journal": journal, "phase": phase,
                                "agent": agent, "round_n": round_n,
                                "external_observer": observer})
    try:
        yield
    finally:
        _REQUEST_SCOPE.reset(token)


def current_request_scope():
    return _REQUEST_SCOPE.get()


def observe_external_request(path, key, *, owned):
    """Reference an existing accounting owner; never copy its cost rows."""
    scope = _REQUEST_SCOPE.get() or {}
    observer = scope.get("journal") or scope.get("external_observer")
    if observer is not None:
        observer.link_external(path, key, owned=owned)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def _evidence(value):
    """Keep malformed provider output as diagnostics, never as valid JSON facts."""
    issues, active = [], set()
    def visit(item, path, depth=0):
        def invalid(kind, detail):
            issues.append({"path": path, "kind": kind, "detail": detail})
            return {kind: detail}
        if item is None or type(item) is bool:
            return item
        if type(item) is str:
            try:
                item.encode("utf-8")
                return item
            except UnicodeError:
                return invalid("invalid_unicode", json.dumps(item, ensure_ascii=True))
        if type(item) is int:
            return item if item.bit_length() <= 14000 else invalid("oversized_integer_bits", item.bit_length())
        if type(item) is float:
            return item if math.isfinite(item) else invalid("invalid_numeric", str(item))
        if type(item) not in (dict, list):
            return invalid("unsupported_value_type", type(item).__name__)
        if depth >= 64:
            return invalid("evidence_depth_limit", 64)
        if id(item) in active:
            return invalid("circular_value_type", type(item).__name__)
        active.add(id(item))
        try:
            if type(item) is list:
                return [visit(child, path + "/" + str(index), depth + 1) for index, child in enumerate(item)]
            result = {}
            for key, child in item.items():
                if type(key) is not str or not isinstance(visit(key, path + "/key", depth + 1), str):
                    invalid("unsupported_key_type", type(key).__name__)
                    continue
                result[key] = visit(child, path + "/" + key, depth + 1)
            return result
        finally:
            active.remove(id(item))
    return visit(value, ""), issues


def _nano(value):
    if value is None or isinstance(value, bool):
        raise ValueError("a measured nonnegative cost is required")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid request cost")
        result = int((amount * 10**9).to_integral_value(rounding=ROUND_CEILING))
        if result > 2**63 - 1:
            raise ValueError("request cost exceeds journal units")
        return result
    except (InvalidOperation, TypeError, OverflowError) as exc:
        raise ValueError("invalid request cost") from exc


_ROUTING_VARIANTS = frozenset({"exacto", "nitro", "floor"})   # OpenRouter: varianti che rispondono col nome base


# Every reasoning field llm_client._reasoning_openai can send for an effort variable,
# including none (adaptive or any effort on z-ai/). Used only to recognise paid Capo work.
_CAPO_REASONING_ON_WIRE = (None, {"enabled": False}, *({"effort": e} for e in
                           ("minimal", "low", "medium", "high", "xhigh", "max")))


# REV G7/R2 (04/10): a legacy form is recognised only when it stands for money spent with
# an answer or for an outcome still unresolved (which rightly blocks). A ``settled`` row
# (paid, no usable answer, reconciled) follows the rule of the new key: a new attempt.
_LEGACY_STATES = ("received", "reserved", "unknown", "incomplete", "overrun")


class RequestBlocked(RuntimeError):
    def __init__(self, message, request_id=None):
        super().__init__(message)
        self.request_id = request_id


class RequestJournal:
    """One immutable run authorization, exact request replay, durable receipts.

    A weekly launch historically has no monetary cap. ``authorized_usd=None``
    records that limitation explicitly, rather than inventing an authorization.
    All requests still require a verified quote and retain unknown reservations.
    """
    def __init__(self, path, *, run_id, authorization, authorized_usd=None, metadata=None):
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("request journal requires a run identity")
        if not isinstance(authorization, dict) or not authorization:
            raise ValueError("explicit existing run authorization required")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.metadata = metadata
        self._quotes = {}
        self._inflight = set()
        self._dichiarate = set()   # richieste incerte gia' dichiarate a log (regola PM 05/10)
        self._external_inflight = set()
        # RV-KA (05/10): failed requests whose outcome could not be written (DB locked...).
        # Fail closed: they block every further request of this run in this process.
        self._write_failures = {}
        self._lock = threading.RLock()
        cap = None if authorized_usd is None else _nano(authorized_usd)
        auth = _json(authorization)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS authorization(
                    id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT NOT NULL,
                    authorization TEXT NOT NULL, cap INTEGER);
                CREATE TABLE IF NOT EXISTS requests(
                    key TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL, reserved INTEGER NOT NULL, cost INTEGER,
                    request TEXT NOT NULL, scope TEXT NOT NULL, response TEXT,
                    receipt TEXT, error TEXT, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS checkpoints(
                    request_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(request_id, sequence));
                CREATE TABLE IF NOT EXISTS external_requests(
                    path TEXT NOT NULL, key TEXT NOT NULL, owned INTEGER NOT NULL,
                    PRIMARY KEY(path,key));
            """)
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO authorization VALUES(1,?,?,?)", (run_id, auth, cap))
            row = db.execute("SELECT run_id,authorization,cap FROM authorization WHERE id=1").fetchone()
            if tuple(row) != (run_id, auth, cap):
                raise ValueError("request journal authorization differs; cannot replace identity or cap")

    @contextmanager
    def _db(self):
        db = (sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=15)
              if getattr(self, "_read_only", False) else sqlite3.connect(str(self.path), timeout=15))
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @classmethod
    def read_summary(cls, path):
        """Read status without schema creation, writes, metadata or providers."""
        journal = cls.__new__(cls)
        journal.path, journal._read_only = Path(path).resolve(), True
        if not journal.path.is_file():
            raise FileNotFoundError(journal.path)
        with journal._db() as db:
            row = db.execute("SELECT run_id FROM authorization WHERE id=1").fetchone()
            if row is None:
                raise ValueError("request journal authorization absent")
            journal.run_id = row[0]
        return journal.summary()

    def link_external(self, path, key, *, owned):
        if type(owned) is not bool or not isinstance(key, str) or len(key) != 64:
            raise ValueError("external accounting requires the original request key")
        path = Path(path).resolve()
        if path == self.path or not path.is_file():
            raise ValueError("external journal must be a distinct existing accounting owner")
        with self._db() as db:
            db.execute("INSERT INTO external_requests(path,key,owned) VALUES(?,?,?) "
                       "ON CONFLICT(path,key) DO UPDATE SET owned=MAX(owned,excluded.owned)",
                       (str(path), key, int(owned)))
        if owned:
            self._external_inflight.add(key)

    def _external_rows(self):
        with self._db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_requests'").fetchone():
                return []
            links = db.execute("SELECT path,key,owned FROM external_requests").fetchall()
        result = []
        for link in links:
            path = Path(link["path"])
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM requests WHERE key=?", (link["key"],)).fetchone()
            if row is None:
                raise ValueError("linked preparer request is missing")
            from bellomberg.valuation.preparation_ai import BudgetedProposer
            BudgetedProposer._validate_receipt(row)
            if (row["state"] not in ("received", "reserved", "unknown", "overrun", "rejected")
                    or type(row["reserved"]) is not int or row["reserved"] < 0
                    or row["cost"] is not None and (type(row["cost"]) is not int or row["cost"] < 0)):
                raise ValueError("invalid linked preparer accounting")
            request = json.loads(row["request"])
            request.pop("provider_max_price", None)
            # Preparation's canonical serialization predates this shared journal.
            if sha256(json.dumps(request, sort_keys=True, ensure_ascii=False,
                                 allow_nan=False, separators=(",", ":")).encode()).hexdigest() != link["key"]:
                raise ValueError("linked preparer request identity differs")
            receipt = json.loads(row["receipt"])
            if row["state"] == "received" and (not receipt.get("response_id")
                    or _nano(receipt.get("cost_usd")) != row["cost"]):
                raise ValueError("linked preparer cost differs from its receipt")
            manual = None
            if row["state"] == "rejected":
                from bellomberg.valuation.preparation_ai import manual_reconciliation_declared
                if "pre_provider_rejection" in receipt:
                    from bellomberg.valuation.preparation_rejections import validate_proof
                    validate_proof(receipt)
                    if row["cost"] is not None:
                        manual = manual_reconciliation_declared(row, receipt)  # v2 -> etichetta v2; v1 -> None
                else:
                    # ZR 05/10: riconciliazione manuale storica, solo la forma esatta; il costo 0
                    # resta nel conteggio e la riga lo DICHIARA.
                    from bellomberg.valuation.preparation_ai import (manual_reconciliation_status,
                                                                      MANUAL_RECONCILIATION_LABEL)
                    manual_reconciliation_status(row, receipt)
                    manual = MANUAL_RECONCILIATION_LABEL
            result.append({"request_id": link["key"], "accounting_owner": "valuation_preparer",
                "journal_ref": sha256(str(path).encode()).hexdigest(), "owned": bool(link["owned"]),
                "state": row["state"], "reserved": row["reserved"], "cost": row["cost"],
                "response_id": receipt.get("response_id"),
                **({"cost_status": manual} if manual else {})})
        return result

    def metadati_modello(self, model):
        """Listino del modello (Models API) letto UNA volta per run e memorizzato: lo usano il
        controllo prezzi e chi deve conoscere i limiti prima dell'invio (tetto del Red Team,
        V0-REDTEAM 05/10). Restituisce (copia, gia_letto); un errore di lettura si propaga e
        non si memorizza (il controllo prezzi successivo riprova e decide)."""
        with self._lock:
            gia_letto = model in self._quotes
            if not gia_letto:
                if self.metadata is None:
                    from bellomberg.valuation.preparation_ai import live_metadata
                    metadata = live_metadata(model)
                else:
                    metadata = self.metadata(model)
                self._quotes[model] = deepcopy(metadata)
            return deepcopy(self._quotes[model]), gia_letto

    def _quote(self, body):
        from bellomberg.core.llm_pricing import preparation_price_ceiling
        model = body.get("model")
        with self._lock:
            self.metadati_modello(model)
            return preparation_price_ceiling(self._quotes[model], model=model,
                                              max_tokens=body.get("max_tokens"))

    def _attempt_labels(self, body, labels):
        """A request settled from the provider's bill (paid, no usable answer) is closed:
        the same work becomes a new attempt with its own key, never a replay or a block.
        KA (04/10): a ``released`` request (provably never billed) is closed the same way."""
        attempt = 0
        with self._db() as db:
            while True:
                current = {**labels, **({"attempt": attempt} if attempt else {})}
                key = sha256(_json({"request": body, "scope": current}).encode()).hexdigest()
                row = db.execute("SELECT state FROM requests WHERE key=?", (key,)).fetchone()
                if row is None or row["state"] not in ("settled", "released"):
                    return current
                attempt += 1

    def prepare(self, body, scope):
        """Return (request_id, exact wire body, saved response or None)."""
        labels = {key: scope.get(key) for key in ("phase", "agent", "round_n")}
        labels = self._attempt_labels(body, labels)
        source = _json(body)
        key = sha256(_json({"request": body, "scope": labels}).encode()).hexdigest()
        candidates = [(key, source)]
        # A cap upgrade must not repay a completed side-stage after a crash.
        # Only these known policy transitions may match an old, otherwise exact
        # request. _replay still verifies its receipt, cost and terminal state.
        legacy_cap = {
            ("reflection", "_reflection", None, 8000): 1000,
            ("action_extraction", "_action_table", None, 16000): 2100,
        }.get((labels["phase"], labels["agent"], labels["round_n"], body.get("max_tokens")))
        if legacy_cap is not None:
            legacy_body = {**body, "max_tokens": legacy_cap}
            legacy_key = sha256(_json({"request": legacy_body, "scope": labels}).encode()).hexdigest()
            candidates.append((legacy_key, _json(legacy_body)))
        # Reflection moved from thinking disabled to effort low (integration of the
        # effort-by-phase policy): a request journaled before that change is replayed
        # with its original reasoning (and cap), never paid a second time.
        if ((labels["phase"], labels["agent"], labels["round_n"]) == ("reflection", "_reflection", None)
                and body.get("reasoning") == {"effort": "low"}):
            for old_cap in dict.fromkeys((body.get("max_tokens"), 1000)):
                for old_reasoning in ({"enabled": False}, {"effort": "minimal"}):
                    old_body = {**body, "max_tokens": old_cap, "reasoning": old_reasoning}
                    old_key = sha256(_json({"request": old_body, "scope": labels}).encode()).hexdigest()
                    candidates.append((old_key, _json(old_body)))
        # G7/C1 (04/10): the Capo is the most expensive call of the run. Its effort moved
        # from adaptive to CAPO_EFFORT, and checkpoints written before that change carry no
        # effort. A Capo request identical in every field but its reasoning (any value an
        # effort variable can put on the wire, or none) is the same paid work: replayed with
        # its original body, never paid a second time.
        if labels["phase"] == "capo":
            for old_reasoning in _CAPO_REASONING_ON_WIRE:
                if old_reasoning == body.get("reasoning"):
                    continue
                old_body = {k: v for k, v in body.items() if k != "reasoning"}
                if old_reasoning is not None:
                    old_body["reasoning"] = old_reasoning
                old_key = sha256(_json({"request": old_body, "scope": labels}).encode()).hexdigest()
                candidates.append((old_key, _json(old_body)))
        with self._lock, self._db() as db:
            found = self._find(db, candidates)
            if found is not None:
                return self._replay(found[0], found[1], labels)
        if getattr(self, "_write_failures", None):
            request_id, cause = next(iter(self._write_failures.items()))
            raise RequestBlocked("outcome of a failed request was not written to the journal (" + cause
                                 + "); further spending blocked until reconciled", request_id)
        for row in self._external_rows():
            if (row["state"] in ("unknown", "overrun")
                    or row["state"] == "reserved" and row["request_id"] not in self._external_inflight):
                raise RequestBlocked("linked preparer request unresolved; further spending blocked", row["request_id"])
        quote = self._quote(body)
        wire = deepcopy(body)
        wire.setdefault("provider", {})["max_price"] = quote["max_price"]
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = self._find(db, candidates)
            if found is not None:
                return self._replay(found[0], found[1], labels)
            rows = db.execute("SELECT * FROM requests").fetchall()
            for row in rows:
                self._verify_row(row)
            unresolved = [row for row in rows if row["state"] in ("unknown", "incomplete", "overrun")
                          or row["state"] == "reserved" and row["request_id"] not in self._inflight]
            # REGOLA PM 05/10/2026 (confermata dal PM, Opus 5.5): un costo incerto si DICHIARA, non
            # ferma la run. La riga resta 'unknown' (riconciliabile, conta nel tetto con la sua
            # prenotazione); il replay di cio' che e' gia' pagato resta sopra (_find): nessuna doppia spesa.
            for row in unresolved:
                if row["request_id"] not in self._dichiarate:
                    self._dichiarate.add(row["request_id"])
                    print("[request_journal] costo/esito incerto DICHIARATO, la run prosegue: richiesta "
                          + str(row["request_id"]) + " stato " + str(row["state"]))
            cap = db.execute("SELECT cap FROM authorization WHERE id=1").fetchone()[0]
            committed = sum(row["cost"] if row["cost"] is not None else row["reserved"] for row in rows)
            if cap is not None and committed + quote["reserve_nano_usd"] > cap:
                raise RequestBlocked("authorized budget insufficient for complete request reservation")
            request_id = uuid4().hex
            db.execute("INSERT INTO requests(key,request_id,state,reserved,request,scope,receipt) "
                       "VALUES(?,?,'reserved',?,?,?,?)", (key, request_id, quote["reserve_nano_usd"],
                       source, _json(labels), _json({"quote": quote, "run_id": self.run_id})))
            # The transaction must commit before this request may reach a provider.
            db.commit()
            self._inflight.add(request_id)
        return request_id, wire, None

    @staticmethod
    def _find(db, candidates):
        """The exact request first; among known legacy forms a received answer wins over an
        unresolved one (two legacy rows mean the same work was already paid twice)."""
        rows = []
        for candidate_key, candidate_source in candidates:
            row = db.execute("SELECT * FROM requests WHERE key=?", (candidate_key,)).fetchone()
            if row is not None:
                if candidate_key == candidates[0][0]:
                    return row, candidate_source
                if row["state"] in _LEGACY_STATES:
                    rows.append((row, candidate_source))
        return next((item for item in rows if item[0]["state"] == "received"), rows[0] if rows else None)

    @staticmethod
    def _replay(row, source, labels):
        RequestJournal._verify_row(row)
        if row["request"] != source or row["scope"] != _json(labels):
            raise ValueError("stored request identity differs from its key")
        if row["state"] != "received" or row["response"] is None or row["cost"] is None:
            raise RequestBlocked("paid request not replayable; original outcome is unresolved", row["request_id"])
        receipt = json.loads(row["receipt"])
        response = json.loads(row["response"])
        if receipt.get("response_sha256") != sha256(row["response"].encode()).hexdigest():
            raise ValueError("saved response checksum differs")
        settlement = receipt.get("settlement")
        if settlement and isinstance(response, dict) and (response.get("usage") or {}).get("cost") is None:
            # REV G7/R3: the stream carried no bill; the cost is the provider's MEASURED bill
            # recorded by the reconciliation, labelled as such (never invented, never zero).
            response["usage"] = {**(response.get("usage") or {}), "cost": row["cost"] / 1e9,
                                 "cost_source": settlement.get("source")}
        return row["request_id"], json.loads(source), response

    @staticmethod
    def _verify_row(row):
        if (row["state"] not in ("reserved", "received", "unknown", "incomplete", "overrun", "settled",
                                 "released")
                or type(row["reserved"]) is not int or row["reserved"] < 0
                or row["cost"] is not None and (type(row["cost"]) is not int or row["cost"] < 0)):
            raise ValueError("invalid stored request accounting")
        request, scope, receipt = (json.loads(row[key]) for key in ("request", "scope", "receipt"))
        if row["state"] == "released":
            # KA (04/10): closed without a bill only with its evidence; never a response.
            release = receipt.get("release") or {}
            if (row["cost"] != 0 or row["response"] is not None or release.get("billable") is not False
                    or not release.get("reason") or not release.get("evidence")):
                raise ValueError("released request lacks its unbilled evidence")
        if sha256(_json({"request": request, "scope": scope}).encode()).hexdigest() != row["key"]:
            raise ValueError("stored request checksum differs")
        if (receipt.get("quote") or {}).get("reserve_nano_usd") != row["reserved"]:
            raise ValueError("stored reservation differs from its quote")
        settled_bill = row["state"] == "settled" or (row["state"] == "received" and "settlement" in receipt)
        if settled_bill:
            settlement = receipt.get("settlement") or {}
            if row["cost"] is None or settlement.get("cost_nano") != row["cost"] or not settlement.get("generation_id"):
                raise ValueError("settled request lacks its provider bill")
        if row["response"] is not None:
            if receipt.get("response_sha256") != sha256(row["response"].encode()).hexdigest():
                raise ValueError("saved response checksum differs")
            response = json.loads(row["response"])
            if (not settled_bill and row["cost"] is not None
                    and _nano((response.get("usage") or {}).get("cost")) != row["cost"]):
                raise ValueError("stored cost differs from its original provider receipt")
        if row["state"] == "received" and (row["response"] is None or row["cost"] is None
                or receipt.get("identity_verified") is not True or receipt.get("complete") is not True):
            raise ValueError("received request lacks an attested receipt")

    def checkpoint(self, request_id, payload):
        """Append stream evidence immediately; a partial stream is never a result."""
        self.checkpoint_many(request_id, [payload])

    def checkpoint_many(self, request_id, payloads):
        """Append several stream chunks in ONE transaction, one row per chunk in order.

        G7/M2 (04/10): one BEGIN IMMEDIATE per SSE chunk, with four desks streaming into
        the same journal, risked a lock past the 15 s timeout (-> unknown request, run
        blocked). Evidence is still never a result: resume relies on the durable
        reservation written before the POST, not on these rows."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row["state"] != "reserved":
                raise ValueError("stream checkpoint lacks an active request")
            seq = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM checkpoints WHERE request_id=?",
                             (request_id,)).fetchone()[0]
            db.executemany("INSERT INTO checkpoints VALUES(?,?,?)",
                           [(request_id, seq + offset, _json(_evidence(payload)[0]))
                            for offset, payload in enumerate(payloads)])

    def receive(self, request_id, response, *, complete=True, diagnostic=None):
        response, evidence_issues = _evidence(response)
        text = _json(response)
        fields = response if isinstance(response, dict) else {}
        usage = fields.get("usage") if isinstance(fields.get("usage"), dict) else {}
        try:
            cost = _nano(usage.get("cost"))
        except ValueError:
            cost = None
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("response has no durable reservation")
            if row["state"] != "reserved":
                if row["response"] == text:
                    return row["state"]
                raise ValueError("cannot overwrite an original provider receipt")
            receipt = json.loads(row["receipt"])
            expected_model = json.loads(row["request"]).get("model")
            base, _, variant = str(expected_model).partition(":")
            # Run 05/10 20:19: OpenRouter risponde col modello BASE a una variante di instradamento
            # (":exacto" -> "deepseek/...-0731"): stessa identita', dichiarata nella ricevuta.
            via_variante = variant in _ROUTING_VARIANTS and fields.get("model") == base
            identity = bool(isinstance(fields.get("id"), str) and fields["id"].strip()
                            and (fields.get("model") == expected_model or via_variante))
            if not identity:
                cost = None  # An unrelated response is not a measured bill for this request.
            choices = fields.get("choices")
            complete = bool(complete and not evidence_issues and isinstance(choices, list) and choices
                            and isinstance(choices[0], dict) and choices[0].get("finish_reason") is not None)
            state = ("unknown" if cost is None or not identity else "overrun" if cost > row["reserved"]
                     else "received" if complete else "incomplete")
            receipt.update({"response_id": fields.get("id"), "model": fields.get("model"),
                "provider": fields.get("provider"), "usage": fields.get("usage"),
                "complete": bool(complete), "identity_verified": identity,
                **({"identity_basis": "routing variant :" + variant + " answered as base model"}
                   if identity and via_variante else {}),
                "response_sha256": sha256(text.encode()).hexdigest(), "diagnostic": _evidence(diagnostic)[0],
                "evidence_issues": evidence_issues})
            db.execute("UPDATE requests SET state=?,cost=?,response=?,receipt=?,error=? WHERE request_id=?",
                       (state, cost, text, _json(receipt), None if complete else "incomplete_response", request_id))
            self._inflight.discard(request_id)
        return state

    def _write_failed(self, request_id, exc):
        """The failure could not be recorded: the row stays 'reserved' on disk. Never leave it
        counted as in flight (that would let the next request through): block, declared."""
        self._inflight.discard(request_id)
        self._write_failures.setdefault(request_id, type(exc).__name__ + ": " + str(exc)[:160])

    def fail(self, request_id, error, *, response=None):
        """Store the first cause. A missing bill keeps the full reservation."""
        try:
            self._fail(request_id, error, response=response)
        except Exception as exc:
            self._write_failed(request_id, exc)
            raise

    def _fail(self, request_id, error, *, response=None):
        if response is not None:
            self.receive(request_id, response, complete=False,
                         diagnostic={"type": type(error).__name__, "message": str(error)[:1000],
                                     "generation_id": getattr(error, "generation_id", None)})
        else:
            with self._lock, self._db() as db:
                db.execute("UPDATE requests SET state='unknown',error=COALESCE(error,?) "
                           "WHERE request_id=? AND state='reserved'",
                           (_json({"type": type(error).__name__, "message": str(error)[:1000],
                                   "status_code": getattr(error, "status_code", None),
                                   "generation_id": getattr(error, "generation_id", None)}), request_id))
                self._inflight.discard(request_id)
        try:
            error.request_id = request_id
        except (AttributeError, TypeError):
            pass

    def release_unbilled(self, request_id, error, *, reason):
        """reserved -> released: the request provably never reached a model (KA 04/10).

        Only the caller's pure classifier (core/unbilled.provably_unbilled) may decide it;
        the evidence (exception type, message, transport phase, status) is stored with the
        row, the cost is 0 and the same work becomes a new attempt (_attempt_labels).
        Returns False, writing nothing, when the row is no longer reserved.
        A failed write blocks the run's further requests (fail closed, RV-KA 05/10)."""
        try:
            return self._release_unbilled(request_id, error, reason=reason)
        except Exception as exc:
            self._write_failed(request_id, exc)
            raise

    def _release_unbilled(self, request_id, error, *, reason):
        evidence = {"type": type(error).__name__, "message": str(error)[:1000],
                    "status_code": getattr(error, "status_code", None),
                    "transport_phase": getattr(error, "transport_phase", None),
                    # RV-KA P2: an id the provider opened stays reconcilable (GET /generation).
                    "generation_id": getattr(error, "generation_id", None)}
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,receipt FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("release has no durable reservation")
            if row["state"] != "reserved":
                return False
            receipt = json.loads(row["receipt"])
            receipt["release"] = {"billable": False, "reason": str(reason)[:300], "evidence": evidence}
            db.execute("UPDATE requests SET state='released',cost=0,receipt=?,error=COALESCE(error,?) "
                       "WHERE request_id=? AND state='reserved'",
                       (_json(receipt), _json(evidence), request_id))
            self._inflight.discard(request_id)
        try:
            error.request_id = request_id
        except (AttributeError, TypeError):
            pass
        return True

    @staticmethod
    def unknown_requests(path):
        """Read-only: unknown native requests with model and the provider id captured for them.

        REV G7/R5 (04/10): a ``reserved`` row is listed too. Read from another process it is
        a request whose outcome nobody recorded (hard crash after the POST): it blocks the
        run like an unknown one and must be visible to the reconciliation. Call it only when
        no run is executing (the HTTP endpoint already refuses a running run)."""
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM requests WHERE state IN ('unknown','reserved')").fetchall()
            out = []
            for row in rows:
                receipt = json.loads(row["receipt"] or "{}")
                error = json.loads(row["error"]) if row["error"] and row["error"].startswith("{") else {}
                first = db.execute("SELECT payload FROM checkpoints WHERE request_id=? ORDER BY sequence LIMIT 5",
                                   (row["request_id"],)).fetchall()
                chunk_ids = [json.loads(item["payload"]).get("id") for item in first
                             if isinstance(json.loads(item["payload"]), dict)]
                diagnostic = receipt.get("diagnostic") if isinstance(receipt.get("diagnostic"), dict) else {}
                out.append({"request_id": row["request_id"], "model": json.loads(row["request"]).get("model"),
                            "reserved": row["reserved"], "state": row["state"],
                            "receipt": {**receipt, "generation_id": error.get("generation_id")
                                        or diagnostic.get("generation_id")
                                        or next((value for value in chunk_ids if value), None)}})
        return out

    @staticmethod
    def settle_unknown(path, request_id, *, charged_usd, generation_id, provider_generation, lookup_sha256):
        """unknown -> settled with the provider's measured bill; idempotent, never overwrites."""
        cost = _nano(charged_usd)
        with closing(sqlite3.connect(str(Path(path).resolve()), timeout=15)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
                if row is None:
                    raise KeyError("request not in this journal")
                receipt = json.loads(row["receipt"])
                if row["state"] == "settled":
                    same = (receipt.get("settlement") or {}).get("generation_id") == generation_id and row["cost"] == cost
                    db.execute("COMMIT")
                    if same:
                        return False
                    raise ValueError("request already settled with a different bill")
                if row["state"] == "received" and "settlement" in receipt:
                    same = receipt["settlement"].get("generation_id") == generation_id and row["cost"] == cost
                    db.execute("COMMIT")
                    if same:
                        return False
                    raise ValueError("request already settled with a different bill")
                if row["state"] not in ("unknown", "reserved"):
                    raise ValueError("only an unknown request can be settled from the provider bill")
                receipt["settlement"] = {"source": "OpenRouter GET /api/v1/generation",
                                         "generation_id": generation_id, "cost_nano": cost,
                                         "lookup_sha256": lookup_sha256, "provider_generation": provider_generation}
                # REV G7/R3 (04/10): a COMPLETE answer with verified identity whose only gap was
                # the bill (stream closed without usage) is the paid work: once the provider's
                # measured bill is recorded it becomes ``received`` and is replayed, never paid
                # again. Without a complete answer the row is ``settled`` (work redone as before).
                complete = (row["response"] is not None and receipt.get("complete") is True
                            and receipt.get("identity_verified") is True)
                db.execute("UPDATE requests SET state=?,cost=?,receipt=? WHERE request_id=? AND state=?",
                           ("received" if complete else "settled", cost, _json(receipt), request_id, row["state"]))
                db.execute("COMMIT")
                return True
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def summary(self):
        with self._db() as db:
            rows = db.execute("SELECT * FROM requests").fetchall()
            cap = db.execute("SELECT cap FROM authorization WHERE id=1").fetchone()[0]
        for row in rows:
            self._verify_row(row)
        unknown = [row for row in rows if row["cost"] is None or row["state"] == "unknown"]
        known = sum(row["cost"] for row in rows if row["cost"] is not None)
        reserved = sum(row["reserved"] for row in rows if row["cost"] is None)
        external = self._external_rows()
        external_owned = [row for row in external if row["owned"]]
        external_unresolved = [row for row in external if row["cost"] is None or row["state"] in ("unknown", "overrun")]
        external_unknown = [row for row in external_unresolved if row["cost"] is None or row["state"] == "unknown"]
        external_known = sum(row["cost"] for row in external_owned if row["cost"] is not None)
        external_reserved = sum(row["reserved"] for row in external_owned if row["cost"] is None)
        native = [{**{key: row[key] for key in ("request_id", "state", "reserved", "cost")},
                   **json.loads(row["scope"]), "response_id": json.loads(row["receipt"]).get("response_id"),
                   "accounting_owner": "run"} for row in rows]
        # KA (04/10): every request closed as provably unbilled, in journal order; an empty
        # list is a measurement (none), not a missing value.
        released = []
        for row in sorted((r for r in rows if r["state"] == "released"), key=lambda r: r["created"]):
            scope = json.loads(row["scope"])
            released.append({"request_id": row["request_id"], "agent": scope.get("agent"),
                             "phase": scope.get("phase"), "round_n": scope.get("round_n"),
                             "reason": (json.loads(row["receipt"]).get("release") or {}).get("reason")})
        return {"run_id": self.run_id, "request_count": len(rows) + len(external_owned),
                "released_requests": released,
                "native_request_count": len(rows), "known_cost_usd": (known + external_known) / 1e9,
                "cost_usd": None if unknown or external_unknown else (known + external_known) / 1e9,
                "unknown_requests": len(unknown) + sum(row["owned"] for row in external_unknown),
                "overrun_requests": sum(row["state"] == "overrun" for row in rows + external_owned),
                "reserved_usd": (reserved + external_reserved) / 1e9,
                "native_reserved_usd": reserved / 1e9,
                "external_unresolved_requests": len(external_unresolved),
                "external_requests": external,
                "authorized_usd": None if cap is None else cap / 1e9,
                "authorization_scope": "native_requests; preparer retains separate immutable authorization",
                "remaining_known_usd": None if cap is None or unknown or external_unknown else (cap - known) / 1e9,
                "requests": native + external_owned}
