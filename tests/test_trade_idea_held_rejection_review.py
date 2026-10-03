"""Independent narrow review of held-cost authorization, on disposable databases."""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

import pytest

from bellomberg.storage.trade_idea_store import RunConflict, _digest
from test_trade_idea_budget_amendment import (
    db_path, migrated, held_parent, rejected, snapshot,
)
from test_trade_idea_store import result


def accepted_child(current, parent):
    return current.create_continuation(parent, idempotency_key="independent-held-child",
        authorize_new_requests=True, budget_limit_usd="15",
        resume_with_held_rejection_request_id="held-request")


def test_held_unknown_still_prevents_false_terminal_completion(held_parent, migrated):
    current, payload, parent = held_parent
    original = snapshot(migrated, parent)
    child = accepted_child(current, parent)
    ident = child["run"]["id"]
    token = current.claim_run(ident)
    final = current.finish_run(ident, token, result(payload["ticker"]), "completed")
    assert final["run"]["technical_status"] == "incomplete"
    assert final["cost"]["unknown_requests"] == 1
    assert final["cost"]["unacknowledged_unknown_requests"] == 0
    assert final["cost"]["remaining_known_usd"] is None
    assert Decimal(final["cost"]["unknown_reserved_usd"]) == Decimal("1.548436250")
    assert Decimal(final["cost"]["remaining_after_holds_usd"]) == Decimal("6.515822850")
    assert snapshot(migrated, parent) == original


@pytest.mark.parametrize("mutation", ["request_id", "reservation", "proof", "original_receipt"])
def test_resealed_held_descriptor_cannot_change_its_native_evidence(held_parent, migrated, mutation):
    current, _payload, parent = held_parent
    original = snapshot(migrated, parent)
    child = accepted_child(current, parent)
    ident = child["run"]["id"]
    request = deepcopy(current.get_accepted_request(ident))
    held = request["continuation"]["held_rejection"]
    if mutation == "request_id":
        held["request_id"] = "previous-paid"
    elif mutation == "reservation":
        held["reserved_usd"] = "0"
    elif mutation == "proof":
        held["proof"]["transport_http_status"] = 402
    else:
        held["original_cost_row"]["receipt_json"] = "{}"
    with sqlite3.connect(migrated) as conn:
        conn.execute("DROP TRIGGER trade_idea_run_input_immutable")
        conn.execute("UPDATE trade_idea_runs SET request_json=?,request_sha256=? WHERE id=?",
                     (json.dumps(request), _digest(request), ident))
    with pytest.raises(RunConflict):
        current.get_run(ident)
    assert snapshot(migrated, parent) == original


def test_failure_after_proof_build_keeps_hold_and_creates_no_successor(held_parent, migrated):
    current, _payload, parent = held_parent
    original = snapshot(migrated, parent)
    with sqlite3.connect(migrated) as conn:
        before = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                  for table in ("trade_idea_runs", "trade_idea_costs", "trade_idea_events")}
        conn.execute("UPDATE cash_state SET balance_cents=balance_cents+100,version=version+1")
    with pytest.raises(RunConflict, match="book, feedback or mandate"):
        accepted_child(current, parent)
    assert snapshot(migrated, parent) == original
    with sqlite3.connect(migrated) as conn:
        after = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                 for table in before}
    assert after == before
