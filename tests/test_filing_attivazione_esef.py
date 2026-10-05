"""Attivazione ESEF (fase B, task 6): proposta, conferma del LEI, attivazione in blocco. Dati sintetici."""
import sqlite3

import pytest

from bellomberg.market_data import filing_attivazione as fa
from bellomberg.market_data import filing_identita
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_esef_sintetici import LEI_KORE, LEI_NOVA, riga_indice
from tests.filing_sec_sintetici import CIK_NOVA

LEI_NOVA2 = "999900NOVA0000000002"


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


@pytest.fixture
def pref(tmp_path):
    return tmp_path / "preferenze.json"


def _proposta(sec_stato, esef_stato, *leis, sec_cand=()):
    def proponi(ticker, nome=None, rifiutati=frozenset()):
        return {"ticker": ticker, "nome": "Nova S.p.A.",
                "sec": {"stato": sec_stato, "candidati": list(sec_cand), "motivo": "sec test"},
                "esef": {"stato": esef_stato, "motivo": "esef test",
                         "candidati": [{"lei": l, "nome": "NOVA S.P.A.", "origine": "nome"} for l in leis if l not in rifiutati]}}
    return proponi


def _indice(*righe):
    def indice(lei):
        return {"righe": list(righe), "origine": "rete", "motivo": None}
    return indice


INDICE_NOVA = _indice(riga_indice(1, 2024, lingua="it"), riga_indice(2, 2024), riga_indice(3, 2025, lingua="it"), riga_indice(4, 2025))


def test_lei_univoco_attiva_profilo_esef_col_ticker_del_portafoglio(store, pref):
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("nessuno", "univoco", LEI_NOVA), indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "attivato" and r["fonte"] == "esef"
    prof = store.get_profile("NOVA.MI")
    assert prof["interval_hours"] == 24 and prof["enabled"] is True and prof["qualitative_enabled"] is False
    p = prof["profile"]
    assert p["ticker"] == "NOVA.MI" and p["lei"] == LEI_NOVA and p["esef_modo"] == "blocchi"
    assert p["lingua"] == "en" and p["origine_collegamento"] == "nome"


def test_lingua_originale_se_inglese_manca(store, pref):
    indice = _indice(riga_indice(1, 2024, lingua="it"), riga_indice(3, 2025, lingua="it"), riga_indice(4, 2025))
    fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("nessuno", "univoco", LEI_NOVA), indice_fn=indice, pref_path=pref)
    assert store.get_profile("NOVA.MI")["profile"]["lingua"] == "it"


def test_lei_ambiguo_resta_da_confermare(store, pref):
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("nessuno", "ambiguo", LEI_NOVA, LEI_NOVA2),
                  indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "da_confermare" and store.get_profile("NOVA.MI") is None


def test_conferma_del_lei_con_un_clic(store, pref):
    r = fa.attiva(store, "NOVA.MI", lei=LEI_NOVA2, proponi_fn=_proposta("nessuno", "ambiguo", LEI_NOVA, LEI_NOVA2),
                  indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "attivato"
    p = store.get_profile("NOVA.MI")["profile"]
    assert p["lei"] == LEI_NOVA2 and p["origine_collegamento"] == "confermato_utente"


def test_lei_scelto_fuori_dai_candidati_e_errore(store, pref):
    r = fa.attiva(store, "NOVA.MI", lei=LEI_KORE, proponi_fn=_proposta("nessuno", "ambiguo", LEI_NOVA, LEI_NOVA2),
                  indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "errore" and store.get_profile("NOVA.MI") is None


def test_cik_e_lei_insieme_rifiutati(store, pref):
    with pytest.raises(ValueError):
        fa.attiva(store, "NOVA.MI", cik=CIK_NOVA, lei=LEI_NOVA, proponi_fn=_proposta("nessuno", "univoco", LEI_NOVA),
                  indice_fn=INDICE_NOVA, pref_path=pref)


def test_sec_in_errore_non_ripiega_sull_esef(store, pref):
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("errore", "univoco", LEI_NOVA), indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "errore" and store.get_profile("NOVA.MI") is None


def test_sec_ambigua_resta_da_confermare_anche_con_lei_univoco(store, pref):
    cand = [{"cik": CIK_NOVA, "ticker": "NOVA", "nome": "Nova", "origine": "nome"},
            {"cik": "0009990002", "ticker": "NOVB", "nome": "Nova", "origine": "nome"}]
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("ambiguo", "univoco", LEI_NOVA, sec_cand=cand),
                  indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "da_confermare"


def test_esef_in_errore_e_errore(store, pref):
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("nessuno", "errore"), indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "errore" and "esef test" in r["motivo"]


def test_nessuna_fonte(store, pref):
    r = fa.attiva(store, "KORE.DE", proponi_fn=_proposta("nessuno", "nessuno"), indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] == "senza_fonte" and "esef test" in r["motivo"]


def test_repository_senza_json_e_senza_fonte(store, pref):
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("nessuno", "univoco", LEI_NOVA),
                  indice_fn=_indice(riga_indice(1, 2023, json=False)), pref_path=pref)
    assert r["esito"] == "senza_fonte" and "xBRL-JSON" in r["motivo"]


def test_indice_illeggibile_e_errore(store, pref):
    def giu(lei):
        raise RuntimeError("rete giu'")

    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("nessuno", "univoco", LEI_NOVA), indice_fn=giu, pref_path=pref)
    assert r["esito"] == "errore" and "rete giu'" in r["motivo"]


