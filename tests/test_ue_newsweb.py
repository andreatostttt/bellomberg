# -*- coding: utf-8 -*-
"""ue_newsweb.py (handoff-3, 05/10/2026, Opus 5.5): data di diffusione delle relazioni
finanziarie norvegesi da Oslo Børs NewsWeb (OAM).

Nessuna rete: `ue_newsweb._post_http` (lo strato requests) e' sostituito da un finto server
sulle fixture SINTETICHE di tests/fixtures/fonti_ue/newsweb_*.json (emittente «ACME Sintetica
ASA», sigla ZZACME, date e id inventati; FORMA misurata il 05/10). La pausa (`_dormi`) e'
registrata, non dormita; la cache va in tmp_path.
"""
import json
import os
from datetime import date

import pytest

from bellomberg.market_data import ue_newsweb as nw

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fonti_ue")
TICKER = "ZZACME.OL"
NOME = "ACME Sintetica ASA"
CHIAVI_CONTRATTO = {
    "ticker", "isin", "lei", "nome", "tipo", "periodo_fine", "stato", "errore", "motivo", "data_deposito",
    "ora_deposito", "fuso", "natura_data", "titolo", "url", "url_documento", "sha256_documento", "categoria",
    "lingua", "candidati", "conferme", "fonte", "paese", "url_liste", "sha256_liste", "risposte_salvate",
    "fonte_modulo", "pagine_lette", "letto_il", "limiti", "cache", "prova", "scartati"}


def _carica(nome):
    with open(os.path.join(FIX, nome), encoding="utf-8") as fh:
        return json.load(fh)


class Server:
    """Risposte per categoria (dict JSON o bytes) + registro delle chiamate."""

    def __init__(self, r1001=None, r1002=None):
        self.r = {1001: r1001 if r1001 is not None else _carica("newsweb_1001.json"),
                  1002: r1002 if r1002 is not None else _carica("newsweb_1002.json")}
        self.chiamate = []
        self.http = 200
        self.errore = None

    def __call__(self, url):
        self.chiamate.append(url)
        if self.errore is not None:
            raise self.errore
        cat = 1001 if "category=1001" in url else 1002
        corpo = self.r[cat]
        if isinstance(corpo, dict):
            corpo = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        return self.http, corpo


@pytest.fixture
def server(monkeypatch, tmp_path):
    s = Server()
    s.pause = []
    monkeypatch.setattr(nw, "_post_http", s)
    monkeypatch.setattr(nw, "_dormi", lambda sec: s.pause.append(sec))
    monkeypatch.setattr(nw, "CACHE_DIR", str(tmp_path / "cache_fonti_ue"))
    monkeypatch.setattr(nw, "_ultima_richiesta", {})
    return s


def _chiama(tipo, fine, **kw):
    kw.setdefault("nome", NOME)
    return nw.get_data_deposito(kw.pop("ticker", TICKER), tipo=tipo, periodo_fine=fine, **kw)


def _msgs(risposta):
    return risposta["data"]["messages"]


