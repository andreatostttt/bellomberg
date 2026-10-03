"""A disclosed dilution estimate stays distinct through wire, compiler and Excel."""
from copy import deepcopy
from hashlib import sha256
import json

from jsonschema import validate
from openpyxl import load_workbook

from bellomberg.valuation.operating_adapter import SCHEMA
from bellomberg.valuation.preparation_ai import BudgetedProposer, response_format


def test_share_wire_exception_is_local_and_requires_complete_operand_proof(tmp_path):
    contract = {'method_id': 'operating_fcff', 'schema': {'shares': SCHEMA['shares']},
                'preparation_stage': {'scope': 'model', 'drivers': ['shares']}}
    ordinary = response_format(contract)
    kinds = ordinary['json_schema']['schema']['properties']['drivers']['properties']['shares']['anyOf']
    assert kinds[0]['properties']['kind']['enum'] == ['historical']
    assert len(kinds) == 2
    changed = deepcopy(contract)
    changed['preparation_stage']['diluted_denominator_policy'] = {'basis': 'explicit proxy'}
    schema = response_format(changed)['json_schema']['schema']
    branches = schema['properties']['drivers']['properties']['shares']['anyOf']
    estimate = next(b for b in branches if b.get('properties', {}).get('kind', {}).get('enum') == ['analyst_estimate'])
    assert 'dilution_estimate' in estimate['required']
    assert set(estimate['properties']['dilution_estimate']['properties']['facts']['required']) == {
        'outstanding', 'weighted_basic', 'weighted_diluted'}
    validate({'drivers': {'shares': None}, 'rationale': 'Sources do not support this proxy'}, schema)
    proposer = BudgetedProposer(tmp_path / 'wire.sqlite', authorized_usd=0,
        model='synthetic-test-model', max_tokens=10, thinking={'type': 'disabled'})
    old = proposer._request({}, contract)
    new = proposer._request({}, changed)
    assert 'Explicit FCFF shares exception' not in old['system']
    assert 'Explicit FCFF shares exception' in new['system']
    assert old['model'] == new['model'] and old['max_tokens'] == new['max_tokens']


def test_estimated_share_denominator_has_visible_excel_label_and_provenance(tmp_path):
    from test_documented_cashflow_bridge import _bundle
    from bellomberg.valuation.dcf_engine import generate_valuation
    from bellomberg.valuation.documented_inputs import build_documented_workbook
    payload = generate_valuation('SYNTH-EXT', prepared_bundle=_bundle(4), output_dir=str(tmp_path))
    assert payload['valuation_usability']['usable'], payload
    proof = next(r for r in payload['analytical_quality']['rows'] if r['driver'] == 'shares')['evidence']
    proof.update(kind='analyst_estimate', rationale='PROXY/STIMA DILUZIONE: synthetic explicit ratio; averaging risk.')
    assert proof['kind'] == 'analyst_estimate'
    assert 'PROXY/STIMA DILUZIONE' in proof['rationale']
    with_workbook = load_workbook(build_documented_workbook(payload, tmp_path))
    try:
        for scenario in ('bear', 'base', 'bull'):
            assert 'proxy' in with_workbook[scenario]['B53'].value.lower()
            assert with_workbook[scenario]['D53'].data_type == 'f'
    finally:
        with_workbook.close()


def test_verified_dilution_proxy_compiles_through_existing_fcff_engine(tmp_path):
    from test_diluted_share_estimate import _case, OPENING
    from test_input_preparation import _bundle, _documents, _operating_plan, DAY
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.dcf_engine import generate_valuation
    item, catalog, filings = _case()
    item.update(valid_until=DAY, valid_until_basis={'policy': 'same_day', 'as_of': DAY})
    plan = json.loads(json.dumps(_operating_plan()).replace('2025-12-31', OPENING))
    plan = json.loads(json.dumps(plan).replace('Price: EUR 10 per share', 'Price: EUR 1 per share'))
    plan['model']['quotation']['value']['price'] = 1.
    plan['model']['quotation']['facts']['price']['quoted_value'] = 1.
    plan['model']['perimeter']['value']['entity'] = 'SYNTHETIC INC'
    plan['model']['perimeter']['value']['share_class'] = 'Common Stock'
    plan['model']['quotation']['value']['share_class'] = 'Common Stock'
    plan['model']['calendar']['value']['periods'] = [
        {'start': f'{year}-10-01', 'end': f'{year+1}-09-30'} for year in range(2025, 2035)]
    plan['model']['shares'] = item
    docs = _documents()
    docs[0]['text'] = docs[0]['text'].replace('2025-12-31', OPENING).replace('Price: EUR 10 per share', 'Price: EUR 1 per share')
    docs[0]['sha256'] = sha256(docs[0]['text'].encode()).hexdigest()
    report = {'selection': {'selected_document_id': filings[0]['id'],
                           'selected_document_ids': [filings[0]['id']]}}
    result = prepare_method_inputs(_bundle(), documents=docs + list(catalog.values()),
                                   propose=lambda *_: plan, source_report=report)
    assert result['status'] == 'prepared', result['issues']
    record = next(r for r in result['proposal']['method_records'] if r['driver'] == 'shares')
    assert record['kind'] == 'analyst_estimate'
    assert record['rationale'].startswith('PROXY/STIMA DILUZIONE')
    payload = generate_valuation('SYNTH-EXT', prepared_bundle=result['bundle'], output_dir=str(tmp_path))
    assert payload['valuation_usability']['usable'], payload['valuation_usability']
    for scenario in ('bear', 'base', 'bull'):
        assert payload['calculation_details']['scenarios'][scenario]['valuation_bridge']['diluted_shares'] == item['value']
