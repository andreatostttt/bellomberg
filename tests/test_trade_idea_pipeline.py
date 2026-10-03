"""Trade Idea pipeline gates, exercised with synthetic providers and a temporary DB."""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.language import language_context
from bellomberg.storage.trade_idea_store import BudgetBlocked
from test_trade_idea_store import (db_path, migrated, request, store, result as store_result,
                                  all_checks, _save_resume_checkpoint)
from trade_idea_evolution_fixtures import (DESKS, MODEL_META_TOOLS, committee_review,
                                         model_build_tool_blocks, review_tool_block)
from test_trade_idea_live_consultation import board
from test_trade_idea_committee_model_context import model_board


@pytest.fixture(autouse=True)
def frozen_usage_fx(monkeypatch):
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "_fx_usd_to_eur", lambda: (0.9, "offline_frozen_fx"))


def _priced_request(*, budget="5"):
    payload = request()
    payload["budget_limit_usd"] = budget
    for row in payload["catalog_snapshot"]["models"].values():
        row.update(context_length=500000, max_completion_tokens=128000,
                   reasoning_effort="max", tools=True,
                   pricing={"prompt": "0.000001", "completion": "0.000002"})
    return payload


class FakeMessages:
    def __init__(self, cost):
        self.cost = cost
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(cost_usd=self.cost,
                                to_dict=lambda: {"cost_usd": self.cost})
        return SimpleNamespace(id="synthetic-response", model=kwargs["model"],
                               stop_reason="end_turn", usage=usage)


def _gate(s, run_id, payload):
    with sqlite3.connect(s.db_path) as conn:
        token = conn.execute("SELECT worker_token FROM trade_idea_runs WHERE id=?", (run_id,)).fetchone()[0]
    return trade_idea.TradeIdeaBudgetGate(s, run_id, token,
        payload["catalog_snapshot"], catalog_fetcher=lambda: payload["catalog_snapshot"])


def _call_kwargs():
    return {"model": trade_idea.model_for_role("specialist"),
            "max_tokens": 100, "thinking": {"type": "effort", "effort": "max"},
            "messages": [{"role": "user", "content": "Offline provider test"}]}


def test_preflight_missing_budget_never_calls_provider():
    touched = []
    catalogue = _priced_request()["catalog_snapshot"]
    result = trade_idea.preflight_trade_idea("TEST", "", "manual", None,
        catalog_fetcher=lambda: catalogue,
        identity_resolver=lambda ticker: {"ticker": ticker, "name": "Synthetic",
            "exchange": "XNAS", "currency": "USD", "status": "confirmed", "reason": None},
        active_checker=lambda: False, mandate_loader=lambda: {},
        valuation_runtime_factory=lambda: SimpleNamespace(status=lambda: {"status": "disabled"}),
        key_checker=lambda: touched.append("configuration-only"))
    assert result["ok"] is False
    assert result["budget"]["status"] == "missing"
    assert result["budget"]["estimated_cost_usd"] is None
    assert touched == ["configuration-only"]


def test_exhausted_budget_blocks_before_provider_create(migrated):
    s = store(migrated)
    payload = _priced_request(budget="0.000001")
    run_id = s.create_run(payload, idempotency_key="exhausted")["run"]["id"]
    s.claim_run(run_id)
    fake = FakeMessages("0.000001")
    gate = _gate(s, run_id, payload)
    with pytest.raises(BudgetBlocked):
        gate.wrap_client(SimpleNamespace(messages=fake), role="specialist:fundamentals")\
            .messages.create(**_call_kwargs())
    assert fake.calls == 0
    assert s.get_run(run_id)["cost"]["requests"] == 0


def test_missing_provider_cost_blocks_every_later_request(migrated):
    s = store(migrated)
    payload = _priced_request()
    run_id = s.create_run(payload, idempotency_key="unknown-billing")["run"]["id"]
    s.claim_run(run_id)
    fake = FakeMessages(None)
    budgeted = _gate(s, run_id, payload).wrap_client(
        SimpleNamespace(messages=fake), role="specialist:fundamentals")
    with pytest.raises(RuntimeError, match="costo provider non disponibile"):
        budgeted.messages.create(**_call_kwargs())
    assert s.get_run(run_id)["cost"]["unknown_requests"] == 1
    with pytest.raises(BudgetBlocked):
        budgeted.messages.create(**_call_kwargs())
    assert fake.calls == 1


@pytest.mark.parametrize("mutation", [None, "text", "unsealed", "identity", "cost"])
def test_paid_response_before_checkpoint_is_reused_once_with_verified_receipt(migrated, mutation):
    from bellomberg.storage.trade_idea_store import RunConflict, _digest
    s, payload = store(migrated), _priced_request()
    parent = s.create_run(payload, idempotency_key="paid-response-parent")["run"]["id"]
    token = s.claim_run(parent)
    original = FakeMessages("0.0001")
    received = _gate(s, parent, payload).wrap_client(SimpleNamespace(messages=original),
        role="specialist:macro").messages.create(**_call_kwargs())
    _save_resume_checkpoint(s, parent, token)
    s.finish_run(parent, token, None, "incomplete", reason="Process stopped before response checkpoint")
    child = s.create_continuation(parent, idempotency_key="paid-response-resume", authorize_new_requests=True)["run"]["id"]
    if mutation:
        with sqlite3.connect(migrated) as conn:
            encoded = conn.execute("SELECT receipt_json FROM trade_idea_costs WHERE run_id=?", (parent,)).fetchone()[0]
            receipt = json.loads(encoded)
            if mutation == "text":
                receipt["response"]["content"] = [{"type": "text", "text": "Tampered paid output"}]
            elif mutation == "unsealed":
                receipt.pop("response_sha256")
            elif mutation == "identity":
                receipt["response"]["model"] = "other-model"
                receipt["response_sha256"] = _digest(receipt["response"])
            else:
                receipt["response"]["usage"]["cost_usd"] = "0"
                receipt["response_sha256"] = _digest(receipt["response"])
            conn.execute("UPDATE trade_idea_costs SET receipt_json=? WHERE run_id=?", (json.dumps(receipt), parent))
    s.claim_run(child)
    extra = FakeMessages("0.001")
    messages = _gate(s, child, payload).wrap_client(SimpleNamespace(messages=extra), role="specialist:macro").messages
    if mutation:
        with pytest.raises(RunConflict, match="saved paid response"):
            messages.create(**_call_kwargs())
    else:
        replay = messages.create(**_call_kwargs())
        assert replay.id == received.id and replay.request_id == received.request_id
        assert replay.usage.request_id == received.request_id
        assert str(replay.usage.cost_usd) == "0.0001"
    assert extra.calls == 0 and original.calls == 1
    assert s.get_run(child)["cost"]["requests"] == 1
    assert s.get_run(child)["cost"]["charged_usd"] == "0.0001"


