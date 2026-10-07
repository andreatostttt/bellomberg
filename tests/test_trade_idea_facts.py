"""extract_facts: fatti numerici della run Trade Idea dalle ricevute dei tool.

Solo ticker e numeri sintetici inventati (privacy: nessun valore del book o di titoli veri).
"""

import json

from bellomberg.reporting.trade_idea_facts import extract_facts

T = "ZZTEST.MI"
OTHER = "ZZALTRO.MI"


def receipt(tool, data, *, inp=None, ts="2030-01-02T10:00:00+00:00", success=True,
            truncated=False, output=None, source=None):
    envelope = {"_source": source or ("fonte sintetica " + tool), "_timestamp": ts, "data": data}
    return {"tool": tool, "input": {"ticker": T} if inp is None else inp,
            "output": json.dumps(envelope) if output is None else output,
            "source": source or ("fonte sintetica " + tool), "success": success,
            "timestamp": ts, "truncated": truncated}


def history_data(ticker=T, fcf=None):
    return {"ticker": ticker, "years": [2027, 2028],
            "items": {"revenue": {"2027": 1000.0, "2028": 1234.5},
                      "operating_income": {"2027": 100.0, "2028": 111.0},
                      "net_income": {"2027": 50.0, "2028": 61.0},
                      "cfo": {"2027": 90.0, "2028": 95.5},
                      "capex": {"2027": 30.0, "2028": 25.5},
                      "eps_diluted": {"2027": 1.1, "2028": 1.3},
                      "cash": {"2027": 40.0, "2028": 45.0},
                      "lt_debt": {"2027": 200.0, "2028": 210.0},
                      "goodwill": {"2027": 70.0, "2028": 70.0},
                      "equity": {"2027": 300.0, "2028": 320.0},
                      "total_assets": {"2027": 900.0, "2028": 950.0},
                      "buyback": {"2028": 12.0}},
            "derived": {"fcf": {"2027": 60.0, "2028": 70.0} if fcf is None else fcf},
            "units": {"revenue": "EUR", "operating_income": "EUR", "net_income": "EUR",
                      "cfo": "EUR", "capex": "EUR", "eps_diluted": "EUR/shares", "cash": "EUR",
                      "lt_debt": "EUR", "goodwill": "EUR", "equity": "EUR",
                      "total_assets": "EUR", "buyback": "EUR"}}


def price_data(px=12.34, ticker=T):
    return {"ticker": ticker, "px": px, "var_1d": 1.5, "var_20d": -2.25, "var_60d": 8.0,
            "vs_sma20_pct": 0.8, "vs_sma50_pct": 2.1, "rsi14": 55.5, "dist_max_pct": -9.75,
            "dist_max_note": None, "price_asof": "2030-01-01 00:00:00+01:00",
            "source": "yfinance daily Close (not an intraday quote)"}


def fundamentals_data():
    return {"ticker": T, "market_cap": 5000.0, "pe_trailing": 18.5, "pe_forward": 15.25,
            "price_to_book": 2.5, "price_to_sales": 1.25, "ev_to_ebitda": 9.5,
            "operating_margin": 0.1234, "roe": 0.0875, "dividend_yield": 1.5,
            "total_cash": 400.0, "total_debt": 900.0, "operating_cashflow": 300.0,
            "free_cashflow": 120.0}


def consensus_data(currency="EUR", year_ago_revenue=1234.5):
    return {"ticker": T, "identity_symbol": T, "currency": currency, "source": "consensus sintetico",
            "data_as_of": None, "consensus_status": "partial", "consensus_reason": "data assente",
            "price_targets": {"mean": 15.5, "median": 15.0, "high": 19.0, "low": 11.0,
                              "number_of_analysts": 9, "implied_upside_pct": 25.6},
            "eps_estimates": [{"period": "0y", "avg": 1.45, "numberOfAnalysts": 8.0, "yearAgoEps": 1.3}],
            "revenue_estimates": [{"period": "0y", "avg": 1300.0, "numberOfAnalysts": 7.0,
                                   "yearAgoRevenue": year_ago_revenue}],
            "eps_revisions": [{"period": "0y", "current": 1.45, "chg_vs_30d_pct": -1.25,
                               "chg_vs_90d_pct": 2.5}],
            "recommendations": {
                "current_month": {"period": "0", "strongBuy": 2.0, "buy": 4.0, "hold": 2.0,
                                  "sell": 1.0, "strongSell": 0.0},
                "three_months_ago": {"period": "3", "strongBuy": 1.0, "buy": 5.0, "hold": 3.0,
                                     "sell": 0.0, "strongSell": 0.0}}}


