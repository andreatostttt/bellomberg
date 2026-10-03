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


def test_http_preflight_admits_historical_work_but_requires_preparation_grant(historical_runtime):
    client, current, qualification, _plan, body, workers = historical_runtime
    checked = client.post("/trade-ideas/preflight", json=body)
    assert checked.status_code == 200, checked.text
    public = checked.json()
    assert public["ok"] is True and public["source_qualification"]["status"] == "preparation_required"
    assert public["preparation"] == {"required": True, "paid": True, "status": "historical_required"}
    assert public["source_qualification"]["fingerprint"] == qualification["fingerprint"]
    assert "source_report" not in checked.text and current.list_runs()["total"] == 0

    without_preparation = {"accepted": True, "source_fingerprint": qualification["fingerprint"],
        "activities": ["committee"], "max_revision_rounds": 0}
    denied = client.post("/trade-ideas/runs", json={**body,
        "authorization": without_preparation, "cost_acknowledged": True,
        "idempotency_key": "historical-without-preparation"})
    assert denied.status_code == 428
    assert current.list_runs()["total"] == 0 and workers == []

    run_id, grant = _accept(client, qualification, body)
    stored = current.get_run(run_id)
    assert workers == [run_id]
    assert stored["run"]["authorization"] == grant
    assert stored["run"]["source_qualification"]["status"] == "preparation_required"
    assert stored["cost"]["requests"] == 0
    detail = client.get("/trade-ideas/runs/" + run_id)
    assert detail.status_code == 200
    assert detail.json()["run"]["source_qualification"]["status"] == "preparation_required"
    assert "source_report" not in detail.text


def test_worker_checks_history_and_common_excel_at_r1_before_model_review(
        historical_runtime, tmp_path, monkeypatch):
    client, current, qualification, plan, body, _workers = historical_runtime
    run_id, _grant = _accept(client, qualification, body)
    stages, rounds = [], []
    checkpoint_path = tmp_path / "worker" / "model" / "historical-preparation.json"

    def offline_stage(_dossier, contract):
        stage = contract["preparation_stage"]
        scope, names = stage["scope"], stage["drivers"]
        stages.append((scope, tuple(names)))
        if "capdev_amortization_years" in names or "revenue_growth" in names:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            assert checkpoint["status"] == "historical_qualified"
            assert checkpoint["admission_fingerprint"] == qualification["fingerprint"]
        values = plan["model"] if scope == "model" else plan["scenarios"][scope]
        return {"drivers": {name: deepcopy(values[name]) for name in names},
                "rationale": "Offline synthetic source-linked economic reasoning"}

    def first_round(board, number):
        rounds.append(number)
        if number == 0:
            assert not board.valuation_results
            return
        assert number == 1
        from bellomberg.valuation.trade_idea_model import prepare
        model = prepare(board.source_qualification, board.valuation_preparer, checkpoint_path.parent)
        trade_idea._record_candidate_model(board, model)
        assert trade_idea._verified_candidate_valuations(board)
        model = board.valuation_results[body["ticker"]]
        assert model["valuation_usability"]["usable"] is True
        assert Path(model["path"]).is_file() and checkpoint_path.is_file()
        current.request_stop(run_id)
        raise RuntimeError("Intentional offline stop after the historical R1 compiler boundary")

    monkeypatch.setattr(trade_idea, "deliver_trade_idea",
                        lambda *_args, **_kwargs: pytest.fail("cancelled worker delivered"))
    detail = trade_idea.execute_trade_idea(run_id, store=current, **_worker_options(tmp_path),
        source_qualifier=lambda *_args, **_kwargs: deepcopy(qualification),
        preparer_binder=lambda *_args, **_kwargs: (offline_stage, {"status": "offline"}),
        round_runner=first_round,
        catalog_fetcher=lambda: _priced_request(budget="30")["catalog_snapshot"])
    assert detail["run"]["technical_status"] == "cancelled", detail["run"]["reason"]
    assert rounds == [0, 1] and stages
    assert checkpoint_path.is_file() and detail["cost"]["requests"] == 0


def test_worker_missing_historical_balance_never_starts_model_review_or_writes_excel(
        historical_runtime, tmp_path, monkeypatch):
    client, current, qualification, plan, body, _workers = historical_runtime
    run_id, _grant = _accept(client, qualification, body)
    stages, rounds = [], []

    def incomplete_stage(_dossier, contract):
        stage = contract["preparation_stage"]
        scope, names = stage["scope"], stage["drivers"]
        stages.extend(names)
        values = plan["model"] if scope == "model" else plan["scenarios"][scope]
        return {"drivers": {name: None if name == "equity_adjustments" else deepcopy(values[name])
                            for name in names}, "rationale": "Historical balance not proven"}

    monkeypatch.setattr(trade_idea, "deliver_trade_idea", lambda *_args, **_kwargs: None)
    def historical_boundary(board, number):
        rounds.append(number)
        if number == 1:
            from bellomberg.valuation.trade_idea_model import prepare
            model = prepare(board.source_qualification, board.valuation_preparer, tmp_path / "worker" / "model")
            trade_idea._record_candidate_model(board, model)
    detail = trade_idea.execute_trade_idea(run_id, store=current, **_worker_options(tmp_path),
        source_qualifier=lambda *_args, **_kwargs: deepcopy(qualification),
        preparer_binder=lambda *_args, **_kwargs: (incomplete_stage, {"status": "offline"}),
        round_runner=historical_boundary,
        red_runner=lambda *_args, **_kwargs: pytest.fail("Unverified history reached model review"),
        catalog_fetcher=lambda: _priced_request(budget="30")["catalog_snapshot"])
    assert detail["run"]["technical_status"] == "incomplete", detail["run"]["reason"]
    assert "equity_adjustments" in stages
    assert "capdev_amortization_years" in stages and "revenue_growth" in stages
    assert stages.index("equity_adjustments") > stages.index("revenue_growth")
    assert rounds == [0, 1] and detail["cost"]["requests"] == 0
    assert not list((tmp_path / "worker").rglob("*.xlsx"))
    checkpoint = json.loads((tmp_path / "worker" / "model" / "historical-preparation.json").read_text())
    assert checkpoint['status'] == 'historical_qualified'
    assert all('net_debt' in values and 'equity_adjustments' not in values
               for values in checkpoint['candidate']['plan']['scenarios'].values())


