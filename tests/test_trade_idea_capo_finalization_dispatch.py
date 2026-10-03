"""Native Capo dispatch changes only an explicit, store-verified finalization.

Providers are captured before transport. Budget tests use temporary SQLite and
inject only the authorization resolver; ordinary reservation accounting stays real.
"""
from copy import deepcopy
import json
from threading import RLock
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea, chat_tools
from bellomberg.agents.specialists import Blackboard
from bellomberg.core import mandato_pm, research_analysis
from bellomberg.storage.trade_idea_store import BudgetBlocked
from bellomberg.valuation import company_dossier
from test_trade_idea_pipeline import _priced_request, _gate, FakeMessages
from test_trade_idea_store import db_path, migrated, store


POLICY = {'max_tokens': 20000, 'thinking': {'type': 'effort', 'effort': 'low'},
          'context_projection': 'sealed_all_rounds_red_team_dedup_v1'}


class BeforeTransport(BaseException):
    pass


@pytest.fixture
def research_board(monkeypatch):
    dossier = {'ticker': 'TEST', 'contract': 'company_dossier/1', 'documents': [],
        'records': [], 'status': 'research_required',
        'issues': [{'reason': 'Frozen missing primary source; no inferred facts'}]}
    monkeypatch.setattr(company_dossier, 'dossier_for_board', lambda *_a: deepcopy(dossier))
    monkeypatch.setattr(chat_tools, '_compatta_portfolio_live', lambda value: deepcopy(value))
    monkeypatch.setattr(mandato_pm, 'blocco_prompt', lambda _value: 'Frozen mandate; no allocation approved.')
    data = {desk: {1: 'Original independent thesis ' + desk + ': explicit source limitation. ' * 20,
                   2: 'Final review ' + desk + ': opposing view remains unresolved. ' * 20}
        for desk in trade_idea.TRADE_IDEA_DESKS}
    board = SimpleNamespace(data=data, _lock=RLock(), analysis_mode='fundamentals_research_v1',
        run_scope='trade_idea', run_id='offline-finalization', language='it', target_ticker='TEST',
        pm_view='Original PM view', candidate_history='Frozen history', source_qualification={},
        valuation_results={}, valuation_generations=[], valuation_attempts=[], orari_report={},
        tool_receipts=[], tool_log=[], mark_specialist_start=lambda *_a: None,
        record_usage=lambda *_a, **_k: None, mark_specialist_error=lambda *_a: None,
        mark_specialist_done=lambda *_a: None)
    board.read = lambda *args: Blackboard.read(board, *args)
    board.get_latest = lambda *args: Blackboard.get_latest(board, *args)
    research_analysis.seal_research_thesis(board, desks=trade_idea.TRADE_IDEA_DESKS)
    reference = research_analysis.research_reference(board)
    review = {'decisive_questions': ['Which observed fact can falsify this thesis?'],
        'objections': [{'id': 'review-' + desk, 'desk': desk, 'category': 'interpretation',
            'material': True, 'objection': 'The ' + desk + ' interpretation lacks primary confirmation.',
            'evidence_refs': [], 'requested_change': 'Keep the limit explicit.'}
            for desk in trade_idea.TRADE_IDEA_DESKS]}
    report = json.dumps(review)
    data['_red_team'] = {1: report}
    data['_red_research_review'] = {'research_ref': reference,
        'report_sha256': trade_idea._plan_digest(report)}
    data['_desk_research_reviews'] = {desk: {'round': 2, 'research_ref': reference,
        'report_sha256': trade_idea._plan_digest(board.read(desk,2))}
        for desk in trade_idea.TRADE_IDEA_DESKS}
    data['_objections'] = [{'objection': deepcopy(row), 'response': 'Uncertainty remains explicit.',
        'evidence_refs': [], 'state': 'answered', 'model_revision_id': None}
        for row in review['objections']]
    data['_decisive_questions'] = deepcopy(review['decisive_questions'])
    data['_research_review'] = {'research_ref': reference,
        'desks': deepcopy(data['_desk_research_reviews']),
        'red_team': deepcopy(data['_red_research_review']), 'objections': deepcopy(data['_objections'])}
    board.budget_gate = SimpleNamespace(wrap_client=lambda raw, role: raw)
    return board


