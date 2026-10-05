"""Worker dei trigger di revisione Trade Idea /4 (TI-RESEARCH-PIPELINE, Opus 5.5).

Gira dentro il PriceUpdater (un blocco in cli/price_updater.py::_run_once) e a
mano col CLI --due/--status, sul modello di market_data/filing_worker.py.

Un giro: importa i trigger delle run /4 'watch' gia' instradate a RESEARCH
(idempotente, recupera gli arretrati; route_result non e' toccato) -> superate
dalla run piu' nuova sullo stesso ticker -> annullate dal veto PM -> valuta date
e prezzi -> UNA voce RESEARCH PENDING per run scattata -> email dopo il COMMIT,
esito registrato (mai zitto).

Decisioni PM 04/10 sera: prezzi dei candidati da chat_tools.dispatch('get_price_live')
(chiusura giornaliera con data e freschezza) e MAI scritti in position_prices;
data -> avviso il giorno stesso; prezzo STALE -> solo dichiarato; ticker con run
attiva -> rinviato dichiarato; solo il veto annulla. Il worker NON crea la
tabella: la crea tools/migrations/migra_trade_idea_watch.py; se manca lo dice.

La valuta del prezzo osservato NON e' nel tool (yfinance daily Close): e' quella
della run (stessa base della ricevuta Trade Idea, currency_basis=accepted_identity)
ed e' dichiarata come tale nella fonte della quotazione.
"""
import argparse
import functools
import json
import os
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

DAILY_CLOSE_SOURCE = "yfinance daily Close (not an intraday quote)"
CURRENCY_BASIS = "valuta dalla run (il tool non la riporta)"
# Il task Bellomberg-PriceUpdater ha ExecutionTimeLimit 5 minuti
# (tools/ops/windows/install_all_schedulers.ps1): il worker si tiene largo.
# Il giro intero (quotazioni + email) sta in TIME_BUDGET_S; le quotazioni si fermano a
# QUOTES_BUDGET_S; un invio parte solo se restano EMAIL_RESERVE_S (email_sender ha un
# timeout SMTP di 30 s PER OPERAZIONE, fisso nel suo modulo: un invio appeso puo' superarli).
MAX_TICKERS = 10
TIME_BUDGET_S = 180.0
QUOTES_BUDGET_S = 120.0
EMAIL_RESERVE_S = 35.0
QUOTE_TIMEOUT_S = 25.0
CACHE_TTL_S = 3600.0
EMAIL_MAX_ATTEMPTS = 3
EMAIL_STALE_AFTER_S = 900          # 'sending' piu' vecchia = giro caduto durante l'invio
DEFERRED_STUCK_DAYS = 2            # rinvio per run attiva oltre questi giorni = ATTENZIONE
_ACTION_VERDICTS = ("fire", "remind")
_OK_VERDICTS = ("wait", "fire", "remind")


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Quotazioni dei candidati ──────────────────────────────────────────────

def _quote(ticker, status, reason, *, price=None, currency=None, asof=None, source=None, bar_label=None):
    from bellomberg.core.trade_idea_watch import Quote
    return Quote(ticker=ticker, price=price, currency=currency, asof=asof, source=source,
                 status=status, reason=reason, bar_label=bar_label)


def _dispatch_with_timeout(ticker, timeout_s):
    """get_price_live in un thread daemon: oltre il timeout il giro va avanti (dichiarato)."""
    from bellomberg.agents import chat_tools
    box = {}

    def _call():
        try:
            box["envelope"] = chat_tools.dispatch("get_price_live", {"ticker": ticker},
                                                  caller="trade-idea-watch")
        except Exception as exc:  # dispatch non dovrebbe sollevare: se lo fa, si dichiara il tipo
            box["error"] = type(exc).__name__

    worker = threading.Thread(target=_call, name=f"ti-watch-quote-{ticker}", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"get_price_live oltre {timeout_s:.0f}s")
    if "error" in box:
        raise RuntimeError(f"get_price_live ha sollevato {box['error']}")
    return box.get("envelope")


