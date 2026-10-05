"""trade_idea_market_charts.py - Figure di mercato del memo Trade Idea, impianto M (Lotto 3, L3, Opus 5.5).

Otto figure PNG dalle statistiche PURE di ``trade_idea_market_stats.market_stats(pack)``
(il pacchetto di mercato della run, chiave ``run["market_pack"]``, mai dentro ``facts``),
nello stesso stile di ``trade_idea_charts`` (solo navy + grigi, candidato navy pieno,
DPI 220, larghezza 160 mm), nell'ordine di ``MARKET_KEYS``:

1. ``price_history``    prezzo rettificato in base 100: candidato, benchmark, peer (EUR)
2. ``fat_tails``        istogramma dei log-rendimenti giornalieri con la normale + QQ-plot normale
3. ``vol_cone``         cono di volatilita' (min, 25-75 percentile, mediana, valore corrente)
4. ``drawdown``         drawdown dal massimo precedente, episodi peggiori annotati
5. ``acf_abs``          autocorrelazione di |r| e di r con banda di Bartlett
6. ``rolling_beta``     beta mobile contro il benchmark, beta dell'intero periodo tratteggiato
7. ``vix_sensitivity``  log-rendimento del titolo contro variazione del VIX, retta OLS
8. ``multiples``        TABELLA (scelta PM 04/10, non un PNG): multipli correnti di candidato e peer,
                        mediana dei peer in fondo con n e scarti

Contratto di ogni figura prodotta: ``{"key","title","subtitle","path","source","aspect",
"notes","checks"}`` (``multiples``: ``"table"`` = ``{"columns","rows","bold_rows","foot"}`` al posto di
``path``/``aspect``); figura non producibile: ``{"key","title","missing"}`` (regola PM 14/07:
blocco statistico ``insufficient``/``unavailable`` = nessun PNG, il motivo va nella nota sui
dati; il PNG vecchio della chiave viene tolto).

LETTURE PROVATE. Ogni numero di una lettura nasce da ``_Reading.num``/``_Reading.op``, che
registra in ``checks`` da QUALE campo delle statistiche viene (percorso + operazione +
decimali stampati). ``verify_readings(stats, readings)`` ritokenizza il testo della lettura
e pretende la corrispondenza uno-a-uno fra numeri stampati e valori riletti dalle stats:
un numero scritto a mano o calcolato fuori da ``_Reading`` resta senza controparte e fallisce.
Il controllo d'integrita' del PDF (``trade_idea_report``) usa la stessa verifica. Le letture
non contengono date (andrebbero provate anch'esse): le date stanno in sottotitoli e assi.
"""
import math
import os
import re
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import dates as mdates  # noqa: E402

from bellomberg.core.language import validate_language  # noqa: E402
from bellomberg.reporting.trade_idea_charts import (  # noqa: E402  stile M: importato, non copiato
    AXIS, G1, G2, G3, G4, GRID, H_MAIN, H_MID, H_SMALL, INK, NAVY, NOTE, WIDTH, _RC, _F,
    _date_label, _legend_top, _provider, _save, _style, fmt_number)

MARKET_KEYS = ("price_history", "fat_tails", "vol_cone", "drawdown", "acf_abs", "rolling_beta",
               "vix_sensitivity", "multiples")
P_FLOOR = 0.001          # sotto questa soglia il p-value si stampa «inferiore a 0,001»
SIGNIFICANCE_PCT = 5     # livello dei test dichiarato nelle letture
_MULTIPLES = (("ev_sales", "EV/Sales"), ("ev_ebitda", "EV/EBITDA"), ("pe_trailing", "P/E trailing"))
_MULTIPLES_TABLE = _MULTIPLES + (("pe_forward", "P/E forward"),)

_T = {
    "it": {
        "candidate": "Candidato",
        "ph_title": "Prezzo in base 100: candidato, benchmark e peer",
        "ph_sub": "Candidato e peer: prezzi rettificati per dividendi e frazionamenti; benchmark: {bidx}; in {ccy}, base 100 al {base}; {n} osservazioni del candidato, {start}–{end}{conv}",
        "idx_price": "indice di prezzo, senza dividendi", "idx_tr": "indice total return, dividendi reinvestiti",
        "idx_nd": "tipo di indice non dichiarato",
        "ft_title": "{name}: code dei rendimenti giornalieri",
        "ft_sub": "Log-rendimenti giornalieri in {ccy}, {n} osservazioni, {start}–{end}; a sinistra istogramma con la normale di pari media e deviazione standard, a destra QQ-plot normale",
        "ft_hist": "Istogramma", "ft_qq": "QQ-plot normale", "ft_normal": "Normale",
        "ft_xq": "quantili della normale standard", "ft_yq": "rendimento %",
        "vc_title": "{name}: cono di volatilità",
        "vc_sub": "Volatilità annualizzata su finestre mobili (rendimenti semplici giornalieri in {ccy}, std × radice di 252), {start}–{end}; barra 25°–75° percentile, trattino mediana, linea minimo–massimo, punto = valore corrente",
        "vc_x": "finestra in sedute", "vc_cur": "Corrente", "vc_med": "Mediana",
        "dd_title": "{name}: drawdown dal massimo precedente",
        "dd_sub": "Prezzi rettificati in {ccy}, {n} osservazioni, {start}–{end}; durate in sedute di borsa",
        "acf_title": "{name}: memoria della volatilità",
        "acf_sub": "Autocorrelazione dei log-rendimenti giornalieri in {ccy} (|r| a sinistra, r a destra), ritardi 1–{L}, {n} osservazioni, {start}–{end}; area grigia = banda ±1,96 / radice di T",
        "acf_abs": "|r|", "acf_r": "r", "acf_lag": "ritardo (sedute)",
        "rb_title": "{name}: beta mobile contro {bench}",
        "rb_sub": "Beta mobile su {w} {unit} di rendimenti semplici {freq} in {ccy} alle date comuni ({n} rendimenti comuni, {start}–{end}); tratteggio = beta dell'intero periodo{conv}",
        "rb_freq_w": "settimanali (ultima chiusura comune della settimana)", "rb_freq_d": "giornalieri",
        "rb_roll": "Beta mobile", "rb_full": "Beta intero periodo",
        "vx_title": "{name}: sensibilità al VIX",
        "vx_sub": "Log-rendimento giornaliero del titolo (%, {ccy}) contro variazione del VIX nello stesso giorno (punti, livello non convertito); {n} date comuni consecutive, {start}–{end}; retta OLS",
        "vx_x": "variazione del VIX (punti)", "vx_y": "rendimento del titolo %",
        "mu_title": "{name}: multipli contro i peer",
        "mu_sub": "Multipli correnti per titolo (volte); candidato in grassetto, mediana dei peer in fondo{hist}",
        "mu_col": "Titolo", "mu_med_row": "Mediana dei peer", "mu_n_row": "Peer nella mediana (esclusi)",
        "mu_foot": "Mediana sui soli peer con valore positivo; tra parentesi i peer esclusi perché con valore non positivo o non valido. P/E trailing e forward su basi separate.",
        "mu_off": " {ticker}: n.d., {why}.", "nd": "n.d.",
        "mu_hist": "; storia del candidato con approssimazioni: {appr}",
        "src_prices": "Prezzi giornalieri {prov}",
        "src_calc": "calcoli Bellomberg",
        "src_mult": "Multipli {prov}",
        "src_nd": "fonte n.d.",
        "conv": "; {ticker} convertito da {frm} al cambio dello stesso giorno ({fx}, senza riempimenti)",
        "conv_no": "; {ticker} non convertito ({ccy})",
        "miss_block": "{why}",
        "miss_no_block": "Statistiche non calcolate per questa figura.",
        "miss_no_ok": "Nessuna finestra calcolabile.",
        "miss_no_cand_row": "Multipli del candidato non disponibili.",
        "miss_no_median": "Nessun multiplo confrontabile fra candidato e mediana dei peer.",
        "miss_series": "Serie del grafico assenti dalle statistiche.",
    },
    "en": {
        "candidate": "Candidate",
        "ph_title": "Price rebased to 100: candidate, benchmark and peers",
        "ph_sub": "Candidate and peers: prices adjusted for dividends and splits; benchmark: {bidx}; in {ccy}, rebased to 100 on {base}; {n} candidate observations, {start}–{end}{conv}",
        "idx_price": "price index, no dividends", "idx_tr": "total return index, dividends reinvested",
        "idx_nd": "index type not stated",
        "ft_title": "{name}: tails of daily returns",
        "ft_sub": "Daily log returns in {ccy}, {n} observations, {start}–{end}; left, histogram with the normal of equal mean and standard deviation; right, normal QQ plot",
        "ft_hist": "Histogram", "ft_qq": "Normal QQ plot", "ft_normal": "Normal",
        "ft_xq": "standard normal quantiles", "ft_yq": "return %",
        "vc_title": "{name}: volatility cone",
        "vc_sub": "Annualised volatility over rolling windows (simple daily returns in {ccy}, std × square root of 252), {start}–{end}; bar 25th–75th percentile, tick median, line min–max, dot = current value",
        "vc_x": "window in sessions", "vc_cur": "Current", "vc_med": "Median",
        "dd_title": "{name}: drawdown from the previous peak",
        "dd_sub": "Adjusted prices in {ccy}, {n} observations, {start}–{end}; durations in trading sessions",
        "acf_title": "{name}: volatility memory",
        "acf_sub": "Autocorrelation of daily log returns in {ccy} (|r| left, r right), lags 1–{L}, {n} observations, {start}–{end}; grey area = ±1.96 / square root of T band",
        "acf_abs": "|r|", "acf_r": "r", "acf_lag": "lag (sessions)",
        "rb_title": "{name}: rolling beta against {bench}",
        "rb_sub": "Rolling beta over {w} {unit} of simple {freq} returns in {ccy} on common dates ({n} common returns, {start}–{end}); dashed = full-period beta{conv}",
        "rb_freq_w": "weekly (last common close of the week)", "rb_freq_d": "daily",
        "rb_roll": "Rolling beta", "rb_full": "Full-period beta",
        "vx_title": "{name}: sensitivity to the VIX",
        "vx_sub": "Daily log return of the stock (%, {ccy}) against same-day VIX change (points, level not converted); {n} consecutive common dates, {start}–{end}; OLS line",
        "vx_x": "VIX change (points)", "vx_y": "stock return %",
        "mu_title": "{name}: multiples against peers",
        "mu_sub": "Current multiples per stock (times); candidate in bold, peer median at the bottom{hist}",
        "mu_col": "Stock", "mu_med_row": "Peer median", "mu_n_row": "Peers in the median (excluded)",
        "mu_foot": "Median over peers with a positive value only; in brackets the peers excluded for a non-positive or invalid value. Trailing and forward P/E on separate bases.",
        "mu_off": " {ticker}: n/a, {why}.", "nd": "n/a",
        "mu_hist": "; candidate history with approximations: {appr}",
        "src_prices": "Daily prices {prov}",
        "src_calc": "Bellomberg calculations",
        "src_mult": "Multiples {prov}",
        "src_nd": "source n/a",
        "conv": "; {ticker} converted from {frm} at the same-day rate ({fx}, no filling)",
        "conv_no": "; {ticker} not converted ({ccy})",
        "miss_block": "{why}",
        "miss_no_block": "Statistics not computed for this figure.",
        "miss_no_ok": "No computable window.",
        "miss_no_cand_row": "Candidate multiples not available.",
        "miss_no_median": "No multiple comparable between the candidate and the peer median.",
        "miss_series": "Chart series missing from the statistics.",
    },
}


