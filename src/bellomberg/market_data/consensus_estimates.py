"""
consensus_estimates.py — CONSENSUS DEGLI ANALISTI (Fase 5, 15/07, richiesta PM).

Attese di mercato per un ticker via yfinance (fonte dichiarata, gratis):
  - stime EPS e RICAVI (trimestre corrente/prossimo, anno corrente/prossimo)
    con avg/low/high, numero analisti e growth implicito
  - REVISIONI delle stime EPS (oggi vs 7/30/60/90 giorni fa): il momentum
    delle stime, spesso piu' informativo del livello
  - target price (mean/median/high/low) + upside implicito vs prezzo corrente
  - mix raccomandazioni (strongBuy..strongSell) e trend vs 3 mesi fa

Regola PM "no fallback silenziosi": ogni blocco mancante e' dichiarato
("n.d. (motivo)"), mai omesso in silenzio. Cache in-memory 1h per ticker
(le stime non si muovono intraday).
"""
import time
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from numbers import Real
from pathlib import Path
from threading import RLock

_CACHE = {}   # {ticker: (ts, payload)}
_TTL_S = 3600
OBSERVATION_KIND = 'market_consensus'
OBSERVATION_CONTRACT = 'market-consensus/1'
_WRITE_LOCK = RLock()
_DETAIL_FIELDS = frozenset({'eps_estimates', 'revenue_estimates', 'eps_revisions',
    'eps_revisions_note', 'recommendations', 'details_acquired_at', 'details_identity_symbol', 'details_source'})


def _stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _finite(value, *, positive=False):
    if isinstance(value, Real) and not isinstance(value, bool):
        try:
            number = float(value)
            if math.isfinite(number) and (not positive or number > 0):
                return number
        except (ValueError, OverflowError):
            pass
    return None


def _cache_path(ticker, cache_dir):
    return Path(cache_dir) / (sha256(ticker.encode('utf-8')).hexdigest() + '.json')


def _empty_observation(ticker):
    return {'observation_kind': OBSERVATION_KIND, 'observation_contract': OBSERVATION_CONTRACT,
        'ticker': ticker, 'identity_symbol': None, 'currency': None, 'currency_source': None,
        'source': None, 'acquired_at': None, 'data_as_of': None, 'price_targets': {},
        'quote': None, 'consensus_status': 'unavailable', 'consensus_reason': 'Consensus not acquired'}


def read_market_observation(ticker, *, cache_dir):
    path = _cache_path(ticker, cache_dir)
    if not path.is_file():
        return _empty_observation(ticker)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('ticker') != ticker
                or value.get('observation_kind') != OBSERVATION_KIND
                or value.get('observation_contract') != OBSERVATION_CONTRACT):
            raise ValueError('unattested_or_wrong_identity_cache_ignored')
        return value
    except (OSError, ValueError) as exc:
        return {**_empty_observation(ticker), '_cache_warning': type(exc).__name__ + ': ' + str(exc)[:160]}


@contextmanager
def _observation_lock(ticker, folder):
    """Serialize component merge across both backend threads and AI subprocesses."""
    with _WRITE_LOCK:
        locks = Path(folder) / '.locks'
        locks.mkdir(parents=True, exist_ok=True)
        with open(locks / (sha256(ticker.encode()).hexdigest() + '.lock'), 'a+b') as stream:
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Market observation cache lock unavailable')
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def refresh_receipt(previous, now, error=None):
    previous = previous or {}
    failures = int(previous.get('consecutive_failures') or 0) + 1 if error else 0
    return {'status': 'failed' if error else 'success', 'last_attempt_at': now.isoformat(),
        'last_success_at': previous.get('last_success_at') if error else now.isoformat(),
        'retry_after': (now + timedelta(seconds=min(21600, 300 * 2 ** min(failures - 1, 7)))).isoformat() if error else None,
        'error': error, 'consecutive_failures': failures}


def provider_symbol(ticker):
    """Yahoo symbol for a book ticker: same aliases as the price updater, never guessed."""
    from bellomberg.cli.price_updater import data_ticker
    return data_ticker(ticker)


