"""Single coordinator for scheduled and manual News feed refreshes.

The manager owns one worker at a time.  Both the backend timer and the manual API
trigger use the same ``trigger`` method, so a button click cannot create a second
provider pull while a scheduled pull is running.

G3 (04/10/2026, decisione PM: i giri automatici li fa SOLO il backend, il task Windows
Bellomberg-NewsFeed lo spegne il PM):
- pausa notturna (default 22:00-07:00 ora locale, tutti i giorni) e nessun giro all'avvio:
  il primo giro parte dopo NEWS_REFRESH_FIRST_DELAY_MINUTES. Weekend (decisione PM 04/10 sera):
  si gira anche sabato e domenica, col passo NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES (default
  120) al posto di NEWS_REFRESH_INTERVAL_MINUTES; NEWS_REFRESH_QUIET_DAYS assente = nessun giorno
  di pausa;
- una variabile vuota o invalida NON ferma il backend: lo scheduler resta spento e
  ``status()["scheduler"]["config_error"]`` dice quale e perche' (il click resta possibile);
- un trigger durante un giro restituisce lo STESSO job (``accepted: false``,
  ``reason: already_running``); ``job(id)`` lo segue fino al risultato;
- ``stop()`` aspetta al massimo ``stop_timeout_seconds``: il giro ancora vivo e' dichiarato
  ``interrupted`` e il suo thread (daemon) non tiene acceso il processo;
- errori esposti: solo tipo dell'eccezione e stato HTTP, mai il messaggio grezzo (gli URL
  dei provider portano la chiave nella querystring).
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from collections import OrderedDict
from datetime import timedelta
from typing import Any, Callable, Dict, FrozenSet, Optional, Tuple

from bellomberg.core.language import text as _t


DEFAULT_INTERVAL_SECONDS = 15 * 60
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_QUIET_HOURS = "22:00-07:00"
DEFAULT_QUIET_DAYS = "none"          # PM 04/10 sera: notizie anche nel weekend
DEFAULT_WEEKEND_INTERVAL_SECONDS = 120 * 60
_WEEKEND = (5, 6)
DEFAULT_FIRST_DELAY_SECONDS = 10 * 60
STOP_TIMEOUT_SECONDS = 5.0
WAIT_CAP_SECONDS = 30.0          # il ciclo ricontrolla l'orologio (sospensione del PC, ora legale)
JOB_HISTORY = 20
_GIORNI = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def describe_exception(exc: BaseException) -> str:
    """Solo tipo e stato HTTP: il messaggio grezzo puo' contenere l'URL con la chiave API."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (" (HTTP %s)" % status if status is not None else "")


def news_quiet_hours(value: Any = None) -> Optional[Tuple[int, int]]:
    """Pausa giornaliera in minuti (inizio, fine); assente = 22:00-07:00, 'off' = nessuna pausa."""
    nome = "NEWS_REFRESH_QUIET_HOURS"
    raw = os.environ.get(nome) if value is None else value
    if raw is None:
        raw = DEFAULT_QUIET_HOURS
    text = str(raw).strip().lower()
    if text in {"off", "none"}:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
    if m:
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60 and (h1, m1) != (h2, m2):
            return h1 * 60 + m1, h2 * 60 + m2
    raise ValueError("%s: valore non valido %r (HH:MM-HH:MM, inizio diverso dalla fine, oppure off)"
                     % (nome, raw))


def news_quiet_days(value: Any = None) -> FrozenSet[int]:
    """Giorni senza giri automatici; assente o 'none' = nessuno (presente ma vuoto = errore)."""
    nome = "NEWS_REFRESH_QUIET_DAYS"
    raw = os.environ.get(nome) if value is None else value
    if raw is None:
        raw = DEFAULT_QUIET_DAYS
    text = str(raw).strip().lower()
    if text == "none":
        return frozenset()
    parti = [x.strip() for x in text.split(",")]
    if not text or any(x not in _GIORNI for x in parti):
        raise ValueError("%s: valore non valido %r (elenco fra mon,tue,wed,thu,fri,sat,sun oppure none)"
                         % (nome, raw))
    giorni = frozenset(_GIORNI[x] for x in parti)
    if len(giorni) == 7:
        raise ValueError("%s: tutti i giorni in pausa (%r): per spegnere i giri usa "
                         "NEWS_AUTO_REFRESH_ENABLED=false" % (nome, raw))
    return giorni