def bar_label(price_asof, today, *, language="it"):
    """Etichetta del prezzo (decisione PM 04/10): lo scatto intraday e' ammesso ma DICHIARATO.

    - barra di un giorno passato -> «chiusura del GG/MM»;
    - barra di oggi con un orario vero (non mezzanotte) -> «prezzo intraday delle HH:MM ora di Roma
      (barra non chiusa)»: l'orario della fonte e' nel fuso della borsa, si converte in Europe/Rome;
    - barra di oggi senza orario (yfinance daily: indice a mezzanotte) -> la fonte non distingue
      chiusura e seduta in corso -> «barra di oggi: potrebbe non essere la chiusura».
    """
    from bellomberg.core.language import text
    raw = str(price_asof or "").strip()
    try:
        day = date.fromisoformat(raw[:10])
    except ValueError:
        return text("data della barra n.d.", "bar date n/a", language=language)
    if day < today:
        return text(f"chiusura del {day:%d/%m}", f"close of {day:%d/%m}", language=language)
    clock = None
    if len(raw) > 10:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            # senza fuso non si sa di che ora si parla: niente orario (mai un fuso indovinato)
            if moment.tzinfo is not None and (moment.hour, moment.minute, moment.second) != (0, 0, 0):
                from zoneinfo import ZoneInfo
                clock = f"{moment.astimezone(ZoneInfo('Europe/Rome')):%H:%M}"
        except (ValueError, KeyError):
            clock = None
    if clock is not None:
        return text(f"prezzo intraday delle {clock} ora di Roma (barra non chiusa)",
                    f"intraday price at {clock} Rome time (bar not closed)", language=language)
    return text("barra di oggi: potrebbe non essere la chiusura",
                "today's bar: may not be the close", language=language)


def fetch_quote(ticker, *, today, currency, timeout_s=QUOTE_TIMEOUT_S, language="it"):
    """Chiusura giornaliera del candidato con data e freschezza; ogni buco e' uno status dichiarato.

    Stessi controlli di agents/trade_idea._candidate_quote_receipt. Niente viene
    scritto in position_prices. Nei motivi solo testi nostri e tipi di eccezione
    (il testo di un errore di rete puo' contenere URL).
    """
    from bellomberg.agents.specialists.base import _trade_idea_tool_receipt_success
    from bellomberg.valuation.market_quote import _freshness
    if not currency:
        return _quote(ticker, "unavailable", "valuta della run assente: livello non confrontabile")
    try:
        envelope = _dispatch_with_timeout(ticker, timeout_s)
    except Exception as exc:
        return _quote(ticker, "source_unavailable", f"{type(exc).__name__}: {exc}"[:300])
    payload = envelope.get("data", envelope) if isinstance(envelope, dict) else None
    if not _trade_idea_tool_receipt_success(envelope, "get_price_live"):
        return _quote(ticker, "source_unavailable", "tool get_price_live in errore o incompleto")
    if not isinstance(payload, dict) or payload.get("ticker") != ticker:
        return _quote(ticker, "unavailable", "identita' ticker della quotazione non verificata")
    source = str(payload.get("source") or "").strip()
    if source != DAILY_CLOSE_SOURCE:
        return _quote(ticker, "unavailable", "fonte daily close non attestata")
    try:
        price = Decimal(str(payload.get("px")))
    except (InvalidOperation, ValueError):
        price = None
    if price is None or not price.is_finite() or price <= 0:
        return _quote(ticker, "unavailable", "prezzo non numerico o non positivo")
    day = str(payload.get("price_asof") or "")[:10]
    freshness = _freshness(day, today.isoformat())
    reason = None if freshness == "ok" else f"freschezza {freshness} (asof {day or 'n.d.'}, oggi {today.isoformat()})"
    # Etichetta della barra in un campo PROPRIO (contratto con P1): il core la mostra nella
    # voce RESEARCH e nell'email; la fonte grezza resta in source, solo per il JSON
    # (decisione PM 04/10: intraday ammesso ma dichiarato).
    label = bar_label(payload.get("price_asof"), today, language=language)
    return _quote(ticker, freshness, reason, price=price, currency=currency, asof=day or None,
                  source=f"{source}; {CURRENCY_BASIS}", bar_label=label)


