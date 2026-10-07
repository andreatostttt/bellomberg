"""Weekly evidence prompt contracts, native checkpoints and offline SDK boundary."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from test_tetto_specialisti_16k import bb, _Resp, _TextBlock, _Usage
from test_weekly_memory_memo15 import db
from bellomberg.agents import red_team, consigliere_multi as cm
from bellomberg.agents.specialists.base import Specialist
from bellomberg.agents.specialists.eventdesk import EventDeskSpecialist
from bellomberg.agents.weekly_lifecycle import bind_blackboard
from bellomberg.storage.weekly_run_store import WeeklyRunStore, WeeklyRunBlocked
from bellomberg.core import llm_client

POLICY='weekly-evidence-prompts/1'
MISSING=object()

class Client:
    def __init__(self): self.calls=[]; self.messages=self
    def create(self,**kw):
        self.calls.append(deepcopy(kw))
        return _Resp('end_turn',[_TextBlock('Complete synthetic report with declared gaps. '*35)],_Usage())

def attach(bb,db,marker=POLICY,priming=MISSING):
    contract={} if marker is MISSING else {'evidence_prompt_policy':marker}
    contract.update(roster=[], r2_specialists=[])
    mid=db.save_memo('[IN PROGRESS]')
    store=WeeklyRunStore(db,mid,context={'contract_version':1,'contract':contract,'language':'it',
        'research_started_at':'2032-01-01T10:00:00+00:00','portfolio':{'positions':[]}})
    bb.run_scope='weekly';bind_blackboard(bb,store)
    if priming is MISSING: priming={'beta_reconcile':{'verdict':'UNRELIABLE','beta_per_decisioni':False,
        'betas':{'private_a':-123.456,'private_b':789.123},'threshold':.3,'calcolato_il':'2031-12-31T12:00:00'},
        'freshness_report':{'checked':2,'fresh':0,'stale':['CPI: osservazione del 2031-01-01'],
                            'unknown':['funding: n.d. - data osservazione assente']}}
    if priming is not None: store.complete('priming',priming)
    return store

def actor(bb,monkeypatch,name='eventdesk'):
    client=Client()
    cls=type('TestDesk',(Specialist,),{'name':name,'role':'offline','system_prompt':
        EventDeskSpecialist.system_prompt if name=='eventdesk' else 'Original synthetic quant prompt.','tools_used':[]})
    desk=cls(bb,client=client)
    monkeypatch.setattr(desk,'_build_round_context',lambda n:'Original round context')
    monkeypatch.setattr(desk,'compute_score',lambda:pytest.fail('Q must not compute score'))
    return desk,client

@pytest.fixture(autouse=True)
def no_recompute(monkeypatch):
    from bellomberg.portfolio import advanced_metrics
    from bellomberg.core import freshness
    monkeypatch.setattr(advanced_metrics,'reconcile_betas',lambda *a,**k:pytest.fail('beta recompute'))
    monkeypatch.setattr(freshness,'check_and_update',lambda *a,**k:pytest.fail('freshness recompute'))
    monkeypatch.setattr(red_team,'_preventivo_prima_dell_invio',lambda *a:None)

def test_eventdesk_real_request_requires_evidence(bb,db,monkeypatch):
    attach(bb,db);desk,client=actor(bb,monkeypatch)
    desk._run_loop(1)
    assert len(client.calls)==1
    text=str(client.calls[0]['system'])
    assert 'Mercato davvero inesistente = dichiarato' not in text
    assert 'variazione vs 7 e 30 giorni' not in text
    assert 'PRICING NON VALUTABILE' in text
    assert 'delta_7gg (n.d. senza storia documentata)' in text
    assert 'stesso mercato e outcome' in text

def test_quant_r2_receives_saved_priming_not_live(bb,db,monkeypatch):
    attach(bb,db);bb.data['_beta_reconcile']={'verdict':'RECONCILED','beta_per_decisioni':True,'betas':{'LIVE':900}}
    desk,client=actor(bb,monkeypatch,'quant');desk._run_loop(2)
    text=str(client.calls[0]['messages'])
    assert 'UNRELIABLE' in text and '2031-12-31T12:00:00' in text
    assert 'CPI: osservazione' in text and 'funding: n.d.' in text
    assert '123.456' not in text and '789.123' not in text and 'LIVE' not in text
    assert 'NON' in text and 'decision' in text

def test_redteam_native_loop_transports_saved_diagnostics(bb,db,monkeypatch):
    attach(bb,db);client=Client();monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **k:client)
    red_team._run_red_team_loop(bb,False,'synthetic/model','Frozen original user',[], 'red_team:R1',None)
    assert len(client.calls)==1
    text=str(client.calls[0]);assert 'UNRELIABLE' in text and 'CPI: osservazione' in text
    assert 'numero vero accanto' not in text and 'quelli ufficiali' not in text
    assert '123.456' not in text and '789.123' not in text

@pytest.mark.parametrize('marker',[None,'',False,{},'weekly-evidence-prompts/999'])
@pytest.mark.parametrize('consumer',['quant','red_team'])
def test_present_invalid_policy_blocks_before_client(bb,db,monkeypatch,marker,consumer):
    attach(bb,db,marker);client=Client();monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **k:client)
    if consumer=='quant': desk,client=actor(bb,monkeypatch,'quant');invoke=lambda:desk._run_loop(2)
    else: invoke=lambda:red_team._run_red_team_loop(bb,False,'synthetic/model','user',[],None,None)
    with pytest.raises(WeeklyRunBlocked):invoke()
    assert client.calls==[]

def test_new_and_historical_contract_policy(monkeypatch):
    monkeypatch.setattr(llm_client,'modello_o_buco',lambda *a:'synthetic-model')
    new=cm._weekly_contract();assert new['evidence_prompt_policy']==POLICY
    assert 'evidence_prompt_policy' not in cm._resume_publication_contract(new,{})
    assert cm._resume_publication_contract(new,{'evidence_prompt_policy':None})['evidence_prompt_policy'] is None

def test_public_red_invalid_policy_precedes_best_effort_config(bb,db,monkeypatch):
    attach(bb,db,None)
    bb.record_run_failure = None  # Public best-effort caller without run failure callback.
    calls=[]
    monkeypatch.setattr(llm_client,'modello',lambda *a:calls.append('config') or (_ for _ in ()).throw(ValueError('missing config')))
    with pytest.raises(WeeklyRunBlocked):red_team.run_red_team(bb)
    assert calls==[]

@pytest.mark.parametrize('consumer',['quant','red_team'])
def test_missing_priming_blocks_without_client(bb,db,monkeypatch,consumer):
    attach(bb,db,priming=None);client=Client();monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **k:client)
    if consumer=='quant':desk,client=actor(bb,monkeypatch,'quant');invoke=lambda:desk._run_loop(2)
    else:invoke=lambda:red_team._run_red_team_loop(bb,False,'synthetic/model','user',[],None,None)
    with pytest.raises(WeeklyRunBlocked,match='priming'):invoke()
    assert client.calls==[]

def block_data(bb):
    from bellomberg.core.evidence_prompt_policy import diagnostic_block
    text=diagnostic_block(bb)
    return json.loads(text[text.index('{'):])

@pytest.mark.parametrize('fields',[{}, {'beta_reconcile':None,'freshness_report':None},
    {'beta_reconcile':{'error':'PRIVATE https://secret'},'freshness_report':{'error':'PRIVATE https://secret'}},
    {'beta_reconcile':{'verdict':'PRIVATE'},'freshness_report':{'checked':True,'fresh':1,'stale':[],'unknown':[]}}])
def test_missing_invalid_diagnostics_do_not_invent_green_or_leak(bb,db,fields):
    attach(bb,db,priming=fields);out=block_data(bb)
    assert out['beta']['status']=='UNAVAILABLE' and out['beta']['decision_eligible'] is False
    assert out['freshness']['status']=='UNAVAILABLE'
    assert 'PRIVATE' not in str(out) and 'https://' not in str(out)

@pytest.mark.parametrize('eligible',[True,False])
def test_reconciled_clearance_requires_explicit_true(bb,db,eligible):
    attach(bb,db,priming={'beta_reconcile':{'verdict':'RECONCILED','beta_per_decisioni':eligible,
        'betas':{'advanced_metrics_twr':1.123,'portfolio_risk_spy':1.122},'beta_consensus':1.1225},
        'freshness_report':{'checked':0,'fresh':0,'stale':[],'unknown':[]}})
    out=block_data(bb);assert out['beta']['decision_eligible'] is eligible
    assert ('1.123' in str(out)) is eligible
    assert out['freshness']['checked']==0 and 'Zero controlli' in out['freshness']['diagnostic_text']

@pytest.mark.parametrize('bad',[True,-1,1.5,'2',None])
def test_freshness_invalid_count_is_explicit(bb,db,bad):
    attach(bb,db,priming={'freshness_report':{'checked':bad,'fresh':0,'stale':[],'unknown':[]}})
    assert block_data(bb)['freshness']['status']=='UNAVAILABLE'

def test_normalizer_exception_is_safe_visible_and_store_integrity_blocks(bb,db,monkeypatch):
    from bellomberg.core import evidence_prompt_policy as ep
    store=attach(bb,db)
    monkeypatch.setattr(ep,'_beta',lambda _:(_ for _ in ()).throw(ValueError('PRIVATE')))
    out=block_data(bb);assert out['beta']['diagnostic_text'].startswith('CHECK_UNAVAILABLE') and 'PRIVATE' not in str(out)
    with db._conn() as conn:conn.execute("UPDATE weekly_checkpoints SET payload_json='{}' WHERE memo_id=? AND stage='priming'",(store.memo_id,))
    with pytest.raises(WeeklyRunBlocked):block_data(bb)

def test_mandate_compilation_fallback_keeps_selected_event_template(bb,db,monkeypatch):
    from bellomberg.core import mandato_pm
    attach(bb,db);desk,client=actor(bb,monkeypatch)
    monkeypatch.setattr(mandato_pm,'compila_o_dichiara',lambda *a:(_ for _ in ()).throw(RuntimeError('synthetic')))
    desk._run_loop(1);text=str(client.calls[0]['system'])
    assert 'PRICING NON VALUTABILE' in text and 'Mercato davvero inesistente' not in text
    assert 'MANDATO n.d.' in text

def test_template_drift_blocks_instead_of_silent_legacy(bb,db,monkeypatch):
    attach(bb,db);desk,client=actor(bb,monkeypatch);desk.system_prompt='unrecognized template'
    with pytest.raises(WeeklyRunBlocked,match='template'):desk._run_loop(1)
    assert client.calls==[]

def test_quant_real_round_context_keeps_score_uninvoked(bb,db,monkeypatch):
    attach(bb,db);desk,client=actor(bb,monkeypatch,'quant')
    monkeypatch.setattr(desk,'_build_round_context',Specialist._build_round_context.__get__(desk))
    desk._run_loop(2)
    text=str(client.calls[0]['messages']);assert 'ROUND 2 - CROSS-REVIEW' in text and 'UNRELIABLE' in text

@pytest.mark.parametrize('consumer',['quant','red_team'])
@pytest.mark.parametrize('marker',[MISSING,POLICY])
def test_completed_native_checkpoint_ignores_changed_live_diagnostics(bb,db,monkeypatch,consumer,marker):
    store=attach(bb,db,marker);client=Client();monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **k:client)
    if consumer=='quant':desk,client=actor(bb,monkeypatch,'quant');invoke=lambda:desk._run_loop(2)
    else:
        bb.write('quant',1,'Synthetic report')
        monkeypatch.setattr(llm_client,'modello',lambda *a:'synthetic/model')
        invoke=lambda:red_team.run_red_team(bb)
    first=invoke();requests=deepcopy(client.calls);frozen=deepcopy(bb.specialist_checkpoints)
    bb.data['_beta_reconcile']={'LIVE':999};bb.data['_freshness_report']={'LIVE':True}
    # Rebind restores the native persisted checkpoint, not an in-memory mock stage.
    bind_blackboard(bb,WeeklyRunStore(db,store.memo_id))
    assert invoke()==first and client.calls==requests and bb.specialist_checkpoints==frozen
    text=str(requests)
    assert ('weekly-evidence-prompts/1' in text) is (marker is not MISSING)
    if marker is MISSING and consumer=='red_team':
        from bellomberg.core.language import prompt_for_language
        assert requests[0]['system']==prompt_for_language(red_team.RED_TEAM_PROMPT)

@pytest.mark.parametrize('scope',['trade_idea','weekly'])
def test_scope_and_absent_policy_keep_original_template_and_no_priming_read(bb,db,scope):
    from bellomberg.core.evidence_prompt_policy import select_template,diagnostic_block
    store=attach(bb,db,MISSING if scope=='weekly' else None,priming=None);bb.run_scope=scope
    store.get=lambda *_:pytest.fail('legacy/TI priming read')
    assert select_template(bb,'eventdesk',EventDeskSpecialist.system_prompt)==EventDeskSpecialist.system_prompt
    assert select_template(bb,'red_team',red_team.RED_TEAM_PROMPT)==red_team.RED_TEAM_PROMPT
    assert diagnostic_block(bb)==''

@pytest.mark.parametrize('marker',[MISSING,POLICY])
def test_paid_red_receipt_replay_after_crash_preserves_body_and_cost(bb,db,monkeypatch,tmp_path,marker):
    import httpx
    from bellomberg.core.request_journal import RequestJournal
    store=attach(bb,db,marker);sent=[]
    from bellomberg.agents import chat_tools
    schema = deepcopy(chat_tools.TOOL_DEFINITIONS)
    def send(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'synthetic-q-paid','model':'synthetic/model',
            'choices':[{'message':{'role':'assistant','content':'Complete synthetic critique with explicit evidence limits. '*20},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'cost':.02}})
    client=llm_client.OpenRouterClient(api_key='offline-test',max_retries=0,trasporto=httpx.MockTransport(send))
    monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **k:client)
    def journal():return RequestJournal(tmp_path/'requests.sqlite',run_id=store.run_id,authorized_usd=10,
        authorization={'source':'offline-test'},metadata=lambda model:{'id':model,'context_length':400000,
            'top_provider':{'max_completion_tokens':128000},'pricing':{'prompt':'0.000001','completion':'0.000002'}})
    class Crash(BaseException):pass
    persisted=bb.persist_run_checkpoint;crashes=[]
    def crash(event,payload):
        if event=='red_team_report':crashes.append(event);raise Crash()
        persisted(event,payload)
    bb.persist_run_checkpoint=crash
    with llm_client.request_scope(journal(),phase='red_team'):
        with pytest.raises(Crash):red_team._run_red_team_loop(bb,False,'synthetic/model','Frozen original user',schema, 'red_team:R1',None)
    assert len(sent)==1 and crashes==['red_team_report']
    with journal()._db() as conn:before=[dict(row) for row in conn.execute('SELECT * FROM requests')]
    bind_blackboard(bb,WeeklyRunStore(db,store.memo_id))
    saved=deepcopy(bb.specialist_checkpoints['red_team:R1']);saved.pop('sha256')
    assert saved['status']=='running'
    bb.data['_beta_reconcile']={'LIVE':999};bb.data['_freshness_report']={'fresh':999}
    from bellomberg.core import evidence_prompt_policy as ep
    monkeypatch.setattr(ep,'diagnostic_block',lambda *_:pytest.fail('paid context rebuilt'))
    with llm_client.request_scope(journal(),phase='red_team'):
        result=red_team._run_red_team_loop(bb,False,'synthetic/model',saved['user_msg'],saved['tools_schema'],'red_team:R1',saved)
    assert result and len(sent)==1
    with journal()._db() as conn:after=[dict(row) for row in conn.execute('SELECT * FROM requests')]
    assert after==before
    assert bb.specialist_checkpoints['red_team:R1']['user_msg']==saved['user_msg']

@pytest.mark.parametrize('consumer',['eventdesk','red_team'])
def test_trade_idea_native_request_unchanged_by_weekly_marker(bb,db,monkeypatch,consumer):
    requests=[]
    for marker in (MISSING,POLICY,None):
        attach(bb,db,marker,priming=None);bb.run_scope='trade_idea';bb.target_ticker='SYNTH';bb.model_phase='building'
        bb.source_qualification={};bb.budget_gate=SimpleNamespace(catalog_snapshot=__import__('_trade_idea_contratto').contratto_default(),wrap_client=lambda client,**kw:client)
        if consumer=='eventdesk':
            desk,client=actor(bb,monkeypatch);desk._run_loop(1)
        else:
            client=Client();monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **k:client)
            red_team._run_red_team_loop(bb,True,'synthetic/model','Frozen TI input',[],None,None)
        assert len(client.calls)==1
        requests.append(client.calls[0])
    assert requests[0]==requests[1]==requests[2]
    assert 'weekly-evidence-prompts' not in str(requests)

def test_quant_ready_crash_reuses_original_initial_context(bb,db,monkeypatch):
    store=attach(bb,db);desk,client=actor(bb,monkeypatch,'quant')
    persisted=bb.persist_run_checkpoint;crashes=[]
    class Crash(BaseException):pass
    def crash(event,payload):
        persisted(event,payload)
        if event=='specialist_ready':crashes.append(event);raise Crash()
    bb.persist_run_checkpoint=crash
    with pytest.raises(Crash):desk._run_loop(2)
    assert not client.calls and crashes==['specialist_ready']
    original=deepcopy(bb.specialist_checkpoints['quant:R2'])
    bind_blackboard(bb,WeeklyRunStore(db,store.memo_id))
    from bellomberg.core import evidence_prompt_policy as ep
    monkeypatch.setattr(ep,'diagnostic_block',lambda *_:pytest.fail('ready checkpoint changed'))
    monkeypatch.setattr(desk,'_build_round_context',lambda *_:pytest.fail('saved context rebuilt'))
    bb.data['_beta_reconcile']={'LIVE':999}
    desk._run_loop(2)
    assert len(client.calls)==1 and client.calls[0]['messages'][0]['content']==original['initial_context']
    assert 'UNRELIABLE' in original['initial_context']

@pytest.mark.parametrize('n_engines',[0,1,2,3])
def test_reconciled_schema_requires_two_real_engines(bb,db,n_engines):
    names=['advanced_metrics_twr','portfolio_risk_spy','factor_model_mkt']
    values={name:1.0+index*.01 for index,name in enumerate(names[:n_engines])}
    attach(bb,db,priming={'beta_reconcile':{'verdict':'RECONCILED','beta_per_decisioni':True,'betas':values}})
    beta=block_data(bb)['beta']
    assert beta['decision_eligible'] is (n_engines>=2)
    assert beta['status']==('AVAILABLE' if n_engines>=2 else 'UNAVAILABLE')
    assert ('beta riconciliato, utilizzabile' in beta['diagnostic_text']) is (n_engines>=2)

@pytest.mark.parametrize('bad',[True,None,'1.1',{'bad':'shape'}])
def test_second_invalid_engine_never_satisfies_reconciled_schema(bb,db,bad):
    attach(bb,db,priming={'beta_reconcile':{'verdict':'RECONCILED','beta_per_decisioni':True,
        'betas':{'advanced_metrics_twr':1.0,'portfolio_risk_spy':bad}}})
    beta=block_data(bb)['beta'];assert beta['status']=='UNAVAILABLE' and beta['decision_eligible'] is False
