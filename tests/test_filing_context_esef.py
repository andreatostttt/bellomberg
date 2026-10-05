"""Contesto del Consigliere con un titolo ESEF (fase B, task 8): run completo con la pipeline vera."""
import sqlite3

import pytest

from bellomberg.agents import filing_context as fc
from bellomberg.market_data import esef
from bellomberg.market_data.filing_profili_auto import profilo_esef
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_esef_sintetici import LEI_NOVA, json_nova, riga_indice
from tests.test_filing_pipeline import rete


@pytest.fixture
def archivio_esef(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    monkeypatch.setattr(esef, "attendi_esef", lambda **k: None)
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    monkeypatch.setattr(esef, "indice_depositi", lambda lei, **k: {"righe": righe, "origine": "rete", "motivo": None})
    rete(monkeypatch, {righe[0]["json_url"]: json_nova(2024), righe[1]["json_url"]: json_nova(2025)})
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    store.set_profile("NOVA.MI", profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en"),
                      interval_hours=24)
    svc = FilingService(store, tmp_path / "arch", indexer=lambda *a: {"status": "skipped"}, impronta_fn=None)
    run = svc.run("NOVA.MI", trigger="scheduled")
    return path, run


def test_riga_esef_nel_contesto_del_comitato(archivio_esef, tmp_path):
    path, run = archivio_esef
    assert run["status"] == "ok", run["reason"]
    testo = fc.committee_filing_context(["NOVA.MI"], db_path=path, pref_path=tmp_path / "pref.json")
    riga = next(l for l in testo.splitlines() if l.startswith("NOVA.MI"))
    assert f"ESEF LEI {LEI_NOVA}" in riga and "2025" in riga, riga
    assert "ricavi +15,0%" in testo, testo
    cambi = [l for l in testo.splitlines() if l[:1] in "+~−↔"]
    assert cambi and "rischi finanziari" in cambi[0], testo  # rischi in testa per punteggio
    assert "[C" in cambi[0] and "-dopo]" in cambi[0]
    assert testo.splitlines()[-1].startswith("TRONCAMENTI:")


def test_get_filing_changes_esef(archivio_esef):
    path, _ = archivio_esef
    v = fc.get_filing_changes("NOVA.MI", db_path=path, ordine="punteggio")
    assert v["status"] == "ok" and v["changes_total"] >= 3
    assert "rischi finanziari" in str(v["changes"][0])
