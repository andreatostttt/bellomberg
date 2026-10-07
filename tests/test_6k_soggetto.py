"""Regola standard 6-K: il SOGGETTO delle frasi sul periodo nel corpo (revisione R-SITI2 E8 e F1).

Regola PM: mai documenti di altre societa' attestati come del titolo, ma i documenti veri del registrante non
si scartano. Sotto il CIK del titolo la copertina porta sempre il registrante; il corpo puo' essere di un partner
o di una controllata. Regola (impianto 07/10, dichiarata in ``_altro_soggetto``):
- ogni frase sul periodo («… quarter/months/period ended …») nella TESTA del corpo conta; basta UNA frase con
  un'altra entita' nominata come soggetto (attivo, passivo «by X», possessivo «<Registrante>'s partner X»,
  soggetto composto «<Registrante> and X», nome esteso «<Marchio> Payments») e il 6-K e' «non_verificato»;
- una frase senza un'altra entita' soggetto («As of and for the three months ended …», «In the second quarter
  ended …, we …», «the Company», «Our revenue in Brazil …») vale per il registrante.
Rete assente, nessuna AI, ticker e numeri sintetici (ZZ*).
"""
import pytest

CIK = "0009990077"
BASE_URL = "https://www.sec.gov/Archives/edgar/data/9990077/"
PROFILO = {"ticker": "ZZTEST", "emittente_id": "CIK:" + CIK, "cik": CIK, "nome": "Zztest Holdings",
           "lingua": "en", "tipo": "trimestrale", "perimetro": "consolidato", "fonti": ["sec"],
           "forme_sec": ["6-K"], "sezioni": {}, "sezioni_intero": True, "periodo_regola": "piu_recente",
           "stesso_periodo": "piu_lungo", "origine_collegamento": "confermato_utente",
           "verifica": {"lingua": r"\b(?:the|and|of)\b", "perimetro": "consolidated",
                        "tipo": r"months\s+ended|half[-\s]year", "emittente": r"\bzztest\s+holdings\b",
                        "periodo": r"(?P<mesi>three|3)(?:\s+and\s+(?:six|nine))?\s+months\s+ended\s+"
                                   r"(?P<fine>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})"}}
# identita' dal CIK del percorso: il nome del profilo NON compare nel testo, valgono registrante e marchio
PROFILO_CIK = {**PROFILO, "verifica": {**PROFILO["verifica"], "emittente": r"\bzzassente\s+nome\b"}}
REGISTRANTE = "Zztest Holdings Ltd."


def _html(corpo, registrante=REGISTRANTE, coda=""):
    return ('<html lang="en"><body><p>FORM 6-K</p><p>For the month of August, 2025</p>'
            f'<p>{registrante}</p><p>(Exact name of registrant as specified in its charter)</p>'
            '<p>Indicate by check mark whether the registrant files annual reports under cover of Form 20-F or '
            f'Form 40-F.</p>{corpo}<p>Revenue 333.33 million; net income 44.44 million.</p>'
            f'<p>All figures are consolidated.</p>{coda}<p>Boilerplate.</p></body></html>').encode()


def _verifica(tmp_path, html, profilo=PROFILO, n=1):
    from bellomberg.market_data.filing_verifica import verifica_6k_standard
    path = tmp_path / f"s{n}.htm"
    path.write_bytes(html)
    return verifica_6k_standard(path, url=BASE_URL + f"00099900772900{n:04d}/s{n}.htm", profilo=profilo,
                                catalogo={"form": "6-K"})


def _altro(esito):
    return [m for m in esito.get("motivi", []) if "frase del periodo" in m]


def _sonda(corpo):
    """La pagina ESATTA delle sonde R-SITI2 bis (copertina senza «Indicate by check mark»)."""
    return ('<html lang="en"><body><p>FORM 6-K</p><p>For the month of August, 2025</p><p>Zztest Holdings Ltd.</p>'
            '<p>(Exact name of registrant as specified in its charter)</p>' + corpo +
            '<p>Revenue 333.33 million; net income 44.44 million.</p><p>Boilerplate.</p></body></html>').encode()


