"""Store dei trigger watch Trade Idea: solo SQLite temporaneo, ticker e numeri inventati."""
import json
import re
import sqlite3
from datetime import date, datetime, timezone

import pytest

from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V3, EXECUTION_POLICY_V4
from bellomberg.core.trade_idea_watch import Outcome, WatchRow
from bellomberg.storage import trade_idea_watch_store as ws
from bellomberg.storage.trade_idea_watch_store import (
    SCHEMA_MISSING, TradeIdeaWatchStore, WatchConflict, WatchDeferred,
)
from tools.migrations import migra_trade_idea, migra_trade_idea_watch

# Schema delle tabelle legacy copiato dal DB reale (solo DDL, nessun dato).
BASE_SCHEMA = """
CREATE TABLE memos (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, title TEXT,
  full_markdown TEXT, pdf_path TEXT, appendix_path TEXT, dcf_files TEXT, capo_tokens_in INTEGER,
  capo_tokens_out INTEGER, portfolio_nav_eur REAL, notes TEXT,
  output_language TEXT CHECK(output_language IN ('it','en')));
CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, memo_id INTEGER, timestamp TEXT NOT NULL,
  action TEXT NOT NULL, ticker TEXT, eur_amount REAL, timing TEXT, confidence TEXT, rationale TEXT,
  status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING','EXECUTED','SKIPPED','EXPIRED','PARTIAL')),
  pm_feedback TEXT, outcome_pct REAL, outcome_eur REAL, outcome_notes TEXT,
  closed_at TEXT, archive_override INTEGER, veto INTEGER DEFAULT 0, veto_reason TEXT, veto_at TEXT,
  veto_revoked_at TEXT, FOREIGN KEY(memo_id) REFERENCES memos(id));
CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL UNIQUE, nome TEXT,
  quantita REAL NOT NULL DEFAULT 0, prezzo_medio REAL, valuta TEXT DEFAULT 'EUR', data_apertura TEXT,
  tesi TEXT, temi_monitoraggio TEXT, note TEXT, last_updated TEXT, is_active INTEGER DEFAULT 1);
CREATE TABLE position_prices (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
  prezzo REAL NOT NULL, valuta TEXT DEFAULT 'USD', source TEXT, timestamp TEXT DEFAULT (datetime('now')));
CREATE TABLE cash_state (singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
  balance_cents INTEGER NOT NULL, updated_at TEXT NOT NULL, source TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE trade_history (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('BUY','SELL','TRIM','ADD','DIVIDEND')), quantita REAL NOT NULL,
  prezzo REAL NOT NULL, valuta TEXT DEFAULT 'EUR', data TEXT NOT NULL, note TEXT, pm_rationale TEXT,
  linked_decision_id INTEGER, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE decision_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER NOT NULL,
  autore TEXT NOT NULL CHECK(autore IN ('PM','AI')), testo TEXT NOT NULL,
  timestamp TEXT DEFAULT (datetime('now','localtime')), FOREIGN KEY(decision_id) REFERENCES decisions(id));
CREATE TABLE pm_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, memo_id INTEGER, decision_id INTEGER,
  specialist TEXT, feedback_text TEXT NOT NULL,
  sentiment TEXT CHECK(sentiment IN ('POSITIVE','NEGATIVE','NEUTRAL','SUGGESTION')),
  timestamp TEXT DEFAULT (datetime('now')), FOREIGN KEY(memo_id) REFERENCES memos(id),
  FOREIGN KEY(decision_id) REFERENCES decisions(id));
INSERT INTO decisions(timestamp,action,ticker,rationale,status)
VALUES('2026-09-01T10:00:00','RESEARCH','OTHERZZ','legacy untouched','PENDING');
"""

