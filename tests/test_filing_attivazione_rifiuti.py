"""Fase E: i collegamenti rifiutati (CIK e LEI) e annullati valgono anche per l'attivazione."""
import sqlite3

import pytest

from bellomberg.market_data import filing_attivazione as fa
from bellomberg.market_data import filing_identita
from bellomberg.storage import filing_preferenze as fpref
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_esef_sintetici import LEI_NOVA, riga_indice
from tests.filing_sec_sintetici import CIK_NOVA

LEI_NOVA2 = "999900NOVA0000000002"
NOVA = {"cik": CIK_NOVA, "ticker": "NOVA", "nome": "Nova Semiconductors Inc.", "origine": "nome"}


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


@pytest.fixture
def pref(tmp_path):
    return tmp_path / "preferenze.json"


def _sorda(sec_stato, sec_cand, esef_stato, *leis):
    """Proposta che IGNORA i rifiuti (caso peggiore): il filtro deve stare anche in attiva."""
    def proponi(ticker, nome=None, **k):
        return {"ticker": ticker, "nome": "Nova S.p.A.",
                "sec": {"stato": sec_stato, "candidati": list(sec_cand), "motivo": "sec test"},
                "esef": {"stato": esef_stato, "motivo": "esef test",
                         "candidati": [{"lei": l, "nome": "NOVA S.P.A.", "origine": "nome"} for l in leis]}}
    return proponi


INDICE = lambda lei: {"righe": [riga_indice(1, 2024), riga_indice(2, 2025)], "origine": "rete", "motivo": None}
CAT_USA = lambda ticker, cik=None: {"stato": "ok", "motivi": [], "documenti": [{"form": "10-Q"}, {"form": "10-K"}]}


def test_proponi_filtra_rifiutati_lei(monkeypatch):
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", lambda: {"righe": [], "origine": "cache", "motivo": None})
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})
    monkeypatch.setattr("bellomberg.market_data.esef.candidati_lei", lambda t, n=None, **k: {
        "stato": "ambiguo", "motivo": "2", "candidati": [{"lei": LEI_NOVA, "nome": "NOVA", "origine": "nome"},
                                                        {"lei": LEI_NOVA2, "nome": "NOVA 2", "origine": "nome"}]})
    out = filing_identita.proponi("NOVA.MI", "Nova S.p.A.", rifiutati_lei=frozenset({LEI_NOVA}))
    assert [c["lei"] for c in out["esef"]["candidati"]] == [LEI_NOVA2]
    out = filing_identita.proponi("NOVA.MI", "Nova S.p.A.", rifiutati_lei=frozenset({LEI_NOVA, LEI_NOVA2}))
    assert out["esef"]["stato"] == "nessuno" and out["esef"]["candidati"] == []


def test_attiva_non_ricollega_un_lei_rifiutato(store, pref):
    fpref.rifiuta_lei("NOVA.MI", LEI_NOVA, path=pref)
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_sorda("nessuno", [], "univoco", LEI_NOVA), indice_fn=INDICE,
                  pref_path=pref)
    assert r["esito"] == "senza_fonte" and store.get_profile("NOVA.MI") is None


def test_attiva_lei_scelto_ma_rifiutato(store, pref):
    fpref.rifiuta_lei("NOVA.MI", LEI_NOVA, path=pref)
    r = fa.attiva(store, "NOVA.MI", lei=LEI_NOVA, proponi_fn=_sorda("nessuno", [], "ambiguo", LEI_NOVA, LEI_NOVA2),
                  indice_fn=INDICE, pref_path=pref)
    assert r["esito"] == "errore" and store.get_profile("NOVA.MI") is None


def test_attiva_non_ricollega_un_cik_rifiutato(store, pref):
    fpref.rifiuta("NOVA", CIK_NOVA, path=pref)
    r = fa.attiva(store, "NOVA", proponi_fn=_sorda("univoco", [NOVA], "nessuno"), catalogo_fn=CAT_USA,
                  indice_fn=INDICE, pref_path=pref)
    assert r["esito"] != "attivato" and store.get_profile("NOVA") is None


def test_rifiutati_lei_passati_a_proponi(store, pref):
    fpref.rifiuta_lei("NOVA.MI", LEI_NOVA, path=pref)
    fpref.rifiuta("NOVA.MI", "9990077", path=pref)
    visti = {}

    def proponi(ticker, nome=None, rifiutati=frozenset()):  # firma vecchia: solo `rifiutati`
        visti["rifiutati"] = rifiutati
        return _sorda("nessuno", [], "nessuno")(ticker)

    fa.attiva(store, "NOVA.MI", proponi_fn=proponi, indice_fn=INDICE, pref_path=pref)
    assert visti["rifiutati"] == {LEI_NOVA, "0009990077"}


def _scollega(store, pref, ticker="NOVA.MI"):
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    p = profilo_esef(ticker, lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="it")
    store.set_profile(ticker, p, enabled=True, interval_hours=24)
    v = store.set_profile(ticker, p, enabled=False, interval_hours=24)["version"]
    fpref.rifiuta_lei(ticker, LEI_NOVA, path=pref)
    fpref.imposta_scollegato(ticker, v, path=pref)
    return v


def test_attiva_mancanti_salta_gli_scollegati(store, pref):
    v = _scollega(store, pref)
    out = fa.attiva_mancanti(store, ["NOVA.MI"], proponi_fn=_sorda("nessuno", [], "univoco", LEI_NOVA2),
                             indice_fn=INDICE, pref_path=pref)
    assert out["scollegati"] == ["NOVA.MI"] and out["attivati"] == []
    assert store.get_profile("NOVA.MI")["version"] == v


def test_attiva_esplicita_dopo_scollega(store, pref):
    v = _scollega(store, pref)
    r = fa.attiva(store, "NOVA.MI", lei=LEI_NOVA2, proponi_fn=_sorda("nessuno", [], "ambiguo", LEI_NOVA, LEI_NOVA2),
                  indice_fn=INDICE, pref_path=pref)
    assert r["esito"] == "attivato"
    nuovo = store.get_profile("NOVA.MI")
    assert nuovo["version"] == v + 1 and nuovo["enabled"] and nuovo["profile"]["lei"] == LEI_NOVA2
    assert fpref.scollegati(fpref.carica(pref)) == {}


def test_attiva_dopo_scollega_senza_candidati_resta_scollegato(store, pref):
    v = _scollega(store, pref)
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_sorda("nessuno", [], "univoco", LEI_NOVA), indice_fn=INDICE,
                  pref_path=pref)
    assert r["esito"] == "senza_fonte" and store.get_profile("NOVA.MI")["version"] == v
    assert fpref.scollegati(fpref.carica(pref)) == {"NOVA.MI": v}


def test_profilo_esistente_resta_gia_attivo(store, pref):
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    store.set_profile("NOVA.MI", profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova", origine="nome", lingua="it"),
                      interval_hours=24)
    r = fa.attiva(store, "NOVA.MI", proponi_fn=_sorda("nessuno", [], "univoco", LEI_NOVA2), indice_fn=INDICE,
                  pref_path=pref)
    assert r["esito"] == "gia_attivo"
