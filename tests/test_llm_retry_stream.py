"""Retry del trasporto reale, con HTTP/SSE sintetici: niente provider o DB."""
import asyncio
import json

import httpx
import pytest

from bellomberg.core import llm_client as llm


def sse(*chunks, headers=None):
    body = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks)
    return httpx.Response(200, content=(body + 'data: [DONE]\n\n').encode(),
                          headers={'content-type': 'text/event-stream', **(headers or {})})


def chunk(delta=None, finish=None):
    return {'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}]}


def error(code=429):
    return {'error': {'code': code, 'message': 'temporary test error'}}


def success():
    return sse(chunk({'content': 'Complete.'}), chunk(finish='stop'))


@pytest.fixture(params=[False, True], ids=['sync', 'async'])
def run(request, monkeypatch):
    waits, requests, events = [], [], []
    monkeypatch.setattr(llm.time, 'sleep', waits.append)
    async def asleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(llm.asyncio, 'sleep', asleep)

    def execute(responses, retries=2):
        def transport(req):
            requests.append(json.loads(req.content))
            assert responses, 'unexpected additional request'
            return responses.pop(0)
        cls = llm.AsyncOpenRouterClient if request.param else llm.OpenRouterClient
        client = cls(api_key='test-only', max_retries=retries,
                     trasporto=httpx.MockTransport(transport))
        kwargs = dict(model='provider/test', max_tokens=20, messages=[
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call_test',
                'name': 'lookup', 'input': {'symbol': 'DEMO'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result',
                'tool_use_id': 'call_test', 'content': 'already acquired'}]},
        ])
        if request.param:
            async def work():
                try:
                    async with client.messages.stream(**kwargs) as stream:
                        async for event in stream:
                            events.append(event)
                        return await stream.get_final_message()
                finally:
                    await client._http.aclose()
            return asyncio.run(work())
        try:
            with client.messages.stream(**kwargs) as stream:
                events.extend(stream)
                return stream.get_final_message()
        finally:
            client._http.close()
    execute.waits, execute.requests, execute.events = waits, requests, events
    return execute


@pytest.mark.parametrize('header,delay', [('7', 7), ('0', 0), ('invalid', 1),
                                         ('NaN', 1), ('-3', 1)])
def test_http_retry_honors_delay_and_preserves_acquired_tools(run, header, delay):
    result = run([httpx.Response(429, json=error(), headers={'Retry-After': header}), success()])
    assert result.content[0].text == 'Complete.'
    assert run.waits == [delay]
    assert len(run.requests) == 2 and run.requests[0] == run.requests[1]
    assert run.requests[1]['messages'][-1]['content'] == 'already acquired'


def test_retry_after_http_date(run, monkeypatch):
    monkeypatch.setattr(llm.time, 'time', lambda: 0)
    run([httpx.Response(429, json=error(),
         headers={'Retry-After': 'Thu, 01 Jan 1970 00:00:11 GMT'}), success()])
    assert run.waits == [11]


def test_long_server_delay_does_not_retry_early(run):
    with pytest.raises(llm.APIStatusError) as exc:
        run([httpx.Response(429, json=error(), headers={'Retry-After': '3600'})])
    assert exc.value.status_code == 429
    assert len(run.requests) == 1 and not run.waits


def test_sse_rate_limit_before_output_recovers_without_duplicate_tools(run):
    result = run([sse(chunk({'role': 'assistant'}), error(),
                      headers={'Retry-After': '3'}), success()])
    assert result.content[0].text == 'Complete.'
    assert run.waits == [3] and len(run.requests) == 2
    assert run.requests[0] == run.requests[1]


@pytest.mark.parametrize('delta', [
    {'content': 'Partial.'},
    {'tool_calls': [{'index': 0, 'id': 'new_call', 'type': 'function',
                     'function': {'name': 'lookup', 'arguments': '{'}}]},
    {'reasoning': 'Hidden partial reasoning'},
])
def test_sse_never_replays_after_model_output(run, delta):
    with pytest.raises(llm.APIStatusError) as exc:
        run([sse(chunk(delta), error())])
    assert exc.value.status_code == 429
    assert len(run.requests) == 1 and not run.waits


