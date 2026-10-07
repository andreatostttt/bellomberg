"""Piattaforme IR con l'elenco dei documenti caricato da JavaScript (richiesta PM 06/10: bilanci nuovi per
qualsiasi societa'). Si leggono le stesse chiamate pubbliche JSON della pagina, senza eseguire JavaScript:
Q4 (feed FinancialReport) e MZiQ (file manager). Risposte JSON SINTETICHE, siti inventati, nessuna rete.
"""
import json
from datetime import date

import pytest

from bellomberg.market_data import esef_sito, piattaforme_ir
from tests.esef_sito_sintetici import RispostaFinta, get_finto, pagina

OGGI = date(2026, 10, 5)
HOME = "https://www.zzq4.example/"
IR = "https://investor.zzq4.example/financials/quarterly-results/default.aspx"
FEED = "https://investor.zzq4.example/feed/FinancialReport.svc/GetFinancialReportList?LanguageId=1&year=-1"
CDN = "https://s999.q4cdn.example/111/files/doc_financials/"

HTML_Q4 = ("<html><head><script src='//s999.q4cdn.com/js/q4App.js'></script></head><body>"
           "<div class='module-financial-table'></div><script>$('#x').q4FinancialDetails({});</script></body></html>")

MZ_FM = "11111111-2222-3333-4444-555555555555"
MZ_BASE = "https://api.mziq.example/mzfilemanager"
HTML_MZ = ("<html><body><script>const fmId = '" + MZ_FM + "'; const fmName = 'Zz - 2021'; "
           "const fmBase = '" + MZ_BASE + "'; const language = 'en-US';</script>"
           "<script>var categories = []; categories.push({ title: 'Financial Statements', internal_name: 'itr' }); "
           "categories.push({ title: 'Earnings Press Release', internal_name: 'release_resultados ' }); "
           "categories.push({ title: 'Results Spreadsheet', internal_name: 'planilha_resultados' });</script>"
           "</body></html>")


def _q4_json(*report):
    return json.dumps({"GetFinancialReportListResult": [
        {"ReportSubType": sub, "ReportTitle": f"{sub} {anno}", "ReportYear": anno,
         "Documents": [{"DocumentCategory": cat, "DocumentPath": path, "DocumentTitle": titolo, "DocumentFileType": ft}
                       for cat, path, titolo, ft in docs]}
        for sub, anno, docs in report]}).encode()


def _mz_json(*docs):
    return json.dumps({"success": True, "data": {"document_metas": [
        {"file_title": t, "file_year": a, "file_quarter": q, "category_internal_name": c, "link_url": None,
         "permalink": f"{MZ_BASE}/v2/d/{MZ_FM}/{i:04d}?origin=2", "extension_file": e}
        for i, (t, a, q, c, e) in enumerate(docs)]}}).encode()


def test_rileva_q4_e_mziq_dalla_pagina():
    q4 = piattaforme_ir.rileva(HTML_Q4, IR)
    assert q4 == [{"tipo": "q4", "url": FEED, "pagina": IR}], q4
    mz = piattaforme_ir.rileva(HTML_MZ, "https://www.zzmz.example/en/results/")
    assert mz == [{"tipo": "mziq", "url": f"{MZ_BASE}/company/{MZ_FM}/filter/categories/meta",
                   "corpo": {"categoryInternalNames": ["itr", "release_resultados ", "release_resultados"],
                             "language": "en_US", "published": True},
                   "pagina": "https://www.zzmz.example/en/results/"}], mz
    assert piattaforme_ir.rileva("<html><body>nessuna piattaforma</body></html>", IR) == []
    # MZiQ senza fmId o con base non https: non rilevata
    assert piattaforme_ir.rileva(HTML_MZ.replace(MZ_FM, "x"), IR) == []
    assert piattaforme_ir.rileva(HTML_MZ.replace("https://api", "http://api"), IR) == []


