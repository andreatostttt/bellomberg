"""Only an explicit quote refresh may relax the original price fingerprint.

All persistence is temporary and providers are absent. Run with offline_pytest.
"""
from copy import deepcopy
import json
import sqlite3

import pytest

from bellomberg.storage.trade_idea_store import BudgetBlocked, RunConflict, _digest
from test_trade_idea_store import (
    db_path, migrated, store, request, result, all_checks, finish, _save_resume_checkpoint,
)


def changed_price_parent(path, *, unit="EUR"):
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('OTHER',10,100,?,1)", (unit,))
        conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) "
                     "VALUES('OTHER',101,?,'frozen fixture','2026-10-02T10:00:00')", (unit,))
    current = store(path)
    parent = current.create_run(request(), idempotency_key="price-refresh-parent")["run"]["id"]
    token = current.claim_run(parent)
    checkpoint = _save_resume_checkpoint(current, parent, token)
    current.reserve_cost(parent, "already-paid", "specialist:macro", "meta/muse-spark-1.3", "1")
    current.reconcile_cost(parent, "already-paid", charged_usd="0.40",
        usage={"cost_usd": "0.40"}, receipt={"response_id": "frozen-paid", "model": "meta/muse-spark-1.3"})
    current.interrupt_run(parent, reason="Frozen interrupted research")
    move_price(path, 102, unit=unit)
    return current, parent, checkpoint


def move_price(path, value, *, unit="EUR"):
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) "
                     "VALUES('OTHER',?,?,'frozen fixture','2026-10-02T11:00:00')", (value, unit))


def child_for(current, parent):
    return current.create_continuation(parent, idempotency_key="explicit-price-refresh",
        authorize_new_requests=True, authorize_price_refresh=True)["run"]["id"]


def receipt_for(current, run_id, proposal):
    before, after = current.price_refresh_context(run_id), current.price_refresh_context(run_id)
    evidence = {"portfolio": {"fixture": "current book"}, "risk": {"fixture": "current risk"},
        "stress": {"fixture": "current stress"}, "sizing": {"fixture": "current size"},
        "candidate_quote": {"fixture": "current quote"}, "fx_receipt": {"fixture": "current FX"},
        "proposal": deepcopy(proposal)}
    return {"version": 1, "run_id": run_id, "before": before, "after": after,
        "measurements": {name: True for name in ("portfolio_reloaded", "risk_recomputed",
            "stress_recomputed", "sizing_recomputed", "candidate_price_verified", "fx_verified", "proposal_valid",
            "context_stable")},
        "evidence_sha256": _digest(evidence), "evidence": evidence}


def test_price_only_change_requires_explicit_authorization_and_preserves_the_accepted_context(migrated):
    current, parent, checkpoint = changed_price_parent(migrated)
    before = current.get_run(parent)
    assert before["recovery"]["price_refresh_required"] is True
    assert before["recovery"]["can_continue"] is False
    assert before["recovery"]["can_continue_with_price_refresh"] is True
    with pytest.raises(RunConflict, match="price|context|book"):
        current.create_continuation(parent, idempotency_key="not-authorized", authorize_new_requests=True)
    child = child_for(current, parent)
    detail = current.get_run(child)
    grant = detail["run"]["continuation"]["price_refresh"]
    assert grant["scope"] == "portfolio_price_inputs"
    assert grant["authorize_price_refresh"] is True and grant["require_fresh_final_verification"] is True
    with sqlite3.connect(migrated) as conn:
        accepted = conn.execute("SELECT context_json FROM trade_idea_runs WHERE id=?", (parent,)).fetchone()[0]
        inherited = conn.execute("SELECT context_json FROM trade_idea_runs WHERE id=?", (child,)).fetchone()[0]
    assert inherited == accepted
    assert grant["accepted_context_sha256"] == _digest(json.loads(accepted))
    assert grant["accepted_price_inputs_sha256"] != grant["observed_price_inputs_sha256"]
    assert detail["progress"]["checkpoint"] == checkpoint
    assert detail["cost"]["charged_usd"] == "0.40" and detail["cost"]["requests"] == 1
    after = current.get_run(parent)
    for key in ("run", "progress", "cost", "result"):
        assert after[key] == before[key]


