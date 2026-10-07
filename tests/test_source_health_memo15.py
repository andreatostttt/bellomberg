"""Passive health with native SQLite checkpoints; all external services fake."""
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import pytest
from bellomberg.core import source_health as h
from bellomberg.storage.weekly_run_store import WeeklyRunStore, WeeklyRunBlocked
from bellomberg.agents.weekly_lifecycle import bind_blackboard
from test_cablaggio_consigliere_multi import run_offline

@pytest.fixture
def store(tmp_path):
    class DB:
        db_path=str(tmp_path/'synthetic.sqlite')
        @contextmanager
        def _conn(self):
            conn=sqlite3.connect(self.db_path);conn.row_factory=sqlite3.Row
            try:
                with conn:yield conn
            finally:conn.close()
    db=DB()
    with db._conn() as c:
        c.execute('CREATE TABLE memos (id INTEGER PRIMARY KEY, notes TEXT)')
        c.execute("INSERT INTO memos VALUES (1,'synthetic')")
    return WeeklyRunStore(db,1,context={'contract_version':1,'contract':{'source_health_policy':h.POLICY},
        'language':'it','portfolio':{'positions':[]},'research_started_at':'2032-01-01T10:00:00+00:00'})


def board_for(store):
    bb=SimpleNamespace(_lock=threading.RLock(),data={},tool_receipts=[],tool_log=[])
    bind_blackboard(bb,store)
    return bb


def test_nonempty_macro_with_failed_children_never_ok():
    payload={'indicators':{'first':{'value':2,'date':'2032-01-01'},'second':{'error':'HTTP 429'}}}
    before=deepcopy(payload)
    out=h.normalize_health('macro_dashboard',payload)
    assert out['status']=='PARTIAL' and out['coverage']['usable']==1 and out['coverage']['observed']==2
    assert payload==before


def test_snapshot_checkpoint_recovery_no_reclassification(store,monkeypatch):
    bb=board_for(store)
    payload={'error':'HTTP 429 apiKey=PRIVATE_SENTINEL https://secret.invalid'}
    out=h.capture_health(bb,'tool-one','get_options_data',payload,desk='options',round_n=1)
    bb.persist_run_checkpoint('specialist_tool',{})
    assert out['observation']['status']=='RATE_LIMITED'
    block=h.checkpoint_source_health(store,'canonical memo')
    assert 'RATE_LIMITED' in block and 'PRIVATE_SENTINEL' not in block and 'https://' not in block
    saved=store.get(h.STAGE)
    monkeypatch.setattr(h,'normalize_health',lambda *a:pytest.fail('classifier repeated'))
    reopened=WeeklyRunStore(store.db,1)
    restored=board_for(reopened)
    assert h.capture_health(restored,'tool-one','get_options_data',payload,desk='options',round_n=1)==out
    assert h.checkpoint_source_health(reopened,'canonical memo')==block
    assert reopened.get(h.STAGE)==saved
    assert restored.tool_receipts==[] and restored.tool_log==[]
    with pytest.raises(WeeklyRunBlocked):h.checkpoint_source_health(reopened,'changed memo')


@pytest.mark.parametrize('legacy',['absent','validated'])
def test_legacy_policy_absence_and_saved_memo_unchanged(store,legacy):
    if legacy=='absent':store.context['contract'].pop('source_health_policy')
    else:store.complete('memo_validated',{'memo':'historical immutable'})
    bb=board_for(store)
    before=deepcopy(bb.data)
    assert h.capture_health(bb,'one','macro_dashboard',{},phase='preflight') is None
    assert h.checkpoint_source_health(store,'canonical')==''
    assert bb.data==before and store.get(h.STAGE) is None


