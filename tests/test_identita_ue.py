# -*- coding: utf-8 -*-
"""Test di identita_ue (ISIN/LEI verificati da GLEIF): fixture SINTETICHE, nessuna rete.
Emittente inventato «ACME SINTETICA», ISIN e LEI sintetici con cifre di controllo valide calcolate
dall'oracolo QUI SOTTO (scritto a parte, non importato dal modulo). Forma delle risposte GLEIF misurata il
05/10/2026 (JSON:API, meta.pagination.total + data)."""
import copy
import json
import os
import time
from pathlib import Path

import pytest

from bellomberg.market_data import identita_ue as I

FIX = Path(__file__).parent / "fixtures" / "fonti_ue"


def _oracolo_isin(corpo11):
    cifre = "".join(str(int(c, 36)) for c in corpo11)
    s = 0
    for k, c in enumerate(reversed(cifre)):
        n = int(c) * (2 if k % 2 == 0 else 1)
        s += n - 9 if n > 9 else n
    return corpo11 + str((10 - s % 10) % 10)


def _oracolo_lei(corpo18):
    n = int("".join(str(int(c, 36)) for c in corpo18 + "00"))
    return corpo18 + "%02d" % (98 - n % 97)


ISIN = _oracolo_isin("NL00ZZ00001")
LEI_A, LEI_B, LEI_C, LEI_D = (_oracolo_lei("ZZTEST0000000000" + x) for x in ("A1", "B2", "C3", "D4"))


def _j(nome):
    return json.loads((FIX / nome).read_text(encoding="utf-8"))


def _b(d):
    return json.dumps(d).encode("utf-8")


@pytest.fixture(autouse=True)
def isolato(monkeypatch, tmp_path):
    monkeypatch.setattr(I, "CACHE_DIR", str(tmp_path / "cache_id"))
    monkeypatch.setattr(I, "PAUSA_S", 0)
    import requests

    def _vietato(*a, **k):
        raise AssertionError("rete vera in un test di identita_ue")
    monkeypatch.setattr(requests, "get", _vietato)
    monkeypatch.setattr(requests, "request", _vietato)


@pytest.fixture
def rete(monkeypatch):
    """Finta _scarica per URL: pagine[url] = (http, bytes) o eccezione; URL fuori elenco = 404."""
    s = {"chiamate": [], "pagine": {I.url_gleif_isin(ISIN): (200, (FIX / "idue_isin_ok.json").read_bytes())}}

    def _finta(url):
        ok, motivo = I.url_ammesso(url)
        assert ok, motivo
        s["chiamate"].append(url)
        e = s["pagine"].get(url, (404, b""))
        if isinstance(e, Exception):
            raise e
        return e
    monkeypatch.setattr(I, "_scarica", _finta)
    return s


def _nome_es(pagina=1, base="acme sintetica"):
    return I.url_gleif_nome(base, pagina)


def _r(ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN, **k):
    return I.risolvi_identita_ue(ticker, nome=nome, isin=isin, **k)


CHIAVI = {"ticker", "nome", "stato", "errore", "motivo", "isin", "lei", "nome_ufficiale", "fonte_isin", "fonte_lei",
          "paese_isin", "verifica", "limiti", "url_liste", "sha256_liste", "risposte_salvate", "fonte_modulo",
          "letto_il", "stato_originale", "cache", "regola", "isin_chiamante", "fonte_isin_chiamante", "salvato_ts"}


# ---------------- oracolo ----------------
def test_oracoli_coincidono_col_modulo():
    assert I.isin_valido(ISIN) and not I.isin_valido(ISIN[:-1] + str((int(ISIN[-1]) + 1) % 10))
    assert all(I.lei_valido(x) for x in (LEI_A, LEI_B, LEI_C, LEI_D))


# ---------------- ramo ISIN dal chiamante ----------------
def test_isin_confermato_da_gleif_lei_e_nome(rete):
    r = _r()
    assert r["stato"] == "ok" and r["isin"] == ISIN and r["lei"] == LEI_A
    assert r["nome_ufficiale"] == "ACME SINTETICA N.V." and r["paese_isin"] == "NL"
    assert r["fonte_isin"] == "negozio ISIN italiano (chiamante)" and r["fonte_lei"] == I.FONTE_GLEIF
    assert rete["chiamate"] == [I.url_gleif_isin(ISIN)]
    assert any("confermato da GLEIF" in x for x in r["limiti"])
    assert set(r) == CHIAVI


