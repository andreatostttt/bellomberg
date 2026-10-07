"""«Attiva i mancanti» col sito dell'emittente (decisione PM 05/10, opzione B), end-to-end su DB in tmp.

Titolo senza SEC/ESEF -> relazioni in PDF dal sito -> profilo IR DETERMINISTICO (nessuna AI) salvato
solo se il PDF si verifica -> run Filing -> documento nell'archivio con origine «sito_emittente».
Sito, societa' e PDF inventati (ir.zztest.example), rete finta, nessuna spesa.
"""
import io
import json
import socket
import sqlite3

import pytest

from bellomberg.market_data import esef_sito, filing_attivazione as fa, lettore_trimestrali
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema

SITO = "https://ir.zztest.example/"
REPORTS = SITO + "investors/reports"
FILE = SITO + "files/"
NOME = "Zztest Group SE"
TICKER = "ZZTEST.DE"


def _pdf(*righe):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for r in righe:
        c.drawString(60, y, r)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def annuale(anno, nome=NOME):
    return _pdf(nome, f"Annual Report {anno}", "Consolidated financial statements of the group",
                f"for the twelve months ended 31 December {anno}",
                "The group and the management of the company report on the results of the year.",
                f"Risks of the business in {anno} and the outlook of the group are described below.",
                "Demand for the products of the group is uncertain and the order book is stable.")


def semestrale(anno, nome=NOME):
    return _pdf(nome, f"Half-Year Financial Report {anno}", "Consolidated interim financial statements of the group",
                f"for the six months ended 30 June {anno}",
                "The group and the management of the company report on the results of the half-year.",
                f"Risks of the business in {anno} and the outlook of the group are described below.",
                "Demand for the products of the group is growing and the order book is larger.")


class Risposta:
    def __init__(self, url, corpo, status=200, tipo="text/html; charset=utf-8"):
        self.url, self.content, self.status_code = url, corpo, status
        self.headers = {"Content-Type": tipo, "Content-Length": str(len(corpo))}
        self.encoding = "utf-8"
        self.raw = None

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, n=65536):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def close(self):
        pass


def _pagina(*link):
    return ("<html><body>" + "".join(f'<a href="{h}">{t}</a>' for h, t in link) + "</body></html>").encode()


@pytest.fixture
def rete(monkeypatch):
    """Sito finto: {url: bytes | Risposta}; 404 altrove; chiamate registrate. DNS finto pubblico."""
    sito = {}
    chiamate = []
    monkeypatch.setattr(lettore_trimestrali.socket, "getaddrinfo",
                        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])

    def get(url, **kw):
        chiamate.append(url)
        v = sito.get(url)
        if v is None:
            return Risposta(url, b"", status=404)
        if isinstance(v, Risposta):
            return v
        return Risposta(url, v, tipo="application/pdf" if v.startswith(b"%PDF") else "text/html; charset=utf-8")
    monkeypatch.setattr(lettore_trimestrali.requests, "get", get)
    monkeypatch.setattr(esef_sito, "_info_yfinance", lambda t: {"website": SITO})
    monkeypatch.setattr(esef_sito, "PAUSA_S", 0)
    sito["chiamate"] = chiamate
    return sito


def _sito_annuali(rete, extra=()):
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"),
                                  (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024"),
                                  (FILE + "Zztest-Investor-Presentation-2026.pdf", "Investor presentation"), *extra),
                 FILE + "Zztest-Annual-Report-2025.pdf": annuale(2025),
                 FILE + "Zztest-Annual-Report-2024.pdf": annuale(2024)})


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "filing.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


def _proponi(ticker, nome=None, rifiutati=frozenset()):
    return {"ticker": ticker, "nome": NOME,
            "sec": {"stato": "nessuno", "candidati": [], "motivo": "nessun emittente SEC"},
            "esef": {"stato": "nessuno", "candidati": [], "motivo": "nessun LEI ESEF"}}


def _attiva(store, tmp_path):
    return fa.attiva(store, TICKER, proponi_fn=_proponi, pref_path=tmp_path / "pref.json")


def _run(store, tmp_path):
    svc = FilingService(store, tmp_path / "filing_archive", indexer=lambda *a: {"status": "skipped"}, impronta_fn=None)
    return svc.run(TICKER)


def test_attivazione_crea_profilo_ir_dal_sito_e_il_run_archivia_con_l_origine(store, rete, tmp_path):
    _sito_annuali(rete)
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and esito["fonte"] == "sito_emittente", esito
    assert esito["origine"] == "sito dell'emittente, non archivio ufficiale (OAM)"
    assert "relazione annuale al 2025-12-31 dal sito dell'emittente, non archivio ufficiale (OAM)" in esito["motivo"]
    p = store.get_profile(TICKER)["profile"]
    assert p["fonti"] == ["ir"] and p["origine_collegamento"] == "sito_emittente" and p["tipo"] == "annuale"
    assert p["ir_urls"] == [FILE + "Zztest-Annual-Report-2025.pdf", FILE + "Zztest-Annual-Report-2024.pdf"]
    assert p["origine_documenti"] == esef_sito.ETICHETTA_SITO and "proposta_ai" not in p
    assert p["lingua"] == "en" and p["verifica"]["periodo"] and p["sezioni_intero"] is True
    assert not any("Presentation" in c for c in rete["chiamate"])  # la presentazione non si scarica

    run = _run(store, tmp_path)
    assert run["status"] in ("ok", "parziale"), run.get("reason")
    candidati = run["result"]["candidati"]
    verificati = [c for c in candidati if c["stato"] == "verificato"]
    assert {c["fonte"] for c in candidati} == {"IR"} and all(c["origine"] == "sito_emittente" for c in candidati)
    assert sorted(c["metadati"]["periodo_fine"] for c in verificati) == ["2024-12-31", "2025-12-31"]
    for c in verificati:
        assert str(tmp_path / "filing_archive") in c["path"] and c["sha256"]


def test_run_successivo_col_semestrale_aggiorna_il_candidato_e_tiene_l_etichetta(store, rete, tmp_path):
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    assert _run(store, tmp_path)["status"] in ("ok", "parziale")
    # il sito pubblica la semestrale (e c'e' quella dell'anno prima)
    rete[REPORTS] = _pagina((FILE + "Zztest-Half-Year-Financial-Report-2026.pdf", "Half-Year Report 2026"),
                            (FILE + "Zztest-Half-Year-Financial-Report-2025.pdf", "Half-Year Report 2025"),
                            (FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"),
                            (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024"))
    rete[FILE + "Zztest-Half-Year-Financial-Report-2026.pdf"] = semestrale(2026)
    rete[FILE + "Zztest-Half-Year-Financial-Report-2025.pdf"] = semestrale(2025)
    (esef_sito._cache_dir() / f"{TICKER}.json").unlink()  # cache di 7 giorni scaduta
    agg = fa.aggiorna_dal_sito(store, TICKER)
    assert agg["esito"] == "aggiornato" and "semestrale al 2026-06-30" in agg["motivo"], agg
    riga = store.get_profile(TICKER)
    assert riga["version"] == 2 and riga["profile"]["tipo"] == "semestrale"
    assert riga["profile"]["origine_collegamento"] == "sito_emittente"
    run = _run(store, tmp_path)
    verificati = [c for c in run["result"]["candidati"] if c["stato"] == "verificato"]
    assert max(c["metadati"]["periodo_fine"] for c in verificati) == "2026-06-30"
    assert all(c["fonte"] == "IR" and c["origine"] == "sito_emittente" for c in run["result"]["candidati"])
    assert fa.aggiorna_dal_sito(store, TICKER)["esito"] == "invariato"


def test_controllo_giornaliero_aggiorna_i_profili_del_sito(store, rete, tmp_path, monkeypatch):
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    visti = []
    monkeypatch.setattr(fa, "aggiorna_dal_sito", lambda st, t: visti.append(t) or {"ticker": t, "esito": "invariato"})
    esiti = esef_sito.scopri_giornaliero(store, consigliere_fn=lambda: False, pref_fn=lambda: {"esiti": {}, "esclusi": []})
    assert visti == [TICKER] and {"ticker": TICKER, "esito": "invariato"} in esiti
    visti.clear()
    esef_sito.scopri_giornaliero(store, escludi={TICKER}, consigliere_fn=lambda: False, pref_fn=lambda: {"esiti": {}})
    assert visti == []


def test_sito_che_blocca_i_bot_resta_dichiarato_senza_profilo(store, rete, tmp_path):
    rete[SITO + "robots.txt"] = Risposta(SITO + "robots.txt", b"blocked", status=403)
    rete[SITO] = Risposta(SITO, b"blocked", status=403)
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "sito blocca i bot (HTTP 403" in esito["motivo"], esito
    assert esito["motivo"].startswith("SEC: nessun emittente SEC; ESEF: nessun LEI ESEF")
    assert store.get_profile(TICKER) is None


def test_solo_presentazioni_senza_periodo_nessun_profilo(store, rete, tmp_path):
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zztest-Investor-Presentation-2026.pdf", "Investor presentation"),
                                  (FILE + "2026-03-11-Zztest-Conference-Call-FY2025.pdf", "Call"))})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "nessun documento periodico ammesso tra 2 PDF" in esito["motivo"]
    assert "presentazione" in esito["motivo"] and store.get_profile(TICKER) is None


def test_pdf_di_un_altra_societa_non_crea_il_profilo(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = annuale(2025, nome="Qqsyn Holding AG")
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "non trovato nel documento" in esito["motivo"], esito
    assert store.get_profile(TICKER) is None  # mai un profilo non verificato


def test_periodo_del_testo_discorde_dal_nome_profilo_non_verificato(store, rete, tmp_path):
    # decisione PM 06/10 sera: periodo ambiguo = profilo creato, NON verificato, con le due date viste
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = annuale(2024)
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and esito["verificato"] is False, esito
    assert any("periodo ambiguo" in c and "il testo 2024-12-31" in c for c in esito["controlli_non_superati"])
    assert esito["periodo_stato"] == "da_confermare" and "NON verificata" in esito["motivo"]
    p = store.get_profile(TICKER)["profile"]
    assert p["verificato"] is False and p["periodo_stato"] == "da_confermare"
    assert set(p["periodi_visti"]) == {"2024-12-31", "2025-12-31"}
    assert p["controlli_non_superati"] == esito["controlli_non_superati"]


def test_semestrale_col_periodo_solo_numerico_si_attiva(store, rete, tmp_path):
    # prova reale 06/10: copertina «HALF-YEARLY FINANCIAL REPORT 2026 1/1–30/6/2026», nessuna frase
    # «six months ended»: le regole di ripiego di filing_proposta_ai non bastano, quelle del sito si'
    def h1(anno):
        return _pdf(NOME, f"HALF-YEARLY FINANCIAL REPORT {anno}", f"1/1–30/6/{anno}",
                    "Interim consolidated financial statements of the group",
                    "The group and the management of the company report on the results of the half-year.",
                    "Demand for the products of the group is growing and the order book is larger.")
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zztest-Halbjahresbericht-2026.pdf", ""),
                                  (FILE + "Zztest-Halbjahresbericht-2025.pdf", "")),
                 FILE + "Zztest-Halbjahresbericht-2026.pdf": h1(2026),
                 FILE + "Zztest-Halbjahresbericht-2025.pdf": h1(2025)})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato", esito
    assert store.get_profile(TICKER)["profile"]["verifica"]["periodo"] == fa._PERIODI_SITO["semestrale"][0]


def test_regole_del_periodo_del_sito_su_testi_reali_sintetici():
    tipo_rx = fa._PERIODI_SITO
    import re
    testo = "HALF- YEARLY FINANCIAL REPORT 2026 1/1–30/6 /2026 KEY FIGURES"
    assert fa._periodo_sito(testo, "semestrale", "u", []) == tipo_rx["semestrale"][0]
    testo = "ZZTEST HALF-YEAR REPORT 2025 Group sales grew. Backlog as of June 30, 2025 increased"
    assert fa._periodo_sito(testo, "semestrale", "u", []) == tipo_rx["semestrale"][1]
    assert fa._periodo_sito("Annual report for the period January 1, 2025 to December 31, 2025", "annuale", "u",
                            []) == tipo_rx["annuale"][0]
    assert fa._periodo_sito("nessuna data qui", "semestrale", "u", []) is None
    assert all(re.compile(rx) for v in tipo_rx.values() for rx in v)


def test_pdf_che_non_supera_la_verifica_completa_profilo_non_verificato(store, rete, tmp_path):
    # emittente, tipo, perimetro e regola del periodo reggono, ma il periodo del testo e' nel futuro:
    # la verifica completa (filing_verifica) dice no: profilo creato NON verificato, dichiarato
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _pdf(
        NOME, "Annual Report 2025", "Consolidated financial statements of the group",
        "for the twelve months ended 31 December 2099",
        "The group and the management of the company report on the results of the year.")
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = rete[FILE + "Zztest-Annual-Report-2025.pdf"]
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and esito["verificato"] is False, esito
    assert any(c.startswith("PDF non verificato") for c in esito["controlli_non_superati"]), esito
    assert store.get_profile(TICKER)["profile"]["verificato"] is False
    # il run non si rompe: il documento resta «non verificato», dichiarato
    run = _run(store, tmp_path)
    assert run["result"]["candidati"] and not any(c["stato"] == "verificato" and c["url"].endswith("2025.pdf")
                                                  for c in run["result"]["candidati"])


