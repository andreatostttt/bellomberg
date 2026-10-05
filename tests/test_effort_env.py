"""Effort di ragionamento per fase dal .env (decisione maintainer E, integrazione 04/10).

Variabile assente = default documentato (tabella di Andrea del 02/10); presente ma vuota o
non valida = ConfigurazioneLLMMancante che NOMINA la variabile, mai un default silenzioso.
Ogni valore passa da _reasoning_openai: su uno slug z-ai/ (GLM) nessun effort esplicito parte.
Zero rete: trasporto httpx finto, modelli inventati.
"""
import json

import httpx
import pytest

from bellomberg.core import llm_client
from bellomberg.core.llm_client import ConfigurazioneLLMMancante

EFFORT_VARS = tuple(llm_client.DEFAULT_EFFORT)


@pytest.fixture
def env_pulito(monkeypatch):
    """Nessuna variabile effort dal .env vero: si collaudano i default."""
    for nome in EFFORT_VARS:
        monkeypatch.delenv(nome, raising=False)
    return monkeypatch


def test_le_variabili_lette_sono_esattamente_quelle_documentate():
    assert set(llm_client._letture_effort()) == set(llm_client.DEFAULT_EFFORT)
    assert all(v is None or v in llm_client.VALORI_EFFORT
               for v in llm_client.DEFAULT_EFFORT.values())


@pytest.mark.parametrize(("agente", "round_n", "expected"), [
    # Tabella di Andrea: R0 low per tutti; R1/R2 high per fundamentals/quant/options/macro;
    # crypto ed eventdesk adaptive.
    *[(desk, 0, {"type": "effort", "effort": "low"}) for desk in llm_client.DESK],
    *[(desk, r, {"type": "effort", "effort": "high"})
      for desk in ("fundamentals", "quant", "options", "macro") for r in (1, 2)],
    *[(desk, r, {"type": "adaptive"}) for desk in ("crypto", "eventdesk") for r in (1, 2)],
])
def test_default_riproducono_la_tabella_di_andrea(env_pulito, agente, round_n, expected):
    assert llm_client.thinking_consigliere("acme/desk-model", agente=agente,
                                           round_n=round_n) == expected


def test_default_fasi_singole(env_pulito):
    assert llm_client.thinking_fase("capo") == {"type": "effort", "effort": "high"}
    assert llm_client.thinking_fase("red_team") == {"type": "effort", "effort": "high"}
    assert llm_client.thinking_fase("reflection") == {"type": "effort", "effort": "low"}


def test_default_di_round_e_override_di_desk(env_pulito):
    env_pulito.setenv("CONSIGLIERE_R0_EFFORT", "minimal")
    env_pulito.setenv("CONSIGLIERE_R1_EFFORT", "medium")
    env_pulito.setenv("CONSIGLIERE_R2_EFFORT", "xhigh")
    env_pulito.setenv("CONSIGLIERE_CRYPTO_EFFORT", "max")
    env_pulito.setenv("CONSIGLIERE_QUANT_EFFORT", "adaptive")
    tc = llm_client.thinking_consigliere
    # R0: una variabile per tutti i desk, l'override di desk non vale in R0
    assert tc("acme/m", agente="crypto", round_n=0) == {"type": "effort", "effort": "minimal"}
    # R1/R2: default di round per i desk senza override
    assert tc("acme/m", agente="macro", round_n=1) == {"type": "effort", "effort": "medium"}
    assert tc("acme/m", agente="macro", round_n=2) == {"type": "effort", "effort": "xhigh"}
    # override del desk, per R1 e R2
    assert tc("acme/m", agente="crypto", round_n=1) == {"type": "effort", "effort": "max"}
    assert tc("acme/m", agente="crypto", round_n=2) == {"type": "effort", "effort": "max"}
    assert tc("acme/m", agente="quant", round_n=2) == {"type": "adaptive"}
    # eventdesk senza variabile: il suo default (adaptive), non il default di round
    assert tc("acme/m", agente="eventdesk", round_n=1) == {"type": "adaptive"}


def test_fasi_singole_dal_env(env_pulito):
    env_pulito.setenv("CAPO_EFFORT", "max")
    env_pulito.setenv("RED_TEAM_EFFORT", "adaptive")
    env_pulito.setenv("REFLECTION_EFFORT", "disabled")
    assert llm_client.thinking_fase("capo") == {"type": "effort", "effort": "max"}
    assert llm_client.thinking_fase("red_team") == {"type": "adaptive"}
    assert llm_client.thinking_fase("reflection") == {"type": "disabled"}


