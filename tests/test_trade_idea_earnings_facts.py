"""Actual metric proofs are exact outputs and full primary durations, offline."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from bellomberg.valuation.trade_idea_model import prepare
from bellomberg.valuation.trade_idea_earnings_facts import model_earnings_expectations, prove_earnings_fact
from test_trade_idea_economic import qualified, _operating_plan, _documents


def xbrl_document(*, start='2025-01-01', end='2025-12-31', published='2026-09-09', value=100_000_000.):
    facts = [{'taxonomy': 'us-gaap', 'concept': 'Revenues', 'unit': 'EUR',
              'observation': {'start': start, 'end': end, 'val': value}},
             {'taxonomy': 'us-gaap', 'concept': 'OperatingIncomeLoss', 'unit': 'EUR',
              'observation': {'start': start, 'end': end, 'val': 15_000_000.}}]
    text = json.dumps({'synthetic': True, 'cik': '0000000001', 'issuer': 'SYNTH-GROUP', 'facts': facts}, sort_keys=True)
    return {'id': 'xbrl-0000000001-000000000126000001',
            'url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json',
            'published_at': published, 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'metadata': {'emittente_id': 'CIK:0000000001', 'accession': '000000000126000001',
                         'scope': 'consolidated', 'synthetic_transport_fixture': True}}


@pytest.fixture
def model(tmp_path):
    document = xbrl_document()
    plan = _operating_plan()
    record = plan['model']['historical_revenue']
    for key in ('evidence_quote', 'period_quote'):
        record.pop(key)
    record.update(evidence_ids=[document['id']], quoted_value=100_000_000., quoted_unit='EUR',
                  evidence_pointer={'value': '/facts/0/observation/val', 'unit': '/facts/0/unit',
                                    'period': '/facts/0/observation/end'})
    q = qualified(tmp_path, plan=plan, documents=[*_documents(), document])
    assert q['status'] == 'qualified', q['reasons']
    payload = prepare(q, lambda *_: deepcopy(plan), tmp_path / 'model')
    assert payload['valuation_usability']['usable']
    return payload


def actual_for(expected):
    document = xbrl_document(start=expected['period_start'], end=expected['period_end'],
                             published='2027-02-01', value=102_000_000.)
    submitted = {key: deepcopy(expected[key]) for key in ('driver', 'entity', 'period', 'unit')}
    submitted.update(value=102., source=document['url'], published_at=document['published_at'])
    return document, submitted


def replace_facts(document, transform):
    parsed = json.loads(document['text'])
    transform(parsed)
    document['text'] = json.dumps(parsed, sort_keys=True)
    document['sha256'] = sha256(document['text'].encode()).hexdigest()


def test_forecasts_are_calculated_outputs_with_exact_cells_and_definitions(model):
    packet = model_earnings_expectations(model)
    assert packet['status'] == 'ready', packet['reasons']
    assert len(packet['expectations']) == 20
    metrics = {row['driver'] for row in packet['expectations']}
    assert metrics == {'revenue', 'ebit'}
    revenue = packet['expectations'][0]
    assert revenue['value'] == model['calculation_details']['scenarios']['base']['rows']['revenue'][0]
    assert revenue['cell'] and revenue['display_cell'] == "'base'!D8"
    assert revenue['primary_measure'] == {'taxonomy': 'us-gaap', 'concept': 'Revenues', 'scope': 'consolidated'}
    assert revenue['period'] == '2026-01-01/2026-12-31'
    assert packet['unavailable_metrics'][0]['driver'] == 'ebitda'
    assert model_earnings_expectations(json.loads(json.dumps(model, sort_keys=True))) == packet


def test_normalized_revenue_actual_consumes_exact_primary_duration_and_unit(model):
    expected = model_earnings_expectations(model)['expectations'][0]
    document, submitted = actual_for(expected)
    proof = prove_earnings_fact({'documents': [document]}, expected, submitted, as_of='2027-02-02')
    assert proof['status'] == 'verified'
    assert proof['observation']['value'] == 102.
    assert proof['source_pointer'] == '/facts/0'
    assert proof['duration'] == {'start': '2026-01-01', 'end': '2026-12-31'}


@pytest.mark.parametrize('fault', ['debt', 'interim', 'entity', 'currency', 'duplicate_conflicting', 'value', 'publication'])
def test_equal_units_do_not_make_wrong_or_ambiguous_primary_facts_revenue(model, fault):
    expected = model_earnings_expectations(model)['expectations'][0]
    document, submitted = actual_for(expected)
    def change(parsed):
        fact = parsed['facts'][0]
        if fault == 'debt': fact['concept'] = 'LongTermDebtCurrent'
        elif fault == 'interim': fact['observation']['start'] = '2026-10-01'
        elif fault == 'entity': parsed['issuer'] = 'OTHER-ISSUER'
        elif fault == 'currency': fact['unit'] = 'USD'
        elif fault == 'value': fact['observation']['val'] += 100_000.
        elif fault == 'duplicate_conflicting':
            second = deepcopy(fact); second['observation']['val'] += 100_000.
            parsed['facts'].append(second)
    replace_facts(document, change)
    if fault == 'publication': submitted['published_at'] = '2027-02-02'
    with pytest.raises(ValueError, match='unique|Ambiguous'):
        prove_earnings_fact({'documents': [document]}, expected, submitted, as_of='2027-02-02')


def test_input_assumption_is_not_a_new_reported_actual_expectation(model):
    expected = model_earnings_expectations(model)['expectations'][0]
    expected.update(driver='gross_margin', kind='analyst_estimate')
    document, submitted = actual_for(expected)
    with pytest.raises(ValueError, match='standalone assumptions'):
        prove_earnings_fact({'documents': [document]}, expected, submitted, as_of='2027-02-02')


def test_whole_typed_primary_contract_is_supported_without_inventing_a_concept(model):
    expected = model_earnings_expectations(model)['expectations'][0]
    observation = {key: deepcopy(expected[key]) for key in
                   ('field', 'driver', 'entity', 'period', 'unit', 'accounting_basis')}
    observation['value'] = 101.
    text = json.dumps({'synthetic': True, 'observations': [observation]}, sort_keys=True)
    document = {'id': 'whole-typed-earnings', 'url': 'https://example.org/synthetic/earnings',
                'published_at': '2027-02-01', 'text': text, 'sha256': sha256(text.encode()).hexdigest()}
    submitted = {key: deepcopy(expected[key]) for key in ('driver', 'entity', 'period', 'unit')}
    submitted.update(value=101., source=document['url'], published_at=document['published_at'])
    proof = prove_earnings_fact({'documents': [document]}, expected, submitted, as_of='2027-02-02')
    assert proof['observation']['value'] == 101.


def test_workbook_drift_blocks_frozen_outputs(model):
    Path(model['path']).write_bytes(b'changed synthetic artifact')
    packet = model_earnings_expectations(model)
    assert packet['status'] == 'blocked' and not packet['expectations']


def test_other_method_does_not_receive_a_fake_operating_earnings_path(tmp_path):
    from test_trade_idea_families import family_factories, sourced_family
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.dcf_engine import generate_valuation
    bundle, plan, documents = sourced_family(family_factories()[2])
    prepared = prepare_method_inputs(bundle, documents=documents, propose=lambda *_: deepcopy(plan))
    assert prepared['status'] == 'prepared'
    payload = generate_valuation(bundle['case']['ticker'], prepared_bundle=prepared['bundle'], output_dir=str(tmp_path))
    assert payload['valuation_usability']['usable']
    packet = model_earnings_expectations(payload)
    assert packet['status'] == 'unavailable' and not packet['expectations']
