# -*- coding: utf-8 -*-
"""emarket_sdir.py (voce 6, 04/10/2026, Opus 5.5): internal dealing italiano da eMarket SDIR.

Nessuna rete: `borsa_italiana._scarica` (il fetcher comune, con la guardia robots) e'
sostituito da un finto server sulle fixture SINTETICHE di tests/fixtures/fonti_it/ (persona,
emittente, ISIN e numeri inventati; struttura del modello MAR e della lista Drupal misurate
il 04/10). Il cablaggio di `_scarica` e' provato in test_borsa_italiana.py.
"""
import json
import os
from datetime import date

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import emarket_sdir as em

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fonti_it")
ISIN_ACME = "ITZZACME0007"
LISTA = em.URL_LISTA.format(id=4242)


def _fixture(nome):
    with open(os.path.join(FIX, nome), "rb") as fh:
        return fh.read()


def _pdf_url(prot, mese="2026-09"):
    return em.BASE + "/sites/default/files/comunicati/%s/sint_%s.pdf" % (mese, prot)


def _testo_pdf(nome):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(os.path.join(FIX, nome)).pages)


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"ACME.MI": {"isin": ISIN_ACME, "emarket": 4242},
                                   "ZZTEST.MI": {"isin": "ITZZTEST0001", "emarket": None},
                                   "QQSYN.MI": {"isin": "ITZZQQSYN005", "emarket": 777777}}),
                       encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(negozio.parent / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 4))
    monkeypatch.setattr(em, "PAUSA_S", 0)
    # IT2b 05/10: la sezione DOCUMENTI (emarket_documenti, IT3) e' l'ancora primaria del deposito.
    # Qui si provano le regole dei COMUNICATI: la sezione Documenti risponde «nessun documento»
    # (vuoto_misurato), quindi vale il comunicato. Il cablaggio vero e' in test_emarket_deposito_documenti.py.
    from bellomberg.market_data import emarket_documenti as ed
    monkeypatch.setattr(ed, "leggi_documenti", lambda id_em, **k: {
        "stato": "vuoto_misurato", "errore": None, "motivo": None, "righe": [], "url_liste": [],
        "sha256_liste": {}, "pagine_lette": 0, "limiti": []})
    risposte, chieste = {}, []

    def finto(url):
        ok, motivo = bi.url_consentito(url)
        assert ok, motivo
        chieste.append(url)
        r = risposte.get(url)
        if r is None:
            raise ConnectionError("nessuna risposta finta")
        if isinstance(r, Exception):
            raise r
        return r[0], r[1], url
    monkeypatch.setattr(bi, "_scarica", finto)
    return {"risposte": risposte, "chieste": chieste, "tmp": tmp_path}


def _lista_ok(amb, pdf2="id_multipla.pdf", pdf1="id_singola.pdf"):
    amb["risposte"][LISTA] = (200, _fixture("em_lista_ok.html"))
    amb["risposte"][_pdf_url("900002")] = (200, _fixture(pdf2))
    amb["risposte"][_pdf_url("900001")] = (200, _fixture(pdf1))


# ------------------------------------------------------------ A. parser del PDF
def test_pdf_singola_operazione_prezzo_zero_ha_la_nota():
    p = em.parse_pdf_internal_dealing(_fixture("id_singola.pdf"))
    assert p["parse_ok"] and p["motivo_parse"] is None
    assert p["soggetto"] == "Zeno Fittizio" and p["ruolo"] == "Amministratore Delegato"
    (op,) = p["operazioni"]
    assert op["prezzo"] == 0 and op["quantita"] == 5000 and op["valuta"] == "EUR"
    assert op["data_operazione"] == "2026-09-14" and op["isin"] == ISIN_ACME
    assert op["tipo_operazione"].startswith("Altro / Other - ATTRIBUZIONE GRATUITA")
    assert "NON un acquisto" in op["nota"]


def test_pdf_due_operazioni_e_righe_multiple():
    p = em.parse_pdf_internal_dealing(_fixture("id_multipla.pdf"))
    assert p["parse_ok"], p["motivo_parse"]
    a, b = p["operazioni"]
    assert (a["tipo_operazione"], a["prezzo"], a["quantita"], a["data_operazione"]) == \
        ("Acquisto / Purchase", 12.34, 1500, "2026-09-18")
    assert (b["tipo_operazione"], b["prezzo"], b["quantita"], b["data_operazione"]) == \
        ("Vendita / Sale", 12.57, 1000, "2026-09-19")
    assert b["righe_prezzo_volume"] == [["12.5000", "300"], ["12.6000", "700"]]
    assert a["nota"] is None and b["luogo"] == "EURONEXT MILAN - MTAA"


