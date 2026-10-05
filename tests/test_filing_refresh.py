import pytest
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema

PROFILO = {"ticker": "NOVA.DE", "emittente_id": "CIK:0009990001", "lingua": "en", "tipo": "annuale",
           "perimetro": "consolidato",
           "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
           "sezioni": {"rischi": {"inizio": "Risk Factors", "fine": "Properties"}}}


def _store(tmp_path, tickers=("NOVA.DE",)):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    for t in tickers:
        store.set_profile(t, {**PROFILO, "ticker": t}, interval_hours=24)
    return store


def _ok(profilo, archivio):
    return {"stato": "ok", "motivi": [], "confronto_corrente": {"stato": "ok", "cambiamenti": []}}


def _service(store, tmp_path, pipeline=_ok):
    return FilingService(store, tmp_path / "arch", pipeline=pipeline, indexer=lambda *a: {"status": "skipped"})


def test_orfano_vecchio_recuperato_recente_no(tmp_path):
    store = _store(tmp_path, ("NOVA.DE", "KORE.MI"))
    vecchio = store.start_run("NOVA.DE")
    recente = store.start_run("KORE.MI")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE filing_runs SET started_at=? WHERE id=?",
                     ((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(), vecchio["id"]))
    svc = _service(store, tmp_path)
    assert svc.recupera_orfani(eta_max_ore=2) == [vecchio["id"]]
    assert store.get_run(vecchio["id"])["status"] == "errore"
    assert "orfano" in store.get_run(vecchio["id"])["reason"]
    assert store.get_run(recente["id"])["status"] == "queued"


def test_run_due_limitato_ai_titoli_e_in_parallelo(tmp_path):
    # Cantiere zero rossi 05/10 (TIMING): prima la sovrapposizione si sperava con sleep(0.2) (rosso
    # sotto carico: il secondo thread partiva dopo la fine del primo). Ora una BARRIERA a 2: passa
    # solo se i due titoli sono DAVVERO in volo insieme; serializzati, il primo resta fermo fino al
    # timeout (rete larga 10 s, mai raggiunta quando il parallelo c'e') e i run cadono in errore.
    store = _store(tmp_path, ("NOVA.DE", "KORE.MI", "ACME.PA"))
    attivi, massimo, incontrati = [0], [0], []
    lock = threading.Lock()
    barriera = threading.Barrier(2)

    def lenta(profilo, archivio):
        with lock:
            attivi[0] += 1
            massimo[0] = max(massimo[0], attivi[0])
        try:
            barriera.wait(timeout=10)       # entrambi in volo nello stesso istante
            incontrati.append(profilo["ticker"])
        finally:
            with lock:
                attivi[0] -= 1
        return _ok(profilo, archivio)

    out = _service(store, tmp_path, lenta).run_due(tickers={"NOVA.DE", "KORE.MI"}, max_workers=4)
    assert sorted(o["ticker"] for o in out) == ["KORE.MI", "NOVA.DE"]
    assert sorted(incontrati) == ["KORE.MI", "NOVA.DE"], out
    assert all(o["status"] == "ok" for o in out), out
    assert massimo[0] == 2
    assert store.list_runs("ACME.PA", limit=1) == []


# --- FilingRefreshManager (fase D, task 3) ---
from bellomberg.market_data.filing_refresh import FilingRefreshManager, filing_auto_refresh_enabled


class _SvcFinto:
    def __init__(self, ritardo=0.0):
        self.chiamate, self.ritardo = [], ritardo

    def recupera_orfani(self, **kw):
        self.chiamate.append("orfani")
        return []

    def run_due(self, now=None, *, tickers=None, max_workers=1, escludi=None, stop_event=None):
        self.chiamate.append("due")
        time.sleep(self.ritardo)
        return [{"ticker": "NOVA.DE", "status": "ok"}]


def _aspetta(cond, s=3):
    fine = time.time() + s
    while time.time() < fine and not cond():
        time.sleep(0.01)
    return cond()


def test_avvio_recupera_e_controlla_poi_periodico():
    svc = _SvcFinto()
    m = FilingRefreshManager(lambda: svc, interval_seconds=0.2, auto_enabled=True)
    m.start(immediate=True)
    try:
        assert _aspetta(lambda: svc.chiamate.count("due") >= 2)
        assert svc.chiamate[:2] == ["orfani", "due"]
        assert m.status()["trigger"] in ("startup", "schedule")
    finally:
        m.stop()


def test_un_solo_lavoro_alla_volta():
    svc = _SvcFinto(ritardo=0.3)
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True)
    m.start(immediate=False)
    try:
        assert m.trigger("manual")["accepted"] is True
        assert m.trigger("activation")["accepted"] is False
    finally:
        m.stop()


def test_archivio_assente_errore_dichiarato_e_thread_vivo():
    def rotta():
        raise FileNotFoundError("DB filing assente")
    m = FilingRefreshManager(rotta, interval_seconds=3600, auto_enabled=True)
    m.start(immediate=True)
    try:
        assert _aspetta(lambda: m.status()["status"] == "error")
        assert "DB filing assente" in m.status()["error"]
        assert m.trigger("manual")["accepted"] is True
    finally:
        m.stop()


