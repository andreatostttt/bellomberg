"""Ordinary weekly entry point: failures, durable continuation and artifact recovery."""
from pathlib import Path
import json

import httpx
import pytest

from test_cablaggio_consigliere_multi import run_offline, _DeskFinto, _DeskFundamentals, _DeskQuant
from bellomberg.agents import consigliere_multi as cm
from bellomberg.storage import memory_db
from bellomberg.storage.weekly_run_store import WeeklyRunStore, WeeklyRunBlocked


def _store():
    db = memory_db.MemoryDB()
    with db._conn() as conn:
        memo_id = conn.execute("SELECT MAX(memo_id) FROM weekly_runs").fetchone()[0]
    return WeeklyRunStore(db, memo_id)


def _research_contract(run_offline, monkeypatch):
    """ZR 05/10: una run NUOVA nasce col contratto research (fundamentals_research_v1);
    il contratto legacy con workbook e' archiviato (1326312) e la sua ripresa analitica e'
    bloccata per decisione PM. I desk finti scrivono il rapporto senza produrre workbook."""
    monkeypatch.setattr(cm, "_weekly_contract", run_offline.native_weekly_contract)

    def research_report(self, round_n):
        self.run_result_status = "complete"
        self.bb.write(self.name, round_n, "Synthetic research report %s R%d " % (self.name, round_n) + "x" * 200)
    monkeypatch.setattr(_DeskFinto, "run", research_report)


@pytest.mark.parametrize("broken", ["database", "portfolio", "placeholder"])
def test_prerequisite_failure_prevents_every_paid_phase(run_offline, monkeypatch, broken):
    def fail(*args, **kwargs):
        raise OSError("synthetic " + broken + " failure")
    if broken == "database":
        monkeypatch.setattr(cm, "MemoryDB", fail)
    elif broken == "portfolio":
        monkeypatch.setattr(memory_db.MemoryDB, "get_portfolio_summary", fail)
    else:
        monkeypatch.setattr(memory_db.MemoryDB, "save_memo", fail)
    with pytest.raises(OSError, match=broken):
        cm.run_multi_agent(send_email=False)
    assert run_offline.sondati == [] and run_offline.catturato == {} and run_offline.inviati == []


def test_missing_database_does_not_bootstrap_an_empty_book_or_dispatch(run_offline, monkeypatch, tmp_path):
    absent = tmp_path / 'configured-but-missing.db'
    monkeypatch.setattr(memory_db, 'SQLITE_PATH', str(absent))
    with pytest.raises(WeeklyRunBlocked, match='DB configurato assente'):
        cm.run_multi_agent(send_email=False)
    assert not absent.exists()
    assert run_offline.sondati == [] and run_offline.catturato == {} and run_offline.inviati == []


