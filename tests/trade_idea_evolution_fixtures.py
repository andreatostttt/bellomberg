"""Provider protocol fixtures; every value describes fictional offline research."""
import json
from copy import deepcopy
from hashlib import sha256


DESKS = ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options")
MODEL_META_TOOLS = {"read_blackboard", "ask_specialist", "get_valuation",
    "add_guidance", "add_research_note", "review_candidate_model", "respond_trade_idea_objection",
    "get_candidate_model_inputs", "submit_candidate_model_plan", "read_candidate_model_consultation"}


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def model_build_tool_blocks(kwargs, basis_plan, document_ids=(), *, ticker="SYNTH-EXT"):
    """A fictional Fundamentals authors/adopts a real plan through the live tools.

    Five questions invoke the real peer classes. Their real tool-result IDs, rather
    than invented consultation receipts, are used for the adoption decisions.
    """
    context = str(kwargs["messages"][0].get("content") or "")
    if ("You are the fundamentals desk" not in str(kwargs.get("system"))
            or "Round 1." not in context or kwargs.get("tool_choice") == {"type": "none"}):
        return []
    calls, conversations = set(), []
    for message in kwargs["messages"]:
        for block in message.get("content") if isinstance(message.get("content"), list) else []:
            name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if name:
                calls.add(name)
            if isinstance(block, dict) and block.get("type") == "tool_result":
                try:
                    answer = json.loads(block["content"])
                except (ValueError, TypeError):
                    continue
                if isinstance(answer, dict) and isinstance(answer.get("consultation"), dict):
                    conversations.append(answer["consultation"])
    if "get_candidate_model_inputs" not in calls:
        return [{"name": "get_candidate_model_inputs", "input": {
            "scope": scope, "drivers": [next(iter(drivers))]}}
            for scope, drivers in [("model", basis_plan["model"]), *basis_plan["scenarios"].items()]]
    if "ask_specialist" not in calls:
        refs = list(document_ids) or ["get_candidate_model_inputs"]
        return [{"name": "ask_specialist", "input": {
            "specialist": desk,
            "question": "What transmission mechanism or downside invalidates the proposed cash-flow drivers?",
            "draft_assumptions": {"wacc": deepcopy(basis_plan["scenarios"]["base"]["wacc"])},
            "evidence_refs": refs}} for desk in DESKS if desk != "fundamentals"]
    if "submit_candidate_model_plan" not in calls:
        assert {row["desk"] for row in conversations if row["status"] == "complete"} == set(DESKS)-{"fundamentals"}
        reuse = [{"scope": scope, "driver": driver,
            "basis_plan_sha256": _digest(basis_plan), "driver_sha256": _digest(item)}
            for scope, drivers in [("model", basis_plan["model"]), *basis_plan["scenarios"].items()]
            for driver, item in drivers.items()]
        return [{"name": "submit_candidate_model_plan", "input": {
            "reuse": reuse, "plan": {"scenario_rationale": deepcopy(basis_plan["scenario_rationale"])},
            "rationale": "Explicit fictional Fundamentals adoption of qualified inputs after actual peer scrutiny; not PM approval.",
            "consultation_decisions": [{"consultation_id": row["id"], "decision": "incorporated",
                "rationale": "The actual peer answer is consistent with the declared synthetic scenario and its limits."}
                for row in conversations]}}]
    if "get_valuation" not in calls:
        return [{"name": "get_valuation", "input": {"ticker": ticker}}]
    return []


def committee_review():
    return {"decisive_questions": ["Which sourced model assumption could invalidate the investment?"],
            "objections": [{"id": "challenge-" + desk, "desk": desk,
                "category": "driver" if desk != "quant" else "risk", "material": True,
                "objection": "The synthetic " + desk + " assumption needs explicit counterevidence.",
                "evidence_refs": ["get_valuation"], "requested_change": "Explain the supported driver and its limits."}
                for desk in DESKS]}