def quant(operation, row, *, period="1y", params=None, tickers=None, ts="2030-01-02T10:00:00+00:00"):
    params = {"period": period} if params is None else params
    data = {"operation": operation, "tickers": tickers or [T], "period": period, "n_obs": 250,
            "errors": {}}
    if operation == "correlation_matrix":
        data["matrix"] = row
    else:
        data[T] = row
    return receipt("quant_compute", data, ts=ts,
                   inp={"operation": operation, "params": params, "tickers": tickers or [T]})


def mc_data(weight=None, var99=-21.5):
    data = {"timestamp": "2030-01-02T11:00:00", "method": "fhs", "horizon_days": 252,
            "n_sims": 5000, "lookback_days_calibration": 400, "n_assets": 6,
            "stress_scenario": "none", "var_95_pct": -15.5,
            "returns_basis": "valuta LOCALE per-asset (FX non convertito, dichiarato)", "var_99_pct": var99,
            "es_95_pct": -18.0, "es_99_pct": -24.5, "expected_return_pct": 1.5,
            "median_return_pct": 0.5, "stdev_pct": 12.5, "prob_negative_pct": 45.5,
            "percentiles_ratio": {"p5": 0.8, "p50": 1.01, "p95": 1.25},
            "weights": {"ZZBOOK.MI": 1.0}}
    if weight is not None:
        data["weights"] = {"ZZBOOK.MI": 1 - weight, T: weight}
    return data


def full_receipts():
    return [
        receipt("get_financial_history", history_data(), source="ESEF sintetico"),
        receipt("get_price_live", price_data()),
        receipt("get_fundamentals", fundamentals_data()),
        receipt("get_consensus_estimates", consensus_data()),
        quant("sharpe", {"vol_annualized_pct": 31.25, "sharpe_annualized": 0.9}),
        quant("beta", {"beta_vs_ZZBENCH": 1.15}, params={"period": "1y", "benchmark": "ZZBENCH"},
              tickers=[T]),
        quant("var_cvar", {"daily_var_pct": -4.5, "week_var_pct": -10.1, "week_cvar_pct": -12.2,
                           "confidence": 0.99}),
        quant("max_drawdown", {"max_drawdown_pct": -22.5}),
        quant("correlation_matrix", {T: {T: 1.0, "ZZBOOK.MI": 0.45, "ZZBENCH": 0.55}},
              period="6mo", tickers=[T, "ZZBOOK.MI", "ZZBENCH"]),
        receipt("get_portfolio_risk", {"timestamp": "2030-01-02T11:00:00",
                                       "beta_basis": "portafoglio EUR vs ZZBENCH convertito in EUR (fix 01/01; nota interna)",
                                       "portfolio": {"vol_annual_pct": 14.5, "beta_vs_spy": 0.85,
                                                     "var_99_1d_pct": -2.25, "max_dd_1y_pct": -9.5}},
                inp={}),
        receipt("get_portfolio_live", {"timestamp": "2030-01-02T11:00:00", "nav_total_eur": 50000.0,
                                       "cash_disponibile_eur": 2500.0, "cash_source": "sqlite:cash_state",
                                       "positions": [{"ticker": "ZZBOOK.MI"}]}, inp={}),
        # ricevute in ordine cronologico (append): la 6mo viene prima delle 1y
        receipt("compare_assets", {"period": "6mo", "comparison": [
            {"ticker": "ZZALTRAFIN.MI", "performance_periodo_pct": 1.5, "volatilita_annualizzata_pct": 9.5}]},
            inp={"period": "6mo", "tickers": ["ZZALTRAFIN.MI"]}, ts="2030-01-02T09:00:00+00:00"),
        receipt("compare_assets", {"period": "1y", "comparison": [
            {"ticker": T, "performance_periodo_pct": 22.5, "volatilita_annualizzata_pct": 31.25},
            {"ticker": "ZZBENCH", "performance_periodo_pct": 8.5, "volatilita_annualizzata_pct": 12.5}]},
            inp={"period": "1y", "tickers": [T, "ZZBENCH"]}),
        receipt("compare_assets", {"period": "1y", "comparison": [
            {"ticker": T, "performance_periodo_pct": 22.5, "volatilita_annualizzata_pct": 31.25},
            {"ticker": "ZZBOOK.MI", "performance_periodo_pct": 4.5, "volatilita_annualizzata_pct": 20.5},
            {"ticker": "ZZPEER.PA", "performance_periodo_pct": -3.5, "volatilita_annualizzata_pct": 27.5}]},
            inp={"period": "1y", "tickers": [T, "ZZBOOK.MI", "ZZPEER.PA"]}),
        receipt("get_sector_exposure", {"by_sector": [{"sector": "Settore A", "weight_pct": 60.5},
                                                      {"sector": "Settore B", "weight_pct": 39.5}],
                                        "hhi_sector": 0.5, "effective_n_sectors": 1.9,
                                        "econ_axis": {"by_bucket": [{"bucket": "Bucket A", "weight_pct": 100.0}]}},
                inp={}),
        receipt("get_portfolio_montecarlo", mc_data(), inp={"horizon_days": 252, "n_sims": 5000}),
        receipt("get_portfolio_montecarlo", mc_data(weight=0.125, var99=-26.5),
                inp={"horizon_days": 252, "n_sims": 5000, "add_tickers": [T]}),
        receipt("get_portfolio_montecarlo", mc_data(var99=-55.5),
                inp={"horizon_days": 252, "n_sims": 5000, "stress": "gfc_2008"}),
    ]


