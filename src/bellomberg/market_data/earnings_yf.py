"""Data della prossima trimestrale da yfinance, per i titoli che Finnhub non copre.

Il piano gratuito di Finnhub risponde 403 su `/calendar/earnings` per i simboli non
USA (misurato il 02/10/2026 su titoli Xetra e Milano): per i titoli
europei senza un ADR dichiarato in `alias_fonti.json` la data arriva da
`yfinance.Ticker(...).calendar["Earnings Date"]`.

Quando yfinance da' DUE date e' una finestra stimata, non un annuncio: si prende la
prima e si dichiara `stimata=True` (la card la mostra come «data stimata»).
Una risposta riuscita resta in cache 12 ore; un errore non si mette in cache.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

_CACHE: Dict[str, tuple] = {}
_TTL_S = 12 * 3600

# Paese del listino dal suffisso di borsa (codici come le chip del calendario: UK, non GB).
# G8 (04/10/2026, Opus 5.5): prima ogni titolo in riserva era "EU", anche .T e .HK; un
# suffisso che non e' qui da' "?" (sconosciuto dichiarato), mai un paese dedotto.
PAESE_DA_SUFFISSO = {
    "MI": "IT", "DE": "DE", "F": "DE", "FRA": "DE", "PA": "FR", "L": "UK", "AS": "NL",
    "BR": "BE", "MC": "ES", "LS": "PT", "VI": "AT", "SW": "CH", "HE": "FI", "AT": "GR",
    "CO": "DK", "ST": "SE", "OL": "NO", "IR": "IE", "T": "JP", "HK": "HK", "SS": "CN",
    "SZ": "CN", "KS": "KR", "TW": "TW", "NS": "IN", "BO": "IN", "AX": "AU", "TO": "CA",
    "V": "CA", "SA": "BR", "MX": "MX",
}


def paese_da_suffisso(ticker: str) -> str:
    """'ACME.MI' -> 'IT'; senza suffisso -> 'US'; suffisso sconosciuto -> '?'."""
    t = (ticker or "").strip().upper()
    if "." not in t:
        return "US"
    return PAESE_DA_SUFFISSO.get(t.rsplit(".", 1)[1], "?")


def svuota_cache() -> None:
    _CACHE.clear()


def _come_data(valore: Any) -> Optional[date]:
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    try:
        return date.fromisoformat(str(valore)[:10])
    except ValueError:
        return None


def _leggi_calendario(simbolo: str) -> Any:
    import yfinance as yf  # import pigro: il modulo serve solo a questa riserva
    return yf.Ticker(simbolo).calendar


def prossima_trimestrale(simbolo: str, oggi: date,
                         motivo: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """{"date": ISO, "stimata": bool} della prossima trimestrale da `oggi` in poi, o None.
    `motivo` riceve la causa quando la fonte non risponde (mai un vuoto muto)."""
    salvato = _CACHE.get(simbolo)
    if salvato and time.time() - salvato[0] < _TTL_S:
        calendario = salvato[1]
    else:
        try:
            calendario = _leggi_calendario(simbolo)
        except Exception as exc:  # rete, simbolo sconosciuto, cambio di formato di Yahoo
            # G8: solo il tipo: il testo dell'eccezione di rete porta l'URL con la querystring
            if motivo is not None:
                motivo.append("yfinance %s: %s" % (simbolo, type(exc).__name__))
            return None
        _CACHE[simbolo] = (time.time(), calendario)
    grezze = calendario.get("Earnings Date") if isinstance(calendario, dict) else None
    if not isinstance(grezze, (list, tuple)):
        grezze = [grezze] if grezze else []
    date_valide = sorted(d for d in (_come_data(g) for g in grezze) if d is not None)
    if not date_valide or date_valide[-1] < oggi:
        return None
    prima = next((d for d in date_valide if d >= oggi), date_valide[-1])
    return {"date": prima.isoformat(), "stimata": len(set(date_valide)) > 1}
