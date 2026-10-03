"""Explicit final-only authority, on disposable SQLite and frozen receipts."""
import json
import sqlite3

import pytest

from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.storage.trade_idea_store import BudgetBlocked, IdempotencyConflict, RunConflict, _digest
from test_trade_idea_store import db_path, migrated, request, store
from test_trade_idea_run_controls_api import admission


def parent_case(path, fault=None, v2=False):
    current, payload = store(path), request()
    payload['budget_limit_usd'] = '15'
    if v2:
        payload['execution_policy'] = 'trade-idea-research/2'
        for role, selected in payload['models'].items():
            selected['reasoning_effort'] = 'low' if role == 'capo' else 'medium'
    payload['analysis_mode'] = payload['source_qualification']['analysis_mode'] = RESEARCH_ANALYSIS_MODE
    payload['authorization'].update(activities=['committee'], max_revision_rounds=0)
    parent = current.create_run(payload, idempotency_key='capo-source')['run']['id']
    token = current.claim_run(parent)
    model = payload['models']['capo']['model']
    contract = {key: payload[key] for key in ('ticker', 'language', 'view_text', 'models', 'analysis_mode')
                + (('execution_policy',) if v2 else ())}
    contract.update(version=1, source_fingerprint=payload['source_qualification']['fingerprint'])
    checkpoint = {'version': 1, 'contract': contract, 'data': {'macro': {'2': 'Original report'}},
                  'specialist_checkpoints': {}}
    if fault == 'checkpoint_contract': checkpoint['contract']['ticker'] = 'ALTERED'
    progress = {'checkpoint': checkpoint, 'checkpoint_sha256': _digest(checkpoint),
                'primary_failure': {'desk': 'capo', 'message': 'Capo troncato al limite di output'}}
    if fault == 'checkpoint_hash': progress['checkpoint_sha256'] = 'c' * 64
    current.update_progress(parent, token, 'capo', progress)
    wire_hash = _digest({'model': model, 'max_tokens': 128000, 'thinking': {'type': 'effort', 'effort': 'max'}})
    usage = {'cost_usd': '3.252236', 'input_tokens': 173059, 'output_tokens': 128000, 'reasoning_tokens': 128000}
    response = {'id': 'provider-capo', 'model': model, 'request_id': 'failed-capo',
        'stop_reason': 'max_tokens', 'content': [{'type': 'thinking', 'thinking': 'Frozen reasoning'}], 'usage': usage}
    if fault == 'public_text': response['content'].append({'type': 'text', 'text': 'An actual partial memo'})
    if fault == 'stop': response['stop_reason'] = 'end_turn'
    if fault == 'model': response['model'] = 'other-model'
    receipt = {'request_sha256': wire_hash, 'response': response, 'response_sha256': _digest(response),
        'response_id': response['id'], 'model': model, 'stop_reason': response['stop_reason']}
    if fault == 'response_hash': receipt['response_sha256'] = 'd' * 64
    if fault == 'request_hash': receipt['request_sha256'] = 'e' * 64
    current.reserve_cost(parent, 'failed-capo', 'capo', model, '4.2', request_sha256=wire_hash)
    if fault == 'unknown':
        current.mark_cost_unknown(parent, 'failed-capo', reason='Unmeasured', receipt=receipt)
    else:
        current.reconcile_cost(parent, 'failed-capo', charged_usd=usage['cost_usd'], usage=usage, receipt=receipt)
    current.finish_run(parent, token, None, 'incomplete', reason='Capo max_tokens')
    return current, parent, payload, receipt


def grant(current, parent, key='grant', **kwargs):
    return current.create_continuation(parent, idempotency_key=key, authorize_new_requests=True,
        capo_finalization_request_id='failed-capo', **kwargs)


def snapshot(path, parent):
    with sqlite3.connect(path) as conn:
        return (conn.execute('SELECT * FROM trade_idea_runs WHERE id=?', (parent,)).fetchone(),
                conn.execute('SELECT * FROM trade_idea_costs WHERE run_id=?', (parent,)).fetchall())


