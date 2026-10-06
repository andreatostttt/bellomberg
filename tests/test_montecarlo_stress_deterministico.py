"""Voce 5 handoff-4 (05/10/2026, Opus 5.5): replay storico = SCENARIO DETERMINISTICO.

Negli stress «GFC 2008» e «COVID 2020» il motore rimpiazza le prime sedute di OGNI
simulazione con la stessa finestra storica. Se la finestra copre tutto l'orizzonte
(es. F4 Performance chiede 21 sedute), le simulazioni sono identiche: la UI e il tool
del comitato mostravano E[R] = ES99 e P(perdita > 20%) = 100% come se fossero
probabilita'. Ora:
- `stress_nature` = "deterministic" e `deterministic_scenario` porta l'etichetta
  «scenario deterministico», la perdita dello scenario e il motivo;
- le metriche probabilistiche (E[R], mediana, dev. std., Sharpe, probabilita',
  VaR, ES, percentili, drawdown p5/p50/p95) escono null DICHIARATE, elencate in
  `deterministic_scenario.metrics_not_applicable`;
- se la finestra e' piu' corta dell'orizzonte le metriche restano (c'e' una
  distribuzione) ma sono etichettate CONDIZIONATE al replay fisso.

Zero rete: holding, rendimenti, finestra stress e negozio prezzi stubbati.
Ticker e rendimenti inventati.
"""
import json
import re

import numpy as np
import pandas as pd
import pytest

from bellomberg.core.language import language_context
import bellomberg.portfolio.portfolio_montecarlo as pm

PESI = {"ZZDET": 0.6, "QQSYN.MI": 0.4}
CAMPI_NA = ("expected_return_pct", "median_return_pct", "stdev_pct", "sharpe_simulated",
            "prob_negative_pct", "prob_loss_10pct", "prob_loss_20pct",
            "prob_gain_10pct", "prob_gain_20pct", "var_95_pct", "var_99_pct",
            "var_99_cornish_fisher_pct", "es_95_pct", "es_99_pct", "es_95_eur", "es_99_eur",
            "max_drawdown_p5_pct", "max_drawdown_median_pct", "max_drawdown_p95_pct",
            "percentiles_ratio", "percentiles_eur")


class _FakeDB:
    def get_portfolio_summary(self):
        return {"positions": [], "totale_valore_mercato_eur": 123450.0}


def _finestra(giorni, r_a=-0.012, r_b=-0.0135):
    idx = pd.bdate_range("2008-09-15", periods=giorni)
    return pd.DataFrame({"ZZDET": np.full(giorni, r_a), "QQSYN.MI": np.full(giorni, r_b)},
                        index=idx)


def _monta(monkeypatch, finestra):
    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2023-01-02", periods=420)
    rdf = pd.DataFrame({t: rng.normal(0, 0.011, 420) for t in PESI}, index=idx)
    monkeypatch.setattr(pm, "prezzi_speciali", lambda: {
        "prezzi": {"senza_yfinance": frozenset()}, "origine": "test", "motivo": ""})
    monkeypatch.setattr(pm, "MemoryDB", _FakeDB)
    monkeypatch.setattr(pm, "_get_holdings_weights", lambda salta: (dict(PESI), 123450.0))
    monkeypatch.setattr(pm, "_download_returns",
                        lambda tickers, *a, **k: rdf[[t for t in tickers if t in rdf.columns]])
    if finestra is None:
        monkeypatch.setattr(pm, "_stress_window_returns",
                            lambda *a, **k: (None, "finestra assente (test)"))
    else:
        monkeypatch.setattr(pm, "_stress_window_returns", lambda tickers, *a, **k: (
            finestra[list(tickers)],
            {"window": {"start": "2008-09-15", "end": "2009-03-31",
                        "trading_days": len(finestra)},
             "real_history": list(tickers), "proxied": {}}))
    pm.invalidate_cache()


