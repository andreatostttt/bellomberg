# -*- coding: utf-8 -*-
"""P1 (04/10/2026, Opus 5.5): le fonti SOLO-USA rispondono «non coperto» PRIMA della rete.

Quiver, Form 4 SEC e opzioni Polygon chiesti con un ticker europeo tornavano `n: 0` /
«no data» (indistinguibile da «nessun dato») o, peggio, i dati di un OMONIMO americano
(`BA.L` -> `BA` -> Boeing). Qui si misura con un CONTATORE sul trasporto che su un
ticker estero inventato non parte nessuna chiamata, e che un ticker USA inventato
continua a passare (la guardia non deve spegnere la fonte).

Ticker e numeri INVENTATI (regola del cancello privacy): QQSYN.MI, QQSYN.L, ZZTEST...
"""
import pytest

from bellomberg.market_data import copertura, polygon_data, quiver_data, sec_edgar
from bellomberg.market_data.copertura import (copertura_opzioni, copertura_usa,
                                              risposta_non_coperta)
from bellomberg.portfolio import vol_surface


# ------------------------------------------------------------ trasporto finto

class _Risposta:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = "[]"

    def json(self):
        return self._payload


class _Contatore:
    """Sostituisce `requests.get` nel modulo: conta e risponde 200 con `payload`."""

    def __init__(self, payload):
        self.chiamate = []
        self.payload = payload

    def get(self, url, *a, **k):
        self.chiamate.append(url)
        return _Risposta(self.payload)


@pytest.fixture
def rete_quiver(monkeypatch):
    c = _Contatore([])
    monkeypatch.setattr(quiver_data, "QUIVER_KEY", "kk-finta-quiver")
    monkeypatch.setattr(quiver_data, "REQ_OK", True)
    monkeypatch.setattr(quiver_data.requests, "get", c.get)
    return c


@pytest.fixture
def rete_polygon(monkeypatch):
    c = _Contatore({"results": []})
    monkeypatch.setattr(polygon_data, "POLYGON_KEY", "kk-finta-polygon")
    monkeypatch.setattr(polygon_data, "REQ_OK", True)
    monkeypatch.setattr(polygon_data.requests, "get", c.get)
    return c


@pytest.fixture
def rete_sec(monkeypatch):
    c = _Contatore({})
    monkeypatch.setattr(sec_edgar.requests, "get", c.get)
    # il negozio privato degli alias vive in data/: nei test un negozio finto
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {"QQALI": "QQALIUS"})
    risolti = []

    def _lookup(ticker, motivo=None):
        risolti.append(ticker)
        if motivo is not None:
            motivo.append("finto: nessun CIK")
        return None
    monkeypatch.setattr(sec_edgar, "lookup_cik", _lookup)
    c.risolti = risolti
    return c


ESTERI = ["QQSYN.MI", "QQSYN.DE", "QQSYN.L", "QQSYN.PA", "qqsyn.mi "]


# ------------------------------------------------------------ classificazione

@pytest.mark.parametrize("ticker,mercato", [
    ("QQSYN.MI", "Borsa Italiana"), ("QQSYN.DE", "Xetra"), ("QQSYN.L", "London SE"),
    ("QQSYN.PA", "Euronext Paris"), ("QQSYN.T", "Tokyo SE"), ("qqsyn.mi ", "Borsa Italiana"),
])
def test_suffisso_del_registro_e_non_coperto_col_nome_del_mercato(ticker, mercato):
    e = copertura_usa(ticker, "quiver_congress")
    assert e["stato"] == "non_coperto", e
    assert e["mercato"] == mercato, e
    assert e["fonte"] == "quiver_congress"
    # il suffisso NON si toglie: il ticker esce intero
    assert e["ticker"] == ticker.strip().upper(), e
    assert mercato in e["motivo"], e


def test_senza_suffisso_e_coperto_ma_il_motivo_dice_presunto():
    e = copertura_usa("ZZTEST", "quiver_lobbying")
    assert e["stato"] == "coperto", e
    assert e["ticker"] == "ZZTEST"
    assert "PRESUNTO" in e["motivo"] or "PRESUMED" in e["motivo"], e


@pytest.mark.parametrize("valuta", ["EUR", "gbx", " CHF "])
def test_senza_suffisso_con_valuta_non_usd_e_indeterminato(valuta):
    # dottrina classificazione.valuta: niente suffisso non implica USD
    e = copertura_usa("ZZTEST", "insider_form4", valuta)
    assert e["stato"] == "indeterminato", e
    assert valuta.strip().upper() in e["motivo"], e


