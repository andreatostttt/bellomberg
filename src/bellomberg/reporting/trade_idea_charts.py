"""trade_idea_charts.py - Grafici del memo Trade Idea, impianto B (approvato dal PM).

Dodici grafici PNG (matplotlib, backend Agg, DPI 220) dal dict ``facts`` di
``trade_idea_facts.extract_facts``, nell'ordine di ``KEYS``:

1. ``revenue_margin``  ricavi (barre, mln) + pannello separato margine operativo %
2. ``cash``            flusso operativo, capex, flusso libero (mln), barre raggruppate
3. ``price_targets``   prezzo contro ventaglio target e range 52 settimane
4. ``risk``            candidato contro book: vol, beta, VaR 99% 1g, max drawdown
5. ``balance``         cassa, debito a lungo termine, avviamento, patrimonio netto (mln)
6. ``margins``         margine operativo, netto e FCF in % dei ricavi
7. ``eps_path``        EPS diluito storico + stime del consensus
8. ``revenue_path``    ricavi storici + stime del consensus
9. ``capital_allocation`` flusso operativo contro capex, dividendi, buyback (mln)
10. ``recommendations`` distribuzione delle raccomandazioni (attuale e precedente)
11. ``returns``        rendimento % sulla finestra, candidato evidenziato
12. ``tail_risk``      VaR 99% 1g, VaR 99% 1 settimana, ES 99% 1 settimana del candidato

Ogni grafico prodotto restituisce ``{"key","title","subtitle","path","source","aspect","notes"}``;
``notes`` sono 2-4 frasi CALCOLATE dai numeri del grafico (mai opinioni).

Regola PM 14/07: un grafico si produce SOLO se i suoi dati ci sono tutti; altrimenti
la lista contiene ``{"key","title","missing"}`` col perche' e su disco non resta nessun
PNG di quella chiave. Niente proxy, niente valori di ripiego.
"""
import math
import os
import re
from decimal import ROUND_HALF_UP, Decimal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import ticker  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from bellomberg.core.language import validate_language  # noqa: E402

try:  # stesso caricamento font dei grafici istituzionali (Arial -> Liberation -> DejaVu)
    from bellomberg.reporting.charts_institutional import FONT
except Exception:  # pragma: no cover - modulo fratello non importabile
    FONT = "DejaVu Sans"

# Impianto M (memo istituzionale sobrio, PM 04/10): solo navy + grigi; stime tratteggiate navy;
# il titolo in esame in navy pieno, il resto in grigio. Figure larghe per una colonna di ~160 mm.
NAVY = "#1F3864"
G1 = "#4D4D4D"
G2 = "#7F7F7F"
G3 = "#A6A6A6"
G4 = "#D0D0D0"
GRID = "#EBEBEB"
INK = "#262626"
TICK = "#404040"
NOTE = "#7F7F7F"
AXIS = "#8C8C8C"
DPI = 220
WIDTH = 6.3          # 160 mm
H_MAIN = 3.0         # aspect 0,48
H_MID = 2.85         # aspect 0,45
H_SMALL = 2.6        # aspect 0,41 (small multiples)
KEYS = ("revenue_margin", "cash", "price_targets", "risk", "balance", "margins", "eps_path",
        "revenue_path", "capital_allocation", "recommendations", "returns", "tail_risk")
RECO_KEYS = ("strong_buy", "buy", "hold", "sell", "strong_sell")

_RC = {"font.family": FONT, "font.size": 8.5, "axes.edgecolor": AXIS, "axes.linewidth": .6,
       "xtick.color": TICK, "ytick.color": TICK, "axes.labelcolor": TICK, "xtick.labelsize": 8.5,
       "ytick.labelsize": 8, "axes.titlesize": 8.5, "axes.titleweight": "bold",
       "hatch.color": NAVY, "hatch.linewidth": .6}

_T = {
    "it": {
        "rev_title": "Ricavi e margine operativo{years}",
        "rev_sub": "Un'unità per pannello: ricavi in mln {unit}, margine in %",
        "margin_axis": "margine op.",
        "cash_title": "Flusso operativo, capex e flusso libero",
        "cash_sub": "Stessa unità (mln {unit}) per le tre serie",
        "cash_sub_abs": "Stessa unità (mln {unit}); capex in valore assoluto",
        "cfo": "Flusso operativo", "capex": "Capex", "fcf": "Flusso libero",
        "price_title": "Prezzo contro target degli analisti",
        "price_sub": "Dove sta il titolo nel ventaglio dei target",
        "price_sub_no52": "Dove sta il titolo nel ventaglio dei target; range 52 settimane non disponibile",
        "targets": "Target analisti", "range52": "Range 52 sett.",
        "mean": "Obiettivo medio", "median": "Mediano", "price": "Prezzo", "min": "Minimo", "max": "Massimo",
        "per_share": "{cur} per azione",
        "risk_title": "{name} contro il book: rischio",
        "risk_sub": "Ogni metrica con la sua scala",
        "vol": "Volatilità 1a", "beta": "Beta", "var": "VaR 99% 1g", "dd": "Max drawdown",
        "book": "Book", "candidate": "Candidato",
        "abs_note": "VaR e drawdown in valore assoluto",
        "bal_title": "Cassa, debito, avviamento e patrimonio",
        "bal_sub": "mln {unit} a fine esercizio, stessa unità per le quattro serie",
        "cash_bs": "Cassa", "ltd": "Debito a lungo termine", "goodwill": "Avviamento", "equity": "Patrimonio netto",
        "mar_title": "Margini: operativo, netto e FCF",
        "mar_sub": "In % dei ricavi, stessa unità per le tre linee",
        "op_m": "Margine operativo", "net_m": "Margine netto", "fcf_m": "Margine FCF",
        "eps_title": "Utile per azione: storico e stime",
        "eps_sub": "EPS diluito in {cur} per azione; stime del consensus tratteggiate",
        "estimate": "stima",
        "rp_title": "Ricavi: storico e stime",
        "rp_sub": "{scale} {cur}; stime del consensus tratteggiate",
        "ca_title": "Allocazione del capitale",
        "ca_sub": "mln {unit}; capex, dividendi e buyback in valore assoluto",
        "div": "Dividendi", "bb": "Buyback",
        "rec_title": "Raccomandazioni degli analisti",
        "rec_sub": "Numero di analisti per classe",
        "rec_sub_prev": "Numero di analisti per classe: attuale contro precedente",
        "current": "Attuale", "previous": "Precedente",
        "ret_title": "Rendimento su {window}",
        "ret_sub": "Rendimento % sulla stessa finestra; candidato in blu scuro",
        "tail_title": "{name}: rischio di coda",
        "tail_sub": "VaR 99% ed expected shortfall 99%, valori assoluti",
        "var1d": "VaR 99% 1g", "var1w": "VaR 99% 1 sett.", "es": "ES 99% 1 sett.",
        "tail_note": "Perdite in valore assoluto",
        "src_nd": "fonte n.d.",
        "src_fin": "Bilanci", "src_cons": "Consensus", "src_price": "Prezzo",
        "src_quant": "Calcoli quantitativi Bellomberg e rischio del portafoglio",
        "src_ret": "Rendimenti calcolati da Bellomberg sui prezzi giornalieri",
        "miss_history": "Storico di bilancio non disponibile.",
        "miss_revenue": "Servono i ricavi di almeno 2 esercizi; disponibili: {n}.",
        "miss_margin": "Margine operativo non calcolabile: nessun esercizio con ricavi e utile operativo insieme.",
        "miss_cash": "Servono flusso operativo, capex e flusso libero nello stesso esercizio per almeno 2 esercizi; disponibili: {n}.",
        "miss_block": "{block} non disponibili.",
        "miss_fields": "Mancano {fields}.",
        "miss_currency": "Valuta non dichiarata.",
        "miss_series_absent": "Nello storico mancano {fields}.",
        "miss_rows": "Servono {fields} nello stesso esercizio per almeno {need} esercizi; disponibili: {n}.",
        "miss_cons": "Stime del consensus non disponibili: {field}.",
        "miss_cons_q": "Stime annuali del consensus non disponibili ({field}): ci sono solo stime trimestrali, che non si mescolano con gli anni.",
        "miss_units": "Storico in {a} e consensus in {b}: unità diverse, confronto non possibile.",
        "miss_reco": "Raccomandazioni incomplete: mancano {fields}.",
        "miss_reco_zero": "Raccomandazioni tutte a zero.",
        "miss_window": "Finestra dei rendimenti non dichiarata.",
        "miss_assets": "Servono almeno 2 titoli con rendimento; disponibili: {n}.",
        "miss_cand": "Il candidato non è fra i titoli con rendimento.",
    },
    "en": {
        "rev_title": "Revenue and operating margin{years}",
        "rev_sub": "One unit per panel: revenue in {unit} mn, margin in %",
        "margin_axis": "op. margin",
        "cash_title": "Operating cash flow, capex and free cash flow",
        "cash_sub": "Same unit ({unit} mn) for all three series",
        "cash_sub_abs": "Same unit ({unit} mn); capex in absolute value",
        "cfo": "Operating cash flow", "capex": "Capex", "fcf": "Free cash flow",
        "price_title": "Price against analyst targets",
        "price_sub": "Where the stock sits within the target range",
        "price_sub_no52": "Where the stock sits within the target range; 52-week range not available",
        "targets": "Analyst targets", "range52": "52-week range",
        "mean": "Mean target", "median": "Median", "price": "Price", "min": "Low", "max": "High",
        "per_share": "{cur} per share",
        "risk_title": "{name} against the book: risk",
        "risk_sub": "Each metric on its own scale",
        "vol": "Volatility 1y", "beta": "Beta", "var": "VaR 99% 1d", "dd": "Max drawdown",
        "book": "Book", "candidate": "Candidate",
        "abs_note": "VaR and drawdown in absolute value",
        "bal_title": "Cash, debt, goodwill and equity",
        "bal_sub": "{unit} mn at fiscal year end, same unit for all four series",
        "cash_bs": "Cash", "ltd": "Long-term debt", "goodwill": "Goodwill", "equity": "Equity",
        "mar_title": "Margins: operating, net and FCF",
        "mar_sub": "As % of revenue, same unit for all three lines",
        "op_m": "Operating margin", "net_m": "Net margin", "fcf_m": "FCF margin",
        "eps_title": "Earnings per share: history and estimates",
        "eps_sub": "Diluted EPS in {cur} per share; consensus estimates hatched",
        "estimate": "estimate",
        "rp_title": "Revenue: history and estimates",
        "rp_sub": "{cur} {scale}; consensus estimates hatched",
        "ca_title": "Capital allocation",
        "ca_sub": "{unit} mn; capex, dividends and buybacks in absolute value",
        "div": "Dividends", "bb": "Buybacks",
        "rec_title": "Analyst recommendations",
        "rec_sub": "Number of analysts per rating",
        "rec_sub_prev": "Number of analysts per rating: current against previous",
        "current": "Current", "previous": "Previous",
        "ret_title": "Return over {window}",
        "ret_sub": "Return % over the same window; candidate in dark blue",
        "tail_title": "{name}: tail risk",
        "tail_sub": "99% VaR and 99% expected shortfall, absolute values",
        "var1d": "VaR 99% 1d", "var1w": "VaR 99% 1w", "es": "ES 99% 1w",
        "tail_note": "Losses in absolute value",
        "src_nd": "source n/a",
        "src_fin": "Financial statements", "src_cons": "Consensus", "src_price": "Price",
        "src_quant": "Bellomberg quantitative calculations and portfolio risk",
        "src_ret": "Returns computed by Bellomberg from daily prices",
        "miss_history": "Financial history not available.",
        "miss_revenue": "Revenue for at least 2 fiscal years is required; available: {n}.",
        "miss_margin": "Operating margin not computable: no fiscal year with both revenue and operating income.",
        "miss_cash": "Operating cash flow, capex and free cash flow in the same fiscal year are required for at least 2 years; available: {n}.",
        "miss_block": "{block} not available.",
        "miss_fields": "Missing {fields}.",
        "miss_currency": "Currency not stated.",
        "miss_series_absent": "The history lacks {fields}.",
        "miss_rows": "{fields} in the same fiscal year are required for at least {need} years; available: {n}.",
        "miss_cons": "Consensus estimates not available: {field}.",
        "miss_cons_q": "Annual consensus estimates not available ({field}): only quarterly estimates exist, which are not mixed with years.",
        "miss_units": "History in {a} and consensus in {b}: different units, no comparison possible.",
        "miss_reco": "Incomplete recommendations: missing {fields}.",
        "miss_reco_zero": "All recommendation counts are zero.",
        "miss_window": "Return window not stated.",
        "miss_assets": "At least 2 assets with a return are required; available: {n}.",
        "miss_cand": "The candidate is not among the assets with a return.",
    },
}