def capture(board, calls):
    def before_transport(**kwargs):
        calls.append(deepcopy(kwargs)); raise BeforeTransport()
    trade_idea.run_trade_idea_capo(board, portfolio={'positions': [], 'cash': 'explicitly unavailable'},
        mandate={}, decision_context={'records': []}, sizing={'status': 'unavailable', 'reason': 'No fabricated size'},
        client=SimpleNamespace(messages=SimpleNamespace(stream=before_transport)))


def test_without_verified_grant_keeps_128k_max_and_full_original_red_block(research_board):
    calls=[]
    with pytest.raises(BeforeTransport): capture(research_board,calls)
    assert len(calls)==1 and calls[0]['max_tokens']==128000
    assert calls[0]['thinking']=={'type':'effort','effort':'max'}
    assert research_board.data['_red_team'][1] in calls[0]['messages'][0]['content']
    assert trade_idea._remaining_stage_token_cap(research_board,'capo')==128000


@pytest.mark.parametrize('wrapper', ['{}', '```json\n{}\n```', '```\r\n{}\r\n```'])
def test_capo_accepts_only_whole_json_preserving_raw_and_full_contract(research_board, wrapper):
    from trade_idea_fixtures import research_result
    payload = research_result('watch')
    for key in ('run_id', 'run_type', 'pm_view', 'destination'):
        payload.pop(key, None)
    payload['ticker'] = 'TEST'
    payload['valuation_refs'], payload['model_review'] = [], None
    raw = wrapper.format(json.dumps(payload))
    response = SimpleNamespace(content=[SimpleNamespace(type='text', text=raw)], stop_reason='end_turn')
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def get_final_message(self): return response
    research_board.write = lambda key, round_n, content: research_board.data.setdefault(key, {}).__setitem__(round_n, content)
    result = trade_idea.run_trade_idea_capo(research_board, portfolio={}, mandate={},
        client=SimpleNamespace(messages=SimpleNamespace(stream=lambda **_kwargs: Stream())))
    assert research_board.data['_capo'][3] == raw
    assert {key: value for key, value in result.items() if key not in {'run_id', 'run_type', 'pm_view'}} == payload


@pytest.mark.parametrize('content', [
    'Here is the result:\n```json\n{}\n```', '```json\n{}',
    '```json\n{}\n```\nMore commentary', '```json\n{}\n```\n```json\n{}\n```',
    '```json\n{"truncated":\n```', '```python\n{}\n```',
])
def test_capo_does_not_extract_or_repair_malformed_json(content):
    with pytest.raises(ValueError):
        trade_idea._parse_capo_json(content)


def test_authorized_20k_low_dispatch_changes_only_duplicate_red_and_exact_parameters(research_board):
    ordinary=[]
    with pytest.raises(BeforeTransport): capture(research_board,ordinary)
    original_data=deepcopy(research_board.data)
    research_board.budget_gate.capo_finalization=lambda: deepcopy(POLICY)
    authorized=[]
    with pytest.raises(BeforeTransport): capture(research_board,authorized)
    assert len(authorized)==1 and authorized[0]['max_tokens']==20000
    assert authorized[0]['thinking']=={'type':'effort','effort':'low'}
    original=deepcopy(ordinary[0]);actual=deepcopy(authorized[0])
    old='Red Team obbligatorio, obiezioni integrali:\n'+research_board.data['_red_team'][1]
    replacement=trade_idea._finalization_red_team_block(research_board,research_board.data['_red_team'][1])
    original['messages'][0]['content']=original['messages'][0]['content'].replace(old,replacement)
    original['max_tokens']=20000;original['thinking']={'type':'effort','effort':'low'}
    assert actual==original
    assert research_board.data==original_data
    assert trade_idea._remaining_stage_token_cap(research_board,'capo')==20000


