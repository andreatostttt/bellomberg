"""One explicit failed model response recovery; original turns/costs stay paid.

The ordinary specialist loop, ledger, checkpoints and compiler are real. Only
provider output and public market evidence are frozen by the shared fixture.
"""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.agents.specialists import base
from bellomberg.core import llm_client
from bellomberg.storage.trade_idea_store import RunConflict, IdempotencyConflict
from test_trade_idea_model_authoring_recovery import (
    native_case, migrated, db_path, bb, bind, complete, Crash, _operating_plan,
)
from test_trade_idea_response_recovery_native import rows


def stopped_failed_author(case):
    """Produce the failed 28th response through the native saved author task."""
    calls = []
    def fail_on_28(**kwargs):
        body = base._checkpoint_json(kwargs)
        calls.append(body)
        if len(calls) < 28:
            content = [llm_client.ToolUseBlock("saved-read-" + str(len(calls)), "read_blackboard", {})]
            stop, amount = "tool_use", .003
        else:
            assert len(calls) == 28 and body.get("tool_choice") is None
            content = [llm_client.ThinkingBlock("Unfinished internal attempt; no tool or report was produced.")]
            stop, amount = "error", 0
        return llm_client.Messaggio(id="old-author-task-" + str(len(calls)), model=body["model"],
            content=content, stop_reason=stop, finish_reason=stop, provider="Frozen provider",
            usage=llm_client.Usage(input_tokens=100, output_tokens=50,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
                reasoning_tokens=50 if stop == "error" else 20, cost_usd=amount))
    case.provider.create = fail_on_28
    assert complete(case) is False
    case.error_parent = case.child
    case.error_key = next(key for key in case.board.specialist_checkpoints if key.startswith("fundamentals:R1:"))
    case.error_state = deepcopy(case.board.specialist_checkpoints[case.error_key])
    assert case.error_state["status"] == "failed" and case.error_state["iteration"] == 28
    assert not case.error_state["pending_tools"] and not case.error_state["inflight_tools"]
    case.error_id = case.error_state["usage"]["request_ids"][-1]
    case.store.finish_run(case.child, case.token, None, "incomplete", reason="Frozen provider error")
    case.error_costs = rows(case.path, case.child)
    case.error_progress = deepcopy(case.store.get_run(case.child)["progress"])
    case.error_calls = calls
    case.error_tool_receipts = deepcopy(case.board.tool_receipts)
    assert len(case.error_costs) == 28 and case.error_costs[-1]["charged_usd"] == "0"
    return case


def recover(case, *, key="one-explicit-error-recovery"):
    return case.store.create_continuation(case.error_parent, idempotency_key=key,
        authorize_new_requests=True, recover_failed_request_id=case.error_id,
        budget_limit_usd="20")


def finishing_provider(case, calls, *, error_again=False):
    def create(**kwargs):
        body = base._checkpoint_json(kwargs)
        calls.append(body)
        if error_again:
            content, stop, cost = [llm_client.ThinkingBlock("Second failed response is not automatically retried.")], "error", 0
        elif len(calls) == 1:
            assert body.get("tool_choice") is None
            decisions = [{"consultation_id": row["id"], "decision": "incorporated",
                          "rationale": "Preserve the author's explicit peer assessment."}
                         for row in case.board.data["_model_consultations"]]
            content = [llm_client.ToolUseBlock("retry-save-plan", "submit_candidate_model_plan", {
                "plan": _operating_plan(), "consultation_decisions": decisions}),
                llm_client.ToolUseBlock("retry-compile-plan", "get_valuation", {"ticker": "SYNTH-EXT"})]
            stop, cost = "tool_use", .003
        else:
            assert len(calls) == 2 and body["tool_choice"] == {"type": "none"}
            content, stop, cost = [llm_client.TextBlock("Verified workbook completed from the preserved source history.")], "end_turn", .003
        return llm_client.Messaggio(id="new-author-task-" + str(len(calls)), model=body["model"],
            content=content, stop_reason=stop, finish_reason=stop, provider="Frozen provider",
            usage=llm_client.Usage(input_tokens=100, output_tokens=50,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
                reasoning_tokens=50 if stop == "error" else 20, cost_usd=cost))
    return create


