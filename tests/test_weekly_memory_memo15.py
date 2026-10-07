"""Operational weekly memory: native SQLite and receipts, no live models/portfolio."""
import hashlib
import json
from pathlib import Path
import pytest
from bellomberg.storage import memory_db
from bellomberg.storage.weekly_run_store import WeeklyRunStore, current_book_identity


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, '_init_chroma', lambda self: self.__dict__.update(col_memos=None,col_decisions=None,col_feedback=None))
    from bellomberg.agents import scorekeeper, reflection
    monkeypatch.setattr(scorekeeper,'get_track_record_for_capo',lambda *a,**k:'')
    monkeypatch.setattr(scorekeeper,'get_track_record_for_specialist',lambda *a,**k:'')
    monkeypatch.setattr(reflection,'LESSONS_PATH',str(tmp_path/'lessons.json'))
    return memory_db.MemoryDB(db_path=str(tmp_path/'memory.db'),chroma_path=str(tmp_path/'chroma'))


def native(db, tmp_path, *, body='# PUBLISHED SYNTHETIC', state='completed', report='R2 verified synthetic'):
    mid=db.save_memo(body,title=body)
    context={'contract_version':1,'book_identity':current_book_identity(db),
        'contract':{'analysis_mode':'fundamentals_research_v1','roster':['macro'],'r2_specialists':['macro']}}
    store=WeeklyRunStore(db,mid,context=context)
    stages=['priming','desk:macro:0','desk:macro:1','desk:macro:2','research_dossier','red_team','synthesis_context','capo','memo_validated','reflection','render_context','decisions_finalized']
    for stage in stages:
        payload=({'memo':body,'publication':{'memo_markdown':body}} if stage=='memo_validated' else
                 {'status':'complete','report_sha256':hashlib.sha256(report.encode()).hexdigest()} if stage=='desk:macro:2' else
                 {'ids':[],'error':None} if stage=='decisions_finalized' else {})
        store.complete(stage,payload)
    receipts=[]
    for kind,suffix,content in [('Markdown','md',body.encode()),('PDF','pdf',b'%PDF-synthetic')]:
        path=tmp_path/('artifact-'+str(mid)+'.'+suffix);path.write_bytes(content)
        receipts.append({'path':str(path),'sha256':hashlib.sha256(content).hexdigest(),'kind':kind,'role':'memo'})
    store.complete('artifact_bundle',{'artifacts':receipts})
    store.update(status=state,analytical_status='complete',artifact_status='available',artifacts=receipts)
    db.save_specialist_report(mid,'macro',2,report)
    return mid,store


def test_two_native_aborts_cannot_replace_last_completed_and_archive_stays(db,tmp_path):
    good,_=native(db,tmp_path,body='# GOOD PUBLISHED')
    bad1,_=native(db,tmp_path,body='# FIRST ABORT',state='incomplete')
    bad2,_=native(db,tmp_path,body='[IN PROGRESS]',state='incomplete')
    with db._conn() as conn: before='\n'.join(conn.iterdump())
    assert [r['id'] for r in db.get_completed_weekly_memos(3)]==[good]
    ctx=db.build_capo_memory_context(max_chars=50000)
    assert '# GOOD PUBLISHED' in ctx and '# FIRST ABORT' not in ctx and '[IN PROGRESS]' not in ctx
    assert [r['id'] for r in db.get_archive_memos(10)]==[bad2,bad1,good]
    with db._conn() as conn: assert '\n'.join(conn.iterdump())==before


@pytest.mark.parametrize('fault',['state','artifact','checkpoint','body','missing_checkpoint','snapshot','other_checkpoint'])
def test_native_completion_requires_verified_evidence(db,tmp_path,fault):
    mid,store=native(db,tmp_path)
    if fault=='state': store.update(status='incomplete')
    elif fault=='artifact': Path(store.status()['artifacts'][0]['path']).write_text('tampered')
    elif fault=='snapshot':
        with db._conn() as conn: conn.execute("UPDATE weekly_runs SET snapshot_json=? WHERE memo_id=?",(json.dumps({'payload':{},'sha256':'wrong'}),mid))
    elif fault=='other_checkpoint':
        with db._conn() as conn: conn.execute("UPDATE weekly_checkpoints SET payload_json=? WHERE memo_id=? AND stage='red_team'",('{"tampered":true}',mid))
    elif fault=='body':
        with db._conn() as conn: conn.execute('UPDATE memos SET full_markdown=? WHERE id=?',('changed',mid))
    else:
        with db._conn() as conn:
            if fault=='checkpoint': conn.execute("UPDATE weekly_checkpoints SET payload_json='{}' WHERE memo_id=? AND stage='memo_validated'",(mid,))
            else: conn.execute("DELETE FROM weekly_checkpoints WHERE memo_id=? AND stage='decisions_finalized'",(mid,))
    assert db.get_completed_weekly_memos(3)==[]
    assert db.get_archive_memos(10)[0]['id']==mid


