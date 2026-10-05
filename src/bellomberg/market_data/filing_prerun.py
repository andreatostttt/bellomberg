"""Aggiornamento dei profili filing scaduti a inizio run del Consigliere.

Mai bloccante: si attende al più fino a `avvio_run + attesa_max_s` (scadenza assoluta
dall'avvio della run); oltre, la scheda usa l'ultimo confronto e lo dichiara. Un run già
attivo (thread del backend) si attende, non se ne lancia un secondo. Nessuna chiamata AI.

Revisione 04/10/2026 (R9): i thread sono DAEMON (un download appeso non tiene vivo il
processo del comitato oltre la fine della run) e c'e' un tetto di tempo TOTALE
(`tetto_totale_s`, default TETTO_TOTALE_S): oltre, nessun titolo nuovo parte e la scheda lo
dichiara NON AGGIORNATO. A fine run chiudi() annulla i titoli non ancora partiti e dichiara
nel log quelli ancora in corso; un run interrotto a fine processo resta orfano e lo recupera
il backend (ETA_ORFANI_ORE).
"""
import logging
import queue
import threading
import time
from concurrent.futures import Future, wait

logger = logging.getLogger(__name__)

TETTO_TOTALE_S = 900  # oltre questo tempo dall'avvio della run nessun titolo nuovo parte
OLTRE = "aggiornamento oltre {s:.0f} s (in corso)"
OLTRE_TETTO = "non partito: tetto totale di {s:.0f} s del pre-run superato"
ATTIVI = ("queued", "running")
AGGIORNATI = ("ok", "parziale", "skipped")  # skipped = controllato, nessun deposito nuovo


