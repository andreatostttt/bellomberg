"""A completed native author task compiles its saved draft without another AI turn."""
from copy import deepcopy
from pathlib import Path

import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.agents.specialists import base
from bellomberg.core import llm_client
from test_trade_idea_model_authoring_recovery import (
    native_case, migrated, db_path, bb, complete, _operating_plan, assert_original_preserved, bind, Crash,
)
from bellomberg.storage.trade_idea_store import RunConflict


def submit_only_provider(case, calls, *, terminal="end_turn", final_iteration=30):
    def create(**kwargs):
        body = base._checkpoint_json(kwargs)
        calls.append(body)
        number = len(calls)
        if number < final_iteration - 1:
            blocks, stop = [llm_client.ToolUseBlock("tail-read-" + str(number), "read_blackboard", {})], "tool_use"
        elif number == final_iteration - 1:
            decisions = [{"consultation_id": row["id"], "decision": "incorporated",
                          "rationale": "Keep the saved peer outcome and author the supported frozen plan."}
                         for row in case.board.data["_model_consultations"]]
            blocks = [llm_client.ToolUseBlock("tail-submit-only", "submit_candidate_model_plan", {
                "plan": _operating_plan(), "consultation_decisions": decisions})]
            stop = "tool_use"
        else:
            assert number == final_iteration
            if final_iteration == 30:
                assert body["tool_choice"] == {"type": "none"}
            blocks = ([llm_client.ThinkingBlock("Provider error, no completed output.")]
                      if terminal == "error" else [llm_client.TextBlock("Author decisions saved; compiler output is still required.")])
            stop = terminal
        return llm_client.Messaggio(id="tail-native-" + str(number), model=body["model"],
            content=blocks, stop_reason=stop, finish_reason=stop,
            usage=llm_client.Usage(input_tokens=100, output_tokens=50, reasoning_tokens=20,
                cache_read_input_tokens=0, cache_creation_input_tokens=0, cost_usd=.003))
    return create


def test_submit_only_turn29_then_forced_report30_compiles_real_workbook_once(native_case):
    case = native_case
    calls, builds = [], []
    real_build = case.board.build_candidate_model
    def build(inputs):
        builds.append(deepcopy(inputs))
        assert len(calls) == 30  # Compiler is reached only after the completed paid task.
        return real_build(inputs)
    case.board.build_candidate_model = build
    case.provider.create = submit_only_provider(case, calls)
    assert complete(case) is True
    assert len(calls) == 30 and builds == [{"ticker": "SYNTH-EXT"}]
    assert all("get_valuation" not in [block.get("name") for message in body["messages"]
        if isinstance(message.get("content"), list) for block in message["content"]
        if block.get("type") == "tool_use"] for body in calls)
    refs = trade._verified_candidate_valuations(case.board)
    assert len(refs) == 1 and Path(case.board.valuation_results["SYNTH-EXT"]["path"]).is_file()
    attempts = deepcopy(case.board.data["_model_compilation_attempts"])
    assert len(attempts) == 1 and attempts[0]["status"] == "succeeded"
    assert len(case.board.valuation_generations) == 1
    assert_original_preserved(case)
    assert complete(case) is True
    assert len(calls) == 30 and len(builds) == 1
    assert case.board.data["_model_compilation_attempts"] == attempts


@pytest.mark.parametrize("terminal", ["error", "max_tokens"])
def test_failed_or_truncated_author_does_not_trigger_tail_compilation(native_case, terminal):
    case = native_case
    calls = []
    case.provider.create = submit_only_provider(case, calls, terminal=terminal, final_iteration=2)
    case.board.build_candidate_model = lambda _inputs: pytest.fail("Incomplete author task must not auto-compile")
    assert complete(case) is False
    assert len(calls) == 2 and case.board.data.get("_model_input_draft")
    key = next(key for key in case.board.specialist_checkpoints if key.startswith("fundamentals:R1:"))
    assert case.board.specialist_checkpoints[key]["status"] == ("failed" if terminal == "error" else "truncated")
    assert not trade._verified_candidate_valuations(case.board)
    assert not case.board.data.get("_model_compilation_attempts")
    assert case.board.data["_model_authoring_completion_failed"] is True
    assert_original_preserved(case)


@pytest.mark.parametrize("attempt", ["none", "same_failed", "old_failed", "qualification_failed"])
def test_native_continuation_for_saved_local_compile_never_restarts_author(native_case, attempt):
    case = native_case
    calls = []
    case.provider.create = submit_only_provider(case, calls, final_iteration=2)
    # The real interrupted lineage can retain the previous model failure flag
    # while a new paid author task successfully saves the remaining drivers.
    case.board.data["_model_authoring_completion_failed"] = True
    case.board.data["_model_completion_error"] = "Previous incomplete model; paid draft has since advanced"
    persist = case.board.persist_run_checkpoint
    def crash(event="checkpoint", payload=None):
        persist(event, payload)
        if event == "specialist_report" and any(key.startswith("fundamentals:R1:")
            and value.get("status") == "complete" for key, value in case.board.specialist_checkpoints.items()):
            raise Crash("Completed report durable, local compiler not yet reached")
    case.board.persist_run_checkpoint = crash
    with pytest.raises(Crash):
        complete(case)
    assert len(calls) == 2 and case.board.data.get("_model_input_draft")
    case.board.persist_run_checkpoint = persist
    if attempt == "qualification_failed":
        case.board.data["_model_plan_last_error"] = {"status": "historical_proofs_incomplete",
            "plan_sha256": trade._plan_digest(case.board.data["_model_input_draft"]),
            "error": "Frozen qualification rejection before the compilation-attempt journal"}
        persist("frozen_prior_qualification_failure")
    elif attempt != "none":
        plan_sha = trade._plan_digest(case.board.data["_model_input_draft"])
        case.board.data["_model_compilation_attempts"] = [{"status": "failed",
            "source_fingerprint": case.board.source_qualification["fingerprint"],
            "plan_sha256": plan_sha if attempt == "same_failed" else "f" * 64,
            "error": "Frozen prior financial-validation failure"}]
        persist("frozen_prior_compile_attempt")
    parent = case.child
    case.store.interrupt_run(parent, reason="Interrupted after complete paid authoring")
    before = deepcopy(case.store.get_run(parent))
    if attempt in ("same_failed", "qualification_failed"):
        assert before["recovery"]["can_continue"] is False
        with pytest.raises(RunConflict, match="model_authoring_review_required"):
            case.store.create_continuation(parent, idempotency_key="repeat-same-plan", authorize_new_requests=True)
        return
    assert before["recovery"]["can_continue"] is True
    child = case.store.create_continuation(parent, idempotency_key="local-compile-only", authorize_new_requests=True)
    case.child = child["run"]["id"]
    case.board, case.token = bind(case, case.child)
    case.board.model_phase = "building"
    def no_provider(**kwargs):
        pytest.fail("The completed author task must not restart during local compilation")
    case.provider.create = no_provider
    assert case.board.build_candidate_model({"ticker": "SYNTH-EXT"})["ok"] is True
    assert complete(case) is True
    assert len(calls) == 2 and len(trade._verified_candidate_valuations(case.board)) == 1
    assert case.store.get_run(parent)["progress"] == before["progress"]
    assert case.store.get_run(case.child)["cost"]["charged_usd"] == before["cost"]["charged_usd"]
    assert_original_preserved(case)
