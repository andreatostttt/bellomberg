"""
BELLOMBERG - Live Price Updater

Aggiorna i prezzi nel database SQLite per tutte le posizioni attive.
Fonti (in priorita):
1. IBKR TWS (se aperto, port 7496/7497) - real-time o delayed
2. Tradegate (sede retail, real-time + chiusura di sede: orologio TR)
3. yfinance (sempre disponibile, delayed 15min su US e EU)
4. CoinGecko per i simboli dichiarati nel negozio privato dei prezzi speciali
   (data/prezzi_speciali.json, sezione `coingecko`; forma in prezzi_speciali.example.json)

Uso standalone:
    python price_updater.py              # un singolo update
    python price_updater.py --loop 60    # loop infinito ogni 60 secondi

Schedulazione automatica:
    Crea task Windows Task Scheduler che lancia "python price_updater.py" ogni 15 minuti.
"""
import sys
import time
import argparse
import threading
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Dict

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from bellomberg.storage.memory_db import MemoryDB

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False




# === FX CONVERSION CACHE ===
_FX_CACHE = {}
_FX_CACHE_AT = {}
_FX_CACHE_TTL_SECONDS = 300
_FX_LOCK = threading.RLock()
_FX_SOURCE_LAST: Dict[str, str] = {}  # 'live' | 'fallback' per currency, per audit

# Fallback statici se yfinance fallisce (Maggio 2026, rough approximations).
# Ultima rete di sicurezza per evitare che FX None rompa il NAV.
_FX_FALLBACK_TO_EUR = {
    "USD": 0.92,
    "GBP": 1.17,
    "GBX": 0.0117,
    "CHF": 1.03,
    "JPY": 0.0058,
    "HKD": 0.118,
    "CNY": 0.127,
    "BRL": 0.17,
}


def get_fx_to_eur(currency):
    """Return the CURRENCY->EUR rate with a five-minute live cache."""
    with _FX_LOCK:
        if currency is None or not str(currency).strip():
            return None
        currency = str(currency).strip().upper()
        if currency == "EUR":
            _FX_SOURCE_LAST["EUR"] = "identity"
            return 1.0
        if currency == "GBX":
            gbp_eur = get_fx_to_eur("GBP")
            return gbp_eur / 100.0 if gbp_eur else _FX_FALLBACK_TO_EUR.get("GBX")
        now = time.monotonic()
        cached = _FX_CACHE.get(currency)
        if (cached is not None and math.isfinite(cached) and cached > 0
                and now - _FX_CACHE_AT.get(currency, float("-inf")) <= _FX_CACHE_TTL_SECONDS):
            _FX_SOURCE_LAST[currency] = "live"
            return _FX_CACHE[currency]
        _FX_CACHE.pop(currency, None)
        _FX_CACHE_AT.pop(currency, None)
        if YFINANCE_AVAILABLE:
            try:
                hist = yf.Ticker("EUR" + currency + "=X").history(period="1d")
                if hist is not None and len(hist) > 0:
                    rate_eur_to_x = float(hist["Close"].iloc[-1])
                    if math.isfinite(rate_eur_to_x) and rate_eur_to_x > 0:
                        _FX_CACHE[currency] = 1.0 / rate_eur_to_x
                        _FX_CACHE_AT[currency] = now
                        _FX_SOURCE_LAST[currency] = "live"
                        return _FX_CACHE[currency]
            except Exception:
                pass
        fb = _FX_FALLBACK_TO_EUR.get(currency)
        if fb is not None:
            _FX_SOURCE_LAST[currency] = "fallback"
            print(f"[FX] WARN: using FALLBACK {currency}->EUR={fb} (yfinance unreachable, "
                  "non cachato: retry live al prossimo giro)", flush=True)
            return fb
        _FX_SOURCE_LAST.pop(currency, None)
        return None

def get_fx_sources():
    """Returns {currency: 'live'|'fallback'} for currencies fetched this session."""
    return dict(_FX_SOURCE_LAST)


def fx_sources_for(currencies, rates):
    """Fonte DICHIARATA per ogni valuta richiesta (blocco cassa/valute 03/08,
    regola 14/07 sul percorso della cassa): da rendere accanto ai tassi.
      'live'     = yfinance in sessione (freschezza governata dal chiamante)
      'fallback' = tasso statico di maggio 2026, NON un cambio vivo
      'assente'  = nessun tasso in risposta (buco dichiarato, non omesso)
      'n.d.'     = tasso presente ma fonte non tracciata (imprevisto:
                   dichiarato invece che indovinato)
    GBX deriva da GBP/100 (v. get_fx_to_eur) e ne eredita la fonte.
    """
    out = {}
    for cur in currencies:
        cur_u = (cur or "").upper()
        if cur_u not in rates:
            out[cur_u] = "assente"
            continue
        chiave = "GBP" if cur_u == "GBX" else cur_u
        out[cur_u] = _FX_SOURCE_LAST.get(chiave, "n.d.")
    return out


