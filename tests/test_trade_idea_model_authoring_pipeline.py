"""Ordinary worker resumes missing model work after an authentic paid R1.

The existing native recovery fixture supplies frozen provider receipts and all
five real peer consultations. Worker, compiler, SQLite and renderers stay real.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bellomberg.agents import consigliere_multi, trade_idea
from bellomberg.agents.specialists import base
from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist
from bellomberg.core import llm_client, mandato_pm
from bellomberg.valuation import trade_idea_model
from test_trade_idea_historical_runtime import _worker_options
from test_trade_idea_model_authoring_recovery import native_case, bb, db_path, migrated, blocks, rows
from trade_idea_evolution_fixtures import committee_review, review_tool_block
from trade_idea_fixtures import bind_workbook, research_result


@pytest.fixture(autouse=True)
def frozen_native_mandate(monkeypatch):
    # The origin and worker must see the same complete frozen mandate; changing
    # it only at continuation would correctly invalidate the saved request.
    monkeypatch.setattr(mandato_pm, "carica", mandato_pm.profilo_esempio)
    native_configure = trade_idea._configure_native_recovery
    def configure(board, store, token, **kwargs):
        # The reusable helper fixture begins at the specialist, whereas the
        # ordinary worker already loads this exact DB context before binding.
        # Seed it before the origin's first sealed checkpoint, never on replay.
        if kwargs.get("inherited") is None:
            board.data.setdefault("_decision_context", store.decision_context(board.run_id))
        return native_configure(board, store, token, **kwargs)
    monkeypatch.setattr(trade_idea, "_configure_native_recovery", configure)


def test_ordinary_worker_finishes_missing_model_then_red_capo_and_real_delivery(native_case, tmp_path, monkeypatch):
    case = native_case
    # The helper fixture has opened a child to test local recovery. Hand off a
    # queued linked continuation to the actual worker; no DB state is forged.
    case.store.interrupt_run(case.child, reason="Offline handoff to the ordinary worker")
    run_id = case.store.create_continuation(case.child, idempotency_key="ordinary-model-completion",
        authorize_new_requests=True)["run"]["id"]
    accepted = deepcopy(case.store.get_accepted_request(case.parent))
    parent = deepcopy(case.store.get_run(case.parent))
    calls, boards, completion_entries, stages, deliveries = [], [], [], [], []
    final_payload = research_result("rejected", run_id=run_id)
    for field in ("run_id", "run_type", "pm_view", "destination"):
        final_payload.pop(field, None)

    # Preserve the source fixture's native schema. Only restore ordinary round
    # context construction for the new R0/R2 requests; saved R1 stays exact.
    monkeypatch.setattr(FundamentalsSpecialist, "_build_round_context", base.Specialist._build_round_context)

    def current_workbook():
        return boards[-1].valuation_results.get("SYNTH-EXT") if boards else None

    def response(body, content, stop="end_turn"):
        return llm_client.Messaggio(id="offline-worker-response-" + str(len(calls)),
            model=body["model"], content=content, stop_reason=stop,
            usage=llm_client.Usage(input_tokens=120, output_tokens=80,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
                reasoning_tokens=20, cost_usd=.002))

    class FrozenStream:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def get_final_message(self):
            stages.append("capo")
            assert current_workbook(), "Capo must see the actually compiled model"
            payload = bind_workbook(deepcopy(final_payload), current_workbook())
            return response(self.body, [llm_client.TextBlock(json.dumps(payload))])

    class FrozenWorkerProvider:
        def __init__(self, **_kwargs):
            self.messages = self
            self.timeout = 3600
            self._http = SimpleNamespace(timeout=httpx.Timeout(3600, connect=30))

        def create(self, **kwargs):
            body = base._checkpoint_json(kwargs)
            calls.append(body)
            if "Continue only the unfinished model construction." in json.dumps(body["messages"]):
                stages.append("author_completion")
                assert boards[-1].read("fundamentals", 1) == case.old_report
                return case.provider.create(**kwargs)
            schema = (body.get("response_format") or {}).get("json_schema", {}).get("name")
            if schema == "trade_idea_committee_review":
                stages.append("red_team")
                assert current_workbook(), "Red Team must follow native model completion"
                return response(body, [llm_client.TextBlock(json.dumps(committee_review()))])
            # Ordinary desk research/review uses only a local native read. All
            # R2 retention and objection replies still pass the real guards.
            names = {block["name"] for block in blocks(body, "tool_use")}
            review = review_tool_block(body, current_workbook())
            tool = review if review else ({"name": "read_blackboard", "input": {}} if not names else None)
            if tool:
                return response(body, [llm_client.ToolUseBlock(
                    "worker-tool-" + str(len(calls)), tool["name"], tool["input"])], "tool_use")
            return response(body, [llm_client.TextBlock(
                "Frozen independent research retains the exact admitted sources, economic uncertainty "
                "and the preserved peer decisions [src: read_blackboard]. " * 30)])

        def stream(self, **kwargs):
            body = base._checkpoint_json(kwargs)
            calls.append(body)
            return FrozenStream(body)

    for module in (base, llm_client, trade_idea):
        monkeypatch.setattr(module, "OpenRouterClient", FrozenWorkerProvider)

    native_completion = trade_idea._complete_saved_model_authoring
    def observed_completion(board):
        completion_entries.append({
            "r1_complete": board.should_run_specialist("fundamentals", 1) is False,
            "model_absent": not trade_idea._verified_candidate_valuations(board),
            "origin": deepcopy(board.specialist_checkpoints["fundamentals:R1"])})
        return native_completion(board)
    monkeypatch.setattr(trade_idea, "_complete_saved_model_authoring", observed_completion)

    def round_runner(board, number):
        boards.append(board)
        case.provider.board = board
        stages.append("R" + str(number))
        consigliere_multi.run_round(board, number)

    options = _worker_options(tmp_path)
    options["output_dir"] = tmp_path / "ordinary-worker"
    options["mandate_loader"] = mandato_pm.profilo_esempio
    options["delivery_sender"] = lambda manifest, language: deliveries.append(manifest["run_id"]) or {
        "email_status": "accepted", "status": "accepted", "message_id": "offline-simulated-delivery"}
    qualification = case.payload["source_qualification"]
    detail = trade_idea.execute_trade_idea(run_id, store=case.store, **options,
        source_qualifier=lambda _ticker, _identity, _as_of, **_kw: trade_idea_model.recheck_accepted_sources(
            qualification, "SYNTH-EXT", qualification["identity"], archive_root=tmp_path / "evidence",
            allow_historical=True),
        preparer_binder=lambda *_args: (None, {"status": "explicit_author_only"}),
        document_archive_root=tmp_path / "evidence", round_runner=round_runner,
        catalog_fetcher=lambda: case.payload["catalog_snapshot"])

    assert detail["run"]["technical_status"] == "completed", detail["run"]["reason"]
    assert len(completion_entries) == 1
    assert completion_entries[0] == {"r1_complete": True, "model_absent": True, "origin": case.old_checkpoint}
    assert stages.index("R1") < stages.index("author_completion") < stages.index("red_team")
    assert stages.index("red_team") < stages.index("R2") < stages.index("capo")
    assert len(case.provider.new_calls) == 3
    assert all({tool["name"] for tool in request["tools"]} <= base.MODEL_AUTHORING_LOCAL_TOOLS
               for request in case.provider.new_calls)
    board = boards[-1]
    assert board.specialist_checkpoints["fundamentals:R1"] == case.old_checkpoint
    assert board.read("fundamentals", 1) == case.old_report
    assert len(board.valuation_generations) == 1
    assert board.valuation_results["SYNTH-EXT"]["request_origin"] == "fundamentals-model-build"
    assert all(trade_idea._consultation_received(board, row)
               and row["fundamentals_decision"]["decision"] == "incorporated"
               for row in board.data["_model_consultations"])
    assert {row["id"] for row in board.data["_model_consultations"]} == {
        row["id"] for row in case.old_consultations}
    assert case.store.get_accepted_request(case.parent) == accepted
    preserved_parent = case.store.get_run(case.parent)
    assert all(preserved_parent[field] == parent[field]
               for field in ("run", "progress", "result", "artifacts", "email", "cost"))
    assert rows(case.path, case.parent) == case.old_costs
    assert detail["cost"]["requests"] == len(case.provider.calls) + len(calls) - len(case.provider.new_calls)
    assert detail["cost"]["budget_limit_usd"] == "10" and detail["cost"]["unknown_requests"] == 0
    assert Decimal(detail["cost"]["charged_usd"]) == (
        Decimal(".003") * len(case.provider.calls)
        + Decimal(".002") * (len(calls) - len(case.provider.new_calls)))
    assert detail["artifacts"]["complete_package_status"] == "ready"
    assert {artifact["kind"] for artifact in detail["artifacts"]["artifacts"]} == {"pdf", "xlsx"}
    assert detail["run"]["memo_id"] and detail["email"]["status"] == "accepted", detail["email"]
    assert deliveries == [run_id] and detail["result"]["judgment"] == "rejected"

    manifest = deepcopy(detail["artifacts"])
    final_calls, provider_calls = len(calls), len(case.provider.calls)
    for _ in range(2):
        trade_idea.deliver_trade_idea(case.store, run_id, output_dir=options["output_dir"], send_email=False)
        assert case.store.get_run(run_id)["artifacts"] == manifest
        for receipt in manifest["exact_artifact_receipts"]:
            assert sha256(Path(receipt["path"]).read_bytes()).hexdigest() == receipt["sha256"]
    assert len(calls) == final_calls and len(case.provider.calls) == provider_calls
    assert deliveries == [run_id] and len(board.valuation_generations) == 1
    (tmp_path / "ordinary-model-authoring-proof.json").write_text(json.dumps({
        "run_id": run_id, "parent_run_id": case.parent, "stages": stages,
        "origin_requests": 35, "completion_requests": len(case.provider.new_calls),
        "worker_requests": len(calls), "known_cost_usd": detail["cost"]["charged_usd"],
        "memo_id": detail["run"]["memo_id"], "generation_id": current_workbook()["generation_id"],
        "complete_package_status": manifest["complete_package_status"],
        "fake_delivery_calls": len(deliveries), "second_delivery_provider_calls": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
