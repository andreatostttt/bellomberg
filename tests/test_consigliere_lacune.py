# -*- coding: utf-8 -*-
"""Consigliere «a lacune» (decisioni PM 04/10, KB Opus 5.5): end-to-end offline.

Gira `run_multi_agent()` VERO (fixture `research_weekly`: DB in tmp, desk finti, Capo
finto, SMTP finto, renderer PDF di produzione) con un roster di SEI desk come in
produzione. Nessuna rete, nessuna spesa. Casi:
  1. un desk cade in R1 -> run completa, lacuna in elenco nel memo, dominio scoperto
     al Capo ([NO REPORT]), email con «COMITATO INCOMPLETO»;
  2. sotto quorum -> nessun memo di decisione, nessun round pagato in piu', stato dichiarato;
  3. Red Team mancante -> la run prosegue, niente convinzione ALTA (dal codice), dichiarato;
  4. Capo fallito -> memo parziale INCOMPLETO, nessuna decisione, nessuna email;
  5. costo ignoto -> ancora bloccante (nessuna lacuna «comoda»);
  + ripresa (il desk in lacuna resta fuori), stato della run, email incerta con conferma PM.
Ticker e importi inventati (ZZTEST, SYNTH-*)."""
from pathlib import Path
import json

import httpx
import pytest

from test_cablaggio_consigliere_multi import run_offline, _DeskFinto, _html, _SMTP  # noqa: F401
from test_weekly_research_without_workbook import research_weekly  # noqa: F401
from test_weekly_recovery import _store
from bellomberg.agents import consigliere_multi as cm
from bellomberg.agents import red_team
from bellomberg.agents import weekly_lifecycle as wl
from bellomberg.core.llm_client import APIConnectionError
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked

ROSTER = ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options")


@pytest.fixture
def comitato(research_weekly, monkeypatch):
    """Roster di produzione (6 desk, R2 = fundamentals/quant/options) con desk finti."""
    observed, calls = research_weekly
    # Il renderer PDF vero disegna i grafici in charts_institutional.DIR, che nasce da
    # core.paths.REPORT_DIR (la cartella report VERA): qui va in tmp, come il resto.
    import bellomberg.reporting.charts_institutional as charts
    import bellomberg.reporting.pdf_institutional as renderer
    monkeypatch.setattr(charts, "DIR", str(Path(cm.REPORT_DIR) / "inst_charts"))
    monkeypatch.setattr(renderer, "REPORT_DIR", str(cm.REPORT_DIR))
    classes = [type("Desk_" + name, (_DeskFinto,), {"name": name}) for name in ROSTER]
    monkeypatch.setattr(cm, "SPECIALIST_ORDER", classes)
    report = _DeskFinto.run
    faults = {}

    def run(self, round_n):
        fault = faults.get((self.name, round_n))
        if fault == "truncated":
            calls["reports"].append((self.name, round_n))
            text = "Report troncato a meta' di " + self.name
            self.bb.write(self.name, round_n, text)
            self.run_result_status = "truncated"
            return text
        if fault == "connect":
            calls["reports"].append((self.name, round_n))
            raise APIConnectionError("synthetic connection never established")
        if fault == "crash":
            raise RuntimeError("synthetic crash outside the allow-list")
        return report(self, round_n)

    monkeypatch.setattr(_DeskFinto, "run", run)
    return observed, calls, faults


def _memo_validated():
    return _store().get("memo_validated")["memo"]


# ------------------------------------------------------------------- 1. desk in lacuna

