# -*- coding: utf-8 -*-
"""lookthrough_usa.py — il GEMELLO USA di un simbolo europeo, come PROXY DICHIARATO (04/10,
Opus 5.5, voce 0-quater).

Molte posizioni europee del book sono ETF/fondi con esposizione USA, o societa' con un ADR:
le fonti solo-USA (Quiver, Form 4, catena opzioni OPRA) non le coprono. Il PM conferma UNA
volta, nel negozio privato `data/gemelli_usa.json` (storage.negozi_privati.carica_gemelli,
scritto solo da tools/ops/proponi_gemelli_usa.py), quale strumento USA fa da proxy e per
quali usi. Qui si LEGGE e si ETICHETTA, nient'altro:

  esito_gemello(ticker, uso=None)   -> {"stato", "voce", "motivo"}: perche' c'e' o non c'e'
  gemello_confermato(ticker, uso=None) -> dict | None   (SOLO voci `confermato`)
  etichetta_proxy(voce, uso)        -> dict da mettere come PRIMA chiave del payload
  metti_in_testa(payload, etichetta) -> {"proxy": etichetta, **payload}
  source_con_proxy(source, etichetta) -> "<source> [PROXY: ...]" per il `_source` di _stamp

Regole: mai un gemello indovinato dal simbolo (nessuno `split(".")[0]` qui); una voce
`proposto` o `rifiutato` NON si usa mai; il negozio si rilegge a ogni chiamata; negozio
assente o illeggibile e' uno STATO dichiarato con motivo (regola PM 14/07), non un None muto
per chi chiede il perche' (`esito_gemello`). Nessun provider, nessun DB: il collegamento nei
wrapper dei tool lo fa chi integra, con uso opt-in esplicito (`proxy_usa=true`).
"""
import datetime as _dt
from typing import Any, Dict, Optional

from bellomberg.core.presentation import message as _message
from bellomberg.storage import negozi_privati as _np

# Le partecipazioni invecchiano: un factsheet trimestrale piu' un margine. Oltre questa eta'
# l'etichetta le dichiara STALE (non le nasconde: il desk decide sapendolo).
ETA_MAX_PARTECIPAZIONI_GIORNI = 120  # decisione PM 04/10

# Cosa misura il proxy, uso per uso (frase dell'etichetta).
_COSA = {
    "opzioni": ("opzioni e vol implicita", "options and implied volatility"),
    "congress": ("operazioni del Congresso USA", "US Congress trades"),
    "lobbying": ("spesa di lobbying USA", "US lobbying spend"),
    "gov_contracts": ("contratti del governo USA", "US government contracts"),
    "insider": ("operazioni degli insider (Form 4)", "insider trades (Form 4)"),
    "fondamentali": ("fondamentali", "fundamentals"),
    "notizie": ("notizie", "news"),
}


def _carica(path: Optional[str]) -> Dict[str, Any]:
    # PERCORSO letto a ogni chiamata (le prove lo puntano altrove con monkeypatch)
    return _np.carica_gemelli(path or _np.PERCORSO_GEMELLI)


def esito_gemello(ticker: str, uso: Optional[str] = None, path: Optional[str] = None) -> Dict[str, Any]:
    """{"stato": confermato | uso_non_ammesso | proposto | rifiutato | nessuna_voce |
    negozio_assente | negozio_illeggibile, "ticker": T, "uso": uso, "voce": dict | None,
    "motivo": str | None}. `voce` e' piena SOLO per `confermato` (con `ticker_book` dentro)."""
    t = str(ticker or "").strip().upper()
    if uso is not None and uso not in _np.USI_GEMELLO:
        raise ValueError(_message('uso {v0!r} sconosciuto: ammessi {v1}', 'Unknown use {v0!r}: allowed {v1}', v0=uso, v1=", ".join(_np.USI_GEMELLO)))
    negozio = _carica(path)
    base = {"ticker": t, "uso": uso, "voce": None}
    if negozio["origine"] in ("assente", "illeggibile"):
        return {"stato": "negozio_" + negozio["origine"], **base, "motivo": negozio["motivo"]}
    v = negozio["gemelli"].get(t)
    if v is None:
        return {"stato": "nessuna_voce", **base,
                "motivo": _message('nessun gemello USA dichiarato per {v0} (proponilo con tools/ops/proponi_gemelli_usa.py)', 'No US twin declared for {v0} (propose it with tools/ops/proponi_gemelli_usa.py)', v0=t)}
    if v["stato"] != "confermato":
        return {"stato": v["stato"], **base,
                "motivo": _message('gemello USA di {v0} in stato {v1}: si usa solo dopo la conferma del PM', 'US twin of {v0} is {v1}: usable only after PM confirmation', v0=t, v1=v["stato"])}
    if uso is not None and uso not in v["usi_ammessi"]:
        return {"stato": "uso_non_ammesso", **base,
                "motivo": _message('gemello USA di {v0} confermato per {v1}, non per {v2}', 'US twin of {v0} confirmed for {v1}, not for {v2}', v0=t, v1=", ".join(v["usi_ammessi"]), v2=uso)}
    return {"stato": "confermato", **base, "voce": {"ticker_book": t, **v}, "motivo": None}


