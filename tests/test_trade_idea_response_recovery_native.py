"""Explicit report completion keeps paid native tool history and receipts intact.

Run only through the pre-import offline harness. The Specialist loop, budget
gate, request receipts, checkpoint binding and temporary SQLite store are real;
the provider and tool evidence are frozen synthetic fixtures.
"""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.agents.specialists import base
from bellomberg.core import llm_client
from test_run_robustness import bb, _FakeClient, _MockSpecialist
from test_trade_idea_pipeline import db_path, migrated, store, _priced_request, _gate


REPORT = "Conclusione sintetica basata sulle nove verifiche congelate; lacune dichiarate n.d. " * 35


class Crash(BaseException):
    pass


class FrozenProvider(_FakeClient):
    def __init__(self, *, original=False, truncated=False, completion_tool=False):
        self.original, self.truncated, self.completion_tool = original, truncated, completion_tool
        super().__init__(self.reply)

    def create(self, **kwargs):
        # The native loop mutates its conversation; retain the actual request
        # at dispatch, not an alias of the later message list.
        self.calls.append(deepcopy(kwargs))
        return self.reply(len(self.calls), kwargs)

    def reply(self, number, body):
        tool = self.original and number < 10 or self.completion_tool
        stop = "tool_use" if tool else "max_tokens" if self.original or self.truncated else "end_turn"
        content = ([llm_client.ToolUseBlock("frozen-tool-" + str(number), "read_blackboard", {})]
                   if tool else [] if stop == "max_tokens" else [llm_client.TextBlock(REPORT)])
        return llm_client.Messaggio(
            id=("old" if self.original else "new") + "-receipt-" + str(number),
            model=body["model"], content=content, stop_reason=stop,
            usage=llm_client.Usage(input_tokens=100, output_tokens=body["max_tokens"] if stop == "max_tokens" else 50,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
                reasoning_tokens=body["max_tokens"] if stop == "max_tokens" else 20,
                cost_usd=.003))


def rows(path, run_id):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT * FROM trade_idea_costs WHERE run_id=? ORDER BY created_at, request_id", (run_id,))]


def bind_board(template, current, run_id, payload):
    detail = current.get_run(run_id)
    token = current.claim_run(run_id)
    board = base.Blackboard(heartbeat_path=template.HEARTBEAT_PATH)
    board.run_scope, board.run_id, board.target_ticker = "trade_idea", run_id, "TEST"
    board.model_phase, board.model_roots = "research", []
    board.r2_specialists = set(trade.TRADE_IDEA_DESKS) - {"fundamentals"}
    board.source_qualification = deepcopy(payload["source_qualification"])
    board.budget_gate = _gate(current, run_id, payload)
    trade._configure_native_recovery(board, current, token,
        inherited=detail["progress"] if detail["run"].get("continuation") else None)
    return board, token


def actor(board, provider, monkeypatch, *, cap=128000, tools=None):
    instance = _MockSpecialist(board, client=provider)
    monkeypatch.setattr(instance, "_max_tokens_for_round", lambda *a: cap)
    monkeypatch.setattr(instance, "_build_round_context", lambda *a: "Frozen exact company source, same acceptance and PM context.")
    monkeypatch.setattr(instance, "_build_tools_schema", lambda: [{"name": "read_blackboard",
        "description": "Read the frozen native blackboard", "input_schema": {"type": "object", "properties": {}}}])
    execute = instance._execute_meta_tool
    def execute_once(*args):
        if tools is not None:
            tools.append(deepcopy(args))
        return execute(*args)
    monkeypatch.setattr(instance, "_execute_meta_tool", execute_once)
    return instance