@pytest.mark.parametrize("nome", ["ACME SINTETICA NV", "Acme Sintetica", "Acme Sintetica, N.V.", "Zeta Sintetica N.V."])
def test_nome_coincide_in_forma_base_o_con_altro_nome_gleif(rete, nome):
    r = _r(nome=nome)
    assert r["stato"] == "ok" and r["lei"] == LEI_A


@pytest.mark.parametrize("nome", ["Beta Sintetica N.V.", "Acme Sintetica Holding N.V."])
def test_nome_diverso_ko_identita_incoerente_niente_dato(rete, nome):
    r = _r(nome=nome)
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente"
    assert r["isin"] is None and r["lei"] is None and "ACME SINTETICA N.V." in r["motivo"]


def test_isin_non_legato_a_nessun_lei_non_trovato(rete):
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, (FIX / "idue_isin_vuoto.json").read_bytes())
    r = _r()
    assert r["stato"] == "non_trovato" and r["isin"] is None and r["lei"] is None
    assert "non confermato" in r["motivo"]


def test_isin_legato_a_entita_inattiva_non_trovato(rete):
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, (FIX / "idue_isin_inattivo.json").read_bytes())
    r = _r()
    assert r["stato"] == "non_trovato" and r["lei"] is None


def test_isin_legato_a_due_lei_ambiguo_mai_il_primo(rete):
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, (FIX / "idue_isin_due.json").read_bytes())
    r = _r()
    assert r["stato"] == "ambiguo" and r["lei"] is None and r["isin"] is None
    assert {c["lei"] for c in r["verifica"]["lei"]["candidati"]} == {LEI_A, LEI_B}


def test_isin_non_valido_parametro_senza_rete(rete):
    r = _r(isin=ISIN[:-1] + str((int(ISIN[-1]) + 1) % 10))
    assert r["stato"] == "KO" and r["errore"] == "parametro" and rete["chiamate"] == []


@pytest.mark.parametrize("nome", [None, "", "  ", "ZZTEST", "zztest s.a."])
def test_nome_assente_o_uguale_al_simbolo_senza_rete(rete, nome):
    r = _r(nome=nome)
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and rete["chiamate"] == []


@pytest.mark.parametrize("reg", ["LAPSED", "ANNULLED", "DUPLICATE", "RETIRED", "MERGED"])
def test_ramo_isin_registrazione_non_ammessa_mai_ok(rete, reg):
    d = _j("idue_isin_ok.json")
    d["data"][0]["attributes"]["registration"]["status"] = reg
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, _b(d))
    r = _r()
    assert r["stato"] == "non_trovato" and r["lei"] is None and r["isin"] is None and reg in r["motivo"]


def test_ramo_isin_pending_transfer_ok_dichiarato(rete):
    d = _j("idue_isin_ok.json")
    d["data"][0]["attributes"]["registration"]["status"] = "PENDING_TRANSFER"
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, _b(d))
    r = _r()
    assert r["stato"] == "ok" and any("PENDING_TRANSFER" in x for x in r["limiti"])


@pytest.mark.parametrize("prefisso", ["XS", "EU"])
def test_isin_non_nazionale_rifiutato_senza_rete(rete, prefisso):
    isin = _oracolo_isin(prefisso + "00ZZ00001")
    r = _r(isin=isin)
    assert r["stato"] == "KO" and r["errore"] == "parametro" and prefisso in r["motivo"] and rete["chiamate"] == []


def test_totale_diverso_dai_record_ko_troncata(rete):
    d = _j("idue_isin_ok.json")
    d["meta"]["pagination"]["total"] = 3
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, _b(d))
    r = _r()
    assert r["stato"] == "KO" and r["errore"] == "ricerca_troncata"


# ---------------- ramo nome + paese (ISIN assente) ----------------
def test_senza_isin_lei_per_nome_e_paese_univoco(rete):
    rete["pagine"][_nome_es()] = (200, (FIX / "idue_nome_es.json").read_bytes())
    r = _r(ticker="QQSYN.MC", nome="Acme Sintetica, S.A.", isin=None)
    assert r["stato"] == "ok" and r["lei"] == LEI_C and r["isin"] is None and r["paese_isin"] is None
    assert r["verifica"]["isin"]["stato"] == "non_coperto"
    assert r["verifica"]["isin"]["motivo"] == I.MOTIVO_ISIN_SENZA_FONTE
    assert "Euronext: termini d'uso" in I.MOTIVO_ISIN_SENZA_FONTE
    assert I.LIMITE_NOME_PAESE in r["limiti"] and any(x.startswith("ISIN non dato") for x in r["limiti"])
    assert rete["chiamate"] == [_nome_es()]