def test_explicit_error_recovery_preserves_28_turns_then_builds_at29_and_reports_at30(native_case, monkeypatch):
    case = stopped_failed_author(native_case)
    with pytest.raises(RunConflict, match="incomplete_response_review_required"):
        case.store.create_continuation(case.error_parent, idempotency_key="not-explicit",
                                      authorize_new_requests=True)
    child = recover(case)
    assert recover(case)["run"]["id"] == child["run"]["id"]
    descriptor = child["run"]["continuation"]["specialist_response_recovery"]
    assert descriptor["kind"] == "failed_model_authoring" and descriptor["original_iteration"] == 28
    assert descriptor["request_id"] == case.error_id
    with pytest.raises(IdempotencyConflict):
        case.store.create_continuation(case.error_parent, idempotency_key="changed-error",
            authorize_new_requests=True, recover_failed_request_id="different-request", budget_limit_usd="20")
    case.child = child["run"]["id"]
    case.board, case.token = bind(case, case.child)
    original_calls, original_tools = deepcopy(case.error_calls), deepcopy(case.error_tool_receipts)
    calls, evidence_calls = [], []
    preparations = []
    prepare = base.Specialist._prepare_failed_model_completion
    def capture(actor, fields, key, saved, descriptor):
        preparations.append((actor, deepcopy(fields), key, deepcopy(descriptor)))
        return prepare(actor, fields, key, saved, descriptor)
    monkeypatch.setattr(base.Specialist, "_prepare_failed_model_completion", capture)
    case.provider.create = finishing_provider(case, calls)
    case.board.model_authoring_recovery_evidence = lambda selected: evidence_calls.append(deepcopy(selected)) or {
        "kind": "offline_verified_basis", "source": "Frozen fixtures only; no draft value adopted"}
    assert complete(case) is True
    assert len(calls) == 2 and len(evidence_calls) == 1
    saved = case.board.specialist_checkpoints[case.error_key]
    assert saved["status"] == "complete" and saved["iteration"] == 30
    assert saved["forced_report"] is True and saved["response_recovery_call_offset"] == 0
    assert saved["usage"]["request_ids"][:28] == case.error_state["usage"]["request_ids"]
    assert len(set(saved["usage"]["request_ids"])) == 30
    assert len(rows(case.path, case.child)) == 2
    assert Decimal(case.store.get_run(case.child)["cost"]["charged_usd"]) == Decimal("0.192")
    assert rows(case.path, case.error_parent) == case.error_costs
    assert case.store.get_run(case.error_parent)["progress"] == case.error_progress
    assert case.error_calls == original_calls
    assert case.board.tool_receipts[:len(original_tools)] == original_tools
    assert trade._verified_candidate_valuations(case.board)
    history = case.board.data["_specialist_response_recovery_history"]
    archived = next(row for row in history if row["request_id"] == case.error_id)
    assert archived["checkpoint"] == case.error_state
    assert case.board.data.get("_model_authoring_completion_failed") is not True
    actor, fields, key, descriptor = preparations[-1]
    intact = deepcopy(saved)
    intact.pop("sha256")
    # Validate a complete restored state against its final paid response before
    # demonstrating that locally resealing a substituted report/transcript fails.
    assert prepare(actor, fields, key, deepcopy(intact), descriptor)[1] == intact
    for altered in ("report", "tool_result"):
        changed = deepcopy(intact)
        if altered == "report":
            changed["report"] = "Invented complete report absent from the final provider receipt"
        else:
            block = next(block for block in changed["messages"][-1]["content"]
                         if block.get("type") == "tool_result")
            block["content"] = '{"ok": true, "invented_context": "not the delivered tool output"}'
        with pytest.raises(ValueError, match="derived failed model"):
            prepare(actor, fields, key, changed, descriptor)


def test_second_error_is_not_an_automatic_new_recovery(native_case):
    case = stopped_failed_author(native_case)
    child = recover(case)
    case.child = child["run"]["id"]
    case.board, case.token = bind(case, case.child)
    calls = []
    case.provider.create = finishing_provider(case, calls, error_again=True)
    assert complete(case) is False
    assert len(calls) == 1
    state = case.board.specialist_checkpoints[case.error_key]
    assert state["status"] == "failed" and state["iteration"] == 29
    case.store.finish_run(case.child, case.token, None, "incomplete", reason="Second explicit provider error")
    with pytest.raises(RunConflict):
        case.store.create_continuation(case.child, idempotency_key="not-a-loop", authorize_new_requests=True)
    assert rows(case.path, case.error_parent) == case.error_costs


