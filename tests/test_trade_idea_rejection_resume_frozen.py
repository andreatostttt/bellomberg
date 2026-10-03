"""Frozen UBER continuation: native store/checkpoint/wire, stopped before dispatch.

Only the external read-only snapshots are read. All SQLite operations are in
the offline harness sandbox; no source archive or personal database is opened.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from test_trade_idea_store import db_path, migrated


DELIVERY = Path("2026-10-02-uber-native-continuation")
REQUEST_ID = "4084b7a4027a4dc3b71676a45b9f6ed4"
FROZEN = {
    "original": (Path("2026-10-02-agent-source-research/"
                      "live-b7b3a6e4-4de1-464c-b95e-dec4b38c621f/esito-live-preservato.json"),
                 "46171f2c2becef77c493347fd76c6e72123390d7acd27a5a11c30006d2686d52"),
    "first_child": (DELIVERY / "live-followup/terminal-native-frozen.json",
                    "6996a7027294aed49fc87abdc6ff2938d5dd76c4b7d702262b6dcb516be83be7"),
    "terminal": (DELIVERY / "live-model-completion/terminal-native-frozen-1cab-172142.json",
                 "2a9fcc363495892f9d36bba88b0abd1bd6bacd0728ef53bf35a9da0d47f15ab0"),
    "events": (DELIVERY / "live-model-completion/RECONCILIATION_EVIDENCE_RO_402.json",
               "4f154eade2eeb3ae9396a4ee16b2a8315044dc7b37832318327c3600d31dc276"),
    "mandate": (DELIVERY / "frozen/mandato_pm.json",
                "0700e1f053fee5257786143a5eda4713a4f017984a3a5c11e012ea5a390d11b3"),
}


def _insert(conn, table, row):
    columns = list(row)
    conn.execute(f"INSERT INTO {table} (" + ",".join(columns) + ") VALUES ("
                 + ",".join("?" for _ in columns) + ")", [row[key] for key in columns])


def _cost_rows(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return {row["request_id"]: dict(row) for row in conn.execute("SELECT * FROM trade_idea_costs")}


@pytest.fixture
def frozen_case(migrated, monkeypatch):
    configured = os.environ.get("BELLOMBERG_TEST_PRIVATE_FIXTURE_ROOT")
    if not configured:
        pytest.skip("Private frozen fixtures not configured: BELLOMBERG_TEST_PRIVATE_FIXTURE_ROOT")
    frozen_paths = {name: (Path(configured) / path, expected)
                    for name, (path, expected) in FROZEN.items()}
    if not all(path.exists() for path, _ in frozen_paths.values()):
        pytest.skip("External frozen UBER evidence unavailable; synthetic receipt/store tests remain independent")
    from bellomberg.core import mandato_pm
    from bellomberg.storage.trade_idea_store import TradeIdeaStore

    loaded = {}
    for name, (path, expected) in frozen_paths.items():
        raw = path.read_bytes()
        assert sha256(raw).hexdigest() == expected, name
        loaded[name] = json.loads(raw.decode("utf-8-sig"))
    terminal = loaded["terminal"]
    assert len(terminal["costs"]) == 119
    context = json.loads(terminal["run"]["context_json"])
    mandate = loaded["mandate"]
    assert mandato_pm.impronta(mandate) == context["mandate_sha256"]
    with sqlite3.connect(migrated) as conn:
        for name in ("original", "first_child", "terminal"):
            _insert(conn, "trade_idea_runs", loaded[name]["run"])
        # The terminal snapshot contains the original rows of the entire chain.
        for row in terminal["costs"]:
            _insert(conn, "trade_idea_costs", row)
        for row in loaded["events"]["events"]:
            _insert(conn, "trade_idea_events", row)
    current = TradeIdeaStore(migrated, mandate_loader=lambda: mandato_pm.impronta(mandate))
    # This proof fixes the accepted context; it does not read today's book or
    # assert current prices. The inherited final price refresh gate stays real.
    monkeypatch.setattr(current, "_context", lambda *_a, **_kw:
                        (deepcopy(context["book"]), deepcopy(context["feedback"])))
    monkeypatch.setattr(mandato_pm, "carica", lambda *_a, **_kw: deepcopy(mandate))
    return SimpleNamespace(store=current, path=migrated, snapshots=loaded, terminal=terminal,
                           parent=terminal["run"]["id"], old_costs=_cost_rows(migrated),
                           frozen_paths=frozen_paths)


def _continue(case, key):
    return case.store.create_continuation(case.parent, idempotency_key=key,
        authorize_new_requests=True, budget_limit_usd="15",
        resume_with_held_rejection_request_id=REQUEST_ID)


def _assert_originals(case):
    actual = _cost_rows(case.path)
    assert {key: actual[key] for key in case.old_costs} == case.old_costs
    with sqlite3.connect(case.path) as conn:
        conn.row_factory = sqlite3.Row
        for name in ("original", "first_child", "terminal"):
            row = case.snapshots[name]["run"]
            assert dict(conn.execute("SELECT * FROM trade_idea_runs WHERE id=?", (row["id"],)).fetchone()) == row
    for path, expected in case.frozen_paths.values():
        assert sha256(path.read_bytes()).hexdigest() == expected


def test_frozen_held_rejection_resumes_exact_iteration12_without_dispatch(frozen_case, monkeypatch, tmp_path):
    from bellomberg.agents import trade_idea as trade
    from bellomberg.agents.specialists import base
    from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist
    from bellomberg.core.language import language_context
    from bellomberg.storage.trade_idea_store import BudgetBlocked

    case = frozen_case
    with pytest.raises(BudgetBlocked, match="unresolved"):
        case.store.create_continuation(case.parent, idempotency_key="no-held-consent",
            authorize_new_requests=True, budget_limit_usd="15")
    child_detail = _continue(case, "frozen-iteration12-held")
    child = child_detail["run"]["id"]
    assert _continue(case, "frozen-iteration12-held")["run"]["id"] == child
    cost = child_detail["cost"]
    assert cost["unknown_requests"] == cost["held_unknown_requests"] == 1
    assert cost["unacknowledged_unknown_requests"] == 0
    assert cost["remaining_known_usd"] is None
    assert Decimal(cost["unknown_reserved_usd"]) == Decimal("1.54843625")
    assert Decimal(cost["charged_usd"]) == Decimal("6.93574090")
    assert Decimal(cost["remaining_after_holds_usd"]) == Decimal("6.51582285")
    assert Decimal(cost["budget_limit_usd"]) == Decimal("15")
    assert cost["chain_run_ids"] == case.terminal["chain_run_ids"] + [child]
    assert cost["requests"] == 119
    payload = case.store.get_accepted_request(child)
    token = case.store.claim_run(child)
    progress = json.loads(case.terminal["run"]["progress_json"])
    checkpoint = progress["checkpoint"]
    origin = checkpoint["specialist_checkpoints"]["fundamentals:R1"]
    task = {"kind": "model_authoring_completion", "version": 1,
            "origin_checkpoint_sha256": origin["sha256"]}
    key = "fundamentals:R1:" + base._checkpoint_digest(task)
    saved = checkpoint["specialist_checkpoints"][key]
    assert saved["status"] == "ready" and saved["iteration"] == 12
    assert not saved["pending_tools"] and not saved["inflight_tools"]
    assert not checkpoint["valuation_results"]
    counts = {"provider": 0, "catalog": 0, "source": 0, "dispatch_boundary": 0}

    def forbidden(kind):
        def call(*_args, **_kwargs):
            counts[kind] += 1
            pytest.fail("Frozen proof crossed forbidden boundary: " + kind)
        return call

    board = base.Blackboard(heartbeat_path=tmp_path / "heartbeat.json", run_scope="trade_idea",
        run_id=child, target_ticker=payload["ticker"], pm_view=payload["view_text"])
    board.current_round, board.model_phase, board.independent_round = 1, "building", 0
    board.model_roots = []
    board.r2_specialists = set(trade.TRADE_IDEA_DESKS)
    board.source_admission = deepcopy(payload["source_qualification"])
    # Only the frozen projection is needed at the wire boundary. This test does
    # not certify a source revalidation and never opens the personal archive.
    board.source_qualification = deepcopy(progress["source_qualification"])
    board.company_source_session = forbidden("source")
    board.budget_gate = trade.TradeIdeaBudgetGate(case.store, child, token,
        payload["catalog_snapshot"], catalog_fetcher=forbidden("catalog"))
    trade._configure_native_recovery(board, case.store, token, inherited=child_detail["progress"])
    board.raise_if_run_blocked()
    assert board.specialist_checkpoints[key] == saved
    assert board.data["_model_input_draft"] == checkpoint["data"]["_model_input_draft"]
    assert board.data["_model_consultations"] == checkpoint["data"]["_model_consultations"]
    last = case.store.specialist_response_receipts(child, "fundamentals", origin["usage"])[-1]
    messages = deepcopy(origin["messages"])
    messages.extend([{"role": "assistant", "content": deepcopy(last["response"]["content"])},
                     {"role": "user", "content": "Unused new context: saved task must remain exact."}])
    actor = FundamentalsSpecialist(board,
        client=SimpleNamespace(messages=SimpleNamespace(create=forbidden("provider"))))
    actor._trade_idea_author_history = {"genuine_history": True, "native_model_completion": True,
        "origin": deepcopy(origin), "last_receipt": deepcopy(last),
        "source_fingerprint": board.source_qualification["fingerprint"],
        "messages": messages, "messages_sha256": trade._plan_digest(messages)}

    class BeforeDispatch(BaseException):
        pass

    observed = {}
    def stop_before_reservation(kwargs, role):
        counts["dispatch_boundary"] += 1
        board.raise_if_run_blocked()
        assert role == "specialist:fundamentals"
        assert kwargs["messages"] == saved["messages"]
        assert kwargs["max_tokens"] == saved["max_tokens"] == 128000
        pricing = payload["catalog_snapshot"]["models"]["specialist"]["pricing"]
        wire = trade.costruisci_corpo(**{**kwargs,
            "messages": trade._stable_provider_messages(kwargs["messages"]),
            "provider_max_price": {"prompt": float(Decimal(pricing["prompt"]) * 10**6),
                "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}})
        digest = trade._plan_digest(wire)
        original_receipt = json.loads(case.old_costs[REQUEST_ID]["receipt_json"])
        assert digest == original_receipt["request_sha256"]
        observed.update(request_sha256=digest, task_key=key, iteration=saved["iteration"],
                        restored_messages=len(kwargs["messages"]))
        raise BeforeDispatch()

    monkeypatch.setattr(board.budget_gate, "_reserve", stop_before_reservation)
    with language_context(payload["language"]), pytest.raises(BeforeDispatch):
        actor.run(1, task_context=task, publish_report=False)
    assert counts == {"provider": 0, "catalog": 0, "source": 0, "dispatch_boundary": 1}
    assert len(_cost_rows(case.path)) == 119
    _assert_originals(case)
    (tmp_path / "frozen-held-wire-proof.json").write_text(json.dumps({
        "run_id": child, "parent_run_id": case.parent, "old_cost_rows_preserved": 119,
        "cost": cost, "observed": observed, "calls": counts,
        "scope": "Offline native store/checkpoint/wire; stopped before reservation and provider dispatch",
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def test_frozen_selected_hold_never_authorizes_a_new_unknown_request(frozen_case):
    from bellomberg.storage.trade_idea_store import BudgetBlocked

    case = frozen_case
    child = _continue(case, "frozen-held-new-unknown")["run"]["id"]
    token = case.store.claim_run(child)
    model = json.loads(case.terminal["run"]["models_json"])["specialist"]["model"]
    assert case.store.reserve_cost(child, "offline-new-reservation", "specialist:fundamentals", model,
        ".10", request_sha256="a" * 64, worker_token=token) is True
    case.store.mark_cost_unknown(child, "offline-new-reservation", reason="Simulated missing provider receipt")
    summary = case.store.get_run(child)["cost"]
    assert summary["unknown_requests"] == 2 and summary["held_unknown_requests"] == 1
    assert summary["unacknowledged_unknown_requests"] == 1
    with pytest.raises(BudgetBlocked, match="unresolved|unknown"):
        case.store.reserve_cost(child, "offline-must-not-reserve", "specialist:fundamentals", model,
            ".10", request_sha256="b" * 64, worker_token=token)
    assert "offline-must-not-reserve" not in _cost_rows(case.path)
    assert _cost_rows(case.path)[REQUEST_ID]["status"] == "unknown"
    _assert_originals(case)