def test_r2_from_incomplete_run_and_unverified_report_excluded(db,tmp_path):
    good,_=native(db,tmp_path,report='GOOD R2')
    native(db,tmp_path,state='incomplete',report='ABORTED R2')
    mid,_=native(db,tmp_path,report='ORIGINAL R2')
    with db._conn() as conn: conn.execute('UPDATE specialist_reports SET content=? WHERE memo_id=?',('TAMPERED R2',mid))
    rows=db.get_completed_specialist_reports('macro',3)
    assert [r['memo_id'] for r in rows]==[good]
    ctx=db.build_specialist_memory_context('macro',max_chars=10000)
    assert 'GOOD R2' in ctx and 'ABORTED R2' not in ctx and 'TAMPERED R2' not in ctx


def test_latest_legacy_completed_is_not_implicitly_skipped_and_ti_isolated(db):
    db.save_memo('# OLDER LEGACY',title='OLDER LEGACY')
    latest=db.save_memo('# LATEST LEGACY',title='LATEST LEGACY')
    ti=db.save_memo('# TI PRIVATE')
    with db._conn() as conn: conn.execute('UPDATE memos SET notes=? WHERE id=?',('trade_idea:synthetic',ti))
    db.save_memo('[IN PROGRESS]')
    rows=db.get_completed_weekly_memos(5)
    assert rows[0]['id']==latest and all(r['id']!=ti for r in rows)
    assert rows[0]['memory_completion']=='legacy_untracked'
    ctx=db.build_capo_memory_context(max_chars=50000)
    assert 'LATEST LEGACY' in ctx and 'TI PRIVATE' not in ctx and 'legacy' in ctx.lower()


def test_legacy_gate_closure_not_interpreted_as_pm_skip(db):
    mid=db.save_memo('# PUBLISHED')
    with db._conn() as conn:
        for ticker,note in [('ZZGATE','AUTO-ESCLUSA: synthetic'),('ZZNEW',memory_db.MARCA_CHIUSA_DAL_GATE+' BLOCKED]'),('ZZPM','PM chose to wait')]:
            conn.execute("INSERT INTO decisions(memo_id,timestamp,action,ticker,status,outcome_notes) VALUES (?,'2026-10-07','BUY',?,'SKIPPED',?)",(mid,ticker,note))
        before='\n'.join(conn.iterdump())
    ctx=db.build_capo_memory_context(max_chars=50000)
    gate=next(line for line in ctx.splitlines() if line.startswith('CHIUSE DAL GATE'))
    pm=next(line for line in ctx.splitlines() if line.startswith('NON eseguite questa volta'))
    assert 'ZZGATE' in gate and 'ZZNEW' in gate and 'ZZPM' not in gate
    assert 'ZZPM' in pm and 'ZZGATE' not in pm
    with db._conn() as conn: assert '\n'.join(conn.iterdump())==before



def test_lessons_from_aborted_runs_are_not_operational_but_archive_unchanged(db,tmp_path):
    from bellomberg.agents import reflection
    good,_=native(db,tmp_path,body='# GOOD LESSON SOURCE')
    bad,_=native(db,tmp_path,body='# ABORT LESSON SOURCE',state='incomplete')
    legacy=db.save_memo('# LEGACY LESSON SOURCE')
    lessons=[{'memo_id':good,'lesson':'GOOD LESSON'}, {'memo_id':legacy,'lesson':'LEGACY LESSON'}, {'memo_id':bad,'lesson':'ABORTED LESSON'}]
    p=Path(reflection.LESSONS_PATH);p.write_text(json.dumps(lessons),encoding='utf-8');before=p.read_bytes()
    ids={r['id'] for r in db.get_completed_weekly_memos(n=None)}
    block=reflection.get_latest_lesson_block(eligible_memo_ids=ids)
    assert 'LEGACY LESSON' in block and 'ABORTED LESSON' not in block
    ctx=db.build_capo_memory_context(max_chars=50000)
    assert 'LEGACY LESSON' in ctx and 'ABORTED LESSON' not in ctx
    assert p.read_bytes()==before


