"""G7, seguito della revisione avversariale (REV_G7, 04/10): R1-R7.

Codice critico: costi delle chiamate. Ogni test fissa una garanzia del request journal o del
preparer: una risposta pagata non si ripaga, un esito non pagato non si congela, un esito
incerto resta visibile e riconciliabile. Zero rete: trasporti httpx finti, modelli inventati.
"""
from copy import deepcopy
import json
import time

import httpx
import pytest

from bellomberg.core import llm_client
from bellomberg.core.request_journal import RequestJournal, RequestBlocked

GEN = "gen-1759500000-AbCdEfGhIjKlMnOpQrSt"


def _journal(tmp_path, name="j.sqlite"):
    return RequestJournal(tmp_path / name, run_id="g7-seguito",
        authorization={"source": "offline test"}, authorized_usd=10,
        metadata=lambda model: {"id": model, "context_length": 1_000_000,
            "top_provider": {"max_completion_tokens": 128000},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


def _sse(*chunks, headers=None):
    body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(),
                          headers={"content-type": "text/event-stream", **(headers or {})})


def _client(send, cls=llm_client.OpenRouterClient):
    return cls(api_key="offline-test", max_retries=0, trasporto=httpx.MockTransport(send))


# ------------------------------------------------------------------------------------ R1
def test_r1_glm_disabled_diventa_minimal_ma_adaptive_ed_effort_restano_senza_effort(monkeypatch):
    """Decisione del coordinatore (04/10): su z-ai/ «minimal» e' AMMESSO solo come modo misurato
    (05/09) di SPEGNERE la deliberazione quando il modello rifiuta {"enabled":false}; una
    chiamata che la vuole ACCESA (adaptive/effort) non manda mai un effort a z-ai/."""
    llm_client.RAGIONAMENTO_OBBLIGATORIO.discard("z-ai/glm-finto")
    corpi = []
    def send(req):
        corpo = json.loads(req.content)
        corpi.append(corpo)
        if corpo.get("reasoning") == {"enabled": False}:
            return httpx.Response(400, json={"error": {"code": 400,
                "message": "Reasoning is mandatory for this endpoint and cannot be disabled."}})
        return httpx.Response(200, json={"id": "gen-x", "model": corpo["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.00001}})
    c = _client(send)
    base = dict(model="z-ai/glm-finto", max_tokens=10, messages=[{"role": "user", "content": "u"}])
    try:
        c.messages.create(thinking={"type": "disabled"}, **base)
        assert [b.get("reasoning") for b in corpi] == [{"enabled": False}, {"effort": "minimal"}]
        c.messages.create(thinking={"type": "disabled"}, **base)   # gia' marcato: minimal subito
        assert corpi[-1]["reasoning"] == {"effort": "minimal"}
        for thinking in ({"type": "adaptive"}, {"type": "effort", "effort": "high"},
                         {"type": "effort", "effort": "low"}):
            c.messages.create(thinking=thinking, **base)
            assert "reasoning" not in corpi[-1], thinking
    finally:
        llm_client.RAGIONAMENTO_OBBLIGATORIO.discard("z-ai/glm-finto")


# ------------------------------------------------------------------------------------ R2
def test_r2_preparer_rifiuto_a_costo_zero_resta_ritentabile_dopo_il_cambio_di_effort(tmp_path, monkeypatch):
    from test_preparation_rejections import rejection
    from test_preparation_ai import _proposer
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    seen = []
    def call(**request):
        seen.append(deepcopy(request))
        if len(seen) == 1:
            raise rejection()
        return _proposer(tmp_path).call(**request)
    p = _proposer(tmp_path, call=call)     # thinking adaptive
    with pytest.raises(Exception, match="Temporarily held"):
        p({"ticker": "SYNTH"}, {"schema": {}})
    now[0] = 2000.0
    q = _proposer(tmp_path, call=call)
    q.thinking = {"type": "effort", "effort": "high"}
    assert q({"ticker": "SYNTH"}, {"schema": {}}) == {"model": {}, "scenarios": {}}
    assert len(seen) == 2 and seen[1]["thinking"] == {"type": "effort", "effort": "high"}


def test_r2_capo_settled_formato_vecchio_con_effort_cambiato_non_si_blocca(tmp_path, monkeypatch):
    from bellomberg.agents import capo
    from test_capo_collasso import _prepara, _msg, _bb, MEMO_VERO
    _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    posts = []
    def send(request):
        body = json.loads(request.content)
        posts.append(body)
        if len(posts) == 1:
            raise httpx.ReadTimeout("synthetic lost receipt", request=request)
        return _sse({"id": "gen-capo-2", "model": body["model"],
                     "choices": [{"index": 0, "delta": {"content": MEMO_VERO}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": .02}})
    client = _client(send)
    monkeypatch.setattr(capo, "OpenRouterClient", lambda **kw: client)
    board = _bb()
    board.persist_run_checkpoint = lambda *a: None
    monkeypatch.setenv("CAPO_EFFORT", "adaptive")
    with llm_client.request_scope(_journal(tmp_path), phase="capo", agent="capo", round_n=3):
        memo, usage = capo.run_capo(board)
    assert usage["complete"] is False and len(posts) == 1
    [row] = RequestJournal.unknown_requests(_journal(tmp_path).path)
    RequestJournal.settle_unknown(_journal(tmp_path).path, row["request_id"], charged_usd="0.01",
                                  generation_id=GEN, provider_generation={}, lookup_sha256="x")
    frozen = deepcopy(board.data["_capo_request"])
    frozen.pop("thinking")                     # checkpoint nel formato vecchio
    monkeypatch.setenv("CAPO_EFFORT", "high")  # effort cambiato
    restored = _bb()
    restored.data["_capo_request"] = frozen
    with llm_client.request_scope(_journal(tmp_path), phase="capo", agent="capo", round_n=3):
        memo2, usage2 = capo.run_capo(restored)
    assert "[CAPO ERROR]" not in memo2 and usage2["complete"] is True
    assert len(posts) == 2   # la riga settled (pagata SENZA risposta) non blocca: lavoro rifatto


# ------------------------------------------------------------------------------------ R3
def test_r3_stream_completo_senza_usage_dopo_la_riconciliazione_non_si_ripaga(tmp_path):
    posts = []
    def send(request):
        body = json.loads(request.content)
        posts.append(body)
        return _sse({"id": GEN, "model": body["model"],
                     "choices": [{"index": 0, "delta": {"content": "Risposta pagata."}, "finish_reason": None}]},
                    {"id": GEN, "model": body["model"],
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    client = _client(send)
    kw = dict(model="acme/desk", max_tokens=100, messages=[{"role": "user", "content": "lavoro"}])
    scope = dict(phase="weekly", agent="macro", round_n=1)
    with llm_client.request_scope(_journal(tmp_path), **scope):
        with pytest.raises(RequestBlocked):
            with client.messages.stream(**kw) as s:
                s.get_final_message()
    path = _journal(tmp_path).path
    [row] = RequestJournal.unknown_requests(path)
    assert _journal(tmp_path).summary()["cost_usd"] is None   # n.d. dichiarato, mai zero
    assert RequestJournal.settle_unknown(path, row["request_id"], charged_usd="0.00004",
                                         generation_id=GEN, provider_generation={}, lookup_sha256="x")
    summary = _journal(tmp_path).summary()
    assert summary["unknown_requests"] == 0 and summary["cost_usd"] == pytest.approx(0.00004)
    for _ in range(2):
        with llm_client.request_scope(_journal(tmp_path), **scope):
            with client.messages.stream(**kw) as s:
                msg = s.get_final_message()
        assert msg.content[0].text == "Risposta pagata."
        assert msg.usage.cost_usd == pytest.approx(0.00004)   # il costo MISURATO della riconciliazione
    assert len(posts) == 1
    assert _journal(tmp_path).summary()["cost_usd"] == pytest.approx(0.00004)


# ------------------------------------------------------------------------------------ R4
def test_r4_il_chunk_finale_oltre_il_tetto_non_scarta_una_risposta_completa(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "DURATA_MAX_STREAM_S", 0.12)
    dati = {"id": GEN, "model": "acme/m", "choices": [{"index": 0, "delta": {"content": "tutto"}, "finish_reason": None}]}
    fine = {"id": GEN, "model": "acme/m", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.00001}}
    client = _client(lambda r: httpx.Response(200, content=_ritarda_finale(dati, fine)(),
                                              headers={"content-type": "text/event-stream"}))
    with llm_client.request_scope(_journal(tmp_path), phase="weekly", agent="quant", round_n=1):
        with client.messages.stream(model="acme/m", max_tokens=10,
                                    messages=[{"role": "user", "content": "u"}]) as s:
            msg = s.get_final_message()
    assert msg.content[0].text == "tutto"
    assert _journal(tmp_path).summary()["cost_usd"] == pytest.approx(0.00001)


def _ritarda_finale(dati, fine):
    def corpo():
        yield ("data: " + json.dumps(dati) + "\n\n").encode()
        time.sleep(0.25)   # il chunk FINALE arriva oltre il tetto
        yield ("data: " + json.dumps(fine) + "\n\n").encode()
        yield b"data: [DONE]\n\n"
    return corpo


def test_r4_stream_di_soli_keepalive_resta_riconciliabile_con_l_id_dell_header(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "DURATA_MAX_STREAM_S", 0.1)
    def corpo():
        for _ in range(20):
            time.sleep(0.02)
            yield b": OPENROUTER PROCESSING\n\n"
        yield b"data: [DONE]\n\n"
    client = _client(lambda r: httpx.Response(200, content=corpo(), headers={
        "content-type": "text/event-stream", "x-generation-id": GEN}))
    with llm_client.request_scope(_journal(tmp_path), phase="weekly", agent="options", round_n=1):
        with pytest.raises(llm_client.APITimeoutError, match="durata totale"):
            with client.messages.stream(model="acme/m", max_tokens=10,
                                        messages=[{"role": "user", "content": "u"}]) as s:
                s.get_final_message()
    from bellomberg.core.generation_lookup import generation_id_from
    [row] = RequestJournal.unknown_requests(_journal(tmp_path).path)
    assert generation_id_from(row["receipt"]) == GEN


# ------------------------------------------------------------------------------------ R5
def test_r5_riga_reserved_dopo_un_crash_duro_e_visibile_e_riconciliabile(tmp_path):
    journal = _journal(tmp_path)
    body = {"model": "acme/m", "max_tokens": 10, "messages": [{"role": "user", "content": "u"}]}
    request_id, _wire, saved = journal.prepare(body, {"phase": "weekly", "agent": "macro", "round_n": 1})
    assert saved is None
    journal.checkpoint(request_id, {"id": GEN, "model": "acme/m", "choices": []})
    # crash duro: nessun fail/receive. Il processo nuovo vede la riga e il suo id.
    [row] = RequestJournal.unknown_requests(journal.path)
    assert row["request_id"] == request_id and row["state"] == "reserved"
    from bellomberg.core.generation_lookup import generation_id_from
    assert generation_id_from(row["receipt"]) == GEN
    assert RequestJournal.settle_unknown(journal.path, request_id, charged_usd="0.00002",
                                         generation_id=GEN, provider_generation={}, lookup_sha256="x")
    assert _journal(tmp_path).summary()["unknown_requests"] == 0


# ------------------------------------------------------------------------------------ R6
def test_r6_fra_due_righe_legacy_del_capo_vince_quella_ricevuta(tmp_path, monkeypatch):
    import bellomberg.core.request_journal as rj
    journal = _journal(tmp_path)
    scope = {"phase": "capo", "agent": "capo", "round_n": 3}
    base = {"model": "acme/capo", "max_tokens": 10, "messages": [{"role": "user", "content": "memo"}]}
    risposta = {"id": "gen-r", "model": "acme/capo", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "memo pagato"}}], "usage": {"cost": 0.00001}}
    monkeypatch.setattr(rj, "_CAPO_REASONING_ON_WIRE", ())   # righe create senza riconoscimento
    rid, _, _ = journal.prepare({**base, "reasoning": {"effort": "high"}}, scope)
    assert journal.receive(rid, risposta) == "received"
    rid2, _, _ = journal.prepare({**base, "reasoning": {"effort": "low"}}, scope)
    journal.fail(rid2, TimeoutError("lost"))
    monkeypatch.undo()
    # «low» viene prima di «high» nell'ordine dei candidati: deve vincere la riga ricevuta
    _, _, saved = _journal(tmp_path).prepare({**base, "reasoning": {"effort": "max"}}, scope)
    assert saved is not None and saved["choices"][0]["message"]["content"] == "memo pagato"


# ------------------------------------------------------------------------------------ R7
def test_r7_effort_non_valido_rifiutato_prima_di_create_run(monkeypatch, tmp_path):
    from bellomberg.agents import consigliere_multi as cm
    import bellomberg.agents.weekly_lifecycle as wl
    import bellomberg.core.paths as paths
    # run_multi_agent prende il lock dei comitati pagati (DATA_DIR/committee_paid_run.lock)
    # prima di validare l'effort: senza questo il test apriva il lock nella cartella dati vera
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    for nome in llm_client.DEFAULT_EFFORT:
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("REFLECTION_EFFORT", "turbo")
    creati = []
    monkeypatch.setattr(wl, "create_run", lambda *a, **k: creati.append("create_run"))
    monkeypatch.setattr(wl, "require_existing_database", lambda *a, **k: creati.append("db"))
    monkeypatch.setattr(cm, "MemoryDB", lambda *a, **k: creati.append("MemoryDB"))
    with pytest.raises(llm_client.ConfigurazioneLLMMancante, match="REFLECTION_EFFORT"):
        cm.run_multi_agent()
    assert creati == []
