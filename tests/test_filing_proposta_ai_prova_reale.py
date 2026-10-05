"""Fase C: difetti trovati dalla prova reale (2026-10-03) su PDF IR veri (documenti A-D: emittenti europei
nello scratchpad, mai nel repo), riprodotti con PDF sintetici."""
import pytest

from bellomberg.market_data import filing_proposta_ai as fp
from tests.filing_pdf_sintetici import pdf, semestrale_kore_it, semestrale_nova
from tests.test_filing_proposta_ai_verifica import PERIODO, URL, _proposta, _verifica


def test_periodo_con_gruppo_ripetuto_usa_la_regola_di_ripiego(tmp_path):
    """Documento A: alternativa con (?P<fine>) ripetuto -> regex che non compila; prima: nulla di salvabile."""
    proposta = _proposta()
    proposta["periodo"] = (r"as of (?P<fine>\d{1,2} \w+ \d{4})|(?P<mesi>six) months ended (?P<fine>\d{1,2} \w+ \d{4})")
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True, out
    assert out["periodo"] == {"inizio": "2025-10-01", "fine": "2026-03-31"}
    assert out["profilo"]["verifica"]["periodo"] != proposta["periodo"]
    assert out["regole"]["periodo"]["origine"] == "ripiego"
    assert any("periodo" in m and "ripiego" in m for m in out["avvisi"])


def test_periodo_con_gruppi_alternativi_non_rompe_la_verifica(tmp_path):
    """Documento B: (?P<inizio>…)|(?P<fine>…) lasciava un gruppo a None: TypeError criptico."""
    proposta = _proposta()
    proposta["periodo"] = r"(?P<inizio>\d{1,2} [A-Za-z]+ \d{4})|(?P<fine>\d{1,2} [A-Za-z]+ \d{4})"
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True, out
    assert not any("TypeError" in m for m in out["motivi"] + out["avvisi"])


def test_emittente_non_trovato_ripiega_sul_nome(tmp_path):
    """Documenti B e C: «Kore\\s+S\\.p\\.A\\.» non compare nelle prime pagine (solo «KORE»)."""
    proposta = _proposta()
    proposta["emittente"] = r"Nova\s+AG\s+Holding\s+S\.p\.A\."
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True, out
    assert out["regole"]["emittente"]["origine"] == "ripiego"


def test_prova_tipo_non_trovata_ripiega_sulla_regola_del_tipo(tmp_path):
    proposta = _proposta()
    proposta["prova_tipo"] = r"Interim Statement Q2"
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True, out
    assert out["regole"]["tipo"]["origine"] == "ripiego"


def test_nessuna_regola_del_periodo_funziona_motivo_chiaro(tmp_path):
    proposta = _proposta()
    proposta["periodo"] = r"for the year (?P<fine>\d{4})"
    contenuto = pdf([["Nova AG", "Half-Year Financial Report", "Outlook", "We expect growth for the group.",
                      "Risks and opportunities", "Risks are unchanged in the consolidated group.", "Notes"]])
    proposta["sezioni"] = {"prospettive": {"inizio": "Outlook", "fine": "Risks and opportunities"}}
    _, out = _verifica(tmp_path, contenuto, proposta)
    assert out["salvabile"] is False
    assert any("periodo" in m for m in out["motivi"]), out["motivi"]


