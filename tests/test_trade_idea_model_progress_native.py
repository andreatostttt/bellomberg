"""Native author progress: new prompts, exact saved prompts and a real workbook.

Provider responses are simulated. The native source contract, draft tools,
checkpoint sealing and workbook compiler remain in use under offline_pytest.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists import base
from test_run_robustness import bb, _FakeClient, _MockSpecialist, _text_resp, _tool_resp
from test_trade_idea_economic import qualified, _operating_plan
from test_trade_idea_store import db_path, migrated


MARKER = "RECORDED MODEL WORK / MISSING AUTHOR ACTIONS:\n"
REPORT = "Offline analysis retains all missing model inputs and makes no approval claim. " * 30


class ProcessStopped(BaseException):
    """Simulate process loss without the application's Exception handler."""


def native_board(bb, tmp_path):
    bb.run_scope, bb.model_phase, bb.target_ticker = "trade_idea", "building", "SYNTH-EXT"
    bb.run_id, bb.current_round = "offline-model-progress", 1
    bb.r2_specialists = set(trade_idea.TRADE_IDEA_DESKS)
    bb.model_roots = [tmp_path]
    bb.source_qualification = qualified(tmp_path / "sources")
    bb.data["_model_input_basis"] = {
        "plan": deepcopy(bb.source_qualification["source_report"]["source_plan"]),
        "provenance": "Explicit fictional frozen source inputs"}
    bb.budget_gate = SimpleNamespace(wrap_client=lambda client, **_kw: client)
    saved = []
    # Only persistence transport is simulated; the native hook builds and seals
    # the actual checkpoint, progress and contract from this real Blackboard.
    store = SimpleNamespace(
        get_run=lambda _run_id: {"run": {"continuation": None, "stop_requested": False},
                                  "cost": {"unknown_requests": 0}},
        update_progress=lambda *args: saved.append(deepcopy(args[-1])),
        record_failure=lambda *_args: pytest.fail("Unexpected native execution failure"))
    trade_idea._configure_native_recovery(bb, store, "offline-worker-token")
    return bb, saved


def actor_for(board, client, monkeypatch, *, name="fundamentals", context="Frozen author context."):
    actor_type = type("NativeProgressDesk", (_MockSpecialist,), {"name": name})
    actor = actor_type(board, client=client)
    monkeypatch.setattr(actor, "_model_for_round", lambda _round: "synthetic/model")
    monkeypatch.setattr(actor, "_build_round_context", lambda _round: context)
    monkeypatch.setattr(actor, "_build_tools_schema", lambda: [
        {"name": tool, "description": "Native offline author tool",
         "input_schema": {"type": "object", "properties": {}}}
        for tool in ("get_candidate_model_inputs", "submit_candidate_model_plan")])
    executed = []
    def execute(name, inputs):
        executed.append((name, deepcopy(inputs)))
        return trade_idea.handle_trade_idea_review_tool(board, actor.name, name, inputs)
    monkeypatch.setattr(actor, "_execute_meta_tool", execute)
    return actor, executed


def tool_response(name, inputs, number):
    response = _tool_resp(name)
    response.id = "offline-provider-response-" + str(number)
    response.content[0].id = "offline-tool-" + str(number)
    response.content[0].input = deepcopy(inputs)
    return response


def ledgers(messages):
    result = []
    for message in base._checkpoint_json(messages):
        content = message["content"]
        texts = ([content] if isinstance(content, str) else
                 [block["text"] for block in content if block.get("type") == "text"])
        for text in texts:
            if MARKER in text:
                result.append(json.loads(text.split(MARKER, 1)[1]))
    return result