def test_lei_esef_senza_depositi_passa_al_sito(store, rete, tmp_path):
    _sito_annuali(rete)
    lei = "ZZTEST00000000000099"

    def proponi(ticker, nome=None, rifiutati=frozenset()):
        return {**_proponi(ticker), "esef": {"stato": "univoco", "motivo": "LEI univoco",
                                             "candidati": [{"lei": lei, "nome": NOME, "origine": "nome"}]}}
    esito = fa.attiva(store, TICKER, proponi_fn=proponi, indice_fn=lambda l: {"righe": []},
                      pref_path=tmp_path / "pref.json")
    assert esito["esito"] == "attivato" and esito["fonte"] == "sito_emittente", esito
    assert esito["motivo"].endswith("nessun pacchetto ESEF col LEI ZZTEST00000000000099 nelle pagine visitate")


def test_lingua_ignota_nessun_profilo(store, rete, tmp_path):
    # testo senza parole: PDF illeggibile per la verifica, uno dei soli scarti rimasti (con identita' e blocchi)
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _pdf(NOME, "Annual Report 2025", "31.12.2025 123 456 789")
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "lingua del documento non determinata" in esito["motivo"], esito


def test_pdf_vietato_da_robots_non_si_scarica(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[SITO + "robots.txt"] = Risposta(SITO + "robots.txt", b"User-agent: *\nDisallow: /files/\n", tipo="text/plain")
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "robots.txt vieta /files/" in esito["motivo"], esito
    assert not any(c.startswith(FILE) for c in rete["chiamate"])


def test_titolo_usa_mai_il_sito_come_ripiego(store, rete, tmp_path):
    _sito_annuali(rete)
    esito = fa.attiva(store, "ZZUS", proponi_fn=_proponi, pref_path=tmp_path / "pref.json")
    assert esito["esito"] == "senza_fonte" and "sito" not in esito["motivo"] and rete["chiamate"] == []


# ---------------------------------------------------------------- revisione R-8 (06/10)

def test_r8_controllata_col_nome_della_capogruppo_mai_attivata(store, rete, tmp_path):
    sub = FILE + "Zztest-Group-Finance-BV-Half-Year-Report-2026.pdf"
    sub_prev = FILE + "Zztest-Group-Finance-BV-Half-Year-Report-2025.pdf"
    _sito_annuali(rete, extra=((sub, "Half-Year Report 2026"), (sub_prev, "Half-Year Report 2025")))
    rete[sub], rete[sub_prev] = semestrale(2026, nome="Zztest Group Finance BV"), semestrale(2025, nome="Zztest Group Finance BV")
    esito = _attiva(store, tmp_path)
    p = (store.get_profile(TICKER) or {}).get("profile") or {}
    assert sub not in (p.get("ir_urls") or []), esito
    assert "Zztest Group Finance BV" in esito["motivo"] and "altra entita'" in esito["motivo"], esito
    # la relazione della capogruppo resta attivabile
    assert esito["esito"] == "attivato" and p["ir_urls"][0] == FILE + "Zztest-Annual-Report-2025.pdf"


def test_r8_regola_d_identita_salvata_non_regge_sulla_controllata(store, rete, tmp_path):
    import re
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    rx = store.get_profile(TICKER)["profile"]["verifica"]["emittente"]
    assert re.search(rx, "Zztest Group SE Annual Report", re.I)
    assert not re.search(rx, "Zztest Group Finance BV Annual Report", re.I)
    perimetro = store.get_profile(TICKER)["profile"]["verifica"]["perimetro"]
    assert not re.search(perimetro, "Zztest Group SE Annual Report", re.I)  # il nome non prova il perimetro
    assert re.search(perimetro, "Consolidated financial statements", re.I)


def test_r8_lei_nel_pdf_diverso_nessun_profilo(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _pdf(
        NOME, "Annual Report 2025", "LEI: QQSYN000000000000077", "Consolidated financial statements of the group",
        "for the twelve months ended 31 December 2025",
        "The group and the management of the company report on the results of the year.")
    esito = fa._attiva_sito(store, TICKER, NOME, {}, "base", lei="ZZTEST00000000000099")
    assert esito["esito"] == "senza_fonte" and "LEI" in esito["motivo"], (esito["motivo"], esito.get("documenti"))


def test_r8_perimetro_non_si_regge_sul_nome_profilo_non_verificato(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _pdf(
        NOME, "Annual Report 2025", "Separate financial statements of the parent company only",
        "for the twelve months ended 31 December 2025",
        "The company and the management of the company report on the results of the year.",
        "Risks of the business in 2025 and the outlook of the company are described below.")
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = rete[FILE + "Zztest-Annual-Report-2025.pdf"]
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and esito["verificato"] is False, esito
    perche = [c for c in esito["controlli_non_superati"] if "perimetro consolidato non dichiarato" in c]
    assert perche and "il nome dell'emittente non conta" in perche[0]
    p = store.get_profile(TICKER)["profile"]
    assert p["verificato"] is False  # mai dichiarato verificato


def test_pdf_del_cdn_linkato_dalla_pagina_ir_attivato_con_l_etichetta(store, rete, tmp_path):
    altro = "https://s201.q4cdn.example/docs/Annual-Report-2025.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((altro, "Annual Report 2025"), (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024")),
                 altro: annuale(2025), FILE + "Zztest-Annual-Report-2024.pdf": annuale(2024)})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and esito["verificato"] is True, esito
    assert "https://s201.q4cdn.example/robots.txt" in rete["chiamate"]  # robots dell'host del PDF
    p = store.get_profile(TICKER)["profile"]
    assert p["ir_urls"][0] == altro and p["via_host"] == "s201.q4cdn.example"
    assert p["origine_documenti"] == "sito dell'emittente via s201.q4cdn.example, non archivio ufficiale (OAM)"
    assert esito["origine"] == p["origine_documenti"]
    run = _run(store, tmp_path)  # il run scarica dal CDN e verifica
    verificati = [c for c in run["result"]["candidati"] if c["stato"] == "verificato"]
    assert altro in {c["url"] for c in verificati}, run["result"]["candidati"]


def test_pdf_del_cdn_vietato_dal_suo_robots_non_si_scarica(store, rete, tmp_path):
    altro = "https://s201.q4cdn.example/docs/Annual-Report-2025.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((altro, "Annual Report 2025"), (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024")),
                 altro: annuale(2025), FILE + "Zztest-Annual-Report-2024.pdf": annuale(2024),
                 "https://s201.q4cdn.example/robots.txt": Risposta("", b"User-agent: *\nDisallow: /docs/\n", tipo="text/plain")})
    esito = _attiva(store, tmp_path)
    assert altro not in rete["chiamate"], rete["chiamate"]
    assert "robots.txt vieta /docs/" in json.dumps(esito, ensure_ascii=False), esito


def test_r8_download_verso_ip_non_pubblico_rifiutato(monkeypatch, rete, tmp_path):
    monkeypatch.setattr(lettore_trimestrali.socket, "getaddrinfo",
                        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))])
    r = fa._scarica_pdf_sito(FILE + "Zztest-Annual-Report-2025.pdf", tmp_path)
    assert r["stato"] == "errore" and "non pubblico" in r["motivo"] and rete["chiamate"] == []

    class NavConsente:  # robots gia' letto e favorevole: resta solo la difesa del download (public_only)
        bloccato, robots_ignoto = {}, {}

        def consentito(self, url):
            return True
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = annuale(2025)
    r = fa._scarica_pdf_sito(FILE + "Zztest-Annual-Report-2025.pdf", tmp_path, nav=NavConsente())
    assert r["stato"] == "errore" and "non pubblico" in r["motivo"] and rete["chiamate"] == [], r


