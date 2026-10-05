# -*- coding: utf-8 -*-
"""Prezzi di SEDE retail (stesso orologio di Trade Republic).

Il fatto (misurato 17/09): Yahoo quota Xetra in delayed ~15 min con baseline
chiusura Xetra 17:30, TR quota L&S real-time con baseline serale — il P&L GG
diverge di ~1pp. La sede retail tedesca (Tradegate, keyless) rende live +
chiusura DI SEDE: stessa baseline di TR, senza credenziali.

Il contratto:
  - la mappa simbolo -> ISIN di sede sta nel negozio privato degli alias
    (sezione `tradegate`); un ticker fuori mappa resta sul fallback Yahoo
    dichiarato, mai silenzio;
  - `_fetch_venue` rende {"price", "venue_close"} con parsing dei decimali
    tedeschi ("54,80", "1.046,80"); `last` assente -> medio bid/ask;
  - `update_all_prices` con fonte "tradegate" scrive snapshot + chiusura di
    sede; `get_portfolio` preferisce la chiusura di sede datata prima
    dell'ultimo giorno con prezzi, altrimenti snapshot+carico invariati.
"""
import json
import sqlite3

import pytest

import bellomberg.cli.price_updater as pu


@pytest.fixture(autouse=True)
def _alias_sede(tmp_path, monkeypatch):
    from bellomberg.storage import negozi_privati
    percorso = tmp_path / "alias_fonti.json"
    percorso.write_text(json.dumps({"tradegate": {"ALFA.DE": "DE000ALFA001"}}),
                        encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(percorso))
    # 04/10: stato di frequenza/pausa della sede azzerato e nessun intervallo
    # minimo (i test della frequenza lo fissano da soli).
    monkeypatch.setattr(pu, "_VENUE_STATO", {"ultima_richiesta": {}, "errori_consecutivi": 0,
                                             "pausa_fino": None, "ultimo_errore": None})
    monkeypatch.setenv("VENUE_POLL_MIN_SECONDS", "0")


def test_isin_di_sede_dal_negozio_privato():
    assert pu._isin_sede("alfa.de") == "DE000ALFA001"
    assert pu._isin_sede("ZZZZ.XX") is None


def test_isin_di_sede_malformato_rende_il_negozio_illeggibile(tmp_path, monkeypatch):
    from bellomberg.storage import negozi_privati
    percorso = tmp_path / "alias_rotto.json"
    percorso.write_text(json.dumps({"tradegate": {"ALFA.DE": "non-un-isin"}}),
                        encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(percorso))
    assert pu._isin_sede("ALFA.DE") is None


def test_catena_automatica_mette_la_sede_prima_senza_ibkr():
    assert pu.AUTOMATIC_PRICE_SOURCES[0] == "tradegate"
    assert "ibkr" not in pu.AUTOMATIC_PRICE_SOURCES
    assert pu.FONTI_PREZZI[0] == "ibkr"
    assert "tradegate" in pu.FONTI_PREZZI


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _rete(monkeypatch, payload):
    monkeypatch.setattr(
        pu, "requests",
        type("Req", (), {"get": staticmethod(lambda *a, **k: _Resp(payload))})())


def test_fetch_venue_parsa_last_e_chiusura(monkeypatch):
    _rete(monkeypatch, {"bid": 54.79, "ask": "54,80",
                        "last": 54.82, "close": 54.38})
    assert pu._fetch_venue("ALFA.DE", "EUR") == {
        "price": 54.82, "currency": "EUR", "venue_close": 54.38, "venue_close_date": None}


def test_fetch_venue_senza_last_usa_il_medio_bid_ask(monkeypatch):
    _rete(monkeypatch, {"bid": "54,80", "ask": 54.90,
                        "last": None, "close": "54,38"})
    out = pu._fetch_venue("ALFA.DE", "EUR")
    assert out["price"] == pytest.approx((54.80 + 54.90) / 2)
    assert out["venue_close"] == pytest.approx(54.38)


def test_fetch_venue_parsa_le_migliaia_tedesche(monkeypatch):
    _rete(monkeypatch, {"bid": "1.046,80", "ask": "1.047,20",
                        "last": "1.046,40", "close": 1000})
    assert pu._fetch_venue("ALFA.DE", "EUR")["price"] == pytest.approx(1046.40)


def test_fetch_venue_none_su_sconosciuto_vuoto_errore(monkeypatch):
    assert pu._fetch_venue("ZZZZ.XX", "EUR") is None
    _rete(monkeypatch, {})
    assert pu._fetch_venue("ALFA.DE", "EUR") is None

    def _boom(*a, **k):
        raise ConnectionError("sede giu'")
    monkeypatch.setattr(
        pu, "requests", type("Req", (), {"get": staticmethod(_boom)})())
    assert pu._fetch_venue("ALFA.DE", "EUR") is None


