# -*- coding: utf-8 -*-
"""Test di market_data/ue_nasdaq_nordic.py (handoff-3, EU-NQ, Opus 5.5). Nessuna rete: `_scarica`
e' sostituito da un finto che serve le fixture SINTETICHE tests/fixtures/fonti_ue/nasdaq_*.json
(emittente «Acme Sintetica», forma copiata dalle risposte vere misurate da U1)."""
import copy
import hashlib
import re
import json
import os
from urllib.parse import parse_qs, urlsplit

import pytest

from bellomberg.market_data import ue_nasdaq_nordic as nq

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "fonti_ue")
SE_NOME = "Acme Sintetica, AB"
FI_NOME = "Acme Sintetica Oyj"


def _fixture(nome):
    with open(os.path.join(FIX, nome), encoding="utf-8") as fh:
        return json.load(fh)


def _corpo(d):
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


class _Finto:
    """Finto `_scarica`: a ogni pagina (start=) il corpo dato; conta le chiamate."""

    def __init__(self, pagine=None, http=200, eccezione=None):
        self.pagine = pagine or []
        self.http = http
        self.eccezione = eccezione
        self.chiamate = []

    def __call__(self, url):
        ok, motivo = nq.url_consentito(url)
        assert ok, motivo
        self.chiamate.append(url)
        if self.eccezione is not None:
            raise self.eccezione
        start = int(parse_qs(urlsplit(url).query)["start"][0])
        i = start // nq.LIMITE_PAGINA
        return self.http, self.pagine[i] if i < len(self.pagine) else _corpo({"results": {"item": []}, "count": 0})


@pytest.fixture(autouse=True)
def _isola(tmp_path, monkeypatch):
    monkeypatch.setattr(nq, "CACHE_DIR", str(tmp_path / "cache_ue"))
    monkeypatch.setattr(nq, "PAUSA_S", 0.0)

    def _vietata(url):
        raise AssertionError("rete vera chiamata nei test: %s" % url)
    monkeypatch.setattr(nq, "_scarica", _vietata)


def _monta(monkeypatch, *corpi, **kw):
    f = _Finto(list(corpi), **kw)
    monkeypatch.setattr(nq, "_scarica", f)
    return f


def _se(monkeypatch, d=None):
    return _monta(monkeypatch, _corpo(d if d is not None else _fixture("nasdaq_acme_se.json")))


# ------------------------------------------------------------ parametri e identita'
def test_nome_mancante_ko_identita_senza_rete(monkeypatch):
    f = _se(monkeypatch)
    for nome in (None, "", "   "):
        r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=nome)
        assert r["stato"] == "KO" and r["errore"] == "identita_mancante"
        assert "nome" in r["motivo"] and "simbolo" in r["motivo"]
    assert f.chiamate == []


def test_suffisso_non_nordic_senza_paese_ko_identita_senza_rete(monkeypatch):
    # AGGIUNTA 4: mai 'non_coperto' fuorviante; il mercato non e' ricavabile -> identita_mancante
    f = _se(monkeypatch)
    r = nq.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and "paese" in r["motivo"]
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME, paese="NO")
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and "'NO'" in r["motivo"]
    assert f.chiamate == []


def test_paese_dal_router_comanda_sul_suffisso(monkeypatch):
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.DE", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME, paese="SE")
    assert r["stato"] == "ok" and r["paese"] == "SE" and "CANALE DI BORSA" in r["fonte"]
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.DE", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo
    ok, _ = nq.riverifica_ricevuta(r, ticker="ACMEB.DE", tipo="semestrale", periodo_fine="2026-06-30", paese="FI")
    assert not ok
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME, paese="DK")
    assert r["paese"] == "DK" and r["errore"] == "mercato_diverso"


@pytest.mark.parametrize("tipo,periodo", [("mensile", "2026-06-30"), ("semestrale", "2026-03-31"),
                                          ("trimestrale", "2026-06-30"), ("annuale", "31/12/2025"),
                                          ("annuale", "2025-12-30")])
def test_parametri_incoerenti_ko_senza_rete(monkeypatch, tipo, periodo):
    f = _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo=tipo, periodo_fine=periodo, nome=SE_NOME)
    assert r["stato"] == "KO" and r["errore"] == "parametro"
    assert f.chiamate == []


