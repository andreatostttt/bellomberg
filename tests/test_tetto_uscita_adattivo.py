# -*- coding: utf-8 -*-
"""Tetto di uscita ADATTIVO (MOD-CAP, 06/10/2026, Opus 5.5).

Decisione PM: «di default 128.000, ma se lancio un modello con un tetto minore si adatta in
automatico nella chiamata». Problema reale: chi mette google/gemini-3.8-flash (tetto di uscita
65536 nella Models API) si vedeva rifiutare ogni chiamata di analisti/Capo/Red Team perche' il
codice chiedeva 128000. Il 05/10 era curata solo la run settimanale del Red Team.

Cura di classe nel punto unico del client (llm_client.tetto_uscita, prima del preventivo del
registro e dell'invio). Qui si prova:
  1. OGNI slug del .env.example (+ gemini-3.8-flash e deepseek-v4-flash-0731) in OGNI ruolo, col
     client VERO, il registro VERO e il fornitore finto di trasporto (rifiuta come OpenRouter):
     sul filo arriva min(richiesti, tetto) e il fornitore non rifiuta nulla;
  2. dichiarazione UNA volta per (ruolo, modello), col testo deciso;
  3. fuori run: listino per processo con TTL su orologio monotonico; guasto dichiarato;
  4. riprese: una richiesta gia' registrata col cap RICHIESTO (pagata o incerta) non si rimanda
     mai con un corpo diverso; una mai inviata parte col cap adattato;
  5. proprietario esterno (Trade Idea, preparer): il client non tocca il corpo;
  6. la sonda dei modelli riporta tetto e adattamento; il preparer settimanale si adatta.
Fixture: tests/fixtures/openrouter_models_snapshot.json (campi pubblici della Models API, GET
gratuita). Ticker e numeri inventati."""
import json

import httpx
import pytest

import fornitore_openrouter_finto as ff
from bellomberg.core import llm_client
from bellomberg.core.request_journal import RequestBlocked, RequestJournal, request_scope

CATALOGO, DOC = ff.carica_catalogo()
ESEMPIO = ff.variabili_env(ff.FIXTURE.parents[2] / ".env.example")
GEMINI = "google/gemini-3.8-flash"
DEEPSEEK = "deepseek/deepseek-v4-flash-0731"
SLUG = sorted({v for v in ESEMPIO.values() if v} | {GEMINI, DEEPSEEK})

# (etichetta, scope (phase, agent, round_n) o None = fuori run, max_tokens richiesto, stream)
RUOLI = [
    ("desk R0", ("specialist", "macro", 0), 128000, True),
    ("desk R1", ("specialist", "fundamentals", 1), 128000, True),
    ("desk R2", ("specialist", "crypto", 2), 128000, True),
    ("red team", ("red_team", "_red_team", 1), 128000, False),
    ("capo", ("capo", "capo", 3), 128000, True),
    ("reflection", ("reflection", "_reflection", None), 8000, False),
    ("estrattore", ("action_extraction", "_action_table", None), 16000, False),
    ("sonda", ("model_probe", "_probe", None), 2048, False),
    ("chat", None, 128000, True),
    ("filing_judgment", None, 2500, False),
    ("notizie", None, 1500, False),
]


@pytest.fixture(autouse=True)
def stato_pulito(monkeypatch):
    monkeypatch.setattr(llm_client, "_TETTI_DICHIARATI", set())
    monkeypatch.setattr(llm_client, "_LISTINO_PROCESSO", {})


@pytest.fixture
def fornitore(monkeypatch, tmp_path):
    """Client VERO (nessun trasporto passato: e' quello di produzione) col fornitore finto al
    livello del trasporto, e GET /models servita dalla fixture."""
    f = ff.FornitoreFinto(CATALOGO)
    ff.monta_trasporto(llm_client, f, monkeypatch.setattr)
    f.models = ff.AdattatoreModels(DOC)
    ff.monta_requests_models(f.models, monkeypatch.setattr)
    return f


def _registro(tmp_path, nome="zz", metadata=None):
    return RequestJournal(tmp_path / (nome + "-requests.sqlite"), run_id="zz-" + nome,
                          authorization={"scope": "test sintetico MOD-CAP"}, metadata=metadata)


