"""Consigliere «a lacune»: blocco di dichiarazione (reporting/weekly_gaps.py).

Dati tutti sintetici (desk e richieste inventati: ZZREQ-*); nessun valore del book.
"""
import json

import pytest

from bellomberg.reporting.weekly_gaps import (committee_gaps_summary, format_gaps_for_email,
                                              format_gaps_for_memo)

ROSTER = ["macro", "eventdesk", "crypto", "fundamentals", "quant", "options"]
R2 = ["fundamentals", "quant", "options"]
CAPO_OK = {"memo": "## BLUF\nTesto sintetico ZZTEST.", "usage": {"complete": True, "stop_reason": "end_turn"}}


def _data(**overrides):
    data = {desk: {1: "Report R1 sintetico " + desk, 2: "Replica R2 sintetica " + desk} for desk in ROSTER}
    data["_red_team"] = {1: "Critica sintetica: obiezione su ZZTEST."}
    data.update(overrides)
    return data


def _store(**checkpoints):
    return {"roster": ROSTER, "r2_specialists": R2, "checkpoints": checkpoints}


def _gap(desk, round_n, message="specialist response truncated", request_id="ZZREQ-1"):
    return {"message": message, "exception_type": "RuntimeError", "desk": desk,
            "round": round_n, "request_id": request_id, "phase": "round_" + str(round_n)}


def _sintetico_due_lacune(language="it"):
    data = _data(_desk_gaps={"crypto": _gap("crypto", 1, request_id="ZZREQ-7"),
                             "options": _gap("options", 1, "provider error 529", "ZZREQ-9")},
                 _red_team_gap={"message": "Red Team incomplete: risposta inutilizzabile"})
    for desk in ("crypto", "options"):
        data[desk] = {}
    data.pop("_red_team")
    return committee_gaps_summary(data, _store(), capo=CAPO_OK, language=language)


def test_comitato_completo_non_scrive_nulla():
    s = committee_gaps_summary(_data(), _store(), capo=CAPO_OK, language="it")
    assert s["status"] == "complete"
    assert s["memo_markdown"] == "" and s["email_line"] == ""
    assert s["high_conviction_allowed"] and s["decisions_allowed"] and s["automatic_email_allowed"]
    assert s["quorum"]["present_count"] == 6 and s["quorum"]["reached"]


def test_due_desk_in_lacuna_e_red_team_assente():
    s = _sintetico_due_lacune()
    assert s["status"] == "incomplete"
    assert s["quorum"]["reached"] and s["quorum"]["present_count"] == 4
    assert s["quorum"]["missing"] == ["crypto", "options"]
    assert [g["desk"] for g in s["gaps"]] == ["crypto", "options"]
    assert s["gaps"][0]["request_id"] == "ZZREQ-7" and s["gaps"][0]["phase"] == "round_1"
    assert [d["desk"] for d in s["uncovered_domains"]] == ["crypto", "options"]
    assert s["red_team"]["status"] == "absent" and "Red Team incomplete" in s["red_team"]["reason"]
    assert s["high_conviction_allowed"] is False
    assert s["decisions_allowed"] is True  # quorum + Capo completo: decisioni sui domini coperti
    memo = s["memo_markdown"]
    assert memo.startswith("## Comitato: lacune dichiarate")
    assert "- Crypto, R1 (round_1): specialist response truncated. Richiesta ZZREQ-7." in memo
    assert "- Options, R1 (round_1): provider error 529. Richiesta ZZREQ-9." in memo
    # impianto C scelto dal PM: elenco, nessuna tabella markdown (intestazioni illeggibili nel PDF)
    assert not any(line.lstrip().startswith("|") for line in memo.split("\n"))
    assert "Desk presenti: 4 di 6; Fundamentals presente" in memo
    assert "**Domini scoperti.** Crypto: esposizioni cripto effettive del book; Options: strutture in opzioni." in memo
    assert "**Red Team.** Assente: Red Team incomplete: risposta inutilizzabile." in memo
    assert "nessuna proposta ha convinzione ALTA" in memo
    assert "**Capo.** Memo completo" in memo


def test_riga_email_una_sola_e_parla_chiaro():
    line = _sintetico_due_lacune()["email_line"]
    assert "\n" not in line
    assert line.startswith("COMITATO INCOMPLETO: quorum 4 di 6; desk in lacuna: Crypto (R1), Options (R1); "
                           "Red Team assente; Capo completo.")