# ------------------------------------------------------------ scelta: casi veri (forma)
def test_semestrale_da_categoria_periodo_dal_titolo_coppia_en_sv(monkeypatch):
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "ok" and r["prova"] == "categoria"
    assert (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-07-17", "05:20", "UTC")
    assert r["titolo"] == "Acme Sintetica Group - the second quarter 2026" and r["lingua"] == "en"
    assert r["natura_data"] == "diffusione" and r["paese"] == "SE"
    assert [c["lingua"] for c in r["conferme"]] == ["sv"]
    # due PDF allegati: url_documento non scelto, dichiarato
    assert r["url_documento"] is None and any("2 allegati PDF" in x for x in r["limiti"])
    # l'invito al webcast ha nome+periodo ma e' scartato col motivo
    assert any("invito" in s["motivo"] for s in r["scartati"])


def test_annuale_sotto_categoria_sbagliata_preso_dal_nome(monkeypatch):
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["stato"] == "ok" and r["prova"] == "titolo"
    assert r["data_deposito"] == "2026-02-26" and r["titolo"] == "Acme Sintetica publishes Annual Report 2025"
    assert r["categoria"].startswith("Other information")
    assert r["url_documento"].startswith("https://attachment.news.eu.nasdaq.com/")
    assert r["sha256_documento"] is None


def test_semestrale_con_categoria_sbagliata_periodo_dal_titolo(monkeypatch):
    # Q2 2025 depositata sotto «Interim report (Q1 and Q3)»: il periodo viene dal titolo
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2025-06-30", nome=SE_NOME)
    assert r["stato"] == "ok" and r["data_deposito"] == "2025-07-17"
    assert r["categoria"] == "Interim report (Q1 and Q3)"


def test_trimestrale_q1_e_q2_scartato_per_periodo_diverso(monkeypatch):
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo="trimestrale", periodo_fine="2026-03-31", nome=SE_NOME)
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-04-24"
    assert any("second quarter 2026" in s["titolo"] and "diverso" in s["motivo"] for s in r["scartati"])


def test_annuale_non_usa_financial_statement_release(monkeypatch):
    d = _fixture("nasdaq_acme_se.json")
    d["results"]["item"] = [x for x in d["results"]["item"] if "Annual Report 2025" not in x["headline"]
                            and "årsredovisning 2025" not in x["headline"]]
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["stato"] == "non_trovato" and r["data_deposito"] is None
    assert "non trovato nella fonte" in r["motivo"] and "mai" not in r["motivo"]


def test_categoria_periodica_con_titolo_senza_periodo_non_candidato(monkeypatch):
    d = _fixture("nasdaq_acme_se.json")
    for x in d["results"]["item"]:
        if x["categoryId"] == 78:
            x["headline"] = "Acme Sintetica Group - results update"
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "non_trovato" and r["candidati"] == []


def test_annuale_senza_deroga_di_categoria(monkeypatch):
    # categoria PERIODICA (78) + periodo dell'esercizio ma SENZA il nome del documento: per
    # l'annuale la deroga della categoria non vale -> non e' candidato
    d = _fixture("nasdaq_acme_se.json")
    for x in d["results"]["item"]:
        if "Annual Report 2025" in x["headline"] or "årsredovisning 2025" in x["headline"]:
            x["headline"], x["categoryId"] = "Acme Sintetica results for the financial year 2025", 78
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["stato"] == "non_trovato"


def test_pubblicato_il_giorno_di_fine_periodo_scartato(monkeypatch):
    d = _fixture("nasdaq_acme_se.json")
    d["results"]["item"] = [x for x in d["results"]["item"] if x["language"] == "en"]
    for x in d["results"]["item"]:
        if x["headline"].endswith("second quarter 2026"):
            x["releaseTime"] = "2026-06-30 21:00:00"     # 23:00 a Stoccolma: ancora il 30/06
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "non_trovato"
    assert any("non dopo la fine del periodo" in s["motivo"] for s in r["scartati"])


def test_finestra_titolo_senza_anno_lingua_inglese(monkeypatch):
    _monta(monkeypatch, _corpo(_fixture("nasdaq_acme_fi_finestra.json")))
    r = nq.get_data_deposito("ACME.HE", tipo="semestrale", periodo_fine="2026-06-30", nome=FI_NOME)
    assert r["stato"] == "ok" and r["prova"] == "finestra" and r["lingua"] == "en"
    assert r["data_deposito"] == "2026-08-05" and [c["lingua"] for c in r["conferme"]] == ["fi"]
    assert r["fonte"].startswith("Nasdaq Helsinki") and "OAM" in r["fonte"]


def test_finestra_oltre_i_giorni_scartata(monkeypatch):
    _monta(monkeypatch, _corpo(_fixture("nasdaq_acme_fi_finestra.json")))
    # trimestrale al 30/09/2025: l'«Interim report» del 07/05/2026 cade oltre i 120 giorni
    r = nq.get_data_deposito("ACME.HE", tipo="trimestrale", periodo_fine="2025-09-30", nome=FI_NOME)
    assert r["stato"] == "non_trovato"
    assert any("oltre la finestra di 120 giorni" in s["motivo"] for s in r["scartati"])


def test_anno_diverso_scartato_annuale(monkeypatch):
    _monta(monkeypatch, _corpo(_fixture("nasdaq_acme_fi_finestra.json")))
    r = nq.get_data_deposito("ACME.HE", tipo="annuale", periodo_fine="2025-12-31", nome=FI_NOME)
    assert r["stato"] == "non_trovato"
    assert any("Annual Report 2024" in s["titolo"] and "diverso" in s["motivo"] for s in r["scartati"])