@pytest.mark.parametrize("fault", ["missing_cost", "partial_transport", "malformed_partial"])
def test_unknown_provider_receipt_preserves_output_and_original_hold(migrated, fault):
    from bellomberg.core.llm_client import APIConnectionError
    s, payload = store(migrated), _priced_request()
    run_id = s.create_run(payload, idempotency_key="unknown-output")["run"]["id"]
    s.claim_run(run_id)
    class PartialMessages(FakeMessages):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if fault == "missing_cost":
                response.content = [SimpleNamespace(type="text", text="Paid output without billing receipt")]
                return response
            error = APIConnectionError("stream lost after visible output")
            error.partial_response = {"id": "partial-provider", "model": kwargs["model"],
                "headers": {"Authorization": "SECRET_CANARY"}, "api_key": "SECRET_CANARY",
                "choices": [{"finish_reason": None, "message": {"role": "assistant",
                    "content": "Partial paid output"}}]}
            if fault == "malformed_partial":
                error.partial_response["usage"] = {"cost": float("nan")}
                error.partial_response["choices"][0]["message"]["tool_calls"] = [{"function": None}]
            raise error
    fake = PartialMessages(None)
    messages = _gate(s, run_id, payload).wrap_client(SimpleNamespace(messages=fake), role="specialist:macro").messages
    with pytest.raises((RuntimeError, APIConnectionError)):
        messages.create(**_call_kwargs())
    with sqlite3.connect(migrated) as conn:
        status, reserve, receipt_text = conn.execute("SELECT status,reserved_usd,receipt_json FROM trade_idea_costs WHERE run_id=?",
            (run_id,)).fetchone()
    receipt = json.loads(receipt_text)
    assert "SECRET_CANARY" not in receipt_text
    if fault == "malformed_partial":
        assert receipt["partial_response"]["usage"]["cost"] == {"invalid_numeric": "nan"}
        assert receipt["partial_response"]["choices"][0]["message"]["tool_calls"][0]["malformed_function_type"] == "NoneType"
    assert (receipt["response"]["content"][0]["text"] if fault == "missing_cost" else
            receipt["partial_response"]["choices"][0]["message"]["content"])
    assert status == "unknown" and Decimal(reserve) > 0
    with pytest.raises(BudgetBlocked):
        messages.create(**_call_kwargs())
    assert fake.calls == 1
    assert s.get_run(run_id)["cost"]["remaining_known_usd"] is None


@pytest.mark.parametrize("budget", ["0.12", "1"])
def test_remaining_work_discloses_native_reservations_without_promising_completion(migrated, budget):
    s, payload = store(migrated), _priced_request(budget=budget)
    run_id = s.create_run(payload, idempotency_key="remaining-work")["run"]["id"]
    s.claim_run(run_id)
    gate = _gate(s, run_id, payload)
    board = SimpleNamespace(data={}, budget_gate=gate, run_scope="trade_idea", model_phase="research",
                            target_ticker=payload["ticker"])
    remaining = trade_idea._remaining_work(board)
    assert remaining["estimated_remaining_usd"] is None
    assert remaining["completion_guaranteed"] is False and remaining["input_cost_included"] is False
    assert remaining["cost"]["budget_limit_usd"] == budget
    assert len(remaining["remaining"]) == 21
    assert "model_authoring" in remaining["remaining"]
    author = next(row for row in remaining["reservation_floors"] if row["stage"] == "fundamentals:R1")
    ordinary = next(row for row in remaining["reservation_floors"] if row["stage"] == "macro:R1")
    assert author["max_output_tokens"] == ordinary["max_output_tokens"] == 128000
    assert board.model_phase == "research"
    assert remaining["feasibility"] == ("insufficient_even_before_input" if budget == "0.12" else "not_guaranteed")
    # The insufficiency is a measurable consequence of the existing native
    # reservation, not a newly introduced financial threshold.
    if budget == "0.12":
        fake = FakeMessages("0.001")
        arguments = {**_call_kwargs(), "model": trade_idea.model_for_role("red_team"),
                     "max_tokens": trade_idea._remaining_stage_token_cap(board, "red_team")}
        with pytest.raises(BudgetBlocked):
            gate.wrap_client(SimpleNamespace(messages=fake), role="red_team").messages.create(**arguments)
        assert fake.calls == 0
    s.reserve_cost(run_id, "uncertain-tail", "specialist:macro", trade_idea.model_for_role("specialist"), "0.01")
    s.mark_cost_unknown(run_id, "uncertain-tail", reason="Provider receipt not available")
    unknown = trade_idea._remaining_work(board)
    assert unknown["feasibility"] == "blocked"
    assert unknown["cost"]["remaining_known_usd"] is None
    assert unknown["cost"]["unknown_reserved_usd"] == "0.01"


