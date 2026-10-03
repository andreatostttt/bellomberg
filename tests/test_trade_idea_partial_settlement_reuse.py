"""A measured incomplete stream remains paid evidence, never a fabricated answer."""
from copy import deepcopy
import json
import sqlite3

import pytest

from bellomberg.storage.trade_idea_store import BudgetBlocked, RunConflict, _digest
from test_trade_idea_store import db_path, migrated, request, store, _save_resume_checkpoint


def partial_parent(path, *, fault=None, settle=True):
    current, payload = store(path), request()
    parent = current.create_run(payload, idempotency_key='partial-parent')['run']['id']
    token = current.claim_run(parent)
    _save_resume_checkpoint(current, parent, token)
    model = payload['models']['capo']['model']
    fingerprint = _digest({'exact_body': 'unchanged Capo prompt'})
    partial = {'id': 'provider-partial', 'model': model,
        'choices': [{'index': 0, 'finish_reason': None, 'message': {'role': 'assistant', 'content': 'Partial'}}]}
    receipt = {'request_sha256': fingerprint, 'complete': False, 'partial_response': partial,
        'partial_response_sha256': _digest(partial), 'response_id': partial['id'], 'model': model}
    current.reserve_cost(parent, 'stream-lost', 'capo', model, '0.50', request_sha256=fingerprint)
    current.mark_cost_unknown(parent, 'stream-lost', reason='Frozen connection loss', receipt=receipt)
    current.finish_run(parent, token, None, 'incomplete', reason='Frozen connection loss')
    if fault == 'seal': receipt['partial_response_sha256'] = 'a' * 64
    if fault == 'identity':
        partial['id'] = 'different'
        receipt['partial_response_sha256'] = _digest(partial)
    if fault == 'model':
        partial['model'] = 'different'
        receipt['partial_response_sha256'] = _digest(partial)
    if fault == 'flag': receipt.pop('complete')
    if fault == 'complete_corrupt':
        receipt['complete'] = True
        receipt['response'] = {'id': receipt['response_id'], 'model': model, 'usage': {'cost_usd': '0.20'}}
        receipt['response_sha256'] = 'f' * 64
    if settle:
        current.reconcile_cost(parent, 'stream-lost', charged_usd='0.20', usage={'cost_usd': '0.20'},
            receipt=receipt)
    return current, parent, model, fingerprint, receipt


def test_unsettled_stream_never_authorizes_a_replacement(migrated):
    current, parent, *_ = partial_parent(migrated, settle=False)
    with pytest.raises(BudgetBlocked, match='unresolved provider'):
        current.create_continuation(parent, idempotency_key='blocked', authorize_new_requests=True)
    assert current.get_run(parent)['cost']['unknown_requests'] == 1


def test_settled_partial_is_preserved_and_replacement_has_a_new_accounted_request(migrated):
    current, parent, model, fingerprint, receipt = partial_parent(migrated)
    with sqlite3.connect(migrated) as conn:
        before = conn.execute('SELECT * FROM trade_idea_costs WHERE request_id=?', ('stream-lost',)).fetchone()
    child = current.create_continuation(parent, idempotency_key='replacement', authorize_new_requests=True)['run']['id']
    token = current.claim_run(child)
    assert current.reusable_response(child, token, role='capo', request_sha256=fingerprint) is None
    assert current.reserve_cost(child, 'replacement-call', 'capo', model, '0.50',
        request_sha256=fingerprint, worker_token=token) is True
    assert current.reserve_cost(child, 'replacement-call', 'capo', model, '0.50',
        request_sha256=fingerprint, worker_token=token) is False
    usage = {'cost_usd': '0.10'}
    response = {'id': 'provider-complete', 'model': model, 'stop_reason': 'end_turn',
        'content': [{'type': 'text', 'text': 'The complete new response.'}], 'usage': usage}
    current.reconcile_cost(child, 'replacement-call', charged_usd='0.10', usage=usage,
        receipt={'request_sha256': fingerprint, 'response_id': response['id'], 'model': model,
            'response': response, 'response_sha256': _digest(response)})
    _save_resume_checkpoint(current, child, token)
    current.finish_run(child, token, None, 'incomplete', reason='Crash after complete paid response')
    grandchild = current.create_continuation(child, idempotency_key='reuse-complete',
        authorize_new_requests=True)['run']['id']
    grand_token = current.claim_run(grandchild)
    reused = current.reusable_response(grandchild, grand_token, role='capo', request_sha256=fingerprint)
    assert reused == {**response, 'request_id': 'replacement-call'}
    assert current.get_run(grandchild)['cost']['charged_usd'] == '0.30'
    assert current.get_run(grandchild)['cost']['requests'] == 2
    with sqlite3.connect(migrated) as conn:
        assert conn.execute('SELECT * FROM trade_idea_costs WHERE request_id=?', ('stream-lost',)).fetchone() == before
    assert receipt['request_sha256'] == fingerprint and receipt['complete'] is False


@pytest.mark.parametrize('fault', ['seal', 'identity', 'model', 'flag', 'complete_corrupt'])
def test_invalid_partial_or_complete_evidence_never_silently_authorizes_retry(migrated, fault):
    current, parent, _, fingerprint, _ = partial_parent(migrated, fault=fault)
    child = current.create_continuation(parent, idempotency_key='bad-evidence', authorize_new_requests=True)['run']['id']
    token = current.claim_run(child)
    with pytest.raises(RunConflict, match='integrity|identity'):
        current.reusable_response(child, token, role='capo', request_sha256=fingerprint)
    assert current.get_run(child)['cost']['requests'] == 1
