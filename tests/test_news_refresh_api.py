from types import SimpleNamespace


def _endpoint(api, path, method):
    return next(
        route.endpoint
        for route in api.app.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    )


def test_manual_news_refresh_delegates_to_the_shared_manager(monkeypatch):
    from bellomberg.api import bellomberg_api as api

    calls = []

    class FakeManager:
        def trigger(self, source):
            calls.append(source)
            return {"accepted": True, "job": {"id": "job-1", "status": "running", "trigger": source}}

    monkeypatch.setattr(api, "news_refresh_manager", FakeManager())
    endpoint = _endpoint(api, "/news/feed/refresh", "POST")
    assert endpoint(days=1, classify=True) == {
        "accepted": True,
        "job": {"id": "job-1", "status": "running", "trigger": "manual"},
    }
    assert calls == ["manual"]


def test_news_provider_status_exposes_refresh_job(monkeypatch):
    from bellomberg.api import bellomberg_api as api
    import bellomberg.market_data.news_aggregator as aggregator

    job = {"id": "job-1", "status": "running", "trigger": "startup"}
    monkeypatch.setattr(api, "news_refresh_manager", SimpleNamespace(status=lambda: job))
    monkeypatch.setattr(aggregator, "providers_blocked", lambda: {})
    monkeypatch.setattr(aggregator, "NEWS_PROVIDER_LIMITS", {})
    monkeypatch.setattr(aggregator, "stato_ultimo_giro", lambda: {"stato": "n.d."})
    endpoint = _endpoint(api, "/news/providers", "GET")
    result = endpoint()
    assert result["refresh_job"] == job


def test_api_lifespan_starts_and_stops_the_shared_manager(monkeypatch):
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api

    calls = []

    class FakeManager:
        def start(self, immediate=False):
            calls.append(("start", immediate))

        def stop(self):
            calls.append(("stop",))

    monkeypatch.setattr(api, "news_refresh_manager", FakeManager())
    with TestClient(api.app, base_url="http://127.0.0.1:8765"):
        assert calls == [("start", False)]          # G3: nessun giro immediato all'avvio
    assert calls == [("start", False), ("stop",)]


def test_get_refresh_segue_il_job_fino_al_risultato(monkeypatch):
    """G3: il POST risponde subito {accepted, job}; il GET legge lo stesso job per id."""
    import pytest
    from fastapi import HTTPException
    from bellomberg.api import bellomberg_api as api
    from bellomberg.api.news_refresh_manager import NewsRefreshManager

    manager = NewsRefreshManager(puller=lambda **_: {"saved": 4, "classification_failed": 2},
                                 interval_seconds=900, auto_enabled=False)
    monkeypatch.setattr(api, "news_refresh_manager", manager)
    post = _endpoint(api, "/news/feed/refresh", "POST")
    get = _endpoint(api, "/news/feed/refresh", "GET")
    risposta = post(days=1, classify=True)
    assert set(risposta) == {"accepted", "job"} and risposta["accepted"] is True
    job_id = risposta["job"]["id"]
    import time
    for _ in range(200):
        letto = get(job_id=job_id)
        if letto["job"]["status"] != "running":
            break
        time.sleep(0.01)
    assert letto["job"]["id"] == job_id and letto["job"]["status"] == "success"
    assert letto["job"]["result"] == {"saved": 4, "classification_failed": 2}
    assert letto["job"]["duration_s"] is not None and letto["job"]["error"] is None
    assert letto["scheduler"]["enabled"] is False
    assert get()["job"]["id"] == job_id                      # senza id: l'ultimo
    with pytest.raises(HTTPException) as exc:
        get(job_id="job-sconosciuto")
    assert exc.value.status_code == 404
    manager.stop()


def test_post_durante_un_giro_non_ne_avvia_un_secondo(monkeypatch):
    import threading
    from bellomberg.api import bellomberg_api as api
    from bellomberg.api.news_refresh_manager import NewsRefreshManager

    rilascio, chiamate = threading.Event(), []

    def pull(**_kwargs):
        chiamate.append(1)
        rilascio.wait(2)
        return {"saved": 0}

    manager = NewsRefreshManager(puller=pull, interval_seconds=900, auto_enabled=False)
    monkeypatch.setattr(api, "news_refresh_manager", manager)
    post = _endpoint(api, "/news/feed/refresh", "POST")
    try:
        primo = post(days=1, classify=True)
        secondo = post(days=1, classify=True)
        assert secondo["accepted"] is False and secondo["reason"] == "already_running"
        assert secondo["job"]["id"] == primo["job"]["id"]
    finally:
        rilascio.set()
        manager.stop()
    assert chiamate == [1]