def test_forma_societaria_diversa_non_si_fonde(rete):
    rete["pagine"][_nome_es()] = (200, (FIX / "idue_nome_es.json").read_bytes())
    r = _r(ticker="QQSYN.MC", nome="Acme Sintetica SL", isin=None)
    assert r["stato"] == "ok" and r["lei"] == LEI_A


def test_nome_assente_dalla_ricerca_non_trovato(rete):
    rete["pagine"][_nome_es(base="omega sintetica")] = (200, (FIX / "idue_nome_es.json").read_bytes())
    r = _r(ticker="QQSYN.MC", nome="Omega Sintetica SA", isin=None)
    assert r["stato"] == "non_trovato" and r["lei"] is None and I.MOTIVO_ISIN_SENZA_FONTE in r["motivo"]


def test_due_entita_attive_stesso_nome_ambiguo(rete):
    d = _j("idue_nome_es.json")
    d["data"][3]["attributes"]["entity"]["status"] = "ACTIVE"
    rete["pagine"][_nome_es()] = (200, _b(d))
    r = _r(ticker="QQSYN.MC", nome="Acme Sintetica SA", isin=None)
    assert r["stato"] == "ambiguo" and r["lei"] is None
    assert {c["lei"] for c in r["verifica"]["lei"]["candidati"]} == {LEI_C, LEI_D}


def test_record_di_altro_paese_ignorato(rete):
    d = _j("idue_nome_es.json")
    d["data"][0]["attributes"]["entity"]["legalAddress"]["country"] = "PT"
    rete["pagine"][_nome_es()] = (200, _b(d))
    r = _r(ticker="QQSYN.MC", nome="Acme Sintetica SA", isin=None)
    assert r["stato"] == "non_trovato" and r["lei"] is None
    assert [c["paese"] for c in r["verifica"]["lei"]["candidati"]] == ["PT"] and "PT" in r["motivo"]


def test_ricerca_su_due_pagine(rete):
    d = _j("idue_nome_es.json")
    p1, p2 = copy.deepcopy(d), copy.deepcopy(d)
    p1["data"] = d["data"][1:3]
    p2["data"] = d["data"][:1]
    p1["meta"]["pagination"]["total"] = p2["meta"]["pagination"]["total"] = 3
    rete["pagine"][_nome_es(1)] = (200, _b(p1))
    rete["pagine"][_nome_es(2)] = (200, _b(p2))
    r = _r(ticker="QQSYN.MC", nome="Acme Sintetica SA", isin=None)
    assert r["stato"] == "ok" and r["lei"] == LEI_C and rete["chiamate"] == [_nome_es(1), _nome_es(2)]


def test_ricerca_oltre_le_pagine_ko_troncata(rete):
    d = _j("idue_nome_es.json")
    d["meta"]["pagination"]["total"] = 999
    for n in range(1, I.GLEIF_MAX_PAGINE + 1):
        rete["pagine"][_nome_es(n)] = (200, _b(d))
    r = _r(ticker="QQSYN.MC", nome="Acme Sintetica SA", isin=None)
    assert r["stato"] == "KO" and r["errore"] == "ricerca_troncata" and r["lei"] is None
    assert len(rete["chiamate"]) == I.GLEIF_MAX_PAGINE


def test_pagina_vuota_con_totale_maggiore_ko_troncata(rete):
    vuota = _j("idue_isin_vuoto.json")
    vuota["meta"]["pagination"]["total"] = 9
    rete["pagine"][_nome_es(1)] = (200, _b(vuota))
    r = _r(ticker="QQSYN.MC", nome="Acme Sintetica SA", isin=None)
    assert r["stato"] == "KO" and r["errore"] == "ricerca_troncata" and rete["chiamate"] == [_nome_es(1)]


