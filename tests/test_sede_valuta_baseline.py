# -*- coding: utf-8 -*-
"""Sede retail (Tradegate): valuta, ripiego dichiarato, baseline datata, frequenza.

Revisione 04/10 del commit dei prezzi di sede:
  P1-A  la sede quota in EUR: un prezzo di sede non entra MAI su una posizione
        in un'altra valuta (rifiuto dichiarato, nessuna conversione);
  P1-B  ogni ripiego sede -> fonte successiva e' DICHIARATO (source_warnings e
        log anche con --quiet);
  P1-C  una chiusura di sede vecchia non batte lo snapshot di ieri;
  P2-D/E la chiusura vale solo se la SEDE la data; `prev_close_ts` = sessione;
  P2-G  intervallo minimo per ISIN e pausa crescente dopo un errore;
  rischio: blocco benchmark assente con motivo, valuta dichiarata.
Ticker e numeri inventati.
"""
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

import bellomberg.cli.price_updater as pu
from test_venue_prices import SCHEMA_POS, SCHEMA_PRICES

ISIN_ALFA = "DE000ALFA001"
ISIN_ZZ = "US0ZZTEST001"
ISIN_GAMMA = "DE000GAMMA01"


@pytest.fixture(autouse=True)
def _ambiente(tmp_path, monkeypatch):
    from bellomberg.storage import negozi_privati
    percorso = tmp_path / "alias_fonti.json"
    percorso.write_text(json.dumps({"tradegate": {"ALFA.DE": ISIN_ALFA, "ZZTEST": ISIN_ZZ,
                                              "GAMMA.DE": ISIN_GAMMA}}),
                        encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(percorso))
    monkeypatch.setattr(pu, "_VENUE_STATO", {"ultima_richiesta": {}, "errori_consecutivi": 0,
                                             "pausa_fino": None, "ultimo_errore": None},
                        raising=False)
    monkeypatch.setenv("VENUE_POLL_MIN_SECONDS", "0")
    monkeypatch.delenv("VENUE_BACKOFF_MAX_SECONDS", raising=False)
    monkeypatch.setattr(
        pu, "prezzi_speciali",
        lambda: {"prezzi": {"coingecko": {}}, "origine": "fixture", "motivo": None})


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Rete:
    """requests finto che CONTA le richieste alla sede."""

    def __init__(self, payload=None, errore=None):
        self.payload, self.errore, self.chiamate = payload, errore, []

    def get(self, url, params=None, **k):
        self.chiamate.append(params.get("isin"))
        if self.errore is not None:
            raise self.errore
        return _Resp(self.payload)


def _rete(monkeypatch, **k):
    rete = _Rete(**k)
    monkeypatch.setattr(pu, "requests", rete)
    return rete


def _giro(tmp_path, posizioni, source_order=("tradegate", "yfinance")):
    scritte, chiusure = [], []

    class DB:
        db_path = str(tmp_path / "giro.db")   # record_nav_snapshot: no-op dichiarato

        def get_portfolio_summary(self):
            return {"positions": posizioni}

        def update_price(self, ticker, prezzo, valuta=None, source=None):
            scritte.append((ticker, prezzo, valuta, source))

        def save_venue_close(self, ticker, prezzo, source=None, *, data_sessione):
            chiusure.append((ticker, prezzo, source, data_sessione))

    out = pu.update_all_prices(DB(), source_order=source_order, verbose=False)
    return out, scritte, chiusure


def _yf(monkeypatch, prezzo=180.0):
    chiamate = []

    def finto(ticker, valuta_posizione=None):
        chiamate.append(ticker)
        return (prezzo, None, None)
    monkeypatch.setattr(pu, "_fetch_yfinance", finto)
    return chiamate


# ---------------------------------------------------------------- P1-A valuta

