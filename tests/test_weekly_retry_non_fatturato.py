"""KA (04/10/2026, Opus 5.5): nel percorso del weekly vale la regola di Trade Idea
«TI-RITENTATIVO-NON-FATTURATO» (decisione PM): UN solo nuovo tentativo quando la chiamata
CERTAMENTE non e' stata fatturata (connessione mai stabilita o 402 di ammissione
OpenRouter); la prima richiesta si chiude nel journal come 'released' (costo 0, con la
prova), non blocca prepare() ne' costs_unresolved. Un guasto dopo l'invio (fatturabile)
resta 'unknown' e blocca come prima; due guasti non fatturati = nessun terzo tentativo,
dichiarato sull'eccezione e nel summary.

Suite OFFLINE: httpx.MockTransport, journal in tmp_path, modelli/id/importi inventati.
"""
import asyncio
import json
import sqlite3

import httpx
import pytest

from bellomberg.core import llm_client as llm
from bellomberg.core.request_journal import RequestBlocked, RequestJournal
from bellomberg.core.unbilled import provably_unbilled
from bellomberg.storage.weekly_run_store import costs_unresolved

MODEL = "synthetic/zz-desk"
ADMISSION = {"reason": "in_flight_budget_exhausted", "limit_source": "openrouter_in_flight_budget",
             "provider_name": None}