def test_real_orchestrator_preflight_output_and_native_recovery(run_offline,monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.storage.memory_db import MemoryDB
    from test_weekly_recovery import _research_contract
    _research_contract(run_offline, monkeypatch)
    calls=[]
    monkeypatch.setattr(cm,'tool_get_macro_dashboard',lambda:calls.append('macro') or {'indicators':{'broken':{'error':'PRIVATE_SENTINEL http://secret.invalid token=secret'}}})
    stages=[];original=WeeklyRunStore.complete
    def complete(self,stage,*a,**kw):
        stages.append(stage);return original(self,stage,*a,**kw)
    monkeypatch.setattr(WeeklyRunStore,'complete',complete)
    cm.run_multi_agent(send_email=False)
    db=MemoryDB()
    with db._conn() as c:memo_id=c.execute('SELECT MAX(memo_id) FROM weekly_runs').fetchone()[0]
    saved=WeeklyRunStore(db,memo_id)
    assert saved.context['contract']['source_health_policy']==h.POLICY
    assert not any(x.startswith('macro_dashboard') for x in run_offline.catturato['bb'].data['_tool_health']['ok'])
    assert h.STAGE in stages and stages.index(h.STAGE)<stages.index('memo_validated')
    block=saved.get(h.STAGE)['block']
    assert 'ERROR' in block and 'PRIVATE_SENTINEL' not in block
    assert block in saved.get('memo_validated')['memo']
    assert 'SOURCE HEALTH' in run_offline.catturato['sizing_context']
    before=deepcopy(saved.get('memo_validated'));n=len(calls)
    cm.run_multi_agent(resume_memo_id=memo_id,delivery_only=True,send_email=False)
    assert len(calls)==n and saved.get('memo_validated')==before


def test_resume_preserves_policy_or_absence():
    from bellomberg.agents.action_validator import research_gate_enabled
    source=Path(__file__).parents[1]/'src/bellomberg/agents/consigliere_multi.py'
    node=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name=='_resume_publication_contract')
    scope={'research_gate_enabled':research_gate_enabled};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
    assert 'source_health_policy' not in scope[node.name]({'source_health_policy':h.POLICY},{})
    assert scope[node.name]({}, {'source_health_policy':h.POLICY})['source_health_policy']==h.POLICY

