"""Exact evidence IDs and a single native citations-only correction remain fail-closed."""
from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace
import pytest
from bellomberg.agents import trade_idea,red_team
from bellomberg.core import llm_client
from bellomberg.core.llm_client import Messaggio,TextBlock,ThinkingBlock,Usage
from test_trade_idea_live_consultation import board

@pytest.fixture
def review_board(board,monkeypatch):
    board.source_qualification={"fingerprint":"fixture-source","source_report":{"documents":[{"id":"primary","title":"Admitted filing","sha256":"a"*64}]}}
    board.model_phase="review"
    board.tool_receipts=[{"tool":"get_financial_history","success":True},{"tool":"get_portfolio_risk","success":False}]
    rows=[]
    for desk in ("macro","eventdesk","crypto","quant","options"):
        draft={"wacc":0.115,"terminal_growth":0.0275}
        row={"id":"consult-"+desk,"desk":desk,"requester":"fundamentals","source_fingerprint":"fixture-source",
             "question":"Explain the economic assumption for "+desk,"draft_assumptions":draft,"draft_sha256":trade_idea._plan_digest(draft),
             "evidence_refs":["primary"],"status":"complete","author_view_complete":True,"response":"Complete actual fixture opinion: "+desk,
             "fundamentals_decision":{"consultation_id":"consult-"+desk,"decision":"disagreed","rationale":"Retain with dissent explicitly recorded."}}
        rows.append(row)
    board.data["_model_consultations"]=rows
    board.data["_fundamentals_received_consultations"]={r["id"]:{**trade_idea._consultation_answer_identity(r),"response_id":"received-F-turn"} for r in rows}
    board.valuation_results={"TEST":{"ticker":"TEST","generation_id":"fixture-gen","snapshot_id":"fixture-snapshot","workbook_sha256":"b"*64,
        "acquisition_snapshot":{"case":{"records":[],"assumptions":{}}}}}
    def verified(b):
        p=b.valuation_results["TEST"]
        return [{k:p[k] for k in ("generation_id","snapshot_id","workbook_sha256")}]
    monkeypatch.setattr(trade_idea,"_verified_candidate_valuations",verified)
    return board

def review():
    return {"decisive_questions":["Which terminal assumption fails?"],"objections":[{"id":"q1","desk":"quant","category":"driver","material":True,
        "objection":"RONIC below WACC is an economic stress.","evidence_refs":["descriptive non-ID citation"],"requested_change":"Discuss, preserving the driver values."}]}

def test_catalog_and_guard_share_exact_ids_kinds_and_verified_model_identity(review_board):
    before=deepcopy(review_board.data)
    catalog=trade_idea.review_evidence_catalog(review_board)
    byid={e["id"]:e for e in catalog}
    assert byid["primary"]["kind"]=="admitted_document"
    assert byid["get_financial_history"]["kind"]=="retrieved_tool"
    assert "get_portfolio_risk" not in byid
    assert {e["id"] for e in catalog if e["kind"]=="desk_opinion"}=={"consult-"+d for d in ("macro","eventdesk","crypto","quant","options")}
    model=next(e for e in catalog if e["kind"]=="derived_model")
    assert len(model["id"])<=100 and model["model_ref"]["workbook_sha256"]=="b"*64
    assert model["source_fingerprint"]=="fixture-source"
    assert trade_idea._review_source_ids(review_board)==set(byid)
    assert review_board.data==before
    oldid=model["id"];review_board.valuation_results["TEST"]["generation_id"]="revised-native-gen"
    assert oldid not in trade_idea._review_source_ids(review_board)
    generation_id=next(e["id"] for e in trade_idea.review_evidence_catalog(review_board) if e["kind"]=="derived_model")
    review_board.valuation_results["TEST"]["workbook_sha256"]="c"*64
    assert generation_id not in trade_idea._review_source_ids(review_board)

def test_unverified_model_has_no_derived_reference_or_peer_alias(review_board,monkeypatch):
    monkeypatch.setattr(trade_idea,"_verified_candidate_valuations",lambda b:[])
    assert {e["kind"] for e in trade_idea.review_evidence_catalog(review_board)}=={"admitted_document","retrieved_tool"}
    assert "consult-quant" not in trade_idea._review_source_ids(review_board)

@pytest.mark.parametrize("bad",["failed","unread","not_received","not_accepted","foreign"])
def test_catalog_omits_failed_unread_foreign_or_unaccepted_consultation(review_board,bad):
    row=review_board.data["_model_consultations"][-1]
    if bad=="failed":row["status"]="failed"
    elif bad=="unread":row["author_view_complete"]=False
    elif bad=="not_received":review_board.data["_fundamentals_received_consultations"].pop(row["id"])
    elif bad=="not_accepted":row["fundamentals_decision"]=None
    else:
        row["source_fingerprint"]="foreign-source"
        with pytest.raises(ValueError,match="identity, source, draft or evidence"):
            trade_idea._review_source_ids(review_board)
        return
    assert row["id"] not in trade_idea._review_source_ids(review_board)
    assert "primary descriptive citation" not in trade_idea._review_source_ids(review_board)