TODAY = date(2026, 10, 4)
TRIGGERS = [
    {"kind": "date", "date": "2027-01-15", "price_level": None, "condition": None,
     "what": "Risultati annuali ZZTEST"},
    {"kind": "price", "date": None, "price_level": 150.0, "condition": None,
     "what": "Rottura del livello"},
    {"kind": "condition", "date": None, "price_level": None, "condition": "Nuovo contratto firmato",
     "what": "Verificare il backlog"},
]
REFERENCE = {"price": "123.45", "price_asof": "2026-10-02", "currency": "EUR",
             "source": "yfinance daily Close (not an intraday quote)", "status": "ready"}
UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def make_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
    return path


@pytest.fixture
def raw_db(tmp_path, monkeypatch):
    """DB con le sole tabelle Trade Idea (migra_trade_idea), senza la tabella watch."""
    path = make_db(tmp_path / "isolated.db")
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    monkeypatch.setattr(migra_trade_idea_watch, "backend_alive", lambda: False)
    migra_trade_idea.migra(path, apply=True)
    return path


@pytest.fixture
def db(raw_db):
    receipt = migra_trade_idea_watch.migra(raw_db, apply=True)
    assert receipt["applicazione"]["preesistente_invariato"]
    return raw_db


def conn_of(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def add_run(path, run_id, *, ticker="ZZTEST", judgment="watch", policy=EXECUTION_POLICY_V4,
            triggers=TRIGGERS, reference=REFERENCE, status="completed", created="2026-10-01T10:00:00.000000Z",
            routed="research", currency="EUR", veto=0, decision_status="PENDING", result=None,
            with_result=True):
    """Una run Trade Idea gia' instradata (memo + decisione d'origine) scritta a mano."""
    request = {"ticker": ticker}
    if policy is not None:
        request.update(execution_policy=policy, analysis_mode=RESEARCH_ANALYSIS_MODE)
    if result is None:
        result = {"judgment": judgment, "summary": "sintesi inventata"}
        if triggers is not None:
            result["review_triggers"] = triggers
    progress = {"candidate_quote_receipts": {"initial": reference, "final": reference}} if reference else {}
    with conn_of(path) as conn:
        memo_id = decision_id = None
        if routed != "none":
            memo_id = conn.execute("INSERT INTO memos(timestamp,title) VALUES(?,?)",
                                   (created, f"TI {run_id}")).lastrowid
            decision_id = conn.execute(
                "INSERT INTO decisions(memo_id,timestamp,action,ticker,rationale,status,veto) "
                "VALUES(?,?,?,?,?,?,?)",
                (memo_id, created, "RESEARCH" if routed == "research" else "BUY", ticker,
                 "origine inventata", decision_status, veto)).lastrowid
        conn.execute(
            "INSERT INTO trade_idea_runs(id,idempotency_key,request_sha256,request_json,ticker,currency,"
            "language,view_text,view_origin,models_json,catalog_snapshot_json,budget_limit_usd,"
            "context_json,technical_status,phase,progress_json,result_json,worker_token,"
            "destination_kind,destination_decision_id,memo_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, "idem-" + run_id, "0" * 64, json.dumps(request), ticker, currency, "it", "vista",
             "pm", "{}", "{}", "1.00", "{}", status, "result", json.dumps(progress),
             json.dumps(result) if with_result and status not in ("accepted", "running") else None,
             "tok" if status == "running" else None, routed, decision_id, memo_id, created, created))
    return decision_id


def store(path):
    return TradeIdeaWatchStore(path, clock=lambda: datetime(2026, 10, 4, 8, 30, tzinfo=timezone.utc))


def rows(path):
    with conn_of(path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM trade_idea_watch_triggers ORDER BY id")]


def count_decisions(path):
    with conn_of(path) as conn:
        return conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]


FIELDS = {"action": "RESEARCH", "eur_amount": None, "confidence": "BASSA",
          "timing": "Rivedere ora", "rationale": "[TRIGGER] livello inventato attraversato"}


# ── Schema ───────────────────────────────────────────────────────────────

def test_store_requires_db_path_and_schema(raw_db):
    with pytest.raises(ValueError, match="db_path obbligatorio"):
        TradeIdeaWatchStore(None)
    with pytest.raises(RuntimeError) as exc:
        TradeIdeaWatchStore(raw_db)
    assert str(exc.value) == SCHEMA_MISSING
    assert "trade_idea_watch_triggers" not in __import__(
        "bellomberg.storage.trade_idea_store", fromlist=["TABLES"]).TABLES


