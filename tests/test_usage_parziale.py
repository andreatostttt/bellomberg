"""Un contatore assente non diventa zero; costo provider e token sono indipendenti."""
from types import SimpleNamespace

import pytest
import sys
import json
import httpx

from bellomberg.core import llm_client
from bellomberg.core import llm_pricing
from bellomberg.agents.specialists.base import Blackboard
from bellomberg.agents.specialists import base


def u(**cambi):
    valori = dict(input_tokens=10, output_tokens=4, cache_read_input_tokens=0,
                  cache_creation_input_tokens=0, cost_usd=0.03)
    valori.update(cambi)
    return SimpleNamespace(**valori)


@pytest.mark.parametrize("risposta, atteso", [(None, None), (u(input_tokens=None), None),
                                             (u(input_tokens=0), 0), (u(), 10)])
def test_accumulo_distingue_assente_da_zero(risposta, atteso):
    totale = llm_client.somma_usage(None, risposta)
    assert totale["in"] == atteso
    assert totale["tokens_status"] == ("completo" if atteso is not None else "parziale")


def test_chiamata_successiva_non_ripara_token_mancanti_ma_costo_resta():
    a = llm_client.somma_usage(None, u(input_tokens=None))
    b = llm_client.somma_usage(a, u())
    assert b["in"] is None and b["out"] == 8
    assert b["tokens_missing"] == ["in"]
    assert b["cost_usd"] == pytest.approx(0.06)


def test_listino_non_prezzabile_con_input_parziale():
    r = llm_pricing.cost_usd("claude-opus-5", {"in": None, "out": 4})
    assert r["cost"] is None and r["status"] == "usage_unknown"
    assert r["breakdown"]["tokens"]["in"] is None


@pytest.mark.parametrize("valore", [True, 1.5, float("inf"), float("nan"), -1])
def test_contatore_invalido_non_diventa_misura(valore, monkeypatch):
    assert llm_pricing.normalize_usage({"in": valore})["in"] is None
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    bb = Blackboard()
    entry = bb.record_usage("quant", 1, "claude-opus-5", {"in": valore, "out": 4})
    assert entry["in"] is None


def test_costo_booleano_non_e_misurato():
    assert llm_client.somma_costo(0.0, u(cost_usd=False)) is None


def test_costo_provider_prevale_anche_con_token_parziali():
    r = llm_pricing.cost_usd("claude-opus-5", {"in": None, "out": 4, "cost_usd": 0.07})
    assert r["cost"] == 0.07 and r["status"] == "ok"
    assert r["breakdown"]["tokens"]["in"] is None


def test_blackboard_e_totale_preservano_null_e_costo_misurato(monkeypatch):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    llm_pricing.reset_fx_memo()
    try:
        bb = Blackboard()
        r = bb.record_usage("quant", 1, "claude-opus-5",
                            {"in": None, "out": 4, "cache_read": 0, "cache_write": 0, "cost_usd": 0.1},
                            status="usage_unknown")
        assert r["in"] is None and r["cost_eur"] == pytest.approx(0.09)
        bb.record_usage("quant", 2, "claude-opus-5",
                        {"in": 10, "out": 4, "cache_read": 0, "cache_write": 0, "cost_usd": 0.1})
        by, total = bb._usage_aggregates()
        for risultato in (by["quant"], total):
            assert risultato["in"] is None and risultato["out"] == 8
            assert risultato["tokens_status"] == "parziale"
            assert risultato["tokens_missing"] == ["in"]
            assert risultato["cost_eur"] == pytest.approx(0.18)
    finally:
        llm_pricing.reset_fx_memo()


@pytest.mark.parametrize("risposta", [None, u(input_tokens=None), u(input_tokens=0)])
def test_round_reale_con_usage_incompleta_conserva_report_e_contatori(monkeypatch, risposta):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", SimpleNamespace(
        current_facts_block=lambda: "", favorites_block=lambda: "", pm_theses_block=lambda: ""), raising=False)
    llm_pricing.reset_fx_memo()
    try:
        bb = Blackboard()
        report = "REPORT SINTETICO COMPLETO. " * 40
        resp = SimpleNamespace(usage=risposta, stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=report)])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
        spec = base.Specialist(bb, client=client)
        monkeypatch.setattr(spec, "_build_tools_schema", lambda: [])
        assert spec.run(0).strip() == report.strip()
        entry = bb.usage_log[0]
        assert entry["in"] == (getattr(risposta, "input_tokens", None))
        if risposta is not None:
            assert entry["cost_eur"] == pytest.approx(0.027)
        else:
            assert entry["cost_eur"] is None
    finally:
        llm_pricing.reset_fx_memo()


