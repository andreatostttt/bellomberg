"""Revisione G1 (04/10/2026): identita' degli emittenti SEC nell'attivazione automatica.

Nomi, ticker e CIK inventati. Casi:
1. listino estero (suffisso): un omonimo SEC con la stessa radice del nome ma un'altra forma
   («ACME Holdings plc» contro «ACME Inc.») non si collega; nemmeno il nome identico basta per
   un collegamento automatico (solo «da confermare»); la prova d'identita' del profilo non passa
   sul documento dell'altra societa';
2. ticker senza suffisso: il solo ticker uguale (crypto/ETF contro un trust USA omonimo) non
   attiva nulla senza conferma;
3. la normalizzazione che toglie holding/group non collega da sola due emittenti diversi.
"""
import re
import sqlite3

import pytest

from bellomberg.market_data import filing_attivazione as fa
from bellomberg.market_data import filing_identita, sec_edgar
from bellomberg.market_data.filing_identita import proponi_sec
from bellomberg.market_data.filing_profili_auto import _regex_nome, profilo_sec
from bellomberg.storage.filing_store import FilingStore, ensure_schema

ACME_INC = {"cik": "0009990901", "ticker": "ACMI", "nome": "ACME Inc."}
TRUST = {"cik": "0009990902", "ticker": "ZZCOIN", "nome": "Grayscale ZZCoin Mini Trust"}
NOVA = {"cik": "0009990903", "ticker": "NOVQ", "nome": "Nova Quantum Inc."}
ALFA = {"cik": "0009990904", "ticker": "ALFQ", "nome": "Alfa Holdings Inc."}
RIGHE = [ACME_INC, TRUST, NOVA, ALFA]
CAT_10K = lambda ticker, cik=None: {"stato": "ok", "motivi": [], "documenti": [{"form": "10-K"}, {"form": "10-Q"}]}


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


def _rete_finta(monkeypatch, nome, esef=None):
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", lambda **k: {"righe": RIGHE, "origine": "cache", "motivo": None})
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})
    monkeypatch.setattr(filing_identita, "nome_emittente", lambda t: nome)
    monkeypatch.setattr("bellomberg.market_data.esef.candidati_lei", lambda *a, **k: esef or {
        "stato": "nessuno", "candidati": [], "motivo": "nessuna entita' (test)"})


# --- 1. listino estero ----------------------------------------------------------------------------

def test_holding_estera_non_e_l_omonimo_sec_con_altra_forma():
    r = proponi_sec("ACME.L", "ACME Holdings plc", righe=RIGHE, alias={})
    assert r["stato"] != "univoco"
    assert all(c["origine"] == "nome_simile" for c in r["candidati"])  # al massimo un suggerimento


def test_nome_identico_con_suffisso_e_solo_da_confermare():
    r = proponi_sec("ACME.L", "ACME Inc.", righe=RIGHE, alias={})
    assert r["stato"] == "ambiguo" and r["candidati"][0]["cik"] == ACME_INC["cik"]
    assert "suffisso di listino" in r["motivo"] and "conferma" in r["motivo"]


def test_alias_verificato_resta_univoco():
    r = proponi_sec("ACX.L", "ACME Holdings plc", righe=RIGHE, alias={"ACX": "ACMI"})
    assert r["stato"] == "univoco" and r["candidati"][0]["origine"] == "alias"


def test_attiva_mancanti_non_collega_la_holding_estera(store, tmp_path, monkeypatch):
    _rete_finta(monkeypatch, "ACME Holdings plc")
    out = fa.attiva_mancanti(store, ["ACME.L"], catalogo_fn=CAT_10K, pref_path=tmp_path / "p.json")
    assert out["attivati"] == [] and store.get_profile("ACME.L") is None


def test_attiva_mancanti_nome_identico_con_suffisso_da_confermare(store, tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", CAT_10K)
    _rete_finta(monkeypatch, "ACME Inc.")
    out = fa.attiva_mancanti(store, ["ACME.L"], catalogo_fn=CAT_10K, pref_path=tmp_path / "p.json")
    assert out["da_confermare"] == ["ACME.L"] and store.get_profile("ACME.L") is None


def test_omonimo_sec_senza_bilanci_non_ferma_l_esef(store, tmp_path, monkeypatch):
    # Nome identico a un emittente SEC senza 10-K/20-F (ADR OTC): scartato, si attiva l'ESEF.
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda t, cik=None, **k: {"stato": "ok", "motivi": [], "documenti": [{"form": "F-6"}]})
    _rete_finta(monkeypatch, "ACME Inc.")
    out = filing_identita.proponi("ACME.MI")
    assert out["sec"]["stato"] == "nessuno" and out["sec"]["scartati"][0]["cik"] == ACME_INC["cik"]


def test_prova_d_identita_del_profilo_fallisce_sull_altra_societa():
    rx = profilo_sec("ACME.L", cik=ACME_INC["cik"], sec_ticker="ACMI", nome="ACME Holdings plc",
                     origine="nome", forme={"10-K"})["verifica"]["emittente"]
    assert not re.search(rx, "ACME Inc.\nANNUAL REPORT PURSUANT TO SECTION 13", re.I | re.M)
    assert re.search(rx, "ACME HOLDINGS PLC\nAnnual report", re.I | re.M)


def test_prova_d_identita_regge_sui_nomi_veri_con_altra_grafia():
    assert re.search(_regex_nome("Nova Quantum Inc."), "NOVA QUANTUM, INC.\nForm 10-K", re.I)
    assert re.search(_regex_nome("Nova Quantum Corporation"), "Nova Quantum Corp. annual report", re.I)


