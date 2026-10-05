import sqlite3

import pytest

from bellomberg.market_data import filing_attivazione as fa
from bellomberg.storage import filing_preferenze as fp
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_sec_sintetici import CIK_KORE, CIK_NOVA


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


@pytest.fixture
def pref(tmp_path):
    return tmp_path / "preferenze.json"


def _proposta(stato, *cands):
    def proponi(ticker, nome=None, rifiutati=frozenset()):
        return {"ticker": ticker, "nome": "Nova Semiconductors Inc.",
                "sec": {"stato": stato, "candidati": [c for c in cands if c["cik"] not in rifiutati], "motivo": "test"}}
    return proponi


NOVA = {"cik": CIK_NOVA, "ticker": "NOVA", "nome": "Nova Semiconductors Inc.", "origine": "nome"}
KORE = {"cik": CIK_KORE, "ticker": "KORE", "nome": "Kore Mining plc", "origine": "nome"}
KORE2 = {"cik": "0009990012", "ticker": "KOREY", "nome": "Kore Mining Ltd", "origine": "nome"}


def CAT_USA(ticker, cik=None):
    return {"stato": "ok", "motivi": [], "documenti": [{"form": "10-Q"}, {"form": "10-K"}]}


def test_attiva_univoco_salva_col_ticker_del_portafoglio(store, pref):
    r = fa.attiva(store, "NOVA.DE", proponi_fn=_proposta("univoco", NOVA), catalogo_fn=CAT_USA, pref_path=pref)
    assert r["esito"] == "attivato" and r["profilo_versione"] == 1
    prof = store.get_profile("NOVA.DE")
    assert prof["interval_hours"] == 24 and prof["enabled"] is True and prof["qualitative_enabled"] is False
    assert prof["profile"]["sec_ticker"] == "NOVA" and prof["profile"]["origine_collegamento"] == "nome"
    assert [v["tipo"] for v in prof["profile"]["varianti"]] == ["annuale", "trimestrale"]


def test_ambiguo_non_crea_profilo(store, pref):
    r = fa.attiva(store, "KORE.DE", proponi_fn=_proposta("ambiguo", KORE, KORE2), catalogo_fn=CAT_USA, pref_path=pref)
    assert r["esito"] == "da_confermare" and store.get_profile("KORE.DE") is None


def test_conferma_cik_scelto(store, pref):
    r = fa.attiva(store, "KORE.DE", cik="9990012", proponi_fn=_proposta("ambiguo", KORE, KORE2),
                  catalogo_fn=CAT_USA, pref_path=pref)
    assert r["esito"] == "attivato"
    prof = store.get_profile("KORE.DE")["profile"]
    assert prof["origine_collegamento"] == "confermato_utente" and prof["cik"] == "0009990012"


def test_cik_non_tra_i_candidati(store, pref):
    r = fa.attiva(store, "KORE.DE", cik="1", proponi_fn=_proposta("ambiguo", KORE, KORE2),
                  catalogo_fn=CAT_USA, pref_path=pref)
    assert r["esito"] == "errore" and store.get_profile("KORE.DE") is None


def test_nessuna_forma_supportata_e_senza_fonte(store, pref, monkeypatch):
    # Fase B: senza forme SEC si prova l'ESEF (qui senza candidati, nessuna rete).
    from bellomberg.market_data import filing_identita
    monkeypatch.setattr(filing_identita, "proponi_esef",
                        lambda t, n, rifiutati=frozenset(): {"stato": "nessuno", "candidati": [], "motivo": "nessuna entita'"})
    r = fa.attiva(store, "NOVA.DE", proponi_fn=_proposta("univoco", NOVA),
                  catalogo_fn=lambda t, cik=None: {"stato": "ok", "motivi": [], "documenti": [{"form": "8-K"}]},
                  pref_path=pref)
    assert r["esito"] == "senza_fonte" and store.get_profile("NOVA.DE") is None


def test_catalogo_in_errore_e_errore(store, pref):
    r = fa.attiva(store, "NOVA.DE", proponi_fn=_proposta("univoco", NOVA),
                  catalogo_fn=lambda t, cik=None: {"stato": "errore", "motivi": ["HTTP 503"], "documenti": []},
                  pref_path=pref)
    assert r["esito"] == "errore" and "503" in r["motivo"]


