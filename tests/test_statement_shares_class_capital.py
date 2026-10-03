"""A listed class count is proved by its own issued and treasury facts."""
from datetime import date
from hashlib import sha256
import json

import pytest

from test_statement_shares_inline import ISSUER, OPENING, _sources


TITLE = 'Class A ordinary shares, par value $0.001 per share'


def _class_sources(tmp_path, fault=None):
    source, tagged, path = _sources(tmp_path)
    raw = path.read_text(encoding='utf-8')
    raw = raw.replace('Common Stock, $0.001 par value per share', TITLE)
    raw = raw.replace('us-gaap:StatementEquityComponentsAxis', 'us-gaap:StatementClassOfStockAxis')
    raw = raw.replace('us-gaap:CommonStockMember', 'us-gaap:CommonClassAMember')
    raw = raw.replace('us-gaap:CommonStockSharesOutstanding', 'us-gaap:CommonStockSharesIssued', 1)
    raw = raw.replace('us-gaap:CommonStockSharesOutstanding', 'us-gaap:TreasuryStockCommonShares', 1)
    raw = raw.replace('scale="6" decimals="-6"', 'scale="0" decimals="INF"')
    raw = raw.replace('>24,147</ix:nonFraction>', '>700,000,000</ix:nonFraction>', 1)
    raw = raw.replace('>24,147</ix:nonFraction>', '>100,000,000</ix:nonFraction>', 1)
    x_context = ('<xbrli:context id="c-x"><xbrli:entity><xbrli:identifier '
                 'scheme="http://www.sec.gov/CIK">0000000123</xbrli:identifier>'
                 '<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
                 'acn:CommonClassXMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>'
                 '<xbrli:period><xbrli:instant>'+OPENING+'</xbrli:instant></xbrli:period></xbrli:context>')
    raw = raw.replace('</body>', x_context + '<ix:nonFraction id="f-x" '
        'name="us-gaap:CommonStockSharesOutstanding" contextRef="c-x" unitRef="shares" '
        'scale="0" decimals="INF" format="ixt:num-dot-decimal">300,673</ix:nonFraction></body>')
    if fault == 'missing_treasury':
        raw = raw.replace('us-gaap:TreasuryStockCommonShares', 'us-gaap:CommonStockSharesIssued')
    elif fault == 'ambiguous_period':
        raw = raw.replace('</xbrli:instant>', '</xbrli:instant><xbrli:instant>2026-07-27</xbrli:instant>', 1)
    elif fault == 'ambiguous_raw_pointer':
        raw = raw.replace('</body>', '<div id="f-1">duplicate raw ID</div></body>')
    elif fault in ('wrong_class_treasury', 'wrong_date_treasury', 'wrong_cik_treasury'):
        context = x_context.replace('id="c-x"', 'id="c-other"')
        if fault == 'wrong_date_treasury':
            context = context.replace(OPENING, '2026-07-27').replace('acn:CommonClassXMember',
                'us-gaap:CommonClassAMember')
        elif fault == 'wrong_cik_treasury':
            context = context.replace('0000000123', '0000000456').replace('acn:CommonClassXMember',
                'us-gaap:CommonClassAMember')
        raw = raw.replace('</body>', context + '</body>')
        raw = raw.replace('id="f-2" name="us-gaap:TreasuryStockCommonShares" contextRef="c-1"',
                          'id="f-2" name="us-gaap:TreasuryStockCommonShares" contextRef="c-other"')
    elif fault == 'wrong_unit_treasury':
        raw = raw.replace('</body>', '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure>'
            '</xbrli:unit></body>')
        raw = raw.replace('id="f-2" name="us-gaap:TreasuryStockCommonShares" contextRef="c-1" unitRef="shares"',
                          'id="f-2" name="us-gaap:TreasuryStockCommonShares" contextRef="c-1" unitRef="usd"')
    elif fault == 'duplicate_issued_conflict':
        raw = raw.replace('</body>', '<ix:nonFraction id="f-4" name="us-gaap:CommonStockSharesIssued" '
            'contextRef="c-1" unitRef="shares" scale="0" decimals="INF" '
            'format="ixt:num-dot-decimal">800,000,000</ix:nonFraction></body>')
    elif fault == 'direct_outstanding_conflict':
        raw = raw.replace('</body>', '<ix:nonFraction id="f-4" name="us-gaap:CommonStockSharesOutstanding" '
            'contextRef="c-1" unitRef="shares" scale="0" decimals="INF" '
            'format="ixt:num-dot-decimal">650,000,000</ix:nonFraction></body>')
    elif fault == 'extra_dimensional_rollforward':
        extra = ('<xbrli:context id="c-more"><xbrli:entity><xbrli:identifier '
                 'scheme="http://www.sec.gov/CIK">0000000123</xbrli:identifier><xbrli:segment>'
                 '<xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
                 'us-gaap:CommonClassAMember</xbrldi:explicitMember>'
                 '<xbrldi:explicitMember dimension="us-gaap:StatementEquityComponentsAxis">'
                 'us-gaap:CommonStockMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>'
                 '<xbrli:period><xbrli:instant>'+OPENING+'</xbrli:instant></xbrli:period></xbrli:context>'
                 '<ix:nonFraction id="f-more" name="us-gaap:CommonStockSharesIssued" '
                 'contextRef="c-more" unitRef="shares" scale="6" decimals="-6" '
                 'format="ixt:num-dot-decimal">700</ix:nonFraction>')
        raw = raw.replace('</body>', extra+'</body>')
    elif fault == 'rounded_operands':
        raw = raw.replace('scale="0" decimals="INF"', 'scale="6" decimals="-6"')
        raw = raw.replace('>700,000,000</ix:nonFraction>', '>700</ix:nonFraction>')
        raw = raw.replace('>100,000,000</ix:nonFraction>', '>100</ix:nonFraction>')
    raw_bytes = raw.encode()
    path.write_bytes(raw_bytes)
    source['id'] = source['document_sha256'] = sha256(raw_bytes).hexdigest()
    return source, tagged, path


