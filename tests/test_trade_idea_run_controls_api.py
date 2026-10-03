"""Authenticated admission binds the accepted source pin and exact run activities."""
from copy import deepcopy
from datetime import datetime, timezone
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from bellomberg.api import trade_idea_routes as routes
from test_trade_idea_store import db_path, migrated, store
from test_trade_idea_pipeline import _priced_request


@pytest.fixture
def admission(migrated, monkeypatch):
    from bellomberg.api import bellomberg_api as api
    from bellomberg.agents.trade_idea import source_qualification_summary
    current = store(migrated)
    accepted = _priced_request(budget="30")
    qualification = deepcopy(accepted["source_qualification"])
    qualification['as_of'] = datetime.now(timezone.utc).date().isoformat()
    qualification["source_report"] = {"documents": [{"id": "filing", "archive_path": "PRIVATE_ARCHIVE_CANARY"}]}
    qualification["bundle"] = {"private_runtime_path": "PRIVATE_ARCHIVE_CANARY"}
    calls, workers = [], []
    def preflight(*_a, **_k):
        calls.append(1)
        identity = {"ticker": "TEST", "name": "Synthetic Company",
            "exchange": "XNAS", "currency": "USD", "status": "confirmed"}
        selected = (_k['source_qualifier']('TEST', identity, qualification['as_of'])
                    if 'source_qualifier' in _k else qualification)
        return {"ok": True, "reasons": [], "identity": {"ticker": "TEST", "name": "Synthetic Company",
            "exchange": "XNAS", "currency": "USD", "status": "confirmed"},
            "budget": {"status": "ready", "limit_usd": "30"},
            "models": [{"role": role, **spec} for role, spec in accepted["models"].items()],
            "catalog_snapshot": deepcopy(accepted["catalog_snapshot"]),
            "source_qualification": source_qualification_summary(selected),
            "_source_qualification": deepcopy(selected),
            "preparation": {"required": True, "paid": True, "status": "authorization_required"}}
    monkeypatch.setattr(routes, "_store", lambda *_a, **_k: current)
    monkeypatch.setattr(routes, "preflight_trade_idea", preflight)
    # This suite isolates HTTP grant/idempotency. Documentary counterproof is
    # exercised with real archive bytes by test_trade_idea_pm_sources.py.
    from bellomberg.valuation import trade_idea_model
    monkeypatch.setattr(trade_idea_model, 'recheck_accepted_sources', lambda saved, *_a, **_k: deepcopy(saved))
    monkeypatch.setattr(routes, "_spawn_worker", lambda run, *_a, **_k: workers.append(run))
    monkeypatch.setattr(api, "_SESSIONS", {"admission-session": time.time() + 3600})
    app = FastAPI()
    routes.install_trade_idea_routes(app, api.require_session, db_path=migrated,
                                     source_archive_root=migrated.parent / 'archive')
    body = {"ticker": "TEST", "pm_view": "Source-bound synthetic thesis", "view_source": "manual",
        "budget_limit_usd": "30", "idempotency_key": "isolated-admission",
        "cost_acknowledged": True, "authorization": deepcopy(accepted["authorization"])}
    with TestClient(app, headers={"X-BB-Token": "admission-session"}) as client:
        yield client, current, body, qualification, calls, workers


def test_public_preflight_excludes_private_source_bundle_and_document_paths(admission):
    client, current, body, qualification, calls, workers = admission
    response = client.post("/trade-ideas/preflight", json={key: body[key] for key in
        ("ticker", "pm_view", "view_source", "budget_limit_usd")})
    assert response.status_code == 200
    assert "PRIVATE_ARCHIVE_CANARY" not in response.text and "_source_qualification" not in response.text
    assert response.json()["source_qualification"]["fingerprint"] == body["authorization"]["source_fingerprint"]
    assert current.list_runs()["total"] == 0 and workers == []


@pytest.mark.parametrize("mutation", ["missing_grant", "false_consent", "changed_source_pin", "missing_preparation"])
def test_incomplete_admission_never_creates_a_run_or_worker(admission, mutation):
    client, current, body, qualification, calls, workers = admission
    body = deepcopy(body)
    if mutation == "missing_grant":
        body.pop("authorization")
    elif mutation == "false_consent":
        body["cost_acknowledged"] = False
    elif mutation == "changed_source_pin":
        qualification["fingerprint"] = "c" * 64
    else:
        body["authorization"].update(activities=["committee"], max_revision_rounds=0)
    response = client.post("/trade-ideas/runs", json=body)
    assert response.status_code in (422, 428), response.text
    assert current.list_runs()["total"] == 0 and workers == []


def test_accepted_http_run_and_exact_retry_keep_one_source_pin_and_worker(admission):
    client, current, body, qualification, calls, workers = admission
    checked = client.post('/trade-ideas/preflight', json={key: body[key] for key in
        ('ticker', 'pm_view', 'view_source', 'budget_limit_usd')})
    assert checked.status_code == 200, checked.text
    first = client.post("/trade-ideas/runs", json=body)
    assert first.status_code == 202, first.text
    run_id = first.json()["run_id"]
    qualification["fingerprint"] = "c" * 64
    retry = client.post("/trade-ideas/runs", json=body)
    assert retry.status_code == 202 and retry.json()["run_id"] == run_id
    assert calls == [1, 1] and workers == [run_id]
    detail = current.get_run(run_id)
    assert detail["run"]["authorization"] == body["authorization"]
    assert detail["run"]["source_qualification"]["fingerprint"] == body["authorization"]["source_fingerprint"]
    assert detail["cost"]["requests"] == 0
    changed = deepcopy(body)
    changed["authorization"].update(activities=["model_preparation", "committee"], max_revision_rounds=0)
    assert client.post("/trade-ideas/runs", json=changed).status_code == 409
    assert current.list_runs()["total"] == 1 and workers == [run_id]


def test_unauthenticated_admission_calls_no_preflight_or_worker(admission):
    client, current, body, qualification, calls, workers = admission
    assert client.post("/trade-ideas/runs", json=body, headers={"X-BB-Token": "expired"}).status_code == 401
    assert calls == workers == [] and current.list_runs()["total"] == 0
