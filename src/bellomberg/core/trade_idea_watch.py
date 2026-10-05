"""Trigger di revisione Trade Idea: logica pura (TI-RESEARCH-PIPELINE, Opus 5.5).

Nessun I/O: niente DB, rete, email o orologio. Lo store e il worker iniettano
righe, quotazioni, data di oggi e run attive; qui si decide solo COSA e' scattato.

Decisioni PM 04/10: trigger automatici solo DATE e PREZZI; le condition sono
promemoria alla data del prossimo trigger kind=date della stessa run (i
catalizzatori non hanno date); nessun rilancio automatico, nessuna spesa.
Ogni buco (riferimento assente, valuta diversa, quotazione vecchia, run attiva,
condizione senza data) e' DICHIARATO come stato/verdetto, mai riempito.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Mapping

from bellomberg.core.language import text, validate_language
from bellomberg.core.trade_idea_contract import ReviewTriggerV4
from bellomberg.valuation.market_quote import _freshness

# Parametri (scelte §9 di R3 in attesa di conferma PM: si cambiano qui).
WATCH_JUDGMENTS = frozenset({"watch"})          # §9.4: solo esito watch
REFERENCE_READY = "ready"                        # stato della ricevuta candidate_quote_receipts.final
QUOTE_OK = "ok"
DIRECTIONS = ("at_or_above", "at_or_below")
VERDICTS = ("fire", "remind", "wait", "stale", "unavailable", "deferred", "blocked")
ROW_KEYS = ("trigger_index", "kind", "date", "price_level", "price_currency", "direction",
            "reference_price", "reference_asof", "reference_source", "condition",
            "reminder_date", "what", "status", "status_reason")
_TIMING_MAX = 2000
# Guardia contro unita' diverse (GBX/GBP, valute): livello/riferimento fuori banda = blocked
# dichiarato. Soglia tecnica, rivedibile dal PM (main 04/10).
PRICE_RATIO_BAND = (Decimal("0.2"), Decimal("5"))


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: Decimal | None
    currency: str | None
    asof: str | None          # giorno YYYY-MM-DD del daily close
    source: str | None
    status: str               # ok | stale | data_missing | source_unavailable | unavailable ...
    reason: str | None
    bar_label: str | None = None   # etichetta breve per il PM (chiusura/intraday); source resta grezza


@dataclass(frozen=True)
class WatchRow:
    id: int
    run_id: str
    ticker: str
    kind: str
    date: str | None
    price_level: float | None
    price_currency: str | None
    direction: str | None
    condition: str | None
    reminder_date: str | None
    what: str
    status: str


@dataclass(frozen=True)
class Outcome:
    row_id: int
    verdict: str              # fire|remind|wait|stale|unavailable|deferred|blocked
    reason: str
    observation: dict = field(default_factory=dict)


def _day(value, label):
    if type(value) is not date:
        raise TypeError(f"{label} deve essere datetime.date, non {type(value).__name__}")
    return value


def _iso_day(value):
    """Data di calendario ISO esatta o None (mai una data indovinata)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def eligibility(result) -> str | None:
    """None se la run va importata; altrimenti il motivo (dichiarato dallo store)."""
    if not isinstance(result, dict):
        return "result_json assente o non oggetto"
    judgment = result.get("judgment")
    if judgment not in WATCH_JUDGMENTS:
        return f"giudizio {judgment!r} fuori dagli esiti sorvegliati {sorted(WATCH_JUDGMENTS)}"
    if "review_triggers" not in result:
        return "review_triggers assenti: risultato non /4"
    return None


def _reference_view(reference, run_currency):
    """(prezzo, asof, fonte, motivo_blocco). Il motivo e' None solo se usabile."""
    if not isinstance(reference, dict):
        return None, None, None, "prezzo di riferimento della run assente"
    asof, source = reference.get("price_asof"), reference.get("source")
    asof = asof if isinstance(asof, str) else None
    source = source if isinstance(source, str) else None
    if reference.get("status") != REFERENCE_READY:
        return None, asof, source, f"prezzo di riferimento non pronto (status={reference.get('status')!r})"
    price = _decimal(reference.get("price"))
    if price is None:
        return None, asof, source, "prezzo di riferimento non numerico o non positivo"
    if not run_currency:
        return price, asof, source, "valuta della run assente: livello senza valuta"
    if reference.get("currency") != run_currency:
        return price, asof, source, (f"valuta del riferimento {reference.get('currency')!r} "
                                     f"diversa dalla valuta della run {run_currency!r}")
    return price, asof, source, None


