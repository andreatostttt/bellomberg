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


def test_solo_presentazioni_sul_sito_nessun_profilo(store, rete, tmp_path):
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((FILE + "Zztest-Investor-Presentation-2026.pdf", "Investor presentation"),
                                  (FILE + "2026-03-11-Zztest-Conference-Call-FY2025.pdf", "Call"))})
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "nessuna relazione periodica ammessa tra 2 PDF" in esito["motivo"]
    assert "presentazione" in esito["motivo"] and store.get_profile(TICKER) is None


def test_pdf_di_un_altra_societa_non_crea_il_profilo(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = annuale(2025, nome="Qqsyn Holding AG")
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "non trovato nel documento" in esito["motivo"], esito
    assert store.get_profile(TICKER) is None  # mai un profilo non verificato


def test_periodo_del_testo_discorde_dal_nome_nessun_profilo(store, rete, tmp_path):
    # prova reale 06/10: la semestrale nuova verificata sul comparativo dell'anno prima
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = annuale(2024)
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "periodo ambiguo" in esito["motivo"], esito
    assert "il testo 2024-12-31" in esito["motivo"]
    assert store.get_profile(TICKER) is None


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


def test_pdf_che_non_supera_la_verifica_completa_nessun_profilo(store, rete, tmp_path):
    # emittente, tipo, perimetro e regola del periodo reggono, ma il periodo del testo e' nel futuro:
    # la verifica completa del documento (filing_verifica) dice no e nessun profilo si salva
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _pdf(
        NOME, "Annual Report 2025", "Consolidated financial statements of the group",
        "for the twelve months ended 31 December 2099",
        "The group and the management of the company report on the results of the year.")
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = rete[FILE + "Zztest-Annual-Report-2025.pdf"]
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "PDF non verificato" in esito["motivo"], esito
    assert store.get_profile(TICKER) is None


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


def test_r8_perimetro_non_si_regge_sul_nome(store, rete, tmp_path):
    _sito_annuali(rete)
    rete[FILE + "Zztest-Annual-Report-2025.pdf"] = _pdf(
        NOME, "Annual Report 2025", "Separate financial statements of the parent company only",
        "for the twelve months ended 31 December 2025",
        "The company and the management of the company report on the results of the year.",
        "Risks of the business in 2025 and the outlook of the company are described below.")
    rete[FILE + "Zztest-Annual-Report-2024.pdf"] = rete[FILE + "Zztest-Annual-Report-2025.pdf"]
    esito = _attiva(store, tmp_path)
    assert esito["esito"] == "senza_fonte" and "perimetro consolidato non dichiarato" in esito["motivo"], esito
    assert "il nome dell'emittente non conta" in esito["motivo"]


def test_r8_pdf_di_altro_dominio_mai_scaricato(store, rete, tmp_path):
    altro = "https://cdn.qqsyn.example/docs/Annual-Report-2025.pdf"
    rete.update({SITO: _pagina((REPORTS, "Financial reports")),
                 REPORTS: _pagina((altro, "Annual Report 2025"), (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024")),
                 altro: annuale(2025), FILE + "Zztest-Annual-Report-2024.pdf": annuale(2024)})
    esito = _attiva(store, tmp_path)
    assert not any(c.startswith("https://cdn.qqsyn.example/") for c in rete["chiamate"])
    assert altro not in ((store.get_profile(TICKER) or {}).get("profile") or {}).get("ir_urls", []), esito


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


def test_r8_scarica_solo_dal_dominio_dell_emittente(rete, tmp_path):
    altro = "https://cdn.qqsyn.example/docs/Annual-Report-2025.pdf"
    rete[altro] = annuale(2025)
    r = fa._scarica_pdf_sito(altro, tmp_path, dominio="zztest.example")
    assert r["stato"] == "errore" and "altro dominio (cdn.qqsyn.example)" in r["motivo"] and rete["chiamate"] == []


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
