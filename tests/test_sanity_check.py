"""Fair-value/price distance is informational; invalid inputs stay explicit."""
import pytest

from bellomberg.valuation.dcf_engine import sanity_check


def test_sanity_severita_e_esclusione():
    ok = sanity_check(100.0, 100.0)
    assert ok["severity"] == "OK" and not ok["exclude_from_action_table"]

    comparison = sanity_check(145.0, 100.0)
    assert comparison["severity"] == "OK" and not comparison["exclude_from_action_table"]

    distant = sanity_check(160.0, 100.0)
    assert distant["severity"] == "OK" and not distant["exclude_from_action_table"]
    assert "ESCLUSO" not in (distant["headline"] or "")

    nd = sanity_check(None, 100.0)      # input mancante: n/d dichiarato, mai crash
    assert nd["status"] == "n/d" and nd["severity"] == "OK"


@pytest.mark.parametrize("fair_value,price,upside", [(100., 1., 9900.), (1., 100., -99.),
    (1100., 100., 1000.), (100., 100., 0.)])
def test_any_positive_finite_divergence_is_informational(fair_value, price, upside):
    check = sanity_check(fair_value, price)
    assert check["status"] == "ok"
    assert check["severity"] == "OK" and check["exclude_from_action_table"] is False
    assert check["upside_pct"] == upside
    assert check["ratio"] == pytest.approx(fair_value / price)
    text = str(check.get("headline") or "").upper()
    assert all(label not in text for label in ("VAL SOSPETTA", "ESCLUSO", "SUSPECT VALUATION", "EXCLUDED"))