def test_lei_rifiutato_non_proposto(store, pref):
    from bellomberg.storage import filing_preferenze
    dati = filing_preferenze.carica(pref)
    dati["rifiutati"]["NOVA.MI"] = [LEI_NOVA]
    filing_preferenze.salva(dati, pref)
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_proposta("nessuno", "univoco", LEI_NOVA), indice_fn=INDICE_NOVA, pref_path=pref)
    assert r["esito"] != "attivato"


def test_in_blocco_un_errore_esef_non_ferma_gli_altri(store, pref):
    def proponi(ticker, nome=None, rifiutati=frozenset()):
        if ticker == "KORE.MI":
            raise RuntimeError("boom")
        return _proposta("nessuno", "univoco", LEI_NOVA)(ticker)

    out = fa.attiva_mancanti(store, ["KORE.MI", "NOVA.MI"], proponi_fn=proponi, indice_fn=INDICE_NOVA, pref_path=pref)
    assert out["attivati"] == ["NOVA.MI"] and out["errori"][0]["ticker"] == "KORE.MI"


# --- proponi ------------------------------------------------------------------------------------

def _sec_vuota(monkeypatch, stato="nessuno"):
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", lambda: {"righe": [], "origine": "cache", "motivo": None})
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})


def test_proponi_aggiunge_esef_se_la_sec_non_e_univoca(monkeypatch):
    _sec_vuota(monkeypatch)
    viste = []
    monkeypatch.setattr("bellomberg.market_data.esef.candidati_lei",
                        lambda t, n=None, **k: viste.append((t, n)) or {"stato": "univoco", "motivo": "",
                                                                       "candidati": [{"lei": LEI_NOVA, "nome": "NOVA", "origine": "nome"}]})
    out = filing_identita.proponi("NOVA.MI", "Nova S.p.A.")
    assert out["sec"]["stato"] == "nessuno" and out["esef"]["stato"] == "univoco"
    assert viste == [("NOVA.MI", "Nova S.p.A.")]


def test_proponi_filtra_i_lei_rifiutati(monkeypatch):
    _sec_vuota(monkeypatch)
    monkeypatch.setattr("bellomberg.market_data.esef.candidati_lei",
                        lambda t, n=None, **k: {"stato": "univoco", "motivo": "",
                                                "candidati": [{"lei": LEI_NOVA, "nome": "NOVA", "origine": "nome"}]})
    out = filing_identita.proponi("NOVA.MI", "Nova S.p.A.", rifiutati=frozenset({LEI_NOVA}))
    assert out["esef"]["stato"] == "nessuno" and out["esef"]["candidati"] == []


def test_proponi_sec_univoca_non_interroga_l_esef(monkeypatch):
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", lambda: {"righe": [
        {"cik": CIK_NOVA, "ticker": "NOVA", "nome": "Nova Semiconductors Inc."}], "origine": "cache", "motivo": None})
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})
    monkeypatch.setattr("bellomberg.market_data.esef.candidati_lei", lambda *a, **k: pytest.fail("ESEF non atteso"))
    out = filing_identita.proponi("NOVA", "Nova Semiconductors Inc.")
    assert out["sec"]["stato"] == "univoco" and "esef" not in out
