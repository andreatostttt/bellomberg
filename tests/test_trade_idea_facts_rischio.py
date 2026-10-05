"""extract_facts, voce 7 D (04/10, Opus 5.5): campi di rischio nuovi del memo Trade Idea.

- beta del book contro SPY: beta_error / beta_obs (mai 0 o 1 inventati), beta Fama-French
  del desk etichettato con la SUA base;
- Monte Carlo: campione di calibrazione, avviso code, allocazione what-if del pro-forma
  (l'ES al 99% esce sempre con la quota simulata e la sua origine);
- backtest del VaR: verdetto per asse, peso escluso, bassa potenza, NON AFFIDABILE.
Payload vecchi (run salvate prima della cura): «n.d. (run precedente alla cura)».

Solo ticker e numeri inventati (privacy: nessun valore del book o di titoli veri).
"""

import json
import re

from bellomberg.reporting.trade_idea_facts import extract_facts

T = "ZZRISK.MI"
ANTE = "n.d. (run precedente alla cura)"


def receipt(tool, data, *, inp=None, ts="2030-01-02T10:00:00+00:00"):
    envelope = {"_source": "fonte sintetica " + tool, "_timestamp": ts, "data": data}
    return {"tool": tool, "input": {} if inp is None else inp, "output": json.dumps(envelope),
            "source": "fonte sintetica " + tool, "success": True, "timestamp": ts, "truncated": False}


def checkpoint(receipts):
    return {"tool_receipts": receipts, "data": {"_identity": {"ticker": T, "currency": "EUR"}},
            "contract": {"ticker": T}}


def has_gap(facts, *fragments):
    return any(all(f in gap for f in fragments) for gap in facts["gaps"])


def gaps_with(facts, fragment):
    return [g for g in facts["gaps"] if fragment in g]


# ---------------------------------------------------------------- beta del book

def risk_data(beta, *, new=True, error=None, obs=123, ts="2030-01-02T11:00:00"):
    data = {"timestamp": ts, "beta_basis": "portafoglio EUR vs SPY convertito in EUR",
            "portfolio": {"vol_annual_pct": 17.25, "beta_vs_spy": beta,
                          "var_99_1d_pct": -2.75, "max_dd_1y_pct": -11.5}}
    if new:
        data["beta_error"] = error
        data["beta_obs"] = obs
    return data


def factors_data(beta_market=0.873):
    return {"timestamp": "2030-01-02T11:30:00", "period": "1y",
            "model": "Fama-French 5-factor + Momentum (Carhart) REGIONALE + optional BTC factor",
            "portfolio_aggregate": {"beta_market": beta_market, "beta_smb": 0.125},
            "portfolio_avg_r_squared": 0.315, "coverage_weight_pct": 97.5}


def test_beta_non_misurato_va_nei_gap_col_motivo_mai_zero_o_uno():
    why = "SPY non disponibile: il download del benchmark non ha restituito prezzi, beta del book non misurato"
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(None, error=why, obs=0))]))
    risk = facts["risk"]
    assert risk["book"]["beta"] is None
    assert risk["book_beta"]["value"] is None and risk["book_beta"]["error"] == why
    assert risk["book_beta"]["observations"] == 0
    assert has_gap(facts, "Rischio del portafoglio", "non misurato", "SPY non disponibile")
    # niente frase generica «non è disponibile il dato beta» accanto a quella col motivo
    assert not has_gap(facts, "Rischio del portafoglio", "«beta»")


def test_beta_none_senza_motivo_dichiara_motivo_non_dichiarato():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(None, error=None, obs=0))]))
    assert facts["risk"]["book"]["beta"] is None
    assert has_gap(facts, "beta contro l'indice SPY non misurato", "motivo non dichiarato")


def test_beta_misurato_formato_nuovo_porta_le_osservazioni():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(1.137, obs=234))]))
    bb = facts["risk"]["book_beta"]
    assert facts["risk"]["book"]["beta"] == 1.137
    assert bb["value"] == 1.137 and bb["observations"] == 234 and bb["error"] is None
    assert bb["observations_status"] is None and bb["benchmark"] == "SPY" and "SPY" in bb["label"]
    assert not gaps_with(facts, "non misurato")


