"""Cache GNews condivisa + igiene chiavi nei log (Opus 5.5 04/10, contratto C-G recon R1).

Il difetto: il limiter mette ogni query GNews in cooldown 2h e, senza cache, la seconda
chiamata della stessa query (altro specialista, feed, tool) tornava SKIP_COOLDOWN e lista
vuota -> la fonte pagata risultava MUTA agli agenti. La cura: gli articoli grezzi di una
risposta 200 si salvano per 2h e si servono in cooldown con stato "cache", contato fra le
fonti VIVE da tool_search_news. Una cache rotta si dichiara (mai "niente in cache" zitto).

Igiene: str(e) di requests contiene l'URL con la chiave in querystring; nei log e nei
motivi va solo il tipo dell'eccezione.

Nessuna rete: requests.get finto; nessun file in data/: la cache e' ridiretta in tmp_path.
Query e chiavi INVENTATE.
"""
import json

import pytest

from bellomberg.market_data import finnhub_news
from bellomberg.market_data import gnews_cache
from bellomberg.market_data import news_sources

SEGRETO = "QQSEGRETO123"


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(gnews_cache, "GNEWS_CACHE_PATH", tmp_path / "gnews_cache.json")
    monkeypatch.setattr(gnews_cache, "_PAUSA_TENTATIVO_S", 0)
    gnews_cache.reset_errori()
    news_sources.reset_status()
    yield tmp_path / "gnews_cache.json"
    gnews_cache.reset_errori()
    news_sources.reset_status()


def _art(n):
    return [{"title": "Titolo finto %d" % i, "description": "desc %d" % i,
             "url": "https://esempio.invalid/%d" % i, "publishedAt": "2026-10-04T10:0%dZ" % i,
             "source": {"name": "Testata Finta"}} for i in range(n)]


# --- chiave e TTL -----------------------------------------------------------------------

def test_chiave_contiene_la_lingua_e_normalizza_come_il_limiter():
    assert gnews_cache.chiave("  ZZTEST Corp ", "it") == "it|zztest corp"
    assert gnews_cache.chiave("ZZTEST Corp", "en") != gnews_cache.chiave("ZZTEST Corp", "it")
    lunga = "x" * 200
    assert gnews_cache.chiave(lunga) == "en|" + "x" * 80


def test_lingue_diverse_non_si_servono_a_vicenda():
    gnews_cache.scrivi("ZZTEST Corp", "en", _art(2), ora=1000.0)
    assert gnews_cache.leggi("ZZTEST Corp", "it", ora=1001.0) is None
    assert gnews_cache.leggi("zztest corp ", "en", ora=1001.0) is not None


def test_ttl_uguale_al_cooldown_gnews_del_limiter():
    from bellomberg.market_data.news_aggregator import NEWS_PROVIDER_LIMITS
    assert gnews_cache.TTL_S == NEWS_PROVIDER_LIMITS["gnews"]["cooldown"] == 7200


def test_ttl_voce_valida_fino_al_limite_poi_scade():
    gnews_cache.scrivi("ZZTEST Corp", "en", _art(3), ora=10_000.0)
    dentro = gnews_cache.leggi("ZZTEST Corp", "en", ora=10_000.0 + gnews_cache.TTL_S - 1)
    assert dentro is not None
    assert len(dentro["articles"]) == 3
    assert dentro["eta_s"] == pytest.approx(gnews_cache.TTL_S - 1)
    assert dentro["salvato_il"].endswith("Z")
    assert gnews_cache.leggi("ZZTEST Corp", "en", ora=10_000.0 + gnews_cache.TTL_S) is None
    assert gnews_cache.stato_ultimo_errore() is None  # scaduta non e' un guasto


# --- cache rotta: dichiarata ------------------------------------------------------------

def test_cache_assente_non_e_un_errore(cache_in_tmp):
    assert not cache_in_tmp.exists()
    assert gnews_cache.leggi("ZZTEST Corp") is None
    assert gnews_cache.stato_ultimo_errore() is None