def test_native_callback_updates_real_contract_and_incremental_draft_each_tool_turn(bb, tmp_path, monkeypatch):
    board, persisted = native_board(bb, tmp_path)
    progress_calls, requests = [], []
    native_callback = board.model_authoring_progress
    def progress(**kwargs):
        before = deepcopy(board.data)
        result = native_callback(**kwargs)
        assert board.data == before
        progress_calls.append(deepcopy(result))
        return result
    board.model_authoring_progress = progress
    plan = board.data["_model_input_basis"]["plan"]
    sequence = [
        ("get_candidate_model_inputs", {"scope": "model", "contract_section": "evidence"}),
        ("get_candidate_model_inputs", {"scope": "base", "drivers": ["revenue_growth"]}),
        ("submit_candidate_model_plan", {
            "rationale": "Explicitly retain this fictional supported estimate after reading it",
            "reuse": [{"scope": "base", "driver": "revenue_growth",
                       "basis_plan_sha256": trade_idea._plan_digest(plan),
                       "driver_sha256": trade_idea._plan_digest(plan["scenarios"]["base"]["revenue_growth"])}]})]
    def provider(number, kwargs):
        requests.append(base._checkpoint_json(kwargs))
        if number <= len(sequence):
            name, inputs = sequence[number - 1]
            return tool_response(name, inputs, number)
        return _text_resp(REPORT)
    actor, executed = actor_for(board, _FakeClient(provider), monkeypatch)
    assert actor.run(1) == REPORT
    assert len(requests) == 4 and len(executed) == 3
    assert [entry["remaining_turns"] for entry in progress_calls] == [29, 28, 27, 26]
    assert [len(ledgers(request["messages"])) for request in requests] == [1, 2, 3, 4]
    assert all(request["max_tokens"] == 128000 for request in requests)
    assert actor._max_tool_iterations_for_round(1) == 30
    assert progress_calls[0]["admitted_document_ids"] == sorted(
        row["id"] for row in board.source_qualification["source_report"]["documents"])
    assert progress_calls[0]["draft"]["present"] is False
    evidence_read = progress_calls[1]["contract_reads"]["evidence"]
    assert evidence_read["status"] == "observed_partial"
    assert 0 < evidence_read["next_offset"] < evidence_read["total_chars"]
    assert any("contract_section=evidence, offset=" + str(evidence_read["next_offset"]) in action
               for action in progress_calls[1]["next_actions"])
    assert progress_calls[-1]["draft"]["scopes"]["base"]["stored"] == ["revenue_growth"]
    assert progress_calls[-1]["draft"]["proofs_validated"] is False
    assert progress_calls[-1]["consultations"]["missing_desks"] == sorted(
        set(trade_idea.TRADE_IDEA_DESKS) - {"fundamentals"})
    assert all(entry["kind"] == "informational_not_approval" for entry in progress_calls)
    assert board.valuation_results == {} and "_model_review" not in board.data
    assert "model_authoring" in trade_idea._remaining_work(board)["remaining"]
    checkpoint = board.specialist_checkpoints["fundamentals:R1"]
    assert checkpoint["author_progress_version"] == 1
    assert persisted and all(row["checkpoint_sha256"] == trade_idea._plan_digest(row["checkpoint"])
                             for row in persisted)


def test_new_progress_checkpoint_resumes_exact_tool_boundary_and_second_resume_dispatches_nothing(bb, tmp_path, monkeypatch):
    board, _ = native_board(bb, tmp_path)
    progress_calls = []
    original_progress = board.model_authoring_progress
    def progress(**kwargs):
        progress_calls.append(deepcopy(kwargs))
        return original_progress(**kwargs)
    board.model_authoring_progress = progress
    original_persist = board.persist_run_checkpoint
    def crash_after_tool(event, payload=None):
        original_persist(event, payload)
        if event == "specialist_turn":
            raise ProcessStopped()
    board.persist_run_checkpoint = crash_after_tool
    client = _FakeClient(lambda number, _kw: tool_response("get_candidate_model_inputs",
        {"scope": "model", "contract_section": "evidence"}, number))
    actor, executed = actor_for(board, client, monkeypatch)
    with pytest.raises(ProcessStopped):
        actor.run(1)
    saved = deepcopy(board.specialist_checkpoints["fundamentals:R1"])
    assert len(client.calls) == len(executed) == 1 and len(progress_calls) == 2
    assert saved["author_progress_version"] == 1 and len(ledgers(saved["messages"])) == 2
    board.persist_run_checkpoint = original_persist
    # This new fact must not be retroactively inserted into a saved paid request.
    board.data["_model_plan_last_error"] = "New diagnostic after the saved tool boundary"
    requests = []
    def finish(_number, kwargs):
        requests.append(base._checkpoint_json(kwargs))
        return _text_resp(REPORT)
    resumed, new_tools = actor_for(board, _FakeClient(finish), monkeypatch, context="Changed live context")
    assert resumed.run(1) == REPORT
    assert requests[0]["messages"] == saved["messages"]
    assert len(progress_calls) == 2 and new_tools == []
    complete = deepcopy(board.specialist_checkpoints["fundamentals:R1"])
    no_dispatch = _FakeClient(lambda *_args: pytest.fail("Completed checkpoint must not call a provider"))
    again, more_tools = actor_for(board, no_dispatch, monkeypatch)
    assert again.run(1) == REPORT
    assert not no_dispatch.calls and not more_tools
    assert board.specialist_checkpoints["fundamentals:R1"] == complete