def test_posizione_usd_mappata_in_sede_non_prende_mai_il_prezzo_eur(monkeypatch, tmp_path):
    rete = _rete(monkeypatch, payload={"last": 100.0, "close": 99.0})
    _yf(monkeypatch, 180.0)
    out, scritte, chiusure = _giro(tmp_path, [{"ticker": "ZZTEST", "valuta": "USD",
                                                "prezzo_medio": 150.0}])
    assert rete.chiamate == []                       # la sede non viene nemmeno interrogata
    assert scritte == [("ZZTEST", 180.0, "USD", "yfinance")]
    assert chiusure == []
    avvisi = " ".join(out["details"][0]["source_warnings"])
    assert "rifiutata" in avvisi and "EUR" in avvisi and "USD" in avvisi


def test_posizione_usd_senza_altre_fonti_resta_senza_prezzo_e_lo_dice(monkeypatch, tmp_path):
    _rete(monkeypatch, payload={"last": 100.0})
    out, scritte, _ = _giro(tmp_path, [{"ticker": "ZZTEST", "valuta": "USD"}],
                            source_order=("tradegate",))
    assert scritte == [] and out["failed"] == 1
    assert "rifiutata" in out["details"][0]["error"]


def test_seconda_cintura_prezzo_di_sede_mai_scritto_con_altra_valuta(monkeypatch, tmp_path):
    """Anche se un giorno la sede rendesse un prezzo per una posizione USD,
    lo scrittore non lo etichetta USD."""
    monkeypatch.setattr(pu, "_interroga_sede", lambda t, v: (
        "ok", {"price": 100.0, "currency": "EUR", "venue_close": None,
               "venue_close_date": None}, None))
    out, scritte, _ = _giro(tmp_path, [{"ticker": "ZZTEST", "valuta": "USD"}],
                            source_order=("tradegate",))
    assert scritte == []
    assert out["failed"] == 1 and "EUR" in out["details"][0]["error"]


def test_posizione_eur_mappata_usa_la_sede(monkeypatch, tmp_path):
    _rete(monkeypatch, payload={"last": 54.82, "close": 54.38})
    yf = _yf(monkeypatch)
    out, scritte, _ = _giro(tmp_path, [{"ticker": "ALFA.DE", "valuta": "EUR"}])
    assert scritte == [("ALFA.DE", 54.82, "EUR", "tradegate")]
    assert yf == [] and "source_warnings" not in out["details"][0]


def test_coingecko_in_usd_su_posizione_eur_non_scritto(monkeypatch, tmp_path):
    """Revisione R-5: la cintura vale per ogni fonte che MISURA la valuta."""
    monkeypatch.setattr(pu, "_fetch_coingecko", lambda t, m: (100.0, "USD"))
    out, scritte, _ = _giro(tmp_path, [{"ticker": "KRYPTO", "valuta": "EUR"}],
                            source_order=("coingecko",))
    assert scritte == [] and out["failed"] == 1
    assert "coingecko in USD" in out["details"][0]["error"]


def test_coingecko_in_usd_su_posizione_usd_invariato(monkeypatch, tmp_path):
    monkeypatch.setattr(pu, "_fetch_coingecko", lambda t, m: (100.0, "USD"))
    out, scritte, _ = _giro(tmp_path, [{"ticker": "KRYPTO", "valuta": "USD"}],
                            source_order=("coingecko",))
    assert scritte == [("KRYPTO", 100.0, "USD", "coingecko")] and out["failed"] == 0


def test_ibkr_in_usd_su_posizione_eur_non_scritto(monkeypatch, tmp_path):
    monkeypatch.setattr(pu, "_fetch_ibkr", lambda t, port=7496: (100.0, "USD"))
    out, scritte, _ = _giro(tmp_path, [{"ticker": "ACME", "valuta": "EUR"}],
                            source_order=("ibkr",))
    assert scritte == [] and "ibkr in USD" in out["details"][0]["error"]


def test_yfinance_non_confronta_la_sua_etichetta_di_classificazione(monkeypatch, tmp_path):
    """La valuta nella tupla di yfinance e' la NOSTRA classificazione, non una misura
    di Yahoo: il comportamento con yfinance non cambia."""
    monkeypatch.setattr(pu, "_fetch_yfinance", lambda t, valuta_posizione=None: (9.0, "GBX", None))
    out, scritte, _ = _giro(tmp_path, [{"ticker": "BETA.L", "valuta": "GBP"}],
                            source_order=("yfinance",))
    assert scritte == [("BETA.L", 9.0, "GBP", "yfinance")]


