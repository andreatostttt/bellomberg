"""Research closeout is explicit, local and does not pretend the ledger is complete."""
from types import SimpleNamespace

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists.base import Specialist
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE


def board():
    return SimpleNamespace(analysis_mode=RESEARCH_ANALYSIS_MODE, run_scope='trade_idea',
        data={'_completed_stages': {f'{desk}:{n}': {} for desk in trade_idea.TRADE_IDEA_DESKS
            for n in range(3)}, '_research_thesis': {'saved': True}, '_red_research_review': {'saved': True},
            '_objections': [{'objection': {'id': 'missing-event', 'desk': 'eventdesk', 'material': True},
                'response': None, 'state': 'open', 'evidence_refs': [], 'model_revision_id': None}]})


def test_remaining_work_keeps_missing_material_reply_separate_from_completed_r2():
    pending = trade_idea._remaining_work(board())
    assert pending['remaining'] == ['eventdesk:objection_replies', 'capo']
    assert pending['missing_material_objections'] == [{'id': 'missing-event', 'desk': 'eventdesk'}]


def test_reply_closeout_exposes_only_local_tools_and_blocks_external_dispatch(monkeypatch):
    actor = object.__new__(Specialist)
    actor.name, actor.blackboard = 'eventdesk', board()
    actor._task_context = {'kind': 'research_objection_completion', 'objection_ids': ['missing-event']}
    monkeypatch.setattr(actor, '_execute_meta_tool_unlocked', lambda *_a: (_ for _ in ()).throw(
        AssertionError('unapproved tool reached dispatch')))
    assert {tool['name'] for tool in actor._build_tools_schema()} == {
        'respond_trade_idea_objection', 'read_candidate_source', 'read_company_dossier', 'ask_specialist'}
    assert actor._execute_meta_tool('search_company_sources', {'ticker': 'REY.MI'})['ok'] is False
    assert actor._execute_meta_tool('respond_trade_idea_objection', {'objection_id': 'other'})['ok'] is False


def test_reply_closeout_cannot_overwrite_an_already_recorded_response(monkeypatch):
    actor = object.__new__(Specialist)
    actor.name, actor.blackboard = 'eventdesk', board()
    actor._task_context = {'kind': 'research_objection_completion', 'objection_ids': ['missing-event']}
    actor.blackboard.data['_objections'][0]['response'] = 'Uncertainty retained with reasons.'
    monkeypatch.setattr(actor, '_execute_meta_tool_unlocked', lambda *_a: (_ for _ in ()).throw(
        AssertionError('previous response was overwritten')))
    reply = actor._execute_meta_tool('respond_trade_idea_objection',
        {'objection_id': 'missing-event', 'response': 'Replacement', 'state': 'answered', 'evidence_refs': []})
    assert reply['ok'] is False
    assert actor.blackboard.data['_objections'][0]['response'] == 'Uncertainty retained with reasons.'
    exact = actor._execute_meta_tool('respond_trade_idea_objection', {
        'objection_id': 'missing-event', 'response': 'Uncertainty retained with reasons.',
        'state': 'open', 'evidence_refs': []})
    assert exact == {'ok': True, 'objection_id': 'missing-event', 'state': 'open'}