def test_fine_che_ricorre_nell_indice_e_in_una_pagina_di_capitolo_non_e_una_testata(tmp_path):
    """Documento A: «Condensed Consolidated Interim Financial Statements» nell'indice e due volte nella pagina
    di capitolo (3 righe) era scartata come intestazione ripetuta; la testata vera e' in testa a 3+ pagine."""
    testata = "Nova AG Half-Year Financial Report 31 March 2026"
    contenuto = pdf([
        ["Nova AG", "Half-Year Financial Report", "31 March 2026"],
        [f"{testata} 2", "Contents", "Risks and opportunities 3", "Condensed Interim Financial Statements 4"],
        [f"{testata} 3", "Interim Report", "Risks and opportunities", "Export restrictions could limit sales.",
         "The consolidated group hedges currency risks for the six months ended 31 March 2026."],
        [f"{testata} 4", "Condensed Interim Financial Statements", "Condensed Interim Financial Statements"],
        [f"{testata} 5", "Interim Report", "Notes", "The accounting policies are unchanged."],
        [f"{testata} 6", "Interim Report", "Responsibility Statement"],
    ])
    proposta = _proposta(rischi={"inizio": "Risks and opportunities", "fine": "Condensed Interim Financial Statements"},
                         gestione={"inizio": "Notes", "fine": "Interim Report"})
    _, out = _verifica(tmp_path, contenuto, proposta)
    assert [s["nome"] for s in out["verificate"]] == ["rischi"], out
    motivi = {s["nome"]: s["motivo"] for s in out["scartate"]}
    assert "intestazione di pagina" in motivi["gestione"]


def test_righe_candidate_non_escludono_un_titolo_ripetuto_fuori_dalle_testate(tmp_path):
    from tests.filing_pdf_sintetici import pdf as crea
    path = tmp_path / "x.pdf"
    pagine = [["Nova AG report", "Intro", "Business Outlook", "We expect growth.", "Text here.", "Footer"]
              for _ in range(3)]
    path.write_bytes(crea(pagine))
    testi = [r["testo"] for r in fp.estrai_input(path)["righe"]]
    assert "Business Outlook" in testi


def test_periodo_italiano_in_mesi_di_ripiego(tmp_path):
    from bellomberg.market_data.filing_verifica import _periodo_con_prova
    testo = "Relazione per i sei mesi chiusi al 30 giugno 2026 del Gruppo Kore."
    inizio, fine, _ = _periodo_con_prova(testo, fp._PERIODI_RIPIEGO["it"][1], "semestrale", None, URL, "0" * 64,
                                         regola="piu_recente")
    assert (inizio.isoformat(), fine.isoformat()) == ("2026-01-01", "2026-06-30")


def test_kore_italiano_con_periodo_di_ripiego(tmp_path):
    proposta = {"tipo": "semestrale", "lingua": "it", "scartate": [], "periodo": None, "emittente": None,
                "prova_tipo": None,
                "sezioni": {"prospettive": {"inizio": "EVOLUZIONE PREVEDIBILE DELLA GESTIONE",
                                            "fine": "OPERAZIONI CON PARTI CORRELATE"}}}
    path = tmp_path / "kore.pdf"
    path.write_bytes(semestrale_kore_it())
    profilo = fp.profilo_ir("KORE.MI", nome="Kore S.p.A.", ir_urls=[URL], proposta=proposta,
                            sha256="0" * 64, modello="m", lingua_rilevata="it")
    out = fp.verifica_proposta(path, url=URL, profilo=profilo, proposta=proposta)
    assert out["salvabile"] is True, out
    assert out["periodo"] == {"inizio": "2026-01-01", "fine": "2026-06-30"}


@pytest.mark.parametrize("testo,lingua", [
    ("NOVA GROUP HALF-YEAR FINANCIAL REPORT AS OF 30 JUNE 2026", "en"),
    ("Half-Year Financial Report at 30 June 2026", "en"),
    ("RELAZIONE FINANZIARIA SEMESTRALE AL 30 GIUGNO 2026", "it"),
    ("BILANCIO CONSOLIDATO SEMESTRALE ABBREVIATO AL 30 GIUGNO 2026", "it"),
])
def test_semestrale_con_sola_data_di_fine(testo, lingua):
    """Documenti B e C: la semestrale dichiara solo la fine («al 30 giugno 2026»); la durata e' la parola
    «semestrale»/«half-year» del titolo. Comparativi dell'anno prima: vince la fine piu' recente."""
    corpo = testo + "\n" + testo.replace("2026", "2025")
    assert fp._periodo_regge(corpo, next(rx for rx in fp._PERIODI_RIPIEGO[lingua]
                                         if fp._periodo_regge(corpo, rx, "semestrale", URL)),
                             "semestrale", URL)[1].isoformat() == "2026-06-30"


