"""Deterministic request projection; full native history remains untouched."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents.model_authoring_progress import build_model_authoring_progress


MARKER = "\n\nRECORDED MODEL WORK / MISSING AUTHOR ACTIONS:\n"


def ledger(turn):
    board = SimpleNamespace(data={"_model_input_draft": {"model": {
        "calendar": {"value": {"valuation_date": "2025-12-31"}, "evidence_ids": ["frozen-filing"]}}
    }}, source_qualification={"fingerprint": "f" * 64,
        "source_report": {"documents": [{"id": "frozen-filing"}]}})
    return build_model_authoring_progress(board,
        contract={"method_id": "operating_fcff", "schema": {"calendar": ["contract", "model"]},
                  "scenarios": []}, messages=[], remaining_turns=30 - turn)


def progress_text(turn):
    return MARKER + json.dumps(ledger(turn), ensure_ascii=False, sort_keys=True, allow_nan=False)


def history():
    return [
        {"role": "user", "content": "PM constraints, original sources and portfolio unchanged." + progress_text(0)},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Preserved thesis and financial assumptions."},
            {"type": "tool_use", "id": "save-1", "name": "submit_candidate_model_plan", "input": {
                "plan": {"model": {"quotation": {"value": {"price": 42.5, "price_as_of": "2025-12-31"}}}},
                "consultation_decisions": [{"consultation_id": "peer-1", "decision": "incorporated",
                    "rationale": "Keep the exact recorded decision."}]}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "save-1", "content": '{"ok":true,"draft":"exact"}'},
            {"type": "text", "text": progress_text(1)}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "read-1", "name": "read_candidate_source", "input": {
                "document_id": "frozen-filing", "offset": 0}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "read-1", "content": 'Exact primary fact; no summary.'},
            {"type": "text", "text": progress_text(2)}]},
    ]


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def project(messages):
    from bellomberg.agents.model_authoring_context import project_model_authoring_messages
    return project_model_authoring_messages(messages)


def test_projection_replaces_only_nonfinal_informational_ledgers_and_keeps_all_evidence():
    original = history()
    before = deepcopy(original)
    projected, receipt = project(original)
    assert original == before
    assert receipt["contract"] == "model_authoring_context_projection/1"
    assert receipt["original_messages_sha256"] == digest(original)
    assert receipt["projected_messages_sha256"] == digest(projected)
    assert len(receipt["replaced_ledgers"]) == 2
    assert receipt["retained_latest_ledger_sha256"] == digest(ledger(2))
    assert projected[1] == original[1] and projected[3] == original[3]
    assert projected[2]["content"][0] == original[2]["content"][0]
    assert projected[4] == original[4]
    assert projected[0]["content"].startswith("PM constraints, original sources and portfolio unchanged.")
    for item in receipt["replaced_ledgers"]:
        assert item["ledger_sha256"] in json.dumps(projected)
        assert len(item["original_text_sha256"]) == len(item["replacement_sha256"]) == 64
    assert receipt["projected_bytes"] < receipt["original_bytes"]


def test_projection_is_reproducible_after_sorted_checkpoint_restore_and_idempotent():
    original = history()
    first, proof = project(original)
    restored = json.loads(json.dumps(original, sort_keys=True))
    again, same = project(restored)
    assert first == again and proof == same
    twice, repeated = project(first)
    assert twice == first and repeated["replaced_ledgers"] == []


@pytest.mark.parametrize("mutation", ["unknown_version", "unknown_kind", "extra_field", "malformed", "ordinary_text"])
def test_unrecognized_or_ambiguous_text_is_not_removed(mutation):
    value = ledger(0)
    if mutation == "unknown_version":
        value["contract"] = "model_authoring_progress/2"
    elif mutation == "unknown_kind":
        value["kind"] = "economic_approval"
    elif mutation == "extra_field":
        value["extra_economic_assumption"] = "must remain"
    text = MARKER + json.dumps(value)
    if mutation == "malformed":
        text = text[:-20]
    elif mutation == "ordinary_text":
        text += "\nPM decision after this quoted example: keep this paragraph."
    messages = [{"role": "user", "content": text},
                {"role": "assistant", "content": "continue"},
                {"role": "user", "content": progress_text(1)}]
    result, receipt = project(messages)
    assert result == messages and receipt["replaced_ledgers"] == []


def test_progress_like_text_inside_tool_result_or_assistant_is_never_removed():
    messages = history()
    messages[1]["content"][0]["text"] = progress_text(0)
    messages[2]["content"][0]["content"] = progress_text(0)
    projected, receipt = project(messages)
    assert len(receipt["replaced_ledgers"]) == 2
    assert projected[1] == messages[1]
    assert projected[2]["content"][0] == messages[2]["content"][0]


def test_unicode_and_escaped_financial_text_remains_exact_and_hashable():
    messages = history()
    text = 'Decisione immutabile: "EUR\\USD" — società 日本語; valore n.d.\n' * 100
    messages[1]["content"][0]["text"] = text
    projected, receipt = project(messages)
    assert projected[1]["content"][0]["text"] == text
    assert receipt["original_bytes"] == max(len(json.dumps(messages, ensure_ascii=flag).encode()) for flag in (False, True))
    assert receipt["projected_bytes"] == max(len(json.dumps(projected, ensure_ascii=flag).encode()) for flag in (False, True))


@pytest.mark.parametrize("invalid", [None, {}, [{"role": "user", "content": float("nan")}],
                                     [{"role": "user", "content": {"not": "native content"}}]])
def test_invalid_native_history_is_rejected_before_projection(invalid):
    with pytest.raises(ValueError, match="native|finite|history|message"):
        project(invalid)