def test_nessuna_posizione_stessa_forma_del_risultato(tmp_path):
    out, _, _ = _giro(tmp_path, [])
    assert out["skipped"] == 0 and "venue" in out and "timestamp" in out


# ---------------------------------------------------------- P1-B ripiego

def test_sede_giu_ripiego_su_yfinance_dichiarato_anche_in_quiet(monkeypatch, tmp_path, capsys):
    _rete(monkeypatch, errore=ConnectionError("sede giu'"))
    _yf(monkeypatch, 55.0)
    out, scritte, _ = _giro(tmp_path, [{"ticker": "ALFA.DE", "valuta": "EUR"}])
    assert scritte == [("ALFA.DE", 55.0, "EUR", "yfinance")]
    avvisi = " ".join(out["details"][0]["source_warnings"])
    assert "sede KO" in avvisi and "ConnectionError" in avvisi and "ripiego" in avvisi
    assert "[SEDE KO] ALFA.DE" in capsys.readouterr().out


def test_risposta_vuota_ripiego_dichiarato(monkeypatch, tmp_path):
    _rete(monkeypatch, payload={})
    _yf(monkeypatch, 55.0)
    out, _, _ = _giro(tmp_path, [{"ticker": "ALFA.DE", "valuta": "EUR"}])
    assert "risposta vuota" in " ".join(out["details"][0]["source_warnings"])


def test_negozio_alias_illeggibile_dichiarato(monkeypatch, tmp_path):
    from bellomberg.storage import negozi_privati
    rotto = tmp_path / "alias_rotto.json"
    rotto.write_text(json.dumps({"tradegate": {"ALFA.DE": "non-un-isin"}}), encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(rotto))
    _rete(monkeypatch, payload={"last": 1.0})
    _yf(monkeypatch, 55.0)
    out, scritte, _ = _giro(tmp_path, [{"ticker": "ALFA.DE", "valuta": "EUR"}])
    assert scritte and scritte[0][3] == "yfinance"
    assert "illeggibile" in " ".join(out["details"][0]["source_warnings"])


def test_ticker_fuori_mappa_nessun_avviso(monkeypatch, tmp_path):
    _rete(monkeypatch, payload={"last": 1.0})
    _yf(monkeypatch, 12.0)
    out, scritte, _ = _giro(tmp_path, [{"ticker": "BETA.DE", "valuta": "EUR"}])
    assert scritte == [("BETA.DE", 12.0, "EUR", "yfinance")]
    assert "source_warnings" not in out["details"][0]


# ---------------------------------------------------------- P2-G frequenza

class _Orologio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_pausa_crescente_dopo_errore_senza_richieste(monkeypatch):
    monkeypatch.setenv("VENUE_POLL_MIN_SECONDS", "60")
    orologio = _Orologio()
    monkeypatch.setattr(pu.time, "monotonic", orologio)
    rete = _rete(monkeypatch, errore=TimeoutError("lenta"))
    esito, _, motivo = pu._interroga_sede("ALFA.DE", "EUR")
    assert esito == "ko" and "pausa 60s" in motivo
    esito, _, motivo = pu._interroga_sede("ALFA.DE", "EUR")
    assert esito == "ko" and "in pausa" in motivo
    assert len(rete.chiamate) == 1                    # nessuna richiesta in pausa
    orologio.t += 61
    esito, _, motivo = pu._interroga_sede("ALFA.DE", "EUR")
    assert len(rete.chiamate) == 2 and "pausa 120s" in motivo   # raddoppia
    rete.errore, rete.payload = None, {"last": 10.0}
    orologio.t += 121
    assert pu._interroga_sede("ALFA.DE", "EUR")[0] == "ok"
    assert pu._VENUE_STATO["errori_consecutivi"] == 0