def _quote_to_json(quote, fetched_at):
    return {"ticker": quote.ticker, "price": None if quote.price is None else str(quote.price),
            "currency": quote.currency, "asof": quote.asof, "source": quote.source,
            "status": quote.status, "reason": quote.reason, "bar_label": quote.bar_label,
            "fetched_at": fetched_at}


def _quote_from_json(item):
    price = item.get("price")
    return _quote(item["ticker"], item["status"], item.get("reason"),
                  price=None if price is None else Decimal(price), currency=item.get("currency"),
                  asof=item.get("asof"), source=item.get("source"), bar_label=item.get("bar_label"))


class QuoteCache:
    """Cache del GIORNO dei prezzi dei candidati (un file; cambia giorno = si riparte vuoti).

    Una voce vale CACHE_TTL_S secondi (eta' sul wall-clock UTC fra processi diversi:
    un'eta' negativa = orologio tornato indietro -> voce scaduta, mai riusata).
    Si conservano solo le quotazioni ok/stale; gli errori si ritentano al giro dopo.
    Un file illeggibile o non scrivibile e' dichiarato in `error`, mai fatale.
    """

    def __init__(self, path, *, today, ttl_s=CACHE_TTL_S, now=None):
        self.path = None if path is None else Path(path)
        self.today = today.isoformat()
        self.ttl_s = float(ttl_s)
        self._now = now or _utc_now
        self.error = None
        self.quotes = {}
        self.dirty = False
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("day") == self.today and isinstance(data.get("quotes"), dict):
                self.quotes = data["quotes"]
        except (OSError, ValueError) as exc:
            self.error = f"cache prezzi illeggibile ({type(exc).__name__}): si riscarica"

    def get(self, ticker, currency):
        item = self.quotes.get(ticker)
        if not isinstance(item, dict) or item.get("currency") != currency:
            return None
        try:
            fetched = datetime.fromisoformat(item["fetched_at"].replace("Z", "+00:00"))
            age = (self._now() - fetched).total_seconds()
            if age < 0 or age > self.ttl_s:
                return None
            return _quote_from_json(item)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None

    def put(self, quote):
        if quote.status not in ("ok", "stale"):
            return
        self.quotes[quote.ticker] = _quote_to_json(quote, _iso(self._now()))
        self.dirty = True

    def save(self):
        if self.path is None or not self.dirty:
            return
        text = json.dumps({"day": self.today, "quotes": self.quotes}, ensure_ascii=False,
                          sort_keys=True, indent=1)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            for attempt in range(3):
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:  # lettore concorrente su Windows: si ritenta, poi si dichiara
                    if attempt == 2:
                        raise
                    time.sleep(0.2)
            self.dirty = False
        except OSError as exc:
            self.error = f"cache prezzi non salvata ({type(exc).__name__})"


