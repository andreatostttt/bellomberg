"""Approved output limits and immutable legacy requests; no live provider."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import httpx

from bellomberg.agents import capo, red_team
from bellomberg.agents.specialists.base import Blackboard, _checkpoint_digest
from bellomberg.core import llm_client
from bellomberg.valuation.preparation_ai import BudgetedProposer
from test_capo_collasso import _prepara, _msg, _bb, MEMO_VERO


def test_approved_chair_and_red_caps():
    assert capo.CAPO_MAX_TOKENS == 128000
    assert red_team.TRADE_IDEA_RED_MAX_TOKENS == 128000
    assert red_team.WEEKLY_RED_MAX_TOKENS == 128000


def test_capo_keeps_original_64k_request_after_policy_upgrade(monkeypatch):
    calls = _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    board = _bb()
    board.persist_run_checkpoint = lambda *a: None
    monkeypatch.setattr(capo, "CAPO_MAX_TOKENS", 64000)
    first, _ = capo.run_capo(board)
    frozen = deepcopy(board.data["_capo_request"])
    monkeypatch.setattr(capo, "CAPO_MAX_TOKENS", 128000)
    for _ in range(2):
        assert capo.run_capo(board)[0] == first
        assert calls[-1] == calls[0] and calls[-1]["max_tokens"] == 64000
        assert board.data["_capo_request"] == frozen


@pytest.mark.parametrize("field,value", [("max_tokens", 32000), ("model", "unapproved/model"),
                                          ("system", "changed system")])
def test_capo_rejects_arbitrary_legacy_cap_or_other_contract_changes(monkeypatch, field, value):
    calls = _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    board = _bb()
    board.persist_run_checkpoint = lambda *a: None
    capo.run_capo(board)
    board.data["_capo_request"][field] = value
    with pytest.raises(ValueError, match="checkpoint contract"):
        capo.run_capo(board)
    assert len(calls) == 1


@pytest.mark.parametrize("trade_idea,old_cap", [(False, 4200), (True, 65536)])
@pytest.mark.parametrize("mutation", [None, "system", "iterations", "max_tokens"])
def test_red_completed_legacy_contract_is_exact_and_never_dispatched(tmp_path, monkeypatch, trade_idea, old_cap, mutation):
    from bellomberg.core.trade_idea_contract import TRADE_IDEA_RED_TEAM_INSTRUCTIONS
    from bellomberg.core.language import prompt_for_language
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr("bellomberg.core.llm_pricing._fx_usd_to_eur",
                        lambda: (.9, "frozen synthetic FX fixture"))
    board = Blackboard()
    system = prompt_for_language(TRADE_IDEA_RED_TEAM_INSTRUCTIONS if trade_idea else red_team.RED_TEAM_PROMPT)
    from bellomberg.agents import chat_tools
    schema = [tool for tool in chat_tools.TOOL_DEFINITIONS if tool["name"] in
              ("get_portfolio_live", "get_portfolio_risk", "get_advanced_metrics")]
    contract = {"version": 1, "model": "synthetic/model", "system": system,
                "tools": schema, "iterations": 4, "max_tokens": old_cap,
                "thinking": {"type": "effort", "effort": "max"} if trade_idea else {"type": "adaptive"}}
    if mutation == "system": contract["system"] += " changed"
    if mutation == "iterations": contract["iterations"] = 5
    if mutation == "max_tokens": contract["max_tokens"] = old_cap - 1
    saved = {"contract": _checkpoint_digest(contract), "status": "complete", "critique": "Original exact critique"}
    monkeypatch.setattr(llm_client, "OpenRouterClient", lambda **kw: pytest.fail("completed legacy report dispatched"))
    invoke = lambda: red_team._run_red_team_loop(board, trade_idea, "synthetic/model", "Frozen user", schema, "legacy:red", saved)
    if mutation:
        with pytest.raises(ValueError, match="checkpoint contract"):
            invoke()
    else:
        assert invoke() == saved["critique"]


def preparer(path, cap, call, *, provider_cap=200000):
    return BudgetedProposer(path, authorized_usd="10", model="synthetic/model",
        max_tokens=cap, thinking={"type": "adaptive"}, call=call,
        metadata=lambda _: {"id": "synthetic/model", "context_length": 1000000,
            "top_provider": {"max_completion_tokens": provider_cap},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


@pytest.mark.parametrize("old_cap", [16000, 65536])
@pytest.mark.parametrize("outcome", ["received", "truncated", "unknown"])
def test_preparer_old_request_and_cost_survive_cap_upgrade(tmp_path, old_cap, outcome):
    calls = []
    def call(**body):
        calls.append(deepcopy(body))
        if outcome == "unknown": raise TimeoutError("synthetic lost receipt")
        return SimpleNamespace(id="frozen-" + str(len(calls)), model="synthetic/model", provider="synthetic",
            stop_reason="max_tokens" if outcome == "truncated" else "end_turn",
            usage=SimpleNamespace(cost_usd=.2), content=[SimpleNamespace(type="text", text='{"approved":false}')])
    path = tmp_path / "preparer.sqlite"
    old = preparer(path, old_cap, call)
    dossier, contract = {"ticker": "SYNTH", "evidence": "frozen"}, {}
    if outcome == "received": assert old(dossier, contract) == {"approved": False}
    else:
        with pytest.raises(TimeoutError if outcome == "unknown" else ValueError): old(dossier, contract)
    with old._db() as db: before = [dict(row) for row in db.execute("SELECT * FROM requests")]
    new = preparer(path, 128000, call)
    for _ in range(2):
        if outcome == "received": assert new(dossier, contract) == {"approved": False}
        else:
            with pytest.raises(RuntimeError if outcome == "unknown" else ValueError): new(dossier, contract)
        assert len(calls) == 1
    with new._db() as db: assert [dict(row) for row in db.execute("SELECT * FROM requests")] == before
    if outcome == "received":
        assert new({**dossier, "evidence": "different exact source"}, contract) == {"approved": False}
        assert len(calls) == 2 and calls[1]["max_tokens"] == 128000


def test_preparer_new_cap_remains_subject_to_provider_limit(tmp_path):
    p = preparer(tmp_path / "preparer.sqlite", 128000,
        lambda **_: pytest.fail("provider cap bypassed"), provider_cap=65536)
    with pytest.raises(ValueError, match="provider output limit"):
        p({"ticker": "SYNTH"}, {})
    assert p.summary()["requests"] == 0


@pytest.mark.parametrize("outcome", ["complete", "truncated", "unknown"])
def test_capo_legacy_stream_reopens_journal_without_repaying(tmp_path, monkeypatch, outcome):
    from bellomberg.core.request_journal import RequestJournal
    from test_llm_retry_stream import sse, chunk
    _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    requests = []
    def send(request):
        body = json.loads(request.content)
        requests.append(body)
        if outcome == "unknown":
            raise httpx.ReadTimeout("frozen lost receipt", request=request)
        return sse({"id": "original-64k-receipt", "model": body["model"],
                    **chunk({"content": MEMO_VERO})},
                   {**chunk(finish="length" if outcome == "truncated" else "stop"),
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100, "cost": .02}})
    client = llm_client.OpenRouterClient(api_key="offline-test", max_retries=0,
                                        trasporto=httpx.MockTransport(send))
    monkeypatch.setattr(capo, "OpenRouterClient", lambda **kw: client)
    def journal():
        return RequestJournal(tmp_path / "capo.sqlite", run_id="capo-cap-upgrade",
            authorization={"source": "frozen test"}, authorized_usd=10,
            metadata=lambda model: {"id": model, "context_length": 1_000_000,
                "top_provider": {"max_completion_tokens": 128000},
                "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    board = _bb()
    board.persist_run_checkpoint = lambda *a: None
    monkeypatch.setattr(capo, "CAPO_MAX_TOKENS", 64000)
    with llm_client.request_scope(journal(), phase="capo"):
        first, first_usage = capo.run_capo(board)
    frozen = deepcopy(board.data["_capo_request"])
    monkeypatch.setattr(capo, "CAPO_MAX_TOKENS", 128000)
    for _ in range(2):
        restored = _bb()
        restored.data["_capo_request"] = deepcopy(frozen)
        with llm_client.request_scope(journal(), phase="capo"):
            report, usage = capo.run_capo(restored)
        assert len(requests) == 1 and requests[0]["max_tokens"] == 64000
        assert restored.data["_capo_request"] == frozen
        if outcome == "unknown":
            assert usage["complete"] is False
            assert journal().summary()["cost_usd"] is None
        else:
            assert report == first and usage["complete"] == first_usage["complete"]
            assert usage["complete"] is (outcome == "complete")
            assert journal().summary()["cost_usd"] == pytest.approx(.02)
        assert journal().summary()["request_count"] == 1


@pytest.mark.parametrize("trade_idea,old_cap", [(False, 4200), (True, 65536)])
@pytest.mark.parametrize("state", ["failed", "truncated"])
def test_red_legacy_incomplete_loop_is_not_reopened(tmp_path, monkeypatch, trade_idea, old_cap, state):
    from bellomberg.core.trade_idea_contract import TRADE_IDEA_RED_TEAM_INSTRUCTIONS
    from bellomberg.core.language import prompt_for_language
    from bellomberg.agents import chat_tools
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr("bellomberg.core.llm_pricing._fx_usd_to_eur", lambda: (.9, "frozen FX"))
    board = Blackboard()
    schema = [tool for tool in chat_tools.TOOL_DEFINITIONS if tool["name"] in
              ("get_portfolio_live", "get_portfolio_risk", "get_advanced_metrics")]
    contract = {"version": 1, "model": "synthetic/model",
        "system": prompt_for_language(TRADE_IDEA_RED_TEAM_INSTRUCTIONS if trade_idea else red_team.RED_TEAM_PROMPT),
        "tools": schema, "iterations": 4, "max_tokens": old_cap,
        "thinking": {"type": "effort", "effort": "max"} if trade_idea else {"type": "adaptive"}}
    saved = {"contract": _checkpoint_digest(contract), "status": state}
    monkeypatch.setattr(llm_client, "OpenRouterClient", lambda **kw: pytest.fail("incomplete loop reopened"))
    for _ in range(2):
        with pytest.raises(ValueError, match="response incomplete"):
            red_team._run_red_team_loop(board, trade_idea, "synthetic/model", "Frozen", schema, "legacy:red", saved)


def test_extractor_and_reflection_use_approved_caps_without_thinking_change(monkeypatch):
    from bellomberg.agents import action_table_extract as ae, reflection as rf, scorekeeper as sk
    calls, clients = [], []
    def create(**body):
        calls.append(body)
        extract = "tools" in body
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=20,
                cache_read_input_tokens=0, cache_creation_input_tokens=0, cost_usd=.01),
            stop_reason="tool_use" if extract else "end_turn",
            content=[SimpleNamespace(type="tool_use", name="emit_action_table",
                input={"rows": [], "table_found": False})] if extract else
                [SimpleNamespace(type="text", text="Lezione congelata basata su esiti misurati.")])
    def client(**kw):
        clients.append(kw)
        return SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm_client, "OpenRouterClient", client)
    monkeypatch.setattr(sk, "compute_scorecard", lambda: {"overall": {"n": 30}})
    monkeypatch.setattr(sk, "format_track_record_for_capo", lambda *a, **kw: "frozen measured track")
    monkeypatch.setattr(rf, "_load_lessons", lambda: [])
    monkeypatch.setattr(rf, "_save_lessons", lambda value: None)
    assert "error" not in ae.extract_rows_structured("## ACTION TABLE\nNo actions")
    assert rf.generate_lesson("## ACTION TABLE\nNo actions")
    assert [call["max_tokens"] for call in calls] == [16000, 8000]
    assert [call["thinking"] for call in calls] == [{"type": "disabled"}] * 2
    assert calls[0]["model"] == llm_client.modello("action_extractor")
    assert calls[1]["model"] == llm_client.modello("reflection")
    assert clients == [{"timeout": 450.0, "max_retries": 1}, {"timeout": 240.0, "max_retries": 1}]


@pytest.mark.parametrize("cap,timeout", [(64000, 1800.0), (128000, 3600.0)])
def test_capo_timeout_follows_effective_output_cap(monkeypatch, cap, timeout):
    calls = _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    original = capo.OpenRouterClient
    clients = []
    def client(**kw):
        clients.append(kw)
        return original(**kw)
    monkeypatch.setattr(capo, "OpenRouterClient", client)
    monkeypatch.setattr(capo, "CAPO_MAX_TOKENS", cap)
    capo.run_capo(_bb())
    assert calls[0]["max_tokens"] == cap
    assert clients == [{"timeout": timeout, "max_retries": 0}]


@pytest.mark.parametrize("cap,timeout", [(16000, 450.0), (128000, 3600.0)])
def test_native_preparer_timeout_follows_output_cap(tmp_path, monkeypatch, cap, timeout):
    clients, calls, closed = [], [], []
    def create(**body):
        calls.append(body)
        return SimpleNamespace(id="frozen-native", model="synthetic/model", provider="synthetic",
            stop_reason="end_turn", usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type="text", text='{"approved":false}')])
    def client(**kw):
        clients.append(kw)
        return SimpleNamespace(messages=SimpleNamespace(create=create),
                               _http=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(llm_client, "OpenRouterClient", client)
    p = preparer(tmp_path / "native-preparer.sqlite", cap, None)
    assert p({"ticker": "SYNTH"}, {}) == {"approved": False}
    assert calls[0]["max_tokens"] == cap
    assert clients == [{"timeout": timeout, "max_retries": 0}]
    assert closed == [True]