@pytest.mark.parametrize("fault", ["truncated", "connect"])
def test_desk_caduto_in_r1_e_lacuna_dichiarata_e_la_run_completa(comitato, fault):
    observed, calls, faults = comitato
    faults[("crypto", 1)] = fault
    result = cm.run_multi_agent(send_email=True)
    store = _store()
    assert result["status"] == "completed", result.get("last_error")
    # fuori per il resto della run: niente R2 (crypto non e' R2) ne' altre chiamate
    assert calls["reports"].count(("crypto", 1)) == 1
    # il sigillo dichiara il desk mancante, non un report
    sealed = store.get("research_dossier")
    assert "crypto" in sealed["missing_reports"] and "crypto" not in sealed["reports"]
    # il Capo vede il dominio SCOPERTO (regola DOMINI SCOPERTI del prompt)
    from bellomberg.agents.capo import scegli_report_specialisti
    bb = observed.catturato["bb"]
    chosen = scegli_report_specialisti(bb.data, bb.orari_report)
    assert chosen["crypto"]["no_report"] is True and chosen["crypto"]["report"].startswith("[NO REPORT]")
    assert not chosen["fundamentals"]["no_report"]
    # il memo porta l'ELENCO delle lacune in coda (blocco del codice)
    memo = _memo_validated()
    assert "## Comitato: lacune dichiarate" in memo
    assert "- Crypto, R1 (round_1):" in memo
    assert "Domini scoperti" in memo and "esposizioni cripto" in memo
    # l'email parte con la riga COMITATO INCOMPLETO
    assert len(observed.inviati) == 1
    html = _html(observed.inviati[0])
    assert "COMITATO INCOMPLETO" in html and "Crypto (R1)" in html
    # stato della run per la UI
    status = store.status()
    assert status["committee_gaps"]["status"] == "incomplete"
    assert status["committee_gaps"]["quorum"]["present_count"] == 5
    assert set(status["desk_gaps"]) == {"crypto"}
    assert not any(stage.startswith("desk:crypto") for stage in status["remaining_work"])


def test_desk_fuori_allow_list_resta_errore_di_run(comitato):
    observed, calls, faults = comitato
    faults[("crypto", 1)] = "crash"
    with pytest.raises(RuntimeError, match="synthetic crash"):
        cm.run_multi_agent(send_email=True)
    status = _store().status()
    assert status["first_error"]["desk"] == "crypto" and not status.get("desk_gaps")
    assert observed.catturato == {} and observed.inviati == []


def test_comitato_completo_non_scrive_lacune(comitato):
    observed, calls, faults = comitato
    result = cm.run_multi_agent(send_email=True)
    assert result["status"] == "completed"
    assert "Comitato: lacune" not in _memo_validated()
    assert "COMITATO INCOMPLETO" not in _html(observed.inviati[0])
    assert _store().status()["committee_gaps"]["status"] == "complete"


def test_desk_caduto_in_r2_conserva_r1_e_obiezioni_senza_risposta(comitato):
    observed, calls, faults = comitato
    faults[("options", 2)] = "truncated"
    result = cm.run_multi_agent(send_email=True)
    assert result["status"] == "completed", result.get("last_error")
    gaps = result["committee_gaps"]
    assert gaps["quorum"]["present_count"] == 6          # l'R1 sigillato di options conta
    assert gaps["unreplied_r2"] == ["options"]
    from bellomberg.agents.capo import scegli_report_specialisti
    chosen = scegli_report_specialisti(observed.catturato["bb"].data)
    assert chosen["options"]["round"] == 1 and chosen["options"]["report"].startswith("[ROUND 2 SENZA REPORT")
    memo = _memo_validated()
    assert "- Options, R2 (round_2):" in memo and "Repliche al Red Team mancanti" in memo


# ------------------------------------------------------------------- 2. sotto quorum

def test_fundamentals_caduto_sotto_quorum_nessun_memo_ne_round_in_piu(comitato):
    observed, calls, faults = comitato
    faults[("fundamentals", 0)] = "truncated"
    with pytest.raises(WeeklyRunBlocked, match="sotto quorum"):
        cm.run_multi_agent(send_email=True)
    store = _store()
    # stop dopo R0: nessun R1 pagato, nessun Red Team, nessun Capo, nessuna email
    assert not any(round_n == 1 for _, round_n in calls["reports"])
    assert observed.catturato == {} and observed.inviati == []
    assert store.get("memo_validated") is None and store.get("red_team") is None
    status = store.status()
    assert status["status"] == "incomplete"
    assert status["committee_gaps"]["status"] == "below_quorum"
    assert "sotto quorum" in status["first_error"]["message"]
    # RV-KB P2-1: run CHIUSA, non riprendibile (il desk in lacuna resta fuori anche in ripresa)
    assert status["resume_available"] is False
    assert status["blocked_reason"].startswith("Comitato sotto quorum")
    assert status["remaining_work"] == [] and status["terminal_reason"] == "committee_below_quorum"


