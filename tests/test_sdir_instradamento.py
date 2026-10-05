# -*- coding: utf-8 -*-
"""sdir.py (handoff-3, 05/10/2026, Opus 5.5): instradatore fra eMarket SDIR e 1INFO-SDIR.

Nessuna rete. Le LETTURE dei due moduli sono sostituite da spie (si prova la SCELTA e la
fusione, e che la fonte non scelta NON sia letta); le MISURE di attivita' girano vere sulle
fixture sintetiche (eMarket: em_lista_*.html, id 4242; 1INFO: oi_*.json, ndg 99901). Un test
finale fa girare oneinfo_sdir vero dietro l'instradatore.
"""
import json
import os
from datetime import date

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import emarket_sdir as em
from bellomberg.market_data import oneinfo_sdir as oi
from bellomberg.market_data import sdir

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fonti_it")
ISIN_ACME = "ITZZACME0007"


def _fixture(nome):
    with open(os.path.join(FIX, nome), "rb") as fh:
        return fh.read()


class Ambiente:
    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.letture = []
        self.kwargs_dep = []
        self.em_html = _fixture("em_lista_ok.html")          # ultimo comunicato 20/09/2026: attivo
        self.oi = {"emittenti": _fixture("oi_emittenti.json"), "attivita": _fixture("oi_attivita_recente.json")}
        self.rete = []
        monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 5))

        def scarica(url):
            ok, motivo = bi.url_consentito(url)
            assert ok, motivo
            self.rete.append(url)
            r = self.em_html
            if isinstance(r, Exception):
                raise r
            return (200, r, url) if isinstance(r, bytes) else (r[0], r[1], url)
        monkeypatch.setattr(bi, "_scarica", scarica)

        def richiesta(metodo, url, dati=None):
            assert oi.richiesta_consentita(metodo, url)[0]
            self.rete.append(url)
            r = self.oi["emittenti"] if url == oi.URL_EMITTENTI else self.oi["attivita"]
            if isinstance(r, Exception):
                raise r
            emittente = (dati or {}).get("SearchFilter[emittente]")
            if isinstance(r, bytes) and emittente and url != oi.URL_EMITTENTI:
                # attivita' per ndg: le righe portano l'ndg chiesto (per-ndg in self.oi["per_ndg"])
                j = json.loads((self.oi.get("per_ndg", {}).get(int(emittente)) or r).decode("utf-8"))
                for x in j["data"]:
                    if x.get("ndg") is not None:
                        x["ndg"] = int(emittente)
                r = json.dumps(j).encode("utf-8")
            return r if isinstance(r, tuple) else (200, r)
        monkeypatch.setattr(oi, "_richiesta", richiesta)
        self.dep = {"emarket": {"stato": "ok", "data_deposito": "2026-08-01"},
                    "oneinfo": {"stato": "ok", "data_deposito": "2026-08-01"}}
        self.idd = {"emarket": {"stato": "ok", "comunicazioni": [{"data": "2026-09-01", "ora": "10:00"}]},
                    "oneinfo": {"stato": "ok", "comunicazioni": [{"data": "2026-09-20", "ora": "17:00"}]}}

        def finto_dep(nome_fonte):
            def f(ticker, *, tipo, periodo_fine, ndg=None, **kw):
                self.letture.append((nome_fonte, "deposito", ndg))
                self.kwargs_dep.append((nome_fonte, kw))
                base = em._base_deposito(ticker, tipo, periodo_fine)
                return dict(base, **self.dep[nome_fonte])
            return f

        def finto_id(nome_fonte):
            def f(ticker, *, giorni=180, ndg=None):
                self.letture.append((nome_fonte, "id", ndg))
                return dict(em._base(ticker, giorni), **self.idd[nome_fonte])
            return f
        monkeypatch.setattr(em, "get_data_deposito", finto_dep("emarket"))
        monkeypatch.setattr(oi, "get_data_deposito", finto_dep("oneinfo"))
        monkeypatch.setattr(em, "get_internal_dealing", finto_id("emarket"))
        monkeypatch.setattr(oi, "get_internal_dealing", finto_id("oneinfo"))

    def voce(self, negozio="confermato", nome_listino=None, mercato=None, **campi):
        """`nome_listino` / `mercato`: le risposte di borsa_italiana.nome_listino / mercato_listino
        (lettori di IT1, senza rete)."""
        v = dict({"isin": ISIN_ACME, "negozio": negozio}, **campi)
        self.mp.setattr(bi, "voce_ticker_o_auto", lambda t: (dict(v), None, None))
        self.mp.setattr(bi, "nome_listino", lambda t: nome_listino)
        self.mp.setattr(bi, "mercato_listino", lambda t: mercato)

    def fonti(self):
        return [x[0] for x in self.letture]


