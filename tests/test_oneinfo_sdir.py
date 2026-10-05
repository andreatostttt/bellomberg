# -*- coding: utf-8 -*-
"""oneinfo_sdir.py (handoff-3, 05/10/2026, Opus 5.5): depositi e internal dealing da 1INFO-SDIR.

Nessuna rete: `oneinfo_sdir._richiesta` e' sostituita da un finto server sulle fixture SINTETICHE
tests/fixtures/fonti_it/oi_* (emittente ACME SINTETICA ndg 99901, persone e numeri inventati;
la FORMA delle risposte e' quella misurata dalla sonda S1). Il cablaggio della `_richiesta`
vera (POST, User-Agent, niente redirect, rifiuto prima della rete) e' provato con `requests`
sostituito, mai verso la rete.
"""
import hashlib
import json
import os
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import oneinfo_sdir as oi

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fonti_it")
ISIN_ACME = "ITZZACME0007"
NDG = 99901


def _fixture(nome):
    with open(os.path.join(FIX, nome), "rb") as fh:
        return fh.read()


def _json(nome):
    return json.loads(_fixture(nome).decode("utf-8"))


def _corpo(obj):
    return json.dumps(obj).encode("utf-8")


class Server:
    """Finto 1INFO: risposte per endpoint (bytes | (http, bytes) | eccezione)."""

    def __init__(self):
        self.documenti = _fixture("oi_documenti_ok.json")
        self.mantra = _fixture("oi_comunicati_mantra.json")
        self.testo = _fixture("oi_comunicati_testo.json")
        self.attivita = _fixture("oi_attivita_recente.json")
        self.emittenti = _fixture("oi_emittenti.json")
        self.pdf = {"99901_900020_2026_oneinfo": _fixture("oi_id_iso.pdf"),
                    "99901_900021_2026_oneinfo": _fixture("oi_id_usa.pdf"),
                    "99901_900022_2026_oneinfo": _fixture("oi_id_ambiguo.pdf")}
        self.chieste = []

    def __call__(self, metodo, url, dati=None):
        ok, motivo = oi.richiesta_consentita(metodo, url)
        assert ok, motivo
        self.chieste.append((metodo, url, dict(dati or {})))
        if url == oi.URL_EMITTENTI:
            r = self.emittenti
        elif "PdfShow" in url:
            f = parse_qs(urlparse(url).query)["file"][0][:-4]
            r = self.pdf.get(f, (404, b""))
        elif url == oi.URL_DOCUMENTI:
            r = self.documenti
        elif "SearchFilter[categoria]" in (dati or {}):
            r = self.mantra
        elif "SearchFilter[oggetto]" in (dati or {}):
            r = self.testo
        else:
            r = self.attivita
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, tuple) else (200, r)

    def post(self, endpoint=None):
        return [c for c in self.chieste if c[0] == "POST" and (endpoint is None or c[1] == endpoint)]


def _voce(isin=ISIN_ACME, emarket=None, negozio="confermato", **extra):
    return lambda ticker: (dict({"isin": isin, "emarket": emarket, "negozio": negozio}, **extra), None, None)


@pytest.fixture
def srv(monkeypatch, tmp_path):
    s = Server()
    monkeypatch.setattr(oi, "_richiesta", s)
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(tmp_path / "isin_it.json"))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 5))
    monkeypatch.setattr(bi, "voce_ticker_o_auto", _voce(oneinfo=NDG))
    return s


# ============================================================ A. rete: elenco ammessi, cablaggio, pausa
@pytest.mark.parametrize("metodo,url,atteso", [
    ("POST", oi.URL_DOCUMENTI, True),
    ("POST", oi.URL_COMUNICATI, True),
    ("GET", oi.URL_EMITTENTI, True),
    ("GET", oi.URL_PDF.format(tipo="comunicati", anno=2026, file="99901_900020_2026_oneinfo"), True),
    ("GET", oi.URL_COMUNICATI, False),                       # le API si chiamano solo in POST
    ("POST", oi.URL_EMITTENTI, False),                       # POST solo verso le due API misurate
    ("GET", "https://www.1info.it/PORTALE1INFO/Pdf/Pdf?pdf=x", False),   # involucro HTML, non il PDF
    ("GET", "http://www.1info.it/PORTALE1INFO/API/companies/comunicatistoccati", False),
    ("POST", "https://www.altrosito.it/PORTALE1INFO/API/Documenti", False),
    ("GET", "https://www.1info.it/PdfViewer/PdfShow.aspx?service=&type=comunicati&year=2026&file=../x.pdf&download=1", False),
    ("DELETE", oi.URL_DOCUMENTI, False),
])
def test_richiesta_consentita_solo_endpoint_misurati(metodo, url, atteso):
    assert oi.richiesta_consentita(metodo, url)[0] is atteso


def test_richiesta_vietata_rifiutata_prima_della_rete(monkeypatch):
    import requests

    def esplode(*a, **k):
        raise AssertionError("la rete non doveva partire")
    monkeypatch.setattr(requests, "request", esplode)
    with pytest.raises(bi.URLVietato):
        oi._richiesta("GET", oi.URL_DOCUMENTI)


def test_richiesta_vera_cablata_post_user_agent_senza_redirect(monkeypatch):
    import requests
    visto = {}

    class R:
        status_code, content = 302, b"altrove"

    def finto(metodo, url, **k):
        visto.update(k, metodo=metodo, url=url)
        return R()
    monkeypatch.setattr(requests, "request", finto)
    monkeypatch.setattr(oi, "_ULTIMA", {"t": None})
    http, corpo = oi._richiesta("POST", oi.URL_DOCUMENTI, {"draw": "1"})
    assert (http, corpo) == (302, b"altrove")         # il redirect torna come codice, non si segue
    assert visto["metodo"] == "POST" and visto["allow_redirects"] is False
    assert visto["headers"]["User-Agent"] == bi.USER_AGENT
    assert visto["headers"]["X-Requested-With"] == "XMLHttpRequest" and visto["data"] == {"draw": "1"}
    assert visto["timeout"] == oi.TIMEOUT_S and oi.TIMEOUT_S > 0          # review RV-ON P2-4


