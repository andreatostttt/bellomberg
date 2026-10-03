"""Install additive Trade Idea run, cost and delivery tables.

Default is a read-only dry-run on a transactionally copied database. --apply
requires a free backend port, a verified backup and unchanged source state.
No personal row, decision, position or economic record is backfilled.
"""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bellomberg.storage import trade_idea_store
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
from tools.migrations import migra_method_records as _archive


TABLES = trade_idea_store.TABLES


def backend_alive():
    return _archive.backend_alive()


def _target_schema(conn):
    names = {kind + ":" + name for kind, name, table in conn.execute(
        "SELECT type,name,tbl_name FROM sqlite_master") if table in TABLES}
    return {key: value for key, value in schema_fingerprint(conn).items() if key in names}


def _expected_schema():
    with closing(sqlite3.connect(":memory:")) as conn:
        # Referenced legacy tables exist on the real source before Trade Idea DDL.
        conn.execute("CREATE TABLE decisions(id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE memos(id INTEGER PRIMARY KEY)")
        trade_idea_store.ensure_schema(conn)
        return _target_schema(conn)


def _validate(conn, expected, *, complete=False):
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("DB quick_check fallito")
    required = {
        "decisions": {"id", "timestamp", "action", "ticker", "eur_amount", "timing",
                      "confidence", "rationale", "status", "pm_feedback", "veto",
                      "veto_reason", "veto_at", "veto_revoked_at", "outcome_pct",
                      "outcome_eur", "outcome_notes", "closed_at", "archive_override"},
        "positions": {"ticker", "quantita", "prezzo_medio", "valuta", "is_active", "last_updated"},
        "position_prices": {"id", "ticker", "prezzo", "valuta", "source", "timestamp"},
        "memos": {"id", "timestamp", "title", "full_markdown", "pdf_path",
                  "dcf_files", "notes", "output_language"},
        "cash_state": {"singleton_id", "balance_cents", "version"},
        "trade_history": {"id", "ticker", "action", "quantita", "data", "linked_decision_id"},
        "decision_notes": {"id", "decision_id", "autore", "testo", "timestamp"},
        "pm_feedback": {"id", "decision_id", "feedback_text", "sentiment"},
    }
    for table, columns in required.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not columns <= existing:
            raise ValueError(f"schema precedente mancante/incompatibile: {table}")
    actual = _target_schema(conn)
    if any(expected.get(key) != value for key, value in actual.items()) or (complete and actual != expected):
        raise ValueError("schema Trade Idea incompatibile")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign key check fallito")


def _apply(path, before, schema_before, expected):
    counter = _archive.Statements()
    with closing(_archive.apri(path, sola_lettura=False)) as conn:
        conn.set_trace_callback(counter.trace)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        initial_changes = conn.total_changes
        try:
            if fingerprint(conn) != before or schema_fingerprint(conn) != schema_before:
                raise ValueError("DB variato dopo preflight/backup: migrazione non applicata")
            _validate(conn, expected)
            trade_idea_store.ensure_schema(conn)
            _validate(conn, expected, complete=True)
            after = fingerprint(conn)
            after_schema = schema_fingerprint(conn)
            unchanged = all(after.get(name) == value for name, value in before.items()) and all(
                after_schema.get(name) == value for name, value in schema_before.items())
            if not unchanged:
                raise ValueError("dati/schema preesistenti cambiati: rollback")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return {"schema_completo": True, "preesistente_invariato": unchanged,
                "righe_cambiate": conn.total_changes - initial_changes,
                "scritture": _archive.conteggio(counter)}


def _rileggi(path, before, schema_before, expected):
    with closing(_archive.apri(path, sola_lettura=True)) as conn:
        _validate(conn, expected, complete=True)
        after = fingerprint(conn)
        schema_after = schema_fingerprint(conn)
        unchanged = all(after.get(name) == value for name, value in before.items()) and all(
            schema_after.get(name) == value for name, value in schema_before.items())
    if not unchanged:
        raise ValueError("rilettura: righe o schema preesistenti diversi")
    return {"schema_completo": True, "preesistente_invariato": True}


def migra(db_path, *, apply=False):
    path = _archive.richiedi_wal(db_path)
    expected = _expected_schema()
    counter = _archive.Statements()
    backup = None
    with tempfile.TemporaryDirectory(prefix="bellomberg_trade_idea_", ignore_cleanup_errors=True) as work:
        rehearsal = Path(work) / "rehearsal.db"
        with closing(_archive.apri(path, sola_lettura=True)) as conn:
            conn.set_trace_callback(counter.trace)
            conn.execute("BEGIN")
            _validate(conn, expected)
            before, schema_before = fingerprint(conn), schema_fingerprint(conn)
            if apply and backend_alive():
                raise RuntimeError("porta 8765 occupata: chiudere il backend prima della migrazione")
            if apply:
                backup = path.with_name(path.name + ".pre-trade-idea-" +
                                        datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
                backup.touch(exist_ok=False)
            for destination in (rehearsal, *([backup] if backup else [])):
                with closing(sqlite3.connect(destination)) as copy:
                    conn.backup(copy)
                    _validate(copy, expected)
                    if fingerprint(copy) != before or schema_fingerprint(copy) != schema_before:
                        raise ValueError("backup diverso dal preflight")
            conn.execute("ROLLBACK")
            source_changes = conn.total_changes
        with closing(_archive.apri(path, sola_lettura=True)) as conn:
            source_unchanged = fingerprint(conn) == before and schema_fingerprint(conn) == schema_before
        result = {"modo": "apply" if apply else "dry-run", "db": str(path),
                  "backup": str(backup) if backup else None,
                  "tabelle_mancanti": sorted(set(TABLES) - set(before)),
                  "prima": before,
                  "scritture_sorgente": {**_archive.conteggio(counter),
                                       "total_changes": source_changes},
                  "sorgente_invariata": source_unchanged,
                  "prova": _apply(rehearsal, before, schema_before, expected)}
    if not apply:
        return result
    if not source_unchanged:
        raise ValueError("DB variato durante preflight: nessun apply")
    if backend_alive():
        raise RuntimeError("porta 8765 occupata dopo la prova: nessun apply")
    result["applicazione"] = _apply(path, before, schema_before, expected)
    result["rilettura"] = _rileggi(path, before, schema_before, expected)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.db is None:
        from bellomberg.storage import memory_db
        args.db = memory_db.SQLITE_PATH
    print(json.dumps(migra(args.db, apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