def test_blocco_un_errore_non_ferma_gli_altri(store, pref):
    def proponi(ticker, nome=None, rifiutati=frozenset()):
        if ticker == "ROTTO.DE":
            return {"ticker": ticker, "nome": None, "sec": {"stato": "errore", "candidati": [],
                                                            "motivo": "ContattoMancante: SEC_CONTACT_EMAIL assente"}}
        if ticker == "ESPLODE.DE":
            raise OSError("rete giu")
        return _proposta("univoco", NOVA)(ticker)

    fp.imposta_escluso("FONDO.FRA", True, path=pref)
    out = fa.attiva_mancanti(store, ["NOVA.DE", "ROTTO.DE", "ESPLODE.DE", "FONDO.FRA", "NOVA.DE"],
                             proponi_fn=proponi, catalogo_fn=CAT_USA, pref_path=pref)
    assert out["attivati"] == ["NOVA.DE"] and out["esclusi"] == ["FONDO.FRA"]
    assert [e["ticker"] for e in out["errori"]] == ["ROTTO.DE", "ESPLODE.DE"]
    assert "SEC_CONTACT_EMAIL" in out["errori"][0]["motivo"] and "rete giu" in out["errori"][1]["motivo"]
    assert out["senza_fonte"] == [] and out["da_confermare"] == []


def test_gia_attivo_non_riscritto(store, pref):
    fa.attiva(store, "NOVA.DE", proponi_fn=_proposta("univoco", NOVA), catalogo_fn=CAT_USA, pref_path=pref)
    out = fa.attiva_mancanti(store, ["NOVA.DE"], proponi_fn=_proposta("univoco", NOVA), catalogo_fn=CAT_USA,
                             pref_path=pref)
    assert out["gia_attivi"] == ["NOVA.DE"] and store.get_profile("NOVA.DE")["version"] == 1


def test_collegamento_rifiutato_non_riproposto(store, pref):
    fp.rifiuta("KORE.DE", "0009990012", path=pref)
    r = fa.attiva(store, "KORE.DE", proponi_fn=_proposta("ambiguo", KORE, KORE2), catalogo_fn=CAT_USA, pref_path=pref)
    assert r["proposta"]["sec"]["candidati"] == [KORE]


def test_preferenze_illeggibili_non_sovrascritte(pref):
    pref.write_text("{rotto")
    with pytest.raises(ValueError):
        fp.imposta_escluso("X", True, path=pref)
    assert pref.read_text() == "{rotto"


def test_preferenze_escludi_e_reincludi(pref):
    fp.imposta_escluso("A.DE", True, path=pref)
    fp.imposta_escluso("A.DE", False, path=pref)
    assert fp.carica(pref)["esclusi"] == []


def _profilo_20f(store, pref):
    fa.attiva(store, "KORE.DE", proponi_fn=_proposta("univoco", KORE),
              catalogo_fn=lambda t, cik=None: {"stato": "ok", "motivi": [], "documenti": [{"form": "20-F"}]},
              pref_path=pref)


def test_completa_6k_aggiunge_la_variante(store, pref):
    _profilo_20f(store, pref)
    cat = {"stato": "ok", "motivi": [], "documenti": [
        {"form": "6-K", "accession": "a2", "filed_date": "2026-08-31"},
        {"form": "6-K", "accession": "a1", "filed_date": "2026-07-29"}]}
    allegati = {"a2": [{"seq": 1, "tipo": "6-K", "descrizione": "6-K", "ixbrl": False, "url": "https://www.sec.gov/x/notice.htm"}],
                "a1": [{"seq": 2, "tipo": "EX-99.1", "descrizione": "EX-99.1", "ixbrl": True, "url": "https://www.sec.gov/x/kore-20260630.htm"}]}
    scaricati = []
    r = fa.completa_6k(store, "KORE.DE", catalogo_fn=lambda t, cik=None: cat,
                       allegati_fn=lambda cik, acc: allegati[acc],
                       scarica_fn=lambda url: scaricati.append(url) or "Interim report for the six months ended 30 June 2026",
                       oggi="2026-10-03")
    assert r["esito"] == "aggiunto" and r["tipo"] == "semestrale" and r["profilo_versione"] == 2
    assert scaricati == ["https://www.sec.gov/x/kore-20260630.htm"]
    prof = store.get_profile("KORE.DE")
    assert [v["tipo"] for v in prof["profile"]["varianti"]] == ["annuale", "semestrale"]
    assert prof["interval_hours"] == 24


