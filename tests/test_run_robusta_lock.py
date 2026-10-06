"""06/10/2026: un lock SQLite transitorio non uccide la run Trade Idea.

La run Trade Idea del 06/10 e' morta perche' un «database is locked» di ~25 s durante il desk quant
e' stato trattato come guasto della run. Regola PM: la run arriva in fondo, ogni
buco e' dichiarato, mai doppia spesa. Qui il lock lo tiene DAVVERO un secondo
processo (BEGIN IMMEDIATE su un DB tmp), non un mock dell'errore.
"""
import sqlite3
import subprocess
import sys

import pytest

from bellomberg.storage import trade_idea_store as tis
from test_trade_idea_store import db_path, migrated, request, store  # noqa: F401

_HOLDER = r'''
import sqlite3, sys, time
conn = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=30)
conn.execute("BEGIN IMMEDIATE")
print("LOCKED", flush=True)
hold = float(sys.argv[2])
if hold < 0:
    sys.stdin.readline()
else:
    time.sleep(hold)
conn.execute("COMMIT")
print("RELEASED", flush=True)
'''


class Holder:
    """Secondo processo che tiene il lock di scrittura (per N secondi o fino al rilascio)."""

    def __init__(self, path, seconds=-1.0):
        self.proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(path), str(seconds)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        line = self.proc.stdout.readline().strip()
        assert line == "LOCKED", line

    def release(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write("\n")
                self.proc.stdin.flush()
            except OSError:
                pass
        assert self.proc.wait(timeout=30) == 0


def _running(current, key):
    ident = current.create_run(request(), idempotency_key=key)["run"]["id"]
    return ident, current.claim_run(ident)


def _cost_rows(path, run_id):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT request_id,status FROM trade_idea_costs WHERE run_id=?",
                            (run_id,)).fetchall()


def test_reserve_cost_aspetta_il_lock_e_procede_una_sola_volta(migrated, capfd):
    current = store(migrated)
    ident, token = _running(current, "lock-wait")
    current.lock_wait_s, current.lock_busy_s = 20.0, 0.3
    holder = Holder(migrated, seconds=2.0)
    try:
        assert current.reserve_cost(ident, "req-lock-1", "specialist:quant", "meta/muse-spark-1.3", "0.5",
                                    worker_token=token) is True
    finally:
        holder.release()
    assert _cost_rows(migrated, ident) == [("req-lock-1", "reserved")]
    err = capfd.readouterr().err
    # Ogni attesa si misura: chi (pid), quale scrittura, quanto.
    assert "[LOCK]" in err and "scrittura=reserve_cost" in err and "riprovo" in err, err


def test_lock_oltre_il_tetto_dichiarato_nessuna_prenotazione_poi_una_sola(migrated, capfd):
    current = store(migrated)
    ident, token = _running(current, "lock-over")
    current.lock_wait_s, current.lock_busy_s = 1.2, 0.3
    holder = Holder(migrated)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            current.reserve_cost(ident, "req-lock-2", "specialist:quant", "meta/muse-spark-1.3", "0.5",
                                 worker_token=token)
        assert _cost_rows(migrated, ident) == []  # niente prenotato: nessuna richiesta pagabile
    finally:
        holder.release()
    err = capfd.readouterr().err
    assert "scrittura=reserve_cost DB occupato oltre il tetto" in err, err
    # Stesso request_id ritentato a DB libero: una prenotazione, e il replay non autorizza una seconda.
    assert current.reserve_cost(ident, "req-lock-2", "specialist:quant", "meta/muse-spark-1.3", "0.5",
                                worker_token=token) is True
    assert current.reserve_cost(ident, "req-lock-2", "specialist:quant", "meta/muse-spark-1.3", "0.5",
                                worker_token=token) is False
    assert _cost_rows(migrated, ident) == [("req-lock-2", "reserved")]