@pytest.mark.parametrize("fault", ["503", "504", "timeout", "disconnect", "incomplete"])
def test_provider_first_cause_and_unknown_cost_survive_both_resumes(run_offline, monkeypatch, fault):
    from bellomberg.core.llm_client import OpenRouterClient
    _research_contract(run_offline, monkeypatch)
    from bellomberg.valuation import preparation_ai
    monkeypatch.setattr(preparation_ai, "live_metadata", lambda model: {
        "id": model, "context_length": 1000, "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    calls = []
    def send(request):
        calls.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("original synthetic provider timeout")
        if fault == "disconnect":
            raise httpx.RemoteProtocolError("original synthetic disconnection")
        if fault == "incomplete":
            return httpx.Response(200, json={"id": "incomplete", "model": "test/model",
                "choices": [{"message": {"content": "partial output"}, "finish_reason": None}]})
        return httpx.Response(int(fault), json={"error": {"code": int(fault), "message": "original provider cause"}})
    client = OpenRouterClient(api_key="test", max_retries=4, trasporto=httpx.MockTransport(send))
    def run(self, round_n):
        client.messages.create(model="test/model", max_tokens=20,
                               messages=[{"role": "user", "content": "frozen weekly input"}])
    monkeypatch.setattr(_DeskFundamentals, "run", run)
    with pytest.raises(Exception):
        cm.run_multi_agent(send_email=False)
    store = _store()
    # RISCRITTO sulla REGOLA PM 05/10 (V0-REDTEAM / R-0RT F-A, 06/10): il costo incerto non
    # trasforma piu' il guasto del desk in errore di run. La CAUSA ORIGINALE resta, nella lacuna
    # dichiarata del desk (con la sua richiesta e l'incertezza del costo); la run si ferma per il
    # QUORUM (Fundamentals obbligatorio), e il motivo del fermo nomina quella causa.
    gap = store.status()["desk_gaps"]["fundamentals"]
    assert gap["round"] == 0 and gap["request_id"] and gap.get("cost_uncertain") is True
    assert "costo incerto nel registro richieste (DICHIARATO" in gap["message"]
    first = store.status()["first_error"]
    assert "Comitato sotto quorum" in first["message"] and "fundamentals (" in first["message"]
    assert gap["message"].split(";")[0] in first["message"]       # la prima causa non si perde
    for _ in range(2):
        with pytest.raises(Exception) as resumed:
            cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
        assert "Run legacy in archivio" not in str(resumed.value)
    status = store.status()
    assert status["first_error"] == first
    assert len(calls) == 1 and status["status"] == "incomplete"
    assert status["request_costs"]["unknown_requests"] == 1
    assert status["request_costs"]["cost_usd"] is None
    assert status["request_costs"]["reserved_usd"] > 0
    assert run_offline.catturato == {} and run_offline.inviati == []


def test_crash_resume_second_resume_reuses_completed_reports(run_offline, monkeypatch):
    # ZR 05/10: era ..._and_workbook sul contratto legacy (corpo originale in
    # archive/private/attic/tests_excel_archiviato_20261005/test_weekly_recovery_legacy.py). Il riuso dei rapporti
    # completati vale sul contratto research vivo; il riuso del workbook e' codice archiviato.
    _research_contract(run_offline, monkeypatch)
    original = _DeskFinto.run
    attempts = []
    fail_once = [True]
    def run(self, round_n):
        attempts.append((self.name, round_n))
        if self.name == "quant" and round_n == 1 and fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("crash before the quant request")
        return original(self, round_n)
    monkeypatch.setattr(_DeskFinto, "run", run)
    with pytest.raises(RuntimeError, match="crash before"):
        cm.run_multi_agent(send_email=False)
    store = _store()
    before = store.get("desk:fundamentals:1")
    completed = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert completed["status"] == "completed"
    assert store.get("desk:fundamentals:1") == before
    assert attempts.count(("fundamentals", 1)) == 1
    assert attempts.count(("quant", 1)) == 2
    after_first = list(attempts)
    second = cm.run_multi_agent(resume_memo_id=store.memo_id, send_email=False)
    assert second["status"] == "completed" and attempts == after_first
    with store.db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memo_decision_extractions").fetchone()[0] == 1


@pytest.mark.parametrize("fault", ["error", "truncated", "empty"])
def test_capo_failure_cannot_write_completed_memo(run_offline, monkeypatch, fault):
    def capo(*args, **kwargs):
        return ("[CAPO ERROR]: original timeout" if fault == "error" else "" if fault == "empty" else "partial memo",
                {"model": "test", "input_tokens": None, "output_tokens": None, "api_calls": 1,
                 "complete": False, "error": "original timeout" if fault == "error" else fault,
                 "stop_reason": "max_tokens" if fault == "truncated" else None})
    monkeypatch.setattr(cm, "run_capo", capo)
    # Decisione PM 04/10 (comitato a lacune): Capo fallito = memo PARZIALE marcato
    # INCOMPLETO, nessuna decisione, nessuna email automatica (prima: run ferma).
    result = cm.run_multi_agent(send_email=True)
    store = _store()
    assert result["status"] == "incomplete" and result["analytical_status"] == "partial"
    assert result["first_error"]["phase"] == "capo"
    assert result["first_error"]["message"].startswith("Capo non completo")
    assert store.get("capo") is None and store.get("memo_validated") is None
    assert store.get("decisions_finalized") is None
    assert run_offline.inviati == []
    partial = result["capo_partial"]
    assert Path(partial["markdown_path"]).read_text(encoding="utf-8").startswith("# MEMO INCOMPLETO")
    assert partial["decisions"] == "none" and partial["email"] == "not_sent"
    with store.db._conn() as conn:
        assert conn.execute("SELECT full_markdown FROM memos").fetchone()[0] == "[IN PROGRESS]"
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_pdf_failure_preserves_memo_and_recovery_uses_real_renderer_without_ai(run_offline, monkeypatch):
    import sys
    from bellomberg.reporting import pdf_report
    pdf_module = sys.modules["bellomberg.reporting.pdf_institutional"]
    def fail(**kwargs):
        raise RuntimeError("synthetic PDF renderer failure")
    monkeypatch.setattr(pdf_module, "build_institutional_memo", fail)
    monkeypatch.setattr(pdf_report, "build_pdf_report", fail)
    with pytest.raises(WeeklyRunBlocked, match="PDF richiesto assente"):
        cm.run_multi_agent(send_email=False)
    store = _store()
    assert store.get("memo_validated") is not None
    monkeypatch.delitem(sys.modules, "bellomberg.reporting.pdf_institutional")
    from importlib import import_module
    renderer = import_module("bellomberg.reporting.pdf_institutional")
    # The normal renderer, not a dummy, renders the frozen memo/context.
    monkeypatch.setattr(renderer, "REPORT_DIR", Path(cm.REPORT_DIR))
    calls = []
    monkeypatch.setattr(cm, "run_capo", lambda *a, **k: calls.append("capo"))
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True)
    assert calls == [] and result["artifact_status"] == "available"
    assert run_offline.inviati == []  # Decisions were not finalized before the PDF failure.
    assert result["status"] == "incomplete" and result["phase"] == "decisions_pending"
    pdf = next(Path(row["path"]) for row in result["artifacts"] if row["kind"] == "PDF")
    assert pdf.read_bytes().startswith(b"%PDF-") and pdf.stat().st_size > 1000


def test_delivery_recovery_and_second_recovery_never_repeat_capo_or_email(run_offline, monkeypatch):
    result = cm.run_multi_agent(send_email=True)
    assert len(run_offline.inviati) == 1
    monkeypatch.setattr(cm, "run_capo", lambda *a, **k: pytest.fail("delivery invoked AI"))
    for _ in range(2):
        recovered = cm.run_multi_agent(resume_memo_id=result["memo_id"], delivery_only=True, send_email=True)
        assert recovered["status"] == "completed"
    assert len(run_offline.inviati) == 1


def test_parallel_resume_and_snapshot_tampering_are_rejected(run_offline):
    result = cm.run_multi_agent(send_email=False)
    store = _store()
    with store.claim(mode="first"):
        assert store.status()["worker_active"] and not store.status()["resume_available"]
        with pytest.raises(WeeklyRunBlocked, match="gia in corso"):
            with WeeklyRunStore(store.db, store.memo_id).claim(mode="second"):
                pytest.fail("duplicate worker admitted")
    with store.db._conn() as conn:
        raw = json.loads(conn.execute("SELECT snapshot_json FROM weekly_runs").fetchone()[0])
        raw["payload"]["data"]["fundamentals"]["1"] = "modified source"
        conn.execute("UPDATE weekly_runs SET snapshot_json=?", (json.dumps(raw),))
    with pytest.raises(WeeklyRunBlocked, match="Snapshot di ripresa modificato"):
        cm.run_multi_agent(resume_memo_id=result["memo_id"], delivery_only=True, send_email=False)


def test_incomplete_requested_workbook_blocks_completion_and_email(run_offline, monkeypatch):
    original = _DeskFinto.run
    def run(self, round_n):
        value = original(self, round_n)
        if self.name == "fundamentals" and round_n == 1:
            self.bb.record_valuation("BROKEN", {"error": "verified sources missing"}, self.name)
        return value
    monkeypatch.setattr(_DeskFinto, "run", run)
    with pytest.raises(WeeklyRunBlocked, match="Workbook richiesto non consegnabile"):
        cm.run_multi_agent()
    store = _store()
    assert store.status()["status"] == "incomplete" and not run_offline.inviati
    assert store.get("memo_validated") is not None


def test_changed_book_blocks_analytical_resume_but_saved_material_remains(run_offline, monkeypatch):
    _research_contract(run_offline, monkeypatch)
    original = _DeskFinto.run
    def run(self, round_n):
        if self.name == "quant" and round_n == 1:
            raise RuntimeError("synthetic crash before quant")
        return original(self, round_n)
    monkeypatch.setattr(_DeskFinto, "run", run)
    with pytest.raises(RuntimeError, match="before quant"):
        cm.run_multi_agent(send_email=False)
    store = _store()
    saved = store.get("desk:fundamentals:1")
    with store.db._conn() as conn:
        conn.execute("INSERT OR REPLACE INTO cash_state VALUES(1,100,'2026-01-01','synthetic change',1)")
    with pytest.raises(WeeklyRunBlocked, match="Book cambiato"):
        cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert store.get("desk:fundamentals:1") == saved


def test_modified_original_markdown_is_preserved_on_delivery_recovery(run_offline):
    result = cm.run_multi_agent(send_email=False)
    md = next(Path(row["path"]) for row in result["artifacts"] if row["kind"] == "Markdown")
    md.write_text("PM annotated original", encoding="utf-8")
    with pytest.raises(WeeklyRunBlocked, match="Originale modificato"):
        cm.run_multi_agent(resume_memo_id=result["memo_id"], delivery_only=True, send_email=False)
    assert md.read_text(encoding="utf-8") == "PM annotated original"


def test_status_reads_reservation_durably_after_snapshot_and_refuses_missing_journal(run_offline, monkeypatch):
    from bellomberg.core.request_journal import RequestJournal
    result = cm.run_multi_agent(send_email=False)
    store = _store()
    journal_path = Path(result["request_journal_path"])
    journal = RequestJournal(journal_path, run_id=store.run_id,
        authorization={"scope": "weekly", "memo_id": store.memo_id,
            "context_sha256": result["context_sha256"], "authorized_usd": None},
        metadata=lambda model: {"id": model, "context_length": 1000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    journal.prepare({"model": "test/model", "max_tokens": 20,
                     "messages": [{"role": "user", "content": "reserved before a hard crash"}]},
                    {"phase": "synthetic_crash"})
    status = store.status()
    assert status["request_costs"]["unknown_requests"] == 1
    assert status["request_costs"]["cost_usd"] is None
    assert status["status"] == "incomplete" and status["resume_available"] is False
    journal_path.unlink()
    status = store.status()
    assert status["request_costs"]["unavailable"] is True and status["resume_available"] is False
    assert not journal_path.exists()
    recovered = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=False)
    assert recovered['status'] == 'incomplete' and recovered['artifact_status'] == 'available'
    # RISCRITTO sulla decisione main 06/10 (regola PM 05/10): il registro non verificabile NON
    # blocca l'email; parte col costo DICHIARATO incerto/non verificabile in testa al corpo.
    sent = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True)
    assert len(run_offline.inviati) == 1 and sent["delivery_status"] == "sent"
    corpo = next(part.get_payload(decode=True).decode("utf-8") for part in run_offline.inviati[0].walk()
                 if part.get_content_type() == "text/html")
    assert "COSTO DELLA RUN INCERTO" in corpo and "registro richieste non verificabile" in corpo
    assert sent["status"] == "incomplete"
    assert not journal_path.exists()


def test_status_detects_artifact_removed_after_completion_without_losing_analysis(run_offline):
    result = cm.run_multi_agent(send_email=False)
    pdf = next(Path(row["path"]) for row in result["artifacts"] if row["kind"] == "PDF")
    pdf.unlink()
    state = _store().status()
    assert state["status"] == "incomplete" and state["artifact_status"] == "recovery_needed"
    assert state["analytical_status"] == "complete" and state["delivery_recovery_available"] is True
    assert "artifacts" in state["remaining_work"] and state["artifact_issues"]


def test_unknown_reused_external_request_still_blocks_completion():
    from bellomberg.storage.weekly_run_store import costs_unresolved
    assert costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 1,
        "cost_usd": None, "external_requests": [{"owned": False, "state": "unknown", "cost": None}]})
    assert not costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 0,
        "cost_usd": 0, "external_requests": [{"owned": False, "state": "rejected", "cost": 0}]})