def test_tre_desk_caduti_in_r1_sotto_quorum(comitato):
    observed, calls, faults = comitato
    for desk in ("macro", "eventdesk", "crypto"):
        faults[(desk, 1)] = "truncated"
    with pytest.raises(WeeklyRunBlocked, match="sotto quorum"):
        cm.run_multi_agent(send_email=True)
    assert not any(round_n == 2 for _, round_n in calls["reports"])
    assert observed.catturato == {} and observed.inviati == []
    assert _store().get("research_dossier") is None


def test_due_desk_caduti_quorum_ancora_raggiunto(comitato):
    observed, calls, faults = comitato
    faults[("macro", 1)] = "truncated"
    faults[("options", 0)] = "connect"
    result = cm.run_multi_agent(send_email=True)
    assert result["status"] == "completed"
    assert result["committee_gaps"]["quorum"]["present_count"] == 4
    assert ("options", 1) not in calls["reports"] and ("options", 2) not in calls["reports"]


# ------------------------------------------------------------------- 3. Red Team mancante

_MEMO_CON_ALTA = ("# Memo settimanale\n\n## ACTION TABLE\n"
                  "| Action | Ticker | Size | Timing | Confidence |\n"
                  "|---|---|---|---|---|\n"
                  "| HOLD | SYNTH-A | 0 EUR | ora | **ALTA** |\n"
                  "| HOLD | SYNTH-B | 0 EUR | ora | MEDIA |\n\n"
                  "Tesi in prosa con convinzione ALTA dichiarata dal Capo. " + "m" * 80)


@pytest.mark.parametrize("shape", ["api_error", "incomplete"])
def test_red_team_mancante_prosegue_senza_convinzione_alta(comitato, monkeypatch, shape):
    observed, calls, faults = comitato

    def red(bb, **kwargs):
        if shape == "api_error":
            error = APIConnectionError("synthetic red team connection failure")
            bb.record_run_failure(error, desk="red_team", round_n=1)   # come red_team.py
            bb.write("_red_team", 1, red_team.SEGNAPOSTO_NON_DISPONIBILE + " synthetic]")
            raise error
        bb.write("_red_team", 1, "Critica parziale [CRITICA TRONCATA]")
        error = ValueError("Red Team incomplete: terminal response incomplete: max_tokens")
        bb.record_run_failure(error, desk="red_team", round_n=1)
        raise error
    monkeypatch.setattr(red_team, "run_red_team", red)
    capo = cm.run_capo

    def capo_alta(bb, **kwargs):
        capo(bb, **kwargs)
        return _MEMO_CON_ALTA, {"model": "m/finto", "input_tokens": 10, "output_tokens": 10,
                                "api_calls": 1, "complete": True, "stop_reason": "end_turn"}
    monkeypatch.setattr(cm, "run_capo", capo_alta)
    result = cm.run_multi_agent(send_email=True)
    store = _store()
    assert result["status"] == "completed", result.get("last_error")
    assert store.get("red_team")["gap"].startswith(("APIConnectionError", "ValueError"))
    # R2 gira lo stesso (fundamentals/quant/options)
    assert {("fundamentals", 2), ("quant", 2), ("options", 2)} <= set(calls["reports"])
    # il Capo e' avvisato nel prompt
    assert "RED TEAM MANCANTE" in observed.catturato["sizing_context"]
    # il CODICE toglie ALTA dalla ACTION TABLE e lo dichiara
    memo = _memo_validated()
    table = [line for line in memo.splitlines() if line.startswith("| HOLD")]
    assert table and not any("ALTA" in line for line in table), table
    assert "**MEDIA**" in table[0]
    assert "Riga 1: HOLD SYNTH-A, ALTA declassata a MEDIA." in memo
    assert "**Red Team.** Assente" in memo
    assert "Red Team assente" in _html(observed.inviati[0])
    assert store.status()["committee_gaps"]["high_conviction_allowed"] is False


