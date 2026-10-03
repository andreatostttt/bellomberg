"""Native model-only continuation of a paid, complete Fundamentals report.

Run through tools/testing/offline_pytest.py. Providers are frozen; the specialist
loops, consultations, ledger, checkpoints and workbook compiler are real.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import json

import httpx
import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.agents.specialists import base
from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist
from bellomberg.core import llm_client
from test_run_robustness import bb
from test_trade_idea_pipeline import db_path, migrated, store, _priced_request, _gate
from test_trade_idea_response_recovery_native import rows
from test_trade_idea_economic import qualified, _operating_plan


REPORT = "Frozen original research; five peer answers received. Common workbook still absent. " * 20
PEERS = set(trade.TRADE_IDEA_DESKS) - {"fundamentals"}


class Crash(BaseException):
    pass


def blocks(body, kind):
    return [block for message in body["messages"]
            if isinstance(message.get("content"), list) for block in message["content"]
            if isinstance(block, dict) and block.get("type") == kind]


class FrozenProvider:
    """One measured provider boundary for real Fundamentals and peer classes."""
    def __init__(self):
        self.messages = self
        self.calls = []
        self.mode = "origin"
        self.board = None
        self.origin_turns = 0
        self.new_calls = []
        self.timeout = 3600
        self._http = SimpleNamespace(timeout=httpx.Timeout(3600, connect=30))

    def create(self, **kwargs):
        body = base._checkpoint_json(kwargs)
        self.calls.append(body)
        desk = next(name for name in trade.TRADE_IDEA_DESKS
                    if "You are the " + name + " desk" in str(body["system"]))
        if desk != "fundamentals":
            assert self.mode == "origin", "A paid peer consultation was repeated"
            content = [llm_client.TextBlock(("Frozen actual " + desk + " answer to the author's driver question. ") * 45)]
            stop = "end_turn"
        elif self.mode == "origin":
            self.origin_turns += 1
            if self.origin_turns == 1:
                content = [llm_client.ToolUseBlock("ask-" + peer, "ask_specialist", {
                    "specialist": peer, "question": "Which cash-flow mechanism invalidates this explicit draft?",
                    "draft_assumptions": {"wacc": .10}, "evidence_refs": []}) for peer in sorted(PEERS)]
                stop = "tool_use"
            elif self.origin_turns < 30:
                content = [llm_client.ToolUseBlock("read-" + str(self.origin_turns), "read_blackboard", {})]
                stop = "tool_use"
            else:
                assert self.origin_turns == 30 and body["tool_choice"] == {"type": "none"}
                content, stop = [llm_client.TextBlock(REPORT)], "end_turn"
        else:
            self.new_calls.append(body)
            assert desk == "fundamentals"
            names = {block["name"] for block in blocks(body, "tool_use")}
            if self.mode == "prose_only":
                content, stop = [llm_client.TextBlock("More preserved prose, explicitly no workbook. " * 100)], "end_turn"
            elif self.mode == "truncated":
                content, stop = [llm_client.TextBlock("Incomplete author plan, not a verified workbook.")], "max_tokens"
            elif "submit_candidate_model_plan" not in names:
                decisions = [{"consultation_id": row["id"], "decision": "incorporated",
                    "rationale": "Explicit author adoption of this preserved peer answer."}
                    for row in self.board.data["_model_consultations"]]
                content = [llm_client.ToolUseBlock("complete-plan", "submit_candidate_model_plan", {
                    "plan": _operating_plan(), "consultation_decisions": decisions,
                    "rationale": "Author the missing native driver groups from the frozen verified documents."})]
                stop = "tool_use"
            elif "get_valuation" not in names:
                content = [llm_client.ToolUseBlock("compile-plan", "get_valuation", {"ticker": "SYNTH-EXT"})]
                stop = "tool_use"
            else:
                content, stop = [llm_client.TextBlock("The exact authored workbook is now compiled; prior research retained.")], "end_turn"
        return llm_client.Messaggio(id="frozen-author-receipt-" + str(len(self.calls)), model=body["model"],
            content=content, stop_reason=stop, usage=llm_client.Usage(input_tokens=100, output_tokens=50,
                cache_read_input_tokens=0, cache_creation_input_tokens=0, reasoning_tokens=20, cost_usd=.003))


def bind(case, run_id):
    current, payload = case.store, case.payload
    detail, token = current.get_run(run_id), current.claim_run(run_id)
    board = base.Blackboard(heartbeat_path=case.heartbeat)
    board.run_scope, board.run_id, board.target_ticker = "trade_idea", run_id, "SYNTH-EXT"
    board.model_phase, board.current_round, board.independent_round = "research", 1, 0
    board.model_roots = [str(case.directory)]
    board.model_registry = trade._model_registry(case.path)
    board.r2_specialists = set(trade.TRADE_IDEA_DESKS)
    board.source_qualification = deepcopy(payload["source_qualification"])
    board.budget_gate = _gate(current, run_id, payload)
    trade._configure_native_recovery(board, current, token,
        inherited=detail["progress"] if detail["run"].get("continuation") else None)
    board.data.setdefault("_model_input_basis", trade._qualified_model_input_basis(board.source_qualification))
    board.model_checkpoint_writer = lambda _progress: board.persist_run_checkpoint("model_build")
    board.consult_specialist = lambda requester, target, question, draft_assumptions, evidence_refs, **kw: trade._consult_model_desk(
        board, requester, target, question, draft_assumptions, evidence_refs, **kw)
    board.build_candidate_model = lambda inputs: trade._build_fundamentals_candidate(board, inputs, case.directory)
    case.provider.board = board
    return board, token


@pytest.fixture
def native_case(migrated, bb, monkeypatch, tmp_path):
    qualification = qualified(tmp_path / "evidence")
    assert qualification["status"] == "qualified", qualification.get("reasons")
    payload = _priced_request(budget="10")
    payload.update(ticker="SYNTH-EXT", company_name="Synthetic issuer", exchange="TEST", currency="EUR",
                   source_qualification=qualification)
    payload["authorization"]["source_fingerprint"] = qualification["fingerprint"]
    provider = FrozenProvider()
    monkeypatch.setattr(base, "OpenRouterClient", lambda **_kwargs: provider)
    monkeypatch.setattr(FundamentalsSpecialist, "_build_round_context", lambda *_args:
        "Frozen Trade Idea candidate SYNTH-EXT. Round 1. Existing sources and peer reports only.")
    from bellomberg.agents import chat_tools
    definition = {"name": "get_valuation", "description": "Compile the explicitly authored candidate plan",
                  "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}}}
    monkeypatch.setattr(chat_tools, "get_tools_for_agent", lambda _name: [deepcopy(definition)])
    monkeypatch.setattr(chat_tools, "TETTO_TOOL_RESULT", 12000, raising=False)
    monkeypatch.setattr(chat_tools, "dispatch", lambda *_a, **_k: pytest.fail("External research dispatch is forbidden"))
    case = SimpleNamespace(store=store(migrated), payload=payload, provider=provider,
        path=migrated, directory=tmp_path / "models", heartbeat=bb.HEARTBEAT_PATH)
    case.parent = case.store.create_run(payload, idempotency_key="native-author-origin")["run"]["id"]
    board, token = bind(case, case.parent)
    board.model_phase = "building"
    actor = FundamentalsSpecialist(board)
    report = actor.run(1)
    assert actor.run_result_status == "complete"
    board.record_specialist_completion("fundamentals", 1, report, actor.run_result_status)
    assert provider.origin_turns == 30 and len(provider.calls) == 35
    assert {row["desk"] for row in board.data["_model_consultations"]} == PEERS
    assert all(trade._consultation_received(board, row) for row in board.data["_model_consultations"])
    assert not board.data.get("_model_input_draft") and not board.valuation_results
    case.old_checkpoint = deepcopy(board.specialist_checkpoints["fundamentals:R1"])
    case.old_report, case.old_consultations = report, deepcopy(board.data["_model_consultations"])
    case.old_usage, case.old_costs = deepcopy(board.usage_log), rows(migrated, case.parent)
    assert case.old_checkpoint["iteration"] == 30 and case.old_checkpoint["forced_report"] is True
    case.store.finish_run(case.parent, token, None, "incomplete", reason="Model authoring was not completed")
    case.child = case.store.create_continuation(case.parent, idempotency_key="native-author-child",
                                              authorize_new_requests=True)["run"]["id"]
    case.board, case.token = bind(case, case.child)
    provider.mode = "completion"
    return case


def complete(case):
    assert hasattr(trade, "_complete_saved_model_authoring"), "Native model-only completion entry point is missing"
    return trade._complete_saved_model_authoring(case.board)


def assert_original_preserved(case):
    assert case.board.specialist_checkpoints["fundamentals:R1"] == case.old_checkpoint
    assert case.board.read("fundamentals", 1) == case.old_report
    assert rows(case.path, case.parent) == case.old_costs
    assert case.store.get_run(case.parent)["progress"]["checkpoint"]["specialist_checkpoints"][
        "fundamentals:R1"] == case.old_checkpoint
    prior = {row["id"]: row for row in case.old_consultations}
    for row in case.board.data["_model_consultations"]:
        assert {key: value for key, value in row.items() if key != "fundamentals_decision"} == prior[row["id"]]


def test_native_authoring_completes_real_workbook_and_preserves_paid_research(native_case):
    case = native_case
    complete(case)
    refs = trade._verified_candidate_valuations(case.board)
    assert len(refs) == 1
    workbook = case.board.valuation_results["SYNTH-EXT"]
    assert Path(workbook["path"]).is_file()
    assert workbook["preparation"]["proposal"]["plan"] == _operating_plan()
    assert all(row.get("fundamentals_decision") for row in case.board.data["_model_consultations"])
    assert len(case.provider.new_calls) == 3 and len(rows(case.path, case.child)) == 3
    assert Decimal(case.store.get_run(case.child)["cost"]["charged_usd"]) == Decimal("0.114")
    assert_original_preserved(case)
    new_usages = case.board.usage_log[len(case.old_usage):]
    assert len(new_usages) == 1 and new_usages[0]["api_calls"] == 3
    assert new_usages[0]["cost_usd"] == pytest.approx(.009)
    assert set(new_usages[0]["request_ids"]).isdisjoint(case.old_checkpoint["usage"]["request_ids"])
    assert all(call["max_tokens"] == 128000 for call in case.provider.new_calls)
    author_keys = [key for key in case.board.specialist_checkpoints if key.startswith("fundamentals:R1:")]
    assert len(author_keys) == 1
    assert case.board.specialist_checkpoints[author_keys[0]]["author_progress_version"] == 1
    assert "RECORDED MODEL WORK / MISSING AUTHOR ACTIONS:" in json.dumps(
        case.provider.new_calls[0]["messages"][-1]["content"])
    transcript = case.provider.new_calls[0]["messages"]
    raw_first = case.board.specialist_checkpoints[author_keys[0]]["messages"][:len(transcript)]
    assert raw_first[:len(case.old_checkpoint["messages"])] == case.old_checkpoint["messages"]
    from bellomberg.agents.model_authoring_context import project_model_authoring_messages, PROGRESS_MARKER
    projected, projection = project_model_authoring_messages(raw_first)
    assert transcript == projected
    assert case.board.data["_model_authoring_context_projections"][0]["receipt"] == projection
    # Independent content checks: only old informational ledgers may differ;
    # every assistant/report, tool argument, tool result and original context
    # preceding the first ledger stays byte-for-byte equal in the request view.
    for original, delivered in zip(raw_first, transcript, strict=True):
        assert original["role"] == delivered["role"]
        if original["role"] == "assistant":
            assert delivered == original
        elif isinstance(original["content"], str):
            assert delivered["content"].startswith(original["content"].split(PROGRESS_MARKER)[0])
        else:
            for old_block, new_block in zip(original["content"], delivered["content"], strict=True):
                if old_block.get("type") != "text" or PROGRESS_MARKER not in old_block.get("text", ""):
                    assert new_block == old_block
    serialized = json.dumps(transcript)
    assert serialized.count(REPORT) == 1
    assert serialized.count("Frozen Trade Idea candidate SYNTH-EXT. Round 1.") == 1
    assert all({tool["name"] for tool in call["tools"]} <= base.MODEL_AUTHORING_LOCAL_TOOLS
               for call in case.provider.new_calls)
    before = deepcopy((case.board.specialist_checkpoints, case.board.valuation_generations,
                       case.board.usage_log, case.board.data["_model_authoring_completion"]))
    complete(case)
    assert len(case.provider.new_calls) == 3
    assert (case.board.specialist_checkpoints, case.board.valuation_generations,
            case.board.usage_log, case.board.data["_model_authoring_completion"]) == before


@pytest.mark.parametrize("boundary", ["paid_response", "persisted_plan_tool"])
def test_native_authoring_crash_recovers_same_task_without_repaying_or_resubmitting(
        native_case, monkeypatch, boundary):
    case = native_case
    reconcile, persist = case.store.reconcile_cost, case.board.persist_run_checkpoint
    if boundary == "paid_response":
        def crash(*args, **kwargs):
            reconcile(*args, **kwargs)
            raise Crash("after durable receipt, before response checkpoint")
        monkeypatch.setattr(case.store, "reconcile_cost", crash)
    else:
        def crash(event="checkpoint", payload=None):
            persist(event, payload)
            if event == "specialist_tool" and case.board.data.get("_model_input_draft"):
                raise Crash("after durable author plan and tool result, before next provider turn")
        case.board.persist_run_checkpoint = crash
    with pytest.raises(Crash):
        complete(case)
    assert len(case.provider.new_calls) == 1
    child_costs = rows(case.path, case.child)
    assert len(child_costs) == 1 and child_costs[0]["status"] == "charged"
    descriptor = deepcopy(case.board.data["_model_authoring_completion"])
    task_keys = [key for key in case.board.specialist_checkpoints if key.startswith("fundamentals:R1:")]
    assert len(task_keys) == 1
    monkeypatch.setattr(case.store, "reconcile_cost", reconcile)
    case.store.interrupt_run(case.child, reason="Frozen native authoring crash")
    grandchild = case.store.create_continuation(case.child, idempotency_key="native-author-grandchild",
        authorize_new_requests=True)["run"]["id"]
    case.board, _ = bind(case, grandchild)
    complete(case)
    assert len(trade._verified_candidate_valuations(case.board)) == 1
    assert case.board.data["_model_authoring_completion"] == descriptor
    assert [key for key in case.board.specialist_checkpoints if key.startswith("fundamentals:R1:")] == task_keys
    assert len(case.provider.new_calls) == 3
    assert rows(case.path, case.child) == child_costs
    assert len(rows(case.path, grandchild)) == 2
    assert Decimal(case.store.get_run(grandchild)["cost"]["charged_usd"]) == Decimal("0.114")
    assert len(case.board.data["_model_draft_history"]) == 1
    assert len(case.board.valuation_generations) == 1
    assert_original_preserved(case)
    prior = deepcopy((case.board.usage_log, case.board.valuation_generations, case.board.specialist_checkpoints))
    complete(case)
    assert len(case.provider.new_calls) == 3
    assert (case.board.usage_log, case.board.valuation_generations, case.board.specialist_checkpoints) == prior


@pytest.mark.parametrize("fault", ["truncated", "pending_tools", "inflight_tools", "unknown", "cost_tamper",
                                    "messages_tamper", "contract_tamper"])
def test_native_authoring_refuses_unsettled_or_unattested_origin_before_dispatch(native_case, fault):
    case = native_case
    state = deepcopy(case.board.specialist_checkpoints["fundamentals:R1"])
    if fault == "truncated":
        state["status"] = "truncated"
    elif fault in ("pending_tools", "inflight_tools"):
        state[fault] = {"unfinished-tool": {"tool": "read_blackboard", "input": {}}}
    elif fault == "unknown":
        state["usage_unknown"] = True
    elif fault == "messages_tamper":
        state["messages"][0]["content"] = "A substituted origin context, absent from the paid request."
    elif fault == "contract_tamper":
        state["contract"] = "0" * 64
    else:
        state["usage"]["cost_usd"] = 0
    state.pop("sha256")
    state["sha256"] = base._checkpoint_digest(state)
    case.board.specialist_checkpoints["fundamentals:R1"] = state
    with pytest.raises((ValueError, RuntimeError)):
        complete(case)
    assert case.provider.new_calls == [] and rows(case.path, case.child) == []
    assert rows(case.path, case.parent) == case.old_costs
    assert not case.board.valuation_results


@pytest.mark.parametrize("mode", ["prose_only", "truncated"])
def test_native_authoring_terminal_without_workbook_cannot_restart_automatically(native_case, mode):
    case = native_case
    case.provider.mode = mode
    try:
        complete(case)
    except (ValueError, RuntimeError):
        pass
    assert len(case.provider.new_calls) == 1
    assert not trade._verified_candidate_valuations(case.board)
    checkpoint = deepcopy(case.board.specialist_checkpoints)
    for _ in range(2):
        with pytest.raises((ValueError, RuntimeError)):
            complete(case)
    assert len(case.provider.new_calls) == 1 and rows(case.path, case.parent) == case.old_costs
    assert len(rows(case.path, case.child)) == 1
    assert case.board.specialist_checkpoints == checkpoint
    assert case.board.read("fundamentals", 1) == case.old_report
    case.store.finish_run(case.child, case.token, None, "incomplete",
                          reason="Native model authoring ended without an attested workbook")
    before = rows(case.path, case.child)
    expected_block = ("model_authoring_review_required" if mode == "prose_only"
                      else "incomplete_response_review_required")
    recovery = case.store.get_run(case.child)["recovery"]
    assert recovery["can_continue"] is False and recovery["blocked_reason"] == expected_block
    assert recovery["successor_run_id"] is None
    with pytest.raises((ValueError, RuntimeError), match=expected_block):
        case.store.create_continuation(case.child, idempotency_key="no-automatic-author-retry",
                                       authorize_new_requests=True)
    assert len(case.provider.new_calls) == 1 and rows(case.path, case.child) == before


def legacy_wire_case(*, described_nested=True):
    nested = {"type": "object"}
    if described_nested:
        nested["properties"] = {"z": {"type": "integer"}, "a": {"type": "integer"}}
    tool = {"name": "read_frozen", "description": "Fictional local evidence reader",
        "input_schema": {"type": "object", "properties": {
            "z": nested, "a": {"type": "array", "items": nested}}}}
    request = {"model": "meta/muse-spark-1.3", "max_tokens": 128000,
        "thinking": {"type": "effort", "effort": "max"}, "system": "Frozen native author system",
        "tools": [tool], "provider_max_price": {"prompt": 1.0, "completion": 2.0, "request": 0.0},
        "messages": [{"role": "user", "content": "Frozen first question"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "old-tool",
                "name": "read_frozen", "input": {"z": {"z": 2, "a": 1}, "a": [{"z": 4, "a": 3}]}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old-tool",
                "content": '{"ok": true, "evidence": "frozen source"}'}]}]}
    # The historical schema remains installed in its original order. Only the
    # conversation went through the old sorted durable checkpoint serializer.
    restored = deepcopy(request)
    restored["messages"] = json.loads(json.dumps(request["messages"], sort_keys=True))
    fingerprint = trade._plan_digest(trade.costruisci_corpo(**request))
    return request, restored, fingerprint


def test_legacy_wire_schema_order_requires_exact_whole_request_hash():
    original, restored, fingerprint = legacy_wire_case()
    assert trade._plan_digest(trade.costruisci_corpo(**restored)) != fingerprint
    before = deepcopy(restored)
    assert trade._model_author_request_matches(restored, fingerprint) is True
    assert restored == before
    assert trade._model_author_request_matches(original, fingerprint) is True
    restored["messages"][-1]["content"][0]["content"] = '{"ok": true, "evidence": "forged"}'
    assert trade._model_author_request_matches(restored, fingerprint) is False


def test_legacy_wire_unknown_nested_order_is_not_silently_attested():
    _original, restored, fingerprint = legacy_wire_case(described_nested=False)
    before = deepcopy(restored)
    assert trade._model_author_request_matches(restored, fingerprint) is False
    assert restored == before


def test_legacy_wire_schema_order_covers_typed_additional_properties_without_relaxing_values():
    original, _restored, _fingerprint = legacy_wire_case()
    properties = original["tools"][0]["input_schema"]["properties"]
    properties["z"] = {"type": "object", "additionalProperties": properties["z"]}
    original["messages"][1]["content"][0]["input"]["z"] = {"one-record": {"z": 2, "a": 1}}
    fingerprint = trade._plan_digest(trade.costruisci_corpo(**original))
    restored = deepcopy(original)
    restored["messages"] = json.loads(json.dumps(original["messages"], sort_keys=True))
    assert trade._plan_digest(trade.costruisci_corpo(**restored)) != fingerprint
    assert trade._model_author_request_matches(restored, fingerprint) is True
    restored["messages"][1]["content"][0]["input"]["z"]["one-record"]["a"] = 999
    assert trade._model_author_request_matches(restored, fingerprint) is False


def test_forward_message_order_is_stable_without_changing_values_or_source():
    original, restored, _fingerprint = legacy_wire_case()
    before = deepcopy(original)
    stable_original = trade._stable_provider_messages(original["messages"])
    stable_restored = trade._stable_provider_messages(restored["messages"])
    assert stable_original == stable_restored
    left = trade.costruisci_corpo(**{**original, "messages": stable_original})
    right = trade.costruisci_corpo(**{**original, "messages": stable_restored})
    assert trade._plan_digest(left) == trade._plan_digest(right)
    assert original == before
    assert stable_original[-1] == original["messages"][-1]


def test_resume_block_requires_dedicated_new_authoring_failure_not_a_stale_legacy_error():
    from bellomberg.storage.trade_idea_store import _checkpoint_resume_block
    checkpoint = {"data": {"_model_authoring_completion": {"version": 1},
        "_model_completion_error": "An earlier phase stopped before the current workbook existed"},
        "specialist_checkpoints": {"fundamentals:R1": {"status": "complete"},
                                   "fundamentals:R1:dedicated-task": {"status": "complete"}}}
    assert _checkpoint_resume_block(checkpoint) is None
    checkpoint["data"]["_model_authoring_completion_failed"] = False
    assert _checkpoint_resume_block(checkpoint) is None
    checkpoint["data"]["_model_authoring_completion_failed"] = True
    assert _checkpoint_resume_block(checkpoint) == "model_authoring_review_required"


def test_successful_model_authoring_clears_stale_failure_before_later_stage_resume(native_case):
    case = native_case
    old_error = "A previous model-only attempt stopped with its work preserved"
    # Seed the historical error marker only. Paid receipts and unknown-cost
    # controls are untouched; the following workbook is compiled natively.
    case.board.data["_model_authoring_completion_failed"] = True
    case.board.data["_model_completion_error"] = old_error
    case.board.persist_run_checkpoint("preserved_model_authoring_error")
    assert complete(case) is True
    assert len(trade._verified_candidate_valuations(case.board)) == 1
    saved = case.store.get_run(case.child)["progress"]["checkpoint"]["data"]
    assert saved.get("_model_authoring_completion_failed") is not True
    assert not saved.get("_model_completion_error")
    assert old_error in saved.get("_historical_model_completion_errors", [])
    assert len(case.provider.new_calls) == 3

    # The same cleanup must survive re-entry when the exact workbook was
    # already persisted before an earlier process reached its cleanup block.
    late_error = "A stopped process left a failure marker beside a verified model"
    generations = deepcopy(case.board.valuation_generations)
    case.board.data["_model_authoring_completion_failed"] = True
    case.board.data["_model_completion_error"] = late_error
    case.board.persist_run_checkpoint("model_saved_before_error_cleanup")
    assert complete(case) is True
    assert case.board.valuation_generations == generations and len(case.provider.new_calls) == 3
    saved = case.store.get_run(case.child)["progress"]["checkpoint"]["data"]
    assert saved.get("_model_authoring_completion_failed") is not True
    assert not saved.get("_model_completion_error")
    assert saved.get("_historical_model_completion_errors", []).count(late_error) == 1

    case.store.finish_run(case.child, case.token, None, "incomplete",
                          reason="A later Capo stage stopped after the model was complete")
    before = rows(case.path, case.child)
    grandchild = case.store.create_continuation(case.child, idempotency_key="resume-after-model-success",
                                               authorize_new_requests=True)
    assert grandchild["created"] is True
    assert rows(case.path, grandchild["run"]["id"]) == []
    assert rows(case.path, case.child) == before
    assert_original_preserved(case)