def test_store_storico_senza_tetto_non_ritenta(migrated):
    current = store(migrated)  # backend: lock_wait_s None, comportamento storico
    ident, token = _running(current, "lock-legacy")
    current.lock_busy_s = 0.3
    holder = Holder(migrated)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            current.update_progress(ident, token, "x", {"phase": "x"})
    finally:
        holder.release()


def test_errore_non_di_lock_non_si_ritenta(migrated, monkeypatch):
    current = store(migrated)
    current.lock_wait_s = 30.0
    calls = []

    @tis._scrittura_ritentata
    def broken(self):
        calls.append(1)
        raise sqlite3.OperationalError("no such table: trade_idea_costs")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        broken(current)
    assert calls == [1]


def test_lock_dopo_il_commit_non_ripete_la_scrittura(migrated, monkeypatch):
    """Una lettura successiva al COMMIT che trova il DB occupato non rifa' la scrittura."""
    current = store(migrated)
    ident, token = _running(current, "lock-post-commit")
    current.lock_wait_s, current.lock_busy_s = 20.0, 0.3
    real_get_run = current.get_run
    calls = []

    def get_run(run_id):
        calls.append(run_id)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(current, "get_run", get_run)
    with pytest.raises(sqlite3.OperationalError):
        current.finish_run(ident, token, None, "failed", reason="synthetic")
    assert calls == [ident]  # una sola scrittura, nessun nuovo tentativo dopo il commit
    assert real_get_run(ident)["run"]["technical_status"] == "failed"