def test_no_authority_defaults_unchanged_and_missing_cost_ack_rejected(migrated):
    current, parent, payload, receipt = parent_case(migrated)
    before = snapshot(migrated, parent)
    with pytest.raises(ValueError, match='explicit authorization'):
        current.create_continuation(parent, idempotency_key='not-approved', capo_finalization_request_id='failed-capo')
    ordinary = current.create_continuation(parent, idempotency_key='ordinary', authorize_new_requests=True)
    assert current.accepted_capo_finalization(ordinary['run']['id']) is None
    assert ordinary['run']['models'] == payload['models']
    assert 'capo_finalization' not in ordinary['run']['continuation']
    with pytest.raises(IdempotencyConflict, match='finalization'):
        grant(current, parent)
    assert snapshot(migrated, parent) == before


def test_authorized_grant_preserves_source_and_budget_and_allows_only_one_new_request(migrated):
    current, parent, payload, receipt = parent_case(migrated)
    before = snapshot(migrated, parent)
    child = grant(current, parent)
    ident = child['run']['id']
    accepted = current.accepted_capo_finalization(ident)
    assert accepted == child['run']['continuation']['capo_finalization']
    assert accepted['kind'] == 'research_capo_finalization_v1'
    assert accepted['model'] == payload['models']['capo']['model']
    assert accepted['thinking'] == {'type': 'effort', 'effort': 'low'} and accepted['max_tokens'] == 20000
    assert accepted['context_projection'] == 'sealed_all_rounds_red_team_dedup_v1'
    assert accepted['response_sha256'] == receipt['response_sha256']
    assert child['cost']['budget_limit_usd'] == '15' and child['cost']['charged_usd'] == '3.252236'
    assert grant(current, parent, key='another-click')['created'] is False
    token = current.claim_run(ident)
    with pytest.raises(BudgetBlocked, match='Capo'):
        current.reserve_cost(ident, 'no-research', 'specialist:macro', payload['models']['specialist']['model'], '.1')
    with pytest.raises(BudgetBlocked, match='limit'):
        current.reserve_cost(ident, 'over-budget', 'capo', accepted['model'], '12')
    wire = _digest({'final-only': accepted})
    assert current.reserve_cost(ident, 'final-once', 'capo', accepted['model'], '.5', request_sha256=wire)
    assert current.reserve_cost(ident, 'final-once', 'capo', accepted['model'], '.5', request_sha256=wire) is False
    full = {'id': 'final-provider', 'model': accepted['model'], 'content': [{'type': 'text', 'text': 'Complete memo'}],
            'stop_reason': 'end_turn', 'usage': {'cost_usd': '.2'}}
    current.reconcile_cost(ident, 'final-once', charged_usd='.2', usage=full['usage'], receipt={
        'request_sha256': wire, 'response': full, 'response_sha256': _digest(full),
        'response_id': full['id'], 'model': full['model']})
    with pytest.raises(BudgetBlocked, match='one|consumed'):
        current.reserve_cost(ident, 'second-call', 'capo', accepted['model'], '.5', request_sha256=wire)
    current.finish_run(ident, token, None, 'incomplete', reason='Crash after paid complete response')
    grandchild = current.create_continuation(ident, idempotency_key='reuse', authorize_new_requests=True)
    grand = grandchild['run']['id']
    assert current.accepted_capo_finalization(grand) == accepted
    grand_token = current.claim_run(grand)
    assert current.reusable_response(grand, grand_token, role='capo', request_sha256=wire) == {**full, 'request_id': 'final-once'}
    with pytest.raises(BudgetBlocked, match='one|consumed'):
        current.reserve_cost(grand, 'third-call', 'capo', accepted['model'], '.5', request_sha256=wire)
    assert snapshot(migrated, parent) == before


@pytest.mark.parametrize('fault', ['unknown', 'public_text', 'stop', 'model', 'response_hash',
                                  'request_hash', 'checkpoint_hash', 'checkpoint_contract'])
def test_invalid_source_cannot_authorize_finalization(migrated, fault):
    current, parent, _, _ = parent_case(migrated, fault)
    before = snapshot(migrated, parent)
    with pytest.raises((RunConflict, BudgetBlocked)):
        grant(current, parent)
    assert snapshot(migrated, parent) == before