def test_pausa_ha_un_tetto(monkeypatch):
    monkeypatch.setenv("VENUE_POLL_MIN_SECONDS", "60")
    monkeypatch.setenv("VENUE_BACKOFF_MAX_SECONDS", "100")
    monkeypatch.setattr(pu.time, "monotonic", _Orologio())
    monkeypatch.setitem(pu._VENUE_STATO, "errori_consecutivi", 50)
    _rete(monkeypatch, errore=TimeoutError("lenta"))
    assert "pausa 100s" in pu._interroga_sede("ALFA.DE", "EUR")[2]


def test_intervallo_minimo_salta_il_ticker_senza_ripiego(monkeypatch, tmp_path):
    monkeypatch.setenv("VENUE_POLL_MIN_SECONDS", "120")
    orologio = _Orologio()
    monkeypatch.setattr(pu.time, "monotonic", orologio)
    rete = _rete(monkeypatch, payload={"last": 54.82})
    yf = _yf(monkeypatch)
    pos = [{"ticker": "ALFA.DE", "valuta": "EUR"}]
    out1, scritte1, _ = _giro(tmp_path, pos)
    orologio.t += 60
    out2, scritte2, _ = _giro(tmp_path, pos)
    assert len(rete.chiamate) == 1 and yf == []      # niente sede, niente yfinance
    assert scritte1 and scritte2 == []
    assert out2["skipped"] == 1 and out2["failed"] == 0
    assert "minimo 120s" in out2["details"][0]["skipped"]
    assert out2["venue"]["poll_min_seconds"] == 120
    orologio.t += 61
    _giro(tmp_path, pos)
    assert len(rete.chiamate) == 2


@pytest.mark.parametrize("nome", ["VENUE_POLL_MIN_SECONDS", "VENUE_BACKOFF_MAX_SECONDS"])
@pytest.mark.parametrize("valore", ["", "  ", "ogni tanto", "-5", "nan"])
def test_frequenza_presente_ma_invalida_e_errore_col_nome(monkeypatch, tmp_path, capsys,
                                                           nome, valore):
    """Revisione R-1: presente ma vuota/invalida NON vale default. La sede non si
    interroga, ogni ticker mappato lo dichiara, yfinance resta etichettato."""
    monkeypatch.setenv(nome, valore)
    with pytest.raises(ValueError, match=nome):
        pu.venue_config()
    rete = _rete(monkeypatch, payload={"last": 54.82})
    _yf(monkeypatch, 55.0)
    out, scritte, _ = _giro(tmp_path, [{"ticker": "ALFA.DE", "valuta": "EUR"}])
    assert rete.chiamate == []
    assert scritte == [("ALFA.DE", 55.0, "EUR", "yfinance")]
    assert nome in " ".join(out["details"][0]["source_warnings"])
    assert nome in out["venue"]["errore"]
    assert "[SEDE KO] ALFA.DE" in capsys.readouterr().out


def test_frequenza_assente_vale_default(monkeypatch):
    monkeypatch.delenv("VENUE_POLL_MIN_SECONDS", raising=False)
    monkeypatch.delenv("VENUE_BACKOFF_MAX_SECONDS", raising=False)
    cfg = pu.venue_config()
    assert cfg["poll_min_seconds"] == pu.VENUE_POLL_MIN_SECONDS_DEFAULT
    assert cfg["backoff_max_seconds"] == pu.VENUE_BACKOFF_MAX_SECONDS_DEFAULT


class _HTTPErrore(Exception):
    def __init__(self, status):
        super().__init__("HTTP %d" % status)
        self.response = type("R", (), {"status_code": status})()


class _RetePerIsin(_Rete):
    """Un ISIN risponde con errore HTTP, gli altri con un prezzo."""

    def __init__(self, isin_rotto, status, payload):
        super().__init__(payload=payload)
        self.isin_rotto, self.status = isin_rotto, status

    def get(self, url, params=None, **k):
        self.chiamate.append(params.get("isin"))
        if params.get("isin") == self.isin_rotto:
            raise _HTTPErrore(self.status)
        return _Resp(self.payload)


