"""/market/movers e /market/overview: le azioni per paese arrivano a runtime dallo
screener del provider (mai un elenco nel codice); un paese che non risponde e'
dichiarato, mai sostituito; cache solo se non vuota. Ticker e numeri inventati."""
import sys
from types import SimpleNamespace as NS

import pandas as pd
import pytest


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api
    monkeypatch.setitem(api._SESSIONS, "synthetic-token", 9_999_999_999)
    monkeypatch.setattr(api, "news_refresh_manager", NS(start=lambda immediate=True: None, stop=lambda: None))
    api._MKT_CACHE.pop("movers", None)
    api._LAST_CALL.clear()
    with TestClient(api.app, base_url="http://127.0.0.1:8765") as c:
        yield c, api
    api._MKT_CACHE.pop("movers", None)
    api._LAST_CALL.clear()


HEADERS = {"X-BB-Token": "synthetic-token"}


def _screener_fake(calls, per_regione, guasti=()):
    """Screener Yahoo finto: per regione, quote inventate (nessuna rete)."""
    def screen(regione, quanti):
        calls.append(regione)
        if regione in guasti:
            raise ConnectionError("screener giu'")
        return {"quotes": per_regione.get(regione, [])[:quanti]}
    return screen


def _quota(simbolo, nome, prezzo, variazione, tipo="EQUITY"):
    return {"symbol": simbolo, "shortName": nome, "quoteType": tipo,
            "regularMarketPrice": prezzo, "regularMarketChangePercent": variazione}


def test_no_hard_coded_stock_universe_in_the_api():
    from bellomberg.api import bellomberg_api as api
    assert not hasattr(api, "OVERVIEW_COUNTRIES")
    sorgente = open(api.__file__, encoding="utf-8").read()
    assert "OVERVIEW_COUNTRIES" not in sorgente


def test_movers_universe_comes_from_the_provider_screener(client, monkeypatch):
    c, api = client
    from bellomberg.market_data import universo_mercati
    calls = []
    fake = _screener_fake(calls, {
        "us": [_quota("ZZTEST", "Zztest Corp", 110.0, 10.0), _quota("ZZFUND", "Zz Fund", 5.0, 1.0, tipo="ETF")],
        "it": [_quota("ACME.MI", "Acme SpA", 19.0, -5.0), _quota("ZZNOQ.MI", "Zz Senza Quote", None, None)],
    }, guasti={"de"})
    monkeypatch.setattr(universo_mercati, "_screen_yahoo", fake)

    body = c.get("/market/movers", headers=HEADERS).json()

    assert sorted(calls) == sorted(universo_mercati.REGIONI.values())
    rows = {r["ticker"]: r for r in body["azioni"]}
    assert rows["ZZTEST"] == {"ticker": "ZZTEST", "name": "Zztest Corp", "country": "US",
                              "price": 110.0, "change_pct": 10.0}
    assert rows["ACME.MI"]["country"] == "IT" and rows["ACME.MI"]["change_pct"] == -5.0
    assert "ZZFUND" not in rows, "solo azioni"
    # simbolo senza dati: presente ma dichiarato vuoto, mai uno zero inventato
    assert rows["ZZNOQ.MI"]["price"] is None and rows["ZZNOQ.MI"]["change_pct"] is None
    # paese che non risponde: dichiarato, nessuna lista sostitutiva
    assert body["paesi"]["DE"]["stato"] == "non_disponibile" and "ConnectionError" in body["paesi"]["DE"]["motivo"]
    assert body["paesi"]["FR"]["stato"] == "non_disponibile" and body["paesi"]["US"]["stato"] == "ok"
    assert set(body["paesi"]) == {"US", "IT", "DE", "FR", "UK", "JP", "CN", "IN", "BR"}
    assert body["motivo"] is None and body["fonte"] == universo_mercati.FONTE
    # seconda lettura dalla cache
    c.get("/market/movers", headers=HEADERS)
    assert len(calls) == len(universo_mercati.REGIONI)


def test_movers_declares_an_unavailable_source_and_never_caches_it(client, monkeypatch):
    c, api = client
    from bellomberg.market_data import universo_mercati
    calls = []
    monkeypatch.setattr(universo_mercati, "_screen_yahoo",
                        _screener_fake(calls, {}, guasti=set(universo_mercati.REGIONI.values())))
    first = c.get("/market/movers", headers=HEADERS).json()
    assert first["azioni"] == []
    assert first["motivo"] and "screener" in first["motivo"]
    assert all(p["stato"] == "non_disponibile" for p in first["paesi"].values())
    c.get("/market/movers", headers=HEADERS)
    assert len(calls) == 2 * len(universo_mercati.REGIONI)


