"""Output policy upgrades must not repay side-stage requests after a crash."""
import json
import sqlite3

import httpx
import pytest

from bellomberg.core import llm_client
from bellomberg.core.request_journal import RequestJournal, RequestBlocked


SCOPES = [("reflection", "_reflection", 1000, 8000),
          ("action_extraction", "_action_table", 2100, 16000)]


def journal(tmp_path):
    return RequestJournal(tmp_path / "aux.sqlite", run_id="synthetic-upgrade",
        authorization={"source": "offline-test"}, authorized_usd="10",
        metadata=lambda model: {"id": model, "context_length": 200000,
            "max_completion_tokens": 128000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


def client_with_receipts(dispatches, *, finish="stop", fault=None):
    def send(request):
        body = json.loads(request.content)
        dispatches.append(body)
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if fault == "disconnect":
            raise httpx.RemoteProtocolError("synthetic disconnect")
        return httpx.Response(200, json={"id": "synthetic-paid-" + str(len(dispatches)),
            "model": body["model"], "choices": [{"message": {"content": "Saved result"},
                "finish_reason": finish}], "usage": {"cost": 0.00003}})
    return llm_client.OpenRouterClient(api_key="offline", max_retries=4,
        trasporto=httpx.MockTransport(send))


def request(client, store, phase, agent, cap, **extra):
    kwargs = {"model": "synthetic/model", "max_tokens": cap,
        "messages": [{"role": "user", "content": "Frozen decision"}], **extra}
    with llm_client.request_scope(store, phase=phase, agent=agent):
        return client.messages.create(**kwargs)


def rows(store):
    with sqlite3.connect(store.path) as db:
        return db.execute("SELECT * FROM requests ORDER BY key").fetchall()


@pytest.mark.parametrize("phase,agent,old,new", SCOPES)
@pytest.mark.parametrize("finish", ["stop", "length"])
def test_upgrade_reuses_paid_body_twice_and_keeps_terminal_reason(tmp_path, phase, agent, old, new, finish):
    store, dispatches = journal(tmp_path), []
    client = client_with_receipts(dispatches, finish=finish)
    original = request(client, store, phase, agent, old)
    before = rows(store)
    for _ in range(2):
        replay = request(client, journal(tmp_path), phase, agent, new)
        assert replay.request_id == original.request_id
        assert replay.stop_reason == original.stop_reason
        assert replay.content[0].text == original.content[0].text
        assert replay.replayed is True
    assert len(dispatches) == 1 and dispatches[0]["max_tokens"] == old
    assert rows(store) == before
    assert store.summary()["request_count"] == 1
    assert store.summary()["cost_usd"] == 0.00003
    assert store.summary()["authorized_usd"] == 10
    assert replay.stop_reason == ("max_tokens" if finish == "length" else "end_turn")


@pytest.mark.parametrize("phase,agent,old,new", SCOPES)
@pytest.mark.parametrize("fault", ["timeout", "disconnect"])
def test_upgrade_never_retries_an_uncertain_old_request(tmp_path, phase, agent, old, new, fault):
    store, dispatches = journal(tmp_path), []
    client = client_with_receipts(dispatches, fault=fault)
    with pytest.raises(llm_client.APIConnectionError):
        request(client, store, phase, agent, old)
    before = rows(store)
    for _ in range(2):
        with pytest.raises(RequestBlocked, match="unresolved"):
            request(client, journal(tmp_path), phase, agent, new)
    assert len(dispatches) == 1 and rows(store) == before
    assert store.summary()["cost_usd"] is None
    assert store.summary()["reserved_usd"] > 0


@pytest.mark.parametrize("phase,agent,old,new", SCOPES)
@pytest.mark.parametrize("difference", ["prompt", "model", "scope", "unsupported_cap"])
def test_compatibility_requires_the_other_fields_to_match(tmp_path, phase, agent, old, new, difference):
    store, dispatches = journal(tmp_path), []
    client = client_with_receipts(dispatches)
    original = request(client, store, phase, agent, old)
    extra = {}
    if difference == "prompt":
        extra["messages"] = [{"role": "user", "content": "Different decision"}]
    elif difference == "model":
        extra["model"] = "synthetic/other-model"
    elif difference == "scope":
        agent = "different-agent"
    else:
        new += 1
    result = request(client, journal(tmp_path), phase, agent, new, **extra)
    assert result.request_id != original.request_id and not result.replayed
    assert len(dispatches) == 2 and dispatches[-1]["max_tokens"] == new
    assert store.summary()["request_count"] == 2
    assert store.summary()["cost_usd"] == 0.00006


@pytest.mark.parametrize("phase,agent,old,new", SCOPES)
def test_compatibility_does_not_hide_tampered_legacy_cost(tmp_path, phase, agent, old, new):
    store, dispatches = journal(tmp_path), []
    client = client_with_receipts(dispatches)
    request(client, store, phase, agent, old)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE requests SET cost=0")
    with pytest.raises(ValueError, match="cost"):
        request(client, journal(tmp_path), phase, agent, new)
    assert len(dispatches) == 1


# Integration of both sides: the PM's journaled cap upgrade + Andrea's reflection
# effort low. A reflection journaled with thinking disabled (old or new cap) must be
# replayed with its original reasoning after a crash, never paid twice; an uncertain
# old request stays blocked.
@pytest.mark.parametrize("old_cap", [1000, 8000])
def test_reflection_disabled_thinking_replays_after_effort_low_upgrade(tmp_path, old_cap, monkeypatch):
    store, dispatches = journal(tmp_path), []
    client = client_with_receipts(dispatches)
    # Riga di un journal VECCHIO (prima della regola PM 05/10 «mai spento"): la si scrive col
    # vecchio {"enabled": false}; il replay sotto deve restare quello di allora.
    vero = llm_client._reasoning_openai
    monkeypatch.setattr(llm_client, "_reasoning_openai", lambda th, model="": (
        dict(llm_client.REASONING_DISABLED) if (th or {}).get("type") == "disabled" else vero(th, model)))
    original = request(client, store, "reflection", "_reflection", old_cap, thinking={"type": "disabled"})
    monkeypatch.setattr(llm_client, "_reasoning_openai", vero)
    for _ in range(2):
        replay = request(client, journal(tmp_path), "reflection", "_reflection", 8000,
                         thinking={"type": "effort", "effort": "low"})
        assert replay.request_id == original.request_id and replay.replayed is True
    assert len(dispatches) == 1 and dispatches[0]["reasoning"] == {"enabled": False}
    assert store.summary()["request_count"] == 1


def test_reflection_uncertain_request_is_declared_and_the_new_attempt_proceeds(tmp_path, capsys):
    """Regola PM 05/10/2026: l'esito incerto della prima chiamata si DICHIARA e non blocca;
    il nuovo tentativo parte (qui cade di nuovo per il guasto finto) e l'incerta resta contata."""
    store, dispatches = journal(tmp_path), []
    client = client_with_receipts(dispatches, fault="disconnect")
    with pytest.raises(llm_client.APIConnectionError):
        request(client, store, "reflection", "_reflection", 1000, thinking={"type": "disabled"})
    with pytest.raises(llm_client.APIConnectionError):
        request(client, journal(tmp_path), "reflection", "_reflection", 8000,
                thinking={"type": "effort", "effort": "low"})
    assert len(dispatches) == 2
    assert "incerto DICHIARATO" in capsys.readouterr().out
    assert journal(tmp_path).summary()["unknown_requests"] == 2
