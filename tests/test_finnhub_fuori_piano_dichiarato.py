"""Finnhub, seconda meta' (27/08, ponte (78) → C1 «chiave dedicata», decisione PM 25/08).

Il fatto (misurato il 22/08 e il 27/08 sull'API vera, piano gratuito):
`/press-releases`, `/news-sentiment` e `/calendar/economic` rispondono 403
(«You don't have access to this resource»). Prima di oggi: `fetch_finnhub_intel`
sprecava due HTTP per ticker e rendeva `press_releases_30d: []` /
`sentiment: {}` muti; `GET /news/economic-calendar` sprecava una HTTP e
rendeva il calendario Finnhub vuoto come se fosse quiete.

Cura: un REGISTRO `ENDPOINT_FUORI_PIANO` (path → perche') consultato da
`_api_get` — niente HTTP, motivo esplicito — e la chiave dedicata `fonti_mute`
(dict fonte → motivo) + `avviso` sui due payload, nella stessa forma di
`/news/macro` che NewsPage consuma gia' (`takeFonti`). Il registro e' una
MISURA datata (`ULTIMA_MISURA_FUORI_PIANO`), non una legge:
`prova_finnhub_piano.py --vero` lo rimisura.

Zero rete: `requests.get` finto per path; un tocco a un endpoint fuori piano
fa cadere il test (la misura della cura, non l'assenza del sintomo).
"""
import datetime as _dt
import re

import pytest

from bellomberg.market_data import finnhub_news


@pytest.fixture(autouse=True)
def _negozio_sintetico(monkeypatch):
    from bellomberg.storage import classificazione
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {
        "origine": "fixture/veicoli.json", "motivo": None,
        "veicoli": {"ALFA": {"tipo": "operating"}},
    })


class _R:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text or ("" if status == 200 else "HTTP %d" % status)

    def json(self):
        return self._payload


def _trasporto(monkeypatch, risposte):
    """`risposte`: {path: _R | callable(params) -> _R}. Un path non previsto
    (compresi quelli fuori piano) fa cadere il test. Ritorna i path toccati."""
    toccati = []

    def _get(url, params=None, timeout=None, **k):
        path = url.replace(finnhub_news.BASE_URL, "")
        toccati.append(path)
        if path not in risposte:
            pytest.fail("HTTP verso un path non previsto (fuori piano o non stubbato): %s" % path)
        r = risposte[path]
        return r(params) if callable(r) else r
    monkeypatch.setattr(finnhub_news, "REQ_OK", True)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: "chiave-finta")
    monkeypatch.setattr(finnhub_news.requests, "get", _get)
    return toccati


class _DB:
    def get_portfolio_summary(self):
        return {"positions": [{"ticker": "ALFA"}]}


# ---------------------------------------------------------- il registro

def test_il_registro_elenca_i_tre_endpoint_con_403_e_data_di_misura():
    assert set(finnhub_news.ENDPOINT_FUORI_PIANO) == {"/press-releases", "/news-sentiment",
                                                      "/calendar/economic"}
    assert isinstance(finnhub_news.ULTIMA_MISURA_FUORI_PIANO, _dt.date)
    giorno = finnhub_news.ULTIMA_MISURA_FUORI_PIANO.strftime("%d/%m")
    for perche in finnhub_news.ENDPOINT_FUORI_PIANO.values():
        assert "403" in perche, perche
        assert re.search(r"\d\d/\d\d", perche), perche
        # la data dell'ultima misura sta ANCHE nel testo che arriva al chiamante
        assert giorno in perche, (giorno, perche)


def test_api_get_su_un_path_fuori_piano_non_fa_http_e_dice_perche(monkeypatch):
    toccati = _trasporto(monkeypatch, {})
    motivo = []

    esito = finnhub_news._api_get("/press-releases", {"symbol": "ALFA"}, motivo=motivo)

    assert esito is None
    assert toccati == []
    assert len(motivo) == 1 and "fuori piano" in motivo[0] and "/press-releases" in motivo[0], motivo
    assert "403" in motivo[0], motivo


def test_api_get_su_un_path_in_piano_fa_http_come_prima(monkeypatch):
    toccati = _trasporto(monkeypatch, {"/company-news": _R(200, [{"headline": "x"}])})

    esito = finnhub_news._api_get("/company-news", {"symbol": "ALFA"})

    assert esito == [{"headline": "x"}]
    assert toccati == ["/company-news"]