def _corri(stress, orizzonte=21, lingua="it"):
    with language_context(lingua):
        return pm.run_monte_carlo(horizon_days=orizzonte, n_sims=600, method="block_bootstrap",
                                  stress_scenario=stress, seed=5, force_refresh=True)


def _perdita_attesa(finestra, giorni):
    w = np.array([PESI[t] for t in finestra.columns])
    return (np.prod(1.0 + finestra.values[:giorni] @ w) - 1.0) * 100


@pytest.fixture(autouse=True)
def _pulisci_cache():
    pm.invalidate_cache()
    yield
    pm.invalidate_cache()


@pytest.mark.parametrize("scenario", ["gfc_2008", "covid_2020"])
def test_replay_che_copre_l_orizzonte_e_scenario_deterministico(monkeypatch, scenario):
    fin = _finestra(30)
    _monta(monkeypatch, fin)
    out = _corri(scenario)
    assert "error" not in out, out.get("error")
    assert out["stress_scenario"] == scenario and out["stress_fallback"] is False
    assert out["stress_nature"] == "deterministic"
    det = out["deterministic_scenario"]
    assert det is not None
    assert "scenario deterministico" in str(det["label"])
    assert "scenario deterministico" in str(out["stress_nature_label"])
    assert det["scenario"] == scenario
    assert det["replayed_days"] == 21 and det["horizon_days"] == 21
    # la perdita dello scenario e' quella vera della finestra (21 sedute replicate)
    attesa = _perdita_attesa(fin, 21)
    assert attesa < -20
    assert det["scenario_loss_pct"] == pytest.approx(attesa, abs=0.01)
    assert det["scenario_loss_pct"] == out["stress_meta"]["window_loss_pct"]
    assert det["scenario_loss_eur"] == pytest.approx(attesa / 100 * 123450.0, abs=1)
    # rendimenti tutti negativi: il drawdown dello scenario e' la perdita stessa
    assert det["scenario_max_drawdown_pct"] == pytest.approx(attesa, abs=0.01)
    assert "non sono probabilit" in str(det["reason"])


@pytest.mark.parametrize("scenario", ["gfc_2008", "covid_2020"])
def test_metriche_probabilistiche_null_dichiarate(monkeypatch, scenario):
    _monta(monkeypatch, _finestra(30))
    out = _corri(scenario)
    det = out["deterministic_scenario"]
    for campo in CAMPI_NA:
        assert out[campo] is None, (campo, out[campo])
    assert set(det["metrics_not_applicable"]) == set(CAMPI_NA)
    json.dumps(out, allow_nan=False)


def test_finestra_esattamente_uguale_all_orizzonte_e_deterministica(monkeypatch):
    _monta(monkeypatch, _finestra(21))
    out = _corri("gfc_2008")
    assert out["stress_nature"] == "deterministic"
    assert out["prob_loss_20pct"] is None


def test_finestra_piu_corta_dell_orizzonte_metriche_condizionate(monkeypatch):
    _monta(monkeypatch, _finestra(10))
    out = _corri("gfc_2008", orizzonte=21)
    assert out["stress_nature"] == "fixed_then_simulated"
    assert out["deterministic_scenario"] is None
    for campo in ("expected_return_pct", "es_99_pct", "prob_loss_20pct", "var_99_pct"):
        assert isinstance(out[campo], float), campo
    assert out["percentiles_eur"]["p50"] is not None
    etichetta = str(out["stress_nature_label"]).lower()
    assert "condizionat" in etichetta and "10" in etichetta and "21" in etichetta


def test_senza_stress_nessuna_etichetta(monkeypatch):
    _monta(monkeypatch, _finestra(30))
    out = _corri("none")
    assert out["stress_nature"] == "none"
    assert out["stress_nature_label"] is None
    assert out["deterministic_scenario"] is None
    assert isinstance(out["expected_return_pct"], float)
    assert isinstance(out["es_99_pct"], float)