def test_beta_formato_vecchio_misurato_resta_con_osservazioni_nd():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(0.85, new=False))]))
    bb = facts["risk"]["book_beta"]
    assert bb["value"] == 0.85 and facts["risk"]["book"]["beta"] == 0.85
    assert bb["observations"] is None and bb["observations_status"] == ANTE


def test_beta_zero_formato_vecchio_non_e_una_misura():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(0.0, new=False))]))
    assert facts["risk"]["book"]["beta"] is None and facts["risk"]["book_beta"]["value"] is None
    assert has_gap(facts, "non misurato", "versione precedente alla cura", ANTE)


def test_beta_zero_formato_nuovo_e_una_misura_vera():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(0.0, obs=210))]))
    assert facts["risk"]["book"]["beta"] == 0.0
    assert not gaps_with(facts, "non misurato")


def test_lettura_non_misurata_precedente_e_dichiarata_ma_vince_la_misurata_recente():
    old = receipt("get_portfolio_risk", risk_data(None, error="SPY insufficiente: 15 giorni", obs=15),
                  ts="2030-01-02T08:00:00+00:00")
    new = receipt("get_portfolio_risk", risk_data(1.234, obs=240), ts="2030-01-02T10:00:00+00:00")
    facts = extract_facts(checkpoint([old, new]))
    assert facts["risk"]["book"]["beta"] == 1.234
    assert has_gap(facts, "nelle letture del 02/01/2030", "non risultava misurato", "che lo misura")


def test_lettura_misurata_precedente_non_si_nasconde_ne_si_mescola():
    old = receipt("get_portfolio_risk", risk_data(1.234, obs=240), ts="2030-01-02T08:00:00+00:00")
    new = receipt("get_portfolio_risk", risk_data(None, error="SPY non disponibile", obs=0),
                  ts="2030-01-02T10:00:00+00:00")
    facts = extract_facts(checkpoint([old, new]))
    assert facts["risk"]["book"]["beta"] is None
    assert has_gap(facts, "era stato misurato", "(1,23)", "non lo misura", "non si mescolano")


def test_beta_fattoriale_e_spy_etichettati_con_la_loro_base():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(1.137, obs=234)),
                                      receipt("get_portfolio_factors", factors_data(), inp={"period": "1y"})]))
    ff = facts["risk"]["book_factor_beta"]
    assert ff["value"] == 0.873 and ff["tool"] == "get_portfolio_factors"
    assert "Fama-French regionale" in ff["label"] and "non contro l'indice SPY" in ff["label"]
    assert "SPY" in facts["risk"]["book_beta"]["label"]
    assert has_gap(facts, "due beta su basi diverse", "1,14 contro l'indice SPY",
                   "0,87 sul fattore di mercato", "non sono in contraddizione")


def test_beta_fattoriale_non_sostituisce_lo_spy_mancante():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(None, error="SPY non disponibile", obs=0)),
                                      receipt("get_portfolio_factors", factors_data())]))
    assert facts["risk"]["book"]["beta"] is None            # nessun proxy dal fattore
    assert has_gap(facts, "non sostituisce il beta contro l'indice SPY", "0,87")


def test_senza_ricevuta_dei_fattori_nessun_blocco_e_nessun_gap():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(1.137, obs=234))]))
    assert facts["risk"]["book_factor_beta"] is None
    assert not gaps_with(facts, "Fattori di rischio")


def test_il_controllo_di_integrita_riconosce_il_beta_fattoriale():
    from bellomberg.reporting.trade_idea_report import _known_values
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(1.137, obs=234)),
                                      receipt("get_portfolio_factors", factors_data())]))
    known = _known_values(facts)
    assert 0.873 in known and 1.137 in known


# ---------------------------------------------------------------- Monte Carlo

def mc_data(*, es99=-24.5, weights=None, alloc=None, sample=None, warning=None, note=None):
    data = {"timestamp": "2030-01-02T11:00:00", "method": "fhs", "horizon_days": 252, "n_sims": 5000,
            "lookback_days_calibration": (sample or {}).get("n_obs", 400), "n_assets": 6,
            "stress_scenario": "none", "var_95_pct": -15.5, "var_99_pct": -21.5,
            "es_95_pct": -18.0, "es_99_pct": es99, "weights": weights or {"ZZBOOK.MI": 1.0}}
    if alloc is not None:
        data["what_if_allocation"] = alloc
    if sample is not None:
        data["calibration_sample"] = sample
        data["tail_reliability_warning"] = warning
    if note is not None:
        data["calibration_note"] = note
    return data