def test_schema_checks_reject_incoherent_rows(db):
    add_run(db, "run-a")
    insert = ("INSERT INTO trade_idea_watch_triggers(run_id,decision_id,trigger_index,ticker,kind,date,"
              "price_level,condition,what,status,fired_at,fired_decision_id,created_at,updated_at) "
              "VALUES('run-a',2,?,'ZZTEST',?,?,?,?,'x',?,?,?,'t','t')")
    with conn_of(db) as conn:
        for args in ((0, "date", None, 10.0, None, "active", None, None),     # kind date senza data
                     (0, "price", "2027-01-01", 10.0, None, "active", None, None),  # due campi
                     (0, "date", "2027-01-01", None, None, "fired", None, None),    # fired senza voce
                     (0, "date", "2027-01-01", None, None, "active", "t", 2),       # voce senza fired
                     (10, "date", "2027-01-01", None, None, "active", None, None)): # indice > 9
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(insert, args)


def test_input_immutable_no_delete_and_terminal_status(db):
    add_run(db, "run-a")
    store(db).ingest_pending(today=TODAY)
    with conn_of(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="input immutable"):
            conn.execute("UPDATE trade_idea_watch_triggers SET what='altro' WHERE id=1")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM trade_idea_watch_triggers WHERE id=1")
        conn.execute("UPDATE trade_idea_watch_triggers SET status='cancelled' WHERE id=1")
        with pytest.raises(sqlite3.IntegrityError, match="terminal"):
            conn.execute("UPDATE trade_idea_watch_triggers SET status='active' WHERE id=1")


# ── Popolamento ──────────────────────────────────────────────────────────

def test_ingest_imports_watch_v4_rows(db):
    origin = add_run(db, "run-a")
    report = store(db).ingest_pending(today=TODAY)
    assert report["inserted"] == 3 and report["errors"] == [] and report["ignored"] == []
    got = rows(db)
    assert [r["kind"] for r in got] == ["date", "price", "condition"]
    assert all(r["run_id"] == "run-a" and r["decision_id"] == origin and r["ticker"] == "ZZTEST"
               for r in got)
    price = got[1]
    assert (price["direction"], price["price_currency"], price["reference_price"]) == (
        "at_or_above", "EUR", 123.45)
    assert got[2]["reminder_date"] == "2027-01-15" and got[2]["status"] == "active"
    assert all(UTC_Z.match(r["created_at"]) for r in got)


