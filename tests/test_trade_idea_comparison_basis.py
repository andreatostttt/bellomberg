"""A numerical delta requires the same documented economic and quotation basis."""
from copy import deepcopy

import pytest

from bellomberg.valuation.trade_idea_comparison import differences, model_comparison


@pytest.fixture
def model():
    def record(driver, value):
        return {"scenario": "model", "driver": driver, "field": "valuation_perimeter",
                "entity": "Fictional group", "period": "2026-06-30", "unit": "contract",
                "accounting_basis": "scope", "value": value}
    return {"generation_id": "fictional-original", "ticker": "SYNTH-COMP",
            "valuation_date": "2026-06-30", "financial_currency": "EUR", "currency": "EUR",
            "valuation_decision": {"method_id": "operating_fcff", "method_version": "2"},
            "fair_value_bear": 8., "fair_value_base": 10., "fair_value_bull": 12.,
            "acquisition_snapshot": {"case": {"records": [
                record("perimeter", {"entity": "Fictional group", "currency": "EUR", "share_class": "Common"}),
                record("quotation", {"financial_currency": "EUR", "quote_currency": "EUR",
                       "quote_unit": "EUR", "quote_units_per_currency": 1., "financial_to_quote_rate": 1.,
                       "shares_per_quote": 1., "share_class": "Common", "price": 9., "price_as_of": "2026-06-30"}),
            ]}}}


def change(model, driver, field, value):
    updated = deepcopy(model)
    updated["generation_id"] = "fictional-revision"
    next(r for r in updated["acquisition_snapshot"]["case"]["records"]
         if r["driver"] == driver)["value"][field] = value
    return updated


@pytest.mark.parametrize("driver,field,value", [
    ("perimeter", "share_class", "Preferred"), ("perimeter", "entity", "Different group"),
    ("quotation", "shares_per_quote", 2.), ("quotation", "share_class", "Preferred"),
    ("quotation", "financial_to_quote_rate", 1.1),
])
def test_contract_basis_change_suppresses_fair_value_delta_and_labels_driver(model, driver, field, value):
    revised = change(model, driver, field, value)
    compared = model_comparison(model, revised)
    assert all(row["comparability"] == "not_comparable" and row["delta"] is None
               for row in compared["values"])
    row = next(row for row in compared["drivers"] if row["driver"] == driver)
    assert row["comparability"] == "not_comparable" and row["reasons"]


def test_same_basis_economic_change_and_price_only_refresh_keep_numeric_delta(model):
    revised = change(model, "quotation", "price", 11.)
    revised["fair_value_base"] = 11.
    compared = model_comparison(model, revised)
    assert compared["values"][1]["delta"] == 1.
    assert compared["values"][1]["comparability"] == "same_basis"
    assert compared["drivers"][0]["comparability"] == "same_basis"


@pytest.mark.parametrize("missing", ["perimeter", "quotation", "method_version"])
def test_missing_basis_on_both_sides_does_not_claim_comparability(model, missing):
    if missing == "method_version":
        model["valuation_decision"].pop(missing)
    else:
        model["acquisition_snapshot"]["case"]["records"] = [
            r for r in model["acquisition_snapshot"]["case"]["records"] if r["driver"] != missing]
    compared = model_comparison(model, deepcopy(model))
    assert all(row["delta"] is None and row["reasons"] for row in compared["values"])


def test_method_version_change_is_not_a_numeric_comparison(model):
    revised = deepcopy(model)
    revised["valuation_decision"]["method_version"] = "3"
    assert all(r["delta"] is None for r in model_comparison(model, revised)["values"])


@pytest.mark.parametrize("value", [float("inf"), float("nan"), True])
def test_nonfinite_or_boolean_fair_value_never_produces_delta(model, value):
    revised = deepcopy(model)
    revised["fair_value_base"] = value
    result = model_comparison(model, revised)["values"][1]
    assert result["delta"] is None and result["comparability"] == "not_comparable"


def test_explicit_gbx_to_gbp_normalization_preserves_same_share_comparison(model):
    left = change(model, "quotation", "quote_currency", "GBP")
    quote = left["acquisition_snapshot"]["case"]["records"][1]["value"]
    quote.update(quote_unit="GBX", quote_units_per_currency=100., financial_to_quote_rate=.8)
    left.update(currency="GBX", fair_value_bear=800., fair_value_base=1000., fair_value_bull=1200.)
    right = deepcopy(left)
    right["currency"] = "GBP"
    right["acquisition_snapshot"]["case"]["records"][1]["value"].update(quote_unit="GBP", quote_units_per_currency=1.)
    right.update(fair_value_bear=8., fair_value_base=10., fair_value_bull=12.)
    compared = model_comparison(left, right)
    assert all(row["delta"] == 0. and row["unit"] == "GBP" for row in compared["values"])


def test_nonfinite_driver_vector_is_explicitly_noncomparable(model):
    row = {"scenario": "base", "driver": "growth", "value": [.1, .2], "unit": "ratio"}
    model["acquisition_snapshot"]["case"]["records"].append(row)
    revised = deepcopy(model)
    revised["acquisition_snapshot"]["case"]["records"][-1]["value"] = [.1, float("inf")]
    result = differences(model, revised)[0]
    assert result["delta"] is None and result["comparability"] == "not_comparable"


def test_finite_operands_with_overflowing_delta_are_not_comparable(model):
    model["fair_value_base"] = -1e308
    revised = deepcopy(model)
    revised["fair_value_base"] = 1e308
    result = model_comparison(model, revised)["values"][1]
    assert result["delta"] is None and result["comparability"] == "not_comparable"


@pytest.mark.parametrize("before,after", [([-1e308], [1e308]), ([1.], [1., 2.])])
def test_vector_overflow_or_shape_change_has_no_numeric_delta(model, before, after):
    model["acquisition_snapshot"]["case"]["records"].append(
        {"scenario": "base", "driver": "growth", "value": before, "unit": "EUR"})
    revised = deepcopy(model)
    revised["acquisition_snapshot"]["case"]["records"][-1]["value"] = after
    result = differences(model, revised)[0]
    assert result["delta"] is None and result["comparability"] == "not_comparable"