def sample(n_obs=192, limiting="ZZYNG", dropped_limit=400, dropped_cal=8):
    return {"n_obs": n_obs, "start_date": "2029-03-05", "end_date": "2030-01-01",
            "approx_months": round(n_obs / 21, 1), "panel_obs": n_obs + dropped_limit + dropped_cal,
            "limiting_ticker": limiting, "limiting_tickers": [limiting] if limiting else [],
            "limiting_ticker_start_date": "2029-03-01" if limiting else None,
            "obs_dropped_by_limiting_ticker": dropped_limit, "obs_dropped_by_calendars": dropped_cal,
            "min_obs_reliable_tails": 250, "low_tail_reliability": n_obs < 250,
            "sentence": "calibrazione su circa 9 mesi (192 osservazioni, dal 2029-03-05 al 2030-01-01)"}


def stress_alloc(effective=30.0, requested=30.0, skipped=(), without=()):
    return {"added_weight_pct_requested": requested, "origin": "stress_fisso", "is_proposed_size": False,
            "note": "valore fisso di stress 30% del book: NON e' la size proposta",
            "added_tickers_applied": [T], "added_tickers_skipped": list(skipped),
            "added_weight_pct_effective": effective, "added_weight_pct_by_ticker": {T: effective},
            "added_tickers_without_returns": list(without)}


def proforma(data):
    return receipt("get_portfolio_montecarlo", data, inp={"horizon_days": 252, "add_tickers": [T]})


def test_pro_forma_usa_il_peso_effettivo_e_dichiara_lo_stress_fisso():
    # weights dice 0.3333 (rinormalizzazione vecchia): vale il peso EFFETTIVO dichiarato
    data = mc_data(es99=-50.25, weights={"ZZBOOK.MI": 0.6667, T: 0.3333}, alloc=stress_alloc(),
                   sample=sample(n_obs=300, limiting=None, dropped_limit=0))
    facts = extract_facts(checkpoint([proforma(data)]))
    pro = facts["montecarlo"]["pro_forma"]
    assert pro["candidate_weight_pct"] == 30.0
    assert pro["allocation"]["origin"] == "stress_fisso" and pro["allocation"]["is_proposed_size"] is False
    assert pro["allocation"]["status"] is None
    assert has_gap(facts, "Monte Carlo con il titolo aggiunto", "ES al 99%", "-50,25%",
                   "titolo al 30,00% del portafoglio", "NON la size proposta")


def test_pro_forma_con_la_size_del_chiamante():
    alloc = dict(stress_alloc(effective=2.5, requested=2.5), origin="parametro_chiamante", is_proposed_size=True,
                 note="quota 2.5% del book passata dal chiamante")
    facts = extract_facts(checkpoint([proforma(mc_data(alloc=alloc, sample=sample(n_obs=300)))]))
    pro = facts["montecarlo"]["pro_forma"]
    assert pro["candidate_weight_pct"] == 2.5 and pro["allocation"]["is_proposed_size"] is True
    es = gaps_with(facts, "ES al 99%")
    assert len(es) == 1 and "titolo al 2,50%" in es[0] and "come size proposta" in es[0]
    assert "NON la size proposta" not in es[0]


def test_pro_forma_vecchio_formato_dichiara_origine_nd():
    data = mc_data(weights={"ZZBOOK.MI": 0.7, T: 0.3})
    facts = extract_facts(checkpoint([proforma(data)]))
    pro = facts["montecarlo"]["pro_forma"]
    assert pro["candidate_weight_pct"] == 30.0               # dai pesi della simulazione, come prima
    assert pro["allocation"]["status"] == ANTE and pro["allocation"]["origin"] is None
    assert pro["calibration"]["status"] == ANTE and pro["calibration"]["observations"] is None
    assert has_gap(facts, "ES al 99%", "titolo al 30,00%", ANTE, "non è registrato se sia la size proposta")


def test_es99_del_pro_forma_mai_senza_quota_neanche_senza_peso():
    alloc = stress_alloc()
    alloc["added_weight_pct_effective"] = None
    facts = extract_facts(checkpoint([proforma(mc_data(alloc=alloc, sample=sample(n_obs=300)))]))
    assert has_gap(facts, "ES al 99%", "peso del titolo non indicato", "NON la size proposta")
    assert has_gap(facts, "non è indicato il peso dato al titolo")


