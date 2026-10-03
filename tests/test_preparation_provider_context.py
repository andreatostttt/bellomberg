"""Provider context validation must preserve all text, budget and paid replay."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import json

import pytest

from bellomberg.core.llm_client import costruisci_corpo
from test_preparation_ai import _proposer
from test_preparation_sections import _budgeted, _large_case
from test_trade_idea_pipeline import _priced_request, _gate, _call_kwargs
from test_trade_idea_store import db_path, migrated, store


def test_no_compression_policy_reaches_the_wire_and_ordinary_calls_stay_identical():
    kwargs = {'model': 'synthetic/model', 'max_tokens': 100,
              'messages': [{'role': 'user', 'content': 'Complete financial source'}]}
    ordinary = costruisci_corpo(**kwargs)
    assert costruisci_corpo(**kwargs, require_full_context=False) == ordinary
    strict = costruisci_corpo(**kwargs, require_full_context=True)
    assert strict.pop('plugins') == [{'id': 'context-compression', 'enabled': False}]
    assert strict == ordinary
    with pytest.raises(ValueError):
        costruisci_corpo(**kwargs, require_full_context='true')


def test_full_source_fallback_and_exact_paid_replay_preserve_the_completed_plan(tmp_path):
    source, calls, contract = _large_case(), [], {'schema': {}}
    source['completed_plan']['scenario_rationale']['bear'] = 'Completed economic reasoning. ' * 5000
    original = deepcopy(source)
    proposer = _budgeted(tmp_path, calls=calls)
    proposer.provider_context_check = True
    view = proposer.prepare_context(source, contract, source_dossier=source)
    assert view['provider_context_validation'] == 'openrouter_full_context_no_compression_v1'
    assert view['documents'] == source['documents']
    assert view['completed_plan'] == source['completed_plan']
    proposer(view, contract)
    assert calls[0]['require_full_context'] is True
    assert source == original
    proposer.metadata = lambda _: pytest.fail('paid replay must precede pricing')
    again = proposer.prepare_context(source, contract, source_dossier=source)
    assert proposer(again, contract) == {'done': True}
    assert len(calls) == 1


def test_strict_marker_without_opt_in_cannot_bypass_local_guard(tmp_path):
    proposer = _proposer(tmp_path, call=lambda **_: pytest.fail('provider called'))
    with pytest.raises(ValueError):
        proposer({'text': 'x' * 150000,
                  'provider_context_validation': 'openrouter_full_context_no_compression_v1'}, {})
    assert proposer.summary()['requests'] == 0


def test_strict_full_context_still_requires_the_entire_cost_reservation(tmp_path):
    proposer = _proposer(tmp_path, limit=1.31, call=lambda **_: pytest.fail('budget exceeded'))
    proposer.provider_context_check = True
    with pytest.raises(RuntimeError, match='budget'):
        proposer({'text': 'x' * 150000,
                  'provider_context_validation': 'openrouter_full_context_no_compression_v1'}, {})
    assert proposer.summary()['requests'] == 0


def test_provider_context_rejection_is_durable_and_never_retried(tmp_path):
    from bellomberg.core.llm_client import APIStatusError
    calls = []
    def reject(**kwargs):
        calls.append(kwargs)
        raise APIStatusError(400, 'Context length exceeded')
    proposer = _proposer(tmp_path, call=reject)
    proposer.provider_context_check = True
    dossier = {'text': 'x' * 150000,
               'provider_context_validation': 'openrouter_full_context_no_compression_v1'}
    with pytest.raises(APIStatusError):
        proposer(dossier, {})
    with pytest.raises(RuntimeError, match='unresolved'):
        proposer(dossier, {})
    assert len(calls) == 1
    assert proposer.summary()['unknown_requests'] == 1


def test_native_gate_reserves_full_context_only_for_preparation_and_revision(migrated):
    from bellomberg.agents import trade_idea
    current = store(migrated)
    payload = _priced_request()
    run_id = current.create_run(payload, idempotency_key='strict-context')['run']['id']
    current.claim_run(run_id)
    gate = _gate(current, run_id, payload)
    kwargs = {**_call_kwargs(), 'model': trade_idea.model_for_role('aux'),
              'require_full_context': True,
              'messages': [{'role': 'user', 'content': 'source ' * 100000}]}
    with pytest.raises(ValueError):
        gate._reserve(kwargs, 'specialist:fundamentals')
    assert current.get_run(run_id)['cost']['requests'] == 0
    for role in ('aux:preparation', 'aux:revision'):
        request_id, accepted = gate._reserve(kwargs, role)
        assert costruisci_corpo(**accepted)['plugins'] == [{'id': 'context-compression', 'enabled': False}]
        assert current.get_run(run_id)['cost']['reserved_usd'] == str(Decimal('0.500200000'))
        current.reconcile_cost(run_id, request_id, charged_usd='0.001', usage={'cost_usd': '0.001'},
                               receipt={'response_id': request_id, 'model': kwargs['model']})
