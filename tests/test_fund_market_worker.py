"""Background acquisition belongs to backend lifecycle, never to a Fund GET."""
import asyncio
import importlib
import json
import sqlite3
from threading import Event, Lock
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



def worker_module():
    return importlib.import_module('bellomberg.market_data.fund_market_worker')


def test_backend_lifecycle_starts_and_stops_market_updates(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api as api, trade_idea_routes
    module = worker_module()
    touched = Event()
    observed = []
    db = tmp_path / 'fund.sqlite'
    sqlite3.connect(db).close()

    def acquire(db_path, *, cache_dir, stop_event):
        observed.append((str(db_path), str(cache_dir), stop_event.is_set()))
        touched.set()
        return {'status': 'ready', 'updated': 1}

    monkeypatch.setattr(module, 'refresh_followed_market_data', acquire)
    monkeypatch.setattr(api, 'SQLITE_PATH', str(db))
    monkeypatch.setattr(api, 'DB_DIR', str(tmp_path))
    monkeypatch.setattr(trade_idea_routes, 'recover_orphan_runs', lambda _: {'status': 'ready'})
    app = SimpleNamespace(state=SimpleNamespace())

    async def boot():
        async with api.lifespan(app):
            assert touched.wait(2), 'ordinary startup did not begin market acquisition'
            assert app.state.valuation_automation['runner'] is None
        assert app.state.fund_market_worker.status()['status'] == 'stopped'

    asyncio.run(boot())
    assert observed == [(str(db), str(tmp_path / 'consensus_cache'), False)]


def test_worker_serializes_ticks_recovers_from_error_and_stops(tmp_path, monkeypatch):
    module = worker_module()
    complete = Event()
    lock = Lock()
    calls = 0
    active = 0
    peak = 0

    def acquire(*_args, stop_event, **_kwargs):
        nonlocal calls, active, peak
        with lock:
            calls += 1
            active += 1
            peak = max(peak, active)
        try:
            if calls == 1:
                raise OSError('synthetic acquisition unavailable')
            stop_event.set()
            complete.set()
            return {'status': 'ready', 'updated': 2}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(module, 'refresh_followed_market_data', acquire)
    runner = module.FundMarketWorker(tmp_path / 'fund.sqlite', tmp_path / 'cache', interval_seconds=0.01)
    runner.start()
    runner.start()
    assert complete.wait(2)
    runner.stop()
    state = runner.status()
    assert calls == 2 and peak == 1
    assert state['status'] == 'stopped' and state['last_result']['updated'] == 2
    assert state['last_completed_at'] and state['error'] is None


def test_stopping_is_not_claimed_complete_while_provider_is_still_running(tmp_path, monkeypatch):
    module = worker_module()
    entered, release = Event(), Event()

    def acquire(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return {'status': 'ready', 'updated': 1}

    monkeypatch.setattr(module, 'refresh_followed_market_data', acquire)
    runner = module.FundMarketWorker(tmp_path / 'fund.sqlite', tmp_path / 'cache')
    runner.start()
    try:
        assert entered.wait(2)
        runner.stop(timeout=0.01)
        assert runner.status()['status'] == 'stopping'
    finally:
        release.set()
        runner.stop()
    assert runner.status()['status'] == 'stopped'


def test_provider_backoff_is_waiting_not_a_successful_refresh(tmp_path, monkeypatch):
    import time
    module = worker_module()
    def acquire(*_args, **_kwargs):
        return {'status': 'backoff', 'retry_after': '2030-03-04T13:00:00Z',
                'counts': {'processed': 0, 'not_attempted': 2}}
    monkeypatch.setattr(module, 'refresh_followed_market_data', acquire)
    runner = module.FundMarketWorker(tmp_path / 'fund.sqlite', tmp_path / 'cache')
    runner.start()
    try:
        deadline = time.monotonic() + 2
        while runner.status()['last_result'] is None and time.monotonic() < deadline:
            Event().wait(0.001)
        state = runner.status()
        assert state['status'] == 'waiting'
        assert state['next_retry_at'] == '2030-03-04T13:00:00Z'
        assert state['last_result']['counts']['processed'] == 0
    finally:
        runner.stop()


def test_research_get_exposes_refresh_state_without_acquisition(tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.testclient import TestClient
    from bellomberg.api.fundamentals_routes import create_fundamentals_router
    db = tmp_path / 'fund.sqlite'
    with sqlite3.connect(db) as connection:
        connection.executescript('CREATE TABLE positions(ticker,nome,quantita,is_active);'
            'CREATE TABLE favorite_companies(ticker,name);')
        connection.execute("INSERT INTO favorite_companies VALUES('SYNTH.X','Synthetic')")
    before = db.read_bytes()
    status = {'status': 'error', 'last_completed_at': None, 'last_result': None,
              'error': 'synthetic market provider unavailable'}

    def auth(request: Request):
        if request.headers.get('X-BB-Token') != 'offline':
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(create_fundamentals_router(auth, db_provider=lambda: db,
        cache_dir=tmp_path / 'cache', roots=[], refresh_provider=lambda: dict(status)))
    with TestClient(app) as client:
        assert client.get('/fundamentals/research').status_code == 401
        for _ in range(2):
            result = client.get('/fundamentals/research', headers={'X-BB-Token': 'offline'})
            assert result.status_code == 200
            assert result.json()['market_refresh'] == status
            assert result.json()['items'][0]['consensus']['status'] == 'missing'
    assert db.read_bytes() == before
    assert not (tmp_path / 'cache').exists()


def test_ordinary_backend_populates_portfolio_and_favorite_from_market_without_a_run(tmp_path, monkeypatch):
    """The lifecycle, collector, persistence and GET form a real offline path."""
    from datetime import datetime, timezone
    from hashlib import sha256
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api, trade_idea_routes
    from bellomberg.api.fundamentals_routes import create_fundamentals_router
    from bellomberg.market_data.fund_market_refresh import refresh_followed_market_data
    module = worker_module()
    db, cache = tmp_path / 'fund.sqlite', tmp_path / 'consensus_cache'
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE positions(ticker,nome,quantita,is_active);'
            'CREATE TABLE favorite_companies(ticker,name);')
        conn.execute("INSERT INTO positions VALUES('SYNTH.X','Synthetic Company',3,1)")
        conn.execute("INSERT INTO favorite_companies VALUES('FUND.X','Synthetic ETF')")
    before = db.read_bytes()
    cache.mkdir()
    (cache / (sha256(b'SYNTH.X').hexdigest() + '.json')).write_text(json.dumps({
        'ticker': 'SYNTH.X', 'source': 'obsolete Excel model',
        'price_targets': {'mean': 9000}}), encoding='utf-8')
    now = datetime.now(timezone.utc)
    requested = []

    class Instrument:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def info(self):
            requested.append(('info', self.ticker))
            return {'symbol': self.ticker, 'currency': 'USD', 'regularMarketPrice': 40,
                'regularMarketTime': int(now.timestamp()), 'numberOfAnalystOpinions': 7,
                'quoteType': 'ETF' if self.ticker == 'FUND.X' else 'EQUITY'}

        def get_info(self):
            return self.info

        @property
        def analyst_price_targets(self):
            requested.append(('targets', self.ticker))
            return {} if self.ticker == 'FUND.X' else {'current': 40, 'mean': 50, 'median': 49, 'low': 42, 'high': 61}

    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=Instrument))
    monkeypatch.setattr(api, 'SQLITE_PATH', str(db))
    monkeypatch.setattr(api, 'DB_DIR', str(tmp_path))
    monkeypatch.setattr(trade_idea_routes, 'recover_orphan_runs', lambda _: {'status': 'ready'})
    finished = Event()
    outcomes = []

    def acquire(*args, stop_event, **kwargs):
        try:
            outcomes.append(refresh_followed_market_data(*args, **kwargs, now=now, stop_event=stop_event))
            stop_event.set()
            return outcomes[-1]
        finally:
            finished.set()

    monkeypatch.setattr(module, 'refresh_followed_market_data', acquire)
    app = FastAPI(lifespan=api.lifespan)
    app.include_router(create_fundamentals_router(lambda: 'offline', db_provider=lambda: db,
        cache_dir=cache, roots=[], refresh_provider=lambda: app.state.fund_market_worker.status()))
    with TestClient(app) as client:
        assert finished.wait(3)
        assert outcomes, app.state.fund_market_worker.status()
        result = client.get('/fundamentals/research').json()
        rows = {row['ticker']: row for row in result['items']}
        assert rows['SYNTH.X']['consensus']['mean'] == 50
        assert rows['SYNTH.X']['comparison']['upside_pct'] == 25
        assert rows['FUND.X']['quote']['value'] == 40
        assert rows['FUND.X']['consensus']['mean'] is None
        assert rows['FUND.X']['comparison']['upside_pct'] is None
        assert all(row['analysis']['status'] == 'missing' for row in rows.values())
        provider_calls = list(requested)
        assert {ticker for kind, ticker in provider_calls if kind == 'info'} == {'SYNTH.X', 'FUND.X'}
        for _ in range(2):
            assert client.get('/fundamentals/research/SYNTH.X').json()['consensus']['mean'] == 50
        assert requested == provider_calls
    assert db.read_bytes() == before
    assert not list(tmp_path.rglob('*.xlsx'))