def test_quota_richiesta_diversa_e_ticker_saltati_sono_dichiarati():
    alloc = stress_alloc(effective=27.5, requested=30.0, skipped=["ZZSKIP"], without=[T])
    facts = extract_facts(checkpoint([proforma(mc_data(alloc=alloc, sample=sample(n_obs=300)))]))
    assert has_gap(facts, "quota richiesta 30,00%", "peso effettivo nella simulazione 27,50%")
    assert has_gap(facts, "ZZSKIP", "lista delle posizioni escluse")
    assert has_gap(facts, T, "peso effettivo nella simulazione è zero")


def test_campione_corto_avviso_code_e_frase_con_date_italiane():
    note = ("campione di calibrazione TAGLIATO a 192 obs dal ticker piu' giovane (ZZYNG: 200 obs su 600 "
            "del panel 5y): vol; calibrazione su circa 9 mesi")
    data = mc_data(sample=sample(), warning="BASSA AFFIDABILITA' DELLE CODE: 192 osservazioni", note=note)
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", data, inp={})]))
    book = facts["montecarlo"]["book"]
    assert book["calibration"]["observations"] == 192 and book["calibration"]["low_tail_reliability"] is True
    assert book["calibration"]["limiting_ticker"] == "ZZYNG" and book["tail_reliability_warning"]
    assert has_gap(facts, "calibrato su 192 giorni", "dal 05/03/2029 al 01/01/2030", "limitata da ZZYNG",
                   "400 giorni in meno", "altri 8 giorni tolti", "calendari di borsa")
    assert has_gap(facts, "code poco affidabili", "192 osservazioni", "soglia di 250", "VaR ed ES al 99%")
    assert not gaps_with(facts, "calibrato su soli")       # la regex vecchia non duplica


def test_campione_lungo_nessun_avviso_sulle_code():
    data = mc_data(sample=sample(n_obs=600, limiting=None, dropped_limit=0, dropped_cal=4))
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", data, inp={})]))
    assert facts["montecarlo"]["book"]["calibration"]["low_tail_reliability"] is False
    assert not gaps_with(facts, "code poco affidabili") and not gaps_with(facts, "calibrato su")


def test_avviso_del_tool_basta_anche_senza_il_flag():
    s = sample(n_obs=300)
    s["low_tail_reliability"] = None
    data = mc_data(sample=s, warning="BASSA AFFIDABILITA' DELLE CODE")
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", data, inp={})]))
    assert has_gap(facts, "Monte Carlo del portafoglio attuale: code poco affidabili")


def test_pro_forma_con_code_corte_lo_dice_accanto_all_es99():
    facts = extract_facts(checkpoint([proforma(mc_data(alloc=stress_alloc(), sample=sample(),
                                                       warning="BASSA AFFIDABILITA'"))]))
    assert has_gap(facts, "ES al 99%", "titolo al 30,00%", "code poco affidabili", "192 osservazioni")


# ---------------------------------------------------------------- backtest del VaR

def axis(verdict, p, test="Kupiec POF (chi2 1 gdl)"):
    return {"verdict": verdict, "p_value": p, "test": test, "p_value_chi2": p}


def level(cov, ind, *, verdict=None, exc=5, expected=5.0, low_power=False, detail="dettaglio del tool"):
    combined = verdict or ("PASS" if cov == "PASS" and ind == "PASS" else "FAIL")
    return {"kupiec_pof": {"exceptions": exc, "expected": expected, "p_value": 0.5, "pass_5pct": cov == "PASS"},
            "christoffersen_ind": {"p_value": 0.5, "pass_5pct": ind == "PASS", "consecutive_exceptions": 1},
            "coverage": axis(cov, 0.6125 if cov == "PASS" else 0.0125),
            "independence": axis(ind, 0.7125 if ind == "PASS" else 0.0375),
            "verdict": combined, "verdict_detail": detail, "expected_exceptions": expected, "low_power": low_power}


def bt_data(var95, var99, *, excluded=(), weight=0.0, reliable=True):
    return {"timestamp": "2030-01-02T11:00:00", "window": 252, "period": "3y", "n_obs_tested": 495,
            "excluded_tickers": [e["ticker"] for e in excluded], "excluded_detail": list(excluded),
            "excluded_weight_pct": weight, "excluded_weight_threshold_pct": 10.0, "reliable": reliable,
            "var95": var95, "var99": var99}


