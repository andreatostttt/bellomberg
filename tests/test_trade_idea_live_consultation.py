"""Targeted consultation through the real native adapter, HTTP transport isolated."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from bellomberg.agents import chat_tools, trade_idea
from bellomberg.agents.specialists import base as specialist_base
from bellomberg.agents.specialists.base import Blackboard, Specialist, MAX_TOKENS_SPECIALIST
from bellomberg.core import current_facts, llm_pricing, mandato_pm
from bellomberg.core.llm_client import OpenRouterClient


class Desk(Specialist):
    name = "macro"
    system_prompt = "Answer the selected candidate question."

    def _build_round_context(self, round_n):
        return "Offline research context for the exact candidate."


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "")
    def missing_mandate():
        raise mandato_pm.MandatoMancante("isolated-unit-fixture", "assente")
    monkeypatch.setattr(mandato_pm, "carica", missing_mandate)
    monkeypatch.setattr(llm_pricing, "cost_eur", lambda *_a, **_k: {
        "cost": None, "fx_rate": None, "fx_source": "unavailable in unit test",
        "status": "pricing_unavailable"})
    b = Blackboard(memory_db=None, heartbeat_path=tmp_path / "heartbeat.json",
        run_scope="trade_idea", run_id="isolated-consultation", target_ticker="TEST",
        budget_gate=SimpleNamespace(wrap_client=lambda client, role: client))
    b.current_round = 1
    b.independent_round = 0
    b.model_phase = "building"
    b.write("macro", 1, "Existing official report")
    b.mark_specialist_done("macro")
    monkeypatch.setattr(chat_tools, "dispatch", lambda name, inputs, **kw: {
        "ok": True, "data": {"ticker": "TEST", "answer": "isolated observed tool result"},
        "_source": "unit-fixture: " + name, "_timestamp": "2026-09-30T00:00:00Z"})
    return b


def adapter(script, *, missing_usage=False):
    requests = []
    def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        message, finish = script(len(requests), body)
        return httpx.Response(200, json={"id": "synthetic-response-" + str(len(requests)),
            "model": body["model"], "provider": "synthetic-unit-transport",
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": None if missing_usage else {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.00001,
                "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0}}})
    client = OpenRouterClient(api_key="synthetic-unit-key", max_retries=0,
        trasporto=httpx.MockTransport(transport))
    return client, requests


def tool_call(name, inputs=None):
    return {"id": "tool-" + name, "type": "function", "function": {
        "name": name, "arguments": json.dumps(inputs or {"ticker": "TEST"})}}


def task():
    return {"consultation": True, "requester": "fundamentals",
        "question": "Which exact cash-flow driver changes under this regime?",
        "draft_assumptions": {"driver": "explicit synthetic draft"}, "evidence_refs": []}


def test_targeted_task_reaches_native_client_and_preserves_reports_usage_receipts(board):
    client, requests = adapter(lambda n, body: (
        {"content": None, "tool_calls": [tool_call("get_fundamentals")]}, "tool_calls")
        if n == 1 else ({"content": "Answer to the exact cash-flow question from retrieved evidence."}, "stop"))
    sp = Desk(board, client=client)
    before = deepcopy(board.data)
    before_status = deepcopy(board.specialist_status)
    answer = sp.run(1, task_context=task(), publish_report=False)
    assert task()["question"] in json.dumps(requests[0]["messages"])
    assert "explicit synthetic draft" in json.dumps(requests[0]["messages"])
    assert requests[0]["max_tokens"] == MAX_TOKENS_SPECIALIST
    assert requests[0]["model"] == sp._model_for_round(1)
    assert requests[0]["reasoning"] == {"effort": "max"}
    assert client.max_retries == 0
    assert "exact cash-flow question" in answer
    assert sp.consultation_result_status == "complete"
    assert sp.run_result_status == "complete"
    assert board.data == before and board.specialist_status == before_status
    assert len(board.usage_log) == 1 and board.usage_log[0]["api_calls"] == 2
    assert len(board.tool_receipts) == 1 and board.tool_receipts[0]["tool"] == "get_fundamentals"
    assert sp._task_context is None


@pytest.mark.parametrize("stop", ["length", "content_filter"])
def test_incomplete_native_answer_is_not_complete_and_does_not_publish(board, stop):
    client, _ = adapter(lambda *_a: ({"content": "Synthetic partial answer"}, stop))
    sp = Desk(board, client=client)
    sp.run(1, task_context=task(), publish_report=False)
    assert sp.consultation_result_status == ("truncated" if stop == "length" else "failed")
    assert sp.run_result_status == sp.consultation_result_status
    assert board.read("macro", 1) == "Existing official report"


def test_api_error_keeps_official_report_and_records_failure_usage(board):
    client, requests = adapter(lambda *_a: (_ for _ in ()).throw(RuntimeError("isolated transport failed")))
    sp = Desk(board, client=client)
    answer = sp.run(1, task_context=task(), publish_report=False)
    assert answer.startswith("[ERROR") and len(requests) == 1
    assert sp.consultation_result_status == "failed"
    assert sp.run_result_status == "failed"
    assert board.read("macro", 1) == "Existing official report"
    assert board.usage_log[-1]["status"] == "api_error"


def test_consultation_blocks_recursive_questions_and_model_mutation(board, monkeypatch):
    called = []
    board.consult_specialist = lambda **kw: called.append(kw)
    board.build_candidate_model = lambda inputs: called.append(inputs)
    monkeypatch.setattr(trade_idea, "handle_trade_idea_review_tool", lambda *args: called.append(args))
    names = ["ask_specialist", "review_candidate_model", "submit_candidate_model_plan",
        "respond_trade_idea_objection", "get_valuation"]
    client, requests = adapter(lambda n, body: (
        {"content": None, "tool_calls": [tool_call(name, {"ticker": "TEST", "method_records": []}) for name in names]},
        "tool_calls") if n == 1 else ({"content": "Targeted reply with blocked mutation declared."}, "stop"))
    sp = Desk(board, client=client)
    sp.name = "fundamentals"
    sp.run(1, task_context=task(), publish_report=False)
    assert called == []
    assert len(board.tool_receipts) == len(names)
    assert all(not row["success"] for row in board.tool_receipts)
    assert all("blocked_consultation" in row["output"] for row in board.tool_receipts)
    assert "ask_specialist" not in {t["function"]["name"] for t in requests[0]["tools"]}
    assert board.read("macro", 1) == "Existing official report"


def test_r1_fundamentals_invokes_real_consultation_seam(board):
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    sp.name = "fundamentals"
    seen = []
    board.consult_specialist = lambda **kwargs: seen.append(kwargs) or {"ok": True, "answer": "fresh peer answer"}
    inputs = {"specialist": "macro", "question": "Targeted question",
        "draft_assumptions": {"driver": "draft"}, "evidence_refs": ["document-a"]}
    assert sp._execute_meta_tool("ask_specialist", inputs)["answer"] == "fresh peer answer"
    assert seen == [{"requester": "fundamentals", "target": "macro", "question": "Targeted question",
        "draft_assumptions": {"driver": "draft"}, "evidence_refs": ["document-a"]}]
    assert board.read("macro", 1) == "Existing official report"


def test_r0_blocks_both_tools_and_r2_reads_without_callback(board):
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    sp.name = "fundamentals"
    seen = []
    board.consult_specialist = lambda **kwargs: seen.append(kwargs)
    board.current_round = 0
    for name in ("ask_specialist", "read_blackboard"):
        assert sp._execute_meta_tool(name, {"specialist": "macro"})["ok"] is False
    board.current_round = 2
    assert sp._execute_meta_tool("ask_specialist", {"specialist": "macro", "question": "read"})["report"] == "Existing official report"
    assert seen == []


def test_generic_independent_first_analysis_is_still_hidden(board):
    board.independent_round = 1
    board.model_phase = "research"
    assert board.summary_for_specialist("fundamentals") == {}
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    assert sp._execute_meta_tool("ask_specialist", {"specialist": "macro"})["ok"] is False


def test_only_building_fundamentals_can_create_model_and_missing_seam_is_explicit(board):
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    seen = []
    board.build_candidate_model = lambda inputs: seen.append(inputs) or {"ok": True, "data": {"created": True}}
    assert sp._execute_meta_tool("get_valuation", {"ticker": "TEST"})["ok"] is False
    sp.name = "fundamentals"
    assert sp._execute_meta_tool("get_valuation", {"ticker": "TEST"})["data"]["created"] is True
    assert seen == [{"ticker": "TEST"}]
    del board.build_candidate_model
    assert sp._execute_meta_tool("get_valuation", {"ticker": "TEST"})["ok"] is False


def test_new_model_tools_dispatch_through_existing_review_handler(board, monkeypatch):
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    seen = []
    monkeypatch.setattr(trade_idea, "handle_trade_idea_review_tool", lambda *args: seen.append(args[2]) or {"ok": True})
    for name in ("get_candidate_model_inputs", "submit_candidate_model_plan"):
        assert sp._execute_meta_tool(name, {})["ok"] is True
    assert seen == ["get_candidate_model_inputs", "submit_candidate_model_plan"]


def test_ti_ask_schema_requires_explicit_draft_and_evidence(board):
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    schema = next(tool for tool in sp._build_tools_schema() if tool["name"] == "ask_specialist")
    assert set(schema["input_schema"]["required"]) == {"specialist", "question", "draft_assumptions", "evidence_refs"}
    assert "R1" in schema["description"] and "R2" in schema["description"]


def test_missing_native_usage_is_not_a_complete_consultation(board):
    client, _ = adapter(lambda *_a: ({"content": "Sufficiently detailed synthetic answer. " * 8}, "stop"), missing_usage=True)
    sp = Desk(board, client=client)
    sp.run(1, task_context=task(), publish_report=False)
    assert sp.consultation_result_status == "failed"
    assert board.usage_log[-1]["tokens_status"] == "parziale"
    assert board.read("macro", 1) == "Existing official report"


def test_plain_valuation_during_consultation_never_invokes_builder(board):
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    sp.name = "fundamentals"
    sp._task_context = task()
    board.valuation_results["TEST"] = {"generation_id": "existing-exact-generation"}
    board.build_candidate_model = lambda *_a: pytest.fail("consultation built a model")
    result = sp._execute_meta_tool("get_valuation", {"ticker": "TEST"})
    assert result["data"]["generation_id"] == "existing-exact-generation"


def test_nonfinite_task_fails_before_transport_and_cannot_reuse_complete_status(board):
    client, requests = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    sp.consultation_result_status = "complete"
    context = {**task(), "invalid_driver": float("nan")}
    with pytest.raises(ValueError):
        sp.run(1, task_context=context, publish_report=False)
    assert sp.consultation_result_status == "failed" and requests == []
    assert sp.run_result_status == "failed"


def test_real_root_callback_uses_targeted_native_loop_without_overwriting_desk_report(board, monkeypatch):
    board.source_qualification = {"fingerprint": "b" * 64, "source_report": {"documents": []}}
    client, requests = adapter(lambda n, body: (
        {"content": None, "tool_calls": [tool_call("get_fundamentals")]}, "tool_calls")
        if n == 1 else ({"content": "Fresh targeted macro answer supported by the observed tool receipt."}, "stop"))
    created = []
    def isolated_client(**kwargs):
        created.append(kwargs)
        return client
    monkeypatch.setattr(specialist_base, "OpenRouterClient", isolated_client)
    before_report = board.read("macro", 1)
    before_macro_status = deepcopy(board.specialist_status["macro"])
    context = task()
    result = trade_idea._consult_model_desk(board, "fundamentals", "macro",
        context["question"], context["draft_assumptions"], context["evidence_refs"])
    assert result["ok"] is True and result["consultation"]["status"] == "complete"
    assert len(created) == 1 and created[0]["max_retries"] == 0
    assert len(requests) == 2
    assert context["question"] in json.dumps(requests[0]["messages"])
    assert "explicit synthetic draft" in json.dumps(requests[0]["messages"])
    assert "ask_specialist" not in {t["function"]["name"] for t in requests[0]["tools"]}
    assert board.read("macro", 1) == before_report
    assert board.specialist_status["macro"] == before_macro_status
    actual_audit = board.data["_model_consultations"][-1]
    assert actual_audit["usage"][0]["api_calls"] == 2
    assert actual_audit["tool_receipts"][0]["tool"] == "get_fundamentals"
    assert result["consultation"]["source_fingerprint"] == "b" * 64
    assert board._consultation_active is False


def test_fundamentals_building_context_requires_draft_and_five_live_answers(board):
    client, _ = adapter(lambda *_a: ({"content": "unused"}, "stop"))
    sp = Desk(board, client=client)
    sp.name = "fundamentals"
    context = Specialist._build_round_context(sp, 1)
    assert "not_created" in context and "Exact read-only common model" not in context
    assert "get_candidate_model_inputs" in context and "submit_candidate_model_plan" in context
    assert "compila automaticamente il piano finale" in context and "get_valuation" in context
    assert all(name in context for name in ("macro", "eventdesk", "quant", "options", "crypto"))
    board.current_round = 2
    board.model_phase = "review"
    assert "Exact read-only common model" in Specialist._build_round_context(sp, 2)


@pytest.mark.parametrize("finish,expected", [("stop", "complete"), ("length", "truncated"),
    ("content_filter", "failed")])
def test_final_r2_status_requires_real_native_completion_even_with_visible_text(board, finish, expected):
    board.current_round = 2
    board.model_phase = "review"
    client, requests = adapter(lambda *_a: ({"content": "Visible final review of the exact model. " * 4}, finish))
    sp = Desk(board, client=client)
    sp.run_result_status = "complete"
    answer = sp.run(2)
    assert "Visible final review" in answer and len(requests) == 1
    assert sp.run_result_status == expected
    assert board.read("macro", 2) == answer
    assert board.usage_log[-1]["api_calls"] == 1


def test_regular_trade_idea_error_and_missing_usage_fail_closed(board):
    client, requests = adapter(lambda *_a: (_ for _ in ()).throw(RuntimeError("isolated transport failed")))
    sp = Desk(board, client=client)
    sp.run_result_status = "complete"
    assert sp.run(2).startswith("[ERROR") and len(requests) == 1
    assert sp.run_result_status == "failed"
    client, requests = adapter(lambda *_a: ({"content": "Visible final analysis with unavailable usage."}, "stop"), missing_usage=True)
    sp = Desk(board, client=client)
    sp.run(2)
    assert len(requests) == 1 and sp.run_result_status == "failed"


def test_oversized_root_consultation_requires_complete_bounded_pages_without_provider(board, monkeypatch):
    from bellomberg.agents import consigliere_multi
    board.source_qualification = {"fingerprint": "b" * 64, "source_report": {"documents": []}}
    # This bounded paging test uses a protocol seam, not an economic answer or successful provider run.
    answer = ('Driver evidence:\n"quoted"\\path\t' * 1000)[:20000]
    invoked = []
    class RecordedProtocolDesk:
        name = "macro"
        def __init__(self, blackboard):
            self.blackboard = blackboard
        def run(self, round_n, *, task_context=None, publish_report=True):
            assert round_n == 1 and task_context["consultation"] is True and publish_report is False
            invoked.append(task_context)
            self.consultation_result_status = "complete"
            self.blackboard.usage_log.append({"fixture": "oversized_complete_protocol_seam"})
            return answer
    monkeypatch.setattr(consigliere_multi, "SPECIALIST_ORDER", [RecordedProtocolDesk])
    result = trade_idea._consult_model_desk(board, "fundamentals", "macro", "Read this actual recorded answer",
        {"driver": "explicit draft"}, [])
    row = board.data["_model_consultations"][0]
    assert result["ok"] is False and result["consultation"]["status"] == "answer_requires_paged_read"
    assert "response" not in result["consultation"] and row["author_view_complete"] is False
    assert "usage" not in result["consultation"] and "tool_receipts" not in result["consultation"]
    client, requests = adapter(lambda *_a: pytest.fail("paging must not invoke a provider transport"))
    sp = Desk(board, client=client)
    sp.name = "fundamentals"
    sp._task_context = task()
    assert sp._execute_meta_tool("read_candidate_model_consultation", {
        "consultation_id": row["id"], "offset": 0})["status"] == "blocked_consultation"
    sp._task_context = None
    before_offset = row.get("answer_read_offset", 0)
    assert sp._execute_meta_tool("read_candidate_model_consultation", {
        "consultation_id": row["id"], "offset": 1})["ok"] is False
    assert row.get("answer_read_offset", 0) == before_offset
    recovered, sizes, offset = [], [], 0
    while offset < len(answer):
        page = sp._execute_meta_tool("read_candidate_model_consultation", {
            "consultation_id": row["id"], "offset": offset})
        assert page["ok"] is True and page["offset"] == offset and page["next_offset"] > offset
        wire_result = json.dumps(page, default=str, ensure_ascii=False)
        sizes.append((len(wire_result), len(wire_result.encode("utf-8"))))
        assert len(wire_result) <= specialist_base._tetto_tool_result(), sizes
        recovered.append(page["response"])
        offset = page["next_offset"]
        assert row["author_view_complete"] is (offset == len(answer))
    assert "".join(recovered) == answer and row["author_view_complete"] is True
    assert len(invoked) == 1 and requests == []
    assert board.read("macro", 1) == "Existing official report"