def test_pdf_senza_etichette_parse_ok_false_e_campi_none():
    p = em.parse_pdf_internal_dealing(_fixture("id_senza_etichette.pdf"))
    assert p["parse_ok"] is False
    assert p["soggetto"] is None and p["ruolo"] is None and p["operazioni"] == []
    assert "sezione 4" in p["motivo_parse"]


def test_pdf_illeggibile_dichiarato():
    p = em.parse_pdf_internal_dealing(b"questo non e' un pdf")
    assert p["parse_ok"] is False and p["motivo_parse"].startswith("PDF illeggibile")


@pytest.mark.parametrize("cosa,da,a,motivo,campi_none", [
    ("quantita ambigua", "Volume aggregato: 5000", "Volume aggregato: 5.000", "ambiguo", ["quantita"]),
    ("prezzo con due separatori", "Prezzo: 0 EUR", "Prezzo: 1.234,5 EUR", "non riconosciuto", ["prezzo"]),
    ("data operazione mancante", "2026-09-14 - 08:00:00", "quattordici settembre", "data operazione",
     ["data_operazione"]),
    ("data operazione impossibile", "2026-09-14 - 08:00:00", "2026-02-30 - 08:00:00", "non valida",
     ["data_operazione"]),
    ("tipo mancante", "Altro / Other - ATTRIBUZIONE GRATUITA DI AZIONI DEL PIANO SINTETICO\n", "",
     "tipo operazione", ["tipo_operazione"]),
])
def test_etichetta_rotta_parse_ok_false_campi_none(cosa, da, a, motivo, campi_none):
    testo = _testo_pdf("id_singola.pdf")
    assert da in testo, cosa
    p = em.parse_testo_internal_dealing(testo.replace(da, a))
    (op,) = p["operazioni"]
    assert op["parse_ok"] is False and motivo in op["motivo"], op["motivo"]
    assert p["parse_ok"] is False and "operazioni non lette" in p["motivo_parse"]
    for c in campi_none:
        assert op[c] is None, c


def test_somma_righe_diversa_dal_volume_aggregato_niente_numeri():
    testo = _testo_pdf("id_multipla.pdf").replace("12.6000 EUR 700", "12.6000 EUR 800")
    p = em.parse_testo_internal_dealing(testo)
    b = p["operazioni"][1]
    assert b["parse_ok"] is False and "diversa dal volume aggregato" in b["motivo"]
    assert b["prezzo"] is None and b["quantita"] is None
    assert p["operazioni"][0]["parse_ok"] is True


def test_senza_aggregati_e_piu_righe_niente_media_indovinata():
    testo = _testo_pdf("id_multipla.pdf")
    i = testo.index("Operazione - 2")
    coda = testo[i:].replace("Volume aggregato: 1000", "").replace("Prezzo: 12.5700 EUR", "")
    op = em.parse_testo_internal_dealing(testo[:i] + coda)["operazioni"][1]
    assert op["parse_ok"] is False and op["prezzo"] is None and op["quantita"] is None
    assert "2 righe" in op["motivo"]


def test_senza_aggregati_riga_unica_letta():
    testo = _testo_pdf("id_singola.pdf").replace("Volume aggregato: 5000", "").replace("Prezzo: 0 EUR", "")
    (op,) = em.parse_testo_internal_dealing(testo)["operazioni"]
    assert op["parse_ok"] and op["prezzo"] == 0 and op["quantita"] == 5000


def test_soggetto_e_ruolo_mancanti_parse_ok_false():
    testo = _testo_pdf("id_singola.pdf").replace("First name:", "Nome proprio:").replace("Role:", "Funzione:")
    p = em.parse_testo_internal_dealing(testo)
    assert p["soggetto"] is None and p["ruolo"] is None and p["parse_ok"] is False
    assert "soggetto" in p["motivo_parse"] and "ruolo" in p["motivo_parse"]
    assert p["operazioni"][0]["parse_ok"] is True   # le operazioni si leggono lo stesso


# ------------------------------------------------------------ B. parser della lista
def test_lista_ok_righe():
    r = em.parse_lista(_fixture("em_lista_ok.html").decode("utf-8"), 4242)
    assert r["stato"] == "ok" and not r["pagina_successiva"]
    assert [(x["data"], x["ora"], x["protocollo"]) for x in r["righe"]] == [
        ("2026-09-20", "18:30", "900002"), ("2026-09-15", "09:05", "900001"), ("2026-01-10", "12:00", "900000")]
    assert r["righe"][0]["url_pdf"] == _pdf_url("900002")


def test_lista_vuota_con_id_nel_menu_e_vuoto_misurato():
    assert em.parse_lista(_fixture("em_lista_vuota.html").decode("utf-8"), 4242)["stato"] == "vuoto_misurato"


