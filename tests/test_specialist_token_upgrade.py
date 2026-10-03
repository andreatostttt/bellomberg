"""Token policy upgrades preserve the contracts of already paid native stages."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bellomberg.agents.specialists import base
from test_run_robustness import bb, _FakeClient, _MockSpecialist, _tool_resp, _text_resp


REPORT = "Complete synthetic research with explicitly sourced evidence and declared gaps. " * 40


class Crash(BaseException):
    pass


def actor(board, monkeypatch, client, cap, scope):
    board.run_scope, board.model_phase, board.target_ticker = scope, "research", "SYNTH"
    board.budget_gate = SimpleNamespace(wrap_client=lambda client, **kwargs: client)
    board.source_qualification = {"fingerprint": "frozen-synthetic-source"}
    board.persist_run_checkpoint = lambda *args: None
    instance = _MockSpecialist(board, client=client)
    monkeypatch.setattr(instance, "_max_tokens_for_round", lambda *args: cap)
    monkeypatch.setattr(instance, "_model_for_round", lambda *args: "synthetic/model")
    monkeypatch.setattr(instance, "_build_round_context", lambda *args: "Frozen synthetic context")
    monkeypatch.setattr(instance, "_build_tools_schema", lambda: [{
        "name": "read_blackboard", "description": "Read frozen synthetic evidence",
        "input_schema": {"type": "object", "properties": {}}}])
    return instance


def legacy_checkpoint(board):
    # Old persisted payloads had only the full contract digest, no explicit cap.
    for state in board.specialist_checkpoints.values():
        state.pop("max_tokens", None)
        state.pop("sha256")
        state["sha256"] = base._checkpoint_digest(state)


@pytest.mark.parametrize("scope", ["weekly", "trade_idea"])
@pytest.mark.parametrize("old_cap", [16000, 64000, 65536])
def test_completed_legacy_stage_survives_output_policy_upgrade(bb, monkeypatch, scope, old_cap):
    first = _FakeClient(lambda n, kw: _text_resp(REPORT))
    original = actor(bb, monkeypatch, first, old_cap, scope).run(1)
    legacy_checkpoint(bb)
    saved = deepcopy(bb.specialist_checkpoints)
    later = _FakeClient(lambda *args: pytest.fail("A complete paid stage must not be dispatched again"))
    recovered = actor(bb, monkeypatch, later, 128000, scope)
    assert recovered.run(1) == original and original.endswith(REPORT)
    assert recovered.run_result_status == "complete"
    assert bb.specialist_checkpoints == saved
    assert len(first.calls) == 1 and not later.calls


@pytest.mark.parametrize("scope", ["weekly", "trade_idea"])
def test_crashed_legacy_tool_turn_keeps_original_cap_and_never_reexecutes_tool(bb, monkeypatch, scope):
    first = _FakeClient(lambda n, kw: _tool_resp())
    old = actor(bb, monkeypatch, first, 16000, scope)
    tools = []
    monkeypatch.setattr(old, "_execute_meta_tool", lambda *args: tools.append(args) or {"ok": True})
    def stop(event, payload):
        if event == "specialist_turn":
            raise Crash("after durable tool turn")
    bb.persist_run_checkpoint = stop
    with pytest.raises(Crash):
        old.run(1)
    legacy_checkpoint(bb)
    later = _FakeClient(lambda n, kw: _text_resp(REPORT))
    recovered = actor(bb, monkeypatch, later, 128000, scope)
    monkeypatch.setattr(recovered, "_execute_meta_tool", lambda *args: pytest.fail("Tool already paid and saved"))
    assert recovered.run(1) == REPORT
    assert len(tools) == len(first.calls) == len(later.calls) == 1
    assert later.calls[0]["max_tokens"] == 16000
    saved = deepcopy(bb.specialist_checkpoints)
    assert recovered.run(1) == REPORT and len(later.calls) == 1
    assert bb.specialist_checkpoints == saved
    # A new stage, unlike an existing contract, receives the new output policy.
    if scope == "weekly":
        assert recovered.run(2).endswith(REPORT)
        assert later.calls[-1]["max_tokens"] == 128000


@pytest.mark.parametrize("scope", ["weekly", "trade_idea"])
def test_token_upgrade_does_not_accept_a_changed_model_contract(bb, monkeypatch, scope):
    first = _FakeClient(lambda n, kw: _text_resp(REPORT))
    actor(bb, monkeypatch, first, 16000, scope).run(1)
    legacy_checkpoint(bb)
    later = _FakeClient(lambda *args: pytest.fail("Changed contracts cannot dispatch"))
    changed = actor(bb, monkeypatch, later, 128000, scope)
    monkeypatch.setattr(changed, "_model_for_round", lambda *args: "different/model")
    with pytest.raises(ValueError, match="contract differs"):
        changed.run(1)
    assert not later.calls


@pytest.mark.parametrize("scope", ["weekly", "trade_idea"])
def test_token_upgrade_does_not_authorize_repeating_a_truncated_response(bb, monkeypatch, scope):
    response = _text_resp(REPORT)
    response.stop_reason = "max_tokens"
    first = _FakeClient(lambda n, kw: response)
    actor(bb, monkeypatch, first, 16000, scope).run(1)
    legacy_checkpoint(bb)
    later = _FakeClient(lambda *args: pytest.fail("A token increase is not permission to retry"))
    recovered = actor(bb, monkeypatch, later, 128000, scope)
    with pytest.raises(RuntimeError, match="explicit review required"):
        recovered.run(1)
    assert len(first.calls) == 1 and not later.calls