def test_pausa_minima_fra_due_richieste(monkeypatch):
    # Cantiere zero rossi 05/10 (TIMING): prima orologio vero e `dormito > 1.4` (bastava un
    # ritardo di 0,1 s dello scheduler fra le due letture per il rosso). Ora orologio finto solo
    # nel modulo: la pausa e' ESATTAMENTE il residuo di PAUSA_S dall'ultima richiesta.
    import types
    assert oi.PAUSA_S >= 1.5
    dormito = []
    monkeypatch.setattr(oi, "time", types.SimpleNamespace(monotonic=lambda: 1000.0, sleep=dormito.append))
    monkeypatch.setattr(oi, "_ULTIMA", {"t": 1000.0})          # richiesta appena partita
    oi._attendi()
    assert dormito == [oi.PAUSA_S]
    monkeypatch.setattr(oi, "_ULTIMA", {"t": 999.75})          # partita 0,25 s fa: solo il residuo
    oi._attendi()
    assert dormito == [oi.PAUSA_S, pytest.approx(oi.PAUSA_S - 0.25)]
    monkeypatch.setattr(oi, "_ULTIMA", {"t": 1000.0 - oi.PAUSA_S})  # pausa gia' trascorsa: nessuna attesa
    oi._attendi()
    assert len(dormito) == 2


def test_redirect_su_post_e_ko_dichiarato(srv):
    srv.documenti = (302, b"")
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "http" and "302" in r["motivo"]


# ============================================================ B. date: ora di Roma, nessuna conversione
def test_data_ora_letta_come_utc_senza_conversione():
    ep = oi.epoch_di(date(2026, 7, 30)) + 14 * 3600 + 5 * 60
    assert oi.data_ora(ep) == ("2026-07-30", "14:05")      # NON 16:05 (Roma estate = UTC+2)
    assert oi.data_ora(None) == (None, None) and oi.data_ora(True) == (None, None)


