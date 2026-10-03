"""Authorization, ledger and restart controls with isolated run storage."""
from copy import deepcopy
from decimal import Decimal
import sqlite3
import io
import json

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists.base import Blackboard
from bellomberg.storage.trade_idea_store import RunConflict
from test_trade_idea_store import db_path, migrated, request, store, result, finish


def test_cost_phases_partition_measured_reserved_and_unknown_requests(migrated):
    current = store(migrated)
    run_id = current.create_run(request(), idempotency_key="phase-costs")["run"]["id"]
    current.claim_run(run_id)
    entries = [("prepare", "aux:preparation", "0.30"),
               ("desk", "specialist:fundamentals", "0.20"),
               ("revise", "aux:revision", "0.10"),
               ("review", "specialist:macro:model_revision", None)]
    for key, role, charge in entries:
        assert current.reserve_cost(run_id, key, role, "meta/muse-spark-1.3", "0.50")
        if charge is not None:
            current.reconcile_cost(run_id, key, charged_usd=charge,
                usage={"cost_usd": charge},
                receipt={"response_id": "synthetic-" + key, "model": "meta/muse-spark-1.3"})
    before = current.get_run(run_id)["cost"]
    assert before["by_phase"]["model_preparation"]["charged_usd"] == "0.30"
    assert before["by_phase"]["committee"]["charged_usd"] == "0.20"
    assert before["by_phase"]["model_revision"]["charged_usd"] == "0.10"
    assert before["by_phase"]["model_revision"]["reserved_usd"] == "0.50"
    current.mark_cost_unknown(run_id, "review", reason="Synthetic disconnect after dispatch")
    after = current.get_run(run_id)["cost"]
    phases = list(after["by_phase"].values())
    assert sum(Decimal(row["charged_usd"]) for row in phases) == Decimal(after["charged_usd"])
    assert sum(Decimal(row["reserved_usd"]) for row in phases) == Decimal(after["reserved_usd"])
    assert sum(row["unknown_requests"] for row in phases) == after["unknown_requests"] == 1
    assert sum(row["requests"] for row in phases) == after["requests"] == 4
    assert after["remaining_known_usd"] is None


def test_refresh_requiring_review_is_never_recovered_or_delivered(migrated, monkeypatch, tmp_path):
    from bellomberg.api import trade_idea_routes as routes
    current = store(migrated)
    run_id = current.create_run(request(), idempotency_key="refresh-review")["run"]["id"]
    finish(current, run_id, result(judgment="rejected", proposal=False))
    current.route_result(run_id, {})
    with sqlite3.connect(migrated) as conn:
        conn.execute("UPDATE trade_idea_runs SET technical_status='incomplete',phase='review_required',"
                     "reason='Source/model refresh requires a new review' WHERE id=?", (run_id,))
        decisions_before = conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
    before = current.get_run(run_id)
    touched = []
    monkeypatch.setattr(routes, "_worker_state", lambda *_: touched.append("worker") or (None, False))
    recovered = routes.recover_orphan_runs(migrated,
        delivery=lambda *_a, **_k: touched.append("delivery"),
        quote_sampler=lambda *_: touched.append("quote"),
        portfolio_loader=lambda: touched.append("portfolio"))
    assert recovered["errors"] == [] and recovered["recovered"] == recovered["interrupted"] == 0
    assert touched == []
    with pytest.raises(RuntimeError, match="new review"):
        trade_idea.deliver_trade_idea(current, run_id, output_dir=tmp_path,
            send=lambda *_a, **_k: touched.append("send"))
    with pytest.raises(RunConflict):
        current.claim_email(run_id)
    after = current.get_run(run_id)
    assert after["run"] == before["run"] and after["result"] == before["result"]
    assert after["artifacts"] == before["artifacts"] and after["cost"]["requests"] == 0
    assert touched == []
    with sqlite3.connect(migrated) as conn:
        assert conn.execute("SELECT * FROM decisions ORDER BY id").fetchall() == decisions_before


def test_progress_preserves_revision_inputs_and_previous_objection_replies(tmp_path):
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="TEST")
    board.r2_specialists = set()
    board.data["_revision_requests"] = [{"id": "model-review-1", "changes": {"method_records": []},
        "evidence_refs": ["audited-bridge"], "needs_paid_preparation": False}]
    board.data["_objection_history"] = [{"id": "debt-bridge", "round": 2,
        "previous": {"response": "Prior answer to the debt challenge", "state": "answered"}}]
    saved = trade_idea._progress(board, "review")
    assert saved.get("revision_requests") == board.data["_revision_requests"]
    assert saved.get("objection_history") == board.data["_objection_history"]