@pytest.fixture
def amb(monkeypatch, tmp_path):
    return Ambiente(monkeypatch, tmp_path)


def _dep(**k):
    return sdir.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30", **k)


# ============================================================ A. negozio confermato: precedenza, nessuna misura
@pytest.mark.parametrize("campi,scelta,fonti,sdir_atteso", [
    ({"emarket": 4242}, "emarket", ["emarket"], sdir.SDIR_EMARKET),
    ({"emarket": 4242, "oneinfo": None}, "emarket", ["emarket"], sdir.SDIR_EMARKET),
    ({"emarket": None, "oneinfo": 99901}, "oneinfo", ["oneinfo"], sdir.SDIR_1INFO),
    ({"emarket": 4242, "oneinfo": 99901}, "entrambi", ["emarket", "oneinfo"], sdir.SDIR_EMARKET),
])
def test_confermato_ha_la_precedenza(amb, campi, scelta, fonti, sdir_atteso):
    amb.voce(**campi)
    r = _dep()
    assert r["instradamento"]["scelta"] == scelta and r["instradamento"]["regola"] == "negozio_confermato"
    assert amb.fonti() == fonti and r["sdir"] == sdir_atteso and r["stato"] == "ok"
    assert amb.rete == []                                 # nessuna misura: il negozio basta
    if "oneinfo" in fonti:
        assert ("oneinfo", "deposito", 99901) in amb.letture


def test_confermato_nessuno_non_coperto_senza_letture(amb):
    amb.voce(emarket=None, oneinfo=None)
    r = _dep()
    assert r["stato"] == "non_coperto" and r["errore"] == "nessuno_sdir" and r["sdir"] is None
    assert amb.letture == [] and amb.rete == []
    i = sdir.get_internal_dealing("ACME.MI")
    assert i["stato"] == "non_coperto" and i["instradamento"]["scelta"] == "nessuno"


def test_confermato_emarket_null_oneinfo_assente_si_misura(amb):
    amb.voce(emarket=None, nome_cercato=None)
    r = _dep(nome="Acme Sintetica S.p.A.")
    assert r["instradamento"]["regola"] == "misto" and r["instradamento"]["scelta"] == "oneinfo"
    assert r["instradamento"]["oneinfo_ndg"] == 99901 and amb.fonti() == ["oneinfo"]
    amb.letture.clear()
    amb.oi["attivita"] = _fixture("oi_attivita_storico.json")
    amb.mp.setattr(bi, "CACHE_DIR", bi.CACHE_DIR + "_2")     # la misura di attivita' e' in cache 24 h
    r = _dep(nome="Acme Sintetica S.p.A.")
    assert r["stato"] == "non_coperto" and amb.letture == [] and "2015-04-30" in r["instradamento"]["perche"]


def test_confermato_misura_senza_nome_ko(amb):
    amb.voce(emarket=None)
    r = _dep()
    assert r["stato"] == "KO" and r["errore"] == "instradamento_non_misurabile" and amb.letture == []


# ============================================================ B. negozio automatico: misura di tutti e due
@pytest.mark.parametrize("em_html,oi_att,scelta,fonti", [
    ("em_lista_ok.html", "oi_attivita_storico.json", "emarket", ["emarket"]),
    ("em_lista_vuota.html", "oi_attivita_recente.json", "oneinfo", ["oneinfo"]),
    ("em_lista_ok.html", "oi_attivita_recente.json", "entrambi", ["emarket", "oneinfo"]),
    ("em_lista_vuota.html", "oi_attivita_storico.json", "nessuno", []),
])
def test_automatico_attivita(amb, em_html, oi_att, scelta, fonti):
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Acme Sintetica", nome_cercato="ACME SINTETICA SPA")
    amb.em_html = _fixture(em_html)
    amb.oi["attivita"] = _fixture(oi_att)
    r = _dep()
    assert r["instradamento"]["regola"] == "misura_automatica" and r["instradamento"]["scelta"] == scelta
    assert amb.fonti() == fonti
    assert r["stato"] == ("non_coperto" if scelta == "nessuno" else "ok")
    assert amb.rete[0] == sdir.URL_ATTIVITA_EMARKET.format(id=4242)


