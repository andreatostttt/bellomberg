# -*- coding: utf-8 -*-
"""Test di emarket_documenti (sezione Documenti di eMarket Storage, handoff-3 IT3, Opus 5.5).
Nessuna rete: `_scarica` finto che rifiuta comunque gli URL vietati. Fixture SINTETICHE
(emittente «ACME SINTETICA», id 900, protocolli inventati): solo la forma imita le pagine vere."""
import os
from datetime import date

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import emarket_documenti as ed

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "fonti_it")
B0 = ed.BASE + "/it/documenti?azienda=900&data_from=%s&data_to=%s"


class _B:
    """URL chiesto per la finestra (da, a) INCLUSIVA: il sito ha data_to esclusivo, si manda a + 1."""
    def __mod__(self, finestra):
        da, a = finestra
        return B0 % (da, (date.fromisoformat(a) + __import__("datetime").timedelta(days=1)).isoformat())


B = _B()


def _f(nome):
    with open(os.path.join(FIX, nome), "rb") as fh:
        return fh.read()


def _h(nome):
    return _f(nome).decode("utf-8")


@pytest.fixture
def amb(monkeypatch, tmp_path):
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 5))
    monkeypatch.setattr(ed, "PAUSA_S", 0)
    risposte, chieste = {}, []

    def finto(url):
        ok, motivo = bi.url_consentito(url)
        if not ok:
            raise bi.URLVietato(motivo)
        chieste.append(url)
        r = risposte.get(url)
        if r is None:
            raise ConnectionError("nessuna risposta finta")
        if isinstance(r, Exception):
            raise r
        return r[0], r[1], url
    monkeypatch.setattr(bi, "_scarica", finto)
    return {"risposte": risposte, "chieste": chieste}


def _righe(nome, **kw):
    p = ed.parse_documenti(_h(nome), 900, **kw)
    assert p["stato"] == "ok", p
    return p["righe"]


def _semestrale_righe():
    tutte = _righe("emd_semestrale.html", data_da="2026-07-01", data_a="2026-10-28")
    c101 = _righe("emd_semestrale_cat101.html", data_da="2026-07-01", data_a="2026-10-28", categoria=101)
    return ed.unisci_righe(tutte, {101: c101})


# ---------------------------------------------------------------- parse
def test_parse_riga_esef_accettata_con_bandierina():
    r = _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")
    esef = {x["protocollo"]: x for x in r if x["esef"]}
    assert set(esef) == {"900120", "900112", "900108"}
    assert esef["900108"]["url"].endswith("/sites/default/files/xbrl/2026-03/20260320_900108.zip")
    assert esef["900112"]["url"].endswith(".xbri")
    pdf = next(x for x in r if x["protocollo"] == "900107")
    assert pdf["esef"] is False and pdf["data"] == "2026-03-20" and pdf["ora"] == "18:05"


def test_parse_riga_senza_file_contata_non_inventata():
    p = ed.parse_documenti(_h("emd_annuale.html"), 900, data_da="2026-01-01", data_a="2026-05-31")
    assert p["righe_illeggibili"] == 1
    assert "900105" not in {x["protocollo"] for x in p["righe"]}
    assert "illeggibili" in p["motivo"]