def test_cache_illeggibile_e_dichiarata(cache_in_tmp, capsys):
    cache_in_tmp.write_text("{non json", encoding="utf-8")
    assert gnews_cache.leggi("ZZTEST Corp") is None
    err = gnews_cache.stato_ultimo_errore()
    assert err and "illeggibile" in err and "JSONDecodeError" in err
    assert "[GNEWS-CACHE]" in capsys.readouterr().out


def test_cache_di_forma_inattesa_e_dichiarata(cache_in_tmp):
    cache_in_tmp.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert gnews_cache.leggi("ZZTEST Corp") is None
    assert "forma inattesa" in (gnews_cache.stato_ultimo_errore() or "")


def test_eta_negativa_non_servita_e_dichiarata():
    gnews_cache.scrivi("ZZTEST Corp", "en", _art(1), ora=5000.0)
    assert gnews_cache.leggi("ZZTEST Corp", "en", ora=4000.0) is None
    assert "negativa" in (gnews_cache.stato_ultimo_errore() or "")


def test_scrittura_ritenta_permission_error_poi_riesce(monkeypatch, cache_in_tmp):
    vero = gnews_cache.os.replace
    cadute = {"n": 0}

    def replace_instabile(a, b):
        if cadute["n"] < 2:
            cadute["n"] += 1
            raise PermissionError("bloccato")
        return vero(a, b)
    monkeypatch.setattr(gnews_cache.os, "replace", replace_instabile)
    gnews_cache.scrivi("ZZTEST Corp", "en", _art(1))
    assert cadute["n"] == 2
    assert gnews_cache.leggi("ZZTEST Corp") is not None
    assert gnews_cache.stato_ultimo_errore() is None


def test_scrittura_bloccata_sempre_e_dichiarata_e_non_lascia_tmp(monkeypatch, cache_in_tmp):
    def replace_bloccato(a, b):
        raise PermissionError("bloccato")
    monkeypatch.setattr(gnews_cache.os, "replace", replace_bloccato)
    gnews_cache.scrivi("ZZTEST Corp", "en", _art(1))  # non solleva
    err = gnews_cache.stato_ultimo_errore()
    assert err and "scrivi" in err and "PermissionError" in err
    assert list(cache_in_tmp.parent.glob("*.tmp")) == []


def test_potatura_scadute_e_tetto_voci(monkeypatch, cache_in_tmp):
    monkeypatch.setattr(gnews_cache, "MAX_VOCI", 3)
    gnews_cache.scrivi("vecchia", "en", _art(1), ora=0.0)
    for i in range(5):
        gnews_cache.scrivi("q%d" % i, "en", _art(1), ora=10_000.0 + i)
    voci = json.loads(cache_in_tmp.read_text(encoding="utf-8"))["voci"]
    assert sorted(voci) == ["en|q2", "en|q3", "en|q4"]


# --- news_sources.fetch_gnews ----------------------------------------------------------

class _R:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def gnews_isolato(monkeypatch):
    monkeypatch.setattr(news_sources, "GNEWS_API_KEY", "chiave-finta")
    monkeypatch.setattr(news_sources, "_rate_limiter",
                        lambda: ((lambda p, q: True), (lambda p, q, got_429=False: None)))

    def niente_rete(*a, **k):
        raise AssertionError("rete non attesa")
    monkeypatch.setattr(news_sources.requests, "get", niente_rete)
    return monkeypatch


def test_cooldown_con_cache_serve_i_risultati_e_dichiara_cache(gnews_isolato):
    gnews_isolato.setattr(news_sources, "_provider_skip_reason", lambda p, q: "SKIP_COOLDOWN")
    gnews_cache.scrivi("ZZTEST Corp", "en", _art(4))
    out = news_sources.fetch_gnews("ZZTEST Corp", max_news=3)
    assert [n["titolo"] for n in out] == ["Titolo finto 0", "Titolo finto 1", "Titolo finto 2"]
    assert out[0]["fonte"] == "GNews (Testata Finta)"
    assert news_sources.last_status()["gnews"] == "cache"
    assert "gnews" in news_sources.last_cache_eta()