def test_red_team_integrita_resta_errore_di_run(comitato, monkeypatch):
    observed, calls, faults = comitato

    def red(bb, **kwargs):
        error = ValueError("Red Team tool outcome unknown: get_portfolio_live")
        bb.record_run_failure(error, desk="red_team", round_n=1)
        raise error
    monkeypatch.setattr(red_team, "run_red_team", red)
    with pytest.raises(ValueError, match="tool outcome unknown"):
        cm.run_multi_agent(send_email=True)
    assert observed.catturato == {} and _store().get("red_team") is None


_H = "## ACTION TABLE\n| Action | Ticker | EUR | Timing | Confidence |\n|---|---|---|---|---|\n"


@pytest.mark.parametrize("row, expected", [
    ("| HOLD | ZZTEST | 0 EUR | ora | HIGH |\n", "MEDIUM"),
    ("| HOLD | ZZTEST | 0 EUR | Q4 \\| venerdi | ALTA |\n", "MEDIA"),                  # pipe escapato
    ("| HOLD | ZZTEST | 0 EUR | ora | ALTA |\n   motivazione ALTA a capo\n", "MEDIA <br> motivazione MEDIA a capo"),
    ("| HOLD | ZZTEST | 0 EUR | ora | ALTA\n", "MEDIA"),                               # senza pipe finale
    ("| HOLD | ZZTEST | 0 EUR | ora | Alta (ma con riserve) |\n", "MEDIA (ma con riserve)"),
    ("| HOLD | ZZTEST | 0 EUR | ora | **ALTA** |\n", "**MEDIA**"),
])
def test_cap_high_conviction_su_ogni_forma_di_riga_e_verificato_col_parser_del_registro(row, expected):
    from bellomberg.agents.action_table_extract import parse_action_table_rows
    capped_memo, capped, residual = wl.cap_high_conviction(_H + row)
    rows = parse_action_table_rows(capped_memo)["rows"]
    assert rows[0]["confidence"] == expected          # cio' che il registro decisioni leggera'
    assert [r["ticker"] for r in capped] == ["ZZTEST"] and residual == []
    block = wl.conviction_cap_block(capped, residual, "it")
    assert "ALTA declassata a MEDIA" in block and "nessuna riga con ALTA" in block


def test_cap_high_conviction_solo_il_valore_intero():
    memo = _H + "| HOLD | ZZTEST | 0 EUR | ora | Medio-Alta |\n| HOLD | QQSYN | 0 EUR | ora | MEDIA |\n"
    capped_memo, capped, residual = wl.cap_high_conviction(memo)
    assert capped_memo == memo and capped == [] and residual == []


def test_blocco_convinzione_non_afferma_cio_che_non_ha_verificato():
    residual = [{"row_index": 1, "ticker": "QQSYN", "action": "HOLD"}]
    block = wl.conviction_cap_block([], residual, "it")
    assert "nessuna riga con ALTA" not in block and "1 righe conservano ALTA" in block
    assert "Riga 2: HOLD QQSYN, ALTA NON riscrivibile" in block


def _summary_override(monkeypatch, **forced):
    original = wl.committee_summary

    def summary(bb, store, **kwargs):
        result = original(bb, store, **kwargs)
        if kwargs.get("usage") is not None:     # il riepilogo dopo il Capo, quello che decide
            result = dict(result, **forced)
        return result
    monkeypatch.setattr(wl, "committee_summary", summary)


def test_decisioni_non_ammesse_bloccano_la_persistenza(comitato, monkeypatch):
    observed, calls, faults = comitato
    _summary_override(monkeypatch, decisions_allowed=False)
    with pytest.raises(WeeklyRunBlocked, match="Decisioni non ammesse"):
        cm.run_multi_agent(send_email=True)
    store = _store()
    assert store.get("memo_validated") is None and store.get("decisions_finalized") is None
    with store.db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert observed.inviati == []


def test_email_automatica_non_ammessa_non_parte(comitato, monkeypatch):
    observed, calls, faults = comitato
    _summary_override(monkeypatch, automatic_email_allowed=False)
    result = cm.run_multi_agent(send_email=True)
    assert observed.inviati == []
    assert result["email_blocked_reason"].startswith("Email automatica non ammessa")
    assert result["status"] == "incomplete"           # consegna richiesta e non avvenuta: dichiarata


