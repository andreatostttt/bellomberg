"""Correzioni dalla revisione finale della fase B (2026-10-03). Dati sintetici, nessuna rete."""
import json
import sqlite3

import pytest
import requests

import bellomberg.storage.negozi_privati as np_
from bellomberg.market_data import esef, filing_attivazione as fa, filing_identita, filing_pipeline
from bellomberg.market_data.filing_impronta import impronta_depositi, stessi_depositi
from bellomberg.market_data.filing_profili_auto import profilo_esef
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_esef_sintetici import LEI_KORE, LEI_NOVA, blocchi_nova, json_nova, riga_indice, xbrl_json
from tests.filing_sec_sintetici import CIK_NOVA
from tests.test_filing_pipeline import rete

PROFILO = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en")


def _repo(monkeypatch, tmp_path, entita):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    monkeypatch.setattr(np_, "PERCORSO_LEI", str(tmp_path / "lei.json"))

    class R:
        ok, status_code = True, 200

        def json(self):
            return {"data": [{"id": lei, "attributes": {"name": n, "identifier": lei}} for n, lei in entita]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: R())


# --- 1. nome «identico» solo a meno della forma giuridica ------------------------------------

@pytest.mark.parametrize("portafoglio, repository", [
    ("Nova Holding S.p.A.", "NOVA S.P.A."),
    ("Nova Group S.p.A.", "NOVA S.P.A."),
    ("Nova S.p.A.", "NOVA"),
])
def test_holding_gruppo_o_forma_mancante_non_sono_lo_stesso_nome(monkeypatch, tmp_path, portafoglio, repository):
    _repo(monkeypatch, tmp_path, [(repository, LEI_NOVA)])
    r = esef.candidati_lei("NOVA.MI", portafoglio, ritmo=lambda: None)
    assert r["stato"] == "ambiguo" and r["candidati"][0]["origine"] == "nome_simile"


@pytest.mark.parametrize("portafoglio, repository", [
    ("Nova S.p.A.", "NOVA S.P.A."), ("Nova S.p.A.", "NOVA - SPA"), ("Nova S.p.A.", "NOVA - SOCIETA' PER AZIONI"),
    ("Nova Group S.p.A.", "NOVA GROUP SPA"), ("Kore AG", "Kore Aktiengesellschaft"), ("Kore N.V.", "KORE NV"),
])
def test_solo_le_grafie_della_forma_giuridica_si_equivalgono(monkeypatch, tmp_path, portafoglio, repository):
    _repo(monkeypatch, tmp_path, [(repository, LEI_NOVA)])
    assert esef.candidati_lei("NOVA.MI", portafoglio, ritmo=lambda: None)["stato"] == "univoco"


# --- 6. pagina di risultati piena: un omonimo puo' stare oltre ---------------------------------

def test_pagina_piena_non_e_mai_univoca(monkeypatch, tmp_path):
    altre = [(f"NOVA ALTRA {i} S.P.A.", f"999900NOVA00000000{i:02d}") for i in range(2, 11)]
    _repo(monkeypatch, tmp_path, [("NOVA S.P.A.", LEI_NOVA)] + altre)
    r = esef.candidati_lei("NOVA.MI", "Nova S.p.A.", ritmo=lambda: None)
    assert r["stato"] == "ambiguo" and [c["lei"] for c in r["candidati"]] == [LEI_NOVA]
    assert "prima pagina" in r["motivo"]


# --- 2. titolo USA: niente ripiego ESEF -------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


def test_titolo_usa_senza_forme_non_ripiega_sull_esef(store, tmp_path, monkeypatch):
    monkeypatch.setattr(filing_identita, "proponi_esef", lambda *a, **k: pytest.fail("ESEF non atteso per un titolo USA"))

    def proponi(t, rifiutati=frozenset()):
        return {"ticker": t, "nome": "Nova Inc.", "preferita": "sec",
                "sec": {"stato": "univoco", "motivo": "ticker", "candidati": [
                    {"cik": CIK_NOVA, "ticker": "NOVA", "nome": "Nova Inc.", "origine": "ticker"}]}}

    r = fa.attiva(store, "NOVA", proponi_fn=proponi, pref_path=tmp_path / "p.json",
                  catalogo_fn=lambda t, cik=None: {"stato": "ok", "documenti": [{"form": "8-K"}]})
    assert r["esito"] == "senza_fonte" and store.get_profile("NOVA") is None


# --- 3. repository fermo: confronto storico, mai corrente --------------------------------------

