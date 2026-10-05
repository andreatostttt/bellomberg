# -*- coding: utf-8 -*-
"""Test di market_data/ue_amf.py (OAM Francia, handoff-3, 05/10/2026, Opus 5.5).
Fixture SINTETICHE (emittente «ACME SINTETICA», ISIN/LEI inventati con cifre di controllo valide)
in tests/fixtures/fonti_ue/amf_*.json, con la FORMA delle risposte vere dell'API Opendatasoft.
Nessuna rete: guardia autouse su socket e requests; la fonte e' un `_scarica` finto."""
import hashlib
import json
import os
import socket
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from bellomberg.market_data import ue_amf as m

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "fonti_ue")
ISIN = "FR00ZZSYNT08"
LEI = "9999ZZSYNTETICA00080"
TICKER = "ZZSYN.PA"

CHIAVI_CONTRATTO = {
    "ticker", "isin", "lei", "nome", "tipo", "periodo_fine", "stato", "errore", "motivo",
    "data_deposito", "ora_deposito", "fuso", "natura_data", "titolo", "url", "url_documento",
    "sha256_documento", "categoria", "lingua", "candidati", "conferme", "fonte", "paese",
    "url_liste", "sha256_liste", "risposte_salvate", "fonte_modulo", "pagine_lette", "letto_il",
    "limiti", "cache", "prova", "scartati"}


def _fix(nome):
    with open(os.path.join(FIX, nome + ".json"), "rb") as fh:
        return fh.read()


@pytest.fixture(autouse=True)
def niente_rete(monkeypatch, tmp_path):
    """Guardia: nessun socket verso fuori, nessun requests.get; cache in tmp_path; pausa nulla."""
    def _vieta(*a, **k):
        raise AssertionError("rete vera nei test di ue_amf")
    monkeypatch.setattr(socket.socket, "connect", _vieta)
    monkeypatch.setattr(socket, "getaddrinfo", _vieta)
    import requests
    monkeypatch.setattr(requests, "get", _vieta)
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path / "cache_ue"))
    monkeypatch.setattr(m, "PAUSA_S", 0)


class Fonte:
    """_scarica finto: risponde con una fixture (o una lista di pagine), conta le chiamate."""
    def __init__(self, corpi, http=200, errore=None):
        self.corpi = corpi if isinstance(corpi, list) else [corpi]
        self.http, self.errore, self.url = http, errore, []

    def __call__(self, url):
        ok, motivo = m.url_ammesso(url)
        assert ok, motivo
        self.url.append(url)
        if self.errore:
            raise self.errore
        return self.http, self.corpi[min(len(self.url), len(self.corpi)) - 1]


def _chiama(monkeypatch, corpi, *, tipo="semestrale", fine="2026-06-30", **k):
    f = Fonte(corpi, **{x: k.pop(x) for x in ("http", "errore") if x in k})
    monkeypatch.setattr(m, "_scarica", f)
    k.setdefault("isin", ISIN)
    return m.get_data_deposito(TICKER, tipo=tipo, periodo_fine=fine, **k), f


