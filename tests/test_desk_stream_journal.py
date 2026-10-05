"""Streaming dei desk sotto il request journal (decisione maintainer D, integrazione 04/10).

(a) il costo del chunk finale `usage` di uno stream finisce nella riga del request journal;
(b) uno stream interrotto DOPO che la richiesta e' stata accettata (connessione caduta a meta')
    non viene ne' rimandato ne' ripagato: la riga resta incerta e blocca la spesa successiva.
Nessun retry di rete al desk: l'unico strato e' llm_client, spento sotto un journal.

Zero rete (httpx.MockTransport), zero DB vero, modelli e numeri inventati.
"""
import json
import sqlite3
import sys
import types

import httpx
import pytest

from bellomberg.core import llm_pricing
from bellomberg.core.llm_client import OpenRouterClient
from bellomberg.core.request_journal import RequestJournal
from bellomberg.agents.specialists.base import Blackboard, Specialist

MODEL = "acme/desk-stream"
REPORT = "Report ZZTEST: analisi completa con i numeri inventati dei tool."
USAGE_TOOL = {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.000425,
              "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}
USAGE_TESTO = {"prompt_tokens": 180, "completion_tokens": 60, "cost": 0.000875,
               "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}


class _Desk(Specialist):
    name = "quant"
    role = "mock"
    system_prompt = "Sei un mock per il collaudo offline."
    tools_used = []


@pytest.fixture
def bb(tmp_path, monkeypatch):
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH", str(tmp_path / "current_run.json"))
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "fallback"))
    llm_pricing.reset_fx_memo()
    monkeypatch.setenv("CONSIGLIERE_QUANT_MODEL", MODEL)
    for nome in ("CONSIGLIERE_R1_EFFORT", "CONSIGLIERE_QUANT_EFFORT"):
        monkeypatch.delenv(nome, raising=False)
    cf = types.ModuleType("current_facts")
    cf.current_facts_block = lambda: ""
    cf.favorites_block = lambda: ""
    cf.pm_theses_block = lambda: ""
    cf.research_block = lambda: ""
    monkeypatch.setitem(sys.modules, 'bellomberg.core.current_facts', cf)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", cf, raising=False)
    ct = types.ModuleType("chat_tools")
    _tool = {"name": "get_portfolio_live", "description": "mock",
             "input_schema": {"type": "object", "properties": {}}}
    ct.get_tools_for_agent = lambda name: [dict(_tool)]
    ct.TOOL_DEFINITIONS = [dict(_tool)]
    ct.dispatch = lambda name, args=None: {"ok": True, "mock": name}
    monkeypatch.setitem(sys.modules, 'bellomberg.agents.chat_tools', ct)
    import bellomberg.agents
    monkeypatch.setattr(bellomberg.agents, "chat_tools", ct, raising=False)
    board = Blackboard()
    yield board
    llm_pricing.reset_fx_memo()


def _journal(tmp_path):
    return RequestJournal(tmp_path / "requests.sqlite", run_id="offline-desk-stream",
                          authorization={"source": "test-launch"}, authorized_usd="50",
                          metadata=lambda model: {"id": model, "context_length": 400000,
                                                  "pricing": {"prompt": "0.0000001",
                                                              "completion": "0.0000004"}})


def _chunk(delta=None, finish=None, usage=None, gen="gen-acme-1"):
    c = {"id": gen, "model": MODEL,
         "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        c["usage"] = usage
    return c


def _sse(*chunks):
    righe = [": OPENROUTER PROCESSING", ""]
    for c in chunks:
        righe += ["data: " + json.dumps(c), ""]
    righe += ["data: [DONE]", ""]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="\n".join(righe).encode("utf-8"))


