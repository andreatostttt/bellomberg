"""Installazione pulita senza SEC_CONTACT_EMAIL (RUN-ANDREA, Opus 5.5, 05/10): la SEC non
configurata si DICHIARA, l'ESEF dei listini esteri si prova lo stesso, i motivi di ogni esito
dell'attivazione in blocco arrivano alle preferenze.
Ticker e numeri inventati, nessuna rete."""
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.market_data import filing_attivazione as fa
from bellomberg.market_data import filing_identita, sec_edgar
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_esef_sintetici import LEI_NOVA, riga_indice


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


@pytest.fixture
def pref(tmp_path):
    return tmp_path / "preferenze.json"


def _senza_contatto(**k):
    raise sec_edgar.ContattoMancante("SEC_CONTACT_EMAIL assente nel .env")


def _indice(lei):
    return {"righe": [riga_indice(1, 2025), riga_indice(2, 2025, lingua="it")], "origine": "rete", "motivo": None}


def _esef(stato, motivo="esef test"):
    cand = [{"lei": LEI_NOVA, "nome": "QQSYN S.P.A.", "origine": "nome"}] if stato in ("univoco", "ambiguo") else []
    return lambda t, n, rifiutati=frozenset(), rifiutati_lei=frozenset(): {"stato": stato, "candidati": cand,
                                                                          "motivo": motivo}


# ---------------------------------------------------------------- proposta
def test_proponi_sec_non_configurata_prova_l_esef_per_il_listino_estero(monkeypatch):
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", _senza_contatto)
    monkeypatch.setattr(filing_identita, "proponi_esef", _esef("univoco"))
    r = filing_identita.proponi("QQSYN.MI", "QQSYN S.p.A.")
    assert r["sec"]["stato"] == "non_configurata"
    assert r["sec"]["motivo"] == "SEC non configurata: manca SEC_CONTACT_EMAIL nel .env — ESEF attivo"
    assert r["esef"]["stato"] == "univoco" and r["preferita"] == "esef"


def test_proponi_sec_non_configurata_titolo_usa_non_interroga_l_esef(monkeypatch):
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", _senza_contatto)

    def vietato(*a, **k):
        raise AssertionError("un titolo USA non interroga filings.xbrl.org")

    monkeypatch.setattr(filing_identita, "proponi_esef", vietato)
    r = filing_identita.proponi("ZZTEST", "Zz Test Inc.")
    assert r["sec"]["stato"] == "non_configurata" and "esef" not in r and r["preferita"] is None


def test_proponi_esef_senza_contatto_interroga_l_esef(monkeypatch):
    """Riscritto sulla decisione PM 05/10 sera: prima senza contatto l'ESEF rispondeva «ESEF non
    configurato»; ora esef._headers non solleva e proponi_esef riceve i candidati veri."""
    from bellomberg.market_data import esef
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    monkeypatch.setattr(esef, "_cerca_entita", lambda nome, ritmo=None: (
        [{"id": "1", "attributes": {"identifier": "ZZLEI0000000000000007", "name": "QQSYN S.p.A."}}],
        ["QQSYN S.p.A."], "QQSYN S.p.A.", None))
    from bellomberg.storage import negozi_privati
    monkeypatch.setattr(negozi_privati, "carica_lei", lambda: {"lei": {}, "origine": "test", "motivo": ""})
    r = filing_identita.proponi_esef("QQSYN.MI", "QQSYN S.p.A.")
    assert r["stato"] == "univoco" and r["candidati"][0]["lei"] == "ZZLEI0000000000000007", r
    assert not hasattr(filing_identita, "ESEF_NON_CONFIGURATO")


# ---------------------------------------------------------------- attivazione
def _proposta_nc(esef=None):
    def proponi(ticker, nome=None, rifiutati=frozenset()):
        out = {"ticker": ticker, "nome": "QQSYN S.p.A.",
               "sec": {"stato": "non_configurata", "candidati": [], "motivo": filing_identita.SEC_NON_CONFIGURATA}}
        if esef is not None:
            out["esef"] = esef
        return out
    return proponi


def test_attiva_estero_senza_sec_si_attiva_su_esef_e_lo_dichiara(store, pref):
    esef = {"stato": "univoco", "motivo": "LEI dal negozio privato lei_emittenti",
            "candidati": [{"lei": LEI_NOVA, "nome": "QQSYN S.P.A.", "origine": "negozio"}]}
    r = fa.attiva(store, "QQSYN.MI", proponi_fn=_proposta_nc(esef), indice_fn=_indice, pref_path=pref)
    assert r["esito"] == "attivato" and r["fonte"] == "esef"
    assert "SEC non configurata" in r["motivo"] and "SEC non consultata" in r["motivo"]
    assert store.get_profile("QQSYN.MI")["profile"]["lei"] == LEI_NOVA


