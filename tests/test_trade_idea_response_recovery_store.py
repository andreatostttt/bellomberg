"""Explicit final-report continuation: immutable evidence and aggregate grants."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3

import pytest

from bellomberg.storage.trade_idea_store import BudgetBlocked, IdempotencyConflict, RunConflict, _digest
from test_trade_idea_store import db_path, migrated, request, store


def _seal_specialist(state):
    state.pop("sha256", None)
    state["sha256"] = hashlib.sha256(json.dumps(state, sort_keys=True,
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _rows(path, table, run_id):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE run_id=?", (run_id,))]


def truncated_parent(path, *, explicit_cap=False, terminal_cost="0.02"):
    current, accepted = store(path), request()
    parent = current.create_run(accepted, idempotency_key="truncated-parent")["run"]["id"]
    token = current.claim_run(parent)
    ids = ["paid-tool-turn", "paid-truncated-report"]
    for index, ident in enumerate(ids):
        usage = {"cost_usd": "0.01" if index == 0 else terminal_cost,
            "input_tokens": 20, "output_tokens": 50 if index == 0 else 16000,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "reasoning_tokens": 0 if index == 0 else 15997}
        response = {"id": "provider-" + ident, "model": accepted["models"]["specialist"]["model"],
            "request_id": ident, "stop_reason": "tool_use" if index == 0 else "max_tokens",
            "content": ([{"type": "tool_use", "id": "tool-1", "name": "read_frozen", "input": {}}]
                        if index == 0 else [{"type": "thinking", "thinking": "Preserved reasoning without final report"}]),
            "usage": usage}
        fingerprint = _digest({"frozen_body": ident, "max_tokens": 16000})
        current.reserve_cost(parent, ident, "specialist:quant", response["model"], "0.05",
            request_sha256=fingerprint, worker_token=token)
        current.reconcile_cost(parent, ident, charged_usd=usage["cost_usd"], usage=usage,
            receipt={"response_id": response["id"], "model": response["model"],
                "stop_reason": response["stop_reason"], "request_sha256": fingerprint,
                "response": response, "response_sha256": _digest(response)})
    state = {"version": 1, "contract": "c" * 64, "status": "truncated", "forced_report": True,
        "iteration": 10, "usage_unknown": False, "pending_tools": {}, "inflight_tools": {},
        "messages": [{"role": "user", "content": "Frozen report request"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "read_frozen", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "Frozen measured evidence"},
                {"type": "text", "text": "The existing final-iteration report instruction"}]}],
        "usage": {"in": 40, "out": 16050, "cost_usd": float(terminal_cost) + 0.01,
            "cache_read": 0, "cache_write": 0,
            "request_ids": ids, "tokens_status": "completo", "tokens_missing": []},
        "report": "[quant] No output produced in round 1", "tool_calls": 1,
        "thinking": {"type": "effort", "effort": "max"}, "thinking_prima": None,
        "nudge_collasso": False, "retry_529": 0, "retry_vuoto": 0, "initial_context": "Frozen context"}
    if explicit_cap:
        state["max_tokens"] = 16000
    _seal_specialist(state)
    failure = {"message": "specialist response truncated: stop_reason=max_tokens",
        "exception_type": "RuntimeError", "desk": "quant", "round": 1,
        "request_id": ids[-1], "phase": "research"}
    checkpoint = {"version": 1, "contract": {"version": 1, "ticker": accepted["ticker"],
        "language": accepted["language"], "view_text": accepted["view_text"], "models": accepted["models"],
        "source_fingerprint": accepted["source_qualification"]["fingerprint"]},
        "data": {"_primary_failure": failure, "quant": {"0": "Completed earlier report kept verbatim"}},
        "tool_receipts": [{"tool": "read_frozen", "output": "Frozen measured evidence"}],
        "specialist_checkpoints": {"quant:R1": state}}
    current.update_progress(parent, token, "checkpoint", {"checkpoint": checkpoint,
        "checkpoint_sha256": _digest(checkpoint), "primary_failure": failure})
    current.finish_run(parent, token, None, "incomplete", reason=failure["message"])
    return current, parent, ids[-1], checkpoint


def _mutate_progress(path, parent, mutate):
    with sqlite3.connect(path) as conn:
        progress = json.loads(conn.execute("SELECT progress_json FROM trade_idea_runs WHERE id=?", (parent,)).fetchone()[0])
        mutate(progress)
        progress["checkpoint_sha256"] = _digest(progress["checkpoint"])
        conn.execute("UPDATE trade_idea_runs SET progress_json=? WHERE id=?", (json.dumps(progress), parent))


@pytest.mark.parametrize("explicit_cap", [False, True])
def test_report_completion_requires_specific_authorization_and_keeps_original_evidence(migrated, explicit_cap):
    current, parent, ident, checkpoint = truncated_parent(migrated, explicit_cap=explicit_cap)
    before, costs = current.get_run(parent), _rows(migrated, "trade_idea_costs", parent)
    candidate = before["recovery"]["truncated_response_recovery"]
    assert before["recovery"]["can_continue"] is False
    assert before["recovery"]["can_complete_truncated_response"] is True
    assert candidate["original_max_tokens"] == (16000 if explicit_cap else None)
    assert candidate["replacement_max_tokens"] == 128000 and candidate["mode"] == "report_only"
    assert candidate["native_validation_required"] is True
    with pytest.raises(RunConflict, match="incomplete_response_review_required"):
        current.create_continuation(parent, idempotency_key="generic", authorize_new_requests=True)
    with pytest.raises(ValueError, match="explicit authorization"):
        current.create_continuation(parent, idempotency_key="no-spend-authorization", recover_truncated_request_id=ident)
    child = current.create_continuation(parent, idempotency_key="explicit", authorize_new_requests=True,
        recover_truncated_request_id=ident)
    assert child["created"] is True
    assert child["run"]["continuation"]["specialist_response_recovery"] == candidate
    assert child["progress"]["checkpoint"] == checkpoint
    assert child["cost"]["requests"] == 2 and child["cost"]["charged_usd"] == "0.03"
    assert child["cost"]["budget_limit_usd"] == "5.00"
    after = current.get_run(parent)
    for key in ("run", "progress", "cost", "result"):
        assert after[key] == before[key]
    assert _rows(migrated, "trade_idea_costs", parent) == costs
    assert _rows(migrated, "trade_idea_costs", child["run"]["id"]) == []
    assert current.get_run(parent)["recovery"]["can_complete_truncated_response"] is False


def test_explicit_report_completion_is_single_successor_under_concurrent_requests(migrated):
    current, parent, ident, _ = truncated_parent(migrated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        children = list(pool.map(lambda key: current.create_continuation(parent, idempotency_key=key,
            authorize_new_requests=True, recover_truncated_request_id=ident), ["first", "second"]))
    assert sum(item["created"] for item in children) == 1
    assert children[0]["run"]["id"] == children[1]["run"]["id"]
    with pytest.raises(IdempotencyConflict, match="different response"):
        current.create_continuation(parent, idempotency_key="third", authorize_new_requests=True,
            recover_truncated_request_id="a-different-response")
    events = _rows(migrated, "trade_idea_events", children[0]["run"]["id"])
    assert [row["kind"] for row in events] == ["continuation_accepted"]


@pytest.mark.parametrize("fault", ["failed", "second_truncated", "consultation", "r2", "forced_report",
    "new_cap", "bool_cap", "iteration", "usage_unknown", "token_gap", "pending", "inflight",
    "specialist_seal", "source_contract", "primary_id", "primary_phase", "already_complete",
    "receipt_seal", "receipt_identity", "receipt_model", "receipt_cost", "request_digest", "unknown",
    "terminal_stop", "terminal_tool", "terminal_tokens", "foreign_request", "book", "feedback",
    "aggregate_cost", "aggregate_in", "aggregate_out", "aggregate_cache", "aggregate_missing"])
def test_invalid_report_completion_never_creates_a_successor_or_rewrites_evidence(migrated, fault):
    current, parent, ident, _ = truncated_parent(migrated)
    state_faults = {"failed", "forced_report", "new_cap", "bool_cap", "iteration", "usage_unknown",
        "token_gap", "pending", "inflight", "specialist_seal", "aggregate_cost", "aggregate_in",
        "aggregate_out", "aggregate_cache", "aggregate_missing"}
    if fault in state_faults or fault in {"second_truncated", "consultation", "r2", "source_contract",
            "primary_id", "primary_phase", "already_complete"}:
        def mutate(progress):
            cp = progress["checkpoint"]
            state = cp["specialist_checkpoints"]["quant:R1"]
            if fault == "failed": state["status"] = "failed"
            elif fault == "forced_report": state["forced_report"] = False
            elif fault == "new_cap": state["max_tokens"] = 128000
            elif fault == "bool_cap": state["max_tokens"] = True
            elif fault == "iteration": state["iteration"] = 0
            elif fault == "usage_unknown": state["usage_unknown"] = True
            elif fault == "token_gap": state["usage"]["tokens_missing"] = [ident]
            elif fault == "aggregate_cost": state["usage"]["cost_usd"] = 0
            elif fault == "aggregate_in": state["usage"]["in"] += 1
            elif fault == "aggregate_out": state["usage"]["out"] += 1
            elif fault == "aggregate_cache": state["usage"]["cache_read"] += 1
            elif fault == "aggregate_missing": state["usage"].pop("cache_write")
            elif fault == "pending": state["pending_tools"] = {"pending": {"tool": "read_frozen"}}
            elif fault == "inflight": state["inflight_tools"] = {"pending": {"name": "read_frozen"}}
            elif fault == "second_truncated": cp["specialist_checkpoints"]["macro:R1"] = deepcopy(state)
            elif fault in ("consultation", "r2"):
                cp["specialist_checkpoints"]["quant:R1:consultation" if fault == "consultation" else "quant:R2"] = cp["specialist_checkpoints"].pop("quant:R1")
            elif fault == "source_contract": cp["contract"]["source_fingerprint"] = "different"
            elif fault == "primary_id": progress["primary_failure"]["request_id"] = "different"
            elif fault == "primary_phase": progress["primary_failure"]["phase"] = "building"
            elif fault == "already_complete": cp["data"]["_completed_stages"] = {"quant:1": {"status": "complete"}}
            if fault == "specialist_seal": state["sha256"] = "0" * 64
            else: _seal_specialist(state)
        _mutate_progress(migrated, parent, mutate)
    else:
        with sqlite3.connect(migrated) as conn:
            if fault == "book": conn.execute("UPDATE cash_state SET version=version+1")
            elif fault == "feedback": conn.execute("INSERT INTO decisions(timestamp,ticker,action,status,pm_feedback) VALUES('now','TEST','RESEARCH','PENDING','New PM decision')")
            elif fault == "unknown": conn.execute("UPDATE trade_idea_costs SET status='unknown' WHERE request_id=?", (ident,))
            elif fault == "foreign_request": conn.execute("UPDATE trade_idea_costs SET role='specialist:macro' WHERE request_id=?", (ident,))
            else:
                receipt = json.loads(conn.execute("SELECT receipt_json FROM trade_idea_costs WHERE request_id=?", (ident,)).fetchone()[0])
                if fault == "receipt_seal": receipt["response_sha256"] = "0" * 64
                elif fault == "receipt_identity": receipt["response"]["request_id"] = "different"
                elif fault == "receipt_model": receipt["response"]["model"] = "different/model"
                elif fault == "receipt_cost": receipt["response"]["usage"]["cost_usd"] = "0"
                elif fault == "request_digest": receipt["request_sha256"] = "0" * 64
                elif fault == "terminal_stop": receipt["response"]["stop_reason"] = "end_turn"
                elif fault == "terminal_tool": receipt["response"]["content"] = [{"type": "tool_use", "name": "unresolved"}]
                elif fault == "terminal_tokens": receipt["response"]["usage"]["output_tokens"] = True
                if fault != "receipt_seal": receipt["response_sha256"] = _digest(receipt["response"])
                conn.execute("UPDATE trade_idea_costs SET receipt_json=? WHERE request_id=?", (json.dumps(receipt), ident))
    before, costs = current.get_run(parent), _rows(migrated, "trade_idea_costs", parent)
    if fault not in ("book", "feedback"):
        assert before["recovery"]["can_complete_truncated_response"] is False
    with pytest.raises((RunConflict, BudgetBlocked, ValueError)):
        current.create_continuation(parent, idempotency_key="must-not-create", authorize_new_requests=True,
            recover_truncated_request_id=ident)
    assert current.list_runs()["total"] == 1
    assert current.get_run(parent) == before
    assert _rows(migrated, "trade_idea_costs", parent) == costs


@pytest.mark.parametrize("derived", [False, True])
def test_second_continuation_preserves_the_same_explicit_recovery_without_new_cost(migrated, derived):
    current, parent, ident, original = truncated_parent(migrated)
    child = current.create_continuation(parent, idempotency_key="first", authorize_new_requests=True,
        recover_truncated_request_id=ident)
    descriptor = child["run"]["continuation"]["specialist_response_recovery"]
    child_id = child["run"]["id"]
    token = current.claim_run(child_id)
    if derived:
        cp = deepcopy(original)
        state = cp["specialist_checkpoints"]["quant:R1"]
        cp["data"]["_specialist_response_recovery_history"] = {ident: deepcopy(state)}
        cp["data"].pop("_primary_failure")
        state.update(status="ready", max_tokens=128000, response_recovery=descriptor)
        _seal_specialist(state)
        current.update_progress(child_id, token, "checkpoint", {"checkpoint": cp, "checkpoint_sha256": _digest(cp)})
    current.interrupt_run(child_id, reason="Frozen crash before a new request")
    grandchild = current.create_continuation(child_id, idempotency_key="second", authorize_new_requests=True)
    assert grandchild["run"]["continuation"]["specialist_response_recovery"] == descriptor
    assert grandchild["cost"]["charged_usd"] == "0.03" and grandchild["cost"]["requests"] == 2
    assert current.get_run(parent)["progress"]["checkpoint"] == original
    assert _rows(migrated, "trade_idea_costs", child_id) == []
    assert _rows(migrated, "trade_idea_costs", grandchild["run"]["id"]) == []


def test_report_completion_keeps_the_original_aggregate_budget_and_known_overrun(migrated):
    current, parent, ident, _ = truncated_parent(migrated, terminal_cost="5.01")
    before = current.get_run(parent)
    assert before["cost"]["overrun"] is True and before["cost"]["charged_usd"] == "5.02"
    assert before["recovery"]["can_complete_truncated_response"] is False
    with pytest.raises(BudgetBlocked, match="original aggregate budget"):
        current.create_continuation(parent, idempotency_key="overrun", authorize_new_requests=True,
            recover_truncated_request_id=ident)
    assert current.get_run(parent) == before


def test_usage_attestation_includes_current_child_receipts_and_is_read_only(migrated, monkeypatch):
    current, parent, ident, checkpoint = truncated_parent(migrated)
    child = current.create_continuation(parent, idempotency_key="usage-child", authorize_new_requests=True,
        recover_truncated_request_id=ident)["run"]["id"]
    token = current.claim_run(child)
    new_id, model = "new-final-report", request()["models"]["specialist"]["model"]
    measured = {"cost_usd": "0.003", "input_tokens": 100, "output_tokens": 50,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    response = {"id": "provider-new-report", "request_id": new_id, "model": model,
        "usage": measured, "stop_reason": "end_turn", "content": [{"type": "text", "text": "Frozen report"}]}
    current.reserve_cost(child, new_id, "specialist:quant", model, "0.1",
        request_sha256=_digest({"frozen_completion": True}), worker_token=token)
    current.reconcile_cost(child, new_id, charged_usd="0.003", usage=measured,
        receipt={"response_id": response["id"], "model": model, "response": response,
            "response_sha256": _digest(response)})
    usage = deepcopy(checkpoint["specialist_checkpoints"]["quant:R1"]["usage"])
    usage["request_ids"].append(new_id)
    usage["in"] += 100
    usage["out"] += 50
    usage["cost_usd"] += 0.003
    before = {run_id: _rows(migrated, "trade_idea_costs", run_id) for run_id in (parent, child)}
    original_connect, connections = current._connect, []

    def connect(*, read_only=False):
        connections.append(read_only)
        assert read_only is True
        return original_connect(read_only=read_only)

    monkeypatch.setattr(current, "_connect", connect)
    assert current.validate_specialist_usage(child, "quant", usage) is True
    for field in ("cost_usd", "in", "out", "cache_read", "cache_write"):
        tampered = deepcopy(usage)
        tampered[field] += 1
        with pytest.raises(RunConflict, match="aggregate usage"):
            current.validate_specialist_usage(child, "quant", tampered)
    assert connections == [True] * 6
    assert {run_id: _rows(migrated, "trade_idea_costs", run_id) for run_id in (parent, child)} == before
