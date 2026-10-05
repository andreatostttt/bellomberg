"""Test OFFLINE 04/10 (Opus 5.5): voce 7 c+g del Monte Carlo.

c) Il campione di calibrazione e' l'INTERSEZIONE delle serie: parte dal ticker piu'
   giovane e perde i giorni in cui un titolo non quotava. Il payload lo dichiara in
   forma strutturata (`calibration_sample`) e, sotto CALIBRATION_MIN_OBS_TAILS,
   porta un avviso esplicito sulle code (`tail_reliability_warning`).
g) Il what-if `add_tickers` dava al candidato il 30% del book con un valore cablato e
   nessuno lo diceva: Red Team e PDF hanno letto l'ES99 come lo scenario della size
   proposta. Ora la quota e' il parametro `new_alloc`; senza, resta il 30% ma
   ETICHETTATO come stress (`what_if_allocation`).

Zero rete: holding, rendimenti e negozio prezzi sono stubbati. Ticker inventati.
"""
import inspect
import re

import numpy as np
import pandas as pd
import pytest

from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload
import bellomberg.portfolio.portfolio_montecarlo as pm

N = 600            # righe del pannello
INIZIO_GIOVANE = 400   # il ticker giovane parte da questa riga: 200 obs
BUCHI_A = [410, 450, 480, 520, 560]   # giorni senza quotazione di ZZAAA (calendario)
BUCHI_B = [430, 500, 590]             # giorni senza quotazione di ZZBBB


def _pannello(con_giovane=True):
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2023-01-02", periods=N)
    df = pd.DataFrame({t: rng.normal(0, 0.01, N) for t in ("ZZAAA", "ZZBBB", "ZZNEW", "ZZYNG")},
                      index=idx)
    if con_giovane:
        df.iloc[:INIZIO_GIOVANE, df.columns.get_loc("ZZYNG")] = np.nan
        df.iloc[BUCHI_A, df.columns.get_loc("ZZAAA")] = np.nan
        df.iloc[BUCHI_B, df.columns.get_loc("ZZBBB")] = np.nan
    return df


class _FakeDB:
    def get_portfolio_summary(self):
        return {"positions": [], "totale_valore_mercato_eur": 100000.0}


def _monta(monkeypatch, pesi, pannello):
    monkeypatch.setattr(pm, "prezzi_speciali", lambda: {
        "prezzi": {"senza_yfinance": frozenset()}, "origine": "test", "motivo": ""})
    monkeypatch.setattr(pm, "MemoryDB", _FakeDB)
    monkeypatch.setattr(pm, "_get_holdings_weights", lambda salta: (dict(pesi), 100000.0))
    monkeypatch.setattr(pm, "_download_returns",
                        lambda tickers, *a, **k: pannello[[t for t in tickers if t in pannello.columns]])
    pm.invalidate_cache()


@pytest.fixture
def mc_giovane(monkeypatch):
    _monta(monkeypatch, {"ZZAAA": 0.5, "ZZBBB": 0.3, "ZZYNG": 0.2}, _pannello())
    yield lambda **kw: pm.run_monte_carlo(n_sims=1000, horizon_days=20,
                                          method="block_bootstrap", seed=3, **kw)
    pm.invalidate_cache()


@pytest.fixture
def mc_pieno(monkeypatch):
    _monta(monkeypatch, {"ZZAAA": 0.6, "ZZBBB": 0.4}, _pannello(con_giovane=False))
    yield lambda **kw: pm.run_monte_carlo(n_sims=1000, horizon_days=20,
                                          method="block_bootstrap", seed=3, **kw)
    pm.invalidate_cache()


# --- c) campione di calibrazione strutturato ---------------------------------

def test_campione_strutturato_separa_i_due_effetti(mc_giovane):
    out = mc_giovane(force_refresh=True)
    assert "error" not in out, out.get("error")
    cs = out["calibration_sample"]
    attese = (N - INIZIO_GIOVANE) - len(BUCHI_A) - len(BUCHI_B)   # 192
    assert cs["n_obs"] == attese == out["lookback_days_calibration"]
    assert cs["panel_obs"] == N
    assert cs["limiting_ticker"] == "ZZYNG"
    assert cs["limiting_tickers"] == ["ZZYNG"]
    assert cs["limiting_ticker_start_date"] == pd.bdate_range("2023-01-02", periods=N)[INIZIO_GIOVANE].date().isoformat()
    assert cs["obs_dropped_by_limiting_ticker"] == INIZIO_GIOVANE
    assert cs["obs_dropped_by_calendars"] == len(BUCHI_A) + len(BUCHI_B)
    # conteggio INDIPENDENTE dal residuo (review RV-R: l'invariante sotto e' vera per
    # costruzione): i giorni mancanti per ticker nella finestra
    assert cs["missing_days_in_window_by_ticker"] == {"ZZAAA": len(BUCHI_A), "ZZBBB": len(BUCHI_B)}
    # i conti tornano: niente osservazioni sparite senza spiegazione
    assert cs["n_obs"] + cs["obs_dropped_by_limiting_ticker"] + cs["obs_dropped_by_calendars"] == cs["panel_obs"]
    assert cs["start_date"] == cs["limiting_ticker_start_date"]
    assert cs["end_date"] == pd.bdate_range("2023-01-02", periods=N)[-1].date().isoformat()
    assert cs["approx_months"] == round(attese / 21.0, 1)