def test_ambiguo_con_rettifica_mai_il_primo(monkeypatch):
    d = _fixture("nasdaq_acme_se.json")
    corr = copy.deepcopy(next(x for x in d["results"]["item"] if x["headline"].endswith("second quarter 2026")
                              and x["language"] == "en"))
    corr.update(disclosureId=999999, headline="Correction: " + corr["headline"], releaseTime="2026-07-18 08:00:00")
    d["results"]["item"].insert(0, corr)
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and len(r["candidati"]) == 2
    assert "rettifiche" in r["motivo"]


def test_titolo_e_finestra_con_date_diverse_ambiguo(monkeypatch):
    d = _fixture("nasdaq_acme_fi_finestra.json")
    altro = copy.deepcopy(d["results"]["item"][1])
    altro.update(disclosureId=999998, headline="Acme Sintetica Oyj: Half-year financial report January-June 2026",
                 releaseTime="2026-08-20 06:00:00")
    d["results"]["item"].insert(0, altro)
    _monta(monkeypatch, _corpo(d))
    r = nq.get_data_deposito("ACME.HE", tipo="semestrale", periodo_fine="2026-06-30", nome=FI_NOME)
    assert r["stato"] == "ambiguo" and {c["prova"] for c in r["candidati"]} == {"titolo", "finestra"}


def test_solo_lingua_locale(monkeypatch):
    d = _fixture("nasdaq_acme_se.json")
    d["results"]["item"] = [x for x in d["results"]["item"] if x["language"] == "sv"]
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["stato"] == "ok" and r["lingua"] == "sv"


# ------------------------------------------------------------ identita' dalla fonte
def test_nome_non_trovato_dichiara_nome_esatto(monkeypatch):
    _monta(monkeypatch, _corpo(_fixture("nasdaq_vuoto.json")))
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome="AB Acme Sintetica")
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_non_trovato" and "ESATTO" in r["motivo"]


def test_fonte_risponde_con_altro_nome(monkeypatch):
    _monta(monkeypatch, _corpo(_fixture("nasdaq_altro_nome.json")))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_non_trovato"
    assert "Acme Sintetica Holding, AB" in r["motivo"]


def test_mercato_diverso_dal_suffisso_ambiguo(monkeypatch):
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.CO", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "ambiguo" and r["errore"] == "mercato_diverso" and r["data_deposito"] is None


def test_maiuscole_contano_nella_regola_esatta():
    # la regola ESATTA distingue le maiuscole: e' la risoluzione normalizzata (sotto) che le assorbe,
    # e allora lo dichiara in 'instradamento'
    righe = nq.parse_risposta(_corpo(_fixture("nasdaq_acme_se.json")))["righe"]
    v = nq.scegli(righe, nome=SE_NOME.lower(), tipo="semestrale", fine=nq.date(2026, 6, 30), paese="SE")
    assert v["stato"] == "non_trovato" and v["errore"] == "nome_non_trovato"


# ------------------------------------------------------------ guasti della fonte
def test_formato_cambiato_ko(monkeypatch):
    _monta(monkeypatch, _corpo(_fixture("nasdaq_formato_cambiato.json")))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato" and "releaseTime" in r["motivo"]
    _monta(monkeypatch, b"<html>pagina diversa</html>")
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato"


def test_http_e_rete_ko_senza_testo_eccezione(monkeypatch):
    _monta(monkeypatch, b"", http=503)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "KO" and r["errore"] == "http" and "503" in r["motivo"]
    _monta(monkeypatch, eccezione=ConnectionError("https://segreto?chiave=XYZ"))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "KO" and r["errore"] == "rete" and "ConnectionError" in r["motivo"]
    assert "segreto" not in r["motivo"]


def test_url_consentito():
    assert nq.url_consentito(nq.url_pagina(SE_NOME, 0))[0]
    assert not nq.url_consentito("http://api.news.eu.nasdaq.com/news/query.action?x=1")[0]
    assert not nq.url_consentito("https://view.news.eu.nasdaq.com/news/query.action")[0]
    assert not nq.url_consentito("https://api.news.eu.nasdaq.com/altro")[0]
    q = parse_qs(urlsplit(nq.url_pagina(SE_NOME, 0)).query)
    assert q["company"] == [SE_NOME] and q["timeZone"] == ["UTC"] and "language" not in q