def test_native_specialist_capture_crash_replay_no_extra_ai_or_tool(store,tmp_path,monkeypatch):
    import httpx
    from bellomberg.agents.specialists import base
    from bellomberg.core import llm_client, llm_pricing
    from bellomberg.core.request_journal import RequestJournal
    monkeypatch.setattr(base.Blackboard,'_write_heartbeat',lambda self:None)
    monkeypatch.setattr(base.Specialist,'_build_round_context',lambda self,rnd:'frozen initial task')
    monkeypatch.setattr(base.Specialist,'_model_for_round',lambda self,rnd:'test/model')
    monkeypatch.setattr(base.Specialist,'_build_tools_schema',lambda self:[{'name':'get_options_data','description':'fake','input_schema':{'type':'object','properties':{}}}])
    monkeypatch.setattr(base,'USE_PROMPT_CACHING',False)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core,'current_facts',SimpleNamespace(current_facts_block=lambda:'',favorites_block=lambda:'',pm_theses_block=lambda:''),raising=False)
    monkeypatch.setattr(llm_pricing,'_resolve_fx_usd_to_eur',lambda:(.9,'live'))
    calls=[];tools=[]
    result={'_source':'polygon options summary','expiry_used':'2099-01-16','put_call_oi_ratio':1,
            'coverage':{'status':'PARTIAL','pages_received':1,'errors':['HTTP 429'],'rows_observed':2}}
    monkeypatch.setattr(base.Specialist,'_execute_meta_tool',lambda self,name,args:tools.append((name,args)) or deepcopy(result))
    def send(request):
        body=json.loads(request.content);calls.append(body)
        message=({'tool_calls':[{'id':'tool-one','type':'function','function':{'name':'get_options_data','arguments':'{"ticker":"ZZTEST"}'}}]}
                 if len(calls)==1 else {'content':'Verified synthetic report. '*80})
        finish='tool_calls' if len(calls)==1 else 'stop'
        usage={'prompt_tokens':20,'completion_tokens':5,'cost':.00003,'prompt_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}}
        delta=({'tool_calls':[dict(call,index=i) for i,call in enumerate(message['tool_calls'])]} if 'tool_calls' in message else message)
        chunks=[{'id':'reply-'+str(len(calls)),'model':'test/model','choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                {'id':'reply-'+str(len(calls)),'model':'test/model','choices':[{'index':0,'delta':{},'finish_reason':finish}]},
                {'id':'reply-'+str(len(calls)),'model':'test/model','choices':[],'usage':usage}]
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode())
    class Crash(BaseException):pass
    armed=[True]
    def build():
        bb=base.Blackboard()
        bind_blackboard(bb,WeeklyRunStore(store.db,1))
        bb.request_journal=RequestJournal(tmp_path/'requests.sqlite',run_id=store.run_id,
            authorization={'source':'offline_test'},authorized_usd='1',
            metadata=lambda model:{'id':model,'context_length':200000,'pricing':{'prompt':'.000001','completion':'.000002'}})
        native=bb.persist_run_checkpoint
        def persist(event,payload):
            native(event,payload)
            if event=='specialist_tool' and armed[0]:
                armed[0]=False;raise Crash()
        bb.persist_run_checkpoint=persist
        return bb
    client=llm_client.OpenRouterClient(api_key='offline',trasporto=httpx.MockTransport(send))
    bb=build();actor=base.Specialist(bb,client=client);actor.name='options'
    with pytest.raises(Crash):actor.run(1)
    assert len(calls)==len(tools)==1
    frozen=deepcopy(bb.data[h.KEY])
    assert len(frozen['observations'])==1 and bb.tool_receipts==[]
    for _ in range(2):
        bb=build();actor=base.Specialist(bb,client=client);actor.name='options'
        assert 'Verified synthetic report.' in actor.run(1)
        assert bb.data[h.KEY]==frozen and not bb.tool_receipts
    assert len(calls)==2 and len(tools)==1 and len(bb.tool_log)==1
    assert bb.request_journal.summary()['request_count']==2
    assert json.loads(bb.tool_log[0]['output'])==result
    record=next(iter(frozen['observations'].values()))
    assert record['desk']=='options' and record['round']==1
    assert record['evidence']['result_sha256']==hashlib.sha256(bb.tool_log[0]['output'].encode()).hexdigest()


@pytest.mark.parametrize('failure',['normalize','report','render'])
def test_diagnostic_failure_visible_sanitized_and_measured(store,monkeypatch,failure):
    def fail(*args,**kwargs):raise RuntimeError('PRIVATE_SENTINEL token=secret https://bad.invalid')
    bb=board_for(store)
    if failure=='normalize':monkeypatch.setattr(h,'normalize_health',fail)
    h.capture_health(bb,'one','get_options_data',{'error':'HTTP 429'},desk='options',round_n=0)
    bb.persist_run_checkpoint('specialist_tool',{})
    if failure=='report':monkeypatch.setattr(h,'build_report',fail)
    if failure=='render':monkeypatch.setattr(h,'render_health',fail)
    block=h.checkpoint_source_health(store,'source')
    saved=store.get(h.STAGE)
    assert 'CHECK_UNAVAILABLE' in block and 'PRIVATE_SENTINEL' not in json.dumps(saved) and 'https://' not in block
    if failure=='normalize':
        assert saved['report']['counters']['normalization_attempted']==1
        assert saved['report']['counters']['normalization_completed']==0
    else:
        assert saved['counters']['report_attempted']==1
        assert saved['counters']['report_completed']==int(failure=='render')
        assert saved['counters']['render_attempted']==int(failure=='render')
        assert saved['counters']['render_completed']==0
    assert h.checkpoint_source_health(WeeklyRunStore(store.db,1),'source')==block


@pytest.mark.parametrize('corrupt',['snapshot','checkpoint'])
def test_integrity_failure_blocks_not_check_unavailable(store,corrupt):
    bb=board_for(store)
    h.capture_health(bb,'one','get_options_data',{'error':'HTTP 429'})
    bb.persist_run_checkpoint('specialist_tool',{})
    h.checkpoint_source_health(store,'source')
    with store.db._conn() as c:
        if corrupt=='snapshot':c.execute("UPDATE weekly_runs SET snapshot_json='{}x'")
        else:c.execute("UPDATE weekly_checkpoints SET payload_json='{}' WHERE stage=?",(h.STAGE,))
    with pytest.raises((WeeklyRunBlocked,json.JSONDecodeError)):h.checkpoint_source_health(store,'source')


@pytest.mark.parametrize('tool,payload,status',[
    ('get_options_data',{'error':'POLYGON_API_KEY mancante'},'NOT_CONFIGURED'),
    ('get_options_data',{'error':'HTTP 401'},'UNAUTHORIZED'),
    ('get_options_data',{'error':'HTTP 403'},'UNAUTHORIZED'),
    ('get_options_data',{'error':'HTTP 429'},'RATE_LIMITED'),
    ('get_options_data',{'error':'HTTP 503'},'ERROR'),
    ('get_options_data',{'configured':True},'UNVERIFIED'),
    ('unmapped_tool',{'good':True},'UNVERIFIED'),
    ('news_feed',[{'provider':'GNews','pulled_at':'2032-01-01'}],'UNVERIFIED'),
    ('news_feed',[],'UNVERIFIED'),
    ('polymarket',{'results':[{'id':'fake'}],'fetch_warnings':['PRIVATE_SENTINEL']},'PARTIAL'),
    ('polymarket',{'results':[],'fetch_warnings':['PRIVATE_SENTINEL']},'ERROR'),
])
def test_capability_and_response_states_are_distinct(tool,payload,status):
    assert h.normalize_health(tool,payload)['status']==status

@pytest.mark.parametrize('tool,payload,status',[
    ('get_options_data',{'error':'unsupported market','stato':'non_coperto'},'NOT_SUPPORTED'),
    ('get_options_chain_polygon',{'error':'no data','coverage':{'status':'UNAVAILABLE','pages_received':1,'rows_observed':0,'errors':[]}},'EMPTY'),
    ('get_options_chain_polygon',{'coverage':{'status':'COMPLETE','pages_received':True,'rows_observed':1}},'UNVERIFIED'),
    ('get_options_chain_polygon',{'coverage':{'status':'COMPLETE','pages_received':1,'rows_observed':1,'errors':['HTTP 429']}},'PARTIAL'),
    ('macro_dashboard',{'indicators':{'x':{'error':'FRED_API_KEY non configurata nel .env'}}},'NOT_CONFIGURED'),
    ('macro_dashboard',{'indicators':{'x':{'error':'HTTP 429'}}},'RATE_LIMITED'),
])
def test_known_metadata_conflicts_and_empty_not_confused(tool,payload,status):
    assert h.normalize_health(tool,payload)['status']==status


def test_polygon_error_still_attributed_to_its_endpoint():
    out=h.normalize_health('get_options_data',{'_source':'polygon options summary','error':'HTTP 429'})
    assert out['provider']=='polygon' and out['endpoint']=='snapshot'

def test_priming_crash_native_resume_reuses_observations_and_no_new_probes(run_offline,monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _research_contract, _store
    _research_contract(run_offline,monkeypatch)
    calls=[]
    monkeypatch.setattr(cm,'tool_get_macro_dashboard',lambda:calls.append(1) or {'indicators':{'x':{'value':2,'date':'2032-01-01'}}})
    original=WeeklyRunStore.complete;armed=[True]
    def complete(self,stage,*args,**kwargs):
        result=original(self,stage,*args,**kwargs)
        if stage=='priming' and armed[0]:
            armed[0]=False;raise RuntimeError('synthetic after priming')
        return result
    monkeypatch.setattr(WeeklyRunStore,'complete',complete)
    with pytest.raises(RuntimeError,match='after priming'):cm.run_multi_agent(send_email=False)
    store=_store();restored=SimpleNamespace();store.restore(restored)
    before=deepcopy(restored.data[h.KEY]);probes=list(run_offline.sondati)
    monkeypatch.setattr(h,'normalize_health',lambda *args:pytest.fail('repeat normalization on persisted priming'))
    cm.run_multi_agent(resume_memo_id=store.memo_id,authorize_new_ai=True,send_email=False)
    store=_store();store.restore(restored)
    assert len(calls)==1 and run_offline.sondati==probes
    assert restored.data[h.KEY]==before and store.get(h.STAGE)


def test_concurrent_desk_provenance_and_hashes_no_receipt_index_join(store):
    from concurrent.futures import ThreadPoolExecutor
    bb=board_for(store)
    bb.tool_receipts=[{'tool':'unrelated','output':'PRIVATE_SENTINEL'}]
    bb.tool_log=[{'specialist':'fake_desk','round':99}]
    receipts=deepcopy(bb.tool_receipts);logs=deepcopy(bb.tool_log)
    def capture(desk):
        return h.capture_health(bb,'same-key','get_options_data',{'error':'HTTP 429'},desk=desk,round_n=1)
    with ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(capture,['options','macro']))
    assert len(bb.data[h.KEY]['observations'])==2
    assert {r['desk'] for r in records}=={'options','macro'}
    assert len({r['id'] for r in records})==2
    assert bb.tool_receipts==receipts and bb.tool_log==logs
    bb.persist_run_checkpoint('specialist_tool',{})
    block=h.checkpoint_source_health(store,'source')
    assert 'fake_desk' not in block and 'PRIVATE_SENTINEL' not in block


def test_changed_same_tool_key_blocks_and_old_success_does_not_erase_error(store):
    bb=board_for(store)
    h.capture_health(bb,'one','get_options_chain_polygon',{'error':'HTTP 429'},desk='options',round_n=1)
    with pytest.raises(WeeklyRunBlocked):h.capture_health(bb,'one','get_options_chain_polygon',{'error':'HTTP 503'},desk='options',round_n=1)
    h.capture_health(bb,'two','get_options_chain_polygon',{'coverage':{'status':'COMPLETE','pages_received':1,'rows_observed':2}},desk='options',round_n=2)
    block=h.context_block(bb)
    assert 'RATE_LIMITED' in block and 'OK' in block and len(bb.data[h.KEY]['observations'])==2


def test_acquisition_timestamp_not_economic_freshness():
    out=h.normalize_health('get_options_chain_polygon',{'_timestamp':'2032-01-01T10:00:00+00:00','coverage':{'status':'COMPLETE','pages_received':1,'rows_observed':2}})
    assert out['observed_at']=='2032-01-01T10:00:00+00:00' and out['as_of'] is None
    assert h.normalize_health('get_options_chain_polygon',{'_timestamp':'2032-01-01T10:00:00'})['observed_at'] is None


def test_unsafe_normalizer_strings_never_rendered(store,monkeypatch):
    bb=board_for(store)
    monkeypatch.setattr(h,'normalize_health',lambda *a:{'provider':'PRIVATE_SENTINEL','endpoint':'https://private.invalid','status':'OK','cause_codes':[]})
    h.capture_health(bb,'one','get_options_data',{'safe':'input'})
    bb.persist_run_checkpoint('specialist_tool',{})
    block=h.checkpoint_source_health(store,'source')
    assert 'CHECK_UNAVAILABLE' in block and 'PRIVATE_SENTINEL' not in block and 'https://' not in block

def test_unserializable_diagnostic_input_is_visible_not_run_failure(store):
    bb=board_for(store);payload={};payload['cycle']=payload
    result=h.capture_health(bb,'one','unknown',payload)
    assert result['observation']['status']=='CHECK_UNAVAILABLE'
    assert result['evidence']['result_sha256'] is None
    bb.persist_run_checkpoint('specialist_tool',{})
    assert 'CHECK_UNAVAILABLE' in h.checkpoint_source_health(store,'source')


@pytest.mark.parametrize('complete',[False,True])
def test_native_macro_comparison_gap_cannot_be_ok(monkeypatch,store,complete):
    from bellomberg.agents import agent_tools as at
    monkeypatch.setattr(at,'_FRED_INDICATORS',{'us_cpi_yoy':('CPIAUCSL','CPI YoY','yoy')})
    monkeypatch.setattr(at,'_NATIVE_INDICATORS',{})
    monkeypatch.setattr(at,'_FRED_REMOVED_NOTE',{})
    rows=[{'date':'2032-01-01','value':123.45}]
    if complete:rows.insert(0,{'date':'2031-01-01','value':120.0})
    monkeypatch.setattr(at,'_fred_fetch_series',lambda *a,**k:{'observations':rows,'rejected_observations':[]})
    payload=at.tool_get_macro_dashboard();original=deepcopy(payload)
    bb=board_for(store);out=h.capture_health(bb,'macro','macro_dashboard',payload)['observation']
    assert out['status']==('OK' if complete else 'PARTIAL')
    assert out['coverage']['comparison_unavailable']==(0 if complete else 1)
    assert payload==original
    if not complete:
        assert 'COMPARISON_UNAVAILABLE' in h.context_block(bb)
        assert 'comparison_unavailable=1' in h.context_block(bb)

@pytest.mark.parametrize('partial',[False,True])
def test_native_polygon_expiry_partial_cannot_be_empty(monkeypatch,partial):
    from bellomberg.market_data import polygon_data as pd
    monkeypatch.setattr(pd,'polygon_available',lambda:True)
    monkeypatch.setattr(pd,'get_option_expirations',lambda *a,**k:{'expirations':['2099-01-16'],
        'coverage':{'status':'PARTIAL' if partial else 'COMPLETE','pages_received':1,
        'expirations_observed':1,'errors':[],'issues':['budget_limit'] if partial else []}})
    monkeypatch.setattr(pd,'get_options_chain',lambda *a,**k:{'error':'no data',
        'coverage':{'status':'UNAVAILABLE','pages_received':1,'rows_observed':0,'errors':[]}})
    payload=pd.get_options_summary_polygon('ZZTEST');original=deepcopy(payload)
    out=h.normalize_health('get_options_data',payload)
    assert out['status']==('PARTIAL' if partial else 'EMPTY')
    if partial:assert 'COVERAGE_PARTIAL' in out['cause_codes']
    assert payload==original

@pytest.mark.parametrize('payload,status,cause',[
    ({'indicators':{'x':{'value':123.45,'quality':{'status':'STALE'}}}},'STALE','EXPLICIT_STALE'),
    ({'indicators':{'x':{'value':123.45,'quality':{'status':'STALE'}},'y':{'value':7}}},'PARTIAL','EXPLICIT_STALE'),
    ({'indicators':{'x':{'value':123.45,'quality':{'latest_status':'unavailable'}}}},'PARTIAL','QUALITY_UNAVAILABLE'),
])
def test_explicit_macro_quality_not_hidden(payload,status,cause):
    out=h.normalize_health('macro_dashboard',payload)
    assert out['status']==status and cause in out['cause_codes']

@pytest.mark.parametrize('coverage,expiry',[
    ({'status':'PARTIAL','pages_received':1,'rows_observed':0},None),
    ({'status':'COMPLETE','pages_received':1,'rows_observed':0},{'status':'PARTIAL'}),
    ({'status':'COMPLETE','pages_received':1,'rows_observed':0},{'status':'COMPLETE','errors':['HTTP 429']}),
])
def test_empty_cannot_hide_negative_coverage(coverage,expiry):
    out=h.normalize_health('get_options_chain_polygon',{'coverage':coverage,'expiry_coverage':expiry})
    assert out['status']=='PARTIAL' and 'COVERAGE_PARTIAL' in out['cause_codes']


def test_render_aggregation_explained_in_both_languages():
    report=h.build_report(None)
    assert 'non elementi unici' in h.render_health(report,'it')
    assert 'fase/round nel ledger' in h.render_health(report,'it')
    assert 'not unique items' in h.render_health(report,'en')
    assert 'phase/round detail in the ledger' in h.render_health(report,'en')
