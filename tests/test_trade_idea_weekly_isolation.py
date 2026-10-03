"""Real SQLite memo selection never treats a Trade Idea as a weekly recovery."""
from test_persistence import db
import pytest


def test_shared_paid_lock_reports_real_exclusion_without_creating_an_admission_file(tmp_path, monkeypatch):
    from bellomberg.agents.trade_idea import exclusive_paid_run, paid_run_is_active, PaidRunBusy
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    lock = tmp_path / "committee_paid_run.lock"
    assert paid_run_is_active() is False
    assert not lock.exists()
    with exclusive_paid_run(lock):
        assert paid_run_is_active() is True
        with pytest.raises(PaidRunBusy):
            with exclusive_paid_run(lock):
                pytest.fail("Second paid committee acquired the same live lock")
    assert paid_run_is_active() is False


def test_recent_weekly_memos_keep_original_order_with_newer_trade_idea(db):
    first = db.save_memo("Weekly first", title="Weekly first")
    second = db.save_memo("Weekly second", title="Weekly second")
    idea = db.save_memo("Candidate research", title="Trade Idea")
    with db._conn() as conn:
        conn.execute("UPDATE memos SET notes=? WHERE id=?", ("tradeXidea:ordinary-note", second))
        conn.execute("UPDATE memos SET notes=? WHERE id=?", ("trade_idea:synthetic-run", idea))
    assert [row["id"] for row in db.get_recent_memos(3)] == [second, first]
    with db._conn() as conn:
        assert conn.execute("SELECT title FROM memos WHERE id=?", (idea,)).fetchone()[0] == "Trade Idea"


def test_weekly_recovery_does_not_select_trade_idea(db, monkeypatch, capsys):
    from bellomberg.cli import regenerate_memo
    weekly = db.save_memo("Weekly without specialist reports", title="Weekly")
    idea = db.save_memo("Candidate research", title="Trade Idea")
    with db._conn() as conn:
        conn.execute("UPDATE memos SET notes=? WHERE id=?", ("trade_idea:synthetic-run", idea))
    monkeypatch.setattr(regenerate_memo, "MemoryDB", lambda: db)
    monkeypatch.setattr(regenerate_memo, "run_capo", lambda *a, **k: (_ for _ in ()).throw(AssertionError("No LLM")))
    regenerate_memo.main()
    output = capsys.readouterr().out
    assert f"memo #{weekly}:" in output
    assert f"memo #{idea}:" not in output