# ------------------------------------------------------------------ numeri e frasi
def fmt_number(value, decimals=0, language="it"):
    """Migliaia '.' e decimali ',' in italiano; il contrario in inglese.

    Arrotondamento commerciale (metà in su, lontano da zero) sul repr del float:
    1.565 -> «1,57», non «1,56» come darebbe il binario di format().
    """
    q = Decimal(1).scaleb(-decimals)
    rounded = Decimal(repr(float(value))).quantize(q, rounding=ROUND_HALF_UP)
    rendered = f"{rounded:,.{decimals}f}"
    if rendered.startswith("-") and float(rendered.replace(",", "")) == 0:
        rendered = rendered[1:]  # niente "-0"
    if validate_language(language) == "it":
        rendered = rendered.translate(str.maketrans(",.", ".,"))
    return rendered


class _F:
    """Formattazione locale + scelta della frase per lingua."""

    def __init__(self, language):
        self.lang = language

    def n(self, v, d=0):
        return fmt_number(v, d, self.lang)

    def pct(self, v, d=1, signed=False):
        s = fmt_number(v, d, self.lang)
        if signed and v > 0 and s.strip("0,."):
            s = "+" + s
        return s + "%"

    def pp(self, new, old):
        """Scarto in punti fra due percentuali, calcolato sui valori MOSTRATI (1 decimale)."""
        v = round(new, 1) - round(old, 1)
        s = self.pct(v, 1, True)[:-1]
        return self.tr(f"{s} punti percentuali", f"{s} percentage points")

    def tr(self, it, en):
        return it if self.lang == "it" else en

    def il(self, s):
        """Articolo davanti a un numero: «l'80%», «l'11%», «il 39%» (in inglese nulla)."""
        if self.lang != "it":
            return s
        elide = s.startswith("8") or (s.startswith("11") and not s[2:3].isdigit())
        return ("l'" if elide else "il ") + s

    def al(self, s):
        if self.lang != "it":
            return "at " + s
        elide = s.startswith("8") or (s.startswith("11") and not s[2:3].isdigit())
        return ("all'" if elide else "al ") + s

    def join(self, items):
        items = [str(i) for i in items]
        if len(items) <= 1:
            return "".join(items)
        return ", ".join(items[:-1]) + self.tr(" e ", " and ") + items[-1]


def _chg(a, b):
    """Variazione % da a a b; None se la base non e' positiva (non significativa)."""
    return (b - a) / a * 100.0 if a > 0 else None


def _cagr(a, b, years):
    if a > 0 and b > 0 and years > 0:
        return ((b / a) ** (1.0 / years) - 1.0) * 100.0
    return None


def _dec(values):
    top = max((abs(v) for v in values), default=0)
    return 0 if top >= 100 else (1 if top >= 1 else 2)


