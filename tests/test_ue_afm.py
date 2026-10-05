# -*- coding: utf-8 -*-
"""Test di ue_afm (Paesi Bassi, data di DEPOSITO AFM): fixture SINTETICHE, nessuna rete.
Emittenti inventati (ACME SINTETICA, ZZTEST, QQSYN); la forma dell'XML e' quella misurata il 05/10/2026."""
import json
import os
import time
from pathlib import Path

import pytest

from bellomberg.market_data import ue_afm as A

FIX = Path(__file__).parent / "fixtures" / "fonti_ue"
CORPO = (FIX / "afm_export.xml").read_bytes()


@pytest.fixture(autouse=True)
def isolato(monkeypatch, tmp_path):
    """Cache in tmp_path, niente pause, e TRIPWIRE: requests vero vietato in tutto il file."""
    monkeypatch.setattr(A, "CACHE_DIR", str(tmp_path / "cache_ue"))
    monkeypatch.setattr(A, "PAUSA_S", 0)
    import requests

    def _vietato(*a, **k):
        raise AssertionError("rete vera in un test di ue_afm")
    monkeypatch.setattr(requests, "get", _vietato)
    monkeypatch.setattr(requests, "request", _vietato)


@pytest.fixture
def rete(monkeypatch):
    """Finta _richiesta: conta le chiamate; `esito` si cambia nel test."""
    stato = {"n": 0, "esito": (200, CORPO)}

    def _finta(url):
        stato["n"] += 1
        ok, motivo = A.richiesta_consentita(url)
        assert ok, motivo
        e = stato["esito"]
        if isinstance(e, Exception):
            raise e
        return e
    monkeypatch.setattr(A, "_richiesta", _finta)
    return stato


def _dd(tipo, fine, nome="ACME SINTETICA N.V.", ticker="ACME.AS"):
    return A.get_data_deposito(ticker, tipo=tipo, periodo_fine=fine, nome=nome)