def test_opt_out_da_env(monkeypatch):
    monkeypatch.setenv("FILING_AUTO_REFRESH_ENABLED", "off")
    assert filing_auto_refresh_enabled() is False
    monkeypatch.delenv("FILING_AUTO_REFRESH_ENABLED")
    assert filing_auto_refresh_enabled() is True
    # Present but not an explicit true/false: declared error, never a silent "enabled".
    monkeypatch.setenv("FILING_AUTO_REFRESH_ENABLED", "")
    with pytest.raises(ValueError, match="FILING_AUTO_REFRESH_ENABLED"):
        filing_auto_refresh_enabled()


# --- revisione finale M1: un'attivazione durante un lavoro chiede un altro giro subito dopo ---

def test_attivazione_durante_un_lavoro_rilancia_subito_dopo():
    svc = _SvcFinto(ritardo=0.3)
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True)
    m.start(immediate=False)
    try:
        assert m.trigger("manual")["accepted"] is True
        occupato = m.trigger("activation")
        assert occupato["accepted"] is False and occupato["rerun"] is True
        assert m.trigger("activation")["rerun"] is True       # un solo giro in coda
        assert _aspetta(lambda: svc.chiamate.count("due") == 2 and m.status()["status"] == "success")
        assert m.status()["trigger"] == "activation"
        time.sleep(0.4)
        assert svc.chiamate.count("due") == 2
    finally:
        m.stop()


def test_controllo_orario_durante_un_lavoro_non_si_accoda():
    svc = _SvcFinto(ritardo=0.2)
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True)
    m.start(immediate=False)
    try:
        assert m.trigger("manual")["accepted"] is True
        assert m.trigger("schedule").get("rerun") is False
        assert _aspetta(lambda: m.status()["status"] == "success")
        time.sleep(0.3)
        assert svc.chiamate.count("due") == 1
    finally:
        m.stop()


# --- revisione finale M2: i titoli esclusi non si aggiornano ---

def test_run_due_salta_gli_esclusi(tmp_path):
    store = _store(tmp_path, ("NOVA.DE", "KORE.MI"))
    out = _service(store, tmp_path).run_due(escludi={"KORE.MI"})
    assert [o["ticker"] for o in out] == ["NOVA.DE"] and store.list_runs("KORE.MI") == []


class _SvcEsclusi(_SvcFinto):
    def run_due(self, now=None, *, tickers=None, max_workers=1, escludi=None, stop_event=None):
        self.escludi = escludi
        return super().run_due(now, tickers=tickers, max_workers=max_workers)


def test_manager_passa_le_esclusioni_e_con_preferenze_illeggibili_si_ferma():
    svc = _SvcEsclusi()
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True,
                             esclusi_fn=lambda: frozenset({"KORE.MI"}))
    m.start(immediate=False)
    try:
        m.trigger("manual")
        assert _aspetta(lambda: m.status()["status"] == "success") and svc.escludi == {"KORE.MI"}
    finally:
        m.stop()
    from bellomberg.storage import filing_preferenze
    assert filing_preferenze.esclusi_sicuri("/nonexistent-dir/ok.json") == frozenset()  # file assente: nessuna scelta
    import pathlib, tempfile
    rotto = pathlib.Path(tempfile.mkdtemp()) / "p.json"
    rotto.write_text("{rotto", encoding="utf-8")
    # Seguito revisione G1: illeggibili non e' piu' «nessuna esclusione» zitta.
    with pytest.raises(ValueError, match="illeggibili"):
        filing_preferenze.esclusi_sicuri(rotto)
    svc = _SvcEsclusi()
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True,
                             esclusi_fn=lambda: filing_preferenze.esclusi_sicuri(rotto))
    m.start(immediate=False)
    try:
        m.trigger("manual")
        assert _aspetta(lambda: m.status()["status"] == "error")
        assert "illeggibili" in m.status()["error"] and "due" not in svc.chiamate
    finally:
        m.stop()


# 04/10/2026 (fase E, prova reale): lo stato del gestore teneva i run interi (5,2 MB con 6 titoli)
# e la panoramica /filings lo inoltrava a ogni polling della pagina Filing.
class _SvcPesante(_SvcFinto):
    def recupera_orfani(self, **kw):
        return [{"id": 3, "ticker": "KORE.DE", "status": "errore", "result": {"x": "y" * 10_000}}]

    def run_due(self, now=None, *, tickers=None, max_workers=1, escludi=None, stop_event=None):
        self.chiamate.append("due")
        return [{"id": 7, "ticker": "NOVA.DE", "status": "ok", "reason": None, "result": {"testo": "z" * 200_000}},
                {"ticker": "ACME.MI", "status": "skipped", "reason": "run gia' attivo"}]


def test_stato_del_gestore_compatto():
    import json
    svc = _SvcPesante()
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True)
    m.start(immediate=True)
    try:
        assert _aspetta(lambda: m.status()["status"] == "success")
        st = m.status()
        assert len(json.dumps(st)) < 2000
        assert st["result"]["esiti"] == [{"id": 7, "ticker": "NOVA.DE", "status": "ok", "reason": None},
                                         {"id": None, "ticker": "ACME.MI", "status": "skipped", "reason": "run gia' attivo"}]
        assert st["result"]["recuperati"] == [{"id": 3, "ticker": "KORE.DE", "status": "errore", "reason": None}]
    finally:
        m.stop()
