"""The normal weekly route resumes the Red Team's paid tool loop exactly once."""
import json

import httpx
import pytest

from bellomberg.agents import consigliere_multi as cm, red_team, chat_tools, weekly_lifecycle
from bellomberg.core import llm_client
from bellomberg.valuation import preparation_ai
from test_cablaggio_consigliere_multi import run_offline, _DeskFinto
from test_weekly_recovery import _store


NATIVE_RED_TEAM = red_team.run_red_team
NATIVE_WEEKLY_CONTRACT = cm._weekly_contract
consigliere_multi = cm


class Crash(BaseException):
    pass


def setup_provider(monkeypatch):
    requests, tool_calls, clients = [], [], []
    monkeypatch.setattr('bellomberg.core.llm_pricing._fx_usd_to_eur',
                        lambda: (.9, 'frozen synthetic FX fixture'))
    monkeypatch.setattr(preparation_ai, 'live_metadata', lambda model: {
        'id': model, 'context_length': 1_000_000,
        'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    def send(request):
        body = json.loads(request.content)
        requests.append(body)
        first = len(requests) == 1
        message = ({'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'verify-risk-once', 'type': 'function', 'function': {
                'name': 'get_portfolio_risk', 'arguments': '{}'}}]} if first else
            {'role': 'assistant', 'content': 'Critica completa: le fonti congelate dichiarano il rischio; nessuna cifra inventata.'})
        return httpx.Response(200, json={'id': 'red-response-' + str(len(requests)),
            'model': body['model'], 'choices': [{'message': message,
                'finish_reason': 'tool_calls' if first else 'stop'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'cost': .001}})
    client = llm_client.OpenRouterClient(api_key='offline-test', max_retries=0,
                                         trasporto=httpx.MockTransport(send))
    def create_client(**kwargs):
        clients.append(kwargs)
        return client
    monkeypatch.setattr(llm_client, 'OpenRouterClient', create_client)
    monkeypatch.setattr(red_team, 'run_red_team', NATIVE_RED_TEAM)
    # ZR 05/10: run NUOVA = contratto research (fundamentals_research_v1). Sul contratto legacy
    # (workbook, archiviato in 1326312) la ripresa analitica e' bloccata per decisione PM.
    monkeypatch.setattr(consigliere_multi, '_weekly_contract', NATIVE_WEEKLY_CONTRACT)
    def research_report(self, round_n):
        # Desk finto in modalita' research: rapporto in blackboard, nessun workbook.
        self.run_result_status = 'complete'
        self.bb.write(self.name, round_n, 'Synthetic research report %s R%d ' % (self.name, round_n) + 'x' * 200)
    monkeypatch.setattr(_DeskFinto, 'run', research_report)
    monkeypatch.setattr(chat_tools, 'dispatch', lambda *args, **kwargs:
                        tool_calls.append(args) or {'source': 'frozen synthetic risk', 'status': 'ok'})
    return requests, tool_calls, clients


@pytest.mark.parametrize('legacy_cap_upgrade', [False, True])
def test_weekly_red_tool_checkpoint_survives_crash_and_second_resume(run_offline, monkeypatch, legacy_cap_upgrade):
    requests, tool_calls, clients = setup_provider(monkeypatch)
    if legacy_cap_upgrade:
        monkeypatch.setattr(red_team, 'WEEKLY_RED_MAX_TOKENS', 4200)
    native_bind = weekly_lifecycle.bind_blackboard
    crashed = []
    def bind(bb, store):
        native_bind(bb, store)
        persist = bb.persist_run_checkpoint
        def save(event, payload):
            persist(event, payload)
            if event == 'red_team_tool' and not crashed:
                crashed.append(True)
                raise Crash('hard crash after durable Red Team tool result')
        bb.persist_run_checkpoint = save
    monkeypatch.setattr(weekly_lifecycle, 'bind_blackboard', bind)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    assert len(requests) == len(tool_calls) == 1
    monkeypatch.setattr(red_team, 'WEEKLY_RED_MAX_TOKENS', 128000)
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert result['status'] == 'completed'
    assert len(requests) == 2 and len(tool_calls) == 1
    assert [request['max_tokens'] for request in requests] == [4200 if legacy_cap_upgrade else 128000] * 2
    assert clients == [{'timeout': 240.0 if legacy_cap_upgrade else 3600.0, 'max_retries': 0}] * 2
    assert result['request_costs']['request_count'] == 2
    assert result['request_costs']['cost_usd'] == pytest.approx(.002)
    second = cm.run_multi_agent(resume_memo_id=store.memo_id, send_email=False)
    assert second['status'] == 'completed' and len(requests) == 2 and len(tool_calls) == 1
    assert second['first_error']['message'] == 'hard crash after durable Red Team tool result'


def test_ambiguous_red_tool_dispatch_blocks_repetition(run_offline, monkeypatch):
    requests, tool_calls, _ = setup_provider(monkeypatch)
    def crash_tool(*args, **kwargs):
        tool_calls.append(args)
        raise Crash('hard crash before tool receipt')
    monkeypatch.setattr(chat_tools, 'dispatch', crash_tool)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    for _ in range(2):
        with pytest.raises(ValueError, match='tool outcome unknown'):
            cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert len(requests) == len(tool_calls) == 1
    assert store.status()['status'] == 'incomplete'
    assert store.status()['first_error']['message'] == 'hard crash before tool receipt'