def test_regno_unito_cerca_gb(rete):
    url = I.url_gleif_nome("acme sintetica", 1)
    d = _j("idue_nome_es.json")
    d["data"] = d["data"][:1]
    d["data"][0]["attributes"]["entity"]["legalAddress"]["country"] = "GB"
    d["data"][0]["attributes"]["entity"]["legalName"]["name"] = "ACME SINTETICA PLC"
    d["meta"]["pagination"]["total"] = 1
    rete["pagine"][url] = (200, _b(d))
    r = _r(ticker="ZZTEST.L", nome="Acme Sintetica PLC", isin=None)
    assert r["stato"] == "ok" and rete["chiamate"] == [url]


@pytest.mark.parametrize("ticker", ["ZZTEST.HK", "ZZTEST"])
def test_listino_non_europeo_o_senza_suffisso_non_coperto(rete, ticker):
    r = _r(ticker=ticker, isin=None)
    assert r["stato"] == "non_coperto" and r["lei"] is None and rete["chiamate"] == []
    assert I.MOTIVO_ISIN_SENZA_FONTE in r["motivo"]


# ---------------- guasti e formato ----------------
def test_http_errore_ko_senza_cache(rete):
    rete["pagine"][I.url_gleif_isin(ISIN)] = (503, b"")
    r = _r()
    assert r["stato"] == "KO" and r["errore"] == "http" and "503" in r["motivo"]
    assert not os.path.isdir(I.CACHE_DIR) or not os.listdir(I.CACHE_DIR)


def test_eccezione_di_rete_solo_il_tipo(rete):
    rete["pagine"][I.url_gleif_isin(ISIN)] = ConnectionError("https://segreto?key=XYZ")
    r = _r()
    assert r["stato"] == "KO" and r["errore"] == "rete" and "ConnectionError" in r["motivo"]
    assert "segreto" not in json.dumps(r)


def test_formato_cambiato_ko(rete):
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, (FIX / "idue_formato_cambiato.json").read_bytes())
    r = _r()
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato"


def test_lei_non_valido_nel_record_ko(rete):
    d = _j("idue_isin_ok.json")
    cattivo = LEI_A[:-1] + str((int(LEI_A[-1]) + 1) % 10)
    d["data"][0]["id"] = d["data"][0]["attributes"]["lei"] = cattivo
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, _b(d))
    r = _r()
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato" and r["lei"] is None


def test_url_ammessi_solo_gleif():
    assert I.url_ammesso(I.url_gleif_isin(ISIN))[0]
    assert not I.url_ammesso("http://api.gleif.org/api/v1/lei-records")[0]
    assert not I.url_ammesso("https://api.gleif.org/api/v1/fuzzycompletions")[0]
    assert not I.url_ammesso("https://live.euronext.com/en/pd_es/data/stocks/download")[0]
    with pytest.raises(I.URLVietato):
        I._scarica("https://example.org/api/v1/lei-records")


# ---------------- cache e STALE ----------------
def test_cache_fresca_niente_seconda_richiesta(rete):
    assert _r()["cache"] is None
    r = _r()
    assert r["stato"] == "ok" and r["cache"] == "fresca" and len(rete["chiamate"]) == 1


def _invecchia(giorni):
    for f in os.listdir(I.CACHE_DIR):
        p = os.path.join(I.CACHE_DIR, f)
        c = json.load(open(p, encoding="utf-8"))
        c["salvato_ts"] = time.time() - giorni * 86400
        json.dump(c, open(p, "w", encoding="utf-8"))


def test_cache_scaduta_si_rilegge(rete):
    _r()
    _invecchia(31)
    r = _r()
    assert r["stato"] == "ok" and len(rete["chiamate"]) == 2 and r["cache"] is None


def test_guasto_con_cache_scaduta_stale_dichiarato(rete):
    _r()
    _invecchia(31)
    rete["pagine"][I.url_gleif_isin(ISIN)] = ConnectionError("x")
    r = _r()
    assert r["stato"] == "STALE" and r["stato_originale"] == "ok" and r["cache"] == "scaduta"
    assert any(x.startswith("STALE") for x in r["limiti"])
    assert I.riverifica_identita(r, ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)[0]


def test_cache_nel_futuro_vale_scaduta(rete):
    _r()
    _invecchia(-2)
    _r()
    assert len(rete["chiamate"]) == 2