def _identity(ticker, info, provider=None):
    if not isinstance(info, dict) or info.get('symbol') != (provider or ticker):
        raise ValueError('Exact provider identity missing or mismatched')
    if not isinstance(info.get('currency'), str) or not info['currency'].strip():
        raise ValueError('Provider currency unavailable')


def quote_observation(ticker, info, now, provider=None):
    _identity(ticker, info, provider)
    value = _finite(info.get('regularMarketPrice'), positive=True)
    timestamp = _finite(info.get('regularMarketTime'))
    if value is None or timestamp is None:
        raise ValueError('Observed quote price or provider timestamp unavailable')
    observed = datetime.fromtimestamp(timestamp, timezone.utc)
    if observed > now:
        raise ValueError('Provider quote timestamp is in the future')
    return {'value': value, 'currency': info['currency'], 'identity_symbol': ticker,
        'provider_symbol': provider or ticker, 'observed_at': observed.isoformat(), 'acquired_at': now.isoformat(),
        'source': 'yfinance info.regularMarketPrice', 'exchange': info.get('exchange')}


def consensus_observation(ticker, info, targets, now, provider=None):
    _identity(ticker, info, provider)
    if not isinstance(targets, dict):
        raise ValueError('Provider targets response is not an object')
    not_applicable = info.get('quoteType') in ('ETF', 'MUTUALFUND', 'INDEX', 'CURRENCY', 'CRYPTOCURRENCY', 'FUTURE')
    target = {key: _finite(targets.get(key), positive=True) if not not_applicable else None
              for key in ('mean', 'median', 'high', 'low')}
    current = _finite(targets.get('current'), positive=True) if not not_applicable else None
    count = _finite(info.get('numberOfAnalystOpinions'), positive=True)
    target.update(current_price=current,
        number_of_analysts=int(count) if count is not None and count.is_integer() and not not_applicable else None,
        number_of_analysts_source='yfinance info.numberOfAnalystOpinions',
        implied_upside_pct=round((target['mean'] / current - 1) * 100, 1) if target['mean'] and current else None)
    status = 'not_applicable' if not_applicable else 'unavailable' if target['mean'] is None else 'partial'
    reason = ('Analyst company price targets do not apply to provider quoteType ' + info['quoteType']
        if not_applicable else 'Analyst target coverage unavailable' if target['mean'] is None
        else 'Provider does not supply a consensus observation date; missing fields remain null')
    return {'identity_symbol': ticker, 'provider_symbol': provider or ticker,
        'currency': info['currency'], 'currency_source': 'yfinance info.currency',
        'source': 'yfinance (consensus Yahoo Finance)', 'acquired_at': now.isoformat(), 'data_as_of': None,
        'price_targets': target, 'consensus_status': status, 'consensus_reason': reason}


