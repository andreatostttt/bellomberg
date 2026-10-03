"""Consultation recovery uses real native turns; HTTP and compilation are isolated."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists.base import Specialist
from test_trade_idea_live_consultation import board, adapter, tool_call, Desk


PEERS = ("macro", "eventdesk", "crypto", "quant", "options")


def install_rows(board, *, failed=()):
    board.source_qualification = {"fingerprint": "qualified-source", "source_report": {
        "documents": [{"id": "source-document"}]}}
    board.budget_gate = SimpleNamespace(wrap_client=lambda client, role: client,
        run_id="isolated-consultation", store=SimpleNamespace(get_run=lambda _id: {
            "run": {"authorization": {"activities": ["model_preparation"]}}}))
    board.data["_model_input_draft"] = {"model": {}, "scenarios": {}}
    board.r2_specialists = set(trade_idea.TRADE_IDEA_DESKS)
    rows = []
    for desk in PEERS:
        draft = {"economic_driver": "original explicit " + desk + " assumption"}
        rows.append({"id": "consultation-" + desk, "requester": "fundamentals", "desk": desk,
            "question": "How does the original " + desk + " risk affect cash flows?",
            "draft_assumptions": draft, "draft_sha256": trade_idea._plan_digest(draft),
            "source_fingerprint": "qualified-source", "evidence_refs": ["source-document"],
            "status": "failed" if desk in failed else "complete",
            "response": "[ERROR: original failed request]" if desk in failed else "Actual " + desk + " answer.",
            "usage": [{"cost_usd": None if desk in failed else 0.00001}],
            "error": "preserved unknown request" if desk in failed else None,
            "author_view_complete": desk not in failed})
    board.data["_model_consultations"] = rows
    return rows


def fundamentals(board, script, **kwargs):
    client, requests = adapter(script, **kwargs)
    sp = Desk(board, client=client)
    sp.name = "fundamentals"
    return sp, requests


def acknowledge(board):
    sp, requests = fundamentals(board, lambda *_a: ({"content": "Received the existing complete peer answers. " * 20}, "stop"))
    sp.run(1, task_context={"kind": "model_build"}, publish_report=False)
    assert len(requests) == 1


def decision(row, *, value="incorporated"):
    return {"consultation_id": row["id"], "decision": value,
            "rationale": "Explicit Fundamentals decision preserving the peer's economic argument."}


def compile_spy(board, monkeypatch, tmp_path):
    calls = []
    reference = {"snapshot_id": "fixture-snapshot", "generation_id": "fixture-generation",
                 "workbook_sha256": "a" * 64, "valuation_date": "2026-10-01"}
    monkeypatch.setattr(trade_idea, "_verified_candidate_valuations", lambda _b:
        [reference] if _b.valuation_results.get(_b.target_ticker) else [])
    def compile_model(current):
        calls.append("compile")
        current.valuation_results[current.target_ticker] = {**reference, "ticker": current.target_ticker}
    board.build_candidate_model = lambda inputs: trade_idea._build_fundamentals_candidate(
        board, inputs, tmp_path, evaluator=compile_model)
    return calls


def test_same_batch_last_page_then_compile_waits_for_received_followup(board, monkeypatch, tmp_path):
    rows = install_rows(board)
    for row in rows:
        row["fundamentals_decision"] = decision(row)
    macro = rows[0]
    macro.update(author_view_complete=False, answer_read_offset=0)
    calls = compile_spy(board, monkeypatch, tmp_path)
    def script(n, body):
        if n == 1:
            return {"content": None, "tool_calls": [
                tool_call("read_candidate_model_consultation", {"consultation_id": macro["id"], "offset": 0}),
                tool_call("get_valuation")]}, "tool_calls"
        first_results = board.tool_receipts
        assert json.loads(first_results[0]["output"])["complete"] is True
        assert json.loads(first_results[1]["output"])["ok"] is False
        assert calls == []
        if n == 2:
            return {"content": None, "tool_calls": [tool_call("get_valuation")]}, "tool_calls"
        return {"content": "The following received Fundamentals turn compiled the explicit model. " * 10}, "stop"
    sp, requests = fundamentals(board, script)
    sp.run(1, task_context={"kind": "model_build"}, publish_report=False)
    assert json.loads(board.tool_receipts[1]["output"])["ok"] is False
    assert len(requests) == 3 and calls == ["compile"]
    receipt = board.data["_fundamentals_received_consultations"][macro["id"]]
    assert receipt["response_id"] == "synthetic-response-2"
    assert receipt["answer_sha256"] == trade_idea._plan_digest(macro["response"])


@pytest.mark.parametrize("failure", ["502", "402", "missing_usage"])
def test_no_received_followup_does_not_attest_last_page(board, failure):
    row = install_rows(board)[0]
    row.update(author_view_complete=False, answer_read_offset=0)
    def script(n, _body):
        if n == 1:
            return {"content": None, "tool_calls": [tool_call("read_candidate_model_consultation", {
                "consultation_id": row["id"], "offset": 0})]}, "tool_calls"
        if failure != "missing_usage":
            raise RuntimeError("isolated HTTP " + failure)
        return {"content": "Received response without known usage; no receipt may certify a read. " * 10}, "stop"
    sp, requests = fundamentals(board, script, missing_usage=failure == "missing_usage")
    sp.run(1, task_context={"kind": "model_build"}, publish_report=False)
    if failure == "missing_usage":
        assert len(requests) == 1 and row["author_view_complete"] is False
    else:
        assert len(requests) == 2 and row["author_view_complete"] is True
    assert row["id"] not in board.data.get("_fundamentals_received_consultations", {})


def bind_native_peers(board, monkeypatch):
    from bellomberg.agents import consigliere_multi
    seen = []
    classes = []
    for desk in ("quant", "options", "macro"):
        client, requests = adapter(lambda n, _body: (
            {"content": None, "tool_calls": [tool_call("get_fundamentals")]}, "tool_calls")
            if n == 1 else ({"content": "Independent sourced answer to this new economic question. " * 12}, "stop"))
        def init(self, blackboard, *, _client=client):
            Desk.__init__(self, blackboard, client=_client)
        cls = type("Native" + desk.title(), (Desk,), {"name": desk, "__init__": init})
        classes.append(cls)
        seen.append((desk, requests))
    monkeypatch.setattr(consigliere_multi, "SPECIALIST_ORDER", classes)
    return seen


def recovery_input(board, desk):
    row = next(row for row in board.data["_model_consultations"] if row["desk"] == desk)
    return {"consultation_id": row["id"], "consultation_row_sha256": trade_idea._plan_digest(row),
            "rationale": "The now received Macro/Event/Crypto analysis changes this economic question.",
            "after_consultation_ids": ["consultation-" + peer for peer in PEERS[:3]]}


def recover(board, desk, link):
    return trade_idea._consult_model_desk(board, "fundamentals", desk,
        "Given the received Macro/Event/Crypto mechanisms, quantify the revised " + desk + " downside cash-flow transmission?",
        {"economic_driver": "new explicit post-peer " + desk + " assumption"}, ["source-document"],
        supersedes_failed=link)


def valid_recovery(board, monkeypatch):
    rows = install_rows(board, failed=("quant", "options"))
    original = deepcopy(rows)
    seen = bind_native_peers(board, monkeypatch)
    acknowledge(board)
    for desk in ("quant", "options"):
        result = recover(board, desk, recovery_input(board, desk))
        assert result["ok"] is True, (result.get("error"), result.get("consultation", {}).get("status"),
            result.get("consultation", {}).get("error"), board.data["_model_consultations"][-1].get("usage"))
    acknowledge(board)
    return original, seen


def test_seven_raw_rows_five_current_heads_preserve_audit_and_dissent(board, monkeypatch, tmp_path):
    original, seen = valid_recovery(board, monkeypatch)
    heads = trade_idea._current_model_consultations(board)
    assert len(heads) == 5 and len(board.data["_model_consultations"]) == 7
    assert board.data["_model_consultations"][:5] == original
    for row in heads:
        row["fundamentals_decision"] = decision(row, value="disagreed" if row["desk"] == "crypto" else "incorporated")
    calls = compile_spy(board, monkeypatch, tmp_path)
    assert board.build_candidate_model({"ticker": "TEST"})["ok"] and calls == ["compile"]
    assert [len(requests) for desk, requests in seen] == [2, 2, 0]
    quant_body = seen[0][1][0]
    assert "revised quant downside" in json.dumps(quant_body)
    assert "supersedes_failed" in json.dumps(quant_body) and "synthetic-response-1" in json.dumps(quant_body)
    restored = SimpleNamespace(data=deepcopy(board.data), source_qualification=deepcopy(board.source_qualification),
                              tool_receipts=deepcopy(board.tool_receipts))
    assert [row["id"] for row in trade_idea._current_model_consultations(restored)] == [row["id"] for row in heads]


@pytest.mark.parametrize("fault", ["hash", "id", "desk", "requester", "fingerprint", "draft", "evidence", "unread", "unreceived", "completed", "truncated", "empty_rationale", "missing_context", "unknown_field"])
def test_invalid_recovery_blocks_before_native_dispatch_and_preserves_rows(board, monkeypatch, fault):
    rows = install_rows(board, failed=("quant", "options"))
    seen = bind_native_peers(board, monkeypatch)
    acknowledge(board)
    old = rows[3]
    if fault in ("desk", "requester", "fingerprint", "draft", "evidence", "completed", "truncated"):
        if fault == "draft": old["draft_sha256"] = "altered"
        elif fault == "evidence": old["evidence_refs"] = ["unretrieved-document"]
        elif fault in ("completed", "truncated"): old["status"] = "complete" if fault == "completed" else "truncated"
        else: old[{"desk": "desk", "requester": "requester", "fingerprint": "source_fingerprint"}[fault]] = "different"
    link = recovery_input(board, "quant" if fault != "desk" else "different")
    if fault == "hash": link["consultation_row_sha256"] = "altered"
    if fault == "id": link["consultation_id"] = "absent"
    if fault == "unread": rows[0]["author_view_complete"] = False
    if fault == "unreceived": board.data.pop("_fundamentals_received_consultations", None)
    if fault == "empty_rationale": link["rationale"] = " "
    if fault == "missing_context": link["after_consultation_ids"] = []
    if fault == "unknown_field": link["untrusted_received"] = True
    before = deepcopy(rows)
    assert recover(board, "quant", link)["ok"] is False
    assert rows == before and all(not requests for _, requests in seen)


def test_completed_peer_cannot_be_replaced_and_duplicate_requires_link(board, monkeypatch):
    rows = install_rows(board, failed=("quant",))
    seen = bind_native_peers(board, monkeypatch)
    for desk in ("macro", "quant"):
        out = trade_idea._consult_model_desk(board, "fundamentals", desk, "A second question?",
            {"explicit": "driver"}, ["source-document"])
        assert out["ok"] is False
    assert len(rows) == 5 and all(not requests for _, requests in seen)


@pytest.mark.parametrize("fault", ["duplicate", "fork", "missing_parent", "cycle", "parent_hash", "changed_context", "failed_head", "truncated_head", "old_decision", "empty_decision", "invented_decision", "decision_id", "unreceived_head"])
def test_chain_and_final_head_guards_fail_closed(board, monkeypatch, tmp_path, fault):
    original, _ = valid_recovery(board, monkeypatch)
    rows = board.data["_model_consultations"]
    for row in trade_idea._current_model_consultations(board): row["fundamentals_decision"] = decision(row)
    if fault == "duplicate": rows.append({**deepcopy(rows[0]), "id": "duplicate-macro"})
    elif fault == "fork": rows.append({**deepcopy(rows[5]), "id": "second-quant-successor"})
    elif fault in ("missing_parent", "cycle", "parent_hash"):
        rows[5]["supersedes_failed"]["consultation_row_sha256" if fault == "parent_hash" else "consultation_id"] = (
            "bad-hash" if fault == "parent_hash" else rows[5]["id"] if fault == "cycle" else "absent")
    elif fault == "changed_context": rows[0]["response"] += " altered answer"
    elif fault in ("failed_head", "truncated_head"): rows[5]["status"] = "failed" if fault == "failed_head" else "truncated"
    elif fault == "old_decision": rows[5].pop("fundamentals_decision"); rows[3]["fundamentals_decision"] = decision(rows[3])
    elif fault == "empty_decision": rows[5]["fundamentals_decision"]["rationale"] = " "
    elif fault == "invented_decision": rows[5]["fundamentals_decision"]["decision"] = "approved"
    elif fault == "decision_id": rows[5]["fundamentals_decision"]["consultation_id"] = rows[3]["id"]
    elif fault == "unreceived_head": board.data["_fundamentals_received_consultations"].pop(rows[5]["id"])
    calls = compile_spy(board, monkeypatch, tmp_path)
    assert board.build_candidate_model({"ticker": "TEST"})["ok"] is False and calls == []


def test_uuid_only_new_question_is_not_a_new_economic_intent(board, monkeypatch):
    rows = install_rows(board, failed=("quant",))
    seen = bind_native_peers(board, monkeypatch)
    acknowledge(board)
    out = trade_idea._consult_model_desk(board, "fundamentals", "quant", rows[3]["question"] + " 01234567-89ab-cdef-0123-456789abcdef",
        rows[3]["draft_assumptions"], ["source-document"], supersedes_failed=recovery_input(board, "quant"))
    assert out["ok"] is False and len(rows) == 5 and all(not requests for _, requests in seen)


def test_same_batch_final_page_and_recovery_do_not_dispatch_peer(board, monkeypatch):
    rows = install_rows(board, failed=("quant", "options"))
    rows[0].update(author_view_complete=False, answer_read_offset=0)
    seen = bind_native_peers(board, monkeypatch)
    board.consult_specialist = lambda **kwargs: trade_idea._consult_model_desk(board, **kwargs)
    link = recovery_input(board, "quant")
    def script(n, body):
        if n == 1:
            return {"content": None, "tool_calls": [
                tool_call("read_candidate_model_consultation", {"consultation_id": rows[0]["id"], "offset": 0}),
                tool_call("ask_specialist", {"specialist": "quant", "question": "Post-peer cash-flow downside transmission?",
                    "draft_assumptions": {"new": "actual economic assumption"}, "evidence_refs": ["source-document"],
                    "supersedes_failed": link})]}, "tool_calls"
        assert len(board.data["_model_consultations"]) == 5
        assert all(not requests for _, requests in seen)
        return {"content": "The new question was refused until a real follow-up received the last peer page. " * 10}, "stop"
    sp, requests = fundamentals(board, script)
    sp.run(1, task_context={"kind": "model_build"}, publish_report=False)
    assert len(requests) == 2


def test_same_batch_read_and_plan_decision_waits_for_real_received_turn(board, monkeypatch):
    rows = install_rows(board)
    rows[0].update(author_view_complete=False, answer_read_offset=0)
    monkeypatch.setattr(trade_idea, "_candidate_input_contract", lambda _b: {"schema": {}, "scenarios": []})
    submit = {"rationale": "The explicit economic decision follows the received peer argument.",
              "consultation_decisions": [decision(rows[0])]}
    def script(n, _body):
        if n == 1:
            return {"content": None, "tool_calls": [
                tool_call("read_candidate_model_consultation", {"consultation_id": rows[0]["id"], "offset": 0}),
                tool_call("submit_candidate_model_plan", submit)]}, "tool_calls"
        if n == 2:
            assert json.loads(board.tool_receipts[1]["output"])["ok"] is False
            assert not board.data["_model_consultations"][0].get("fundamentals_decision")
            return {"content": None, "tool_calls": [tool_call("submit_candidate_model_plan", submit)]}, "tool_calls"
        return {"content": "The later received turn explicitly adopted this peer argument. " * 12}, "stop"
    sp, requests = fundamentals(board, script)
    sp.run(1, task_context={"kind": "model_build"}, publish_report=False)
    assert len(requests) == 3
    assert json.loads(board.tool_receipts[2]["output"])["ok"] is True
    assert board.data["_model_consultations"][0]["fundamentals_decision"] == decision(rows[0])


@pytest.mark.parametrize("fault", ["old_id", "unreceived", "invented", "empty", "non_object"])
def test_plan_decisions_accept_only_received_current_heads(board, monkeypatch, fault):
    valid_recovery(board, monkeypatch)
    monkeypatch.setattr(trade_idea, "_candidate_input_contract", lambda _b: {"schema": {}, "scenarios": []})
    current = trade_idea._current_model_consultations(board)[3]
    outcome = decision(current)
    if fault == "old_id": outcome["consultation_id"] = "consultation-quant"
    elif fault == "unreceived": board.data["_fundamentals_received_consultations"].pop(current["id"])
    elif fault == "invented": outcome["decision"] = "approved"
    elif fault == "empty": outcome["rationale"] = " "
    else: outcome = "untrusted decision"
    before = deepcopy(board.data)
    result = trade_idea._handle_candidate_plan_tool(board, "fundamentals", "submit_candidate_model_plan", {
        "rationale": "Explicit decision", "consultation_decisions": [outcome]})
    assert result["ok"] is False and board.data == before


def test_checkpoint_and_context_expose_current_hashes_without_fabricating_reads(board, monkeypatch):
    original, _ = valid_recovery(board, monkeypatch)
    context = trade_idea.candidate_model_context(board)
    views = context["consultations"]
    assert len(views) == 7 and sum(row["current"] for row in views) == 5
    assert [row["status"] for row in views[3:5]] == ["failed", "failed"]
    assert all(not row["fundamentals_received"] for row in views[3:5])
    assert views[3]["consultation_row_sha256"] == trade_idea._plan_digest(original[3])
    checkpoints = []
    board.model_checkpoint_writer = lambda progress: checkpoints.append(progress["model_build_checkpoint"])
    trade_idea._persist_model_build(board)
    checkpoint = checkpoints[-1]
    assert checkpoint["consultations"] == board.data["_model_consultations"]
    assert checkpoint["fundamentals_received_consultations"] == board.data["_fundamentals_received_consultations"]
    assert checkpoint["consultations"][3:5] == original[3:5]


def test_untrusted_model_read_flag_never_attests_a_page(board, monkeypatch):
    row = install_rows(board)[0]
    monkeypatch.setattr(trade_idea, "_candidate_input_contract", lambda _b: {"schema": {}, "scenarios": []})
    out = trade_idea._handle_candidate_plan_tool(board, "fundamentals", "submit_candidate_model_plan", {
        "rationale": "The model claims to have read the answer.", "fundamentals_received": True,
        "consultation_decisions": [decision(row)]})
    assert out["ok"] is False and not board.data.get("_fundamentals_received_consultations")


def test_native_fundamentals_context_delivers_three_peer_answers_before_receipt(board):
    rows = install_rows(board, failed=("quant", "options"))
    expected = deepcopy(rows[:3])
    def script(_n, body):
        assert not board.data.get("_fundamentals_received_consultations")
        delivered = json.dumps(body["messages"], ensure_ascii=False)
        for row in expected:
            assert row["id"] in delivered
            assert row["response"] in delivered
            assert trade_idea._plan_digest(row) in delivered
            assert row["source_fingerprint"] in delivered
        return {"content": "Fundamentals received all three actual peer answers in the native model-building context. " * 10}, "stop"
    client, requests = adapter(script)
    class NativeFundamentals(Desk):
        name = "fundamentals"
        _build_round_context = Specialist._build_round_context
    sp = NativeFundamentals(board, client=client)
    sp.run(1, task_context={"kind": "model_build"}, publish_report=False)
    assert len(requests) == 1 and sp.run_result_status == "complete"
    receipts = board.data["_fundamentals_received_consultations"]
    assert set(receipts) == {row["id"] for row in expected}
    for row in expected:
        assert receipts[row["id"]]["answer_sha256"] == trade_idea._plan_digest(row["response"])
        assert receipts[row["id"]]["response_id"] == "synthetic-response-1"
