"""Cronologia fonti: SQLite TEMP, scritture e snapshot misurati."""
from contextlib import contextmanager
from datetime import date
import pytest
from bellomberg.storage.memory_db import MemoryDB
from test_trade_idea_live_consultation import board

TODAY = date(2026, 10, 7)

@pytest.fixture
def db(tmp_path, monkeypatch):
    instance = MemoryDB(db_path=str(tmp_path / "guidance.db"),
                        chroma_path=str(tmp_path / "chroma"))
    original = instance._conn
    instance.measured_changes = []
    @contextmanager
    def measured():
        with original() as conn:
            before = conn.total_changes
            try:
                yield conn
            finally:
                instance.measured_changes.append(conn.total_changes - before)
    monkeypatch.setattr(instance, "_conn", measured)
    return instance

def add(db, **kwargs):
    payload = dict(ticker="ZZSYN", metric="other", period="FY2027", value_mid=17,
                   value_low=15, value_high=19, unit="meur (EBITA FY)",
                   source_doc="synthetic release", source_date="2026-07-01", today=TODAY)
    payload.update(kwargs)
    return db.add_guidance(**payload)

def snapshot(db):
    with db._conn() as conn:
        return [tuple(row) for row in conn.execute("SELECT * FROM company_guidance ORDER BY id")]

def unchanged(db, before, result, reason):
    changes = list(db.measured_changes)
    assert snapshot(db) == before
    assert sum(changes) == 0
    assert "error" in result and result["reason_code"] == reason

@pytest.mark.parametrize("delta,reason", [
    ({"source_date": "2026-04-01"}, "source_older_than_active"),
    ({"source_date": "2026-10-08"}, "source_date_in_future"),
    ({"value_mid": 18}, "same_date_conflict"),
    ({"value_low": 14}, "same_date_conflict"),
    ({"value_high": 20}, "same_date_conflict"),
    ({"source_doc": "another release"}, "same_date_conflict"),
    ({"source_date": "2026-04-01", "unit": "MEUR\u00a0(EBITA\u200b FY)"}, "source_older_than_active"),
])
def test_rejections_no_writes(db, delta, reason):
    assert add(db)["ok"]
    before = snapshot(db)
    db.measured_changes.clear()
    unchanged(db, before, add(db, **delta), reason)

def test_future_without_active(db):
    before = snapshot(db)
    db.measured_changes.clear()
    unchanged(db, before, add(db, source_date="2026-10-08"), "source_date_in_future")

def test_duplicate_no_renewal(db):
    old = add(db, today=date(2026, 7, 2), note="original")
    before = snapshot(db)
    db.measured_changes.clear()
    result = add(db, valid_until="2027-04-01", note="ignored")
    assert result["id"] == old["id"]
    assert result["status"] == "already_registered" and result["superseded"] == 0
    assert result["valid_until"] == old["valid_until"]
    assert result["valid_until_source"] == old["valid_until_source"]
    assert result["requested_valid_until"] == "2027-04-01"
    assert snapshot(db) == before and sum(db.measured_changes) == 0

def test_advancement_and_history_frozen(db):
    first = add(db, source_date="2026-04-01", today=date(2026, 4, 2))
    second = add(db)
    assert second["superseded"] == 1
    rows = db.get_guidance("ZZSYN", include_history=True, today=TODAY)
    assert rows["history"][0]["superseded_by"] == second["id"]
    assert rows["history"][0]["id"] == first["id"]
    before = snapshot(db)
    db.measured_changes.clear()
    unchanged(db, before, add(db, source_date="2026-03-01"), "source_older_than_active")

@pytest.mark.parametrize("legacy_date", ["not-a-date", "2026-07-01"])
def test_legacy_ambiguous_rejected(db, legacy_date):
    first = add(db)
    with db._conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(company_guidance)") if r[1] != "id"]
        names = ",".join(cols)
        conn.execute(f"INSERT INTO company_guidance ({names}) SELECT {names} FROM company_guidance WHERE id=?", (first["id"],))
        conn.execute("UPDATE company_guidance SET source_date=? WHERE id != ?", (legacy_date, first["id"]))
    before = snapshot(db)
    db.measured_changes.clear()
    unchanged(db, before, add(db), "active_chronology_ambiguous")

@pytest.mark.parametrize("delta", [{"unit": "meur (FOCF FY)"}, {"period": "Q3-2027"}, {"metric": "eps", "unit": None}])
def test_distinct_identity(db, delta):
    add(db)
    result = add(db, source_date="2026-04-01", **delta)
    assert result["ok"] and result["superseded"] == 0
    assert len(db.get_guidance("ZZSYN", today=TODAY)["active"]) == 2

