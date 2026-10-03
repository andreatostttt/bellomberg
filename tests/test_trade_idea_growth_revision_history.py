"""A second scoped review must retain the actual archived Growth evidence."""
from copy import deepcopy

import pytest

from bellomberg.valuation.trade_idea_model import prepare, revise
from test_trade_idea_economic import qualified, _operating_plan


@pytest.mark.parametrize('current_thesis', [False, True], ids=['history_only', 'history_and_current'])
def test_scoped_revision_retains_previous_growth_history(tmp_path, current_thesis):
    sources = qualified(tmp_path)
    before = prepare(sources, lambda *_: deepcopy(_operating_plan()), tmp_path / 'before')
    previous_history = [{'thesis': {'historical_basis': {'summary': 'Synthetic previous business analysis'}},
        'source_fingerprint': sources['fingerprint'], 'previous_generation_id': 'synthetic-previous-growth'}]
    before['preparation'].update(growth_thesis_history=deepcopy(previous_history),
        growth_thesis_status='superseded_by_explicit_model_revision')
    expected = deepcopy(previous_history)
    if current_thesis:
        thesis = {'historical_basis': {'summary': 'Synthetic current business analysis'}}
        before['preparation'].update(growth_thesis=deepcopy(thesis),
            growth_thesis_source_fingerprint=sources['fingerprint'],
            growth_thesis_generation_id=before['generation_id'])
        expected.append({'thesis': thesis, 'source_fingerprint': sources['fingerprint'],
                         'previous_generation_id': before['generation_id']})
    frozen = deepcopy(before)
    calls = []

    def reviewer(dossier, contract):
        calls.append(contract['preparation_stage'])
        return {'drivers': {'wacc': deepcopy(before['preparation']['proposal']['plan']['scenarios']['base']['wacc'])},
                'rationale': 'Synthetic retention of the same discount rate and economic drivers.'}

    after = revise(before, {'rationale': 'Synthetic scoped retention review', 'evidence_ids': ['annual-1'],
        'analysis_context': {'review_scope': {'base': ['wacc']}}}, qualification=sources,
        propose=reviewer, output_dir=tmp_path / 'after')
    assert len(calls) == 1
    assert before == frozen
    assert after['preparation']['growth_thesis_history'] == expected
    assert after['preparation']['growth_thesis_status'] == 'superseded_by_explicit_model_revision'
    assert 'growth_thesis' not in after['preparation']
    assert after['preparation']['proposal']['plan']['model'] == before['preparation']['proposal']['plan']['model']
    assert after['preparation']['proposal']['method_records'] == before['preparation']['proposal']['method_records']
