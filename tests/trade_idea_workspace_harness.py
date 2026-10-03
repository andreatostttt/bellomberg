"""Isolated browser/API harness. Every issuer and economic observation is fictional.

This is a test server, never imported by the application. It refuses remote
connections, uses an explicit fresh SQLite path, and does not instantiate AI or SMTP.
"""
from copy import deepcopy
from datetime import datetime,timezone
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time

from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import HTMLResponse,FileResponse
import uvicorn

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def build(root, family='operating_fcff'):
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    if (root/"research.db").exists():
        raise ValueError("Harness requires a fresh explicit artifact directory")
    os.environ["BELLOMBERG_DATA_DIR"]=str(root/"runtime")
    import tempfile
    (root/"tmp").mkdir()
    tempfile.tempdir=str(root/"tmp")
    import sqlite3
    original_connect=sqlite3.connect
    def isolated_sqlite(database,*args,**kwargs):
        raw=str(database)
        if raw!=":memory:":
            from urllib.parse import urlsplit
            from urllib.request import url2pathname
            path=url2pathname(urlsplit(raw).path) if raw.startswith("file:") else raw
            if not Path(path).resolve().is_relative_to(root):
                raise RuntimeError("Harness SQLite access outside its explicit root")
        return original_connect(database,*args,**kwargs)
    sqlite3.connect=isolated_sqlite
    from bellomberg.storage import memory_db
    memory_db.MemoryDB._init_chroma=lambda self:None
    db=memory_db.MemoryDB(str(root/"research.db"),str(root/"unused-chroma"))
    from tools.migrations import migra_trade_idea
    migra_trade_idea.backend_alive=lambda:False
    migra_trade_idea.migra(db.db_path,apply=True)
    from bellomberg.storage.trade_idea_store import TradeIdeaStore
    store=TradeIdeaStore(db.db_path,mandate_loader=lambda:"a"*64)
    from test_trade_idea_economic import qualified,_operating_plan
    from bellomberg.valuation.trade_idea_model import prepare
    from test_trade_idea_store import request
    from trade_idea_fixtures import research_result,bind_workbook
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery
    from bellomberg.valuation.trade_idea_workspace import ResearchWorkspace
    # The declared synthetic acquisition is qualified on September 10 only.
    # Exercise the positive refresh on that date; stale-source rejection has
    # separate service/API tests and must never be defeated by extending expiry.
    clock={"now":datetime(2026,9,10,12,tzinfo=timezone.utc)}
    if family=='bank_residual_income':
        from test_trade_idea_families import sourced_family, family_factories
        from bellomberg.valuation.preparation_service import prepare_and_generate
        bundle, plan, documents=sourced_family(family_factories()[0])
        model=prepare_and_generate(bundle,documents=documents,propose=lambda *_:deepcopy(plan),
            output_dir=str(root/'models'),source_report={'source_plan':plan})
    else:
        q=qualified(root)
        model=prepare(q,lambda *_:deepcopy(_operating_plan()),root/"models")
    assert model["valuation_usability"]["usable"],model.get("error")
    ticker=model['ticker']
    thesis=db.save_valuation_thesis(ticker,valuation_payload=model,reuse_generation=True)
    assert thesis
    model["_thesis_saved"]={"thesis_id":thesis}
    runs=[]
    for index,language in enumerate(("it","en")):
        asked=request(ticker)
        asked.update(currency=model['currency'],language=language,view_text="PRIVATE_CANARY_PM_VIEW: fictional research fixture")
        run=store.create_run(asked,idempotency_key="research-browser-"+language)["run"]
        token=store.claim_run(run["id"])
        report=bind_workbook(research_result("rejected",run_id=run["id"]),model)
        report['ticker']=ticker
        for key in ("run_id","run_type","pm_view","destination"):report.pop(key,None)
        report["proposal"]=None
        store.update_progress(run["id"],token,"done",{"valuation_results":{ticker:model}})
        store.finish_run(run["id"],token,report,"incomplete",reason="Fictional offline browser fixture; no committee was called")
        store.route_result(run["id"],{})
        detail=store.get_run(run["id"])
        if family=='operating_fcff':
            manifest=prepare_trade_idea_delivery(detail["run"],detail["result"],[model],output_dir=root/"delivery"/run["id"],model_roots=[root],language=language)
            store.save_manifest(run["id"],manifest)
        runs.append(run["id"])
    def quote(ticker):
        day=clock["now"].date().isoformat()
        return {"status":"ok","source_id":"https://example.org/synthetic/quote","as_of":day,
            "retrieved_at":clock["now"].isoformat(),
            "data":{"info":{"symbol":ticker,"regularMarketPrice":12.,"regularMarketTime":clock["now"].timestamp(),
                "currency":"EUR","fullExchangeName":"TEST","exchangeTimezoneName":"Europe/Rome"}}}
    def sources(selected,as_of,directory):
        from hashlib import sha256
        fields=("field","driver","value","entity","period","unit","accounting_basis")
        if as_of<"2027-02-01":
            record=next(row for row in selected["acquisition_snapshot"]["case"]["records"] if row["driver"]=="opening_nwc")
            observation={key:deepcopy(record[key]) for key in fields}; observation["value"]=float(record["value"])+1.
            url="https://example.org/synthetic/issuer-correction.json"; publication="2026-09-10"
        else:
            preparations=[event for run in runs for event in workspace.events.list(run) if event["kind"]=="earnings_prepare"]
            expected=preparations[-1]["data"]["expectations"][0]
            observation={key:deepcopy(expected[key]) for key in fields}; observation["value"]=expected["value"]*.97
            url="https://example.org/synthetic/results"; publication="2027-02-01"
        text=json.dumps({"synthetic":True,"observations":[observation]},sort_keys=True)
        return {"documents":[{"id":"synthetic-source-"+publication,"url":url,"published_at":publication,
            "text":text,"sha256":sha256(text.encode()).hexdigest()}]}
    workspace=ResearchWorkspace(store,artifact_root=root,clock=lambda:clock["now"],price_fetcher=quote,source_fetcher=sources)
    from bellomberg.api import bellomberg_api as api
    from bellomberg.api.trade_idea_routes import _public_run,_public_detail
    from bellomberg.api.trade_idea_workspace_routes import install_trade_idea_workspace_routes
    token="isolated-research-browser-session"
    api._SESSIONS={token:time.time()+43200}
    app=FastAPI()
    install_trade_idea_workspace_routes(app,api.require_session,workspace=workspace)
    requests=[]
    preferences={"language":"it","selected":True,"source":"preferences"}
    @app.middleware("http")
    async def record(request:Request,call_next):
        response=await call_next(request)
        requests.append({"method":request.method,"path":request.url.path,"status":response.status_code})
        return response
    @app.get("/__qa/state")
    def qa_state():
        return {"runs":runs,"clock":clock["now"].isoformat(),"requests":requests,
            "events":{run:workspace.events.list(run) for run in runs},
            "costs":{run:workspace.costs(run) for run in runs}}
    @app.post("/__qa/clock")
    async def qa_clock(request:Request):
        data=await request.json()
        clock["now"]=datetime.fromisoformat(data["now"])
        return {"now":clock["now"].isoformat()}
    @app.post("/__qa/saved-operation")
    def qa_saved_operation():
        from uuid import uuid4
        identity=str(uuid4()); publish=workspace.events._publish
        def interrupted(*args,**kwargs):raise RuntimeError('Synthetic crash after durable result')
        workspace.events._publish=interrupted
        try:
            workspace.act(runs[0],'question',{'question':'Quale valore base?'},request_id=identity,generation_id=model['generation_id'])
        except RuntimeError as exc:
            if str(exc)!='Synthetic crash after durable result':raise
        finally:
            workspace.events._publish=publish
        return {'request_id':identity,'pending':workspace.events.pending(runs[0])}
    @app.get("/health")
    def health():return {"status":"ok","version":"isolated-workspace-fixture"}
    @app.get("/auth/status")
    def auth_status():return {"configured":True,"default_pin":False}
    @app.get("/preferences")
    def get_preferences():return preferences
    @app.put("/preferences")
    async def put_preferences(request:Request):
        body=await request.json()
        if body.get("language") not in ("it","en"):raise HTTPException(422,"language")
        preferences["language"]=body["language"]
        return preferences
    @app.get("/trade-ideas/active")
    def active():return {"run_id":None}
    @app.get("/trade-ideas/runs")
    def history():return {"runs":[_public_run(store.get_run(run)["run"]) for run in runs],"total":len(runs)}
    @app.get("/trade-ideas/runs/{run_id}")
    def detail(run_id:str):return _public_detail(store.get_run(run_id),run_id=run_id)
    @app.get("/portfolio")
    def portfolio():return {"positions":[],"cash_disponibile_eur":None,"cash_source":"uninitialized"}
    @app.get("/mandato")
    def mandate():return {"dichiarato":True,"causa":None,"dettaglio":None,"campi_mancanti":[],
        "valori":{},"origine":"synthetic","impronta":"fixture","campi":[],"errori":[],"esempio":{}}
    @app.get("/agents/list")
    def agents():return {"agents":[],"engines":{}}
    @app.get("/favorites")
    def favorites():return {"favorites":[]}
    @app.get("/tasks/scheduled")
    def tasks():return {"tasks":[]}
    @app.get("/db/backups")
    def backups():return {"backups":[],"count":0}
    @app.get("/fx")
    def fx():return {"rates":{"EUR":1}}
    distribution=Path(__file__).resolve().parents[1]/"app"/"dist"
    @app.get("/assets/{filename:path}")
    def asset(filename:str):
        path=(distribution/"assets"/filename).resolve()
        if not path.is_relative_to(distribution/"assets") or not path.is_file():raise HTTPException(404)
        return FileResponse(path)
    @app.get("/")
    def index(request:Request):
        document=(distribution/"index.html").read_text(encoding="utf-8")
        bootstrap="<script>window.bellomberg={apiUrl:location.origin};localStorage.setItem('bellomberg_token_v1',"+json.dumps(token)+");localStorage.setItem('bellomberg_unlocked_v1',JSON.stringify({ts:Date.now()}));</script>"
        return HTMLResponse(document.replace("<head>","<head>"+bootstrap))
    (root/"fixture.json").write_text(json.dumps({"runs":runs,"token":token,"db":str(root/"research.db"),
        "family":family,"ticker":ticker,"generation_id":model['generation_id']},indent=2),encoding="utf-8")
    return app


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True)
    parser.add_argument("--port",type=int,required=True)
    parser.add_argument('--family',choices=('operating_fcff','bank_residual_income'),default='operating_fcff')
    args=parser.parse_args()
    connect=socket.socket.connect
    def local_only(sock,address):
        if not isinstance(address,tuple) or address[0] not in ("127.0.0.1","::1"):
            raise RuntimeError("Isolated browser harness forbids external network")
        return connect(sock,address)
    socket.socket.connect=local_only
    resolve=socket.getaddrinfo
    def local_dns(host,*args,**kwargs):
        if host not in ("127.0.0.1","::1","localhost",None):
            raise RuntimeError("Isolated browser harness forbids external DNS")
        return resolve(host,*args,**kwargs)
    socket.getaddrinfo=local_dns
    uvicorn.run(build(args.root,args.family),host="127.0.0.1",port=args.port,log_level="warning")
