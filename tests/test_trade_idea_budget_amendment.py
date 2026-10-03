"""Explicit aggregate-budget amendments, on disposable SQLite and frozen providers."""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

import pytest

from bellomberg.storage.trade_idea_store import BudgetBlocked, IdempotencyConflict, RunConflict, _digest
from test_trade_idea_store import db_path, migrated, store, _save_resume_checkpoint
from test_trade_idea_pipeline import _priced_request, _gate, _call_kwargs, FakeMessages
from types import SimpleNamespace
from test_preprovider_receipt import rejected


def snapshot(path, run_id):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM trade_idea_runs WHERE id=?", (run_id,)).fetchone()), [
            dict(row) for row in conn.execute("SELECT * FROM trade_idea_costs WHERE run_id=? ORDER BY request_id", (run_id,))]


def parent_case(path, *, paid="9.99", unresolved=None):
    current, payload = store(path), _priced_request(budget="10")
    ident = current.create_run(payload, idempotency_key="budget-parent")["run"]["id"]
    token = current.claim_run(ident)
    _save_resume_checkpoint(current, ident, token)
    current.reserve_cost(ident, "original-paid", "specialist:macro", payload["models"]["specialist"]["model"], paid)
    current.reconcile_cost(ident, "original-paid", charged_usd=paid,
        usage={"cost_usd": paid}, receipt={"response_id": "frozen-paid", "model": payload["models"]["specialist"]["model"]})
    if unresolved:
        current.reserve_cost(ident, "unresolved", "specialist:macro", payload["models"]["specialist"]["model"], ".005")
        if unresolved == "unknown":
            current.mark_cost_unknown(ident, "unresolved", reason="frozen disconnection", receipt={"error": "frozen disconnection"})
    current.interrupt_run(ident, reason="Frozen stopped run")
    return current, payload, ident


def test_explicit_increase_uses_same_cost_chain_and_preserves_parent_and_grant(migrated):
    current, payload, parent = parent_case(migrated)
    original = snapshot(migrated, parent)
    accepted = current.get_accepted_request(parent)
    child = current.create_continuation(parent, idempotency_key="budget-child",
        authorize_new_requests=True, budget_limit_usd="15")
    ident = child["run"]["id"]
    request = current.get_accepted_request(ident)
    amendment = request["continuation"]["budget_amendment"]
    assert amendment == {"version": 1, "scope": "aggregate_chain_budget", "parent_run_id": parent,
        "parent_request_sha256": original[0]["request_sha256"], "previous_budget_limit_usd": "10",
        "budget_limit_usd": "15", "authorized_at": request["continuation"]["authorized_at"],
        "authorize_budget_increase": True}
    assert request["authorization"] == accepted["authorization"]
    assert child["cost"]["charged_usd"] == "9.99" and child["cost"]["budget_limit_usd"] == "15"
    assert child["cost"]["chain_run_ids"] == [parent, ident]
    current.claim_run(ident)
    fake = FakeMessages("0.02")
    _gate(current, ident, payload).wrap_client(SimpleNamespace(messages=fake), role="specialist:macro").messages.create(
        **{**_call_kwargs(), "max_tokens": 100000})
    assert fake.calls == 1
    assert Decimal(current.get_run(ident)["cost"]["charged_usd"]) == Decimal("10.01")
    assert snapshot(migrated, parent) == original
    current.reserve_cost(ident, "last-hold", "specialist:macro", payload["models"]["specialist"]["model"], "4.99")
    with pytest.raises(BudgetBlocked, match="limit"):
        current.reserve_cost(ident, "over-cap", "specialist:macro", payload["models"]["specialist"]["model"], ".01")


def test_budget_amendment_idempotence_and_inherited_ceiling(migrated):
    current, _payload, parent = parent_case(migrated)
    child = current.create_continuation(parent, idempotency_key="budget-child", authorize_new_requests=True, budget_limit_usd="15")
    for value in ("15", "15.00", None):
        repeated = current.create_continuation(parent, idempotency_key="other-click", authorize_new_requests=True, budget_limit_usd=value)
        assert repeated["created"] is False and repeated["run"]["id"] == child["run"]["id"]
    with pytest.raises(IdempotencyConflict, match="budget"):
        current.create_continuation(parent, idempotency_key="budget-child", authorize_new_requests=True, budget_limit_usd="16")
    ident = child["run"]["id"]
    current.claim_run(ident)
    current.interrupt_run(ident, reason="Next saved phase")
    next_child = current.create_continuation(ident, idempotency_key="budget-grandchild", authorize_new_requests=True)
    assert next_child["cost"]["budget_limit_usd"] == "15"
    assert next_child["cost"]["charged_usd"] == "9.99" and next_child["cost"]["requests"] == 1
    assert "budget_amendment" not in next_child["run"]["continuation"]


