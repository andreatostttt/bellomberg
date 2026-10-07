"""Immutable input acquisition for weekly quant rendering; no financial formulas."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import json
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked, digest

POLICY = 'weekly-quant-snapshot/1'
STAGE = 'quant_render_context_v1'
UNSET = object()
PRODUCERS = {
    'nav_history': ('bellomberg.portfolio.portfolio_analytics', 'compute_nav_history'),
    'garch': ('bellomberg.portfolio.portfolio_garch', 'compute_portfolio_garch'),
    'mc': ('bellomberg.portfolio.portfolio_montecarlo', 'run_monte_carlo'),
    'factors': ('bellomberg.portfolio.portfolio_factors', 'compute_portfolio_factors'),
    'rates_us': ('bellomberg.market_data.macro_rates', 'get_us_curve'),
    'rates_de': ('bellomberg.market_data.macro_rates', 'get_bund_curve'),
    'rates_jp': ('bellomberg.market_data.macro_rates', 'get_jgb_curve'),
    'rates_credit': ('bellomberg.market_data.macro_rates', 'get_eu_hy_credit'),
}
SLOTS = ('nav_history', 'risk_data', 'advanced_metrics', 'beta_reconcile',
         'garch', 'mc', 'factors', 'rates_us', 'rates_de', 'rates_jp', 'rates_credit')


def enabled(store):
    contract = store.context.get('contract', {})
    if 'quant_render_policy' not in contract:
        return False
    policy = contract['quant_render_policy']
    if policy != POLICY:
        raise WeeklyRunBlocked('Policy quant render non compatibile')
    return True


def entry(value, source, cause=None):
    payload, payload_hash = None, None
    status = 'UNAVAILABLE'
    code = cause or 'MISSING_INPUT'
    if value is not None:
        try:
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            if not isinstance(value, dict):
                raise ValueError('Invalid snapshot shape')
            payload = json.loads(serialized)
            payload_hash = sha256(serialized.encode('utf-8')).hexdigest()
            if payload and not payload.get('error') and payload.get('status') not in ('error', 'needs_connector', 'declared_gap'):
                status, code = 'AVAILABLE', None
            else:
                code = cause or ('PRODUCER_ERROR' if payload else 'EMPTY_INPUT')
        except (ValueError, TypeError, OverflowError):
            payload, payload_hash, code = None, None, 'INVALID_INPUT'
    return {'status': status, 'cause': code, 'source': source,
            'payload': payload, 'payload_sha256': payload_hash}


def _collect(slot):
    module, name = PRODUCERS[slot]
    return getattr(importlib.import_module(module), name)()


def capture_once(store, slot):
    """Persist intent before the existing acquisition; an uncertain result is not retried."""
    if slot not in PRODUCERS:
        raise ValueError('Unknown quant snapshot component')
    signature = {'version': POLICY, 'slot': slot, 'producer': '.'.join(PRODUCERS[slot]), 'kwargs': {}}
    intent_key, result_key = 'quant_input_intent:' + slot, 'quant_input_result:' + slot
    saved = store.get(result_key)
    intent = store.get(intent_key)
    if intent is not None and intent.get('signature') != signature:
        raise WeeklyRunBlocked('Contratto acquisizione quant modificato')
    if saved is not None:
        if intent is None or saved.get('signature') != signature:
            raise WeeklyRunBlocked('Risultato quant senza intento coerente')
        return deepcopy(saved['entry'])
    if intent is not None:
        result = entry(None, signature['producer'], 'ATTEMPT_OUTCOME_UNAVAILABLE')
        result['status'] = 'ATTEMPT_OUTCOME_UNAVAILABLE'
    else:
        store.complete(intent_key, {'signature': signature,
            'intent_at': datetime.now(timezone.utc).isoformat()})
        try:
            value = _collect(slot)
        except WeeklyRunBlocked:
            raise
        except Exception:
            result = entry(None, signature['producer'], 'PRODUCER_FAILED')
        else:
            result = entry(value, signature['producer'])
    # Persistence and integrity exceptions are never converted to source failures.
    store.complete(result_key, {'signature': signature, 'entry': result})
    return deepcopy(result)


def prepare_snapshot(store):
    """Use only persisted shared inputs; collect the remaining original appendix inputs once."""
    if not enabled(store):
        return UNSET
    render = store.get('render_context')
    priming = store.get('priming')
    if render is None or priming is None:
        raise WeeklyRunBlocked('Contesto quant persistito assente')
    nav_result = store.get('quant_input_result:nav_history')
    dependencies = {'version': POLICY, 'render_context': render, 'nav_result': nav_result,
                    'advanced_metrics': priming.get('quant_advanced_metrics'),
                    'beta_reconcile': priming.get('beta_reconcile'),
                    'run_id': store.run_id, 'language': store.context.get('language', 'it')}
    input_hash = digest(dependencies)
    saved = store.get(STAGE)
    if saved is not None:
        if saved.get('input_sha256') != input_hash:
            raise WeeklyRunBlocked('Dipendenze snapshot quant modificate')
        return deepcopy(saved)
    entries = {
        'nav_history': entry(render.get('nav_history'), 'render_context.nav_history'),
        'risk_data': entry(render.get('risk_data'), 'render_context.risk_data'),
        'advanced_metrics': deepcopy(priming.get('quant_advanced_metrics')) or entry(None, 'priming.quant_advanced_metrics'),
        'beta_reconcile': entry(priming.get('beta_reconcile'), 'priming.beta_reconcile'),
    }
    # Keep precise acquisition-failure reasons for nav without changing the shared renderer value.
    if nav_result is not None:
        entries['nav_history'] = deepcopy(nav_result['entry'])
    for slot in PRODUCERS:
        if slot != 'nav_history':
            entries[slot] = capture_once(store, slot)
    result = {'version': POLICY, 'run_id': store.run_id, 'input_sha256': input_hash,
              'research_started_at': store.context.get('research_started_at'),
              'captured_at': datetime.now(timezone.utc).isoformat(), 'entries': entries}
    store.complete(STAGE, result)
    return deepcopy(result)


def payload_for(snapshot, slot):
    """Explicitly missing snapshots/slots never mean permission to fetch."""
    item = ((snapshot or {}).get('entries') or {}).get(slot) if isinstance(snapshot, dict) else None
    if not isinstance(item, dict):
        return {'error': 'MISSING_FROZEN_INPUT', 'status': 'error'}
    payload = item.get('payload')
    if isinstance(payload, dict):
        expected = entry(payload, item.get('source'))['payload_sha256']
        if not expected or item.get('payload_sha256') != expected:
            raise WeeklyRunBlocked('Hash payload quant modificato')
        return deepcopy(payload)
    return {'error': item.get('cause') or 'MISSING_FROZEN_INPUT', 'status': 'error'}
