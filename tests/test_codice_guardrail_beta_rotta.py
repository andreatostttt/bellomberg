"""Codice STABILE del guardrail beta (BG-ROTTA, 06/10): una sola funzione pura
(`codice_guardrail_beta`) lo calcola per il punteggio quant E per la rotta
GET /portfolio/metrics/beta_reconcile (campo di primo livello `beta_guardrail`).
La rotta non dice mai NON_CALCOLATO (lei il calcolo lo fa): guasto o `error` = NON_DISPONIBILE.
Niente TestClient ne' backend: la funzione della rotta si chiama direttamente con
reconcile_betas sostituito; il cablaggio nella rotta si legge dal sorgente (ast).
Numeri e ticker INVENTATI.
"""
import ast
from pathlib import Path

import pytest

# moduli VERI presi all'import: la fixture della run offline mette dei finti in sys.modules
from bellomberg.portfolio import advanced_metrics as _AM_VERO
from bellomberg.agents import specialist_scores as _SCORER_VERO

_FONTE = "portfolio_risk_spy"
_PORT = {"vol_annual_pct": 22.5, "sharpe": 0.78, "beta_vs_spy": 1.37,
         "var_95_1d_pct": -3.2, "max_dd_1y_pct": -17.4}
_POS = {"positions": [{"ticker": "ZZTEST", "valore_mercato_eur": 600.0},
                      {"ticker": "QQSYN.MI", "valore_mercato_eur": 400.0}]}

# i 7 codici: payload -> codice atteso
CASI = [
    ({"verdict": "RECONCILED", "beta_per_decisioni": True,
      "betas": {_FONTE: 1.37, "factor_model_mkt": 1.29}}, "RECONCILED"),
    ({"verdict": "UNRELIABLE", "beta_per_decisioni": False, "betas": {_FONTE: 1.37}}, "UNRELIABLE"),
    ({"verdict": "INSUFFICIENT_SOURCES", "beta_per_decisioni": False}, "INSUFFICIENT_SOURCES"),
    (None, "NON_CALCOLATO"),
    ({"error": "RuntimeError"}, "NON_DISPONIBILE"),
    ({"verdict": "RECONCILED", "betas": {_FONTE: 1.37}}, "RECONCILED_SENZA_VIA_LIBERA"),
    ({"verdict": "RECONCILED", "beta_per_decisioni": True,
      "betas": {"factor_model_mkt": 1.29, "advanced_metrics_twr": 1.31},
      "sources_insufficient": {_FONTE: {"beta": 1.37, "n_obs": 41, "min_obs": 60}}},
     "RECONCILED_SENZA_FONTE_RISCHIO"),
]
# casi limite in piu' (stessa regola di quant_score)
CASI_LIMITE = [
    ({}, "NON_DISPONIBILE"),
    ("non un dict", "NON_DISPONIBILE"),
    ({"verdict": "UNRELIABLE", "error": "parziale"}, "NON_DISPONIBILE"),
    ({"verdict": "RECONCILED", "beta_per_decisioni": "si", "betas": {_FONTE: 1.37}},
     "RECONCILED_SENZA_VIA_LIBERA"),   # via libera solo con True vero
    ({"verdict": "RECONCILED", "beta_per_decisioni": True}, "RECONCILED_SENZA_FONTE_RISCHIO"),
    # un errore del motore vince SEMPRE, anche su RECONCILED con via libera e fonte di rischio
    ({"error": "parziale", "verdict": "RECONCILED", "beta_per_decisioni": True,
      "betas": {_FONTE: 1.37, "factor_model_mkt": 1.29}}, "NON_DISPONIBILE"),
]


@pytest.mark.parametrize("rb,codice", CASI, ids=[c for _, c in CASI])
def test_funzione_pura_sette_codici(rb, codice):
    assert _SCORER_VERO.codice_guardrail_beta(rb) == codice


@pytest.mark.parametrize("rb,codice", CASI_LIMITE)
def test_funzione_pura_casi_limite(rb, codice):
    assert _SCORER_VERO.codice_guardrail_beta(rb) == codice


def test_funzione_pura_non_muta_il_payload():
    rb = {"verdict": "UNRELIABLE", "betas": {_FONTE: 1.37}}
    copia = {"verdict": "UNRELIABLE", "betas": {_FONTE: 1.37}}
    _SCORER_VERO.codice_guardrail_beta(rb)
    assert rb == copia