def test_lacuna_senza_fase_ne_richiesta_dichiara_nd():
    data = _data(_desk_gaps={"crypto": {"message": "guasto sintetico.", "round": 0}})
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert "- Crypto, R0 (fase n.d.): guasto sintetico. Richiesta n.d." in s["memo_markdown"]
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="en")
    assert "- Crypto, R0 (phase n/a): guasto sintetico. Request n/a." in s["memo_markdown"]


def test_email_html_escapa():
    s = _sintetico_due_lacune()
    s["gaps"][0]["label"] = "<script>"
    out = format_gaps_for_email(s, as_html=True)
    assert out.startswith("<p><strong>COMITATO INCOMPLETO</strong>: ")
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_fundamentals_caduto_sotto_quorum():
    data = _data(_desk_gaps={"fundamentals": _gap("fundamentals", 0)})
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert s["status"] == "below_quorum" and not s["quorum"]["reached"]
    assert s["decisions_allowed"] is False and s["automatic_email_allowed"] is False
    assert "Sotto quorum" in s["memo_markdown"] and "Fundamentals assente" in s["memo_markdown"]
    assert "quorum 5 di 6 (NON raggiunto)" in s["email_line"]


def test_tre_desk_assenti_sotto_quorum_anche_con_fundamentals():
    gaps = {d: _gap(d, 1) for d in ("macro", "crypto", "options")}
    s = committee_gaps_summary(_data(_desk_gaps=gaps), _store(), capo=CAPO_OK, language="it")
    assert s["quorum"]["present_count"] == 3 and s["status"] == "below_quorum"


def test_quattro_presenti_e_il_minimo_che_basta():
    gaps = {d: _gap(d, 1) for d in ("macro", "crypto")}
    s = committee_gaps_summary(_data(_desk_gaps=gaps), _store(), capo=CAPO_OK, language="it")
    assert s["quorum"]["present_count"] == 4 and s["quorum"]["reached"]


def test_r1_segnaposto_senza_lacuna_registrata_e_lacuna():
    data = _data(crypto={1: "[ERROR] risposta vuota"})
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert [g["desk"] for g in s["gaps"]] == ["crypto"]
    assert s["gaps"][0]["reason"] == "report R1 non disponibile"
    assert s["gaps"][0]["round"] == 1


def test_desk_caduto_in_r2_conserva_r1_e_lascia_obiezioni_senza_risposta():
    data = _data(_desk_gaps={"quant": _gap("quant", 2)})
    data["quant"] = {1: "Report R1 sintetico quant"}
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert "quant" in s["quorum"]["present"] and s["quorum"]["present_count"] == 6
    assert s["unreplied_r2"] == ["quant"]
    assert s["uncovered_domains"] == []
    assert "**Repliche al Red Team mancanti.** Quant" in s["memo_markdown"]
    assert "repliche R2 mancanti: Quant" in s["email_line"]


def test_r2_mancante_senza_lacuna_e_dichiarata():
    data = _data()
    data["options"] = {1: "Report R1 sintetico options"}
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert s["status"] == "incomplete" and s["unreplied_r2"] == ["options"]
    assert s["gaps"] == []


def test_red_team_segnaposto_non_disponibile_e_assente():
    from bellomberg.agents.red_team import SEGNAPOSTO_NON_DISPONIBILE
    data = _data(_red_team={1: SEGNAPOSTO_NON_DISPONIBILE + " HTTP 403 sintetico]"})
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert s["red_team"]["status"] == "absent" and s["high_conviction_allowed"] is False


def test_red_team_troncato_e_parziale():
    data = _data(_red_team={1: "Critica sintetica.\n[CRITICA TRONCATA: raggiunto il limite di token del red team]"})
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert s["red_team"]["status"] == "truncated" and s["high_conviction_allowed"] is False
    assert "**Red Team.** Critica parziale" in s["memo_markdown"]
    assert "Red Team parziale" in s["email_line"]


def test_red_team_mai_registrato_e_assente():
    data = _data()
    data.pop("_red_team")
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert s["red_team"]["status"] == "absent"
    assert "nessuna critica del Red Team a registro" in s["memo_markdown"]


def test_red_team_dal_checkpoint_dello_store():
    data = _data()
    data.pop("_red_team")
    s = committee_gaps_summary(data, _store(red_team={"report": "Critica sintetica."}),
                               capo=CAPO_OK, language="it")
    assert s["red_team"]["status"] == "present"
    s = committee_gaps_summary(data, _store(red_team={"report": "x", "gap": "guasto sintetico"}),
                               capo=CAPO_OK, language="it")
    assert s["red_team"] == {"status": "absent", "reason": "guasto sintetico", "request_id": None}


