"""
trade_idea_market_stats.py — statistiche di mercato PURE per il memo Trade Idea
(Lotto 3, costruttore L2, Opus 5.5, 04/10/2026).

Dal "pacchetto di mercato" (serie giornaliere scaricate dal costruttore L1 e
salvate nel checkpoint della run) calcola i fatti stilizzati del candidato:
rendimenti, momenti con curtosi in ECCESSO, Jarque-Bera, quantili per il
QQ-plot, cono di volatilita', drawdown ed episodi, ACF di |r| con Ljung-Box,
beta mobile contro il benchmark, sensibilita' al VIX, confronto coi peer.
Nessuna rete, nessun I/O, nessuna preferenza letta dal DB (lingua esplicita).

=====================================================================
SCHEMA DELL'INPUT (contratto con L1 = download e L3 = grafici)
=====================================================================
pack = {
  "version": 1,                        # PACK_VERSION
  "status": "ready" | "partial" | "unavailable",
  "reason": str | None,                # frase per il PM se partial/unavailable
  "as_of": "YYYY-MM-DD" | None,        # se presente: dati dopo questa data ignorati
  "provider": str,                     # es. "yfinance 1.4.0"
  "base_currency": "EUR",              # decisione PM 04/10: rendimenti in EUR
  "benchmark_ticker": "^GSPC",         # S&P 500 unico (PM 04/10)
  "peer_selection": {                  # facoltativo (anche dentro "multiples"),
    "method": "pm" | "select_peer_comps",   # riportato tal quale
    "note": str,                       # nota del selettore (scarti inclusi)
  },
  "multiples": {...},                  # di L1 per L3: qui ignorato
  "fetched_at", "window", "issues", "sha256": campi di L1, qui ignorati
  "series": {                          # ruolo -> SeriesEntry
    "candidate": SeriesEntry,          # obbligatorio
    "benchmark": SeriesEntry,          # facoltativo (senza: beta dichiarato n.d.)
    "vix": SeriesEntry,                # facoltativo (senza: sensibilita' VIX n.d.)
  },
  "peers": [SeriesEntry, ...],         # lista, anche vuota
}
SeriesEntry = {
  "ticker": str,                       # simbolo del fornitore
  "currency": str | None,              # dal metadata del fornitore, mai dedotta
  "price_field": "adj_close" | "close" | "level",   # adj_close per i titoli,
                                       # level per il VIX (indice, non prezzo)
  "source": str,                       # es. "yfinance Adj Close"
  "status": "ok" | "stale" | "error" | "missing",
  "reason": str | None,                # obbligatoria se status != ok
  "dates": ["YYYY-MM-DD", ...],        # crescenti, calendario del listino
  "values": [float | None, ...],       # None = buco del fornitore (mai fill)
  "native_currency": str | None,       # valuta del listino (metadata fornitore)
  "conversion": Conversion | None,     # fatta da L1 (currency = valuta DOPO)
  "index_type": "total_return" | "price" | None,   # benchmark: tipo di indice (riportato)
  "proxy": str | None,                 # frase se la serie e' un ripiego (dichiarato nei gap)
  "coverage": {...}, "sha256": str,    # di L1, qui ignorati (le soglie sono mie)
}
Conversion = {
  "method": "native" | "fx_same_date" | "none_level",
  "fx_ticker": str | None,             # es. "EURUSD=X"
  "fx_field": "Close", "rate_unit": "USD per EUR",
  "operation": str,                    # "prezzo nativo / cambio stesso giorno, senza fill"
  "n_dropped_no_fx": int,              # date tolte per cambio mancante
  "note": str,
}
Regola EUR (PM 04/10): candidato, benchmark (S&P 500 unico) e peer arrivano in
EUR con effetto cambio INCLUSO (currency == base_currency). Una serie di prezzo
in altra valuta o senza dichiarazione fx coerente si usa ma e' dichiarata nei
gap ("non convertita"). Il VIX e' un livello (price_field "level"): resta in
punti, fx None, nessuna conversione.
"stale" = serie usabile ma vecchia: si calcola e il buco e' dichiarato nei gap.
"error"/"missing" = serie non usabile: ogni statistica che la richiede e'
dichiarata "unavailable" con la reason del pacchetto.

=====================================================================
CONVENZIONI DELL'OUTPUT (dichiarate, una volta sola)
=====================================================================
- Percentuali in PUNTI (12.5 = 12,5%), come trade_idea_facts.
- Ogni blocco ha "status": "ok" | "insufficient" | "unavailable"; "ok" porta
  n_obs, start, end; "insufficient" porta n_obs e anni OSSERVATI e la reason;
  mai un numero calcolato su meno della soglia.
- Soglia di storia: MIN_YEARS = 3 anni di calendario coperti (prima data valida
  entro START_SLACK_DAYS dall'inizio teorico, per weekend/festivi) E almeno
  MIN_OBS_PER_YEAR * MIN_YEARS rendimenti. Vale per ogni serie e per ogni
  coppia (sulle date COMUNI).
- Rendimenti: LOG per i fatti stilizzati (momenti, QQ, ACF, Ljung-Box, VIX);
  SEMPLICI per cono di volatilita' (convenzione di vol_cone), beta (convenzione
  di return_statistics) e peer. Un rendimento esiste solo fra due osservazioni
  valide ADIACENTI nel calendario della serie e a distanza di al piu'
  MAX_SKIPPED_WEEKDAYS giorni feriali saltati (festivi): un buco (None) o un
  vuoto di calendario piu' lungo toglie il rendimento a cavallo (contato e
  dichiarato), mai un rendimento a piu' giorni spacciato per giornaliero.
- Coppie (beta, VIX): intersezione delle date valide PRIMA dei rendimenti,
  rendimenti fra date comuni consecutive (modello beta_reference_evidence) SOLO se
  fra le due date nessuna delle due serie ha un buco (None) e i feriali saltati
  sono al massimo MAX_SKIPPED_WEEKDAYS (un festivo di un solo listino e' ammesso:
  rendimento sincrono su due sedute); dichiarati n prima, dopo e scartati.
  Mai allineamento per posizione.
- Beta (decisione PM 04/10): misura PRINCIPALE su rendimenti SETTIMANALI
  (ultima data comune di ogni settimana ISO, di norma il venerdi'), solo fra
  settimane CONSECUTIVE (una settimana senza data comune e' un buco dichiarato);
  beta mobile su 52 settimane. Il giornaliero resta come confronto, dichiarato
  distorto dalle chiusure asincrone Europa/USA quando i listini sono diversi.
- Settimanali/mensili dei momenti: solo fra periodi consecutivi, come sopra.
- Curtosi: scipy.stats.kurtosis default (fisher=True, bias=True) e' GIA'
  l'eccesso: non si sottrae 3 (errore 1 del notebook Rubin). Std con ddof=1.
- Jarque-Bera: scipy.stats.jarque_bera (curtosi grezza dentro, corretto).
- Ljung-Box: statsmodels acorr_ljungbox, riga letta PER LAG (errore 4 del notebook).
- Drawdown: sui prezzi validi della finestra (portfolio_tearsheet._drawdown_episodes);
  le durate sono in OSSERVAZIONI di borsa, non giorni di calendario.
- Valute: rendimenti nella valuta del proprio listino; se candidato e benchmark
  hanno valute diverse il gap lo dichiara.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict

import numpy as np
from scipy import stats as _st

from bellomberg.core.language import text as _text, validate_language
from bellomberg.market_data.return_statistics import paired_beta_statistics
from bellomberg.portfolio.portfolio_tearsheet import _drawdown_episodes
from bellomberg.portfolio.vol_cone import build_cone

PACK_VERSION = 1
STATS_VERSION = 1
MIN_YEARS = 3
DEFAULT_WINDOW_YEARS = 5        # decisione PM 04/10
BASE_CURRENCY = "EUR"           # decisione PM 04/10
MIN_OBS_PER_YEAR = 240          # densita' minima: ~252 sedute meno festivi/buchi
START_SLACK_DAYS = 7            # tolleranza all'inizio (weekend, festivi)
CONE_WINDOWS = (21, 63, 126, 252)
BETA_WINDOW = 52                # settimane per il beta mobile (PM 04/10)
MIN_WEEKLY_PER_YEAR = 48        # densita' minima dei rendimenti settimanali
MAX_SKIPPED_WEEKDAYS = 2        # feriali saltati ammessi fra due osservazioni (festivi)
WEEK_RULE = "ultima chiusura comune della settimana ISO, solo settimane consecutive"
ROLLING_STALE_DAYS = 10         # ultimo beta mobile piu' vecchio della fine: dichiarato
ACF_NLAGS = 40
LJUNG_BOX_LAGS = (5, 10, 20)
QQ_MAX_POINTS = 400
HIST_BINS = 50
TRADING_DAYS = 252
ROLES = ("candidate", "benchmark", "vix")
SERIES_STATUSES = ("ok", "stale", "error", "missing")
PRICE_FIELDS = ("adj_close", "close", "level")


class Conversion(TypedDict, total=False):
    method: str
    fx_ticker: Optional[str]
    fx_field: str
    rate_unit: str
    operation: str
    n_dropped_no_fx: int
    note: str


class SeriesEntry(TypedDict, total=False):
    ticker: str
    currency: Optional[str]
    price_field: str
    source: str
    status: str
    reason: Optional[str]
    dates: List[str]
    values: List[Optional[float]]
    native_currency: Optional[str]
    conversion: Optional[Conversion]
    coverage: Dict[str, Any]
    sha256: str


class MarketPack(TypedDict, total=False):
    version: int
    status: str
    reason: Optional[str]
    as_of: Optional[str]
    provider: str
    base_currency: str
    benchmark_ticker: str
    peer_selection: Dict[str, Any]
    multiples: Dict[str, Any]
    series: Dict[str, SeriesEntry]
    peers: List[SeriesEntry]


# ---------------------------------------------------------------- utilita'

class _Gaps:
    """Gap in lingua piana, deduplicati nell'ordine di arrivo."""

    def __init__(self, language: str):
        self.language = language
        self.items: List[str] = []
        self.base = BASE_CURRENCY

    def _num(self, v: Any) -> Any:
        """Numeri nella forma della lingua (it: 1.288 e 2,5), stessa funzione dei grafici."""
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return v
        from bellomberg.reporting.trade_idea_charts import fmt_number
        if isinstance(v, int):
            return fmt_number(v, 0, self.language)
        dec = fmt_number(v, 2, self.language)
        sep = "," if self.language == "it" else "."
        return dec.rstrip("0").rstrip(sep) if sep in dec else dec

    def t(self, it: str, en: str, **values) -> str:
        return _text(it, en, language=self.language).format(
            **{k: self._num(v) for k, v in values.items()})

    def add(self, it: str, en: str, **values) -> str:
        s = self.t(it, en, **values)
        if s not in self.items:
            self.items.append(s)
        return s


