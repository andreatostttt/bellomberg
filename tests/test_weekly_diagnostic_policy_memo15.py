"""Policy validation before legacy/completed shortcuts, using native TEMP stores."""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.core import memo_facts_context, source_health
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked, WeeklyRunStore


@pytest.fixture
def native_store(tmp_path):
    class DB:
        db_path = str(tmp_path / "diagnostic-policy.sqlite")

        @contextmanager
        def _conn(self):
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    db = DB()
    with db._conn() as connection:
        connection.execute("CREATE TABLE memos (id INTEGER PRIMARY KEY, notes TEXT)")
        connection.execute("INSERT INTO memos VALUES (1, 'synthetic')")

    def create(contract, validated):
        store = WeeklyRunStore(db, 1, context={"contract_version": 1,
            "contract": contract, "language": "it", "research_started_at": "2032-01-01T00:00:00Z"})
        if validated:
            store.complete("memo_validated", {"memo": "Synthetic published memo"})
        return store

    return create


SURFACES = [
    ("source_health_policy", source_health.POLICY, source_health.STAGE, source_health.checkpoint_source_health),
    ("memo_facts_policy", memo_facts_context.POLICY, memo_facts_context.STAGE, memo_facts_context.checkpoint_memo_facts),
]


def checkpoint_rows(store):
    with store.db._conn() as connection:
        return [tuple(row) for row in connection.execute("SELECT * FROM weekly_checkpoints ORDER BY rowid")]


@pytest.mark.parametrize("key,policy,stage,consumer", SURFACES)
@pytest.mark.parametrize("validated", [False, True])
@pytest.mark.parametrize("invalid", [None, "", False, 0, {}, [], "private-marker/unsupported-version"])
def test_present_invalid_policy_blocks_before_completed_shortcut(native_store, key, policy, stage, consumer, validated, invalid):
    store = native_store({key: invalid}, validated)
    before = checkpoint_rows(store)
    with pytest.raises(WeeklyRunBlocked) as error:
        consumer(store, "Synthetic canonical memo")
    assert "private-marker" not in str(error.value)
    assert checkpoint_rows(store) == before
    assert store.get(stage) is None


@pytest.mark.parametrize("key,policy,stage,consumer", SURFACES)
@pytest.mark.parametrize("validated", [False, True])
def test_absent_policy_preserves_legacy_without_checkpoint_writes(native_store, key, policy, stage, consumer, validated):
    store = native_store({}, validated)
    before = checkpoint_rows(store)
    assert consumer(store, "Synthetic canonical memo") == ""
    assert checkpoint_rows(store) == before
    assert key not in store.context["contract"]


@pytest.mark.parametrize("key,policy,stage,consumer", SURFACES)
def test_supported_completed_run_remains_unchanged(native_store, key, policy, stage, consumer):
    store = native_store({key: policy}, True)
    before = checkpoint_rows(store)
    assert consumer(store, "Synthetic canonical memo") == ""
    assert checkpoint_rows(store) == before
    assert store.get(stage) is None


@pytest.mark.parametrize("key,policy,stage,consumer", SURFACES)
def test_supported_unvalidated_run_still_checkpoints_and_replays(native_store, key, policy, stage, consumer):
    store = native_store({key: policy}, False)
    first = consumer(store, "Synthetic canonical memo")
    assert isinstance(first, str) and first
    assert store.get(stage) is not None
    before = checkpoint_rows(store)
    assert consumer(WeeklyRunStore(store.db, 1), "Synthetic canonical memo") == first
    assert checkpoint_rows(store) == before


def test_source_health_nonweekly_board_without_store_stays_disabled():
    assert source_health.enabled(SimpleNamespace()) is False