def bt(data):
    return receipt("get_var_backtest", data, inp={"window": 252, "period": "3y"})


def test_fail_solo_indipendenza_con_bassa_potenza_dice_asse_e_peso():
    data = bt_data(level("PASS", "PASS", exc=25, expected=24.8),
                   level("PASS", "FAIL", exc=5, expected=5.0, low_power=True))
    facts = extract_facts(checkpoint([bt(data)]))
    b = facts["risk"]["var_backtest"]
    assert b["reliable"] is True and b["excluded_weight_pct"] == 0.0 and b["perimeter_status"] is None
    lv = b["levels"]["99"]
    assert lv["verdict"] == "FAIL" and lv["independence"]["verdict"] == "FAIL" and lv["coverage"]["verdict"] == "PASS"
    assert lv["low_power"] is True and lv["verdict_detail"] == "dettaglio del tool"
    assert has_gap(facts, "Backtest del VaR del portafoglio al 99%", "FAIL sull'asse indipendenza",
                   "a grappoli", "copertura PASS (p 0,6125)", "indipendenza FAIL (p 0,0375)",
                   "5 eccezioni contro 5,0 attese", "bassa potenza", "titoli esclusi pari al 0,00% del perimetro")
    assert not gaps_with(facts, "al 95%")                    # PASS pieno: nessuna riga


def test_fail_solo_copertura():
    facts = extract_facts(checkpoint([bt(bt_data(level("FAIL", "PASS", exc=40, expected=24.8),
                                                 level("PASS", "PASS")))]))
    assert has_gap(facts, "al 95%", "FAIL sull'asse copertura", "non è coerente con il livello di confidenza")


def test_non_affidabile_dichiarato_con_peso_e_nomi_esclusi():
    excluded = [{"ticker": "ZZAAA", "motivo": "serie vuota (download fallito)", "obs_valide": 0, "peso_pct": 9.0},
                {"ticker": "ZZBBB", "motivo": "serie assente nel download", "obs_valide": 0, "peso_pct": 6.0}]
    data = bt_data(level("PASS", "PASS", verdict="NON AFFIDABILE"), level("PASS", "FAIL", verdict="NON AFFIDABILE"),
                   excluded=excluded, weight=15.0, reliable=False)
    facts = extract_facts(checkpoint([bt(data)]))
    b = facts["risk"]["var_backtest"]
    assert b["reliable"] is False and b["excluded_tickers"] == ["ZZAAA", "ZZBBB"]
    assert b["excluded_detail"][0] == {"ticker": "ZZAAA", "reason": "serie vuota (download fallito)",
                                       "valid_obs": 0, "weight_pct": 9.0}
    assert has_gap(facts, "NON AFFIDABILE, i titoli esclusi pesano il 15,00%", "(soglia 10%)", "non valida il VaR")
    assert has_gap(facts, "esclusi ZZAAA (serie vuota (download fallito)) e ZZBBB", "15,00% del perimetro")
    assert has_gap(facts, "al 95%: NON AFFIDABILE") and has_gap(facts, "al 99%: NON AFFIDABILE")


def test_backtest_formato_vecchio_assi_dai_test_e_perimetro_nd():
    old95 = {"kupiec_pof": {"exceptions": 27, "expected": 24.8, "p_value": 0.6475, "pass_5pct": True},
             "christoffersen_ind": {"p_value": 0.0125, "pass_5pct": False, "consecutive_exceptions": 5},
             "verdict": "FAIL"}
    old99 = {"kupiec_pof": {"exceptions": 5, "expected": 5.0, "p_value": 0.9825, "pass_5pct": True},
             "christoffersen_ind": {"p_value": 0.0375, "pass_5pct": False}, "verdict": "FAIL",
             "note": "verdetto indicativo"}
    data = {"timestamp": "2030-01-02T11:00:00", "window": 252, "period": "3y", "n_obs_tested": 495,
            "excluded_tickers": [], "var95": old95, "var99": old99}
    facts = extract_facts(checkpoint([bt(data)]))
    b = facts["risk"]["var_backtest"]
    assert b["perimeter_status"] == ANTE and b["reliable"] is None and b["excluded_weight_pct"] is None
    lv = b["levels"]["95"]
    assert lv["coverage"]["verdict"] == "PASS" and lv["independence"]["verdict"] == "FAIL"
    assert lv["detail_status"] == ANTE and lv["low_power"] is None and lv["verdict_detail"] is None
    assert has_gap(facts, "versione precedente alla cura", "potevano risultare inclusi")
    assert has_gap(facts, "al 95%", "FAIL sull'asse indipendenza", "potenza del test " + ANTE,
                   "peso dei titoli esclusi " + ANTE)


