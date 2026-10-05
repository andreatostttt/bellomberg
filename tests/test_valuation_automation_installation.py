"""Offline lifecycle and committed-event checks for opt-in valuation automation."""
import asyncio
from datetime import date
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from test_valuation_automation import automation as _automation_fixture


@pytest.fixture
def automation(tmp_path, monkeypatch):
    return _automation_fixture.__wrapped__(tmp_path, monkeypatch)


def _tracking_rows(db):
    with db._conn() as conn:
        return (conn.execute("SELECT count(*) FROM positions").fetchone()[0],
                conn.execute("SELECT count(*) FROM favorite_companies").fetchone()[0])


ARCHIVED = {"status": "archived", "reason": "excel_generation_archived_by_research_contract"}


def test_archived_installation_and_notification_do_not_read_policy_open_db_or_start_thread(tmp_path, monkeypatch):
    # ZR 05/10 (Z4): contratto attuale (1326312, valuation_automation_installation.py:71-78, 221-223).
    # Garanzia conservata: avvio e notifica non aprono DB, manager o thread. Garanzia NON piu' provabile
    # qui: lo stato 'disabled/configuration_absent' della policy assente vive solo in
    # _start_legacy_installation/_notify_legacy_tracking, senza chiamanti (codice morto, censimento Z4).
    from bellomberg.valuation import valuation_automation_installation as install
    from bellomberg.api import bellomberg_api as api
    monkeypatch.setattr(install, "installation_runtime", lambda: pytest.fail("archived installation read the policy"))
    monkeypatch.setattr(install, "build_manager", lambda *_: pytest.fail("archived installation opened manager"))
    monkeypatch.setattr(install, "Thread", lambda *_a, **_k: pytest.fail("archived installation started thread"))
    db_path = tmp_path / "absent.db"
    monkeypatch.setattr(api, "SQLITE_PATH", str(db_path))
    assert install.start_installation(db_path) == {"runner": None, "state": ARCHIVED}
    app = SimpleNamespace(state=SimpleNamespace())
    async def start_and_stop():
        async with api.lifespan(app):
            assert app.state.valuation_automation["runner"] is None
            assert app.state.valuation_automation["state"] == ARCHIVED
    asyncio.run(start_and_stop())
    assert install.notify_tracking(db_path, "SYNTH-A", "portfolio") == ARCHIVED
    assert not db_path.exists() and not (tmp_path / "data").exists()


def test_archived_notification_never_enqueues_or_acquires_for_tracked_tickers(automation, monkeypatch):
    # Il corpo storico (accodamento e deduplica) e' in
    # archive/private/attic/tests_excel_archiviato_20261005/test_valuation_automation_installation_legacy.py.
    from bellomberg.valuation import valuation_automation_installation as install
    manager, observed, _, _ = automation
    db = manager.versions.db
    with db._conn() as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES('SYNTH-A',10,1)")
        conn.execute("INSERT INTO favorite_companies(ticker,name) VALUES('SYNTH-A','Synthetic issuer')")
    monkeypatch.setattr(install, "installation_runtime", lambda: manager.runtime)
    monkeypatch.setattr(install, "build_manager", lambda *_: manager)
    before = _tracking_rows(db)
    for ticker, trigger in (("synth-a", "portfolio"), ("SYNTH-A", "watchlist"), ("SYNTH-N", "portfolio")):
        assert install.notify_tracking(db.db_path, ticker, trigger) == ARCHIVED
    assert _tracking_rows(db) == before
    with sqlite3.connect(manager.jobs.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM valuation_jobs").fetchone()[0] == 0
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]


def test_build_manager_reads_existing_schema_without_memorydb_init_or_migration(automation, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.valuation import valuation_automation_installation as install
    manager, _, _, _ = automation
    path = manager.jobs.db_path
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    monkeypatch.setattr(MemoryDB, "__init__", lambda self, *a, **k: pytest.fail("MemoryDB created/migrated"))
    monkeypatch.setattr(MemoryDB, "_init_sqlite", lambda self: pytest.fail("migration attempted"))
    built = install.build_manager(manager.runtime, path)
    assert built.jobs.db_path == path
    assert built.versions.current("SYNTH-A") is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == before


def test_startup_reconciles_union_of_active_portfolio_and_watchlist(automation):
    from bellomberg.valuation.valuation_automation_installation import reconcile_tracking
    manager, observed, _, _ = automation
    with manager.versions.db._conn() as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES('SYNTH-A',3,1)")
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES('SYNTH-X',4,0)")
        conn.execute("INSERT INTO favorite_companies(ticker,name) VALUES('SYNTH-A','Synthetic A')")
        conn.execute("INSERT INTO favorite_companies(ticker,name) VALUES('SYNTH-B','Synthetic B')")
    first = reconcile_tracking(manager)
    assert first["status"] == "ok" and not first["sources"]
    assert [row["ticker"] for row in first["outcomes"]] == ["SYNTH-A", "SYNTH-B"]
    second = reconcile_tracking(manager)
    assert [row["id"] for row in second["outcomes"]] == [row["id"] for row in first["outcomes"]]
    assert all(row["reused"] for row in second["outcomes"])
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]