def test_every_paid_request_fetches_uncached_live_catalog(migrated, monkeypatch):
    s = store(migrated)
    payload = _priced_request()
    run_id = s.create_run(payload, idempotency_key="fresh-catalog-each-call")["run"]["id"]
    s.claim_run(run_id)
    seen = []

    def catalog(*, use_cache=True):
        seen.append(use_cache)
        return payload["catalog_snapshot"]

    monkeypatch.setattr(trade_idea, "fetch_model_catalog", catalog)
    fake = FakeMessages("0.0001")
    budgeted = _gate(s, run_id, payload)
    budgeted.catalog_fetcher = lambda: catalog(use_cache=False)
    budgeted = budgeted.wrap_client(
            SimpleNamespace(messages=fake), role="specialist:fundamentals")
    budgeted.messages.create(**_call_kwargs())
    budgeted.messages.create(**_call_kwargs())
    assert seen == [False, False]
    assert fake.calls == 2


@pytest.mark.parametrize("response_id,response_model", [
    (None, "meta/muse-spark-1.3"),
    ("provider-other-model", "another/model"),
])
def test_provider_identity_mismatch_blocks_billing_and_later_spend(
        migrated, response_id, response_model):
    s = store(migrated)
    payload = _priced_request()
    run_id = s.create_run(payload, idempotency_key="wrong-provider-" + str(response_id))["run"]["id"]
    s.claim_run(run_id)

    class WrongModelMessages(FakeMessages):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            response.id = response_id
            response.model = response_model
            return response

    fake = WrongModelMessages("0.001")
    budgeted = _gate(s, run_id, payload).wrap_client(
        SimpleNamespace(messages=fake), role="specialist:fundamentals")
    with pytest.raises(RuntimeError, match="identita' modello/risposta"):
        budgeted.messages.create(**_call_kwargs())
    assert s.get_run(run_id)["cost"]["unknown_requests"] == 1
    with pytest.raises(BudgetBlocked):
        budgeted.messages.create(**_call_kwargs())
    assert fake.calls == 1


def test_negative_or_truncated_tool_receipt_never_backs_evidence():
    from bellomberg.agents.specialists.base import _trade_idea_tool_receipt_success
    positive = {"ok": True, "data": {"ticker": "TEST", "as_of": "2026-09-27",
                                    "price": 100, "currency": "USD", "status": "ready"}}
    assert _trade_idea_tool_receipt_success(positive, "get_price_live") is True
    for bad in ({"ok": False}, {"status": "stale"}, {"status": "partial"},
                {"truncated": True}, {"error": "tool unavailable"}):
        envelope = {**positive, "data": {**positive["data"], **bad}}
        assert _trade_idea_tool_receipt_success(envelope, "get_price_live") is False
    assert _trade_idea_tool_receipt_success(positive, "get_price_live", truncated=True) is False


def test_short_no_tool_specialist_response_has_no_paid_retry(tmp_path, monkeypatch):
    from bellomberg.agents.specialists import Blackboard
    from bellomberg.agents.specialists.base import Specialist
    from bellomberg.core import current_facts, mandato_pm
    from bellomberg.core.llm_client import Usage

    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "Offline facts")
    monkeypatch.setattr(mandato_pm, "carica", lambda: mandato_pm.profilo_esempio())
    calls = []

    class OneAnswer:
        def create(self, **kwargs):
            calls.append(kwargs)
            assert len(calls) == 1, "Trade Idea must not retry a successful paid response"
            return SimpleNamespace(id="once", model=kwargs["model"],
                content=[SimpleNamespace(type="text", text="I will investigate.")],
                stop_reason="end_turn", usage=Usage(input_tokens=10, output_tokens=8,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0, cost_usd=0.0001))

    gate = SimpleNamespace(wrap_client=lambda client, role: client)
    board = Blackboard(memory_db=None, memo_id=None,
        heartbeat_path=tmp_path / "heartbeat.json", run_scope="trade_idea",
        run_id="synthetic", target_ticker="TEST", pm_view="Thesis",
        candidate_history="[]", budget_gate=gate)
    specialist = Specialist(board, client=SimpleNamespace(messages=OneAnswer()))
    report = specialist.run(0)
    assert len(calls) == 1
    assert report.startswith("[ERROR BASE round 0]: report troppo breve senza tool")


def test_numeric_claims_require_a_bound_same_context_source():
    receipts = [{"tool": "get_price_live", "input": {"ticker": "TEST"},
                 "success": True, "truncated": False,
                 "output": '{"as_of":"2026-09-27","price":100,"currency":"USD"}'},
                {"tool": "get_fundamentals", "input": {"ticker": "TEST"},
                 "success": True, "truncated": False,
                 "output": '{"as_of":"2026-09-27","revenue":999,"unit":"USD million"}'}]
    board = SimpleNamespace(tool_receipts=receipts)
    result = {"summary": "Revenue was 999m USD [src: get_fundamentals].",
              "pm_view_response": "The FY25 filing and 2026-09-27 date are identifiers, not values.",
              "evidence": [{"id": "financial", "source": "[src: get_fundamentals] official",
                            "as_of": "2026-09-27", "summary": "Revenue 999 USD million", "url": None},
                           {"id": "price", "source": "[src: get_price_live] official",
                            "as_of": "2026-09-27", "summary": "Price 100 USD", "url": None}],
              "dossier": [{"key": "executive", "paragraphs": [
                  "Price 100 USD [evidence: price]."], "evidence_ids": ["financial", "price"],
                  "tables": []}], "scenarios": [], "objections": [], "proposal": None}
    assert trade_idea._numeric_claim_gaps(result, board, "TEST",
        "2026-09-27T22:00:00Z", {}) == []
    result["summary"] = "Price $2027 without a source."
    assert any("summary" in gap for gap in trade_idea._numeric_claim_gaps(
        result, board, "TEST", "2026-09-27T22:00:00Z", {}))
    result["summary"] = "Fair value 2027 USD without a source."
    assert any("summary" in gap for gap in trade_idea._numeric_claim_gaps(
        result, board, "TEST", "2026-09-27T22:00:00Z", {}))
    result["summary"] = "Revenue was 999m USD."
    assert any("summary" in gap for gap in trade_idea._numeric_claim_gaps(
        result, board, "TEST", "2026-09-27T22:00:00Z", {}))
    result["summary"] = "Revenue was 999m USD [src: get_fundamentals]."
    result["dossier"][0]["tables"] = [{"source": "Company filings", "rows": [["Revenue", "999"]]}]
    assert any("table" in gap for gap in trade_idea._numeric_claim_gaps(
        result, board, "TEST", "2026-09-27T22:00:00Z", {}))
    result["dossier"][0]["tables"] = []
    result["scenarios"] = [{"analysis":
        "Upside 27% [assumption] formula=(127-100)/100 [src: get_price_live]"}]
    assert trade_idea._numeric_claim_gaps(result, board, "TEST",
        "2026-09-27T22:00:00Z", {}) == []


