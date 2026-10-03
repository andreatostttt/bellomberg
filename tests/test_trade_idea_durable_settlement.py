"""A paid response is settled even when SQLite is briefly busy; the provider is never re-called."""
import sqlite3

import pytest

from bellomberg.agents import trade_idea
from test_trade_idea_store import db_path, migrated  # noqa: F401


def _gate():
    return trade_idea.TradeIdeaBudgetGate.__new__(trade_idea.TradeIdeaBudgetGate)


def test_busy_database_is_retried_for_the_write_only(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    calls = []

    def settle(run_id, request_id, **kwargs):
        calls.append((run_id, request_id, kwargs))
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "settled"

    assert _gate()._durable(settle, "run", "req", charged_usd="0.40") == "settled"
    assert len(calls) == 3 and all(call == ("run", "req", {"charged_usd": "0.40"}) for call in calls)


def test_other_database_errors_and_persistent_locks_are_not_hidden(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: trade_idea_costs")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _gate()._durable(broken)
    attempts = []
    def locked(*args, **kwargs):
        attempts.append(1)
        raise sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        _gate()._durable(locked)
    assert len(attempts) == 6


def test_reconcile_routes_the_settlement_through_the_durable_write(monkeypatch):
    gate = _gate()
    gate.run_id, gate._requests = "run", {"req": {"request_sha256": "a" * 64}}
    seen = []
    gate._durable = lambda write, *args, **kwargs: seen.append(write.__name__)
    class Store:
        def reconcile_cost(self, *args, **kwargs):
            raise AssertionError("must go through _durable")
        mark_cost_unknown = reconcile_cost
    gate.store = Store()
    class Usage:
        cost_usd = "0.40"
        def to_dict(self):
            return {"cost_usd": "0.40", "input_tokens": 10, "output_tokens": 5}
    class Response:
        id, model, stop_reason, content, usage = "resp-1", "m", "end_turn", [], Usage()
    gate._reconcile("req", Response(), "m")
    assert seen == ["reconcile_cost"]


def test_ambiguous_create_failure_blocks_further_spending_in_the_same_run(migrated):
    from bellomberg.storage.trade_idea_store import BudgetBlocked
    from test_trade_idea_paid_capo_checkpoint import case, v2_request, save_cp
    from test_trade_idea_policy_runtime import gate
    current, _, cp, *_ = case(migrated)
    ident = current.create_run(v2_request(), idempotency_key='ambiguous-create')['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    budget = gate(current, ident, token)
    kwargs = {'model': trade_idea.model_for_role('capo'), 'max_tokens': 32768,
              'thinking': {'type': 'effort', 'effort': 'low'}, 'system': 'S',
              'messages': [{'role': 'user', 'content': 'U'}]}
    class Inner:
        def create(self, **call):
            raise ConnectionError("connection reset after the request was sent")
    messages = trade_idea._BudgetedMessages(Inner(), budget, 'capo')
    with pytest.raises(ConnectionError):
        messages.create(**kwargs)
    import sqlite3 as _sql
    with _sql.connect(migrated) as conn:
        states = [row[0] for row in conn.execute('SELECT status FROM trade_idea_costs WHERE run_id=?', (ident,))]
    assert states == ['unknown'], states
    with pytest.raises((BudgetBlocked, ValueError)):
        budget._reserve({**kwargs, 'messages': [{'role': 'user', 'content': 'Another call'}]}, 'capo')