def test_r8_aggiornamento_mai_verso_un_periodo_piu_vecchio(store, rete, tmp_path):
    h26, h25 = FILE + "Zztest-Half-Year-Financial-Report-2026.pdf", FILE + "Zztest-Half-Year-Financial-Report-2025.pdf"
    _sito_annuali(rete, extra=((h26, ""), (h25, "")))
    rete[h26], rete[h25] = semestrale(2026), semestrale(2025)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    assert store.get_profile(TICKER)["profile"]["tipo"] == "semestrale"
    rete[REPORTS] = _pagina((h26, ""), (FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"),
                            (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024"))
    (esef_sito._cache_dir() / f"{TICKER}.json").unlink()
    agg = fa.aggiorna_dal_sito(store, TICKER)
    riga = store.get_profile(TICKER)
    assert agg["esito"] == "invariato" and riga["version"] == 1 and riga["profile"]["tipo"] == "semestrale", agg
    # il documento del profilo sparisce dal sito: resta quello archiviato, dichiarato
    rete[REPORTS] = _pagina((FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"))
    (esef_sito._cache_dir() / f"{TICKER}.json").unlink()
    agg = fa.aggiorna_dal_sito(store, TICKER)
    assert agg["esito"] == "invariato" and "non e' piu' sul sito" in agg["motivo"], agg


def test_r8_pubblicazione_a_fine_mese_non_blocca_l_attivazione(store, rete, tmp_path):
    h26, h25 = FILE + "Zztest-Half-Year-Report-2026_31.07.2026.pdf", FILE + "Zztest-Half-Year-Report-2025_31.07.2025.pdf"
    _sito_annuali(rete, extra=((h26, ""), (h25, "")))
    rete[h26], rete[h25] = semestrale(2026), semestrale(2025)
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and "semestrale al 2026-06-30" in esito["motivo"], esito


def test_r8_cache_senza_accesso_si_rilegge(store, rete, tmp_path):
    rete[SITO] = Risposta(SITO, b"blocked", status=403)
    cartella = esef_sito._cache_dir()
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / f"{TICKER}.json").write_text(json.dumps({  # voce scritta prima del 05/10: niente «accesso»
        "ticker": TICKER, "at": "2026-10-01T10:00:00", "giorno": "2026-10-01", "sito": SITO, "pagine": [],
        "pacchetti": [], "pdf": [], "motivi": ["/: ValueError: HTTP 403"], "fallita": True}))
    esito = fa._attiva_sito(store, TICKER, NOME, {}, "base", trovato=json.loads((cartella / f"{TICKER}.json").read_text()))
    assert esito["esito"] == "senza_fonte" and "sito blocca i bot (HTTP 403)" in esito["motivo"], esito


def test_r8_run_rispetta_robots_e_non_scarica_due_volte(store, rete, tmp_path):
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    rete["chiamate"].clear()
    _run(store, tmp_path)
    pdf_get = [c for c in rete["chiamate"] if c.endswith(".pdf")]
    assert pdf_get and sorted(pdf_get) == sorted(set(pdf_get)), pdf_get  # una sola GET per PDF
    # dopo l'attivazione il sito vieta i documenti: il run non li scarica e lo dichiara
    rete[SITO + "robots.txt"] = Risposta(SITO + "robots.txt", b"User-agent: *\nDisallow: /files/\n", tipo="text/plain")
    rete["chiamate"].clear()
    run = _run(store, tmp_path)
    assert not any(c.startswith(FILE) for c in rete["chiamate"]), rete["chiamate"]
    assert "robots.txt vieta" in json.dumps(run["result"], ensure_ascii=False)


def test_r8_scarica_da_altro_host_solo_se_linkato_dalla_pagina_ir(rete, tmp_path):
    altro = "https://s3.q4cdn.example/docs/Annual-Report-2025.pdf"
    rete[altro] = annuale(2025)
    r = fa._scarica_pdf_sito(altro, tmp_path, dominio="zztest.example")
    assert r["stato"] == "errore" and "altro dominio (s3.q4cdn.example)" in r["motivo"] and rete["chiamate"] == []
    nav = esef_sito.Navigatore("zztest.example")  # il navigatore del sito: per il CDN se ne usa un altro
    r = fa._scarica_pdf_sito(altro, tmp_path, dominio="zztest.example", via_host="s3.q4cdn.example", nav=nav)
    assert r["stato"] == "ok", r
    assert rete["chiamate"] == ["https://s3.q4cdn.example/robots.txt", altro]
    # revisione R-FONTI S6: un host che non e' una piattaforma IR non si scarica nemmeno con via_host
    terzo = "https://cdn.qqsyn.example/docs/Annual-Report-2025.pdf"
    rete["chiamate"].clear()
    r = fa._scarica_pdf_sito(terzo, tmp_path, dominio="zztest.example", via_host="cdn.qqsyn.example")
    assert r["stato"] == "errore" and rete["chiamate"] == []


def test_r8_righe_ir_di_ogni_profilo_portano_l_origine(store, rete, tmp_path):
    # anche un profilo IR non creato dal sito (es. proposta AI accettata): l'origine resta dichiarata
    from bellomberg.market_data.filing_pipeline import esegui_profilo
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    profilo = {**store.get_profile(TICKER)["profile"], "origine_collegamento": "proposta_ai"}
    out = esegui_profilo(profilo, archivio=tmp_path / "arch_ai")
    assert out["candidati"] and all(c["fonte"] == "IR" and c["origine"] == "sito_emittente" for c in out["candidati"])


# ---------------------------------------------------------------- controllo leggero (R-8 C4, HEAD)

def _scaduto(store):
    from bellomberg.storage.filing_store import SCHEMA
    guardia = next(s for s in SCHEMA if "filing_runs_final_immutable" in s)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER filing_runs_final_immutable")
        conn.execute("UPDATE filing_runs SET started_at='2026-01-01T00:00:00+00:00'")
        conn.execute(guardia)


@pytest.fixture
def head(monkeypatch, rete):
    """HEAD finta: {url: headers | Risposta}; chiamate registrate."""
    firme, chiamate = {}, []

    def fai(url, **kw):
        chiamate.append(url)
        v = firme.get(url, {"ETag": '"v1"', "Content-Length": "1234"})
        if isinstance(v, Risposta):
            return v
        r = Risposta(url, b"")
        r.headers = dict(v)
        return r
    monkeypatch.setattr(lettore_trimestrali.requests, "head", fai)
    firme["chiamate"] = chiamate
    return firme


def _servizio(store, tmp_path):
    return FilingService(store, tmp_path / "filing_archive", indexer=lambda *a: {"status": "skipped"})


def test_r8_controllo_leggero_con_la_head_niente_download(store, rete, head, tmp_path):
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    svc = _servizio(store, tmp_path)
    primo = svc.run_programmato(TICKER)
    assert primo["status"] in ("ok", "parziale") and primo["result"]["impronta_depositi"]["fonte"] == "sito"
    _scaduto(store)
    rete["chiamate"].clear()
    leggero = svc.run_programmato(TICKER)
    assert leggero["status"] == "skipped", leggero.get("reason")
    assert not any(c.endswith(".pdf") for c in rete["chiamate"])  # nessun PDF riscaricato
    assert head["chiamate"] and all(c.endswith(".pdf") for c in head["chiamate"])
    # il sito cambia il PDF (firma diversa): run completo
    head[FILE + "Zztest-Annual-Report-2025.pdf"] = {"ETag": '"v2"', "Content-Length": "1234"}
    _scaduto(store)
    completo = svc.run_programmato(TICKER)
    assert completo["status"] in ("ok", "parziale"), completo.get("reason")


@pytest.mark.parametrize("caso,atteso", [
    ("senza_firma", "nessun controllo leggero"),
    ("head_403", "sito blocca i bot (HTTP 403) sulla HEAD"),
    ("robots", "robots.txt vieta"),
])
def test_r8_head_non_confrontabile_run_completo_col_motivo(store, rete, head, tmp_path, caso, atteso):
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    svc = _servizio(store, tmp_path)
    assert svc.run_programmato(TICKER)["status"] in ("ok", "parziale")
    pdf = FILE + "Zztest-Annual-Report-2025.pdf"
    if caso == "senza_firma":
        head[pdf] = {}
    elif caso == "head_403":
        head[pdf] = Risposta(pdf, b"", status=403)
    else:
        rete[SITO + "robots.txt"] = Risposta(SITO + "robots.txt", b"User-agent: Bellomberg\nDisallow: /files/\n",
                                             tipo="text/plain")
    _scaduto(store)
    run = svc.run_programmato(TICKER)
    assert run["status"] != "skipped", run.get("reason")  # mai un controllo saltato in silenzio
    impronta = run["result"].get("impronta_depositi") or {}
    assert impronta.get("non_confrontabile") and atteso in impronta["motivo"], impronta


def test_r8_due_run_non_confrontabili_di_fila_mai_saltati(store, rete, head, tmp_path):
    _sito_annuali(rete)
    assert _attiva(store, tmp_path)["esito"] == "attivato"
    head[FILE + "Zztest-Annual-Report-2025.pdf"] = {}  # il sito non da' mai una firma
    svc = _servizio(store, tmp_path)
    assert svc.run_programmato(TICKER)["result"]["impronta_depositi"]["non_confrontabile"]
    _scaduto(store)
    secondo = svc.run_programmato(TICKER)
    assert secondo["status"] != "skipped", secondo.get("reason")


# ---------------------------------------------------------------- decisione PM 06/10 sera: «piu' aperti»

def test_ultimo_trimestre_dal_comunicato_dei_risultati_attivato_ed_etichettato(store, rete, tmp_path):
    def comunicato(anno):
        return _pdf(NOME, f"Zztest reports results for the second quarter {anno}",
                    f"Quarterly results for the three months ended 30 June {anno}",
                    "The group and the management of the company report on the results of the quarter.",
                    "Consolidated revenue of the group grew and the order book is larger.")
    q26, q25 = FILE + "Zztest-Q2-2026-Earnings-Release.pdf", FILE + "Zztest-Q2-2025-Earnings-Release.pdf"
    _sito_annuali(rete, extra=((q26, ""), (q25, "")))
    rete[q26], rete[q25] = comunicato(2026), comunicato(2025)
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato", esito
    assert esito["tipo_documento"] == "comunicato_risultati" and "comunicato dei risultati trimestrale" in esito["motivo"]
    p = store.get_profile(TICKER)["profile"]
    assert p["ir_urls"][0] == q26 and p["tipo"] == "trimestrale" and p["tipo_documento"] == "comunicato_risultati"
    assert isinstance(p["verificato"], bool) and "controlli_non_superati" in p


def test_pdf_illeggibile_resta_uno_scarto(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = b"%PDF-1.4 rotto"
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = b"%PDF-1.4 rotto"
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and store.get_profile(TICKER) is None, esito
    assert "PDF non utilizzabile" in esito["motivo"] or "illeggibile" in esito["motivo"], esito


def test_identita_mai_rilassata_anche_col_resto_non_verificato(store, rete, tmp_path):
    # un'altra societa' col perimetro mancante: l'identita' blocca prima di ogni etichetta
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _pdf("Qqsyn Holding AG", "Annual Report 2025",
                                                       "Separate financial statements of the parent company only")
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = rete[FILE + "Zztest-Annual-Report-2025.pdf"]
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and store.get_profile(TICKER) is None, esito


def test_profilo_verificato_dichiarato_verificato(store, rete, tmp_path):
    _sito_annuali(rete)
    esito = _attiva(store, tmp_path)
    assert esito["verificato"] is True and esito["controlli_non_superati"] == [] and esito["periodo_stato"] == "certo"
    p = store.get_profile(TICKER)["profile"]
    assert p["verificato"] is True and p["controlli_non_superati"] == [] and p["tipo_documento"] == "relazione"
    assert p["periodo_stato"] == "certo" and p["via_host"] is None


def _redirect(url, verso):
    r = Risposta(url, b"", status=302)
    r.headers["Location"] = verso
    return r


def test_pdf_dell_emittente_che_rimanda_al_cdn_si_scarica_col_robots_del_cdn(store, rete, tmp_path):
    # prova dal vivo 06/10: l'URL del sito rimanda al «content hub» su un altro host
    _sito_annuali(rete)
    cdn = "https://zz-p-001.sitecorecontenthub.cloud/api/public/content/ar25"
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _redirect(FILE + "Zztest-Annual-Report-2025.pdf", cdn)
    rete[cdn] = annuale(2025)
    esito = _attiva(store, tmp_path)
    assert "https://zz-p-001.sitecorecontenthub.cloud/robots.txt" in rete["chiamate"]
    p = store.get_profile(TICKER)["profile"]
    assert p["host_documenti"] == ["zz-p-001.sitecorecontenthub.cloud"]
    # impianto 07/10: redirect fuori dominio = fonte esterna, entra etichettato ma MAI verificato (anche nel run)
    _mai_verificato(store, tmp_path, esito, TICKER, "zz-p-001.sitecorecontenthub.cloud")


def test_redirect_verso_un_cdn_che_vieta_col_robots_non_si_scarica(store, rete, tmp_path):
    _sito_annuali(rete)
    cdn = "https://zz-p-001.sitecorecontenthub.cloud/api/public/content/ar25"
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _redirect(FILE + "Zztest-Annual-Report-2025.pdf", cdn)
    rete[cdn] = annuale(2025)
    rete["https://zz-p-001.sitecorecontenthub.cloud/robots.txt"] = Risposta("", b"User-agent: *\nDisallow: /api/\n",
                                                                      tipo="text/plain")
    esito = _attiva(store, tmp_path)
    assert cdn not in rete["chiamate"], rete["chiamate"]
    assert "robots.txt di zz-p-001.sitecorecontenthub.cloud vieta /api/" in json.dumps(esito, ensure_ascii=False), esito


def test_impronta_con_pdf_su_due_host_legge_il_robots_di_ciascuno(rete, head):
    cdn = "https://s201.q4cdn.example/docs/Annual-Report-2025.pdf"
    profilo = {"tipo": "annuale", "ir_urls": [cdn, FILE + "Zztest-Annual-Report-2024.pdf"]}
    imp = esef_sito.impronta_sito(profilo)
    assert not imp.get("non_confrontabile"), imp
    assert {"https://s201.q4cdn.example/robots.txt", SITO + "robots.txt"} <= set(rete["chiamate"])
    assert [f[0] for f in imp["firme"]] == profilo["ir_urls"]


def test_attivazione_dal_sito_ir_scoperto_su_altro_dominio(store, rete, tmp_path):
    # seguito main 06/10: la home commerciale linka il sito IR su un dominio dedicato; il profilo lo dichiara
    irx = "https://www.zztest-investors.example/"
    rete.update({SITO: _pagina((irx, "Investor Relations")),
                 irx: _pagina((irx + "docs/Zztest-Annual-Report-2025.pdf", "Annual Report 2025"),
                              (irx + "docs/Zztest-Annual-Report-2024.pdf", "Annual Report 2024")),
                 irx + "docs/Zztest-Annual-Report-2025.pdf": annuale(2025),
                 irx + "docs/Zztest-Annual-Report-2024.pdf": annuale(2024)})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and esito["verificato"] is True, esito
    p = store.get_profile(TICKER)["profile"]
    assert p["ir_urls"][0] == irx + "docs/Zztest-Annual-Report-2025.pdf"
    assert p["sito_ir"] == irx and "scoperto da " + SITO in p["sito_ir_origine"]
    assert p["origine_documenti"].startswith("sito IR dell'emittente zztest-investors.example (scoperto dal sito")
    run = _run(store, tmp_path)
    assert "2025-12-31" in {c["metadati"]["periodo_fine"] for c in run["result"]["candidati"] if c["stato"] == "verificato"}


def test_regole_trimestrali_del_sito_su_testi_sintetici():
    # prova dal vivo 06/10 (feed delle piattaforme IR): copertine di 10-Q e di bilanci intermedi IFRS
    r = fa._PERIODI_SITO["trimestrale"]
    casi = {"FORM 10-Q QUARTERLY REPORT for the quarterly period ended June 30, 2026 Zztest Inc.": 2026,
            "Unaudited Interim Condensed Consolidated Financial Statements for the three and six-month periods "
            "ended June 30, 2026 Zztest Ltd.": 2026,
            "Interim statements for the three-month period ended March 31, 2026 Zztest": 2026}
    for testo in casi:
        assert fa._periodo_sito(testo, "trimestrale", "u", []) in r, testo
    assert fa._periodo_sito("Annual report for the twelve months ended December 31, 2025", "trimestrale", "u", []) is None


# ---------------------------------------------------------------- alias dichiarati dell'emittente (main 07/10)

LEI_ALIAS = "ZZALIAS0000000000077"


def _gleif_record(lei=LEI_ALIAS, legale="Zzindustria de Diseño Qqsyn, S.A.", altri=("ZZITEX",), translit=()):
    return {"id": lei, "attributes": {"entity": {
        "legalName": {"name": legale}, "otherNames": [{"name": n, "type": "TRADING_OR_OPERATING_NAME"} for n in altri],
        "transliteratedOtherNames": [{"name": n} for n in translit]}}}


def test_alias_normalizzati_apostrofi_accenti_forma_giuridica():
    assert fa.norm_alias("Z’Oréal S.A.") == fa.norm_alias("Z'Oreal") == fa.norm_alias("z'ORÉAL SE") == "zoreal"
    assert fa.norm_alias("Zzindustria de Diseño Qqsyn, S.A.") == "zzindustria de diseno qqsyn"
    import re
    rx = fa.regex_alias("Z'Oreal")
    for testo in ("Z’ORÉAL Universal Registration Document", "Z'Oréal annual report", "zoreal group"):
        assert re.search(rx, testo, re.I), testo
    assert not re.search(rx, "Zoreality Ltd annual report", re.I)


def test_alias_dalle_fonti_dichiarate_con_la_fonte(monkeypatch):
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [
        _gleif_record(lei="QQSYN0000000000000099", legale="Qqaltra Holding AG"),  # altro LEI: ignorato
        _gleif_record(translit=("Zzindustria de Diseno Qqsyn SA",))])
    monkeypatch.setattr(esef_sito, "_info_yfinance", lambda t: {"longName": "Zzindustria de Diseño Qqsyn, S.A.",
                                                                 "shortName": "ZZITEX", "website": "x"})
    proposta = {"esef": {"candidati": [{"lei": LEI_ALIAS, "nome": "ZZINDUSTRIA DE DISENO QQSYN SA"}]}}
    alias, avvisi = fa.alias_emittente("ZZITX.MC", "Zzindustria Qqsyn", lei=LEI_ALIAS, proposta=proposta)
    fonti = {a["nome"]: a["fonte"] for a in alias}
    assert fonti == {"Zzindustria Qqsyn": "nome del titolo", "ZZINDUSTRIA DE DISENO QQSYN SA": "ESEF (filings.xbrl.org)",
                     "ZZITEX": "GLEIF otherNames"}, fonti  # doppioni normalizzati tolti, prima fonte che vince
    assert "Qqaltra" not in str(alias) and avvisi == []


def test_alias_fonti_in_errore_dichiarate_e_alias_generico_scartato(monkeypatch):
    def rotta(q):
        raise ConnectionError("giu")
    monkeypatch.setattr(esef_sito, "gleif_lei_records", rotta)
    monkeypatch.setattr(esef_sito, "_info_yfinance", lambda t: {"shortName": "Group"})
    alias, avvisi = fa.alias_emittente("ZZ.PA", "Zzalfa SA", lei=LEI_ALIAS)
    assert [a["nome"] for a in alias] == ["Zzalfa SA"]
    assert any(a.startswith("alias GLEIF non letti (ConnectionError") for a in avvisi), avvisi
    assert any("troppo generico" in a and "Group" in a for a in avvisi), avvisi


def _sito_con(rete, testo_nome):
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"),
                                  (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024")),
                 FILE + "Zztest-Annual-Report-2025.pdf": annuale(2025, nome=testo_nome),
                 FILE + "Zztest-Annual-Report-2024.pdf": annuale(2024, nome=testo_nome)})


def test_documento_col_nome_breve_gleif_attivato_e_alias_dichiarato(store, rete, tmp_path, monkeypatch):
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [_gleif_record()])
    _sito_con(rete, "ZZITEX")
    esito = fa._attiva_sito(store, TICKER, "Zzindustria de Diseño Qqsyn, S.A.", {}, "base", lei=LEI_ALIAS)
    assert esito["esito"] == "attivato" and esito["verificato"] is True, esito
    assert esito["alias_identita"] == {"nome": "ZZITEX", "fonte": "GLEIF otherNames"}
    p = store.get_profile(TICKER)["profile"]
    assert p["identita"] == esito["alias_identita"] and {a["nome"] for a in p["alias_emittente"]} >= {"ZZITEX"}
    run = _run(store, tmp_path)  # il run verifica con l'alias che ha retto
    assert {c["metadati"]["periodo_fine"] for c in run["result"]["candidati"] if c["stato"] == "verificato"} == {
        "2025-12-31", "2024-12-31"}


def test_apostrofo_tipografico_e_accenti_reggono(store, rete, tmp_path):
    _sito_con(rete, "Z’ORÉAL")
    esito = fa._attiva_sito(store, TICKER, "Z'Oreal SA", {}, "base")
    assert esito["esito"] == "attivato" and esito["alias_identita"]["fonte"] == "nome del titolo", esito


def test_alias_non_fanno_passare_un_altra_societa_ne_una_controllata(store, rete, tmp_path, monkeypatch):
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [_gleif_record()])
    _sito_con(rete, "Qqother Textiles AG")
    esito = fa._attiva_sito(store, TICKER, "Zzindustria de Diseño Qqsyn, S.A.", {}, "base", lei=LEI_ALIAS)
    assert esito["esito"] == "senza_fonte" and "non trovato nel documento" in esito["motivo"], esito
    assert "ZZITEX" in esito["motivo"]  # dichiara gli alias provati
    _sito_con(rete, "ZZITEX Finance BV")
    (esef_sito._cache_dir() / f"{TICKER}.json").unlink(missing_ok=True)
    esito = fa._attiva_sito(store, TICKER, "Zzindustria de Diseño Qqsyn, S.A.", {}, "base", lei=LEI_ALIAS)
    assert esito["esito"] == "senza_fonte" and "altra entita'" in esito["motivo"], esito
    assert store.get_profile(TICKER) is None


def test_alias_dal_record_gleif_duplicato_e_dal_nome_tra_parentesi(monkeypatch):
    # prova dal vivo 07/10: il nome breve sta nel record DUPLICATE che dichiara come successore il LEI
    duplicato = _gleif_record(lei="ZZDUPL0000000000001", legale="ZZINDUSTRIA DE DISEÑO QQSYN, S.A. (ZZITEX, S.A.)", altri=())
    duplicato["attributes"]["entity"]["successorEntities"] = [{"lei": LEI_ALIAS}]
    duplicato["attributes"]["registration"] = {"status": "DUPLICATE"}
    estraneo = _gleif_record(lei="ZZESTR0000000000002", legale="Qqaltra (QQALTRA BRAND) SA", altri=())
    estraneo["attributes"]["entity"]["successorEntities"] = [{"lei": "QQSYN0000000000000099"}]
    estraneo["attributes"]["registration"] = {"status": "DUPLICATE"}
    # prova dal vivo 07/10: una controllata FUSA nell'emittente ha lo stesso successore ma e' un'altra societa'
    fusa = _gleif_record(lei="ZZFUSA0000000000003", legale="Qqprofumi Zzrubin SAS", altri=())
    fusa["attributes"]["entity"]["successorEntities"] = [{"lei": LEI_ALIAS}]
    fusa["attributes"]["registration"] = {"status": "RETIRED"}
    marchi = _gleif_record(altri=("ZZBRAND ; QQBRAND ; ZZITEX PROFESSIONAL",))
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [duplicato, estraneo, fusa, marchi])
    alias, _ = fa.alias_emittente("ZZITX.MC", "Zzindustria de Diseño Qqsyn, S.A.", lei=LEI_ALIAS)
    fonti = {fa.norm_alias(a["nome"]): a["fonte"] for a in alias}
    assert fonti.get("zzitex") == "GLEIF legalName (record duplicato con successore il LEI dell'emittente, tra parentesi)", fonti
    assert "qqaltra brand" not in fonti and "qqaltra" not in fonti
    assert "qqprofumi zzrubin sas" not in fonti and "qqprofumi zzrubin" not in fonti, fonti  # fusa: mai alias
    assert not any("zzbrand" in k for k in fonti), fonti  # elenco di marchi: scartato