def test_errore_di_un_isin_non_ferma_la_sede_per_gli_altri(monkeypatch, tmp_path):
    """Revisione R-2: un 404 su un ISIN mette in pausa solo quell'ISIN."""
    monkeypatch.setattr(pu.time, "monotonic", _Orologio())
    rete = _RetePerIsin(ISIN_ALFA, 404, {"last": 20.0})
    monkeypatch.setattr(pu, "requests", rete)
    _yf(monkeypatch, 19.0)
    pos = [{"ticker": "ALFA.DE", "valuta": "EUR"}, {"ticker": "GAMMA.DE", "valuta": "EUR"}]
    out, scritte, _ = _giro(tmp_path, pos)
    assert ("GAMMA.DE", 20.0, "EUR", "tradegate") in scritte
    assert ("ALFA.DE", 19.0, "EUR", "yfinance") in scritte
    assert "per questo ISIN" in " ".join(out["details"][0]["source_warnings"])
    assert pu._VENUE_STATO["pausa_fino"] is None
    # secondo giro subito: ALFA resta in pausa (nessuna richiesta), GAMMA ok
    out2, _, _ = _giro(tmp_path, pos)
    assert rete.chiamate.count(ISIN_ALFA) == 1
    assert "in pausa" in " ".join(out2["details"][0]["source_warnings"])


@pytest.mark.parametrize("errore", [ConnectionError("giu'"), _HTTPErrore(429)])
def test_errore_di_rete_o_429_ferma_tutta_la_sede(monkeypatch, errore):
    monkeypatch.setattr(pu.time, "monotonic", _Orologio())
    rete = _rete(monkeypatch, errore=errore)
    assert pu._interroga_sede("ALFA.DE", "EUR")[0] == "ko"
    esito, _, motivo = pu._interroga_sede("GAMMA.DE", "EUR")
    assert esito == "ko" and "sede in pausa" in motivo
    assert rete.chiamate == [ISIN_ALFA]


# --------------------------------------------- P1-C / P2-D baseline di sede

def _db(tmp_path, snapshots, ticker="ALFA.DE"):
    from bellomberg.storage.memory_db import MemoryDB
    path = str(tmp_path / "base.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA_POS)
    con.execute(SCHEMA_PRICES)
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,"
                "data_apertura,is_active) VALUES (?,'Alfa',10,50.0,'EUR',"
                "'2026-09-01T12:00:00',1)", (ticker,))
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,"
                    "timestamp) VALUES (?,?,'EUR',?,?)",
                    [(ticker,) + tuple(s) for s in snapshots])
    con.commit()
    con.close()
    return MemoryDB(path), path


def _riga(db, now="2026-09-17 15:00:00"):
    return [p for p in db.get_portfolio(now=now) if p["ticker"] == "ALFA.DE"][0]


SNAP = [(55.00, "yfinance", "2026-09-16 18:30:00"),
        (54.82, "tradegate", "2026-09-17 14:00:00")]


def test_chiusura_di_sede_vecchia_non_batte_lo_snapshot_di_ieri(tmp_path):
    db, _ = _db(tmp_path, SNAP)
    db.save_venue_close("ALFA.DE", 50.10, source="tradegate", data_sessione="2026-09-13")
    riga = _riga(db)
    assert riga["prev_close"] == pytest.approx(55.00)
    assert riga["prev_close_source"] == "position_prices"
    assert riga["prev_close_ts"] == "2026-09-16 18:30:00"
    assert "2026-09-13" in riga["prev_close_nota"] and "2026-09-16" in riga["prev_close_nota"]


def test_chiusura_della_sessione_giusta_vince_e_porta_la_sua_data(tmp_path):
    db, _ = _db(tmp_path, SNAP)
    db.save_venue_close("ALFA.DE", 54.38, source="tradegate", data_sessione="2026-09-16")
    riga = _riga(db)
    assert riga["prev_close"] == pytest.approx(54.38)
    assert riga["prev_close_ts"] == "2026-09-16"      # la sessione, non l'ora dell'INSERT
    assert riga["prev_close_nota"] is None


def test_chiusura_della_stessa_sessione_del_live_non_e_baseline(tmp_path):
    db, _ = _db(tmp_path, SNAP)
    db.save_venue_close("ALFA.DE", 54.90, source="tradegate", data_sessione="2026-09-17")
    riga = _riga(db)
    assert riga["prev_close"] == pytest.approx(55.00)
    assert riga["prev_close_source"] == "position_prices"