def news_weekend_interval_seconds(value: Any = None) -> float:
    """Passo dei giri sabato e domenica; assente = 120 minuti, vuoto/invalido = errore."""
    nome = "NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES"
    raw = os.environ.get(nome) if value is None else value
    if raw is None:
        return float(DEFAULT_WEEKEND_INTERVAL_SECONDS)
    try:
        seconds = float(raw) * 60.0
    except (TypeError, ValueError, OverflowError):
        seconds = math.nan
    if not math.isfinite(seconds) or not MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError("%s: valore non valido %r (minuti tra 1 e 1440)" % (nome, raw))
    return seconds


def news_first_delay_seconds(value: Any = None) -> float:
    """Ritardo del primo giro dopo l'avvio; assente = 10 minuti, vuoto/invalido = errore."""
    nome = "NEWS_REFRESH_FIRST_DELAY_MINUTES"
    raw = os.environ.get(nome) if value is None else value
    if raw is None:
        return float(DEFAULT_FIRST_DELAY_SECONDS)
    try:
        seconds = float(raw) * 60.0
    except (TypeError, ValueError, OverflowError):
        seconds = math.nan
    if not math.isfinite(seconds) or not 60 <= seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError("%s: valore non valido %r (minuti tra 1 e 1440)" % (nome, raw))
    return seconds


def news_refresh_interval_seconds(value: Any = None) -> float:
    """Return the interval in seconds; absent = default, empty/invalid/out of range = error."""
    raw = os.environ.get("NEWS_REFRESH_INTERVAL_MINUTES") if value is None else value
    if raw is None:
        return float(DEFAULT_INTERVAL_SECONDS)
    try:
        seconds = float(raw) * 60.0
    except (TypeError, ValueError, OverflowError):
        seconds = math.nan
    if not math.isfinite(seconds) or not MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError("NEWS_REFRESH_INTERVAL_MINUTES: valore non valido %r (minuti tra 1 e 1440)" % (raw,))
    return seconds


def news_auto_refresh_enabled(value: Any = None) -> bool:
    """Read the opt-out flag; absent = enabled, anything but an explicit true/false = error."""
    raw = os.environ.get("NEWS_AUTO_REFRESH_ENABLED") if value is None else value
    if raw is None:
        return True
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError("%s: valore non valido %r (usa true/false)" % ("NEWS_AUTO_REFRESH_ENABLED", raw))


