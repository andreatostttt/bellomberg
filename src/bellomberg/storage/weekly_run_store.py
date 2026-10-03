"""Durable checkpoints for the ordinary weekly run; no provider or tool imports."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import threading
import uuid


CONTRACT_VERSION = 1
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_BB_FIELDS = ("data", "tool_log", "tool_receipts", "usage_log", "orari_report",
              "valuation_results", "valuation_attempts", "valuation_generations",
              "specialist_checkpoints", "specialist_status")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(value):
    return sha256(_json(value).encode("utf-8")).hexdigest()


def current_book_identity(db):
    with db._conn() as conn:
        return {"positions": [dict(row) for row in conn.execute("SELECT * FROM positions ORDER BY ticker")],
                "cash": [dict(row) for row in conn.execute("SELECT * FROM cash_state ORDER BY singleton_id")]}


def costs_unresolved(costs):
    """A reused uncertain preparer receipt also blocks, without charging it twice."""
    return bool(costs.get("unavailable") or costs.get("unknown_requests") or
                costs.get("external_unresolved_requests") or
                any(request.get("state") not in ("received", "rejected") or request.get("cost") is None
                    for request in costs.get("requests", []) + costs.get("external_requests", [])))


class WeeklyRunBlocked(RuntimeError):
    pass


class WeeklyRunStore:
    """The memo remains the run identity; attempts never replace earlier checkpoints."""

    def __init__(self, db, memo_id, *, context=None):
        self.db, self.memo_id = db, int(memo_id)
        self._mutex = threading.RLock()
        self._claimed = False
        if context is not None:
            with db._conn() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS weekly_runs (
                    memo_id INTEGER PRIMARY KEY REFERENCES memos(id), run_id TEXT UNIQUE NOT NULL,
                    context_json TEXT NOT NULL, context_sha256 TEXT NOT NULL,
                    state_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL)""")
                conn.execute("""CREATE TABLE IF NOT EXISTS weekly_checkpoints (
                    memo_id INTEGER NOT NULL REFERENCES memos(id), stage TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(memo_id,stage))""")
                memo = conn.execute("SELECT notes FROM memos WHERE id=?", (self.memo_id,)).fetchone()
                if memo is None or str(memo[0] or "").startswith("trade_idea:"):
                    raise WeeklyRunBlocked("Identita memo settimanale non valida")
                encoded = _json(context)
                conn.execute("INSERT INTO weekly_runs VALUES (?,?,?,?,?,?,?)",
                             (self.memo_id, str(uuid.uuid4()), encoded,
                              sha256(encoded.encode("utf-8")).hexdigest(),
                              _json({"status": "ready", "analytical_status": "pending",
                                     "artifact_status": "pending", "delivery_status": "pending",
                                     "operational_status": "not_evaluated", "attempts": [],
                                     "first_error": None, "errors": [], "phase": "preflight"}),
                              "{}", _now()))
        row = self._row()
        self.run_id = row["run_id"]
        self.context = json.loads(row["context_json"])
        if digest(self.context) != row["context_sha256"]:
            raise WeeklyRunBlocked("Contesto originale modificato: ripresa rifiutata")
        if self.context.get("contract_version") != CONTRACT_VERSION:
            raise WeeklyRunBlocked("Contratto checkpoint non compatibile")

    def _row(self):
        with self.db._conn() as conn:
            try:
                row = conn.execute("SELECT * FROM weekly_runs WHERE memo_id=?", (self.memo_id,)).fetchone()
            except Exception as exc:
                raise WeeklyRunBlocked("Checkpoint nativo assente: il memo storico non puo riprendere AI") from exc
        if row is None:
            raise WeeklyRunBlocked("Checkpoint nativo assente per il memo selezionato")
        return dict(row)

    def status(self):
        row = self._row()
        result = json.loads(row["state_json"])
        with self.db._conn() as conn:
            stages = [r[0] for r in conn.execute(
                "SELECT stage FROM weekly_checkpoints WHERE memo_id=? ORDER BY created_at", (self.memo_id,))]
        result.update(memo_id=self.memo_id, run_id=row["run_id"], completed_stages=stages,
                      research_started_at=self.context.get("research_started_at"),
                      context_sha256=row["context_sha256"], updated_at=row["updated_at"])
        from bellomberg.core.research_analysis import is_research_mode
        research = is_research_mode(self.context['contract'])
        if research:
            result.update(analysis_mode=self.context['contract']['analysis_mode'], workbook_status='not_required')
        active = self.worker_active()
        costs = result.get("request_costs") or {}
        journal_path = Path(self.db.db_path).with_name("weekly-" + self.run_id + "-requests.sqlite")
        journal_required = result.get("request_journal_path") is not None or bool(costs)
        if journal_required or journal_path.is_file():
            try:
                from bellomberg.core.request_journal import RequestJournal
                costs = RequestJournal.read_summary(journal_path)
                if costs.get("run_id") != self.run_id:
                    raise ValueError("Identita registro richieste diversa dalla run")
            except Exception as exc:
                costs = {**costs, "cost_usd": None, "unavailable": True,
                         "error": "Registro richieste non verificabile: " + type(exc).__name__}
            result["request_costs"] = costs
        uncertain = costs_unresolved(costs)
        snapshot = json.loads(row["snapshot_json"])
        payload = snapshot.get("payload", {})
        changed = bool(snapshot and snapshot.get("sha256") != digest(payload))
        incomplete_report = any(checkpoint.get("status") in ("failed", "truncated")
                                for checkpoint in payload.get("specialist_checkpoints", {}).values()
                                if isinstance(checkpoint, dict))
        def unresolved_tool(tool):
            # Public research is independently journaled: resuming it replays
            # verified bytes or returns the explicit unresolved GET. The issuer
            # binding must already belong to this exact weekly run.
            from bellomberg.agents.company_research_tools import TOOL_NAMES
            if not isinstance(tool, dict) or tool.get('name') not in TOOL_NAMES:
                return True
            ticker = (tool.get('input') or {}).get('ticker')
            return not (isinstance(ticker, str) and self.get('company-source-identity:' + ticker.upper()))
        uncertain_tool = any(unresolved_tool(tool)
                             for checkpoint in payload.get("specialist_checkpoints", {}).values()
                             if isinstance(checkpoint, dict)
                             for tool in (checkpoint.get("inflight_tools") or {}).values())
        try:
            book_compatible = current_book_identity(self.db) == self.context["book_identity"]
        except Exception:
            book_compatible = False
        reason = ("Worker ancora attivo" if active else
                  "Richieste o costi incerti: riconciliazione necessaria" if uncertain else
                  "Snapshot modificato: ripresa rifiutata" if changed else
                  "Esito tool incerto: riconciliazione necessaria senza ripetere il dispatch" if uncertain_tool else
                  "Book cambiato o non verificabile: disponibile soltanto la consegna storica" if not book_compatible else
                  "Risposta analitica incompleta conservata: revisione esplicita necessaria" if incomplete_report else
                  "Invio precedente incerto: verifica della consegna necessaria, nessun reinvio automatico"
                  if (result.get("email_delivery") or {}).get("state") in ("sending", "uncertain") else None)
        result["worker_active"] = active
        result["book_compatible"] = book_compatible
        result["blocked_reason"] = reason
        artifact_issues = []
        for receipt in result.get("artifacts", []):
            try:
                path = Path(receipt["path"])
                if not path.is_file() or sha256(path.read_bytes()).hexdigest() != receipt["sha256"]:
                    artifact_issues.append("Artefatto mancante o modificato: " + path.name)
            except (OSError, ValueError, KeyError, TypeError):
                artifact_issues.append("Ricevuta artefatto non verificabile")
        if artifact_issues:
            result.update(status="incomplete", artifact_status="recovery_needed", artifact_issues=artifact_issues)
        if uncertain and result.get("status") == "completed":
            result.update(status="incomplete", operational_status="blocked")
        result["resume_available"] = research and result.get("status") != "completed" and reason is None
        if not research:
            result["blocked_reason"] = 'Run Excel in archivio: disponibile solo il recupero dei risultati gia prodotti' + (
                '; ' + reason if reason else '')
        result["delivery_recovery_available"] = "memo_validated" in stages and not active and not changed
        expected = ["priming"]
        for round_n in (0, 1, 2):
            expected += ["desk:" + name + ":" + str(round_n) for name in self.context["contract"]["roster"]
                         if round_n != 2 or name in self.context["contract"]["r2_specialists"]]
        expected += ["research_dossier" if research else "valuation_coverage", "red_team", "synthesis_context", "capo", "memo_validated",
                     "reflection", "render_context", "decisions_finalized"]
        result["remaining_work"] = [stage for stage in expected if stage not in stages]
        if result.get("artifact_status") != "available":
            result["remaining_work"].append("artifacts")
        if result.get("delivery_status") != "sent":
            result["remaining_work"].append("email_reconciliation" if
                (result.get("email_delivery") or {}).get("state") in ("sending", "uncertain")
                else "requested_email_delivery" if result.get("delivery_requested") else "optional_email_delivery")
        return result

    def worker_active(self):
        key = str(Path(self.db.db_path).resolve()) + ":" + str(self.memo_id)
        with _LOCKS_GUARD:
            guard = _LOCKS.get(key)
            if guard is not None and guard.locked():
                return True
        path = Path(self.db.db_path).with_name(Path(self.db.db_path).name + ".weekly-" + str(self.memo_id) + ".lock")
        if not path.is_file():
            return False
        try:
            with open(path, "r+b") as handle:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
        except OSError:
            return True

    def update(self, **changes):
        with self._mutex, self.db._conn() as conn:
            row = conn.execute("SELECT state_json FROM weekly_runs WHERE memo_id=?", (self.memo_id,)).fetchone()
            state = json.loads(row[0])
            state.update(changes)
            conn.execute("UPDATE weekly_runs SET state_json=?,updated_at=? WHERE memo_id=?",
                         (_json(state), _now(), self.memo_id))

    def get(self, stage):
        with self.db._conn() as conn:
            row = conn.execute("SELECT payload_json,payload_sha256 FROM weekly_checkpoints "
                               "WHERE memo_id=? AND stage=?", (self.memo_id, stage)).fetchone()
        if row is None:
            return None
        if sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
            raise WeeklyRunBlocked("Checkpoint modificato: " + stage)
        return json.loads(row[0])

    def save_snapshot(self, bb):
        with bb._lock, self._mutex:
            snapshot = {name: getattr(bb, name, {} if name in (
                "data", "orari_report", "valuation_results", "specialist_checkpoints", "specialist_status") else [])
                        for name in _BB_FIELDS}
            encoded = _json({"payload": snapshot, "sha256": digest(snapshot)})
            with self.db._conn() as conn:
                conn.execute("UPDATE weekly_runs SET snapshot_json=?,updated_at=? WHERE memo_id=?",
                             (encoded, _now(), self.memo_id))

    def restore(self, bb):
        saved = json.loads(self._row()["snapshot_json"])
        if not saved:
            return
        snapshot = saved.get("payload")
        if not isinstance(snapshot, dict) or saved.get("sha256") != digest(snapshot):
            raise WeeklyRunBlocked("Snapshot di ripresa modificato")
        for name, value in snapshot.items():
            if name in ("data", "orari_report") and isinstance(value, dict):
                value = {key: ({int(r) if str(r).isdigit() else r: text for r, text in item.items()}
                               if isinstance(item, dict) and (name == "orari_report"
                                   or not key.startswith("_") or key == "_red_team") else item)
                         for key, item in value.items()}
            if name in _BB_FIELDS:
                setattr(bb, name, value)

    def complete(self, stage, payload, bb=None):
        encoded = _json(payload)
        with (bb._lock if bb is not None else nullcontext()), self._mutex, self.db._conn() as conn:
            existing = conn.execute("SELECT payload_json FROM weekly_checkpoints WHERE memo_id=? AND stage=?",
                                    (self.memo_id, stage)).fetchone()
            if existing is not None and existing[0] != encoded:
                raise WeeklyRunBlocked("Checkpoint completo immutabile: " + stage)
            conn.execute("INSERT OR IGNORE INTO weekly_checkpoints VALUES (?,?,?,?,?)",
                         (self.memo_id, stage, encoded, sha256(encoded.encode("utf-8")).hexdigest(), _now()))
            if bb is not None:
                with bb._lock:
                    snapshot = {name: getattr(bb, name, {} if name in (
                        "data", "orari_report", "valuation_results", "specialist_checkpoints", "specialist_status") else [])
                                for name in _BB_FIELDS}
                    conn.execute("UPDATE weekly_runs SET snapshot_json=?,updated_at=? WHERE memo_id=?",
                                 (_json({"payload": snapshot, "sha256": digest(snapshot)}), _now(), self.memo_id))

    def fail(self, error, *, phase=None, desk=None, round_n=None):
        with self._mutex:
            state = self.status()
            cause = {"type": type(error).__name__, "message": str(error)[:2000],
                     "phase": phase or state.get("phase"), "desk": desk,
                     "round": round_n, "request_id": getattr(error, "request_id", None), "at": _now()}
            errors = state.get("errors", [])
            if not errors or any(errors[-1].get(k) != cause.get(k) for k in ("type", "message", "phase", "desk")):
                errors.append(cause)
            self.update(status="incomplete", first_error=state.get("first_error") or cause,
                        errors=errors, last_error=cause,
                        analytical_status=("complete" if self.get("memo_validated") else "incomplete"),
                        operational_status="blocked")

    @contextmanager
    def claim(self, *, mode):
        """OS lock is released by process death; no elapsed-time takeover of a live worker."""
        key = str(Path(self.db.db_path).resolve()) + ":" + str(self.memo_id)
        with _LOCKS_GUARD:
            guard = _LOCKS.setdefault(key, threading.Lock())
        if not guard.acquire(blocking=False):
            raise WeeklyRunBlocked("Ripresa gia in corso per questo memo")
        handle = None
        try:
            lock_path = Path(self.db.db_path).with_name(Path(self.db.db_path).name + ".weekly-" + str(self.memo_id) + ".lock")
            handle = open(lock_path, "a+b")
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise WeeklyRunBlocked("Worker attivo: doppia ripresa rifiutata") from exc
            self._claimed = True
            state = self.status()
            attempts = state.get("attempts", []) + [{"attempt_id": str(uuid.uuid4()), "mode": mode,
                                                      "started_at": _now(), "pid": os.getpid()}]
            self.update(attempts=attempts, status="running", last_error=None)
            yield self
        finally:
            self._claimed = False
            if handle is not None:
                handle.close()
            guard.release()


def get_weekly_run_status(db, memo_id):
    return WeeklyRunStore(db, memo_id).status()