def test_documenti_q4_diventano_voci_con_trimestre_e_tipo():
    dati = json.loads(_q4_json(
        ("Second Quarter", 2026, [("tenq", CDN + "2026/q2/10-Q.pdf", "10-Q", "PDF"),
                                  ("news", "/news/news-details/2026/q2/default.aspx", "Q2 Press Release", "Online"),
                                  ("presentation", CDN + "2026/q2/deck.pdf", "Q2 2026 Earnings Presentation", "PDF"),
                                  ("webcast", "https://webcast.example/x", "Webcast", None)]),
        ("Annual Report", 2025, [("file", CDN + "2025/ar/AR.pdf", "2025 Annual Report", "PDF")]),
        ("First Quarter", 2019, [("tenq", CDN + "2019/q1/10-Q.pdf", "10-Q", "PDF")])))
    voci = piattaforme_ir.documenti_q4(dati, pagina=IR, oggi=OGGI)
    per_url = {v["url"]: v for v in voci}
    assert set(per_url) == {CDN + "2026/q2/10-Q.pdf", CDN + "2026/q2/deck.pdf", CDN + "2025/ar/AR.pdf"}  # 2019: vecchio
    assert all(v["pagina"] == IR and v["piattaforma"] == "q4" for v in voci)
    e = esef_sito.classifica_pdf(per_url[CDN + "2026/q2/10-Q.pdf"], dominio="zzq4.example")
    assert (e["ammesso"], e["tipo"], e["periodo"], e["tipo_documento"], e["via_host"]) == (
        True, "trimestrale", "2026-06-30", "relazione", "s999.q4cdn.example"), e
    assert e["periodo_stato"] == "da_confermare"  # trimestre col solo anno: solare presunto
    p = esef_sito.classifica_pdf(per_url[CDN + "2026/q2/deck.pdf"], dominio="zzq4.example")
    assert p["tipo_documento"] == "presentazione"
    a = esef_sito.classifica_pdf(per_url[CDN + "2025/ar/AR.pdf"], dominio="zzq4.example")
    assert (a["tipo"], a["periodo"]) == ("annuale", "2025-12-31")


def test_documenti_mziq_senza_estensione_e_senza_fogli_di_calcolo():
    dati = json.loads(_mz_json(("Financial Statements 2Q26", 2026, 2, "itr", None),
                               ("2Q26 Earnings Release", 2026, 2, "release_resultados", None),
                               ("Results Spreadsheet 2Q26", 2026, 2, "planilha_resultados", None),
                               ("Financial Statements 2Q26", 2026, 2, "itr", "xlsx")))
    voci = piattaforme_ir.documenti_mziq(dati, pagina="https://www.zzmz.example/en/results/", oggi=OGGI)
    assert len(voci) == 2, voci
    e = esef_sito.classifica_pdf(voci[0], dominio="zzmz.example")
    assert (e["ammesso"], e["tipo"], e["periodo"], e["tipo_documento"], e["via_host"]) == (
        True, "trimestrale", "2026-06-30", "relazione", "api.mziq.example"), e
    c = esef_sito.classifica_pdf(voci[1], dominio="zzmz.example")
    assert c["tipo_documento"] == "comunicato_risultati" and c["periodo"] == "2026-06-30"


def test_json_inatteso_dichiarato_non_ignorato():
    with pytest.raises(ValueError, match="forma inattesa"):
        piattaforme_ir.documenti_q4({"altro": 1}, pagina=IR, oggi=OGGI)
    with pytest.raises(ValueError, match="success"):
        piattaforme_ir.documenti_mziq({"success": False}, pagina=IR, oggi=OGGI)


def _nav(risposte, chiamate):
    def get(url, **kw):
        chiamate.append((kw.get("method") or "GET", url, kw.get("json")))
        v = risposte.get(url)
        if v is None:
            return RispostaFinta(url, b"", status=404)
        return v if isinstance(v, RispostaFinta) else RispostaFinta(url, v)
    return lambda d: esef_sito.Navigatore(d, get=get, dormi=lambda s: None)