@pytest.mark.parametrize("nome", EFFORT_VARS)
@pytest.mark.parametrize("valore", ["", "   "])
def test_variabile_presente_ma_vuota_e_un_errore_col_nome(env_pulito, nome, valore):
    env_pulito.setenv(nome, valore)
    with pytest.raises(ConfigurazioneLLMMancante) as exc:
        llm_client.effort_env(nome)
    assert exc.value.variabile == nome and nome in str(exc.value)
    assert "vuota" in str(exc.value)


@pytest.mark.parametrize("nome", EFFORT_VARS)
@pytest.mark.parametrize("valore", ["HIGH", "hihg", "auto", "0", "none"])
def test_valore_non_valido_e_un_errore_col_nome(env_pulito, nome, valore):
    env_pulito.setenv(nome, valore)
    with pytest.raises(ConfigurazioneLLMMancante) as exc:
        llm_client.effort_env(nome)
    assert exc.value.variabile == nome and nome in str(exc.value)
    assert repr(valore) in str(exc.value)


def test_errore_arriva_dal_call_site_e_non_ripiega(env_pulito):
    env_pulito.setenv("CONSIGLIERE_R1_EFFORT", "")
    with pytest.raises(ConfigurazioneLLMMancante, match="CONSIGLIERE_R1_EFFORT"):
        llm_client.thinking_consigliere("acme/m", agente="fundamentals", round_n=1)
    env_pulito.setenv("CAPO_EFFORT", "turbo")
    with pytest.raises(ConfigurazioneLLMMancante, match="CAPO_EFFORT"):
        llm_client.thinking_fase("capo")
    with pytest.raises(ValueError, match="round"):
        llm_client.thinking_consigliere("acme/m", agente="macro", round_n=3)


