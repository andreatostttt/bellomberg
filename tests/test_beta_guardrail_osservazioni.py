"""Guardrail beta con osservazioni dichiarate (voce 4 handoff-4, opzioni 2+3 autorizzate dal PM 05/10).

- Opzione 2: ogni motore dichiara `n_obs`; sotto `BETA_MIN_OBS` la fonte e' «insufficiente»,
  resta visibile con il motivo e NON entra nello scarto.
- Opzione 3: con UNRELIABLE il payload porta comunque `indicative` (range, mediana) con
  `uso: "non_per_decisioni"`; il Capo non riceve quei numeri e il beta resta vietato per
  le coperture in ogni verdetto diverso da RECONCILED.
Numeri e payload INVENTATI (nessun dato del book).
"""
import numpy as np
import pytest

from bellomberg.core.language import language_context
# moduli VERI presi all'import: la fixture della run offline mette dei finti in sys.modules
# (eseguiti da soli, `from pkg import mod` dentro il test risolverebbe il finto)
from bellomberg.portfolio import advanced_metrics as _AM_VERO, portfolio_risk as _PR_VERO, portfolio_factors as _PF_VERO


def _motori(monkeypatch, twr=None, risk=None, factor=None):
    """Monta i tre motori finti. Ogni argomento e' (beta, n_obs) oppure None (motore muto)."""
    from bellomberg.portfolio import advanced_metrics as am, portfolio_risk, portfolio_factors
    if twr is None:
        monkeypatch.setattr(am, "portfolio_metrics", lambda: {"error": "serie finta assente"})
    else:
        bench = {"beta": twr[0]}
        if twr[1] is not None:
            bench["n_obs"] = twr[1]
        monkeypatch.setattr(am, "portfolio_metrics", lambda: {"benchmark": bench})
    if risk is None:
        monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk", lambda: {"error": "rischio finto assente"})
    else:
        pr = {"portfolio": {"beta_vs_spy": risk[0]}}
        if risk[1] is not None:
            pr["beta_obs"] = risk[1]
        monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk", lambda: pr)
    if factor is None:
        monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors", lambda: {"error": "fattori finti assenti"})
    else:
        ph = {}
        if factor[1] is not None:
            ph = {"ZZTEST": {"n_obs": factor[1]}, "QQSYN.MI": {"n_obs": factor[1] + 500}}
        monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                            lambda: {"portfolio_aggregate": {"beta_market": factor[0]}, "per_holding": ph})
    return am


def test_soglia_minima_dichiarata_e_coerente():
    from bellomberg.portfolio import advanced_metrics as am
    assert am.BETA_MIN_OBS == 60


def test_tre_fonti_sufficienti_vicine_riconciliano(monkeypatch):
    am = _motori(monkeypatch, twr=(.91, 140), risk=(1.02, 230), factor=(.97, 400))
    out = am.reconcile_betas()
    assert out["verdict"] == "RECONCILED"
    assert out["beta_consensus"] == .97
    assert out["beta_per_decisioni"] is True
    assert out["sources_insufficient"] == {}
    assert out["n_obs"] == {"advanced_metrics_twr": 140, "portfolio_risk_spy": 230, "factor_model_mkt": 400}
    assert out["min_obs"] == 60
    assert "indicative" not in out


def test_fonte_sotto_soglia_esce_dallo_scarto_ma_resta_visibile(monkeypatch):
    # il caso del clone con pochi dati: un motore su 30 date dice 1,57, gli altri due sono vicini
    am = _motori(monkeypatch, twr=(1.57, 30), risk=(.84, 200), factor=(.80, 300))
    out = am.reconcile_betas()
    assert out["verdict"] == "RECONCILED"
    assert out["max_spread"] == .04
    assert set(out["betas"]) == {"portfolio_risk_spy", "factor_model_mkt"}
    ins = out["sources_insufficient"]["advanced_metrics_twr"]
    assert ins["beta"] == 1.57 and ins["n_obs"] == 30 and ins["min_obs"] == 60
    assert "30" in ins["reason"] and "60" in ins["reason"]
    assert out["n_obs"]["advanced_metrics_twr"] == 30