def test_listed_class_issued_less_treasury_is_recompiled_with_both_raw_operands(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog, _compile
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    source, tagged, _ = _class_sources(tmp_path)
    normalized = normalize_statement_shares(source, [tagged])
    assert normalized['status'] == 'ready', normalized
    doc = normalized['documents'][0]
    body = json.loads(doc['text'])
    assert body['facts'][0] == {'taxonomy': 'sec_statement_shares_v1',
        'concept': 'CommonStockSharesOutstanding', 'entity': ISSUER,
        'share_class': 'Class A ordinary shares', 'value': 600_000_000,
        'unit': 'shares', 'end': OPENING}
    assert body['source_proof']['calculation'] == {
        'operation': 'issued_minus_treasury',
        'issued': {'fact_ids': ['f-1'], 'raw_fact_pointers': ['//*[@id="f-1"]'],
                   'context_id': 'c-1', 'value': 700_000_000,
                   'unit': 'shares', 'scale': 0, 'decimals': 'INF'},
        'treasury': {'fact_ids': ['f-2'], 'raw_fact_pointers': ['//*[@id="f-2"]'],
                     'context_id': 'c-1', 'value': 100_000_000,
                     'unit': 'shares', 'scale': 0, 'decimals': 'INF'}}
    assert body['reported_precision']['exact_legal_count'] is True
    assert body['tag_comparison']['status'] == 'missing_same_date_tag'
    assert doc['metadata']['share_title'] == TITLE
    catalog, issues, _ = _catalog([source, tagged, doc], date(2026, 9, 1))
    assert not issues
    item = {'value': 600., 'kind': 'historical', 'evidence_ids': [doc['id']],
        'calculation': {'type': 'statement_shares', 'fact_index': 0,
            'selection_basis': 'primary_inline_without_same_date_tag', 'acknowledged_conflicts': []},
        'rationale': 'Class A net opening shares.', 'valid_until': '2026-09-01',
        'valid_until_basis': {'policy': 'same_day', 'as_of': '2026-09-01'}}
    rows, problems, _ = _compile({'model': {'shares': item}, 'scenarios': {
        name: {} for name in ('bear', 'base', 'bull')}},
        {'shares': ('shares', 'million shares', 'common', 'opening', 'number', 'model')}, {},
        {'entity': ISSUER, 'currency': 'USD', 'share_class': TITLE},
        {'valuation_date': OPENING}, None, catalog, date(2026, 9, 1))
    assert not problems and len(rows) == 1
    assert '700000000' in rows[0]['rationale'] and '100000000' in rows[0]['rationale']
    assert 'issued minus' in rows[0]['rationale'].lower()
    assert 'treasury =' in rows[0]['rationale'].lower()


def test_other_rollforward_dimension_is_not_the_class_balance(tmp_path):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    source, tagged, _ = _class_sources(tmp_path, 'extra_dimensional_rollforward')
    result = normalize_statement_shares(source, [tagged])
    assert result['status'] == 'ready', result
    proof = json.loads(result['documents'][0]['text'])['source_proof']['calculation']
    assert proof['issued']['fact_ids'] == ['f-1']


def test_subtracted_count_retains_both_operand_rounding_limits(tmp_path):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares, share_conflict_disclosure
    source, tagged, _ = _class_sources(tmp_path, 'rounded_operands')
    result = normalize_statement_shares(source, [tagged])
    assert result['status'] == 'ready', result
    doc = result['documents'][0]
    precision = json.loads(doc['text'])['reported_precision']
    assert precision['exact_legal_count'] is False
    assert precision['maximum_rounding_error_shares'] == '1000000'
    assert 'not an exact legal count' in share_conflict_disclosure([doc])


@pytest.mark.parametrize('fault', ['missing_treasury', 'ambiguous_period', 'ambiguous_raw_pointer',
    'wrong_class_treasury', 'wrong_date_treasury',
    'wrong_cik_treasury', 'wrong_unit_treasury', 'duplicate_issued_conflict',
    'direct_outstanding_conflict'])
def test_class_count_refuses_unproved_subtraction_or_primary_conflict(tmp_path, fault):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    source, tagged, _ = _class_sources(tmp_path, fault)
    result = normalize_statement_shares(source, [tagged])
    assert result['status'] == 'incomplete' and not result['documents'], (fault, result)


def test_class_count_admission_requires_recompiled_listed_class_and_intact_raw(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_fresh_historical import _has_opening_share_observation
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    source, tagged, path = _class_sources(tmp_path)
    doc = normalize_statement_shares(source, [tagged])['documents'][0]
    catalog, issues, _ = _catalog([source, tagged, doc], date(2026, 9, 1))
    assert not issues
    filings = [catalog[source['id']]]
    assert _has_opening_share_observation(catalog, ISSUER, OPENING, '2026-09-01', filings,
        share_class=TITLE)
    assert not _has_opening_share_observation(catalog, ISSUER, OPENING, '2026-09-01', filings,
        share_class='Class X ordinary shares')
    assert not _has_opening_share_observation(catalog, ISSUER, OPENING, '2026-09-01', filings)
    path.write_bytes(path.read_bytes() + b'after catalog')
    assert not _has_opening_share_observation(catalog, ISSUER, OPENING, '2026-09-01', filings,
        share_class=TITLE)
