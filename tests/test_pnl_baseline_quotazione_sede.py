"""Baseline P&L GG = quotazione di sede a chiusura (02/10, confronto broker).

Il `close` di sede e' l'ultimo scambio eseguito: sui titoli poco liquidi puo'
essere di ore prima della chiusura. Se c'e' uno snapshot di sede del giorno
precedente preso dopo le 22:00 di Berlino, la baseline e' quello; senza
(updater spento la sera) resta il `close` di sede.
"""
import sqlite3

import pytest

from test_venue_prices import SCHEMA_POS, SCHEMA_PRICES


def _db(tmp_path, snapshots):
    from bellomberg.storage.memory_db import MemoryDB
    path = str(tmp_path / "baseline.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA_POS)
    con.execute(SCHEMA_PRICES)
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,"
                "data_apertura,is_active) VALUES "
                "('BETA.FRA','Beta',77,10.0,'EUR','2026-09-01T12:00:00',1)")
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,"
                    "timestamp) VALUES ('BETA.FRA',?,'EUR',?,?)", snapshots)
    con.commit()
    con.close()
    db = MemoryDB(path)
    db.save_venue_close("BETA.FRA", 10.7, source="tradegate", data_sessione="2026-10-01")
    con = sqlite3.connect(path)
    con.execute("UPDATE venue_closes SET timestamp='2026-10-02 07:40:00'")
    con.commit()
    con.close()
    righe = [p for p in db.get_portfolio(now="2026-10-02 07:45:00")
             if p["ticker"] == "BETA.FRA"]
    return righe[0]


def test_snapshot_a_sede_chiusa_vince_sul_close(tmp_path):
    riga = _db(tmp_path, [
        (10.7, "tradegate", "2026-10-01 15:00:00"),
        (10.6, "tradegate", "2026-10-01 20:30:00"),   # 22:30 Berlino (CEST)
        (10.9, "tradegate", "2026-10-02 07:40:00"),
    ])
    assert riga["prev_close"] == pytest.approx(10.6)
    assert riga["prev_close_source"] == "tradegate"
    assert riga["prev_close_ts"] == "2026-10-01 20:30:00"


def test_senza_snapshot_serale_resta_il_close_di_sede(tmp_path):
    riga = _db(tmp_path, [
        (10.6, "tradegate", "2026-10-01 15:00:00"),   # 17:00 Berlino: sede aperta
        (10.9, "tradegate", "2026-10-02 07:40:00"),
    ])
    assert riga["prev_close"] == pytest.approx(10.7)


def test_snapshot_serale_di_altra_fonte_non_conta(tmp_path):
    riga = _db(tmp_path, [
        (10.5, "yfinance", "2026-10-01 21:00:00"),
        (10.9, "tradegate", "2026-10-02 07:40:00"),
    ])
    assert riga["prev_close"] == pytest.approx(10.7)


@pytest.mark.parametrize("ts,atteso", [
    ("2026-10-01 20:00:00", True),    # 22:00 CEST
    ("2026-10-01 19:59:00", False),
    ("2026-12-01 20:30:00", False),   # 21:30 CET: sede ancora aperta
    ("2026-12-01 21:00:00", True),
    ("2026-10-01 22:30:00", True),    # gia' domani a Berlino
    ("illeggibile", False),
])
def test_a_sede_chiusa(ts, atteso):
    from bellomberg.storage.memory_db import MemoryDB
    assert MemoryDB._a_sede_chiusa(ts) is atteso