def test_scarica_rispetta_pausa_e_non_segue_redirect(monkeypatch):
    import requests
    dormite, viste = [], []

    class _R:
        status_code, content = 200, b"{}"
    # la funzione VERA (catturata all'import, prima che la fixture autouse la sostituisca)
    monkeypatch.setattr(nq, "PAUSA_S", 2.0)
    monkeypatch.setattr(nq, "_ultima_richiesta", [1000.0])
    monkeypatch.setattr(nq.time, "monotonic", lambda: 1000.5)
    monkeypatch.setattr(nq.time, "sleep", lambda s: dormite.append(s))
    monkeypatch.setattr(requests, "get", lambda url, **kw: viste.append(kw) or _R())
    http, _ = _SCARICA_VERA(nq.url_pagina(SE_NOME, 0))
    assert http == 200 and dormite == [pytest.approx(1.5)]
    assert viste[0]["allow_redirects"] is False and "User-Agent" in viste[0]["headers"]
    with pytest.raises(nq.URLVietato):
        _SCARICA_VERA("https://example.com/news/query.action")


_SCARICA_VERA = nq._scarica


# ------------------------------------------------------------ paginazione
def _pagine(d, n):
    righe = d["results"]["item"]
    return [_corpo({"results": {"item": righe[i:i + n]}, "count": len(righe)}) for i in range(0, len(righe), n)]


def test_paginazione_si_ferma_alla_fine_del_periodo(monkeypatch):
    monkeypatch.setattr(nq, "LIMITE_PAGINA", 3)
    f = _monta(monkeypatch, *_pagine(_fixture("nasdaq_acme_se.json"), 3))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    # pagina 1: 31/08 x2, 17/07 ; pagina 2: 17/07 sv, 03/07, 24/04 -> la piu' vecchia e' <= 30/06: stop
    assert r["stato"] == "ok" and len(f.chiamate) == 2 and r["pagine_lette"] == 2
    assert len(r["url_liste"]) == 2 and set(r["sha256_liste"]) == set(r["url_liste"])


def test_finestra_non_raggiunta_ko(monkeypatch):
    monkeypatch.setattr(nq, "LIMITE_PAGINA", 2)
    monkeypatch.setattr(nq, "MAX_PAGINE", 2)
    _monta(monkeypatch, *_pagine(_fixture("nasdaq_acme_se.json"), 2))
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2024-12-31", nome=SE_NOME)
    assert r["stato"] == "KO" and r["errore"] == "finestra_non_raggiunta"


# ------------------------------------------------------------ cache e STALE
def test_cache_fresca_poi_stale(monkeypatch):
    f = _se(monkeypatch)
    a = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert a["stato"] == "ok" and a["cache"] is None
    b = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert b["cache"] == "fresca" and len(f.chiamate) == 1 and b["data_deposito"] == a["data_deposito"]
    monkeypatch.setattr(nq, "TTL_S", -1)
    _monta(monkeypatch, b"", http=500)
    c = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert c["stato"] == "STALE" and c["cache"] == "scaduta" and c["errore"] == "http"
    assert c["data_deposito"] == "2026-02-26" and "guasto" in c["motivo"]


def test_non_ok_non_va_in_cache(monkeypatch):
    _monta(monkeypatch, _corpo(_fixture("nasdaq_vuoto.json")))
    nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    f = _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["stato"] == "ok" and len(f.chiamate) == 1


# ------------------------------------------------------------ forma del ritorno
def test_stesse_chiavi_in_ogni_stato(monkeypatch):
    chiavi = None
    casi = [("ACMEB.ST", "annuale", "2025-12-31", SE_NOME, "nasdaq_acme_se.json"),
            ("ACMEB.ST", "annuale", "2025-12-31", None, "nasdaq_acme_se.json"),
            ("ACME.MI", "annuale", "2025-12-31", SE_NOME, "nasdaq_acme_se.json"),
            ("ACMEB.ST", "annuale", "2025-12-31", "X", "nasdaq_vuoto.json"),
            ("ACMEB.CO", "annuale", "2025-12-31", SE_NOME, "nasdaq_acme_se.json"),
            ("ACMEB.ST", "semestrale", "2026-06-30", SE_NOME, "nasdaq_formato_cambiato.json")]
    stati = set()
    for t, tipo, pf, nome, fx in casi:
        _monta(monkeypatch, _corpo(_fixture(fx)))
        r = nq.get_data_deposito(t, tipo=tipo, periodo_fine=pf, nome=nome)
        stati.add(r["stato"])
        chiavi = chiavi or set(r)
        assert set(r) == chiavi
        assert r["fonte_modulo"] == "bellomberg.market_data.ue_nasdaq_nordic"
    assert {"ok", "KO", "non_trovato", "ambiguo"} <= stati
    assert {"prova", "scartati", "risposte_salvate", "sha256_liste", "url_documento", "natura_data"} <= chiavi


CHIAVI_CONTRATTO = {
    "ticker", "isin", "lei", "nome", "tipo", "periodo_fine", "stato", "errore", "motivo", "data_deposito",
    "ora_deposito", "fuso", "natura_data", "titolo", "url", "url_documento", "sha256_documento", "categoria",
    "lingua", "candidati", "conferme", "fonte", "paese", "url_liste", "sha256_liste", "risposte_salvate",
    "fonte_modulo", "pagine_lette", "letto_il", "limiti", "cache", "prova", "scartati",
    "nome_nasdaq", "instradamento"}