@pytest.mark.parametrize("valuta", ["USD", "usd", None, ""])
def test_senza_suffisso_con_valuta_usd_o_assente_e_coperto(valuta):
    assert copertura_usa("ZZTEST", "insider_form4", valuta)["stato"] == "coperto"


@pytest.mark.parametrize("ticker", ["BTC", "ETH-USD", "QQC-USDT", "qqc-eur"])
def test_crypto_e_non_coperto_dichiarato(ticker):
    e = copertura_usa(ticker, "quiver_congress")
    assert e["stato"] == "non_coperto", e
    assert e["mercato"] == "crypto 24/7", e


def test_classe_di_azioni_col_trattino_non_e_crypto():
    # BRK-B in forma yfinance: il trattino da solo non fa una coppia crypto
    assert copertura_usa("ZZT-B", "quiver_congress")["stato"] == "coperto"


@pytest.mark.parametrize("ticker", ["QQSYN.MC", "QQSYN.CO", "QQSYN.ST", "QQSYN.OL", "QQSYN.AX", "qqsyn.zz "])
def test_suffisso_di_due_lettere_fuori_registro_e_non_coperto(ticker):
    # ok main 04/10 (opzione b, review RV-C P3a)
    e = copertura_usa(ticker, "quiver_lobbying")
    assert e["stato"] == "non_coperto", e
    assert e["mercato"] == copertura.MERCATO_FUORI_REGISTRO, e
    assert e["ticker"] == ticker.strip().upper(), e
    assert "fuori registro" in e["motivo"] or "outside the registry" in e["motivo"], e
    o = copertura_opzioni(ticker)
    assert o["stato"] == "non_coperto" and "IBKR" in o["motivo"], o


def test_quiver_e_polygon_listino_fuori_registro_nessuna_chiamata(rete_quiver, rete_polygon):
    r = quiver_data.get_lobbying("QQSYN.MC")
    p = polygon_data.get_options_chain("QQSYN.MC")
    assert rete_quiver.chiamate == [] and rete_polygon.chiamate == [], (rete_quiver.chiamate, rete_polygon.chiamate)
    assert r["copertura"] == p["copertura"] == "non_coperto", (r, p)


@pytest.mark.parametrize("ticker", ["ZZTEST.B", "DEMO.X", "QQSYN.12", "^QQIDX", "QQF=F", "QQ USD", "", None])
def test_suffisso_sconosciuto_simboli_strani_e_vuoto_sono_indeterminati(ticker):
    e = copertura_usa(ticker, "quiver_congress")
    assert e["stato"] == "indeterminato", e
    assert e["motivo"], e


def test_copertura_usa_non_solleva_mai():
    for t in (123, object(), "....", "."):
        assert copertura_usa(t, None)["stato"] in copertura.STATI


# ------------------------------------------------------------ opzioni

def test_opzioni_idem_motivo_dichiara_nessun_tentativo_ibkr():
    e = copertura_opzioni("QQSYN.MI")
    assert e["stato"] == "non_coperto", e
    assert e["fonte"] == "opzioni_usa"
    assert "IDEM" in e["motivo"] and "IBKR" in e["motivo"], e


@pytest.mark.parametrize("ticker", ["QQSYN.DE", "QQSYN.L", "QQSYN.PA", "QQSYN.SW"])
def test_opzioni_ogni_suffisso_estero_e_non_coperto(ticker):
    e = copertura_opzioni(ticker)
    assert e["stato"] == "non_coperto", e
    assert "IBKR" in e["motivo"] and e["mercato"] in e["motivo"], e
    assert "IDEM" not in e["motivo"], e


def test_opzioni_proxy_adr_etf_esplicito_resta_coperto():
    # un ADR/ETF USA passato esplicitamente e' un ticker USA vero
    assert copertura_opzioni("ZZADR")["stato"] == "coperto"
    assert copertura_opzioni("ZZADR", valuta="USD")["stato"] == "coperto"


def test_risposta_non_coperta_e_un_error_non_uno_zero():
    r = risposta_non_coperta(copertura_opzioni("QQSYN.MI"), "src finta")
    assert r["error"].startswith("non coperto: ") or r["error"].startswith("not covered: "), r
    assert r["copertura"] == "non_coperto" and r["_source"] == "src finta"
    assert r["ticker"] == "QQSYN.MI" and "n" not in r
    ind = risposta_non_coperta(copertura_usa("ZZT.B", "x"), "s")
    assert ind["copertura"] == "indeterminato", ind


def test_risposta_non_coperta_rifiuta_un_esito_coperto():
    with pytest.raises(ValueError):
        risposta_non_coperta(copertura_usa("ZZTEST", "x"), "s")


# ------------------------------------------------------------ cablaggio Quiver