def _chiama(client, slug, richiesti, stream):
    kw = dict(model=slug, max_tokens=richiesti, thinking={"type": "effort", "effort": "high"},
              messages=[{"role": "user", "content": "analizza ZZTEST.MI a 123.45"}])
    if stream:
        with client.messages.stream(**kw) as s:
            return s.get_final_message()
    return client.messages.create(**kw)


def _tetto(slug):
    meta, _ = ff.metadati(CATALOGO, slug)
    return meta["top_provider"]["max_completion_tokens"]


# ----------------------------------------------------------------------------- 1. tutti gli slug x ruoli
@pytest.mark.parametrize("slug", SLUG)
@pytest.mark.parametrize("etichetta,scope,richiesti,stream", RUOLI, ids=[r[0] for r in RUOLI])
def test_ogni_slug_in_ogni_ruolo_arriva_al_fornitore_entro_il_tetto(slug, etichetta, scope, richiesti, stream,
                                                                  fornitore, tmp_path, capsys):
    client = llm_client.OpenRouterClient(max_retries=0)
    if scope is None:
        msg = _chiama(client, slug, richiesti, stream)
    else:
        registro = _registro(tmp_path)
        with request_scope(registro, phase=scope[0], agent=scope[1], round_n=scope[2]):
            msg = _chiama(client, slug, richiesti, stream)
        assert not registro.summary()["unknown_requests"]
    assert msg.stop_reason == "end_turn" and msg.content, fornitore.rifiuti()
    assert not fornitore.rifiuti(), fornitore.rifiuti()
    atteso = min(richiesti, _tetto(slug))
    assert [r["max_tokens"] for r in fornitore.registro] == [atteso]
    out = capsys.readouterr().out
    if atteso < richiesti:
        assert ("richiesti %d token, %s ne accetta %d: uso %d" % (richiesti, slug, atteso, atteso)) in out, out
    else:
        assert "ne accetta" not in out


def test_la_copertura_contiene_un_modello_sotto_il_default_del_pm():
    # Senza un tetto < 128000 fra gli slug la prova sopra non adatterebbe mai nulla.
    assert _tetto(GEMINI) == 65536 < llm_client.TETTO_USCITA_DEFAULT
    assert GEMINI in SLUG and DEEPSEEK in SLUG and set(v for v in ESEMPIO.values() if v) <= set(SLUG)


# ----------------------------------------------------------------------------- 2. dichiarazione
def test_dichiarato_una_volta_per_ruolo_e_modello(fornitore, tmp_path, capsys):
    client = llm_client.OpenRouterClient(max_retries=0)
    registro = _registro(tmp_path)
    for agente in ("macro", "macro", "quant"):
        with request_scope(registro, phase="specialist", agent=agente, round_n=1):
            client.messages.create(model=GEMINI, max_tokens=128000,
                                   messages=[{"role": "user", "content": "ZZTEST " + agente + str(len(fornitore.registro))}])
    out = capsys.readouterr().out
    riga = "specialist/macro/R1: richiesti 128000 token, %s ne accetta 65536: uso 65536" % GEMINI
    assert out.count(riga) == 1, out
    assert out.count("specialist/quant/R1: richiesti 128000 token") == 1
    assert [r["max_tokens"] for r in fornitore.registro] == [65536] * 3


def test_tetto_provider_esplicito_non_legge_nulla(monkeypatch):
    monkeypatch.setattr(llm_client, "tetto_provider_letto", lambda m: pytest.fail("letto"))
    assert llm_client.tetto_uscita("zz/x", 128000, ruolo="trade_idea", tetto_provider=65536) == 65536
    assert llm_client.tetto_uscita("zz/x", 1000, ruolo="trade_idea", tetto_provider=65536) == 1000
    for sbagliato in (0, -1, "65536", True, 1.5):
        with pytest.raises(TypeError):
            llm_client.tetto_uscita("zz/x", 128000, ruolo="t", tetto_provider=sbagliato)
    with pytest.raises(TypeError):
        llm_client.tetto_uscita("zz/x", "128000", ruolo="t", tetto_provider=65536)