def test_scopri_legge_il_feed_q4_col_robots_dell_host(tmp_path):
    chiamate = []
    risposte = {HOME: pagina((IR, "Quarterly results")), IR: RispostaFinta(IR, HTML_Q4.encode()),
                FEED: RispostaFinta(FEED, _q4_json(
                    ("Second Quarter", 2026, [("tenq", CDN + "2026/q2/10-Q.pdf", "10-Q", "PDF")]),
                    ("Second Quarter", 2025, [("tenq", CDN + "2025/q2/10-Q.pdf", "10-Q", "PDF")])),
                    tipo="application/json")}
    esito = esef_sito.scopri("ZZQ4", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: HOME, navigatore_fn=_nav(risposte, chiamate))
    assert ("GET", FEED, None) in chiamate and ("GET", "https://investor.zzq4.example/robots.txt", None) in chiamate
    # i documenti della piattaforma contano come documenti della home: nessun candidato standard provato
    assert not any(u.startswith("https://investors.zzq4.example/") for _, u, _ in chiamate), chiamate
    assert esito["piattaforme"] == [{"tipo": "q4", "pagina": IR, "documenti": 2, "motivo": None}], esito["piattaforme"]
    trovati = esef_sito.pdf_trovati("ZZQ4", cache_dir=tmp_path, oggi=OGGI)
    assert (trovati["ultimo"]["periodo"], trovati["precedente"]["periodo"]) == ("2026-06-30", "2025-06-30")
    assert trovati["etichetta"] == "sito dell'emittente via s999.q4cdn.example, non archivio ufficiale (OAM)"


def test_scopri_mziq_con_post_e_robots_vietato_dichiarato(tmp_path):
    chiamate = []
    risultati = "https://www.zzmz.example/en/results/"
    api = f"{MZ_BASE}/company/{MZ_FM}/filter/categories/meta"
    home = "https://www.zzmz.example/"
    risposte = {home: pagina((risultati, "Results Center")), risultati: RispostaFinta(risultati, HTML_MZ.encode()),
                api: RispostaFinta(api, _mz_json(("Financial Statements 2Q26", 2026, 2, "itr", None)),
                                   tipo="application/json")}
    esito = esef_sito.scopri("ZZMZ", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: home, navigatore_fn=_nav(risposte, chiamate))
    post = [c for c in chiamate if c[0] == "POST"]
    assert post and post[0][1] == api and post[0][2]["language"] == "en_US", chiamate
    assert esito["piattaforme"][0]["documenti"] == 1
    assert esef_sito.pdf_trovati("ZZMZ", cache_dir=tmp_path, oggi=OGGI)["ultimo"]["periodo"] == "2026-06-30"
    # robots.txt dell'API che vieta: nessuna POST, dichiarato
    chiamate.clear()
    risposte["https://api.mziq.example/robots.txt"] = RispostaFinta("", b"User-agent: *\nDisallow: /\n", tipo="text/plain")
    esito = esef_sito.scopri("ZZMZ", oggi=OGGI, cache_dir=tmp_path / "b", sito_fn=lambda t: home,
                             navigatore_fn=_nav(risposte, chiamate))
    assert not [c for c in chiamate if c[0] == "POST"]
    assert "robots.txt vieta" in esito["piattaforme"][0]["motivo"] and esito["piattaforme"][0]["documenti"] == 0


def test_feed_403_dichiarato(tmp_path):
    chiamate = []
    risposte = {HOME: pagina((IR, "Quarterly results")), IR: RispostaFinta(IR, HTML_Q4.encode()),
                FEED: RispostaFinta(FEED, b"no", status=403)}
    esito = esef_sito.scopri("ZZQ4", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: HOME, navigatore_fn=_nav(risposte, chiamate))
    assert esito["piattaforme"][0]["motivo"].startswith("sito blocca i bot (HTTP 403)"), esito["piattaforme"]


def test_nessun_nome_di_societa_nel_modulo():
    import inspect
    sorgente = inspect.getsource(piattaforme_ir).lower()
    for vietato in ("nubank", "first solar", "firstsolar", "micron", "nu holdings"):
        assert vietato not in sorgente


def test_rfonti_r9_fmbase_che_non_e_il_file_manager_non_rilevato():
    html = HTML_MZ.replace(MZ_BASE, "https://api.zzaltro.example/altro")
    assert piattaforme_ir.rileva(html, "https://www.zzmz.example/en/results/") == []
