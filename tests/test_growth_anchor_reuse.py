"""Growth estimates are compiled once, never repurchased as identical copies."""
from copy import deepcopy

import pytest

from test_preparation_historical_first import _case
from test_preparation_growth_thesis import _thesis, ANCHORS
from test_sector_operating_segment_guidance import segment_build, growth_evidence, PERIODS


def _segment_anchor(periods):
    build = segment_build()
    for item in build['segments']:
        item['growth'].extend([0.] * (len(periods) - len(PERIODS)))
        item['growth_evidence'].extend(growth_evidence() for _ in periods[len(PERIODS):])
    return build


def test_segment_growth_mismatch_stops_before_forecast_stages():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs

    bundle, docs, plan = _case()
    periods = plan['model']['calendar']['value']['periods']
    calls = []

    def propose(_dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            calls.append('growth')
            answer = _thesis(plan, contract)
            build = _segment_anchor(periods)
            build['segments'][1]['growth'][1] = -.14
            answer['growth_thesis']['scenarios']['base']['anchors']['revenue_build']['value'] = build
            return answer
        calls.append((stage['scope'], tuple(stage['drivers'])))
        source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                'rationale': 'Synthetic source-bound stage'}

    result = prepare_method_inputs(bundle, documents=docs, propose=StagedProposer(
        propose, historical_first=True, on_historical=lambda *_: None, growth_thesis_first=True))
    assert result['status'] == 'incomplete'
    assert any('segmenti non riconciliati' in issue['reason'] for issue in result['issues']), result['issues']
    assert calls.count('growth') == 2  # One bounded correction, still invalid.
    assert all(call[0] == 'model' or call[1] == ('net_debt',)
               for call in calls if isinstance(call, tuple))


def test_valid_segment_growth_proof_preserves_anchor():
    from bellomberg.valuation.growth_thesis_arithmetic import prove_growth_revenues

    build = segment_build()
    anchor = {'value': build, 'kind': 'analyst_estimate'}
    thesis = {'scenarios': {scope: {'anchors': {
        'revenue_build': deepcopy(anchor),
        'revenue_growth': {'value': [0., 0.], 'kind': 'analyst_estimate'}}}
        for scope in ('bear', 'base', 'bull')}}
    plan = {'model': {'historical_revenue': {'value': 100.},
                      'calendar': {'value': {'valuation_date': '2025-12-31', 'periods': PERIODS}}}}
    before = deepcopy((thesis, plan))
    assert prove_growth_revenues(thesis, plan) is None
    assert (thesis, plan) == before


@pytest.mark.parametrize('bad_repair', (False, True))
def test_growth_correction_is_bounded_and_anchor_reuse_precedes_forecasts(bad_repair):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    bundle, docs, plan = _case()
    calls, growth_calls = [], []

    def propose(dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            growth_calls.append(deepcopy(stage))
            answer = _thesis(plan, contract)
            if len(growth_calls) == 1 or bad_repair:
                answer['growth_thesis']['scenarios']['base']['anchors']['revenue_build']['value']['unit_price'][0] += 1
            else:
                assert 'fcff_engine_semantics' in dossier
                assert 'failed_growth_thesis' in dossier
                assert stage['growth_correction']['attempt'] == 1
            return answer
        calls.append((stage['scope'], tuple(stage['drivers'])))
        if stage['scope'] != 'model' and stage['drivers'] != ['net_debt']:
            assert not set(stage['drivers']) & set(ANCHORS)
            for scope in ('bear', 'base', 'bull'):
                for name in ANCHORS:
                    assert dossier['completed_plan']['scenarios'][scope][name]['value'] == plan['scenarios'][scope][name]['value']
        assert stage['drivers'] != ['capdev_amortization_years']
        source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                'rationale': 'Synthetic sourced forecast'}

    result = prepare_method_inputs(bundle, documents=docs, propose=StagedProposer(
        propose, historical_first=True, on_historical=lambda *_: None, growth_thesis_first=True))
    assert len(growth_calls) == 2
    assert result['status'] == ('incomplete' if bad_repair else 'prepared'), result['issues']
    if bad_repair:
        assert all(scope == 'model' or names == ('net_debt',) for scope, names in calls)