def test_frase_per_il_pm_dice_quanti_mesi_e_chi_limita(mc_giovane):
    with language_context("it"):
        out = render_payload(mc_giovane(force_refresh=True))
    frase = str(out["calibration_sample"]["sentence"])
    assert "circa 9 mesi" in frase          # 192 / 21 = 9,1
    assert "192 osservazioni" in frase
    assert "limitata da ZZYNG" in frase
    assert "400 osservazioni in meno" in frase
    assert "altre 8 tolte" in frase and "calendari" in frase


def test_frase_in_inglese(mc_giovane):
    with language_context("en"):
        out = render_payload(mc_giovane(force_refresh=True))
    frase = str(out["calibration_sample"]["sentence"])
    assert "about 9 months" in frase and "limited by ZZYNG" in frase


def test_sotto_soglia_avviso_esplicito_sulle_code(mc_giovane):
    out = mc_giovane(force_refresh=True)
    assert out["calibration_sample"]["low_tail_reliability"] is True
    assert out["calibration_sample"]["min_obs_reliable_tails"] == pm.CALIBRATION_MIN_OBS_TAILS == 250
    avviso = str(out["tail_reliability_warning"] or "")
    assert "ES99" in avviso and "192" in avviso and "250" in avviso


def test_sopra_soglia_nessun_avviso_e_nessun_limitante(mc_pieno):
    out = mc_pieno(force_refresh=True)
    cs = out["calibration_sample"]
    assert cs["n_obs"] == N   # pannello pieno, nessun buco
    assert cs["low_tail_reliability"] is False
    assert out["tail_reliability_warning"] is None
    assert cs["limiting_ticker"] is None and cs["limiting_tickers"] == []
    assert cs["obs_dropped_by_limiting_ticker"] == 0
    assert out["calibration_note"] is None


def test_nota_storica_spiega_i_calendari_e_resta_leggibile_dai_facts(mc_giovane):
    with language_context("it"):
        out = render_payload(mc_giovane(force_refresh=True))
    nota = str(out["calibration_note"])
    assert "TAGLIATO a 192 obs" in nota
    assert "altre 8 tolte" in nota          # i giorni dei calendari ora sono spiegati
    # la regex dei facts (trade_idea_facts._mc_notes) deve continuare a riconoscerla
    assert re.search(r"(\d+)\s*obs dal ticker pi\S* giovane \(([^:]+):\s*(\d+)\s*obs su (\d+) del panel (\w+)\)", nota)


# --- g) allocazione what-if dichiarata ---------------------------------------

def test_default_resta_30_ma_etichettato_stress():
    assert inspect.signature(pm.run_monte_carlo).parameters["new_alloc"].default is None
    assert pm.WHAT_IF_STRESS_ALLOC == 0.30


def test_senza_add_nessuna_allocazione_dichiarata(mc_pieno):
    assert mc_pieno(force_refresh=True)["what_if_allocation"] is None


def test_add_senza_new_alloc_e_stress_fisso_non_size_proposta(mc_pieno):
    with language_context("it"):
        out = render_payload(mc_pieno(force_refresh=True, add_tickers=["ZZNEW"]))
    wa = out["what_if_allocation"]
    assert wa["origin"] == "stress_fisso"
    assert wa["is_proposed_size"] is False
    assert wa["added_weight_pct_requested"] == 30.0
    assert wa["added_weight_pct_effective"] == pytest.approx(30.0, abs=1e-3)
    assert "NON e' la size proposta" in str(wa["note"])
    assert out["weights"]["ZZNEW"] == pytest.approx(0.30, abs=1e-4)