def test_bordo_soglia_59_insufficiente_60_sufficiente(monkeypatch):
    am = _motori(monkeypatch, twr=(.5, 59), risk=(1.5, 60), factor=(1.4, 60))
    out = am.reconcile_betas()
    assert "advanced_metrics_twr" in out["sources_insufficient"]
    assert set(out["betas"]) == {"portfolio_risk_spy", "factor_model_mkt"}
    assert out["verdict"] == "RECONCILED"


def test_n_obs_non_dichiarato_e_insufficiente_con_motivo(monkeypatch):
    am = _motori(monkeypatch, twr=(.9, None), risk=(1.0, 120), factor=(.95, None))
    with language_context("it"):
        out = am.reconcile_betas()
    assert out["verdict"] == "INSUFFICIENT_SOURCES"
    assert out["n_obs"]["advanced_metrics_twr"] is None
    assert "non dichiar" in out["sources_insufficient"]["advanced_metrics_twr"]["reason"]
    assert "non dichiar" in out["sources_insufficient"]["factor_model_mkt"]["reason"]


def test_unreliable_porta_range_e_mediana_non_per_decisioni(monkeypatch):
    am = _motori(monkeypatch, twr=(.39, 100), risk=(.84, 200), factor=(1.57, 300))
    out = am.reconcile_betas()
    assert out["verdict"] == "UNRELIABLE"
    assert "beta_consensus" not in out
    assert out["beta_per_decisioni"] is False
    ind = out["indicative"]
    assert ind["uso"] == "non_per_decisioni"
    assert ind["range"] == [.39, 1.57]
    assert ind["median"] == .84
    assert sorted(ind["basis"]) == sorted(out["betas"])
    assert ind["note"]


def test_una_sola_fonte_sufficiente_mai_reconciled(monkeypatch):
    am = _motori(monkeypatch, twr=(.9, 20), risk=(1.0, 150), factor=(.95, 30))
    out = am.reconcile_betas()
    assert out["verdict"] == "INSUFFICIENT_SOURCES"
    assert "beta_consensus" not in out and "max_spread" not in out
    assert out["beta_per_decisioni"] is False
    assert out["betas"] == {"portfolio_risk_spy": 1.0}
    assert set(out["sources_insufficient"]) == {"advanced_metrics_twr", "factor_model_mkt"}


def test_tutte_insufficienti_dichiarate(monkeypatch):
    am = _motori(monkeypatch, twr=(.9, 20), risk=(1.0, 25), factor=(.95, 30))
    out = am.reconcile_betas()
    assert out["verdict"] == "INSUFFICIENT_SOURCES"
    assert out["betas"] == {}
    assert len(out["sources_insufficient"]) == 3
    assert out["beta_per_decisioni"] is False
    assert "beta_consensus" not in out and "indicative" not in out


def test_motore_fattoriale_dichiara_il_minimo_per_titolo(monkeypatch):
    am = _motori(monkeypatch, twr=(.9, 100), risk=(.95, 100), factor=(1.0, 61))
    out = am.reconcile_betas()
    assert out["n_obs"]["factor_model_mkt"] == 61


def test_compute_metrics_espone_le_osservazioni_del_beta():
    from bellomberg.portfolio.advanced_metrics import compute_metrics
    rng = np.random.default_rng(7)
    b = rng.normal(0, .01, 75)
    r = 1.2 * b + rng.normal(0, .002, 75)
    out = compute_metrics(r, benchmark=b)
    assert out["benchmark"]["n_obs"] == 75


def test_portfolio_metrics_passa_n_obs_al_guardrail(monkeypatch):
    # cablaggio: il beta TWR arriva da compute_metrics con le sue osservazioni, non da un numero a parte
    from bellomberg.portfolio import advanced_metrics as am, portfolio_risk, portfolio_factors
    rng = np.random.default_rng(3)
    b = rng.normal(0, .01, 45)
    monkeypatch.setattr(am, "portfolio_metrics",
                        lambda: am.compute_metrics(1.1 * b + rng.normal(0, .001, 45), benchmark=b))
    monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk",
                        lambda: {"portfolio": {"beta_vs_spy": 1.1}, "beta_obs": 200})
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors", lambda: {"error": "finto"})
    out = am.reconcile_betas()
    assert out["n_obs"]["advanced_metrics_twr"] == 45
    assert "advanced_metrics_twr" in out["sources_insufficient"]


