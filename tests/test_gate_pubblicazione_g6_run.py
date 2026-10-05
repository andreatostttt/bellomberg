# -*- coding: utf-8 -*-
"""Gate di pubblicazione sulla run VERA (run_offline): il registro occupato all'auto-chiusura
non blocca la consegna, la finalizzazione ritenta e chiude, il guasto che resta arriva nell'email.
Adattato dalle prove F1/F1b del revisore REV2_G6 (04/10). Ticker inventati ZZ*."""
import json
import os
import sqlite3
import sys

import pytest

from test_cablaggio_consigliere_multi import run_offline, _html  # noqa: F401
from test_weekly_research_without_workbook import research_weekly  # noqa: F401
from bellomberg.agents import consigliere_multi as cm
from bellomberg.storage import memory_db
from bellomberg.storage.weekly_run_store import WeeklyRunStore

MEMO = ("# Memo\n\n## ACTION TABLE\n| Action | Ticker | EUR | Timing | Confidence | Rationale |\n"
        "|---|---|---|---|---|---|\n"
        "| BUY | ZZOK | 5000 | ora | ALTA | valida |\n"
        "| BUY | ZZBLK | 3000 | ora | ALTA | su block |\n"
        "| TRIM | ZZWRN | 2000 | ora | MEDIA | trim |\n"
        "\n## 2. Analisi\n" + "testo " * 40 + "\n")
_ORIG_EXTRACT = memory_db.MemoryDB.extract_and_save_decisions


def _sidecar(report, ticker, severity):
    name = "VAL_" + ticker + ("_FLAGGED.payload.json" if severity == "BLOCK" else ".payload.json")
    with open(os.path.join(report, name), "w", encoding="utf-8") as f:
        json.dump({"ticker": ticker, "_timestamp": "2026-09-30T10:00:00",
                   "sanity": {"severity": severity, "headline": "giudizio di prova"}}, f)


@pytest.fixture
def vero(research_weekly, run_offline, monkeypatch, tmp_path):
    import importlib
    nome = "bellomberg.agents.action_validator"
    sys.modules.pop(nome, None)
    monkeypatch.setitem(sys.modules, nome, importlib.import_module(nome))
    monkeypatch.setattr(memory_db.MemoryDB, "extract_and_save_decisions", _ORIG_EXTRACT)
    monkeypatch.setattr(cm, "run_capo", lambda bb, **k: (MEMO, {
        "model": "m/finto", "in": 10, "out": 10, "input_tokens": 10, "output_tokens": 10,
        "api_calls": 1, "complete": True, "stop_reason": "end_turn"}))
    monkeypatch.setattr(cm.time, "sleep", lambda *_: None)
    # i grafici del PDF istituzionale scrivono in charts_institutional.DIR, fissato all'import:
    # se il modulo e' gia' importato da un altro test punterebbe a report/ dell'albero
    import bellomberg.reporting.charts_institutional as ci
    monkeypatch.setattr(ci, "DIR", str(tmp_path / "inst_charts"))
    rep = str(tmp_path / "report")
    _sidecar(rep, "ZZOK", "OK")
    _sidecar(rep, "ZZBLK", "BLOCK")
    return rep


def _bloccato(monkeypatch, quante):
    from bellomberg.agents import action_validator as av
    real = av.apply_sanity_exclusions
    n = {"k": 0}

    def locked(db, mid, pairs, **kw):
        n["k"] += 1
        if n["k"] <= quante:
            raise sqlite3.OperationalError("database is locked")
        return real(db, mid, pairs, **kw)
    monkeypatch.setattr(av, "apply_sanity_exclusions", locked)


def _stato():
    db = memory_db.MemoryDB()
    with db._conn() as conn:
        memo_id = conn.execute("SELECT MAX(memo_id) FROM weekly_runs").fetchone()[0]
        reg = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT ticker,status,assessment_status FROM decisions WHERE memo_id=? AND status!='EXPIRED'",
            (memo_id,))}
    return WeeklyRunStore(db, memo_id), reg


def test_f1b_auto_chiusura_occupata_al_gate_la_finalizzazione_ritenta_e_chiude(run_offline, vero, monkeypatch):
    _bloccato(monkeypatch, cm._RITENTATIVI_TRANSITORI)      # il gate esaurisce i tentativi
    cm.run_multi_agent(send_email=True)
    store, reg = _stato()
    fin = store.get("decisions_finalized")
    assert len(run_offline.inviati) == 1 and fin["error"] is None
    assert reg["ZZBLK"] == ("SKIPPED", "BLOCKED") and reg["ZZOK"] == ("PENDING", "OPERATIVE")


def test_f1_auto_chiusura_mai_riuscita_consegna_e_lo_dichiara(run_offline, vero, monkeypatch):
    _bloccato(monkeypatch, 10 ** 6)
    cm.run_multi_agent(send_email=True)
    store, reg = _stato()
    fin = store.get("decisions_finalized")
    assert len(run_offline.inviati) == 1
    assert fin["error"] and "locked" in fin["error"]
    assert reg["ZZBLK"] == ("PENDING", "BLOCKED")
    assert "auto-chiusura nel registro non riuscita" in _html(run_offline.inviati[0])
    zzblk = next(d for d in store.db.get_recent_decisions(10) if d["ticker"] == "ZZBLK")
    with pytest.raises(ValueError):
        store.db.update_decision(zzblk["id"], status="EXECUTED")