def test_half_e_semestrale_non_valgono_per_un_trimestrale():
    from bellomberg.market_data.filing_verifica import _periodo_con_prova
    with pytest.raises(ValueError):
        _periodo_con_prova("Half-Year Financial Report at 30 June 2026",
                           r"(?P<mesi>half)[-\s]*year financial report at (?P<fine>\d{1,2} \w+ \d{4})",
                           "trimestrale", None, URL, "0" * 64, regola="piu_recente")


def test_sezione_quasi_vuota_scartata(tmp_path, monkeypatch):
    """Documento C: «Garanzie, impegni e passivita' potenziali» verificata con 19 caratteri di corpo."""
    monkeypatch.setattr(fp, "MIN_CARATTERI_SEZIONE", 100)
    proposta = _proposta(vuota={"inizio": "Review of results of operations", "fine": r"Revenue 7,475 .*"})
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    motivi = {s["nome"]: s["motivo"] for s in out["scartate"]}
    assert "quasi vuota" in motivi["vuota"], out


def test_inizio_senza_fine_dopo_diagnosi_chiara(tmp_path):
    """Documento C: inizio presente (indice e corpo) ma la fine scelta non segue il corpo: il motivo lo dice."""
    proposta = _proposta(rotta={"inizio": "Risks and opportunities", "fine": "Contents"})
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    motivi = {s["nome"]: s["motivo"] for s in out["scartate"]}
    assert "nessuna seguita dalla fine" in motivi["rotta"], motivi


