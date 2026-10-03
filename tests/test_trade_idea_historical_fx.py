"""Historical FX qualification: deterministic synthetic observations, no network."""
import numpy as np
import pandas as pd
import pytest

from bellomberg.portfolio import portfolio_risk as risk
from bellomberg.portfolio import portfolio_montecarlo as mc


def convert(prices, currencies, fx):
    assert hasattr(risk, "historical_eur_returns"), "strict dated FX conversion absent"
    requests = []
    def fetch(symbols, **kwargs):
        requests.append((symbols, kwargs))
        return fx
    result, meta = risk.historical_eur_returns(prices, currencies, fx_fetch=fetch)
    return result, meta, requests


def test_first_return_uses_prior_close_fx_and_eur_price_identity():
    dates = pd.to_datetime(["2024-03-28", "2024-04-02", "2024-04-03"])
    prices = pd.DataFrame({"SYNUSD": [100., 110., 99.], "SYNEUR": [20., 22., 21.]}, index=dates)
    fx = pd.DataFrame({"EURUSD=X": [1.20, 1.10, 1.25]}, index=dates)
    result, meta, requests = convert(prices, {"SYNUSD":"USD", "SYNEUR":"EUR"}, fx)
    expected = (prices["SYNUSD"] / fx["EURUSD=X"]).pct_change(fill_method=None).iloc[1:]
    pd.testing.assert_series_equal(result["SYNUSD"], expected, check_names=False)
    assert result.iloc[0]["SYNUSD"] == pytest.approx(.20)
    assert result.iloc[0]["SYNEUR"] == pytest.approx(.10)
    assert meta["qualified"] is True
    assert meta["converted"] == ["SYNUSD"]
    assert meta["base_currency"] == "EUR"
    assert requests[0][1]["start"] == "2024-03-28"
    assert requests[0][1]["end"] == "2024-04-04"


@pytest.mark.parametrize("bad_rate", [None, 0., -1., float("inf")])
def test_fx_hole_never_forward_filled_or_called_qualified(bad_rate):
    dates = pd.bdate_range("2024-02-01", periods=4)
    prices = pd.DataFrame({"SYNUSD": [100.,101.,102.,103.]}, index=dates)
    fx = pd.DataFrame({"EURUSD=X": [1.1,bad_rate,1.2,1.3]}, index=dates)
    result, meta, _ = convert(prices, {"SYNUSD":"USD"}, fx)
    assert meta["qualified"] is False
    assert meta["missing_fx"]["SYNUSD"]
    assert result["SYNUSD"].isna().sum() >= 2


def test_missing_currency_blocks_even_when_eur_is_plausible():
    dates = pd.bdate_range("2024-02-01", periods=3)
    prices = pd.DataFrame({"SYN": [100.,101.,102.]}, index=dates)
    result, meta, requests = convert(prices, {}, pd.DataFrame())
    assert meta["qualified"] is False
    assert meta["missing_currency"] == ["SYN"]
    assert result["SYN"].isna().all()
    assert not requests


def test_gbx_scale_cancels_and_uses_pound_fx():
    dates = pd.bdate_range("2024-02-01", periods=3)
    prices = pd.DataFrame({"SYN.L": [10000.,10100.,10200.]}, index=dates)
    fx = pd.DataFrame({"EURGBP=X": [.85,.86,.87]}, index=dates)
    result, meta, requests = convert(prices, {"SYN.L":"GBX"}, fx)
    expected = (prices["SYN.L"] / 100 / fx["EURGBP=X"]).pct_change(fill_method=None).iloc[1:]
    pd.testing.assert_series_equal(result["SYN.L"], expected, check_names=False)
    assert requests[0][0] == ["EURGBP=X"]
    assert meta["qualified"] is True


def test_missing_local_price_is_not_a_zero_return():
    dates = pd.bdate_range("2024-02-01", periods=4)
    prices = pd.DataFrame({"SYNEUR": [100.,None,102.,103.]}, index=dates)
    result, meta, _ = convert(prices, {"SYNEUR":"EUR"}, pd.DataFrame())
    assert meta["qualified"] is False
    assert meta["missing_prices"]["SYNEUR"]
    assert result["SYNEUR"].isna().sum() == 2


