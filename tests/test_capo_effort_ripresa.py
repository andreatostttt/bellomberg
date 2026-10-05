"""G7/C1 (04/10): la ripresa del Capo usa l'effort salvato con la richiesta originale.

Il Capo (Opus, 128k) e' la chiamata piu' cara della run. Se la run cade dopo la sua risposta
e riparte con CAPO_EFFORT cambiato (o dopo l'aggiornamento che ha portato l'effort dal .env),
la chiave del request journal non deve cambiare: 0 chiamate pagate in piu'.
Anche le richieste salvate nel formato VECCHIO (senza il campo thinking) si riconoscono.
Zero rete: trasporto httpx finto, modelli inventati.
"""
from copy import deepcopy
import json

import httpx
import pytest

from bellomberg.agents import capo
from bellomberg.core import llm_client
from bellomberg.core.request_journal import RequestJournal
from test_capo_collasso import _prepara, _msg, _bb, MEMO_VERO
from test_llm_retry_stream import sse, chunk


def _client(monkeypatch, outcome, requests):
    def send(request):
        body = json.loads(request.content)
        requests.append(body)
        if outcome == "unknown":
            raise httpx.ReadTimeout("synthetic lost receipt", request=request)
        return sse({"id": "capo-receipt-" + str(len(requests)), "model": body["model"],
                    **chunk({"content": MEMO_VERO})},
                   {**chunk(finish="stop"),
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100, "cost": .02}})
    client = llm_client.OpenRouterClient(api_key="offline-test", max_retries=0,
                                        trasporto=httpx.MockTransport(send))
    monkeypatch.setattr(capo, "OpenRouterClient", lambda **kw: client)


def _journal(tmp_path):
    return RequestJournal(tmp_path / "capo.sqlite", run_id="capo-effort-resume",
        authorization={"source": "frozen test"}, authorized_usd=10,
        metadata=lambda model: {"id": model, "context_length": 1_000_000,
            "top_provider": {"max_completion_tokens": 128000},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


def _env(monkeypatch, valore):
    if valore is None:
        monkeypatch.delenv("CAPO_EFFORT", raising=False)
    else:
        monkeypatch.setenv("CAPO_EFFORT", valore)


@pytest.mark.parametrize("formato", ["nuovo", "vecchio"])
@pytest.mark.parametrize("effort_prima,effort_dopo", [
    ("adaptive", None),      # richiesta del codice storico (adaptive) -> default high di oggi
    ("high", "max"),         # il PM cambia CAPO_EFFORT fra la caduta e la ripresa
    ("xhigh", "low"),
])
@pytest.mark.parametrize("outcome", ["complete", "unknown"])
def test_ripresa_del_capo_non_ripaga_dopo_un_cambio_di_effort(tmp_path, monkeypatch, formato,
                                                             effort_prima, effort_dopo, outcome):
    _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    requests = []
    _client(monkeypatch, outcome, requests)
    board = _bb()
    board.persist_run_checkpoint = lambda *a: None
    _env(monkeypatch, effort_prima)
    with llm_client.request_scope(_journal(tmp_path), phase="capo", agent="capo", round_n=3):
        first, _usage = capo.run_capo(board)
    assert len(requests) == 1
    frozen = deepcopy(board.data["_capo_request"])
    if formato == "vecchio":
        # checkpoint scritto prima di questa cura: nessun campo thinking
        frozen.pop("thinking", None)
    _env(monkeypatch, effort_dopo)
    for _ in range(2):
        restored = _bb()
        restored.data["_capo_request"] = deepcopy(frozen)
        with llm_client.request_scope(_journal(tmp_path), phase="capo", agent="capo", round_n=3):
            report, usage = capo.run_capo(restored)
        assert len(requests) == 1, "Capo ripagato dopo il cambio di effort"
        assert restored.data["_capo_request"] == frozen
        assert _journal(tmp_path).summary()["request_count"] == 1
        if outcome == "unknown":
            assert usage["complete"] is False
        else:
            assert report == first and usage["complete"] is True


def test_il_checkpoint_nuovo_congela_l_effort(tmp_path, monkeypatch):
    calls = _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    board = _bb()
    board.persist_run_checkpoint = lambda *a: None
    _env(monkeypatch, "xhigh")
    capo.run_capo(board)
    assert board.data["_capo_request"]["thinking"] == {"type": "effort", "effort": "xhigh"}
    _env(monkeypatch, "low")
    capo.run_capo(board)
    assert [c["thinking"] for c in calls] == [{"type": "effort", "effort": "xhigh"}] * 2


@pytest.mark.parametrize("valore", [{"type": "effort", "effort": "turbo"}, "high", None])
def test_effort_salvato_non_valido_e_un_contratto_cambiato(monkeypatch, valore):
    calls = _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    board = _bb()
    board.persist_run_checkpoint = lambda *a: None
    capo.run_capo(board)
    board.data["_capo_request"]["thinking"] = valore
    with pytest.raises(ValueError, match="checkpoint contract"):
        capo.run_capo(board)
    assert len(calls) == 1
