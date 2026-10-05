"""Migrazione della tabella watch: dry-run che non scrive, apply con backup, solo DB temporanei."""
import sqlite3

import pytest

from tests.test_trade_idea_watch_store import make_db
from tools.migrations import migra_trade_idea, migra_trade_idea_watch


def tables(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger','index')")}


def legacy_rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()


@pytest.fixture
def ti_db(tmp_path, monkeypatch):
    path = make_db(tmp_path / "isolated.db")
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    monkeypatch.setattr(migra_trade_idea_watch, "backend_alive", lambda: False)
    migra_trade_idea.migra(path, apply=True)
    return path


def test_dry_run_does_not_write(ti_db):
    before_tables, before_rows = tables(ti_db), legacy_rows(ti_db)
    receipt = migra_trade_idea_watch.migra(ti_db)
    assert receipt["modo"] == "dry-run" and receipt["backup"] is None
    assert receipt["tabelle_mancanti"] == ["trade_idea_watch_triggers"]
    assert receipt["sorgente_invariata"] is True
    assert receipt["scritture_sorgente"]["total_changes"] == 0
    assert receipt["prova"]["schema_completo"] and receipt["prova"]["preesistente_invariato"]
    assert tables(ti_db) == before_tables and legacy_rows(ti_db) == before_rows
    assert list(ti_db.parent.glob("*.pre-trade-idea-watch-*.bak")) == []


def test_apply_creates_table_with_backup_and_is_repeatable(ti_db):
    before_rows = legacy_rows(ti_db)
    receipt = migra_trade_idea_watch.migra(ti_db, apply=True)
    assert receipt["applicazione"]["preesistente_invariato"] and receipt["rilettura"]["schema_completo"]
    assert {"trade_idea_watch_triggers", "ti_watch_input_immutable", "ti_watch_no_delete",
            "ti_watch_terminal_status"} <= tables(ti_db)
    backups = list(ti_db.parent.glob("*.pre-trade-idea-watch-*.bak"))
    assert len(backups) == 1 and "trade_idea_watch_triggers" not in tables(backups[0])
    assert legacy_rows(ti_db) == before_rows
    again = migra_trade_idea_watch.migra(ti_db, apply=True)
    assert again["tabelle_mancanti"] == [] and again["applicazione"]["preesistente_invariato"]


def test_apply_refused_with_backend_alive(ti_db, monkeypatch):
    monkeypatch.setattr(migra_trade_idea_watch, "backend_alive", lambda: True)
    with pytest.raises(RuntimeError, match="8765"):
        migra_trade_idea_watch.migra(ti_db, apply=True)
    assert "trade_idea_watch_triggers" not in tables(ti_db)


def test_requires_trade_idea_tables_first(tmp_path):
    path = make_db(tmp_path / "legacy_only.db")
    with pytest.raises(ValueError, match="migra_trade_idea.py"):
        migra_trade_idea_watch.migra(path)


def test_incompatible_existing_table_refused(ti_db):
    with sqlite3.connect(ti_db) as conn:
        conn.execute("CREATE TABLE trade_idea_watch_triggers(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="incompatibile"):
        migra_trade_idea_watch.migra(ti_db)


def test_trade_idea_tables_untouched(ti_db):
    with sqlite3.connect(ti_db) as conn:
        before = conn.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name LIKE 'trade_idea_%' "
                              "AND tbl_name!='trade_idea_watch_triggers' ORDER BY name").fetchall()
    migra_trade_idea_watch.migra(ti_db, apply=True)
    with sqlite3.connect(ti_db) as conn:
        after = conn.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name LIKE 'trade_idea_%' "
                             "AND tbl_name!='trade_idea_watch_triggers' ORDER BY name").fetchall()
    assert before == after
