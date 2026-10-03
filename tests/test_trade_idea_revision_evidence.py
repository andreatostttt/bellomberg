"""Trade Idea reviews preserve observed facts and allow explicit analyst views."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.trade_idea_model import (
    qualification, prepare, revise, candidate_model_usability, source_fingerprint,
)
from test_trade_idea_economic import (
    qualified, _operating_plan, providers_for, IDENTITY, DAY,
)


@pytest.fixture(scope='module')
def observed_model(tmp_path_factory):
    folder = tmp_path_factory.mktemp('review-observed-model')
    qualified_sources = qualified(folder)
    model = prepare(qualified_sources, lambda *_: deepcopy(_operating_plan()), folder / 'model')
    assert candidate_model_usability(model)['usable']
    return qualified_sources, model


def changes(records):
    return {'method_records': records, 'rationale': 'Explicit synthetic committee review',
            'evidence_ids': ['annual-1']}


@pytest.mark.parametrize('fault', [
    'value', 'nested_value', 'kind_estimate', 'kind_guidance', 'source_id',
    'source_locator', 'extra_evidence_ids', 'extra_pointer', 'as_of', 'valid_until',
    'unit', 'entity', 'period', 'scenario', 'field', 'accounting_basis',
    'provider', 'delete', 'duplicate', 'add',
])
def test_raw_review_cannot_change_add_delete_or_rebind_historical_records(observed_model, tmp_path, fault):
    sources, before = observed_model
    records = deepcopy(before['acquisition_snapshot']['case']['records'])
    record = next(row for row in records if row['driver'] == ('quotation' if fault == 'nested_value' else 'net_debt'))
    if fault == 'value': record['value'] = 30.
    elif fault == 'nested_value': record['value']['price'] += 1.
    elif fault == 'kind_estimate': record['kind'] = 'analyst_estimate'
    elif fault == 'kind_guidance': record['kind'] = 'company_guidance'
    elif fault == 'source_id': record['source_id'] = 'https://example.org/issuer/different-proof'
    elif fault == 'source_locator': record['source_locator'] = 'annual-1'
    elif fault == 'extra_evidence_ids': record['evidence_ids'] = ['annual-1']
    elif fault == 'extra_pointer': record['record_pointer'] = '/facts/99/value'
    elif fault == 'as_of': record['as_of'] = DAY
    elif fault == 'valid_until': record['valid_until'] = '2026-09-11'
    elif fault == 'unit': record['unit'] = 'USD million'
    elif fault == 'entity': record['entity'] = 'SYNTH-OTHER'
    elif fault == 'period': record['period'] = '2026-09-11'
    elif fault == 'scenario': record['scenario'] = 'model'
    elif fault == 'field': record['field'] = 'operating_assets'
    elif fault == 'accounting_basis': record['accounting_basis'] = 'different_basis'
    elif fault == 'provider': record['provider'] = 'filings'
    elif fault == 'delete': records.remove(record)
    elif fault == 'duplicate': records.append(deepcopy(record))
    elif fault == 'add':
        added = deepcopy(record)
        added['driver'] = 'unproved_new_fact'
        records.append(added)
    with pytest.raises(ValueError, match='Fatti storici e company guidance immutabili'):
        revise(before, changes(records), qualification=sources, output_dir=tmp_path / 'forbidden')
    assert not (tmp_path / 'forbidden').exists()


def test_fact_protection_survives_a_resealed_claim_of_qualification(observed_model, tmp_path):
    sources, before = observed_model
    claimed = deepcopy(sources)
    claimed['source_report']['documents'].append({'id': 'unproved-new-proof',
        'url': 'https://example.org/claimed-new-fact', 'sha256': '0' * 64,
        'published_at': DAY, 'text': '{"claim":30}'})
    claimed['fingerprint'] = source_fingerprint(claimed)
    records = deepcopy(before['acquisition_snapshot']['case']['records'])
    next(row for row in records if row['driver'] == 'net_debt')['value'] = 30.
    with pytest.raises(ValueError, match='immutabili'):
        revise(before, changes(records), qualification=claimed, output_dir=tmp_path / 'forbidden')


def test_valid_reordering_and_analyst_estimates_remain_usable(observed_model, tmp_path):
    sources, before = observed_model
    records = list(reversed(deepcopy(before['acquisition_snapshot']['case']['records'])))
    for row in records:
        if row['driver'] == 'wacc':
            assert row['kind'] == 'analyst_estimate'
            row['value'] = .12
            row['rationale'] = 'Explicit synthetic higher discount rate'
    after = revise(before, changes(records), qualification=sources, output_dir=tmp_path / 'after')
    assert candidate_model_usability(after)['usable']
    assert after['generation_id'] != before['generation_id']
    assert all(row['value'] == .12 for row in after['acquisition_snapshot']['case']['records'] if row['driver'] == 'wacc')
    assert all(row['value'] == 0. for row in after['acquisition_snapshot']['case']['records'] if row['driver'] == 'net_debt')


@pytest.mark.parametrize('partial', [False, True])
def test_paid_historical_requests_stop_before_the_proposer(observed_model, tmp_path, partial):
    sources, before = observed_model
    records = deepcopy(before['acquisition_snapshot']['case']['records'])
    historical = next(row for row in records if row['driver'] == 'net_debt')
    historical['value'] = 30.
    if partial:
        records = [historical]
    calls = []
    with pytest.raises(ValueError, match='immutabili'):
        revise(before, changes(records), qualification=sources,
            propose=lambda *_: calls.append('forbidden paid request'), output_dir=tmp_path / 'forbidden')
    assert calls == []


@pytest.mark.parametrize('rekey', ['entity', 'field', 'scenario'])
def test_paid_request_cannot_disguise_a_fact_as_a_new_analyst_slot(observed_model, tmp_path, rekey):
    sources, before = observed_model
    requested = deepcopy(next(row for row in before['acquisition_snapshot']['case']['records'] if row['driver'] == 'net_debt'))
    requested.update(kind='analyst_estimate', value=30.)
    requested[rekey] = {'entity': 'OTHER-GROUP', 'field': 'operating_assets', 'scenario': 'model'}[rekey]
    calls = []
    def proposer(*_):
        calls.append('forbidden')
        return deepcopy(_operating_plan())
    with pytest.raises(ValueError):
        revise(before, changes([requested]), qualification=sources, propose=proposer, output_dir=tmp_path / 'forbidden')
    assert calls == []


def test_conflicting_paid_analyst_replacements_stop_before_the_proposer(observed_model, tmp_path):
    sources, before = observed_model
    row = next(row for row in before['acquisition_snapshot']['case']['records']
               if row['driver'] == 'wacc' and row['scenario'] == 'base')
    requested = [deepcopy(row), deepcopy(row)]
    requested[0]['value'], requested[1]['value'] = .11, .12
    calls = []
    def proposer(*_):
        calls.append('forbidden')
        plan = deepcopy(_operating_plan())
        plan['scenarios']['base']['wacc']['value'] = .12
        return plan
    with pytest.raises(ValueError, match='una sola sostituzione'):
        revise(before, changes(requested), qualification=sources, propose=proposer, output_dir=tmp_path / 'forbidden')
    assert calls == []
    assert not (tmp_path / 'forbidden').exists()


@pytest.mark.parametrize('with_records', [False, True])
def test_paid_output_cannot_change_facts_even_when_no_factual_input_was_requested(observed_model, tmp_path, with_records):
    sources, before = observed_model
    request = {'rationale': 'Explicit forecast review only', 'evidence_ids': ['annual-1']}
    if with_records:
        request['method_records'] = [deepcopy(row) for row in before['acquisition_snapshot']['case']['records'] if row['driver'] == 'wacc']
    calls = []
    def proposer(*_):
        calls.append('synthetic proposer')
        plan = deepcopy(_operating_plan())
        for scenario in plan['scenarios'].values():
            # Deliberately invalid source proposal. Even incomplete output is
            # checked; it cannot turn into a usable altered fact later.
            scenario['net_debt']['value'] = 30.
        return plan
    with pytest.raises(ValueError, match='immutabili'):
        revise(before, request, qualification=sources, propose=proposer, output_dir=tmp_path / 'forbidden')
    assert calls == ['synthetic proposer']


def test_blocked_qualification_stops_a_paid_analyst_review(observed_model, tmp_path):
    sources, before = observed_model
    blocked = deepcopy(sources)
    blocked['status'] = 'blocked'
    records = [deepcopy(row) for row in before['acquisition_snapshot']['case']['records'] if row['driver'] == 'wacc']
    calls = []
    with pytest.raises(ValueError, match='Fonti della revisione non qualificate'):
        revise(before, changes(records), qualification=blocked,
            propose=lambda *_: calls.append('forbidden'), output_dir=tmp_path / 'forbidden')
    assert calls == []


@pytest.fixture(scope='module')
def guidance_model(observed_model, tmp_path_factory):
    sources, before = observed_model
    folder = tmp_path_factory.mktemp('review-guidance-model')
    sample = next(row for row in before['acquisition_snapshot']['case']['records'] if row['driver'] == 'wacc')
    observed = {key: deepcopy(sample[key]) for key in ('field', 'driver', 'value', 'entity', 'period', 'unit', 'accounting_basis')}
    text = json.dumps({'synthetic': True, 'observations': [observed]}, sort_keys=True)
    document = {'id': 'synthetic-published-wacc-guidance', 'url': 'https://example.org/issuer/fictional-guidance',
                'text': text, 'sha256': sha256(text.encode()).hexdigest(), 'published_at': '2026-09-09'}
    plan = deepcopy(_operating_plan())
    for scenario in plan['scenarios'].values():
        scenario['wacc'].update(kind='company_guidance', evidence_ids=[document['id']],
            record_pointer='/observations/0', rationale='Explicit fictional published management guidance')
    report = {'documents': deepcopy(sources['source_report']['documents']) + [document], 'source_plan': plan}
    qualified_sources = qualification(IDENTITY['ticker'], IDENTITY, DAY,
        archive_root=folder, providers=providers_for(), source_report=report)
    assert qualified_sources['status'] == 'qualified', qualified_sources['reasons']
    model = prepare(qualified_sources, lambda *_: deepcopy(plan), folder / 'model')
    assert candidate_model_usability(model)['usable'], model.get('preparation')
    assert any(row['kind'] == 'company_guidance' for row in model['acquisition_snapshot']['case']['records'])
    return qualified_sources, model


@pytest.mark.parametrize('fault', ['value', 'relabel', 'delete', 'duplicate', 'nested_proof'])
def test_published_guidance_is_not_a_mutable_analyst_view(guidance_model, tmp_path, fault):
    sources, before = guidance_model
    records = deepcopy(before['acquisition_snapshot']['case']['records'])
    guidance = next(row for row in records if row['kind'] == 'company_guidance')
    if fault == 'value': guidance['value'] = .12
    elif fault == 'relabel': guidance['kind'] = 'analyst_estimate'
    elif fault == 'delete': records.remove(guidance)
    elif fault == 'duplicate': records.append(deepcopy(guidance))
    elif fault == 'nested_proof': guidance['evidence_pointer'] = {'value': '/observations/99/value'}
    with pytest.raises(ValueError, match='immutabili'):
        revise(before, changes(records), qualification=sources, output_dir=tmp_path / 'forbidden')
