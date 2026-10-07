"""Diagnostica passiva delle fonti: nessun provider, configurazione o dato vivo."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json
import math
import re
from types import SimpleNamespace

POLICY = 'weekly-source-health/1'
KEY = '_source_health'
STAGE = 'source_health_v1'
STATUSES = frozenset(('NOT_CONFIGURED', 'NOT_SUPPORTED', 'UNVERIFIED', 'OK', 'EMPTY',
    'PARTIAL', 'STALE', 'UNAUTHORIZED', 'RATE_LIMITED', 'ERROR', 'CHECK_UNAVAILABLE'))
DESKS = frozenset(('macro', 'quant', 'fundamentals', 'options', 'crypto', 'eventdesk', 'news', 'politics'))
REGISTRY = {
    'macro_dashboard': ('macro_sources', 'dashboard'), 'get_macro_dashboard': ('macro_sources', 'dashboard'),
    'get_macro_indicator': ('macro_sources', 'indicator'),
    'get_options_data': ('options_sources', 'summary'),
    'get_options_chain_polygon': ('polygon', 'snapshot'),
    'get_option_expirations': ('polygon', 'contracts'),
    'get_polymarket_events': ('polymarket', 'search'), 'polymarket': ('polymarket', 'search'),
    'news_feed': ('local_cache', 'news_feed'),
}
EXPECTED = (('polygon', 'contracts'), ('polygon', 'snapshot'), ('ibkr', 'options'),
            ('gnews', 'news'), ('finnhub', 'company_news'))
CAUSES = frozenset(('NOT_OBSERVED', 'UNMAPPED_SCHEMA', 'EXPLICIT_MISSING_CONFIG', 'HTTP_AUTH',
    'HTTP_RATE_LIMIT', 'TOOL_ERROR', 'CHILD_ERROR', 'VALUE_UNAVAILABLE', 'MISSING_SERIES',
    'COVERAGE_PARTIAL', 'COVERAGE_UNVERIFIED', 'CACHE_ONLY', 'CACHE_READ_UNVERIFIED',
    'FETCH_WARNINGS', 'EXPLICIT_STALE', 'NOT_SUPPORTED', 'NORMALIZATION_FAILED',
    'COMPARISON_UNAVAILABLE', 'QUALITY_UNAVAILABLE'))
COVERAGE_FIELDS = ('requests', 'pages_received', 'rows_observed', 'rows_returned', 'rows_used',
    'rows_rejected_expiry', 'expirations_observed', 'expirations_returned',
    'observed', 'usable', 'missing', 'comparison_unavailable', 'stale', 'quality_unavailable')


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def enabled(board):
    store = getattr(board, 'weekly_store', None)
    if store is None:
        return False
    contract = store.context.get('contract', {})
    if 'source_health_policy' not in contract:
        return False
    policy = contract['source_health_policy']
    if policy != POLICY:
        from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
        raise WeeklyRunBlocked('Policy source health non compatibile')
    if store.get('memo_validated') is not None:
        return False
    return True


def _failure(error):
    text = error if isinstance(error, str) else ''
    # Solo prove esplicite note: una chiave presente NON prova autorizzazione.
    if text in ('POLYGON_API_KEY mancante', 'FRED_API_KEY non configurata nel .env',
                'QUIVER_API_KEY non configurata', 'missing FINNHUB_API_KEY in .env'):
        return 'NOT_CONFIGURED', 'EXPLICIT_MISSING_CONFIG'
    if re.match(r'^(?:HTTP )?(401|403)(?:\b|$)', text):
        return 'UNAUTHORIZED', 'HTTP_AUTH'
    if re.match(r'^(?:HTTP )?429(?:\b|$)', text):
        return 'RATE_LIMITED', 'HTTP_RATE_LIMIT'
    return 'ERROR', 'TOOL_ERROR'


def _date_text(value):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10 and date.fromisoformat(value).isoformat() == value:
            return value
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def normalize_health(tool, result):
    """Registro chiuso: nonempty/error-free non significa fonte sana."""
    provider, endpoint = REGISTRY.get(tool, ('unknown', 'unmapped'))
    out = {'provider': provider, 'endpoint': endpoint, 'status': 'UNVERIFIED',
           'observed_at': None, 'as_of': None, 'coverage': {}, 'cause_codes': ['UNMAPPED_SCHEMA']}
    if tool == 'news_feed':
        out.update(coverage={'observed': len(result)} if isinstance(result, list) else {},
                   cause_codes=['CACHE_ONLY' if isinstance(result, list) and result else 'CACHE_READ_UNVERIFIED'])
        return out
    if not isinstance(result, dict):
        return out
    out['observed_at'] = _date_text(result.get('_timestamp'))  # acquisizione, mai freschezza
    if tool == 'get_options_data' and (result.get('_source') == 'polygon options summary' or
                                      str(result.get('data_source', '')).startswith('polygon_options_starter')):
        out.update(provider='polygon', endpoint='snapshot')
    if result.get('status') in ('non_coperto', 'not_supported') or result.get('stato') == 'non_coperto':
        out.update(status='NOT_SUPPORTED', cause_codes=['NOT_SUPPORTED'])
        return out
    coverage = result.get('coverage')
    if out['provider'] == 'polygon' and isinstance(coverage, dict):
        out['coverage'] = {k: coverage[k] for k in COVERAGE_FIELDS
                           if type(coverage.get(k)) is int and coverage[k] >= 0}
        expiry = result.get('expiry_coverage')
        expiry = expiry if isinstance(expiry, dict) else {}
        partial = (result.get('partial') is True or coverage.get('status') == 'PARTIAL'
                   or expiry.get('status') in ('PARTIAL', 'UNAVAILABLE', 'ERROR')
                   or any(c.get('errors') or c.get('issues') for c in (coverage, expiry)))
        if partial:
            causes = {'COVERAGE_PARTIAL'}
            for c in (coverage, expiry):
                errors = c.get('errors')
                if isinstance(errors, list):
                    causes.update(_failure(error)[1] for error in errors)
            if result.get('error') not in (None, 'no data'):
                causes.add(_failure(result['error'])[1])
            out.update(status='PARTIAL', cause_codes=sorted(causes))
            return out
        pages = coverage.get('pages_received')
        seen = coverage.get('rows_observed', coverage.get('expirations_observed'))
        if (type(pages) is int and pages > 0 and type(seen) is int and seen == 0
                and coverage.get('status') in ('COMPLETE', 'UNAVAILABLE')
                and result.get('error') in (None, 'no data')):
            out.update(status='EMPTY', coverage=dict(out['coverage'], observed=0), cause_codes=[])
            return out
    if result.get('error'):
        out['status'], cause = _failure(result['error'])
        out['cause_codes'] = [cause]
        return out
    if endpoint == 'dashboard' and isinstance(result.get('indicators'), dict):
        rows = list(result['indicators'].values())
        usable = sum(isinstance(r, dict) and not r.get('error') and
                     isinstance(r.get('value'), (int, float)) and not isinstance(r.get('value'), bool)
                     and math.isfinite(r['value']) for r in rows)
        missing = len(result.get('indicators_not_available') or {})
        failures = [_failure(r['error']) for r in rows if isinstance(r, dict) and r.get('error')]
        errors = len(failures)
        comparisons = sum(isinstance(r, dict) and isinstance(r.get('comparison'), dict)
            and r['comparison'].get('status') in ('unavailable', 'ambiguous') for r in rows)
        stale = sum(isinstance(r, dict) and isinstance(r.get('quality'), dict)
            and r['quality'].get('status') == 'STALE' for r in rows)
        quality_gaps = sum(isinstance(r, dict) and isinstance(r.get('quality'), dict)
            and (r['quality'].get('latest_status') in ('unavailable', 'ambiguous')
                 or r['quality'].get('status') in ('PARTIAL', 'ERROR', 'UNAVAILABLE')) for r in rows)
        out['coverage'] = {'observed': len(rows), 'usable': usable, 'missing': missing,
                          'comparison_unavailable': comparisons, 'stale': stale, 'quality_unavailable': quality_gaps}
        if usable:
            if stale == len(rows) and not (missing or comparisons or quality_gaps or errors):
                out['status'] = 'STALE'
            else:
                out['status'] = 'PARTIAL' if usable < len(rows) or missing or comparisons or stale or quality_gaps else 'OK' 
        elif errors and errors == len(rows) and len({f[0] for f in failures}) == 1:
            out['status'] = failures[0][0]
        else:
            out['status'] = 'ERROR' if errors else 'UNVERIFIED'
        out['cause_codes'] = ((['CHILD_ERROR'] if errors else []) +
                              (['VALUE_UNAVAILABLE'] if usable < len(rows) else []) +
                              (['MISSING_SERIES'] if missing else []) +
                              (['COMPARISON_UNAVAILABLE'] if comparisons else []) +
                              (['EXPLICIT_STALE'] if stale else []) +
                              (['QUALITY_UNAVAILABLE'] if quality_gaps else []) + sorted({f[1] for f in failures}))
        if not rows:
            out['cause_codes'] = ['UNMAPPED_SCHEMA']
        return out
    coverage = result.get('coverage')
    if out['provider'] == 'polygon' and isinstance(coverage, dict):
        status = coverage.get('status')
        if (status == 'COMPLETE' and type(coverage.get('pages_received')) is int
              and coverage['pages_received'] > 0 and any(type(coverage.get(k)) is int and coverage[k] > 0
                  for k in ('rows_observed', 'expirations_observed'))):
            out.update(status='OK', cause_codes=[])
        else:
            out['cause_codes'] = ['COVERAGE_UNVERIFIED']
        return out
    if out['provider'] == 'polymarket' and isinstance(result.get('results'), list):
        rows = result['results']
        out['coverage'] = {'observed': len(rows)}
        if result.get('fetch_warnings'):
            out.update(status='PARTIAL' if rows else 'ERROR', cause_codes=['FETCH_WARNINGS'])
        else:
            # I vecchi payload non attestano endpoint/cap/troncamento: non inventare OK.
            out['cause_codes'] = ['COVERAGE_UNVERIFIED']
        return out
    if endpoint == 'indicator' and result.get('quality', {}).get('status') == 'STALE':
        out.update(status='STALE', cause_codes=['EXPLICIT_STALE'], as_of=_date_text(result.get('latest_date')))
    return out


def capture_health(board, key, tool, result, *, desk=None, round_n=None, phase='tool',
                   output_json=None, input_values=None):
    """Cattura passiva; il callback gia esistente persiste ledger e tool insieme."""
    if not enabled(board):
        return None
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    event_id = _digest({'key': key, 'desk': desk, 'round': round_n, 'phase': phase})
    serialization_failed = False
    try:
        output_json = output_json if isinstance(output_json, str) else json.dumps(result, default=str, ensure_ascii=False)
        evidence = {'result_sha256': sha256(output_json.encode('utf-8')).hexdigest(),
                    'input_sha256': _digest(input_values), 'scope': 'exact_request'}
    except (TypeError, ValueError, OverflowError):
        serialization_failed = True
        evidence = {'result_sha256': None, 'input_sha256': None, 'scope': 'unverifiable'}
    with board._lock:
        ledger = board.data.setdefault(KEY, {'version': POLICY, 'observations': {}})
        if ledger.get('version') != POLICY or not isinstance(ledger.get('observations'), dict):
            raise WeeklyRunBlocked('Ledger source health incompatibile')
        previous = ledger['observations'].get(event_id)
        if previous is not None:
            if previous.get('evidence') != evidence:
                raise WeeklyRunBlocked('Esito tool source health modificato')
            return deepcopy(previous)
        counters = {'normalization_attempted': 0, 'normalization_completed': 0}
        try:
            counters['normalization_attempted'] += 1
            if serialization_failed:
                raise ValueError('Diagnostic input not serializable')
            observation = normalize_health(tool, deepcopy(result))
            _digest(observation)
            if observation.get('status') not in STATUSES:
                raise ValueError('Invalid source health status')
            counters['normalization_completed'] += 1
        except Exception:
            provider, endpoint = REGISTRY.get(tool, ('unknown', 'unmapped'))
            observation = {'provider': provider, 'endpoint': endpoint, 'status': 'CHECK_UNAVAILABLE',
                'observed_at': None, 'as_of': None, 'coverage': {}, 'cause_codes': ['NORMALIZATION_FAILED']}
        record = {'id': event_id, 'desk': desk if desk in DESKS else None,
                  'round': round_n if type(round_n) is int and round_n in (0, 1, 2) else None,
                  'phase': phase if phase in ('tool', 'preflight') else 'unknown',
                  'evidence': evidence, 'observation': observation, 'counters': counters}
        ledger['observations'][event_id] = record
        return deepcopy(record)


def build_report(ledger):
    records = deepcopy((ledger or {}).get('observations') or {})
    if not isinstance(records, dict):
        raise ValueError('Invalid source health observations')
    observations = [records[key] for key in sorted(records)]
    seen = {(row['observation']['provider'], row['observation']['endpoint']) for row in observations}
    unverified = [{'provider': p, 'endpoint': e, 'status': 'UNVERIFIED', 'cause_codes': ['NOT_OBSERVED']}
                  for p, e in EXPECTED if (p, e) not in seen]
    return {'version': POLICY, 'observations': observations, 'unverified_endpoints': unverified,
            'counters': {'captured': len(observations), 'unverified_endpoints': len(unverified),
                'normalization_attempted': sum(r['counters']['normalization_attempted'] for r in observations),
                'normalization_completed': sum(r['counters']['normalization_completed'] for r in observations)}}


def render_health(report, language='it'):
    """Solo vocabolario chiuso e contatori: niente output, URL, corpo o query."""
    allowed = set(REGISTRY.values()) | set(EXPECTED) | {('unknown', 'unmapped')}
    counts = Counter()
    coverage_counts = {}
    for record in report['observations']:
        o = record['observation']
        pair = (o['provider'], o['endpoint'])
        status = o['status']
        if pair not in allowed or status not in STATUSES or not set(o['cause_codes']) <= CAUSES:
            raise ValueError('Unsafe source health vocabulary')
        desk = record.get('desk') if record.get('desk') in DESKS else 'unattributed'
        key = (pair[0], pair[1], desk, status, ','.join(sorted(o['cause_codes'])))
        counts[key] += 1
        counters = coverage_counts.setdefault(key, Counter())
        for field, value in o.get('coverage', {}).items():
            if field in COVERAGE_FIELDS and type(value) is int and value >= 0:
                counters[field] += value
    lines = ['## SOURCE HEALTH',
        ('Osservazioni della run; cache, configurazione e data di acquisizione non attestano disponibilita live.'
         if language == 'it' else 'Run observations; cache, configuration and acquisition dates do not attest live availability.'),
        ('Conteggi sommati sulle osservazioni; non elementi unici ne cronologia. Dettaglio fase/round nel ledger.'
         if language == 'it' else 'Counts summed across observations; not unique items or chronology. See phase/round detail in the ledger.')]
    for (provider, endpoint, desk, status, causes), count in sorted(counts.items()):
        counters = coverage_counts[(provider, endpoint, desk, status, causes)]
        coverage_text = '; '.join(f'{field}={value}' for field, value in sorted(counters.items()))
        lines.append(f'- {provider}/{endpoint}; {desk}: {status}; n={count}' + (f'; {causes}' if causes else '')
                     + (f'; {coverage_text}' if coverage_text else ''))
    for o in report['unverified_endpoints']:
        if (o['provider'], o['endpoint']) not in EXPECTED or o['status'] != 'UNVERIFIED':
            raise ValueError('Unsafe source health endpoint')
        lines.append(f"- {o['provider']}/{o['endpoint']}: UNVERIFIED; NOT_OBSERVED")
    lines.append('captured=' + str(report['counters']['captured']))
    return '\n'.join(lines)


def context_block(board, language='it'):
    if not enabled(board):
        return ''
    try:
        return render_health(build_report(board.data.get(KEY)), language)
    except Exception:
        return '## SOURCE HEALTH — CHECK_UNAVAILABLE\nRENDER_FAILED'


def checkpoint_source_health(store, source_memo):
    """Integrita verificata fuori dai catch; ripresa senza normalizzare di nuovo."""
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    board = SimpleNamespace(data={}, weekly_store=store)
    if not enabled(board):
        return ''
    store.restore(board)
    ledger = board.data.get(KEY)
    language = store.context.get('language', 'it')
    input_hash = _digest({'source_memo': source_memo, 'ledger': ledger, 'policy': POLICY, 'language': language})
    saved = store.get(STAGE)
    if saved is not None:
        if saved.get('input_sha256') != input_hash:
            raise WeeklyRunBlocked('Input source health modificato')
        return saved['block']
    counters = {'report_attempted': 0, 'report_completed': 0, 'render_attempted': 0, 'render_completed': 0}
    report, error = None, None
    try:
        counters['report_attempted'] += 1
        report = build_report(ledger)
        _digest(report)
        counters['report_completed'] += 1
        counters['render_attempted'] += 1
        block = render_health(report, language)
        if not isinstance(block, str) or not block.strip():
            raise ValueError('Empty source health projection')
        counters['render_completed'] += 1
    except Exception:
        error = 'REPORT_FAILED' if not counters['report_completed'] else 'RENDER_FAILED'
        block = '## SOURCE HEALTH — CHECK_UNAVAILABLE\n' + error
    store.complete(STAGE, {'version': POLICY, 'input_sha256': input_hash, 'report': report,
                           'block': block, 'counters': counters, 'error': error})
    return block
