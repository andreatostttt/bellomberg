"""Revisione import 04/10/2026 (G2b, R9): il pre-run filing non tiene vivo il processo del comitato.

Thread daemon (un download appeso non trattiene il processo a fine run) e tetto di tempo TOTALE
oltre il quale nessun titolo nuovo parte; l'attesa resta quella della run (max 60 s) e i titoli
non partiti sono dichiarati NON AGGIORNATO col motivo.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from bellomberg.market_data.filing_prerun import AggiornamentoPreRun
from tests.test_filing_prerun import _Store, _Svc

ALBERO = Path(__file__).resolve().parents[1]


def test_thread_del_pre_run_daemon():
    blocco = threading.Event()
    store = _Store(["NOVA.DE"])

    class Appeso(_Svc):
        def run_programmato(self, t):
            self.chiamate.append(t)
            blocco.wait(10)
            return {"ticker": t, "status": "ok", "reason": None}

    agg = AggiornamentoPreRun(Appeso(store, {}), ["NOVA.DE"], avvio_run=time.monotonic(), attesa_max_s=0.1,
                              esclusi_fn=frozenset)
    agg.avvia()
    try:
        time.sleep(0.1)
        lavoratori = [t for t in threading.enumerate() if t.name.startswith("filing-prerun")]
        assert lavoratori and all(t.daemon for t in lavoratori)
        assert agg.esiti()["NOVA.DE"]["stato"] == "non_aggiornato"
    finally:
        blocco.set()


def test_tetto_totale_nessun_titolo_nuovo_dopo_la_scadenza():
    store = _Store(["AAA.MI", "BBB.MI"])
    svc = _Svc(store, {"AAA.MI": 0.4})
    agg = AggiornamentoPreRun(svc, ["AAA.MI", "BBB.MI"], avvio_run=time.monotonic(), attesa_max_s=0.1,
                              max_workers=1, tetto_totale_s=0.2, esclusi_fn=frozenset)
    agg.avvia()
    time.sleep(0.8)                      # AAA finisce dopo il tetto: BBB non deve partire
    assert svc.chiamate == ["AAA.MI"]
    esiti = agg.esiti()
    assert esiti["BBB.MI"]["stato"] == "non_aggiornato" and "tetto" in esiti["BBB.MI"]["motivo"]
    assert esiti["AAA.MI"]["stato"] == "aggiornato"


def test_processo_esce_anche_con_un_download_appeso():
    """Il processo della run termina subito anche se un titolo e' fermo in rete (prima: fino al kill)."""
    codice = (
        "import time, bellomberg\n"
        "from bellomberg.market_data.filing_prerun import AggiornamentoPreRun\n"
        "class St:\n"
        "    def next_due(self, now=None): return [{'ticker': 'ZZTEST.MI'}]\n"
        "    def get_profile(self, t): return {'enabled': True}\n"
        "    def list_runs(self, t, limit=20): return [{'status': 'ok'}]\n"
        "class Sv:\n"
        "    store = St()\n"
        "    def run_programmato(self, t): time.sleep(60); return {'status': 'ok'}\n"
        "a = AggiornamentoPreRun(Sv(), ['ZZTEST.MI'], avvio_run=time.monotonic(), attesa_max_s=0.2,\n"
        "                        esclusi_fn=frozenset)\n"
        "a.avvia(); print(a.esiti()); a.chiudi(); print('FINE', bellomberg.__file__, flush=True)\n")
    env = {**os.environ, "PYTHONPATH": str(ALBERO / "src") + os.pathsep + str(ALBERO),
           "PYTHONDONTWRITEBYTECODE": "1"}
    inizio = time.monotonic()
    try:
        r = subprocess.run([sys.executable, "-B", "-c", codice], cwd=ALBERO, env=env, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=30)
    except subprocess.TimeoutExpired:
        raise AssertionError("processo trattenuto oltre 30 s dal pre-run (thread non daemon)")
    durata = time.monotonic() - inizio
    assert r.returncode == 0, r.stderr[-800:]
    assert "FINE" in r.stdout and str(ALBERO / "src") in r.stdout  # codice di questo albero
    assert "non_aggiornato" in r.stdout
    assert durata < 20, f"processo trattenuto {durata:.1f} s dal pre-run"