def test_lista_vuota_con_id_fuori_menu_e_non_coperto():
    r = em.parse_lista(_fixture("em_lista_vuota.html").decode("utf-8"), 777777)
    assert r["stato"] == "non_coperto" and "777777" in r["motivo"]


def test_lista_layout_cambiato_KO():
    r = em.parse_lista(_fixture("em_lista_layout.html").decode("utf-8"), 4242)
    assert r["stato"] == "KO" and r["errore"] == "layout_cambiato"


def test_pagina_diversa_KO():
    r = em.parse_lista(_fixture("em_lista_altra.html").decode("utf-8"), 4242)
    assert r["stato"] == "KO" and r["errore"] == "pagina_diversa"


# ------------------------------------------------------------ C. la funzione pubblica
def test_internal_dealing_ok_finestra_e_pdf(ambiente):
    _lista_ok(ambiente)
    r = em.get_internal_dealing("ACME.MI", giorni=180)
    assert r["stato"] == "ok", r["motivo"]
    assert r["isin"] == ISIN_ACME and r["emarket_id"] == 4242 and r["url"] == LISTA and r["letto_il"]
    # la riga del 10/01/2026 e' fuori dai 180 giorni (soglia 2026-04-07): il suo PDF non si scarica
    assert [c["protocollo"] for c in r["comunicazioni"]] == ["900002", "900001"]
    assert _pdf_url("900000", "2026-01") not in ambiente["chieste"]
    c2, c1 = r["comunicazioni"]
    assert c2["soggetto"] == "Zeno Fittizio" and len(c2["operazioni"]) == 2 and c2["parse_ok"]
    assert c2["errore"] is None and all(o["isin_coerente"] for o in c2["operazioni"])
    assert c1["operazioni"][0]["quantita"] == 5000 and c1["data"] == "2026-09-15"
    assert (r["pdf_letti"], r["pdf_falliti"], r["parse_falliti"], r["troncato"]) == (2, 0, 0, False)


def test_finestra_corta_tutto_fuori_vuoto_misurato(ambiente):
    _lista_ok(ambiente)
    r = em.get_internal_dealing("ACME.MI", giorni=5)
    assert r["stato"] == "vuoto_misurato" and "2026-09-20" in r["motivo"]
    assert ambiente["chieste"] == [LISTA]


def test_pdf_illeggibile_resta_con_link_e_parse_ok_false(ambiente):
    _lista_ok(ambiente, pdf2="id_senza_etichette.pdf")
    r = em.get_internal_dealing("ACME.MI")
    c = r["comunicazioni"][0]
    assert c["parse_ok"] is False and c["soggetto"] is None and c["url_pdf"] == _pdf_url("900002")
    assert r["stato"] == "ok" and r["parse_falliti"] == 1 and "1 comunicazioni su 2" in r["motivo"]


def test_pdf_non_scaricato_dichiarato(ambiente):
    _lista_ok(ambiente)
    ambiente["risposte"][_pdf_url("900001")] = (404, b"")
    r = em.get_internal_dealing("ACME.MI")
    c = r["comunicazioni"][1]
    assert c["parse_ok"] is False and "HTTP 404" in c["motivo_parse"] and r["pdf_falliti"] == 1


def test_tetto_pdf_dichiarato(ambiente, monkeypatch):
    monkeypatch.setattr(em, "MAX_PDF", 1)
    _lista_ok(ambiente)
    r = em.get_internal_dealing("ACME.MI")
    assert r["troncato"] and r["pdf_non_letti"] == 1 and _pdf_url("900001") not in ambiente["chieste"]
    assert "tetto" in r["comunicazioni"][1]["motivo_parse"]


def test_paginazione_segue_la_pagina_successiva(ambiente):
    ambiente["risposte"][LISTA] = (200, _fixture("em_lista_pagina1.html"))
    ambiente["risposte"][LISTA + "&page=1"] = (200, _fixture("em_lista_ok.html"))
    for prot, mese in (("900004", "2026-10"), ("900003", "2026-09"), ("900002", "2026-09"), ("900001", "2026-09")):
        ambiente["risposte"][_pdf_url(prot, mese)] = (200, _fixture("id_singola.pdf"))
    r = em.get_internal_dealing("ACME.MI")
    assert r["pagine_lette"] == 2 and len(r["comunicazioni"]) == 4 and not r["troncato"]


def test_paginazione_troncata_dichiarata(ambiente, monkeypatch):
    monkeypatch.setattr(em, "MAX_PAGINE", 1)
    ambiente["risposte"][LISTA] = (200, _fixture("em_lista_pagina1.html"))
    for prot, mese in (("900004", "2026-10"), ("900003", "2026-09")):
        ambiente["risposte"][_pdf_url(prot, mese)] = (200, _fixture("id_singola.pdf"))
    r = em.get_internal_dealing("ACME.MI")
    assert r["troncato"] and any("pagine" in l for l in r["limiti"])


