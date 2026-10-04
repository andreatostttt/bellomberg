"""PM 04/10/2026: uncertain costs are reconciled from the provider's measured bill, never assumed free."""
import io
import json
import sqlite3
from urllib.error import HTTPError, URLError

import httpx
import pytest

from bellomberg.core import llm_client
from bellomberg.core.cost_reconciliation import reconcile_trade_idea_costs, reconcile_weekly_journal
from bellomberg.core.generation_lookup import fetch_generation, generation_id_from, settlement_from_lookup
from bellomberg.core.request_journal import RequestJournal
from test_trade_idea_store import db_path, migrated, request, store  # noqa: F401

GEN = "gen-1791036096-8gU9Zh0qum9DztNtIlpK"


def _lookup(model, total="1.357928", status=200, gid=GEN):
    body = {"data": {"id": gid, "model": model, "total_cost": total, "cancelled": True,
                     "tokens_prompt": 10, "tokens_completion": 5}} if status == 200 else {"error": {"code": status}}
    return {"generation_id": GEN, "http_status": status, "body": body}


def test_decision_rules_accept_only_this_generation_with_a_measured_bill():
    model = "anthropic/claude-opus-5.5"
    settled = settlement_from_lookup(_lookup(model + "-20260921"), requested_model=model)
    assert settled["status"] == "settled" and settled["charged_usd"] == "1.357928"
    assert settled["usage"]["cost_usd"] == "1.357928"
    assert "not a zero cost" in settlement_from_lookup(_lookup(model, status=404), requested_model=model)["reason"]
    for bad in (_lookup("anthropic/claude-other"), _lookup(model, total=None), _lookup(model, total="-1"),
                _lookup(model, gid="gen-someone-else")):
        assert settlement_from_lookup(bad, requested_model=model)["status"] == "pending"


def test_generation_id_comes_only_from_this_request_evidence():
    assert generation_id_from({"partial_response": {"id": GEN}}) == GEN
    assert generation_id_from({"response_id": "cmpl-abc"}) is None
    error = type("E", (), {"generation_id": GEN})()
    assert generation_id_from({}, error) == GEN


def test_fetch_returns_http_statuses_as_evidence_and_never_raises():
    class Response(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
    ok = fetch_generation(GEN, api_key="k", opener=lambda req, timeout: Response(json.dumps({"data": {"id": GEN}}).encode()))
    assert ok["http_status"] == 200 and ok["body"]["data"]["id"] == GEN and ok["body_sha256"]

    def not_found(req, timeout):
        raise HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"error":{"code":404}}'))
    assert fetch_generation(GEN, api_key="k", opener=not_found)["http_status"] == 404

    def offline(req, timeout):
        raise URLError("dns")
    assert fetch_generation(GEN, api_key="k", opener=offline)["http_status"] is None
    with pytest.raises(ValueError):
        fetch_generation("not-a-generation", api_key="k")


def _unknown_trade_idea_cost(migrated):
    current = store(migrated)
    run_id = current.create_run(request(), idempotency_key="reconcile")["run"]["id"]
    token = current.claim_run(run_id)
    model = request()["models"]["specialist"]["model"]
    current.reserve_cost(run_id, "req-uncertain", "specialist:macro", model, "0.5", worker_token=token)
    current.mark_cost_unknown(run_id, "req-uncertain", reason="ReadTimeout after send",
                              receipt={"complete": False, "partial_response": {"id": GEN, "model": model},
                                       "response_id": GEN, "model": model})
    current.finish_run(run_id, token, None, "incomplete", reason="provider timeout")
    return current, run_id, model


def _statuses(path, run_id):
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute("SELECT status FROM trade_idea_costs WHERE run_id=?", (run_id,))]


def test_trade_idea_preview_writes_nothing_and_apply_records_the_measured_bill(migrated):
    current, run_id, model = _unknown_trade_idea_cost(migrated)
    fetch = lambda gid: _lookup(model + "-20260921", total="0.31")
    preview = reconcile_trade_idea_costs(current, run_id, apply=False, fetch=fetch)
    assert preview["outcomes"][0]["status"] == "settleable" and _statuses(migrated, run_id) == ["unknown"]
    applied = reconcile_trade_idea_costs(current, run_id, apply=True, fetch=fetch)
    assert applied["settled"] == 1 and _statuses(migrated, run_id) == ["charged"]
    cost = current.get_run(run_id)["cost"]
    assert cost["unknown_requests"] == 0 and cost["charged_usd"] == "0.31"
    assert reconcile_trade_idea_costs(current, run_id, apply=True, fetch=fetch)["outcomes"] == []


def test_trade_idea_not_yet_indexed_generation_stays_unknown(migrated):
    current, run_id, model = _unknown_trade_idea_cost(migrated)
    result = reconcile_trade_idea_costs(current, run_id, apply=True, fetch=lambda gid: _lookup(model, status=404))
    assert result["pending"] == 1 and _statuses(migrated, run_id) == ["unknown"]


