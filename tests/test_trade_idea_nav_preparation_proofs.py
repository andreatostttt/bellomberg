"""Full original NAV ledger and dilution disclosure are preparer proof inputs."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.fund_nav_preparation import prove_nav, prove_incremental_policy
from bellomberg.valuation.input_preparation import _catalog, _compile
from bellomberg.valuation.preparation_methods import method_schema
from test_trade_idea_nav_components_evidence import normalize, source, dilution_source


DAY = '2026-09-28'
OPENING = '2026-06-30'
PERIMETER = {'entity': 'Synthetic Holding Ltd.', 'share_class': 'Public Shares', 'currency': 'USD'}


def component_item(report):
    return {'value': deepcopy(report['component_value']), 'kind': 'historical',
        'evidence_ids': [report['documents'][0]['id']],
        'evidence_pointer': {'value': '/component_value'}, 'rationale': 'Synthetic exhaustive primary ledger',
        'valid_until': DAY, 'valid_until_basis': {'policy': 'same_day', 'as_of': DAY}}


def policy_item(primary, proof):
    return {'value': {'liability_basis': 'all_reported_claims_in_components',
        'fees_basis': 'incremental_deductions_after_covered_aggregates',
        'distributions_basis': 'incremental_deductions_after_covered_aggregates',
        'share_basis': 'basic_no_convertibles_or_other_dilution', 'dilution_proof': deepcopy(proof)},
        'kind': 'analyst_estimate', 'evidence_ids': [primary['id']],
        'rationale': 'Synthetic off-balance disclosure, never inferred from basic shares',
        'valid_until': DAY, 'valid_until_basis': {'policy': 'same_day', 'as_of': DAY}}


def test_complete_original_primary_recompiles_exhaustive_component_proof():
    primary = source()
    report = normalize(primary=primary)
    normalized = report['documents'][0]
    catalog, issues, _ = _catalog([primary, normalized], date.fromisoformat(DAY))
    assert issues == []
    item = component_item(report)
    assert prove_nav('components', item, [catalog[normalized['id']]], 'USD million', OPENING,
                     PERIMETER, {'components': item}, lambda *a, **k: pytest.fail('No literal fallback')) is None
    without_original, missing, _ = _catalog([normalized], date.fromisoformat(DAY))
    assert normalized['id'] not in without_original and missing
    changed = deepcopy(normalized)
    content = json.loads(changed['text'])
    content['component_value']['other_liabilities'] += 1
    changed['text'] = json.dumps(content, sort_keys=True)
    changed['sha256'] = sha256(changed['text'].encode()).hexdigest()
    rejected, mismatches, _ = _catalog([primary, changed], date.fromisoformat(DAY))
    assert normalized['id'] not in rejected and mismatches


@pytest.mark.parametrize('fault', ['coverage', 'pointer', 'mixed', 'value'])
def test_component_proof_does_not_ignore_changed_coverage_or_extra_proofs(fault):
    report = normalize()
    item = component_item(report)
    if fault == 'coverage': item['value']['coverage']['share_class'] = 'Special Voting Share'
    elif fault == 'pointer': item['evidence_pointer']['unit'] = '/unit'
    elif fault == 'mixed': item['quoted_value'] = item['value']['cash']
    else: item['value']['accrued_fees'] += 1
    assert prove_nav('components', item, report['documents'], 'USD million', OPENING,
                     PERIMETER, {'components': item}, lambda *a, **k: pytest.fail('No literal fallback'))


def test_dilution_estimate_is_bound_to_real_original_page_before_compilation():
    primary, report, proof = dilution_source()
    catalog, issues, _ = _catalog([primary, *report['documents']], date.fromisoformat(DAY))
    assert issues == []
    components, policy = component_item(report), policy_item(primary, proof)
    model = {'components': components, 'policy': policy}
    assert prove_incremental_policy(policy, model, catalog, PERIMETER, OPENING, DAY) is None
    forged = deepcopy(policy)
    forged['value']['dilution_proof']['page']['text'] += ' Invented disclosure.'
    forged['value']['dilution_proof']['page']['sha256'] = sha256(
        forged['value']['dilution_proof']['page']['text'].encode()).hexdigest()
    assert prove_incremental_policy(forged, model, catalog, PERIMETER, OPENING, DAY)
    absent = deepcopy(policy)
    absent['value'].pop('dilution_proof')
    assert prove_incremental_policy(absent, model, catalog, PERIMETER, OPENING, DAY)
    uncited = deepcopy(policy)
    uncited['evidence_ids'] = [report['documents'][0]['id']]
    assert prove_incremental_policy(uncited, model, catalog, PERIMETER, OPENING, DAY)
    # Exercise the actual compiler estimate branch, not only its proof helper.
    subset = {'policy': method_schema('fund_nav')[0]['policy']}
    records, failures, _ = _compile({'model': model, 'scenarios': {s: {} for s in ('bear', 'base', 'bull')}},
        subset, {}, PERIMETER, {'valuation_date': OPENING, 'periods': []}, '', catalog,
        date.fromisoformat(DAY), method='fund_nav')
    assert any(row['driver'] == 'policy' for row in records), failures
    model['policy'] = forged
    records, failures, _ = _compile({'model': model, 'scenarios': {s: {} for s in ('bear', 'base', 'bull')}},
        subset, {}, PERIMETER, {'valuation_date': OPENING, 'periods': []}, '', catalog,
        date.fromisoformat(DAY), method='fund_nav')
    assert not any(row['driver'] == 'policy' for row in records)
    assert any(row['code'] == 'unverified_dilution_policy' for row in failures)


def test_legacy_component_basis_does_not_add_a_new_dilution_policy():
    assert prove_incremental_policy({'value': {}}, {'components': {'value': {'cash': 20}}},
        {}, PERIMETER, OPENING, DAY) is None


def test_free_synthetic_nav_gate_checks_off_balance_dilution_before_proposer(tmp_path):
    """Arithmetic fixture qualification is no live issuer qualification claim."""
    from test_trade_idea_nav_components_evidence import incremental_bundle
    from test_trade_idea_families import sourced_family
    from bellomberg.valuation.trade_idea_model import qualification, prepare
    bundle, plan, documents = sourced_family(incremental_bundle)
    primary, report, proof = dilution_source()
    plan['model']['components'] = component_item(report)
    plan['model']['policy'] = policy_item(primary, proof)
    for drivers in [plan['model'], *plan['scenarios'].values()]:
        for item in drivers.values():
            item.update(valid_until=DAY, valid_until_basis={'policy': 'same_day', 'as_of': DAY})
    documents.extend([primary, *report['documents']])
    profile = deepcopy(bundle['case']['sources']['profile'])
    profile['as_of'] = DAY
    profile['retrieved_at'] = DAY+'T12:00:00+00:00'
    profile['data'].setdefault('info', {}).update(symbol='SYNTH-NAV', longName=PERIMETER['entity'],
        currency='USD', financialCurrency='USD', exchange='SYNTHETIC')
    from test_trade_idea_economic import synthetic_observed_quote_info
    profile['data']['info'].update(synthetic_observed_quote_info('SYNTH-NAV', DAY,
        currency='USD', exchange='SYNTHETIC'))
    identity = {'status': 'confirmed', 'ticker': 'SYNTH-NAV', 'name': PERIMETER['entity'],
                'exchange': 'SYNTHETIC', 'currency': 'USD'}
    def qualify(candidate):
        return qualification('SYNTH-NAV', identity, DAY, archive_root=tmp_path,
            providers={'profile': lambda *_a, **_k: deepcopy(profile)},
            source_report={'documents': deepcopy(documents), 'source_plan': deepcopy(candidate)})
    positive = qualify(plan)
    assert positive['status'] == 'qualified', positive['reasons']
    absent = deepcopy(plan)
    absent['model']['policy']['value']['dilution_proof'] = None
    negative = qualify(absent)
    assert negative['status'] == 'blocked'
    assert any('policy' in reason and 'dilution' in reason for reason in negative['reasons'])
    calls = []
    with pytest.raises(ValueError, match='Fonti non qualificate'):
        prepare(negative, lambda *_: calls.append('paid proposer forbidden'), tmp_path/'blocked')
    assert calls == []
