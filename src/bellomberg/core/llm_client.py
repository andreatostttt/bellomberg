"""
llm_client.py — OpenRouter con la SUPERFICIE dell'SDK Anthropic che Bellomberg usa.

PERCHE' ESISTE (05/09/2026, ordine del PM, Fable 5.1 chat backend `bellomberg-d8`)
    Il PM ha ordinato di usare la chiave di OpenRouter al posto di quella Anthropic, con un
    modello DIVERSO per ogni funzione (chat per agente, desk R0/R1/R2, Capo, red team,
    reflection, estrattore, briefing, classificatore news) scritto nel `.env`.
    Dieci call site del prodotto chiamavano `anthropic.Anthropic(...).messages.create/stream`
    e leggevano la risposta nella forma dell'SDK (`content[]` a blocchi, `stop_reason`,
    `usage.input_tokens`...). Riscriverli nel formato OpenAI avrebbe voluto dire rifare i
    tool loop dei desk e della chat e invalidare i client finti di 16 file di test: questo
    modulo parla con OpenRouter (chat/completions) ma ESPONE quella stessa superficie, cosi'
    i call site cambiano tre righe e i finti dei test restano validi.
    Spec: docs/superpowers/specs/2026-09-05-openrouter-migrazione-design.md.

COSA TRADUCE (richiesta)
    system (stringa o blocchi)      -> messaggio role=system
    messages a blocchi Anthropic    -> assistant.tool_calls + role=tool + user
    tools {name, input_schema}      -> {"type":"function","function":{name, parameters}}
    tool_choice {"type": none/any/tool} -> "none" / "required" / {"type":"function",...}
    thinking adaptive / disabled    -> reasoning REASONING_ADAPTIVE / REASONING_DISABLED
    cache_control                   -> conservato SOLO per gli slug in PREFISSI_CACHE_CONTROL
                                       (gli altri provider cachano da soli); sui tools non ha
                                       equivalente e si toglie sempre; l'header beta Anthropic
                                       non parte mai (OpenRouter legge il ttl dal cache_control).

COSA TRADUCE (risposta)
    message.content / tool_calls / reasoning -> blocchi text / tool_use / thinking
    finish_reason stop/length/tool_calls     -> end_turn / max_tokens / tool_use
    content_filter o message.refusal         -> "refusal" + stop_details (llm_refusal lo legge)
    usage: input_tokens = prompt_tokens - cached - cache_write (Anthropic li conta A PARTE e i
    call site lo assumono), cache_read/cache_write dai prompt_tokens_details, output_tokens =
    completion_tokens (ragionamento incluso, com'era), cost_usd = usage.cost di OpenRouter.
    Un campo assente resta None, mai 0 (regola 14/07: i call site distinguono «assente» da zero).

STREAMING
    `with client.messages.stream(...) as s:` (sync, il Capo: solo get_final_message()) e
    `async with ...: async for ev in s` (la chat): gli eventi hanno la forma SDK
    content_block_start / content_block_delta (text_delta, input_json_delta) / content_block_stop,
    ricomposti dai delta OpenAI da `_Ricomposizione` (pura, senza I/O, provata a parte).
    Un chunk con `error` a HTTP 200 (OpenRouter lo fa) solleva; uno stream chiuso senza
    finish_reason solleva APIConnectionError (mai un memo a meta' spacciato per intero).

ERRORI E RETRY
    HTTP >= 400 -> APIStatusError con .status_code (i retry a mano di news/briefing e la logica
    529 dei desk leggono quell'attributo); rete/timeout -> APIConnectionError/APITimeoutError.
    Retry interni come l'SDK: `max_retries` su STATUS_RETRY e sugli errori di connessione, con
    BACKOFF_S; poi si rilancia con la causa verbatim (error.message + metadata di OpenRouter).

MODELLI DAL .ENV (precedenza scritta dal PM)
    chat:        CHAT_<AGENTE>_MODEL se presente, altrimenti CHAT_MODEL
    consigliere: round 0 = CONSIGLIERE_R0_MODEL; R1/R2 = CONSIGLIERE_<DESK>_MODEL o CONSIGLIERE_MODEL
    capo / red_team / reflection / action_extractor / briefing / news_classifier / news_summary
    (opzionale, fuori da VARIABILI_BASE): una variabile
    Variabile BASE assente o vuota = ConfigurazioneLLMMancante col NOME della variabile, al
    momento della chiamata: nessun default nel codice (regola 14/07; le MODEL STRINGS del
    CLAUDE.md vivono nel .env dal 05/09 su ordine del PM).

EFFORT DAL .ENV (04/10, decisione maintainer)
    CONSIGLIERE_R0/R1/R2_EFFORT, CONSIGLIERE_<DESK>_EFFORT (override R1/R2), CAPO_EFFORT,
    RED_TEAM_EFFORT, REFLECTION_EFFORT, VALUATION_PREPARER_EFFORT: assente = default
    documentato in DEFAULT_EFFORT; vuota o non valida = ConfigurazioneLLMMancante col nome.
    Validate TUTTE all'avvio della run del comitato (valida_effort_env), prima di ogni
    chiamata pagata. Tutto passa da _reasoning_openai.
"""
from bellomberg.core.paths import PROJECT_ROOT
import json
import os
import time
import math
import asyncio
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from bellomberg.core.request_journal import current_request_scope, request_scope, RequestBlocked
from bellomberg.core.unbilled import provably_unbilled, unbilled_reason

import httpx
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

BASE_URL = "https://openrouter.ai/api/v1"
URL_CHAT = BASE_URL + "/chat/completions"
X_TITLE = "Bellomberg"
HTTP_REFERER = "https://bellomberg.local"
TIMEOUT_DEFAULT_S = 600.0
MAX_RETRIES_DEFAULT = 2
# Status che si ritentano (come l'SDK Anthropic: 408/409/429/5xx). 402 (crediti finiti),
# 401 e 400 NON si ritentano: sarebbero lo stesso errore ripetuto a pagamento di tempo.
STATUS_RETRY = (408, 409, 429, 500, 502, 503, 504)
BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 8.0)

# thinking Anthropic -> reasoning OpenRouter. «adaptive» non esiste su OpenRouter: medium e'
# il livello che `{"enabled": true}` accende di default (doc reasoning-tokens, 05/09); per
# Anthropic via OpenRouter vale budget = 0,5 x max_tokens, per Gemini thinkingLevel medium.
REASONING_ADAPTIVE = {"effort": "medium"}
REASONING_DISABLED = {"enabled": False}
# MISURATO 05/09 (sonda, tetto 800, prompt tipo briefing):
#   z-ai/glm-5.3-flash : senza parametro 522 token di ragionamento; QUALUNQUE effort (minimal,
#                        low, medium) o reasoning.max_tokens = 0 token; {"enabled": false} = HTTP 400
#                        «Reasoning is mandatory for this endpoint and cannot be disabled».
#   google/gemini-3.8-flash: senza parametro 764 (tetto raggiunto); minimal/low 0; medium 529; spento = 400.
#   deepseek/deepseek-v4-flash: senza 497; enabled false 0; minimal 469 (!); low 156; medium 85.
# Quindi: «adaptive» su uno slug z-ai/ = NESSUN parametro (e' l'unico modo di accenderlo, il tetto
# resta max_tokens); «disabled» su chi lo rifiuta = `effort: minimal` (0 token misurati), con un
# retry dichiarato la prima volta e memoria del modello per le chiamate dopo.
REASONING_MINIMO = {"effort": "minimal"}
PREFISSI_RAGIONAMENTO_NATIVO = ("z-ai/",)
RAGIONAMENTO_OBBLIGATORIO = set()   # slug che hanno risposto 400 a {"enabled": false} in questo processo
_MSG_RAGIONAMENTO_OBBLIGATORIO = "reasoning is mandatory"
# cache_control conservato solo qui: gli altri provider (Z.ai, DeepSeek, Google) cachano da
# soli e riportano cached_tokens; mandare loro il campo non aggiunge nulla e puo' essere rifiutato.
PREFISSI_CACHE_CONTROL = ("anthropic/",)
# 20/09: capacita' verificate sullo slug standard (non Contributor): auto-only
# per tool_choice, Max disponibile. La scelta dei modelli resta nel .env.
MUSE_STANDARD = "meta/muse-spark-1.3"

VARIABILI_BASE = (
    "OPENROUTER_API_KEY", "CHAT_MODEL", "CHAT_MAX_TOKENS", "CONSIGLIERE_MODEL",
    "CONSIGLIERE_R0_MODEL", "CAPO_MODEL", "RED_TEAM_MODEL", "REFLECTION_MODEL",
    "ACTION_EXTRACTOR_MODEL", "BRIEFING_MODEL", "NEWS_CLASSIFIER_MODEL",
)
_FUNZIONI_SINGOLE = {
    "capo": "CAPO_MODEL",
    "red_team": "RED_TEAM_MODEL",
    "reflection": "REFLECTION_MODEL",
    "action_extractor": "ACTION_EXTRACTOR_MODEL",
    "briefing": "BRIEFING_MODEL",
    "news_classifier": "NEWS_CLASSIFIER_MODEL",
    # 02/10: sintesi di UN articolo su richiesta esplicita (market_data/article_summary.py).
    # Funzione OPZIONALE: fuori da VARIABILI_BASE, vuota = «non configurata» solo li'.
    "news_summary": "NEWS_SUMMARY_MODEL",
}
# Gli id degli agenti chat (chat_engine.SYSTEM_PROMPTS_BASE) e dei desk (specialists/*.name):
# servono solo all'endpoint `engines` per elencare i modelli risolti, non alla risoluzione.
AGENTI_CHAT = ("capo", "macro", "quant", "options", "fundamentals", "crypto", "eventdesk",
               "politics", "news")
DESK = ("macro", "quant", "options", "fundamentals", "crypto", "eventdesk")


