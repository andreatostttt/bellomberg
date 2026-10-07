"""P0-8: metadata provider preservati, inferenza fiscale e FCF qualificati."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from bellomberg.market_data import consensus_estimates as ce
from bellomberg.reporting.trade_idea_facts import extract_facts
from test_trade_idea_facts import T, checkpoint, receipt, history_data, consensus_data, fundamentals_data


def consensus(currency="EUR"):
    data = consensus_data()
    for key in ("revenue_estimates", "eps_estimates"):
        for row in data[key]:
            row.update(currency=currency, period_end="2029-06-30", period_start="2028-07-01")
    return data


def facts(cons, hist=None, fund=None):
    rows = [receipt("get_consensus_estimates", cons), receipt("get_financial_history", hist or history_data())]
    if fund is not None:
        rows.append(receipt("get_fundamentals", fund))
    return extract_facts(checkpoint(rows))


def test_dataframe_metadata_not_numeric_and_values_unchanged():
    df = pd.DataFrame([{"avg": 123.45, "yearAgoRevenue": 0.0, "currency": "EUR",
                        "endDate": "2029-06-30", "startDate": "2028-07-01", "fiscalYear": "FY2029"}], index=["0y"])
    row = ce._df_rows(df, ["avg", "yearAgoRevenue"])[0]
    assert row["avg"] == 123.45 and row["yearAgoRevenue"] == 0
    assert row.get("currency") == "EUR"
    assert row.get("period_end") == "2029-06-30"
    assert row.get("fiscal_year_label") == "FY2029"
    assert row["provider_metadata"]["endDate"] == "2029-06-30"


def test_ambiguous_fiscal_match_not_latest_and_order_independent():
    hist = history_data()
    hist["items"]["revenue"] = {"2027": 1234.5, "2028": 1234.5}
    first = facts(consensus(), hist)
    hist["items"]["revenue"] = dict(reversed(list(hist["items"]["revenue"].items())))
    second = facts(consensus(), hist)
    assert first["consensus"]["current_fiscal_year"] is None
    assert first["consensus"]["fiscal_year_candidates"] == [2028, 2029]
    assert first["consensus"] == second["consensus"]
    assert any("piu esercizi" in gap for gap in first["gaps"])


def test_unique_match_is_labelled_inference_and_absent_currency_not_compared():
    current = facts(consensus())
    assert current["consensus"]["current_fiscal_year"] == 2029
    assert current["consensus"].get("fiscal_year_basis") == "revenue_match_inference"
    legacy = facts(consensus_data())
    assert legacy["consensus"]["current_fiscal_year"] is None


def test_conflicting_fiscal_label_does_not_get_overridden():
    data = consensus()
    data["revenue_estimates"][0]["fiscal_year_label"] = "FY2031"
    result = facts(data)
    assert result["consensus"]["current_fiscal_year"] is None
    assert any("conflitto" in gap for gap in result["gaps"])


def test_mixed_currency_targets_do_not_discard_estimates_or_mutate_receipts():
    data = consensus()
    data["currency"] = "GBP"
    data["eps_estimates"][0]["currency"] = "USD"
    rows = checkpoint([receipt("get_consensus_estimates", data)])
    before = deepcopy(rows)
    result = extract_facts(rows)
    assert result["consensus"] is not None
    assert result["consensus"]["eps"][0]["currency"] == "USD"
    assert result["consensus"]["revenue"][0]["currency"] == "EUR"
    assert result["consensus"]["target_mean"] is None
    assert result["consensus"]["target_currency"] == "GBP"
    assert rows == before


def test_no_match_means_not_reconciled_not_missing_fiscal_year():
    data = consensus()
    data["revenue_estimates"][0]["yearAgoRevenue"] = 9999.5
    result = facts(data)
    assert result["consensus"]["current_fiscal_year"] is None
    assert not any("manca almeno un esercizio" in gap for gap in result["gaps"])
    assert any("non riconciliat" in gap for gap in result["gaps"])


def test_fcf_snapshot_metadata_absent_keeps_sign_and_history():
    fund = fundamentals_data()
    fund["free_cashflow"] = -777.5
    result = facts(consensus(), fund=fund)
    assert result["fundamentals"]["free_cashflow"] == -777.5
    meta = result["fundamentals"].get("cashflow_metadata", {}).get("free_cashflow")
    assert meta and meta["period_end"] is None and meta["definition"] is None
    assert result["history"]["series"]["fcf"][2028] == 70
    assert any("definizione" in gap and "periodo" in gap for gap in result["gaps"])


def test_fundamentals_tool_does_not_assign_fiscal_date_or_quote_currency_to_fcf(monkeypatch):
    from bellomberg.agents import agent_tools as at
    monkeypatch.setattr(at, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(ce, "provider_symbol", lambda ticker: ticker)
    monkeypatch.setattr(at.yf, "Ticker", lambda ticker: SimpleNamespace(info={
        "symbol": T, "currency": "GBP", "financialCurrency": "USD", "marketCap": 55,
        "freeCashflow": -777.5, "operatingCashflow": 888.5,
        "lastFiscalYearEnd": 1835481600, "mostRecentQuarter": 1843344000}))
    result = at.tool_get_fundamentals(T)
    assert result["free_cashflow"] == -777.5
    meta = result.get("cashflow_metadata", {}).get("free_cashflow")
    assert meta and meta["currency"] is None and meta["period_end"] is None
    assert meta["provider_financial_currency"] == "USD" and meta["definition"] is None
    assert result.get("market_cap_currency") is None  # quote currency does not attest market cap
    assert result["quote_currency"] == "GBP"


def test_report_units_per_series_and_common_history_excludes_other_currency():
    from bellomberg.reporting import trade_idea_report as report
    data = consensus()
    data["eps_estimates"][0]["currency"] = "USD"
    result = facts(data)
    labels = "\n".join(a + " " + b for a, b in report._consensus_rows(result, "en"))
    assert "USD" in labels and "EUR" in labels and "2029-06-30" in labels
    table, note = report._history_table(result, "en", report._m_styles(report._memo_styles()), 450)
    eps = next(row for row in table._cellvalues if "EPS" in row[0].getPlainText())
    assert eps[-1].getPlainText() == "n.d."
    assert "not comparable" in note


def test_chart_comparison_does_not_assume_consensus_currency():
    from bellomberg.reporting import trade_idea_charts as charts
    result = facts(consensus_data())
    _, _, _, missing = charts._hist_and_cons(result, "eps_path", "EPS", charts._T["en"],
                                            "eps_diluted", "eps", "en")
    assert isinstance(missing, dict) and "not comparable" in missing["missing"]


def test_provider_real_local_schema_currency_survives_and_period_not_invented():
    from yfinance.scrapers.analysis import Analysis
    analysis = Analysis(None, T)
    analysis._earnings_trend = [{"period": "0y", "endDate": "2029-06-30",
        "revenueEstimate": {"avg": {"raw": 1300}, "yearAgoRevenue": {"raw": 1234.5},
                            "revenueCurrency": "EUR"}}]
    frame = analysis.revenue_estimate
    rows = ce._df_rows(frame, ["avg", "yearAgoRevenue"])
    assert rows[0]["currency"] == "EUR"
    # yfinance drops the root endDate: preserving metadata must not reconstruct it.
    assert rows[0]["period_end"] is None


def test_estimate_metadata_cache_round_trip_and_invalid_date(tmp_path):
    df = pd.DataFrame([{"avg": 123.45, "yearAgoRevenue": 0, "currency": "EUR", "endDate": "bad-date"}], index=["0y"])
    rows = ce._df_rows(df, ["avg", "yearAgoRevenue"])
    observation = {"details_acquired_at": "2030-01-02T10:00:00+00:00",
                   "details_identity_symbol": T, "revenue_estimates": rows}
    saved = ce.persist_market_observation(T, cache_dir=tmp_path, consensus=observation)
    assert saved["revenue_estimates"] == rows
    assert rows[0]["metadata_status"]["period_end"] == "INVALID"
    assert rows[0]["provider_metadata"]["endDate"] == "bad-date"
    assert saved.get("data_as_of") is None


def test_currency_case_does_not_convert_pence_to_pounds():
    meta = ce.estimate_metadata({"currency": "GBp"})
    assert meta["currency"] is None
    assert meta["provider_metadata"]["currency"] == "GBp"
    assert meta["metadata_status"]["currency"] == "INVALID"


@pytest.mark.parametrize("value", [0, None])
def test_year_ago_values_are_never_imputed(value):
    data = consensus()
    data["revenue_estimates"][0]["yearAgoRevenue"] = value
    result = facts(data)
    assert result["consensus"]["year_ago_revenue"] == value
    assert result["consensus"]["revenue"][0]["yearAgoRevenue"] == value
    assert result["consensus"]["current_fiscal_year"] is None


def test_acquisition_not_estimate_vintage_and_legacy_immutable():
    data = consensus_data()
    data["details_acquired_at"] = "2030-01-02T10:00:00+00:00"
    data["data_as_of"] = "2029-12-31T10:00:00+00:00"
    original = checkpoint([receipt("get_consensus_estimates", data), receipt("get_financial_history", history_data())])
    before = deepcopy(original)
    result = extract_facts(original)
    assert result["consensus"]["details_acquired_at"] == data["details_acquired_at"]
    assert result["consensus"]["estimates_as_of"] is None
    assert result["consensus"]["current_fiscal_year"] is None
    assert result["consensus"]["revenue"][0]["value"] == 1300
    assert original == before
    assert any("data economica/revisione" in gap for gap in result["gaps"])


def test_eps_forecast_never_labelled_diluted_and_basis_note_visible(tmp_path):
    from bellomberg.reporting import trade_idea_report as report
    from bellomberg.reporting import trade_idea_charts as charts
    from test_trade_idea_charts import _facts
    result = _facts()
    table, note = report._history_table(result, "en", report._m_styles(report._memo_styles()), 450)
    row = next(row for row in table._cellvalues if "EPS" in row[0].getPlainText())
    assert "diluted" not in row[0].getPlainText().lower()
    assert "historical diluted eps" in note.lower() and "consensus EPS basis is not attested" in note
    chart = charts._eps_path(result, str(tmp_path), "en", "ZZTEST")
    assert "basis not attested" in chart["subtitle"]
    assert any("economic comparability is not attested" in text for text in chart["notes"])