def _collect_quotes(rows, *, today, quote_fn, cache, max_tickers, deadline, clock):
    """Quotazioni per i ticker con trigger di prezzo attivi; oltre i limiti -> dichiarati."""
    wanted = {}
    for row in rows:
        if row.kind == "price":
            wanted.setdefault(row.ticker, set()).add(row.price_currency)
    quotes, report = {}, {}
    fetched = 0
    for ticker in sorted(wanted):
        currencies = wanted[ticker] - {None}
        if len(currencies) != 1:
            quotes[ticker] = _quote(ticker, "unavailable",
                                    f"valute dei livelli non univoche per {ticker}: {sorted(map(str, wanted[ticker]))}")
            report[ticker] = {"status": "unavailable", "cached": False, "reason": quotes[ticker].reason}
            continue
        currency = next(iter(currencies))
        cached = cache.get(ticker, currency)
        if cached is not None:
            quotes[ticker] = cached
            report[ticker] = {"status": cached.status, "asof": cached.asof, "cached": True}
            continue
        if fetched >= max_tickers:
            reason = f"non controllato in questo giro: limite di {max_tickers} ticker scaricati per giro"
        elif clock() >= deadline:
            reason = "non controllato in questo giro: tempo del giro esaurito"
        else:
            reason = None
        if reason is not None:
            quotes[ticker] = _quote(ticker, "unavailable", reason)
            report[ticker] = {"status": "unavailable", "cached": False, "reason": reason}
            continue
        fetched += 1
        try:
            quote = quote_fn(ticker, today=today, currency=currency)
        except Exception as exc:
            quote = _quote(ticker, "source_unavailable", f"quote_fn ha sollevato {type(exc).__name__}")
        if quote.ticker != ticker:
            quote = _quote(ticker, "unavailable", f"quotazione restituita per {quote.ticker!r}")
        cache.put(quote)
        quotes[ticker] = quote
        report[ticker] = {"status": quote.status, "asof": quote.asof, "cached": False}
        if quote.reason:
            report[ticker]["reason"] = quote.reason
    return quotes, report


# ── Email ────────────────────────────────────────────────────────────────

def default_send_email(language):
    """Avviso via reporting/email_sender.invia_briefing_email; config assente = 'not_configured'."""
    def send(subject, body):
        from bellomberg.reporting import email_sender
        if not email_sender.email_configurata():
            return "not_configured"
        return email_sender.invia_briefing_email(body, oggetto=subject, language=language)
    return send


def _record_outcome(outcome, error):
    if outcome is True:
        return "sent", None
    if outcome == "not_configured":
        return "not_configured", None
    return "failed", error or ("invio non riuscito (send_email ha restituito False)" if outcome is False
                               else f"esito send_email non riconosciuto: {type(outcome).__name__}")


def _send_claimed(store, row_ids, compose, send_email, *, budget):
    """Budget -> presa atomica (claim_email) -> composizione -> invio -> mark_email.

    Senza tempo sufficiente NON si prende la riga (nessun tentativo consumato): 'postponed'.
    Presa persa = un altro giro la sta inviando: 'taken', nessun invio (niente doppioni).
    """
    ids = list(row_ids)
    record = {"row_ids": ids}
    if not budget.can_send():
        record.update(status="postponed", reason=(
            f"email rinviata al giro dopo: tempo del giro esaurito (servono {budget.reserve_s:.0f} s per un invio)"))
        return record
    claim = store.claim_email(ids, max_attempts=EMAIL_MAX_ATTEMPTS, stale_after_s=EMAIL_STALE_AFTER_S)
    if claim is None:
        record.update(status="taken", reason="invio gia' preso da un altro giro o non piu' ritentabile: nessun invio")
        return record
    record["previous"] = claim["previous"]
    try:
        subject, body = compose(claim)
    except Exception as exc:
        status, error = "failed", f"messaggio non costruito ({type(exc).__name__})"
    else:
        try:
            outcome, error = send_email(subject, body), None
        except Exception as exc:
            outcome, error = False, f"send_email ha sollevato {type(exc).__name__}"
        status, error = _record_outcome(outcome, error)
    record["status"] = status
    if error:
        record["error"] = error
    try:
        store.mark_email(ids, status, error)
    except Exception as exc:
        record["mark_email_error"] = type(exc).__name__
    return record


