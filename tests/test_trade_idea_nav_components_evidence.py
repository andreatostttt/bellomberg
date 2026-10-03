"""Synthetic statement layouts verify complete NAV account coverage boundaries."""
from copy import deepcopy
from hashlib import sha256
import pytest

from bellomberg.valuation import nav_components_evidence as nav_evidence


BODY = '''Synthetic Holding Ltd. 26
STATEMENT OF FINANCIAL POSITION
As of June 30, 2026 and December 31, 2025
(Stated in United States Dollars)
2026 2025
Assets
Cash and cash equivalents $ 20 $ 10
Due from brokers 10 10
Investments in securities 4,5 170 140
Derivative financial instruments 4,5 — —
Total Assets $ 200 $ 160
Liabilities
Trade and other payables 10 10
Deferred tax expense payables 10 10
Bonds 11 70 40
Total Liabilities $ 90 $ 60
Equity
Share capital 6 90 90
Treasury shares 6 (10) (10)
Retained earnings 30 20
Total Equity 110 100
Total Liabilities and Equity $ 200 $ 160
Net assets attributable to Public Shares $ 100 $ 90
Public Shares outstanding 10 10
Net assets per Public Share $ 10.00 $ 9.00
Net assets attributable to Special Voting Share $ 10 $ 10
Special Voting Share outstanding 1 1
Net assets per Special Voting Share $ 9.89 $ 10.00
'''


def source(body=BODY):
    digest = sha256(body.encode()).hexdigest()
    return {'id': 'a'*64, 'document_sha256': 'a'*64, 'url': 'https://example.org/synthetic/statement.pdf',
        'published_at': '2026-08-12', 'text': body, 'sha256': digest,
        'metadata': {'issuer': 'Synthetic Holding Ltd.', 'report_date': '2026-06-30'},
        'page_references': [{'pagina': 1, 'inizio': 0, 'fine': len(body), 'sha256': digest}]}


def classification():
    return {'version': 'exhaustive_balance_sheet_accounts/1',
        'assets': {'Cash and cash equivalents': 'cash', 'Due from brokers': 'gross_assets',
            'Investments in securities': 'gross_assets', 'Derivative financial instruments': 'gross_assets'},
        'liabilities': {'Trade and other payables': 'other_liabilities', 'Deferred tax expense payables': 'tax', 'Bonds': 'debt'},
        'aggregate_coverage': {'Trade and other payables': ['accrued_fees', 'distributions_payable']},
        'class_policy': 'exclude_all_other_reported_share_classes',
        'dash_policy': 'printed_dash_is_nil_balance',
        'rationale': 'Synthetic account assessment; fees and distributions remain economically undisaggregated.'}


def normalize(primary=None, mapping=None, **kwargs):
    return nav_evidence.normalize_nav_components(primary or source(), classification=mapping or classification(),
        on=kwargs.get('on', '2026-06-30'), as_of='2026-09-28',
        entity='Synthetic Holding Ltd.', share_class=kwargs.get('share_class', 'Public Shares'))


def test_complete_account_mapping_and_class_allocation_produce_diagnostic_only():
    result = normalize()
    assert result['status'] == 'diagnostic_ready', result['issues']
    assert result['usable'] is False and result['method_records'] == []
    assert result['component_basis'] == 'incremental_claim_deductions/1'
    diagnostic = result['diagnostic']
    assert diagnostic['incremental_components'] == {'gross_assets': 180.0, 'cash': 20.0, 'debt': 70.0,
        'preferred': 0.0, 'other_liabilities': 10.0, 'accrued_fees': 0.0, 'distributions_payable': 0.0,
        'tax': 10.0, 'equity_adjustments': -10.0}
    assert diagnostic['common_equity_nav'] == 100.0 and diagnostic['nav_per_share'] == 10.0
    assert diagnostic['economic_stock_components']['accrued_fees'] is None
    assert diagnostic['economic_stock_components']['distributions_payable'] is None
    assert diagnostic['covered_aggregates']['Trade and other payables']['value'] == 10.0
    assert result['checks']['every_balance_account_consumed_once'] is True
    assert result['checks']['both_comparative_columns_closed'] is True
    assert result['checks']['all_reported_classes_allocated'] is True
    assert result['blocking_gaps'] and 'dilution' in repr(result['blocking_gaps'])
    for row in result['account_rows']:
        assert source()['text'][row['locator']['start']:row['locator']['end']] == row['locator']['text']


def test_absent_fee_lines_without_explicit_aggregate_coverage_do_not_prove_stock_zero():
    mapping = classification()
    mapping['aggregate_coverage'] = {}
    result = normalize(mapping=mapping)
    assert result['status'] == 'incomplete'
    assert result['diagnostic'] is None and result['issues']


@pytest.mark.parametrize('fault', ['missing_asset', 'missing_liability', 'unknown_account', 'cash_as_gross',
    'liability_as_cash', 'double_count_aggregate', 'no_rationale', 'unknown_policy', 'unknown_key'])