def review_tool_block(kwargs, workbook):
    """Serve review/reply tool turns after the real class has collected a market tool."""
    messages = kwargs["messages"]
    names = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if name:
                names.add(name)
    if not names or kwargs.get("tool_choice") == {"type": "none"}:
        return None
    system = str(kwargs.get("system") or "")
    desk = next((name for name in DESKS if "You are the " + name + " desk" in system), None)
    context = str(messages[0].get("content") or "")
    if (workbook and desk == "fundamentals" and "Round 2." in context
            and "review_candidate_model" not in names):
        return {"name": "review_candidate_model", "input": {
            "generation_id": workbook["generation_id"], "action": "retain",
            "rationale": "The archived synthetic records and accounting bridge support the unchanged initial candidate.",
            "evidence_refs": ["get_valuation"], "changes": {}, "needs_paid_preparation": False}}
    if desk and "Round 2." in context and "respond_trade_idea_objection" not in names:
        return {"name": "respond_trade_idea_objection", "input": {
            "objection_id": "challenge-" + desk,
            "response": "The exact common workbook supplies the evidence; the synthetic economic uncertainty remains explicit.",
            "state": "answered", "evidence_refs": ["get_valuation"], "model_revision_id": None}}
    return None


def qualified_source(workbook, fingerprint="b" * 64):
    return {"status": "qualified", "fingerprint": fingerprint,
            "method_id": (workbook.get("valuation_decision") or {}).get("method_id"),
            "reasons": [], "coverage": {"basis": "fictional documented model records"},
            "bundle": workbook.get("acquisition_snapshot"), "source_report": {"documents": []}}


def historical_fx_engines(monkeypatch, tmp_path, book, last_close, *, missing_fx=False):
    """Real risk and Monte Carlo engines over exact synthetic price/FX closes."""
    import numpy as np
    import pandas as pd
    from bellomberg.portfolio import portfolio_risk as risk, portfolio_montecarlo as mc
    recent = pd.bdate_range(end=last_close, periods=260)
    historic = pd.bdate_range("2008-09-08", "2009-03-30")
    calls, outputs = [], {"simulations": 0}
    def download(symbols, **kwargs):
        calls.append((list(symbols), dict(kwargs)))
        if missing_fx and any(symbol.startswith("EUR") and symbol.endswith("=X") for symbol in symbols):
            return pd.DataFrame()
        stress = "start" in kwargs and str(kwargs["start"]) < "2010"
        dates = historic if stress else recent
        n = np.arange(len(dates))
        data = {}
        for symbol in symbols:
            if symbol == "EURUSD=X":
                values = np.full(len(dates), 1.1) if stress else 1.1 + .0001*n + .005*np.sin(n/6)
            elif stress:
                values = np.linspace(100, 95, len(dates))
            elif symbol == "SYNTH-EXT":
                values = 100 + .02*n + 3*np.sin(n/4)
            elif symbol == "SYNTH-PEER":
                values = 110 + .03*n + 3*np.cos(n/3)
            elif symbol == "SPY":
                values = 200 + .04*n + 5*np.sin(n/5)
            else:
                raise AssertionError("unexpected historical symbol: " + symbol)
            data[("Close", symbol)] = values
        return pd.DataFrame(data, index=dates)
    monkeypatch.setattr("yfinance.download", download)
    monkeypatch.setattr("bellomberg.cli.price_updater.data_ticker_map", lambda symbols, **_: {s:s for s in symbols})
    monkeypatch.setattr("bellomberg.cli.price_updater.data_ticker", lambda symbol: symbol)
    monkeypatch.setattr(risk, "prezzi_speciali", lambda: {"origine":"file", "prezzi":{"senza_yfinance":[]}})
    monkeypatch.setattr(mc, "prezzi_speciali", lambda: {"origine":"file", "prezzi":{"senza_yfinance":[]}})
    monkeypatch.setattr(mc, "STRESS_CACHE_DIR", str(tmp_path / "stress-cache"))
    simulate = mc._simulate_block_bootstrap
    def measured_simulation(*args, **kwargs):
        outputs["simulations"] += 1
        return simulate(*args, **kwargs)
    monkeypatch.setattr(mc, "_simulate_block_bootstrap", measured_simulation)
    class FakeDB:
        def get_portfolio_summary(self):
            return book
    monkeypatch.setattr(risk, "MemoryDB", FakeDB)
    def risk_loader():
        outputs["risk"] = risk.compute_portfolio_risk(force=True, strict_eur=True)
        return outputs["risk"]
    def stress_loader():
        nav = sum(row["valore_mercato_eur"] for row in book["positions"])
        outputs["stress"] = mc.run_monte_carlo(horizon_days=252, n_sims=1000, lookback_years=1,
            method="block_bootstrap", stress_scenario="gfc_2008", seed=7, force_refresh=True,
            _override_weights={row["ticker"]:row["valore_mercato_eur"]/nav for row in book["positions"]},
            _override_nav=nav, return_currencies={row["ticker"]:row["valuta"] for row in book["positions"]})
        return outputs["stress"]
    return risk_loader, stress_loader, calls, outputs