def test_delivery_restores_exact_real_pdf_excel_and_sidecar_without_generation(run_offline, monkeypatch):
    import sys
    from importlib import import_module
    from hashlib import sha256
    monkeypatch.delitem(sys.modules, 'bellomberg.reporting.pdf_institutional')
    renderer = import_module('bellomberg.reporting.pdf_institutional')
    monkeypatch.setattr(renderer, 'REPORT_DIR', Path(cm.REPORT_DIR))
    first = cm.run_multi_agent(send_email=False)
    assert {'Markdown', 'PDF', 'Excel', 'Excel metadata'} <= {row['kind'] for row in first['artifacts']}
    originals = {row['path']: row['sha256'] for row in first['artifacts']}
    for path in originals:
        Path(path).unlink()
    monkeypatch.setattr(renderer, 'build_institutional_memo', lambda **kwargs: pytest.fail('exact restore must not rerender'))
    monkeypatch.setattr(cm, 'run_capo', lambda *args, **kwargs: pytest.fail('exact restore must not call AI'))
    recovered = cm.run_multi_agent(resume_memo_id=first['memo_id'], delivery_only=True, send_email=False)
    assert recovered['status'] == 'completed'
    assert set(recovered['artifact_recovery']['restored']) == set(originals)
    for path, expected in originals.items():
        assert sha256(Path(path).read_bytes()).hexdigest() == expected
    second = cm.run_multi_agent(resume_memo_id=first['memo_id'], delivery_only=True, send_email=False)
    assert second['artifact_recovery']['restored'] == []
    assert second['artifacts'] == first['artifacts']


