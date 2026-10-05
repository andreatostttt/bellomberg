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
    app.state.research_workspace=workspace
    with TestClient(app,headers={"X-BB-Token":"research-session"}) as client:
        yield client,workspace,current,run,model


class _Reply:
    """Risposta pubblica dell'azione, come la serializzava la vecchia POST /actions."""
    def __init__(self,status_code,payload):
        import json
        self.status_code,self._payload=status_code,payload
        self.text=json.dumps(payload,ensure_ascii=False)
    def json(self):
        return self._payload


def post(client,run,model,kind,data,key=None):
    # Contratto attuale (Excel archiviato, commit 1326312): le azioni via HTTP sono
    # rifiutate con 409 excel_archived PRIMA di toccare il servizio; consultazione e
    # download dei risultati salvati restano. L'azione si esegue quindi sul servizio
    # vero (gia' provato in test_trade_idea_workspace.py) e qui si prova lo strato HTTP.
    from bellomberg.api.trade_idea_workspace_routes import _public_event
    workspace=client.app.state.research_workspace
    key=key or str(uuid4())
    before=len(workspace.events.list(run))
    refused=client.post(f"/trade-ideas/runs/{run}/workspace/actions",json={
        "kind":kind,"data":data,"generation_id":model["generation_id"],"request_id":key})
    assert refused.status_code==409 and refused.json()["detail"]["code"]=="excel_archived", refused.text
    assert len(workspace.events.list(run))==before
    try:
        event=workspace.act(run,kind,data,request_id=key,generation_id=model["generation_id"])
    except (ValueError,TypeError) as exc:
        return _Reply(422,{"detail":str(exc)})
    return _Reply(200,_public_event(event))


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
    shapeless=api.post(url+"/actions",json={"kind":"question","data":{},"generation_id":model["generation_id"]})
    assert shapeless.status_code==409 and len(workspace.events.list(run))==1
    assert post(api,run,model,"question",{}).status_code==422
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
    refused=api.post(recover)
    assert refused.status_code==409 and refused.json()['detail']['code']=='excel_archived'
    assert api.get(url).json()['pending_actions'][0]['request_id']==key
    from bellomberg.api.trade_idea_workspace_routes import _public_event
    response=_public_event(workspace.recover(run,key))
    assert response['data']['status']=='answered'
    assert _public_event(workspace.recover(run,key))==response
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


def test_source_acquisition_and_document_refresh_are_archived_before_the_service(client):
    # Contratto attuale (Excel archiviato, commit 1326312): acquisizione fonti e refresh del
    # modello del workspace erano raggiungibili SOLO da POST /actions, ora 409 excel_archived
    # prima del servizio. Il test storico end-to-end e' in quarantena:
    # archive/private/attic/tests_excel_archiviato_20261005/test_trade_idea_workspace_api_legacy.py
    api,workspace,current,run,model=client
    before=len(workspace.events.list(run))
    calls=[]
    workspace.source_fetcher=lambda *_:calls.append("fetch") or pytest.fail("archived acquisition reached the fetcher")
    for kind,data in (("acquire_sources",{}),("refresh",{"kind":"documents","source_event_id":1})):
        reply=api.post(f"/trade-ideas/runs/{run}/workspace/actions",json={"kind":kind,"data":data,
            "generation_id":model["generation_id"],"request_id":str(uuid4())})
        assert reply.status_code==409 and reply.json()["detail"]["code"]=="excel_archived", reply.text
    assert calls==[] and len(workspace.events.list(run))==before
    assert current.get_run(run)["cost"]["requests"]==0