@pytest.mark.parametrize("motori,verdetto", [
    (dict(twr=(.39, 100), risk=(.84, 200), factor=(1.57, 300)), "UNRELIABLE"),
    (dict(twr=(.9, 20), risk=(1.0, 150), factor=(.95, 30)), "INSUFFICIENT_SOURCES"),
    (dict(twr=(.9, 20), risk=(1.0, 25), factor=(.95, 30)), "INSUFFICIENT_SOURCES"),
])
def test_capo_non_puo_usare_il_beta_fuori_da_reconciled(monkeypatch, motori, verdetto):
    am = _motori(monkeypatch, **motori)
    rb = am.reconcile_betas()
    assert rb["verdict"] == verdetto
    testo = am.testo_guardrail_beta_capo(rb)
    assert "verdetto: " + verdetto in testo
    assert "NON e' un argomento decisionale" in testo
    assert "vietati verdetti di hedge basati sul beta" in testo
    # i numeri indicativi (range/mediana) NON arrivano al Capo
    assert "non_per_decisioni" not in testo and "mediana" not in testo.lower()
    # fuori da RECONCILED nessun valore per fonte (tre valori grezzi = range/mediana leggibili)
    assert "beta:" not in testo
    for k, v in motori.items():
        assert str(v[0]) not in testo, (k, testo)


def test_capo_con_reconciled_riceve_il_consenso(monkeypatch):
    am = _motori(monkeypatch, twr=(.91, 140), risk=(1.02, 230), factor=(.97, 400))
    testo = am.testo_guardrail_beta_capo(am.reconcile_betas())
    assert "verdetto: RECONCILED" in testo and "consenso: 0.97" in testo
    assert "NON e' un argomento decisionale" not in testo.split("REGOLA:")[0]


def test_payload_identico_nelle_due_lingue(monkeypatch):
    am = _motori(monkeypatch, twr=(.39, 30), risk=(.84, 200), factor=(1.57, 300))
    with language_context("it"):
        it = am.reconcile_betas()
    with language_context("en"):
        en = am.reconcile_betas()
    for k in ("verdict", "betas", "n_obs", "min_obs", "beta_per_decisioni"):
        assert it[k] == en[k], k
    assert it["indicative"]["range"] == en["indicative"]["range"]
    assert en["indicative"]["uso"] == "non_per_decisioni"
    assert "30" in en["sources_insufficient"]["advanced_metrics_twr"]["reason"]


# ------------------------------------------------- cablaggio nel prompt del Capo (run vera offline)

from test_cablaggio_consigliere_multi import run_offline  # noqa: E402,F401  (fixture della run offline)


@pytest.mark.parametrize("payload_motori,verdetto", [
    (dict(twr=(.9, 20), risk=(1.0, 150), factor=(.95, 30)), "INSUFFICIENT_SOURCES"),
    (dict(twr=(.39, 100), risk=(.84, 200), factor=(1.57, 300)), "UNRELIABLE"),
])
def test_la_run_passa_al_capo_il_divieto_e_non_la_mediana(run_offline, monkeypatch, payload_motori, verdetto):
    import sys
    from bellomberg.agents import consigliere_multi as cm
    with monkeypatch.context() as veri:   # il payload si calcola sui moduli VERI, poi la fixture riprende
        for nome, mod in (("advanced_metrics", _AM_VERO), ("portfolio_risk", _PR_VERO), ("portfolio_factors", _PF_VERO)):
            veri.setitem(sys.modules, "bellomberg.portfolio." + nome, mod)
        vero = _motori(veri, **payload_motori)
        rb = vero.reconcile_betas()
    assert rb["verdict"] == verdetto
    finto = sys.modules["bellomberg.portfolio.advanced_metrics"]   # il modulo finto della fixture
    monkeypatch.setattr(finto, "reconcile_betas", lambda: rb, raising=False)
    monkeypatch.setattr(finto, "testo_guardrail_beta_capo", vero.testo_guardrail_beta_capo, raising=False)
    cm.run_multi_agent()
    ctx = run_offline.catturato["sizing_context"]
    assert "verdetto: " + verdetto in ctx, ctx[-600:]
    assert "verdetto diverso da RECONCILED: il beta NON e' un argomento decisionale" in ctx, ctx[-600:]
    assert "non_per_decisioni" not in ctx
    assert run_offline.catturato["bb"].data["_beta_reconcile"]["verdict"] == verdetto


# ------------------------------------------------- correzioni della review R-4 (05/10)