def stopped_parent(path, template, monkeypatch, *, round_n=1):
    current, payload = store(path), _priced_request(budget="5")
    parent = current.create_run(payload, idempotency_key="native-truncated-parent")["run"]["id"]
    board, token = bind_board(template, current, parent, payload)
    provider, tools = FrozenProvider(original=True), []
    old_cap = 16000 if round_n == 0 else 65536
    specialist = actor(board, provider, monkeypatch, cap=old_cap, tools=tools)
    report = specialist.run(round_n)
    assert specialist.run_result_status == "truncated" and "No output produced" in report
    assert len(provider.calls) == 10 and len(tools) == len(board.tool_log) == 9
    assert provider.calls[-1]["tool_choice"] == {"type": "none"}
    assert all(call["max_tokens"] == old_cap for call in provider.calls)
    saved = deepcopy(board.specialist_checkpoints["quant:R" + str(round_n)])
    assert saved["status"] == "truncated" and saved["forced_report"] is True
    assert saved["iteration"] == 10 and not saved["pending_tools"] and not saved["inflight_tools"]
    costs = rows(path, parent)
    assert len(costs) == 10 and all(row["status"] == "charged" for row in costs)
    terminal = next(row for row in costs if json.loads(row["receipt_json"])["response_id"] == "old-receipt-10")
    assert current.get_run(parent)["progress"]["primary_failure"]["request_id"] == terminal["request_id"]
    current.finish_run(parent, token, None, "incomplete", reason="Known forced-report output limit")
    return current, payload, parent, board, saved, terminal, costs, provider, tools


def child_of(current, parent, request_id, *, key="native-report-recovery"):
    return current.create_continuation(parent, idempotency_key=key,
        authorize_new_requests=True, recover_truncated_request_id=request_id)["run"]["id"]


def assert_report_only(old_call, new_call):
    assert new_call["max_tokens"] == 128000
    assert new_call["model"] == old_call["model"]
    assert new_call["thinking"] == old_call["thinking"] == {"type": "effort", "effort": "max"}
    assert new_call["tool_choice"] == {"type": "none"}
    assert new_call["system"] == old_call["system"]
    old_body, new_body = base._checkpoint_json(old_call), base._checkpoint_json(new_call)
    old_messages, new_messages = old_body.pop("messages"), new_body.pop("messages")
    old_body.pop("max_tokens")
    new_body.pop("max_tokens")
    assert new_body == old_body  # Every other request field is unchanged.
    assert len(new_messages) == len(old_messages) and new_messages[:-1] == old_messages[:-1]
    assert new_messages[-1]["role"] == old_messages[-1]["role"] == "user"
    old_tail, new_tail = old_messages[-1]["content"], new_messages[-1]["content"]
    assert new_tail[:len(old_tail)] == old_tail
    additions = new_tail[len(old_tail):]
    assert sum(block.get("type") == "text" and block.get("text", "").startswith(
        "[AUTHORIZED REPORT COMPLETION]") for block in additions) == 1
    assert all(block.get("type") == "text" and (block.get("text", "").startswith(
        "[AUTHORIZED REPORT COMPLETION]") or block == old_tail[-1]) for block in additions)
    # All collected evidence survives. An explicit completion nudge may follow
    # the original forced-report prompt, but no source result can disappear.
    old_results = [block for message in old_call["messages"] if isinstance(message.get("content"), list)
                   for block in message["content"] if isinstance(block, dict) and block.get("type") == "tool_result"]
    new_results = [block for message in new_call["messages"] if isinstance(message.get("content"), list)
                   for block in message["content"] if isinstance(block, dict) and block.get("type") == "tool_result"]
    assert len(old_results) == 9 and new_results == old_results


@pytest.mark.parametrize("round_n", [0, 1])
def test_native_report_only_completion_keeps_nine_tools_and_costs_once(migrated, bb, monkeypatch, round_n):
    current, payload, parent, old_board, saved, terminal, costs, old_provider, old_tools = stopped_parent(
        migrated, bb, monkeypatch, round_n=round_n)
    child = child_of(current, parent, terminal["request_id"])
    descriptor = deepcopy(current.get_run(child)["run"]["continuation"]["specialist_response_recovery"])
    board, _ = bind_board(bb, current, child, payload)
    provider, tools = FrozenProvider(), []
    specialist = actor(board, provider, monkeypatch, tools=tools)
    report = specialist.run(round_n)
    assert specialist.run_result_status == "complete" and REPORT in report
    assert len(provider.calls) == 1 and tools == [] and board.tool_log == old_board.tool_log
    assert_report_only(old_provider.calls[-1], provider.calls[0])
    assert rows(migrated, parent) == costs and len(rows(migrated, child)) == 1
    assert Decimal(current.get_run(child)["cost"]["charged_usd"]) == Decimal("0.033")
    assert current.get_run(child)["cost"]["requests"] == 11
    usages = [row for row in board.usage_log if row["agent"] == "quant" and row["round"] == round_n]
    assert len(usages) == 1 and usages[0]["cost_usd"] == pytest.approx(.033)
    assert usages[0]["api_calls"] == 11
    assert len(usages[0]["request_ids"]) == len(set(usages[0]["request_ids"])) == 11
    history = deepcopy(board.data["_specialist_response_recovery_history"])
    assert history == [{"request_id": terminal["request_id"], "descriptor": descriptor, "checkpoint": saved}]
    before = deepcopy(board.specialist_checkpoints)
    assert specialist.run(round_n) == report and len(provider.calls) == 1 and tools == []
    assert board.specialist_checkpoints == before and rows(migrated, parent) == costs
    assert board.data["_specialist_response_recovery_history"] == history
    assert current.get_run(parent)["progress"]["checkpoint"]["specialist_checkpoints"]["quant:R" + str(round_n)] == saved