def test_chat_usage_parziale_non_perde_il_costo_provider(monkeypatch):
    from bellomberg.agents import chat_engine
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    llm_pricing.reset_fx_memo()
    try:
        r = chat_engine._done_payload(1, ok=True, model="claude-opus-5", tokens_in=None,
                                      tokens_out=4, cache_read=None, cache_write=None,
                                      iterations=1, usage_visto=True, cost_usd=0.2)
        assert r["ok"] and r["tokens_in"] is None and r["tokens_status"] == "parziale"
        assert r["cost_eur"]["cost"] == pytest.approx(0.18)
    finally:
        llm_pricing.reset_fx_memo()


@pytest.mark.parametrize("crash_phase", ["after_tool", "during_tool"])
def test_specialist_crash_reuses_paid_response_and_completed_tool_then_second_resume(tmp_path, monkeypatch, crash_phase):
    from bellomberg.core.request_journal import RequestJournal
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", SimpleNamespace(
        current_facts_block=lambda: "frozen context", favorites_block=lambda: "", pm_theses_block=lambda: ""), raising=False)
    monkeypatch.setattr(base.Specialist, "_build_round_context", lambda self, rnd: "frozen initial task")
    monkeypatch.setattr(base.Specialist, "_model_for_round", lambda self, rnd: "test/model")
    monkeypatch.setattr(base.Specialist, "_build_tools_schema", lambda self: [
        {"name": "frozen_lookup", "description": "test", "input_schema": {"type": "object", "properties": {}}}])
    completed_tools, dispatched = [], []
    def run_tool(self, name, args):
        completed_tools.append(args["number"])
        if crash_phase == "during_tool" and completed_tools == [1]:
            raise Crash()
        return {"verified": args["number"]}
    monkeypatch.setattr(base.Specialist, "_execute_meta_tool", run_tool)
    usage = {"prompt_tokens": 20, "completion_tokens": 5, "cost": 0.00003,
             "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}
    def send(request):
        dispatched.append(json.loads(request.content))
        if len(dispatched) == 1:
            message = {"tool_calls": [{"id": "tool-" + str(n), "type": "function", "function": {
                "name": "frozen_lookup", "arguments": json.dumps({"number": n})}} for n in (1, 2)]}
            finish = "tool_calls"
        else:
            message, finish = {"content": "Verified report. " * 80}, "stop"
        reply = "reply-" + str(len(dispatched))
        if dispatched[-1].get("stream"):
            # ZR 05/10: dal 02/10 il desk col client vero va in STREAMING (SSE); stessa
            # risposta a pezzi, con la usage nel chunk finale come OpenRouter.
            delta = ({"tool_calls": [dict(call, index=i) for i, call in enumerate(message["tool_calls"])]}
                     if "tool_calls" in message else message)
            chunks = [{"id": reply, "model": "test/model", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": reply, "model": "test/model", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                      {"id": reply, "model": "test/model", "choices": [], "usage": usage}]
            body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode("utf-8"))
        return httpx.Response(200, json={"id": reply, "model": "test/model",
            "choices": [{"message": message, "finish_reason": finish}], "usage": usage})
    class Crash(BaseException):
        pass
    persisted = {}
    crash_once = True
    def build_board():
        bb = Blackboard()
        bb.request_journal = RequestJournal(tmp_path / "llm.sqlite", run_id="crash-test",
            authorization={"source": "offline_test"}, authorized_usd="1",
            # ZR 05/10: il cap del desk e' ora 128000 token (> 40000 di prima): la quota esige
            # max_tokens < context_length: 200000 (riserva 0,456 USD entro l autorizzazione di 1 USD).
            metadata=lambda model: {"id": model, "context_length": 200_000,
                "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
        for key, value in persisted.items():
            setattr(bb, key, json.loads(json.dumps(value)))
        def save(event, payload):
            nonlocal crash_once
            for key in ("specialist_checkpoints", "tool_log", "tool_receipts", "usage_log", "data"):
                persisted[key] = json.loads(json.dumps(getattr(bb, key)))
            if event == "specialist_tool" and crash_once and crash_phase == "after_tool":
                crash_once = False
                raise Crash()
        bb.persist_run_checkpoint = save
        return bb
    client = llm_client.OpenRouterClient(api_key="offline", trasporto=httpx.MockTransport(send))
    bb = build_board()
    with pytest.raises(Crash):
        base.Specialist(bb, client=client).run(1)
    assert dispatched and completed_tools == [1]
    if crash_phase == "during_tool":
        for _ in range(2):
            bb = build_board()
            with pytest.raises(RuntimeError, match="tool.*unknown|unknown.*tool"):
                base.Specialist(bb, client=client).run(1)
        assert len(dispatched) == 1 and completed_tools == [1]
        return
    for _ in range(2):
        bb = build_board()
        spec = base.Specialist(bb, client=client)
        assert "Verified report." in spec.run(1)
        assert spec.run_result_status == "complete"
    assert len(dispatched) == 2 and completed_tools == [1, 2]
    assert bb.request_journal.summary()["request_count"] == 2
    assert len(bb.usage_log) == 1 and bb.usage_log[0]["api_calls"] == 2
    assert bb.usage_log[0]["cost_usd"] == pytest.approx(0.00006)
    assert len(bb.tool_log) == 2


def test_weekly_specialist_first_provider_failure_is_not_zero_or_complete(monkeypatch):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    monkeypatch.setattr(base.Specialist, "_build_round_context", lambda self, rnd: "frozen context")
    monkeypatch.setattr(base.Specialist, "_build_tools_schema", lambda self: [])
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", SimpleNamespace(
        current_facts_block=lambda: "", favorites_block=lambda: "", pm_theses_block=lambda: ""), raising=False)
    cause = llm_client.APIStatusError(504, "original provider timeout")
    cause.request_id = "failed-request"
    calls, failures = [], []
    def fail(**kwargs):
        calls.append(kwargs)
        raise cause
    bb = Blackboard()
    bb.record_run_failure = lambda error, **meta: failures.append((error, meta))
    spec = base.Specialist(bb, client=SimpleNamespace(messages=SimpleNamespace(create=fail)))
    result = spec.run(1)
    assert len(calls) == 1 and "original provider timeout" in result
    assert spec.run_result_status == "failed"
    assert failures[0][0] is cause and failures[0][1]["request_id"] == "failed-request"
    assert bb.usage_log[0]["cost_usd"] is None and bb.usage_log[0]["cost_eur"] is None


def test_preparer_owns_its_receipt_without_double_weekly_cost(tmp_path, monkeypatch):
    from bellomberg.core.request_journal import RequestJournal
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    metadata = lambda model: {"id": model, "context_length": 100000,
        "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
    dispatches = []
    def send(request):
        dispatches.append(request)
        return httpx.Response(200, json={"id": "prepared", "model": "test/model",
            "choices": [{"message": {"content": '{"model":{}}'}, "finish_reason": "stop"}],
            "usage": {"cost": 0.00003}})
    client = llm_client.OpenRouterClient(api_key="offline", trasporto=httpx.MockTransport(send))
    proposer = BudgetedProposer(tmp_path / "preparation.sqlite", authorized_usd="1",
        model="test/model", max_tokens=20, thinking={"type": "disabled"},
        metadata=metadata, call=client.messages.create)
    bb = Blackboard(valuation_preparer=proposer)
    journal = RequestJournal(tmp_path / "weekly.sqlite", run_id="weekly-test",
        authorization={"source": "test"}, authorized_usd="1", metadata=metadata)
    with llm_client.request_scope(journal, phase="specialist", agent="fundamentals"):
        assert bb.valuation_preparer({"ticker": "TEST"}, {}) == {"model": {}}
        assert bb.valuation_preparer({"ticker": "TEST"}, {}) == {"model": {}}
    assert len(dispatches) == 1 and proposer.summary()["requests"] == 1
    assert proposer.summary()["spent_usd"] == 0.00003
    assert journal.summary()["native_request_count"] == 0
    assert journal.summary()["request_count"] == 1
    assert journal.summary()["cost_usd"] == 0.00003
    assert len(journal.summary()["external_requests"]) == 1
    assert RequestJournal.read_summary(journal.path) == journal.summary()
    later = RequestJournal(tmp_path / "later-weekly.sqlite", run_id="later-weekly",
        authorization={"source": "later-test"}, authorized_usd="1", metadata=metadata)
    with llm_client.request_scope(later, phase="specialist", agent="fundamentals"):
        assert bb.valuation_preparer({"ticker": "TEST"}, {}) == {"model": {}}
    assert len(dispatches) == 1 and proposer.summary()["requests"] == 1
    reused_summary = RequestJournal.read_summary(later.path)
    assert reused_summary["request_count"] == 0 and reused_summary["cost_usd"] == 0
    assert len(reused_summary["external_requests"]) == 1
    assert reused_summary["external_requests"][0]["owned"] is False


def test_unknown_preparer_reservation_is_in_total_and_blocks_further_calls(tmp_path, monkeypatch):
    from bellomberg.core.request_journal import RequestJournal
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    metadata = lambda model: {"id": model, "context_length": 100000,
        "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
    dispatches = []
    def send(request):
        dispatches.append(request)
        return httpx.Response(200, json={"id": "prepared", "model": "test/model",
            "choices": [{"message": {"content": '{"model":{}}'}, "finish_reason": "stop"}],
            "usage": {}})
    client = llm_client.OpenRouterClient(api_key="offline", trasporto=httpx.MockTransport(send))
    proposer = BudgetedProposer(tmp_path / "preparation.sqlite", authorized_usd="1",
        model="test/model", max_tokens=20, thinking={"type": "disabled"},
        metadata=metadata, call=client.messages.create)
    bb = Blackboard(valuation_preparer=proposer)
    journal = RequestJournal(tmp_path / "weekly.sqlite", run_id="weekly-test",
        authorization={"source": "test"}, authorized_usd="1", metadata=metadata)
    with llm_client.request_scope(journal, phase="specialist", agent="fundamentals"):
        with pytest.raises(RuntimeError, match="unresolved"):
            bb.valuation_preparer({"ticker": "TEST"}, {})
        with pytest.raises(RuntimeError, match="preparer request unresolved"):
            client.messages.create(model="test/model", max_tokens=20,
                messages=[{"role": "user", "content": "must not dispatch"}])
    summary = RequestJournal.read_summary(journal.path)
    assert len(dispatches) == 1 and summary["request_count"] == 1
    assert summary["cost_usd"] is None and summary["remaining_known_usd"] is None
    assert summary["unknown_requests"] == 1 and summary["reserved_usd"] > 0


def test_usage_replay_and_phase_snapshot_cannot_count_receipt_twice(monkeypatch):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    receipt = u()
    receipt.request_id = "paid-one"
    total = llm_client.somma_usage(None, receipt)
    assert llm_client.somma_usage(total, receipt) == total
    bb = Blackboard()
    for _ in range(2):
        bb.record_usage("capo", 3, "test/model", total, api_calls=1)
    assert len(bb.usage_log) == 1 and bb.usage_log[0]["cost_usd"] == 0.03


def test_trade_idea_review_checkpoint_is_bound_to_exact_model_generation(monkeypatch):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    monkeypatch.setattr(base.Specialist, "_build_round_context", lambda self, rnd: "review " +
                        self.blackboard.valuation_results["TEST"]["generation_id"])
    monkeypatch.setattr(base.Specialist, "_build_tools_schema", lambda self: [])
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", SimpleNamespace(
        current_facts_block=lambda: ""), raising=False)
    bb = Blackboard()
    bb.run_scope, bb.target_ticker = "trade_idea", "TEST"
    bb.budget_gate = SimpleNamespace(catalog_snapshot=__import__('_trade_idea_contratto').contratto_default(), wrap_client=lambda client, **scope: client)
    bb.persist_run_checkpoint = lambda event, payload: None
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        generation = bb.valuation_results["TEST"]["generation_id"]
        return SimpleNamespace(usage=u(), stop_reason="end_turn", content=[SimpleNamespace(
            type="text", text=("Verified review for " + generation + ". ") * 60)])
    for generation in ("generation-one", "generation-two"):
        bb.valuation_results["TEST"] = {"snapshot_id": "frozen-source", "generation_id": generation,
                                        "workbook_sha256": generation + "-workbook"}
        for _ in range(2):
            specialist = base.Specialist(bb, client=SimpleNamespace(messages=SimpleNamespace(create=create)))
            assert generation in specialist.run(2)
            assert specialist.run_result_status == "complete"
    assert len(calls) == 2 and len(bb.specialist_checkpoints) == 2
    assert {item["initial_context"].strip() for item in bb.specialist_checkpoints.values()} == {
        "review generation-one", "review generation-two"}


def test_parallel_desk_failure_blocks_new_tool_dispatch_preserving_first_cause(monkeypatch):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    monkeypatch.setattr(base.Specialist, "_build_round_context", lambda self, rnd: "frozen task")
    monkeypatch.setattr(base.Specialist, "_build_tools_schema", lambda self: [])
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", SimpleNamespace(
        current_facts_block=lambda: "", favorites_block=lambda: "", pm_theses_block=lambda: ""), raising=False)
    cause = RuntimeError("original failure in another desk")
    blocked = False
    def guard():
        if blocked:
            raise cause
    calls, tools = [], []
    def create(**kwargs):
        nonlocal blocked
        calls.append(kwargs)
        blocked = True
        return SimpleNamespace(usage=u(), stop_reason="tool_use", content=[SimpleNamespace(
            type="tool_use", name="paid_lookup", input={}, id="tool-one")])
    monkeypatch.setattr(base.Specialist, "_execute_meta_tool", lambda *args: tools.append(args))
    bb = Blackboard()
    bb.raise_if_run_blocked = guard
    specialist = base.Specialist(bb, client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    with pytest.raises(RuntimeError) as caught:
        specialist.run(1)
    assert caught.value is cause and len(calls) == 1 and tools == []
    assert specialist.run_result_status == "failed"
