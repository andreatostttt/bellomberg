# -*- coding: utf-8 -*-
"""Test di market_data/ue_fsma.py (FSMA STORI, OAM Belgio). Fixture SINTETICHE (emittente «ACME
SINTETICA», ISIN/LEI inventati ma validi), nessuna rete: requests.post e la pausa sono finti."""
import copy
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from bellomberg.market_data import ue_fsma as m

FIX = Path(__file__).parent / "fixtures" / "fonti_ue"
ISIN = "BE00ZZSYNT07"
ALTRO_ISIN = "BE00ZZSYNT15"
LEI = "ZZTEST00000000ACME38"
ALTRO_LEI = "ZZTEST00000000ALTR81"


def _fix(nome):
    return json.loads((FIX / nome).read_text(encoding="utf-8"))


def _voce(categoria, data_pub, titoli, *, isin=ISIN, lei=LEI, ricevuta=None, n=1):
    return {"requiredReportingTopicId": "00000000-0000-4000-8000-%012d" % (900 + n),
            "companyName": "ACME SINTETICA", "companyNumber": "0000000001", "nationality": "BE",
            "reportingTopicName": categoria, "datePublication": data_pub,
            "dateReceived": ricevuta if ricevuta is not None else data_pub, "lei": lei,
            "mainDocuments": [{"fileDataId": "00000000-0000-4000-8000-%012d" % (n * 100 + i), "language": "en",
                               "title": t, "originalFileName": t, "size": 1, "fileType": "pdf"}
                              for i, t in enumerate(titoli)],
            "attachments": [], "isinCodes": [{"code": isin}], "documentTitle": ""}


def _risposta(voci, totale=None):
    return {"resultCount": len(voci) if totale is None else totale, "storiResultItems": voci}


class _Finto:
    def __init__(self, status_code, content):
        self.status_code, self.content = status_code, content


@pytest.fixture
def rete(monkeypatch, tmp_path):
    """requests.post finto: risponde con `rete.risposte` (dict/bytes/eccezione) in ordine e
    registra le chiamate; la pausa si registra senza dormire; cache in tmp_path."""
    import requests

    class R:
        risposte = []
        chiamate = []
        pause = []

    def _post(url, json=None, **kw):
        R.chiamate.append({"url": url, "corpo": json, "kw": kw})
        r = R.risposte.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, tuple):
            return _Finto(*r)
        corpo = r if isinstance(r, bytes) else __import__("json").dumps(r).encode("utf-8")
        return _Finto(200, corpo)

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(m, "_dormi", lambda s: R.pause.append(s))
    monkeypatch.setattr(m, "_ultima_richiesta", {})
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path / "cache_ue"))
    return R


def _chiama(tipo="semestrale", fine="2026-06-30", **kw):
    kw.setdefault("isin", ISIN)
    return m.get_data_deposito("ZZTEST.BR", tipo=tipo, periodo_fine=fine, **kw)


