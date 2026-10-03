"""Source-pinned IFRS context selection; no provider, database, or paid request."""
from copy import deepcopy
from hashlib import sha256
import json
import re

import pytest


ISSUER = 'CIK:0000000999'
NAME = 'Synthetic SA'


def _doc(text, *, form, period, accession, published='2026-08-01', **extra):
    digest = sha256(text.encode()).hexdigest()
    return {'id': digest, 'text': text, 'sha256': digest, 'document_sha256': digest,
            'url': 'https://www.sec.gov/Archives/edgar/data/999/' + accession.replace('-', '') + '/report.htm',
            'published_at': published,
            'metadata': {'emittente_id': ISSUER, 'issuer': NAME, 'accession': accession,
                         'form': form, 'report_date': period, **extra}}


def _normalized(source, *, start, end, amount):
    body = {'issuer': NAME, 'facts': [{'taxonomy': 'reported-statement', 'concept': 'Revenue',
        'entity': NAME, 'scope': 'consolidated', 'statement': 'income', 'label': 'Net sales',
        'start': start, 'end': end, 'unit': 'USD thousand', 'value': amount,
        'proof': {'source_document_id': source['id'], 'table_index': 1, 'row_index': 2,
                  'column': 1, 'cell_index': 1, 'cell_text': str(amount),
                  'packet_sha256': 'a' * 64}}]}
    text = json.dumps(body, separators=(',', ':'))
    return {'id': 'statement-tables-' + source['id'], 'text': text,
        'sha256': sha256(text.encode()).hexdigest(), 'document_sha256': source['id'],
        'url': source['url'], 'published_at': source['published_at'],
        'metadata': {'normalizer': 'statement_tables_v1', 'source_document_id': source['id'],
            'emittente_id': ISSUER, 'entity': NAME,
            'accession': source['metadata']['accession'].replace('-', ''),
            'scope': 'consolidated', 'report_date': source['metadata']['report_date']}}


def _annual():
    return '''TABLE OF CONTENTS
RISK FACTORS
12
INFORMATION ON THE COMPANY
24
Operating and Financial Review and Prospects
55
Major Shareholders and Related Party Transactions
90
RISK FACTORS
The issuer faces demand and currency risks that can reduce its operating cash flows and the value of its assets.
INFORMATION ON THE COMPANY
Overview
The issuer serves several industrial markets through distinct operations and earns revenue from equipment and services.
Operating and Financial Review and Prospects
Management discusses demand, margins, investment and liquidity using the reported annual statements and segment information.
Major Shareholders and Related Party Transactions
Ownership discussion ends the selected operating review and is not represented as an operating assumption.
I. GENERAL INFORMATION
Synthetic consolidated issuer.
II. ACCOUNTING POLICIES
A  Property, plant and equipment
Depreciation policies support assets.
B  Intangible assets
Development capitalization has explicit criteria.
C  Inventories
Inventory cost and impairment are described.
D  Trade and other receivables
Receivable classes are described.
E  Current and deferred income tax
Tax bases are described.
F  Provisions
Provision recognition is described.
G  Trade and other payables
Payable classes are described.
H  Revenue recognition
Revenue recognition is described.
III. FINANCIAL RISK MANAGEMENT
Financial risk detail is not a zero balance.
IV. OTHER NOTES TO THE CONSOLIDATED FINANCIAL STATEMENTS
1  Segment information
Segment detail.
2  Inventories, net
Inventory detail.
3  Trade receivables, net
Receivable detail.
4  Cash flow disclosures
Working capital detail.
5  Business combinations
Subsequent developments.
'''