@pytest.mark.parametrize("ticker,paese,nome,fx", [("ACMEB.ST", "SE", SE_NOME, "nasdaq_acme_se.json"),
                                                  ("ACME.HE", "FI", FI_NOME, "nasdaq_acme_fi_finestra.json"),
                                                  ("ACMEB.CO", "DK", SE_NOME, "nasdaq_acme_se.json")])
def test_vincolo_instradatore_paese_modulo_chiavi(monkeypatch, ticker, paese, nome, fx):
    # vincolo EU-R: paese = quello del suffisso, fonte_modulo fisso, tutte le chiavi del contratto
    # in OGNI stato (altrimenti l'instradatore rifiuta il modulo come guasto)
    for tipo, pf, n, corpo in (("semestrale", "2026-06-30", nome, _corpo(_fixture(fx))),
                               ("semestrale", "2026-06-30", None, None),
                               ("semestrale", "2026-03-31", nome, None),
                               ("annuale", "2025-12-31", "Nome Inesistente", _corpo(_fixture("nasdaq_vuoto.json"))),
                               ("semestrale", "2026-06-30", nome, b"non json")):
        _monta(monkeypatch, corpo)
        r = nq.get_data_deposito(ticker, tipo=tipo, periodo_fine=pf, nome=n)
        assert r["paese"] == paese, (tipo, pf, n, r["stato"])
        assert r["fonte_modulo"] == "bellomberg.market_data.ue_nasdaq_nordic"
        assert CHIAVI_CONTRATTO <= set(r), sorted(CHIAVI_CONTRATTO - set(r))


@pytest.mark.parametrize("ticker,attesa", [("ACMEB.ST", "SE"), ("ACMEB.CO", "DK")])
def test_se_dk_dichiarano_canale_di_borsa(ticker, attesa):
    r = nq.get_data_deposito(ticker, tipo="annuale", periodo_fine="2025-12-31", nome=None)
    assert r["paese"] == attesa and "CANALE DI BORSA" in r["fonte"]
    assert any("CANALE DI BORSA" in x and "OAM" in x for x in r["limiti"])
    fi = nq.get_data_deposito("ACME.HE", tipo="annuale", periodo_fine="2025-12-31", nome=None)
    assert "CANALE DI BORSA" not in fi["fonte"] and any("OAM finlandese" in x for x in fi["limiti"])


# ------------------------------------------------------------ riverifica senza rete
def _ricevuta(monkeypatch):
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    monkeypatch.setattr(nq, "_scarica", lambda url: (_ for _ in ()).throw(AssertionError("rete in riverifica")))
    return r


def test_riverifica_ok(monkeypatch):
    r = _ricevuta(monkeypatch)
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo


def test_riverifica_corpo_alterato(monkeypatch):
    r = _ricevuta(monkeypatch)
    url = r["url_liste"][0]
    r["risposte_salvate"][url] = r["risposte_salvate"][url].replace("05:20:00", "04:20:00", 1)
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "sha256" in motivo


