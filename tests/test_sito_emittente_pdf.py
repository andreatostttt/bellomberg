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


@pytest.mark.parametrize("nome,tipo,periodo,stato", [
    # decisione PM 06/10 sera («piu' aperti»): presentazioni e call dei risultati AMMESSE con l'etichetta
    ("2026-08-06-Zztest-Conference-Call-Q2-2026.pdf", "trimestrale", "2026-06-30", "da_confermare"),
    ("Zztest-2024-Q3-Transcript.pdf", "trimestrale", "2024-09-30", "da_confermare"),
    ("Zztest-Half-Year-2026-Results-Presentation.pdf", "semestrale", "2026-06-30", "da_confermare"),
    # periodo non deducibile (la sola data e' la pubblicazione): ammessa, periodo da confermare
    ("2026-07-02-Zztest-Q2-Analyst-and-Investor-Recap-call.pdf", "trimestrale", None, "da_confermare"),
])
def test_presentazioni_dei_risultati_ammesse_con_etichetta(nome, tipo, periodo, stato):
    e = _classe(nome)
    assert (e["ammesso"], e["tipo_documento"], e["tipo"], e["periodo"], e["periodo_stato"]) == (
        True, "presentazione", tipo, periodo, stato), e
    assert e["etichetta"] == esef_sito.ETICHETTA_SITO and e["via_host"] is None


@pytest.mark.parametrize("nome", [
    "Zztest-Investor-Presentation-2026-May.pdf", "2026-03-11-Zztest-Praesentation-Bilanz-Pressekonferenz.pdf",
    "Zztest-CMD-2025-Presentation.pdf",
])
def test_presentazioni_senza_periodo_dichiarate_fuori_dal_profilo(nome):
    e = _classe(nome)
    assert e["ammesso"] is False and e["motivo"].startswith("presentazione senza periodo di riferimento"), e
    assert e["etichetta"] == esef_sito.ETICHETTA_SITO  # anche gli scarti portano l'origine


def test_comunicati_dei_risultati_ammessi_con_etichetta():
    e = esef_sito.classifica_pdf(_voce("2026-03-11-Zztest-presents-Annual-Report-for-2025.pdf",
                                       base="https://ir.zztest.example/Zztest Group/Presse/News/Documents/2026/03/"))
    assert (e["ammesso"], e["tipo_documento"], e["tipo"], e["periodo"], e["periodo_stato"]) == (
        True, "comunicato_risultati", "annuale", "2025-12-31", "da_confermare"), e
    e = _classe("2026-08-06-Zztest-News-Interim-Report-H1-H1.pdf")
    assert (e["ammesso"], e["tipo_documento"], e["periodo"], e["serve_prima_pagina"]) == (
        True, "comunicato_risultati", None, True), e
    e = _classe("Zztest-Q2-2026-Earnings-Release.pdf")
    assert (e["ammesso"], e["tipo_documento"], e["tipo"], e["periodo"], e["periodo_stato"]) == (
        True, "comunicato_risultati", "trimestrale", "2026-06-30", "da_confermare"), e
    # un comunicato che non riguarda i risultati resta fuori, col motivo
    e = _classe("Zztest-Press-Release-New-Chairman.pdf")
    assert not e["ammesso"] and e["motivo"], e


@pytest.mark.parametrize("nome", ["Zztest-Sustainability-Report-2025.pdf", "Zztest-Corporate-Governance-Report-2025.pdf",
                                  "Zztest-Remuneration-Report-2025.pdf", "Zztest-ESG-Data-2025.pdf"])
def test_esg_e_governance_dichiarati_fuori_dal_profilo_finanziario(nome):
    e = _classe(nome)
    assert not e["ammesso"] and e["motivo"].startswith("non finanziario"), e


