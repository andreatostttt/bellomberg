"""Revisione import 04/10/2026 (G2b, R10 d): «Proponi con AI» come lavoro con stato.

Il client dell'app ha un timeout di 30 s; la chiamata al modello puo' durarne di piu' e il server
la paga comunque. POST /filings/{t}/ai-proposal attende al massimo `attesa_ai_s` (default 20 s):
se il lavoro non e' finito risponde {"stato": "in_corso", "job_id", ...} e il risultato si legge
con GET /filings/{t}/ai-proposal/job/{job_id}. Un secondo click durante il lavoro non ne avvia
un altro (nessuna seconda spesa).
"""
import json
import time
from types import SimpleNamespace as NS

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bellomberg.api.filing_routes import create_filing_router
from bellomberg.core import llm_client as lc
from tests.test_filing_proposta_ai_chiamata import RISPOSTA
from tests.test_filing_routes_ai import SHA, URL, env  # noqa: F401


def _client(env, attesa, nome_fn=lambda t: "Nova AG"):
    app = FastAPI()
    app.include_router(create_filing_router(
        lambda: None, service_factory=lambda: env.service, tickers_portafoglio=lambda: ["NOVA.DE"],
        attivazione=NS(completa_6k=lambda store, t, **k: {"esito": "non_applicabile"}),
        proponi_fn=lambda t, **k: {}, avvia_aggiornamento=lambda motivo: None,
        nome_fn=nome_fn, attesa_ai_s=attesa))
    return TestClient(app)


def _lento(env, monkeypatch, secondi):
    def create(**kwargs):
        env.chiamate.append(kwargs)
        time.sleep(secondi)
        return NS(content=[lc.TextBlock(json.dumps(RISPOSTA))],
                  usage=NS(input_tokens=8_000, output_tokens=600, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0031))
    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))


def test_modello_lento_risposta_in_corso_poi_risultato_dal_job(env, monkeypatch):
    # REV_G2b A9: modello da 5 s e attesa 0,2 s, vincolo largo (< 3 s) e attesa del job SEMPRE
    # (anche se il test cade): il thread non deve scrivere nella cache del test dopo.
    _lento(env, monkeypatch, 5.0)
    client = _client(env, 0.2)
    job = None
    try:
        inizio = time.monotonic()
        r = client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL})
        assert r.status_code == 200 and time.monotonic() - inizio < 3
        primo = r.json()
        job = primo.get("job_id")
        assert primo["stato"] == "in_corso" and job and primo["ticker"] == "NOVA.DE"
        doppio = client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()
        assert doppio["stato"] == "in_corso" and doppio["job_id"] == job
    finally:
        if job:
            _attendi(client, job, secondi=30)
    stato = client.get(f"/filings/NOVA.DE/ai-proposal/job/{job}").json()
    assert stato["stato"] == "done" and stato["sha256"] == SHA and stato["job_id"] == job
    assert len(env.chiamate) == 1  # il doppio click non ha pagato una seconda volta


def test_modello_veloce_risposta_sincrona_come_prima(env):
    out = _client(env, 5).post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()
    assert out["stato"] == "done" and out["sha256"] == SHA and len(env.chiamate) == 1


def test_job_sconosciuto_404_e_di_un_altro_titolo_404(env, monkeypatch):
    _lento(env, monkeypatch, 0.5)
    client = _client(env, 0.05)
    job = client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()["job_id"]
    assert client.get("/filings/NOVA.DE/ai-proposal/job/inesistente").status_code == 404
    assert client.get(f"/filings/KORE.MI/ai-proposal/job/{job}").status_code == 404
    _attendi(client, job)  # il thread non deve sopravvivere al test (scriverebbe nel tmp del test dopo)


def _attendi(client, job, secondi=10):
    fine = time.monotonic() + secondi
    while client.get(f"/filings/NOVA.DE/ai-proposal/job/{job}").json()["stato"] == "in_corso":
        assert time.monotonic() < fine, "job AI ancora in corso"
        time.sleep(0.05)


def test_documento_non_scaricabile_errore_dichiarato_nel_job(env, monkeypatch):
    client = _client(env, 5)
    r = client.post("/filings/NOVA.DE/ai-proposal", json={"url": "https://ir.nova.example/assente.pdf"})
    assert r.status_code == 422 and "404" in r.json()["detail"]
    assert env.chiamate == []


def test_a7_click_diverso_sullo_stesso_titolo_e_un_altro_job(env, monkeypatch):
    _lento(env, monkeypatch, 1.0)
    monkeypatch.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    client = _client(env, 0.05)
    a = client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()["job_id"]
    b = client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL, "riprova": True}).json()["job_id"]
    try:
        assert a != b
    finally:
        _attendi(client, a, 30)
        _attendi(client, b, 30)


def test_a7_job_oltre_la_scadenza_dichiarato_scaduto(env, monkeypatch):
    import bellomberg.api.filing_routes as fr
    _lento(env, monkeypatch, 1.5)
    monkeypatch.setattr(fr, "SCADENZA_JOB_AI_S", 0.3)
    client = _client(env, 0.05)
    job = client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()["job_id"]
    time.sleep(0.5)
    stato = client.get(f"/filings/NOVA.DE/ai-proposal/job/{job}").json()
    assert stato["stato"] == "error" and stato["scaduto"] is True and stato["riprovabile"] is True
    time.sleep(2)  # il thread finisce dentro il test: l'esito resta in cache


def test_a7_404_cita_il_riavvio(env):
    r = _client(env, 1).get("/filings/NOVA.DE/ai-proposal/job/ai-inesistente")
    assert r.status_code == 404 and ("riavviato" in r.json()["detail"] or "restarted" in r.json()["detail"])


def test_b1_nome_sconosciuto_mai_il_ticker(env):
    out = _client(env, 5, nome_fn=lambda t: (None, "Yahoo senza nome")).post(
        "/filings/NOVA.DE/ai-proposal", json={"url": URL}).json()
    prompt = env.chiamate[0]["messages"][0]["content"]
    assert "(nome non disponibile)" in prompt and "emittente NOVA.DE" not in prompt
    assert any("Yahoo senza nome" in a for a in out["avvisi"])