def test_elenco_di_marchi_scartato_e_dichiarato(monkeypatch):
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [_gleif_record(altri=("ZZBRAND ; QQBRAND",))])
    alias, avvisi = fa.alias_emittente("ZZ.PA", "Zzalfa SA", lei=LEI_ALIAS)
    assert not any("ZZBRAND" in a["nome"] for a in alias) and any("elenco di marchi" in a for a in avvisi), avvisi


def test_aggiornamento_usa_gli_alias_salvati_nel_profilo(store, rete, tmp_path, monkeypatch):
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [_gleif_record()])
    _sito_con(rete, "ZZITEX")
    assert fa._attiva_sito(store, TICKER, "Zzindustria de Diseño Qqsyn, S.A.", {}, "base", lei=LEI_ALIAS)["esito"] == "attivato"

    def giu(q):
        raise ConnectionError("GLEIF giu")
    monkeypatch.setattr(esef_sito, "gleif_lei_records", giu)  # il giorno dopo GLEIF non risponde
    h26, h25 = FILE + "Zztest-Half-Year-Financial-Report-2026.pdf", FILE + "Zztest-Half-Year-Financial-Report-2025.pdf"
    rete[REPORTS] = _pagina((h26, ""), (h25, ""), (FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"))
    rete[h26], rete[h25] = semestrale(2026, nome="ZZITEX"), semestrale(2025, nome="ZZITEX")
    (esef_sito._cache_dir() / f"{TICKER}.json").unlink()
    agg = fa.aggiorna_dal_sito(store, TICKER)
    assert agg["esito"] == "aggiornato", agg
    assert store.get_profile(TICKER)["profile"]["identita"]["nome"] == "ZZITEX"


# ---------------------------------------------------------------- revisione R-FONTI (07/10): l'emittente e' il SOGGETTO

NOME_SUB = "Zzsub S.p.A."


def _annuale_capogruppo(anno):
    return _pdf("Zzparent Group AG", f"Annual Report {anno}", "Consolidated financial statements of the group",
                f"for the twelve months ended 31 December {anno}",
                "The group includes Zzsub S.p.A., listed in Milan, and twelve other companies.",
                f"Risks of the business in {anno} and the outlook of the group are described below.",
                "Demand for the products of the group is uncertain and the order book is stable.")


def test_rfonti_S1_relazione_della_capogruppo_sul_sito_rifiutata(store, rete, tmp_path):
    # anche sullo STESSO dominio: la copertina nomina come soggetto un'altra societa'
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zzparent-Group-Annual-Report-2025.pdf", "Group Annual Report 2025"),
                                  (FILE + "Zzparent-Group-Annual-Report-2024.pdf", "Group Annual Report 2024")),
                 FILE + "Zzparent-Group-Annual-Report-2025.pdf": _annuale_capogruppo(2025),
                 FILE + "Zzparent-Group-Annual-Report-2024.pdf": _annuale_capogruppo(2024)})
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte" and "in copertina il soggetto e' «Zzparent Group AG»" in esito["motivo"], esito
    assert store.get_profile("ZZSUB.MI") is None


def test_rfonti_S1_capogruppo_su_altro_dominio_non_entra_nemmeno(store, rete, tmp_path):
    par = "https://www.zzparent.example/files/"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((par + "Zzparent-Group-Annual-Report-2025.pdf", "Group Annual Report 2025")),
                 par + "Zzparent-Group-Annual-Report-2025.pdf": _annuale_capogruppo(2025)})
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte" and "piattaforma IR riconosciuta" in esito["motivo"], esito
    assert not any(c.startswith(par) for c in rete["chiamate"])


def test_rfonti_S2_sito_di_gruppo_etichettato_e_capogruppo_rifiutata(store, rete, tmp_path):
    par = "https://www.zzparent.example/"
    rete.update({SITO: _pagina((par, "Zzparent Group"), (SITO + "products", "Products")),
                 par + "investors": _pagina((par + "docs/Annual-Report-2025.pdf", "Annual Report 2025"),
                                            (par + "docs/Annual-Report-2024.pdf", "Annual Report 2024")),
                 par + "docs/Annual-Report-2025.pdf": _annuale_capogruppo(2025),
                 par + "docs/Annual-Report-2024.pdf": _annuale_capogruppo(2024)})
    trovato = esef_sito.scopri("ZZSUB.MI", sito_fn=lambda t: SITO)
    assert trovato["sito_ir"].startswith(par) and "sito di gruppo" in trovato["sito_ir_origine"]
    pt = esef_sito.pdf_trovati("ZZSUB.MI")
    assert pt["etichetta"].startswith("sito di gruppo zzparent.example linkato dal sito"), pt["etichetta"]
    assert "soggetto da verificare" in pt["etichetta"]
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base", trovato=trovato)
    assert esito["esito"] == "senza_fonte" and "Zzparent Group AG" in esito["motivo"], esito


def test_rfonti_S3_nome_legale_precedente_escluso_e_societa_scissa_rifiutata(store, rete, tmp_path, monkeypatch):
    lei = "ZZMOBIL00000000000042"
    rec = {"id": lei, "attributes": {"entity": {
        "legalName": {"name": "Zzmobil Group AG"},
        "otherNames": [{"name": "Zzold AG", "type": "PREVIOUS_LEGAL_NAME"},
                       {"name": "ZZMOBIL", "type": "TRADING_OR_OPERATING_NAME"}]}}}
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [rec])
    alias, avvisi = fa.alias_emittente("ZZMB.DE", "Zzmobil Group AG", lei=lei)
    assert not any(a["nome"] == "Zzold AG" for a in alias) and any("Zzold AG" in a and "precedente" in a for a in avvisi)
    assert any(a["nome"] == "ZZMOBIL" for a in alias)

    def scissa(anno):
        return _pdf("Zzmobil Trucks Holding AG", f"Annual Report {anno}", "Consolidated financial statements of the group",
                    f"for the twelve months ended 31 December {anno}",
                    "The group and the management of the company report on the results of the year.",
                    f"Risks of the business in {anno} and the outlook of the group are described below.",
                    "Demand for the products of the group is uncertain and the order book is stable.")
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Annual-Report-2025.pdf", "Annual Report 2025"),
                                  (FILE + "Annual-Report-2024.pdf", "Annual Report 2024")),
                 FILE + "Annual-Report-2025.pdf": scissa(2025), FILE + "Annual-Report-2024.pdf": scissa(2024)})
    esito = fa._attiva_sito(store, "ZZMB.DE", "Zzmobil Group AG", {}, "base", lei=lei)
    # «ZZMOBIL» e' un alias vero, ma in copertina il soggetto e' un'altra societa' che lo porta nel nome
    assert esito["esito"] == "senza_fonte" and "Zzmobil Trucks Holding AG" in esito["motivo"], esito