def test_KO_a_meta_paginazione_non_serve_mezza_lista(ambiente):
    ambiente["risposte"][LISTA] = (200, _fixture("em_lista_pagina1.html"))
    ambiente["risposte"][LISTA + "&page=1"] = (502, b"")
    r = em.get_internal_dealing("ACME.MI")
    assert r["stato"] == "KO" and r["comunicazioni"] == [] and "pagina 2" in r["motivo"]


def test_non_coperto_dichiarato_nel_negozio_senza_rete(ambiente):
    r = em.get_internal_dealing("ZZTEST.MI")
    assert r["stato"] == "non_coperto" and r["errore"] == "dichiarato_non_su_emarket"
    assert "Borsa Italiana" in r["motivo"] and ambiente["chieste"] == []


def test_non_coperto_misurato_sul_menu(ambiente):
    ambiente["risposte"][em.URL_LISTA.format(id=777777)] = (200, _fixture("em_lista_vuota.html"))
    r = em.get_internal_dealing("QQSYN.MI")
    assert r["stato"] == "non_coperto" and r["errore"] == "id_non_nel_menu"


def test_vuoto_misurato(ambiente):
    ambiente["risposte"][LISTA] = (200, _fixture("em_lista_vuota.html"))
    r = em.get_internal_dealing("ACME.MI")
    assert r["stato"] == "vuoto_misurato" and r["comunicazioni"] == [] and r["letto_il"]


