"""PM 03/10/2026: one failed desk is a declared gap; the committee still reaches the Capo."""
import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V3, ROLE_EFFORT_V3
from test_trade_idea_no_workbook_e2e import no_workbook_case, _provider_round  # noqa: F401
from _smtp_cattura import smtp  # noqa: F401
from test_trade_idea_source_research import frozen_clock  # noqa: F401
from test_trade_idea_store import db_path, migrated  # noqa: F401


def _desk_calls(case, desk, round_n):
    return [call for call in case.providers
            if _provider_round(call) == round_n and ('You are the ' + desk + ' desk' in str(call.get('system'))
                                                    or 'as the ' + desk + ' desk' in str(call.get('system')))]


def _policy(case, policy):
    if policy is None:
        return
    case.request['execution_policy'] = policy
    for role, effort in ROLE_EFFORT_V3.items():
        case.request['models'][role]['reasoning_effort'] = effort
        snapshot = case.request['catalog_snapshot']['models'][role]
        snapshot['supported_efforts'] = ['low', 'medium', 'max']
        snapshot['max_completion_tokens'] = max(int(snapshot.get('max_completion_tokens') or 0), 128000)
        snapshot['context_length'] = max(int(snapshot.get('context_length') or 0), 1000000)


@pytest.mark.parametrize('policy, round_n', [(None, 1), (EXECUTION_POLICY_V3, 0), (EXECUTION_POLICY_V3, 1),
                                             (EXECUTION_POLICY_V3, 2)])
def test_one_truncated_desk_is_declared_and_the_capo_still_writes_the_memo(no_workbook_case, policy, round_n):
    case = no_workbook_case
    _policy(case, policy)
    case.state['truncate'] = ('crypto', round_n)
    ident = case.current.create_run(case.request, idempotency_key='desk-gap-' + str(policy) + str(round_n))['run']['id']
    detail = case.execute(ident, 'gap')
    assert case.state['capo_calls'] == 1, detail['run']['reason']
    assert detail['result']['judgment'] != 'incomplete', detail['run']['reason']
    data = detail['progress']['checkpoint']['data']
    assert set(data['_desk_gaps']) == {'crypto'}
    if round_n < 2:
        assert 'crypto' in data['_research_thesis']['missing_reports']
        assert not _desk_calls(case, 'crypto', 2)  # no spending on a desk without a thesis
    else:  # a final-round gap keeps the sealed R1, declared to the Capo
        assert 'crypto' in data['_research_thesis']['reports']
    assert detail['progress']['routing_checks']['evidence_sufficient'] is False
    if policy:
        capo = [call for call in case.providers if call.get('max_tokens') == 128000
                and (call.get('thinking') or {}).get('effort') == 'medium']
        assert capo, 'the /3 Capo request was not sent with its accepted policy'


def test_fundamentals_failure_is_below_quorum_and_stops_before_paying_r1(no_workbook_case):
    case = no_workbook_case
    case.state['truncate'] = ('fundamentals', 0)
    ident = case.current.create_run(case.request, idempotency_key='desk-gap-quorum')['run']['id']
    detail = case.execute(ident, 'quorum')
    assert case.state['capo_calls'] == 0
    assert detail['run']['technical_status'] == 'incomplete'
    assert 'quorum' in detail['run']['reason']
    assert not [call for call in case.providers if _provider_round(call) == 1]  # R1 never paid
    assert detail['result'] is None or detail['result']['judgment'] == 'incomplete'
    assert not case.smtp[0]


def test_only_allow_listed_failures_are_desk_gaps():
    import sqlite3
    from bellomberg.core.llm_client import APIStatusError
    from bellomberg.storage.trade_idea_store import BudgetBlocked
    local = trade_idea._desk_local_failure
    assert local(RuntimeError('specialist response truncated: stop_reason=max_tokens'))
    assert local(RuntimeError('crypto R1 response incomplete: failed'))
    assert local(APIStatusError(503, 'overloaded'))
    for error in (RuntimeError('Research thesis changed after the committee seal'),
                  RuntimeError('report persistence failed: disk'), sqlite3.OperationalError('database is locked'),
                  BudgetBlocked('per-run cost limit would be exceeded'), ValueError('catalogo modello cambiato'),
                  RuntimeError('Committee below quorum')):
        assert not local(error), error


def test_clean_committee_is_evidence_sufficient_so_the_gap_clauses_are_what_flip_it(no_workbook_case):
    case = no_workbook_case
    _policy(case, EXECUTION_POLICY_V3)
    case.state.update(judgment='favorable', source_gaps=False)
    ident = case.current.create_run(case.request, idempotency_key='clean-committee')['run']['id']
    detail = case.execute(ident, 'clean')
    assert case.state['capo_calls'] == 1
    assert detail['progress']['routing_checks']['evidence_sufficient'] is True, detail['progress']['routing_checks']


def test_unanswered_material_objection_is_declared_and_never_promotes(no_workbook_case):
    case = no_workbook_case
    _policy(case, EXECUTION_POLICY_V3)
    case.state.update(judgment='favorable', source_gaps=False, no_reply='quant')
    ident = case.current.create_run(case.request, idempotency_key='unanswered')['run']['id']
    detail = case.execute(ident, 'unanswered')
    assert case.state['capo_calls'] == 1, detail['run']['reason']
    rows = detail['progress']['checkpoint']['data']['_objections']
    assert any(row['objection']['desk'] == 'quant' and row.get('unanswered') for row in rows)
    assert detail['progress']['routing_checks']['evidence_sufficient'] is False