def test_rfonti_S6_nota_di_broker_mai_ammessa(store, rete, tmp_path):
    ric = "https://research.zzbroker.example/notes/"

    def nota(anno):
        return _pdf("Zzsub S.p.A. - Q2 results update", "Equity research note by Zzbroker Securities Ltd",
                    f"Quarterly results for the three months ended 30 June {anno}",
                    "Consolidated revenue grew; our estimates for the group are raised.",
                    "Target price and rating: see disclosures of Zzbroker Securities Ltd.",
                    f"Interim results of the second quarter {anno} were above consensus.")
    rete.update({SITO: _pagina((REPORTS, "Investors - Analyst coverage")),
                 REPORTS: _pagina((ric + "Zzsub-Q2-2026-results-update.pdf", "Zzbroker: Q2 2026 results update"),
                                  (ric + "Zzsub-Q2-2025-results-update.pdf", "Zzbroker: Q2 2025 results update")),
                 ric + "Zzsub-Q2-2026-results-update.pdf": nota(2026), ric + "Zzsub-Q2-2025-results-update.pdf": nota(2025)})
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte", esito
    # la stessa nota ospitata sul dominio dell'emittente: esclusa dal testo
    rete.update({REPORTS: _pagina((FILE + "Zzsub-Q2-2026-results-update.pdf", "Q2 2026 results update"),
                                  (FILE + "Zzsub-Q2-2025-results-update.pdf", "Q2 2025 results update")),
                 FILE + "Zzsub-Q2-2026-results-update.pdf": nota(2026), FILE + "Zzsub-Q2-2025-results-update.pdf": nota(2025)})
    (esef_sito._cache_dir() / "ZZSUB.MI.json").unlink(missing_ok=True)
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte" and "nota di ricerca" in esito["motivo"], esito


def test_rfonti_S4_tetto_di_tempo_per_titolo_comune_a_tutti_i_navigatori(rete, monkeypatch):
    ora = [0.0]
    base_get = lettore_trimestrali.requests.get

    def get_lento(url, **kw):
        ora[0] += 19.0
        return base_get(url, **kw)
    monkeypatch.setattr(lettore_trimestrali.requests, "get", get_lento)
    rete[SITO] = _pagina(*[(f"https://ir.zzcand{i}.example/", "Investors") for i in range(8)])
    for i in range(8):
        rete[f"https://ir.zzcand{i}.example/"] = Risposta("", b"", status=302)
        rete[f"https://ir.zzcand{i}.example/"].headers["Location"] = f"https://ir.zzhop{i}a.example/"
    nav = lambda d: esef_sito.Navigatore(d, orologio=lambda: ora[0], dormi=lambda s: None)  # noqa: E731
    esito = esef_sito.scopri("ZZSLOW.DE", sito_fn=lambda t: SITO, navigatore_fn=nav)
    assert ora[0] <= esef_sito.TEMPO_SITO_S + 2 * esef_sito.TIMEOUT_S, ora[0]
    assert any("tempo massimo" in m for m in esito["motivi"]), esito["motivi"]


def test_rfonti_S5_al_massimo_MAX_PIATTAFORME_chiamate_per_titolo(rete):
    feed = "/feed/FinancialReport.svc/GetFinancialReportList"
    q4 = '<script src="https://s1.q4cdn.com/x.js"></script>'
    pag = lambda *l: (_pagina(*l).decode().replace("</body>", q4 + "</body>")).encode()  # noqa: E731
    hosts = [f"https://r{i}.zztest.example/" for i in range(4)]
    irx = "https://www.zzir.example/"
    sub = [f"https://q{i}.zzir.example/" for i in range(3)]
    rete[SITO] = pag(*[(h + "investors", "Investors reports") for h in hosts], (irx, "Investor Relations"))
    for h in hosts:
        rete[h + "investors"] = pag()
    rete[irx] = pag(*[(s + "reports", "Financial reports") for s in sub])
    for s in sub:
        rete[s + "reports"] = pag()
    esito = esef_sito.scopri("ZZPLAT.DE", sito_fn=lambda t: SITO)
    assert len([c for c in rete["chiamate"] if feed in c]) <= esef_sito.MAX_PIATTAFORME
    assert len(esito["piattaforme"]) <= esef_sito.MAX_PIATTAFORME


# ---------------------------------------------------------------- R-FONTI: sopravvissuti del banco del revisore e rilievi


def test_rfonti_R1_alias_corto_scartato(monkeypatch):
    monkeypatch.setattr(esef_sito, "_info_yfinance", lambda t: {"shortName": "ZZQ"})
    alias, avvisi = fa.alias_emittente("ZZQ.MI", "Zzqualcosa SpA")
    assert [a["nome"] for a in alias] == ["Zzqualcosa SpA"] and any("ZZQ" in a and "troppo generico" in a for a in avvisi)


@pytest.mark.parametrize("stato", ["MERGED", "ANNULLED", "LAPSED", "RETIRED"])
def test_rfonti_R2_solo_il_duplicato_non_altri_stati_col_successore(monkeypatch, stato):
    rec = _gleif_record(lei="ZZALTRO000000000000005", legale="Qqfusa Industries SA", altri=())
    rec["attributes"]["entity"]["successorEntities"] = [{"lei": LEI_ALIAS}]
    rec["attributes"]["registration"] = {"status": stato}
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [rec, _gleif_record(altri=())])
    alias, _ = fa.alias_emittente("ZZ.MC", "Zzalfa SA", lei=LEI_ALIAS)
    assert not any("Qqfusa" in a["nome"] for a in alias), alias


def test_rfonti_R3_json_di_piattaforma_oltre_il_tetto(monkeypatch):
    url = "https://investor.zztest.example/feed/x"
    corpo = json.dumps({"GetFinancialReportListResult": [{"x": "y" * 500}]}).encode()
    nav = esef_sito.Navigatore("zztest.example", get=lambda u, **kw: Risposta(u, corpo, tipo="application/json")
                               if not u.endswith("robots.txt") else Risposta(u, b"", status=404), dormi=lambda s: None)
    monkeypatch.setattr(esef_sito, "MAX_JSON_BYTES", 100)
    with pytest.raises(ValueError, match="troppo grande"):
        nav.dati_json(url)


def test_rfonti_R5_lei_discorde_anche_oltre_le_prime_righe():
    testo = "Zztest Group SE Annual Report 2025 " + "testo della relazione " * 200 + " LEI: QQSYN000000000000077"
    assert "LEI nel documento" in (fa._codici_discordi(testo, lei="ZZTEST00000000000099") or "")
    testo = "Zztest Group SE " + "x " * 2000 + " ISIN: QQ0000000077"
    assert "ISIN nel documento" in (fa._codici_discordi(testo, isin="ZZ0000000099") or "")


@pytest.mark.parametrize("destinazione", ["http://cdn.zz.example/doc.pdf", "https://u:p@cdn.zz.example/doc.pdf"])
def test_rfonti_R6_redirect_mai_verso_http_o_con_credenziali(rete, destinazione):
    partenza = FILE + "Zztest-Annual-Report-2025.pdf"
    r = Risposta(partenza, b"", status=302)
    r.headers["Location"] = destinazione
    rete[partenza] = r
    esito = fa._risolvi_redirect(partenza)
    assert "non https o con credenziali" in (esito.get("motivo") or ""), esito
    assert not any(c.startswith(("http://cdn", "https://u:p@")) for c in rete["chiamate"])


def test_rfonti_R7_candidati_al_sito_ir_col_tetto():
    home = "https://www.zzbrand.example/"
    link = [{"url": f"https://ir{i}.zzaltro{i}.example/", "testo": "Investors", "pagina": home} for i in range(12)]
    cand = esef_sito._candidati_ir(home, {"pagine": [home], "link": link}, None, True)
    assert len(cand) == esef_sito.MAX_CANDIDATI_IR and cand[0][0] == "https://ir0.zzaltro0.example/"


def test_rfonti_R9_mziq_solo_con_fmbase_del_file_manager():
    from bellomberg.market_data import piattaforme_ir
    html = ("<script>const fmId = '11111111-2222-3333-4444-555555555555'; const fmBase = 'https://api.zz.example/altro';"
            "</script><script>categories.push({ internal_name: 'itr' });</script>")
    assert piattaforme_ir.rileva(html, "https://www.zz.example/ir/") == []


def test_rfonti_cache_del_sito_mai_persa_e_irwebsite_in_errore_dichiarato(tmp_path):
    cartella = tmp_path / "siti"
    cartella.mkdir()
    (cartella / "ZZCACHE.MI.json").write_text(json.dumps({"at": "2026-10-01", "sito": "https://www.zzcache.example/"}),
                                              encoding="utf-8")

    def giu(t):
        raise ConnectionError("yfinance giu")
    with pytest.raises(ConnectionError):
        esef_sito.sito_ir_societa("ZZCACHE.MI", info_fn=giu, cache_dir=tmp_path)
    voce = json.loads((cartella / "ZZCACHE.MI.json").read_text(encoding="utf-8"))
    assert voce["sito"] == "https://www.zzcache.example/"  # il sito noto resta
    esito = esef_sito.scopri("ZZCACHE.MI", cache_dir=tmp_path / "c", sito_fn=lambda t: "https://www.zzcache.example/",
                             sito_ir_fn=giu, navigatore_fn=lambda d: esef_sito.Navigatore(
                                 d, get=lambda u, **kw: Risposta(u, b"", status=404), dormi=lambda s: None))
    assert any(m.startswith("irWebsite non letto (ConnectionError") for m in esito["motivi"]), esito["motivi"]


def test_rfonti_alias_esef_mai_senza_lei(monkeypatch):
    proposta = {"esef": {"candidati": [{"lei": "QQALTRA0000000000001", "nome": "Qqaltra Holding SA"},
                                       {"nome": "Qqsenza Lei SA"}]}}
    alias, _ = fa.alias_emittente("ZZ.MI", "Zzalfa SA", proposta=proposta)
    assert [a["nome"] for a in alias] == ["Zzalfa SA"]


def test_rfonti_tipi_gleif_ignoti_esclusi_e_dichiarati(monkeypatch):
    rec = _gleif_record(altri=())
    rec["attributes"]["entity"]["otherNames"] = [{"name": "Zzstrano Nome", "type": "QUALCOSA_DI_NUOVO"}]
    monkeypatch.setattr(esef_sito, "gleif_lei_records", lambda q: [rec])
    alias, avvisi = fa.alias_emittente("ZZ.MC", "Zzalfa SA", lei=LEI_ALIAS)
    assert not any("Zzstrano" in a["nome"] for a in alias) and any("QUALCOSA_DI_NUOVO" in a for a in avvisi)


@pytest.mark.parametrize("copertina", [
    "Report on Limited Review ZZTEST GROUP SE AND SUBSIDIARIES Interim Condensed Consolidated Statements",
    "Unaudited Interim Condensed Financial Statements Zztest Group SE KPMG Auditores Independentes Ltda.",
    "KPMG Auditores Independentes Ltda. Independent review report Zztest Group SE",  # il revisore non conta
])
def test_rfonti_copertine_buone_dell_emittente(copertina):
    assert fa._soggetto_in_copertina(copertina, [{"nome": NOME, "fonte": "nome del titolo"}]) is None


@pytest.mark.parametrize("copertina,entita", [
    ("Zzparent Group AG Annual Report 2025 The group includes Zztest Group SE", "Zzparent Group AG"),
    ("Zztest Group Trucks Holding AG Annual Report 2025", "Zztest Group Trucks Holding AG"),
    ("Zzbroker Securities Ltd Morning note on Zztest Group SE", "Zzbroker Securities Ltd"),
])
def test_rfonti_copertine_di_altre_entita(copertina, entita):
    assert f"«{entita}»" in (fa._soggetto_in_copertina(copertina, [{"nome": NOME, "fonte": "nome del titolo"}]) or "")


def test_rfonti_soggetto_non_in_copertina():
    testo = "Annual Report 2025 " + "testo " * 1000 + " Zztest Group SE"
    assert (fa._soggetto_in_copertina(testo, [{"nome": NOME, "fonte": "nome del titolo"}]) or "").startswith(
        "soggetto non provato in copertina")


def _sito_ir_lungo(rete, radice, n=12):
    """Sito IR di un candidato con `n` sotto-pagine IR vuote (esplorazione lunga)."""
    figli = [f"{radice}financial-reports-{i}" for i in range(n)]
    rete[radice] = _pagina(*[(f, f"Financial reports {i}") for i, f in enumerate(figli)])
    for f in figli:
        rete[f] = _pagina()


def test_rfonti_S4_il_tempo_del_titolo_vale_anche_dentro_un_candidato_lungo(rete, monkeypatch):
    ora = [0.0]
    base_get = lettore_trimestrali.requests.get

    def get_lento(url, **kw):
        ora[0] += 19.0
        return base_get(url, **kw)
    monkeypatch.setattr(lettore_trimestrali.requests, "get", get_lento)
    rete[SITO] = _pagina(("https://ir.zzlungo.example/", "Investors"))
    _sito_ir_lungo(rete, "https://ir.zzlungo.example/")
    nav = lambda d: esef_sito.Navigatore(d, orologio=lambda: ora[0], dormi=lambda s: None)  # noqa: E731
    esef_sito.scopri("ZZLUNGO.DE", sito_fn=lambda t: SITO, navigatore_fn=nav)
    assert ora[0] <= esef_sito.TEMPO_SITO_S + 2 * esef_sito.TIMEOUT_S, ora[0]


