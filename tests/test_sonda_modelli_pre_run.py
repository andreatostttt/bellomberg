# -*- coding: utf-8 -*-
"""Sonda dei modelli PRIMA del Round 0: un modello respinto si scopre subito (audit 10/09).

Run 1 del 10/09: il modello del red team era bloccato da OpenRouter (HTTP 403) e lo si e'
scoperto alle 16:15, quarantacinque minuti dopo l'avvio, a Round 0 e Round 1 gia' pagati.
L'HEALTH-CHECK pre-run pingava solo i tool locali. La sonda chiama ogni slug distinto una
volta con pochi token; l'esito e' dichiarato (OK/KO con la causa) e la run NON si ferma:
il red team resta best-effort, i desk hanno il loro modello.

Client finto; niente rete.
"""
from bellomberg.core import llm_client as lc


class _Resp:
    def __init__(self):
        self.content = []
        self.stop_reason = "end_turn"
        self.usage = None


class _Client:
    def __init__(self, ko):
        self.ko = ko
        self.messages = self
        self.chiamate = []

    def create(self, **kw):
        self.chiamate.append(kw)
        if kw["model"] in self.ko:
            raise RuntimeError(self.ko[kw["model"]])
        return _Resp()


def test_ogni_slug_distinto_e_sondato_una_volta_con_pochi_token():
    cl = _Client(ko={})
    esiti = lc.sonda_modelli(["a/uno", "a/uno", "b/due"], client=cl)
    assert [c["model"] for c in cl.chiamate] == ["a/uno", "b/due"]
    assert all(c["max_tokens"] == 2048 for c in cl.chiamate)   # ragionamento acceso: oltre il budget minimo 1024
    assert all(c["thinking"] == {"type": "effort", "effort": "minimal"} for c in cl.chiamate)
    assert esiti == {"a/uno": {"ok": True, "motivo": None}, "b/due": {"ok": True, "motivo": None}} or \
        all(e["ok"] and e["motivo"] is None for e in esiti.values())


def test_il_modello_respinto_e_ko_con_la_causa_e_gli_altri_restano_ok():
    cl = _Client(ko={"m/bloccato": "HTTP 403: This model requires you to complete the following before use: 18+ age confirmation"})
    esiti = lc.sonda_modelli(["m/bloccato", "a/uno"], client=cl)
    assert esiti["a/uno"]["ok"] is True
    assert esiti["m/bloccato"]["ok"] is False and "403" in esiti["m/bloccato"]["motivo"]


def test_senza_slug_nessuna_chiamata():
    cl = _Client(ko={})
    assert lc.sonda_modelli([], client=cl) == {}
    assert cl.chiamate == []


def test_la_riga_di_log_nomina_ok_e_ko():
    cl = _Client(ko={"m/bloccato": "HTTP 403 gate"})
    esiti = lc.sonda_modelli(["m/bloccato", "a/uno"], client=cl)
    righe = lc.righe_log_sonda(esiti)
    assert any("[OK]" in r and "a/uno" in r for r in righe), righe
    assert any("[KO]" in r and "m/bloccato" in r and "403" in r for r in righe), righe


def test_sonda_has_real_receipts_and_is_reused_without_second_charge(tmp_path):
    import json
    import httpx
    from bellomberg.core.request_journal import RequestJournal
    calls = []
    def send(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"id": "probe-" + str(len(calls)), "model": body["model"],
            "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
            "usage": {"cost": 0.000001}})
    client = lc.OpenRouterClient(api_key="offline", trasporto=httpx.MockTransport(send))
    journal = RequestJournal(tmp_path / "sonda.sqlite", run_id="probe-run",
        authorization={"source": "test"}, authorized_usd="1", metadata=lambda model: {
            "id": model, "context_length": 100000, "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    for _ in range(2):
        with lc.request_scope(journal, phase="sonda"):
            receipts = lc.sonda_modelli(["test/one", "test/two", "test/one"], client=client)
        assert all(row["ok"] and row["request_id"] and row["response_id"]
                   and row["cost_usd"] == 0.000001 for row in receipts.values())
    assert len(calls) == 2 and journal.summary()["request_count"] == 2
    assert journal.summary()["cost_usd"] == 0.000002
