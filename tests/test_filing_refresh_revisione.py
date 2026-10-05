"""Revisione import 04/10/2026 (G2b): gestore del controllo filing automatico.

R5 (decisione PM): acceso, una volta al giorno, primo giro 10 minuti dopo l'avvio del backend.
R13: una variabile del gestore non valida non ferma il backend: controllo spento e dichiarato.
R6: lo spegnimento non resta appeso a un giro in corso (join con tempo massimo, giro «interrotto»).
"""
import threading
import time
from datetime import datetime

import pytest

from bellomberg.market_data.filing_refresh import FilingRefreshManager

VARIABILI = ("FILING_AUTO_REFRESH_ENABLED", "FILING_AUTO_REFRESH_INTERVAL_S", "FILING_AUTO_REFRESH_DELAY_S")


class _Svc:
    def __init__(self, blocco=None):
        self.chiamate, self.blocco = [], blocco

    def recupera_orfani(self, **kw):
        self.chiamate.append("orfani")
        return []

    def run_due(self, now=None, *, tickers=None, max_workers=1, escludi=None, stop_event=None):
        self.chiamate.append("due")
        if self.blocco is not None:
            self.blocco.wait(30)
        return []


def _aspetta(cond, s=3):
    fine = time.time() + s
    while time.time() < fine and not cond():
        time.sleep(0.01)
    return cond()


@pytest.fixture
def env_pulito(monkeypatch):
    for nome in VARIABILI:
        monkeypatch.delenv(nome, raising=False)
    return monkeypatch


def test_default_acceso_una_volta_al_giorno_primo_giro_dopo_dieci_minuti(env_pulito):
    svc = _Svc()
    m = FilingRefreshManager(lambda: svc, esclusi_fn=frozenset)
    assert m.auto_enabled is True and m.config_error is None
    assert m.interval_seconds == 86_400 and m.startup_delay_seconds == 600
    prima = time.time()
    m.start()  # come nel lifespan: nessun giro subito all'apertura dell'app
    try:
        time.sleep(0.3)
        assert svc.chiamate == [] and m.status()["status"] == "idle"
        prossimo = datetime.fromisoformat(m.status()["next_run_at"]).timestamp()
        assert 590 <= prossimo - prima <= 610
    finally:
        m.stop()


def test_primo_giro_ritardato_etichettato_startup(env_pulito):
    svc = _Svc()
    m = FilingRefreshManager(lambda: svc, startup_delay_seconds=0.2, interval_seconds=3600, esclusi_fn=frozenset)
    m.start()
    try:
        assert _aspetta(lambda: m.status()["status"] == "success")
        assert m.status()["trigger"] == "startup" and svc.chiamate == ["orfani", "due"]
    finally:
        m.stop()


def test_parametri_da_env(env_pulito):
    env_pulito.setenv("FILING_AUTO_REFRESH_INTERVAL_S", "7200")
    env_pulito.setenv("FILING_AUTO_REFRESH_DELAY_S", "0")
    m = FilingRefreshManager(lambda: _Svc())
    assert (m.interval_seconds, m.startup_delay_seconds, m.config_error) == (7200, 0, None)


@pytest.mark.parametrize("nome,valore", [("FILING_AUTO_REFRESH_ENABLED", "si"),
                                         ("FILING_AUTO_REFRESH_ENABLED", ""),
                                         ("FILING_AUTO_REFRESH_INTERVAL_S", ""),
                                         ("FILING_AUTO_REFRESH_INTERVAL_S", "un giorno"),
                                         ("FILING_AUTO_REFRESH_INTERVAL_S", "10"),
                                         ("FILING_AUTO_REFRESH_DELAY_S", "-5"),
                                         ("FILING_AUTO_REFRESH_DELAY_S", "10m")])
def test_env_non_valida_backend_vivo_controllo_spento_e_dichiarato(env_pulito, nome, valore):
    env_pulito.setenv(nome, valore)
    svc = _Svc()
    m = FilingRefreshManager(lambda: svc, esclusi_fn=frozenset)  # mai un'eccezione all'import del backend
    st = m.status()
    assert st["auto_enabled"] is False and nome in (st["config_error"] or "")
    m.start()
    try:
        time.sleep(0.2)
        assert svc.chiamate == [] and m.status()["next_run_at"] is None
        with pytest.raises(ValueError, match=nome):
            m.imposta_auto(True)
        assert m.trigger("schedule")["accepted"] is False
    finally:
        m.stop()