def persist_market_observation(ticker, *, cache_dir, consensus=None, quote=None,
                               consensus_refresh=None, quote_refresh=None):
    """Merge only observed components; older writers cannot replace newer data."""
    temporary = None
    with _observation_lock(ticker, cache_dir):
        payload = read_market_observation(ticker, cache_dir=cache_dir)
        payload.pop('_cache_warning', None)
        old_acquired = _stamp(payload.get('acquired_at'))
        incoming_acquired = _stamp((consensus or {}).get('acquired_at'))
        if consensus is not None and incoming_acquired is not None and (old_acquired is None or incoming_acquired >= old_acquired):
            if consensus.get('identity_symbol') != ticker or not consensus.get('currency'):
                raise ValueError('Consensus observation identity or currency unavailable')
            payload.update(deepcopy({key: value for key, value in consensus.items() if key not in
                ('ticker', 'observation_kind', 'observation_contract', 'quote', 'quote_refresh', 'consensus_refresh', '_cache_warning')
                and key not in _DETAIL_FIELDS}))
        # A slow complete tool call may finish after a newer targets-only fetch.
        # Its estimates/revisions keep their own acquisition and source identity.
        incoming_details = _stamp((consensus or {}).get('details_acquired_at'))
        old_details = _stamp(payload.get('details_acquired_at'))
        if incoming_details is not None and (old_details is None or incoming_details >= old_details):
            if consensus.get('details_identity_symbol', consensus.get('identity_symbol')) != ticker:
                raise ValueError('Detailed analyst observations have no confirmed identity')
            payload.update(deepcopy({key: value for key, value in consensus.items() if key in _DETAIL_FIELDS}))
        if quote is not None:
            old_quote = payload.get('quote') or {}
            incoming_time, old_time = _stamp(quote.get('observed_at')), _stamp(old_quote.get('observed_at'))
            incoming_stamp, old_stamp = _stamp(quote.get('acquired_at')), _stamp(old_quote.get('acquired_at'))
            if (quote.get('identity_symbol') != ticker or not quote.get('currency')
                    or _finite(quote.get('value'), positive=True) is None or incoming_time is None
                    or incoming_stamp is None or incoming_time > incoming_stamp):
                raise ValueError('Quote observation identity, currency, price or timestamps unavailable')
            if ((old_time is None or incoming_time >= old_time)
                    and (old_stamp is None or incoming_stamp >= old_stamp)):
                payload['quote'] = deepcopy(quote)
            elif quote_refresh and old_time and incoming_time < old_time:
                quote_refresh = refresh_receipt(payload.get('quote_refresh'),
                    _stamp(quote_refresh['last_attempt_at']), 'Provider quote observation regressed; last valid quote retained')
        for key, update in (('consensus_refresh', consensus_refresh), ('quote_refresh', quote_refresh)):
            if update is not None:
                old_attempt = _stamp((payload.get(key) or {}).get('last_attempt_at'))
                incoming_attempt = _stamp(update.get('last_attempt_at'))
                if incoming_attempt is not None and (old_attempt is None or incoming_attempt >= old_attempt):
                    merged = deepcopy(update)
                    old_success = _stamp((payload.get(key) or {}).get('last_success_at'))
                    incoming_success = _stamp(merged.get('last_success_at'))
                    if old_success is not None and (incoming_success is None or old_success > incoming_success):
                        merged['last_success_at'] = (payload.get(key) or {})['last_success_at']
                    payload[key] = merged
        try:
            contents = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache_dir,
                                              suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, _cache_path(ticker, cache_dir))
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return payload


def _save_observation(payload):
    """Persist only the acquisition already requested by an ordinary tool call.

    Fund reads this cache; its GET never invokes this function or yfinance.
    A failed write is disclosed to the caller and cannot masquerade as a refresh.
    """
    from bellomberg.core.paths import DATA_DIR
    folder = DATA_DIR / "consensus_cache"
    try:
        old = read_market_observation(payload['ticker'], cache_dir=folder)
        now = datetime.fromtimestamp(time.time(), timezone.utc)
        consensus_ok = payload.get('acquired_at') is not None
        return persist_market_observation(payload['ticker'], cache_dir=folder, consensus=payload,
            quote=payload.get('quote'),
            consensus_refresh=refresh_receipt(old.get('consensus_refresh'), now,
                None if consensus_ok else payload.get('consensus_reason', 'Consensus acquisition failed')),
            quote_refresh=refresh_receipt(old.get('quote_refresh'), now,
                None if payload.get('quote') else payload.get('identity_warning', 'Quote acquisition failed')))
    except (OSError, TypeError, ValueError) as exc:
        payload["cache_warning"] = "consensus snapshot not saved: " + type(exc).__name__
        return payload


def _df_rows(df, cols):
    """DataFrame yfinance -> lista di dict {period, <cols>} (period e' l'indice)."""
    out = []
    for period, row in df.iterrows():
        d = {"period": str(period)}
        for c in cols:
            v = row.get(c)
            try:
                number = None if v is None else _finite(float(v))
                d[c] = None if number is None else round(number, 4)
            except Exception:
                d[c] = v
        out.append(d)
    return out