def test_data_di_pubblicazione_non_e_il_periodo():
    # «…bilancio 2024 …_23.05.2025»: il 23/05 non chiude un periodo, e' la pubblicazione
    e = _classe("QQSYN_bilanciointegrato2024_ENG_23.05.2025.pdf")
    assert (e["ammesso"], e["periodo"]) == (True, "2024-12-31")
    # solo la data di pubblicazione: periodo ignoto -> AMMESSO con periodo da confermare, serve la prima pagina
    e = _classe("2026-08-06-Zztest-Interim-Report-H1.pdf")
    assert (e["ammesso"], e["periodo"], e["periodo_stato"]) == (True, None, "da_confermare"), e
    assert "periodo di riferimento non dichiarato" in e["nota"] and e["serve_prima_pagina"]


def test_periodo_ignoto_ammesso_da_confermare():
    e = _classe("Zztest-Quarterly-Statement-2023.pdf")  # trimestre mancante: l'anno da solo non basta
    assert (e["ammesso"], e["periodo"], e["periodo_stato"], e["tipo"]) == (True, None, "da_confermare", "trimestrale")
    letta = _classe("Zztest-Quarterly-Statement-2023.pdf", prima_pagina="Zztest AG Quarterly Statement Q1 2023")
    assert (letta["ammesso"], letta["periodo"], letta["periodo_stato"]) == (True, "2023-03-31", "da_confermare")
    nulla = _classe("Zztest-Quarterly-Statement-2023.pdf", prima_pagina="Zztest AG")
    assert nulla["ammesso"] and nulla["periodo"] is None and "ne' nella prima pagina" in nulla["nota"]
    # robots.txt / sito che blocca i bot sul PDF: fermi, mai aggirati
    errore = _classe("Zztest-Quarterly-Statement-2023.pdf", prima_pagina={"errore": "SitoBloccato: sito blocca i bot (HTTP 403)"})
    assert not errore["ammesso"] and "HTTP 403" in errore["motivo"] and not errore["serve_prima_pagina"]
    vietato = _classe("Zztest-Quarterly-Statement-2023.pdf",
                      prima_pagina={"errore": "PermissionError: robots.txt vieta /files/x.pdf", "vietato": True})
    assert not vietato["ammesso"] and "robots.txt vieta" in vietato["motivo"]
    # prima pagina illeggibile per altri motivi: ammesso, periodo da confermare, nota col motivo
    rotto = _classe("Zztest-Quarterly-Statement-2023.pdf", prima_pagina={"errore": "ValueError: HTTP 500"})
    assert rotto["ammesso"] and rotto["periodo"] is None and "HTTP 500" in rotto["nota"]


def test_periodi_visti_e_stato_del_periodo():
    certo = _classe("Zztest-Half-Year-Report-2026-06-30.pdf")
    assert (certo["periodo_stato"], certo["periodi_visti"]) == ("certo", ["2026-06-30"])
    presunto = _classe("Zztest-Annual-Report-2025.pdf")
    assert (presunto["periodo_stato"], presunto["periodi_visti"]) == ("da_confermare", ["2025-12-31"])
    # nome e prima pagina discordi: le due date viste, periodo da confermare
    due = _classe("Zztest First Half 2026 results.pdf",
                  prima_pagina="Zztest Half-Year Financial Report as at 30 June 2025")
    assert due["ammesso"] and due["periodo_stato"] == "da_confermare", due
    assert set(due["periodi_visti"]) == {"2025-06-30", "2026-06-30"}, due


def test_nome_generico_deciso_dalla_prima_pagina():
    assert _classe("Zztest First Half 2026 results.pdf")["serve_prima_pagina"]
    vera = _classe("Zztest First Half 2026 results.pdf",
                   prima_pagina="Zztest S.p.A. Half-Year Financial Report as at 30 June 2026")
    assert (vera["ammesso"], vera["tipo"], vera["periodo"], vera["tipo_documento"]) == (
        True, "semestrale", "2026-06-30", "relazione")
    slide = _classe("Zztest First Half 2026 results.pdf", prima_pagina="First Half 2026 Results - Analyst presentation")
    assert (slide["ammesso"], slide["tipo_documento"], slide["tipo"]) == (True, "presentazione", "semestrale"), slide
    # nome generico senza prima pagina: ammesso come comunicato dei risultati, da confermare
    nudo = _classe("Zztest First Half 2026 results.pdf")
    assert (nudo["ammesso"], nudo["tipo_documento"], nudo["periodo_stato"]) == (True, "comunicato_risultati", "da_confermare")
    # documento senza tipo nel nome: tipo e periodo dalla copertina
    copertina = _classe("Consolidated Financial Report 2026.pdf",
                        prima_pagina="Interim Consolidated Financial Report as of June 30, 2026")
    assert (copertina["ammesso"], copertina["tipo"], copertina["periodo"]) == (True, "semestrale", "2026-06-30")


