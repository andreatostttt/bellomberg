"""Trade Idea decision links are checked inside the real cash/trade transaction."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest
from openpyxl import Workbook

from bellomberg.storage import memory_db
from bellomberg.storage.trade_idea_store import RunConflict, TradeIdeaStore, ensure_schema


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", _no_chroma)
    instance = memory_db.MemoryDB(str(tmp_path / "isolated.db"), str(tmp_path / "chroma"))
    instance.apply_cash_movement("DEPOSIT", 1000)
    return instance


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest(run_id, *, path, status="ready", with_workbook=True):
    path = str(path)
    Path(path).write_bytes(b"%PDF-1.7 Synthetic sealed artifact for transaction tests")
    digest = sha256(Path(path).read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "run_type": "trade_idea", "run_id": run_id,
            "ticker": "TEST", "attachments": [path], "expected_hashes": {path: digest},
            "artifacts": [{"id": "pdf", "kind": "pdf", "status": status,
                           "path": path, "sha256": digest}],
            "pdf_quality": {"status": status, "analytical_pages": 10,
                            "analytical_words": 4000}}
    # The transaction boundary consumes an already sealed delivery receipt.
    # These are declared synthetic artifacts; candidate-model provenance and
    # report substance are exercised by the pipeline/delivery tests.
    if with_workbook:
        workbook_path = Path(path).with_suffix(".xlsx")
        workbook = Workbook()
        workbook.active.title = "Synthetic transaction fixture"
        workbook.active["A1"] = "Synthetic sealed workbook, not a live economic valuation"
        workbook.save(workbook_path)
        workbook.close()
        workbook_hash = sha256(workbook_path.read_bytes()).hexdigest()
        manifest["attachments"].append(str(workbook_path))
        manifest["expected_hashes"][str(workbook_path)] = workbook_hash
        manifest["artifacts"].append({"id": "excel-1", "kind": "xlsx", "status": "ready",
                                      "path": str(workbook_path), "sha256": workbook_hash})
    manifest["model_status"] = "ready" if with_workbook else "incomplete"
    from bellomberg.reporting.trade_idea_delivery import assess_trade_idea_completion
    completion = assess_trade_idea_completion(manifest)
    manifest.update(completion_contract="pdf-and-economic-workbook/2",
                    complete_package_status=completion["status"],
                    complete_package_reasons=completion["reasons"])
    return manifest


def _attach_manifest(db, run_id, *, status="ready", with_workbook=True):
    manifest = _manifest(run_id, path=Path(db.db_path).parent / "synthetic-trade-idea.pdf",
                         status=status, with_workbook=with_workbook)
    encoded = _encoded(manifest)
    with db._conn() as conn:
        conn.execute("UPDATE trade_idea_delivery SET manifest_json=?,manifest_sha256=? "
                     "WHERE run_id=?", (encoded, sha256(encoded.encode()).hexdigest(), run_id))


def _idea(db, *, ready=True):
    with db._conn() as conn:
        ensure_schema(conn)  # Isolated test DB only; no personal migration.
        when = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
        memo_id = conn.execute("INSERT INTO memos(timestamp,title,notes) VALUES(?,?,?)",
                               (when, "Synthetic Trade Idea", "trade_idea:synthetic-run")).lastrowid
        decision_id = conn.execute(
            "INSERT INTO decisions(memo_id,timestamp,action,ticker,eur_amount,status) "
            "VALUES(?,?,'BUY','TEST',10,'PENDING')", (memo_id, when)).lastrowid
        run_id = "11111111-1111-4111-8111-111111111111"
        conn.execute("""INSERT INTO trade_idea_runs(
            id,idempotency_key,request_sha256,request_json,ticker,language,view_text,
            view_origin,models_json,catalog_snapshot_json,budget_limit_usd,context_json,
            technical_status,phase,progress_json,result_json,destination_kind,
            destination_decision_id,memo_id,created_at,updated_at,finished_at)
            VALUES(?,?,?,?,?,'it','','manual','{}','{}','5','{}',
                   'completed','completed','{}','{"judgment":"favorable"}',
                   'dcn',?,?,?,?,?)""",
            (run_id, "synthetic-key", "b" * 64, "{}", "TEST", decision_id,
             memo_id, when, when, when))
        conn.execute("INSERT INTO trade_idea_delivery(run_id,updated_at) VALUES(?,?)",
                     (run_id, when))
    if ready:
        _attach_manifest(db, run_id)
    return run_id, decision_id, memo_id


def _trade(decision_id):
    return {"ticker": "TEST", "action": "BUY", "quantita": 1, "prezzo": 10,
            "valuta": "EUR", "data": (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
            "linked_decision_id": decision_id, "link_origin": "explicit"}


def _state(db):
    with db._conn() as conn:
        return (tuple(conn.execute("SELECT balance_cents,version FROM cash_state "
                                   "WHERE singleton_id=1").fetchone()),
                conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0])


def test_trade_idea_link_requires_final_ready_pdf_even_with_completed_dcn(db):
    run_id, decision_id, _ = _idea(db, ready=False)
    context = db.trade_context("TEST", decision_id)
    assert context["trade_idea"]["id"] == run_id
    assert context["trade_idea"]["artifacts_ready"] is False
    before = _state(db)
    with pytest.raises(ValueError, match="PDF"):
        db.execute_trade(cash_delta_cents=-1000, **_trade(decision_id))
    assert _state(db) == before
    _attach_manifest(db, run_id, status="partial")
    with pytest.raises(ValueError, match="PDF"):
        db.execute_trade(cash_delta_cents=-1000, **_trade(decision_id))
    assert _state(db) == before


def test_completed_trade_idea_with_ready_manifest_can_execute_linked_trade(db):
    _, decision_id, _ = _idea(db)
    preview = db.trade_context("TEST", decision_id)
    assert preview["trade_idea"]["artifacts_ready"] is True
    before = _state(db)
    committed = db.execute_trade(cash_delta_cents=-1000,
                                 expected_context=preview["fingerprint"],
                                 **_trade(decision_id))
    assert committed["decisione"]["id"] == decision_id
    after = _state(db)
    assert after[0][0] == before[0][0] - 1000
    assert after[1] == before[1] + 1
    assert after[2] == before[2] + 1


def test_ready_pdf_without_economic_workbook_cannot_execute_linked_trade(db):
    run_id, decision_id, _ = _idea(db, ready=False)
    _attach_manifest(db, run_id, with_workbook=False)
    preview = db.trade_context("TEST", decision_id)
    assert preview["trade_idea"]["artifacts_ready"] is False
    before = _state(db)
    with pytest.raises(ValueError, match="PDF|Excel|Trade Idea"):
        db.execute_trade(cash_delta_cents=-1000, **_trade(decision_id))
    assert _state(db) == before


@pytest.mark.parametrize("change", ["delete", "tamper"])
def test_workbook_bytes_changed_after_preview_block_linked_trade_atomically(db, change):
    _, decision_id, _ = _idea(db)
    preview = db.trade_context("TEST", decision_id)
    assert preview["trade_idea"]["artifacts_ready"] is True
    before = _state(db)
    workbook = Path(db.db_path).parent / "synthetic-trade-idea.xlsx"
    if change == "delete":
        workbook.unlink()
    else:
        workbook.write_bytes(b"changed since sealing")
    with pytest.raises(memory_db.RicalcoloImpossibile):
        db.execute_trade(cash_delta_cents=-1000, expected_context=preview["fingerprint"],
                         **_trade(decision_id))
    with pytest.raises(ValueError, match="PDF|Excel|Trade Idea"):
        db.execute_trade(cash_delta_cents=-1000, **_trade(decision_id))
    assert _state(db) == before


@pytest.mark.parametrize("change", ["delete", "tamper"])
def test_pdf_bytes_changed_after_preview_block_linked_trade_atomically(db, change):
    _, decision_id, _ = _idea(db)
    preview = db.trade_context("TEST", decision_id)
    assert preview["trade_idea"]["artifacts_ready"] is True
    before = _state(db)
    pdf = Path(db.db_path).parent / "synthetic-trade-idea.pdf"
    if change == "delete":
        pdf.unlink()
    else:
        pdf.write_bytes(b"changed since sealing")
    with pytest.raises(memory_db.RicalcoloImpossibile):
        db.execute_trade(cash_delta_cents=-1000, expected_context=preview["fingerprint"],
                         **_trade(decision_id))
    with pytest.raises(ValueError, match="PDF"):
        db.execute_trade(cash_delta_cents=-1000, **_trade(decision_id))
    assert _state(db) == before


@pytest.mark.parametrize("drift", ["run_status", "manifest"])
def test_trade_idea_drift_after_preview_rolls_back_trade_and_cash(db, drift):
    run_id, decision_id, _ = _idea(db)
    preview = db.trade_context("TEST", decision_id)
    assert preview["trade_idea"]["artifacts_ready"] is True
    assert TradeIdeaStore(db.db_path, mandate_loader=lambda: "a" * 64).lookup_decision(
        decision_id)["artifacts_ready"] is True
    before = _state(db)
    if drift == "run_status":
        with db._conn() as conn:
            conn.execute("UPDATE trade_idea_runs SET technical_status='incomplete' WHERE id=?",
                         (run_id,))
    else:
        _attach_manifest(db, run_id, status="partial")
    assert db.trade_context("TEST", decision_id)["fingerprint"] != preview["fingerprint"]
    with pytest.raises(memory_db.RicalcoloImpossibile, match="anteprima cambiata"):
        db.execute_trade(cash_delta_cents=-1000, expected_context=preview["fingerprint"],
                         **_trade(decision_id))
    assert _state(db) == before
    with pytest.raises(ValueError, match="Trade Idea|PDF"):
        db.execute_trade(cash_delta_cents=-1000, **_trade(decision_id))
    assert _state(db) == before


def test_pm_touched_demotion_blocked_dcn_cannot_execute(db):
    run_id, decision_id, _ = _idea(db)
    preview = db.trade_context("TEST", decision_id)
    before = _state(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO decision_notes(decision_id,autore,testo) "
                     "VALUES(?,'PM','Do not lose this note')", (decision_id,))
    store = TradeIdeaStore(db.db_path, mandate_loader=lambda: "a" * 64)
    with pytest.raises(RunConflict, match="PM"):
        store.demote_unreviewed_destination(run_id, "Final PDF quality fell to partial")
    assert store.lookup_decision(decision_id)["technical_status"] == "incomplete"
    with pytest.raises(memory_db.RicalcoloImpossibile, match="anteprima cambiata"):
        db.execute_trade(cash_delta_cents=-1000, expected_context=preview["fingerprint"],
                         **_trade(decision_id))
    with pytest.raises(ValueError, match="Trade Idea"):
        db.execute_trade(cash_delta_cents=-1000, **_trade(decision_id))
    assert _state(db) == before


def test_ordinary_decision_without_trade_idea_schema_remains_linkable(db):
    when = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    with db._conn() as conn:
        decision_id = conn.execute(
            "INSERT INTO decisions(timestamp,action,ticker,status) "
            "VALUES(?,'BUY','TEST','PENDING')", (when,)).lastrowid
    context = db.trade_context("TEST", decision_id)
    assert context["trade_idea"] is None
    result = db.execute_trade(cash_delta_cents=-1000,
                              expected_context=context["fingerprint"], **_trade(decision_id))
    assert result["decisione"]["id"] == decision_id
    assert _state(db)[1] == 1


def test_missing_delivery_table_for_trade_idea_is_not_silent(db):
    _, decision_id, _ = _idea(db, ready=False)
    with db._conn() as conn:
        conn.execute("DROP TABLE trade_idea_delivery")
    with pytest.raises(sqlite3.OperationalError, match="trade_idea_delivery"):
        db.trade_context("TEST", decision_id)


def test_tagged_trade_idea_memo_without_run_link_is_not_treated_as_weekly(db):
    run_id, decision_id, _ = _idea(db)
    with db._conn() as conn:
        conn.execute("UPDATE trade_idea_runs SET destination_kind='none',"
                     "destination_decision_id=NULL WHERE id=?", (run_id,))
    with pytest.raises(ValueError, match="Trade Idea"):
        db.trade_context("TEST", decision_id)
