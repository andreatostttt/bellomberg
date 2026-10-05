"""
BELLOMBERG - Reddit Sentiment & Catalyst Scraper

Estrae top post da subreddit finance senza richiedere OAuth.
Reddit esponeva JSON publicamente su /r/<sub>/{top,hot,new}.json (con UA custom).
04/10/2026: NON PIU' — il JSON anonimo risponde 403 (pagina di blocco HTML);
l'esito misurato di ogni chiamata e' in `last_status()` (v. sezione DICHIARATO).
Decisione PM 04/10: fonte SPENTA (`FONTE_SPENTA = True`), nessuna rete.

Subreddit monitorati:
  - r/wallstreetbets : retail momentum / meme stocks
  - r/stocks         : analisi mainstream
  - r/options        : flow su opzioni
  - r/SecurityAnalysis : value/long-term
  - r/investing      : generalista

OUTPUT: lista di dict normalizzati (compatibile con news_aggregator schema):
  {title, snippet, url, provider, sentiment, relevance, ticker, published_at, score}

USO:
    from reddit_news import fetch_reddit_top, fetch_reddit_for_ticker
    posts = fetch_reddit_top(limit_per_sub=10)
    msft = fetch_reddit_for_ticker("MSFT", days=2)
"""
import threading
import time
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

try:
    import requests
    REQ_OK = True
except Exception:
    REQ_OK = False

# Custom UA obbligatorio per Reddit JSON API
USER_AGENT = "Bellomberg/0.5 (Personal AI Hedge Fund Terminal - by /u/anonymous)"

SUBREDDITS = {
    "wallstreetbets":   {"limit": 15, "min_score": 100,  "weight": 1.0},
    "stocks":           {"limit": 15, "min_score": 50,   "weight": 1.2},
    "options":          {"limit": 10, "min_score": 30,   "weight": 1.1},
    "SecurityAnalysis": {"limit": 8,  "min_score": 10,   "weight": 1.5},
    "investing":        {"limit": 10, "min_score": 30,   "weight": 1.0},
}

# Ticker regex: solo $TICKER o TICKER allcaps 2-5 lettere
TICKER_RE = re.compile(r"(?:\$([A-Z]{2,5})|\b([A-Z]{2,5})\b)")

# Blacklist parole comuni che matcherebbero il regex ma non sono ticker
COMMON_WORDS_BLACKLIST = {
    "IPO", "CEO", "CFO", "ETF", "USD", "EUR", "GBP", "FED", "ECB", "FOMC",
    "EPS", "PE", "PEG", "ROE", "ROI", "FCF", "EBITDA", "EBIT", "WACC",
    "OK", "YES", "NO", "I", "A", "AN", "THE", "IT", "IS", "AS", "AT",
    "TO", "OF", "BY", "ON", "IN", "OR", "BE", "ME", "MY", "WE", "US",
    "ALL", "AND", "BUT", "FOR", "NOT", "YOU", "CAN", "HAS", "HAD", "WHO",
    "ANY", "NEW", "OUT", "WAY", "TWO", "USE", "ONE", "OFF", "SEE", "GOT",
    "DD", "TLDR", "LOL", "IMO", "AFAIK", "BTW", "TBH", "FOMO", "DCA", "WSB",
    "GAIN", "LOSS", "HOLD", "BUY", "SELL", "PUMP", "DUMP", "MOON", "DROP",
    "BULL", "BEAR", "CALL", "PUT", "PUTS", "CALLS", "STOCK", "MARKET",
    "OPEN", "CLOSE", "HIGH", "LOW", "RED", "GREEN", "BLACK", "GOLD",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    "GOD", "WTF", "OMG", "IRL", "AMA", "NSFW", "MOD", "BAN", "EDIT",
    "FREE", "NOW", "WIN", "WOW", "FAR", "BEST", "GOOD", "BAD", "LOOK",
    "AI", "AR", "VR", "ML", "CPU", "GPU", "RAM", "SSD", "API",
}


