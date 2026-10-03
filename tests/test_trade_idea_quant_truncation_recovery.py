"""Private source-copy proof, native adapter with isolated HTTP and heartbeat."""
from copy import deepcopy
import json

import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.agents.specialists import base
from test_trade_idea_live_consultation import board, adapter, tool_call
import test_trade_idea_consultation_recovery as fixtures


class NativeDesk(base.Specialist):
    name = "quant"
    system_prompt = "Answer only the explicit cash-flow question from this draft."
    def _build_round_context(self, round_n):
        return "Isolated consultation of the explicit candidate draft."


class NativeFundamentals(NativeDesk):
    name = "fundamentals"
    _build_round_context = base.Specialist._build_round_context


def acknowledge(board):
    expected = [row for row in trade._current_model_consultations(board)
                if row["status"] == "complete" and row["author_view_complete"]]
    def script(_n, body):
        delivered = json.dumps(body["messages"], ensure_ascii=False)
        for row in expected:
            assert row["response"] in delivered and row["id"] in delivered
            assert trade._plan_digest(row) in delivered
        return {"content": "Fundamentals received the actual peer evidence and dissent. " * 12}, "stop"
    client, requests = adapter(script)
    NativeFundamentals(board, client=client).run(1, task_context={"kind": "model_build"}, publish_report=False)
    assert len(requests) == 1


def bind_peers(monkeypatch):
    from bellomberg.agents import consigliere_multi
    classes, requests_by_desk = [], {}
    for desk in ("quant", "options"):
        def script(n, _body, *, _desk=desk):
            if n % 2:
                return {"content": None, "tool_calls": [tool_call("get_fundamentals")]}, "tool_calls"
            if _desk == "quant" and n == 2:
                return {"content": "Actual partial cash-flow analysis; quantitative consultation remains unfinished. " * 12}, "length"
            return {"content": "Complete answer: distinguish the terminal accounting identity from economic stress. " * 12}, "stop"
        client, requests = adapter(script)
        def init(self, blackboard, *, _client=client):
            NativeDesk.__init__(self, blackboard, client=_client)
        classes.append(type("Native" + desk, (NativeDesk,), {"name": desk, "__init__": init}))
        requests_by_desk[desk] = requests
    monkeypatch.setattr(consigliere_multi, "SPECIALIST_ORDER", classes)
    return requests_by_desk


def recover(board, desk, predecessor, *, question=None):
    link = {"consultation_id": predecessor["id"],
            "consultation_row_sha256": trade._plan_digest(predecessor),
            "rationale": "The preserved partial needs a focused economic completion after received peer evidence.",
            "after_consultation_ids": ["consultation-" + peer for peer in fixtures.PEERS[:3]]}
    return trade._consult_model_desk(board, "fundamentals", desk,
        question or "Given the received Macro/Event/Crypto evidence, separate terminal FCFF arithmetic from RONIC below WACC economic stress.",
        {"economic_driver": "same explicit values, now discussed against received peer mechanisms"},
        ["source-document"], supersedes_failed=link)


def truncated_chain(board, monkeypatch):
    original = deepcopy(fixtures.install_rows(board, failed=("quant", "options")))
    requests = bind_peers(monkeypatch)
    acknowledge(board)
    first = recover(board, "quant", board.data["_model_consultations"][3],
                    question="Given the three received peer answers, assess the downside cash-flow transmission quantitatively.")
    assert first["ok"] is False
    partial = board.data["_model_consultations"][-1]
    assert partial["status"] == "truncated"
    assert recover(board, "options", board.data["_model_consultations"][4])["ok"]
    return original, partial, requests


def test_native_terminal_snapshot_captures_known_actual_response(board, monkeypatch):
    _, partial, requests = truncated_chain(board, monkeypatch)
    terminal = partial["terminal_response"]
    assert terminal["stop_reason"] == "max_tokens"
    assert terminal["response_id"] == "synthetic-response-2"
    assert terminal["report_sha256"] == trade._plan_digest(partial["response"])
    assert terminal["model"] == requests["quant"][1]["model"]
    assert terminal["usage"]["cost_usd"] == 0.00001
    assert terminal["run_usage"]["cost_usd"] == 0.00002
    assert terminal["usage"]["output_tokens"] == 5
    assert terminal["run_usage"]["out"] == 10
    assert trade.MODEL_CONSULTATION_LIMIT == 10  # actual frozen native limit, unchanged


