"""Wiring con store SQLite reale TEMP e auditor double esplicitamente dichiarato."""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest

from bellomberg.core import memo_facts_context as facts
from bellomberg.storage.weekly_run_store import WeeklyRunStore, WeeklyRunBlocked
from test_cablaggio_consigliere_multi import run_offline


@pytest.fixture
def store(tmp_path):
    class DB:
        db_path = str(tmp_path / "synthetic.sqlite")

        @contextmanager
        def _conn(self):
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()
    db = DB()
    with db._conn() as conn:
        conn.execute("CREATE TABLE memos (id INTEGER PRIMARY KEY, notes TEXT)")
        conn.execute("INSERT INTO memos VALUES (1, 'synthetic')")
    result = WeeklyRunStore(db, 1, context={"contract_version": 1,
        "contract": {"memo_facts_policy": facts.POLICY}, "language": "it",
        "portfolio": {"positions": []},
        "research_started_at": "2026-10-07T10:00:00+00:00"})
    receipt = {"tool": "synthetic_tool", "input": {"ticker": "ZZTEST"},
               "output": "123.45", "success": True, "truncated": False}
    from bellomberg.agents.weekly_lifecycle import bind_blackboard
    board = SimpleNamespace(_lock=threading.RLock(), data={}, tool_receipts=[receipt],
                            tool_log=[{"specialist": "fake_desk"}])
    bind_blackboard(board, result)
    board.data["_capo_request"] = {"system": "system 123.45", "user_message": "user 678.90"}
    board.persist_run_checkpoint("capo_request", board.data["_capo_request"])
    result.complete("capo", {"memo": "canonical"}, board)
    assert result.get("capo_request") is None  # The real callback saves the Blackboard snapshot.
    return result


@pytest.fixture
def auditor(monkeypatch):
    calls = []
    def audit(source, snapshot, **kwargs):
        calls.append((source, snapshot, kwargs))
        return {"status": "PARTIAL", "source": source}
    module = SimpleNamespace(audit_memo_facts=audit, render_memo_facts=lambda report: "FACTS DIAGNOSTIC")
    monkeypatch.setitem(sys.modules, "bellomberg.reporting.memo_facts", module)
    return module, calls


def test_partial_exact_parts_no_desk_receipt_join(store, auditor):
    assert facts.checkpoint_memo_facts(store, "canonical") == "FACTS DIAGNOSTIC"
    source, snapshot, kwargs = auditor[1][0]
    assert source == "canonical"
    assert snapshot["context"]["status"] == "PARTIAL"
    assert [(p["role"], p["text"]) for p in snapshot["context"]["parts"]] == [
        ("system", "system 123.45"), ("user", "user 678.90")]
    assert snapshot["context"]["missing_parts"]
    assert snapshot["receipts"][0]["attribution"] == "UNAVAILABLE"
    assert "desk" not in snapshot["receipts"][0]
    assert snapshot["receipts"][0]["sha256"] == facts._digest(snapshot["receipts"][0]["receipt"])
    assert store.get("memo_validated") is None


def test_crash_recovery_uses_saved_block_without_new_audit(store, auditor):
    restored = SimpleNamespace()
    store.restore(restored)
    before = restored.data["_capo_request"]
    block = facts.checkpoint_memo_facts(store, "canonical")
    payload = store.get(facts.STAGE)
    reopened = WeeklyRunStore(store.db, 1)
    auditor[0].audit_memo_facts = lambda *a, **kw: pytest.fail("Auditor repeated after crash")
    assert facts.checkpoint_memo_facts(reopened, "canonical") == block
    assert reopened.get(facts.STAGE) == payload
    reopened.restore(restored)
    assert restored.data["_capo_request"] == before
    with pytest.raises(WeeklyRunBlocked, match="Input"):
        facts.checkpoint_memo_facts(reopened, "changed source")


def test_changed_snapshot_request_cannot_reuse_existing_facts(store, auditor):
    facts.checkpoint_memo_facts(store, "canonical")
    restored = SimpleNamespace(_lock=threading.RLock())
    store.restore(restored)
    restored.data["_capo_request"]["user_message"] = "different request 999"
    store.save_snapshot(restored)
    with pytest.raises(WeeklyRunBlocked, match="Input"):
        facts.checkpoint_memo_facts(store, "canonical")
    assert len(auditor[1]) == 1


@pytest.mark.parametrize("historical", ["no_policy", "already_validated"])
def test_historical_memo_is_not_audited_or_rewritten(store, auditor, historical):
    if historical == "no_policy":
        store.context["contract"].pop("memo_facts_policy")
    else:
        store.complete("memo_validated", {"memo": "historical immutable"})
    assert facts.checkpoint_memo_facts(store, "canonical") == ""
    assert not auditor[1]
    assert store.get(facts.STAGE) is None
    if historical == "already_validated":
        assert store.get("memo_validated") == {"memo": "historical immutable"}