def checkpoint(receipts, thesis=None, identity=None):
    data = {"_identity": {"ticker": T, "currency": "EUR"} if identity is None else identity}
    if thesis is not None:
        data["_research_thesis"] = {"tool_receipts": thesis}
    return {"tool_receipts": receipts, "data": data, "contract": {"ticker": T}}


def has_gap(facts, *fragments):
    return any(all(f in gap for f in fragments) for gap in facts["gaps"])


# ---------------------------------------------------------------- schema

def test_schema_keys_are_exact():
    facts = extract_facts(checkpoint(full_receipts()))
    assert list(facts) == ["currency", "history", "quote", "fundamentals", "consensus", "risk",
                           "returns", "exposure", "montecarlo", "portfolio", "gaps"]
    for name in ("history", "quote", "fundamentals", "consensus", "risk", "returns",
                 "exposure", "montecarlo", "portfolio"):
        block = facts[name]
        assert {"source", "tool", "as_of"} <= set(block), name
    assert set(facts["quote"]) == {"price", "date", "change_1d_pct", "change_20d_pct",
                                   "change_60d_pct", "rsi", "from_high_pct", "ma20", "ma50",
                                   "low_52w", "high_52w", "source", "tool", "as_of"}
    assert set(facts["portfolio"]) == {"nav", "cash", "cash_pct", "source", "tool", "as_of"}


def test_full_extraction_values():
    facts = extract_facts(checkpoint(full_receipts()))
    assert facts["currency"] == "EUR"
    history = facts["history"]
    assert history["unit"] == "EUR" and history["years"] == [2027, 2028]
    assert history["source"] == "ESEF sintetico" and history["as_of"] is None
    assert history["series"]["revenue"] == {2027: 1000.0, 2028: 1234.5}
    assert history["series"]["long_term_debt"][2028] == 210.0
    assert history["series"]["fcf"] == {2027: 60.0, 2028: 70.0}      # quello del tool
    assert history["series"]["total_assets"][2027] == 900.0
    assert history["series"]["gross_profit"] == {}
    assert has_gap(facts, "Storico di bilancio", "«utile lordo»")
    assert has_gap(facts, "Storico di bilancio", "non indica la data di riferimento", "2028")
    quote = facts["quote"]
    assert quote["price"] == 12.34 and quote["date"] == "2030-01-01"
    assert quote["as_of"] == "2030-01-01T00:00:00+01:00"
    assert quote["ma20"] is None and quote["high_52w"] is None
    assert has_gap(facts, "Quotazione", "medie mobili a 20 e 50 giorni", "+0,80%", "+2,10%")
    assert facts["portfolio"]["cash_pct"] == 5.0
    assert facts["portfolio"]["nav"] == 50000.0


