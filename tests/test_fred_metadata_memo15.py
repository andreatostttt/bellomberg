"""Real helper AST, synthetic HTTP only: no provider, config or DB imports."""
import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from bellomberg.core import current_facts as cf

SOURCE = Path(__file__).resolve().parents[1] / 'src/bellomberg/agents/agent_tools.py'


def helper(monkeypatch, payload):
    config = ModuleType('bellomberg.core.config')
    config.FRED_API_KEY = 'offline-secret-must-not-escape'
    monkeypatch.setitem(sys.modules, config.__name__, config)
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_fred_fetch_series')
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    scope = {'_FRED_CACHE': {}, '_FRED_TTL_S': 900, '_req': SimpleNamespace(get=get)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['_fred_fetch_series'], calls


def live(monkeypatch, data):
    module = ModuleType('bellomberg.agents.agent_tools')
    module._fred_fetch_series = lambda series, last_n=3: data if series == 'CPIAUCSL' else {'observations': []}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return '\n'.join(cf._live_numbers())


def payload(value='.'):
    return {'realtime_start': '2031-06-03', 'realtime_end': '2031-06-03', 'api_key': 'DO-NOT-COPY',
            'observations': [{'date': '2031-05-01', 'value': value, 'realtime_start': '2031-06-03', 'realtime_end': '2031-06-03'},
                             {'date': '2031-04-01', 'value': '100'}, {'date': '2030-04-01', 'value': '80'}]}


@pytest.mark.parametrize('bad', ['.', None, True, 'nan', 'inf'])
def test_rejected_latest_is_preserved_and_never_rolls_back(monkeypatch, bad):
    fetch, _ = helper(monkeypatch, payload(bad))
    data = fetch('CPIAUCSL', 14)
    assert data['observations'] == [{'date': '2030-04-01', 'value': 80.0}, {'date': '2031-04-01', 'value': 100.0}]
    assert data['rejected_observations'][0]['date'] == '2031-05-01'
    text = live(monkeypatch, data)
    assert 'CPI USA: n.d.' in text and '+25.0% a/a' not in text
    assert '2031-05-01' in text and '2031-04-01' in text and 'scartat' in text


def test_realtime_whitelist_and_cache(monkeypatch):
    fetch, calls = helper(monkeypatch, payload())
    data = fetch('CPIAUCSL', 14)
    assert data['realtime_start'] == data['realtime_end'] == '2031-06-03'
    assert data['observation_metadata'][0]['realtime_start'] == '2031-06-03'
    assert 'api_key' not in str(data) and 'DO-NOT-COPY' not in str(data)
    assert fetch('CPIAUCSL', 14) == data and len(calls) == 1


def test_intermediate_rejection_retains_valid_same_month_pair(monkeypatch):
    raw = payload()
    raw['observations'] = [{'date': '2031-04-01', 'value': '100'}, {'date': '2031-03-01', 'value': '.'}, {'date': '2030-04-01', 'value': '80'}]
    fetch, _ = helper(monkeypatch, raw)
    text = live(monkeypatch, fetch('CPIAUCSL', 14))
    assert '+25.0% a/a' in text and '2031-04-01 / 2030-04-01' in text
    assert '2031-03-01' in text and 'scartat' in text and '2031-06-03' in text


def test_absent_metadata_is_explicit(monkeypatch):
    text = live(monkeypatch, {'observations': [{'date': '2031-04-01', 'value': 100}, {'date': '2030-04-01', 'value': 80}]})
    assert '+25.0% a/a' in text and 'metadati scarti n.d.' in text and 'vintage n.d.' in text
