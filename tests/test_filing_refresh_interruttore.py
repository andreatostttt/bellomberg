"""Fase E: interruttore del controllo giornaliero (FilingRefreshManager.imposta_auto)."""
import threading
import time

import pytest

from bellomberg.market_data.filing_refresh import FilingRefreshManager


class _Svc:
    def __init__(self):
        self.chiamate = []

    def recupera_orfani(self, **kw):
        self.chiamate.append("orfani")
        return []

    def run_due(self, now=None, *, tickers=None, max_workers=1, escludi=None, stop_event=None):
        self.chiamate.append("due")
        return []


def _aspetta(cond, s=3):
    fine = time.time() + s
    while time.time() < fine and not cond():
        time.sleep(0.01)
    return cond()


def _schedulatori():
    return [t for t in threading.enumerate() if t.name == "bellomberg-filing-refresh-scheduler" and t.is_alive()]


def test_spento_nessuna_scadenza_acceso_controlla_subito(monkeypatch):
    monkeypatch.delenv("FILING_AUTO_REFRESH_ENABLED", raising=False)
    svc = _Svc()
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True, esclusi_fn=frozenset)
    prima = len(_schedulatori())
    m.start(immediate=False)
    try:
        assert m.status()["next_run_at"] is not None and m.status()["auto_enabled"] is True
        m.imposta_auto(False)
        st = m.status()
        assert st["auto_enabled"] is False and st["next_run_at"] is None and st["auto_forzato_spento"] is False
        assert m.trigger("schedule")["accepted"] is False
        m.imposta_auto(True)
        # riacceso: controllo immediato dei soli profili dovuti (sorgente "schedule", gratis)
        assert _aspetta(lambda: svc.chiamate.count("due") == 1 and m.status()["status"] == "success")
        assert m.status()["trigger"] == "schedule" and m.status()["next_run_at"] is not None
        m.imposta_auto(False)
        m.imposta_auto(True)
        assert _aspetta(lambda: svc.chiamate.count("due") == 2)
        assert len(_schedulatori()) == prima + 1  # on/off/on: un solo scheduler
    finally:
        m.stop()


def test_acceso_dopo_avvio_spento_crea_un_solo_scheduler(monkeypatch):
    monkeypatch.delenv("FILING_AUTO_REFRESH_ENABLED", raising=False)
    svc = _Svc()
    m = FilingRefreshManager(lambda: svc, interval_seconds=0.2, auto_enabled=False, esclusi_fn=frozenset)
    prima = len(_schedulatori())
    m.start(immediate=True)
    try:
        time.sleep(0.1)
        assert svc.chiamate == [] and len(_schedulatori()) == prima
        m.imposta_auto(True)
        m.imposta_auto(True)
        assert _aspetta(lambda: svc.chiamate.count("due") >= 2)  # subito, poi periodico
        assert len(_schedulatori()) == prima + 1
        m.imposta_auto(False)
        time.sleep(0.05)
        n = svc.chiamate.count("due")
        time.sleep(0.4)
        assert svc.chiamate.count("due") <= n + 1  # al piu' il giro gia' partito
        assert m.status()["next_run_at"] is None
    finally:
        m.stop()


def test_env_falso_vince(monkeypatch):
    monkeypatch.setenv("FILING_AUTO_REFRESH_ENABLED", "false")
    m = FilingRefreshManager(lambda: _Svc(), interval_seconds=3600)
    assert m.auto_enabled is False and m.auto_forzato_spento is True
    assert m.status()["auto_forzato_spento"] is True
    with pytest.raises(ValueError):
        m.imposta_auto(True)
    m.imposta_auto(False)  # spegnere resta lecito
    assert m.auto_enabled is False


def test_imposta_auto_non_avviato_cambia_solo_il_flag(monkeypatch):
    monkeypatch.delenv("FILING_AUTO_REFRESH_ENABLED", raising=False)
    svc = _Svc()
    m = FilingRefreshManager(lambda: svc, interval_seconds=3600, auto_enabled=True)
    m.imposta_auto(False)
    m.imposta_auto(True)
    time.sleep(0.05)
    assert svc.chiamate == [] and m._scheduler is None


# --- preferenza salvata letta all'avvio ---

def test_preferenza_salvata_all_avvio(tmp_path, monkeypatch):
    from bellomberg.storage import filing_preferenze as fpref
    monkeypatch.delenv("FILING_AUTO_REFRESH_ENABLED", raising=False)
    p = tmp_path / "p.json"
    m = FilingRefreshManager(lambda: _Svc(), auto_enabled=True)
    m.applica_preferenza(pref_path=p)  # mai scelto: resta il default
    assert m.auto_enabled is True
    fpref.imposta_controllo_giornaliero(False, path=p)
    m.applica_preferenza(pref_path=p)
    assert m.auto_enabled is False
    fpref.imposta_controllo_giornaliero(True, path=p)
    m.applica_preferenza(pref_path=p)
    assert m.auto_enabled is True


def test_preferenza_accesa_ma_env_spento(tmp_path, monkeypatch):
    from bellomberg.storage import filing_preferenze as fpref
    monkeypatch.setenv("FILING_AUTO_REFRESH_ENABLED", "0")
    p = tmp_path / "p.json"
    fpref.imposta_controllo_giornaliero(True, path=p)
    m = FilingRefreshManager(lambda: _Svc())
    m.applica_preferenza(pref_path=p)
    assert m.auto_enabled is False


def test_preferenze_illeggibili_default_env(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("FILING_AUTO_REFRESH_ENABLED", raising=False)
    p = tmp_path / "p.json"
    p.write_text("{rotto")
    m = FilingRefreshManager(lambda: _Svc())
    with caplog.at_level("WARNING"):
        m.applica_preferenza(pref_path=p)
    assert m.auto_enabled is True and "illeggibili" in caplog.text
    assert p.read_text() == "{rotto"


def test_lifespan_applica_la_preferenza_prima_dello_start():
    import inspect
    from bellomberg.api import bellomberg_api
    sorgente = inspect.getsource(bellomberg_api.lifespan)
    assert "filing_refresh_manager.applica_preferenza()" in sorgente
    assert sorgente.index("filing_refresh_manager.applica_preferenza()") < sorgente.index(
        "filing_refresh_manager.start(")