def test_reconciled_con_fonte_esclusa_la_nomina_nella_nota(monkeypatch):
    # C1: la pagina di oggi legge betas/sources_failed/note: la fonte esclusa non deve sparire muta
    am = _motori(monkeypatch, twr=(1.57, 30), risk=(.84, 200), factor=(.80, 300))
    with language_context("it"):
        out = am.reconcile_betas()
    assert out["verdict"] == "RECONCILED"
    assert "advanced_metrics_twr (30/60)" in out["note"] and "riconciliati 2 motori su 3" in out["note"]


def test_reconciled_senza_esclusioni_non_ha_nota(monkeypatch):
    am = _motori(monkeypatch, twr=(.91, 140), risk=(1.02, 230), factor=(.97, 400))
    assert "note" not in am.reconcile_betas()


def test_unreliable_con_fonte_esclusa_indicativi_solo_sulle_sufficienti(monkeypatch):
    # M5: range/mediana/basis NON includono la fonte esclusa; la nota la nomina
    am = _motori(monkeypatch, twr=(3.0, 20), risk=(.40, 200), factor=(1.60, 300))
    with language_context("it"):
        out = am.reconcile_betas()
    assert out["verdict"] == "UNRELIABLE"
    ind = out["indicative"]
    assert ind["basis"] == ["factor_model_mkt", "portfolio_risk_spy"]
    assert ind["range"] == [.40, 1.60] and ind["median"] == 1.0
    assert "NON usare il beta" in out["note"] and "advanced_metrics_twr (20/60)" in out["note"]
    # M6: il Capo sa QUALI fonti sono escluse (senza il loro beta)
    testo = am.testo_guardrail_beta_capo(out)
    assert "fonti escluse per osservazioni insufficienti: advanced_metrics_twr (20 oss.)" in testo
    assert "3.0" not in testo


def test_beta_non_finito_scartato_e_dichiarato(monkeypatch):
    # C3: NaN nel fattoriale non avvelena il consenso
    am = _motori(monkeypatch, twr=(.9, 100), risk=(1.0, 200), factor=(float("nan"), 300))
    with language_context("it"):
        out = am.reconcile_betas()
    assert "factor_model_mkt" not in out["betas"] and "factor_model_mkt" not in out["n_obs"]
    assert "non finito" in out["sources_failed"]["factor_model_mkt"]
    assert out["beta_consensus"] == .95


def test_beta_infinito_in_testa_non_maschera_la_divergenza(monkeypatch):
    am = _motori(monkeypatch, twr=(float("inf"), 100), risk=(.40, 200), factor=(1.60, 300))
    out = am.reconcile_betas()
    assert out["verdict"] == "UNRELIABLE" and "advanced_metrics_twr" in out["sources_failed"]


def test_legacy_tail_align_e_insufficiente(monkeypatch):
    # C2: coppie allineate per POSIZIONE (twr_engine KO, date NAV disallineate) non sono date comuni
    from bellomberg.portfolio import advanced_metrics as am, portfolio_risk, portfolio_factors
    import bellomberg.portfolio.twr_engine as twr
    import bellomberg.portfolio.portfolio_analytics as pa
    import bellomberg.market_data.benchmark_series as bsm
    import bellomberg.market_data.market_inputs as mi
    rng = np.random.default_rng(11)
    n, nb = 200, 150
    ratio = np.cumprod(1 + rng.normal(0, .01, n))
    monkeypatch.setattr(twr, "compute_twr_payload", lambda: {"error": "twr finto spento"})
    monkeypatch.setattr(pa, "compute_nav_history",
                        lambda: {"pnl_eur": list(ratio - 1), "cost_basis_eur": [1.0] * n,
                                 "dates": ["2099-01-01"] * (n - 3)})
    monkeypatch.setattr(bsm, "compute_benchmark_series",
                        lambda ticker=None, twr_payload=None: {
                            "dates": ["g%d" % i for i in range(nb + 1)], "ret_daily": list(rng.normal(0, .01, nb)),
                            "carried_flags": [False] * (nb + 1), "carried_days": 0})
    monkeypatch.setattr(mi, "get_risk_free", lambda c: .02)
    m = am.portfolio_metrics()
    assert "tail" in str(m["benchmark_alignment"])
    assert m["benchmark"]["n_obs"] is None and m["benchmark"]["allineamento"] == "posizionale"
    monkeypatch.setattr(am, "portfolio_metrics", lambda: m)
    monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk",
                        lambda: {"portfolio": {"beta_vs_spy": 1.0}, "beta_obs": 200})
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors", lambda: {"error": "finto"})
    with language_context("it"):
        out = am.reconcile_betas()
    ins = out["sources_insufficient"]["advanced_metrics_twr"]
    assert ins["n_obs"] is None and "posizionale" in ins["reason"]
    assert out["verdict"] == "INSUFFICIENT_SOURCES"