@pytest.mark.parametrize("ticker", ESTERI)
@pytest.mark.parametrize("funzione,chiave", [
    ("get_congress_trades", "quiver_congress"), ("get_lobbying", "quiver_lobbying"),
    ("get_gov_contracts", "quiver_gov_contracts")])
def test_quiver_estero_nessuna_chiamata_e_non_coperto(rete_quiver, ticker, funzione, chiave):
    r = getattr(quiver_data, funzione)(ticker)
    assert rete_quiver.chiamate == [], rete_quiver.chiamate
    assert r.get("copertura") == "non_coperto", r
    assert r.get("fonte") == chiave, r
    assert "n" not in r and r.get("error"), r
    assert r["_source"].startswith("quiver"), r


@pytest.mark.parametrize("funzione", ["get_congress_trades", "get_lobbying", "get_gov_contracts"])
def test_quiver_indeterminato_passa_col_simbolo_intatto(rete_quiver, funzione):
    # il provider ferma solo non_coperto: un suffisso fuori registro (classe .B)
    # va all'URL INTATTO, mai troncato (nessun omonimo possibile)
    getattr(quiver_data, funzione)("ZZTEST.B")
    assert len(rete_quiver.chiamate) == 1, rete_quiver.chiamate
    assert rete_quiver.chiamate[0].endswith("/ZZTEST.B"), rete_quiver.chiamate


@pytest.mark.parametrize("funzione", ["get_congress_trades", "get_lobbying", "get_gov_contracts"])
def test_quiver_ticker_usa_passa_ancora_dalla_rete(rete_quiver, funzione):
    r = getattr(quiver_data, funzione)("ZZTEST")
    assert len(rete_quiver.chiamate) == 1, rete_quiver.chiamate
    assert rete_quiver.chiamate[0].endswith("/ZZTEST"), rete_quiver.chiamate
    assert r["n"] == 0 and "copertura" not in r, r


def test_quiver_feed_generale_senza_ticker_non_e_bloccato(rete_quiver):
    r = quiver_data.get_congress_trades()
    assert len(rete_quiver.chiamate) == 1 and r["ticker"] == "ALL", r


# ------------------------------------------------------------ cablaggio Polygon

@pytest.mark.parametrize("ticker", ESTERI)
def test_polygon_scadenze_estero_nessuna_chiamata(rete_polygon, ticker):
    r = polygon_data.get_option_expirations(ticker)
    assert rete_polygon.chiamate == [], rete_polygon.chiamate
    assert r["copertura"] == "non_coperto" and "IBKR" in r["error"], r


@pytest.mark.parametrize("ticker", ESTERI)
def test_polygon_chain_estero_nessuna_chiamata(rete_polygon, ticker):
    r = polygon_data.get_options_chain(ticker, "2099-01-16")
    assert rete_polygon.chiamate == [], rete_polygon.chiamate
    assert r["copertura"] == "non_coperto", r


def test_polygon_indeterminato_passa_col_simbolo_intatto(rete_polygon):
    r = polygon_data.get_options_chain("ZZTEST.B")
    assert "copertura" not in r, r
    assert len(rete_polygon.chiamate) == 1, rete_polygon.chiamate
    assert rete_polygon.chiamate[0].endswith("/v3/snapshot/options/ZZTEST.B"), rete_polygon.chiamate
    polygon_data.get_option_expirations("ZZTEST.B")
    assert len(rete_polygon.chiamate) > 1, rete_polygon.chiamate


def test_polygon_summary_estero_nessuna_chiamata_e_niente_scadenze(rete_polygon, monkeypatch):
    chiamate_interne = []
    monkeypatch.setattr(polygon_data, "get_option_expirations",
                        lambda *a, **k: chiamate_interne.append(a) or {"error": "x"})
    r = polygon_data.get_options_summary_polygon("QQSYN.MI")
    assert rete_polygon.chiamate == [] and chiamate_interne == [], (rete_polygon.chiamate, chiamate_interne)
    assert r["copertura"] == "non_coperto" and r["_source"] == "polygon options summary", r


def test_polygon_summary_estero_senza_chiave_dice_comunque_non_coperto(monkeypatch):
    # «non coperto» e' vero a prescindere dalla chiave: la guardia viene prima
    monkeypatch.setattr(polygon_data, "POLYGON_KEY", "")
    r = polygon_data.get_options_summary_polygon("QQSYN.MI")
    assert r.get("copertura") == "non_coperto", r


def test_polygon_ticker_usa_passa_ancora_dalla_rete(rete_polygon):
    r = polygon_data.get_options_chain("ZZTEST", "2099-01-16")
    assert len(rete_polygon.chiamate) == 1, rete_polygon.chiamate
    assert "/v3/snapshot/options/ZZTEST" in rete_polygon.chiamate[0]
    assert "copertura" not in r, r
    polygon_data.get_option_expirations("ZZTEST")
    assert len(rete_polygon.chiamate) > 1