def test_svuotando_il_registro_l_http_riparte(monkeypatch):
    """E' il registro a decidere, non una rimozione cablata: se un giorno la
    sonda misura 200, si toglie la riga e la chiamata torna da sola."""
    monkeypatch.setattr(finnhub_news, "ENDPOINT_FUORI_PIANO", {})
    toccati = _trasporto(monkeypatch, {"/news-sentiment": _R(200, {"buzz": {"articlesInLastWeek": 3}})})

    out = finnhub_news.fetch_news_sentiment("ALFA")

    assert toccati == ["/news-sentiment"]
    assert out["buzz"] == {"articlesInLastWeek": 3}


# ---------------------------------------------------- fetch_finnhub_intel

def _risposte_sane():
    return {
        "/company-news": _R(200, [{"headline": "h", "datetime": 1700000000, "source": "s", "url": "u"}]),
        "/calendar/earnings": _R(200, {"earningsCalendar": []}),
        "/stock/insider-transactions": _R(200, {"data": []}),
    }


def test_intel_dichiara_le_due_fonti_fuori_piano_senza_toccarle(monkeypatch):
    toccati = _trasporto(monkeypatch, _risposte_sane())

    out = finnhub_news.fetch_finnhub_intel("ALFA")

    assert "/press-releases" not in toccati and "/news-sentiment" not in toccati
    assert isinstance(out["fonti_mute"], dict)
    assert set(out["fonti_mute"]) == {"/press-releases", "/news-sentiment"}, out["fonti_mute"]
    for m in out["fonti_mute"].values():
        assert "fuori piano" in m and "403" in m, m
    assert out["avviso"] and "PARZIALE" in out["avviso"] and "2 fonti" in out["avviso"], out["avviso"]
    # i campi vecchi restano (contratto): vuoti, ma ora NON muti
    assert out["press_releases_30d"] == [] and out["sentiment"] == {}
    assert out["news_sample"][0]["title"] == "h"  # fetch_company_news normalizza headline -> title


def test_intel_dichiara_anche_la_fonte_in_piano_che_cade(monkeypatch):
    r = _risposte_sane()
    r["/company-news"] = _R(429, text="rate limited")
    _trasporto(monkeypatch, r)

    out = finnhub_news.fetch_finnhub_intel("ALFA")

    assert "/company-news" in out["fonti_mute"], out["fonti_mute"]
    assert "429" in out["fonti_mute"]["/company-news"], out["fonti_mute"]
    assert out["news_sample"] == []
    assert "3 fonti" in out["avviso"], out["avviso"]


def test_intel_dichiara_insider_ed_earnings_che_cadono(monkeypatch):
    r = _risposte_sane()
    r["/stock/insider-transactions"] = _R(503, text="giu'")
    r["/calendar/earnings"] = _R(401, text="unauthorized")
    _trasporto(monkeypatch, r)

    out = finnhub_news.fetch_finnhub_intel("ALFA")

    assert "503" in out["fonti_mute"].get("/stock/insider-transactions", ""), out["fonti_mute"]
    assert "401" in out["fonti_mute"].get("/calendar/earnings", ""), out["fonti_mute"]


def test_earnings_senza_ticker_dichiara_il_non_interrogato():
    """Review 27/08: un book senza ticker US → `[]` senza toccare `motivo`, e
    il payload avrebbe detto «nessuna fonte ha taciuto». Ora lo dice."""
    motivo = []

    assert finnhub_news.fetch_earnings_for_portfolio([], motivo=motivo) == []
    assert motivo and "nessun ticker" in motivo[0], motivo


