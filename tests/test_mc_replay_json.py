"""Regressione 02/10/2026: i replay deterministici (gfc_2008, covid_2020 con
orizzonte <= finestra) producono simulazioni identiche; il Cornish-Fisher
diventava NaN e l'endpoint rispondeva 500 in testo semplice (FastAPI non
serializza NaN). Zero rete: `_download_returns` e le holding sono stubbate."""
import json
import math

import numpy as np
import pandas as pd
import pytest

import bellomberg.portfolio.portfolio_montecarlo as pm


class _FakeDB:
    def get_portfolio_summary(self):
        return {"positions": [], "totale_valore_mercato_eur": 100000.0}


@pytest.fixture
def mc_offline_costante(monkeypatch):
    """Rendimenti tutti nulli: ogni traiettoria simulata e' identica, come un replay pieno."""
    idx = pd.bdate_range("2022-01-03", periods=400)
    rdf = pd.DataFrame({"AAA": np.zeros(400), "BBB": np.zeros(400)}, index=idx)
    monkeypatch.setattr(pm, "MemoryDB", _FakeDB)
    monkeypatch.setattr(pm, "_get_holdings_weights",
                        lambda salta: ({"AAA": 0.6, "BBB": 0.4}, 100000.0))
    monkeypatch.setattr(pm, "_download_returns", lambda *a, **k: rdf)
    pm.invalidate_cache()
    yield
    pm.invalidate_cache()


def test_cornish_fisher_su_campione_costante_e_none():
    assert pm._cornish_fisher_var(np.full(500, -0.153), alpha=0.01) is None


def test_cornish_fisher_su_campione_normale_resta_un_numero():
    rng = np.random.default_rng(7)
    v = pm._cornish_fisher_var(rng.normal(0, 0.02, 5000), alpha=0.01)
    assert isinstance(v, float) and math.isfinite(v) and v < 0


def test_payload_con_campione_costante_e_serializzabile(mc_offline_costante):
    out = pm.run_monte_carlo(horizon_days=21, n_sims=1000, lookback_years=1,
                             method="block_bootstrap", drift_mode="zero",
                             stress_scenario="none", force_refresh=True)
    assert "error" not in out, out.get("error")
    assert out["var_99_cornish_fisher_pct"] is None
    json.dumps(out, allow_nan=False)  # nessun NaN/inf da nessuna parte
