"""PNL-B (06/10/2026, Opus 5.5) — tools/ops/registra_dividendi_yahoo.py.
Dati SINTETICI (QQSYN.MI, ZZTEST.L, importi inventati), DB in tmp_path, BCE e Yahoo finti."""
import hashlib
import importlib.util
import os
import sqlite3

import pandas as pd
import pytest

from bellomberg.storage import memory_db

_RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def mod():
    # PNLB_SCRIPT: copia mutata del banco di mutazioni (mai il file vero)
    percorso = os.environ.get("PNLB_SCRIPT") or os.path.join(_RADICE, "tools", "ops", "registra_dividendi_yahoo.py")
    spec = importlib.util.spec_from_file_location("registra_dividendi_yahoo", percorso)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _bce(valori):
    def scarica(url):
        giorno = url.split("startPeriod=")[1][:10]
        valuta = url.split("EXR/D.")[1][:3]
        if (valuta, giorno) not in valori:
            return "KEY,TIME_PERIOD,OBS_VALUE\n"
        return f"KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.{valuta}.EUR,{giorno},{valori[(valuta, giorno)]}\n"
    return scarica


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "finto.db")
    d = memory_db.MemoryDB(db_path=path)
    d.log_trade("QQSYN.MI", "BUY", 10, 100.0, "EUR", data="2026-03-02")
    d.log_trade("ZZTEST.L", "BUY", 200, 500.0, "GBX", data="2026-03-02")
    d.log_trade("QQSYN.MI", "DIVIDEND", 10, 1.0, "EUR", data="2026-05-06")
    import bellomberg.portfolio.portfolio_analytics as pa
    from types import SimpleNamespace
    monkeypatch.setattr(pa, "_currency_labels_for_tickers", lambda tks, trades, negozio=None: {
        "QQSYN.MI": SimpleNamespace(valore="EUR"), "ZZTEST.L": SimpleNamespace(valore="GBX")})
    monkeypatch.setattr(pa, "prezzi_speciali", lambda: {"prezzi": {"senza_yfinance": frozenset()}})
    return path


def _prezzi(div):
    df = pd.DataFrame({"QQSYN.MI": [100.0]}, index=pd.to_datetime(["2026-03-02"]))
    df.attrs["dividendi"] = div
    return lambda *a, **k: df


_DIV = {"QQSYN.MI": [("2026-09-15", 2.0), ("2026-05-04", 1.0)], "ZZTEST.L": [("2026-03-12", 5.0)]}


def test_prepara_righe_nello_stesso_formato_con_cambio_bce_della_data_di_stacco(mod, db):
    trades = mod._leggi_trade(db)
    pronte, rifiutate, motivo = mod.prepara(trades, scarica_bce=_bce({("GBP", "2026-03-12"): "0.8"}),
                                            scarica_prezzi=_prezzi(_DIV))
    assert motivo is None and rifiutate == []
    per = {(r["ticker"], r["data"]): r for r in pronte}
    assert set(per) == {("QQSYN.MI", "2026-09-15"), ("ZZTEST.L", "2026-03-12")}   # 04/05 gia' registrato
    q = per[("QQSYN.MI", "2026-09-15")]
    assert (q["quantita"], q["prezzo"], q["valuta"], q["fx_fonte"], q["importo_eur"]) == (10, 2.0, "EUR", "identity", 20.0)
    z = per[("ZZTEST.L", "2026-03-12")]
    # 5 pence = 0,05 GBP / 0,8 GBP per EUR = 0,0625 EUR per azione x 200
    assert z["prezzo"] == pytest.approx(0.0625) and z["importo_eur"] == 12.5 and z["fx_fonte"] == "storico"
    assert "da Yahoo, non da estratto broker" in z["note"] and "BCE del 2026-03-12" in z["note"]
    assert "LORDI" in z["note"]


def test_stacco_vicino_a_una_registrazione_esistente_e_un_possibile_duplicato(mod, db):
    # registrato a mano il 06/05 (incasso); Yahoo ha anche uno stacco il 10/03, fuori dalla finestra
    # di abbinamento (57 giorni) ma entro 120: possibile duplicato, MAI proposto
    div = {"QQSYN.MI": [("2026-03-10", 2.0), ("2026-05-04", 1.0)]}
    pronte, rifiutate, _ = mod.prepara(mod._leggi_trade(db), scarica_bce=_bce({}), scarica_prezzi=_prezzi(div))
    assert pronte == []
    assert rifiutate[0]["data"] == "2026-03-10" and "possibile duplicato" in rifiutate[0]["motivo"]
    assert "2026-05-06" in rifiutate[0]["motivo"]


def test_output_dichiara_lordo_e_cambio_di_oggi(mod, db, monkeypatch, capsys):
    monkeypatch.setattr(mod, "prepara", lambda trades: ([], [], None))
    assert mod.main(["--db", db]) == 0
    out = capsys.readouterr().out
    assert "LORDO" in out and "NETTO" in out and "cambio di oggi" in out


def _csv(tmp_path, righe):
    f = tmp_path / "estratto.csv"
    f.write_text("ticker,data,importo_netto_eur,azioni" + chr(10) + chr(10).join(righe) + chr(10), encoding="utf-8")
    return str(f)


