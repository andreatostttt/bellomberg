"""Research workspace actions use immutable run evidence and the common engine."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from test_trade_idea_store import db_path, migrated, request, result, store
from test_sector_operating_drivers import bundle_for
from test_valuation_snapshot_persistence import db
from bellomberg.valuation.dcf_engine import generate_valuation


@pytest.fixture
def research(db, tmp_path, monkeypatch):
    from tools.migrations import migra_trade_idea
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    migra_trade_idea.migra(db.db_path, apply=True)
    current = store(db.db_path)
    asked = request("SYNTH-EXT")
    asked.update(currency="EUR")
    run = current.create_run(asked, idempotency_key=str(uuid4()))["run"]
    token = current.claim_run(run["id"])
    from test_trade_idea_economic import qualified, _operating_plan
    from bellomberg.valuation.trade_idea_model import prepare
    model = prepare(qualified(tmp_path), lambda *_: deepcopy(_operating_plan()), tmp_path / "models")
    assert model["valuation_usability"]["usable"]
    thesis_id = db.save_valuation_thesis("SYNTH-EXT", valuation_payload=model, reuse_generation=True)
    assert thesis_id
    model["_thesis_saved"] = {"thesis_id":thesis_id}
    report = result("SYNTH-EXT", proposal=False)
    report["summary"] = "La crescita dipende dal rinnovo dei contratti [src: e1]"
    report["evidence"] = [{"id":"e1","source":"https://example.org/public/filing","as_of":"2026-09-10",
                            "summary":"Il rinnovo dei contratti condiziona i ricavi."}]
    report["valuation_refs"] = [{"generation_id":model["generation_id"],
        "snapshot_id":model["snapshot_id"],"valuation_date":model["valuation_date"],
        "interpretation":"Caso sintetico dichiarato"}]
    current.update_progress(run["id"],token,"done",{"valuation_results":{"SYNTH-EXT":model}})
    current.finish_run(run["id"],token,report,"incomplete",reason="Synthetic offline workspace fixture")
    assert importlib.util.find_spec("bellomberg.valuation.trade_idea_workspace"), "research workspace absent"
    from bellomberg.valuation.trade_idea_workspace import ResearchWorkspace
    return ResearchWorkspace(current, artifact_root=tmp_path), current, run["id"], model


def action(workspace, run_id, model, kind, data, key=None):
    return workspace.act(run_id, kind, data, request_id=key or str(uuid4()),
                         generation_id=model["generation_id"])


def acquire_observations(workspace,run,model,observations):
    """Fake only source transport: typed, dated, declared fictional observations."""
    from hashlib import sha256
    from bellomberg.valuation.preparation_record_evidence import FIELDS
    documents=[]
    for index,row in enumerate(observations):
        text=json.dumps({"synthetic":True,"observations":[{k:row[k] for k in FIELDS}]},sort_keys=True)
        documents.append({"id":"synthetic-observation-"+str(index),"url":row["source"],
            "published_at":row["published_at"],"text":text,"sha256":sha256(text.encode()).hexdigest()})
    workspace.source_fetcher=lambda *_:{"documents":deepcopy(documents)}
    return action(workspace,run,model,"acquire_sources",{})


def test_chat_cites_exact_model_or_declares_missing_and_is_persisted(research):
    workspace,current,run,model = research
    answer = action(workspace,run,model,"question",{"question":"Quale valore base?"})
    assert answer["data"]["status"] == "answered"
    assert any(c.get("cell") and c["generation_id"]==model["generation_id"] for c in answer["data"]["citations"])
    missing = action(workspace,run,model,"question",{"question":"Zebre interstellari consensus ufficiale?"})
    assert missing["data"]["status"] == "insufficient_evidence"
    assert len(workspace.view(run)["history"]) == 2
    assert current.get_run(run)["cost"]["requests"] == 0


def test_simulation_recalculates_common_model_without_replacing_committee(research):
    workspace,current,run,model = research
    before = Path(model["path"]).read_bytes()
    committee_result = deepcopy(current.get_run(run)["result"])
    changed = action(workspace,run,model,"simulate",{"label":"Discount sensitivity", "rationale":"Personal higher discount rate",
        "changes":[{"scenario":"base","driver":"wacc","value":.12}]})
    assert changed["data"]["status"] == "ready", changed["data"]["issues"]
    variant = changed["data"]["model"]
    assert variant["fair_value_base"] < model["fair_value_base"]
    assert variant["generation_id"] != model["generation_id"]
    assert Path(variant["path"]).exists()
    assert Path(model["path"]).read_bytes() == before
    assert current.get_run(run)["progress"]["valuation_results"]["SYNTH-EXT"]["generation_id"] == model["generation_id"]
    assert current.get_run(run)["run"]["phase"] == "review_required"
    assert current.get_run(run)["result"] == committee_result
    assert changed["data"]["invalidates_conclusion"] is True


def test_simulation_rejects_historical_fact_and_duplicate_request_is_exact(research):
    workspace,current,run,model = research
    with pytest.raises(ValueError, match="editable|historical|consentiti"):
        action(workspace,run,model,"simulate",{"label":"bad","rationale":"must not alter fact",
            "changes":[{"scenario":"model","driver":"historical_revenue","value":1.}]})
    key = str(uuid4())
    first = action(workspace,run,model,"question",{"question":"valore base"},key)
    assert action(workspace,run,model,"question",{"question":"valore base"},key)==first
    with pytest.raises(ValueError, match="identity|identit|Idempot"):
        action(workspace,run,model,"question",{"question":"different"},key)


def test_objection_reply_and_future_run_history_are_append_only(research):
    workspace,current,run,model = research
    row = action(workspace,run,model,"objection",{"text":"Il margine e troppo ottimista", "driver":"gross_margin"})
    action(workspace,run,model,"objection_reply",{"objection_id":row["id"],"text":"Rivedere dopo bilancio", "status":"open"})
    history = workspace.events.pm_history("SYNTH-EXT")
    assert history[0]["objection"] == "Il margine e troppo ottimista"
    assert history[0]["responses"][0]["text"] == "Rivedere dopo bilancio"
    with sqlite3.connect(current.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM trade_idea_events WHERE id=?",(row["id"],))


def test_monitor_records_only_observed_condition_and_requires_activation(research):
    workspace,current,run,model = research
    with pytest.raises(ValueError, match="activ|attiv"):
        action(workspace,run,model,"monitor",{"metric":"price","operator":"lt","threshold":11.,"active":False})
    monitor = action(workspace,run,model,"monitor",{"metric":"price","operator":"lt","threshold":11.,"active":True})
    checked = action(workspace,run,model,"monitor_check",{"monitor_id":monitor["id"]})
    assert checked["data"]["status"] in ("stale","insufficient_evidence")
    assert checked["data"]["observed_at"] == model["market_quote"]["observed_local_date"]
    assert checked["data"]["observed_at"] != model["valuation_date"]
    assert "alert" not in checked["data"] or checked["data"]["alert"] is None


def test_earnings_freezes_expectations_and_rejects_lookahead_or_wrong_units(research):
    workspace,current,run,model = research
    prepared = action(workspace,run,model,"earnings_prepare",{"event_date":"2027-02-01"})
    assert prepared["data"]["expectations"]
    expectation = prepared["data"]["expectations"][0]
    wrong = dict(expectation,value=120.,unit="unsupported unit",published_at="2027-02-01",source="https://example.org/results")
    with pytest.raises(ValueError,match="unit|perimeter|perimetro"):
        action(workspace,run,model,"earnings_review",{"preparation_id":prepared["id"],"observations":[wrong]})
    with pytest.raises(ValueError,match="date|data|future|futur"):
        action(workspace,run,model,"earnings_review",{"preparation_id":prepared["id"],
            "observations":[dict(wrong,unit=expectation["unit"],published_at="2099-01-01")]})
    assert workspace.events.get(run,prepared["id"])["data"] == prepared["data"]


def test_error_review_without_later_history_is_explicitly_insufficient(research):
    workspace,current,run,model = research
    reviewed = action(workspace,run,model,"error_review",{})
    assert reviewed["data"]["status"] == "insufficient_history"
    assert not reviewed["data"].get("performance")


def test_compare_personal_generation_and_exact_currency_basis(research):
    workspace,current,run,model = research
    changed = action(workspace,run,model,"simulate",{"label":"Higher discount", "rationale":"Explicit scenario",
        "changes":[{"scenario":"base","driver":"wacc","value":.12}]})
    variant = changed["data"]["model"]
    compare = action(workspace,run,model,"compare_runs",{"other_run_id":run,"other_generation_id":variant["generation_id"]})
    assert compare["data"]["values"][1]["delta"] < 0
    driver = next(r for r in compare["data"]["drivers"] if r["driver"]=="wacc" and r["scenario"]=="base")
    assert driver["unit"]=="%" and driver["delta"]==pytest.approx(2.)
    assert len(workspace.view(run)["versions"])==2
    with pytest.raises(ValueError,match="generation"):
        action(workspace,run,model,"compare_runs",{"other_run_id":run,"other_generation_id":"absent"})


def test_price_refresh_new_generation_preserves_economics_and_invalidates_old_conclusion(research):
    from datetime import datetime,timezone
    workspace,current,run,model = research
    workspace.clock = lambda:datetime(2026,9,10,12,tzinfo=timezone.utc)
    workspace.price_fetcher = lambda ticker:{"status":"ok","source_id":"https://example.org/quote","as_of":"2026-09-10",
        "retrieved_at":"2026-09-10T12:00:00+00:00",
        "data":{"info":{"symbol":ticker,"regularMarketPrice":12.,"regularMarketTime":datetime(2026,9,10,10,tzinfo=timezone.utc).timestamp(),
            "currency":"EUR","fullExchangeName":"TEST","exchangeTimezoneName":"Europe/Rome"}}}
    before = current.get_run(run)
    refreshed = action(workspace,run,model,"refresh",{"kind":"price"})["data"]
    assert refreshed["status"]=="ready", refreshed["issues"]
    assert refreshed["model"]["generation_id"]!=model["generation_id"]
    assert refreshed["model"]["fair_value_base"]==model["fair_value_base"]
    assert refreshed["model"]["valuation_date"]==model["valuation_date"]
    assert refreshed["quote"]["price"]==12.
    assert refreshed["invalidates_conclusion"] and not refreshed["economic_rollforward"]
    after = current.get_run(run)
    assert after["run"]["phase"]=="review_required"
    assert after["result"]==before["result"]
    monitor = action(workspace,run,refreshed["model"],"monitor",{"metric":"price","operator":"gt","threshold":11.,"active":True})
    checked = action(workspace,run,refreshed["model"],"monitor_check",{"monitor_id":monitor["id"]})["data"]
    assert checked["status"]=="triggered" and checked["alert"] is True
    assert checked["value"]==12. and checked["observed_at"]=="2026-09-10"
    assert workspace.costs(run)["requests"]==0
    # A current price cannot requalify an expired underlying economic profile.
    workspace.clock = lambda:datetime(2026,9,28,12,tzinfo=timezone.utc)
    workspace.price_fetcher = lambda ticker:{"status":"ok","source_id":"https://example.org/quote","as_of":"2026-09-28",
        "retrieved_at":"2026-09-28T12:00:00+00:00",
        "data":{"info":{"symbol":ticker,"regularMarketPrice":12.,"regularMarketTime":datetime(2026,9,28,10,tzinfo=timezone.utc).timestamp(),
            "currency":"EUR","fullExchangeName":"TEST","exchangeTimezoneName":"Europe/Rome"}}}
    expired=action(workspace,run,model,"refresh",{"kind":"price"})["data"]
    assert expired["status"]=="blocked" and not expired["issues"]["usable"]
    workspace.price_fetcher = lambda ticker:{"status":"unavailable"}
    with pytest.raises(ValueError,match="unavailable"):
        action(workspace,run,model,"refresh",{"kind":"price"})


def test_ideas_comparison_exposes_evidence_and_no_mixed_numeric_ranking(research):
    workspace,current,run,model = research
    compared = action(workspace,run,model,"compare_ideas",{"run_ids":[run]})["data"]
    assert compared["ideas"][0]["evidence"]
    assert compared["ideas"][0]["valuation"]["method"]=="operating_fcff"
    assert compared["numeric_ranking"] is None
    with pytest.raises(ValueError,match="distinct"):
        action(workspace,run,model,"compare_ideas",{"run_ids":[run,run]})


def test_costs_are_real_phase_ledger_and_unknown_remaining_is_missing(research):
    workspace,current,run,model = research
    asked = request("COST-SYNTH")
    created = current.create_run(asked,idempotency_key=str(uuid4()))["run"]["id"]
    current.claim_run(created)
    current.reserve_cost(created,"synthetic-unknown","aux:preparation",asked["models"]["aux"]["model"],"0.1")
    current.mark_cost_unknown(created,"synthetic-unknown",reason="No observed provider receipt")
    cost = workspace.costs(created)
    assert cost["remaining_known_usd"] is None
    assert cost["unknown_requests"]==1
    assert cost["phases"][0]["phase"]=="aux:preparation"
    assert cost["phases"][0]["cache_read_tokens"] is None
    assert cost["workspace_paid_requests"]==0


def test_earnings_observations_preserve_frozen_period_and_recalculate_explicit_forward_revision(research):
    from datetime import datetime,timezone
    workspace,current,run,model = research
    workspace.clock = lambda:datetime(2026,12,1,12,tzinfo=timezone.utc)
    prepared = action(workspace,run,model,"earnings_prepare",{"event_date":"2027-02-01"})
    expected = next(r for r in prepared["data"]["expectations"] if r["driver"]=="revenue")
    assert expected["period"]=="2026-01-01/2026-12-31"
    assert expected["kind"]=="engine_calculated_forecast" and expected["cell"]
    assert prepared["data"]["metrics_contract"]=="common_earnings_facts/1"
    workspace.clock = lambda:datetime(2027,2,2,12,tzinfo=timezone.utc)
    observed=dict(expected,value=expected['value']-5.,published_at="2027-02-01",source="https://example.org/results")
    acquired=acquire_observations(workspace,run,model,[observed])
    reviewed = action(workspace,run,model,"earnings_review",{"preparation_id":prepared["id"],
        "observations":[observed],"source_event_id":acquired["id"],
        "changes":[{"scenario":"base","driver":"wacc","value":.12}],
        "rationale":"Personal increase in the discount rate after reported uncertainty"})
    assert reviewed["data"]["differences"][0]["delta"]==pytest.approx(-5.)
    assert reviewed["data"]["model_impact"]["values"][1]["delta"]<0
    assert current.get_run(run)["run"]["phase"]=="review_required"
    assert workspace.events.get(run,prepared["id"])["data"]==prepared["data"]
    attribution = action(workspace,run,model,"error_attribution",{"category":"judgment",
        "evidence_event_id":reviewed["id"],"rationale":"PM review: underestimated execution sensitivity"})
    review = action(workspace,run,model,"error_review",{})["data"]
    assert review["categories"]["judgment"]["status"]=="documented"
    assert review["categories"]["data"]["status"]=="insufficient_evidence"
    assert not review["causal_improvement_claimed"]
    with pytest.raises(ValueError,match="verified primary"):
        action(workspace,run,model,"earnings_review",{"preparation_id":prepared["id"],"source_event_id":acquired["id"],
            "observations":[dict(observed,value=.99)]})
    with pytest.raises(ValueError,match="later"):
        action(workspace,run,model,"error_attribution",{"category":"data","evidence_event_id":prepared["id"],"rationale":"unproven"})


@pytest.mark.parametrize('deviation', [0., -5.])
def test_error_review_distinguishes_observation_from_causal_attribution(research, deviation):
    from datetime import datetime, timezone
    workspace, current, run, model = research
    workspace.clock = lambda: datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
    prepared = action(workspace, run, model, 'earnings_prepare', {'event_date': '2027-02-01'})
    expected = next(row for row in prepared['data']['expectations'] if row['driver'] == 'revenue')
    workspace.clock = lambda: datetime(2027, 2, 2, 12, tzinfo=timezone.utc)
    observed = dict(expected, value=expected['value']+deviation,
                    published_at='2027-02-01', source='https://example.org/results')
    acquired = acquire_observations(workspace, run, model, [observed])
    reviewed = action(workspace, run, model, 'earnings_review', {'preparation_id': prepared['id'],
        'observations': [observed], 'source_event_id': acquired['id']})
    view = action(workspace, run, model, 'error_review', {})['data']
    assert all(row['status'] == 'insufficient_evidence' for row in view['categories'].values())
    assert view['matched_observations'] == (1 if deviation == 0 else 0)
    assert len(view['findings']) == (0 if deviation == 0 else 1)
    if deviation:
        finding = view['findings'][0]
        assert finding['category'] == 'unattributed_deviation'
        assert finding['generation_id'] == model['generation_id']
        assert finding['event_id'] == reviewed['id']
        attribution = action(workspace, run, model, 'error_attribution', {
            'category': 'timing', 'evidence_event_id': reviewed['id'],
            'rationale': 'PM judgment: this shortfall reflects delayed recognition, subject to subsequent verification'})
        after = action(workspace, run, model, 'error_review', {})['data']
        assert after['findings'][0]['category'] == 'timing'
        assert after['findings'][0]['attribution_author'] == 'PM'
        assert after['categories']['assumption']['status'] == 'insufficient_evidence'
        assert attribution['data']['evidence_generation_id'] == model['generation_id']


def test_exposure_workspace_reads_observed_holdings_without_fair_value_or_editable_facts(research,tmp_path):
    from test_trade_idea_exposure import sourced_exposure
    from bellomberg.valuation.trade_idea_model import prepare
    from bellomberg.storage.memory_db import MemoryDB
    workspace,current,_,_ = research
    q,plan = sourced_exposure(tmp_path/"exposure-source")
    model = prepare(q,lambda *_:deepcopy(plan),tmp_path/"exposure-model")
    db = MemoryDB.__new__(MemoryDB); db.db_path=current.db_path
    thesis = db.save_valuation_thesis(model["ticker"],valuation_payload=model,reuse_generation=True)
    assert thesis
    model["_thesis_saved"]={"thesis_id":thesis}
    asked=request(model["ticker"]); asked.update(currency="EUR")
    run=current.create_run(asked,idempotency_key=str(uuid4()))["run"]["id"]
    worker=current.claim_run(run)
    report=result(model["ticker"],proposal=False)
    report["valuation_refs"]=[{"generation_id":model["generation_id"],"snapshot_id":model["snapshot_id"],
        "valuation_date":model["valuation_date"],"interpretation":"Observed exposure, no corporate FV"}]
    current.update_progress(run,worker,"done",{"valuation_results":{model["ticker"]:model}})
    current.finish_run(run,worker,report,"incomplete",reason="Declared synthetic observed exposure")
    view=workspace.view(run)
    assert view["model"]["status"]=="ready",view["model"]
    assert view["model"]["kind"]=="exposure" and not view["model"]["intrinsic_value_applicable"]
    assert view["model"]["editable"]==[] and view["versions"][0]["usable"]
    assert all(value is None for value in view["model"]["values"].values())
    selected=workspace._model(current.get_run(run),model["generation_id"])
    assert selected.get("method")=="exposure_analysis"
    assert view["model"]["exhibits"]["status"]=="complete", view["model"]["exhibits"]["reasons"]
    answer=action(workspace,run,model,"question",{"question":"Quali esposizioni e leva?"})["data"]
    assert answer["status"]=="answered" and any(c.get("cell") for c in answer["citations"]), (model.get("method"),view["model"]["exhibits"],answer)
    assert "Synthetic A" in answer["answer"]
    with pytest.raises(ValueError,match="editable|historical"):
        action(workspace,run,model,"simulate",{"label":"Alter fact","rationale":"Unsupported change",
            "changes":[{"scenario":"model","driver":"holdings","value":.5}]})
