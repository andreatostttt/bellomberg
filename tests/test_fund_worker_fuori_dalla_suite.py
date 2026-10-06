"""V0-RETE (06/10/2026, Opus 5.5): il worker Fund del lifespan non fa rete nella suite.

Misurato: ogni `with TestClient(api.app)` avvia FundMarketWorker (bellomberg_api, lifespan)
su SQLITE_PATH/DB_DIR di PRODUZIONE; `_universe` legge il book vero (sqlite3 diretto, fuori
dal tripwire (b)) e ogni ticker con quotazione in cache scaduta chiama yf.Ticker in un thread.
Verde o rosso secondo l'eta' della cache vera: i test delle notizie cadevano in teardown con
['yfinance.Ticker'], gli altri facevano rete vera senza saperlo. La sandbox in conftest
(`acquisizione_fund_fuori_dalla_suite`) restituisce uno stato DICHIARATO, mai un «completato»."""
import sqlite3


def _spie(monkeypatch):
    from bellomberg.market_data import fund_market_refresh as refresh
    toccati = []

    def universo(db_path):
        toccati.append(("universe", str(db_path)))
        return {"ZZTEST"}, []

    def fabbrica(simbolo):
        toccati.append(("yfinance", simbolo))
        raise ConnectionError("rete in un test (spia V0-RETE)")

    monkeypatch.setattr(refresh, "_universe", universo)
    monkeypatch.setattr(refresh, "_ticker_factory", fabbrica)
    return toccati


def test_la_suite_non_acquisisce_dati_fund(tmp_path, monkeypatch):
    from bellomberg.market_data import fund_market_worker as worker
    toccati = _spie(monkeypatch)
    db = tmp_path / "book_finto.sqlite"
    sqlite3.connect(db).close()
    esito = worker.refresh_followed_market_data(db, cache_dir=tmp_path / "cache")
    assert toccati == [], "il worker Fund ha letto il book o chiamato Yahoo nella suite: %r" % toccati
    # dichiarato, non un «completato» zitto: chi legge lo stato del worker vede il perche'
    assert esito["status"] == "unavailable"
    assert esito["error"] == "fund_market_refresh_disabled_in_test_suite"


def test_il_lifespan_non_porta_il_worker_sul_book_vero(monkeypatch):
    from fastapi.testclient import TestClient
    from types import SimpleNamespace as NS
    from bellomberg.api import bellomberg_api as api
    toccati = _spie(monkeypatch)
    monkeypatch.setattr(api, "news_refresh_manager", NS(start=lambda immediate=True: None, stop=lambda: None))
    with TestClient(api.app, base_url="http://127.0.0.1:8765"):
        stato = api.app.state.fund_market_worker
        for _ in range(200):          # il primo giro parte subito: aspetta che sia concluso
            if stato.status().get("last_completed_at") or stato.status().get("error"):
                break
            import time
            time.sleep(0.01)
        assert stato.status()["error"] == "fund_market_refresh_disabled_in_test_suite"
    assert toccati == [], "il lifespan ha portato il worker Fund sul book/Yahoo: %r" % toccati