def _ok(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _year_value(series, year):
    """Le chiavi possono essere int o str (round-trip JSON)."""
    if not isinstance(series, dict):
        return None
    for k in (year, str(year)):
        if k in series and _ok(series[k]):
            return float(series[k])
    return None


def _years(history):
    out = []
    for y in (history or {}).get("years") or []:
        try:
            out.append(int(y))
        except (TypeError, ValueError):
            continue
    return out


def _rows(history, fields):
    """[(anno, [valori])] solo per gli esercizi in cui TUTTE le serie hanno un numero."""
    series = (history or {}).get("series") or {}
    rows = []
    for y in _years(history):
        vals = [_year_value(series.get(f), y) for f in fields]
        if all(v is not None for v in vals):
            rows.append((y, vals))
    return rows


def operating_margins(history):
    """{anno: margine %} solo dove ricavi (>0) e utile operativo ci sono entrambi."""
    return {y: v[1] / v[0] * 100.0 for y, v in _rows(history, ("revenue", "operating_income")) if v[0] > 0}


_PERIODS = {
    "0y": ("anno in corso", "current year"), "+1y": ("anno prossimo", "next year"),
    "+2y": ("tra due anni", "in two years"), "0q": ("trimestre in corso", "current quarter"),
    "+1q": ("prossimo trimestre", "next quarter"),
}


def period_label(period, language="it"):
    """Periodo del consensus in parole: «0y» -> «anno in corso»; un periodo sconosciuto resta invariato."""
    language = validate_language(language)
    pair = _PERIODS.get(str(period).strip().lower())
    return (pair[0] if language == "it" else pair[1]) if pair else str(period)


def is_quarterly(period):
    """Periodo trimestrale del consensus («0q», «+1q»): mai mescolato con gli anni."""
    return "q" in str(period).lower()


_WINDOW_UNITS = {"d": ("giorno", "giorni", "day", "days"), "wk": ("settimana", "settimane", "week", "weeks"),
                 "w": ("settimana", "settimane", "week", "weeks"), "mo": ("mese", "mesi", "month", "months"),
                 "y": ("anno", "anni", "year", "years")}


def window_label(window, language="it"):
    """Finestra dei rendimenti in parole: «1y» -> «1 anno», «6mo» -> «6 mesi»; altro invariato."""
    language = validate_language(language)
    raw = str(window).strip()
    if raw.lower() == "ytd":
        return "da inizio anno" if language == "it" else "year to date"
    m = re.match(r"^(\d+)\s*(d|wk|w|mo|y)$", raw.lower())
    if not m:
        return raw
    n = int(m.group(1))
    one_it, many_it, one_en, many_en = _WINDOW_UNITS[m.group(2)]
    word = (one_it if n == 1 else many_it) if language == "it" else (one_en if n == 1 else many_en)
    return f"{n} {word}"


def _estimates(cons, field):
    """([(periodo, valore)] annuali, ci_sono_trimestrali): i trimestri non entrano mai nel grafico."""
    out, quarterly = [], False
    for item in (cons or {}).get(field) or []:
        if isinstance(item, dict) and _ok(item.get("value")) and item.get("period") not in (None, ""):
            if is_quarterly(item["period"]):
                quarterly = True
                continue
            out.append((str(item["period"]), float(item["value"])))
    return out, quarterly


# ------------------------------------------------------------------ stile
def _tick_formatter(axis, language, suffix=""):
    """Decimali dei tick scelti dai tick stessi: mai «-2%» per un tick a -2,5%."""
    def fmt(v, _pos):
        locs = list(axis.get_majorticklocs())
        d = 0
        while d < 4 and any(abs(round(x, d) - x) > 1e-9 * max(1.0, abs(x)) for x in locs):
            d += 1
        return fmt_number(v, d, language) + suffix
    return ticker.FuncFormatter(fmt)


def _style(ax, language, suffix="", axis="y"):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis=axis, color=GRID, lw=.5)
    ax.set_axisbelow(True)
    target = ax.yaxis if axis == "y" else ax.xaxis
    target.set_major_locator(ticker.MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
    target.set_major_formatter(_tick_formatter(target, language, suffix))


def _limits(values, head=.14):
    lo, hi = min(min(values), 0.0), max(max(values), 0.0)
    span = (hi - lo) or abs(hi) or 1.0
    return (lo - span * head if lo < 0 else 0.0), (hi + span * head if hi > 0 else span * head)


def _label_size(n):
    return 8 if n <= 6 else (7.4 if n <= 9 else 6.8)


def _bar_labels(ax, bars, vals, text, fs, color=INK, horizontal=False):
    lo, hi = (ax.get_xlim() if horizontal else ax.get_ylim())
    pad = (hi - lo) * .015
    for r, v, s in zip(bars, vals, text):
        if horizontal:
            ax.text(v + pad if v >= 0 else v - pad, r.get_y() + r.get_height() / 2, s,
                    ha="left" if v >= 0 else "right", va="center", fontsize=fs, color=color)
        else:
            ax.text(r.get_x() + r.get_width() / 2, v + pad if v >= 0 else v - pad, s, ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=fs, color=color)


def _zero_line(ax, lo, horizontal=False):
    if lo < 0:
        (ax.axvline if horizontal else ax.axhline)(0, color=AXIS, lw=.6)


def _legend_top(ax, ncol, fs=7.5, handlelength=1.0):
    handles, labels = ax.get_legend_handles_labels()
    rows = math.ceil(len(handles) / ncol)
    order = [r * ncol + c for c in range(ncol) for r in range(rows) if r * ncol + c < len(handles)]
    ax.legend([handles[i] for i in order], [labels[i] for i in order], fontsize=fs, frameon=False, loc="lower left", ncol=ncol, bbox_to_anchor=(-.02, 1.0),
              handlelength=handlelength, handleheight=.8, columnspacing=1.4, borderaxespad=.1,
              labelcolor=TICK)


def _estimate_bars(ax, x, vals, width):
    """Stime del consensus: barre bianche tratteggiate in navy (impianto M)."""
    return ax.bar(x, vals, width, color="white", edgecolor=NAVY, linewidth=.8, hatch="////", zorder=2)


def _place_text(fig, ax, s, candidates, others, **kw):
    """Mette il testo nella prima posizione candidata che non tocca le altre etichette e resta nell'asse."""
    renderer = fig.canvas.get_renderer()
    box_ax = ax.get_window_extent(renderer)
    boxes = [o.get_window_extent(renderer).expanded(1.06, 1.15) for o in others]
    txt = None
    for x, y, ha, va in candidates:
        if txt is not None:
            txt.remove()
        txt = ax.text(x, y, s, ha=ha, va=va, **kw)
        bb = txt.get_window_extent(renderer)
        inside = bb.x0 >= box_ax.x0 - 2 and bb.x1 <= box_ax.x1 + 2
        if inside and not any(bb.overlaps(b) for b in boxes):
            return txt
    return txt


def _mln_label(unit, language):
    return f"mln {unit}" if language == "it" else f"{unit} mn"


def _save(fig, out_dir, key):
    path = os.path.join(out_dir, f"ti_{key}.png")
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


def _date_label(value):
    """gg/mm/aaaa da una data ISO o da un oggetto data; altrimenti nessuna data (mai l'ISO grezzo)."""
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def _provider(src):
    """Dicitura breve del fornitore; None se la stringa e' tecnica (modulo, funzione, chiave)."""
    raw = str(src or "").strip()
    low = raw.lower()
    if not low:
        return None
    if "esef" in low:
        return "ESEF"
    if "xbrl" in low or "edgar" in low or re.search(r"\bsec\b", low):
        return "SEC XBRL"
    if "yahoo" in low or "yfinance" in low:
        return "Yahoo Finance"
    if " " not in raw and re.search(r"[._()]", raw):
        return None  # nome di modulo/funzione/chiave: non e' una fonte da stampare
    return raw


def _source(language, *parts):
    """Fonte leggibile da [(dicitura, blocco)]: «Bilanci ESEF, 31/12/2024 · Consensus Yahoo Finance»."""
    t = _T[language]
    seen = []
    for prefix, block in parts:
        block = block if isinstance(block, dict) else {}
        prov = _provider(block.get("source"))
        if prefix and "Bellomberg" in prefix:
            label = prefix  # calcoli interni: la dicitura fissa e' gia' la fonte
        elif prefix and prov:
            label = prov if prefix.split()[0].lower() in prov.lower() else f"{prefix} {prov}"
        else:
            label = prefix or prov
        if not label:
            continue
        when = _date_label(block.get("as_of") or block.get("date"))
        part = f"{label}, {when}" if when else label
        if part not in seen:
            seen.append(part)
    return " · ".join(seen) if seen else t["src_nd"]


# Nomi leggibili per le frasi «missing» (regola PM: niente nomi di campo).
_NAMES = {
    "it": {"revenue": "i ricavi", "operating_income": "l'utile operativo", "net_income": "l'utile netto",
           "cfo": "il flusso operativo", "capex": "il capex", "fcf": "il flusso libero", "cash": "la cassa",
           "long_term_debt": "il debito a lungo termine", "goodwill": "l'avviamento", "equity": "il patrimonio netto",
           "dividends_paid": "i dividendi pagati", "buyback": "i buyback", "eps_diluted": "l'EPS diluito",
           "price": "il prezzo", "low_52w": "il minimo a 52 settimane", "high_52w": "il massimo a 52 settimane",
           "target_low": "il target minimo", "target_high": "il target massimo", "target_mean": "il target medio",
           "target_median": "il target mediano", "eps": "EPS attesi", "revenue_est": "ricavi attesi",
           "vol_pct": "la volatilità", "beta": "il beta", "var99_1d_pct": "il VaR 99% a 1 giorno",
           "max_drawdown_pct": "il max drawdown", "var99_1w_pct": "il VaR 99% a 1 settimana",
           "es99_1w_pct": "l'ES 99% a 1 settimana", "candidate": "del candidato", "book": "del book",
           "quote": "Dati di prezzo", "consensus": "Dati del consensus", "risk": "Metriche di rischio",
           "returns": "Rendimenti"},
    "en": {"revenue": "revenue", "operating_income": "operating income", "net_income": "net income",
           "cfo": "operating cash flow", "capex": "capex", "fcf": "free cash flow", "cash": "cash",
           "long_term_debt": "long-term debt", "goodwill": "goodwill", "equity": "equity",
           "dividends_paid": "dividends paid", "buyback": "buybacks", "eps_diluted": "diluted EPS",
           "price": "the price", "low_52w": "the 52-week low", "high_52w": "the 52-week high",
           "target_low": "the lowest target", "target_high": "the highest target", "target_mean": "the mean target",
           "target_median": "the median target", "eps": "expected EPS", "revenue_est": "expected revenue",
           "vol_pct": "volatility", "beta": "beta", "var99_1d_pct": "1-day 99% VaR",
           "max_drawdown_pct": "max drawdown", "var99_1w_pct": "1-week 99% VaR",
           "es99_1w_pct": "1-week 99% ES", "candidate": "of the candidate", "book": "of the book",
           "quote": "Price data", "consensus": "Consensus data", "risk": "Risk metrics",
           "returns": "Returns"},
}
RECO_NAMES = {"strong_buy": "Strong buy", "buy": "Buy", "hold": "Hold", "sell": "Sell", "strong_sell": "Strong sell"}


def _names(fields, language):
    return _F(language).join(_NAMES[language].get(x, x) for x in fields)


def _rows_missing(history, fields, need, n, t, language):
    """Perché mancano le righe: serie assente del tutto, o esercizi incompleti."""
    series = (history or {}).get("series") or {}
    absent = [x for x in fields if not any(_year_value(series.get(x), y) is not None for y in _years(history))]
    if absent:
        return t["miss_series_absent"].format(fields=_names(absent, language))
    return t["miss_rows"].format(fields=_names(fields, language), need=need, n=n)


def _missing(key, title, reason):
    # accordo del verbo con un solo elemento: «Manca il target mediano», non «Mancano»
    for plural, singular in (("Mancano ", "Manca "), ("Nello storico mancano ", "Nello storico manca ")):
        if reason.startswith(plural):
            rest = reason[len(plural):]
            if " e " not in rest and ", " not in rest:
                reason = singular + rest
    return {"key": key, "title": title, "missing": reason}


def _done(key, title, subtitle, path, source, height, notes):
    return {"key": key, "title": title, "subtitle": subtitle, "path": path, "source": source,
            "aspect": height / WIDTH, "notes": [s for s in notes if s][:4]}


def _history(facts, key, title, t):
    history = facts.get("history")
    if not isinstance(history, dict):
        return None, _missing(key, title, t["miss_history"])
    unit = history.get("unit") or facts.get("currency")
    if not unit:
        return None, _missing(key, title, t["miss_currency"])
    return history, None


def _from_to(f, label, y0, v0, yn, vn, d, unit_word, with_cagr=False):
    """«X passa da a a b <unità> tra il y0 e il yn (+c%, media annua composta +g%).»"""
    extra = []
    chg = _chg(v0, vn)
    if chg is not None:
        extra.append(f.pct(chg, 1, True))
    cg = _cagr(v0, vn, yn - y0) if with_cagr and yn - y0 > 1 else None
    if cg is not None:
        extra.append(f.tr(f"media annua composta {f.pct(cg, 1, True)}", f"compound annual {f.pct(cg, 1, True)}"))
    tail = f" ({', '.join(extra)})." if extra else "."
    verb = "passano" if label.startswith("I ") else "passa"
    return f.tr(f"{label} {verb} da {f.n(v0, d)} a {f.n(vn, d)}{unit_word} tra il {y0} e il {yn}",
                f"{label} moves from {f.n(v0, d)} to {f.n(vn, d)}{unit_word} between {y0} and {yn}") + tail


# ------------------------------------------------------------------ 1. ricavi + margine
def _revenue_margin(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "revenue_margin"
    history, miss = _history(facts, key, t["rev_title"].format(years=""), t)
    if miss:
        return miss
    unit = history.get("unit") or facts.get("currency")
    rev = [(y, v[0]) for y, v in _rows(history, ("revenue",))]
    title = t["rev_title"].format(years=f", {rev[0][0]}-{rev[-1][0]}" if len(rev) >= 2 else "")
    if len(rev) < 2:
        return _missing(key, title, t["miss_revenue"].format(n=len(rev)))
    margins = operating_margins(history)
    if not margins:
        return _missing(key, title, t["miss_margin"])

    labels = [str(y) for y, _ in rev]
    vals = [v / 1e6 for _, v in rev]
    d = _dec(vals)
    marg = [margins.get(y, float("nan")) for y, _ in rev]
    n = len(labels)
    fs = _label_size(n)
    with plt.rc_context(_RC):
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(WIDTH, H_MAIN), dpi=DPI,
                                     gridspec_kw={"height_ratios": [2.1, 1]}, sharex=True)
        x = list(range(n))
        bars = a1.bar(x, vals, color=G2, width=.56, zorder=2)
        _style(a1, language)
        lo, hi = _limits(vals)
        a1.set_ylim(lo, hi)
        _bar_labels(a1, bars, vals, [f.n(v, d) for v in vals], fs)
        a1.set_ylabel(_mln_label(unit, language), fontsize=8, color=TICK)
        _zero_line(a1, lo)

        a2.plot(x, marg, color=NAVY, marker="o", ms=3.4, lw=1.4)
        _style(a2, language, "%")
        present = [m for m in marg if not math.isnan(m)]
        mlo, mhi = min(present), max(present)
        mspan = max(mhi - mlo, 2.0)
        a2.set_ylim(mlo - mspan * .35, mhi + mspan * .55)
        a2.yaxis.set_major_locator(ticker.MaxNLocator(3, steps=[1, 2, 5, 10]))
        for xi, m in zip(x, marg):
            if not math.isnan(m):
                a2.text(xi, m + mspan * .12, f.pct(m), ha="center", va="bottom", fontsize=fs - .2, color=NAVY)
        a2.set_ylabel(t["margin_axis"], fontsize=8, color=NAVY)
        a2.tick_params(axis="y", colors=NAVY)
        a2.set_xticks(x, labels)
        fig.align_ylabels()
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)

    # note
    mw = f.tr(f" milioni di {unit}", f" {unit} mn")
    notes = [_from_to(f, f.tr("I ricavi", "Revenue"), rev[0][0], vals[0], rev[-1][0], vals[-1], d, mw, True)]
    ym = dict(zip([y for y, _ in rev], vals))
    steps = [(y, _chg(ym[y - 1], ym[y])) for y in ym if y - 1 in ym and ym[y - 1] > 0]
    downs = [(y, c) for y, c in steps if c < 0]
    if steps:
        if not downs:
            notes.append(f.tr("Nessun esercizio in calo rispetto al precedente.",
                              "No fiscal year declines versus the previous one."))
        elif len(downs) == 1:
            notes.append(f.tr(f"Il {downs[0][0]} è l'unico esercizio in calo ({f.pct(downs[0][1], 1, True)}).",
                              f"{downs[0][0]} is the only year of decline ({f.pct(downs[0][1], 1, True)})."))
        else:
            lst = f.join(f"{y} ({f.pct(c, 1, True)})" for y, c in downs)
            notes.append(f.tr(f"Esercizi in calo rispetto al precedente: {lst}.",
                              f"Years of decline versus the previous one: {lst}."))
    my = [y for y, _ in rev if y in margins]
    if len(my) >= 2:
        m0, mn_ = margins[my[0]], margins[my[-1]]
        notes.append(f.tr(f"Il margine operativo passa da {f.pct(m0)} a {f.pct(mn_)} tra il {my[0]} e il {my[-1]} ({f.pp(mn_, m0)}).",
                          f"Operating margin moves from {f.pct(m0)} to {f.pct(mn_)} between {my[0]} and {my[-1]} ({f.pp(mn_, m0)})."))
    else:
        notes.append(f.tr(f"Margine operativo calcolabile solo per il {my[0]}: {f.pct(margins[my[0]])}.",
                          f"Operating margin computable only for {my[0]}: {f.pct(margins[my[0]])}."))
    gaps = [y for y, _ in rev if y not in margins]
    if gaps:
        notes.append(f.tr(f"Margine operativo non calcolabile per {f.join(gaps)}: manca l'utile operativo o i ricavi non sono positivi.",
                          f"Operating margin not computable for {f.join(gaps)}: operating income missing or revenue not positive."))
    return _done(key, title, t["rev_sub"].format(unit=unit), path, _source(language, (t["src_fin"], history)), H_MAIN, notes)


