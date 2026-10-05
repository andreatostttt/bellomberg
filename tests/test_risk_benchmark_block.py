"""Blocco SPY del rischio: stesse formule e stessi giorni del portafoglio."""
import numpy as np
import pandas as pd

from bellomberg.portfolio.portfolio_risk import _benchmark_block


def test_benchmark_usa_solo_i_giorni_del_portafoglio():
    idx = pd.bdate_range("2026-01-01", periods=60)
    rng = np.random.default_rng(3)
    port = pd.Series(rng.normal(0, 0.01, 60), index=idx)
    spy = pd.Series(rng.normal(0, 0.012, 80), index=pd.bdate_range("2025-12-01", periods=80))
    out = _benchmark_block(port, spy)
    common = port.index.intersection(spy.index)
    r = spy.loc[common]
    assert out["n_obs"] == len(common)
    assert out["vol_annual_pct"] == round(float(r.std() * np.sqrt(252) * 100), 2)
    assert out["var_95_1d_pct"] == round(float(np.percentile(r, 5) * 100), 2)
    assert out["beta_vs_spy"] == 1.0
    assert out["max_dd_1y_pct"] <= 0


def test_benchmark_troppo_corto_e_none():
    idx = pd.bdate_range("2026-01-01", periods=15)
    s = pd.Series(np.full(15, 0.001), index=idx)
    assert _benchmark_block(s, s) is None
