"""Independent quotation overlay checks; all observations are synthetic and frozen."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_author_quotation import case  # Reuse the raw-bound, declared synthetic fixture.


def _board(case):
    return SimpleNamespace(
        source_qualification=case.qualification,
        source_admission=deepcopy(case.qualification),
        current_round=1, model_phase="building", run_scope="trade_idea",
        run_id="independent-quotation-review", target_ticker=case.qualification["ticker"],
        valuation_results={}, quotation_price_fetch=case.fetch,
        data={"_model_input_draft": deepcopy(case.plan),
              "_source_research": {"archive_root": str(case.root)},
              "_model_input_basis": {"plan": {}, "source_fingerprint": case.qualification["fingerprint"]}},
        persist_run_checkpoint=lambda *_: None,
    )


def test_changed_author_date_and_failed_read_never_expose_old_basis_as_current(case):
    from bellomberg.agents.trade_idea import _handle_candidate_plan_tool
    from bellomberg.valuation.author_quotation import board_quotation_view
    from bellomberg.valuation.trade_idea_model import source_fingerprint

    # A second, explicitly declared synthetic filing makes the alternate date
    # admissible. Failure therefore occurs in the exact new-date read, not an
    # earlier missing-primary guard.
    alternate = deepcopy(case.primary)
    alternate["id"] = "synthetic-alternate-opening"
    alternate["metadata"]["report_date"] = "2025-09-30"
    case.qualification["source_report"]["documents"].append(alternate)
    case.qualification["fingerprint"] = source_fingerprint(case.qualification)
    board = _board(case)
    accepted_before = deepcopy(board.source_admission)
    request = {"scope": "model", "drivers": ["quotation"]}
    first = _handle_candidate_plan_tool(board, "fundamentals", "get_candidate_model_inputs", request)
    assert first["quotation_basis"]["status"] == "ready", first
    old_receipts = deepcopy(board.data["_author_quotation"])
    old_basis = deepcopy(board.data["_model_input_basis"])
    board.data["_model_input_draft"]["model"]["calendar"]["value"]["valuation_date"] = "2025-09-30"
    authored_before = deepcopy(board.data["_model_input_draft"])
    failed_reads = []

    def timeout(ticker, on):
        failed_reads.append((ticker, on))
        raise TimeoutError("Frozen alternate-date acquisition failure")

    board.quotation_price_fetch = timeout
    result = _handle_candidate_plan_tool(board, "fundamentals", "get_candidate_model_inputs", request)
    assert result["ok"] is True
    assert result["quotation_basis"]["status"] == "incomplete"
    assert result["quotation_basis"]["issues"]
    assert "quotation" not in result["basis"]["drivers"]
    assert failed_reads == [(case.qualification["ticker"], "2025-09-30")]
    with pytest.raises(ValueError, match="context changed"):
        board_quotation_view(board)
    assert board.data["_author_quotation"] == old_receipts
    assert board.data["_model_input_basis"] == old_basis
    assert board.data["_model_input_draft"] == authored_before
    assert board.source_qualification == board.source_admission == accepted_before


def test_derived_close_cannot_replace_conflicting_admitted_same_id(case):
    from bellomberg.valuation.author_quotation import quotation_basis
    from bellomberg.valuation.quotation_evidence import historical_quote_document
    from bellomberg.valuation.trade_idea_model import source_fingerprint

    admitted = historical_quote_document(case.qualification["ticker"],
        on=case.plan["model"]["calendar"]["value"]["valuation_date"],
        as_of=case.qualification["as_of"], fetch=lambda ticker, on: {
            "symbol": ticker, "date": on, "close": 20., "currency": "EUR"})
    assert admitted["status"] == "ready"
    case.qualification["source_report"]["documents"].extend(admitted["documents"])
    case.qualification["fingerprint"] = source_fingerprint(case.qualification)
    before = deepcopy(case.qualification)
    result = quotation_basis(case.qualification, case.plan,
        archive_root=case.root, price_fetch=case.fetch)
    assert result["status"] == "incomplete"
    assert "replace an admitted document" in str(result["issues"])
    assert not result.get("driver") and not result.get("receipt")
    assert case.qualification == before
    assert len(case.calls) == 1


def test_different_financial_currency_requires_actual_fx_evidence(case):
    from bellomberg.valuation.author_quotation import quotation_basis

    case.plan["model"]["perimeter"]["value"]["currency"] = "USD"
    before = deepcopy(case.plan)
    result = quotation_basis(case.qualification, case.plan,
        archive_root=case.root, price_fetch=case.fetch)
    assert result["status"] == "incomplete"
    assert "FX bridge required" in str(result["issues"])
    assert not result.get("driver") and not result.get("receipt")
    assert case.plan == before
    assert len(case.calls) == 1
