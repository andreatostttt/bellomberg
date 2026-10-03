"""Fundamentals' explicit plans compile freely through the existing common engine."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from bellomberg.valuation import trade_idea_model as model
from test_trade_idea_economic import qualified, _operating_plan
from test_trade_idea_historical_admission import preparation_case


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('Explicit author plan must not invoke any provider or AI proposer')
    monkeypatch.setattr('bellomberg.valuation.preparation_ai.BudgetedProposer.__call__', forbidden)
    monkeypatch.setattr('bellomberg.core.llm_client.OpenRouterClient', forbidden)


def build(qualification, plan, output_dir, **kwargs):
    assert hasattr(model, 'build_from_plan'), 'Approved explicit author-plan compiler is missing'
    return model.build_from_plan(qualification, plan, output_dir, **kwargs)


def values(plan):
    return {'model': {name: deepcopy(item['value']) for name, item in plan['model'].items()},
            'scenarios': {scope: {name: deepcopy(item['value']) for name, item in drivers.items()}
                          for scope, drivers in plan['scenarios'].items()}}


def assert_new_workbook(payload, plan):
    assert model.candidate_model_usability(payload)['usable'] is True, payload.get('error')
    assert payload['preparation']['status'] == 'prepared'
    assert payload['generation_id'] and payload['workbook_sha256']
    path = Path(payload['path'])
    assert path.is_file() and path.suffix == '.xlsx'
    assert sha256(path.read_bytes()).hexdigest() == payload['workbook_sha256']
    compiled = payload['preparation']['proposal']['plan']
    assert values(compiled) == values(plan)
    assert payload['input_consumption']['status'] == 'complete'
    sidecar = json.loads(path.with_suffix('.payload.json').read_text(encoding='utf-8'))
    assert sidecar['generation_id'] == payload['generation_id']
    assert sidecar['preparation'] == payload['preparation']


def test_explicit_fcff_plan_creates_new_exact_workbook_without_provider(tmp_path):
    qualification = qualified(tmp_path)
    plan = _operating_plan()
    plan['scenarios']['base']['capex_pct']['value'] = [.057] * 10
    before = deepcopy((qualification, plan))
    context = {'consultations': [{'desk': 'macro', 'answer': 'Explicit synthetic rate regime'}]}
    payload = build(qualification, plan, tmp_path/'model', author_context=context)
    assert_new_workbook(payload, plan)
    assert (qualification, plan) == before
    assert payload['preparation']['proposal']['plan']['scenario_rationale'] == plan['scenario_rationale']
    consumed_context = payload['preparation']['review_basis']['dossier']['preparation_context']
    assert consumed_context['consultations'] == context['consultations']
    assert consumed_context['model_author']['actor'] == 'fundamentals'
    assert consumed_context['model_author']['approval_status'] == 'not_PM_approved'
    assert payload['sanity']['policy'] == 'documented_operating_base_v1'


def test_pending_history_verifies_checkpoint_before_consuming_explicit_forecasts(tmp_path):
    qualification, plan = preparation_case(tmp_path)
    before = deepcopy((qualification, plan))
    payload = build(qualification, plan, tmp_path/'model')
    assert_new_workbook(payload, plan)
    assert payload['historical_preparation']['status'] == 'historical_qualified'
    assert payload['historical_preparation']['admission_fingerprint'] == qualification['fingerprint']
    assert json.loads((tmp_path/'model'/'historical-preparation.json').read_text())['status'] == 'historical_qualified'
    assert (qualification, plan) == before


def test_same_plan_always_generates_a_new_excel_instead_of_returning_old_artifact(tmp_path):
    qualification = qualified(tmp_path)
    plan = _operating_plan()
    first = build(qualification, plan, tmp_path/'first')
    second = build(qualification, plan, tmp_path/'second')
    assert_new_workbook(first, plan)
    assert_new_workbook(second, plan)
    assert first['generation_id'] != second['generation_id']
    assert first['path'] != second['path']


def test_explicit_delegate_returns_only_requested_driver_objects_without_mutation():
    assert hasattr(model, 'ExplicitPlanProposer'), 'Explicit staged plan delegate is missing'
    plan = _operating_plan()
    delegate = model.ExplicitPlanProposer(plan)
    contract = {'preparation_stage': {'scope': 'base', 'drivers': ['capex_pct', 'wacc']}}
    result = delegate({}, contract)
    assert set(result['drivers']) == {'capex_pct', 'wacc'}
    assert result['drivers'] == {name: plan['scenarios']['base'][name] for name in contract['preparation_stage']['drivers']}
    assert result['rationale'] == plan['scenario_rationale']['base']
    result['drivers']['wacc']['value'] = 99
    assert delegate({}, contract)['drivers']['wacc']['value'] == plan['scenarios']['base']['wacc']['value']
    assert delegate({}, {}) == plan


@pytest.mark.parametrize('fault', ['missing_driver', 'missing_evidence', 'fact_override', 'nonfinite', 'missing_rationale'])
def test_invalid_explicit_plan_is_rejected_without_workbook_or_promotion(tmp_path, fault):
    qualification = qualified(tmp_path)
    plan = _operating_plan()
    if fault == 'missing_driver':
        del plan['scenarios']['bull']['wacc']
    elif fault == 'missing_evidence':
        plan['scenarios']['base']['wacc']['evidence_ids'] = ['not-a-qualified-document']
    elif fault == 'fact_override':
        plan['scenarios']['base']['net_debt']['value'] = 1.
    elif fault == 'nonfinite':
        plan['scenarios']['base']['wacc']['value'] = float('nan')
    else:
        plan['scenario_rationale']['base'] = ''
    with pytest.raises((ValueError, KeyError)):
        build(qualification, plan, tmp_path/'invalid')
    assert not list((tmp_path/'invalid').glob('*.xlsx'))


@pytest.mark.parametrize('fault', ['source_fingerprint', 'quote_price', 'historical_fact'])
def test_source_guards_are_not_bypassed_by_an_explicit_author_plan(tmp_path, fault):
    qualification = qualified(tmp_path)
    plan = _operating_plan()
    if fault == 'source_fingerprint':
        qualification['fingerprint'] = '0'*64
    elif fault == 'quote_price':
        plan['model']['quotation']['value']['price'] += 1
    else:
        plan['model']['historical_revenue']['value'] += 1
    with pytest.raises(ValueError):
        build(qualification, plan, tmp_path/'invalid')
    assert not list((tmp_path/'invalid').glob('*.xlsx'))


def test_pending_history_cannot_silently_copy_a_different_authored_net_debt(tmp_path):
    qualification, plan = preparation_case(tmp_path)
    plan['scenarios']['bull']['net_debt']['value'] += 1
    with pytest.raises(ValueError):
        build(qualification, plan, tmp_path/'invalid')
    assert not list((tmp_path/'invalid').glob('*.xlsx'))


def test_pending_history_cannot_replace_invalid_authored_evidence_with_another_scenario(tmp_path):
    qualification, plan = preparation_case(tmp_path)
    plan['scenarios']['bull']['net_debt']['evidence_ids'] = ['not-a-qualified-document']
    with pytest.raises(ValueError):
        build(qualification, plan, tmp_path/'invalid')
    assert not list((tmp_path/'invalid').glob('*.xlsx'))


def test_pending_history_preserves_complete_authored_scenario_rationales(tmp_path):
    qualification, plan = preparation_case(tmp_path)
    payload = build(qualification, plan, tmp_path/'model')
    assert payload['preparation']['proposal']['plan']['scenario_rationale'] == plan['scenario_rationale']


def test_observed_exposure_uses_its_native_analysis_plan_without_fake_fair_value(tmp_path):
    from test_trade_idea_exposure import sourced_exposure
    qualification, plan = sourced_exposure(tmp_path)
    payload = build(qualification, plan, tmp_path/'exposure')
    assert_new_workbook(payload, plan)
    assert payload['analysis_usability']['usable'] is True
    assert all(payload.get('fair_value_'+scope) is None for scope in ('bear', 'base', 'bull'))
    assert payload['preparation']['proposal']['plan']['analysis_rationale'] == plan['analysis_rationale']


@pytest.mark.parametrize('context', [[], {'model_author': {'actor': 'PM', 'approval_status': 'approved'}}])
def test_author_metadata_cannot_claim_pm_approval_or_be_unstructured(tmp_path, context):
    qualification = qualified(tmp_path)
    with pytest.raises(ValueError):
        build(qualification, _operating_plan(), tmp_path/'invalid', author_context=context)
    assert not list((tmp_path/'invalid').glob('*.xlsx'))


def test_bank_plan_uses_existing_dynamic_capital_schema_without_fcff_projection(tmp_path):
    from test_trade_idea_families import family_factories, sourced_family
    from test_trade_idea_economic import synthetic_observed_quote_info
    bundle, plan, documents = sourced_family(family_factories()[0])
    ticker, day = bundle['case']['ticker'], bundle['case']['as_of']
    profile = deepcopy(bundle['case']['sources']['profile'])
    profile['data'] = {**profile['data'], 'info': deepcopy(bundle['case']['info']), 'vehicle_registry': None}
    quote = plan['model']['quotation']['value']
    profile.update(as_of=day, retrieved_at=day+'T12:00:00+00:00')
    profile['data']['info'].update(synthetic_observed_quote_info(
        ticker, day, currency=quote['quote_currency'], price=quote['price']))
    profile['data']['info']['shortName'] = 'Synthetic bank'
    identity = {'ticker': ticker, 'name': 'Synthetic bank', 'status': 'confirmed',
                'exchange': 'TEST', 'currency': quote['quote_currency']}
    qualification = model.qualification(ticker, identity, day, archive_root=tmp_path/'sources',
        providers={'profile': lambda *_a, **_k: deepcopy(profile)},
        source_report={'documents': documents, 'source_plan': deepcopy(plan)})
    assert qualification['status'] == 'qualified', qualification['reasons']
    payload = build(qualification, plan, tmp_path/'bank')
    assert_new_workbook(payload, plan)
    assert payload['method'] == 'bank_residual_income'
    assert 'capital.terminal_equity' in payload['preparation']['proposal']['plan']['scenarios']['base']
