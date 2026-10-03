"""Reconcile a complete SEC printed IFRS balance from original table cells.

This ledger proves reported arithmetic and coverage, not economic treatment.
"""
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path
import json

from .pdf_balance_evidence import _reconcile
from .statement_table_evidence import (
    _columns, _grid, _identity, _json, _number, extract_statement_packet,
    normalize_statement_tables,
)

_HEADINGS = {'Non-current assets': 'Noncurrent assets', 'Current assets': 'Current assets',
             'EQUITY': 'Equity', 'Non-current liabilities': 'Noncurrent liabilities',
             'Current liabilities': 'Current liabilities'}
_CONTAINERS = {'ASSETS', 'LIABILITIES'}
_TOTALS = {'Total assets', 'Total equity', 'Total liabilities',
           'Total equity and liabilities'}


def _reported_row(source, table_index, row_index, cell, label, section, unit, opening, *, proof):
    amount = _number(cell, [], exact=True)
    if amount is None:
        raise ValueError('balance amount is not an exact reported number')
    return {'reported_tag': f'reported-statement:{table_index}:{row_index}:{cell["cell_index"]}',
            'namespace': 'reported-statement', 'value_exact': str(amount), 'unit': unit,
            'end': opening, 'label': label, 'reported_section': section,
            'proof': {'source_document_id': source['id'], 'table_index': table_index,
                      'row_index': row_index, 'cell_index': cell['cell_index'],
                      'column': cell['column'], 'cell_text': cell['text'], **proof}}


def _opening_rows(source, observations):
    opening = source['metadata']['report_date']
    facts = [f for f in observations if f['statement'] == 'balance' and f['end'] == opening]
    if not facts or len({f['proof']['table_index'] for f in facts}) != 1:
        raise ValueError('one opening printed balance table required')
    table_index = facts[0]['proof']['table_index']
    table = next((t for t in source['statement_table_fields']['tables']
                  if t['index'] == table_index), None)
    if table is None:
        raise ValueError('printed balance table differs from verified packet')
    rows = _grid(table['rows'])
    columns, header = _columns(rows, 'balance')
    current = [(a, b) for a, b, p in columns if p['end'] == opening]
    if len(current) != 1 or len({f['unit'] for f in facts}) != 1:
        raise ValueError('unique dated opening balance and monetary unit required')
    left, right = current[0]; unit = facts[0]['unit']
    by_row = {}
    for fact in facts:
        if fact['unit'] != unit or fact['proof']['source_document_id'] != source['id']:
            raise ValueError('opening balance unit or source changes')
        index = fact['proof']['row_index']
        if index in by_row:
            raise ValueError('ambiguous opening balance row')
        by_row[index] = fact

    result, disclosures, liabilities, section, closing = [], [], None, None, False
    seen = set()
    for row_index, row in enumerate(rows[header + 1:], header + 1):
        if not row:
            continue
        label = row[0]['text']
        dated = [c for c in row if left <= c['column'] < right]
        if any(c['end_column'] > right for c in dated):
            raise ValueError('balance cell crosses dated-column boundary')
        populated = [c for c in dated if c['text']]
        if closing:
            if label or populated:
                raise ValueError('uncovered rows after printed balance closing')
            continue
        if (section is None and row_index == header + 1 and not label
                and {c['text'] for c in row if c['text']} <= {'Notes', '(Unaudited)'}):
            continue
        if label in _CONTAINERS or label in _HEADINGS:
            if populated or row_index in by_row:
                raise ValueError('classified balance heading has an amount')
            if label in _HEADINGS:
                section = _HEADINGS[label]
                if section in seen:
                    raise ValueError('duplicate printed balance section')
                seen.add(section)
            continue
        if not label and not populated:
            continue
        if section is None or not label:
            raise ValueError('unclassified or unlabeled printed balance row')
        fact = by_row.pop(row_index, None)
        if fact is None:
            if (len(populated) == 1 and populated[0]['text'] == '-'
                    and label not in _TOTALS):
                cell = populated[0]
                disclosures.append({'label': label, 'cell_text': '-',
                                    'reported_section': section,
                                    'source_document_id': source['id'],
                                    'table_index': table_index, 'row_index': row_index,
                                    'cell_index': cell['cell_index'], 'column': cell['column'],
                                    'end': opening})
                continue
            raise ValueError('printed balance row has no unique numeric observation')
        proof = fact['proof']
        cell = next((c for c in dated if c['cell_index'] == proof['cell_index']
                     and c['column'] == proof['column']
                     and c['text'] == proof['cell_text']), None)
        if (cell is None or Decimal(str(fact['value'])) != _number(cell, row, exact=True)
                or fact['label'] != label):
            raise ValueError('normalized amount differs from the reported source cell')
        result.append(_reported_row(source, table_index, row_index, cell, label, section,
                                    unit, opening, proof={'packet_sha256': proof['packet_sha256']}))
        subtotal = proof.get('section_subtotal')
        if subtotal:
            printed = subtotal['subtotal']
            parent_cell = next((c for c in dated if c['cell_index'] == printed['cell_index']
                                and c['column'] == printed['column']
                                and c['text'] == printed['cell_text']), None)
            if (_HEADINGS.get(subtotal['section']) != section
                    or parent_cell is None or printed['row_index'] != row_index
                    or _number(parent_cell, row, exact=True) != Decimal(str(printed['value']))):
                raise ValueError('reported section subtotal differs from source cells')
            result.append(_reported_row(source, table_index, row_index, parent_cell, None,
                                        section, unit, opening,
                                        proof={'packet_sha256': proof['packet_sha256'],
                                               'section_subtotal': subtotal}))
        if label == 'Total liabilities':
            liabilities = result.pop()
        if label == 'Total equity and liabilities':
            closing = True
    if by_row or not closing or set(_HEADINGS.values()) != seen:
        raise ValueError('printed balance rows or classified sections remain uncovered')
    if not liabilities:
        raise ValueError('reported total liabilities missing')
    return result, disclosures, liabilities


