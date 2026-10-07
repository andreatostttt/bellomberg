"""Operational semantic search: native SQLite/receipts, synthetic vector results."""
import copy
import hashlib
from types import SimpleNamespace
import pytest
from test_weekly_memory_memo15 import db, native
from test_desk_stream_journal import bb
from bellomberg.storage.semantic_memory import search_operational_memos, POLICY


class Collection:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
    def query(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        rows = self.rows[:kwargs['n_results']]
        return {key: [[r[key] for r in rows]] for key in ('ids','metadatas','documents','distances')}
    def add(self, **kwargs): raise AssertionError('No embedding writes')
    upsert = update = delete = add


def candidate(mid, content, idx=0):
    return {'ids':f'memo_{mid}_chunk_{idx}', 'metadatas':{'memo_id':mid,'chunk_idx':idx},
            'documents':content,'distances':0.2}


def test_completed_chunks_only_with_two_aborts_and_archive_untouched(db,tmp_path):
    good,_=native(db,tmp_path,body='# PUBLISHED')
    bad,_=native(db,tmp_path,body='# ABORTED',state='incomplete')
    pending,_=native(db,tmp_path,body='[IN PROGRESS]',state='incomplete')
    db.col_memos=Collection([candidate(bad,'# ABORTED'),candidate(good,'# PUBLISHED'),candidate(pending,'[IN PROGRESS]')])
    with db._conn() as c: before='\n'.join(c.iterdump())
    out=search_operational_memos(db,'synthetic',5)
    assert [m['memo_id'] for m in out['memos']]==[good]
    assert out['status']=='partial' and out['coverage']['excluded']==2
    assert out['coverage']['retrieved']==3 and out['count']==1
    assert out['memos'][0]['memory_completion']=='native_completed'
    assert out['memos'][0]['published_chunk_sha256']==hashlib.sha256(b'# PUBLISHED').hexdigest()
    assert len(db.col_memos.calls)==1
    with db._conn() as c: assert '\n'.join(c.iterdump())==before
    assert [m['id'] for m in db.get_archive_memos(10)]==[pending,bad,good]


@pytest.mark.parametrize('fault',['stale','extra','wrong_index','wrong_id','metadata_bool','metadata_fraction','metadata_string','idx_bool','missing_idx','not_text','duplicate'])
def test_bad_provenance_cannot_borrow_completed_id(db,tmp_path,fault):
    mid,_=native(db,tmp_path,body='# PUBLISHED\n\n## NEW\ncurrent')
    row=candidate(mid,'# PUBLISHED')
    if fault=='stale': row['documents']='[IN PROGRESS]'
    elif fault=='extra': row=candidate(mid,'old extra',9)
    elif fault=='wrong_index': row=candidate(mid,'## NEW\ncurrent',0)
    elif fault=='wrong_id': row['ids']='memo_999_chunk_0'
    elif fault.startswith('metadata_'): row['metadatas']['memo_id']={'metadata_bool':True,'metadata_fraction':float(mid),'metadata_string':str(mid)}[fault]
    elif fault=='idx_bool': row['metadatas']['chunk_idx']=False
    elif fault=='missing_idx': row['metadatas'].pop('chunk_idx')
    elif fault=='not_text': row['documents']={'text':'# PUBLISHED'}
    db.col_memos=Collection([row,row] if fault=='duplicate' else [row])
    out=search_operational_memos(db,'synthetic')
    assert out['memos']==[] and out['status']=='partial'
    assert out['coverage']['excluded']==len(db.col_memos.rows)


def test_exact_legacy_and_ranking_not_sql_position(db):
    first=db.save_memo('# FIRST');second=db.save_memo('# SECOND')
    db.col_memos=Collection([candidate(first,'# FIRST'),candidate(second,'# SECOND')])
    out=search_operational_memos(db,'synthetic')
    assert [m['memo_id'] for m in out['memos']]==[first,second]
    assert all(m['memory_completion']=='legacy_untracked' for m in out['memos'])
    assert all('MANDATO' in m['content'] and m['provenance']=='exact_published_chunk' for m in out['memos'])
    assert out['coverage']['universe_verified'] is False


@pytest.mark.parametrize('fault',['absent','query','shape','database'])
def test_unavailable_is_explicit_not_empty_success(db,monkeypatch,fault):
    db.col_memos=Collection([])
    if fault=='absent': db.col_memos=None
    elif fault=='query': monkeypatch.setattr(db.col_memos,'query',lambda **k: (_ for _ in ()).throw(RuntimeError('PRIVATE')))
    elif fault=='shape': monkeypatch.setattr(db.col_memos,'query',lambda **k: {'ids':[['x']],'metadatas':[[]],'documents':[[]],'distances':[[]]})
    else: monkeypatch.setattr(db,'_conn',lambda: (_ for _ in ()).throw(RuntimeError('PRIVATE')))
    out=search_operational_memos(db,'synthetic')
    assert out['status']=='unavailable' and out['reason'] and out['count']==0
    assert 'PRIVATE' not in str(out)


def test_limit_is_explicit_and_no_fill_retry(db):
    db.col_memos=Collection([])
    out=search_operational_memos(db,'synthetic',72)
    assert out['coverage']['requested']==72 and out['coverage']['effective_limit']==50
    assert db.col_memos.calls==[{'query_texts':['synthetic'],'n_results':50}]
    assert out['status']=='partial'


@pytest.mark.parametrize('n',[0,-1,True,2.5,'5'])
def test_invalid_limit_never_queries(db,n):
    db.col_memos=Collection([])
    out=search_operational_memos(db,'synthetic',n)
    assert out['status']=='unavailable' and not db.col_memos.calls


@pytest.mark.parametrize('kind',['trade_idea','duplicate','refusal','native_tampered'])
def test_operational_semantic_reuses_memory_eligibility(db,tmp_path,kind):
    if kind=='native_tampered':
        mid,store=native(db,tmp_path,body='# PUBLISHED')
        with db._conn() as c: c.execute("UPDATE weekly_checkpoints SET payload_json='{}' WHERE memo_id=? AND stage='memo_validated'",(mid,))
        body='# PUBLISHED'
    else:
        from bellomberg.core.llm_refusal import REFUSAL_TAG
        body=REFUSAL_TAG+' refused' if kind=='refusal' else '# PUBLISHED'
        mid=db.save_memo(body)
        if kind in ('trade_idea','duplicate'):
            with db._conn() as c:
                c.execute('UPDATE memos SET notes=? WHERE id=?',('trade_idea:synthetic' if kind=='trade_idea' else '[DUPLICATO synthetic]',mid))
    db.col_memos=Collection([candidate(mid,body)])
    out=search_operational_memos(db,'synthetic')
    assert out['count']==0 and out['coverage']['exclusion_reasons']=={'memo_not_operational':1}


def test_current_second_chunk_is_accepted_with_exact_index(db):
    mid=db.save_memo('# FIRST\n\n## SECOND\ncurrent')
    db.col_memos=Collection([candidate(mid,'## SECOND\ncurrent',1)])
    out=search_operational_memos(db,'synthetic')
    assert out['count']==1 and out['memos'][0]['chunk_id']==f'memo_{mid}_chunk_1'


def test_archive_default_still_returns_unverified_historical_chunk(db):
    mid=db.save_memo('[IN PROGRESS]')
    db.col_memos=Collection([candidate(mid,'unverified old historical text')])
    out=db.search_memos_semantic('synthetic')
    assert len(out)==1 and 'unverified old historical text' in out[0]['content']


def test_tool_optional_flag_keeps_default_archive(db,monkeypatch):
    from bellomberg.agents import agent_tools as at
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db,'MemoryDB',lambda:db)
    mid=db.save_memo('[IN PROGRESS]')
    db.col_memos=Collection([candidate(mid,'old historical text')])
    assert at.tool_search_past_memos('synthetic')['count']==1
    result=at.tool_search_past_memos('synthetic',operational_only=True)
    assert result['count']==0 and result['memory_scope']=='weekly_operational'