def test_snapshot_serale_di_sede_vecchio_non_batte_lo_snapshot_di_ieri(tmp_path):
    db, _ = _db(tmp_path, [(51.00, "tradegate", "2026-09-14 20:30:00"),   # 22:30 Berlino
                           (55.00, "yfinance", "2026-09-16 18:30:00"),
                           (54.82, "tradegate", "2026-09-17 14:00:00")])
    riga = _riga(db)
    assert riga["prev_close"] == pytest.approx(55.00)
    assert "2026-09-14" in riga["prev_close_nota"]


def test_chiusura_non_datata_rifiutata_in_scrittura(tmp_path):
    db, path = _db(tmp_path, SNAP)
    with pytest.raises(ValueError, match="non datata"):
        db.save_venue_close("ALFA.DE", 54.38, source="tradegate", data_sessione=None)
    with pytest.raises(TypeError):
        db.save_venue_close("ALFA.DE", 54.38, source="tradegate")
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM venue_closes").fetchone()[0] == 0
    con.close()


def test_stessa_chiusura_ripetuta_non_aggiunge_righe(tmp_path):
    db, path = _db(tmp_path, SNAP)
    for _ in range(3):
        db.save_venue_close("ALFA.DE", 54.38, source="tradegate", data_sessione="2026-09-16")
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM venue_closes").fetchone()[0] == 1
    con.close()


def test_tabella_vecchia_senza_sessione_dichiarata_e_poi_migrata(tmp_path):
    """Righe salvate prima del 04/10 (senza data di sessione) non sono baseline."""
    from bellomberg.storage.memory_db import MemoryDB
    path = str(tmp_path / "vecchia.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA_POS)
    con.execute(SCHEMA_PRICES)
    con.execute("CREATE TABLE venue_closes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ticker TEXT NOT NULL, prezzo REAL NOT NULL, source TEXT, "
                "timestamp TEXT DEFAULT (datetime('now')))")
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,"
                "data_apertura,is_active) VALUES ('ALFA.DE','Alfa',10,50.0,'EUR',"
                "'2026-09-01T12:00:00',1)")
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,timestamp) "
                    "VALUES ('ALFA.DE',?,'EUR',?,?)", SNAP)
    con.execute("INSERT INTO venue_closes (ticker,prezzo,source,timestamp) "
                "VALUES ('ALFA.DE',40.0,'tradegate','2026-09-17 10:00:00')")
    con.commit()
    con.close()
    db = MemoryDB(path)
    riga = _riga(db)
    assert riga["prev_close"] == pytest.approx(55.00)
    assert "illeggibili" in riga["prev_close_nota"]
    db.save_venue_close("ALFA.DE", 54.38, source="tradegate", data_sessione="2026-09-16")
    assert _riga(db)["prev_close"] == pytest.approx(54.38)


def test_gap_days_parte_dalla_sessione_della_chiusura(tmp_path, monkeypatch):
    """La finestra di gap_days nasce da min(prev_close_ts): con la data della
    sessione non collassa piu' su oggi."""
    db, _ = _db(tmp_path, [(55.00, "yfinance", "2026-09-14 18:30:00"),
                           (54.82, "tradegate", "2026-09-17 14:00:00")])
    db.save_venue_close("ALFA.DE", 54.38, source="tradegate", data_sessione="2026-09-16")
    riga = _riga(db)
    assert str(riga["prev_close_ts"])[:10] == "2026-09-16"


# ------------------------------------------- numeri storici intatti