def test_automatico_emarket_vecchio_conta_come_inattivo(amb, monkeypatch):
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Acme Sintetica")
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2027, 9, 1))   # ultimo eMarket 20/09/2026: > 180 gg
    amb.oi["attivita"] = _fixture("oi_attivita_storico.json")
    r = _dep()
    assert r["instradamento"]["scelta"] == "nessuno" and r["instradamento"]["emarket"]["esito"] == "inattivo"


def test_automatico_id_emarket_assente_dal_menu(amb):
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Acme Sintetica")
    amb.em_html = _fixture("em_lista_vuota.html").replace(b'option value="4242"', b'option value="4243"')
    r = sdir.instrada("ACME.MI")
    assert r["emarket"]["esito"] == "inattivo" and r["scelta"] == "oneinfo"


@pytest.mark.parametrize("guasto", ["emarket", "oneinfo"])
def test_misura_ko_nessun_ripiego(amb, guasto):
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Acme Sintetica")
    if guasto == "emarket":
        amb.em_html = ConnectionError("giu")
    else:
        amb.oi["attivita"] = (503, b"")
    r = _dep()
    assert r["stato"] == "KO" and r["errore"] == "instradamento_KO" and amb.letture == []
    assert r["sdir"] is None and guasto in r["motivo"]
    i = sdir.get_internal_dealing("ACME.MI")
    assert i["stato"] == "KO" and amb.letture == []