# ---------------- riverifica senza rete ----------------
def test_riverifica_ok_entrambi_i_rami(rete):
    r = _r()
    assert I.riverifica_identita(r, ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN) == \
        (True, "identita' riverificata senza rete (ok, 1 risposte)")
    rete["pagine"][_nome_es()] = (200, (FIX / "idue_nome_es.json").read_bytes())
    r2 = _r(ticker="QQSYN.MC", nome="Acme Sintetica SA", isin=None)
    assert I.riverifica_identita(r2, ticker="QQSYN.MC", nome="Acme Sintetica SA")[0]


def test_riverifica_ambiguo_e_non_trovato(rete):
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, (FIX / "idue_isin_due.json").read_bytes())
    r = _r()
    assert r["stato"] == "ambiguo" and I.riverifica_identita(r, ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)[0]


def test_riverifica_manomissioni(rete):
    r = _r()
    kw = dict(ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)
    url = I.url_gleif_isin(ISIN)
    m = copy.deepcopy(r)
    m["risposte_salvate"][url] = m["risposte_salvate"][url].replace(LEI_A, LEI_B)
    assert I.riverifica_identita(m, **kw) == (False, "sha256 della risposta salvata %s diverso dalla ricevuta" % url)
    m = copy.deepcopy(r)
    m["lei"] = LEI_B
    ok, motivo = I.riverifica_identita(m, **kw)
    assert not ok and motivo.startswith("lei ricalcolato")
    m = copy.deepcopy(r)
    m["isin"] = None
    assert not I.riverifica_identita(m, **kw)[0]
    assert not I.riverifica_identita(r, ticker="ZZTEST.MI", nome="Beta Sintetica N.V.", isin=ISIN)[0]
    # stesso verdetto ricalcolato, ma la ricevuta e' di un'ALTRA richiesta: rifiutata
    ok, motivo = I.riverifica_identita(r, ticker="ZZTEST.MI", nome="ACME SINTETICA NV", isin=ISIN)
    assert not ok and motivo.startswith("ticker/nome/ISIN")
    assert not I.riverifica_identita(r, ticker="ZZTEST.AS", nome="Acme Sintetica N.V.", isin=ISIN)[0]
    m = copy.deepcopy(r)
    m["stato"] = "non_trovato"
    assert I.riverifica_identita(m, **kw)[1].startswith("stato ricalcolato")
    m = copy.deepcopy(r)
    m["fonte_modulo"] = "altro"
    assert not I.riverifica_identita(m, **kw)[0]
    m = copy.deepcopy(r)
    m["regola"] = 99
    assert not I.riverifica_identita(m, **kw)[0]


def test_riverifica_risposta_mancante_o_in_piu(rete):
    r = _r()
    kw = dict(ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)
    m = copy.deepcopy(r)
    m["risposte_salvate"], m["sha256_liste"], m["url_liste"] = {}, {}, []
    assert not I.riverifica_identita(m, **kw)[0]
    m = copy.deepcopy(r)
    extra = I.url_gleif_nome("acme sintetica", 1)
    m["risposte_salvate"][extra] = "{}"
    m["sha256_liste"][extra] = I._sha("{}")
    m["salvato_ts"][extra] = time.time()
    m["url_liste"].append(extra)
    ok, motivo = I.riverifica_identita(m, **kw)
    assert not ok and "non usate" in motivo


# ---------------- nomi ----------------
def test_nome_base_forme_societarie_solo_in_coda():
    assert I.nome_base("Acme Sintetica SA/NV") == "acme sintetica"
    assert I.nome_base("Acme Sintetica, Société Européenne") == "acme sintetica"
    assert I.nome_base("SA Acme Sintetica") == "sa acme sintetica"
    assert I.nome_base("SE") == "se"
    assert not I.stesso_nome("Acme Holding N.V.", "Acme N.V.")
    assert not I.stesso_nome("", "")


# ---------------- review RV-ID: forma societaria, omonime fuori paese, nome legale ----------------
def _rec(lei, nome, paese, elf, stato="ACTIVE", reg="ISSUED", altri=()):
    return {"type": "lei-records", "id": lei, "attributes": {"lei": lei, "registration": {"status": reg}, "entity": {
        "legalName": {"name": nome}, "otherNames": [{"name": a, "type": "TRADING_OR_OPERATING_NAME"} for a in altri],
        "transliteratedOtherNames": [], "legalAddress": {"country": paese}, "legalForm": {"id": elf, "other": None},
        "status": stato}}}


def _lista(*rec):
    return _b({"meta": {"pagination": {"total": len(rec)}}, "data": list(rec)})