def test_evidence_date_must_be_in_economic_payload_not_wrapper_stamp():
    board = SimpleNamespace(tool_receipts=[{"tool": "get_fundamentals",
        "input": {"ticker": "TEST"}, "success": True, "truncated": False,
        "output": json.dumps({"_timestamp": "2026-09-27T20:00:00Z",
            "data": {"ticker": "TEST", "as_of": "2022-01-01", "revenue": 999}})}])
    result = {"evidence": [{"id": "fabricated_date", "source": "[src: get_fundamentals]",
                "as_of": "2026-09-27", "summary": "Revenue 999 USD", "url": None}],
              "dossier": [{"evidence_ids": ["fabricated_date"]}]}
    bound, _ = trade_idea._bound_evidence_details(result, board, "TEST",
                                                   "2026-09-27T22:00:00Z")
    assert bound == {}
    result["evidence"][0]["as_of"] = "2022-01-01"
    bound, _ = trade_idea._bound_evidence_details(result, board, "TEST",
                                                   "2026-09-27T22:00:00Z")
    assert "fabricated_date" in bound


def test_candidate_daily_close_must_be_fresh_and_identical_before_routing(monkeypatch):
    from bellomberg.agents import chat_tools
    today = datetime.now(timezone.utc).date()
    day = today
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    prices = iter((100, 101))

    def quote(*args, **kwargs):
        return {"data": {"ticker": "TEST", "px": next(prices),
                "price_asof": day.isoformat(),
                "source": "yfinance daily Close (not an intraday quote)"},
                "_source": "yfinance/IBKR via agent_tools.get_market_data(TEST)"}

    monkeypatch.setattr(chat_tools, "dispatch", quote)
    board = SimpleNamespace(data={"_identity": {"ticker": "TEST", "currency": "USD"}},
                            tool_receipts=[], tool_log=[])
    first = trade_idea._candidate_quote_receipt(board, "TEST")
    second = trade_idea._candidate_quote_receipt(board, "TEST")
    assert first["status"] == second["status"] == "ready"
    assert first["currency_basis"] == "accepted_identity"
    assert trade_idea._candidate_quote_matches(first, second) is False
    assert all(receipt["success"] for receipt in board.tool_receipts)


def test_final_partial_pdf_demotes_untouched_dcn_before_email(migrated, tmp_path, monkeypatch):
    from bellomberg.reporting.trade_idea_delivery import verify_trade_idea_manifest
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES('TEST',10,1)")
    s = store(migrated)
    run_id = s.create_run(_priced_request(), idempotency_key="partial-final-pdf")["run"]["id"]
    token = s.claim_run(run_id)
    s.finish_run(run_id, token, store_result(), "completed")
    assert s.route_result(run_id, all_checks())["kind"] == "dcn"
    observed = []
    trade_idea.deliver_trade_idea(s, run_id, output_dir=tmp_path / "delivery",
        send=lambda manifest, language: (observed.append(manifest["artifacts"][0]["name"])
            or {"email_status": "accepted", "status": "accepted"}))
    detail = s.get_run(run_id)
    assert detail["run"]["destination"]["kind"] == "research"
    assert detail["run"]["technical_status"] == "incomplete"
    assert detail["email"]["status"] == "accepted"
    assert observed == ["trade-idea-research.pdf"]
    assert (tmp_path / "delivery" / "trade-idea.pdf").exists()
    assert verify_trade_idea_manifest(detail["artifacts"])
    from pathlib import Path
    from bellomberg.reporting import trade_idea_report
    final_pdf = Path(detail["artifacts"]["artifacts"][0]["path"])
    original = final_pdf.read_bytes()
    final_pdf.unlink()
    monkeypatch.setattr(trade_idea_report, "build_trade_idea_report",
        lambda *_args, **_kwargs: pytest.fail("Recovery must reuse the exact final research PDF"))
    for _ in range(2):
        trade_idea.deliver_trade_idea(s, run_id, output_dir=tmp_path / "delivery", send_email=False,
            send=lambda *_args, **_kwargs: pytest.fail("Files-only recovery must not send email"))
    assert final_pdf.read_bytes() == original
    assert s.get_run(run_id)["artifacts"] == detail["artifacts"]
    assert s.get_run(run_id)["run"]["technical_status"] == "incomplete"