# ============================================================================ errori
class ConfigurazioneLLMMancante(RuntimeError):
    """Variabile del .env assente o vuota: si dichiara col nome, non si ripiega (14/07)."""

    def __init__(self, variabile, dettaglio=None):
        self.variabile = variabile
        msg = (variabile + " assente o vuota nel .env: nessun default nel codice (regola 14/07). "
               "Aggiungi la riga " + variabile + "=... al .env (tabella in .env.example).")
        if dettaglio:
            msg = variabile + ": " + dettaglio
        super().__init__(msg)


class APIError(Exception):
    """Base degli errori del client."""


class APIStatusError(APIError):
    """Risposta HTTP >= 400, o `error` nel corpo (anche a HTTP 200 nello streaming)."""

    def __init__(self, status_code, message, body=None):
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__("HTTP " + str(status_code) + ": " + str(message))


class APIConnectionError(APIError):
    """Rete, stream interrotto, corpo illeggibile."""


class APITimeoutError(APIConnectionError):
    """Timeout del client."""


# ============================================================================ .env
def _letture():
    """OGNI variabile letta, col nome scritto per esteso in una `os.getenv("...")`: e' cosi'
    che tests/test_env_example.py (B1, 02/09) verifica che .env.example documenti ESATTAMENTE
    cio' che il prodotto legge, nei due versi. Una lettura a nome composto sarebbe invisibile."""
    return {
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
        "CHAT_MODEL": os.getenv("CHAT_MODEL"),
        "CHAT_MAX_TOKENS": os.getenv("CHAT_MAX_TOKENS"),
        "CHAT_CAPO_MODEL": os.getenv("CHAT_CAPO_MODEL"),
        "CHAT_MACRO_MODEL": os.getenv("CHAT_MACRO_MODEL"),
        "CHAT_QUANT_MODEL": os.getenv("CHAT_QUANT_MODEL"),
        "CHAT_OPTIONS_MODEL": os.getenv("CHAT_OPTIONS_MODEL"),
        "CHAT_FUNDAMENTALS_MODEL": os.getenv("CHAT_FUNDAMENTALS_MODEL"),
        "CHAT_CRYPTO_MODEL": os.getenv("CHAT_CRYPTO_MODEL"),
        "CHAT_EVENTDESK_MODEL": os.getenv("CHAT_EVENTDESK_MODEL"),
        "CHAT_POLITICS_MODEL": os.getenv("CHAT_POLITICS_MODEL"),
        "CHAT_NEWS_MODEL": os.getenv("CHAT_NEWS_MODEL"),
        "CONSIGLIERE_MODEL": os.getenv("CONSIGLIERE_MODEL"),
        "CONSIGLIERE_R0_MODEL": os.getenv("CONSIGLIERE_R0_MODEL"),
        "CONSIGLIERE_MACRO_MODEL": os.getenv("CONSIGLIERE_MACRO_MODEL"),
        "CONSIGLIERE_QUANT_MODEL": os.getenv("CONSIGLIERE_QUANT_MODEL"),
        "CONSIGLIERE_OPTIONS_MODEL": os.getenv("CONSIGLIERE_OPTIONS_MODEL"),
        "CONSIGLIERE_FUNDAMENTALS_MODEL": os.getenv("CONSIGLIERE_FUNDAMENTALS_MODEL"),
        "CONSIGLIERE_CRYPTO_MODEL": os.getenv("CONSIGLIERE_CRYPTO_MODEL"),
        "CONSIGLIERE_EVENTDESK_MODEL": os.getenv("CONSIGLIERE_EVENTDESK_MODEL"),
        "CAPO_MODEL": os.getenv("CAPO_MODEL"),
        "RED_TEAM_MODEL": os.getenv("RED_TEAM_MODEL"),
        "REFLECTION_MODEL": os.getenv("REFLECTION_MODEL"),
        "ACTION_EXTRACTOR_MODEL": os.getenv("ACTION_EXTRACTOR_MODEL"),
        "BRIEFING_MODEL": os.getenv("BRIEFING_MODEL"),
        "NEWS_CLASSIFIER_MODEL": os.getenv("NEWS_CLASSIFIER_MODEL"),
        "NEWS_SUMMARY_MODEL": os.getenv("NEWS_SUMMARY_MODEL"),
    }


def _env(nome):
    letture = _letture()
    v = letture[nome] if nome in letture else os.environ.get(nome)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _richiesta(nome):
    v = _env(nome)
    if v is None:
        raise ConfigurazioneLLMMancante(nome)
    return v


def chiave_api():
    return _richiesta("OPENROUTER_API_KEY")


def modello(funzione, agente=None, round_n=None):
    """Slug OpenRouter per la funzione, con la precedenza scritta dal PM (v. docstring)."""
    if funzione == "chat":
        if agente:
            o = _env("CHAT_" + str(agente).upper() + "_MODEL")
            if o:
                return o
        return _richiesta("CHAT_MODEL")
    if funzione == "consigliere":
        if round_n == 0:
            return _richiesta("CONSIGLIERE_R0_MODEL")
        if agente:
            o = _env("CONSIGLIERE_" + str(agente).upper() + "_MODEL")
            if o:
                return o
        return _richiesta("CONSIGLIERE_MODEL")
    var = _FUNZIONI_SINGOLE.get(funzione)
    if var is None:
        raise ValueError("funzione LLM sconosciuta: " + repr(funzione)
                         + " (attese: chat, consigliere, " + ", ".join(_FUNZIONI_SINGOLE) + ")")
    return _richiesta(var)


def chat_max_tokens():
    v = _richiesta("CHAT_MAX_TOKENS")
    try:
        tokens = int(v)
    except ValueError:
        raise ConfigurazioneLLMMancante(
            "CHAT_MAX_TOKENS", "'" + v + "' non e' un intero (tetto per chiamata chat, ragionamento incluso)")
    if tokens <= 0:
        raise ConfigurazioneLLMMancante(
            "CHAT_MAX_TOKENS", "'" + v + "' deve essere un intero positivo (tetto per chiamata chat, ragionamento incluso)")
    return tokens


def variabili_mancanti():
    """Le variabili BASE assenti o vuote, nell'ordine di VARIABILI_BASE (per verifica_config/engines)."""
    return [n for n in VARIABILI_BASE if _env(n) is None]


def somma_costo(precedente, usage):
    """Accumula `usage.cost_usd` di una risposta su un totale per agente/tool-loop.
    None se il totale o la risposta non portano il costo: un totale a meta' spacciato per
    intero e' il fallback silenzioso vietato (14/07); il buco resta dichiarato (None)."""
    c = getattr(usage, "cost_usd", None) if usage is not None else None
    if precedente is None or c is None or isinstance(precedente, bool) or isinstance(c, bool):
        return None
    try:
        valori = (float(precedente), float(c))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(v) and v >= 0 for v in valori):
        return None
    return sum(valori)


def modello_o_buco(funzione, agente=None, round_n=None):
    """Per l'endpoint `engines`: lo slug risolto, oppure «n.d. (VARIABILE assente)»."""
    try:
        return modello(funzione, agente, round_n)
    except ConfigurazioneLLMMancante as e:
        return "n.d. (" + e.variabile + " assente)"


def somma_usage(precedente, usage):
    """Somma per campo: un buco rimane None anche dopo risposte complete.

    Costo consegnato e completezza dei token sono misure indipendenti.
    None come precedente indica il primo messaggio, non una misura a zero.
    """
    request_id = getattr(usage, "request_id", None)
    previous_ids = list((precedente or {}).get("request_ids") or [])
    if request_id and request_id in previous_ids:
        return dict(precedente)  # The same paid receipt is not charged twice on replay.
    campi = {"in": "input_tokens", "out": "output_tokens",
             "cache_read": "cache_read_input_tokens",
             "cache_write": "cache_creation_input_tokens"}
    totale = {}
    for breve, nome in campi.items():
        n = getattr(usage, nome, None)
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            n = None
        prima = precedente.get(breve) if precedente is not None else 0
        totale[breve] = prima + n if prima is not None and n is not None else None
    totale["cost_usd"] = somma_costo(
        precedente.get("cost_usd") if precedente is not None else 0.0, usage)
    totale["tokens_missing"] = [k for k in campi if totale[k] is None]
    totale["tokens_status"] = "parziale" if totale["tokens_missing"] else "completo"
    if request_id or previous_ids:
        totale["request_ids"] = previous_ids + ([request_id] if request_id else [])
    return totale


# ============================================================================ oggetti risposta
class TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "TextBlock(" + repr(self.text[:60]) + ")"


class ToolUseBlock:
    type = "tool_use"

    def __init__(self, id, name, input):
        self.id = id
        self.name = name
        self.input = input

    def __repr__(self):
        return "ToolUseBlock(" + repr(self.id) + ", " + repr(self.name) + ")"


class ThinkingBlock:
    type = "thinking"

    def __init__(self, thinking):
        self.thinking = thinking

    def __repr__(self):
        return "ThinkingBlock(" + repr(self.thinking[:40]) + ")"


class StopDetails:
    type = "refusal"

    def __init__(self, category, explanation=None):
        self.category = category
        self.explanation = explanation


