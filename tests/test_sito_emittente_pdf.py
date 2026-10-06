"""Relazioni periodiche in PDF dal sito dell'emittente (decisione PM 05/10, opzione B).

Per QUALUNQUE emittente senza documenti dall'archivio ufficiale: riconoscimento su nome, titolo e
prima pagina, presentazioni escluse col motivo, periodo obbligatorio, origine sempre dichiarata
(«sito dell'emittente, non archivio ufficiale (OAM)»), robots.txt e blocchi (HTTP 403) dichiarati.
Societa', domini e PDF inventati (ir.zztest.example), nessuna rete.
"""
import io
import json
from datetime import date

import pytest

from bellomberg.market_data import depositi_ue, esef_sito
from tests.esef_sito_sintetici import RispostaFinta, get_finto, pagina

SITO = "https://ir.zztest.example/"
IR = "https://ir.zztest.example/investors/reports"
FILE = "https://ir.zztest.example/files/"
OGGI = date(2026, 10, 5)


def _pdf(*righe):
    """PDF vero (reportlab) con le righe date sulla prima pagina."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for r in righe:
        c.drawString(72, y, r)
        y -= 24
    c.showPage()
    c.drawString(72, 800, "Seconda pagina 2099")
    c.showPage()
    c.save()
    return buf.getvalue()


def _voce(nome, testo="", base=FILE):
    return {"url": base + nome, "testo": testo}


def _classe(nome, testo="", **kw):
    return esef_sito.classifica_pdf(_voce(nome, testo), **kw)


def _nav(pagine, chiamate=None, pause=None):
    chiamate = [] if chiamate is None else chiamate
    pause = [] if pause is None else pause
    return lambda dominio: esef_sito.Navigatore(dominio, get=get_finto(pagine, chiamate), dormi=pause.append)


# ---------------------------------------------------------------- riconoscimento del documento giusto

@pytest.mark.parametrize("nome,tipo,periodo", [
    ("Zztest-Geschaeftsbericht-2025.pdf", "annuale", "2025-12-31"),
    ("Zztest-Annual-Report-2025.pdf", "annuale", "2025-12-31"),
    ("Zztest-Halbjahresfinanzbericht-2026.pdf", "semestrale", "2026-06-30"),
    ("Zztest-Quartalsmitteilung-2026-Q1.pdf", "trimestrale", "2026-03-31"),
    ("Zztest-Quarterly-Statement-2025-Q3.pdf", "trimestrale", "2025-09-30"),
    ("qqsyn-relazione-finanziaria-annuale-2025.pdf", "annuale", "2025-12-31"),
    ("qqsyn-rapport-financier-semestriel-30-juin-2026.pdf", "semestrale", "2026-06-30"),
    ("qqsyn-informe-anual-2025.pdf", "annuale", "2025-12-31"),
    ("qqsyn-informe-semestral-2026.pdf", "semestrale", "2026-06-30"),
])
def test_relazioni_multilingua_ammesse_col_periodo_e_l_origine(nome, tipo, periodo):
    e = _classe(nome)
    assert (e["ammesso"], e["tipo"], e["periodo"]) == (True, tipo, periodo), e
    assert e["base_periodo"]  # il periodo dichiara da dove viene
    assert e["origine"] == "sito_emittente" and e["etichetta"] == "sito dell'emittente, non archivio ufficiale (OAM)"


@pytest.mark.parametrize("nome", [
    "Zztest-Investor-Presentation-2026-May.pdf", "2026-08-06-Zztest-Conference-Call-Q2-2026.pdf",
    "2026-03-11-Zztest-Praesentation-Bilanz-Pressekonferenz.pdf", "Zztest-CMD-2025-Presentation.pdf",
    "2026-07-02-Zztest-Q2-Analyst-and-Investor-Recap-call.pdf", "Zztest-2024-Q3-Transcript.pdf",
])
def test_presentazioni_escluse_col_motivo(nome):
    e = _classe(nome)
    assert e["ammesso"] is False and e["motivo"].startswith("presentazione/slide per investitori"), e
    assert e["etichetta"] == esef_sito.ETICHETTA_SITO  # anche gli scarti portano l'origine


def test_comunicati_della_sezione_stampa_esclusi_col_motivo():
    e = esef_sito.classifica_pdf(_voce("2026-03-11-Zztest-presents-Annual-Report-for-2025.pdf",
                                       base="https://ir.zztest.example/Zztest Group/Presse/News/Documents/2026/03/"))
    assert not e["ammesso"] and "sezione stampa" in e["motivo"]
    e = _classe("2026-08-06-Zztest-News-Half-Yearly-Financial-Report-H1.pdf")
    assert not e["ammesso"] and "news" in e["motivo"].lower()


def test_data_di_pubblicazione_non_e_il_periodo():
    # «…bilancio 2024 …_23.05.2025»: il 23/05 non chiude un periodo, e' la pubblicazione
    e = _classe("QQSYN_bilanciointegrato2024_ENG_23.05.2025.pdf")
    assert (e["ammesso"], e["periodo"]) == (True, "2024-12-31")
    # solo la data di pubblicazione: periodo ignoto -> non ammesso, motivo scritto, serve la prima pagina
    e = _classe("2026-08-06-Zztest-Half-Yearly-Financial-Report.pdf")
    assert not e["ammesso"] and e["periodo"] is None and "periodo di riferimento non dichiarato" in e["motivo"]
    assert e["serve_prima_pagina"]


def test_periodo_ignoto_non_ammesso_e_dichiarato():
    e = _classe("Zztest-Quarterly-Statement-2023.pdf")  # trimestre mancante: l'anno da solo non basta
    assert not e["ammesso"] and "non ammesso" in e["motivo"] and e["tipo"] == "trimestrale"
    letta = _classe("Zztest-Quarterly-Statement-2023.pdf", prima_pagina="Zztest AG Quarterly Statement Q1 2023")
    assert (letta["ammesso"], letta["periodo"]) == (True, "2023-03-31")
    nulla = _classe("Zztest-Quarterly-Statement-2023.pdf", prima_pagina="Zztest AG")
    assert not nulla["ammesso"] and "ne' nella prima pagina" in nulla["motivo"]
    errore = _classe("Zztest-Quarterly-Statement-2023.pdf", prima_pagina={"errore": "SitoBloccato: sito blocca i bot (HTTP 403)"})
    assert not errore["ammesso"] and "HTTP 403" in errore["motivo"] and not errore["serve_prima_pagina"]


def test_nome_generico_deciso_dalla_prima_pagina():
    assert _classe("Zztest First Half 2026 results.pdf")["serve_prima_pagina"]
    vera = _classe("Zztest First Half 2026 results.pdf",
                   prima_pagina="Zztest S.p.A. Half-Year Financial Report as at 30 June 2026")
    assert (vera["ammesso"], vera["tipo"], vera["periodo"]) == (True, "semestrale", "2026-06-30")
    slide = _classe("Zztest First Half 2026 results.pdf", prima_pagina="First Half 2026 Results - Analyst presentation")
    assert not slide["ammesso"] and slide["motivo"].startswith("prima pagina: presentazione")
    # documento senza tipo nel nome: tipo e periodo dalla copertina
    copertina = _classe("Consolidated Financial Report 2026.pdf",
                        prima_pagina="Interim Consolidated Financial Report as of June 30, 2026")
    assert (copertina["ammesso"], copertina["tipo"], copertina["periodo"]) == (True, "semestrale", "2026-06-30")


def test_scelta_con_etichetta_scarti_e_inglese_preferito():
    link = [_voce(n) for n in (
        "Zztest-Halbjahresfinanzbericht-2026.pdf", "Zztest-Half-Yearly-Financial-Report-2026.pdf",
        "Zztest-Half-Yearly-Financial-Report-2025.pdf", "Zztest-Investor-Presentation-2026-September.pdf",
        "Zztest-Annual-Report-2025.pdf", "Zztest-Annual-Report-2027.pdf")]
    s = esef_sito.scegli_pdf(link, oggi=OGGI)
    assert (s["tipo"], s["ultimo"]["url"], s["precedente"]["url"]) == (
        "semestrale", FILE + "Zztest-Half-Yearly-Financial-Report-2026.pdf", FILE + "Zztest-Half-Yearly-Financial-Report-2025.pdf")
    assert s["origine"] == "sito_emittente" and s["deposito_ufficiale"] is False
    assert s["etichetta"] == "sito dell'emittente, non archivio ufficiale (OAM)" and s["etichetta_en"]
    assert s["ultimo"]["etichetta"] == s["precedente"]["etichetta"] == s["etichetta"]
    motivi = {x["url"].rsplit("/", 1)[-1]: x["motivo"] for x in s["scartati"]}
    assert motivi["Zztest-Investor-Presentation-2026-September.pdf"].startswith("presentazione")
    assert "nel futuro" in motivi["Zztest-Annual-Report-2027.pdf"]
    assert s["scartati_totale"] == 2


# ---------------------------------------------------------------- link nei dati della pagina

def test_link_in_json_con_slash_codificati_e_spazi():
    html = ('<html><body><script>var d={"file":"\\u002FZztest Group\\u002FInvestor Relations\\u002F2026'
            '\\u002FZztest-Annual-Report-2025.pdf","x":"\\/files\\/Zztest-Quartalsmitteilung-2026-Q1.pdf"}'
            '</script></body></html>')
    urls = [u for u, _ in esef_sito.estrai_link(html, SITO)]
    assert "https://ir.zztest.example/Zztest Group/Investor Relations/2026/Zztest-Annual-Report-2025.pdf" in urls
    assert "https://ir.zztest.example/files/Zztest-Quartalsmitteilung-2026-Q1.pdf" in urls


# ---------------------------------------------------------------- esplorazione: robots.txt, 403, pausa

def _sito(pdf_link, robots=None, extra=None):
    pagine = {SITO: pagina((IR, "Financial reports")), IR: pagina(*pdf_link)}
    if robots is not None:
        pagine[SITO + "robots.txt"] = RispostaFinta("", robots.encode(), tipo="text/plain")
    pagine.update(extra or {})
    return pagine


def test_scopri_trova_le_relazioni_e_dichiara_l_origine_nei_motivi(tmp_path):
    chiamate, pause = [], []
    pagine = _sito([(FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025"),
                    (FILE + "Zztest-Annual-Report-2024.pdf", "Annual Report 2024"),
                    (FILE + "Zztest-Investor-Presentation-2026.pdf", "Investor presentation")])
    esito = esef_sito.scopri("ZZTEST.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(pagine, chiamate, pause))
    assert esito["accesso"] == {"stato": "ok", "motivo": None}
    assert esito["motivi"][0].startswith("relazione annuale al 2025-12-31 in PDF dal sito dell'emittente, "
                                         "non archivio ufficiale (OAM)")
    assert pause and all(p == esef_sito.PAUSA_S for p in pause)  # pausa fra le richieste
    trovati = esef_sito.pdf_trovati("ZZTEST.DE", cache_dir=tmp_path, oggi=OGGI)
    assert trovati["ultimo"]["periodo"] == "2025-12-31" and trovati["precedente"]["periodo"] == "2024-12-31"
    assert trovati["etichetta"] == esef_sito.ETICHETTA_SITO and trovati["deposito_ufficiale"] is False
    assert trovati["accesso"]["stato"] == "ok"
    assert any(x["motivo"].startswith("presentazione") for x in trovati["scartati"])


def test_sito_che_blocca_i_bot_dichiarato_e_nessuna_insistenza(tmp_path):
    chiamate = []
    pagine = {SITO: RispostaFinta(SITO, b"Your bot has been blocked", status=403)}
    esito = esef_sito.scopri("QQSYN.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(pagine, chiamate))
    assert esito["accesso"] == {"stato": "bloccato", "motivo": "sito blocca i bot (HTTP 403)"}
    assert esito["motivi"][0].startswith("sito blocca i bot (HTTP 403)")
    assert [c for c in chiamate if not c.endswith("robots.txt")] == [SITO]  # una pagina e basta
    assert esef_sito.pdf_trovati("QQSYN.DE", cache_dir=tmp_path, oggi=OGGI) is None


def test_403_a_meta_esplorazione_ferma_tutto(tmp_path):
    chiamate = []
    altra = SITO + "investors/financial-reports"
    pagine = {SITO: pagina((IR, "Annual reports"), (altra, "Financial reports")),
              altra: RispostaFinta(altra, b"blocked", status=403), IR: pagina()}
    esito = esef_sito.scopri("QQSYN.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(pagine, chiamate))
    pagine_chieste = [c for c in chiamate if not c.endswith("robots.txt")]
    assert pagine_chieste == [SITO, altra]  # dopo il 403 nessun'altra pagina (IR resta non chiesta)
    assert esito["accesso"]["stato"] == "bloccato"


def test_robots_txt_negato_403_vale_come_tutto_vietato(tmp_path):
    chiamate = []
    pagine = {SITO + "robots.txt": RispostaFinta("", b"no", status=403), SITO: pagina((IR, "Reports"))}
    esito = esef_sito.scopri("QQSYN.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(pagine, chiamate))
    assert chiamate == [SITO + "robots.txt"]  # la home non si chiede
    assert esito["accesso"]["stato"] == "bloccato" and "HTTP 403" in esito["accesso"]["motivo"]


def test_robots_txt_che_vieta_tutto_dichiarato(tmp_path):
    chiamate = []
    pagine = _sito([(FILE + "Zztest-Annual-Report-2025.pdf", "Annual report")], robots="User-agent: *\nDisallow: /\n")
    esito = esef_sito.scopri("QQSYN.FR", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(pagine, chiamate))
    assert chiamate == [SITO + "robots.txt"]
    assert esito["accesso"] == {"stato": "robots_vieta",
                                "motivo": "robots.txt vieta l'esplorazione delle pagine del sito"}


def test_prima_pagina_letta_dal_pdf_vero_con_robots_e_tetto(tmp_path):
    generico = FILE + "Zztest-Results-H1-2026.pdf"     # nome generico: decide la copertina
    slide = FILE + "Zztest-First-Half-2025-results.pdf"
    vietato = "https://ir.zztest.example/private/Zztest-Interim-2024-results.pdf"
    pagine = _sito([(generico, ""), (slide, ""), (vietato, "")],
                   robots="User-agent: *\nDisallow: /private\n",
                   extra={generico: RispostaFinta(generico, _pdf("Zztest SE", "Half-Year Financial Report",
                                                                  "1 January to 30 June 2026"), tipo="application/pdf"),
                          slide: RispostaFinta(slide, _pdf("First Half 2025", "Investor presentation"),
                                               tipo="application/pdf")})
    chiamate = []
    esito = esef_sito.scopri("ZZTEST.MI", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(pagine, chiamate))
    prime = esito["prime_pagine"]
    assert "30 June 2026" in prime[generico]["testo"] and "Seconda pagina" not in prime[generico]["testo"]
    assert "robots.txt vieta" in prime[vietato]["errore"] and vietato not in chiamate
    trovati = esef_sito.pdf_trovati("ZZTEST.MI", cache_dir=tmp_path, oggi=OGGI)
    assert (trovati["ultimo"]["url"], trovati["ultimo"]["periodo"]) == (generico, "2026-06-30")
    assert "prima pagina" in trovati["ultimo"]["base_periodo"]
    motivi = {x["url"]: x["motivo"] for x in trovati["scartati"]}
    assert motivi[slide].startswith("prima pagina: presentazione") and "robots.txt vieta" in motivi[vietato]


def test_prime_pagine_al_massimo_il_limite_dichiarato(tmp_path):
    link = [(FILE + f"Zztest-Results-H1-20{a}.pdf", "") for a in range(10, 20)]
    letti = []
    esito = esef_sito.scopri("ZZTEST.MI", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(_sito(link)), prima_pagina_fn=lambda u: letti.append(u) or "")
    assert len(letti) == esef_sito.MAX_PRIME_PAGINE and letti[0].endswith("2019.pdf")  # i piu' recenti
    assert esito["motivi"][0].startswith("nessuna relazione periodica ammessa tra 10 PDF del sito dell'emittente")


def test_prima_pagina_pdf_troppo_grande_o_non_pdf():
    url = FILE + "x.pdf"
    nav = esef_sito.Navigatore("zztest.example", get=get_finto({url: RispostaFinta(url, b"<html>", tipo="text/html")}, []),
                               dormi=lambda s: None)
    with pytest.raises(ValueError, match="non e' un PDF"):
        nav.prima_pagina_pdf(url)
    nav = esef_sito.Navigatore("zztest.example", get=get_finto({url: RispostaFinta(url, _pdf("Annual Report 2025"))}, []),
                               dormi=lambda s: None)
    with pytest.raises(ValueError, match="PDF troppo grande"):
        nav.prima_pagina_pdf(url, max_bytes=100)


# ---------------------------------------------------------------- generale, non una lista di societa'

def test_tre_paesi_tre_siti_inventati_stessa_regola(tmp_path):
    casi = {"ZZFR.PA": ("https://www.zzfr.example/", "zzfr-rapport-financier-annuel-2025.pdf", "annuale"),
            "ZZES.MC": ("https://www.zzes.example/", "zzes-informe-semestral-30-junio-2026.pdf", "semestrale"),
            "ZZSE.ST": ("https://www.zzse.example/", "zzse-interim-report-q2-2026.pdf", "trimestrale")}
    for ticker, (sito, nome, tipo) in casi.items():
        reports = sito + "investors/reports"
        pagine = {sito: pagina((reports, "Financial reports")), reports: pagina((sito + "docs/" + nome, ""))}
        esef_sito.scopri(ticker, oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t, s=sito: s, navigatore_fn=_nav(pagine))
        trovati = esef_sito.pdf_trovati(ticker, cache_dir=tmp_path, oggi=OGGI)
        assert trovati["tipo"] == tipo and trovati["etichetta"] == esef_sito.ETICHETTA_SITO, ticker


def test_nessun_nome_di_societa_vera_nel_codice():
    import inspect
    sorgente = inspect.getsource(esef_sito).lower()
    for vietato in ("rheinmetall", "bayer", "saipem", "leonardo", "fincantieri"):
        assert vietato not in sorgente


# ---------------------------------------------------------------- copertura UE: motivo misurato

def test_copertura_de_col_motivo_misurato_e_il_ripiego_dichiarato():
    de = depositi_ue.COPERTURA_UE["DE"]
    assert de["modulo"] is None  # il sito non e' un archivio ufficiale: la data resta del PM
    for parola in ("robots.txt", "captcha", "filings.xbrl.org", "0 ", "sito dell'emittente", "fornire dal PM"):
        assert parola in de["motivo_limite"], parola
    assert "captcha" in de["motivo_limite_en"] and "robots.txt" in de["motivo_limite_en"]
    assert "non archivio ufficiale (OAM)" in de["ripiego_documenti"]
    assert all(v["ripiego_documenti"] for v in depositi_ue.COPERTURA_UE.values() if not v["modulo"])
    assert all(v["ripiego_documenti"] is None for v in depositi_ue.COPERTURA_UE.values() if v["modulo"])


def test_cache_porta_accesso_e_prime_pagine(tmp_path):
    pagine = _sito([(FILE + "Zztest-Annual-Report-2025.pdf", "")])
    esef_sito.scopri("ZZTEST.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO, navigatore_fn=_nav(pagine))
    voce = json.loads((tmp_path / "ZZTEST.DE.json").read_text(encoding="utf-8"))
    assert voce["accesso"]["stato"] == "ok" and voce["prime_pagine"] == {}


# ---------------------------------------------------------------- revisione R-8 (06/10)

def _nav_r8(risposte, chiamate=None):
    chiamate = [] if chiamate is None else chiamate

    def get(url, **kw):
        chiamate.append(url)
        v = risposte.get(url)
        if isinstance(v, Exception):
            raise v
        return v if v is not None else RispostaFinta(url, b"", status=404)
    return esef_sito.Navigatore("zztest.example", get=get, dormi=lambda s: None)


@pytest.mark.parametrize("stato", [500, 503])
def test_r8_robots_5xx_vale_tutto_vietato_e_dichiarato(stato):
    nav = _nav_r8({SITO + "robots.txt": RispostaFinta("", b"down", status=stato)})
    assert nav.consentito(SITO + "files/x.pdf") is False
    assert f"HTTP {stato}" in nav.robots_ignoto["ir.zztest.example"]


def test_r8_robots_irraggiungibile_vale_tutto_vietato_e_dichiarato(tmp_path):
    pagine = {SITO + "robots.txt": ConnectionError("giu https://ir.zztest.example/robots.txt?k=ZZSEGRETO")}
    nav = _nav_r8(pagine)
    assert nav.consentito(SITO) is False and "ConnectionError" in nav.robots_ignoto["ir.zztest.example"]
    esito = esef_sito.scopri("QQSYN.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=lambda d: _nav_r8(pagine))
    assert esito["accesso"]["stato"] == "robots_illeggibile" and "robots.txt non leggibile" in esito["accesso"]["motivo"]
    assert "ZZSEGRETO" not in json.dumps(esito)
    assert any(m.startswith("robots.txt non leggibile") and "rinviata" in m for m in esito["motivi"]), esito["motivi"]


def test_r8_robots_404_resta_consentito():
    assert _nav_r8({}).consentito(SITO + "files/x.pdf") is True


def test_r8_regola_per_nome_del_bot_rispettata():
    nav = _nav_r8({SITO + "robots.txt": RispostaFinta("", b"User-agent: Bellomberg\nDisallow: /\n", tipo="text/plain")})
    assert nav.consentito(SITO + "files/x.pdf") is False
    from bellomberg.market_data.lettore_trimestrali import UA
    assert "Bellomberg" in UA


def test_r8_429_e_un_limite_di_ritmo_e_ferma_l_esplorazione():
    ch = []
    visita = esef_sito.esplora(SITO, navigatore=_nav_r8({SITO: RispostaFinta(SITO, b"slow", status=429)}, ch), oggi=OGGI)
    assert any("limita il ritmo (HTTP 429)" in m for m in visita["motivi"]), visita["motivi"]


def test_r8_5xx_ripetuti_fermano_l_esplorazione():
    ch = []
    figli = [SITO + f"investor-relations/reports-{i}" for i in range(20)]
    risposte = {SITO: RispostaFinta(SITO, pagina(*[(u, "Investor reports") for u in figli]))}
    risposte.update({u: RispostaFinta(u, b"busy", status=503) for u in figli})
    visita = esef_sito.esplora(SITO, navigatore=_nav_r8(risposte, ch), oggi=OGGI)
    assert sum(1 for c in ch if "reports-" in c) == esef_sito.MAX_5XX_DI_FILA
    assert any("non disponibile" in m for m in visita["motivi"])


def test_r8_motivi_senza_url_ne_querystring():
    def get(url, **kw):
        if url.endswith("robots.txt"):
            return RispostaFinta(url, b"", status=404)
        raise ConnectionError(f"Max retries exceeded with url: {url}")
    nav = esef_sito.Navigatore("zztest.example", get=get, dormi=lambda s: None)
    visita = esef_sito.esplora(SITO + "ir?token=ZZSEGRETO", navigatore=nav, oggi=OGGI)
    assert visita["motivi"] and not any("ZZSEGRETO" in m or "https://" in m for m in visita["motivi"]), visita["motivi"]
    assert any("ConnectionError" in m for m in visita["motivi"])


def test_r8_pdf_di_altro_dominio_scartato_col_motivo():
    pdf = [{"url": "https://cdn.qqsyn.example/x/Qqsyn-Annual-Report-2025.pdf", "testo": "Annual Report 2025"},
           _voce("Zztest-Annual-Report-2024.pdf")]
    s = esef_sito.scegli_pdf(pdf, oggi=OGGI, dominio="zztest.example")
    assert s["ultimo"]["url"] == FILE + "Zztest-Annual-Report-2024.pdf"
    assert any("altro dominio (cdn.qqsyn.example)" in x["motivo"] for x in s["scartati"])


def test_r8_scopri_e_pdf_trovati_filtrano_il_dominio(tmp_path):
    altro = "https://cdn.qqsyn.example/x/Qqsyn-Annual-Report-2025.pdf"
    generico = "https://cdn.qqsyn.example/x/Results-H1-2026.pdf"   # nome generico: niente prima pagina fuori dominio
    pagine = _sito([(altro, "Annual Report 2025"), (generico, ""), (FILE + "Zztest-Annual-Report-2024.pdf", "")])
    letti = []
    esef_sito.scopri("ZZTEST.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO, navigatore_fn=_nav(pagine),
                     prima_pagina_fn=lambda u: letti.append(u) or "")
    assert letti == []
    trovati = esef_sito.pdf_trovati("ZZTEST.DE", cache_dir=tmp_path, oggi=OGGI)
    assert trovati["ultimo"]["url"] == FILE + "Zztest-Annual-Report-2024.pdf"


def test_r8_da_leggere_prima_pagina_solo_stesso_dominio():
    pdf = [{"url": "https://cdn.qqsyn.example/x/Results-H1-2026.pdf", "testo": ""}, _voce("Zztest-Results-H1-2026.pdf")]
    assert esef_sito.da_leggere_prima_pagina(pdf, "zztest.example") == [FILE + "Zztest-Results-H1-2026.pdf"]


@pytest.mark.parametrize("nome,periodo", [
    ("Zztest-Half-Year-Report-2026_31.07.2026.pdf", "2026-06-30"),      # pubblicazione a fine luglio
    ("Zztest-Annual-Report-2025_2026-03-31.pdf", "2025-12-31"),         # pubblicazione l'anno dopo
    ("Zztest-Annual-Report-2025-web_2026-04-30.pdf", "2025-12-31"),
])
def test_r8_pubblicazione_a_fine_mese_non_e_il_periodo(nome, periodo):
    e = _classe(nome)
    assert (e["ammesso"], e["periodo"]) == (True, periodo), e
    assert "presunta" in e["base_periodo"]


@pytest.mark.parametrize("nome", [
    "Zztest-Group-Pension-Fund-Annual-Report-2025.pdf", "Zztest-Annual-Report-2025-Key-Figures.pdf",
    "Zztest-Annual-Report-2025-Summary.pdf", "Zztest-Annual-Report-2025-Errata-Corrigendum.pdf",
    "Zztest-Half-Year-Report-2026-Auditor-Review-Report.pdf",
])
def test_r8_estratti_e_documenti_di_altri_soggetti_esclusi(nome):
    e = _classe(nome)
    assert not e["ammesso"] and "non e' una relazione periodica" in e["motivo"], e


def test_r8_guardiano_consigliere_sulla_forma_dell_argv(monkeypatch):
    # il conftest spegne consigliere_in_corso per tutti i test: qui si carica la funzione VERA dal file
    import importlib.util
    import psutil
    spec = importlib.util.spec_from_file_location("esef_sito_guardiano", esef_sito.__file__)
    vero = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vero)

    class Proc:
        def __init__(self, argv):
            self.info = {"cmdline": argv}
    righe = [["C:/Program Files/Git/bin/bash.exe", "-c", "grep -n consigliere_multi src"],
             ["python.exe", "-m", "pytest", "tests/test_cablaggio_consigliere_multi.py"]]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [Proc(a) for a in righe])
    assert vero.consigliere_in_corso() is False  # una shell che cita la parola non e' una run
    righe.append(["C:/Py/python.exe", "-u", "-m", "bellomberg.agents.consigliere_multi"])
    assert vero.consigliere_in_corso() is True