@pytest.mark.parametrize("value", ["10", "9", "0", "-1", "NaN", "Infinity", "", True, 15.0])
def test_only_explicit_valid_increases_create_a_child(migrated, value):
    current, _payload, parent = parent_case(migrated)
    before = snapshot(migrated, parent)
    with pytest.raises(ValueError):
        current.create_continuation(parent, idempotency_key="invalid", authorize_new_requests=True, budget_limit_usd=value)
    assert snapshot(migrated, parent) == before
    assert current.get_run(parent)["recovery"]["successor_run_id"] is None


@pytest.mark.parametrize("unresolved", ["unknown", "reserved"])
def test_increase_never_reconciles_or_bypasses_unresolved_requests(migrated, unresolved):
    current, _payload, parent = parent_case(migrated, unresolved=unresolved)
    before = snapshot(migrated, parent)
    with pytest.raises(BudgetBlocked, match="unresolved"):
        current.create_continuation(parent, idempotency_key="blocked", authorize_new_requests=True, budget_limit_usd="15")
    assert snapshot(migrated, parent) == before


def test_known_exhausted_budget_requires_explicit_new_capacity(migrated):
    current, _payload, parent = parent_case(migrated, paid="10")
    with pytest.raises(BudgetBlocked):
        current.create_continuation(parent, idempotency_key="no-increase", authorize_new_requests=True)
    child = current.create_continuation(parent, idempotency_key="increase", authorize_new_requests=True, budget_limit_usd="15")
    assert child["cost"]["remaining_known_usd"] == "5"


@pytest.mark.parametrize("mutation", ["missing_grant", "old_cap", "new_cap", "parent_hash", "authorized_at", "authorization", "projection"])
def test_ancestry_refuses_resealed_or_unbound_budget_changes(migrated, mutation):
    current, _payload, parent = parent_case(migrated)
    child = current.create_continuation(parent, idempotency_key="amend", authorize_new_requests=True, budget_limit_usd="15")
    ident = child["run"]["id"]
    request = deepcopy(current.get_accepted_request(ident))
    grant = request["continuation"]["budget_amendment"]
    if mutation == "missing_grant": request["continuation"].pop("budget_amendment")
    elif mutation == "old_cap": grant["previous_budget_limit_usd"] = "11"
    elif mutation == "new_cap": grant["budget_limit_usd"] = "16"
    elif mutation == "parent_hash": grant["parent_request_sha256"] = "0" * 64
    elif mutation == "authorized_at": grant["authorized_at"] = "different"
    elif mutation == "authorization": request["authorization"]["activities"].append("unaccepted_activity")
    else: request["budget_limit_usd"] = "16"
    # Adversarial local corruption in the disposable DB, never a migration.
    with sqlite3.connect(migrated) as conn:
        conn.execute("DROP TRIGGER trade_idea_run_input_immutable")
        conn.execute("UPDATE trade_idea_runs SET request_json=?,request_sha256=? WHERE id=?",
                     (json.dumps(request), _digest(request), ident))
    with pytest.raises(RunConflict):
        current.get_run(ident)


@pytest.fixture
def held_parent(migrated, rejected):
    current, payload = store(migrated), _priced_request(budget="10")
    parent = current.create_run(payload, idempotency_key="held-parent")["run"]["id"]
    token = current.claim_run(parent)
    _save_resume_checkpoint(current, parent, token)
    model = payload["models"]["specialist"]["model"]
    current.reserve_cost(parent, "previous-paid", "specialist:fundamentals", model, "6.93574090")
    current.reconcile_cost(parent, "previous-paid", charged_usd="6.93574090",
        usage={"cost_usd": "6.93574090"}, receipt={"response_id": "paid", "model": model})
    row, _events, failure = deepcopy(rejected)
    receipt = json.loads(row["receipt_json"])
    current.reserve_cost(parent, "held-request", "specialist:fundamentals", model, "1.548436250",
                         request_sha256=receipt["request_sha256"])
    current.mark_cost_unknown(parent, "held-request", reason=row["reason"], receipt=receipt)
    failure["request_id"] = "held-request"
    current.record_failure(parent, token, failure)
    current.interrupt_run(parent, reason="Frozen pre-provider rejection")
    return current, payload, parent