def test_descriptor_tamper_is_rejected_before_dispatch(migrated):
    current, parent, _, _ = parent_case(migrated)
    child = grant(current, parent)['run']['id']
    with sqlite3.connect(migrated) as conn:
        conn.execute('DROP TRIGGER trade_idea_run_input_immutable')
        saved = json.loads(conn.execute('SELECT request_json FROM trade_idea_runs WHERE id=?', (child,)).fetchone()[0])
        saved['continuation']['capo_finalization']['max_tokens'] = 128000
        conn.execute('UPDATE trade_idea_runs SET request_json=?, request_sha256=? WHERE id=?',
                     (json.dumps(saved), _digest(saved), child))
    with pytest.raises(RunConflict, match='finalization'):
        current.accepted_capo_finalization(child)


def test_finalization_cannot_raise_budget(migrated):
    current, parent, _, _ = parent_case(migrated)
    with pytest.raises(ValueError, match='budget|scope'):
        grant(current, parent, budget_limit_usd='16')


@pytest.mark.parametrize('inherited', [False, True])
def test_removing_grant_never_reopens_ordinary_provider_dispatch(migrated, inherited):
    current, parent, payload, _ = parent_case(migrated)
    child = grant(current, parent)['run']['id']
    if inherited:
        token = current.claim_run(child)
        current.finish_run(child, token, None, 'incomplete', reason='Stopped before final dispatch')
        child = current.create_continuation(child, idempotency_key='inherited', authorize_new_requests=True)['run']['id']
    current.claim_run(child)
    with sqlite3.connect(migrated) as conn:
        conn.execute('DROP TRIGGER trade_idea_run_input_immutable')
        saved = json.loads(conn.execute('SELECT request_json FROM trade_idea_runs WHERE id=?', (child,)).fetchone()[0])
        saved['continuation'].pop('capo_finalization')
        conn.execute('UPDATE trade_idea_runs SET request_json=?, request_sha256=? WHERE id=?',
                     (json.dumps(saved), _digest(saved), child))
    with pytest.raises(RunConflict, match='finalization'):
        current.reserve_cost(child, 'ordinary-bypass', 'capo', payload['models']['capo']['model'], '.5')
    with sqlite3.connect(migrated) as conn:
        assert conn.execute('SELECT count(*) FROM trade_idea_costs WHERE run_id=?', (child,)).fetchone()[0] == 0


@pytest.mark.parametrize('acknowledged', [False, True])
def test_api_requires_explicit_cost_consent_and_passes_only_the_selected_request(admission, monkeypatch, acknowledged):
    from bellomberg.api import trade_idea_routes as routes
    client, current, _, _, _, workers = admission
    calls = []
    monkeypatch.setattr(current, 'get_run', lambda _: {'run': {'analysis_mode': RESEARCH_ANALYSIS_MODE}, 'recovery': {}})
    monkeypatch.setattr(routes, 'active_paid_reason', lambda *_a, **_k: None)
    def create(parent, **kwargs):
        calls.append((parent, kwargs))
        return {'run': {'id': 'final-only-child', 'technical_status': 'accepted'}, 'created': True}
    monkeypatch.setattr(current, 'create_continuation', create)
    response = client.post('/trade-ideas/runs/selected-parent/resume', json={
        'idempotency_key': 'explicit-finalization', 'cost_acknowledged': acknowledged,
        'capo_finalization_request_id': 'failed-capo'})
    if not acknowledged:
        assert response.status_code == 428 and calls == workers == []
    else:
        assert response.status_code == 202, response.text
        assert calls == [('selected-parent', {'idempotency_key': 'explicit-finalization',
            'authorize_new_requests': True, 'recover_truncated_request_id': None,
            'capo_finalization_request_id': 'failed-capo'})]
        assert workers == ['final-only-child']



def test_v2_finalization_grant_never_has_less_room_than_the_accepted_capo_cap(migrated):
    from test_trade_idea_paid_capo_checkpoint import case as v2_case
    current, parent, _, body, _ = v2_case(migrated, public=' ', stop_reason='max_tokens')
    assert body['max_tokens'] == 32768
    child = current.create_continuation(parent, idempotency_key='v2-grant', authorize_new_requests=True,
                                        capo_finalization_request_id='original-paid-capo')
    accepted = current.accepted_capo_finalization(child['run']['id'])
    assert accepted['max_tokens'] == 32768
    assert accepted['thinking'] == {'type': 'effort', 'effort': 'low'}