def test_confermato_dall_utente_vale_anche_il_nome_sec(store, tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", CAT_10K)
    _rete_finta(monkeypatch, "NOVA QUANT.")  # nome abbreviato del listino: solo «nome simile»
    r = fa.attiva(store, "NOVQ.DE", cik=NOVA["cik"], catalogo_fn=CAT_10K, pref_path=tmp_path / "p.json")
    assert r["esito"] == "attivato", r
    rx = store.get_profile("NOVQ.DE")["profile"]["verifica"]["emittente"]
    assert re.search(rx, "Nova Quantum Inc.\nAnnual report", re.I)
    assert not re.search(rx, "ACME Inc.\nAnnual report", re.I)


# --- 2. ticker senza suffisso ---------------------------------------------------------------------

def test_solo_ticker_uguale_a_un_trust_e_da_confermare():
    r = proponi_sec("ZZCOIN", "ZZCoin", righe=RIGHE, alias={})
    assert r["stato"] == "ambiguo" and "conferma" in r["motivo"]


def test_solo_ticker_senza_nome_e_da_confermare():
    assert proponi_sec("ZZCOIN", None, righe=RIGHE, alias={})["stato"] == "ambiguo"


def test_attiva_mancanti_non_attiva_il_trust_omonimo(store, tmp_path, monkeypatch):
    _rete_finta(monkeypatch, "ZZCoin")
    out = fa.attiva_mancanti(store, ["ZZCOIN"], catalogo_fn=CAT_10K, pref_path=tmp_path / "p.json")
    assert out["attivati"] == [] and out["da_confermare"] == ["ZZCOIN"]
    assert store.get_profile("ZZCOIN") is None


def test_ticker_e_nome_compatibili_restano_automatici(store, tmp_path, monkeypatch):
    for nome in ("Nova Quantum Inc.", "NOVA QUANTUM", "Nova"):
        assert proponi_sec("NOVQ", nome, righe=RIGHE, alias={})["stato"] == "univoco", nome
    _rete_finta(monkeypatch, "Nova Quantum Inc.")
    out = fa.attiva_mancanti(store, ["NOVQ"], catalogo_fn=CAT_10K, pref_path=tmp_path / "p.json")
    assert out["attivati"] == ["NOVQ"]


# --- 3. holding/group --------------------------------------------------------------------------------

def test_group_e_holdings_non_sono_lo_stesso_emittente():
    # Prima: _norm_issuer toglieva entrambe le parole e «Alfa Group» diventava «Alfa Holdings Inc.».
    r = proponi_sec("ALFQ", "Alfa Group", righe=RIGHE, alias={})
    assert r["stato"] != "univoco"
    r = proponi_sec("ALFX.DE", "Alfa Group AG", righe=RIGHE, alias={})
    assert r["stato"] != "univoco"


# --- Seguito della revisione (REV_G1) -----------------------------------------------------------------

TRUST_OMONIMO = [{"cik": "0009990911", "ticker": "ZTC", "nome": "ZENTACOIN TRUST"}]


def test_crypto_e_trust_omonimo_con_lo_stesso_simbolo_mai_automatico():
    # Rilievo 4: «Zentacoin» e' l'inizio di «ZENTACOIN TRUST»: compatibili per nome, ma il trust non e' la moneta.
    r = proponi_sec("ZTC", "Zentacoin", righe=TRUST_OMONIMO, alias={})
    assert r["stato"] == "ambiguo" and "trust" in r["motivo"]


def test_tipo_non_operativo_dal_registro_mai_automatico():
    r = proponi_sec("NOVQ", "Nova Quantum", righe=RIGHE, alias={}, tipo="crypto")
    assert r["stato"] == "ambiguo" and "crypto" in r["motivo"]
    assert proponi_sec("NOVQ", "Nova Quantum", righe=RIGHE, alias={}, tipo="operating")["stato"] == "univoco"


def test_tipo_dal_registro_arriva_alla_proposta(monkeypatch):
    _rete_finta(monkeypatch, "Nova Quantum Inc.")
    monkeypatch.setattr("bellomberg.storage.classificazione.voce", lambda t, negozio=None: {"tipo": "etf"})
    assert filing_identita.proponi("NOVQ")["sec"]["stato"] == "ambiguo"


@pytest.mark.parametrize("nome, sec", [("Harrow & Finch", "HARROW AND FINCH INC"),
                                       ("Harrow and Finch", "HARROW & FINCH INC"),
                                       ("Zélon Group", "ZELON GROUP INC"),
                                       ("VELMORA P.L.C.", "Velmora plc")])
def test_e_commerciale_accenti_e_forme_puntate_non_bloccano(nome, sec):
    # Rilievo 5: prima finivano «da confermare» con un motivo falso («nome SEC diverso»).
    r = proponi_sec("HFZ", nome, righe=[{"cik": "0009990912", "ticker": "HFZ", "nome": sec}], alias={})
    assert r["stato"] == "univoco", r["motivo"]
    assert re.search(_regex_nome(nome), sec, re.I)


def test_nome_prefisso_di_parola_di_un_altro_nome_sec_con_lo_stesso_ticker():
    # Rilievo 6: «Brantel» non e' «BRANTELLO CORP» anche se ne e' l'inizio (prima parola diversa).
    r = proponi_sec("BRT", "Brantel", righe=[{"cik": "0009990913", "ticker": "BRT", "nome": "BRANTELLO CORP"}], alias={})
    assert r["stato"] == "ambiguo" and "diverso" in r["motivo"]