def test_failed_truncated_complete_chain_eight_raw_five_current_and_real_read_required(board, monkeypatch, tmp_path):
    original, partial, requests = truncated_chain(board, monkeypatch)
    saved_partial = deepcopy(partial)
    calls = fixtures.compile_spy(board, monkeypatch, tmp_path)
    assert board.build_candidate_model({"ticker": "TEST"})["ok"] is False and not calls
    result = recover(board, "quant", partial)
    assert result["ok"] is True, result
    rows = board.data["_model_consultations"]
    assert len(rows) == 8 and rows[:5] == original and rows[5] == saved_partial
    assert len(trade._current_model_consultations(board)) == 5
    assert board.build_candidate_model({"ticker": "TEST"})["ok"] is False and not calls
    acknowledge(board)
    for row in trade._current_model_consultations(board):
        row["fundamentals_decision"] = fixtures.decision(row, value="disagreed" if row["desk"] == "crypto" else "incorporated")
    assert board.build_candidate_model({"ticker": "TEST"})["ok"] is True and calls == ["compile"]
    assert [row["status"] for row in rows if row["desk"] == "quant"] == ["failed", "truncated", "complete"]
    assert len(requests["quant"]) == 4 and len(requests["options"]) == 2


@pytest.mark.parametrize("fault", ["missing_terminal", "nonterminal", "missing_id", "wrong_report", "empty_report",
    "unknown_cost", "nonfinite_cost", "bool_token", "missing_token", "unknown_run_cost", "partial_run_tokens",
    "aggregate_mismatch", "other_status", "source_changed", "unreceived_peers", "same_question"])
def test_invalid_truncated_predecessor_rejected_before_dispatch(board, monkeypatch, fault):
    _, partial, requests = truncated_chain(board, monkeypatch)
    if fault == "missing_terminal": partial.pop("terminal_response", None)
    elif fault == "empty_report": partial["response"] = " "
    elif fault == "other_status": partial["status"] = "started"
    elif fault == "source_changed": partial["source_fingerprint"] = "changed-source"
    elif fault == "unreceived_peers": board.data["_fundamentals_received_consultations"] = {}
    elif fault != "same_question":
        terminal = partial["terminal_response"]
        if fault == "nonterminal": terminal["stop_reason"] = "tool_use"
        elif fault == "missing_id": terminal["response_id"] = None
        elif fault == "wrong_report": terminal["report_sha256"] = "changed-partial"
        elif fault == "unknown_cost": terminal["usage"]["cost_usd"] = None
        elif fault == "nonfinite_cost": terminal["usage"]["cost_usd"] = "NaN"
        elif fault == "bool_token": terminal["usage"]["output_tokens"] = True
        elif fault == "missing_token": terminal["usage"]["input_tokens"] = None
        elif fault == "unknown_run_cost": terminal["run_usage"]["cost_usd"] = None
        elif fault == "partial_run_tokens": terminal["run_usage"]["tokens_status"] = "parziale"
        elif fault == "aggregate_mismatch": terminal["run_usage"]["out"] += 1
    before = deepcopy(board.data["_model_consultations"])
    result = recover(board, "quant", partial,
                     question=partial["question"] + " nonce=ignored" if fault == "same_question" else None)
    assert result["ok"] is False
    assert len(requests["quant"]) == 2 and board.data["_model_consultations"] == before


def test_native_unknown_usage_does_not_create_recoverable_terminal(board):
    client, _ = adapter(lambda *_a: ({"content": "Preserved but usage-unknown partial text."}, "length"), missing_usage=True)
    sp = NativeDesk(board, client=client)
    sp.run(1, task_context={"consultation": True}, publish_report=False)
    assert sp.consultation_result_status == "truncated"
    assert sp.consultation_terminal_response is None


def test_native_empty_max_tokens_placeholder_is_not_actual_partial_provenance(board):
    client, _ = adapter(lambda n, _body: (
        {"content": None, "tool_calls": [tool_call("get_fundamentals")]}, "tool_calls")
        if n == 1 else ({"content": ""}, "length"))
    sp = NativeDesk(board, client=client)
    answer = sp.run(1, task_context={"consultation": True}, publish_report=False)
    assert "No output produced" in answer
    assert sp.consultation_result_status == "truncated"
    assert sp.consultation_terminal_response is None