def test_scelta_con_etichetta_scarti_e_inglese_preferito():
    link = [_voce(n) for n in (
        "Zztest-Halbjahresfinanzbericht-2026.pdf", "Zztest-Interim-Report-H1-2026.pdf",
        "Zztest-Interim-Report-H1-2025.pdf", "Zztest-Investor-Presentation-2026-September.pdf",
        "Zztest-Annual-Report-2025.pdf", "Zztest-Annual-Report-2027.pdf")]
    s = esef_sito.scegli_pdf(link, oggi=OGGI)
    assert (s["tipo"], s["ultimo"]["url"], s["precedente"]["url"]) == (
        "semestrale", FILE + "Zztest-Interim-Report-H1-2026.pdf", FILE + "Zztest-Interim-Report-H1-2025.pdf")
    assert s["origine"] == "sito_emittente" and s["deposito_ufficiale"] is False
    assert s["etichetta"] == "sito dell'emittente, non archivio ufficiale (OAM)" and s["etichetta_en"]
    assert s["ultimo"]["etichetta"] == s["precedente"]["etichetta"] == s["etichetta"]
    motivi = {x["url"].rsplit("/", 1)[-1]: x["motivo"] for x in s["scartati"]}
    assert motivi["Zztest-Investor-Presentation-2026-September.pdf"].startswith("presentazione")
    assert s["scartati_totale"] == 1
    # periodo nel futuro: ammesso ma mai scelto, dichiarato nella lista dei documenti
    futuro = next(d for d in s["documenti"] if d["url"].endswith("Annual-Report-2027.pdf"))
    assert futuro["periodo_stato"] == "da_confermare" and "nel futuro" in futuro["nota"]
    assert s["ammessi_per_tipo"] == {"relazione": 5}


def test_scelta_preferisce_la_relazione_e_dichiara_il_piu_recente():
    link = [_voce(n) for n in (
        "Zztest-Half-Year-Report-2026-06-30.pdf", "Zztest-Half-Year-Report-2025-06-30.pdf",
        "Zztest-H1-2026-Results-Presentation.pdf", "Zztest-Q3-2026-Earnings-Release.pdf",
        "Zztest-Quarterly-Statement-Q3-2025.pdf")]
    s = esef_sito.scegli_pdf(link, oggi=date(2026, 11, 5))
    # il trimestre piu' recente vince anche se e' un comunicato (etichetta dichiarata); omologo dell'anno prima
    assert s["ultimo"]["url"].endswith("Q3-2026-Earnings-Release.pdf"), s
    assert s["precedente"]["url"].endswith("Quarterly-Statement-Q3-2025.pdf")
    assert s["tipo_documento"] == "comunicato_risultati" and s["periodo_stato"] == "da_confermare"
    assert s["piu_recente"]["url"].endswith("Q3-2026-Earnings-Release.pdf")
    assert s["piu_recente"]["tipo_documento"] == "comunicato_risultati" and s["piu_recente"]["periodo"] == "2026-09-30"
    assert s["ammessi_per_tipo"] == {"relazione": 3, "presentazione": 1, "comunicato_risultati": 1}
    pari = esef_sito.scegli_pdf([_voce("Zztest-H1-2026-Results-Presentation.pdf"),
                                 _voce("Zztest-Half-Year-Report-2026.pdf")], oggi=OGGI)
    assert pari["ultimo"]["tipo_documento"] == "relazione", pari


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
    assert "robots.txt vieta" in motivi[vietato]  # robots.txt resta fermo
    letta = next(d for d in trovati["documenti"] if d["url"] == slide)
    assert (letta["tipo_documento"], letta["periodo"]) == ("presentazione", "2025-06-30")  # ammessa con l'etichetta


