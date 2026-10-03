"""Price is a comparison, never a target used to accept valid model economics."""
from copy import deepcopy
from pathlib import Path

import pytest

from bellomberg.valuation import dcf_engine


@pytest.mark.parametrize("fair_value,price", [(1., 100.), (100., 1.), (1100., 100.)])
def test_legacy_flag_adapter_preserves_positive_value_at_any_distance(tmp_path, fair_value, price):
    artifact = tmp_path / "original.xlsx"
    artifact.write_bytes(b"Already generated artifact; this test covers the flag adapter only")
    payload = {"fair_value_base": fair_value, "fair_value_sotp": fair_value + 1.,
               "sotp_delta_pct": 2., "path": str(artifact)}
    result = dcf_engine._apply_sanity_flag(deepcopy(payload), price)
    assert result["fair_value_base"] == fair_value
    assert result["fair_value_sotp"] == fair_value + 1.
    assert result.get("valuation_flagged") is not True
    assert not result.get("error") and "fair_value_rejected" not in result
    assert result["sanity"]["exclude_from_action_table"] is False
    assert result["sanity"]["severity"] == "OK"
    assert result["path"] == str(artifact) and artifact.is_file()
    assert not list(tmp_path.glob("*_FLAGGED.xlsx"))


@pytest.mark.parametrize("fair_value", [0., -1.])
def test_legacy_flag_adapter_still_rejects_nonpositive_calculated_value(tmp_path, fair_value):
    artifact = tmp_path / "invalid.xlsx"
    artifact.write_bytes(b"Actual invalid model placeholder")
    result = dcf_engine._apply_sanity_flag({"fair_value_base": fair_value,
        "fair_value_sotp": 20., "sotp_delta_pct": 2., "path": str(artifact)}, 100.)
    assert result["valuation_flagged"] is True and result["error"]
    assert result["fair_value_rejected"] == fair_value
    assert "fair_value_base" not in result and "fair_value_sotp" not in result
    assert Path(result["path"]).is_file() and "_FLAGGED" in result["path"]


def _documented_factories():
    from test_sector_operating_drivers import bundle_for
    from test_sector_bank_capital import bank_bundle
    from test_managed_care_integration import make_bundle, records_for
    from test_insurance_valuation import insurance_bundle
    from test_insurance_life import life_bundle
    from test_sector_rab_drivers import rab_bundle
    from test_sector_nav_drivers import nav_bundle
    from test_real_estate_valuation import property_bundle
    from test_property_development import developer_bundle
    from test_resources_valuation import resource_bundle
    from test_development_valuation import development_bundle
    from test_sotp_documented import sotp_bundle
    _, context = records_for()
    return {
        "operating_fcff": bundle_for,
        "bank_residual_income": bank_bundle,
        "managed_care_distributable_equity": lambda rows=None: make_bundle(records=rows, context=context),
        "insurance_pc_distributable_equity": insurance_bundle,
        "insurance_life_distributable_equity": life_bundle,
        "regulated_rab": rab_bundle,
        "fund_nav": nav_bundle,
        "digital_asset_nav": lambda rows=None: nav_bundle(rows, profile="dat"),
        "property_nav": property_bundle,
        "property_development_fcff": developer_bundle,
        "resources_asset_dcf": resource_bundle,
        "development_rnpv": development_bundle,
        "mixed_business_sotp": sotp_bundle,
    }


METHODS = ("operating_fcff", "bank_residual_income", "managed_care_distributable_equity",
    "insurance_pc_distributable_equity", "insurance_life_distributable_equity", "regulated_rab",
    "fund_nav", "digital_asset_nav", "property_nav", "property_development_fcff",
    "resources_asset_dcf", "development_rnpv", "mixed_business_sotp")


def _comparison_price(original, direction):
    fair_values = [original["fair_value_" + scenario] for scenario in ("bear", "base", "bull")]
    if direction == "below":
        return max(fair_values) * 100.
    if original["valuation_decision"]["method_id"] == "digital_asset_nav":
        # Existing fixture exercises a warrant with strike 5. Price 6 retains
        # that economic condition and still crosses the old 50% exclusion.
        # Below its strike a separate test must preserve the legitimate KO.
        return 6.
    return min(fair_values) / 100.