def test_fail_vecchio_senza_test_dichiara_che_manca_l_asse():
    data = {"timestamp": "2030-01-02T11:00:00", "var95": {"verdict": "FAIL"}, "var99": {"verdict": "PASS"}}
    facts = extract_facts(checkpoint([bt(data)]))
    assert has_gap(facts, "al 95%: FAIL senza indicazione dell'asse")


def test_mai_un_fail_nudo_nei_gap():
    cases = [bt_data(level("PASS", "FAIL"), level("FAIL", "FAIL")),
             {"timestamp": "2030-01-02T11:00:00", "var95": {"verdict": "FAIL"}}]
    for data in cases:
        for gap in gaps_with(extract_facts(checkpoint([bt(data)])), "FAIL"):
            assert "asse" in gap or "NON AFFIDABILE" in gap, gap


def test_senza_backtest_e_un_buco_dichiarato():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(1.137, obs=234))]))
    assert facts["risk"]["var_backtest"] is None
    assert "Backtest del VaR del portafoglio: dato non procurato in questa run." in facts["gaps"]


def test_backtest_da_solo_tiene_in_vita_il_blocco_rischio():
    facts = extract_facts(checkpoint([bt(bt_data(level("PASS", "PASS"), level("PASS", "PASS")))]))
    assert facts["risk"]["candidate"] is None and facts["risk"]["book"] is None
    assert facts["risk"]["var_backtest"]["levels"]["95"]["verdict"] == "PASS"
    assert facts["risk"]["tool"] == "get_var_backtest"


# ---------------------------------------------------------------- frasi per il PM

TOOL_NAMES = ("get_", "quant_compute", "_pct", "beta_error", "beta_obs", "what_if", "new_alloc",
              "calibration_sample", "verdict_detail", "ricevuta", "payload", "JSON", "None")


def test_frasi_nuove_in_italiano_piano():
    excluded = [{"ticker": "ZZAAA", "motivo": "serie vuota", "obs_valide": 0, "peso_pct": 15.0}]
    receipts = [
        receipt("get_portfolio_risk", risk_data(None, error="SPY non disponibile", obs=0)),
        receipt("get_portfolio_factors", factors_data()),
        bt(bt_data(level("PASS", "FAIL", low_power=True), level("FAIL", "PASS"),
                   excluded=excluded, weight=15.0, reliable=False)),
        receipt("get_portfolio_montecarlo", mc_data(sample=sample(), warning="BASSA"), inp={}),
        proforma(mc_data(alloc=stress_alloc(effective=27.5, skipped=["ZZSKIP"]), sample=sample(), warning="X")),
    ]
    for facts in (extract_facts(checkpoint(receipts)),
                  extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(0.0, new=False)),
                                            proforma(mc_data(weights={T: 0.3}))]))):
        for gap in facts["gaps"]:
            assert not any(name in gap for name in TOOL_NAMES), gap
            assert not re.search(r"\d{4}-\d{2}-\d{2}", gap), gap
            assert gap.endswith("."), gap
        assert len(facts["gaps"]) == len(set(facts["gaps"]))


# ---------------------------------------------------------------- aggiornamento C7 dopo RV-R

def test_esclusi_per_storia_insufficiente_sono_dichiarati():
    data = mc_data(sample=sample(n_obs=300, limiting=None, dropped_limit=0))
    data["tickers_excluded_insufficient_history"] = [{"ticker": "ZZIPO", "n_obs": 5}]
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", data, inp={})]))
    assert facts["montecarlo"]["book"]["excluded_insufficient_history"] == [{"ticker": "ZZIPO", "observations": 5}]
    assert has_gap(facts, "Monte Carlo del portafoglio attuale", "storia insufficiente", "ZZIPO (5 rendimenti validi)")


