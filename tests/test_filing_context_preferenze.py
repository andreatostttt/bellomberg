"""Revisione G1 (04/10/2026): get_filing_changes rispetta «Scollega» ed «Escludi» come il contesto,
e preferenze illeggibili sono uno stato dichiarato (mai «nessuna esclusione» zitta). Ticker inventati."""
import json
import sqlite3

import pytest

from bellomberg.agents import filing_context as fc
from bellomberg.storage import filing_preferenze as fp
from bellomberg.storage.filing_store import FilingStore, ensure_schema

PROFILO = {"ticker": "ZZLINK.DE", "emittente_id": "CIK:0009990777", "cik": "0009990777", "lingua": "en",
           "tipo": "trimestrale", "perimetro": "consolidato",
           "verifica": {"lingua": "English", "tipo": "quarterly", "perimetro": "consolidated"},
           "sezioni": {"rischi": {"inizio": "Risk Factors", "fine": "Properties"}}}


def _cit(testo):
    return {"url": "https://www.sec.gov/Archives/x.htm", "sha256": "a" * 64, "sezione": "rischi",
            "inizio": 0, "fine": len(testo), "pagine_fisiche": [], "testo": testo}


@pytest.fixture
def pref(tmp_path, monkeypatch):
    """Il file preferenze che il tool legge da solo (DATA_DIR), come in produzione."""
    monkeypatch.setattr("bellomberg.core.paths.DATA_DIR", tmp_path)
    return tmp_path / "filing_preferenze.json"


@pytest.fixture
def archivio(tmp_path):
    path = tmp_path / "a.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    store.set_profile("ZZLINK.DE", PROFILO, interval_hours=24)
    run = store.start_run("ZZLINK.DE")
    store.claim_execution(run["id"])
    result = {"stato": "ok", "variante": "trimestrale",
              "confronto_corrente": {"stato": "ok", "cambiamenti": [
                  {"tipo": "aggiunto", "dopo": _cit("Rischio dell'emittente sbagliato " * 10)}]}}
    store.finish_run(run["id"], status="ok", result=result,
                     judgment={"status": "skipped", "findings": []}, index={"status": "skipped"})
    return path, store


def test_senza_preferenze_il_confronto_arriva(archivio, pref):
    path, _ = archivio
    r = fc.get_filing_changes("ZZLINK.DE", db_path=path)
    assert r["status"] == "ok" and r["changes_shown"] == 1


def test_scollegato_non_restituisce_l_emittente_rifiutato(archivio, pref):
    path, store = archivio
    fp.imposta_scollegato("ZZLINK.DE", store.get_profile("ZZLINK.DE")["version"], path=pref)
    r = fc.get_filing_changes("ZZLINK.DE", db_path=path)
    assert r["status"] != "ok" and "changes" not in r and "Scollega" in r["reason"]
    # anche citando il run per numero
    r = fc.get_filing_changes("ZZLINK.DE", db_path=path, run_id=1)
    assert r["status"] != "ok" and "changes" not in r


def test_escluso_non_restituisce_confronti(archivio, pref):
    path, _ = archivio
    fp.imposta_escluso("ZZLINK.DE", True, path=pref)
    r = fc.get_filing_changes("ZZLINK.DE", db_path=path)
    assert r["status"] != "ok" and "changes" not in r and "escluso" in r["reason"]


def test_preferenze_illeggibili_dichiarate_dal_tool(archivio, pref):
    path, _ = archivio
    pref.write_text("{rotto", encoding="utf-8")
    r = fc.get_filing_changes("ZZLINK.DE", db_path=path)
    assert r["status"] == "non_disponibile" and "changes" not in r
    assert "preferenze filing illeggibili" in r["reason"]


def test_preferenze_illeggibili_dichiarate_nel_contesto(archivio, tmp_path):
    path, _ = archivio
    pref = tmp_path / "p.json"
    pref.write_text(json.dumps({"esclusi": "non una lista", "rifiutati": {}}), encoding="utf-8")
    schede = fc.schede_filing(["ZZLINK.DE"], db_path=path, pref_path=pref)
    assert schede[0]["cambiamenti"] == [] and "preferenze filing illeggibili" in schede[0]["stato"]
    testo = fc.committee_filing_context(["ZZLINK.DE"], db_path=path, pref_path=pref)
    assert "emittente sbagliato" not in testo and "preferenze filing illeggibili" in testo


# --- Seguito revisione (rilievo 1): dopo un ri-collegamento il confronto vecchio non e' evidenza ---

def _ricollega(store):
    store.set_profile("ZZLINK.DE", {**PROFILO, "emittente_id": "CIK:0009990778", "cik": "0009990778"},
                      interval_hours=24)


def test_ricollegato_il_tool_non_serve_il_confronto_dell_emittente_rifiutato(archivio, pref):
    path, store = archivio
    _ricollega(store)
    r = fc.get_filing_changes("ZZLINK.DE", db_path=path)
    assert r["status"] == "non_disponibile" and "changes" not in r
    assert "collegamento precedente" in r["reason"] and "CIK:9990777" in r["reason"]
    assert fc.get_filing_changes("ZZLINK.DE", db_path=path, run_id=1)["status"] == "non_disponibile"


def test_ricollegato_il_contesto_lo_dichiara(archivio, pref):
    path, store = archivio
    _ricollega(store)
    testo = fc.committee_filing_context(["ZZLINK.DE"], db_path=path)
    assert "emittente sbagliato" not in testo and "collegamento precedente" in testo


def test_nuova_versione_dello_stesso_emittente_resta_valida(archivio, pref):
    path, store = archivio
    store.set_profile("ZZLINK.DE", {**PROFILO, "emittente_id": "CIK:9990777"}, interval_hours=24)  # stessa CIK
    assert fc.get_filing_changes("ZZLINK.DE", db_path=path)["status"] == "ok"