def _log(msg: str):
    # riga ADDITIVA: se stdout e' morto o rifiuta l'encoding si perde LEI,
    # non il contratto verso il chiamante (stesso pattern di tiingo_news)
    try:
        print(f"[REDDIT] {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ── Reddit DICHIARATO (04/10, F7 — Opus 5.5) ────────────────────────────────
# Reddit non serve piu' il JSON anonimo: /r/<sub>/top.json risponde 403 con la
# pagina di blocco HTML (serve OAuth). Fino a oggi ogni guasto tornava `[]`,
# indistinguibile da «nessun post», e la ricerca per ticker non loggava nemmeno
# il non-200. Ora, come per Tiingo:
#   - una riga `[REDDIT] ...` nel log per ogni subreddit non-live (mai il testo
#     dell'eccezione: solo il TIPO) e una riga MUTO quando cadono tutti;
#   - `last_status()` = esito AGGREGATO dell'ULTIMA chiamata pubblica
#     (fetch_reddit_top o fetch_reddit_for_ticker) in questo processo.
# Il ritorno delle funzioni pubbliche resta una lista (compatibilita').
#
# SPENTA (decisione PM 04/10): niente OAuth, Reddit esce dalle fonti. Con
# `FONTE_SPENTA = True` le funzioni pubbliche NON fanno rete, tornano `[]`
# (ritorno invariato) e `last_status()` dice {"stato": "SPENTA", "motivo": ...}
# — mai una lista vuota zitta. Il codice di rete resta, dormiente e coperto dai
# test: per riaccenderla (es. con una chiave OAuth) basta il flag a False.
FONTE_SPENTA = True
MOTIVO_SPENTA = "Reddit tolto dalle fonti (decisione PM 04/10: 403 senza login)"
PATH_TOP = "/r/<sub>/<listing>.json"
PATH_SEARCH = "/r/<sub>/search.json"
_STATO_LOCK = threading.Lock()
_ULTIMO_STATO: Optional[Dict[str, Any]] = None


def _segna_esito(esito: Optional[Dict[str, Any]], stato: str,
                 http: Optional[int] = None) -> None:
    """Esito di UNA chiamata a un subreddit, nel dict passato dal chiamante."""
    if esito is not None:
        esito["stato"] = stato
        esito["http"] = http


def _segna_stato(esiti: Dict[str, Dict[str, Any]], path: str, n_item: int) -> None:
    """Esito aggregato dell'ultima chiamata pubblica.

    `live` se ALMENO un subreddit ha risposto 200 (i caduti restano elencati in
    `sub_ko`: copertura parziale DICHIARATA, non zitta); altrimenti lo stato del
    guasto piu' frequente (a parita', il primo incontrato) — `HTTP_403` oggi.
    `quando` e' solo un'ETICHETTA (wall-clock); l'eta' si misura su `mono`.
    """
    global _ULTIMO_STATO
    ok = [s for s, e in esiti.items() if e.get("stato") == "live"]
    ko = {s: e.get("stato", "n.d.") for s, e in esiti.items() if e.get("stato") != "live"}
    if ok:
        stato, http = "live", 200
    elif ko:
        stato = Counter(ko.values()).most_common(1)[0][0]
        http = next((e.get("http") for e in esiti.values() if e.get("stato") == stato), None)
    else:  # nessun subreddit interrogato: non e' un «live» con zero post
        stato, http = "NESSUN_SUBREDDIT", None
    if not ok and esiti:
        _log(f"MUTO: {stato} su {len(ko)}/{len(esiti)} subreddit ({path}) "
             f"— nessun post Reddit in questo giro")
    nuovo = {
        "stato": stato,
        "motivo": None,
        "http": http,
        "path": path,
        "quando": datetime.now().isoformat(timespec="seconds"),
        "mono": time.monotonic(),
        "n_item": n_item,
        "sub_ok": len(ok),
        "sub_tot": len(esiti),
        "sub_ko": ko,
    }
    with _STATO_LOCK:
        _ULTIMO_STATO = nuovo


def reddit_available() -> bool:
    """False se la fonte e' spenta per decisione PM o manca requests
    (stessa domanda di `tiingo_available`: «ha senso interrogarla?»)."""
    return bool(REQ_OK and not FONTE_SPENTA)


def _segna_spenta(path: str) -> None:
    """Fonte spenta per decisione PM: stato DICHIARATO, nessuna rete."""
    global _ULTIMO_STATO
    _log(f"SPENTA: {MOTIVO_SPENTA} — nessuna chiamata ({path})")
    nuovo = {
        "stato": "SPENTA",
        "motivo": MOTIVO_SPENTA,
        "http": None,
        "path": path,
        "quando": datetime.now().isoformat(timespec="seconds"),
        "mono": time.monotonic(),
        "n_item": 0,
        "sub_ok": 0,
        "sub_tot": 0,
        "sub_ko": {},
    }
    with _STATO_LOCK:
        _ULTIMO_STATO = nuovo


def last_status() -> Optional[Dict[str, Any]]:
    """Esito dell'ultima chiamata a `fetch_reddit_top`/`fetch_reddit_for_ticker`
    in QUESTO processo.

    None = mai interrogata (chi legge NON deve dichiarare la fonte muta).
    Forma: {"stato": "SPENTA"|"live"|"HTTP_<code>"|"ERRORE_<Tipo>"|
    "RISPOSTA_INATTESA"|"NESSUN_SUBREDDIT", "motivo": str|None (valorizzato
    per SPENTA), "http": int|None, "path": str, "quando": iso,
    "mono": float, "n_item": int, "sub_ok": int, "sub_tot": int,
    "sub_ko": {subreddit: stato}}. Restituisce una COPIA.
    """
    with _STATO_LOCK:
        if _ULTIMO_STATO is None:
            return None
        copia = dict(_ULTIMO_STATO)
        copia["sub_ko"] = dict(copia.get("sub_ko") or {})
        return copia


def reset_status() -> None:
    """Riporta a «mai interrogata» (per i test)."""
    global _ULTIMO_STATO
    with _STATO_LOCK:
        _ULTIMO_STATO = None


def _extract_tickers(text: str, max_tickers: int = 3) -> List[str]:
    """Estrae ticker mentionati nel post (preferendo $TICKER)."""
    found = []
    for m in TICKER_RE.finditer(text):
        dollar_t, plain_t = m.groups()
        t = dollar_t or plain_t
        if not t or t in COMMON_WORDS_BLACKLIST:
            continue
        if t not in found:
            found.append(t)
        if len(found) >= max_tickers:
            break
    return found


def _fetch_subreddit(subreddit: str, listing: str = "top", t: str = "day",
                      limit: int = 15,
                      esito: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Fetch top/hot da un singolo subreddit via JSON.

    `esito` (dict del chiamante) riceve {"stato", "http"} di QUESTA chiamata:
    la lista vuota da sola non distingue «zero post» da «Reddit muto»."""
    if not REQ_OK:
        _segna_esito(esito, "ERRORE_ImportError")
        return []
    url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
    params = {"limit": limit}
    if listing == "top":
        params["t"] = t  # hour|day|week|month|year|all
    try:
        r = requests.get(url, params=params, timeout=10,
                          headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            _log(f"  {subreddit} HTTP {r.status_code}")
            _segna_esito(esito, f"HTTP_{r.status_code}", r.status_code)
            return []
        data = r.json()
        if not isinstance(data, dict):
            _log(f"  {subreddit} risposta inattesa ({type(data).__name__})")
            _segna_esito(esito, "RISPOSTA_INATTESA", 200)
            return []
        posts = data.get("data", {}).get("children", [])
        out = []
        for p in posts:
            d = p.get("data", {})
            if d.get("stickied") or d.get("over_18"):
                continue
            title = d.get("title", "")
            selftext = (d.get("selftext", "") or "")[:400]
            score = d.get("score", 0)
            num_comments = d.get("num_comments", 0)
            created_utc = d.get("created_utc", 0)
            permalink = d.get("permalink", "")
            url = f"https://www.reddit.com{permalink}" if permalink else d.get("url", "")
            flair = d.get("link_flair_text", "") or ""

            tickers = _extract_tickers(title + " " + selftext)

            # Naive sentiment: flair + score-based
            flair_lower = flair.lower()
            sentiment = "neutral"
            if any(w in flair_lower for w in ["dd", "discussion"]):
                sentiment = "neutral"
            elif any(w in flair_lower for w in ["yolo", "gain", "moon"]):
                sentiment = "bullish"
            elif any(w in flair_lower for w in ["loss", "puts", "drill"]):
                sentiment = "bearish"

            out.append({
                "title": title[:200],
                "snippet": selftext[:300],
                "url": url,
                "provider": f"Reddit r/{subreddit}",
                "sentiment": sentiment,
                "relevance": min(10, max(1, int(score / 100))),  # score 100->1, 1000->10
                "ticker": tickers[0] if tickers else "",
                "tickers_mentioned": tickers,
                "score": score,
                "num_comments": num_comments,
                "flair": flair,
                "published_at": datetime.fromtimestamp(created_utc).isoformat() if created_utc else "",
            })
        _segna_esito(esito, "live", 200)
        return out
    except Exception as e:
        # solo il TIPO: il testo di requests contiene l'URL (oggi senza chiave,
        # domani con OAuth potrebbe non esserlo)
        _log(f"  {subreddit} {type(e).__name__}")
        _segna_esito(esito, f"ERRORE_{type(e).__name__}")
        return []


def fetch_reddit_top(listing: str = "top", t: str = "day",
                      filter_min_score: bool = True) -> List[Dict[str, Any]]:
    """Fetch top posts da TUTTI i subreddit configurati.

    Ritorno INVARIATO (lista); l'esito misurato e' in `last_status()`."""
    if FONTE_SPENTA:
        _segna_spenta(PATH_TOP)
        return []
    if not REQ_OK:
        _segna_stato({s: {"stato": "ERRORE_ImportError", "http": None} for s in SUBREDDITS},
                     PATH_TOP, 0)
        return []
    all_posts = []
    esiti: Dict[str, Dict[str, Any]] = {}
    for sub, cfg in SUBREDDITS.items():
        esiti[sub] = {}
        posts = _fetch_subreddit(sub, listing=listing, t=t, limit=cfg["limit"],
                                 esito=esiti[sub])
        if filter_min_score:
            posts = [p for p in posts if p.get("score", 0) >= cfg["min_score"]]
        # Apply per-sub weight to relevance
        weight = cfg.get("weight", 1.0)
        for p in posts:
            p["relevance"] = min(10, int(p["relevance"] * weight))
        all_posts.extend(posts)
        time.sleep(0.6)  # be nice to Reddit
    # Sort by score desc
    all_posts.sort(key=lambda p: -p.get("score", 0))
    n_ok = sum(1 for e in esiti.values() if e.get("stato") == "live")
    _log(f"fetched {len(all_posts)} posts from {n_ok}/{len(SUBREDDITS)} subs live")
    _segna_stato(esiti, PATH_TOP, len(all_posts))
    return all_posts


def fetch_reddit_for_ticker(ticker: str, days: int = 2,
                             include_subs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Cerca post che menzionano un ticker specifico negli ultimi N giorni.

    Ritorno INVARIATO (lista); l'esito misurato e' in `last_status()`. Prima
    del 04/10 i non-200 facevano `continue` senza nemmeno una riga di log."""
    if FONTE_SPENTA:
        _segna_spenta(PATH_SEARCH)
        return []
    subs = include_subs or list(SUBREDDITS.keys())
    if not REQ_OK:
        _segna_stato({s: {"stato": "ERRORE_ImportError", "http": None} for s in subs},
                     PATH_SEARCH, 0)
        return []
    cutoff = datetime.now() - timedelta(days=days)
    matches = []
    esiti: Dict[str, Dict[str, Any]] = {}
    ticker_upper = ticker.upper().split(".")[0]  # rimuovi suffisso .MI etc.
    for sub in subs:
        esiti[sub] = {}
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {
            "q": ticker_upper,
            "restrict_sr": "on",
            "sort": "top",
            "t": "week" if days <= 7 else "month",
            "limit": 10,
        }
        try:
            r = requests.get(url, params=params, timeout=10,
                              headers={"User-Agent": USER_AGENT})
            if r.status_code != 200:
                _log(f"  search {sub}/{ticker_upper} HTTP {r.status_code}")
                _segna_esito(esiti[sub], f"HTTP_{r.status_code}", r.status_code)
                continue
            data = r.json()
            if not isinstance(data, dict):
                _log(f"  search {sub}/{ticker_upper} risposta inattesa ({type(data).__name__})")
                _segna_esito(esiti[sub], "RISPOSTA_INATTESA", 200)
                continue
            posts = data.get("data", {}).get("children", [])
            _segna_esito(esiti[sub], "live", 200)
            for p in posts:
                d = p.get("data", {})
                if d.get("stickied") or d.get("over_18"):
                    continue
                created = datetime.fromtimestamp(d.get("created_utc", 0))
                if created < cutoff:
                    continue
                title = d.get("title", "")
                # Verifica menzione effettiva (no false match)
                text_full = (title + " " + (d.get("selftext", "") or "")).upper()
                if ticker_upper not in text_full:
                    continue
                matches.append({
                    "title": title[:200],
                    "snippet": (d.get("selftext", "") or "")[:300],
                    "url": f"https://www.reddit.com{d.get('permalink', '')}",
                    "provider": f"Reddit r/{sub}",
                    "sentiment": "neutral",  # caller can classify
                    "relevance": min(10, max(1, int(d.get("score", 0) / 50))),
                    "ticker": ticker,
                    "score": d.get("score", 0),
                    "num_comments": d.get("num_comments", 0),
                    "published_at": created.isoformat(),
                })
            time.sleep(0.4)
        except Exception as e:
            # solo il TIPO, mai il testo (contiene l'URL con la querystring)
            _log(f"  search {sub}/{ticker_upper} {type(e).__name__}")
            _segna_esito(esiti[sub], f"ERRORE_{type(e).__name__}")
            continue
    matches.sort(key=lambda p: -p.get("score", 0))
    _segna_stato(esiti, PATH_SEARCH, len(matches[:15]))
    return matches[:15]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # Cerca per ticker
        t = sys.argv[1].upper()
        posts = fetch_reddit_for_ticker(t, days=3)
        print(f"=== {len(posts)} posts mentioning {t} ===")
    else:
        posts = fetch_reddit_top(listing="top", t="day")
        print(f"=== Top {len(posts)} posts del giorno ===")
    for p in posts[:25]:
        tk = ",".join(p.get("tickers_mentioned", [])[:3]) or p.get("ticker", "")
        print(f"  [score={p.get('score',0):5d}] {p.get('provider','')[:25]:25s} | "
              f"tickers={tk:15s} | {p.get('title','')[:90]}")