# ------------------------------------------------------------------- 4. Capo fallito

@pytest.mark.parametrize("fault", ["error", "truncated"])
def test_capo_fallito_memo_parziale_incompleto_senza_decisioni_ne_email(comitato, monkeypatch, fault):
    observed, calls, faults = comitato
    faults[("crypto", 1)] = "truncated"

    def capo(bb, **kwargs):
        observed.catturato["bb"] = bb
        return ("[CAPO ERROR]: synthetic timeout" if fault == "error" else "partial memo",
                {"model": "test", "input_tokens": None, "output_tokens": None, "api_calls": 1,
                 "complete": False, "error": "synthetic timeout" if fault == "error" else None,
                 "stop_reason": "max_tokens" if fault == "truncated" else None})
    monkeypatch.setattr(cm, "run_capo", capo)
    result = cm.run_multi_agent(send_email=True)
    store = _store()
    assert result["status"] == "incomplete" and result["analytical_status"] == "partial"
    assert result["first_error"]["phase"] == "capo"
    assert "memo parziale INCOMPLETO" in result["first_error"]["message"]
    assert store.get("capo") is None and store.get("memo_validated") is None
    assert store.get("decisions_finalized") is None
    assert observed.inviati == []
    partial = result["capo_partial"]
    document = Path(partial["markdown_path"]).read_text(encoding="utf-8")
    assert document.startswith("# MEMO INCOMPLETO")
    assert "Report dei desk (non sintetizzati)" in document and "### Fundamentals" in document
    assert "## Comitato: lacune dichiarate" in document and "- Crypto, R1" in document
    assert "Nessuna decisione estratta" in document
    assert partial["pdf_path"] and Path(partial["pdf_path"]).read_bytes().startswith(b"%PDF-")
    assert partial["decisions"] == "none" and partial["email"] == "not_sent"
    with store.db._conn() as conn:
        assert conn.execute("SELECT full_markdown FROM memos").fetchone()[0] == "[IN PROGRESS]"
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert result["resume_available"] is True   # il PM puo' rifare il Capo con una ripresa esplicita


# ------------------------------------------------------------------- 5. costo ignoto