# ------------------------------------------------------------------ scelte giuste
def test_annuale_ok_inglese_scelto_norvegese_conferma(server):
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "ok", r["motivo"]
    assert (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-03-18", "08:30", "Europe/Oslo")
    assert r["titolo"] == "ACME Sintetica annual report for 2025" and r["lingua"] == "en"
    assert r["prova"] == "titolo" and r["natura_data"] == "diffusione"
    assert r["url"] == "https://newsweb.oslobors.no/message/900102"
    assert [c["lingua"] for c in r["conferme"]] == ["no"]
    assert r["categoria"] == "ANNUAL FINANCIAL REPORT" and r["paese"] == "NO"
    assert r["fonte_modulo"] == "bellomberg.market_data.ue_newsweb" and "non documentata" in r["fonte"]
    assert len(r["url_liste"]) == 2 and r["pagine_lette"] == 2


def test_annuale_non_prende_i_risultati_q4_sotto_half_year(server):
    """Trappola misurata: il Q4 «full year 2025 results» sta sotto HALF YEAR e cita l'anno, ma
    non e' la relazione annuale: senza il messaggio dell'annual report -> non_trovato."""
    server.r[1001] = _carica("newsweb_vuota.json")
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "non_trovato"
    assert any("full year 2025" in s["titolo"] and "senza il nome" in s["motivo"] for s in r["scartati"])


def test_semestrale_dal_secondo_trimestre_in_categoria_1002(server):
    r = _chiama("semestrale", "2026-06-30")
    assert r["stato"] == "ok", r["motivo"]
    assert r["titolo"] == "ACME Sintetica second quarter 2026 results"
    assert (r["data_deposito"], r["ora_deposito"]) == ("2026-07-21", "07:15")    # 05:15Z = 07:15 a Oslo (CEST)
    assert any("SECONDO TRIMESTRE" in x for x in r["limiti"])


def test_secondo_trimestre_fuori_dalla_1002_non_vale_come_semestrale(server):
    d = _carica("newsweb_1002.json")
    for m in _msgs(d):
        m["category"] = [{"id": 1010, "category_en": "ADDITIONAL REGULATED INFORMATION"}]
    server.r[1002] = d
    assert _chiama("semestrale", "2026-06-30")["stato"] == "non_trovato"


@pytest.mark.parametrize("fine,titolo,data", [
    ("2026-03-31", "ACME Sintetica first quarter 2026 results", "2026-05-05"),
    ("2025-09-30", "ACME Sintetica third quarter 2025 results", "2025-10-28")])
def test_trimestrale_dal_titolo_non_dalla_categoria(server, fine, titolo, data):
    r = _chiama("trimestrale", fine)
    assert r["stato"] == "ok" and r["titolo"] == titolo and r["data_deposito"] == data
    assert r["categoria"] == "HALF YEAR FINANCIAL REPORT"     # categoria sbagliata dell'emittente, ignorata


def test_invito_alla_presentazione_scartato(server):
    r = _chiama("semestrale", "2026-06-30")
    inviti = [s for s in r["scartati"] if s["titolo"].startswith("Invitation")]
    assert inviti and "invito" in inviti[0]["motivo"]
    assert all(not c["titolo"].startswith("Invitation") for c in r["candidati"] + r["conferme"])


def test_rettifica_rende_ambiguo_mai_il_primo(server):
    r = _chiama("annuale", "2024-12-31")
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None
    assert len(r["candidati"]) == 2 and "rettifiche" in r["motivo"]


def test_anno_diverso_scartato_col_motivo(server):
    server.r[1001] = {"header": {"result.val": 0},
                      "data": {"messages": [m for m in _msgs(_carica("newsweb_1001.json")) if "2024" in m["title"]][:1]}}
    server.r[1001]["data"]["messages"][0]["publishedTime"] = "2026-03-02T08:00:00.000Z"
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "non_trovato"
    assert any("anno/periodo diverso" in s["motivo"] for s in r["scartati"])


def test_pubblicato_prima_della_fine_del_periodo_scartato(server):
    d = _carica("newsweb_1001.json")
    m = [x for x in _msgs(d) if x["title"] == "ACME Sintetica annual report for 2025"][0]
    m["publishedTime"] = "2025-12-20T08:00:00.000Z"
    d["data"]["messages"] = [m]
    server.r[1001] = d
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "non_trovato"
    assert any("non dopo la fine del periodo" in s["motivo"] for s in r["scartati"])


# ------------------------------------------------------------------ AGGIUNTA 05/10: prova 'finestra'
def _solo(titolo, pub, cat=1002):
    d = _carica("newsweb_1002.json")
    m = dict(_msgs(d)[0], title=titolo, publishedTime=pub, messageId=900777, id=900777)
    m["category"] = [{"id": cat, "category_en": "HALF YEAR FINANCIAL REPORT"}]
    return m


def test_titolo_senza_anno_entro_finestra_prova_finestra(server):
    server.r[1002] = {"header": {"result.val": 0},
                      "data": {"messages": [_solo("ACME Sintetica half-year report", "2026-08-20T06:00:00.000Z")]}}
    r = _chiama("semestrale", "2026-06-30")
    assert r["stato"] == "ok" and r["prova"] == "finestra"
    assert any("prova='finestra'" in x for x in r["limiti"])


def test_titolo_senza_anno_oltre_la_finestra_del_tipo_scartato(server):
    server.r[1002] = {"header": {"result.val": 0},
                      "data": {"messages": [_solo("ACME Sintetica half-year report", "2026-12-15T06:00:00.000Z")]}}
    r = _chiama("semestrale", "2026-06-30")
    assert r["stato"] == "non_trovato"
    assert any("finestra del tipo" in s["motivo"] for s in r["scartati"])


def test_titolo_e_finestra_con_date_diverse_ambiguo(server):
    d = _carica("newsweb_1002.json")
    _msgs(d).append(_solo("ACME Sintetica half-year report", "2026-08-25T06:00:00.000Z"))
    server.r[1002] = d
    r = _chiama("semestrale", "2026-06-30")
    assert r["stato"] == "ambiguo" and "finestra" in r["motivo"]


# ------------------------------------------------------------------ identita'
def test_nome_diverso_ko_identita_incoerente(server):
    r = _chiama("annuale", "2025-12-31", nome="ACME Sintetica Energy ASA")
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente" and r["data_deposito"] is None


def test_nome_con_forma_giuridica_diversa_coincide(server):
    assert _chiama("annuale", "2025-12-31", nome="Acme sintetica")["stato"] == "ok"


def test_sigla_diversa_nella_risposta_ko(server):
    d = _carica("newsweb_1001.json")
    _msgs(d)[0]["issuerSign"] = "ZZALTRO"
    server.r[1001] = d
    r = _chiama("annuale", "2025-12-31")
    assert r["errore"] == "identita_incoerente" and "ZZALTRO" in r["motivo"]


def test_senza_nome_identita_mancante_senza_rete(server):
    r = _chiama("annuale", "2025-12-31", nome=None, isin="NO00ZZACME07")
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and "NOME" in r["motivo"]
    assert server.chiamate == []


@pytest.mark.parametrize("ticker,tipo,fine", [
    ("ZZACME.MI", "annuale", "2025-12-31"), ("ZZACME.OL", "trimestrale", "2026-06-30"),
    ("ZZACME.OL", "mensile", "2025-12-31"), ("ZZACME.OL", "annuale", "31/12/2025")])
def test_parametri_sbagliati_ko_parametro_senza_rete(server, ticker, tipo, fine):
    r = _chiama(tipo, fine, ticker=ticker)
    assert r["stato"] == "KO" and r["errore"] == "parametro"
    assert server.chiamate == []


def test_chiavi_del_contratto_in_ogni_stato(server):
    stati = [_chiama("annuale", "2025-12-31"), _chiama("annuale", "2024-12-31"),
             _chiama("annuale", "2025-12-31", nome=None), _chiama("annuale", "x")]
    for r in stati:
        assert CHIAVI_CONTRATTO <= set(r), CHIAVI_CONTRATTO - set(r)


# ------------------------------------------------------------------ guasti dichiarati
def test_formato_cambiato_ko(server):
    d = _carica("newsweb_1001.json")
    del _msgs(d)[0]["publishedTime"]
    server.r[1001] = d
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato" and "publishedTime" in r["motivo"]


def test_header_con_errore_ko_non_lista_vuota(server):
    d = _carica("newsweb_1002.json")      # messaggi validi, ma l'header dichiara errore
    d["header"]["result.val"] = 1
    server.r[1002] = d
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato"


def test_overflow_ko_lista_troncata(server):
    d = _carica("newsweb_1002.json")
    d["data"]["overflow"] = True
    server.r[1002] = d
    assert _chiama("annuale", "2025-12-31")["errore"] == "lista_troncata"


def test_lista_vuota_non_trovato_dichiarato(server):
    server.r[1001] = server.r[1002] = _carica("newsweb_vuota.json")
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "non_trovato" and "non trovato in NewsWeb" in r["motivo"]
    assert "mai" not in r["motivo"]
    assert any("identita' non confermabile" in x for x in r["limiti"])


def test_errore_di_rete_solo_il_tipo_mai_il_testo(server):
    server.errore = ConnectionError("https://x/?key=SEGRETO")
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "rete" and "ConnectionError" in r["motivo"]
    assert "SEGRETO" not in json.dumps(r)


def test_http_non_200_ko(server):
    server.http = 503
    r = _chiama("annuale", "2025-12-31")
    assert r["errore"] == "http" and "503" in r["motivo"]


# ------------------------------------------------------------------ rete educata
def test_url_ammessi_solo_host_e_percorso_della_lista(server):
    assert nw.url_ammesso(nw.url_lista("ZZACME", 1001, date(2026, 1, 1), date(2026, 2, 1)))[0]
    for u in ("http://api3.oslo.oslobors.no/v1/newsreader/list", "https://evil.example/v1/newsreader/list",
              "https://api3.oslo.oslobors.no/v1/newsreader/message?messageId=1"):
        assert not nw.url_ammesso(u)[0]
        with pytest.raises(nw.URLVietato):
            nw._scarica(u)
    assert server.chiamate == []


def test_pausa_di_almeno_2_secondi_fra_richieste(server):
    assert nw.PAUSA_S >= 2.0
    _chiama("annuale", "2025-12-31")
    assert len(server.chiamate) == 2 and len(server.pause) == 1 and server.pause[0] > 1.9


# ------------------------------------------------------------------ cache e STALE
def test_cache_fresca_poi_stale_col_guasto(server, monkeypatch):
    primo = _chiama("annuale", "2025-12-31")
    assert primo["stato"] == "ok" and primo["cache"] is None
    secondo = _chiama("annuale", "2025-12-31")
    assert secondo["cache"] == "fresca" and len(server.chiamate) == 2
    monkeypatch.setattr(nw, "TTL_S", -1)
    server.errore = TimeoutError()
    terzo = _chiama("annuale", "2025-12-31")
    assert terzo["stato"] == "STALE" and terzo["stato_originale"] == "ok" and terzo["cache"] == "scaduta"
    assert "TimeoutError" in terzo["motivo"] and terzo["data_deposito"] == "2026-03-18"


# ------------------------------------------------------------------ riverifica senza rete
def test_riverifica_ricevuta_ok_e_manomissioni(server):
    r = _chiama("annuale", "2025-12-31")
    ok, mot = nw.riverifica_ricevuta(r, ticker=TICKER, tipo="annuale", periodo_fine="2025-12-31")
    assert ok, mot
    n = len(server.chiamate)
    falsa = json.loads(json.dumps(r))
    falsa["data_deposito"] = "2026-03-17"
    assert not nw.riverifica_ricevuta(falsa, ticker=TICKER, tipo="annuale", periodo_fine="2025-12-31")[0]
    alterata = json.loads(json.dumps(r))
    u = alterata["url_liste"][0]
    alterata["risposte_salvate"][u] = alterata["risposte_salvate"][u].replace("07:30:01", "06:30:01")
    ok2, mot2 = nw.riverifica_ricevuta(alterata, ticker=TICKER, tipo="annuale", periodo_fine="2025-12-31")
    assert not ok2 and "sha256" in mot2
    assert not nw.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")[0]
    assert len(server.chiamate) == n          # nessuna rete nella riverifica


# ------------------------------------------------------------------ AGGIUNTA 2 (05/10)
def test_nomi_documento_pubblici_compilabili_e_coerenti():
    import re
    assert set(nw.NOMI_DOCUMENTO) == {"annuale", "semestrale", "trimestrale"}
    for tipo, lista in nw.NOMI_DOCUMENTO.items():
        assert lista and all(isinstance(x, str) for x in lista)
        for x in lista:
            re.compile(x, re.I)
    tutto = lambda tipo, t: any(re.search(x, t, re.I) for x in nw.NOMI_DOCUMENTO[tipo])
    assert tutto("annuale", "ACME Sinteticas \u00e5rsrapport for 2025")
    assert tutto("annuale", "ACME Sintetica annual report for 2025")
    assert tutto("semestrale", "ACME Sintetica halv\u00e5rsrapport 2026")
    assert tutto("trimestrale", "ACME Sinteticas resultater for f\u00f8rste kvartal 2026")
    assert not tutto("annuale", "ACME Sintetica fourth quarter and full year 2025 results")


def test_formato_uniforme_di_candidati_e_conferme(server):
    for r in (_chiama("annuale", "2025-12-31"), _chiama("annuale", "2024-12-31")):
        for c in r["candidati"] + r["conferme"]:
            assert {"titolo", "data", "ora", "url", "categoria", "lingua", "prova"} <= set(c)
            assert c["prova"] in ("titolo", "finestra") and len(c["data"]) == 10 and len(c["ora"]) == 5


def test_riverifica_ambiguo_ricalcola_la_lista_dei_candidati(server):
    r = _chiama("annuale", "2024-12-31")
    assert r["stato"] == "ambiguo"
    assert nw.riverifica_ricevuta(r, ticker=TICKER, tipo="annuale", periodo_fine="2024-12-31")[0]
    falsa = json.loads(json.dumps(r))
    falsa["candidati"] = falsa["candidati"][:1]           # lista accorciata: stato e data restano uguali
    ok, mot = nw.riverifica_ricevuta(falsa, ticker=TICKER, tipo="annuale", periodo_fine="2024-12-31")
    assert not ok and "candidati" in mot


# ------------------------------------------------------------------ rilievi RV-UE1 (giro 1)
def _lista(*messaggi):
    return {"header": {"result.val": 0}, "data": {"messages": list(messaggi), "overflow": False}}


def test_rv_no1_lingue_a_un_mese_di_distanza_ambiguo_non_conferma(server):
    """EN «second quarter» il 21/07 + NO «halvårsrapport 2026» il 20/08: non e' lo stesso deposito."""
    server.r[1002] = _lista(_solo("ACME Sintetica second quarter 2026 results", "2026-07-21T05:15:08.700Z"),
                            dict(_solo("ACME Sintetica halv\u00e5rsrapport 2026", "2026-08-20T06:00:00.000Z"),
                                 messageId=900778, id=900778))
    r = _chiama("semestrale", "2026-06-30")
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and "minuti" in r["motivo"]


def test_rv_no2_halvarsrapport_e_semi_annual_non_sono_annuali(server):
    server.r[1002] = _carica("newsweb_vuota.json")
    for titolo in ("ACME Sintetica halv\u00e5rsrapport", "ACME Sintetica semi-annual report"):
        server.r[1001] = _lista(dict(_solo(titolo, "2026-07-10T06:00:00.000Z", cat=1001)))
        assert _chiama("annuale", "2025-12-31")["stato"] == "non_trovato", titolo


def test_rv_no3_lettera_norvegese_nel_nome_non_fa_la_lingua(server):
    en = _solo("ACME L\u00f8ytnant second quarter 2026 results", "2026-07-21T05:15:08.700Z")
    no = dict(_solo("ACME L\u00f8ytnants resultater for andre kvartal 2026", "2026-07-21T05:15:09.700Z"),
              messageId=900778, id=900778)
    server.r[1002] = _lista(en, no)
    r = _chiama("semestrale", "2026-06-30")
    assert r["stato"] == "ok" and r["lingua"] == "en" and [c["lingua"] for c in r["conferme"]] == ["no"]


def test_rv_no5_data_nel_fuso_di_oslo_non_utc(server):
    server.r[1002] = _lista(_solo("ACME Sintetica second quarter 2026 results", "2026-07-21T22:30:00.000Z"))
    r = _chiama("semestrale", "2026-06-30")
    assert (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-07-22", "00:30", "Europe/Oslo")
    assert r["candidati"][0]["pubblicato"] == "2026-07-21T22:30:00.000Z"


def test_rv_p3_nome_trimestrale_pubblico_non_riconosce_le_presentazioni():
    import re
    sa = lambda t: any(re.search(x, t, re.I) for x in nw.NOMI_DOCUMENTO["trimestrale"])
    assert sa("ACME Sintetica first quarter 2026 results") and sa("Q3 2025 report")
    assert not sa("ACME Sintetica Q1 2026 presentation") and not sa("Q1")


def test_rv_no4_riverifica_ricontrolla_gli_url(server):
    r = _chiama("annuale", "2025-12-31")
    args = dict(ticker=TICKER, tipo="annuale", periodo_fine="2025-12-31")
    senza_1002 = json.loads(json.dumps(r))
    senza_1002["url_liste"] = senza_1002["url_liste"][:1]
    assert not nw.riverifica_ricevuta(senza_1002, **args)[0]
    for vecchio, nuovo in (("fromDate=2026-01-01", "fromDate=2026-03-01"),
                           ("https://api3.oslo.oslobors.no", "https://evil.example")):
        f = json.loads(json.dumps(r))
        mappa = {u: u.replace(vecchio, nuovo) for u in f["url_liste"]}
        f["url_liste"] = [mappa[u] for u in f["url_liste"]]
        f["risposte_salvate"] = {mappa[u]: v for u, v in f["risposte_salvate"].items()}
        f["sha256_liste"] = {mappa[u]: v for u, v in f["sha256_liste"].items()}
        ok, mot = nw.riverifica_ricevuta(f, **args)
        assert not ok and "URL" in mot, nuovo


# ------------------------------------------------------------------ sopravvissuti del banco RV-UE1 (N1-N7)
def test_n1_stesso_messaggio_in_due_categorie_e_uno_solo(server):
    d = _carica("newsweb_1001.json")
    server.r[1002] = d                          # le stesse righe anche nella 1002
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "ok" and len(r["candidati"]) == 1 and len(r["conferme"]) == 1


def test_n2_periodo_non_concluso_ko_senza_rete(server):
    r = _chiama("annuale", "2099-12-31")
    assert r["stato"] == "KO" and r["errore"] == "parametro" and "non ancora concluso" in r["motivo"]
    assert server.chiamate == []


def test_n3_riverifica_con_altro_ticker_fallisce(server):
    r = _chiama("annuale", "2025-12-31")
    assert not nw.riverifica_ricevuta(r, ticker="ZZALTRO.OL", tipo="annuale", periodo_fine="2025-12-31")[0]


def test_n4_published_time_senza_z_formato_cambiato(server):
    d = _carica("newsweb_1001.json")
    _msgs(d)[0]["publishedTime"] = "2026-03-18T07:30:03.456"
    server.r[1001] = d
    assert _chiama("annuale", "2025-12-31")["errore"] == "formato_cambiato"


def test_n7_riverifica_confronta_la_prova(server):
    r = _chiama("annuale", "2025-12-31")
    f = json.loads(json.dumps(r))
    f["prova"] = "finestra"
    ok, mot = nw.riverifica_ricevuta(f, ticker=TICKER, tipo="annuale", periodo_fine="2025-12-31")
    assert not ok and "prova" in mot


def test_trimestre_senza_results_report_non_e_candidato(server):
    d = _carica("newsweb_1002.json")
    _msgs(d).append(_solo("ACME Sintetica Q1 2026 trading update", "2026-04-20T06:00:00.000Z"))
    server.r[1002] = d
    r = _chiama("trimestrale", "2026-03-31")
    assert r["stato"] == "ok" and r["titolo"] == "ACME Sintetica first quarter 2026 results"


def test_n3_ricevuta_con_ticker_manomesso_fallisce(server):
    r = _chiama("annuale", "2025-12-31")
    f = json.loads(json.dumps(r))
    f["ticker"] = "ZZALTRO.OL"                 # URL e risposte intatti: solo l'intestazione cambia
    ok, mot = nw.riverifica_ricevuta(f, ticker=TICKER, tipo="annuale", periodo_fine="2025-12-31")
    assert not ok and "ticker" in mot


# ------------------------------------------------------------------ AGGIUNTA 4 punto 1: paese dal router
def test_ag4_ticker_non_ol_instradato_in_norvegia_identita_mancante_senza_rete(server):
    r = _chiama("annuale", "2025-12-31", ticker="ZZACME.DE", paese="NO", isin="NO00ZZACME07")
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and ".OL" in r["motivo"]
    assert server.chiamate == []


def test_ag4_paese_no_con_ticker_ol_funziona(server):
    assert _chiama("annuale", "2025-12-31", paese="NO")["stato"] == "ok"


def test_ag4_paese_diverso_parametro_e_senza_paese_resta_parametro(server):
    assert _chiama("annuale", "2025-12-31", paese="SE")["errore"] == "parametro"
    assert _chiama("annuale", "2025-12-31", ticker="ZZACME.DE")["errore"] == "parametro"
    assert server.chiamate == []


def test_ag4_parametri_sbagliati_restano_parametro_anche_col_paese(server):
    r = _chiama("trimestrale", "2026-06-30", ticker="ZZACME.DE", paese="NO")
    assert r["errore"] == "parametro"


def test_ag4_riverifica_con_paese(server):
    r = _chiama("annuale", "2025-12-31")
    args = dict(tipo="annuale", periodo_fine="2025-12-31")
    assert nw.riverifica_ricevuta(r, ticker=TICKER, paese="NO", **args)[0]
    ok, mot = nw.riverifica_ricevuta(r, ticker=TICKER, paese="SE", **args)
    assert not ok and mot.startswith("parametro") and "NO" in mot
    ok, mot = nw.riverifica_ricevuta(r, ticker="ZZACME.DE", paese="NO", **args)
    assert not ok and mot.startswith("identita_mancante") and ".OL" in mot