def test_cooldown_senza_cache_resta_skip_cooldown(gnews_isolato):
    gnews_isolato.setattr(news_sources, "_provider_skip_reason", lambda p, q: "SKIP_COOLDOWN")
    assert news_sources.fetch_gnews("ZZTEST Corp") == []
    assert news_sources.last_status()["gnews"] == "SKIP_COOLDOWN"
    assert news_sources.last_cache_eta() == {}


def test_cooldown_con_cache_rotta_e_stato_proprio(gnews_isolato, cache_in_tmp):
    gnews_isolato.setattr(news_sources, "_provider_skip_reason", lambda p, q: "SKIP_COOLDOWN")
    cache_in_tmp.write_text("{rotta", encoding="utf-8")
    assert news_sources.fetch_gnews("ZZTEST Corp") == []
    assert news_sources.last_status()["gnews"] == "SKIP_COOLDOWN_CACHE_KO"


def test_budget_esaurito_non_usa_la_cache(gnews_isolato):
    gnews_isolato.setattr(news_sources, "_provider_skip_reason", lambda p, q: "SKIP_BUDGET")
    gnews_cache.scrivi("ZZTEST Corp", "en", _art(2))
    assert news_sources.fetch_gnews("ZZTEST Corp") == []
    assert news_sources.last_status()["gnews"] == "SKIP_BUDGET"


def test_risposta_200_scrive_i_grezzi_in_cache(gnews_isolato):
    gnews_isolato.setattr(news_sources, "_provider_skip_reason", lambda p, q: None)
    gnews_isolato.setattr(news_sources.requests, "get",
                          lambda url, **k: _R(200, {"articles": _art(2)}))
    out = news_sources.fetch_gnews("ZZTEST Corp", max_news=5)
    assert len(out) == 2 and news_sources.last_status()["gnews"] == "live"
    c = gnews_cache.leggi("ZZTEST Corp", "en")
    assert c is not None and c["articles"] == _art(2)


def test_eccezione_gnews_non_scrive_la_chiave(gnews_isolato, capsys):
    gnews_isolato.setattr(news_sources, "_provider_skip_reason", lambda p, q: None)

    def esplode(url, **k):
        raise ConnectionError("https://gnews.io/api/v4/search?q=x&apikey=" + SEGRETO)
    gnews_isolato.setattr(news_sources.requests, "get", esplode)
    assert news_sources.fetch_gnews("ZZTEST Corp") == []
    uscita = capsys.readouterr().out
    assert SEGRETO not in uscita and "ConnectionError" in uscita
    assert news_sources.last_status()["gnews"] == "ERROR"


def test_eccezione_marketaux_non_scrive_la_chiave(monkeypatch, capsys):
    monkeypatch.setattr(news_sources, "MARKETAUX_API_KEY", "chiave-finta")
    monkeypatch.setattr(news_sources, "_provider_skip_reason", lambda p, q: None)
    monkeypatch.setattr(news_sources, "_rate_limiter",
                        lambda: ((lambda p, q: True), (lambda p, q, got_429=False: None)))

    def esplode(url, **k):
        raise TimeoutError("https://api.marketaux.com/v1/news/all?api_token=" + SEGRETO)
    monkeypatch.setattr(news_sources.requests, "get", esplode)
    assert news_sources.fetch_marketaux(query="ZZTEST") == []
    uscita = capsys.readouterr().out
    assert SEGRETO not in uscita and "TimeoutError" in uscita


# --- finnhub: motivo senza chiave ------------------------------------------------------

def test_finnhub_eccezione_motivo_senza_chiave(monkeypatch, capsys):
    monkeypatch.setattr(finnhub_news, "REQ_OK", True)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: SEGRETO)

    def esplode(url, **k):
        raise ConnectionError("https://finnhub.io/api/v1/company-news?symbol=ZZTEST&token=" + SEGRETO)
    monkeypatch.setattr(finnhub_news.requests, "get", esplode)
    motivo = []
    assert finnhub_news._api_get("/company-news", {"symbol": "ZZTEST"}, motivo=motivo) is None
    assert motivo and all(SEGRETO not in m for m in motivo), motivo
    assert any("ConnectionError" in m and "/company-news" in m for m in motivo)
    assert SEGRETO not in capsys.readouterr().out