def test_riverifica_data_alterata_o_modulo_diverso(monkeypatch):
    r = _ricevuta(monkeypatch)
    r2 = dict(r, ora_deposito="09:99")
    ok, motivo = nq.riverifica_ricevuta(r2, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "ora_deposito" in motivo
    ok, _ = nq.riverifica_ricevuta(dict(r, fonte_modulo="altro"), ticker="ACMEB.ST", tipo="semestrale",
                                   periodo_fine="2026-06-30")
    assert not ok
    ok, _ = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2025-06-30")
    assert not ok


# ------------------------------------------------------------ risoluzione del nome (seguito main 05/10)
class _FintoNomi:
    """Finto `_scarica` che distingue company= (nome esatto) e freeText= (ricerca)."""

    def __init__(self, per_nome, ricerca):
        self.per_nome, self.ricerca, self.chiamate = per_nome, ricerca, []

    def __call__(self, url):
        ok, motivo = nq.url_consentito(url)
        assert ok, motivo
        self.chiamate.append(url)
        q = parse_qs(urlsplit(url).query, keep_blank_values=True)
        if q["freeText"][0]:
            return 200, self.ricerca
        return 200, self.per_nome.get(q["company"][0], _corpo(_fixture("nasdaq_vuoto.json")))


def _ricerca(*nomi_mercati):
    base = _fixture("nasdaq_acme_se.json")["results"]["item"][0]
    righe = [dict(base, disclosureId=800000 + i, company=n, market=m) for i, (n, m) in enumerate(nomi_mercati)]
    return _corpo({"results": {"item": righe}, "count": len(righe)})


def _monta_nomi(monkeypatch, ricerca):
    f = _FintoNomi({SE_NOME: _corpo(_fixture("nasdaq_acme_se.json")),
                    FI_NOME: _corpo(_fixture("nasdaq_acme_fi_finestra.json"))}, ricerca)
    monkeypatch.setattr(nq, "_scarica", f)
    return f


STO = "Main Market, Stockholm"


@pytest.mark.parametrize("dato,atteso", [("AB Acme Sintetica", "acme sintetica"),
                                         ("Acme Sintetica, AB", "acme sintetica"),
                                         ("ACME SINTETICA AB (publ)", "acme sintetica"),
                                         ("Acme Sintetica A/S", "acme sintetica"),
                                         ("Acme Sintetica Oyj", "acme sintetica"),
                                         ("Acme Sintetica Car AB", "acme sintetica car")])
def test_normalizza_nome(dato, atteso):
    assert nq.normalizza_nome(dato) == atteso


def test_nome_legale_risolto_unico_e_dichiarato(monkeypatch):
    f = _monta_nomi(monkeypatch, _ricerca((SE_NOME, STO), ("Acme Sintetica Car AB", STO),
                                          ("Acme Sintetica Holding, AB", STO)))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-07-17"
    assert r["nome"] == "AB Acme Sintetica" and r["nome_nasdaq"] == SE_NOME
    assert "risolto" in r["instradamento"] and "unica corrispondenza" in r["instradamento"]
    assert len(f.chiamate) == 3        # tentativo col nome dato, ricerca, comunicati del nome risolto
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo


def test_nome_esatto_nessuna_ricerca(monkeypatch):
    f = _monta_nomi(monkeypatch, _ricerca((SE_NOME, STO)))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "ok" and r["nome_nasdaq"] == SE_NOME and "esatto" in r["instradamento"]
    assert len(f.chiamate) == 1


def test_due_nomi_normalizzati_uguali_ambiguo(monkeypatch):
    _monta_nomi(monkeypatch, _ricerca(("Acme Sintetica AB", STO), ("Acme Sintetica, AB (publ)", STO)))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    assert r["stato"] == "ambiguo" and r["errore"] == "nome_ambiguo" and r["data_deposito"] is None
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo


def test_mai_prefissi(monkeypatch):
    _monta_nomi(monkeypatch, _ricerca((SE_NOME, STO)))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="Acme AB")
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_non_trovato" and r["nome_nasdaq"] is None
    assert "ESATTO" in r["motivo"]


def test_corrispondenza_solo_su_altro_mercato_non_trovato(monkeypatch):
    _monta_nomi(monkeypatch, _ricerca((SE_NOME, "Main Market, Helsinki")))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    assert r["stato"] == "non_trovato" and r["nome_nasdaq"] is None


def test_risoluzione_in_cache_30_giorni(monkeypatch):
    f = _monta_nomi(monkeypatch, _ricerca((SE_NOME, STO)))
    nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    f.chiamate.clear()
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome="AB Acme Sintetica")
    assert r["stato"] == "ok" and r["nome_nasdaq"] == SE_NOME and "cache 30 gg" in r["instradamento"]
    assert len(f.chiamate) == 1 and "freeText=&" in f.chiamate[0]
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31")
    assert ok, motivo


def test_nome_finlandese_senza_forma(monkeypatch):
    nasdaq_fi = "Acme Sintetica"
    d = _fixture("nasdaq_acme_fi_finestra.json")
    for x in d["results"]["item"]:
        x["company"] = nasdaq_fi
    f = _FintoNomi({nasdaq_fi: _corpo(d)}, _ricerca((nasdaq_fi, "Main Market, Helsinki")))
    monkeypatch.setattr(nq, "_scarica", f)
    r = nq.get_data_deposito("ACME.HE", tipo="semestrale", periodo_fine="2026-06-30", nome="Acme Sintetica Oyj")
    assert r["stato"] == "ok" and r["nome_nasdaq"] == nasdaq_fi and r["paese"] == "FI"