def _retry_note(previous, language):
    """Frase VERA sul perche' di un nuovo invio, dagli stati precedenti della presa."""
    from bellomberg.core.language import text
    states = set(previous.values())
    if "sending" in states:
        return (text(" (nuovo invio)", " (resend)", language=language), text(
            "Nuovo invio: un invio precedente di questo avviso e' rimasto senza esito (il giro si e' "
            "interrotto durante l'invio): potresti riceverlo due volte.",
            "Resend: a previous delivery of this notice has no known outcome (the run stopped while "
            "sending): you may receive it twice.", language=language))
    if "failed" in states:
        return (text(" (nuovo invio)", " (resend)", language=language), text(
            "Nuovo invio: un invio precedente di questo avviso non e' riuscito.",
            "Resend: a previous delivery of this notice failed.", language=language))
    return (text(" (in ritardo)", " (late)", language=language), text(
        "Avviso in ritardo: non era partito nel giro che ha creato la voce.",
        "Late notice: it was not sent by the run that created the item.", language=language))


def _retry_emails(store, *, send_email, language, budget, skip_decisions):
    """Ritento degli avvisi non partiti, col messaggio COMPLETO ricostruito dalle righe scattate."""
    from bellomberg.core.trade_idea_watch import email_message
    groups = {}
    for item in store.pending_email(max_attempts=EMAIL_MAX_ATTEMPTS, stale_after_s=EMAIL_STALE_AFTER_S):
        if item["fired_decision_id"] not in skip_decisions:
            groups.setdefault(item["fired_decision_id"], []).append(item)
    out = []
    for decision_id, items in sorted(groups.items()):
        ids = [i["id"] for i in items]

        def compose(claim, decision_id=decision_id, ids=ids, run_id=items[0]["run_id"]):
            pairs = store.fired_rows(ids)
            fired = [row for row, _ in pairs if row.kind != "condition"]
            reminders = [row for row, _ in pairs if row.kind == "condition"]
            observations = {row.id: obs for row, obs in pairs if obs is not None}
            subject, body = email_message(store.run_info(run_id), fired, reminders, decision_id,
                                          language=language, observations=observations)
            suffix, note = _retry_note(claim["previous"], language)
            return subject + suffix, note + "\n\n" + body

        record = _send_claimed(store, ids, compose, send_email, budget=budget)
        record.update(decision_id=decision_id, ticker=items[0]["ticker"], retry=True)
        out.append(record)
    return out


class _Budget:
    """Tetto di tempo del giro (task da 5 minuti): quotazioni ed email lo guardano entrambe."""

    def __init__(self, clock, total_s, quotes_s, reserve_s):
        self.clock = clock
        start = clock()
        self.deadline = start + float(total_s)
        self.quotes_deadline = start + min(float(quotes_s), float(total_s))
        self.reserve_s = float(reserve_s)

    def can_send(self):
        return self.clock() + self.reserve_s <= self.deadline


def _since_days(since, today):
    try:
        return (today - date.fromisoformat(str(since)[:10])).days
    except ValueError:
        return None


# ── Giro ─────────────────────────────────────────────────────────────────

