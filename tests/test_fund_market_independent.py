"""Independent Fund boundary proofs: frozen observations, sandbox DB, no providers."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _alias_store_present(tmp_path, monkeypatch):
    # The Fund refresher resolves Yahoo symbols like the price updater; an absent
    # private alias store is a declared failure, so tests provide an empty valid one.
    import bellomberg.storage.negozi_privati as stores
    path = tmp_path / "alias_fonti.json"
    path.write_text('{"yfinance": {}}', encoding="utf-8")
    monkeypatch.setattr(stores, "PERCORSO_ALIAS", str(path))



NOW = datetime(2030, 3, 4, 12, tzinfo=timezone.utc)
MODE = 'fundamentals_research_v1'


@pytest.fixture
def fund_snapshot(tmp_path):
    database = tmp_path / 'fund.sqlite'
    cache = tmp_path / 'market-cache'
    cache.mkdir()
    with sqlite3.connect(database) as conn:
        conn.executescript('''
            CREATE TABLE positions(ticker TEXT, nome TEXT, quantita REAL, is_active INT);
            CREATE TABLE favorite_companies(ticker TEXT, name TEXT);
            CREATE TABLE position_prices(id INTEGER PRIMARY KEY, ticker TEXT,
                prezzo REAL, valuta TEXT, source TEXT, timestamp TEXT);
            CREATE TABLE trade_idea_runs(id TEXT, ticker TEXT, company_name TEXT,
                currency TEXT, request_json TEXT, result_json TEXT, context_json TEXT,
                progress_json TEXT, technical_status TEXT, created_at TEXT,
                updated_at TEXT, finished_at TEXT, memo_id INT);
        ''')
        conn.execute("INSERT INTO positions VALUES ('ACTIVE.X','Active position',2,1)")
        conn.execute("INSERT INTO favorite_companies VALUES ('FAVORITE.X','Followed company')")
    return database, cache


def _run(database, ticker, ident, timestamp, *, mode, summary):
    result = {'ticker': ticker, 'summary': summary, 'judgment': 'watch',
              'scenarios': [{'name': 'Base', 'analysis': 'Fixture assumption, not market consensus'}],
              'price_targets': {'mean': 9000},
              'consensus': {'mean': 9000, 'source': 'Synthetic run assumption'}}
    request = {'analysis_mode': mode} if mode else {}
    with sqlite3.connect(database) as conn:
        conn.execute('INSERT INTO trade_idea_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, ticker, 'Synthetic company', 'USD', json.dumps(request),
             json.dumps(result), '{}', '{}', 'completed', timestamp,
             timestamp, timestamp, 31))
    return result


def _market(cache, ticker='ACTIVE.X', **changes):
    value = {
        'ticker': ticker, 'identity_symbol': ticker,
        'observation_kind': 'market_consensus', 'observation_contract': 'market-consensus/1',
        'source': 'Frozen Yahoo analyst observation', 'currency': 'USD',
        'currency_source': 'provider.currency', 'acquired_at': NOW.isoformat(),
        'data_as_of': None, 'price_targets': {'mean': 50, 'median': 49,
                                            'number_of_analysts': 7},
        'quote': {'value': 40, 'currency': 'USD', 'identity_symbol': ticker,
                  'source': 'Frozen Yahoo market price', 'observed_at': NOW.isoformat(),
                  'acquired_at': NOW.isoformat()},
    }
    value.update(changes)
    path = cache / (sha256(ticker.encode('utf-8')).hexdigest() + '.json')
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def _read(database, cache, **kwargs):
    from bellomberg.market_data.fundamentals_view import research_view
    return research_view(database, cache_dir=cache, now=NOW, **kwargs)


def _row(view, ticker='ACTIVE.X'):
    return next(row for row in view['items'] if row['ticker'] == ticker)


def _bytes_under(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in Path(root).rglob('*') if path.is_file()}


def test_legacy_rows_cannot_enter_current_fund_or_shadow_research(fund_snapshot):
    database, cache = fund_snapshot
    current = _run(database, 'RESEARCH.X', 'research-original', '2030-03-01T10:00:00Z',
                   mode=MODE, summary='Current research conclusion')
    _run(database, 'RESEARCH.X', 'later-legacy', '2030-03-03T10:00:00Z',
         mode=None, summary='Legacy workbook prose must remain archived')
    _run(database, 'LEGACY-ONLY.X', 'archive-only', '2030-03-03T10:00:00Z',
         mode=None, summary='Archive-only company')
    _run(database, 'ACTIVE.X', 'active-legacy', '2030-03-03T10:00:00Z',
         mode=None, summary='Held company with legacy-only analysis')
    before = database.read_bytes()
    out = _read(database, cache, detail=True)
    assert {row['ticker'] for row in out['items']} == {'ACTIVE.X', 'FAVORITE.X', 'RESEARCH.X'}
    analysis = _row(out, 'RESEARCH.X')['analysis']
    assert analysis['run_id'] == 'research-original' and analysis['content'] == current
    assert _row(out)['analysis']['status'] == 'missing'
    assert database.read_bytes() == before


def test_run_estimates_are_separate_from_saved_market_consensus(fund_snapshot):
    database, cache = fund_snapshot
    original = _run(database, 'ACTIVE.X', 'run-estimate', '2030-03-03T10:00:00Z',
                    mode=MODE, summary='Independent run opinion')
    before_database = database.read_bytes()
    absent = _row(_read(database, cache, detail=True))
    assert absent['consensus']['mean'] is None
    assert absent['analysis']['content'] == original
    _market(cache)
    present = _row(_read(database, cache, detail=True))
    assert present['consensus']['mean'] == 50
    assert present['analysis']['content'] == original
    assert present['comparison']['upside_pct'] == 25.0
    assert database.read_bytes() == before_database


@pytest.mark.parametrize('provenance', [
    {}, {'observation_kind': 'run_estimate', 'observation_contract': 'market-consensus/1'},
    {'observation_kind': 'market_consensus', 'observation_contract': 'unknown/99'},
])
def test_unmarked_or_run_cache_is_not_market_consensus(fund_snapshot, provenance):
    database, cache = fund_snapshot
    path = _market(cache)
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw.pop('observation_kind')
    raw.pop('observation_contract')
    raw.update(provenance)
    path.write_text(json.dumps(raw), encoding='utf-8')
    before = path.read_bytes()
    row = _row(_read(database, cache))
    assert row['consensus']['status'] != 'available'
    assert row['consensus']['mean'] is None and row['comparison']['upside_pct'] is None
    assert row['quote']['value'] is None
    assert path.read_bytes() == before


def test_fresh_quote_does_not_refresh_target_date_or_invent_provider_date(fund_snapshot):
    database, cache = fund_snapshot
    stale_acquisition = (NOW - timedelta(days=3)).isoformat()
    _market(cache, acquired_at=stale_acquisition)
    row = _row(_read(database, cache))
    assert row['quote']['status'] == 'available' and row['quote']['value'] == 40
    assert row['consensus']['acquired_at'] == stale_acquisition
    assert row['consensus']['data_as_of'] is None
    assert row['consensus']['status'] == 'stale'
    assert row['comparison']['upside_pct'] is None


def test_repeated_fund_gets_are_read_only_and_never_fetch_providers(fund_snapshot, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api.fundamentals_routes import create_fundamentals_router
    from bellomberg.market_data import consensus_estimates
    database, cache = fund_snapshot
    _market(cache)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('GET must not fetch or refresh consensus')

    monkeypatch.setattr(consensus_estimates, 'get_consensus', forbidden)
    app = FastAPI()
    app.include_router(create_fundamentals_router(lambda: None,
        db_provider=lambda: database, cache_dir=cache, roots=[tmp_path / 'absent-archive']))
    before = _bytes_under(tmp_path)
    with TestClient(app) as client:
        for _ in range(3):
            listed = client.get('/fundamentals/research')
            company = client.get('/fundamentals/research/ACTIVE.X')
            assert listed.status_code == company.status_code == 200
            assert listed.headers['cache-control'] == company.headers['cache-control'] == 'no-store'
    assert calls == [] and _bytes_under(tmp_path) == before
    assert not list(tmp_path.rglob('*.xlsx'))


def test_concurrent_sections_merge_and_delayed_responses_do_not_regress_cache(fund_snapshot):
    from bellomberg.market_data.consensus_estimates import persist_market_observation
    database, cache = fund_snapshot
    path = _market(cache)
    initial = json.loads(path.read_text(encoding='utf-8'))
    before_database = database.read_bytes()
    consensus = {key: deepcopy(value) for key, value in initial.items()
                 if key not in ('quote', 'observation_kind', 'observation_contract')}
    quote = deepcopy(initial['quote'])

    for sequence in range(1, 9):
        acquired = (NOW + timedelta(minutes=sequence)).isoformat()
        consensus.update(acquired_at=acquired, price_targets={'mean': 50 + sequence})
        quote.update(acquired_at=acquired, observed_at=acquired, value=40 + sequence)
        status = {'status': 'success', 'last_attempt_at': acquired,
                  'last_success_at': acquired, 'consecutive_failures': 0,
                  'error': None, 'retry_after': None}
        start = Barrier(2)

        def publish_consensus():
            start.wait(timeout=3)
            return persist_market_observation('ACTIVE.X', cache_dir=cache,
                consensus=consensus, consensus_refresh=status)

        def publish_quote():
            start.wait(timeout=3)
            return persist_market_observation('ACTIVE.X', cache_dir=cache,
                quote=quote, quote_refresh=status)

        with ThreadPoolExecutor(max_workers=2) as threads:
            first = threads.submit(publish_consensus)
            second = threads.submit(publish_quote)
            first.result(timeout=5)
            second.result(timeout=5)
        stored = json.loads(path.read_text(encoding='utf-8'))
        assert stored['price_targets']['mean'] == 50 + sequence
        assert stored['quote']['value'] == 40 + sequence
        assert stored['acquired_at'] == stored['quote']['acquired_at'] == acquired

    newest = json.loads(path.read_text(encoding='utf-8'))
    late_failure = {'status': 'failed', 'last_attempt_at': NOW.isoformat(),
                    'last_success_at': None, 'consecutive_failures': 1,
                    'error': 'Late result from an older request',
                    'retry_after': (NOW + timedelta(minutes=5)).isoformat()}
    persist_market_observation('ACTIVE.X', cache_dir=cache, consensus={**consensus,
        'acquired_at': NOW.isoformat(), 'price_targets': {'mean': 9999}},
        quote={**quote, 'acquired_at': NOW.isoformat(), 'observed_at': NOW.isoformat(), 'value': 9999},
        quote_refresh=late_failure, consensus_refresh=late_failure)
    assert json.loads(path.read_text(encoding='utf-8')) == newest
    assert database.read_bytes() == before_database


def test_periodic_quote_failure_keeps_last_success_and_backoff(fund_snapshot, monkeypatch):
    from bellomberg.market_data import fund_market_refresh as refresh
    database, cache = fund_snapshot
    _run(database, 'RESEARCH.X', 'new-mode', NOW.isoformat(), mode=MODE,
         summary='New research joins followed symbols')
    _run(database, 'LEGACY.X', 'old-mode', NOW.isoformat(), mode=None,
         summary='Legacy-only research stays archived')
    before_database = database.read_bytes()
    calls = []
    fail = set()

    class FrozenTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def info(self):
            calls.append((self.ticker, 'quote'))
            if self.ticker in fail:
                raise TimeoutError('Synthetic Yahoo read timeout; no live request')
            return {'symbol': self.ticker, 'currency': 'USD',
                    'regularMarketPrice': 40, 'regularMarketTime': NOW.timestamp(),
                    'numberOfAnalystOpinions': 7}

        @property
        def analyst_price_targets(self):
            calls.append((self.ticker, 'consensus'))
            return {'current': 40, 'mean': 50, 'median': 49, 'low': 42, 'high': 60}

        def __getattr__(self, name):
            raise AssertionError('Periodic quote/targets acquisition requested heavy field: ' + name)

    monkeypatch.setattr(refresh, '_ticker_factory', FrozenTicker)
    monkeypatch.setattr(refresh, 'TICKER_PAUSE_SECONDS', 0)
    refresh.refresh_followed_market_data(database, cache_dir=cache, now=NOW)
    assert sorted(calls) == sorted((ticker, kind)
        for ticker in ('ACTIVE.X', 'FAVORITE.X', 'RESEARCH.X')
        for kind in ('quote', 'consensus'))
    path = cache / (sha256(b'ACTIVE.X').hexdigest() + '.json')
    successful = json.loads(path.read_text(encoding='utf-8'))
    calls.clear()
    refresh.refresh_followed_market_data(database, cache_dir=cache, now=NOW + timedelta(minutes=14))
    assert calls == []

    fail.add('ACTIVE.X')
    attempt_at = NOW + timedelta(minutes=16)
    refresh.refresh_followed_market_data(database, cache_dir=cache, now=attempt_at)
    assert sorted(calls) == sorted((ticker, 'quote')
        for ticker in ('ACTIVE.X', 'FAVORITE.X', 'RESEARCH.X'))
    failed = json.loads(path.read_text(encoding='utf-8'))
    assert failed['quote'] == successful['quote']
    assert failed['price_targets'] == successful['price_targets']
    assert failed['acquired_at'] == successful['acquired_at']
    assert failed['quote_refresh']['status'] == 'failed' and failed['quote_refresh']['error']
    retry_at = datetime.fromisoformat(failed['quote_refresh']['retry_after'])
    assert retry_at > attempt_at
    calls.clear()
    refresh.refresh_followed_market_data(database, cache_dir=cache, now=retry_at - timedelta(seconds=1))
    assert not any(ticker == 'ACTIVE.X' for ticker, _ in calls)
    fail.clear()
    refresh.refresh_followed_market_data(database, cache_dir=cache, now=retry_at + timedelta(seconds=1))
    recovered = json.loads(path.read_text(encoding='utf-8'))
    assert recovered['quote_refresh']['status'] == 'success'
    assert recovered['quote']['acquired_at'] != successful['quote']['acquired_at']
    assert recovered['acquired_at'] == successful['acquired_at']
    assert recovered['data_as_of'] is None
    assert database.read_bytes() == before_database and not list(cache.rglob('*.xlsx'))


def test_weekly_only_current_research_receives_market_data(fund_snapshot, monkeypatch):
    from bellomberg.core.research_analysis import research_digest
    from bellomberg.market_data import fund_market_refresh as refresh
    database, cache = fund_snapshot
    dossiers = {'WEEKLY.X': {'ticker': 'WEEKLY.X', 'documents': [], 'issues': []}}
    reports = {'fundamentals': 'Frozen weekly thesis for a new nonportfolio candidate'}
    seal = {'analysis_mode': MODE, 'version': 1, 'dossiers': dossiers,
            'reports': reports, 'tool_receipts': []}
    seal['dossier_sha256'] = research_digest({'dossiers': dossiers, 'tool_receipts': []})
    seal['thesis_sha256'] = research_digest({'reports': reports,
                                           'dossier_sha256': seal['dossier_sha256']})
    payload = {'data': {'_research_thesis': seal,
                       'fundamentals': {'1': reports['fundamentals'], '2': 'Frozen final research'}}}
    with sqlite3.connect(database) as conn:
        conn.execute('''CREATE TABLE weekly_runs(memo_id INT, run_id TEXT, context_json TEXT,
                        state_json TEXT, snapshot_json TEXT, updated_at TEXT)''')
        conn.execute('INSERT INTO weekly_runs VALUES (?,?,?,?,?,?)',
            (40, 'weekly-current', json.dumps({'contract': {'analysis_mode': MODE}}),
             json.dumps({'status': 'completed'}),
             json.dumps({'payload': payload, 'sha256': research_digest(payload)}), NOW.isoformat()))
    assert _row(_read(database, cache), 'WEEKLY.X')['analysis']['origin'] == 'weekly'
    before_database = database.read_bytes()
    calls = []

    def factory(ticker):
        calls.append(ticker)
        return SimpleNamespace(info={'symbol': ticker, 'currency': 'USD',
            'regularMarketPrice': 40, 'regularMarketTime': NOW.timestamp()},
            analyst_price_targets={'current': 40, 'mean': 50})

    monkeypatch.setattr(refresh, '_ticker_factory', factory)
    monkeypatch.setattr(refresh, 'TICKER_PAUSE_SECONDS', 0)
    refresh.refresh_followed_market_data(database, cache_dir=cache, now=NOW)
    assert set(calls) == {'ACTIVE.X', 'FAVORITE.X', 'WEEKLY.X'}
    row = _row(_read(database, cache), 'WEEKLY.X')
    assert row['quote']['value'] == 40 and row['consensus']['mean'] == 50
    assert database.read_bytes() == before_database
