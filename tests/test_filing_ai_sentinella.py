"""Fase C, Task 6: nessuna chiamata AI fuori dal pulsante «Proponi con AI» (POST ai-proposal).

Sentinella sul client OpenRouter e sulla funzione di chiamata: ogni altro percorso del filing
(panoramica, proposta SEC/ESEF, attivazione, refresh con esecuzione, stima, accettazione, run
programmato, aggiornamento del backend, contesto del Consigliere) non deve toccarli.
"""
import ast
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bellomberg.api.filing_routes import create_filing_router
from bellomberg.core import llm_client as lc
from bellomberg.core import llm_usage as asum   # helper neutri (G3, 04/10)
from bellomberg.market_data import filing_proposta_ai as fp
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_pdf_sintetici import semestrale_nova
from tests.test_filing_proposta_ai_chiamata import RISPOSTA

URL = "https://ir.nova.example/reports/h1-2026.pdf"
PDF = semestrale_nova()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("bellomberg.storage.filing_preferenze._path", lambda p=None: tmp_path / "p.json")
    chiamate = NS(n=0, armata=True)

    def create(**kwargs):
        if chiamate.armata:
            pytest.fail("chiamata AI fuori dal pulsante")
        chiamate.n += 1
        return NS(content=[lc.TextBlock(json.dumps(RISPOSTA))],
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0))

    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    monkeypatch.setattr(lc, "modello", lambda funzione, *a: "nova/flash")
    monkeypatch.setattr(lc, "chiave_api", lambda: "chiave-di-prova")
    monkeypatch.setattr(asum, "MemoryDB", lambda: NS(save_llm_usage=lambda memo_id, log: None))
    monkeypatch.setattr(fp, "_fx", lambda: (1.0, "live"))
    monkeypatch.setattr(fp, "_metadati_openrouter", lambda m: {"pricing": {"prompt": "0", "completion": "0"}})

    def scarica(url, dest_dir, timeout=30, *, host_consentiti=None, public_only=False, max_bytes=None):
        digest = hashlib.sha256(PDF).hexdigest()
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        dest = Path(dest_dir) / f"h1-{digest}.pdf"
        dest.write_bytes(PDF)
        return {"stato": "ok", "url": url, "url_finale": url, "path": str(dest), "sha256": digest,
                "bytes": len(PDF)}

    monkeypatch.setattr("bellomberg.market_data.lettore_trimestrali.scarica_documento", scarica)
    db = tmp_path / "f.sqlite"
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    # Pipeline vera: il profilo IR salvato si esegue come un profilo IR qualsiasi.
    service = FilingService(FilingStore(db), tmp_path / "archive", impronta_fn=None,
                            indexer=lambda *a: {"status": "skipped"})
    app = FastAPI()
    app.include_router(create_filing_router(
        lambda: None, service_factory=lambda: service, tickers_portafoglio=lambda: ["NOVA.DE"],
        proponi_fn=lambda t, **k: {"ticker": t, "nome": "Nova AG",
                                   "sec": {"stato": "nessuno", "candidati": [], "motivo": ""}},
        attivazione=NS(completa_6k=lambda store, t, **k: {"esito": "non_applicabile"},
                       attiva=lambda store, t, **k: {"ticker": t, "esito": "senza_fonte", "motivo": "x"},
                       attiva_mancanti=lambda store, ts, **k: {"attivati": [], "da_confermare": [],
                                                              "senza_fonte": ts, "esclusi": [], "gia_attivi": [],
                                                              "errori": []}),
        contesto_fn=lambda tickers, **k: {"righe": {}, "caratteri": 0, "budget": 14_000, "omessi_totali": 0,
                                          "schede": [{"ticker": t, "gruppo": 3, "stato": "x", "fresco": None}
                                                     for t in tickers]},
        ultima_run_fn=lambda: None, nome_fn=lambda t: "Nova AG"))
    return NS(client=TestClient(app), service=service, chiamate=chiamate, tmp=tmp_path)


def test_solo_il_pulsante_chiama_il_modello(env):
    c = env.client
    assert c.get("/filings").status_code == 200
    assert c.get("/filings/NOVA.DE/proposal").status_code == 200
    assert c.post("/filings/NOVA.DE/activate", json={}).status_code == 200
    assert c.post("/filings/activate-missing").status_code == 200
    assert c.get("/filings/NOVA.DE/ai-estimate", params={"url": URL}).json()["stato"] == "ok"
    assert env.chiamate.n == 0

    env.chiamate.armata = False  # il pulsante: una chiamata
    proposta = c.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()
    assert proposta["stato"] == "done" and env.chiamate.n == 1
    env.chiamate.armata = True

    assert c.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()["cached"] is True
    assert c.get("/filings/NOVA.DE/ai-estimate", params={"url": URL}).json()["cache"] is True
    assert c.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": proposta["sha256"]}).status_code == 200
    # Primo confronto (run_programmato in background), refresh manuale ed esecuzione: pipeline vera.
    r = c.post("/filings/NOVA.DE/refresh")
    assert r.status_code in (202, 409)
    runs = env.service.store.list_runs("NOVA.DE")  # run programmato dopo l'accettazione + refresh
    assert runs and all(r["status"] not in ("queued", "running") for r in runs)
    from bellomberg.agents import filing_context as fc
    fc.contesto_dettaglio(["NOVA.DE"], db_path=env.service.store.db_path, novita_dopo=None)
    assert c.get("/filings/NOVA.DE").json()["profile"]["profile"]["origine_collegamento"] == "proposta_ai"
    assert env.chiamate.n == 1


def test_proponi_chiamata_solo_dalla_route_del_pulsante():
    """Statico: l'unico chiamante di filing_proposta_ai.proponi e' la route POST ai-proposal."""
    radice = Path("src/bellomberg")
    chiamanti = []
    for path in radice.rglob("*.py"):
        if path.name == "filing_proposta_ai.py":
            continue
        albero = ast.parse(path.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "proponi"
                    and isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == "fp"):
                chiamanti.append(path.as_posix())
        sorgente = path.read_text(encoding="utf-8")
        if "filing_proposta_ai import proponi" in sorgente or "filing_proposta_ai import *" in sorgente:
            chiamanti.append(path.as_posix() + " (import diretto)")
    assert chiamanti == ["src/bellomberg/api/filing_routes.py"]