def test_unpersisted_manifest_cannot_restore_files_outside_its_attested_inventory(migrated, tmp_path):
    from hashlib import sha256
    from pathlib import Path
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery
    current = store(migrated)
    run_id = current.create_run(_priced_request(), idempotency_key="unpersisted-receipt-tamper")["run"]["id"]
    token = current.claim_run(run_id)
    current.finish_run(run_id, token, store_result(judgment="watch", proposal=False), "completed")
    current.route_result(run_id, all_checks())
    detail = current.get_run(run_id)
    directory = tmp_path / "delivery"
    manifest = prepare_trade_idea_delivery(detail["run"], detail["result"],
        output_dir=directory, model_roots=[tmp_path])
    unexpected = directory / "unattested.pdf"
    digest = sha256(b"Unattested bytes").hexdigest()
    (directory / ".exact-artifacts" / (digest + ".blob")).write_bytes(b"Unattested bytes")
    manifest["exact_artifact_receipts"].append({"path": str(unexpected), "sha256": digest, "kind": "pdf"})
    (directory / "delivery.json").write_text(json.dumps(manifest), encoding="utf-8")
    original_pdf = Path(manifest["artifacts"][0]["path"])
    original_pdf.unlink()
    with pytest.raises(ValueError, match="inventory differs"):
        trade_idea.deliver_trade_idea(current, run_id, output_dir=directory, send_email=False)
    assert not unexpected.exists() and not original_pdf.exists()
    assert current.get_run(run_id)["artifacts"] is None
    assert current.get_run(run_id)["email"]["attempts"] == 0


def test_demoted_report_recovers_after_crash_before_manifest_reseal(migrated, tmp_path, monkeypatch):
    from bellomberg.reporting import trade_idea_report
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES('TEST',10,1)")
    s = store(migrated)
    run_id = s.create_run(_priced_request(), idempotency_key="reseal-crash")["run"]["id"]
    token = s.claim_run(run_id)
    s.finish_run(run_id, token, store_result(), "completed")
    s.route_result(run_id, all_checks())
    real_report = trade_idea_report.build_trade_idea_report

    def fail_research_report(*args, output_path, **kwargs):
        if str(output_path).endswith("trade-idea-research.pdf"):
            raise OSError("synthetic crash before manifest reseal")
        return real_report(*args, output_path=output_path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(trade_idea_report, "build_trade_idea_report", fail_research_report)
        with pytest.raises(OSError, match="synthetic crash"):
            trade_idea.deliver_trade_idea(s, run_id, output_dir=tmp_path / "delivery",
                send=lambda *args, **kwargs: pytest.fail("email before reseal"))
    detail = s.get_run(run_id)
    assert detail["run"]["destination"]["kind"] == "research"
    assert detail["run"]["phase"] == "report_incomplete"
    assert detail["artifacts"] is None
    assert detail["email"]["status"] == "not_attempted"
    observed = []
    trade_idea.deliver_trade_idea(s, run_id, output_dir=tmp_path / "delivery",
        send=lambda manifest, language: (observed.append(manifest["artifacts"][0]["name"])
            or {"email_status": "accepted", "status": "accepted"}))
    assert observed == ["trade-idea-research.pdf"]
    assert s.get_run(run_id)["email"]["status"] == "accepted"


def test_final_partial_pdf_cannot_demote_pm_touched_dcn_or_send(migrated, tmp_path):
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES('TEST',10,1)")
    s = store(migrated)
    run_id = s.create_run(_priced_request(), idempotency_key="pm-touched-final-pdf")["run"]["id"]
    token = s.claim_run(run_id)
    s.finish_run(run_id, token, store_result(), "completed")
    destination = s.route_result(run_id, all_checks())
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO decision_notes(decision_id,autore,testo,timestamp) "
                     "VALUES(?,?,?,?)", (destination["decision_id"], "PM", "Reviewed", "2026-09-27"))
    with pytest.raises(Exception, match="(?i)review|touch|modif|demot"):
        trade_idea.deliver_trade_idea(s, run_id, output_dir=tmp_path / "delivery",
            send=lambda *args, **kwargs: pytest.fail("email for touched DCN"))
    detail = s.get_run(run_id)
    assert detail["run"]["destination"]["kind"] == "dcn"
    assert detail["run"]["technical_status"] == "incomplete"
    assert detail["email"]["status"] == "blocked"


def test_alternate_db_without_isolated_risk_loaders_blocks_before_paid_call(migrated, tmp_path):
    s = store(migrated)
    run_id = s.create_run(_priced_request(), idempotency_key="risk-isolation")["run"]["id"]
    calls = []
    trade_idea.execute_trade_idea(run_id, store=s, lock_path=tmp_path / "paid.lock",
        output_dir=tmp_path / "report", round_runner=lambda *_: calls.append("round"),
        catalog_fetcher=lambda: _priced_request()["catalog_snapshot"])
    detail = s.get_run(run_id)
    assert detail["run"]["technical_status"] == "failed"
    assert "loader rischio e stress isolati" in detail["run"]["reason"]
    assert detail["cost"]["requests"] == 0
    assert calls == []


def test_candidate_history_counts_omitted_prior_runs():
    rows = [{"id": "current", "created_at": "2026-09-27", "technical_status": "accepted",
             "destination": {"kind": "none"}}]
    rows.extend({"id": f"old-{n}", "created_at": "2026-09-20",
                 "technical_status": "completed", "destination": {"kind": "research"}}
                for n in range(20))
    fake = SimpleNamespace(
        list_runs=lambda **kwargs: {"runs": rows, "total": 30},
        get_run=lambda run_id: {"result": {"judgment": "watch", "summary": run_id}})
    history = trade_idea._candidate_history(fake, "TEST", "current")
    assert history["total_prior"] == 29
    assert history["included"] == 20
    assert history["omitted"] == 9


def test_fx_receipt_rejects_fallback_and_stale_book():
    portfolio = {"positions": [{"ticker": "US", "valuta": "USD",
                               "fx_to_eur": 0.9, "fx_source": "fallback"}]}
    assert trade_idea._fx_receipt(portfolio)["valid"] is False
    portfolio["positions"][0]["fx_source"] = "live"
    receipt = trade_idea._fx_receipt(portfolio)
    assert receipt["valid"] is True
    assert receipt["market_as_of"] is None
    portfolio["stale_positions"] = ["US"]
    assert trade_idea._fx_receipt(portfolio)["valid"] is False


def test_no_workbook_is_valid_empty_reference_set_but_not_operational():
    assert trade_idea._valuation_refs_match({"valuation_refs": []}, []) is True
    assert trade_idea._valuation_refs_match({"valuation_refs": [{"snapshot_id": "s",
        "generation_id": "g", "valuation_date": "2026-09-27"}]}, []) is False


