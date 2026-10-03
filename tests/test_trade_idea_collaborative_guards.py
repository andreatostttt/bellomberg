"""Exact generation, writer and draft guards for the existing Trade Idea pipeline."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists.base import Blackboard
from test_trade_idea_economic import qualified


def board_with_inputs(tmp_path):
    board = Blackboard(heartbeat_path=tmp_path / "hb.json", run_scope="trade_idea",
                       target_ticker="SYNTH-EXT")
    board.current_round, board.model_phase = 1, "building"
    board.source_qualification = qualified(tmp_path / "sources")
    board.data["_model_input_basis"] = {
        "plan": deepcopy(board.source_qualification["source_report"]["source_plan"]),
        "provenance": "Fictional qualified source inputs"}
    return board


def test_native_input_contract_is_read_without_creating_a_workbook(tmp_path):
    board = board_with_inputs(tmp_path)
    result = trade_idea.handle_trade_idea_review_tool(board, "fundamentals",
        "get_candidate_model_inputs", {"scope": "base", "drivers": ["revenue_growth"]})
    assert result["ok"] and result["status"] == "inputs_only_no_current_workbook"
    assert result["contract"]["schema"]["revenue_growth"][-1] == "scenario"
    assert board.valuation_results == {}
    assert list(tmp_path.rglob("*.xlsx")) == []


@pytest.mark.parametrize("fault", ["basis", "driver", "other_desk", "other_phase", "nonfinite"])
def test_invalid_authored_draft_is_atomic_and_never_dispatches(tmp_path, fault):
    board = board_with_inputs(tmp_path)
    plan = board.data["_model_input_basis"]["plan"]
    driver = next(iter(plan["model"]))
    payload = {"rationale": "Explicit adoption of fictional facts", "reuse": [{
        "scope": "model", "driver": driver, "basis_plan_sha256": trade_idea._plan_digest(plan),
        "driver_sha256": trade_idea._plan_digest(plan["model"][driver])}]}
    desk = "fundamentals"
    if fault == "basis": payload["reuse"][0]["basis_plan_sha256"] = "0" * 64
    if fault == "driver": payload["reuse"][0]["driver_sha256"] = "0" * 64
    if fault == "other_desk": desk = "macro"
    if fault == "other_phase": board.model_phase = "review"
    if fault == "nonfinite": payload["plan"] = {"model": {driver: {"value": float("nan")}}}
    result = trade_idea.handle_trade_idea_review_tool(board, desk, "submit_candidate_model_plan", payload)
    assert result["ok"] is False
    assert "_model_input_draft" not in board.data
    assert "_model_draft_history" not in board.data
    assert board.valuation_results == {}


def test_adopted_numeric_driver_is_exact_and_remains_unapproved(tmp_path):
    board = board_with_inputs(tmp_path)
    plan = board.data["_model_input_basis"]["plan"]
    item = plan["scenarios"]["base"]["revenue_growth"]
    result = trade_idea.handle_trade_idea_review_tool(board, "fundamentals", "submit_candidate_model_plan", {
        "rationale": "Retain this explicit archived estimate after examination",
        "reuse": [{"scope": "base", "driver": "revenue_growth",
                   "basis_plan_sha256": trade_idea._plan_digest(plan),
                   "driver_sha256": trade_idea._plan_digest(item)}]})
    assert result["ok"] and result["model_created"] is False
    assert board.data["_model_input_draft"]["scenarios"]["base"]["revenue_growth"] == item
    assert board.data["_model_input_basis"]["plan"] == plan
    assert "_model_review" not in board.data


@pytest.mark.parametrize("fault", ["missing_desk", "missing_outcome", "failed_answer", "no_plan", "wrong_ticker", "answer_not_delivered"])
def test_incomplete_dialogue_stops_compilation_before_evaluator(tmp_path, fault):
    board = board_with_inputs(tmp_path)
    board.data["_model_input_draft"] = deepcopy(board.data["_model_input_basis"]["plan"])
    rows = [{"id": name, "desk": name, "status": "complete", "author_view_complete": True,
             "fundamentals_decision": {"decision": "incorporated", "rationale": "Explicit fictional reasoning"}}
            for name in trade_idea.TRADE_IDEA_DESKS if name != "fundamentals"]
    board.data["_model_consultations"] = rows
    if fault == "missing_desk": rows.pop()
    if fault == "missing_outcome": rows[0].pop("fundamentals_decision")
    if fault == "failed_answer": rows[0]["status"] = "failed"
    if fault == "answer_not_delivered": rows[0]["author_view_complete"] = False
    if fault == "no_plan": board.data.pop("_model_input_draft")
    inputs = {"ticker": "OTHER" if fault == "wrong_ticker" else "SYNTH-EXT"}
    called = []
    result = trade_idea._build_fundamentals_candidate(board, inputs, tmp_path / "model",
        evaluator=lambda *_: called.append("forbidden"))
    assert result["ok"] is False and called == []
    assert list(tmp_path.rglob("*.xlsx")) == []


def test_large_escaped_consultation_is_read_completely_without_truncation_or_paid_retry(tmp_path):
    import json
    board = board_with_inputs(tmp_path)
    text = ('"A sourced mechanism"\n' * 1600) + "End of complete answer"
    row = {"id": "actual-answer", "desk": "macro", "status": "complete",
           "response": text, "author_view_complete": False}
    board.data["_model_consultations"] = [row]
    parts, offset = [], 0
    while offset < len(text):
        result = trade_idea.handle_trade_idea_review_tool(board, "fundamentals",
            "read_candidate_model_consultation", {"consultation_id": row["id"], "offset": offset})
        assert result["ok"] and len(json.dumps(result, ensure_ascii=False)) + 256 <= 12000
        assert result["next_offset"] > offset
        parts.append(result["response"])
        offset = result["next_offset"]
        assert row["author_view_complete"] is (offset == len(text))
    assert "".join(parts) == text and row["author_view_complete"] is True
    result = trade_idea.handle_trade_idea_review_tool(board, "fundamentals",
        "read_candidate_model_consultation", {"consultation_id": row["id"], "offset": 0})
    assert result["ok"] is False


def test_durable_build_checkpoint_preserves_unread_answer_and_exact_page_offset(tmp_path):
    board = board_with_inputs(tmp_path)
    board.current_specialist = "fundamentals"
    board.r2_specialists = set(trade_idea.TRADE_IDEA_DESKS)
    answer = '"Sourced live answer"\n' * 1700
    row = {"id": "paid-answer", "desk": "macro", "status": "complete",
           "response": answer, "author_view_complete": False}
    board.data["_model_consultations"] = [row]
    snapshots = []
    board.model_checkpoint_writer = lambda snapshot: snapshots.append(deepcopy(snapshot))
    page = trade_idea.handle_trade_idea_review_tool(board, "fundamentals",
        "read_candidate_model_consultation", {"consultation_id": row["id"], "offset": 0})
    checkpoint = snapshots[-1]["model_build_checkpoint"]
    assert checkpoint["consultations"][0]["response"] == answer
    assert checkpoint["consultations"][0]["answer_read_offset"] == page["next_offset"]
    assert checkpoint["consultations"][0]["author_view_complete"] is False
    assert checkpoint["source_fingerprint"] == board.source_qualification["fingerprint"]
    assert checkpoint["draft"] is None and board.valuation_results == {}
    prompt_view = trade_idea.candidate_model_context(board)["consultations"][0]
    assert "response" not in prompt_view
    assert prompt_view["answer_read_offset"] == page["next_offset"]
    assert prompt_view["answer_sha256"] == trade_idea._plan_digest(answer)
    assert prompt_view["response_chars"] == len(answer)
    row["response"] = "Changed live state after the write"
    assert checkpoint["consultations"][0]["response"] == answer


@pytest.mark.parametrize("fault", [None, "missing", "old_generation", "changed_report", "error"])
def test_capo_requires_all_six_current_generation_reports_before_any_request(tmp_path, monkeypatch, fault):
    board = Blackboard(heartbeat_path=tmp_path / "hb.json", run_scope="trade_idea", target_ticker="TEST")
    ref = {"snapshot_id": "snapshot", "generation_id": "new-generation", "workbook_sha256": "a" * 64}
    monkeypatch.setattr(trade_idea, "_verified_candidate_valuations", lambda _: [deepcopy(ref)])
    rows = board.data.setdefault("_desk_model_reviews", {})
    for desk in trade_idea.TRADE_IDEA_DESKS:
        report = "A complete discussion of the exact fictional model by " + desk
        board.write(desk, 2, report)
        rows[desk] = {"round": 2, "model_ref": deepcopy(ref), "report_sha256": trade_idea._plan_digest(report)}
    red_report = "A complete adversarial discussion of the exact fictional workbook"
    board.write("_red_team", 1, red_report)
    board.data["_red_model_review"] = {"model_ref": deepcopy(ref),
                                       "report_sha256": trade_idea._plan_digest(red_report)}
    if fault == "missing": rows.pop("macro")
    if fault == "old_generation": rows["macro"]["model_ref"]["generation_id"] = "old-generation"
    if fault == "changed_report": board.write("macro", 2, "A different report after attestation")
    if fault == "error": board.write("macro", 2, "[ERROR] model review failed")
    if fault is None:
        assert trade_idea._require_final_desk_models(board) == [ref]
    else:
        client = SimpleNamespace(messages=SimpleNamespace(
            create=lambda **_: pytest.fail("Capo create dispatched with stale review"),
            stream=lambda **_: pytest.fail("Capo stream dispatched with stale review")))
        with pytest.raises(RuntimeError, match="missing or stale"):
            trade_idea.run_trade_idea_capo(board, portfolio={}, mandate={}, client=client)


def test_fundamentals_builds_last_after_all_five_independent_r1_reports(tmp_path, monkeypatch):
    from bellomberg.agents import consigliere_multi
    board = Blackboard(heartbeat_path=tmp_path / "hb.json", run_scope="trade_idea", target_ticker="TEST")
    board.model_phase, board.independent_round = "research", 1
    order = []
    def run(self, round_n):
        if self.name == "fundamentals":
            assert set(order) == set(trade_idea.TRADE_IDEA_DESKS) - {"fundamentals"}
            assert self.board.model_phase == "building" and self.board.independent_round == 0
        else:
            assert self.board.model_phase == "research" and self.board.independent_round == 1
        assert self.board.valuation_results == {}
        order.append(self.name)
        self.board.write(self.name, round_n, "Independent fictional analysis")
        return self.board.read(self.name, round_n)
    classes = [type(name, (), {"name": name, "__init__": lambda self, bb: setattr(self, "board", bb), "run": run})
               for name in trade_idea.TRADE_IDEA_DESKS]
    monkeypatch.setattr(consigliere_multi, "SPECIALIST_ORDER", classes)
    consigliere_multi.run_round(board, 1)
    assert order[-1] == "fundamentals" and len(order) == 6


def test_oversized_driver_is_delivered_as_exact_complete_json_pages(tmp_path):
    import json
    board = board_with_inputs(tmp_path)
    plan = board.data["_model_input_basis"]["plan"]
    driver = next(iter(plan["model"]))
    plan["model"][driver]["rationale"] = ('"Native sourced evidence"\n' * 1700)
    parts, offset = [], 0
    while True:
        inputs = {"scope": "model", "drivers": [driver]}
        if offset:
            inputs["offset"] = offset
        page = trade_idea.handle_trade_idea_review_tool(board, "fundamentals",
            "get_candidate_model_inputs", inputs)
        assert page["ok"] and page["status"] == "input_driver_page"
        assert len(json.dumps(page, ensure_ascii=False)) + 256 <= 12000
        assert page["next_offset"] > offset
        parts.append(page["input_json_fragment"])
        offset = page["next_offset"]
        if page["complete"]:
            break
    assert json.loads("".join(parts)) == plan["model"][driver]
    assert page["driver_sha256"] == trade_idea._plan_digest(plan["model"][driver])
    assert board.valuation_results == {}


@pytest.mark.parametrize("fault", ["skipped", "not_published", "different_return", "changed_generation"])
def test_new_red_team_cannot_reuse_the_previous_generation_critique(tmp_path, monkeypatch, fault):
    board = Blackboard(heartbeat_path=tmp_path / "hb.json", run_scope="trade_idea", target_ticker="TEST")
    ref = {"snapshot_id": "snap", "generation_id": "new", "workbook_sha256": "a" * 64}
    monkeypatch.setattr(trade_idea, "_verified_candidate_valuations", lambda _: [deepcopy(ref)])
    board.write("_red_team", 1, "Old generation critique")
    board.data["_red_model_review"] = {"model_ref": {**ref, "generation_id": "old"}}
    calls = []
    def runner(bb, **kwargs):
        calls.append("red")
        assert bb.get_latest("red_team") is None
        if fault == "skipped": return ""
        if fault == "not_published": return "New critique"
        bb.write("_red_team", 1, "New critique")
        if fault == "changed_generation": ref["generation_id"] = "changed"
        return "Different returned critique" if fault == "different_return" else "New critique"
    with pytest.raises(RuntimeError, match="fresh complete Red Team"):
        trade_idea._run_exact_model_red_team(board, {}, runner)
    assert calls == ["red"] and "_red_model_review" not in board.data
    assert board.data["_red_review_history"][0]["review"]["report"] == "Old generation critique"
