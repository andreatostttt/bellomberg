"""Research UI API operates the real service, archive and exact-byte artifacts."""
from hashlib import sha256
from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_trade_idea_workspace import research, db


@pytest.fixture
def client(research,monkeypatch):
    from bellomberg.api import bellomberg_api as api
    from bellomberg.api.trade_idea_workspace_routes import install_trade_idea_workspace_routes
    workspace,current,run,model = research
    monkeypatch.setattr(api,"_SESSIONS",{"research-session":time.time()+3600})
    app=FastAPI()
    install_trade_idea_workspace_routes(app,api.require_session,workspace=workspace)
    with TestClient(app,headers={"X-BB-Token":"research-session"}) as client:
        yield client,workspace,current,run,model


def post(client,run,model,kind,data,key=None):
    return client.post(f"/trade-ideas/runs/{run}/workspace/actions",json={
        "kind":kind,"data":data,"generation_id":model["generation_id"],"request_id":key or str(uuid4())})


def test_auth_required_real_persisted_question_and_shape_rejected(client):
    api,workspace,current,run,model=client
    url=f"/trade-ideas/runs/{run}/workspace"
    assert api.get(url,headers={"X-BB-Token":"expired"}).status_code==401
    first=post(api,run,model,"question",{"question":"Quale valore base?"})
    assert first.status_code==200, first.text
    assert first.json()["data"]["citations"][0]["cell"]
    saved=api.get(url).json()
    assert saved["history"][0]["id"]==first.json()["id"]
    assert "acquisition_snapshot" not in first.text and "C:\\" not in first.text
    assert api.post(url+"/actions",json={"kind":"question","data":{},"generation_id":model["generation_id"]}).status_code==422
    assert post(api,run,dict(model,generation_id="missing"),"question",{"question":"value"}).status_code==422


def test_simulation_download_bytes_idempotence_and_tampering(client):
    api,workspace,current,run,model=client
    data={"label":"Personal discount","rationale":"Manual sensitivity", "changes":[{"scenario":"base","driver":"wacc","value":.12}]}
    key=str(uuid4())
    first=post(api,run,model,"simulate",data,key)
    assert first.status_code==200,first.text
    assert post(api,run,model,"simulate",data,key).json()==first.json()
    artifact=first.json()["artifact"]
    blob=api.get(artifact["download_url"])
    assert blob.status_code==200
    assert sha256(blob.content).hexdigest()==artifact["sha256"]
    saved=workspace.events.get(run,first.json()["id"])
    assert blob.content==Path(saved["data"]["model"]["path"]).read_bytes()
    Path(saved["data"]["model"]["path"]).write_bytes(b"tampered")
    assert api.get(artifact["download_url"]).status_code==409
    assert current.get_run(run)["cost"]["requests"]==0


def test_saved_operation_recovery_is_authenticated_visible_and_never_recomputed(client,monkeypatch):
    api,workspace,current,run,model=client
    key=str(uuid4()); publish=workspace.events._publish
    monkeypatch.setattr(workspace.events,'_publish',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('publication interrupted')))
    with pytest.raises(RuntimeError,match='publication interrupted'):
        workspace.act(run,'question',{'question':'Quale valore base?'},request_id=key,generation_id=model['generation_id'])
    monkeypatch.setattr(workspace.events,'_publish',publish)
    url=f'/trade-ideas/runs/{run}/workspace'
    pending=api.get(url).json()['pending_actions']
    assert pending[0]['status']=='ready_to_recover' and pending[0]['request_id']==key
    assert 'path' not in str(pending) and 'request_sha256' not in str(pending)
    monkeypatch.setattr(workspace,'_question',lambda *a:pytest.fail('Recovery must not compute'))
    recover=url+'/actions/'+key+'/recover'
    assert api.post(recover,headers={'X-BB-Token':'expired'}).status_code==401
    response=api.post(recover)
    assert response.status_code==200 and response.json()['data']['status']=='answered'
    assert api.post(recover).json()==response.json()
    assert not api.get(url).json()['pending_actions']
    assert len(workspace.events.list(run))==1 and current.get_run(run)['cost']['requests']==0


def test_objection_monitor_comparison_cost_and_missing_export_errors(client):
    api,workspace,current,run,model=client
    response=post(api,run,model,"objection",{"text":"Validate margin against next filing"})
    assert response.status_code==200
    reply=post(api,run,model,"objection_reply",{"objection_id":response.json()["id"],"text":"Keep open","status":"open"})
    assert reply.status_code==200
    monitor=post(api,run,model,"monitor",{"active":True,"metric":"price","operator":"lt","threshold":11.})
    checked=post(api,run,model,"monitor_check",{"monitor_id":monitor.json()["id"]})
    assert checked.json()["data"]["status"]=="stale"
    assert post(api,run,model,"compare_runs",{"other_run_id":run}).status_code==200
    assert post(api,run,model,"compare_ideas",{"run_ids":[run]}).status_code==200
    assert api.get(f"/trade-ideas/runs/{run}/workspace/costs").json()["requests"]==0
    assert post(api,run,model,"export",{"scope":"private"}).status_code==422
    assert post(api,run,model,"export",{"scope":"shareable","exclusions_acknowledged":False}).status_code==422


