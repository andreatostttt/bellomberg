"""Signed EV/equity arithmetic on original, recompiled balance-sheet leaves.

This proves reported operands and arithmetic, not their economic inclusion or
recoverable value. A NWC exclusion alone never proves an EV/equity adjustment.
"""
from decimal import Decimal, localcontext
from math import isclose, isfinite
import json
import re

from .balance_sheet_evidence import NORMALIZER, normalize_balance_sheet
from .balance_working_capital import balance_issuer_matches


DISCLOSURE = ('BALANCE BRIDGE CLASSIFICATION: reported component amounts and signed arithmetic '
    'are verified; inclusion, recoverability and overlap with projected cash flows remain '
    'analyst judgments requiring review, not historical valuation facts. ')


def uses_balance_components(item):
    calculation = item.get('calculation') if isinstance(item, dict) else None
    terms = calculation.get('terms') if isinstance(calculation, dict) else None
    return isinstance(terms, list) and any(isinstance(term, dict) and
        isinstance(term.get('evidence_pointer'), dict) and
        str(term['evidence_pointer'].get('value', '')).startswith('/components/') for term in terms)


def balance_component_sum_proof(driver, item, evidence, unit, period, entity, *, scale):
    try:
        if driver not in ('net_debt', 'equity_adjustments'):
            raise ValueError('balance component sum is limited to opening EV/equity bridges')
        if any(key in item for key in ('evidence_pointer', 'evidence_quote', 'quoted_value',
                                       'quoted_unit', 'period_quote', 'facts', 'record_pointer')):
            raise ValueError('balance component sum cannot mix proof formats')
        calc = item['calculation']
        if set(calc) != {'operation', 'terms'} or calc['operation'] != 'sum':
            raise ValueError('explicit signed balance component sum required')
        terms = calc['terms']
        if not isinstance(terms, list) or not 2 <= len(terms) <= 32:
            raise ValueError('balance bridge requires 2 to 32 distinct components')
        catalog = {doc['id']: doc for doc in evidence}
        ids = item.get('evidence_ids')
        if (len(catalog) != len(evidence) or not isinstance(ids, list)
                or any(not isinstance(ident, str) for ident in ids)
                or len(set(ids)) != len(ids) or set(ids) != set(catalog)):
            raise ValueError('balance bridge must cite exactly its original evidence')
        seen, used, proven = set(), set(), {}
        with localcontext() as ctx:
            ctx.prec = 256
            total = Decimal(0)
            for term in terms:
                if not isinstance(term, dict) or set(term) != {
                        'coefficient', 'evidence_ids', 'evidence_pointer', 'quoted_value', 'quoted_unit'}:
                    raise ValueError('balance component term incomplete or unused fields')
                pointers, sources, sign = term['evidence_pointer'], term['evidence_ids'], term['coefficient']
                if (not isinstance(sources, list) or len(sources) != 1 or sources[0] not in catalog
                        or type(sign) is not int or sign not in (-1, 1)
                        or not isinstance(pointers, dict) or set(pointers) != {'value', 'unit', 'period'}):
                    raise ValueError('one balance source, exact pointers and signed coefficient required')
                match = re.fullmatch(r'/components/(0|[1-9][0-9]*)/value_exact', str(pointers['value']))
                if not match:
                    raise ValueError('original balance leaf value_exact pointer required')
                root, index = '/components/' + match[1], int(match[1])
                if pointers['unit'] != root+'/unit' or pointers['period'] != root+'/end':
                    raise ValueError('balance pointers must refer to the same component')
                identity = sources[0], index
                if identity in seen:
                    raise ValueError('balance component duplicated in bridge')
                seen.add(identity)
                ledger = catalog[sources[0]]
                if ledger['id'] not in proven:
                    meta = ledger.get('metadata') or {}
                    if (meta.get('normalizer') != NORMALIZER or meta.get('source_document_id') not in catalog
                            or not balance_issuer_matches(ledger, entity, catalog) or meta.get('report_date') != period):
                        raise ValueError('balance bridge issuer, date or original primary missing')
                    origin = catalog[meta['source_document_id']]
                    result = normalize_balance_sheet(origin)
                    if result['status'] != 'ready' or any(ledger.get(key) != result['documents'][0].get(key)
                            for key in ('id', 'text', 'sha256', 'metadata', 'url', 'document_sha256', 'published_at')):
                        raise ValueError('balance bridge differs from recompiled primary')
                    proven[ledger['id']] = json.loads(ledger['text'])
                    used.update((ledger['id'], origin['id']))
                component = proven[ledger['id']]['components'][index]
                side = component.get('accounting_side')
                expected_sign = (1 if side == 'liability' else -1) if driver == 'net_debt' else (1 if side == 'asset' else -1)
                factor = scale(component['unit'], unit)
                raw = Decimal(component['value_exact'])
                quote = term['quoted_value']
                if (side not in ('asset', 'liability') or sign != expected_sign or factor is None
                        or component['end'] != period or term['quoted_unit'] != component['unit']
                        or type(quote) not in (int, float) or not isfinite(quote)
                        or not raw.is_finite() or Decimal(str(quote)) != raw):
                    raise ValueError('balance amount, unit, date or economic sign differs')
                total += sign * raw * Decimal(str(factor))
            value = item.get('value')
            if (type(value) not in (int, float) or not isfinite(value)
                    or not isclose(value, float(total), rel_tol=1e-10, abs_tol=1e-8)):
                raise ValueError('bridge does not reconcile to signed balance components')
        if used != set(ids):
            raise ValueError('unused balance bridge evidence')
    except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
        return str(exc)
    return None