def test_giro_sede_scrive_snapshot_e_chiusura(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pu, "prezzi_speciali",
        lambda: {"prezzi": {"coingecko": {}}, "origine": "fixture", "motivo": None})
    _rete(monkeypatch, {"bid": 54.79, "ask": 54.84,
                        "last": 54.82, "close": 54.38})

    scritte = []
    chiusure = []
    # updated>0 chiama record_nav_snapshot(db): senza db_path cadrebbe sul DB
    # di produzione (tripwire). Con un tmp fa no-op dichiarato (niente tabella).
    tmp_db = str(tmp_path / "giro.db")

    class DB:
        db_path = tmp_db

        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "ALFA.DE", "valuta": "EUR",
                                   "prezzo_medio": 50.0}]}

        def update_price(self, ticker, prezzo, valuta=None, source=None):
            scritte.append((ticker, prezzo, valuta, source))

        def save_venue_close(self, ticker, prezzo, source=None, *, data_sessione):
            chiusure.append((ticker, prezzo, source, data_sessione))

    out = pu.update_all_prices(DB(), source_order=("tradegate",), verbose=False)
    assert out["updated"] == 1 and out["failed"] == 0
    assert scritte == [("ALFA.DE", 54.82, "EUR", "tradegate")]
    # 04/10: la sede non data `close` (campo non sondato): niente baseline, detto
    assert chiusure == []
    assert "non datata" in out["details"][0]["venue_close_nota"]

    # con il campo data noto, la chiusura entra con la SUA sessione
    monkeypatch.setattr(pu, "VENUE_CLOSE_DATE_FIELD", "dataSessione")
    _rete(monkeypatch, {"last": 54.82, "close": 54.38, "dataSessione": "16.09.2026"})
    pu.update_all_prices(DB(), source_order=("tradegate",), verbose=False)
    assert chiusure == [("ALFA.DE", 54.38, "tradegate", "2026-09-16")]


SCHEMA_POS = """
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, nome TEXT,
    quantita REAL, prezzo_medio REAL, valuta TEXT, data_apertura TEXT,
    tesi TEXT, temi_monitoraggio TEXT, note TEXT, last_updated TEXT,
    is_active INTEGER DEFAULT 1)
"""
SCHEMA_PRICES = """
CREATE TABLE position_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, prezzo REAL,
    valuta TEXT, source TEXT, timestamp TEXT)
"""


def test_prev_close_usa_la_chiusura_salata_in_giornata(tmp_path):
    """La riga di sede salvata OGGI porta la chiusura di IERI (la sede non
    aggiorna `close` in intraday): e' eleggibile per i prezzi di oggi."""
    from bellomberg.storage.memory_db import MemoryDB
    path = str(tmp_path / "sede2.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA_POS)
    con.execute(SCHEMA_PRICES)
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,"
                "data_apertura,is_active) VALUES "
                "('ALFA.DE','Alfa',10,50.0,'EUR','2026-09-01T12:00:00',1)")
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,"
                    "timestamp) VALUES (?,?,?,?,?)", [
                        ("ALFA.DE", 55.00, "EUR", "yfinance", "2026-09-16 18:30:00"),
                        ("ALFA.DE", 54.82, "EUR", "tradegate", "2026-09-17 14:00:00"),
                    ])
    con.commit()
    con.close()
    db = MemoryDB(path)
    # 04/10: la riga salvata OGGI vale per la sessione che la SEDE dichiara
    # (qui ieri), non per l'ipotesi «close in intraday = ieri».
    db.save_venue_close("ALFA.DE", 54.38, source="tradegate", data_sessione="2026-09-16")
    con = sqlite3.connect(path)
    con.execute("UPDATE venue_closes SET timestamp='2026-09-17 10:00:00' "
                "WHERE ticker='ALFA.DE'")
    con.commit()
    con.close()
    righe = [p for p in db.get_portfolio(now="2026-09-17 15:00:00")
             if p["ticker"] == "ALFA.DE"]
    assert righe[0]["prev_close"] == pytest.approx(54.38)
    assert righe[0]["prev_close_source"] == "tradegate"
    assert righe[0]["prev_close_ts"] == "2026-09-16"


def test_prev_close_preferisce_la_chiusura_di_sede(tmp_path):
    from bellomberg.storage.memory_db import MemoryDB
    path = str(tmp_path / "sede.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA_POS)
    con.execute(SCHEMA_PRICES)
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,"
                "data_apertura,is_active) VALUES "
                "('ALFA.DE','Alfa',10,50.0,'EUR','2026-09-01T12:00:00',1)")
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,"
                    "timestamp) VALUES (?,?,?,?,?)", [
                        ("ALFA.DE", 55.00, "EUR", "yfinance", "2026-09-16 18:30:00"),
                        ("ALFA.DE", 54.82, "EUR", "tradegate", "2026-09-17 14:00:00"),
                    ])
    con.commit()
    con.close()
    db = MemoryDB(path)
    db.save_venue_close("ALFA.DE", 54.38, source="tradegate", data_sessione="2026-09-16")
    con = sqlite3.connect(path)
    con.execute("UPDATE venue_closes SET timestamp='2026-09-16 23:00:00' "
                "WHERE ticker='ALFA.DE'")
    con.commit()
    con.close()
    righe = [p for p in db.get_portfolio(now="2026-09-17 15:00:00")
             if p["ticker"] == "ALFA.DE"]
    assert righe[0]["prev_close"] == pytest.approx(54.38)
    assert righe[0]["prev_close_source"] == "tradegate"
