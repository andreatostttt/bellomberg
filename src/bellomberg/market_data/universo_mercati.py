"""Azioni per paese del cruscotto Mercati globali, lette dal provider a runtime.

Perche' (04/10/2026, decisione del maintainer): nel repository non ci sono elenchi
fissi di titoli, che potrebbero far intuire il portafoglio. L'insieme delle azioni
di ogni paese arriva dallo screener gratuito di Yahoo Finance (lo stesso provider
del resto della pagina): le prime N societa' del paese per capitalizzazione, con
prezzo e variazione di oggi nella stessa risposta.

Se lo screener non risponde, il paese torna VUOTO con `stato: "non_disponibile"` e
il motivo: nessun elenco sostitutivo.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

# Codice paese del cruscotto -> regione dello screener Yahoo. Solo paesi, nessun titolo.
# CN usa la borsa di Hong Kong, dove sono quotate le grandi societa' cinesi.
REGIONI: Dict[str, str] = {"US": "us", "IT": "it", "DE": "de", "FR": "fr", "UK": "gb",
                           "JP": "jp", "CN": "hk", "IN": "in", "BR": "br"}
PER_PAESE = 20
FONTE = "Yahoo Finance screener"


def _screen_yahoo(regione: str, quanti: int) -> Dict[str, Any]:
    """Una richiesta allo screener: azioni della regione, per capitalizzazione decrescente."""
    import yfinance as yf
    from yfinance.screener.query import EquityQuery
    return yf.screen(EquityQuery("eq", ["region", regione]), size=quanti,
                     sortField="intradaymarketcap", sortAsc=False) or {}


def _numero(valore: Any, cifre: int) -> Optional[float]:
    if isinstance(valore, bool) or not isinstance(valore, (int, float)):
        return None
    return round(float(valore), cifre)


def azioni_paese(paese: str, quanti: int = PER_PAESE,
                 screen: Optional[Callable[[str, int], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """{"paese", "azioni": [{ticker, name, price, change_pct}], "stato", "motivo", "fonte"}.

    `price`/`change_pct` restano None quando il provider non li da' (mai uno zero inventato).
    """
    paese = (paese or "").strip().upper()
    regione = REGIONI.get(paese)
    if regione is None:
        return {"paese": paese, "azioni": [], "stato": "non_disponibile",
                "motivo": "paese non gestito dal cruscotto", "fonte": FONTE}
    try:
        risposta = (screen or _screen_yahoo)(regione, quanti)
    except Exception as exc:
        return {"paese": paese, "azioni": [], "stato": "non_disponibile",
                # G8: solo il tipo: il testo di rete porta l'URL con la querystring
                "motivo": "screener non disponibile: %s" % type(exc).__name__,
                "fonte": FONTE}
    azioni: List[Dict[str, Any]] = []
    visti = set()
    for voce in (risposta.get("quotes") if isinstance(risposta, dict) else None) or []:
        if not isinstance(voce, dict) or voce.get("quoteType", "EQUITY") != "EQUITY":
            continue
        simbolo = voce.get("symbol")
        if not isinstance(simbolo, str) or not simbolo or simbolo in visti:
            continue
        visti.add(simbolo)
        azioni.append({"ticker": simbolo,
                       "name": voce.get("shortName") or voce.get("longName") or simbolo,
                       "price": _numero(voce.get("regularMarketPrice"), 4),
                       "change_pct": _numero(voce.get("regularMarketChangePercent"), 2)})
        if len(azioni) >= quanti:
            break
    if not azioni:
        return {"paese": paese, "azioni": [], "stato": "non_disponibile",
                "motivo": "lo screener non ha restituito azioni per la regione %r" % regione,
                "fonte": FONTE}
    return {"paese": paese, "azioni": azioni, "stato": "ok", "motivo": None, "fonte": FONTE}


def universo(paesi: Optional[List[str]] = None, quanti: int = PER_PAESE,
             screen: Optional[Callable[[str, int], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Azioni di tutti i paesi (una richiesta per paese, in parallelo).

    {"azioni": [{ticker, name, country, price, change_pct}],
     "paesi": {CC: {"stato", "motivo", "n"}}, "fonte", "motivo"}
    `motivo` (globale) e' valorizzato solo se nessun paese ha risposto.
    """
    elenco = list(paesi or REGIONI)
    with ThreadPoolExecutor(max_workers=min(len(elenco), 9) or 1) as pool:
        esiti = list(pool.map(lambda cc: azioni_paese(cc, quanti, screen), elenco))
    azioni: List[Dict[str, Any]] = []
    stato: Dict[str, Dict[str, Any]] = {}
    for esito in esiti:
        cc = esito["paese"]
        stato[cc] = {"stato": esito["stato"], "motivo": esito["motivo"], "n": len(esito["azioni"])}
        azioni.extend(dict(riga, country=cc) for riga in esito["azioni"])
    motivo = None
    if not azioni:
        motivo = "nessun paese disponibile dallo screener: " + "; ".join(
            "%s: %s" % (cc, s["motivo"]) for cc, s in stato.items())
    return {"azioni": azioni, "paesi": stato, "fonte": FONTE, "motivo": motivo}
