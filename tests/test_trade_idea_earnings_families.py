"""Native output comparisons use real common engines and synthetic sources."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.preparation_service import prepare_and_generate
from bellomberg.valuation.trade_idea_earnings_facts import model_earnings_expectations, prove_earnings_fact
from test_trade_idea_families import family_factories, sourced_family


FAMILIES = [0, 1, 4, 5, 6, 7, 8, 11]


def primary(body, *, published='2027-02-01'):
    text = json.dumps(body, sort_keys=True)
    return {'id': 'synthetic-primary-earnings', 'url': 'https://example.org/synthetic/earnings',
            'published_at': published, 'text': text, 'sha256': sha256(text.encode()).hexdigest()}


def income_document(entity, start, end, *, published, currency='EUR', parent_income=20., minorities=10.):
    """US GAAP parent income and profit including nonzero NCI are distinct."""
    facts = [{'taxonomy': 'us-gaap', 'concept': concept, 'unit': currency,
              'observation': {'start': start, 'end': end, 'val': value*1_000_000}}
             for concept, value in [('ProfitLoss', parent_income+minorities),
                                    ('NetIncomeLoss', parent_income),
                                    ('NetIncomeLossAttributableToNoncontrollingInterest', minorities)]]
    document = primary({'synthetic': True, 'issuer': entity, 'facts': facts}, published=published)
    document.update(id='xbrl-0000000001-'+published.replace('-', '')+'000001',
                    url='https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json')
    return document


@pytest.fixture(scope='module', params=FAMILIES)
def native_model(request, tmp_path_factory):
    bundle, plan, documents = sourced_family(family_factories()[request.param])
    if bundle['decision']['method_id'] == 'bank_residual_income':
        entity = plan['model']['perimeter']['value']['entity']
        fact = {'taxonomy': 'us-gaap', 'concept': 'NetIncomeLossAvailableToCommonStockholdersBasic',
                'unit': 'EUR', 'observation': {'start': '2025-01-01', 'end': '2025-12-31', 'val': 20_000_000.}}
        doc = primary({'synthetic': True, 'issuer': entity, 'facts': [fact]}, published=bundle['case']['as_of'])
        doc.update(id='xbrl-0000000001-000000000126000001',
                   url='https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json')
        documents.append(doc)
    elif bundle['decision']['method_id'] in ('managed_care_distributable_equity',
            'insurance_pc_distributable_equity', 'insurance_life_distributable_equity', 'property_nav'):
        perimeter = plan['model']['perimeter']['value']
        entity = perimeter.get('entity', perimeter.get('consolidated_entity'))
        documents.append(income_document(entity, '2025-01-01', '2025-12-31',
                                         published=bundle['case']['as_of'], currency=perimeter['currency']))
    payload = prepare_and_generate(bundle, documents=documents, propose=lambda *_: deepcopy(plan),
                                   output_dir=str(tmp_path_factory.mktemp('native-earnings')))
    assert payload['preparation']['status'] == 'prepared', payload['preparation']['issues']
    assert payload['valuation_usability']['usable'], payload.get('error')
    return payload


def test_native_outputs_have_exact_entity_duration_units_and_baselines(native_model):
    packet = model_earnings_expectations(native_model)
    assert packet['status'] == 'ready', (native_model['method'], packet['reasons'])
    assert packet['expectations']
    from openpyxl import load_workbook
    from bellomberg.valuation.trade_idea_exhibits import _baselines
    wb = load_workbook(native_model['path'], read_only=True, data_only=False)
    try:
        baselines = _baselines(wb)
    finally:
        wb.close()
    for row in packet['expectations']:
        assert row['kind'] == 'engine_calculated_forecast' and row['entity'] and row['unit']
        assert row['period'] == row['period_start']+'/'+row['period_end']
        baseline = baselines[row['source_binding']['engine_path']]
        assert row['value'] == pytest.approx(baseline[0], rel=1e-12, abs=1e-12)
        assert row['cell'] == baseline[1]
        assert row['driver'] not in ('terminal_roe', 'gross_margin', 'revenue_growth')
    assert model_earnings_expectations(json.loads(json.dumps(native_model, sort_keys=True))) == packet
    if native_model['method'] == 'resources_asset_dcf':
        production = next(row for row in packet['expectations'] if row['driver'].endswith('.production'))
        assert production['unit'] in ('million_barrels', 'million_tonnes')
        assert production['primary_measure'] is None
    if native_model['method'] == 'managed_care_distributable_equity':
        expected = next(row for row in packet['expectations'] if row['driver'] == 'consolidated_gaap_net_income')
        assert expected['value'] == native_model['managed_care']['scenarios']['base']['rows'][0]['net_income']
        assert 'remaining_' not in expected['source_binding']['engine_path']


def test_whole_typed_actual_proves_native_definition_without_accounting_proxy(native_model):
    expected = model_earnings_expectations(native_model)['expectations'][0]
    observation = {key: deepcopy(expected[key]) for key in
                   ('field', 'driver', 'entity', 'period', 'unit', 'accounting_basis')}
    observation['value'] = expected['value']+1
    document = primary({'synthetic': True, 'observations': [observation]},
                       published=str(int(expected['period_end'][:4])+1)+'-02-01')
    submitted = {key: deepcopy(expected[key]) for key in ('driver', 'entity', 'period', 'unit')}
    submitted.update(value=observation['value'], source=document['url'], published_at=document['published_at'])
    proof = prove_earnings_fact({'documents': [document]}, expected, submitted, as_of=document['published_at'])
    assert proof['observation']['accounting_basis'] == expected['accounting_basis']
    wrong = deepcopy(observation)
    wrong['accounting_basis'] = 'generic_consolidated_GAAP_income'
    with pytest.raises(ValueError, match='unique'):
        prove_earnings_fact({'documents': [primary({'synthetic': True, 'observations': [wrong]},
                          published=document['published_at'])]}, expected, submitted, as_of=document['published_at'])


def test_bank_common_income_is_not_generic_net_income(native_model):
    if native_model['method'] != 'bank_residual_income':
        return
    expected = next(row for row in model_earnings_expectations(native_model)['expectations'] if row['driver'] == 'common_net_income')
    assert expected['primary_measure']['concept'] == 'NetIncomeLossAvailableToCommonStockholdersBasic'
    fact = {'taxonomy': 'us-gaap', 'concept': 'NetIncomeLoss', 'unit': 'EUR',
            'observation': {'start': expected['period_start'], 'end': expected['period_end'], 'val': 21_000_000.}}
    document = primary({'synthetic': True, 'issuer': expected['entity'], 'facts': [fact]}, published='2027-02-01')
    document.update(id='xbrl-0000000001-000000000127000001',
                    url='https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json')
    submitted = {key: deepcopy(expected[key]) for key in ('driver', 'entity', 'period', 'unit')}
    submitted.update(value=21., source=document['url'], published_at=document['published_at'])
    with pytest.raises(ValueError, match='unique'):
        prove_earnings_fact({'documents': [document]}, expected, submitted, as_of='2027-02-02')


def test_income_attribution_does_not_promote_parent_income_to_consolidated(native_model):
    method = native_model['method']
    if method not in ('managed_care_distributable_equity', 'insurance_pc_distributable_equity',
                      'insurance_life_distributable_equity', 'property_nav'):
        return
    driver = 'gaap_net_income' if method == 'property_nav' else 'consolidated_gaap_net_income'
    expected = next(row for row in model_earnings_expectations(native_model)['expectations']
                    if row['driver'] == driver)
    if method != 'managed_care_distributable_equity':
        assert expected['primary_measure'] is None
        assert expected['actual_proof_status'] == 'typed_contract_only'
        assert 'attribution' in expected['limitation']
        return
    assert expected['primary_measure'] == {'taxonomy': 'us-gaap', 'concept': 'ProfitLoss',
                                           'scope': 'consolidated'}
    assert expected['accounting_basis'] == 'consolidated_profit_including_noncontrolling_interests_before_attribution'
    document = income_document(expected['entity'], expected['period_start'], expected['period_end'],
                               published=str(int(expected['period_end'][:4])+1)+'-02-01',
                               currency=expected['unit'].split()[0])
    submitted = {key: expected[key] for key in ('driver', 'entity', 'period', 'unit')}
    submitted.update(value=30., source=document['url'], published_at=document['published_at'])
    proof = prove_earnings_fact({'documents': [document]}, expected, submitted,
                               as_of=document['published_at'])
    assert proof['primary_measure']['concept'] == 'ProfitLoss'
    assert proof['observation']['value'] == 30.
    submitted['value'] = 20.
    with pytest.raises(ValueError, match='unique'):
        prove_earnings_fact({'documents': [document]}, expected, submitted,
                            as_of=document['published_at'])
