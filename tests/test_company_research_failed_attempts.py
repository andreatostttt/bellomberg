"""Native failure then source acquisition; all dispatch and transport simulated locally."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_company_source_research import DOCUMENT, IDENTITY, SITE, FrozenTransport
from bellomberg.agents.specialists.base import Blackboard, Specialist
from bellomberg.agents.company_research_tools import bind_weekly_company_research
from bellomberg.agents.company_source_research import ResearchSession
from bellomberg.agents import chat_tools


def weekly(tmp_path, transport):
    saved, snapshots = {}, []
    store = SimpleNamespace(
        run_id="failed-attempt-source-review",
        context={"research_started_at": "2026-09-28T10:00:00+00:00", "portfolio": {}},
        db=SimpleNamespace(db_path=str(tmp_path / "unused-book.sqlite")),
        get=lambda key: deepcopy(saved.get(key)),
        complete=lambda key, value, board: saved.setdefault(key, deepcopy(value)),
        save_snapshot=lambda board: snapshots.append(deepcopy(board.data)),
    )
    board = Blackboard()
    board.current_round = 1
    bind_weekly_company_research(board, store,
        identity_resolver=lambda ticker: deepcopy(IDENTITY),
        profile_provider=lambda *a, **k: {"status": "ok", "data": {"info": {"website": SITE}}},
        session_factory=lambda **kwargs: ResearchSession(**kwargs, download=transport))
    return board, snapshots


FAILURES = [
    {"ok": False, "error": "Primary financial documents unavailable", "exclude_from_action_table": True},
    {"ok": False, "error": "Preparazione incompleta: primary financial statements unavailable",
     "exclude_from_action_table": True, "snapshot_id": "frozen-source-snapshot",
     "generation_id": "ebae27c9-15df-4d49-a6e3-7c0185e17fbc",
     "input_consumption": {"status": "incomplete", "consumed_fields": [], "consumed_records": []},
     "preparation": {"status": "incomplete", "issues": [{"reason": "Missing source"}]},
     "valuation_usability": {"usable": False, "reasons": ["Primary documents unavailable"]}},
]


@pytest.mark.parametrize("failure", FAILURES, ids=["dispatch_error", "preparation_error_with_uuid"])
def test_native_failed_get_valuation_does_not_seal_nonexistent_model(tmp_path, monkeypatch, failure):
    transport = FrozenTransport()
    board, snapshots = weekly(tmp_path, transport)
    calls = []

    def fail(name, input_, **kwargs):
        calls.append((name, deepcopy(input_)))
        return deepcopy(failure)

    monkeypatch.setattr(chat_tools, "dispatch", fail)
    desk = Specialist(board, client=object())
    desk.name = "fundamentals"
    result = desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH"})
    assert result == failure and board.valuation_results["SYNTH"] == failure
    frozen_attempts = deepcopy(board.valuation_attempts)
    assert len(frozen_attempts) == 1 and frozen_attempts[0]["revision_origin"] == "not_created"
    acquired = desk._execute_meta_tool("acquire_company_source", {"ticker": "SYNTH", "source": {"url": DOCUMENT}})
    assert acquired["ok"] is True, acquired
    assert len(transport.requests) == 1
    assert board.valuation_results["SYNTH"] == failure and board.valuation_attempts == frozen_attempts
    assert board.data["_company_research"]["SYNTH"]["revision_id"] == acquired["revision_id"]
    assert snapshots and calls == [("get_valuation", {"ticker": "SYNTH"})]


def test_later_failure_does_not_unseal_a_previously_generated_workbook(tmp_path, monkeypatch):
    transport = FrozenTransport()
    board, _ = weekly(tmp_path, transport)
    # A historical generated-artifact receipt remains a seal even when the file
    # is currently absent. This test does not claim to compile this workbook.
    generated = {"ok": True, "path": str(tmp_path / "previously-created.xlsx"),
        "snapshot_id": "prior-snapshot", "generation_id": "prior-generation",
        "valuation_usability": {"usable": False, "reasons": ["Technical review outstanding"]}}
    board.record_valuation("SYNTH", generated, "fundamentals")
    failure = deepcopy(FAILURES[0])
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: deepcopy(failure))
    desk = Specialist(board, client=object())
    desk.name = "fundamentals"
    desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH"})
    assert board.valuation_results["SYNTH"] == failure
    attempts = deepcopy(board.valuation_attempts)
    assert [row["revision_origin"] for row in attempts] == ["generated", "not_created"]
    blocked = desk._execute_meta_tool("acquire_company_source", {"ticker": "SYNTH", "source": {"url": DOCUMENT}})
    assert blocked["ok"] is False and transport.requests == []
    assert board.valuation_attempts == attempts


@pytest.mark.parametrize("evidence", [
    {"path": "previously-generated-but-missing.xlsx",
     "valuation_usability": {"usable": False, "reasons": ["Technical review outstanding"]}},
    {"preparation": {"status": "prepared"}},
    {"input_consumption": {"status": "incomplete", "consumed_records": [{"field": "historical_revenue"}]}},
    {"input_consumption": {"status": "incomplete", "consumed_fields": ["historical_revenue"]}},
], ids=["unusable_workbook", "prepared_without_path", "consumed_records_without_path", "consumed_fields_without_path"])
def test_failed_result_with_artifact_or_consumed_preparation_stays_sealed(tmp_path, monkeypatch, evidence):
    transport = FrozenTransport()
    board, snapshots = weekly(tmp_path, transport)
    failure = {**deepcopy(FAILURES[0]), **deepcopy(evidence)}
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: deepcopy(failure))
    desk = Specialist(board, client=object())
    desk.name = "fundamentals"
    desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH"})
    attempts = deepcopy(board.valuation_attempts)
    blocked = desk._execute_meta_tool("acquire_company_source", {"ticker": "SYNTH", "source": {"url": DOCUMENT}})
    assert blocked["ok"] is False and transport.requests == []
    assert board.valuation_results["SYNTH"] == failure and board.valuation_attempts == attempts
    assert not snapshots and not board.data.get("_company_research")