def test_prime_pagine_al_massimo_il_limite_dichiarato(tmp_path):
    link = [(FILE + f"Zztest-Results-H1-20{a}.pdf", "") for a in range(10, 20)]
    letti = []
    esito = esef_sito.scopri("ZZTEST.MI", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(_sito(link)), prima_pagina_fn=lambda u: letti.append(u) or "")
    assert len(letti) == esef_sito.MAX_PRIME_PAGINE and letti[0].endswith("2019.pdf")  # i piu' recenti
    # nomi generici: ammessi come comunicati dei risultati, periodo da confermare (dichiarato in testa)
    assert esito["motivi"][0].startswith("comunicato dei risultati semestrale al 2019-06-30 in PDF dal sito dell'emittente")


def test_nessun_documento_ammesso_dichiarato(tmp_path):
    link = [(FILE + "Zztest-Brochure.pdf", ""), (FILE + "Zztest-Sustainability-Report-2025.pdf", "")]
    esito = esef_sito.scopri("ZZTEST.MI", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(_sito(link)))
    assert esito["motivi"][0].startswith("nessun documento periodico ammesso tra 2 PDF del sito dell'emittente")
    assert "non finanziario" in esito["motivi"][0]



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


def test_pdf_di_altro_dominio_senza_pagina_ir_scartato_col_motivo():
    # provenienza non provata (nessuna pagina del sito IR che lo linka): fuori, col motivo
    pdf = [{"url": "https://cdn.qqsyn.example/x/Zztest-Annual-Report-2025.pdf", "testo": "Annual Report 2025"},
           _voce("Zztest-Annual-Report-2024.pdf")]
    s = esef_sito.scegli_pdf(pdf, oggi=OGGI, dominio="zztest.example")
    assert s["ultimo"]["url"] == FILE + "Zztest-Annual-Report-2024.pdf"
    assert any("altro dominio (cdn.qqsyn.example)" in x["motivo"] and "pagina del sito IR" in x["motivo"]
               for x in s["scartati"])
    # anche un link http (non https) o con credenziali resta fuori
    for url in ("http://cdn.qqsyn.example/x/Zztest-Annual-Report-2025.pdf",
                "https://u:p@cdn.qqsyn.example/x/Zztest-Annual-Report-2025.pdf"):
        e = esef_sito.classifica_pdf({"url": url, "testo": "", "pagina": IR}, dominio="zztest.example")
        assert not e["ammesso"], url


@pytest.mark.parametrize("host", ["s201.q4cdn.com", "files.mziq.example", "zztest.gcs-web.com"])
def test_pdf_su_altro_host_linkato_dalla_pagina_ir_ammesso_con_l_etichetta(host):
    url = f"https://{host}/files/doc_financials/2026/q2/Zztest-Quarterly-Report-Q2-2026.pdf"
    e = esef_sito.classifica_pdf({"url": url, "testo": "Q2 2026 Quarterly Report", "pagina": IR}, dominio="zztest.example")
    assert (e["ammesso"], e["via_host"], e["tipo"], e["periodo"]) == (True, host, "trimestrale", "2026-06-30"), e
    assert e["etichetta"] == f"sito dell'emittente via {host}, non archivio ufficiale (OAM)"
    assert e["etichetta_en"] == f"issuer website via {host}, not the official archive (OAM)"
    assert e["origine"] == "sito_emittente"
    s = esef_sito.scegli_pdf([{"url": url, "testo": "Q2 2026 Quarterly Report", "pagina": IR}], oggi=OGGI,
                             dominio="zztest.example")
    assert s["ultimo"]["via_host"] == host and s["etichetta"] == e["etichetta"] and s["deposito_ufficiale"] is False