# ------------------------------------------------------------------ 2. cassa
def _cash(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "cash"
    title = t["cash_title"]
    history, miss = _history(facts, key, title, t)
    if miss:
        return miss
    unit = history.get("unit") or facts.get("currency")
    rows = _rows(history, ("cfo", "capex", "fcf"))
    if len(rows) < 2:
        return _missing(key, title, t["miss_cash"].format(n=len(rows)))

    capex_negative = any(v[1] < 0 for _, v in rows)
    years = [y for y, _ in rows]
    cfo = [v[0] / 1e6 for _, v in rows]
    capex = [abs(v[1]) / 1e6 for _, v in rows]  # uscita di cassa: barra positiva, dichiarato
    fcf = [v[2] / 1e6 for _, v in rows]
    d = _dec(cfo + capex + fcf)
    n = len(years)
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, H_MAIN), dpi=DPI)
        w = .26
        lo, hi = _limits(cfo + capex + fcf, head=.18)
        a.set_ylim(lo, hi)
        for k, (vals, col, lab) in enumerate([(cfo, G2, t["cfo"]), (capex, G4, t["capex"]),
                                               (fcf, NAVY, t["fcf"])]):
            bars = a.bar([i + (k - 1) * w for i in range(n)], vals, w, color=col, label=lab, zorder=2)
            if k == 2:
                _bar_labels(a, bars, vals, [f.n(v, d) for v in vals], _label_size(n) - .4, NAVY)
        a.set_xticks(range(n), [str(y) for y in years])
        _style(a, language)
        _zero_line(a, lo)
        a.set_ylabel(_mln_label(unit, language), fontsize=8, color=TICK)
        _legend_top(a, 3)
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)

    mw = f.tr(f" milioni di {unit}", f" {unit} mn")
    notes = [_from_to(f, f.tr("Il flusso libero", "Free cash flow"), years[0], fcf[0], years[-1], fcf[-1], d, mw)]
    neg = [y for y, v in zip(years, fcf) if v < 0]
    if neg:
        notes.append(f.tr(f"Flusso libero negativo nel {f.join(neg)}.", f"Free cash flow is negative in {f.join(neg)}."))
    elif all(v > 0 for v in fcf):
        notes.append(f.tr(f"Il flusso libero è positivo in tutti i {n} esercizi.",
                          f"Free cash flow is positive in all {n} fiscal years."))
    pos = [(c, k) for c, k in zip(cfo, capex) if c > 0]
    if pos:
        avg = sum(k / c for c, k in pos) / len(pos) * 100
        notes.append(f.tr(f"Negli esercizi con flusso operativo positivo ({len(pos)} su {n}) il capex ne vale in media {f.il(f.pct(avg))}.",
                          f"In the years with positive operating cash flow ({len(pos)} of {n}) capex averages {f.pct(avg)} of it."))
    off = [(y, fv - (c - k)) for y, c, k, fv in zip(years, cfo, capex, fcf)
           if abs(fv - (c - k)) > max(abs(c) * .01, 10 ** -d)]
    if off:
        y, diff = off[-1]
        notes.append(f.tr(f"Nel {y} il flusso libero della fonte differisce da flusso operativo meno capex di {f.n(diff, d)}{mw} (definizione della fonte).",
                          f"In {y} the source's free cash flow differs from operating cash flow minus capex by {f.n(diff, d)}{mw} (source definition)."))
    sub = t["cash_sub_abs" if capex_negative else "cash_sub"].format(unit=unit)
    return _done(key, title, sub, path, _source(language, (t["src_fin"], history)), H_MAIN, notes)


# ------------------------------------------------------------------ 3. prezzo vs target
def _line_segments(fig, ax, x, texts, y_lo, y_hi):
    """Tratti verticali in x da y_lo a y_hi che saltano le etichette attraversate."""
    renderer = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    gaps = []
    for txt in texts:
        bb = txt.get_window_extent(renderer)
        (bx0, by0), (bx1, by1) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
        pad_x = (bx1 - bx0) * .04
        if bx0 - pad_x <= x <= bx1 + pad_x:
            gaps.append((by0 - .06, by1 + .06))
    segments, start = [], y_lo
    for g0, g1 in sorted(gaps):
        if g1 <= start or g0 >= y_hi:
            continue
        if g0 > start:
            segments.append((start, g0))
        start = max(start, g1)
    if start < y_hi:
        segments.append((start, y_hi))
    return segments


def _price_num(v, language, dec):
    s = fmt_number(v, dec, language)
    return s[:-3] if dec and s.endswith("00") and s[-3] in ",." else s


