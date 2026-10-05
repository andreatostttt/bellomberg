"""One retry only after a provably unbilled failure; anything possibly billed stays unknown."""
import json
import sqlite3
import time

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.llm_client import APIConnectionError, APIStatusError
from test_trade_idea_paid_capo_checkpoint import db_path, migrated, case, v2_request, save_cp  # noqa: F401
from test_trade_idea_policy_runtime import gate


def _gate(migrated, key):
    current, _, cp, *_ = case(migrated)
    ident = current.create_run(v2_request(), idempotency_key=key)['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    return gate(current, ident, token), ident


def _states(path, ident):
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute(
            'SELECT status FROM trade_idea_costs WHERE run_id=? ORDER BY rowid', (ident,))]


def _call():
    return {'model': trade_idea.model_for_role('capo'), 'max_tokens': 32768,
            'thinking': {'type': 'effort', 'effort': 'low'}, 'system': 'S',
            'messages': [{'role': 'user', 'content': 'U'}]}


def _connect_error():
    exc = APIConnectionError('ConnectError: connection refused')
    exc.transport_phase = 'connect'
    return exc


def _admission_402(measured=True):
    metadata = {'reason': 'in_flight_budget_exhausted', 'limit_source': 'openrouter_in_flight_budget',
                'provider_name': None, 'headers': {'Retry-After': '3'}}
    exc = APIStatusError(402, 'In-flight budget exhausted | metadata: ' + json.dumps(metadata))
    if measured:
        exc.http_status = 402  # as llm_client._invia measures it (KA 05/10: rule from core/unbilled)
    return exc


class Settled(Exception):
    pass


@pytest.mark.parametrize('first_error', [_connect_error, _admission_402])
def test_provably_unbilled_failure_is_released_and_retried_exactly_once(migrated, monkeypatch, first_error):
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    budget, ident = _gate(migrated, 'unbilled-' + first_error.__name__)
    calls = []

    class Inner:
        def create(self, **call):
            calls.append(call)
            if len(calls) == 1:
                raise first_error()
            raise Settled('second request reached the transport')
    with pytest.raises(Settled):
        trade_idea._BudgetedMessages(Inner(), budget, 'capo').create(**_call())
    assert len(calls) == 2
    assert _states(migrated, ident)[:1] == ['released']


def test_possibly_billed_failure_stays_unknown_and_is_not_retried(migrated, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda seconds: pytest.fail('no retry wait for a 502'))
    budget, ident = _gate(migrated, 'billed-502')
    calls = []

    class Inner:
        def create(self, **call):
            calls.append(call)
            raise APIStatusError(502, 'upstream error')
    with pytest.raises(APIStatusError):
        trade_idea._BudgetedMessages(Inner(), budget, 'capo').create(**_call())
    assert len(calls) == 1 and _states(migrated, ident) == ['unknown']


def test_admission_402_without_a_measured_http_status_stays_unknown(migrated, monkeypatch):
    """KA (05/10, main): the shared stricter rule. A 402 body whose HTTP status was not
    measured by the client proves nothing: unknown, no retry."""
    monkeypatch.setattr(time, 'sleep', lambda seconds: pytest.fail('no retry wait'))
    budget, ident = _gate(migrated, 'unmeasured-402')
    calls = []

    class Inner:
        def create(self, **call):
            calls.append(call)
            raise _admission_402(measured=False)
    with pytest.raises(APIStatusError):
        trade_idea._BudgetedMessages(Inner(), budget, 'capo').create(**_call())
    assert len(calls) == 1 and _states(migrated, ident) == ['unknown']


def test_second_unbilled_failure_is_never_followed_by_a_third_attempt(migrated, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    budget, ident = _gate(migrated, 'unbilled-twice')
    calls = []

    class Inner:
        def create(self, **call):
            calls.append(call)
            raise _connect_error()
    with pytest.raises(APIConnectionError):
        trade_idea._BudgetedMessages(Inner(), budget, 'capo').create(**_call())
    assert len(calls) == 2 and _states(migrated, ident) == ['released', 'released']


def test_read_error_after_sending_is_not_treated_as_unbilled():
    assert trade_idea._provably_unbilled(APIConnectionError('ReadError: connection reset')) is None
    metadata = {'reason': 'in_flight_budget_exhausted', 'limit_source': 'openrouter_in_flight_budget',
                'provider_name': 'Anthropic'}
    assert trade_idea._provably_unbilled(APIStatusError(402, 'x | metadata: ' + json.dumps(metadata))) is None


@pytest.mark.parametrize('error, phase', [('connect', 'connect'), ('read', None)])
def test_client_marks_only_never_established_connections(error, phase):
    import httpx
    from bellomberg.core.llm_client import OpenRouterClient

    def handler(request):
        if error == 'connect':
            raise httpx.ConnectError('connection refused', request=request)
        raise httpx.ReadError('connection reset', request=request)
    client = OpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(handler))
    with pytest.raises(APIConnectionError) as info:
        client._invia({'model': 'x', 'messages': []})
    assert getattr(info.value, 'transport_phase', None) == phase


def test_read_timeout_after_sending_is_never_marked_as_never_connected():
    import httpx
    from bellomberg.core.llm_client import OpenRouterClient, APITimeoutError

    def handler(request):
        raise httpx.ReadTimeout('no answer after the request was sent', request=request)
    client = OpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(handler))
    with pytest.raises(APITimeoutError) as info:
        client._invia({'model': 'x', 'messages': []})
    assert getattr(info.value, 'transport_phase', None) is None
    assert trade_idea._provably_unbilled(info.value) is None


def test_stream_open_retries_at_most_once_after_unbilled_failures(migrated, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    budget, ident = _gate(migrated, 'stream-unbilled-twice')
    calls = []

    class Inner:
        def stream(self, **call):
            calls.append(call)
            raise _connect_error()
    stream = trade_idea._BudgetedStream(Inner(), budget, 'capo', _call())
    with pytest.raises(APIConnectionError):
        stream.__enter__()
    assert len(calls) == 2 and _states(migrated, ident) == ['released', 'released']


def test_seal_hash_without_gaps_is_frozen_to_the_historical_formula():
    from bellomberg.core.research_analysis import _thesis_identity, research_digest
    sealed = {'reports': {'macro': 'R1'}, 'dossier_sha256': 'd' * 64}
    assert research_digest(_thesis_identity(sealed)) == research_digest(
        {'reports': {'macro': 'R1'}, 'dossier_sha256': 'd' * 64})
    gapped = {**sealed, 'missing_reports': {'crypto': 'truncated'}}
    assert research_digest(_thesis_identity(gapped)) != research_digest(_thesis_identity(sealed))