def test_rfonti_S4_tetto_di_richieste_per_titolo(rete, monkeypatch):
    # candidati che non portano a niente (redirect verso altri domini, poi 404): senza tetto ~50 richieste
    monkeypatch.setattr(esef_sito, "MAX_RICHIESTE_TITOLO", 10)
    rete[SITO] = _pagina(*[(f"https://ir.zzmolti{i}.example/", "Investors") for i in range(8)])
    for i in range(8):
        rete[f"https://ir.zzmolti{i}.example/"] = Risposta("", b"", status=302)
        rete[f"https://ir.zzmolti{i}.example/"].headers["Location"] = f"https://ir.zzsalto{i}.example/"
    esito = esef_sito.scopri("ZZMOLTI.DE", sito_fn=lambda t: SITO)
    richieste = len([c for c in rete["chiamate"] if "zz" in c])
    assert richieste <= 10 + 4, richieste
    assert any("richieste per titolo" in m for m in esito["motivi"]), esito["motivi"]


def test_rfonti_cache_tiene_il_sito_se_yfinance_non_lo_da(tmp_path):
    cartella = tmp_path / "siti"
    cartella.mkdir()
    (cartella / "ZZC2.MI.json").write_text(json.dumps({"at": "2026-10-01", "sito": "https://www.zzc2.example/"}),
                                           encoding="utf-8")
    ir = esef_sito.sito_ir_societa("ZZC2.MI", info_fn=lambda t: {"irWebsite": "http://ir.zzc2.example/"}, cache_dir=tmp_path)
    voce = json.loads((cartella / "ZZC2.MI.json").read_text(encoding="utf-8"))
    assert ir == "https://ir.zzc2.example/" and voce["sito"] == "https://www.zzc2.example/", voce


def test_rfonti_anno_prima_di_un_altra_entita_scartato_col_motivo(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = _pdf(
        "Zzparent Group AG", "Annual Report 2024", "Consolidated financial statements of the group",
        "for the twelve months ended 31 December 2024", "The group includes Zztest Group SE, listed in Frankfurt.")
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato", esito
    p = store.get_profile(TICKER)["profile"]
    assert p["ir_urls"] == [FILE + "Zztest-Annual-Report-2025.pdf"], p["ir_urls"]
    assert any("anno prima scartato" in a and "Zzparent Group AG" in a for a in esito["avvisi"]), esito["avvisi"]


# ---------------------------------------------------------------- CDN generici linkati dal sito (main 07/10)

CDN_PDF = "https://d18zz7test.cloudfront.net/files/Zztest-Annual-Report-2025.pdf"


def test_cdn_generico_linkato_dal_sito_ammesso_con_l_etichetta():
    e = esef_sito.classifica_pdf({"url": CDN_PDF, "testo": "Annual Report 2025", "pagina": REPORTS},
                                 dominio="zztest.example")
    assert (e["ammesso"], e["via_host"], e["cdn_generico"]) == (True, "d18zz7test.cloudfront.net", True), e
    assert e["etichetta"] == ("PDF su CDN esterno (d18zz7test.cloudfront.net) linkato dal sito dell'emittente, "
                              "non archivio ufficiale (OAM)")
    for host in ("x.s3.amazonaws.com", "zz.azureedge.net", "zz.akamaized.net", "storage.googleapis.com",
                 "zz.blob.core.windows.net"):
        assert esef_sito.cdn_generico(host), host
    assert not esef_sito.cdn_generico("cdn.qqsyn.example") and not esef_sito.cdn_generico("cloudfront.net.zz.example")


def test_cdn_generico_linkato_da_una_pagina_terza_escluso():
    e = esef_sito.classifica_pdf({"url": CDN_PDF, "testo": "Annual Report 2025",
                                  "pagina": "https://www.zzterzo.example/reports"}, dominio="zztest.example")
    assert not e["ammesso"] and "non linkato da una pagina del sito IR" in e["motivo"], e
    e = esef_sito.classifica_pdf({"url": CDN_PDF, "testo": "Annual Report 2025"}, dominio="zztest.example")
    assert not e["ammesso"], e


def test_cdn_generico_nota_di_broker_esclusa():
    e = esef_sito.classifica_pdf({"url": "https://d18zz7test.cloudfront.net/x/Zztest-Equity-Research-2025.pdf",
                                  "testo": "", "pagina": REPORTS}, dominio="zztest.example")
    assert not e["ammesso"] and e["motivo"].startswith("nota di ricerca"), e


def test_cdn_generico_dal_sito_con_copertina_dell_emittente_attivato(store, rete, tmp_path):
    cdn24 = "https://d18zz7test.cloudfront.net/files/Zztest-Annual-Report-2024.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((CDN_PDF, "Annual Report 2025"), (cdn24, "Annual Report 2024")),
                 CDN_PDF: annuale(2025), cdn24: annuale(2024)})
    esito = _attiva(store, tmp_path)
    assert esito["origine"].startswith("PDF su CDN esterno (d18zz7test.cloudfront.net) linkato dal sito dell'emittente")
    assert "https://d18zz7test.cloudfront.net/robots.txt" in rete["chiamate"]  # robots del CDN rispettato
    # impianto 07/10: CDN generico = fonte esterna, entra etichettato ma MAI verificato (anche nel run)
    _mai_verificato(store, tmp_path, esito, TICKER, "d18zz7test.cloudfront.net")


def test_cdn_generico_dal_sito_con_copertina_di_un_altra_societa_escluso(store, rete, tmp_path):
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((CDN_PDF, "Annual Report 2025")),
                 CDN_PDF: _pdf("Zzparent Group AG", "Annual Report 2025", "Consolidated financial statements of the group",
                               "for the twelve months ended 31 December 2025",
                               "The group includes Zztest Group SE, listed in Frankfurt.")})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "in copertina il soggetto e' «Zzparent Group AG»" in esito["motivo"], esito
    assert store.get_profile(TICKER) is None


# ---------------------------------------------------------------- revisione R-SITI2 (07/10): sonde ribaltate in rifiuti

TK_SUB = "ZZSUB.MI"


def _capogruppo_senza_forma(anno):
    return _pdf("Zzparent Group", f"Annual Report {anno}", "Consolidated financial statements of the group",
                f"for the twelve months ended 31 December {anno}",
                "Group companies: Zzsub, Zzother and Zzthird, with plants in Europe and Asia.",
                f"Risks of the business in {anno} and the outlook of the group are described below.",
                "Demand for the products of the group is uncertain and the order book is stable.")


def test_rsiti2_D1_capogruppo_senza_forma_su_cdn_mai_verificata(store, rete, tmp_path):
    cdn = "https://d1zzparent.cloudfront.net/reports/"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((cdn + "Group-Annual-Report-2025.pdf", "Group Annual Report 2025"),
                                  (cdn + "Group-Annual-Report-2024.pdf", "Group Annual Report 2024")),
                 cdn + "Group-Annual-Report-2025.pdf": _capogruppo_senza_forma(2025),
                 cdn + "Group-Annual-Report-2024.pdf": _capogruppo_senza_forma(2024)})
    esito = fa._attiva_sito(store, TK_SUB, NOME_SUB, {}, "base")
    _mai_verificato(store, tmp_path, esito, TK_SUB, "d1zzparent.cloudfront.net")


def _nota_it(anno):
    return _pdf("Zzsub S.p.A.", "Ricerca azionaria - aggiornamento sui risultati del primo semestre",
                "Relazione finanziaria semestrale consolidata: i conti del gruppo sono in crescita",
                f"per il semestre chiuso al 30 giugno {anno}, con ricavi e margini sopra le nostre stime",
                "Prezzo obiettivo: 4,20 euro (da 3,90). Raccomandazione: ACQUISTARE. Giudizio confermato.",
                "La societa' e il gruppo hanno una posizione finanziaria netta positiva e in miglioramento.",
                "Il giudizio della ricerca tiene conto delle stime degli analisti e delle prospettive per il gruppo,",
                "che per il secondo semestre della gestione vede un aumento degli ordini e delle vendite.",
                "Analista: Mario Rossi, Zzsim SIM S.p.A., che e' specialista e operatore incaricato.")