def _price_targets(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "price_targets"
    title = t["price_title"]
    quote, cons = facts.get("quote"), facts.get("consensus")
    if not isinstance(quote, dict):
        return _missing(key, title, t["miss_block"].format(block=_NAMES[language]["quote"]))
    if not isinstance(cons, dict):
        return _missing(key, title, t["miss_block"].format(block=_NAMES[language]["consensus"]))
    need = [("quote", "price"), ("consensus", "target_low"), ("consensus", "target_high"),
            ("consensus", "target_mean"), ("consensus", "target_median")]
    src = {"quote": quote, "consensus": cons}
    absent = [k for b, k in need if not _ok(src[b].get(k))]
    if absent:
        return _missing(key, title, t["miss_fields"].format(fields=_names(absent, language)))
    cur = facts.get("currency")
    if not cur:
        return _missing(key, title, t["miss_currency"])

    price = float(quote["price"])
    has52 = _ok(quote.get("low_52w")) and _ok(quote.get("high_52w")) and quote["high_52w"] >= quote["low_52w"]
    lo52, hi52 = (float(quote["low_52w"]), float(quote["high_52w"])) if has52 else (None, None)
    tlo, thi = float(cons["target_low"]), float(cons["target_high"])
    mean, median = float(cons["target_mean"]), float(cons["target_median"])
    allv = [price, tlo, thi, mean, median] + ([lo52, hi52] if has52 else [])
    height = 1.9 if has52 else 1.2  # fascia bassa come la Figura 2 approvata
    xmin, xmax = min(allv), max(allv)
    span = (xmax - xmin) or abs(xmax) or 1.0
    x0, x1 = xmin - span * .1, xmax + span * .1
    dec = 2 if xmax < 1000 else 0

    def num(v):
        return _price_num(v, language, dec)

    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, height), dpi=DPI)
        a.set_ylim(-.95 if has52 else .05, 1.85)
        a.set_xlim(x0, x1)
        a.xaxis.set_major_locator(ticker.MaxNLocator(8, steps=[1, 2, 2.5, 5, 10]))
        a.xaxis.set_major_formatter(_tick_formatter(a.xaxis, language))
        a.spines[["top", "right", "left"]].set_visible(False)
        a.grid(axis="x", color=GRID, lw=.5)
        a.set_axisbelow(True)
        if has52:
            a.set_yticks([0, 1], [t["range52"], t["targets"]])
        else:
            a.set_yticks([1], [t["targets"]])
        a.tick_params(axis="y", length=0, labelsize=8)
        a.set_xlabel(t["per_share"].format(cur=cur), fontsize=8, color=TICK)
        fig.tight_layout(pad=.3)

        a.hlines(1, tlo, thi, color=G4, lw=5, zorder=1)
        a.plot([tlo, thi], [1, 1], "|", color=G1, ms=12, mew=1.1, zorder=2)
        a.plot([mean], [1], "o", color="white", mec=INK, mew=1.1, ms=6.5, zorder=3)
        a.plot([median], [1], "s", color=G1, ms=5.5, zorder=3)
        if has52:
            a.hlines(0, lo52, hi52, color=G4, lw=3, zorder=1)
            a.plot([lo52, hi52], [0, 0], "|", color=G2, ms=9, mew=1, zorder=2)
            a.plot([price], [0], "D", color=NAVY, ms=5, zorder=4)
        a.plot([price], [1], "D", color=NAVY, ms=7, zorder=4)
        placed = []
        narrow = (thi - tlo) < span * .25
        placed.append(a.text(tlo, .68, f"{t['min']} {num(tlo)}", ha="right" if narrow else "center",
                             va="top", fontsize=8, color=TICK))
        placed.append(a.text(thi, .68, f"{t['max']} {num(thi)}", ha="left" if narrow else "center",
                             va="top", fontsize=8, color=TICK))
        if has52:
            n52 = (hi52 - lo52) < span * .15
            placed.append(a.text(lo52, -.32, num(lo52), ha="right" if n52 else "center", va="top", fontsize=8, color=TICK))
            placed.append(a.text(hi52, -.32, num(hi52), ha="left" if n52 else "center", va="top", fontsize=8, color=TICK))
        gap = span * .015
        first, second = sorted([(mean, t["mean"]), (median, t["median"])], key=lambda p: p[0])
        placed.append(a.text(first[0] - gap, 1.3, f"{first[1]} {num(first[0])}", ha="right", va="bottom", fontsize=8, color=INK))
        placed.append(a.text(second[0] + gap, 1.3, f"{second[1]} {num(second[0])}", ha="left", va="bottom", fontsize=8, color=INK))
        g2 = span * .02
        cands = [(price, .68, "center", "top"), (price + g2, .5, "left", "center"), (price - g2, .5, "right", "center"),
                 (price, .4, "center", "top"), (price, 1.62, "center", "bottom")]
        if not has52:
            cands = [(price, .68, "center", "top"), (price, .42, "center", "top"),
                     (price + g2, .62, "left", "top"), (price - g2, .62, "right", "top")]
        lab = _place_text(fig, a, f"{t['price']} {num(price)}", cands, placed, fontsize=8, color=NAVY, weight="bold")
        if has52:  # collegamento tratteggiato fra i due rombi, interrotto dove passa un'etichetta
            for y_lo, y_hi in _line_segments(fig, a, price, placed + [lab], 0, 1):
                a.vlines(price, y_lo, y_hi, color=NAVY, lw=.8, linestyles=(0, (3, 2)), zorder=1)
        path = _save(fig, out_dir, key)

    notes = []
    if price > 0:
        um, ud = (mean / price - 1) * 100, (median / price - 1) * 100
        notes.append(f.tr(f"Il target medio ({num(mean)} {cur}) implica {f.pct(um, 1, True)} rispetto al prezzo di {num(price)} {cur}; il mediano ({num(median)}) {f.pct(ud, 1, True)}.",
                          f"The mean target ({cur} {num(mean)}) implies {f.pct(um, 1, True)} versus the price of {cur} {num(price)}; the median ({num(median)}) {f.pct(ud, 1, True)}."))
    if tlo > 0 and thi > 0:
        if price < tlo:
            notes.append(f.tr(f"Il prezzo è {f.pct((tlo - price) / tlo * 100)} sotto il target minimo ({num(tlo)}).",
                              f"The price is {f.pct((tlo - price) / tlo * 100)} below the lowest target ({num(tlo)})."))
        elif price > thi:
            notes.append(f.tr(f"Il prezzo è {f.pct((price - thi) / thi * 100)} sopra il target massimo ({num(thi)}).",
                              f"The price is {f.pct((price - thi) / thi * 100)} above the highest target ({num(thi)})."))
        else:
            notes.append(f.tr(f"Il prezzo è {f.pct((price - tlo) / tlo * 100)} sopra il target minimo ({num(tlo)}) e {f.pct((thi - price) / thi * 100)} sotto il massimo ({num(thi)}).",
                              f"The price is {f.pct((price - tlo) / tlo * 100)} above the lowest target ({num(tlo)}) and {f.pct((thi - price) / thi * 100)} below the highest ({num(thi)})."))
    if not has52:
        pass  # dichiarato nel sottotitolo
    elif hi52 > lo52 and lo52 <= price <= hi52:
        pos = (price - lo52) / (hi52 - lo52) * 100
        below = (hi52 - price) / hi52 * 100
        notes.append(f.tr(f"Nel range di 52 settimane ({num(lo52)}-{num(hi52)}) il prezzo sta {f.al(f.pct(pos, 0))} della distanza fra minimo e massimo, {f.pct(below)} sotto il massimo.",
                          f"Within the 52-week range ({num(lo52)}-{num(hi52)}) the price sits at {f.pct(pos, 0)} of the distance from low to high, {f.pct(below)} below the high."))
    else:
        notes.append(f.tr(f"Il prezzo ({num(price)}) è fuori dal range di 52 settimane riportato ({num(lo52)}-{num(hi52)}).",
                          f"The price ({num(price)}) is outside the reported 52-week range ({num(lo52)}-{num(hi52)})."))
    disp = f.pct((thi - tlo) / mean * 100, 0) if mean > 0 else None
    n_an = cons.get("analysts")
    if _ok(n_an) and disp:
        notes.append(f.tr(f"Target di {int(n_an)} analisti; l'ampiezza del ventaglio (massimo meno minimo) vale {f.il(disp)} del target medio.",
                          f"Targets from {int(n_an)} analysts; the range width (high minus low) equals {disp} of the mean target."))
    elif disp:
        notes.append(f.tr(f"L'ampiezza del ventaglio dei target (massimo meno minimo) vale {f.il(disp)} del target medio; numero di analisti n.d.",
                          f"The target range width (high minus low) equals {disp} of the mean target; number of analysts n/a."))
    sub = t["price_sub"] if has52 else t["price_sub_no52"]
    return _done(key, title, sub, path, _source(language, (t["src_cons"], cons), (t["src_price"], quote)), height, notes)


# ------------------------------------------------------------------ 4. rischio
_METRICS = (("vol_pct", "vol", 1, "%", False), ("beta", "beta", 2, "", False),
            ("var99_1d_pct", "var", 1, "%", True), ("max_drawdown_pct", "dd", 1, "%", True))


def _short_name(ticker_label, t):
    return (ticker_label or "").split(".")[0][:8] or t["candidate"]


def _beta_note(f, bc, bb, basis_c, basis_b):
    """Beta del candidato e del book; su basi diverse lo dichiara invece di confrontarli."""
    if basis_c and basis_b and str(basis_c) != str(basis_b):
        return f.tr(f"Beta: {f.n(bc, 2)} (base {basis_c}) e {f.n(bb, 2)} del book (base {basis_b}): basi diverse, non confrontabili direttamente.",
                    f"Beta: {f.n(bc, 2)} (basis {basis_c}) and {f.n(bb, 2)} for the book (basis {basis_b}): different bases, not directly comparable.")
    return f.tr(f"Beta: {f.n(bc, 2)} contro {f.n(bb, 2)} del book.", f"Beta: {f.n(bc, 2)} against {f.n(bb, 2)} for the book.")