def test_overview_stocks_come_from_the_screener_and_failure_is_declared(client, monkeypatch):
    c, api = client
    from bellomberg.market_data import universo_mercati
    calls = []
    monkeypatch.setitem(sys.modules, "yfinance", NS(download=lambda syms, **k: pd.DataFrame()))
    monkeypatch.setattr(universo_mercati, "_screen_yahoo",
                        _screener_fake(calls, {"it": [_quota("ACME.MI", "Acme SpA", 19.0, -5.0)]}, guasti={"jp"}))
    for key in ("ovw|IT", "ovw|JP"):
        api._MKT_CACHE.pop(key, None)

    it = c.get("/market/overview?country=IT", headers=HEADERS).json()
    assert it["azioni"] == [{"ticker": "ACME.MI", "name": "Acme SpA", "price": 19.0, "change_pct": -5.0}]
    assert it["azioni_fonte"]["stato"] == "ok"
    assert it["countries"] == sorted(universo_mercati.REGIONI)

    jp = c.get("/market/overview?country=JP", headers=HEADERS).json()
    assert jp["azioni"] == [] and jp["azioni_fonte"]["stato"] == "non_disponibile"
    assert "ConnectionError" in jp["azioni_fonte"]["motivo"]
    for key in ("ovw|IT", "ovw|JP"):
        api._MKT_CACHE.pop(key, None)


def test_news_falls_back_to_yahoo_search_and_never_caches_empty(client, monkeypatch):
    c, api = client
    calls = []

    class Ticker:
        def __init__(self, sym):
            self.news = []

    def search(sym, **kwargs):
        calls.append(sym)
        return NS(news=[{"title": "Synthetic headline", "publisher": "Synthetic Wire",
                         "link": "https://example.com/n/1", "providerPublishTime": 1_790_000_000}])

    monkeypatch.setitem(sys.modules, "yfinance", NS(Ticker=Ticker, Search=search))
    api._MKT_CACHE.pop("n|SYNTH", None)
    body = c.get("/market/news?ticker=SYNTH", headers=HEADERS).json()
    assert body["items"] == [{"title": "Synthetic headline", "link": "https://example.com/n/1",
                              "publisher": "Synthetic Wire", "published": 1_790_000_000,
                              "source": "yahoo_search", "match": "simbolo"}]
    assert len(calls) == 1
    api._MKT_CACHE.pop("n|SYNTH", None)

    monkeypatch.setitem(sys.modules, "yfinance", NS(Ticker=Ticker, Search=lambda sym, **kw: NS(news=[])))
    assert c.get("/market/news?ticker=SYNTH", headers=HEADERS).json()["items"] == []
    assert "n|SYNTH" not in api._MKT_CACHE


def test_news_outside_the_us_searches_by_company_name(client, monkeypatch):
    c, api = client
    asked = []

    class Ticker:
        def __init__(self, sym):
            self.news = []

    def search(q, **kwargs):
        asked.append(q)
        if q == "SYN.MI":
            return NS(news=[], quotes=[{"symbol": "SYN.MI", "longname": "Synthetic S.p.A."}])
        return NS(news=[{"title": "Synthetic S.p.A. headline", "publisher": "Wire", "link": "https://example.com/n/2",
                         "providerPublishTime": 1}], quotes=[])

    monkeypatch.setitem(sys.modules, "yfinance", NS(Ticker=Ticker, Search=search))
    api._MKT_CACHE.pop("n|SYN.MI", None)
    body = c.get("/market/news?ticker=SYN.MI", headers=HEADERS).json()
    assert asked == ["SYN.MI", "Synthetic S.p.A."]
    assert [i["title"] for i in body["items"]] == ["Synthetic S.p.A. headline"]
    api._MKT_CACHE.pop("n|SYN.MI", None)


# ---------------------------------------------- G8 (04/10/2026): fonte dichiarata per item
def _yf_news(monkeypatch, ticker_news=None, ticker_err=None, search=None):
    class Ticker:
        def __init__(self, sym):
            if ticker_err:
                raise ticker_err
            self.news = ticker_news or []
    monkeypatch.setitem(sys.modules, "yfinance", NS(Ticker=Ticker, Search=search or (lambda q, **k: NS(news=[], quotes=[]))))


def test_news_items_declare_source_and_symbol_match(client, monkeypatch):
    c, api = client
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "data_ticker", lambda t: t)  # alias leggibile, nessuna voce
    _yf_news(monkeypatch, ticker_news=[{"content": {"title": "Zztest direct", "provider": {"displayName": "Wire"},
                                                    "pubDate": "2026-10-01T10:00:00Z",
                                                    "canonicalUrl": {"url": "https://example.com/d"}}}])
    api._MKT_CACHE.pop("n|ZZTEST", None)
    body = c.get("/market/news?ticker=ZZTEST", headers=HEADERS).json()
    assert body["items"][0]["source"] == "yahoo_ticker_news" and body["items"][0]["match"] == "simbolo"
    assert body["errori"] is None and body["symbol"] == "ZZTEST"
    api._MKT_CACHE.pop("n|ZZTEST", None)