def test_nome_ambiguo_su_1info_ko(amb):
    amb.voce(negozio="automatico", emarket=None, riga_listino="Doppia Sintetica")
    r = sdir.instrada("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "instradamento_ambiguo"


def test_nome_non_in_lista_1info_e_inattivo(amb):
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Altra Fittizia")
    r = sdir.instrada("ACME.MI")
    assert r["oneinfo"]["esito"] == "non_trovato" and r["scelta"] == "emarket"


# ============================================================ C. letti tutti e due: fusione
@pytest.mark.parametrize("a,b,stato,sdir_atteso", [
    ({"stato": "ok", "data_deposito": "2026-08-01"}, {"stato": "ok", "data_deposito": "2026-08-01"}, "ok", sdir.SDIR_EMARKET),
    ({"stato": "ok", "data_deposito": "2026-07-31"}, {"stato": "ok", "data_deposito": "2026-08-01"}, "ambiguo", sdir.SDIR_ENTRAMBI),
    ({"stato": "non_trovato"}, {"stato": "ok", "data_deposito": "2026-08-01"}, "ok", sdir.SDIR_1INFO),
    ({"stato": "ok", "data_deposito": "2026-08-01"}, {"stato": "non_coperto"}, "ok", sdir.SDIR_EMARKET),
    ({"stato": "KO", "errore": "rete"}, {"stato": "ok", "data_deposito": "2026-08-01"}, "KO", sdir.SDIR_ENTRAMBI),
    ({"stato": "ambiguo"}, {"stato": "non_trovato"}, "ambiguo", sdir.SDIR_EMARKET),
    ({"stato": "non_trovato"}, {"stato": "non_trovato"}, "non_trovato", sdir.SDIR_ENTRAMBI),
])
def test_deposito_entrambi(amb, a, b, stato, sdir_atteso):
    amb.voce(emarket=4242, oneinfo=99901)
    amb.dep = {"emarket": a, "oneinfo": b}
    r = _dep()
    assert r["stato"] == stato and r["sdir"] == sdir_atteso
    assert set(r["letture"]) == {"emarket", "oneinfo"}
    if stato == "ambiguo" and a["stato"] == "ok":
        assert r["data_deposito"] is None and "discordi" in r["motivo"]
    if stato == "KO":
        assert r["errore"] == "fonte_ko"


def test_internal_dealing_entrambi_unione_con_sdir(amb):
    amb.voce(emarket=4242, oneinfo=99901)
    r = sdir.get_internal_dealing("ACME.MI")
    assert r["stato"] == "ok" and r["sdir"] == sdir.SDIR_ENTRAMBI
    assert [(c["data"], c["sdir"]) for c in r["comunicazioni"]] == [
        ("2026-09-20", sdir.SDIR_1INFO), ("2026-09-01", sdir.SDIR_EMARKET)]


@pytest.mark.parametrize("a,b,stato", [("KO", "ok", "KO"), ("vuoto_misurato", "ok", "ok"),
                                       ("vuoto_misurato", "non_coperto", "vuoto_misurato"),
                                       ("non_coperto", "non_coperto", "non_coperto")])
def test_internal_dealing_entrambi_stati(amb, a, b, stato):
    amb.voce(emarket=4242, oneinfo=99901)
    amb.idd["emarket"]["stato"], amb.idd["oneinfo"]["stato"] = a, b
    if a != "ok":
        amb.idd["emarket"]["comunicazioni"] = []
    r = sdir.get_internal_dealing("ACME.MI")
    assert r["stato"] == stato
    if stato == "KO":
        assert r["errore"] == "fonte_ko" and r["comunicazioni"] == []


def test_internal_dealing_una_fonte_comunicazioni_marcate(amb):
    amb.voce(emarket=None, oneinfo=99901)
    r = sdir.get_internal_dealing("ACME.MI", giorni=90)
    assert r["sdir"] == sdir.SDIR_1INFO and all(c["sdir"] == sdir.SDIR_1INFO for c in r["comunicazioni"])
    assert amb.letture == [("oneinfo", "id", 99901)]


# ============================================================ D. parametri e chiavi sempre presenti
@pytest.mark.parametrize("tipo,fine", [("mensile", "2026-06-30"), ("semestrale", "ieri"), ("trimestrale", "2026-06-30")])
def test_parametri_prima_dell_instradamento(amb, tipo, fine):
    amb.voce(emarket=4242)
    r = sdir.get_data_deposito("ACME.MI", tipo=tipo, periodo_fine=fine)
    assert r["errore"] == "parametro" and r["sdir"] is None and r["instradamento"]["scelta"] is None
    assert amb.letture == [] and amb.rete == []


def test_giorni_non_validi(amb):
    amb.voce(emarket=4242)
    r = sdir.get_internal_dealing("ACME.MI", giorni=0)
    assert r["errore"] == "parametro" and r["sdir"] is None and "instradamento" in r


def test_ticker_non_mappato(amb, monkeypatch):
    monkeypatch.setattr(bi, "voce_ticker_o_auto", lambda t: (None, "ticker_non_mappato", "assente"))
    r = _dep()
    assert r["stato"] == "KO" and r["errore"] == "ticker_non_mappato" and "sdir" in r and "instradamento" in r


# ============================================================ E. cablaggio vero: oneinfo_sdir dietro sdir
def test_end_to_end_oneinfo_vero(monkeypatch, tmp_path):
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 5))
    monkeypatch.setattr(bi, "voce_ticker_o_auto",
                        lambda t: ({"isin": ISIN_ACME, "emarket": None, "oneinfo": 99901, "negozio": "confermato"}, None, None))
    chieste = []

    def richiesta(metodo, url, dati=None):
        assert oi.richiesta_consentita(metodo, url)[0]
        chieste.append(url)
        return 200, _fixture("oi_documenti_ok.json")
    monkeypatch.setattr(oi, "_richiesta", richiesta)
    r = _dep()
    assert r["stato"] == "ok" and r["sdir"] == sdir.SDIR_1INFO and r["protocollo"] == "900004_oneinfo"
    assert r["oneinfo_ndg"] == 99901 and r["tipo_data"] == "stoccaggio_documento" and chieste == [oi.URL_DOCUMENTI]


def test_campo_oneinfo_letto_dal_negozio_vero(amb, monkeypatch, tmp_path):
    """Nessuna voce finta: il negozio confermato su disco (validazione di borsa_italiana, IT1)."""
    import json
    neg = tmp_path / "isin_it.json"
    neg.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": None, "oneinfo": 99901},
                               "QQSYN.MI": {"isin": "ITZZQQSYN005", "emarket": 777777}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(neg))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    r = sdir.instrada("ACME.MI")
    assert (r["scelta"], r["oneinfo_ndg"], r["regola"]) == ("oneinfo", 99901, "negozio_confermato")
    r = sdir.instrada("QQSYN.MI")           # voce senza 'oneinfo': resta valida, vale eMarket dichiarato
    assert (r["scelta"], r["emarket_id"]) == ("emarket", 777777) and r["oneinfo"]["esito"] == "non_dichiarato"