def _risk(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "risk"
    name = _short_name(ticker_label, t)
    title = t["risk_title"].format(name=name)
    risk = facts.get("risk")
    if not isinstance(risk, dict):
        return _missing(key, title, t["miss_block"].format(block=_NAMES[language]["risk"]))
    cand, book = risk.get("candidate"), risk.get("book")
    nm = _NAMES[language]
    absent = [f"{nm[field]} {nm[side]}" for side, blk in (("candidate", cand), ("book", book))
              for field, *_ in _METRICS if not isinstance(blk, dict) or not _ok(blk.get(field))]
    if absent:
        return _missing(key, title, t["miss_fields"].format(fields=_F(language).join(absent)))

    shown = {}
    with plt.rc_context(_RC):
        fig, axs = plt.subplots(1, 4, figsize=(WIDTH, H_SMALL), dpi=DPI)
        for a, (field, lab, dec, suf, absolute) in zip(axs, _METRICS):
            vals = [float(book[field]), float(cand[field])]
            if absolute:
                vals = [abs(v) for v in vals]
            shown[field] = vals
            bars = a.bar([t["book"], name], vals, color=[G3, NAVY], width=.6, zorder=2)
            lo, hi = _limits(vals, head=.16)
            a.set_ylim(lo, hi)
            _bar_labels(a, bars, vals, [f.n(v, dec) + suf for v in vals], 8)
            _zero_line(a, lo)
            a.set_title(t[lab], fontsize=8.5, color=INK)
            a.set_yticks([])
            a.spines[["top", "right", "left"]].set_visible(False)
            a.tick_params(axis="x", labelsize=8, length=0)
        fig.text(.5, .01, t["abs_note"], ha="center", fontsize=7.5, color=NOTE)
        fig.tight_layout(pad=.4, rect=(0, .05, 1, 1))
        path = _save(fig, out_dir, key)

    def times(bk, cd):
        return f.tr(f" ({f.n(cd / bk, 1)} volte)", f" ({f.n(cd / bk, 1)}x)") if bk > 0 and cd > 0 else ""

    (vb, vc), (bb, bc), (rb, rc), (db, dc) = (shown[m[0]] for m in _METRICS)
    notes = [
        f.tr(f"Volatilità annua: {f.pct(vc)} per {name} contro {f.pct(vb)} del book{times(vb, vc)}.",
             f"Annual volatility: {f.pct(vc)} for {name} against {f.pct(vb)} for the book{times(vb, vc)}."),
        _beta_note(f, bc, bb, cand.get("beta_basis"), book.get("beta_basis")),
        f.tr(f"VaR 99% a 1 giorno in valore assoluto: {f.pct(rc)} contro {f.pct(rb)} del book{times(rb, rc)}.",
             f"1-day 99% VaR in absolute value: {f.pct(rc)} against {f.pct(rb)} for the book{times(rb, rc)}."),
        f.tr(f"Max drawdown in valore assoluto: {f.pct(dc)} contro {f.pct(db)} del book{times(db, dc)}.",
             f"Max drawdown in absolute value: {f.pct(dc)} against {f.pct(db)} for the book{times(db, dc)}."),
    ]
    return _done(key, title, t["risk_sub"], path, _source(language, (t["src_quant"], risk)), H_SMALL, notes)


# ------------------------------------------------------------------ grafici a barre raggruppate
def _grouped(fig, a, years, series, language, unit, d, w):
    n = len(years)
    k_n = len(series)
    allv = [v for vals, *_ in series for v in vals]
    lo, hi = _limits(allv, head=.12)
    a.set_ylim(lo, hi)
    for k, (vals, col, lab) in enumerate(series):
        a.bar([i + (k - (k_n - 1) / 2) * w for i in range(n)], vals, w, color=col, label=lab, zorder=2)
    a.set_xticks(range(n), [str(y) for y in years])
    _style(a, language)
    _zero_line(a, lo)
    a.set_ylabel(_mln_label(unit, language), fontsize=8, color=TICK)
    _legend_top(a, 4)


# ------------------------------------------------------------------ 5. stato patrimoniale
def _balance(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "balance"
    title = t["bal_title"]
    history, miss = _history(facts, key, title, t)
    if miss:
        return miss
    unit = history.get("unit") or facts.get("currency")
    fields = ("cash", "long_term_debt", "goodwill", "equity")
    rows = _rows(history, fields)
    if not rows:
        return _missing(key, title, _rows_missing(history, fields, 1, 0, t, language))
    years = [y for y, _ in rows]
    cols = [[v[i] / 1e6 for _, v in rows] for i in range(4)]
    d = _dec([x for c in cols for x in c])
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, H_MAIN), dpi=DPI)
        _grouped(fig, a, years, [(cols[0], G4, t["cash_bs"]), (cols[1], NAVY, t["ltd"]),
                                 (cols[2], G2, t["goodwill"]), (cols[3], G1, t["equity"])],
                 language, unit, d, .2)
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)

    mw = f.tr(f" milioni di {unit}", f" {unit} mn")
    cash, ltd, gw, eq = (c[-1] for c in cols)
    yn = years[-1]
    notes = [f.tr(f"Nel {yn}: cassa {f.n(cash, d)}, debito a lungo termine {f.n(ltd, d)}, avviamento {f.n(gw, d)}, patrimonio netto {f.n(eq, d)}{mw}.",
                  f"In {yn}: cash {f.n(cash, d)}, long-term debt {f.n(ltd, d)}, goodwill {f.n(gw, d)}, equity {f.n(eq, d)}{mw}.")]
    if len(years) >= 2:
        notes.append(_from_to(f, f.tr("Il debito a lungo termine", "Long-term debt"), years[0], cols[1][0], yn, ltd, d, mw))
        notes.append(_from_to(f, f.tr("L'avviamento", "Goodwill"), years[0], cols[2][0], yn, gw, d, mw))
    if ltd > 0:
        notes.append(f.tr(f"Nel {yn} la cassa vale {f.il(f.pct(cash / ltd * 100))} del debito a lungo termine.",
                          f"In {yn} cash equals {f.pct(cash / ltd * 100)} of long-term debt."))
    if eq > 0:
        notes.append(f.tr(f"Nel {yn} l'avviamento vale {f.il(f.pct(gw / eq * 100))} del patrimonio netto.",
                          f"In {yn} goodwill equals {f.pct(gw / eq * 100)} of equity."))
    else:
        notes.append(f.tr(f"Nel {yn} il patrimonio netto non è positivo ({f.n(eq, d)}{mw}).",
                          f"In {yn} equity is not positive ({f.n(eq, d)}{mw})."))
    if len(notes) > 4:  # priorita': fotografia, debito, copertura cassa, avviamento/PN
        notes = [notes[0], notes[1], notes[3], notes[4]]
    return _done(key, title, t["bal_sub"].format(unit=unit), path, _source(language, (t["src_fin"], history)), H_MAIN, notes)


# ------------------------------------------------------------------ 6. margini
def _margins(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "margins"
    title = t["mar_title"]
    history, miss = _history(facts, key, title, t)
    if miss:
        return miss
    fields = ("revenue", "operating_income", "net_income", "fcf")
    rows = [(y, v) for y, v in _rows(history, fields) if v[0] > 0]
    if len(rows) < 2:
        return _missing(key, title, _rows_missing(history, fields, 2, len(rows), t, language))
    years = [y for y, _ in rows]
    op = [v[1] / v[0] * 100 for _, v in rows]
    net = [v[2] / v[0] * 100 for _, v in rows]
    fm = [v[3] / v[0] * 100 for _, v in rows]
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, H_MID), dpi=DPI)
        x = list(range(len(years)))
        for vals, col, mk, ls, lab in [(op, NAVY, "o", "-", t["op_m"]), (net, G2, "s", "-", t["net_m"]),
                                       (fm, G1, "^", (0, (4, 2)), t["fcf_m"])]:
            a.plot(x, vals, color=col, marker=mk, ms=3.4, lw=1.4, ls=ls, label=lab)
        _style(a, language, "%")
        allv = op + net + fm
        lo, hi = min(min(allv), 0), max(max(allv), 0)
        span = (hi - lo) or 1.0
        a.set_ylim(lo - span * .08 if lo < 0 else 0, hi + span * .12)
        _zero_line(a, lo)
        a.set_xticks(x, [str(y) for y in years])
        _legend_top(a, 3, handlelength=2.4)
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)

    y0, yn = years[0], years[-1]
    notes = [
        f.tr(f"Il margine operativo passa da {f.pct(op[0])} a {f.pct(op[-1])} tra il {y0} e il {yn} ({f.pp(op[-1], op[0])}).",
             f"Operating margin moves from {f.pct(op[0])} to {f.pct(op[-1])} between {y0} and {yn} ({f.pp(op[-1], op[0])})."),
        f.tr(f"Il margine netto passa da {f.pct(net[0])} a {f.pct(net[-1])} ({f.pp(net[-1], net[0])}).",
             f"Net margin moves from {f.pct(net[0])} to {f.pct(net[-1])} ({f.pp(net[-1], net[0])})."),
    ]
    i_min = min(range(len(fm)), key=lambda i: fm[i])
    notes.append(f.tr(f"Il margine FCF (flusso libero su ricavi) è in media {f.pct(sum(fm) / len(fm))}, minimo {f.pct(fm[i_min])} nel {years[i_min]}.",
                      f"FCF margin (free cash flow over revenue) averages {f.pct(sum(fm) / len(fm))}, lowest {f.pct(fm[i_min])} in {years[i_min]}."))
    excluded = [y for y in _years(history) if y not in years and
                _year_value((history.get("series") or {}).get("revenue"), y) is not None]
    neg = [y for y, v in zip(years, net) if v < 0]
    if excluded:
        notes.append(f.tr(f"Esclusi per dati incompleti: {f.join(excluded)}.", f"Excluded for incomplete data: {f.join(excluded)}."))
    elif neg:
        notes.append(f.tr(f"Margine netto negativo nel {f.join(neg)}.", f"Net margin is negative in {f.join(neg)}."))
    return _done(key, title, t["mar_sub"], path, _source(language, (t["src_fin"], history)), H_MID, notes)


# ------------------------------------------------------------------ 7-8. storico + stime
def _path_chart(hist, cons, d, ylabel, language, estimate_word, out_dir, key, suffix=""):
    f = _F(language)
    def tick(p):  # «anno in corso» su due righe: le etichette vicine non si toccano
        name = period_label(p, language)
        return name.replace(" ", "\n", 1) if len(name) > 8 else name
    labels = [str(p) for p, _ in hist] + [f"{tick(p)}\n({estimate_word})" for p, _ in cons]
    vals = [v for _, v in hist] + [v for _, v in cons]
    n = len(vals)
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, H_MID), dpi=DPI)
        x = list(range(n))
        lo, hi = _limits(vals)
        a.set_ylim(lo, hi)
        bars = a.bar(x[:len(hist)], vals[:len(hist)], color=G2, width=.56, zorder=2)
        est = _estimate_bars(a, x[len(hist):], vals[len(hist):], .56)
        a.axvline(len(hist) - .5, color=G3, lw=.6, ls=(0, (3, 3)))
        _bar_labels(a, list(bars) + list(est), vals, [f.n(v, d) + suffix for v in vals], _label_size(n) - .4)
        _style(a, language)
        _zero_line(a, lo)
        a.set_xticks(x, labels)
        a.tick_params(axis="x", labelsize=8 if n <= 8 else 7)
        a.set_ylabel(ylabel, fontsize=8, color=TICK)
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)
    return path


