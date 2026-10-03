"""Native committee views retain financial evidence and reject incomplete Red responses."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from bellomberg.agents import trade_idea, red_team
from bellomberg.agents.specialists import base
from bellomberg.core import llm_client
from bellomberg.core.llm_client import Messaggio, TextBlock, ThinkingBlock, Usage
from bellomberg.core.trade_idea_contract import validate_committee_review
from test_trade_idea_live_consultation import board


@pytest.fixture
def model_board(board, monkeypatch):
    records = [{"driver": "driver_" + str(i), "value": [i + 0.25, i + 0.5],
        "rationale": "Unique full economic rationale " + str(i), "source_id": "primary",
        "scenario": "base", "entity": "Fixture", "kind": "forecast", "as_of": "2026-09-29",
        "valid_until": "2026-10-29", "accounting_basis": "reported", "period": ["2027", "2028"], "unit": "USD million",
        "evidence": {"date": "2026-09-29", "excerpt": "Full evidence " + str(i)}} for i in range(61)]
    rows = [{"id": "parent-" + str(i), "desk": "quant", "requester": "fundamentals",
        "status": "failed" if i < 2 else "truncated", "response": "Unaccepted partial answer " + str(i),
        "author_view_complete": False, "source_fingerprint": "source-fingerprint",
        "supersedes_failed": {"id": "parent-" + str(i - 1)} if i else None} for i in range(4)]
    for i, desk in enumerate(("macro", "eventdesk", "crypto", "options", "quant")):
        rows.append({"id": "current-" + str(i), "desk": desk, "requester": "fundamentals", "status": "complete",
            "response": "Current full answer and dissent: " + desk, "author_view_complete": True,
            "source_fingerprint": "source-fingerprint", "draft_sha256": "draft-digest", "question": "Economic question: " + desk,
            "draft_assumptions": {"wacc": 0.115, "growth": [0.02, 0.03]}, "evidence_refs": ["primary"],
            "fundamentals_decision": {"effect": "retain", "rationale": "Full decision and dissent: " + desk},
            "historical_provenance": {"copy": "Archived repeated audit " * 20},
            "recovery_context": {"copy": "Archived repeated recovery " * 20},
            "supersedes_failed": {"id": "parent-3"} if desk == "quant" else None})
    board.data.update(_model_consultations=rows, _model_input_draft={"wacc": 0.115},
        _model_input_basis={"archived_growth_thesis_history": [{"previous_generation_id": "old-generation", "thesis": {"scenarios": "Archive narrative " * 100}}],
            "independent_annotations": [{"review": "Paid economic dissent must remain complete."}], "provenance": {"source": "paid-source"}})
    board.source_qualification = {"fingerprint": "source-fingerprint", "source_report": {"documents": [{"id": "primary", "title": "Primary filing", "url": "https://example.test/filing", "sha256": "a" * 64}]}}
    model = {"ticker": "TEST", "generation_id": "new-generation", "snapshot_id": "snapshot", "valuation_date": "2026-09-29",
        "valuation_decision": {"method_id": "operating_fcff"}, "valuation_usability": {"usable": True, "reasons": []},
        "acquisition_snapshot": {"case": {"records": records, "assumptions": {"price": 10.5}}, "analysis_context": {"scenario_rationale": {"base": "Full base rationale."}}},
        "preparation": {"growth_thesis_status": "unavailable"}, "analytical_quality": {"status": "WARN", "rows": [{"rationale": "Current quality is not PM approval."}]},
        "sanity": {"status": "WARN", "scenario_checks": {"bear": {"status": "BLOCK"}}}}
    aliases = {"scenario": "scenario", "driver": "driver", "values": "value", "kind": "kind", "source": "source_id",
        "source_date": "as_of", "valid_until": "valid_until", "metric": "driver", "basis": "accounting_basis",
        "rationale": "rationale", "entity": "entity", "period": "period", "unit": "unit"}
    model["analytical_quality"]["rows"] = [{"driver": r["driver"], "scenario": r["scenario"], "values": deepcopy(r["value"]),
        "evidence": {key: deepcopy(r[field]) for key, field in aliases.items()}, "issues": ["Current retained check note."]} for r in records]
    board.valuation_results = {"TEST": model}
    ref = {"generation_id": "new-generation", "snapshot_id": "snapshot", "workbook_sha256": "b" * 64,
        "model_values": {"fair_value_base": 10.5}, "model_exhibits": {"input_bindings": [{"cell": "D" + str(i), "formula": "=B1+C1"} for i in range(461)],
            "exhibits": [{"id": str(i), "rows": [[i + 1.25]]} for i in range(5)]}}
    monkeypatch.setattr(trade_idea, "_verified_candidate_valuations", lambda _b: [deepcopy(ref)])
    monkeypatch.setattr(trade_idea, "_current_model_consultations", lambda _b: [r for r in rows if r["id"].startswith("current-")])
    monkeypatch.setattr(trade_idea, "_consultation_received", lambda _b, row: row["status"] == "complete")
    return board


def test_default_author_view_keeps_full_history_and_all_nine_consultations(model_board):
    actual = trade_idea.candidate_model_context(model_board)
    assert actual == trade_idea.candidate_model_context(model_board, purpose="author")
    assert actual["input_basis"] == model_board.data["_model_input_basis"]
    assert len(actual["consultations"]) == 9
    assert "consultation_audit_refs" not in actual


def test_committee_view_retains_full_records_numbers_formulas_and_current_quality(model_board):
    author = trade_idea.candidate_model_context(model_board)
    committee = trade_idea.candidate_model_context(model_board, purpose="committee")
    for key in ("method_records", "assumptions", "analysis_context", "qualified_documents", "verified_models", "model_usability", "generation_id", "snapshot_id"):
        assert committee[key] == author[key]
    assert len(committee["method_records"]) == 61
    assert len(committee["verified_models"][0]["model_exhibits"]["input_bindings"]) == 461
    assert committee["input_basis"]["independent_annotations"] == author["input_basis"]["independent_annotations"]
    quality = deepcopy(committee["analytical_quality"])
    aliases = quality.pop("evidence_record_field_aliases")
    for row in quality["rows"]:
        reference = row["evidence"]
        assert reference["status"] == "retained_equivalent_reference"
        index = int(reference["reference"].split("[")[1][:-1])
        evidence = {key: deepcopy(committee["method_records"][index][field]) for key, field in aliases.items()}
        assert trade_idea._plan_digest(evidence) == reference["canonical_sha256"]
        row["evidence"] = evidence
    assert quality == model_board.valuation_results["TEST"]["analytical_quality"]
    # A unique current evidence note has no exact retained equivalent and stays whole.
    model_board.valuation_results["TEST"]["analytical_quality"]["rows"][0]["evidence"]["unique_note"] = "Current unique dissent."
    changed = trade_idea.candidate_model_context(model_board, purpose="committee")
    assert changed["analytical_quality"]["rows"][0] == model_board.valuation_results["TEST"]["analytical_quality"]["rows"][0]
    assert committee["sanity"] == model_board.valuation_results["TEST"]["sanity"]


def test_committee_preserves_current_answers_decisions_lineage_and_immutable_raw_audit(model_board):
    before = deepcopy(model_board.data)
    author = trade_idea.candidate_model_context(model_board)
    committee = trade_idea.candidate_model_context(model_board, purpose="committee")
    assert len(committee["consultations"]) == 5 and len(committee["consultation_audit_refs"]) == 9
    for original, current in zip([r for r in author["consultations"] if r["current"]], committee["consultations"]):
        for key in ("id", "desk", "status", "response", "fundamentals_decision", "question", "draft_assumptions", "evidence_refs", "supersedes_failed", "consultation_row_sha256"):
            assert current[key] == original[key]
        for key in ("historical_provenance", "recovery_context"):
            assert current[key]["canonical_sha256"] == trade_idea._plan_digest(original[key])
    archive = committee["input_basis"]["archived_growth_thesis_history"]
    assert archive["canonical_sha256"] == trade_idea._plan_digest(author["input_basis"]["archived_growth_thesis_history"])
    assert archive["canonical_chars"] > 0 and archive["status"] == "archived_reference"
    assert [r["status"] for r in committee["consultation_audit_refs"][:4]] == ["failed", "failed", "truncated", "truncated"]
    assert committee["consultation_audit_refs"][3]["supersedes_failed"] == {"id": "parent-2"}
    assert model_board.data == before


@pytest.mark.parametrize("round_n,scope,desk,expected", [(0,"trade_idea","quant","author"), (1,"trade_idea","fundamentals","author"), (2,"trade_idea","quant","committee"), (2,"weekly","quant",None)])
def test_native_round_context_opts_in_only_for_trade_idea_r2(board, monkeypatch, round_n, scope, desk, expected):
    calls = []
    def context(_b, *, purpose="author"):
        calls.append(purpose)
        return {"purpose": purpose}
    monkeypatch.setattr(trade_idea, "candidate_model_context", context)
    board.current_round, board.run_scope = round_n, scope
    cls = type("ContextDesk", (base.Specialist,), {"name": desk})
    cls(board, client=SimpleNamespace())._build_round_context(round_n)
    assert calls == ([] if expected is None else [expected])


def native_red(model_board, monkeypatch, stop_reason, text):
    calls = []
    model = trade_idea.model_for_role("red_team")
    response = Messaggio("synthetic-red-response", model, [ThinkingBlock("synthetic omitted reasoning")] + ([] if text is None else [TextBlock(text)]),
        stop_reason, usage=Usage(10, 12000 if stop_reason == "max_tokens" else 5, 0, 0, cost_usd=0.001))
    def create(**kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(llm_client, "OpenRouterClient", lambda **_kw: SimpleNamespace(messages=SimpleNamespace(create=create)))
    result = trade_idea._run_exact_model_red_team(model_board, None, red_team.run_red_team)
    return result, calls


@pytest.mark.parametrize("stop_reason,text", [("max_tokens",None), ("max_tokens",'{"decisive_questions": []}'), ("end_turn",None)])
def test_native_red_incomplete_response_is_rejected_before_any_review_attestation(model_board, monkeypatch, stop_reason, text):
    with pytest.raises(RuntimeError, match="Red Team native response"):
        native_red(model_board, monkeypatch, stop_reason, text)
    assert "_red_model_review" not in model_board.data
    assert model_board.data["_red_team_native_terminal"]["stop_reason"] == stop_reason


def test_native_complete_red_json_is_valid_and_uses_committee_context(model_board, monkeypatch):
    review = {"decisive_questions": ["Is terminal reinvestment supported?"], "objections": [{"id": "q1", "desk": "quant", "category": "driver", "material": True,
        "objection": "Explain terminal reinvestment.", "evidence_refs": ["primary"], "requested_change": "Explain the supplied RONIC evidence."}]}
    result, calls = native_red(model_board, monkeypatch, "end_turn", json.dumps(review))
    assert validate_committee_review(result) == review
    assert len(calls) == 1 and calls[0]["max_tokens"] == 65536 and calls[0]["thinking"] == {"type": "effort", "effort": "max"}
    context = json.loads(calls[0]["messages"][0]["content"].split("EXACT COMMON MODEL DRIVERS AND EVIDENCE:\n",1)[1])
    assert len(context["consultations"]) == 5
    assert context["input_basis"]["archived_growth_thesis_history"]["status"] == "archived_reference"
    assert model_board.data["_red_model_review"]["model_ref"]["generation_id"] == "new-generation"
