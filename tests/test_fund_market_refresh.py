"""Periodic Fund market observations: frozen providers, existing read-only DB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest


NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
MODE = 'fundamentals_research_v1'


def _path(cache, ticker):
    return cache / (sha256(ticker.encode()).hexdigest() + '.json')


def _read(cache, ticker):
    return json.loads(_path(cache, ticker).read_text(encoding='utf-8'))


@pytest.fixture
def market(tmp_path, monkeypatch):
    from bellomberg.market_data import fund_market_refresh as refresh
    database = tmp_path / 'watchlist.sqlite'
    with sqlite3.connect(database) as conn:
        conn.executescript('CREATE TABLE positions(ticker TEXT, is_active INTEGER, quantita REAL);'
            'CREATE TABLE favorite_companies(ticker TEXT);'
            'CREATE TABLE trade_idea_runs(ticker TEXT, request_json TEXT);')
        conn.executemany('INSERT INTO positions VALUES(?,?,?)',
            [('ALPHA.X', 1, 2), ('SHORT.X', 1, -1), ('INACTIVE.X', 0, 5), ('ZERO.X', 1, 0)])
        conn.executemany('INSERT INTO favorite_companies VALUES(?)', [('ALPHA.X',), ('ETF.X',)])
        conn.executemany('INSERT INTO trade_idea_runs VALUES(?,?)',
            [('RESEARCH.X', json.dumps({'analysis_mode': MODE})), ('LEGACY.X', '{}')])
    cache, calls, faults = tmp_path / 'cache', [], {}
    state = {'observed_at': NOW - timedelta(hours=18), 'price': 10., 'mean': 12.}
    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker
        @property
        def info(self):
            calls.append((self.ticker, 'info'))
            if 'info' in faults:
                raise faults['info']
            return {'symbol': faults.get('symbol', self.ticker), 'currency': faults.get('currency', 'EUR'),
                'regularMarketPrice': faults.get('price', state['price']),
                'regularMarketTime': faults.get('timestamp', state['observed_at'].timestamp()),
                'quoteType': 'ETF' if self.ticker == 'ETF.X' else 'EQUITY',
                'numberOfAnalystOpinions': 5}
        @property
        def analyst_price_targets(self):
            calls.append((self.ticker, 'targets'))
            if 'targets' in faults:
                raise faults['targets']
            return {'current': state['price'], 'mean': state['mean'], 'median': 11.5, 'low': 9., 'high': 15.}
        def __getattr__(self, name):
            pytest.fail('Periodic refresh invoked heavyweight endpoint: ' + name)
    monkeypatch.setattr(refresh, '_ticker_factory', Ticker)
    monkeypatch.setattr(refresh, 'TICKER_PAUSE_SECONDS', 0)
    return SimpleNamespace(module=refresh, db=database, cache=cache, calls=calls, faults=faults, state=state, ticker=Ticker)


def test_read_only_universe_all_followed_and_new_research_no_legacy(market, monkeypatch):
    before = market.db.read_bytes()
    connect = sqlite3.connect
    connections = []
    def read_only(path, *args, **kwargs):
        connections.append((str(path), kwargs.get('uri')))
        assert 'mode=ro' in str(path) and kwargs.get('uri') is True
        return connect(path, *args, **kwargs)
    monkeypatch.setattr(market.module.sqlite3, 'connect', read_only)
    result = market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW)
    assert result['status'] == 'completed' and result['universe_count'] == 4
    assert result['counts']['quotes_updated'] == result['counts']['consensus_updated'] == 4
    assert {ticker for ticker, endpoint in market.calls if endpoint == 'info'} == {'ALPHA.X', 'SHORT.X', 'ETF.X', 'RESEARCH.X'}
    assert ('ETF.X', 'targets') not in market.calls
    assert market.db.read_bytes() == before and connections
    for ticker in ('ALPHA.X', 'SHORT.X', 'ETF.X', 'RESEARCH.X'):
        saved = _read(market.cache, ticker)
        assert saved['observation_kind'] == 'market_consensus' and saved['observation_contract'] == 'market-consensus/1'
        assert saved['identity_symbol'] == ticker and saved['currency'] == 'EUR'
        assert saved['acquired_at'] == NOW.isoformat() and saved['data_as_of'] is None
        assert saved['quote']['observed_at'] == market.state['observed_at'].isoformat()
        assert saved['quote']['acquired_at'] == NOW.isoformat()
    etf = _read(market.cache, 'ETF.X')
    assert etf['consensus_status'] == 'not_applicable' and etf['price_targets']['mean'] is None


def test_quote_refresh_preserves_target_acquisition_and_daily_cadence(market):
    run = market.module.refresh_followed_market_data
    run(market.db, cache_dir=market.cache, now=NOW)
    target = deepcopy(_read(market.cache, 'ALPHA.X')['price_targets'])
    market.state.update(price=11., mean=99.)
    minute = NOW + timedelta(minutes=1)
    result = run(market.db, cache_dir=market.cache, now=minute)
    assert result['counts']['quotes_updated'] == result['counts']['consensus_updated'] == 0
    assert len(market.calls) == 7
    later = NOW + timedelta(minutes=16)
    result = run(market.db, cache_dir=market.cache, now=later)
    saved = _read(market.cache, 'ALPHA.X')
    assert saved['quote']['value'] == 11 and saved['quote']['acquired_at'] == later.isoformat()
    assert saved['acquired_at'] == NOW.isoformat() and saved['price_targets'] == target
    assert result['counts']['consensus_updated'] == 0
    daily = NOW + timedelta(hours=24, minutes=1)
    run(market.db, cache_dir=market.cache, now=daily)
    saved = _read(market.cache, 'ALPHA.X')
    assert saved['acquired_at'] == daily.isoformat() and saved['price_targets']['mean'] == 99.
    assert saved['data_as_of'] is None


def test_provider_failure_retains_last_valid_with_error_and_backoff(market):
    run = market.module.refresh_followed_market_data
    run(market.db, cache_dir=market.cache, now=NOW)
    original = _read(market.cache, 'ALPHA.X')
    market.faults['info'] = TimeoutError('frozen provider timeout')
    later = NOW + timedelta(days=1)
    result = run(market.db, cache_dir=market.cache, now=later)
    saved = _read(market.cache, 'ALPHA.X')
    assert result['status'] == 'partial' and result['counts']['failures'] == 4
    assert saved['quote'] == original['quote'] and saved['price_targets'] == original['price_targets']
    assert saved['acquired_at'] == original['acquired_at']
    assert saved['quote_refresh']['status'] == saved['consensus_refresh']['status'] == 'failed'
    assert 'TimeoutError' in saved['quote_refresh']['error']
    assert saved['quote_refresh']['last_attempt_at'] == later.isoformat()
    requests = len(market.calls)
    run(market.db, cache_dir=market.cache, now=later + timedelta(seconds=60))
    assert len(market.calls) == requests


def test_consensus_failure_does_not_prevent_fresh_quote_or_change_consensus_date(market):
    run = market.module.refresh_followed_market_data
    run(market.db, cache_dir=market.cache, now=NOW)
    market.faults['targets'] = OSError('frozen targets outage')
    market.state['price'] = 13.
    result = run(market.db, cache_dir=market.cache, now=NOW + timedelta(days=1))
    saved = _read(market.cache, 'ALPHA.X')
    assert result['status'] == 'partial'
    assert saved['acquired_at'] == NOW.isoformat() and saved['price_targets']['mean'] == 12.
    assert saved['quote']['value'] == 13. and saved['consensus_refresh']['status'] == 'failed'


@pytest.mark.parametrize('fault,value', [('symbol', 'OTHER.X'), ('currency', None), ('price', float('nan')), ('timestamp', None)])
def test_invalid_quote_never_becomes_an_observation(market, fault, value):
    market.faults[fault] = value
    result = market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW)
    assert result['status'] == 'partial' and result['counts']['quotes_updated'] == 0
    assert _read(market.cache, 'ALPHA.X').get('quote') is None
    assert _read(market.cache, 'ALPHA.X')['quote_refresh']['status'] == 'failed'


def test_unattested_old_excel_cache_is_never_promoted_on_failure(market):
    market.cache.mkdir()
    _path(market.cache, 'ALPHA.X').write_text(json.dumps({'ticker': 'ALPHA.X', 'source': 'old workbook',
        'acquired_at': NOW.isoformat(), 'price_targets': {'mean': 99999.}}), encoding='utf-8')
    market.faults['info'] = OSError('frozen outage')
    market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW)
    saved = _read(market.cache, 'ALPHA.X')
    assert saved.get('acquired_at') is None and saved.get('price_targets', {}).get('mean') is None
    assert saved['consensus_refresh']['status'] == 'failed'


def test_missing_database_is_explicit_and_does_not_create_anything(tmp_path):
    from bellomberg.market_data.fund_market_refresh import refresh_followed_market_data
    database, cache = tmp_path / 'absent.sqlite', tmp_path / 'absent-cache'
    result = refresh_followed_market_data(database, cache_dir=cache, now=NOW)
    assert result['status'] == 'unavailable' and result['universe_count'] is None and result['errors']
    assert not database.exists() and not cache.exists()


def test_stop_event_stops_between_symbols(market):
    class Stop:
        stopped = False
        def is_set(self):
            return self.stopped
        def wait(self, delay):
            self.stopped = True
            return True
    result = market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW, stop_event=Stop())
    assert result['status'] == 'stopped' and result['counts']['processed'] == 1
    assert len([row for row in market.calls if row[1] == 'info']) == 1


def test_shared_atomic_merge_retains_newer_quote_and_full_consensus_fields(market):
    from bellomberg.market_data.consensus_estimates import persist_market_observation
    market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW)
    original = _read(market.cache, 'ALPHA.X')
    quote = {**original['quote'], 'value': 14., 'observed_at': NOW.isoformat(),
             'acquired_at': (NOW + timedelta(minutes=1)).isoformat()}
    consensus = {**original, 'price_targets': {**original['price_targets'], 'mean': 18.},
        'eps_estimates': [{'period': '+1y', 'avg': 2.}], 'details_acquired_at': NOW.isoformat(),
        'acquired_at': (NOW + timedelta(minutes=2)).isoformat()}
    for key in ('quote', 'quote_refresh', 'consensus_refresh'):
        consensus.pop(key, None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(persist_market_observation, 'ALPHA.X', cache_dir=market.cache, quote=quote),
                   pool.submit(persist_market_observation, 'ALPHA.X', cache_dir=market.cache, consensus=consensus)]
        for future in futures:
            future.result()
    persist_market_observation('ALPHA.X', cache_dir=market.cache, quote=original['quote'], consensus=original)
    saved = _read(market.cache, 'ALPHA.X')
    assert saved['quote'] == quote and saved['price_targets']['mean'] == 18.
    assert saved['eps_estimates'] == consensus['eps_estimates']
    assert saved['acquired_at'] == consensus['acquired_at']
    assert not list(market.cache.glob('*.tmp'))


def test_ordinary_consensus_writes_attested_full_contract_without_losing_details(tmp_path, monkeypatch):
    from bellomberg.market_data import consensus_estimates as module
    from bellomberg.core import paths
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(module, '_CACHE', {})
    monkeypatch.setattr(module.time, 'time', lambda: NOW.timestamp())
    fake = SimpleNamespace(info={'symbol': 'ORDINARY.X', 'currency': 'GBp', 'regularMarketPrice': 100.,
        'regularMarketTime': (NOW - timedelta(hours=1)).timestamp(), 'numberOfAnalystOpinions': 3},
        analyst_price_targets={'current': 100., 'mean': 120.}, earnings_estimate=None, revenue_estimate=None,
        eps_trend=None, recommendations_summary=None)
    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda ticker: fake))
    result = module.get_consensus('ORDINARY.X')
    saved = _read(tmp_path / 'consensus_cache', 'ORDINARY.X')
    assert result['observation_kind'] == saved['observation_kind'] == 'market_consensus'
    assert saved['observation_contract'] == 'market-consensus/1'
    assert all(key in saved for key in ('eps_estimates', 'revenue_estimates', 'eps_revisions', 'recommendations'))
    assert saved['quote']['currency'] == 'GBp' and saved['quote']['acquired_at'] == NOW.isoformat()
    assert saved['acquired_at'] == NOW.isoformat() and saved['data_as_of'] is None


def test_provider_rate_limit_stops_batch_and_persists_shared_cooldown(market):
    market.faults['info'] = RuntimeError('HTTP 429 Too Many Requests from frozen provider')
    result = market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW)
    assert result['status'] == 'partial' and result['counts']['processed'] == 1
    assert result['counts']['not_attempted'] == 3 and result['provider_backoff_until']
    assert len(market.calls) == 1
    market.faults.clear()
    delayed = market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW + timedelta(minutes=1))
    assert delayed['status'] == 'backoff' and len(market.calls) == 1
    retry = datetime.fromisoformat(result['provider_backoff_until']) + timedelta(seconds=1)
    market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=retry)
    assert len(market.calls) > 1


def test_ordinary_missing_dataframe_values_are_null_and_do_not_drop_snapshot(tmp_path, monkeypatch):
    from bellomberg.market_data import consensus_estimates as module
    from bellomberg.core import paths
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(module, '_CACHE', {})
    monkeypatch.setattr(module.time, 'time', lambda: NOW.timestamp())
    missing = SimpleNamespace(empty=False, iterrows=lambda: iter([('+1y', {
        'avg': float('nan'), 'current': float('nan'), '30daysAgo': float('nan'), '90daysAgo': 2.})]))
    fake = SimpleNamespace(info={'symbol': 'MISSING.X', 'currency': 'EUR', 'regularMarketPrice': 10.,
        'regularMarketTime': (NOW - timedelta(hours=1)).timestamp()},
        analyst_price_targets={'current': 10., 'mean': 12.}, earnings_estimate=missing,
        revenue_estimate=missing, eps_trend=missing, recommendations_summary=None)
    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda ticker: fake))
    result = module.get_consensus('MISSING.X')
    assert result['eps_estimates'][0]['avg'] is None
    assert result['eps_revisions'][0]['current'] is None
    saved = _read(tmp_path / 'consensus_cache', 'MISSING.X')
    assert saved['price_targets']['mean'] == 12. and saved['eps_estimates'][0]['avg'] is None


def test_production_acquisition_clock_is_read_after_provider_response(market, monkeypatch):
    clock = {'now': NOW}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now']
    original = market.ticker
    class SlowTicker(original):
        @property
        def info(self):
            clock['now'] = NOW + timedelta(hours=1)
            market.state['observed_at'] = NOW + timedelta(minutes=10)
            return super().info
    monkeypatch.setattr(market.module, 'datetime', Clock)
    monkeypatch.setattr(market.module, '_ticker_factory', SlowTicker)
    result = market.module.refresh_followed_market_data(market.db, cache_dir=market.cache)
    assert result['counts']['quotes_updated'] == 4
    saved = _read(market.cache, 'ALPHA.X')
    assert saved['quote']['acquired_at'] == clock['now'].isoformat()
    assert saved['quote']['observed_at'] > result['started_at']


def test_later_failure_from_older_reader_keeps_concurrent_last_success(market):
    from bellomberg.market_data.consensus_estimates import persist_market_observation, refresh_receipt
    market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW)
    earlier = _read(market.cache, 'ALPHA.X')
    success_at, failure_at = NOW + timedelta(minutes=20), NOW + timedelta(minutes=30)
    quote = {**earlier['quote'], 'value': 15., 'acquired_at': success_at.isoformat()}
    persist_market_observation('ALPHA.X', cache_dir=market.cache, quote=quote,
        quote_refresh=refresh_receipt(earlier['quote_refresh'], success_at))
    saved = persist_market_observation('ALPHA.X', cache_dir=market.cache,
        quote_refresh=refresh_receipt(earlier['quote_refresh'], failure_at, 'Failed after another writer succeeded'))
    assert saved['quote'] == quote
    assert saved['quote_refresh']['last_success_at'] == success_at.isoformat()
    assert saved['quote_refresh']['last_attempt_at'] == failure_at.isoformat()


def test_full_consensus_details_survive_target_failure(tmp_path, monkeypatch):
    from bellomberg.market_data import consensus_estimates as module
    from bellomberg.core import paths
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(module, '_CACHE', {})
    monkeypatch.setattr(module.time, 'time', lambda: NOW.timestamp())
    class Provider:
        info = {'symbol': 'DETAIL.X', 'currency': 'EUR', 'regularMarketPrice': 10.,
                'regularMarketTime': NOW.timestamp()}
        earnings_estimate = SimpleNamespace(empty=False, iterrows=lambda: iter([('+1y', {'avg': 2.})]))
        revenue_estimate = eps_trend = recommendations_summary = None
        @property
        def analyst_price_targets(self):
            raise TimeoutError('Only targets unavailable')
    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda ticker: Provider()))
    result = module.get_consensus('DETAIL.X')
    assert result['eps_estimates'][0]['avg'] == 2.
    saved = _read(tmp_path / 'consensus_cache', 'DETAIL.X')
    assert saved['eps_estimates'] == result['eps_estimates']
    assert saved['acquired_at'] is None and saved['consensus_refresh']['status'] == 'failed'
    assert saved['details_acquired_at'] == NOW.isoformat()


def test_new_full_details_do_not_regress_newer_periodic_targets(market):
    from bellomberg.market_data.consensus_estimates import persist_market_observation
    market.module.refresh_followed_market_data(market.db, cache_dir=market.cache, now=NOW)
    original = _read(market.cache, 'ALPHA.X')
    newer = {**original, 'acquired_at': (NOW + timedelta(minutes=30)).isoformat(), 'price_targets': {'mean': 20.}}
    persist_market_observation('ALPHA.X', cache_dir=market.cache, consensus=newer)
    full = {**original, 'details_acquired_at': (NOW + timedelta(hours=1)).isoformat(),
            'eps_estimates': [{'period': '+1y', 'avg': 2.}]}
    saved = persist_market_observation('ALPHA.X', cache_dir=market.cache, consensus=full)
    assert saved['price_targets']['mean'] == 20. and saved['acquired_at'] == newer['acquired_at']
    assert saved['eps_estimates'] == full['eps_estimates'] and saved['details_acquired_at'] == full['details_acquired_at']