# ------------------------------------------------------------------ percorsi nelle statistiche
def _ok(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def resolve(stats, path):
    """Valore di ``stats`` lungo ``path`` (tupla): str = chiave, int = indice di lista,
    dict = selettore ``{campo: valore}`` sulla prima voce di lista che lo soddisfa."""
    node = stats
    for seg in path:
        if isinstance(seg, dict):
            if not isinstance(node, list):
                raise KeyError(path)
            node = next((x for x in node if isinstance(x, dict) and all(x.get(k) == v for k, v in seg.items())), None)
            if node is None:
                raise KeyError(path)
        elif isinstance(seg, int):
            node = node[seg]
        else:
            if not isinstance(node, dict) or seg not in node:
                raise KeyError(path)
            node = node[seg]
    return node


def _round(value, decimals):
    return float(fmt_number(value, decimals, "en").replace(",", ""))


def check_value(stats, check):
    """Valore FIRMATO di un controllo, riletto dalle statistiche (nessun numero dalla lettura).
    ``abs_pctchg`` e' l'unica operazione stampata senza segno: il segno lo porta la parola
    (premio/sconto), verificata da ``_label_problem``."""
    op, args, d = check["op"], check["args"], check["decimals"]
    if op == "get":
        return float(resolve(stats, args[0]))
    if op == "len":
        return float(len(resolve(stats, args[0])))
    if op == "const":
        return float(args[0])
    a, b = float(resolve(stats, args[0])), float(resolve(stats, args[1]))
    if op == "diff":   # sui valori MOSTRATI, come _F.pp
        return _round(a, d) - _round(b, d)
    if op == "pctchg":
        return (a / b - 1.0) * 100.0
    if op == "abs_pctchg":
        return abs((a / b - 1.0) * 100.0)
    raise ValueError(f"operazione di controllo sconosciuta: {op}")


def _raw_sign(stats, check):
    a, b = float(resolve(stats, check["args"][0])), float(resolve(stats, check["args"][1]))
    return (a / b - 1.0)


# Etichetta che DEVE precedere il numero di questi campi (oracolo indipendente dalle frasi):
# minimo/massimo scambiati o un'etichetta sbagliata sono un problema. Chiave: segmenti testuali del percorso.
FIELD_LABELS = {
    ("rolling_beta", "min"): ("minimo", "low"), ("rolling_beta", "max"): ("massimo", "high"),
    ("rolling_beta", "last"): ("ultimo", "last"),
    ("vol_cone", "windows", "min_pct"): ("minimo", "low"), ("vol_cone", "windows", "max_pct"): ("massimo", "high"),
    ("vol_cone", "windows", "p50_pct"): ("mediana storica", "historical median"),
    ("drawdown", "max_pct"): ("drawdown massimo", "maximum drawdown"),
    ("drawdown", "current", "current_dd_pct"): ("drawdown corrente", "current drawdown"),
    ("beta", "beta"): ("periodo", "beta"), ("beta", "correlation"): ("correlazione", "correlation"),
}
_CONNECTORS = ("di", "del", "dello", "della", "a", "al", "è", "of", "at", "is", "to")
_MARK = re.compile("\x00(\\d+)\x00")


class _Reading:
    """Frasi di una lettura: ogni numero passa di qui e lascia il suo controllo.

    I numeri escono come SEGNAPOSTO; ``place`` li sostituisce nel testo finale e registra
    dove sta ogni numero (testo, posizione): la verifica pretende il valore firmato
    esattamente in quella posizione, con l'etichetta del campo davanti quando prevista."""

    def __init__(self, stats, language):
        self.stats, self.f, self.checks = stats, _F(language), []

    def _fmt(self, value, d, signed, pct):
        s = fmt_number(value, d, self.f.lang)
        if signed and value > 0 and s.strip("0,."):
            s = "+" + s
        return s + ("%" if pct else "")

    def _mark(self, check, text):
        check["text"] = text
        self.checks.append(check)
        return f"\x00{len(self.checks) - 1}\x00"

    def op(self, op, args, d=0, signed=False, pct=False):
        check = {"op": op, "args": args, "decimals": d}
        return self._mark(check, self._fmt(check_value(self.stats, check), d, signed, pct))

    def num(self, path, d=0, signed=False, pct=False):
        return self.op("get", (path,), d, signed, pct)

    def year(self, path):
        """Anno senza separatore delle migliaia («2024», non «2.024»)."""
        check = {"op": "get", "args": (path,), "decimals": 0, "year": True}
        return self._mark(check, str(int(round(check_value(self.stats, check)))))

    def p_value(self, path):
        p = float(resolve(self.stats, path))
        if p < P_FLOOR:
            return self.f.tr("inferiore a ", "below ") + self.op("const", (P_FLOOR,), 3)
        return self.num(path, 3)

    def tr(self, it, en):
        """Frase nella lingua: gli argomenti sono lambda, cosi' si valuta (e lascia controlli)
        SOLO la frase stampata, mai quella dell'altra lingua."""
        chosen = self.f.tr(it, en)
        return chosen() if callable(chosen) else chosen

    def place(self, text, where):
        """Testo finale (segnaposto -> numeri) e posizione di ogni numero in ``where``."""
        out, pos = [], 0
        for m in _MARK.finditer(text):
            out.append(text[pos:m.start()])
            check = self.checks[int(m.group(1))]
            check["at"] = {**where, "offset": sum(len(x) for x in out)}
            out.append(check["text"])
            pos = m.end()
        out.append(text[pos:])
        return "".join(out)


# ------------------------------------------------------------------ verifica delle letture
_TOKEN = {"it": (r"(?<![\w.,])[+\-−]?(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)", ".", ","),
          "en": (r"(?<![\w.,])[+\-−]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)", ",", ".")}


def reading_labels(stats, ticker=""):
    """Ticker completi stampati nelle letture e nelle tabelle (possono contenere cifre): solo le loro
    occorrenze INTERE si escludono dalla verifica, mai un numero uguale fuori dal ticker."""
    names = {str(ticker or "")}
    for role in ("candidate", "benchmark", "vix"):
        names.add(str(((stats.get("series") or {}).get(role) or {}).get("ticker") or ""))
    for block, key in (("peers", None), ("price_history", "lines"), ("multiples", "rows")):
        rows = stats.get(block)
        rows = (rows or {}).get(key) if key and isinstance(rows, dict) else rows
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                names.add(str(row.get("ticker") or ""))
    for block in ("rolling_beta", "beta"):
        names.add(str((stats.get(block) or {}).get("benchmark") or ""))
    names.add(str((stats.get("vix_sensitivity") or {}).get("vix") or ""))
    names.discard("")
    return sorted(names, key=len, reverse=True)


def number_tokens(text, language, labels=()):
    """[(posizione, valore FIRMATO, decimali)] dei numeri del testo; esclusi i numeri dentro
    un'occorrenza intera di un ticker (confini di parola, non sottostringhe)."""
    spans = [m.span() for name in labels if name
             for m in re.finditer(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", text)]
    pattern, group, point = _TOKEN[validate_language(language)]
    out = []
    for m in re.finditer(pattern, text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        raw = m.group(0)
        sign = -1.0 if raw[0] in "-−" else 1.0
        clean = raw.lstrip("+-−").replace(group, "")
        out.append((m.start(), sign * float(clean.replace(point, ".")), len(clean.partition(point)[2])))
    return out


def _label_problem(stats, check, text, language):
    """Frase del problema se l'etichetta davanti al numero non e' quella del campo, altrimenti None."""
    i = 0 if language == "it" else 1
    if check["op"] == "abs_pctchg":
        sign = _raw_sign(stats, check)
        want = (("premio", "premium") if sign > 0 else ("sconto", "discount"))[i]
    elif check["op"] == "get":
        pair = FIELD_LABELS.get(tuple(seg for seg in check["args"][0] if isinstance(seg, str)))
        if not pair:
            return None
        want = pair[i]
    else:
        return None
    words = text[:check["at"]["offset"]].rstrip(" (").casefold().split()
    while words and words[-1] in _CONNECTORS:
        words.pop()
    if not " ".join(words).endswith(want.casefold()):
        return f"etichetta «{want}» assente davanti al numero di {check['op']}{check['args']}"
    return None


def _texts(item):
    notes = list(item.get("notes") or [])
    rows = (item.get("table") or {}).get("rows") or []
    return notes, rows


def verify_readings(stats, readings, language, ticker=""):
    """Problemi (lista di frasi) fra i numeri stampati nelle letture/tabelle e le statistiche.

    Ogni controllo deve trovare, ESATTAMENTE nella posizione registrata da ``_Reading.place``,
    un numero con gli stessi decimali, lo stesso SEGNO ed entro mezza unita' dell'ultima cifra
    dal valore riletto dalle stats; per i campi di ``FIELD_LABELS`` (e premio/sconto) anche
    l'etichetta giusta davanti. Ogni numero stampato deve appartenere a un controllo.
    Vuota = letture provate.
    """
    labels = reading_labels(stats, ticker)
    problems = []
    for item in readings:
        if item.get("missing"):
            continue
        key = item["key"]
        notes, rows = _texts(item)
        tokens = {}
        for i, text in enumerate(notes):
            for pos, v, d in number_tokens(text, language, labels):
                tokens[("notes", i, pos)] = (v, d)
        for r_, row in enumerate(rows):
            for c_, text in enumerate(row):
                for pos, v, d in number_tokens(text, language, labels):
                    tokens[("cells", (r_, c_), pos)] = (v, d)
        used = set()
        for check in item.get("checks") or []:
            at = check.get("at")
            if not at:
                problems.append(f"{key}: valore {check['op']}{check['args']} non stampato")
                continue
            loc = (at["field"], at["index"] if at["field"] == "notes" else tuple(at["index"]), at["offset"])
            try:
                value = check_value(stats, check)
            except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
                problems.append(f"{key}: controllo non rileggibile {check['op']}{check['args']}")
                continue
            d = check["decimals"]
            hit = tokens.get(loc)
            if hit is None or loc in used:
                problems.append(f"{key}: valore {check['op']}{check['args']} non stampato nella sua posizione")
                continue
            v, dec = hit
            if dec != d or abs(v - value) > .5 * 10 ** -d + 1e-9:
                problems.append(f"{key}: stampato {v} al posto di {check['op']}{check['args']} = {round(value, d + 1)}")
            used.add(loc)
            text = notes[loc[1]] if loc[0] == "notes" else rows[loc[1][0]][loc[1][1]]
            label = _label_problem(stats, check, text, language)
            if label:
                problems.append(f"{key}: {label}")
        for loc, (v, _) in tokens.items():
            if loc not in used:
                problems.append(f"{key}: numero {v} senza controparte nelle statistiche")
    return problems


# ------------------------------------------------------------------ utilita'
def _short(ticker):
    return (str(ticker or "")).split(".")[0][:8]


def _name(stats, ticker, t):
    tk = ticker or ((stats.get("series") or {}).get("candidate") or {}).get("ticker")
    return _short(tk) or t["candidate"]


def _d(value):
    return date.fromisoformat(str(value)[:10])


def _block(stats, key, t):
    """(blocco, None) se ok; (None, motivo dichiarato) se insufficient/unavailable/assente."""
    block = stats.get(key)
    if not isinstance(block, dict):
        return None, t["miss_no_block"]
    if block.get("status") != "ok":
        return None, str(block.get("reason") or t["miss_no_block"])
    return block, None


def _source(stats, pack, language, extra=""):
    t = _T[language]
    prov = _provider((pack or {}).get("provider")) if isinstance(pack, dict) else None
    when = _date_label((pack or {}).get("as_of") if isinstance(pack, dict) else None) or \
        _date_label(((stats.get("window") or {}).get("end")))
    parts = []
    if prov:
        parts.append(t["src_prices"].format(prov=prov) + (f", {when}" if when else ""))
    parts.append(t["src_calc"] if parts else t["src_calc"][:1].upper() + t["src_calc"][1:])
    if extra:
        parts.append(extra)
    return " · ".join(parts)


def _conv(stats, roles, language, extra_rows=()):
    """Conversioni in valuta base dichiarate per le serie nominate (testo per i sottotitoli)."""
    t = _T[language]
    base = stats.get("base_currency") or "EUR"
    rows = [((stats.get("series") or {}).get(r) or {}) for r in roles] + list(extra_rows)
    out = []
    for row in rows:
        conv = row.get("conversion") or {}
        if conv.get("method") == "fx_same_date":
            out.append(t["conv"].format(ticker=row.get("ticker"), frm=row.get("native_currency") or "n.d.",
                                        fx=conv.get("fx_ticker") or "n.d."))
        elif row.get("currency") and row.get("currency") != base and row.get("price_field") != "level":
            out.append(t["conv_no"].format(ticker=row.get("ticker"), ccy=row.get("currency")))
    return "".join(out)


def _foreign_listing(stats):
    """Candidato non quotato in USD nativo (valuta del listino dichiarata dal pacchetto)."""
    native = ((stats.get("series") or {}).get("candidate") or {}).get("native_currency")
    return bool(native) and native != "USD"


def _date_axis(ax):
    loc = mdates.AutoDateLocator(minticks=4, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc, show_offset=False))


def _x_numbers(ax, language):
    """Tick dell'asse x nella convenzione locale (niente meno tipografico di matplotlib)."""
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: fmt_number(v, 0 if float(v).is_integer() else 1, language)))


def _missing(key, title, reason):
    return {"key": key, "title": title, "missing": reason}


def _spec(key, title, subtitle, source, height, reading, notes, draw):
    notes = [reading.place(s, {"field": "notes", "index": i}) for i, s in enumerate(x for x in notes if x)]
    if len(notes) > 4:  # i controlli valgono per TUTTE le frasi: tagliarle li renderebbe falsi
        raise ValueError(f"{key}: piu' di 4 frasi nella lettura")
    return {"key": key, "title": title, "subtitle": subtitle, "source": source, "height": height,
            "notes": notes, "checks": reading.checks, "draw": draw}


# ------------------------------------------------------------------ 1. prezzo base 100
def _price_history(stats, pack, language, ticker):
    t = _T[language]
    key, title = "price_history", t["ph_title"]
    block, why = _block(stats, key, t)
    if why:
        return _missing(key, title, why)
    lines = [ln for ln in block.get("lines") or [] if isinstance(ln, dict)]
    ok = [ln for ln in lines if ln.get("status") == "ok" and ln.get("dates") and ln.get("index")]
    cand = next((ln for ln in ok if ln.get("role") == "candidate"), None)
    if cand is None:
        return _missing(key, title, t["miss_series"])
    r = _Reading(stats, language)
    sel = lambda ln: ("price_history", "lines", {"role": ln["role"], "ticker": ln["ticker"]})
    ccy = stats.get("base_currency") or "EUR"
    ctk = cand["ticker"]
    notes = [r.tr(lambda: f"Sul periodo {ctk} rende {r.num(sel(cand) + ('total_return_pct',), 1, True, True)} in {ccy} "
                  f"({r.num(sel(cand) + ('n_obs',))} osservazioni).",
                  lambda: f"Over the period {ctk} returns {r.num(sel(cand) + ('total_return_pct',), 1, True, True)} in {ccy} "
                  f"({r.num(sel(cand) + ('n_obs',))} observations).")]
    bench = next((ln for ln in ok if ln.get("role") == "benchmark"), None)
    if bench is not None:
        a, b = sel(cand) + ("total_return_pct",), sel(bench) + ("total_return_pct",)
        kind = bench.get("index_type")
        caveat_it = {"price": " (indice di prezzo: lo scarto favorisce il candidato, che include i dividendi)",
                     "total_return": " (entrambi con dividendi reinvestiti)"}.get(kind, " (tipo di indice del benchmark non dichiarato)")
        caveat_en = {"price": " (price index: the gap favours the candidate, whose return includes dividends)",
                     "total_return": " (both with dividends reinvested)"}.get(kind, " (benchmark index type not stated)")
        notes.append(r.tr(lambda: f"Benchmark {bench['ticker']}: {r.num(b, 1, True, True)}; scarto del candidato "
                          f"{r.op('diff', (a, b), 1, True)} punti percentuali{caveat_it}.",
                          lambda: f"Benchmark {bench['ticker']}: {r.num(b, 1, True, True)}; candidate gap "
                          f"{r.op('diff', (a, b), 1, True)} percentage points{caveat_en}."))
    peers_ok = [ln for ln in ok if ln.get("role") == "peer"]
    if peers_ok:
        parts = [f"{ln['ticker']} {r.num(sel(ln) + ('total_return_pct',), 1, True, True)}" for ln in peers_ok]
        notes.append(r.tr(lambda: "Peer: ", lambda: "Peers: ") + r.f.join(parts) + ".")
    off = [ln for ln in lines if ln.get("status") != "ok"]
    if off:
        notes.append(r.tr(lambda: "Non tracciati: ", lambda: "Not plotted: ") + r.f.join(
            f"{ln.get('ticker')} ({r.tr(lambda: 'storia insufficiente', lambda: 'insufficient history') if ln.get('status') == 'insufficient' else r.tr(lambda: 'non disponibile', lambda: 'not available')})"
            for ln in off) + ".")
    extra = [ln for ln in lines if ln.get("role") == "peer"]
    peer_rows = [p for p in stats.get("peers") or [] if isinstance(p, dict)
                 and any(p.get("ticker") == ln.get("ticker") and ln.get("status") == "ok" for ln in extra)]
    bkind = next((ln.get("index_type") for ln in lines if ln.get("role") == "benchmark"), None)
    sub = t["ph_sub"].format(bidx={"price": t["idx_price"], "total_return": t["idx_tr"]}.get(bkind, t["idx_nd"]),
                             ccy=ccy, base=_date_label(cand.get("base_date") or cand["dates"][0]),
                             n=fmt_number(cand.get("n_obs") or len(cand["index"]), 0, language),
                             start=_date_label(block.get("start")), end=_date_label(block.get("end")),
                             conv=_conv(stats, ("candidate", "benchmark"), language, peer_rows))

    def draw(out_dir):
        with plt.rc_context(_RC):
            fig, a = plt.subplots(figsize=(WIDTH, H_MAIN))
            greys = [G2, G3, G4, G1]
            for i, ln in enumerate([x for x in ok if x is not cand] + [cand]):
                xs = [_d(x) for x in ln["dates"]]
                ys = [v if _ok(v) else float("nan") for v in ln["index"]]
                role = ln.get("role")
                if ln is cand:
                    a.plot(xs, ys, color=NAVY, lw=1.3, label=ln["ticker"], zorder=4)
                elif role == "benchmark":
                    a.plot(xs, ys, color=G1, lw=.9, ls="--", label=f"{ln['ticker']} (benchmark)", zorder=3)
                else:
                    a.plot(xs, ys, color=greys[i % len(greys)], lw=.8, label=ln["ticker"], zorder=2)
            a.axhline(100, color=AXIS, lw=.5)
            _style(a, language)
            _date_axis(a)
            _legend_top(a, min(4, len(ok)))
            fig.tight_layout(pad=.4)
            return _save(fig, out_dir, key)
    return _spec(key, title, sub, _source(stats, pack, language), H_MAIN, r, notes, draw)


# ------------------------------------------------------------------ 2. code grasse
def _fat_tails(stats, pack, language, ticker):
    t = _T[language]
    name = _name(stats, ticker, t)
    key, title = "fat_tails", t["ft_title"].format(name=name)
    mom, why = _block(stats, "moments", t)
    hist, why_h = _block(stats, "histogram", t)
    qq, why_q = _block(stats, "qq", t)
    why = why or why_h or why_q
    if why:
        return _missing(key, title, why)
    if not hist.get("counts") or not qq.get("theoretical_z"):
        return _missing(key, title, t["miss_series"])
    r = _Reading(stats, language)
    dly = ("moments", "daily")
    notes = [r.tr(lambda: f"Curtosi in eccesso {r.num(dly + ('excess_kurtosis',), 2)} e asimmetria {r.num(dly + ('skew',), 2)} "
                  f"su {r.num(dly + ('n_obs',))} rendimenti giornalieri: una normale darebbe zero per entrambe.",
                  lambda: f"Excess kurtosis {r.num(dly + ('excess_kurtosis',), 2)} and skewness {r.num(dly + ('skew',), 2)} "
                  f"over {r.num(dly + ('n_obs',))} daily returns: a normal distribution would give zero for both."),
             r.tr(lambda: f"Rendimenti oltre tre deviazioni standard dalla media: {r.num(dly + ('beyond_3sd',))} contro "
                  f"{r.num(dly + ('beyond_3sd_normal_expected',), 1)} attesi con una normale.",
                  lambda: f"Returns beyond three standard deviations from the mean: {r.num(dly + ('beyond_3sd',))} against "
                  f"{r.num(dly + ('beyond_3sd_normal_expected',), 1)} expected under a normal.")]
    p = float(mom["daily"]["jb_p"])
    verdict = (r.tr(lambda: "normalità respinta al ", lambda: "normality rejected at the ") if p < SIGNIFICANCE_PCT / 100
               else r.tr(lambda: "normalità non respinta al ", lambda: "normality not rejected at the "))
    notes.append(r.tr(lambda: f"Jarque-Bera: statistica {r.num(dly + ('jb_stat',), 1)}, p-value {r.p_value(dly + ('jb_p',))}: "
                      f"{verdict}{r.op('const', (SIGNIFICANCE_PCT,), 0)}%.",
                      lambda: f"Jarque-Bera: statistic {r.num(dly + ('jb_stat',), 1)}, p-value {r.p_value(dly + ('jb_p',))}: "
                      f"{verdict}{r.op('const', (SIGNIFICANCE_PCT,), 0)}% level."))
    if isinstance(mom.get("weekly"), dict) and isinstance(mom.get("monthly"), dict):
        notes.append(r.tr(lambda: f"Curtosi in eccesso dei rendimenti settimanali {r.num(('moments', 'weekly', 'excess_kurtosis'), 2)} "
                          f"e mensili {r.num(('moments', 'monthly', 'excess_kurtosis'), 2)}.",
                          lambda: f"Excess kurtosis of weekly returns {r.num(('moments', 'weekly', 'excess_kurtosis'), 2)} "
                          f"and of monthly returns {r.num(('moments', 'monthly', 'excess_kurtosis'), 2)}."))
    sub = t["ft_sub"].format(ccy=stats.get("base_currency") or "EUR", n=fmt_number(mom["daily"]["n_obs"], 0, language),
                             start=_date_label(mom.get("start")), end=_date_label(mom.get("end")))

    def draw(out_dir):
        edges, counts = hist["edges_pct"], hist["counts"]
        n = float(sum(counts))
        mu, sd = float(hist["normal_mean_pct"]), float(hist["normal_std_pct"])
        with plt.rc_context(_RC):
            fig, (a, b) = plt.subplots(1, 2, figsize=(WIDTH, H_MAIN))
            widths = [edges[i + 1] - edges[i] for i in range(len(counts))]
            dens = [c / n / w if w > 0 else 0 for c, w in zip(counts, widths)]
            a.bar(edges[:-1], dens, width=widths, align="edge", color=G4, edgecolor="white", lw=.3)
            xs = [edges[0] + (edges[-1] - edges[0]) * i / 200 for i in range(201)]
            a.plot(xs, [math.exp(-.5 * ((x - mu) / sd) ** 2) / (sd * math.sqrt(2 * math.pi)) for x in xs],
                   color=NAVY, lw=1.1, label=t["ft_normal"])
            a.set_title(t["ft_hist"], loc="left")
            _style(a, language)
            a.set_yticks([])
            a.spines["left"].set_visible(False)
            a.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: fmt_number(v, 0, language) + "%"))
            b.scatter(qq["theoretical_z"], qq["sample_pct"], s=4, color=NAVY, lw=0)
            z = [min(qq["theoretical_z"]), max(qq["theoretical_z"])]
            b.plot(z, [qq["intercept_pct"] + qq["slope_pct"] * v for v in z], color=G2, lw=.8, ls="--")
            b.set_title(t["ft_qq"], loc="left")
            b.set_xlabel(t["ft_xq"], fontsize=7.5)
            b.set_ylabel(t["ft_yq"], fontsize=7.5)
            _style(b, language, "%")
            _x_numbers(b, language)
            fig.tight_layout(pad=.4)
            return _save(fig, out_dir, key)
    return _spec(key, title, sub, _source(stats, pack, language), H_MAIN, r, notes, draw)