# ------------------------------------------------------------------ sonde R-SITI2 bis rese test
def test_E8_sonda_possessivo_del_registrante_il_soggetto_e_il_partner(tmp_path):
    esito = _verifica(tmp_path, _sonda(
        "<p>Zzpartner Reports Second Quarter Results</p><p>Zztest Holdings' payments partner Zzpartner "
        "today released its consolidated financial results for the second quarter ended June 30, 2025.</p>"))
    assert esito["stato"] == "non_verificato", esito
    assert any("Zzpartner" in m for m in _altro(esito)), esito


@pytest.mark.parametrize("corpo", [
    "<p>Zztest Holdings Ltd.</p><p>Unaudited Interim Condensed Consolidated Financial Statements</p>"
    "<p>As of and for the three months ended June 30, 2025</p>",
    "<p>Zztest Holdings Reports Second Quarter 2025 Results</p><p>In the second quarter ended June 30, 2025, Zztest "
    "Holdings grew its consolidated revenue.</p>"])
def test_F1_sonda_6k_vero_del_registrante_verificabile(tmp_path, corpo):
    esito = _verifica(tmp_path, _sonda(corpo))
    assert esito["stato"] in ("ok", "periodo_da_confermare"), esito
    assert not _altro(esito), esito


# ------------------------------------------------------------------ un'altra entita' soggetto: non_verificato
@pytest.mark.parametrize("frase,nome", [
    # partner nominato soggetto, senza possessivo
    ("Zzpartner today released its consolidated financial results for the second quarter ended June 30, 2025.",
     "Zzpartner"),
    # avverbio d'apertura prima del soggetto
    ("Today, Zzpartner released its consolidated financial results for the second quarter ended June 30, 2025.",
     "Zzpartner"),
    # controllata nominata soggetto (senza forma giuridica: la regola della copertina non la vede)
    ("Zztest Pagamentos, a subsidiary of Zztest Holdings, today released its consolidated financial results for "
     "the second quarter ended June 30, 2025.", "Zztest Pagamentos"),
    # frase passiva: l'agente e' il soggetto vero
    ("The consolidated financial results for the second quarter ended June 30, 2025 were released today by "
     "Zzpartner.", "Zzpartner"),
    # frase introduttiva sul periodo, poi il soggetto
    ("In the second quarter ended June 30, 2025, Zzpartner grew its consolidated revenue.", "Zzpartner"),
    # soggetto composto: registrante E un'altra entita'
    ("Zztest Holdings and Zzpartner today released their combined financial results for the second quarter "
     "ended June 30, 2025.", "Zzpartner"),
    # possessivo in prima persona: «il nostro partner X»
    ("Our payments partner Zzpartner today released its consolidated financial results for the second quarter "
     "ended June 30, 2025.", "Zzpartner"),
    # complemento di specificazione del soggetto
    ("The consolidated financial statements of Zzpartner for the three months ended June 30, 2025 are attached.",
     "Zzpartner"),
])
def test_altra_entita_soggetto_della_frase_sul_periodo_non_verificato(tmp_path, frase, nome):
    esito = _verifica(tmp_path, _html(f"<p>Zztest Holdings - Exhibit 99.1</p><p>{frase}</p>"))
    assert esito["stato"] == "non_verificato", esito
    assert any(nome.split()[0] in m for m in _altro(esito)), esito


def test_controllata_col_marchio_del_registrante_non_e_il_registrante(tmp_path):
    # identita' dal CIK: il marchio «Zztest» e' un alias, ma «Zztest Payments» e' un'altra entita'
    esito = _verifica(tmp_path, _html("<p>Zztest Holdings - Exhibit 99.1</p><p>Zztest Payments today released its consolidated financial results for "
                                      "the second quarter ended June 30, 2025.</p>"), PROFILO_CIK)
    assert esito["stato"] == "non_verificato", esito
    assert any("Zztest Payments" in m for m in _altro(esito)), esito


def test_controllo_marchio_del_registrante_soggetto_resta_verificabile(tmp_path):
    esito = _verifica(tmp_path, _html("<p>Zztest today released its consolidated financial results for the second "
                                      "quarter ended June 30, 2025.</p>"), PROFILO_CIK)
    assert esito["stato"] in ("ok", "periodo_da_confermare") and not _altro(esito), esito


