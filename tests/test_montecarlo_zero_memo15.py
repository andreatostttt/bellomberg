"""Zero pure baseline uses the same real simulation and cache as omitted size."""
import pytest
from test_montecarlo_dichiarato import mc_pieno
from bellomberg.portfolio import portfolio_montecarlo as pm


@pytest.mark.parametrize('zero', [0, 0.0, -0.0])
@pytest.mark.parametrize('zero_first', [False, True])
def test_zero_and_omitted_baseline_share_real_cache(mc_pieno, monkeypatch, zero, zero_first):
    downloads = []
    original = pm._download_returns
    def counted(*args, **kwargs):
        downloads.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(pm, '_download_returns', counted)
    first = mc_pieno(**({'new_alloc': zero} if zero_first else {}))
    second = mc_pieno(**({} if zero_first else {'new_alloc': zero}))
    assert 'error' not in first and 'error' not in second
    assert second == first
    assert len(downloads) == 1 and len(pm._CACHE) == 1
    assert first['what_if_allocation'] is None


def test_explicit_empty_ticker_lists_are_pure_baseline(mc_pieno):
    baseline = mc_pieno()
    assert mc_pieno(new_alloc=0, add_tickers=[], remove_tickers=[]) == baseline


def test_forced_zero_baseline_matches_real_simulation(mc_pieno):
    baseline = mc_pieno(force_refresh=True)
    zero = mc_pieno(force_refresh=True, new_alloc=0)
    assert 'error' not in zero
    assert {k: v for k, v in zero.items() if k != 'timestamp'} == {
        k: v for k, v in baseline.items() if k != 'timestamp'}


@pytest.mark.parametrize('value', [False, True])
def test_boolean_never_means_baseline(mc_pieno, value):
    assert 'error' in mc_pieno(new_alloc=value)


@pytest.mark.parametrize('changes', [
    {'add_tickers': ['ZZNEW']}, {'remove_tickers': ['ZZAAA']},
    {'_override_weights': {'ZZAAA': 1}}, {'_override_weights': {}},
    {'_override_nav': 100000}, {'_override_nav': 0},
])
def test_zero_with_any_position_change_or_override_stays_error(mc_pieno, changes):
    assert 'error' in mc_pieno(new_alloc=0, **changes)


@pytest.mark.parametrize('value', [0.05, '0.05', -0.1, 1, float('nan'), float('inf')])
def test_nonzero_without_add_stays_error(mc_pieno, value):
    assert 'error' in mc_pieno(new_alloc=value)


def test_positive_add_and_override_contracts_unchanged(mc_pieno):
    assert 'error' in mc_pieno(new_alloc=0.05, add_tickers=['ZZNEW'], _override_weights={'ZZAAA': 1})
    out = mc_pieno(new_alloc=0.05, add_tickers=['ZZNEW'])
    assert out['weights']['ZZNEW'] == pytest.approx(0.05)
    assert out['what_if_allocation']['origin'] == 'parametro_chiamante'