def _journal(tmp_path):
    return RequestJournal(tmp_path / "weekly-requests.sqlite", run_id="synthetic-weekly",
        authorization={"source": "offline-test"}, authorized_usd="10",
        metadata=lambda model: {"id": model, "context_length": 200000, "max_completion_tokens": 128000,
                                "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


def test_weekly_uncertain_request_is_settled_from_the_header_id_and_the_work_can_be_redone(tmp_path):
    dispatches = []

    def send(req):
        dispatches.append(json.loads(req.content))
        if len(dispatches) == 1:
            return httpx.Response(503, json={"error": {"message": "overloaded", "code": 503}},
                                  headers={"x-generation-id": GEN})
        return httpx.Response(200, json={"id": "gen-second-attempt", "model": "synthetic/model",
            "choices": [{"message": {"content": "Done"}, "finish_reason": "stop"}], "usage": {"cost": 0.00003}})
    client = llm_client.OpenRouterClient(api_key="offline", max_retries=4, trasporto=httpx.MockTransport(send))
    kwargs = {"model": "synthetic/model", "max_tokens": 100, "messages": [{"role": "user", "content": "Desk work"}]}
    journal = _journal(tmp_path)
    with pytest.raises(llm_client.APIStatusError):
        with llm_client.request_scope(journal, phase="weekly", agent="macro", round_n=1):
            client.messages.create(**kwargs)
    assert journal.summary()["unknown_requests"] == 1
    fetch = lambda gid: _lookup("synthetic/model", total="0.00002") if gid == GEN else pytest.fail(gid)
    result = reconcile_weekly_journal(journal.path, apply=True, fetch=fetch)
    assert result["settled"] == 1
    summary = _journal(tmp_path).summary()
    assert summary["unknown_requests"] == 0 and summary["cost_usd"] == pytest.approx(0.00002)
    with llm_client.request_scope(_journal(tmp_path), phase="weekly", agent="macro", round_n=1):
        answer = client.messages.create(**kwargs)
    assert answer.content[0].text == "Done" and len(dispatches) == 2
    final = _journal(tmp_path).summary()
    assert final["unknown_requests"] == 0 and final["cost_usd"] == pytest.approx(0.00005)
    assert RequestJournal.settle_unknown(journal.path, result["outcomes"][0]["request_id"], charged_usd="0.00002",
                                        generation_id=GEN, provider_generation={}, lookup_sha256="x") is False


def test_http_endpoint_previews_applies_and_refuses_a_running_run(migrated, monkeypatch):
    import time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api, trade_idea_routes as routes
    from bellomberg.core import generation_lookup
    current, run_id, model = _unknown_trade_idea_cost(migrated)
    monkeypatch.setattr(routes, "_store", lambda *_a, **_k: current)
    monkeypatch.setattr(api, "_SESSIONS", {"reconcile-session": time.time() + 3600})
    import bellomberg.core.cost_reconciliation as service
    monkeypatch.setattr(service, "fetch_generation", lambda gid: _lookup(model + "-20260921", total="0.31"))
    calls = []
    real = service.reconcile_trade_idea_costs
    monkeypatch.setattr(service, "reconcile_trade_idea_costs",
                        lambda store_, rid, apply=False: calls.append(apply) or real(
                            store_, rid, apply=apply, fetch=service.fetch_generation))
    app = FastAPI()
    routes.install_trade_idea_routes(app, api.require_session, db_path=migrated,
                                     source_archive_root=migrated.parent / "archive")
    with TestClient(app) as anonymous:
        assert anonymous.post(f"/trade-ideas/runs/{run_id}/costs/reconcile").status_code in (401, 403)
    with TestClient(app, headers={"X-BB-Token": "reconcile-session"}) as client:
        preview = client.post(f"/trade-ideas/runs/{run_id}/costs/reconcile")
        assert preview.status_code == 200 and preview.json()["outcomes"][0]["status"] == "settleable"
        assert _statuses(migrated, run_id) == ["unknown"]
        applied = client.post(f"/trade-ideas/runs/{run_id}/costs/reconcile", json={"apply": True})
        assert applied.status_code == 200 and applied.json()["settled"] == 1
        assert _statuses(migrated, run_id) == ["charged"] and calls == [False, True]
        assert client.post("/trade-ideas/runs/missing/costs/reconcile").status_code == 404
        running = current.create_run(request(), idempotency_key="still-running")["run"]["id"]
        current.claim_run(running)
        refused = client.post(f"/trade-ideas/runs/{running}/costs/reconcile", json={"apply": True})
        assert refused.status_code == 409 and "run ferma" in refused.json()["detail"]
