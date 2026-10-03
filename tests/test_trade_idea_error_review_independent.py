"""Independent F10 probes with declared synthetic ledger observations.

The pure probes classify already-admitted immutable observations, including
legacy vector deltas supported by earnings_review. The integration probe uses
only a temporary registered common model and temporary event database.
"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bellomberg.valuation.trade_idea_workspace import ResearchWorkspace
from test_trade_idea_workspace import research, action
from test_valuation_snapshot_persistence import db


def _observation(event_id, generation, delta):
    return {'id': event_id, 'kind': 'earnings_review', 'generation_id': generation,
            'data': {'status': 'review_required', 'differences': [{
                'driver': 'revenue', 'expected': [10., 20.] if isinstance(delta, list) else 10.,
                'observed': [10.+delta[0], 20.+delta[1]] if isinstance(delta, list) else 10.+delta,
                'delta': delta, 'unit': 'EUR million', 'entity': 'SYNTHETIC GROUP',
                'period': '2026-01-01/2026-12-31', 'source': 'https://example.org/synthetic'}]}}


def _review(history, generation='selected-generation'):
    workspace = ResearchWorkspace.__new__(ResearchWorkspace)
    workspace.events = SimpleNamespace(list=lambda run_id: deepcopy(history))
    return workspace._error_review({'run': {'id': 'synthetic-run'}},
                                   {'generation_id': generation}, {}, None)


@pytest.mark.parametrize('delta', [0., -0., [0., 0.], [-0., 0.]])
def test_exact_matches_are_not_deviations_even_for_retained_vector_observations(delta):
    result = _review([_observation(17, 'observed-generation', delta)])
    assert result['findings'] == []
    assert result['matched_observations'] == 1
    assert all(row['status'] == 'insufficient_evidence' for row in result['categories'].values())
    assert result['causal_improvement_claimed'] is False


@pytest.mark.parametrize('delta', [3., [0., 3.], [-3., 3.]])
def test_any_nonzero_observation_stays_unattributed_without_a_pm_judgment(delta):
    result = _review([_observation(23, 'observed-generation', delta)])
    assert result['matched_observations'] == 0
    assert len(result['findings']) == 1
    finding = result['findings'][0]
    assert finding['event_id'] == 23 and finding['generation_id'] == 'observed-generation'
    assert finding['category'] == 'unattributed_deviation'
    assert finding['attribution_author'] is None
    assert finding['data_error_status'] == 'not_established'
    assert finding['causal_attribution_established'] is False


def test_a_pm_judgment_does_not_relabel_other_event_ids_or_generations():
    history = [_observation(23, 'older-generation', 3.), _observation(24, 'newer-generation', -3.),
               {'id': 25, 'kind': 'error_attribution', 'generation_id': 'selected-generation',
                'data': {'category': 'timing', 'evidence_event_id': 23,
                         'evidence_generation_id': 'older-generation', 'author': 'PM'}}]
    before = deepcopy(history)
    result = _review(history)
    assert [(row['event_id'], row['generation_id'], row['category']) for row in result['findings']] == [
        (23, 'older-generation', 'timing'), (24, 'newer-generation', 'unattributed_deviation')]
    assert result['review_generation_id'] == 'selected-generation'
    assert result['findings'][0]['attribution_author'] == 'PM'
    assert not result['findings'][0]['causal_attribution_established']
    assert history == before


def test_multiple_pm_categories_remain_visible_without_selecting_a_cause():
    history = [_observation(23, 'older-generation', -3.)]
    history += [{'id': 24+index, 'kind': 'error_attribution', 'generation_id': 'selected-generation',
                 'data': {'category': category, 'evidence_event_id': 23}}
                for index, category in enumerate(('data', 'assumption', 'timing', 'judgment'))]
    result = _review(history)
    finding = result['findings'][0]
    assert finding['category'] == 'multiple_attributions'
    assert finding['attributed_categories'] == ['assumption', 'data', 'judgment', 'timing']
    assert finding['data_error_status'] == 'not_established'
    assert finding['causal_attribution_established'] is False
    assert result['causal_improvement_claimed'] is False


def test_event_binding_persistence_and_attribution_idempotence_use_temporary_ledger(research):
    workspace, current, run_id, model = research
    older = str(uuid4())
    admitted = _observation(0, older, -3.)['data']
    event = workspace.events.apply(run_id, 'earnings_review', {}, lambda _: deepcopy(admitted),
                                   request_id=str(uuid4()), generation_id=older)
    first = action(workspace, run_id, model, 'error_review', {})
    assert first['data']['findings'][0]['category'] == 'unattributed_deviation'
    key = str(uuid4())
    request = {'category': 'judgment', 'evidence_event_id': event['id'],
               'rationale': 'Declared synthetic PM judgment, causality has not been established'}
    attribution = action(workspace, run_id, model, 'error_attribution', request, key)
    repeated = action(workspace, run_id, model, 'error_attribution', request, key)
    assert repeated == attribution
    assert attribution['data']['evidence_generation_id'] == older
    assert attribution['data']['review_generation_id'] == model['generation_id']
    reviewed = action(workspace, run_id, model, 'error_review', {})
    finding = reviewed['data']['findings'][0]
    assert finding['event_id'] == event['id'] and finding['generation_id'] == older
    assert finding['category'] == 'judgment' and finding['causal_attribution_established'] is False
    history = workspace.events.list(run_id)
    assert sum(row['kind'] == 'error_attribution' for row in history) == 1
    assert workspace.events.get(run_id, event['id'])['data'] == admitted
    assert workspace.events.get(run_id, reviewed['id'])['data'] == reviewed['data']
    with pytest.raises(ValueError, match='absent'):
        action(workspace, run_id, model, 'error_attribution', dict(request, evidence_event_id=2**31))
    assert current.get_run(run_id)['cost']['requests'] == 0