def _case():
    annual = _doc(_annual(), form='20-F', period='2025-12-31',
                  accession='0000000999-26-000001', published='2026-03-31')
    current_text = ('Current primary issuer outlook and guidance.\nMarket Background and Outlook\n'
        'Demand remains conditional.\n' + 'Cash flow and statement detail remain in the primary. ' * 80 + '\n'
        'Consolidated Condensed Interim Financial Statements\n'
        'For the six-month period ended June 30, 2026 - all amounts in thousands of U.S. dollars.\n'
        'Reported current revenue: 65.\n')
    current = _doc(current_text, form='6-K', period='2026-06-30',
                   accession='0000000999-26-000002', perimetro='consolidato')
    prior = _doc('Prior comparative interim statements and narrative.\nPrior guidance remains a dated source.\n',
                 form='6-K', period='2025-06-30', accession='0000000999-25-000003',
                 published='2025-08-01', perimetro='consolidato')
    companion = _doc('Companion cover differs.\n' + current_text + '\nCompanion footer 777 remains.\n',
                     form='6-K', period=None, accession='0000000999-26-000004',
                     event_date='2026-06-30', evidence_role='foreign_current_report')
    n_annual = _normalized(annual, start='2025-01-01', end='2025-12-31', amount=120)
    n_current = _normalized(current, start='2026-01-01', end='2026-06-30', amount=65)
    n_prior = _normalized(prior, start='2025-01-01', end='2025-06-30', amount=60)
    documents = [annual, current, prior, companion, n_annual, n_current, n_prior]
    selection = {'policy': 'opening_plus_latest_annual_and_available_prior_year_comparative',
        'opening_date': '2026-06-30', 'selected_document_id': current['id'],
        'annual_document_id': annual['id'], 'comparative_document_id': prior['id'],
        'selected_document_ids': [current['id'], annual['id'], prior['id']]}
    dossier = {'ticker': 'SYNTH', 'as_of': '2026-09-01', 'method_id': 'operating_fcff',
        'documents': documents, 'document_acquisition': {'selection': selection}}
    def term(doc):
        row = json.loads(doc['text'])['facts'][0]
        return {'evidence_ids': [doc['id']], 'quoted_value': row['value'],
            'quoted_unit': row['unit'], 'evidence_pointer': {
                'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'}}
    revenue = {'value': .125, 'kind': 'historical',
        'evidence_ids': [n_annual['id'], n_current['id'], n_prior['id']],
        'calculation': {'operation': 'trailing_twelve_months', 'terms': {
            'annual': term(n_annual), 'current_ytd': term(n_current), 'prior_ytd': term(n_prior)}}}
    plan = {'model': {'perimeter': {'value': {'entity': NAME}},
        'calendar': {'value': {'valuation_date': '2026-06-30'}},
        'quotation': {'value': {'financial_currency': 'USD'}},
        'historical_revenue': revenue}, 'scenarios': {}, 'scenario_rationale': {}}
    from bellomberg.valuation.preparation_view import select_stage_view
    context = select_stage_view(dossier, 'forecast')
    context['completed_plan'] = plan
    return dossier, context, {'annual': annual, 'current': current, 'prior': prior,
                              'companion': companion, 'n_prior': n_prior}


def _select(dossier, context, *, growth=False):
    from bellomberg.valuation.preparation_sections import select_ifrs_fcff_context
    stage = ({'scope': 'forecast', 'purpose': 'growth_thesis', 'drivers': []} if growth else
             {'scope': 'model', 'drivers': ['opening_nwc']})
    return select_ifrs_fcff_context(dossier, context, {'method_id': 'operating_fcff',
                                                        'preparation_stage': stage})


def test_ifrs_nwc_keeps_current_and_structured_facts_but_excludes_only_proven_prior_narrative():
    dossier, context, docs = _case()
    original = deepcopy((dossier, context))
    result = _select(dossier, context)
    assert result is not None
    visible = {d['id']: d for d in result['documents']}
    assert 'Demand remains conditional.' in visible[docs['current']['id']]['text']
    assert 'Inventory detail.' in visible[docs['annual']['id']]['text']
    assert 'The issuer faces demand' not in visible[docs['annual']['id']]['text']
    assert 'text' not in visible[docs['prior']['id']]
    assert visible[docs['n_prior']['id']]['text'] == docs['n_prior']['text']
    prior_receipt = next(row for row in result['stage_view']['documents']
                         if row['id'] == docs['prior']['id'])
    assert prior_receipt['view'] == 'metadata_only'
    assert prior_receipt['selected_source_utf8_bytes'] == 0
    assert prior_receipt['excluded_source_utf8_bytes'] == len(docs['prior']['text'].encode())
    assert 'excerpts' not in prior_receipt and 'projected_text_sha256' not in prior_receipt
    assert result['completed_plan'] == context['completed_plan']
    assert result['stage_view']['automatic_selection']['semantic_coverage_certified'] is False
    assert (dossier, context) == original


