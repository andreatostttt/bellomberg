"""Controllo periodico dei profili filing scaduti (thread del backend).

Una volta al giorno (FILING_AUTO_REFRESH_INTERVAL_S, default 86400): recupero dei run
orfani, poi run_due su tutti i profili attivi (scadenza per profilo, 24 h per gli
automatici). Il primo giro parte FILING_AUTO_REFRESH_DELAY_S dopo l'avvio del backend
(default 600 = 10 minuti; decisione PM 04/10/2026: l'app avvia il backend da sola, mai
rete subito all'apertura). Un solo lavoro alla volta: lo scheduler, il pulsante e
l'attivazione in blocco passano tutti da trigger(). Nessuna AI: i run periodici saltano
il giudizio qualitativo anche dove e' attivo (solo "Verifica ora").

Configurazione: variabile assente = default documentato; presente ma vuota o non valida =
il controllo automatico resta SPENTO e `status()["config_error"]` dice quale variabile
(il backend parte comunque: un errore di questa pagina non ferma il terminale).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Dict, Optional

INTERVALLO_S = 86_400       # default di FILING_AUTO_REFRESH_INTERVAL_S: una volta al giorno
RITARDO_AVVIO_S = 600       # default di FILING_AUTO_REFRESH_DELAY_S: 10 minuti dopo l'avvio
TEMPO_STOP_S = 5.0          # attesa massima dei thread allo spegnimento (porta 8765 mai trattenuta)
ETA_ORFANI_ORE = 2
_log = logging.getLogger(__name__)


def _compatto(run: Any) -> Dict[str, Any]:
    r = run if isinstance(run, dict) else {}
    return {k: r.get(k) for k in ("id", "ticker", "status", "reason")}


def filing_auto_refresh_enabled(value: Any = None) -> bool:
    """Opt-out da env: assente = attivo; un valore che non e' vero/falso esplicito e' un errore."""
    raw = os.environ.get("FILING_AUTO_REFRESH_ENABLED") if value is None else value
    if raw is None:
        return True
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError("%s: valore non valido %r (usa true/false)" % ("FILING_AUTO_REFRESH_ENABLED", raw))


def filing_auto_refresh_forzato_spento() -> bool:
    """Env FILING_AUTO_REFRESH_ENABLED esplicitamente falsa: vince sull'interruttore della pagina."""
    raw = os.environ.get("FILING_AUTO_REFRESH_ENABLED")
    return raw is not None and not filing_auto_refresh_enabled(raw)


def _secondi_da_env(nome: str, default: float, minimo: int) -> float:
    """Secondi interi da env: assente = default; presente ma vuota, non intera o sotto il
    minimo = ValueError col nome della variabile (mai un ripiego zitto sul default)."""
    raw = os.environ.get(nome)
    if raw is None:
        return float(default)
    text = str(raw).strip()
    if not text.isascii() or not text.isdigit() or int(text) < minimo:
        raise ValueError("%s: valore non valido %r (secondi interi >= %d)" % (nome, raw, minimo))
    return float(int(text))


def filing_auto_refresh_interval_s() -> float:
    return _secondi_da_env("FILING_AUTO_REFRESH_INTERVAL_S", INTERVALLO_S, 60)


def filing_auto_refresh_delay_s() -> float:
    return _secondi_da_env("FILING_AUTO_REFRESH_DELAY_S", RITARDO_AVVIO_S, 0)


