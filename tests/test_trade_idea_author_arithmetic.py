"""Read-only arithmetic diagnostics expose native options, never adopt estimates."""
from copy import deepcopy
import json
from types import SimpleNamespace

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists import base
from bellomberg.valuation.operating_adapter import operating_revenue_errors
from test_input_preparation import _bundle, _documents, _operating_plan


def _board(*, rounded=False):
    plan = _operating_plan()
    if rounded:
        # Same failure mechanism as the saved ACN draft: cumulative growth,
        # normalized volume 1000, and prices rounded to six decimal places.
        rates = [.025, .03, .028, .025, .023, .022, .021, .02, .02, .02]
        for scenario in plan["scenarios"].values():
            scenario["revenue_growth"]["value"] = list(rates)
            projected, prices = plan["model"]["historical_revenue"]["value"], []
            for rate in rates:
                projected *= 1 + rate
                prices.append(round(projected / 1000, 6))
            scenario["revenue_build"]["value"].update(volume=[1000.] * 10, unit_price=prices)
    return SimpleNamespace(data={"_model_input_draft": plan}, valuation_results={},
        source_qualification={"method_id": "operating_fcff", "bundle": _bundle(),
            "source_report": {"documents": _documents()}})


def _diagnostic(board):
    fragments, offset = [], 0
    while True:
        page = trade_idea._candidate_contract_page(board,
            {"contract_section": "draft_validation", "offset": offset})
        assert page["ok"], page
        assert len(json.dumps(page, ensure_ascii=False)) + 256 <= base._tetto_tool_result()
        fragments.append(page["text"])
        assert page["next_offset"] > offset
        offset = page["next_offset"]
        if page["complete"]:
            break
    return json.loads("".join(fragments)), len(fragments)


def test_rounded_prices_have_exact_native_diagnostic_without_adoption():
    board = _board(rounded=True)
    before = deepcopy(vars(board))
    report, _ = _diagnostic(board)
    assert report["status"] == "prepared" and report["issues"] == []
    assert "revenue_arithmetic" in report, "prepared input proof lacks native revenue arithmetic diagnostic"
    assert report["revenue_arithmetic"]["status"] == "blocked"
    assert report["forecast_arithmetic"]["status"] == "not_checked"
    for scope, issue in report["revenue_arithmetic"]["issues"].items():
        scenario = board.data["_model_input_draft"]["scenarios"][scope]
        build = scenario["revenue_build"]["value"]
        opening = board.data["_model_input_draft"]["model"]["historical_revenue"]["value"]
        growth = scenario["revenue_growth"]["value"]
        assert issue["errors"] == operating_revenue_errors(build, opening, growth)
        option = issue["calculated_unit_price_option"]
        assert option["basis"] == "(revenue_from_growth - other_revenue) / (volume * utilization)"
        assert "not sourced prices or approval" in option["limitation"]
        # Verify the option only on a test copy: the production read never adopts it.
        copied_build = {**deepcopy(build), "unit_price": option["value"]}
        assert operating_revenue_errors(copied_build, opening, growth) == []
        assert option["value"] != build["unit_price"]
    assert vars(board) == before
    assert report["workbook_created"] is False
    assert report["plan_sha256"] == trade_idea._plan_digest(before["data"]["_model_input_draft"])


def test_arithmetic_report_keeps_native_paging(monkeypatch):
    monkeypatch.setattr(base, "_tetto_tool_result", lambda: 1800)
    board = _board(rounded=True)
    before = deepcopy(vars(board))
    report, pages = _diagnostic(board)
    assert pages > 1
    assert set(report["revenue_arithmetic"]["issues"]) == {"bear", "base", "bull"}
    assert vars(board) == before


def test_reconciled_inputs_report_existing_forecast_arithmetic_without_mutation():
    board = _board()
    before = deepcopy(vars(board))
    report, _ = _diagnostic(board)
    assert report["revenue_arithmetic"] == {"status": "ready", "issues": {}}
    forecast = report["forecast_arithmetic"]
    assert forecast["status"] == "ready"
    assert set(forecast["derived_fcff_forecasts"]) == {"bear", "base", "bull"}
    assert vars(board) == before


def test_incomplete_input_proof_skips_arithmetic_instead_of_inventing_inputs():
    board = _board()
    del board.data["_model_input_draft"]["model"]["opening_nwc"]
    before = deepcopy(vars(board))
    report, _ = _diagnostic(board)
    assert report["status"] == "incomplete"
    assert any(issue["field"] == "opening_nwc" for issue in report["issues"])
    assert report["revenue_arithmetic"]["status"] == report["forecast_arithmetic"]["status"] == "not_checked"
    assert vars(board) == before


def test_reconciled_revenue_does_not_hide_existing_terminal_error():
    board = _board()
    board.data["_model_input_draft"]["scenarios"]["bear"]["terminal_bridge"]["value"]["normalized_ebit"] = 16.
    before = deepcopy(vars(board))
    report, _ = _diagnostic(board)
    assert report["status"] == "prepared"
    assert report["revenue_arithmetic"]["status"] == "ready"
    assert report["forecast_arithmetic"]["status"] == "blocked"
    assert "EBIT normalizzato non riconciliato" in report["forecast_arithmetic"]["reason"]
    assert vars(board) == before