# ---------------------------------------------------------------- caso felice
def test_semestrale_ok_nome_e_periodo_nel_file(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama(lei=LEI)
    assert r["stato"] == "ok" and r["prova"] == "titolo"
    assert (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-07-29", "07:00", "Europe/Brussels")
    assert r["natura_data"] == "pubblicazione_dichiarata"
    assert (r["data_ricezione"], r["ora_ricezione"]) == ("2026-07-29", "07:31")
    assert r["titolo"] == "ACME HY26 Financial Statements.pdf" and r["lingua"] == "en"
    assert r["url"] == r["url_documento"] == ("https://webapi.fsma.be/api/v1/en/stori/download?fileDataId="
                                              "00000000-0000-4000-8000-0000000000b3")
    assert r["categoria"] == "Half-yearly financial report" and r["sha256_documento"] is None
    assert len(r["candidati"]) == 1 and len(r["conferme"]) == 2     # nl e fr dello stesso deposito
    # la richiesta: un POST all'URL ammesso, ISIN e finestra semestrale (150 g) nel corpo
    (c,) = rete.chiamate
    assert c["url"] == m.URL_RISULTATI and c["kw"]["allow_redirects"] is False
    assert c["corpo"]["isinCode"] == ISIN
    assert (c["corpo"]["publicationStart"], c["corpo"]["publicationEnd"]) == ("2026-06-30", "2026-11-28")
    # ricevuta: corpo grezzo + sha256 del corpo
    (k,) = r["url_liste"]
    assert hashlib.sha256(r["risposte_salvate"][k].encode("utf-8")).hexdigest() == r["sha256_liste"][k]
    assert r["fonte_modulo"] == "bellomberg.market_data.ue_fsma" and r["paese"] == "BE"
    assert any("non documentata" in x for x in r["limiti"]) and any("DICHIARATE" in x for x in r["limiti"])


def test_annuale_esef_si_comunicato_annuale_no(rete):
    rete.risposte = [_fix("fsma_annuale.json")]
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-03-20" and r["ora_deposito"] == "07:30"
    assert r["titolo"] == "ZZTEST00000000ACME38-2025-12-31-0-en.xhtml"
    assert r["data_ricezione"] == "2026-03-27"          # ESEF ricevuto una settimana dopo: riportato a parte


def test_trimestrale_comunicato_col_trimestre(rete):
    rete.risposte = [_risposta([_voce("Quarterly information", "2026-05-06T07:00:00",
                                      ["1Q26_ACME_Press Release ENG.pdf"])])]
    r = _chiama("trimestrale", "2026-03-31")
    assert r["stato"] == "ok" and r["prova"] == "titolo" and r["data_deposito"] == "2026-05-06"


# ---------------------------------------------------------------- regola NOME + PERIODO
def test_categoria_sola_non_basta(rete):
    rete.risposte = [_risposta([_voce("Half-yearly financial report", "2026-07-29T07:00:00",
                                      ["ACME_Press Release_English.pdf"])])]
    r = _chiama()
    assert r["stato"] == "non_trovato" and r["data_deposito"] is None
    assert "categoria" in r["scartati"][0]["motivo"] and "non basta" in r["scartati"][0]["motivo"]


def test_nome_con_altro_anno_scartato(rete):
    rete.risposte = [_risposta([_voce("Half-yearly financial report", "2026-08-02T08:00:00",
                                      ["Halfjaarverslag 2025 ACME.pdf"])])]
    r = _chiama()
    assert r["stato"] == "non_trovato"
    assert "periodo/anno diverso" in r["scartati"][0]["motivo"]


def test_nome_senza_anno_prova_finestra(rete):
    rete.risposte = [_risposta([_voce("Other", "2026-08-02T08:00:00", ["Rapport financier semestriel ACME.pdf"])])]
    r = _chiama()
    assert r["stato"] == "ok" and r["prova"] == "finestra" and r["data_deposito"] == "2026-08-02"
    assert any("senza anno" in x for x in r["limiti"])


def test_finestra_rispettata_per_tipo(rete):
    # trimestrale: finestra 120 g dal 31/03 -> fino al 29/07; il 30/07 e' fuori
    rete.risposte = [_risposta([_voce("Quarterly information", "2026-07-30T07:00:00", ["Trading update ACME.pdf"])])]
    r = _chiama("trimestrale", "2026-03-31")
    assert r["stato"] == "non_trovato" and "oltre la finestra di 120" in r["scartati"][0]["motivo"]


def test_pubblicato_il_giorno_della_fine_scartato(rete):
    rete.risposte = [_risposta([_voce("Half-yearly financial report", "2026-06-30T18:00:00",
                                      ["ACME HY26 Financial Statements.pdf"])])]
    r = _chiama()
    assert r["stato"] == "non_trovato" and "non dopo la fine del periodo" in r["scartati"][0]["motivo"]


def test_titolo_e_finestra_insieme_ambiguo(rete):
    rete.risposte = [_risposta([
        _voce("Half-yearly financial report", "2026-07-29T07:00:00", ["ACME HY26 Financial Statements.pdf"], n=1),
        _voce("Half-yearly financial report", "2026-08-20T07:00:00", ["Halfjaarverslag ACME.pdf"], n=2)])]
    r = _chiama()
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and len(r["candidati"]) == 2
    assert {c["prova"] for c in r["candidati"]} == {"titolo", "finestra"}


def test_due_depositi_col_periodo_ambiguo(rete):
    rete.risposte = [_risposta([
        _voce("Half-yearly financial report", "2026-07-29T07:00:00", ["ACME HY26 Financial Statements.pdf"], n=1),
        _voce("Half-yearly financial report", "2026-07-31T09:00:00",
              ["ACME HY26 Financial Statements corrected.pdf"], n=2)])]
    r = _chiama()
    assert r["stato"] == "ambiguo" and r["candidati"][1]["rettifica"] is True


def test_nomi_specifici_vincono_sui_generici():
    assert m.tipi_nel_nome("ACME semi-annual report 2026") == ["semestrale"]
    assert m.tipi_nel_nome("HY26 ACME consolidated financial statements") == ["semestrale"]
    assert m.tipi_nel_nome("ACME halfjaarverslag 2026") == ["semestrale"]   # non «jaarverslag»
    assert m.tipi_nel_nome("ZZTEST00000000ACME38-2025-12-31-0-en") == ["annuale"]
    assert m.tipi_nel_nome("HY26 ACME Press Release") == []                 # comunicato: nessun nome
    assert m.tipi_nel_nome("2Q26 ACME Press Release") == []                 # Q2 non e' una trimestrale


# ---------------------------------------------------------------- parametri e identita'
def test_identita_mancante_nessuna_rete(rete):
    r = _chiama(isin=None)
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and "isin" in r["motivo"]
    assert rete.chiamate == []


def test_isin_o_lei_non_validi_nessuna_rete(rete):
    assert _chiama(isin="BE00ZZSYNT08")["errore"] == "identita_non_valida"
    assert _chiama(lei="ZZTEST00000000ACME39")["errore"] == "identita_non_valida"
    assert rete.chiamate == []


def test_parametri_incoerenti(rete):
    assert _chiama("semestrale", "2026-03-31")["errore"] == "parametro"
    assert _chiama("mensile", "2026-03-31")["errore"] == "parametro"
    assert _chiama("annuale", "2026-02-30")["errore"] == "parametro"
    assert rete.chiamate == []


def test_stesse_chiavi_in_ogni_stato(rete):
    rete.risposte = [_fix("fsma_semestrale.json"), _risposta([]), b"<html>errore</html>"]
    stati = [_chiama(), _chiama("annuale", "2025-12-31"), _chiama("trimestrale", "2026-03-31"),
             _chiama(isin=None), _chiama("semestrale", "2026-04-30")]
    assert [s["stato"] for s in stati] == ["ok", "non_trovato", "KO", "KO", "KO"]
    chiavi = set(stati[0])
    assert all(set(s) == chiavi for s in stati)
    assert {"prova", "scartati", "data_ricezione", "natura_data", "risposte_salvate"} <= chiavi


def test_vuoto_e_non_trovato_mai_mai_pubblicato(rete):
    rete.risposte = [_risposta([])]
    r = _chiama()
    assert r["stato"] == "non_trovato" and "non trovato in FSMA STORI fra il 2026-07-01 e il 2026-11-27" in r["motivo"]
    assert "mai" not in r["motivo"]


# ---------------------------------------------------------------- formato e guasti
def test_fuso_esplicito_e_formato_cambiato(rete):
    d = _fix("fsma_semestrale.json")
    d["storiResultItems"][1]["datePublication"] = "2026-07-29T07:00:00Z"
    rete.risposte = [d]
    r = _chiama()
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato" and r["data_deposito"] is None


def test_chiavi_mancanti_formato_cambiato(rete):
    rete.risposte = [{"count": 1, "items": []}]
    assert _chiama()["errore"] == "formato_cambiato"


def test_filtro_isin_ignorato_ko(rete):
    rete.risposte = [_risposta([_voce("Half-yearly financial report", "2026-07-29T07:00:00",
                                      ["ACME HY26 Financial Statements.pdf"], isin=ALTRO_ISIN)])]
    r = _chiama()
    assert r["stato"] == "KO" and r["errore"] == "filtro_ignorato"


def test_lei_diverso_ko(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama(lei=ALTRO_LEI)
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente"


def test_lista_troncata_ko(rete):
    rete.risposte = [_risposta([_voce("Half-yearly financial report", "2026-07-29T07:00:00",
                                      ["ACME HY26 Financial Statements.pdf"])], totale=250)]
    r = _chiama()
    assert r["stato"] == "KO" and r["errore"] == "troncato" and r["data_deposito"] is None


def test_http_e_rete_ko_senza_testo_eccezione(rete):
    rete.risposte = [(503, b"down"), ConnectionError("https://webapi.fsma.be/segreto?x=1")]
    a, b = _chiama(), _chiama("annuale", "2025-12-31")
    assert (a["errore"], b["errore"]) == ("http", "rete")
    assert "503" in a["motivo"] and b["motivo"].endswith("ConnectionError") and "segreto" not in b["motivo"]


def test_url_ammesso():
    assert m.url_ammesso(m.URL_RISULTATI)[0]
    assert not m.url_ammesso("http://webapi.fsma.be/api/v1/en/stori/result")[0]
    assert not m.url_ammesso("https://www.fsma.be/api/v1/en/stori/result")[0]
    assert not m.url_ammesso("https://webapi.fsma.be/api/v1/en/stori/download")[0]
    assert not m.url_ammesso(m.URL_RISULTATI + "?x=1")[0]


# ---------------------------------------------------------------- ritmo e cache
def test_pausa_30_secondi_fra_richieste(rete):
    rete.risposte = [_risposta([]), _risposta([])]
    _chiama()
    _chiama("annuale", "2025-12-31")
    assert rete.pause and rete.pause[-1] > 29 and m.PAUSA_S >= 30


def test_cache_fresca_poi_stale_su_guasto(rete, monkeypatch):
    rete.risposte = [_fix("fsma_semestrale.json")]
    primo = _chiama()
    secondo = _chiama()
    assert secondo["cache"] == "fresca" and len(rete.chiamate) == 1
    assert secondo["data_deposito"] == primo["data_deposito"]
    monkeypatch.setattr(m, "TTL_S", -1)                 # cache scaduta
    rete.risposte = [(500, b"")]
    terzo = _chiama()
    assert terzo["stato"] == "STALE" and terzo["stato_originale"] == "ok" and terzo["cache"] == "scaduta"
    assert "guasto" in terzo["motivo"] and terzo["data_deposito"] == "2026-07-29"


def test_non_trovato_non_va_in_cache(rete):
    rete.risposte = [_risposta([]), _risposta([])]
    _chiama()
    assert _chiama()["cache"] is None and len(rete.chiamate) == 2


# ---------------------------------------------------------------- riverifica senza rete
def test_riverifica_ok_e_manomissioni(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama()
    ok, motivo = m.riverifica_ricevuta(r, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo
    falsa = copy.deepcopy(r)
    falsa["ora_deposito"] = "06:00"
    assert m.riverifica_ricevuta(falsa, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")[0] is False
    alterata = copy.deepcopy(r)
    (k,) = alterata["url_liste"]
    alterata["risposte_salvate"][k] = alterata["risposte_salvate"][k].replace("07:00:00.9", "05:00:00.9")
    ok, motivo = m.riverifica_ricevuta(alterata, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "sha256" in motivo
    assert m.riverifica_ricevuta(r, ticker="ALTRO.BR", tipo="semestrale", periodo_fine="2026-06-30")[0] is False
    assert m.riverifica_ricevuta(r, ticker="ZZTEST.BR", tipo="annuale", periodo_fine="2026-06-30")[0] is False


def test_riverifica_richiesta_di_un_altro_isin(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama()
    falsa = copy.deepcopy(r)
    falsa["isin"] = ALTRO_ISIN
    ok, motivo = m.riverifica_ricevuta(falsa, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine=date(2026, 6, 30))
    assert not ok and "query" in motivo


# ---------------------------------------------------------------- AGGIUNTA 2 del contratto
def test_nomi_documento_costante_pubblica_uniforme():
    import re
    assert set(m.NOMI_DOCUMENTO) == {"annuale", "semestrale", "trimestrale"}
    for tipo, lista in m.NOMI_DOCUMENTO.items():
        assert isinstance(lista, list) and all(isinstance(x, str) for x in lista)
        assert all(re.compile(x, re.I) for x in lista)
    assert any(re.search(x, "ACME halfjaarverslag 2026", re.I) for x in m.NOMI_DOCUMENTO["semestrale"])
    assert any(re.search(x, "Rapport financier annuel 2025", re.I) for x in m.NOMI_DOCUMENTO["annuale"])


def test_formato_uniforme_candidati_e_conferme(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama()
    chiavi = {"titolo", "data", "ora", "url", "categoria", "lingua", "prova"}
    for c in r["candidati"] + r["conferme"]:
        assert chiavi <= set(c)
        assert c["data"] == "2026-07-29" and c["ora"] == "07:00" and c["prova"] in ("titolo", "finestra")


def test_riverifica_ambiguo_confronta_la_lista(rete):
    rete.risposte = [_risposta([
        _voce("Half-yearly financial report", "2026-07-29T07:00:00", ["ACME HY26 Financial Statements.pdf"], n=1),
        _voce("Half-yearly financial report", "2026-08-20T07:00:00", ["Halfjaarverslag ACME.pdf"], n=2)])]
    r = _chiama()
    assert r["stato"] == "ambiguo"
    ok, motivo = m.riverifica_ricevuta(r, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo
    meno = copy.deepcopy(r)
    meno["candidati"] = meno["candidati"][:1]
    ok, motivo = m.riverifica_ricevuta(meno, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "candidati" in motivo
    cambiata = copy.deepcopy(r)
    cambiata["candidati"][1]["ora"] = "06:00"
    assert m.riverifica_ricevuta(cambiata, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")[0] is False
    # un candidato DUPLICATO lascia uguale l'insieme: lo deve fermare il conteggio
    doppia = copy.deepcopy(r)
    doppia["candidati"].append(copy.deepcopy(doppia["candidati"][0]))
    ok, motivo = m.riverifica_ricevuta(doppia, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "(2)" in motivo and "(3)" in motivo


# ---------------------------------------------------------------- rilievi del revisore RV-UE1
def test_riverifica_confronta_prova_ricezione_categoria(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama()
    for campo, falso in (("prova", "finestra"), ("data_ricezione", "2026-07-28"), ("ora_ricezione", "06:00"),
                         ("categoria", "Annual financial report"), ("lingua", "nl"), ("fuso", "UTC")):
        alterata = copy.deepcopy(r)
        alterata[campo] = falso
        ok, motivo = m.riverifica_ricevuta(alterata, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
        assert not ok and campo in motivo, campo


def test_annuncio_scartato_se_c_e_il_documento(rete):
    rete.risposte = [_risposta([
        _voce("Annual financial report", "2026-03-20T07:30:00", ["ACME Annual Report 2025.pdf"], n=1),
        _voce("Other", "2026-03-27T08:00:00", ["Availability of the Annual Report 2025.pdf"], n=2)])]
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-03-20"
    assert any("annuncio" in s["motivo"] for s in r["scartati"])


def test_annuncio_solo_vale_con_limite(rete):
    rete.risposte = [_risposta([_voce("Other", "2026-03-27T08:00:00",
                                      ["Availability of the Annual Report 2025.pdf"])])]
    r = _chiama("annuale", "2025-12-31")
    assert r["stato"] == "ok" and r["candidati"][0]["annuncio"] is True
    assert any("ANNUNCIA" in x for x in r["limiti"])


def test_titolo_del_deposito_col_periodo_porta_l_url_del_file(rete):
    v = _voce("Half-yearly financial report", "2026-07-29T07:00:00", ["Rapport financier semestriel ACME.pdf"])
    v["documentTitle"] = "ACME HY26 Financial Report"
    rete.risposte = [_risposta([v])]
    r = _chiama()
    assert r["stato"] == "ok" and r["url"] and r["url_documento"] == r["url"]
    assert r["url"].endswith("00000000-0000-4000-8000-000000000100")


def test_piu_voci_di_result_count_formato_cambiato(rete):
    rete.risposte = [_risposta([_voce("Other", "2026-07-29T07:00:00", ["x.pdf"], n=1),
                                _voce("Other", "2026-07-30T07:00:00", ["y.pdf"], n=2)], totale=1)]
    assert _chiama()["errore"] == "formato_cambiato"


def test_riverifica_copertura_result_count(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama()
    alterata = copy.deepcopy(r)
    (k,) = alterata["url_liste"]
    corpo = json.loads(alterata["risposte_salvate"][k])
    corpo["resultCount"] = 999
    nuovo = json.dumps(corpo)
    alterata["risposte_salvate"][k] = nuovo
    alterata["sha256_liste"][k] = hashlib.sha256(nuovo.encode("utf-8")).hexdigest()
    ok, motivo = m.riverifica_ricevuta(alterata, ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "non copre" in motivo


def test_nome_conteso_semestrale_trimestrale_scartato(rete):
    rete.risposte = [_risposta([_voce("Half-yearly financial report", "2026-07-29T07:00:00",
                                      ["ACME half-year and first quarter report.pdf"])])]
    r = _chiama()
    assert r["stato"] == "non_trovato" and "nome conteso" in r["scartati"][0]["motivo"]


def test_data_ricezione_illeggibile_formato_cambiato(rete):
    d = _fix("fsma_semestrale.json")
    d["storiResultItems"][1]["dateReceived"] = "29/07/2026 07:31"
    rete.risposte = [d]
    r = _chiama()
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato" and "dateReceived" in r["motivo"]


def test_cache_non_servita_a_un_lei_diverso(rete):
    rete.risposte = [_fix("fsma_semestrale.json"), _fix("fsma_semestrale.json")]
    _chiama()
    r = _chiama(lei=LEI)
    assert r["cache"] is None and len(rete.chiamate) == 2 and r["lei"] == LEI


# ---------------------------------------------------------------- AGGIUNTA 4 del contratto
def test_kwarg_paese_accettato_esito_identico(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    senza = _chiama()
    rete.risposte = [_fix("fsma_semestrale.json")]
    m.CACHE_DIR = m.CACHE_DIR + "_b"          # seconda lettura vera, non dalla cache
    con = _chiama(paese="BE")
    assert len(rete.chiamate) == 2 and rete.chiamate[0]["corpo"] == rete.chiamate[1]["corpo"]
    for k in ("stato", "data_deposito", "ora_deposito", "fuso", "titolo", "url", "candidati", "sha256_liste"):
        assert con[k] == senza[k], k
    assert con["stato"] == "ok" and con["fuso"] == "Europe/Brussels"
    # anche nei KO precoci il kwarg non rompe nulla
    assert _chiama(isin=None, paese="BE")["errore"] == "identita_mancante"


def test_riverifica_kwarg_paese_coerente_col_sigillo(rete):
    rete.risposte = [_fix("fsma_semestrale.json")]
    r = _chiama(paese="BE")
    kw = dict(ticker="ZZTEST.BR", tipo="semestrale", periodo_fine="2026-06-30")
    assert m.riverifica_ricevuta(r, paese="BE", **kw)[0] is True
    assert m.riverifica_ricevuta(r, **kw)[0] is True                  # paese non dato: nessun vincolo in piu'
    ok, motivo = m.riverifica_ricevuta(r, paese="NL", **kw)
    assert not ok and "paese" in motivo and "'NL'" in motivo
    falsa = copy.deepcopy(r)
    falsa["paese"] = "NL"                                             # sigillo alterato
    assert m.riverifica_ricevuta(falsa, paese="NL", **kw)[0] is False
