"""Documented tool contract through real adapters, temporary SQLite, F17 and local MIME.

The SYNTH-EXT fixture is invented and flat: this certifies wiring, not issuer economics.
No provider/LLM/SMTP connection is permitted. No real issuer is certified by this fixture.
"""
from copy import deepcopy
from datetime import datetime
from email import policy
from email.parser import BytesParser
from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest

from test_sector_operating_drivers import DAY, bundle_for
from test_sector_valuation_api import endpoint
from test_valuation_snapshot_persistence import db


@pytest.fixture
def contract_tools(tmp_path, monkeypatch, db):
    from bellomberg.agents import chat_tools, consigliere_multi
    from bellomberg.reporting import email_sender
    from bellomberg.storage import memory_db
    from bellomberg.valuation import dcf_engine

    def forbidden(*args, **kwargs):
        pytest.fail("Documented contract test attempted network or legacy calculation")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(dcf_engine, "_generate_valuation_legacy", forbidden)
    monkeypatch.setattr(dcf_engine, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(consigliere_multi, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(consigliere_multi, "MODELS_DIR", tmp_path / "unused-models")
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: db)
    monkeypatch.setattr(email_sender, "EMAIL_FROM", "synthetic@example.invalid")
    monkeypatch.setattr(email_sender, "EMAIL_TO", "receiver@example.invalid")
    monkeypatch.setattr(email_sender, "EMAIL_PASSWORD", "synthetic-test-only")
    return chat_tools, consigliere_multi, email_sender


def _call(chat_tools, *, bundle=None, extra=None):
    source = bundle_for(profile="manufacturing") if bundle is None else bundle
    providers = {"profile": lambda *a, **k: deepcopy(source["case"]["sources"]["profile"])}
    args = {"ticker": "SYNTH-EXT", "method_records": deepcopy(source["case"]["records"]),
            "analysis_context": deepcopy(source["analysis_context"]), **(extra or {})}
    return args, chat_tools.dispatch("get_valuation", args, sector_providers=providers, as_of=DAY)["data"]


def _publication_store(db, tmp_path, monkeypatch):
    from datetime import timezone
    from bellomberg.storage import valuation_versions as module
    with db._conn() as conn:
        module.ensure_schema(conn)
    real = module.ValuationVersions
    monkeypatch.setattr(module, "ValuationVersions", lambda db, *, roots: real(db, roots=roots,
        clock=lambda: datetime.fromisoformat(DAY).replace(tzinfo=timezone.utc)))
    return real(db, roots=[tmp_path], clock=lambda: datetime.fromisoformat(DAY).replace(tzinfo=timezone.utc))




# ---------------------------------------------------------------------------------------------
# Contratto ATTUALE (ZR 05/10, Z1). I 13 test del ramo get_valuation documentato (schema/adapter,
# preparatore, pubblicazione/lock, tesi, parametri legacy, catena SQLite->F17->workbook->MIME, riuso)
# sono in archive/private/attic/tests_excel_archiviato_20261005/test_documented_dcf_contract_legacy.py: dal commit 1326312
# il dispatch risponde excel_archived prima di quel ramo e nessun percorso vivo lo raggiunge.
# Qui: le stesse forme di chiamata ricevono il contratto dichiarato e non toccano niente del ramo.
# Le fixture/helper sopra (contract_tools, _call, _publication_store) restano: altri file le importano.
# ---------------------------------------------------------------------------------------------
from _contratto_excel_archiviato import blinda_ramo_archiviato, file_in, spia_chiamante, verifica_archiviato

_TABELLE_VALUTAZIONE = ("valuation_snapshots", "valuation_theses", "valuation_snapshot_links")


@pytest.mark.parametrize("variante", ["solo_ticker", "record_e_contesto", "bundle_e_preparatore",
                                      "parametri_legacy", "riuso_bundle"])
def test_get_valuation_documented_calls_meet_the_archived_contract(contract_tools, db, tmp_path, monkeypatch,
                                                                   variante):
    chat_tools, _, _ = contract_tools
    bundle = bundle_for(profile="manufacturing")  # costruito PRIMA di blindare il ramo
    usati = []
    args = {"ticker": "SYNTH-EXT"}
    kwargs = {"sector_providers": {"profile": spia_chiamante(usati, "provider profile"),
                                   "method_inputs": spia_chiamante(usati, "provider method_inputs")},
              "as_of": DAY}
    if variante == "record_e_contesto":
        args.update(method_records=deepcopy(bundle["case"]["records"]),
                    analysis_context=deepcopy(bundle["analysis_context"]))
    elif variante == "bundle_e_preparatore":
        kwargs.update(prepared_bundle=bundle, valuation_preparer=spia_chiamante(usati, "preparatore"))
    elif variante == "parametri_legacy":
        args.update(variant_view="Legacy estimate", growth_path=[.1, .1], peers=["SYNTH-PEER"],
                    scenarios={"base": {"revenue_growth": [.1, .1]}})
    elif variante == "riuso_bundle":
        kwargs = {"prepared_bundle": bundle, "as_of": DAY}

    def conteggi():
        with db._conn() as connection:
            presenti = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {t: connection.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                    for t in _TABELLE_VALUTAZIONE if t in presenti}

    prima_db, prima_file = conteggi(), file_in(tmp_path)
    assert set(prima_db) == set(_TABELLE_VALUTAZIONE), prima_db  # misura non vuota: le tabelle esistono
    chiamate = blinda_ramo_archiviato(monkeypatch)
    risposta = chat_tools.dispatch("get_valuation", args, **kwargs)
    verifica_archiviato(risposta, chiamate + usati, cartella=tmp_path, prima=prima_file)
    assert conteggi() == prima_db, "il contratto archiviato non registra tesi, snapshot o collegamenti"