def test_compute_metrics_conta_le_coppie_col_benchmark_piu_corto():
    # M1: n_obs = coppie usate (benchmark piu' corto), non le righe della serie
    from bellomberg.portfolio.advanced_metrics import compute_metrics
    rng = np.random.default_rng(5)
    out = compute_metrics(rng.normal(0, .01, 80), benchmark=rng.normal(0, .01, 50))
    assert out["n_obs"] == 80 and out["benchmark"]["n_obs"] == 50


def test_fattoriale_minimo_non_in_testa(monkeypatch):
    # M2: il minimo per titolo e' il minimo vero, non il primo titolo
    from bellomberg.portfolio import portfolio_factors
    am = _motori(monkeypatch, twr=(.9, 100), risk=(.95, 100))
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors", lambda: {
        "portfolio_aggregate": {"beta_market": 1.0},
        "per_holding": {"ZZAAA": {"n_obs": 700}, "ZZBBB": {"n_obs": 45}, "ZZCCC": {"n_obs": 300}}})
    out = am.reconcile_betas()
    assert out["n_obs"]["factor_model_mkt"] == 45 and "factor_model_mkt" in out["sources_insufficient"]


@pytest.mark.parametrize("n", [True, False, "120", 120.5, float("nan"), -3])
def test_n_obs_non_numerico_o_non_intero_non_e_dichiarato(monkeypatch, n):
    # M3: bool/stringhe/non interi/negativi non contano come osservazioni
    am = _motori(monkeypatch, twr=(.9, n), risk=(1.0, 120), factor=(.95, 300))
    out = am.reconcile_betas()
    assert out["n_obs"]["advanced_metrics_twr"] is None
    assert "advanced_metrics_twr" in out["sources_insufficient"]


def test_soglia_passata_come_parametro_e_rispettata(monkeypatch):
    # M4: min_obs=90 (alternativa al 5% di falsi allarmi) davvero applicato
    am = _motori(monkeypatch, twr=(.9, 80), risk=(1.0, 120), factor=(.95, 300))
    out = am.reconcile_betas(min_obs=90)
    assert out["min_obs"] == 90
    assert out["sources_insufficient"]["advanced_metrics_twr"]["min_obs"] == 90
    assert set(out["betas"]) == {"portfolio_risk_spy", "factor_model_mkt"}


@pytest.mark.parametrize("rb", [None, {}, {"error": "numpy non disponibile"}])
def test_capo_guardrail_non_disponibile_vieta_comunque(rb):
    from bellomberg.portfolio.advanced_metrics import testo_guardrail_beta_capo
    testo = testo_guardrail_beta_capo(rb)
    assert "GUARDRAIL BETA non disponibile" in testo
    assert "il beta NON e' un argomento decisionale valido (vietati verdetti di hedge basati sul beta)" in testo


@pytest.mark.parametrize("finto,atteso", [
    ("solleva", "GUARDRAIL BETA non disponibile (RuntimeError)"),
    ("vuoto", "GUARDRAIL BETA non disponibile (verdetto assente)"),
])
def test_la_run_dichiara_il_guardrail_rotto_al_capo(run_offline, monkeypatch, finto, atteso):
    import sys
    from bellomberg.agents import consigliere_multi as cm
    vero = _AM_VERO
    modulo = sys.modules["bellomberg.portfolio.advanced_metrics"]   # il modulo finto della fixture

    def _rotto():
        raise RuntimeError("guardrail finto rotto")
    monkeypatch.setattr(modulo, "reconcile_betas", _rotto if finto == "solleva" else (lambda: {}), raising=False)
    monkeypatch.setattr(modulo, "testo_guardrail_beta_capo", vero.testo_guardrail_beta_capo, raising=False)
    cm.run_multi_agent()
    ctx = run_offline.catturato["sizing_context"]
    assert atteso in ctx, ctx[-600:]
    assert "vietati verdetti di hedge basati sul beta" in ctx