def test_runner_stop_keeps_inflight_lease_until_worker_finishes(monkeypatch):
    from bellomberg.valuation import valuation_automation_installation as install
    entered, release = Event(), Event()
    class Manager:
        def __init__(self):
            self.calls = 0
            self.lease = "running"
        def recover(self):
            return []
        def run_one(self, *, owner):
            self.calls += 1
            assert owner.startswith("backend-")
            entered.set()
            assert release.wait(2), "test worker was not released"
            self.lease = "finished"
            return {"id": 1, "ticker": "SYNTH-A", "kind": "prepare",
                    "status": "succeeded", "reason": "fixture"}
    manager = Manager()
    monkeypatch.setattr(install, "reconcile_tracking", lambda _: {"status": "ok", "outcomes": []})
    monkeypatch.setattr(install, "reconcile_price_events", lambda _: {"status": "ok", "outcomes": []})
    monkeypatch.setattr(install, "reconcile_source_events", lambda _: {"status": "ok", "outcomes": []})
    runner = install.AutomationRunner(manager, poll_seconds=.01, reconcile_seconds=.02)
    runner.start()
    try:
        assert entered.wait(1), "worker never claimed synthetic job"
        with pytest.raises(RuntimeError, match="already started"):
            runner.start()
        stopping = runner.stop(timeout=.01)
        assert stopping["status"] == "stopping" and manager.lease == "running"
    finally:
        release.set()
        runner.stop(timeout=2)
    assert runner.status()["status"] == "stopped"
    assert runner.status()["last_job"]["status"] == "succeeded"
    assert manager.calls == 1


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.api import bellomberg_api as api
    from bellomberg.portfolio import portfolio_analytics as pa, twr_engine as twr
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self: None)
    db = MemoryDB(str(tmp_path / "api.db"), str(tmp_path / "chroma"))
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "SQLITE_PATH", db.db_path)
    monkeypatch.setattr(api, "_trade_fx", lambda day, currency: {
        "tasso": 1.0, "fonte": "synthetic identity", "data": day, "nota": None})
    monkeypatch.setattr(pa, "MemoryDB", lambda: db)
    monkeypatch.setattr(twr, "MemoryDB", lambda: db)
    monkeypatch.setattr(twr, "SQLITE_PATH", db.db_path)
    monkeypatch.setattr(pa, "_build_fx_history", lambda *_a, **_k: pytest.fail("FX network"))
    twr.invalidate_cache()
    db.apply_cash_movement("DEPOSIT", 100000, data="2026-01-02")
    return api, db


def _opening_request(api):
    return api.OpeningPositionIn(ticker="SYNTH-A", quantita=10, prezzo_medio=20,
        valuta="EUR", as_of="2024-01-31", provenienza="Synthetic broker balance")


def _trade_request(api):
    return api.TradeIn(ticker="SYNTH-B", action="BUY", quantita=2,
        prezzo=10, valuta="EUR", data=date.today().isoformat(), senza_decisione=True)


def test_api_notifies_only_after_opening_trade_and_favorite_commit(api_db, monkeypatch):
    api, db = api_db
    seen = []
    def notification(path, ticker, trigger):
        assert path == db.db_path
        with db._conn() as conn:
            if ticker == "SYNTH-A":
                assert conn.execute("SELECT count(*) FROM position_openings WHERE ticker=?", (ticker,)).fetchone()[0] == 1
            elif ticker == "SYNTH-B":
                assert conn.execute("SELECT count(*) FROM trade_history WHERE ticker=?", (ticker,)).fetchone()[0] == 1
            else:
                assert conn.execute("SELECT count(*) FROM favorite_companies WHERE ticker=?", (ticker,)).fetchone()[0] == 1
        seen.append((ticker, trigger))
        return {"status": "queued"}
    monkeypatch.setattr(api, "_notify_model_tracking", notification)
    opening = _opening_request(api)
    preview = api.preview_opening_position(opening)
    assert seen == []
    saved = api.create_opening_position(opening.model_copy(update={"preview_id": preview["preview_id"]}))
    assert saved["ok"] and saved["valuation_automation"]["status"] == "queued"
    trade = _trade_request(api)
    preview = api.preview_trade(trade)
    assert seen == [("SYNTH-A", "portfolio")]
    posted = api.log_trade(trade.model_copy(update={"preview_id": preview["preview_id"]}))
    assert posted["ok"] and posted["valuation_automation"]["status"] == "queued"
    favorite = api.favorites_add(ticker="SYNTH-C", name="Synthetic issuer")
    assert favorite["ok"] and favorite["valuation_automation"]["status"] == "queued"
    assert seen == [("SYNTH-A", "portfolio"), ("SYNTH-B", "portfolio"), ("SYNTH-C", "watchlist")]


def test_queue_error_after_commit_is_declared_without_http_retry(api_db, monkeypatch):
    from bellomberg.valuation import valuation_automation_installation as install
    api, db = api_db
    def fail(*_args):
        raise RuntimeError("synthetic queue unavailable")
    monkeypatch.setattr(install, "notify_tracking", fail)
    opening = _opening_request(api)
    preview = api.preview_opening_position(opening)
    saved = api.create_opening_position(opening.model_copy(update={"preview_id": preview["preview_id"]}))
    assert saved["ok"] and saved["valuation_automation"]["status"] == "error"
    trade = _trade_request(api)
    preview = api.preview_trade(trade)
    posted = api.log_trade(trade.model_copy(update={"preview_id": preview["preview_id"]}))
    assert posted["ok"] and posted["valuation_automation"]["status"] == "error"
    favorite = api.favorites_add(ticker="SYNTH-C", name="Synthetic issuer")
    assert favorite["ok"] and favorite["valuation_automation"]["status"] == "error"
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM position_openings").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM trade_history").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM favorite_companies WHERE ticker='SYNTH-C'").fetchone()[0] == 1