def run_due(*, db_path, today=None, quote_fn=None, send_email=None, language="it",
            max_tickers=MAX_TICKERS, time_budget_s=TIME_BUDGET_S, quotes_budget_s=QUOTES_BUDGET_S,
            email_reserve_s=EMAIL_RESERVE_S, cache_path=None, clock=None, store_clock=None):
    """Un giro completo. Ritorna sempre un dict; status ok | attention | errore.

    db_path obbligatorio (nessun default al DB vero: lo mette solo main/run_from_updater).
    quote_fn(ticker, *, today, currency) -> Quote e send_email(subject, body) -> True |
    False | 'not_configured' sono iniettabili (test senza rete ne' SMTP).
    """
    from bellomberg.core.language import validate_language
    from bellomberg.core.trade_idea_watch import decision_fields, email_message, evaluate
    from bellomberg.storage.trade_idea_watch_store import (
        SCHEMA_MISSING, TradeIdeaWatchStore, WatchConflict, WatchDeferred)
    clock = clock or time.monotonic
    budget = _Budget(clock, time_budget_s, quotes_budget_s, email_reserve_s)
    today = today or date.today()
    output = {"checked_at": _iso(_utc_now()), "today": today.isoformat(), "status": "ok",
              "limits": {"max_tickers": max_tickers, "time_budget_s": time_budget_s,
                         "quotes_budget_s": quotes_budget_s, "email_reserve_s": email_reserve_s}}
    try:
        language = validate_language(language)
        if type(today) is not date:
            raise TypeError("today deve essere datetime.date")
        store = TradeIdeaWatchStore(db_path, clock=store_clock)
    except RuntimeError as exc:
        if str(exc) == SCHEMA_MISSING:
            output.update(status="errore", reason=SCHEMA_MISSING)
            return output
        output.update(status="errore", reason=f"store non apribile ({type(exc).__name__})")
        return output
    except Exception as exc:
        output.update(status="errore", reason=f"store non apribile ({type(exc).__name__}: {exc})"[:300])
        return output
    if quote_fn is None:
        quote_fn = functools.partial(fetch_quote, language=language)
    if send_email is None:
        send_email = default_send_email(language)
    errors = []

    ingest = store.ingest_pending(today=today)
    output["ingest"] = {"inserted": ingest["inserted"], "runs": ingest["runs"],
                        "ignored": len(ingest["ignored"]), "errors": ingest["errors"]}
    output["superseded"] = store.supersede_older()
    output["cancelled_by_veto"] = store.cancel_vetoed()
    # Trigger che non verranno controllati (blocked/undated/veto revocato): dichiarati a OGNI giro.
    output["attention_rows"] = store.attention_rows()

    rows = store.active()
    busy_runs = store.active_runs()
    busy = frozenset(busy_runs)
    cache = QuoteCache(cache_path, today=today)
    quotes, quote_report = _collect_quotes(rows, today=today, quote_fn=quote_fn, cache=cache,
                                           max_tickers=max_tickers, deadline=budget.quotes_deadline,
                                           clock=clock)
    cache.save()
    output["quotes"] = quote_report
    if cache.error:
        output["cache_error"] = cache.error

    outcomes = evaluate(rows, today=today, quotes=quotes, active_run_tickers=busy)
    store.record_checks(outcomes)
    by_id = {row.id: row for row in rows}
    verdicts = {}
    for outcome in outcomes:
        verdicts[outcome.verdict] = verdicts.get(outcome.verdict, 0) + 1
    output["checked"] = len(outcomes)
    output["verdicts"] = verdicts
    output["not_ok"] = [{"row_id": o.row_id, "ticker": by_id[o.row_id].ticker, "verdict": o.verdict,
                         "reason": o.reason} for o in outcomes if o.verdict not in _OK_VERDICTS + ("deferred",)]
    # Rinvio per run attiva: normale per poco, ATTENZIONE oltre DEFERRED_STUCK_DAYS (run bloccata).
    deferred = {}
    for outcome in outcomes:
        if outcome.verdict != "deferred":
            continue
        ticker = by_id[outcome.row_id].ticker
        info = busy_runs.get(ticker) or {}
        days = _since_days(info.get("since"), today)
        item = deferred.setdefault(ticker, {
            "ticker": ticker, "run_id": info.get("run_id"), "state": info.get("state"),
            "technical_status": info.get("technical_status"), "since": info.get("since"),
            "days": days, "row_ids": [],
            "stuck": days is None or days > DEFERRED_STUCK_DAYS})
        item["row_ids"].append(outcome.row_id)
    output["deferred"] = sorted(deferred.values(), key=lambda d: d["ticker"])

    # Una voce per run: righe scattate (fire) + promemoria delle condizioni (remind).
    per_run = {}
    for outcome in outcomes:
        if outcome.verdict in _ACTION_VERDICTS:
            per_run.setdefault(by_id[outcome.row_id].run_id, []).append(outcome)
    fired_report = []
    for run_id, items in sorted(per_run.items()):
        fired = [by_id[o.row_id] for o in items if o.verdict == "fire"]
        reminders = [by_id[o.row_id] for o in items if o.verdict == "remind"]
        observations = {o.row_id: o.observation for o in items}
        ids = [o.row_id for o in items]
        entry = {"run_id": run_id, "ticker": by_id[ids[0]].ticker, "row_ids": ids}
        try:
            run = store.run_info(run_id)
            fields = decision_fields(run, fired, reminders, observations, language=language)
            decision_id = store.fire(run_id, ids, fields, observations)
        except WatchDeferred as exc:
            entry.update(status="deferred", reason=str(exc)[:300])
            fired_report.append(entry)
            continue
        except WatchConflict as exc:
            entry.update(status="conflict", reason=str(exc)[:300])
            fired_report.append(entry)
            errors.append({"run_id": run_id, "error": "WatchConflict"})
            continue
        except Exception as exc:
            entry.update(status="errore", reason=type(exc).__name__)
            fired_report.append(entry)
            errors.append({"run_id": run_id, "error": type(exc).__name__})
            continue
        entry.update(status="fired", decision_id=decision_id)

        # Email SOLO dopo il COMMIT della voce: un invio fallito non la annulla, si registra.
        def compose(claim, run=run, fired=fired, reminders=reminders, decision_id=decision_id,
                    observations=observations):
            return email_message(run, fired, reminders, decision_id, language=language,
                                 observations=observations)
        entry["email"] = _send_claimed(store, ids, compose, send_email, budget=budget)
        fired_report.append(entry)
    output["fired"] = fired_report
    handled = {e["decision_id"] for e in fired_report if e.get("status") == "fired"}
    output["email_retry"] = _retry_emails(store, send_email=send_email, language=language,
                                          budget=budget, skip_decisions=handled)
    # Avvisi arrivati al tetto dei tentativi: nessun nuovo tentativo automatico, ma DICHIARATI
    # a ogni giro (regola di casa: niente silenzi).
    output["email_exhausted"] = store.email_exhausted(max_attempts=EMAIL_MAX_ATTEMPTS)
    output["errors"] = errors

    attention = (errors or ingest["errors"] or output["not_ok"] or cache.error
                 or output["attention_rows"] or output["email_exhausted"]
                 or any(d["stuck"] for d in output["deferred"])
                 or any(e.get("status") not in ("fired", "deferred") for e in fired_report)
                 or any(e.get("email", {}).get("status") not in ("sent", "taken") for e in fired_report
                        if e.get("status") == "fired")
                 or any(r["status"] not in ("sent", "taken") for r in output["email_retry"]))
    if attention:
        output["status"] = "attention"
    return output


