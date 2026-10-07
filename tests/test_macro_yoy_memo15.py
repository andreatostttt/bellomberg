"""Consumer reale estratto AST; FRED finto contato, nessun import provider/DB."""
import ast
import os
from pathlib import Path

import pytest

SOURCE = Path(os.environ.get('R15_AGENT_SOURCE', Path(__file__).resolve().parents[1] / 'src/bellomberg/agents/agent_tools.py'))
KEYS = ['us_cpi_yoy', 'us_core_cpi_yoy', 'us_industrial_prod', 'us_retail_sales', 'us_gdp_real_yoy']


def consumer(data):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'_FRED_INDICATORS', '_FRED_YOY_PERIODS', '_FRED_REMOVED_NOTE'}
    nodes = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name in {'tool_get_macro_indicator', 'tool_get_macro_dashboard'})]
    calls = []
    def fetch(series, last_n):
        calls.append((series, last_n))
        return data
    scope = {'_fred_fetch_series': fetch, '_NATIVE_INDICATORS': {}, '_senza_chiavi': str}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope, calls


def data(*rows, **metadata):
    return {'observations': [{'date': d, 'value': v} for d, v in rows], 'rejected_observations': [], **metadata}


@pytest.mark.parametrize('key', KEYS)
def test_semester_is_not_yoy(key):
    scope, calls = consumer(data(('2032-02-01', 100), ('2032-08-01', 120)))
    out = scope['tool_get_macro_indicator'](key)
    assert out.get('yoy_pct') is None
    assert len(calls) == 1


@pytest.mark.parametrize('key', KEYS)
def test_exact_pair_unsorted(key):
    scope, calls = consumer(data(('2032-02-29', 120), ('2031-02-28', 100)))
    out = scope['tool_get_macro_indicator'](key)
    assert out['yoy_pct'] == 20
    assert out['year_ago_date'] == '2031-02-28'
    assert len(calls) == 1


def test_gdp_same_quarter_not_same_month():
    scope, _ = consumer(data(('2031-01-01', 100), ('2032-03-01', 125)))
    assert scope['tool_get_macro_indicator']('us_gdp_real_yoy')['yoy_pct'] == 25
    assert scope['tool_get_macro_indicator']('us_cpi_yoy')['yoy_pct'] is None


@pytest.mark.parametrize('bad', [0, -1, True, float('nan'), float('inf'), None])
@pytest.mark.parametrize('at_latest', [True, False])
def test_invalid_levels(bad, at_latest):
    scope, _ = consumer(data(('2031-01-01', 100 if at_latest else bad), ('2032-01-01', bad if at_latest else 120)))
    out = scope['tool_get_macro_indicator']('us_cpi_yoy')
    assert out['yoy_pct'] is None
    if at_latest:
        assert 'latest_value' not in out and 'error' in out
    else:
        assert out['latest_value'] == 120


@pytest.mark.parametrize('day', ['2032-01-02', '2031-01-02', 'bad-date'])
def test_duplicate_or_invalid_period(day):
    scope, _ = consumer(data(('2031-01-01', 100), ('2032-01-01', 120), (day, 130)))
    assert scope['tool_get_macro_indicator']('us_cpi_yoy')['yoy_pct'] is None


@pytest.mark.parametrize('day', ['2032-02-01', '2032-01-01', None])
def test_latest_rejection_dashboard(day):
    scope, calls = consumer(data(('2031-01-01', 100), ('2032-01-01', 120), rejected_observations=[{'date': day}]))
    out = scope['tool_get_macro_dashboard']()
    item = out['indicators']['us_cpi_yoy']
    assert 'value' not in item and 'error' in item
    assert item['quality']['latest_status'] == 'unavailable'
    assert item['last_available_value'] == 120
    assert item['comparison']['status'] == 'unavailable'
    assert 'real_fed_funds_pct' not in out
    assert len(calls) == len(scope['_FRED_INDICATORS'])


def test_intermediate_rejection_and_legacy_metadata():
    payload = data(('2031-01-01', 100), ('2032-01-01', 120), rejected_observations=[{'date': '2031-06-01'}])
    scope, _ = consumer(payload)
    out = scope['tool_get_macro_indicator']('us_cpi_yoy')
    assert out['yoy_pct'] == 20 and out['quality']['warning']
    del payload['rejected_observations']
    out = scope['tool_get_macro_indicator']('us_cpi_yoy')
    assert out['yoy_pct'] == 20 and out['quality']['metadata_status'] == 'unknown'


@pytest.mark.parametrize('key', ['CPIAUCSL', '10y_treasury', 'china_export_yoy'])
def test_raw_daily_native_level_unchanged(key):
    scope, calls = consumer(data(('2032-01-01', -5), ('2032-01-02', -4)))
    out = scope['tool_get_macro_indicator'](key)
    assert out['mode'] == 'level' and out['latest_value'] == -4
    assert 'yoy_pct' not in out and len(calls) == 1


