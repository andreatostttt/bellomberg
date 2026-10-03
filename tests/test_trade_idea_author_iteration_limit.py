"""Only the Trade Idea author gets more tool turns; providers and tools are synthetic."""
from types import SimpleNamespace

import pytest

from bellomberg.agents.specialists import base
from test_run_robustness import bb, _FakeClient, _MockSpecialist, _tool_resp, _text_resp


@pytest.mark.parametrize("name,scope,round_n,phase,task,expected", [
    ("fundamentals", "trade_idea", 1, "building", None, 30),
    ("fundamentals", "trade_idea", 1, "building", {"consultation": False}, 30),
    ("fundamentals", "trade_idea", 1, "building", {"consultation": True}, 10),
    ("fundamentals", "weekly", 1, "building", None, 10),
    ("fundamentals", "trade_idea", 0, "building", None, 10),
    ("fundamentals", "trade_idea", 2, "building", None, 10),
    ("fundamentals", "trade_idea", 1, "research", None, 10),
    ("fundamentals", "trade_idea", 1, "review", None, 10),
    ("macro", "trade_idea", 1, "building", None, 10),
    ("quant", "trade_idea", 1, "building", None, 10),
])
def test_iteration_scope_is_only_r1_building_author(name, scope, round_n, phase, task, expected):
    actor = object.__new__(base.Specialist)
    actor.name = name
    actor.blackboard = SimpleNamespace(run_scope=scope, model_phase=phase)
    assert base.Specialist._max_tool_iterations_for_round(actor, round_n, task) == expected
    assert actor._max_tokens_for_round(round_n, task) == 128000
    assert base.MAX_TOOL_ITERS_SPECIALIST == 10
    assert base.MAX_TOKENS_SPECIALIST == 128000


@pytest.mark.parametrize("name,phase,task,end_at,expected_calls,expected_tokens", [
    ("fundamentals", "building", None, None, 30, 128000),
    ("fundamentals", "building", None, 12, 12, 128000),
    ("fundamentals", "building", {"consultation": True}, None, 10, 128000),
    ("fundamentals", "research", None, None, 10, 128000),
    ("macro", "building", None, None, 10, 128000),
])
def test_native_loop_uses_local_limit_and_preserves_per_call_contract(
        bb, monkeypatch, name, phase, task, end_at, expected_calls, expected_tokens):
    executed, wrapped = [], []
    bb.run_scope, bb.model_phase, bb.target_ticker = "trade_idea", phase, "SYNTH"
    def wrap_client(client, *, role):
        wrapped.append(role)
        return client
    bb.budget_gate = SimpleNamespace(wrap_client=wrap_client)
    bb.tool_receipts = []
    bb.source_qualification = {}
    def response(n, kwargs):
        forced = kwargs.get("tool_choice") == {"type": "none"}
        if forced:
            assert end_at is None and n == expected_calls
        elif end_at is None:
            assert n < expected_calls
        result = (_text_resp("Synthetic report with all remaining evidence gaps declared.")
                  if forced or n == end_at else _tool_resp())
        result.usage.cost_usd = 0.001  # synthetic receipt; no provider or billing request
        return result
    client = _FakeClient(response)
    actor_type = type("SyntheticIterationSpecialist", (_MockSpecialist,), {"name": name})
    actor = actor_type(bb, client=client)
    monkeypatch.setattr(actor, "_model_for_round", lambda round_n: "synthetic/model")
    monkeypatch.setattr(actor, "_build_round_context", lambda round_n: "Synthetic current context.")
    monkeypatch.setattr(actor, "_build_tools_schema", lambda: [{"name": "read_blackboard",
        "description": "Synthetic read", "input_schema": {"type": "object", "properties": {}}}])
    def execute(name, inputs):
        executed.append(name)
        return {"ok": True, "source": "synthetic"}
    monkeypatch.setattr(actor, "_execute_meta_tool", execute)
    report = actor.run(1, task_context=task)
    assert wrapped == ["specialist:" + name]
    assert len(client.calls) == expected_calls
    assert len(executed) == len(bb.tool_log) == expected_calls - 1
    assert bb.usage_log[-1]["api_calls"] == expected_calls
    assert all(call["model"] == "synthetic/model" for call in client.calls)
    assert all(call["max_tokens"] == expected_tokens for call in client.calls)
    assert all(call["thinking"] == {"type": "effort", "effort": "max"} for call in client.calls)
    if end_at is None:
        assert report.startswith(f"[REPORT FORZATO AL LIMITE ITERAZIONI ({expected_calls})")
        assert client.calls[-1]["tool_choice"] == {"type": "none"}
        last = client.calls[-1]["messages"][-1]["content"][-1]
        assert f"LIMITE ITERAZIONI TOOL RAGGIUNTO ({expected_calls})" in last["text"]
        assert all("tool_choice" not in call for call in client.calls[:-1])
    else:
        assert "REPORT FORZATO" not in report
        assert all("tool_choice" not in call for call in client.calls)


@pytest.mark.parametrize("name,phase,task,expected_timeout", [
    ("fundamentals", "building", None, 3600.0),
    ("fundamentals", "building", {"consultation": True}, 3600.0),
    ("fundamentals", "research", None, 3600.0),
    ("macro", "building", None, 3600.0),
])
def test_owned_client_timeout_follows_effective_output_cap(bb, monkeypatch, name, phase, task, expected_timeout):
    import httpx
    created = []
    raw = _FakeClient(lambda n, kw: _text_resp("Synthetic completed report. " * 80))
    raw.timeout = base.TIMEOUT_SPECIALIST_S
    raw._http = SimpleNamespace(timeout=httpx.Timeout(raw.timeout, connect=30.0))
    def constructor(**kwargs):
        created.append(kwargs)
        return raw
    monkeypatch.setattr(base, "OpenRouterClient", constructor)
    bb.run_scope, bb.model_phase, bb.target_ticker = "trade_idea", phase, "SYNTH"
    bb.budget_gate = SimpleNamespace(wrap_client=lambda client, **kw: client)
    bb.tool_receipts, bb.source_qualification = [], {}
    actor_type = type("SyntheticOwnedClientSpecialist", (_MockSpecialist,), {"name": name})
    actor = actor_type(bb)
    monkeypatch.setattr(actor, "_model_for_round", lambda round_n: "synthetic/model")
    monkeypatch.setattr(actor, "_build_round_context", lambda round_n: "Synthetic context.")
    monkeypatch.setattr(actor, "_build_tools_schema", lambda: [])
    actor.run(1, task_context=task)
    assert created == [{"timeout": 3600.0, "max_retries": 0}]
    assert len(raw.calls) == 1
    assert raw.timeout == expected_timeout
    assert raw._http.timeout.connect == 30.0
    assert all(getattr(raw._http.timeout, field) == expected_timeout for field in ("read", "write", "pool"))
    if name == "fundamentals" and phase == "building" and task is None:
        actor.run(1, task_context={"consultation": True})
        assert len(raw.calls) == 2 and len(created) == 1
        assert raw.timeout == 3600.0 and raw._http.timeout.read == 3600.0
        assert raw.calls[-1]["max_tokens"] == 128000
