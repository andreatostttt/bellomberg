"""Trigger di revisione delle run Trade Idea /4 con giudizio 'watch' (TI-RESEARCH-PIPELINE).

Tabella additiva propria, installata SOLO dalla migrazione esplicita
tools/migrations/migra_trade_idea_watch.py (o da un DB di test usa e getta).
NON e' in trade_idea_store.TABLES: il TradeIdeaStore non si blocca se manca.

Questo modulo non chiama LLM, non scarica prezzi e non manda email: legge le
run gia' instradate a RESEARCH, conserva i trigger e, quando il worker lo
chiede, scrive UNA voce RESEARCH PENDING in `decisions` in una transazione sola.

Timestamp: stesso formato di Trade Idea (trade_idea_store._now), UTC con
suffisso 'Z' e microsecondi. Il Consigliere scrive `decisions.timestamp` in ora
locale ISO secondi: i due formati convivono nella stessa colonna (gia' vero per
le voci Trade Idea), dichiarato.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V4, execution_policy
from bellomberg.storage.trade_idea_store import _now

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS trade_idea_watch_triggers (
      id INTEGER PRIMARY KEY,
      run_id TEXT NOT NULL REFERENCES trade_idea_runs(id),
      decision_id INTEGER NOT NULL REFERENCES decisions(id),
      trigger_index INTEGER NOT NULL CHECK(trigger_index BETWEEN 0 AND 9),
      ticker TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('date','price','condition')),
      date TEXT,
      price_level REAL CHECK(price_level IS NULL OR price_level > 0),
      price_currency TEXT,
      direction TEXT CHECK(direction IS NULL OR direction IN ('at_or_above','at_or_below')),
      reference_price REAL,
      reference_asof TEXT,
      reference_source TEXT,
      condition TEXT,
      reminder_date TEXT,
      what TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN
        ('active','fired','blocked','undated','superseded','cancelled')),
      status_reason TEXT,
      fired_at TEXT,
      fired_decision_id INTEGER REFERENCES decisions(id),
      fired_observation_json TEXT,
      last_check_at TEXT,
      last_check_status TEXT,
      last_check_reason TEXT,
      email_status TEXT NOT NULL DEFAULT 'not_attempted'
        CHECK(email_status IN ('not_attempted','sending','sent','failed','not_configured')),
      email_claimed_at TEXT,
      email_attempts INTEGER NOT NULL DEFAULT 0 CHECK(email_attempts >= 0),
      email_error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(run_id, trigger_index),
      CHECK((kind='date' AND date IS NOT NULL AND price_level IS NULL AND condition IS NULL) OR
            (kind='price' AND price_level IS NOT NULL AND date IS NULL AND condition IS NULL) OR
            (kind='condition' AND condition IS NOT NULL AND date IS NULL AND price_level IS NULL)),
      CHECK((status='fired') = (fired_at IS NOT NULL AND fired_decision_id IS NOT NULL)),
      CHECK((email_status='sending') = (email_claimed_at IS NOT NULL))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ti_watch_status ON trade_idea_watch_triggers(status, kind)",
    "CREATE INDEX IF NOT EXISTS idx_ti_watch_ticker ON trade_idea_watch_triggers(ticker, status)",
    "CREATE INDEX IF NOT EXISTS idx_ti_watch_fired ON trade_idea_watch_triggers(fired_decision_id)",
    """CREATE TRIGGER IF NOT EXISTS ti_watch_input_immutable BEFORE UPDATE OF
      run_id,decision_id,trigger_index,ticker,kind,date,price_level,price_currency,direction,
      reference_price,reference_asof,reference_source,condition,reminder_date,what
      ON trade_idea_watch_triggers
      BEGIN SELECT RAISE(ABORT,'watch trigger input immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS ti_watch_no_delete BEFORE DELETE ON trade_idea_watch_triggers
      BEGIN SELECT RAISE(ABORT,'watch trigger immutable'); END""",
    # Uno stato terminale (fired/superseded/cancelled) non torna mai indietro.
    """CREATE TRIGGER IF NOT EXISTS ti_watch_terminal_status BEFORE UPDATE OF status
      ON trade_idea_watch_triggers
      WHEN OLD.status IN ('fired','superseded','cancelled') AND NEW.status IS NOT OLD.status
      BEGIN SELECT RAISE(ABORT,'watch trigger status terminal'); END""",
)

TABLES = ("trade_idea_watch_triggers",)
REQUIRED_TABLES = ("trade_idea_runs", "decisions") + TABLES
SCHEMA_MISSING = ("schema watch assente: eseguire tools/migrations/migra_trade_idea_watch.py "
                  "(dopo migra_trade_idea.py)")

# Chiavi che core.trade_idea_watch.rows_from_result deve restituire, ne' piu' ne' meno.
ROW_KEYS = frozenset((
    "trigger_index", "kind", "date", "price_level", "price_currency", "direction",
    "reference_price", "reference_asof", "reference_source", "condition",
    "reminder_date", "what", "status", "status_reason"))
_INGEST_STATUSES = frozenset(("active", "blocked", "undated"))
_OPEN_STATUSES = ("active", "blocked", "undated")
_EMAIL_STATUSES = frozenset(("sent", "failed", "not_configured"))
# Ticker occupato: run in corso OPPURE finita con risultato ma non ancora instradata
# (finestra finish_run -> route_result, che resta aperta se il processo cade li': il
# recupero passa dall'API). route_result confronta l'impronta delle decisions del
# ticker: una voce nuova in quella finestra degraderebbe una DCN a research (rilievo RV-P-A 1).
# Una run finita SENZA risultato non viene mai instradata e non occupa.
_BUSY_RUN = ("(technical_status IN ('accepted','running') OR "
             "(destination_kind='none' AND result_json IS NOT NULL))")
_ROW_COLUMNS = ("id", "run_id", "ticker", "kind", "date", "price_level", "price_currency",
                "direction", "condition", "reminder_date", "what", "status")


class WatchConflict(RuntimeError):
    """fire() rifiutato: righe non piu' attive o non della run (niente scritto)."""


class WatchDeferred(RuntimeError):
    """fire() rinviato: run Trade Idea attiva sul ticker (niente scritto)."""


def ensure_schema(conn) -> None:
    """Solo per la migrazione esplicita e i DB di test usa e getta."""
    for statement in SCHEMA:
        conn.execute(statement)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


class TradeIdeaWatchStore:
    """Accesso alla tabella dei trigger; db_path obbligatorio (nessun default al DB vero)."""

    def __init__(self, db_path, *, clock=None):
        if db_path is None:
            raise ValueError("db_path obbligatorio")
        self.db_path = os.fspath(db_path)
        self._clock = clock or _now
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(f"DB assente: {self.db_path}")
        with self._connect(read_only=True) as conn:
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(REQUIRED_TABLES) <= existing:
            raise RuntimeError(SCHEMA_MISSING)

    @contextmanager
    def _connect(self, *, read_only=False):
        path = Path(self.db_path).resolve(strict=True)
        conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"),
                               uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()

    def _at(self):
        value = self._clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("clock must be timezone-aware")
            return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("clock invalid")
        return value

    # ── Popolamento ───────────────────────────────────────────────────────

    @staticmethod
    def _candidates(conn):
        """Run instradate a RESEARCH con result_json e ancora senza righe watch."""
        return conn.execute(
            "SELECT r.* FROM trade_idea_runs r WHERE r.destination_kind='research' "
            "AND r.destination_decision_id IS NOT NULL AND r.result_json IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM trade_idea_watch_triggers w WHERE w.run_id=r.id) "
            "ORDER BY r.created_at, r.id").fetchall()

    def ingest_pending(self, *, today: date) -> dict:
        """Importa i trigger delle run /4 'watch' instradate a research. Idempotente.

        Ogni run in una transazione propria; UNIQUE(run_id,trigger_index) rende
        innocuo un secondo giro. Run che non si possono importare finiscono in
        `errors` (mai saltate in silenzio); run /2-/3 o non-watch in `ignored`.
        """
        from bellomberg.core.trade_idea_watch import rows_from_result
        if type(today) is not date:  # datetime (sottoclasse) rifiutato come nel core
            raise ValueError("today: datetime.date richiesta")
        report = {"runs": [], "inserted": 0, "ignored": [], "errors": []}
        with self._connect() as conn:
            candidates = self._candidates(conn)
            for run in candidates:
                run_id = run["id"]
                try:
                    policy = execution_policy(json.loads(run["request_json"]))
                    result = json.loads(run["result_json"])
                except (ValueError, TypeError) as exc:
                    report["errors"].append({"run_id": run_id, "error": type(exc).__name__,
                                             "reason": "request/result illeggibile"})
                    continue
                if policy != EXECUTION_POLICY_V4 or result.get("judgment") != "watch":
                    report["ignored"].append({"run_id": run_id, "policy": policy,
                                              "judgment": result.get("judgment")})
                    continue
                try:
                    progress = json.loads(run["progress_json"] or "{}")
                except ValueError:
                    progress = None
                reference = None
                if isinstance(progress, dict):
                    quotes = progress.get("candidate_quote_receipts")
                    if isinstance(quotes, dict) and isinstance(quotes.get("final"), dict):
                        reference = quotes["final"]
                # reference=None e' passato com'e': il core dichiara i trigger di prezzo 'blocked'.
                try:
                    rows = rows_from_result(result, run=dict(run), reference=reference, today=today)
                    rows = [self._checked_row(row) for row in rows]
                    if not rows:
                        raise ValueError("run watch senza trigger importabili")
                except Exception as exc:  # dichiarato nel rapporto, mai muto
                    report["errors"].append({"run_id": run_id, "error": type(exc).__name__,
                                             "reason": str(exc)[:300]})
                    continue
                now = self._at()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    inserted = 0
                    for row in rows:
                        cur = conn.execute(
                            "INSERT INTO trade_idea_watch_triggers(run_id,decision_id,trigger_index,"
                            "ticker,kind,date,price_level,price_currency,direction,reference_price,"
                            "reference_asof,reference_source,condition,reminder_date,what,status,"
                            "status_reason,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(run_id,trigger_index) DO NOTHING",
                            (run_id, run["destination_decision_id"], row["trigger_index"],
                             run["ticker"], row["kind"], row["date"], row["price_level"],
                             row["price_currency"], row["direction"], row["reference_price"],
                             row["reference_asof"], row["reference_source"], row["condition"],
                             row["reminder_date"], row["what"], row["status"],
                             row["status_reason"], now, now))
                        inserted += cur.rowcount
                    conn.execute("COMMIT")
                except BaseException as exc:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    if not isinstance(exc, (sqlite3.Error, ValueError)):
                        raise
                    report["errors"].append({"run_id": run_id, "error": type(exc).__name__,
                                             "reason": str(exc)[:300]})
                    continue
                report["inserted"] += inserted
                report["runs"].append({"run_id": run_id, "ticker": run["ticker"],
                                       "rows": len(rows), "inserted": inserted,
                                       "not_active": [{"trigger_index": r["trigger_index"],
                                                       "kind": r["kind"], "status": r["status"],
                                                       "reason": r["status_reason"]}
                                                      for r in rows if r["status"] != "active"]})
        return report

    @staticmethod
    def _checked_row(row):
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            got = sorted(row) if isinstance(row, dict) else type(row).__name__
            raise ValueError(f"riga dal core con chiavi inattese: {got}")
        if row["status"] not in _INGEST_STATUSES:
            raise ValueError(f"stato iniziale non ammesso: {row['status']}")
        if row["status"] != "active" and not row["status_reason"]:
            raise ValueError("stato non attivo senza motivo dichiarato")
        return row

    # ── Letture ───────────────────────────────────────────────────────────

    def active(self) -> list:
        from bellomberg.core.trade_idea_watch import WatchRow
        with self._connect(read_only=True) as conn:
            rows = conn.execute(f"SELECT {','.join(_ROW_COLUMNS)} FROM trade_idea_watch_triggers "
                                "WHERE status='active' ORDER BY id").fetchall()
        return [WatchRow(**{name: row[name] for name in _ROW_COLUMNS}) for row in rows]

    def active_run_tickers(self) -> frozenset:
        with self._connect(read_only=True) as conn:
            return frozenset(r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM trade_idea_runs WHERE " + _BUSY_RUN))

    def active_runs(self) -> dict:
        """Ticker occupati (stesso predicato di fire) -> run piu' vecchia che lo occupa.

        since = started_at, altrimenti created_at (ISO Z). state: 'in_corso' (accepted/running)
        o 'non_instradata' (finita con risultato, destinazione ancora 'none').
        """
        with self._connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT id,ticker,technical_status,COALESCE(started_at,created_at) AS since "
                "FROM trade_idea_runs WHERE " + _BUSY_RUN + " ORDER BY since, id").fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["ticker"], {
                "run_id": r["id"], "technical_status": r["technical_status"], "since": r["since"],
                "state": "in_corso" if r["technical_status"] in ("accepted", "running")
                else "non_instradata"})
        return out

    def fired_rows(self, row_ids) -> list:
        """(WatchRow, osservazione|None) delle righe scattate, per ricostruire l'email di un ritento."""
        from bellomberg.core.trade_idea_watch import WatchRow
        ids = list(row_ids)
        if not ids or any(type(v) is not int or v < 1 for v in ids) or len(ids) > 1000:
            raise ValueError("row_ids non validi")
        marks = ",".join("?" for _ in ids)
        with self._connect(read_only=True) as conn:
            rows = conn.execute(f"SELECT {','.join(_ROW_COLUMNS)},fired_observation_json "
                                f"FROM trade_idea_watch_triggers WHERE id IN ({marks}) "
                                "AND status='fired' ORDER BY id", ids).fetchall()
        if len(rows) != len(ids):
            raise WatchConflict("fired_rows: righe assenti o non scattate")
        return [(WatchRow(**{name: r[name] for name in _ROW_COLUMNS}),
                 None if r["fired_observation_json"] is None else json.loads(r["fired_observation_json"]))
                for r in rows]

    def run_info(self, run_id: str) -> dict:
        """La riga trade_idea_runs (per decision_fields/email_message del core)."""
        with self._connect(read_only=True) as conn:
            row = conn.execute("SELECT * FROM trade_idea_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"run assente: {run_id}")
        return dict(row)

    def summary(self) -> dict:
        """Conteggi per stato e ultimi controlli: per --status del worker."""
        with self._connect(read_only=True) as conn:
            counts = {r[0]: r[1] for r in conn.execute(
                "SELECT status,COUNT(*) FROM trade_idea_watch_triggers GROUP BY status")}
            pending_email = conn.execute(
                "SELECT COUNT(*) FROM trade_idea_watch_triggers WHERE status='fired' "
                "AND email_status IN ('not_attempted','sending','failed')").fetchone()[0]
            last = conn.execute("SELECT MAX(last_check_at) FROM trade_idea_watch_triggers").fetchone()[0]
        return {"by_status": counts, "email_pending": pending_email, "last_check_at": last,
                "attention": len(self.attention_rows()),
                "email_exhausted": len(self.email_exhausted())}

    def email_exhausted(self, *, max_attempts: int = 3) -> list[dict]:
        """Voci scattate la cui email ha esaurito i tentativi: da DICHIARARE a ogni giro."""
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts: intero >= 1")
        with self._connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT id,run_id,ticker,kind,fired_decision_id,email_attempts,email_error "
                "FROM trade_idea_watch_triggers WHERE status='fired' AND email_status='failed' "
                "AND email_attempts>=? ORDER BY id", (max_attempts,)).fetchall()
        return [dict(r) for r in rows]

    def attention_rows(self) -> list[dict]:
        """Trigger che NON verranno mai controllati e che il PM deve sapere (rilievo RV-P-B).

        - status blocked/undated: importati ma fuori dai controlli (motivo dal core);
        - status 'veto_revoked': righe annullate da un veto poi revocato. L'annullamento
          e' terminale (immutabilita'): i trigger NON si riattivano, serve una nuova run.
        Il worker li elenca a ogni giro; finche' la lista non e' vuota il giro non e' 'ok'.
        """
        with self._connect(read_only=True) as conn:
            open_rows = conn.execute(
                "SELECT id,run_id,ticker,kind,status,status_reason FROM trade_idea_watch_triggers "
                "WHERE status IN ('blocked','undated') ORDER BY id").fetchall()
            revoked = conn.execute(
                "SELECT w.id,w.run_id,w.ticker,w.kind,w.decision_id FROM trade_idea_watch_triggers w "
                "JOIN decisions d ON d.id=w.decision_id WHERE w.status='cancelled' "
                "AND COALESCE(d.veto,0)=0 AND d.veto_revoked_at IS NOT NULL ORDER BY w.id").fetchall()
        out = [{"id": r["id"], "run_id": r["run_id"], "ticker": r["ticker"], "kind": r["kind"],
                "status": r["status"], "reason": r["status_reason"]} for r in open_rows]
        out += [{"id": r["id"], "run_id": r["run_id"], "ticker": r["ticker"], "kind": r["kind"],
                 "status": "veto_revoked",
                 "reason": (f"veto revocato (decisione d'origine #{r['decision_id']}): i trigger "
                            "annullati non si riattivano da soli; rilancia la Trade Idea")}
                for r in revoked]
        return out

    def _cutoff(self, seconds):
        if type(seconds) is not int or seconds < 0:
            raise ValueError("secondi: intero >= 0")
        now = datetime.fromisoformat(self._at().replace("Z", "+00:00"))
        return (now - timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z")

    _CLAIMABLE = ("(email_status='not_attempted' OR (email_status='failed' AND email_attempts<?) "
                  "OR (email_status='sending' AND email_claimed_at<=?))")

    def pending_email(self, *, max_attempts: int = 3, stale_after_s: int = 900) -> list[dict]:
        """Righe scattate la cui email va (ri)tentata: mai tentata, failed sotto il tetto,
        o 'sending' rimasta appesa oltre `stale_after_s` (processo caduto durante l'invio:
        consegna INCERTA, dichiarata da claim_email con stale_sending=True).
        Solo lettura: chi invia deve prima vincere claim_email (rilievo RV-P-A 2).
        """
        cutoff = self._cutoff(stale_after_s)
        with self._connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT id,run_id,ticker,kind,fired_decision_id,email_status,email_attempts,"
                "email_claimed_at FROM trade_idea_watch_triggers WHERE status='fired' AND "
                + self._CLAIMABLE + " ORDER BY id", (max_attempts, cutoff)).fetchall()
        return [dict(r) for r in rows]

    # ── Scritture ─────────────────────────────────────────────────────────

    def record_checks(self, outcomes) -> None:
        """Esito dell'ultimo controllo sulle righe attive; nessun cambio di stato."""
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for outcome in outcomes:
                    conn.execute("UPDATE trade_idea_watch_triggers SET last_check_at=?,"
                                 "last_check_status=?,last_check_reason=?,updated_at=? "
                                 "WHERE id=? AND status='active'",
                                 (now, outcome.verdict, outcome.reason, now, outcome.row_id))
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def fire(self, run_id: str, row_ids, fields: dict, observations: dict) -> int:
        """UNA voce RESEARCH PENDING per le righe scattate della run, tutto o niente.

        Dentro BEGIN IMMEDIATE ricontrolla che le righe siano della run e ancora
        'active' (WatchConflict) e che non ci sia una run Trade Idea attiva sul
        ticker (WatchDeferred: la voce nuova invaliderebbe il suo contesto).
        Ritorna l'id della decisione creata.
        """
        ids = list(row_ids)
        if not ids or any(type(v) is not int or v < 1 for v in ids) or len(set(ids)) != len(ids):
            raise ValueError("row_ids non validi")
        if not isinstance(fields, dict) or fields.get("action") != "RESEARCH":
            raise ValueError("fields: action RESEARCH richiesta")
        if fields.get("eur_amount") is not None:
            raise ValueError("una voce da trigger non porta importi")
        rationale = fields.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("fields: rationale richiesto")
        if not isinstance(observations, dict):
            raise ValueError("observations: dict {row_id: osservazione}")
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                marks = ",".join("?" for _ in ids)
                rows = conn.execute(f"SELECT id,run_id,ticker,status FROM trade_idea_watch_triggers "
                                    f"WHERE id IN ({marks})", ids).fetchall()
                if len(rows) != len(ids) or any(r["run_id"] != run_id for r in rows):
                    raise WatchConflict("righe assenti o di un'altra run")
                stale = [r["id"] for r in rows if r["status"] != "active"]
                if stale:
                    raise WatchConflict(f"righe non piu' attive: {stale}")
                vetoed = [r[0] for r in conn.execute(
                    f"SELECT w.id FROM trade_idea_watch_triggers w JOIN decisions d ON d.id=w.decision_id "
                    f"WHERE w.id IN ({marks}) AND COALESCE(d.veto,0)=1", ids)]
                if vetoed:
                    # veto arrivato fra cancel_vetoed e fire: nessuna voce, righe annullate (come cancel_vetoed)
                    vmarks = ",".join("?" for _ in vetoed)
                    conn.execute("UPDATE trade_idea_watch_triggers SET status='cancelled',"
                                 "status_reason='veto PM sulla decisione d''origine #' || decision_id,"
                                 f"updated_at=? WHERE id IN ({vmarks}) AND status='active'", (now, *vetoed))
                    conn.execute("COMMIT")
                    raise WatchConflict(f"decisione d'origine con veto PM: righe {vetoed} annullate, nessuna voce")
                ticker = rows[0]["ticker"]
                busy = conn.execute("SELECT id FROM trade_idea_runs WHERE ticker=? AND "
                                    + _BUSY_RUN + " LIMIT 1", (ticker,)).fetchone()
                if busy is not None:
                    raise WatchDeferred(f"run Trade Idea attiva o non ancora instradata su {ticker}: {busy['id']}")
                run = conn.execute("SELECT memo_id FROM trade_idea_runs WHERE id=?",
                                   (run_id,)).fetchone()
                cursor = conn.execute(
                    "INSERT INTO decisions(memo_id,timestamp,action,ticker,eur_amount,timing,"
                    "confidence,rationale,status) VALUES(?,?,?,?,?,?,?,?,'PENDING')",
                    (run["memo_id"], now, "RESEARCH", ticker, None, fields.get("timing"),
                     fields.get("confidence"), rationale))
                decision_id = cursor.lastrowid
                for row_id in ids:
                    observation = observations.get(row_id)
                    conn.execute("UPDATE trade_idea_watch_triggers SET status='fired',fired_at=?,"
                                 "fired_decision_id=?,fired_observation_json=?,updated_at=? "
                                 "WHERE id=? AND status='active'",
                                 (now, decision_id, None if observation is None else _json(observation),
                                  now, row_id))
                conn.execute("COMMIT")
                return decision_id
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def claim_email(self, row_ids, *, max_attempts: int = 3, stale_after_s: int = 900):
        """Presa atomica dell'invio (rilievo RV-P-A 2: due giri sovrapposti = due email).

        In BEGIN IMMEDIATE: se TUTTE le righe sono fired e prendibili (not_attempted,
        failed sotto il tetto, 'sending' piu' vecchia di stale_after_s) le porta a
        'sending' con email_claimed_at=adesso e ritorna
        {"previous": {row_id: stato_precedente}, "stale_sending": bool}.
        Altrimenti None e nessuna scrittura (un altro giro la sta inviando o e' gia' fatta).
        stale_sending=True = il giro precedente e' caduto durante l'invio: consegna incerta.
        """
        ids = list(row_ids)
        if not ids or any(type(v) is not int or v < 1 for v in ids) or len(set(ids)) != len(ids):
            raise ValueError("row_ids non validi")
        cutoff = self._cutoff(stale_after_s)
        now = self._at()
        marks = ",".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    f"SELECT id,email_status FROM trade_idea_watch_triggers WHERE id IN ({marks}) "
                    "AND status='fired' AND " + self._CLAIMABLE,
                    (*ids, max_attempts, cutoff)).fetchall()
                if len(rows) != len(ids):
                    conn.execute("ROLLBACK")
                    return None
                conn.execute(f"UPDATE trade_idea_watch_triggers SET email_status='sending',"
                             f"email_claimed_at=?,updated_at=? WHERE id IN ({marks})", (now, now, *ids))
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        previous = {r["id"]: r["email_status"] for r in rows}
        return {"previous": previous, "stale_sending": "sending" in previous.values()}

    def mark_email(self, row_ids, status: str, error: str | None) -> None:
        """Esito dell'email (dopo il COMMIT di fire). sent/failed contano un tentativo."""
        if status not in _EMAIL_STATUSES:
            raise ValueError(f"email status non ammesso: {status}")
        if status == "failed" and not error:
            raise ValueError("email failed senza errore dichiarato")
        ids = list(row_ids)
        if not ids or any(type(v) is not int or v < 1 for v in ids):
            raise ValueError("row_ids non validi")
        now = self._at()
        bump = 1 if status in ("sent", "failed") else 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                marks = ",".join("?" for _ in ids)
                fired = conn.execute(f"SELECT COUNT(*) FROM trade_idea_watch_triggers WHERE id IN ({marks}) "
                                     "AND status='fired'", ids).fetchone()[0]
                if fired != len(ids):
                    raise WatchConflict("email solo su righe scattate")
                sent = [r[0] for r in conn.execute(
                    f"SELECT id FROM trade_idea_watch_triggers WHERE id IN ({marks}) "
                    "AND email_status='sent'", ids)]
                if sent:
                    # una consegna avvenuta non si riscrive mai (giro sovrapposto): dichiarato.
                    raise WatchConflict(f"email gia' inviata per le righe {sent}: stato {status} rifiutato")
                conn.execute(f"UPDATE trade_idea_watch_triggers SET email_status=?,email_claimed_at=NULL,"
                             f"email_attempts=email_attempts+?,email_error=?,updated_at=? "
                             f"WHERE id IN ({marks})",
                             (status, bump, (error or None) and str(error)[:1000], now, *ids))
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def cancel_vetoed(self) -> int:
        """Veto PM (veto=1) sulla voce RESEARCH d'origine -> 'cancelled'.

        Solo il veto annulla: EXPIRED (auto-archivio a 30 giorni) NON tocca i trigger.
        """
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE trade_idea_watch_triggers SET status='cancelled',"
                    "status_reason='veto PM sulla decisione d''origine #' || decision_id,updated_at=? "
                    f"WHERE status IN ({','.join('?' for _ in _OPEN_STATUSES)}) AND decision_id IN "
                    "(SELECT id FROM decisions WHERE COALESCE(veto,0)=1)",
                    (now, *_OPEN_STATUSES))
                conn.execute("COMMIT")
                return cur.rowcount
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def supersede_older(self) -> int:
        """Trigger aperti di una run superata da una run piu' nuova, gia' instradata, sullo stesso ticker."""
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE trade_idea_watch_triggers SET status='superseded',"
                    "status_reason='superata dalla run ' || (SELECT n.id FROM trade_idea_runs n, "
                    " trade_idea_runs o WHERE o.id=trade_idea_watch_triggers.run_id AND "
                    " n.ticker=o.ticker AND n.destination_kind!='none' AND "
                    " (n.created_at>o.created_at OR (n.created_at=o.created_at AND n.id>o.id)) "
                    " ORDER BY n.created_at DESC, n.id DESC LIMIT 1),updated_at=? "
                    f"WHERE status IN ({','.join('?' for _ in _OPEN_STATUSES)}) AND EXISTS "
                    "(SELECT 1 FROM trade_idea_runs n, trade_idea_runs o "
                    " WHERE o.id=trade_idea_watch_triggers.run_id AND n.ticker=o.ticker "
                    " AND n.destination_kind!='none' AND "
                    " (n.created_at>o.created_at OR (n.created_at=o.created_at AND n.id>o.id)))",
                    (now, *_OPEN_STATUSES))
                conn.execute("COMMIT")
                return cur.rowcount
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def lookup_decisions(self, decision_ids) -> dict:
        """Provenienza per la pagina Decisioni delle voci create da un trigger."""
        ids = list(decision_ids)
        if any(type(v) is not int or v < 1 for v in ids) or len(ids) > 1000:
            raise ValueError("decision IDs invalid or batch too large")
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self._connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT w.fired_decision_id,w.id,w.run_id,w.ticker,w.kind,w.decision_id,"
                "w.fired_at,w.email_status,r.memo_id FROM trade_idea_watch_triggers w "
                "JOIN trade_idea_runs r ON r.id=w.run_id "
                f"WHERE w.fired_decision_id IN ({marks}) ORDER BY w.id", ids).fetchall()
        output = {}
        for row in rows:
            item = output.setdefault(row["fired_decision_id"], {
                "origin": "trade_idea_trigger", "run_id": row["run_id"], "ticker": row["ticker"],
                "memo_id": row["memo_id"], "source_decision_id": row["decision_id"],
                "fired_at": row["fired_at"], "trigger_ids": [], "kinds": [], "email_status": []})
            item["trigger_ids"].append(row["id"])
            item["kinds"].append(row["kind"])
            item["email_status"].append(row["email_status"])
        return output