# ----------------------------------------------------------------------------- 3. fuori run
def test_fuori_run_listino_per_processo_con_ttl_monotonico(monkeypatch):
    letture, orologio = [], {"t": 1000.0}
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: orologio["t"])
    from bellomberg.valuation import preparation_ai
    monkeypatch.setattr(preparation_ai, "live_metadata",
                        lambda m: letture.append(m) or {"id": m, "top_provider": {"max_completion_tokens": 65536}})
    assert llm_client.tetto_uscita(GEMINI, 128000, ruolo="chat") == 65536
    orologio["t"] += llm_client.LISTINO_TTL_S - 1
    assert llm_client.tetto_uscita(GEMINI, 128000, ruolo="chat") == 65536
    assert letture == [GEMINI]
    orologio["t"] += 2                                   # oltre il TTL: si rilegge
    assert llm_client.tetto_uscita(GEMINI, 128000, ruolo="chat") == 65536
    assert letture == [GEMINI, GEMINI]


def test_fuori_run_catalogo_giu_dichiarato_e_ritentato_dopo_il_ttl_del_guasto(monkeypatch, capsys):
    letture, orologio = [], {"t": 50.0}
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: orologio["t"])
    from bellomberg.valuation import preparation_ai

    def giu(m):
        letture.append(m)
        raise ConnectionError("https://example.invalid/?segreto=zz")
    monkeypatch.setattr(preparation_ai, "live_metadata", giu)
    assert llm_client.tetto_uscita(GEMINI, 24000, ruolo="chat") == 24000
    assert llm_client.tetto_uscita(GEMINI, 24000, ruolo="chat") == 24000
    assert letture == [GEMINI]                           # il guasto si memorizza (niente 30 s a chiamata)
    orologio["t"] += llm_client.LISTINO_GUASTO_TTL_S + 1
    llm_client.tetto_uscita(GEMINI, 24000, ruolo="chat")
    assert letture == [GEMINI, GEMINI]
    out = capsys.readouterr().out
    assert out.count("chat: tetto di uscita di %s non verificato (listino non letto (ConnectionError))" % GEMINI) == 1
    assert "segreto" not in out


def test_fuori_run_chat_adattata_sul_filo(fornitore, capsys):
    client = llm_client.OpenRouterClient(max_retries=0)
    msg = _chiama(client, GEMINI, 128000, True)
    assert msg.stop_reason == "end_turn" and [r["max_tokens"] for r in fornitore.registro] == [65536]
    assert "chiamata fuori run: richiesti 128000 token" in capsys.readouterr().out
    _chiama(client, GEMINI, 128000, False)
    assert fornitore.models.letture == 1                 # una sola GET per processo (TTL)


def test_dentro_la_run_nessuna_lettura_fuori_dal_registro(fornitore, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "_listino_fuori_run", lambda m: pytest.fail("lettura fuori dal registro"))
    registro = _registro(tmp_path)
    client = llm_client.OpenRouterClient(max_retries=0)
    with request_scope(registro, phase="capo", agent="capo", round_n=3):
        _chiama(client, GEMINI, 128000, True)
    assert fornitore.models.letture == 1                 # tetto e preventivo: lo stesso listino