def _elf(rete, codice):
    rete["pagine"][I.url_elf(codice)] = (200, (FIX / ("idue_elf_%s.json" % codice.lower())).read_bytes())


def test_holding_estera_e_controllata_locale_omonima_con_altra_forma(rete):
    """Caso RV-ID P1 (SE e BV omonime): la SE estera e la BV locale «ACME SINTETICA» senza forma nel nome."""
    rete["pagine"][_nome_es()] = (200, _lista(_rec(LEI_A, "ACME SINTETICA", "BE", "ZZBV"),
                                              _rec(LEI_B, "Acme Sintetica SE", "NL", "ZZSE")))
    _elf(rete, "ZZBV")
    r = _r(ticker="QQSYN.BR", nome="Acme Sintetica SE", isin=None)
    assert r["stato"] == "non_trovato" and r["lei"] is None
    assert [c["lei"] for c in r["verifica"]["lei"]["scartati"]] == [LEI_A]
    assert "bv" in r["verifica"]["lei"]["scartati"][0]["motivo"]
    assert [c["lei"] for c in r["verifica"]["lei"]["candidati"]] == [LEI_B]
    assert I.riverifica_identita(r, ticker="QQSYN.BR", nome="Acme Sintetica SE")[0]


def test_nome_legale_senza_forma_forma_dal_codice_elf(rete):
    """Caso misurato a Parigi: il nome legale non scrive la forma, l'ELF dice «Societe Europeenne»."""
    rete["pagine"][_nome_es()] = (200, _lista(_rec(LEI_A, "ACME SINTETICA", "FR", "ZZSE")))
    _elf(rete, "ZZSE")
    r = _r(ticker="QQSYN.PA", nome="Acme Sintetica, Societe Europeenne", isin=None)
    assert r["stato"] == "ok" and r["lei"] == LEI_A and I.url_elf("ZZSE") in r["url_liste"]
    assert I.riverifica_identita(r, ticker="QQSYN.PA", nome="Acme Sintetica, Societe Europeenne")[0]


@pytest.mark.parametrize("elf", [None, "8888"])
def test_forma_del_record_non_leggibile_scartato(rete, elf):
    rete["pagine"][_nome_es()] = (200, _lista(_rec(LEI_A, "ACME SINTETICA", "FR", elf)))
    if elf:
        rete["pagine"][I.url_elf(elf)] = (200, _b({"data": {"attributes": {"code": elf, "names": [{"localName": "altra forma"}]}}}))
    r = _r(ticker="QQSYN.PA", nome="Acme Sintetica SE", isin=None)
    assert r["stato"] == "non_trovato" and r["lei"] is None


def test_elf_irraggiungibile_ko(rete):
    rete["pagine"][_nome_es()] = (200, _lista(_rec(LEI_A, "ACME SINTETICA", "FR", "ZZSE")))
    r = _r(ticker="QQSYN.PA", nome="Acme Sintetica SE", isin=None)
    assert r["stato"] == "KO" and r["errore"] == "http" and r["lei"] is None


def test_nome_senza_forma_omonima_attiva_altrove_ambiguo(rete):
    rete["pagine"][_nome_es()] = (200, _lista(_rec(LEI_A, "ACME SINTETICA", "BE", "ZZBV"),
                                              _rec(LEI_B, "Acme Sintetica SE", "NL", "ZZSE")))
    r = _r(ticker="QQSYN.BR", nome="Acme Sintetica", isin=None)
    assert r["stato"] == "ambiguo" and r["lei"] is None
    assert {c["lei"] for c in r["verifica"]["lei"]["candidati"]} == {LEI_A, LEI_B}
    assert I.LIMITE_SENZA_FORMA in r["limiti"]


def test_omonima_altrove_inattiva_non_rende_ambiguo(rete):
    rete["pagine"][_nome_es()] = (200, _lista(_rec(LEI_A, "Acme Sintetica SE", "FR", "ZZSE"),
                                              _rec(LEI_B, "Acme Sintetica SE", "NL", "ZZSE", stato="INACTIVE", reg="RETIRED")))
    r = _r(ticker="QQSYN.PA", nome="Acme Sintetica SE", isin=None)
    assert r["stato"] == "ok" and r["lei"] == LEI_A