def get_fx_to_eur_con_fonte(currency):
    """Resolve rate and source atomically, including EUR and GBX identity/source."""
    with _FX_LOCK:
        if currency is None or not str(currency).strip():
            return None, "assente"
        cur = str(currency).strip().upper()
        rate = get_fx_to_eur(cur)
        rates = {cur: rate} if rate is not None else {}
        return rate, fx_sources_for([cur], rates)[cur]

def convert_to_eur(amount, currency):
    """Converte amount da currency a EUR."""
    fx = get_fx_to_eur(currency)
    if fx is None:
        return None
    return amount * fx


# Conversioni pubbliche di simboli canonici crypto. Gli alias personali broker->Yahoo
# vivono invece in data/alias_fonti.json, sezione `yfinance`.
_YFINANCE_CANONICI = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
}


class AliasFontiError(RuntimeError):
    """Il negozio alias non consente una risoluzione affidabile."""


def _alias_yfinance():
    from bellomberg.storage.negozi_privati import carica_alias
    esito = carica_alias()
    if esito["origine"] in ("assente", "illeggibile"):
        raise AliasFontiError("alias_fonti %s: %s" % (
            esito["origine"], esito["motivo"] or "motivo n.d."))
    return esito["alias"]["yfinance"]


def data_ticker(t):
    """Ticker Yahoo: canonici pubblici, poi alias privato riletto a ogni chiamata."""
    ticker = str(t or "").strip().upper()
    if not ticker:
        raise ValueError("ticker vuoto: alias yfinance non risolvibile")
    if ticker in _YFINANCE_CANONICI:
        return _YFINANCE_CANONICI[ticker]
    alias = _alias_yfinance()
    if ticker.endswith(".FRA") and ticker not in alias:
        raise AliasFontiError("alias yfinance mancante per %s in alias_fonti" % ticker)
    return alias.get(ticker, ticker)


def data_ticker_map(tickers, *, riservati=()):
    """Congela `{ticker reale: ticker Yahoo}` una volta per download e rinomina."""
    reali = [str(t or "").strip().upper() for t in tickers]
    if any(not t for t in reali):
        raise ValueError("ticker vuoto: alias yfinance non risolvibile")
    if not reali:
        return {}
    non_canonici = [t for t in reali if t not in _YFINANCE_CANONICI]
    alias = _alias_yfinance() if non_canonici else {}
    mancanti = [t for t in reali if t.endswith(".FRA") and t not in alias]
    if mancanti:
        raise AliasFontiError("alias yfinance mancante per %s in alias_fonti" % mancanti[0])
    out = {t: _YFINANCE_CANONICI.get(t, alias.get(t, t)) for t in reali}
    riservati_norm = {str(t).strip().upper() for t in riservati}
    for reale, dato in out.items():
        if reale != dato and (dato in riservati_norm or reale in riservati_norm):
            raise AliasFontiError(
                "alias yfinance non valido: %s risolve in %s e coinvolge un simbolo riservato" %
                (reale, dato))
    inversa = {}
    for reale, dato in out.items():
        if dato in inversa and inversa[dato] != reale:
            raise AliasFontiError("alias yfinance ambiguo: %s e %s risolvono entrambi in %s" %
                                  (inversa[dato], reale, dato))
        inversa[dato] = reale
    return out


class _YFinanceProxyMap(Mapping):
    """Compatibilita' per gli import legacy: vista Mapping viva, mai uno snapshot."""

    def __getitem__(self, key):
        ticker = str(key or "").strip().upper()
        if ticker in _YFINANCE_CANONICI:
            return _YFINANCE_CANONICI[ticker]
        alias = _alias_yfinance()
        if ticker not in alias:
            if ticker.endswith(".FRA"):
                raise AliasFontiError(
                    "alias yfinance mancante per %s in alias_fonti" % ticker)
            raise KeyError(key)
        return alias[ticker]

    def __iter__(self):
        return iter({**_alias_yfinance(), **_YFINANCE_CANONICI})

    def __len__(self):
        return len({**_alias_yfinance(), **_YFINANCE_CANONICI})


YFINANCE_PROXY_MAP = _YFinanceProxyMap()


# La mappa simbolo -> id CoinGecko sta nel NEGOZIO PRIVATO dei prezzi speciali
# (negozi_privati.carica_prezzi_speciali: data/prezzi_speciali.json, sezione `coingecko`,
# forma in prezzi_speciali.example.json), RILETTA A OGNI GIRO. Negozio assente = nessun id:
# non e' «nessun simbolo da prezzare cosi'», ed e' per questo che _run_once lo DICHIARA nel
# log a ogni giro, fuori da `if verbose` (il task schedulato gira con --quiet).
def prezzi_speciali():
    """L'esito INTERO del negozio: {"prezzi": {...}, "origine": ..., "motivo": ...}."""
    from bellomberg.storage.negozi_privati import carica_prezzi_speciali
    return carica_prezzi_speciali()