def test_percentages_are_normalized_to_points():
    facts = extract_facts(checkpoint(full_receipts()))
    fund = facts["fundamentals"]
    assert fund["operating_margin"] == 12.34      # frazione 0.1234 -> punti
    assert fund["roe"] == 8.75                    # frazione 0.0875 -> punti
    assert fund["dividend_yield"] == 1.5          # yfinance gia' in punti: invariato
    assert facts["quote"]["change_20d_pct"] == -2.25
    assert facts["consensus"]["upside_pct"] == 25.6
    assert facts["risk"]["candidate"]["vol_pct"] == 31.25
    assert facts["montecarlo"]["pro_forma"]["candidate_weight_pct"] == 12.5   # frazione -> punti


# ---------------------------------------------------------------- gap e ricevute

def test_missing_tool_gives_none_block_and_gap():
    receipts = [r for r in full_receipts() if r["tool"] != "get_financial_history"]
    facts = extract_facts(checkpoint(receipts))
    assert facts["history"] is None
    assert "Storico di bilancio: dato non procurato in questa run." in facts["gaps"]


def test_truncated_unparsable_output_is_declared():
    bad = receipt("get_fundamentals", None, truncated=True,
                  output='{"_source": "x", "data": {"ticker": "ZZTEST.MI", "market_ca')
    receipts = [r for r in full_receipts() if r["tool"] != "get_fundamentals"] + [bad]
    facts = extract_facts(checkpoint(receipts))
    assert facts["fundamentals"] is None
    assert has_gap(facts, "Indicatori di bilancio", "02/01/2030", "arrivata incompleta")
    assert has_gap(facts, "Indicatori di bilancio: nessuna lettura valida in questa run.")


def test_truncated_last_falls_back_to_previous_and_says_so():
    bad = receipt("get_price_live", None, truncated=True, output='{"data": {"px": 9',
                  ts="2030-01-02T12:00:00+00:00")
    facts = extract_facts(checkpoint(full_receipts() + [bad]))
    assert facts["quote"]["price"] == 12.34
    assert has_gap(facts, "Quotazione", "arrivata incompleta", "si usa quella del")


def test_receipt_of_other_ticker_is_ignored():
    later_other = receipt("get_price_live", price_data(px=99.5, ticker=OTHER),
                          inp={"ticker": OTHER}, ts="2030-01-02T12:00:00+00:00")
    facts = extract_facts(checkpoint(full_receipts() + [later_other]))
    assert facts["quote"]["price"] == 12.34
    only_other = [r for r in full_receipts() if r["tool"] != "get_price_live"] + [later_other]
    facts = extract_facts(checkpoint(only_other))
    assert facts["quote"] is None
    assert has_gap(facts, "Quotazione: nessuna lettura valida per questo titolo in questa run.")


def test_output_declaring_other_ticker_is_ignored():
    lying = receipt("get_price_live", price_data(px=77.5, ticker=OTHER), ts="2030-01-02T12:00:00+00:00")
    facts = extract_facts(checkpoint(full_receipts() + [lying]))
    assert facts["quote"]["price"] == 12.34


def test_last_valid_receipt_wins_and_failed_ones_are_skipped():
    newer = receipt("get_price_live", price_data(px=13.5), ts="2030-01-02T12:00:00+00:00")
    failed = receipt("get_price_live", price_data(px=14.75), ts="2030-01-02T13:00:00+00:00",
                     success=False)
    errored = receipt("get_price_live", {"error": "rete giu'"}, ts="2030-01-02T14:00:00+00:00")
    facts = extract_facts(checkpoint(full_receipts() + [newer, failed, errored]))
    assert facts["quote"]["price"] == 13.5