@pytest.fixture
def esef_finto(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    monkeypatch.setattr(esef, "attendi_esef", lambda **k: None)

    def imposta(righe, pagine):
        monkeypatch.setattr(esef, "indice_depositi", lambda lei, **k: {"righe": righe, "origine": "rete", "motivo": None})
        return rete(monkeypatch, pagine)
    return imposta


def test_repository_fermo_e_un_confronto_storico(esef_finto, tmp_path):
    righe = [riga_indice(1, 2021), riga_indice(2, 2022)]
    esef_finto(righe, {righe[0]["json_url"]: xbrl_json(2021, blocchi=blocchi_nova(2024)),
                       righe[1]["json_url"]: xbrl_json(2022, blocchi=blocchi_nova(2025))})
    out = filing_pipeline.esegui_profilo(PROFILO, archivio=tmp_path / "a", oggi="2026-10-03")
    assert out["confronto_corrente"] is None and out["confronto_storico"]
    assert out["coppia"]["ambito"] == "storico" and out["stato"] == "parziale"


# --- 4. impronta: il repository che diventa «fermo» o un deposito senza JSON fanno il run completo ---

def _ind(righe):
    return lambda lei: {"righe": righe, "origine": "rete", "motivo": None}


def test_impronta_cambia_quando_il_repository_diventa_fermo():
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    prima = impronta_depositi(PROFILO, indice_fn=_ind(righe), oggi="2026-10-03")
    dopo = impronta_depositi(PROFILO, indice_fn=_ind(righe), oggi="2027-09-01")
    assert not stessi_depositi(prima, dopo)


def test_impronta_cambia_con_un_deposito_nuovo_senza_json():
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    a = impronta_depositi(PROFILO, indice_fn=_ind(righe), oggi="2026-10-03")
    b = impronta_depositi(PROFILO, indice_fn=_ind(righe + [riga_indice(6, 2026, json=False)]), oggi="2026-10-03")
    assert not stessi_depositi(a, b)


# --- 5. fattore prosa: solo per il testo ESEF, la SEC resta com'era -----------------------------

def test_prosa_numerica_sec_non_penalizzata():
    from bellomberg.agents import filing_context as fc
    testo = ("Revenue increased 12% to $4.5 billion in 2025, and $450 million of the $2.1 billion term loan "
             "matures in 2026 under covenant 4.2 of the 2023 agreement. ") * 3
    sec = {"tipo": "aggiunto", "dopo": {"sezione": "rischi", "testo": testo, "url": "https://www.sec.gov/x.htm"}}
    assert fc.punteggio(sec) == 3.0


def test_prosa_breve_con_cifre_esef_non_scende_al_minimo():
    from bellomberg.agents import filing_context as fc
    testo = ("The Group is defendant in a tax claim of EUR 12 million and expects a ruling in 2026. ") * 5
    c = {"tipo": "aggiunto", "dopo": {"sezione": "contenziosi e passività potenziali", "testo": testo,
                                      "url": "https://filings.xbrl.org/x.json"}}
    assert fc.punteggio(c) == 2.0


# --- 7. lingua: inglese sempre preferita, anche se il profilo e' nato in italiano --------------

def test_profilo_nato_in_italiano_passa_all_inglese_quando_c_e(esef_finto, tmp_path):
    righe = [riga_indice(1, 2024, lingua="it"), riga_indice(2, 2024), riga_indice(3, 2025, lingua="it"), riga_indice(4, 2025)]
    chiamate = esef_finto(righe, {righe[1]["json_url"]: json_nova(2024), righe[3]["json_url"]: json_nova(2025)})
    out = filing_pipeline.esegui_profilo({**PROFILO, "lingua": "it"}, archivio=tmp_path / "a", oggi="2026-10-03")
    assert out["coppia"]["dopo"]["metadati"]["lingua"] == "en"
    assert set(chiamate) == {righe[1]["json_url"], righe[3]["json_url"]}


# --- 8. SEC solo per nomi simili e ESEF ambiguo: si propone l'ESEF -----------------------------

def test_sec_simile_ed_esef_ambiguo_propone_esef(store, tmp_path):
    sim = {"stato": "ambiguo", "motivo": "nome simile",
           "candidati": [{"cik": CIK_NOVA, "ticker": "NOVC", "nome": "Nova Chile", "origine": "nome_simile"}]}
    amb = {"stato": "ambiguo", "motivo": "due",
           "candidati": [{"lei": LEI_NOVA, "nome": "NOVA", "origine": "nome"}, {"lei": LEI_KORE, "nome": "NOVA", "origine": "nome"}]}
    assert filing_identita.preferita({"sec": sim, "esef": amb}) == "esef"
    assert filing_identita.preferita({"sec": sim, "esef": {"stato": "nessuno"}}) == "sec"

    def proponi(t, rifiutati=frozenset()):
        p = {"ticker": t, "nome": "Nova S.p.A.", "sec": sim, "esef": amb}
        return {**p, "preferita": filing_identita.preferita(p)}

    r = fa.attiva(store, "NOVA.MI", proponi_fn=proponi, pref_path=tmp_path / "p.json",
                  indice_fn=_ind([riga_indice(4, 2025)]))
    assert r["esito"] == "da_confermare" and r["motivo"].startswith("ESEF") and store.get_profile("NOVA.MI") is None


def test_contesto_dichiara_il_repository_fermo(esef_finto, tmp_path):
    from bellomberg.agents import filing_context as fc
    from bellomberg.market_data.filing_service import FilingService
    righe = [riga_indice(1, 2021), riga_indice(2, 2022)]
    esef_finto(righe, {righe[0]["json_url"]: xbrl_json(2021, blocchi=blocchi_nova(2024)),
                       righe[1]["json_url"]: xbrl_json(2022, blocchi=blocchi_nova(2025))})
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    st = FilingStore(path)
    st.set_profile("NOVA.MI", PROFILO, interval_hours=24)
    svc = FilingService(st, tmp_path / "arch", indexer=lambda *a: {"status": "skipped"}, impronta_fn=None)
    svc.run("NOVA.MI", trigger="scheduled")
    riga = next(l for l in fc.committee_filing_context(["NOVA.MI"], db_path=path, pref_path=tmp_path / "p.json")
                .splitlines() if l.startswith("NOVA.MI"))
    assert "confronto storico: repository ESEF fermo all'esercizio FY2022" in riga, riga
    assert "non verificato" not in riga