@pytest.mark.parametrize("ordine", ["registrante_prima", "partner_prima"])
def test_piu_frasi_sul_periodo_basta_una_con_un_altro_soggetto(tmp_path, ordine):
    # regola prudente dichiarata: nella testa del corpo vince il soggetto ESTRANEO, in qualunque ordine
    reg = ("<p>Zztest Holdings today released its consolidated financial results for the second quarter ended "
           "June 30, 2025.</p>")
    par = ("<p>Zzpartner, its payments partner, also released its results for the quarter ended June 30, "
           "2025.</p>")
    esito = _verifica(tmp_path, _html(reg + par if ordine == "registrante_prima" else par + reg))
    assert esito["stato"] == "non_verificato", esito
    assert any("Zzpartner" in m for m in _altro(esito)), esito


# ------------------------------------------------------------------ registrante (o nessun altro): verificabile
@pytest.mark.parametrize("frase", [
    "For the three months ended June 30, 2025, the Company grew its consolidated revenue in Brazil.",
    "In the second quarter ended June 30, 2025, we grew consolidated revenue in Brazil and Mexico.",
    "During the three months ended June 30, 2025, the Group recorded consolidated revenue growth.",
    # possessivo del registrante sui SUOI numeri
    "Zztest Holdings' consolidated revenue for the second quarter ended June 30, 2025 rose 12%.",
    # possessivo in prima persona sui propri numeri: il luogo dopo «in» non e' nel sintagma del soggetto
    "Our consolidated revenue in Brazil for the second quarter ended June 30, 2025 rose 12%.",
    # un luogo nominato non e' il soggetto
    "Consolidated revenue in Brazil for the second quarter ended June 30, 2025 rose 12%.",
    # citazione del management: «We …», poi il nome di una persona dopo «said»
    "“We delivered strong growth in the second quarter ended June 30, 2025,” said Jane Zzdoe, Chief "
    "Executive Officer.",
    # passiva col registrante agente
    "The consolidated financial results for the second quarter ended June 30, 2025 were released today by "
    "Zztest Holdings.",
    # forma giuridica abbreviata e sigla di borsa dopo il nome
    "Zztest Holdings Corp. (NYSE: ZZT) today released its consolidated results for the second quarter ended "
    "June 30, 2025.",
])
def test_registrante_o_nessun_altro_soggetto_verificabile(tmp_path, frase):
    esito = _verifica(tmp_path, _html(f"<p>Zztest Holdings - Exhibit 99.1</p><p>{frase}</p>"))
    assert esito["stato"] in ("ok", "periodo_da_confermare"), esito
    assert not _altro(esito), esito


def test_frasi_oltre_la_testa_del_corpo_non_contano(tmp_path):
    # le note al bilancio (oltre la testa) citano controllate soggetto: non sono il comunicato
    lead = ("<p>Zztest Holdings today released its consolidated financial results for the second quarter ended "
            "June 30, 2025.</p>")
    note = "".join(f"<p>Note {i}. Accounting policies are unchanged from the annual report.</p>" for i in range(150))
    nota = "<p>Zzsub Ltda. was acquired by the Group during the six months ended June 30, 2025.</p>"
    esito = _verifica(tmp_path, _html(lead, coda=note + nota))
    assert esito["stato"] in ("ok", "periodo_da_confermare") and not _altro(esito), esito


# ------------------------------------------------------------------ unita': la frase e il soggetto
def test_unita_frasi_del_periodo_e_altro_soggetto():
    from bellomberg.market_data import filing_verifica as fv
    alias = [{"nome": "Zztest Holdings"}]
    corpo = ("Zztest Holdings today released results for the quarter ended June 30, 2025. Zzpartner also released "
             "results for the quarter ended June 30, 2025.\n")
    frasi = fv._frasi_del_periodo(corpo)
    assert len(frasi) == 2 and frasi[1].startswith("Zzpartner"), frasi
    assert fv._altro_soggetto(frasi[0], alias) is None
    assert fv._altro_soggetto(frasi[1], alias) == "Zzpartner"
    # la vecchia interfaccia resta: la PRIMA frase sul periodo
    assert fv._frase_del_periodo(corpo).startswith("Zztest Holdings today")