def test_add_con_new_alloc_usa_la_size_del_chiamante(mc_pieno):
    out = mc_pieno(force_refresh=True, add_tickers=["ZZNEW"], new_alloc=0.025)
    wa = out["what_if_allocation"]
    assert wa["origin"] == "parametro_chiamante"
    assert wa["is_proposed_size"] is True
    assert wa["added_weight_pct_requested"] == 2.5
    assert wa["added_weight_pct_effective"] == pytest.approx(2.5, abs=1e-3)
    assert wa["added_weight_pct_by_ticker"] == {"ZZNEW": pytest.approx(2.5, abs=1e-3)}
    assert out["weights"]["ZZNEW"] == pytest.approx(0.025, abs=1e-4)


def test_remove_e_add_insieme_il_candidato_resta_alla_quota_dichiarata(monkeypatch):
    # prima: remove toglieva peso, il resto scalava di 0.7 e la rinormalizzazione
    # portava il candidato SOPRA il 30% dichiarato
    _monta(monkeypatch, {"ZZAAA": 0.5, "ZZBBB": 0.3, "ZZYNG": 0.2}, _pannello(con_giovane=False))
    out = pm.run_monte_carlo(n_sims=1000, horizon_days=20, method="block_bootstrap", seed=3,
                             force_refresh=True, add_tickers=["ZZNEW"], remove_tickers=["ZZYNG"])
    wa = out["what_if_allocation"]
    assert wa["added_weight_pct_effective"] == pytest.approx(30.0, abs=1e-3)
    assert out["weights"]["ZZNEW"] == pytest.approx(0.30, abs=1e-4)
    pm.invalidate_cache()


def _run(**kw):
    return pm.run_monte_carlo(n_sims=1000, horizon_days=20, method="block_bootstrap", seed=3,
                              force_refresh=True, **kw)


def test_candidato_senza_colonna_e_errore_dichiarato(monkeypatch):
    # un pro-forma senza il candidato sarebbe il book attuale travestito
    _monta(monkeypatch, {"ZZAAA": 0.6, "ZZBBB": 0.4}, _pannello(con_giovane=False)[["ZZAAA", "ZZBBB"]])
    out = _run(add_tickers=["ZZMISS"], new_alloc=0.05)
    assert "error" in out and "pro-forma" in str(out["error"])
    assert out["what_if_allocation"]["added_tickers_without_returns"] == ["ZZMISS"]
    assert out["tickers_excluded_insufficient_history"] == [{"ticker": "ZZMISS", "n_obs": 0}]
    pm.invalidate_cache()


def test_candidato_colonna_tutta_nan_non_va_in_crash(monkeypatch):
    # review RV-R: la forma di yfinance per un simbolo fallito; prima ValueError di broadcast
    pann = _pannello(con_giovane=False)
    pann["ZZNEW"] = np.nan
    _monta(monkeypatch, {"ZZAAA": 0.6, "ZZBBB": 0.4}, pann)
    out = _run(add_tickers=["ZZNEW"], new_alloc=0.025)
    assert "error" in out
    assert out["what_if_allocation"]["added_tickers_without_returns"] == ["ZZNEW"]
    pm.invalidate_cache()


def test_candidato_ipo_con_5_rendimenti_escluso_e_dichiarato(monkeypatch):
    pann = _pannello(con_giovane=False)
    pann.iloc[:N - 5, pann.columns.get_loc("ZZNEW")] = np.nan
    _monta(monkeypatch, {"ZZAAA": 0.6, "ZZBBB": 0.4}, pann)
    out = _run(add_tickers=["ZZNEW"], new_alloc=0.025)
    assert "error" in out
    assert out["tickers_excluded_insufficient_history"] == [{"ticker": "ZZNEW", "n_obs": 5}]
    pm.invalidate_cache()


def test_titolo_del_book_con_storia_insufficiente_escluso_e_dichiarato(monkeypatch):
    pann = _pannello(con_giovane=False)
    pann.iloc[:N - 10, pann.columns.get_loc("ZZYNG")] = np.nan
    _monta(monkeypatch, {"ZZAAA": 0.5, "ZZBBB": 0.3, "ZZYNG": 0.2}, pann)
    out = _run()
    assert "error" not in out, out.get("error")
    assert "ZZYNG" not in out["tickers_analyzed"]
    assert out["tickers_excluded_insufficient_history"] == [{"ticker": "ZZYNG", "n_obs": 10}]
    assert out["calibration_sample"]["n_obs"] == N
    pm.invalidate_cache()