# ------------------------------------------------------------------ 3. cono di volatilita'
def _vol_cone(stats, pack, language, ticker):
    t = _T[language]
    name = _name(stats, ticker, t)
    key, title = "vol_cone", t["vc_title"].format(name=name)
    block, why = _block(stats, "vol_cone", t)
    if why:
        return _missing(key, title, why)
    wins = [w for w in block.get("windows") or [] if isinstance(w, dict)]
    ok = [w for w in wins if w.get("status") == "ok"]
    if not ok:
        return _missing(key, title, t["miss_no_ok"])
    r = _Reading(stats, language)
    notes = []
    for w in ([ok[0], ok[-1]] if len(ok) > 1 else ok):
        p = ("vol_cone", "windows", {"window": w["window"]})
        notes.append(r.tr(lambda: f"A {r.num(p + ('window',))} sedute la volatilità corrente è {r.num(p + ('current_pct',), 1, pct=True)}, "
                          f"contro una mediana storica di {r.num(p + ('p50_pct',), 1, pct=True)} "
                          f"(minimo {r.num(p + ('min_pct',), 1, pct=True)}, massimo {r.num(p + ('max_pct',), 1, pct=True)}).",
                          lambda: f"Over {r.num(p + ('window',))} sessions current volatility is {r.num(p + ('current_pct',), 1, pct=True)}, "
                          f"against a historical median of {r.num(p + ('p50_pct',), 1, pct=True)} "
                          f"(low {r.num(p + ('min_pct',), 1, pct=True)}, high {r.num(p + ('max_pct',), 1, pct=True)})."))
    above = [w for w in ok if w["current_pct"] > w["p75_pct"]]
    below = [w for w in ok if w["current_pct"] < w["p25_pct"]]
    for group, q in ((above, 75), (below, 25)):
        if group and len(notes) < 4:
            wl = r.f.join(r.num(("vol_cone", "windows", {"window": w["window"]}, "window")) for w in group)
            qs = r.op("const", (q,), 0)
            side = r.tr(lambda: "sopra" if q == 75 else "sotto", lambda: "above" if q == 75 else "below")
            notes.append(r.tr(lambda: f"Valore corrente {side} il {qs}° percentile storico sulle finestre di {wl} sedute.",
                              lambda: f"Current value {side} the historical {qs}th percentile on the {wl}-session windows."))
    skipped = [w for w in wins if w.get("status") != "ok"]
    if skipped and len(notes) < 4:
        notes.append(r.tr(lambda: "Finestre non calcolate per storia insufficiente: ", lambda: "Windows not computed for insufficient history: ")
                     + r.f.join(r.num(("vol_cone", "windows", {"window": w["window"]}, "window")) for w in skipped)
                     + r.tr(lambda: " sedute.", lambda: " sessions."))
    sub = t["vc_sub"].format(ccy=stats.get("base_currency") or "EUR", start=_date_label(block.get("start")),
                             end=_date_label(block.get("end")))

    def draw(out_dir):
        with plt.rc_context(_RC):
            fig, a = plt.subplots(figsize=(WIDTH, H_MID))
            xs = list(range(len(ok)))
            for x, w in zip(xs, ok):
                a.plot([x, x], [w["min_pct"], w["max_pct"]], color=G3, lw=1, zorder=1)
                a.bar(x, w["p75_pct"] - w["p25_pct"], bottom=w["p25_pct"], width=.42, color=G4, zorder=2)
                a.plot([x - .21, x + .21], [w["p50_pct"]] * 2, color=G1, lw=1.2, zorder=3)
            a.scatter(xs, [w["current_pct"] for w in ok], color=NAVY, s=22, zorder=4, label=t["vc_cur"])
            a.plot([], [], color=G1, lw=1.2, label=t["vc_med"])
            a.set_xticks(xs, [fmt_number(w["window"], 0, language) for w in ok])
            a.set_xlabel(t["vc_x"], fontsize=7.5)
            _style(a, language, "%")
            _legend_top(a, 2)
            fig.tight_layout(pad=.4)
            return _save(fig, out_dir, key)
    return _spec(key, title, sub, _source(stats, pack, language), H_MID, r, notes, draw)


