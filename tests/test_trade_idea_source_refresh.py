"""Source replacements prove observations and never silently renew a forecast."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from bellomberg.valuation.trade_idea_model import prepare, model_exhibits
from bellomberg.valuation.trade_idea_source_refresh import refresh_model_sources, verify_source_observation
from test_trade_idea_economic import qualified, _operating_plan, DAY


def model_and_source(tmp_path, *, driver='opening_nwc', value=None):
    before = prepare(qualified(tmp_path), lambda *_: deepcopy(_operating_plan()), tmp_path / 'before')
    record = next(row for row in before['acquisition_snapshot']['case']['records'] if row['driver'] == driver)
    observation = {key: deepcopy(record[key]) for key in
                   ('field', 'driver', 'value', 'entity', 'period', 'unit', 'accounting_basis')}
    observation['value'] = record['value'] + 1. if value is None else value
    text = json.dumps({'synthetic': True, 'observations': [observation]}, sort_keys=True)
    document = {'id': 'synthetic-source-correction', 'url': 'https://example.org/synthetic/issuer-correction.json',
                'published_at': DAY, 'text': text, 'sha256': sha256(text.encode()).hexdigest()}
    update = {'driver': record['driver'], 'scenario': record['scenario'], 'entity': record['entity'],
              'document_id': document['id'], 'record_pointer': '/observations/0'}
    return before, document, update, observation


def test_exact_source_observation_and_value_are_proved(tmp_path):
    _before, document, _update, observation = model_and_source(tmp_path)
    expected = {key: value for key, value in observation.items() if key != 'value'}
    proof = verify_source_observation(document, '/observations/0', expected=expected, as_of=DAY)
    assert proof['status'] == 'verified', proof['reasons']
    assert proof['observation'] == observation
    wrong = dict(expected, value=observation['value'] + 1.)
    assert verify_source_observation(document, '/observations/0', expected=wrong, as_of=DAY)['status'] == 'blocked'


def test_preview_and_apply_consume_source_and_keep_original_immutable(tmp_path):
    before, document, update, observation = model_and_source(tmp_path)
    original = Path(before['path']).read_bytes()
    report = {'documents': [document], 'acquisition_receipt': {'scope': 'synthetic server transport fixture'}}
    preview = refresh_model_sources(before, report, [update])
    assert preview['status'] == 'ready', preview['reasons']
    assert not (tmp_path / 'after').exists()
    applied = refresh_model_sources(before, report, [update], apply=True, output_dir=tmp_path / 'after')
    assert applied['status'] == 'applied', applied['reasons']
    after = applied['payload']
    assert after['valuation_usability']['usable']
    assert after['generation_id'] != before['generation_id'] and after['snapshot_id'] != before['snapshot_id']
    assert after['valuation_date'] == before['valuation_date']
    assert after['fair_value_base'] != before['fair_value_base']
    assert model_exhibits(after)['status'] == 'complete'
    record = next(row for row in after['acquisition_snapshot']['case']['records'] if row['driver'] == update['driver'])
    assert record['source_locator'] == document['id'] and record['value'] == observation['value']
    assert Path(before['path']).read_bytes() == original
    persisted = json.loads(Path(after['path']).with_suffix('.payload.json').read_text(encoding='utf-8'))
    assert persisted['preparation']['provenance']['source_refresh']['previous_generation_id'] == before['generation_id']


def test_automatic_mapping_requires_one_exact_typed_source_identity(tmp_path):
    before, document, _update, _observation = model_and_source(tmp_path)
    assert refresh_model_sources(before, {'documents': [document]})['status'] == 'ready'
    second = deepcopy(document); second['id'] = 'second-conflicting-identity'
    assert refresh_model_sources(before, {'documents': [document, second]})['status'] == 'blocked'


def test_new_period_and_duplicate_source_identity_are_not_rollforward(tmp_path):
    before, document, update, observation = model_and_source(tmp_path)
    observation['period'] = '2026-09-11'
    document['text'] = json.dumps({'observations': [observation]})
    document['sha256'] = sha256(document['text'].encode()).hexdigest()
    assert refresh_model_sources(before, {'documents': [document]}, [update])['status'] == 'blocked'
    observation['period'] = before['acquisition_snapshot']['case']['records'][0]['period']
    document['text'] = json.dumps({'observations': [observation, observation]})
    document['sha256'] = sha256(document['text'].encode()).hexdigest()
    proof = verify_source_observation(document, '/observations/0', expected={k: v for k, v in observation.items() if k != 'value'}, as_of=DAY)
    assert proof['status'] == 'blocked' and any('Ambiguous' in reason for reason in proof['reasons'])


def test_later_cutoff_does_not_renew_unchanged_estimates(tmp_path):
    before, document, update, _observation = model_and_source(tmp_path)
    result = refresh_model_sources(before, {'documents': [document]}, [update], as_of='2026-09-11')
    assert result['status'] == 'blocked'
    assert any('valid_until' in reason or 'scadut' in reason for reason in result['reasons'])


def test_changed_revenue_does_not_silently_rebase_absolute_forecast_drivers(tmp_path):
    before, document, update, _observation = model_and_source(tmp_path, driver='historical_revenue', value=1500.)
    result = refresh_model_sources(before, {'documents': [document]}, [update], apply=True, output_dir=tmp_path / 'conflict')
    assert result['status'] == 'blocked'
    assert any('unusable' in reason for reason in result['reasons'])


def test_source_refresh_preserves_current_pm_estimates_instead_of_reverting_old_plan(tmp_path):
    from bellomberg.valuation.trade_idea_model import revise
    before, document, update, _observation = model_and_source(tmp_path)
    current_records = deepcopy(before['acquisition_snapshot']['case']['records'])
    for record in current_records:
        if record['driver'] == 'wacc':
            assert record['kind'] == 'analyst_estimate'
            record['value'] = .12
            record['rationale'] = 'Explicit synthetic PM discount-rate revision'
    current = revise(before, {'method_records': current_records, 'rationale': 'Synthetic current model revision',
        'evidence_ids': ['annual-1']}, output_dir=tmp_path / 'current')
    assert current['valuation_usability']['usable']
    assert current['preparation']['proposal']['plan']['scenarios']['base']['wacc']['value'] == .10
    applied = refresh_model_sources(current, {'documents': [document]}, [update], apply=True, output_dir=tmp_path / 'refreshed')
    assert applied['status'] == 'applied', applied['reasons']
    after = applied['payload']
    assert after['preparation']['proposal']['plan']['scenarios']['base']['wacc']['value'] == .12
    assert all(row['value'] == .12 for row in after['acquisition_snapshot']['case']['records'] if row['driver'] == 'wacc')
    assert all(row['value'] == 0. and row['kind'] == 'historical'
               for row in after['acquisition_snapshot']['case']['records'] if row['driver'] == 'net_debt')
    assert applied['preserved_analyst_revisions'] == ['base.wacc', 'bear.wacc', 'bull.wacc']
    assert after['fair_value_base'] > current['fair_value_base']


@pytest.mark.parametrize('extension', [{'source_plan': {}}, {'preparation_ready': True}, {'qualification': 'qualified'}])
def test_supplied_plan_or_readiness_is_not_an_acquisition_attestation(tmp_path, extension):
    before, document, update, _observation = model_and_source(tmp_path)
    result = refresh_model_sources(before, {'documents': [document], **extension}, [update])
    assert result['status'] == 'blocked'
    assert any('attest' in reason for reason in result['reasons'])
