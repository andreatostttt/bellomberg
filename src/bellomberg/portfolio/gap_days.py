"""gap_days — scomposizione del P&L finestra multi-seduta per singola seduta.

Quando l'updater salta un giorno di borsa, `prev_close` resta indietro di piu'
di una seduta e il GG diventa una finestra (UI: etichetta 14/09->16/09). Questo
modulo la RIAPRE per seduta, senza riscrivere la storia:

- sedute CHIUSE: gambe chiusura-chiusura sulle chiusure ufficiali Yahoo
  (NON rettificate, auto_adjust=False: sedute di borsa, non NAV rettificato);
- seduta IN FORMAZIONE (la close di oggi e' NaN nel feed): gamba live snapshot
  contro l'ultima chiusura ufficiale — stessa gamba che il blotter attribuisce
  al GG, base dichiarata mista;
- STUB: tratto baseline-intraday -> prima chiusura (lun 11:07 -> chiusura lun):
  QUANTIFICATO, non nascosto. stub + gambe = live - baseline al centesimo.

Regole (no-fallback 14/07): nome senza chiusure = ESCLUSO e DICHIARATO in
unpriced; seduta senza close per un nome = missing DICHIARATO sul giorno;
FX mancante = nome escluso (mai nativo sommato come EUR). Le date-seduta sono
le etichette di borsa del feed (tz del mercato), mai date UTC degli snapshot.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional

try:
    import pandas as pd
    PANDAS_OK = True
except Exception:
    PANDAS_OK = False

try:
    import yfinance as yf
    YF_OK = True
except Exception:
    YF_OK = False

_CACHE: Dict[str, Any] = {}
CACHE_TTL_SEC = 600  # 10 min, come le analytics


def split_sessions(
    sessions: List[str],
    today: str,
    closes: Mapping[str, Mapping[str, Optional[float]]],
    baseline: Mapping[str, Optional[float]],
    qty: Mapping[str, float],
    live: Mapping[str, Optional[float]],
    fx: Mapping[str, Mapping[str, float]],
    qty_sod: Optional[Mapping[str, Mapping[str, float]]] = None,
    adds: Optional[Mapping[str, List[Mapping[str, float]]]] = None,
) -> Dict[str, Any]:
    """Gambe per seduta. `sessions` ascendenti (etichette di borsa), `today` la
    seduta in formazione. `fx[ticker][data]` = cambio->EUR della seduta/giorno.
    `qty_sod[ticker][seduta]` = qty a INIZIO seduta (default: qty odierna);
    `adds[ticker]` = acquisti in finestra [{qty, price, fx}], ancorati al costo
    sulla gamba live (il rally pre-acquisto non e' attribuito). Lo stub copre
    solo le qty pre-finestra. Senza i due opzionali il comportamento e' quello
    di prima."""
    days: Dict[str, Any] = {}
    for d in sessions[1:]:
        days[d] = {"pnl_eur": 0.0, "forming": False, "missing": [], "tickers": {}}
    if today not in days:
        days[today] = {"pnl_eur": 0.0, "forming": True, "missing": [], "tickers": {}}
    stub_eur = 0.0
    unpriced: List[str] = []
    no_baseline: List[str] = []
    sod = qty_sod or {}
    adw = adds or {}

    for ticker, per in closes.items():
        q = float(qty.get(ticker) or 0.0)
        if q == 0.0:
            continue
        valid = [(d, per.get(d)) for d in sessions
                 if per.get(d) is not None]
        if not valid:
            unpriced.append(ticker)
            continue
        nuove = adw.get(ticker) or []
        q_nuove = sum(float(a.get("qty") or 0.0) for a in nuove)
        q_vecchie = q - q_nuove
        base = baseline.get(ticker)
        if base is None:
            no_baseline.append(ticker)
        else:
            r0 = fx.get(ticker, {}).get(valid[0][0])
            if r0 is None:
                unpriced.append(ticker)
                continue
            stub_eur += q_vecchie * (float(valid[0][1]) - float(base)) * r0
        for (d0, c0), (d1, c1) in zip(valid, valid[1:]):
            r1 = fx.get(ticker, {}).get(d1)
            if r1 is None:
                days[d1]["missing"].append(ticker)
                continue
            qd = float(sod.get(ticker, {}).get(d1, q))
            leg = qd * (float(c1) - float(c0)) * r1
            days[d1]["pnl_eur"] += leg
            days[d1]["tickers"][ticker] = days[d1]["tickers"].get(ticker, 0.0) + leg
        anchor = valid[-1]
        lv = live.get(ticker)
        if lv is not None:
            rt = fx.get(ticker, {}).get(today)
            if rt is None:
                days[today]["missing"].append(ticker)
            else:
                leg = q_vecchie * (float(lv) - float(anchor[1])) * rt
                for a in nuove:
                    ra = a.get("fx") or rt
                    leg += float(a.get("qty") or 0.0) * (float(lv) - float(a["price"])) * float(ra)
                days[today]["pnl_eur"] += leg
                days[today]["tickers"][ticker] = days[today]["tickers"].get(ticker, 0.0) + leg
        for d in sessions[1:]:
            if per.get(d) is None and ticker not in days[d]["missing"]:
                days[d]["missing"].append(ticker)

    priced = [t for t in closes if t not in unpriced]
    if today in days:
        days[today]["forming"] = any(
            closes.get(t, {}).get(today) is None for t in priced)
    window_check = stub_eur + sum(v["pnl_eur"] for v in days.values())
    return {"days": days, "stub_eur": stub_eur, "window_check_eur": window_check,
            "unpriced": sorted(unpriced), "no_baseline": sorted(no_baseline),
            "error": None}


def _session_label(idx: Any) -> Optional[str]:
    """Etichetta di borsa della candela: la data nel fuso del mercato, mai UTC."""
    try:
        ts = pd.Timestamp(idx)
        return str(ts.date())
    except Exception:
        return None


def reconstruct(db: Any = None, force: bool = False) -> Dict[str, Any]:
    """Scompone la finestra multi-seduta corrente per seduta di borsa.

    Finestra: da min(prev_close_ts) a max(position_prices.timestamp) — le stesse
    date che la UI usa per l'etichetta 14/09->16/09. Chiusure ufficiali Yahoo
    NON rettificate + live snapshot per la seduta in formazione. Riusa i mattoni
    di portfolio_analytics (timeline qty, FX storico, negozio prezzi speciali)."""
    import time as _time
    hit = _CACHE.get("gap_days")
    if hit and not force and (_time.time() - hit[0]) < CACHE_TTL_SEC:
        return hit[1]
    if not PANDAS_OK or not YF_OK:
        return {"error": "dipendenze n.d. (pandas/yfinance): ricostruzione non calcolabile",
                "days": {}, "unpriced": []}
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.portfolio.portfolio_analytics import (
        _trade_history, _build_position_timeline, _qty_at,
        _build_fx_history, salta_prezzi,
    )
    if db is None:
        db = MemoryDB()
    summary = db.get_portfolio_summary()
    positions = summary.get("positions") or []
    covered = [p for p in positions
               if (p.get("quantita") or 0) > 0 and p.get("prev_close") is not None]
    if not covered:
        return {"error": None, "days": {}, "unpriced": [],
                "nota": "nessuna posizione con prev_close: niente da scomporre"}
    start = min(str(p["prev_close_ts"])[:10] for p in covered)
    with db._conn() as conn:
        row = conn.execute("SELECT MAX(timestamp) FROM position_prices").fetchone()
    newest = str(row[0])[:10] if row and row[0] else None
    if not newest:
        return {"error": "position_prices vuoto: live n.d.", "days": {}, "unpriced": []}
    tickers = sorted({p["ticker"] for p in covered})
    salta = salta_prezzi()
    speciali = sorted(t for t in tickers if t in salta)
    yf_tickers = [t for t in tickers if t not in salta]
    try:
        from bellomberg.cli.price_updater import data_ticker_map
        dl_map = data_ticker_map(yf_tickers) if yf_tickers else {}
    except Exception as e:
        return {"error": "alias yfinance non risolvibile (%s)" % e,
                "days": {}, "unpriced": []}
    dl_end = (datetime.strptime(newest, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        dl_list = sorted(set(dl_map.values()))
        raw = yf.download(dl_list, start=start, end=dl_end, progress=False,
                          auto_adjust=False, threads=True)
        if raw is None or raw.empty:
            raise ValueError("storico vuoto")
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        inv = {v: k for k, v in dl_map.items() if v != k}
        if isinstance(close, pd.DataFrame):
            close = close.rename(columns={c: inv.get(c, c) for c in close.columns})
        else:
            close = close.to_frame(dl_list[0]).rename(
                columns={dl_list[0]: inv.get(dl_list[0], dl_list[0])})
    except Exception as e:
        return {"error": "storico Yahoo n.d. (%s): sedute non ricostruibili" % e,
                "days": {}, "unpriced": []}
    labels: List[str] = []
    per_cell: Dict[str, Dict[str, Optional[float]]] = {t: {} for t in tickers}
    for idx, _row in close.iterrows():
        lab = _session_label(idx)
        if lab is None or lab < start or lab in labels:
            continue
        labels.append(lab)
        for t in yf_tickers:
            v = None
            try:
                vv = close.at[idx, t] if t in close.columns else None
                f = float(vv) if vv is not None else None
                v = f if f is not None and f == f else None
            except Exception:
                v = None
            per_cell[t][lab] = v
    sessions = sorted(labels)
    if len(sessions) < 1:
        return {"error": "nessuna seduta Yahoo nella finestra %s..%s" % (start, newest),
                "days": {}, "unpriced": []}
    today = sessions[-1]
    qtys = {p["ticker"]: float(p.get("quantita") or 0.0) for p in covered}
    all_trades = _trade_history()
    timeline = _build_position_timeline(all_trades)
    in_win = [t for t in all_trades
              if t.get("ticker") in set(tickers)
              and start < (t.get("data") or "")[:10] <= newest]
    # qty a INIZIO seduta (convenzione fine-giornata come l'attribution):
    # la gamba di martedi' non vede gli acquisti di mercoledi'.
    def _giorno_prima(iso: str) -> str:
        return (datetime.strptime(iso, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    qty_sod = {t: {d: _qty_at(timeline.get(t, []), _giorno_prima(d))
                   for d in sessions + ([today] if today not in sessions else [])}
               for t in tickers}
    trades_in_window = bool(in_win)
    sells_in_window = sorted({t.get("ticker") for t in in_win
                              if (t.get("action") or "").upper() in ("SELL", "TRIM")})
    # FX per seduta (brick) + live dallo snapshot (stesso fx del blotter)
    ccys = sorted({str((p.get("valuta") or "EUR")).upper() for p in covered} - {"EUR"})
    fx_hist = _build_fx_history(ccys, start, dl_end) if ccys else pd.DataFrame()
    fx: Dict[str, Dict[str, float]] = {}
    fx_missing: List[str] = []
    live_fx = {p["ticker"]: p.get("fx_to_eur") for p in covered}
    for p in covered:
        t, ccy = p["ticker"], str(p.get("valuta") or "EUR").upper()
        per: Dict[str, float] = {}
        if ccy == "EUR":
            per = {d: 1.0 for d in sessions + [today]}
        else:
            for d in sessions:
                try:
                    per[d] = float(fx_hist.loc[:d, ccy].iloc[-1])
                except Exception:
                    pass
            if len(per) < len(sessions):
                fx_missing.append(t)
                continue
        lf = live_fx.get(t)
        if lf is None:
            fx_missing.append(t)
            continue
        per[today] = float(lf)
        fx[t] = per
    kept = [t for t in tickers if t not in set(speciali) | set(fx_missing)]
    # base %: investito a INIZIO seduta (stessa convenzione del GG: % di ieri)
    sess_idx = {d: i for i, d in enumerate(sessions)}
    invested: Dict[str, float] = {}
    for d in sessions[1:] + [today]:
        tot = 0.0
        for t in kept:
            prev_d = sessions[sess_idx[d] - 1] if d in sess_idx and sess_idx[d] > 0 else None
            if d == today and today not in sess_idx:
                prev_d = sessions[-1]
            c = per_cell.get(t, {}).get(prev_d) if prev_d else None
            r = fx.get(t, {}).get(d)
            if c is not None and r is not None:
                tot += float(qty_sod.get(t, {}).get(d, qtys[t])) * float(c) * r
        invested[d] = tot
    # acquisti in finestra ancorati al costo (il rally pre-acquisto non attribuito)
    adds: Dict[str, List[Dict[str, float]]] = {}
    for t in in_win:
        if (t.get("action") or "").upper() not in ("BUY", "ADD"):
            continue
        tk = t["ticker"]
        if tk not in kept:
            continue
        d = (t.get("data") or "")[:10]
        ccy = str(next((p.get("valuta") for p in covered if p["ticker"] == tk), "EUR")).upper()
        if ccy == "EUR":
            rf = 1.0
        else:
            try:
                rf = float(fx_hist.loc[:d, ccy].iloc[-1])
            except Exception:
                rf = None
        if rf is None:
            continue
        adds.setdefault(tk, []).append(
            {"qty": float(t.get("quantita") or 0.0),
             "price": float(t.get("prezzo") or 0.0), "fx": float(rf)})
    approx_today = [t for t in sells_in_window if t in kept]
    core = split_sessions(
        sessions=sessions, today=today,
        closes={t: per_cell.get(t, {}) for t in kept},
        baseline={p["ticker"]: p.get("prev_close") for p in covered if p["ticker"] in kept},
        qty={t: qtys[t] for t in kept},
        live={p["ticker"]: p.get("prezzo_live") for p in covered if p["ticker"] in kept},
        fx={t: fx[t] for t in kept},
        qty_sod={t: qty_sod.get(t, {}) for t in kept},
        adds={t: v for t, v in adds.items() if t in kept and t not in approx_today},
    )
    core["approx_today"] = sorted(approx_today)
    core["unpriced"] = sorted(set(core["unpriced"]) | set(speciali) | set(fx_missing))
    live_px = {p["ticker"]: p.get("prezzo_live") for p in covered}
    base_px = {p["ticker"]: p.get("prev_close") for p in covered}
    window_live = sum(
        qtys[t] * (float(live_px[t]) - float(base_px[t])) * fx[t][today]
        for t in kept
        if live_px[t] is not None and base_px[t] is not None)
    # baseline_gap: le nuove azioni in pagina sono misurate dalla baseline di
    # lunedi', non dal costo di carico — il check esatto non lo e'. La somma
    # check + baseline_gap = window_live (GG di pagina) al centesimo.
    baseline_gap = sum(
        float(a["qty"]) * (float(a["price"]) - float(base_px[t])) * fx[t][today]
        for t, lst in adds.items() if t in kept and base_px.get(t) is not None
        for a in lst)
    for d, v in core["days"].items():
        base = invested.get(d, 0.0)
        v["invested_start_eur"] = base
        v["pnl_pct"] = (v["pnl_eur"] / base * 100.0) if base > 0 else None
    if not trades_in_window:
        qty_nota = ("qty invariate nella finestra (ultimo trade %s)" % _last_trade_date())
    else:
        bits = []
        if any(t not in approx_today for t in adds):
            bits.append("acquisti ancorati al costo")
        if approx_today:
            bits.append("vendite in finestra su %s: gamba live approssimata" % ", ".join(approx_today))
        qty_nota = "trade NELLA finestra: " + ("; ".join(bits) if bits else "qty di inizio seduta")
    out = {"window": {"start": start, "end": newest, "today": today},
           "qty_stable": not trades_in_window,
           "qty_nota": qty_nota,
           "window_live_eur": window_live,
           "baseline_gap_eur": baseline_gap,
           "sources": ("sedute chiuse: chiusure ufficiali Yahoo NON rettificate "
                       "(stessa fonte dei grafici MKT); seduta in formazione: live "
                       "snapshot vs ultima chiusura; FX storico per seduta, live per oggi"),
           **core}
    _CACHE["gap_days"] = (_time.time(), out)
    return out


def _last_trade_date() -> str:
    try:
        from bellomberg.portfolio.portfolio_analytics import _trade_history
        ds = [(t.get("data") or "")[:10] for t in _trade_history()]
        ds = [d for d in ds if d]
        return max(ds) if ds else "n.d."
    except Exception:
        return "n.d."