# ------------------------------------------------------------------ 4. drawdown
def _drawdown(stats, pack, language, ticker):
    t = _T[language]
    name = _name(stats, ticker, t)
    key, title = "drawdown", t["dd_title"].format(name=name)
    block, why = _block(stats, "drawdown", t)
    if why:
        return _missing(key, title, why)
    curve = block.get("curve") or {}
    if not curve.get("dates") or not curve.get("dd_pct"):
        return _missing(key, title, t["miss_series"])
    r = _Reading(stats, language)
    notes = []
    top = [e for e in block.get("top") or [] if isinstance(e, dict)]
    if top:
        e0 = ("drawdown", "top", 0)
        tail = (r.tr(lambda: f", recuperato in {r.num(e0 + ('days_total',))} sedute dal picco",
                     lambda: f", recovered {r.num(e0 + ('days_total',))} sessions after the peak")
                if not top[0].get("open") else
                r.tr(lambda: f", non ancora recuperato dopo {r.num(e0 + ('days_total',))} sedute",
                     lambda: f", not yet recovered after {r.num(e0 + ('days_total',))} sessions"))
        notes.append(r.tr(lambda: f"Drawdown massimo {r.num(('drawdown', 'max_pct'), 1, pct=True)}, toccato "
                          f"{r.num(e0 + ('days_to_trough',))} sedute dopo il picco{tail}.",
                          lambda: f"Maximum drawdown {r.num(('drawdown', 'max_pct'), 1, pct=True)}, reached "
                          f"{r.num(e0 + ('days_to_trough',))} sessions after the peak{tail}."))
    else:
        notes.append(r.tr(lambda: f"Drawdown massimo {r.num(('drawdown', 'max_pct'), 1, pct=True)}.",
                          lambda: f"Maximum drawdown {r.num(('drawdown', 'max_pct'), 1, pct=True)}."))
    if isinstance(block.get("current"), dict):
        notes.append(r.tr(lambda: f"Drawdown corrente {r.num(('drawdown', 'current', 'current_dd_pct'), 1, pct=True)}.",
                          lambda: f"Current drawdown {r.num(('drawdown', 'current', 'current_dd_pct'), 1, pct=True)}."))
    else:
        notes.append(r.tr(lambda: "Il titolo chiude sul massimo del periodo: nessun drawdown aperto.",
                          lambda: "The stock closes at its period high: no open drawdown."))
    worst = top[:3]
    if worst:
        notes.append(r.tr(lambda: f"Episodi nel periodo: {r.num(('drawdown', 'n_episodes_total'))}; i più profondi: ",
                          lambda: f"Episodes in the period: {r.num(('drawdown', 'n_episodes_total'))}; the deepest: ")
                     + r.f.join(r.num(("drawdown", "top", i, "depth_pct"), 1, pct=True) for i in range(len(worst))) + ".")
    sub = t["dd_sub"].format(ccy=stats.get("base_currency") or "EUR", n=fmt_number(block.get("n_obs") or len(curve["dates"]), 0, language),
                             start=_date_label(block.get("start")), end=_date_label(block.get("end")))

    def draw(out_dir):
        xs = [_d(x) for x in curve["dates"]]
        ys = [v if _ok(v) else float("nan") for v in curve["dd_pct"]]
        with plt.rc_context(_RC):
            fig, a = plt.subplots(figsize=(WIDTH, H_MID))
            a.fill_between(xs, ys, 0, color=G4, lw=0)
            a.plot(xs, ys, color=NAVY, lw=.8)
            for i, e in enumerate(worst, 1):
                if e.get("trough_date"):
                    a.annotate(f"{i}", (_d(e["trough_date"]), e["depth_pct"]), xytext=(0, -9),
                               textcoords="offset points", ha="center", fontsize=7.5, color=INK)
            a.set_ylim(min(v for v in ys if v == v) * 1.12, 0)
            _style(a, language, "%")
            _date_axis(a)
            fig.tight_layout(pad=.4)
            return _save(fig, out_dir, key)
    return _spec(key, title, sub, _source(stats, pack, language), H_MID, r, notes, draw)