def _iso(d: date) -> str:
    return d.isoformat()


def _years_back(end: date, years: int) -> date:
    try:
        return end.replace(year=end.year - years)
    except ValueError:              # 29 febbraio
        return end.replace(year=end.year - years, day=28)


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _clean(entry: Dict[str, Any], as_of: Optional[date], positive: bool
           ) -> Tuple[List[date], List[Optional[float]], int]:
    """Date e valori come date/float; valori non validi -> None (contati).
    Date dopo as_of tagliate. Solleva ValueError su forma rotta."""
    dates = entry.get("dates")
    values = entry.get("values")
    if not isinstance(dates, list) or not isinstance(values, list) or len(dates) != len(values):
        raise ValueError("dates/values assenti o di lunghezza diversa")
    out_d: List[date] = []
    out_v: List[Optional[float]] = []
    invalid = 0
    for ds, v in zip(dates, values):
        d = date.fromisoformat(str(ds)[:10])
        if as_of is not None and d > as_of:
            continue
        if out_d and d <= out_d[-1]:
            raise ValueError("date non strettamente crescenti")
        ok = _finite(v) and (float(v) > 0 if positive else True)
        if v is not None and not ok:
            invalid += 1
        out_d.append(d)
        out_v.append(float(v) if ok else None)
    return out_d, out_v, invalid


def _window_cut(dates: List[date], values: List[Optional[float]], start: date
                ) -> Tuple[List[date], List[Optional[float]]]:
    i = 0
    while i < len(dates) and dates[i] < start:
        i += 1
    return dates[i:], values[i:]


def _weekdays_between(d0: date, d1: date) -> int:
    """Giorni lun-ven strettamente fra d0 e d1."""
    n, d = 0, d0 + timedelta(days=1)
    while d < d1:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def _long_gaps(dates: List[date], values: List[Optional[float]]) -> int:
    """Rendimenti tolti per vuoto di calendario > MAX_SKIPPED_WEEKDAYS."""
    return sum(1 for i in range(1, len(values))
               if values[i - 1] is not None and values[i] is not None
               and _weekdays_between(dates[i - 1], dates[i]) > MAX_SKIPPED_WEEKDAYS)