def test_legacy_checkpoint_without_progress_marker_keeps_exact_request_and_no_new_ledgers(bb, tmp_path, monkeypatch):
    board, _ = native_board(bb, tmp_path)
    del board.model_authoring_progress
    original_requests = []
    def crash_before_response(_number, kwargs):
        original_requests.append(base._checkpoint_json(kwargs))
        raise ProcessStopped()
    actor, _ = actor_for(board, _FakeClient(crash_before_response), monkeypatch)
    with pytest.raises(ProcessStopped):
        actor.run(1)
    saved = deepcopy(board.specialist_checkpoints["fundamentals:R1"])
    assert "author_progress_version" not in saved and not ledgers(saved["messages"])
    board.model_authoring_progress = lambda **_kw: pytest.fail("Legacy history must remain exact")
    requests = []
    def provider(number, kwargs):
        requests.append(base._checkpoint_json(kwargs))
        return (tool_response("get_candidate_model_inputs", {"scope": "model"}, number)
                if number == 1 else _text_resp(REPORT))
    resumed, tools = actor_for(board, _FakeClient(provider), monkeypatch, context="New context must not enter history")
    assert resumed.run(1) == REPORT
    assert requests[0] == original_requests[0]
    assert len(requests) == 2 and len(tools) == 1
    assert all(not ledgers(request["messages"]) for request in requests)
    assert "author_progress_version" not in board.specialist_checkpoints["fundamentals:R1"]
    assert saved["messages"] == board.specialist_checkpoints["fundamentals:R1"]["messages"][:1]


@pytest.mark.parametrize("name,round_n,task", [
    ("fundamentals", 0, None), ("macro", 1, None),
    ("fundamentals", 1, {"consultation": True})])
def test_author_progress_is_not_inserted_into_other_native_specialist_scopes(bb, tmp_path, monkeypatch, name, round_n, task):
    board, _ = native_board(bb, tmp_path)
    board.model_authoring_progress = lambda **_kw: pytest.fail("This is not the model author scope")
    requests = []
    def provider(_number, kwargs):
        requests.append(base._checkpoint_json(kwargs))
        return _text_resp(REPORT)
    actor, executed = actor_for(board, _FakeClient(provider), monkeypatch, name=name)
    report = actor.run(round_n, task_context=task)
    assert report.endswith(REPORT)
    if round_n == 1:
        assert "ROUND 1 SENZA TOOL" in report
    assert len(requests) == 1 and not executed and not ledgers(requests[0]["messages"])
    assert all("author_progress_version" not in checkpoint for checkpoint in board.specialist_checkpoints.values())


def test_remaining_model_work_requires_real_verified_workbook_even_after_r1_report(bb, tmp_path, migrated):
    from bellomberg.valuation.trade_idea_model import build_from_plan
    board, _ = native_board(bb, tmp_path)
    report = "The model is not yet authored; no valuation approval is implied."
    board.write("fundamentals", 1, report)
    board.record_specialist_completion("fundamentals", 1, report, "complete")
    before = trade_idea._remaining_work(board)
    assert "fundamentals:R1" not in before["remaining"]
    assert "model_authoring" in before["remaining"]
    native_author = object.__new__(base.Specialist)
    native_author.name, native_author.blackboard = "fundamentals", board
    assert trade_idea._remaining_stage_token_cap(board, "model_authoring") == native_author._max_tokens_for_round(1)
    assert board.model_phase == "building"
    payload = build_from_plan(board.source_qualification, _operating_plan(), tmp_path / "workbook")
    # Registration is real too: a compiled but unregistered workbook must still
    # remain missing work under the ordinary delivery verification contract.
    trade_idea._record_candidate_model(board, payload)
    assert "model_authoring" in trade_idea._remaining_work(board)["remaining"]
    board.model_registry = trade_idea._model_registry(migrated)
    trade_idea._record_candidate_model(board, payload)
    assert trade_idea._verified_candidate_valuations(board)
    assert "model_authoring" not in trade_idea._remaining_work(board)["remaining"]
    workbook = Path(payload["path"])
    original = workbook.read_bytes()
    workbook.write_bytes(original + b"offline-integrity-test")
    assert "model_authoring" in trade_idea._remaining_work(board)["remaining"]
    assert board.valuation_results[board.target_ticker]["generation_id"] == payload["generation_id"]


def test_native_progress_does_not_turn_observed_partial_consultations_into_decisions(bb, tmp_path):
    board, _ = native_board(bb, tmp_path)
    draft = {"questioned_driver": "wacc", "basis": "Fictional assumption under review"}
    board.data["_model_consultations"] = [{"id": "unattested-answer", "desk": "macro", "requester": "fundamentals",
        "source_fingerprint": board.source_qualification["fingerprint"],
        "question": "How does the fictional funding assumption change this exact cash-flow driver?",
        "draft_assumptions": draft, "draft_sha256": trade_idea._plan_digest(draft),
        "evidence_refs": [board.source_qualification["source_report"]["documents"][0]["id"]],
        "status": "complete", "author_view_complete": True,
        "response": "An answer is present, but no native receipt or explicit author decision exists."}]
    before = deepcopy(board.data)
    ledger = board.model_authoring_progress(remaining_turns=1, messages=[])
    assert ledger["kind"] == "informational_not_approval"
    assert ledger["consultations"]["items"][0]["decided"] is False
    assert board.data == before and not board.valuation_results
    assert "fundamentals_decision" not in board.data["_model_consultations"][0]