def _rotta(monkeypatch, effetto):
    """Chiama la funzione della rotta con reconcile_betas sostituito; ritorna (payload, chiamate)."""
    chiamate = []

    def finto(threshold=0.35, **kw):
        chiamate.append(threshold)
        if isinstance(effetto, BaseException):
            raise effetto
        return effetto

    monkeypatch.setattr(_AM_VERO, "reconcile_betas", finto)
    return _SCORER_VERO.payload_rotta_beta_reconcile(threshold=0.42), chiamate


@pytest.mark.parametrize("rb,codice", [c for c in CASI if c[0] is not None and "error" not in c[0]],
                         ids=lambda x: x if isinstance(x, str) else "")
def test_rotta_aggiunge_il_codice_e_conserva_il_payload(monkeypatch, rb, codice):
    p, chiamate = _rotta(monkeypatch, dict(rb))
    assert chiamate == [0.42]                       # soglia passata al motore
    assert p["beta_guardrail"] == codice
    assert {k: v for k, v in p.items() if k != "beta_guardrail"} == rb   # niente altro cambia


@pytest.mark.parametrize("effetto", [
    RuntimeError("motore giu"),
    {"error": "numpy non disponibile"},
    {"error": "parziale", "verdict": "RECONCILED", "beta_per_decisioni": True, "betas": {_FONTE: 1.37}},
    None,
    ["non", "un", "dict"],
    {},
    {"betas": {_FONTE: 1.37}, "verdict": None},
], ids=["solleva", "error", "error_con_verdetto_ok", "None", "non_dict", "vuoto", "senza_verdetto"])
def test_rotta_guasto_e_non_disponibile_mai_non_calcolato(monkeypatch, effetto):
    p, _ = _rotta(monkeypatch, effetto)
    assert p["beta_guardrail"] == "NON_DISPONIBILE"
    assert p.get("error"), p                        # il buco e' DICHIARATO, non muto
    if isinstance(effetto, RuntimeError):
        assert "RuntimeError" in str(p["error"])
    if isinstance(effetto, dict) and "error" in effetto:
        assert p["error"] == effetto["error"]       # l'errore del motore resta verbatim
    elif isinstance(effetto, dict):
        assert "senza verdetto" in str(p["error"])  # payload senza verdetto: il buco ha un nome


def test_rotta_non_muta_il_payload_del_motore(monkeypatch):
    rb = {"verdict": "UNRELIABLE", "beta_per_decisioni": False}
    p, _ = _rotta(monkeypatch, rb)
    assert "beta_guardrail" not in rb and p["beta_guardrail"] == "UNRELIABLE"


@pytest.mark.parametrize("rb", [c[0] for c in CASI + CASI_LIMITE if isinstance(c[0], dict)])
def test_quant_score_e_rotta_stesso_codice(monkeypatch, rb):
    s = _SCORER_VERO.quant_score(_POS, {"portfolio": dict(_PORT)}, beta_reconcile=dict(rb))
    p, _ = _rotta(monkeypatch, dict(rb))
    assert s["metrics"]["beta_guardrail"] == p["beta_guardrail"] == _SCORER_VERO.codice_guardrail_beta(rb)


@pytest.mark.parametrize("rb,codice", CASI + CASI_LIMITE)
def test_quant_score_usa_la_funzione_pura(rb, codice):
    s = _SCORER_VERO.quant_score(_POS, {"portfolio": dict(_PORT)}, beta_reconcile=rb)
    assert s["metrics"]["beta_guardrail"] == codice


def test_rotta_api_cablata_sulla_funzione():
    """Il cablaggio: get_beta_reconcile in bellomberg_api.py delega a payload_rotta_beta_reconcile
    con la soglia ricevuta (letto dal sorgente: niente import del backend)."""
    src = Path(_SCORER_VERO.__file__).resolve().parents[1] / "api" / "bellomberg_api.py"
    albero = ast.parse(src.read_text(encoding="utf-8"))
    rotte = [n for n in ast.walk(albero) if isinstance(n, ast.FunctionDef) and n.name == "get_beta_reconcile"]
    assert len(rotte) == 1
    chiamate = [n for n in ast.walk(rotte[0]) if isinstance(n, ast.Call)
                and getattr(n.func, "id", getattr(n.func, "attr", None)) == "payload_rotta_beta_reconcile"]
    assert len(chiamate) == 1
    c = chiamate[0]
    passa_soglia = any(isinstance(a, ast.Name) and a.id == "threshold" for a in c.args) or any(
        k.arg == "threshold" and isinstance(k.value, ast.Name) and k.value.id == "threshold" for k in c.keywords)
    assert passa_soglia
    # il valore restituito dalla rotta e' quello della funzione
    ritorni = [n for n in ast.walk(rotte[0]) if isinstance(n, ast.Return)]
    assert any(r.value is c for r in ritorni)