@pytest.mark.parametrize("usage", [{"complete": False, "stop_reason": "max_tokens", "error": "Capo incomplete response: max_tokens"},
                                   {"complete": True, "stop_reason": "end_turn", "error": "boom sintetico"}])
def test_capo_parziale_niente_decisioni_niente_email(usage):
    s = committee_gaps_summary(_data(), _store(), capo={"memo": "[CAPO ERROR]: x", "usage": usage}, language="it")
    assert s["capo"]["status"] == "partial"
    assert s["status"] == "incomplete"
    assert s["decisions_allowed"] is False and s["automatic_email_allowed"] is False
    assert "**Capo.** Memo INCOMPLETO (parziale)" in s["memo_markdown"]
    assert "Nessuna decisione estratta, nessuna email automatica." in s["memo_markdown"]
    assert "Capo parziale, nessuna decisione" in s["email_line"]


def test_capo_ignoto_non_e_completo():
    s = committee_gaps_summary(_data(), _store(), language="it")
    assert s["capo"]["status"] == "unknown" and s["decisions_allowed"] is False
    assert "esito ignoto" in s["memo_markdown"]
    s = committee_gaps_summary(_data(), _store(capo=CAPO_OK), language="it")
    assert s["capo"]["status"] == "complete"


def test_store_oggetto_come_weekly_run_store():
    class FakeStore:
        context = {"contract": {"roster": ROSTER, "r2_specialists": R2}}

        def get(self, stage):
            return CAPO_OK if stage == "capo" else None

    class FakeBoard:
        data = _data()

    s = committee_gaps_summary(FakeBoard(), FakeStore(), language="it")
    assert s["status"] == "complete"


def test_senza_roster_nessun_quorum_di_ripiego():
    with pytest.raises(ValueError):
        committee_gaps_summary(_data(), None, capo=CAPO_OK, language="it")
    with pytest.raises(ValueError):
        committee_gaps_summary(_data(), {"roster": [], "r2_specialists": R2}, capo=CAPO_OK, language="it")


def test_inglese():
    s = _sintetico_due_lacune(language="en")
    assert s["memo_markdown"].startswith("## Committee: declared gaps")
    assert "**Uncovered domains.** Crypto: the book's actual crypto exposures" in s["memo_markdown"]
    assert s["email_line"].startswith("COMMITTEE INCOMPLETE: quorum 4 of 6; desks with a gap: Crypto (R1)")
    assert "Comitato" not in s["memo_markdown"] and "COMITATO" not in s["email_line"]


def test_causa_senza_url_senza_pipe_e_taglio_dichiarato():
    msg = "errore su https://zz.example/api?key=ZZSEGRETO | dettaglio " + "x" * 400
    data = _data(_desk_gaps={"crypto": _gap("crypto", 1, msg)})
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    reason = s["gaps"][0]["reason"]
    assert "ZZSEGRETO" not in s["memo_markdown"] and "[URL omesso]" in reason
    assert "|" not in reason and reason.endswith(" [...]")


def test_chiavi_round_stringa_come_dopo_un_json():
    data = json.loads(json.dumps(_data()))
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert s["status"] == "complete"


def test_lacuna_fuori_roster_dichiarata():
    data = _data(_desk_gaps={"zzdesk": _gap("zzdesk", 1)})
    s = committee_gaps_summary(data, _store(), capo=CAPO_OK, language="it")
    assert s["status"] == "incomplete"
    assert any(g["desk"] == "zzdesk" and "fuori roster" in g["reason"] for g in s["gaps"])
    assert s["quorum"]["present_count"] == 6


def test_ritentativi_non_fatturati():
    retries = [{"request_id": "ZZREQ-3", "agent": "macro", "reason": "connect"}]
    s = committee_gaps_summary(_data(), _store(), capo=CAPO_OK, unbilled_retries=retries, language="it")
    assert s["status"] == "complete" and s["memo_markdown"] == ""
    s = committee_gaps_summary(_data(_desk_gaps={"crypto": _gap("crypto", 1)}), _store(), capo=CAPO_OK,
                               unbilled_retries=retries, language="it")
    assert "**Ritentativi non fatturati.** macro (ZZREQ-3)" in s["memo_markdown"]
    assert committee_gaps_summary(_data(), _store(), capo=CAPO_OK, language="it")["unbilled_retries"] is None


def test_serializzabile_e_formattatori_coerenti():
    s = _sintetico_due_lacune()
    json.dumps(s)
    assert format_gaps_for_memo(s) == s["memo_markdown"]
    assert format_gaps_for_email(s) == s["email_line"]