def test_ora_del_deposito_e_quella_pubblicata(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert (r["data_deposito"], r["ora_deposito"]) == ("2026-08-01", "14:15")


def test_filtro_date_in_epoch_finto_utc(srv):
    oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    (_m, _u, dati), = srv.post(oi.URL_DOCUMENTI)
    assert dati["SearchFilter[dataStoccaggio][from]"] == str(oi.epoch_di(date(2026, 7, 1)))
    assert dati["SearchFilter[dataStoccaggio][to]"] == str(oi.epoch_di(date(2026, 10, 6)))
    assert dati["SearchFilter[emittente]"] == str(NDG)


# ============================================================ C. risposte: righe null, recordsFiltered, layout
def test_righe_null_scartate_e_conteggio_da_recordsfiltered():
    p = oi.parse_risposta(_fixture("oi_documenti_vuoto.json"), NDG)
    assert p["stato"] == "ok" and p["righe"] == [] and p["filtrati"] == 0 and p["righe_null"] == 12


def test_righe_vere_fra_le_null():
    p = oi.parse_risposta(_fixture("oi_comunicati_mantra.json"), NDG)
    assert len(p["righe"]) == 2 == p["filtrati"] and p["righe_null"] == 8


def test_riga_di_altro_emittente_filtro_ignorato_ko():
    j = _json("oi_comunicati_mantra.json")
    j["data"][0]["ndg"] = 12345
    p = oi.parse_risposta(_corpo(j), NDG)
    assert p["stato"] == "KO" and p["errore"] == "filtro_ignorato"


@pytest.mark.parametrize("corpo", [b"<html>errore</html>", _corpo({"draw": 1, "recordsFiltered": 3}),
                                   _corpo({"recordsFiltered": "3", "data": []}), _corpo([1, 2])])
def test_layout_cambiato_ko(corpo):
    p = oi.parse_risposta(corpo, NDG)
    assert p["stato"] == "KO" and p["errore"] == "layout_cambiato"


def test_recordsfiltered_senza_righe_vere_ko(srv):
    j = _json("oi_documenti_vuoto.json")
    j["recordsFiltered"] = 4
    srv.documenti = _corpo(j)
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "layout_cambiato"


def test_paginazione_fino_a_recordsfiltered_poi_troncato(monkeypatch):
    j = _json("oi_comunicati_mantra.json")
    j["recordsFiltered"] = 9
    chiamate = []
    monkeypatch.setattr(oi, "_richiesta", lambda m, u, d=None: (chiamate.append(d["start"]), (200, _corpo(j)))[1])
    r = oi.cerca(oi.URL_COMUNICATI, {"emittente": NDG}, "dataDiffusione", ndg=NDG, length=2, max_pagine=2)
    assert chiamate == ["0", "2"] and r["stato"] == "ok" and r["troncato"] is True and len(r["righe"]) == 4


# ============================================================ D. deposito
def test_annuale_ancora_esef(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and r["prova"] == "esef" and r["esef"] is True and r["consolidato"] == 1
    assert (r["data_deposito"], r["ora_deposito"], r["protocollo"]) == ("2026-03-24", "18:58", "900001_oneinfo")
    assert r["categoria"] == "1.1,3.1" and r["oneinfo_ndg"] == NDG and r["emarket_id"] is None
    assert {c["protocollo"] for c in r["conferme"]} == {"900002_oneinfo", "900003_oneinfo"}
    assert "type=documenti&year=2026&file=900001_oneinfo.pdf" in r["url"]


def test_annuale_senza_esef_regola_del_titolo_e_ambiguo():
    righe = [oi.riga_documento(x) for x in _json("oi_documenti_ok.json")["data"] if x["ndg"]]
    senza = [dict(r, esef=False) for r in righe]
    v = oi.candidati_deposito(senza, "annuale", date(2025, 12, 31))
    assert v["stato"] == "ambiguo" and v["prova"] == "titolo" and len(v["candidati"]) == 2   # ESEF e cortesia
    solo = [r for r in senza if r["protocollo"] != "900001_oneinfo"]
    v = oi.candidati_deposito(solo, "annuale", date(2025, 12, 31))
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "900002_oneinfo"
    assert [c["protocollo"] for c in v["conferme"]] == ["900003_oneinfo"]   # l'inglese conferma


def test_semestrale_italiano_comanda_inglese_conferma(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["prova"] == "titolo" and r["lingua"] == "it"
    assert r["protocollo"] == "900004_oneinfo" and [c["protocollo"] for c in r["conferme"]] == ["900005_oneinfo"]
    assert r["tipo_data"] == "stoccaggio_documento" and r["natura"] == "documento"


def test_trimestrale_resoconto_e_risultati_scartati(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-03-31")
    assert r["stato"] == "ok" and r["protocollo"] == "900007_oneinfo" and r["data_deposito"] == "2026-06-04"
    assert any(s["protocollo"] == "900006_oneinfo" for s in r["scartati"])     # risultati, non la relazione


def test_trimestrale_nome_extra_primo_trimestre():
    riga = {"data": "2026-05-14", "ora": "15:03", "titolo": "Relazione Finanziaria Primo Trimestre 2026",
            "protocollo": "900040_oneinfo", "url_pdf": None, "categoria": "REGEM", "esef": False, "consolidato": None}
    assert oi.candidati_deposito([riga], "trimestrale", date(2026, 3, 31))["stato"] == "ok"


def test_semestrale_nome_e_anno_senza_data():
    riga = {"data": "2026-09-11", "ora": "08:58", "titolo": "Relazione Finanziaria Semestrale 2026",
            "protocollo": "900041_oneinfo", "url_pdf": None, "categoria": "1.2", "esef": False, "consolidato": None}
    assert oi.candidati_deposito([riga], "semestrale", date(2026, 6, 30))["stato"] == "ok"


def test_categoria_sbagliata_non_conta():
    riga = {"data": "2026-08-01", "ora": "14:15", "titolo": "Relazione finanziaria semestrale al 30 giugno 2026",
            "protocollo": "900042_oneinfo", "url_pdf": None, "categoria": "REGEM", "esef": False, "consolidato": None}
    assert oi.candidati_deposito([riga], "semestrale", date(2026, 6, 30))["stato"] == "non_trovato"


def test_finestra_dopo_la_fine_del_periodo():
    righe = [oi.riga_documento(x) for x in _json("oi_documenti_ok.json")["data"] if x["ndg"]]
    v = oi.candidati_deposito(righe, "annuale", date(2026, 12, 31))   # documenti del 2026 sono PRIMA
    assert v["stato"] == "non_trovato"
    lontano = [dict(r, data="2027-06-30") for r in righe]           # oltre FINESTRA_DEPOSITO_GIORNI
    assert oi.candidati_deposito(lontano, "semestrale", date(2026, 6, 30))["stato"] == "non_trovato"


def test_approvazione_scartata_e_doppione_stesso_giorno():
    base = {"ora": "10:00", "url_pdf": None, "categoria": "1.2", "esef": False, "consolidato": None}
    righe = [dict(base, data="2026-07-29", protocollo="a", titolo="Il CdA approva la relazione finanziaria semestrale al 30 giugno 2026"),
             dict(base, data="2026-08-01", protocollo="b", titolo="Relazione finanziaria semestrale al 30 giugno 2026"),
             dict(base, data="2026-08-01", protocollo="c", titolo="Relazione finanziaria semestrale al 30 giugno 2026", ora="10:05")]
    v = oi.candidati_deposito(righe, "semestrale", date(2026, 6, 30))
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "b"
    assert [c["protocollo"] for c in v["conferme"]] == ["c"] and v["scartati"][0]["protocollo"] == "a"


@pytest.mark.parametrize("tipo,fine", [("mensile", "2026-06-30"), ("semestrale", "30/06/2026"),
                                       ("trimestrale", "2026-06-30")])
def test_parametri_senza_rete(srv, tipo, fine):
    r = oi.get_data_deposito("ACME.MI", tipo=tipo, periodo_fine=fine)
    assert r["stato"] == "KO" and r["errore"] == "parametro" and srv.chieste == []


def test_vuoto_con_righe_null_e_emittente_attivo_e_non_trovato(srv):
    srv.documenti = _fixture("oi_documenti_vuoto.json")
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_trovato" and r["candidati"] == []


def test_emittente_storico_non_coperto_mai_non_trovato(srv):
    srv.documenti = _fixture("oi_documenti_vuoto.json")
    srv.attivita = _fixture("oi_attivita_storico.json")
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_coperto" and r["errore"] == "emittente_storico" and "2015-04-30" in r["motivo"]


def test_deposito_rete_ko_poi_stale(srv, monkeypatch):
    srv.documenti = ConnectionError("giu")
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "rete" and "ConnectionError" in r["motivo"]
    srv.documenti = _fixture("oi_documenti_ok.json")
    assert oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")["stato"] == "ok"
    monkeypatch.setattr(oi, "TTL_LISTA_S", -1)
    srv.documenti = ConnectionError("giu")
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "STALE" and r["stato_originale"] == "ok" and r["data_deposito"] == "2026-08-01"
    assert r["cache"]["stato"] == "scaduta_servita"


def test_cache_fresca_nessuna_nuova_richiesta(srv):
    oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    n = len(srv.chieste)
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert len(srv.chieste) == n and r["cache"]["stato"] == "fresca"


def test_troncato_senza_scelta_e_verdetto_sospeso(srv, monkeypatch):
    j = _json("oi_documenti_vuoto.json")
    j["data"][0] = dict(_json("oi_documenti_ok.json")["data"][6])   # solo il verbale, ma 900 dichiarati
    j["recordsFiltered"] = 900
    srv.documenti = _corpo(j)
    monkeypatch.setattr(oi, "MAX_PAGINE", 1)
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "troncato"


def test_ricevuta_della_post(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    (chiave,) = r["url_liste"]
    assert chiave.startswith("POST " + oi.URL_DOCUMENTI + "?") and "emittente=%d" % NDG in chiave
    assert r["sha256_liste"] == {chiave: hashlib.sha256(_fixture("oi_documenti_ok.json")).hexdigest()}
    assert (r["richieste"]["liste"], r["richieste"]["pdf"]) == (1, 0) and r["fonte"] == oi.FONTE
    assert any("uso personale" in x for x in r["limiti"]) and any("STOCCAGGIO" in x for x in r["limiti"])


@pytest.mark.parametrize("voce,errore", [({}, "oneinfo_non_dichiarato"), ({"oneinfo": None}, "dichiarato_non_su_oneinfo")])
def test_ndg_dal_negozio(srv, monkeypatch, voce, errore):
    monkeypatch.setattr(bi, "voce_ticker_o_auto", _voce(**voce))
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_coperto" and r["errore"] == errore and srv.chieste == []
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "non_coperto" and r["errore"] == errore and srv.chieste == []


def test_ndg_passato_dall_instradatore_vince(srv, monkeypatch):
    monkeypatch.setattr(bi, "voce_ticker_o_auto", _voce(oneinfo=None))
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30", ndg=NDG)
    assert r["stato"] == "ok" and r["oneinfo_ndg"] == NDG


def test_ticker_non_mappato_ko(srv, monkeypatch):
    monkeypatch.setattr(bi, "voce_ticker_o_auto", lambda t: (None, "ticker_non_mappato", "assente"))
    assert oi.get_internal_dealing("ZZTEST.MI")["errore"] == "ticker_non_mappato"


# ============================================================ E. internal dealing
def test_id_unione_categoria_e_testo(srv):
    r = oi.get_internal_dealing("ACME.MI", giorni=180)
    assert r["stato"] == "ok" and r["oneinfo_ndg"] == NDG and r["fonte"] == oi.FONTE
    assert [c["protocollo"] for c in r["comunicazioni"]] == [
        "99901_900020_2026_oneinfo", "99901_900021_2026_oneinfo", "99901_900022_2026_oneinfo"]
    a, b, c = r["comunicazioni"]
    assert (a["data"], a["ora"]) == ("2026-09-23", "16:52") and a["categoria"] == "MANTRA"
    assert c["categoria"] == "3.1"                       # trovata SOLO dalla ricerca per testo
    assert a["soggetto"] == "MARIO SINTETICO" and len(a["operazioni"]) == 2 and a["parse_ok"]
    assert b["operazioni"][0]["nota"] and b["operazioni"][0]["data_operazione"] == "2026-08-31"
    assert c["errore"] == "isin_incoerente" and c["operazioni"][0]["isin_coerente"] is False
    assert r["pdf_letti"] == 3 and r["parse_falliti"] == 1 and r["richieste"] == {"liste": 2, "pdf": 3}
    filtri = [d for _m, _u, d in srv.post(oi.URL_COMUNICATI)]
    assert filtri[0]["SearchFilter[categoria]"] == "MANTRA" and filtri[1]["SearchFilter[oggetto]"] == "internal dealing"


def test_id_vuoto_misurato_solo_se_emittente_attivo(srv):
    srv.mantra = srv.testo = _fixture("oi_documenti_vuoto.json").replace(b'"documenti"', b'"comunicati"')
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "vuoto_misurato" and "2026-09-30" in r["motivo"]


def test_id_emittente_storico_non_coperto(srv):
    srv.mantra = srv.testo = _fixture("oi_documenti_vuoto.json")
    srv.attivita = _fixture("oi_attivita_storico.json")
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "non_coperto" and r["errore"] == "emittente_storico"


def test_id_vuoto_con_attivita_non_misurata_ko(srv):
    srv.mantra = srv.testo = _fixture("oi_documenti_vuoto.json")
    srv.attivita = (500, b"")
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "attivita_non_misurata"


def test_id_una_ricerca_ko_niente_meta_lista(srv):
    srv.testo = ConnectionError("giu")
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "rete" and r["comunicazioni"] == []


def test_id_nessuna_operazione_sul_titolo_marcata_non_cancellata(srv):
    """RV-ON P2-2: come eMarket per la singola operazione: tutto resta, marcato e dichiarato."""
    srv.pdf = {k: _fixture("oi_id_ambiguo.pdf") for k in srv.pdf}
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "ok" and len(r["comunicazioni"]) == 3
    assert all(c["errore"] == "isin_incoerente" for c in r["comunicazioni"])
    assert all(o["isin_coerente"] is False and o["quantita"] is None for c in r["comunicazioni"] for o in c["operazioni"])
    assert "nessuna operazione sull'ISIN del book" in r["motivo"] and any("ndg da verificare" in x for x in r["limiti"])


def test_id_pdf_fallito_non_congela_la_cache(srv):
    srv.pdf.pop("99901_900021_2026_oneinfo")
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "ok" and r["pdf_falliti"] == 1 and "HTTP 404" in r["comunicazioni"][1]["motivo_parse"]
    n = len(srv.chieste)
    r2 = oi.get_internal_dealing("ACME.MI")
    assert r2["cache"]["stato"] == "nessuna" and len(srv.chieste) > n     # riletta, non servita dalla cache


def test_id_troncato_dichiarato(srv, monkeypatch):
    j = _json("oi_comunicati_mantra.json")
    j["recordsFiltered"] = 999
    srv.mantra = _corpo(j)
    monkeypatch.setattr(oi, "LUNGHEZZA_ID", 2)
    r = oi.get_internal_dealing("ACME.MI")
    assert r["troncato"] is True and any("lette 4 righe su 999" in x for x in r["limiti"])


def test_id_finestra_giorni(srv):
    r = oi.get_internal_dealing("ACME.MI", giorni=30)
    assert [c["protocollo"] for c in r["comunicazioni"]] == ["99901_900020_2026_oneinfo"]


@pytest.mark.parametrize("giorni", [0, -1, True, "180", 99999])
def test_id_parametro(srv, giorni):
    r = oi.get_internal_dealing("ACME.MI", giorni=giorni)
    assert r["errore"] == "parametro" and srv.chieste == []


def test_id_limiti_dichiarati(srv):
    lim = " ".join(oi.get_internal_dealing("ACME.MI")["limiti"])
    assert "MANTRA" in lim and "ora di Roma" in lim and "uso personale" in lim


# ============================================================ F. parser PDF
def test_pdf_iso_due_operazioni():
    p = oi.parse_pdf_internal_dealing_1info(_fixture("oi_id_iso.pdf"))
    assert p["parse_ok"] and p["ruolo"] == "Amministratore delegato di ACME SINTETICA"
    a, b = p["operazioni"]
    assert (a["tipo_operazione"], a["prezzo"], a["valuta"], a["quantita"], a["data_operazione"], a["isin"]) == \
        ("ACQUISTO/PURCHASE", 12.34, "EUR", 1000, "2026-09-22", ISIN_ACME)
    assert (b["tipo_operazione"], b["prezzo"], b["quantita"]) == ("CESSIONE/DISPOSAL", 13.5, 250)
    assert a["ora_operazione_utc"] is None and a["luogo"] == "MTAA - MERCATO SINTETICO"


def test_pdf_data_usa_univoca_e_prezzo_zero():
    p = oi.parse_pdf_internal_dealing_1info(_fixture("oi_id_usa.pdf"))
    (op,) = p["operazioni"]
    assert p["soggetto"] == "MARIA FITTIZIA" and op["data_operazione"] == "2026-08-31"
    assert op["prezzo"] == 0 and op["quantita"] == 1500 and "NON un acquisto" in op["nota"]


def test_pdf_ambiguo_resta_none_mai_inventato():
    p = oi.parse_pdf_internal_dealing_1info(_fixture("oi_id_ambiguo.pdf"))
    (op,) = p["operazioni"]
    assert p["parse_ok"] is False and op["parse_ok"] is False
    assert op["data_operazione"] is None and op["quantita"] is None and op["prezzo"] is None
    assert "ambigua" in op["motivo"] and "221.821" in op["motivo"]
    assert op["isin"] == "ITZZQQSYN005"


@pytest.mark.parametrize("testo,atteso", [("2026-09-22", "2026-09-22"), ("9/18/2026", "2026-09-18"),
                                          ("18/9/2026", "2026-09-18"), ("5/5/2026", "2026-05-05"),
                                          ("18.09.2026", "2026-09-18"), ("9/10/2026", None),
                                          ("2026-02-30", None), ("ieri", None)])
def test_data_operazione(testo, atteso):
    assert oi.data_operazione(testo)[0] == atteso


def test_pdf_illeggibile_e_senza_sezione_4():
    p = oi.parse_pdf_internal_dealing_1info(b"non e' un pdf")
    assert p["parse_ok"] is False and p["motivo_parse"].startswith("PDF illeggibile")
    p = oi.parse_testo_internal_dealing_1info("a) Nome/First Name X Y\n2 Motivo della notifica\n")
    assert p["parse_ok"] is False and "sezione 4" in p["motivo_parse"] and p["operazioni"] == []


# ============================================================ G. anagrafe e attivita' (per l'instradatore)
@pytest.mark.parametrize("nomi,esito,ndg", [(["Acme Sintetica S.p.A."], "ok", NDG), (["ACME SINTETICA"], "ok", NDG),
                                            (["Doppia Sintetica"], "ambiguo", None),
                                            (["Inesistente Fittizia"], "non_trovato", None),
                                            ([None, ""], "non_trovato", None)])
def test_cerca_emittente(srv, nomi, esito, ndg):
    e = oi.cerca_emittente(nomi)
    assert (e["esito"], e["ndg"]) == (esito, ndg)


def test_anagrafe_layout_cambiato(srv):
    srv.emittenti = b"{}"
    assert oi.cerca_emittente(["Acme Sintetica"])["esito"] == "KO"


def test_attivita_recente_e_storico(srv):
    a = oi.attivita(NDG)
    assert a["stato"] == "ok" and a["attivo"] is True and a["ultimo"] == "2026-09-30"
    srv.attivita = _fixture("oi_attivita_storico.json")
    a = oi._leggi_attivita(NDG)
    assert a["attivo"] is False and a["ultimo"] is None and a["ultimo_terzi"] == "2015-04-30"


# ============================================================ H. review RV-ON
_TESTA = ("1 Dati relativi alla persona\na) Nome/First Name\nMARIO SINTETICO\n2\nMotivo della notifica\n"
          "a) Posizione - Qualifica/Position - Status(1) AMMINISTRATORE\nb) Notifica iniziale\nNuova\n")
_SEZ4 = ("4 Dati relativi all'operazione: sezione da ripetere\n"
         "a) Descrizione dello strumento finanziario, tipo di strumento\nCodice di identificazione/ Identification code\n"
         "AZIONI ORDINARIE\nITZZACME0007\nb) Natura dell'operazione/ Nature of the transaction(5)\nACQUISTO/PURCHASE\n"
         "c) Prezzo/i e volume/i Prezzo Volume\nEUR {p} {v}\nd) Informazioni aggregate/Aggregated information\n"
         "Prezzo Volume\nEUR {p} {v}\ne) Data dell'operazione/Date of the transaction(8)\n{d}\n"
         "f) Luogo dell'operazione/Place of the transaction(9)\nMTAA\n")


@pytest.mark.parametrize("solo_af", [False, True])
def test_formato_word_sezione_4_ripetuta_tutte_le_operazioni(solo_af):
    """RV-ON P1-a: senza «Operazione/Operation - N» la seconda operazione non si perde."""
    seconda = _SEZ4.format(p="12,50", v="4000", d="2026-09-22")
    if solo_af:
        seconda = seconda.split("\n", 1)[1]
    p = oi.parse_testo_internal_dealing_1info(_TESTA + _SEZ4.format(p="12,34", v="1000", d="2026-09-21") + seconda)
    assert [(o["quantita"], o["data_operazione"]) for o in p["operazioni"]] == [(1000, "2026-09-21"), (4000, "2026-09-22")]
    assert p["parse_ok"] is True


def test_due_operazioni_in_un_blocco_mai_parse_ok():
    blocco = _SEZ4.format(p="12,34", v="1000", d="2026-09-21")
    doppio = blocco + "b) Natura dell'operazione\nVENDITA\ne) Data dell'operazione/Date of the transaction(8)\n2026-09-23\n"
    op = oi._parse_operazione_1i(doppio)
    assert op["parse_ok"] is False and "ripetute" in op["motivo"]


@pytest.mark.parametrize("campo", ["pdf", "dataDiffusione"])
def test_campo_sparito_dai_comunicati_ko_mai_vuoto(srv, campo):
    """RV-ON P1-b: un campo rinominato dall'API = layout_cambiato, non «nessuna comunicazione»."""
    for nome in ("mantra", "testo"):
        j = json.loads(getattr(srv, nome).decode("utf-8"))
        for x in j["data"]:
            if x["ndg"]:
                x[campo + "_nuovo"] = x.pop(campo)
        setattr(srv, nome, _corpo(j))
    r = oi.get_internal_dealing("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "layout_cambiato"


@pytest.mark.parametrize("campo", ["dataStoccaggio", "protocolCode"])
def test_campo_sparito_dai_documenti_ko_mai_non_trovato(srv, campo):
    j = _json("oi_documenti_ok.json")
    for x in j["data"]:
        if x["ndg"]:
            x.pop(campo)
            if campo == "protocolCode":
                x.pop("pdf")
    srv.documenti = _corpo(j)
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "layout_cambiato"


def _attivita(righe, filtrati=None):
    j = _json("oi_attivita_recente.json")
    base = dict(j["data"][0])
    j["data"] = [dict(base, dataDiffusione=e, sdir=s) for e, s in righe]
    j["recordsFiltered"] = filtrati if filtrati is not None else len(righe)
    return _corpo(j)


# epoch LETTERALI (oracolo indipendente da epoch_di e dal fuso della macchina):
# 1790757000 = 2026-09-30 08:30 (ora di Roma come pubblicata); 1785420120 = 2026-07-30 14:02
def test_sdir_terzi_non_conta_come_attivita(srv):
    """RV-ON P2-1: comunicati di un altro SDIR solo stoccati su 1INFO non fanno attivo."""
    srv.attivita = _attivita([(1790757000, "SDIR TERZI")])
    a = oi._leggi_attivita(NDG)
    assert a["stato"] == "ok" and a["attivo"] is False and a["ultimo_terzi"] == "2026-09-30"


def test_tutte_terzi_dentro_la_soglia_e_altre_non_lette_ko(srv):
    srv.attivita = _attivita([(1790757000, "SDIR TERZI")], filtrati=500)
    assert oi._leggi_attivita(NDG)["errore"] == "attivita_non_misurata"


def test_comunicato_proprio_vecchio_non_fa_attivo(srv):
    # 1430401299 = 2015-04-30 13:41, diffuso da 1INFO ma oltre GIORNI_ATTIVITA
    srv.attivita = _attivita([(1430401299, "SDIR 1INFO")])
    a = oi._leggi_attivita(NDG)
    assert a["ultimo"] == "2015-04-30" and a["attivo"] is False


def test_ultimo_e_il_massimo_non_il_primo(srv):
    srv.attivita = _attivita([(1785420120, "SDIR 1INFO"), (1790757000, "SDIR 1INFO")])
    assert oi._leggi_attivita(NDG)["ultimo"] == "2026-09-30"


def test_epoch_letterali_indipendenti_dal_fuso():
    assert oi.data_ora(1785420120) == ("2026-07-30", "14:02")
    assert oi.data_ora(1790757000) == ("2026-09-30", "08:30")
    assert oi.epoch_di(date(2026, 7, 1)) == 1782864000
    assert oi.epoch_di(date(2026, 1, 1)) == 1767225600


def test_cache_deposito_per_isin(srv, monkeypatch):
    """RV-ON P3-1: due titoli dello stesso emittente (stesso ndg) non si scambiano l'ISIN."""
    oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    monkeypatch.setattr(bi, "voce_ticker_o_auto", _voce(isin="ITZZQQSYN005", oneinfo=NDG))
    r = oi.get_data_deposito("ACMER.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["isin"] == "ITZZQQSYN005" and r["cache"]["stato"] == "nessuna"


def test_pausa_rispettata_fra_thread(monkeypatch):
    """RV-ON P3-2: due thread non partono insieme (lock); fra processi: limite dichiarato."""
    import threading
    import requests
    partenze = []

    class R:
        status_code, content = 200, b"{}"

    def finto(*a, **k):
        partenze.append(time.monotonic())
        time.sleep(0.2)        # richiesta lenta: senza lock il secondo thread partirebbe durante la prima
        return R()
    monkeypatch.setattr(requests, "request", finto)
    monkeypatch.setattr(oi, "_ULTIMA", {"t": None})
    monkeypatch.setattr(oi, "PAUSA_S", 0.3)
    t = [threading.Thread(target=oi._richiesta, args=("GET", oi.URL_EMITTENTI)) for _ in range(2)]
    for x in t:
        x.start()
    for x in t:
        x.join()
    assert len(partenze) == 2 and abs(partenze[1] - partenze[0]) >= 0.29


def test_consolidato_dichiarato_come_flag_della_fonte(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert any("NON prova che il documento sia il bilancio consolidato" in x for x in r["limiti"])


# ============================================================ I. fine_esercizio
def test_non_solare_semestrale_non_supportato_senza_rete(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-10-31", fine_esercizio="04-30")
    assert r["stato"] == "KO" and r["errore"] == "parametro" and r["parametro_non_supportato"] is True
    assert "non supportato da 1INFO" in r["motivo"] and srv.chieste == []


def test_non_solare_incoerente_parametro(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31", fine_esercizio="04-30")
    assert r["errore"] == "parametro" and "incoerente" in r["motivo"] and srv.chieste == []


def test_non_solare_annuale_supportato_legge(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2026-04-30", fine_esercizio="04-30")
    assert r["errore"] != "parametro" and len(srv.post(oi.URL_DOCUMENTI)) == 1


def test_solare_esplicito_come_default(srv):
    r = oi.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30", fine_esercizio="12-31")
    assert r["stato"] == "ok" and r["protocollo"] == "900004_oneinfo"


# ============================================================ J. abbreviazioni del Listino e abbinamenti stretti (main 05/10, caso BDB)
def _lista(monkeypatch, *nomi):
    em = [{"ndg": 99950 + i, "nome": n} for i, n in enumerate(nomi)]
    monkeypatch.setattr(oi, "anagrafe", lambda: {"stato": "ok", "emittenti": em, "cache": None, "motivo": None})


def test_bco_espanso_e_parole_vuote_ignorate(monkeypatch):
    _lista(monkeypatch, "BANCO DI SINTETICO E DELLA BRIANZA", "BANCA SINTETICA", "ALTRO EMITTENTE")
    e = oi.cerca_emittente(["Bco Sintetico Brianza"], ["nome listino"])
    assert e["esito"] == "ok" and e["ndg"] == 99950 and e["per_nome"][0]["gradino"] == "espanso"
    assert e["per_nome"][0]["espanso"] == ["BANCO SINTETICO BRIANZA"]


def test_abbreviazione_con_due_letture_su_due_emittenti_ambiguo(monkeypatch):
    _lista(monkeypatch, "INDUSTRIE SINTETICHE", "INDUSTRIA SINTETICHE")
    e = oi.cerca_emittente(["Ind Sintetiche"])
    assert e["esito"] == "ambiguo" and e["ndg"] is None


def test_nome_corto_comune_a_due_emittenti_resta_ambiguo(monkeypatch):
    """Caso misurato 05/10: il nome corto sta dentro due nomi 1INFO che cominciano con altre parole."""
    _lista(monkeypatch, "ASSICURAZIONI FITTIZIE", "BANCA FITTIZIE")
    assert oi.cerca_emittente(["Fittizie"])["esito"] == "ambiguo"


def test_abbinamento_debole_da_solo_non_basta(monkeypatch):
    """Caso «Reti» -> «CDP RETI»: un solo emittente ma con un'altra prima parola = ambiguo dichiarato."""
    _lista(monkeypatch, "ZZQ RETI SINTETICHE")
    e = oi.cerca_emittente(["Reti Sintetiche"])
    assert e["esito"] == "ambiguo" and e["per_nome"][0]["gradino"] == "debole"


@pytest.mark.parametrize("cercato,atteso", [("Mare Fittizia Engineering", "non_trovato"),   # parole in piu' non generiche
                                            ("Banca Zetafin", "ok"),                         # «Banca» in piu': generica
                                            ("Zetafin", "ok")])                              # stessa prima parola
def test_nome_1info_contenuto_nel_cercato_solo_con_parole_generiche(monkeypatch, cercato, atteso):
    _lista(monkeypatch, "ENGINEERING", "ZETAFIN")
    assert oi.cerca_emittente([cercato])["esito"] == atteso


# ============================================================ K. ricevuta con risposte salvate e riverifica senza rete (P1 di RV-D4)
def _ricevuta(srv, tipo="semestrale", fine="2026-06-30"):
    return oi.get_data_deposito("ACME.MI", tipo=tipo, periodo_fine=fine)


def _riv(r, tipo="semestrale", fine="2026-06-30", ticker="ACME.MI"):
    return oi.riverifica_deposito(json.loads(json.dumps(r)), ticker=ticker, tipo=tipo, periodo_fine=fine)


def test_risposte_salvate_gzip_e_sha_sul_corpo_non_compresso(srv):
    r = _ricevuta(srv)
    (chiave,) = r["url_liste"]
    sal = r["risposte_salvate"][chiave]
    assert sal["codifica"] == "gzip+base64"
    assert oi.decomprimi(sal) == _fixture("oi_documenti_ok.json")
    assert r["sha256_liste"][chiave] == hashlib.sha256(_fixture("oi_documenti_ok.json")).hexdigest()


@pytest.mark.parametrize("tipo,fine", [("semestrale", "2026-06-30"), ("annuale", "2025-12-31"),
                                       ("trimestrale", "2026-03-31")])
def test_riverifica_ok_senza_rete(srv, monkeypatch, tipo, fine):
    r = _ricevuta(srv, tipo, fine)
    assert r["stato"] == "ok"
    monkeypatch.setattr(oi, "_richiesta", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rete")))
    ok, motivo = _riv(r, tipo, fine)
    assert ok, motivo


def test_riverifica_non_trovato_e_ambiguo(srv):
    j = _json("oi_documenti_ok.json")
    for x in j["data"]:
        if x.get("protocolCode") == "900001_oneinfo":
            x["protocolCodeXbrl"] = None            # niente ESEF: annuale ambiguo (cortesia + ex ESEF)
    srv.documenti = _corpo(j)
    r = _ricevuta(srv, "annuale", "2025-12-31")
    assert r["stato"] == "ambiguo" and _riv(r, "annuale", "2025-12-31")[0]
    srv.documenti = _fixture("oi_documenti_vuoto.json")
    r = oi.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-09-30")
    assert r["stato"] == "non_trovato" and _riv(r, "trimestrale", "2026-09-30")[0]


@pytest.mark.parametrize("campo,valore", [("data_deposito", "2026-08-02"), ("ora_deposito", "09:00"),
                                          ("protocollo", "900005_oneinfo"), ("titolo", "Altro"), ("stato", "ambiguo"),
                                          ("prova", "esef"), ("url", None)])
def test_manomissione_dei_campi_con_sha_coerenti_cade(srv, campo, valore):
    """IL P1 di RV-D4: una data (o altro campo) falsificata con gli sha intatti NON ripassa."""
    r = _ricevuta(srv)
    r[campo] = valore
    ok, motivo = _riv(r)
    assert ok is False and campo in motivo


def test_corpo_manomesso_senza_aggiornare_lo_sha_cade(srv):
    r = _ricevuta(srv)
    (chiave,) = r["url_liste"]
    falso = _fixture("oi_documenti_ok.json").replace(b"Relazione finanziaria semestrale", b"Relazione finanziaria semestrale bis")
    r["risposte_salvate"][chiave] = oi.comprimi(falso)
    ok, motivo = _riv(r)
    assert ok is False and "sha256" in motivo


def test_corpo_e_sha_manomessi_ma_data_vecchia_cade(srv):
    """Chi riscrive corpo E sha ma non la scelta: la scelta rifatta differisce."""
    r = _ricevuta(srv)
    (chiave,) = r["url_liste"]
    j = _json("oi_documenti_ok.json")
    for x in j["data"]:
        if x.get("protocolCode") == "900004_oneinfo":
            x["dataStoccaggio"] += 86400
    falso = _corpo(j)
    r["risposte_salvate"][chiave] = oi.comprimi(falso)
    r["sha256_liste"][chiave] = hashlib.sha256(falso).hexdigest()
    ok, motivo = _riv(r)
    assert ok is False and "data_deposito" in motivo


def test_risposta_di_un_altro_periodo_o_emittente_cade(srv):
    r = _ricevuta(srv)
    (chiave,) = r["url_liste"]
    altra = chiave.replace("dataStoccaggio.from=%d" % oi.epoch_di(date(2026, 7, 1)),
                           "dataStoccaggio.from=%d" % oi.epoch_di(date(2026, 4, 1)))
    assert altra != chiave
    for k in ("risposte_salvate", "sha256_liste"):
        r[k] = {altra: r[k][chiave]}
    r["url_liste"] = [altra]
    ok, motivo = _riv(r)
    assert ok is False and "non e' la richiesta" in motivo
    r2 = _ricevuta(srv)
    r2["oneinfo_ndg"] = 12345
    assert _riv(r2)[0] is False


def test_candidati_manomessi_cadono(srv):
    r = _ricevuta(srv)
    r["candidati"] = r["candidati"] + [dict(r["candidati"][0], protocollo="900099_oneinfo")]
    ok, motivo = _riv(r)
    assert ok is False and "candidati" in motivo


@pytest.mark.parametrize("guasto", ["senza_risposte", "chiavi_diverse", "codifica", "ticker", "periodo", "stato_ko"])
def test_ricevute_non_riverificabili_dichiarate(srv, guasto):
    r = _ricevuta(srv)
    kw = {}
    if guasto == "senza_risposte":
        r.pop("risposte_salvate")
    elif guasto == "chiavi_diverse":
        r["url_liste"] = r["url_liste"] + ["POST extra"]
    elif guasto == "codifica":
        (chiave,) = r["url_liste"]
        r["risposte_salvate"][chiave] = {"codifica": "gzip+base64", "corpo": "non-base64!!"}
    elif guasto == "ticker":
        kw["ticker"] = "ZZTEST.MI"
    elif guasto == "periodo":
        kw["fine"] = "2026-12-31"
    else:
        r["stato"] = "KO"
    ok, motivo = _riv(r, **kw)
    assert ok is False and motivo


def test_risposta_di_un_altro_emittente_con_righe_del_giusto_cade(srv):
    """La chiave della richiesta dice un altro emittente anche se il corpo e' coerente."""
    r = _ricevuta(srv)
    (chiave,) = r["url_liste"]
    altra = chiave.replace("emittente=%d" % NDG, "emittente=99999")
    for k in ("risposte_salvate", "sha256_liste"):
        r[k] = {altra: r[k][chiave]}
    r["url_liste"] = [altra]
    ok, motivo = _riv(r)
    assert ok is False and "non e' la richiesta" in motivo


# ============================================================ L. casi veri di MF (05/10), in forma sintetica
def _r(data, ora, titolo, cat, prot, esef=False, cons=None):
    return {"data": data, "ora": ora, "titolo": titolo, "protocollo": prot, "url_pdf": None, "categoria": cat,
            "esef": esef, "consolidato": cons}


@pytest.mark.parametrize("titolo_revisore", [
    "Relazione della Societa' di Revisione sulla Relazione finanziaria semestrale al 30 giugno 2026",
    "Relazione di revisione contabile limitata sul bilancio consolidato semestrale abbreviato al 30 giugno 2026",
    "Review report on the interim condensed consolidated financial statements as at June 30, 2026"])
def test_relazione_del_revisore_non_e_candidato(titolo_revisore):
    righe = [_r("2026-07-31", "13:52", "Relazione finanziaria semestrale al 30 giugno 2026", "1.2", "a"),
             _r("2026-07-31", "13:54", titolo_revisore, "1.2", "b")]
    v = oi.candidati_deposito(righe, "semestrale", date(2026, 6, 30))
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "a"
    assert any(s["protocollo"] == "b" and "revisione" in s["motivo"] for s in v["scartati"])


def test_revisore_in_coda_al_titolo_resta_il_documento():
    righe = [_r("2026-07-30", "13:58", "Relazione finanziaria semestrale al 30 giugno 2026 e relativa relazione "
                                       "della societa' di revisione", "1.2", "a")]
    assert oi.candidati_deposito(righe, "semestrale", date(2026, 6, 30))["stato"] == "ok"


def test_errata_corrige_vale_la_data_dell_originale():
    righe = [_r("2026-04-07", "16:32", "Fascicolo di bilancio al 31.12.2025 - ESEF", "1.1,REGEM", "orig", True),
             _r("2026-04-10", "09:52", "Errata corrige - Fascicolo di bilancio al 31.12.2025 - ESEF", "1.1,REGEM", "err", True)]
    v = oi.candidati_deposito(righe, "annuale", date(2025, 12, 31))
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "orig" and v["prova"] == "esef"
    assert [c["protocollo"] for c in v["conferme"] if c.get("rettifica")] == ["err"] and "originale" in v["nota"]


def test_rettifica_precedente_all_originale_resta_ambigua():
    righe = [_r("2026-08-01", "10:00", "Relazione finanziaria semestrale al 30 giugno 2026 - rettifica", "1.2", "r"),
             _r("2026-08-05", "10:00", "Relazione finanziaria semestrale al 30 giugno 2026", "1.2", "o")]
    assert oi.candidati_deposito(righe, "semestrale", date(2026, 6, 30))["stato"] == "ambiguo"


def test_avviso_di_deposito_diventa_conferma_e_vale_il_documento(srv):
    j = _json("oi_documenti_vuoto.json")
    j["data"][0] = dict(_json("oi_documenti_ok.json")["data"][4], protocolCode="900060_oneinfo", pdf="900060_oneinfo",
                        oggetto="Deposito informazioni periodiche aggiuntive al 31 marzo 2026",
                        dataStoccaggio=oi.epoch_di(date(2026, 5, 15)) + 18 * 3600)
    j["data"][1] = dict(_json("oi_documenti_ok.json")["data"][4], protocolCode="900061_oneinfo", pdf="900061_oneinfo",
                        oggetto="Informazioni periodiche aggiuntive al 31 marzo 2026 Gruppo Acme Sintetica",
                        dataStoccaggio=oi.epoch_di(date(2026, 5, 21)) + 16 * 3600)
    j["recordsFiltered"] = 2
    srv.documenti = _corpo(j)
    r = oi.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-03-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-05-21" and r["protocollo"] == "900061_oneinfo"
    cc = r["comunicato_conferma"]
    assert cc["protocollo"] == "900060_oneinfo" and cc["scarto_giorni"] == -6 and cc["natura"] == "avviso_di_deposito"
    assert any("scarto -6 giorni" in x for x in r["limiti"])
    assert oi.riverifica_deposito(json.loads(json.dumps(r)), ticker="ACME.MI", tipo="trimestrale",
                                  periodo_fine="2026-03-31")[0]
    r["comunicato_conferma"] = None
    ok, motivo = oi.riverifica_deposito(r, ticker="ACME.MI", tipo="trimestrale", periodo_fine="2026-03-31")
    assert ok is False and "comunicato_conferma" in motivo


@pytest.mark.parametrize("cercato,lista,atteso", [
    ("Qqventus Fc", ["QQVENTUS FOOTBALL CLUB"], "ok"),
    ("Caltasint Edit", ["CALTASINT EDITORE", "CALTASINT"], "ok"),
    ("Mondasint Edit", ["ARNOLDO MONDASINT EDITORE"], "non_trovato")])   # prima parola diversa: non abbinato
def test_abbreviazioni_fc_ed_edit(monkeypatch, cercato, lista, atteso):
    _lista(monkeypatch, *lista)
    e = oi.cerca_emittente([cercato])
    assert e["esito"] == atteso
    if atteso == "ok":
        assert e["nome_1info"] == lista[0]
