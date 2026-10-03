"""Exact typed observations in a sourced JSON statement, with no inferred fields.

This is a proof format, not an economic certification or a new extractor. A raw
document must itself contain the whole named observation. The preparer cannot
turn an analyst's plan or a source URL into a historical observation.
"""
from copy import deepcopy
import json
from math import isclose


FIELDS = frozenset({'field', 'driver', 'value', 'entity', 'period', 'unit', 'accounting_basis'})


def prove_reported_record(item, evidence, *, driver, field, entity, period, unit, basis):
    from .input_evidence import _pointer
    from .input_preparation import _finite, _source_scale
    if len(evidence) != 1 or not isinstance(item.get('record_pointer'), str):
        return 'A typed historical observation requires one source and an exact JSON record_pointer'
    if any(key in item for key in ('facts', 'evidence_quote', 'quoted_value', 'quoted_unit',
                                   'period_quote', 'evidence_pointer', 'calculation')):
        return 'The exact typed record proof cannot be combined with ignored leaf or arithmetic proofs'
    try:
        raw = json.loads(evidence[0]['text'])
        observed = _pointer(raw, item['record_pointer'])
        if not isinstance(observed, dict) or set(observed) != FIELDS:
            return 'Source typed observation has missing or unconsumed metadata'
        expected = {'field': field, 'driver': driver, 'entity': entity,
                    'period': period, 'accounting_basis': basis}
        if any(observed.get(key) != value for key, value in expected.items()):
            return 'Source measure, legal entity, period or accounting basis differs from the consumed historical driver'
        source_value, value = observed['value'], item.get('value')
        if _finite(source_value) and _finite(value):
            scale = _source_scale(observed['unit'], unit)
            if scale is None or not isclose(value, source_value * scale, rel_tol=1e-10, abs_tol=1e-9):
                return 'Historical value does not match the source observation and declared unit scale'
        elif observed['unit'] != unit or json.dumps(source_value, sort_keys=True, allow_nan=False) != json.dumps(value, sort_keys=True, allow_nan=False):
            return 'Historical contract differs from the exact source observation; no inferred zero, label, right or claim is allowed'
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return 'Unverified source typed observation: ' + str(exc)
    return None


def record_period(timing, calendar, span, *, method, opening_bridge=False):
    """Mirror the actual adapter's economic spans, including managed care FY/YTD."""
    if timing == 'opening' or opening_bridge:
        return calendar['valuation_date']
    if method == 'managed_care_distributable_equity':
        periods = calendar['fiscal_periods']
        if timing == 'actuals':
            return calendar['actuals_start'] + '/' + calendar['valuation_date']
        if timing == 'terminal':
            return periods[-1]['end']
        if timing == 'annual':
            return '|'.join(p['start'] + '/' + p['end'] for p in periods)
        if timing == 'future':
            from datetime import date, timedelta
            future = (date.fromisoformat(calendar['valuation_date']) + timedelta(days=1)).isoformat()
            return '|'.join((future if index == 0 else p['start']) + '/' + p['end'] for index, p in enumerate(periods))
    if timing == 'terminal':
        return calendar['periods'][-1]['end']
    return span