def test_http_and_sse_share_one_retry_budget(run):
    with pytest.raises(llm.APIStatusError) as exc:
        run([httpx.Response(429, json=error()), sse(error()), sse(error())])
    assert exc.value.status_code == 429
    assert len(run.requests) == 3 and run.waits == [1, 2]


def test_closed_log_pipe_does_not_abort_a_recoverable_request(run, monkeypatch):
    def closed_pipe(*args, **kwargs):
        raise OSError('stdout closed')
    monkeypatch.setattr(llm, 'print', closed_pipe, raising=False)
    result = run([sse(error()), success()])
    assert result.content[0].text == 'Complete.'


@pytest.mark.parametrize('code,retries', [(402, 2), (403, 2), (429, 0)])
def test_sse_nonretryable_or_zero_budget_is_reported(run, code, retries):
    with pytest.raises(llm.APIStatusError) as exc:
        run([sse(error(code))], retries=retries)
    assert exc.value.status_code == code
    assert len(run.requests) == 1 and not run.waits


def scoped_journal(tmp_path):
    from bellomberg.core.request_journal import RequestJournal
    return RequestJournal(tmp_path / 'stream.sqlite', run_id='offline-stream',
        authorization={'source': 'test-launch'}, authorized_usd='1',
        metadata=lambda model: {'id': model, 'context_length': 1000,
            'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})


def scoped_success():
    return sse({'id': 'stream-receipt', 'model': 'provider/test',
        **chunk({'content': 'Complete.'})},
        {**chunk(finish='stop'), 'usage': {'cost': 0.00002}})


@pytest.mark.parametrize('failure', ['503', '504', 'sse_error', 'partial', 'empty'])
def test_scoped_stream_ambiguous_failure_never_replays_and_keeps_evidence(run, tmp_path, failure):
    journal = scoped_journal(tmp_path)
    response = (httpx.Response(int(failure), json=error(int(failure))) if failure.isdigit()
        else sse(error(503)) if failure == 'sse_error'
        else sse({'id': 'partial-receipt', 'model': 'provider/test', **chunk({'content': 'Partial.'})})
        if failure == 'partial' else sse(chunk()))
    with llm.request_scope(journal, phase='capo'):
        with pytest.raises((llm.APIStatusError, llm.APIConnectionError)) as exc:
            run([response])
    assert getattr(exc.value, 'request_id', None)
    assert len(run.requests) == 1 and not run.waits
    summary = journal.summary()
    assert summary['request_count'] == 1 and summary['cost_usd'] is None
    assert summary['reserved_usd'] > 0
    if failure == 'partial':
        import sqlite3
        with sqlite3.connect(journal.path) as db:
            recorded = db.execute('SELECT payload FROM checkpoints').fetchall()
        assert any('Partial.' in row[0] for row in recorded)


def test_scoped_stream_replay_after_reopen_preserves_one_charge(run, tmp_path):
    for index in range(3):
        with llm.request_scope(scoped_journal(tmp_path), phase='capo'):
            result = run([scoped_success()])
        assert result.content[0].text == 'Complete.'
    assert len(run.requests) == 1
    assert scoped_journal(tmp_path).summary()['cost_usd'] == 0.00002


def test_scoped_partial_stream_nan_is_diagnostic_and_keeps_original_failure(run, tmp_path):
    import sqlite3
    journal = scoped_journal(tmp_path)
    response = sse({'id': 'partial-nan', 'model': 'provider/test',
                    **chunk({'content': 'Partial evidence'}), 'usage': {'cost': float('nan')}})
    with llm.request_scope(journal, phase='capo'):
        with pytest.raises(llm.APIConnectionError, match='INCOMPLETA'):
            run([response])
    assert journal.summary()['cost_usd'] is None and journal.summary()['reserved_usd'] > 0
    with sqlite3.connect(journal.path) as db:
        checkpoints = db.execute('SELECT payload FROM checkpoints').fetchall()
        response, receipt = db.execute('SELECT response,receipt FROM requests').fetchone()
    assert any('Partial evidence' in row[0] and 'invalid_numeric' in row[0] for row in checkpoints)
    assert 'invalid_numeric' in response and 'INCOMPLETA' in receipt
    assert len(run.requests) == 1