def test_thesis_only_receipts_are_older_than_top_level():
    old = receipt("get_price_live", price_data(px=11.25), ts="2030-01-01T08:00:00+00:00")
    facts = extract_facts(checkpoint(full_receipts(), thesis=[old]))
    assert facts["quote"]["price"] == 12.34
    receipts = [r for r in full_receipts() if r["tool"] != "get_price_live"]
    facts = extract_facts(checkpoint(receipts, thesis=[old]))
    assert facts["quote"]["price"] == 11.25


def test_cutoff_excludes_later_receipts():
    later = receipt("get_price_live", price_data(px=13.5), ts="2030-01-03T12:00:00+00:00")
    facts = extract_facts(checkpoint(full_receipts() + [later]), cutoff="2030-01-03T00:00:00Z")
    assert facts["quote"]["price"] == 12.34
    assert has_gap(facts, "Quotazione", "03/01/2030", "successiva alla chiusura dei dati")
    assert extract_facts(checkpoint(full_receipts() + [later]))["quote"]["price"] == 13.5


def test_fcf_is_derived_only_where_tool_lacks_it_and_both_inputs_exist():
    facts = extract_facts(checkpoint([receipt("get_financial_history",
                                              history_data(fcf={"2027": 60.0}))]))
    assert facts["history"]["series"]["fcf"] == {2027: 60.0, 2028: 70.0}   # 95.5 - 25.5
    assert has_gap(facts, "flusso di cassa libero del 2028 è calcolato qui")
    data = history_data(fcf={})
    del data["items"]["capex"]["2028"]
    facts = extract_facts(checkpoint([receipt("get_financial_history", data)]))
    assert facts["history"]["series"]["fcf"] == {2027: 60.0}


def test_consensus_in_other_currency_is_not_used():
    """Contratto D: la valuta diversa vieta il confronto target, non le stime archiviate."""
    receipts = [r for r in full_receipts() if r["tool"] != "get_consensus_estimates"]
    receipts.append(receipt("get_consensus_estimates", consensus_data(currency="USD")))
    facts = extract_facts(checkpoint(receipts))
    assert facts["consensus"]["target_mean"] is None
    assert has_gap(facts, "prezzi obiettivo non usati", "USD", "EUR")


def test_consensus_in_other_currency_keeps_estimates_but_not_target_comparison():
    """Le osservazioni originali restano leggibili con metadati valuta non inventati."""
    receipts = [r for r in full_receipts() if r["tool"] != "get_consensus_estimates"]
    receipts.append(receipt("get_consensus_estimates", consensus_data(currency="USD")))
    facts = extract_facts(checkpoint(receipts))
    assert facts["consensus"]["target_values"]["mean"] == 15.5
    assert facts["consensus"]["eps"][0]["value"] == 1.45
    assert facts["consensus"]["eps"][0]["currency"] is None


def test_consensus_details():
    facts = extract_facts(checkpoint(full_receipts()))
    cons = facts["consensus"]
    assert cons["analysts"] == 9 and cons["status"] == "partial" and cons["as_of"] is None
    assert len(cons["eps"]) == 1
    assert {key: cons["eps"][0][key] for key in ("period", "value", "analysts")} == {
        "period": "0y", "value": 1.45, "analysts": 8}
    assert cons["eps"][0]["currency"] is None and cons["eps"][0]["period_end"] is None
    assert cons["eps"][0]["yearAgoEps"] == 1.3
    assert cons["revenue"][0]["analysts"] == 7
    assert cons["eps_revisions"] == [{"period": "0y", "current": 1.45,
                                      "change_30d_pct": -1.25, "change_90d_pct": 2.5}]
    assert cons["recommendations"] == {"strong_buy": 2, "buy": 4, "hold": 2, "sell": 1, "strong_sell": 0}
    assert cons["recommendations_prev"]["hold"] == 3
    assert cons["recommendations_period"] == {"current": "0", "prev": "3"}
    assert has_gap(facts, "Consensus degli analisti", "non indica a che data")