def test_real_imports_wrapper_dashboard_and_native(monkeypatch):
    from bellomberg.agents import agent_tools as at
    from bellomberg.core.current_facts import _cpi_year_pair
    payload = data(('2031-01-01', 100), ('2032-01-01', 120))
    calls = []
    def fetch(series, last_n):
        calls.append(series)
        return payload
    monkeypatch.setattr(at, '_fred_fetch_series', fetch)
    native_calls = []
    def native(key, fn):
        native_calls.append(key)
        return {'value': 7, 'date': '2032-01-01'}
    monkeypatch.setattr(at, '_native_cached', native)
    indicator = at.tool_get_macro_indicator('us_cpi_yoy')
    assert indicator['yoy_pct'] == round(_cpi_year_pair(payload['observations'])[0], 2)
    dashboard = at.tool_get_macro_dashboard()
    assert dashboard['indicators']['us_cpi_yoy']['comparison'] == indicator['comparison']
    assert dashboard['indicators']['us_cpi_yoy']['quality'] == indicator['quality']
    assert len(calls) == 1 + len(at._FRED_INDICATORS)
    for key in ('uk_cpi_yoy', 'brazil_cpi_yoy'):
        assert at.tool_get_macro_indicator(key)['yoy_pct'] == 7
    assert len(native_calls) == len(at._NATIVE_INDICATORS) + 2


def test_missing_base_never_scores_cpi_or_real_fed():
    from bellomberg.agents.specialist_scores import macro_score
    scope, _ = consumer(data(('2032-02-01', 100), ('2032-08-01', 120)))
    dashboard = scope['tool_get_macro_dashboard']()
    assert 'real_fed_funds_pct' not in dashboard
    scored = macro_score(dashboard)
    assert scored['metrics']['cpi_yoy'] is None
    assert scored['metrics']['real_fed_funds_pct'] is None


def test_sparse_pair_is_not_row_thirteen():
    scope, _ = consumer(data(('2031-01-01', 100), ('2031-06-01', 110), ('2032-01-01', 120)))
    assert scope['tool_get_macro_indicator']('us_cpi_yoy')['yoy_pct'] == 20


@pytest.mark.parametrize('duplicate_day,ambiguous', [('2031-12-02', True), ('2031-06-02', False)])
def test_previous_period_duplicates_are_declared(duplicate_day, ambiguous):
    scope, _ = consumer(data(('2031-01-01', 100), ('2031-06-01', 105),
                             ('2031-12-01', 110), (duplicate_day, 111), ('2032-01-01', 120)))
    out = scope['tool_get_macro_indicator']('us_cpi_yoy')
    assert out['yoy_pct'] == 20
    assert out['quality']['duplicate_periods']
    assert len(out['history_last_12']) == 5
    if ambiguous:
        assert all(key not in out for key in ('prev_value', 'prev_date', 'change_vs_prev'))
        assert out['quality']['previous_status'] == 'ambiguous'
        assert out['quality']['previous_reason']
    else:
        assert out['quality']['previous_status'] == 'available'
        assert out['prev_value'] == 110 and out['change_vs_prev'] == 10
    raw = scope['tool_get_macro_indicator']('CPIAUCSL')
    assert 'prev_value' in raw and 'change_vs_prev' in raw


@pytest.mark.parametrize('extra', [-1, 106])
def test_real_wrapper_rejects_intermediate_invalid_or_duplicate(monkeypatch, extra):
    from bellomberg.agents import agent_tools as at
    from bellomberg.core import current_facts as cf
    payload = data(('2031-08-01', 100), ('2032-07-01', 105),
                   ('2032-07-02', extra), ('2032-08-01', 110))
    with pytest.raises(ValueError):
        cf._cpi_year_pair(payload['observations'])
    calls = []
    def fetch(series, last_n=3):
        calls.append(series)
        return payload
    monkeypatch.setattr(at, '_fred_fetch_series', fetch)
    lines = '\n'.join(cf._live_numbers())
    assert 'CPI USA: n.d.' in lines and '+10.0% a/a' not in lines
    assert calls.count('CPIAUCSL') == 1


@pytest.mark.parametrize('location', ['inline', 'metadata', 'newer_period'])
def test_real_previous_contaminated_by_rejection(monkeypatch, location):
    from bellomberg.agents import agent_tools as at
    payload = data(('2031-08-01', 100), ('2032-06-01', 105), ('2032-08-01', 110))
    if location == 'inline':
        payload['observations'].append({'date': '2032-06-02', 'value': -1})
    else:
        payload['rejected_observations'] = [{'date': '2032-07-01' if location == 'newer_period' else '2032-06-02'}]
    calls = []
    def fetch(series, last_n):
        calls.append(series)
        return payload
    monkeypatch.setattr(at, '_fred_fetch_series', fetch)
    out = at.tool_get_macro_indicator('us_cpi_yoy')
    assert out['yoy_pct'] == 10
    assert out['quality']['previous_status'] == 'unavailable'
    assert 'scart' in out['quality']['previous_reason']
    assert all(key not in out for key in ('prev_value', 'prev_date', 'change_vs_prev'))
    assert calls == ['CPIAUCSL']