def test_ticker_non_mappato_e_negozio_assente(ambiente, monkeypatch):
    assert em.get_internal_dealing("NONCE.MI")["errore"] == "ticker_non_mappato"
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(ambiente["tmp"] / "manca.json"))
    r = em.get_internal_dealing("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "negozio_assente" and ambiente["chieste"] == []


@pytest.mark.parametrize("giorni", [0, -1, 5000, "180", 1.5, True])
def test_giorni_fuori_dominio_KO(ambiente, giorni):
    r = em.get_internal_dealing("ACME.MI", giorni=giorni)
    assert r["stato"] == "KO" and r["errore"] == "parametro" and ambiente["chieste"] == []


def test_stale_quando_la_lista_cade(ambiente):
    _lista_ok(ambiente)
    buono = em.get_internal_dealing("ACME.MI")
    cartella = ambiente["tmp"] / "cache"
    for n in os.listdir(cartella):
        p = cartella / n
        c = json.loads(p.read_text(encoding="utf-8"))
        c["salvato_ts"] -= em.TTL_LISTA_S + 60
        p.write_text(json.dumps(c), encoding="utf-8")
    ambiente["risposte"][LISTA] = TimeoutError("x")
    r = em.get_internal_dealing("ACME.MI")
    assert r["stato"] == "STALE" and r["errore"] == "rete" and r["letto_il"] == buono["letto_il"]
    assert len(r["comunicazioni"]) == 2 and r["cache"]["stato"] == "scaduta_servita"


@pytest.mark.parametrize("s", ["1.234", "12,500", "0.125"])
def test_prezzo_con_tre_cifre_dopo_un_separatore_e_ambiguo(s):
    assert em._num_prezzo(s) is None


@pytest.mark.parametrize("s,atteso", [("8.9957", 8.9957), ("12.34", 12.34), ("0", 0.0), ("8,9957", 8.9957)])
def test_prezzo_non_ambiguo_letto(s, atteso):
    assert em._num_prezzo(s) == atteso


def test_operazione_su_altro_isin_non_firmata_col_ticker():
    """Review RV-C: bond/derivato dello stesso emittente nel PDF -> l'operazione resta col link ma
    senza numeri, e la comunicazione non e' parse_ok."""
    testo = _testo_pdf("id_multipla.pdf")
    i = testo.index("Operazione - 2")
    altro = "ITZZQQSYN005"
    com = em.parse_testo_internal_dealing(testo[:i] + testo[i:].replace("ISIN: " + ISIN_ACME, "ISIN: " + altro))
    assert com["parse_ok"] is True               # il parser da solo non lo vede
    com = em.incrocia_isin(com, ISIN_ACME)
    a, b = com["operazioni"]
    assert a["isin_coerente"] is True and a["parse_ok"] and a["quantita"] == 1500
    assert b["isin_coerente"] is False and b["parse_ok"] is False
    assert b["prezzo"] is None and b["quantita"] is None and altro in b["motivo"]
    assert com["parse_ok"] is False and "diverso dal titolo del book" in com["motivo_parse"]
    assert com["errore"] == "isin_incoerente"


def test_isin_assente_nel_pdf_non_verificabile():
    com = em.parse_testo_internal_dealing(_testo_pdf("id_singola.pdf").replace("ISIN: " + ISIN_ACME, ""))
    (op,) = em.incrocia_isin(com, ISIN_ACME)["operazioni"]
    assert op["parse_ok"] is False and op["quantita"] is None and "non verificabile" in op["motivo"]


def test_id_emarket_di_un_altro_emittente_KO(ambiente, monkeypatch):
    """Il negozio dice ACME.MI -> un altro ISIN, ma l'id eMarket porta a PDF tutti su ITZZACME0007."""
    neg = ambiente["tmp"] / "isin_it.json"
    neg.write_text(json.dumps({"ACME.MI": {"isin": "ITZZQQSYN005", "emarket": 4242}}), encoding="utf-8")
    _lista_ok(ambiente)
    r = em.get_internal_dealing("ACME.MI")
    assert r["stato"] == "KO" and r["errore"] == "id_emarket_incoerente" and r["comunicazioni"] == []
    assert ISIN_ACME in r["motivo"] and "ITZZQQSYN005" in r["motivo"]


def test_pdf_caduto_per_rete_si_ritenta(ambiente):
    """Review RV-C: la lista in cache congelava per 6 h anche i PDF non scaricati."""
    _lista_ok(ambiente)
    ambiente["risposte"][_pdf_url("900001")] = TimeoutError("x")
    r1 = em.get_internal_dealing("ACME.MI")
    assert r1["pdf_falliti"] == 1
    ambiente["risposte"][_pdf_url("900001")] = (200, _fixture("id_singola.pdf"))
    r2 = em.get_internal_dealing("ACME.MI")
    assert r2["cache"]["stato"] == "nessuna" and r2["pdf_falliti"] == 0 and r2["parse_falliti"] == 0
    assert ambiente["chieste"].count(_pdf_url("900001")) == 2
    r3 = em.get_internal_dealing("ACME.MI")
    assert r3["cache"]["stato"] == "fresca"


def test_pdf_letto_una_volta_sola(ambiente, monkeypatch):
    """Il parse riuscito di un protocollo resta in cache: alla rilettura della lista (scaduta)
    il PDF non si riscarica."""
    _lista_ok(ambiente)
    em.get_internal_dealing("ACME.MI", giorni=180)
    em.get_internal_dealing("ACME.MI", giorni=200)   # altra chiave di lista, stessi PDF
    assert ambiente["chieste"].count(_pdf_url("900002")) == 1


# ------------------------------------------------------------ D. data di deposito (PM 04/10)
def _cat(c, pagina=0):
    return em.URL_CATEGORIA.format(cat=c, id=4242) + ("&page=%d" % pagina if pagina else "")


def _depositi(amb, **sovrascrivi):
    vuota = (200, _fixture("em_lista_vuota.html"))
    base = {_cat(101): (200, _fixture("dep_cat101.html")), _cat(150): (200, _fixture("dep_cat150.html")),
            _cat(150, 1): (200, _fixture("dep_cat150_p2.html")), _cat(100): vuota, _cat(109): vuota}
    base.update(sovrascrivi)
    amb["risposte"].update(base)


def test_deposito_semestrale_ok_coppia_it_en_non_ambigua(ambiente):
    _depositi(ambiente)
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok", r["motivo"]
    assert (r["data_deposito"], r["ora_deposito"], r["protocollo"], r["categoria"], r["lingua"]) == \
        ("2026-08-12", "11:02", "700104", 101, "it")
    assert "Relazione finanziaria semestrale al 30 giugno 2026" in r["titolo"]
    assert [c["protocollo"] for c in r["conferme"]] == ["700105"]
    # il comunicato dei RISULTATI del semestre (29/07) non e' il deposito
    assert all(c["protocollo"] != "700103" for c in r["candidati"] + r["conferme"])
    # IT2b 05/10: la semestrale cerca anche 109 e 100 (misura M1 su 59 emittenti)
    assert r["categorie_cercate"] == [101, 150, 109, 100]
    assert r["url_liste"] == [_cat(101), _cat(150), _cat(109), _cat(100)]
    assert set(r["sha256_liste"]) == set(r["url_liste"]) and all(len(h) == 64 for h in r["sha256_liste"].values())
    assert r["letto_il"] and r["isin"] == ISIN_ACME and r["emarket_id"] == 4242


def test_deposito_annuale_con_paginazione(ambiente):
    _depositi(ambiente)
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and (r["data_deposito"], r["protocollo"], r["categoria"]) == ("2026-04-15", "700203", 150)
    assert _cat(150, 1) in ambiente["chieste"]          # la prima pagina non arrivava al periodo
    assert r["pagine_lette"] == 4      # 150 (2 pagine) + 100 + 109 (categorie dell'annuale)


@pytest.mark.parametrize("fine,data,prot", [("2026-03-31", "2026-05-14", "700205"),
                                            ("2025-09-30", "2025-11-13", "700200")])
def test_deposito_trimestrale(ambiente, fine, data, prot):
    _depositi(ambiente)
    r = em.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine=fine)
    assert r["stato"] == "ok" and (r["data_deposito"], r["protocollo"]) == (data, prot)