def _fetch_yfinance(ticker, valuta_posizione=None):
    """Tenta yfinance. Ritorna prezzo, valuta e relativa etichetta, oppure None."""
    if not YFINANCE_AVAILABLE:
        return None
    yf_ticker = data_ticker(ticker)
    try:
        tk = yf.Ticker(yf_ticker)
        hist = tk.history(period="1d")
        if hist is not None and len(hist) > 0:
            price = float(hist['Close'].iloc[-1])
            from bellomberg.storage.classificazione import valuta
            etichetta_valuta = valuta(ticker, valuta_posizione=valuta_posizione)
            return (price, etichetta_valuta.valore, etichetta_valuta.as_dict())
    except Exception:
        return None
    return None


def _fetch_coingecko(symbol, mappa):
    """CoinGecko per i simboli del negozio privato (sezione `coingecko`).
    `mappa` arriva dal chiamante, letta una volta per giro: senza default, cosi' un punto
    di chiamata dimenticato e' un TypeError e non «nessun id» zitto."""
    if not REQUESTS_AVAILABLE:
        return None
    cg_id = mappa.get(symbol.upper())
    if not cg_id:
        return None
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if cg_id in data and "usd" in data[cg_id]:
            return (float(data[cg_id]["usd"]), "USD")
    except Exception:
        return None
    return None


def _fetch_ibkr(ticker, port=7496):
    """IBKR TWS se aperto. Best-effort."""
    try:
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        from ib_async import IB, Stock
    except ImportError:
        return None
    ib = IB()
    try:
        ib.connect("127.0.0.1", port, clientId=99, timeout=3, readonly=True)
        try:
            ib.reqMarketDataType(4)  # delayed-frozen (free)
        except Exception:
            pass
        # Per ticker US semplici
        if "." not in ticker and len(ticker) <= 5:
            stock = Stock(ticker, "SMART", "USD")
            ib.qualifyContracts(stock)
            t = ib.reqMktData(stock, "", snapshot=True)
            ib.sleep(2)
            mp = t.marketPrice()
            if mp and mp == mp:
                return (float(mp), "USD")
    except Exception:
        return None
    finally:
        try: ib.disconnect()
        except Exception: pass
    return None


def _fetch_polygon(ticker):
    """DISATTIVATA come fonte di prezzi LIVE (19/08, Opus 5, ok PM).

    L'unica funzione polygon che abbiamo, `get_stock_daily`, rende AGGREGATI
    DAILY (`/v2/aggs/.../range/1/day/`): prenderne `bars[-1]["c"]` significa
    leggere la chiusura dell'ULTIMA BARRA CHIUSA, che a mercato aperto e'
    quella di IERI. Il confronto con quotazioni intraday ha confermato che gli
    aggregati daily ripetevano la chiusura precedente e falsavano NAV e P&L.

    Un prezzo vecchio spacciato per live e' PEGGIO di un buco dichiarato
    (regola PM 14/07): qui si restituisce None e la catena passa alla fonte
    successiva. Polygon resta in uso per OPZIONI/IV (`polygon_data`), dove
    l'endpoint e' quello giusto. Per riattivarla servirebbe un endpoint di
    last-trade/snapshot, non gli aggregati daily.
    """
    return None


# SEDE RETAIL (17/09, allineamento Trade Republic): Tradegate rende live +
# chiusura DI SEDE in keyless (refresh.php?isin=...): la baseline ha lo stesso
# orologio dell'app TR (L&S irraggiungibile da qui: ls-x.de parcheggiata,
# lsx.de timeout — probe 17/09, resta la candidata se torna su).
# La mappa simbolo -> ISIN di sede descrive il book, quindi vive nel negozio
# privato degli alias (data/alias_fonti.json, sezione tradegate), riletta a
# ogni chiamata. Verificare l'ISIN in sede: alcuni titoli USA su Tradegate
# stanno sotto un ISIN diverso da quello ufficiale (refresh vuoto con
# l'ufficiale). Un simbolo fuori mappa resta sul fallback Yahoo dichiarato.
def _isin_sede(ticker):
    return _negozio_sede()[0].get(str(ticker or "").strip().upper())


def _negozio_sede():
    """(mappa tradegate, motivo KO o None). Un negozio ILLEGGIBILE svuota la
    mappa: senza dirlo, tutti i ticker di sede passerebbero zitti a yfinance."""
    from bellomberg.storage.negozi_privati import carica_alias
    esito = carica_alias()
    if esito["origine"] == "illeggibile":
        return {}, "alias_fonti illeggibile: %s" % (esito["motivo"] or "motivo n.d.")
    return esito["alias"].get("tradegate", {}), None