def test_costo_ignoto_resta_bloccante_anche_se_il_guasto_e_di_un_desk(comitato, monkeypatch):
    from bellomberg.core.llm_client import OpenRouterClient
    from bellomberg.valuation import preparation_ai
    observed, calls, faults = comitato
    monkeypatch.setattr(preparation_ai, "live_metadata", lambda model: {
        "id": model, "context_length": 1000, "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    sent = []

    def send(request):
        sent.append(request)
        return httpx.Response(503, json={"error": {"code": 503, "message": "synthetic provider outage"}})
    client = OpenRouterClient(api_key="test", max_retries=4, trasporto=httpx.MockTransport(send))
    original = _DeskFinto.run

    def run(self, round_n):
        if self.name == "crypto" and round_n == 1:
            client.messages.create(model="test/model", max_tokens=20,
                                   messages=[{"role": "user", "content": "frozen weekly input"}])
        return original(self, round_n)
    monkeypatch.setattr(_DeskFinto, "run", run)
    with pytest.raises(Exception):
        cm.run_multi_agent(send_email=True)
    status = _store().status()
    assert len(sent) == 1
    assert status["first_error"]["desk"] == "crypto" and status["first_error"]["round"] == 1
    assert not status.get("desk_gaps")     # NON una lacuna: errore di run
    assert status["request_costs"]["unknown_requests"] == 1
    assert observed.catturato == {} and observed.inviati == []


# ------------------------------------------------------------------- ripresa e stato

def test_desk_in_lacuna_resta_fuori_anche_in_ripresa(comitato, monkeypatch):
    observed, calls, faults = comitato
    faults[("crypto", 1)] = "truncated"
    crashed = []
    capo = cm.run_capo

    def capo_crash_once(bb, **kwargs):
        if not crashed:
            crashed.append(True)
            raise RuntimeError("synthetic crash before the Capo request")
        return capo(bb, **kwargs)
    monkeypatch.setattr(cm, "run_capo", capo_crash_once)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        cm.run_multi_agent(send_email=True)
    store = _store()
    assert store.status()["resume_available"] is True
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=True)
    assert result["status"] == "completed"
    assert calls["reports"].count(("crypto", 1)) == 1
    assert "- Crypto, R1" in _memo_validated()


def test_stato_run_risposta_troncata_di_un_desk_in_lacuna_non_chiede_revisione(comitato):
    observed, calls, faults = comitato
    faults[("crypto", 1)] = "truncated"
    cm.run_multi_agent(send_email=False)
    store = _store()
    bb = observed.catturato["bb"]
    bb.specialist_checkpoints["crypto:R1"] = {"status": "truncated"}
    store.save_snapshot(bb)
    assert store.status()["blocked_reason"] != "Risposta analitica incompleta conservata: revisione esplicita necessaria"
    bb.specialist_checkpoints["quant:R1"] = {"status": "truncated"}   # desk NON in lacuna
    store.save_snapshot(bb)
    assert store.status()["blocked_reason"] == "Risposta analitica incompleta conservata: revisione esplicita necessaria"


def test_stato_run_red_team_in_lacuna_non_chiede_revisione(comitato):
    observed, calls, faults = comitato
    cm.run_multi_agent(send_email=False)
    store = _store()
    bb = observed.catturato["bb"]
    bb.specialist_checkpoints["red_team:R1"] = {"status": "failed"}
    store.save_snapshot(bb)
    revisione = "Risposta analitica incompleta conservata: revisione esplicita necessaria"
    assert store.status()["blocked_reason"] == revisione       # Red Team NON in lacuna: revisione
    bb.data["_red_team_gap"] = {"message": "ValueError: Red Team incomplete: sintetico"}
    store.save_snapshot(bb)
    assert store.status()["blocked_reason"] != revisione


def test_capo_partial_si_azzera_dopo_una_ripresa_riuscita(comitato, monkeypatch):
    observed, calls, faults = comitato
    capo = cm.run_capo
    failed = []

    def capo_once(bb, **kwargs):
        if not failed:
            failed.append(True)
            return "[CAPO ERROR]: synthetic timeout", {"model": "test", "input_tokens": None,
                "output_tokens": None, "api_calls": 1, "complete": False, "error": "synthetic timeout",
                "stop_reason": None}
        return capo(bb, **kwargs)
    monkeypatch.setattr(cm, "run_capo", capo_once)
    first = cm.run_multi_agent(send_email=False)
    assert first["capo_partial"]
    store = _store()
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert result["status"] == "completed"
    assert result["capo_partial"] is None
    assert result["capo_partial_superseded"]["markdown_path"] == first["capo_partial"]["markdown_path"]


# ------------------------------------------------------------------- email incerta

def test_email_incerta_si_ripete_solo_con_la_conferma_del_pm(comitato, monkeypatch):
    import bellomberg.reporting.email_sender as sender
    observed, calls, faults = comitato

    class DisconnectAfterAcceptance(_SMTP):
        def send_message(self, message):
            super().send_message(message)
            raise sender.smtplib.SMTPServerDisconnected("synthetic disconnect after acceptance")
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", DisconnectAfterAcceptance)
    first = cm.run_multi_agent(send_email=True)
    store = _store()
    assert first["delivery_status"] == "uncertain" and len(observed.inviati) == 1
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", _SMTP)
    again = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True)
    assert again["delivery_status"] == "uncertain" and len(observed.inviati) == 1
    # la conferma senza invio richiesto non spedisce
    cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=False,
                       acknowledge_uncertain_email=True)
    assert len(observed.inviati) == 1
    confirmed = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True,
                                   acknowledge_uncertain_email=True)
    assert len(observed.inviati) == 2 and confirmed["delivery_status"] == "sent"
    assert confirmed["status"] == "completed"
    acks = confirmed["email_uncertain_acknowledgements"]
    assert len(acks) == 1 and acks[0]["prior"]["state"] == "uncertain" and acks[0]["outcome"] == "sent"
    # inviata: un'altra conferma non spedisce di nuovo
    cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True,
                       acknowledge_uncertain_email=True)
    assert len(observed.inviati) == 2


