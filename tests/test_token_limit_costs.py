"""128k policy: historical paid receipts and unchanged economic authorization.

Only the established temporary-DB fixtures and frozen provider are used. Run
through tools/testing/offline_pytest.py, which isolates before these imports.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import json
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.storage.trade_idea_store import BudgetBlocked, RunConflict, _digest
from test_trade_idea_pipeline import (
    FakeMessages, _call_kwargs, _gate, _priced_request,
    db_path, migrated, store, _save_resume_checkpoint,
)
from test_run_robustness import bb


NEW_CAP = 128000
LEGACY_CALLS = [
    ("specialist:quant", 16000),
    ("specialist:fundamentals", 64000),
    ("specialist:macro", 65536),
    ("capo", 64000),
    ("red_team", 65536),
    ("aux:preparation", 65536),
]


class FrozenProvider(FakeMessages):
    def __init__(self, cost="0.005", *, label="new", observer=None):
        super().__init__(cost)
        self.label, self.observer, self.arguments = label, observer, []

    def create(self, **kwargs):
        if self.observer:
            self.observer(kwargs)
        self.arguments.append(deepcopy(kwargs))
        response = super().create(**kwargs)
        response.id = self.label + "-response-" + str(self.calls)
        response.content = [SimpleNamespace(type="text", text="Frozen complete paid report.")]
        return response


def _payload(*, cap=NEW_CAP, budget="1"):
    payload = _priced_request(budget=budget)
    for metadata in payload["catalog_snapshot"]["models"].values():
        metadata.update(max_completion_tokens=cap, context_length=500000)
    return payload


def _arguments(role, cap):
    model_role = "specialist" if role.startswith("specialist:") else "aux" if role.startswith("aux:") else role
    return {**_call_kwargs(), "model": trade_idea.model_for_role(model_role), "max_tokens": cap}


def _cost_rows(path, run_id):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT * FROM trade_idea_costs WHERE run_id=? ORDER BY created_at,request_id", (run_id,))]


def _reuses(path, run_id):
    with sqlite3.connect(path) as conn:
        return [json.loads(row[0]) for row in conn.execute(
            "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (run_id,))]


def _legacy_parent_and_child(path, role, cap, *, accepted_cap=65536):
    current, payload = store(path), _payload(cap=accepted_cap)
    parent = current.create_run(payload, idempotency_key="legacy-parent")["run"]["id"]
    token = current.claim_run(parent)
    original = FrozenProvider(label="legacy")
    response = _gate(current, parent, payload).wrap_client(
        SimpleNamespace(messages=original), role=role).messages.create(**_arguments(role, cap))
    checkpoint = _save_resume_checkpoint(current, parent, token)
    current.finish_run(parent, token, None, "incomplete", reason="Frozen crash after paid response")
    child = current.create_continuation(parent, idempotency_key="legacy-child",
        authorize_new_requests=True)["run"]["id"]
    current.claim_run(child)
    return current, payload, parent, child, original, response, checkpoint


@pytest.mark.parametrize("role,legacy_cap", LEGACY_CALLS)
def test_128k_reuses_exact_legacy_ancestor_once_without_new_reservation(migrated, role, legacy_cap):
    current, payload, parent, child, original, paid, checkpoint = _legacy_parent_and_child(
        migrated, role, legacy_cap)
    prior_cost = _cost_rows(migrated, parent)
    provider = FrozenProvider(label="must-not-dispatch")
    gate = _gate(current, child, payload)
    # The original accepted catalog only allowed 65536. A received response
    # needs no new capacity/catalog permission, and must not alter that grant.
    gate.catalog_fetcher = lambda: pytest.fail("receipt replay must not fetch a new catalog")
    recovered = gate.wrap_client(SimpleNamespace(messages=provider), role=role).messages.create(
        **_arguments(role, NEW_CAP))
    assert recovered.id == paid.id and recovered.request_id == paid.request_id
    assert recovered.content[0].text == paid.content[0].text
    assert Decimal(str(recovered.usage.cost_usd)) == Decimal("0.005")
    assert original.calls == 1 and provider.calls == 0
    assert _cost_rows(migrated, parent) == prior_cost and _cost_rows(migrated, child) == []
    assert current.get_run(child)["cost"]["requests"] == 1
    assert Decimal(current.get_run(child)["cost"]["charged_usd"]) == Decimal("0.005")
    assert current.get_run(child)["progress"]["checkpoint"] == checkpoint
    assert _reuses(migrated, child) == [{"request_id": paid.request_id,
        "source_run_id": parent, "request_sha256": json.loads(prior_cost[0]["receipt_json"])["request_sha256"]}]
    # The same received call cannot be counted/replayed a second time in this child.
    assert gate._reuse(_arguments(role, NEW_CAP), role) is None
    assert len(_reuses(migrated, child)) == 1 and provider.calls == 0


@pytest.mark.parametrize("change", ["messages", "system", "tools", "thinking"])
def test_128k_compatibility_never_reuses_a_different_request(migrated, change):
    role = "specialist:quant"
    current, payload, parent, child, _, _, _ = _legacy_parent_and_child(migrated, role, 16000)
    arguments = _arguments(role, NEW_CAP)
    arguments[change] = {
        "messages": [{"role": "user", "content": "A different economic question"}],
        "system": "A new system contract",
        "tools": [{"name": "new_tool", "description": "New tool contract",
                   "input_schema": {"type": "object", "properties": {}}}],
        "thinking": {"type": "effort", "effort": "low"},
    }[change]
    provider = FrozenProvider(label="blocked-new-request")
    with pytest.raises(ValueError, match="max_tokens|reasoning"):
        _gate(current, child, payload).wrap_client(SimpleNamespace(messages=provider), role=role)\
            .messages.create(**arguments)
    assert provider.calls == 0 and _reuses(migrated, child) == []
    assert len(_cost_rows(migrated, parent)) == 1 and _cost_rows(migrated, child) == []


def test_changed_payload_with_capacity_is_a_new_measured_request(migrated):
    role = "specialist:quant"
    current, payload, parent, child, _, paid, _ = _legacy_parent_and_child(
        migrated, role, 16000, accepted_cap=NEW_CAP)
    prior_cost = _cost_rows(migrated, parent)
    arguments = _arguments(role, NEW_CAP)
    arguments["messages"] = [{"role": "user", "content": "Different frozen economic question"}]
    provider = FrozenProvider(cost="0.007", label="new-question")
    response = _gate(current, child, payload).wrap_client(SimpleNamespace(messages=provider), role=role)\
        .messages.create(**arguments)
    assert provider.calls == 1 and response.id != paid.id and response.request_id != paid.request_id
    assert _reuses(migrated, child) == [] and _cost_rows(migrated, parent) == prior_cost
    assert len(_cost_rows(migrated, child)) == 1
    assert Decimal(current.get_run(child)["cost"]["charged_usd"]) == Decimal("0.012")


@pytest.mark.parametrize("role,legacy_cap,target_cap", [
    ("specialist:quant", 32000, NEW_CAP),
    ("capo", 16000, NEW_CAP),
    ("red_team", 16000, NEW_CAP),
    ("aux:preparation", 64000, NEW_CAP),
    ("specialist:quant", 16000, NEW_CAP + 1),
])
def test_legacy_compatibility_is_limited_to_approved_role_and_cap_pairs(
        migrated, role, legacy_cap, target_cap):
    current, payload, parent, child, _, _, _ = _legacy_parent_and_child(migrated, role, legacy_cap)
    provider = FrozenProvider(label="must-not-reuse-or-dispatch")
    with pytest.raises(ValueError, match="max_tokens"):
        _gate(current, child, payload).wrap_client(SimpleNamespace(messages=provider), role=role)\
            .messages.create(**_arguments(role, target_cap))
    assert provider.calls == 0 and _reuses(migrated, child) == []
    assert len(_cost_rows(migrated, parent)) == 1 and _cost_rows(migrated, child) == []


@pytest.mark.parametrize("tamper", ["text", "hash", "identity", "cost"])
def test_128k_legacy_replay_rejects_tampered_receipt_without_dispatch(migrated, tamper):
    role = "specialist:quant"
    current, payload, parent, child, _, _, _ = _legacy_parent_and_child(migrated, role, 16000)
    receipt = json.loads(_cost_rows(migrated, parent)[0]["receipt_json"])
    if tamper == "text":
        receipt["response"]["content"][0]["text"] = "Altered paid output"
    elif tamper == "hash":
        receipt["response_sha256"] = "0" * 64
    else:
        if tamper == "identity":
            receipt["response"]["model"] = "different/model"
        else:
            receipt["response"]["usage"]["cost_usd"] = "0"
        receipt["response_sha256"] = _digest(receipt["response"])
    with sqlite3.connect(migrated) as conn:
        conn.execute("UPDATE trade_idea_costs SET receipt_json=? WHERE run_id=?",
            (json.dumps(receipt), parent))
    provider = FrozenProvider(label="must-not-dispatch")
    with pytest.raises(RunConflict, match="saved paid response"):
        _gate(current, child, payload).wrap_client(SimpleNamespace(messages=provider), role=role)\
            .messages.create(**_arguments(role, NEW_CAP))
    assert provider.calls == 0 and _cost_rows(migrated, child) == []
    assert _reuses(migrated, child) == []
    assert current.get_run(child)["cost"]["charged_usd"] == "0.005"


def test_128k_reservation_is_durable_and_larger_inside_original_budget(migrated):
    current, payload, observed = store(migrated), _payload(), []
    run_id = current.create_run(payload, idempotency_key="new-reservation")["run"]["id"]
    current.claim_run(run_id)
    with sqlite3.connect(migrated) as conn:
        grant_before = conn.execute("SELECT request_json,budget_limit_usd FROM trade_idea_runs WHERE id=?", (run_id,)).fetchone()
    def before_dispatch(arguments):
        row = _cost_rows(migrated, run_id)[-1]
        assert row["status"] == "reserved" and row["charged_usd"] is None
        body = trade_idea.costruisci_corpo(**arguments)
        input_bound = max(len(json.dumps(body, ensure_ascii=False).encode()),
                          len(json.dumps(body, ensure_ascii=True).encode()))
        expected = (Decimal(input_bound) * Decimal("0.000001") +
                    Decimal(arguments["max_tokens"]) * Decimal("0.000002")).quantize(
                        Decimal("0.000000001"), rounding=ROUND_CEILING)
        assert Decimal(row["reserved_usd"]) == expected
        observed.append((arguments["max_tokens"], expected))
    provider = FrozenProvider(observer=before_dispatch)
    messages = _gate(current, run_id, payload).wrap_client(
        SimpleNamespace(messages=provider), role="specialist:quant").messages
    for cap in (16000, NEW_CAP):
        messages.create(**_arguments("specialist:quant", cap))
    assert [cap for cap, _ in observed] == [16000, NEW_CAP]
    assert observed[1][1] > observed[0][1] and observed[1][1] < Decimal(payload["budget_limit_usd"])
    with sqlite3.connect(migrated) as conn:
        grant_after = conn.execute("SELECT request_json,budget_limit_usd FROM trade_idea_runs WHERE id=?", (run_id,)).fetchone()
    assert grant_after == grant_before
    summary = current.get_run(run_id)["cost"]
    assert summary["budget_limit_usd"] == "1" and summary["requests"] == 2
    assert Decimal(summary["charged_usd"]) == Decimal("0.01")
    assert Decimal(summary["remaining_after_holds_usd"]) == Decimal("0.99")


@pytest.mark.parametrize("fault", ["budget", "accepted_cap", "live_cap", "accepted_context",
    "live_context", "missing_live_catalog", "missing_live_cap", "catalog_error"])
def test_128k_infeasible_request_stops_before_reservation_and_dispatch(migrated, fault):
    current = store(migrated)
    payload = _payload(budget="0.1" if fault == "budget" else "1")
    live = deepcopy(payload["catalog_snapshot"])
    if fault == "accepted_cap":
        payload["catalog_snapshot"]["models"]["specialist"]["max_completion_tokens"] = 65536
    elif fault == "live_cap":
        live["models"]["specialist"]["max_completion_tokens"] = 65536
    elif fault == "accepted_context":
        payload["catalog_snapshot"]["models"]["specialist"]["context_length"] = NEW_CAP
    elif fault == "live_context":
        live["models"]["specialist"]["context_length"] = NEW_CAP
    elif fault == "missing_live_catalog":
        live = {}
    elif fault == "missing_live_cap":
        live["models"]["specialist"].pop("max_completion_tokens")
    run_id = current.create_run(payload, idempotency_key="infeasible")["run"]["id"]
    current.claim_run(run_id)
    gate, fetched = _gate(current, run_id, payload), []
    def catalog():
        fetched.append(True)
        if fault == "catalog_error":
            raise RuntimeError("Frozen catalog unavailable")
        return live
    gate.catalog_fetcher = catalog
    provider = FrozenProvider()
    with pytest.raises((ValueError, KeyError, BudgetBlocked, RuntimeError)):
        gate.wrap_client(SimpleNamespace(messages=provider), role="specialist:quant").messages.create(
            **_arguments("specialist:quant", NEW_CAP))
    assert fetched == [True] and provider.calls == 0
    assert _cost_rows(migrated, run_id) == []
    assert current.get_run(run_id)["cost"]["requests"] == 0


@pytest.mark.parametrize("state", ["unknown", "reserved"])
def test_128k_does_not_release_or_replace_an_older_uncertain_request(migrated, state):
    current, payload = store(migrated), _payload()
    run_id = current.create_run(payload, idempotency_key="uncertain")["run"]["id"]
    token = current.claim_run(run_id)
    gate, provider = _gate(current, run_id, payload), FrozenProvider(cost=None)
    messages = gate.wrap_client(SimpleNamespace(messages=provider), role="specialist:quant").messages
    if state == "unknown":
        with pytest.raises(RuntimeError, match="costo provider non disponibile"):
            messages.create(**_arguments("specialist:quant", 16000))
        assert provider.calls == 1
    else:
        gate._reserve(_arguments("specialist:quant", 16000), "specialist:quant")
        assert provider.calls == 0
    before = _cost_rows(migrated, run_id)
    _save_resume_checkpoint(current, run_id, token)
    if state == "reserved":
        # An active worker may legitimately hold several concurrent reserves.
        # The crash boundary is a stopped run with an unresolved old dispatch.
        current.finish_run(run_id, token, None, "incomplete", reason="Frozen crash after reservation")
        stopped = _cost_rows(migrated, run_id)
        assert len(stopped) == 1 and stopped[0]["status"] == "unknown"
        # Normal terminal reconciliation marks the old hold uncertain; it
        # retains the request identity, held amount and absence of a bill.
        assert {key: value for key, value in stopped[0].items()
                if key not in ("status", "reason", "updated_at")} == {
                    key: value for key, value in before[0].items()
                    if key not in ("status", "reason", "updated_at")}
        before = stopped
    with pytest.raises(BudgetBlocked if state == "unknown" else RunConflict,
                       match="unknown|worker claim lost"):
        messages.create(**_arguments("specialist:quant", NEW_CAP))
    assert _cost_rows(migrated, run_id) == before
    assert provider.calls == (1 if state == "unknown" else 0)
    if state == "unknown":
        current.finish_run(run_id, token, None, "incomplete", reason="Frozen unresolved request")
    with pytest.raises(BudgetBlocked, match="unresolved"):
        current.create_continuation(run_id, idempotency_key="must-not-continue", authorize_new_requests=True)
    assert _cost_rows(migrated, run_id) == before


@pytest.mark.parametrize("phase,agent,old_cap,new_cap", [
    ("reflection", "_reflection", 1000, 8000),
    ("action_extraction", "_action_table", 2100, 16000),
])
@pytest.mark.parametrize("outcome", ["received", "unknown"])
def test_aux_legacy_receipt_arriving_during_quote_prevents_a_second_reservation(
        tmp_path, phase, agent, old_cap, new_cap, outcome):
    from bellomberg.core import llm_client
    from bellomberg.core.request_journal import RequestBlocked
    from test_aux_token_upgrade import journal, client_with_receipts, request, rows
    original, upgraded, dispatches = journal(tmp_path), journal(tmp_path), []
    client = client_with_receipts(dispatches, fault="timeout" if outcome == "unknown" else None)
    frozen_metadata = upgraded.metadata
    arrived, lookups = {}, []

    def metadata(model):
        # Deterministic interleaving: the first lookup saw no old row, then
        # another journal finishes/reserves it before BEGIN IMMEDIATE.
        lookups.append(model)
        assert len(lookups) == 1
        if outcome == "unknown":
            with pytest.raises(llm_client.APIConnectionError):
                request(client, original, phase, agent, old_cap)
        else:
            arrived["response"] = request(client, original, phase, agent, old_cap)
        arrived["rows"] = rows(original)
        return frozen_metadata(model)

    upgraded.metadata = metadata
    if outcome == "unknown":
        with pytest.raises(RequestBlocked, match="unresolved"):
            request(client, upgraded, phase, agent, new_cap)
        assert upgraded.summary()["cost_usd"] is None
        assert upgraded.summary()["reserved_usd"] > 0
    else:
        response = request(client, upgraded, phase, agent, new_cap)
        assert response.request_id == arrived["response"].request_id and response.replayed
        assert upgraded.summary()["cost_usd"] == 0.00003
    assert len(dispatches) == 1 and dispatches[0]["max_tokens"] == old_cap
    assert len(lookups) == 1 and rows(upgraded) == arrived["rows"]
    assert upgraded.summary()["request_count"] == 1


@pytest.mark.parametrize("old_cap", [16000, 65536])
@pytest.mark.parametrize("tamper", ["response", "cost", "body", "seal"])
def test_preparer_128k_alias_does_not_hide_corrupt_original_evidence(tmp_path, old_cap, tamper):
    from test_auxiliary_output_caps import preparer
    calls = []
    def provider(**arguments):
        calls.append(deepcopy(arguments))
        return SimpleNamespace(id="original-preparer", model="synthetic/model", provider="frozen",
            stop_reason="end_turn", usage=SimpleNamespace(cost_usd=0.02),
            content=[SimpleNamespace(type="text", text='{"approved":false}')])
    path, dossier = tmp_path / "preparer.sqlite", {"ticker": "SYNTH", "evidence": "frozen"}
    old = preparer(path, old_cap, provider)
    assert old(dossier, {}) == {"approved": False}
    with old._db() as db:
        row = db.execute("SELECT * FROM requests").fetchone()
        if tamper == "response":
            db.execute("UPDATE requests SET response=?", ('{"approved":true}',))
        elif tamper == "cost":
            db.execute("UPDATE requests SET cost=0")
        elif tamper == "body":
            body = json.loads(row["request"])
            body["max_tokens"] = old_cap + 1
            db.execute("UPDATE requests SET request=?", (json.dumps(body),))
        else:
            receipt = json.loads(row["receipt"])
            receipt.pop("response_sha256")
            db.execute("UPDATE requests SET receipt=?", (json.dumps(receipt),))
    with old._db() as db:
        before = [dict(row) for row in db.execute("SELECT * FROM requests")]
    upgraded = preparer(path, NEW_CAP, provider)
    upgraded.metadata = lambda _: pytest.fail("corrupt legacy evidence triggered a new quote")
    for _ in range(2):
        with pytest.raises(ValueError, match="receipt|response|cost|key|legacy"):
            upgraded(dossier, {})
    with upgraded._db() as db:
        assert [dict(row) for row in db.execute("SELECT * FROM requests")] == before
    assert len(calls) == 1 and calls[0]["max_tokens"] == old_cap


@pytest.mark.parametrize("old_cap", [16000, 64000, 65536])
def test_remaining_floor_does_not_replace_a_native_ready_legacy_contract(
        bb, migrated, monkeypatch, old_cap):
    from test_specialist_token_upgrade import (
        actor, legacy_checkpoint, Crash, REPORT, _FakeClient, _tool_resp, _text_resp,
    )
    first = _FakeClient(lambda n, kw: _tool_resp())
    original = actor(bb, monkeypatch, first, old_cap, "trade_idea")
    tools = []
    monkeypatch.setattr(original, "_execute_meta_tool", lambda *args: tools.append(args) or {"ok": True})

    def crash_after_durable_tool(event, payload):
        if event == "specialist_turn":
            raise Crash("Frozen crash after the native READY checkpoint")

    bb.persist_run_checkpoint = crash_after_durable_tool
    with pytest.raises(Crash):
        original.run(1)
    legacy_checkpoint(bb)
    saved = deepcopy(bb.specialist_checkpoints)
    assert saved["quant:R1"]["status"] == "ready"
    assert "max_tokens" not in saved["quant:R1"]

    current, payload = store(migrated), _payload(budget="0.12")
    run_id = current.create_run(payload, idempotency_key="legacy-ready-floor")["run"]["id"]
    current.claim_run(run_id)
    bb.budget_gate = _gate(current, run_id, payload)
    summary = trade_idea._remaining_work(bb)
    assert len(first.calls) == len(tools) == 1
    assert _cost_rows(migrated, run_id) == []
    assert bb.specialist_checkpoints == saved

    # Isolate the summary branch where the sole remaining contract is saved.
    # These status flags are a view fixture, not an executed committee order.
    # ZR 05/10 (Z4): la vista gira in modalita' RESEARCH, l'unica che una run Trade Idea puo' avere
    # dal 03/10 (1326312: nuove run solo research, prosecuzione legacy 409). In legacy la tappa
    # model_authoring (workbook verificato) resta aperta per sempre: e' codice dietro excel_archived.
    # La garanzia provata qui (il contratto SALVATO non prende il floor della nuova policy) e'
    # indipendente dalla modalita'.
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    progress = {**deepcopy(bb.data), "_completed_stages": {
        f"{desk}:{round_n}": {"status": "complete"}
        for round_n in range(3) for desk in trade_idea.TRADE_IDEA_DESKS
        if (desk, round_n) != ("quant", 1)},
        "_research_thesis": {"status": "sealed"},
        "_red_research_review": {"status": "complete"}, "_capo_completed": True}
    only_saved = SimpleNamespace(**{**vars(bb), "data": progress, "analysis_mode": RESEARCH_ANALYSIS_MODE})
    saved_summary = trade_idea._remaining_work(only_saved)
    assert saved_summary["remaining"] == ["quant:R1"]
    assert saved_summary["largest_output_reservation_floor_usd"] is None
    assert saved_summary["feasibility"] == "not_guaranteed"
    assert saved_summary["completion_guaranteed"] is False
    assert len(first.calls) == 1 and _cost_rows(migrated, run_id) == []
    assert bb.specialist_checkpoints == saved

    # Only the native full-contract validation can determine this saved cap.
    # The summary itself must neither replay the tool nor invoke a provider.
    later = _FakeClient(lambda n, kw: _text_resp(REPORT))
    recovered = actor(bb, monkeypatch, later, NEW_CAP, "trade_idea")
    monkeypatch.setattr(recovered, "_execute_meta_tool",
                        lambda *args: pytest.fail("The saved tool cannot run again"))
    assert recovered.run(1) == REPORT
    assert len(later.calls) == 1 and later.calls[0]["max_tokens"] == old_cap

    floors = {row["stage"]: row for row in summary["reservation_floors"]}
    assert floors["quant:R1"]["max_output_tokens"] is None
    assert floors["quant:R1"]["output_reservation_floor_usd"] is None
    assert floors["quant:R1"]["basis"] == "saved_contract_pending_validation"
    # New stages still require the new cap, even with a historical stage saved.
    assert floors["macro:R1"]["max_output_tokens"] == NEW_CAP
    assert Decimal(floors["macro:R1"]["output_reservation_floor_usd"]) == Decimal("0.256")
    assert summary["feasibility"] == "insufficient_even_before_input"
    assert summary["completion_guaranteed"] is False
