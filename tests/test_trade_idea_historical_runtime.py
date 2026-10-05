"""Historical document admission through HTTP and the isolated Trade Idea worker."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from bellomberg.agents import trade_idea
from bellomberg.api import trade_idea_routes as routes
from test_trade_idea_historical_admission import preparation_case
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_store import db_path, migrated, store


@pytest.fixture
def historical_runtime(migrated, tmp_path, monkeypatch):
    from bellomberg.valuation import trade_idea_model
    from test_sector_analysis import DAY
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(DAY + 'T12:00:00+00:00').astimezone(tz or timezone.utc)
    # Build and accept the signed historical fixture at its actual research
    # date, preserving all date/hash fields and the real admission recheck.
    for module in (trade_idea, trade_idea_model, routes):
        monkeypatch.setattr(module, 'datetime', FrozenDatetime)
    qualification, plan = preparation_case(tmp_path)
    current = store(migrated)
    priced = _priced_request(budget="30")
    workers = []

    def offline_preflight(ticker, pm_view, view_source, budget, **options):
        return trade_idea.preflight_trade_idea(ticker, pm_view, view_source, budget,
            active_checker=options["active_checker"],
            catalog_fetcher=lambda: priced["catalog_snapshot"],
            identity_resolver=options.get("identity_resolver", lambda _ticker: deepcopy(qualification["identity"])),
            key_checker=lambda: None, mandate_loader=lambda: {},
            source_qualifier=options.get("source_qualifier", lambda *_args, **_kwargs: deepcopy(qualification)),
            archive_root=options.get("archive_root", tmp_path))

    monkeypatch.setattr(routes, "_store", lambda *_args, **_kwargs: current)
    monkeypatch.setattr(routes, "paid_run_is_active", lambda: False)
    monkeypatch.setattr(routes, "preflight_trade_idea", offline_preflight)
    monkeypatch.setattr(routes, "_spawn_worker", lambda run_id, *_args, **_kwargs: workers.append(run_id))
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: "offline-session", db_path=migrated,
                                    source_archive_root=tmp_path)
    body = {"ticker": "SYNTH-EXT", "pm_view": "Synthetic source-bound thesis",
            "view_source": "manual", "budget_limit_usd": "30"}
    with TestClient(app) as client:
        yield client, current, qualification, plan, body, workers


def _accept(client, qualification, body, *, revision=False):
    checked = client.post("/trade-ideas/preflight", json=body)
    assert checked.status_code == 200 and checked.json()["ok"], checked.text
    grant = {"accepted": True, "source_fingerprint": qualification["fingerprint"],
             "activities": ["model_preparation", "committee"] + (["model_revision"] if revision else []),
             "max_revision_rounds": 1 if revision else 0}
    accepted = client.post("/trade-ideas/runs", json={**body,
        "authorization": grant, "cost_acknowledged": True,
        "idempotency_key": "historical-runtime"})
    assert accepted.status_code == 202, accepted.text
    return accepted.json()["run_id"], grant


def _worker_options(tmp_path):
    return {"lock_path": tmp_path / "paid.lock", "output_dir": tmp_path / "worker",
        "portfolio_loader": lambda: {"positions": [], "cash_disponibile_eur": 10000,
                                      "cash_source": "sqlite:cash_state", "stale_positions": [],
                                      "fx_incomplete": []},
        "mandate_loader": lambda: {}, "risk_loader": lambda: {"error": "offline unavailable"},
        "stress_loader": lambda: {"error": "offline unavailable"},
        "candidate_metrics_loader": lambda *_args: {"status": "unavailable"},
        "isolated_tool_dispatcher": lambda *_args, **_kwargs: {"ok": False, "error": "offline"},
        "isolated_facts_loader": lambda: "Synthetic offline facts only"}


def test_http_historical_preparation_admission_is_archived(historical_runtime):
    # Contratto attuale (Excel archiviato, commit 1326312): una qualificazione storica non di
    # ricerca puo' essere mostrata dal preflight, ma l'avvio a pagamento e' rifiutato prima di
    # creare run o worker. I test storici del percorso Excel sono in quarantena:
    # archive/private/attic/tests_excel_archiviato_20261005/test_trade_idea_historical_runtime_legacy.py
    client, current, qualification, _plan, body, workers = historical_runtime
    checked = client.post("/trade-ideas/preflight", json=body)
    assert checked.status_code == 200 and checked.json()["ok"], checked.text
    assert checked.json()["source_qualification"]["status"] == "preparation_required"
    assert "source_report" not in checked.text
    for activities in (["model_preparation", "committee"], ["committee"]):
        grant = {"accepted": True, "source_fingerprint": qualification["fingerprint"],
                 "activities": activities, "max_revision_rounds": 0}
        denied = client.post("/trade-ideas/runs", json={**body, "authorization": grant,
            "cost_acknowledged": True, "idempotency_key": "historical-" + "-".join(activities)})
        assert denied.status_code == 428 and "Nuove analisi senza Excel" in denied.text, denied.text
    assert current.list_runs()["total"] == 0 and workers == []


def test_worker_rechecks_accepted_research_pin_before_any_paid_work(migrated, tmp_path):
    # Portato in modalita' ricerca: garanzia conservata = se le fonti ricontrollate dal worker
    # non sono quelle del grant accettato, la run fallisce prima di sessione fonti, comitato e
    # qualunque chiamata pagata.
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    from bellomberg.valuation import trade_idea_model as model
    from test_trade_idea_pm_sources import IDENTITY, _profile_providers
    day = datetime.now(timezone.utc).date().isoformat()
    admission = model.research_admission(IDENTITY["ticker"], deepcopy(IDENTITY), day,
        archive_root=tmp_path, providers=_profile_providers(day), analysis_mode=RESEARCH_ANALYSIS_MODE)
    assert admission["status"] == "research_required", admission["reasons"]
    priced = _priced_request(budget="30")
    current = store(migrated)
    request = {**priced, "analysis_mode": RESEARCH_ANALYSIS_MODE, "ticker": IDENTITY["ticker"],
        "company_name": IDENTITY["name"], "exchange": IDENTITY["exchange"],
        "currency": IDENTITY["currency"], "source_qualification": admission,
        "authorization": {"accepted": True, "source_fingerprint": admission["fingerprint"],
                          "activities": ["committee"], "max_revision_rounds": 0}}
    run_id = current.create_run(request, idempotency_key="research-pin")["run"]["id"]
    changed = deepcopy(admission)
    changed["source_report"]["documents"] = [{"id": "changed-after-acceptance",
        "text": "Changed after acceptance.", "sha256": "d" * 64}]
    changed["fingerprint"] = model.source_fingerprint(changed)
    assert changed["fingerprint"] != admission["fingerprint"]

    detail = trade_idea.execute_trade_idea(run_id, store=current, **_worker_options(tmp_path),
        source_qualifier=lambda *_args, **_kwargs: deepcopy(changed),
        source_session_factory=lambda **_kwargs: pytest.fail("changed source reached the source session"),
        preparer_binder=lambda *_args, **_kwargs: pytest.fail("research run reached a preparer"),
        round_runner=lambda *_args: pytest.fail("changed source reached committee"),
        catalog_fetcher=lambda: priced["catalog_snapshot"])
    assert detail["run"]["technical_status"] == "failed", detail["run"]["reason"]
    assert "fingerprint" in detail["run"]["reason"], detail["run"]["reason"]
    assert detail["cost"]["requests"] == 0
    assert not list(tmp_path.rglob("*.xlsx"))