class Usage:
    """Token e costo nella semantica Anthropic (input SENZA la cache), piu' i campi OpenRouter."""

    def __init__(self, input_tokens=None, output_tokens=None, cache_read_input_tokens=None,
                 cache_creation_input_tokens=None, reasoning_tokens=None, cost_usd=None,
                 prompt_tokens=None, completion_tokens=None, reasoning_forzato=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.reasoning_tokens = reasoning_tokens
        self.cost_usd = cost_usd
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        # "minimal" se il chiamante voleva il ragionamento SPENTO ma il modello lo rifiuta
        # (400 «Reasoning is mandatory») e la chiamata e' partita con effort minimal: dichiarato.
        self.reasoning_forzato = reasoning_forzato

    def to_dict(self):
        result = {
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "reasoning_tokens": self.reasoning_tokens, "cost_usd": self.cost_usd,
            "reasoning_forzato": self.reasoning_forzato,
        }
        if getattr(self, "request_id", None):
            result["request_ids"] = [self.request_id]
        return result

    def __repr__(self):
        return "Usage(" + json.dumps(self.to_dict()) + ")"


class Messaggio:
    """La forma che i call site leggono: content[], stop_reason, stop_details, usage."""

    role = "assistant"

    def __init__(self, id, model, content, stop_reason, stop_details=None, usage=None,
                 provider=None, finish_reason=None):
        self.id = id
        self.model = model
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.usage = usage
        self.provider = provider
        self.finish_reason = finish_reason

    def __repr__(self):
        return ("Messaggio(stop_reason=" + repr(self.stop_reason) + ", blocchi="
                + str(len(self.content)) + ", model=" + repr(self.model) + ")")


# ============================================================================ blocchi (dict o oggetto)
def _tipo(b):
    return b.get("type") if isinstance(b, dict) else getattr(b, "type", None)


def _campo(b, k, default=None):
    if isinstance(b, dict):
        return b.get(k, default)
    return getattr(b, k, default)


def _testo_di(content):
    """Testo di un content Anthropic: stringa, lista di blocchi text, dict o None."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "\n".join(str(_campo(b, "text") or "") for b in content if _tipo(b) == "text")
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


# ============================================================================ richiesta
def _conserva_cache(model):
    return str(model or "").startswith(PREFISSI_CACHE_CONTROL)


def _parte_testo(b, conserva):
    p = {"type": "text", "text": _campo(b, "text") or ""}
    cc = _campo(b, "cache_control")
    if conserva and cc:
        p["cache_control"] = cc
    return p


def _system_openai(system, conserva):
    if system is None:
        return None
    if isinstance(system, str):
        return {"role": "system", "content": system}
    parti = [_parte_testo(b, conserva) for b in system if _tipo(b) == "text"]
    if conserva and any("cache_control" in p for p in parti):
        return {"role": "system", "content": parti}
    return {"role": "system", "content": "\n".join(p["text"] for p in parti)}


def _messaggi_openai(messages, conserva):
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str) or content is None:
            out.append({"role": role, "content": content or ""})
            continue
        if role == "assistant":
            testi, tool_calls = [], []
            for b in content:
                t = _tipo(b)
                if t == "text":
                    testi.append(_campo(b, "text") or "")
                elif t == "tool_use":
                    tool_calls.append({
                        "id": _campo(b, "id"), "type": "function",
                        "function": {"name": _campo(b, "name"),
                                     "arguments": json.dumps(_campo(b, "input") or {},
                                                             ensure_ascii=False, default=str)},
                    })
                # thinking: non si rispedisce (OpenRouter lo conserva via reasoning_details
                # solo nel suo formato; i call site non ne hanno bisogno)
            msg = {"role": "assistant", "content": "\n".join(testi) if testi else None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if msg["content"] is None and not tool_calls:
                msg["content"] = ""
            out.append(msg)
            continue
        # user a blocchi: i tool_result diventano messaggi role=tool (subito dopo l'assistant
        # che li ha chiesti), il testo che segue (nudge) un messaggio user a parte.
        tool_msgs, parti = [], []
        for b in content:
            t = _tipo(b)
            if t == "tool_result":
                tm = {"role": "tool", "tool_call_id": _campo(b, "tool_use_id"),
                      "content": _testo_di(_campo(b, "content"))}
                cc = _campo(b, "cache_control")
                if conserva and cc:
                    tm["content"] = [{"type": "text", "text": tm["content"], "cache_control": cc}]
                tool_msgs.append(tm)
            elif t == "text":
                parti.append(_parte_testo(b, conserva))
        out.extend(tool_msgs)
        if parti:
            if conserva and any("cache_control" in p for p in parti):
                out.append({"role": role, "content": parti})
            else:
                out.append({"role": role, "content": "\n".join(p["text"] for p in parti)})
    return out


def _tools_openai(tools):
    out = []
    for t in tools or []:
        out.append({"type": "function", "function": {
            "name": t.get("name"),
            "description": t.get("description", "") or "",
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out


def _tool_choice_openai(tc):
    if tc is None:
        return None
    if isinstance(tc, str):
        return tc
    tipo = tc.get("type")
    if tipo == "none":
        return "none"
    if tipo == "auto":
        return "auto"
    if tipo == "any":
        return "required"
    if tipo == "tool":
        return {"type": "function", "function": {"name": tc.get("name")}}
    raise ValueError("tool_choice non riconosciuto: " + repr(tc))


# ============================================================================ effort per fase
# Decisione maintainer (integrazione 04/10): l'effort di ragionamento per fase vive nel .env
# come i modelli, una variabile per fase. Variabile ASSENTE = il default qui sotto (la tabella
# di Andrea del 02/10); presente ma VUOTA o con un valore fuori da VALORI_EFFORT =
# ConfigurazioneLLMMancante col NOME della variabile, mai un default silenzioso.
# Consigliere, stessa precedenza dei modelli:
#   R0     = CONSIGLIERE_R0_EFFORT, per tutti i desk;
#   R1/R2  = CONSIGLIERE_<DESK>_EFFORT (override del desk, vale per R1 e R2) se presente o se ha
#            un default, altrimenti CONSIGLIERE_R1_EFFORT / CONSIGLIERE_R2_EFFORT.
# Tabella di Andrea espressa cosi': R0 low; R1/R2 high (default di round, quindi fundamentals,
# quant, options, macro); crypto ed eventdesk restano adaptive (default del loro override).
# Ogni valore passa da _reasoning_openai: su z-ai/ (GLM) nessun effort esplicito parte mai.
VALORI_EFFORT = ("adaptive", "disabled", "minimal", "low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = {
    "CONSIGLIERE_R0_EFFORT": "low",
    "CONSIGLIERE_R1_EFFORT": "high",
    "CONSIGLIERE_R2_EFFORT": "high",
    # override per desk (R1 e R2): None = segue CONSIGLIERE_R1_EFFORT / CONSIGLIERE_R2_EFFORT
    "CONSIGLIERE_MACRO_EFFORT": None,
    "CONSIGLIERE_QUANT_EFFORT": None,
    "CONSIGLIERE_OPTIONS_EFFORT": None,
    "CONSIGLIERE_FUNDAMENTALS_EFFORT": None,
    "CONSIGLIERE_CRYPTO_EFFORT": "adaptive",
    "CONSIGLIERE_EVENTDESK_EFFORT": "adaptive",
    "CAPO_EFFORT": "high",
    "RED_TEAM_EFFORT": "high",
    "REFLECTION_EFFORT": "low",
    # G7/C2 (04/10): il preparer delle valutazioni ha la SUA variabile. Prima seguiva di
    # nascosto CONSIGLIERE_FUNDAMENTALS_EFFORT / CONSIGLIERE_R1_EFFORT: chi toccava l'effort
    # dei desk cambiava anche lui. Default = il valore di Andrea (fundamentals R1 = high).
    "VALUATION_PREPARER_EFFORT": "high",
}
_FASI_EFFORT = {"capo": "CAPO_EFFORT", "red_team": "RED_TEAM_EFFORT",
                "reflection": "REFLECTION_EFFORT",
                "valuation_preparer": "VALUATION_PREPARER_EFFORT"}


def _letture_effort():
    """Come _letture: ogni nome per esteso in una `os.getenv("...")` (test_env_example).
    Valori GREZZI: qui «vuota» e «assente» sono due casi diversi."""
    return {
        "CONSIGLIERE_R0_EFFORT": os.getenv("CONSIGLIERE_R0_EFFORT"),
        "CONSIGLIERE_R1_EFFORT": os.getenv("CONSIGLIERE_R1_EFFORT"),
        "CONSIGLIERE_R2_EFFORT": os.getenv("CONSIGLIERE_R2_EFFORT"),
        "CONSIGLIERE_MACRO_EFFORT": os.getenv("CONSIGLIERE_MACRO_EFFORT"),
        "CONSIGLIERE_QUANT_EFFORT": os.getenv("CONSIGLIERE_QUANT_EFFORT"),
        "CONSIGLIERE_OPTIONS_EFFORT": os.getenv("CONSIGLIERE_OPTIONS_EFFORT"),
        "CONSIGLIERE_FUNDAMENTALS_EFFORT": os.getenv("CONSIGLIERE_FUNDAMENTALS_EFFORT"),
        "CONSIGLIERE_CRYPTO_EFFORT": os.getenv("CONSIGLIERE_CRYPTO_EFFORT"),
        "CONSIGLIERE_EVENTDESK_EFFORT": os.getenv("CONSIGLIERE_EVENTDESK_EFFORT"),
        "CAPO_EFFORT": os.getenv("CAPO_EFFORT"),
        "RED_TEAM_EFFORT": os.getenv("RED_TEAM_EFFORT"),
        "REFLECTION_EFFORT": os.getenv("REFLECTION_EFFORT"),
        "VALUATION_PREPARER_EFFORT": os.getenv("VALUATION_PREPARER_EFFORT"),
    }


def effort_env(nome):
    """Il valore effort della variabile: dal .env, oppure il default se ASSENTE (None per un
    override di desk senza default). Vuota o fuori da VALORI_EFFORT = errore col nome."""
    letture = _letture_effort()
    if nome not in letture:
        raise ValueError("variabile effort sconosciuta: " + repr(nome))
    grezzo = letture[nome]
    if grezzo is None:
        return DEFAULT_EFFORT[nome]
    valore = grezzo.strip()
    attesi = " / ".join(VALORI_EFFORT)
    if not valore:
        raise ConfigurazioneLLMMancante(
            nome, "presente ma vuota nel .env: vuota NON vale default. Cancella la riga (default "
            + repr(DEFAULT_EFFORT[nome]) + ") o scrivi uno fra " + attesi)
    if valore not in VALORI_EFFORT:
        raise ConfigurazioneLLMMancante(
            nome, repr(valore) + " non e' un effort valido (attesi: " + attesi + ")")
    return valore


def valida_effort_env():
    """G7/M1 (04/10): TUTTE le variabili *_EFFORT lette e validate in un colpo, all'avvio
    della run del comitato, PRIMA di qualunque chiamata pagata. Prima un CAPO_EFFORT
    sbagliato emergeva solo al Capo, a desk e red team gia' pagati. Ritorna {nome: valore};
    la prima variabile vuota o non valida = ConfigurazioneLLMMancante col SUO nome (le altre
    sbagliate sono elencate nello stesso messaggio)."""
    valori, errori = {}, []
    for nome in DEFAULT_EFFORT:
        try:
            valori[nome] = effort_env(nome)
        except ConfigurazioneLLMMancante as exc:
            errori.append(exc)
    if errori:
        primo = errori[0]
        altri = [e.variabile for e in errori[1:]]
        raise ConfigurazioneLLMMancante(
            primo.variabile, str(primo).split(": ", 1)[-1]
            + ((" (non valide anche: " + ", ".join(altri) + ")") if altri else ""))
    return valori


def _thinking_da_effort(valore):
    if valore == "adaptive":
        return {"type": "adaptive"}
    if valore == "disabled":
        return {"type": "disabled"}
    return {"type": "effort", "effort": valore}


# Ogni thinking che una variabile *_EFFORT puo' produrre (piu' la policy storica: adaptive e
# max Muse sono gia' dentro). Serve SOLO a riconoscere richieste gia' pagate con un effort
# diverso da quello di oggi (G7/C2, preparer): mai a sceglierne uno.
THINKING_DA_EFFORT = tuple(_thinking_da_effort(v) for v in VALORI_EFFORT)


def thinking_fase(fase):
    """thinking per capo / red_team / reflection / valuation_preparer, da CAPO_EFFORT /
    RED_TEAM_EFFORT / REFLECTION_EFFORT / VALUATION_PREPARER_EFFORT (default high / high /
    low / high)."""
    if fase not in _FASI_EFFORT:
        raise ValueError("fase effort sconosciuta: " + repr(fase)
                         + " (attese: " + ", ".join(_FASI_EFFORT) + ")")
    return _thinking_da_effort(effort_env(_FASI_EFFORT[fase]))


def thinking_consigliere(model, agente=None, round_n=None):
    """Effort per fase dal .env (tabella sopra DEFAULT_EFFORT).

    Le chiamate senza contesto mantengono la policy storica per compatibilita'
    con strumenti esterni; i call site del Consigliere passano agente e round.
    """
    if agente is None or round_n is None:
        if model == MUSE_STANDARD:
            return {"type": "effort", "effort": "max"}
        return {"type": "adaptive"}
    if round_n == 0:
        return _thinking_da_effort(effort_env("CONSIGLIERE_R0_EFFORT"))
    if round_n not in (1, 2):
        raise ValueError("round del Consigliere senza effort configurabile: " + repr(round_n)
                         + " (attesi 0, 1, 2)")
    valore = None
    if str(agente).lower() in DESK:
        valore = effort_env("CONSIGLIERE_" + str(agente).upper() + "_EFFORT")
    if valore is None:
        valore = effort_env("CONSIGLIERE_R" + str(round_n) + "_EFFORT")
    return _thinking_da_effort(valore)


_EFFORT_OMESSI_DICHIARATI = set()   # (slug, effort) gia' dichiarati a log in questo processo


def _dichiara_effort_omesso(model, effort):
    chiave = (str(model), effort)
    if chiave in _EFFORT_OMESSI_DICHIARATI:
        return
    _EFFORT_OMESSI_DICHIARATI.add(chiave)
    try:
        print("[llm_client] " + str(model) + ": effort " + repr(effort) + " NON inviato "
              "(su z-ai/ ogni effort esplicito azzera il ragionamento, misurato 05/09): "
              "ragionamento nativo acceso, tetto = max_tokens")
    except OSError:
        pass


def _reasoning_openai(thinking, model=""):
    if thinking is None:
        return None
    tipo = thinking.get("type")
    if tipo == "effort":
        effort = thinking.get("effort")
        if effort not in ("minimal", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("reasoning effort non riconosciuto: " + repr(effort))
        if str(model).startswith(PREFISSI_RAGIONAMENTO_NATIVO):
            # Misurato 05/09: su GLM QUALUNQUE effort esplicito azzera il ragionamento;
            # omesso = acceso (il tetto resta max_tokens). Vale anche per gli effort dal .env.
            _dichiara_effort_omesso(model, effort)
            return None
        return {"effort": effort}
    if tipo == "adaptive":
        if str(model).startswith(PREFISSI_RAGIONAMENTO_NATIVO):
            return None   # misurato: su GLM ogni effort azzera il ragionamento; omesso = acceso
        return dict(REASONING_ADAPTIVE)
    if tipo == "disabled":
        if model in RAGIONAMENTO_OBBLIGATORIO:
            # Gia' rifiutato una volta: minimal (0 token misurati). AMMESSO anche su z-ai/
            # (decisione coordinatore 04/10, REV G7/R1): la regola «a z-ai/ non parte mai un
            # effort» vale per le chiamate che vogliono la deliberazione ACCESA (adaptive /
            # effort), dove un effort esplicito la azzererebbe. Qui la si vuole SPENTA e il
            # modello rifiuta {"enabled": false}: minimal e' il modo misurato (05/09) per
            # ottenere 0 token, cioe' proprio «disabled». Omettere il campo la accenderebbe.
            return dict(REASONING_MINIMO)
        return dict(REASONING_DISABLED)
    if tipo == "minimal":
        # Percorsi di recupero che devono contenere il reasoning senza pagare
        # prima il 400 "Reasoning is mandatory" dei modelli che non accettano
        # {"enabled": false}. G7/M4 (04/10): stessa guardia del tipo «effort» — su z-ai/
        # (GLM) nessun effort esplicito parte mai, nemmeno «minimal» (misurato 05/09).
        return _reasoning_openai({"type": "effort", "effort": "minimal"}, model)
    if tipo == "enabled":
        b = thinking.get("budget_tokens")
        return {"max_tokens": int(b)} if b else {"enabled": True}
    raise ValueError("thinking non riconosciuto: " + repr(thinking))


def costruisci_corpo(model, max_tokens, messages, system=None, tools=None, tool_choice=None,
                     thinking=None, stream=False, provider_max_price=None, response_format=None,
                     require_full_context=False, **ignorati):
    """Il corpo JSON per chat/completions a partire dai kwargs Anthropic dei call site.
    `extra_headers` e altri kwargs Anthropic-only finiscono in `ignorati` (non partono)."""
    conserva = _conserva_cache(model)
    msgs = []
    s = _system_openai(system, conserva)
    if s is not None:
        msgs.append(s)
    msgs.extend(_messaggi_openai(messages, conserva))
    corpo = {"model": model, "messages": msgs, "max_tokens": int(max_tokens)}
    if type(require_full_context) is not bool:
        raise ValueError('require_full_context richiede un booleano esplicito')
    if require_full_context:
        # OpenRouter must reject a real token overflow, never truncate sources.
        corpo['plugins'] = [{'id': 'context-compression', 'enabled': False}]
    if provider_max_price is not None:
        from math import isfinite
        if (not isinstance(provider_max_price, dict)
            or set(provider_max_price) != {"prompt", "completion", "request"}
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or v < 0
                   for v in provider_max_price.values())):
            raise ValueError("provider_max_price richiede tariffe prompt/completion/request finite non negative")
        corpo["provider"] = {"max_price": dict(provider_max_price)}
    if response_format is not None:
        if response_format != {"type": "json_object"}:
            spec = response_format.get("json_schema") if isinstance(response_format, dict) else None
            if (not isinstance(spec, dict) or set(response_format) != {"type", "json_schema"}
                    or response_format["type"] != "json_schema" or set(spec) != {"name", "strict", "schema"}
                    or not isinstance(spec["name"], str) or not spec["name"].strip()
                    or type(spec["strict"]) is not bool or not isinstance(spec["schema"], dict)):
                raise ValueError("response_format richiede json_object o json_schema con strict booleano esplicito")
        corpo["response_format"] = json.loads(json.dumps(response_format, allow_nan=False))
        # Reject a provider that would silently ignore the requested JSON mode.
        corpo.setdefault("provider", {})["require_parameters"] = True
    tc = _tool_choice_openai(tool_choice)
    # Muse rifiuta anche `none`: omettere entrambi disabilita davvero i tool
    # nel turno finale. Cronologia, risultati e nudge restano nel payload.
    if not (model == MUSE_STANDARD and tc == "none"):
        if tools:
            corpo["tools"] = _tools_openai(tools)
        if tc is not None:
            corpo["tool_choice"] = tc
    r = _reasoning_openai(thinking, model)
    if r is not None:
        corpo["reasoning"] = r
    if stream:
        corpo["stream"] = True
    return corpo


def _voleva_spento(kw):
    return (kw.get("thinking") or {}).get("type") == "disabled"


def _e_ragionamento_obbligatorio(errore, corpo):
    """True se il 400 e' «Reasoning is mandatory…» su una richiesta partita SPENTA."""
    return (isinstance(errore, APIStatusError) and errore.status_code == 400
            and _MSG_RAGIONAMENTO_OBBLIGATORIO in str(errore.message).lower()
            and corpo.get("reasoning") == REASONING_DISABLED)


def _forza_minimal(corpo):
    """Segna il modello e riscrive la richiesta con effort minimal, dichiarandolo nel log.

    Solo dopo un 400 «Reasoning is mandatory» su una richiesta partita SPENTA: e' la via
    misurata per spegnere la deliberazione, ammessa anche su z-ai/ (REV G7/R1, vedi il ramo
    «disabled» di _reasoning_openai). Chi la vuole accesa non passa mai di qui."""
    RAGIONAMENTO_OBBLIGATORIO.add(corpo.get("model"))
    corpo["reasoning"] = dict(REASONING_MINIMO)
    print("[llm_client] " + str(corpo.get("model")) + ": il ragionamento non si puo' spegnere "
          "(HTTP 400 «Reasoning is mandatory»): riprovo con effort minimal (0 token misurati "
          "il 05/09) — dichiarato in usage.reasoning_forzato")


def _dichiara_forzato(messaggio, forzato):
    if forzato and messaggio is not None and messaggio.usage is not None:
        messaggio.usage.reasoning_forzato = "minimal"
    return messaggio


# ============================================================================ risposta
def _input_da_arguments(args):
    if isinstance(args, dict):
        return args
    if args is None or args == "":
        return {}
    try:
        v = json.loads(args)
        return v if isinstance(v, dict) else {"__input_parse_error__": True, "__valore__": v}
    except Exception:
        # lo stesso marcatore che chat_engine gestisce: niente dispatch coi default
        return {"__input_parse_error__": True, "__raw__": str(args)[:200]}


def _stop_da_finish(finish, refusal, ha_tool_use):
    if refusal:
        return "refusal", StopDetails("refusal", str(refusal))
    if finish == "content_filter":
        return "refusal", StopDetails("content_filter", None)
    if finish == "length":
        return "max_tokens", None
    if finish == "tool_calls" or (ha_tool_use and finish in (None, "stop")):
        return "tool_use", None
    if finish in (None, "stop"):
        return "end_turn", None
    # valore ignoto: passa verbatim, dichiarato (i call site lo trattano come «altro»)
    return str(finish), None


def _usage_da_json(u):
    if not isinstance(u, dict):
        return None
    ptd = u.get("prompt_tokens_details") or {}
    ctd = u.get("completion_tokens_details") or {}
    pt = u.get("prompt_tokens")
    cached = ptd.get("cached_tokens")
    cw = ptd.get("cache_write_tokens")
    input_tokens = None
    if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (pt, cached, cw)):
        residuo = pt - cached - cw
        input_tokens = residuo if residuo >= 0 else None
    return Usage(
        input_tokens=input_tokens,
        output_tokens=u.get("completion_tokens"),
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=cw,
        reasoning_tokens=ctd.get("reasoning_tokens"),
        cost_usd=u.get("cost"),
        prompt_tokens=pt,
        completion_tokens=u.get("completion_tokens"),
    )