def test_fallback_a_shock_non_e_deterministico(monkeypatch):
    _monta(monkeypatch, None)
    out = _corri("gfc_2008")
    assert out["stress_scenario"] == "shock_3sigma" and out["stress_fallback"] is True
    assert out["stress_nature"] == "fixed_then_simulated"
    assert out["deterministic_scenario"] is None
    assert isinstance(out["es_99_pct"], float)


def test_etichetta_in_inglese(monkeypatch):
    _monta(monkeypatch, _finestra(30))
    out = _corri("covid_2020", lingua="en")
    assert "deterministic scenario" in str(out["deterministic_scenario"]["label"])
    assert "are not probabilities" in str(out["deterministic_scenario"]["reason"])


def test_l_etichetta_arriva_al_tool_del_comitato(monkeypatch):
    """La vista compatta degli agenti (chat_tools) deve portare etichetta e perdita."""
    from bellomberg.agents.chat_tools import _compatta_montecarlo
    _monta(monkeypatch, _finestra(30))
    vista = _compatta_montecarlo(_corri("gfc_2008"))
    assert vista["stress_nature"] == "deterministic"
    assert "scenario deterministico" in str(vista["deterministic_scenario"]["label"])
    assert vista["deterministic_scenario"]["scenario_loss_pct"] is not None
    assert vista["es_99_pct"] is None and vista["prob_loss_20pct"] is None
    testo = json.dumps(vista, ensure_ascii=False, default=str)
    assert "scenario deterministico" in testo


# --- seguito (05/10, autorizzato da main): drawdown dal NAV di PARTENZA ----------

def test_drawdown_distribuito_conta_la_caduta_del_primo_giorno(monkeypatch):
    """Le prime 10 sedute sono la stessa caduta in ogni simulazione: OGNI traiettoria
    ha un drawdown almeno pari alla perdita di quelle 10 sedute misurata dal NAV di
    partenza. Prima il massimo mobile partiva dalla chiusura del giorno 1 e la caduta
    del primo giorno spariva (p95 ~ perdita di 9 sedute)."""
    fin = _finestra(10)
    _monta(monkeypatch, fin)
    out = _corri("gfc_2008", orizzonte=21)
    perdita_10 = _perdita_attesa(fin, 10)
    assert out["stress_meta"]["window_loss_pct"] == pytest.approx(perdita_10, abs=0.01)
    assert out["max_drawdown_p95_pct"] <= perdita_10 + 0.01
    assert out["max_drawdown_median_pct"] <= out["max_drawdown_p95_pct"]
    assert out["max_drawdown_p5_pct"] <= out["max_drawdown_median_pct"]


def test_drawdown_mai_positivo_senza_stress(monkeypatch):
    _monta(monkeypatch, _finestra(30))
    out = _corri("none")
    assert out["max_drawdown_p95_pct"] <= 0.0


def test_la_descrizione_del_tool_nomina_la_natura_dello_stress():
    """Il comitato deve sapere DALLA DESCRIZIONE che un replay deterministico non
    ha probabilita' da citare (il payload lo dice, ma il modello sceglie cosa citare
    leggendo la descrizione del tool)."""
    from bellomberg.agents.chat_tools import TOOL_DEFINITIONS
    desc = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_portfolio_montecarlo")["description"]
    # il CAMPO stress_nature (non solo stress_nature_label) con i suoi valori
    assert re.search(r"\bstress_nature\b(?!_)", desc) and "deterministic_scenario" in desc
    assert "'deterministic'" in desc and "'fixed_then_simulated'" in desc
    assert "scenario_loss_pct" in desc
    assert "MAI E[R]/ES/VaR/probabilita'" in desc
    assert "CONDIZIONATE" in desc


# --- revisione R-5 (06/10): C1 ventaglio dichiarato, C2-C4 confini, C7 segno ----

def _finestra_profilo(rendimenti):
    idx = pd.bdate_range("2020-02-20", periods=len(rendimenti))
    r = np.array(rendimenti, dtype=float)
    return pd.DataFrame({"ZZDET": r, "QQSYN.MI": r}, index=idx)