def _adjacent_returns(dates: List[date], values: List[Optional[float]], kind: str
                      ) -> Tuple[List[date], List[float]]:
    """Rendimenti fra osservazioni valide ADIACENTI (un buco o un vuoto di
    calendario lungo li toglie)."""
    rd, rv = [], []
    for i in range(1, len(values)):
        a, b = values[i - 1], values[i]
        if a is None or b is None:
            continue
        if _weekdays_between(dates[i - 1], dates[i]) > MAX_SKIPPED_WEEKDAYS:
            continue
        rd.append(dates[i])
        rv.append(math.log(b / a) if kind == "log" else b / a - 1.0)
    return rd, rv


def _coverage(first: Optional[date], end: date, n_returns: int, window_start: date,
              per_year: int = MIN_OBS_PER_YEAR) -> Tuple[bool, float]:
    """Copertura >= MIN_YEARS (anni di calendario + densita'). Restituisce
    (sufficiente, anni osservati)."""
    if first is None:
        return False, 0.0
    years = (end - first).days / 365.25
    need_start = _years_back(end, MIN_YEARS) + timedelta(days=START_SLACK_DAYS)
    ok = first <= need_start and n_returns >= per_year * MIN_YEARS
    return ok, round(years, 2)


def _insufficient(g: _Gaps, what_it: str, what_en: str, n: int, years: float,
                  start: Optional[date], end: Optional[date]) -> Dict[str, Any]:
    reason = g.add("{w}: storia insufficiente ({y} anni, {n} osservazioni; servono almeno {m} anni)",
                   "{w}: insufficient history ({y} years, {n} observations; at least {m} years required)",
                   w=g.t(what_it, what_en), y=years, n=n, m=MIN_YEARS)
    return {"status": "insufficient", "n_obs": n, "years_observed": years,
            "start": _iso(start) if start else None, "end": _iso(end) if end else None,
            "min_years": MIN_YEARS, "reason": reason}


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _r(x: Optional[float], nd: int) -> Optional[float]:
    if x is None or not math.isfinite(x):
        return None
    return round(float(x), nd)


# ---------------------------------------------------------------- blocchi