def _path_notes(f, label, hist, cons, d, unit_word, gaps=(), quarterly=False):
    (y0, v0), (yn, vn) = hist[0], hist[-1]
    notes = []
    if len(hist) >= 2:
        notes.append(_from_to(f, label, int(y0), v0, int(yn), vn, d, unit_word, True))
    else:
        notes.append(f.tr(f"Un solo esercizio storico: {yn}, {f.n(vn, d)}{unit_word}.",
                          f"Only one historical year: {yn}, {f.n(vn, d)}{unit_word}."))
    if gaps:
        notes.append(f.tr(f"Storico non disponibile per {f.join(gaps)}: il grafico mostra solo gli esercizi presenti.",
                          f"History not available for {f.join(gaps)}: the chart shows only the years present."))
    prev = vn
    for i, (p, v) in enumerate(cons[:2]):
        c = _chg(prev, v)
        base_it = f"all'ultimo esercizio storico ({yn})" if i == 0 else "alla stima precedente"
        base_en = f"the last reported year ({yn})" if i == 0 else "the previous estimate"
        rel = f.tr(f"{f.pct(c, 1, True)} rispetto {base_it}", f"{f.pct(c, 1, True)} versus {base_en}") if c is not None \
            else f.tr(f"variazione rispetto {base_it} non calcolabile: base non positiva",
                      f"change versus {base_en} not computable: base not positive")
        name = period_label(p, f.lang)
        name = name[:1].upper() + name[1:]
        notes.append(f.tr(f"{name} (stima): {f.n(v, d)}{unit_word}, {rel}.",
                          f"{name} (estimate): {f.n(v, d)}{unit_word}, {rel}."))
        prev = v
    neg = [y for y, v in hist if v < 0]
    if neg:
        notes.append(f.tr(f"Valori storici negativi nel {f.join(neg)}.", f"Negative historical values in {f.join(neg)}."))
    if len(cons) > 2:
        notes.append(f.tr(f"Altre {len(cons) - 2} stime nel grafico.", f"{len(cons) - 2} further estimates in the chart."))
    if quarterly:
        notes.append(f.tr("Le stime trimestrali sono nella tabella del consensus.",
                          "Quarterly estimates are in the consensus table."))
    return notes[:4] if not quarterly or len(notes) <= 4 else notes[:3] + notes[-1:]


def _hist_and_cons(facts, key, title, t, series_field, cons_field, language):
    history, miss = _history(facts, key, title, t)
    if miss:
        return None, None, None, miss
    hist = [(y, v[0]) for y, v in _rows(history, (series_field,))]
    if not hist:
        return None, None, None, _missing(key, title, t["miss_series_absent"].format(fields=_names([series_field], language)))
    cons, quarterly = _estimates(facts.get("consensus") if isinstance(facts.get("consensus"), dict) else None, cons_field)
    if not cons:
        label = "revenue_est" if cons_field == "revenue" else cons_field
        reason = t["miss_cons_q" if quarterly else "miss_cons"].format(field=_names([label], language))
        return None, None, None, _missing(key, title, reason)
    unit, cur = history.get("unit"), facts.get("currency")
    if not cur:
        return None, None, None, _missing(key, title, t["miss_currency"])
    if unit and unit != cur:
        return None, None, None, _missing(key, title, t["miss_units"].format(a=unit, b=cur))
    present = {y for y, _ in hist}
    gaps = [y for y in _years(history) if hist[0][0] < y < hist[-1][0] and y not in present]
    return history, hist, cons, (gaps, quarterly)


def _eps_path(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "eps_path"
    title = t["eps_title"]
    history, hist, cons, miss = _hist_and_cons(facts, key, title, t, "eps_diluted", "eps", language)
    if isinstance(miss, dict):
        return miss
    cur = facts["currency"]
    path = _path_chart(hist, cons, 2, t["per_share"].format(cur=cur), language, t["estimate"], out_dir, key)
    notes = _path_notes(f, f.tr("L'EPS diluito", "Diluted EPS"), hist, cons, 2, f" {cur}", *miss)
    return _done(key, title, t["eps_sub"].format(cur=cur), path,
                 _source(language, (t["src_fin"], history), (t["src_cons"], facts.get("consensus"))), H_MID, notes)


def _revenue_path(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "revenue_path"
    title = t["rp_title"]
    history, hist, cons, miss = _hist_and_cons(facts, key, title, t, "revenue", "revenue", language)
    if isinstance(miss, dict):
        return miss
    cur = facts["currency"]
    big = max(abs(v) for _, v in hist + cons) >= 1e9
    div = 1e9 if big else 1e6
    scale = (f.tr("mld", "bn") if big else f.tr("mln", "mn"))
    hist_s = [(y, v / div) for y, v in hist]
    cons_s = [(p, v / div) for p, v in cons]
    d = 1 if big else _dec([v for _, v in hist_s + cons_s])
    ylabel = f"{scale} {cur}" if language == "it" else f"{cur} {scale}"
    path = _path_chart(hist_s, cons_s, d, ylabel, language, t["estimate"], out_dir, key)
    uw = f.tr(f" {'miliardi' if big else 'milioni'} di {cur}", f" {cur} {scale}")
    notes = _path_notes(f, f.tr("I ricavi", "Revenue"), hist_s, cons_s, d, uw, *miss)
    return _done(key, title, t["rp_sub"].format(scale=scale, cur=cur), path,
                 _source(language, (t["src_fin"], history), (t["src_cons"], facts.get("consensus"))), H_MID, notes)


# ------------------------------------------------------------------ 9. allocazione del capitale
def _capital_allocation(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "capital_allocation"
    title = t["ca_title"]
    history, miss = _history(facts, key, title, t)
    if miss:
        return miss
    unit = history.get("unit") or facts.get("currency")
    fields = ("cfo", "capex", "dividends_paid", "buyback")
    rows = _rows(history, fields)
    if not rows:
        return _missing(key, title, _rows_missing(history, fields, 1, 0, t, language))
    years = [y for y, _ in rows]
    cfo = [v[0] / 1e6 for _, v in rows]  # col suo segno: un flusso operativo negativo resta negativo
    capex, div, bb = ([abs(v[i]) / 1e6 for _, v in rows] for i in (1, 2, 3))
    d = _dec(cfo + capex + div + bb)
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, H_MAIN), dpi=DPI)
        _grouped(fig, a, years, [(cfo, G2, t["cfo"]), (capex, G4, t["capex"]),
                                 (div, NAVY, t["div"]), (bb, G1, t["bb"])], language, unit, d, .2)
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)

    mw = f.tr(f" milioni di {unit}", f" {unit} mn")
    sc, sk, sd, sb = sum(cfo), sum(capex), sum(div), sum(bb)
    span = f.tr(f"Tra il {years[0]} e il {years[-1]} ({len(years)} esercizi)", f"Between {years[0]} and {years[-1]} ({len(years)} fiscal years)") \
        if len(years) > 1 else f.tr(f"Nel {years[0]}", f"In {years[0]}")
    notes = [f.tr(f"{span} il flusso operativo cumulato è {f.n(sc, d)}{mw}.",
                  f"{span} cumulative operating cash flow is {f.n(sc, d)}{mw}.")]
    if sc > 0:
        notes.append(f.tr(f"Il capex cumulato è {f.n(sk, d)}, {f.il(f.pct(sk / sc * 100))} del flusso operativo.",
                          f"Cumulative capex is {f.n(sk, d)}, {f.pct(sk / sc * 100)} of operating cash flow."))
    else:
        notes.append(f.tr(f"Il capex cumulato è {f.n(sk, d)} a fronte di un flusso operativo non positivo.",
                          f"Cumulative capex is {f.n(sk, d)} against a non-positive operating cash flow."))
    free = sc - sk
    if free > 0:
        notes.append(f.tr(f"Dividendi {f.n(sd, d)} e buyback {f.n(sb, d)}: insieme {f.n(sd + sb, d)}, {f.il(f.pct((sd + sb) / free * 100))} del flusso operativo meno capex ({f.n(free, d)}).",
                          f"Dividends {f.n(sd, d)} and buybacks {f.n(sb, d)}: together {f.n(sd + sb, d)}, {f.pct((sd + sb) / free * 100)} of operating cash flow minus capex ({f.n(free, d)})."))
    else:
        notes.append(f.tr(f"Dividendi {f.n(sd, d)} e buyback {f.n(sb, d)}, mentre il flusso operativo meno capex è {f.n(free, d)} (non positivo).",
                          f"Dividends {f.n(sd, d)} and buybacks {f.n(sb, d)}, while operating cash flow minus capex is {f.n(free, d)} (not positive)."))
    zero_bb = [y for y, v in zip(years, bb) if v == 0]
    if zero_bb:
        notes.append(f.tr(f"Buyback pari a zero nel {f.join(zero_bb)}.", f"Buybacks equal zero in {f.join(zero_bb)}."))
    return _done(key, title, t["ca_sub"].format(unit=unit), path, _source(language, (t["src_fin"], history)), H_MAIN, notes)


# ------------------------------------------------------------------ 10. raccomandazioni
def _reco_counts(block):
    if not isinstance(block, dict):
        return None, list(RECO_KEYS)
    absent = [k for k in RECO_KEYS if not _ok(block.get(k)) or block.get(k) < 0]
    return ([float(block[k]) for k in RECO_KEYS] if not absent else None), absent