class NewsRefreshManager:
    """Coordinate the News pull scheduler and manual refreshes."""

    def __init__(
        self,
        puller: Optional[Callable[..., Dict[str, Any]]] = None,
        interval_seconds: Optional[float] = None,
        auto_enabled: Optional[bool] = None,
        quiet_hours: Optional[str] = None,
        quiet_days: Optional[str] = None,
        first_delay_seconds: Optional[float] = None,
        weekend_interval_seconds: Optional[float] = None,
        stop_timeout_seconds: float = STOP_TIMEOUT_SECONDS,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        # G3: una configurazione illeggibile NON fa cadere il backend (prima: ValueError
        # all'import di bellomberg_api). Lo scheduler resta spento e lo dice, col nome.
        errori = []

        def _leggi(fn, *args):
            try:
                return fn(*args)
            except ValueError as exc:
                errori.append(str(exc))
                return None

        self.interval_seconds = (
            _leggi(news_refresh_interval_seconds)
            if interval_seconds is None else float(interval_seconds)
        )
        self.auto_enabled = (
            _leggi(news_auto_refresh_enabled)
            if auto_enabled is None else bool(auto_enabled)
        )
        self.quiet_hours = _leggi(news_quiet_hours, quiet_hours)
        self.quiet_days = _leggi(news_quiet_days, quiet_days)
        self.first_delay_seconds = (
            _leggi(news_first_delay_seconds)
            if first_delay_seconds is None else float(first_delay_seconds)
        )
        self.weekend_interval_seconds = (
            _leggi(news_weekend_interval_seconds)
            if weekend_interval_seconds is None else float(weekend_interval_seconds)
        )
        self.config_error = "; ".join(errori) or None
        if self.config_error:
            self.auto_enabled = False
        self.auto_enabled = bool(self.auto_enabled)
        self.stop_timeout_seconds = float(stop_timeout_seconds)
        self._now = now or time.time
        self._puller = puller or self._default_puller
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._scheduler: Optional[threading.Thread] = None
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._started = False
        self._stopped = False
        self._next_deadline: Optional[float] = None
        self._history: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._job: Dict[str, Any] = {
            "id": None,
            "status": "idle",
            "trigger": None,
            "started_at": None,
            "finished_at": None,
            "duration_s": None,
            "next_run_at": None,
            "interval_seconds": self.interval_seconds,
            "result": None,
            "error": None,
        }

    @staticmethod
    def _default_puller(**kwargs: Any) -> Dict[str, Any]:
        from bellomberg.market_data.news_aggregator import auto_pull_feed

        return auto_pull_feed(**kwargs)

    @staticmethod
    def _timestamp(epoch: Optional[float] = None) -> Optional[str]:
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch).isoformat()

    def _in_pausa(self, epoch: float) -> bool:
        quando = datetime.fromtimestamp(epoch)
        if quando.weekday() in (self.quiet_days or ()):
            return True
        if not self.quiet_hours:
            return False
        inizio, fine = self.quiet_hours
        minuto = quando.hour * 60 + quando.minute
        if inizio < fine:
            return inizio <= minuto < fine
        return minuto >= inizio or minuto < fine

    def _prossimo_consentito(self, epoch: float) -> float:
        """Primo istante fuori pausa a partire da ``epoch`` (ora locale)."""
        quando = datetime.fromtimestamp(epoch)
        for _ in range(32):
            if not self._in_pausa(quando.timestamp()):
                return quando.timestamp()
            if quando.weekday() in (self.quiet_days or ()):
                quando = (quando + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                continue
            _inizio, fine = self.quiet_hours
            fine_oggi = quando.replace(hour=fine // 60, minute=fine % 60, second=0, microsecond=0)
            quando = fine_oggi if fine_oggi > quando else fine_oggi + timedelta(days=1)
        raise RuntimeError("nessun istante fuori pausa: configurazione incoerente")  # pragma: no cover

    def _scheduler_status_locked(self) -> Dict[str, Any]:
        ore = None
        if self.quiet_hours:
            ore = "%02d:%02d-%02d:%02d" % (self.quiet_hours[0] // 60, self.quiet_hours[0] % 60,
                                           self.quiet_hours[1] // 60, self.quiet_hours[1] % 60)
        nomi = {v: k for k, v in _GIORNI.items()}
        return {
            "enabled": self.auto_enabled,
            "config_error": self.config_error,
            "quiet_hours": ore,
            "quiet_days": sorted(nomi[g] for g in self.quiet_days) if self.quiet_days is not None else None,
            "first_delay_seconds": self.first_delay_seconds,
            "weekend_interval_seconds": self.weekend_interval_seconds,
            "paused_now": self._in_pausa(self._now()) if self.config_error is None else None,
        }

    def status(self) -> Dict[str, Any]:
        with self._condition:
            snapshot = deepcopy(self._job)
            snapshot["interval_seconds"] = self.interval_seconds
            snapshot["next_run_at"] = self._timestamp(self._next_deadline)
            snapshot["scheduler"] = self._scheduler_status_locked()
            return snapshot

    def job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Stato di un job recente (ultimi JOB_HISTORY); None = id sconosciuto."""
        with self._condition:
            found = self._history.get(job_id)
            return deepcopy(found) if found is not None else None

    def trigger(self, source: str) -> Dict[str, Any]:
        if source not in {"startup", "schedule", "manual"}:
            raise ValueError(f"invalid News refresh trigger: {source}")
        with self._condition:
            if self._stopped or self._running:
                return {"accepted": False,
                        "reason": "stopped" if self._stopped else "already_running",
                        "job": deepcopy(self._job)}
            now = self._now()
            self._running = True
            self._job = {
                "id": "job-" + uuid.uuid4().hex,
                "status": "running",
                "trigger": source,
                "started_at": self._timestamp(now),
                "finished_at": None,
                "duration_s": None,
                "next_run_at": None,
                "interval_seconds": self.interval_seconds,
                "result": None,
                "error": None,
            }
            job_id = self._job["id"]
            self._history[job_id] = self._job
            while len(self._history) > JOB_HISTORY:
                self._history.popitem(last=False)
            worker = threading.Thread(
                target=self._run_worker,
                args=(job_id, time.monotonic()),
                name="bellomberg-news-refresh",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return {"accepted": True, "job": deepcopy(self._job)}

    def start(self, immediate: bool = False) -> None:
        with self._condition:
            if self._started:
                return
            self._started = True
            self._stopped = False
            self._stop_event.clear()
            if self.config_error:
                print("[news-refresh] giri automatici SPENTI, configurazione non valida: "
                      + self.config_error, flush=True)
            if self.auto_enabled:
                # Leave the scheduler without a deadline until the startup worker
                # has been accepted.  Otherwise the scheduler thread can win the
                # race and label the first run as ``schedule`` instead of
                # ``startup``.  G3: no immediate run by default (PM decision).
                self._next_deadline = (None if immediate else
                                       self._prossimo_consentito(self._now() + self.first_delay_seconds))
                self._job["next_run_at"] = self._timestamp(self._next_deadline)
                self._scheduler = threading.Thread(
                    target=self._scheduler_loop,
                    name="bellomberg-news-refresh-scheduler",
                    daemon=True,
                )
                self._scheduler.start()
        if immediate and self.auto_enabled:
            self.trigger("startup")

    def stop(self) -> None:
        with self._condition:
            if self._stopped and not self._started:
                return
            self._stopped = True
            self._started = False
            self._stop_event.set()
            scheduler = self._scheduler
            worker = self._worker
            self._condition.notify_all()
        current = threading.current_thread()
        deadline = time.monotonic() + self.stop_timeout_seconds
        if scheduler and scheduler is not current:
            scheduler.join(max(0.0, deadline - time.monotonic()))
        if worker and worker is not current:
            worker.join(max(0.0, deadline - time.monotonic()))
        with self._condition:
            if worker is not None and worker.is_alive() and self._job.get("status") == "running":
                # G3: il backend non resta vivo fino a fine giro (porta 8765 occupata).
                # Il thread e' daemon: muore col processo; il giro e' dichiarato abbandonato.
                finished = self._now()
                self._job["status"] = "interrupted"
                self._job["finished_at"] = self._timestamp(finished)
                self._job["error"] = _t(
                    "arresto del backend durante il giro: giro abbandonato dopo %.1f s, "
                    "le notizie gia' salvate restano, l'esito non e' registrato",
                    "backend shutdown during the refresh: refresh abandoned after %.1f s, "
                    "news already saved are kept, the outcome is not recorded") % self.stop_timeout_seconds
                print("[news-refresh] " + self._job["error"], flush=True)
            self._next_deadline = None
            self._scheduler = None
            self._worker = None

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._condition:
                if self._stopped:
                    return
                deadline = self._next_deadline
                if deadline is None:
                    self._condition.wait(0.25)
                    continue
                now = self._now()
                wait_for = deadline - now
                if wait_for > 0:
                    self._condition.wait(min(wait_for, WAIT_CAP_SECONDS))
                    continue
                if self._running:
                    self._next_deadline = now + 0.1
                    continue
                if self._in_pausa(now):
                    # scadenza raggiunta in pausa (PC sospeso, orologio spostato): si rinvia
                    self._next_deadline = self._prossimo_consentito(now)
                    self._job["next_run_at"] = self._timestamp(self._next_deadline)
                    continue
                self._next_deadline = None
            self.trigger("schedule")

    def _run_worker(self, job_id: str, started_mono: float) -> None:
        try:
            result = self._puller(days=1, classify=True, should_stop=self._stop_event.is_set)
        except BaseException as exc:   # REV_G3 R7: anche SystemExit/KeyboardInterrupt chiudono il job
            with self._condition:
                if self._job.get("id") == job_id and self._job.get("status") == "running":
                    finished = self._now()
                    self._job["status"] = "error"
                    self._job["finished_at"] = self._timestamp(finished)
                    self._job["duration_s"] = round(time.monotonic() - started_mono, 3)
                    self._job["error"] = describe_exception(exc)
                    self._job["result"] = None
                    self._set_next_run_locked(finished)
                self._running = False
                self._condition.notify_all()
            if not isinstance(exc, Exception):
                raise
            return

        with self._condition:
            if self._job.get("id") == job_id and self._job.get("status") == "running":
                finished = self._now()
                interrupted = isinstance(result, dict) and bool(result.get("interrupted"))
                self._job["status"] = "interrupted" if interrupted else "success"
                self._job["finished_at"] = self._timestamp(finished)
                self._job["duration_s"] = round(time.monotonic() - started_mono, 3)
                self._job["result"] = result
                self._job["error"] = (_t("giro interrotto dall'arresto del backend",
                                         "refresh interrupted by backend shutdown")
                                      if interrupted else None)
                self._set_next_run_locked(finished)
            self._running = False
            self._condition.notify_all()

    def _set_next_run_locked(self, finished: float) -> None:
        if self._started and self.auto_enabled and not self._stopped:
            passo = (self.weekend_interval_seconds
                     if datetime.fromtimestamp(finished).weekday() in _WEEKEND else self.interval_seconds)
            self._next_deadline = self._prossimo_consentito(finished + passo)
            self._job["next_run_at"] = self._timestamp(self._next_deadline)
        else:
            self._next_deadline = None
            self._job["next_run_at"] = None