def test_price_refresh_grant_survives_second_resume_without_changing_its_original_observation(migrated):
    current, parent, checkpoint = changed_price_parent(migrated)
    child = child_for(current, parent)
    grant = current.get_run(child)["run"]["continuation"]["price_refresh"]
    current.claim_run(child)
    current.interrupt_run(child, reason="Second frozen interruption")
    move_price(migrated, 103)
    grandchild = current.create_continuation(child, idempotency_key="price-refresh-again",
        authorize_new_requests=True)["run"]["id"]
    detail = current.get_run(grandchild)
    assert detail["run"]["continuation"]["price_refresh"] == grant
    assert detail["progress"]["checkpoint"] == checkpoint
    assert detail["cost"]["requests"] == 1 and detail["cost"]["charged_usd"] == "0.40"
    assert current.price_refresh_context(grandchild)["price_inputs_sha256"] != grant["observed_price_inputs_sha256"]


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_ancestry_rejects_a_lost_or_replaced_price_refresh_grant(migrated, fault):
    current, parent, _ = changed_price_parent(migrated)
    child = child_for(current, parent)
    current.claim_run(child)
    current.interrupt_run(child, reason="Frozen second interruption")
    grandchild = current.create_continuation(child, idempotency_key="grant-preservation",
        authorize_new_requests=True)["run"]["id"]
    accepted = current.get_accepted_request(grandchild)
    if fault == "missing": accepted["continuation"].pop("price_refresh")
    else: accepted["continuation"]["price_refresh"]["observed_price_inputs_sha256"] = "0" * 64
    # Deliberately bypass SQLite's immutable trigger only in this temporary
    # database to prove ancestry validation also rejects internally resealed drift.
    with sqlite3.connect(migrated) as conn:
        conn.execute("DROP TRIGGER trade_idea_run_input_immutable")
        conn.execute("UPDATE trade_idea_runs SET request_json=?,request_sha256=? WHERE id=?",
            (json.dumps(accepted), _digest(accepted), grandchild))
    with pytest.raises(RunConflict, match="price refresh authorization"):
        current.price_refresh_context(grandchild)


@pytest.mark.parametrize("changed", ["position", "cash", "trade", "feedback", "mandate", "currency"])
def test_price_refresh_never_waives_non_price_context_changes(migrated, monkeypatch, changed):
    current, parent, _ = changed_price_parent(migrated)
    with sqlite3.connect(migrated) as conn:
        if changed == "position": conn.execute("UPDATE positions SET quantita=11 WHERE ticker='OTHER'")
        elif changed == "cash": conn.execute("UPDATE cash_state SET balance_cents=balance_cents+1,version=version+1")
        elif changed == "trade": conn.execute("INSERT INTO trade_history(ticker,action,quantita,data) VALUES('TEST','BUY',1,'now')")
        elif changed == "feedback": conn.execute("INSERT INTO decisions(timestamp,ticker,action,status,pm_feedback) VALUES('now','TEST','RESEARCH','PENDING','New PM feedback')")
        elif changed == "currency": conn.execute("UPDATE positions SET valuta='USD' WHERE ticker='OTHER'")
    if changed == "mandate":
        monkeypatch.setattr(current, "_mandate_loader", lambda: "c" * 64)
    assert current.get_run(parent)["recovery"]["can_continue_with_price_refresh"] is False
    with pytest.raises(RunConflict, match="context|book|feedback|mandate"):
        child_for(current, parent)
    assert current.list_runs()["total"] == 1


@pytest.mark.parametrize("period,position_unit,quote_unit", [
    ("latest", "EUR", "USD"), ("previous", "EUR", "USD"),
    ("latest", "GBX", "GBP"), ("previous", "GBX", "GBP")])
def test_price_refresh_rejects_current_or_previous_quote_unit_mismatch(migrated, period, position_unit, quote_unit):
    current, parent, _ = changed_price_parent(migrated, unit=position_unit)
    with sqlite3.connect(migrated) as conn:
        if period == "latest":
            conn.execute("UPDATE position_prices SET valuta=? WHERE id=(SELECT MAX(id) FROM position_prices)", (quote_unit,))
        else:
            conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) "
                         "VALUES('OTHER',100,?,'frozen fixture','2026-10-01T10:00:00')", (quote_unit,))
    assert current.get_run(parent)["recovery"]["can_continue_with_price_refresh"] is False
    with pytest.raises(RunConflict, match="currency|unit"):
        child_for(current, parent)
    with pytest.raises(RunConflict, match="currency|unit"):
        current.price_refresh_context(parent)


