"""Review proof: historical facts remain frozen while ordinary comments may evolve."""
from copy import deepcopy

import pytest

from bellomberg.valuation import trade_idea_model as model
from test_trade_idea_historical_admission import preparation_case


def test_explicit_plan_accepts_distinct_scenario_comment_on_same_frozen_net_debt(tmp_path):
    qualification, plan = preparation_case(tmp_path)
    plan['scenarios']['bull']['net_debt']['rationale'] += ' This opening observation also anchors the bull case.'
    payload = model.build_from_plan(qualification, plan, tmp_path / 'model')
    assert payload['valuation_usability']['usable'] is True
    assert payload['preparation']['proposal']['plan'] == plan


@pytest.mark.parametrize('field,value', [('value', 9), ('kind', 'analyst_estimate'),
    ('evidence_ids', ['other-document']), ('quoted_unit', 'EUR million'),
    ('period_quote', '2020-01-01'), ('quoted_value', 10),
    ('evidence_pointer', {'value': '/different'}), ('valid_until', '2099-12-31')])
def test_historical_identity_reports_mutated_exact_field(field, value):
    before = {'value': 1, 'kind': 'historical', 'evidence_ids': ['verified-source'],
              'rationale': 'Plain analyst comment', 'quoted_unit': 'USD million',
              'period_quote': '2025-12-31', 'quoted_value': 1,
              'evidence_pointer': {'value': '/facts/0/val'}, 'valid_until': '2026-09-01'}
    after = deepcopy(before)
    after[field] = value
    assert any(path == field or path.startswith(field + '[') or path.startswith(field + '.')
               for path in model._historical_driver_changes(before, after))


def test_historical_identity_preserves_currency_entity_and_nested_nwc_proof():
    before = {'value': {'entity': 'Original issuer', 'currency': 'USD'},
              'rationale': 'Plain commentary',
              'calculation': {'classifications': [{'treatment': 'operating_nwc', 'evidence_quote': 'original'}]}}
    for field in ('currency', 'entity'):
        after = deepcopy(before)
        after['value'][field] = 'changed'
        assert 'value.' + field in model._historical_driver_changes(before, after)
    after = deepcopy(before)
    after['calculation']['classifications'][0]['evidence_quote'] = 'forged'
    assert 'calculation.classifications[0].evidence_quote' in model._historical_driver_changes(before, after)


@pytest.mark.parametrize('proof', [
    'NWC coverage: {"classification_sha256":"original"}\nComment',
    'SOURCE POINTER NORMALIZED: [{"old":"/one","new":"/two"}]\nComment',
    'BALANCE BRIDGE CLASSIFICATION: verified component arithmetic.\nComment',
    'Financial proof {"currency":"USD","value":1}',
    'Opening NWC equals current assets 25 minus operating liabilities 10.',
    'Source details https://issuer.example/report with a retained calculation'])
def test_embedded_proof_is_never_discarded_as_ordinary_comment(proof):
    assert 'rationale' in model._historical_driver_changes(
        {'value': 1, 'rationale': proof}, {'value': 1, 'rationale': 'Replacement narrative'})


@pytest.mark.parametrize('fault', ['currency', 'entity', 'period', 'unit', 'provenance'])
def test_real_compiler_rejects_changed_historical_identity_without_workbook(tmp_path, fault):
    qualification, plan = preparation_case(tmp_path)
    if fault in ('currency', 'entity'):
        plan['model']['perimeter']['value'][fault] = 'USD' if fault == 'currency' else 'Another issuer'
    elif fault == 'period':
        plan['model']['calendar']['value']['valuation_date'] = '2024-12-31'
    elif fault == 'unit':
        plan['scenarios']['bull']['net_debt']['quoted_unit'] = 'USD billion'
    else:
        plan['scenarios']['bull']['net_debt']['evidence_ids'] = ['forged-source']
    with pytest.raises((ValueError, KeyError)):
        model.build_from_plan(qualification, plan, tmp_path / 'invalid')
    assert not list((tmp_path / 'invalid').glob('*.xlsx'))
