"""New research policy is explicit; historical runs retain their paid contract."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from test_trade_idea_capo_finalization_dispatch import research_board, capture, BeforeTransport


V2 = {'analysis_mode': 'fundamentals_research_v1', 'execution_policy': 'trade-idea-research/2'}


def test_new_policy_changes_only_declared_role_efforts_and_capo_output():
    from bellomberg.core.trade_idea_policy import role_effort, output_cap
    assert {role: role_effort(V2, role) for role in trade_idea.MODEL_IDS} == {
        'specialist': 'medium', 'red_team': 'medium', 'capo': 'low', 'aux': 'medium'}
    assert output_cap(V2, 'capo', 128000) == 32768
    assert output_cap(V2, 'specialist', 128000) == 128000
    assert role_effort({}, 'capo') == 'max'
    assert output_cap({}, 'capo', 128000) == 128000


@pytest.mark.parametrize('payload', [
    {'execution_policy': 'unknown'}, {'execution_policy': 'trade-idea-research/2'},
])
def test_unknown_or_nonresearch_policy_is_rejected(payload):
    from bellomberg.core.trade_idea_policy import execution_policy
    with pytest.raises(ValueError): execution_policy(payload)


def test_new_capo_dispatch_keeps_evidence_but_sends_r1_and_red_only_once(research_board):
    legacy = []
    with pytest.raises(BeforeTransport): capture(research_board, legacy)
    research_board.execution_policy = V2['execution_policy']
    research_board.budget_gate.reuse_capo_response = lambda: None
    original = deepcopy(research_board.data)
    calls = []
    with pytest.raises(BeforeTransport): capture(research_board, calls)
    assert calls[0]['thinking'] == {'type': 'effort', 'effort': 'low'}
    assert calls[0]['max_tokens'] == 32768
    prompt = calls[0]['messages'][0]['content']
    # JSON encoding escapes, so match the unique report opening rather than
    # guessing a serialized whitespace representation.
    for desk in trade_idea.TRADE_IDEA_DESKS:
        assert prompt.count('Original independent thesis ' + desk) == 1
        assert prompt.count('Final review ' + desk) == 1
    assert research_board.data == original
    assert '4000' not in calls[0]['system']
    assert '4,000' not in calls[0]['system']
    assert legacy[0]['thinking'] == {'type': 'effort', 'effort': 'max'}


def test_policy_mapping_is_immutable_to_callers():
    from bellomberg.core.trade_idea_policy import execution_policy, role_effort
    assert execution_policy(SimpleNamespace(**V2)) == V2['execution_policy']
    with pytest.raises(ValueError): role_effort(V2, 'unconfigured')


@pytest.mark.parametrize('field', ['fundamentals', '_research_thesis', '_sizing', '_decision_context',
                                  '_candidate_quote_initial', '_data_cutoff', '_portfolio_context'])
def test_completed_capo_fingerprint_binds_economic_inputs_not_dynamic_history(research_board, monkeypatch, field):
    monkeypatch.setattr(trade_idea, '_checkpoint_contract', lambda _board: {'source': 'frozen'})
    research_board.execution_policy = V2['execution_policy']
    original = trade_idea._capo_input_fingerprint(research_board, None, {'mandate': 'original'})
    research_board.candidate_history = 'A later purely technical failure'
    assert trade_idea._capo_input_fingerprint(research_board, None, {'mandate': 'original'}) == original
    if field == 'fundamentals':
        research_board.data[field][1] += ' A changed independent interpretation.'
    else:
        research_board.data[field] = {'changed': True}
    assert trade_idea._capo_input_fingerprint(research_board, None, {'mandate': 'original'}) != original


def test_v2_completion_gate_requires_matching_policy_and_rendered_text_integrity():
    from bellomberg.reporting.trade_idea_delivery import assess_trade_idea_completion
    manifest = {**V2, 'completion_contract': 'research-memo-pdf/1', 'model_status': 'not_required',
        'judgment': 'watch', 'complete_package_status': 'ready',
        'pdf_quality': {'status': 'ready', 'execution_policy': V2['execution_policy'], 'content_integrity': 'complete'}}
    assert assess_trade_idea_completion(manifest)['status'] == 'ready'
    for field in ('execution_policy', 'content_integrity'):
        broken = deepcopy(manifest)
        broken['pdf_quality'].pop(field)
        assert assess_trade_idea_completion(broken)['status'] == 'incomplete'


def test_completed_capo_ignores_only_book_render_timestamp(research_board, monkeypatch):
    monkeypatch.setattr(trade_idea, '_checkpoint_contract', lambda _board: {'source': 'frozen'})
    research_board.execution_policy = V2['execution_policy']
    research_board.data['_portfolio_context'] = {'timestamp': 'old', 'nav': '100',
        'positions': [{'ticker': 'TEST', 'price_timestamp': 'original', 'quantity': '2'}]}
    original = trade_idea._capo_input_fingerprint(research_board, None, {})
    research_board.data['_portfolio_context']['timestamp'] = 'later technical rendering'
    assert trade_idea._capo_input_fingerprint(research_board, None, {}) == original
    research_board.data['_portfolio_context']['positions'][0]['price_timestamp'] = 'new observation'
    assert trade_idea._capo_input_fingerprint(research_board, None, {}) != original
    research_board.data['_portfolio_context']['positions'][0]['price_timestamp'] = 'original'
    research_board.data['_portfolio_context']['nav'] = '101'
    assert trade_idea._capo_input_fingerprint(research_board, None, {}) != original
