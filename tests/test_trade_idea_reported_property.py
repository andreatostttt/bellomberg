"""Reported-NAV source gates and authoritative exhibits, explicitly synthetic."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.property_nav_requirements import REPORTED_SCHEMA
from bellomberg.valuation.trade_idea_model import qualification, prepare, model_exhibits
from test_property_reported_nav import synthetic_model, synthetic_scenarios, synthetic_bundle, DAY, ON
from test_trade_idea_economic import synthetic_observed_quote_info


def synthetic_qualification(tmp_path, *, fault=None, accepted_currency='EUR', confirmed_name=None):
    model, scenarios = synthetic_model(), synthetic_scenarios()
    if fault == 'missing_liability':
        model['balance_sheet']['liabilities'].pop('current_accruals')
    elif fault == 'unclosed_assets':
        model['balance_sheet']['assets']['cash'] += 1
    elif fault == 'wrong_nta':
        model['epra_bridge']['reported_nav'] += 1
    elif fault == 'diluted_denominator':
        model['claims']['epra_diluted_shares_m'] += 1
    elif fault == 'breached_covenant':
        model['restrictions']['covenants']['interest_coverage']['observed'] = 1.
    elif fault == 'future_compliance':
        model['restrictions']['compliance_date'] = '2026-12-31'
    entity = model['perimeter']['entity']
    confirmed_name = confirmed_name or entity
    observations, plan = [], {'model': {}, 'scenarios': {s: {} for s in scenarios},
        'scenario_rationale': {s: 'Declared synthetic property value sensitivity, never a primary forecast' for s in scenarios}}
    for scope, values in [('model', model), *scenarios.items()]:
        for driver, value in values.items():
            historical = scope == 'model' and driver not in ('perimeter', 'calendar', 'policy')
            item = {'value': deepcopy(value), 'kind': 'historical' if historical else 'analyst_estimate',
                'evidence_ids': ['synthetic-property-primary'], 'valid_until': DAY,
                'valid_until_basis': {'policy': 'same_day', 'as_of': DAY},
                'rationale': 'Declared synthetic source observation or prospective sensitivity'}
            if historical:
                field, unit, basis, *_ = REPORTED_SCHEMA[driver]
                observations.append({'field': field, 'driver': driver, 'value': deepcopy(value),
                    'unit': unit, 'accounting_basis': basis, 'entity': entity, 'period': ON})
                item['record_pointer'] = '/observations/'+str(len(observations)-1)
            (plan['model'] if scope == 'model' else plan['scenarios'][scope])[driver] = item
    text = json.dumps({'synthetic': True, 'observations': observations}, sort_keys=True)
    document = {'id': 'synthetic-property-primary', 'url': 'https://example.org/synthetic/property-primary',
        'published_at': DAY, 'text': text, 'sha256': sha256(text.encode()).hexdigest()}
    profile = {'status': 'ok', 'source_id': document['url'], 'as_of': DAY,
        'retrieved_at': DAY+'T12:00:00+00:00', 'data': {
        'info': {'symbol': entity, 'longName': confirmed_name, 'currency': accepted_currency, 'financialCurrency': 'EUR', 'exchange': 'SYNTHETIC'},
        'evidence': [{'field': field, 'value': value, 'source_id': document['url'], 'as_of': DAY}
                     for field, value in [('instrument', 'equity'), ('business_model', 'property_owner')]]}}
    identity = {'status': 'confirmed', 'ticker': entity, 'name': confirmed_name, 'exchange': 'SYNTHETIC', 'currency': accepted_currency}
    profile['data']['info'].update(synthetic_observed_quote_info(entity, DAY,
        currency=accepted_currency, exchange='SYNTHETIC'))
    return qualification(entity, identity, DAY, archive_root=tmp_path,
        providers={'profile': lambda *_a, **_k: deepcopy(profile)},
        source_report={'documents': [document], 'source_plan': plan})


def test_reported_nav_synthetic_free_gate_has_positive_economic_control(tmp_path):
    result = synthetic_qualification(tmp_path)
    assert result['status'] == 'qualified', result['reasons']


def test_reported_nav_profile_and_confirmed_alias_cannot_replace_a_primary_issuer(tmp_path):
    entity = synthetic_model()['perimeter']['entity']
    result = synthetic_qualification(tmp_path, confirmed_name=entity+' SA')
    assert result['status'] == 'blocked'
    assert any('legal-name and registration binding' in reason for reason in result['reasons'])
    calls = []
    with pytest.raises(ValueError, match='Fonti non qualificate'):
        prepare(result, lambda *_: calls.append('forbidden-paid'), tmp_path/'model')
    assert calls == []


def test_reported_nav_primary_cannot_replace_the_confirmed_quotation_currency(tmp_path):
    result = synthetic_qualification(tmp_path, accepted_currency='USD')
    assert result['status'] == 'blocked'
    assert any('confirmed instrument' in reason for reason in result['reasons'])
    with pytest.raises(ValueError, match='Fonti non qualificate'):
        prepare(result, lambda *_: pytest.fail('paid callback after quotation identity mismatch'), tmp_path/'model')


@pytest.mark.parametrize('fault', ['missing_liability', 'unclosed_assets', 'wrong_nta',
    'diluted_denominator', 'breached_covenant', 'future_compliance'])
def test_reported_nav_false_economic_bridge_blocks_before_paid(tmp_path, fault):
    result = synthetic_qualification(tmp_path, fault=fault)
    assert result['status'] == 'blocked'
    calls = []
    with pytest.raises(ValueError, match='Fonti non qualificate'):
        prepare(result, lambda *_: calls.append('forbidden'), tmp_path/'model')
    assert calls == []


def test_reported_nav_exhibits_have_exact_bridge_reconciliations_and_sensitivities(tmp_path):
    from bellomberg.valuation.dcf_engine import generate_valuation
    from bellomberg.valuation.trade_idea_earnings_facts import model_earnings_expectations
    payload = generate_valuation('SYNTH-REPORTED-PROP', prepared_bundle=synthetic_bundle(), output_dir=str(tmp_path))
    packet = model_exhibits(payload)
    assert packet['status'] == 'complete', packet['reasons']
    exhibits = {row['id']: row for row in packet['exhibits']}
    assert set(exhibits) == {'scenarios', 'valuation_bridge', 'drivers', 'sensitivity'}
    assert len(exhibits['drivers']['rows']) == 8
    assert [row[-1] for row in exhibits['sensitivity']['rows']] == pytest.approx([7.9, 9.2, 10.4])
    assert all(refs[-1] for refs in exhibits['sensitivity']['cell_refs'])
    assert exhibits['sensitivity']['column_units'][-1] == 'per share'
    assert model_exhibits(json.loads(json.dumps(payload, sort_keys=True))) == packet
    earnings = model_earnings_expectations(payload)
    assert earnings['status'] == 'unavailable'
    assert earnings['expectations'] == []
    assert 'no corporate earnings forecast' in earnings['reasons'][0]