# ------------------------------------------------------------ casi ok
def test_semestrale_ok_ora_utc_e_conferma_inglese(monkeypatch):
    r, f = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    assert r["stato"] == "ok" and r["prova"] == "titolo"
    assert (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-07-24", "13:05", "UTC")
    assert r["lingua"] == "fr" and r["titolo"] == "ACME SINTETICA Rapport financier semestriel 2026"
    assert [c["lingua"] for c in r["conferme"]] == ["en"]
    assert r["url_documento"] == r["url"] and r["url"].endswith(".pdf")
    assert r["natura_data"] == "diffusione" and r["paese"] == "FR" and r["fonte_modulo"] == "bellomberg.market_data.ue_amf"
    assert r["sha256_documento"] is None and r["cache"] is None and len(f.url) == 1


def test_chiavi_del_contratto_in_ogni_stato(monkeypatch):
    stati = set()
    monkeypatch.setattr(m, "TTL_S", 0)          # ogni giro rilegge la fonte finta
    for fx, k in (("amf_semestrale_ok", {}), ("amf_ambiguo", {}), ("amf_vuoto", {}), ("amf_vuoto", {"isin": None})):
        r, _ = _chiama(monkeypatch, _fix(fx), **k)
        assert set(r) >= CHIAVI_CONTRATTO, CHIAVI_CONTRATTO - set(r)
        stati.add(r["stato"])
    assert stati == {"ok", "ambiguo", "non_trovato", "KO"}


def test_query_filtra_isin_e_finestra_in_utc(monkeypatch):
    _r, f = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    q = parse_qs(urlsplit(f.url[0]).query)
    assert 'identificationsociete_iso_cd_isi="%s"' % ISIN in q["where"][0]
    assert 'informationdeposee_inf_dat_emt >= "2026-06-30"' in q["where"][0]
    assert 'informationdeposee_inf_dat_emt < "2026-11-28"' in q["where"][0]      # 150 giorni + 1
    assert q["offset"] == ["0"] and q["limit"] == ["100"]


def test_titolo_senza_anno_prova_finestra_e_categoria_tolta(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_senza_anno"))
    assert r["stato"] == "ok" and r["prova"] == "finestra"
    assert (r["data_deposito"], r["ora_deposito"]) == ("2026-07-23", "16:40")
    assert any("titolo senza anno" in x for x in r["limiti"])


def test_annuncio_di_messa_a_disposizione_scartato_se_c_e_il_documento(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_senza_anno"))
    assert r["ora_deposito"] == "16:40" and len(r["candidati"]) == 1
    assert sum("messa a disposizione" in s["motivo"] for s in r["scartati"]) == 2


def test_solo_annuncio_vale_con_limite(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_solo_annuncio"))
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-07-30" and r["candidati"][0]["annuncio"] is True
    assert any("MESSA A DISPOSIZIONE" in x for x in r["limiti"])


def test_contratto_di_liquidita_non_e_la_semestrale(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_solo_inglese"))
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-07-29"
    assert any("liquidita'" in s["motivo"] for s in r["scartati"])


def test_categoria_in_testa_al_titolo_non_fa_da_nome():
    t = m.titolo_proprio("Rapports financiers et d'audit semestriels/examens réduits / Comptes consolidés 2025",
                         ["Rapports financiers et d'audit semestriels/examens réduits", None])
    assert t == "Comptes consolidés 2025"


def test_annuale_dal_deu_e_amendamento_scartato(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_annuale_deu"), tipo="annuale", fine="2025-12-31")
    assert r["stato"] == "ok" and r["titolo"] == "Document d'enregistrement universel 2025"
    assert r["url_documento"] is None and r["url"].endswith(".zip")      # ZIP: non e' un PDF
    assert any("amendamento" in s["motivo"] for s in r["scartati"])


def test_categoria_dell_emittente_sbagliata_non_conta(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_categoria_sbagliata"), tipo="annuale", fine="2025-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-02-11" and r["prova"] == "titolo"
    r2, _ = _chiama(monkeypatch, _fix("amf_categoria_sbagliata"), tipo="semestrale", fine="2025-06-30")
    assert r2["stato"] == "non_trovato" and r2["data_deposito"] is None


def test_titolo_e_finestra_con_date_diverse_ambiguo(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_ambiguo"))
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and r["titolo"] is None
    assert sorted(c["prova"] for c in r["candidati"]) == ["finestra", "titolo"]
    assert "nessuno scelto" in r["motivo"]


def test_altro_anno_nel_titolo_scartato(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_altro_anno"))
    assert r["stato"] == "non_trovato"
    assert [s["motivo"][:28] for s in r["scartati"]] == ["il titolo cita un anno/perio"]


def test_non_trovato_dice_dove_e_quando(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_vuoto"))
    assert r["stato"] == "non_trovato" and r["errore"] is None
    assert "non trovato nella fonte AMF" in r["motivo"] and "2026-06-30" in r["motivo"] and "2026-11-27" in r["motivo"]
    assert "mai" not in r["motivo"]


def test_solo_inglese_sceglie_l_inglese(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_solo_inglese"))
    assert r["stato"] == "ok" and r["lingua"] == "en" and r["conferme"] == []


def test_trimestrale_primo_trimestre(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_trimestrale"), tipo="trimestrale", fine="2026-03-31")
    assert r["stato"] == "ok" and r["prova"] == "titolo" and r["data_deposito"] == "2026-04-23"


def test_trimestrale_l_anno_accanto_al_nome_non_basta():
    per = m.regex_periodo(date(2026, 9, 30), "trimestrale")
    assert not per.search("Information financière du premier trimestre 2026")
    assert per.search("Information financière du troisième trimestre 2026")


# ------------------------------------------------------------ regola pura: confini
def _riga(quando, titolo, lingua="fr"):
    return {"id": None, "quando_utc": quando, "data": quando[:10], "ora": quando[11:16], "titolo": titolo,
            "titolo_proprio": titolo, "url": "https://x/1.pdf", "categoria": None, "lingua": lingua,
            "stato_aggiornamento": None}


def test_diffuso_il_giorno_della_fine_periodo_scartato():
    v = m.scegli([_riga("2026-06-30T18:00:00+00:00", "Rapport financier semestriel 2026")], "semestrale", date(2026, 6, 30))
    assert v["stato"] == "non_trovato" and "non dopo la fine del periodo" in v["scartati"][0]["motivo"]


def test_oltre_la_finestra_scartato_e_ultimo_giorno_tenuto():
    fine = date(2026, 6, 30)
    v = m.scegli([_riga("2026-11-28T08:00:00+00:00", "Rapport financier semestriel")], "semestrale", fine)
    assert v["stato"] == "non_trovato" and "oltre la finestra" in v["scartati"][0]["motivo"]
    v = m.scegli([_riga("2026-11-27T08:00:00+00:00", "Rapport financier semestriel")], "semestrale", fine)
    assert v["stato"] == "ok" and v["prova"] == "finestra"


def test_stesso_deposito_duplicato_e_uno_solo():
    r = _riga("2026-07-24T13:05:00+00:00", "Rapport financier semestriel 2026")
    v = m.scegli([r, dict(r, url="https://x/2.pdf")], "semestrale", date(2026, 6, 30))
    assert v["stato"] == "ok" and len(v["conferme"]) == 1


def test_nome_obbligatorio():
    v = m.scegli([_riga("2026-07-24T13:05:00+00:00", "Résultats du premier semestre 2026")], "semestrale", date(2026, 6, 30))
    assert v["stato"] == "non_trovato" and v["scartati"] == []


# ------------------------------------------------------------ identita' e parametri (nessuna rete)
def test_identita_mancante_senza_rete(monkeypatch):
    r, f = _chiama(monkeypatch, _fix("amf_vuoto"), isin=None)
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and "isin" in r["motivo"] and f.url == []


def test_isin_non_valido_senza_rete(monkeypatch):
    r, f = _chiama(monkeypatch, _fix("amf_vuoto"), isin="FR00ZZSYNT09")
    assert r["errore"] == "identita_non_valida" and f.url == []


def test_lei_come_filtro(monkeypatch):
    assert m.lei_valido(LEI) and not m.lei_valido(LEI[:-1] + "1")
    r, f = _chiama(monkeypatch, _fix("amf_semestrale_ok"), isin=None, lei=LEI)
    assert r["stato"] == "ok" and 'identificationsociete_iso_cd_lei="%s"' % LEI in parse_qs(urlsplit(f.url[0]).query)["where"][0]


@pytest.mark.parametrize("tipo,fine", [("semestrale", "2026-03-31"), ("trimestrale", "2026-06-30"),
                                       ("mensile", "2026-06-30"), ("annuale", "2025-12-30"), ("annuale", "31/12/2025")])
def test_parametri_incoerenti_ko_senza_rete(monkeypatch, tipo, fine):
    r, f = _chiama(monkeypatch, _fix("amf_vuoto"), tipo=tipo, fine=fine)
    assert r["stato"] == "KO" and r["errore"] == "parametro" and f.url == []


# ------------------------------------------------------------ guasti della fonte
def test_filtro_ignorato_dalla_fonte_ko(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_estraneo"))
    assert r["stato"] == "KO" and r["errore"] == "filtro_ignorato" and r["data_deposito"] is None


def test_formato_cambiato_ko(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_formato_cambiato"))
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato"
    r, _ = _chiama(monkeypatch, b"<html>manutenzione</html>")
    assert r["errore"] == "formato_cambiato"


def test_fuso_convertito_in_utc_e_data_senza_fuso_illeggibile(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_fusi"))
    assert r["stato"] == "ok" and (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-07-24", "13:05", "UTC")
    assert any("1 righe illeggibili" in x for x in r["limiti"])


def test_rete_ko_dice_solo_il_tipo(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_vuoto"), errore=ConnectionError("https://segreto?k=1"))
    assert r["stato"] == "KO" and r["errore"] == "rete" and "ConnectionError" in r["motivo"]
    assert "segreto" not in r["motivo"]


def test_http_ko_poi_stale_con_cache_scaduta(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    assert r["stato"] == "ok"
    r, f = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    assert r["cache"] == "fresca" and f.url == []            # cache fresca: nessuna rete
    monkeypatch.setattr(m, "TTL_S", 0)
    r, _ = _chiama(monkeypatch, b"", http=503)
    assert r["stato"] == "STALE" and r["stato_originale"] == "ok" and r["cache"] == "scaduta"
    assert r["errore"] == "http" and "503" in r["motivo"] and r["data_deposito"] == "2026-07-24"


def test_ko_senza_cache_resta_ko(monkeypatch):
    r, _ = _chiama(monkeypatch, b"", http=500)
    assert r["stato"] == "KO" and r["errore"] == "http" and r["cache"] is None


def test_non_trovato_non_si_mette_in_cache(monkeypatch):
    _chiama(monkeypatch, _fix("amf_vuoto"))
    r, f = _chiama(monkeypatch, _fix("amf_vuoto"))
    assert len(f.url) == 1 and r["cache"] is None


def test_paginazione_legge_tutte_le_pagine(monkeypatch):
    base = json.loads(_fix("amf_semestrale_ok"))
    rumore, buone = base["results"][0], base["results"][2:]
    p1 = json.dumps({"total_count": 102, "results": [dict(rumore, uin_idt_uin="P%d" % i) for i in range(100)]}).encode()
    p2 = json.dumps({"total_count": 102, "results": buone}).encode()
    r, f = _chiama(monkeypatch, [p1, p2])
    assert r["stato"] == "ok" and r["pagine_lette"] == 2 and len(f.url) == 2
    assert parse_qs(urlsplit(f.url[1]).query)["offset"] == ["100"]


def test_troppe_pagine_ko_troncato(monkeypatch):
    base = json.loads(_fix("amf_semestrale_ok"))
    p = json.dumps({"total_count": 999, "results": [dict(base["results"][0], uin_idt_uin="P%d" % i)
                                                     for i in range(100)]}).encode()
    r, f = _chiama(monkeypatch, p)
    assert r["stato"] == "KO" and r["errore"] == "troncato" and len(f.url) == m.MAX_PAGINE


# ------------------------------------------------------------ rete: host ammessi
def test_url_ammessi_solo_api_amf_https():
    assert m.url_ammesso(m.url_query(ISIN, None, "annuale", date(2025, 12, 31), 0))[0]
    assert not m.url_ammesso("http://www.info-financiere.gouv.fr" + m.AMMESSI["www.info-financiere.gouv.fr"][0])[0]
    assert not m.url_ammesso("https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/altro/records")[0]
    assert not m.url_ammesso("https://example.org/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records")[0]


def test_scarica_vero_rifiuta_prima_della_rete():
    with pytest.raises(m.URLVietato):
        m._scarica("https://example.org/x")


def test_docstring_dichiara_licenza_e_robots():
    assert m.URL_LICENZA in m.__doc__ and "Disallow: /api/" in m.__doc__ and "Licence Ouverte" in m.__doc__


# ------------------------------------------------------------ riverifica senza rete
def test_riverifica_ok_e_ambiguo(monkeypatch):
    for fx in ("amf_semestrale_ok", "amf_ambiguo", "amf_vuoto"):
        r, _ = _chiama(monkeypatch, _fix(fx))
        monkeypatch.setattr(m, "_scarica", None)                 # la riverifica non deve chiamare la rete
        ok, motivo = m.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
        assert ok, motivo


def test_riverifica_rifiuta_ricevute_alterate(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    u = r["url_liste"][0]
    alterata = json.loads(json.dumps(r))
    alterata["risposte_salvate"][u] = alterata["risposte_salvate"][u].replace("13:05", "09:05")
    ok, motivo = m.riverifica_ricevuta(alterata, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "sha256" in motivo
    # corpo alterato CON impronta ricalcolata: la scelta ricalcolata non coincide piu'
    alterata["sha256_liste"][u] = hashlib.sha256(alterata["risposte_salvate"][u].encode("utf-8")).hexdigest()
    ok, motivo = m.riverifica_ricevuta(alterata, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "ora_deposito" in motivo
    data = dict(r, data_deposito="2026-07-20")
    assert not m.riverifica_ricevuta(data, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")[0]
    assert not m.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2025-06-30")[0]
    assert not m.riverifica_ricevuta(r, ticker="ALTRO.PA", tipo="semestrale", periodo_fine="2026-06-30")[0]
    altra = dict(r, isin="FR00QQSYNT00")
    ok, motivo = m.riverifica_ricevuta(altra, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "query" in motivo
    assert not m.riverifica_ricevuta(dict(r, fonte_modulo="altro"), ticker=TICKER, tipo="semestrale",
                                     periodo_fine="2026-06-30")[0]


# ------------------------------------------------------------ AGGIUNTA 2 del contratto UE
def test_nomi_documento_costante_pubblica_uniforme():
    assert set(m.NOMI_DOCUMENTO) == {"annuale", "semestrale", "trimestrale"}
    import re
    for tipo, lista in m.NOMI_DOCUMENTO.items():
        assert isinstance(lista, list) and lista and all(isinstance(x, str) for x in lista)
        for x in lista:
            re.compile(x, re.I)
    assert any(re.search(x, "Document d'enregistrement universel 2025", re.I) for x in m.NOMI_DOCUMENTO["annuale"])


def test_scegli_usa_nomi_documento(monkeypatch):
    """Cablaggio: la regola legge NOMI_DOCUMENTO, non una copia privata."""
    riga = _riga("2026-07-24T13:05:00+00:00", "Rapport financier semestriel 2026")
    assert m.scegli([riga], "semestrale", date(2026, 6, 30))["stato"] == "ok"
    monkeypatch.setitem(m.NOMI_DOCUMENTO, "semestrale", [r"nome\s+inesistente\s+zz"])
    assert m.scegli([riga], "semestrale", date(2026, 6, 30))["stato"] == "non_trovato"


def test_candidati_e_conferme_formato_uniforme(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_ambiguo"))
    r2, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    elementi = r["candidati"] + r2["candidati"] + r2["conferme"]
    assert len(elementi) == 4
    for c in elementi:
        assert {"titolo", "data", "ora", "url", "categoria", "lingua", "prova"} <= set(c)
        assert len(c["data"]) == 10 and len(c["ora"]) == 5 and c["prova"] in ("titolo", "finestra")


def test_riverifica_ambiguo_confronta_la_lista_dei_candidati(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_ambiguo"))
    assert r["stato"] == "ambiguo"
    for alterata in (dict(r, candidati=r["candidati"][:1]),
                     dict(r, candidati=[dict(r["candidati"][0], ora="00:00"), r["candidati"][1]]),
                     dict(r, candidati=r["candidati"] + [dict(r["candidati"][0], url="https://x/altro.pdf")])):
        ok, motivo = m.riverifica_ricevuta(alterata, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
        assert not ok and "candidati ricalcolati" in motivo


def test_riverifica_rifiuta_ricevuta_ko(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_estraneo"))
    ok, motivo = m.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "KO" in motivo


# ------------------------------------------------------------ rilievi del revisore RV-UE1
def _risposta(*righe, totale=None):
    """Corpo JSON nella forma dell'API, dalle righe della fixture semestrale (modificate)."""
    return json.dumps({"total_count": len(righe) if totale is None else totale, "results": list(righe)}).encode()


def _base_riga(**k):
    r = dict(json.loads(_fix("amf_semestrale_ok"))["results"][2])
    r.update(k)
    return r


def test_amf2_documento_diverso_in_altra_lingua_e_ambiguo():
    fine = date(2025, 12, 31)
    righe = [_riga("2026-02-12T13:51:00+00:00", "ACME Comptes consolidés 2025", "fr"),
             _riga("2026-03-31T14:26:00+00:00", "ACME Universal Registration Document 2025", "en")]
    v = m.scegli(righe, "annuale", fine)
    assert v["stato"] == "ambiguo" and len(v["candidati"]) == 2 and v["conferme"] == []
    # la stessa coppia ma lo STESSO deposito (entro CONFERMA_MINUTI): conferma
    righe[1] = _riga("2026-02-12T14:20:00+00:00", "ACME 2025 Consolidated Financial Statements", "en")
    v = m.scegli(righe, "annuale", fine)
    assert v["stato"] == "ok" and len(v["conferme"]) == 1


def test_amf3_total_count_non_coperto_ko_e_riverifica_false(monkeypatch):
    corpo = _risposta(_base_riga(), totale=30)
    r, _ = _chiama(monkeypatch, corpo)
    assert r["stato"] == "KO" and r["errore"] == "troncato" and "30" in r["motivo"]
    # una ricevuta costruita a mano su quella risposta non si riverifica
    u = m.url_query(ISIN, None, "semestrale", date(2026, 6, 30), 0)
    finta = dict(r, stato="ok", url_liste=[u], risposte_salvate={u: corpo.decode()},
                 sha256_liste={u: hashlib.sha256(corpo).hexdigest()})
    ok, motivo = m.riverifica_ricevuta(finta, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "non coprono" in motivo


def test_amf1_semestrale_non_e_l_annuale():
    v = m.scegli([_riga("2026-07-10T08:00:00+00:00", "Semi-annual financial report", "en")], "annuale", date(2025, 12, 31))
    assert v["stato"] == "non_trovato" and "semestrale/trimestrale" in v["scartati"][0]["motivo"]


def test_amf4_isin_e_lei_incoerenti_ko(monkeypatch):
    r, _ = _chiama(monkeypatch, _risposta(_base_riga(identificationsociete_iso_cd_lei="9999ZZALTRA000000000")), lei=LEI)
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente"
    r, _ = _chiama(monkeypatch, _risposta(_base_riga(identificationsociete_iso_cd_lei=None)), lei=LEI)
    assert r["stato"] == "ok"                      # LEI assente nella riga: nessuna contraddizione
    ok, motivo = m.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo


def test_amf5_mezzanotte_dichiarata(monkeypatch):
    r, _ = _chiama(monkeypatch, _risposta(_base_riga(informationdeposee_inf_dat_emt="2026-07-24T22:30:00+00:00")))
    assert (r["data_deposito"], r["ora_deposito"], r["fuso"]) == ("2026-07-24", "22:30", "UTC")
    assert (r["data_deposito_locale"], r["ora_deposito_locale"], r["fuso_locale"]) == ("2026-07-25", "00:30", "Europe/Paris")
    assert any(x.startswith("MEZZANOTTE") for x in r["limiti"])
    monkeypatch.setattr(m, "TTL_S", 0)          # niente cache fra le due letture
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    assert r["data_deposito_locale"] == "2026-07-24" and not any(x.startswith("MEZZANOTTE") for x in r["limiti"])


def test_filtro_lei_senza_isin_ignorato_ko(monkeypatch):
    r, _ = _chiama(monkeypatch, _risposta(_base_riga(identificationsociete_iso_cd_lei="X" * 20)), isin=None, lei=LEI)
    assert r["stato"] == "KO" and r["errore"] == "filtro_ignorato"


def test_rettifica_dichiarata_nei_limiti(monkeypatch):
    r, _ = _chiama(monkeypatch, _risposta(_base_riga(
        informationdeposee_inf_tit_inf="Rapport financier semestriel 2026 (version rectifiée)")))
    assert r["stato"] == "ok" and r["candidati"][0]["rettifica"] is True
    assert any("rettifica" in x for x in r["limiti"])


def test_tutte_le_righe_illeggibili_ko_formato(monkeypatch):
    r, _ = _chiama(monkeypatch, _risposta(_base_riga(informationdeposee_inf_dat_emt=None),
                                          _base_riga(url_de_recuperation="ftp://x")))
    assert r["stato"] == "KO" and r["errore"] == "formato_cambiato"


# ------------------------------------------------------------ RV-UE1 giro 2: AMF-4c (cache e STALE)
LEI_2 = "9999ZZALTRA000000079"       # secondo LEI sintetico valido


def test_amf4c_cache_non_serve_l_ok_a_un_altro_lei(monkeypatch):
    assert m.lei_valido(LEI_2)
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"), lei=LEI)
    assert r["stato"] == "ok"
    # stesso ISIN, LEI diverso: la fonte (righe col LEI vero) lo contraddice -> KO, non la cache fresca
    r, f = _chiama(monkeypatch, _fix("amf_semestrale_ok"), lei=LEI_2)
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente" and r["cache"] is None and len(f.url) == 1


def test_amf4c_ko_d_identita_mai_stale(monkeypatch):
    _chiama(monkeypatch, _fix("amf_semestrale_ok"), lei=LEI)
    monkeypatch.setattr(m, "TTL_S", 0)
    altro = _risposta(_base_riga(identificationsociete_iso_cd_lei="9999ZZALTRA000000000"))
    r, _ = _chiama(monkeypatch, altro, lei=LEI)
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente" and r["data_deposito"] is None
    r, _ = _chiama(monkeypatch, _risposta(_base_riga(), totale=30), lei=LEI)
    assert r["stato"] == "KO" and r["errore"] == "troncato"
    r, _ = _chiama(monkeypatch, _fix("amf_estraneo"), lei=LEI)
    assert r["stato"] == "KO" and r["errore"] == "filtro_ignorato"
    # un guasto della FONTE invece si', dichiarato
    r, _ = _chiama(monkeypatch, b"", http=503, lei=LEI)
    assert r["stato"] == "STALE" and r["data_deposito"] == "2026-07-24"


def test_lei_non_valido_anche_con_isin_senza_rete(monkeypatch):
    r, f = _chiama(monkeypatch, _fix("amf_semestrale_ok"), lei=LEI[:-1] + "1")
    assert r["stato"] == "KO" and r["errore"] == "identita_non_valida" and f.url == []


def test_amf4c_cache_con_identita_diversa_non_si_serve(monkeypatch):
    """Cintura 2: un file di cache sotto la chiave giusta ma con un'altra identita' (scritto da
    una versione vecchia o alterato) non si serve: si rilegge la fonte."""
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"), lei=LEI)
    chiave = "amf_v%d_%s_%s_%s_%s" % (m.VERSIONE_REGOLA, ISIN, LEI, "semestrale", "2026-06-30")
    c = m._cache_leggi(chiave)
    assert c is not None
    m._cache_scrivi(chiave, dict(c["risultato"], lei=LEI_2))
    r, f = _chiama(monkeypatch, _fix("amf_semestrale_ok"), lei=LEI)
    assert r["cache"] is None and len(f.url) == 1 and r["lei"] == LEI


# ------------------------------------------------------------ AGGIUNTA 4: paese dal router
def test_paese_dal_router_accettato_e_riportato(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"))
    assert r["paese"] == "FR"                                  # default
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"), paese="FR")
    assert r["stato"] == "ok" and r["paese"] == "FR" and not any("instradato" in x for x in r["limiti"])
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"), paese="NL")
    assert r["stato"] == "ok" and r["paese"] == "NL" and any("instradato dal router come paese NL" in x for x in r["limiti"])
    r, f = _chiama(monkeypatch, _fix("amf_vuoto"), isin=None, paese="FR")
    assert r["errore"] == "identita_mancante" and r["paese"] == "FR" and f.url == []


def test_riverifica_con_paese_del_router(monkeypatch):
    r, _ = _chiama(monkeypatch, _fix("amf_semestrale_ok"), paese="FR")
    assert m.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30", paese="FR")[0]
    assert m.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30")[0]   # senza paese
    ok, motivo = m.riverifica_ricevuta(r, ticker=TICKER, tipo="semestrale", periodo_fine="2026-06-30", paese="BE")
    assert not ok and "paese" in motivo and "sigillato" in motivo
