"""Fatti numerici di una run Trade Idea, estratti dalle ricevute dei tool.

Funzione pura (nessun I/O, nessuna rete): riceve il dict ``checkpoint`` della run
(in DB: ``trade_idea_runs.progress_json`` -> chiave ``checkpoint``) e restituisce i
blocchi numerici che il report puo' citare, ognuno con la SUA fonte, il tool e la
data dichiarata dal fornitore.

Regole (casa Bellomberg, fallback silenziosi vietati):
- per ogni tool si usa l'ULTIMA ricevuta valida: ``success`` vero, output JSON
  parsabile, nessun ``error`` nel payload, ticker coerente con quello della run;
- una ricevuta troncata e non parsabile non si usa e lascia un gap scritto;
- un blocco o un campo atteso e non trovato resta ``None`` e finisce in ``gaps``;
- unica fonte per blocco: niente medie fra tool, nessun ripiego su un altro tool;
- le sole grandezze derivate sono dichiarate qui: ``fcf`` (cfo - |capex|, la
  stessa formula del tool, solo dove il tool non lo da' e ci sono entrambi),
  ``cash_pct`` (cassa / NAV dalla STESSA ricevuta) e ``current_fiscal_year`` del
  consensus (solo se i ricavi «anno scorso» del consensus COINCIDONO con un anno
  dello storico, tolleranza 0,5% per arrotondamenti del fornitore: mai indovinato).

``gaps`` e' letto dal PM nel memo: frasi brevi in italiano piano, senza nomi di tool
o di campo, date gg/mm/aaaa; doppioni eliminati (stessa frase = una sola).

Ordine delle ricevute: ``checkpoint["tool_receipts"]`` e' scritto in append, quindi in
ordine cronologico; ``data["_research_thesis"]["tool_receipts"]`` e' una copia
precedente della stessa lista (un prefisso, misurato sulla run reale del 04/10).
Le ricevute presenti solo nella copia della tesi si mettono DAVANTI (sono piu'
vecchie); i doppioni si scartano.

``cutoff`` (ISO str o datetime, facoltativo): se dato, le ricevute con timestamp
successivo si escludono. I timestamp senza fuso (scritti con ``datetime.now()``
dal dispatcher) si leggono come ora LOCALE della macchina (anche per stampare le
date nei gap): e' l'unica dipendenza dall'ambiente.

Convenzione percentuali in uscita: punti percentuali (41.29 = 41,29%). Per tool:
- get_price_live: var_1d/var_20d/var_60d/vs_sma*/dist_max_pct gia' in punti (pct() * 100);
- get_fundamentals (yfinance info): operating_margin e roe sono FRAZIONI -> * 100;
  dividend_yield e' gia' in PUNTI (yfinance recente; misura 17/07 su 25 ticker,
  v. valuation/dcf_bank.py e dcf_rab.py) -> invariato;
- get_consensus_estimates: implied_upside_pct e chg_vs_*_pct gia' in punti;
- quant_compute: vol_annualized_pct, daily/week_var_pct, week_cvar_pct, max_drawdown_pct
  gia' in punti;
- get_portfolio_risk: vol_annual_pct, var_99_1d_pct, max_dd_1y_pct gia' in punti;
- get_portfolio_montecarlo: campi *_pct gia' in punti; i pesi sono FRAZIONI -> * 100.
VaR, ES e drawdown mantengono il segno del tool (negativi = perdita).

Voce 7 D (04/10, Opus 5.5) — campi di rischio nuovi, letti dai payload curati:
- ``risk.book_beta``: beta del book contro SPY con ``observations``/``error`` del tool
  (beta non misurato = None + motivo nei gap, mai 0 o 1 inventati);
  ``risk.book_factor_beta``: beta sul fattore di mercato Fama-French del desk, con la
  SUA base, cosi' i due beta non sembrano in contraddizione;
- ``risk.var_backtest``: verdetto per asse (copertura / indipendenza), peso escluso,
  bassa potenza, NON AFFIDABILE: mai un «FAIL» senza l'asse;
- ``montecarlo.*.calibration`` e ``tail_reliability_warning``; ``pro_forma.allocation``
  (origine della quota simulata: l'ES al 99% del pro-forma esce sempre con la quota e
  la sua origine nei gap).
Payload di run salvate prima della cura: i campi che non portano restano None con
``status`` = «n.d. (run precedente alla cura)», mai ricostruiti a indovinare.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

__all__ = ["extract_facts"]

_SERIES = {  # chiave di uscita -> chiave in data["items"] del tool
    "revenue": "revenue", "operating_income": "operating_income",
    "net_income": "net_income", "cfo": "cfo", "capex": "capex",
    "eps_diluted": "eps_diluted", "cash": "cash", "long_term_debt": "lt_debt",
    "goodwill": "goodwill", "equity": "equity",
    "total_assets": "total_assets", "total_liabilities": "total_liabilities",
    "dividends_paid": "dividends_paid", "buyback": "buyback",
    "interest_expense": "interest_expense", "pretax_income": "pretax_income",
    "tax_expense": "tax_expense", "inventory": "inventory", "receivables": "receivables",
    "gross_profit": "gross_profit",
}
_SERIES_ORDER = ["revenue", "gross_profit", "operating_income", "interest_expense",
                 "pretax_income", "tax_expense", "net_income", "eps_diluted",
                 "cfo", "capex", "fcf", "dividends_paid", "buyback",
                 "cash", "inventory", "receivables", "total_assets", "goodwill",
                 "total_liabilities", "long_term_debt", "equity"]
_PER_SHARE = {"eps_diluted"}

# nomi in italiano piano, per i gap letti dal PM
_IT = {
    "revenue": "ricavi", "gross_profit": "utile lordo", "operating_income": "utile operativo",
    "interest_expense": "oneri finanziari", "pretax_income": "utile prima delle imposte",
    "tax_expense": "imposte", "net_income": "utile netto", "eps_diluted": "utile per azione",
    "cfo": "flusso di cassa operativo", "capex": "investimenti", "fcf": "flusso di cassa libero",
    "dividends_paid": "dividendi pagati", "buyback": "riacquisto di azioni proprie",
    "cash": "liquidità", "inventory": "magazzino", "receivables": "crediti commerciali",
    "total_assets": "totale attivo", "goodwill": "avviamento",
    "total_liabilities": "totale passivo", "long_term_debt": "debito a lungo termine",
    "equity": "patrimonio netto",
    "price": "prezzo", "date": "data del prezzo", "change_1d_pct": "variazione a 1 giorno",
    "change_20d_pct": "variazione a 20 giorni", "change_60d_pct": "variazione a 60 giorni",
    "rsi": "indice RSI", "from_high_pct": "distanza dal massimo a 52 settimane",
    "market_cap": "capitalizzazione", "ev_to_ebitda": "EV/EBITDA",
    "pe_trailing": "P/E sugli utili passati", "pe_forward": "P/E sugli utili attesi",
    "price_to_book": "prezzo/patrimonio", "price_to_sales": "prezzo/ricavi",
    "dividend_yield": "rendimento da dividendo", "total_debt": "debito totale",
    "total_cash": "cassa totale", "free_cashflow": "flusso di cassa libero",
    "operating_cashflow": "flusso di cassa operativo", "operating_margin": "margine operativo",
    "roe": "ROE",
    "target_mean": "prezzo obiettivo medio", "target_median": "prezzo obiettivo mediano",
    "target_high": "prezzo obiettivo massimo", "target_low": "prezzo obiettivo minimo",
    "analysts": "numero di analisti", "upside_pct": "potenziale di rialzo",
    "vol_pct": "volatilità annua", "beta": "beta", "var99_1d_pct": "VaR al 99% a un giorno",
    "max_drawdown_pct": "massima perdita dal picco",
    "horizon_days": "orizzonte", "var_99_pct": "VaR al 99%", "es_99_pct": "perdita media nel caso peggiore (ES al 99%)",
}
_OPS = {"sharpe": "volatilità", "beta": "beta", "var_cvar": "VaR",
        "max_drawdown": "massima perdita dal picco", "correlation_matrix": "correlazioni"}

# get_portfolio_montecarlo: campi principali ripresi tali e quali (gia' in punti %)
_MC_PCT_FIELDS = ("expected_return_pct", "median_return_pct", "stdev_pct",
                  "var_95_pct", "var_99_pct", "es_95_pct", "es_99_pct",
                  "prob_negative_pct", "prob_loss_10pct", "prob_loss_20pct",
                  "prob_gain_10pct", "prob_gain_20pct",
                  "max_drawdown_p5_pct", "max_drawdown_median_pct", "max_drawdown_p95_pct")

# tolleranza per riconoscere lo stesso esercizio fra consensus e storico (arrotondamenti)
_FY_MATCH_TOLERANCE = 0.005

# payload salvati prima della cura del 04/10 (voci 7 a/b/c/g): il campo non c'era
_ANTE_CURA = "n.d. (run precedente alla cura)"


# ---------------------------------------------------------------- utilita'

def _num(value):
    """Numero finito o None (bool e stringhe non sono numeri)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _pct_from_fraction(value):
    value = _num(value)
    return None if value is None else round(value * 100, 4)


def _int_if_whole(value):
    value = _num(value)
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _norm_ticker(value):
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _as_list(value):
    """Lista di stringhe da una lista o da una stringa singola (mai iterare i caratteri)."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None]
    return []


def _params(given):
    params = given.get("params") if isinstance(given, dict) else None
    return params if isinstance(params, dict) else {}


def _parse_ts(value):
    """ISO -> datetime aware (UTC). Senza fuso = ora locale della macchina."""
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        try:
            stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()  # naive = locale (datetime.now() del dispatcher)
    return stamp.astimezone(timezone.utc)


def _iso_or_none(value):
    """Data/ora dichiarata dal fornitore in forma ISO (lo spazio diventa 'T')."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) > 10 and text[10] == " ":
        text = text[:10] + "T" + text[11:]
    return text


