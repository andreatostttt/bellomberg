"""Synthetic observations and fake providers; no network or production DB."""
from datetime import date
from types import SimpleNamespace

import pytest

from bellomberg.core import freshness as f
from bellomberg.portfolio import positioning_tools as pt
from test_macro_yoy_memo15 import consumer, data


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: pytest.fail('unexpected live HTTP'))


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(f, 'SNAP_PATH', str(tmp_path / 'snapshot.json'))


def test_unchanged_value_with_new_observation_is_fresh(snapshot):
    f.check_and_update({'vix': {'value': 20, 'obs_date': '2032-01-01'}}, date(2032, 1, 1))
    out = f.check_and_update({'vix': {'value': 20, 'obs_date': '2032-02-01'}}, date(2032, 2, 1))
    assert out['fresh'] == 1 and out['stale'] == []


@pytest.mark.parametrize('od', [None, '', 'bad', '2032-02-30', '2032-03-01', '2032-02-01garbage'])
def test_unknown_dates_are_not_fresh_and_are_rendered(snapshot, od):
    out = f.check_and_update({'vix': {'value': 20, 'obs_date': od}}, date(2032, 2, 1))
    assert out['fresh'] == 0 and out['stale'] == []
    assert len(out['unknown']) == 1
    assert 'vix' in f.format_for_memo(out)
    assert 'vix' in f.format_for_capo(out)
    assert 'n.d.' in f.format_for_memo(out)


def test_counts_reconcile_missing_value_and_stale(snapshot):
    out = f.check_and_update({
        'missing': {'value': None, 'obs_date': '2032-02-01'},
        'vix': {'value': 20, 'obs_date': '2032-01-01'},
        'us_cpi_yoy': {'value': 3, 'obs_date': '2032-01-01'},
        'undated': {'value': 7}}, date(2032, 2, 1))
    assert out['checked'] == out['fresh'] + len(out['stale']) + len(out['unknown']) == 4
    assert out['fresh'] == 1 and len(out['stale']) == 1 and len(out['unknown']) == 2


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), False, True, 'NaN', 'inf', 'not-numeric'])
def test_invalid_values_cannot_be_fresh(snapshot, value):
    out = f.check_and_update({'vix': {'value': value, 'obs_date': '2032-02-01'}}, date(2032, 2, 1))
    assert out['fresh'] == 0 and len(out['unknown']) == 1 and out['stale'] == []
    assert out['checked'] == 1 and 'valore' in out['unknown'][0]


@pytest.mark.parametrize('value', [0, -1.5, 5.25, '0', '-1.5', '5.25'])
def test_finite_zero_negative_and_numeric_string_preserved(snapshot, value):
    out = f.check_and_update({'spread': {'value': value, 'obs_date': '2032-02-01'}}, date(2032, 2, 1))
    assert out['fresh'] == 1 and out['unknown'] == out['stale'] == []
    assert f._load()['spread']['value'] == repr(value)


@pytest.mark.parametrize('key,limit', [('vix', 6), ('claims', 12), ('us_cpi_yoy', 45), ('us_gdp_real_yoy', 130)])
def test_existing_threshold_boundaries(snapshot, key, limit):
    from datetime import timedelta
    today = date(2032, 6, 1)
    for age in (limit, limit + 1):
        out = f.check_and_update({key: {'value': 10, 'obs_date': (today - timedelta(days=age)).isoformat()}}, today)
        assert out['fresh'] == int(age == limit)
        assert len(out['stale']) == int(age > limit)


def test_hicp_index_becomes_exact_month_yoy():
    scope, calls = consumer(data(('2031-01-01', 100), ('2031-07-01', 110), ('2032-01-01', 120)))
    out = scope['tool_get_macro_indicator']('ez_cpi_yoy')
    assert out['yoy_pct'] == 20 and out['latest_value'] == 120
    assert out['comparison']['frequency'] == 'monthly'
    assert scope['tool_get_macro_dashboard']()['indicators']['ez_cpi_yoy']['yoy_pct'] == 20
    assert calls[0][0] == 'CP0000EZ19M086NEST'


def test_hicp_six_months_is_unavailable():
    scope, _ = consumer(data(('2031-07-01', 100), ('2032-01-01', 120)))
    out = scope['tool_get_macro_indicator']('ez_cpi_yoy')
    assert out['yoy_pct'] is None
    assert out['comparison']['status'] == 'unavailable'


@pytest.mark.parametrize('rows', [
    [('2031-01-01', 100), ('2031-01-02', 101), ('2032-01-01', 120)],
    [('2031-01-01', 100), ('2032-01-01', 120), ('2032-01-02', 121)],
    [('2031-01-01', 0), ('2032-01-01', 120)],
])
def test_hicp_reuses_annual_quality_guards(rows):
    scope, _ = consumer(data(*rows))
    out = scope['tool_get_macro_indicator']('ez_cpi_yoy')
    assert out['yoy_pct'] is None and out['comparison']['status'] == 'unavailable'


