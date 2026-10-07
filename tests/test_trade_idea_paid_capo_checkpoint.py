"""Exact paid Capo recovery uses immutable requests and disposable SQLite only."""
from copy import deepcopy
import json
import sqlite3

import pytest

from bellomberg.storage.trade_idea_store import BudgetBlocked, RunConflict, _digest
from test_trade_idea_store import db_path, migrated, request, store
from trade_idea_fixtures import research_result


POLICY = 'trade-idea-research/2'
DESKS = ('macro', 'eventdesk', 'crypto', 'fundamentals', 'quant', 'options')


def v2_request():
    # Z3b 05/10: qualificazione di ricerca VERA (research_required), non piu' il finto 'qualified'.
    from _trade_idea_research_request import research_request
    payload = research_request(request())
    payload['execution_policy'] = POLICY
    for role, selected in payload['models'].items():
        selected['reasoning_effort'] = 'low' if role == 'capo' else 'medium'
    return payload


def case(path, *, public=None, stop_reason='end_turn', settled=True, record_public=False):
    current, payload = store(path), v2_request()
    ident = current.create_run(payload, idempotency_key='paid-capo-source')['run']['id']
    token = current.claim_run(ident)
    contract = {key: deepcopy(payload[key]) for key in
        ('ticker', 'language', 'view_text', 'models', 'analysis_mode', 'execution_policy')}
    contract.update(version=1, source_fingerprint=payload['source_qualification']['fingerprint'])
    from bellomberg.core.mandato_pm import MANDATE_TEXT_POLICY_KEY
    persisted = current.get_run(ident)['run']
    if MANDATE_TEXT_POLICY_KEY in persisted:
        contract[MANDATE_TEXT_POLICY_KEY] = persisted[MANDATE_TEXT_POLICY_KEY]
    data = {desk: {str(n): desk + ' original report ' + str(n) for n in (0, 1, 2)} for desk in DESKS}
    data.update(_research_thesis={'research_ref': 'sealed-research', 'dossiers': {'TEST': {'source': 'frozen'}}},
        _research_review={'objections': [], 'research_ref': 'sealed-research'},
        _red_team={'1': 'Original Red Team'}, _objections=[], _decisive_questions=['Original question'],
        _sizing={'status': 'unavailable', 'reason': 'Missing observed prices'},
        _decision_context={'ticker': 'TEST', 'required': []},
        _candidate_quote_initial={'status': 'unavailable', 'reason': 'No observed quote'},
        _data_cutoff='2026-10-03T00:00:00Z',
        _portfolio_context={'positions': [], 'observed_at': 'original technical timestamp'})
    checkpoint = {'version': 1, 'contract': contract, 'data': data, 'specialist_checkpoints': {},
                  'orari_report': {desk: {'1': 'original', '2': 'final'} for desk in DESKS}}
    save_cp(current, ident, token, checkpoint)
    body = {'model': payload['models']['capo']['model'], 'max_tokens': 32768,
        'reasoning': {'effort': 'low'}, 'messages': [{'role': 'system', 'content': 'Complete original system'},
        {'role': 'user', 'content': 'Original dynamic history, unchanged paid context and citations'}],
        'response_format': {'type': 'json_object'}}
    assert current.reserve_cost(ident, 'original-paid-capo', 'capo', body['model'], '1',
        request_sha256=_digest(body), worker_token=token, capo_request_body=body)
    payload_result = research_result('watch')
    for key in ('run_id', 'run_type', 'pm_view', 'destination'):
        payload_result.pop(key, None)
    payload_result.update(ticker='TEST', valuation_refs=[], model_review=None)
    public = public if public is not None else '```json\n' + json.dumps(payload_result) + '\n```'
    usage = {'input_tokens': 100, 'output_tokens': 1500, 'reasoning_tokens': 100, 'cost_usd': '0.30'}
    response = {'id': 'provider-complete-capo', 'model': body['model'], 'request_id': 'original-paid-capo',
        'stop_reason': stop_reason, 'content': [{'type': 'text', 'text': public}], 'usage': usage}
    receipt = {'request_sha256': _digest(body), 'response': response, 'response_sha256': _digest(response),
        'response_id': response['id'], 'model': response['model'], 'stop_reason': stop_reason}
    if settled:
        current.reconcile_cost(ident, 'original-paid-capo', charged_usd='0.30', usage=usage, receipt=receipt)
    else:
        current.mark_cost_unknown(ident, 'original-paid-capo', reason='No known cost', receipt=receipt)
    if record_public:
        after_public = deepcopy(checkpoint)
        after_public['data']['_capo'] = {'3': public}
        after_public['orari_report']['_capo'] = {'3': 'later public response timestamp'}
        save_cp(current, ident, token, after_public)
    current.finish_run(ident, token, None, 'incomplete', reason='Crash after paid provider response')
    return current, ident, checkpoint, body, response