def _recommendations(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "recommendations"
    title = t["rec_title"]
    cons = facts.get("consensus")
    if not isinstance(cons, dict):
        return _missing(key, title, t["miss_block"].format(block=_NAMES[language]["consensus"]))
    cur, absent = _reco_counts(cons.get("recommendations"))
    if cur is None:
        return _missing(key, title, t["miss_reco"].format(fields=_F(language).join(RECO_NAMES[k] for k in absent)))
    if sum(cur) <= 0:
        return _missing(key, title, t["miss_reco_zero"])
    prev_block = cons.get("recommendations_prev")
    prev, prev_absent = _reco_counts(prev_block) if prev_block is not None else (None, [])
    names = ["Strong buy", "Buy", "Hold", "Sell", "Strong sell"]
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, H_SMALL if prev is None else H_MAIN), dpi=DPI)
        y = list(range(5))[::-1]
        hmax = max(cur + (prev or []))
        a.set_xlim(0, hmax * 1.15 if hmax > 0 else 1)
        if prev is None:
            bars = a.barh(y, cur, color=NAVY, height=.6)
            _bar_labels(a, bars, cur, [f.n(v) for v in cur], 8, horizontal=True)
        else:
            h = .36
            b1 = a.barh([v + h / 2 for v in y], cur, height=h, color=NAVY, label=t["current"])
            b2 = a.barh([v - h / 2 for v in y], prev, height=h, color=G4, label=t["previous"])
            _bar_labels(a, b1, cur, [f.n(v) for v in cur], 8, horizontal=True)
            _bar_labels(a, b2, prev, [f.n(v) for v in prev], 7.5, NOTE, horizontal=True)
            _legend_top(a, 2)
        a.set_yticks(y, names)
        a.tick_params(axis="y", length=0, labelsize=8.5)
        a.set_xticks([])
        a.spines[["top", "right", "bottom"]].set_visible(False)
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)

    tot = sum(cur)
    pos, neu, neg = cur[0] + cur[1], cur[2], cur[3] + cur[4]
    notes = [f.tr(f"{f.n(tot)} raccomandazioni in tutto: positive {f.n(pos)} ({f.pct(pos / tot * 100)}), neutrali {f.n(neu)} ({f.pct(neu / tot * 100)}), negative {f.n(neg)} ({f.pct(neg / tot * 100)}).",
                  f"{f.n(tot)} recommendations in total: positive {f.n(pos)} ({f.pct(pos / tot * 100)}), neutral {f.n(neu)} ({f.pct(neu / tot * 100)}), negative {f.n(neg)} ({f.pct(neg / tot * 100)}).")]
    top = max(cur)
    modes = [nm for nm, v in zip(names, cur) if v == top]
    notes.append(f.tr(f"Classe più frequente: {f.join(modes)} ({f.n(top)}).", f"Most frequent rating: {f.join(modes)} ({f.n(top)})."))
    if prev is not None and sum(prev) > 0:
        ppos = prev[0] + prev[1]
        notes.append(f.tr(f"Nella rilevazione precedente le positive erano {f.n(ppos)} su {f.n(sum(prev))} ({f.pct(ppos / sum(prev) * 100)}): ora {f.n(pos)} su {f.n(tot)}.",
                          f"In the previous reading positive ratings were {f.n(ppos)} of {f.n(sum(prev))} ({f.pct(ppos / sum(prev) * 100)}): now {f.n(pos)} of {f.n(tot)}."))
    elif prev_block is not None:
        notes.append(f.tr("Rilevazione precedente presente ma incompleta o a zero: non mostrata.",
                          "Previous reading present but incomplete or all zero: not shown."))
    sub = t["rec_sub"] if prev is None else t["rec_sub_prev"]
    return _done(key, title, sub, path, _source(language, (t["src_cons"], cons)), H_SMALL if prev is None else H_MAIN, notes)


# ------------------------------------------------------------------ 11. rendimenti
def return_colors(assets):
    """Colore di ogni barra dei rendimenti: candidato navy pieno, posizioni del book e benchmark
    grigio scuro, gli altri grigio chiaro (impianto M: niente arancio)."""
    return [NAVY if a.get("role") == "candidate" else (G2 if a.get("role") in ("holding", "book", "benchmark") else G4)
            for a in assets]


def _returns(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "returns"
    block = facts.get("returns")
    if not isinstance(block, dict):
        return _missing(key, t["ret_title"].format(window="n.d."), t["miss_block"].format(block=_NAMES[language]["returns"]))
    window = block.get("window")
    if not window:
        return _missing(key, t["ret_title"].format(window="n.d."), t["miss_window"])
    window = window_label(window, language)
    title = t["ret_title"].format(window=window)
    assets = [a for a in block.get("assets") or []
              if isinstance(a, dict) and _ok(a.get("return_pct")) and (a.get("label") or a.get("ticker"))]
    if len(assets) < 2:
        return _missing(key, title, t["miss_assets"].format(n=len(assets)))
    if not any(a.get("role") == "candidate" for a in assets):
        return _missing(key, title, t["miss_cand"])
    assets = sorted(assets, key=lambda a: float(a["return_pct"]), reverse=True)
    names = [str(a.get("label") or a.get("ticker")) for a in assets]
    vals = [float(a["return_pct"]) for a in assets]
    colors = return_colors(assets)
    n = len(assets)
    height = max(H_SMALL, .7 + .32 * n)
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, height), dpi=DPI)
        y = list(range(n))[::-1]
        lo, hi = min(min(vals), 0), max(max(vals), 0)
        span = (hi - lo) or 1.0
        a.set_xlim(lo - span * .22 if lo < 0 else 0, hi + span * .22 if hi > 0 else span * .05)
        bars = a.barh(y, vals, color=colors, height=.62)
        _bar_labels(a, bars, vals, [f.pct(v, 1, True) for v in vals], 8, horizontal=True)
        _zero_line(a, -1 if lo < 0 else 0, horizontal=True)
        a.set_yticks(y, names)
        a.tick_params(axis="y", length=0, labelsize=8.5)
        a.set_xticks([])
        a.spines[["top", "right", "bottom"]].set_visible(False)
        if lo < 0:
            a.spines["left"].set_visible(False)
        fig.tight_layout(pad=.4)
        path = _save(fig, out_dir, key)

    i_c = next(i for i, x in enumerate(assets) if x.get("role") == "candidate")
    cname, cval = names[i_c], vals[i_c]
    notes = [f.tr(f"Su {window} {cname} rende {f.pct(cval, 1, True)}, {i_c + 1}° su {n}.",
                  f"Over {window} {cname} returns {f.pct(cval, 1, True)}, ranked {i_c + 1} of {n}."),
             f.tr(f"Migliore: {names[0]} ({f.pct(vals[0], 1, True)}); peggiore: {names[-1]} ({f.pct(vals[-1], 1, True)}).",
                  f"Best: {names[0]} ({f.pct(vals[0], 1, True)}); worst: {names[-1]} ({f.pct(vals[-1], 1, True)}).")]
    for j, x in enumerate(assets):
        if x.get("role") == "benchmark":
            notes.append(f.tr(f"Scarto contro {names[j]}, benchmark: {f.pp(cval, vals[j])}.",
                              f"Gap against {names[j]}, benchmark: {f.pp(cval, vals[j])}."))
    for j, x in enumerate(assets):  # «holding» = una posizione del portafoglio; «book» e' il nome vecchio
        if x.get("role") in ("holding", "book"):
            notes.append(f.tr(f"Scarto contro {names[j]}, posizione del book: {f.pp(cval, vals[j])}.",
                              f"Gap against {names[j]}, a book position: {f.pp(cval, vals[j])}."))
    return _done(key, title, t["ret_sub"], path, _source(language, (t["src_ret"], block)), height, notes)


# ------------------------------------------------------------------ 12. rischio di coda
_TAIL = (("var99_1d_pct", "var1d", G3), ("var99_1w_pct", "var1w", G1), ("es99_1w_pct", "es", NAVY))


def _tail_risk(facts, out_dir, language, ticker_label):
    t, f = _T[language], _F(language)
    key = "tail_risk"
    name = _short_name(ticker_label, t)
    title = t["tail_title"].format(name=name)
    risk = facts.get("risk")
    if not isinstance(risk, dict):
        return _missing(key, title, t["miss_block"].format(block=_NAMES[language]["risk"]))
    cand = risk.get("candidate")
    nm = _NAMES[language]
    absent = [f"{nm[k]} {nm['candidate']}" for k, *_ in _TAIL if not isinstance(cand, dict) or not _ok(cand.get(k))]
    if absent:
        return _missing(key, title, t["miss_fields"].format(fields=_F(language).join(absent)))
    vals = [abs(float(cand[k])) for k, *_ in _TAIL]
    with plt.rc_context(_RC):
        fig, a = plt.subplots(figsize=(WIDTH, H_SMALL), dpi=DPI)
        bars = a.bar([t[lab] for _, lab, _ in _TAIL], vals, color=[c for *_, c in _TAIL], width=.45, zorder=2)
        lo, hi = _limits(vals, head=.22)
        a.set_ylim(lo, hi)
        _bar_labels(a, bars, vals, [f.pct(v) for v in vals], 8)
        a.set_yticks([])
        a.spines[["top", "right", "left"]].set_visible(False)
        a.tick_params(axis="x", labelsize=8.5, length=0)
        fig.text(.5, .01, t["tail_note"], ha="center", fontsize=7.5, color=NOTE)
        fig.tight_layout(pad=.4, rect=(0, .05, 1, 1))
        path = _save(fig, out_dir, key)

    v1, vw, es = vals
    notes = [f.tr(f"VaR 99% di {name} in valore assoluto: {f.pct(v1)} in un giorno e {f.pct(vw)} in una settimana.",
                  f"{name} 99% VaR in absolute value: {f.pct(v1)} over one day and {f.pct(vw)} over one week.")]
    if v1 > 0:
        notes.append(f.tr(f"Il VaR settimanale è {f.n(vw / v1, 2)} volte quello giornaliero (la regola della radice del tempo su 5 sedute darebbe {f.n(math.sqrt(5), 2)}).",
                          f"Weekly VaR is {f.n(vw / v1, 2)} times the daily one (the square-root-of-time rule over 5 sessions would give {f.n(math.sqrt(5), 2)})."))
    if vw > 0:
        notes.append(f.tr(f"L'ES 99% a una settimana (perdita media oltre il VaR) vale {f.pct(es)}, {f.n(es / vw, 2)} volte il VaR 99% a una settimana.",
                          f"1-week 99% ES (average loss beyond VaR) is {f.pct(es)}, {f.n(es / vw, 2)} times the 1-week 99% VaR."))
    else:
        notes.append(f.tr(f"L'ES 99% a una settimana vale {f.pct(es)}.", f"1-week 99% ES is {f.pct(es)}."))
    return _done(key, title, t["tail_sub"], path, _source(language, (t["src_quant"], risk)), H_SMALL, notes)


# ------------------------------------------------------------------ API
_BUILDERS = (_revenue_margin, _cash, _price_targets, _risk, _balance, _margins, _eps_path,
             _revenue_path, _capital_allocation, _recommendations, _returns, _tail_risk)


def build_charts(facts, out_dir, *, language="it", ticker=""):
    """Disegna i grafici in ``out_dir`` (``ti_<key>.png``, DPI 220), ordine fisso ``KEYS``.

    Grafico prodotto: ``{"key","title","subtitle","path","source","aspect","notes"}``.
    Dati incompleti: ``{"key","title","missing"}`` e l'eventuale PNG vecchio viene tolto.
    """
    language = validate_language(language)
    facts = facts if isinstance(facts, dict) else {}
    os.makedirs(out_dir, exist_ok=True)
    out = [build(facts, out_dir, language, ticker) for build in _BUILDERS]
    for item in out:
        if "missing" in item:
            stale = os.path.join(out_dir, f"ti_{item['key']}.png")
            if os.path.exists(stale):
                os.remove(stale)
    return out
