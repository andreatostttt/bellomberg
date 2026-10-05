from bellomberg.cli import price_updater as pu


def _endpoint(api, path, method):
    return next(
        route.endpoint
        for route in api.app.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    )


def test_api_automatic_price_cycle_uses_explicit_non_ibkr_policy(monkeypatch):
    from bellomberg.api import bellomberg_api as api

    observed = []

    def fake_update(db, source_order, verbose):
        observed.append(tuple(source_order))
        return {"updated": 0}

    monkeypatch.setattr(api, "get_db", lambda: object())
    monkeypatch.setattr(pu, "update_all_prices", fake_update)
    endpoint = _endpoint(api, "/prices/update", "POST")
    assert endpoint() == {"updated": 0}
    assert observed == [pu.AUTOMATIC_PRICE_SOURCES]
    assert "ibkr" not in pu.AUTOMATIC_PRICE_SOURCES
    assert pu.FONTI_PREZZI[0] == "ibkr"