def _errore_da_corpo(status, corpo):
    """APIStatusError dal JSON d'errore OpenRouter {"error": {"code","message","metadata"}}."""
    err = corpo.get("error") if isinstance(corpo, dict) else None
    if isinstance(err, dict):
        msg = str(err.get("message") or err)
        meta = err.get("metadata")
        if meta:
            msg += " | metadata: " + json.dumps(meta, ensure_ascii=False, default=str)[:400]
        code = err.get("code")
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = status
        return APIStatusError(code if code else status, msg, corpo)
    return APIStatusError(status, str(corpo)[:400] if corpo else "risposta senza corpo", corpo)


def messaggio_da_json(data):
    """Messaggio (forma SDK) da una risposta chat/completions non in streaming."""
    if not isinstance(data, dict):
        error = APIConnectionError("risposta INCOMPLETA: corpo provider non oggetto")
        error.partial_response = data
        raise error
    if isinstance(data, dict) and data.get("error") and not data.get("choices"):
        raise _errore_da_corpo(200, data)
    scelte = data.get("choices") or []
    if not isinstance(scelte, list) or not scelte or not isinstance(scelte[0], dict):
        error = APIConnectionError("risposta INCOMPLETA: choices assente o malformato")
        error.partial_response = data
        raise error
    scelta = scelte[0]
    if scelta.get("finish_reason") is None:
        error = APIConnectionError("risposta INCOMPLETA: finish_reason assente")
        error.partial_response = data
        raise error
    msg = scelta.get("message")
    if not isinstance(msg, dict):
        error = APIConnectionError("risposta INCOMPLETA: message assente o malformato")
        error.partial_response = data
        raise error
    blocchi = []
    r = msg.get("reasoning")
    if r:
        blocchi.append(ThinkingBlock(str(r)))
    c = msg.get("content")
    if isinstance(c, str):
        if c:
            blocchi.append(TextBlock(c))
    elif isinstance(c, list):
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text"):
                blocchi.append(TextBlock(p["text"]))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        blocchi.append(ToolUseBlock(id=tc.get("id"), name=fn.get("name"),
                                    input=_input_da_arguments(fn.get("arguments"))))
    ha_tool = any(b.type == "tool_use" for b in blocchi)
    stop, det = _stop_da_finish(scelta.get("finish_reason"), msg.get("refusal"), ha_tool)
    return Messaggio(id=data.get("id"), model=data.get("model"), content=blocchi,
                     stop_reason=stop, stop_details=det, usage=_usage_da_json(data.get("usage")),
                     provider=data.get("provider"), finish_reason=scelta.get("finish_reason"))