def test_deposito_ambiguo_mai_il_primo_a_caso(ambiente):
    _depositi(ambiente, **{_cat(101): (200, _fixture("dep_ambiguo.html"))})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and r["titolo"] is None
    assert sorted(c["protocollo"] for c in r["candidati"]) == ["700301", "700302"]


def test_deposito_non_trovato_dichiarato(ambiente):
    _depositi(ambiente)
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-12-31")
    assert r["stato"] == "non_trovato" and r["data_deposito"] is None
    assert any("non_trovato" in l for l in r["limiti"])


def test_deposito_non_coperto(ambiente):
    r = em.get_data_deposito("ZZTEST.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "non_coperto" and r["errore"] == "dichiarato_non_su_emarket" and ambiente["chieste"] == []
    ambiente["risposte"][em.URL_CATEGORIA.format(cat=101, id=777777)] = (200, _fixture("em_lista_vuota.html"))
    r = em.get_data_deposito("QQSYN.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_coperto" and r["errore"] == "id_non_nel_menu"


@pytest.mark.parametrize("guasto,errore", [((500, b""), "http"), (TimeoutError("x"), "rete"),
                                           ((200, b"<html>manutenzione</html>"), "pagina_diversa")])
def test_deposito_KO_su_una_categoria_niente_verdetto_parziale(ambiente, guasto, errore):
    """La seconda categoria cade: non si decide su meta' delle liste."""
    _depositi(ambiente, **{_cat(150): guasto})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == errore and r["data_deposito"] is None


@pytest.mark.parametrize("tipo,fine", [("mensile", "2026-06-30"), ("semestrale", "30/06/2026"),
                                       ("semestrale", "2026-02-30"), ("semestrale", None)])
def test_deposito_parametri_KO(ambiente, tipo, fine):
    r = em.get_data_deposito("ACME.MI", tipo=tipo, periodo_fine=fine)
    assert r["stato"] == "KO" and r["errore"] == "parametro" and ambiente["chieste"] == []


def test_deposito_ok_in_cache_e_STALE(ambiente):
    _depositi(ambiente)
    a = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine=date(2026, 6, 30))
    n = len(ambiente["chieste"])
    b = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert b["cache"]["stato"] == "fresca" and len(ambiente["chieste"]) == n and b["data_deposito"] == a["data_deposito"]
    cartella = ambiente["tmp"] / "cache"
    for nome in os.listdir(cartella):
        p = cartella / nome
        c = json.loads(p.read_text(encoding="utf-8"))
        c["salvato_ts"] -= em.TTL_DEPOSITO_S + 60
        p.write_text(json.dumps(c), encoding="utf-8")
    ambiente["risposte"][_cat(101)] = TimeoutError("x")
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "STALE" and r["stato_originale"] == "ok" and r["data_deposito"] == "2026-08-12"


def test_deposito_non_trovato_non_va_in_cache(ambiente):
    _depositi(ambiente)
    em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-12-31")
    n = len(ambiente["chieste"])
    em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-12-31")
    assert len(ambiente["chieste"]) == 2 * n


def _r(prot, data, titolo, cat=101):
    return {"data": data, "ora": "10:00", "protocollo": prot, "titolo": titolo,
            "url_pdf": "https://x/%s.pdf" % prot, "categoria": cat}


def test_candidati_regole_pure():
    fine = date(2026, 6, 30)
    # solo inglese -> ok in inglese; due inglesi -> ambiguo
    v = em.candidati_deposito([_r("1", "2026-08-01", "Half-year financial report at 30 June 2026 published")],
                              "semestrale", fine)
    assert v["stato"] == "ok" and v["scelto"]["lingua"] == "en"
    v = em.candidati_deposito([_r("1", "2026-08-01", "Half-year financial report at June 30, 2026 published"),
                               _r("2", "2026-08-03", "Half-yearly financial report at June 30th 2026 filed")],
                              "semestrale", fine)
    assert v["stato"] == "ambiguo"
    # approvazione + deposito -> vince il verbo del deposito
    v = em.candidati_deposito([_r("1", "2026-07-29", "Il CdA approva la Relazione finanziaria semestrale al 30 giugno 2026"),
                               _r("2", "2026-08-05", "Messa a disposizione della Relazione finanziaria semestrale al 30/06/2026")],
                              "semestrale", fine)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2"
    # un titolo col PERIODO ma senza il NOME del documento (i risultati) non entra da nessuna
    # parte: ne' fra i candidati ne' fra le conferme (banco D6)
    v = em.candidati_deposito([_r("1", "2026-07-29", "ACME: risultati consolidati al 30 giugno 2026"),
                               _r("2", "2026-08-05", "Pubblicata la Relazione finanziaria semestrale al 30 giugno 2026")],
                              "semestrale", fine)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2" and v["conferme"] == []
    # pubblicato nel giorno di fine periodo o prima: non e' un deposito di quel periodo
    v = em.candidati_deposito([_r("1", "2026-06-30", "Pubblicata la Relazione finanziaria semestrale al 30 giugno 2026")],
                              "semestrale", fine)
    assert v["stato"] == "non_trovato"
    # periodo diverso nel titolo
    v = em.candidati_deposito([_r("1", "2026-08-01", "Pubblicata la Relazione finanziaria semestrale al 30 giugno 2025")],
                              "semestrale", fine)
    assert v["stato"] == "non_trovato"
    # la stessa riga vista in due categorie conta una volta
    riga = _r("9", "2026-08-01", "Pubblicata la Relazione finanziaria semestrale al 30 giugno 2026")
    v = em.candidati_deposito([riga, dict(riga, categoria=150)], "semestrale", fine)
    assert v["stato"] == "ok"


# ------------------------------------------------------------ E. deposito dal titolo GENERICO (misura 04/10 sera)
def _pdf_dep(prot, mese="2026-08"):
    return em.BASE + "/sites/default/files/comunicati/%s/sint_%s.pdf" % (mese, prot)


def _generici(amb, it="dep_generico_it.pdf", en="dep_generico_en.pdf", **altro):
    _depositi(amb, **{_cat(101): (200, _fixture("dep_generico_cat101.html")),
                      _cat(150): (200, _fixture("em_lista_vuota.html"))})
    amb["risposte"][_pdf_dep("710203")] = (200, _fixture(it))
    amb["risposte"][_pdf_dep("710204")] = (200, _fixture(en))
    amb["risposte"][_pdf_dep("710201", "2025-08")] = (200, _fixture("dep_generico_2025.pdf"))
    amb["risposte"].update(altro)


def test_deposito_titolo_generico_confermato_dal_testo_del_pdf(ambiente):
    """Il caso misurato: «deposito documenti» / «documents filing», nome e periodo SOLO nel PDF."""
    _generici(ambiente)
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok", r["motivo"]
    assert (r["data_deposito"], r["ora_deposito"], r["protocollo"], r["lingua"], r["prova"]) == \
        ("2026-08-13", "12:16", "710203", "it", "testo_pdf")
    assert [c["protocollo"] for c in r["conferme"]] == ["710204"]
    assert set(r["sha256_pdf"]) == {_pdf_dep("710203"), _pdf_dep("710204")}
    # il comunicato dei RISULTATI (29/07) non e' generico e non e' il deposito; il PDF del 2025 non si legge
    assert _pdf_dep("710201", "2025-08") not in ambiente["chieste"]
    assert _pdf_dep("710202", "2026-07") not in ambiente["chieste"]


def test_deposito_generico_periodo_precedente(ambiente):
    _generici(ambiente)
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2025-06-30")
    assert r["stato"] == "ok" and r["protocollo"] == "710201" and r["data_deposito"] == "2025-08-14"


def test_deposito_generico_testo_senza_nome_e_periodo_vicini_non_trovato(ambiente):
    _generici(ambiente, it="dep_generico_lontano.pdf", en="dep_generico_lontano.pdf")
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_trovato" and r["data_deposito"] is None and "PDF letti: 2" in r["motivo"]


def test_deposito_generico_solo_pdf_inglese(ambiente):
    _generici(ambiente, it="dep_generico_lontano.pdf")
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["protocollo"] == "710204" and r["lingua"] == "en"


def test_deposito_generico_due_pdf_italiani_stesso_giorno(ambiente):
    """IT2b 05/10: due PDF italiani dello STESSO giorno (12:16 e 12:18) danno una data univoca:
    si tiene il primo per ora (regola fissa), l'altro va fra le conferme e il limite lo dice.
    Giorni diversi restano ambigui (test_deposito_ambiguo_mai_il_primo_a_caso)."""
    _generici(ambiente, en="dep_generico_it.pdf")
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["prova"] == "testo_pdf"
    assert (r["data_deposito"], r["ora_deposito"], r["protocollo"]) == ("2026-08-13", "12:16", "710203")
    assert [c["protocollo"] for c in r["conferme"]] == ["710204"]
    assert any("stesso giorno 2026-08-13" in l for l in r["limiti"])


def test_deposito_generico_pdf_non_letto_KO(ambiente):
    _generici(ambiente, **{_pdf_dep("710203"): TimeoutError("x")})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "rete" and "verdetto sospeso" in r["motivo"]


def test_deposito_generico_tetto_dei_pdf_dichiarato(ambiente, monkeypatch):
    monkeypatch.setattr(em, "MAX_PDF_DEPOSITO", 1)
    _generici(ambiente, it="dep_generico_lontano.pdf")
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_trovato" and _pdf_dep("710204") not in ambiente["chieste"]
    assert any("letti i PDF dei primi 1 (prima i titoli italiani" in l for l in r["limiti"])


def test_deposito_titolo_solo_inglese_con_data_americana(ambiente):
    """«Half-Year Financial Report at June 30, 2026»: titolo inglese, data mese-giorno."""
    _depositi(ambiente, **{_cat(101): (200, _fixture("dep_solo_en_cat101.html")),
                           _cat(150): (200, _fixture("em_lista_vuota.html"))})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["protocollo"] == "720102" and r["lingua"] == "en" and r["prova"] == "titolo"
    assert r["sha256_pdf"] == {}


@pytest.mark.parametrize("titolo", [
    "ACME: Half-Year Financial Report at June 30, 2026 published",
    "ACME: half-yearly report as at 30 June 2026 filed",
    "ACME: semi-annual financial report at 30/06/2026 available",
    "ACME: depositata la Relazione semestrale al 30 giugno 2026",
    "ACME: messa a disposizione del Bilancio consolidato semestrale al 30.06.2026",
])
def test_formulazioni_del_titolo_riconosciute(titolo):
    v = em.candidati_deposito([_r("1", "2026-08-01", titolo)], "semestrale", date(2026, 6, 30))
    assert v["stato"] == "ok", titolo


@pytest.mark.parametrize("titolo", ["ACME S.p.A.: deposito documenti", "ACME: documents filing",
                                    "ACME: filing of documents", "ACME: adempimenti informativi"])
def test_titoli_generici_riconosciuti_ma_non_bastano_da_soli(titolo):
    righe = [_r("1", "2026-08-01", titolo)]
    v = em.candidati_deposito(righe, "semestrale", date(2026, 6, 30))
    assert v["stato"] == "non_trovato" and [g["protocollo"] for g in v["generici"]] == ["1"]


def test_voce_automatica_senza_emarket_non_coperto_con_la_sua_frase(ambiente):
    auto = ambiente["tmp"] / "isin_it_auto.json"
    auto.write_text(json.dumps({"AUTO.MI": {"isin": ISIN_ACME, "emarket": None, "origine": bi.ORIGINE_AUTO % "x",
                                            "emarket_motivo": "id eMarket non risolto (ambiguo): due emittenti"}}),
                    encoding="utf-8")
    r = em.get_internal_dealing("AUTO.MI")
    assert r["stato"] == "non_coperto" and r["errore"] == "emarket_non_risolto" and "ambiguo" in r["motivo"]
    assert r["voce_da"] == "automatico" and ambiente["chieste"] == []
    r = em.get_data_deposito("AUTO.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_coperto" and r["errore"] == "emarket_non_risolto"
    r = em.get_internal_dealing("ZZTEST.MI")
    assert r["errore"] == "dichiarato_non_su_emarket" and r["voce_da"] == "confermato"


def test_documenti_deposito_e_lo_stesso_oggetto_di_doc():
    """Contratto con D4 (trade_idea_sources): nome pubblico, stesse chiavi, coppie (IT, EN)."""
    assert em.DOCUMENTI_DEPOSITO is em._DOC
    assert set(em.DOCUMENTI_DEPOSITO) == {"semestrale", "annuale", "trimestrale"}
    assert all(isinstance(v, tuple) and len(v) == 2 for v in em.DOCUMENTI_DEPOSITO.values())

def test_url_vietato_internal_dealing_motivo_controllato_senza_il_testo_dell_eccezione(monkeypatch):
    """VF 05/10 (regola 16): il messaggio di URLVietato puo' contenere l'URL del redirect; nel motivo va un
    testo controllato (tipo dell'eccezione + motivo fisso), lo stato resta KO/url_vietato."""
    def vietato(url):
        raise bi.URLVietato("redirect verso https://zz-host-finto.example/x?token=QQSEGRETO: host non previsto")
    monkeypatch.setattr(bi, "_scarica", vietato)
    out = em._leggi("ACME.MI", "ITZZACME0007", 4242, 30)
    assert (out["stato"], out["errore"]) == ("KO", "url_vietato")
    assert "QQSEGRETO" not in out["motivo"] and "zz-host-finto" not in out["motivo"]
    assert "URLVietato" in out["motivo"] and "pagina 1" in out["motivo"]
