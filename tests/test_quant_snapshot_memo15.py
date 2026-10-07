"""Frozen quant render inputs: real store/renderers, synthetic producers only."""
from copy import deepcopy
import hashlib
import json
import sys
from pathlib import Path
import pytest
from bellomberg.reporting import charts_quant as cq
from bellomberg.core.language import language_context
from test_cablaggio_consigliere_multi import run_offline
from test_quant_caveats_memo15 import rendered_text


@pytest.fixture(autouse=True)
def fake_extra_producers(monkeypatch,tmp_path,request):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.reporting import charts_rates
    if 'run_offline' not in request.fixturenames:
        monkeypatch.setattr(qs,'_collect',lambda slot:{'error':'synthetic unavailable: '+slot})
    monkeypatch.setattr(charts_rates,'OUT_DIR',str(tmp_path/'rates'))


def test_native_orchestrator_shares_frozen_inputs(run_offline,monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _research_contract,_store
    _research_contract(run_offline,monkeypatch)
    from test_cablaggio_consigliere_multi import _DeskFinto
    original_report = _DeskFinto.run
    def complete_report(self, round_n):
        original_report(self, round_n)
        return self.bb.read(self.name, round_n)
    monkeypatch.setattr(_DeskFinto, 'run', complete_report)
    seen={};risk_calls=[]
    risk_fn=sys.modules['bellomberg.portfolio.portfolio_risk'].compute_portfolio_risk
    monkeypatch.setattr(sys.modules['bellomberg.portfolio.portfolio_risk'],'compute_portfolio_risk',lambda:risk_calls.append(1) or risk_fn())
    def appendix(**kwargs):
        seen.update(kwargs);Path(kwargs['output_path']).write_bytes(b'%PDF-1.4 synthetic');return kwargs['output_path']
    monkeypatch.setattr(sys.modules['bellomberg.reporting.charts_quant'],'build_quant_appendix_v2',appendix)
    cm.run_multi_agent(send_email=False)
    store=_store();saved=store.get('render_context')
    assert 'quant_snapshot' in seen
    snap=seen['quant_snapshot']
    assert snap['entries']['risk_data']['payload']==saved['risk_data']
    assert snap['entries']['nav_history']['payload']==saved['nav_history']
    assert store.get('quant_render_context_v1') is not None
    assert len(risk_calls)==1
    assert sorted(run_offline.quant_acquisitions)==sorted(['garch','factors','rates_us','rates_de','rates_jp','rates_credit'])

@pytest.mark.parametrize('snapshot',[None,{}])
def test_explicit_missing_snapshot_never_refetches(tmp_path,monkeypatch,snapshot):
    calls=[]
    from bellomberg.core import quant_render_snapshot as qs
    import importlib
    for module,name in list(qs.PRODUCERS.values())+[
        ('bellomberg.portfolio.portfolio_risk','compute_portfolio_risk'),
        ('bellomberg.portfolio.advanced_metrics','portfolio_metrics')]:
        monkeypatch.setattr(importlib.import_module(module),name,lambda *a,_name=name,**k:calls.append(_name) or {'error':'synthetic'})
    monkeypatch.setattr(cq,'REPORT_DIR',tmp_path)
    monkeypatch.setattr(cq,'CHART_DIR',str(tmp_path/'charts'))
    path=cq.build_quant_appendix_v2(quant_snapshot=snapshot,output_path=str(tmp_path/'quant.pdf'))
    assert path and Path(path).stat().st_size>1000 and calls==[]


def test_two_sharpe_definitions_and_beta_guardrail_visible():
    risk={'portfolio':{'sharpe':1.25,'beta_vs_spy':1.1},'risk_free_used':0.0,'sharpe_note':'rf=0 (not excess return)'}
    advanced={'sharpe':0.75,'risk_free_used':0.03,'risk_free_status':'fallback',
              'risk_free_source':'advanced_metrics.static_fallback','risk_free_note':'explicit static fallback'}
    beta={'verdict':'UNRELIABLE','threshold':0.35,'betas':{'x':9.99},'calcolato_il':'2032-01-01T10:00:00'}
    with language_context('en'):
        text=rendered_text(cq._numeric_tables(risk,{}, {},advanced_metrics_snapshot=advanced,beta_reconcile_snapshot=beta))
    assert 'rf=0' in text and 'fallback' in text and '0.03' in text
    assert 'UNRELIABLE' in text and '2032-01-01' in text and '9.99' not in text



@pytest.fixture
def quant_store(tmp_path):
    import sqlite3
    from contextlib import contextmanager
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from bellomberg.core.quant_render_snapshot import POLICY
    class DB:
        db_path=str(tmp_path/'quant-synthetic.sqlite')
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
    return WeeklyRunStore(db,1,context={'contract_version':1,'contract':{'quant_render_policy':POLICY},
        'language':'it','research_started_at':'2032-01-01T10:00:00+00:00'})

@pytest.mark.parametrize('point',['intent','producer','result'])
def test_native_component_crash_never_repeats_acquisition(quant_store,monkeypatch,point):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    class Crash(BaseException):pass
    calls=[];armed=[True];original=WeeklyRunStore.complete
    def collect(slot):
        calls.append(slot)
        if point=='producer' and armed[0]:armed[0]=False;raise Crash()
        return {'result':'frozen'}
    def complete(self,stage,*args,**kwargs):
        value=original(self,stage,*args,**kwargs)
        if armed[0] and stage==('quant_input_'+point+':garch'):
            armed[0]=False;raise Crash()
        return value
    monkeypatch.setattr(qs,'_collect',collect);monkeypatch.setattr(WeeklyRunStore,'complete',complete)
    with pytest.raises(Crash):qs.capture_once(quant_store,'garch')
    before=len(calls);reopened=WeeklyRunStore(quant_store.db,1)
    one=qs.capture_once(reopened,'garch');two=qs.capture_once(reopened,'garch')
    assert one==two and len(calls)==before==(0 if point=='intent' else 1)
    assert one['status']==('AVAILABLE' if point=='result' else 'ATTEMPT_OUTCOME_UNAVAILABLE')

@pytest.mark.parametrize('value',[None,{},[],{'x':float('nan')},{'error':'PRIVATE_SECRET https://secret.invalid'}])
def test_failed_input_frozen_without_retry(quant_store,monkeypatch,value):
    from bellomberg.core import quant_render_snapshot as qs
    calls=[];monkeypatch.setattr(qs,'_collect',lambda slot:calls.append(slot) or value)
    one=qs.capture_once(quant_store,'garch');two=qs.capture_once(quant_store,'garch')
    assert one==two and one['status']=='UNAVAILABLE' and calls==['garch']

@pytest.mark.parametrize('where',['producer','persistence'])
def test_blocking_and_persistence_errors_are_not_absorbed(quant_store,monkeypatch,where):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    if where=='producer':
        monkeypatch.setattr(qs,'_collect',lambda _:(_ for _ in ()).throw(WeeklyRunBlocked('blocked')))
        expected=WeeklyRunBlocked
    else:
        original=quant_store.complete
        def complete(stage,*args,**kwargs):
            if stage.startswith('quant_input_result:'):raise OSError('storage failure')
            return original(stage,*args,**kwargs)
        monkeypatch.setattr(quant_store,'complete',complete);expected=OSError
    with pytest.raises(expected):qs.capture_once(quant_store,'garch')


def seed_render(quant_store):
    quant_store.complete('render_context',{'nav_history':None,'risk_data':{'portfolio':{'sharpe':1.25},'risk_free_used':0.0}})
    from bellomberg.core.quant_render_snapshot import entry
    quant_store.complete('priming',{'quant_advanced_metrics':entry({'sharpe':0.75,'risk_free_used':0.03},'preflight.portfolio_metrics'),
        'beta_reconcile':{'verdict':'UNRELIABLE','calcolato_il':'2032-01-01'}})


def test_native_snapshot_replay_and_dependency_integrity(quant_store,monkeypatch):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked,digest
    seed_render(quant_store);calls=[]
    monkeypatch.setattr(qs,'_collect',lambda slot:calls.append(slot) or {'data':slot})
    first=qs.prepare_snapshot(quant_store);before=deepcopy(first)
    first['entries']['mc']['payload']['data']='changed copy'
    assert qs.prepare_snapshot(quant_store)==before
    assert calls==[slot for slot in qs.PRODUCERS if slot!='nav_history']
    changed=quant_store.get('priming');changed['beta_reconcile']['verdict']='RECONCILED'
    encoded=json.dumps(changed,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    with quant_store.db._conn() as c:c.execute('UPDATE weekly_checkpoints SET payload_json=?,payload_sha256=? WHERE stage=?',
        (encoded,hashlib.sha256(encoded.encode()).hexdigest(),'priming'))
    with pytest.raises(WeeklyRunBlocked):qs.prepare_snapshot(quant_store)


def test_corrupt_snapshot_blocks_before_render(quant_store):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    seed_render(quant_store);qs.prepare_snapshot(quant_store)
    with quant_store.db._conn() as c:c.execute("UPDATE weekly_checkpoints SET payload_json='{}' WHERE stage=?",(qs.STAGE,))
    with pytest.raises(WeeklyRunBlocked):qs.prepare_snapshot(quant_store)

@pytest.mark.parametrize('language',['it','en'])
def test_real_renderer_full_snapshot_no_producer_calls(tmp_path,monkeypatch,language):
    from bellomberg.core import quant_render_snapshot as qs
    import importlib
    modules=list(qs.PRODUCERS.values())+[
        ('bellomberg.portfolio.portfolio_risk','compute_portfolio_risk'),
        ('bellomberg.portfolio.advanced_metrics','portfolio_metrics')]
    for module,name in modules:
        monkeypatch.setattr(importlib.import_module(module),name,lambda *a,**k:pytest.fail('renderer fetched'))
    entries={slot:qs.entry(None,slot) for slot in qs.SLOTS}
    entries['risk_data']=qs.entry({'portfolio':{'sharpe':1.25},'risk_free_used':0.0,'sharpe_note':'rf=0 (not excess return)'},'risk')
    entries['advanced_metrics']=qs.entry({'sharpe':0.75,'risk_free_used':0.03,'risk_free_status':'fallback',
        'risk_free_source':'synthetic','risk_free_note':'declared fallback'},'advanced')
    entries['beta_reconcile']=qs.entry({'verdict':'UNRELIABLE','betas':{'a':9.99},'calcolato_il':'2032-01-01'},'priming')
    snapshot={'version':qs.POLICY,'entries':entries,'research_started_at':'2032-01-01T10:00:00+00:00'};before=deepcopy(snapshot)
    monkeypatch.setattr(cq,'REPORT_DIR',tmp_path);monkeypatch.setattr(cq,'CHART_DIR',str(tmp_path/'charts'))
    path=cq.build_quant_appendix_v2(quant_snapshot=snapshot,output_path=str(tmp_path/'frozen.pdf'),language=language)
    import fitz
    with fitz.open(path) as doc:text='\n'.join(page.get_text() for page in doc)
    assert 'UNRELIABLE' in text and '2032-01-01' in text and '9.99' not in text
    assert 'rf=0' in text and ('0.03' in text if language=='en' else '0,03' in text)
    assert 'fallback' in text and 'garch' in text and snapshot==before
    assert ('1 GENNAIO 2032' if language=='it' else '1 JANUARY 2032') in text


def test_legacy_omission_keeps_original_acquisition(tmp_path,monkeypatch):
    from bellomberg.core import quant_render_snapshot as qs
    import importlib
    calls=[]
    for module,name in list(qs.PRODUCERS.values())+[
        ('bellomberg.portfolio.portfolio_risk','compute_portfolio_risk'),
        ('bellomberg.portfolio.advanced_metrics','portfolio_metrics')]:
        monkeypatch.setattr(importlib.import_module(module),name,lambda *a,_name=name,**k:calls.append(_name) or {'error':'synthetic'})
    monkeypatch.setattr(cq,'REPORT_DIR',tmp_path);monkeypatch.setattr(cq,'CHART_DIR',str(tmp_path/'charts'))
    cq.build_quant_appendix_v2(output_path=str(tmp_path/'legacy.pdf'))
    assert set(calls)=={name for _,name in list(qs.PRODUCERS.values())+[
        ('risk','compute_portfolio_risk'),('advanced','portfolio_metrics')]}
    assert len(calls)==10


def test_resume_contract_preserves_policy_absence():
    from bellomberg.agents import consigliere_multi as cm
    assert 'quant_render_policy' not in cm._resume_publication_contract({'quant_render_policy':'new'}, {})
    assert cm._resume_publication_contract({'quant_render_policy':'new'}, {'quant_render_policy':'old'})['quant_render_policy']=='old'


@pytest.mark.parametrize('language,expected',[('it','0,025'),('en','0.025')])
def test_rf_metadata_precision_retained(language,expected):
    with language_context(language):
        text=rendered_text(cq._numeric_tables({'portfolio':{'sharpe':1.0}}, {}, {},
            advanced_metrics_snapshot={'sharpe':0.75,'risk_free_used':0.025},beta_reconcile_snapshot=None))
    assert expected in text and ('rf=n.d.' if language=='it' else 'rf=n/a') in text

@pytest.mark.parametrize('point',['quant_input_intent:garch','quant_input_result:garch','quant_render_context_v1'])
def test_real_orchestrator_crash_and_pdf_delivery_replay(run_offline,monkeypatch,point):
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from test_weekly_recovery import _research_contract,_store
    _research_contract(run_offline,monkeypatch)
    from test_cablaggio_consigliere_multi import _DeskFinto
    original_report = _DeskFinto.run
    def complete_report(self, round_n):
        original_report(self, round_n)
        return self.bb.read(self.name, round_n)
    monkeypatch.setattr(_DeskFinto, 'run', complete_report)
    acquired=[];renders=[];advanced=[]
    monkeypatch.setattr(qs,'_collect',lambda slot:acquired.append(slot) or {'error':'synthetic unavailable'})
    monkeypatch.setattr(sys.modules['bellomberg.portfolio.advanced_metrics'],'portfolio_metrics',
        lambda:advanced.append(1) or {'sharpe':0.75,'risk_free_used':0.03})
    def appendix(**kwargs):
        renders.append(deepcopy(kwargs['quant_snapshot']))
        Path(kwargs['output_path']).write_bytes(b'%PDF-1.4 synthetic frozen');return kwargs['output_path']
    monkeypatch.setattr(sys.modules['bellomberg.reporting.charts_quant'],'build_quant_appendix_v2',appendix)
    original=WeeklyRunStore.complete;armed=[True]
    def complete(self,stage,*args,**kwargs):
        result=original(self,stage,*args,**kwargs)
        if stage==point and armed[0]:armed[0]=False;raise RuntimeError('synthetic render crash')
        return result
    monkeypatch.setattr(WeeklyRunStore,'complete',complete)
    with pytest.raises(RuntimeError,match='synthetic render crash'):cm.run_multi_agent(send_email=False)
    before=list(acquired);store=_store();probes=list(run_offline.sondati)
    cm.run_multi_agent(resume_memo_id=store.memo_id,authorize_new_ai=True,send_email=False)
    assert len(advanced)==1 and run_offline.sondati==probes
    assert len(acquired)==len(set(acquired))
    assert renders[0]['entries']['advanced_metrics']['payload']['sharpe']==0.75
    assert renders[0]['entries']['beta_reconcile']['payload']==store.get('priming')['beta_reconcile']
    if point.endswith('intent:garch'):assert renders[0]['entries']['garch']['status']=='ATTEMPT_OUTCOME_UNAVAILABLE'
    store=_store();receipt=store.get('rendered_pdf:appendix');content=Path(receipt['path']).read_bytes()
    counters=(len(acquired),len(renders),len(advanced));Path(receipt['path']).unlink()
    cm.run_multi_agent(resume_memo_id=store.memo_id,delivery_only=True,send_email=False)
    assert counters==(len(acquired),len(renders),len(advanced))
    assert Path(receipt['path']).read_bytes()==content


def test_native_preflight_unserializable_metrics_freezes_visible_failure(run_offline,monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _research_contract,_store
    from test_cablaggio_consigliere_multi import _DeskFinto
    _research_contract(run_offline,monkeypatch)
    original=_DeskFinto.run
    def report(self,round_n):original(self,round_n);return self.bb.read(self.name,round_n)
    monkeypatch.setattr(_DeskFinto,'run',report)
    monkeypatch.setattr(sys.modules['bellomberg.portfolio.advanced_metrics'],'portfolio_metrics',lambda:{'sharpe':float('nan')})
    cm.run_multi_agent(send_email=False)
    slot=_store().get('quant_render_context_v1')['entries']['advanced_metrics']
    assert slot['status']=='UNAVAILABLE' and slot['cause']=='INVALID_INPUT' and slot['payload'] is None


@pytest.mark.parametrize('beta,reason',[
    ({'verdict':'UNRELIABLE','betas':{'x':9.99}},'engines diverge'),
    ({'verdict':'INSUFFICIENT_SOURCES','betas':{'x':9.99}},'insufficient sources'),
    ({'error':'PRIVATE_SENTINEL'},'guardrail missing'),
    (None,'guardrail missing'),
    ({'verdict':'RECONCILED','beta_per_decisioni':False,'beta_consensus':9.99},'guardrail missing'),
])
def test_beta_failure_reason_safe_and_no_improper_consensus(beta,reason):
    with language_context('en'):
        text=rendered_text(cq._numeric_tables({}, {}, {},advanced_metrics_snapshot=None,beta_reconcile_snapshot=beta))
    assert reason in text and '9.99' not in text and 'PRIVATE_SENTINEL' not in text


def test_real_pdf_receipt_restore_is_exact(quant_store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.agents.weekly_lifecycle import render_pdf_once
    seed_render(quant_store);snapshot=qs.prepare_snapshot(quant_store)
    module=SimpleNamespace(REPORT_DIR=tmp_path/'report')
    monkeypatch.setattr(cq,'REPORT_DIR',module.REPORT_DIR);monkeypatch.setattr(cq,'CHART_DIR',str(tmp_path/'charts'))
    path=render_pdf_once(quant_store,module,cq.build_quant_appendix_v2,role='appendix',quant_snapshot=snapshot)
    content=Path(path).read_bytes();Path(path).unlink()
    monkeypatch.setattr(qs,'_collect',lambda _:pytest.fail('acquisition during restore'))
    restored=render_pdf_once(quant_store,module,lambda **k:pytest.fail('rerender during restore'),role='appendix',quant_snapshot=qs.prepare_snapshot(quant_store))
    assert Path(restored).read_bytes()==content and len(content)>1000


def test_renderer_rejects_payload_hash_corruption(tmp_path,monkeypatch):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    item=qs.entry({'portfolio':{'sharpe':1}},'risk');item['payload']['portfolio']['sharpe']=99
    with pytest.raises(WeeklyRunBlocked):
        cq.build_quant_appendix_v2(quant_snapshot={'entries':{'risk_data':item}},output_path=str(tmp_path/'corrupt.pdf'))


@pytest.mark.parametrize('language',['it','en'])
@pytest.mark.parametrize('sortino',[None,0.25,1.75])
def test_native_pdf_performance_prose_neutral_for_any_sortino(tmp_path,monkeypatch,language,sortino):
    from bellomberg.core import quant_render_snapshot as qs
    import pymupdf
    entries={slot:qs.entry(None,slot) for slot in qs.SLOTS}
    entries['advanced_metrics']=qs.entry({'sharpe':0.75,'sortino':sortino,'risk_free_used':0.025},'advanced')
    snapshot={'version':qs.POLICY,'entries':entries,'research_started_at':'2032-01-01T10:00:00+00:00'}
    monkeypatch.setattr(cq,'REPORT_DIR',tmp_path);monkeypatch.setattr(cq,'CHART_DIR',str(tmp_path/'charts'))
    path=cq.build_quant_appendix_v2(quant_snapshot=snapshot,output_path=str(tmp_path/'neutral.pdf'),language=language)
    with pymupdf.open(path) as doc:text='\n'.join(page.get_text() for page in doc)
    text=' '.join(text.split())  # PDF line wrapping is not semantic whitespace.
    assert 'Sortino None' not in text
    assert 'Il Sortino sopra lo Sharpe' not in text and 'Sortino above Sharpe' not in text
    assert 'velocita' not in text and 'recovery speed' not in text
    assert ('convenzioni dichiarati' if language=='it' else 'stated conventions') in text
    if sortino is None:assert ('Sortino n.d.' if language=='it' else 'Sortino n/a') in text
    else:assert str(sortino) in text


@pytest.mark.parametrize('marker',[None,'',0,False,{},[], 'weekly-quant-snapshot/999'])
def test_explicit_invalid_policy_blocks_native_store(quant_store,marker):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunStore,WeeklyRunBlocked
    with quant_store.db._conn() as c:c.execute("INSERT INTO memos VALUES(2,'policy test')")
    other=WeeklyRunStore(quant_store.db,2,context={'contract_version':1,'contract':{'quant_render_policy':marker}})
    with pytest.raises(WeeklyRunBlocked):qs.prepare_snapshot(other)
    assert other.get(qs.STAGE) is None


def test_absent_policy_is_legacy_without_acquisition(quant_store,monkeypatch):
    from bellomberg.core import quant_render_snapshot as qs
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    with quant_store.db._conn() as c:c.execute("INSERT INTO memos VALUES(2,'legacy policy test')")
    other=WeeklyRunStore(quant_store.db,2,context={'contract_version':1,'contract':{}})
    before=other.context.copy()
    monkeypatch.setattr(qs,'_collect',lambda _:pytest.fail('legacy helper acquired'))
    assert qs.prepare_snapshot(other) is qs.UNSET
    assert other.context==before and other.get(qs.STAGE) is None


def test_shared_offline_fixture_covers_moved_producer_boundaries(run_offline):
    from bellomberg.core import quant_render_snapshot as qs
    import importlib
    # Read-only inventory, never invoke a missing fake or a live producer.
    real=[]
    for slot,(module,name) in qs.PRODUCERS.items():
        obj=importlib.import_module(module)
        assert callable(getattr(obj,name))
        if getattr(obj,'__file__',None):real.append(slot)
    assert real==[]