# ------------------------------------------------------------------ 5. ACF di |r|
def _acf_abs(stats, pack, language, ticker):
    t = _T[language]
    name = _name(stats, ticker, t)
    key, title = "acf_abs", t["acf_title"].format(name=name)
    ab, why = _block(stats, "acf_abs", t)
    rr, why_r = _block(stats, "acf_returns", t)
    why = why or why_r
    if why:
        return _missing(key, title, why)
    if not ab.get("values") or not rr.get("values"):
        return _missing(key, title, t["miss_series"])
    r = _Reading(stats, language)
    lags = ("acf_abs", "lags")
    notes = [r.tr(lambda: f"Autocorrelazioni di |r| fuori dalla banda di ±{r.num(('acf_abs', 'band'), 3)}: "
                  f"{r.num(('acf_abs', 'n_outside_band'))} su {r.op('len', (lags,))} ritardi; dei rendimenti r: "
                  f"{r.num(('acf_returns', 'n_outside_band'))} su {r.op('len', (('acf_returns', 'lags'),))}.",
                  lambda: f"Autocorrelations of |r| outside the ±{r.num(('acf_abs', 'band'), 3)} band: "
                  f"{r.num(('acf_abs', 'n_outside_band'))} of {r.op('len', (lags,))} lags; for returns r: "
                  f"{r.num(('acf_returns', 'n_outside_band'))} of {r.op('len', (('acf_returns', 'lags'),))}."),
             r.tr(lambda: f"Primo ritardo: {r.num(('acf_abs', 'values', 0), 3)} per |r| e {r.num(('acf_returns', 'values', 0), 3)} per r.",
                  lambda: f"First lag: {r.num(('acf_abs', 'values', 0), 3)} for |r| and {r.num(('acf_returns', 'values', 0), 3)} for r.")]
    lb = [x for x in ab.get("ljung_box") or [] if isinstance(x, dict)]
    if lb:
        pick = next((x for x in lb if x.get("lag") == 10), lb[0])
        p = ("acf_abs", "ljung_box", {"lag": pick["lag"]})
        verdict = (r.tr(lambda: "dipendenza significativa al ", lambda: "significant dependence at the ") if pick["p"] < SIGNIFICANCE_PCT / 100
                   else r.tr(lambda: "dipendenza non significativa al ", lambda: "dependence not significant at the "))
        notes.append(r.tr(lambda: f"Ljung-Box su |r| a {r.num(p + ('lag',))} ritardi: statistica {r.num(p + ('stat',), 1)}, "
                          f"p-value {r.p_value(p + ('p',))}: {verdict}{r.op('const', (SIGNIFICANCE_PCT,), 0)}%.",
                          lambda: f"Ljung-Box on |r| at {r.num(p + ('lag',))} lags: statistic {r.num(p + ('stat',), 1)}, "
                          f"p-value {r.p_value(p + ('p',))}: {verdict}{r.op('const', (SIGNIFICANCE_PCT,), 0)}% level."))
    sub = t["acf_sub"].format(ccy=stats.get("base_currency") or "EUR", L=len(ab["lags"]),
                              n=fmt_number(ab.get("n_obs") or 0, 0, language),
                              start=_date_label(ab.get("start")), end=_date_label(ab.get("end")))

    def draw(out_dir):
        with plt.rc_context(_RC):
            fig, axes = plt.subplots(1, 2, figsize=(WIDTH, H_MAIN), sharey=True)
            for ax, blk, lab in ((axes[0], ab, t["acf_abs"]), (axes[1], rr, t["acf_r"])):
                lg, vals, band = blk["lags"], blk["values"], blk["band"]
                ax.axhspan(-band, band, color=GRID, lw=0)
                ax.vlines(lg, 0, vals, color=NAVY if blk is ab else G1, lw=1)
                ax.scatter(lg, vals, s=6, color=NAVY if blk is ab else G1, zorder=3)
                ax.axhline(0, color=AXIS, lw=.5)
                ax.set_title(lab, loc="left")
                ax.set_xlabel(t["acf_lag"], fontsize=7.5)
                _style(ax, language)
            fig.tight_layout(pad=.4)
            return _save(fig, out_dir, key)
    return _spec(key, title, sub, _source(stats, pack, language), H_MAIN, r, notes, draw)