@pytest.mark.parametrize('scope,marker,operational',[('weekly',POLICY,True),('weekly',None,False),('trade_idea',POLICY,False),(None,POLICY,False)])
def test_real_specialist_boundary_scope_policy_and_immutable_input(monkeypatch,scope,marker,operational):
    from bellomberg.agents.specialists.base import Specialist
    from bellomberg.agents import agent_tools as at,chat_tools
    contract={'semantic_memory_policy':marker} if marker is not None else {}
    bb=SimpleNamespace(run_scope=scope,current_round=1,weekly_store=SimpleNamespace(context={'contract':contract}))
    desk=Specialist.__new__(Specialist);desk.blackboard=bb;desk.name='macro'
    calls=[]
    monkeypatch.setattr(at,'tool_search_past_memos',lambda **k:calls.append(k) or {'count':0,'memos':[]})
    monkeypatch.setattr(chat_tools,'dispatch',lambda *a,**k:{'legacy':True})
    original={'query':'synthetic','n_results':5};before=copy.deepcopy(original)
    result=desk._execute_meta_tool_unlocked('search_past_memos',original)
    assert original==before
    assert bool(calls)==operational
    if operational: assert calls==[{'query':'synthetic','n_results':5,'operational_only':True}]
    else: assert result=={'legacy':True}