def test_gex_eredita_la_guardia_del_provider(rete_polygon):
    from bellomberg.portfolio.positioning_tools import compute_gex
    r = compute_gex("QQSYN.MI")
    assert rete_polygon.chiamate == [], rete_polygon.chiamate
    assert "IDEM" in str(r.get("error")), r


# ------------------------------------------------------------ cablaggio vol_surface

@pytest.fixture
def provider_contato(monkeypatch):
    chiamate = []
    monkeypatch.setattr(polygon_data, "polygon_available", lambda: True)
    monkeypatch.setattr(polygon_data, "_get",
                        lambda path, params=None: chiamate.append(path) or {"results": []})
    vol_surface._CHAIN_CACHE.clear()
    return chiamate


@pytest.mark.parametrize("ticker", ["QQSYN.MI", "QQSYN.L"])
def test_vol_catalogo_estero_nessuna_chiamata(provider_contato, ticker):
    r = vol_surface.get_expiry_catalog(ticker)
    assert provider_contato == [], provider_contato
    assert r["copertura"] == "non_coperto" and r["requests_used"] == 0, r
    assert r["complete"] is False and r["expirations"] == [], r


@pytest.mark.parametrize("ticker", ["QQSYN.MI", "QQSYN.DE"])
def test_vol_chain_estero_nessuna_chiamata_e_nessuna_cache(provider_contato, ticker):
    r = vol_surface.get_chain_detail(ticker, "2099-01-16")
    assert provider_contato == [], provider_contato
    assert r["copertura"] == "non_coperto" and r["requests_used"] == 0, r
    assert r["error"], r
    assert not vol_surface._CHAIN_CACHE, vol_surface._CHAIN_CACHE


def test_vol_indeterminato_passa_col_simbolo_intatto(provider_contato):
    a = vol_surface.get_expiry_catalog("ZZTEST.B")
    vol_surface.get_chain_detail("ZZTEST.B", "2099-01-16")
    assert len(provider_contato) == 2, provider_contato
    assert provider_contato[1].endswith("/ZZTEST.B"), provider_contato
    assert "copertura" not in a, a


def test_vol_ticker_usa_passa_ancora_dal_provider(provider_contato):
    vol_surface.get_expiry_catalog("ZZTEST")
    vol_surface.get_chain_detail("ZZTEST", "2099-01-16")
    assert len(provider_contato) == 2, provider_contato


# ------------------------------------------------------------ cablaggio SEC (insider)

@pytest.mark.parametrize("ticker", ["QQSYN.L", "QQSYN.MI", "QQSYN.DE"])
def test_insider_sec_estero_non_risolve_il_cik_e_dichiara(rete_sec, ticker):
    motivo = []
    r = sec_edgar.get_insider_trades(ticker, motivo=motivo)
    assert r == []
    assert rete_sec.risolti == [], rete_sec.risolti        # lookup_cik MAI chiamata
    assert rete_sec.chiamate == [], rete_sec.chiamate
    assert motivo and "OMONIMO" in motivo[0], motivo


@pytest.mark.parametrize("ticker", ["BTC", "SOL", "BTC-USD", "QQC-USDT"])
def test_insider_sec_crypto_non_risolve_il_cik_e_dichiara(rete_sec, ticker):
    # review RV-C: le crypto non hanno punto e arrivavano a lookup_cik
    motivo = []
    assert sec_edgar.get_insider_trades(ticker, motivo=motivo) == []
    assert rete_sec.risolti == [] and rete_sec.chiamate == [], rete_sec.risolti
    assert motivo and "crypto" in motivo[0], motivo
    assert sec_edgar.get_recent_filings(ticker) == []
    assert rete_sec.risolti == []


def test_recent_filings_estero_senza_motivo_non_risolve(rete_sec):
    assert sec_edgar.get_recent_filings("QQSYN.L") == []
    assert rete_sec.risolti == [] and rete_sec.chiamate == []


def test_insider_sec_alias_verificato_passa_ancora(rete_sec):
    # la guardia CIK resta quella di ticker_ambiguo_per_cik: un alias verificato passa
    motivo = []
    sec_edgar.get_insider_trades("QQALI.MI", motivo=motivo)
    assert rete_sec.risolti == ["QQALI.MI"], rete_sec.risolti


def test_insider_sec_ticker_usa_passa_ancora(rete_sec):
    sec_edgar.get_insider_trades("ZZTEST", motivo=[])
    assert rete_sec.risolti == ["ZZTEST"], rete_sec.risolti