def get_consensus(ticker: str) -> dict:
    """Consensus completo per un ticker. Non solleva: errori dichiarati nel payload."""
    tkr = (ticker or "").upper().strip()
    if not tkr:
        return {"error": "ticker mancante"}
    hit = _CACHE.get(tkr)
    if (hit and time.time() - hit[0] < _TTL_S
            and hit[1].get('observation_contract') == OBSERVATION_CONTRACT
            and hit[1].get('observation_kind') == OBSERVATION_KIND):
        return hit[1]

    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance non disponibile"}
    out = _empty_observation(tkr)
    try:
        provider = provider_symbol(tkr)
    except Exception as e:
        out["error"] = f"alias yfinance non risolvibile ({type(e).__name__}: {str(e)[:80]})"
        return out
    tk = yf.Ticker(provider)
    # Currency and exact provider identity are observations, never guessed from
    # an exchange, the requested suffix or the portfolio's accounting currency.
    info = {}
    try:
        info = tk.info or {}
        _identity(tkr, info, provider)
        out['details_identity_symbol'] = tkr
        out['details_source'] = 'yfinance analyst estimates/revisions/recommendations'
        out['quote'] = quote_observation(tkr, info, datetime.fromtimestamp(time.time(), timezone.utc), provider)
    except Exception as e:
        out["identity_warning"] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    # --- target price + upside implicito ---
    try:
        _identity(tkr, info, provider)
        pt = {} if info.get('quoteType') in ('ETF', 'MUTUALFUND', 'INDEX', 'CURRENCY', 'CRYPTOCURRENCY', 'FUTURE') else tk.analyst_price_targets or {}
        out.update(consensus_observation(tkr, info, pt, datetime.fromtimestamp(time.time(), timezone.utc), provider))
    except Exception as e:
        out['consensus_reason'] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    # --- stime EPS e ricavi ---
    for attr, key, cols in (
            ("earnings_estimate", "eps_estimates",
             ["avg", "low", "high", "yearAgoEps", "numberOfAnalysts", "growth"]),
            ("revenue_estimate", "revenue_estimates",
             ["avg", "low", "high", "yearAgoRevenue", "numberOfAnalysts", "growth"])):
        try:
            df = getattr(tk, attr)
            if df is None or df.empty:
                out[key] = "n.d. (Yahoo non copre le stime per questo ticker)"
            else:
                out[key] = _df_rows(df, cols)
        except Exception as e:
            out[key] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    # --- revisioni EPS: momentum delle stime (current vs 30/90 giorni fa) ---
    try:
        df = tk.eps_trend
        if df is None or df.empty:
            out["eps_revisions"] = "n.d. (eps_trend vuoto)"
        else:
            revs = []
            for period, row in df.iterrows():
                cur = _finite(row.get("current"))
                d = {"period": str(period), "current": cur}
                for horizon in ("30daysAgo", "90daysAgo"):
                    prev = _finite(row.get(horizon))
                    d["chg_vs_" + horizon.replace("daysAgo", "d") + "_pct"] = (
                        round((cur / prev - 1) * 100, 2) if cur and prev else None)
                revs.append(d)
            out["eps_revisions"] = revs
            out["eps_revisions_note"] = ("revisioni POSITIVE = analisti che alzano le stime "
                                         "(momentum favorevole); negative = tagli in corso")
    except Exception as e:
        out["eps_revisions"] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    # --- raccomandazioni: mix corrente + trend vs 3 mesi fa ---
    try:
        df = tk.recommendations_summary
        if df is None or df.empty:
            out["recommendations"] = "n.d. (nessuna copertura)"
        else:
            rows = _df_rows(df, ["strongBuy", "buy", "hold", "sell", "strongSell"])
            # yfinance etichetta i period come '0m'/'-3m' oppure '0'/'3' a seconda
            # della versione: accetta entrambi
            now = next((r for r in rows if r["period"] in ("0m", "0")), rows[0])
            m3 = next((r for r in rows if r["period"] in ("-3m", "3")), None)
            out["recommendations"] = {"current_month": now, "three_months_ago": m3}
    except Exception as e:
        out["recommendations"] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    acquired = time.time()
    out['details_acquired_at'] = datetime.fromtimestamp(acquired, timezone.utc).isoformat()
    out = _save_observation(out)
    _CACHE[tkr] = (acquired, out)
    return out


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(get_consensus(sys.argv[1] if len(sys.argv) > 1 else "BSX"),
                     indent=2, ensure_ascii=False, default=str))