def test_explicit_held_rejection_preserves_costs_and_counts_full_reserve(held_parent, migrated):
    current, payload, parent = held_parent
    before = snapshot(migrated, parent)
    child = current.create_continuation(parent, idempotency_key="held-child", authorize_new_requests=True,
        budget_limit_usd="15", resume_with_held_rejection_request_id="held-request")
    cost, ident = child["cost"], child["run"]["id"]
    assert (cost["unknown_requests"], cost["held_unknown_requests"], cost["unacknowledged_unknown_requests"]) == (1, 1, 0)
    assert cost["remaining_known_usd"] is None
    assert Decimal(cost["remaining_after_holds_usd"]) == Decimal("6.515822850")
    assert snapshot(migrated, parent) == before
    grant = child["run"]["continuation"]["held_rejection"]
    assert grant["proof"]["transport_http_status"] is None
    assert grant["original_cost_row"]["status"] == "unknown"
    assert grant["original_cost_row"]["charged_usd"] is None
    current.claim_run(ident)
    fake = FakeMessages("0.02")
    _gate(current, ident, payload).wrap_client(SimpleNamespace(messages=fake), role="specialist:macro").messages.create(
        **{**_call_kwargs(), "max_tokens": 100000})
    assert fake.calls == 1 and snapshot(migrated, parent) == before
    summary = current.get_run(ident)["cost"]
    assert summary["unknown_requests"] == 1 and summary["remaining_known_usd"] is None
    with pytest.raises(BudgetBlocked, match="limit"):
        current.reserve_cost(ident, "too-large", "specialist:macro", payload["models"]["specialist"]["model"], "6.50")
    current.interrupt_run(ident, reason="Stopped after one new paid response")
    assert current.get_run(ident)["recovery"]["can_continue"] is True
    grandchild = current.create_continuation(ident, idempotency_key="held-grandchild", authorize_new_requests=True)
    assert grandchild["run"]["continuation"]["held_rejection"] == grant
    assert grandchild["cost"]["held_unknown_requests"] == 1
    assert snapshot(migrated, parent) == before


def test_held_authorization_does_not_cover_a_new_unknown(held_parent):
    current, payload, parent = held_parent
    child = current.create_continuation(parent, idempotency_key="new-unknown-child", authorize_new_requests=True,
        budget_limit_usd="15", resume_with_held_rejection_request_id="held-request")
    ident = child["run"]["id"]
    current.claim_run(ident)
    current.reserve_cost(ident, "new-unknown", "specialist:macro", payload["models"]["specialist"]["model"], ".1")
    current.mark_cost_unknown(ident, "new-unknown", reason="A new transport failure")
    summary = current.get_run(ident)["cost"]
    assert (summary["unknown_requests"], summary["held_unknown_requests"], summary["unacknowledged_unknown_requests"]) == (2, 1, 1)
    with pytest.raises(BudgetBlocked, match="unknown"):
        current.reserve_cost(ident, "blocked-request", "specialist:macro", payload["models"]["specialist"]["model"], ".1")
    current.interrupt_run(ident, reason="New unknown must block")
    with pytest.raises(BudgetBlocked, match="unresolved"):
        current.create_continuation(ident, idempotency_key="blocked-grandchild", authorize_new_requests=True)


def test_held_authorization_is_explicit_idempotent_and_does_not_mutate_on_rejection(held_parent, migrated):
    current, _payload, parent = held_parent
    before = snapshot(migrated, parent)
    with pytest.raises(BudgetBlocked, match="unresolved"):
        current.create_continuation(parent, idempotency_key="no-hold-consent", authorize_new_requests=True, budget_limit_usd="15")
    with pytest.raises(RunConflict):
        current.create_continuation(parent, idempotency_key="wrong-request", authorize_new_requests=True,
            budget_limit_usd="15", resume_with_held_rejection_request_id="previous-paid")
    assert snapshot(migrated, parent) == before
    child = current.create_continuation(parent, idempotency_key="held-once", authorize_new_requests=True,
        budget_limit_usd="15", resume_with_held_rejection_request_id="held-request")
    repeated = current.create_continuation(parent, idempotency_key="held-once", authorize_new_requests=True,
        budget_limit_usd="15", resume_with_held_rejection_request_id="held-request")
    assert repeated["created"] is False and repeated["run"]["id"] == child["run"]["id"]
    with pytest.raises(IdempotencyConflict, match="held"):
        current.create_continuation(parent, idempotency_key="held-once", authorize_new_requests=True,
            budget_limit_usd="15", resume_with_held_rejection_request_id="another-request")
    assert snapshot(migrated, parent) == before


def test_future_native_settlement_does_not_invalidate_historical_hold_grant(held_parent):
    current, payload, parent = held_parent
    child = current.create_continuation(parent, idempotency_key="future-settlement", authorize_new_requests=True,
        budget_limit_usd="15", resume_with_held_rejection_request_id="held-request")
    current.reconcile_cost(parent, "held-request", charged_usd="0.10", usage={"cost_usd": "0.10"},
        receipt={"response_id": "later-provider-proof", "model": payload["models"]["specialist"]["model"]})
    summary = current.get_run(child["run"]["id"])["cost"]
    assert summary["unknown_requests"] == summary["held_unknown_requests"] == 0
    assert Decimal(summary["charged_usd"]) == Decimal("7.03574090")


def test_selected_generic_unknown_cannot_be_acknowledged_as_preprovider_rejection(migrated):
    current, _payload, parent = parent_case(migrated, unresolved="unknown")
    before = snapshot(migrated, parent)
    with pytest.raises(ValueError):
        current.create_continuation(parent, idempotency_key="not-a-preprovider-proof", authorize_new_requests=True,
            budget_limit_usd="15", resume_with_held_rejection_request_id="unresolved")
    assert snapshot(migrated, parent) == before
    assert current.get_run(parent)["recovery"]["successor_run_id"] is None
