"""Fase F, Task 5: PDF IR trovati da soli sul sito, proposta AI a un clic. Sito finto, nessuna rete, nessuna AI."""
import json
from datetime import date, datetime, timedelta

import pytest

from bellomberg.market_data import esef_sito
from bellomberg.storage import filing_preferenze as fpref
from tests.test_filing_routes_pagina import _titolo, env  # noqa: F401  (fixture)

BASE = "https://www.acme.example/investors/"
OGGI = date(2026, 10, 3)


def _link(*voci):
    return [{"url": BASE + nome, "testo": testo} for nome, testo in voci]


def test_scelta_ultimo_e_omologo_dell_anno_prima():
    scelta = esef_sito.scegli_pdf(_link(
        ("acme-annual-report-2025.pdf", "Annual Report 2025"), ("acme-annual-report-2024.pdf", "Annual Report 2024"),
        ("acme-half-year-report-2025.pdf", "Half-year report 2025"),
        ("acme-q1-2026-presentation.pdf", "Q1 2026 results presentation"),  # presentazione: ammessa con etichetta (PM 06/10)
        ("remuneration-report-2025.pdf", "Remuneration report"), ("brochure.pdf", "Our products")), oggi=OGGI)
    assert scelta["tipo"] == "annuale" and scelta["ultimo"]["url"].endswith("annual-report-2025.pdf")
    assert scelta["precedente"]["url"].endswith("annual-report-2024.pdf") and scelta["candidati"] == 4
    assert scelta["ammessi_per_tipo"] == {"relazione": 3, "presentazione": 1}


def test_md_and_a_trimestrali_con_data_nel_nome():
    scelta = esef_sito.scegli_pdf(_link(
        ("Acme-MDA-2026-06-30.pdf", "Q2 2026 MD&A"), ("Acme-MDA-2025-06-30.pdf", "Q2 2025 MD&A"),
        ("Acme-MDA-2026-03-31.pdf", "Q1 2026 MD&A"), ("Acme-AIF-2025.pdf", "Annual Information Form")), oggi=OGGI)
    assert (scelta["tipo"], scelta["ultimo"]["periodo"], scelta["precedente"]["periodo"]) == (
        "trimestrale", "2026-06-30", "2025-06-30")


def test_semestrale_piu_recente_dell_annuale_senza_omologo():
    scelta = esef_sito.scegli_pdf(_link(("annual-report-2025.pdf", "Annual report"),
                                        ("relazione-finanziaria-semestrale-2026.pdf", "Relazione semestrale 2026")), oggi=OGGI)
    assert scelta["tipo"] == "semestrale" and scelta["precedente"] is None


def test_nessuna_relazione_o_periodo_futuro():
    assert esef_sito.scegli_pdf(_link(("brochure.pdf", "Products"), ("annual-report.pdf", "Annual report")), oggi=OGGI) is None
    assert esef_sito.scegli_pdf(_link(("annual-report-2027.pdf", "Annual report 2027")), oggi=OGGI) is None


def test_scopri_senza_fonte_mai_con_il_consigliere():
    esplorati = []
    scopri = lambda t, oggi=None: esplorati.append(t) or {"pdf": _link(("annual-report-2025.pdf", "Annual report"))}
    assert esef_sito.scopri_senza_fonte(["ACME.PA"], consigliere_fn=lambda: True, scopri_fn=scopri)[0]["rinviata"]
    assert esplorati == []
    assert esef_sito.scopri_senza_fonte(["ACME.PA"], oggi=OGGI, consigliere_fn=lambda: False, scopri_fn=scopri) == [
        {"ticker": "ACME.PA", "pdf": True, "motivi": []}]


def test_controllo_giornaliero_solo_senza_fonte_senza_profilo(monkeypatch):
    class Store:
        def list_profiles(self):
            return [{"ticker": "NOVA.DE", "profile": {"cik": "1"}, "enabled": True}]
    pref = {"esiti": {"ACME.PA": {"esito": "senza_fonte"}, "NOVA.DE": {"esito": "senza_fonte"},
                      "KORE.MI": {"esito": "da_confermare"}, "ESCL.MI": {"esito": "senza_fonte"}}}
    visti = []
    monkeypatch.setattr(esef_sito, "scopri_senza_fonte", lambda tickers, **k: visti.extend(tickers) or [])
    esef_sito.scopri_giornaliero(Store(), escludi={"ESCL.MI"}, consigliere_fn=lambda: False, pref_fn=lambda: pref)
    assert visti == ["ACME.PA"]


def _cache(tmp_path_sito, ticker, pdf, at=None):
    tmp_path_sito.mkdir(parents=True, exist_ok=True)
    (tmp_path_sito / f"{ticker}.json").write_text(json.dumps({
        "ticker": ticker, "at": at or datetime.now().isoformat(timespec="seconds"), "sito": "https://www.acme.example/",
        "pagine": [], "pacchetti": [], "pdf": pdf, "motivi": []}))


def test_panoramica_mostra_i_pdf_trovati_senza_rete(env, monkeypatch):
    _cache(esef_sito._cache_dir(), "ACME.PA", _link(("annual-report-2025.pdf", "Annual report 2025"),
                                                    ("annual-report-2024.pdf", "Annual report 2024")))
    monkeypatch.setattr(esef_sito, "scopri", lambda *a, **k: pytest.fail("nessuna esplorazione dalla panoramica"))
    fpref.registra_esito("ACME.PA", "senza_fonte", "nessuna fonte gratuita", path=env.pref)
    acme = _titolo(env.client().get("/filings").json(), "ACME.PA")
    assert acme["stato"] == "senza_fonte"  # resta «senza fonte» finche' la proposta non e' salvata
    assert acme["pdf_ir"]["ultimo"]["url"] == BASE + "annual-report-2025.pdf"
    assert acme["pdf_ir"]["precedente"]["url"] == BASE + "annual-report-2024.pdf"
    assert _titolo(env.client().get("/filings").json(), "NOVA.DE")["pdf_ir"] is None