def test_unmapped_wrong_signed_or_unsupported_account_criteria_are_not_proofs(fault):
    mapping = classification()
    if fault == 'missing_asset': del mapping['assets']['Due from brokers']
    elif fault == 'missing_liability': del mapping['liabilities']['Bonds']
    elif fault == 'unknown_account': mapping['assets']['Invented asset'] = 'gross_assets'
    elif fault == 'cash_as_gross': mapping['assets']['Cash and cash equivalents'] = 'gross_assets'
    elif fault == 'liability_as_cash': mapping['liabilities']['Bonds'] = 'cash'
    elif fault == 'double_count_aggregate': mapping['liabilities']['Trade and other payables'] = 'accrued_fees'
    elif fault == 'no_rationale': mapping['rationale'] = ''
    elif fault == 'unknown_policy': mapping['class_policy'] = 'ignore_unselected_classes'
    else: mapping['ignored_approval'] = True
    result = normalize(mapping=mapping)
    assert result['status'] == 'incomplete' and result['usable'] is False
    assert result['diagnostic'] is None and result['issues'] and result['method_records'] == []


@pytest.mark.parametrize('old,new', [
    ('Bonds 11 70 40', 'Bonds 11 71 40'),
    ('Due from brokers 10 10', 'Due from brokers 9 10'),
    ('Retained earnings 30 20', 'Retained earnings 30 21'),
    ('Bonds 11 70 40', 'Bonds 11 70'),
    ('Bonds 11 70 40', 'Bonds 11 70 40\nBonds 11 70 40'),
    ('2026 2025', '2025 2026'),
    ('Derivative financial instruments 4,5 — —', 'Derivative financial instruments 4,5 n.d. —'),
])
def test_totals_do_not_hide_missing_duplicate_ambiguous_or_unclosed_accounts(old, new):
    result = normalize(primary=source(BODY.replace(old, new)))
    assert result['status'] == 'incomplete' and result['diagnostic'] is None


@pytest.mark.parametrize('kwargs', [{'on': '2025-12-31'}, {'share_class': 'Preferred Shares'}])
def test_identity_date_and_selected_class_remain_exact(kwargs):
    result = normalize(**kwargs)
    assert result['status'] == 'incomplete' and result['diagnostic'] is None


def test_changed_primary_text_without_original_page_hash_is_not_normalized():
    primary = source()
    primary['text'] = primary['text'].replace('Bonds 11 70 40', 'Bonds 11 71 40')
    primary['sha256'] = sha256(primary['text'].encode()).hexdigest()
    result = normalize(primary=primary)
    assert result['status'] == 'incomplete' and result['issues']


def test_engine_component_contract_rechecks_source_math_and_rejects_double_deductions():
    result = normalize()
    original = result['component_value']
    kwargs = {'entity': 'Synthetic Holding Ltd.', 'share_class': 'Public Shares', 'on': '2026-06-30',
              'currency': 'USD', 'as_of': '2026-09-28'}
    numeric, error = nav_evidence.verify_components_contract(original, **kwargs)
    assert error is None and numeric['accrued_fees'] == 0.0
    for changed in ('accrued_fees', 'other_liabilities', 'equity_adjustments'):
        modified = deepcopy(original)
        modified[changed] += 1e-6
        numeric, error = nav_evidence.verify_components_contract(modified, **kwargs)
        assert numeric is None and error
    modified = deepcopy(original)
    modified['coverage']['share_class'] = 'Special Voting Share'
    assert nav_evidence.verify_components_contract(modified, **kwargs)[1]
    modified = deepcopy(original)
    modified['coverage']['normalization_sha256'] = '0' * 64
    assert nav_evidence.verify_components_contract(modified, **kwargs)[1]


def dilution_source():
    quote = ('As of 2026-06-30, Synthetic Holding Ltd. had no outstanding options, warrants or convertible instruments.')
    primary = source(BODY + '\n' + quote)
    primary['page_references'] = [
        {'pagina': 1, 'inizio': 0, 'fine': len(BODY), 'sha256': sha256(BODY.encode()).hexdigest()},
        {'pagina': 2, 'inizio': len(BODY)+1, 'fine': len(primary['text']), 'sha256': sha256(quote.encode()).hexdigest()}]
    report = normalize(primary=primary)
    assert report['status'] == 'diagnostic_ready', report['issues']
    proof = {'primary_document_id': primary['id'], 'primary_text_sha256': primary['sha256'],
        'page': {'number': 2, 'text': quote, 'sha256': sha256(quote.encode()).hexdigest()},
        'entity': 'Synthetic Holding Ltd.', 'as_of': '2026-06-30', 'evidence_quote': quote}
    return primary, report, proof