# ============================================================================ streaming
def _evento(tipo, **campi):
    return SimpleNamespace(type=tipo, **campi)


class _Ricomposizione:
    """Stato dello stream: ricompone i blocchi Anthropic dai delta OpenAI ed emette gli
    eventi SDK (content_block_start/delta/stop). Pura: riceve chunk gia' decodificati."""

    def __init__(self):
        self.blocchi = []          # dict: {"type": "text", "text"} | {"type": "tool_use", id, name, args}
        self.reasoning = []
        self.refusal = None
        self.finish = None
        self.usage = None
        self.id = None
        self.model = None
        self.provider = None
        self._corrente = None      # indice del blocco aperto
        self._tool_idx = {}        # index OpenAI -> indice blocco
        self._eventi = []

    def _chiudi(self):
        if self._corrente is not None:
            self._eventi.append(_evento("content_block_stop", index=self._corrente))
            self._corrente = None

    def _apri_testo(self):
        self.blocchi.append({"type": "text", "text": ""})
        i = len(self.blocchi) - 1
        self._corrente = i
        self._eventi.append(_evento("content_block_start", index=i,
                                    content_block=SimpleNamespace(type="text", text="")))

    def alimenta(self, chunk):
        """Un chunk JSON dello stream -> lista di eventi SDK. Solleva su `error`."""
        if isinstance(chunk, dict) and chunk.get("error"):
            raise _errore_da_corpo(200, chunk)
        self.id = chunk.get("id") or self.id
        self.model = chunk.get("model") or self.model
        self.provider = chunk.get("provider") or self.provider
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
        for scelta in chunk.get("choices") or []:
            delta = scelta.get("delta") or {}
            r = delta.get("reasoning")
            if r:
                self.reasoning.append(str(r))
            c = delta.get("content")
            if c:
                if self._corrente is None or self.blocchi[self._corrente]["type"] != "text":
                    self._chiudi()
                    self._apri_testo()
                self.blocchi[self._corrente]["text"] += c
                self._eventi.append(_evento("content_block_delta", index=self._corrente,
                                            delta=SimpleNamespace(type="text_delta", text=c)))
            rf = delta.get("refusal")
            if rf:
                self.refusal = (self.refusal or "") + str(rf)
            for tc in delta.get("tool_calls") or []:
                oi = tc.get("index", 0)
                fn = tc.get("function") or {}
                if oi not in self._tool_idx:
                    self._chiudi()
                    self.blocchi.append({"type": "tool_use", "id": tc.get("id") or "",
                                         "name": fn.get("name") or "", "args": ""})
                    bi = len(self.blocchi) - 1
                    self._tool_idx[oi] = bi
                    self._corrente = bi
                    self._eventi.append(_evento(
                        "content_block_start", index=bi,
                        content_block=SimpleNamespace(type="tool_use", id=self.blocchi[bi]["id"],
                                                      name=self.blocchi[bi]["name"], input={})))
                bi = self._tool_idx[oi]
                if self._corrente != bi:
                    self._chiudi()
                    self._corrente = bi
                if fn.get("name") and not self.blocchi[bi]["name"]:
                    self.blocchi[bi]["name"] = fn["name"]
                if tc.get("id") and not self.blocchi[bi]["id"]:
                    self.blocchi[bi]["id"] = tc["id"]
                frag = fn.get("arguments")
                if frag:
                    self.blocchi[bi]["args"] += frag
                    self._eventi.append(_evento(
                        "content_block_delta", index=bi,
                        delta=SimpleNamespace(type="input_json_delta", partial_json=frag)))
            fr = scelta.get("finish_reason")
            if fr:
                self.finish = fr
                self._chiudi()
        ev, self._eventi = self._eventi, []
        return ev

    def fine(self):
        self._chiudi()
        ev, self._eventi = self._eventi, []
        return ev

    def messaggio(self):
        if self.finish is None:
            raise APIConnectionError("stream chiuso senza finish_reason: risposta INCOMPLETA "
                                     "(blocchi ricevuti: " + str(len(self.blocchi)) + ")")
        blocchi = []
        if self.reasoning:
            blocchi.append(ThinkingBlock("".join(self.reasoning)))
        for b in self.blocchi:
            if b["type"] == "text":
                if b["text"]:
                    blocchi.append(TextBlock(b["text"]))
            else:
                blocchi.append(ToolUseBlock(id=b["id"], name=b["name"],
                                            input=_input_da_arguments(b["args"])))
        ha_tool = any(b.type == "tool_use" for b in blocchi)
        stop, det = _stop_da_finish(self.finish, self.refusal, ha_tool)
        return Messaggio(id=self.id, model=self.model, content=blocchi, stop_reason=stop,
                         stop_details=det, usage=_usage_da_json(self.usage),
                         provider=self.provider, finish_reason=self.finish)

    def raw_response(self):
        """Provider fields reconstructed without inventing absent usage/finish."""
        message = {"content": "".join(b["text"] for b in self.blocchi if b["type"] == "text")}
        if self.reasoning:
            message["reasoning"] = "".join(self.reasoning)
        if self.refusal:
            message["refusal"] = self.refusal
        tools = [{"id": b["id"], "type": "function", "function": {
                    "name": b["name"], "arguments": b["args"]}}
                 for b in self.blocchi if b["type"] == "tool_use"]
        if tools:
            message["tool_calls"] = tools
        return {"id": self.id, "model": self.model, "provider": self.provider,
                "usage": self.usage, "choices": [{"message": message, "finish_reason": self.finish}]}