def test_opzione_conferma_reinvio_validata():
    from bellomberg.api.weekly_recovery_routes import WeeklyRunOptions
    ok = WeeklyRunOptions(resume_memo_id=7, delivery_only=True, send_email=True, acknowledge_uncertain_email=True)
    assert ok.acknowledge_uncertain_email is True
    for bad in ({"acknowledge_uncertain_email": True, "send_email": True},
                {"resume_memo_id": 7, "delivery_only": True, "acknowledge_uncertain_email": True}):
        with pytest.raises(ValueError):
            WeeklyRunOptions(**bad)
    assert WeeklyRunOptions().acknowledge_uncertain_email is False


# ------------------------------------------------------------------- aggancio API -> worker

from test_run_mandato import ambiente, _valido  # noqa: E402,F401


@pytest.mark.parametrize("acknowledge", [True, False])
def test_api_passa_al_worker_la_conferma_del_reinvio_solo_se_richiesta(ambiente, run_offline, monkeypatch,
                                                                      acknowledge):
    from bellomberg.storage import memory_db
    client, mandate, calls, api = ambiente
    mandate.write_text(json.dumps(_valido()), encoding="utf-8")
    result = cm.run_multi_agent(send_email=False)
    database = memory_db.MemoryDB()
    monkeypatch.setattr(api, "get_db", lambda: database)
    api._LAST_CALL.clear()
    response = client.post("/consigliere/run", json={"resume_memo_id": result["memo_id"], "delivery_only": True,
                                                     "send_email": True,
                                                     "acknowledge_uncertain_email": acknowledge})
    assert response.status_code == 200, response.text
    argv = calls[0][0][0]
    assert ("--acknowledge-uncertain-email" in argv) is acknowledge
    assert "--no-email" not in argv and "--delivery-only" in argv


def _incerta(comitato, monkeypatch):
    import bellomberg.reporting.email_sender as sender
    observed, calls, faults = comitato

    class DisconnectAfterAcceptance(_SMTP):
        def send_message(self, message):
            super().send_message(message)
            raise sender.smtplib.SMTPServerDisconnected("synthetic disconnect after acceptance")
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", DisconnectAfterAcceptance)
    first = cm.run_multi_agent(send_email=True)
    assert first["delivery_status"] == "uncertain"
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", _SMTP)
    return observed, _store()


def test_conferma_reinvio_nella_ripresa_senza_delivery_only(comitato, monkeypatch):
    observed, store = _incerta(comitato, monkeypatch)
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, send_email=True, acknowledge_uncertain_email=True)
    assert len(observed.inviati) == 2 and result["delivery_status"] == "sent"


def test_conferma_reinvio_bloccata_resta_a_registro_col_suo_esito(comitato, monkeypatch):
    observed, store = _incerta(comitato, monkeypatch)
    monkeypatch.setattr(wl, "costs_unresolved", lambda costs: True)
    with pytest.raises(WeeklyRunBlocked, match="Costi o richieste incerti"):
        cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True,
                           acknowledge_uncertain_email=True)
    acks = store.status()["email_uncertain_acknowledgements"]
    assert len(observed.inviati) == 1
    assert len(acks) == 1 and acks[0]["outcome"].startswith("blocked: Costi o richieste incerti")


def test_i_grafici_del_pdf_restano_nel_tmp(comitato):
    """Main 05/10: nella suite completa il renderer PDF vero disegnava i grafici nella cartella
    report VERA (core.paths.REPORT_DIR/inst_charts). Con dati che generano un grafico, il file
    deve nascere nella cartella report del test."""
    import bellomberg.reporting.pdf_institutional as renderer
    portfolio = {"totale_valore_mercato_eur": 123.45,
                 "positions": [{"ticker": "ZZTEST", "peso_pct": 60.0}, {"ticker": "QQSYN", "peso_pct": 40.0}]}
    paths = [p for p in renderer._gen_charts(portfolio, None) if p]
    assert paths, "nessun grafico generato: la prova non misura nulla"
    root = Path(cm.REPORT_DIR).resolve()
    assert all(Path(p).resolve().is_relative_to(root) for p in paths), paths