def test_search_pdf_su_richiesta_e_409_con_il_consigliere(env, monkeypatch):
    chiamate = []

    def scopri(ticker, forza=False):
        chiamate.append((ticker, forza))
        _cache(esef_sito._cache_dir(), ticker, _link(("annual-report-2025.pdf", "Annual report 2025")))
        return {"sito": "https://www.acme.example/", "pagine": [BASE], "motivi": ["limite di 12 pagine raggiunto"]}
    monkeypatch.setattr(esef_sito, "scopri", scopri)
    monkeypatch.setattr(esef_sito, "consigliere_in_corso", lambda: False)
    r = env.client().post("/filings/ACME.PA/search-pdf")
    assert r.status_code == 200, r.text
    out = r.json()
    assert chiamate == [("ACME.PA", True)] and out["pdf_ir"]["tipo"] == "annuale" and out["pagine"] == 1
    monkeypatch.setattr(esef_sito, "consigliere_in_corso", lambda: True)
    assert env.client().post("/filings/ACME.PA/search-pdf").status_code == 409
    assert env.client().post("/filings/ACME.PA/search-pdf", headers={"X-BB-Token": "x"}).status_code == 401


def test_su_richiesta_al_massimo_un_esplorazione_l_ora(tmp_path, monkeypatch):
    visite = []
    # conta le esplorazioni del sito della societa' (la scoperta del sito IR, seguito 06/10, ne aggiunge altre)
    monkeypatch.setattr(esef_sito, "esplora", lambda sito, navigatore=None, oggi=None: (
        visite.append(sito) if sito == "https://www.acme.example/" else None) or {"pagine": [], "link": [], "motivi": []})
    kw = dict(oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: "https://www.acme.example/",
              navigatore_fn=lambda d: None)
    esef_sito.scopri("ACME.PA", **kw)
    esef_sito.scopri("ACME.PA", forza=True, **kw)
    assert len(visite) == 1
    voce = json.loads((tmp_path / "ACME.PA.json").read_text())
    voce["at"] = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    (tmp_path / "ACME.PA.json").write_text(json.dumps(voce))
    esef_sito.scopri("ACME.PA", forza=True, **kw)
    assert len(visite) == 2


# -- prova reale 04/10/2026: regole emerse sui siti veri (dati sintetici) --

def test_prova_reale_date_e_nomi_dei_documenti():
    doc = lambda nome, testo="": esef_sito._documento_pdf({"url": BASE + nome, "testo": testo})
    assert doc("2026-05-06-acme-half-year-financial-report-31-march-2026-v01-00-en.pdf")["periodo"] == "2026-03-31"
    assert doc("2026-05-06-acme-halbjahresfinanzbericht-31-maerz-2026-de.pdf")["periodo"] == "2026-03-31"
    assert doc("Relazione_semestrale_30062025.pdf")["periodo"] == "2025-06-30"
    assert (doc("ACME-Q226-MDA-Final.pdf", "Management Discussion and Analysis")["periodo"]) == "2026-06-30"
    assert doc("Resoconto_intermedio_31032026.pdf")["tipo"] == "trimestrale"
    # decisione PM 06/10 sera: la trascrizione della call dei risultati entra, etichettata «presentazione»
    assert doc("ACME-Q2-2026-Earnings-Transcipt.pdf")["tipo_documento"] == "presentazione"
    # seguito main 06/10: gli estratti (at a glance, sintesi) entrano come «estratto»
    assert doc("2026-08-05-q3-fy26-acme-at-a-glance-v01-00-en.pdf")["tipo_documento"] == "estratto"
    for escluso in ("2024.03.14-ACME-Annual-Information-Form.pdf",
                    "Avviso_Relazione_Semestrale_30062026.pdf", "ACME_2025_Climate_Report.pdf"):
        assert doc(escluso) is None, escluso


def test_prova_reale_omologo_dello_stesso_tipo_di_documento():
    scelta = esef_sito.scegli_pdf(_link(("ACME-Q226-MDA-Final.pdf", "MD&A"), ("ACME-Q226-FS-Final.pdf", "Financial statements"),
                                        ("ACME-2025-Q2-FS-final.pdf", "Q2"), ("ACME-2025-Q2-MDA-final.pdf", "Q2")), oggi=OGGI)
    assert scelta["ultimo"]["url"].endswith("MDA-Final.pdf") and scelta["precedente"]["url"].endswith("2025-Q2-MDA-final.pdf")


def test_prova_reale_interim_a_marzo_e_trimestrale():
    doc = esef_sito._documento_pdf({"url": BASE + "interim-financial-report_31march2026.pdf", "testo": ""})
    assert (doc["tipo"], doc["periodo"]) == ("trimestrale", "2026-03-31")
    meta = esef_sito._documento_pdf({"url": BASE + "acme-half-year-financial-report-31-march-2026.pdf", "testo": ""})
    assert meta["tipo"] == "semestrale"  # esercizio a settembre: il semestre chiude a marzo