def test_riverifica_risoluzione_alterata(monkeypatch):
    _monta_nomi(monkeypatch, _ricerca((SE_NOME, STO)))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    ok, _ = nq.riverifica_ricevuta(dict(r, nome_nasdaq="Acme Sintetica Holding, AB"), ticker="ACMEB.ST",
                                   tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok
    senza = dict(r, url_liste=[u for u in r["url_liste"] if "freeText=&" in u])
    ok, motivo = nq.riverifica_ricevuta(senza, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "ricerca" in motivo


def test_riverifica_ricerca_alterata_coerente_fallisce(monkeypatch):
    # chi altera la ricerca salvata (e ne ricalcola lo sha) per renderla ambigua non passa
    import hashlib
    _monta_nomi(monkeypatch, _ricerca((SE_NOME, STO)))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    url = next(u for u in r["url_liste"] if "freeText=acme" in u)
    nuovo = _ricerca((SE_NOME, STO), ("Acme Sintetica AB (publ)", STO)).decode("utf-8")
    r["risposte_salvate"][url] = nuovo
    r["sha256_liste"][url] = hashlib.sha256(nuovo.encode("utf-8")).hexdigest()
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "risoluzione" in motivo


# ------------------------------------------------------------ AGGIUNTA 2 dell'interfaccia
def test_nomi_documento_pubblico_uniforme():
    import re
    assert set(nq.NOMI_DOCUMENTO) == {"annuale", "semestrale", "trimestrale"}
    for tipo, lista in nq.NOMI_DOCUMENTO.items():
        assert isinstance(lista, list) and all(isinstance(x, str) for x in lista)
        rx = re.compile("|".join(lista), re.I)
        assert rx.pattern == nq.DOCUMENTI[tipo].pattern       # e' la stessa regola che il modulo usa
    assert re.search("|".join(nq.NOMI_DOCUMENTO["annuale"]), "Acme publicerar årsredovisning 2025", re.I)
    assert not re.search("|".join(nq.NOMI_DOCUMENTO["annuale"]), "Acme bokslutskommuniké 2025", re.I)


def test_candidati_formato_uniforme(monkeypatch):
    d = _fixture("nasdaq_acme_se.json")
    corr = copy.deepcopy(next(x for x in d["results"]["item"] if x["headline"].endswith("second quarter 2026")
                              and x["language"] == "en"))
    corr.update(disclosureId=999997, headline="Correction: " + corr["headline"], releaseTime="2026-07-18 08:00:00")
    d["results"]["item"].insert(0, corr)
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    for c in r["candidati"] + r["conferme"]:
        assert {"titolo", "data", "ora", "url", "categoria", "lingua", "prova"} <= set(c)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["data"]) and re.fullmatch(r"\d{2}:\d{2}", c["ora"])
        assert c["prova"] in ("titolo", "finestra", "categoria")


def test_riverifica_ambiguo_ricalcola_la_lista(monkeypatch):
    d = _fixture("nasdaq_acme_se.json")
    corr = copy.deepcopy(next(x for x in d["results"]["item"] if x["headline"].endswith("second quarter 2026")
                              and x["language"] == "en"))
    corr.update(disclosureId=999996, headline="Correction: " + corr["headline"], releaseTime="2026-07-18 08:00:00")
    d["results"]["item"].insert(0, corr)
    _se(monkeypatch, d)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "ambiguo"
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo
    ok, motivo = nq.riverifica_ricevuta(dict(r, candidati=r["candidati"][:1]), ticker="ACMEB.ST",
                                        tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "candidati" in motivo


# ------------------------------------------------------------ rilievi RV-UE2 e AGGIUNTA 4
def test_giorno_locale_del_listino(monkeypatch):
    # 00:30 a Helsinki del 01/07 = 30/06 21:30 UTC: e' DOPO la fine del periodo nel giorno locale
    d = _fixture("nasdaq_acme_fi_finestra.json")
    d["results"]["item"] = [x for x in d["results"]["item"] if x["language"] == "en"]
    for x in d["results"]["item"]:
        if "Half-year" in x["headline"]:
            x["releaseTime"] = "2026-06-30 21:30:00"
    _monta(monkeypatch, _corpo(d))
    r = nq.get_data_deposito("ACME.HE", tipo="semestrale", periodo_fine="2026-06-30", nome=FI_NOME)
    assert r["stato"] == "ok" and (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-06-30", "21:30", "UTC")
    assert (r["data_deposito_locale"], r["ora_deposito_locale"], r["fuso_locale"]) == \
        ("2026-07-01", "00:30", "Europe/Helsinki")


def test_cache_con_isin_della_chiamata(monkeypatch):
    f = _se(monkeypatch)
    a = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    b = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME,
                             isin="SE0000000005")
    assert a["isin"] is None and b["isin"] == "SE0000000005" and len(f.chiamate) == 2
    c = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME,
                             isin="SE0000000005")
    assert c["cache"] == "fresca" and c["isin"] == "SE0000000005"


def _tronca(corpo, totale):
    d = json.loads(corpo)
    d["count"] = totale
    return _corpo(d)


def test_ricerca_troncata(monkeypatch):
    _monta_nomi(monkeypatch, _tronca(_ricerca(("Acme Sintetica Car AB", STO)), 6549))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    assert r["stato"] == "KO" and r["errore"] == "ricerca_troncata" and "di 6549" in r["motivo"]
    _monta_nomi(monkeypatch, _tronca(_ricerca((SE_NOME, STO)), 6549))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    assert r["stato"] == "ok" and "ricerca troncata: 1 comunicati letti di 6549" in r["instradamento"]
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo


@pytest.mark.parametrize("campo,valore", [("fuso", "Europe/Stockholm"), ("natura_data", "deposito_autorita"),
                                          ("prova", "titolo"), ("url_documento", "https://altro/doc.pdf")])
def test_riverifica_campi_di_istante_e_documento(monkeypatch, campo, valore):
    _se(monkeypatch)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r[campo] != valore
    ok, motivo = nq.riverifica_ricevuta(dict(r, **{campo: valore}), ticker="ACMEB.ST", tipo="semestrale",
                                        periodo_fine="2026-06-30")
    assert not ok and campo in motivo


def _fi_en_finestra_sv_titolo(data_sv):
    d = _fixture("nasdaq_acme_fi_finestra.json")
    sv = copy.deepcopy(d["results"]["item"][2])
    sv.update(disclosureId=777777, language="sv", headline="Acme Sintetica Oyj: Halvårsrapport januari–juni 2026",
              releaseTime=data_sv)
    d["results"]["item"] = [x for x in d["results"]["item"] if x["language"] != "fi"]
    d["results"]["item"].insert(0, sv)
    return d


def test_finestra_inglese_contro_titolo_svedese_date_diverse_ambiguo(monkeypatch):
    _monta(monkeypatch, _corpo(_fi_en_finestra_sv_titolo("2026-08-20 06:00:00")))
    r = nq.get_data_deposito("ACME.HE", tipo="semestrale", periodo_fine="2026-06-30", nome=FI_NOME)
    assert r["stato"] == "ambiguo" and {(c["lingua"], c["prova"]) for c in r["candidati"]} == \
        {("en", "finestra"), ("sv", "titolo")}
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACME.HE", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo


def test_conferma_stesso_giorno_ora_diversa_dichiarata(monkeypatch):
    _monta(monkeypatch, _corpo(_fi_en_finestra_sv_titolo("2026-08-05 05:10:00")))
    r = nq.get_data_deposito("ACME.HE", tipo="semestrale", periodo_fine="2026-06-30", nome=FI_NOME)
    assert r["stato"] == "ok" and r["lingua"] == "en"
    assert any("data/ora diversa" in x and "sv 2026-08-05 05:10" in x for x in r["limiti"])


@pytest.mark.parametrize("a,b", [("SE Banken AB", "Banken AB"), ("Corp Holding AB", "Holding AB"),
                                 ("Acme AB Sintetica", "Acme Sintetica")])
def test_normalizza_forme_solo_in_testa_o_coda(a, b):
    assert nq.normalizza_nome(a) != nq.normalizza_nome(b)


def test_riverifica_risoluzione_non_dovuta(monkeypatch):
    # N2: se la pagina del nome dato contiene gia' quel nome esatto, la risoluzione non era dovuta
    _monta_nomi(monkeypatch, _ricerca((SE_NOME, STO)))
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome="AB Acme Sintetica")
    url = nq.url_pagina("AB Acme Sintetica", 0)
    assert url in r["url_liste"]
    d = _fixture("nasdaq_acme_se.json")
    for x in d["results"]["item"]:
        x["company"] = "AB Acme Sintetica"
    corpo = json.dumps(d, ensure_ascii=False)
    r["risposte_salvate"][url] = corpo
    r["sha256_liste"][url] = hashlib.sha256(corpo.encode("utf-8")).hexdigest()
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "non dovuta" in motivo