# ============================================================ F. review RV-ON
def test_nome_non_trovato_e_misura_fallita_non_assenza(amb):
    """RV-ON P2-3: eMarket inattivo + nome assente dalla lista 1INFO = KO dichiarato, non non_coperto."""
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Altra Fittizia")
    amb.em_html = _fixture("em_lista_vuota.html")
    r = _dep()
    assert r["stato"] == "KO" and r["errore"] == "instradamento_nome_non_trovato" and amb.letture == []


def test_confermato_misto_nome_non_trovato_ko(amb):
    amb.voce(emarket=None)
    r = _dep(nome="Altra Fittizia")
    assert r["stato"] == "KO" and r["errore"] == "instradamento_nome_non_trovato"


def _com(soggetto, prot, url):
    return {"data": "2026-09-21", "ora": "17:40", "protocollo": prot, "url_pdf": url, "soggetto": soggetto,
            "operazioni": [{"isin": ISIN_ACME, "quantita": 1000, "data_operazione": "2026-09-21"}]}


def test_unione_deduplica_e_conserva_le_ricevute(amb):
    """RV-ON P2-1: la stessa comunicazione su due SDIR conta una volta; ricevute di entrambe."""
    amb.voce(emarket=4242, oneinfo=99901)
    amb.idd["emarket"].update(comunicazioni=[_com("MARIO SINTETICO", "555001", "u1")], url="https://e/lista",
                              fonte=em.FONTE, motivo=None)
    amb.idd["oneinfo"].update(comunicazioni=[_com("Mario  Sintetico", "99901_9_2026_oneinfo", "u2"),
                                             _com("ALTRA PERSONA", "99901_8_2026_oneinfo", "u3")],
                              url_liste=["POST x"], sha256_liste={"POST x": "abc"}, richieste={"liste": 2, "pdf": 2},
                              fonte=oi.FONTE, motivo="nota 1INFO")
    r = sdir.get_internal_dealing("ACME.MI")
    assert len(r["comunicazioni"]) == 2 and any("contate una volta" in x for x in r["limiti"])
    assert r["url_liste"] == ["https://e/lista", "POST x"] and r["sha256_liste"] == {"POST x": "abc"}
    assert r["richieste_oneinfo"] == {"liste": 2, "pdf": 2} and oi.FONTE in r["fonte"] and em.FONTE in r["fonte"]
    assert r["motivo"] == "nota 1INFO"


@pytest.mark.parametrize("a,b,stato", [("STALE", "ok", "STALE"), ("ok", "STALE", "STALE"),
                                       ("STALE", "non_coperto", "STALE"), ("non_coperto", "STALE", "STALE")])
def test_stale_propagato_nell_unione(amb, a, b, stato):
    amb.voce(emarket=4242, oneinfo=99901)
    amb.idd["emarket"]["stato"], amb.idd["oneinfo"]["stato"] = a, b
    assert sdir.get_internal_dealing("ACME.MI")["stato"] == stato


# ============================================================ G. nomi provati uno alla volta (caso misurato 05/10)
def test_nome_dato_non_abbina_nome_listino_si(amb):
    """Caso misurato: il nome commerciale non e' quello di 1INFO, il nome del Listino si'."""
    amb.voce(emarket=None, nome_listino="Acme Sintetica")
    r = _dep(nome="Acme Nuova S.p.A.")
    i = r["instradamento"]
    assert r["stato"] == "ok" and i["scelta"] == "oneinfo" and i["oneinfo_ndg"] == 99901
    assert i["nome_abbinato"] == "ACME SINTETICA" and "nome listino" in i["oneinfo"]["motivo"]
    esiti = {p["origine"]: p["esito"] for p in i["oneinfo"]["per_nome"]}
    assert esiti == {"nome dato": "non_trovato", "nome listino": "ok"}


def test_nome_dato_e_listino_su_emittenti_diversi_ambiguo(amb):
    amb.voce(emarket=None, nome_listino="Acme Sintetica")
    r = _dep(nome="Zeta Fittizia")
    assert r["stato"] == "KO" and r["errore"] == "instradamento_ambiguo" and amb.letture == []
    assert "DIVERSI" in r["motivo"] and r["instradamento"]["nome_abbinato"] is None