def test_real_macro_import_routes_metadata_without_native_changes(monkeypatch):
    from bellomberg.agents import agent_tools as at
    calls = []
    def fetch(series, last_n):
        calls.append(series)
        return data(('2031-01-01', 100), ('2032-01-01', 120))
    monkeypatch.setattr(at, '_fred_fetch_series', fetch)
    native = []
    def cached(key, fn):
        native.append(key)
        return {'date': '2032-01-01', 'value': 3}
    monkeypatch.setattr(at, '_native_cached', cached)
    assert 'error' in at.tool_get_macro_indicator('brazil_selic') and calls == []
    out = at.tool_get_macro_dashboard()
    assert out['indicators']['ez_cpi_yoy']['yoy_pct'] == 20
    assert out['indicators']['ez_cpi_yoy']['comparison']['frequency'] == 'monthly'
    assert out['indicators']['brazil_discount_rate']['value'] == 120
    assert 'brazil_selic' in out['indicators_not_available']
    assert len(calls) == len(at._FRED_INDICATORS)
    assert set(native) == set(at._NATIVE_INDICATORS)
    assert at.tool_get_macro_indicator('CP0000EZ19M086NEST')['mode'] == 'level'


def test_spread_is_percentage_points_without_changing_value():
    scope, _ = consumer(data(('2032-01-01', -0.5)))
    out = scope['tool_get_macro_indicator']('yield_curve_10y_2y')
    assert out['latest_value'] == -0.5
    assert 'percentage points' in out['description'] and 'bps' not in out['description']


def test_selic_is_not_discount_and_does_not_call_provider():
    scope, calls = consumer(data(('2032-01-01', 15)))
    out = scope['tool_get_macro_indicator']('brazil_selic')
    assert 'error' in out and 'latest_value' not in out and calls == []
    assert 'brazil_selic' in scope['tool_get_macro_dashboard']()['indicators_not_available']
    discount = scope['tool_get_macro_indicator']('brazil_discount_rate')
    assert discount['series_id'] == 'INTDSRBRM193N' and discount['latest_value'] == 15
    assert 'Discount' in discount['description'] and 'SELIC' not in discount['description']


def _cot(name, code='13874A'):
    return {'contract_market_name': name, 'cftc_contract_market_code': code,
            'report_date_as_yyyy_mm_dd': date.today().isoformat(),
            'open_interest_all': '1000', 'lev_money_positions_long': '120',
            'lev_money_positions_short': '20'}


@pytest.mark.parametrize('market', ['ES', 'SPX', 'SP500'])
def test_cot_no_micro_or_first_row_fallback(monkeypatch, market):
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: [_cot('MICRO E-MINI S&P 500')])
    monkeypatch.setattr(pt.requests, 'get', get)
    out = pt.get_cot_positioning(market)
    assert 'error' in out and 'net_positions' not in out and len(calls) == 1


def test_cot_exact_contract_selected_amid_substrings(monkeypatch):
    rows = [_cot('MICRO E-MINI S&P 500', 'WRONG'), _cot('E-MINI S&P 500')]
    monkeypatch.setattr(pt.requests, 'get', lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: rows))
    out = pt.get_cot_positioning('ES')
    assert out['contract_market_name'] == 'E-MINI S&P 500'
    assert out['cftc_contract_market_code'] == '13874A'


def test_cot_same_name_conflicting_contracts_is_unavailable(monkeypatch):
    rows = [_cot('E-MINI S&P 500', 'ONE'), _cot('E-MINI S&P 500', 'TWO')]
    monkeypatch.setattr(pt.requests, 'get', lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: rows))
    out = pt.get_cot_positioning('ES')
    assert 'error' in out and 'net_positions' not in out


@pytest.mark.parametrize('age', [15, 100, -1, None, 'invalid'])
def test_tff_without_fresh_date_suppresses_percentile_and_reading(monkeypatch, age):
    from datetime import timedelta
    rows = []
    for i in range(12):
        row = _cot('E-MINI S&P 500')
        row['report_date_as_yyyy_mm_dd'] = (date.today() - timedelta(days=(age if isinstance(age, int) else 0) + i * 7)).isoformat()
        row['lev_money_positions_long'] = str(200-i)
        row['lev_money_positions_short'] = '20'
        rows.append(row)
    if age is None or age == 'invalid':
        rows[0]['report_date_as_yyyy_mm_dd'] = age
    monkeypatch.setattr(pt.requests, 'get', lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: rows))
    out = pt.get_cot_positioning('ES')
    assert out['freshness']['status'] != 'FRESH'
    assert out['leveraged_funds_net_percentile_1y'] is None and out['reading'] is None
    assert out['net_positions']['leveraged_funds'] == 180


@pytest.mark.parametrize('age', [0, 14])
def test_tff_fresh_boundary_keeps_percentile(monkeypatch, age):
    from datetime import timedelta
    rows = []
    for i in range(12):
        row = _cot('E-MINI S&P 500')
        row.update(report_date_as_yyyy_mm_dd=(date.today() - timedelta(days=age + i*7)).isoformat(),
                   lev_money_positions_long=str(200-i), lev_money_positions_short='20')
        rows.append(row)
    monkeypatch.setattr(pt.requests, 'get', lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: rows))
    out = pt.get_cot_positioning('ES')
    assert out['freshness']['status'] == 'FRESH'
    assert out['leveraged_funds_net_percentile_1y'] == 100 and out['reading']