# --- tool_search_news: "cache" e' una fonte VIVA ---------------------------------------

def _fonti_finte(monkeypatch, stati, eta_gnews=None):
    def fetcher(nome):
        def f(*a, **k):
            news_sources._record_status(nome, stati[nome])
            if nome == "gnews" and stati[nome] == "cache":
                news_sources._status_tls.eta_cache = {"gnews": eta_gnews}
                return [{"titolo": "Titolo finto cache", "fonte": "GNews (X)", "url": "u", "data": "d"}]
            return []
        return f
    for nome in ("marketaux", "thenewsapi", "gnews"):
        monkeypatch.setattr(news_sources, "fetch_" + nome, fetcher(nome))
    monkeypatch.setattr(news_sources, "fetch_yfinance_news",
                        lambda *a, **k: (news_sources._record_status("yfinance", stati["yfinance"]), [])[1])


def test_tool_search_news_cache_conta_fra_le_vive(monkeypatch):
    from bellomberg.agents import agent_tools
    _fonti_finte(monkeypatch, {"marketaux": "live", "thenewsapi": "live", "gnews": "cache",
                               "yfinance": "live"}, eta_gnews=1234.5)
    out = agent_tools.tool_search_news("ZZTEST")
    assert out["fonti"]["gnews"] == "cache"
    assert "fonti_mute" not in out and "copertura" not in out
    assert out["fonti_eta_cache_s"] == {"gnews": 1234.5}
    assert out["count"] == 1


def test_tool_search_news_sola_cache_viva_e_copertura_parziale(monkeypatch):
    from bellomberg.agents import agent_tools
    _fonti_finte(monkeypatch, {"marketaux": "SKIP_BUDGET", "thenewsapi": "SKIP_BUDGET",
                               "gnews": "cache", "yfinance": "ERROR"}, eta_gnews=60.0)
    out = agent_tools.tool_search_news("ZZTEST")
    assert out["copertura"] == "PARZIALE"
    assert out["fonti_mute"] == ["marketaux", "thenewsapi", "yfinance"]
    assert "fonti vive (gnews)" in out["avviso"]


def test_tool_search_news_cache_rotta_e_muta(monkeypatch):
    from bellomberg.agents import agent_tools
    _fonti_finte(monkeypatch, {"marketaux": "live", "thenewsapi": "live",
                               "gnews": "SKIP_COOLDOWN_CACHE_KO", "yfinance": "live"})
    out = agent_tools.tool_search_news("ZZTEST")
    assert out["fonti_mute"] == ["gnews"]
    assert "fonti_eta_cache_s" not in out


# --- finnhub: epoch originale accanto all'ora locale (review RV-N P3) -------------------

def _company_news_finta(monkeypatch, item):
    monkeypatch.setattr(finnhub_news, "_norm_ticker", lambda t: t)
    monkeypatch.setattr(finnhub_news, "_api_get", lambda path, params, motivo=None: [item])


def test_company_news_porta_l_epoch_originale(monkeypatch):
    epoch = 1792895400  # inventato: 2026-10-25 01:30 UTC, ora ripetuta del cambio d'ora in Europa
    _company_news_finta(monkeypatch, {"headline": "Titolo finto", "datetime": epoch,
                                      "url": "https://esempio.invalid/x", "source": "Finta"})
    out = finnhub_news.fetch_company_news("ZZTEST")
    assert out[0]["published_epoch"] == epoch
    from datetime import datetime as _dt
    assert out[0]["published_at"] == _dt.fromtimestamp(epoch).isoformat()  # invariato
    assert set(out[0]) == {"title", "snippet", "url", "provider", "source", "published_at",
                           "published_epoch", "ticker_mentioned", "image", "category"}


def test_company_news_senza_epoch_e_none_dichiarato(monkeypatch):
    _company_news_finta(monkeypatch, {"headline": "Titolo finto", "url": "u"})
    out = finnhub_news.fetch_company_news("ZZTEST")
    assert out[0]["published_epoch"] is None
    assert out[0]["published_at"] == ""