def _payload_sse(riga):
    """La riga SSE -> chunk JSON, None se da ignorare, "[DONE]" alla fine."""
    if not riga:
        return None
    riga = riga.strip()
    if not riga or riga.startswith(":"):
        return None
    if not riga.startswith("data:"):
        return None
    payload = riga[5:].strip()
    if payload == "[DONE]":
        return "[DONE]"
    try:
        return json.loads(payload)
    except Exception:
        raise APIConnectionError("chunk SSE illeggibile: " + payload[:200])


# ============================================================================ HTTP
def _intestazioni(chiave):
    return {"Authorization": "Bearer " + chiave, "Content-Type": "application/json",
            "HTTP-Referer": HTTP_REFERER, "X-Title": X_TITLE}


def _nuovo_client_http(timeout, trasporto):
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), transport=trasporto)


def _nuovo_client_http_async(timeout, trasporto):
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0), transport=trasporto)


def _ritentabile(status):
    return status in STATUS_RETRY


def _pausa(tentativo):
    return BACKOFF_S[min(tentativo, len(BACKOFF_S) - 1)]


class _RetryBudget:
    """Un solo budget per HTTP e SSE; mai ripetere token gia' consegnati."""
    def __init__(self, max_retries):
        self.limit = max_retries
        self.used = 0

    def delay(self, error, headers=None):
        # A committee owns durable reservations. An HTTP status or absence of
        # visible output does not prove that the provider did not charge.
        if current_request_scope() is not None:
            return None
        if self.used >= self.limit or not (isinstance(error, APIConnectionError)
                or isinstance(error, APIStatusError) and _ritentabile(error.status_code)):
            return None
        delay = _pausa(self.used)
        raw = (headers or {}).get("retry-after")
        if raw is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                try:
                    value = max(0.0, parsedate_to_datetime(raw).timestamp() - time.time())
                except (TypeError, ValueError, OverflowError):
                    value = float("nan")
            if math.isfinite(value) and value >= 0:
                # Non anticipare il provider ne' tenere la chat sospesa per ore.
                if value > 60:
                    return None
                delay = value
        self.used += 1
        try:
            print(f"[llm_client] retry {self.used}/{self.limit}: "
                  f"status={getattr(error, 'status_code', 'rete')}, attesa={delay:g}s")
        except OSError:
            pass  # Una pipe di log chiusa non deve interrompere il recupero.
        return delay


def _stream_senza_output(ric):
    return not (ric.blocchi or ric.reasoning or ric.refusal or ric.finish
                or ric.usage is not None)


def _decodifica(resp):
    try:
        return resp.json()
    except Exception:
        return None


# G7/M2 (04/10): i chunk SSE si scrivono nel request journal a GRUPPI (una transazione ogni
# CHECKPOINT_GRUPPO_CHUNK chunk o ogni CHECKPOINT_GRUPPO_S secondi), non uno per chunk: 4 desk
# in streaming parallelo sullo stesso journal rischiavano un lock oltre il timeout SQLite.
# Il gruppo pendente si scrive SEMPRE prima della ricevuta o del guasto (prova parziale
# intera); la ripresa non dipende da questi chunk ma dalla prenotazione scritta prima del POST.
CHECKPOINT_GRUPPO_CHUNK = 64
CHECKPOINT_GRUPPO_S = 2.0
# B5 (04/10): i commenti keepalive SSE («: OPENROUTER PROCESSING») azzerano il read-timeout,
# quindi uno stream poteva durare senza limite. Tetto DICHIARATO alla durata totale di una
# chiamata in streaming (dal POST all'ultimo byte): oltre, APITimeoutError col motivo; sotto
# un journal la richiesta resta incerta (prenotazione intera), mai ripagata in automatico.
DURATA_MAX_STREAM_S = 7200.0


class _RequestAttempt:
    """Bind the existing SDK surface to its durable accounting owner."""
    def __init__(self, body):
        scope = current_request_scope()
        self.journal = scope.get("journal") if scope else None
        self.request_id, self.body, self.saved = None, body, None
        self.finished = False
        self.released = None  # KA: secondi d'attesa se chiusa come certamente non fatturata
        # RV-KA P3.2: True appena il provider ha risposto 2xx (stream aperto o corpo letto).
        # Da li' in poi nessun guasto e' «certamente non fatturato», qualunque cosa porti.
        self.response_started = False
        self._checkpointed = False
        self._pending = []
        self._last_flush = time.monotonic()
        if self.journal is not None:
            self.request_id, self.body, self.saved = self.journal.prepare(body, scope)
            self.finished = self.saved is not None

    def flush(self):
        """Write the pending stream chunks (one transaction); a no-op when none."""
        if self._pending and self.journal is not None and not self.finished:
            pending, self._pending = self._pending, []
            self.journal.checkpoint_many(self.request_id, pending)
        self._last_flush = time.monotonic()

    def receive(self, data):
        if self.journal is not None and not self.finished:
            self.flush()
            choices = data.get("choices") if isinstance(data, dict) else None
            complete = bool(isinstance(choices, list) and choices and isinstance(choices[0], dict)
                            and choices[0].get("finish_reason") is not None)
            state = self.journal.receive(self.request_id, data, complete=complete)
            self.finished = True
            if state in ("unknown", "overrun", "incomplete"):
                error = RequestBlocked(("measured provider cost exceeds its reservation; further spending blocked"
                                        if state == "overrun" else
                                        "provider evidence is malformed or incomplete; original response preserved"
                                        if state == "incomplete" else
                                        "provider receipt or cost is unresolved; paid response preserved"),
                                       self.request_id)
                error.partial_response = data
                raise error

    def fail(self, error):
        if self.request_id:
            error.request_id = self.request_id
        if self.journal is not None and not self.finished:
            # KA (04/10, decisione PM «TI-RITENTATIVO-NON-FATTURATO»): un guasto che PROVA di
            # non aver mai raggiunto un modello (connessione mai stabilita, 402 di ammissione)
            # chiude la riga come 'released' (costo 0, con la prova) invece di 'unknown'.
            # Ogni altro guasto resta incerto e blocca la run come prima.
            wait = provably_unbilled(error)
            if wait is not None and not (self.response_started or self._checkpointed or self._pending):
                self._pending = []
                if self.journal.release_unbilled(self.request_id, error, reason=unbilled_reason(error)):
                    self.released = wait
                self.finished = True
                return
            try:
                self.flush()   # the partial evidence first, then the uncertain outcome
            except Exception as flush_error:
                self._pending = []
                print("[llm_client] checkpoint del guasto non scritti: " + type(flush_error).__name__
                      + ": " + str(flush_error)[:160])
            self.journal.fail(self.request_id, error, response=getattr(error, "partial_response", None))
            self.finished = True

    def checkpoint(self, chunk):
        if self.journal is not None and not self.finished:
            self._checkpointed = True
            self._pending.append(chunk)
            if (len(self._pending) >= CHECKPOINT_GRUPPO_CHUNK
                    or time.monotonic() - self._last_flush >= CHECKPOINT_GRUPPO_S):
                self.flush()

    def message(self, message):
        message.request_id = self.request_id
        message.replayed = self.saved is not None
        if message.usage is not None:
            message.usage.request_id = self.request_id
        return message


# RV-KA P3.4: acceso solo dentro sonda_modelli (ContextVar: vale per il thread/task corrente).
_SENZA_NUOVO_TENTATIVO = ContextVar("bellomberg_senza_nuovo_tentativo", default=False)


def _nuovo_tentativo_non_fatturato(attempt, error, rilasciati):
    """KA (04/10): secondi d'attesa per l'UNICO nuovo tentativo, o None (si rilancia).

    Solo una richiesta che il journal ha appena chiuso come 'released' si ritenta, e una
    sola volta per chiamata: il secondo guasto non fatturato si rilancia DICHIARATO
    (unbilled_retry_exhausted) con l'elenco delle richieste rilasciate.
    Limite (RV-KA P3.3): il tetto e' PER SINGOLA CHIAMATA; un chiamante che ripete la
    chiamata ottiene un altro giro (ogni riga 'released' porta comunque la sua prova).
    La sonda modelli non ritenta mai (RV-KA P3.4): la riga resta 'released', l'esito KO."""
    if attempt.released is not None:
        rilasciati.append(attempt.request_id)
    if rilasciati:
        error.unbilled_released = list(rilasciati)
    if attempt.released is None:
        return None
    if _SENZA_NUOVO_TENTATIVO.get():
        error.unbilled_retry_skipped = "sonda modelli: misura, non insiste"
        return None
    if len(rilasciati) > 1:
        error.unbilled_retry_exhausted = True
        try:
            print("[llm_client] secondo guasto non fatturato (" + type(error).__name__
                  + "): nessun terzo tentativo; richieste rilasciate " + ", ".join(rilasciati))
        except OSError:
            pass
        return None
    try:
        print("[llm_client] richiesta " + str(attempt.request_id) + " non fatturata ("
              + str(unbilled_reason(error)) + "): un solo nuovo tentativo fra "
              + format(attempt.released, "g") + " s")
    except OSError:
        pass
    return attempt.released


def _chunk_terminale(chunk):
    """Il chunk che chiude la risposta: finish_reason o usage (anche con choices: [])."""
    if not isinstance(chunk, dict):
        return False
    return chunk.get("usage") is not None or any(
        isinstance(c, dict) and c.get("finish_reason") is not None for c in (chunk.get("choices") or []))


