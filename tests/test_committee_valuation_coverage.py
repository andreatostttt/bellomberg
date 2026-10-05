"""Deterministic portfolio coverage with real tool/storage and synthetic sources."""
from copy import deepcopy
import json
import socket

import pytest

from bellomberg.agents import chat_tools, consigliere_multi as cm
from bellomberg.agents.specialists.base import Blackboard, Specialist
from bellomberg.storage import memory_db
from bellomberg.valuation import preparation_runtime, sector_analysis
from test_input_preparation import _bundle
from test_preparation_runtime import _policy
from test_sector_operating_drivers import bundle_for


@pytest.fixture
def coverage_env(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        pytest.fail("Coverage attempted a real network connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(tmp_path / "coverage.db"))
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path)
    from bellomberg.valuation import dcf_engine
    monkeypatch.setattr(dcf_engine, "REPORT_DIR", tmp_path)
    from bellomberg.storage.valuation_versions import ensure_schema
    db = memory_db.MemoryDB()
    with db._conn() as conn:
        ensure_schema(conn)
    bb = Blackboard(memory_db=db)
    bb.current_round = 1
    bb.data["_valuation_preparation"] = {"status": "disabled", "reason": "configuration_absent"}
    return bb


def _portfolio(*tickers):
    return {"n_positions": len(tickers), "positions": [{"ticker": t} for t in tickers]}


@pytest.mark.parametrize("arguments", [{}, {"method_records": []}, {"growth_path": [0.0123]},
                                       {"analysis_context": {"as_of": "2026-09-10"}}])
def test_archived_get_valuation_never_invokes_ai_sources_or_reuses_a_saved_workbook(coverage_env, monkeypatch, arguments):
    # ZR 05/10 (Z4): contratto attuale (1326312). Le garanzie storiche di copertura/riuso sono in
    # archive/private/attic/tests_excel_archiviato_20261005/test_committee_valuation_coverage_legacy.py.
    from bellomberg.valuation import dcf_engine
    from bellomberg.storage import valuation_versions
    def forbidden(*a, **k):
        pytest.fail("Archived get_valuation reached sources, AI, cache or workbook engine")
    monkeypatch.setattr(sector_analysis, "prepare_sector_analysis", forbidden)
    monkeypatch.setattr(dcf_engine, "generate_valuation", forbidden)
    monkeypatch.setattr(memory_db.MemoryDB, "get_valuation_history", forbidden)
    monkeypatch.setattr(valuation_versions.ValuationVersions, "current", forbidden)
    monkeypatch.setattr(preparation_runtime, "installation_runtime", forbidden)
    result = chat_tools.dispatch("get_valuation", {"ticker": "SYNTH-EXT", **arguments},
        caller="committee-orchestrator", prepared_bundle=_bundle(), valuation_preparer=forbidden)
    assert (result["ok"], result["status"], result["code"]) == (False, "archived", "excel_archived")
    assert "Archivio Excel" in result["error"]
    assert not {"valuation_usability", "reused", "path", "acquisition_snapshot", "_thesis_saved"} & set(result)
    with coverage_env.memory_db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 0


def test_previous_success_failure_and_attempt_only_are_not_retried(coverage_env, monkeypatch):
    bb = coverage_env
    bb.record_valuation("DONE", {"ok": True}, "fundamentals")
    bb.record_valuation("FAILED", {"ok": False, "error": "budget exhausted"}, "fundamentals")
    bb.valuation_attempts.append({"ticker": "ATTEMPTED"})
    before = deepcopy(bb.valuation_attempts)
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: pytest.fail("Implicit retry"))
    cm._ensure_portfolio_valuations(bb, _portfolio("DONE", "FAILED", "ATTEMPTED", "DONE"))
    assert bb.valuation_attempts == before
    assert bb.data["_valuation_coverage"]["already_requested"] == ["DONE", "FAILED", "ATTEMPTED"]
    assert bb.tool_log == []


@pytest.mark.parametrize("empty_options", [{}, {"analysis_context": {}}, {"method_records": None},
                                         {"growth_path": [], "variant_view": ""}])
def test_bare_r2_reuses_automatic_failure_but_explicit_revision_dispatches(coverage_env, monkeypatch, empty_options):
    bb = coverage_env
    original = {"ok": False, "error": "Source incomplete", "request_origin": "committee-orchestrator"}
    bb.record_valuation("SYNTH-EXT", original, "committee-orchestrator")
    calls = []
    def revised(name, arguments, **kwargs):
        calls.append((name, arguments))
        return {"data": {"ok": False, "error": "Explicit revised result"}}
    monkeypatch.setattr(chat_tools, "dispatch", revised)
    bb.current_round = 2
    desk = Specialist(bb, client=object())
    reused = desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH-EXT", **empty_options})["data"]
    assert reused["reused_in_run"] is True and reused["error"] == "Source incomplete"
    assert calls == []
    changed = desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH-EXT", "method_records": []})["data"]
    assert changed["error"] == "Explicit revised result" and len(calls) == 1
    assert "request_origin" not in changed


def test_exact_failure_and_preparation_reason_reach_committee_context(coverage_env):
    bb = coverage_env
    bb.record_valuation("SYNTH-EXT", {"ok": False, "error": "Synthetic source refusal",
        "preparation": {"status": "disabled", "reason": "ticker_not_authorized"}}, "committee-orchestrator")
    shared = sector_analysis.valuation_results_block(bb.valuation_results)
    r2 = Specialist(bb, client=object())._build_round_context(2)
    for block in (shared, r2):
        assert "Synthetic source refusal" in block and "ticker_not_authorized" in block


def test_empty_portfolio_never_selects_authorized_or_watchlist_tickers(coverage_env, monkeypatch):
    bb = coverage_env
    bb.data["_valuation_preparation"] = {"status": "enabled", "tickers": ["AUTHORIZED"]}
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: pytest.fail("No DB holding"))
    cm._ensure_portfolio_valuations(bb, _portfolio())
    assert not bb.valuation_results and not bb.valuation_attempts
    assert bb.data["_valuation_coverage"]["reason"] == "portfolio_empty_or_unavailable"


@pytest.mark.parametrize("malformed", [{"valuation_usability": "broken"},
                                      {"valuation_usability": {"usable": "false"}}, []])
def test_invalid_tool_shape_is_a_declared_failure_not_a_run_abort(coverage_env, monkeypatch, malformed):
    bb = coverage_env
    def dispatch(name, arguments, **kwargs):
        return {"data": malformed if arguments["ticker"] == "BROKEN" else {"error": "Source unavailable"}}
    monkeypatch.setattr(chat_tools, "dispatch", dispatch)
    cm._ensure_portfolio_valuations(bb, _portfolio("BROKEN", "NEXT"))
    assert "get_valuation returned invalid" in bb.valuation_results["BROKEN"]["error"]
    assert bb.valuation_results["BROKEN"]["exclude_from_action_table"] is True
    assert "Source unavailable" in bb.valuation_results["NEXT"]["error"]


def test_malformed_preparation_is_explicit_in_shared_context():
    block = sector_analysis.valuation_results_block({"SYNTH-EXT": {"preparation": "broken"}})
    assert "invalid_preparation_metadata" in block