def test_giro_di_sede_non_tocca_nav_snapshots_ne_prezzi_passati(tmp_path, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB
    path = str(tmp_path / "storia.db")
    db = MemoryDB(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,"
                "data_apertura,is_active) VALUES ('ALFA.DE','Alfa',10,50.0,'EUR',"
                "'2026-09-01T12:00:00',1)")
    # cassa misurata: senza, record_nav_snapshot esce prima di scrivere e il
    # test non vedrebbe nessuna scrittura (mutazione M17 non catturata)
    con.execute("INSERT INTO cash_state (singleton_id,balance_cents,updated_at,source) "
                "VALUES (1,23450,'2026-09-15T22:00:00','fixture')")
    con.executemany("INSERT INTO nav_snapshots (date,nav_total_eur,invested_eur,cash_eur,"
                    "source,created_at) VALUES (?,?,?,?,?,?)", [
                        ("2026-09-14", 1234.5, 1000.0, 234.5, "price_updater", "2026-09-14T22:00:00"),
                        ("2026-09-15", 1240.0, 1005.5, 234.5, "price_updater", "2026-09-15T22:00:00"),
                    ])
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,timestamp) "
                    "VALUES ('ALFA.DE',?,'EUR',?,?)",
                    [(50.5, "yfinance", "2026-09-15 18:30:00")])
    con.commit()
    prima_nav = con.execute("SELECT * FROM nav_snapshots ORDER BY date").fetchall()
    prima_px = con.execute("SELECT * FROM position_prices ORDER BY id").fetchall()
    con.close()
    monkeypatch.setattr(pu, "VENUE_CLOSE_DATE_FIELD", "dataSessione")
    _rete(monkeypatch, payload={"last": 54.82, "close": 54.38, "dataSessione": "2026-09-15"})
    out = pu.update_all_prices(db, source_order=("tradegate",), verbose=False)
    assert out["updated"] == 1
    db.get_portfolio()
    con = sqlite3.connect(path)
    dopo_nav = con.execute("SELECT * FROM nav_snapshots WHERE date IN "
                           "('2026-09-14','2026-09-15') ORDER BY date").fetchall()
    dopo_px = con.execute("SELECT * FROM position_prices ORDER BY id").fetchall()
    con.close()
    assert dopo_nav == prima_nav
    # il percorso di scrittura e' stato davvero percorso: c'e' la riga di OGGI
    con = sqlite3.connect(path)
    oggi = con.execute("SELECT COUNT(*) FROM nav_snapshots WHERE date NOT IN "
                       "('2026-09-14','2026-09-15')").fetchone()[0]
    con.close()
    assert oggi == 1
    assert dopo_px[:len(prima_px)] == prima_px and len(dopo_px) == len(prima_px) + 1


# ------------------------------------------------ rischio: benchmark

def _serie(n, start="2026-01-01", seed=3):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0, 0.01, n), index=pd.bdate_range(start, periods=n))


def test_benchmark_assente_porta_il_motivo():
    from bellomberg.portfolio.portfolio_risk import _benchmark_con_motivo
    port = _serie(60)
    blocco, motivo = _benchmark_con_motivo(port, pd.DataFrame({"X": port}), {})
    assert blocco is None and "SPY" in motivo
    corto = pd.DataFrame({"SPY": _serie(10)})
    blocco, motivo = _benchmark_con_motivo(port, corto, {"converted": ["SPY"]})
    assert blocco is None and "10" in motivo and "21" in motivo


def test_benchmark_dichiara_la_valuta():
    from bellomberg.portfolio.portfolio_risk import _benchmark_con_motivo
    port = _serie(60)
    rets = pd.DataFrame({"SPY": _serie(60, seed=4)})
    blocco, motivo = _benchmark_con_motivo(port, rets, {"converted": ["SPY"]})
    assert motivo is None and blocco["valuta"] == "EUR" and blocco["fx_nota"] is None
    blocco, _ = _benchmark_con_motivo(port, rets, {"converted": [], "local_declared": ["SPY (USD)"]})
    assert blocco["valuta"] == "USD" and "USD" in blocco["fx_nota"]


def test_beta_basis_coerente_con_la_valuta_del_benchmark():
    """Revisione R-6: beta_basis non dice «convertito in EUR» se SPY e' rimasto in USD."""
    from bellomberg.portfolio.portfolio_risk import _beta_basis
    assert "convertito in EUR" in _beta_basis({"converted": ["SPY"]}) or \
        "converted to EUR" in _beta_basis({"converted": ["SPY"]})
    usd = _beta_basis({"converted": []})
    assert "USD" in usd and "convertito in EUR" not in usd and "converted to EUR" not in usd