def test_news_found_by_company_name_are_marked_as_such(client, monkeypatch):
    c, api = client

    def search(q, **kwargs):
        if q == "SYN.MI":
            return NS(news=[], quotes=[{"symbol": "SYN.MI", "longname": "Synthetic S.p.A."}])
        return NS(news=[{"title": "Synthetic S.p.A. headline", "link": "https://example.com/n/2"}], quotes=[])
    _yf_news(monkeypatch, search=search)
    api._MKT_CACHE.pop("n|SYN.MI", None)
    body = c.get("/market/news?ticker=SYN.MI", headers=HEADERS).json()
    item = body["items"][0]
    assert item["source"] == "yahoo_search" and item["match"] == "nome"
    assert item["match_query"] == "Synthetic S.p.A."
    api._MKT_CACHE.pop("n|SYN.MI", None)


def test_news_search_error_is_declared_not_an_empty_list(client, monkeypatch):
    c, api = client

    def search(q, **kwargs):
        raise ConnectionError("ricerca giu'")
    _yf_news(monkeypatch, ticker_err=TimeoutError("ticker giu'"), search=search)
    api._MKT_CACHE.pop("n|ZZERR", None)
    body = c.get("/market/news?ticker=ZZERR", headers=HEADERS).json()
    assert body["items"] == []
    testo = " | ".join(body["errori"] or [])
    assert "ConnectionError" in testo and "TimeoutError" in testo
    assert "n|ZZERR" not in api._MKT_CACHE


def test_news_unreadable_alias_is_declared(client, monkeypatch):
    c, api = client
    from bellomberg.cli import price_updater

    def rotto(t):
        raise price_updater.AliasFontiError("alias_fonti.json illeggibile")
    monkeypatch.setattr(price_updater, "data_ticker", rotto)
    _yf_news(monkeypatch, ticker_news=[{"title": "Zz raw", "link": "https://example.com/r"}])
    api._MKT_CACHE.pop("n|ZZRAW.FRA", None)
    body = c.get("/market/news?ticker=ZZRAW.FRA", headers=HEADERS).json()
    assert body["symbol"] == "ZZRAW.FRA"
    assert any("alias" in e and "ZZRAW.FRA" in e for e in (body["errori"] or []))
    api._MKT_CACHE.pop("n|ZZRAW.FRA", None)


# ---------------------------------------------- G8: una sola chiusura = variazione n.d.
def test_single_close_gives_no_change_not_zero(monkeypatch):
    from bellomberg.api import bellomberg_api as api
    colonne = pd.MultiIndex.from_tuples([("ZZA", "Close"), ("ZZB", "Close")])
    raw = pd.DataFrame([[100.0, float("nan")], [110.0, 50.0]], columns=colonne)
    monkeypatch.setitem(sys.modules, "yfinance", NS(download=lambda syms, **k: raw))
    out = api._ultime_chiusure(["ZZA", "ZZB"])
    assert out["ZZA"]["change_pct"] == 10.0
    assert out["ZZB"]["price"] == 50.0 and out["ZZB"]["change_pct"] is None
    assert out["ZZB"]["motivo"]


def test_news_errors_never_carry_the_url_querystring(client, monkeypatch):
    c, api = client
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "data_ticker", lambda t: t)
    errore = ConnectionError("Max retries exceeded with url: /v1/finance/search?q=ZZQ&crumb=SEGRETOFINTO")

    def search(q, **kwargs):
        raise errore
    _yf_news(monkeypatch, ticker_err=errore, search=search)
    api._MKT_CACHE.pop("n|ZZQ", None)
    body = c.get("/market/news?ticker=ZZQ", headers=HEADERS).json()
    testo = " | ".join(body["errori"] or [])
    assert "ConnectionError" in testo and "SEGRETOFINTO" not in testo and "?" not in testo


def test_screener_error_never_carries_the_url_querystring():
    from bellomberg.market_data import universo_mercati

    def screen(regione, quanti):
        raise ConnectionError("Max retries exceeded with url: /v1/finance/screener?crumb=SEGRETOFINTO")
    out = universo_mercati.azioni_paese("US", screen=screen)
    assert "ConnectionError" in out["motivo"] and "SEGRETOFINTO" not in out["motivo"]


def test_news_with_a_failed_network_route_is_not_cached(client, monkeypatch):
    """G8 seguito (rilievo 4): notizie del simbolo cadute + ricerca con item = risposta
    dichiarata ma NON in cache: la lettura dopo rifà la chiamata."""
    c, api = client
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "data_ticker", lambda t: t)
    giri = []

    def search(q, **kwargs):
        giri.append(q)
        return NS(news=[{"title": "Zz headline %d" % len(giri), "link": "https://example.com/z"}], quotes=[])
    _yf_news(monkeypatch, ticker_err=TimeoutError("ticker giu'"), search=search)
    api._MKT_CACHE.pop("n|ZZNC", None)
    primo = c.get("/market/news?ticker=ZZNC", headers=HEADERS).json()
    secondo = c.get("/market/news?ticker=ZZNC", headers=HEADERS).json()
    assert primo["errori"] and primo["items"][0]["title"] == "Zz headline 1"
    assert len(giri) == 2 and secondo["items"][0]["title"] == "Zz headline 2"
    api._MKT_CACHE.pop("n|ZZNC", None)
