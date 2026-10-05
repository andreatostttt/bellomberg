# -*- coding: utf-8 -*-
"""05/10/2026 (Opus 5.5): la chiave Polygon non deve mai finire in un messaggio d'errore.

data/price_updater.log aveva 48 righe «[IV] <T> KO dichiarato: expirations:
HTTPSConnectionPool(...) url: ...&apiKey=<chiave in chiaro>»: il TESTO
dell'eccezione di requests (che contiene l'URL con la querystring) diventava il
campo `error` e da li' il log. Qui si simula quell'eccezione con una chiave FINTA
e si verifica che non compaia in nessun messaggio, a nessun livello della catena
polygon_data -> vol_surface -> iv_history. Nessuna rete, DB in tmp_path.
"""
import pytest
import requests

from bellomberg.market_data import iv_history, polygon_data
from bellomberg.portfolio import vol_surface

CHIAVE = "kkFINTA0SEGRETA0zz"
# variante codificata: la vecchia maschera (replace della chiave identica) non la prende
CHIAVE_COD = "kk%2BFINTA%2FSEGRETA"


def _eccezione(chiave):
    return requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='api.polygon.io', port=443): Max retries exceeded with url: "
        "/v3/reference/options/contracts?underlying_ticker=ZZTEST&limit=1000&apiKey=%s "
        "(Caused by NameResolutionError(\"Failed to resolve 'api.polygon.io'\"))" % chiave)


@pytest.fixture
def rete_rotta(monkeypatch):
    chiamate = []

    def _get(url, *a, **k):
        chiamate.append(url)
        raise _eccezione(CHIAVE if len(chiamate) % 2 else CHIAVE_COD)
    monkeypatch.setattr(polygon_data, "POLYGON_KEY", CHIAVE)
    monkeypatch.setattr(polygon_data, "REQ_OK", True)
    monkeypatch.setattr(polygon_data.requests, "get", _get)
    vol_surface._CHAIN_CACHE.clear()
    return chiamate


def _pulito(oggetto):
    testo = repr(oggetto)
    return CHIAVE not in testo and CHIAVE_COD not in testo and "apiKey" not in testo


def test_get_restituisce_solo_tipo_e_path(rete_rotta, capsys):
    for _ in range(2):   # chiave identica e chiave codificata
        r = polygon_data._get("/v3/reference/options/contracts", {"underlying_ticker": "ZZTEST"})
        assert r == {"error": "ConnectionError on /v3/reference/options/contracts"}, r
    assert _pulito(capsys.readouterr().out)


def test_funzioni_pubbliche_e_catena_iv_senza_chiave(rete_rotta, capsys):
    esiti = [polygon_data.get_option_expirations("ZZTEST"),
             polygon_data.get_options_chain("ZZTEST", "2099-01-16"),
             polygon_data.get_options_summary_polygon("ZZTEST"),
             vol_surface.get_expiry_catalog("ZZTEST"),
             vol_surface.get_chain_detail("ZZTEST", "2099-01-16"),
             vol_surface.build_vol_surface("ZZTEST", include_context=False)]
    assert rete_rotta, "la rete finta non e' stata chiamata: il test non misura nulla"
    for e in esiti:
        assert e.get("error"), e
        assert _pulito(e), e
    assert "ConnectionError" in str(esiti[0]["error"]), esiti[0]
    assert _pulito(capsys.readouterr().out)


def test_iv_history_messaggio_senza_chiave(rete_rotta, tmp_path, capsys):
    out = iv_history.save_daily_snapshot(db_path=str(tmp_path / "iv.db"), tickers=["ZZTEST"],
                                         snap_date="2099-01-14", force=True)
    assert out["errors"].get("ZZTEST"), out
    assert _pulito(out), out
    assert _pulito(capsys.readouterr().out)


def test_iv_history_eccezione_di_vol_surface_diventa_il_tipo(monkeypatch, tmp_path):
    def _esplode(*a, **k):
        raise _eccezione(CHIAVE)
    monkeypatch.setattr(vol_surface, "build_vol_surface", _esplode)
    out = iv_history.save_daily_snapshot(db_path=str(tmp_path / "iv.db"), tickers=["ZZTEST"],
                                         snap_date="2099-01-14", force=True)
    assert out["errors"]["ZZTEST"] == "vol_surface: ConnectionError", out


def test_build_vol_surface_eccezione_esterna_diventa_il_tipo(monkeypatch):
    def _esplode(*a, **k):
        raise _eccezione(CHIAVE)
    monkeypatch.setattr(polygon_data, "polygon_available", lambda: True)
    monkeypatch.setattr(polygon_data, "get_option_expirations", _esplode)
    r = vol_surface.build_vol_surface("ZZTEST", include_context=False)
    assert r["error"] == "ConnectionError", r
