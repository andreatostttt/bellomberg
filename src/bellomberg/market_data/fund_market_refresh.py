"""Bounded market-only acquisition for Fund; book access is strictly read-only."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from threading import Event

from .consensus_estimates import (
    _identity, _stamp, consensus_observation, provider_symbol, persist_market_observation,
    quote_observation, read_market_observation, refresh_receipt)

QUOTE_REFRESH_SECONDS = 15 * 60
CONSENSUS_REFRESH_SECONDS = 24 * 60 * 60
TICKER_PAUSE_SECONDS = 1.0
PROVIDER_BACKOFF_SECONDS = 15 * 60


def _ticker_factory(ticker):
    import yfinance as yf
    return yf.Ticker(ticker)


def _universe(db_path):
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ValueError('Existing portfolio database unavailable')
    tickers, notices = set(), []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, query in (
            ('positions', 'SELECT ticker FROM positions WHERE is_active=1 AND quantita<>0'),
            ('favorite_companies', 'SELECT ticker FROM favorite_companies'),
        ):
            if table not in tables:
                notices.append(table + '_unavailable')
                continue
            tickers.update(row[0] for row in conn.execute(query))
        if not {'positions', 'favorite_companies'}.intersection(tables):
            raise ValueError('Portfolio and favorites tables unavailable')
        if 'trade_idea_runs' in tables:
            for ticker, request_json in conn.execute('SELECT ticker,request_json FROM trade_idea_runs'):
                try:
                    request = json.loads(request_json)
                    if isinstance(request, dict) and request.get('analysis_mode') == 'fundamentals_research_v1':
                        tickers.add(ticker)
                except (ValueError, TypeError):
                    notices.append('research_run_request_unreadable:' + str(ticker))
        if 'weekly_runs' in tables:
            from bellomberg.core.research_analysis import research_digest, RESEARCH_ANALYSIS_MODE, _thesis_identity
            for context_json, snapshot_json in conn.execute('SELECT context_json,snapshot_json FROM weekly_runs'):
                try:
                    context, snapshot = json.loads(context_json), json.loads(snapshot_json)
                    if (context.get('contract') or {}).get('analysis_mode') != RESEARCH_ANALYSIS_MODE:
                        continue
                    payload = snapshot['payload']
                    seal = payload['data']['_research_thesis']
                    dossiers, reports = seal['dossiers'], seal['reports']
                    if (snapshot['sha256'] != research_digest(payload)
                            or seal.get('analysis_mode') != RESEARCH_ANALYSIS_MODE or seal.get('version') != 1
                            or seal['dossier_sha256'] != research_digest({'dossiers': dossiers, 'tool_receipts': seal['tool_receipts']})
                            or seal['thesis_sha256'] != research_digest(_thesis_identity(seal))):
                        raise ValueError('Research seal integrity differs')
                    for ticker, dossier in dossiers.items():
                        if not isinstance(dossier, dict) or dossier.get('ticker') != ticker:
                            raise ValueError('Weekly dossier identity differs')
                        tickers.add(ticker)
                except (ValueError, TypeError, KeyError, AttributeError):
                    notices.append('weekly_research_scope_unavailable')
    valid = []
    for ticker in tickers:
        if isinstance(ticker, str) and re.fullmatch(r'[A-Z0-9^][A-Z0-9.^=_\-]{0,31}', ticker):
            valid.append(ticker)
        else:
            notices.append('invalid_exact_ticker:' + str(ticker))
    return sorted(valid), notices


def _due(saved, component, now, cadence):
    metadata = saved.get(component + '_refresh') or {}
    retry = _stamp(metadata.get('retry_after'))
    if retry is not None and retry > now:
        return False
    if metadata.get('status') == 'failed':
        return True
    acquired = _stamp((saved.get('quote') or {}).get('acquired_at') if component == 'quote' else saved.get('acquired_at'))
    return acquired is None or acquired > now or (now - acquired).total_seconds() >= cadence


def _rate_limited(exc):
    return (getattr(getattr(exc, 'response', None), 'status_code', None) == 429
        or 'ratelimit' in type(exc).__name__.lower()
        or re.search(r'\b429\b|too many requests|rate.?limit', str(exc), re.I) is not None)


def _provider_backoff(cache_dir, *, update=None):
    path = Path(cache_dir) / '_provider_backoff.json'
    if update is None:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('contract') != 'fund-market-provider-backoff/1' or value.get('source') != 'yfinance':
            raise ValueError('Provider cooldown cache contract differs')
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            json.dump(update, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return update


def refresh_followed_market_data(db_path, *, cache_dir, now=None, stop_event=None):
    """Fetch lightweight quotes every 15min and targets daily, with per-part backoff.

    This function never constructs MemoryDB, imports agents, calls AI or updates
    holdings. Provider errors keep dated last-success data and an explicit error.
    The caller owns the timer; GET/readers never invoke this function.
    """
    explicit_clock = now is not None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Refresh clock requires an explicit timezone')
    now = now.astimezone(timezone.utc)
    stop = stop_event or Event()
    counts = {'processed': 0, 'quotes_updated': 0, 'consensus_updated': 0,
              'skipped': 0, 'failures': 0, 'not_attempted': 0}
    result = {'contract': 'fund-market-refresh/1', 'status': 'unavailable', 'started_at': now.isoformat(),
        'universe_count': None, 'counts': counts, 'errors': [], 'notices': []}
    try:
        tickers, notices = _universe(db_path)
    except (OSError, sqlite3.Error, ValueError) as exc:
        result['errors'].append({'stage': 'universe', 'error': type(exc).__name__ + ': ' + str(exc)[:200]})
        return result
    result.update(universe_count=len(tickers), notices=notices, status='completed')
    try:
        backoff = _provider_backoff(cache_dir)
        retry = _stamp(backoff.get('retry_after'))
        if retry is not None and retry > now:
            result.update(status='backoff', provider_backoff_until=backoff['retry_after'], retry_after=backoff['retry_after'])
            result['errors'].append({'stage': 'provider', 'error': backoff.get('error')})
            counts['not_attempted'] = len(tickers)
            return result
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result['notices'].append('provider_backoff_unreadable:' + type(exc).__name__)
    for index, ticker in enumerate(tickers):
        if stop.is_set():
            result['status'] = 'stopped'
            break
        saved = read_market_observation(ticker, cache_dir=cache_dir)
        if saved.get('_cache_warning'):
            result['notices'].append(ticker + ': ' + saved['_cache_warning'])
        quote_due = _due(saved, 'quote', now, QUOTE_REFRESH_SECONDS)
        consensus_due = _due(saved, 'consensus', now, CONSENSUS_REFRESH_SECONDS)
        if not quote_due and not consensus_due:
            counts['skipped'] += 1
            continue
        counts['processed'] += 1
        quote, consensus, quote_error, consensus_error = None, None, None, None
        rate_limit_error = None
        acquired_at = now if explicit_clock else datetime.now(timezone.utc)
        try:
            symbol = provider_symbol(ticker)
            provider = _ticker_factory(symbol)
            info = provider.info
            _identity(ticker, info, symbol)
        except Exception as exc:
            message = type(exc).__name__ + ': ' + str(exc)[:200]
            if _rate_limited(exc):
                rate_limit_error = message
            if quote_due:
                quote_error = message
            if consensus_due:
                consensus_error = message
        else:
            acquired_at = now if explicit_clock else datetime.now(timezone.utc)
            if quote_due:
                try:
                    quote = quote_observation(ticker, info, acquired_at, symbol)
                except (ValueError, TypeError, OSError, OverflowError) as exc:
                    quote_error = type(exc).__name__ + ': ' + str(exc)[:200]
            if consensus_due:
                try:
                    targets = ({} if info.get('quoteType') in ('ETF', 'MUTUALFUND', 'INDEX', 'CURRENCY', 'CRYPTOCURRENCY', 'FUTURE')
                               else provider.analyst_price_targets or {})
                    acquired_at = now if explicit_clock else datetime.now(timezone.utc)
                    consensus = consensus_observation(ticker, info, targets, acquired_at, symbol)
                except Exception as exc:
                    consensus_error = type(exc).__name__ + ': ' + str(exc)[:200]
                    if _rate_limited(exc):
                        rate_limit_error = consensus_error
        attempt_at = now if explicit_clock else datetime.now(timezone.utc)
        quote_receipt = refresh_receipt(saved.get('quote_refresh'), attempt_at, quote_error) if quote_due else None
        consensus_receipt = refresh_receipt(saved.get('consensus_refresh'), attempt_at, consensus_error) if consensus_due else None
        try:
            published = persist_market_observation(ticker, cache_dir=cache_dir, quote=quote, consensus=consensus,
                quote_refresh=quote_receipt, consensus_refresh=consensus_receipt)
            if quote is not None and published.get('quote') == quote:
                counts['quotes_updated'] += 1
            if consensus is not None and published.get('acquired_at') == consensus['acquired_at']:
                counts['consensus_updated'] += 1
            for component, due in (('quote', quote_due), ('consensus', consensus_due)):
                receipt = published.get(component + '_refresh') or {}
                if due and receipt.get('status') == 'failed':
                    result['errors'].append({'ticker': ticker, 'stage': component, 'error': receipt.get('error')})
        except (OSError, TypeError, ValueError) as exc:
            result['errors'].append({'ticker': ticker, 'stage': 'cache', 'error': type(exc).__name__ + ': ' + str(exc)[:200]})
        if any(row.get('ticker') == ticker for row in result['errors']):
            counts['failures'] += 1
        if rate_limit_error:
            retry_after = (attempt_at + timedelta(seconds=PROVIDER_BACKOFF_SECONDS)).isoformat()
            result.update(provider_backoff_until=retry_after, retry_after=retry_after)
            try:
                _provider_backoff(cache_dir, update={'contract': 'fund-market-provider-backoff/1',
                    'source': 'yfinance', 'last_attempt_at': attempt_at.isoformat(),
                    'retry_after': retry_after, 'error': rate_limit_error})
            except (OSError, ValueError, TypeError) as exc:
                result['errors'].append({'stage': 'provider_backoff_cache', 'error': type(exc).__name__ + ': ' + str(exc)[:200]})
            break
        if index < len(tickers) - 1 and stop.wait(TICKER_PAUSE_SECONDS):
            result['status'] = 'stopped'
            break
    # Notices (e.g. an unsealed research run, a skipped malformed symbol) stay visible in
    # the result but are not acquisition failures: only errors make the batch partial.
    if result['status'] != 'stopped' and result['errors']:
        result['status'] = 'partial'
    counts['not_attempted'] = len(tickers) - counts['processed'] - counts['skipped']
    return result