def _quando(value):
    """Data e ora leggibili (gg/mm/aaaa alle hh:mm, ora locale)."""
    stamp = _parse_ts(value)
    if stamp is None:
        return "data non leggibile"
    return stamp.astimezone().strftime("%d/%m/%Y alle %H:%M")


def _n(value, decimals=2):
    """Numero all'italiana: 1.234,56."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "§").replace(".", ",").replace("§", ".")


def _data_it(value):
    """Data ISO del tool (AAAA-MM-GG...) in gg/mm/aaaa; illeggibile = «data n.d.»."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value) if isinstance(value, str) else None
    return match.group(3) + "/" + match.group(2) + "/" + match.group(1) if match else "data n.d."


def _testo(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _importo(value, unit=None):
    suffix = (" " + unit) if unit else ""
    if abs(value) >= 1e9:
        return _n(value / 1e9) + " mld" + suffix
    if abs(value) >= 1e6:
        return _n(value / 1e6) + " mln" + suffix
    return _n(value) + suffix


def _finestra(period):
    if not isinstance(period, str):
        return "periodo non dichiarato"
    match = re.fullmatch(r"(\d+)(mo|y|d)", period.strip())
    if not match:
        return period
    n, unit = int(match.group(1)), match.group(2)
    word = {"mo": ("mese", "mesi"), "y": ("anno", "anni"), "d": ("giorno", "giorni")}[unit]
    return str(n) + " " + (word[0] if n == 1 else word[1])


def _elenco(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


def _motivo(raw):
    """Motivo dichiarato dal tool in forma «n.d. (...)»: si tiene il contenuto."""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    text = raw.strip()
    match = re.fullmatch(r"n\.d\.\s*\((.*)\)", text, re.S)
    return " (" + (match.group(1) if match else text) + ")"


def _mancano(label, names):
    names = [_IT.get(n, n) for n in names]
    if not names:
        return None
    return (label + ": non è disponibile il dato «" + names[0] + "»." if len(names) == 1
            else label + ": non sono disponibili i dati " + _elenco(["«" + n + "»" for n in names]) + ".")


# ---------------------------------------------------------------- ricevute

def _ordered_receipts(checkpoint):
    top = checkpoint.get("tool_receipts")
    top = top if isinstance(top, list) else []
    data = checkpoint.get("data") if isinstance(checkpoint.get("data"), dict) else {}
    thesis = data.get("_research_thesis") if isinstance(data.get("_research_thesis"), dict) else {}
    older = thesis.get("tool_receipts") if isinstance(thesis.get("tool_receipts"), list) else []

    def key(receipt):
        try:
            return json.dumps([receipt.get("tool"), receipt.get("input"), receipt.get("timestamp"),
                               receipt.get("output")], sort_keys=True, default=str)
        except (TypeError, ValueError):
            return repr(receipt)

    seen = {key(r) for r in top if isinstance(r, dict)}
    only_thesis = []
    for receipt in older:
        if isinstance(receipt, dict) and key(receipt) not in seen:
            seen.add(key(receipt))
            only_thesis.append(receipt)
    return only_thesis + [r for r in top if isinstance(r, dict)]


def _parse_output(receipt):
    output = receipt.get("output")
    try:
        parsed = json.loads(output) if isinstance(output, str) else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return None, None
    payload = parsed.get("data", parsed)
    return parsed, payload


class _Picker:
    """Sceglie l'ultima ricevuta valida per tool e annota perche' le altre cadono."""

    def __init__(self, receipts, cutoff, gaps):
        self.receipts = receipts
        self.cutoff = _parse_ts(cutoff) if cutoff is not None else None
        self.gaps = gaps
        if cutoff is not None and self.cutoff is None:
            gaps.append("La data di chiusura dei dati della run non è leggibile: "
                        "nessuna lettura è stata esclusa per data.")
        self._cutoff_noted = set()

    def _in_window(self, receipt):
        if self.cutoff is None:
            return True
        stamp = _parse_ts(receipt.get("timestamp"))
        return stamp is not None and stamp <= self.cutoff

    def valid(self, tool, accept):
        """Tutte le ricevute valide (cronologiche), senza annotare gap."""
        out = []
        for receipt in self.receipts:
            if receipt.get("tool") != tool or not self._in_window(receipt) or not receipt.get("success"):
                continue
            parsed, payload = _parse_output(receipt)
            if not isinstance(payload, dict) or payload.get("error") or accept(receipt, payload):
                continue
            out.append((receipt, parsed, payload))
        return out

    def pick(self, tool, label, accept):
        """``accept(receipt, payload) -> str|None``: None = coerente, stringa = motivo."""
        candidates = [r for r in self.receipts if r.get("tool") == tool]
        if not candidates:
            self.gaps.append(label + ": dato non procurato in questa run.")
            return None, None
        rejected_tail = []   # ricevute piu' recenti di quella usata, scartate per difetto
        mismatched = 0
        for receipt in reversed(candidates):
            if not self._in_window(receipt):
                stamp = _parse_ts(receipt.get("timestamp"))
                if (label, stamp is None) not in self._cutoff_noted:
                    self._cutoff_noted.add((label, stamp is None))
                    self.gaps.append(
                        label + (": una lettura senza data leggibile è stata scartata perché non si "
                                 "può collocare rispetto alla chiusura dei dati della run."
                                 if stamp is None else
                                 ": una lettura del " + _quando(receipt.get("timestamp"))
                                 + " è successiva alla chiusura dei dati della run e non è stata usata."))
                continue
            if not receipt.get("success"):
                continue
            parsed, payload = _parse_output(receipt)
            if parsed is None:
                rejected_tail.append((receipt, "è arrivata incompleta" if receipt.get("truncated")
                                      else "è illeggibile"))
                continue
            if not isinstance(payload, dict) or payload.get("error"):
                continue
            if accept(receipt, payload):
                mismatched += 1
                continue
            for bad, why in rejected_tail:
                self.gaps.append(label + ": la lettura del " + _quando(bad.get("timestamp")) + " " + why
                                 + " ed è stata scartata; si usa quella del "
                                 + _quando(receipt.get("timestamp")) + ".")
            return receipt, (parsed, payload)
        for bad, why in rejected_tail:
            self.gaps.append(label + ": la lettura del " + _quando(bad.get("timestamp")) + " " + why
                             + " ed è stata scartata.")
        self.gaps.append(label + ": nessuna lettura valida"
                         + (" per questo titolo" if mismatched else "") + " in questa run.")
        return None, None


def _source(receipt, parsed):
    return (receipt.get("source") or parsed.get("_source") or "fonte non dichiarata")


def _ticker_accept(ticker):
    def accept(receipt, payload):
        given = receipt.get("input")
        if not isinstance(given, dict) or _norm_ticker(given.get("ticker")) != ticker:
            return "input di altro ticker"
        declared = payload.get("ticker", payload.get("identity_symbol"))
        if declared is not None and _norm_ticker(declared) != ticker:
            return "output di altro ticker"
        return None
    return accept


def _latest(stamps):
    stamps = [s for s in stamps if s]
    return max(stamps, key=lambda s: _parse_ts(s) or datetime.min.replace(tzinfo=timezone.utc)) if stamps else None


def _earliest(stamps):
    stamps = [s for s in stamps if s]
    return min(stamps, key=lambda s: _parse_ts(s) or datetime.max.replace(tzinfo=timezone.utc)) if stamps else None


# ---------------------------------------------------------------- blocchi

def _history(picker, ticker, currency, gaps):
    label = "Storico di bilancio"
    receipt, got = picker.pick("get_financial_history", label, _ticker_accept(ticker))
    if receipt is None:
        return None
    parsed, data = got
    items = data.get("items") if isinstance(data.get("items"), dict) else {}
    derived = data.get("derived") if isinstance(data.get("derived"), dict) else {}
    units = data.get("units") if isinstance(data.get("units"), dict) else {}

    def series_of(raw):
        out = {}
        if isinstance(raw, dict):
            for year, value in raw.items():
                try:
                    year_int = int(year)
                except (TypeError, ValueError):
                    continue
                if _num(value) is not None:
                    out[year_int] = value
        return dict(sorted(out.items()))

    series = {name: series_of(items.get(item_key)) for name, item_key in _SERIES.items()}
    # FCF: quello del tool (derived.fcf = cfo - |capex|); solo per gli anni dove il tool
    # non lo da' e ci sono ENTRAMBI cfo e capex si deriva con la stessa formula.
    fcf = series_of(derived.get("fcf"))
    for year in sorted(set(series["cfo"]) & set(series["capex"])):
        if year not in fcf:
            fcf[year] = series["cfo"][year] - abs(series["capex"][year])
            gaps.append(label + ": il flusso di cassa libero del " + str(year) + " è calcolato qui "
                        "(flusso operativo meno investimenti), la fonte non lo fornisce.")
    series["fcf"] = dict(sorted(fcf.items()))
    series = {name: series[name] for name in _SERIES_ORDER}
    sentence = _mancano(label, [name for name in _SERIES_ORDER if not series[name]])
    if sentence:
        gaps.append(sentence)

    monetary = {units.get(item_key) for name, item_key in _SERIES.items()
                if name not in _PER_SHARE and item_key in items}
    monetary.discard(None)
    unit = None
    if len(monetary) == 1:
        unit = next(iter(monetary))
    elif not monetary:
        gaps.append(label + ": la fonte non indica la valuta degli importi.")
    else:
        gaps.append(label + ": gli importi sono in valute diverse (" + _elenco(sorted(map(str, monetary)))
                    + "), quindi non si possono confrontare fra loro.")
    if unit and currency and unit != currency:
        gaps.append(label + " non usato: gli importi sono in " + str(unit) + ", il titolo quota in "
                    + currency + ".")
        return None

    raw_years = data.get("years")
    years = []
    for year in raw_years if isinstance(raw_years, list) else []:
        try:
            years.append(int(year))
        except (TypeError, ValueError):
            continue
    if not years:
        years = sorted({y for s in series.values() for y in s})
    gaps.append(label + ": la fonte non indica la data di riferimento"
                + ("; l'ultimo esercizio disponibile è il " + str(max(years)) + "." if years else "."))
    return {"years": sorted(set(years)), "series": series, "unit": unit,
            "source": _source(receipt, parsed), "tool": "get_financial_history", "as_of": None}


def _quote(picker, ticker, gaps):
    label = "Quotazione"
    receipt, got = picker.pick("get_price_live", label, _ticker_accept(ticker))
    if receipt is None:
        return None
    parsed, data = got
    as_of = _iso_or_none(data.get("price_asof"))
    block = {
        "price": _num(data.get("px")),
        "date": as_of[:10] if as_of else None,
        "change_1d_pct": _num(data.get("var_1d")),
        "change_20d_pct": _num(data.get("var_20d")),
        "change_60d_pct": _num(data.get("var_60d")),
        "rsi": _num(data.get("rsi14")),
        "from_high_pct": _num(data.get("dist_max_pct")),
        # il tool da' solo la DISTANZA % dalle medie (vs_sma20_pct/vs_sma50_pct), non il livello
        "ma20": None, "ma50": None,
        # minimo/massimo 52 settimane: il tool non li restituisce (solo dist_max_pct)
        "low_52w": None, "high_52w": None,
        "source": data.get("source") or _source(receipt, parsed),
        "tool": "get_price_live", "as_of": as_of,
    }
    sentence = _mancano(label, [n for n in ("price", "date", "change_1d_pct", "change_20d_pct",
                                             "change_60d_pct", "rsi", "from_high_pct") if block[n] is None])
    if sentence:
        gaps.append(sentence)
    distances = [(days, _num(data.get(key))) for days, key in (("20", "vs_sma20_pct"), ("50", "vs_sma50_pct"))]
    known = [d + " giorni " + ("+" if v >= 0 else "") + _n(v) + "%" for d, v in distances if v is not None]
    gaps.append(label + ": il livello delle medie mobili a 20 e 50 giorni non è disponibile"
                + (", solo la distanza del prezzo da esse (" + _elenco(known) + ")." if known else "."))
    gaps.append(label + ": minimo e massimo delle ultime 52 settimane non disponibili"
                + (" (c'è solo la distanza dal massimo)." if block["from_high_pct"] is not None else "."))
    if data.get("dist_max_note"):
        gaps.append(label + ": " + str(data.get("dist_max_note")).rstrip(".") + ".")
    return block


def _fundamentals(picker, ticker, gaps):
    label = "Indicatori di bilancio"
    receipt, got = picker.pick("get_fundamentals", label, _ticker_accept(ticker))
    if receipt is None:
        return None
    parsed, data = got
    own_currency = next((data.get(k) for k in ("financial_currency", "financialCurrency", "currency")
                         if isinstance(data.get(k), str) and data.get(k).strip()), None)
    block = {
        "market_cap": _num(data.get("market_cap")),
        "ev_to_ebitda": _num(data.get("ev_to_ebitda")),
        "pe_trailing": _num(data.get("pe_trailing")),
        "pe_forward": _num(data.get("pe_forward")),
        "price_to_book": _num(data.get("price_to_book")),
        "price_to_sales": _num(data.get("price_to_sales")),
        "dividend_yield": _num(data.get("dividend_yield")),        # gia' in punti (yfinance)
        "total_debt": _num(data.get("total_debt")),
        "total_cash": _num(data.get("total_cash")),
        "free_cashflow": _num(data.get("free_cashflow")),
        "operating_cashflow": _num(data.get("operating_cashflow")),
        "operating_margin": _pct_from_fraction(data.get("operating_margin")),  # frazione -> punti
        "roe": _pct_from_fraction(data.get("roe")),                            # frazione -> punti
        "currency": own_currency,
        "source": _source(receipt, parsed), "tool": "get_fundamentals", "as_of": None,
    }
    sentence = _mancano(label, [n for n, v in block.items()
                                if n not in ("source", "tool", "as_of", "currency") and v is None])
    if sentence:
        gaps.append(sentence)
    gaps.append(label + ": il fornitore non indica la data dei dati"
                + (" né la valuta degli importi." if own_currency is None else "."))
    return block


def _consensus(picker, ticker, currency, gaps):
    label = "Consensus degli analisti"
    receipt, got = picker.pick("get_consensus_estimates", label, _ticker_accept(ticker))
    if receipt is None:
        return None
    parsed, data = got
    own_currency = data.get("currency")
    if not own_currency:
        gaps.append(label + ": il fornitore non indica la valuta dei prezzi obiettivo.")
    elif currency and own_currency != currency:
        gaps.append(label + " non usato: i prezzi obiettivo sono in " + str(own_currency)
                    + ", il titolo quota in " + currency + ".")
        return None
    targets = data.get("price_targets") if isinstance(data.get("price_targets"), dict) else {}
    year_ago = {}

    def rows(key, what, ago_key):
        raw = data.get(key)
        if not isinstance(raw, list):
            gaps.append(label + ": mancano le stime di " + what + _motivo(raw) + ".")
            return []
        out = []
        for row in raw:
            if isinstance(row, dict):
                period = str(row.get("period")) if row.get("period") is not None else None
                out.append({"period": period, "value": _num(row.get("avg")),
                            "analysts": _int_if_whole(row.get("numberOfAnalysts"))})
                if period == "0y":
                    year_ago[ago_key] = _num(row.get(ago_key))
        return out

    def rec_counts(row):
        return {out: _int_if_whole(row.get(src)) for out, src in (
            ("strong_buy", "strongBuy"), ("buy", "buy"), ("hold", "hold"),
            ("sell", "sell"), ("strong_sell", "strongSell"))}

    recommendations = recommendations_prev = None
    recommendations_period = {"current": None, "prev": None}
    raw_rec = data.get("recommendations")
    current = raw_rec.get("current_month") if isinstance(raw_rec, dict) else None
    previous = raw_rec.get("three_months_ago") if isinstance(raw_rec, dict) else None
    if isinstance(current, dict):
        recommendations = rec_counts(current)
        recommendations_period["current"] = (str(current.get("period"))
                                             if current.get("period") is not None else None)
    else:
        gaps.append(label + ": mancano le raccomandazioni (compra/tieni/vendi)" + _motivo(raw_rec) + ".")
    if isinstance(previous, dict):
        recommendations_prev = rec_counts(previous)
        recommendations_period["prev"] = (str(previous.get("period"))
                                          if previous.get("period") is not None else None)
    elif isinstance(current, dict):
        gaps.append(label + ": manca il confronto con le raccomandazioni di 3 mesi fa.")

    # revisioni EPS: il tool le da' gia' in punti % (chg_vs_30d_pct / chg_vs_90d_pct)
    eps_revisions = []
    raw_rev = data.get("eps_revisions")
    if isinstance(raw_rev, list):
        for row in raw_rev:
            if isinstance(row, dict):
                eps_revisions.append({
                    "period": str(row.get("period")) if row.get("period") is not None else None,
                    "current": _num(row.get("current")),
                    "change_30d_pct": _num(row.get("chg_vs_30d_pct")),
                    "change_90d_pct": _num(row.get("chg_vs_90d_pct"))})
    else:
        gaps.append(label + ": mancano le revisioni recenti delle stime di utile per azione"
                    + _motivo(raw_rev) + ".")

    as_of = _iso_or_none(data.get("data_as_of"))
    block = {
        "target_mean": _num(targets.get("mean")),
        "target_median": _num(targets.get("median")),
        "target_high": _num(targets.get("high")),
        "target_low": _num(targets.get("low")),
        "analysts": _int_if_whole(targets.get("number_of_analysts")),
        "upside_pct": _num(targets.get("implied_upside_pct")),   # gia' in punti
        "eps": rows("eps_estimates", "utile per azione", "yearAgoEps"),
        "revenue": rows("revenue_estimates", "ricavi", "yearAgoRevenue"),
        # valori dell'esercizio precedente a «0y» dichiarati dal fornitore
        "year_ago_eps": year_ago.get("yearAgoEps"),
        "year_ago_revenue": year_ago.get("yearAgoRevenue"),
        # anno fiscale di «0y»: lo fissa extract_facts SOLO se i ricavi coincidono con lo storico
        "current_fiscal_year": None,
        "eps_revisions": eps_revisions,
        "recommendations": recommendations,
        "recommendations_prev": recommendations_prev,
        "recommendations_period": recommendations_period,
        "status": data.get("consensus_status") if isinstance(data.get("consensus_status"), str) else None,
        "source": data.get("source") or _source(receipt, parsed),
        "tool": "get_consensus_estimates", "as_of": as_of,
    }
    sentence = _mancano(label, [n for n in ("target_mean", "target_median", "target_high",
                                             "target_low", "analysts", "upside_pct") if block[n] is None])
    if sentence:
        gaps.append(sentence)
    if as_of is None:
        gaps.append(label + ": il fornitore non indica a che data risalgono prezzi obiettivo e stime.")
    reason = str(data.get("consensus_reason") or "")
    if block["status"] == "not_applicable":
        gaps.append(label + ": non applicabile a questo tipo di strumento.")
    elif block["status"] == "unavailable":
        gaps.append(label + ": nessun analista copre il titolo presso il fornitore.")
    elif (block["status"] and block["status"] not in ("complete", "partial")) or (
            block["status"] == "partial" and reason and "observation date" not in reason):
        # 'partial' con la data mancante e' gia' detto sopra; altri motivi si riportano
        gaps.append(label + ": dato incompleto" + (" (" + reason + ")" if reason else "") + ".")
    return block


def _risk(picker, ticker, gaps):
    label = "Rischio del titolo"

    def quant(operation):
        def accept(receipt, payload):
            given = receipt.get("input")
            if not isinstance(given, dict) or given.get("operation") != operation:
                return "altra operazione"
            if ticker not in {_norm_ticker(t) for t in _as_list(given.get("tickers"))}:
                return "ticker assente dall'input"
            if operation == "correlation_matrix":   # righe in data["matrix"], non in data[ticker]
                matrix = payload.get("matrix")
                row = matrix.get(ticker) if isinstance(matrix, dict) else None
            else:
                row = payload.get(ticker)
            if not isinstance(row, dict) or row.get("error"):
                return "riga del ticker assente o in errore"
            return None
        receipts = [r for r in picker.receipts if r.get("tool") == "quant_compute"
                    and isinstance(r.get("input"), dict) and r["input"].get("operation") == operation]
        sub_label = label + " (" + _OPS[operation] + ")"
        if not receipts:
            gaps.append(sub_label + ": non calcolata in questa run.")
            return None, None
        sub = _Picker(receipts, None, gaps)
        sub.cutoff, sub._cutoff_noted = picker.cutoff, picker._cutoff_noted
        return sub.pick("quant_compute", sub_label, accept)

    used = []
    candidate = {"vol_pct": None, "beta": None, "beta_basis": None, "var99_1d_pct": None,
                 "var99_1w_pct": None, "es99_1w_pct": None,
                 "max_drawdown_pct": None, "observations": None,
                 "correlations": [], "correlations_window": None}
    periods = {}
    receipt, got = quant("sharpe")
    if receipt is not None:
        parsed, data = got
        candidate["vol_pct"] = _num(data[ticker].get("vol_annualized_pct"))
        periods["volatilità"] = data.get("period")
        used.append((receipt, parsed))
        # n_obs del tool e' la lunghezza del PANNELLO (ticker + benchmark), non del ticker
        n_obs = _num(data.get("n_obs"))
        gaps.append(label + ": non è disponibile il numero di giorni di borsa usati per volatilità e VaR"
                    + (" (il conteggio fornito, " + str(int(n_obs)) + ", include anche i giorni del "
                       "solo indice di confronto)." if n_obs is not None else "."))
    receipt, got = quant("beta")
    if receipt is not None:
        parsed, data = got
        benchmark = _params(receipt["input"]).get("benchmark", "SPY")
        candidate["beta"] = _num(data[ticker].get("beta_vs_" + str(benchmark)))
        # quant_compute scarica i prezzi di chiusura come sono (auto_adjust), senza cambio
        candidate["beta_basis"] = ("prezzi del titolo nella sua valuta di quotazione contro "
                                   + str(benchmark) + " nella sua valuta, senza cambio")
        periods["beta"] = data.get("period")
        used.append((receipt, parsed))
    receipt, got = quant("var_cvar")
    if receipt is not None:
        parsed, data = got
        row = data[ticker]
        confidence = _num(row.get("confidence"))
        if confidence == 0.99:
            candidate["var99_1d_pct"] = _num(row.get("daily_var_pct"))
            # il tool scala il quantile giornaliero per sqrt(giorni della settimana) e da' la
            # CVaR (expected shortfall) SOLO a 1 settimana: niente ES giornaliero, non si inventa
            candidate["var99_1w_pct"] = _num(row.get("week_var_pct"))
            candidate["es99_1w_pct"] = _num(row.get("week_cvar_pct"))
            periods["VaR"] = data.get("period")
            used.append((receipt, parsed))
        else:
            gaps.append(label + ": il VaR è disponibile solo con confidenza "
                        + (_n(confidence * 100, 0) + "%" if confidence is not None else "non dichiarata")
                        + ", non al 99%.")
    receipt, got = quant("max_drawdown")
    if receipt is not None:
        parsed, data = got
        candidate["max_drawdown_pct"] = _num(data[ticker].get("max_drawdown_pct"))
        periods["massima perdita"] = data.get("period")
        used.append((receipt, parsed))
    receipt, got = quant("correlation_matrix")
    if receipt is not None:
        parsed, data = got
        row = data["matrix"][ticker]
        candidate["correlations"] = [{"ticker": other, "corr": _num(value)}
                                     for other, value in row.items() if other != ticker]
        candidate["correlations_window"] = data.get("period")
        periods["correlazioni"] = data.get("period")
        used.append((receipt, parsed))
        nulls = [c["ticker"] for c in candidate["correlations"] if c["corr"] is None]
        if nulls:
            gaps.append(label + ": correlazione non misurabile con " + _elenco(nulls) + ".")
    if len(set(periods.values())) > 1:
        groups = {}
        for name, period in periods.items():
            groups.setdefault(period, []).append(name)
        gaps.append(label + ": le misure coprono periodi diversi ("
                    + "; ".join(_elenco(names) + " su " + _finestra(period)
                                for period, names in groups.items()) + ").")
    sentence = _mancano(label, [n for n in ("vol_pct", "beta", "var99_1d_pct", "max_drawdown_pct")
                                if candidate[n] is None])
    if sentence and used:
        gaps.append(sentence)
    if not used:
        candidate = None
    else:
        candidate.update(source=used[0][1].get("_source") or used[0][0].get("source")
                         or "fonte non dichiarata",
                         tool="quant_compute",
                         as_of=_latest([p.get("_timestamp") or r.get("timestamp") for r, p in used]))

    book = book_beta = None
    book_label = "Rischio del portafoglio"
    receipt, got = picker.pick("get_portfolio_risk", book_label, lambda r, p: None)
    if receipt is not None:
        parsed, data = got
        port = data.get("portfolio") if isinstance(data.get("portfolio"), dict) else {}
        basis = data.get("beta_basis")
        beta, beta_why, beta_new = _book_beta_status(data)
        book = {"vol_pct": _num(port.get("vol_annual_pct")),
                "beta": beta,
                # la nota del tool porta un riferimento interno «(fix gg/mm; ...)»: si toglie
                "beta_basis": (re.sub(r"\s*\(fix[^)]*\)", "", basis).strip() or None
                               if isinstance(basis, str) else None),
                "var99_1d_pct": _num(port.get("var_99_1d_pct")),
                "max_drawdown_pct": _num(port.get("max_dd_1y_pct")),
                "vol_target_pct": None,
                "source": _source(receipt, parsed), "tool": "get_portfolio_risk",
                "as_of": _iso_or_none(data.get("timestamp")) or _iso_or_none(parsed.get("_timestamp"))}
        # beta non misurato con un motivo: frase propria (non il generico «non disponibile»)
        sentence = _mancano(book_label, [n for n in ("vol_pct", "beta", "var99_1d_pct", "max_drawdown_pct")
                                         if book[n] is None and not (n == "beta" and beta_why)])
        if sentence:
            gaps.append(sentence)
        book_beta = _book_beta_block(picker, receipt, data, book, beta_why, beta_new, book_label, gaps)
        skipped = _as_list(data.get("skipped_tickers"))
        if skipped:
            gaps.append(book_label + ": calcolato senza " + _elenco(skipped)
                        + " (posizioni escluse dal calcolo).")
        if book["beta"] is not None and book["beta_basis"] is None:
            gaps.append(book_label + ": non è indicato su quale base è calcolato il beta.")
    gaps.append("Volatilità obiettivo del portafoglio non presente fra i dati della run: "
                "è fissata nel mandato del PM.")
    if (candidate and book and candidate["beta"] is not None and book["beta"] is not None
            and candidate["beta_basis"] != book["beta_basis"]):
        gaps.append("I due beta sono calcolati su basi diverse (titolo: " + candidate["beta_basis"]
                    + "; portafoglio: " + (book["beta_basis"] or "base non dichiarata")
                    + "): non vanno confrontati direttamente.")
    factor_beta = _book_factor_beta(picker, book_beta, gaps)
    backtest = _var_backtest(picker, gaps)

    if candidate is None and book is None and factor_beta is None and backtest is None:
        return None
    # source/tool/as_of del blocco = candidato e book (il grafico del rischio); i sotto-blocchi
    # nuovi portano la loro fonte e la loro data
    parts = [p for p in (candidate, book) if p] or [p for p in (factor_beta, backtest) if p]
    return {"candidate": candidate, "book": book,
            "book_beta": book_beta, "book_factor_beta": factor_beta, "var_backtest": backtest,
            "source": " + ".join(p["source"] for p in parts),
            "tool": " + ".join(p["tool"] for p in parts),
            # il piu' vecchio dei due: il blocco e' fresco almeno fino a li'
            "as_of": _earliest([p.get("as_of") for p in parts])}


def _book_beta_status(data):
    """(beta, motivo, formato_nuovo) del beta del book contro SPY da get_portfolio_risk.

    Formato nuovo (dal 04/10): il tool scrive ``beta_error``/``beta_obs``; beta None =
    non misurato, col motivo. Formato vecchio: lo 0.0 era ANCHE il valore di ripiego
    quando SPY non veniva scaricato (R6 a), quindi uno zero non e' usabile come misura.
    """
    port = data.get("portfolio") if isinstance(data.get("portfolio"), dict) else {}
    raw = _num(port.get("beta_vs_spy"))
    new = "beta_error" in data or "beta_obs" in data
    if new:
        if raw is None:
            return None, _testo(data.get("beta_error")) or "motivo non dichiarato", True
        return raw, None, True
    if raw == 0:
        return None, ("la lettura riporta 0,00 ma è di una versione precedente alla cura, in cui lo "
                      "zero veniva scritto anche quando l'indice SPY non era stato scaricato: non "
                      "distingue un beta misurato da un dato mancante, " + _ANTE_CURA), False
    return raw, None, False


def _book_beta_block(picker, receipt, data, book, why, new, label, gaps):
    """Beta del book contro SPY con osservazioni e motivo; dichiara le letture discordi."""
    obs = _int_if_whole(data.get("beta_obs")) if new else None
    block = {"value": book["beta"], "benchmark": "SPY",
             "label": "beta del portafoglio contro l'indice SPY (rendimenti giornalieri)",
             "basis": book["beta_basis"], "observations": obs,
             "observations_status": None if new else _ANTE_CURA, "error": why,
             # avviso del tool (pochi giorni comuni: stima indicativa); vale solo su un beta misurato
             "note": _testo(data.get("beta_note")) if book["beta"] is not None else None}
    if why:
        gaps.append(label + ": beta contro l'indice SPY non misurato (" + why.rstrip(".") + ").")
    elif block["note"]:
        gaps.append(label + ": beta contro l'indice SPY " + _n(book["beta"]) + " da leggere con cautela: "
                    + block["note"].rstrip(".") + ".")
    # l'ultima ricevuta vale (unica fonte), ma una lettura discorde non si nasconde
    other_measured, other_missing = [], []
    for other, _, payload in picker.valid("get_portfolio_risk", lambda r, p: None):
        if other is receipt:
            continue
        value, other_why, _ = _book_beta_status(payload)
        if book["beta"] is None and value is not None:
            other_measured.append(_quando(other.get("timestamp")) + " (" + _n(value) + ")")
        elif book["beta"] is not None and value is None:
            other_missing.append(_quando(other.get("timestamp")))
    if other_measured:
        gaps.append(label + ": il beta contro l'indice SPY era stato misurato nelle letture del "
                    + _elenco(other_measured) + ", ma si usa la più recente, del "
                    + _quando(receipt.get("timestamp")) + ", che non lo misura: le letture non si mescolano.")
    if other_missing:
        gaps.append(label + ": nelle letture del " + _elenco(other_missing) + " il beta contro l'indice "
                    "SPY non risultava misurato; si usa quella del " + _quando(receipt.get("timestamp"))
                    + ", che lo misura.")
    return block


def _book_factor_beta(picker, book_beta, gaps):
    """Beta del book sul fattore di mercato Fama-French (get_portfolio_factors), con la SUA base.

    Facoltativo: senza ricevuta non e' un buco (il beta del book e' quello contro SPY).
    """
    if not any(r.get("tool") == "get_portfolio_factors" for r in picker.receipts):
        return None
    label = "Fattori di rischio del portafoglio"
    receipt, got = picker.pick("get_portfolio_factors", label,
                               lambda r, p: None if isinstance(p.get("portfolio_aggregate"), dict)
                               else "aggregato assente")
    if receipt is None:
        return None
    parsed, data = got
    model = _testo(data.get("model"))
    regional = " regionale" if model and "REGIONAL" in model.upper() else ""
    block = {"value": _num(data["portfolio_aggregate"].get("beta_market")),
             "factor": "mercato",
             "label": ("beta del portafoglio sul fattore di mercato del modello Fama-French"
                       + regional + " (non contro l'indice SPY)"),
             "model": model, "period": data.get("period"),
             "r_squared": _num(data.get("portfolio_avg_r_squared")),
             "coverage_weight_pct": _num(data.get("coverage_weight_pct")),
             "source": _source(receipt, parsed), "tool": "get_portfolio_factors",
             "as_of": _iso_or_none(data.get("timestamp")) or _iso_or_none(parsed.get("_timestamp"))}
    if block["value"] is None:
        gaps.append(label + ": non è disponibile il beta sul fattore di mercato.")
        return block
    spy = (book_beta or {}).get("value")
    where = "sul fattore di mercato del modello Fama-French" + regional + " (" + _finestra(block["period"]) + ")"
    if spy is not None:
        gaps.append("Il portafoglio ha due beta su basi diverse: " + _n(spy) + " contro l'indice SPY e "
                    + _n(block["value"]) + " " + where + "; misurano cose diverse, non sono in "
                    "contraddizione e non vanno confrontati direttamente.")
    elif book_beta is not None:
        gaps.append("Il beta del portafoglio " + where + " è " + _n(block["value"]) + ": è su un'altra "
                    "base e non sostituisce il beta contro l'indice SPY, che resta non misurato.")
    return block


_ASSI = (("coverage", "copertura", "il numero di eccezioni non è coerente con il livello di confidenza"),
         ("independence", "indipendenza", "le eccezioni arrivano a grappoli"))


def _bt_level(row, new):
    """Un livello (95/99) del backtest: verdetto per asse. Formato vecchio: gli assi sono i due
    test riportati dal tool (pass al 5%), senza p esatti ne' bassa potenza (n.d.)."""
    kup = row.get("kupiec_pof") if isinstance(row.get("kupiec_pof"), dict) else {}
    ind = row.get("christoffersen_ind") if isinstance(row.get("christoffersen_ind"), dict) else {}
    level = {"verdict": _testo(row.get("verdict")), "verdict_detail": _testo(row.get("verdict_detail")),
             "exceptions": _int_if_whole(kup.get("exceptions")),
             "expected_exceptions": _num(row.get("expected_exceptions") if new else kup.get("expected")),
             "low_power": row.get("low_power") if isinstance(row.get("low_power"), bool) else None,
             "detail_status": None if new else _ANTE_CURA}
    for key, _, _ in _ASSI:
        axis = row.get(key) if isinstance(row.get(key), dict) else None
        if axis is not None:
            verdict, p_value, test = _testo(axis.get("verdict")), _num(axis.get("p_value")), _testo(axis.get("test"))
        else:
            src = kup if key == "coverage" else ind
            flag = src.get("pass_5pct")
            verdict = ("PASS" if flag else "FAIL") if isinstance(flag, bool) else None
            p_value, test = _num(src.get("p_value")), None
        level[key] = {"verdict": verdict, "p_value": p_value, "test": test}
    return level


def _var_backtest(picker, gaps):
    """Backtest del VaR del book (get_var_backtest): mai un «FAIL» senza l'asse e il perimetro."""
    label = "Backtest del VaR del portafoglio"
    receipt, got = picker.pick("get_var_backtest", label,
                               lambda r, p: None if any(isinstance(p.get(k), dict) for k in ("var95", "var99"))
                               else "livelli assenti")
    if receipt is None:
        return None
    parsed, data = got
    new = "reliable" in data or "excluded_weight_pct" in data
    detail = [{"ticker": row.get("ticker"), "reason": _testo(row.get("motivo")),
               "valid_obs": _int_if_whole(row.get("obs_valide")), "weight_pct": _num(row.get("peso_pct"))}
              for row in (data.get("excluded_detail") if isinstance(data.get("excluded_detail"), list) else [])
              if isinstance(row, dict)]
    block = {"window": _int_if_whole(data.get("window")), "period": data.get("period"),
             "observations_tested": _int_if_whole(data.get("n_obs_tested")),
             "reliable": data.get("reliable") if isinstance(data.get("reliable"), bool) else None,
             "excluded_tickers": _as_list(data.get("excluded_tickers")),
             "excluded_detail": detail,
             "excluded_weight_pct": _num(data.get("excluded_weight_pct")),
             "excluded_weight_threshold_pct": _num(data.get("excluded_weight_threshold_pct")),
             # review RV-R/RV-L3: peso MANCANTE in media nei giorni testati (anche con esclusi 0%)
             # e nomi presenti in meno del 90% di quei giorni; None = campo assente nella lettura
             "missing_weight_tested_window_pct": _num(data.get("missing_weight_tested_window_pct")),
             "partial_coverage_tested_window": ({str(k): str(v) for k, v in
                                                 data["partial_coverage_tested_window"].items()}
                                                if isinstance(data.get("partial_coverage_tested_window"), dict)
                                                else None),
             "perimeter_status": None if new else _ANTE_CURA,
             "levels": {conf: _bt_level(data["var" + conf], new) if isinstance(data.get("var" + conf), dict) else None
                        for conf in ("95", "99")},
             "source": _source(receipt, parsed), "tool": "get_var_backtest",
             "as_of": _iso_or_none(data.get("timestamp")) or _iso_or_none(parsed.get("_timestamp"))}
    weight, threshold = block["excluded_weight_pct"], block["excluded_weight_threshold_pct"]
    if not new:
        perimeter = "peso dei titoli esclusi " + _ANTE_CURA
    elif weight is None:
        perimeter = "peso dei titoli esclusi non dichiarato"
    else:
        perimeter = "titoli esclusi pari al " + _n(weight) + "% del perimetro"
    missing = block["missing_weight_tested_window_pct"]
    partial = block["partial_coverage_tested_window"] or {}
    if new:
        perimeter += ("; nei giorni testati manca in media il " + _n(missing) + "% del perimetro"
                      if missing is not None else "; peso mancante nei giorni testati non dichiarato")
    if detail:
        gaps.append(label + ": esclusi " + _elenco([(d["ticker"] or "titolo senza nome")
                                                     + (" (" + d["reason"] + ")" if d["reason"] else "")
                                                     for d in detail])
                    + (", che pesano il " + _n(weight) + "% del perimetro" if weight is not None else "")
                    + (" (soglia " + _n(threshold, 0) + "%)" if threshold is not None else "") + ".")
    elif not new and block["excluded_tickers"]:
        gaps.append(label + ": esclusi " + _elenco(block["excluded_tickers"]) + ".")
    if not new:
        gaps.append(label + ": lettura di una versione precedente alla cura, in cui i titoli senza dati "
                    "potevano risultare inclusi; peso escluso, p esatti e bassa potenza " + _ANTE_CURA + ".")
    if block["reliable"] is False:
        # la frase nomina la causa VERA: esclusi sopra soglia, oppure perimetro mancante nei
        # giorni testati (esclusi anche 0%), altrimenti il dettaglio del tool
        soglia = " (soglia " + _n(threshold, 0) + "%)" if threshold is not None else ""
        if weight is not None and threshold is not None and weight > threshold:
            cause = "i titoli esclusi pesano il " + _n(weight) + "% del perimetro" + soglia
        elif missing is not None and threshold is not None and missing > threshold:
            cause = ("nei giorni testati manca in media il " + _n(missing) + "% del perimetro" + soglia
                     + (", per i titoli presenti solo in parte dei giorni: "
                        + _elenco([t + " " + v.replace("/", " giorni su ") for t, v in partial.items()])
                        if partial else ""))
        else:
            details = [lv["verdict_detail"] for lv in block["levels"].values() if lv and lv["verdict_detail"]]
            cause = ("motivo dichiarato dal calcolo: " + details[0].rstrip(".") if details
                     else "motivo non dichiarato")
        gaps.append(label + ": NON AFFIDABILE, " + cause
                    + ": l'esito non valida il VaR di questo portafoglio.")
    for conf, level in block["levels"].items():
        if level is None:
            gaps.append(label + ": esito al " + conf + "% non disponibile.")
            continue
        verdict = level["verdict"] or "non dichiarato"
        if verdict == "PASS" and not level["low_power"] and new:
            continue
        axes = []
        failed = []
        for key, name, why in _ASSI:
            axis = level[key]
            axes.append(name + " " + (axis["verdict"] or "n.d.")
                        + (" (p " + _n(axis["p_value"], 4) + ")" if axis["p_value"] is not None else ""))
            if axis["verdict"] == "FAIL":
                failed.append((name, why))
        if verdict == "NON AFFIDABILE":
            head = "NON AFFIDABILE (esito statistico non valido per questo portafoglio)"
        elif verdict == "FAIL" and failed:
            head = ("FAIL sull'asse " + " e ".join(n for n, _ in failed) + " ("
                    + "; ".join(w for _, w in failed) + ")")
        elif verdict == "FAIL":
            head = "FAIL senza indicazione dell'asse che fallisce"
        else:
            head = verdict
        text = (label + " al " + conf + "%: " + head + "; " + ", ".join(axes))
        if level["exceptions"] is not None and level["expected_exceptions"] is not None:
            text += "; " + str(level["exceptions"]) + " eccezioni contro " + _n(level["expected_exceptions"], 1) + " attese"
        if level["low_power"]:
            text += "; verdetto indicativo per bassa potenza (poche eccezioni attese)"
        elif level["low_power"] is None:
            text += "; potenza del test " + _ANTE_CURA
        gaps.append(text + "; " + perimeter + ".")
    return block


def _portfolio(picker, gaps):
    label = "Portafoglio"
    receipt, got = picker.pick("get_portfolio_live", label, lambda r, p: None)
    if receipt is None:
        return None, set()
    parsed, data = got
    nav = _num(data.get("nav_total_eur"))
    cash = _num(data.get("cash_disponibile_eur"))
    if cash is not None and not data.get("cash_source"):
        # memory_db: cash_source None = lo zero e' un buco, non una cassa vuota
        gaps.append(label + ": la cassa non ha una fonte registrata e non è riportata "
                    "(uno zero qui sarebbe un buco, non una cassa vuota).")
        cash = None
    elif cash is None:
        gaps.append(label + ": cassa non disponibile.")
    fx = _as_list(data.get("fx_incomplete"))
    if fx:
        gaps.append(label + ": manca il cambio per " + _elenco(fx)
                    + ", quindi i totali in euro non sono affidabili.")
    if nav is None:
        gaps.append(label + ": valore totale non disponibile.")
    elif nav == 0:
        gaps.append(label + ": il valore totale risulta zero, quindi la quota di cassa non è calcolabile.")
    # cash_pct derivato dalla STESSA ricevuta (cassa / NAV, in punti)
    cash_pct = round(cash / nav * 100, 2) if cash is not None and nav else None
    as_of = _iso_or_none(data.get("timestamp")) or _iso_or_none(parsed.get("_timestamp"))
    positions = data.get("positions") if isinstance(data.get("positions"), list) else []
    holdings = {_norm_ticker(p.get("ticker")) for p in positions
                if isinstance(p, dict) and _norm_ticker(p.get("ticker"))}
    return {"nav": nav, "cash": cash, "cash_pct": cash_pct,
            "source": data.get("source") or _source(receipt, parsed),
            "tool": "get_portfolio_live", "as_of": as_of}, holdings


def _benchmark(picker, ticker):
    """Benchmark dichiarato dall'ultima ricevuta quant_compute 'beta' del ticker (o None)."""
    for receipt in reversed(picker.receipts):
        given = receipt.get("input")
        if (receipt.get("tool") == "quant_compute" and receipt.get("success")
                and isinstance(given, dict) and given.get("operation") == "beta"):
            if ticker in {_norm_ticker(t) for t in _as_list(given.get("tickers"))}:
                return _norm_ticker(_params(given).get("benchmark", "SPY"))
    return None


def _returns(picker, ticker, holdings, benchmark, gaps):
    """compare_assets: si UNISCONO le ricevute valide con la STESSA finestra dell'ultima.

    Per ticker vale la ricevuta piu' recente; l'unione e' dichiarata in ``note``.
    Ruolo: candidate (ticker della run), benchmark (quello del beta quant_compute),
    holding (posizione in get_portfolio_live), altrimenti None (non deducibile: un
    nome confrontato non e' per forza un peer, e non lo si indovina).
    """
    label = "Confronto dei rendimenti"

    def accept(receipt, payload):
        return None if isinstance(payload.get("comparison"), list) else "comparison assente"

    last, got = picker.pick("compare_assets", label, accept)
    if last is None:
        return None
    window = got[1].get("period")
    merged, used, skipped = {}, [], 0
    for receipt, parsed, payload in picker.valid("compare_assets", accept):  # cronologico
        if payload.get("period") != window:
            skipped += 1
            continue
        used.append((receipt, parsed))
        for row in payload["comparison"]:
            name = _norm_ticker(row.get("ticker")) if isinstance(row, dict) else None
            if name:
                merged[name] = row
    assets, unknown = [], []
    for name, row in merged.items():
        role = ("candidate" if name == ticker else "benchmark" if name == benchmark
                else "holding" if name in holdings else None)
        if role is None:
            unknown.append(name)
        assets.append({"ticker": name, "return_pct": _num(row.get("performance_periodo_pct")),
                       "vol_pct": _num(row.get("volatilita_annualizzata_pct")), "role": role})
    if ticker not in merged:
        gaps.append(label + ": il titolo analizzato non compare nel confronto.")
    if unknown:
        gaps.append(label + ": per " + _elenco(unknown) + " non è registrato il ruolo "
                    "(concorrente o altro), quindi non sono etichettati.")
    gaps.append(label + ": la data degli ultimi prezzi non è dichiarata; "
                "vale come riferimento l'ora del calcolo.")
    return {"window": window, "assets": assets,
            "note": ("unione di " + str(len(used)) + " confronti sullo stesso periodo ("
                     + _finestra(window) + "); per ogni titolo vale il più recente"
                     + ("; esclusi " + str(skipped) + " confronti su un periodo diverso" if skipped else "")),
            "source": _source(last, got[0]), "tool": "compare_assets",
            "as_of": _latest([p.get("_timestamp") or r.get("timestamp") for r, p in used])}


def _exposure(picker, gaps):
    label = "Esposizione per settore"
    receipt, got = picker.pick("get_sector_exposure", label,
                               lambda r, p: None if isinstance(p.get("by_sector"), list)
                               else "by_sector assente")
    if receipt is None:
        return None
    parsed, data = got
    sectors = [{"name": row.get("sector"), "weight_pct": _num(row.get("weight_pct"))}
               for row in data["by_sector"] if isinstance(row, dict)]
    econ = data.get("econ_axis") if isinstance(data.get("econ_axis"), dict) else {}
    raw_buckets = econ.get("by_bucket") if isinstance(econ.get("by_bucket"), list) else []
    buckets = [{"name": row.get("bucket"), "weight_pct": _num(row.get("weight_pct"))}
               for row in raw_buckets if isinstance(row, dict)]
    if not buckets:
        gaps.append(label + ": manca la vista per area economica.")
    return {"sectors": sectors, "econ_buckets": buckets,
            "hhi_sector": _num(data.get("hhi_sector")),
            "effective_n_sectors": _num(data.get("effective_n_sectors")),
            "source": _source(receipt, parsed), "tool": "get_sector_exposure",
            # calcolo interno sul book: l'ora di calcolo E' l'osservazione
            "as_of": _iso_or_none(data.get("timestamp")) or _iso_or_none(parsed.get("_timestamp"))}


def _mc_notes(data, gaps):
    """Note del Monte Carlo in italiano piano (una frase per nota, i doppioni li toglie extract_facts)."""
    label = "Monte Carlo del portafoglio"
    note = data.get("calibration_note")
    # formato nuovo: la nota ripete il campione strutturato, che _mc_tail_notes scrive separando
    # ticker limitante e calendari (la regex vecchia attribuiva tutto al ticker giovane)
    if isinstance(note, str) and note.strip() and not isinstance(data.get("calibration_sample"), dict):
        match = re.search(r"(\d+)\s*obs dal ticker pi\S* giovane \(([^:]+):\s*(\d+)\s*obs su (\d+) del panel (\w+)\)",
                          note)
        if match:
            gaps.append(label + " calibrato su soli " + match.group(1) + " giorni perché il titolo più "
                        "recente del book (" + match.group(2).strip() + ") ha storia corta ("
                        + match.group(3) + " giorni su " + match.group(4) + " della finestra di "
                        + _finestra(match.group(5)) + "): volatilità e correlazioni sono stimate "
                        "su una finestra breve.")
        else:
            gaps.append(label + ": " + note.strip().rstrip(".") + ".")
    basis = data.get("returns_basis")
    if isinstance(basis, str) and basis.strip():
        if "LOCALE" in basis.upper() and "FX" in basis.upper():
            gaps.append(label + ": i rendimenti sono simulati nella valuta di ciascun titolo, senza "
                        "l'effetto del cambio; i valori in euro sono solo riproporzionati al patrimonio.")
        else:
            gaps.append(label + ": " + basis.strip().rstrip(".") + ".")
    fallback = _as_list(data.get("garch_fallback_assets"))
    if fallback:
        gaps.append(label + ": per " + _elenco(fallback) + " la volatilità è stimata con un metodo "
                    "semplificato perché il modello principale (GARCH) non è riuscito.")


def _mc_calibration(data):
    """Campione di calibrazione dichiarato dal tool (formato nuovo) o n.d. (run precedente)."""
    sample = data.get("calibration_sample")
    warning = _testo(data.get("tail_reliability_warning"))
    if not isinstance(sample, dict):
        return {"calibration": {"observations": None, "start_date": None, "end_date": None,
                                "limiting_ticker": None, "limiting_ticker_start_date": None,
                                "obs_dropped_by_limiting_ticker": None, "obs_dropped_by_calendars": None,
                                "min_obs_reliable_tails": None, "low_tail_reliability": None,
                                "missing_days_by_ticker": None, "truncated_by_tool": None,
                                "sentence": None, "status": _ANTE_CURA},
                "tail_reliability_warning": warning}
    low = sample.get("low_tail_reliability")
    holes = sample.get("missing_days_in_window_by_ticker")
    holes = ({str(k): _int_if_whole(v) for k, v in holes.items() if _int_if_whole(v)}
             if isinstance(holes, dict) else None)   # None = campo assente (versione intermedia)
    return {"calibration": {
        "observations": _int_if_whole(sample.get("n_obs")),
        "start_date": sample.get("start_date"), "end_date": sample.get("end_date"),
        "limiting_ticker": _testo(sample.get("limiting_ticker")),
        "limiting_ticker_start_date": sample.get("limiting_ticker_start_date"),
        "obs_dropped_by_limiting_ticker": _int_if_whole(sample.get("obs_dropped_by_limiting_ticker")),
        "obs_dropped_by_calendars": _int_if_whole(sample.get("obs_dropped_by_calendars")),
        "min_obs_reliable_tails": _int_if_whole(sample.get("min_obs_reliable_tails")),
        "low_tail_reliability": low if isinstance(low, bool) else None,
        "missing_days_by_ticker": holes,
        # il tool scrive calibration_note SOLO quando il campione e' tagliato (ticker giovane o
        # giorni mancanti dentro le serie): la frase si scrive dai campi strutturati qui sotto
        "truncated_by_tool": bool(_testo(data.get("calibration_note"))),
        # frase del tool tale e quale (date ISO): i gap la riscrivono dai campi strutturati
        "sentence": _testo(sample.get("sentence")), "status": None},
        "tail_reliability_warning": warning}


def _mc_excluded(data):
    """Titoli tolti dalla simulazione per storia insufficiente; None = campo assente (run precedente)."""
    rows = data.get("tickers_excluded_insufficient_history")
    if not isinstance(rows, list):
        return None
    return [{"ticker": row.get("ticker"), "observations": _int_if_whole(row.get("n_obs"))}
            for row in rows if isinstance(row, dict)]


def _mc_excluded_text(rows):
    return ("esclusi dalla simulazione per storia insufficiente "
            + _elenco([(r["ticker"] or "titolo senza nome")
                       + (" (" + str(r["observations"]) + " rendimenti validi)" if r["observations"] is not None else "")
                       for r in rows]))


def _mc_pro_forma_error(picker, accept, after, label, gaps):
    """Pro-forma rifiutato dal tool (errore con allegati): dichiarato, MAI un ES.

    Si guarda il tentativo pro-forma piu' recente; conta solo se e' successivo alla lettura
    valida usata (``after``) o se una lettura valida non c'e'.
    """
    last = None
    for receipt in picker.receipts:
        if (receipt.get("tool") != "get_portfolio_montecarlo" or not receipt.get("success")
                or not picker._in_window(receipt)):
            continue
        parsed, payload = _parse_output(receipt)
        if isinstance(payload, dict) and payload.get("error") and not accept(receipt, payload):
            last = (receipt, parsed, payload)
    if last is None:
        return None
    receipt, parsed, payload = last
    if after is not None:
        when, used = _parse_ts(receipt.get("timestamp")), _parse_ts(after.get("timestamp"))
        if when is None or used is None or when <= used:
            return None
    reason = _testo(str(payload.get("error"))) or "motivo non dichiarato"
    sample = payload.get("calibration_sample") if isinstance(payload.get("calibration_sample"), dict) else {}
    alloc = payload.get("what_if_allocation") if isinstance(payload.get("what_if_allocation"), dict) else {}
    block = {"reason": reason, "excluded_insufficient_history": _mc_excluded(payload),
             "calibration_observations": _int_if_whole(sample.get("n_obs")),
             "allocation_origin": _testo(alloc.get("origin")),
             "source": _source(receipt, parsed), "tool": "get_portfolio_montecarlo",
             "as_of": _iso_or_none(payload.get("timestamp")) or _iso_or_none(parsed.get("_timestamp"))}
    text = label + (": pro-forma non calcolabile nella simulazione del " + _quando(receipt.get("timestamp"))
                    + " (" + reason.rstrip(".") + ")")
    if block["excluded_insufficient_history"]:
        text += "; " + _mc_excluded_text(block["excluded_insufficient_history"])
    if after is not None:
        text += "; resta riportata la simulazione precedente del " + _quando(after.get("timestamp"))
    else:
        text += "; nessuna perdita estrema del titolo aggiunto è riportata"
    gaps.append(text + ".")
    return block


def _mc_tail_text(block):
    """Inciso sull'affidabilita' delle code (VaR/ES al 99%) del blocco, o None se affidabili."""
    cal = block["calibration"]
    if cal["status"]:
        return "affidabilità delle code " + cal["status"]
    if cal["low_tail_reliability"] or block["tail_reliability_warning"]:
        return ("code poco affidabili: calibrazione su "
                + (str(cal["observations"]) if cal["observations"] is not None else "n.d.")
                + " osservazioni, sotto la soglia di "
                + (str(cal["min_obs_reliable_tails"]) if cal["min_obs_reliable_tails"] is not None else "n.d.")
                + " (circa un anno di borsa)")
    return None


def _mc_tail_notes(label, block, gaps):
    """Campione di calibrazione in italiano piano + avviso code se n sotto soglia."""
    cal = block["calibration"]
    if cal["status"] or cal["observations"] is None:
        return
    if (cal["limiting_ticker"] or cal["low_tail_reliability"] or block["tail_reliability_warning"]
            or cal["truncated_by_tool"] or cal["missing_days_by_ticker"]):
        n = cal["observations"]
        text = (label + " calibrato su " + str(n) + " giorni di borsa (circa " + _n(n / 21, 0)
                + " mesi, dal " + _data_it(cal["start_date"]) + " al " + _data_it(cal["end_date"]) + ")")
        if cal["limiting_ticker"]:
            text += (", finestra limitata da " + cal["limiting_ticker"] + " (storia dal "
                     + _data_it(cal["limiting_ticker_start_date"]) + ": "
                     + str(cal["obs_dropped_by_limiting_ticker"]) + " giorni in meno)")
        if cal["obs_dropped_by_calendars"]:
            text += ("; altri " + str(cal["obs_dropped_by_calendars"]) + " giorni tolti perché almeno un "
                     "titolo non quotava (calendari di borsa diversi o buchi di dati)")
        holes = cal["missing_days_by_ticker"]
        if holes:
            ordered = sorted(holes.items(), key=lambda kv: (-kv[1], kv[0]))
            text += ("; giorni mancanti dentro la finestra: "
                     + _elenco([t + " " + str(d) for t, d in ordered]))
        gaps.append(text + ".")
    tail = _mc_tail_text(block)
    if tail:
        gaps.append(label + ": " + tail + "; VaR ed ES al 99% vanno citati con questa riserva.")


_ORIGINE = {"stress_fisso": "valore fisso di stress, NON la size proposta",
            "parametro_chiamante": "quota passata alla simulazione come size proposta"}


def _mc_allocation_notes(label, block, gaps):
    """L'ES al 99% del pro-forma esce SEMPRE con la quota simulata e la sua origine."""
    alloc = block["allocation"]
    weight = block["candidate_weight_pct"]
    if alloc["status"]:
        origin = "origine della quota " + alloc["status"] + ": non è registrato se sia la size proposta"
    else:
        origin = _ORIGINE.get(alloc["origin"]) or ("origine della quota: " + (alloc["origin"] or "non dichiarata"))
    share = ("con il titolo al " + _n(weight) + "% del portafoglio" if weight is not None
             else "con un peso del titolo non indicato")
    es99 = block.get("es_99_pct")
    tail = _mc_tail_text(block)
    gaps.append(label + ": " + ("la perdita media nel caso peggiore (ES al 99%), " + _n(es99) + "%, "
                                if es99 is not None else "lo scenario ")
                + "si riferisce a una simulazione " + share + " (" + origin + ")"
                + ("; " + tail if tail else "") + ".")
    requested = alloc["weight_pct_requested"]
    if requested is not None and weight is not None and abs(requested - weight) > 0.005:
        gaps.append(label + ": quota richiesta " + _n(requested) + "%, peso effettivo nella simulazione "
                    + _n(weight) + "% dopo la rinormalizzazione del portafoglio.")
    if alloc["tickers_skipped"]:
        gaps.append(label + ": " + _elenco(alloc["tickers_skipped"]) + " è nella lista delle posizioni "
                    "escluse dal calcolo e non è entrato nella simulazione.")
    if alloc["tickers_without_returns"]:
        gaps.append(label + ": " + _elenco(alloc["tickers_without_returns"]) + " non ha una serie di "
                    "rendimenti, quindi il suo peso effettivo nella simulazione è zero.")


def _montecarlo(picker, ticker, gaps):
    """Due scenari distinti, mai mescolati: book attuale e pro-forma col candidato aggiunto.

    La run del book abbinata e' la PIU' VICINA nel tempo alla pro-forma (stesso mercato,
    stessa calibrazione); senza pro-forma vale l'ultima del book.
    """
    def tickers_of(given, key):
        return {_norm_ticker(t) for t in _as_list(given.get(key)) if _norm_ticker(t)}

    def accept_for(kind):
        def accept(receipt, payload):
            given = receipt.get("input") if isinstance(receipt.get("input"), dict) else {}
            if str(given.get("stress") or "none") != "none" or payload.get("stress_scenario") not in (None, "none"):
                return "scenario di stress"
            added, removed = tickers_of(given, "add_tickers"), tickers_of(given, "remove_tickers")
            if kind == "book":
                return None if not added and not removed else "what-if"
            return None if added == {ticker} and not removed else "altro what-if"
        return accept

    def build(kind, sub_label, receipt, parsed, data):
        block = {"horizon_days": _num(data.get("horizon_days")), "n_sims": _num(data.get("n_sims")),
                 "method": data.get("method"), "observations": _num(data.get("lookback_days_calibration")),
                 "n_assets": _num(data.get("n_assets"))}
        for name in _MC_PCT_FIELDS:   # gia' in punti % nel tool
            block[name] = _num(data.get(name))
        ratios = data.get("percentiles_ratio") if isinstance(data.get("percentiles_ratio"), dict) else {}
        # rapporto NAV finale / NAV iniziale (1.0 = invariato), come dal tool: non sono punti %
        block["terminal_ratio_percentiles"] = {k: _num(v) for k, v in ratios.items()}
        block.update(_mc_calibration(data))
        block["excluded_insufficient_history"] = _mc_excluded(data)
        if block["excluded_insufficient_history"]:
            gaps.append(sub_label + ": " + _mc_excluded_text(block["excluded_insufficient_history"]) + ".")
        if kind == "pro_forma":
            alloc = data.get("what_if_allocation")
            if isinstance(alloc, dict):
                # peso VERO del candidato nella simulazione (dopo la rinormalizzazione), gia' in punti
                block["candidate_weight_pct"] = _num(alloc.get("added_weight_pct_effective"))
                block["allocation"] = {
                    "origin": _testo(alloc.get("origin")),
                    "is_proposed_size": (alloc.get("is_proposed_size")
                                         if isinstance(alloc.get("is_proposed_size"), bool) else None),
                    "weight_pct_requested": _num(alloc.get("added_weight_pct_requested")),
                    "weight_pct_effective": block["candidate_weight_pct"],
                    "note": _testo(alloc.get("note")),
                    "tickers_skipped": _as_list(alloc.get("added_tickers_skipped")),
                    "tickers_without_returns": _as_list(alloc.get("added_tickers_without_returns")),
                    "status": None}
            else:
                weights = data.get("weights") if isinstance(data.get("weights"), dict) else {}
                weight = _num(weights.get(ticker))
                block["candidate_weight_pct"] = round(weight * 100, 4) if weight is not None else None  # frazione -> punti
                block["allocation"] = {"origin": None, "is_proposed_size": None, "weight_pct_requested": None,
                                       "weight_pct_effective": None, "note": None, "tickers_skipped": [],
                                       "tickers_without_returns": [], "status": _ANTE_CURA}
            if block["candidate_weight_pct"] is None:
                gaps.append(sub_label + ": non è indicato il peso dato al titolo nella simulazione.")
            _mc_allocation_notes(sub_label, block, gaps)
        _mc_notes(data, gaps)
        _mc_tail_notes(sub_label, block, gaps)
        sentence = _mancano(sub_label, [n for n in ("horizon_days", "var_99_pct", "es_99_pct")
                                        if block[n] is None])
        if sentence:
            gaps.append(sentence)
        block.update(source=_source(receipt, parsed), tool="get_portfolio_montecarlo",
                     as_of=_iso_or_none(data.get("timestamp")) or _iso_or_none(parsed.get("_timestamp")))
        return block

    blocks = {"book": None, "pro_forma": None}
    pro_label, book_label = "Monte Carlo con il titolo aggiunto", "Monte Carlo del portafoglio attuale"
    pro_receipt, pro_got = picker.pick("get_portfolio_montecarlo", pro_label, accept_for("pro_forma"))
    if pro_receipt is not None:
        blocks["pro_forma"] = build("pro_forma", pro_label, pro_receipt, *pro_got)
    book_receipt, book_got = picker.pick("get_portfolio_montecarlo", book_label, accept_for("book"))
    if book_receipt is not None and pro_receipt is not None:
        anchor = _parse_ts(pro_receipt.get("timestamp"))
        options = picker.valid("get_portfolio_montecarlo", accept_for("book"))
        if anchor is not None and options:
            def distance(option):
                stamp = _parse_ts(option[0].get("timestamp"))
                return abs((stamp - anchor).total_seconds()) if stamp is not None else math.inf
            closest = min(reversed(options), key=distance)   # a pari distanza vince la piu' recente
            if distance(closest) != math.inf:
                book_receipt, book_got = closest[0], (closest[1], closest[2])
    if book_receipt is not None:
        blocks["book"] = build("book", book_label, book_receipt, *book_got)
    pro_error = _mc_pro_forma_error(picker, accept_for("pro_forma"), pro_receipt, pro_label, gaps)
    if blocks["book"] is None and blocks["pro_forma"] is None and pro_error is None:
        return None
    parts = [b for b in blocks.values() if b] or [pro_error]
    return {"book": blocks["book"], "pro_forma": blocks["pro_forma"], "pro_forma_error": pro_error,
            "source": parts[0]["source"], "tool": "get_portfolio_montecarlo",
            "as_of": _earliest([b.get("as_of") for b in parts])}


def _fiscal_year_check(history, consensus, gaps):
    """Anno fiscale di «0y» SOLO se i ricavi «anno scorso» coincidono con un anno dello storico."""
    if not history or not consensus:
        return
    revenue = history["series"].get("revenue") or {}
    last = max(revenue) if revenue else None
    year_ago = consensus.get("year_ago_revenue")
    if last is None or year_ago is None:
        gaps.append("Anno fiscale delle stime degli analisti non determinabile: manca il confronto "
                    "fra i ricavi dell'anno scorso indicati dagli analisti e lo storico di bilancio.")
        return
    matches = [y for y, v in revenue.items() if v and abs(year_ago - v) / abs(v) <= _FY_MATCH_TOLERANCE]
    if matches:
        consensus["current_fiscal_year"] = max(matches) + 1
        return
    unit = history.get("unit")
    gaps.append("Storico di bilancio fermo al " + str(last) + ": i ricavi dell'anno scorso indicati dagli "
                "analisti (" + _importo(year_ago, unit) + ") non coincidono con quelli del " + str(last)
                + " (" + _importo(revenue[last], unit) + "), quindi fra lo storico e le stime dell'anno "
                "in corso manca almeno un esercizio.")


# ---------------------------------------------------------------- ingresso

BLOCKS = ("history", "quote", "fundamentals", "consensus", "risk", "returns",
          "exposure", "montecarlo", "portfolio")


def _dedup(gaps):
    seen, out = set(), []
    for gap in gaps:
        if gap not in seen:
            seen.add(gap)
            out.append(gap)
    return out


def extract_facts(checkpoint, *, cutoff=None) -> dict:
    """Blocchi numerici della run dalle ricevute dei tool (v. docstring del modulo)."""
    gaps = []
    out = {key: None for key in BLOCKS}
    if not isinstance(checkpoint, dict):
        out.update(currency=None, gaps=["La run non contiene dati leggibili: nessun numero estratto."])
        return {key: out[key] for key in ("currency",) + BLOCKS + ("gaps",)}
    data = checkpoint.get("data") if isinstance(checkpoint.get("data"), dict) else {}
    identity = data.get("_identity") if isinstance(data.get("_identity"), dict) else {}
    ticker = _norm_ticker(identity.get("ticker"))
    currency = identity.get("currency") if isinstance(identity.get("currency"), str) else None
    contract = checkpoint.get("contract") if isinstance(checkpoint.get("contract"), dict) else {}
    contract_ticker = _norm_ticker(contract.get("ticker"))
    conflict = False
    if ticker is None and contract_ticker:
        ticker = contract_ticker
        gaps.append("Identità del titolo non registrata nella run: si usa il ticker indicato "
                    "alla richiesta (" + ticker + ").")
    elif ticker and contract_ticker and ticker != contract_ticker:
        gaps.append("Identità del titolo incoerente (" + ticker + " accettato, " + contract_ticker
                    + " richiesto): i dati del titolo non sono estratti.")
        ticker, conflict = None, True
    if currency is None:
        gaps.append("Valuta del titolo non registrata nella run.")

    picker = _Picker(_ordered_receipts(checkpoint), cutoff, gaps)
    if ticker is None and not conflict:
        gaps.append("Ticker della run non registrato: storico, quotazione, indicatori, consensus, "
                    "rischio, rendimenti e Monte Carlo del titolo non sono estratti.")
    out["portfolio"], holdings = _portfolio(picker, gaps)
    out["exposure"] = _exposure(picker, gaps)
    if ticker is not None:
        out["history"] = _history(picker, ticker, currency, gaps)
        out["quote"] = _quote(picker, ticker, gaps)
        out["fundamentals"] = _fundamentals(picker, ticker, gaps)
        out["consensus"] = _consensus(picker, ticker, currency, gaps)
        _fiscal_year_check(out["history"], out["consensus"], gaps)
        out["risk"] = _risk(picker, ticker, gaps)
        out["returns"] = _returns(picker, ticker, holdings, _benchmark(picker, ticker), gaps)
        out["montecarlo"] = _montecarlo(picker, ticker, gaps)
    out.update(currency=currency, gaps=_dedup(gaps))
    return {key: out[key] for key in ("currency",) + BLOCKS + ("gaps",)}