@pytest.mark.parametrize("reg", ["LAPSED", "ANNULLED", "DUPLICATE", "RETIRED", "MERGED"])
def test_ramo_nome_registrazione_non_ammessa_mai_ok(rete, reg):
    """Caso misurato (una N.V. olandese fusa, ACTIVE ma LAPSED): mai 'ok' nel ramo nome."""
    rete["pagine"][_nome_es()] = (200, _lista(_rec(LEI_A, "ACME SINTETICA N.V.", "NL", "ZZNV", reg=reg)))
    r = _r(ticker="QQSYN.AS", nome="Acme Sintetica N.V.", isin=None)
    assert r["stato"] == "non_trovato" and r["lei"] is None and reg in r["motivo"]


def test_ramo_nome_solo_nome_legale_non_commerciale(rete):
    """R3: un nome commerciale/precedente che coincide non basta nel ramo nome."""
    rete["pagine"][I.url_gleif_nome("zeta sintetica", 1)] = (200, _lista(
        _rec(LEI_A, "ACME SINTETICA N.V.", "NL", "ZZNV", altri=["ZETA SINTETICA N.V."])))
    r = _r(ticker="QQSYN.AS", nome="Zeta Sintetica N.V.", isin=None)
    assert r["stato"] == "non_trovato" and r["lei"] is None


# ---------------- review RV-ID P3: STALE sigillato, cache, ricevuta ----------------
def test_stale_riscritto_in_ok_rifiutato(rete):
    _r()
    _invecchia(31)
    rete["pagine"][I.url_gleif_isin(ISIN)] = ConnectionError("x")
    r = _r()
    assert r["stato"] == "STALE"
    m = copy.deepcopy(r)
    m["stato"], m["stato_originale"] = "ok", None
    m["limiti"] = [x for x in m["limiti"] if not x.startswith("STALE")]
    ok, motivo = I.riverifica_identita(m, ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)
    assert not ok and "STALE" in motivo
    m = copy.deepcopy(r)
    m["salvato_ts"] = {k: time.time() for k in m["salvato_ts"]}
    assert not I.riverifica_identita(m, ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)[0]


def test_ricevuta_senza_eta_rifiutata(rete):
    r = _r()
    m = copy.deepcopy(r)
    del m["salvato_ts"]
    assert not I.riverifica_identita(m, ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)[0]


@pytest.mark.parametrize("corpo", [b"", b"{}", b"<html>manutenzione</html>"])
def test_risposta_200_malformata_mai_in_cache(rete, corpo):
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, corpo)
    r = _r()
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato"
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, (FIX / "idue_isin_ok.json").read_bytes())
    assert _r()["stato"] == "ok" and len(rete["chiamate"]) == 2


def test_riverifica_url_liste_diversa_rifiutata(rete):
    """R2: url_liste deve coincidere con le risposte salvate."""
    r = _r()
    kw = dict(ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)
    m = copy.deepcopy(r)
    m["url_liste"] = [I.url_gleif_nome("acme sintetica", 1)]
    assert I.riverifica_identita(m, **kw) == (False, "risposte salvate, sha, url_liste e salvato_ts non coincidono")


def test_cache_di_un_altro_url_ignorata(rete):
    """R5: un file di cache il cui url interno non e' quello chiesto non si serve."""
    _r()
    for f in os.listdir(I.CACHE_DIR):
        p = os.path.join(I.CACHE_DIR, f)
        c = json.load(open(p, encoding="utf-8"))
        c["url"] = I.url_gleif_isin(_oracolo_isin("NL00ZZ00004"))
        json.dump(c, open(p, "w", encoding="utf-8"))
    r = _r()
    assert r["stato"] == "ok" and r["cache"] is None and len(rete["chiamate"]) == 2


def test_ramo_isin_entita_inattiva_anche_se_issued(rete):
    d = _j("idue_isin_ok.json")
    d["data"][0]["attributes"]["entity"]["status"] = "INACTIVE"
    rete["pagine"][I.url_gleif_isin(ISIN)] = (200, _b(d))
    r = _r()
    assert r["stato"] == "non_trovato" and r["lei"] is None and "INACTIVE" in r["motivo"]


def test_ricevuta_con_eta_mancante_per_una_risposta_rifiutata(rete):
    r = _r()
    m = copy.deepcopy(r)
    m["salvato_ts"] = {}
    ok, motivo = I.riverifica_identita(m, ticker="ZZTEST.MI", nome="Acme Sintetica N.V.", isin=ISIN)
    assert not ok and "salvato_ts" in motivo