def test_historical_stress_converts_actual_prices_without_proxy(monkeypatch, tmp_path):
    import inspect
    assert "currencies" in inspect.signature(mc._stress_window_returns).parameters, "stress has no historical FX path"
    dates = pd.bdate_range("2008-09-08", "2009-03-30")
    prices = pd.DataFrame({("Close", "SYNUSD"): np.linspace(100.0, 75.0, len(dates)),
                           ("Close", "SYNEUR"): np.linspace(80.0, 65.0, len(dates))}, index=dates)
    rates = pd.DataFrame({("Close", "EURUSD=X"): np.linspace(1.4, 1.2, len(dates))}, index=dates)
    calls = []
    def download(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return rates if "EURUSD=X" in symbols else prices
    monkeypatch.setattr("yfinance.download", download)
    monkeypatch.setattr("bellomberg.cli.price_updater.data_ticker_map", lambda symbols, **kw: {s:s for s in symbols})
    monkeypatch.setattr(mc, "STRESS_CACHE_DIR", str(tmp_path))
    result, meta = mc._stress_window_returns(["SYNUSD", "SYNEUR"], "gfc_2008", None,
                                               currencies={"SYNUSD":"USD", "SYNEUR":"EUR"})
    direct = (prices[("Close","SYNUSD")] / rates[("Close","EURUSD=X")]).pct_change(fill_method=None)
    pd.testing.assert_series_equal(result["SYNUSD"], direct.loc[result.index], check_names=False)
    assert meta["fx_conversion"]["qualified"] is True
    assert not meta["proxied"] and not meta["zero_filled_days"]
    assert result.index.min() >= pd.Timestamp("2008-09-15")
    assert any("EURUSD=X" in symbols for symbols, _ in calls)


def test_monte_carlo_calibration_and_stress_share_eur_basis(monkeypatch):
    import inspect
    assert "return_currencies" in inspect.signature(mc.run_monte_carlo).parameters, "Monte Carlo has no qualified EUR path"
    recent_dates = pd.bdate_range("2024-01-02", periods=90)
    stress_dates = pd.bdate_range("2008-09-08", "2009-03-30")
    calls = []
    def download(symbols, **kwargs):
        calls.append((symbols, kwargs))
        dates = stress_dates if str(kwargs.get("start", "")) < "2010" and "start" in kwargs else recent_dates
        if symbols == ["EURUSD=X"]:
            values = 1.1 + .001 * np.arange(len(dates))
            return pd.DataFrame({("Close", "EURUSD=X"): values}, index=dates)
        return pd.DataFrame({("Close", "SYNUSD"): 100 + np.arange(len(dates)),
                             ("Close", "SYNEUR"): 80 + np.sin(np.arange(len(dates)))}, index=dates)
    monkeypatch.setattr("yfinance.download", download)
    monkeypatch.setattr("bellomberg.cli.price_updater.data_ticker_map", lambda symbols, **kw: {s:s for s in symbols})
    monkeypatch.setattr(mc, "prezzi_speciali", lambda: {"origine":"file", "prezzi":{"senza_yfinance":[]}})
    result = mc.run_monte_carlo(horizon_days=252, n_sims=1000, lookback_years=1,
        method="block_bootstrap", stress_scenario="gfc_2008", seed=7, force_refresh=True,
        _override_weights={"SYNUSD":.5,"SYNEUR":.5}, _override_nav=10000,
        return_currencies={"SYNUSD":"USD","SYNEUR":"EUR"})
    assert "error" not in result, result
    assert result["fx_conversion"]["qualified"] is True
    assert result["stress_meta"]["fx_conversion"]["qualified"] is True
    assert result["stress_fallback"] is False
    # Independent price/FX identity and daily rebalanced weighted compounding.
    n = np.arange(len(stress_dates))
    local = pd.DataFrame({"usd":(100+n)/(1.1+.001*n), "eur":80+np.sin(n)}, index=stress_dates)
    daily = local.pct_change(fill_method=None).loc["2008-09-15":]
    expected = (np.prod(1 + daily.mean(axis=1)) - 1) * 100
    assert result["stress_meta"]["window_loss_pct"] == pytest.approx(round(expected,2))


def test_monte_carlo_missing_fx_returns_error_before_simulation(monkeypatch):
    import inspect
    assert "return_currencies" in inspect.signature(mc.run_monte_carlo).parameters, "Monte Carlo has no qualified EUR path"
    dates = pd.bdate_range("2024-01-02", periods=90)
    prices = pd.DataFrame({("Close","SYNUSD"):np.arange(90)+100.,
                           ("Close","SYNEUR"):np.arange(90)+80.}, index=dates)
    monkeypatch.setattr("yfinance.download", lambda symbols, **kw: pd.DataFrame() if symbols==["EURUSD=X"] else prices)
    monkeypatch.setattr("bellomberg.cli.price_updater.data_ticker_map", lambda symbols, **kw: {s:s for s in symbols})
    monkeypatch.setattr(mc, "prezzi_speciali", lambda: {"origine":"file", "prezzi":{"senza_yfinance":[]}})
    calls = []
    monkeypatch.setattr(mc, "_simulate_block_bootstrap", lambda *a, **kw: calls.append(1))
    result = mc.run_monte_carlo(method="block_bootstrap", force_refresh=True,
        _override_weights={"SYNUSD":.5,"SYNEUR":.5}, _override_nav=10000,
        return_currencies={"SYNUSD":"USD","SYNEUR":"EUR"})
    assert result.get("error")
    assert result["fx_conversion"]["qualified"] is False
    assert calls == []


def test_portfolio_risk_strict_uses_price_ratio_without_polluting_weekly_cache(monkeypatch):
    import inspect
    assert "strict_eur" in inspect.signature(risk.compute_portfolio_risk).parameters
    dates = pd.bdate_range("2024-01-02", periods=90)
    n = np.arange(90)
    prices = pd.DataFrame({("Close","SYNUSD"): 100+n+np.sin(n),
                           ("Close","SYNEUR"): 80+n+np.cos(n),
                           ("Close","SPY"): 120+n+np.sin(n/3)}, index=dates)
    rates = pd.DataFrame({("Close","EURUSD=X"):1.1+n*.001}, index=dates)
    monkeypatch.setattr("yfinance.download", lambda symbols, **kw: rates if symbols==["EURUSD=X"] else prices)
    monkeypatch.setattr("bellomberg.cli.price_updater.data_ticker_map", lambda symbols, **kw: {s:s for s in symbols})
    monkeypatch.setattr(risk, "prezzi_speciali", lambda: {"origine":"file", "prezzi":{"senza_yfinance":[]}})
    snap = {"positions":[{"ticker":"SYNUSD","valuta":"USD","valore_mercato":5000,"peso_pct":50},
                         {"ticker":"SYNEUR","valuta":"EUR","valore_mercato":5000,"peso_pct":50}]}
    class FakeDB:
        def get_portfolio_summary(self): return snap
    monkeypatch.setattr(risk, "MemoryDB", FakeDB)
    sentinel = {"weekly_cache":"untouched"}
    monkeypatch.setitem(risk._CACHE, "data", sentinel)
    result = risk.compute_portfolio_risk(force=True, strict_eur=True)
    assert "error" not in result, result
    assert result["fx_conversion"]["qualified"] is True
    direct = ((100+n+np.sin(n))/(1.1+n*.001))
    expected = pd.Series(direct).pct_change(fill_method=None).dropna().std()*np.sqrt(252)*100
    assert result["per_asset"]["SYNUSD"]["vol_annual_pct"] == pytest.approx(round(expected,2))
    assert risk._CACHE["data"] is sentinel


@pytest.mark.parametrize("fault", ["absent", "short", "unmapped"])
def test_strict_book_cannot_omit_a_small_holding(monkeypatch,fault):
    dates=pd.bdate_range("2024-01-02",periods=90)
    n=np.arange(90)
    values={("Close","SYNEUR"):80+n+np.cos(n),("Close","SPY"):120+n+np.sin(n/3)}
    if fault=="short": values[("Close","SYNMISS")]=[np.nan]*80+list(50+np.arange(10))
    prices=pd.DataFrame(values,index=dates)
    rates=pd.DataFrame({("Close","EURUSD=X"):1.1+n*.001},index=dates)
    monkeypatch.setattr("yfinance.download",lambda symbols,**kw:rates if symbols==["EURUSD=X"] else prices)
    monkeypatch.setattr("bellomberg.cli.price_updater.data_ticker_map",lambda symbols,**kw:{s:s for s in symbols})
    monkeypatch.setattr(risk,"prezzi_speciali",lambda:{"origine":"file","prezzi":{"senza_yfinance":["SYNMISS"] if fault=="unmapped" else []}})
    class FakeDB:
        def get_portfolio_summary(self):
            return {"positions":[{"ticker":"SYNMISS","valuta":"USD","valore_mercato":25.,"peso_pct":.25},
                {"ticker":"SYNEUR","valuta":"EUR","valore_mercato":9975.,"peso_pct":99.75}]}
    monkeypatch.setattr(risk,"MemoryDB",FakeDB)
    result=risk.compute_portfolio_risk(force=True,strict_eur=True)
    assert result.get("error"),result
    assert result["fx_conversion"]["qualified"] is False
    assert "portfolio" not in result


def test_qualified_eur_stress_note_is_not_local_currency_and_weekly_note_is_preserved():
    from copy import deepcopy
    from bellomberg.portfolio.sizing_engine import _stress_var_budget
    parameters={"budget_stress_nav_pct":-25.,"budget_var99_nav_pct":-4.}
    stress={"stress_scenario":"gfc_2008","stress_fallback":False,"stress_meta":{"window_loss_pct":-30.,
        "fx_conversion":{"qualified":True,"base_currency":"EUR"}}}
    result=_stress_var_budget(10000.,1000.,{},stress,parameters)
    assert result["gfc_basis_currency"]=="EUR" and "FX storico" in result["gfc_basis_note"]
    weekly=deepcopy(stress); weekly["stress_meta"].pop("fx_conversion")
    legacy=_stress_var_budget(10000.,1000.,{},weekly,parameters)
    assert legacy["gfc_basis_note"]=="replay in valuta locale per-asset (dichiarato, direzione conservativa per GFC)"
    assert "gfc_basis_currency" not in legacy
