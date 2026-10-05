"""G3 seguito 2 (04/10/2026, REV_G8 rilievo 2): il testo delle eccezioni di requests porta l'URL
intero con la chiave API in querystring. Nessun log e nessun valore restituito all'agente la
deve contenere. Chiavi FINTE, rete finta."""
import pytest

from bellomberg.core.errori_sicuri import descrivi_eccezione, senza_segreti

CHIAVE = "CHIAVEFINTAqq1234567890zz"


class _Risposta:
    status_code = 429


class _ErroreRequests(Exception):
    def __init__(self, con_stato=False):
        super().__init__(
            "HTTPSConnectionPool(host='api.example', port=443): Max retries exceeded with url: "
            "/fred/series/observations?series_id=ZZTEST&api_key=" + CHIAVE + "&file_type=json "
            "(Caused by NewConnectionError('x'))")
        if con_stato:
            self.response = _Risposta()


@pytest.mark.parametrize("testo", [
    "url: /v2/everything?q=ACME&apiKey=" + CHIAVE,
    "429 Client Error: Too Many Requests for url: https://api.example/news?api_token=" + CHIAVE + "&x=1",
    "https://api.example/v1/x?token=" + CHIAVE,
    "corpo che riecheggia la chiave " + CHIAVE,
])
def test_senza_segreti_toglie_chiave_e_querystring(monkeypatch, testo):
    monkeypatch.setenv("ZZTEST_API_KEY", CHIAVE)
    pulito = senza_segreti(testo)
    assert CHIAVE not in pulito


def test_querystring_tolta_anche_con_chiave_sconosciuta():
    pulito = senza_segreti("for url: https://api.example/a/b?q=1&apikey=" + CHIAVE)
    assert CHIAVE not in pulito and "https://api.example/a/b" in pulito


def test_descrivi_eccezione_tipo_stato_e_messaggio_ripulito():
    testo = descrivi_eccezione(_ErroreRequests(con_stato=True))
    assert CHIAVE not in testo and "_ErroreRequests" in testo and "HTTP 429" in testo
    assert "/fred/series/observations" in testo          # il percorso resta: aiuta la diagnosi


def test_fred_non_restituisce_la_chiave_all_agente(monkeypatch, capsys):
    from bellomberg.agents import agent_tools as at

    import bellomberg.core.config as cfg
    monkeypatch.setattr(cfg, "FRED_API_KEY", CHIAVE)
    monkeypatch.setattr(at._req, "get", lambda *a, **k: (_ for _ in ()).throw(_ErroreRequests()))
    at._FRED_CACHE.clear()
    out = at._fred_fetch_series("ZZTEST")
    assert "error" in out and CHIAVE not in out["error"] and "api_key" not in out["error"]
    assert "ZZTEST" in out["error"]
    assert CHIAVE not in capsys.readouterr().out


def test_tavily_non_restituisce_la_chiave_all_agente(monkeypatch):
    from bellomberg.agents import agent_tools as at

    monkeypatch.setattr(at, "TAVILY_API_KEY", CHIAVE, raising=False)

    class _Errore(Exception):
        pass

    monkeypatch.setattr(at._req, "post", lambda *a, **k: (_ for _ in ()).throw(_Errore("echo " + CHIAVE)))
    out = at.tool_tavily_search("ACME")
    assert CHIAVE not in out["error"]


def test_finnhub_motivo_senza_chiave(monkeypatch):
    import bellomberg.market_data.finnhub_news as fh

    monkeypatch.setattr(fh, "_get_key", lambda: CHIAVE)
    monkeypatch.setattr(fh.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        _ErroreRequests()))
    motivo = []
    assert fh._api_get("/company-news", {"symbol": "ZZTEST"}, motivo=motivo) is None
    assert motivo and CHIAVE not in " ".join(motivo)


def test_quiver_errore_senza_chiave(monkeypatch):
    import bellomberg.market_data.quiver_data as qd

    monkeypatch.setattr(qd, "quiver_available", lambda: True)
    monkeypatch.setattr(qd, "QUIVER_KEY", CHIAVE, raising=False)
    monkeypatch.setattr(qd.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        _ErroreRequests()))
    out = qd._get("/beta/x")
    assert CHIAVE not in out["error"]


def test_finnhub_cablaggio_vero_fino_a_providers_blocked(monkeypatch):
    """REV_G3 R1: l'eccezione VERA di requests dentro finnhub_news (non un fetch finto che
    solleva) arriva a providers_blocked senza la chiave."""
    import bellomberg.market_data.finnhub_news as fh
    import bellomberg.market_data.news_aggregator as na

    monkeypatch.setattr(fh, "_get_key", lambda: CHIAVE)
    monkeypatch.setattr(fh, "_norm_ticker", lambda t: "ZZTEST", raising=False)
    monkeypatch.setattr(fh.requests, "get", lambda *a, **k: (_ for _ in ()).throw(_ErroreRequests()))
    monkeypatch.setattr(na, "_voce_termini", lambda t: (["Zztest"], "voce", {}))
    monkeypatch.setattr(na, "_simbolo_news", lambda t, nome: ("ZZTEST", "test"))
    monkeypatch.setattr(na, "_all_rss_cached", lambda *a, **k: [])
    for fn in ("_fetch_newsapi", "_fetch_thenewsapi", "_fetch_gnews", "_fetch_yfinance_news"):
        monkeypatch.setattr(na, fn, lambda *a, **k: [])
    import bellomberg.market_data.tiingo_news as tn
    monkeypatch.setattr(tn, "tiingo_available", lambda: False)
    na.invalidate_cache()
    try:
        na.search_news_for_ticker("ZZTEST", days=1, max_per_source=1)
        guasti = na._runtime_failures_correnti()
        assert "finnhub" in guasti                                   # il guasto resta dichiarato
        testo = " ".join(str(v) for v in guasti.values())
        assert CHIAVE not in testo and "_ErroreRequests" in testo
    finally:
        na.invalidate_cache()


def test_parametro_chiave_fuori_da_un_url():
    """Corpo d'errore che riporta i parametri senza URL e con una chiave non nota."""
    pulito = senza_segreti("invalid request: api_token=" + CHIAVE + " limit=5")
    assert CHIAVE not in pulito and "limit=5" in pulito


def test_guardia_di_rete_copre_i_file_che_nominano_i_moduli_notizie(tmp_path):
    """REV_G3 R4: la guardia non dipende piu' dal solo nome del file di test."""
    from tests.conftest import _test_delle_notizie

    neutro = tmp_path / "test_zz_calendario.py"
    neutro.write_text("from bellomberg.market_data import finnhub_news\n", encoding="utf-8")
    estraneo = tmp_path / "test_zz_altro.py"
    estraneo.write_text("import math\n", encoding="utf-8")
    assert _test_delle_notizie(neutro) is True
    assert _test_delle_notizie(estraneo) is False


def test_guardia_di_rete_attiva_in_questo_file(niente_rete_notizie):
    """Il cablaggio: questo file non ha «news» nel nome ma nomina finnhub_news, quindi la
    guardia e' accesa (prima della REV_G3 R4 non lo era)."""
    import socket

    assert niente_rete_notizie is not None
    with pytest.raises(ConnectionError):
        socket.getaddrinfo("zz.example.invalid", 443)
    assert niente_rete_notizie == ["zz.example.invalid"]
    niente_rete_notizie.clear()