def test_rsiti2_D2a_nota_di_broker_italiana_sul_dominio_rifiutata(store, rete):
    up = SITO + "wp-content/uploads/2026/07/"
    rete.update({SITO: _pagina((REPORTS, "Investor relations")),
                 REPORTS: _pagina((up + "Zzsub_H1_2026_risultati_Zzsim.pdf", "Zzsim SIM: risultati H1 2026"),
                                  (up + "Zzsub_H1_2025_risultati_Zzsim.pdf", "Zzsim SIM: risultati H1 2025")),
                 up + "Zzsub_H1_2026_risultati_Zzsim.pdf": _nota_it(2026),
                 up + "Zzsub_H1_2025_risultati_Zzsim.pdf": _nota_it(2025)})
    esito = fa._attiva_sito(store, TK_SUB, NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte" and "nota di ricerca" in esito["motivo"], esito


def test_rsiti2_D2b_nota_di_broker_italiana_su_cdn_rifiutata(store, rete):
    s3 = "https://zzsim-ricerca.s3.eu-south-1.amazonaws.com/2026/"
    rete.update({SITO: _pagina((REPORTS, "Investor relations")),
                 REPORTS: _pagina((s3 + "Zzsub_H1_2026_risultati.pdf", "Zzsim SIM: risultati H1 2026"),
                                  (s3 + "Zzsub_H1_2025_risultati.pdf", "Zzsim SIM: risultati H1 2025")),
                 s3 + "Zzsub_H1_2026_risultati.pdf": _nota_it(2026), s3 + "Zzsub_H1_2025_risultati.pdf": _nota_it(2025)})
    esito = fa._attiva_sito(store, TK_SUB, NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte", esito


@pytest.mark.parametrize("testo", [
    "Kursziel: 12 EUR. Kaufempfehlung bestaetigt.", "Objectif de cours : 10 EUR. Recommandation : achat.",
    "Precio objetivo: 8 EUR. Recomendacion: comprar.", "Rating: BUY. Target price 12.", "Prezzo obiettivo 4,20 euro"])
def test_rsiti2_lessico_note_di_ricerca_multilingue(testo):
    assert fa._nota_di_ricerca("Zzsub AG\n" + testo), testo


@pytest.mark.parametrize("testo", [
    "This document does not constitute an offer to sell, nor an investment recommendation.",
    "Il presente documento non costituisce raccomandazione di investimento ne' sollecitazione.",
    "Dieses Dokument stellt keine Anlageempfehlung dar.",
    "Q2-2026 Operating and Financial Review"])
def test_rsiti2_disclaimer_negati_e_operating_non_sono_note(testo):
    assert fa._nota_di_ricerca("Zzsub AG Results for the first half 2026\n" + testo) is None, testo


def test_rsiti2_D3_redirect_verso_host_terzo_rifiutato(store, rete):
    terzo = "https://docs.zzaltra.example/files/"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zzsub-Annual-Report-2025.pdf", "Annual Report 2025"))})
    r = Risposta(FILE + "Zzsub-Annual-Report-2025.pdf", b"", status=302)
    r.headers["Location"] = terzo + "Group-Annual-Report-2025.pdf"
    rete[FILE + "Zzsub-Annual-Report-2025.pdf"] = r
    rete[terzo + "Group-Annual-Report-2025.pdf"] = _capogruppo_senza_forma(2025)
    esito = fa._attiva_sito(store, TK_SUB, NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte" and "redirect verso un host terzo (docs.zzaltra.example)" in esito["motivo"], esito
    assert terzo + "Group-Annual-Report-2025.pdf" not in rete["chiamate"]


def test_rsiti2_D3b_redirect_dal_cdn_verso_host_terzo_rifiutato(store, rete):
    cdn, terzo = "https://d9zz.cloudfront.net/r/", "https://docs.zzaltra.example/files/"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((cdn + "Annual-Report-2025.pdf", "Annual Report 2025"))})
    r = Risposta(cdn + "Annual-Report-2025.pdf", b"", status=302)
    r.headers["Location"] = terzo + "Group-Annual-Report-2025.pdf"
    rete[cdn + "Annual-Report-2025.pdf"] = r
    rete[terzo + "Group-Annual-Report-2025.pdf"] = _capogruppo_senza_forma(2025)
    esito = fa._attiva_sito(store, TK_SUB, NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte" and terzo + "Group-Annual-Report-2025.pdf" not in rete["chiamate"], esito


def test_rsiti2_redirect_al_cdn_dichiara_l_host_finale(store, rete, tmp_path):
    cdn = "https://zz-p-001.blob.core.windows.net/api/ar25"
    _sito_annuali(rete)
    r = Risposta(FILE + "Zztest-Annual-Report-2025.pdf", b"", status=302)
    r.headers["Location"] = cdn
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = r
    rete[cdn] = annuale(2025)
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato", esito
    assert "zz-p-001.blob.core.windows.net" in esito["origine"], esito["origine"]


def test_rsiti2_D4_banca_omonima_rifiutata():
    alias = [{"nome": "Assicurazioni Zzgen S.p.A."}, {"nome": "ZZGEN"}]
    motivo = fa._soggetto_in_copertina("Banca Zzgen S.p.A.\nHalf-Year Financial Report 2026\n", alias)
    assert motivo and "Banca Zzgen S.p.A." in motivo, motivo


@pytest.mark.parametrize("testa", [
    "Audirevi S.p.A.\nRelazione di revisione contabile limitata\nAgli Azionisti della Zzsub S.p.A.\n",
    "PKF Italia S.p.A.\nReview report on the interim financial statements\nTo the shareholders of Zzsub S.p.A.\n",
    "The Bank of New York Mellon Corporation, as Depositary\nAmerican Depositary Shares of\nZzsub S.p.A.\n",
    "Euronext Milan - Borsa Italiana S.p.A.\nRelazione finanziaria semestrale\nZzsub S.p.A.\n",
    "Citibank N.A., as Depositary\nZzsub S.p.A.\n", "Deutsche Boerse AG Prime Standard\nZzsub S.p.A.\n"])
    # (impianto 07/10: una depositaria FUORI lista conta come altra entita', v. test_impianto_E9u)
def test_rsiti2_D5_revisori_depositari_e_borse_non_sono_il_soggetto(testa):
    assert fa._soggetto_in_copertina(testa, [{"nome": NOME_SUB}]) is None, testa


@pytest.mark.parametrize("nome,testo", [
    ("Q2-2026-Operating-and-Financial-Review.pdf", "Interim report Q2 2026 - Operating and financial review"),
    ("Informativa-finanziaria-trimestrale-30-settembre-2026.pdf", "Informativa finanziaria trimestrale al 30 settembre 2026")])
def test_rsiti2_D5c_relazioni_vere_non_scartate_dal_nome(nome, testo):
    e = esef_sito.classifica_pdf({"url": FILE + nome, "testo": testo}, dominio="zztest.example")
    assert e["ammesso"], e["motivo"]


def test_rsiti2_D6_tetto_richieste_vale_dentro_l_esplorazione(rete):
    link = [(SITO + f"investors/report-{i}", f"Investor relations report {i}") for i in range(150)]
    rete[SITO] = _pagina(*link)
    esito = esef_sito.scopri("ZZMANY.MI", sito_fn=lambda t: SITO)
    assert len(rete["chiamate"]) <= esef_sito.MAX_RICHIESTE_TITOLO, len(rete["chiamate"])
    assert any("richieste per titolo" in m for m in esito["motivi"]), esito["motivi"]


@pytest.mark.parametrize("host,atteso", [
    ("www.zzsub.com.pl", "zzsub.com.pl"), ("dm.zzbroker.com.pl", "zzbroker.com.pl"), ("ir.zz.co.th", "zz.co.th"),
    ("www.zz.or.jp", "zz.or.jp"), ("zz.com.es", "zz.com.es"), ("www.zztest.example", "zztest.example"),
    ("zzsub.wixsite.com", "zzsub.wixsite.com"), ("zzsub.github.io", "zzsub.github.io"), ("www.zz.co.uk", "zz.co.uk")])
def test_rsiti2_D7_dominio_registrabile(host, atteso):
    assert esef_sito.dominio_registrabile(host) == atteso


def test_rsiti2_D7_broker_polacco_non_e_lo_stesso_dominio():
    dom = esef_sito.dominio_sito("https://www.zzsub.com.pl/")
    url = "https://dm.zzbroker.com.pl/raporty/Zzsub-Half-Year-Report-2026.pdf"
    e = esef_sito.classifica_pdf({"url": url, "testo": "Half-Year Report 2026", "pagina": "https://www.zzsub.com.pl/ir"},
                                 dominio=dom)
    assert dom == "zzsub.com.pl" and not e["ammesso"], e
    assert not esef_sito.stesso_dominio("https://altro.wixsite.com/x.pdf", esef_sito.dominio_sito("https://zzsub.wixsite.com/"))


def test_rsiti2_D9_robots_riletto_sul_redirect_interno(rete):
    rete.update({SITO + "robots.txt": Risposta("", b"User-agent: *\nDisallow: /privato/\n", tipo="text/plain"),
                 SITO: _pagina((SITO + "investors", "Investor relations"))})
    r = Risposta(SITO + "investors", b"", status=302)
    r.headers["Location"] = SITO + "privato/reports"
    rete[SITO + "investors"] = r
    rete[SITO + "privato/reports"] = _pagina((FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"))
    esef_sito.scopri("ZZROB2.MI", sito_fn=lambda t: SITO)
    assert SITO + "privato/reports" not in rete["chiamate"]


def test_rsiti2_aggiornamento_passa_lei_e_isin(store, rete, tmp_path, monkeypatch):
    _sito_annuali(rete)
    lei = "ZZTEST00000000000099"
    assert fa._attiva_sito(store, TICKER, NOME, {}, "base", lei=lei)["esito"] == "attivato"
    visti = {}
    vero = fa._prova_scelte

    def spia(*a, **kw):
        visti.update(kw)
        return vero(*a, **kw)
    monkeypatch.setattr(fa, "_prova_scelte", spia)
    fa.aggiorna_dal_sito(store, TICKER)
    assert visti.get("lei") == lei, visti


# ---------------------------------------------------------------- R-SITI2: mutanti sopravvissuti (Q6...Q27)

def test_rsiti2_Q6_rating_buy_e_nota():
    assert fa._nota_di_ricerca("Zzsub AG\nRating: BUY")


def test_rsiti2_Q7_cdn_senza_punto_non_vale():
    assert not esef_sito.cdn_generico("evilcloudfront.net") and esef_sito.cdn_generico("d1.cloudfront.net")


def test_rsiti2_Q8_Q9_download_dal_cdn_solo_https_e_solo_l_host_via(rete, tmp_path):
    r = fa._scarica_pdf_sito("http://d1zz.cloudfront.net/a.pdf", tmp_path, dominio="zztest.example", via_host="d1zz.cloudfront.net")
    assert r["stato"] == "errore" and rete["chiamate"] == [], r
    r = fa._scarica_pdf_sito("https://d2zz.cloudfront.net/a.pdf", tmp_path, dominio="zztest.example", via_host="d1zz.cloudfront.net")
    assert r["stato"] == "errore" and rete["chiamate"] == [], r


def test_rsiti2_Q10_tetto_raggiunto_esattamente(monkeypatch):
    monkeypatch.setattr(esef_sito, "MAX_RICHIESTE_TITOLO", 3)
    nav = esef_sito.Navigatore("zztest.example", get=lambda u, **kw: Risposta(u, b"", status=404), dormi=lambda s: None)
    b = esef_sito.BudgetTitolo(nav)
    nav.richieste = 3
    assert b.esaurito() and "richieste per titolo" in b.esaurito()
    nav.richieste = 2
    assert b.esaurito() is None


def test_rsiti2_Q12_Q13_piattaforme_e_prime_pagine_dentro_il_budget(rete, monkeypatch):
    # la home consuma il budget (robots + pagina): piattaforme e prime pagine si fermano FRA i passi, dichiarato
    monkeypatch.setattr(esef_sito, "MAX_RICHIESTE_TITOLO", 2)
    q4 = '<script src="https://s1.q4cdn.com/x.js"></script>'
    rete[SITO] = (_pagina((FILE + "Zztest-Results-H1-2026.pdf", "")).decode().replace("</body>", q4 + "</body>")).encode()
    esito = esef_sito.scopri("ZZBUD.DE", sito_fn=lambda t: SITO)
    assert not any("FinancialReport.svc" in c for c in rete["chiamate"]), rete["chiamate"]
    assert FILE + "Zztest-Results-H1-2026.pdf" not in rete["chiamate"]
    assert any(m.startswith("tetto di 2 richieste per titolo raggiunto: piattaforme IR non lette") for m in esito["motivi"])
    assert esito["prime_pagine"][FILE + "Zztest-Results-H1-2026.pdf"]["errore"].startswith(
        "tetto di 2 richieste per titolo raggiunto: prima pagina non letta"), esito["prime_pagine"]


def test_rsiti2_Q14_nota_di_ricerca_dal_percorso_e_dall_host():
    e = esef_sito.classifica_pdf({"url": "https://ir.zztest.example/research/Zzsub-H1-2026.pdf", "testo": "H1 2026"},
                                 dominio="zztest.example")
    assert not e["ammesso"] and e["motivo"].startswith("nota di ricerca"), e
    e = esef_sito.classifica_pdf({"url": "https://zz-broker-notes.s3.amazonaws.com/x/Zzsub-Half-Year-Report-2026.pdf",
                                  "testo": "", "pagina": REPORTS}, dominio="zztest.example")
    assert not e["ammesso"] and e["motivo"].startswith("nota di ricerca"), e


def test_rsiti2_Q15_cdn_con_credenziali_escluso():
    e = esef_sito.classifica_pdf({"url": "https://u:p@d1.cloudfront.net/Annual-Report-2025.pdf", "testo": "Annual Report 2025",
                                  "pagina": REPORTS}, dominio="zztest.example")
    assert not e["ammesso"], e


def test_rsiti2_Q17_prima_pagina_letta_anche_sul_cdn():
    pdf = [{"url": "https://d1.cloudfront.net/x/Results-H1-2026.pdf", "testo": "", "pagina": REPORTS}]
    assert esef_sito.da_leggere_prima_pagina(pdf, "zztest.example") == ["https://d1.cloudfront.net/x/Results-H1-2026.pdf"]


def test_rsiti2_Q18_piattaforma_mai_per_sottostringa():
    assert not esef_sito.piattaforma_documenti("x.notq4cdn.example") and esef_sito.piattaforma_documenti("s1.q4cdn.com")
    assert not esef_sito.piattaforma_documenti("q4cdn.zzattacker.example.com")


def test_rsiti2_Q24_primo_alias_e_il_piu_vicino_all_inizio():
    alias = [{"nome": "Zzalfa Holding SE"}, {"nome": "ZZALFA"}]
    testo = "ZZALFA\nAnnual Report 2025\nQqother Services AG provides the IT platform\nZzalfa Holding SE\n"
    assert fa._soggetto_in_copertina(testo, alias) is None


def test_rsiti2_Q27_nota_di_ricerca_come_anno_prima_scartata(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = _pdf(
        NOME, "Annual Report 2024", "Consolidated financial statements of the group",
        "for the twelve months ended 31 December 2024", "Target price: 12 EUR. Rating: BUY.")
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato", esito
    assert store.get_profile(TICKER)["profile"]["ir_urls"] == [FILE + "Zztest-Annual-Report-2025.pdf"]
    assert any("nota di ricerca" in a for a in esito["avvisi"]), esito["avvisi"]


def test_rsiti2_N21_tetto_dichiarato_una_volta_e_esplorazione_ferma(rete):
    link = [(SITO + f"investors/report-{i}", f"Investor relations report {i}") for i in range(150)]
    rete[SITO] = _pagina(*link)
    esito = esef_sito.scopri("ZZMANY2.MI", sito_fn=lambda t: SITO)
    grezzi = [m for m in esito["motivi"] if m == f"tetto di {esef_sito.MAX_RICHIESTE_TITOLO} richieste per titolo raggiunto"]
    assert len(grezzi) == 1, esito["motivi"]  # l'esplorazione si ferma al primo superamento


def test_rsiti2_N22_anno_prima_su_cdn_entra_mai_verificato(store, rete, tmp_path):
    cdn24 = "https://d7zz.cloudfront.net/f/Zztest-Annual-Report-2024.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"), (cdn24, "Annual Report 2024")),
                 FILE + "Zztest-Annual-Report-2025.pdf": annuale(2025),
                 cdn24: _pdf("Annual Report 2024", "Consolidated financial statements of the group",
                             "for the twelve months ended 31 December 2024",
                             "Results of the year of Zztest Group were stable and the order book is larger.")})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato", esito
    p = store.get_profile(TICKER)["profile"]
    assert p["ir_urls"] == [FILE + "Zztest-Annual-Report-2025.pdf", cdn24] and p["documenti_esterni"] == {
        cdn24: "d7zz.cloudfront.net"}
    # anche se il documento recente del dominio regge, il profilo resta NON verificato (fonte esterna dichiarata)
    _mai_verificato(store, tmp_path, esito, TICKER, "d7zz.cloudfront.net")


# ---------------------------------------------------------------- impianto nuovo (main 07/10): fonte esterna mai verificata

ISIN_SUB = "IT0000ZZSUB1"
CDN_PAR = "https://d1zzparent.cloudfront.net/reports/"


def _coda_cap(anno):
    return (f"for the twelve months ended 31 December {anno}", "Consolidated financial statements of the group",
            f"Risks of the business in {anno} and the outlook of the group are described below.",
            "Demand for the products of the group is uncertain and the order book is stable.")


def _sul_cdn(rete, crea):
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((CDN_PAR + "Group-Annual-Report-2025.pdf", "Group Annual Report 2025"),
                                  (CDN_PAR + "Group-Annual-Report-2024.pdf", "Group Annual Report 2024")),
                 CDN_PAR + "Group-Annual-Report-2025.pdf": crea(2025), CDN_PAR + "Group-Annual-Report-2024.pdf": crea(2024)})