class AggiornamentoPreRun:
    def __init__(self, service, tickers, *, avvio_run, attesa_max_s=60, max_workers=4,
                 orologio=time.monotonic, sonno=time.sleep, esclusi_fn=None, tetto_totale_s=TETTO_TOTALE_S):
        self.service = service
        self.esclusi_fn = esclusi_fn
        self.tickers = list(dict.fromkeys(t for t in tickers if isinstance(t, str) and t))
        self.scadenza = avvio_run + attesa_max_s
        self.tetto = avvio_run + tetto_totale_s
        self.attesa_max_s, self.max_workers, self.tetto_totale_s = attesa_max_s, max_workers, tetto_totale_s
        self.orologio, self.sonno = orologio, sonno
        self._futuri, self._errore, self._seguiti = {}, None, set()
        self._coda = None
        self._oltre_tetto = set()

    def avvia(self):
        """Lancia in background i profili dovuti (e attende quelli con un run già attivo). Mai solleva."""
        try:
            store = self.service.store
            esclusi = self._esclusi()
            if esclusi is None:
                return  # preferenze illeggibili: nessun aggiornamento, esiti() lo dichiara
            dovuti = {d["ticker"] for d in store.next_due()} & set(self.tickers)
            attesi = set()
            for t in self.tickers:
                if t in esclusi:
                    continue  # escluso dall'utente: nessun aggiornamento (la scheda lo dichiara)
                profilo = store.get_profile(t)
                if not profilo or not profilo.get("enabled"):
                    continue  # senza profilo o disabilitato: nessuna voce (freschezza dall'archivio)
                self._seguiti.add(t)
                # next_due esclude i ticker con un run attivo: si attende quello
                if t not in dovuti and ((store.list_runs(t, limit=1) or [{}])[0].get("status") in ATTIVI):
                    attesi.add(t)
            lavori = sorted((dovuti & self._seguiti) | attesi)
            if not lavori:
                return
            # Thread daemon propri (non ThreadPoolExecutor: i suoi thread si attendono all'uscita
            # dell'interprete e terrebbero vivo il processo della run su un download appeso).
            coda = self._coda = queue.Queue()
            for t in lavori:
                self._futuri[t] = Future()
                coda.put(t)
            for i in range(min(self.max_workers, len(lavori))):
                threading.Thread(target=self._lavoratore, args=(coda,), name=f"filing-prerun-{i}",
                                 daemon=True).start()
        except Exception as exc:
            self._errore = f"archivio filing: {type(exc).__name__}: {exc}"[:160]

    def _lavoratore(self, coda):
        """Prende i titoli dalla coda finche' ce ne sono; oltre il tetto totale non ne parte nessuno."""
        while True:
            try:
                t = coda.get_nowait()
            except queue.Empty:
                return
            futuro = self._futuri[t]
            if self.orologio() >= self.tetto:
                self._oltre_tetto.add(t)
                futuro.cancel()
                continue
            if not futuro.set_running_or_notify_cancel():
                continue  # annullato da chiudi(): nessun run avviato
            try:
                futuro.set_result(self._uno(t))
            except BaseException as exc:  # _uno non solleva; per sicurezza il futuro si chiude sempre
                futuro.set_exception(exc)

    def _esclusi(self):
        """Titoli esclusi dalle preferenze; illeggibili: None e nessun aggiornamento, dichiarato per
        titolo (seguito revisione G1: prima si scaricavano anche gli esclusi, solo nel log)."""
        try:
            if self.esclusi_fn is None:
                from bellomberg.storage.filing_preferenze import esclusi_sicuri
                return esclusi_sicuri()
            return frozenset(self.esclusi_fn() or ())
        except Exception as exc:
            logger.warning("filing pre-run: preferenze illeggibili, nessun aggiornamento: %s", exc)
            self._errore = f"preferenze filing illeggibili (esclusioni non verificabili): {exc}"[:160]
            return None

    def _ultimo(self, ticker):
        """Esito dell'ultimo run (attende fino alla scadenza se ancora attivo)."""
        while True:
            ultimo = (self.service.store.list_runs(ticker, limit=1) or [{}])[0]
            if ultimo.get("status") not in ATTIVI:
                return ultimo.get("status"), ultimo.get("reason")
            residuo = self.scadenza - self.orologio()
            if residuo <= 0:
                return "in_corso", None
            self.sonno(min(0.5, residuo))

    def _uno(self, ticker):
        from bellomberg.storage.filing_store import RunAlreadyActive, RunNotDue
        try:
            try:
                r = self.service.run_programmato(ticker)  # controllo leggero se nessun deposito nuovo
                return r.get("status"), r.get("reason")
            except (RunAlreadyActive, RunNotDue):
                # attivo: si attende lo stesso run; non dovuto: un altro run si è appena concluso
                return self._ultimo(ticker)
        except Exception as exc:
            return "errore", f"{type(exc).__name__}: {exc}"

    def chiudi(self):
        """Fine run: annulla i lavori non ancora partiti (nessun run avviato), quelli in corso
        finiscono. Il processo non resta vivo per titoli che nessuno leggera'. Mai solleva."""
        try:
            for f in self._futuri.values():
                f.cancel()  # solo i non partiti: un futuro in corso non si annulla
            in_corso = sorted(t for t, f in self._futuri.items() if f.running())
            if in_corso:
                logger.warning("filing pre-run: a fine run ancora in corso (thread daemon, non trattengono "
                               "il processo; run orfani recuperati dal backend): %s", ", ".join(in_corso))
        except Exception as exc:
            logger.warning("filing pre-run: chiusura non riuscita: %s", exc)

    def esiti(self):
        """{ticker: {"stato", "motivo"}} entro la scadenza; mai solleva."""
        if self._errore:
            return {t: {"stato": "non_aggiornato", "motivo": self._errore} for t in self.tickers}
        if self._futuri:
            wait(list(self._futuri.values()), timeout=max(0.0, self.scadenza - self.orologio()))
        oltre = OLTRE.format(s=self.attesa_max_s)
        out = {}
        for t in self.tickers:
            if t not in self._seguiti:
                continue
            f = self._futuri.get(t)
            if f is None:  # non dovuto
                out[t] = {"stato": "aggiornato", "motivo": None}
                continue
            if t in self._oltre_tetto:
                out[t] = {"stato": "non_aggiornato", "motivo": OLTRE_TETTO.format(s=self.tetto_totale_s)}
                continue
            if not f.done() or f.cancelled():
                out[t] = {"stato": "non_aggiornato", "motivo": oltre}
                continue
            stato, motivo = f.result()
            if stato in AGGIORNATI:
                out[t] = {"stato": "aggiornato", "motivo": None}
            elif stato == "in_corso":
                out[t] = {"stato": "non_aggiornato", "motivo": oltre}
            else:
                out[t] = {"stato": "non_aggiornato",
                          "motivo": f"controllo in errore: {str(motivo or stato)[:100]}"}
        return out