def test_esclusi_assente_nel_formato_vecchio_e_none_senza_gap():
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", mc_data(), inp={})]))
    assert facts["montecarlo"]["book"]["excluded_insufficient_history"] is None
    assert not gaps_with(facts, "storia insufficiente")


def test_taglio_da_giorni_mancanti_senza_ticker_giovane_e_dichiarato_una_volta():
    s = sample(n_obs=300, limiting=None, dropped_limit=0, dropped_cal=12)
    s["missing_days_in_window_by_ticker"] = {"ZZAAA": 5, "ZZBBB": 3}
    note = ("campione di calibrazione TAGLIATO a 300 obs su 312 del panel 5y da giorni mancanti dentro "
            "le serie (non da un ticker giovane): calibrazione su circa 14 mesi")
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", mc_data(sample=s, note=note), inp={})]))
    cal = facts["montecarlo"]["book"]["calibration"]
    assert cal["missing_days_by_ticker"] == {"ZZAAA": 5, "ZZBBB": 3} and cal["truncated_by_tool"] is True
    hits = gaps_with(facts, "calibrato su 300")
    assert len(hits) == 1 and "giorni mancanti dentro la finestra: ZZAAA 5 e ZZBBB 3" in hits[0]
    assert not gaps_with(facts, "TAGLIATO") and not gaps_with(facts, "non da un ticker giovane")


def error_proforma(payload, ts="2030-01-02T12:00:00+00:00"):
    return receipt("get_portfolio_montecarlo", payload, inp={"horizon_days": 252, "add_tickers": [T]}, ts=ts)


def test_pro_forma_non_calcolabile_mai_un_es():
    payload = {"error": "nessun ticker aggiunto ha almeno 60 rendimenti validi: pro-forma non calcolabile",
               "what_if_allocation": stress_alloc(), "timestamp": "2030-01-02T12:00:00",
               "tickers_excluded_insufficient_history": [{"ticker": T, "n_obs": 12}]}
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", mc_data(), inp={}),
                                      error_proforma(payload)]))
    mc = facts["montecarlo"]
    assert mc["pro_forma"] is None
    err = mc["pro_forma_error"]
    assert "pro-forma non calcolabile" in err["reason"] and err["allocation_origin"] == "stress_fisso"
    assert err["excluded_insufficient_history"] == [{"ticker": T, "observations": 12}]
    assert has_gap(facts, "Monte Carlo con il titolo aggiunto: pro-forma non calcolabile", "60 rendimenti validi",
                   T + " (12 rendimenti validi)", "nessuna perdita estrema")
    assert not gaps_with(facts, "ES al 99%")


def test_intersezione_sotto_minimo_dichiarata_col_campione():
    payload = {"error": "campione di calibrazione di 40 osservazioni dopo l'intersezione delle serie, sotto il "
                        "minimo di 60: simulazione non eseguita",
               "calibration_sample": sample(n_obs=40), "what_if_allocation": stress_alloc(),
               "tickers_excluded_insufficient_history": [], "timestamp": "2030-01-02T12:00:00"}
    facts = extract_facts(checkpoint([error_proforma(payload)]))
    mc = facts["montecarlo"]
    assert mc is not None and mc["book"] is None and mc["pro_forma"] is None
    assert mc["pro_forma_error"]["calibration_observations"] == 40
    assert has_gap(facts, "pro-forma non calcolabile", "40 osservazioni", "simulazione non eseguita")
    assert not gaps_with(facts, "ES al 99%")


def test_errore_successivo_a_un_pro_forma_valido_e_dichiarato_ma_non_lo_cancella():
    ok = proforma(mc_data(alloc=stress_alloc(), sample=sample(n_obs=300)))
    payload = {"error": "pro-forma non calcolabile", "timestamp": "2030-01-02T12:00:00"}
    facts = extract_facts(checkpoint([ok, error_proforma(payload)]))
    assert facts["montecarlo"]["pro_forma"]["candidate_weight_pct"] == 30.0
    assert has_gap(facts, "pro-forma non calcolabile", "resta riportata la simulazione precedente")
    # errore PRECEDENTE alla lettura valida: non conta
    facts = extract_facts(checkpoint([error_proforma(payload, ts="2030-01-02T08:00:00+00:00"), ok]))
    assert facts["montecarlo"]["pro_forma_error"] is None
    assert not gaps_with(facts, "non calcolabile")