def _moments(x: Sequence[float]) -> Dict[str, Any]:
    a = np.asarray(x, dtype=float)
    n = len(a)
    std = float(np.std(a, ddof=1))
    jb = _st.jarque_bera(a)
    q = np.quantile(a, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    mean = float(np.mean(a))
    beyond = int(np.sum(np.abs(a - mean) > 3 * std)) if std > 0 else 0
    return {
        "n_obs": n,
        "mean_pct": _r(mean * 100, 4),
        "std_pct": _r(std * 100, 4),
        "skew": _r(float(_st.skew(a)), 4),
        "excess_kurtosis": _r(float(_st.kurtosis(a)), 4),
        "q01_pct": _r(q[0] * 100, 4), "q05_pct": _r(q[1] * 100, 4),
        "q25_pct": _r(q[2] * 100, 4), "q50_pct": _r(q[3] * 100, 4),
        "q75_pct": _r(q[4] * 100, 4), "q95_pct": _r(q[5] * 100, 4),
        "q99_pct": _r(q[6] * 100, 4),
        "min_pct": _r(float(a.min()) * 100, 4), "max_pct": _r(float(a.max()) * 100, 4),
        "jb_stat": _r(float(jb.statistic), 4), "jb_p": _r(float(jb.pvalue), 6),
        "beyond_3sd": beyond,
        "beyond_3sd_normal_expected": _r(2 * _st.norm.sf(3) * n, 2),
    }


def _week_ord(d: date) -> int:
    return (d - timedelta(days=d.weekday())).toordinal() // 7


def _month_ord(d: date) -> int:
    return d.year * 12 + d.month


def _period_log_returns(dates: List[date], values: List[Optional[float]], key) -> List[float]:
    """Log-rendimenti fra ultime chiusure valide di periodi CONSECUTIVI (key = ordinale
    del periodo: un periodo senza chiusure e' un buco, nessun rendimento a cavallo)."""
    last: Dict[Any, float] = {}
    order: List[Any] = []
    for d, v in zip(dates, values):
        if v is None:
            continue
        k = key(d)
        if k not in last:
            order.append(k)
        last[k] = v
    out = []
    for i in range(1, len(order)):
        if order[i] - order[i - 1] != 1:
            continue
        out.append(math.log(last[order[i]] / last[order[i - 1]]))
    return out


def _qq(x: Sequence[float]) -> Dict[str, Any]:
    (osm, osr), (slope, intercept, r) = _st.probplot(np.asarray(x, dtype=float), dist="norm")
    n = len(osm)
    if n > QQ_MAX_POINTS:
        idx = np.unique(np.linspace(0, n - 1, QQ_MAX_POINTS).round().astype(int))
    else:
        idx = np.arange(n)
    return {"n_obs": n, "n_points": int(len(idx)), "sampled": bool(n > QQ_MAX_POINTS),
            "theoretical_z": [_r(float(osm[i]), 4) for i in idx],
            "sample_pct": [_r(float(osr[i]) * 100, 4) for i in idx],
            "slope_pct": _r(slope * 100, 4), "intercept_pct": _r(intercept * 100, 4),
            "r": _r(r, 4)}


def _histogram(x: Sequence[float]) -> Dict[str, Any]:
    a = np.asarray(x, dtype=float)
    counts, edges = np.histogram(a, bins=HIST_BINS)
    return {"bins": HIST_BINS, "edges_pct": [_r(e * 100, 4) for e in edges],
            "counts": [int(c) for c in counts],
            "normal_mean_pct": _r(float(a.mean()) * 100, 4),
            "normal_std_pct": _r(float(a.std(ddof=1)) * 100, 4), "n_obs": len(a)}


def _ljung_box(x: Sequence[float]) -> List[Dict[str, Any]]:
    from statsmodels.stats.diagnostic import acorr_ljungbox
    df = acorr_ljungbox(np.asarray(x, dtype=float), lags=list(LJUNG_BOX_LAGS))
    out = []
    for lag in LJUNG_BOX_LAGS:
        row = df.loc[lag]                       # riga del lag GIUSTO (errore 4 del notebook)
        out.append({"lag": lag, "stat": _r(float(row["lb_stat"]), 4),
                    "p": _r(float(row["lb_pvalue"]), 6)})
    return out


def _acf_block(x: Sequence[float]) -> Dict[str, Any]:
    from statsmodels.tsa.stattools import acf
    a = np.asarray(x, dtype=float)
    vals = acf(a, nlags=ACF_NLAGS, fft=True)
    band = 1.96 / math.sqrt(len(a))
    lags = list(range(1, ACF_NLAGS + 1))
    v = [_r(float(vals[k]), 4) for k in lags]
    return {"lags": lags, "values": v, "band": _r(band, 4),
            "n_outside_band": int(sum(1 for k in lags if abs(vals[k]) > band)),
            "ljung_box": _ljung_box(a)}


def _cone(rets_simple: Sequence[float], language: str) -> Dict[str, Any]:
    # indice dai soli rendimenti validi: build_cone rifa' esattamente quei rendimenti
    idx = [1.0]
    for r in rets_simple:
        idx.append(idx[-1] * (1.0 + r))
    cone = build_cone(idx, CONE_WINDOWS, language=language)
    if "error" in cone:
        return _unavailable(str(cone["error"]))
    wins = []
    for w in cone["windows"]:
        if "error" in w:
            wins.append({"window": w["window"], "n_obs": w["n_obs"], "status": "insufficient",
                         "reason": str(w["error"])})
            continue
        wins.append({"window": w["window"], "n_obs": w["n_obs"], "young": w["young"],
                     "status": "ok",
                     **{k + "_pct": _r(w[k] * 100, 2)
                        for k in ("current", "min", "p25", "p50", "p75", "max")}})
    return {"status": "ok", "windows": wins, "n_returns": cone["n_returns"],
            "basis": "rendimenti semplici, std ddof=1 rolling, x sqrt(252) (vol_cone.build_cone)"}


def _drawdown(dates: List[date], values: List[Optional[float]]) -> Dict[str, Any]:
    pd_, pv = [], []
    for d, v in zip(dates, values):
        if v is not None:
            pd_.append(_iso(d))
            pv.append(v)
    ep = _drawdown_episodes(pd_, pv)
    curve, peak = [], pv[0]
    for v in pv:
        peak = max(peak, v)
        curve.append(_r((v / peak - 1.0) * 100, 2))
    return {"max_pct": min(curve) if curve else None, "top": ep["top"],
            "current": ep["current"], "n_episodes_total": ep["n_episodes_total"],
            "curve": {"dates": pd_, "dd_pct": curve},
            "durations_unit": "osservazioni di borsa"}


def _common(a: Tuple[List[date], List[Optional[float]]],
            b: Tuple[List[date], List[Optional[float]]], kind: str
            ) -> Dict[str, Any]:
    """Intersezione delle date VALIDE, poi rendimenti fra date comuni consecutive,
    scartando gli intervalli con un buco (None) in una delle due serie o con piu'
    di MAX_SKIPPED_WEEKDAYS feriali saltati. 'contig'[i] = il rendimento i-1 finisce
    dove comincia il rendimento i (serve al lag)."""
    va = {d: v for d, v in zip(*a) if v is not None}
    vb = {d: v for d, v in zip(*b) if v is not None}
    holes = sorted({d for d, v in zip(*a) if v is None} | {d for d, v in zip(*b) if v is None})
    common = sorted(set(va) & set(vb))
    ra, rb, rd, contig = [], [], [], []
    excluded = 0
    import bisect
    for i in range(1, len(common)):
        d0, d1 = common[i - 1], common[i]
        k = bisect.bisect_right(holes, d0)
        if (k < len(holes) and holes[k] < d1) or _weekdays_between(d0, d1) > MAX_SKIPPED_WEEKDAYS:
            excluded += 1
            continue
        contig.append(bool(rd) and rd[-1] == d0)
        if kind == "log":
            ra.append(math.log(va[d1] / va[d0]))
            rb.append(vb[d1] - vb[d0])          # VIX: variazione di LIVELLO (punti)
        else:
            ra.append(va[d1] / va[d0] - 1.0)
            rb.append(vb[d1] / vb[d0] - 1.0)
        rd.append(d1)
    return {"n_a": len(va), "n_b": len(vb), "n_common_dates": len(common),
            "first": common[0] if common else None, "dates": rd, "a": ra, "b": rb,
            "contig": contig, "n_excluded_gaps": excluded, "va": va, "vb": vb, "common": common}


def _weekly_pairs(c: Dict[str, Any]) -> Dict[str, Any]:
    """Rendimenti settimanali semplici sulle date COMUNI: ultima data comune di ogni
    settimana ISO; solo fra settimane consecutive (le altre sono buchi contati)."""
    last: Dict[int, date] = {}
    for d in c["common"]:
        last[_week_ord(d)] = d
    weeks = sorted(last)
    ra, rb, rd = [], [], []
    missing = 0
    for i in range(1, len(weeks)):
        w0, w1 = weeks[i - 1], weeks[i]
        if w1 - w0 != 1:
            missing += w1 - w0 - 1
            continue
        d0, d1 = last[w0], last[w1]
        ra.append(c["va"][d1] / c["va"][d0] - 1.0)
        rb.append(c["vb"][d1] / c["vb"][d0] - 1.0)
        rd.append(d1)
    return {"dates": rd, "a": ra, "b": rb, "n_weeks": len(weeks), "n_missing_weeks": missing,
            "first": last[weeks[0]] if weeks else None}


def _beta(cand, bench, end: date, wstart: date, g: _Gaps, asynchronous: bool
          ) -> Tuple[Dict, Dict]:
    """Beta contro il benchmark. Principale: SETTIMANALE (PM 04/10); giornaliero
    solo come confronto. Beta mobile su BETA_WINDOW settimane."""
    c = _common(cand, bench, "simple")
    w = _weekly_pairs(c)
    n_d, n_w = len(c["a"]), len(w["a"])
    align = {"n_candidate_prices": c["n_a"], "n_benchmark_prices": c["n_b"],
             "n_common_dates": c["n_common_dates"], "n_common_daily_returns": n_d,
             "n_daily_excluded_gaps": c["n_excluded_gaps"],
             "n_weeks": w["n_weeks"], "n_weekly_returns": n_w, "n_missing_weeks": w["n_missing_weeks"],
             "method": "intersezione delle date valide; settimanale = ultima data comune della "
                       "settimana, solo settimane consecutive"}
    if c["n_a"] != c["n_common_dates"] or c["n_b"] != c["n_common_dates"]:
        g.add("Calendari diversi fra candidato e benchmark: {a} e {b} chiusure, {c} date comuni usate",
              "Different calendars for candidate and benchmark: {a} and {b} closes, {c} common dates used",
              a=c["n_a"], b=c["n_b"], c=c["n_common_dates"])
    if c["n_excluded_gaps"]:
        g.add("Beta: {n} rendimenti giornalieri a cavallo di buchi esclusi",
              "Beta: {n} daily returns across gaps excluded", n=c["n_excluded_gaps"])
    if w["n_missing_weeks"]:
        g.add("Beta: {n} settimane senza data comune (buchi, nessun rendimento a cavallo)",
              "Beta: {n} weeks without a common date (gaps, no return across them)",
              n=w["n_missing_weeks"])
    ok, yrs = _coverage(w["first"], end, n_w, wstart, MIN_WEEKLY_PER_YEAR)
    if not ok:
        ins = _insufficient(g, "Beta contro il benchmark", "Beta versus the benchmark", n_w, yrs,
                            w["first"], end)
        ins["alignment"] = align
        return ins, dict(ins)
    why = g.t("settimanale: neutralizza le chiusure asincrone fra listini (decisione PM)",
              "weekly: neutralises asynchronous closes across exchanges (PM decision)")
    wp = np.column_stack([w["a"], w["b"]])
    full = paired_beta_statistics(wp)
    start, last = _iso(w["dates"][0]), _iso(w["dates"][-1])
    daily = None
    if n_d >= 20:
        dfull = paired_beta_statistics(np.column_stack([c["a"], c["b"]]))
        daily = {"beta": _r(dfull["beta"], 4), "correlation": _r(dfull["correlation"], 4),
                 "n_obs": dfull["n_obs"], "asynchronous_closes": asynchronous,
                 "label": (g.t("giornaliero: distorto dalle chiusure asincrone Europa/USA",
                               "daily: biased by asynchronous Europe/US closes") if asynchronous
                           else g.t("giornaliero: solo confronto", "daily: comparison only"))}
    full_b = {"status": "ok", "frequency": "weekly", "frequency_reason": why,
              "window_unit": "settimane", "week_rule": WEEK_RULE,
              "beta": _r(full["beta"], 4), "correlation": _r(full["correlation"], 4),
              "n_obs": full["n_obs"], "start": start, "end": last, "alignment": align,
              "returns": "semplici, settimanali", "daily_comparison": daily}
    rd, rb = [], []
    for j in range(BETA_WINDOW - 1, n_w):
        st_ = paired_beta_statistics(wp[j - BETA_WINDOW + 1:j + 1])
        rd.append(_iso(w["dates"][j]))
        rb.append(_r(st_["beta"], 4))
    vals = [v for v in rb if v is not None]
    last_date = rd[-1] if rd else None
    stale = bool(last_date) and (end - date.fromisoformat(last_date)).days > ROLLING_STALE_DAYS
    if stale:
        g.add("Ultimo beta mobile al {d}, piu' vecchio della fine dei dati ({e})",
              "Latest rolling beta as of {d}, older than the end of the data ({e})",
              d=date.fromisoformat(last_date).strftime("%d/%m/%Y"), e=end.strftime("%d/%m/%Y"))
    roll = {"status": "ok", "frequency": "weekly", "frequency_reason": why, "week_rule": WEEK_RULE,
            "window": BETA_WINDOW, "window_unit": "settimane", "dates": rd, "beta": rb,
            "last": rb[-1] if rb else None, "last_date": last_date, "last_stale": stale,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None, "n_obs": n_w, "n_windows": len(rb),
            "start": start, "end": last, "alignment": align, "returns": "semplici, settimanali"}
    return full_b, roll


def _ols(x: Sequence[float], y: Sequence[float]) -> Dict[str, Any]:
    lr = _st.linregress(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    return {"slope": _r(lr.slope, 6), "intercept": _r(lr.intercept, 6),
            "corr": _r(lr.rvalue, 4), "r2": _r(lr.rvalue ** 2, 4),
            "p_value": _r(lr.pvalue, 6), "slope_stderr": _r(lr.stderr, 6), "n_obs": len(x)}


def _vix(cand, vix, end: date, wstart: date, g: _Gaps) -> Dict[str, Any]:
    c = _common(cand, vix, "log")
    n = len(c["a"])
    ok, yrs = _coverage(c["first"], end, n, wstart)
    align = {"n_candidate_prices": c["n_a"], "n_vix_levels": c["n_b"],
             "n_common_dates": c["n_common_dates"], "n_common_returns": n,
             "n_excluded_gaps": c["n_excluded_gaps"]}
    if c["n_excluded_gaps"]:
        g.add("VIX: {n} rendimenti a cavallo di buchi esclusi",
              "VIX: {n} returns across gaps excluded", n=c["n_excluded_gaps"])
    if not ok:
        ins = _insufficient(g, "Sensibilita' al VIX", "Sensitivity to the VIX", n, yrs, c["first"], end)
        ins["alignment"] = align
        return ins
    y = [v * 100 for v in c["a"]]                # log-rendimento in punti %
    x = c["b"]                                   # variazione VIX in punti
    lag0 = _ols(x, y)
    # VIX di t contro candidato di t+1 (asincronia EU), solo su rendimenti contigui
    idx1 = [i for i in range(1, n) if c["contig"][i]]
    lag1 = _ols([x[i - 1] for i in idx1], [y[i] for i in idx1])
    return {"status": "ok", "method": "OLS: log-rendimento del candidato (%) su variazione del VIX "
                                      "(punti), date comuni consecutive",
            "lag0": lag0, "lag1": lag1, "n_obs": n,
            "start": _iso(c["dates"][0]), "end": _iso(c["dates"][-1]), "alignment": align,
            "scatter": {"dvix": [_r(v, 4) for v in x], "ret_pct": [_r(v, 4) for v in y]}}


def _series_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: entry.get(k) for k in ("ticker", "currency", "price_field", "source", "status",
                                      "reason", "native_currency", "conversion", "index_type",
                                      "proxy")}


def _label(role: str, ticker: Any) -> str:
    """'Ruolo (TICKER)', senza ripetere il ticker se il ruolo lo contiene gia' (peer)."""
    if not ticker or str(ticker) in role:
        return role
    return f"{role} ({ticker})"


def _check_currency(entry: Dict[str, Any], label: str, base: str, g: _Gaps) -> None:
    """Serie di prezzo: deve essere in valuta base con conversione dichiarata coerente."""
    ccy = entry.get("currency")
    conv = entry.get("conversion")
    method = conv.get("method") if isinstance(conv, dict) else None
    if ccy != base:
        g.add("{r}: serie in {c}, non convertita in {b}: rendimenti nella valuta del listino",
              "{r}: series in {c}, not converted to {b}: returns in the listing currency",
              r=_label(label, entry.get("ticker")), c=ccy or "n.d.", b=base)
    elif method not in ("native", "fx_same_date"):
        g.add("{r}: conversione in {b} non dichiarata",
              "{r}: conversion to {b} not stated",
              r=_label(label, entry.get("ticker")), b=base)
    elif method == "fx_same_date" and (conv.get("n_dropped_no_fx") or 0) > 0:
        g.add("{r}: {n} date tolte per cambio mancante",
              "{r}: {n} dates dropped for missing exchange rate",
              r=_label(label, entry.get("ticker")), n=conv.get("n_dropped_no_fx"))


def _usable(entry: Any, role_it: str, role_en: str, g: _Gaps) -> Optional[str]:
    """None se la serie e' usabile, altrimenti la reason dichiarata."""
    if not isinstance(entry, dict):
        return g.add("{r}: serie assente dal pacchetto", "{r}: series missing from the pack",
                     r=g.t(role_it, role_en))
    st = entry.get("status")
    if st not in SERIES_STATUSES:
        return g.add("{r}: stato della serie non riconosciuto", "{r}: unrecognised series status",
                     r=g.t(role_it, role_en))
    if st in ("error", "missing"):
        return g.add("{r}: serie non disponibile: {why}", "{r}: series unavailable: {why}",
                     r=_label(g.t(role_it, role_en), entry.get("ticker")),
                     why=entry.get("reason") or g.t("motivo non dichiarato", "reason not stated"))
    if entry.get("proxy"):
        g.add("{r}: PROXY: {why}", "{r}: PROXY: {why}",
              r=_label(g.t(role_it, role_en), entry.get("ticker")), why=entry.get("proxy"))
    if st == "stale":
        g.add("{r}: dati non aggiornati: {why}", "{r}: stale data: {why}",
              r=_label(g.t(role_it, role_en), entry.get("ticker")),
              why=entry.get("reason") or g.t("motivo non dichiarato", "reason not stated"))
    return None


def _prep(entry, role_it, role_en, as_of, wstart_fn, g, positive=True):
    """Serie pulita e tagliata alla finestra, o reason."""
    why = _usable(entry, role_it, role_en, g)
    if why:
        return None, why
    try:
        d, v, invalid = _clean(entry, as_of, positive)
    except (ValueError, TypeError):
        return None, g.add("{r}: serie malformata nel pacchetto", "{r}: malformed series in the pack",
                           r=g.t(role_it, role_en))
    if entry.get("price_field") != "level":
        _check_currency(entry, g.t(role_it, role_en), g.base, g)
    if invalid:
        g.add("{r}: {n} valori non validi trattati come buchi", "{r}: {n} invalid values treated as gaps",
              r=g.t(role_it, role_en), n=invalid)
    return (d, v), None


def _peer(entry, as_of, end, wstart, cand, g) -> Tuple[Dict[str, Any], Optional[tuple]]:
    out = {**_series_summary(entry if isinstance(entry, dict) else {})}
    tk = (entry or {}).get("ticker") if isinstance(entry, dict) else None
    s, why = _prep(entry, "Peer " + str(tk), "Peer " + str(tk), as_of, None, g)
    if s is None:
        out.update(_unavailable(why))
        return out, None
    d, v = _window_cut(s[0], s[1], wstart)
    rd, rs = _adjacent_returns(d, v, "simple")
    first = next((x for x, y in zip(d, v) if y is not None), None)
    ok, yrs = _coverage(first, end, len(rs), wstart)
    if not ok:
        out.update(_insufficient(g, "Peer " + str(tk), "Peer " + str(tk), len(rs), yrs, first,
                                 d[-1] if d else None))
        return out, None
    valid = [x for x in v if x is not None]
    lr = [math.log(1 + x) for x in rs]
    dd = _drawdown(d, v)
    c = _common(cand, (d, v), "simple")
    corr = None
    if len(c["a"]) >= 20:
        corr = _r(paired_beta_statistics(np.column_stack([c["a"], c["b"]]))["correlation"], 4)
    out.update({"status": "ok", "n_obs": len(rs), "start": _iso(rd[0]), "end": _iso(rd[-1]),
                "total_return_pct": _r((valid[-1] / valid[0] - 1) * 100, 2),
                "vol_annual_pct": _r(float(np.std(rs, ddof=1)) * math.sqrt(TRADING_DAYS) * 100, 2),
                "excess_kurtosis_log": _r(float(_st.kurtosis(lr)), 4),
                "max_drawdown_pct": dd["max_pct"],
                "corr_with_candidate": corr, "n_common_returns_with_candidate": len(c["a"]),
                "returns": "semplici, nella valuta della serie"})
    return out, (d, v)


def _index_line(role: str, entry: Dict[str, Any], dv: tuple, cand_first: date,
                cand_valid: Dict[date, float], end: date, g: _Gaps) -> Dict[str, Any]:
    """Linea base 100 alla prima data valida >= inizio candidato comune col candidato."""
    line = {"role": role, "ticker": entry.get("ticker"), "currency": entry.get("currency"),
            "index_type": entry.get("index_type")}
    pts = [(d, v) for d, v in zip(*dv) if v is not None and d >= cand_first]
    base_i = next((i for i, (d, _) in enumerate(pts) if d in cand_valid), None)
    if base_i is None:
        line.update(_unavailable(g.add("{t}: nessuna data in comune col candidato",
                                       "{t}: no date in common with the candidate",
                                       t=entry.get("ticker"))))
        return line
    pts = pts[base_i:]
    ok, yrs = _coverage(pts[0][0], end, len(pts) - 1, cand_first)
    if not ok:
        line.update(_insufficient(g, str(entry.get("ticker")), str(entry.get("ticker")),
                                  len(pts) - 1, yrs, pts[0][0], pts[-1][0]))
        return line
    p0 = pts[0][1]
    idx = [100.0 * v / p0 for _, v in pts]
    line.update({"status": "ok", "base_date": _iso(pts[0][0]), "dates": [_iso(d) for d, _ in pts],
                 "index": [_r(x, 4) for x in idx], "total_return_pct": _r(idx[-1] - 100.0, 2),
                 "n_obs": len(pts), "start": _iso(pts[0][0]), "end": _iso(pts[-1][0])})
    return line


_MULTIPLE_KEYS = ("ev_sales", "ev_ebitda", "pe_trailing", "pe_forward")


def _multiples(m: Any, g: _Gaps) -> Dict[str, Any]:
    """Multipli dal pacchetto (L1): righe riportate, mediana dei peer validi (>0)
    con n e scarti dichiarati; storico riportato tal quale."""
    if not isinstance(m, dict) or not isinstance(m.get("current"), list):
        return _unavailable(g.add("Il pacchetto non contiene multipli", "The pack contains no multiples"))
    rows, med = [], {}
    for r in m["current"]:
        if not isinstance(r, dict):
            continue
        rows.append({k: r.get(k) for k in ("ticker", "role", *_MULTIPLE_KEYS, "status", "reason",
                                           "source", "as_of")})
    cand = [r for r in rows if r.get("role") == "candidate"]
    peers_ok = [r for r in rows if r.get("role") == "peer" and r.get("status") in (None, "ok")]
    for k in _MULTIPLE_KEYS:
        vals = [r[k] for r in peers_ok if _finite(r.get(k)) and r[k] > 0]
        excl = sum(1 for r in peers_ok if r.get(k) is not None) - len(vals)
        med[k] = {"value": _r(float(np.median(vals)), 2) if vals else None, "n": len(vals),
                  "n_excluded_non_positive_or_invalid": excl}
    status = "ok" if cand and cand[0].get("status") in (None, "ok") and peers_ok else "partial"
    out = {"status": status, "rows": rows, "peer_median": med, "n_peers_ok": len(peers_ok),
           "history": m.get("history"), "median_rule": "mediana dei peer con valore > 0"}
    hist = m.get("history") if isinstance(m.get("history"), dict) else {}
    # approssimazioni dichiarate da L1 (livello history) + "missing" per esercizio:
    # esposte qui perche' arrivino al memo (contratto con L3)
    out["history_approximations"] = list(hist.get("approximations") or [])
    out["history_status"] = hist.get("status") if hist else "missing"
    out["history_reason"] = hist.get("reason") if hist else g.add(
        "Multipli storici assenti dal pacchetto", "Historical multiples missing from the pack")
    if status != "ok":
        out["reason"] = g.add("Multipli incompleti: candidato o peer senza valori",
                              "Incomplete multiples: candidate or peers without values")
    return out


# ---------------------------------------------------------------- pubblica

def market_stats(pack: Dict[str, Any], *, window_years: int = DEFAULT_WINDOW_YEARS,
                 language: str = "it") -> Dict[str, Any]:
    """Statistiche di mercato del candidato dal pacchetto (schema in testa).
    Pura: nessuna rete, nessun I/O. Non solleva su pacchetti rotti (li dichiara);
    solleva ValueError solo su parametri del chiamante (window_years < MIN_YEARS,
    lingua non supportata)."""
    language = validate_language(language)
    if not isinstance(window_years, int) or isinstance(window_years, bool) or window_years < MIN_YEARS:
        raise ValueError(f"window_years must be an int >= {MIN_YEARS}")
    g = _Gaps(language)
    out: Dict[str, Any] = {"version": STATS_VERSION, "pack_version": None,
                           "window_years_requested": window_years, "min_years": MIN_YEARS,
                           "language": language}
    blocks = ("moments", "qq", "histogram", "vol_cone", "drawdown", "acf_abs", "acf_returns",
              "beta", "rolling_beta", "vix_sensitivity", "price_history", "multiples")

    def _all_unavailable(reason: str) -> Dict[str, Any]:
        for b in blocks:
            out[b] = _unavailable(reason)
        out["peers"] = []
        out["beta_daily"] = _unavailable(reason)
        out["status"] = "unavailable"
        out["gaps"] = g.items
        return out

    if not isinstance(pack, dict):
        return _all_unavailable(g.add("Pacchetto di mercato assente", "Market data pack missing"))
    out["pack_version"] = pack.get("version")
    if pack.get("version") != PACK_VERSION:
        return _all_unavailable(g.add("Pacchetto di mercato con versione non supportata ({v})",
                                      "Market data pack with unsupported version ({v})",
                                      v=pack.get("version")))
    if pack.get("status") == "unavailable":
        return _all_unavailable(g.add("Dati di mercato non disponibili: {why}",
                                      "Market data unavailable: {why}",
                                      why=pack.get("reason") or g.t("motivo non dichiarato",
                                                                    "reason not stated")))
    if pack.get("status") == "partial":
        g.add("Pacchetto di mercato parziale: {why}", "Partial market data pack: {why}",
              why=pack.get("reason") or g.t("motivo non dichiarato", "reason not stated"))
    base = pack.get("base_currency") or BASE_CURRENCY
    if base != BASE_CURRENCY:
        g.add("Valuta base del pacchetto {c} diversa da {b}", "Pack base currency {c} differs from {b}",
              c=base, b=BASE_CURRENCY)
    g.base = base
    out["base_currency"] = base
    ps = pack.get("peer_selection")
    if not isinstance(ps, dict):
        ps = (pack.get("multiples") or {}).get("peer_selection") if isinstance(pack.get("multiples"), dict) else None
    out["peer_selection"] = ps if isinstance(ps, dict) else None
    as_of = None
    if pack.get("as_of"):
        try:
            as_of = date.fromisoformat(str(pack["as_of"])[:10])
        except ValueError:
            return _all_unavailable(g.add("Data di riferimento del pacchetto non leggibile",
                                          "Pack reference date unreadable"))
    series = pack.get("series") if isinstance(pack.get("series"), dict) else {}
    out["series"] = {r: _series_summary(series[r]) for r in ROLES if isinstance(series.get(r), dict)}

    cand, why = _prep(series.get("candidate"), "Candidato", "Candidate", as_of, None, g)
    if cand is None:
        return _all_unavailable(why)
    valid_dates = [d for d, v in zip(*cand) if v is not None]
    if not valid_dates:
        return _all_unavailable(g.add("Candidato: nessun prezzo valido", "Candidate: no valid price"))
    end = valid_dates[-1]
    wstart = _years_back(end, window_years)
    cd, cv = _window_cut(cand[0], cand[1], wstart)
    cand_w = (cd, cv)
    first = next(d for d, v in zip(cd, cv) if v is not None)
    rd_log, r_log = _adjacent_returns(cd, cv, "log")
    rd_s, r_s = _adjacent_returns(cd, cv, "simple")
    holes = sum(1 for v in cv if v is None)
    if holes:
        g.add("Candidato: {n} chiusure mancanti nel calendario del listino (rendimenti a cavallo esclusi)",
              "Candidate: {n} closes missing from the exchange calendar (returns across them excluded)",
              n=holes)
    lg = _long_gaps(cd, cv)
    if lg:
        g.add("Candidato: {n} vuoti di calendario oltre {m} sedute (rendimenti a cavallo esclusi)",
              "Candidate: {n} calendar gaps beyond {m} sessions (returns across them excluded)",
              n=lg, m=MAX_SKIPPED_WEEKDAYS)
    ok, yrs = _coverage(first, end, len(r_log), wstart)
    out["window"] = {"start": _iso(first), "end": _iso(end), "requested_start": _iso(wstart),
                     "years_observed": yrs, "n_prices": sum(1 for v in cv if v is not None),
                     "n_returns": len(r_log), "n_missing_closes": holes}
    if yrs < window_years - (START_SLACK_DAYS / 365.25) and ok:
        g.add("Finestra richiesta {w} anni, storia disponibile {y} anni: statistiche sulla storia disponibile",
              "Requested window {w} years, available history {y} years: statistics on the available history",
              w=window_years, y=yrs)
    out["returns_basis"] = {"stylized_facts": "log", "vol_cone": "semplici", "beta": "semplici",
                            "drawdown": "prezzi", "std_ddof": 1,
                            "kurtosis": "eccesso (Fisher, scipy default)"}
    win = {"start": _iso(rd_log[0]) if rd_log else None, "end": _iso(rd_log[-1]) if rd_log else None}

    if not ok:
        ins = _insufficient(g, "Candidato", "Candidate", len(r_log), yrs, first, end)
        for b in ("moments", "qq", "histogram", "vol_cone", "drawdown", "acf_abs", "acf_returns"):
            out[b] = dict(ins)
    else:
        mom = {"daily": _moments(r_log)}
        wk = _period_log_returns(cd, cv, _week_ord)
        mo = _period_log_returns(cd, cv, _month_ord)
        mom["weekly"] = _moments(wk)
        mom["monthly"] = _moments(mo)
        out["moments"] = {"status": "ok", **win, "n_obs": len(r_log), "returns": "log", **mom}
        out["qq"] = {"status": "ok", **win, **_qq(r_log), "returns": "log, giornalieri"}
        out["histogram"] = {"status": "ok", **win, **_histogram(r_log), "returns": "log, giornalieri"}
        cone = _cone(r_s, language)
        out["vol_cone"] = {**cone, **({"start": _iso(rd_s[0]), "end": _iso(rd_s[-1]), "n_obs": len(r_s)}
                                      if cone.get("status") == "ok" else {})}
        out["drawdown"] = {"status": "ok", "start": _iso(first), "end": _iso(end),
                           "n_obs": out["window"]["n_prices"], **_drawdown(cd, cv)}
        out["acf_abs"] = {"status": "ok", **win, "n_obs": len(r_log), "series": "|r| log",
                          **_acf_block([abs(x) for x in r_log])}
        out["acf_returns"] = {"status": "ok", **win, "n_obs": len(r_log), "series": "r log",
                              **_acf_block(r_log)}

    bench, why_b = (_prep(series.get("benchmark"), "Benchmark", "Benchmark", as_of, None, g)
                    if "benchmark" in series else
                    (None, g.add("Benchmark assente dal pacchetto", "Benchmark missing from the pack")))
    if bench is None:
        out["beta"] = _unavailable(why_b)
        out["rolling_beta"] = _unavailable(why_b)
        out["beta_daily"] = _unavailable(why_b)
    else:
        bw = _window_cut(bench[0], bench[1], wstart)
        nat_c = (series.get("candidate") or {}).get("native_currency")
        nat_b = (series.get("benchmark") or {}).get("native_currency")
        asynchronous = bool(nat_c and nat_b and nat_c != nat_b)   # euristica dichiarata: valuta del listino
        b_full, b_roll = _beta(cand_w, bw, end, wstart, g, asynchronous)
        ccy_c = (series.get("candidate") or {}).get("currency")
        ccy_b = (series.get("benchmark") or {}).get("currency")
        if ccy_c and ccy_b and ccy_c != ccy_b:
            g.add("Beta calcolato su rendimenti in valute diverse ({a} e {b})",
                  "Beta computed on returns in different currencies ({a} and {b})",
                  a=ccy_c, b=ccy_b)
        b_full["benchmark"] = b_roll["benchmark"] = (series.get("benchmark") or {}).get("ticker")
        out["beta"], out["rolling_beta"] = b_full, b_roll
        dc = b_full.get("daily_comparison")
        out["beta_daily"] = ({"status": "ok", "frequency": "daily", "window_unit": "sedute",
                              "start": b_full["start"], "end": b_full["end"],
                              "benchmark": b_full["benchmark"], **dc}
                             if dc else _unavailable(b_full.get("reason") or g.add(
                                 "Beta giornaliero di confronto non calcolabile",
                                 "Daily comparison beta not computable")))

    vix, why_v = (_prep(series.get("vix"), "VIX", "VIX", as_of, None, g)
                  if "vix" in series else
                  (None, g.add("VIX assente dal pacchetto", "VIX missing from the pack")))
    if vix is None:
        out["vix_sensitivity"] = _unavailable(why_v)
    else:
        vw = _window_cut(vix[0], vix[1], wstart)
        out["vix_sensitivity"] = _vix(cand_w, vw, end, wstart, g)
        out["vix_sensitivity"]["vix"] = (series.get("vix") or {}).get("ticker")

    peers = pack.get("peers") if isinstance(pack.get("peers"), list) else []
    if not peers:
        g.add("Nessun peer nel pacchetto", "No peers in the pack")
    peer_res = [_peer(p, as_of, end, wstart, cand_w, g) for p in peers]
    out["peers"] = [r for r, _ in peer_res]

    # price_history: base 100, candidato sulla sua finestra, gli altri ribasati
    # alla prima data valida comune col candidato (dichiarata per linea)
    if not ok:
        out["price_history"] = dict(_insufficient(g, "Candidato", "Candidate", len(r_log), yrs, first, end))
    else:
        cvalid = {d: v for d, v in zip(cd, cv) if v is not None}
        lines = [_index_line("candidate", series["candidate"], cand_w, first, cvalid, end, g)]
        if bench is not None:
            lines.append(_index_line("benchmark", series["benchmark"], bw, first, cvalid, end, g))
        for (res, dv), pe in zip(peer_res, peers):
            if dv is None:
                lines.append({"role": "peer", "ticker": res.get("ticker"), "currency": res.get("currency"),
                              "status": res.get("status"), "reason": res.get("reason")})
            else:
                lines.append(_index_line("peer", pe, dv, first, cvalid, end, g))
        out["price_history"] = {"status": "ok", "start": _iso(first), "end": _iso(end),
                                "n_obs": out["window"]["n_prices"], "base": 100,
                                "basis": "prezzi rettificati in valuta base, base 100 alla prima "
                                         "data valida comune col candidato", "lines": lines}
    out["multiples"] = _multiples(pack.get("multiples"), g)

    st = {out[b].get("status") for b in blocks} | {p.get("status") for p in out["peers"]}
    out["status"] = "ok" if st <= {"ok"} else ("unavailable" if st <= {"unavailable"} else "partial")
    out["gaps"] = g.items
    return out