def test_malformed_audit_raises_instead_of_silently_omitting_opinions(review_board):
    review_board.data["_model_consultations"]={"malformed":"audit"}
    with pytest.raises(ValueError,match="Consultation audit"):
        trade_idea.review_evidence_catalog(review_board)

def test_author_default_unchanged_committee_exposes_catalog_for_red_and_R2(review_board):
    author=trade_idea.candidate_model_context(review_board)
    assert author==trade_idea.candidate_model_context(review_board,purpose="author")
    assert "review_evidence_catalog" not in author
    committee=trade_idea.candidate_model_context(review_board,purpose="committee")
    assert committee["review_evidence_catalog"]==trade_idea.review_evidence_catalog(review_board)
    assert committee["method_records"]==author["method_records"]
    assert "exact" in committee["evidence_refs_contract"].lower()

def native(review_board,monkeypatch,corrected,*,stop="end_turn",text=True,cost=0.001):
    calls=[];roles=[]
    model=trade_idea.model_for_role("red_team")
    response=Messaggio("received-native-corrector",model,[ThinkingBlock("synthetic reasoning never printed")]+([TextBlock(json.dumps(corrected))] if text else []),
        stop,usage=Usage(10,5,0,0,reasoning_tokens=0,cost_usd=cost))
    def create(**kwargs):calls.append(kwargs);return response
    review_board.budget_gate=SimpleNamespace(wrap_client=lambda client,role:roles.append(role) or client)
    monkeypatch.setattr(llm_client,"OpenRouterClient",lambda **kw:SimpleNamespace(messages=SimpleNamespace(create=create)))
    original=json.dumps(review())
    result=trade_idea._run_exact_model_red_team(review_board,None,partial(red_team.run_red_team,citation_correction=original))
    return result,calls,roles,original

def test_single_native_correction_changes_only_refs_preserves_usage_and_exact_attestation(review_board,monkeypatch):
    corrected=review();corrected["objections"][0]["evidence_refs"]=["primary","consult-quant"]
    result,calls,roles,original=native(review_board,monkeypatch,corrected)
    assert json.loads(result)==corrected and json.loads(original)==review()
    assert len(calls)==1 and roles==["red_team"]
    assert calls[0]["max_tokens"]==65536 and calls[0]["model"]==trade_idea.model_for_role("red_team")
    assert calls[0]["thinking"]=={"type":"effort","effort":"max"} and "tools" not in calls[0]
    assert calls[0]["response_format"]["json_schema"]["name"]=="trade_idea_committee_review"
    assert review_board.data["_red_team_citation_correction"]["evidence_refs_only"] is True
    assert review_board.data["_red_model_review"]["model_ref"]["generation_id"]=="fixture-gen"
    assert len(review_board.usage_log)==1 and review_board.usage_log[0]["api_calls"]==1

@pytest.mark.parametrize("field",["id","desk","material","objection","requested_change","decisive_questions","unknown_id"])
def test_correction_rejects_any_analysis_edit_or_unknown_id(review_board,monkeypatch,field):
    corrected=review();corrected["objections"][0]["evidence_refs"]=["primary"]
    if field=="decisive_questions":corrected[field]=["Changed analysis question"]
    elif field=="unknown_id":corrected["objections"][0]["evidence_refs"]=["primary plus fabricated prose"]
    else:corrected["objections"][0][field]={"id":"new-q","desk":"macro","material":False,"objection":"Changed economics","requested_change":"Change the model"}[field]
    with pytest.raises(RuntimeError):native(review_board,monkeypatch,corrected)
    assert "_red_model_review" not in review_board.data
    assert "_red_team_citation_correction" not in review_board.data

@pytest.mark.parametrize("stop,text,cost",[("max_tokens",True,0.001),("end_turn",False,0.001),("end_turn",True,None)])
def test_incomplete_or_unknown_usage_correction_never_attests(review_board,monkeypatch,stop,text,cost):
    corrected=review();corrected["objections"][0]["evidence_refs"]=["primary"]
    with pytest.raises(RuntimeError):native(review_board,monkeypatch,corrected,stop=stop,text=text,cost=cost)
    assert "_red_model_review" not in review_board.data
    assert "_red_team_citation_correction" not in review_board.data

def test_weekly_cannot_enter_citation_correction_mode(review_board):
    review_board.run_scope="weekly"
    with pytest.raises(ValueError,match="Trade Idea"):
        red_team.run_red_team(review_board,citation_correction=json.dumps(review()))