class _StreamInterrotto(httpx.SyncByteStream):
    """La richiesta e' accettata (HTTP 200, primi chunk arrivati), poi la connessione cade."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __iter__(self):
        for c in self._chunks:
            yield ("data: " + json.dumps(c) + "\n\n").encode("utf-8")
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")


def _client(risposte, corpi, max_retries=2):
    def gestore(req):
        corpi.append(json.loads(req.content))
        assert risposte, "richiesta in piu': il desk o il client ha rimandato una call"
        return risposte.pop(0)
    return OpenRouterClient(api_key="sk-finta", max_retries=max_retries,
                            trasporto=httpx.MockTransport(gestore))


def _righe(journal):
    with sqlite3.connect(journal.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY created, rowid")]


def test_costo_del_chunk_usage_finale_entra_nel_journal(bb, tmp_path):
    journal = _journal(tmp_path)
    bb.request_journal = journal
    corpi = []
    risposte = [
        _sse(_chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                     "function": {"name": "read_blackboard", "arguments": "{}"}}]}),
             _chunk(finish="tool_calls"),
             # come OpenRouter: la usage CON il costo arriva in un chunk finale a parte
             {"id": "gen-acme-1", "model": MODEL, "choices": [], "usage": USAGE_TOOL}),
        _sse(_chunk({"content": REPORT}, gen="gen-acme-2"),
             _chunk(finish="stop", gen="gen-acme-2"),
             {"id": "gen-acme-2", "model": MODEL, "choices": [], "usage": USAGE_TESTO}),
    ]
    out = _Desk(bb, client=_client(risposte, corpi)).run(1)

    assert out == REPORT
    assert len(corpi) == 2 and all(c.get("stream") is True for c in corpi)
    righe = _righe(journal)
    assert [r["state"] for r in righe] == ["received", "received"]
    # nanoUSD: il costo di ciascuna riga e' ESATTAMENTE quello del chunk usage finale
    assert [r["cost"] for r in righe] == [425000, 875000]
    for riga, usage in zip(righe, (USAGE_TOOL, USAGE_TESTO)):
        assert json.loads(riga["receipt"])["usage"]["cost"] == usage["cost"]
        assert json.loads(riga["response"])["usage"] == usage
    summary = journal.summary()
    assert summary["cost_usd"] == pytest.approx(0.0013)
    assert summary["unknown_requests"] == 0
    assert bb.usage_log[-1]["cost_usd"] == pytest.approx(0.0013)


def test_stream_interrotto_non_si_rimanda_ne_si_ripaga(bb, tmp_path, capsys):
    journal = _journal(tmp_path)
    bb.request_journal = journal
    corpi = []
    accettata = httpx.Response(200, headers={"content-type": "text/event-stream"},
                               stream=_StreamInterrotto([_chunk({"content": "Analisi parz"})]))
    # max_retries=2: il budget del client c'e', ma sotto il journal non si usa
    out = _Desk(bb, client=_client([accettata], corpi, max_retries=2)).run(1)

    assert out.startswith("[ERROR quant round 1]")
    assert "Server disconnected" in out
    assert len(corpi) == 1, "uno stream accettato e poi caduto non si rimanda"
    righe = _righe(journal)
    assert len(righe) == 1
    # incerta: nessun costo misurato, prenotazione intera conservata, prova parziale salvata
    assert righe[0]["state"] in ("unknown", "incomplete") and righe[0]["cost"] is None
    assert "Server disconnected" in righe[0]["receipt"] + str(righe[0]["error"])
    with sqlite3.connect(journal.path) as db:
        checkpoint = db.execute("SELECT payload FROM checkpoints").fetchall()
    assert any("Analisi parz" in row[0] for row in checkpoint)
    summary = journal.summary()
    assert summary["cost_usd"] is None and summary["unknown_requests"] == 1
    assert summary["reserved_usd"] > 0
    assert bb.usage_log[-1]["status"] == "api_error"

    # Rilancio dello stesso round sullo stesso journal: bloccato PRIMA di ogni POST.
    out2 = _Desk(bb, client=_client([], corpi, max_retries=2)).run(1)
    assert out2.startswith("[ERROR quant round 1]")
    # stessa richiesta = stessa chiave: il journal rifiuta il replay di un esito irrisolto
    assert "not replayable" in out2 and "unresolved" in out2
    assert len(corpi) == 1, "la richiesta forse pagata non riparte"
    assert len(_righe(journal)) == 1


def test_senza_journal_lo_stream_caduto_non_riparte_comunque(bb):
    """Anche senza journal: llm_client ritenta solo PRIMA della risposta, mai a stream
    iniziato, e il desk non ha piu' un suo retry di rete."""
    corpi = []
    accettata = httpx.Response(200, headers={"content-type": "text/event-stream"},
                               stream=_StreamInterrotto([_chunk({"content": "Analisi parz"})]))
    out = _Desk(bb, client=_client([accettata], corpi, max_retries=2)).run(1)
    assert out.startswith("[ERROR quant round 1]")
    assert len(corpi) == 1
