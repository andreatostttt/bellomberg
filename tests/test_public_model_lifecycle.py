"""Public-model lifecycle: contratto ARCHIVIATO (ZR 05/10, Z4).

Dal 03/10 (1326312) la generazione Excel e' archiviata: dispatch('get_valuation') risponde
excel_archived prima di qualunque acquisizione, preparazione, workbook o pubblicazione.
Il ciclo di vita storico (profilo vuoto -> comitato -> preparatore -> workbook -> download/MIME)
e' in quarantena in archive/private/attic/tests_excel_archiviato_20261005/test_public_model_lifecycle_legacy.py, con il
motivo e la via di riattivazione. Censimento: scratchpad/rapporti/Z4_raggiungibilita.md.
"""
import inspect
import socket

import pytest

from bellomberg.agents import chat_tools
from bellomberg.storage import memory_db, valuation_versions
from bellomberg.valuation import dcf_engine, preparation_runtime, preparation_sources, sector_analysis


ARCHIVED = {"ok": False, "status": "archived", "code": "excel_archived"}


def test_public_profile_get_valuation_is_archived_without_network_ai_sources_or_workbook(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Archived get_valuation reached network, sources, AI, storage or the workbook engine")
    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        caller = inspect.currentframe().f_back
        if caller.f_code is getattr(socket, "_fallback_socketpair", lambda: None).__code__:
            return original_connect(sock, address)
        forbidden()
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(sector_analysis, "prepare_sector_analysis", forbidden)
    monkeypatch.setattr(preparation_sources, "collect_preparation_evidence", forbidden)
    monkeypatch.setattr(preparation_runtime, "installation_runtime", forbidden)
    monkeypatch.setattr(dcf_engine, "generate_valuation", forbidden)
    monkeypatch.setattr(memory_db.MemoryDB, "__init__", forbidden)
    monkeypatch.setattr(valuation_versions.ValuationVersions, "publish", forbidden)
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path)
    for kwargs in ({}, {"valuation_preparer": forbidden},
                   {"valuation_preparer": forbidden, "prepared_bundle": {"case": {"ticker": "SYNTH-EXT"}}}):
        result = chat_tools.dispatch("get_valuation", {"ticker": "SYNTH-EXT"},
                                     caller="committee-orchestrator", **kwargs)
        assert {key: result[key] for key in ARCHIVED} == ARCHIVED
        assert result["error"].startswith("Generazione Excel archiviata")
        # Nessun numero e nessuna pubblicazione: il payload non finge un modello.
        assert not {"valuation_usability", "path", "generation_id", "fair_value_base",
                    "model_publication", "_thesis_saved"} & set(result)
    assert list(tmp_path.iterdir()) == []