# ------------------------------------------------------------------ fonte esterna: byte riscontrati all'attivazione
URL_CDN = "https://d1zz.cloudfront.net/r/zztest-q2.htm"
CORPO_RISCONTRO = ("<p>Zztest Holdings - Exhibit 99.1</p><p>Zztest Holdings today released its consolidated results "
                   "for the three months ended June 30, 2025.</p>")


def _esterno(tmp_path, riscontrati, html=None):
    import hashlib
    from bellomberg.market_data.filing_verifica import verifica_documento
    path = tmp_path / "cdn.htm"
    path.write_bytes(html or _html(CORPO_RISCONTRO))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    profilo = {**PROFILO, "documenti_esterni": {URL_CDN: "d1zz.cloudfront.net"},
               "documenti_riscontrati": riscontrati(sha)}
    return verifica_documento(path, url=URL_CDN, profilo=profilo)


def test_esterno_con_sha_riscontrato_uguale_verificato_con_etichetta(tmp_path):
    esito = _esterno(tmp_path, lambda sha: {URL_CDN: {"sha256": sha, "etichetta": "riscontrato a mano 07/10"}})
    assert esito["stato"] == "ok", esito
    assert esito["etichetta"] == "riscontrato a mano 07/10"
    assert esito["documento"]["riscontro_esterno"]["host"] == "d1zz.cloudfront.net"


def test_esterno_con_sha_diverso_torna_fonte_esterna(tmp_path):
    esito = _esterno(tmp_path, lambda sha: {URL_CDN: {"sha256": "0" * 64, "etichetta": "vecchio PDF"}})
    assert esito["stato"] == "non_verificato" and "etichetta" not in esito, esito
    assert any("fonte esterna" in m and "byte diversi" in m for m in esito["motivi"]), esito


@pytest.mark.parametrize("riscontrati", [lambda sha: {}, lambda sha: None, lambda sha: {URL_CDN: {"etichetta": "x"}},
                                         lambda sha: {"https://altro.example/x.htm": {"sha256": sha}}])
def test_esterno_senza_riscontro_resta_non_verificato(tmp_path, riscontrati):
    esito = _esterno(tmp_path, riscontrati)
    assert esito["stato"] == "non_verificato", esito
    assert any("fonte esterna" in m for m in esito["motivi"]), esito


def test_esterno_riscontrato_non_salta_le_altre_prove(tmp_path):
    # i byte riscontrati tolgono solo il blocco «fonte esterna»: periodo e identita' si provano comunque
    html = _html("<p>Zztest Holdings - Exhibit 99.1</p><p>No period here.</p>")
    esito = _esterno(tmp_path, lambda sha: {URL_CDN: {"sha256": sha, "etichetta": "x"}}, html)
    assert esito["stato"] == "non_verificato" and not any("fonte esterna" in m for m in esito["motivi"]), esito


# ------------------------------------------------------------------ citazioni: una persona non e' un'entita'
@pytest.mark.parametrize("frase", [
    "John Zzsmith, Chief Executive Officer, said the second quarter ended June 30, 2025 was a record for revenue.",
    "Jane Zzdoe said that consolidated revenue for the second quarter ended June 30, 2025 grew 12%.",
    "Maria Zzrossi, CFO, commented on the three months ended June 30, 2025: revenue grew 12%."])
def test_persona_che_dichiara_non_e_un_altra_entita(tmp_path, frase):
    esito = _verifica(tmp_path, _html(f"<p>Zztest Holdings - Exhibit 99.1</p><p>{frase}</p>"))
    assert esito["stato"] in ("ok", "periodo_da_confermare") and not _altro(esito), esito


@pytest.mark.parametrize("frase,nome", [
    ("Zzpartner said its revenue for the second quarter ended June 30, 2025 grew 12%.", "Zzpartner"),
    ("Zzpartner Payments, the CEO said, released results for the second quarter ended June 30, 2025.", "Zzpartner")])
def test_partner_che_dichiara_resta_un_altra_entita(tmp_path, frase, nome):
    esito = _verifica(tmp_path, _html(f"<p>Zztest Holdings - Exhibit 99.1</p><p>{frase}</p>"))
    assert esito["stato"] == "non_verificato" and any(nome in m for m in _altro(esito)), esito
