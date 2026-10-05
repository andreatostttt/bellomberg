# -*- coding: utf-8 -*-
"""borsa_italiana.py (voce 6, 04/10/2026, Opus 5.5): eventi societari dei titoli italiani.

Nessuna rete: `_scarica` e' sostituito da un finto server che serve le fixture SINTETICHE di
tests/fixtures/fonti_it/ (nomi e numeri inventati, struttura come la pagina misurata il
04/10). Il cablaggio di `_scarica` (guardia robots PRIMA della rete) si prova a parte con
`requests.get` che esplode se viene chiamato. Cache e negozio vanno in tmp_path.
"""
import json
import os
from datetime import date

import pytest

from bellomberg.market_data import borsa_italiana as bi

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fonti_it")
ISIN_ACME = "ITZZACME0007"     # inventato, cifra di controllo valida
ISIN_TEST = "ITZZTEST0001"


def _fixture(nome):
    with open(os.path.join(FIX, nome), "rb") as fh:
        return fh.read()


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    """Negozio e cache in tmp_path, oggi fisso, server finto. Torna il dizionario delle
    risposte {url: (http, corpo) | Exception} e la lista delle richieste fatte."""
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": 4242},
                                   "ZZTEST.MI": {"isin": ISIN_TEST, "emarket": None}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(negozio.parent / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 4))
    risposte, chieste = {}, []

    def finto(url):
        ok, motivo = bi.url_consentito(url)
        assert ok, motivo
        chieste.append(url)
        r = risposte.get(url)
        if r is None:
            raise ConnectionError("nessuna risposta finta per %s" % url)
        if isinstance(r, Exception):
            raise r
        return r[0], r[1], url
    monkeypatch.setattr(bi, "_scarica", finto)
    return {"risposte": risposte, "chieste": chieste, "negozio": negozio, "tmp": tmp_path}


def _url(isin):
    return bi.URL_EVENTI.format(isin=isin)


# ------------------------------------------------------------ A. date italiane
@pytest.mark.parametrize("testo,atteso", [
    ("12/11/26", "2026-11-12"),
    ("04/09/2026", "2026-09-04"),
    ("1/2/27", "2027-02-01"),
    (" 30/07/26 ", "2026-07-30"),
    ("29/02/28", "2028-02-29"),
])
def test_data_it_legge_due_e_quattro_cifre(testo, atteso):
    assert bi.data_it(testo) == atteso


@pytest.mark.parametrize("testo", ["31/02/26", "12/13/26", "12/11/026", "12-11-26", "n.d.", "",
                                   "12/11/26 extra", "29/02/27", None, 20261112])
def test_data_it_rifiuta_le_non_date(testo):
    assert bi.data_it(testo) is None


# ------------------------------------------------------------ B. ISIN e negozio
def test_isin_cifra_di_controllo():
    assert bi.isin_valido(ISIN_ACME) and bi.isin_valido(ISIN_TEST)
    assert not bi.isin_valido("ITZZACME0008")      # cifra di controllo sbagliata
    assert not bi.isin_valido("itzzacme0007")      # minuscolo
    assert not bi.isin_valido("ITZZACME007")       # 11 caratteri


def test_esempio_tracciato_si_carica_ed_e_inventato():
    p = os.path.join(os.path.dirname(FIX), "..", "..", "src", "bellomberg", "resources",
                     "examples", bi.ESEMPIO_ISIN)
    r = bi.carica_isin_it(os.path.normpath(p))
    assert r["motivo"] is None, r["motivo"]
    assert r["voci"]["ACME.MI"] == {"isin": ISIN_ACME, "emarket": 4242}
    assert r["voci"]["ZZTEST.MI"]["emarket"] is None
    assert all(v["isin"].startswith("ITZZ") for v in r["voci"].values())