@pytest.mark.parametrize('fault',['extra_red_field','different_objection','different_questions','unknown_projection'])
def test_deduplication_mismatch_blocks_before_any_provider(research_board,fault):
    board=research_board;policy=deepcopy(POLICY)
    board.budget_gate.capo_finalization=lambda: policy
    if fault=='extra_red_field':
        red=json.loads(board.data['_red_team'][1]);red['additional_economic_view']='Do not silently drop this.'
        board.data['_red_team'][1]=json.dumps(red)
        board.data['_red_research_review']['report_sha256']=trade_idea._plan_digest(board.data['_red_team'][1])
    elif fault=='different_objection':
        board.data['_research_review']['objections'][0]['objection']['objection']='Changed criticism'
    elif fault=='different_questions':
        board.data['_decisive_questions']=['A different final question']
    else:
        policy['context_projection']='drop_old_views'
    calls=[]
    with pytest.raises(ValueError,match='(exact content|projection)'):capture(board,calls)
    assert calls==[]


@pytest.mark.parametrize('grant',[None,POLICY])
def test_gate_rejects_low_without_grant_and_wrong_cap_or_desk_before_transport(migrated,monkeypatch,grant):
    current=store(migrated);request=_priced_request(budget='5')
    ident=current.create_run(request,idempotency_key='bad-final-request')['run']['id'];current.claim_run(ident)
    gate=_gate(current,ident,request)
    monkeypatch.setattr(gate,'capo_finalization',lambda: deepcopy(grant))
    fake=FakeMessages('0.001')
    call={'model':trade_idea.model_for_role('capo'),'max_tokens':20000,
        'thinking':{'type':'effort','effort':'low'},'messages':[{'role':'user','content':'Frozen final memo'}]}
    bad=[('capo',call)] if grant is None else [
        ('specialist:eventdesk',{**call,'model':trade_idea.model_for_role('specialist')}),
        ('capo',{**call,'max_tokens':14000}),('capo',{**call,'max_tokens':128000}),
        ('capo',{**call,'thinking':{'type':'effort','effort':'max'}})]
    for role,kwargs in bad:
        with pytest.raises(ValueError):
            gate.wrap_client(SimpleNamespace(messages=fake),role=role).messages.create(**kwargs)
    assert fake.calls==0 and current.get_run(ident)['cost']['requests']==0


@pytest.mark.parametrize('budget,allowed',[('0.05',True),('0.0001',False)])
def test_authorized_capo_still_uses_real_remaining_budget_before_transport(migrated,monkeypatch,budget,allowed):
    current=store(migrated);request=_priced_request(budget=budget)
    ident=current.create_run(request,idempotency_key='bounded-final-request')['run']['id'];current.claim_run(ident)
    gate=_gate(current,ident,request)
    monkeypatch.setattr(gate,'capo_finalization',lambda: deepcopy(POLICY))
    fake=FakeMessages('0.001')
    messages=gate.wrap_client(SimpleNamespace(messages=fake),role='capo').messages
    kwargs={'model':trade_idea.model_for_role('capo'),'max_tokens':20000,
        'thinking':{'type':'effort','effort':'low'},'messages':[{'role':'user','content':'Frozen final memo'}]}
    if allowed:
        messages.create(**kwargs)
        detail=current.get_run(ident)
        assert fake.calls==1 and detail['cost']['charged_usd']=='0.001'
        assert detail['cost']['unknown_requests']==0 and detail['cost']['reserved_usd']=='0'
    else:
        with pytest.raises(BudgetBlocked):messages.create(**kwargs)
        assert fake.calls==0 and current.get_run(ident)['cost']['requests']==0