@pytest.fixture(scope="module", params=METHODS)
def documented_method(request, tmp_path_factory):
    factory = _documented_factories()[request.param]
    bundle = factory()
    output_dir = tmp_path_factory.mktemp("price-independent-" + request.param)
    result = dcf_engine.generate_valuation(bundle["case"]["ticker"], prepared_bundle=bundle,
                                           output_dir=str(output_dir))
    assert result["valuation_usability"]["usable"], result["valuation_usability"]
    assert result["valuation_decision"]["method_id"] == request.param
    return factory, bundle, result


@pytest.mark.parametrize("direction", ["above", "below"])
def test_every_real_method_keeps_all_scenarios_when_only_market_price_changes(
        documented_method, tmp_path, direction):
    from openpyxl import load_workbook
    import json
    factory, original_bundle, original = documented_method
    originals = {scenario: original["fair_value_" + scenario] for scenario in ("bear", "base", "bull")}
    rows = deepcopy(original_bundle["case"]["records"])
    quotation = next(row for row in rows if row.get("driver") == "quotation")
    # This synthetic market observation alone changes. No model assumption or
    # financial record is calibrated to the market or edited to pass a gate.
    quotation["value"]["price"] = _comparison_price(original, direction)
    changed = factory(rows)
    before = deepcopy(changed)
    result = dcf_engine.generate_valuation(changed["case"]["ticker"], prepared_bundle=changed,
                                           output_dir=str(tmp_path))
    assert changed == before
    assert result["valuation_usability"]["usable"], result["valuation_usability"]
    assert result["sanity"]["severity"] == "OK"
    assert result["sanity"]["exclude_from_action_table"] is False
    for scenario, expected in originals.items():
        assert result["fair_value_" + scenario] == expected
        check = result["sanity"]["scenario_checks"][scenario]
        assert check["severity"] == "OK" and check["exclude_from_action_table"] is False
        if direction == "above":
            minimum = 75. if result["method"] == "digital_asset_nav" else 9900.
            assert check["upside_pct"] >= minimum
        else:
            assert check["upside_pct"] <= -99.
    economic_rows = lambda source: [row for row in source["case"]["records"] if row.get("driver") != "quotation"]
    assert economic_rows(result["acquisition_snapshot"]) == economic_rows(original_bundle)
    assert result.get("valuation_flagged") is not True and "_FLAGGED" not in result["path"]
    sidecar = json.loads(Path(result["path"]).with_suffix(".payload.json").read_text(encoding="utf-8"))
    assert sidecar["fair_value_base"] == original["fair_value_base"]
    assert sidecar["valuation_usability"]["usable"]
    book = load_workbook(result["path"], data_only=False)
    try:
        workbook_text = "\n".join(str(cell.value).upper() for sheet in book for row in sheet for cell in row
                                  if isinstance(cell.value, str))
        for label in ("VAL SOSPETTA", "SUSPECT VALUATION", "ESCLUSO DALLA ACTION TABLE", "EXCLUDED FROM ACTION TABLE"):
            assert label not in workbook_text
    finally:
        book.close()


def test_extreme_price_does_not_hide_missing_source_in_any_real_method(documented_method, tmp_path):
    factory, original_bundle, original = documented_method
    rows = deepcopy(original_bundle["case"]["records"])
    quotation = next(row for row in rows if row.get("driver") == "quotation")
    quotation["value"]["price"] = _comparison_price(original, "above")
    quotation["source_id"] = ""
    bundle = factory(rows)
    result = dcf_engine.generate_valuation(bundle["case"]["ticker"], prepared_bundle=bundle,
                                           output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"] is False
    assert result.get("fair_value_base") is None
    assert result["acquisition_tasks"] or result["valuation_usability"]["reasons"]


def test_market_price_still_blocks_unavailable_warrant_exercise(tmp_path):
    from test_sector_nav_drivers import nav_bundle, nav_records
    rows = nav_records("dat")
    original_capitalization = deepcopy(next(row["value"] for row in rows if row["driver"] == "capitalization"))
    next(row["value"] for row in rows if row["driver"] == "quotation")["price"] = 0.105
    result = dcf_engine.generate_valuation("SYNTH-NAV", prepared_bundle=nav_bundle(rows, profile="dat"),
                                           output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"] is False
    assert result.get("fair_value_base") is None
    assert any(task["field"] == "dilution_terms" and "non economicamente disponibile" in task["reason"]
               for task in result["acquisition_tasks"])
    actual_capitalization = next(row["value"] for row in result["acquisition_snapshot"]["case"]["records"]
                                if row["driver"] == "capitalization")
    assert actual_capitalization == original_capitalization