@pytest.mark.parametrize("voci,parola", [
    ({"ACME.MI": {"isin": "ITZZACME0008", "emarket": 1}}, "ISIN"),
    ({"ACME.MI": {"isin": ISIN_ACME}}, "emarket"),
    ({"ACME.MI": {"isin": ISIN_ACME, "emarket": "4242"}}, "intero"),
    ({"ACME.MI": {"isin": ISIN_ACME, "emarket": 1, "mic": "MTAA"}}, "sconosciuto"),
    ({"acme.mi": {"isin": ISIN_ACME, "emarket": 1}}, "MAIUSCOLA"),
])
def test_voce_malformata_rende_illeggibile_il_negozio(tmp_path, voci, parola):
    p = tmp_path / "n.json"
    p.write_text(json.dumps(voci), encoding="utf-8")
    r = bi.carica_isin_it(str(p))
    assert r["origine"] == "illeggibile" and parola in r["motivo"], r["motivo"]


# ------------------------------------------------------------ C. ISIN mancante = KO dichiarato
def test_negozio_assente_KO_dichiarato_senza_rete(ambiente, monkeypatch):
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(ambiente["tmp"] / "non_esiste.json"))
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "negozio_assente"
    assert "ISIN mancante" in r["motivo"] and bi.ESEMPIO_ISIN in r["motivo"]
    assert r["eventi"] == [] and ambiente["chieste"] == []


def test_ticker_non_mappato_KO_dichiarato_senza_rete(ambiente):
    r = bi.get_eventi_societari("NONCE.MI")
    assert r["stato"] == "KO" and r["errore"] == "ticker_non_mappato"
    assert "NONCE.MI" in r["motivo"] and ambiente["chieste"] == []


def test_isin_esplicito_malformato_KO(ambiente):
    r = bi.get_eventi_societari("NONCE.MI", isin="ITZZACME0008")
    assert r["stato"] == "KO" and r["errore"] == "isin_malformato" and ambiente["chieste"] == []


def test_isin_esplicito_diverso_dal_negozio_KO(ambiente):
    """Review RV-C: un ISIN di un altro titolo usciva firmato col ticker del book."""
    r = bi.get_eventi_societari("ACME.MI", isin=ISIN_TEST)
    assert r["stato"] == "KO" and r["errore"] == "isin_incoerente" and r["eventi"] == []
    assert ISIN_TEST in r["motivo"] and ISIN_ACME in r["motivo"] and ambiente["chieste"] == []