def test_stop_con_giro_appeso_non_blocca_e_dichiara_interrotto(env_pulito):
    blocco = threading.Event()
    svc = _Svc(blocco)
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True, esclusi_fn=frozenset)
    m.start(immediate=True)
    try:
        assert _aspetta(lambda: "due" in svc.chiamate)
        lavoratori = [t for t in threading.enumerate() if t.name == "bellomberg-filing-refresh"]
        assert lavoratori and all(t.daemon for t in lavoratori)
        inizio = time.monotonic()
        m.stop(timeout=0.3)
        assert time.monotonic() - inizio < 2
        st = m.status()
        assert st["status"] == "interrotto" and "interrotto" in st["error"]
    finally:
        blocco.set()


def test_lifespan_avvia_col_ritardo_configurato():
    import inspect
    from bellomberg.api import bellomberg_api
    sorgente = inspect.getsource(bellomberg_api.lifespan)
    assert "filing_refresh_manager.start()" in sorgente


# --- seguito revisione (REV_G2b A2): il PROCESSO esce, anche con il run_due VERO appeso ---

def test_processo_del_backend_esce_con_run_due_vero_appeso(tmp_path):
    """Il FilingService.run_due vero (pool di 4) con un titolo fermo in rete: dopo stop() il
    processo deve terminare subito (prima: thread di concurrent.futures attesi all'uscita, 43 s)."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    albero = Path(__file__).resolve().parents[1]
    codice = (
        "import sqlite3, sys, threading, time\n"
        "from pathlib import Path\n"
        "from bellomberg.market_data.filing_service import FilingService\n"
        "from bellomberg.market_data.filing_refresh import FilingRefreshManager\n"
        "from bellomberg.storage.filing_store import FilingStore, ensure_schema\n"
        "from tests.test_filing_refresh import PROFILO\n"
        "base = Path(sys.argv[1])\n"
        "with sqlite3.connect(base / 'f.sqlite') as c: ensure_schema(c)\n"
        "store = FilingStore(base / 'f.sqlite')\n"
        "for t in ('ZZA.MI', 'ZZB.MI', 'ZZC.MI', 'ZZD.MI', 'ZZE.MI', 'ZZF.MI'):\n"
        "    store.set_profile(t, {**PROFILO, 'ticker': t}, interval_hours=24)\n"
        "partiti = []\n"
        "class Appeso(FilingService):\n"
        "    def run_programmato(self, ticker):\n"
        "        partiti.append(ticker); time.sleep(40); return {'ticker': ticker, 'status': 'ok'}\n"
        "svc = Appeso(store, base / 'arch', pipeline=lambda *a, **k: {}, indexer=lambda *a: {'status': 'skipped'})\n"
        "m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True, esclusi_fn=frozenset,\n"
        "                         scoperte_fn=lambda *a, **k: None)\n"
        "m.start(immediate=True)\n"
        "fine = time.time() + 30\n"
        "while len(partiti) < 4 and time.time() < fine: time.sleep(0.05)\n"
        "m.stop(timeout=1)\n"
        "print('PARTITI', len(partiti), 'STATO', m.status()['status'], 'STOP_AT', time.time(), flush=True)\n")
    env = {**os.environ, "PYTHONPATH": str(albero / "src") + os.pathsep + str(albero),
           "PYTHONDONTWRITEBYTECODE": "1", "FILING_AUTO_REFRESH_ENABLED": "true"}
    # Si misura dallo stop() all'uscita del processo: avvio e sqlite possono durare decine di
    # secondi su una macchina carica. Prima della cura: almeno i 40 s del titolo appeso.
    try:
        r = subprocess.run([sys.executable, "-B", "-c", codice, str(tmp_path)], cwd=albero, env=env,
                           capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        raise AssertionError("processo del backend trattenuto oltre 300 s")
    uscita = time.time()
    assert r.returncode == 0, r.stderr[-1500:]
    assert "PARTITI 4" in r.stdout and "STATO interrotto" in r.stdout, r.stdout
    dopo_lo_stop = uscita - float(r.stdout.split("STOP_AT")[1].split()[0])
    assert dopo_lo_stop < 15, f"processo uscito {dopo_lo_stop:.1f} s dopo stop(): {r.stdout}"


def test_run_due_vero_non_parte_altri_titoli_dopo_lo_stop(tmp_path):
    import threading as th
    from bellomberg.market_data.filing_service import FilingService
    from tests.test_filing_refresh import _store
    store = _store(tmp_path, ("ZZA.MI", "ZZB.MI", "ZZC.MI"))
    stop, partiti = th.Event(), []

    class Uno(FilingService):
        def run_programmato(self, ticker):
            partiti.append(ticker)
            stop.set()  # lo spegnimento arriva durante il primo titolo
            return {"ticker": ticker, "status": "ok"}

    svc = Uno(store, tmp_path / "arch", pipeline=lambda *a, **k: {}, indexer=lambda *a: {"status": "skipped"})
    esiti = svc.run_due(max_workers=1, stop_event=stop)
    assert len(partiti) == 1
    assert sorted(e["status"] for e in esiti) == ["interrotto", "interrotto", "ok"]