def _scenario_currency_block(result, run_currency):
    """Motivo di blocco se gli scenari dichiarano valute diverse da quella della run."""
    scenarios = result.get("scenarios")
    if not isinstance(scenarios, list):
        return None
    found = {s.get("currency") for s in scenarios if isinstance(s, dict) and s.get("currency") is not None}
    if not found:
        return None
    if len(found) > 1:
        return f"scenari in valute diverse {sorted(map(str, found))}: valuta del livello incerta"
    (only,) = found
    if only != run_currency:
        return f"scenari in {only!r} ma run in {run_currency!r}: valuta del livello incerta"
    return None


def rows_from_result(result: dict, *, run: dict, reference: dict | None, today: date) -> list[dict]:
    """Trigger /4 validati col contratto -> righe da inserire (chiavi = ROW_KEYS).

    run_id/decision_id/ticker/created_at li aggiunge lo store. Solleva ValueError
    se la run non e' idonea o un trigger non rispetta ReviewTriggerV4 (mai dati grezzi).
    """
    today = _day(today, "today")
    reason = eligibility(result)
    if reason is not None:
        raise ValueError(reason)
    raw = result["review_triggers"]
    if not isinstance(raw, list) or not raw:
        raise ValueError("review_triggers vuoti o non lista: un esito watch ne richiede almeno uno")
    if len(raw) > 10:
        raise ValueError("review_triggers oltre il massimo del contratto (10)")
    triggers = []
    for index, item in enumerate(raw):
        try:
            triggers.append(ReviewTriggerV4.model_validate(item))
        except Exception as exc:
            raise ValueError(f"review_triggers[{index}] non valido per ReviewTriggerV4: "
                             f"{type(exc).__name__}") from exc

    run_currency = run.get("currency") if isinstance(run, dict) else None
    ref_price, ref_asof, ref_source, ref_block = _reference_view(reference, run_currency)
    if ref_block is None:
        ref_block = _scenario_currency_block(result, run_currency)
    future_dates = sorted(t.date for t in triggers if t.kind == "date" and date.fromisoformat(t.date) >= today)
    reminder = future_dates[0] if future_dates else None

    rows = []
    for index, trig in enumerate(triggers):
        row = {key: None for key in ROW_KEYS}
        row.update(trigger_index=index, kind=trig.kind, what=trig.what, status="active")
        if trig.kind == "date":
            row["date"] = trig.date
            if date.fromisoformat(trig.date) < today:
                row["status_reason"] = (f"data {trig.date} gia' passata al popolamento "
                                        f"({today.isoformat()}): scatta al primo giro")
        elif trig.kind == "price":
            row.update(price_level=trig.price_level, price_currency=run_currency or None,
                       reference_price=float(ref_price) if ref_price is not None else None,
                       reference_asof=ref_asof, reference_source=ref_source)
            level = Decimal(str(trig.price_level))
            low, high = PRICE_RATIO_BAND
            if ref_block is not None:
                row.update(status="blocked", status_reason=ref_block)
            elif not low <= level / ref_price <= high:
                row.update(status="blocked", status_reason=(
                    f"livello {level} contro riferimento {ref_price} {run_currency}: rapporto fuori "
                    f"banda {low}-{high}, possibile unita' diversa (pence/sterline): non sorvegliato"))
            elif level > ref_price:
                row["direction"] = "at_or_above"
            elif level < ref_price:
                row["direction"] = "at_or_below"
            else:
                row.update(status="blocked", status_reason=(
                    f"livello {level} uguale al riferimento {ref_price}: direzione indeterminata"))
        else:
            row["condition"] = trig.condition
            if reminder is None:
                row.update(status="undated", status_reason=(
                    "nessun trigger kind=date futuro nella run: promemoria senza data (nessuna data inventata)"))
            else:
                row["reminder_date"] = reminder
        rows.append(row)
    return rows


def _price_verdict(row, quote, today):
    if row.direction not in DIRECTIONS:
        return "blocked", "direzione del livello assente o non valida", {}
    level = _decimal(row.price_level)
    if level is None:
        return "blocked", "livello di prezzo non valido", {}
    if not row.price_currency:
        return "blocked", "valuta del livello assente", {}
    if quote is None:
        return "unavailable", "nessuna quotazione fornita per il ticker", {}
    obs = {"kind": "price", "price": str(quote.price) if quote.price is not None else None,
           "currency": quote.currency, "asof": quote.asof, "source": quote.source,
           "bar_label": quote.bar_label, "quote_status": quote.status, "level": str(level), "direction": row.direction}
    if quote.ticker != row.ticker:
        return "unavailable", f"quotazione per {quote.ticker!r}, non per {row.ticker!r}", obs
    if quote.status == "stale":
        return "stale", f"quotazione vecchia (asof {quote.asof}): {quote.reason or 'stale'}", obs
    if quote.status != QUOTE_OK:
        return "unavailable", f"quotazione {quote.status}: {quote.reason or 'n.d.'}", obs
    freshness = _freshness(quote.asof, today.isoformat())
    if freshness != "ok":
        return ("stale" if freshness == "stale" else "unavailable",
                f"freschezza ricalcolata {freshness} (asof {quote.asof}, oggi {today.isoformat()})", obs)
    price = _decimal(quote.price)
    if price is None:
        return "unavailable", "prezzo quotazione assente o non positivo", obs
    if quote.currency != row.price_currency:
        return "blocked", (f"valuta quotazione {quote.currency!r} diversa dalla valuta "
                           f"del livello {row.price_currency!r}"), obs
    crossed = price >= level if row.direction == "at_or_above" else price <= level
    if crossed:
        return "fire", f"prezzo {price} {quote.currency} ha raggiunto {level} ({row.direction})", obs
    return "wait", f"prezzo {price} {quote.currency} non ha raggiunto {level} ({row.direction})", obs