def test_scopri_ammette_i_pdf_del_cdn_e_legge_la_prima_pagina_col_robots_del_cdn(tmp_path):
    altro = "https://s7.q4cdn.example/x/Zztest-Annual-Report-2025.pdf"
    generico = "https://s7.q4cdn.example/x/Results-H1-2026.pdf"
    vietato = "https://s7.q4cdn.example/riservato/Results-H1-2025.pdf"
    pagine = _sito([(altro, "Annual Report 2025"), (generico, ""), (vietato, ""),
                    (FILE + "Zztest-Annual-Report-2024.pdf", "")],
                   extra={"https://s7.q4cdn.example/robots.txt": RispostaFinta(
                              "", b"User-agent: *\nDisallow: /riservato/\n", tipo="text/plain"),
                          generico: RispostaFinta(generico, _pdf("Zztest SE", "Half-Year Financial Report",
                                                                 "1 January to 30 June 2026"), tipo="application/pdf")})
    chiamate = []
    esito = esef_sito.scopri("ZZTEST.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_nav(pagine, chiamate))
    assert "https://s7.q4cdn.example/robots.txt" in chiamate and vietato not in chiamate
    assert "30 June 2026" in esito["prime_pagine"][generico]["testo"]
    assert esito["prime_pagine"][vietato].get("vietato") is True
    trovati = esef_sito.pdf_trovati("ZZTEST.DE", cache_dir=tmp_path, oggi=OGGI)
    via = {d["url"]: d["via_host"] for d in trovati["documenti"]}
    assert via[altro] == via[generico] == "s7.q4cdn.example" and via[FILE + "Zztest-Annual-Report-2024.pdf"] is None
    assert any(x["url"] == vietato and "robots.txt vieta" in x["motivo"] for x in trovati["scartati"])


def test_da_leggere_prima_pagina_altri_host_solo_se_linkati_dal_sito_ir():
    # revisione R-FONTI S6: e solo se l'host e' una piattaforma IR riconosciuta
    pdf = [{"url": "https://s7.q4cdn.example/x/Results-H1-2026.pdf", "testo": ""},
           {"url": "https://s7.q4cdn.example/x/Results-H1-2025.pdf", "testo": "", "pagina": IR},
           {"url": "https://cdn.qqsyn.example/x/Results-H1-2023.pdf", "testo": "", "pagina": IR},
           _voce("Zztest-Results-H1-2024.pdf")]
    assert esef_sito.da_leggere_prima_pagina(pdf, "zztest.example") == [
        "https://s7.q4cdn.example/x/Results-H1-2025.pdf", FILE + "Zztest-Results-H1-2024.pdf"]


def test_rfonti_S6_pdf_su_host_che_non_e_una_piattaforma_escluso_col_motivo():
    for host in ("cdn.qqsyn.example", "research.zzbroker.example", "d18zz.cloudfront.example"):
        e = esef_sito.classifica_pdf({"url": f"https://{host}/x/Zztest-Annual-Report-2025.pdf", "testo": "Annual report",
                                      "pagina": IR}, dominio="zztest.example")
        assert not e["ammesso"] and "piattaforma IR riconosciuta" in e["motivo"], (host, e["motivo"])
    nota = _classe("Zzsub-Equity-Research-Q2-2026-results.pdf")
    assert not nota["ammesso"] and nota["motivo"].startswith("nota di ricerca o di un broker"), nota


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
    "Zztest-Annual-Report-2025-Errata-Corrigendum.pdf", "Zztest-Half-Year-Report-2026-Auditor-Review-Report.pdf",
    "Zztest-Notice-of-Annual-Report-2025.pdf",  # avvisi senza numeri: fuori (key figures/summary: estratti, sotto)
])
def test_r8_estratti_esclusi(nome):
    e = _classe(nome)
    assert not e["ammesso"] and "non e' una relazione periodica" in e["motivo"], e


def test_r8_documento_di_un_altra_entita_escluso():
    e = _classe("Zztest-Group-Pension-Fund-Annual-Report-2025.pdf")
    assert not e["ammesso"] and e["motivo"].startswith("documento di un'altra entita'"), e


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


# ---------------------------------------------------------------- prova dal vivo 06/10 (dati inventati)

