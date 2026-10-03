"""Two-filing SEC prompt projection with an independently proved TTM."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation import sec_preparation_sections

select_sec_fcff_context = sec_preparation_sections.select_sec_fcff_context


CIK = '0000000999'
ANNUAL_ACC = '0000000999-25-000001'
CURRENT_ACC = '0000000999-25-000002'
ANNUAL = '''Cover and table of contents.
Item 8.
Financial Statements and Supplementary Data
ITEM 1. BUSINESS
Products, markets and customers.
ITEM 1A. RISK FACTORS
Complete annual risk discussion.
ITEM 1B. UNRESOLVED STAFF COMMENTS
None.
ITEM 1C. CYBERSECURITY
Annual cyber risk.
ITEM 2. PROPERTIES
Facilities.
ITEM 3. LEGAL PROCEEDINGS
Annual legal exposure.
ITEM 4. MINE SAFETY DISCLOSURES
None.
ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS
Annual operating drivers and guidance context.
ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK
Annual market risks.
ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA
Consolidated statements and cash flows.
NOTES TO CONSOLIDATED FINANCIAL STATEMENTS
Organization and all annual accounting notes without numbered headings.
Working capital accounting policies and contingent commitments.
ITEM 9. CHANGES IN AND DISAGREEMENTS WITH ACCOUNTANTS ON ACCOUNTING AND FINANCIAL DISCLOSURE
None.
'''
CURRENT = '''Quarter cover and table of contents.
Item 1.
Financial Statements
ITEM 1. FINANCIAL STATEMENTS
Consolidated current and comparative statements.
NOTES TO CONDENSED CONSOLIDATED FINANCIAL STATEMENTS
Current working capital notes without numbered headings.
ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS
Current business trends and guidance.
ITEM 1A. RISK FACTORS
Current emerging risk.
'''


def _document(text, *, form=None, report_date=None, accession=None, published='2025-08-01'):
    digest = sha256(text.encode()).hexdigest()
    result = {'id': digest, 'document_sha256': digest, 'text': text, 'sha256': digest,
              'url': f'https://www.sec.gov/Archives/edgar/data/999/{accession.replace("-", "")}/filing.htm',
              'published_at': published, 'available_at': published,
              'metadata': {'emittente_id': 'CIK:' + CIK, 'issuer': 'SYNTHETIC INC',
                           'form': form, 'report_date': report_date, 'accession': accession}}
    return result


def _xbrl(primary, observations):
    accession = primary['metadata']['accession']
    body = {'cik': CIK, 'issuer': 'Synthetic Inc.', 'facts': [{
        'taxonomy': 'us-gaap', 'concept': 'RevenueFromContractWithCustomerExcludingAssessedTax',
        'unit': 'USD', 'observation': {'val': value, 'start': start, 'end': end,
                                     'accn': accession, 'filed': primary['published_at'],
                                     'form': primary['metadata']['form']}}
        for start, end, value in observations]}
    text = json.dumps(body, sort_keys=True)
    accession_plain = accession.replace('-', '')
    return {'id': f'xbrl-{CIK}-{accession_plain}', 'text': text,
            'sha256': sha256(text.encode()).hexdigest(), 'document_sha256': sha256(text.encode()).hexdigest(),
            'url': f'https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json',
            'published_at': primary['published_at'], 'available_at': primary['published_at'],
            'metadata': {'emittente_id': 'CIK:' + CIK, 'accession': accession_plain}}


def _case(*, unit_typo=True):
    annual = _document(ANNUAL, form='10-K', report_date='2024-12-31',
                       accession=ANNUAL_ACC, published='2025-02-01')
    current = _document(CURRENT, form='10-Q', report_date='2025-06-30',
                        accession=CURRENT_ACC)
    annual_xbrl = _xbrl(annual, [('2024-01-01', '2024-12-31', 100_000_000)])
    current_xbrl = _xbrl(current, [('2025-01-01', '2025-06-30', 70_000_000),
                                   ('2024-01-01', '2024-06-30', 50_000_000)])

    def term(doc, index, amount, *, typo=False):
        root = f'/facts/{index}'
        value = root + '/observation/val'
        return {'evidence_ids': [doc['id']], 'evidence_pointer': {
            'value': value, 'unit': value if typo else root + '/unit',
            'period': root + '/observation/end'}, 'quoted_value': amount, 'quoted_unit': 'USD'}

    revenue = {'value': 120.0, 'kind': 'historical',
               'evidence_ids': [annual_xbrl['id'], current_xbrl['id']],
               'calculation': {'operation': 'trailing_twelve_months', 'terms': {
                   'annual': term(annual_xbrl, 0, 100_000_000),
                   'current_ytd': term(current_xbrl, 0, 70_000_000),
                   'prior_ytd': term(current_xbrl, 1, 50_000_000, typo=unit_typo)}}}
    repairs = []
    if unit_typo:
        repairs.append({'source_id': current_xbrl['id'],
                        'supplied_unit_pointer': '/facts/1/observation/val',
                        'validated_unit_pointer': '/facts/1/unit',
                        'reason': 'unit duplicated the value pointer of the same SEC TTM observation; '
                                  'quoted amount, unit, period, issuer, concept and accession verified',
                        'driver': 'historical_revenue', 'scenario': 'model'})
    dossier = {'ticker': 'SYNTH', 'as_of': '2025-08-02', 'method_id': 'operating_fcff',
               'documents': [annual, current, annual_xbrl, current_xbrl],
               'acquired_sources': {}, 'document_acquisition': {'selection': {
                   'opening_date': '2025-06-30', 'selected_document_id': current['id'],
                   'annual_document_id': annual['id'], 'comparative_document_id': None,
                   'selected_document_ids': [current['id'], annual['id']]}}}
    context = deepcopy(dossier)
    context['completed_plan'] = {'model': {
        'perimeter': {'value': {'entity': 'SYNTHETIC INC', 'currency': 'USD'}},
        'calendar': {'value': {'valuation_date': '2025-06-30'}},
        'historical_revenue': revenue},
        'scenarios': {s: {} for s in ('bear', 'base', 'bull')}}
    context['completed_proof_normalizations'] = repairs
    return dossier, context


def _view(dossier, context, scope='model', purpose=None):
    assert hasattr(sec_preparation_sections, 'select_sec_two_filing_context')
    stage = {'scope': scope, 'drivers': ['opening_nwc'] if scope == 'model' else []}
    if purpose:
        stage['purpose'] = purpose
    return sec_preparation_sections.select_sec_two_filing_context(
        dossier, context, {'preparation_stage': stage})


def test_opening_keeps_both_complete_statement_sections_and_reproves_traced_ttm():
    dossier, context = _case()
    original = deepcopy(dossier)
    assert select_sec_fcff_context(dossier, context, {'preparation_stage': {'scope': 'model'}}) is None
    view = _view(dossier, context)
    texts = {d['id']: d.get('text') for d in view['documents']}
    annual, current = dossier['documents'][:2]
    assert 'Working capital accounting policies and contingent commitments' in texts[annual['id']]
    assert 'Current working capital notes without numbered headings' in texts[current['id']]
    assert 'Annual operating drivers' not in texts[annual['id']]
    assert 'Current business trends' not in texts[current['id']]
    assert all(d['text'] == texts[d['id']] for d in dossier['documents'][2:])
    selected = view['stage_view']['automatic_selection']
    assert selected['annual_document_id'] == annual['id']
    assert selected['current_document_id'] == current['id']
    assert selected['ttm_reproof']['pointer_repairs'] == context['completed_proof_normalizations']
    assert dossier == original


def test_growth_before_model_judgments_and_scenarios_keep_required_economics():
    dossier, context = _case(unit_typo=False)
    for name in ('quotation', 'opening_nwc', 'shares'):
        context['completed_plan']['model'][name] = {'value': 1}
    for scenario in ('bear', 'base', 'bull'):
        context['completed_plan']['scenarios'][scenario] = {
            'net_debt': {'value': 1}}
    annual, current = dossier['documents'][:2]
    for scope, purpose in (('forecast', 'growth_thesis'), ('bear', None)):
        if scope == 'bear':
            context['completed_plan']['model']['capdev_amortization_years'] = {'value': 5}
        view = _view(dossier, context, scope, purpose)
        texts = {d['id']: d.get('text') for d in view['documents']}
        for required in ('Products, markets and customers', 'Complete annual risk discussion',
                         'Annual cyber risk', 'Facilities.', 'Annual legal exposure', 'Annual operating drivers',
                         'Annual market risks', 'all annual accounting notes'):
            assert required in texts[annual['id']]
        current_entry = next(row for row in view['stage_view']['automatic_selection']['manifest']
                             if row['source_id'] == current['id'])
        assert current_entry['excerpts'][0]['char_start'] == 0
        assert current_entry['excerpts'][0]['char_end_exclusive'] == len(CURRENT)
        assert CURRENT in texts[current['id']]
        assert view['stage_view']['automatic_selection']['scope'] == scope


@pytest.mark.parametrize('fault', ['missing_revenue', 'missing_trace', 'bad_amount',
                                   'bad_accession', 'wrong_cik', 'bad_report_date',
                                   'ambiguous_heading', 'wrong_primary_hash', 'extra_primary'])
def test_invalid_two_filing_selection_fails_closed(fault):
    dossier, context = _case()
    if fault == 'missing_revenue':
        del context['completed_plan']['model']['historical_revenue']
    elif fault == 'missing_trace':
        context['completed_proof_normalizations'] = []
    elif fault == 'bad_amount':
        context['completed_plan']['model']['historical_revenue']['calculation']['terms']['prior_ytd']['quoted_value'] += 1
    elif fault == 'bad_accession':
        dossier['documents'][3]['metadata']['accession'] = '000000099925000003'
    elif fault == 'wrong_cik':
        dossier['documents'][1]['metadata']['emittente_id'] = 'CIK:0000000998'
    elif fault == 'bad_report_date':
        dossier['documents'][1]['metadata']['report_date'] = '2025-07-01'
    elif fault == 'ambiguous_heading':
        dossier['documents'][0]['text'] += 'ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA\nExtra.\n'
        dossier['documents'][0]['sha256'] = sha256(dossier['documents'][0]['text'].encode()).hexdigest()
        dossier['documents'][0]['document_sha256'] = dossier['documents'][0]['id'] = dossier['documents'][0]['sha256']
        dossier['document_acquisition']['selection']['annual_document_id'] = dossier['documents'][0]['id']
    elif fault == 'wrong_primary_hash':
        dossier['documents'][0]['text'] += 'Tampered.'
    elif fault == 'extra_primary':
        dossier['documents'].append(deepcopy(dossier['documents'][1]))
    if fault == 'wrong_primary_hash':
        with pytest.raises(ValueError, match='SHA'):
            _view(dossier, context)
    else:
        assert _view(dossier, context) is None