def test_frasi_dell_aggiornamento_in_italiano_piano():
    s = sample(n_obs=300, limiting=None, dropped_limit=0)
    s["missing_days_in_window_by_ticker"] = {"ZZAAA": 5}
    data = mc_data(sample=s, note="TAGLIATO")
    data["tickers_excluded_insufficient_history"] = [{"ticker": "ZZIPO", "n_obs": 5}]
    payload = {"error": "pro-forma non calcolabile", "calibration_sample": sample(n_obs=40),
               "tickers_excluded_insufficient_history": [{"ticker": T, "n_obs": 12}]}
    facts = extract_facts(checkpoint([receipt("get_portfolio_montecarlo", data, inp={}), error_proforma(payload)]))
    for gap in facts["gaps"]:
        assert not any(name in gap for name in TOOL_NAMES), gap
        assert not re.search(r"\d{4}-\d{2}-\d{2}", gap), gap
        assert gap.endswith("."), gap


def test_beta_su_pochi_giorni_porta_l_avviso_accanto_al_numero():
    data = risk_data(1.137, obs=45)
    data["beta_note"] = "beta su 45 giorni comuni: indicativo (sotto 60 giorni la stima e' instabile)"
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", data)]))
    bb = facts["risk"]["book_beta"]
    assert bb["value"] == 1.137 and bb["note"].startswith("beta su 45 giorni comuni")
    assert has_gap(facts, "Rischio del portafoglio", "1,14", "con cautela", "45 giorni comuni", "indicativo")


def test_senza_avviso_nessuna_cautela():
    facts = extract_facts(checkpoint([receipt("get_portfolio_risk", risk_data(1.137, obs=234))]))
    assert facts["risk"]["book_beta"]["note"] is None
    assert not gaps_with(facts, "con cautela")


def test_non_affidabile_per_perimetro_mancante_nomina_la_causa_vera():
    # RV-L3: esclusi 0%, ma nei giorni testati manca il 25% del perimetro
    data = bt_data(level("PASS", "PASS", verdict="NON AFFIDABILE"), level("PASS", "FAIL", verdict="NON AFFIDABILE"),
                   weight=0.0, reliable=False)
    data["missing_weight_tested_window_pct"] = 25.0
    data["partial_coverage_tested_window"] = {"ZZPART": "120/495"}
    facts = extract_facts(checkpoint([bt(data)]))
    b = facts["risk"]["var_backtest"]
    assert b["missing_weight_tested_window_pct"] == 25.0 and b["partial_coverage_tested_window"] == {"ZZPART": "120/495"}
    assert has_gap(facts, "NON AFFIDABILE, nei giorni testati manca in media il 25,00% del perimetro (soglia 10%)",
                   "ZZPART 120 giorni su 495", "non valida il VaR")
    # mai la frase falsa «esclusi 0% sopra la soglia»
    assert not any("NON AFFIDABILE, i titoli esclusi" in g for g in facts["gaps"])
    assert has_gap(facts, "al 99%", "titoli esclusi pari al 0,00%", "manca in media il 25,00%")


def test_non_affidabile_per_esclusi_resta_sugli_esclusi():
    data = bt_data(level("PASS", "PASS", verdict="NON AFFIDABILE"), level("PASS", "PASS", verdict="NON AFFIDABILE"),
                   weight=15.0, reliable=False)
    data["missing_weight_tested_window_pct"] = 15.0
    facts = extract_facts(checkpoint([bt(data)]))
    assert has_gap(facts, "NON AFFIDABILE, i titoli esclusi pesano il 15,00% del perimetro (soglia 10%)")


def test_non_affidabile_senza_causa_misurabile_usa_il_dettaglio_del_tool():
    data = bt_data(level("PASS", "PASS", verdict="NON AFFIDABILE", detail="NON AFFIDABILE: motivo sintetico ZZ"),
                   level("PASS", "PASS", verdict="NON AFFIDABILE", detail="NON AFFIDABILE: motivo sintetico ZZ"),
                   weight=0.0, reliable=False)
    facts = extract_facts(checkpoint([bt(data)]))
    assert has_gap(facts, "NON AFFIDABILE, motivo dichiarato dal calcolo: NON AFFIDABILE: motivo sintetico ZZ")