def test_dilution_proof_is_not_inferred_from_basic_shares_or_a_closed_balance():
    primary, report, proof = dilution_source()
    kwargs = {'coverage': report['component_value']['coverage'], 'entity': 'Synthetic Holding Ltd.',
              'on': '2026-06-30', 'as_of': '2026-09-28'}
    assert nav_evidence.verify_dilution_proof(proof, source=primary, **kwargs) is None
    assert nav_evidence.verify_dilution_proof(proof, **kwargs) is None
    assert nav_evidence.verify_dilution_proof(None, source=primary, **kwargs)
    for old, new in (('warrants', 'other assets'), ('2026-06-30', '2025-12-31'),
                     ('no outstanding', 'no material outstanding')):
        modified = deepcopy(proof)
        modified['page']['text'] = modified['page']['text'].replace(old, new)
        modified['page']['sha256'] = sha256(modified['page']['text'].encode()).hexdigest()
        modified['evidence_quote'] = modified['page']['text']
        assert nav_evidence.verify_dilution_proof(modified, **kwargs)
    modified = deepcopy(proof)
    modified['page']['text'] = modified['page']['text'].rstrip('.') + '; except outstanding employee options.'
    modified['page']['sha256'] = sha256(modified['page']['text'].encode()).hexdigest()
    modified['evidence_quote'] = modified['page']['text']
    assert nav_evidence.verify_dilution_proof(modified, **kwargs)
    modified = deepcopy(proof)
    modified['page']['text'] += ' invented new primary text'
    modified['page']['sha256'] = sha256(modified['page']['text'].encode()).hexdigest()
    assert nav_evidence.verify_dilution_proof(modified, source=primary, **kwargs)


def incremental_bundle(fault=None):
    from test_sector_nav_drivers import nav_records, nav_bundle
    _, report, proof = dilution_source()
    rows = nav_records()
    for row in rows:
        row.update(entity='Synthetic Holding Ltd.', period='2026-06-30')
        if row['driver'] == 'perimeter':
            row['value'] = {'entity': 'Synthetic Holding Ltd.', 'currency': 'USD', 'share_class': 'Public Shares'}
        elif row['driver'] == 'calendar':
            row['value']['valuation_date'] = '2026-06-30'
        elif row['driver'] == 'quotation':
            row['value'].update(financial_currency='USD', quote_currency='USD', quote_unit='USD',
                                price_as_of='2026-06-30', share_class='Public Shares')
        elif row['driver'] == 'shares':
            row['value'] = 10 / 1e6
        elif row['driver'] == 'components':
            row['value'] = deepcopy(report['component_value'])
            row['kind'] = 'historical'
        elif row['driver'] == 'publication':
            row['value'].update(valuation_date='2026-06-30', share_class='Public Shares')
        elif row['driver'] == 'reported_nav_per_share':
            row.update(unit='USD per share')
        elif row['driver'] == 'policy':
            row['value'] = {'liability_basis': 'all_reported_claims_in_components',
                'fees_basis': 'incremental_deductions_after_covered_aggregates',
                'distributions_basis': 'incremental_deductions_after_covered_aggregates',
                'share_basis': 'basic_no_convertibles_or_other_dilution', 'dilution_proof': proof}
    if fault:
        component = next(r for r in rows if r['driver'] == 'components')['value']
        policy = next(r for r in rows if r['driver'] == 'policy')['value']
        if fault == 'double_fee': component['accrued_fees'] += 1e-6
        elif fault == 'omitted_liability': component['other_liabilities'] = 0.0
        elif fault == 'wrong_class': component['coverage']['share_class'] = 'Special Voting Share'
        elif fault == 'missing_dilution': del policy['dilution_proof']
        else:
            policy.pop('dilution_proof')
            policy.update(liability_basis='all_claims_in_components', fees_basis='accrued_fees_in_components',
                          distributions_basis='payables_deducted')
    return nav_bundle(rows)


def test_incremental_common_engine_and_workbook_keep_zero_additional_deduction_semantics(tmp_path):
    from openpyxl import load_workbook
    from bellomberg.core.language import language_context
    from bellomberg.valuation.dcf_engine import generate_valuation
    with language_context('en'):
        payload = generate_valuation('SYNTH-NAV', prepared_bundle=incremental_bundle(), output_dir=str(tmp_path))
    assert payload['valuation_usability']['usable'], payload.get('error')
    scenario = payload['calculation_details']['scenarios']['base']
    assert scenario['components_basis'] == nav_evidence.BASIS
    assert scenario['common_equity_nav'] == pytest.approx(100 / 1e6)
    assert payload['fair_value_base'] == 10.0
    workbook = load_workbook(payload['path'])
    try:
        inputs = workbook['NAV Inputs']
        labels = [str(row[1].value) for row in inputs]
        assert any('additional' in label.lower() and 'fee' in label.lower() for label in labels)
        assert any('additional' in label.lower() and 'distribution' in label.lower() for label in labels)
        assert 'Trade and other payables' in ' '.join(str(cell.value) for row in inputs for cell in row)
        assert "'NAV Inputs'!D18" in workbook['NAV Model']['D12'].value
    finally:
        workbook.close()


@pytest.mark.parametrize('fault', ['double_fee', 'omitted_liability', 'wrong_class', 'missing_dilution', 'legacy_policy'])
def test_incremental_engine_never_drops_coverage_or_dilution_gate(tmp_path, fault):
    from bellomberg.valuation.dcf_engine import generate_valuation
    bundle = incremental_bundle(fault)
    payload = generate_valuation('SYNTH-NAV', prepared_bundle=bundle, output_dir=str(tmp_path))
    assert not payload['valuation_usability']['usable']
    assert payload.get('fair_value_base') is None