def test_completa_6k_senza_relazioni(store, pref):
    _profilo_20f(store, pref)
    cat = {"stato": "ok", "motivi": [], "documenti": [{"form": "6-K", "accession": "a1", "filed_date": "2026-07-29"}]}
    r = fa.completa_6k(store, "KORE.DE", catalogo_fn=lambda t, cik=None: cat,
                       allegati_fn=lambda cik, acc: [{"seq": 1, "tipo": "6-K", "descrizione": "6-K", "ixbrl": False,
                                                      "url": "https://www.sec.gov/x/q2results.htm"}],
                       scarica_fn=lambda url: "Second quarter production update.", oggi="2026-10-03")
    assert r["esito"] == "nessun_allegato" and store.get_profile("KORE.DE")["version"] == 1


def test_completa_6k_non_applicabile_ai_10q(store, pref):
    fa.attiva(store, "NOVA.DE", proponi_fn=_proposta("univoco", NOVA), catalogo_fn=CAT_USA, pref_path=pref)
    assert fa.completa_6k(store, "NOVA.DE", catalogo_fn=CAT_USA)["esito"] == "non_applicabile"


def test_attiva_non_sovrascrive_un_profilo_esistente(store, pref):
    # Review finale: un profilo scritto a mano non viene rimpiazzato dal modello standard.
    manuale = {"ticker": "NOVA.DE", "emittente_id": "CIK:9990001", "lingua": "en", "tipo": "annuale",
               "perimetro": "consolidato", "verifica": {"lingua": "x", "tipo": "x", "perimetro": "x"},
               "sezioni": {"r": {"inizio": "a", "fine": "b"}}}
    store.set_profile("NOVA.DE", manuale, interval_hours=168, qualitative_enabled=True)
    r = fa.attiva(store, "NOVA.DE", proponi_fn=_proposta("univoco", NOVA), catalogo_fn=CAT_USA, pref_path=pref)
    assert r["esito"] == "gia_attivo"
    prof = store.get_profile("NOVA.DE")
    assert prof["version"] == 1 and prof["qualitative_enabled"] is True


def test_completa_6k_un_errore_non_ferma_gli_altri_e_si_dichiara(store, pref):
    _profilo_20f(store, pref)
    cat = {"stato": "ok", "motivi": [], "documenti": [
        {"form": "6-K", "accession": "a2", "filed_date": "2026-08-31"},
        {"form": "6-K", "accession": "a1", "filed_date": "2026-07-29"}]}

    def allegati(cik, acc):
        if acc == "a2":
            raise OSError("indice irraggiungibile")
        return [{"seq": 1, "tipo": "EX-99.1", "descrizione": "EX-99.1", "ixbrl": True, "url": "https://www.sec.gov/x/k.htm"}]

    r = fa.completa_6k(store, "KORE.DE", catalogo_fn=lambda t, cik=None: cat, allegati_fn=allegati,
                       scarica_fn=lambda url: "six months ended 30 June 2026", oggi="2026-10-03")
    assert r["esito"] == "aggiunto"
    r2 = fa.completa_6k(store, "KORE.DE", catalogo_fn=lambda t, cik=None: cat,
                        allegati_fn=lambda c, a: (_ for _ in ()).throw(OSError("rete giu")), oggi="2026-10-03")
    assert r2["esito"] == "non_applicabile"  # ormai ha la variante


def test_completa_6k_tutto_in_errore_e_errore_non_nessun_allegato(store, pref):
    _profilo_20f(store, pref)
    cat = {"stato": "ok", "motivi": [], "documenti": [{"form": "6-K", "accession": "a1", "filed_date": "2026-07-29"}]}
    r = fa.completa_6k(store, "KORE.DE", catalogo_fn=lambda t, cik=None: cat,
                       allegati_fn=lambda c, a: (_ for _ in ()).throw(OSError("rete giu")), oggi="2026-10-03")
    assert r["esito"] == "errore" and "rete giu" in r["motivo"]
