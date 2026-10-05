import pytest
import threading
import time

from bellomberg.api.news_refresh_manager import (
    NewsRefreshManager,
    news_auto_refresh_enabled,
    news_refresh_interval_seconds,
)


# Nessuna pausa notte/weekend: questi test misurano il ciclo, non il calendario.
SEMPRE = {"quiet_hours": "off", "quiet_days": "none"}


def _wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def test_start_runs_immediately_then_again_without_overlap():
    calls = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def pull(**_kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            calls.append(len(calls) + 1)
        time.sleep(0.01)
        with lock:
            active -= 1
        return {"saved": len(calls)}

    manager = NewsRefreshManager(puller=pull, interval_seconds=0.03, weekend_interval_seconds=0.03,
                                 auto_enabled=True, **SEMPRE)
    manager.start(immediate=True)
    try:
        _wait_for(lambda: len(calls) >= 2)
        _wait_for(lambda: manager.status()["next_run_at"] is not None)
        snapshot = manager.status()
        assert calls[:2] == [1, 2]
        assert max_active == 1
        assert snapshot["interval_seconds"] == 0.03
        assert snapshot["next_run_at"] is not None
        assert snapshot["id"] != "job-1" or snapshot["status"] in {"success", "running"}
    finally:
        manager.stop()


def test_each_trigger_gets_a_new_id_and_running_trigger_is_rejected():
    started = threading.Event()
    release = threading.Event()

    def pull(**_kwargs):
        started.set()
        release.wait(1)
        return {"saved": 1}

    manager = NewsRefreshManager(puller=pull, interval_seconds=60, auto_enabled=True)
    first = manager.trigger("manual")
    try:
        assert first["accepted"] is True
        _wait_for(started.is_set)
        second = manager.trigger("schedule")
        assert second["accepted"] is False
        assert second["job"]["id"] == first["job"]["id"]
        assert second["job"]["status"] == "running"
    finally:
        release.set()
        manager.stop()
    third = manager.trigger("manual")
    assert third["accepted"] is False


def test_worker_error_is_observable_and_scheduler_survives():
    calls = []

    def pull(**_kwargs):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("provider down")
        return {"saved": 2}

    manager = NewsRefreshManager(puller=pull, interval_seconds=0.02, weekend_interval_seconds=0.02,
                                 auto_enabled=True, **SEMPRE)
    manager.start(immediate=True)
    try:
        _wait_for(lambda: len(calls) >= 2)
        _wait_for(lambda: manager.status()["status"] == "success")
        assert manager.status()["result"] == {"saved": 2}
        assert manager.status()["error"] is None
    finally:
        manager.stop()


def test_stop_waits_for_active_worker_and_prevents_new_triggers():
    started = threading.Event()
    release = threading.Event()

    def pull(**_kwargs):
        started.set()
        release.wait(1)
        return {"saved": 1}

    manager = NewsRefreshManager(puller=pull, interval_seconds=60, auto_enabled=True, **SEMPRE)
    manager.start(immediate=True)
    _wait_for(started.is_set)
    stopped = threading.Event()

    def stop():
        manager.stop()
        stopped.set()

    thread = threading.Thread(target=stop)
    thread.start()
    time.sleep(0.02)
    assert not stopped.is_set()
    release.set()
    thread.join(1)
    assert stopped.is_set()
    assert manager.trigger("manual")["accepted"] is False


def test_interval_config_supports_decimals_and_declares_invalid_values(monkeypatch):
    # Maintainer rule: absent = default; a present but invalid value is an error naming the variable.
    assert news_refresh_interval_seconds("1.5") == 90
    monkeypatch.delenv("NEWS_REFRESH_INTERVAL_MINUTES", raising=False)
    assert news_refresh_interval_seconds() == 900
    for bad in ("", "0", "nan", "2000", "0.5", "abc"):
        with pytest.raises(ValueError, match="NEWS_REFRESH_INTERVAL_MINUTES"):
            news_refresh_interval_seconds(bad)
    with pytest.raises(ValueError, match="NEWS_AUTO_REFRESH_ENABLED"):
        news_auto_refresh_enabled("maybe")


def test_auto_refresh_can_be_disabled_without_disabling_manual_trigger():
    assert news_auto_refresh_enabled("false") is False
    manager = NewsRefreshManager(puller=lambda **_: {"saved": 1}, auto_enabled=False)
    result = manager.trigger("manual")
    try:
        assert result["accepted"] is True
    finally:
        manager.stop()


# ---------------------------------------------------------------- G3 (04/10/2026)
# Decisione PM: i giri automatici li fa SOLO il backend, mai di notte (22-07) ne' nel weekend,
# mai subito all'avvio; doppio POST = stesso job; stop() con tempo massimo.
from datetime import datetime as _dt

import bellomberg.api.news_refresh_manager as nrm
from bellomberg.api.news_refresh_manager import (
    news_first_delay_seconds,
    news_quiet_days,
    news_weekend_interval_seconds,
    news_quiet_hours,
)

MER_10 = _dt(2026, 9, 30, 10, 0).timestamp()      # mercoledi 10:00 locale
SAB_12 = _dt(2026, 10, 3, 12, 0).timestamp()      # sabato 12:00
VEN_2155 = _dt(2026, 10, 2, 21, 55).timestamp()   # venerdi 21:55
MER_2330 = _dt(2026, 9, 30, 23, 30).timestamp()   # mercoledi 23:30
LUN_07 = _dt(2026, 10, 5, 7, 0).isoformat()
SAB_07 = _dt(2026, 10, 3, 7, 0).isoformat()
SAB_2330 = _dt(2026, 10, 3, 23, 30).timestamp()   # sabato 23:30
DOM_07 = _dt(2026, 10, 4, 7, 0).isoformat()
GIO_07 = _dt(2026, 10, 1, 7, 0).isoformat()


class _Orologio:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _mai(**_kwargs):
    raise AssertionError("giro automatico partito quando non doveva")


@pytest.fixture
def env_pulito(monkeypatch):
    for nome in ("NEWS_REFRESH_QUIET_HOURS", "NEWS_REFRESH_QUIET_DAYS",
                 "NEWS_REFRESH_FIRST_DELAY_MINUTES", "NEWS_REFRESH_INTERVAL_MINUTES",
                 "NEWS_AUTO_REFRESH_ENABLED", "NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES"):
        monkeypatch.delenv(nome, raising=False)


def test_default_pausa_notte_weekend_e_ritardo(env_pulito):
    assert news_quiet_hours() == (22 * 60, 7 * 60)
    # Decisione PM (04/10 sera): notizie ANCHE nel weekend, ogni 2 ore; la notte resta in pausa
    assert news_quiet_days() == frozenset()
    assert news_weekend_interval_seconds() == 120 * 60
    assert news_first_delay_seconds() == 10 * 60
    assert news_quiet_hours("off") is None and news_quiet_days("none") == frozenset()
    assert news_quiet_hours("09:30-12:00") == (570, 720)
    assert news_quiet_days("mon, sun") == frozenset({0, 6})


@pytest.mark.parametrize("nome,fn,valore", [
    ("NEWS_REFRESH_QUIET_HOURS", news_quiet_hours, ""),
    ("NEWS_REFRESH_QUIET_HOURS", news_quiet_hours, "22-7"),
    ("NEWS_REFRESH_QUIET_HOURS", news_quiet_hours, "25:00-07:00"),
    ("NEWS_REFRESH_QUIET_HOURS", news_quiet_hours, "07:00-07:00"),
    ("NEWS_REFRESH_QUIET_DAYS", news_quiet_days, ""),
    ("NEWS_REFRESH_QUIET_DAYS", news_quiet_days, "sab,dom"),
    ("NEWS_REFRESH_QUIET_DAYS", news_quiet_days, "mon,tue,wed,thu,fri,sat,sun"),
    ("NEWS_REFRESH_FIRST_DELAY_MINUTES", news_first_delay_seconds, ""),
    ("NEWS_REFRESH_FIRST_DELAY_MINUTES", news_first_delay_seconds, "0"),
    ("NEWS_REFRESH_FIRST_DELAY_MINUTES", news_first_delay_seconds, "abc"),
    ("NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES", news_weekend_interval_seconds, ""),
    ("NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES", news_weekend_interval_seconds, "0"),
    ("NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES", news_weekend_interval_seconds, "due ore"),
])
def test_valori_vuoti_o_invalidi_sono_errori_col_nome(env_pulito, nome, fn, valore):
    with pytest.raises(ValueError, match=nome):
        fn(valore)


def test_config_invalida_spegne_lo_scheduler_e_lo_dichiara(env_pulito, monkeypatch):
    monkeypatch.setenv("NEWS_REFRESH_QUIET_HOURS", "")
    monkeypatch.setenv("NEWS_REFRESH_QUIET_DAYS", "sab")
    manager = NewsRefreshManager(puller=lambda **_: {"saved": 1})   # nessuna eccezione: il backend parte
    try:
        stato = manager.status()["scheduler"]
        assert stato["enabled"] is False
        assert "NEWS_REFRESH_QUIET_HOURS" in stato["config_error"]
        assert "NEWS_REFRESH_QUIET_DAYS" in stato["config_error"]
        manager.start()
        assert manager._scheduler is None
        assert manager.status()["next_run_at"] is None
        assert manager.trigger("manual")["accepted"] is True       # il click resta possibile
    finally:
        manager.stop()


def test_avvio_senza_giro_immediato_primo_giro_dopo_il_ritardo(env_pulito):
    manager = NewsRefreshManager(puller=_mai, interval_seconds=900, auto_enabled=True,
                                 first_delay_seconds=600, now=_Orologio(MER_10))
    manager.start()
    try:
        time.sleep(0.05)
        assert manager.status()["status"] == "idle"
        assert manager.status()["next_run_at"] == _dt(2026, 9, 30, 10, 10).isoformat()
    finally:
        manager.stop()


@pytest.mark.parametrize("adesso,attesa", [(SAB_2330, DOM_07), (MER_2330, GIO_07)])
def test_avvio_in_pausa_rinvia_alla_fine_della_pausa(env_pulito, adesso, attesa):
    manager = NewsRefreshManager(puller=_mai, interval_seconds=900, auto_enabled=True,
                                 first_delay_seconds=600, now=_Orologio(adesso))
    manager.start()
    try:
        assert manager.status()["next_run_at"] == attesa
        assert manager.status()["scheduler"]["paused_now"] is True
    finally:
        manager.stop()


def test_giro_finito_venerdi_sera_riprende_sabato_mattina(env_pulito):
    orologio = _Orologio(VEN_2155)
    manager = NewsRefreshManager(puller=lambda **_: {"saved": 0}, interval_seconds=900,
                                 auto_enabled=True, first_delay_seconds=60, now=orologio)
    manager._started = True
    job = manager.trigger("schedule")["job"]
    _wait_for(lambda: manager.status()["status"] == "success")
    assert manager.job(job["id"])["next_run_at"] == SAB_07     # 22:10 cade in pausa notturna
    manager.stop()


def test_scadenza_raggiunta_in_pausa_non_parte_e_si_rinvia(env_pulito, monkeypatch):
    monkeypatch.setattr(nrm, "WAIT_CAP_SECONDS", 0.02)
    orologio = _Orologio(MER_10)
    manager = NewsRefreshManager(puller=_mai, interval_seconds=900, auto_enabled=True,
                                 first_delay_seconds=600, now=orologio)
    manager.start()
    try:
        orologio.t = SAB_2330        # l'orologio salta oltre la scadenza (PC sospeso fino a sabato notte)
        _wait_for(lambda: manager.status()["next_run_at"] == DOM_07)
        assert manager.status()["status"] == "idle"
    finally:
        manager.stop()


def test_doppio_trigger_restituisce_il_job_in_corso_e_il_get_lo_segue(env_pulito):
    rilascio = threading.Event()
    chiamate = []

    def pull(**_kwargs):
        chiamate.append(1)
        rilascio.wait(2)
        return {"saved": 3, "classification_failed": 1}

    manager = NewsRefreshManager(puller=pull, interval_seconds=900, auto_enabled=False)
    primo = manager.trigger("manual")
    try:
        secondo = manager.trigger("manual")
        assert secondo["accepted"] is False
        assert secondo["reason"] == "already_running"
        assert secondo["job"]["id"] == primo["job"]["id"]
        assert manager.job(primo["job"]["id"])["status"] == "running"
    finally:
        rilascio.set()
    _wait_for(lambda: manager.job(primo["job"]["id"])["status"] == "success")
    finito = manager.job(primo["job"]["id"])
    assert finito["result"] == {"saved": 3, "classification_failed": 1}
    assert isinstance(finito["duration_s"], float) and finito["duration_s"] >= 0
    assert chiamate == [1]
    assert manager.job("job-sconosciuto") is None
    manager.stop()


def test_stop_ha_un_tempo_massimo_e_dichiara_il_giro_interrotto(env_pulito):
    iniziato = threading.Event()
    rilascio = threading.Event()
    visto = {}

    def pull(should_stop=None, **_kwargs):
        visto["should_stop"] = should_stop
        iniziato.set()
        rilascio.wait(5)
        return {"saved": 9}

    manager = NewsRefreshManager(puller=pull, interval_seconds=900, auto_enabled=False,
                                 stop_timeout_seconds=0.1)
    job = manager.trigger("manual")["job"]
    _wait_for(iniziato.is_set)
    worker = manager._worker
    t0 = time.monotonic()
    manager.stop()
    assert time.monotonic() - t0 < 1.0
    assert worker.daemon is True                     # non tiene vivo il processo
    assert visto["should_stop"]() is True            # il giro sa che deve fermarsi
    stato = manager.job(job["id"])
    assert stato["status"] == "interrupted"
    assert stato["error"] and stato["finished_at"]
    rilascio.set()
    worker.join(1)
    assert manager.job(job["id"])["status"] == "interrupted"   # l'esito tardivo non lo riscrive


def test_risultato_interrotto_dal_giro_e_dichiarato(env_pulito):
    manager = NewsRefreshManager(puller=lambda **_: {"saved": 2, "interrupted": True},
                                 interval_seconds=900, auto_enabled=False)
    job = manager.trigger("manual")["job"]
    _wait_for(lambda: manager.job(job["id"])["status"] != "running")
    assert manager.job(job["id"])["status"] == "interrupted"
    manager.stop()


def test_errore_del_giro_senza_messaggio_grezzo(env_pulito):
    chiave = "CHIAVEFINTA1234567890"

    class HTTPError(Exception):
        def __init__(self):
            super().__init__("429 Client Error for url: https://api.example/v2?apiKey=" + chiave)
            self.response = type("R", (), {"status_code": 429})()

    def pull(**_kwargs):
        raise HTTPError()

    manager = NewsRefreshManager(puller=pull, interval_seconds=900, auto_enabled=False)
    job = manager.trigger("manual")["job"]
    _wait_for(lambda: manager.job(job["id"])["status"] == "error")
    errore = manager.job(job["id"])["error"]
    assert chiave not in errore and "apiKey" not in errore
    assert "HTTPError" in errore and "429" in errore
    manager.stop()


@pytest.mark.parametrize("eccezione", [SystemExit(3), KeyboardInterrupt()])
def test_baseexception_nel_giro_non_blocca_il_manager(env_pulito, eccezione):
    """REV_G3 R7: prima lo stato restava `running` per sempre e ogni trigger diceva already_running."""
    def pull(**_kwargs):
        raise eccezione

    manager = NewsRefreshManager(puller=pull, interval_seconds=900, auto_enabled=False)
    job = manager.trigger("manual")["job"]
    _wait_for(lambda: manager.job(job["id"])["status"] != "running")
    stato = manager.job(job["id"])
    assert stato["status"] == "error" and type(eccezione).__name__ in stato["error"]
    manager._puller = lambda **_: {"saved": 0}
    assert manager.trigger("manual")["accepted"] is True
    manager.stop()



@pytest.mark.parametrize("fine,attesa", [
    (SAB_12, _dt(2026, 10, 3, 14, 0).isoformat()),              # sabato: passo weekend (2 h)
    (_dt(2026, 10, 4, 21, 0).timestamp(), LUN_07),              # domenica 21:00 + 2 h = notte
    (MER_10, _dt(2026, 9, 30, 10, 15).isoformat()),             # feriale: passo normale
])
def test_passo_del_weekend(env_pulito, fine, attesa):
    """Decisione PM (04/10 sera): sabato e domenica ogni NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES."""
    manager = NewsRefreshManager(puller=lambda **_: {"saved": 0}, interval_seconds=900,
                                 auto_enabled=True, first_delay_seconds=60, now=_Orologio(fine))
    manager._started = True
    job = manager.trigger("schedule")["job"]
    _wait_for(lambda: manager.status()["status"] == "success")
    assert manager.job(job["id"])["next_run_at"] == attesa
    assert manager.status()["scheduler"]["weekend_interval_seconds"] == 7200
    manager.stop()


def test_passo_weekend_invalido_spegne_lo_scheduler(env_pulito, monkeypatch):
    monkeypatch.setenv("NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES", "")
    manager = NewsRefreshManager(puller=lambda **_: {"saved": 1})
    try:
        stato = manager.status()["scheduler"]
        assert stato["enabled"] is False
        assert "NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES" in stato["config_error"]
    finally:
        manager.stop()