def test_native_report_recovery_crash_after_charge_reuses_receipt_without_dispatch(migrated, bb, monkeypatch):
    current, payload, parent, _, saved, terminal, costs, old_provider, _ = stopped_parent(migrated, bb, monkeypatch)
    child = child_of(current, parent, terminal["request_id"])
    descriptor = deepcopy(current.get_run(child)["run"]["continuation"]["specialist_response_recovery"])
    board, _ = bind_board(bb, current, child, payload)
    provider = FrozenProvider()
    reconcile = current.reconcile_cost
    def paid_then_crash(*args, **kwargs):
        reconcile(*args, **kwargs)
        raise Crash("after durable paid response, before specialist response checkpoint")
    monkeypatch.setattr(current, "reconcile_cost", paid_then_crash)
    with pytest.raises(Crash):
        actor(board, provider, monkeypatch).run(1)
    assert len(provider.calls) == 1 and len(rows(migrated, child)) == 1
    assert_report_only(old_provider.calls[-1], provider.calls[0])
    child_costs = rows(migrated, child)
    monkeypatch.setattr(current, "reconcile_cost", reconcile)
    current.interrupt_run(child, reason="Frozen crash after receipt")
    grandchild = current.create_continuation(child, idempotency_key="native-recovery-after-crash",
        authorize_new_requests=True)["run"]["id"]
    assert current.get_run(grandchild)["run"]["continuation"]["specialist_response_recovery"] == descriptor
    restored, _ = bind_board(bb, current, grandchild, payload)
    no_dispatch = _FakeClient(lambda *a: pytest.fail("already charged report dispatched twice"))
    tools = []
    specialist = actor(restored, no_dispatch, monkeypatch, tools=tools)
    report = specialist.run(1)
    assert REPORT in report and specialist.run_result_status == "complete"
    assert specialist.run(1) == report
    assert not no_dispatch.calls and not tools
    assert rows(migrated, parent) == costs and rows(migrated, child) == child_costs
    assert rows(migrated, grandchild) == []
    assert current.get_run(grandchild)["cost"]["requests"] == 11
    assert Decimal(current.get_run(grandchild)["cost"]["charged_usd"]) == Decimal("0.033")
    usages = [row for row in restored.usage_log if row["agent"] == "quant" and row["round"] == 1]
    assert len(usages) == 1 and usages[0]["cost_usd"] == pytest.approx(.033)
    assert usages[0]["api_calls"] == 11
    assert len(usages[0]["request_ids"]) == len(set(usages[0]["request_ids"])) == 11
    assert restored.data["_specialist_response_recovery_history"] == [
        {"request_id": terminal["request_id"], "descriptor": descriptor, "checkpoint": saved}]