def test_objection_reply_cannot_claim_a_nonexistent_model_revision(tmp_path):
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="TEST")
    board.current_round = 2
    board.source_qualification = {"source_report": {"documents": [{"id": "filing"}]}}
    board.data["_objections"] = [{"objection": {"id": "bridge", "desk": "fundamentals"},
        "response": None, "evidence_refs": [], "state": "open", "model_revision_id": None}]
    before = deepcopy(board.data["_objections"])
    reply = trade_idea.handle_trade_idea_review_tool(board, "fundamentals", "respond_trade_idea_objection", {
        "objection_id": "bridge", "response": "The new workbook resolves the bridge", "state": "answered",
        "evidence_refs": ["filing"], "model_revision_id": "model-review-invented"})
    assert reply["ok"] is False and "revision" in reply["error"]
    assert board.data["_objections"] == before


def test_exact_observational_model_backs_receipts_without_inventing_fair_value(migrated, tmp_path):
    from test_trade_idea_exposure import sourced_exposure
    from bellomberg.valuation.trade_idea_model import prepare
    from bellomberg.agents.specialists.base import _trade_idea_tool_receipt_success
    qualification, plan = sourced_exposure(tmp_path)
    payload = prepare(qualification, lambda *_: deepcopy(plan), tmp_path / "model")
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="SYNTH-ETF")
    board.model_registry = trade_idea._model_registry(migrated)
    board.model_roots = [tmp_path]
    registered = trade_idea._record_candidate_model(board, payload)
    assert registered["_thesis_saved"]["snapshot_id"] == payload["snapshot_id"]
    assert board.tool_receipts[-1]["success"] is True
    assert _trade_idea_tool_receipt_success({"ok": True, "data": registered}, "get_valuation")
    verified = trade_idea._verified_candidate_valuations(board)
    assert len(verified) == 1 and verified[0]["generation_id"] == payload["generation_id"]
    assert verified[0]["model_exhibits"]["status"] == "complete"
    assert all(registered.get("fair_value_" + scenario) is None for scenario in ("bear", "base", "bull"))
    board.record_valuation("SYNTH-ETF", {"error": "Incomplete newer exposure", "method": "exposure_analysis",
        "analysis_usability": {"usable": False}}, "failed-new-model")
    assert board.valuation_results["SYNTH-ETF"]["generation_id"] == payload["generation_id"]


def test_preview_uses_the_same_isolated_model_roots_as_the_committee(migrated, tmp_path, monkeypatch):
    from test_trade_idea_economic import qualified, _operating_plan
    from bellomberg.valuation.trade_idea_model import prepare
    from bellomberg.reporting import trade_idea_report
    qualification = qualified(tmp_path)
    payload = prepare(qualification, lambda *_: deepcopy(_operating_plan()), tmp_path / "model")
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="SYNTH-EXT")
    board.model_registry = trade_idea._model_registry(migrated)
    board.model_roots = [tmp_path]
    trade_idea._record_candidate_model(board, payload)
    references = [{key: payload[key] for key in ("snapshot_id", "generation_id", "valuation_date")}
                  | {"interpretation": "Exact isolated generation"}]
    rendered = []
    def render(*_a, **kwargs):
        rendered.extend(kwargs["valuations"])
        return {"quality": {"status": "ready"}}
    monkeypatch.setattr(trade_idea_report, "build_trade_idea_report", render)
    trade_idea._preview_quality({"ticker": "SYNTH-EXT", "language": "en"},
        {"valuation_refs": references}, tmp_path / "delivery", board)
    assert len(rendered) == 1 and rendered[0]["status"] == "ready"


def test_exact_etf_identity_can_reach_free_method_qualification():
    calls = []
    def opener(request, **_kwargs):
        calls.append(request.full_url)
        document = ({"quotes": [{"symbol": "SYNTH-ETF", "quoteType": "ETF", "shortname": "Synthetic fund",
            "exchDisp": "Synthetic exchange", "currency": "EUR"}]}
            if len(calls) == 1 else {"chart": {"result": [{"meta": {"symbol": "SYNTH-ETF", "currency": "EUR",
                "instrumentType": "ETF", "longName": "Synthetic fund", "fullExchangeName": "Synthetic exchange"}}]}})
        return io.StringIO(json.dumps(document))
    identity = trade_idea.resolve_ticker_identity("SYNTH-ETF", opener=opener)
    assert identity["status"] == "confirmed", identity
    assert identity["ticker"] == "SYNTH-ETF" and identity["currency"] == "EUR"
    assert len(calls) == 2


def test_search_conflicting_instrument_identity_is_not_resolved_with_a_proxy():
    def opener(request, **_kwargs):
        assert "/search?" in request.full_url
        return io.StringIO(json.dumps({"quotes": [{"symbol": "SYNTH-ETF", "quoteType": kind,
            "shortname": "Synthetic instrument", "exchDisp": "Synthetic exchange", "currency": "EUR"}
            for kind in ("EQUITY", "ETF")]}))
    identity = trade_idea.resolve_ticker_identity("SYNTH-ETF", opener=opener)
    assert identity["status"] == "ambiguous"
