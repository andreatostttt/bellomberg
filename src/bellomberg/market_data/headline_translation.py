"""Traduzione su richiesta dei titoli delle notizie di un titolo (scheda di Mercati globali).

Parte SOLO dal pulsante «Traduci» della scheda: una chiamata al modello economico delle
sintesi (NEWS_SUMMARY_MODEL) per tutti i titoli mancanti, con il costo registrato nel
ledger llm_usage come le sintesi. Le traduzioni restano in memoria per (lingua, titolo):
riaprire lo stesso titolo, o ritradurre un elenco già visto, non costa nulla.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

from bellomberg.core.language import capture_language, scoped_language

MAX_TITLES = 25
MAX_TITLE_CHARS = 300
_LANGUAGE_NAMES = {"it": "italiano", "en": "English"}
_CACHE: Dict[tuple, str] = {}
_LOCK = threading.Lock()


def _prompt(titles: List[str], language: str) -> str:
    target = _LANGUAGE_NAMES.get(language, language)
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    return (
        f"Traduci in {target} questi {len(titles)} titoli di notizie finanziarie, numerati da 0. "
        "Stile agenzia (Reuters, Bloomberg): breve, fedele, senza aggiungere nulla. Lascia invariati "
        "nomi di societa', ticker, sigle e cifre. Se un titolo e' gia' in quella lingua, ricopialo. "
        "Traduci ogni titolo separatamente: non unirli e non saltarne.\n"
        'Rispondi SOLO con JSON nella forma {"titles": [{"i": 0, "t": "titolo tradotto"}, ...]}, '
        "un elemento per ogni numero.\n\n" + numbered
    )


def _parse(message, expected: int) -> Dict[int, str]:
    """Traduzioni per indice. Accetta le forme che i modelli restituiscono davvero:
    [{"i": n, "t": ...}], {"0": ..., "1": ...}, oppure un elenco semplice (solo se completo).
    Un titolo mancante resta in originale; nessuna traduzione leggibile e' un errore."""
    from bellomberg.market_data.news_aggregator import _extract_first_json_value, _textual_llm_content
    text = _textual_llm_content(message)
    data = _extract_first_json_value(text)
    if isinstance(data, dict):
        inner = next((data[k] for k in ("titles", "translations", "items", "t") if k in data), None)
        data = inner if inner is not None else data
    out: Dict[int, str] = {}
    def put(index, value):
        try:
            i = int(index)
        except (TypeError, ValueError):
            return
        if 0 <= i < expected and isinstance(value, str) and value.strip():
            out[i] = value.strip()[:MAX_TITLE_CHARS]
    if isinstance(data, dict):
        for key, value in data.items():
            put(key, value)
    elif isinstance(data, list):
        if all(isinstance(v, str) for v in data):
            if len(data) == expected:
                for i, value in enumerate(data):
                    put(i, value)
        else:
            for pos, entry in enumerate(data):
                if isinstance(entry, dict):
                    index = next((entry[k] for k in ("i", "index", "id", "n") if k in entry), pos)
                    value = next((entry[k] for k in ("t", "title", "translation", "text") if k in entry), None)
                    put(index, value)
    if not out:
        _log(f"answer without readable translations ({expected} expected): {text[:500]!r}")
        raise ValueError(f"no readable translation among {expected} titles")
    if len(out) < expected:
        _log(f"partial answer: {len(out)}/{expected} titles translated")
    return out


def _log(message: str) -> None:
    print(f"[HEADLINE_TRANSLATION] {message}", flush=True)


@scoped_language
def translate_headlines(titles: List[str]) -> Dict[str, Any]:
    """Stati: not_configured / error / done. `titles` già ripuliti dal chiamante."""
    language = capture_language()
    clean = [str(t or "").strip()[:MAX_TITLE_CHARS] for t in titles][:MAX_TITLES]
    with _LOCK:
        missing = [t for t in dict.fromkeys(clean) if t and (language, t) not in _CACHE]
    if missing:
        from bellomberg.core import llm_client as lc
        from bellomberg.core import llm_usage as uso
        from bellomberg.market_data import article_summary as summary
        try:
            model = lc.modello(summary.LLM_FUNCTION)
            client = uso.nuovo_client()   # timeout dichiarato (REV_G3 R3)
        except lc.ConfigurazioneLLMMancante as exc:
            return {"status": "not_configured", "variable": getattr(exc, "variabile", summary.MODEL_VARIABLE)}
        started = time.monotonic()
        try:
            message = uso._call_model(client, model, _prompt(missing, language), max_tokens=summary.MAX_TOKENS)
        except Exception as exc:
            return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"[:400]}
        cost = uso._cost(model, getattr(message, "usage", None))
        uso._record_usage(model, cost, 1, round(time.monotonic() - started, 2), agent=summary.USAGE_AGENT)
        try:
            translated = _parse(message, len(missing))
        except Exception as exc:
            return {"status": "error", "detail": f"Unreadable model answer: {exc}"[:400]}
        with _LOCK:  # solo i titoli tradotti davvero: i mancanti si riprovano la prossima volta
            for i, tr in translated.items():
                _CACHE[(language, missing[i])] = tr
        paid = {"model": model, "cost_eur": cost["cost_eur"], "cost_status": cost["cost_status"]}
    else:
        paid = {"model": None, "cost_eur": None, "cost_status": None}
    with _LOCK:
        items = [_CACHE.get((language, t), t) for t in clean]
    complete = all((language, t) in _CACHE for t in clean if t)
    return {"status": "done", "language": language, "titles": items, "cached": not missing,
            "complete": complete, **paid}