def test_importi_netti_dall_estratto_alla_data_di_incasso(mod, db, tmp_path):
    f = _csv(tmp_path, ["ZZTEST.L,2026-04-02,\"9,60\",200", "QQSYN.MI,2026-05-08,7.4,10",
                        "ZZTEST.L,2026-04-05,9.6,200", "QQSYN.MI,2026-09-30,abc,10"])
    pronte, rifiutate = mod.leggi_importi(f, mod._leggi_trade(db))
    assert [(r["ticker"], r["data"], r["importo_eur"], r["prezzo"]) for r in pronte] == [("ZZTEST.L", "2026-04-02", 9.6, 0.048)]
    assert all("importo NETTO da estratto broker" in r["note"] for r in pronte)
    motivi = {(x["ticker"], x["data"]): x["motivo"] for x in rifiutate}
    assert "possibile duplicato" in motivi[("QQSYN.MI", "2026-05-08")]       # registrato il 06/05
    assert "possibile duplicato" in motivi[("ZZTEST.L", "2026-04-05")]       # stessa riga del file, 3 giorni
    assert ("QQSYN.MI", "2026-09-30") in motivi


def test_importi_in_dry_run_non_scrivono(mod, db, tmp_path):
    f = _csv(tmp_path, ["ZZTEST.L,2026-04-02,9.6,200"])
    n = mod._conta(db)
    assert mod.main(["--db", db, "--importi", f]) == 0 and mod._conta(db) == n


def test_senza_cambio_bce_del_giorno_la_riga_non_si_registra(mod, db):
    pronte, rifiutate, _ = mod.prepara(mod._leggi_trade(db), scarica_bce=_bce({}), scarica_prezzi=_prezzi(_DIV))
    assert [r["ticker"] for r in pronte] == ["QQSYN.MI"]
    assert rifiutate[0]["ticker"] == "ZZTEST.L" and "BCE" in rifiutate[0]["motivo"]


def test_senza_dividendi_yahoo_si_ferma_dichiarando(mod, db):
    pronte, _, motivo = mod.prepara(mod._leggi_trade(db), scarica_bce=_bce({}), scarica_prezzi=_prezzi(None))
    assert pronte is None and "NON eseguita" in motivo


def test_dry_run_non_scrive_niente_misurato(mod, db, monkeypatch):
    monkeypatch.setattr(mod, "prepara", lambda trades: (
        [{"ticker": "QQSYN.MI", "action": "DIVIDEND", "quantita": 10, "prezzo": 2.0, "valuta": "EUR",
          "data": "2026-03-10", "fx_fonte": "identity", "importo_eur": 20.0, "note": "n"}], [], None))
    prima = hashlib.sha256(open(db, "rb").read()).hexdigest()
    n = mod._conta(db)
    assert mod.main(["--db", db]) == 0
    assert mod._conta(db) == n and hashlib.sha256(open(db, "rb").read()).hexdigest() == prima


def test_apply_rifiuta_con_la_porta_occupata(mod, db, monkeypatch):
    monkeypatch.setattr(mod, "_porta_8765_occupata", lambda: True)
    n = mod._conta(db)
    assert mod.main(["--db", db, "--apply"]) == 2 and mod._conta(db) == n


def test_apply_scrive_con_backup_e_conteggio(mod, db, monkeypatch):
    riga = {"ticker": "QQSYN.MI", "action": "DIVIDEND", "quantita": 10, "prezzo": 2.0, "valuta": "EUR",
            "data": "2026-03-10", "fx_fonte": "identity", "importo_eur": 20.0, "note": "nota finta"}
    monkeypatch.setattr(mod, "_porta_8765_occupata", lambda: False)
    monkeypatch.setattr(mod, "prepara", lambda trades: ([riga], [], None))
    n = mod._conta(db)
    assert mod.main(["--db", db, "--apply"]) == 0
    assert mod._conta(db) == n + 1
    assert any(f.startswith("finto.db.pre_dividendi_yahoo_") for f in os.listdir(os.path.dirname(db)))
    con = sqlite3.connect(db)
    r = con.execute("SELECT ticker, action, quantita, prezzo, valuta, data, note, fx_fonte FROM trade_history "
                    "ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    assert r == ("QQSYN.MI", "DIVIDEND", 10.0, 2.0, "EUR", "2026-03-10T12:00:00", "nota finta", "identity")


def test_apply_esce_in_errore_se_il_conteggio_non_torna(mod, db, monkeypatch):
    riga = {"ticker": "QQSYN.MI", "action": "DIVIDEND", "quantita": 10, "prezzo": 2.0, "valuta": "EUR",
            "data": "2026-03-10", "fx_fonte": "identity", "importo_eur": 20.0, "note": "n"}
    monkeypatch.setattr(mod, "_porta_8765_occupata", lambda: False)
    monkeypatch.setattr(mod, "prepara", lambda trades: ([riga], [], None))
    monkeypatch.setattr(memory_db.MemoryDB, "log_trade", lambda self, **kw: 1)     # finge di scrivere
    assert mod.main(["--db", db, "--apply"]) == 1


def test_cambio_bce_solo_del_giorno_esatto(mod):
    def scarica(url):
        return "KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.USD.EUR,2026-03-11,1.2\n"   # giorno diverso
    valore, motivo = mod.cambio_bce("USD", "2026-03-12", scarica)
    assert valore is None and "2026-03-12" in motivo
    valore, _ = mod.cambio_bce("USD", "2026-03-11", scarica)
    assert valore == 1.2