# ---------------- parametri e identita' (nessuna rete) ----------------
def test_tipo_non_ammesso_ko_parametro_senza_rete(rete):
    r = _dd("mensile", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "parametro" and rete["n"] == 0


def test_semestrale_non_a_fine_semestre_ko_parametro(rete):
    r = _dd("semestrale", "2026-03-31")
    assert r["errore"] == "parametro" and "incoerente" in r["motivo"] and rete["n"] == 0


def test_nome_mancante_ko_identita_mancante_senza_rete(rete):
    r = A.get_data_deposito("ACME.AS", tipo="annuale", periodo_fine="2025-12-31", isin="NL0000000000")
    assert r["stato"] == "KO" and r["errore"] == "identita_mancante"
    assert "nome" in r["motivo"] and rete["n"] == 0


def test_stesse_chiavi_in_ogni_stato(rete):
    ko = _dd("mensile", "2026-06-30")
    ok = _dd("semestrale", "2026-06-30")
    assert set(ko) == set(ok)
    for k in ("natura_data", "fuso", "prova", "scartati", "risposte_salvate", "sha256_liste", "fonte_modulo"):
        assert k in ok


# ---------------- regola NOME + PERIODO ----------------
def test_semestrale_ok_data_di_deposito_etichettata(rete):
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "ok" and r["prova"] == "titolo"
    # AGGIUNTA 4.2: la fonte non dichiara il fuso -> ora_deposito None, ora in ora_registro, fuso PROXY dichiarato
    assert (r["data_deposito"], r["ora_deposito"], r["ora_registro"]) == ("2026-09-15", None, "15:04")
    assert r["fuso"] == "Europe/Amsterdam" and r["natura_data"] == "deposito_autorita"
    assert any("PROXY" in l for l in r["limiti"])
    assert "non di diffusione" in r["motivo"] and A.LIMITE_DEPOSITO in r["limiti"]
    assert r["url"].endswith("details?id=A2601-00001") and r["protocollo"] == "A2601-00001"
    assert r["paese"] == "NL" and r["fonte_modulo"] == "bellomberg.market_data.ue_afm"


def test_annuale_ok_con_data_esef_nel_nome(rete):
    r = _dd("annuale", "2025-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-02-20" and r["lingua"] == "en"


def test_nome_del_file_senza_anno_prova_finestra_dichiarata(rete):
    r = _dd("annuale", "2024-12-31")
    assert r["stato"] == "ok" and r["prova"] == "finestra" and r["titolo"] == "70001.pdf"
    assert any("finestra" in l and "DEPOSITO" in l for l in r["limiti"])


def test_senza_anno_fuori_finestra_scartato(rete):
    r = _dd("semestrale", "2026-06-30", nome="ZZTEST VIER N.V.")
    assert r["stato"] == "non_trovato" and r["data_deposito"] is None
    assert any("oltre 150 giorni" in s["motivo"] for s in r["scartati"])


def test_omonimi_ambiguo_con_la_lista_dei_nomi(rete):
    r = _dd("semestrale", "2026-06-30", nome="ACME SINTETICA")
    assert r["stato"] == "ambiguo" and r["errore"] == "nome_non_univoco" and r["data_deposito"] is None
    assert set(r["omonimi"]) == {"ACME SINTETICA N.V.", "ACME SINTETICA HOLDING N.V."} and r["candidati"] == []


def test_nome_esatto_non_prende_l_omonimo_holding(rete):
    r = _dd("semestrale", "2026-06-30", nome="Acme Sintetica NV")
    assert r["stato"] == "ok" and r["protocollo"] == "A2601-00001"


def test_nome_assente_non_trovato(rete):
    r = _dd("annuale", "2025-12-31", nome="NESSUNO SINTETICO B.V.")
    assert r["stato"] == "non_trovato" and "nessun emittente" in r["motivo"]


def test_q2_nel_nome_vale_per_la_semestrale(rete):
    r = _dd("semestrale", "2025-06-30")
    assert r["stato"] == "ok" and r["titolo"].startswith("acme Q2 2025")


def test_nome_del_file_di_altro_tipo_scartato(rete):
    r = _dd("annuale", "2023-12-31")
    assert r["stato"] == "non_trovato"
    assert any("altro documento" in s["motivo"] for s in r["scartati"])


def test_due_depositi_validi_ambiguo_mai_il_primo(rete):
    r = _dd("semestrale", "2026-06-30", nome="ZZTEST TWEE B.V.")
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and len(r["candidati"]) == 2


def test_anno_diverso_nel_nome_scartato(rete):
    r = _dd("semestrale", "2026-06-30", nome="ZZTEST DRIE N.V.")
    assert r["stato"] == "non_trovato"
    assert any("anno nel nome del file" in s["motivo"] for s in r["scartati"])


def test_boekjaar_diverso_scartato_anche_se_l_anno_del_file_torna(rete):
    r = _dd("semestrale", "2025-06-30", nome="ZZTEST DRIE N.V.")
    assert r["stato"] == "non_trovato"
    assert any("boekjaar" in s["motivo"] for s in r["scartati"])


def test_data_esef_diversa_scartata(rete):
    r = _dd("annuale", "2025-12-31", nome="ZZTEST ZES N.V.")
    assert r["stato"] == "non_trovato"
    assert any("data nel nome del file 2024-12-31" in s["motivo"] for s in r["scartati"])


def test_data_del_registro_illeggibile_scartata_non_inventata(rete):
    r = _dd("annuale", "2025-12-31", nome="ZZTEST VIJF N.V.")
    assert r["stato"] == "non_trovato"
    assert any("illeggibile" in s["motivo"] for s in r["scartati"])


def test_trimestrale_interim_management_statement(rete):
    r = _dd("trimestrale", "2026-09-30", nome="QQSYN N.V.")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-11-05"


# ---------------- ricevuta ----------------
def test_ricevuta_compressa_sha_sul_corpo_intero(rete):
    r = _dd("semestrale", "2026-06-30")
    voce = r["risposte_salvate"][A.URL_EXPORT]
    assert voce["codifica"] == "gzip+base64"
    corpo = A.decomprimi(voce)
    assert corpo == CORPO.decode("utf-8")
    assert r["sha256_liste"][A.URL_EXPORT] == A.sha_corpo(corpo)


def test_riverifica_ok_e_manomissioni(rete):
    r = _dd("semestrale", "2026-06-30")
    assert A.riverifica_ricevuta(r, ticker="ACME.AS", tipo="semestrale", periodo_fine="2026-06-30")[0]
    # corpo manomesso: lo sha non torna
    m = json.loads(json.dumps(r))
    m["risposte_salvate"][A.URL_EXPORT] = A.comprimi(CORPO.decode("utf-8").replace("9/15/2026", "9/14/2026"))
    ok, mot = A.riverifica_ricevuta(m, ticker="ACME.AS", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "sha256" in mot
    # data della ricevuta alterata: il ricalcolo la smentisce
    m = json.loads(json.dumps(r))
    m["data_deposito"] = "2026-09-01"
    ok, mot = A.riverifica_ricevuta(m, ticker="ACME.AS", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "data_deposito" in mot
    # periodo diverso da quello della ricevuta
    assert not A.riverifica_ricevuta(r, ticker="ACME.AS", tipo="semestrale", periodo_fine="2025-06-30")[0]


# ---------------- cache, guasti, rete ----------------
def test_cache_fresca_non_rilegge_la_fonte(rete):
    _dd("semestrale", "2026-06-30")
    r = _dd("annuale", "2025-12-31")
    assert rete["n"] == 1 and r["cache"] == "fresca" and r["stato"] == "ok"


def test_guasto_con_cache_scaduta_stale_dichiarato(rete, monkeypatch):
    _dd("semestrale", "2026-06-30")
    vero = time.time
    monkeypatch.setattr(A.time, "time", lambda: vero() + A.TTL_EXPORT_S + 10)
    rete["esito"] = ConnectionError("giu")
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "STALE" and r["stato_originale"] == "ok" and r["cache"] == "scaduta"
    assert r["errore"] == "rete" and "ConnectionError" in r["motivo"] and r["data_deposito"] == "2026-09-15"


def test_guasto_senza_cache_ko_col_tipo_dell_eccezione(rete):
    rete["esito"] = ConnectionError("https://segreto?chiave=XYZ")
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "rete"
    assert "ConnectionError" in r["motivo"] and "segreto" not in r["motivo"]


def test_http_non_200_ko(rete):
    rete["esito"] = (503, b"")
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "http" and "503" in r["motivo"]


def test_formato_cambiato_ko(rete):
    rete["esito"] = (200, b"<html>manutenzione</html>")
    r = _dd("semestrale", "2026-06-30")
    assert r["stato"] == "KO" and r["errore"] == "formato"


def test_doctype_rifiutato():
    with pytest.raises(ValueError):
        A.parse_export('<!DOCTYPE r [<!ENTITY x "y">]><register>&x;</register>')


def test_url_ammessi():
    assert A.richiesta_consentita(A.URL_EXPORT)[0]
    assert not A.richiesta_consentita(A.URL_EXPORT.replace("https", "http"))[0]
    assert not A.richiesta_consentita("https://www.afm.nl/sitecore/x")[0]
    assert not A.richiesta_consentita("https://altro.example/export.aspx")[0]
    assert not A.richiesta_consentita(A.URL_EXPORT.replace("format=xml", "format=csv"))[0]


# ---------------- AGGIUNTA 2 (main 05/10) e banco ----------------
def test_deposito_prima_della_fine_del_periodo_scartato(rete):
    r = _dd("semestrale", "2026-06-30", nome="ZZTEST ACHT N.V.")
    assert r["stato"] == "non_trovato"
    assert any("non dopo la fine del periodo" in s["motivo"] for s in r["scartati"])


def test_nomi_documento_pubblici_riconoscono_il_tipo_del_registro():
    import re
    assert set(A.NOMI_DOCUMENTO) == {"annuale", "semestrale", "trimestrale"}
    for tipo, cat in A.TIPO_REGISTRO.items():
        assert any(re.search(rx, cat, re.I) for rx in A.NOMI_DOCUMENTO[tipo]), tipo
        for altro in set(A.NOMI_DOCUMENTO) - {tipo}:
            assert not any(re.search(rx, cat, re.I) for rx in A.NOMI_DOCUMENTO[altro]), (tipo, altro)
    assert not any(re.search(rx, "Semi-annual report 2026", re.I) for rx in A.NOMI_DOCUMENTO["annuale"])
    # anche i nomi OLANDESI del registro (objecttype), come stanno nell'export
    nl = {"annuale": "Jaarlijkse financiële verslaggeving", "semestrale": "Halfjaarlijkse financiële verslaggeving",
          "trimestrale": "Tussentijdse verklaring"}
    for tipo, cat in nl.items():
        assert any(re.search(rx, cat, re.I) for rx in A.NOMI_DOCUMENTO[tipo]), tipo
        for altro in set(nl) - {tipo}:
            assert not any(re.search(rx, cat, re.I) for rx in A.NOMI_DOCUMENTO[altro]), (tipo, altro)


def test_candidati_in_formato_uniforme(rete):
    r = _dd("semestrale", "2026-06-30", nome="ZZTEST TWEE B.V.")
    for c in r["candidati"]:
        assert {"titolo", "data", "ora", "url", "categoria", "lingua", "prova"} <= set(c)
        assert c["prova"] == "titolo" and len(c["data"]) == 10


def test_riverifica_ambiguo_ricalcola_la_lista(rete):
    r = _dd("semestrale", "2026-06-30", nome="ZZTEST TWEE B.V.")
    assert C_OK(r, "semestrale", "2026-06-30")
    m = json.loads(json.dumps(r))
    m["candidati"] = m["candidati"][:1]
    ok, mot = A.riverifica_ricevuta(m, ticker="ACME.AS", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "candidati" in mot
    o = _dd("semestrale", "2026-06-30", nome="ACME SINTETICA")
    assert C_OK(o, "semestrale", "2026-06-30")
    m = json.loads(json.dumps(o))
    m["omonimi"] = ["ACME SINTETICA N.V."]
    assert not A.riverifica_ricevuta(m, ticker="ACME.AS", tipo="semestrale", periodo_fine="2026-06-30")[0]


def C_OK(r, tipo, fine):
    return A.riverifica_ricevuta(r, ticker="ACME.AS", tipo=tipo, periodo_fine=fine)[0]


# ---------------- rilievi RV-UE2 (05/10) ----------------
def test_trimestrale_numero_del_trimestre_deve_coincidere(rete):
    # Q1 e Q3 presenti: al 31/03 vale il Q1, al 30/09 il Q3 (mai ambiguo, mai l'altro trimestre)
    r = _dd("trimestrale", "2026-03-31", nome="QQSYN N.V.")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-05-05"
    assert any("trimestre nel nome del file (Q3)" in s["motivo"] for s in r["scartati"])
    r = _dd("trimestrale", "2026-09-30", nome="QQSYN N.V.")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-11-05"
    # solo un Q3 al 31/03: scartato, non ok
    r = _dd("trimestrale", "2026-03-31", nome="ZZTEST ELF N.V.")
    assert r["stato"] == "non_trovato" and r["data_deposito"] is None


def test_semestrale_con_q1_e_q2_nel_nome_non_e_la_semestrale(rete):
    r = _dd("semestrale", "2026-06-30", nome="ZZTEST TWAALF N.V.")
    assert r["stato"] == "non_trovato" and any("altro documento" in s["motivo"] for s in r["scartati"])


def test_annuale_esercizio_non_solare_boekjaar_anno_precedente(rete):
    r = _dd("annuale", "2026-03-31", nome="ZZTEST NEGEN N.V.")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-06-15"


def test_riverifica_ora_registro_manomessa(rete):
    r = _dd("semestrale", "2026-06-30")
    m = json.loads(json.dumps(r))
    m["ora_registro"] = "09:00"
    ok, mot = A.riverifica_ricevuta(m, ticker="ACME.AS", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "ora_registro" in mot


def test_nome_canonico_non_fonde_nomi_diversi():
    assert A.nome_canonico("Acme Sintetica N.V.") == A.nome_canonico("ACME SINTETICA NV")
    assert A.nome_canonico("AB C N.V.") != A.nome_canonico("ABC NV")


def test_kwarg_paese_accettato(rete):
    r = A.get_data_deposito("ACME.AS", tipo="semestrale", periodo_fine="2026-06-30", nome="ACME SINTETICA N.V.",
                            paese="NL")
    assert r["stato"] == "ok"


def test_riverifica_paese_coerente_con_la_ricevuta(rete):
    r = _dd("semestrale", "2026-06-30")
    kw = dict(ticker="ACME.AS", tipo="semestrale", periodo_fine="2026-06-30")
    assert A.riverifica_ricevuta(r, paese="NL", **kw)[0]
    assert A.riverifica_ricevuta(r, **kw)[0]
    ok, mot = A.riverifica_ricevuta(r, paese="ES", **kw)
    assert not ok and "paese" in mot