VENUE_SOURCE = "tradegate"
VENUE_QUOTE_URL = "https://www.tradegatebsx.com/refresh.php"
# 04/10 (review P1-A): la sede quota TUTTO in euro. Il prezzo di sede si
# accetta SOLO per posizioni in EUR: un titolo USA mappato qui verrebbe
# scritto come USD 100 quando vale EUR 100 (~14% in meno senza avviso) e la
# serie salterebbe EUR/USD a ogni ripiego su yfinance. Niente conversione:
# il cambio sarebbe un secondo dato (fonte, ora) da cui dipenderebbe il P&L
# GG; il rifiuto dichiarato lascia il ticker alla fonte nella SUA valuta.
VENUE_CURRENCY = "EUR"
# 04/10 (review P2-D/E): la sede NON e' stata sondata su QUALE sessione porti
# `close`. Finche' non si conosce il campo che la data (sonda con rete), ogni
# chiusura di sede e' «non datata» e NON diventa baseline del P&L GG. Dopo la
# sonda: il nome del campo della risposta che porta la data della chiusura.
VENUE_CLOSE_DATE_FIELD = None
# 04/10 (review P2-G): frequenza DICHIARATA e configurabile; il sito non e'
# un'API documentata. Intervallo minimo fra due richieste per lo stesso ISIN
# e pausa crescente dopo un errore di rete/HTTP (raddoppia fino al tetto).
VENUE_POLL_MIN_SECONDS_DEFAULT = 120
VENUE_BACKOFF_MAX_SECONDS_DEFAULT = 1800
VENUE_UA = "Bellomberg/price-updater (installazione locale, poll per ISIN >= %ds)"
_VENUE_LOCK = threading.Lock()
_VENUE_STATO = {"ultima_richiesta": {}, "errori_consecutivi": 0,
                "pausa_fino": None, "ultimo_errore": None,
                "errori_isin": {}}   # isin -> (errori, pausa_fino, ultimo errore)


def _secondi_env(nome, default):
    """Secondi da env. ASSENTE = default; presente ma vuota, illeggibile,
    negativa o non finita = ValueError col NOME della variabile (revisione
    04/10 R-1: una variabile scritta male non vale default, come in
    news_refresh_manager e llm_client)."""
    import os
    grezzo = os.environ.get(nome)
    if grezzo is None:
        return default
    try:
        v = float(str(grezzo).strip())
    except ValueError:
        v = None
    if v is None or not math.isfinite(v) or v < 0:
        raise ValueError("%s: valore non valido %r (secondi >= 0; vuota non vale default)"
                         % (nome, grezzo))
    return v


def venue_config():
    """Frequenza della sede: {poll_min_seconds, backoff_max_seconds}.
    ValueError col nome della variabile se l'env e' presente ma non valida."""
    return {"poll_min_seconds": _secondi_env("VENUE_POLL_MIN_SECONDS",
                                             VENUE_POLL_MIN_SECONDS_DEFAULT),
            "backoff_max_seconds": _secondi_env("VENUE_BACKOFF_MAX_SECONDS",
                                                VENUE_BACKOFF_MAX_SECONDS_DEFAULT)}


def venue_config_dichiarata():
    """Per il risultato del giro: la frequenza, o {"errore": ...} se l'env e' invalida
    (il giro non cade: la sede non si interroga e ogni ticker mappato lo dichiara)."""
    try:
        return venue_config()
    except ValueError as e:
        return {"errore": str(e)}


def _errore_di_trasporto(e):
    """Rete/trasporto o rate limit del sito (429): riguarda TUTTA la sede.
    Ogni altro errore (HTTP 404/500 di un ISIN, JSON rotto) riguarda quell'ISIN."""
    tipi = [ConnectionError, TimeoutError]
    _exc = getattr(requests, "exceptions", None) if REQUESTS_AVAILABLE else None
    for nome in ("ConnectionError", "Timeout"):
        t = getattr(_exc, nome, None)
        if isinstance(t, type):
            tipi.append(t)
    if isinstance(e, tuple(tipi)):
        return True
    return getattr(getattr(e, "response", None), "status_code", None) == 429