def test_source_acquisition_sealed_then_real_document_refresh_and_tamper_rejection(client,tmp_path):
    from copy import deepcopy
    from datetime import datetime,timezone
    from test_trade_idea_source_refresh import model_and_source,DAY
    from test_trade_idea_store import request,result
    from bellomberg.storage.memory_db import MemoryDB
    api,workspace,current,_,_=client
    model,document,_,_=model_and_source(tmp_path/"source-model")
    database=MemoryDB.__new__(MemoryDB); database.db_path=current.db_path
    thesis=database.save_valuation_thesis(model["ticker"],valuation_payload=model,reuse_generation=True)
    assert thesis
    model["_thesis_saved"]={"thesis_id":thesis}
    asked=request(model["ticker"]); asked.update(currency="EUR")
    run=current.create_run(asked,idempotency_key=str(uuid4()))["run"]["id"]
    worker=current.claim_run(run)
    report=result(model["ticker"],proposal=False)
    report["valuation_refs"]=[{"generation_id":model["generation_id"],"snapshot_id":model["snapshot_id"],
        "valuation_date":model["valuation_date"],"interpretation":"Declared source refresh fixture"}]
    current.update_progress(run,worker,"done",{"valuation_results":{model["ticker"]:model}})
    current.finish_run(run,worker,report,"incomplete",reason="Synthetic source refresh API")
    workspace.clock=lambda:datetime.fromisoformat(DAY).replace(tzinfo=timezone.utc)
    original=Path(model["path"]).read_bytes()
    committee_result=deepcopy(current.get_run(run)["result"])
    # A large verified source correction can move FV far from price. The model
    # remains usable, while the old committee conclusion still requires review.
    import json
    large=deepcopy(document)
    body=json.loads(large["text"]); body["observations"][0]["value"]=120.
    large["text"]=json.dumps(body,sort_keys=True); large["sha256"]=sha256(large["text"].encode()).hexdigest()
    workspace.source_fetcher=lambda *_:{"documents":[deepcopy(large)]}
    first=post(api,run,model,"acquire_sources",{})
    assert first.status_code==200,first.text
    refreshed=post(api,run,model,"refresh",{"kind":"documents","source_event_id":first.json()["id"]})
    assert refreshed.status_code==200 and refreshed.json()["data"]["status"]=="ready",refreshed.text
    refreshed_model=refreshed.json()["data"]["model"]
    assert refreshed_model["valuation_usability"]["usable"] is True
    assert refreshed_model["generation_id"]!=model["generation_id"]
    assert refreshed_model["snapshot_id"]!=model["snapshot_id"]
    assert refreshed_model["fair_value_base"]!=model["fair_value_base"]
    saved=workspace.events.get(run,refreshed.json()["id"])["data"]["model"]
    records=[row for row in saved["acquisition_snapshot"]["case"]["records"]
             if row["driver"]=="opening_nwc" and row["scenario"]=="model"]
    assert len(records)==1 and records[0]["value"]==120.
    assert records[0]["source_locator"]==large["id"]
    assert refreshed.json()["data"]["invalidates_conclusion"]
    assert current.get_run(run)["run"]["phase"]=="review_required"
    assert current.get_run(run)["result"]==committee_result
    assert api.get(f"/trade-ideas/runs/{run}/workspace").json()["conclusions_status"]=="review_required"
    assert Path(model["path"]).read_bytes()==original
    workspace.source_fetcher=lambda *_:{"documents":[deepcopy(document)]}
    rejected=post(api,run,model,"acquire_sources",{"documents":[document]})
    assert rejected.status_code==422
    acquired=post(api,run,model,"acquire_sources",{})
    assert acquired.status_code==200,acquired.text
    assert "source_catalog_path" not in acquired.text and "C:\\" not in acquired.text
    updated=post(api,run,model,"refresh",{"kind":"documents","source_event_id":acquired.json()["id"]})
    assert updated.status_code==200,updated.text
    assert updated.json()["data"]["status"]=="ready",updated.text
    variant=updated.json()["data"]["model"]
    assert variant["generation_id"]!=model["generation_id"] and variant["fair_value_base"]!=model["fair_value_base"]
    assert current.get_run(run)["run"]["phase"]=="review_required"
    assert Path(model["path"]).read_bytes()==original
    assert api.get(updated.json()["artifact"]["download_url"]).status_code==200
    source_event=workspace.events.get(run,acquired.json()["id"])
    Path(source_event["data"]["source_catalog_path"]).write_text("tampered",encoding="utf-8")
    invalid=post(api,run,model,"refresh",{"kind":"documents","source_event_id":acquired.json()["id"]})
    assert invalid.status_code==422 and "changed" in invalid.text
    assert current.get_run(run)["cost"]["requests"]==0
