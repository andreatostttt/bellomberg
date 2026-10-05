"""G7 (04/10): checkpoint dello streaming raggruppati e tetto di durata totale dichiarato.

M2: lo streaming scriveva una transazione SQLite (BEGIN IMMEDIATE + commit) per OGNI chunk
SSE; con 4 desk in parallelo sullo stesso journal (non in WAL, timeout 15 s) il rischio era
un lock oltre il timeout -> richiesta «unknown» -> run bloccata. Ora i chunk si scrivono a
gruppi (per numero o per tempo), sempre tutti, e il gruppo pendente si scrive PRIMA della
ricevuta o del guasto: la prova parziale resta intera, la garanzia di ripresa invariata.
B5: i commenti keepalive SSE azzeravano il read-timeout, una call poteva durare per sempre.
Zero rete: trasporto httpx finto, modelli inventati.
"""
import asyncio
import json
import sqlite3
import threading
import time

import httpx
import pytest

from bellomberg.core import llm_client
from bellomberg.core.request_journal import RequestJournal

N_CHUNK = 300


def _journal(tmp_path):
    return RequestJournal(tmp_path / "stream.sqlite", run_id="stream-batch",
        authorization={"source": "offline test"}, authorized_usd=10,
        metadata=lambda model: {"id": model, "context_length": 1_000_000,
            "top_provider": {"max_completion_tokens": 128000},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


def _sse_lungo(body):
    model = body["model"]
    chunks = [{"id": "gen-" + model, "model": model,
               "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}]}
              for _ in range(N_CHUNK)]
    chunks.append({"id": "gen-" + model, "model": model,
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": N_CHUNK, "cost": 0.0001}})
    text = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})


def test_quattro_stream_concorrenti_checkpoint_raggruppati_e_completi(tmp_path, monkeypatch):
    journal = _journal(tmp_path)
    transazioni = []
    lock = threading.Lock()
    for nome in ("checkpoint", "checkpoint_many"):
        originale = getattr(RequestJournal, nome, None)
        if originale is None:
            continue
        def spia(self, *a, _o=originale, **k):
            with lock:
                transazioni.append(a[0])
            return _o(self, *a, **k)
        monkeypatch.setattr(RequestJournal, nome, spia)
    client = llm_client.OpenRouterClient(api_key="offline-test", max_retries=0,
        trasporto=httpx.MockTransport(lambda req: _sse_lungo(json.loads(req.content))))
    esiti, errori = {}, []

    def desk(nome):
        try:
            with llm_client.request_scope(journal, phase="weekly", agent=nome, round_n=1):
                with client.messages.stream(model="acme/desk-" + nome, max_tokens=1000,
                                            messages=[{"role": "user", "content": nome}]) as s:
                    esiti[nome] = s.get_final_message()
        except BaseException as exc:  # pragma: no cover - diagnostica del test
            errori.append(exc)

    threads = [threading.Thread(target=desk, args=(n,)) for n in ("macro", "quant", "options", "crypto")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errori, errori
    assert len(esiti) == 4 and all(m.content[0].text == "x" * N_CHUNK for m in esiti.values())
    summary = journal.summary()
    assert summary["request_count"] == 4 and summary["unknown_requests"] == 0
    with sqlite3.connect(journal.path) as db:
        per_richiesta = db.execute("SELECT request_id, COUNT(*), MIN(sequence), MAX(sequence) "
                                   "FROM checkpoints GROUP BY request_id").fetchall()
    # nessun chunk perso: N_CHUNK + il chunk finale con usage, in sequenza 1..N
    assert sorted(r[1:] for r in per_richiesta) == [(N_CHUNK + 1, 1, N_CHUNK + 1)] * 4
    # al massimo poche transazioni per richiesta, non una per chunk
    for request_id, *_ in per_richiesta:
        assert transazioni.count(request_id) <= 10, transazioni.count(request_id)


def _keepalive_poi_testo(n_keepalive, pausa):
    def corpo():
        for _ in range(n_keepalive):
            time.sleep(pausa)
            yield b": OPENROUTER PROCESSING\n\n"
        yield ("data: " + json.dumps({"id": "gen-k", "model": "acme/m", "choices": [
            {"index": 0, "delta": {"content": "fine"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.00001}})
               + "\n\ndata: [DONE]\n\n").encode()
    return corpo


def test_stream_oltre_la_durata_massima_si_ferma_dichiarato(monkeypatch):
    monkeypatch.setattr(llm_client, "DURATA_MAX_STREAM_S", 0.2, raising=False)
    client = llm_client.OpenRouterClient(api_key="offline-test", max_retries=0,
        trasporto=httpx.MockTransport(lambda req: httpx.Response(
            200, content=_keepalive_poi_testo(40, 0.02)(), headers={"content-type": "text/event-stream"})))
    with pytest.raises(llm_client.APITimeoutError, match="durata totale"):
        with client.messages.stream(model="acme/m", max_tokens=10,
                                    messages=[{"role": "user", "content": "u"}]) as s:
            s.get_final_message()


def test_stream_entro_la_durata_massima_finisce(monkeypatch):
    monkeypatch.setattr(llm_client, "DURATA_MAX_STREAM_S", 30.0, raising=False)
    client = llm_client.OpenRouterClient(api_key="offline-test", max_retries=0,
        trasporto=httpx.MockTransport(lambda req: httpx.Response(
            200, content=_keepalive_poi_testo(3, 0.0)(), headers={"content-type": "text/event-stream"})))
    with client.messages.stream(model="acme/m", max_tokens=10,
                                messages=[{"role": "user", "content": "u"}]) as s:
        assert s.get_final_message().content[0].text == "fine"


def test_stream_async_oltre_la_durata_massima_si_ferma(monkeypatch):
    monkeypatch.setattr(llm_client, "DURATA_MAX_STREAM_S", 0.2, raising=False)

    async def corpo():
        for _ in range(40):
            await asyncio.sleep(0.02)
            yield b": OPENROUTER PROCESSING\n\n"
        yield next(_keepalive_poi_testo(0, 0)())   # chunk finale valido + [DONE]

    async def prova():
        client = llm_client.AsyncOpenRouterClient(api_key="offline-test", max_retries=0,
            trasporto=httpx.MockTransport(lambda req: httpx.Response(
                200, content=corpo(), headers={"content-type": "text/event-stream"})))
        async with client.messages.stream(model="acme/m", max_tokens=10,
                                          messages=[{"role": "user", "content": "u"}]) as s:
            await s.get_final_message()

    with pytest.raises(llm_client.APITimeoutError, match="durata totale"):
        asyncio.run(prova())
