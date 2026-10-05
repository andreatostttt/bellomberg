# -*- coding: utf-8 -*-
"""Test di ue_cnmv (Spagna, registri CNMV IFA/IFI): fixture SINTETICHE, nessuna rete.
Emittente inventato «ACME SINTETICA, S.A.», ISIN sintetico ES0ZZ0000004 (cifra di controllo valida),
NIF inventati. La forma delle pagine e' quella misurata il 05/10/2026."""
import json
import time
from pathlib import Path

import pytest

from bellomberg.market_data import ue_cnmv as C

FIX = Path(__file__).parent / "fixtures" / "fonti_ue"
ISIN = "ES0ZZ0000004"
NIF = "A-12345674"
TEMPLATE = (FIX / "cnmv_det_template.html").read_text(encoding="utf-8")


def _f(nome):
    return (FIX / nome).read_bytes()


def dettaglio(fine="30/06/2026", inizio="01/01/2026", cif=NIF, pub="23/07/2026"):
    return TEMPLATE.replace("{FINE}", fine).replace("{INIZIO}", inizio).replace("{CIF}", cif) \
        .replace("{PUB}", pub).encode("utf-8")


@pytest.fixture(autouse=True)
def isolato(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "CACHE_DIR", str(tmp_path / "cache_ue"))
    monkeypatch.setattr(C, "PAUSA_S", 0)
    import requests

    def _vietato(*a, **k):
        raise AssertionError("rete vera in un test di ue_cnmv")
    monkeypatch.setattr(requests, "get", _vietato)
    monkeypatch.setattr(requests, "request", _vietato)


@pytest.fixture
def rete(monkeypatch):
    """Finta _richiesta instradata per URL; `pagine[url]` = (http, corpo) o eccezione; la POST di
    ricerca risponde con `ricerca` (302 verso la lista col NIF, o la pagina con l'elenco)."""
    s = {"chiamate": [], "ricerca": (302, b"", {"location": "/Portal/Consultas/IFI/ListaIFI.aspx?nif=" + NIF}),
         "pagine": {C.URL_ISIN.format(isin=ISIN): (200, _f("cnmv_isin_acme.html")),
                    C.URL_RICERCA: (200, _f("cnmv_ricerca_form.html")),
                    C.URL_IFA.format(nif=NIF): (200, _f("cnmv_ifa_acme.html")),
                    C.URL_IFI.format(nif=NIF): (200, _f("cnmv_ifi_acme.html")),
                    C.URL_DETTAGLIO.format(nreg="2026000101"): (200, dettaglio()),
                    C.URL_DETTAGLIO.format(nreg="2026000102"): (200, dettaglio("31/12/2025", "01/07/2025", pub="26/02/2026")),
                    C.URL_DETTAGLIO.format(nreg="2020000103"): (200, dettaglio("30/09/2020", "01/01/2020", pub="28/10/2020"))}}

    def _finta(metodo, url, dati=None, cookie=None):
        ok, motivo = C.richiesta_consentita(metodo, url)
        assert ok, motivo
        s["chiamate"].append((metodo, url))
        if metodo == "POST":
            assert dati["ctl00$ContentPrincipal$wNombreEntidad$txtDenominacion"]
            assert dati["__VIEWSTATE"] == "vsSINTETICO123"
            h, b, i = s["ricerca"]
            return h, b, dict(i, _cookie="{}")
        e = s["pagine"].get(url, (404, b""))
        if isinstance(e, Exception):
            raise e
        return e[0], e[1], {"_cookie": "{}"}
    monkeypatch.setattr(C, "_richiesta", _finta)
    return s


def _dd(tipo, fine, isin=ISIN, nome=None, ticker="ACME.MC"):
    return C.get_data_deposito(ticker, tipo=tipo, periodo_fine=fine, isin=isin, nome=nome)