def test_risk_candidate_and_book():
    facts = extract_facts(checkpoint(full_receipts()))
    cand, book = facts["risk"]["candidate"], facts["risk"]["book"]
    assert cand["beta"] == 1.15 and cand["var99_1d_pct"] == -4.5
    assert cand["var99_1w_pct"] == -10.1 and cand["es99_1w_pct"] == -12.2
    assert cand["max_drawdown_pct"] == -22.5 and cand["observations"] is None
    assert cand["correlations"] == [{"ticker": "ZZBOOK.MI", "corr": 0.45},
                                    {"ticker": "ZZBENCH", "corr": 0.55}]
    assert cand["correlations_window"] == "6mo"
    assert book == {"vol_pct": 14.5, "beta": 0.85, "var99_1d_pct": -2.25, "max_drawdown_pct": -9.5,
                    "beta_basis": "portafoglio EUR vs ZZBENCH convertito in EUR",
                    "vol_target_pct": None, "source": "fonte sintetica get_portfolio_risk",
                    "tool": "get_portfolio_risk", "as_of": "2030-01-02T11:00:00"}
    assert has_gap(facts, "Volatilità obiettivo", "mandato del PM")
    assert "ZZBENCH" in cand["beta_basis"] and "senza cambio" in cand["beta_basis"]
    assert has_gap(facts, "I due beta sono calcolati su basi diverse")


def test_var_at_other_confidence_is_not_passed_as_99():
    receipts = [r for r in full_receipts()
                if not (r["tool"] == "quant_compute" and r["input"]["operation"] == "var_cvar")]
    receipts.append(quant("var_cvar", {"daily_var_pct": -3.25, "week_var_pct": -7.5,
                                       "week_cvar_pct": -9.0, "confidence": 0.95}))
    facts = extract_facts(checkpoint(receipts))
    assert facts["risk"]["candidate"]["var99_1d_pct"] is None
    assert facts["risk"]["candidate"]["es99_1w_pct"] is None
    assert has_gap(facts, "il VaR è disponibile solo con confidenza 95%")


def test_returns_merge_same_window_with_roles():
    facts = extract_facts(checkpoint(full_receipts()))
    ret = facts["returns"]
    assert ret["window"] == "1y"
    roles = {a["ticker"]: a["role"] for a in ret["assets"]}
    assert roles == {T: "candidate", "ZZBENCH": "benchmark", "ZZBOOK.MI": "holding", "ZZPEER.PA": None}
    assert "ZZALTRAFIN.MI" not in roles                     # finestra 6mo esclusa
    assert "unione di 2 confronti" in ret["note"] and "esclusi 1 confronti" in ret["note"]
    assert has_gap(facts, "Confronto dei rendimenti", "ZZPEER.PA", "non è registrato il ruolo")
    cand = next(a for a in ret["assets"] if a["ticker"] == T)
    assert cand == {"ticker": T, "return_pct": 22.5, "vol_pct": 31.25, "role": "candidate"}


def test_exposure_block():
    facts = extract_facts(checkpoint(full_receipts()))
    exp = facts["exposure"]
    assert exp["sectors"] == [{"name": "Settore A", "weight_pct": 60.5},
                              {"name": "Settore B", "weight_pct": 39.5}]
    assert exp["econ_buckets"] == [{"name": "Bucket A", "weight_pct": 100.0}]
    assert exp["tool"] == "get_sector_exposure"
    receipts = [r for r in full_receipts() if r["tool"] != "get_sector_exposure"]
    facts = extract_facts(checkpoint(receipts))
    assert facts["exposure"] is None
    assert "Esposizione per settore: dato non procurato in questa run." in facts["gaps"]


def test_montecarlo_book_and_pro_forma_never_mixed_and_stress_ignored():
    facts = extract_facts(checkpoint(full_receipts()))
    mc = facts["montecarlo"]
    assert mc["book"]["var_99_pct"] == -21.5            # non il what-if, non lo stress (-55.5)
    assert mc["pro_forma"]["var_99_pct"] == -26.5
    assert mc["book"]["horizon_days"] == 252 and mc["book"]["observations"] == 400
    assert mc["book"]["terminal_ratio_percentiles"]["p50"] == 1.01
    assert "candidate_weight_pct" not in mc["book"]
    receipts = [r for r in full_receipts() if not (r["tool"] == "get_portfolio_montecarlo"
                                                   and r["input"].get("add_tickers"))]
    facts = extract_facts(checkpoint(receipts))
    assert facts["montecarlo"]["pro_forma"] is None
    assert has_gap(facts, "Monte Carlo con il titolo aggiunto: nessuna lettura valida")