@pytest.mark.parametrize("tamper", ["messages", "contract"])
def test_native_response_recovery_rejects_changed_body_or_contract(migrated, bb, monkeypatch, tamper):
    current, payload, parent, _, _, terminal, costs, _, _ = stopped_parent(migrated, bb, monkeypatch)
    progress = deepcopy(current.get_run(parent)["progress"])
    state = progress["checkpoint"]["specialist_checkpoints"]["quant:R1"]
    if tamper == "messages":
        state["messages"][0]["content"] = "Different unapproved evidence replacing the paid context"
    else:
        state["contract"] = "0" * 64
    state.pop("sha256")
    state["sha256"] = base._checkpoint_digest(state)
    progress["checkpoint_sha256"] = trade._plan_digest(progress["checkpoint"])
    # Simulate internally consistent local checkpoint corruption. Server-side
    # storage attestation cannot infer the native wire body, so the native
    # request/contract proof must reject this even after both seals agree.
    with sqlite3.connect(migrated) as db:
        db.execute("UPDATE trade_idea_runs SET progress_json=? WHERE id=?",
                   (json.dumps(progress), parent))
    child = child_of(current, parent, terminal["request_id"])
    board, _ = bind_board(bb, current, child, payload)
    provider = _FakeClient(lambda *a: pytest.fail("tampered recovery sent to provider"))
    with pytest.raises((ValueError, RuntimeError)):
        actor(board, provider, monkeypatch).run(1)
    assert not provider.calls and rows(migrated, child) == [] and rows(migrated, parent) == costs


def test_native_new_128k_truncation_never_starts_an_automatic_second_attempt(migrated, bb, monkeypatch):
    current, payload, parent, _, _, terminal, costs, _, _ = stopped_parent(migrated, bb, monkeypatch)
    child = child_of(current, parent, terminal["request_id"])
    board, token = bind_board(bb, current, child, payload)
    provider = FrozenProvider(truncated=True)
    specialist = actor(board, provider, monkeypatch)
    specialist.run(1)
    assert specialist.run_result_status == "truncated" and len(provider.calls) == 1
    for _ in range(2):
        with pytest.raises((ValueError, RuntimeError)):
            specialist.run(1)
    assert len(provider.calls) == 1 and len(rows(migrated, child)) == 1 and rows(migrated, parent) == costs
    current.finish_run(child, token, None, "incomplete", reason="Explicit report completion also truncated at 128k")
    newest = rows(migrated, child)[0]["request_id"]
    with pytest.raises((ValueError, RuntimeError)):
        child_of(current, child, newest, key="must-not-authorize-another-128k-attempt")


def test_native_report_completion_refuses_unexpected_provider_tool_without_dispatch(migrated, bb, monkeypatch):
    current, payload, parent, old_board, _, terminal, costs, old_provider, _ = stopped_parent(migrated, bb, monkeypatch)
    child = child_of(current, parent, terminal["request_id"])
    board, _ = bind_board(bb, current, child, payload)
    provider, tools = FrozenProvider(completion_tool=True), []
    specialist = actor(board, provider, monkeypatch, tools=tools)
    report = specialist.run(1)
    assert specialist.run_result_status == "failed"
    assert "no tool dispatched" in report and tools == [] and board.tool_log == old_board.tool_log
    assert len(provider.calls) == 1 and len(rows(migrated, child)) == 1 and rows(migrated, parent) == costs
    assert_report_only(old_provider.calls[-1], provider.calls[0])
    with pytest.raises((ValueError, RuntimeError)):
        specialist.run(1)
    assert len(provider.calls) == 1 and tools == []


def test_native_recovery_rejects_aggregate_cost_that_disagrees_with_paid_receipts(migrated, bb, monkeypatch):
    current, payload, parent, _, _, terminal, costs, _, _ = stopped_parent(migrated, bb, monkeypatch)
    progress = deepcopy(current.get_run(parent)["progress"])
    state = progress["checkpoint"]["specialist_checkpoints"]["quant:R1"]
    state["usage"]["cost_usd"] = 0
    state.pop("sha256")
    state["sha256"] = base._checkpoint_digest(state)
    progress["checkpoint_sha256"] = trade._plan_digest(progress["checkpoint"])
    with sqlite3.connect(migrated) as db:
        db.execute("UPDATE trade_idea_runs SET progress_json=? WHERE id=?", (json.dumps(progress), parent))
    provider = FrozenProvider()
    with pytest.raises((ValueError, RuntimeError), match="cost|usage|accounting|receipt"):
        child = child_of(current, parent, terminal["request_id"])
        board, _ = bind_board(bb, current, child, payload)
        actor(board, provider, monkeypatch).run(1)
    assert provider.calls == [] and rows(migrated, parent) == costs