def _parse_venue_date(x):
    """Data di sessione della sede: ISO (2026-09-16[...]) o tedesca
    (16.09.2026[...]). None se assente o illeggibile."""
    s = str(x or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_venue_number(x):
    """Numeri della sede: float/int nativi o stringhe tedesche ("54,80",
    "2.468,13"). Con la virgola si assume formato tedesco; senza, float
    diretto ("1.021" resta 1.021: le migliaia di sede arrivano con la
    virgola o lo spazio, mai col solo punto). None se illeggibile."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    s = str(x).strip().replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _interroga_sede(ticker, valuta_posizione):
    """La sede per un ticker: (esito, dati, motivo).
      'fuori_mappa' = ticker non mappato: la sede non lo riguarda, niente avviso;
      'ok'          = {"price", "currency", "venue_close", "venue_close_date"};
      'ko'          = rifiuto o errore: il chiamante DICHIARA il ripiego (motivo);
      'attesa'      = ISIN interrogato con successo meno di poll_min_seconds fa:
                      nessuna richiesta e nessun ripiego (l'ultimo prezzo di sede
                      resta il valido, con la sua eta' vera).
    `valuta_posizione` e' obbligatoria: senza, il caso misto EUR/USD non si vede."""
    mappa, ko_negozio = _negozio_sede()
    if ko_negozio:
        return "ko", None, "sede non interrogabile: " + ko_negozio
    isin = mappa.get(str(ticker or "").strip().upper())
    if not isin:
        return "fuori_mappa", None, None
    if valuta_posizione != VENUE_CURRENCY:
        return "ko", None, (
            "mappatura di sede rifiutata per %s: la sede quota in %s, la posizione "
            "e' in %s (nessuna conversione)" % (ticker, VENUE_CURRENCY,
                                                 valuta_posizione or "valuta n.d."))
    if not REQUESTS_AVAILABLE:
        return "ko", None, "sede non interrogabile: requests non installato"
    try:
        cfg = venue_config()
    except ValueError as e:
        return "ko", None, "sede non interrogata: configurazione invalida (%s)" % e
    adesso = time.monotonic()
    with _VENUE_LOCK:
        pausa = _VENUE_STATO["pausa_fino"]
        if pausa is not None and adesso < pausa:
            return "ko", None, "sede in pausa dopo %d errori consecutivi (ancora %ds; ultimo: %s)" % (
                _VENUE_STATO["errori_consecutivi"], int(pausa - adesso) + 1,
                _VENUE_STATO["ultimo_errore"])
        errori_isin = _VENUE_STATO.setdefault("errori_isin", {})
        n_isin, pausa_isin, ultimo_isin = errori_isin.get(isin, (0, None, None))
        if pausa_isin is not None and adesso < pausa_isin:
            return "ko", None, "sede: ISIN di %s in pausa dopo %d errori (ancora %ds; ultimo: %s)" % (
                ticker, n_isin, int(pausa_isin - adesso) + 1, ultimo_isin)
        ultima = _VENUE_STATO["ultima_richiesta"].get(isin)
        if ultima is not None and adesso - ultima[0] < cfg["poll_min_seconds"]:
            if ultima[1]:
                return "attesa", None, (
                    "sede interrogata %ds fa per %s (minimo %ds): nessuna nuova richiesta"
                    % (int(adesso - ultima[0]), ticker, cfg["poll_min_seconds"]))
            return "ko", None, ("sede: ultimo esito KO per %s %ds fa (minimo %ds fra due richieste)"
                                % (ticker, int(adesso - ultima[0]), cfg["poll_min_seconds"]))
        _VENUE_STATO["ultima_richiesta"][isin] = (adesso, False)
    try:
        r = requests.get(VENUE_QUOTE_URL, params={"isin": isin}, timeout=10,
                         headers={"User-Agent": VENUE_UA % cfg["poll_min_seconds"]})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        errore = "%s: %s" % (type(e).__name__, str(e)[:120])
        # 04/10 (revisione R-2): pausa GLOBALE solo per rete/trasporto/429; un
        # errore del singolo ISIN (404, 500, JSON rotto) mette in pausa solo lui,
        # altrimenti un ISIN rotto in mappa manderebbe tutti i mappati su yfinance.
        globale = _errore_di_trasporto(e)
        with _VENUE_LOCK:
            if globale:
                n = _VENUE_STATO["errori_consecutivi"] + 1
                _VENUE_STATO["errori_consecutivi"] = n
            else:
                n = _VENUE_STATO.setdefault("errori_isin", {}).get(isin, (0, None, None))[0] + 1
            pausa_s = min(cfg["backoff_max_seconds"],
                          max(cfg["poll_min_seconds"], 1.0) * 2 ** min(n - 1, 20))
            if globale:
                _VENUE_STATO["pausa_fino"] = time.monotonic() + pausa_s
                _VENUE_STATO["ultimo_errore"] = errore
            else:
                _VENUE_STATO["errori_isin"][isin] = (n, time.monotonic() + pausa_s, errore)
        return "ko", None, "sede KO per %s (%s): pausa %ds%s" % (
            ticker, errore, int(pausa_s), "" if globale else " per questo ISIN")
    with _VENUE_LOCK:
        _VENUE_STATO["errori_consecutivi"] = 0
        _VENUE_STATO["pausa_fino"] = None
        _VENUE_STATO.setdefault("errori_isin", {}).pop(isin, None)
    if not isinstance(data, dict) or not data:
        return "ko", None, "sede: risposta vuota o non leggibile per %s" % ticker
    live = _parse_venue_number(data.get("last"))
    if live is None:
        bid = _parse_venue_number(data.get("bid"))
        ask = _parse_venue_number(data.get("ask"))
        if bid is None or ask is None:
            return "ko", None, "sede: nessun prezzo leggibile (last, bid/ask) per %s" % ticker
        live = (bid + ask) / 2.0
    if not live > 0:
        return "ko", None, "sede: prezzo non positivo (%r) per %s" % (live, ticker)
    with _VENUE_LOCK:
        _VENUE_STATO["ultima_richiesta"][isin] = (adesso, True)
    data_chiusura = (_parse_venue_date(data.get(VENUE_CLOSE_DATE_FIELD))
                     if VENUE_CLOSE_DATE_FIELD else None)
    return "ok", {"price": live, "currency": VENUE_CURRENCY,
                  "venue_close": _parse_venue_number(data.get("close")),
                  "venue_close_date": data_chiusura}, None


def _fetch_venue(ticker, valuta_posizione):
    """Live + chiusura di sede, o None (il motivo lo da' `_interroga_sede`)."""
    esito, dati, _motivo = _interroga_sede(ticker, valuta_posizione)
    return dati if esito == "ok" else None


# FONTI PREZZI — UN SOLO POSTO (19/08, Opus 5). "polygon" e' TOLTO: serviva la
# chiusura di ieri come prezzo live (v. _fetch_polygon); resta per opzioni/IV.
# ⚠️ Prima queste tuple erano DUE: il default della firma e una copia dentro
# main() (riga 384). Il task schedulato passa da main(), quindi correggere solo
# la firma non avrebbe cambiato NULLA in produzione — e un test sulla firma
# sarebbe stato verde su un sistema ancora rotto. Una fonte sola, qui.
# 17/09: "tradegate" PRIMA (live+baseline di sede, orologio TR); yfinance resta
# il fallback dichiarato per i ticker fuori sede e i buchi di rete.
FONTI_PREZZI = ("ibkr", "tradegate", "yfinance", "coingecko")
FONTI_PREZZI_NO_IBKR = ("tradegate", "yfinance", "coingecko")
# Policy for the application-owned automatic cycle.  Manual/specialist callers
# may still pass FONTI_PREZZI (including IBKR) explicitly.
AUTOMATIC_PRICE_SOURCES = FONTI_PREZZI_NO_IBKR


# Fonti che MISURANO la valuta del prezzo che rendono (04/10, revisione R-5):
# la sede (sempre EUR), CoinGecko (chiesto in USD), IBKR (contratto USD).
# yfinance NO: la valuta nella sua tupla e' la classificazione del ticker
# (negozio/suffisso, `classificazione.valuta`), non la valuta letta da Yahoo,
# quindi confrontarla con la posizione confronterebbe due etichette nostre.
_FONTI_CON_VALUTA_MISURATA = (VENUE_SOURCE, "coingecko", "ibkr")


def _valuta_dichiarata_dalla_fonte(source_used, price_data):
    if source_used not in _FONTI_CON_VALUTA_MISURATA:
        return None
    if isinstance(price_data, dict):
        v = price_data.get("currency")
    elif isinstance(price_data, (tuple, list)) and len(price_data) > 1:
        v = price_data[1]
    else:
        v = None
    # una fonte che dovrebbe dichiarare la valuta e non lo fa non passa
    return str(v).strip().upper() if v else "n.d."


def update_all_prices(db, source_order=FONTI_PREZZI,
                      verbose=True, ibkr_port=7496):
    """Aggiorna prezzi per tutte le posizioni attive.
    Il negozio dei prezzi speciali si legge UNA volta per giro e il suo stato finisce nel
    risultato (`negozio_prezzi`): a negozio assente la fonte coingecko non ha nessun id e
    i simboli che dipendono da lei finirebbero fra i `failed` senza dire perche'."""
    _prezzi = prezzi_speciali()
    _mappa_cg = _prezzi["prezzi"]["coingecko"]
    snap = db.get_portfolio_summary()
    positions = snap.get("positions", [])
    if not positions:
        if verbose:
            print("[price_updater] No active positions in DB")
        return {"updated": 0, "failed": 0, "skipped": 0, "details": [],
                "negozio_prezzi": {"origine": _prezzi["origine"], "motivo": _prezzi["motivo"]},
                "venue": venue_config_dichiarata(),
                "timestamp": datetime.now().isoformat(timespec="seconds")}

    details = []
    updated = 0
    failed = 0
    skipped = 0

    for p in positions:
        ticker = p.get("ticker")
        if not ticker:
            continue

        from bellomberg.storage.classificazione import valuta
        etichetta_valuta = valuta(ticker, valuta_posizione=p.get("valuta"))

        price_data = None
        source_used = None
        source_errors = []
        sede_in_attesa = None

        for src in source_order:
            if src == "polygon":
                # Polygon paid: primario per ticker US (no suffisso)
                if "." not in ticker:
                    price_data = _fetch_polygon(ticker)
                    if price_data:
                        source_used = "polygon"
                        break
            elif src == "ibkr":
                # IBKR solo per ticker US senza punti
                if "." not in ticker and len(ticker) <= 5:
                    price_data = _fetch_ibkr(ticker, port=ibkr_port)
                    if price_data:
                        source_used = "ibkr"
                        break
            elif src == "tradegate":
                # Sede retail (17/09, orologio TR): live + chiusura di sede.
                # 04/10 (review P1-A/B): ogni esito che non e' un prezzo
                # (valuta della posizione diversa da quella di sede, rete giu',
                # pausa dopo errori, negozio illeggibile) finisce in
                # source_warnings e nel log ANCHE con --quiet: il ripiego sulla
                # fonte successiva e' dichiarato. 'attesa' ferma la catena:
                # niente ripiego (la serie non salta fra sedi ogni minuto).
                esito_sede, price_data, motivo_sede = _interroga_sede(
                    ticker, etichetta_valuta.valore)
                if esito_sede == "ok":
                    source_used = VENUE_SOURCE
                    break
                if esito_sede == "attesa":
                    sede_in_attesa = motivo_sede
                    break
                if esito_sede == "ko":
                    source_errors.append(motivo_sede + " -> ripiego sulla fonte successiva")
                    print("  [SEDE KO] %s: %s" % (ticker, motivo_sede))
            elif src == "yfinance":
                try:
                    price_data = _fetch_yfinance(ticker)
                except AliasFontiError as e:
                    source_errors.append(str(e))
                    # Il task schedulato usa --quiet: un alias irrisolto deve comparire
                    # comunque nel suo log, non soltanto nel payload di ritorno.
                    print("  [ALIAS YFINANCE KO] %s: %s" % (ticker, e))
                    price_data = None
                if price_data:
                    source_used = "yfinance"
                    break
            elif src == "coingecko":
                # solo i simboli dichiarati nella sezione `coingecko` del negozio
                price_data = _fetch_coingecko(ticker, _mappa_cg)
                if price_data:
                    source_used = "coingecko"
                    break

        if sede_in_attesa:
            skipped += 1
            details.append({"ticker": ticker, "source": VENUE_SOURCE,
                            "skipped": sede_in_attesa})
            continue

        if price_data:
            venue_close = None
            venue_close_date = None
            if isinstance(price_data, dict):
                # sede: v. _interroga_sede
                venue_close = price_data.get("venue_close")
                venue_close_date = price_data.get("venue_close_date")
                price = price_data.get("price")
            else:
                price = price_data[0]
            currency = etichetta_valuta.valore
            currency_label = etichetta_valuta.as_dict()
            if currency is None:
                failed += 1
                dichiarazione = ((currency_label or {}).get("dichiarazione")
                                  or "valuta n.d. (nessuna classificazione disponibile)")
                details.append({
                    "ticker": ticker, "price": price, "currency": None,
                    "currency_label": currency_label, "source": source_used,
                    "error": dichiarazione,
                })
                if verbose:
                    print("  [VALUTA N.D.] " + ticker + ": " + dichiarazione)
                continue
            valuta_fonte = _valuta_dichiarata_dalla_fonte(source_used, price_data)
            if valuta_fonte is not None and valuta_fonte != currency:
                # Seconda cintura (P1-A, estesa 04/10 R-5): un prezzo non entra MAI
                # con l'etichetta di un'altra valuta, qualunque fonte l'abbia portato.
                failed += 1
                errore = ("prezzo %s in %s su posizione in %s: non scritto"
                          % (source_used, valuta_fonte, currency))
                details.append({"ticker": ticker, "price": None, "currency": currency,
                                "source": source_used, "error": errore,
                                **({"source_warnings": source_errors} if source_errors else {})})
                print("  [VALUTA FONTE KO] " + ticker + ": " + errore)
                continue
            venue_close_nota = None
            try:
                db.update_price(ticker, price, valuta=currency, source=source_used)
                updated += 1
                if source_used == VENUE_SOURCE and venue_close is not None:
                    # La chiusura di sede e' la baseline del GG (orologio TR):
                    # scrittura ADDITIVA, mai bloccante per lo snapshot live.
                    # 04/10 (review P2-D/E): solo se DATATA dalla sede; una
                    # chiusura non datata non diventa baseline e lo si dice.
                    _save_close = getattr(db, "save_venue_close", None)
                    if not venue_close_date:
                        venue_close_nota = ("chiusura di sede non datata: non salvata, "
                                            "la baseline del P&L GG resta lo snapshot")
                    elif _save_close is not None:
                        try:
                            _save_close(ticker, venue_close, source=VENUE_SOURCE,
                                        data_sessione=venue_close_date)
                        except Exception as _vc_e:
                            venue_close_nota = "chiusura di sede non salvata: " + str(_vc_e)
                            if verbose:
                                print("  [VENUE] chiusura non salvata per "
                                      + ticker + ": " + str(_vc_e))
                pl_pct = None
                if p.get("prezzo_medio"):
                    pl_pct = (price - p["prezzo_medio"]) / p["prezzo_medio"] * 100
                detail = {
                    "ticker": ticker, "price": price, "currency": currency,
                    "source": source_used, "pl_pct": pl_pct,
                }
                if currency_label is not None:
                    detail["currency_label"] = currency_label
                if source_errors:
                    detail["source_warnings"] = source_errors
                if venue_close_nota:
                    detail["venue_close_nota"] = venue_close_nota
                details.append(detail)
                if verbose:
                    pl_str = "{:+.2f}%".format(pl_pct) if pl_pct is not None else "n/a"
                    print("  [OK] {:<15} {:<10} {:>12.4f} {} ({})".format(
                        ticker, currency, price, pl_str, source_used))
            except Exception as e:
                failed += 1
                if verbose:
                    print("  [DB FAIL] " + ticker + ": " + str(e))
        else:
            failed += 1
            if verbose:
                print("  [NO DATA] " + ticker)
            detail = {"ticker": ticker,
                      "error": " | ".join(source_errors) if source_errors
                               else "no data from any source"}
            if etichetta_valuta.valore is None:
                errore_dati = detail["error"]
                detail.update({"currency": None,
                               "currency_label": etichetta_valuta.as_dict(),
                               "error": etichetta_valuta.dichiarazione
                                        + " | " + errore_dati})
            details.append(detail)

    # fix #30 (motore contabile TWR): a fine giro prezzi riuscito persisti lo
    # snapshot NAV ufficiale del giorno (nav_snapshots). MAI bloccante:
    # twr_engine fa no-op con log se le tabelle non esistono ancora.
    if updated > 0:
        try:
            from bellomberg.portfolio.twr_engine import record_nav_snapshot
            record_nav_snapshot(db)
        except Exception as _twr_e:
            if verbose:
                print("  [TWR] snapshot NAV skipped: " + str(_twr_e))

    return {"updated": updated, "failed": failed, "skipped": skipped, "details": details,
            "negozio_prezzi": {"origine": _prezzi["origine"], "motivo": _prezzi["motivo"]},
            # frequenza della sede DICHIARATA (review P2-G), note se l'env e' illeggibile
            "venue": venue_config_dichiarata(),
            "timestamp": datetime.now().isoformat(timespec="seconds")}


def main():
    parser = argparse.ArgumentParser(description="Bellomberg - Live Price Updater")
    parser.add_argument("--loop", type=int, default=0,
                         help="Loop infinito ogni N secondi (es. 60 = ogni minuto). 0 = single run")
    parser.add_argument("--ibkr-port", type=int, default=7496, help="IBKR TWS port (7496 live, 7497 paper)")
    parser.add_argument("--no-ibkr", action="store_true", help="Skip IBKR, usa solo yfinance/CoinGecko")
    parser.add_argument("--quiet", action="store_true", help="Output minimale")
    args = parser.parse_args()

    sources = FONTI_PREZZI_NO_IBKR if args.no_ibkr else FONTI_PREZZI

    db = MemoryDB()

    def _run_once():
        print("\n[" + datetime.now().strftime("%H:%M:%S") + "] BELLOMBERG | price update starting")
        result = update_all_prices(db, source_order=sources, verbose=not args.quiet)
        if not args.quiet:
            print(f"  updated: {result.get('updated', 0)}  failed: {result.get('failed', 0)}")
        # Il negozio dei prezzi speciali si DICHIARA a ogni giro, FUORI da `if not
        # args.quiet`: il task schedulato gira con --quiet e una dichiarazione dentro il
        # ramo verboso sarebbe muta in produzione per costruzione (misurato sul log vivo).
        _neg = result.get("negozio_prezzi") or {}
        # sull'ORIGINE, non sul motivo: v. il commento gemello in portfolio_attribution
        if _neg.get("origine") in ("assente", "illeggibile"):
            print("  [PREZZI] KO dichiarato: negozio dei prezzi speciali %s: %s"
                  % (_neg.get("origine"), _neg.get("motivo")))
        if (result.get("venue") or {}).get("errore"):
            print("  [SEDE] KO dichiarato: configurazione invalida: " + result["venue"]["errore"])
        # IV History (voce quant P1, 25/07): snapshot giornaliero ATM IV/RR25 —
        # guard INTERNI al modulo (1 volta/giorno, weekend skip dichiarato);
        # un errore qui non deve MAI toccare l'aggiornamento prezzi.
        try:
            from bellomberg.market_data.iv_history import save_daily_snapshot
            iv = save_daily_snapshot()
            if iv.get("error"):
                # review M1: anche l'errore top-level (DB giu', data invalida)
                # va nel log — mai un buco muto in price_updater.log
                print("  [IV] KO dichiarato:", iv["error"])
            if iv.get("saved"):
                print(f"  iv_history: {iv['saved']}")
            for t, err in (iv.get("errors") or {}).items():
                print(f"  [IV] {t} KO dichiarato: {err}")
        except Exception as e:
            print("[IV] collector errore (dichiarato, prezzi non toccati):", str(e))
        # Trigger di revisione Trade Idea /4 (TI-RESEARCH-PIPELINE, Opus 5.5, PM 04/10):
        # date e prezzi dei candidati (get_price_live, MAI in position_prices) ->
        # voce RESEARCH PENDING + email. Righe SEMPRE stampate (anche --quiet);
        # un errore qui non deve MAI toccare l'aggiornamento prezzi.
        try:
            from bellomberg.market_data.trade_idea_watch_worker import run_from_updater, summary_lines
            for line in summary_lines(run_from_updater()):
                print(line)
        except Exception as e:
            # solo il tipo: il testo di un errore di rete puo' contenere URL con chiavi
            print("  [WATCH] KO dichiarato (prezzi non toccati):", type(e).__name__)
        return result

    if args.loop > 0:
        print(f"[BELLOMBERG] price loop every {args.loop}s. Ctrl+C to stop.")
        try:
            while True:
                _run_once()
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\n[BELLOMBERG] stopped.")
    else:
        _run_once()


if __name__ == "__main__":
    main()
