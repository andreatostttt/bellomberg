"""Traduzione su richiesta dei titoli delle notizie (scheda titolo di Mercati globali):
una chiamata per i soli titoli mancanti, costo nel ledger, cache per (lingua, titolo)."""
import json
from types import SimpleNamespace as NS

import pytest

from bellomberg.core import llm_client as lc
from bellomberg.core import llm_usage as asum   # helper neutri (G3, 04/10)
from bellomberg.market_data import headline_translation as ht


@pytest.fixture
def model(monkeypatch):
    ht._CACHE.clear()
    usage = []
    prompts = []
    monkeypatch.setattr(asum, "MemoryDB", lambda: NS(save_llm_usage=lambda memo_id, log: usage.extend(log)))
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "cost_eur", lambda model, usage, ttl=None: {
        "cost": 0.0002, "status": "ok", "fx_rate": 0.9, "fx_source": "live", "breakdown": {}})
    monkeypatch.setattr(lc, "modello", lambda funzione, *a: "fake/summary-model")

    def create(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        lines = [l.split(". ", 1)[1] for l in prompt.split("\n\n", 1)[1].splitlines()]
        return NS(content=[lc.TextBlock(json.dumps({"titles": [{"i": i, "t": "IT: " + l} for i, l in enumerate(lines)]}))],
                  usage=NS(input_tokens=120, output_tokens=60, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0002))

    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: NS(messages=NS(create=create)))
    yield NS(usage=usage, prompts=prompts)
    ht._CACHE.clear()


def test_translates_once_then_serves_from_memory(model):
    first = ht.translate_headlines(["Synthetic beats estimates", "Synthetic cuts guidance"], language="it")
    assert first["status"] == "done" and first["cached"] is False
    assert first["titles"] == ["IT: Synthetic beats estimates", "IT: Synthetic cuts guidance"]
    assert first["cost_eur"] == 0.0002 and len(model.usage) == 1 and "italiano" in model.prompts[0]
    # stesso elenco con un titolo nuovo: si paga solo il nuovo
    second = ht.translate_headlines(["Synthetic beats estimates", "Synthetic opens a plant"], language="it")
    assert second["titles"] == ["IT: Synthetic beats estimates", "IT: Synthetic opens a plant"]
    assert "Synthetic beats estimates" not in model.prompts[1] and len(model.usage) == 2
    # tutto in memoria: nessuna chiamata, nessun costo
    third = ht.translate_headlines(["Synthetic cuts guidance"], language="it")
    assert third["cached"] is True and third["cost_eur"] is None and len(model.prompts) == 2


def test_missing_model_is_not_configured(monkeypatch):
    ht._CACHE.clear()

    def missing(funzione, *a):
        raise lc.ConfigurazioneLLMMancante("NEWS_SUMMARY_MODEL")
    monkeypatch.setattr(lc, "modello", missing)
    assert ht.translate_headlines(["Synthetic"], language="it") == {"status": "not_configured", "variable": "NEWS_SUMMARY_MODEL"}


def _answer(monkeypatch, payload):
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: NS(messages=NS(create=lambda **kw: NS(
        content=[lc.TextBlock(payload if isinstance(payload, str) else json.dumps(payload))],
        usage=NS(input_tokens=1, output_tokens=1, cost_usd=None)))))


def test_partial_answer_shows_what_arrived_and_retries_only_the_rest(model, monkeypatch):
    _answer(monkeypatch, {"titles": [{"i": 1, "t": "B tradotto"}]})
    first = ht.translate_headlines(["A", "B"], language="it")
    assert first["status"] == "done" and first["titles"] == ["A", "B tradotto"] and first["complete"] is False
    assert ("it", "A") not in ht._CACHE and ht._CACHE[("it", "B")] == "B tradotto"


@pytest.mark.parametrize("payload", [
    {"titles": ["A it", "B it"]},
    ["A it", "B it"],
    {"0": "A it", "1": "B it"},
    {"translations": [{"index": 0, "translation": "A it"}, {"index": 1, "translation": "B it"}]},
    'Ecco: {"titles": [{"i": "0", "t": "A it"}, {"i": "1", "t": "B it"}]}',
])
def test_accepts_the_shapes_models_really_return(model, monkeypatch, payload):
    _answer(monkeypatch, payload)
    assert ht.translate_headlines(["A", "B"], language="it")["titles"] == ["A it", "B it"]


def test_unreadable_answer_is_an_error_and_nothing_is_cached(model, monkeypatch):
    _answer(monkeypatch, {"titles": ["solo uno"]})   # elenco semplice incompleto: non si sa a chi va
    result = ht.translate_headlines(["A", "B"], language="it")
    assert result["status"] == "error" and not ht._CACHE


def test_endpoint_requires_titles_and_returns_translation(model, monkeypatch):
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api
    monkeypatch.setitem(api._SESSIONS, "synthetic-token", 9_999_999_999)
    monkeypatch.setattr(api, "news_refresh_manager", NS(start=lambda immediate=True: None, stop=lambda: None))
    api._LAST_CALL.clear()
    headers = {"X-BB-Token": "synthetic-token", "X-BB-Language": "it"}
    with TestClient(api.app, base_url="http://127.0.0.1:8765") as c:
        assert c.post("/market/news/translate", json={"titles": []}, headers=headers).status_code == 400
        body = c.post("/market/news/translate", json={"titles": ["Synthetic beats"]}, headers=headers).json()
        assert body["titles"] == ["IT: Synthetic beats"] and body["language"] == "it"
        assert c.post("/market/news/translate", json={"titles": ["X"]}, headers={"X-BB-Language": "it"}).status_code == 401
    api._LAST_CALL.clear()