def test_ventaglio_deterministico_dichiarato_non_percentili(monkeypatch):
    _monta(monkeypatch, _finestra(30))
    out = _corri("gfc_2008")
    fb = out["fan_bands"]
    assert fb["deterministic"] is True
    assert "scenario deterministico" in str(fb["label"]) and "non sono percentili" in str(fb["label"])
    # le bande coincidono davvero: una sola traiettoria
    assert fb["p5"] == fb["p95"] == fb["p50"]


def test_ventaglio_non_deterministico_resta_percentili(monkeypatch):
    _monta(monkeypatch, _finestra(10))
    fb = _corri("gfc_2008")["fan_bands"]
    assert fb["deterministic"] is False and fb["label"] is None
    _monta(monkeypatch, _finestra(30))
    fb = _corri("none")["fan_bands"]
    assert fb["deterministic"] is False and fb["label"] is None


def test_finestra_che_sale_e_poi_scende_drawdown_diverso_dalla_perdita(monkeypatch):
    """C2: drawdown e perdita a fine scenario sono due numeri diversi."""
    fin = _finestra_profilo([0.03] * 5 + [-0.04] * 8 + [0.02] * 8)
    _monta(monkeypatch, fin)
    det = _corri("covid_2020")["deterministic_scenario"]
    via = np.concatenate(([1.0], np.cumprod(1.0 + fin.values[:, 0])))
    dd_atteso = (via / np.maximum.accumulate(via) - 1.0).min() * 100
    assert det["scenario_loss_pct"] == pytest.approx(_perdita_attesa(fin, 21), abs=0.01)
    assert det["scenario_max_drawdown_pct"] == pytest.approx(dd_atteso, abs=0.01)
    assert det["scenario_max_drawdown_pct"] < det["scenario_loss_pct"] - 20


def test_finestra_di_una_seduta_piu_corta_dell_orizzonte_e_condizionata(monkeypatch):
    """C3: il confine e' l'orizzonte esatto; 20 sedute su 21 = una resta simulata."""
    _monta(monkeypatch, _finestra(20))
    out = _corri("gfc_2008", orizzonte=21)
    assert out["stress_nature"] == "fixed_then_simulated"
    assert out["deterministic_scenario"] is None
    assert isinstance(out["es_99_pct"], float) and isinstance(out["prob_loss_20pct"], float)
    assert "20" in str(out["stress_nature_label"]) and "21" in str(out["stress_nature_label"])


def test_shock_3sigma_chiesto_direttamente_e_condizionato(monkeypatch):
    """C4: lo shock che F4 chiede a 21 sedute, senza passare dal fallback."""
    _monta(monkeypatch, _finestra(30))
    out = _corri("shock_3sigma")
    assert out["stress_scenario"] == "shock_3sigma" and out["stress_fallback"] is False
    assert out["stress_nature"] == "fixed_then_simulated"
    assert "shock fisso" in str(out["stress_nature_label"])
    assert out["deterministic_scenario"] is None
    assert isinstance(out["es_99_pct"], float)


def test_segno_dichiarato_su_finestra_che_sale(monkeypatch):
    """C7: scenario_loss_pct e' un rendimento CON SEGNO, dichiarato nel payload."""
    fin = _finestra_profilo([0.01] * 30)
    _monta(monkeypatch, fin)
    det = _corri("gfc_2008")["deterministic_scenario"]
    assert det["scenario_loss_pct"] > 0
    assert det["scenario_max_drawdown_pct"] == 0.0
    assert "negativo = perdita" in str(det["sign_convention"])


def test_descrizione_dichiara_il_segno():
    from bellomberg.agents.chat_tools import TOOL_DEFINITIONS
    desc = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_portfolio_montecarlo")["description"]
    assert "CON SEGNO: negativo = perdita" in desc