def test_candidate_valuation_dispatch_failure_is_recorded_not_unbound(tmp_path, monkeypatch):
    from bellomberg.agents.specialists import Blackboard
    from bellomberg.agents import chat_tools
    def fail(*args, **kwargs):
        raise TimeoutError("synthetic valuation timeout")
    monkeypatch.setattr(chat_tools, "dispatch", fail)
    with language_context("it"):
        board = Blackboard(memory_db=None, memo_id=None,
            heartbeat_path=tmp_path / "heartbeat.json", run_scope="trade_idea",
            run_id="synthetic", target_ticker="TEST", pm_view="",
            candidate_history="[]")
        payload = trade_idea._evaluate_candidate(board)
    assert payload["ok"] is False
    assert "synthetic valuation timeout" in payload["error"]
    assert board.tool_receipts[-1]["success"] is False
    assert board.valuation_attempts[-1]["ticker"] == "TEST"


def test_dead_worker_is_interrupted_without_paid_replay(migrated, monkeypatch):
    from bellomberg.api import trade_idea_routes
    s = store(migrated)
    payload = _priced_request()
    run_id = s.create_run(payload, idempotency_key="orphaned-worker")["run"]["id"]
    s.claim_run(run_id)
    s.reserve_cost(run_id, "outstanding", "capo", trade_idea.model_for_role("capo"), "1")
    monkeypatch.setattr(trade_idea_routes, "_worker_state", lambda _: (None, False))
    recovered = trade_idea_routes.recover_orphan_runs(migrated)
    detail = s.get_run(run_id)
    assert recovered["interrupted"] == 1
    assert detail["run"]["technical_status"] == "interrupted"
    assert detail["cost"]["unknown_requests"] == 1


def test_capo_without_exact_reviewed_excel_stops_before_any_provider_call(tmp_path, monkeypatch):
    from bellomberg.agents.specialists import Blackboard
    from bellomberg.core import mandato_pm
    from bellomberg.agents import chat_tools
    from trade_idea_fixtures import research_result

    run_id = "11111111-1111-4111-8111-111111111111"
    view = "Switching costs make this business immune to a downturn."
    payload = research_result("rejected", run_id=run_id)
    for key in ("run_id", "run_type", "pm_view", "destination"):
        payload.pop(key, None)
    stream_calls = []
    client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: stream_calls.append(kwargs)))
    gate = SimpleNamespace(wrap_client=lambda client, role: client)
    monkeypatch.setattr(mandato_pm, "blocco_prompt", lambda mandate: "Synthetic mandate")
    monkeypatch.setattr(chat_tools, "_compatta_portfolio_live", lambda portfolio: {"status": "synthetic"})
    with language_context("en"):
        board = Blackboard(memory_db=None, memo_id=None,
            heartbeat_path=tmp_path / "heartbeat.json", run_scope="trade_idea",
            run_id=run_id, target_ticker="SYNTH-EXT", pm_view=view,
            candidate_history="[]", budget_gate=gate)
        for name in ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options"):
            board.write(name, 1, "Synthetic specialist research completed")
            if name in ("fundamentals", "quant", "options"):
                board.write(name, 2, "Synthetic final specialist research completed")
        board.write("_red_team", 1, "Synthetic adversarial critique with unresolved renewal risk")
        board.data["_data_cutoff"] = "2026-09-27T21:00:00Z"
        with pytest.raises(RuntimeError, match="usable exact final workbook"):
            trade_idea.run_trade_idea_capo(board, portfolio={}, mandate={}, client=client)
    assert stream_calls == []


@pytest.mark.parametrize("injected", [False, True])
def test_capo_native_timeout_follows_cap_and_injected_client_is_untouched(model_board, monkeypatch, injected):
    """Stop at the budget wrapper: inspect construction without an inference."""
    import httpx
    from bellomberg.core import mandato_pm

    ref = {"snapshot_id": "snapshot", "generation_id": "new-generation", "workbook_sha256": "b" * 64}
    reviews = {}
    for desk in DESKS:
        report = "Complete frozen exact-model discussion for " + desk + ". " * 40
        model_board.write(desk, 2, report)
        reviews[desk] = {"round": 2, "model_ref": dict(ref), "report_sha256": trade_idea._plan_digest(report)}
    critique = "Complete frozen Red Team critique with explicitly retained economic dissent. " * 20
    model_board.write("red_team", 1, critique)
    model_board.data.update(_desk_model_reviews=reviews,
        _red_model_review={"model_ref": dict(ref), "report_sha256": trade_idea._plan_digest(critique)})
    monkeypatch.setattr(mandato_pm, "blocco_prompt", lambda mandate: "Frozen synthetic mandate.")
    supplied = SimpleNamespace(timeout=37.0, _http=SimpleNamespace(timeout=httpx.Timeout(37.0, connect=5.0)))
    created, wrapped = [], []

    def constructor(**kwargs):
        created.append(kwargs)
        return supplied

    class ReachedBudgetGate(Exception):
        pass

    def wrap(raw, *, role):
        wrapped.append((raw, role))
        raise ReachedBudgetGate("No provider dispatch in this constructor test")

    monkeypatch.setattr(trade_idea, "OpenRouterClient", constructor)
    model_board.budget_gate = SimpleNamespace(wrap_client=wrap)
    with pytest.raises(ReachedBudgetGate):
        trade_idea.run_trade_idea_capo(model_board, portfolio={}, mandate={},
            client=supplied if injected else None)
    assert created == ([] if injected else [{"timeout": 3600.0, "max_retries": 0}])
    assert wrapped == [(supplied, "capo")]
    assert supplied.timeout == 37.0
    assert supplied._http.timeout.connect == 5.0
    assert all(getattr(supplied._http.timeout, key) == 37.0 for key in ("read", "write", "pool"))