class FilingRefreshManager:
    SORGENTI = {"startup", "schedule", "manual", "activation"}
    # Azioni dell'utente arrivate a lavoro in corso: un solo giro in piu' subito dopo.
    RILANCIABILI = {"manual", "activation"}

    def __init__(
        self,
        service_factory: Optional[Callable[[], Any]] = None,
        *,
        interval_seconds: Optional[float] = None,
        auto_enabled: Optional[bool] = None,
        startup_delay_seconds: Optional[float] = None,
        max_workers: int = 4,
        esclusi_fn: Optional[Callable[[], Any]] = None,
        scoperte_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._factory = service_factory
        # fase F: esplorazione dei siti degli emittenti prima dei run (solo qui, mai nella run
        # del Consigliere); None = esef_sito.scopri_giornaliero sui servizi con un archivio profili
        self._scoperte_fn = scoperte_fn
        self._esclusi_fn = esclusi_fn
        # R13: configurazione non valida = controllo automatico SPENTO e dichiarato nello stato,
        # mai il backend intero fermo all'import (portafoglio e chat restano disponibili).
        self.config_error: Optional[str] = None
        try:
            self.auto_forzato_spento = filing_auto_refresh_forzato_spento()
            self.auto_enabled = (
                filing_auto_refresh_enabled() if auto_enabled is None else bool(auto_enabled)
            )
            self.interval_seconds: Optional[float] = float(
                filing_auto_refresh_interval_s() if interval_seconds is None else interval_seconds)
            self.startup_delay_seconds: Optional[float] = float(
                filing_auto_refresh_delay_s() if startup_delay_seconds is None else startup_delay_seconds)
        except ValueError as exc:
            self.config_error = str(exc)
            self.auto_forzato_spento = True
            self.auto_enabled = False
            self.interval_seconds = None
            self.startup_delay_seconds = None
            _log.error("filing: controllo automatico SPENTO, configurazione non valida: %s", exc)
        self.max_workers = max_workers
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._scheduler: Optional[threading.Thread] = None
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._started = False
        self._stopped = False
        self._next_deadline: Optional[float] = None
        self._rilancio: Optional[str] = None
        self._prossima_sorgente = "schedule"  # "startup" solo per il primo giro ritardato
        self._job: Dict[str, Any] = {
            "id": None, "status": "idle", "trigger": None, "started_at": None,
            "finished_at": None, "next_run_at": None,
            "interval_seconds": self.interval_seconds, "result": None, "error": None,
        }

    @staticmethod
    def _timestamp(epoch: Optional[float] = None) -> Optional[str]:
        return None if epoch is None else datetime.fromtimestamp(epoch).isoformat()

    def _servizio(self) -> Any:
        factory = self._factory
        if factory is None:
            # import tardivo: evita il ciclo con bellomberg_api / filing_routes
            from bellomberg.api.filing_routes import default_service as factory
        return factory()

    def _lavoro(self) -> Dict[str, Any]:
        svc = self._servizio()
        recuperati = svc.recupera_orfani(eta_max_ore=ETA_ORFANI_ORE)
        if self._stop_event.is_set():  # spegnimento: nessuna fase di rete in piu' (giro dichiarato interrotto)
            return {"recuperati": [_compatto(r) for r in recuperati or []], "esiti": [], "interrotto": True}
        if self._esclusi_fn is None:
            from bellomberg.storage.filing_preferenze import esclusi_sicuri as esclusi_fn
        else:
            esclusi_fn = self._esclusi_fn
        # titoli esclusi dall'utente: mai aggiornati; preferenze illeggibili: ValueError, il giro si
        # ferma qui e l'errore va nello stato del job (seguito revisione G1)
        esclusi = esclusi_fn()
        scoperte = None
        if getattr(svc, "store", None) is not None and hasattr(svc.store, "list_profiles"):
            try:
                if self._scoperte_fn is None:
                    from bellomberg.market_data.esef_sito import scopri_giornaliero as scoperte_fn
                else:
                    scoperte_fn = self._scoperte_fn
                scoperte = scoperte_fn(svc.store, escludi=esclusi or ())
            except Exception as exc:  # un sito irraggiungibile non ferma il controllo
                _log.warning("filing: esplorazione dei siti non riuscita: %s: %s", type(exc).__name__, exc)
        esiti = [] if self._stop_event.is_set() else svc.run_due(max_workers=self.max_workers, escludi=esclusi,
                                                                stop_event=self._stop_event)
        # Solo l'esito di ogni run: i run interi stanno nell'archivio. Lo stato si legge a ogni
        # polling della pagina Filing (prova reale 04/10/2026: 5,2 MB con sei titoli).
        out = {"recuperati": [_compatto(r) for r in recuperati or []], "esiti": [_compatto(r) for r in esiti or []]}
        if self._stop_event.is_set():
            out["interrotto"] = True
        if scoperte:
            out["siti"] = scoperte
        return out

    def status(self) -> Dict[str, Any]:
        with self._condition:
            snapshot = deepcopy(self._job)
            snapshot["interval_seconds"] = self.interval_seconds
            snapshot["auto_enabled"] = self.auto_enabled
            snapshot["auto_forzato_spento"] = self.auto_forzato_spento
            snapshot["config_error"] = self.config_error
            return snapshot

    def imposta_auto(self, attivo: bool) -> Dict[str, Any]:
        """Interruttore del controllo giornaliero a runtime (pagina Filing).

        Spento: nessuna scadenza; lo scheduler resta vivo e inattivo (mai `stop()`, mai join).
        Acceso: un solo scheduler (creato solo se manca) e un controllo immediato dei profili
        dovuti (sorgente "schedule": run_programmato, mai giudizio AI). Env esplicitamente
        falsa: accenderlo e' un ValueError."""
        if type(attivo) is not bool:
            raise ValueError("attivo deve essere booleano")
        if attivo and self.config_error:
            raise ValueError("controllo automatico spento, configurazione non valida: " + self.config_error)
        if attivo and self.auto_forzato_spento:
            raise ValueError("controllo automatico disattivato dalla configurazione (FILING_AUTO_REFRESH_ENABLED)")
        with self._condition:
            self.auto_enabled = attivo
            avviato = self._started and not self._stopped
            if not attivo:
                self._next_deadline = None
                self._job["next_run_at"] = None
            elif avviato and (self._scheduler is None or not self._scheduler.is_alive()):
                self._scheduler = threading.Thread(
                    target=self._scheduler_loop,
                    name="bellomberg-filing-refresh-scheduler", daemon=True,
                )
                self._scheduler.start()
            self._condition.notify_all()
        if attivo and avviato:
            esito = self.trigger("schedule")
            if not esito.get("accepted"):
                with self._condition:
                    # lavoro gia' in corso: la scadenza la fissa la sua fine; altrimenti un giro presto
                    if not self._running and self._next_deadline is None:
                        self._next_deadline = time.time() + 0.1
                        self._job["next_run_at"] = self._timestamp(self._next_deadline)
                    self._condition.notify_all()
        return self.status()

    def applica_preferenza(self, pref_path: Any = None) -> None:
        """All'avvio (prima di start): scelta salvata dall'utente; env esplicitamente falsa vince.
        Preferenze illeggibili: resta il default dell'env, dichiarato nel log."""
        from bellomberg.storage import filing_preferenze
        try:
            salvata = filing_preferenze.controllo_giornaliero(pref_path)
        except (ValueError, OSError) as exc:
            _log.warning("preferenze filing illeggibili: controllo giornaliero dal default dell'env: %s", exc)
            return
        if salvata is None:
            return
        with self._condition:
            self.auto_enabled = salvata and not self.auto_forzato_spento

    def trigger(self, source: str) -> Dict[str, Any]:
        if source not in self.SORGENTI:
            raise ValueError(f"invalid filing refresh trigger: {source}")
        with self._condition:
            if self._running and not self._stopped:
                # profili appena attivati: non aspettano il controllo dell'ora dopo
                if source in self.RILANCIABILI:
                    self._rilancio = source
                return {"accepted": False, "rerun": self._rilancio is not None, "job": deepcopy(self._job)}
            if self._stopped:
                return {"accepted": False, "job": deepcopy(self._job)}
            # l'attivazione è un'azione dell'utente: vale anche con l'auto spento
            if source in {"startup", "schedule"} and not self.auto_enabled:
                return {"accepted": False, "job": deepcopy(self._job)}
            now = time.time()
            self._running = True
            self._job = {
                "id": "job-" + uuid.uuid4().hex, "status": "running", "trigger": source,
                "started_at": self._timestamp(now), "finished_at": None,
                "next_run_at": None, "interval_seconds": self.interval_seconds,
                "result": None, "error": None,
            }
            job_id = self._job["id"]
            worker = threading.Thread(
                target=self._run_worker, args=(job_id,),
                name="bellomberg-filing-refresh", daemon=True,
            )
            self._worker = worker
            worker.start()
            return {"accepted": True, "job": deepcopy(self._job)}

    def start(self, immediate: Optional[bool] = None) -> None:
        """immediate=None (lifespan del backend): primo giro dopo startup_delay_seconds (default
        10 minuti), etichettato "startup"; True: giro subito; False: dopo un intervallo intero."""
        with self._condition:
            if self._started:
                return
            self._started = True
            self._stopped = False
            self._stop_event.clear()
            if self.auto_enabled:
                # senza scadenza finché il lavoro di avvio non è accettato:
                # evita che lo scheduler lo etichetti "schedule"
                ritardo = (None if immediate else
                           self.startup_delay_seconds if immediate is None else self.interval_seconds)
                self._prossima_sorgente = "startup" if immediate is None else "schedule"
                self._next_deadline = None if ritardo is None else time.time() + ritardo
                self._job["next_run_at"] = self._timestamp(self._next_deadline)
                self._scheduler = threading.Thread(
                    target=self._scheduler_loop,
                    name="bellomberg-filing-refresh-scheduler", daemon=True,
                )
                self._scheduler.start()
        if immediate and self.auto_enabled:
            self.trigger("startup")

    def stop(self, timeout: float = TEMPO_STOP_S) -> None:
        """Ferma lo scheduler e attende il giro in corso al massimo `timeout` secondi (R6): i thread
        sono daemon, lo spegnimento del backend non resta appeso e la porta si libera. Un giro
        ancora vivo allo scadere e' dichiarato «interrotto» (stato e log); i run lasciati in
        corso li recupera il prossimo avvio come orfani (ETA_ORFANI_ORE)."""
        with self._condition:
            if self._stopped and not self._started:
                return
            self._stopped = True
            self._started = False
            self._stop_event.set()
            scheduler, worker = self._scheduler, self._worker
            self._condition.notify_all()
        current = threading.current_thread()
        limite = time.monotonic() + max(0.0, float(timeout))
        if scheduler and scheduler is not current:
            scheduler.join(max(0.0, limite - time.monotonic()))
        if worker and worker is not current:
            worker.join(max(0.0, limite - time.monotonic()))
        with self._condition:
            if worker is not None and worker is not current and worker.is_alive():
                self._job["status"] = "interrotto"
                self._job["error"] = ("spegnimento del backend durante il giro: lavoro interrotto dopo "
                                      f"{float(timeout):g} s; i run rimasti in corso li recupera il prossimo avvio")
                _log.warning("filing: %s (giro %s)", self._job["error"], self._job.get("id"))
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
                wait_for = deadline - time.time()
                if wait_for > 0:
                    self._condition.wait(wait_for)
                    continue
                if self._running:
                    self._next_deadline = time.time() + 0.1
                    continue
                self._next_deadline = None
                sorgente, self._prossima_sorgente = self._prossima_sorgente, "schedule"
            self.trigger(sorgente)

    def _run_worker(self, job_id: str) -> None:
        try:
            result = self._lavoro()
            error = ("spegnimento del backend durante il giro: fasi successive saltate"
                     if (result or {}).get("interrotto") else None)
        except Exception as exc:  # il thread resta vivo: l'errore è dichiarato nello stato
            result = None
            error = f"{type(exc).__name__}: {exc}"
        with self._condition:
            if self._job.get("id") == job_id:
                finished = time.time()
                self._job["status"] = ("interrotto" if (result or {}).get("interrotto") else
                                       "error" if error else "success")
                self._job["finished_at"] = self._timestamp(finished)
                self._job["result"] = result
                self._job["error"] = error
                self._set_next_run_locked(finished)
            self._running = False
            rilancio, self._rilancio = self._rilancio, None
            self._condition.notify_all()
        if rilancio:
            self.trigger(rilancio)

    def _set_next_run_locked(self, finished: float) -> None:
        if self._started and self.auto_enabled and not self._stopped:
            self._next_deadline = finished + self.interval_seconds
            self._job["next_run_at"] = self._timestamp(self._next_deadline)
        else:
            self._next_deadline = None
            self._job["next_run_at"] = None