def save_cp(current, ident, token, cp):
    current.update_progress(ident, token, 'capo', {'checkpoint': cp, 'checkpoint_sha256': _digest(cp)})


def child(current, parent, key):
    ident = current.create_continuation(parent, idempotency_key=key, authorize_new_requests=True)['run']['id']
    return ident, current.claim_run(ident)


def rows(path, sql, args=()):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, args)]


def test_paid_fenced_response_reused_without_dynamic_history_and_two_resumes(migrated):
    current, original, cp, body, response = case(migrated, record_public=True)
    paid_before = rows(migrated, 'SELECT * FROM trade_idea_costs')
    source_before = rows(migrated, 'SELECT * FROM trade_idea_runs WHERE id=?', (original,))
    history = current.create_run(v2_request(), idempotency_key='later-candidate-history')['run']['id']
    history_token = current.claim_run(history)
    current.finish_run(history, history_token, None, 'incomplete', reason='Later historical record')
    successor, token = child(current, original, 'resume-paid')
    assert current.get_run(successor)['run']['execution_policy'] == POLICY
    technical = deepcopy(cp)
    technical['data']['_primary_failure'] = {'message': 'Original JSON parser rejected an outer fence'}
    technical['data']['_portfolio_context']['observed_at'] = 'new observation metadata'
    save_cp(current, successor, token, technical)
    assert current.reusable_capo_response(successor, token) == response
    assert current.reusable_capo_response(successor, token) == response
    reused = rows(migrated, "SELECT * FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (successor,))
    assert len(reused) == 1
    saved = rows(migrated, "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='capo_request_saved'", (original,))
    assert len(saved) == 1 and json.loads(saved[0]['payload_json'])['request_body'] == body
    current.finish_run(successor, token, None, 'incomplete', reason='Crash after exact receipt replay')
    second, second_token = child(current, successor, 'resume-paid-again')
    assert current.reusable_capo_response(second, second_token) == response
    assert rows(migrated, 'SELECT * FROM trade_idea_costs') == paid_before
    assert rows(migrated, 'SELECT * FROM trade_idea_runs WHERE id=?', (original,)) == source_before
    assert current.get_run(second)['cost']['requests'] == 1
    assert current.get_run(second)['cost']['charged_usd'] == '0.30'


@pytest.mark.parametrize('field', ['fundamentals', '_research_thesis', '_research_review', '_sizing',
                                   '_decision_context', '_candidate_quote_initial', '_red_team'])
def test_changed_economic_binding_blocks_paid_reuse_without_new_cost(migrated, field):
    current, parent, cp, _, _ = case(migrated)
    ident, token = child(current, parent, 'changed-economic-context')
    altered = deepcopy(cp)
    altered['data'][field] = {'changed': 'A new economic basis requires explicit review'}
    save_cp(current, ident, token, altered)
    with pytest.raises(RunConflict, match='binding|review|checkpoint'):
        current.reusable_capo_response(ident, token)
    assert current.get_run(ident)['cost']['requests'] == 1
    assert not rows(migrated, "SELECT id FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (ident,))


@pytest.mark.parametrize('fault', ['body', 'response_hash', 'response_model', 'usage', 'usage_tokens', 'body_model'])
def test_tampered_request_response_or_usage_blocks_replay(migrated, fault):
    current, parent, _, _, _ = case(migrated)
    ident, token = child(current, parent, 'integrity-failure')
    with sqlite3.connect(migrated) as conn:
        if fault in ('body', 'body_model'):
            conn.execute('DROP TRIGGER trade_idea_event_immutable')
            saved = json.loads(conn.execute("SELECT payload_json FROM trade_idea_events WHERE kind='capo_request_saved'").fetchone()[0])
            if fault == 'body': saved['request_body']['messages'][1]['content'] = 'Changed original prompt'
            else: saved['request_body']['model'] = 'different-model'
            conn.execute("UPDATE trade_idea_events SET payload_json=? WHERE kind='capo_request_saved'", (json.dumps(saved),))
        elif fault == 'usage':
            conn.execute("UPDATE trade_idea_costs SET usage_json=?", (json.dumps({'cost_usd': '0.01'}),))
        elif fault == 'usage_tokens':
            usage = json.loads(conn.execute('SELECT usage_json FROM trade_idea_costs').fetchone()[0])
            usage['output_tokens'] += 1
            conn.execute('UPDATE trade_idea_costs SET usage_json=?', (json.dumps(usage),))
        else:
            receipt = json.loads(conn.execute('SELECT receipt_json FROM trade_idea_costs').fetchone()[0])
            if fault == 'response_hash': receipt['response_sha256'] = 'c' * 64
            else:
                receipt['response']['model'] = 'different-model'
                receipt['response_sha256'] = _digest(receipt['response'])
            conn.execute('UPDATE trade_idea_costs SET receipt_json=?', (json.dumps(receipt),))
    with pytest.raises(RunConflict):
        current.reusable_capo_response(ident, token)
    assert len(rows(migrated, 'SELECT * FROM trade_idea_costs')) == 1


@pytest.mark.parametrize('public,stop', [('Not JSON', 'end_turn'), ('{"ticker":"TEST"}', 'end_turn'),
    ('', 'end_turn'), ('{"incomplete":', 'max_tokens')])
def test_malformed_or_truncated_paid_output_never_becomes_qualified_reuse(migrated, public, stop):
    current, parent, _, _, _ = case(migrated, public=public, stop_reason=stop)
    ident, token = child(current, parent, 'invalid-public')
    with pytest.raises(RunConflict):
        current.reusable_capo_response(ident, token)
    assert not rows(migrated, "SELECT id FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (ident,))


def test_unknown_cost_blocks_continuation_before_any_replay(migrated):
    current, parent, *_ = case(migrated, settled=False)
    with pytest.raises(BudgetBlocked, match='unresolved provider'):
        child(current, parent, 'unknown-paid')


def test_checkpoint_request_is_saved_atomically_before_provider_and_invalid_hash_rolls_back(migrated):
    current, _, cp, body, _ = case(migrated)
    original = rows(migrated, "SELECT payload_json FROM trade_idea_events WHERE kind='capo_request_saved'")
    saved = json.loads(original[0]['payload_json'])
    assert saved['checkpoint_sha256'] == _digest(cp)
    with sqlite3.connect(migrated) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE trade_idea_events SET payload_json='{}' WHERE kind='capo_request_saved'")
    other = current.create_run(v2_request(), idempotency_key='invalid-pre-dispatch')['run']['id']
    token = current.claim_run(other)
    save_cp(current, other, token, cp)
    with pytest.raises((ValueError, RunConflict), match='hash|fingerprint|request'):
        current.reserve_cost(other, 'not-dispatched', 'capo', body['model'], '1',
            request_sha256='f'*64, worker_token=token, capo_request_body=body)
    assert not rows(migrated, 'SELECT * FROM trade_idea_costs WHERE run_id=?', (other,))
    assert not rows(migrated, "SELECT id FROM trade_idea_events WHERE run_id=? AND kind='capo_request_saved'", (other,))


def test_policy_snapshot_rejects_unknown_and_wrong_role_effort(migrated):
    current = store(migrated)
    invalid = v2_request()
    invalid['execution_policy'] = 'unapproved-policy'
    with pytest.raises(ValueError, match='policy'):
        current.create_run(invalid, idempotency_key='unknown-policy')
    invalid = v2_request()
    invalid['models']['capo']['reasoning_effort'] = 'medium'
    with pytest.raises(ValueError, match='snapshot|effort'):
        current.create_run(invalid, idempotency_key='wrong-effort')


def test_changed_live_book_blocks_paid_replay_even_after_continuation_admission(migrated):
    current, parent, *_ = case(migrated)
    ident, token = child(current, parent, 'book-drift')
    with sqlite3.connect(migrated) as conn:
        conn.execute('UPDATE cash_state SET balance_cents=balance_cents+100')
    with pytest.raises(RunConflict, match='context changed'):
        current.reusable_capo_response(ident, token)
    assert not rows(migrated, "SELECT id FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (ident,))