def test_profilo_cambiato_rende_dovuto_il_run_programmato(tmp_path):
    """Documento D: la variante IR aggiunta al profilo ESEF non partiva (run ESEF di poche ore prima)."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from bellomberg.storage.filing_store import FilingStore, RunNotDue, ensure_schema
    from tests.test_filing_variante_ir import _esef, _misto
    db = tmp_path / "f.sqlite"
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    s = FilingStore(db)
    s.set_profile("NOVA.MI", _esef(), interval_hours=24)
    run = s.start_run("NOVA.MI", "scheduled")
    s.finish_run(run["id"], status="ok", reason=None, result={})
    with pytest.raises(RunNotDue):
        s.start_run("NOVA.MI", "scheduled")
    assert s.next_due() == []
    s.set_profile("NOVA.MI", _misto(), interval_hours=24)
    assert [d["ticker"] for d in s.next_due()] == ["NOVA.MI"]
    assert datetime.fromisoformat(s.next_due_at("NOVA.MI")) <= datetime.now(timezone.utc) + timedelta(seconds=1)
    nuovo = s.start_run("NOVA.MI", "scheduled")
    assert nuovo["profile_version"] == 2 and nuovo["trigger"] == "scheduled"


def test_accept_variante_avvia_subito_il_confronto(rotte_ai):
    rotte_ai.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_ROTTE})
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    from tests.filing_esef_sintetici import LEI_NOVA
    store = rotte_ai.service.store
    store.set_profile("NOVA.DE", profilo_esef("NOVA.DE", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome",
                                              lingua="en"), interval_hours=24)
    run = store.start_run("NOVA.DE", "scheduled")
    store.finish_run(run["id"], status="ok", reason=None, result={})
    r = rotte_ai.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA_ROTTE, "aggiungi_variante": True})
    assert r.status_code == 200
    # Il manager del backend (avvia_aggiornamento) esegue run_due: il profilo cambiato e' dovuto subito.
    assert rotte_ai.avviati == ["activation"]
    assert [d["ticker"] for d in store.next_due()] == ["NOVA.DE"]


def test_pdf_quasi_senza_testo_nella_pipeline_dice_ocr(tmp_path):
    """Documento A, anno prima: 42 pagine su 44 senza testo -> «lingua: prova testuale assente» (criptico)."""
    from bellomberg.market_data.filing_verifica import verifica_documento
    path = tmp_path / "scan.pdf"
    path.write_bytes(pdf([["Nova AG Half-Year Financial Report"], [], [], [], [], [], ["31 March 2025"]]))
    profilo = fp.profilo_ir("NOVA.DE", nome="Nova AG", ir_urls=[URL], proposta=_proposta(), sha256="0" * 64,
                            modello="m", lingua_rilevata="en")
    out = verifica_documento(path, url=URL, profilo=profilo)
    assert out["stato"] == "non_verificato"
    assert any("OCR" in m for m in out["motivi"]), out["motivi"]


from tests.test_filing_routes_ai import SHA as SHA_ROTTE, URL as URL_ROTTE, env as rotte_ai  # noqa: E402,F401


def _con_anno_prima(tmp_path, proposta, prima):
    path = tmp_path / "h1-2026.pdf"
    path.write_bytes(semestrale_nova(2026))
    altro = tmp_path / "h1-2025.pdf"
    altro.write_bytes(prima)
    profilo = fp.profilo_ir("NOVA.DE", nome="Nova AG", ir_urls=[URL], proposta=proposta, sha256="0" * 64,
                            modello="m", lingua_rilevata="en")
    return fp.verifica_proposta(path, url=URL, profilo=profilo, proposta=proposta,
                                altri=[(altro, "https://ir.nova.example/reports/h1-2025.pdf")])


def test_regole_scelte_perche_reggano_anche_sul_pdf_dell_anno_prima(tmp_path):
    """Documento D: «Kore\\s+S\\.p\\.A\\.» reggeva sul 2026 ma non sul 2025 (prova emittente assente)."""
    proposta = _proposta()  # emittente «Nova\\s+AG»
    out = _con_anno_prima(tmp_path, proposta, semestrale_nova(2025, emittente="NOVA Aktiengesellschaft"))
    assert out["salvabile"] is True, out
    assert out["regole"]["emittente"]["origine"] == "ripiego"
    altro = out["altri"][0]
    assert altro["stato"] == "ok", altro
    assert altro["periodo"] == {"inizio": "2024-10-01", "fine": "2025-03-31"}
    assert set(altro["sezioni_ok"]) == {"prospettive", "rischi"}


def test_sezione_che_non_regge_sull_anno_prima_dichiarata(tmp_path):
    out = _con_anno_prima(tmp_path, _proposta(), semestrale_nova(2025, titolo_rischi="Risk report"))
    assert {s["nome"] for s in out["verificate"]} == {"prospettive", "rischi"}  # si salva cio' che regge sul PDF analizzato
    altro = out["altri"][0]
    # «Risks and opportunities» e' inizio di rischi e fine di prospettive: nel 2025 cadono entrambe.
    assert altro["stato"] == "ok" and altro["sezioni_ok"] == []
    assert set(altro["sezioni_mancanti"]) == {"prospettive", "rischi"}
    assert any("h1-2025" in a and "rischi" in a for a in out["avvisi"]), out["avvisi"]


def test_altro_documento_illeggibile_dichiarato_senza_bloccare(tmp_path):
    out = _con_anno_prima(tmp_path, _proposta(), b"<html>pagina IR</html>")
    assert out["salvabile"] is True
    assert out["altri"][0]["stato"] == "non_verificato"
    assert any("h1-2025" in a for a in out["avvisi"])


def test_nome_del_documento_negli_avvisi():
    assert fp._nome_url("https://x.example/documents/1/Half+year+report+2025.pdf/9d1d-uuid?t=1") == "Half+year+report+2025.pdf"
    assert fp._nome_url("https://x.example/a/b/h1-2025.pdf") == "h1-2025.pdf"


def test_stima_dei_token_per_eccesso_sulle_misure_reali(monkeypatch):
    """Documento C: 29.987 caratteri d'input, 11.783 token reali; la stima con 3 caratteri/token dava 10.636."""
    monkeypatch.setattr(fp, "_fx", lambda: (1.0, "live"))
    out = fp.stima({"testo_input": "x" * 29_987, "caratteri_input": 29_987, "lingua_rilevata": "it"},
                   modello="claude-haiku-4-5")
    assert out["token_input_stimati"] >= 11_783