def gemello_confermato(ticker: str, uso: Optional[str] = None, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """La voce CONFERMATA (con `ticker_book`), o None. Chi deve dire PERCHE' non c'e'
    (negozio assente, proposta non confermata, uso non ammesso) usa `esito_gemello`."""
    return esito_gemello(ticker, uso, path)["voce"]


def etichetta_proxy(voce: Dict[str, Any], uso: str, oggi: Optional[_dt.date] = None) -> Dict[str, Any]:
    """L'etichetta da mettere in TESTA al payload. Solleva ValueError su una voce non
    confermata o su un uso non ammesso: un'etichetta su un proxy non confermato sarebbe una
    firma falsa. `oggi` (default: la data della macchina) serve all'eta' delle partecipazioni."""
    if not isinstance(voce, dict) or voce.get("stato") != "confermato" or not voce.get("confermato_il") \
            or not voce.get("confermato_tramite"):
        raise ValueError(_message('etichetta PROXY rifiutata: la voce non e\' confermata dal PM', 'PROXY label refused: entry not confirmed by the PM'))
    if uso not in voce.get("usi_ammessi", ()):
        raise ValueError(_message('etichetta PROXY rifiutata: uso {v0!r} non ammesso per {v1}', 'PROXY label refused: use {v0!r} not allowed for {v1}', v0=uso, v1=voce.get("ticker_book")))
    oggi = oggi or _dt.date.today()
    t = voce["ticker_book"]
    rel = voce["relazione"]
    cosa = _message(_COSA[uso][0], _COSA[uso][1])
    out: Dict[str, Any] = {}
    if rel == "partecipazioni":
        p = voce["partecipazioni"]
        simboli = [x["simbolo_usa"] for x in p["voci"]]
        eta = (oggi - _dt.date.fromisoformat(p["data_riferimento"])).days
        stale = eta > ETA_MAX_PARTECIPAZIONI_GIORNI
        testo = _message(
            "PROXY: {v0} delle principali partecipazioni USA ({v1}) per {v2}, fonte {v3} al {v4}{v5}, confermato dal PM il {v6} ({v7})",
            "PROXY: {v0} of the main US holdings ({v1}) for {v2}, source {v3} as of {v4}{v5}, confirmed by the PM on {v6} ({v7})",
            v0=cosa, v1=", ".join(simboli), v2=t, v3=p["fonte"], v4=p["data_riferimento"],
            v5=_message(" (STALE: {v0} giorni)", " (STALE: {v0} days)", v0=eta) if stale else "", v6=voce["confermato_il"], v7=voce["confermato_tramite"])
        ticker_dati: Any = simboli
        out_extra = {"partecipazioni_al": p["data_riferimento"], "eta_partecipazioni_giorni": eta,
                     "partecipazioni_stale": stale}
    else:
        g = voce["gemello_usa"]
        if rel == "adr":
            testo = _message("PROXY: {v0} dell'ADR USA {v1} (stesso emittente) per {v2}, confermato dal PM il {v3} ({v4})",
                             "PROXY: {v0} of the US ADR {v1} (same issuer) for {v2}, confirmed by the PM on {v3} ({v4})",
                             v0=cosa, v1=g["simbolo"], v2=t, v3=voce["confermato_il"], v4=voce["confermato_tramite"])
        else:
            testo = _message("PROXY: {v0} dell'ETF USA gemello {v1} (stesso indice) per {v2}, confermato dal PM il {v3} ({v4})",
                             "PROXY: {v0} of the twin US ETF {v1} (same index) for {v2}, confirmed by the PM on {v3} ({v4})",
                             v0=cosa, v1=g["simbolo"], v2=t, v3=voce["confermato_il"], v4=voce["confermato_tramite"])
        ticker_dati = g["simbolo"]
        out_extra = {"evidenza": g["evidenza"], "fonte_evidenza": g["fonte"]}
    # `etichetta` per PRIMA: e' la frase che il desk legge e cita
    out["etichetta"] = testo
    out.update({"ticker_book": t, "ticker_dati": ticker_dati, "relazione": rel, "uso": uso,
                "confermato_il": voce["confermato_il"], "confermato_tramite": voce["confermato_tramite"]})
    out.update(out_extra)
    return out


def metti_in_testa(payload: Dict[str, Any], etichetta: Dict[str, Any]) -> Dict[str, Any]:
    """{"proxy": etichetta, **payload}: l'etichetta e' la PRIMA chiave, perche' il tool_result
    viene tagliato in CODA (chat_tools TETTO_TOOL_RESULT) e un'etichetta in fondo puo' sparire."""
    if "proxy" in payload:
        raise ValueError(_message("il payload ha gia' una chiave 'proxy': non la sovrascrivo", "Payload already has a 'proxy' key: not overwriting it"))
    return {"proxy": etichetta, **payload}


def source_con_proxy(source: str, etichetta: Dict[str, Any]) -> str:
    """Il `_source` che il desk cita in [src: ...]: porta la frase PROXY, non solo la fonte."""
    return "%s [%s]" % (source, etichetta["etichetta"])