@pytest.mark.parametrize("capo_malformed,provider_failure", [(False, None), (True, None),
    (False, "HTTP 503 service unavailable"), (False, "HTTP 504 provider timeout"),
    (False, "provider disconnected after dispatch"), (False, "incomplete_response"),
    (False, "stream_disconnected")])
def test_full_committee_replay_uses_real_desk_red_team_and_capo_classes(
        migrated, tmp_path, monkeypatch, capo_malformed, provider_failure):
    """Every agent class runs; only the provider, market tools and book are synthetic."""
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists import base as specialist_base
    from bellomberg.core import current_facts, llm_client, mandato_pm, paths
    from bellomberg.core.llm_client import Usage
    from trade_idea_fixtures import bind_workbook, research_result
    from copy import deepcopy
    from test_trade_idea_economic import qualified, _operating_plan
    from bellomberg.valuation import trade_idea_model
    qualification = qualified(tmp_path)
    assert qualification["status"] == "qualified", qualification["reasons"]
    basis_plan = deepcopy(_operating_plan())
    actual_models, build_events = [], []
    real_build = trade_idea_model.build_from_plan
    def build_common(*args, **kwargs):
        assert {desk for desk in DESKS if desk != "fundamentals"} <= {
            desk for desk in DESKS for call in provider_calls
            if "You are the " + desk + " desk" in str(call.get("system"))
            and "Round 1." in str(call.get("messages"))}
        built = real_build(*args, **kwargs)
        actual_models.append(built)
        build_events.append(deepcopy(kwargs["author_context"]))
        return built
    monkeypatch.setattr(trade_idea_model, "build_from_plan", build_common)
    monkeypatch.setattr(paths, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(paths, "REPORT_DIR", tmp_path)

    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO decisions(id,timestamp,action,ticker,rationale,status,"
                     "pm_feedback,veto,veto_reason) VALUES(2,'2026-09-01','RESEARCH',"
                     "'SYNTH-EXT','historical thesis','PENDING',?,1,?)",
                     ("Synthetic PM veto: renewal proof absent", "Synthetic PM veto"))
        conn.execute("INSERT INTO decision_notes(decision_id,autore,testo,timestamp) "
                     "VALUES(2,'PM','Synthetic PM note: cash bridge needed','2026-09-10')")
    s = store(migrated)
    payload = _priced_request(budget="20")
    payload.update(ticker="SYNTH-EXT", currency="EUR", language="en",
                   view_text="Switching costs make this business immune to a downturn.")
    payload["source_qualification"] = qualification
    payload["authorization"]["source_fingerprint"] = qualification["fingerprint"]
    run_id = s.create_run(payload, idempotency_key="whole-committee")["run"]["id"]
    capo_payload = research_result("rejected", run_id=run_id)
    for key in ("run_id", "run_type", "pm_view", "destination"):
        capo_payload.pop(key, None)
    provider_calls = []
    tool_calls = []

    def response(model, blocks, stop_reason):
        return SimpleNamespace(id="synthetic-provider-" + str(len(provider_calls)),
            model=model, stop_reason=stop_reason, content=blocks,
            usage=Usage(input_tokens=120, output_tokens=180,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
                cost_usd=0.001))

    class SyntheticStream:
        def __init__(self, kwargs): self.kwargs = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get_final_message(self):
            if provider_failure == "stream_disconnected":
                raise RuntimeError("Provider stream disconnected after partial output")
            final = bind_workbook(deepcopy(capo_payload), actual_models[-1])
            return response(self.kwargs["model"],
                [SimpleNamespace(type="text", text="{broken" if capo_malformed
                                 else json.dumps(final))], "end_turn")

    class SyntheticMessages:
        def create(self, **kwargs):
            provider_calls.append(kwargs)
            if provider_failure and provider_failure != "stream_disconnected":
                if provider_failure == "incomplete_response":
                    return response(kwargs["model"], [SimpleNamespace(type="text", text="Partial visible output")], "max_tokens")
                raise RuntimeError(provider_failure)
            messages = kwargs["messages"]
            tool_done = any(isinstance(row.get("content"), list)
                and any(isinstance(block, dict) and block.get("type") == "tool_result"
                        for block in row["content"])
                for row in messages)
            tools = [row["name"] for row in kwargs.get("tools") or []]
            build = model_build_tool_blocks(kwargs, basis_plan,
                [row["id"] for row in qualification["source_report"]["documents"]])
            if build:
                return response(kwargs["model"], [SimpleNamespace(type="tool_use",
                    id="build-" + str(len(provider_calls)) + "-" + str(index), **item)
                    for index, item in enumerate(build)], "tool_use")
            meta = review_tool_block(kwargs, actual_models[-1] if actual_models else None)
            if meta:
                return response(kwargs["model"], [SimpleNamespace(type="tool_use",
                    id="review-" + str(len(provider_calls)), **meta)], "tool_use")
            available = [name for name in tools if name not in MODEL_META_TOOLS]
            if available and not tool_done and kwargs.get("tool_choice") != {"type": "none"}:
                return response(kwargs["model"],
                    [SimpleNamespace(type="tool_use", id="tool-" + str(len(provider_calls)),
                        name=available[0], input={"ticker": "SYNTH-EXT"})], "tool_use")
            if (kwargs.get("response_format") or {}).get("json_schema", {}).get("name") == "trade_idea_committee_review":
                text = json.dumps(committee_review())
            else:
                text = ("SYNTH-EXT research: observed tool data are synthetic and dated; "
                        "renewal economics and cash conversion remain unresolved [src: synthetic_tool]. " * 12)
            return response(kwargs["model"],
                [SimpleNamespace(type="text", text=text)], "end_turn")

        def stream(self, **kwargs):
            provider_calls.append(kwargs)
            return SyntheticStream(kwargs)

    class SyntheticClient:
        def __init__(self, **kwargs):
            import httpx
            self._http = SimpleNamespace(timeout=httpx.Timeout(kwargs.get("timeout", 3600.0)))
            self.messages = SyntheticMessages()

    def dispatch(name, input_, **kwargs):
        tool_calls.append((name, kwargs.get("caller")))
        if name == "get_valuation":
            return {"ok": False, "data": {"ok": False, "error": "No authorized workbook",
                "valuation_usability": {"usable": False}, "exclude_from_action_table": True},
                "_source": "synthetic fixture"}
        today = datetime.now(timezone.utc).date()
        day = today
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return {"ok": True, "data": {"ticker": "SYNTH-EXT",
            "as_of": "2026-09-27", "price": 100, "currency": "EUR", "status": "ready",
            "px": 100, "price_asof": day.isoformat(),
            "source": "yfinance daily Close (not an intraday quote)"},
            "_source": "synthetic fixture", "_timestamp": "2026-09-27T20:00:00Z"}

    monkeypatch.setattr(specialist_base, "OpenRouterClient", SyntheticClient)
    monkeypatch.setattr(llm_client, "OpenRouterClient", SyntheticClient)
    monkeypatch.setattr(trade_idea, "OpenRouterClient", SyntheticClient)
    monkeypatch.setattr(chat_tools, "dispatch", dispatch)
    monkeypatch.setattr(chat_tools, "_compatta_portfolio_live",
                        lambda portfolio: {"positions": portfolio["positions"],
                                           "cash_disponibile_eur": portfolio["cash_disponibile_eur"]})
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "Synthetic offline facts only")
    synthetic_mandate = mandato_pm.profilo_esempio()
    monkeypatch.setattr(mandato_pm, "carica", lambda: synthetic_mandate)
    book = {"positions": [], "cash_source": "sqlite:cash_state",
            "cash_disponibile_eur": 10000, "stale_positions": [], "fx_incomplete": []}
    trade_idea.execute_trade_idea(run_id, store=s,
        lock_path=tmp_path / "whole-committee.lock", output_dir=tmp_path / "delivery",
        portfolio_loader=lambda: book, mandate_loader=lambda: synthetic_mandate,
        preparer_binder=lambda *_: (None, {"status": "disabled", "reason": "synthetic policy"}),
        source_qualifier=lambda *_a, **_k: qualification,
        risk_loader=lambda: {"error": "synthetic risk unavailable"},
        stress_loader=lambda: {"error": "synthetic stress unavailable"},
        candidate_metrics_loader=lambda *_: {"status": "unavailable", "reason": "synthetic"},
        delivery_sender=lambda manifest, language: {"email_status": "accepted",
            "status": "accepted", "message_id": "synthetic-message"},
        catalog_fetcher=lambda: payload["catalog_snapshot"],
        isolated_tool_dispatcher=dispatch,
        isolated_facts_loader=lambda: "Synthetic offline facts only")
    detail = s.get_run(run_id)
    if provider_failure:
        assert detail["run"]["technical_status"] == "incomplete"
        assert detail["result"] is None
        assert detail["progress"]["primary_failure"]["message"] in detail["run"]["reason"]
        assert detail["states"]["analysis"] == "incomplete"
        if provider_failure == "incomplete_response":
            assert "truncated" in detail["run"]["reason"]
            assert detail["cost"]["charged_usd"] == "0.001"
            assert detail["cost"]["unknown_requests"] == 0
        else:
            assert (provider_failure if provider_failure != "stream_disconnected" else "stream disconnected") in detail["run"]["reason"]
            assert detail["cost"]["unknown_requests"] == 1
            assert Decimal(detail["cost"]["unknown_reserved_usd"]) > 0
            assert detail["cost"]["remaining_known_usd"] is None
        if provider_failure != "stream_disconnected":
            assert len(provider_calls) == 1
        assert detail["cost"]["requests"] == len(provider_calls)
        return
    reports = detail["progress"]["reports"]
    assert len(reports) == 6, (detail["run"]["reason"], detail["progress"].get("events"))
    assert {row["specialist"] for row in reports} == {
        "macro", "eventdesk", "crypto", "fundamentals", "quant", "options"}
    assert detail["progress"]["red_team"]["report"]
    assert detail["result"]["judgment"] == ("incomplete" if capo_malformed else "rejected")
    if capo_malformed:
        assert "Capo Trade Idea non valido" in detail["result"]["data_gaps"][0]
        assert detail["result"]["proposal"] is None
        assert detail["progress"]["routing_checks"]["capo_valid"] is False
    assert detail["run"]["destination"]["kind"] == "research"
    assert detail["run"]["technical_status"] == ("incomplete" if capo_malformed else "completed")
    assert detail["email"]["status"] == "accepted"
    assert detail["cost"]["requests"] == len(provider_calls)
    assert len(build_events) == 1
    assert {row["desk"] for row in build_events[0]["consultations"]} == set(DESKS)-{"fundamentals"}
    assert all(row["status"] == "complete" and row["usage"] and row["fundamentals_decision"]
               for row in build_events[0]["consultations"])
    for desk in DESKS:
        assert any("You are the " + desk + " desk" in str(call.get("system"))
                   and "Round 2." in str(call["messages"][0].get("content"))
                   and actual_models[-1]["generation_id"] in str(call["messages"][0].get("content"))
                   for call in provider_calls)
    assert len(tool_calls) >= 16
    specialist_prompt = str(next(call["messages"] for call in provider_calls
                                 if "You are the macro desk" in str(call.get("system"))))
    red_prompt = str(next(call["messages"] for call in provider_calls
        if (call.get("response_format") or {}).get("json_schema", {}).get("name") == "trade_idea_committee_review"))
    for prompt in (specialist_prompt, red_prompt):
        assert "Synthetic PM veto: renewal proof absent" in prompt
        assert "Synthetic PM note: cash bridge needed" in prompt
        assert "cash_disponibile_eur" in prompt