def test_today_datetime_and_canonical_date(db):
    result = add(db, source_date="2026-10-07T12:34:00", valid_until="2026-10-07")
    assert result["ok"]
    row = db.get_guidance("ZZSYN", today=TODAY)["active"][0]
    assert row["source_date"] == "2026-10-07" and not row["stale"]
    assert db.get_guidance("ZZSYN", today=date(2026, 10, 8))["active"][0]["stale"]

def test_compact_iso_canonicalized_and_duplicate(db):
    first = add(db, source_date="20260701")
    assert first["ok"]
    assert db.get_guidance("ZZSYN", today=TODAY)["active"][0]["source_date"] == "2026-07-01"
    assert add(db)["id"] == first["id"]

def test_dispatch_keeps_rejection_and_noop(db, monkeypatch):
    from bellomberg.storage import memory_db
    from bellomberg.agents import chat_tools
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: db)
    monkeypatch.setattr(chat_tools, "_prossima_trimestrale", lambda ticker: (None, None, "synthetic calendar n.d."))
    payload = dict(ticker="ZZSYN", metric="other", period="FY2027", value_mid=17,
                   value_low=15, value_high=19, unit="meur (EBITA FY)",
                   source_doc="synthetic release", source_date="2026-07-01")
    first = chat_tools.dispatch("add_guidance", payload, caller="specialista-run:fundamentals")["data"]
    assert first["ok"]
    before = snapshot(db)
    db.measured_changes.clear()
    same = chat_tools.dispatch("add_guidance", payload, caller="chat:fundamentals")["data"]
    assert same["id"] == first["id"] and same["status"] == "already_registered"
    rejected = chat_tools.dispatch("add_guidance", dict(payload, value_mid=18), caller="chat:fundamentals")["data"]
    unchanged(db, before, rejected, "same_date_conflict")

def test_ti_writer_forbidden_schema_and_dispatch_weekly_allowed(board):
    from types import SimpleNamespace
    from bellomberg.agents.specialists.base import Specialist
    desk_type = type("GuidanceDesk", (Specialist,), {"name": "fundamentals"})
    desk = desk_type(board, client=SimpleNamespace())
    assert "add_guidance" not in {t["name"] for t in desk._build_tools_schema()}
    assert "error" in desk._execute_meta_tool("add_guidance", {})
    board.run_scope = "weekly"
    assert "add_guidance" in {t["name"] for t in desk._build_tools_schema()}
    desk._task_context = {"consultation": True}
    assert desk._execute_meta_tool("add_guidance", {})["status"] == "blocked_consultation"

def test_rollback_update_failure(db):
    add(db)
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER fail_supersede BEFORE UPDATE ON company_guidance BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    before = snapshot(db)
    result = add(db, source_date="2026-08-01")
    assert "error" in result and "synthetic failure" in result["error"]
    assert snapshot(db) == before

def test_all_twins_checked_before_write_and_newer_can_replace(db):
    first = add(db)
    with db._conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(company_guidance)") if r[1] != "id"]
        names = ",".join(cols)
        conn.execute(f"INSERT INTO company_guidance ({names}) SELECT {names} FROM company_guidance WHERE id=?", (first["id"],))
        conn.execute("UPDATE company_guidance SET source_date='2026-08-01' WHERE id != ?", (first["id"],))
    before = snapshot(db)
    db.measured_changes.clear()
    unchanged(db, before, add(db, source_date="2026-07-15"), "source_older_than_active")
    result = add(db, source_date="2026-09-01")
    assert result["ok"] and result["superseded"] == 2
    assert len(db.get_guidance("ZZSYN", today=TODAY)["active"]) == 1

def test_two_writers_serialized_before_read(db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    original = db._conn
    first_read, release, second_started, second_read = (Event() for _ in range(4))
    class Proxy:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, params=()):
            if "FROM company_guidance WHERE ticker=" in sql:
                if current_thread().name.endswith("_0"):
                    result = self.conn.execute(sql, params)
                    first_read.set()
                    assert release.wait(5), "controller did not release writer"
                    return result
                second_read.set()
            return self.conn.execute(sql, params)
    @contextmanager
    def controlled():
        with original() as conn:
            yield Proxy(conn)
    monkeypatch.setattr(db, "_conn", controlled)
    def older():
        second_started.set()
        return add(db, source_date="2026-04-01")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="guidance") as pool:
        new = pool.submit(add, db)
        try:
            assert first_read.wait(5)
            old = pool.submit(older)
            assert second_started.wait(5)
            assert not second_read.wait(0.3), "second writer read before first committed"
        finally:
            release.set()
        assert new.result(timeout=5)["ok"]
        assert old.result(timeout=5)["reason_code"] == "source_older_than_active"
    monkeypatch.setattr(db, "_conn", original)
    rows = db.get_guidance("ZZSYN", today=TODAY)["active"]
    assert len(rows) == 1 and rows[0]["source_date"] == "2026-07-01"