def _mai_verificato(store, tmp_path, esito, ticker, host):
    """Esito «attivato» ma NON verificato con l'etichetta della fonte esterna, e nel run nessun documento
    esterno diventa verificato."""
    assert esito["esito"] == "attivato" and esito["verificato"] is False, esito
    assert any(c.startswith(f"fonte esterna ({host}), identita' non provabile") for c in esito["controlli_non_superati"]), esito
    p = store.get_profile(ticker)["profile"]
    assert p["documenti_esterni"] and all(h == host for h in p["documenti_esterni"].values()), p.get("documenti_esterni")
    svc = FilingService(store, tmp_path / "filing_archive_est", indexer=lambda *a: {"status": "skipped"}, impronta_fn=None)
    run = svc.run(ticker)
    esterni = set(p["documenti_esterni"])
    assert not any(c["stato"] == "verificato" and c["url"] in esterni for c in run["result"]["candidati"]), \
        run["result"]["candidati"]
    assert all(any("fonte esterna" in m for m in c.get("motivi") or []) for c in run["result"]["candidati"]
               if c["url"] in esterni and c["stato"] == "non_verificato"), run["result"]["candidati"]


def test_impianto_E1_capogruppo_su_cdn_che_nomina_la_controllata_mai_verificata(store, rete, tmp_path):
    _sul_cdn(rete, lambda a: _pdf("Zzparent Group", f"Annual Report {a}",
                                  "The group includes Zzsub S.p.A., listed in Milan, and twelve other companies.", *_coda_cap(a)))
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base")
    _mai_verificato(store, tmp_path, esito, "ZZSUB.MI", "d1zzparent.cloudfront.net")


def test_impianto_E2_isin_della_controllata_non_rende_verificato(store, rete, tmp_path):
    _sul_cdn(rete, lambda a: _pdf("Zzparent Group", f"Annual Report {a}",
                                  f"Listed subsidiaries: Zzsub (ISIN {ISIN_SUB}) and Zzother.", *_coda_cap(a)))
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {"isin": ISIN_SUB}, "base", isin=ISIN_SUB)
    _mai_verificato(store, tmp_path, esito, "ZZSUB.MI", "d1zzparent.cloudfront.net")


def test_impianto_E3_sito_di_gruppo_mai_verificato(store, rete, tmp_path):
    par = "https://www.zzparent.example/"
    rete.update({SITO: _pagina((par, "Zzparent Group"), (SITO + "products", "Products")),
                 par + "investors": _pagina((par + "docs/Annual-Report-2025.pdf", "Annual Report 2025"),
                                            (par + "docs/Annual-Report-2024.pdf", "Annual Report 2024"))})
    for a in (2025, 2024):
        rete[par + f"docs/Annual-Report-{a}.pdf"] = _pdf("Zzparent Group", f"Annual Report {a}",
                                                         "Group companies: Zzsub, Zzother and Zzthird.", *_coda_cap(a))
    trovato = esef_sito.scopri("ZZSUB.MI", sito_fn=lambda t: SITO)
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base", trovato=trovato)
    _mai_verificato(store, tmp_path, esito, "ZZSUB.MI", "www.zzparent.example")


def test_impianto_E4_alias_in_testa_su_cdn_mai_verificato(store, rete, tmp_path):
    _sul_cdn(rete, lambda a: _pdf(f"Annual Report {a}", "Zzsub and Zzother: the businesses of Zzparent Group", *_coda_cap(a)))
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base")
    _mai_verificato(store, tmp_path, esito, "ZZSUB.MI", "d1zzparent.cloudfront.net")


@pytest.mark.parametrize("testa", ["Interim Report January-June 2026", "Q2 2026 Quarterly Statement",
                                   "Zwischenbericht 1. Halbjahr 2026", "Resultats semestriels 2026"])
def test_impianto_F3_documento_vero_su_cdn_entra_etichettato_non_rifiutato(store, rete, tmp_path, testa):
    cdn = "https://d18zz7test.cloudfront.net/files/Zztest-Annual-Report-2025.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")), REPORTS: _pagina((cdn, "Annual Report 2025")),
                 cdn: _pdf(testa, "Zztest Group", *_coda_cap(2025))})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "attivato" and esito["verificato"] is False, esito
    assert any(c.startswith("fonte esterna (d18zz7test.cloudfront.net)") for c in esito["controlli_non_superati"])


def test_impianto_piattaforma_ir_resta_verificabile(store, rete, tmp_path):
    q4 = "https://s201.q4cdn.example/docs/Annual-Report-2025.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((q4, "Annual Report 2025"), (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024")),
                 q4: annuale(2025), FILE + "Zztest-Annual-Report-2024.pdf": annuale(2024)})
    esito = _attiva(store, tmp_path)
    assert esito["verificato"] is True and not store.get_profile(TICKER)["profile"].get("documenti_esterni"), esito


@pytest.mark.parametrize("testa", ["Zzparent Corp. (NYSE: ZZP)", "Zzparent Group AG, listed on Euronext",
                                   "Zzparent S.p.A. - bilancio soggetto a revisione"])
def test_impianto_E9_capogruppo_con_forma_seguita_da_contesto_rifiutata(store, rete, testa):
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Group-Annual-Report-2025.pdf", "Group Annual Report 2025"),
                                  (FILE + "Group-Annual-Report-2024.pdf", "Group Annual Report 2024"))})
    for a in (2025, 2024):
        rete[FILE + f"Group-Annual-Report-{a}.pdf"] = _pdf(testa, f"Annual Report {a}",
                                                           "The group includes Zzsub S.p.A., listed in Milan.", *_coda_cap(a))
    esito = fa._attiva_sito(store, "ZZSUB.MI", NOME_SUB, {}, "base")
    assert esito["esito"] == "senza_fonte" and "in copertina il soggetto e'" in esito["motivo"], esito


def test_impianto_E9u_contesto_dopo_il_nome_non_conta():
    alias = [{"nome": NOME_SUB}]
    assert fa._soggetto_in_copertina("Zzparent Corp. (NYSE: ZZP)\nAnnual Report\nZzsub S.p.A.\n", alias)
    assert fa._soggetto_in_copertina("Zzbank Trust Company Ltd., as Depositary\nZzsub S.p.A.\n", alias)  # fuori lista
    assert fa._soggetto_in_copertina("Analisi S.p.A.\nRelazione di revisione\nZzsub S.p.A.\n", alias)  # fuori lista


@pytest.mark.parametrize("testo", [
    "Zztest AG\nWir bestaetigen nicht nur unsere Kaufempfehlung, sondern erhoehen auch das Kursziel auf 45 EUR.\n",
    "Zztest SA\nNous ne modifions pas notre recommandation : Achat, avec un objectif de cours de 32 EUR.\n",
    "Zzsub S.p.A.\nConfermiamo non solo il giudizio: ACQUISTARE ma anche il prezzo obiettivo a 4,20 euro.\n",
    "Zztest Ltd\nNo change to our rating: BUY and price target of 12 USD after strong results.\n"])
def test_impianto_E7_negazione_finta_resta_nota(testo):
    assert fa._nota_di_ricerca(testo), testo


@pytest.mark.parametrize("testo", [
    "This document does not constitute an offer to sell, nor an investment recommendation.",
    "This presentation is not intended as an investment recommendation.",
    "Il presente documento non costituisce raccomandazione di investimento.",
    "Dieses Dokument stellt keine Anlageempfehlung dar.",
    "Ce document ne constitue pas une recommandation d'investissement.",
    "Este documento no constituye una recomendacion de inversion."])
def test_impianto_disclaimer_della_lista_chiusa(testo):
    assert fa._nota_di_ricerca("Zzsub AG Results for the first half 2026\n" + testo) is None, testo


# ---------------------------------------------------------------- cablaggio: riscontro numerico (aggiunta PM 07/10)

def _finto_riscontro(esito, chiamate, **extra):
    def riscontra(testo, **kw):
        chiamate.append({"testo": testo, **kw})
        return {"esito": esito, "voci": ["ricavi", "utile netto"], "fonte": "SEC XBRL companyfacts",
                "motivo": extra.get("motivo"), "etichetta": extra.get("etichetta")}
    return riscontra


def _cdn_annuali(rete, nome_pdf=NOME):
    cdn25 = "https://d5zz.cloudfront.net/r/Zztest-Annual-Report-2025.pdf"
    cdn24 = "https://d5zz.cloudfront.net/r/Zztest-Annual-Report-2024.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((cdn25, "Annual Report 2025"), (cdn24, "Annual Report 2024")),
                 cdn25: annuale(2025, nome=nome_pdf), cdn24: annuale(2024, nome=nome_pdf)})
    return cdn25, cdn24


def test_cablaggio_riscontrato_diventa_verificato_per_riscontro_con_sha256(store, rete, tmp_path):
    import hashlib
    cdn25, cdn24 = _cdn_annuali(rete)
    chiamate = []
    finto = _finto_riscontro("riscontrato", chiamate,
                             etichetta="verificato per riscontro numerico con SEC XBRL companyfacts (ricavi, utile netto)")
    esito = fa._attiva_sito(store, TICKER, NOME, {}, "base", lei="ZZTEST00000000000099", riscontro_fn=finto)
    assert esito["esito"] == "attivato" and esito["verificato"] is True, esito
    p = store.get_profile(TICKER)["profile"]
    assert set(p["documenti_riscontrati"]) == {cdn25, cdn24} and set(p["documenti_esterni"]) == {cdn25, cdn24}
    assert p["documenti_riscontrati"][cdn25]["sha256"] == hashlib.sha256(rete[cdn25]).hexdigest()  # i byte serviti
    assert p["documenti_riscontrati"][cdn25]["etichetta"].startswith("verificato per riscontro numerico con SEC")
    assert any("verificato per riscontro numerico" in a for a in esito["avvisi"])
    # i numeri di confronto non vengono dal documento: al modulo arrivano il testo e le chiavi dell'emittente
    assert {c["periodo_fine"] for c in chiamate} == {"2025-12-31", "2024-12-31"}
    assert all(c["ticker"] == TICKER and c["lei"] == "ZZTEST00000000000099" and c["testo"] for c in chiamate)


def test_cablaggio_numeri_diversi_restano_non_verificati(store, rete, tmp_path):
    _cdn_annuali(rete, nome_pdf="Zztest Group SE")
    finto = _finto_riscontro("diverso", [], motivo="ricavi 999 contro 123 della fonte")
    esito = fa._attiva_sito(store, TICKER, NOME, {}, "base", riscontro_fn=finto)
    assert esito["verificato"] is False and not store.get_profile(TICKER)["profile"].get("documenti_riscontrati")
    assert any("numeri diversi dalla fonte indipendente (ricavi 999 contro 123 della fonte)" in c
               for c in esito["controlli_non_superati"]), esito


def test_cablaggio_senza_confronto_dichiarato(store, rete, tmp_path):
    _cdn_annuali(rete)
    finto = _finto_riscontro("senza_confronto", [], motivo="nessuna fonte indipendente per l'emittente")
    esito = fa._attiva_sito(store, TICKER, NOME, {}, "base", riscontro_fn=finto)
    assert esito["verificato"] is False
    assert any("identita' non provabile" in c and "nessuna fonte indipendente" in c for c in esito["controlli_non_superati"])


def test_cablaggio_riscontro_mai_chiamato_sul_dominio_dell_emittente(store, rete, tmp_path):
    _sito_annuali(rete)
    chiamate = []
    esito = fa._attiva_sito(store, TICKER, NOME, {}, "base", riscontro_fn=_finto_riscontro("riscontrato", chiamate))
    assert esito["verificato"] is True and chiamate == []


def test_cablaggio_riscontro_in_errore_o_modulo_assente_dichiarato(store, rete, tmp_path, monkeypatch):
    import sys
    _cdn_annuali(rete)

    def rotto(testo, **kw):
        raise RuntimeError("guasto finto")
    esito = fa._attiva_sito(store, TICKER, NOME, {}, "base", riscontro_fn=rotto)
    assert esito["verificato"] is False and any("riscontro non eseguito (RuntimeError" in c
                                                 for c in esito["controlli_non_superati"]), esito
    monkeypatch.setitem(sys.modules, "bellomberg.market_data.riscontro_numerico", None)  # import impossibile
    (esef_sito._cache_dir() / f"{TICKER}.json").unlink(missing_ok=True)
    esito = fa._attiva_sito(store, "ZZTEST2.DE", NOME, {}, "base")
    assert any("modulo del riscontro numerico non disponibile" in c for c in esito["controlli_non_superati"]), esito


def test_cablaggio_note_ricerca_dal_modulo(monkeypatch):
    from bellomberg.market_data import note_ricerca
    visti = []
    monkeypatch.setattr(note_ricerca, "motivo_nota_ricerca", lambda t: visti.append(t) or "nota finta")
    assert fa._nota_di_ricerca("Zz" * 5000) == "nota finta" and len(visti[0]) == fa.COPERTINA_BATTUTE