def test_intersezione_sotto_il_minimo_e_errore_col_campione(monkeypatch):
    # ogni serie ha >= 60 rendimenti, ma l'intersezione no: prima girava lo stesso
    pann = _pannello(con_giovane=False)
    pann.iloc[:N - 70, pann.columns.get_loc("ZZYNG")] = np.nan          # 70 obs
    pann.iloc[N - 60:N - 44, pann.columns.get_loc("ZZAAA")] = np.nan     # 16 buchi dentro
    _monta(monkeypatch, {"ZZAAA": 0.5, "ZZBBB": 0.3, "ZZYNG": 0.2}, pann)
    out = _run()
    assert "error" in out and "54" in str(out["error"])
    cs = out["calibration_sample"]
    assert cs["n_obs"] == 54 and cs["limiting_ticker"] == "ZZYNG"
    assert cs["missing_days_in_window_by_ticker"] == {"ZZAAA": 16}
    pm.invalidate_cache()


def test_candidato_gia_in_book_con_remove_resta_alla_quota(monkeypatch):
    # dal caso RV-R: il candidato e' gia' nel book (20%) e un altro titolo esce. Se il
    # candidato restasse nel «resto» scalato e poi venisse sovrascritto, la
    # rinormalizzazione lo porterebbe sopra la quota dichiarata.
    _monta(monkeypatch, {"ZZAAA": 0.5, "ZZBBB": 0.3, "ZZNEW": 0.2}, _pannello(con_giovane=False))
    out = _run(add_tickers=["ZZNEW"], remove_tickers=["ZZBBB"], new_alloc=0.025)
    assert out["what_if_allocation"]["added_weight_pct_effective"] == pytest.approx(2.5, abs=1e-6)
    assert out["weights"]["ZZNEW"] == pytest.approx(0.025, abs=1e-4)
    # remove e add dello STESSO candidato: vince l'add, alla quota dichiarata
    out = _run(add_tickers=["ZZNEW"], remove_tickers=["ZZNEW"], new_alloc=0.025)
    assert out["what_if_allocation"]["added_weight_pct_effective"] == pytest.approx(2.5, abs=1e-6)
    pm.invalidate_cache()


def test_ticker_duplicati_non_dimezzano_la_quota(mc_pieno):
    out = mc_pieno(force_refresh=True, add_tickers=["ZZNEW", "zznew"], new_alloc=0.10)
    wa = out["what_if_allocation"]
    assert wa["added_tickers_applied"] == ["ZZNEW"]
    assert wa["added_weight_pct_effective"] == pytest.approx(10.0, abs=1e-3)


def test_buco_a_meta_serie_nota_non_si_contraddice(monkeypatch):
    # nessun ticker parte tardi: ZZAAA manca 300 giorni A META' serie. La nota non
    # deve dare la colpa a un «ticker piu' giovane» e il buco deve avere un nome.
    pann = _pannello(con_giovane=False)
    pann.iloc[100:400, pann.columns.get_loc("ZZAAA")] = np.nan
    _monta(monkeypatch, {"ZZAAA": 0.6, "ZZBBB": 0.4}, pann)
    with language_context("it"):
        out = render_payload(_run())
    cs = out["calibration_sample"]
    assert cs["limiting_ticker"] is None
    assert cs["obs_dropped_by_calendars"] == 300
    assert cs["missing_days_in_window_by_ticker"] == {"ZZAAA": 300}
    nota = str(out["calibration_note"])
    assert "piu' giovane" not in nota and "non da un ticker giovane" in nota
    assert "ZZAAA, 300" in str(cs["sentence"])
    pm.invalidate_cache()


@pytest.mark.parametrize("val", [2.5, 0, 1, -0.1, "abc"])
def test_new_alloc_fuori_range_e_errore_dichiarato(mc_pieno, val):
    out = mc_pieno(force_refresh=True, add_tickers=["ZZNEW"], new_alloc=val)
    assert "error" in out and "new_alloc" in str(out["error"])


def test_new_alloc_senza_add_e_errore(mc_pieno):
    out = mc_pieno(force_refresh=True, new_alloc=0.05)
    assert "error" in out and "add_tickers" in str(out["error"])


def test_la_cache_non_scambia_due_quote_diverse(mc_pieno):
    a = mc_pieno(force_refresh=True, add_tickers=["ZZNEW"], new_alloc=0.025)
    b = mc_pieno(add_tickers=["ZZNEW"])     # NON forza: non deve pescare la 2,5%
    assert a["what_if_allocation"]["origin"] == "parametro_chiamante"
    assert b["what_if_allocation"]["origin"] == "stress_fisso"
    assert b["weights"]["ZZNEW"] == pytest.approx(0.30, abs=1e-4)