def test_rischi_pilastro_3_e_nomi_generici_senza_risultati_non_sono_comunicati():
    e = _classe("estrutura-de-gerenciamento-de-riscos-pilar3-q4-2020.pdf")
    assert not e["ammesso"] and e["motivo"].startswith("non finanziario"), e
    e = _classe("Zztest-Q4-2025-Overview.pdf")  # nome generico, nessuna parola da risultati: decide la copertina
    assert not e["ammesso"] and e["serve_prima_pagina"] and "da risultati" in e["motivo"], e
    e = _classe("Zztest-Q4-2025-Overview.pdf", prima_pagina="Zztest SE - fleet overview Q4 2025")
    assert not e["ammesso"] and "ne' da risultati" in e["motivo"], e
    e = _classe("Zztest-Q4-2025-Overview.pdf", prima_pagina="Zztest SE results for the fourth quarter 2025")
    assert e["ammesso"] and e["tipo_documento"] == "comunicato_risultati", e


def test_la_presentazione_piu_recente_non_ancora_il_profilo_ma_resta_dichiarata():
    link = [_voce(n) for n in ("2026-10-05-Zztest-Q3-Analyst-and-Investor-Recap-Document-Q3-2026.pdf",
                               "Zztest-Half-Year-Report-2026-06-30.pdf", "Zztest-Half-Year-Report-2025-06-30.pdf",
                               "2025-10-06-Zztest-Q3-Analyst-and-Investor-Recap-Document-Q3-2025.pdf")]  # anche in coppia
    s = esef_sito.scegli_pdf(link, oggi=OGGI)
    assert s["ultimo"]["url"].endswith("Half-Year-Report-2026-06-30.pdf"), s["ultimo"]
    assert s["piu_recente"]["tipo_documento"] == "presentazione" and s["piu_recente"]["periodo"] == "2026-09-30"
    solo = esef_sito.scegli_pdf(link[:1], oggi=OGGI)  # nient'altro: la presentazione, con l'etichetta
    assert solo["ultimo"]["tipo_documento"] == "presentazione"


def test_trimestre_senza_data_e_un_periodo_presunto():
    # prova dal vivo 06/10: «Q4-26-Earnings-Deck» di un esercizio che chiude a settembre. Il numero del
    # trimestre col solo anno presume l'anno solare: periodo da confermare, mai «certo»
    e = esef_sito.classifica_pdf({"url": "https://s25.q4cdn.example/1/files/doc_financials/2026/q4/Q4-26-Earnings-Deck.pdf",
                                  "testo": "", "pagina": IR}, dominio="zztest.example")
    assert e["ammesso"] and e["periodo_stato"] == "da_confermare" and "presunto" in e["base_periodo"], e
    data = _classe("Zztest-Quarterly-Statement-2026-09-30.pdf")  # data di chiusura scritta: certo
    assert data["periodo_stato"] == "certo", data


def test_a_parita_di_periodo_la_relazione_batte_il_comunicato():
    # il comunicato in inglese e per primo: senza il rango del tipo di documento vincerebbe lui
    link = [_voce("Zztest-H1-2026-Earnings-Release-EN.pdf"), _voce("Zztest-Halbjahresfinanzbericht-2026.pdf")]
    s = esef_sito.scegli_pdf(link, oggi=OGGI)
    assert s["ultimo"]["tipo_documento"] == "relazione", s["ultimo"]


@pytest.mark.parametrize("nome", ["Zztest_Q1-2026-Prepared-Remarks.pdf", "Q4-FY26-Prepared-Remarks.pdf"])
def test_testo_della_call_dei_risultati_ammesso_come_presentazione(nome):
    # prova dal vivo 06/10: lo script letto dal management nella call (nome senza «results»)
    e = _classe(nome)
    assert (e["ammesso"], e["tipo_documento"], e["tipo"], e["periodo_stato"]) == (
        True, "presentazione", "trimestrale", "da_confermare"), e


# ---------------------------------------------------------------- seguito main 06/10: sito IR scoperto

HOME = "https://www.zzbrand.example/"
IRX = "https://investors.zzgroup-ir.example/"


def _visita_ir(pagine, chiamate, tmp_path, **kw):
    return esef_sito.scopri("ZZIR", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: HOME,
                            navigatore_fn=_nav(pagine, chiamate), **kw)


