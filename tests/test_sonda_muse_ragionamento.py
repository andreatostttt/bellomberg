"""Run 05/10 19:26: la sonda mandava a Muse {"enabled": false}; sotto journal il retry minimal e' vietato,
il 400 restava 'unknown' e bloccava tutta la run. Qui: Muse parte gia' minimal, la sonda chiede >= 16 token,
una variante di instradamento OpenRouter (:exacto) trova il listino del modello base, dichiarato."""
import inspect

import pytest

from bellomberg.core import llm_client
from bellomberg.valuation import preparation_ai


def test_nessun_modello_parte_mai_col_ragionamento_spento():
    """Regola PM 05/10/2026: «disabled» vale «adaptive» per OGNI modello (Muse, Opus 5.5, gli altri)."""
    llm_client.RAGIONAMENTO_OBBLIGATORIO.clear()
    for slug in (llm_client.MUSE_STANDARD, "zz/modello-finto", "anthropic/finto-opus"):
        assert llm_client._reasoning_openai({"type": "disabled"}, slug) == llm_client.REASONING_ADAPTIVE
    assert llm_client._reasoning_openai({"type": "disabled"}, "z-ai/finto") is None   # nativo acceso


def test_sonda_ha_spazio_oltre_il_budget_minimo_di_ragionamento():
    assert inspect.signature(llm_client.sonda_modelli).parameters["max_tokens"].default > 1024


class _Risposta:
    def __init__(self, dati):
        self._dati = dati

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": self._dati}


def _catalogo(monkeypatch, righe):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Risposta(righe))


def test_variante_di_instradamento_usa_il_listino_base_dichiarato(monkeypatch):
    _catalogo(monkeypatch, [{"id": "zz/finto-7", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}])
    meta = preparation_ai.live_metadata("zz/finto-7:exacto")
    assert meta["pricing"]["completion"] == "0.000002"
    assert meta["pricing_basis"] == "base model zz/finto-7 (routing variant :exacto not listed)"


def test_variante_sconosciuta_o_base_assente_resta_errore(monkeypatch):
    _catalogo(monkeypatch, [{"id": "zz/finto-7", "pricing": {}}])
    for slug in ("zz/finto-7:qualcosa", "zz/assente:exacto"):
        with pytest.raises(ValueError):
            preparation_ai.live_metadata(slug)


def test_modello_listato_esatto_non_porta_la_dichiarazione(monkeypatch):
    _catalogo(monkeypatch, [{"id": "zz/finto-7", "pricing": {}}])
    assert "pricing_basis" not in preparation_ai.live_metadata("zz/finto-7")


def test_variante_exacto_che_risponde_col_nome_base_e_una_ricevuta_misurata(tmp_path):
    """Run 05/10 20:19: ':exacto' ha risposto col modello base, il journal l'ha tenuta 'unknown'
    e la sonda ha fermato la run. Ora e' 'received', col costo vero e la base dichiarata."""
    import json, sqlite3
    import httpx
    from bellomberg.core.request_journal import RequestJournal

    def send(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={"id": "gen-zz-1", "model": body["model"].partition(":")[0],
            "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}], "usage": {"cost": 0.000003}})
    client = llm_client.OpenRouterClient(api_key="offline", trasporto=httpx.MockTransport(send))
    journal = RequestJournal(tmp_path / "j.sqlite", run_id="zz-run", authorization={"source": "test"},
        authorized_usd="1", metadata=lambda model: {"id": model, "context_length": 100000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    with llm_client.request_scope(journal, phase="sonda"):
        esiti = llm_client.sonda_modelli(["zz/finto-7:exacto"], client=client)
    assert esiti["zz/finto-7:exacto"]["ok"] is True
    with sqlite3.connect(journal.path) as db:
        state, receipt = db.execute("SELECT state, receipt FROM requests").fetchone()
    assert state == "received"
    assert json.loads(receipt)["identity_basis"] == "routing variant :exacto answered as base model"
    assert journal.summary()["unknown_requests"] == 0


def test_risposta_di_un_altro_modello_resta_incerta(tmp_path):
    import json
    import httpx
    from bellomberg.core.request_journal import RequestJournal

    def send(request):
        return httpx.Response(200, json={"id": "gen-zz-2", "model": "zz/altro",
            "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}], "usage": {"cost": 0.000003}})
    client = llm_client.OpenRouterClient(api_key="offline", trasporto=httpx.MockTransport(send))
    journal = RequestJournal(tmp_path / "j.sqlite", run_id="zz-run", authorization={"source": "test"},
        authorized_usd="1", metadata=lambda model: {"id": model, "context_length": 100000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    with llm_client.request_scope(journal, phase="sonda"):
        llm_client.sonda_modelli(["zz/finto-7:exacto"], client=client)
    assert journal.summary()["unknown_requests"] == 1