def _journal(tmp_path):
    return RequestJournal(tmp_path / "weekly-zz-requests.sqlite", run_id="synthetic-weekly",
        authorization={"source": "offline-test"}, authorized_usd="1",
        metadata=lambda model: {"id": model, "context_length": 1000,
                                "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


def _admission_402(retry_after=None):
    meta = dict(ADMISSION, **({"headers": {"Retry-After": retry_after}} if retry_after else {}))
    return httpx.Response(402, json={"error": {"code": 402, "message": "In-flight budget exhausted",
                                               "metadata": meta}})


def _ok(stream):
    if not stream:
        return httpx.Response(200, json={"id": "gen-zz-ok", "model": MODEL,
            "choices": [{"message": {"content": "Desk report."}, "finish_reason": "stop"}],
            "usage": {"cost": 0.00004}})
    chunks = [{"id": "gen-zz-ok", "model": MODEL, "choices": [{"index": 0, "delta": {"content": "Desk report."},
               "finish_reason": None}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"cost": 0.00004}}]
    body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


STEPS = {
    "connect": lambda request, stream: (_ for _ in ()).throw(httpx.ConnectError("synthetic refused", request=request)),
    "connect_timeout": lambda request, stream: (_ for _ in ()).throw(httpx.ConnectTimeout("synthetic", request=request)),
    "admission": lambda request, stream: _admission_402(),
    "admission_retry_after": lambda request, stream: _admission_402("7"),
    "other_402": lambda request, stream: httpx.Response(402, json={"error": {"code": 402,
        "message": "Insufficient credits", "metadata": {"reason": "insufficient_credits"}}}),
    "read_timeout": lambda request, stream: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic", request=request)),
    "disconnect": lambda request, stream: (_ for _ in ()).throw(httpx.RemoteProtocolError("synthetic", request=request)),
    "503": lambda request, stream: httpx.Response(503, json={"error": {"code": 503, "message": "synthetic"}}),
    "ok": lambda request, stream: _ok(stream),
}


@pytest.fixture(params=["create", "create_async", "stream", "stream_async"])
def call(request, monkeypatch):
    """Esegue UNA chiamata del client nella modalita' data; registra dispatch e attese."""
    mode = request.param
    waits, dispatched = [], []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    async def asleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(llm.asyncio, "sleep", asleep)

    def execute(steps):
        steps = list(steps)
        streaming = mode.startswith("stream")
        def transport(req):
            dispatched.append(json.loads(req.content))
            assert steps, "dispatch oltre quelli attesi"
            return STEPS[steps.pop(0)](req, streaming)
        cls = llm.AsyncOpenRouterClient if mode.endswith("async") else llm.OpenRouterClient
        client = cls(api_key="test-only", max_retries=2, trasporto=httpx.MockTransport(transport))
        kwargs = {"model": MODEL, "max_tokens": 20, "messages": [{"role": "user", "content": "desk work"}]}
        if mode == "create":
            try:
                return client.messages.create(**kwargs)
            finally:
                client._http.close()
        if mode == "stream":
            try:
                with client.messages.stream(**kwargs) as stream:
                    list(stream)
                    return stream.get_final_message()
            finally:
                client._http.close()
        async def work():
            try:
                if mode == "create_async":
                    return await client.messages.create(**kwargs)
                async with client.messages.stream(**kwargs) as stream:
                    async for _ in stream:
                        pass
                    return await stream.get_final_message()
            finally:
                await client._http.aclose()
        return asyncio.run(work())

    execute.waits, execute.dispatched, execute.mode = waits, dispatched, mode
    return execute


def _rows(journal):
    with sqlite3.connect(journal.path) as db:
        return [tuple(r) for r in db.execute("SELECT state,cost FROM requests ORDER BY rowid")]


@pytest.mark.parametrize("first,wait", [("admission", 30), ("admission_retry_after", 7),
                                        ("connect", 5), ("connect_timeout", 5)])
def test_unbilled_failure_gets_one_retry_released_then_received(call, tmp_path, first, wait):
    journal = _journal(tmp_path)
    with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
        response = call([first, "ok"])
    assert response.content[0].text == "Desk report."
    assert len(call.dispatched) == 2 and call.waits == [wait]
    assert _rows(journal) == [("released", 0), ("received", 40000)]
    summary = journal.summary()
    assert summary["unknown_requests"] == 0 and summary["cost_usd"] == pytest.approx(0.00004)
    [released] = summary["released_requests"]
    assert (released["agent"], released["phase"], released["round_n"]) == ("macro", "R1", 1)
    assert ("402 di ammissione" in released["reason"]) == first.startswith("admission")
    assert costs_unresolved(summary) is False
    # il journal non blocca la richiesta successiva della run
    other = journal.prepare({"model": MODEL, "max_tokens": 20, "messages": [{"role": "user", "content": "next"}]},
                            {"phase": "R1", "agent": "rates", "round_n": 1})
    assert other[2] is None and other[0]


@pytest.mark.parametrize("fault", ["read_timeout", "disconnect", "503", "other_402"])
def test_billable_failure_after_send_is_not_retried_and_stays_unknown(call, tmp_path, fault):
    journal = _journal(tmp_path)
    with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
        with pytest.raises(llm.APIError) as exc:
            call([fault])
    assert len(call.dispatched) == 1 and call.waits == []
    assert _rows(journal) == [("unknown", None)]
    assert not hasattr(exc.value, "unbilled_released")
    summary = journal.summary()
    assert summary["released_requests"] == [] and summary["unknown_requests"] == 1
    assert costs_unresolved(summary) is True
    # Regola PM 05/10/2026: l'incerta resta contata e DICHIARATA, il lavoro successivo parte.
    nuovo, _, _ = journal.prepare({"model": MODEL, "max_tokens": 20, "messages": [{"role": "user", "content": "next"}]},
                                  {"phase": "R1", "agent": "rates", "round_n": 1})
    assert nuovo and _rows(journal)[0] == ("unknown", None)   # l'incerta resta incerta, non cancellata


@pytest.mark.parametrize("steps", [["connect", "connect"], ["admission", "connect"]])
def test_two_unbilled_failures_no_third_attempt_and_declared(call, tmp_path, steps):
    journal = _journal(tmp_path)
    with llm.request_scope(journal, phase="R0", agent="fundamentals", round_n=0):
        with pytest.raises(llm.APIError) as exc:
            call(steps)
    assert len(call.dispatched) == 2 and len(call.waits) == 1
    assert _rows(journal) == [("released", 0), ("released", 0)]
    summary = journal.summary()
    ids = [row["request_id"] for row in summary["released_requests"]]
    assert exc.value.unbilled_retry_exhausted is True and exc.value.unbilled_released == ids
    assert exc.value.request_id == ids[1]
    assert isinstance(exc.value, (llm.APIConnectionError, llm.APIStatusError))
    assert costs_unresolved(summary) is False


def test_unbilled_then_billable_blocks_and_keeps_the_release(call, tmp_path):
    journal = _journal(tmp_path)
    with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
        with pytest.raises(llm.APIError) as exc:
            call(["connect", "read_timeout"])
    assert len(call.dispatched) == 2
    assert _rows(journal) == [("released", 0), ("unknown", None)]
    assert not getattr(exc.value, "unbilled_retry_exhausted", False)
    assert exc.value.unbilled_released == [journal.summary()["released_requests"][0]["request_id"]]
    assert costs_unresolved(journal.summary()) is True


def test_resume_replays_the_received_retry_without_paying_again(call, tmp_path):
    with llm.request_scope(_journal(tmp_path), phase="R1", agent="macro", round_n=1):
        call(["connect", "ok"])
    with llm.request_scope(_journal(tmp_path), phase="R1", agent="macro", round_n=1):
        response = call([])
    assert response.content[0].text == "Desk report."
    assert len(call.dispatched) == 2
    assert _rows(_journal(tmp_path)) == [("released", 0), ("received", 40000)]


@pytest.mark.parametrize("scope", ["none", "external"])
def test_without_an_own_journal_nothing_changes(call, tmp_path, scope):
    """Senza journal proprio (chat, o Trade Idea che fa il suo retry col budget gate)
    llm_client non aggiunge tentativi: un 402 non si ritenta mai."""
    with pytest.raises(llm.APIStatusError):
        if scope == "none":
            call(["admission"])
        else:
            with llm.request_scope(None, phase="trade_idea"):
                call(["admission"])
    assert len(call.dispatched) == 1 and call.waits == []


def test_released_row_must_carry_its_evidence(tmp_path):
    journal = _journal(tmp_path)
    request_id, _, _ = journal.prepare({"model": MODEL, "max_tokens": 20,
        "messages": [{"role": "user", "content": "desk work"}]}, {"phase": "R1", "agent": "macro", "round_n": 1})
    error = llm.APIConnectionError("ConnectError: synthetic")
    error.transport_phase = "connect"
    assert journal.release_unbilled(request_id, error, reason="connessione mai stabilita") is True
    assert journal.release_unbilled(request_id, error, reason="di nuovo") is False  # gia' chiusa
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE requests SET cost=7")
    with pytest.raises(ValueError, match="unbilled evidence"):
        journal.summary()


def _matrix():
    from bellomberg.core.llm_client import APIConnectionError, APIStatusError, APITimeoutError
    connect = APIConnectionError("ConnectError: x")
    connect.transport_phase = "connect"
    connect_partial = APIConnectionError("ConnectError: x")
    connect_partial.transport_phase = "connect"
    connect_partial.partial_response = {"id": "partial"}
    ctimeout = APITimeoutError("timeout")
    ctimeout.transport_phase = "connect"
    meta = json.dumps(ADMISSION)
    meta_ra = json.dumps(dict(ADMISSION, headers={"Retry-After": "500"}))
    def http402(message):
        error = APIStatusError(402, message)
        error.http_status = 402  # come lo misura _invia
        return error
    return [connect, connect_partial, ctimeout, APITimeoutError("read timeout"),
            APIConnectionError("RemoteProtocolError: x"),
            http402("In-flight budget exhausted | metadata: " + meta),
            http402("In-flight budget exhausted | metadata: " + meta_ra),
            APIStatusError(402, "Insufficient credits | metadata: " + json.dumps({"reason": "x"})),
            APIStatusError(402, "x | metadata: " + json.dumps({k: v for k, v in ADMISSION.items()
                                                                 if k != "provider_name"})),
            APIStatusError(503, "x"), APIStatusError(429, "x"), RuntimeError("x"), ValueError("ConnectError")]


def test_same_rule_as_trade_idea_on_an_exception_matrix():
    from bellomberg.agents import trade_idea
    results = [(provably_unbilled(e), trade_idea._provably_unbilled(e)) for e in _matrix()]
    assert [a for a, _ in results] == [b for _, b in results]
    assert [a for a, _ in results] == [5, None, 5, None, None, 30, 120, None, None, None, None, None, None]


def _divergent():
    from bellomberg.core.llm_client import APIStatusError
    body = "In-flight budget exhausted | metadata: " + json.dumps(ADMISSION)
    with_generation = APIStatusError(402, body)
    with_generation.http_status, with_generation.generation_id = 402, "gen-zz-402"
    on_502 = APIStatusError(402, body)
    on_502.http_status = 502
    unmeasured = APIStatusError(402, body)  # nessuno status HTTP misurato
    return [with_generation, on_502, unmeasured]


def test_trade_idea_uses_the_same_stricter_rule():
    """KA (05/10, main): le due divergenze di RV-KA (generation_id presente; status HTTP non
    misurato o diverso da 402) sono chiuse: Trade Idea importa la regola da core/unbilled,
    quindi entrambe le vie danno None (incerto) su questi casi."""
    from bellomberg.agents import trade_idea
    assert [provably_unbilled(e) for e in _divergent()] == [None, None, None]
    assert [trade_idea._provably_unbilled(e) for e in _divergent()] == [None, None, None]
    assert trade_idea._core_provably_unbilled is provably_unbilled


def test_admission_with_a_generation_id_stays_unknown_and_reconcilable(call, tmp_path):
    """RV-KA P2 (decisione main 05/10): un 402 che porta l'id di una generazione NON e'
    certamente non fatturato: nessun retry, riga 'unknown' con l'id salvato (riconciliabile)."""
    journal = _journal(tmp_path)
    STEPS["admission_gen"] = lambda request, stream: httpx.Response(
        402, headers={"x-generation-id": "gen-zz-402"},
        json={"error": {"code": 402, "message": "In-flight budget exhausted", "metadata": ADMISSION}})
    try:
        with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
            with pytest.raises(llm.APIStatusError):
                call(["admission_gen"])
    finally:
        STEPS.pop("admission_gen")
    assert len(call.dispatched) == 1 and _rows(journal) == [("unknown", None)]
    assert RequestJournal.unknown_requests(journal.path)[0]["receipt"]["generation_id"] == "gen-zz-402"
    assert costs_unresolved(journal.summary()) is True


def test_release_evidence_always_carries_the_generation_field(tmp_path):
    journal = _journal(tmp_path)
    request_id, _, _ = journal.prepare({"model": MODEL, "max_tokens": 20,
        "messages": [{"role": "user", "content": "desk work"}]}, {"phase": "R1", "agent": "macro", "round_n": 1})
    error = llm.APIConnectionError("ConnectError: synthetic")
    error.transport_phase = "connect"
    journal.release_unbilled(request_id, error, reason="connessione mai stabilita")
    with sqlite3.connect(journal.path) as db:
        receipt = json.loads(db.execute("SELECT receipt FROM requests").fetchone()[0])
    assert "generation_id" in receipt["release"]["evidence"]


@pytest.mark.parametrize("started", ["response_started", "_checkpointed"])
def test_no_release_once_the_provider_answered(tmp_path, started):
    """RV-KA P3.2: guardia esplicita, senza affidarsi a raw_response() pieno: dopo un 2xx
    (o un chunk salvato) anche un errore marcato «connect» resta incerto."""
    journal = _journal(tmp_path)
    error = llm.APIConnectionError("ConnectError: from another call")
    error.transport_phase = "connect"
    with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
        attempt = llm._RequestAttempt({"model": MODEL, "max_tokens": 20,
                                       "messages": [{"role": "user", "content": "desk work"}]})
        setattr(attempt, started, True)
        attempt.fail(error)
    assert attempt.released is None and _rows(journal) == [("unknown", None)]


def test_stream_marks_the_attempt_started_after_http_200(tmp_path, monkeypatch):
    """Il cablaggio della guardia: uno stream aperto con 200 segna response_started."""
    client = llm.OpenRouterClient(api_key="test-only", max_retries=0,
                                  trasporto=httpx.MockTransport(lambda req: _ok(True)))
    with llm.request_scope(_journal(tmp_path), phase="R1", agent="macro", round_n=1):
        with client.messages.stream(model=MODEL, max_tokens=20,
                                    messages=[{"role": "user", "content": "desk work"}]) as stream:
            assert stream._attempt.response_started is True
            list(stream)
    client._http.close()


def test_sse_admission_error_after_http_200_stays_unknown(call, tmp_path):
    if not call.mode.startswith("stream"):
        pytest.skip("solo streaming")
    body = "data: " + json.dumps({"error": {"code": 402, "message": "In-flight budget exhausted",
                                            "metadata": ADMISSION}}) + "\n\n"
    STEPS["sse_402"] = lambda request, stream: httpx.Response(200, content=body.encode(),
                                                              headers={"content-type": "text/event-stream"})
    journal = _journal(tmp_path)
    try:
        with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
            with pytest.raises(llm.APIStatusError):
                call(["sse_402"])
    finally:
        STEPS.pop("sse_402")
    assert len(call.dispatched) == 1 and _rows(journal) == [("unknown", None)]


def test_model_probe_never_retries_even_when_unbilled(tmp_path, monkeypatch):
    """RV-KA P3.4: la sonda misura, non insiste: 2 slug col 402 = 2 invii, nessuna attesa."""
    waits, dispatched = [], []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    def transport(req):
        dispatched.append(1)
        return _admission_402()
    client = llm.OpenRouterClient(api_key="test-only", max_retries=0, trasporto=httpx.MockTransport(transport))
    journal = _journal(tmp_path)
    with llm.request_scope(journal, phase="model_probe"):
        esiti = llm.sonda_modelli(["synthetic/zz-a", "synthetic/zz-b"], client=client, max_tokens=20)
    assert len(dispatched) == 2 and waits == []
    assert [e["ok"] for e in esiti.values()] == [False, False]
    assert [r[0] for r in _rows(journal)] == ["released", "released"]
    assert costs_unresolved(journal.summary()) is False
    assert llm._SENZA_NUOVO_TENTATIVO.get() is False


def test_trade_idea_real_client_admission_402_is_released_and_retried_once(tmp_path, monkeypatch):
    """Il cablaggio di TI con il client VERO: il 402 passa da _invia (http_status misurato),
    quindi la regola piu' stretta lo riconosce ancora e TI ritenta una sola volta."""
    from bellomberg.agents import trade_idea
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    client = llm.OpenRouterClient(api_key="test-only", max_retries=0,
                                  trasporto=httpx.MockTransport(lambda req: _admission_402("3")))
    with pytest.raises(llm.APIStatusError) as info:
        client.messages.create(model=MODEL, max_tokens=20, messages=[{"role": "user", "content": "x"}])
    client._http.close()
    assert info.value.http_status == 402 and trade_idea._provably_unbilled(info.value) == 3


@pytest.mark.parametrize("fault,internal", [("connect", "_release_unbilled"), ("read_timeout", "_fail")])
def test_failed_journal_write_blocks_the_next_request(call, tmp_path, monkeypatch, fault, internal):
    """RV-KA (a): se l'esito del guasto non si scrive, la riga resta 'reserved' sul disco;
    la richiesta successiva della run NON parte (fail closed, dichiarato)."""
    journal = _journal(tmp_path)
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked (synthetic)")
    monkeypatch.setattr(journal, internal, broken)
    with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
        with pytest.raises(sqlite3.OperationalError):
            call([fault])
    assert len(call.dispatched) == 1 and _rows(journal) == [("reserved", None)]
    with pytest.raises(RequestBlocked, match="not written to the journal"):
        journal.prepare({"model": MODEL, "max_tokens": 20, "messages": [{"role": "user", "content": "next"}]},
                        {"phase": "R1", "agent": "rates", "round_n": 1})


def test_admission_body_inside_another_http_status_is_not_released(call, tmp_path):
    """RV-KA P3.1: un corpo «code 402 in_flight» dentro un HTTP 502 non prova l'ammissione."""
    journal = _journal(tmp_path)
    STEPS["admission_502"] = lambda request, stream: httpx.Response(
        502, json={"error": {"code": 402, "message": "In-flight budget exhausted", "metadata": ADMISSION}})
    try:
        with llm.request_scope(journal, phase="R1", agent="macro", round_n=1):
            with pytest.raises(llm.APIStatusError):
                call(["admission_502"])
    finally:
        STEPS.pop("admission_502")
    assert len(call.dispatched) == 1 and _rows(journal) == [("unknown", None)]
