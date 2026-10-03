"""Explicit failed-author recovery verifies the paid projected wire before retry."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.agents.model_authoring_context import project_model_authoring_messages
from test_trade_idea_context_projection_gate import _board, _request
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_run_controls_api import admission, db_path, migrated
from bellomberg.api import trade_idea_routes as routes


def recovery_fixture():
    catalog = _priced_request()['catalog_snapshot']
    accepted = {'version': 1, 'kind': 'failed_model_authoring',
        'mode': 'model_authoring_error_retry', 'desk': 'fundamentals', 'round_n': 1,
        'original_max_tokens': 128000, 'replacement_max_tokens': 128000,
        'original_iteration': 28, 'max_tool_iters': 30, 'request_id': 'failed-request'}
    current = SimpleNamespace(get_run=lambda _: {'run': {'continuation': {
        'specialist_response_recovery': deepcopy(accepted)}}})
    gate = trade.TradeIdeaBudgetGate(current, 'child', 'owner', catalog,
                                   catalog_fetcher=lambda: pytest.fail('No provider catalog during validation'))
    board, events = _board(gate)
    body, _, _ = _request()
    body['max_tokens'] = 128000
    body['system'] = 'Frozen original author contract'
    body['tools'] = [{'name': 'read_candidate_source', 'description': 'Exact frozen document',
                      'input_schema': {'type': 'object', 'properties': {}}}]
    _, receipt = project_model_authoring_messages(trade._stable_provider_messages(body['messages']))
    gate._project_model_authoring_request(body, receipt, 'specialist:fundamentals')
    accepted['request_sha256'] = board.data['_model_authoring_context_projections'][0]['projected_wire_sha256']
    events.clear()
    return gate, board, events, body, accepted


def test_failed_author_original_wire_is_verified_without_new_pin_or_reservation():
    gate, board, events, body, descriptor = recovery_fixture()
    before = deepcopy((body, board.data))
    gate.validate_response_recovery(descriptor, body, role='specialist:fundamentals')
    assert (body, board.data) == before and events == [] and gate._requests == {}


@pytest.mark.parametrize('mutation', ['no_pin', 'pin_hash', 'wire', 'cap', 'role', 'phase', 'turns', 'authorization'])
def test_failed_author_recovery_rejects_changed_paid_contract(mutation):
    gate, board, events, body, descriptor = recovery_fixture()
    role = 'specialist:fundamentals'
    supplied = deepcopy(descriptor)
    if mutation == 'no_pin': board.data['_model_authoring_context_projections'] = []
    elif mutation == 'pin_hash': board.data['_model_authoring_context_projections'][0]['projected_wire_sha256'] = 'changed'
    elif mutation == 'wire': body['system'] += ' changed'
    elif mutation == 'cap': body['max_tokens'] = 256000
    elif mutation == 'role': role = 'specialist:macro'
    elif mutation == 'phase': board.model_phase = 'review'
    elif mutation == 'turns': descriptor['max_tool_iters'] = 60; supplied = deepcopy(descriptor)
    else: supplied['request_id'] = 'another-receipt'
    with pytest.raises(ValueError):
        gate.validate_response_recovery(supplied, body, role=role)
    assert events == [] and gate._requests == {}


@pytest.mark.parametrize('acknowledged', [False, True])
def test_failed_response_selection_uses_normal_authenticated_resume(admission, monkeypatch, acknowledged):
    client, current, _, _, _, workers = admission
    calls = []
    monkeypatch.setattr(current, 'get_run', lambda _: {'recovery': {}})
    monkeypatch.setattr(routes, 'active_paid_reason', lambda *_a, **_k: None)
    def create(parent, **kwargs):
        calls.append((parent, kwargs))
        return {'run': {'id': 'explicit-failed-child', 'technical_status': 'accepted'}, 'created': True}
    monkeypatch.setattr(current, 'create_continuation', create)
    response = client.post('/trade-ideas/runs/failed-parent/resume', json={
        'idempotency_key': 'failed-author-native', 'cost_acknowledged': acknowledged,
        'recover_failed_request_id': 'a' * 32, 'budget_limit_usd': '20'})
    if not acknowledged:
        assert response.status_code == 428 and calls == workers == []
    else:
        assert response.status_code == 202, response.text
        assert calls == [('failed-parent', {'idempotency_key': 'failed-author-native',
            'authorize_new_requests': True, 'recover_truncated_request_id': None,
            'recover_failed_request_id': 'a' * 32, 'budget_limit_usd': '20'})]
        assert workers == ['explicit-failed-child']