def evaluate(rows: list[WatchRow], *, today: date, quotes: Mapping[str, Quote],
             active_run_tickers: frozenset[str]) -> list[Outcome]:
    """Un Outcome per riga, nello stesso ordine. Nessuna riga e' scartata in silenzio."""
    today = _day(today, "today")
    outcomes = []
    for row in rows:
        if row.status != "active":
            outcomes.append(Outcome(row.id, "blocked", f"riga non attiva (status={row.status})", {}))
            continue
        if row.kind == "date":
            day = _iso_day(row.date)
            if day is None:
                verdict, reason, obs = "blocked", f"data trigger non valida: {row.date!r}", {}
            else:
                obs = {"kind": "date", "date": row.date, "today": today.isoformat(),
                       "late_days": max((today - day).days, 0)}
                if today >= day:
                    verdict = "fire"
                    reason = (f"data {row.date} raggiunta" if today == day else
                              f"data {row.date} gia' passata (oggi {today.isoformat()})")
                else:
                    verdict, reason = "wait", f"data {row.date} non ancora raggiunta"
        elif row.kind == "price":
            verdict, reason, obs = _price_verdict(row, quotes.get(row.ticker), today)
        elif row.kind == "condition":
            day = _iso_day(row.reminder_date)
            if day is None:
                verdict, reason, obs = "blocked", "condizione senza data di promemoria (undated)", {}
            else:
                obs = {"kind": "condition", "reminder_date": row.reminder_date,
                       "today": today.isoformat()}
                if today >= day:
                    verdict, reason = "remind", f"promemoria condizione alla data {row.reminder_date}"
                else:
                    verdict, reason = "wait", f"promemoria previsto il {row.reminder_date}"
        else:
            verdict, reason, obs = "blocked", f"kind sconosciuto {row.kind!r}", {}
        if verdict in ("fire", "remind") and row.ticker in active_run_tickers:
            verdict, reason = "deferred", (f"rinviato: run Trade Idea attiva su {row.ticker} "
                                           f"(avrebbe dato {verdict}: {reason})")
        outcomes.append(Outcome(row.id, verdict, reason, obs))
    return outcomes


def _same_run(run, fired, reminders):
    if not fired and not reminders:
        raise ValueError("nessun trigger scattato ne' promemoria: niente voce da creare")
    run_id = run.get("id")
    for row in [*fired, *reminders]:
        if row.run_id != run_id:
            raise ValueError(f"riga {row.id} della run {row.run_id}, non {run_id}")


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt_num(value, language):
    """Numero nel formato della lingua: almeno 2 decimali, mai decimali persi (150,00 / 150.00)."""
    number = _decimal(value)
    if number is None:
        return text("n.d.", "n/a", language=language)
    places = max(2, -number.normalize().as_tuple().exponent)
    plain = f"{number:,.{places}f}"
    return plain.translate(str.maketrans(",.", ".,")) if language == "it" else plain


def _fmt_day(value, language):
    """Data nel formato della lingua (GG/MM/AAAA in italiano); solo il giorno, mai l'ora."""
    day = _iso_day(value[:10]) if isinstance(value, str) else None
    if day is None:
        return text("data n.d.", "date n/a", language=language)
    return text(day.strftime("%d/%m/%Y"), f"{day.day} {_MONTHS_EN[day.month - 1]} {day.year}",
                language=language)


def _fmt_direction(direction, language):
    if direction == "at_or_above":
        return text("al rialzo: prezzo ≥ livello", "upward: price ≥ level", language=language)
    if direction == "at_or_below":
        return text("al ribasso: prezzo ≤ livello", "downward: price ≤ level", language=language)
    return text("direzione n.d.", "direction n/a", language=language)