# ---------------- parametri e identita' ----------------
def test_parametro_errato_ko_senza_rete(rete):
    r = _dd("trimestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "parametro" and rete["chiamate"] == []


def test_identita_mancante_senza_isin_ne_nome(rete):
    r = C.get_data_deposito("ACME.MC", tipo="annuale", periodo_fine="2025-12-31", lei="SINTETICOLEI00000000")
    assert r["errore"] == "identita_mancante" and "isin" in r["motivo"] and rete["chiamate"] == []


def test_isin_non_spagnolo_identita_mancante(rete):
    r = _dd("annuale", "2025-12-31", isin="IT0000000001")
    assert r["errore"] == "identita_mancante" and rete["chiamate"] == []


def test_nome_diverso_dalla_denominazione_ancv_ko(rete):
    r = _dd("annuale", "2025-12-31", nome="ALTRA SINTETICA, S.A.")
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente"
    assert all(m == "GET" for m, _ in rete["chiamate"])     # nessuna ricerca partita


def test_isin_sconosciuto_all_ancv_ko(rete):
    rete["pagine"][C.URL_ISIN.format(isin=ISIN)] = (200, b"<html><title>CNMV - ISIN code information - </title></html>")
    r = _dd("annuale", "2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante" and r["nif"] is None


# ---------------- verdetti ----------------
def test_semestrale_ok_periodo_dal_dettaglio_senza_ora(rete):
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-07-23" and r["prova"] == "titolo"
    assert r["ora_deposito"] is None and r["fuso"] == "Europe/Madrid" and r["natura_data"] == "diffusione"
    assert r["url"] == C.URL_DETTAGLIO.format(nreg="2026000101") and r["url_documento"].endswith("SINTSEM")
    assert r["nif"] == NIF and r["pagine_lette"] == 2 and C.LIMITE_ORA in r["limiti"]
    assert r["identita"]["denominazione"] == "ACME SINTETICA, S.A."
    assert r["fonte_modulo"] == "bellomberg.market_data.ue_cnmv" and r["paese"] == "ES"


def test_annuale_ok_natura_deposito_e_limite_sostituzione(rete):
    r = _dd("annuale", "2025-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-02-26" and r["protocollo"] == "90001"
    assert r["natura_data"] == "deposito_autorita" and C.LIMITE_IFA in r["limiti"]
    assert r["url_documento"].endswith("SINTCONS2025")


def test_annuale_sceglie_la_data_di_bilancio_esatta(rete):
    r = _dd("annuale", "2024-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2025-02-27" and r["protocollo"] == "89001"


def test_data_lista_diversa_dal_dettaglio_scartata(rete):
    rete["pagine"][C.URL_DETTAGLIO.format(nreg="2026000101")] = (200, dettaglio(pub="24/07/2026"))
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "non_trovato" and any("diversa dal dettaglio" in s["motivo"] for s in r["scartati"])


def test_annuale_due_righe_stesso_periodo_ambiguo(rete):
    rete["pagine"][C.URL_IFA.format(nif=NIF)] = (200, _f("cnmv_ifa_doppio.html"))
    r = _dd("annuale", "2025-12-31")
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and len(r["candidati"]) == 2


def test_semestrale_al_31_12_seconda_meta(rete):
    r = _dd("semestrale", "2025-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-02-26"


def test_trimestrale_iii_quarter(rete):
    r = _dd("trimestrale", "2020-09-30")
    assert r["stato"] == "ok" and r["data_deposito"] == "2020-10-28"


def test_fin_periodo_diverso_scartato(rete):
    rete["pagine"][C.URL_DETTAGLIO.format(nreg="2026000101")] = (200, dettaglio(fine="31/03/2026"))
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "non_trovato" and any("Fin periodo" in s["motivo"] for s in r["scartati"])
    assert "non trovato nel registro CNMV" in r["motivo"]


def test_cif_diverso_scartato(rete):
    rete["pagine"][C.URL_DETTAGLIO.format(nreg="2026000101")] = (200, dettaglio(cif="B-99999999"))
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "non_trovato" and any("CIF" in s["motivo"] for s in r["scartati"])


def test_cif_senza_trattino_e_lo_stesso_nif(rete):
    rete["pagine"][C.URL_DETTAGLIO.format(nreg="2026000101")] = (200, dettaglio(cif="A12345674"))
    assert _dd("semestrale", "2026-06-30")["stato"] == "ok"


def test_esercizio_non_solare_per_finestra_dichiarata(rete):
    # nessuna etichetta «I half-year of 2026» dopo il 31/12/2026: la riga «I half-year of 2027» cade nella
    # finestra e il dettaglio conferma Fin periodo 31/12/2026
    ifi = _f("cnmv_ifi_acme.html").replace(b"2026000101\">23/07/2026", b"2027000101\">20/02/2027") \
        .replace(b"of 2026  individual", b"of 2027  individual")
    rete["pagine"][C.URL_IFI.format(nif=NIF)] = (200, ifi)
    rete["pagine"][C.URL_DETTAGLIO.format(nreg="2027000101")] = (200, dettaglio("31/12/2026", "01/07/2026", pub="20/02/2027"))
    r = _dd("semestrale", "2026-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2027-02-20"
    assert any("finestra" in l for l in r["limiti"])


def test_lista_vuota_non_trovato(rete):
    rete["pagine"][C.URL_IFI.format(nif=NIF)] = (200, _f("cnmv_vuoto.html"))
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "non_trovato" and "non trovato nel registro CNMV" in r["motivo"]


def test_lista_di_un_altra_entita_ko(rete):
    rete["pagine"][C.URL_IFI.format(nif=NIF)] = (200, _f("cnmv_ifi_acme.html").replace(b"ACME SINTETICA, S.A.", b"ZZTEST, S.A."))
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "identita_incoerente"


def test_dettaglio_senza_campi_ko_formato(rete):
    rete["pagine"][C.URL_DETTAGLIO.format(nreg="2026000101")] = (200, b"<html>altro</html>")
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "formato"


def test_dettaglio_irraggiungibile_verdetto_sospeso(rete):
    rete["pagine"][C.URL_DETTAGLIO.format(nreg="2026000101")] = ConnectionError("x")
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "rete" and "sospeso" in r["motivo"] and r["data_deposito"] is None


def test_ricerca_con_piu_esiti_prende_solo_la_denominazione_identica(rete):
    rete["ricerca"] = (200, _f("cnmv_ricerca_multi.html"), {})
    nif_cnmv = "A12345674"       # la forma SENZA trattino data dalla CNMV si usa cosi' com'e'
    rete["pagine"][C.URL_IFI.format(nif=nif_cnmv)] = rete["pagine"][C.URL_IFI.format(nif=NIF)]
    r = _dd("semestrale", "2026-06-30")
    assert r["nif"] == nif_cnmv and r["stato"] == "ok"
    assert ("GET", C.URL_IFI.format(nif=nif_cnmv)) in rete["chiamate"]


def test_ricerca_senza_denominazione_identica_ko(rete):
    rete["ricerca"] = (200, _f("cnmv_ricerca_multi.html").replace(b">ACME SINTETICA, S.A.<", b">ACME SINTETICA SL<"), {})
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "identita_non_trovata"


def test_identita_in_cache_niente_seconda_ricerca(rete):
    _dd("semestrale", "2026-06-30")
    n = len(rete["chiamate"])
    r = _dd("annuale", "2025-12-31")
    nuove = rete["chiamate"][n:]
    assert r["stato"] == "ok" and nuove == [("GET", C.URL_IFA.format(nif=NIF))]
    assert r["identita"]["cache"] == "fresca"


# ---------------- ricevuta, cache, rete ----------------
def test_riverifica_ok_e_manomissioni(rete):
    r = _dd("semestrale", "2026-06-30")
    assert C.riverifica_ricevuta(r, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")[0]
    m = json.loads(json.dumps(r))
    u = C.URL_DETTAGLIO.format(nreg="2026000101")
    m["risposte_salvate"][u] = m["risposte_salvate"][u].replace("23/07/2026", "22/07/2026")
    ok, mot = C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "sha256" in mot
    m = json.loads(json.dumps(r))
    del m["risposte_salvate"][u]
    del m["sha256_liste"][u]
    ok, mot = C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "non salvati" in mot
    m = json.loads(json.dumps(r))
    m["data_deposito"] = "2026-07-01"
    assert not C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")[0]


def test_guasto_con_cache_scaduta_stale(rete, monkeypatch):
    _dd("annuale", "2025-12-31")
    vero = time.time
    monkeypatch.setattr(C.time, "time", lambda: vero() + C.TTL_S + 5)
    rete["pagine"][C.URL_IFA.format(nif=NIF)] = ConnectionError("giu")
    r = _dd("annuale", "2025-12-31")
    assert r["stato"] == "STALE" and r["stato_originale"] == "ok" and r["cache"] == "scaduta"
    assert "ConnectionError" in r["motivo"] and r["data_deposito"] == "2026-02-26"


def test_guasto_senza_cache_ko_senza_testo_eccezione(rete):
    rete["pagine"][C.URL_IFA.format(nif=NIF)] = ConnectionError("https://segreto?k=1")
    r = _dd("annuale", "2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "rete" and "segreto" not in r["motivo"]


def test_url_ammessi_e_robots():
    assert C.richiesta_consentita("GET", C.URL_IFI.format(nif=NIF))[0]
    assert C.richiesta_consentita("POST", C.URL_RICERCA)[0]
    assert not C.richiesta_consentita("POST", C.URL_IFI.format(nif=NIF))[0]
    assert not C.richiesta_consentita("GET", "https://www.cnmv.es/portal/x.shtml")[0]
    assert not C.richiesta_consentita("GET", "https://altro.example/portal/ANCV/Isin?isin=" + ISIN)[0]
    assert not C.richiesta_consentita("GET", "http://www.cnmv.es/portal/ANCV/Isin?isin=" + ISIN)[0]


# ---------------- AGGIUNTA 2 (main 05/10) ----------------
def test_nomi_documento_pubblici_coprono_le_etichette_della_fonte():
    import re
    assert set(C.NOMI_DOCUMENTO) == {"annuale", "semestrale", "trimestrale"}
    casi = {"semestrale": "I half-year of 2026 individual and consolidated", "trimestrale": "III quarter of 2020",
            "annuale": "Informe financiero anual"}
    for tipo, testo in casi.items():
        assert any(re.search(rx, testo, re.I) for rx in C.NOMI_DOCUMENTO[tipo]), tipo
        for altro in set(casi) - {tipo}:
            assert not any(re.search(rx, testo, re.I) for rx in C.NOMI_DOCUMENTO[altro]), (tipo, altro)


def test_candidati_in_formato_uniforme(rete):
    for tipo, fine in (("annuale", "2025-12-31"), ("semestrale", "2026-06-30")):
        r = _dd(tipo, fine)
        assert r["candidati"]
        for c in r["candidati"]:
            assert {"titolo", "data", "ora", "url", "categoria", "lingua", "prova"} <= set(c)


def test_riverifica_ambiguo_ricalcola_la_lista(rete):
    rete["pagine"][C.URL_IFA.format(nif=NIF)] = (200, _f("cnmv_ifa_doppio.html"))
    r = _dd("annuale", "2025-12-31")
    assert r["stato"] == "ambiguo"
    assert C.riverifica_ricevuta(r, ticker="ACME.MC", tipo="annuale", periodo_fine="2025-12-31")[0]
    m = json.loads(json.dumps(r))
    m["candidati"] = m["candidati"][1:]
    ok, mot = C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="annuale", periodo_fine="2025-12-31")
    assert not ok and "candidati" in mot


# ---------------- rilievi RV-UE2 (05/10) ----------------
def test_dettagli_oltre_il_massimo_dichiarati(rete, monkeypatch):
    monkeypatch.setattr(C, "MAX_DETTAGLI", 0)
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "non_trovato" and any("oltre i 0 dettagli" in s["motivo"] for s in r["scartati"])


def test_riverifica_titolo_manomesso(rete):
    r = _dd("annuale", "2025-12-31")
    m = json.loads(json.dumps(r))
    m["titolo"] = "altro"
    ok, mot = C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="annuale", periodo_fine="2025-12-31")
    assert not ok and "titolo" in mot


def test_sa_senza_punti_e_la_stessa_denominazione(rete):
    r = _dd("annuale", "2025-12-31", nome="Acme Sintetica SA")
    assert r["stato"] == "ok"
    assert C.nome_canonico("AB C, S.A.") != C.nome_canonico("ABC SA")


def test_catena_isin_nif_riverificata_senza_rete(rete):
    r = _dd("semestrale", "2026-06-30")
    ok, mot = C.riverifica_ricevuta(r, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok and "catena ISIN -> NIF riverificata" in mot
    # pagina ANCV salvata manomessa: lo sha non torna
    m = json.loads(json.dumps(r))
    u = C.URL_ISIN.format(isin=ISIN)
    m["identita"]["risposte"][u] = m["identita"]["risposte"][u].replace("ACME", "ZZZZ")
    ok, mot = C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "sha256" in mot
    # risposta della ricerca cambiata (e sha ricalcolato): la catena da' un altro NIF -> smentita
    m = json.loads(json.dumps(r))
    m["identita"]["risposte"][C.CHIAVE_POST]["location"] = "/Portal/Consultas/IFI/ListaIFI.aspx?nif=B-99999999"
    m["identita"]["prove"][C.CHIAVE_POST] = C._sha_risposta(m["identita"]["risposte"][C.CHIAVE_POST])
    ok, mot = C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "NIF ricalcolato" in mot
    m = json.loads(json.dumps(r))
    del m["identita"]["risposte"]
    assert not C.riverifica_ricevuta(m, ticker="ACME.MC", tipo="semestrale", periodo_fine="2026-06-30")[0]


def test_lista_ifi_troncata_ko_ricerca_troncata(rete):
    righe = "".join('<tr><td data-th="Date of Publication"><a href="../../aldia/detalleifialdia.aspx?nreg=%d">'
                    '23/07/2026</a></td><td>I half-year of 2026</td></tr>' % (3000000000 + i) for i in range(60))
    pagina = ('<span id="ctl00_ContentPrincipal_lblSubtitulo">ACME SINTETICA, S.A.</span>'
              '<table id="ctl00_ContentPrincipal_gridEntidades"><tbody>%s</tbody></table>' % righe)
    rete["pagine"][C.URL_IFI.format(nif=NIF)] = (200, pagina.encode("utf-8"))
    r = _dd("semestrale", "2025-06-30")
    assert r["stato"] == "KO" and r["errore"] == "ricerca_troncata"


def test_kwarg_paese_accettato(rete):
    r = C.get_data_deposito("ACME.MC", tipo="annuale", periodo_fine="2025-12-31", isin=ISIN, paese="ES")
    assert r["stato"] == "ok"


def test_riverifica_paese_coerente_con_la_ricevuta(rete):
    r = _dd("annuale", "2025-12-31")
    kw = dict(ticker="ACME.MC", tipo="annuale", periodo_fine="2025-12-31")
    assert C.riverifica_ricevuta(r, paese="ES", **kw)[0]
    assert C.riverifica_ricevuta(r, **kw)[0]
    ok, mot = C.riverifica_ricevuta(r, paese="NL", **kw)
    assert not ok and "paese" in mot