def test_crash_after_retry_receipt_replays_once_without_new_evidence_or_counter_reset(native_case, monkeypatch):
    case = stopped_failed_author(native_case)
    case.child = recover(case)["run"]["id"]
    case.board, case.token = bind(case, case.child)
    calls, evidence_calls = [], []
    case.provider.create = finishing_provider(case, calls)
    case.board.model_authoring_recovery_evidence = lambda selected: evidence_calls.append(selected) or {
        "kind": "frozen-information", "source": "same pinned evidence on every resume"}
    reconcile = case.store.reconcile_cost
    def crash(*args, **kwargs):
        reconcile(*args, **kwargs)
        raise Crash("receipt durable, specialist response checkpoint not yet written")
    monkeypatch.setattr(case.store, "reconcile_cost", crash)
    with pytest.raises(Crash):
        complete(case)
    assert len(calls) == 1 and len(evidence_calls) == 1
    charged_child = case.child
    charged_rows = rows(case.path, charged_child)
    assert len(charged_rows) == 1 and charged_rows[0]["status"] == "charged"
    saved = deepcopy(case.board.specialist_checkpoints[case.error_key])
    assert saved["iteration"] == 28 and saved["status"] == "ready"
    pinned_history = deepcopy(case.board.data["_specialist_response_recovery_history"])
    monkeypatch.setattr(case.store, "reconcile_cost", reconcile)
    case.store.interrupt_run(charged_child, reason="Simulated crash after paid retry")
    grandchild = case.store.create_continuation(charged_child, idempotency_key="crashed-explicit-retry",
                                              authorize_new_requests=True)
    case.child = grandchild["run"]["id"]
    case.board, case.token = bind(case, case.child)
    def no_recompute(_):
        pytest.fail("The previously pinned recovery evidence was requested again")
    case.board.model_authoring_recovery_evidence = no_recompute
    assert complete(case) is True
    assert len(calls) == 2 and rows(case.path, charged_child) == charged_rows
    assert len(rows(case.path, case.child)) == 1
    assert case.board.data["_specialist_response_recovery_history"] == pinned_history
    assert case.board.specialist_checkpoints[case.error_key]["iteration"] == 30
    assert Decimal(case.store.get_run(case.child)["cost"]["charged_usd"]) == Decimal("0.192")
    assert rows(case.path, case.error_parent) == case.error_costs
    assert len(case.board.valuation_generations) == 1
    assert len(case.board.data["_model_draft_history"]) == 1


def test_completed_older_report_keeps_its_own_grant_when_active_recovery_changes(migrated, bb, monkeypatch):
    from test_trade_idea_response_recovery_native import (
        stopped_parent, child_of, bind_board, actor, FrozenProvider,
    )
    current, payload, parent, _, _, terminal, _, _, _ = stopped_parent(migrated, bb, monkeypatch)
    child = child_of(current, parent, terminal["request_id"])
    board, _ = bind_board(bb, current, child, payload)
    provider = FrozenProvider()
    specialist = actor(board, provider, monkeypatch)
    report = specialist.run(1)
    original = deepcopy(board.specialist_checkpoints["quant:R1"])
    board.specialist_response_recovery = {"kind": "failed_model_authoring",
        "checkpoint_key": "fundamentals:R1:another-native-task", "request_id": "another-error"}
    get_run = current.get_run
    def newer_active_grant(run_id):
        detail = deepcopy(get_run(run_id))
        if run_id == child:
            detail["run"]["continuation"]["specialist_response_recovery"] = deepcopy(board.specialist_response_recovery)
        return detail
    # Only the current DTO changes here. Historical authority is still read
    # from the real, sealed ancestry in SQLite, never from this view fixture.
    monkeypatch.setattr(current, "get_run", newer_active_grant)
    assert specialist.run(1) == report
    assert len(provider.calls) == 1 and board.specialist_checkpoints["quant:R1"] == original
    assert current.accepted_response_recovery(child, original["response_recovery"]) is True
    changed = {**original["response_recovery"], "request_id": "ungranted-request"}
    assert current.accepted_response_recovery(child, changed) is False
