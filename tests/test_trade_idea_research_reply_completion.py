"""Native reply-only recovery preserves paid research through PDF/MIME delivery.

The provider transports are synthetic; the shared native worker, specialists,
budget ledger, checkpoints, final Capo validation and PDF renderer are real.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists import base
from bellomberg.core.llm_client import Usage
from test_trade_idea_no_workbook_e2e import (
    no_workbook_case, _assert_complete, _native_rows,
)
from _smtp_cattura import smtp
from test_trade_idea_source_research import frozen_clock
from test_trade_idea_store import db_path, migrated


def _closeout_client(calls, *, objection_ids):
    """Only a bounded reply task may reach this synthetic specialist provider."""
    class Messages:
        def create(self, **kwargs):
            calls.append(deepcopy(kwargs))
            assert 'research_objection_completion' in str(kwargs['messages'])
            assert kwargs['model'] == 'meta/muse-spark-1.3'
            assert kwargs['max_tokens'] == 128000
            names = {row['name'] for row in kwargs.get('tools') or []}
            assert names == {'respond_trade_idea_objection', 'read_candidate_source',
                             'read_company_dossier', 'ask_specialist'}
            used = {item.get('input', {}).get('objection_id')
                for row in kwargs['messages'] if isinstance(row.get('content'), list)
                for item in row['content'] if isinstance(item, dict)
                and item.get('name') == 'respond_trade_idea_objection'}
            missing = [ident for ident in objection_ids if ident not in used]
            blocks = ([SimpleNamespace(type='tool_use', id='offline-reply-' + str(len(calls)),
                name='respond_trade_idea_objection', input={
                    'objection_id': missing[0],
                    'response': 'The original report retains this evidentiary limitation. '
                        'The available source does not establish the disputed conclusion; '
                        'no acquisition accretion or trade is assumed.',
                    'state': 'answered', 'evidence_refs': ['read_company_dossier'],
                    'model_revision_id': None})] if missing else
                [SimpleNamespace(type='text', text='The missing replies are registered; '
                    'the original research remains unchanged and its limitations remain explicit. ' * 20)])
            return SimpleNamespace(id='offline-closeout-response-' + str(len(calls)),
                model=kwargs['model'], stop_reason='tool_use' if missing else 'end_turn',
                content=blocks, usage=Usage(input_tokens=120, output_tokens=180,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0, cost_usd=0.001))

    class Client:
        def __init__(self, **_kwargs):
            import httpx
            self._http = SimpleNamespace(timeout=httpx.Timeout(450))
            self.messages = Messages()
    return Client


@pytest.mark.parametrize('crash_after_closeout', [False, True])
def test_native_missing_reply_recovery_keeps_all_paid_reports_and_delivers_once(
        no_workbook_case, monkeypatch, crash_after_closeout):
    case = no_workbook_case
    current = case.current
    ident = current.create_run(case.request, idempotency_key='reply-closeout-original')['run']['id']
    # Reproduce the observed state: R2 report complete but its formal reply did
    # not reach the ledger. No report or paid receipt is rewritten for recovery.
    native_handler = trade_idea.handle_trade_idea_review_tool
    native_closeout = getattr(trade_idea, '_complete_research_objection_replies', None)

    def omitted_reply(board, desk, name, inputs):
        if desk == 'eventdesk' and name == 'respond_trade_idea_objection':
            return {'ok': False, 'error': 'Frozen omitted formal reply before forced report'}
        return native_handler(board, desk, name, inputs)

    # research/3 (PM 03/10): una risposta formale mancante non ferma piu' la run (resta
    # obiezione aperta e dichiarata dopo il turno di completamento). Lo stato osservato
    # -- R2 pagato, risposta assente, completamento non ancora eseguito -- si riproduce
    # con un'interruzione PRIMA del turno di sola risposta; la garanzia provata resta la
    # stessa: il recupero paga solo il turno di risposta, conserva i report e consegna una volta.
    def crash_before_closeout(_board):
        raise RuntimeError('Offline crash before the reply-only completion task')

    with monkeypatch.context() as patch:
        patch.setattr(trade_idea, 'handle_trade_idea_review_tool', omitted_reply)
        patch.setattr(trade_idea, '_complete_research_objection_replies', crash_before_closeout)
        stopped = case.execute(ident, 'original')
    assert stopped['run']['technical_status'] == 'incomplete'
    assert 'before the reply-only completion task' in stopped['run']['reason']
    assert case.state['capo_calls'] == 0 and not case.smtp[0]
    cp = deepcopy(stopped['progress']['checkpoint'])
    assert [row['objection']['id'] for row in cp['data']['_objections']
            if row['objection']['material'] and not row.get('response')] == ['challenge-eventdesk']
    originals = {desk: deepcopy(cp['data'][desk]) for desk in trade_idea.TRADE_IDEA_DESKS}
    seal = deepcopy(cp['data']['_research_thesis'])
    original_rows = _native_rows(case.database, ident)
    original_calls = len(case.providers)
    original_source_calls = len(case.transport.requests)
    closeout_calls = []
    monkeypatch.setattr(base, 'OpenRouterClient', _closeout_client(closeout_calls,
        objection_ids=['challenge-eventdesk']))
    child = current.create_continuation(ident, idempotency_key='reply-closeout-first',
        authorize_new_requests=True)['run']['id']
    if crash_after_closeout:
        def closeout_then_crash(board):
            native_closeout(board)
            raise RuntimeError('Offline crash after durable reply-only task completion')
        with monkeypatch.context() as patch:
            patch.setattr(trade_idea, '_complete_research_objection_replies', closeout_then_crash)
            interrupted = case.execute(child, 'reply-recorded')
        assert interrupted['run']['technical_status'] == 'incomplete'
        assert 'after durable reply-only' in interrupted['run']['reason']
        assert len(closeout_calls) == 2 and case.state['capo_calls'] == 0
        task_cp = interrupted['progress']['checkpoint']['specialist_checkpoints']
        assert len(task_cp) == len(cp['specialist_checkpoints']) + 1
        assert all(row['status'] == 'complete' for key, row in task_cp.items()
                   if key not in cp['specialist_checkpoints'])
        saved_child = _native_rows(case.database, child)
        final_id = current.create_continuation(child, idempotency_key='reply-closeout-second',
            authorize_new_requests=True)['run']['id']
    else:
        final_id = child
    result = case.execute(final_id, 'final')
    assert result['run']['technical_status'] == 'completed', result['run']['reason']
    assert len(closeout_calls) == 2
    assert len(case.providers) == original_calls + 1  # only the native Capo stream
    assert case.state['capo_calls'] == 1
    final_cp = result['progress']['checkpoint']
    assert {desk: final_cp['data'][desk] for desk in originals} == originals
    assert final_cp['data']['_research_thesis'] == seal
    assert final_cp['data']['_desk_research_reviews'] == cp['data']['_desk_research_reviews']
    assert all(final_cp['specialist_checkpoints'][key] == value
               for key, value in cp['specialist_checkpoints'].items())
    assert len(case.transport.requests) == original_source_calls
    assert _native_rows(case.database, ident) == original_rows
    if crash_after_closeout:
        assert _native_rows(case.database, child) == saved_child
    # The common assertion checks real PDF pages, attachment bytes and SMTP
    # receipt. Include the separately injected reply transport in its counter.
    case.providers.extend(closeout_calls)
    manifest = _assert_complete(case, result, judgment='rejected', destination='research')
    assert result['cost']['requests'] == original_calls + 3
    assert Decimal(result['cost']['charged_usd']) == Decimal('0.001') * (original_calls + 3)
    assert result['progress']['remaining_work']['remaining'] == []
    counts = (len(case.providers), len(closeout_calls), len(case.smtp[0]))
    for receipt in manifest['exact_artifact_receipts']:
        path = Path(receipt['path'])
        assert path.is_relative_to(case.root)
        path.unlink()
    for _ in range(2):
        trade_idea.deliver_trade_idea(current, final_id, output_dir=case.root / 'final', send_email=True)
        recovered = current.get_run(final_id)
        assert recovered['artifacts'] == manifest
        for receipt in manifest['exact_artifact_receipts']:
            assert sha256(Path(receipt['path']).read_bytes()).hexdigest() == receipt['sha256']
    assert (len(case.providers), len(closeout_calls), len(case.smtp[0])) == counts
    assert not list(case.root.rglob('*.xlsx'))
