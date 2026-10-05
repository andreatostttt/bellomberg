"""Fase C, Task 5: route ai-estimate, ai-proposal, ai-proposal/accept (nessuna rete, client finto)."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI, HTTPException, Request
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
SHA = hashlib.sha256(PDF).hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("bellomberg.storage.filing_preferenze._path", lambda p=None: tmp_path / "p.json")
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    service = FilingService(FilingStore(path), tmp_path / "archive",
                            pipeline=lambda *a, **k: {"stato": "non_disponibile", "motivi": [],
                                                      "confronto_corrente": None, "confronto_storico": None},
                            indexer=lambda *a: {"status": "skipped"})
    scaricati, tetti = [], []
    documenti = {URL: PDF, "https://ir.nova.example/page.html": b"<html><body>IR</body></html>"}

    def scarica(url, dest_dir, timeout=30, *, host_consentiti=None, public_only=False, max_bytes=None):
        scaricati.append((url, set(host_consentiti or ()), public_only))
        tetti.append(max_bytes)
        if url not in documenti:
            return {"stato": "errore", "url": url, "motivo": "HTTPError: 404"}
        from pathlib import Path
        contenuto = documenti[url]
        digest = hashlib.sha256(contenuto).hexdigest()
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        dest = Path(dest_dir) / f"doc-{digest}{'.pdf' if url.endswith('.pdf') else '.html'}"
        dest.write_bytes(contenuto)
        return {"stato": "ok", "url": url, "url_finale": url, "path": str(dest), "sha256": digest,
                "bytes": len(contenuto)}

    monkeypatch.setattr("bellomberg.market_data.lettore_trimestrali.scarica_documento", scarica)
    usage = []
    monkeypatch.setattr(asum, "MemoryDB", lambda: NS(save_llm_usage=lambda memo_id, log: usage.extend(log)))
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "cost_eur", lambda model, tokens, ttl=None: {
        "cost": 0.002, "status": "ok", "fx_rate": 0.9, "fx_source": "live", "breakdown": {}})
    monkeypatch.setattr(fp, "_fx", lambda: (0.9, "live"))
    monkeypatch.setattr(fp, "_metadati_openrouter", lambda m: {"pricing": {"prompt": "0.0000003",
                                                                          "completion": "0.0000012"}})
    monkeypatch.setattr(lc, "modello", lambda funzione, *a: "nova/flash")
    monkeypatch.setattr(lc, "chiave_api", lambda: "chiave-di-prova")
    chiamate = []

    def create(**kwargs):
        chiamate.append(kwargs)
        return NS(content=[lc.TextBlock(json.dumps(RISPOSTA))],
                  usage=NS(input_tokens=8_000, output_tokens=600, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0031))

    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    avviati = []

    def auth(request: Request):
        if request.headers.get("X-BB-Token") != "test":
            raise HTTPException(401, "sessione richiesta")

    app = FastAPI()
    app.include_router(create_filing_router(
        auth, service_factory=lambda: service, tickers_portafoglio=lambda: ["NOVA.DE"],
        attivazione=NS(completa_6k=lambda store, t, **k: {"esito": "non_applicabile"}),
        proponi_fn=lambda t, **k: {}, avvia_aggiornamento=lambda motivo: avviati.append(motivo),
        nome_fn=lambda t: "Nova AG"))
    client = TestClient(app)
    client.headers.update({"X-BB-Token": "test"})
    return NS(client=client, service=service, chiamate=chiamate, usage=usage, scaricati=scaricati, tetti=tetti,
              avviati=avviati, documenti=documenti)


def _proponi(env):
    r = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL})
    assert r.status_code == 200, r.text
    return r.json()


def test_route_richiedono_sessione(env):
    env.client.headers.pop("X-BB-Token")
    assert env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL}).status_code == 401
    assert env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).status_code == 401
    assert env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA}).status_code == 401


def test_stima_scarica_misura_e_non_chiama_il_modello(env):
    r = env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["stato"] == "ok" and out["sha256"] == SHA and out["cache"] is False
    assert out["pagine"] == 7 and out["caratteri_input"] > 0
    assert out["token_input_stimati"] > 0 and out["costo_max_eur"] > 0 and out["modello"] == "nova/flash"
    assert env.chiamate == []
    assert env.scaricati == [(URL, {"ir.nova.example"}, True)]


def test_fase_f_download_ai_con_tetto_pdf_e_cartella_ripulita(env):
    import os
    from bellomberg.market_data import download_sicuro
    cartella = env.service.archive_root / "ai"
    cartella.mkdir(parents=True)
    vecchio = cartella / "vecchio.pdf"
    vecchio.write_bytes(b"%PDF vecchio")
    os.utime(vecchio, (1, 1))
    assert env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL}).status_code == 200
    assert env.tetti == [download_sicuro.MAX_PDF]
    assert not vecchio.exists() and len(list(cartella.iterdir())) == 1


def test_stima_dopo_la_proposta_costo_zero(env):
    _proponi(env)
    out = env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL}).json()
    assert out["cache"] is True and out["costo_max_eur"] == 0
    assert len(env.chiamate) == 1


def test_stima_modello_non_configurato(env, monkeypatch):
    def mancante(*a):
        raise lc.ConfigurazioneLLMMancante("NEWS_SUMMARY_MODEL")
    monkeypatch.setattr(lc, "modello", mancante)
    out = env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL}).json()
    assert out == {"stato": "not_configured", "variabile": "NEWS_SUMMARY_MODEL"}  # nessun download
    assert env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()["stato"] == "not_configured"


@pytest.mark.parametrize("url", ["ftp://ir.nova.example/a.pdf", "https://user:pw@ir.nova.example/a.pdf",
                                 "https://localhost/a.pdf", "https://10.0.0.1/a.pdf", "non un url", ""])
def test_url_non_valido_422(env, url):
    assert env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": url}).status_code == 422
    assert env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": url}).status_code == 422
    assert env.scaricati == []


def test_documento_non_pdf_o_non_scaricabile_422(env):
    r = env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": "https://ir.nova.example/page.html"})
    assert r.status_code == 422 and "PDF" in r.json()["detail"]
    r = env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": "https://ir.nova.example/assente.pdf"})
    assert r.status_code == 422 and "404" in r.json()["detail"]


def test_proposta_esito_pubblico(env):
    out = _proponi(env)
    assert out["stato"] == "done" and out["sha256"] == SHA and out["salvabile"] is True
    assert {s["nome"] for s in out["verificate"]} == {"prospettive", "rischi"}
    assert env.usage[0]["agent"] == "filing_profile_ai"
    assert env.service.store.get_profile("NOVA.DE") is None  # nulla salvato prima dell'ok


def test_accept_salva_solo_le_verificate_e_ignora_sezioni_del_client(env):
    _proponi(env)
    r = env.client.post("/filings/NOVA.DE/ai-proposal/accept",
                        json={"sha256": SHA, "ir_urls": [URL, "https://ir.nova.example/results"]})
    assert r.status_code == 200, r.text
    profilo = env.service.store.get_profile("NOVA.DE")
    p = profilo["profile"]
    assert set(p["sezioni"]) == {"prospettive", "rischi"}
    assert p["fonti"] == ["ir"] and p["ir_urls"] == [URL, "https://ir.nova.example/results"]
    assert p["origine_collegamento"] == "proposta_ai" and p["proposta_ai"]["sha256"] == SHA
    assert profilo["interval_hours"] == fp.INTERVALLO_ORE and profilo["enabled"]
    assert profilo["qualitative_enabled"] is False
    assert env.avviati == ["activation"]
    assert r.json()["esito"] == "salvato"
    r = env.client.post("/filings/NOVA.DE/ai-proposal/accept",
                        json={"sha256": SHA, "sezioni": {"x": {"inizio": ".*", "fine": ".*"}}})
    assert r.status_code == 422  # campi sconosciuti rifiutati


def test_accept_senza_proposta_404_e_sha_non_valido_422(env):
    assert env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": "a" * 64}).status_code == 404
    assert env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": "../x"}).status_code == 422


def test_accept_profilo_esistente_409_poi_sostituisci(env):
    _proponi(env)
    from bellomberg.market_data.filing_profili_auto import profilo_sec
    esistente = profilo_sec("NOVA.DE", cik="0009990001", sec_ticker="NOVA", nome="Nova AG",
                            origine="manuale", forme={"10-K"})
    env.service.store.set_profile("NOVA.DE", esistente, interval_hours=24)
    r = env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA})
    assert r.status_code == 409
    assert env.service.store.get_profile("NOVA.DE")["profile"]["fonti"] == ["sec"]
    r = env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA, "sostituisci": True})
    assert r.status_code == 200
    assert env.service.store.get_profile("NOVA.DE")["profile"]["fonti"] == ["ir"]


def test_accept_senza_sezioni_verificate_422(env):
    proposta = _proponi(env)
    dati = fp.leggi_proposta(proposta["sha256"])
    dati.update(salvabile=False, profilo=None, verificate=[])
    fp._salva(dati)
    assert env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA}).status_code == 422


@pytest.mark.parametrize("ir_urls", [["ftp://x.example/a.pdf"], "https://x.example", [], ["https://localhost/a"]])
def test_accept_ir_urls_non_validi_422(env, ir_urls):
    _proponi(env)
    r = env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA, "ir_urls": ir_urls})
    assert r.status_code == 422
    assert env.service.store.get_profile("NOVA.DE") is None


def test_err500_invariato():
    import re
    from pathlib import Path
    sorgente = Path("src/bellomberg/api/filing_routes.py").read_text(encoding="utf-8")
    assert not re.search(r"_err500\(", sorgente)


def test_proposta_con_altri_url_verificati_in_locale(env):
    from tests.filing_pdf_sintetici import semestrale_nova as nova
    altro = "https://ir.nova.example/reports/h1-2025.pdf"
    env.documenti[altro] = nova(2025)
    r = env.client.post("/filings/NOVA.DE/ai-proposal",
                        json={"url": URL, "altri_url": [altro, "https://ir.nova.example/page.html",
                                                        "https://ir.nova.example/assente.pdf"]})
    out = r.json()
    assert out["stato"] == "done" and len(env.chiamate) == 1
    stati = {a["url"]: a["stato"] for a in out["altri"]}
    assert stati[altro] == "ok"
    assert stati["https://ir.nova.example/page.html"] == "non_verificato"
    assert stati["https://ir.nova.example/assente.pdf"] == "non_verificato"
    # stessi altri URL: dalla cache; altri URL diversi: riverifica locale, mai una seconda chiamata
    r = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL})
    assert r.json()["cached"] is True and r.json()["altri"] == [] and len(env.chiamate) == 1
    assert env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL, "altri_url": ["x"] * 5}).status_code == 422


def test_revisione_la_pulizia_tiene_tutti_i_pdf_della_richiesta(env):
    import os
    cartella = env.service.archive_root / "ai"
    assert env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL}).status_code == 200
    for f in cartella.iterdir():  # il PDF principale scaricato 40 giorni fa
        os.utime(f, (1, 1))
    r = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL, "altri_url": ["https://ir.nova.example/page.html"]})
    assert r.status_code == 200, r.text  # prima della correzione: il PDF spariva prima di proponi -> 500