def test_email_intent_survives_crash_and_cannot_be_sent_twice(run_offline, monkeypatch):
    sent = []
    class Crash(BaseException):
        pass
    def fail_after_dispatch(*args, **kwargs):
        sent.append(True)
        raise Crash('synthetic lost SMTP receipt')
    monkeypatch.setattr(cm, '_send_weekly_email', fail_after_dispatch)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=True)
    store = _store()
    for _ in range(2):
        result = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True)
        assert result['status'] == 'incomplete' and result['delivery_status'] == 'uncertain'
        assert result['analytical_status'] == 'complete' and result['artifact_status'] == 'available'
        assert result['resume_available'] is False
    assert sent == [True]


def test_email_uncertainty_is_preserved_by_shared_receipt(run_offline, monkeypatch):
    calls = []
    def ambiguous(*args, delivery=None, **kwargs):
        calls.append(True)
        delivery.update(email_status='uncertain', smtp_state='sending', email_error='synthetic disconnect')
        return False
    monkeypatch.setattr(cm, '_send_weekly_email', ambiguous)
    result = cm.run_multi_agent(send_email=True)
    assert result['status'] == 'incomplete' and result['delivery_status'] == 'uncertain'
    receipt = json.loads(Path(result['delivery_manifest']).read_text(encoding='utf-8'))
    assert receipt['email_status'] == 'uncertain'
    cm.run_multi_agent(resume_memo_id=result['memo_id'], delivery_only=True, send_email=True)
    assert calls == [True]