def test_sito_ir_su_altro_dominio_linkato_dalla_home(tmp_path):
    chiamate = []
    pagine = {HOME: pagina((HOME + "prodotti", "Prodotti"), ("http://investors.zzgroup-ir.example/", "Investor Relations")),
              IRX: pagina((IRX + "financials/quarterly-results", "Quarterly results")),
              IRX + "financials/quarterly-results": pagina(
                  ("https://s9.q4cdn.example/f/Zztest-Q2-2026-Quarterly-Report-2026-06-30.pdf", "Quarterly Report"),
                  (IRX + "files/Zztest-Q2-2025-Quarterly-Report-2025-06-30.pdf", "Quarterly Report"))}
    esito = _visita_ir(pagine, chiamate, tmp_path)
    assert esito["sito_ir"] == IRX and esito["sito_ir_origine"].startswith("link «Investor Relations» dalla home"), esito
    # la frase in testa ai motivi vede i documenti del sito IR (filtro sui due domini gia' in scopri)
    assert esito["motivi"][0].startswith("relazione trimestrale al 2026-06-30"), esito["motivi"]
    assert "https://investors.zzgroup-ir.example/robots.txt" in chiamate  # robots del sito IR
    trovati = esef_sito.pdf_trovati("ZZIR", cache_dir=tmp_path, oggi=OGGI)
    assert trovati["ultimo"]["periodo"] == "2026-06-30" and trovati["ultimo"]["via_host"] == "s9.q4cdn.example"
    prec = trovati["precedente"]
    assert prec["etichetta"].startswith("sito IR dell'emittente zzgroup-ir.example (scoperto dal sito ") and prec["sito_ir"]
    assert trovati["sito_ir"] == IRX


def test_sito_ir_da_yfinance_in_http_portato_a_https(tmp_path):
    chiamate = []
    pagine = {HOME: pagina(), IRX: pagina((IRX + "files/Zztest-Annual-Report-2025.pdf", "Annual report"))}
    esito = _visita_ir(pagine, chiamate, tmp_path, sito_ir_fn=lambda t: "http://investors.zzgroup-ir.example/")
    assert esito["sito_ir"] == IRX and "irWebsite" in esito["sito_ir_origine"], esito
    assert not any(c.startswith("http://") for c in chiamate)


def test_candidati_standard_e_sito_di_gruppo_con_redirect(tmp_path):
    # nessun link IR in home: si provano investors./ir. del dominio e i siti di gruppo linkati dalla home;
    # «zzgroup.example/investors» rimanda a un altro dominio: si segue (robots del nuovo dominio)
    chiamate = []
    gruppo = "https://zzgroup.example/"
    finale = "https://www.zzgroup-holding.example/en/investors"
    r = RispostaFinta(gruppo + "investors", b"", status=301, location=finale)
    pagine = {HOME: pagina((gruppo + "global/en", "Zz Global")), gruppo + "investors": r,
              finale: pagina(("https://www.zzgroup-holding.example/docs/Zztest-Annual-Report-2025.pdf", "Annual report"))}
    esito = _visita_ir(pagine, chiamate, tmp_path)
    assert esito["sito_ir"] == finale, (esito.get("sito_ir"), esito["motivi"])
    assert "https://www.zzgroup-holding.example/robots.txt" in chiamate
    assert "sito di gruppo" in esito["sito_ir_origine"]
    trovati = esef_sito.pdf_trovati("ZZIR", cache_dir=tmp_path, oggi=OGGI)
    assert trovati["ultimo"]["periodo"] == "2025-12-31"


def test_sito_ir_che_blocca_i_bot_dichiarato(tmp_path):
    chiamate = []
    pagine = {HOME: pagina((IRX, "Investors")), IRX + "robots.txt": RispostaFinta("", b"no", status=403)}
    esito = _visita_ir(pagine, chiamate, tmp_path)
    assert IRX not in chiamate and esito.get("sito_ir") is None
    assert any("investors.zzgroup-ir.example" in m and "HTTP 403" in m for m in esito["motivi"]), esito["motivi"]