# ---------------------------------------------------------------- guardia z-ai/ (GLM)
def _risposta(model):
    return {"id": "gen-acme", "model": model, "provider": "ProviderFinto",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.00001,
                      "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}}


def _client_registra(model, stream=False):
    corpi = []

    def gestore(req):
        corpi.append(json.loads(req.content))
        if stream:
            d = _risposta(model)
            chunk = {"id": d["id"], "model": model, "usage": d["usage"],
                     "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
        return httpx.Response(200, json=_risposta(model))

    client = llm_client.OpenRouterClient(api_key="sk-finta", max_retries=0,
                                         trasporto=httpx.MockTransport(gestore))
    return client, corpi


@pytest.mark.parametrize("stream", [False, True], ids=["create", "stream"])
def test_glm_con_effort_high_dal_env_non_invia_reasoning(env_pulito, stream):
    """z-ai model + effort high dal .env -> nessun parametro reasoning (misurato 05/09:
    su GLM ogni effort esplicito azzera il ragionamento; omesso = acceso)."""
    env_pulito.setenv("CONSIGLIERE_R1_EFFORT", "high")
    env_pulito.setenv("CAPO_EFFORT", "high")
    for thinking in (llm_client.thinking_consigliere("z-ai/glm-finto", agente="quant", round_n=1),
                     llm_client.thinking_fase("capo")):
        assert thinking == {"type": "effort", "effort": "high"}
        client, corpi = _client_registra("z-ai/glm-finto", stream=stream)
        kw = dict(model="z-ai/glm-finto", max_tokens=50, thinking=thinking,
                  messages=[{"role": "user", "content": "ZZTEST"}])
        if stream:
            with client.messages.stream(**kw) as s:
                s.get_final_message()
        else:
            client.messages.create(**kw)
        assert len(corpi) == 1 and "reasoning" not in corpi[0]


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max"])
def test_glm_nessun_effort_esplicito_parte_mai(effort):
    corpo = llm_client.costruisci_corpo("z-ai/glm-finto", 50, [{"role": "user", "content": "ACME"}],
                                        thinking={"type": "effort", "effort": effort})
    assert "reasoning" not in corpo


def test_fuori_da_glm_l_effort_dal_env_parte(env_pulito):
    env_pulito.setenv("CONSIGLIERE_R1_EFFORT", "high")
    client, corpi = _client_registra("acme/desk-model")
    client.messages.create(model="acme/desk-model", max_tokens=50,
                           thinking=llm_client.thinking_consigliere("acme/desk-model", agente="quant",
                                                                    round_n=1),
                           messages=[{"role": "user", "content": "ZZTEST"}])
    assert corpi[0]["reasoning"] == {"effort": "high"}


# ---------------------------------------------------------------- call site
def test_capo_usa_capo_effort_dal_env(env_pulito):
    from bellomberg.agents import capo
    from test_capo_collasso import _prepara, _msg, _bb, MEMO_VERO
    env_pulito.setenv("CAPO_EFFORT", "medium")
    calls = _prepara(env_pulito, lambda *_: _msg(MEMO_VERO))
    capo.run_capo(_bb())
    assert calls and calls[0]["thinking"] == {"type": "effort", "effort": "medium"}


def test_capo_effort_vuota_e_dichiarata_senza_call(env_pulito):
    from bellomberg.agents import capo
    from test_capo_collasso import _prepara, _msg, _bb, MEMO_VERO
    env_pulito.setenv("CAPO_EFFORT", "")
    calls = _prepara(env_pulito, lambda *_: _msg(MEMO_VERO))
    memo, usage = capo.run_capo(_bb())
    assert not calls
    assert "CAPO_EFFORT" in memo and usage["api_calls"] == 0


# ------------------------------------------------- G7 (04/10): tabella congelata e preflight
def test_tabella_dei_default_congelata():
    """G7/M3: la tabella fase -> effort di default (valori di Andrea, approvati dal PM il
    04/10) e' FISSATA qui per esteso: un cambio di default deve toccare anche questo test,
    quindi e' sempre deliberato. Mai calcolarla dal modulo sotto test."""
    assert llm_client.DEFAULT_EFFORT == {
        "CONSIGLIERE_R0_EFFORT": "low",
        "CONSIGLIERE_R1_EFFORT": "high",
        "CONSIGLIERE_R2_EFFORT": "high",
        "CONSIGLIERE_MACRO_EFFORT": None,
        "CONSIGLIERE_QUANT_EFFORT": None,
        "CONSIGLIERE_OPTIONS_EFFORT": None,
        "CONSIGLIERE_FUNDAMENTALS_EFFORT": None,
        "CONSIGLIERE_CRYPTO_EFFORT": "adaptive",
        "CONSIGLIERE_EVENTDESK_EFFORT": "adaptive",
        "CAPO_EFFORT": "high",
        "RED_TEAM_EFFORT": "high",
        "REFLECTION_EFFORT": "low",
        "VALUATION_PREPARER_EFFORT": "high",
    }
    assert llm_client.VALORI_EFFORT == ("adaptive", "disabled", "minimal", "low", "medium",
                                        "high", "xhigh", "max")


def test_valida_effort_env_legge_tutte_le_variabili(env_pulito):
    assert llm_client.valida_effort_env() == llm_client.DEFAULT_EFFORT
    env_pulito.setenv("CAPO_EFFORT", "turbo")
    env_pulito.setenv("REFLECTION_EFFORT", "")
    with pytest.raises(ConfigurazioneLLMMancante) as exc:
        llm_client.valida_effort_env()
    assert exc.value.variabile == "CAPO_EFFORT"
    assert "turbo" in str(exc.value) and "REFLECTION_EFFORT" in str(exc.value)


def test_effort_sbagliato_ferma_la_run_prima_di_ogni_chiamata_pagata(env_pulito):
    """G7/M1: un CAPO_EFFORT (o qualunque *_EFFORT) sbagliato emergeva solo al Capo, a desk
    e red team gia' pagati. Ora la run del comitato si ferma all'avvio, col nome."""
    from bellomberg.agents import consigliere_multi as cm
    env_pulito.setenv("CAPO_EFFORT", "turbo")
    pagate = []
    env_pulito.setattr(cm, "print_banner", lambda: pagate.append("avvio oltre il preflight"))
    env_pulito.setattr(llm_client, "sonda_modelli", lambda *a, **k: pagate.append("sonda"))
    env_pulito.setattr(llm_client.OpenRouterClient, "__init__",
                       lambda *a, **k: pagate.append("client"))
    with pytest.raises(ConfigurazioneLLMMancante, match="CAPO_EFFORT"):
        cm._run_multi_agent(store=None, db=None)
    assert pagate == []