def summary_lines(output):
    """Righe per price_updater.log: SEMPRE stampate (anche con --quiet), buchi dichiarati."""
    status = output.get("status")
    if status == "errore":
        return [f"  [WATCH] KO dichiarato: {output.get('reason')}"]
    fired = [e for e in output.get("fired", []) if e.get("status") == "fired"]
    lines = [f"  [WATCH] {status}: trigger controllati {output.get('checked', 0)}, "
             f"importati {output.get('ingest', {}).get('inserted', 0)}, voci create {len(fired)}, "
             f"superati {output.get('superseded', 0)}, annullati dal veto {output.get('cancelled_by_veto', 0)}"]
    for entry in output.get("fired", []):
        if entry.get("status") == "fired":
            mail = entry.get("email", {})
            lines.append(f"  [WATCH] {entry['ticker']}: voce RESEARCH #{entry['decision_id']} "
                         f"(trigger {entry['row_ids']}), email {mail.get('status')}"
                         + (f" ({mail.get('error') or mail.get('reason')})"
                            if mail.get("error") or mail.get("reason") else ""))
        else:
            lines.append(f"  [WATCH] {entry['ticker']}: {entry.get('status')} dichiarato: {entry.get('reason')}")
    by_ticker = {}
    for item in output.get("attention_rows", []):
        by_ticker.setdefault(item["ticker"], []).append(
            f"#{item['id']} {item['kind']} {item['status']} ({item.get('reason') or 'motivo n.d.'})")
    for ticker, parts in sorted(by_ticker.items()):
        lines.append(f"  [WATCH] ATTENZIONE {ticker}: trigger non sorvegliati: " + "; ".join(parts))
    for item in output.get("email_exhausted", []):
        lines.append(f"  [WATCH] ATTENZIONE {item['ticker']}: avviso del trigger #{item['id']} non consegnato "
                     f"dopo {item['email_attempts']} tentativi: controlla la voce "
                     f"#{item['fired_decision_id']} in coda decisioni")
    for item in output.get("deferred", []):
        where = ("finita ma non ancora instradata" if item.get("state") == "non_instradata"
                 else f"in {item.get('technical_status')}")
        if item["stuck"]:
            days = "n.d." if item["days"] is None else item["days"]
            lines.append(f"  [WATCH] ATTENZIONE {item['ticker']}: run Trade Idea {item.get('run_id')} bloccata "
                         f"{where} da {days} giorni: i trigger {item['row_ids']} aspettano")
        else:
            lines.append(f"  [WATCH] {item['ticker']}: trigger {item['row_ids']} rinviati: run Trade Idea "
                         f"{item.get('run_id')} {where} (dal {str(item.get('since'))[:10]})")
    for item in output.get("not_ok", []):
        lines.append(f"  [WATCH] #{item['row_id']} {item['ticker']} {item['verdict']}: {item['reason']}")
    for item in output.get("ingest", {}).get("errors", []):
        lines.append(f"  [WATCH] import run {item.get('run_id')} KO dichiarato: {item.get('error')} {item.get('reason', '')}")
    for item in output.get("email_retry", []):
        if item.get("status") != "sent":
            lines.append(f"  [WATCH] nuovo invio email voce #{item.get('decision_id')}: {item.get('status')} "
                         f"{item.get('error') or item.get('reason') or ''}")
    if output.get("cache_error"):
        lines.append(f"  [WATCH] {output['cache_error']}")
    return lines