def test_price_refresh_recognizes_only_the_existing_pence_alias_without_converting_pounds(migrated):
    current, parent, _ = changed_price_parent(migrated, unit="GBX")
    with sqlite3.connect(migrated) as conn:
        conn.execute("UPDATE position_prices SET valuta='GBp' WHERE id=(SELECT MAX(id) FROM position_prices)")
    assert current.get_run(parent)["recovery"]["can_continue_with_price_refresh"] is True
    child = child_for(current, parent)
    assert current.price_refresh_context(child)["price_inputs_sha256"]


def test_price_refresh_route_demotes_a_quote_currency_race_without_losing_result(migrated):
    current, parent, _ = changed_price_parent(migrated)
    child = child_for(current, parent)
    payload = result()
    finish(current, child, payload)
    proof = receipt_for(current, child, payload["proposal"])
    with sqlite3.connect(migrated) as conn:
        conn.execute("UPDATE position_prices SET valuta='USD' WHERE id=(SELECT MAX(id) FROM position_prices)")
    destination = current.route_result(child, {**all_checks(), "candidate_price_revalidated": True,
        "fx_revalidated": True, "price_refresh_verification": proof})
    assert destination["kind"] == "research"
    assert "currency" in destination["reason"].lower() or "unit" in destination["reason"].lower()
    assert current.get_run(child)["result"]["proposal"] == payload["proposal"]


@pytest.mark.parametrize("fault", ["unknown", "overrun"])
def test_price_refresh_does_not_waive_unresolved_costs_or_original_budget(migrated, fault):
    current, parent, _ = changed_price_parent(migrated)
    with sqlite3.connect(migrated) as conn:
        if fault == "unknown": conn.execute("UPDATE trade_idea_costs SET status='unknown' WHERE request_id='already-paid'")
        else: conn.execute("UPDATE trade_idea_costs SET charged_usd='5.01' WHERE request_id='already-paid'")
    assert current.get_run(parent)["recovery"]["can_continue_with_price_refresh"] is False
    with pytest.raises(BudgetBlocked):
        child_for(current, parent)


def test_fresh_final_price_verification_routes_without_rewriting_the_accepted_book(migrated):
    current, parent, _ = changed_price_parent(migrated)
    child = child_for(current, parent)
    payload = result()
    finish(current, child, payload)
    proof = receipt_for(current, child, payload["proposal"])
    route = current.route_result(child, {**all_checks(), "candidate_price_revalidated": True,
        "fx_revalidated": True, "price_refresh_verification": proof})
    assert route["kind"] == "dcn"
    assert current.route_result(child, {}) == route
    with sqlite3.connect(migrated) as conn:
        contexts = conn.execute("SELECT context_json FROM trade_idea_runs WHERE id IN (?,?)", (parent, child)).fetchall()
        assert contexts[0] == contexts[1]
        assert conn.execute("SELECT status FROM decisions WHERE id=?", (route["decision_id"],)).fetchone()[0] == "PENDING"


@pytest.mark.parametrize("fault", ["missing", "digest", "proposal", "run_id", "before", "measurement", "context_unstable", "price_race", "cash_race", "fx"])
def test_price_refresh_cannot_promote_without_exact_current_final_evidence(migrated, fault):
    current, parent, _ = changed_price_parent(migrated)
    child = child_for(current, parent)
    payload = result()
    finish(current, child, payload)
    proof = receipt_for(current, child, payload["proposal"])
    checks = {**all_checks(), "candidate_price_revalidated": True, "fx_revalidated": True,
        "price_refresh_verification": proof}
    if fault == "missing": checks.pop("price_refresh_verification")
    elif fault == "digest": proof["evidence_sha256"] = "0" * 64
    elif fault == "proposal":
        proof["evidence"]["proposal"]["eur_amount"] += 1
        proof["evidence_sha256"] = _digest(proof["evidence"])
    elif fault == "run_id": proof["run_id"] = parent
    elif fault == "before": proof["before"]["current_context_sha256"] = "0" * 64
    elif fault == "measurement": proof["measurements"]["risk_recomputed"] = False
    elif fault == "context_unstable": proof["measurements"]["context_stable"] = False
    elif fault == "price_race": move_price(migrated, 104)
    elif fault == "cash_race":
        with sqlite3.connect(migrated) as conn:
            conn.execute("UPDATE cash_state SET version=version+1")
    elif fault == "fx": checks["fx_revalidated"] = False
    destination = current.route_result(child, checks)
    assert destination["kind"] == "research"
    assert "price" in destination["reason"].lower() or "book" in destination["reason"].lower()