def test_il_worker_costruisce_lo_store_con_i_tentativi(monkeypatch, tmp_path):
    from bellomberg.agents import trade_idea
    seen = {}

    class Fake:
        def __init__(self, path, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop after construction")

    monkeypatch.setattr(tis, "TradeIdeaStore", Fake)
    with pytest.raises(RuntimeError, match="stop after construction"):
        trade_idea.execute_trade_idea("run-x", db_path=tmp_path / "x.db")
    assert seen == {"lock_wait_s": tis.WORKER_LOCK_WAIT_S, "lock_busy_s": tis.WORKER_LOCK_BUSY_S}
    assert 60 <= tis.WORKER_LOCK_WAIT_S <= 120 and 30 <= tis.WORKER_LOCK_BUSY_S <= 60


# --- run intera offline (DB tmp, provider finto): il desk sotto lock e' una lacuna --------------
from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: E402,F401
from _smtp_cattura import smtp  # noqa: E402,F401
from test_trade_idea_source_research import frozen_clock  # noqa: E402,F401


def test_desk_sotto_lock_oltre_il_tetto_e_lacuna_e_la_run_arriva_in_fondo(no_workbook_case, monkeypatch, capfd):
    case = no_workbook_case
    current = case.current
    ident = current.create_run(case.request, idempotency_key="lock-desk-gap")["run"]["id"]
    current.lock_wait_s, current.lock_busy_s = 1.2, 0.3
    state = {"holder": None, "done": False, "failed_reservations": 0}
    real_reserve, real_progress = current.reserve_cost, current.update_progress

    def reserve_cost(run_id, request_id, role, *args, **kwargs):
        if role == "specialist:quant" and not state["done"] and state["holder"] is None:
            state["holder"] = Holder(case.database)  # il lock arriva sulla prenotazione del quant
            try:
                return real_reserve(run_id, request_id, role, *args, **kwargs)
            except sqlite3.OperationalError:
                state["failed_reservations"] += 1
                raise
        return real_reserve(run_id, request_id, role, *args, **kwargs)

    def update_progress(*args, **kwargs):
        if state["holder"] is not None and not state["done"]:
            try:  # il checkpoint del gestore cade sotto lo stesso lock: va rimandato, non ucciso
                return real_progress(*args, **kwargs)
            finally:
                state["holder"].release()
                state["done"] = True
        return real_progress(*args, **kwargs)

    monkeypatch.setattr(current, "reserve_cost", reserve_cost)
    monkeypatch.setattr(current, "update_progress", update_progress)
    detail = case.execute(ident, "lock-gap")
    assert state["failed_reservations"] == 1 and state["done"]
    assert detail["run"]["technical_status"] in ("completed", "incomplete"), detail["run"]["reason"]
    assert case.state["capo_calls"] == 1, detail["run"]["reason"]
    data = detail["progress"]["checkpoint"]["data"]
    assert set(data["_desk_gaps"]) == {"quant"}
    assert "Database occupato oltre il tetto" in data["_desk_gaps"]["quant"]["message"]
    assert not data.get("_primary_failure")
    # Nessuna prenotazione senza chiamata, nessuna chiamata senza prenotazione: niente doppia spesa.
    assert detail["cost"]["requests"] == len(case.providers)
    assert detail["cost"]["unknown_requests"] == 0
    err = capfd.readouterr().err
    assert "desk=quant" in err and "lacuna dichiarata per DB occupato" in err, err
    assert "checkpoint rimandato" in err and "checkpoint scritto dopo 1 rimandati" in err, err


def test_record_failure_sotto_lock_non_uccide_la_run(no_workbook_case, monkeypatch, capfd):
    case = no_workbook_case
    current = case.current
    case.state["stop_after"] = 1  # guasto di run vero dopo il checkpoint R1
    ident = current.create_run(case.request, idempotency_key="lock-record-failure")["run"]["id"]
    current.lock_wait_s, current.lock_busy_s = 1.2, 0.3
    real = current.record_failure
    state = {"calls": 0}

    def record_failure(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            holder = Holder(case.database)
            try:
                return real(*args, **kwargs)
            finally:
                holder.release()
        return real(*args, **kwargs)

    monkeypatch.setattr(current, "record_failure", record_failure)
    detail = case.execute(ident, "lock-failure")
    # La run si chiude in modo controllato (parziale etichettato), il gestore non e' caduto.
    assert detail["run"]["technical_status"] == "incomplete", detail["run"]
    assert "Intentional offline crash" in (detail["run"]["reason"] or "")
    assert state["calls"] >= 2  # rimandato e poi riscritto a DB libero
    with sqlite3.connect(case.database) as conn:
        rows = [r[0] for r in conn.execute(
            "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='execution_failure'", (ident,))]
    assert any("Intentional offline crash" in row for row in rows), rows
    err = capfd.readouterr().err
    assert "guasto non registrabile ora" in err and "guasto rimandato ora registrato" in err, err


def test_risposta_pagata_non_registrata_sotto_lock_resta_guasto_della_run(no_workbook_case, monkeypatch):
    """La ricevuta di una risposta GIA' PAGATA che non si scrive non e' mai una lacuna di desk."""
    import time
    from bellomberg.agents import trade_idea
    case = no_workbook_case
    current = case.current
    ident = current.create_run(case.request, idempotency_key="lock-paid")["run"]["id"]
    current.lock_wait_s, current.lock_busy_s = 1.2, 0.3
    monkeypatch.setattr(time, "sleep", lambda seconds: None)  # i 6 tentativi di _durable senza pause
    state = {"holder": None, "tagged": None}
    real_settle = current._settle_cost
    real_durable = trade_idea.TradeIdeaBudgetGate._durable

    def settle(run_id, request_id, status, **kwargs):
        if status == "charged" and state["holder"] is None:
            state["holder"] = Holder(case.database)
        return real_settle(run_id, request_id, status, **kwargs)

    def durable(self, write, *args, **kwargs):
        try:
            return real_durable(self, write, *args, **kwargs)
        except sqlite3.OperationalError as exc:
            state["tagged"] = getattr(exc, "pagata_non_registrata", None)
            if state["holder"] is not None:
                state["holder"].release()
            raise

    monkeypatch.setattr(current, "_settle_cost", settle)
    monkeypatch.setattr(trade_idea.TradeIdeaBudgetGate, "_durable", durable)
    detail = case.execute(ident, "lock-paid")
    assert state["tagged"] is True
    first = detail["progress"]["primary_failure"]
    assert first and first["exception_type"] == "OperationalError", first
    assert detail["run"]["technical_status"] != "completed"
    assert case.state["capo_calls"] == 0
