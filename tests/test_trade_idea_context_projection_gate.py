"""Native dispatch projection retains paid replay and durable accounting."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.agents.model_authoring_progress import build_model_authoring_progress
from test_trade_idea_pipeline import (
    FakeMessages, _call_kwargs, _gate, _priced_request, db_path, migrated, store,
)
from test_trade_idea_store import _save_resume_checkpoint


def _messages():
    ledger = build_model_authoring_progress(
        SimpleNamespace(data={}, source_qualification={}),
        contract={'schema': {'calendar': ['contract', 'model']}, 'scenarios': []},
        remaining_turns=12)
    marker = '\n\nRECORDED MODEL WORK / MISSING AUTHOR ACTIONS:\n'
    text = marker + json.dumps(ledger, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return [{'role': 'user', 'content': 'Original thesis and decisions.' + text},
            {'role': 'assistant', 'content': 'Preserve this actual author judgment.'},
            {'role': 'user', 'content': 'Keep all original evidence.' + text}]


def _request():
    from bellomberg.agents.model_authoring_context import project_model_authoring_messages
    body = {**_call_kwargs(), 'messages': _messages()}
    projected, receipt = project_model_authoring_messages(trade._stable_provider_messages(body['messages']))
    return body, projected, receipt


def _board(gate):
    events = []
    board = SimpleNamespace(run_scope='trade_idea', current_round=1, model_phase='building',
        data={'_model_authoring_completion': {'version': 1, 'mode': 'model_authoring_completion'}},
        raise_if_run_blocked=lambda: None)
    board.persist_run_checkpoint = lambda event: events.append((event, deepcopy(board.data)))
    gate.blackboard = board
    return board, events


def _setup(path):
    current, payload = store(path), _priced_request()
    run_id = current.create_run(payload, idempotency_key='projection-parent')['run']['id']
    token = current.claim_run(run_id)
    gate = _gate(current, run_id, payload)
    board, events = _board(gate)
    return current, payload, run_id, token, gate, board, events


def test_projection_is_sealed_before_reservation_without_rewriting_messages(migrated):
    current, payload, run_id, token, gate, board, events = _setup(migrated)
    body, projected, receipt = _request()
    original = deepcopy(body)
    observed = []
    fake = FakeMessages('0.0001')
    native = fake.create
    def create(**kwargs):
        assert events and board.data['_model_authoring_context_projections']
        assert current.get_run(run_id)['cost']['reserved_usd'] != '0'
        observed.append(deepcopy(kwargs))
        return native(**kwargs)
    fake.create = create
    result = gate.wrap_client(SimpleNamespace(messages=fake), role='specialist:fundamentals').messages.create(
        **body, model_authoring_context_projection=receipt)
    assert result.id == 'synthetic-response' and fake.calls == 1
    assert body == original
    assert observed[0]['messages'] == projected
    assert 'model_authoring_context_projection' not in observed[0]
    assert observed[0]['max_tokens'] == body['max_tokens']
    assert observed[0]['thinking'] == body['thinking']
    assert current.get_run(run_id)['cost']['charged_usd'] == '0.0001'
    assert len(board.data['_model_authoring_context_projections']) == 1


@pytest.mark.parametrize('mutation', ['receipt', 'role', 'phase', 'descriptor', 'existing_pin'])
def test_invalid_projection_never_reaches_provider_or_reservation(migrated, mutation):
    current, payload, run_id, token, gate, board, events = _setup(migrated)
    body, projected, receipt = _request()
    role = 'specialist:fundamentals'
    if mutation == 'receipt': receipt['projected_messages_sha256'] = 'tampered'
    elif mutation == 'role': role = 'specialist:macro'
    elif mutation == 'phase': board.model_phase = 'review'
    elif mutation == 'descriptor': board.data['_model_authoring_completion'] = {}
    else:
        gate._project_model_authoring_request(body, receipt, role)
        board.data['_model_authoring_context_projections'][0]['projected_wire_sha256'] = 'tampered'
    fake = FakeMessages('0.0001')
    with pytest.raises(ValueError, match='projection'):
        gate.wrap_client(SimpleNamespace(messages=fake), role=role).messages.create(
            **body, model_authoring_context_projection=receipt)
    assert fake.calls == 0 and current.get_run(run_id)['cost']['requests'] == 0


@pytest.mark.parametrize('paid_projected', [False, True])
def test_paid_raw_or_projected_response_replays_without_new_cost(migrated, paid_projected):
    current, payload, parent, token, gate, board, events = _setup(migrated)
    body, projected, receipt = _request()
    fake = FakeMessages('0.0001')
    options = {'model_authoring_context_projection': receipt} if paid_projected else {}
    old = gate.wrap_client(SimpleNamespace(messages=fake), role='specialist:fundamentals').messages.create(
        **body, **options)
    pins = deepcopy(board.data.get('_model_authoring_context_projections', []))
    _save_resume_checkpoint(current, parent, token)
    current.finish_run(parent, token, None, 'incomplete', reason='Offline crash after paid response')
    for index in range(2):
        child = current.create_continuation(parent, idempotency_key='projection-resume-' + str(index),
            authorize_new_requests=True)['run']['id']
        child_token = current.claim_run(child)
        child_gate = _gate(current, child, payload)
        child_board, child_events = _board(child_gate)
        child_board.data['_model_authoring_context_projections'] = deepcopy(pins)
        extra = FakeMessages('0.0002')
        replay = child_gate.wrap_client(SimpleNamespace(messages=extra), role='specialist:fundamentals').messages.create(
            **body, model_authoring_context_projection=receipt)
        assert replay.id == old.id and replay.request_id == old.request_id
        assert extra.calls == 0
        assert child_board.data['_model_authoring_context_projections'] == pins
        assert current.get_run(child)['cost']['requests'] == 1
        assert current.get_run(child)['cost']['charged_usd'] == '0.0001'
        _save_resume_checkpoint(current, child, child_token)
        current.finish_run(child, child_token, None, 'incomplete', reason='Second offline recovery')
        parent = child


def test_checkpoint_failure_prevents_reservation_and_dispatch(migrated):
    current, payload, run_id, token, gate, board, events = _setup(migrated)
    body, projected, receipt = _request()
    board.persist_run_checkpoint = lambda _event: (_ for _ in ()).throw(OSError('offline persistence failure'))
    fake = FakeMessages('0.0001')
    with pytest.raises(OSError, match='persistence'):
        gate.wrap_client(SimpleNamespace(messages=fake), role='specialist:fundamentals').messages.create(
            **body, model_authoring_context_projection=receipt)
    assert fake.calls == 0 and current.get_run(run_id)['cost']['requests'] == 0
    assert board.data.get('_model_authoring_context_projections', []) == []
