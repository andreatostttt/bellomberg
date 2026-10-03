"""A printed IFRS ledger needs every row and the reported arithmetic."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.market_data.lettore_trimestrali import estrai_testo
from bellomberg.valuation.statement_table_evidence import (
    extract_statement_packet, normalize_statement_tables,
)


def primary(tmp_path, *, damage=None):
    units = ('<p>Consolidated Financial Statements - all amounts in thousands of '
             'U.S. dollars, unless otherwise stated</p>')

    def row(label, amount='', subtotal='', comparative=None):
        old = amount if comparative is None else comparative
        return (f'<tr><td>{label}</td><td>{amount}</td><td></td><td>{subtotal}</td>'
                f'<td>{old}</td><td></td><td>{subtotal}</td></tr>')

    balance = [
        row('ASSETS'), row('Non-current assets'),
        row('Property, plant and equipment', '40'), row('Intangible assets', '10'),
        row('Other assets', '5', '55'),
        row('Current assets'), row('Inventories', '20'),
        row('Cash and cash equivalents', '5', '25'), row('Total assets', '', '80'),
        row('EQUITY'), row('Capital stock', '30'), row('Reserves', '10'),
        row('Total equity', '', '40'), row('LIABILITIES'),
        row('Non-current liabilities'), row('Borrowings', '15'),
        row('Derivative financial instruments', '-'), row('Provisions', '5', '20'),
        row('Current liabilities'), row('Trade payables', '10'),
        row('Tax liabilities', '10', '20'), row('Total liabilities', '', '40'),
        row('Total equity and liabilities', '', '80'),
    ]
    if damage == 'missing_measure':
        balance[17] = row('Provisions', '', '20')
    elif damage == 'false_subtotal':
        balance[17] = row('Provisions', '5', '21')
    elif damage == 'unexplained_row':
        balance.insert(17, row('Unexplained commitment', '3'))
    elif damage == 'unbound_dash':
        balance[16] = row('Derivative financial instruments', 'n.d.')
    summary = ('<table><tr><td>(all amounts in thousands of U.S. dollars, '
               'except number of shares)</td><td></td><td colspan="3">June 30,</td>'
               '<td></td><td colspan="3">December 31,</td></tr>'
               '<tr><td></td><td></td><td colspan="3">2026</td><td></td>'
               '<td colspan="3">2025</td></tr>'
               '<tr><td>Total assets</td><td></td><td>80</td><td></td><td></td>'
               '<td></td><td>80</td></tr>'
               '<tr><td>Total liabilities</td><td></td><td>40</td><td></td><td></td>'
               '<td></td><td>40</td></tr>'
               '<tr><td>Equity</td><td></td><td>40</td><td></td><td></td>'
               '<td></td><td>40</td></tr>'
               '<tr><td>Total liabilities and equity</td><td></td><td>80</td><td></td><td></td>'
               '<td></td><td>80</td></tr>'
               '<tr><td>Number of shares outstanding</td><td></td><td>1,000</td>'
               '<td></td><td></td><td></td><td>990</td></tr></table>')
    if damage == 'share_header_scaled':
        summary = summary.replace('except number of shares', 'unless otherwise stated')
    elif damage == 'share_weighted':
        summary = summary.replace('Number of shares outstanding',
                                  'Weighted average number of shares')
    elif damage == 'share_date':
        summary = summary.replace('June 30,', 'July 1,')
    elif damage == 'share_duplicate':
        duplicate = ('<tr><td>Number of shares outstanding</td><td></td><td>1,001</td>'
                     '<td></td><td></td><td></td><td>991</td></tr>')
        summary = summary.replace('</table>', duplicate + '</table>')
    html = ('<html><body>' + summary + units + '<h2>CONSOLIDATED INCOME STATEMENTS</h2>'
            '<table><tr><td></td><td colspan="2">Six-month period ended June 30,</td></tr>'
            '<tr><td></td><td>2026</td><td>2025</td></tr>'
            '<tr><td>Net sales</td><td>100</td><td>90</td></tr></table>' + units +
            '<h2>CONSOLIDATED STATEMENTS OF FINANCIAL POSITION</h2><table>'
            '<tr><td></td><td colspan="3">At June 30, 2026</td>'
            '<td colspan="3">At December 31, 2025</td></tr>' + ''.join(balance) +
            '</table>' + units + '<h2>CONSOLIDATED STATEMENTS OF CASH FLOWS</h2>'
            '<table><tr><td></td><td colspan="2">Six-month period ended June 30,</td></tr>'
            '<tr><td></td><td>2026</td><td>2025</td></tr>'
            '<tr><td>Cash flows from operating activities</td><td>12</td><td>11</td></tr>'
            '</table></body></html>')
    raw = html.encode(); digest = sha256(raw).hexdigest()
    path = tmp_path / 'primary.html'; path.write_bytes(raw)
    text = estrai_testo(str(path), contenuto=raw)['testo']
    source = {'id': digest, 'document_sha256': digest, 'text': text,
              'sha256': sha256(text.encode()).hexdigest(),
              'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
              'published_at': '2026-08-01', 'archive_path': str(path),
              'metadata': {'emittente_id': 'CIK:0000000123', 'issuer': 'Synthetic Issuer SA',
                           'accession': '0000000123-26-000001', 'form': '6-K',
                           'report_date': '2026-06-30'}}
    source['statement_table_fields'] = extract_statement_packet(source, raw)
    return source


def test_terminal_measure_and_subtotal_reconcile_despite_explicit_dash(tmp_path):
    source = primary(tmp_path)
    result = normalize_statement_tables(source)
    assert result['status'] == 'ready', result
    facts = json.loads(result['documents'][0]['text'])['facts']
    provision = [f for f in facts if f['statement'] == 'balance'
                 and f['label'] == 'Provisions' and f['end'] == '2026-06-30']
    assert len(provision) == 1 and provision[0]['value'] == 5
    proof = provision[0]['proof']['section_subtotal']
    assert proof['subtotal']['value'] == 20
    assert len(proof['reported_dashes']) == 1
    assert proof['reported_dashes'][0]['cell_text'] == '-'
    assert not any(f['label'] == 'Derivative financial instruments'
                   and f['end'] == '2026-06-30' for f in facts)


def test_printed_ledger_covers_all_sections_without_economic_guess(tmp_path):
    from bellomberg.valuation.printed_balance_evidence import normalize_printed_balance
    source = primary(tmp_path)
    result = normalize_printed_balance(source)
    assert result['status'] == 'ready', result
    doc = result['documents'][0]; body = json.loads(doc['text'])
    assert body['reported_balance_reconciled'] is True
    assert body['economic_classification_approved'] is False
    assert len(body['components']) == 9
    assert {f['reported_section'] for f in body['components']} == {
        'Noncurrent assets', 'Current assets', 'Noncurrent liabilities', 'Current liabilities'}
    assert all(f['model_treatment'] is None and f['economic_classification'] == 'unreviewed'
               for f in body['components'])
    assert body['nonmonetary_disclosures'][0]['label'] == 'Derivative financial instruments'
    assert body['nonmonetary_disclosures'][0]['cell_text'] == '-'
    assert doc['metadata']['normalizer'] == 'balance_sheet_v1'
    assert doc['metadata']['source_document_id'] == source['id']


@pytest.mark.parametrize('damage', ['missing_measure', 'false_subtotal',
                                    'unexplained_row', 'unbound_dash'])
def test_printed_balance_fails_closed_on_omission_or_arithmetic(tmp_path, damage):
    from bellomberg.valuation.printed_balance_evidence import normalize_printed_balance
    source = primary(tmp_path, damage=damage)
    result = normalize_printed_balance(source)
    assert result['status'] == 'incomplete' and result['documents'] == [], result


def test_rehashed_packet_cannot_move_reported_amount(tmp_path):
    from bellomberg.valuation.printed_balance_evidence import normalize_printed_balance
    source = primary(tmp_path)
    forged = deepcopy(source)
    rows = forged['statement_table_fields']['tables'][1]['rows']
    rows[18][1]['text'] = '6'
    from bellomberg.valuation.statement_table_evidence import _json
    packet = forged['statement_table_fields']
    packet['sha256'] = sha256(_json({k: v for k, v in packet.items() if k != 'sha256'}).encode()).hexdigest()
    result = normalize_printed_balance(forged)
    assert result['status'] == 'incomplete' and result['documents'] == [], result


def test_rehashed_column_geometry_must_match_archived_html(tmp_path):
    from bellomberg.valuation.printed_balance_evidence import normalize_printed_balance
    from bellomberg.valuation.statement_table_evidence import _json
    source = primary(tmp_path)
    forged = deepcopy(source); packet = forged['statement_table_fields']
    packet['tables'][1]['rows'][3][1]['colspan'] = '2'
    packet['sha256'] = sha256(_json({k: v for k, v in packet.items() if k != 'sha256'}).encode()).hexdigest()
    result = normalize_printed_balance(forged)
    assert result['status'] == 'incomplete' and result['documents'] == [], result


def test_opening_share_row_is_unscaled_and_separate_from_balance_money(tmp_path):
    source = primary(tmp_path)
    result = normalize_statement_tables(source)
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    shares = [f for f in body['facts'] if f['concept'] == 'CommonStockSharesOutstanding']
    assert [(f['value'], f['unit'], f['end']) for f in shares] == [
        (1000, 'shares', '2026-06-30'), (990, 'shares', '2025-12-31')]
    assert all(f['statement'] == 'shares' and f['proof']['source_document_id'] == source['id']
               and f['proof']['unit_exception'].endswith('except number of shares)')
               for f in shares)
    from bellomberg.valuation.printed_balance_evidence import normalize_printed_balance
    ledger = normalize_printed_balance(source)
    assert ledger['status'] == 'ready', ledger
    assert not any(f['unit'] == 'shares' for f in json.loads(ledger['documents'][0]['text'])['components'])


@pytest.mark.parametrize('damage', ['share_header_scaled', 'share_weighted',
                                    'share_date', 'share_duplicate'])
def test_share_count_requires_exact_row_unscaled_unit_and_opening_date(tmp_path, damage):
    source = primary(tmp_path, damage=damage)
    result = normalize_statement_tables(source)
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    assert not any(f['concept'] == 'CommonStockSharesOutstanding' for f in body['facts'])
    assert result['documents'][0]['metadata']['coverage']['share_observation'] != 'ready'


def test_share_packet_rehash_cannot_change_source_column_geometry(tmp_path):
    from bellomberg.valuation.statement_table_evidence import _json
    source = primary(tmp_path); forged = deepcopy(source)
    packet = forged['statement_table_fields']
    packet['share_tables'][0]['rows'][-1][2]['colspan'] = '2'
    packet['sha256'] = sha256(_json({k: v for k, v in packet.items() if k != 'sha256'}).encode()).hexdigest()
    result = normalize_statement_tables(forged)
    assert result['status'] == 'ready', result  # Revenue remains usable.
    assert result['documents'][0]['metadata']['coverage']['share_observation'] == 'incomplete'
    facts = json.loads(result['documents'][0]['text'])['facts']
    assert not any(f['concept'] == 'CommonStockSharesOutstanding' for f in facts)
