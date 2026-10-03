"""Independent accounting boundaries for the native context projection."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.storage.trade_idea_store import BudgetBlocked, _digest
from test_trade_idea_context_projection_gate import (
    _setup, _request, _board, _gate, FakeMessages, db_path, migrated,
)
from test_trade_idea_store import _save_resume_checkpoint


def native_rows(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM trade_idea_costs ORDER BY request_id")]


def test_durable_projected_pin_and_paid_response_survive_two_restarts(migrated):
    current, payload, parent, token, gate, board, _events = _setup(migrated)
    body, projected, receipt = _request()
    def persist(_event):
        checkpoint = {"version": 1, "contract": {"ticker": "TEST"},
            "data": deepcopy(board.data), "raw_request_messages": deepcopy(body["messages"])}
        current.update_progress(parent, token, "checkpoint", {
            "checkpoint": checkpoint, "checkpoint_sha256": _digest(checkpoint)})
    board.persist_run_checkpoint = persist
    fake = FakeMessages('0.0001')
    old = gate.wrap_client(SimpleNamespace(messages=fake), role='specialist:fundamentals').messages.create(
        **body, model_authoring_context_projection=receipt)
    frozen = deepcopy(current.get_run(parent)['progress']['checkpoint'])
    assert frozen['raw_request_messages'] == body['messages']
    pins = frozen['data']['_model_authoring_context_projections']
    rows = native_rows(migrated)
    assert json.loads(rows[0]['receipt_json'])['request_sha256'] == pins[0]['projected_wire_sha256']
    assert pins[0]['original_wire_sha256'] != pins[0]['projected_wire_sha256']
    current.finish_run(parent, token, None, 'incomplete', reason='Crash after paid receipt before transcript append')
    for index in range(2):
        child = current.create_continuation(parent, idempotency_key='review-restart-' + str(index),
            authorize_new_requests=True)['run']['id']
        child_token = current.claim_run(child)
        inherited = current.get_run(child)['progress']['checkpoint']
        assert inherited == frozen
        child_gate = _gate(current, child, payload)
        child_board, checkpoints = _board(child_gate)
        child_board.data = deepcopy(inherited['data'])
        another = FakeMessages('0.0002')
        response = child_gate.wrap_client(SimpleNamespace(messages=another), role='specialist:fundamentals').messages.create(
            **{**body, 'messages': inherited['raw_request_messages']}, model_authoring_context_projection=receipt)
        assert response.request_id == old.request_id and response.id == old.id
        assert another.calls == 0 and checkpoints == []
        assert native_rows(migrated) == rows
        assert current.get_run(child)['cost']['charged_usd'] == '0.0001'
        current.finish_run(child, child_token, None, 'incomplete', reason='Second crash with same saved transcript')
        parent = child


def test_projection_does_not_authorize_retry_after_uncertain_dispatch(migrated):
    current, _payload, ident, _token, gate, board, _events = _setup(migrated)
    body, _projected, receipt = _request()
    calls = []
    def disconnected(**kwargs):
        calls.append(deepcopy(kwargs))
        raise ConnectionError('Frozen provider disconnect')
    client = gate.wrap_client(SimpleNamespace(messages=SimpleNamespace(create=disconnected)),
        role='specialist:fundamentals')
    with pytest.raises(ConnectionError):
        client.messages.create(**body, model_authoring_context_projection=receipt)
    before = native_rows(migrated)
    pin_before = deepcopy(board.data['_model_authoring_context_projections'])
    assert len(before) == 1 and before[0]['status'] == 'unknown'
    assert before[0]['charged_usd'] is None and before[0]['usage_json'] is None
    with pytest.raises(BudgetBlocked, match='unknown'):
        client.messages.create(**body, model_authoring_context_projection=receipt)
    assert len(calls) == 1 and native_rows(migrated) == before
    assert board.data['_model_authoring_context_projections'] == pin_before
    assert current.get_run(ident)['cost']['remaining_known_usd'] is None


def test_unused_malformed_projection_cannot_override_exact_paid_raw_replay(migrated):
    current, payload, parent, token, gate, _board_original, _events = _setup(migrated)
    body, _projected, _receipt = _request()
    fake = FakeMessages('0.0001')
    original = gate.wrap_client(SimpleNamespace(messages=fake), role='specialist:fundamentals').messages.create(**body)
    _save_resume_checkpoint(current, parent, token)
    current.finish_run(parent, token, None, 'incomplete', reason='Frozen raw receipt before new projection policy')
    child = current.create_continuation(parent, idempotency_key='review-raw-first', authorize_new_requests=True)['run']['id']
    current.claim_run(child)
    child_gate = _gate(current, child, payload)
    board, events = _board(child_gate)
    extra = FakeMessages('0.0002')
    old_rows = native_rows(migrated)
    replay = child_gate.wrap_client(SimpleNamespace(messages=extra), role='specialist:fundamentals').messages.create(
        **body, model_authoring_context_projection={'contract': 'malformed-unused'})
    assert replay.request_id == original.request_id and replay.id == original.id
    assert extra.calls == 0 and events == []
    assert '_model_authoring_context_projections' not in board.data
    assert native_rows(migrated) == old_rows