def test_nome_dato_abbina_e_vince_listino_che_conferma(amb):
    amb.voce(negozio="automatico", emarket=None, riga_listino="ACME SINTETICA SPA", nome_cercato="Acme")
    r = sdir.instrada("ACME.MI", nome="Acme Sintetica S.p.A.")
    assert r["scelta"] == "oneinfo" and r["oneinfo"]["nome_abbinato"] == "ACME SINTETICA"
    assert r["oneinfo"]["per_nome"][0]["origine"] == "nome dato"


def test_nome_ambiguo_che_esclude_lo_scelto_e_contraddizione(amb):
    """«Doppia Sintetica» abbina due ndg, nessuno dei quali e' quello del nome del listino: ambiguo."""
    amb.voce(emarket=None, nome_listino="Acme Sintetica")
    r = _dep(nome="Doppia Sintetica")
    assert r["stato"] == "KO" and r["errore"] == "instradamento_ambiguo" and "contraddizione" in r["motivo"]


def test_nome_corto_comune_che_include_lo_scelto_non_contraddice(amb, monkeypatch):
    """Nome corto comune a due emittenti (uno dei quali e' quello scelto) + nome lungo univoco = ok."""
    amb.oi["emittenti"] = (b'[{"ndg": 99901, "descrizione": "ACME SINTETICA"}, '
                           b'{"ndg": 99907, "descrizione": "BANCA SINTETICA"}]')
    amb.voce(emarket=None, nome_listino="Sintetica")
    r = _dep(nome="Acme Sintetica")
    assert r["stato"] == "ok" and r["instradamento"]["oneinfo_ndg"] == 99901
    assert {p["origine"]: p["esito"] for p in r["instradamento"]["oneinfo"]["per_nome"]}["nome listino"] == "ambiguo"


def test_nome_listino_vero_dal_negozio_automatico(amb, monkeypatch, tmp_path):
    """Cablaggio senza finti: ticker CONFERMATO (emarket null, oneinfo assente) e voce AUTOMATICA con
    lo stesso ISIN e riga_listino: il lettore vero di IT1 da' il nome che abbina su 1INFO."""
    import json
    conf, auto = tmp_path / "isin_it.json", tmp_path / "isin_it_auto.json"
    conf.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": None}}), encoding="utf-8")
    ora = "2026-10-05T07:00:00+00:00"
    auto.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": None, "origine": bi.ORIGINE_AUTO % ora,
                                            "verificato_il": ora, "riga_listino": "Acme Sintetica",
                                            "nome_cercato": "Acme Nuova S.p.A."}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(conf))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(auto))
    # voce_ticker_o_auto e nome_listino restano quelli VERI: amb.voce non e' chiamato
    r = sdir.instrada("ACME.MI", nome="Acme Nuova S.p.A.")
    assert (r["scelta"], r["oneinfo_ndg"], r["oneinfo"]["nome_abbinato"]) == ("oneinfo", 99901, "ACME SINTETICA")


# ============================================================ H. fine_esercizio (esercizio non solare, main 05/10)
def test_fine_esercizio_default_invariato_nessun_kwarg_inoltrato(amb):
    amb.voce(emarket=4242, oneinfo=99901)
    _dep()
    assert amb.kwargs_dep == [("emarket", {}), ("oneinfo", {})]


def test_fine_esercizio_al_30_aprile_inoltrato_a_emarket(amb):
    amb.voce(emarket=4242)
    r = sdir.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2026-04-30", fine_esercizio="04-30")
    assert r["stato"] == "ok" and amb.kwargs_dep == [("emarket", {"fine_esercizio": "04-30"})]


def test_fine_esercizio_incoerente_parametro_senza_letture(amb):
    amb.voce(emarket=4242)
    r = sdir.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30", fine_esercizio="04-30")
    assert r["errore"] == "parametro" and amb.letture == [] and amb.rete == []


def test_semestrale_non_solare_entrambi_1info_non_supportato_si_legge_emarket(amb):
    amb.voce(emarket=4242, oneinfo=99901)
    amb.dep["oneinfo"] = {"stato": "KO", "errore": "parametro", "parametro_non_supportato": True,
                          "motivo": "esercizio non solare non supportato da 1INFO"}
    r = sdir.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-10-31", fine_esercizio="04-30")
    assert r["stato"] == "ok" and r["sdir"] == sdir.SDIR_EMARKET
    assert any("non supportato da 1INFO" in x for x in r["limiti"])