# ------------------------------------------------------------------ 6. beta mobile
def _rolling_beta(stats, pack, language, ticker):
    t = _T[language]
    name = _name(stats, ticker, t)
    roll, why = _block(stats, "rolling_beta", t)
    full, why_f = _block(stats, "beta", t)
    bench = ((roll or full or stats.get("rolling_beta") or {}).get("benchmark")
             or ((stats.get("series") or {}).get("benchmark") or {}).get("ticker") or "benchmark")
    key, title = "rolling_beta", t["rb_title"].format(name=name, bench=bench)
    why = why or why_f
    if why:
        return _missing(key, title, why)
    if not roll.get("dates") or not roll.get("beta"):
        return _missing(key, title, t["miss_series"])
    r = _Reading(stats, language)
    weekly = full.get("frequency") == "weekly"
    freq_it, freq_en = ("settimanale", "weekly") if weekly else ("giornaliero", "daily")
    ret_it, ret_en = ("rendimenti settimanali comuni", "common weekly returns") if weekly else \
        ("rendimenti giornalieri comuni", "common daily returns")
    unit_it, unit_en = ("settimane", "weeks") if roll.get("window_unit") == "settimane" else ("sedute", "sessions")
    notes = [r.tr(lambda: f"Beta {freq_it} dell'intero periodo {r.num(('beta', 'beta'), 2)} su {r.num(('beta', 'n_obs'))} "
                  f"{ret_it}, correlazione {r.num(('beta', 'correlation'), 2)}.",
                  lambda: f"Full-period {freq_en} beta {r.num(('beta', 'beta'), 2)} over {r.num(('beta', 'n_obs'))} "
                  f"{ret_en}, correlation {r.num(('beta', 'correlation'), 2)}."),
             r.tr(lambda: f"Beta mobile a {r.num(('rolling_beta', 'window'))} {unit_it}: ultimo {r.num(('rolling_beta', 'last'), 2)}, "
                  f"minimo {r.num(('rolling_beta', 'min'), 2)}, massimo {r.num(('rolling_beta', 'max'), 2)}.",
                  lambda: f"Rolling beta over {r.num(('rolling_beta', 'window'))} {unit_en}: last {r.num(('rolling_beta', 'last'), 2)}, "
                  f"low {r.num(('rolling_beta', 'min'), 2)}, high {r.num(('rolling_beta', 'max'), 2)}."),
             r.tr(lambda: f"Scarto dell'ultimo beta mobile dal beta dell'intero periodo: "
                  f"{r.op('diff', (('rolling_beta', 'last'), ('beta', 'beta')), 2, True)}.",
                  lambda: f"Gap of the last rolling beta from the full-period beta: "
                  f"{r.op('diff', (('rolling_beta', 'last'), ('beta', 'beta')), 2, True)}.")]
    # beta giornaliero di confronto: chiave di primo livello «beta_daily» (L2), forma vecchia dentro «beta»
    dp = ("beta_daily", "beta") if isinstance(stats.get("beta_daily"), dict) else ("beta", "daily_comparison", "beta")
    daily = stats.get("beta_daily") if dp[0] == "beta_daily" else full.get("daily_comparison")
    if weekly and isinstance(daily, dict) and daily.get("status", "ok") == "ok" and _ok(daily.get("beta")):
        if daily.get("asynchronous_closes"):
            notes.append(r.tr(lambda: f"Il beta giornaliero sullo stesso periodo ({r.num(dp, 2)}) è distorto dalle chiusure "
                              "non simultanee dei due listini: per questo il beta è settimanale.",
                              lambda: f"The daily beta over the same period ({r.num(dp, 2)}) is distorted by the "
                              "non-simultaneous closes of the two markets: hence the weekly beta."))
        else:
            notes.append(r.tr(lambda: f"Beta giornaliero sullo stesso periodo, per confronto: {r.num(dp, 2)}.",
                              lambda: f"Daily beta over the same period, for comparison: {r.num(dp, 2)}."))
    elif not weekly and _foreign_listing(stats):
        notes.append(r.tr(lambda: "Candidato quotato fuori dagli Stati Uniti: le chiusure giornaliere non sono simultanee "
                          "a quelle del benchmark e il beta giornaliero tende a sottostimare la sensibilità.",
                          lambda: "Candidate listed outside the United States: daily closes are not simultaneous with "
                          "the benchmark's, so the daily beta tends to understate the sensitivity."))
    sub = t["rb_sub"].format(w=fmt_number(roll["window"], 0, language), ccy=stats.get("base_currency") or "EUR",
                             unit=unit_it if language == "it" else unit_en,
                             freq=t["rb_freq_w"] if weekly else t["rb_freq_d"],
                             n=fmt_number(full["n_obs"], 0, language), start=_date_label(full.get("start")),
                             end=_date_label(full.get("end")), conv=_conv(stats, ("candidate", "benchmark"), language))

    def draw(out_dir):
        xs = [_d(x) for x in roll["dates"]]
        ys = [v if _ok(v) else float("nan") for v in roll["beta"]]
        with plt.rc_context(_RC):
            fig, a = plt.subplots(figsize=(WIDTH, H_SMALL))
            a.plot(xs, ys, color=NAVY, lw=1.1, label=t["rb_roll"])
            a.axhline(full["beta"], color=G2, lw=.9, ls="--", label=t["rb_full"])
            _style(a, language)
            _date_axis(a)
            _legend_top(a, 2)
            fig.tight_layout(pad=.4)
            return _save(fig, out_dir, key)
    return _spec(key, title, sub, _source(stats, pack, language), H_SMALL, r, notes, draw)