def test_ingest_is_idempotent(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    again = s.ingest_pending(today=TODAY)
    assert again == {"runs": [], "inserted": 0, "ignored": [], "errors": []}
    assert len(rows(db)) == 3


def test_ingest_unique_survives_a_race(db, monkeypatch):
    """Due worker leggono la stessa run candidata: il secondo non duplica e non va in errore."""
    add_run(db, "run-a")
    s = store(db)
    with conn_of(db) as conn:
        stale_candidates = TradeIdeaWatchStore._candidates(conn)
    s.ingest_pending(today=TODAY)
    monkeypatch.setattr(TradeIdeaWatchStore, "_candidates", staticmethod(lambda conn: stale_candidates))
    again = s.ingest_pending(today=TODAY)
    assert again["inserted"] == 0 and again["errors"] == []
    assert len(rows(db)) == 3


def test_ingest_declares_ignored_and_errors(db):
    add_run(db, "run-v3", policy=EXECUTION_POLICY_V3, created="2026-09-01T10:00:00.000000Z")
    add_run(db, "run-fav", ticker="QQSYN.MI", judgment="rejected", created="2026-09-02T10:00:00.000000Z")
    bad = [dict(TRIGGERS[0], date="2027-02-30")]
    add_run(db, "run-bad", ticker="ZZBAD", triggers=bad, created="2026-09-03T10:00:00.000000Z")
    add_run(db, "run-none", ticker="ZZNONE", triggers=None, created="2026-09-04T10:00:00.000000Z")
    report = store(db).ingest_pending(today=TODAY)
    assert {i["run_id"] for i in report["ignored"]} == {"run-v3", "run-fav"}
    assert {e["run_id"] for e in report["errors"]} == {"run-bad", "run-none"}
    assert report["inserted"] == 0 and rows(db) == []


def test_ingest_skips_runs_not_routed_to_research(db):
    add_run(db, "run-dcn", routed="dcn")
    add_run(db, "run-open", routed="none", status="completed", ticker="ZZOPEN",
            created="2026-09-05T10:00:00.000000Z")
    assert store(db).ingest_pending(today=TODAY) == {"runs": [], "inserted": 0, "ignored": [], "errors": []}


def test_price_trigger_blocked_without_reference(db):
    add_run(db, "run-a", reference=None)
    store(db).ingest_pending(today=TODAY)
    price = rows(db)[1]
    assert price["status"] == "blocked" and price["direction"] is None and price["status_reason"]


def test_ingest_rejects_datetime_today(db):
    with pytest.raises(ValueError):
        store(db).ingest_pending(today=datetime(2026, 10, 4))


def test_checked_row_refuses_unknown_keys():
    with pytest.raises(ValueError, match="chiavi inattese"):
        TradeIdeaWatchStore._checked_row({"trigger_index": 0})
    row = {k: None for k in ws.ROW_KEYS}
    row.update(status="blocked", status_reason=None)
    with pytest.raises(ValueError, match="senza motivo"):
        TradeIdeaWatchStore._checked_row(row)


# ── Letture / controlli ──────────────────────────────────────────────────

def test_active_and_active_run_tickers(db):
    add_run(db, "run-a")
    add_run(db, "run-live", ticker="ZZLIVE", status="running", routed="none",
            created="2026-10-03T10:00:00.000000Z")
    s = store(db)
    s.ingest_pending(today=TODAY)
    active = s.active()
    assert len(active) == 3 and all(isinstance(r, WatchRow) for r in active)
    assert s.active_run_tickers() == frozenset({"ZZLIVE"})


def test_record_checks_touch_only_active(db):
    add_run(db, "run-a", reference=None)  # riga di prezzo 'blocked'
    s = store(db)
    s.ingest_pending(today=TODAY)
    s.record_checks([Outcome(row_id=1, verdict="wait", reason="non ancora"),
                     Outcome(row_id=2, verdict="stale", reason="vecchio")])
    got = rows(db)
    assert (got[0]["last_check_status"], got[0]["status"]) == ("wait", "active")
    assert got[1]["last_check_status"] is None and got[1]["status"] == "blocked"


# ── fire ─────────────────────────────────────────────────────────────────

def test_fire_writes_one_pending_research(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    before = count_decisions(db)
    decision_id = s.fire("run-a", [1, 2], FIELDS, {2: {"price": "151.00", "currency": "EUR"}})
    assert count_decisions(db) == before + 1
    with conn_of(db) as conn:
        d = dict(conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone())
        memo = conn.execute("SELECT memo_id FROM trade_idea_runs WHERE id='run-a'").fetchone()[0]
    assert (d["action"], d["status"], d["ticker"], d["eur_amount"], d["memo_id"]) == (
        "RESEARCH", "PENDING", "ZZTEST", None, memo)
    assert UTC_Z.match(d["timestamp"]) and d["rationale"].startswith("[TRIGGER]")
    got = rows(db)
    assert [r["status"] for r in got] == ["fired", "fired", "active"]
    assert got[0]["fired_decision_id"] == decision_id and got[0]["fired_observation_json"] is None
    assert json.loads(got[1]["fired_observation_json"]) == {"price": "151.00", "currency": "EUR"}


def test_fire_is_atomic_on_failure_midway(db):
    """L'UPDATE delle righe fallisce DOPO l'INSERT della voce: nessuna decisione orfana."""
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    with conn_of(db) as conn:
        conn.execute("CREATE TRIGGER test_boom BEFORE UPDATE OF fired_at ON trade_idea_watch_triggers "
                     "WHEN NEW.id=2 BEGIN SELECT RAISE(ABORT,'guasto simulato'); END")
    before = count_decisions(db)
    with pytest.raises(sqlite3.IntegrityError, match="guasto simulato"):
        s.fire("run-a", [1, 2], FIELDS, {})
    assert count_decisions(db) == before
    assert [r["status"] for r in rows(db)] == ["active", "active", "active"]


def test_fire_rechecks_status(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    s.fire("run-a", [1], FIELDS, {})
    before = count_decisions(db)
    with pytest.raises(WatchConflict, match="non piu' attive"):
        s.fire("run-a", [1, 2], FIELDS, {})
    assert count_decisions(db) == before and rows(db)[1]["status"] == "active"


def test_fire_refuses_rows_of_another_run(db):
    add_run(db, "run-a")
    add_run(db, "run-b", ticker="ZZOTHER", created="2026-10-02T10:00:00.000000Z")
    s = store(db)
    s.ingest_pending(today=TODAY)
    before = count_decisions(db)
    with pytest.raises(WatchConflict):
        s.fire("run-a", [1, 4], FIELDS, {})
    assert count_decisions(db) == before


def test_fire_deferred_while_run_active_on_ticker(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    add_run(db, "run-live", status="accepted", routed="none", created="2026-10-03T10:00:00.000000Z")
    before = count_decisions(db)
    with pytest.raises(WatchDeferred, match="run-live"):
        s.fire("run-a", [1], FIELDS, {})
    assert count_decisions(db) == before and rows(db)[0]["status"] == "active"


def test_fire_deferred_while_finished_run_not_yet_routed(db):
    """Finestra finish_run -> route_result: una voce nuova degraderebbe la DCN (rilievo RV-P-A 1)."""
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    add_run(db, "run-done", status="completed", routed="none", judgment="favorable",
            created="2026-10-03T10:00:00.000000Z")
    assert s.active_run_tickers() == frozenset({"ZZTEST"})
    before = count_decisions(db)
    with pytest.raises(WatchDeferred, match="run-done"):
        s.fire("run-a", [1], FIELDS, {})
    assert count_decisions(db) == before and rows(db)[0]["status"] == "active"


def test_finished_run_without_result_does_not_block(db):
    """Una run finita senza risultato non viene mai instradata: non occupa il ticker."""
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    add_run(db, "run-failed", status="failed", routed="none", created="2026-10-03T10:00:00.000000Z",
            with_result=False)
    assert s.active_run_tickers() == frozenset()
    assert s.fire("run-a", [1], FIELDS, {}) > 0


def test_fire_not_deferred_by_run_on_other_ticker(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    add_run(db, "run-live", ticker="ZZELSE", status="accepted", routed="none",
            created="2026-10-03T10:00:00.000000Z")
    assert s.fire("run-a", [1], FIELDS, {}) > 0


@pytest.mark.parametrize("bad", [dict(FIELDS, action="BUY"), dict(FIELDS, eur_amount=100.0),
                                 dict(FIELDS, rationale=" ")])
def test_fire_validates_fields(db, bad):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    with pytest.raises(ValueError):
        s.fire("run-a", [1], bad, {})


# ── email / veto / supersede / provenienza ───────────────────────────────

def test_mark_email(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    with pytest.raises(WatchConflict):
        s.mark_email([1], "sent", None)          # non ancora scattata
    s.fire("run-a", [1], FIELDS, {})
    s.mark_email([1], "not_configured", None)
    assert (rows(db)[0]["email_status"], rows(db)[0]["email_attempts"]) == ("not_configured", 0)
    with pytest.raises(ValueError):
        s.mark_email([1], "failed", None)
    s.mark_email([1], "failed", "SMTPException")
    assert s.pending_email() == [{"id": 1, "run_id": "run-a", "ticker": "ZZTEST", "kind": "date",
                                  "fired_decision_id": rows(db)[0]["fired_decision_id"],
                                  "email_status": "failed", "email_attempts": 1,
                                  "email_claimed_at": None}]
    s.mark_email([1], "sent", None)
    assert (rows(db)[0]["email_status"], rows(db)[0]["email_attempts"]) == ("sent", 2)
    assert s.pending_email() == []
    with pytest.raises(WatchConflict, match="gia' inviata"):
        s.mark_email([1], "failed", "SMTPException")   # mai declassare una consegna
    with pytest.raises(WatchConflict, match="gia' inviata"):
        s.mark_email([1], "sent", None)
    assert (rows(db)[0]["email_status"], rows(db)[0]["email_attempts"]) == ("sent", 2)


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def fired_store(db):
    add_run(db, "run-a")
    clock = Clock(datetime(2026, 10, 4, 8, 30, tzinfo=timezone.utc))
    s = TradeIdeaWatchStore(db, clock=clock)
    s.ingest_pending(today=TODAY)
    s.fire("run-a", [1, 3], FIELDS, {1: {"date": "2027-01-15"}})
    return s, clock


def test_claim_email_only_one_of_two_overlapping_rounds(db):
    """Rilievo RV-P-A 2: due giri sovrapposti -> una sola presa, una sola email."""
    s, _ = fired_store(db)
    first = s.claim_email([1, 3])
    assert first == {"previous": {1: "not_attempted", 3: "not_attempted"}, "stale_sending": False}
    assert s.claim_email([1, 3]) is None and s.claim_email([1]) is None
    got = rows(db)
    assert got[0]["email_status"] == "sending" and got[0]["email_claimed_at"] and got[0]["email_attempts"] == 0
    s.mark_email([1, 3], "sent", None)
    got = rows(db)
    assert (got[0]["email_status"], got[0]["email_claimed_at"], got[0]["email_attempts"]) == ("sent", None, 1)
    assert s.claim_email([1]) is None


def test_claim_email_partial_set_writes_nothing(db):
    s, _ = fired_store(db)
    s.claim_email([1])
    assert s.claim_email([1, 3]) is None
    assert rows(db)[2]["email_status"] == "not_attempted"
    with pytest.raises(ValueError):
        s.claim_email([1, 1])


def test_stale_sending_is_reclaimable_and_declared(db):
    s, clock = fired_store(db)
    s.claim_email([1])
    assert [r["id"] for r in s.pending_email()] == [3]             # la 1 e' 'sending' fresca
    clock.value = datetime(2026, 10, 4, 8, 44, tzinfo=timezone.utc)
    assert s.claim_email([1]) is None                              # 14 min: presa ancora viva
    clock.value = datetime(2026, 10, 4, 8, 45, tzinfo=timezone.utc)
    assert [r["id"] for r in s.pending_email()] == [1, 3]
    assert s.claim_email([1]) == {"previous": {1: "sending"}, "stale_sending": True}


def test_claim_respects_failed_attempt_cap(db):
    s, _ = fired_store(db)
    for _ in range(3):
        assert s.claim_email([1]) is not None
        s.mark_email([1], "failed", "SMTPException")
    assert s.claim_email([1]) is None and [r["id"] for r in s.pending_email()] == [3]
    assert s.email_exhausted() == [{"id": 1, "run_id": "run-a", "ticker": "ZZTEST", "kind": "date",
                                    "fired_decision_id": rows(db)[0]["fired_decision_id"],
                                    "email_attempts": 3, "email_error": "SMTPException"}]
    assert s.email_exhausted(max_attempts=4) == [] and s.summary()["email_exhausted"] == 1


def test_fired_rows_returns_row_and_observation(db):
    s, _ = fired_store(db)
    got = s.fired_rows([1, 3])
    assert [(r.id, r.kind, r.status) for r, _ in got] == [(1, "date", "fired"), (3, "condition", "fired")]
    assert [obs for _, obs in got] == [{"date": "2027-01-15"}, None]
    with pytest.raises(WatchConflict):
        s.fired_rows([1, 2])


def test_active_runs_reports_oldest_busy_run(db):
    add_run(db, "run-a")
    add_run(db, "run-live", status="running", routed="none", created="2026-10-03T10:00:00.000000Z")
    add_run(db, "run-done", ticker="ZZDONE", status="completed", routed="none",
            created="2026-10-02T10:00:00.000000Z")
    add_run(db, "run-dead", ticker="ZZDEAD", status="failed", routed="none", with_result=False,
            created="2026-10-02T11:00:00.000000Z")
    assert store(db).active_runs() == {
        "ZZTEST": {"run_id": "run-live", "technical_status": "running",
                   "since": "2026-10-03T10:00:00.000000Z", "state": "in_corso"},
        "ZZDONE": {"run_id": "run-done", "technical_status": "completed",
                   "since": "2026-10-02T10:00:00.000000Z", "state": "non_instradata"}}


def test_fire_rechecks_veto_inside_transaction(db):
    """Veto arrivato fra cancel_vetoed e fire: nessuna voce, righe annullate con motivo."""
    origin = add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    with conn_of(db) as conn:
        conn.execute("UPDATE decisions SET veto=1 WHERE id=?", (origin,))
    before = count_decisions(db)
    with pytest.raises(WatchConflict, match="veto"):
        s.fire("run-a", [1, 2], FIELDS, {})
    assert count_decisions(db) == before
    got = rows(db)
    assert [r["status"] for r in got] == ["cancelled", "cancelled", "active"]
    assert all("veto" in r["status_reason"] for r in got[:2])


def test_attention_rows_declares_blocked_undated(db):
    """Rilievo RV-P-B 1: blocked/undated all'import non spariscono zitti."""
    add_run(db, "run-a", reference=None, triggers=[TRIGGERS[1], TRIGGERS[2]])
    s = store(db)
    report = s.ingest_pending(today=TODAY)
    assert [(n["kind"], n["status"]) for n in report["runs"][0]["not_active"]] == [
        ("price", "blocked"), ("condition", "undated")]
    got = s.attention_rows()
    assert [(r["id"], r["kind"], r["status"]) for r in got] == [
        (1, "price", "blocked"), (2, "condition", "undated")]
    assert all(r["reason"] and r["run_id"] == "run-a" and r["ticker"] == "ZZTEST" for r in got)
    assert s.summary()["attention"] == 2


def test_revoked_veto_is_declared_not_reactivated(db):
    """Rilievo RV-P-B 2 / decisione B: il veto resta terminale, la revoca si DICHIARA."""
    origin = add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    with conn_of(db) as conn:
        conn.execute("UPDATE decisions SET veto=1, veto_at='2026-10-04T09:00:00' WHERE id=?", (origin,))
    assert s.cancel_vetoed() == 3
    assert s.attention_rows() == []                       # veto in vigore: nulla da dichiarare
    with conn_of(db) as conn:
        conn.execute("UPDATE decisions SET veto=0, veto_revoked_at='2026-10-04T10:00:00' WHERE id=?",
                     (origin,))
    assert s.cancel_vetoed() == 0
    got = s.attention_rows()
    assert [(r["id"], r["status"]) for r in got] == [
        (1, "veto_revoked"), (2, "veto_revoked"), (3, "veto_revoked")]
    assert "rilancia la Trade Idea" in got[0]["reason"] and f"#{origin}" in got[0]["reason"]
    assert {r["status"] for r in rows(db)} == {"cancelled"}


def test_only_veto_cancels_expired_does_not(db):
    add_run(db, "run-veto", ticker="ZZVETO", veto=1, created="2026-09-01T10:00:00.000000Z")
    add_run(db, "run-exp", ticker="ZZEXP", decision_status="EXPIRED", created="2026-09-02T10:00:00.000000Z")
    s = store(db)
    s.ingest_pending(today=TODAY)
    assert s.cancel_vetoed() == 3
    by_ticker = {}
    for r in rows(db):
        by_ticker.setdefault(r["ticker"], set()).add(r["status"])
    assert by_ticker == {"ZZVETO": {"cancelled"}, "ZZEXP": {"active"}}
    assert all("veto" in r["status_reason"] for r in rows(db) if r["ticker"] == "ZZVETO")
    assert s.cancel_vetoed() == 0


def test_veto_does_not_touch_fired_rows(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    s.fire("run-a", [1], FIELDS, {})
    with conn_of(db) as conn:
        conn.execute("UPDATE decisions SET veto=1 WHERE id=(SELECT decision_id FROM "
                     "trade_idea_watch_triggers WHERE id=1)")
    assert s.cancel_vetoed() == 2
    assert [r["status"] for r in rows(db)] == ["fired", "cancelled", "cancelled"]


def test_supersede_older_runs_same_ticker(db):
    add_run(db, "run-old", created="2026-09-01T10:00:00.000000Z")
    add_run(db, "run-other", ticker="ZZOTHER", created="2026-09-02T10:00:00.000000Z")
    s = store(db)
    s.ingest_pending(today=TODAY)
    add_run(db, "run-new", created="2026-10-02T10:00:00.000000Z")
    s.ingest_pending(today=TODAY)
    assert s.supersede_older() == 3
    status = {}
    for r in rows(db):
        status.setdefault(r["run_id"], set()).add(r["status"])
    assert status == {"run-old": {"superseded"}, "run-other": {"active"}, "run-new": {"active"}}
    assert all(r["status_reason"] == "superata dalla run run-new" for r in rows(db) if r["run_id"] == "run-old")


def test_supersede_ignores_newer_unrouted_run(db):
    add_run(db, "run-old", created="2026-09-01T10:00:00.000000Z")
    s = store(db)
    s.ingest_pending(today=TODAY)
    add_run(db, "run-live", status="running", routed="none", created="2026-10-03T10:00:00.000000Z")
    assert s.supersede_older() == 0


def test_lookup_decisions_provenance(db):
    origin = add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    decision_id = s.fire("run-a", [1, 3], FIELDS, {})
    out = s.lookup_decisions([decision_id, origin])
    assert set(out) == {decision_id}
    item = out[decision_id]
    assert item["origin"] == "trade_idea_trigger" and item["run_id"] == "run-a"
    assert item["source_decision_id"] == origin and item["trigger_ids"] == [1, 3]
    assert item["kinds"] == ["date", "condition"]
    assert s.lookup_decisions([]) == {}
    with pytest.raises(ValueError):
        s.lookup_decisions([0])


def test_summary(db):
    add_run(db, "run-a")
    s = store(db)
    s.ingest_pending(today=TODAY)
    s.fire("run-a", [1], FIELDS, {})
    assert s.summary()["by_status"] == {"active": 2, "fired": 1}
    assert s.summary()["email_pending"] == 1
    assert s.summary()["attention"] == 0 and s.summary()["email_exhausted"] == 0


def test_conftest_tripwire_covers_watch_store():
    """Rilievo RV-P-A 5: lo store usa sqlite3.connect raw; il presidio del conftest lo copre.

    ZR 05/10: in un clone pulito il DB di produzione NON esiste e il costruttore si ferma prima
    (FileNotFoundError, nessuna apertura): la guardia si prova quindi su `_connect`, l'unico
    punto da cui lo store apre il DB, indipendente dall'esistenza del file. Se il DB esiste
    (macchina del PM) si prova anche il costruttore, come prima."""
    import os
    from bellomberg.storage import memory_db
    store = object.__new__(TradeIdeaWatchStore)
    store.db_path = os.fspath(memory_db.SQLITE_PATH)
    with pytest.raises(BaseException) as info:
        with store._connect(read_only=True):
            pass
    assert type(info.value).__name__ == "ProduzioneToccata"
    if os.path.isfile(memory_db.SQLITE_PATH):
        with pytest.raises(BaseException) as info:
            TradeIdeaWatchStore(memory_db.SQLITE_PATH)
        assert type(info.value).__name__ == "ProduzioneToccata"
    else:
        with pytest.raises(FileNotFoundError):
            TradeIdeaWatchStore(memory_db.SQLITE_PATH)