# ============================================================ I. azioni estere del Global Equity Market (main 05/10)
@pytest.mark.parametrize("funzione", ["deposito", "id"])
def test_mercato_bgem_non_coperto_senza_ricerche(amb, funzione):
    """Voce automatica con mic BGEM: nessuna ricerca per nome (troverebbe omonimi italiani)."""
    amb.voce(negozio="automatico", emarket=None, mic="BGEM", riga_listino="Acme Sintetica")
    r = _dep() if funzione == "deposito" else sdir.get_internal_dealing("ACME.MI")
    assert r["stato"] == "non_coperto" and r["errore"] == "azione_estera"
    assert r["motivo"].startswith(sdir.MOTIVO_ESTERO) and "non su eMarket SDIR" not in r["motivo"]
    assert r["instradamento"]["regola"] == "mercato_listino" and r["instradamento"]["scelta"] == "nessuno"
    assert amb.rete == [] and amb.letture == []


@pytest.mark.parametrize("mic", ["MTAA", None])
def test_mercato_italiano_o_assente_come_prima(amb, mic):
    campi = {"mic": mic} if mic else {}
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Acme Sintetica", **campi)
    r = _dep()
    assert r["instradamento"]["regola"] == "misura_automatica" and r["stato"] == "ok"


def test_bgem_dal_lettore_vale_anche_per_il_confermato_misto(amb):
    amb.voce(emarket=None, mercato="BGEM")
    r = _dep(nome="Acme Sintetica")
    assert r["stato"] == "non_coperto" and r["errore"] == "azione_estera" and amb.rete == []


def test_bgem_cablaggio_vero_negozio_automatico(amb, monkeypatch, tmp_path):
    import json
    auto = tmp_path / "isin_it_auto.json"
    ora = "2026-10-05T07:00:00+00:00"
    auto.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": None, "origine": bi.ORIGINE_AUTO % ora,
                                            "verificato_il": ora, "riga_listino": "Acme Sintetica", "mic": "BGEM"}}),
                    encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(tmp_path / "isin_it.json"))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(auto))
    r = sdir.instrada("ACME.MI")
    assert (r["regola"], r["scelta"]) == ("mercato_listino", "nessuno") and amb.rete == []