def test_ifrs_growth_uses_actual_annual_business_risk_management_not_toc():
    dossier, context, docs = _case()
    result = _select(dossier, context, growth=True)
    assert result is not None
    annual = next(d['text'] for d in result['documents'] if d['id'] == docs['annual']['id'])
    assert 'The issuer faces demand and currency risks' in annual
    assert 'The issuer serves several industrial markets' in annual
    assert 'Management discusses demand, margins' in annual
    assert 'Inventory detail.' in annual
    assert 'TABLE OF CONTENTS' not in annual


@pytest.mark.parametrize('damage', ['hash', 'issuer', 'selection', 'period', 'normalized',
                                    'future', 'ttm_value', 'ttm_pointer'])
def test_ifrs_selection_fails_closed_on_identity_period_or_historical_proof(damage):
    dossier, context, docs = _case()
    if damage == 'hash':
        docs['current']['text'] += ' changed'
    elif damage == 'issuer':
        docs['prior']['metadata']['emittente_id'] = 'CIK:0000000888'
    elif damage == 'selection':
        dossier['document_acquisition']['selection']['selected_document_id'] = docs['prior']['id']
    elif damage == 'period':
        docs['prior']['metadata']['report_date'] = '2025-03-31'
    elif damage == 'normalized':
        dossier['documents'].remove(docs['n_prior'])
    elif damage == 'future':
        docs['current']['published_at'] = '2026-10-01'
    elif damage == 'ttm_value':
        context['completed_plan']['model']['historical_revenue']['value'] = 1.25
    else:
        term = context['completed_plan']['model']['historical_revenue']['calculation']['terms']['prior_ytd']
        term['evidence_pointer']['value'] = '/facts/0/end'
    if damage == 'hash':
        with pytest.raises(ValueError, match='SHA'):
            _select(dossier, context)
    else:
        assert _select(dossier, context) is None


def test_ifrs_companion_deduplicates_exact_whitespace_blocks_and_keeps_differences():
    dossier, context, docs = _case()
    result = _select(dossier, context)
    text = next(d['text'] for d in result['documents'] if d['id'] == docs['companion']['id'])
    assert 'Companion cover differs.' in text
    assert 'Companion footer 777 remains.' in text
    assert 'Cash flow and statement detail remain in the primary. ' * 10 not in text
    assert 'Cash flow and statement detail remain in the primary. ' * 10 in next(
        d['text'] for d in result['documents'] if d['id'] == docs['current']['id'])
    assert result['stage_view']['automatic_selection']['deduplicated_blocks']


def test_ifrs_companion_preserves_a_changed_number_inside_an_otherwise_identical_statement():
    dossier, context, docs = _case()
    companion = docs['companion']
    phrase = 'Cash flow and statement detail remain in the primary. '
    hits = list(re.finditer(re.escape(phrase), companion['text']))
    assert len(hits) > 40
    offset = hits[40].start()
    companion['text'] = (companion['text'][:offset]
        + phrase.replace('detail', 'detail 999', 1)
        + companion['text'][offset + len(phrase):])
    digest = sha256(companion['text'].encode()).hexdigest()
    companion.update(id=digest, sha256=digest, document_sha256=digest)
    result = _select(dossier, context)
    assert result is not None
    text = next(d['text'] for d in result['documents'] if d['id'] == digest)
    assert 'statement detail 999 remain in the primary' in text
    assert 'Companion footer 777 remains.' in text
    assert result['stage_view']['automatic_selection']['deduplicated_blocks']


def test_legacy_ifrs_selector_result_is_unchanged_by_new_alternative():
    from bellomberg.valuation.preparation_sections import select_fcff_note_sections
    dossier, context, _ = _case()
    before = select_fcff_note_sections(dossier)
    _select(dossier, context)
    assert select_fcff_note_sections(dossier) == before