# ------------------------------------------------------------------ 7. VIX
def _vix_sensitivity(stats, pack, language, ticker):
    t = _T[language]
    name = _name(stats, ticker, t)
    key, title = "vix_sensitivity", t["vx_title"].format(name=name)
    block, why = _block(stats, "vix_sensitivity", t)
    if why:
        return _missing(key, title, why)
    sc = block.get("scatter") or {}
    if not sc.get("dvix") or not sc.get("ret_pct") or not isinstance(block.get("lag0"), dict):
        return _missing(key, title, t["miss_series"])
    r = _Reading(stats, language)
    l0, l1 = ("vix_sensitivity", "lag0"), ("vix_sensitivity", "lag1")
    notes = [r.tr(lambda: f"Pendenza {r.num(l0 + ('slope',), 3, pct=True)} di rendimento per punto di VIX nello stesso giorno: "
                  f"R quadro {r.num(l0 + ('r2',), 2)}, correlazione {r.num(l0 + ('corr',), 2)}, {r.num(l0 + ('n_obs',))} osservazioni.",
                  lambda: f"Slope {r.num(l0 + ('slope',), 3, pct=True)} of return per VIX point on the same day: "
                  f"R squared {r.num(l0 + ('r2',), 2)}, correlation {r.num(l0 + ('corr',), 2)}, {r.num(l0 + ('n_obs',))} observations.")]
    if isinstance(block.get("lag1"), dict):
        notes.append(r.tr(lambda: f"Con il VIX del giorno prima (chiusure asincrone fra Europa e Stati Uniti) la pendenza è "
                          f"{r.num(l1 + ('slope',), 3, pct=True)} e R quadro {r.num(l1 + ('r2',), 2)}.",
                          lambda: f"With the previous day's VIX (asynchronous closes between Europe and the US) the slope is "
                          f"{r.num(l1 + ('slope',), 3, pct=True)} and R squared {r.num(l1 + ('r2',), 2)}."))
    sub = t["vx_sub"].format(ccy=stats.get("base_currency") or "EUR", n=fmt_number(block.get("n_obs") or 0, 0, language),
                             start=_date_label(block.get("start")), end=_date_label(block.get("end")))

    def draw(out_dir):
        xs = [v for v, y in zip(sc["dvix"], sc["ret_pct"]) if _ok(v) and _ok(y)]
        ys = [y for v, y in zip(sc["dvix"], sc["ret_pct"]) if _ok(v) and _ok(y)]
        lo, hi = min(xs), max(xs)
        with plt.rc_context(_RC):
            fig, a = plt.subplots(figsize=(WIDTH, H_MAIN))
            a.scatter(xs, ys, s=4, color=G3, lw=0, zorder=2)
            k, c = block["lag0"]["slope"], block["lag0"]["intercept"]
            a.plot([lo, hi], [c + k * lo, c + k * hi], color=NAVY, lw=1.2, zorder=3)
            a.axhline(0, color=AXIS, lw=.5)
            a.axvline(0, color=AXIS, lw=.5)
            a.set_xlabel(t["vx_x"], fontsize=7.5)
            a.set_ylabel(t["vx_y"], fontsize=7.5)
            _style(a, language, "%")
            _x_numbers(a, language)
            fig.tight_layout(pad=.4)
            return _save(fig, out_dir, key)
    return _spec(key, title, sub, _source(stats, pack, language), H_MAIN, r, notes, draw)