def test_nessuna_scoperta_se_l_ir_e_gia_nel_sito_con_documenti(tmp_path):
    chiamate = []
    pagine = _sito([(FILE + "Zztest-Annual-Report-2025.pdf", "Annual Report 2025")])
    esef_sito.scopri("ZZTEST.DE", oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO, navigatore_fn=_nav(pagine, chiamate))
    assert not any(c.startswith(("https://investors.zztest.example/", "https://investor.zztest.example/"))
                   or c in (SITO + "investors", SITO + "en/investors") for c in chiamate), chiamate


@pytest.mark.parametrize("nome,tipo", [("Zztest-Annual-Report-2025-Key-Figures.pdf", "annuale"),
                                       ("Zztest-Annual-Report-2025-Summary.pdf", "annuale"),
                                       ("Zztest-Q2-2026-Factsheet.pdf", "trimestrale"),
                                       ("Zztest-Databook-Q1-2026.pdf", "trimestrale")])
def test_estratti_finanziari_ammessi_come_estratto(nome, tipo):
    e = _classe(nome)
    assert (e["ammesso"], e["tipo_documento"], e["tipo"]) == (True, "estratto", tipo), e


def test_estratto_non_ancora_il_profilo_se_c_e_la_relazione():
    s = esef_sito.scegli_pdf([_voce("Zztest-Key-Figures-H1-2026-06-30.pdf"), _voce("Zztest-Half-Year-Report-2026-06-30.pdf")],
                             oggi=OGGI)
    assert s["ultimo"]["tipo_documento"] == "relazione" and s["ammessi_per_tipo"] == {"estratto": 1, "relazione": 1}


def test_sito_di_gruppo_provato_prima_dei_candidati_standard_e_dns_muto_non_e_un_motivo():
    visita = {"pagine": [HOME], "link": [{"url": "https://zzgroup.example/global/en", "testo": "Zz Global", "pagina": HOME}]}
    cand = esef_sito._candidati_ir(HOME, visita, None, False)
    assert cand[0] == ("https://zzgroup.example/investors", "sito di gruppo zzgroup.example linkato dalla home"), cand
    assert ("https://investors.zzbrand.example/", "candidato standard") in cand
    assert len(cand) <= esef_sito.MAX_CANDIDATI_IR

    import socket

    def get(url, **kw):
        raise socket.gaierror(11001, "getaddrinfo failed")
    ir = esef_sito.scopri_sito_ir(HOME, visita, navigatore_fn=lambda d: esef_sito.Navigatore(d, get=get, dormi=lambda s: None))
    assert ir["visita"] is None and not any("robots" in m for m in ir["motivi"]), ir["motivi"]
    assert ir["motivi"][-1].startswith("sito IR non trovato oltre il sito della societa'"), ir["motivi"]


@pytest.mark.parametrize("nome,etichetta", [("Q4-FY26-Prepared-Remarks.pdf", "Q4 FY2026"),
                                            ("Micron_FY25_Q2_Prepared_Remarks_2-1.pdf", "Q2 FY2025"),
                                            ("Zztest-FY2026-Q3-Earnings-Deck.pdf", "Q3 FY2026")])
def test_trimestre_fiscale_dichiarato_senza_inventare_la_data(nome, etichetta):
    e = _classe(nome)
    assert e["ammesso"] and e["periodo"] is None and e["periodo_fiscale"] == etichetta, e
    s = esef_sito.scegli_pdf([_voce(nome), _voce("Q2-2026-Prepared-Remarks.pdf")], oggi=OGGI)
    assert s["piu_recente_fiscale"]["periodo_fiscale"] == etichetta


def test_estratto_piu_recente_non_ancora_il_profilo():
    s = esef_sito.scegli_pdf([_voce("Zztest-Key-Figures-Q3-2026-09-30.pdf"), _voce("Zztest-Half-Year-Report-2026-06-30.pdf")],
                             oggi=OGGI)
    assert s["ultimo"]["tipo_documento"] == "relazione" and s["piu_recente"]["tipo_documento"] == "estratto", s["ultimo"]