def test_operational_failure_never_uses_archive_fallback(monkeypatch):
    from bellomberg.agents.specialists.base import Specialist
    from bellomberg.agents import agent_tools as at,chat_tools
    bb=SimpleNamespace(run_scope='weekly',current_round=1,weekly_store=SimpleNamespace(context={'contract':{'semantic_memory_policy':POLICY}}))
    desk=Specialist.__new__(Specialist);desk.blackboard=bb;desk.name='macro'
    monkeypatch.setattr(at,'tool_search_past_memos',lambda **k:(_ for _ in ()).throw(RuntimeError('PRIVATE')))
    monkeypatch.setattr(at,'execute_tool',lambda *a,**k:pytest.fail('archive fallback'))
    monkeypatch.setattr(chat_tools,'dispatch',lambda *a,**k:pytest.fail('shared archive dispatch'))
    result=desk._execute_meta_tool_unlocked('search_past_memos',{'query':'synthetic'})
    assert result['status']=='unavailable' and 'PRIVATE' not in str(result)


def test_policy_new_run_and_preserved_absence_on_resume(monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.core import llm_client
    monkeypatch.setattr(llm_client,'modello_o_buco',lambda *a:'synthetic-model')
    current=cm._weekly_contract()
    assert current['semantic_memory_policy']==POLICY
    assert 'semantic_memory_policy' not in cm._resume_publication_contract(current,{})
    assert cm._resume_publication_contract(current,{'semantic_memory_policy':'old'})['semantic_memory_policy']=='old'


@pytest.mark.parametrize('marker',['weekly-published-chunks/2',None,'',False,{'private':'PRIVATE_SENTINEL'}])
def test_unsupported_present_policy_never_dispatches_archive(monkeypatch,marker):
    from bellomberg.agents.specialists.base import Specialist
    from bellomberg.agents import agent_tools as at,chat_tools
    board=SimpleNamespace(run_scope='weekly',current_round=1,
        weekly_store=SimpleNamespace(context={'contract':{'semantic_memory_policy':marker}}))
    desk=Specialist.__new__(Specialist);desk.blackboard=board;desk.name='macro'
    def prohibited(*a,**k):pytest.fail('Unsupported policy must not dispatch archive or operational tool')
    monkeypatch.setattr(chat_tools,'dispatch',prohibited)
    monkeypatch.setattr(at,'execute_tool',prohibited)
    monkeypatch.setattr(at,'tool_search_past_memos',prohibited)
    original={'query':'synthetic'};before=copy.deepcopy(original)
    result=desk._execute_meta_tool_unlocked('search_past_memos',original)
    assert result=={'status':'unavailable','reason':'unsupported_policy',
                   'memory_scope':'weekly_operational','count':0,'memos':[]}
    assert 'PRIVATE_SENTINEL' not in str(result) and original==before


@pytest.mark.parametrize('marker',[None,POLICY])
def test_native_paid_tool_replay_keeps_exact_output_and_queries_once(db,bb,tmp_path,monkeypatch,marker):
    import json
    from test_desk_stream_journal import _Desk,_journal,_client,_sse,_chunk,_righe,USAGE_TOOL,USAGE_TESTO
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from bellomberg.storage import memory_db
    from bellomberg.agents import chat_tools
    from bellomberg.agents.agent_tools import tool_search_past_memos
    good=db.save_memo('# VERIFIED LEGACY')
    db.col_memos=Collection([candidate(good,'# VERIFIED LEGACY')])
    current=db.save_memo('[IN PROGRESS]')
    contract={'semantic_memory_policy':marker} if marker else {}
    store=WeeklyRunStore(db,current,context={'contract_version':1,'contract':contract})
    bb.weekly_store=store;bb.request_journal=_journal(tmp_path)
    monkeypatch.setattr(memory_db,'MemoryDB',lambda:db)
    monkeypatch.setattr(chat_tools,'_stamp',lambda r,s:{**r,'_source':s},raising=False)
    monkeypatch.setattr(chat_tools,'dispatch',lambda name,inp,**k:tool_search_past_memos(**inp))
    bodies=[]
    report='Complete synthetic research with declared evidence and no further tools. '*35
    replies=[_sse(_chunk({'tool_calls':[{'index':0,'id':'semantic-1','type':'function',
        'function':{'name':'search_past_memos','arguments':'{"query":"synthetic"}'}}]}),
        _chunk(finish='tool_calls',usage=USAGE_TOOL)),
        _sse(_chunk({'content':report},gen='gen-acme-2'),_chunk(finish='stop',usage=USAGE_TESTO,gen='gen-acme-2'))]
    client=_client(replies,bodies)
    def actor():
        desk=_Desk(bb,client=client)
        monkeypatch.setattr(desk,'_build_round_context',lambda *a:'Frozen semantic test context')
        monkeypatch.setattr(desk,'_build_tools_schema',lambda:[{'name':'search_past_memos','description':'Search',
            'input_schema':{'type':'object','properties':{'query':{'type':'string'}}}}])
        return desk
    class Crash(BaseException): pass
    def persist(event,payload):
        store.save_snapshot(bb)
        if event=='specialist_tool': raise Crash('after durable tool result')
    bb.persist_run_checkpoint=persist
    with pytest.raises(Crash): actor().run(1)
    paid=copy.deepcopy(_righe(bb.request_journal))
    assert len(paid)==len(bodies)==len(db.col_memos.calls)==1
    assert paid[0]['state']=='received'
    saved=copy.deepcopy(bb.specialist_checkpoints['quant:R1']['pending_tools'])
    assert len(saved)==1
    bb.specialist_checkpoints={}
    reopened=WeeklyRunStore(db,current);reopened.restore(bb)
    assert bb.specialist_checkpoints['quant:R1']['pending_tools']==saved
    bb.weekly_store=reopened
    bb.persist_run_checkpoint=lambda *a:reopened.save_snapshot(bb)
    assert actor().run(1).endswith(report)
    assert len(bodies)==2 and len(db.col_memos.calls)==1
    assert _righe(bb.request_journal)[0]==paid[0]
    delivered=next(m['content'] for m in bodies[1]['messages'] if m['role']=='tool')
    assert delivered==next(iter(saved.values()))['delivered_text']
    assert json.loads(next(iter(saved.values()))['output_json'])['count']==1
