"""llm_usage.py — chiamata singola a un modello su click, costo e ledger `llm_usage`.

PERCHE' ESISTE (04/10/2026, correzione G3 dell'import)
    Gli helper stavano in market_data/article_summary.py (sintesi di UN articolo) ma li usano
    anche market_data/filing_proposta_ai.py («Proponi con AI») e headline_translation.py:
    i filing dipendevano da un modulo news. Qui stanno in un modulo neutro, senza nulla di
    specifico delle notizie: il chiamante passa sempre `max_tokens` e `agent`.

CONTRATTO
    _call_model   UNA chiamata con thinking disabilitato e risposta JSON. I retry li fa SOLO il
                  client OpenRouter (max_retries su 408/409/429/5xx e rete): REV_G3 R3, prima un
                  secondo ciclo qui sopra arrivava a 4x3 = 12 POST per click.
    nuovo_client  client per le chiamate da click con timeout dichiarato (TIMEOUT_CLICK_S),
                  non i 600 s di default ritentati dal client.
    _cost         costo in EUR/USD dai token dichiarati; un prezzo o un cambio mancante
                  restano None con `cost_status` che dice perche' (mai uno zero finto).
    _record_usage scrive la riga nel ledger `llm_usage`; se fallisce lo dice nel log e non
                  fa perdere il risultato gia' pagato.
"""
from __future__ import annotations

from typing import Any, Dict

from bellomberg.storage.memory_db import MemoryDB

TIMEOUT_CLICK_S = 90.0   # per tentativo; il client ritenta al massimo max_retries volte


def _log(msg: str) -> None:
    print(f"[LLM_USAGE] {msg}", flush=True)


def nuovo_client():
    from bellomberg.core import llm_client as lc
    return lc.OpenRouterClient(timeout=TIMEOUT_CLICK_S)


def _call_model(client, model: str, prompt: str, max_tokens: int):
    return client.messages.create(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "disabled"},
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )


def _usage_dict(usage) -> Dict[str, Any]:
    fields = {"in": "input_tokens", "out": "output_tokens",
              "cache_read": "cache_read_input_tokens", "cache_write": "cache_creation_input_tokens"}
    out = {}
    for key, attr in fields.items():
        value = getattr(usage, attr, None) if usage is not None else None
        if value is not None:
            out[key] = value
    cost_usd = getattr(usage, "cost_usd", None) if usage is not None else None
    if cost_usd is not None:
        out["cost_usd"] = cost_usd
    return out


def _cost(model: str, usage) -> Dict[str, Any]:
    tokens = _usage_dict(usage)
    reported_usd = tokens.get("cost_usd")
    try:
        from bellomberg.core.llm_pricing import cost_eur
        result = cost_eur(model, tokens, None)
        cost = result.get("cost")
        status = result.get("status") or "n.d."
        if cost is None and status == "ok":
            status = "fx_unavailable"
        usd = reported_usd
        if usd is None:
            usd = (result.get("breakdown") or {}).get("cost_usd")
        return {"cost_eur": float(cost) if cost is not None else None,
                "cost_usd": float(usd) if usd is not None else None,
                "cost_status": status, "fx_rate": result.get("fx_rate"),
                "fx_source": result.get("fx_source"), "tokens": tokens}
    except Exception as exc:
        return {"cost_eur": None, "cost_usd": float(reported_usd) if reported_usd is not None else None,
                "cost_status": f"error: {type(exc).__name__}", "fx_rate": None, "fx_source": None,
                "tokens": tokens}


def _record_usage(model: str, cost: Dict[str, Any], api_calls: int, duration_s: float,
                  agent: str) -> None:
    tokens = cost["tokens"]
    entry = {"agent": agent, "round": None, "model": model,
             "in": tokens.get("in"), "out": tokens.get("out"),
             "cache_read": tokens.get("cache_read"), "cache_write": tokens.get("cache_write"),
             "api_calls": api_calls, "duration_s": duration_s,
             "cost_eur": cost["cost_eur"], "fx_rate": cost["fx_rate"], "fx_source": cost["fx_source"],
             "status": cost["cost_status"], "cache_ttl": None}
    try:
        MemoryDB().save_llm_usage(None, [entry])
    except Exception as exc:   # il ledger non deve far perdere un risultato gia' pagato
        _log(f"llm_usage not recorded: {type(exc).__name__}: {exc}")