def test_isin_esplicito_uguale_al_negozio_si_legge(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_vuoto.html"))
    r = bi.get_eventi_societari("ACME.MI", isin=ISIN_ACME)
    assert r["stato"] == "vuoto_misurato" and not any("ISIN non verificato dal negozio" in l for l in r["limiti"])


def test_isin_esplicito_senza_voce_dichiara_il_limite(ambiente):
    ambiente["risposte"][_url(ISIN_TEST)] = (200, _fixture("bi_eventi_vuoto.html"))
    r = bi.get_eventi_societari("NONCE.MI", isin=ISIN_TEST)
    assert r["isin"] == ISIN_TEST and ambiente["chieste"] == [_url(ISIN_TEST)]
    assert any("ISIN non verificato dal negozio" in l for l in r["limiti"])


def test_righe_con_celle_sbagliate_contate_e_dichiarate():
    html = _fixture("bi_eventi_ok.html").decode("utf-8").replace(
        "</table>", "<tr><td>Relazione</td><td>Strana</td><td>01/01/27</td></tr>\n</table>")
    r = bi.parse_eventi(html)
    assert r["stato"] == "ok" and r["righe_scartate"] == 1 and "1 righe scartate" in r["motivo"]


# ------------------------------------------------------------ D. pagina -> eventi
def test_eventi_ok_dedup_date_e_prossimo(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_ok.html"))
    r = bi.get_eventi_societari("acme.mi")
    assert r["stato"] == "ok", r["motivo"]
    assert r["ticker"] == "ACME.MI" and r["isin"] == ISIN_ACME and r["fonte"] == "Borsa Italiana"
    assert r["url"] == _url(ISIN_ACME) and r["letto_il"]
    assert r["duplicati_rimossi"] == 1
    descr = [(e["descrizione"], e["data"]) for e in r["eventi"]]
    assert descr == [("Resoconto Intermedio di Gestione", "2026-11-14"),
                     ("Presentazione Analisti", "2026-11-14"),
                     ("Stacco Dividendo", "2026-05-20"),
                     ("Assemblea Bilancio", "2026-04-28"),
                     ("Evento Senza Nome Noto", None),
                     ("Cda Bilancio", "2026-03-17")]
    tipi = [e["tipo"] for e in r["eventi"]]
    assert tipi == ["risultati", "presentazione_analisti", "dividendo", "assemblea", "altro", "risultati"]
    # la data illeggibile NON sparisce: resta con data None, contata e dichiarata
    assert r["date_illeggibili"] == 1 and "data illeggibile" in r["motivo"]
    assert r["eventi"][4]["data_grezza"] == "n.d."
    assert r["prossimo"]["data"] == "2026-11-14"
    assert any("page=" in l for l in r["limiti"])


def test_tabella_presente_e_vuota_e_vuoto_misurato(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_vuoto.html"))
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "vuoto_misurato" and r["errore"] is None and r["eventi"] == []
    assert r["letto_il"] and r["prossimo"] is None


def test_tabella_assente_e_KO_non_vuoto(ambiente):
    """L'ISIN che Borsa non conosce da' un'ALTRA pagina con un'altra tabella: KO, mai «nessun evento»."""
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_altra_pagina.html"))
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "tabella_assente" and r["letto_il"] is None


def test_layout_cambiato_KO(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_layout.html"))
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "layout_cambiato"


def test_tutte_le_date_illeggibili_KO():
    html = _fixture("bi_eventi_ok.html").decode("utf-8")
    for d in ("14/11/26", "20/05/26", "28/04/2026", "17/03/26"):
        html = html.replace(d, "99/99/99")
    r = bi.parse_eventi(html)
    assert r["stato"] == "KO" and r["errore"] == "date_illeggibili"


@pytest.mark.parametrize("risposta,errore", [((500, b"errore"), "http"), (TimeoutError("x"), "rete")])
def test_guasti_della_fonte_KO(ambiente, risposta, errore):
    ambiente["risposte"][_url(ISIN_ACME)] = risposta
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == errore and r["eventi"] == []
    assert r["cache"]["stato"] == "nessuna"


# ------------------------------------------------------------ E. cache, TTL, STALE
def test_cache_fresca_non_rilegge(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_ok.html"))
    a = bi.get_eventi_societari("ACME.MI")
    b = bi.get_eventi_societari("ACME.MI")
    assert len(ambiente["chieste"]) == 1
    assert a["cache"]["stato"] == "nessuna" and b["cache"]["stato"] == "fresca"
    assert b["eventi"] == a["eventi"] and b["stato"] == "ok"


def _invecchia(tmp, secondi):
    cartella = tmp / "cache"
    for n in os.listdir(cartella):
        p = cartella / n
        c = json.loads(p.read_text(encoding="utf-8"))
        c["salvato_ts"] -= secondi
        p.write_text(json.dumps(c), encoding="utf-8")


def test_cache_scaduta_e_fonte_in_guasto_STALE_dichiarato(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_ok.html"))
    buono = bi.get_eventi_societari("ACME.MI")
    _invecchia(ambiente["tmp"], bi.TTL_EVENTI_S + 60)
    ambiente["risposte"][_url(ISIN_ACME)] = (503, b"")
    r = bi.get_eventi_societari("ACME.MI")
    assert len(ambiente["chieste"]) == 2
    assert r["stato"] == "STALE" and r["stato_originale"] == "ok" and r["errore"] == "http"
    assert "HTTP 503" in r["motivo"] and buono["letto_il"] in r["motivo"]
    assert r["letto_il"] == buono["letto_il"] and r["eventi"] == buono["eventi"]
    assert r["cache"]["stato"] == "scaduta_servita" and r["cache"]["eta_s"] >= bi.TTL_EVENTI_S


def test_cache_scaduta_e_fonte_viva_rilegge(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_ok.html"))
    bi.get_eventi_societari("ACME.MI")
    _invecchia(ambiente["tmp"], bi.TTL_EVENTI_S + 60)
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_vuoto.html"))
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "vuoto_misurato" and r["cache"]["stato"] == "nessuna"


def test_orologio_indietro_non_e_cache_fresca(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_ok.html"))
    bi.get_eventi_societari("ACME.MI")
    _invecchia(ambiente["tmp"], -3600)      # salvato «nel futuro»: eta' negativa
    ambiente["risposte"][_url(ISIN_ACME)] = (503, b"")
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "STALE" and "non misurabile" in r["motivo"]


def test_KO_non_entra_in_cache(ambiente):
    ambiente["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_altra_pagina.html"))
    bi.get_eventi_societari("ACME.MI")
    assert not os.path.isdir(ambiente["tmp"] / "cache") or not os.listdir(ambiente["tmp"] / "cache")


# ------------------------------------------------------------ F. robots e cablaggio di _scarica
@pytest.mark.parametrize("url", [
    "https://www.borsaitaliana.it/borsa/azioni/elenco-completo-eventi.html?isin=%s&lang=it&page=2" % ISIN_ACME,
    "https://www.borsaitaliana.it/borsa/quotazioni/azioni/elenco-completo-internal-dealing.html?isin=X&page=1",
    "https://www.borsaitaliana.it/borsa/documenti/dealing.htm?filename=abc.pdf",
    "https://www.borsaitaliana.it/borsa/azioni/elenco-completo-eventi.html?isin=X&ord=data",
    "https://www.emarketstorage.it/search/node?keys=x",
    "http://www.borsaitaliana.it/borsa/azioni/elenco-completo-eventi.html?isin=X",
    "https://www.esempio-non-previsto.it/x",
])
def test_url_vietati(url):
    assert bi.url_consentito(url)[0] is False


def test_url_costruiti_dal_modulo_consentiti():
    assert bi.url_consentito(_url(ISIN_ACME)) == (True, "")
    assert bi.url_consentito("https://www.emarketstorage.it/it/comunicati-finanziari?categoria=110&azienda=4242&page=1")[0]
    assert bi.url_consentito("https://www.emarketstorage.it/sites/default/files/comunicati/2026-09/sint_1.pdf")[0]


def test_scarica_rifiuta_PRIMA_della_rete(monkeypatch):
    import requests

    def esplode(*a, **k):
        raise AssertionError("requests.get chiamato su un URL vietato")
    monkeypatch.setattr(requests, "get", esplode)
    with pytest.raises(bi.URLVietato):
        bi._scarica(_url(ISIN_ACME) + "&page=2")


class _Risposta:
    def __init__(self, status, content=b"", location=None):
        self.status_code, self.content = status, content
        self.headers = {"Location": location} if location else {}


def test_scarica_redirect_vietato_non_viene_MAI_richiesto(monkeypatch):
    """Review RV-C: col redirect automatico l'URL vietato partiva e il controllo arrivava dopo.
    Ora i salti si seguono a mano: il vietato si rifiuta PRIMA di chiederlo."""
    import requests
    chiamate = []
    monkeypatch.setattr(requests, "get", lambda url, **k: chiamate.append((url, k)) or
                        _Risposta(302, location="/borsa/documenti/x.htm?filename=y.pdf"))
    with pytest.raises(bi.URLVietato, match="redirect"):
        bi._scarica(_url(ISIN_ACME))
    assert [u for u, _ in chiamate] == [_url(ISIN_ACME)]
    k = chiamate[0][1]
    assert k["allow_redirects"] is False
    assert k["headers"]["User-Agent"] == bi.USER_AGENT and k["timeout"] == bi.TIMEOUT_S


def test_scarica_segue_il_redirect_consentito(monkeypatch):
    import requests
    finale = _url(ISIN_ACME).replace("lang=it", "lang=en")
    risposte = {_url(ISIN_ACME): _Risposta(301, location=finale), finale: _Risposta(200, b"x")}
    chiamate = []
    monkeypatch.setattr(requests, "get", lambda url, **k: chiamate.append(url) or risposte[url])
    assert bi._scarica(_url(ISIN_ACME)) == (200, b"x", finale)
    assert chiamate == [_url(ISIN_ACME), finale]


def test_scarica_troppi_redirect_rifiutati(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **k: _Risposta(302, location=url))
    with pytest.raises(bi.URLVietato, match="redirect"):
        bi._scarica(_url(ISIN_ACME))


@pytest.mark.parametrize("percorso", ["/mediasource/borsa/db/pdf/x.pdf", "/pdf/frame?x=1",
                                      "/borsa/searchengine?q=x", "/borsa/caratteristiche/view.html",
                                      "/borsa/notizie/mf-dow-jones/x.html"])
def test_altri_disallow_di_borsa(percorso):
    assert bi.url_consentito("https://www.borsaitaliana.it" + percorso)[0] is False


@pytest.mark.parametrize("percorso", ["/README.txt", "/web.config", "/index.php/search/x", "/filter/tips"])
def test_altri_disallow_di_emarket(percorso):
    assert bi.url_consentito("https://www.emarketstorage.it" + percorso)[0] is False


def test_get_eventi_usa_scarica_vero_cablato(monkeypatch, tmp_path):
    """Il cablaggio: senza il finto `_scarica`, la funzione pubblica arriva a requests.get con
    l'URL eventi dell'ISIN, e la risposta finisce nel parser."""
    import requests
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": None}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(negozio.parent / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))

    visti = []
    monkeypatch.setattr(requests, "get", lambda url, **k: visti.append(url) or
                        _Risposta(200, _fixture("bi_eventi_vuoto.html")))
    r = bi.get_eventi_societari("ACME.MI")
    assert visti == [_url(ISIN_ACME)] and r["stato"] == "vuoto_misurato"


# ------------------------------------------------------------ G. risolvi_isin (PM 04/10, opzione A)
ISIN_HOLDING = "ITZZACMH0004"
SCHEDA_ACME = bi.BASE + "/borsa/azioni/scheda/%s-MTAA.html?lang=it" % ISIN_ACME
SCHEDA_HOLDING = bi.BASE + "/borsa/azioni/scheda/%s-MTAA.html?lang=it" % ISIN_HOLDING
LISTINO_A = bi.URL_LISTINO.format(iniziale="A")


@pytest.fixture
def risolvi(ambiente, monkeypatch):
    """Negozio confermato SENZA ACME.MI (solo ZZTEST.MI), listino A di due pagine, due schede,
    menu eMarket con ACME SINTETICA (4242)."""
    ambiente["negozio"].write_text(json.dumps({"ZZTEST.MI": {"isin": ISIN_TEST, "emarket": None}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PAUSA_S", 0)
    ambiente["risposte"].update({
        LISTINO_A: (200, _fixture("bi_listino_A_p1.html")),
        LISTINO_A + "&page=2": (200, _fixture("bi_listino_A_p2.html")),
        # lettera di ripiego della seconda parola («Sintetica»): una pagina senza righe utili
        bi.URL_LISTINO.format(iniziale="S"): (200, _fixture("bi_listino_A_p2.html")),
        SCHEDA_ACME: (200, _fixture("bi_scheda_acme.html")),
        SCHEDA_HOLDING: (200, _fixture("bi_scheda_acme_holding.html")),
        bi.URL_MENU_EMARKET: (200, _fixture("em_lista_vuota.html")),
    })
    ambiente["auto"] = ambiente["tmp"] / "isin_it_auto.json"
    return ambiente


@pytest.mark.parametrize("nome,atteso", [
    ("Prysmian S.p.A.", "PRYSMIAN"), ("ACME SpA", "ACME"), ("Acme S.p.A", "ACME"),
    ("Società Sintética S.p.A.", "SOCIETA SINTETICA"), ("Alfa & Beta N.V.", "ALFA E BETA"),
    ("Zeta Fittizia Società per Azioni", "ZETA FITTIZIA"), ("  acme   sintetica ", "ACME SINTETICA"),
    (None, ""),
])
def test_normalizza_nome(nome, atteso):
    assert bi.normalizza_nome(nome) == atteso


def test_risolvi_ok_scheda_decide_e_salva_nel_negozio_automatico(risolvi):
    r = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r["stato"] == "ok", r["motivo"]
    assert (r["isin"], r["emarket"], r["negozio"], r["salvato"]) == (ISIN_ACME, 4242, "automatico", True)
    v = r["verifica"]
    assert v["nome_cercato"] == "Acme S.p.A." and v["nome_normalizzato"] == "ACME" and v["iniziale"] == "A"
    assert v["pagine_listino"] == [LISTINO_A, LISTINO_A + "&page=2"]   # la p.2 inizia dopo ACME: stop
    assert [x["nome"] for x in v["righe_corrispondenti"]] == ["Acme Holding", "Acme Sintetica"]
    assert v["riga_usata"]["nome"] == "Acme Sintetica" and v["riga_usata"]["scheda_url"] == SCHEDA_ACME
    esiti = {s["isin"]: (s["esito"], s["codici_alfanumerici"]) for s in v["schede_controllate"]}
    assert esiti == {ISIN_HOLDING: ("scartata", ["ACH"]), ISIN_ACME: ("accettata", ["ACME"])}
    assert len(v["scheda_sha256"]) == 64 and r["fonte_url"] == SCHEDA_ACME
    salvato = json.loads(risolvi["auto"].read_text(encoding="utf-8"))["ACME.MI"]
    assert salvato["isin"] == ISIN_ACME and salvato["emarket"] == 4242
    assert salvato["origine"].startswith("verificato automaticamente su Borsa Italiana il ")
    assert salvato["riga_listino"] == "Acme Sintetica" and salvato["nome_cercato"] == "Acme S.p.A."
    # il negozio scritto si rilegge col caricatore vero, e la voce dice da dove viene
    voce, err, _ = bi.voce_ticker_o_auto("ACME.MI")
    assert err is None and voce["negozio"] == "automatico" and voce["isin"] == ISIN_ACME
    n = len(risolvi["chieste"])
    r2 = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r2["stato"] == "ok" and r2["negozio"] == "automatico" and len(risolvi["chieste"]) == n


def test_risolvi_negozio_confermato_ha_la_precedenza(risolvi):
    r = bi.risolvi_isin("ZZTEST.MI", nome="Qualunque")
    assert r["stato"] == "ok" and r["negozio"] == "confermato" and r["isin"] == ISIN_TEST
    assert risolvi["chieste"] == []
    # anche quando la stessa chiave sta in tutti e due i negozi
    risolvi["auto"].write_text(json.dumps({"ZZTEST.MI": {
        "isin": ISIN_ACME, "emarket": 1, "origine": bi.ORIGINE_AUTO % "x"}}), encoding="utf-8")
    voce, _e, _m = bi.voce_ticker_o_auto("ZZTEST.MI")
    assert voce["negozio"] == "confermato" and voce["isin"] == ISIN_TEST


@pytest.mark.parametrize("nome", [None, "", "  S.p.A. "])
def test_risolvi_senza_nome_non_trovato_dichiarato(risolvi, nome):
    r = bi.risolvi_isin("ACME.MI", nome=nome)
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_assente"
    assert "serve il nome dell'emittente" in r["motivo"] and r["isin"] is None
    assert risolvi["chieste"] == [] and not risolvi["auto"].exists()


def test_risolvi_nome_assente_dal_listino(risolvi):
    # una parola sola: nessuna altra lettera da provare (il ripiego sulle altre iniziali e'
    # provato in test_borsa_italiana_risolvi.py)
    r = bi.risolvi_isin("ACME.MI", nome="Acquario")
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_non_nel_listino" and not risolvi["auto"].exists()
    assert len(r["verifica"]["pagine_listino"]) == 2 and r["verifica"]["iniziali_lette"] == ["A"]


def test_risolvi_listino_letto_finche_serve(risolvi):
    """«Aurora» viene dopo la p.2 (che inizia con ALFA): serve la p.3, che qui non risponde -> KO."""
    r = bi.risolvi_isin("ACME.MI", nome="Aurora Inesistente")
    assert r["stato"] == "KO" and r["errore"] == "rete" and "pagina 3" in r["motivo"]
    assert risolvi["chieste"][-1] == LISTINO_A + "&page=3"


def test_risolvi_scheda_con_due_codici_non_accettata(risolvi):
    risolvi["risposte"][SCHEDA_ACME] = (200, _fixture("bi_scheda_due_codici.html"))
    r = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r["stato"] == "non_trovato" and r["errore"] == "nessuna_scheda_valida" and r["isin"] is None
    assert not risolvi["auto"].exists()


def test_risolvi_simbolo_diverso_non_accettato(risolvi):
    r = bi.risolvi_isin("ACMX.MI", nome="Acme S.p.A.")
    assert r["stato"] == "non_trovato" and r["isin"] is None


def test_risolvi_due_schede_valide_ambiguo(risolvi):
    doppia = _fixture("bi_scheda_acme_holding.html").replace(b"ACH", b"ACME")
    risolvi["risposte"][SCHEDA_HOLDING] = (200, doppia)
    r = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r["stato"] == "ambiguo" and r["isin"] is None and not risolvi["auto"].exists()
    assert ISIN_ACME in r["motivo"] and ISIN_HOLDING in r["motivo"]


def test_risolvi_scheda_non_letta_KO_niente_verdetto(risolvi):
    risolvi["risposte"][SCHEDA_ACME] = TimeoutError("x")
    r = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r["stato"] == "KO" and r["errore"] == "rete" and not risolvi["auto"].exists()


def test_risolvi_listino_cambiato_KO(risolvi):
    risolvi["risposte"][LISTINO_A] = (200, _fixture("bi_listino_layout.html"))
    r = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r["stato"] == "KO" and r["errore"] == "layout_cambiato"


def test_risolvi_emarket_non_univoco_salvato_con_motivo(risolvi):
    risolvi["risposte"][bi.URL_MENU_EMARKET] = (200, _fixture("em_lista_vuota.html").replace(
        b"ACME SINTETICA", b"OMEGA"))
    r = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == ISIN_ACME and r["emarket"] is None
    assert "non_trovato" in r["emarket_motivo"] and r["salvato"]
    voce, _e, _m = bi.voce_ticker_o_auto("ACME.MI")
    assert voce["emarket"] is None and voce["emarket_motivo"] == r["emarket_motivo"]


def test_risolvi_negozio_automatico_illeggibile_non_sovrascritto(risolvi):
    risolvi["auto"].write_text("{rotto", encoding="utf-8")
    r = bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert r["stato"] == "KO" and r["errore"] == "negozio_illeggibile"
    assert risolvi["auto"].read_text(encoding="utf-8") == "{rotto" and risolvi["chieste"] == []


@pytest.mark.parametrize("ticker", ["ACME", "ACME.DE", "", "AC ME.MI"])
def test_risolvi_solo_simboli_MI(risolvi, ticker):
    r = bi.risolvi_isin(ticker, nome="Acme")
    assert r["stato"] == "KO" and r["errore"] == "parametro" and risolvi["chieste"] == []


def test_voce_ticker_solo_confermato_dichiara(ambiente, monkeypatch):
    """voce_ticker (solo negozio confermato) resta pubblica: ticker assente e negozio assente
    dichiarati, mai una voce inventata (banco M08/M09)."""
    voce, err, mot = bi.voce_ticker("NONCE.MI")
    assert voce is None and err == "ticker_non_mappato" and "NONCE.MI" in mot
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(ambiente["tmp"] / "manca.json"))
    voce, err, _ = bi.voce_ticker("ACME.MI")
    assert voce is None and err == "negozio_assente"


def test_negozio_confermato_illeggibile_non_si_scavalca_con_l_automatico(risolvi):
    """Il negozio del PM rotto NON si aggira leggendo l'automatico: KO dichiarato (banco O3)."""
    risolvi["auto"].write_text(json.dumps({"ACME.MI": {
        "isin": ISIN_ACME, "emarket": 4242, "origine": bi.ORIGINE_AUTO % "x"}}), encoding="utf-8")
    risolvi["negozio"].write_text("{rotto", encoding="utf-8")
    voce, err, mot = bi.voce_ticker_o_auto("ACME.MI")
    assert voce is None and err == "negozio_illeggibile" and "illeggibile" in mot
    r = bi.get_eventi_societari("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "negozio_illeggibile" and risolvi["chieste"] == []


def test_risolvi_scheda_con_isin_diverso_dal_link_non_accettata(risolvi):
    """Banco A4: il link del listino dice ACME, la pagina mostra un altro ISIN (col codice giusto)."""
    altra = _fixture("bi_scheda_acme_holding.html").replace(b"ACH", b"ACME")   # Codice Isin = HOLDING
    risolvi["risposte"][SCHEDA_ACME] = (200, altra)
    r = bi.risolvi_isin("ACME.MI", nome="Acme Sintetica")
    assert r["stato"] == "non_trovato" and r["isin"] is None
    (sch,) = r["verifica"]["schede_controllate"]
    assert sch["esito"] == "scartata" and "Codice Isin" in sch["motivo"]


def test_scrittura_rifiuta_un_negozio_automatico_illeggibile(risolvi):
    """Banco A8: lo scrittore NON sovrascrive un negozio automatico rotto (perderebbe le voci)."""
    risolvi["auto"].write_text("{rotto", encoding="utf-8")
    ok, motivo = bi._scrivi_voce_auto("ACME.MI", {"isin": ISIN_ACME, "emarket": 1, "origine": bi.ORIGINE_AUTO % "x"})
    assert ok is False and "illeggibile" in motivo
    assert risolvi["auto"].read_text(encoding="utf-8") == "{rotto"


def test_risolvi_emarket_ambiguo_nel_menu(risolvi):
    """Banco A10: due emittenti del menu combaciano per parole col nome -> nessun id, motivo 'ambiguo'."""
    menu = _fixture("em_lista_vuota.html").replace(
        b'<option value="4242" selected="selected">ACME SINTETICA</option>',
        b'<option value="4242">ACME SINTETICA GRUPPO</option><option value="4343">ACME SINTETICA RISPARMIO</option>')
    risolvi["risposte"][bi.URL_MENU_EMARKET] = (200, menu)
    r = bi.risolvi_isin("ACME.MI", nome="Acme Sintetica")
    assert r["stato"] == "ok" and r["emarket"] is None and "ambiguo" in r["emarket_motivo"]


def test_voce_automatica_malformata_rende_illeggibile(tmp_path):
    p = tmp_path / "auto.json"
    p.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": 1, "origine": "scritto a mano"}}), encoding="utf-8")
    assert bi.carica_isin_auto(str(p))["origine"] == "illeggibile"


def test_listino_page_permesso_eventi_page_no():
    assert bi.url_consentito(LISTINO_A + "&page=2")[0] is True
    assert bi.url_consentito(_url(ISIN_ACME) + "&page=2")[0] is False


def test_eventi_dichiarano_da_quale_negozio(risolvi):
    risolvi["risposte"][_url(ISIN_ACME)] = (200, _fixture("bi_eventi_vuoto.html"))
    bi.risolvi_isin("ACME.MI", nome="Acme S.p.A.")
    assert bi.get_eventi_societari("ACME.MI")["voce_da"] == "automatico"
    risolvi["risposte"][_url(ISIN_TEST)] = (200, _fixture("bi_eventi_vuoto.html"))
    assert bi.get_eventi_societari("ZZTEST.MI")["voce_da"] == "confermato"

def test_url_vietato_eventi_motivo_controllato_senza_il_testo_dell_eccezione(monkeypatch):
    """VF 05/10 (regola 16): il messaggio di URLVietato puo' contenere l'URL del redirect; nel motivo va un
    testo controllato (tipo dell'eccezione + motivo fisso), lo stato resta KO/url_vietato."""
    def vietato(url):
        raise bi.URLVietato("redirect verso https://zz-host-finto.example/x?token=QQSEGRETO: host non previsto")
    monkeypatch.setattr(bi, "_scarica", vietato)
    out = bi._leggi_eventi("ACME.MI", "ITZZACME0007")
    assert (out["stato"], out["errore"]) == ("KO", "url_vietato")
    assert "QQSEGRETO" not in out["motivo"] and "zz-host-finto" not in out["motivo"]
    assert "URLVietato" in out["motivo"]