def test_cash_without_declared_source_is_a_gap_not_zero():
    receipts = [r for r in full_receipts() if r["tool"] != "get_portfolio_live"]
    receipts.append(receipt("get_portfolio_live", {"nav_total_eur": 50000.0,
                                                   "cash_disponibile_eur": 0.0, "cash_source": None},
                            inp={}))
    facts = extract_facts(checkpoint(receipts))
    assert facts["portfolio"]["cash"] is None and facts["portfolio"]["cash_pct"] is None
    assert has_gap(facts, "Portafoglio", "la cassa non ha una fonte registrata")


def test_without_verified_ticker_no_security_blocks():
    facts = extract_facts(checkpoint(full_receipts(), identity={"ticker": OTHER, "currency": "EUR"}))
    assert facts["quote"] is None and facts["history"] is None and facts["risk"] is None
    assert has_gap(facts, "Identità del titolo incoerente")
    assert facts["portfolio"] is not None


def test_non_dict_checkpoint():
    facts = extract_facts(None)
    assert facts["history"] is None and facts["montecarlo"] is None
    assert facts["gaps"] == ["La run non contiene dati leggibili: nessun numero estratto."]


def test_input_of_other_ticker_is_ignored_even_if_output_is_silent():
    silent = price_data(px=88.5)
    del silent["ticker"]
    later = receipt("get_price_live", silent, inp={"ticker": OTHER}, ts="2030-01-02T12:00:00+00:00")
    facts = extract_facts(checkpoint(full_receipts() + [later]))
    assert facts["quote"]["price"] == 12.34


# ---------------------------------------------------------------- review 04/10 e frasi per il PM

TOOL_NAMES = ("get_", "quant_compute", "compare_assets", "_pct", "cash_source", "ma20", "n_obs",
              "cutoff", "ricevuta", "payload", "JSON")


def _all_gap_cases():
    yield extract_facts(checkpoint(full_receipts()))
    bad = receipt("get_price_live", None, truncated=True, output='{"data": {"px": 9',
                  ts="2030-01-02T12:00:00+00:00")
    later = receipt("get_price_live", price_data(px=13.5), ts="2030-01-03T12:00:00+00:00")
    yield extract_facts(checkpoint(full_receipts() + [bad, later]), cutoff="2030-01-03T00:00:00Z")
    yield extract_facts(checkpoint([]))


def test_gaps_are_plain_italian_without_tool_names_or_iso_dates():
    import re
    for facts in _all_gap_cases():
        for gap in facts["gaps"]:
            assert not any(name in gap for name in TOOL_NAMES), gap
            assert not re.search(r"\d{4}-\d{2}-\d{2}", gap), gap
            assert gap.endswith("."), gap
        assert len(facts["gaps"]) == len(set(facts["gaps"]))


def test_montecarlo_note_shared_by_book_and_pro_forma_appears_once():
    facts = extract_facts(checkpoint(full_receipts()))
    hits = [g for g in facts["gaps"] if "simulati nella valuta di ciascun titolo" in g]
    assert len(hits) == 1


def test_montecarlo_calibration_note_is_rewritten_with_its_numbers():
    data = mc_data()
    data["calibration_note"] = ("campione di calibrazione TAGLIATO a 150 obs dal ticker piu' giovane "
                                "(ZZGIOVANE: 160 obs su 1200 del panel 5y): vol e correlazioni stimate "
                                "su finestra corta")
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", data, inp={})]))
    assert has_gap(facts, "calibrato su soli 150 giorni", "(ZZGIOVANE)", "160 giorni su 1200",
                   "5 anni", "finestra breve")