# ============================================================ J. riverifica senza rete instradata (P1 di RV-D4)
def test_riverifica_1info_vera_dietro_sdir(monkeypatch, tmp_path):
    """Modulo 1INFO vero dietro sdir: ricevuta ok -> riverifica ok; data falsificata -> cade."""
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 5))
    monkeypatch.setattr(bi, "voce_ticker_o_auto",
                        lambda t: ({"isin": ISIN_ACME, "emarket": None, "oneinfo": 99901, "negozio": "confermato"}, None, None))
    monkeypatch.setattr(bi, "mercato_listino", lambda t: None)
    monkeypatch.setattr(oi, "_richiesta", lambda m, u, d=None: (200, _fixture("oi_documenti_ok.json")))
    r = _dep()
    kw = dict(ticker="ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["sdir"] == sdir.SDIR_1INFO and sdir.riverifica_deposito(r, **kw)[0]
    r["data_deposito"] = "2026-08-09"
    ok, motivo = sdir.riverifica_deposito(r, **kw)
    assert ok is False and "data_deposito" in motivo


def test_riverifica_instrada_su_emarket_e_ricevute_vecchie(amb, monkeypatch):
    amb.voce(emarket=4242)
    r = _dep()
    chiamate = []
    monkeypatch.setattr(em, "riverifica_deposito", lambda ric, **kw: (chiamate.append(kw), (True, "em ok"))[1],
                        raising=False)
    kw = dict(ticker="ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert sdir.riverifica_deposito(r, **kw) == (True, "em ok") and chiamate[0]["fine_esercizio"] is None
    vecchia = {k: v for k, v in r.items() if k not in ("sdir", "instradamento")}     # ricevuta vecchia = eMarket
    assert sdir.riverifica_deposito(vecchia, **kw)[0]
    monkeypatch.delattr(em, "riverifica_deposito", raising=False)
    ok, motivo = sdir.riverifica_deposito(r, **kw)
    assert ok is False and "non disponibile" in motivo


def test_riverifica_sdir_e_scelta_incoerenti_cade(amb, monkeypatch):
    amb.voce(emarket=4242)
    r = _dep()
    r["sdir"] = sdir.SDIR_1INFO          # scelta 'emarket' ma sdir 1INFO: manomessa
    monkeypatch.setattr(oi, "riverifica_deposito", lambda ric, **kw: (True, "oi ok"))   # solo la coerenza decide
    assert sdir.riverifica_deposito(r, ticker="ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")[0] is False


def test_riverifica_entrambi_rifa_la_fusione(amb, monkeypatch):
    amb.voce(emarket=4242, oneinfo=99901)
    amb.dep = {"emarket": {"stato": "non_trovato"}, "oneinfo": {"stato": "ok", "data_deposito": "2026-08-01",
                                                               "protocollo": "900004_oneinfo"}}
    r = _dep()
    assert r["sdir"] == sdir.SDIR_1INFO and r["instradamento"]["scelta"] == "entrambi"
    visti = []
    monkeypatch.setattr(em, "riverifica_deposito", lambda ric, **kw: (visti.append("em"), (True, "em"))[1], raising=False)
    monkeypatch.setattr(oi, "riverifica_deposito", lambda ric, **kw: (visti.append("oi"), (True, "oi"))[1])
    kw = dict(ticker="ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert sdir.riverifica_deposito(json.loads(json.dumps(r)), **kw)[0] and visti == ["em", "oi"]
    falsa = json.loads(json.dumps(r))
    falsa["data_deposito"] = "2026-08-09"
    ok, motivo = sdir.riverifica_deposito(falsa, **kw)
    assert ok is False and "fusione" in motivo
    senza = {k: v for k, v in r.items() if k != "ricevute"}
    assert sdir.riverifica_deposito(senza, **kw)[0] is False
    monkeypatch.setattr(oi, "riverifica_deposito", lambda ric, **kw: (False, "sha diverso"))
    ok, motivo = sdir.riverifica_deposito(json.loads(json.dumps(r)), **kw)
    assert ok is False and motivo.startswith("1INFO")


def test_riverifica_sdir_emarket_con_scelta_1info_cade(amb, monkeypatch):
    amb.voce(emarket=None, oneinfo=99901)
    r = _dep()
    r["sdir"] = sdir.SDIR_EMARKET        # scelta 'oneinfo' ma sdir eMarket: manomessa
    monkeypatch.setattr(em, "riverifica_deposito", lambda ric, **kw: (True, "em ok"), raising=False)
    assert sdir.riverifica_deposito(r, ticker="ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")[0] is False


# ============================================================ K. candidati per nome storici (caso MF, main 05/10)
def test_nome_debole_su_emittente_storico_non_coperto_storico(amb):
    """Un nome che abbina solo debolmente un emittente 1INFO INATTIVO: non_coperto «storico», mai ambiguo."""
    amb.oi["emittenti"] = b'[{"ndg": 99908, "descrizione": "ZZQ SINTETICA MILANO NV"}]'
    amb.oi["attivita"] = _fixture("oi_attivita_storico.json")
    amb.voce(negozio="automatico", emarket=4242, riga_listino="Sintetica")
    amb.em_html = _fixture("em_lista_vuota.html")
    r = _dep()
    assert r["stato"] == "non_coperto" and "STORICO" in r["motivo"] and amb.letture == []
    assert r["instradamento"]["oneinfo"]["esito"] == "inattivo"


def test_ambiguo_conta_solo_i_candidati_attivi(amb):
    """Nome corto: due candidati deboli, uno storico e uno attivo: resta ambiguo, il motivo conta 1 attivo."""
    amb.oi["emittenti"] = (b'[{"ndg": 99910, "descrizione": "ASSICURAZIONI FITTIZIE"}, '
                           b'{"ndg": 99911, "descrizione": "BANCA FITTIZIE"}]')
    amb.oi["per_ndg"] = {99910: _fixture("oi_attivita_storico.json")}
    amb.voce(negozio="automatico", emarket=None, riga_listino="Fittizie")
    r = sdir.instrada("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "instradamento_ambiguo"
    assert "candidati ATTIVI su 1INFO: 1 (BANCA FITTIZIE" in r["motivo"] and "storici esclusi: ASSICURAZIONI" in r["motivo"]