def _append_log(path, output):
    text = json.dumps(output, ensure_ascii=False, allow_nan=False, default=str)
    log = Path(path)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(text + "\n")
    return text


def run_from_updater():
    """Giro dal PriceUpdater sul DB vero: log JSONL + righe da stampare."""
    from bellomberg.core.paths import DATA_DIR, SQLITE_PATH
    output = run_due(db_path=SQLITE_PATH, cache_path=DATA_DIR / "trade_idea_watch_quotes.json")
    _append_log(DATA_DIR / "trade_idea_watch.jsonl", output)
    return output


def main(argv=None):
    from bellomberg.core.paths import DATA_DIR, SQLITE_PATH
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(SQLITE_PATH))
    parser.add_argument("--log", default=str(DATA_DIR / "trade_idea_watch.jsonl"))
    parser.add_argument("--cache", default=str(DATA_DIR / "trade_idea_watch_quotes.json"))
    parser.add_argument("--language", default="it", choices=("it", "en"))
    parser.add_argument("--max-tickers", type=int, default=MAX_TICKERS)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--due", action="store_true", help="Giro completo: import, controlli, voci, email (default)")
    modes.add_argument("--status", action="store_true", help="Conteggi per stato, nessuna scrittura")
    args = parser.parse_args(argv)
    try:
        if args.status:
            from bellomberg.storage.trade_idea_watch_store import TradeIdeaWatchStore
            output = {"status": "ok", **TradeIdeaWatchStore(args.db).summary()}
        else:
            output = run_due(db_path=args.db, cache_path=args.cache, language=args.language,
                             max_tickers=args.max_tickers)
        code = 0 if output.get("status") == "ok" else 1
    except Exception as exc:
        output = {"status": "errore", "reason": f"{type(exc).__name__}: {exc}"[:300]}
        code = 1
    text = json.dumps(output, ensure_ascii=False, allow_nan=False, default=str)
    print(text)
    if not args.status:
        _append_log(args.log, output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