def normalize_printed_balance(source):
    """Recompile all reported balance cells; never fill missing rows or infer NWC."""
    from .balance_sheet_evidence import NORMALIZER, PREFIX
    try:
        meta, _, _ = _identity(source)
        if meta['form'] not in ('6-K', '6-K/A', '20-F', '20-F/A'):
            raise ValueError('printed IFRS balance requires a foreign SEC primary')
        # The packet hash alone cannot attest column geometry: a caller could
        # rehash moved spans while keeping the same flat text. Re-extract it
        # from the original SHA-bound bytes before certifying full coverage.
        raw = Path(source['archive_path']).read_bytes()
        if extract_statement_packet(source, raw) != source['statement_table_fields']:
            raise ValueError('printed statement layout differs from verified source bytes')
        statement = normalize_statement_tables(source)
        if statement['status'] != 'ready':
            raise ValueError('printed statements cannot be recompiled: '+repr(statement['issues']))
        observations = json.loads(statement['documents'][0]['text'])['facts']
        with localcontext() as ctx:
            ctx.prec = 256
            rows, disclosures, liabilities = _opening_rows(source, observations)
            groups, leaves = _reconcile(rows)
            sections = {g['structural_role']: g['parent'] for g in groups}
            if (Decimal(liabilities['value_exact']) !=
                    sum((Decimal(sections[s]['value_exact']) for s in
                         ('Noncurrent liabilities', 'Current liabilities')), Decimal(0))):
                raise ValueError('reported total liabilities does not reconcile')
        text = _json({'issuer': meta['issuer'], 'report_date': meta['report_date'],
                      'groups': groups, 'components': leaves,
                      'reported_total_liabilities': liabilities,
                      'nonmonetary_disclosures': disclosures,
                      'reported_balance_reconciled': True,
                      'economic_classification_approved': False})
        doc = {'id': PREFIX + source['id'], 'document_sha256': source['id'],
               'url': source['url'], 'published_at': source['published_at'],
               'text': text, 'sha256': sha256(text.encode()).hexdigest(),
               'origin': NORMALIZER,
               'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'],
                            'entity': meta['issuer'], 'scope': 'consolidated',
                            'report_date': meta['report_date'], 'source_format': 'sec_html_printed_ifrs',
                            'security_identity_verified': False,
                            'limitation': 'Complete reported opening balance and exact totals only. '
                                          'Dashes are disclosures, not zeros. No economic classification, '
                                          'NWC treatment, valuation inputs or forecast inferred.'}}
        return {'status': 'ready', 'documents': [doc], 'issues': []}
    except (ValueError, KeyError, TypeError, IndexError, ArithmeticError, OSError) as exc:
        return {'status': 'incomplete', 'documents': [],
                'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}
