"""Impronta ESEF e controllo leggero (fase B, task 7). Indice finto, nessuna rete."""
import sqlite3

import pytest

from bellomberg.market_data.filing_impronta import impronta_depositi, stessi_depositi
from bellomberg.market_data.filing_profili_auto import profilo_esef
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_esef_sintetici import LEI_NOVA, riga_indice
from tests.test_filing_impronta import _scaduto

OGGI = "2026-10-03"
PROFILO = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en")


def _indice(righe, origine="rete"):
    return lambda lei: {"righe": righe, "origine": origine, "motivo": None}


BASE = [riga_indice(1, 2024, lingua="it"), riga_indice(2, 2024), riga_indice(4, 2025)]


def test_impronta_esef_con_le_regole_della_pipeline():
    imp = impronta_depositi(PROFILO, indice_fn=_indice(BASE), oggi=OGGI)
    assert imp == {"fonte": "esef", "varianti": {"annuale": {"relazione": "4,2|ultimo=2025-12-31", "esaminato_fino_a": "4"}}}


def test_nuovo_deposito_cambia_l_impronta():
    a = impronta_depositi(PROFILO, indice_fn=_indice(BASE), oggi=OGGI)
    b = impronta_depositi(PROFILO, indice_fn=_indice(BASE + [riga_indice(7, 2025, aggiunto="2026-09-01 10:00:00")]), oggi=OGGI)
    c = impronta_depositi(PROFILO, indice_fn=_indice(BASE + [riga_indice(8, 2024, lingua="it", aggiunto="2026-09-01 10:00:00")]), oggi=OGGI)
    assert not stessi_depositi(a, b)
    assert stessi_depositi(a, c)  # un ricaricamento nell'altra lingua non conta


def test_cache_scaduta_o_indice_illeggibile_sollevano():
    with pytest.raises(ValueError):
        impronta_depositi(PROFILO, indice_fn=_indice(BASE, origine="cache_scaduta"), oggi=OGGI)

    def giu(lei):
        raise RuntimeError("rete giu'")

    with pytest.raises(Exception):
        impronta_depositi(PROFILO, indice_fn=giu, oggi=OGGI)


def test_fonti_diverse_non_sono_mai_gli_stessi_depositi():
    esef = {"fonte": "esef", "varianti": {"annuale": {"relazione": "4", "esaminato_fino_a": "4"}}}
    sec = {"fonte": "sec", "varianti": {"annuale": {"relazione": "4", "esaminato_fino_a": "4"}}}
    assert not stessi_depositi(esef, sec) and stessi_depositi(esef, dict(esef))


def test_profilo_esef_manuale_senza_controllo_leggero():
    manuale = {k: v for k, v in PROFILO.items() if k != "esef_modo"}
    assert impronta_depositi(manuale, indice_fn=lambda lei: pytest.fail("indice non atteso")) is None


def test_run_programmato_esef_senza_depositi_nuovi_e_un_controllo(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    store.set_profile("NOVA.MI", PROFILO, interval_hours=24)
    chiamate, righe = [], list(BASE)

    def pipeline(profilo, archivio):
        chiamate.append(profilo["ticker"])
        return {"stato": "ok", "motivi": [], "confronto_corrente": {"stato": "ok", "cambiamenti": []}}

    svc = FilingService(store, tmp_path / "arch", pipeline=pipeline, indexer=lambda *a: {"status": "skipped"},
                        impronta_fn=lambda p, riferimento=None: impronta_depositi(
                            p, riferimento=riferimento, indice_fn=lambda lei: {"righe": righe, "origine": "rete",
                                                                              "motivo": None}, oggi=OGGI))
    primo = svc.run_programmato("NOVA.MI")
    assert primo["status"] == "ok" and chiamate == ["NOVA.MI"]
    _scaduto(store)
    leggero = svc.run_programmato("NOVA.MI")
    assert leggero["status"] == "skipped" and leggero["reason"] == "controllato, nessun deposito nuovo"
    assert chiamate == ["NOVA.MI"]
    righe.append(riga_indice(9, 2026, aggiunto="2027-04-01 10:00:00"))
    _scaduto(store, finito=True)
    completo = svc.run_programmato("NOVA.MI")
    assert completo["status"] == "ok" and chiamate == ["NOVA.MI", "NOVA.MI"]
