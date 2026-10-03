"""Real temporary SQLite: recovery cannot erase proposals or PM feedback."""
import pytest

from bellomberg.storage.memory_db import MemoryDB


@pytest.fixture
def decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self: self.__dict__.update(
        chroma_client=None, col_memos=None, col_decisions=None, col_feedback=None))
    db = MemoryDB(str(tmp_path / "decisions.sqlite"), str(tmp_path / "chroma"))
    memo_id = db.save_memo("Original memo")
    with db._conn() as conn:
        conn.execute("INSERT INTO decisions(memo_id,timestamp,action,ticker,status) VALUES (?,?,'BUY','ALFA','PENDING')",
                     (memo_id, "2026-01-01"))
        conn.execute("INSERT INTO decisions(memo_id,timestamp,action,ticker,status,pm_feedback) "
                     "VALUES (?,?,'HOLD','BETA','PENDING','PM original decision')", (memo_id, "2026-01-01"))
    return db, memo_id


def snapshot(db):
    with db._conn() as conn:
        return ([tuple(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id")],
                tuple(conn.execute("SELECT * FROM memos").fetchone()))


def test_failed_new_extraction_preserves_exact_old_proposals_and_memo(decisions, monkeypatch):
    db, memo_id = decisions
    before = snapshot(db)
    def failed(*args, **kwargs):
        raise RuntimeError("synthetic extractor disconnect")
    monkeypatch.setattr(db, "extract_and_save_decisions", failed)
    with pytest.raises(RuntimeError, match="extractor disconnect"):
        db.replace_memo_decisions(memo_id, "Replacement memo")
    assert snapshot(db) == before


def test_crash_during_final_memo_update_rolls_back_entire_replacement(decisions):
    db, memo_id = decisions
    before = snapshot(db)
    def failed(conn):
        conn.execute("UPDATE memos SET full_markdown='half-written'")
        raise RuntimeError("synthetic crash after decision insert")
    with pytest.raises(RuntimeError, match="after decision insert"):
        db.replace_memo_decisions(memo_id, "Replacement memo", update_memo=failed,
                                 prepared_rows=[{"action": "BUY", "ticker": "GAMMA"}])
    assert snapshot(db) == before
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memo_decision_extractions").fetchone()[0] == 0


def test_success_archives_auto_proposal_keeps_pm_feedback_and_replay_is_idempotent(decisions):
    db, memo_id = decisions
    old, _ = snapshot(db)
    rows = [{"action": "HOLD", "ticker": "BETA"}, {"action": "BUY", "ticker": "GAMMA"}]
    ids = db.replace_memo_decisions(memo_id, "Replacement memo", prepared_rows=rows,
        update_memo=lambda conn: conn.execute("UPDATE memos SET full_markdown='Replacement memo' WHERE id=?", (memo_id,)))
    once = snapshot(db)
    assert db.replace_memo_decisions(memo_id, "Replacement memo", prepared_rows=rows) == ids
    assert snapshot(db) == once
    with db._conn() as conn:
        proposals = [dict(row) for row in conn.execute("SELECT * FROM decisions ORDER BY id")]
    assert len(proposals) == 3
    assert proposals[0]["status"] == "EXPIRED" and proposals[0]["ticker"] == "ALFA"
    assert tuple(proposals[1].values()) == old[1]
    assert proposals[2]["ticker"] == "GAMMA" and proposals[2]["status"] == "PENDING"


def test_later_replacement_does_not_reuse_superseded_expired_row(decisions):
    db, memo_id = decisions
    db.replace_memo_decisions(memo_id, "Version two", prepared_rows=[{"action": "BUY", "ticker": "GAMMA"}])
    ids = db.replace_memo_decisions(memo_id, "Version three", prepared_rows=[{"action": "BUY", "ticker": "ALFA"}])
    with db._conn() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM decisions WHERE ticker='ALFA' ORDER BY id")]
    assert len(rows) == 2 and rows[0]["status"] == "EXPIRED" and rows[1]["status"] == "PENDING"
    assert ids == [rows[1]["id"]]