def test_worker_rechecks_accepted_document_pin_before_preparation(
        historical_runtime, tmp_path):
    client, current, qualification, _plan, body, _workers = historical_runtime
    run_id, _grant = _accept(client, qualification, body)
    changed = deepcopy(qualification)
    changed["source_report"]["documents"][0]["text"] += " Changed after acceptance."

    detail = trade_idea.execute_trade_idea(run_id, store=current, **_worker_options(tmp_path),
        source_qualifier=lambda *_args, **_kwargs: changed,
        preparer_binder=lambda *_args, **_kwargs: pytest.fail("changed source reached preparer"),
        round_runner=lambda *_args: pytest.fail("changed source reached committee"),
        catalog_fetcher=lambda: _priced_request(budget="30")["catalog_snapshot"])
    assert detail["run"]["technical_status"] == "failed", detail["run"]["reason"]
    assert detail["cost"]["requests"] == 0


def test_fundamentals_can_revise_verified_historical_candidate_with_exact_grant(
        historical_runtime, migrated, tmp_path, monkeypatch):
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.valuation.trade_idea_model import prepare

    client, current, qualification, plan, body, _workers = historical_runtime
    run_id, grant = _accept(client, qualification, body, revision=True)
    assert grant["activities"] == ["model_preparation", "committee", "model_revision"]
    token = current.claim_run(run_id)
    catalog = current.get_run(run_id)["run"]["catalog_snapshot"]
    gate = trade_idea.TradeIdeaBudgetGate(current, run_id, token, catalog,
        catalog_fetcher=lambda: catalog)

    def stage_from(values):
        def propose(_dossier, contract):
            stage = contract["preparation_stage"]
            scope, names = stage["scope"], stage["drivers"]
            source = values["model"] if scope == "model" else values["scenarios"][scope]
            return {"drivers": {name: deepcopy(source[name]) for name in names},
                    "rationale": "Synthetic source-linked analyst review"}
        return propose

    initial = prepare(qualification, stage_from(plan), tmp_path / "worker" / "initial")
    assert initial["valuation_usability"]["usable"] is True
    assert initial["historical_preparation"]["status"] == "historical_qualified"
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "review-heartbeat.json",
        run_scope="trade_idea", run_id=run_id, target_ticker=body["ticker"], budget_gate=gate)
    board.model_registry = trade_idea._model_registry(migrated)
    board.model_roots = [tmp_path / "worker"]
    board.source_qualification = deepcopy(qualification)
    trade_idea._record_candidate_model(board, initial)
    refs = trade_idea._verified_candidate_valuations(board)
    assert refs and refs[0]["generation_id"] == initial["generation_id"]
    board.data["_model_review"] = {"initial_refs": refs, "final_refs": refs,
        "revision_log": [], "objections": []}
    board.current_round = 2

    requested = [deepcopy(row) for row in initial["acquisition_snapshot"]["case"]["records"]
                 if row["driver"] == "wacc"]
    assert requested
    for row in requested:
        row["value"] = .12
    document_id = qualification["source_report"]["documents"][0]["id"]
    response = trade_idea.handle_trade_idea_review_tool(board, "fundamentals",
        "review_candidate_model", {"generation_id": initial["generation_id"],
        "action": "revise", "rationale": "Raise the documented synthetic discount rate",
        "evidence_refs": [document_id], "changes": {"method_records": requested},
        "needs_paid_preparation": True})
    assert response["ok"] is True and response["status"] == "queued", response

    revised_plan = deepcopy(plan)
    for scenario in revised_plan["scenarios"].values():
        scenario["wacc"]["value"] = .12
    calls = []
    def fake_revision_provider(_gate, _ticker, *, output_dir, phase):
        calls.append((phase, Path(output_dir)))
        assert phase == "revision"
        return stage_from(revised_plan), {"status": "offline"}
    monkeypatch.setattr(trade_idea, "bind_trade_idea_preparer", fake_revision_provider)

    audit = trade_idea._finalize_model_review(board, gate,
        output_dir=tmp_path / "worker" / "revision")
    final = board.valuation_results[body["ticker"]]
    assert calls and len(calls) == 1
    assert final["valuation_usability"]["usable"] is True
    assert final["generation_id"] != initial["generation_id"]
    assert final["fair_value_base"] != initial["fair_value_base"]
    assert Path(final["path"]).is_file()
    assert final["preparation"]["provenance"]["trade_idea_revision"]["previous_generation_id"] == initial["generation_id"]
    assert audit["revision_log"][0]["status"] == "applied"
    assert audit["final_refs"][0]["generation_id"] == final["generation_id"]
    assert current.get_run(run_id)["cost"]["requests"] == 0