def test_fiscal_year_of_estimates_only_when_revenue_matches_history():
    facts = extract_facts(checkpoint(full_receipts()))
    cons = facts["consensus"]
    assert cons["year_ago_revenue"] == 1234.5 and cons["year_ago_eps"] == 1.3
    assert cons["current_fiscal_year"] is None  # metadata valuta assenti: match numerico non basta
    assert has_gap(facts, "ricavi non confrontabili")
    assert not has_gap(facts, "manca almeno un esercizio")
    receipts = [r for r in full_receipts() if r["tool"] != "get_consensus_estimates"]
    comparable = consensus_data()
    comparable["revenue_estimates"][0]["currency"] = "EUR"
    inferred = extract_facts(checkpoint(receipts + [receipt("get_consensus_estimates", comparable)]))
    assert inferred["consensus"]["current_fiscal_year"] == 2029
    assert inferred["consensus"]["fiscal_year_basis"] == "revenue_match_inference"
    comparable["revenue_estimates"][0]["yearAgoRevenue"] = 1500.0
    facts = extract_facts(checkpoint(receipts + [receipt("get_consensus_estimates", comparable)]))
    assert facts["consensus"]["current_fiscal_year"] is None   # non si indovina
    assert facts["consensus"]["year_ago_revenue"] == 1500.0
    assert has_gap(facts, "non riconciliato")
    assert not has_gap(facts, "manca almeno un esercizio")


def test_book_montecarlo_is_the_run_closest_to_the_pro_forma():
    def mc(var99, ts, add=False):
        inp = {"horizon_days": 252, "n_sims": 5000}
        if add:
            inp["add_tickers"] = [T]
        return receipt("get_portfolio_montecarlo", mc_data(weight=0.1 if add else None, var99=var99),
                       inp=inp, ts=ts)
    early = mc(-21.5, "2030-01-02T09:00:00+00:00")
    pro = mc(-26.5, "2030-01-02T09:10:00+00:00", add=True)
    late = mc(-30.5, "2030-01-02T12:00:00+00:00")
    facts = extract_facts(checkpoint([early, pro, late]))
    assert facts["montecarlo"]["book"]["var_99_pct"] == -21.5
    facts = extract_facts(checkpoint([early, late]))
    assert facts["montecarlo"]["book"]["var_99_pct"] == -30.5   # senza pro-forma: l'ultima


def test_fundamentals_currency_when_declared():
    facts = extract_facts(checkpoint(full_receipts()))
    assert facts["fundamentals"]["currency"] is None
    assert has_gap(facts, "Indicatori di bilancio", "né la valuta degli importi")
    data = fundamentals_data()
    data["financialCurrency"] = "EUR"
    receipts = [r for r in full_receipts() if r["tool"] != "get_fundamentals"]
    facts = extract_facts(checkpoint(receipts + [receipt("get_fundamentals", data)]))
    assert facts["fundamentals"]["currency"] is None  # financialCurrency non qualifica market cap
    assert facts["fundamentals"]["cashflow_metadata"]["free_cashflow"]["provider_financial_currency"] == "EUR"
    assert has_gap(facts, "periodo, definizione o valuta", "non attestati")
    data["market_cap_currency"] = "GBP"
    facts = extract_facts(checkpoint(receipts + [receipt("get_fundamentals", data)]))
    assert facts["fundamentals"]["currency"] == "GBP"


def test_non_dict_params_do_not_break_risk_or_benchmark():
    beta = receipt("quant_compute", {"operation": "beta", "tickers": [T], "period": "1y",
                                     T: {"beta_vs_SPY": 1.25}},
                   inp={"operation": "beta", "tickers": [T], "params": "ZZBENCH"})
    facts = extract_facts(checkpoint([beta]))
    assert facts["risk"]["candidate"]["beta"] == 1.25          # params illeggibili: SPY di default del tool


def test_years_accepted_only_as_list():
    data = history_data()
    data["years"] = "2027"
    facts = extract_facts(checkpoint([receipt("get_financial_history", data)]))
    assert facts["history"]["years"] == [2027, 2028]           # dalle serie, non dai caratteri


def test_fx_incomplete_string_is_not_split_and_zero_nav_is_explained():
    live = receipt("get_portfolio_live", {"nav_total_eur": 0, "cash_disponibile_eur": 5.0,
                                          "cash_source": "sintetica", "fx_incomplete": "USD",
                                          "positions": {"non": "lista"}}, inp={})
    facts = extract_facts(checkpoint([live]))
    assert facts["portfolio"]["cash_pct"] is None
    assert has_gap(facts, "manca il cambio per USD,")
    assert has_gap(facts, "il valore totale risulta zero")