@pytest.mark.parametrize("tamper", ["messages", "usage"])
def test_native_second_resume_rejects_changed_derived_report_state(migrated, bb, monkeypatch, tamper):
    current, payload, parent, _, _, terminal, costs, _, _ = stopped_parent(migrated, bb, monkeypatch)
    child = child_of(current, parent, terminal["request_id"])
    board, _ = bind_board(bb, current, child, payload)
    persist = board.persist_run_checkpoint
    def derived_then_crash(event, checkpoint_payload):
        persist(event, checkpoint_payload)
        if event == "specialist_ready":
            raise Crash("after the derived checkpoint, before its first provider dispatch")
    monkeypatch.setattr(board, "persist_run_checkpoint", derived_then_crash)
    no_dispatch = _FakeClient(lambda *a: pytest.fail("crash fixture dispatched to provider"))
    with pytest.raises(Crash):
        actor(board, no_dispatch, monkeypatch).run(1)
    assert no_dispatch.calls == [] and rows(migrated, child) == []
    current.interrupt_run(child, reason="Frozen crash before the authorized report")
    progress = deepcopy(current.get_run(child)["progress"])
    state = progress["checkpoint"]["specialist_checkpoints"]["quant:R1"]
    assert state["status"] == "ready" and state["response_recovery"]
    if tamper == "messages":
        state["messages"][0]["content"] = "Unapproved replacement for the paid source evidence"
    else:
        state["usage"]["cost_usd"] = 0
    state.pop("sha256")
    state["sha256"] = base._checkpoint_digest(state)
    progress["checkpoint_sha256"] = trade._plan_digest(progress["checkpoint"])
    with sqlite3.connect(migrated) as db:
        db.execute("UPDATE trade_idea_runs SET progress_json=? WHERE id=?", (json.dumps(progress), child))
    provider = FrozenProvider()
    with pytest.raises((ValueError, RuntimeError)):
        grandchild = current.create_continuation(child, idempotency_key="tampered-derived-report",
            authorize_new_requests=True)["run"]["id"]
        restored, _ = bind_board(bb, current, grandchild, payload)
        actor(restored, provider, monkeypatch).run(1)
    assert provider.calls == [] and rows(migrated, child) == [] and rows(migrated, parent) == costs


def test_native_completed_report_rejects_changed_aggregate_before_returning_cached_result(migrated, bb, monkeypatch):
    current, payload, parent, _, _, terminal, costs, _, _ = stopped_parent(migrated, bb, monkeypatch)
    child = child_of(current, parent, terminal["request_id"])
    board, _ = bind_board(bb, current, child, payload)
    provider = FrozenProvider()
    specialist = actor(board, provider, monkeypatch)
    assert REPORT in specialist.run(1) and specialist.run_result_status == "complete"
    assert len(provider.calls) == 1
    child_costs = rows(migrated, child)
    current.interrupt_run(child, reason="Frozen interruption after the completed report")
    progress = deepcopy(current.get_run(child)["progress"])
    state = progress["checkpoint"]["specialist_checkpoints"]["quant:R1"]
    assert state["status"] == "complete" and len(state["usage"]["request_ids"]) == 11
    state["usage"]["cost_usd"] = 0
    state.pop("sha256")
    state["sha256"] = base._checkpoint_digest(state)
    progress["checkpoint_sha256"] = trade._plan_digest(progress["checkpoint"])
    with sqlite3.connect(migrated) as db:
        db.execute("UPDATE trade_idea_runs SET progress_json=? WHERE id=?", (json.dumps(progress), child))
    no_dispatch = _FakeClient(lambda *a: pytest.fail("cached completed report dispatched again"))
    with pytest.raises((ValueError, RuntimeError), match="cost|usage|accounting|receipt"):
        grandchild = current.create_continuation(child, idempotency_key="tampered-completed-report",
            authorize_new_requests=True)["run"]["id"]
        restored, _ = bind_board(bb, current, grandchild, payload)
        actor(restored, no_dispatch, monkeypatch).run(1)
    assert no_dispatch.calls == []
    assert rows(migrated, parent) == costs and rows(migrated, child) == child_costs