def test_pagine_sovrapposte_non_fanno_ambiguo(monkeypatch):
    # N5: la fonte pagina per offset; se l'elenco slitta, la stessa riga arriva in due pagine
    monkeypatch.setattr(nq, "LIMITE_PAGINA", 3)
    d = _fixture("nasdaq_acme_se.json")
    righe = [x for x in d["results"]["item"] if x["language"] == "en"]
    pagine = [_corpo({"results": {"item": righe[0:3]}, "count": len(righe)}),
              _corpo({"results": {"item": righe[1:4]}, "count": len(righe)})]
    _monta(monkeypatch, *pagine)
    r = nq.get_data_deposito("ACMEB.ST", tipo="semestrale", periodo_fine="2026-06-30", nome=SE_NOME)
    assert r["stato"] == "ok" and r["pagine_lette"] == 2


def test_cache_dal_futuro_non_e_fresca(monkeypatch):
    # N6: orologio tornato indietro (eta' negativa) = eta' non misurabile, non «fresca»
    f = _se(monkeypatch)
    nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    for nome_file in os.listdir(nq.CACHE_DIR):
        p = os.path.join(nq.CACHE_DIR, nome_file)
        with open(p, encoding="utf-8") as fh:
            c = json.load(fh)
        c["salvato_ts"] += 10 ** 6
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["cache"] is None and len(f.chiamate) == 2


def test_riverifica_di_una_ricevuta_stale(monkeypatch):
    # N8: una ricevuta STALE e' una lettura ok servita dopo un guasto: si riverifica come ok
    _se(monkeypatch)
    nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    monkeypatch.setattr(nq, "TTL_S", -1)
    _monta(monkeypatch, b"", http=500)
    r = nq.get_data_deposito("ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31", nome=SE_NOME)
    assert r["stato"] == "STALE"
    ok, motivo = nq.riverifica_ricevuta(r, ticker="ACMEB.ST", tipo="annuale", periodo_fine="2025-12-31")
    assert ok, motivo