def _oltre_la_durata_massima(scadenza, ric, chunk, resp):
    """B5: anche i keepalive passano di qui, quindi il tetto vale pure per uno stream che
    non manda mai contenuto. None = risposta salvata riletta offline (nessun tetto).
    REV G7/R4 (04/10): il tetto NON scatta sul chunk terminale (finish/usage) ne' dopo:
    una risposta completa e pagata arrivata a 7200,1 s resta una risposta, non un incerto.
    L'id di generazione dell'header del 200 (se il provider lo manda) viaggia con l'errore,
    cosi' anche uno stream di soli keepalive resta riconciliabile; senza id lo dice la
    riconciliazione («no provider generation id was captured»)."""
    if scadenza is None or ric.finish is not None or _chunk_terminale(chunk):
        return
    if time.monotonic() > scadenza:
        error = APITimeoutError("durata totale dello stream oltre DURATA_MAX_STREAM_S ("
                                + format(DURATA_MAX_STREAM_S, "g") + " s): chiamata interrotta, "
                                "esito incerto dichiarato (nessun nuovo invio automatico)")
        error.generation_id = getattr(resp, "headers", {}).get("x-generation-id")
        raise error


def _saved_stream_response(data):
    """Replay an attested message through the normal event composer, offline."""
    choice = data["choices"][0]
    delta = dict(choice["message"])
    if delta.get("tool_calls"):
        delta["tool_calls"] = [{**tool, "index": index} for index, tool in enumerate(delta["tool_calls"])]
    chunk = {**data, "choices": [{"delta": delta, "finish_reason": choice["finish_reason"]}]}
    return httpx.Response(200, content=("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())


def _finish_stream(stream):
    try:
        stream._ric.messaggio()  # validate terminal evidence before publishing it
        stream._attempt.receive(stream._ric.raw_response())
    except Exception as exc:
        exc.partial_response = stream._ric.raw_response()
        stream._attempt.fail(exc)
        stream._error = exc
        raise


class _Messages:
    def __init__(self, client):
        self._c = client

    def create(self, **kw):
        corpo_base = costruisci_corpo(stream=False, **kw)
        rilasciati = []
        while True:  # KA: al piu' UN nuovo tentativo, solo dopo un guasto certamente non fatturato
            attempt = _RequestAttempt(corpo_base)
            corpo = attempt.body
            forzato = _voleva_spento(kw) and corpo.get("reasoning") == REASONING_MINIMO
            data = None
            try:
                try:
                    data = attempt.saved if attempt.saved is not None else self._c._post_json(corpo)
                except APIStatusError as e:
                    if current_request_scope() is not None or not _e_ragionamento_obbligatorio(e, corpo):
                        raise
                    _forza_minimal(corpo)
                    forzato = True
                    data = self._c._post_json(corpo)
                attempt.response_started = True
                message = messaggio_da_json(data)
                attempt.receive(data)
                return attempt.message(_dichiara_forzato(message, forzato))
            except Exception as exc:
                if data is not None and not hasattr(exc, "partial_response"):
                    exc.partial_response = data
                attempt.fail(exc)
                wait = _nuovo_tentativo_non_fatturato(attempt, exc, rilasciati)
                if wait is None:
                    raise
            time.sleep(wait)

    def stream(self, **kw):
        corpo = costruisci_corpo(stream=True, **kw)
        return _StreamSync(self._c, corpo, forzato=_voleva_spento(kw)
                           and corpo.get("reasoning") == REASONING_MINIMO)


class OpenRouterClient:
    """Client SINCRONO. Stessa firma d'uso dell'SDK: `.messages.create(**kw)` / `.messages.stream(**kw)`.
    `trasporto` serve SOLO ai test (httpx.MockTransport): in produzione resta None."""

    def __init__(self, api_key=None, timeout=TIMEOUT_DEFAULT_S, max_retries=MAX_RETRIES_DEFAULT,
                 trasporto=None):
        self.api_key = api_key or chiave_api()
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._http = _nuovo_client_http(self.timeout, trasporto)
        self.messages = _Messages(self)

    # -- una POST con retry sugli status/errori transitori; ritorna la Response (status < 400)
    def _invia(self, corpo, stream=False, retry=None):
        retry = retry or _RetryBudget(self.max_retries)
        ultimo = None
        while True:
            headers = None
            try:
                req = self._http.build_request("POST", URL_CHAT, json=corpo,
                                               headers=_intestazioni(self.api_key))
                resp = self._http.send(req, stream=stream)
            except httpx.TimeoutException as e:
                ultimo = APITimeoutError("timeout dopo " + str(self.timeout) + " s: " + str(e))
                if isinstance(e, httpx.ConnectTimeout):
                    ultimo.transport_phase = "connect"  # no request bytes reached the provider
            except httpx.HTTPError as e:
                ultimo = APIConnectionError(type(e).__name__ + ": " + str(e))
                if isinstance(e, httpx.ConnectError):
                    ultimo.transport_phase = "connect"
            else:
                if resp.status_code < 400:
                    return resp
                if stream:
                    resp.read()
                ultimo = _errore_da_corpo(resp.status_code, _decodifica(resp))
                headers = resp.headers
                # The provider's generation id lets an uncertain outcome be reconciled later.
                ultimo.generation_id = headers.get("x-generation-id")
                ultimo.http_status = resp.status_code  # RV-KA: il code del corpo puo' differire
                resp.close()
                if not _ritentabile(resp.status_code):
                    raise ultimo
            delay = retry.delay(ultimo, headers)
            if delay is None:
                raise ultimo
            time.sleep(delay)

    def _post_json(self, corpo):
        resp = self._invia(corpo, stream=False)
        data = _decodifica(resp)
        if data is None:
            raise APIConnectionError("risposta non JSON (HTTP " + str(resp.status_code) + "): "
                                     + resp.text[:200])
        return data


class _StreamSync:
    def __init__(self, client, corpo, forzato=False):
        self._c = client
        self._corpo = corpo
        self._corpo_base = corpo  # KA: il nuovo tentativo riparte dalla richiesta originale
        self._forzato = forzato
        self._resp = None
        self._ric = _Ricomposizione()
        self._esaurito = False
        self._retry = _RetryBudget(client.max_retries)
        self._attempt = None
        self._error = None
        self._scadenza = None

    def __enter__(self):
        rilasciati = []
        while True:  # KA: al piu' UN nuovo tentativo, solo dopo un guasto certamente non fatturato
            if self._attempt is None:
                self._attempt = _RequestAttempt(self._corpo_base)
                self._corpo = self._attempt.body
            if self._attempt.saved is not None:
                self._resp = _saved_stream_response(self._attempt.saved)
                return self
            if self._scadenza is None:
                self._scadenza = time.monotonic() + DURATA_MAX_STREAM_S
            try:
                try:
                    self._resp = self._c._invia(self._corpo, stream=True, retry=self._retry)
                except APIStatusError as e:
                    if current_request_scope() is not None or not _e_ragionamento_obbligatorio(e, self._corpo):
                        raise
                    _forza_minimal(self._corpo)
                    self._forzato = True
                    self._resp = self._c._invia(self._corpo, stream=True, retry=self._retry)
            except Exception as exc:
                self._attempt.fail(exc)
                wait = _nuovo_tentativo_non_fatturato(self._attempt, exc, rilasciati)
                if wait is None:
                    raise
                self._attempt = None
                time.sleep(wait)
                continue
            self._attempt.response_started = True
            return self

    def __exit__(self, *a):
        if self._resp is not None:
            self._resp.close()
        if self._attempt is not None and not self._attempt.finished:
            error = a[1] if len(a) > 1 and a[1] is not None else APIConnectionError("stream senza ricevuta terminale")
            error.partial_response = self._ric.raw_response()
            self._attempt.fail(error)
        return False

    def __iter__(self):
        if self._resp is None:
            raise RuntimeError("usa `with client.messages.stream(...) as s:`")
        if self._esaurito:
            return
        try:
            while True:
                try:
                    for riga in self._resp.iter_lines():
                        chunk = _payload_sse(riga)
                        _oltre_la_durata_massima(self._scadenza, self._ric, chunk, self._resp)
                        if chunk is None:
                            continue
                        if chunk == "[DONE]":
                            break
                        self._attempt.checkpoint(chunk)
                        for ev in self._ric.alimenta(chunk):
                            yield ev
                    break
                except APIStatusError as error:
                    delay = (self._retry.delay(error, self._resp.headers)
                             if _stream_senza_output(self._ric) else None)
                    if delay is None:
                        raise
                    self._resp.close()
                    time.sleep(delay)
                    self._ric = _Ricomposizione()
                    self.__enter__()
        except httpx.HTTPError as e:
            error = APIConnectionError("stream interrotto: " + type(e).__name__ + ": " + str(e))
            error.partial_response = self._ric.raw_response()
            self._attempt.fail(error)
            self._error = error
            raise error from e
        except Exception as exc:
            exc.partial_response = self._ric.raw_response()
            self._attempt.fail(exc)
            self._error = exc
            raise
        finally:
            self._esaurito = True
        _finish_stream(self)
        for ev in self._ric.fine():
            yield ev

    def get_final_message(self):
        if self._error is not None:
            raise self._error
        for _ in self:
            pass
        return self._attempt.message(_dichiara_forzato(self._ric.messaggio(), self._forzato))


class _MessagesAsync:
    def __init__(self, client):
        self._c = client

    async def create(self, **kw):
        corpo_base = costruisci_corpo(stream=False, **kw)
        rilasciati = []
        while True:  # KA: al piu' UN nuovo tentativo, solo dopo un guasto certamente non fatturato
            attempt = _RequestAttempt(corpo_base)
            corpo = attempt.body
            forzato = _voleva_spento(kw) and corpo.get("reasoning") == REASONING_MINIMO
            data = None
            try:
                try:
                    data = attempt.saved if attempt.saved is not None else await self._c._post_json(corpo)
                except APIStatusError as e:
                    if current_request_scope() is not None or not _e_ragionamento_obbligatorio(e, corpo):
                        raise
                    _forza_minimal(corpo)
                    forzato = True
                    data = await self._c._post_json(corpo)
                attempt.response_started = True
                message = messaggio_da_json(data)
                attempt.receive(data)
                return attempt.message(_dichiara_forzato(message, forzato))
            except Exception as exc:
                if data is not None and not hasattr(exc, "partial_response"):
                    exc.partial_response = data
                attempt.fail(exc)
                wait = _nuovo_tentativo_non_fatturato(attempt, exc, rilasciati)
                if wait is None:
                    raise
            await asyncio.sleep(wait)

    def stream(self, **kw):
        corpo = costruisci_corpo(stream=True, **kw)
        return _StreamAsync(self._c, corpo, forzato=_voleva_spento(kw)
                            and corpo.get("reasoning") == REASONING_MINIMO)


class AsyncOpenRouterClient:
    """Client ASINCRONO (la chat SSE): `await .messages.create(**kw)` /
    `async with .messages.stream(**kw) as s: async for ev in s`."""

    def __init__(self, api_key=None, timeout=TIMEOUT_DEFAULT_S, max_retries=MAX_RETRIES_DEFAULT,
                 trasporto=None):
        self.api_key = api_key or chiave_api()
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._http = _nuovo_client_http_async(self.timeout, trasporto)
        self.messages = _MessagesAsync(self)

    async def _invia(self, corpo, stream=False, retry=None):
        retry = retry or _RetryBudget(self.max_retries)
        ultimo = None
        while True:
            headers = None
            try:
                req = self._http.build_request("POST", URL_CHAT, json=corpo,
                                               headers=_intestazioni(self.api_key))
                resp = await self._http.send(req, stream=stream)
            except httpx.TimeoutException as e:
                ultimo = APITimeoutError("timeout dopo " + str(self.timeout) + " s: " + str(e))
                if isinstance(e, httpx.ConnectTimeout):
                    ultimo.transport_phase = "connect"  # no request bytes reached the provider
            except httpx.HTTPError as e:
                ultimo = APIConnectionError(type(e).__name__ + ": " + str(e))
                if isinstance(e, httpx.ConnectError):
                    ultimo.transport_phase = "connect"
            else:
                if resp.status_code < 400:
                    return resp
                if stream:
                    await resp.aread()
                ultimo = _errore_da_corpo(resp.status_code, _decodifica(resp))
                headers = resp.headers
                # The provider's generation id lets an uncertain outcome be reconciled later.
                ultimo.generation_id = headers.get("x-generation-id")
                ultimo.http_status = resp.status_code  # RV-KA: il code del corpo puo' differire
                await resp.aclose()
                if not _ritentabile(resp.status_code):
                    raise ultimo
            delay = retry.delay(ultimo, headers)
            if delay is None:
                raise ultimo
            await asyncio.sleep(delay)

    async def _post_json(self, corpo):
        resp = await self._invia(corpo, stream=False)
        data = _decodifica(resp)
        if data is None:
            raise APIConnectionError("risposta non JSON (HTTP " + str(resp.status_code) + "): "
                                     + resp.text[:200])
        return data


class _StreamAsync:
    def __init__(self, client, corpo, forzato=False):
        self._c = client
        self._corpo = corpo
        self._corpo_base = corpo  # KA: il nuovo tentativo riparte dalla richiesta originale
        self._forzato = forzato
        self._resp = None
        self._ric = _Ricomposizione()
        self._esaurito = False
        self._retry = _RetryBudget(client.max_retries)
        self._attempt = None
        self._error = None
        self._scadenza = None

    async def __aenter__(self):
        rilasciati = []
        while True:  # KA: al piu' UN nuovo tentativo, solo dopo un guasto certamente non fatturato
            if self._attempt is None:
                self._attempt = _RequestAttempt(self._corpo_base)
                self._corpo = self._attempt.body
            if self._attempt.saved is not None:
                self._resp = _saved_stream_response(self._attempt.saved)
                return self
            if self._scadenza is None:
                self._scadenza = time.monotonic() + DURATA_MAX_STREAM_S
            try:
                try:
                    self._resp = await self._c._invia(self._corpo, stream=True, retry=self._retry)
                except APIStatusError as e:
                    if current_request_scope() is not None or not _e_ragionamento_obbligatorio(e, self._corpo):
                        raise
                    _forza_minimal(self._corpo)
                    self._forzato = True
                    self._resp = await self._c._invia(self._corpo, stream=True, retry=self._retry)
            except Exception as exc:
                self._attempt.fail(exc)
                wait = _nuovo_tentativo_non_fatturato(self._attempt, exc, rilasciati)
                if wait is None:
                    raise
                self._attempt = None
                await asyncio.sleep(wait)
                continue
            self._attempt.response_started = True
            return self

    async def __aexit__(self, *a):
        if self._resp is not None:
            await self._resp.aclose()
        if self._attempt is not None and not self._attempt.finished:
            error = a[1] if len(a) > 1 and a[1] is not None else APIConnectionError("stream senza ricevuta terminale")
            error.partial_response = self._ric.raw_response()
            self._attempt.fail(error)
        return False

    async def __aiter__(self):
        if self._resp is None:
            raise RuntimeError("usa `async with client.messages.stream(...) as s:`")
        if self._esaurito:
            return
        try:
            while True:
                try:
                    async for riga in self._resp.aiter_lines():
                        chunk = _payload_sse(riga)
                        _oltre_la_durata_massima(self._scadenza, self._ric, chunk, self._resp)
                        if chunk is None:
                            continue
                        if chunk == "[DONE]":
                            break
                        self._attempt.checkpoint(chunk)
                        for ev in self._ric.alimenta(chunk):
                            yield ev
                    break
                except APIStatusError as error:
                    delay = (self._retry.delay(error, self._resp.headers)
                             if _stream_senza_output(self._ric) else None)
                    if delay is None:
                        raise
                    await self._resp.aclose()
                    await asyncio.sleep(delay)
                    self._ric = _Ricomposizione()
                    await self.__aenter__()
        except httpx.HTTPError as e:
            error = APIConnectionError("stream interrotto: " + type(e).__name__ + ": " + str(e))
            error.partial_response = self._ric.raw_response()
            self._attempt.fail(error)
            self._error = error
            raise error from e
        except Exception as exc:
            exc.partial_response = self._ric.raw_response()
            self._attempt.fail(exc)
            self._error = exc
            raise
        finally:
            self._esaurito = True
        _finish_stream(self)
        for ev in self._ric.fine():
            yield ev

    async def get_final_message(self):
        if self._error is not None:
            raise self._error
        async for _ in self:
            pass
        return self._attempt.message(_dichiara_forzato(self._ric.messaggio(), self._forzato))


# ---------------------------------------------------------------------------
# SONDA DEI MODELLI (audit 11/09, Fable 5.1 — run 10/09 memo #53): il modello del red
# team era respinto da OpenRouter (HTTP 403, attestazione 18+) e lo si e' scoperto a
# meta' run, a Round 0 e 1 gia' pagati. Una call da pochi token per slug distinto,
# PRIMA del Round 0; esito dichiarato per slug, nessuna eccezione propagata, nessun
# ritentativo (un 403 e' lo stesso errore ripetuto). Non cambia nessuna model string.
# ---------------------------------------------------------------------------

def sonda_modelli(slugs, client=None, max_tokens=5, timeout_s=45.0):
    """{slug: {"ok": bool, "motivo": None|str, "durata_s": float}} per ogni slug distinto
    (ordine di prima apparizione). `client` finto nei test; in produzione OpenRouterClient
    senza retry: la sonda misura, non insiste. Anche il nuovo tentativo dopo un guasto
    certamente non fatturato (KA) e' spento qui: la riga resta 'released', l'esito KO."""
    token = _SENZA_NUOVO_TENTATIVO.set(True)
    try:
        return _sonda_modelli(slugs, client, max_tokens, timeout_s)
    finally:
        _SENZA_NUOVO_TENTATIVO.reset(token)


def _sonda_modelli(slugs, client, max_tokens, timeout_s):
    esiti = {}
    distinti = []
    for s in slugs or []:
        s = str(s or "").strip()
        if s and s not in distinti:
            distinti.append(s)
    if not distinti:
        return esiti
    if client is None:
        client = OpenRouterClient(timeout=timeout_s, max_retries=0)
    for s in distinti:
        t0 = time.perf_counter()
        try:
            response = client.messages.create(model=s, max_tokens=int(max_tokens),
                                   messages=[{"role": "user", "content": "ping"}],
                                   thinking={"type": "disabled"})
            usage = getattr(response, "usage", None)
            esiti[s] = {"ok": True, "motivo": None, "durata_s": round(time.perf_counter() - t0, 2),
                "request_id": getattr(response, "request_id", None),
                "response_id": getattr(response, "id", None),
                "cost_usd": getattr(usage, "cost_usd", None),
                "usage": usage.to_dict() if hasattr(usage, "to_dict") else None,
                "stop_reason": getattr(response, "stop_reason", None)}
        except Exception as e:
            esiti[s] = {"ok": False,
                        "request_id": getattr(e, "request_id", None), "cost_usd": None,
                        "motivo": (type(e).__name__ + ": " + str(e))[:300],
                        "durata_s": round(time.perf_counter() - t0, 2)}
    return esiti


def righe_log_sonda(esiti):
    """Righe di log '[OK] slug (0.8s)' / '[KO] slug: motivo', una per slug."""
    righe = []
    for s, e in (esiti or {}).items():
        if e.get("ok"):
            righe.append("[OK] modello %s (%ss)" % (s, e.get("durata_s", "?")))
        else:
            righe.append("[KO] modello %s: %s" % (s, e.get("motivo") or "causa n.d."))
    return righe