def test_selettore_earnings_include_xetra_con_alias_finnhub_verificato(tmp_path, monkeypatch):
    """Rottura catturata: il filtro sul punto elimina anche un alias Xetra -> USA dichiarato."""
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import negozi_privati

    alias = tmp_path / "alias_fonti.json"
    alias.write_text('{"finnhub": {"ACME.DE": "ACM"}, "sec": {}, '
                     '"yfinance": {}, "correlazione": {}}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(alias))
    negozio = {
        "origine": "fixture/veicoli.json", "motivo": None,
        "veicoli": {"ACME.DE": {"tipo": "operating"}},
    }

    tickers, nota = bellomberg_api._tickers_earnings_da_negozio(
        [{"ticker": "ACME.DE"}], negozio)

    assert tickers == ["ACME.DE"]
    assert nota is None


def test_earnings_xetra_restituisce_ticker_portafoglio_e_conserva_simbolo_fonte(
        tmp_path, monkeypatch):
    """Il simbolo Finnhub serve al trasporto, non deve sostituire l'identita' Xetra."""
    from bellomberg.storage import negozi_privati

    alias = tmp_path / "alias_fonti.json"
    alias.write_text('{"finnhub": {"ACME.DE": "ACM"}, "sec": {}, '
                     '"yfinance": {}, "correlazione": {}}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(alias))
    _trasporto(monkeypatch, {"/calendar/earnings": _R(200, {"earningsCalendar": [{
        "symbol": "ACM", "date": "2026-09-20", "hour": "amc",
        "epsEstimate": 1.2, "year": 2026, "quarter": 3,
    }]})})

    out = finnhub_news.fetch_earnings_for_portfolio(["ACME.DE"], days_ahead=30)

    assert out[0]["symbol"] == "ACME.DE"
    assert out[0]["source_symbol"] == "ACM"


def test_earnings_estero_senza_alias_non_deduce_il_ticker_e_non_fa_http(
        tmp_path, monkeypatch):
    from bellomberg.storage import negozi_privati

    alias = tmp_path / "alias_fonti.json"
    alias.write_text('{"finnhub": {}, "sec": {}, "yfinance": {}, '
                     '"correlazione": {}}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(alias))
    toccati = _trasporto(monkeypatch, {})
    motivo = []

    out = finnhub_news.fetch_earnings_for_portfolio(
        ["ACME.DE"], days_ahead=30, motivo=motivo)

    assert out == [] and toccati == []
    assert motivo and "alias Finnhub mancante" in motivo[0] and "ACME.DE" in motivo[0]


def test_earnings_deduplica_la_stessa_identita_di_portafoglio(tmp_path, monkeypatch):
    from bellomberg.storage import negozi_privati

    alias = tmp_path / "alias_fonti.json"
    alias.write_text('{"finnhub": {"ACME.DE": "ACM"}, "sec": {}, '
                     '"yfinance": {}, "correlazione": {}}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(alias))
    _trasporto(monkeypatch, {"/calendar/earnings": _R(200, {"earningsCalendar": [{
        "symbol": "ACM", "date": "2026-09-20", "hour": "amc",
    }]})})

    out = finnhub_news.fetch_earnings_for_portfolio(
        ["ACME.DE", "ACME.DE"], days_ahead=30)

    assert len(out) == 1 and out[0]["symbol"] == "ACME.DE"


def test_selettore_earnings_dichiara_alias_estero_mancante(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import negozi_privati

    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS",
                        str(tmp_path / "alias_fonti_assente.json"))
    negozio = {
        "origine": "fixture/veicoli.json", "motivo": None,
        "veicoli": {"ACME.DE": {"tipo": "operating"}},
    }

    tickers, nota = bellomberg_api._tickers_earnings_da_negozio(
        [{"ticker": "ACME.DE"}], negozio)

    assert tickers == []
    assert "alias_fonti assente" in nota and "ACME.DE" in nota


def test_endpoint_earnings_mostra_xetra_non_il_simbolo_finnhub(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import classificazione, memory_db, negozi_privati

    alias = tmp_path / "alias_fonti.json"
    alias.write_text('{"finnhub": {"ACME.DE": "ACM"}, "sec": {}, '
                     '"yfinance": {}, "correlazione": {}}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(alias))
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"positions": [{"ticker": "ACME.DE"}]},
    })())
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {
        "origine": "fixture/veicoli.json", "motivo": None,
        "veicoli": {"ACME.DE": {"tipo": "operating"}},
    })
    _trasporto(monkeypatch, {"/calendar/earnings": _R(200, {"earningsCalendar": [{
        "symbol": "ACM", "date": "2026-09-20", "hour": "amc",
        "epsEstimate": 1.2, "year": 2026, "quarter": 3,
    }]})})

    out = bellomberg_api.get_economic_calendar(days_ahead=30)
    evento = next(e for e in out["items"] if e.get("type") == "Earnings")

    assert evento["title"].startswith("ACME.DE Earnings")
    # G8 (04/10/2026): paese dal suffisso di borsa (.DE -> DE), non piu' "EU" fisso
    assert evento["country"] == "DE"


# ------------------------------------------------- calendario economico

def test_fetch_economic_calendar_non_fa_http_e_dice_perche(monkeypatch):
    toccati = _trasporto(monkeypatch, {})
    motivo = []

    out = finnhub_news.fetch_economic_calendar(days_back=1, days_ahead=7, motivo=motivo)

    assert out == [] and toccati == []
    assert motivo and "/calendar/economic" in motivo[0] and "fuori piano" in motivo[0], motivo


def test_endpoint_calendario_porta_fonti_mute_e_avviso(monkeypatch):
    """`GET /news/economic-calendar`: la baseline cablata resta, il vuoto di
    Finnhub non passa piu' per quiete. Forma = quella di /news/macro
    (`fonti_mute`: dict, `avviso`: str), che NewsPage consuma con takeFonti."""
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import memory_db
    _trasporto(monkeypatch, {"/calendar/earnings": _R(200, {"earningsCalendar": []})})
    monkeypatch.setattr(memory_db, "MemoryDB", _DB)

    out = bellomberg_api.get_economic_calendar(days_ahead=7)

    assert out["count"] == len(out["items"]) and out["count"] > 0
    assert isinstance(out["fonti_mute"], dict)
    chiavi = list(out["fonti_mute"])
    assert any("/calendar/economic" in k for k in chiavi), out["fonti_mute"]
    assert "403" in out["fonti_mute"][chiavi[0]], out["fonti_mute"]
    assert out["avviso"] and "Finnhub" in out["avviso"], out["avviso"]


def test_endpoint_calendario_con_earnings_che_cade_lo_dichiara(monkeypatch):
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import memory_db
    _trasporto(monkeypatch, {"/calendar/earnings": _R(429, text="rate limited")})
    monkeypatch.setattr(memory_db, "MemoryDB", _DB)

    out = bellomberg_api.get_economic_calendar(days_ahead=7)

    assert any("/calendar/earnings" in k and "429" in v for k, v in out["fonti_mute"].items()), out["fonti_mute"]


def test_endpoint_calendario_registra_l_eccezione_degli_earnings(monkeypatch):
    """Review 27/08: il ramo `except` (DB giu', import rotto) non aveva test:
    l'eccezione deve finire in `fonti_mute`, non solo in una riga di stdout."""
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import memory_db
    _trasporto(monkeypatch, {})

    class _DBRotto:
        def get_portfolio_summary(self):
            raise RuntimeError("db giu'")
    monkeypatch.setattr(memory_db, "MemoryDB", _DBRotto)

    out = bellomberg_api.get_economic_calendar(days_ahead=7)

    v = out["fonti_mute"].get("finnhub /calendar/earnings", "")
    assert "eccezione" in v and "RuntimeError" in v, out["fonti_mute"]


def test_calendario_earnings_usa_natura_corrente_e_dichiara_negozio_guasto(monkeypatch):
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import classificazione
    from bellomberg.market_data import finnhub_news
    from bellomberg.storage import memory_db
    _trasporto(monkeypatch, {})

    class _DBMisto:
        def get_portfolio_summary(self):
            return {"positions": [
                {"ticker": "TESORO"}, {"ticker": "TOKEN"}, {"ticker": "IGNOTO"}
            ]}

    chiamati = []

    def earnings(tickers, days_ahead=14, motivo=None):
        chiamati.append(list(tickers))
        return []

    monkeypatch.setattr(memory_db, "MemoryDB", _DBMisto)
    monkeypatch.setattr(finnhub_news, "fetch_earnings_for_portfolio", earnings)
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {
        "origine": "fixture/veicoli.json", "motivo": None,
        "veicoli": {"TESORO": {"tipo": "dat"}, "TOKEN": {"tipo": "crypto"}},
    })

    out = bellomberg_api.get_economic_calendar(days_ahead=7)
    assert chiamati == [["TESORO"]]
    assert "natura non dichiarata" in out["fonti_mute"]["finnhub /calendar/earnings"]

    chiamati.clear()
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {
        "origine": "illeggibile", "motivo": "JSON sintetico rotto", "veicoli": {}
    })
    out = bellomberg_api.get_economic_calendar(days_ahead=7)
    assert chiamati == []
    motivo = out["fonti_mute"]["finnhub /calendar/earnings"]
    assert "illeggibile" in motivo and "JSON sintetico rotto" in motivo


def test_avviso_calendario_non_nega_gli_earnings_arrivati(monkeypatch):
    """Review 27/08: «la baseline c'e', il resto no» era falso nel caso normale
    (economic muto, earnings ARRIVATI e in `items`)."""
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import memory_db
    _trasporto(monkeypatch, {"/calendar/earnings": _R(200, {"earningsCalendar": [
        {"symbol": "ALFA", "date": "2026-09-01", "hour": "amc", "epsEstimate": 1.2,
         "year": 2026, "quarter": 3}]})})
    monkeypatch.setattr(memory_db, "MemoryDB", _DB)

    out = bellomberg_api.get_economic_calendar(days_ahead=7)

    assert any(e.get("type") == "Earnings" and "ALFA" in e.get("title", "") for e in out["items"]), out["items"]
    assert "il resto no" not in out["avviso"], out["avviso"]
    assert "manca" in out["avviso"] and "/calendar/economic" in out["avviso"], out["avviso"]