@pytest.mark.parametrize("failure", ["audit", "render"])
def test_fault_visible_private_message_absent_and_counters_measured(store, auditor, failure):
    def fail(*a, **kw):
        raise ValueError("PRIVATE_SENTINEL token=secret")
    setattr(auditor[0], "audit_memo_facts" if failure == "audit" else "render_memo_facts", fail)
    block = facts.checkpoint_memo_facts(store, "canonical")
    assert "CHECK_UNAVAILABLE" in block and "PRIVATE_SENTINEL" not in block
    saved = store.get(facts.STAGE)
    assert "secret" not in json.dumps(saved)
    assert saved["counters"] == {"audit_attempted": 1, "audit_completed": int(failure == "render"),
                                  "render_attempted": int(failure == "render"), "render_completed": 0}
    assert (saved["report"] is not None) == (failure == "render")


@pytest.mark.parametrize("stage", ["request_snapshot", facts.STAGE, "snapshot"])
def test_corruption_is_blocking_not_auditor_unavailable(store, auditor, stage):
    facts.checkpoint_memo_facts(store, "canonical")
    with store.db._conn() as conn:
        if stage == "snapshot":
            conn.execute("UPDATE weekly_runs SET snapshot_json='{}x'")
        elif stage == "request_snapshot":
            payload = json.loads(conn.execute("SELECT snapshot_json FROM weekly_runs").fetchone()[0])
            payload["payload"]["data"]["_capo_request"]["system"] = "tampered request"
            conn.execute("UPDATE weekly_runs SET snapshot_json=?", (json.dumps(payload),))
        else:
            conn.execute("UPDATE weekly_checkpoints SET payload_json='{}' WHERE stage=?", (stage,))
    with pytest.raises((WeeklyRunBlocked, json.JSONDecodeError)):
        facts.checkpoint_memo_facts(store, "canonical")
    assert len(auditor[1]) == 1


def test_native_snapshot_reaches_real_auditor_and_recovery_preserves_result(store, monkeypatch):
    from bellomberg.reporting import memo_facts
    block = facts.checkpoint_memo_facts(store, "Ricavi 678,90 USD")
    saved = store.get(facts.STAGE)
    assert saved["error"] is None and saved["report"]["status"] == "PARTIAL"
    assert saved["report"]["counters"]["context_parts"] == 2
    assert any(f["dimensions"]["context_presence"] == "MATCH" for f in saved["report"]["findings"])
    assert saved["counters"]["audit_completed"] == saved["counters"]["render_completed"] == 1
    assert "PARTIAL" in block and "UNAVAILABLE" not in saved["report"]["context_completeness"]
    monkeypatch.setattr(memo_facts, "audit_memo_facts", lambda *a, **kw: pytest.fail("Repeated paid-run audit"))
    assert facts.checkpoint_memo_facts(WeeklyRunStore(store.db, 1), "Ricavi 678,90 USD") == block
    assert store.get(facts.STAGE) == saved


def test_resume_policy_preserved_including_absence():
    from bellomberg.agents.action_validator import research_gate_enabled
    # Esegue la funzione reale senza importare provider/orchestratore intero.
    source = Path(__file__).parents[1] / "src/bellomberg/agents/consigliere_multi.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_resume_publication_contract")
    namespace = {'research_gate_enabled': research_gate_enabled}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    resume = namespace[node.name]
    assert "memo_facts_policy" not in resume({"memo_facts_policy": facts.POLICY}, {})
    assert resume({}, {"memo_facts_policy": facts.POLICY})["memo_facts_policy"] == facts.POLICY


def test_real_orchestrator_keeps_source_and_publishes_diagnostic(run_offline, auditor, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.storage.memory_db import MemoryDB
    seen, stages = [], []
    validator = sys.modules["bellomberg.agents.action_validator"]
    monkeypatch.setattr(validator, "build_validator_block", lambda memo, *a, **kw: seen.append(memo) or "")
    original = WeeklyRunStore.complete
    def complete(self, stage, *a, **kw):
        stages.append(stage)
        return original(self, stage, *a, **kw)
    monkeypatch.setattr(WeeklyRunStore, "complete", complete)
    cm.run_multi_agent(send_email=False)
    db = MemoryDB()
    with db._conn() as conn:
        memo_id = conn.execute("SELECT MAX(memo_id) FROM weekly_runs").fetchone()[0]
    saved = WeeklyRunStore(db, memo_id)
    source = auditor[1][0][0]
    assert seen == [source]
    assert "FACTS DIAGNOSTIC" not in source
    assert "FACTS DIAGNOSTIC" in saved.get("memo_validated")["memo"]
    assert stages.index(facts.STAGE) < stages.index("memo_validated")
    assert saved.context["contract"]["memo_facts_policy"] == facts.POLICY
