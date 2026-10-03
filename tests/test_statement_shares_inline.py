"""An inline SEC equity rollforward can prove dated, rounded common shares."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import re

import pytest


OPENING = '2026-07-26'
ISSUER = 'Synthetic Technology Inc'
ACCESSION = '0000000123-26-000001'


def _sources(tmp_path, *, raw_fault=None, tag_value=None):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    raw = '''<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
xmlns:us-gaap="http://fasb.org/us-gaap/2026" xmlns:dei="http://xbrl.sec.gov/dei/2026"
xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"><body>
<ix:nonNumeric name="dei:EntityRegistrantName">Synthetic Technology, Inc.</ix:nonNumeric>
<ix:nonNumeric name="dei:EntityCentralIndexKey">0000000123</ix:nonNumeric>
<ix:nonNumeric name="dei:DocumentType">10-Q</ix:nonNumeric>
<ix:nonNumeric name="dei:DocumentPeriodEndDate">July 26, 2026</ix:nonNumeric>
<ix:nonNumeric name="dei:Security12bTitle">Common Stock, $0.001 par value per share</ix:nonNumeric>
<xbrli:context id="c-1"><xbrli:entity>
<xbrli:identifier scheme="http://www.sec.gov/CIK">0000000123</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementEquityComponentsAxis">us-gaap:CommonStockMember</xbrldi:explicitMember></xbrli:segment>
</xbrli:entity><xbrli:period><xbrli:instant>2026-07-26</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
<table><tr><td>Balances as of Jul 26, 2026</td><td><ix:nonFraction id="f-1" name="us-gaap:CommonStockSharesOutstanding" contextRef="c-1" unitRef="shares" scale="6" decimals="-6" format="ixt:num-dot-decimal">24,147</ix:nonFraction></td></tr></table>
<table><tr><td>Balances as of Jul 26, 2026</td><td><ix:nonFraction id="f-2" name="us-gaap:CommonStockSharesOutstanding" contextRef="c-1" unitRef="shares" scale="6" decimals="-6" format="ixt:num-dot-decimal">24,147</ix:nonFraction></td></tr></table>
</body></html>'''
    if raw_fault == 'raw_cik':
        raw = raw.replace('>0000000123</ix:nonNumeric>', '>0000000456</ix:nonNumeric>')
    elif raw_fault == 'context_cik':
        raw = raw.replace('>0000000123</xbrli:identifier>', '>0000000456</xbrli:identifier>')
    elif raw_fault == 'raw_issuer':
        raw = raw.replace('Synthetic Technology, Inc.', 'Different Technology, Inc.')
    elif raw_fault == 'form':
        raw = raw.replace('>10-Q</ix:nonNumeric>', '>8-K</ix:nonNumeric>')
    elif raw_fault == 'header_period':
        raw = raw.replace('>July 26, 2026</ix:nonNumeric>', '>July 27, 2026</ix:nonNumeric>')
    elif raw_fault == 'context_period':
        raw = raw.replace('>2026-07-26</xbrli:instant>', '>2026-08-21</xbrli:instant>')
    elif raw_fault == 'other_axis':
        raw = raw.replace('StatementEquityComponentsAxis', 'ClassOfStockAxis')
    elif raw_fault == 'other_member':
        raw = raw.replace('us-gaap:CommonStockMember', 'us-gaap:PreferredStockMember')
    elif raw_fault == 'other_share_title':
        raw = raw.replace('Common Stock, $0.001 par value per share</ix:nonNumeric>',
            'Class A Common Stock, $0.001 par value per share</ix:nonNumeric>')
    elif raw_fault == 'extra_dimension':
        raw = raw.replace('</xbrli:segment>', '<xbrldi:explicitMember dimension="us-gaap:ClassOfStockAxis">us-gaap:ClassACommonStockMember</xbrldi:explicitMember></xbrli:segment>')
    elif raw_fault == 'extra_entity':
        raw = raw.replace('</xbrli:entity>', '</xbrli:entity><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000456</xbrli:identifier></xbrli:entity>')
    elif raw_fault == 'extra_period':
        raw = raw.replace('</xbrli:period>', '</xbrli:period><xbrli:period><xbrli:instant>2026-07-26</xbrli:instant></xbrli:period>')
    elif raw_fault == 'extra_instant':
        raw = raw.replace('</xbrli:instant>', '</xbrli:instant><xbrli:instant>2026-08-21</xbrli:instant>')
    elif raw_fault == 'extra_identifier':
        raw = raw.replace('</xbrli:identifier>', '</xbrli:identifier><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000456</xbrli:identifier>')
    elif raw_fault == 'extra_segment':
        raw = raw.replace('</xbrli:entity>', '</xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:ClassOfStockAxis">us-gaap:ClassACommonStockMember</xbrldi:explicitMember></xbrli:segment>')
    elif raw_fault == 'scenario':
        raw = raw.replace('</xbrli:period>', '</xbrli:period><xbrli:scenario><xbrldi:explicitMember dimension="us-gaap:ClassOfStockAxis">us-gaap:ClassACommonStockMember</xbrldi:explicitMember></xbrli:scenario>')
    elif raw_fault == 'typed_member':
        raw = raw.replace('</xbrli:period>', '</xbrli:period><xbrli:scenario><xbrldi:typedMember dimension="us-gaap:ClassOfStockAxis">A</xbrldi:typedMember></xbrli:scenario>')
    elif raw_fault == 'missing_namespace':
        raw = raw.replace('xmlns:us-gaap="http://fasb.org/us-gaap/2026" ', '')
    elif raw_fault == 'wrong_namespace':
        raw = raw.replace('http://www.xbrl.org/2003/instance', 'http://example.invalid/instance')
    elif raw_fault == 'wrong_transform_namespace':
        raw = raw.replace('http://www.xbrl.org/inlineXBRL/transformation/2020-02-12', 'http://example.invalid/transform')
    elif raw_fault == 'local_namespace':
        raw = raw.replace('<xbrli:context id="c-1">', '<xbrli:context id="c-1" xmlns:us-gaap="http://example.invalid/us-gaap">')
    elif raw_fault == 'wrong_unit':
        raw = raw.replace('xbrli:shares</xbrli:measure>', 'iso4217:USD</xbrli:measure>')
    elif raw_fault == 'weighted_only':
        raw = raw.replace('us-gaap:CommonStockSharesOutstanding', 'us-gaap:WeightedAverageNumberOfSharesOutstandingBasic')
    elif raw_fault == 'issued_only':
        raw = raw.replace('us-gaap:CommonStockSharesOutstanding', 'us-gaap:CommonStockSharesIssued')
    elif raw_fault == 'conflicting_duplicate':
        raw = raw.replace('>24,147</ix:nonFraction></td></tr></table>\n</body>', '>24,148</ix:nonFraction></td></tr></table>\n</body>')
    elif raw_fault == 'wrong_precision':
        raw = raw.replace('decimals="-6"', 'decimals="2"')
    elif raw_fault == 'duplicate_context':
        original = re.search(r'<xbrli:context id="c-1">.*?</xbrli:context>', raw, re.S).group()
        raw = raw.replace(original, original.replace('CommonStockMember', 'PreferredStockMember') + original)
    elif raw_fault == 'duplicate_unit':
        original = '<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>'
        raw = raw.replace(original, original.replace('xbrli:shares', 'iso4217:USD') + original)
    elif raw_fault == 'full_integer_rounded':
        raw = raw.replace('scale="6"', 'scale="0"').replace('>24,147</ix:nonFraction>',
            '>24,147,000,000</ix:nonFraction>')
    path = tmp_path / 'filing.htm'
    path.write_bytes(raw.encode())
    digest = sha256(raw.encode()).hexdigest()
    text = 'Synthetic consolidated equity rollforward. Numeric details remain in archived SEC raw bytes.'
    source = {'id': digest, 'document_sha256': digest, 'sha256': sha256(text.encode()).hexdigest(),
        'text': text, 'archive_path': str(path),
        'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
        'published_at': '2026-08-26', 'metadata': {'form': '10-Q', 'emittente_id': 'CIK:0000000123',
        'accession': ACCESSION, 'report_date': OPENING, 'issuer': ISSUER}}
    facts = [
        {'taxonomy': 'dei', 'concept': 'EntityCommonStockSharesOutstanding', 'unit': 'shares',
         'observation': {'val': 24_100_000_000, 'end': '2026-08-21', 'filed': '2026-08-26', 'accn': ACCESSION}},
        {'taxonomy': 'us-gaap', 'concept': 'WeightedAverageNumberOfSharesOutstandingBasic', 'unit': 'shares',
         'observation': {'val': 24_190_000_000, 'start': '2026-04-27', 'end': OPENING,
                         'filed': '2026-08-26', 'accn': ACCESSION}}]
    if tag_value is not None:
        facts.append({'taxonomy': 'us-gaap', 'concept': 'CommonStockSharesOutstanding', 'unit': 'shares',
            'observation': {'val': tag_value, 'end': OPENING, 'filed': '2026-08-26', 'accn': ACCESSION}})
    body = json.dumps({'cik': '0000000123', 'issuer': 'Synthetic Technology, Inc.', 'facts': facts})
    tagged = {'id': 'xbrl-0000000123-000000012326000001', 'text': body,
        'sha256': sha256(body.encode()).hexdigest(), 'published_at': source['published_at'],
        'url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000000123.json',
        'metadata': {'emittente_id': 'CIK:0000000123', 'accession': '000000012326000001'}}
    return source, tagged, path


def test_inline_equity_common_count_is_rounded_opening_fact_reproved_by_catalog(tmp_path):
    from bellomberg.valuation.statement_shares_evidence import (
        normalize_statement_shares, statement_share_proof, share_conflict_disclosure)
    from bellomberg.valuation.input_preparation import _catalog, _compile, _source_scale

    source, tagged, _ = _sources(tmp_path)
    result = normalize_statement_shares(source, [tagged])
    assert result['status'] == 'ready', result
    doc = result['documents'][0]
    body = json.loads(doc['text'])
    assert body['facts'][0]['value'] == 24_147_000_000
    assert body['facts'][0]['end'] == OPENING and body['facts'][0]['unit'] == 'shares'
    assert body['reported_precision'] == {'decimals': -6, 'rounding_unit_shares': 1_000_000,
                                           'exact_legal_count': False}
    assert body['tag_comparison']['status'] == 'missing_same_date_tag'
    assert body['tag_comparison']['observations'] == []
    assert doc['metadata']['share_title'] == 'Common Stock, $0.001 par value per share'
    catalog, issues, _ = _catalog([source, tagged, doc], date(2026, 9, 1))
    assert not issues and doc['id'] in catalog
    item = {'value': 24_147., 'evidence_ids': [doc['id']], 'calculation': {
        'type': 'statement_shares', 'fact_index': 0,
        'selection_basis': 'primary_inline_without_same_date_tag', 'acknowledged_conflicts': []}}
    assert statement_share_proof('shares', item, [doc], 'million shares', OPENING,
        ISSUER, _source_scale) is None
    assert 'rounded to 1000000 shares' in share_conflict_disclosure([doc])
    assert 'not an exact legal count' in share_conflict_disclosure([doc])
    item.update(kind='historical', rationale='Inline reported opening common shares.',
        valid_until='2026-09-01', valid_until_basis={'policy': 'same_day', 'as_of': '2026-09-01'})
    rows, problems, _ = _compile({'model': {'shares': item}, 'scenarios': {
        name: {} for name in ('bear', 'base', 'bull')}},
        {'shares': ('shares', 'million shares', 'common', 'opening', 'number', 'model')}, {},
        {'entity': ISSUER, 'currency': 'USD',
         'share_class': 'Common Stock, $0.001 par value per share'},
        {'valuation_date': OPENING}, None, catalog, date(2026, 9, 1))
    assert not problems and len(rows) == 1
    assert 'rounded to 1000000 shares' in rows[0]['rationale']
    assert 'same-date SEC companyfacts common-share tag absent' in rows[0]['rationale']


@pytest.mark.parametrize('fault', [
    'raw_cik', 'context_cik', 'raw_issuer', 'form', 'header_period', 'context_period',
    'other_axis', 'other_member', 'other_share_title', 'extra_dimension', 'wrong_unit', 'weighted_only',
    'issued_only', 'conflicting_duplicate', 'wrong_precision',
    'duplicate_context', 'duplicate_unit',
    'extra_entity', 'extra_period', 'extra_instant', 'extra_identifier', 'extra_segment',
    'scenario', 'typed_member', 'missing_namespace', 'wrong_namespace',
    'wrong_transform_namespace', 'local_namespace',
])
def test_inline_variant_rejects_unbound_or_ambiguous_count(tmp_path, fault):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    source, tagged, _ = _sources(tmp_path, raw_fault=fault)
    result = normalize_statement_shares(source, [tagged])
    assert result['status'] == 'incomplete' and result['documents'] == [], (fault, result)


def test_inline_count_rejects_changed_raw_and_missing_companyfacts(tmp_path):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    source, tagged, path = _sources(tmp_path)
    path.write_bytes(path.read_bytes() + b'tamper')
    assert normalize_statement_shares(source, [tagged])['status'] == 'incomplete'
    source, _, _ = _sources(tmp_path)
    assert normalize_statement_shares(source, [])['status'] == 'incomplete'


def test_inline_precision_comes_from_declared_decimals_even_with_full_printed_integer(tmp_path):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    source, tagged, _ = _sources(tmp_path, raw_fault='full_integer_rounded')
    result = normalize_statement_shares(source, [tagged])
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    assert body['facts'][0]['value'] == 24_147_000_000
    assert body['reported_precision'] == {'decimals': -6,
        'rounding_unit_shares': 1_000_000, 'exact_legal_count': False}


@pytest.mark.parametrize('tag_value,status,basis', [
    (24_147_000_000, 'consistent', 'primary_statement_consistent_with_tags'),
    (24_100_000_000, 'conflict', 'primary_statement_over_conflicting_tags'),
])
def test_inline_count_retains_same_date_companyfacts_comparison(tmp_path, tag_value, status, basis):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    source, tagged, _ = _sources(tmp_path, tag_value=tag_value)
    result = normalize_statement_shares(source, [tagged])
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    assert body['tag_comparison']['status'] == status
    assert body['tag_comparison']['selection_basis'] == basis
    assert bool(body['tag_comparison']['conflicts']) == (status == 'conflict')


def test_inline_normalized_receipt_cannot_hide_rounding_or_raw_change(tmp_path):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    from bellomberg.valuation.input_preparation import _catalog

    source, tagged, _ = _sources(tmp_path)
    doc = normalize_statement_shares(source, [tagged])['documents'][0]
    altered = deepcopy(doc)
    body = json.loads(altered['text'])
    body['reported_precision']['exact_legal_count'] = True
    altered['text'] = json.dumps(body)
    altered['sha256'] = sha256(altered['text'].encode()).hexdigest()
    assert _catalog([source, tagged, altered], date(2026, 9, 1))[1]


def test_printed_unrounded_row_precedes_unqualified_inline_tag(tmp_path):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    from test_statement_shares_evidence import sources

    primary, tagged = sources()
    baseline = normalize_statement_shares(primary, [tagged])
    assert baseline['status'] == 'ready'
    primary['text'] += ('\n<ix:nonFraction name="us-gaap:CommonStockSharesOutstanding" '
                        'contextRef="unsupported">12,345,678</ix:nonFraction>')
    raw = primary['text'].encode()
    path = tmp_path / 'filing.htm'; path.write_bytes(raw)
    digest = sha256(raw).hexdigest()
    primary.update(id=digest, document_sha256=digest, sha256=digest,
                   archive_path=str(path))
    result = normalize_statement_shares(primary, [tagged])
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    old = json.loads(baseline['documents'][0]['text'])
    assert body['facts'] == old['facts']
    assert body['tag_comparison'] == old['tag_comparison']
