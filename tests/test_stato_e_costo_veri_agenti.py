"""Agenti in diretta: stato di pagina dall'ESITO vero della run e costo misurato sul registro
richieste (handoff-4 voce 2, 05/10/2026, Opus 5.5).

Difetto A: una run ferma (operational_status blocked, memo '[IN PROGRESS]') si leggeva
«Completata · il comitato ha consegnato il memo», perche' la pagina deduceva la fine da
running=false + memo_id. Ora GET /agents/live porta `esito_run` (specialists/base.py).
Difetto B: due righe `_red_team` api_error di tentativi falliti SENZA richieste nel
registro rendevano «costo PARZIALE» un totale completo. La misura si fa sul registro.

Solo run finte: heartbeat e DB in tmp_path, ticker/numeri inventati, nessuna chiamata AI.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from bellomberg.agents.specialists import base
from bellomberg.agents.specialists.base import Blackboard


# ---------------------------------------------------------------------------
# fixture comuni
# ---------------------------------------------------------------------------

def _db_memo(tmp_path, righe):
    """DB finto con la sola tabella memos: {id: full_markdown}."""
    p = tmp_path / "finto.db"
    with sqlite3.connect(str(p)) as cx:
        cx.execute("CREATE TABLE memos (id INTEGER PRIMARY KEY, full_markdown TEXT)")
        for memo_id, testo in righe.items():
            cx.execute("INSERT INTO memos VALUES (?,?)", (memo_id, testo))
    return p


def _api(tmp_path, monkeypatch, heartbeat, db_path=None):
    import bellomberg.api.bellomberg_api as api
    p = tmp_path / "current_run.json"
    if heartbeat is not None:
        p.write_text(json.dumps(heartbeat), encoding="utf-8")
    monkeypatch.setattr(api, "AGENTS_LIVE_PATH", str(p))
    monkeypatch.setattr(api, "SQLITE_PATH", str(db_path or (tmp_path / "manca.db")))
    return api


def _adesso(delta_s=0):
    return (datetime.now() - timedelta(seconds=delta_s)).isoformat(timespec="seconds")


# heartbeat terminale della run settimanale come lo scrive _weekly_terminal_heartbeat:
# il registro (store.status()) fuso sopra l'ultimo heartbeat della Blackboard
FERMA = {
    "run_scope": "weekly", "running": False, "memo_id": 9001,
    "start_time": "2026-01-02T10:00:00", "completed_at": None,
    "technical_status": "completed",    # avanzo di un heartbeat precedente: NON e' l'esito
    "status": "incomplete", "operational_status": "blocked",
    "analytical_status": "incomplete", "artifact_status": "pending",
    "resume_available": True, "blocked_reason": None,
    "last_error": {"message": "requested completion cap exceeds provider output limit",
                   "phase": "red_team", "desk": "red_team"},
    "message": "requested completion cap exceeds provider output limit",
}
COMPLETATA = {
    "run_scope": "weekly", "running": False, "memo_id": 9002,
    "start_time": "2026-01-02T10:00:00", "completed_at": "2026-01-02T10:40:00",
    "technical_status": "completed", "status": "completed",
    "operational_status": "requires_pm_review", "analytical_status": "complete",
    "artifact_status": "available", "resume_available": False, "blocked_reason": None,
    "last_error": None, "message": "Analisi e artefatti verificati",
}


# ---------------------------------------------------------------------------
# Difetto A — esito_run da GET /agents/live
# ---------------------------------------------------------------------------

def test_run_ferma_con_memo_in_progress_non_e_completata(tmp_path, monkeypatch):
    db = _db_memo(tmp_path, {9001: "[IN PROGRESS]"})
    api = _api(tmp_path, monkeypatch, FERMA, db)
    r = api.get_agents_live()
    e = r["esito_run"]
    assert e["stato"] == "bloccata", e
    assert e["memo_consegnato"] is False
    assert e["fonte"] == "registro_run_settimanale"
    assert "cap exceeds" in e["motivo"]
    assert e["ripresa_disponibile"] is True
    assert e["stato_memo_db"] == "in_corso"


def test_run_incompleta_non_bloccata_si_chiama_incompleta(tmp_path, monkeypatch):
    hb = {**FERMA, "operational_status": "requires_pm_review", "analytical_status": "complete",
          "artifact_status": "available", "last_error": None,
          "message": "Consegna richiesta non confermata"}
    db = _db_memo(tmp_path, {9001: "# Memo ZZTEST"})
    e = _api(tmp_path, monkeypatch, hb, db).get_agents_live()["esito_run"]
    assert e["stato"] == "incompleta" and e["memo_consegnato"] is True
    assert e["motivo"] == "Consegna richiesta non confermata"


def test_run_completata_vera(tmp_path, monkeypatch):
    db = _db_memo(tmp_path, {9002: "# Memo ZZTEST completo"})
    e = _api(tmp_path, monkeypatch, COMPLETATA, db).get_agents_live()["esito_run"]
    assert e == {"stato": "completata", "memo_consegnato": True, "motivo": None,
                 "ripresa_disponibile": False, "ripresa": None,
                 "fonte": "registro_run_settimanale", "stato_memo_db": "scritto"}


def test_completata_ma_memo_ancora_in_progress_nel_db_non_e_completata(tmp_path, monkeypatch):
    db = _db_memo(tmp_path, {9002: "[IN PROGRESS]"})
    e = _api(tmp_path, monkeypatch, COMPLETATA, db).get_agents_live()["esito_run"]
    assert e["stato"] == "sconosciuta" and e["memo_consegnato"] is False
    assert "[IN PROGRESS]" in e["motivo"]


def test_completata_con_memo_assente_dal_db_dichiara_il_buco(tmp_path, monkeypatch):
    db = _db_memo(tmp_path, {})
    e = _api(tmp_path, monkeypatch, COMPLETATA, db).get_agents_live()["esito_run"]
    assert e["stato"] == "completata" and e["memo_consegnato"] is None
    assert "assente" in e["motivo"]


def test_db_illeggibile_dichiarato(tmp_path, monkeypatch):
    e = _api(tmp_path, monkeypatch, COMPLETATA, tmp_path / "non_esiste.db").get_agents_live()["esito_run"]
    assert e["stato_memo_db"] == "illeggibile" and e["memo_consegnato"] is None


def test_heartbeat_vecchio_senza_esito_e_sconosciuto_non_completato(tmp_path, monkeypatch):
    hb = {"running": False, "memo_id": 9002, "start_time": "2026-01-02T10:00:00",
          "completed_at": "2026-01-02T10:40:00"}
    db = _db_memo(tmp_path, {9002: "# Memo ZZTEST"})
    e = _api(tmp_path, monkeypatch, hb, db).get_agents_live()["esito_run"]
    assert e["stato"] == "sconosciuta" and e["memo_consegnato"] is None
    assert "senza esito" in e["motivo"]


@pytest.mark.parametrize("ritardo, atteso", [(5, "in_corso"), (900, "in_corso_senza_segnale")])
def test_run_viva(tmp_path, monkeypatch, ritardo, atteso):
    # pid del processo che scrive (questo) e avvio dopo la sua nascita: processo verificato vivo
    hb = {"running": True, "memo_id": 9003, "current_round": 1, "updated_at": _adesso(ritardo),
          "start_time": _adesso(0), "pid": os.getpid()}
    e = _api(tmp_path, monkeypatch, hb).get_agents_live()["esito_run"]
    assert e["stato"] == atteso and e["memo_consegnato"] is False
    assert e["stato_memo_db"] == "non_letto"   # a run viva il memo non si interroga


def test_heartbeat_di_crash_e_fallita(tmp_path, monkeypatch):
    hb = {"running": False, "status": "failed", "finished_at": "2026-01-02T10:05:00",
          "message": "Run TERMINATA con errore (vedi log): ZZTEST esploso"}
    e = _api(tmp_path, monkeypatch, hb).get_agents_live()["esito_run"]
    assert e["stato"] == "fallita" and e["memo_consegnato"] is False
    assert "ZZTEST esploso" in e["motivo"]


@pytest.mark.parametrize("tecnico, atteso", [("completed", "completata"), ("incomplete", "incompleta"),
                                             ("failed", "fallita"), ("cancelled", "annullata"),
                                             ("interrupted", "annullata"), ("boh", "sconosciuta")])
def test_trade_idea_technical_status(tmp_path, monkeypatch, tecnico, atteso):
    hb = {"run_scope": "trade_idea", "running": False, "technical_status": tecnico,
          "reason": "motivo ZZTEST", "memo_id": None}
    e = _api(tmp_path, monkeypatch, hb).get_agents_live()["esito_run"]
    assert e["stato"] == atteso
    assert e["memo_consegnato"] is (tecnico == "completed")


def test_nessun_heartbeat_e_nessuna_run(tmp_path, monkeypatch):
    r = _api(tmp_path, monkeypatch, None).get_agents_live()
    assert r["running"] is False and r["esito_run"]["stato"] == "nessuna_run"


def test_heartbeat_non_oggetto_e_illeggibile_con_esito(tmp_path, monkeypatch):
    p = tmp_path / "current_run.json"
    api = _api(tmp_path, monkeypatch, None)
    p.write_text("[1, 2]", encoding="utf-8")
    r = api.get_agents_live()
    assert r["heartbeat"] == "illeggibile" and r["esito_run"]["stato"] == "sconosciuta"


def test_blackboard_vera_chiusa_incompleta_arriva_alla_pagina(tmp_path, monkeypatch):
    hb = tmp_path / "current_run.json"
    bb = Blackboard(heartbeat_path=hb, memo_id=9004)
    bb.mark_run_complete(technical_status="incomplete", reason="Capo non completo ZZTEST")
    db = _db_memo(tmp_path, {9004: "[IN PROGRESS]"})
    api = _api(tmp_path, monkeypatch, None, db)
    e = api.get_agents_live()["esito_run"]
    assert e["stato"] == "incompleta" and e["memo_consegnato"] is False
    assert e["motivo"] == "Capo non completo ZZTEST"


def test_valori_chiusi():
    for hb in (FERMA, COMPLETATA, {"running": True}, {}, None, {"status": "failed"},
               {"technical_status": "x"}, {"operational_status": "blocked", "status": "ready"}):
        e = base.esito_run(hb)
        assert e["stato"] in base.ESITI_RUN
        assert e["memo_consegnato"] in (True, False, None)


# ---------------------------------------------------------------------------
# round_esiti: Round 0 ripreso da checkpoint non e' «nessuna chiamata»
# ---------------------------------------------------------------------------

def test_round_esiti_ripresa():
    log = [{"agent": "macro", "round": 0, "ts": "2026-01-02T09:00:00"},
           {"agent": "quant", "round": 0, "ts": "2026-01-02T09:01:00"},
           {"agent": "macro", "round": 1, "ts": "2026-01-02T09:30:00"},
           {"agent": "quant", "round": 1, "ts": "2026-01-02T10:05:00"},
           {"agent": "quant", "round": 2, "ts": "2026-01-02T10:20:00"},
           {"agent": "_red_team", "round": 1, "ts": "2026-01-02T10:10:00"},
           {"agent": "capo", "round": 3, "ts": "2026-01-02T10:30:00"}]
    r = base.round_esiti(log, "2026-01-02T10:00:00")
    assert r["0"] == {"stato": "ripreso", "desk_eseguiti": [], "desk_ripresi": ["macro", "quant"],
                      "desk_senza_orario": []}
    assert r["1"]["stato"] == "misto" and r["1"]["desk_ripresi"] == ["macro"]
    assert r["2"]["stato"] == "eseguito" and r["2"]["desk_eseguiti"] == ["quant"]
    assert base.round_esiti([], "2026-01-02T10:00:00")["0"]["stato"] == "assente"
    assert base.round_esiti([{"agent": "macro", "round": 0}], "x")["0"]["stato"] == "n.d."


def test_round_esiti_nel_heartbeat_e_ripresa_nell_esito(tmp_path):
    hb = tmp_path / "current_run.json"
    bb = Blackboard(heartbeat_path=hb, memo_id=9005)
    bb.usage_log.append({"agent": "macro", "round": 0, "ts": "2000-01-01T00:00:00",
                         "cost_eur": 0.5, "in": 1, "out": 1, "cache_read": 0, "cache_write": 0})
    bb._write_heartbeat()
    stato = json.loads(hb.read_text(encoding="utf-8"))
    assert stato["round_esiti"]["0"]["stato"] == "ripreso"
    assert base.esito_run(stato)["ripresa"] is True


# ---------------------------------------------------------------------------
# Difetto B — costo parziale misurato sul registro richieste
# ---------------------------------------------------------------------------

def _registro(tmp_path, righe):
    """Registro richieste vero (schema di RequestJournal), righe inserite a mano:
    (request_id, state, cost_nano_usd|None, agent, round_n)."""
    from bellomberg.core.request_journal import RequestJournal
    p = tmp_path / "weekly-zztest-requests.sqlite"
    RequestJournal(p, run_id="zztest-run", authorization={"scope": "test"})
    with sqlite3.connect(str(p)) as cx:
        for i, (rid, state, cost, agent, rn) in enumerate(righe):
            cx.execute("INSERT INTO requests(key,request_id,state,reserved,cost,request,scope,receipt) "
                       "VALUES(?,?,?,?,?,?,?,?)",
                       ("k" + str(i), rid, state, 1000, cost, "{}",
                        json.dumps({"phase": "red_team", "agent": agent, "round_n": rn}), "{}"))
    return SimpleNamespace(path=p)


def _riga(agent, rnd, *, cost, status="ok", ids=(), tin=10, tout=4):
    return {"agent": agent, "round": rnd, "model": "zz/finto", "in": tin, "out": tout,
            "cache_read": 0, "cache_write": 0, "api_calls": 1, "duration_s": 1.0,
            "cost_eur": cost, "fx_source": "live", "status": status, "request_ids": list(ids),
            "ts": "2026-01-02T10:10:00"}


def _bb_red_team(tmp_path, journal):
    """Il caso del memo #67 con numeri inventati: due tentativi falliti prima di qualunque
    richiesta (uno con costo ignoto, uno a zero) e il terzo riuscito con due richieste."""
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json", memo_id=9006)
    if journal is not None:
        bb.request_journal = journal
    bb.usage_log += [
        _riga("quant", 1, cost=1.25, ids=["q1"]),
        _riga("_red_team", 1, cost=None, status="api_error", tin=None, tout=None),
        _riga("_red_team", 1, cost=0.0, status="api_error", tin=0, tout=0),
        _riga("_red_team", 1, cost=0.25, ids=["r1", "r2"]),
    ]
    return bb


RICHIESTE_OK = [("q1", "received", 1_000_000, "quant", 1),
                ("r1", "received", 100_000, "_red_team", 1),
                ("r2", "received", 120_000, "_red_team", 1)]


def test_tentativi_senza_richieste_non_rendono_parziale_il_totale(tmp_path):
    bb = _bb_red_team(tmp_path, _registro(tmp_path, RICHIESTE_OK))
    by, tot = bb._usage_aggregates()
    assert tot["partial"] is False and tot["unpriced_agents"] == []
    assert tot["cost_eur"] == pytest.approx(1.5)
    assert tot["tokens_status"] == "completo"
    assert tot["misura_richieste"] == "journal"
    assert [t["agent"] for t in tot["tentativi_senza_richieste"]] == ["_red_team"]
    assert by["_red_team"]["partial"] is False and by["_red_team"]["cost_eur"] == pytest.approx(0.25)
    assert by["_red_team"]["tentativi_senza_richieste"] == 1
    # seguito V2 (PM 05/10): fallito due volte e poi RIUSCITO non e' un KO; la storia resta dichiarata
    assert tot["error_agents"] == []
    assert tot["tentativi_falliti_poi_riusciti"] == [
        {"agent": "_red_team", "round": 1, "tentativi_falliti": 2}]
    assert by["_red_team"]["status"] == "api_error"          # storia: il peggiore, invariato
    assert by["_red_team"]["status_finale"] == "ok"


def test_richiesta_scoperta_nel_registro_lascia_il_totale_parziale(tmp_path):
    righe = RICHIESTE_OK + [("r0", "unknown", None, "_red_team", 1)]
    bb = _bb_red_team(tmp_path, _registro(tmp_path, righe))
    _by, tot = bb._usage_aggregates()
    assert tot["partial"] is True and tot["unpriced_agents"] == ["_red_team"]
    assert tot["tentativi_senza_richieste"] == [] and tot["misura_richieste"] == "journal"


def test_spesa_nota_non_attribuita_lascia_il_totale_parziale(tmp_path):
    righe = RICHIESTE_OK + [("r0", "received", 50_000, "_red_team", 1)]
    _by, tot = _bb_red_team(tmp_path, _registro(tmp_path, righe))._usage_aggregates()
    assert tot["partial"] is True


def test_richiesta_rilasciata_a_costo_zero_non_conta(tmp_path):
    righe = RICHIESTE_OK + [("r0", "released", 0, "_red_team", 1)]
    _by, tot = _bb_red_team(tmp_path, _registro(tmp_path, righe))._usage_aggregates()
    assert tot["partial"] is False


def test_scoperta_di_un_altro_agente_tiene_parziale_anche_il_red_team(tmp_path):
    """R-2 F1/F2 (06/10): la copertura si misura sulla RUN intera — un costo incerto di
    qualunque agente vieta di dichiarare «senza richieste» i tentativi di chiunque."""
    righe = RICHIESTE_OK + [("x9", "unknown", None, "macro", 0)]
    _by, tot = _bb_red_team(tmp_path, _registro(tmp_path, righe))._usage_aggregates()
    assert tot["partial"] is True and tot["tentativi_senza_richieste"] == []


def test_senza_registro_resta_parziale_e_lo_dice(tmp_path):
    _by, tot = _bb_red_team(tmp_path, None)._usage_aggregates()
    assert tot["partial"] is True and tot["misura_richieste"] == "assente"
    assert tot["unpriced_agents"] == ["_red_team"]


def test_registro_illeggibile_resta_parziale_e_lo_dice(tmp_path):
    bb = _bb_red_team(tmp_path, SimpleNamespace(path=tmp_path / "non_esiste.sqlite"))
    _by, tot = bb._usage_aggregates()
    assert tot["partial"] is True and tot["misura_richieste"] == "illeggibile"
    assert tot["misura_richieste_errore"] == "FileNotFoundError"


def test_riga_con_richieste_e_costo_ignoto_resta_parziale(tmp_path):
    righe = RICHIESTE_OK + [("r9", "unknown", None, "_red_team", 1)]
    bb = _bb_red_team(tmp_path, _registro(tmp_path, righe))
    bb.usage_log.append(_riga("_red_team", 1, cost=None, status="api_error", ids=["r9"],
                              tin=None, tout=None))
    _by, tot = bb._usage_aggregates()
    assert tot["partial"] is True and "_red_team" in tot["unpriced_agents"]


def test_usage_unknown_senza_richieste_non_e_esente(tmp_path):
    bb = _bb_red_team(tmp_path, _registro(tmp_path, RICHIESTE_OK))
    bb.usage_log.append(_riga("macro", 0, cost=None, status="usage_unknown", tin=None))
    _by, tot = bb._usage_aggregates()
    assert tot["partial"] is True and tot["unpriced_agents"] == ["macro"]


def test_nessun_candidato_non_apre_il_registro(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.request_journal = SimpleNamespace(path=tmp_path / "non_esiste.sqlite")
    bb.usage_log.append(_riga("quant", 1, cost=1.0, ids=["q1"]))
    _by, tot = bb._usage_aggregates()
    assert tot["misura_richieste"] == "non_necessaria" and tot["partial"] is False


def test_heartbeat_e_pagina_dicono_costo_completo(tmp_path, monkeypatch):
    bb = _bb_red_team(tmp_path, _registro(tmp_path, RICHIESTE_OK))
    bb.mark_run_complete(technical_status="completed")
    api = _api(tmp_path, monkeypatch, None, _db_memo(tmp_path, {9006: "# Memo ZZTEST"}))
    r = api.get_agents_live()
    assert r["usage_total"]["partial"] is False and r["usage_total"]["unpriced_agents"] == []
    assert r["usage_total"]["misura_richieste"] == "journal"
    assert r["esito_run"]["stato"] == "completata"


# ---------------------------------------------------------------------------
# Seguito 1 — error_agents = esito FINALE; i tentativi falliti poi riusciti sono storia
# ---------------------------------------------------------------------------

def test_ko_finale_resta_ko(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.usage_log += [_riga("macro", 1, cost=0.5, ids=["m1"]),
                     _riga("macro", 1, cost=None, status="api_error", tin=None, tout=None)]
    by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == ["macro"] and tot["tentativi_falliti_poi_riusciti"] == []
    assert by["macro"]["status_finale"] == "api_error"


def test_due_tentativi_falliti_non_sono_poi_riusciti(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.usage_log += [_riga("eventdesk", 1, cost=None, status="api_error", tin=None, tout=None),
                     _riga("eventdesk", 1, cost=None, status="api_error", tin=None, tout=None)]
    by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == ["eventdesk"] and tot["tentativi_falliti_poi_riusciti"] == []
    assert by["eventdesk"]["tentativi_falliti_poi_riusciti"] == 0


def test_un_round_ko_e_un_altro_ok_resta_ko(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.usage_log += [_riga("quant", 1, cost=None, status="api_error", tin=None, tout=None),
                     _riga("quant", 2, cost=0.5, ids=["q2"])]
    by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == ["quant"] and tot["tentativi_falliti_poi_riusciti"] == []


def test_usage_unknown_finale_resta_in_error_agents(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.usage_log += [_riga("crypto", 0, cost=None, status="usage_unknown", tin=None)]
    _by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == ["crypto"]


def test_ripresa_fallita_poi_riuscita_su_due_round(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.usage_log += [_riga("options", 1, cost=None, status="api_error", tin=None, tout=None),
                     _riga("options", 1, cost=0.3, ids=["o1"]),
                     _riga("options", 2, cost=0.2, ids=["o2"])]
    by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == []
    assert tot["tentativi_falliti_poi_riusciti"] == [{"agent": "options", "round": 1, "tentativi_falliti": 1}]
    assert by["options"]["tentativi_falliti_poi_riusciti"] == 1


# ---------------------------------------------------------------------------
# Seguito 2 — heartbeat finale settimanale: la lavagna di un'ALTRA run non si spaccia per questa
# ---------------------------------------------------------------------------

def _store_finto(run_id, memo_id):
    stato = {"memo_id": memo_id, "run_id": run_id, "status": "incomplete",
             "operational_status": "blocked", "analytical_status": "incomplete",
             "artifact_status": "pending", "resume_available": False,
             "last_error": {"message": "ZZTEST morta prima della lavagna"}}
    return SimpleNamespace(run_id=run_id, memo_id=memo_id, status=lambda: dict(stato))


LAVAGNA_VECCHIA = {"run_scope": "weekly", "run_id": "run-vecchia", "memo_id": 8001,
                   "running": False, "start_time": "2026-01-01T09:00:00",
                   "technical_status": "completed", "completed_at": "2026-01-01T09:40:00",
                   "tool_log": [{"tool": "zz", "round": 0, "specialist": "macro", "time": "09:01:00"}],
                   "n_tool_calls": 1, "specialist_status": {"macro": "done"},
                   "usage_total": {"cost_eur": 9.99, "partial": False},
                   "usage_by_specialist": {"macro": {"cost_eur": 9.99}},
                   "round_esiti": {"0": {"stato": "eseguito"}}}


def _scrivi_hb(dati):
    from pathlib import Path
    p = Path(Blackboard.HEARTBEAT_PATH)
    p.write_text(json.dumps(dati), encoding="utf-8")
    return p


def test_terminale_scarta_la_lavagna_di_unaltra_run(tmp_path):
    from bellomberg.agents import consigliere_multi as cm
    p = _scrivi_hb(LAVAGNA_VECCHIA)
    cm._weekly_terminal_heartbeat(_store_finto("run-nuova", 8002))
    hb = json.loads(p.read_text(encoding="utf-8"))
    for campo in ("tool_log", "usage_total", "usage_by_specialist", "specialist_status",
                  "start_time", "technical_status", "completed_at", "round_esiti", "n_tool_calls"):
        assert campo not in hb, campo
    assert hb["lavagna"] == "assente"
    assert hb["heartbeat_precedente_scartato"] == {
        "run_id": "run-vecchia", "memo_id": 8001, "start_time": "2026-01-01T09:00:00"}
    assert hb["run_id"] == "run-nuova" and hb["memo_id"] == 8002 and hb["running"] is False
    e = base.esito_run(hb)
    assert e["stato"] == "bloccata" and e["memo_consegnato"] is False


def test_terminale_tiene_la_lavagna_della_stessa_run(tmp_path):
    from bellomberg.agents import consigliere_multi as cm
    p = _scrivi_hb({**LAVAGNA_VECCHIA, "run_id": "run-nuova", "memo_id": 8002})
    cm._weekly_terminal_heartbeat(_store_finto("run-nuova", 8002))
    hb = json.loads(p.read_text(encoding="utf-8"))
    assert hb["tool_log"] and hb["usage_total"]["cost_eur"] == 9.99
    assert hb["lavagna"] == "questa_run" and "heartbeat_precedente_scartato" not in hb


def test_terminale_primo_heartbeat_senza_run_id_stesso_memo_e_questa_run(tmp_path):
    """La Blackboard nasce senza run_id (bind_blackboard lo mette dopo): il primo
    heartbeat si riconosce dal memo."""
    from bellomberg.agents import consigliere_multi as cm
    p = _scrivi_hb({**LAVAGNA_VECCHIA, "run_id": None, "memo_id": 8002})
    cm._weekly_terminal_heartbeat(_store_finto("run-nuova", 8002))
    hb = json.loads(p.read_text(encoding="utf-8"))
    assert hb["lavagna"] == "questa_run" and hb["tool_log"]


def test_terminale_senza_file_o_file_non_oggetto(tmp_path):
    from bellomberg.agents import consigliere_multi as cm
    from pathlib import Path
    p = Path(Blackboard.HEARTBEAT_PATH)
    if p.exists():
        p.unlink()
    cm._weekly_terminal_heartbeat(_store_finto("run-nuova", 8002))
    assert json.loads(p.read_text(encoding="utf-8"))["lavagna"] == "assente"
    p.write_text("[1, 2]", encoding="utf-8")
    cm._weekly_terminal_heartbeat(_store_finto("run-nuova", 8002))
    hb = json.loads(p.read_text(encoding="utf-8"))
    assert hb["lavagna"] == "assente" and hb["run_id"] == "run-nuova"



# ---------------------------------------------------------------------------
# Revisione R-2 (06/10): F1/F2 costo incerto della sonda o senza agente; F3/F4 processo;
# F5 «ultimo tentativo» solo per i ritentativi dello stesso lavoro
# ---------------------------------------------------------------------------

def test_sonda_con_richiesta_incerta_resta_parziale(tmp_path):
    """Registro: sonda agent="_probe" con esito ignoto; usage "_probe:<slug>" senza request_ids
    (com'era prima della cura del blocco sonda in consigliere_multi)."""
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.request_journal = _registro(tmp_path, RICHIESTE_OK + [("p1", "unknown", None, "_probe", None)])
    bb.usage_log += [_riga("quant", 1, cost=1.25, ids=["q1"]),
                     _riga("_red_team", 1, cost=0.25, ids=["r1", "r2"]),
                     _riga("_probe:zz/finto", 1, cost=None, status="api_error", tin=None, tout=None)]
    _by, tot = bb._usage_aggregates()
    assert tot["partial"] is True and tot["tentativi_senza_richieste"] == []


def test_richiesta_incerta_senza_agente_tiene_parziale_anche_il_red_team(tmp_path):
    bb = _bb_red_team(tmp_path, _registro(tmp_path, RICHIESTE_OK + [("x1", "unknown", None, None, None)]))
    _by, tot = bb._usage_aggregates()
    assert tot["partial"] is True and tot["tentativi_senza_richieste"] == []


def _proc(pid=None):
    return SimpleNamespace(pid=pid, poll=lambda: None)


def test_avvio_run_nuova_col_file_della_precedente_e_in_corso(tmp_path, monkeypatch):
    db = _db_memo(tmp_path, {9002: "# Memo ZZTEST"})
    api = _api(tmp_path, monkeypatch, COMPLETATA, db)
    monkeypatch.setitem(api._CONSIGLIERE_PROCS, "zz-task", _proc())   # avvio ignoto
    e = api.get_agents_live()["esito_run"]
    assert e["stato"] == "in_corso" and e["fonte"] == "processo_vivo"
    assert e["memo_consegnato"] is False and "run precedente" in e["motivo"]


def test_file_piu_vecchio_del_processo_vivo_e_in_corso(tmp_path, monkeypatch):
    db = _db_memo(tmp_path, {9002: "# Memo ZZTEST"})
    api = _api(tmp_path, monkeypatch, COMPLETATA, db)
    os.utime(api.AGENTS_LIVE_PATH, (1, 1))                       # file del 1970
    monkeypatch.setitem(api._CONSIGLIERE_PROCS, "zz-task", _proc(os.getpid()))
    assert api.get_agents_live()["esito_run"]["fonte"] == "processo_vivo"


def test_file_scritto_dal_processo_vivo_si_legge_dal_file(tmp_path, monkeypatch):
    """Il processo ha gia' scritto il suo esito finale e sta uscendo: vale il file."""
    db = _db_memo(tmp_path, {9002: "# Memo ZZTEST"})
    api = _api(tmp_path, monkeypatch, COMPLETATA, db)          # file scritto ADESSO
    monkeypatch.setitem(api._CONSIGLIERE_PROCS, "zz-task", _proc(os.getpid()))
    assert api.get_agents_live()["esito_run"]["stato"] == "completata"


def test_processo_morto_senza_esito_e_interrotta(tmp_path, monkeypatch):
    hb = {"running": True, "memo_id": 9007, "updated_at": _adesso(30), "start_time": _adesso(60),
          "pid": 2 ** 31 - 7}                                    # pid inesistente
    api = _api(tmp_path, monkeypatch, hb)
    monkeypatch.setattr(api.run_state, "runs", {})
    e = api.get_agents_live()["esito_run"]
    assert e["stato"] == "interrotta" and e["fonte"] == "processo_morto"


def test_pid_riusato_da_un_processo_piu_giovane_non_e_vivo(tmp_path, monkeypatch):
    hb = {"running": True, "memo_id": 9007, "updated_at": _adesso(30),
          "start_time": "2000-01-01T00:00:00", "pid": os.getpid()}
    api = _api(tmp_path, monkeypatch, hb)
    monkeypatch.setattr(api.run_state, "runs", {})
    assert api.get_agents_live()["esito_run"]["stato"] == "interrotta"


def test_stop_del_pm_e_annullata(tmp_path, monkeypatch):
    hb = {"running": True, "memo_id": 9007, "updated_at": _adesso(30), "start_time": _adesso(60),
          "pid": 2 ** 31 - 7}
    api = _api(tmp_path, monkeypatch, hb)
    monkeypatch.setattr(api.run_state, "runs", {
        "t1": {"status": "completed", "started": "2026-01-01T09:00:00"},
        "t2": {"status": "cancelled", "started": "2026-01-02T09:00:00"}})
    e = api.get_agents_live()["esito_run"]
    assert e["stato"] == "annullata" and e["memo_consegnato"] is False


def test_senza_pid_e_senza_processo_non_e_in_corso(tmp_path, monkeypatch):
    hb = {"running": True, "memo_id": 9007, "updated_at": _adesso(30), "start_time": _adesso(60)}
    api = _api(tmp_path, monkeypatch, hb)
    monkeypatch.setattr(api.run_state, "runs", {})
    e = api.get_agents_live()["esito_run"]
    assert e["stato"] == "in_corso_senza_segnale" and e["fonte"] == "processo_non_verificabile"


def test_heartbeat_dichiara_il_pid(tmp_path):
    hb = tmp_path / "current_run.json"
    bb = Blackboard(heartbeat_path=hb)
    assert json.loads(hb.read_text(encoding="utf-8"))["pid"] == os.getpid()
    bb.mark_run_complete()
    assert json.loads(hb.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_fallimento_dopo_un_successo_resta_ko(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    bb.usage_log += [_riga("macro", 1, cost=0.5, ids=["a"]),
                     _riga("macro", 1, cost=0.1, status="api_error", ids=["b"]),
                     _riga("macro", 1, cost=0.2, ids=["c"])]
    by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == ["macro"] and tot["tentativi_falliti_poi_riusciti"] == []
    assert by["macro"]["status_finale"] == "api_error"


def test_fuori_dalla_settimanale_vale_il_peggiore(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json", run_scope="trade_idea")
    bb.usage_log += [_riga("macro", 1, cost=None, status="api_error", tin=None, tout=None),
                     _riga("macro", 1, cost=0.2, ids=["c"])]
    _by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == ["macro"] and tot["tentativi_falliti_poi_riusciti"] == []



# --- R-2 F5: chiave del lavoro (consultazioni diverse nello stesso round)

def _riga_lavoro(agent, rnd, lavoro, **kw):
    return {**_riga(agent, rnd, **kw), "lavoro": lavoro}


def test_consultazione_fallita_e_unaltra_riuscita_resta_ko(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json", run_scope="trade_idea")
    bb.usage_log += [_riga_lavoro("macro", 1, "consultazione:A", cost=None, status="api_error",
                                  tin=None, tout=None),
                     _riga_lavoro("macro", 1, "consultazione:B", cost=0.2, ids=["c"])]
    _by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == ["macro"] and tot["tentativi_falliti_poi_riusciti"] == []


def test_stessa_consultazione_ritentata_e_riuscita_non_e_ko(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json", run_scope="trade_idea")
    bb.usage_log += [_riga_lavoro("macro", 1, "consultazione:A", cost=None, status="api_error",
                                  tin=None, tout=None),
                     _riga_lavoro("macro", 1, "consultazione:A", cost=0.2, ids=["c"])]
    _by, tot = bb._usage_aggregates()
    assert tot["error_agents"] == []
    assert tot["tentativi_falliti_poi_riusciti"] == [
        {"agent": "macro", "round": 1, "tentativi_falliti": 1, "lavoro": "consultazione:A"}]


@pytest.mark.parametrize("ctx, atteso", [
    (None, None), ({"kind": "model_consultation", "consultation": True, "id": "zz7"}, "consultazione:zz7"),
    ({"kind": "model_authoring"}, "model_authoring"), ({}, None)])
def test_lavoro_dalla_task_context(tmp_path, ctx, atteso):
    from bellomberg.agents.specialists.macro import MacroSpecialist
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    sp = MacroSpecialist.__new__(MacroSpecialist)
    sp.blackboard, sp._task_context = bb, ctx
    assert sp._lavoro_corrente() == atteso


def test_record_usage_salva_il_lavoro(tmp_path, monkeypatch):
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    e = bb.record_usage("macro", 1, "zz/finto", {"in": 1, "out": 1, "cost_usd": 0.01},
                        lavoro="consultazione:zz7")
    assert e["lavoro"] == "consultazione:zz7"
    assert bb.record_usage("macro", 2, "zz/finto", {"in": 1, "out": 1, "cost_usd": 0.01})["lavoro"] is None


# --- R-2 K1-K4: buchi della batteria

def test_registro_senza_resume_available_e_null():
    hb = {k: v for k, v in FERMA.items() if k != "resume_available"}
    assert base.esito_run(hb)["ripresa_disponibile"] is None
    assert base.esito_run({**FERMA, "resume_available": "si"})["ripresa_disponibile"] is None


def test_ts_troncato_e_nd():
    r = base.round_esiti([{"agent": "macro", "round": 0, "ts": "2026-01-02"}], "2026-01-02T10:00:00")
    assert r["0"]["stato"] == "n.d." and r["0"]["desk_senza_orario"] == ["macro"]


def test_round_misto_conta_come_ripresa():
    hb = {"running": True, "round_esiti": {"0": {"stato": "eseguito"}, "1": {"stato": "misto"}}}
    assert base.esito_run(hb)["ripresa"] is True
    hb2 = {"running": True, "round_esiti": {"0": {"stato": "eseguito"}, "1": {"stato": "assente"}}}
    assert base.esito_run(hb2)["ripresa"] is False


def test_terminale_scarta_file_senza_run_id_di_un_altro_memo(tmp_path):
    from bellomberg.agents import consigliere_multi as cm
    p = _scrivi_hb({"run_id": None, "memo_id": 8001, "running": False,
                    "tool_log": [{"tool": "zz"}], "usage_total": {"cost_eur": 9.99}})
    cm._weekly_terminal_heartbeat(_store_finto("run-nuova", 8002))
    hb = json.loads(p.read_text(encoding="utf-8"))
    assert hb["lavagna"] == "assente" and "tool_log" not in hb


# --- marker del significato di error_agents (heartbeat e snapshot score_history)

def test_totale_dichiara_il_significato_di_error_agents(tmp_path):
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json")
    _by, tot = bb._usage_aggregates()
    assert tot["error_agents_semantica"] == "esito_finale_v2"
    bb.usage_log.append({"agent": "x"})   # riga rotta: aggregazione fallita, marker ancora presente
    bb.usage_log[-1] = None
    _by, tot = bb._usage_state()
    assert tot["error_agents_semantica"] == "esito_finale_v2"


def test_snapshot_score_history_porta_il_marker(tmp_path, monkeypatch):
    from bellomberg.agents import score_history as sh
    visto = {}
    monkeypatch.setattr(sh, "save_run_snapshot", lambda *a, **k: visto.update(k) or {"saved": True})
    bb = Blackboard(heartbeat_path=tmp_path / "current_run.json", memo_id=9008)
    bb.usage_log.append(_riga("macro", 1, cost=0.5, ids=["a"]))
    sh.record_completed_run(SimpleNamespace(db_path=str(tmp_path / "x.db")), bb, scorecard=None)
    assert visto["operational"]["usage_total"]["error_agents_semantica"] == "esito_finale_v2"