def test_parse_lingua_delle_righe():
    r = {x["protocollo"]: x["lingua"] for x in _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")}
    assert r["900108"] == "it" and r["900112"] == "en" and r["900103"] == "en"
    assert ed.lingua_titolo("Annual Report 2025 ESEF Italiano") == "it"
    assert ed.lingua_titolo("ACME_Relazione Intermedio di gestione") == "it"


def test_parse_vuoto_misurato_con_eco_verificato():
    p = ed.parse_documenti(_h("emd_vuoto.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "vuoto_misurato" and p["righe"] == []


def test_parse_id_fuori_menu_non_coperto():
    p = ed.parse_documenti(_h("emd_non_coperto.html"), 999, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "non_coperto" and p["errore"] == "id_non_nel_menu"


def test_parse_pagina_dei_comunicati_e_pagina_diversa():
    p = ed.parse_documenti(_h("emd_comunicati.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "pagina_diversa"


def test_parse_layout_cambiato_KO():
    p = ed.parse_documenti(_h("emd_layout.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "layout_cambiato"


def test_trappola_data_italiana_eco_diverso_KO_filtro_ignorato():
    p = ed.parse_documenti(_h("emd_filtro_ignorato.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "filtro_ignorato" and "data_from" in p["motivo"]


def test_riga_fuori_finestra_KO_filtro_ignorato_anche_con_eco_giusto():
    p = ed.parse_documenti(_h("emd_fuori_finestra.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "filtro_ignorato" and "2026-06-20" in p["motivo"]


def test_eco_categoria_diversa_KO():
    p = ed.parse_documenti(_h("emd_semestrale.html"), 900, data_da="2026-07-01", data_a="2026-10-28", categoria=101)
    assert p["stato"] == "KO" and p["errore"] == "filtro_ignorato" and "categoria" in p["motivo"]


def test_unisci_righe_marca_la_categoria_101():
    r = {x["protocollo"]: x for x in _semestrale_righe()}
    assert r["900208"]["categoria"] == 101 and r["900208"]["categorie"] == [101]
    assert r["900206"]["categoria"] is None and r["900206"]["categorie"] == []


# ---------------------------------------------------------------- candidati_documento: annuale
def test_annuale_esef_piu_antico_comanda():
    r = _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ok" and e["prova"] == "esef"
    assert e["scelto"]["protocollo"] == "900108" and e["scelto"]["data"] == "2026-03-20"


def test_annuale_ripubblicazione_e_EN_in_conferme_con_nota():
    r = _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    conf = {c["protocollo"]: c["nota"] for c in e["conferme"]}
    assert "ripubblicazione" in conf["900120"] and "(EN)" in conf["900112"]
    assert "stesso giorno" in conf["900107"]
    assert any("PIU' ANTICA" in n for n in e["note"])


def test_annuale_documento_non_esef_precedente_non_comanda():
    r = _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    conf = {c["protocollo"]: c["nota"] for c in e["conferme"]}
    assert "NON ESEF" in conf["900103"]
    assert e["scelto"]["data"] == "2026-03-20"


def test_annuale_esclusioni_revisione_illustrativa_governance_remunerazione_presentazione():
    r = _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    mot = {s["protocollo"]: s["motivo"] for s in e["scartati"]}
    assert "revisione" in mot["900111"]
    assert "illustrativa" in mot["900106"]
    tutti = {c["protocollo"] for c in e["candidati"] + e["conferme"]}
    assert not tutti & {"900111", "900106", "900110", "900109", "900104"}


def test_annuale_senza_esef_nome_periodo_ok():
    r = [x for x in _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")
         if not x["esef"] and x["protocollo"] in ("900107",)]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ok" and e["prova"] == "nome+periodo" and e["scelto"]["protocollo"] == "900107"


def test_annuale_senza_esef_giorni_diversi_ambiguo():
    r = [x for x in _righe("emd_annuale.html", data_da="2026-01-01", data_a="2026-05-31")
         if not x["esef"] and x["protocollo"] in ("900107", "900119")]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ambiguo" and e["scelto"] is None and len(e["candidati"]) == 2


def test_annuale_refuso_bilancio_al_31_12():
    r = [{"data": "2026-04-02", "ora": "21:24", "titolo": "ACME Bilancio al 31.12.2025", "url": "u1",
          "protocollo": "1", "categoria": None, "categorie": [], "esef": False, "lingua": "it"}]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ok" and e["prova"] == "nome+periodo"


def test_annuale_bozza_esef_dichiarata_nella_nota():
    r = [{"data": "2026-04-02", "ora": "08:40", "titolo": "ACME - Assemblea 2026 - Bozza bilancio 2025", "url": "u",
          "protocollo": "1", "categoria": None, "categorie": [], "esef": True, "lingua": "it"}]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ok" and any("bozza" in n for n in e["note"])


def test_documento_prima_della_fine_del_periodo_non_conta():
    r = [{"data": "2025-12-30", "ora": "10:00", "titolo": "Relazione finanziaria annuale 2025", "url": "u",
          "protocollo": "1", "categoria": None, "categorie": [], "esef": True, "lingua": "it"}]
    assert ed.candidati_documento(r, "annuale", "2025-12-31")["stato"] == "non_trovato"


def test_documento_oltre_la_finestra_scartato_col_motivo():
    r = [{"data": "2026-09-30", "ora": "10:00", "titolo": "Relazione finanziaria annuale 2025", "url": "u",
          "protocollo": "1", "categoria": None, "categorie": [], "esef": True, "lingua": "it"}]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "non_trovato" and "oltre" in e["scartati"][0]["motivo"]


# ---------------------------------------------------------------- semestrale
def test_semestrale_categoria_101_con_nome_sbagliato_italiano_comanda():
    e = ed.candidati_documento(_semestrale_righe(), "semestrale", "2026-06-30")
    assert e["stato"] == "ok" and e["prova"] == "categoria+nome"
    assert e["scelto"]["protocollo"] == "900208"          # «Resoconto ... 30 giugno», IT, cat. 101
    conf = {c["protocollo"] for c in e["conferme"]}
    assert {"900209", "900210"} <= conf


def test_semestrale_presentazione_in_101_esclusa():
    e = ed.candidati_documento(_semestrale_righe(), "semestrale", "2026-06-30")
    assert "900207" not in {c["protocollo"] for c in e["candidati"] + e["conferme"]}


@pytest.mark.parametrize("titolo, parola", [
    ("Relazione sul governo societario e gli assetti proprietari", "governance"),
    ("Relazione sulla politica di remunerazione", "remunerazione"),
    ("Relazione illustrativa sul punto 2 all'ordine del giorno", "illustrativa"),
    ("Relazione della societa' di revisione sulla relazione semestrale", "revisione"),
    ("Avviso pagamento dividendo e relazione", "dividendo"),
    ("Relazione e presentazione risultati H1 2026", "presentazione"),
    ("Green Bond Report 2026", "green bond"),
    ("Verbale e relazione dell'assemblea", "assemblea"),
])
def test_esclusioni_in_categoria_101(titolo, parola):
    r = [{"data": "2026-08-05", "ora": "10:00", "titolo": titolo, "url": "u", "protocollo": "1",
          "categoria": 101, "categorie": [101], "esef": False, "lingua": "it"}]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "non_trovato" and parola in e["scartati"][0]["motivo"]


def test_semestrale_senza_categoria_misurata_serve_nome_e_periodo():
    tutte = _righe("emd_semestrale.html", data_da="2026-07-01", data_a="2026-10-28")
    e = ed.candidati_documento(tutte, "semestrale", "2026-06-30")
    # senza la categoria 101 il «Resoconto intermedio di gestione al 30 giugno» non e' riconosciuto per nome:
    # resta il nome EN + periodo, dichiarato come prova 'nome+periodo'
    assert e["stato"] == "ok" and e["prova"] == "nome+periodo" and e["scelto"]["protocollo"] == "900209"


def test_semestrale_refuso_30_al_giugno_e_2Q_in_101():
    base = {"ora": "16:51", "url": "u", "categoria": 101, "categorie": [101], "esef": False, "lingua": "it"}
    r = [dict(base, data="2026-08-13", titolo="Relazione finanziaria semestrale consolidata 30 al giugno 2026", protocollo="1")]
    assert ed.candidati_documento(r, "semestrale", "2026-06-30")["stato"] == "ok"
    r = [dict(base, data="2026-08-05", titolo="IP - 2Q2026 relazione", protocollo="2")]
    assert ed.candidati_documento(r, "semestrale", "2026-06-30")["stato"] == "ok"


def test_semestrale_titolo_di_trimestre_in_101_scartato():
    r = [{"data": "2026-08-15", "ora": "08:00", "titolo": "Relazione finanziaria I Trimestre 2026", "url": "u",
          "protocollo": "1", "categoria": 101, "categorie": [101], "esef": False, "lingua": "it"}]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "non_trovato" and "trimestre" in e["scartati"][0]["motivo"]


def test_versione_inglese_di_un_giorno_prima_vince_la_piu_antica():
    base = {"ora": "10:00", "url": "u", "categoria": 101, "categorie": [101], "esef": False}
    r = [dict(base, data="2026-08-06", titolo="Relazione finanziaria semestrale al 30 giugno 2026", protocollo="1", lingua="it"),
         dict(base, data="2026-08-05", titolo="Half-year financial report 2026", protocollo="2", lingua="en")]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "2" and any("PRIMA" in n for n in e["note"])


# ---------------------------------------------------------------- trimestrale
def test_trimestrale_nome_periodo_refuso_relazione_intermedio():
    r = _righe("emd_pagina2.html", data_da="2026-04-01", data_a="2026-07-29")
    e = ed.candidati_documento(r, "trimestrale", "2026-03-31")
    assert e["stato"] == "ok" and e["prova"] == "nome+periodo"
    assert e["scelto"]["protocollo"] == "900308"       # italiano, anche se l'inglese e' delle 06:50
    assert "900306" not in {c["protocollo"] for c in e["candidati"] + e["conferme"]}   # risultati: non e' il resoconto


def test_trimestrale_solo_risultati_non_trovato_con_motivo_che_rimanda_ai_comunicati():
    r = [{"data": "2026-05-15", "ora": "13:34", "titolo": "Risultati consolidati primo trimestre 2026", "url": "u",
          "protocollo": "1", "categoria": None, "categorie": [], "esef": False, "lingua": "it"},
         {"data": "2026-05-07", "ora": "16:58", "titolo": "ACME: Trimestrale al 31 marzo 2026", "url": "u2",
          "protocollo": "2", "categoria": None, "categorie": [], "esef": False, "lingua": "it"}]
    assert ed.candidati_documento(r, "trimestrale", "2026-03-31")["scelto"]["protocollo"] == "2"
    e = ed.candidati_documento(r[:1], "trimestrale", "2026-03-31")
    assert e["stato"] == "non_trovato" and "comunicati" in e["motivo"] and "non depositato" in e["motivo"]


def test_candidati_parametri_sbagliati_errore_esplicito():
    with pytest.raises(ed.ParametroDocumenti):
        ed.candidati_documento([], "mensile", "2026-03-31")
    with pytest.raises(ed.ParametroDocumenti):
        ed.candidati_documento([], "annuale", "31/12/2025")


def test_finestra_documenti_tagliata_a_oggi():
    assert ed.finestra_documenti("annuale", "2025-12-31", oggi=date(2026, 10, 5)) == ("2026-01-01", "2026-05-30")
    assert ed.finestra_documenti("semestrale", "2026-06-30", oggi=date(2026, 8, 1)) == ("2026-07-01", "2026-08-01")
    with pytest.raises(ed.ParametroDocumenti):
        ed.finestra_documenti("semestrale", "2026-12-31", oggi=date(2026, 10, 5))


# ---------------------------------------------------------------- leggi_documenti (rete finta)
def test_leggi_documenti_lista_completa_piu_categoria_101(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_semestrale.html"))
    amb["risposte"][u + "&categoria=101"] = (200, _f("emd_semestrale_cat101.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "ok" and out["categorie_lette"] == [101]
    assert amb["chieste"] == [u, u + "&categoria=101"]
    assert set(out["sha256_liste"]) == {u, u + "&categoria=101"} and out["url_liste"] == amb["chieste"]
    assert "data_from=2026-07-01" in u                       # date SOLO ISO nella richiesta
    e = ed.candidati_documento(out["righe"], "semestrale", "2026-06-30")
    assert e["prova"] == "categoria+nome" and e["scelto"]["protocollo"] == "900208"
    assert any("STOCCAGGIO" in x for x in out["limiti"]) and any("uso personale" in x for x in out["limiti"])


def test_leggi_documenti_paginazione_fino_a_successiva_assente(amb):
    u = B % ("2026-04-01", "2026-07-29")
    amb["risposte"][u] = (200, _f("emd_pagina1.html"))
    amb["risposte"][u + "&page=1"] = (200, _f("emd_pagina2.html"))
    out = ed.leggi_documenti(900, data_da="2026-04-01", data_a="2026-07-29", categorie=())
    assert out["stato"] == "ok" and out["pagine_lette"] == 2 and len(out["righe"]) == 5
    assert out["categorie_lette"] == [] and all(r["categoria"] is None for r in out["righe"])


def test_leggi_documenti_troppe_pagine_KO_troncato(amb, monkeypatch):
    monkeypatch.setattr(ed, "MAX_PAGINE", 1)
    u = B % ("2026-04-01", "2026-07-29")
    amb["risposte"][u] = (200, _f("emd_pagina1.html"))
    out = ed.leggi_documenti(900, data_da="2026-04-01", data_a="2026-07-29", categorie=())
    assert out["stato"] == "KO" and out["errore"] == "troncato" and out["righe"] == []


def test_leggi_documenti_filtro_ignorato_KO(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_fuori_finestra.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "KO" and out["errore"] == "filtro_ignorato" and out["righe"] == []


def test_leggi_documenti_KO_della_lista_di_categoria_e_KO(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_semestrale.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "KO" and out["errore"] == "rete" and "categoria 101" in out["motivo"]
    assert "ConnectionError" in out["motivo"] and "nessuna risposta finta" not in out["motivo"]


def test_leggi_documenti_vuoto_e_non_coperto(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_vuoto.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "vuoto_misurato" and amb["chieste"] == [u]
    u2 = (B % ("2026-07-01", "2026-10-28")).replace("azienda=900", "azienda=999")
    amb["risposte"][u2] = (200, _f("emd_non_coperto.html"))
    out = ed.leggi_documenti(999, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "non_coperto" and out["errore"] == "id_non_nel_menu"


def test_leggi_documenti_http_e_parametri(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (503, b"")
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "KO" and out["errore"] == "http"
    for kw in ({"data_da": "01/07/2026"}, {"data_da": "2026-07-01", "data_a": "2026-06-01"}):
        assert ed.leggi_documenti(900, **kw)["errore"] == "parametro"
    assert ed.leggi_documenti(True, data_da="2026-07-01")["errore"] == "parametro"


def test_cache_fresca_poi_STALE_dichiarato(amb, monkeypatch):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_vuoto.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["cache"]["stato"] == "nessuna"
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["cache"]["stato"] == "fresca" and len(amb["chieste"]) == 1
    monkeypatch.setattr(ed, "TTL_DOCUMENTI_S", -1)
    amb["risposte"][u] = ConnectionError("giu'")
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "STALE" and out["stato_originale"] == "vuoto_misurato" and out["errore"] == "rete"


def test_KO_non_entra_in_cache(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_fuori_finestra.html"))
    ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert len(amb["chieste"]) == 2


def test_cablaggio_scarica_vero_rifiuta_url_vietato_prima_della_rete(monkeypatch, tmp_path):
    """Senza finto: il modulo passa davvero da borsa_italiana._scarica (robots prima della rete)."""
    import requests
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rete chiamata")))
    monkeypatch.setattr(ed, "URL_DOCUMENTI", "https://www.emarketstorage.it/search/?azienda={id}&data_from={da}&data_to={a}")
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["stato"] == "KO" and out["errore"] == "url_vietato"


# ---------------------------------------------------------------- rilievi RV-IT3 (05/10)
def test_data_to_esclusivo_si_manda_data_a_piu_uno(amb):
    """P1: il sito esclude il giorno data_to (misura RV-IT3): leggere fino al 28/10 INCLUSO = data_to 29/10."""
    u = B0 % ("2026-07-01", "2026-10-29")
    amb["risposte"][u] = (200, _f("emd_vuoto.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28", categorie=())
    assert amb["chieste"] == [u] and out["stato"] == "vuoto_misurato"
    assert ed.data_to_sito("2026-10-28") == "2026-10-29"
    assert any("ESCLUSIVO" in x for x in out["limiti"])


def test_eco_data_to_uguale_a_data_a_e_filtro_diverso_KO():
    p = ed.parse_documenti(_h("emd_eco_data_to_inclusivo.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "filtro_ignorato" and "data_to" in p["motivo"]


def test_riga_del_giorno_data_a_e_dentro_la_finestra():
    p = ed.parse_documenti(_h("emd_semestrale.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert "2026-09-09" in {x["data"] for x in p["righe"]}


def test_lista_con_classe_in_piu_riconosciuta():
    p = ed.parse_documenti(_h("emd_classe_extra.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "ok" and len(p["righe"]) == 2


def test_righe_senza_lista_riconosciuta_KO_non_vuoto():
    p = ed.parse_documenti(_h("emd_righe_senza_lista.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "layout_cambiato"


def test_pager_in_altra_forma_successiva_riconosciuta():
    p = ed.parse_documenti(_h("emd_piena_pager_js.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "ok" and p["pagina_successiva"] is True


def test_pagina_piena_senza_pager_da_verificare():
    p = ed.parse_documenti(_h("emd_piena_senza_pager.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "ok" and p["pagina_successiva"] is None and len(p["righe"]) == 24


def _piena(amb, pagina2):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_piena_senza_pager.html"))
    if pagina2 is not None:
        amb["risposte"][u + "&page=1"] = (200, pagina2)
    return u, ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28", categorie=())


def test_pagina_piena_e_pagina_dopo_vuota_lista_completa(amb):
    u, out = _piena(amb, _f("emd_piena_p2_vuota.html"))
    assert out["stato"] == "ok" and len(out["righe"]) == 24 and amb["chieste"] == [u, u + "&page=1"]
    assert any("piena senza pager" in x and "lista completa" in x for x in out["limiti"])


def test_pagina_piena_e_pagina_dopo_con_righe_nuove_si_prosegue(amb):
    u, out = _piena(amb, _f("emd_piena_p2_nuove.html"))
    assert out["stato"] == "ok" and len(out["righe"]) == 25 and "900801" in {r["protocollo"] for r in out["righe"]}
    assert any("pager non riconosciuto" in x for x in out["limiti"])


def test_pagina_piena_e_pagina_dopo_ripetuta_KO(amb):
    u, out = _piena(amb, _f("emd_piena_senza_pager.html"))
    assert out["stato"] == "KO" and out["errore"] == "layout_cambiato" and "ripete" in out["motivo"]
    assert out["righe"] == []


def test_pagina_piena_e_pagina_dopo_in_guasto_KO(amb):
    u, out = _piena(amb, None)
    assert out["stato"] == "KO" and out["errore"] == "rete"


def _r(titolo, data="2026-03-20", esef=False, cat=None, lingua="it", prot="1", ora="10:00"):
    return {"data": data, "ora": ora, "fuso": "Europe/Rome", "titolo": titolo, "url": "u" + prot, "protocollo": prot,
            "categoria": cat, "categorie": [cat] if cat else [], "esef": esef, "lingua": lingua}


@pytest.mark.parametrize("titolo", [
    "Relazione finanziaria annuale 2025 comprensiva della Rendicontazione consolidata di sostenibilita' (ESEF)",
    "Relazione finanziaria annuale 2025 corredata dalle relazioni del Collegio Sindacale e della Societa' di Revisione",
])
def test_esef_italiano_con_allegati_nel_titolo_non_escluso(titolo):
    r = [_r(titolo, esef=True, prot="1"), _r("Annual Financial Report 2025 (ESEF)", data="2026-04-10", esef=True,
                                              lingua="en", prot="2")]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1" and e["scelto"]["data"] == "2026-03-20"


def test_esef_inglese_col_revisore_nel_titolo_non_escluso():
    r = [_r("Annual Financial Report 2025 including the Independent Auditor's Report (ESEF)", esef=True, lingua="en")]
    assert ed.candidati_documento(r, "annuale", "2025-12-31")["stato"] == "ok"


@pytest.mark.parametrize("titolo", ["Relazione finanziaria semestrale al 30 giugno 2026 e risultati consolidati",
                                    "Relazione finanziaria semestrale al 30 giugno 2026 - prospetti contabili"])
def test_semestrale_col_nome_forte_prima_non_esclusa(titolo):
    e = ed.candidati_documento([_r(titolo, data="2026-08-05")], "semestrale", "2026-06-30")
    assert e["stato"] == "ok"


def test_nome_forte_dopo_la_parola_esclusa_resta_escluso():
    r = [_r("Relazione della societa' di revisione sulla relazione finanziaria semestrale al 30 giugno 2026",
            data="2026-08-05", cat=101)]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "non_trovato" and "revisione" in e["scartati"][0]["motivo"]


def test_esef_di_un_altro_esercizio_scartato_niente_look_ahead():
    r = [_r("Relazione finanziaria annuale 2024 (ESEF) - ripubblicazione", data="2026-02-10", esef=True, prot="1"),
         _r("Relazione finanziaria annuale 2025 (ESEF)", data="2026-03-25", esef=True, prot="2")]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["scelto"]["protocollo"] == "2"
    assert any("2024" in s["motivo"] for s in e["scartati"])


def test_semestrale_di_un_altro_anno_in_101_scartata():
    r = [_r("Relazione finanziaria semestrale al 30 giugno 2025 (nuova versione)", data="2026-07-10", cat=101, prot="1"),
         _r("Half-year financial report as at 30 June 2026", data="2026-08-05", cat=101, lingua="en", prot="2")]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["scelto"]["protocollo"] == "2"


@pytest.mark.parametrize("titolo", ["Relazione trimestrale consolidata al 30.09.2026", "Relazione finanziaria al 30/09/2026"])
def test_trimestre_in_101_entro_la_finestra_non_e_la_semestrale(titolo):
    e = ed.candidati_documento([_r(titolo, data="2026-10-20", cat=101)], "semestrale", "2026-06-30")
    assert e["stato"] == "non_trovato"


def test_trimestrale_al_30_giugno_parametro():
    with pytest.raises(ed.ParametroDocumenti):
        ed.candidati_documento([], "trimestrale", "2026-06-30")
    with pytest.raises(ed.ParametroDocumenti):
        ed.finestra_documenti("trimestrale", "2026-06-30", oggi=date(2026, 10, 5))


def test_fuso_nel_ritorno_e_nelle_righe(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_semestrale.html"))
    amb["risposte"][u + "&categoria=101"] = (200, _f("emd_semestrale_cat101.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["fuso"] == "Europe/Rome" and all(r["fuso"] == "Europe/Rome" for r in out["righe"])


def test_esef_senza_nome_forte_con_parola_esclusa_resta_candidato():
    """Una riga ESEF e' esente dalle esclusioni anche senza nome forte prima della parola."""
    r = [_r("Bilancio consolidato 2025 e relazione della societa' di revisione (ESEF)", esef=True)]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ok" and e["prova"] == "esef"


@pytest.mark.parametrize("titolo", ["Relazione trimestrale consolidata 2026", "Relazione consolidata al 30.09"])
def test_trimestre_senza_data_completa_in_101_non_e_la_semestrale(titolo):
    e = ed.candidati_documento([_r(titolo, data="2026-10-20", cat=101)], "semestrale", "2026-06-30")
    assert e["stato"] == "non_trovato" and "trimestre" in e["scartati"][0]["motivo"]


# ---------------------------------------------------------------- secondo giro RV-IT3
@pytest.mark.parametrize("titolo, tipo, fine, esef, cat", [
    ("Relazione finanziaria annuale 2025 - Assemblea del 29.04.2026", "annuale", "2025-12-31", False, None),
    ("Relazione finanziaria annuale 2025 (ESEF) - Assemblea del 29 aprile 2026", "annuale", "2025-12-31", True, None),
    ("Relazione finanziaria semestrale 2026 approvata dal CdA del 04.08.2026", "semestrale", "2026-06-30", False, 101),
    ("Relazione finanziaria annuale 2025 (dati comparativi al 31.12.2024)", "annuale", "2025-12-31", False, None),
])
def test_date_non_di_periodo_nel_titolo_non_scartano(titolo, tipo, fine, esef, cat):
    e = ed.candidati_documento([_r(titolo, data="2026-04-01" if tipo == "annuale" else "2026-08-05", esef=esef, cat=cat)],
                               tipo, fine)
    assert e["stato"] == "ok", e["scartati"]


def test_annual_report_on_governance_resta_escluso():
    r = [_r("Annual Report on Corporate Governance and Ownership Structures 2025", data="2026-03-10", lingua="en", prot="1"),
         _r("Relazione finanziaria annuale 2025", data="2026-03-20", prot="2")]
    e = ed.candidati_documento(r, "annuale", "2025-12-31")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "2"
    assert any(s["protocollo"] == "1" and "governance" in s["motivo"] for s in e["scartati"])


def test_relazione_di_revisione_dopo_trattino_resta_esclusa():
    r = [_r("Relazione finanziaria semestrale al 30 giugno 2026", data="2026-08-05", cat=101, prot="1"),
         _r("Relazione finanziaria semestrale al 30 giugno 2026 - Relazione della societa' di revisione",
            data="2026-08-06", cat=101, prot="2")]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1"


def test_pagina_successiva_vuota_KO_non_fine_lista(amb):
    u = B % ("2026-04-01", "2026-07-29")
    amb["risposte"][u] = (200, _f("emd_pagina1.html"))
    amb["risposte"][u + "&page=1"] = (200, _f("emd_pagina2_vuota.html"))
    out = ed.leggi_documenti(900, data_da="2026-04-01", data_a="2026-07-29", categorie=())
    assert out["stato"] == "KO" and out["errore"] == "layout_cambiato" and out["righe"] == []


def test_eco_azienda_diversa_KO():
    p = ed.parse_documenti(_h("emd_azienda_diversa.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "filtro_ignorato" and "azienda" in p["motivo"]


# ---------------------------------------------------------------- terzo giro RV-IT3: ricevute e dichiarazioni
def test_ricevuta_sha256_dei_byte_e_letto_il(amb):
    import hashlib
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_semestrale.html"))
    amb["risposte"][u + "&categoria=101"] = (200, _f("emd_semestrale_cat101.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    assert out["sha256_liste"][u] == hashlib.sha256(_f("emd_semestrale.html")).hexdigest()
    assert out["sha256_liste"][u + "&categoria=101"] == hashlib.sha256(_f("emd_semestrale_cat101.html")).hexdigest()
    assert out["letto_il"] is not None


def test_righe_illeggibili_dichiarate_nei_limiti(amb):
    u = B % ("2026-01-01", "2026-05-31")
    amb["risposte"][u] = (200, _f("emd_annuale.html"))
    out = ed.leggi_documenti(900, data_da="2026-01-01", data_a="2026-05-31", categorie=())
    assert out["stato"] == "ok" and out["righe_illeggibili"] == 1 and out["letto_il"] is not None
    assert any("senza link al file" in x for x in out["limiti"])


def test_riga_solo_nella_lista_di_categoria_aggiunta_e_dichiarata(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_semestrale.html"))
    amb["risposte"][u + "&categoria=101"] = (200, _f("emd_semestrale_cat101.html").replace(b"900207", b"900299"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    r = {x["protocollo"]: x for x in out["righe"]}
    assert r["900299"]["solo_in_categoria"] is True and r["900299"]["categoria"] == 101
    assert any("solo nella lista di categoria" in x for x in out["limiti"])


def test_esef_riconosciuto_dal_percorso_xbrl_anche_senza_icona():
    html = _h("emd_annuale.html").replace(
        '<span class="icon-esef" title="Documento ESEF in formato XBRL (zip)"></span>', "")
    assert 'icon-esef' not in html
    p = ed.parse_documenti(html, 900, data_da="2026-01-01", data_a="2026-05-31")
    esef = {x["protocollo"] for x in p["righe"] if x["esef"]}
    assert esef == {"900120", "900112", "900108"}


def test_righe_duplicate_contate_una_volta():
    r = _r("Relazione finanziaria annuale 2025 (ESEF)", esef=True, prot="7")
    e = ed.candidati_documento([r, dict(r), dict(r)], "annuale", "2025-12-31")
    assert e["stato"] == "ok" and len(e["candidati"]) == 1 and e["conferme"] == []


# ---------------------------------------------------------------- esercizi non solari (decisione main 05/10)
def test_non_solare_trimestrale_31_07_ok():
    r = [_r("ACME SINTETICA - Resoconto intermedio di gestione al 31 luglio 2026", data="2026-09-10", prot="1"),
         _r("ACME SINTETICA - Interim report as at 31 July 2026", data="2026-09-10", lingua="en", prot="2")]
    with pytest.raises(ed.ParametroDocumenti):
        ed.candidati_documento(r, "trimestrale", "2026-07-31")            # default solare: 31/07 incoerente
    e = ed.candidati_documento(r, "trimestrale", "2026-07-31", fine_esercizio="04-30")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1" and e["prova"] == "nome+periodo"


def test_non_solare_semestrale_31_10_ok_e_finestra():
    r = [_r("Relazione finanziaria semestrale al 31 ottobre 2026", data="2026-12-15", cat=101, prot="1"),
         _r("Relazione finanziaria semestrale al 30 giugno 2026", data="2026-11-20", cat=101, prot="2")]
    e = ed.candidati_documento(r, "semestrale", "2026-10-31", fine_esercizio="04-30")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1"
    assert any(s["protocollo"] == "2" and "altro periodo" in s["motivo"] for s in e["scartati"])
    assert ed.finestra_documenti("semestrale", "2026-10-31", oggi=date(2027, 3, 1), fine_esercizio="04-30") == \
        ("2026-11-01", "2027-02-28")
    with pytest.raises(ed.ParametroDocumenti):
        ed.finestra_documenti("semestrale", "2026-10-31", oggi=date(2027, 3, 1))


def test_non_solare_data_di_fine_trimestre_fiscale_di_un_altro_periodo_scartata():
    r = [_r("Resoconto intermedio di gestione al 31 gennaio 2026", data="2026-09-10", prot="1")]
    e = ed.candidati_documento(r, "trimestrale", "2026-07-31", fine_esercizio="04-30")
    assert e["stato"] == "non_trovato" and "altro periodo" in e["scartati"][0]["motivo"]


def test_non_solare_etichetta_Q1_ambigua_non_basta():
    for etichetta in ("Q1 2026", "Q3 2026"):      # Q3 = trimestre SOLARE al 30/09: per l'esercizio al 30/06 e' il primo
        r = [_r("ACME SINTETICA %s report" % etichetta, data="2026-11-10", lingua="en")]
        e = ed.candidati_documento(r, "trimestrale", "2026-09-30", fine_esercizio="06-30")
        assert e["stato"] == "non_trovato", etichetta
    assert ed.candidati_documento([_r("ACME SINTETICA Q3 2026 report", data="2026-11-10", lingua="en")],
                                  "trimestrale", "2026-09-30")["stato"] == "ok"


def test_non_solare_anno_di_chiusura_dell_esercizio_valido():
    r = [_r("Relazione finanziaria semestrale esercizio 2027", data="2026-12-15", cat=101)]
    assert ed.candidati_documento(r, "semestrale", "2026-10-31", fine_esercizio="04-30")["stato"] == "ok"


# ---------------------------------------------------------------- giorno di confine data_a + 1 (caso vero MF 05/10)
def test_riga_del_giorno_di_confine_esclusa_e_dichiarata(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_confine.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28", categorie=())
    assert out["stato"] == "ok" and "900901" not in {r["protocollo"] for r in out["righe"]}
    assert len(out["righe"]) == 2 and out["righe_confine"] == 1
    assert any("giorno di confine 2026-10-29" in x and "include data_to" in x for x in out["limiti"])


def test_solo_righe_del_giorno_di_confine_e_vuoto(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_solo_confine.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28", categorie=())
    assert out["stato"] == "vuoto_misurato" and out["righe"] == []
    assert any("giorno di confine" in x for x in out["limiti"])


def test_riga_oltre_il_confine_resta_filtro_ignorato():
    p = ed.parse_documenti(_h("emd_oltre_confine.html"), 900, data_da="2026-07-01", data_a="2026-10-28")
    assert p["stato"] == "KO" and p["errore"] == "filtro_ignorato" and "2026-10-30" in p["motivo"]


# ---------------------------------------------------------------- trimestrale: forme vere misurate da MF (05/10)
def test_resoconto_intermedio_senza_di_gestione_data_con_spazi_e_italiano_comanda():
    r = [_r("Resoconto intermedio 31 03 2026", data="2026-05-12", ora="11:04", prot="1"),
         _r("Interim statement as at 31 March 2026", data="2026-05-29", ora="14:35", lingua="en", prot="2")]
    e = ed.candidati_documento(r, "trimestrale", "2026-03-31")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1" and e["scelto"]["data"] == "2026-05-12"
    assert [c["protocollo"] for c in e["conferme"]] == ["2"]


def test_data_con_spazi_al_30_09():
    e = ed.candidati_documento([_r("Resoconto intermedio 30 09 2025", data="2025-11-11")], "trimestrale", "2025-09-30")
    assert e["stato"] == "ok"


def test_data_con_spazi_di_un_altro_periodo_scartata():
    e = ed.candidati_documento([_r("Resoconto intermedio 30 09 2025", data="2026-05-12")], "trimestrale", "2026-03-31")
    assert e["stato"] == "non_trovato" and "altro periodo" in e["scartati"][0]["motivo"]


def test_interim_statement_solo_inglese():
    e = ed.candidati_documento([_r("Interim statement as at 30 September 2025", data="2025-11-20", lingua="en")],
                               "trimestrale", "2025-09-30")
    assert e["stato"] == "ok" and e["prova"] == "nome+periodo"


@pytest.mark.parametrize("titolo, fine, data", [
    ("ACME SINTETICA S.p.A.: First Quarter 2026 report", "2026-03-31", "2026-04-30"),
    ("ACME SINTETICA S.p.A.: Third Quarter 2025 report", "2025-09-30", "2025-10-31"),
    ("ACME SINTETICA Q1 2026 report", "2026-03-31", "2026-04-30"),
])
def test_quarter_report_anno_e_periodo(titolo, fine, data):
    r = [_r(titolo, data=data, lingua="en", prot="1"),
         _r("ACME SINTETICA S.p.A.: First-quarter 2026 results (analyst presentation)", data=data, lingua="en", prot="2")]
    e = ed.candidati_documento(r, "trimestrale", fine)
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1"


def test_quarter_report_di_un_altro_anno_non_conta():
    e = ed.candidati_documento([_r("ACME SINTETICA: First Quarter 2025 report", data="2026-04-30", lingua="en")],
                               "trimestrale", "2026-03-31")
    assert e["stato"] == "non_trovato"


# ---------------------------------------------------------------- P1 MF (05/10): periodo esplicito non di fine trimestre
@pytest.mark.parametrize("titolo", ["ACME SINTETICA - Resoconto intermedio di gestione consolidato al 31 luglio 2026",
                                    "ACME SINTETICA - Interim report as at July 31, 2026"])
def test_categoria_101_nome_e_altra_data_scartato(titolo):
    e = ed.candidati_documento([_r(titolo, data="2026-09-14", cat=101)], "semestrale", "2026-06-30")
    assert e["stato"] == "non_trovato" and "2026-07-31" in e["scartati"][0]["motivo"]


def test_categoria_101_nome_senza_data_resta_candidato():
    e = ed.candidati_documento([_r("ACME SINTETICA - Relazione finanziaria semestrale", data="2026-09-14", cat=101)],
                               "semestrale", "2026-06-30")
    assert e["stato"] == "ok" and e["prova"] == "categoria+nome"


def test_data_di_assemblea_di_fine_mese_ignorata():
    e = ed.candidati_documento([_r("Relazione finanziaria semestrale al 30 giugno 2026 - Assemblea del 31 luglio 2026",
                                   data="2026-08-05", cat=101)], "semestrale", "2026-06-30")
    assert e["stato"] == "ok"


def test_esef_con_altra_data_esplicita_scartato():
    e = ed.candidati_documento([_r("Relazione finanziaria annuale al 30 aprile 2026 (ESEF)", data="2026-03-20", esef=True)],
                               "annuale", "2025-12-31")
    assert e["stato"] == "non_trovato" and "2026-04-30" in e["scartati"][0]["motivo"]


def test_data_introdotta_da_al_ma_non_di_fine_mese_ignorata():
    e = ed.candidati_documento([_r("Relazione finanziaria semestrale 2026 - messa a disposizione al 4 agosto 2026",
                                   data="2026-08-05", cat=101)], "semestrale", "2026-06-30")
    assert e["stato"] == "ok"


# ---------------------------------------------------------------- AGGIUNTA 5: risposte salvate e riverifica senza rete
def _ricevuta_semestrale(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_semestrale.html"))
    amb["risposte"][u + "&categoria=101"] = (200, _f("emd_semestrale_cat101.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    out["verdetto"] = ed.candidati_documento(out["righe"], "semestrale", "2026-06-30")
    return u, out


def test_risposte_salvate_gzip_base64_e_sha_sul_corpo_non_compresso(amb):
    import base64, gzip, hashlib
    u, out = _ricevuta_semestrale(amb)
    assert set(out["risposte_salvate"]) == set(out["url_liste"])
    rec = out["risposte_salvate"][u]
    assert rec["codifica"] == "gzip+base64"
    corpo = gzip.decompress(base64.b64decode(rec["corpo"]))
    assert corpo == _f("emd_semestrale.html")
    assert out["sha256_liste"][u] == hashlib.sha256(_f("emd_semestrale.html")).hexdigest()


def test_riverifica_ok_senza_rete(amb):
    u, out = _ricevuta_semestrale(amb)
    n = len(amb["chieste"])
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo
    assert len(amb["chieste"]) == n                      # nessuna richiesta in piu'


def test_riverifica_corpo_manomesso_False(amb):
    import base64, gzip
    u, out = _ricevuta_semestrale(amb)
    finto = _f("emd_semestrale.html").replace(b"30 giugno 2026", b"31 luglio 2026")
    out["risposte_salvate"][u]["corpo"] = base64.b64encode(gzip.compress(finto)).decode("ascii")
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "sha256 diverso" in motivo


def test_riverifica_pagina_tolta_False(amb):
    u, out = _ricevuta_semestrale(amb)
    del out["risposte_salvate"][u + "&categoria=101"]
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "assente" in motivo


def test_riverifica_url_liste_diversa_False(amb):
    u, out = _ricevuta_semestrale(amb)
    out["url_liste"] = out["url_liste"][:1]
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "url_liste" in motivo


def test_riverifica_righe_manomesse_False(amb):
    u, out = _ricevuta_semestrale(amb)
    out["righe"][0] = dict(out["righe"][0], data="2026-07-02")
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "righe" in motivo


def test_riverifica_verdetto_manomesso_False(amb):
    u, out = _ricevuta_semestrale(amb)
    out["verdetto"] = dict(out["verdetto"], scelto=dict(out["verdetto"]["scelto"], data="2026-08-01"))
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "scelta" in motivo


def test_riverifica_stato_manomesso_False(amb):
    u, out = _ricevuta_semestrale(amb)
    out["stato"] = "vuoto_misurato"
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "stato" in motivo


def test_riverifica_ricevuta_vecchia_senza_risposte_False(amb):
    u, out = _ricevuta_semestrale(amb)
    out.pop("risposte_salvate")
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "risposte_salvate" in motivo


def test_rileggi_pagina_piena_con_sonda_come_la_lettura(amb):
    u, out = _piena(amb, _f("emd_piena_p2_vuota.html"))
    r = ed.rileggi_documenti(out["risposte_salvate"], out["sha256_liste"], id_emarket=900,
                             data_da="2026-07-01", data_a="2026-10-28", categorie=())
    assert r["stato"] == "ok" and r["url_liste"] == out["url_liste"] and len(r["righe"]) == 24
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo


def test_rileggi_url_mancante_KO_ricevuta(amb):
    u, out = _ricevuta_semestrale(amb)
    r = ed.rileggi_documenti({}, out["sha256_liste"], id_emarket=900, data_da="2026-07-01", data_a="2026-10-28")
    assert r["stato"] == "KO" and r["errore"] == "ricevuta"


def test_riverifica_vuoto_misurato_ok(amb):
    u = B % ("2026-07-01", "2026-10-28")
    amb["risposte"][u] = (200, _f("emd_vuoto.html"))
    out = ed.leggi_documenti(900, data_da="2026-07-01", data_a="2026-10-28")
    ok, motivo = ed.riverifica_documenti(out, tipo="semestrale", periodo_fine="2026-06-30")
    assert out["stato"] == "vuoto_misurato" and ok, motivo


def test_risposte_salvate_oltre_la_soglia_dichiarate(amb, monkeypatch):
    monkeypatch.setattr(ed, "MAX_RISPOSTE_BYTE", 10)
    u, out = _ricevuta_semestrale(amb)
    assert any("risposte_salvate" in x for x in out["limiti"])


# ---------------------------------------------------------------- ripubblicazione con la relazione di revisione (caso vero MF)
@pytest.mark.parametrize("seconda", [
    "ACME SINTETICA - Relazione Finanziaria Semestrale al 30/06/26 con Relazione della Societa' di Revisione",
    "ACME SINTETICA - Relazione Finanziaria Semestrale al 30/06/26 corredata dalla relazione della societa' di revisione",
])
def test_ripubblicazione_con_revisione_vale_la_piu_antica(seconda):
    r = [_r("ACME SINTETICA - Relazione Finanziaria Semestrale al 30/06/26", data="2026-07-31", cat=101, prot="1"),
         _r(seconda, data="2026-08-03", cat=101, prot="2")]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1" and e["scelto"]["data"] == "2026-07-31"
    assert [c["nota"] for c in e["conferme"]] == ["ripubblicazione con la relazione di revisione, stoccata il 2026-08-03"]
    assert any("ripubblicazioni" in n for n in e["note"])


def test_ripubblicazione_inglese_including_auditor_report():
    r = [_r("ACME SINTETICA Half-year financial report as at 30 June 2026", data="2026-07-31", lingua="en", prot="1"),
         _r("ACME SINTETICA Half-year financial report as at 30 June 2026 including the independent auditor's report",
            data="2026-08-03", lingua="en", prot="2")]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "ok" and e["scelto"]["protocollo"] == "1"


def test_solo_versione_con_revisione_resta_candidata():
    r = [_r("ACME SINTETICA - Relazione Finanziaria Semestrale al 30/06/26 con Relazione della Societa' di Revisione",
            data="2026-08-03", cat=101)]
    e = ed.candidati_documento(r, "semestrale", "2026-06-30")
    assert e["stato"] == "ok" and e["scelto"]["data"] == "2026-08-03"


def test_due_versioni_senza_allegato_in_giorni_diversi_restano_ambigue():
    r = [_r("ACME SINTETICA - Relazione Finanziaria Semestrale al 30/06/26", data="2026-07-31", cat=101, prot="1"),
         _r("ACME SINTETICA - Relazione Finanziaria Semestrale al 30/06/26", data="2026-08-03", cat=101, prot="2")]
    assert ed.candidati_documento(r, "semestrale", "2026-06-30")["stato"] == "ambiguo"