def test_generate_lesson_filters_prior_input_preserving_archive(db,tmp_path,monkeypatch):
    from types import SimpleNamespace as NS
    from bellomberg.agents import reflection,scorekeeper
    from bellomberg.core import llm_client
    from bellomberg.agents.specialists import base
    p=Path(reflection.LESSONS_PATH)
    lessons=[{'memo_id':1,'lesson':'VALID PRIOR'}, {'memo_id':2,'lesson':'ABORTED PRIOR'}]
    p.write_text(json.dumps(lessons),encoding='utf-8')
    calls=[]
    def create(**kwargs):
        calls.append(kwargs);return NS(content=[NS(text='NEW SYNTHETIC LESSON')],stop_reason='end_turn')
    monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **kw:NS(messages=NS(create=create)))
    monkeypatch.setattr(llm_client,'modello',lambda *a:'synthetic/offline')
    monkeypatch.setattr(llm_client,'thinking_fase',lambda *a:{'type':'enabled'})
    monkeypatch.setattr(base,'timeout_specialisti',lambda *a:1)
    monkeypatch.setattr(scorekeeper,'compute_scorecard',lambda:{'overall':{'n':3}})
    monkeypatch.setattr(scorekeeper,'format_track_record_for_capo',lambda *a,**k:'SYNTHETIC TRACK')
    assert reflection.generate_lesson('# PUBLISHED',memo_id=3,eligible_memo_ids={1})=='NEW SYNTHETIC LESSON'
    assert len(calls)==1
    text=calls[0]['messages'][0]['content']
    assert 'VALID PRIOR' in text and 'ABORTED PRIOR' not in text
    assert json.loads(p.read_text())[:2]==lessons


from test_weekly_research_without_workbook import research_weekly
from test_cablaggio_consigliere_multi import run_offline


def test_reflection_receives_real_publication_and_resume_never_replays(research_weekly,monkeypatch):
    import sys
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store
    raw=('# Memo\n\n## ACTION TABLE\n'
         '| Action | Ticker | EUR | Timing | Confidence | Rationale |\n'
         '|---|---|---|---|---|---|\n'
         '| BUY | SYNTH-A | 1234 | now | HIGH | Synthetic thesis |\n')
    monkeypatch.setattr(cm,'run_capo',lambda *a,**k:(raw,{'complete':True,'stop_reason':'end_turn','model':'synthetic/offline','api_calls':1,'in':10,'out':10,'input_tokens':10,'output_tokens':10}))
    seen=[]
    def lesson(memo,memo_id=None,usage_out=None,**kwargs):
        seen.append((memo,memo_id,kwargs));return 'SYNTHETIC LESSON'
    monkeypatch.setattr(sys.modules['bellomberg.agents.reflection'],'generate_lesson',lesson)
    cm.run_multi_agent(send_email=False)
    store=_store();publication=store.get('memo_validated')['memo']
    assert seen[0][0]==publication and seen[0][0]!=raw
    assert 'GATE DI PUBBLICAZIONE' in publication
    assert store.get('capo')['memo']==raw
    with store.db._conn() as conn:
        assert conn.execute('SELECT full_markdown FROM memos WHERE id=?',(store.memo_id,)).fetchone()[0]==publication
    cm.run_multi_agent(resume_memo_id=store.memo_id,send_email=False)
    assert len(seen)==1



def test_incomplete_model_recommendation_excluded_but_explicit_pm_binding_kept(db,tmp_path):
    good,_=native(db,tmp_path,body='# DECISION GOOD')
    bad,_=native(db,tmp_path,body='# DECISION ABORT',state='incomplete')
    with db._conn() as conn:
        for mid,ticker,feedback in [(good,'ZZVALID',None),(bad,'ZZABORT',None),(bad,'ZZVETO','Do not buy this synthetic issuer')]:
            conn.execute("INSERT INTO decisions(memo_id,timestamp,action,ticker,status,pm_feedback) VALUES (?,'2026-10-07','BUY',?,'PENDING',?)",(mid,ticker,feedback))
    capo=db.build_capo_memory_context(max_chars=50000)
    specialist=db.build_specialist_memory_context('macro',max_chars=50000)
    assert 'ZZVALID' in capo and 'ZZABORT' not in capo and 'ZZABORT' not in specialist
    assert 'Do not buy this synthetic issuer' in capo and 'Do not buy this synthetic issuer' in specialist