def test_registro_senza_listino_nessuna_spesa(fornitore, tmp_path, capsys):
    # Punto 3 dell'incarico: Models API irraggiungibile dentro una run -> il tetto resta il
    # richiesto (dichiarato) e il preventivo del registro rifiuta PRIMA dell'invio.
    def giu(model):
        raise ConnectionError("rete giu' (sintetico)")
    registro = _registro(tmp_path, metadata=giu)
    client = llm_client.OpenRouterClient(max_retries=0)
    with request_scope(registro, phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(ConnectionError):
            client.messages.create(model=GEMINI, max_tokens=128000, messages=[{"role": "user", "content": "ZZ"}])
    assert fornitore.registro == []                      # nessun invio, nessuna spesa
    assert registro.summary()["requests"] == []
    assert "non verificato (listino non letto (ConnectionError))" in capsys.readouterr().out


# ----------------------------------------------------------------------------- 4. riprese
def _listino_con_tetto(tetto):
    meta, _ = ff.metadati(CATALOGO, GEMINI)

    def leggi(model):
        return {**json.loads(json.dumps(meta)), "top_provider": {"max_completion_tokens": tetto}}
    return leggi


def _messaggio():
    return [{"role": "user", "content": "critica ZZTEST.MI (lavoro gia' pagato)"}]


def test_ripresa_richiesta_gia_pagata_al_cap_vecchio_rigiocata_mai_rimandata(fornitore, tmp_path):
    client = llm_client.OpenRouterClient(max_retries=0)
    prima = _registro(tmp_path, "r", metadata=_listino_con_tetto(200000))     # ieri: 128000 accettato
    fornitore.catalogo = {**CATALOGO, GEMINI: {**CATALOGO[GEMINI], "top_provider": {"max_completion_tokens": 200000}}}
    with request_scope(prima, phase="red_team", agent="_red_team", round_n=1):
        pagata = client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    assert [r["max_tokens"] for r in fornitore.registro] == [128000]
    fornitore.catalogo = CATALOGO                                             # oggi: tetto 65536
    dopo = _registro(tmp_path, "r", metadata=_listino_con_tetto(65536))
    with request_scope(dopo, phase="red_team", agent="_red_team", round_n=1):
        ripresa = client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    assert len(fornitore.registro) == 1                                       # nessun nuovo invio
    assert ripresa.replayed is True and ripresa.request_id == pagata.request_id
    assert [r["state"] for r in dopo.summary()["requests"]] == ["received"]


def test_ripresa_richiesta_incerta_al_cap_vecchio_bloccata_mai_rimandata(fornitore, tmp_path):
    client = llm_client.OpenRouterClient(max_retries=0)
    prima = _registro(tmp_path, "u", metadata=_listino_con_tetto(200000))
    fornitore.rifiuto = lambda corpo: (503, "synthetic outage")   # esito incerto (5xx dopo l'invio)
    with request_scope(prima, phase="capo", agent="capo", round_n=3):
        with pytest.raises(llm_client.APIStatusError):
            client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    assert [(r["status"], r["max_tokens"]) for r in fornitore.registro] == [(503, 128000)]
    del fornitore.rifiuto
    fornitore.registro.clear()
    dopo = _registro(tmp_path, "u", metadata=_listino_con_tetto(65536))
    with request_scope(dopo, phase="capo", agent="capo", round_n=3):
        with pytest.raises(RequestBlocked, match="not replayable"):
            client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    assert fornitore.registro == []                                           # il 65536 non e' partito


def test_ripresa_mai_inviata_parte_col_cap_adattato(fornitore, tmp_path):
    # Checkpoint salvato col cap 128000 e MAI inviato (il preventivo lo aveva rifiutato ieri):
    # nessuna riga nel registro -> oggi parte col tetto del provider.
    client = llm_client.OpenRouterClient(max_retries=0)
    registro = _registro(tmp_path, "m")
    with request_scope(registro, phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(ValueError, match="exceeds"):
            registro.prepare({"model": GEMINI, "max_tokens": 128000, "messages": []}, {"phase": "red_team"})
        client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    assert [r["max_tokens"] for r in fornitore.registro] == [65536]


def test_forma_precedente_chiusa_settled_non_si_rigioca(tmp_path):
    # Una forma col cap richiesto chiusa come settled (pagata, senza risposta utile) e' lavoro
    # chiuso: _find la ignora come le forme legacy (stessa regola, nessun blocco).
    registro = _registro(tmp_path, "s", metadata=_listino_con_tetto(200000))
    corpo = {"model": GEMINI, "max_tokens": 128000, "messages": _messaggio()}
    scope = {"phase": "red_team", "agent": "_red_team", "round_n": 1}
    request_id, _wire, _ = registro.prepare(corpo, scope)
    with registro._db() as db:
        db.execute("UPDATE requests SET state='settled', cost=0, receipt=json_set(receipt, '$.settlement', "
                   "json('{\"cost_nano\": 0, \"generation_id\": \"gen-zz\"}')) WHERE request_id=?", (request_id,))
    adattato = {**corpo, "max_tokens": 65536}
    nuovo_id, wire, salvata = registro.prepare(adattato, scope, forme_precedenti=[corpo])
    assert salvata is None and nuovo_id != request_id and wire["max_tokens"] == 65536


def test_capo_forma_al_cap_vecchio_con_altro_ragionamento_rigiocata(fornitore, tmp_path):
    client = llm_client.OpenRouterClient(max_retries=0)
    prima = _registro(tmp_path, "c", metadata=_listino_con_tetto(200000))
    fornitore.catalogo = {**CATALOGO, GEMINI: {**CATALOGO[GEMINI], "top_provider": {"max_completion_tokens": 200000}}}
    with request_scope(prima, phase="capo", agent="capo", round_n=3):
        with client.messages.stream(model=GEMINI, max_tokens=128000, thinking={"type": "adaptive"},
                                    messages=_messaggio()) as s:
            s.get_final_message()
    fornitore.catalogo = CATALOGO
    dopo = _registro(tmp_path, "c", metadata=_listino_con_tetto(65536))
    with request_scope(dopo, phase="capo", agent="capo", round_n=3):
        with client.messages.stream(model=GEMINI, max_tokens=128000, thinking={"type": "effort", "effort": "high"},
                                    messages=_messaggio()) as s:
            ripresa = s.get_final_message()
    assert len(fornitore.registro) == 1 and ripresa.replayed is True


# ----------------------------------------------------------------------------- 5. proprietario esterno
def test_proprietario_esterno_corpo_intatto(fornitore, monkeypatch):
    # Trade Idea / preparer: request_scope(None) -> il loro contratto (e il loro hash) fissano il
    # corpo; il client non legge listini e non tocca max_tokens (adatta il proprietario).
    monkeypatch.setattr(llm_client, "tetto_provider_letto", lambda m: pytest.fail("letto"))
    client = llm_client.OpenRouterClient(max_retries=0)
    with request_scope(None, phase="valuation_preparer"):
        client.messages.create(model=DEEPSEEK, max_tokens=128000, messages=_messaggio())
    assert [r["max_tokens"] for r in fornitore.registro] == [128000]


def test_client_con_trasporto_di_prova_fuori_run_non_legge_la_models_api(monkeypatch):
    monkeypatch.setattr(llm_client, "tetto_provider_letto", lambda m: pytest.fail("letto"))
    inviati = []

    def send(request):
        body = json.loads(request.content)
        inviati.append(body["max_tokens"])
        return httpx.Response(200, json={"id": "zz-1", "model": body["model"], "choices": [
            {"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"cost": 0.000001}})
    client = llm_client.OpenRouterClient(api_key="offline", trasporto=httpx.MockTransport(send))
    client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    assert inviati == [128000]


# ----------------------------------------------------------------------------- 6. sonda e preparer
def test_sonda_riporta_tetto_e_adattamento(fornitore, tmp_path):
    registro = _registro(tmp_path, "p")
    with request_scope(registro, phase="model_probe", agent="_probe"):
        esiti = llm_client.sonda_modelli([GEMINI, DEEPSEEK])
    assert esiti[GEMINI]["tetto_uscita"] == 65536 and esiti[DEEPSEEK]["tetto_uscita"] == _tetto(DEEPSEEK)
    assert "128000 -> 65536" in esiti[GEMINI]["adattamento"] and esiti[DEEPSEEK]["adattamento"] is None
    righe = llm_client.righe_log_sonda(esiti)
    assert any(GEMINI in r and "tetto di uscita 65536" in r and "128000 -> 65536" in r for r in righe), righe
    assert any(DEEPSEEK in r and "tetto di uscita %d" % _tetto(DEEPSEEK) in r for r in righe), righe
    assert fornitore.models.letture == 2                 # uno per modello, condiviso col preventivo


def test_sonda_tetto_non_letto_dichiarato(tmp_path):
    def giu(model):
        raise ConnectionError("giu'")

    class Client:
        _trasporto_di_prova = False

        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("ping non riuscito (sintetico)")
    with request_scope(_registro(tmp_path, "q", metadata=giu), phase="model_probe", agent="_probe"):
        esiti = llm_client.sonda_modelli([GEMINI], client=Client())
    assert esiti[GEMINI]["tetto_uscita"] is None and "ConnectionError" in esiti[GEMINI]["tetto_origine"]
    assert "tetto di uscita n.d." in llm_client.righe_log_sonda(esiti)[0]


def test_preparer_settimanale_adattato_al_tetto(monkeypatch, tmp_path, capsys):
    from bellomberg.valuation.preparation_ai import configured_proposer
    monkeypatch.setattr(llm_client, "modello", lambda *a: GEMINI)
    monkeypatch.setattr(llm_client, "_listino_fuori_run", lambda m: CATALOGO[GEMINI])
    proposer = configured_proposer(tmp_path / "prep.sqlite3", authorized_usd="1")
    assert proposer.max_tokens == 65536
    assert "valuation_preparer: richiesti 128000 token, %s ne accetta 65536" % GEMINI in capsys.readouterr().out
    monkeypatch.setattr(llm_client, "modello", lambda *a: DEEPSEEK)
    monkeypatch.setattr(llm_client, "_listino_fuori_run", lambda m: CATALOGO[DEEPSEEK])
    assert configured_proposer(tmp_path / "prep2.sqlite3", authorized_usd="1").max_tokens == 128000


# ----------------------------------------------------------------------------- 7. revisione R-MOD
# F1: il tetto CAMBIA fra il crash e la ripresa della stessa run. Il registro congela il tetto
# letto la prima volta (tabella output_caps): stesso lavoro = stesso corpo = stessa chiave.
SCOPE_RT = {"phase": "red_team", "agent": "_red_team", "round_n": 1}


def _incerta_poi_ripresa(fornitore, tmp_path, tetto_prima, tetto_dopo):
    client = llm_client.OpenRouterClient(max_retries=0)
    fornitore.catalogo = {**CATALOGO, GEMINI: {**CATALOGO[GEMINI], "top_provider": {"max_completion_tokens": tetto_prima}}}
    fornitore.rifiuto = lambda corpo: (503, "synthetic outage")
    with request_scope(_registro(tmp_path, "f1", metadata=_listino_con_tetto(tetto_prima)), **SCOPE_RT):
        with pytest.raises(llm_client.APIStatusError):
            client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    del fornitore.rifiuto
    primo = [r["max_tokens"] for r in fornitore.registro]
    fornitore.registro.clear()
    fornitore.catalogo = {**CATALOGO, GEMINI: {**CATALOGO[GEMINI], "top_provider": {"max_completion_tokens": tetto_dopo}}}
    with request_scope(_registro(tmp_path, "f1", metadata=_listino_con_tetto(tetto_dopo)), **SCOPE_RT):
        with pytest.raises(RequestBlocked):
            client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
    return primo, [r["max_tokens"] for r in fornitore.registro]


@pytest.mark.parametrize("prima,dopo,inviato", [(200000, 65536, 128000), (65536, 200000, 65536),
                                               (65536, 60000, 65536)])
def test_F1_tetto_cambiato_fra_crash_e_ripresa_incerta_bloccata(fornitore, tmp_path, prima, dopo, inviato):
    primo, ripresa = _incerta_poi_ripresa(fornitore, tmp_path, prima, dopo)
    assert primo == [inviato]
    assert ripresa == [], "doppia spesa: rimandata con " + str(ripresa)


def test_F1_tetto_congelato_per_run(tmp_path):
    letture = []
    registro = _registro(tmp_path, "cg", metadata=lambda m: letture.append(m) or _listino_con_tetto(65536)(m))
    assert registro.tetto_congelato(GEMINI) == (65536, False)
    di_nuovo = _registro(tmp_path, "cg", metadata=_listino_con_tetto(200000))     # ripresa: tetto salito
    assert di_nuovo.tetto_congelato(GEMINI) == (65536, True)
    with request_scope(di_nuovo, **SCOPE_RT):
        assert llm_client.tetto_uscita(GEMINI, 128000, ruolo="red_team") == 65536


@pytest.mark.parametrize("con_rilascio", [False, True])
def test_F1_tentativo_1_dopo_un_rilascio_ritrovato_in_ripresa(tmp_path, con_rilascio):
    corpo = {"model": GEMINI, "max_tokens": 128000, "messages": _messaggio()}
    prima = _registro(tmp_path, "t1", metadata=_listino_con_tetto(200000))
    rid0, _, _ = prima.prepare(corpo, SCOPE_RT)
    if con_rilascio:
        with prima._db() as db:
            db.execute("UPDATE requests SET state='released', cost=0, receipt=json_set(receipt,'$.release',"
                       " json('{\"billable\": false, \"reason\": \"sintetico\", \"evidence\": \"http 400 pre-invio\"}'))"
                       " WHERE request_id=?", (rid0,))
        rid1, _, _ = prima.prepare(corpo, SCOPE_RT)
        assert rid1 != rid0
    dopo = _registro(tmp_path, "t1", metadata=_listino_con_tetto(65536))
    with pytest.raises(RequestBlocked):
        dopo.prepare({**corpo, "max_tokens": 65536}, SCOPE_RT, forme_precedenti=[corpo])


def test_F1_rilascio_al_cap_adattato_apre_il_tentativo_successivo(tmp_path):
    # Il tentativo si chiude quando TUTTE le sue forme registrate sono chiuse: il nuovo tentativo
    # dopo un rilascio al cap adattato e' una richiesta nuova (non un blocco).
    corpo = {"model": GEMINI, "max_tokens": 128000, "messages": _messaggio()}
    adattato = {**corpo, "max_tokens": 65536}
    registro = _registro(tmp_path, "t2", metadata=_listino_con_tetto(65536))
    rid0, _, _ = registro.prepare(adattato, SCOPE_RT, forme_precedenti=[corpo])
    with registro._db() as db:
        db.execute("UPDATE requests SET state='released', cost=0, receipt=json_set(receipt,'$.release',"
                   " json('{\"billable\": false, \"reason\": \"sintetico\", \"evidence\": \"connessione mai aperta\"}'))"
                   " WHERE request_id=?", (rid0,))
    rid1, wire, salvata = registro.prepare(adattato, SCOPE_RT, forme_precedenti=[corpo])
    assert salvata is None and rid1 != rid0 and wire["max_tokens"] == 65536


@pytest.mark.parametrize("via", ["stream", "create"])
def test_F2_chat_async_non_ferma_il_loop_di_eventi(fornitore, monkeypatch, via):
    """La chat dell'app (AsyncOpenRouterClient) legge il listino fuori dal thread del loop."""
    import asyncio
    import time
    from bellomberg.valuation import preparation_ai
    meta, _ = ff.metadati(CATALOGO, GEMINI)

    def lenta(model):
        time.sleep(0.6)
        return meta
    monkeypatch.setattr(preparation_ai, "live_metadata", lenta)

    async def principale():
        battiti = []

        async def orologio():
            for _ in range(80):
                battiti.append(time.perf_counter())
                await asyncio.sleep(0.01)

        async def chat():
            client = llm_client.AsyncOpenRouterClient(api_key="sk-or-finta", max_retries=0)
            if via == "stream":
                async with client.messages.stream(model=GEMINI, max_tokens=128000, messages=_messaggio()) as s:
                    await s.get_final_message()
            else:
                await client.messages.create(model=GEMINI, max_tokens=128000, messages=_messaggio())
        t = asyncio.create_task(orologio())
        await asyncio.sleep(0.05)
        await chat()
        await t
        return max(b - a for a, b in zip(battiti, battiti[1:]))
    buco = asyncio.run(principale())
    assert [r["max_tokens"] for r in fornitore.registro] == [65536]
    assert buco < 0.3, "loop di eventi fermo per %.2f s durante la lettura del listino" % buco


def test_F1_preventivo_red_team_usa_il_tetto_congelato(tmp_path):
    # Il preventivo anticipato del Red Team valida lo STESSO cap che il client mandera': il tetto
    # congelato della run (65536), non quello vivo salito a 200000 dopo il crash.
    from bellomberg.agents import red_team
    _registro(tmp_path, "rt", metadata=_listino_con_tetto(65536)).tetto_congelato(GEMINI)
    ripresa = _registro(tmp_path, "rt", metadata=_listino_con_tetto(200000))
    with request_scope(ripresa, **SCOPE_RT):
        assert red_team._preventivo_prima_dell_invio(GEMINI, 128000) == 65536
