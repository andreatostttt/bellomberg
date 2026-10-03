"""Offline positive Trade Idea replay through the real committee and operational gates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.llm_client import Usage
from test_documented_dcf_contract import contract_tools, _call
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_store import all_checks, db_path, migrated, store
from test_valuation_snapshot_persistence import db
from trade_idea_fixtures import bind_workbook, research_result
from trade_idea_evolution_fixtures import (DESKS, MODEL_META_TOOLS, committee_review,
    qualified_source, review_tool_block, historical_fx_engines, model_build_tool_blocks)


@pytest.fixture(autouse=True)
def frozen_usage_fx(monkeypatch):
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "_fx_usd_to_eur", lambda: (0.9, "offline_frozen_fx"))


def test_remaining_work_uses_exact_private_r2_caps_and_rejects_other_generation(
        migrated, contract_tools, tmp_path):
    from copy import deepcopy
    from bellomberg.agents.specialists.base import MAX_TOKENS_SPECIALIST
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    current, accepted = store(migrated), _priced_request()
    accepted["ticker"] = "SYNTH-EXT"
    run_id = current.create_run(accepted, idempotency_key="native-r2-floor")["run"]["id"]
    token = current.claim_run(run_id)
    gate = trade_idea.TradeIdeaBudgetGate(current, run_id, token, accepted["catalog_snapshot"],
        catalog_fetcher=lambda: accepted["catalog_snapshot"])
    limits = {"scope": "trade_idea_r2_final_completion_v1", "round": 2,
        "model_ref": {key: workbook[key] for key in ("snapshot_id", "generation_id", "workbook_sha256")},
        "max_tokens_by_desk": {"macro": 65536, "crypto": 65536}}
    board = SimpleNamespace(data={}, budget_gate=gate, run_scope="trade_idea", model_phase="review",
        target_ticker="SYNTH-EXT", valuation_results={"SYNTH-EXT": workbook},
        valuation_generations=[workbook], model_roots=[tmp_path],
        _trade_idea_r2_completion_limits=deepcopy(limits))
    remaining = trade_idea._remaining_work(board)
    floors = {row["stage"]: row for row in remaining["reservation_floors"]}
    for desk in ("macro", "crypto"):
        assert floors[desk + ":R2"]["max_output_tokens"] == 65536
        assert floors[desk + ":R2"]["output_reservation_floor_usd"] == "0.131072"
        assert floors[desk + ":R1"]["max_output_tokens"] == MAX_TOKENS_SPECIALIST
    assert floors["quant:R2"]["max_output_tokens"] == MAX_TOKENS_SPECIALIST
    assert remaining["estimated_remaining_usd"] is None and not remaining["input_cost_included"]
    assert board.model_phase == "review" and board._trade_idea_r2_completion_limits == limits
    board._trade_idea_r2_completion_limits["model_ref"]["generation_id"] = "different-generation"
    with pytest.raises(ValueError, match="current exact verified workbook"):
        trade_idea._remaining_work(board)
    assert current.get_run(run_id)["cost"]["requests"] == 0


@pytest.mark.parametrize("foreign_book,missing_fx,revise_model,crash_at", [
    (False, False, False, None), (True, False, False, None), (True, True, False, None), (False, False, True, None),
    (False, False, False, "first_tool"), (False, False, False, "macro_r2"), (False, False, False, "capo"),
    (False, False, False, "first_tool_prices")],
    ids=["eur_book", "qualified_foreign_book", "missing_foreign_fx", "changed_model_reviewed_again",
         "crash_after_tool", "crash_after_macro_r2", "crash_after_capo", "updated_prices_reverified"])
def test_favorable_full_committee_reaches_dcn_with_real_operational_checks(
        migrated, tmp_path, monkeypatch, foreign_book, missing_fx, revise_model, crash_at):
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists import base as specialist_base
    from bellomberg.core import current_facts, llm_client, mandato_pm, paths
    from bellomberg.storage import classificazione as cl

    # The common engine produces a real workbook and sidecar from fictional,
    # documented records. Only provider responses, market observations and the
    # isolated portfolio/risk snapshots are synthetic.
    from copy import deepcopy
    from test_trade_idea_economic import qualified, _operating_plan
    from bellomberg.valuation import trade_idea_model
    from bellomberg.valuation.trade_idea_model import revise
    qualification = qualified(tmp_path)
    assert qualification["status"] == "qualified", qualification["reasons"]
    basis_plan = deepcopy(_operating_plan())
    actual_models = []
    build_events = []
    real_build = trade_idea_model.build_from_plan
    def build_common(*args, **kwargs):
        assert kwargs["author_context"]["actor"] == "fundamentals"
        assert kwargs["author_context"]["human_approved"] is False
        assert sum("Round 0." in str(call.get("messages")) for call in provider_calls) >= len(DESKS)
        assert {desk for desk in DESKS if desk != "fundamentals"} <= {
            desk for desk in DESKS for call in provider_calls
            if "You are the " + desk + " desk" in str(call.get("system"))
            and "Round 1." in str(call.get("messages"))}
        built = real_build(*args, **kwargs)
        actual_models.append(built)
        build_events.append({"provider_calls": len(provider_calls),
                             "consultations": deepcopy(kwargs["author_context"]["consultations"])})
        return built
    monkeypatch.setattr(trade_idea_model, "build_from_plan", build_common)
    def revise_common(*args, **kwargs):
        revised = revise(*args, **kwargs)
        actual_models.append(revised)
        return revised
    monkeypatch.setattr(paths, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(paths, "REPORT_DIR", tmp_path)

    registry_path = tmp_path / "veicoli.json"
    registry_path.write_text(json.dumps({
        "SYNTH-EXT": {"tipo": "operating", "provenienza": "dichiarato",
                      "classe_size": "single", "settore_policy": "synthetic"},
        "SYNTH-PEER": {"tipo": "etf", "provenienza": "dichiarato",
                       "classe_size": "veicolo"}}), encoding="utf-8")
    registry = cl.carica_veicoli(str(registry_path))
    assert registry["origine"] not in ("assente", "illeggibile")
    monkeypatch.setattr(cl, "carica_veicoli", lambda *a, **k: registry)

    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('SYNTH-EXT',1,100,'EUR',1)")
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('SYNTH-PEER',99,100,?,1)", ("USD" if foreign_book else "EUR",))
    s = store(migrated)
    accepted = _priced_request(budget="30")
    accepted.update(ticker="SYNTH-EXT", currency="EUR", language="en",
        view_text="Switching costs make this business immune to a downturn.")
    accepted["source_qualification"] = qualification
    accepted["authorization"]["source_fingerprint"] = qualification["fingerprint"]
    run_id = s.create_run(accepted, idempotency_key="full-positive-committee")["run"]["id"]
    result = research_result("favorable", run_id=run_id)
    for field in ("run_id", "run_type", "pm_view", "destination"):
        result.pop(field, None)
    result["proposal"].update(action="ADD", eur_amount=100.0,
                              sizing_source="Measured common sizing engine")

    today = datetime.now(timezone.utc).date()
    daily_close = today
    while daily_close.weekday() >= 5:
        daily_close -= timedelta(days=1)
    observed = daily_close.isoformat()
    model_day = qualification["as_of"]
    result["evidence"] = [
        {"id": "archive", "source": "[src: get_filing_changes] fictional filing",
         "as_of": observed, "summary": "Revenue 999 EUR million; scenario indices 80, 100, 125",
         "url": None},
        {"id": "price", "source": "[src: get_price_live] fictional daily close",
         "as_of": observed, "summary": "Price 100 EUR", "url": None},
        {"id": "model", "source": "[src: get_valuation] common engine workbook",
         "as_of": model_day,
         "summary": "Pending Fundamentals construction after independent research", "url": None},
    ]
    for section in result["dossier"]:
        section["evidence_ids"] = ["archive", "price", "model"]
        for table in section["tables"]:
            table["source"] = "[evidence: archive] fictional scenario values"
    for scenario in result["scenarios"]:
        scenario["evidence_ids"] = ["archive", "price", "model"]
    for objection in result["objections"]:
        objection["evidence_ids"] = ["archive", "price", "model"]

    provider_calls = []
    tool_calls = []

    def answer(model, blocks, reason):
        return SimpleNamespace(id="offline-provider-" + str(len(provider_calls)),
            model=model, stop_reason=reason, content=blocks,
            usage=Usage(input_tokens=120, output_tokens=180,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0,
                        cost_usd=0.001))

    class Stream:
        def __init__(self, kwargs):
            self.kwargs = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def get_final_message(self):
            final = bind_workbook(deepcopy(result), actual_models[-1])
            final["evidence"][2]["summary"] = f"Fair value {actual_models[-1]['fair_value_base']} EUR"
            final["evidence"][2]["as_of"] = str(actual_models[-1]["valuation_date"])[:10]
            return answer(self.kwargs["model"],
                          [SimpleNamespace(type="text", text=json.dumps(final))], "end_turn")

    class Messages:
        def create(self, **kwargs):
            provider_calls.append(kwargs)
            tool_done = any(isinstance(row.get("content"), list)
                and any(isinstance(block, dict) and block.get("type") == "tool_result"
                        for block in row["content"])
                for row in kwargs["messages"])
            tools = [row["name"] for row in kwargs.get("tools") or []]
            build = model_build_tool_blocks(kwargs, basis_plan,
                [row["id"] for row in qualification["source_report"]["documents"]])
            if build:
                return answer(kwargs["model"], [SimpleNamespace(type="tool_use",
                    id="build-" + str(len(provider_calls)) + "-" + str(index), **item)
                    for index, item in enumerate(build)], "tool_use")
            meta = review_tool_block(kwargs, actual_models[-1] if actual_models else None)
            if (revise_model and meta and meta["name"] == "review_candidate_model"
                    and "Round 2." in str(kwargs["messages"][0].get("content")) and len(actual_models) == 1):
                revision_records = deepcopy(actual_models[-1]["acquisition_snapshot"]["case"]["records"])
                for record in revision_records:
                    if record["driver"] == "wacc":
                        record["value"] = .12
                meta["input"].update(action="revise",
                    rationale="Explicit sourced revision of the discount rate before final review",
                    evidence_refs=[qualification["source_report"]["documents"][0]["id"]],
                    changes={"method_records": revision_records})
            if meta:
                return answer(kwargs["model"], [SimpleNamespace(type="tool_use",
                    id="review-" + str(len(provider_calls)), **meta)], "tool_use")
            available = [name for name in tools if name not in MODEL_META_TOOLS]
            if available and not tool_done and kwargs.get("tool_choice") != {"type": "none"}:
                return answer(kwargs["model"],
                    [SimpleNamespace(type="tool_use", id="tool-" + str(len(provider_calls)),
                        name=available[0], input={"ticker": "SYNTH-EXT"})], "tool_use")
            text = ("The synthetic filing and common model are checked against the PM thesis. "
                    "Renewal economics, cash conversion and downside conditions remain explicit. " * 8)
            if (kwargs.get("response_format") or {}).get("json_schema", {}).get("name") == "trade_idea_committee_review":
                text = json.dumps(committee_review())
            return answer(kwargs["model"], [SimpleNamespace(type="text", text=text)], "end_turn")
        def stream(self, **kwargs):
            provider_calls.append(kwargs)
            return Stream(kwargs)

    class Client:
        def __init__(self, **_):
            import httpx
            self._http = SimpleNamespace(timeout=httpx.Timeout(450))
            self.messages = Messages()

    def dispatch(name, input_, **kwargs):
        tool_calls.append(name)
        if name == "get_valuation":
            return {"ok": True, "data": actual_models[-1], "_source": "fictional common engine"}
        return {"ok": True, "data": {
            "ticker": "SYNTH-EXT", "as_of": observed, "price": 100,
            "px": 100, "price_asof": observed, "currency": "EUR", "status": "ready",
            "revenue": 999, "scenario_indices": [80, 100, 125],
            "source": "yfinance daily Close (not an intraday quote)"},
            "_source": "fictional provider receipt",
            "_timestamp": datetime.now(timezone.utc).isoformat()}

    for owner in (specialist_base, llm_client, trade_idea):
        monkeypatch.setattr(owner, "OpenRouterClient", Client)
    monkeypatch.setattr(chat_tools, "dispatch", dispatch)
    monkeypatch.setattr(chat_tools, "_compatta_portfolio_live", lambda p: {
        "positions": p["positions"], "cash_disponibile_eur": p["cash_disponibile_eur"]})
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "Offline facts only")
    mandate = mandato_pm.profilo_esempio()
    monkeypatch.setattr(mandato_pm, "carica", lambda: mandate)
    book = {"positions": [
        {"ticker": "SYNTH-EXT", "quantita": 1, "valuta": "EUR",
         "valore_mercato_eur": 100, "valore_mercato": 100},
        {"ticker": "SYNTH-PEER", "quantita": 99, "valuta": "EUR",
         "valore_mercato_eur": 9900, "valore_mercato": 9900}],
        "cash_source": "sqlite:cash_state", "cash_disponibile_eur": 1000,
        "stale_positions": [], "fx_incomplete": []}
    risk = {"portfolio": {"var_99_1d_pct": -1.0},
        "per_asset": {name: {"vol_annual_pct": 20.0}
                      for name in ("SYNTH-EXT", "SYNTH-PEER")},
        "correlation": {"tickers": ["SYNTH-EXT", "SYNTH-PEER"],
                        "matrix": [[1.0, 0.2], [0.2, 1.0]]},
        "fx_conversion": {"local_declared": []}, "skipped_tickers": []}
    stress = {"stress_scenario": "gfc_2008", "stress_fallback": False,
        "returns_basis": "EUR", "stress_meta": {"window_loss_pct": -5.0,
            "real_history": ["SYNTH-EXT", "SYNTH-PEER"],
            "proxied": {}, "zero_filled_days": {}}}
    risk_loader, stress_loader = lambda: risk, lambda: stress
    if foreign_book:
        book["positions"][1].update(valuta="USD", fx_to_eur=1/1.1, fx_source="live", peso_pct=99)
        book["positions"][0]["peso_pct"] = 1
        risk_loader, stress_loader, historical_calls, engine_outputs = historical_fx_engines(
            monkeypatch, tmp_path, book, observed, missing_fx=missing_fx)
    def execute(selected_id, directory):
        return trade_idea.execute_trade_idea(selected_id, store=s,
        lock_path=tmp_path / "paid.lock", output_dir=directory,
        portfolio_loader=lambda: book, mandate_loader=lambda: mandate,
        preparer_binder=lambda *_: (None, {"status": "disabled", "reason": "fictional records ready"}),
        source_qualifier=lambda *_a, **_k: qualification,
        model_reviser=revise_common,
        risk_loader=risk_loader, stress_loader=stress_loader,
        candidate_metrics_loader=lambda *_: {"status": "not_applicable"},
        delivery_sender=lambda manifest, language: {"status": "accepted",
            "email_status": "accepted", "message_id": "offline-positive"},
        catalog_fetcher=lambda: accepted["catalog_snapshot"],
        isolated_tool_dispatcher=dispatch,
        isolated_facts_loader=lambda: "Offline facts only")

    if crash_at is None:
        detail = execute(run_id, tmp_path / "delivery")
    else:
        class SimulatedProcessCrash(BaseException):
            pass
        update_progress = s.update_progress
        crashes = []
        def crash_after_durable_checkpoint(selected_id, token, phase, progress):
            update_progress(selected_id, token, phase, progress)
            checkpoint = progress.get("checkpoint") or {}
            data = checkpoint.get("data") or {}
            trigger = (bool(checkpoint.get("tool_receipts")) if crash_at in ("first_tool", "first_tool_prices") else
                "macro:2" in data.get("_completed_stages", {}) if crash_at == "macro_r2" else
                bool(data.get("_capo_completed")))
            if trigger and not crashes:
                crashes.append(len(provider_calls))
                raise SimulatedProcessCrash("Offline process loss after committed checkpoint")
        monkeypatch.setattr(s, "update_progress", crash_after_durable_checkpoint)
        with pytest.raises(SimulatedProcessCrash):
            execute(run_id, tmp_path / "delivery")
        assert len(crashes) == 1
        s.interrupt_run(run_id, reason="Offline owner process confirmed dead")
        parent_detail = s.get_run(run_id)
        parent_id = run_id
        refresh_options = {}
        if crash_at == "first_tool_prices":
            with sqlite3.connect(migrated) as conn:
                conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) VALUES(?,?,?,?,?)",
                    ("SYNTH-EXT", 100, "EUR", "frozen current price", datetime.now(timezone.utc).isoformat()))
                conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) VALUES(?,?,?,?,?)",
                    ("SYNTH-PEER", 100, "EUR", "frozen current price", datetime.now(timezone.utc).isoformat()))
            refresh_options["authorize_price_refresh"] = True
        next_run = s.create_continuation(parent_id, idempotency_key="explicit-resume", authorize_new_requests=True,
                                        **refresh_options)
        run_id = next_run["run"]["id"]
        assert next_run["cost"]["requests"] == crashes[0]
        detail = execute(run_id, tmp_path / "delivery-resumed")
        repeated = s.create_continuation(parent_id, idempotency_key="second-resume", authorize_new_requests=True)
        assert repeated["created"] is False and repeated["run"]["id"] == run_id
        assert repeated["cost"]["requests"] == len(provider_calls)
        original = s.get_run(parent_id)
        for key in ("run", "progress", "result", "cost"):
            assert original[key] == parent_detail[key]
        if crash_at == "capo":
            assert len(provider_calls) == crashes[0], "Delivery recovery must not buy another Capo"
        if crash_at in ("first_tool", "first_tool_prices"):
            assert sum(row["tool"] == "get_cot_positioning" and row["specialist"] == "macro"
                and row["round"] == 0 for row in detail["progress"]["checkpoint"]["tool_log"]) == 1

    assert len(build_events) == 1, (detail["run"]["reason"], detail.get("progress"))
    workbook = actual_models[0]
    assert len(tool_calls) >= 16
    assert detail["cost"]["requests"] == len(provider_calls)
    assert detail["result"]["judgment"] == "favorable"
    assert detail["run"]["technical_status"] == "completed", (detail["run"]["reason"], detail["result"]["data_gaps"])
    assert detail["run"]["destination"]["kind"] == ("research" if missing_fx else "dcn"), detail["run"]["destination"]
    if missing_fx:
        assert detail["progress"]["routing_checks"]["sizing_valid"] is False
        assert "sizing_valid" in detail["run"]["destination"]["reason"]
    else:
        assert all(detail["progress"]["routing_checks"].values()), detail["progress"]["routing_checks"]
    assert detail["artifacts"]["pdf_quality"]["status"] == "ready"
    assert detail["artifacts"]["model_status"] == "ready"
    assert detail["artifacts"]["complete_package_status"] == "ready"
    assert detail["email"]["status"] == "accepted"
    if crash_at == "first_tool_prices":
        refreshed = detail["progress"]["routing_checks"]["price_refresh_verification"]
        assert all(value is True for value in refreshed["measurements"].values())
        assert refreshed["evidence"]["proposal"] == detail["result"]["proposal"]
        assert refreshed["before"]["current_context_sha256"] == refreshed["after"]["current_context_sha256"]
        assert refreshed["before"]["price_inputs_sha256"] != detail["run"]["continuation"]["price_refresh"]["accepted_price_inputs_sha256"]
    delivery_directory = tmp_path / ("delivery-resumed" if crash_at else "delivery")
    before_delivery_calls = len(provider_calls)
    extra_sends = []
    trade_idea.deliver_trade_idea(s, run_id, output_dir=delivery_directory,
        send_email=False, send=lambda *_a, **_k: extra_sends.append("unexpected"))
    from bellomberg.storage.trade_idea_store import RunConflict
    with trade_idea.exclusive_paid_run(delivery_directory / ".delivery.lock"):
        with pytest.raises(RunConflict, match="delivery recovery"):
            trade_idea.deliver_trade_idea(s, run_id, output_dir=delivery_directory, send_email=False)
    assert len(provider_calls) == before_delivery_calls and extra_sends == []
    assert s.get_run(run_id)["artifacts"] == detail["artifacts"]
    if crash_at == "capo":
        exact = {row["path"]: Path(row["path"]).read_bytes()
                 for row in detail["artifacts"]["exact_artifact_receipts"]}
        for path in exact:
            Path(path).unlink()
        assert s.get_run(run_id)["states"]["artifacts"] == "unavailable"
        for _ in range(2):
            trade_idea.deliver_trade_idea(s, run_id, output_dir=delivery_directory,
                send_email=False, send=lambda *_a, **_k: extra_sends.append("unexpected"))
        assert {path: Path(path).read_bytes() for path in exact} == exact
        assert s.get_run(run_id)["states"]["artifacts"] == "ready"
        assert len(provider_calls) == before_delivery_calls and extra_sends == []
    final_generation = actual_models[-1]["generation_id"]
    for desk in DESKS:
        assert any("You are the " + desk + " desk" in str(call.get("system"))
                   and "Round 2." in str(call["messages"][0].get("content"))
                   and final_generation in str(call["messages"][0].get("content"))
                   for call in provider_calls)
    consultations = build_events[0]["consultations"]
    assert {row["desk"] for row in consultations} == set(DESKS)-{"fundamentals"}
    assert len(consultations) == len(DESKS)-1
    assert all(row["status"] == "complete" and row.get("author_view_complete") is True
               and row.get("fundamentals_decision")
               and row["response"] and row["usage"] for row in consultations)
    if revise_model:
        assert len(actual_models) == 2 and actual_models[-1]["fair_value_base"] != workbook["fair_value_base"]
        final_generation = actual_models[-1]["generation_id"]
        audit = detail["result"]["model_review"]
        assert audit["initial_refs"][0]["generation_id"] == workbook["generation_id"]
        assert audit["final_refs"][0]["generation_id"] == final_generation
        assert len([row for row in audit["revision_log"] if row["status"] == "applied"]) == 1
        assert any(row["status"] == "retained" and row["before_generation_id"] == final_generation
                   for row in audit["revision_log"])
        assert len(detail["progress"]["review_history"]) == 1
        assert detail["cost"]["by_phase"]["model_revision"]["requests"] >= len(DESKS)
        for desk in DESKS:
            assert any("You are the " + desk + " desk" in str(call.get("system"))
                       and "Round 2." in str(call["messages"][0].get("content"))
                       and final_generation in str(call["messages"][0].get("content"))
                       for call in provider_calls)
        attached = [row for row in detail["artifacts"]["artifacts"] if row["kind"] == "xlsx"]
        assert len(attached) == 1 and attached[0]["generation_id"] == final_generation
    if foreign_book:
        assert engine_outputs["risk"]["fx_conversion"]["qualified"] is (not missing_fx)
        assert engine_outputs["stress"]["fx_conversion"]["qualified"] is (not missing_fx)
        assert any(symbols == ["EURUSD=X"] for symbols, _ in historical_calls)
        if missing_fx:
            assert engine_outputs["simulations"] == 0
        else:
            assert engine_outputs["stress"]["stress_meta"]["fx_conversion"]["qualified"] is True
            assert any(str(options.get("start", "")).startswith("2008") for _, options in historical_calls)
            assert engine_outputs["simulations"] == 1


@pytest.mark.parametrize("quote_drift", [False, True])
def test_crash_after_result_before_route_revalidates_without_paid_replay(
        migrated, contract_tools, tmp_path, monkeypatch, quote_drift):
    from bellomberg.api import trade_idea_routes as routes
    from bellomberg.core import paths

    chat_tools_module, _, _ = contract_tools
    _, workbook = _call(chat_tools_module)
    monkeypatch.setattr(paths, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(paths, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(routes, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(routes, "REPORT_DIR", tmp_path)
    s = store(migrated)
    accepted = _priced_request(budget="5")
    accepted.update(ticker="SYNTH-EXT", currency="EUR", language="en",
        view_text="Switching costs make this business immune to a downturn.")
    run_id = s.create_run(accepted, idempotency_key="crash-window-" + str(quote_drift))["run"]["id"]
    result = bind_workbook(research_result("favorable", run_id=run_id), workbook)
    result.pop("destination")
    result["proposal"].update(eur_amount=100.0, sizing_source="Prior measured sizing")
    token = s.claim_run(run_id)
    day = datetime.now(timezone.utc).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    quote = {"status": "ready", "ticker": "SYNTH-EXT", "price": "100",
        "price_asof": day.isoformat(),
        "source": "yfinance daily Close (not an intraday quote)",
        "currency": "EUR", "currency_basis": "accepted_identity"}
    fx = {"valid": True, "observations": [], "sampled_at": datetime.now(timezone.utc).isoformat()}
    checks = {**all_checks(), "candidate_price_revalidated": True,
              "fx_revalidated": True}
    s.update_progress(run_id, token, "result", {
        "routing_checks": checks,
        "report_quality": {"status": "ready", "analytical_pages": 11},
        "candidate_quote_receipts": {"initial": quote, "final": quote},
        "fx_receipts": {"initial": fx, "final": fx},
        "valuation_generations": [workbook], "valuation_attempts": []})
    s.finish_run(run_id, token, result, "completed")
    # No decision exists yet: this is the exact crash window after finish_run.
    assert s.get_run(run_id)["run"]["destination"]["kind"] == "none"
    monkeypatch.setattr(routes, "TradeIdeaStore", lambda *_: s)
    monkeypatch.setattr(routes, "_worker_state", lambda _: (None, False))
    sent = []
    def no_paid_delivery(store, received_id, **kwargs):
        sent.append(received_id)
    observed = {**quote, "price": "101"} if quote_drift else quote
    recovery = routes.recover_orphan_runs(migrated, delivery=no_paid_delivery,
        quote_sampler=lambda _: observed,
        portfolio_loader=lambda: {"positions": [], "fx_incomplete": [], "stale_positions": []})
    detail = s.get_run(run_id)
    assert recovery["errors"] == []
    assert sent == [run_id]
    assert detail["run"]["destination"]["kind"] == ("research" if quote_drift else "dcn")
    assert detail["run"]["technical_status"] == ("incomplete" if quote_drift else "completed")
    if quote_drift:
        assert "Quotazione candidato cambiata" in detail["run"]["reason"]
    # Startup replay never issues a provider reservation or LLM request.
    assert detail["cost"]["requests"] == 0
