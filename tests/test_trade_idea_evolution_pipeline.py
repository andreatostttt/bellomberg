"""Evolution gates use isolated SQLite and intercepted provider transports."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists.base import Blackboard, Specialist
from bellomberg.storage.trade_idea_store import BudgetBlocked
from test_trade_idea_pipeline import FakeMessages, _call_kwargs, _priced_request, _gate, frozen_usage_fx
from test_trade_idea_store import db_path, migrated, request, store
from trade_idea_evolution_fixtures import DESKS, MODEL_META_TOOLS, model_build_tool_blocks


def qualified_request():
    payload = _priced_request()
    payload["source_qualification"] = {
        "status": "qualified", "fingerprint": "b" * 64,
        "method_id": "operating_fcff", "reasons": [], "coverage": {},
    }
    payload["authorization"] = {
        "accepted": True, "source_fingerprint": "b" * 64,
        "activities": ["model_preparation", "committee", "model_revision"],
        "max_revision_rounds": 1,
    }
    return payload


def test_new_run_cannot_spend_without_explicit_run_authorization(migrated):
    s = store(migrated)
    payload = request()
    payload.pop("authorization")
    with pytest.raises(ValueError, match="authorization"):
        s.create_run(payload, idempotency_key="missing-run-grant")
    assert s.list_runs()["total"] == 0


@pytest.mark.parametrize("mutation", ["wrong_fingerprint", "blocked_sources", "not_accepted"])
def test_run_grant_binds_exact_qualified_sources(migrated, mutation):
    s = store(migrated)
    payload = qualified_request()
    if mutation == "wrong_fingerprint":
        payload["authorization"]["source_fingerprint"] = "c" * 64
    elif mutation == "blocked_sources":
        payload["source_qualification"]["status"] = "blocked"
    else:
        payload["authorization"]["accepted"] = False
    with pytest.raises(ValueError, match="authorization|sources"):
        s.create_run(payload, idempotency_key=mutation)
    assert s.list_runs()["total"] == 0


def test_preparation_requires_its_own_activity_before_provider_request(migrated):
    s = store(migrated)
    payload = qualified_request()
    payload["authorization"].update(activities=["committee"], max_revision_rounds=0)
    run_id = s.create_run(payload, idempotency_key="committee-only")["run"]["id"]
    s.claim_run(run_id)
    fake = FakeMessages("0.0001")
    kwargs = {**_call_kwargs(), "model": trade_idea.model_for_role("aux")}
    with pytest.raises(BudgetBlocked, match="activity|preparation"):
        _gate(s, run_id, payload).wrap_client(SimpleNamespace(messages=fake), role="aux")\
            .messages.create(**kwargs)
    assert fake.calls == 0
    assert s.get_run(run_id)["cost"]["requests"] == 0


def test_live_capability_downgrade_blocks_before_provider(migrated):
    s = store(migrated)
    payload = qualified_request()
    run_id = s.create_run(payload, idempotency_key="capability-drift")["run"]["id"]
    s.claim_run(run_id)
    live = deepcopy(payload["catalog_snapshot"])
    live["models"]["specialist"]["reasoning_effort"] = "high"
    fake = FakeMessages("0.0001")
    gate = trade_idea.TradeIdeaBudgetGate(s, run_id, "worker", payload["catalog_snapshot"],
                                       catalog_fetcher=lambda: live)
    with pytest.raises(ValueError, match="max|capability"):
        gate.wrap_client(SimpleNamespace(messages=fake), role="specialist:macro")\
            .messages.create(**_call_kwargs())
    assert fake.calls == 0
    assert s.get_run(run_id)["cost"]["requests"] == 0


@pytest.mark.parametrize("round_n,desk", [(0, desk) for desk in DESKS]
    + [(1, desk) for desk in DESKS if desk != "fundamentals"])
def test_independent_research_hides_every_other_desk(tmp_path, round_n, desk):
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
                       run_scope="trade_idea", run_id="isolated", target_ticker="TEST")
    board.independent_round = 1
    board.current_round = round_n
    board.model_phase = "research"
    board.write("macro", 0, "Macro raw facts")
    board.write("macro", 1, "Macro conclusion produced first")
    board.write("quant", 0, "Quant raw facts")
    assert board.summary_for_specialist(desk) == {}


@pytest.mark.parametrize("round_n,desk", [(0, desk) for desk in DESKS]
    + [(1, desk) for desk in DESKS if desk != "fundamentals"])
def test_independent_research_cannot_read_another_desk_through_tool(tmp_path, round_n, desk):
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
                       run_scope="trade_idea", run_id="isolated", target_ticker="TEST",
                       budget_gate=SimpleNamespace(wrap_client=lambda client, role: client))
    board.independent_round = 1
    board.current_round = round_n
    board.model_phase = "research"
    board.write("macro", 1, "Privileged conclusion")
    specialist = Specialist(board, client=SimpleNamespace(messages=SimpleNamespace()))
    specialist.name = desk
    answer = specialist._execute_meta_tool("ask_specialist", {
        "specialist": "quant" if desk == "macro" else "macro", "question": "view?"})
    assert answer["status"] == "blocked_independent_round"
    assert "report" not in answer


def test_fundamentals_builder_can_read_completed_independent_research(tmp_path):
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
                       run_scope="trade_idea", run_id="isolated", target_ticker="TEST")
    board.current_round, board.independent_round, board.model_phase = 1, 0, "building"
    board.write("macro", 1, "Independent macro conclusion")
    assert board.summary_for_specialist("fundamentals")["macro"]["report"] == "Independent macro conclusion"


def test_insufficient_sources_block_preflight_before_any_paid_phase(monkeypatch):
    fake = FakeMessages("0.0001")
    monkeypatch.setattr(trade_idea, "OpenRouterClient", lambda **_: SimpleNamespace(messages=fake))
    catalogue = _priced_request()["catalog_snapshot"]
    checked = trade_idea.preflight_trade_idea("TEST", "", "manual", "5",
        catalog_fetcher=lambda: catalogue,
        identity_resolver=lambda ticker: {"ticker": ticker, "name": "Synthetic",
            "exchange": "XNAS", "currency": "USD", "status": "confirmed", "reason": None},
        active_checker=lambda: False, mandate_loader=lambda: {}, key_checker=lambda: None,
        source_qualifier=lambda *args, **kwargs: {
            "status": "blocked", "reasons": ["opening working capital note absent"],
            "fingerprint": "c" * 64, "coverage": {}, "method_id": "operating_fcff"})
    assert checked["ok"] is False
    assert checked["source_qualification"]["status"] == "blocked"
    assert "opening working capital note absent" in ";".join(checked["reasons"])
    assert checked["preparation"]["status"] == "blocked"
    assert fake.calls == 0


def test_unusable_fundamentals_model_stops_after_research_before_red_team_and_capo(
        migrated, tmp_path, monkeypatch):
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists import base as specialist_base
    from bellomberg.core import llm_client, mandato_pm
    from bellomberg.core.llm_client import Usage
    from test_trade_idea_economic import qualified, _operating_plan
    qualification, basis_plan = qualified(tmp_path), _operating_plan()
    s = store(migrated)
    payload = qualified_request()
    payload.update(ticker="SYNTH-EXT", currency="EUR", language="en", source_qualification=qualification)
    payload["authorization"]["source_fingerprint"] = qualification["fingerprint"]
    run_id = s.create_run(payload, idempotency_key="model-after-independent-research")["run"]["id"]
    provider_calls, prepared, streams = [], [], []

    class Messages:
        def create(self, **kwargs):
            provider_calls.append(kwargs)
            blocks = model_build_tool_blocks(kwargs, basis_plan,
                [row["id"] for row in qualification["source_report"]["documents"]])
            tool_done = any(isinstance(row.get("content"), list)
                and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in row["content"])
                for row in kwargs["messages"])
            market = [row["name"] for row in kwargs.get("tools") or [] if row["name"] not in MODEL_META_TOOLS]
            if not blocks and market and not tool_done and kwargs.get("tool_choice") != {"type": "none"}:
                blocks = [{"name": market[0], "input": {"ticker": "SYNTH-EXT"}}]
            content = ([SimpleNamespace(type="tool_use", id=f"offline-{len(provider_calls)}-{i}", **block)
                        for i, block in enumerate(blocks)] if blocks else [SimpleNamespace(type="text",
                        text="Independent fictional analysis; capital bridge and downside remain unresolved. " * 20)])
            return SimpleNamespace(id=f"offline-{len(provider_calls)}", model=kwargs["model"],
                stop_reason="tool_use" if blocks else "end_turn", content=content,
                usage=Usage(input_tokens=120, output_tokens=180,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0, cost_usd=.001))
        def stream(self, **kwargs):
            streams.append(kwargs)
            raise AssertionError("Capo must not receive an unusable model")
    class Client:
        def __init__(self, **kwargs):
            import httpx
            self.messages = Messages()
            self._http = SimpleNamespace(timeout=httpx.Timeout(450.0))
    for owner in (specialist_base, llm_client, trade_idea):
        monkeypatch.setattr(owner, "OpenRouterClient", Client)
    monkeypatch.setattr(chat_tools, "dispatch", lambda *_a, **_k: {
        "ok": True, "data": {"status": "synthetic", "as_of": qualification["as_of"]}})
    monkeypatch.setattr(chat_tools, "_compatta_portfolio_live", lambda *_: {"positions": []})
    monkeypatch.setattr(mandato_pm, "carica", mandato_pm.profilo_esempio)

    def initial_model(board):
        assert board.current_round == 1 and board.model_phase == "building"
        assert all(board.read(desk, 0) for desk in DESKS)
        assert all(board.read(desk, 1) for desk in DESKS if desk != "fundamentals")
        assert len(board.data["_model_consultations"]) == 5
        assert all(row["status"] == "complete" and row["fundamentals_decision"]
                   for row in board.data["_model_consultations"])
        prepared.append("initial-model")
        board.record_valuation("SYNTH-EXT", {"ok": False,
            "valuation_usability": {"usable": False}, "error": "capital bridge is incomplete"},
            "trade-idea-orchestrator")

    detail = trade_idea.execute_trade_idea(run_id, store=s, lock_path=tmp_path / "paid.lock",
        output_dir=tmp_path / "report",
        portfolio_loader=lambda: {"positions": [], "cash_disponibile_eur": 0},
        mandate_loader=mandato_pm.profilo_esempio,
        preparer_binder=lambda *_: (None, {"status": "deterministic"}),
        risk_loader=lambda: {}, stress_loader=lambda: {}, candidate_metrics_loader=lambda *_: {},
        valuation_evaluator=initial_model, source_qualifier=lambda *args, **kwargs: qualification,
        catalog_fetcher=lambda: payload["catalog_snapshot"], isolated_tool_dispatcher=chat_tools.dispatch,
        isolated_facts_loader=lambda: "Offline facts")
    assert prepared == ["initial-model"]
    assert not streams
    assert not any(call["max_tokens"] == 12000 or "Round 2." in str(call["messages"][0]["content"])
                   for call in provider_calls)
    assert detail["run"]["technical_status"] in {"failed", "incomplete"}
    assert detail["cost"]["requests"] == len(provider_calls) > 0


def test_revision_tool_never_mutates_or_regenerates_the_model(tmp_path, monkeypatch):
    from bellomberg.agents import chat_tools
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="TEST",
        budget_gate=SimpleNamespace(wrap_client=lambda client, role: client))
    board.valuation_results["TEST"] = {"ticker": "TEST", "snapshot_id": "original",
        "generation_id": "g-original", "valuation_usability": {"usable": True},
        "request_origin": "trade-idea-orchestrator"}
    called = []
    monkeypatch.setattr(chat_tools, "dispatch", lambda *_a, **_k: called.append("regenerated") or {})
    specialist = Specialist(board, client=SimpleNamespace(messages=SimpleNamespace()))
    answer = specialist._execute_meta_tool("get_valuation", {"ticker": "TEST", "growth_path": [0.15]})
    assert answer["ok"] is False
    assert "review_candidate_model" in answer["error"]
    assert called == []
    assert board.valuation_results["TEST"]["generation_id"] == "g-original"


def test_final_review_dispatch_attests_all_six_actual_reports(migrated, tmp_path, monkeypatch):
    from bellomberg.agents import consigliere_multi
    from test_trade_idea_economic import qualified, _operating_plan
    from bellomberg.valuation.trade_idea_model import prepare
    model = prepare(qualified(tmp_path), lambda *_: deepcopy(_operating_plan()), tmp_path / "model")
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="SYNTH-EXT")
    board.model_registry = trade_idea._model_registry(migrated)
    board.model_roots = [tmp_path]
    board.r2_specialists = set(DESKS)
    trade_idea._record_candidate_model(board, model)
    seen = []
    def run(self, round_n):
        seen.append((self.name, round_n))
        report = "Reviewed exact fictional workbook " + model["generation_id"] + " from " + self.name
        self.blackboard.write(self.name, round_n, report)
        self.run_result_status = "complete"
        return report
    for cls in consigliere_multi.SPECIALIST_ORDER:
        monkeypatch.setattr(cls, "run", run)
        monkeypatch.setattr(cls, "__init__", lambda self, board: setattr(self, "blackboard", board))
    consigliere_multi.run_round(board, 2)
    assert seen == [(desk, 2) for desk in DESKS]
    assert set(board.data["_desk_model_reviews"]) == set(DESKS)
    with pytest.raises(RuntimeError, match="Red Team discussion is missing or stale"):
        trade_idea._require_final_desk_models(board)


def test_objection_reply_from_wrong_desk_is_rejected(tmp_path):
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="TEST")
    board.data["_objections"] = [{"objection": {"id": "macro-rates", "desk": "macro",
        "category": "driver", "material": True, "objection": "Rate transmission is unproven",
        "evidence_refs": [], "requested_change": "Test refinancing"}, "response": None,
        "evidence_refs": [], "state": "open", "model_revision_id": None}]
    answer = trade_idea.handle_trade_idea_review_tool(board, "fundamentals", "respond_trade_idea_objection",
        {"objection_id": "macro-rates", "response": "Everything is fine", "state": "answered",
         "evidence_refs": []})
    assert answer["ok"] is False
    assert board.data["_objections"][0]["response"] is None


def test_a_fx_label_cannot_qualify_foreign_risk_and_stress():
    from bellomberg.core import mandato_pm
    book = {"positions": [{"ticker": "TEST", "quantita": 1, "valuta": "USD",
                           "valore_mercato_eur": 100, "valore_mercato": 111}],
            "cash_disponibile_eur": 1000, "cash_source": "sqlite:cash_state"}
    risk = {"portfolio": {"var_99_1d_pct": -1}, "per_asset": {"TEST": {"vol_annual_pct": 20}},
            "fx_conversion": {"local_declared": []}, "skipped_tickers": []}
    stress = {"returns_basis": "EUR FX converted", "stress_scenario": "gfc_2008",
              "stress_fallback": False, "stress_meta": {"window_loss_pct": -5,
                  "proxied": {}, "zero_filled_days": {}}}
    sizing = trade_idea._compute_sizing(book, "TEST", mandato_pm.profilo_esempio(), currency="USD",
        risk_data=risk, stress_data=stress, candidate_metrics={"status": "not_applicable"})
    assert sizing["_trade_idea_measurements"]["risk_status"] == "unavailable"
    assert sizing["_trade_idea_measurements"]["stress_status"] == "unavailable"


def test_candidate_model_registration_is_real_exact_and_idempotent(migrated, tmp_path):
    from test_trade_idea_economic import qualified, _operating_plan
    from bellomberg.valuation.trade_idea_model import prepare
    import sqlite3
    q = qualified(tmp_path)
    model = prepare(q, lambda *_: deepcopy(_operating_plan()), tmp_path / "model")
    registry = trade_idea._model_registry(migrated)
    registered = trade_idea._register_candidate_model(registry, model)
    assert registered["_thesis_saved"]["snapshot_id"] == model["snapshot_id"]
    assert registry.get_valuation_snapshot(model["snapshot_id"], generation_id=model["generation_id"])
    repeated = trade_idea._register_candidate_model(registry, registered)
    assert repeated["_thesis_saved"] == registered["_thesis_saved"]
    with sqlite3.connect(migrated) as conn:
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM valuation_snapshot_links").fetchone()[0] == 1
    changed = {**model, "fair_value_base": model["fair_value_base"] + 1}
    with pytest.raises(ValueError, match="registration|immut"):
        trade_idea._register_candidate_model(registry, changed)


def test_model_revision_requires_the_qualified_document_ids(tmp_path):
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="TEST")
    board.current_round = 2
    board.source_qualification = {"source_report": {"documents": [{"id": "filing-proof"}]}}
    board.tool_receipts.append({"tool": "get_valuation", "success": True})
    board.valuation_results["TEST"] = {"generation_id": "initial"}
    answer = trade_idea.handle_trade_idea_review_tool(board, "fundamentals", "review_candidate_model", {
        "generation_id": "initial", "action": "revise", "rationale": "Opening bridge correction",
        "evidence_refs": ["get_valuation"], "changes": {"assumptions": {"lease_treatment": "debt"}},
        "needs_paid_preparation": False})
    assert answer["ok"] is False
    assert "qualified document" in answer["error"]
    assert board.data.get("_revision_requests") is None


def test_research_only_pdf_does_not_make_a_complete_trade_package(tmp_path, monkeypatch):
    from hashlib import sha256
    import json
    from bellomberg.storage.trade_idea_store import trade_idea_pdf_ready
    from bellomberg.core import paths
    pdf = tmp_path / "research.pdf"
    pdf.write_bytes(b"%PDF isolated diagnostic")
    digest = sha256(pdf.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "run_type": "trade_idea", "run_id": "isolated", "ticker": "TEST",
        "pdf_quality": {"status": "ready", "analytical_pages": 11, "analytical_words": 4100},
        "artifacts": [{"kind": "pdf", "status": "ready", "path": str(pdf), "sha256": digest}],
        "expected_hashes": {str(pdf): digest}, "attachments": [str(pdf)]}
    monkeypatch.setattr(paths, "REPORT_DIR", tmp_path)
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert trade_idea_pdf_ready(encoded, sha256(encoded.encode()).hexdigest(), run_id="isolated", ticker="TEST") is False


def test_red_team_retrieval_has_a_real_bound_receipt(tmp_path, monkeypatch):
    import json
    from bellomberg.agents import chat_tools, red_team
    from bellomberg.core import llm_client, mandato_pm
    from bellomberg.core.llm_client import Usage
    from trade_idea_evolution_fixtures import committee_review
    calls = []
    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            content = ([SimpleNamespace(type="tool_use", id="risk-proof", name="get_portfolio_risk", input={})]
                if len(calls) == 1 else [SimpleNamespace(type="text", text=json.dumps(committee_review()))])
            return SimpleNamespace(stop_reason="tool_use" if len(calls) == 1 else "end_turn", content=content,
                usage=Usage(input_tokens=20, output_tokens=20, cost_usd=0.001))
    monkeypatch.setattr(llm_client, "OpenRouterClient", lambda **_: SimpleNamespace(messages=Messages()))
    monkeypatch.setattr(mandato_pm, "carica", mandato_pm.profilo_esempio)
    monkeypatch.setattr(chat_tools, "dispatch", lambda *_a, **_k: {
        "ok": True, "data": {"var_99_1d_pct": -1.2, "as_of": "2026-09-27"}, "_source": "synthetic EUR history"})
    board = Blackboard(memory_db=None, memo_id=None, heartbeat_path=tmp_path / "hb.json",
        run_scope="trade_idea", run_id="isolated", target_ticker="TEST",
        budget_gate=SimpleNamespace(wrap_client=lambda client, role: client))
    board.write("fundamentals", 1, "Independent synthetic analysis")
    assert red_team.run_red_team(board)
    assert len(calls) == 2
    assert len(board.tool_receipts) == 1
    assert board.tool_receipts[0]["success"] is True
    assert json.loads(board.tool_receipts[0]["output"])["data"]["var_99_1d_pct"] == -1.2


def test_a_superseded_model_receipt_cannot_back_the_final_model_numbers():
    import json
    receipts = [{"tool": "get_valuation", "input": {"ticker": "TEST"}, "success": True,
        "truncated": False, "output": json.dumps({"generation_id": generation,
            "valuation_date": "2026-09-27", "fair_value_base": value, "currency": "EUR"})}
        for generation, value in (("old", 20.0), ("final", 30.0))]
    result = {"summary": "Fair value is 20.0 EUR [src: get_valuation].",
        "valuation_refs": [{"generation_id": "final"}],
        "evidence": [{"id": "model", "source": "[src: get_valuation] common model", "as_of": "2026-09-27",
                     "summary": "Fair value 20.0 EUR", "url": None}],
        "dossier": [{"key": "executive", "paragraphs": [], "evidence_ids": ["model"], "tables": []}]}
    gaps = trade_idea._numeric_claim_gaps(result, SimpleNamespace(tool_receipts=receipts), "TEST",
                                        "2026-09-28T08:00:00Z", {})
    assert any("20.0" in gap for gap in gaps), gaps