# ------------------------------------------------------------------ 8. multipli
def _multiples(stats, pack, language, ticker):
    t = _T[language]
    name = _name(stats, ticker, t)
    key, title = "multiples", t["mu_title"].format(name=name)
    block = stats.get("multiples")
    if not isinstance(block, dict):
        return _missing(key, title, t["miss_no_block"])
    if block.get("status") not in ("ok", "partial"):
        return _missing(key, title, str(block.get("reason") or t["miss_no_block"]))
    rows = [x for x in block.get("rows") or [] if isinstance(x, dict) and x.get("status") == "ok"]
    cand = next((x for x in rows if x.get("role") == "candidate"), None)
    if cand is None:
        return _missing(key, title, t["miss_no_cand_row"])
    med = block.get("peer_median") or {}
    shown = [(f, lab) for f, lab in _MULTIPLES
             if _ok(cand.get(f)) and cand[f] > 0 and isinstance(med.get(f), dict) and _ok(med[f].get("value"))
             and med[f]["value"] > 0]
    if not shown:
        return _missing(key, title, t["miss_no_median"])
    r = _Reading(stats, language)
    cp = ("multiples", "rows", {"role": "candidate"})
    notes = []
    for f, lab in shown:
        mp = ("multiples", "peer_median", f)
        chg = (cand[f] / med[f]["value"] - 1) * 100
        pair = (cp + (f,), mp + ("value",))
        if fmt_number(abs(chg), 1, "en") == "0.0":  # «sconto del 0,0%» no: in linea
            gap_it = gap_en = None
        else:
            gap_it = lambda: ("premio" if chg > 0 else "sconto") + f" del {r.op('abs_pctchg', pair, 1, pct=True)}"
            gap_en = lambda: ("premium" if chg > 0 else "discount") + f" of {r.op('abs_pctchg', pair, 1, pct=True)}"
        notes.append(r.tr(lambda: f"{lab} {r.num(cp + (f,), 1)} contro una mediana dei peer di {r.num(mp + ('value',), 1)} "
                          f"({r.num(mp + ('n',))} peer): " + (gap_it() if gap_it else "in linea con la mediana") + ".",
                          lambda: f"{lab} {r.num(cp + (f,), 1)} against a peer median of {r.num(mp + ('value',), 1)} "
                          f"({r.num(mp + ('n',))} {'peer' if med[f]['n'] == 1 else 'peers'}): "
                          + (gap_en() if gap_en else "in line with the median") + "."))
    excl = [(f, lab) for f, lab in shown if (med[f].get("n_excluded_non_positive_or_invalid") or 0) > 0]
    if excl and len(notes) < 4:
        notes.append(r.tr(lambda: "Peer esclusi dalla mediana perché con valore non positivo o non valido: ",
                          lambda: "Peers excluded from the median for a non-positive or invalid value: ")
                     + r.f.join(f"{r.num(('multiples', 'peer_median', f, 'n_excluded_non_positive_or_invalid'))} "
                                + r.tr(lambda: "per ", lambda: "for ") + lab for f, lab in excl) + ".")
    hist = block.get("history") or {}
    years = [y for y in hist.get("years") or [] if isinstance(y, dict)] if hist.get("status") == "ok" else []
    last = years[-1] if years else None
    # approssimazioni: livello storico (pacchetto di L1) e/o riga anno (forma della fixture), unione senza doppioni
    appr = "; ".join(dict.fromkeys(str(a) for a in [*(block.get("history_approximations") or []),
                                                     *(hist.get("approximations") or []),
                                                     *(a for y in years for a in y.get("approximations") or [])] if a))
    if last and len(notes) < 4:
        hp = ("multiples", "history", "years", len(years) - 1)
        parts = [f"{lab} {r.num(hp + (k,), 1)}" for k, lab in (("pe", "P/E"), ("ev_ebitda", "EV/EBITDA"), ("ev_sales", "EV/Sales"))
                 if _ok(last.get(k))]
        if parts:
            caveat = (r.tr(lambda: " (multipli storici con le approssimazioni dichiarate sopra la tabella)",
                           lambda: " (historical multiples with the approximations stated above the table)")
                      if appr else "")
            notes.append(r.tr(lambda: f"A fine esercizio {r.year(hp + ('fy',))} il candidato quotava ", lambda: "At the end of fiscal year "
                              f"{r.year(hp + ('fy',))} the candidate traded at ") + r.f.join(parts) + caveat + ".")
    sub = t["mu_sub"].format(hist=t["mu_hist"].format(appr=appr) if appr else "")
    provs = list(dict.fromkeys(p for p in (_provider(x.get("source")) for x in rows) if p))
    when = _date_label(cand.get("as_of"))
    src = (t["src_mult"].format(prov=", ".join(provs)) + (f", {when}" if when else "")) if provs else t["src_nd"]
    # Tabella (scelta PM 04/10): ogni cifra passa da _Reading e viene riletta come le letture.
    cols = [(f, lab) for f, lab in _MULTIPLES_TABLE
            if any(_ok(x.get(f)) for x in rows) or (isinstance(med.get(f), dict) and _ok(med[f].get("value")))]
    all_rows = [x for x in block.get("rows") or [] if isinstance(x, dict)]
    cell = lambda path, value: r.num(path, 1) if _ok(value) else t["nd"]
    body, bold = [], []
    for x in all_rows:
        sel = ("multiples", "rows", {"ticker": x.get("ticker")})
        if x.get("role") == "candidate":
            bold.append(len(body))
        body.append([str(x.get("ticker"))] + [cell(sel + (f,), x.get(f)) if x.get("status") == "ok" else t["nd"]
                                              for f, _ in cols])
    bold.append(len(body))
    body.append([t["mu_med_row"]] + [cell(("multiples", "peer_median", f, "value"), (med.get(f) or {}).get("value"))
                                      for f, _ in cols])
    body.append([t["mu_n_row"]] + [
        (r.num(("multiples", "peer_median", f, "n")) + " (" +
         r.num(("multiples", "peer_median", f, "n_excluded_non_positive_or_invalid")) + ")")
        if isinstance(med.get(f), dict) and _ok(med[f].get("n")) else t["nd"] for f, _ in cols])
    off = [x for x in all_rows if x.get("status") != "ok"]
    foot = t["mu_foot"] + "".join(t["mu_off"].format(ticker=x.get("ticker"), why=x.get("reason") or "n.d.") for x in off)
    body = [[r.place(c, {"field": "cells", "index": [i, j]}) for j, c in enumerate(row)] for i, row in enumerate(body)]
    table = {"columns": [t["mu_col"]] + [lab for _, lab in cols], "rows": body, "bold_rows": bold, "foot": foot}
    return {**_spec(key, title, sub, src, 0, r, notes, None), "table": table}


# ------------------------------------------------------------------ API
_BUILDERS = (_price_history, _fat_tails, _vol_cone, _drawdown, _acf_abs, _rolling_beta, _vix_sensitivity, _multiples)


def market_readings(stats, pack=None, *, language="it", ticker=""):
    """Titoli, sottotitoli, letture e controlli delle otto figure SENZA disegnare
    (lo usa anche il controllo d'integrita' del PDF). ``{"key","title","missing"}`` per
    le figure non producibili."""
    language = validate_language(language)
    stats = stats if isinstance(stats, dict) else {}
    out = []
    for build in _BUILDERS:
        item = build(stats, pack, language, ticker)
        item.pop("draw", None)
        out.append(item)
    return out


def build_market_charts(stats, pack, out_dir, *, language="it", ticker=""):
    """Disegna le figure di mercato in ``out_dir`` (``ti_<key>.png``), ordine ``MARKET_KEYS``.

    Figura prodotta: ``{"key","title","subtitle","path","source","aspect","notes","checks"}``.
    Blocco non ``ok``: ``{"key","title","missing"}``, nessun PNG (quello vecchio si toglie).
    """
    language = validate_language(language)
    stats = stats if isinstance(stats, dict) else {}
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for build in _BUILDERS:
        item = build(stats, pack, language, ticker)
        stale = os.path.join(out_dir, f"ti_{item['key']}.png")
        if "missing" in item:
            if os.path.exists(stale):
                os.remove(stale)
            out.append(item)
            continue
        draw, height = item.pop("draw"), item.pop("height")
        if "table" in item:  # tabella: nessun PNG, quello vecchio della chiave si toglie
            if os.path.exists(stale):
                os.remove(stale)
            out.append(item)
            continue
        out.append({**item, "path": draw(out_dir), "aspect": height / WIDTH})
    return out
