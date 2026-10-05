"""errori_sicuri.py — messaggi d'eccezione senza chiavi API (G3, 04/10/2026, REV_G8 rilievo 2).

PERCHE' ESISTE
    `str(e)` di un errore di requests (ConnectionError, Timeout, raise_for_status) porta l'URL
    intero, querystring compresa: per FRED, NewsAPI, Marketaux, TheNewsAPI, GNews, Finnhub e
    Tiingo la chiave viaggia proprio li' (api_key / apiKey / api_token / apikey / token). Quel
    testo finiva nei log (~750 righe in news_feed.log) e, per FRED, nel valore restituito
    all'agente, cioe' nel prompt del modello. Modello di riferimento: polygon_data._senza_chiave.

CONTRATTO
    senza_segreti(testo, *segreti)  toglie (1) la querystring di ogni URL o percorso, (2) ogni
        parametro dal nome «chiave» (key/token/secret/password...) rimasto altrove, (3) ogni
        valore esplicito passato in `segreti` e ogni valore di variabile d'ambiente il cui nome
        contiene KEY/TOKEN/SECRET/PASSWORD (almeno 8 caratteri): anche un corpo d'errore che
        riecheggia la chiave senza URL.
    descrivi_eccezione(exc, *segreti)  tipo + stato HTTP + messaggio ripulito: abbastanza per
        diagnosticare (host, percorso, causa), mai la chiave.
"""
from __future__ import annotations

import os
import re

_OSCURATO = "***"
# URL o percorso seguito da querystring: si tiene il percorso, la query sparisce intera.
_QUERY = re.compile(r"(?P<base>(?:https?://|/)[^\s?'\"<>]*)\?[^\s'\"<>)]*")
_PARAMETRO = re.compile(
    r"(?i)\b(?P<nome>[a-z_]*(?:api[_-]?key|apikey|api[_-]?token|access[_-]?key|token|secret|password|key))"
    r"=(?P<valore>[^&\s'\"<>)]+)")
_NOMI_ENV = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def _segreti_da_env():
    for nome, valore in os.environ.items():
        if valore and len(valore) >= 8 and any(k in nome.upper() for k in _NOMI_ENV):
            yield valore


def senza_segreti(testo, *segreti) -> str:
    out = str(testo)
    valori = [s for s in segreti if isinstance(s, str) and len(s) >= 4]
    valori.extend(_segreti_da_env())
    for valore in sorted(set(valori), key=len, reverse=True):
        out = out.replace(valore, _OSCURATO)
    out = _QUERY.sub(lambda m: m.group("base") + "?" + _OSCURATO, out)
    out = _PARAMETRO.sub(lambda m: m.group("nome") + "=" + _OSCURATO, out)
    return out


def descrivi_eccezione(exc: BaseException, *segreti) -> str:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    testo = type(exc).__name__ + (" (HTTP %s)" % status if status is not None else "")
    messaggio = senza_segreti(exc, *segreti).strip()
    return testo + (": " + messaggio[:300] if messaggio else "")