def _describe(row, observation, language):
    """Testo per il PM: numeri/date nella lingua, direzione a parole; la fonte grezza resta nel JSON."""
    obs = observation or {}
    if row.kind == "date":
        detail = text(f"data {_fmt_day(row.date, language)}", f"date {_fmt_day(row.date, language)}",
                      language=language)
        if isinstance(obs.get("late_days"), int) and obs["late_days"] > 0:
            when = _fmt_day(obs.get("today"), language)
            detail += text(f" (gia' passata quando il trigger e' stato controllato il {when})",
                           f" (already past when the trigger was checked on {when})", language=language)
    elif row.kind == "price":
        level = f"{_fmt_num(row.price_level, language)} {row.price_currency}"
        if obs.get("price") is not None:
            label = obs.get("bar_label") or text("barra n.d.", "bar n/a", language=language)
            seen = (f"{_fmt_num(obs.get('price'), language)} {obs.get('currency')}, "
                    f"{_fmt_day(obs.get('asof'), language)} ({label})")
        else:
            seen = text("osservazione n.d.", "observation n/a", language=language)
        direction = _fmt_direction(row.direction, language)
        detail = text(f"livello {level} ({direction}); osservato {seen}",
                      f"level {level} ({direction}); observed {seen}", language=language)
    else:
        when = _fmt_day(row.reminder_date, language)
        detail = text(f"condizione «{row.condition}» (promemoria del {when})",
                      f"condition \"{row.condition}\" (reminder on {when})", language=language)
    return f"#{row.id} {detail}: {row.what}"


def decision_fields(run: dict, fired: list[WatchRow], reminders: list[WatchRow], observations: dict,
                    *, language: str) -> dict:
    """Campi della voce RESEARCH PENDING (ticker/memo_id/timestamp li mette lo store)."""
    language = validate_language(language)
    _same_run(run, fired, reminders)
    observations = observations or {}
    parts = []
    if fired:
        parts.append(text("Trigger scattati: ", "Triggers fired: ", language=language)
                     + "; ".join(_describe(r, observations.get(r.id), language) for r in fired))
    if reminders:
        parts.append(text("Promemoria condizioni (da verificare a mano): ",
                          "Condition reminders (check by hand): ", language=language)
                     + "; ".join(_describe(r, observations.get(r.id), language) for r in reminders))
    timing = " | ".join(parts)
    if len(timing) > _TIMING_MAX:
        timing = timing[:_TIMING_MAX - 30] + text(" [testo tagliato]", " [text truncated]", language=language)
    rationale = "[TRIGGER] " + text(
        f"Trade Idea {run.get('ticker')} (run {run.get('id')}): " + " ".join(parts)
        + " Nessuna nuova analisi e' stata lanciata e nessuna spesa sostenuta: il rilancio lo decide il PM.",
        f"Trade Idea {run.get('ticker')} (run {run.get('id')}): " + " ".join(parts)
        + " No new analysis was started and nothing was spent: re-running is the PM's decision.",
        language=language)
    return {"action": "RESEARCH", "eur_amount": None, "confidence": "BASSA",
            "timing": timing, "rationale": rationale}


def email_message(run: dict, fired: list[WatchRow], reminders: list[WatchRow], decision_id: int,
                  *, language: str, observations: Mapping | None = None) -> tuple[str, str]:
    """(oggetto, corpo) dell'avviso; observations None = osservazioni dichiarate n.d."""
    language = validate_language(language)
    _same_run(run, fired, reminders)
    observations = observations or {}
    ticker = run.get("ticker")
    subject = "[Bellomberg] " + text(
        f"Trade Idea {ticker}: " + ("trigger di revisione scattato" if fired else "promemoria di revisione"),
        f"Trade Idea {ticker}: " + ("review trigger fired" if fired else "review reminder"),
        language=language)
    lines = [text(f"Run {run.get('id')} su {ticker}.", f"Run {run.get('id')} on {ticker}.", language=language), ""]
    if fired:
        lines.append(text("Trigger scattati:", "Triggers fired:", language=language))
        lines += ["- " + _describe(r, observations.get(r.id), language) for r in fired]
        lines.append("")
    if reminders:
        lines.append(text("Promemoria condizioni (verifica a mano):",
                          "Condition reminders (check by hand):", language=language))
        lines += ["- " + _describe(r, observations.get(r.id), language) for r in reminders]
        lines.append("")
    lines.append(text(
        f"Voce RESEARCH PENDING #{decision_id} in coda Decisioni. Nessuna analisi rilanciata, nessuna spesa.",
        f"RESEARCH PENDING item #{decision_id} in the Decisions queue. No analysis re-run, nothing spent.",
        language=language))
    return subject, "\n".join(lines)