def test_attiva_usa_senza_sec_e_errore_di_configurazione_non_senza_fonte(store, pref):
    r = fa.attiva(store, "ZZTEST", proponi_fn=_proposta_nc(), pref_path=pref)
    assert r["esito"] == "errore" and r["motivo"] == filing_identita.SEC_NON_CONFIGURATA
    assert store.get_profile("ZZTEST") is None


def test_attiva_estero_senza_sec_ed_esef_nessuno_non_e_senza_fonte(store, pref):
    esef = {"stato": "nessuno", "candidati": [], "motivo": "nessuna entita' sul repository ESEF"}
    r = fa.attiva(store, "QQSYN.MI", proponi_fn=_proposta_nc(esef), pref_path=pref)
    assert r["esito"] == "errore"
    assert r["motivo"].startswith(filing_identita.SEC_NON_CONFIGURATA) and "ESEF: nessuna entita'" in r["motivo"]


def test_attiva_cik_scelto_senza_sec_dichiara_la_configurazione(store, pref):
    r = fa.attiva(store, "ZZTEST", cik="9990077", proponi_fn=_proposta_nc(), pref_path=pref)
    assert r["esito"] == "errore" and r["motivo"] == filing_identita.SEC_NON_CONFIGURATA


def test_attiva_mancanti_porta_i_motivi_e_l_avviso(store, pref, monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    esef_ok = {"stato": "univoco", "motivo": "LEI dal negozio privato lei_emittenti",
               "candidati": [{"lei": LEI_NOVA, "nome": "QQSYN S.P.A.", "origine": "negozio"}]}

    def proponi(ticker, nome=None, rifiutati=frozenset()):
        return _proposta_nc(esef_ok if ticker == "QQSYN.MI" else None)(ticker)

    out = fa.attiva_mancanti(store, ["QQSYN.MI", "ZZTEST"], proponi_fn=proponi, indice_fn=_indice, pref_path=pref)
    assert out["attivati"] == ["QQSYN.MI"] and [e["ticker"] for e in out["errori"]] == ["ZZTEST"]
    assert "SEC non consultata" in out["motivi"]["QQSYN.MI"] and "ZZTEST" not in out["motivi"]
    assert out["avviso_configurazione"] == filing_identita.SEC_NON_CONFIGURATA


def test_attiva_mancanti_con_contatto_nessun_avviso(store, pref, monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.invalid")
    out = fa.attiva_mancanti(store, [], pref_path=pref)
    assert out["avviso_configurazione"] is None and out["motivi"] == {}


# ---------------------------------------------------------------- route: il motivo si salva
def test_activate_missing_salva_il_motivo_di_ogni_esito(tmp_path, monkeypatch):
    from tests.test_filing_routes_attivazione import _client
    from bellomberg.storage import filing_preferenze

    riepilogo = {"attivati": [], "da_confermare": ["KQSYN.DE"], "senza_fonte": ["QQSYN.MI"], "esclusi": [],
                 "gia_attivi": [], "scollegati": [], "errori": [{"ticker": "ZZTEST", "motivo": "SEC non configurata"}],
                 "motivi": {"KQSYN.DE": "2 emittenti ESEF con lo stesso nome",
                            "QQSYN.MI": "nessun deposito ESEF con xBRL-JSON"},
                 "avviso_configurazione": None}
    att = SimpleNamespace(attiva_mancanti=lambda store, tickers, **k: riepilogo, attiva=None, completa_6k=None)
    client, _ = _client(tmp_path, att, monkeypatch, tickers=["KQSYN.DE", "QQSYN.MI", "ZZTEST"])
    r = client.post("/filings/activate-missing")
    assert r.status_code == 200
    esiti = filing_preferenze.esiti(filing_preferenze.carica())
    assert esiti["QQSYN.MI"]["esito"] == "senza_fonte"
    assert esiti["QQSYN.MI"]["motivo"] == "nessun deposito ESEF con xBRL-JSON"
    assert esiti["KQSYN.DE"]["motivo"] == "2 emittenti ESEF con lo stesso nome"
    assert esiti["ZZTEST"]["motivo"] == "SEC non configurata"