@pytest.mark.parametrize('declared_gap,expected',[(False,False),(True,True)])
def test_native_incomplete_report_snapshot_excluded_unless_declared_gap(db,tmp_path,declared_gap,expected):
    from bellomberg.storage.weekly_run_store import digest
    mid,store=native(db,tmp_path)
    payload={'data':{'_desk_gaps':{'macro':{'message':'synthetic gap'}}} if declared_gap else {},
             'specialist_checkpoints':{'macro:2':{'status':'truncated'}}}
    snapshot={'payload':payload,'sha256':digest(payload)}
    with db._conn() as conn:
        conn.execute('UPDATE weekly_runs SET snapshot_json=? WHERE memo_id=?',(json.dumps(snapshot),mid))
    assert bool(db.get_completed_weekly_memos()) is expected
    assert db.get_completed_specialist_reports('macro') == []


@pytest.mark.parametrize('body',['[IN PROGRESS]','[ERROR macro]: synthetic','RIFIUTO DEL MODELLO: synthetic',''])
def test_legacy_placeholder_or_refusal_not_previous_memo(db,body):
    good=db.save_memo('# LEGACY COMPLETE')
    db.save_memo(body)
    assert [r['id'] for r in db.get_completed_weekly_memos()]==[good]



def test_reflection_checkpoint_survives_pdf_abort_without_replay(research_weekly,monkeypatch):
    import sys
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.reporting import pdf_report
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    from test_weekly_recovery import _store
    renderer=sys.modules['bellomberg.reporting.pdf_institutional']
    original=renderer.build_institutional_memo
    calls=[]
    # This test exercises an already-versioned legacy lesson, not Reflection36 evidence.
    original_contract=cm._weekly_contract
    def legacy_contract(**kwargs):
        value=original_contract(**kwargs)
        value.pop('reflection_policy',None)
        return value
    monkeypatch.setattr(cm,'_weekly_contract',legacy_contract)
    def lesson(memo,**kwargs): calls.append(memo);return 'FROZEN SYNTHETIC LESSON'
    def fail(**kwargs): raise RuntimeError('synthetic PDF interruption')
    monkeypatch.setattr(sys.modules['bellomberg.agents.reflection'],'generate_lesson',lesson)
    monkeypatch.setattr(renderer,'build_institutional_memo',fail)
    monkeypatch.setattr(pdf_report,'build_pdf_report',fail)
    with pytest.raises(WeeklyRunBlocked,match='PDF richiesto assente'):
        cm.run_multi_agent(send_email=False)
    store=_store();saved=store.get('reflection')
    assert 'reflection_policy' not in store.context['contract']
    assert saved['lesson']=='FROZEN SYNTHETIC LESSON' and len(calls)==1
    assert db_ids(store.db)==[]
    monkeypatch.setattr(renderer,'build_institutional_memo',original)
    result=cm.run_multi_agent(resume_memo_id=store.memo_id,authorize_new_ai=True,send_email=False)
    assert result['status']=='completed' and store.get('reflection')==saved and len(calls)==1
    assert db_ids(store.db)==[store.memo_id]


def db_ids(db):
    return [r['id'] for r in db.get_completed_weekly_memos()]



def test_r2_status_must_be_complete_even_when_hash_valid(db,tmp_path):
    mid,store=native(db,tmp_path)
    data=store.get('desk:macro:2');data['status']='truncated'
    encoded=json.dumps(data)
    with db._conn() as conn:
        conn.execute("UPDATE weekly_checkpoints SET payload_json=?,payload_sha256=? WHERE memo_id=? AND stage='desk:macro:2'",(encoded,hashlib.sha256(encoded.encode()).hexdigest(),mid))
    assert db.get_completed_specialist_reports('macro')==[]


def test_pm_execution_from_incomplete_run_preserved_as_fact(db,tmp_path):
    mid,_=native(db,tmp_path,state='incomplete')
    with db._conn() as conn:
        conn.execute("INSERT INTO decisions(memo_id,timestamp,action,ticker,status) VALUES (?,'2026-10-07','BUY','ZZEXEC','EXECUTED')",(mid,))
    rows=db.get_operational_memory_decisions()
    assert len(rows)==1 and rows[0]['ticker']=='ZZEXEC'
    assert rows[0]['memory_origin']=='explicit_pm_from_unavailable_run'
    assert 'ZZEXEC' in db.build_capo_memory_context(max_chars=50000)
