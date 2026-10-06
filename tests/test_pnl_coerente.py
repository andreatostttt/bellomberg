"""PNL-B (06/10/2026, Opus 5.5) — rendimento di performance e P&L contabile coerenti.

Regole provate (decisioni del PM del 06/10 + revisione R-PNL):
- (C) il rendimento dall'inizio parte dal COSTO (punto base), non dalla prima chiusura;
- (B/F1) i dividendi registrati entrano UNA volta, MATURATI all'ex-date (abbinati 1:1 allo
  stacco Yahoo): nessun salto finto ne' allo stacco ne' all'incasso;
- (F2) drawdowns / charts_quant / advanced_metrics leggono la serie unica con i dividendi;
- (F8/F9) stacchi non registrati dichiarati con conteggio; una registrazione = uno stacco;
- (A) alla cucitura la base e' posizioni ricostruite + cassa dello snapshot (+ credito per
  dividendi maturati non incassati); nessun dividendo contato due volte o perso;
- (A2/F10) ffill limitato, niente bfill, feed vivo del giorno (anche al posto del prezzo
  fermo del ffill) dichiarato con la fonte; prezzi fermi senza feed dichiarati;
- (D/F3) riconciliazione: voci misurate, NESSUNA voce-tappo: la cassa non spiegata resta
  nel residuo, che ha uno stato (un versamento non registrato = «non_riconciliato»);
- (F4) l'attribuzione usa lo stesso ripiego dichiarato sul feed;
- (F7) IRR di nav_history: 100 -> 110 in un anno = +10%.

Dati SINTETICI: ticker QQSYN.MI / ZZTEST.MI, importi interi inventati. Zero rete, zero DB
vero (un DB sqlite in tmp_path dove serve).
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

import bellomberg.portfolio.portfolio_analytics as pa
import bellomberg.portfolio.twr_engine as twr
from bellomberg.storage import memory_db


# ------------------------------------------------------------------ banco TWR

class _DBFinto:
    def __init__(self, nav_live):
        self.nav_live = nav_live

    def get_opening_positions(self):
        return []

    def get_portfolio_summary(self):
        return {"nav_total_eur": self.nav_live, "totale_valore_mercato_eur": self.nav_live}

    def _conn(self):
        raise RuntimeError("niente DB nei test sintetici")


def _recon(dates, nav, cb, re=None, div=None, primo_trade=None, maturati=None):
    re = re or [0] * len(dates)
    div = div or [0] * len(dates)
    return {"dates": dates, "nav_eur": nav, "cost_basis_eur": cb,
            "pnl_eur": [n - c for n, c in zip(nav, cb)],
            "realized_sales_eur": re, "dividend_income_eur": div,
            "dividendi_maturati_eur": maturati if maturati is not None else list(div),
            "dividendi_non_registrati": [],
            "first_trade_date": primo_trade or dates[0]}


def _payload(monkeypatch, recon, snaps=(), ledger=()):
    snaps = [dict(s) for s in snaps]
    nav_live = snaps[-1]["nav_total_eur"] if snaps else recon["nav_eur"][-1]
    monkeypatch.setattr(twr, "_load_snapshots", lambda: snaps)
    monkeypatch.setattr(twr, "get_cash_movements", lambda: list(ledger))
    monkeypatch.setattr(twr, "MemoryDB", lambda: _DBFinto(nav_live))
    monkeypatch.setattr(twr, "connect_sqlite", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(pa, "compute_nav_history", lambda: recon)
    from bellomberg.market_data import market_inputs
    monkeypatch.setattr(market_inputs, "get_risk_free", lambda c: 0.03)
    twr.invalidate_cache()
    out = twr.compute_twr_payload(force=True)
    assert not out.get("error"), out.get("error")
    return out


def _voce(rp, codice):
    return next(v for v in rp["voci"] if v["voce"] == codice)


def _r(out):
    idx = out["twr_index"]
    return [idx[i] / idx[i - 1] - 1 for i in range(1, len(idx))]


# ------------------------------------------------------------------ (C) primo giorno

def test_primo_giorno_parte_dal_costo_alla_data_del_primo_trade(monkeypatch):
    rec = _recon(["2026-03-02", "2026-03-03"], [950, 1000], [1000, 1000], primo_trade="2026-03-01")
    out = _payload(monkeypatch, rec)
    assert out["dates"][:2] == ["2026-03-01", "2026-03-02"]
    assert out["values_eur"][:2] == [1000, 950]
    assert out["flows_eur"][:2] == [1000, 0]
    assert out["twr_index"][1] == 95.0            # primo r = chiusura/costo - 1 = -5%
    assert out["base"]["tipo"] == "costo" and out["base"]["valore_eur"] == 1000
    assert out["base"]["data"] == "2026-03-01" and out["base"]["data_convenzionale"] is False
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_eur"] == rp["pnl_contabile_eur"] == 0


def test_primo_trade_nel_giorno_di_chiusura_usa_la_vigilia_dichiarata(monkeypatch):
    rec = _recon(["2026-03-03", "2026-03-04"], [990, 1000], [1000, 1000], primo_trade="2026-03-03")
    out = _payload(monkeypatch, rec)
    assert out["dates"][0] == "2026-03-02" and out["base"]["data_convenzionale"] is True
    assert out["twr_index"][1] == 99.0


def test_costo_base_al_netto_del_realizzato_del_primo_giorno(monkeypatch):
    # primo giorno: comprati 1000, venduto subito un pezzo con 100 di realizzato
    rec = _recon(["2026-03-02", "2026-03-03"], [900, 900], [1000, 1000], re=[100, 100],
                 primo_trade="2026-03-01")
    out = _payload(monkeypatch, rec)
    assert out["base"]["valore_eur"] == 900 and out["values_eur"][0] == 900


# ------------------------------------------------------------------ (B) dividendi nella catena

def test_dividendo_nel_tratto_ricostruito_entra_una_volta(monkeypatch):
    rec = _recon(["2026-03-02", "2026-03-03", "2026-03-04"], [1000, 1000, 1000],
                 [1000, 1000, 1000], div=[0, 50, 50], primo_trade="2026-03-01")
    out = _payload(monkeypatch, rec)
    assert out["values_eur"] == [1000, 1000, 1050, 1050]
    assert out["twr_index"][-1] == 105.0
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_eur"] == rp["pnl_contabile_eur"] == 50
    assert rp["residuo_eur"] == 0 and rp["residuo_stato"] == "riconciliato_entro_tolleranza"
    assert out["dividendi_ricostruiti"]["stato"] == "inclusi"


def test_catena_usa_i_dividendi_maturati_non_gli_incassati(monkeypatch):
    # stacco il 03/03 (maturato), incasso il 04/03: la catena resta piatta
    rec = _recon(["2026-03-02", "2026-03-03", "2026-03-04"], [1000, 950, 950], [1000] * 3,
                 div=[0, 0, 50], maturati=[0, 50, 50], primo_trade="2026-03-01")
    out = _payload(monkeypatch, rec)
    assert all(abs(x) < 1e-9 for x in _r(out))


def test_dividendo_reinvestito_non_si_conta_due_volte(monkeypatch):
    rec = _recon(["2026-03-02", "2026-03-03", "2026-03-04"], [1000, 1000, 1050],
                 [1000, 1000, 1050], div=[0, 50, 50], primo_trade="2026-03-01")
    out = _payload(monkeypatch, rec)
    assert out["flows_eur"] == [1000, 0, 0, 50]
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_eur"] == 50 == rp["pnl_contabile_eur"]
    assert out["twr_index"][-1] == 105.0


def test_serie_dividendi_maturati_assente_e_dichiarata(monkeypatch):
    rec = _recon(["2026-03-02", "2026-03-03"], [1000, 1000], [1000, 1000], primo_trade="2026-03-01")
    del rec["dividendi_maturati_eur"]
    out = _payload(monkeypatch, rec)
    assert out["dividendi_ricostruiti"]["stato"] == "n.d."
    assert any("n.d." in n and "ividend" in n for n in out["notes"])


def test_stacchi_non_registrati_rendono_parziale_lo_stato_col_conteggio(monkeypatch):
    rec = _recon(["2026-03-02", "2026-03-03"], [1000, 1000], [1000, 1000], primo_trade="2026-03-01")
    rec["dividendi_non_registrati"] = [{"ticker": "QQSYN.MI", "ex_date": "2026-03-03"},
                                       {"ticker": "QQSYN.MI", "ex_date": "2026-09-01"}]
    snaps = [{"date": "2026-03-03", "nav_total_eur": 1000, "invested_eur": 1000, "cash_eur": 0}]
    out = _payload(monkeypatch, rec, snaps)
    dr = out["dividendi_ricostruiti"]
    assert dr["stato"] == "parziale" and dr["stacchi_non_registrati"] == 1
    rec["dividendi_non_registrati"] = None
    out = _payload(monkeypatch, rec, snaps)
    assert out["dividendi_ricostruiti"]["stato"] == "inclusi_registrati_verifica_nd"


# ------------------------------------------------------------------ (A) cucitura + (D) riconciliazione

_D = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]


def test_riconciliazione_chiude_al_centesimo_senza_differenze_di_prezzo(monkeypatch):
    rec = _recon(_D, [1000, 1100, 1200, 1300], [1000] * 4, primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1600, "invested_eur": 1100, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 1700, "invested_eur": 1200, "cash_eur": 500},
             {"date": _D[3], "nav_total_eur": 1800, "invested_eur": 1300, "cash_eur": 500}]
    out = _payload(monkeypatch, rec, snaps)
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 300 == rp["pnl_contabile_eur"]
    assert rp["residuo_eur"] == 0 and rp["residuo_stato"] == "riconciliato_entro_tolleranza"
    assert rp["cassa"]["non_spiegata_eur"] == 0
    for v in rp["voci"]:
        if v["voce"] != "arrotondamento_indice":
            assert v["importo_eur"] == 0, v
    assert "cassa_cambi_commissioni_non_attribuite" not in [v["voce"] for v in rp["voci"]]


def test_voci_misurate_e_cassa_non_spiegata_nel_residuo(monkeypatch):
    rec = _recon(_D, [1000, 1100, 1200, 1300], [1000] * 4, primo_trade="2026-03-01")
    # snapshot d0: posizioni a 1090 (10 sotto la ricostruzione); ultimo: +7 sulle
    # posizioni e +3 di cassa che nessun trade spiega
    snaps = [{"date": _D[1], "nav_total_eur": 1590, "invested_eur": 1090, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 1700, "invested_eur": 1200, "cash_eur": 500},
             {"date": _D[3], "nav_total_eur": 1810, "invested_eur": 1307, "cash_eur": 503}]
    out = _payload(monkeypatch, rec, snaps)
    assert out["cucitura"]["base_eur"] == 1600
    assert out["cucitura"]["scarto_snapshot_meno_ricostruzione_eur"] == -10
    i = out["dates"].index(_D[2])
    assert out["twr_index"][i] / out["twr_index"][i - 1] == pytest.approx(1700 / 1600, abs=1e-5)
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 310 and rp["pnl_contabile_eur"] == 300
    assert _voce(rp, "ultimo_punto")["importo_eur"] == 7
    assert _voce(rp, "ultimo_punto")["stato"] == "misurata"
    assert _voce(rp, "cucitura_ricostruita_ufficiale")["importo_eur"] == 0
    assert rp["cassa"]["variazione_osservata_eur"] == 3 and rp["cassa"]["variazione_attesa_eur"] == 0
    assert rp["residuo_eur"] == 3 == rp["cassa"]["non_spiegata_eur"]
    assert rp["residuo_stato"] == "riconciliato_entro_tolleranza"
    assert any("-10" in n for n in out["notes"])


def test_versamento_non_registrato_rende_il_residuo_non_riconciliato(monkeypatch):
    rec = _recon(_D[:3], [1000, 1000, 1000], [1000] * 3, primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 31500, "invested_eur": 1000, "cash_eur": 30500}]
    out = _payload(monkeypatch, rec, snaps, ledger=())
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 30000 and rp["pnl_contabile_eur"] == 0
    assert rp["residuo_eur"] == 30000 and rp["residuo_stato"] == "non_riconciliato"
    assert rp["cassa"]["non_spiegata_eur"] == 30000 and rp["controllo_cassa"]["stato"] == "coerente"


def test_versamento_registrato_e_un_flusso_e_il_residuo_e_zero(monkeypatch):
    rec = _recon(_D[:3], [1000, 1000, 1000], [1000] * 3, primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 31500, "invested_eur": 1000, "cash_eur": 30500}]
    out = _payload(monkeypatch, rec, snaps, [{"date": _D[2], "type": "DEPOSIT", "amount_eur": 30000}])
    assert out["flows_eur"][-1] == 30000 and out["twr_index"][-1] == 100.0
    rp = out["riconciliazione_pnl"]
    assert rp["cassa"]["versamenti_ledger_eur"] == 30000 and rp["residuo_eur"] == 0
    assert rp["cassa"]["non_spiegata_eur"] == 0 and rp["controllo_cassa"]["stato"] == "coerente"


def test_vendita_dopo_la_cucitura_non_e_rendimento_e_la_cassa_torna(monkeypatch):
    rec = _recon(_D[:3], [1000, 1200, 600], [1000, 1000, 500], re=[0, 0, 100], primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1500, "invested_eur": 1200, "cash_eur": 300},
             {"date": _D[2], "nav_total_eur": 1500, "invested_eur": 600, "cash_eur": 900}]
    out = _payload(monkeypatch, rec, snaps)
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 200 == rp["pnl_contabile_eur"]
    assert rp["cassa"]["acquisti_meno_vendite_eur"] == -600
    assert rp["residuo_eur"] == 0
    i = out["dates"].index(_D[2])
    assert out["twr_index"][i] == out["twr_index"][i - 1]


def test_posizione_comprata_e_venduta_dentro_il_tratto_ricostruito(monkeypatch):
    rec = _recon(_D[:3], [1000, 1520, 1000], [1000, 1500, 1000], re=[0, 0, 40], primo_trade="2026-03-01")
    out = _payload(monkeypatch, rec)
    assert out["flows_eur"] == [1000, 0, 500, -540]
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == pytest.approx(40, abs=0.01) == rp["pnl_contabile_eur"]


def test_snapshot_finale_sospetto_e_da_verificare(monkeypatch):
    rec = _recon(_D[:3], [1000, 1000, 1000], [1000] * 3, primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 6500, "invested_eur": 6000, "cash_eur": 500}]
    out = _payload(monkeypatch, rec, snaps)
    v = _voce(out["riconciliazione_pnl"], "ultimo_punto")
    assert v["importo_eur"] == 5000 and v["stato"] == "da_verificare"


def test_cucitura_con_dividendi_non_li_conta_due_volte_ne_li_perde(monkeypatch):
    d = _D[:3]
    rec = _recon(d, [1000, 1000, 1000], [1000] * 3, div=[0, 50, 50], primo_trade="2026-03-01")
    snaps = [{"date": d[1], "nav_total_eur": 1550, "invested_eur": 1000, "cash_eur": 550},
             {"date": d[2], "nav_total_eur": 1550, "invested_eur": 1000, "cash_eur": 550}]
    out = _payload(monkeypatch, rec, snaps)
    i = out["dates"].index(d[2])
    assert out["twr_index"][i] == out["twr_index"][i - 1]
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 50 == rp["pnl_contabile_eur"]
    assert rp["residuo_eur"] == 0


def test_credito_maturato_non_incassato_alla_cucitura_entra_nella_base(monkeypatch):
    # stacco prima di d0 (maturato 50), incasso DOPO d0: la cassa dello snapshot d0 non
    # lo contiene ancora, quella successiva si'
    d = _D[:3]
    rec = _recon(d, [1000, 950, 950], [1000] * 3, div=[0, 0, 50], maturati=[0, 50, 50],
                 primo_trade="2026-03-01")
    snaps = [{"date": d[1], "nav_total_eur": 1450, "invested_eur": 950, "cash_eur": 500},
             {"date": d[2], "nav_total_eur": 1500, "invested_eur": 950, "cash_eur": 550}]
    out = _payload(monkeypatch, rec, snaps)
    assert out["cucitura"]["credito_dividendi_eur"] == 50 and out["cucitura"]["base_eur"] == 1500
    assert all(abs(x) < 1e-9 for x in _r(out))       # niente salto ne' allo stacco ne' all'incasso
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 0 == rp["pnl_contabile_eur"]
    assert rp["residuo_eur"] == 0 and _voce(rp, "cucitura_ricostruita_ufficiale")["importo_eur"] == 0


def test_acquisto_e_dividendo_dopo_la_cucitura_restano_nella_cassa(monkeypatch):
    d = _D[:3]
    rec = _recon(d, [1000, 1000, 1200], [1000, 1000, 1200], div=[0, 0, 30], primo_trade="2026-03-01")
    snaps = [{"date": d[0], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": d[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": d[2], "nav_total_eur": 1530, "invested_eur": 1200, "cash_eur": 330}]
    out = _payload(monkeypatch, rec, snaps)
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 30 == rp["pnl_contabile_eur"]
    assert rp["cassa"]["dividendi_incassati_eur"] == 30 and rp["residuo_eur"] == 0
    assert rp["cassa"]["non_spiegata_eur"] == 0 and rp["controllo_cassa"]["stato"] == "coerente"


def test_catena_ricostruita_dichiara_i_dividendi_maturati_non_incassati(monkeypatch):
    rec = _recon(_D[:2], [1000, 950], [1000] * 2, div=[0, 0], maturati=[0, 50], primo_trade="2026-03-01")
    out = _payload(monkeypatch, rec)
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 0 and rp["pnl_contabile_eur"] == -50
    assert _voce(rp, "dividendi_maturati_non_incassati")["importo_eur"] == 50
    assert rp["residuo_eur"] == 0


def test_residuo_misurato_mai_forzato():
    rec = _recon(["2026-03-02", "2026-03-03"], [1000, 1100], [1000, 1000])
    rp = twr.build_pnl_reconciliation(
        ["2026-03-01", "2026-03-02", "2026-03-03"], [1000, 1000, 1100], [1000, 0, 0],
        ["reconstructed"] * 3, [1000, 1000], [0.0, 0.3], [100.0, 100.0, 130.0], [], rec, False)
    assert rp["pnl_contabile_eur"] == 100 and rp["pnl_performance_esatto_eur"] == 300
    assert rp["residuo_eur"] == 200 and rp["residuo_stato"] == "non_riconciliato"


def test_residuo_nd_dichiarato_se_la_cassa_dello_snapshot_manca(monkeypatch):
    rec = _recon(_D[:3], [1000, 1100, 1200], [1000] * 3, primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1600, "invested_eur": 1100, "cash_eur": None},
             {"date": _D[2], "nav_total_eur": 1700, "invested_eur": 1200, "cash_eur": 500}]
    out = _payload(monkeypatch, rec, snaps)
    rp = out["riconciliazione_pnl"]
    assert rp["residuo_eur"] is None and rp["residuo_stato"] == "n.d." and rp["motivo_nd"]
    assert out["cucitura"]["base_eur"] is None and out["cucitura"]["nota"]


def test_variante_ultimo_punto_ai_prezzi_contabili_preparata_e_spenta(monkeypatch):
    rec = _recon(_D[:3], [1000, 1000, 1000], [1000] * 3, primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 1520, "invested_eur": 1020, "cash_eur": 500}]
    out = _payload(monkeypatch, rec, snaps)
    assert twr.ULTIMO_PUNTO_PREZZI_CONTABILI is False
    assert out["ultimo_punto_base"]["tipo"] == "snapshot" and out["values_eur"][-1] == 1520
    monkeypatch.setattr(twr, "ULTIMO_PUNTO_PREZZI_CONTABILI", True)
    out = _payload(monkeypatch, rec, snaps)
    assert out["ultimo_punto_base"]["tipo"] == "prezzi_contabili" and out["values_eur"][-1] == 1500
    rp = out["riconciliazione_pnl"]
    assert _voce(rp, "ultimo_punto")["importo_eur"] == 0
    assert rp["pnl_performance_esatto_eur"] == 0 == rp["pnl_contabile_eur"] and rp["residuo_eur"] == 0


# ------------------------------------------------------------------ storia NAV

@pytest.fixture
def nav(monkeypatch):
    """compute_nav_history con trade, prezzi e feed finti; registra i kwargs del download."""
    monkeypatch.setattr(pa, "_opening_positions", lambda: [])
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: pd.DataFrame())
    monkeypatch.setattr(memory_db, "leggi_cassa_portfolio",
                        lambda path=None: {"cash_eur": 0.0, "cash_source": "portfolio.json",
                                           "cash_source_note": None})
    visti = {}

    def esegui(trades, prezzi, dividendi="assenti", feed=None, riempiti=None):
        giorni = sorted({g for serie in prezzi.values() for g in serie})
        df = pd.DataFrame({t: [serie.get(g) for g in giorni] for t, serie in prezzi.items()},
                          index=pd.to_datetime(giorni), dtype=float)
        if dividendi != "assenti":
            df.attrs["dividendi"] = dividendi
        if riempiti is not None:
            df.attrs["riempiti_ffill"] = riempiti

        def scarica(tk, s, e, salta, **kw):
            visti.update(kw)
            return df
        monkeypatch.setattr(pa, "_trade_history", lambda: trades)
        monkeypatch.setattr(pa, "_download_prices_for_history", scarica)
        monkeypatch.setattr(pa, "_feed_giornaliero", lambda ticker: (feed or {}).get(ticker, {}))
        pa._ANALYTICS_CACHE.clear()
        return pa.compute_nav_history(force=True)
    esegui.visti = visti
    return esegui


def _tr(action, qty, prezzo, data, ticker="QQSYN.MI"):
    return {"ticker": ticker, "action": action, "quantita": qty, "prezzo": prezzo,
            "valuta": "EUR", "data": data}


_PX = {"QQSYN.MI": {"2026-03-02": 100, "2026-03-03": 100, "2026-03-04": 98,
                    "2026-03-05": 98, "2026-03-06": 98}}


def test_dividendo_matura_all_ex_date_niente_salti_ne_allo_stacco_ne_all_incasso(nav, monkeypatch):
    trades = [_tr("BUY", 10, 100, "2026-03-01T10:00:00"),
              _tr("DIVIDEND", 10, 2, "2026-03-06T09:00:00")]
    rec = nav(trades, _PX, dividendi={"QQSYN.MI": [("2026-03-04", 2.0)]})
    assert nav.visti.get("auto_adjust") is False        # chiusure NON aggiustate
    assert rec["nav_eur"] == [1000, 1000, 980, 980, 980]
    assert rec["dividendi_maturati_eur"] == [0, 0, 20, 20, 20]
    assert rec["dividend_income_eur"] == [0, 0, 0, 0, 20]
    assert rec["pnl_con_dividendi_eur"] == [0, 0, 0, 0, 0]
    assert rec["dividendi_non_registrati"] == []
    assert rec["dividendi_abbinati"][0]["matura"] == "ex_date"
    out = _payload(monkeypatch, rec)
    assert all(abs(x) < 1e-9 for x in _r(out))       # nessun rendimento finto
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 0 == rp["pnl_contabile_eur"]


def test_drawdowns_e_charts_quant_non_vedono_lo_stacco_come_perdita(nav):
    px = {"QQSYN.MI": {"2026-03-%02d" % i: (100 if i < 4 else 95) for i in range(2, 14)}}
    trades = [_tr("BUY", 10, 100, "2026-03-02T10:00:00"),
              _tr("DIVIDEND", 10, 5, "2026-03-06T09:00:00")]
    rec = nav(trades, px, dividendi={"QQSYN.MI": [("2026-03-04", 5.0)]})
    assert pa.serie_pnl_rendimento(rec) == [0.0] * 12
    dd = pa.compute_drawdowns(force=False)
    assert dd["max_drawdown_pct"] == pytest.approx(0.0, abs=1e-9)
    from bellomberg.reporting import charts_quant as cq
    r = cq._returns_from_nav(rec)
    assert np.allclose(r, 0.0)


def test_advanced_metrics_legacy_legge_la_serie_con_i_dividendi(monkeypatch):
    import bellomberg.portfolio.advanced_metrics as am
    import bellomberg.market_data.benchmark_series as bsm
    import bellomberg.market_data.market_inputs as mi
    n = 40
    con_div = [((i % 4) - 1.5) * 2.0 for i in range(n)]          # oscilla, nessun salto
    nudo = [v - (50.0 if i >= 20 else 0.0) for i, v in enumerate(con_div)]   # stacco senza dividendo
    monkeypatch.setattr(twr, "compute_twr_payload", lambda: {"error": "twr spento nel test"})
    monkeypatch.setattr(pa, "compute_nav_history", lambda: {
        "dates": ["2026-03-%02d" % (i + 1) if i < 31 else "2026-04-%02d" % (i - 30) for i in range(n)],
        "pnl_eur": nudo, "pnl_con_dividendi_eur": con_div, "cost_basis_eur": [1000.0] * n})
    monkeypatch.setattr(bsm, "compute_benchmark_series", lambda ticker=None, twr_payload=None: {"error": "spento"})
    monkeypatch.setattr(mi, "get_risk_free", lambda c: .02)
    m = am.portfolio_metrics()
    assert m["worst_day_pct"] > -2.0, m["worst_day_pct"]


def test_serie_unica_assente_e_none_mai_ripiego_sul_pnl_nudo():
    assert pa.serie_pnl_rendimento({"pnl_eur": [1, 2], "cost_basis_eur": [1, 1]}) is None
    assert pa.serie_pnl_rendimento({"pnl_con_dividendi_eur": [1], "cost_basis_eur": [1, 1]}) is None


def test_dividendo_senza_stacco_yahoo_matura_all_incasso_dichiarato(nav):
    trades = [_tr("BUY", 10, 100, "2026-03-01T10:00:00"),
              _tr("DIVIDEND", 10, 2, "2026-03-06T09:00:00")]
    rec = nav(trades, _PX, dividendi={})
    assert rec["dividendi_maturati_eur"] == [0, 0, 0, 0, 20]
    assert rec["dividendi_abbinati"][0]["matura"] == "incasso" and rec["dividendi_abbinati"][0]["ex_date"] is None


def test_una_registrazione_copre_un_solo_stacco_il_piu_vicino(nav):
    giorni = {"2026-02-02": 100, "2026-02-03": 100, "2026-05-04": 100, "2026-05-06": 100}
    trades = [_tr("BUY", 10, 100, "2026-02-02T10:00:00"),
              _tr("DIVIDEND", 10, 1, "2026-05-06T09:00:00")]
    rec = nav(trades, {"QQSYN.MI": giorni},
              dividendi={"QQSYN.MI": [("2026-02-03", 1.0), ("2026-05-04", 1.0)]})
    assert rec["dividendi_abbinati"][0]["ex_date"] == "2026-05-04"
    assert [x["ex_date"] for x in rec["dividendi_non_registrati"]] == ["2026-02-03"]


def test_registrazione_fuori_finestra_non_abbina_lo_stacco(nav):
    giorni = {"2026-02-02": 100, "2026-02-03": 100, "2026-09-01": 100}
    trades = [_tr("BUY", 10, 100, "2026-02-02T10:00:00"),
              _tr("DIVIDEND", 10, 1, "2026-09-01T09:00:00")]      # ~210 giorni dopo lo stacco
    rec = nav(trades, {"QQSYN.MI": giorni}, dividendi={"QQSYN.MI": [("2026-02-03", 1.0)]})
    assert rec["dividendi_abbinati"][0]["matura"] == "incasso"
    assert [x["ex_date"] for x in rec["dividendi_non_registrati"]] == ["2026-02-03"]


def test_diritto_al_dividendo_misurato_alla_vigilia_dell_ex_date(nav):
    giorni = {"2026-03-02": 100, "2026-03-03": 100, "2026-03-04": 100}
    # comprato il giorno dello stacco: nessun diritto, niente da dichiarare
    rec = nav([_tr("BUY", 10, 100, "2026-03-03T10:00:00")], {"QQSYN.MI": giorni},
              dividendi={"QQSYN.MI": [("2026-03-03", 1.0)]})
    assert rec["dividendi_non_registrati"] == []
    rec = nav([_tr("BUY", 10, 100, "2026-03-02T10:00:00")], {"QQSYN.MI": giorni},
              dividendi={"QQSYN.MI": [("2026-03-03", 1.0)]})
    assert [x["ex_date"] for x in rec["dividendi_non_registrati"]] == ["2026-03-03"]


def test_verifica_non_eseguita_se_mancano_i_dividendi_yahoo(nav):
    rec = nav([_tr("BUY", 10, 100, "2026-03-02T10:00:00")], _PX)
    verifica = str(rec["dividendi_verifica"]).lower()
    assert rec["dividendi_non_registrati"] is None
    assert "non eseguita" in verifica or "not performed" in verifica


def test_buco_oltre_il_ffill_preso_dal_feed_vivo_e_dichiarato(nav):
    px = {"QQSYN.MI": {"2026-03-02": 100, "2026-03-03": None, "2026-03-04": 101}}
    feed = {"QQSYN.MI": {"2026-03-03": (97.0, "EUR", "fontefinta")}}
    rec = nav([_tr("BUY", 10, 100, "2026-03-02T10:00:00")], px, feed=feed)
    assert rec["nav_eur"] == [1000, 970, 1010]
    assert rec["prezzi_da_feed_vivo"] == [{"ticker": "QQSYN.MI", "n_giorni": 1, "primo": "2026-03-03",
                                           "ultimo": "2026-03-03", "fonti": ["fontefinta"]}]
    assert rec["valorizzati_al_costo"] is None


@pytest.mark.parametrize("riga", [(97.0, "USD", "f"), (0.0, "EUR", "f")])
def test_feed_in_altra_valuta_o_non_positivo_non_si_usa(nav, riga):
    # feed inutilizzabile: il buco resta all'ULTIMO prezzo noto (98), dichiarato fermo;
    # ne' il feed sbagliato ne' il costo (100) entrano nel valore
    px = {"QQSYN.MI": {"2026-03-02": 98, "2026-03-03": None, "2026-03-04": 101}}
    rec = nav([_tr("BUY", 10, 100, "2026-03-02T10:00:00")], px, feed={"QQSYN.MI": {"2026-03-03": riga}})
    assert rec["nav_eur"] == [980, 980, 1010] and rec["prezzi_da_feed_vivo"] is None
    assert rec["prezzi_fermi_ffill"] == [{"ticker": "QQSYN.MI", "n_giorni": 1,
                                          "primo": "2026-03-03", "ultimo": "2026-03-03"}]
    assert rec["valorizzati_al_costo"] is None


def test_senza_alcun_prezzo_noto_resta_al_costo_dichiarato(nav):
    px = {"QQSYN.MI": {"2026-03-02": None, "2026-03-03": 101}}
    rec = nav([_tr("BUY", 10, 100, "2026-03-02T10:00:00")], px)
    assert rec["nav_eur"] == [1000, 1010] and rec["prezzi_fermi_ffill"] is None
    assert rec["valorizzati_al_costo"][0]["n_giorni"] == 1


def test_prezzo_fermo_del_ffill_sostituito_dal_feed_dalla_seconda_seduta(nav):
    # 03/03 prima seduta riportata (festivita': il ffill e' giusto, il feed NON si usa anche se
    # c'e'); 04/03 seconda seduta: il feed del giorno; 05/03 terza senza feed: fermo dichiarato
    px = {"QQSYN.MI": {"2026-03-02": 100, "2026-03-03": 100, "2026-03-04": 100, "2026-03-05": 100,
                       "2026-03-06": 90}}
    feed = {"QQSYN.MI": {"2026-03-03": (70.0, "EUR", "f"), "2026-03-04": (80.0, "EUR", "f")}}
    rec = nav([_tr("BUY", 10, 100, "2026-03-02T10:00:00")], px, feed=feed,
              riempiti={"QQSYN.MI": {"2026-03-03": 1, "2026-03-04": 2, "2026-03-05": 3}})
    assert rec["nav_eur"] == [1000, 1000, 800, 1000, 900]
    assert rec["prezzi_da_feed_vivo"][0]["n_giorni"] == 1 and rec["prezzi_da_feed_vivo"][0]["primo"] == "2026-03-04"
    assert rec["prezzi_fermi_ffill"] == [{"ticker": "QQSYN.MI", "n_giorni": 1,
                                          "primo": "2026-03-05", "ultimo": "2026-03-05"}]


def test_festivita_di_un_giorno_senza_feed_non_e_un_prezzo_fermo(nav):
    px = {"QQSYN.MI": {"2026-03-02": 100, "2026-03-03": 100, "2026-03-04": 90}}
    rec = nav([_tr("BUY", 10, 100, "2026-03-02T10:00:00")], px, riempiti={"QQSYN.MI": {"2026-03-03": 1}})
    assert rec["nav_eur"] == [1000, 1000, 900] and rec["prezzi_fermi_ffill"] is None


def test_feed_giornaliero_prende_l_ultima_riga_del_giorno_utc(tmp_path, monkeypatch):
    db_path = str(tmp_path / "finto.db")
    db = memory_db.MemoryDB(db_path=db_path)
    con = sqlite3.connect(db_path)
    righe = [("QQSYN.MI", 10.0, "EUR", "fonteA", "2026-03-02 08:00:00"),
             ("QQSYN.MI", 12.0, "EUR", "fonteB", "2026-03-02 21:30:00"),
             ("QQSYN.MI", 11.0, "EUR", "fonteA", "2026-03-02 15:00:00"),
             ("QQSYN.MI", 13.0, "EUR", "fonteA", "2026-03-03 00:10:00"),
             ("ZZTEST.MI", 99.0, "EUR", "fonteA", "2026-03-02 09:00:00")]
    con.executemany("INSERT INTO position_prices (ticker, prezzo, valuta, source, timestamp) VALUES (?,?,?,?,?)", righe)
    con.commit()
    con.close()
    monkeypatch.setattr(pa, "MemoryDB", lambda: db)
    out = pa._feed_giornaliero("QQSYN.MI")
    assert out == {"2026-03-02": (12.0, "EUR", "fonteB"), "2026-03-03": (13.0, "EUR", "fonteA")}


def _raw_yahoo():
    """Download finto MultiIndex come yfinance con auto_adjust=False: Close, Adj Close, Dividends."""
    giorni = pd.bdate_range("2026-03-02", periods=10)
    a = [100.0] + [None] * 8 + [110.0]
    b = [None, None] + [50.0] * 8
    close = pd.DataFrame({"QQSYN.DE": a, "ZZTEST.MI": b}, index=giorni, dtype=float)
    adj = close.copy()
    adj["QQSYN.DE"] = adj["QQSYN.DE"] * 0.9          # dividendo futuro: aggiustate piu' basse
    div = pd.DataFrame({"QQSYN.DE": [0.0] * 10, "ZZTEST.MI": [0.0] * 10}, index=giorni)
    div.iloc[3, 0] = 1.5
    return giorni, pd.concat({"Close": close, "Adj Close": adj, "Dividends": div}, axis=1)


def test_download_storia_nav_non_aggiustata_dividendi_positivi_e_riempiti(monkeypatch):
    from bellomberg.cli import price_updater
    giorni, raw = _raw_yahoo()
    chiamate = {}

    def scarica(lista, **kw):
        chiamate.update(kw)
        return raw
    monkeypatch.setattr(pa.yf, "download", scarica)
    monkeypatch.setattr(price_updater, "data_ticker_map",
                        lambda tks: {t: ("QQSYN.DE" if t == "QQSYN.MI" else t) for t in tks})
    out = pa._download_prices_for_history(["QQSYN.MI", "ZZTEST.MI"], "2026-03-02", "2026-03-20",
                                          frozenset(), auto_adjust=False)
    assert chiamate["auto_adjust"] is False and chiamate["actions"] is True
    assert out.attrs["dividendi"] == {"QQSYN.MI": [("2026-03-05", 1.5)], "ZZTEST.MI": []}
    col = out["QQSYN.MI"].tolist()
    lim = pa.FFILL_LIMITE_SEDUTE
    assert col[0] == 100.0 and col[1:1 + lim] == [100.0] * lim          # Close, non Adj Close
    assert all(pd.isna(x) for x in col[1 + lim:9])
    assert all(pd.isna(x) for x in out["ZZTEST.MI"].tolist()[:2])     # niente bfill del passato
    assert out.attrs["riempiti_ffill"]["QQSYN.MI"] == {
        g.strftime("%Y-%m-%d"): n + 1 for n, g in enumerate(giorni[1:1 + lim])}
    assert "ZZTEST.MI" not in out.attrs["riempiti_ffill"]
    assert out.attrs["base_prezzi"] == "non_aggiustati" and out.attrs["fattore_adj"] is None


def test_download_attribuzione_adj_close_e_fattore_dallo_stesso_download(monkeypatch):
    from bellomberg.cli import price_updater
    giorni, raw = _raw_yahoo()
    chiamate = {}

    def scarica(lista, **kw):
        chiamate.update(kw)
        return raw
    monkeypatch.setattr(pa.yf, "download", scarica)
    monkeypatch.setattr(price_updater, "data_ticker_map",
                        lambda tks: {t: ("QQSYN.DE" if t == "QQSYN.MI" else t) for t in tks})
    out = pa._download_prices_for_history(["QQSYN.MI", "ZZTEST.MI"], "2026-03-02", "2026-03-20", frozenset())
    assert chiamate["auto_adjust"] is False and chiamate["actions"] is False
    assert out["QQSYN.MI"].tolist()[0] == pytest.approx(90.0)            # Adj Close
    assert out.attrs["base_prezzi"] == "aggiustati"
    f = out.attrs["fattore_adj"]
    assert f["QQSYN.MI"]["2026-03-02"] == pytest.approx(0.9)
    assert set(f["ZZTEST.MI"].values()) == {1.0}                          # senza dividendi = 1


def test_download_senza_adj_close_dichiara_la_base_non_aggiustata(monkeypatch):
    from bellomberg.cli import price_updater
    giorni, raw = _raw_yahoo()
    raw = raw.drop(columns="Adj Close", level=0)
    monkeypatch.setattr(pa.yf, "download", lambda lista, **kw: raw)
    monkeypatch.setattr(price_updater, "data_ticker_map",
                        lambda tks: {t: ("QQSYN.DE" if t == "QQSYN.MI" else t) for t in tks})
    out = pa._download_prices_for_history(["QQSYN.MI", "ZZTEST.MI"], "2026-03-02", "2026-03-20", frozenset())
    assert out.attrs["base_prezzi"] == "non_aggiustati" and out.attrs["fattore_adj"] is None


def test_irr_nav_history_cento_che_diventa_centodieci_in_un_anno():
    tr = [_tr("BUY", 1, 100.0, "2025-10-06")]
    irr = pa._compute_irr(tr, 110.0, lambda d, c: 1.0, "2026-10-06")
    assert irr == pytest.approx(0.10, abs=2e-3)


# ------------------------------------------------------------------ dividendi: abbinamento (G5/G6)

def test_stacco_mancante_su_yahoo_non_si_abbina_al_trimestre_prima(nav):
    giorni = {"2026-02-02": 100, "2026-02-03": 100, "2026-05-20": 100}
    trades = [_tr("BUY", 10, 100, "2026-02-02T10:00:00"), _tr("DIVIDEND", 10, 1, "2026-05-20T09:00:00")]
    rec = nav(trades, {"QQSYN.MI": giorni}, dividendi={"QQSYN.MI": [("2026-02-03", 1.0)]})
    ab = rec["dividendi_abbinati"][0]
    assert ab["ex_date"] is None and ab["matura"] == "incasso"
    assert [x["ex_date"] for x in rec["dividendi_non_registrati"]] == ["2026-02-03"]
    assert rec["dividendi_maturati_eur"] == [0, 0, 10]


def test_finestra_quarantacinque_giorni_abbina_il_pagamento_di_uno_stacco_inglese(nav):
    giorni = {"2026-02-02": 100, "2026-02-03": 100, "2026-03-13": 100}
    trades = [_tr("BUY", 10, 100, "2026-02-02T10:00:00"), _tr("DIVIDEND", 10, 1, "2026-03-13T09:00:00")]
    rec = nav(trades, {"QQSYN.MI": giorni}, dividendi={"QQSYN.MI": [("2026-02-03", 1.0)]})
    assert rec["dividendi_abbinati"][0]["ex_date"] == "2026-02-03"          # 38 giorni: dentro


def test_registrazione_senza_possesso_alla_vigilia_non_matura_all_ex_date(nav):
    giorni = {"2026-02-02": 100, "2026-02-03": 100, "2026-02-04": 100, "2026-02-20": 100}
    trades = [_tr("BUY", 10, 100, "2026-02-02T10:00:00", ticker="ZZTEST.MI"),
              _tr("BUY", 10, 100, "2026-02-04T10:00:00"),
              _tr("DIVIDEND", 10, 1, "2026-02-20T09:00:00")]
    rec = nav(trades, {"QQSYN.MI": giorni, "ZZTEST.MI": giorni},
              dividendi={"QQSYN.MI": [("2026-02-03", 1.0)]})
    ab = rec["dividendi_abbinati"][0]
    assert ab["matura"] == "incasso" and ab["stacchi_scartati_senza_possesso"] == ["2026-02-03"]
    assert rec["dividendi_maturati_eur"] == [0, 0, 0, 10]


def test_due_registrazioni_dello_stesso_titolo_non_usano_lo_stesso_stacco(nav):
    giorni = {"2026-05-04": 100, "2026-05-06": 100, "2026-05-20": 100}
    trades = [_tr("BUY", 10, 100, "2026-05-01T10:00:00"),
              _tr("DIVIDEND", 10, 1, "2026-05-06T09:00:00"),
              _tr("DIVIDEND", 10, 1, "2026-05-20T09:00:00")]
    rec = nav(trades, {"QQSYN.MI": giorni}, dividendi={"QQSYN.MI": [("2026-05-04", 1.0)]})
    ab = {a["data_incasso"]: a for a in rec["dividendi_abbinati"]}
    assert ab["2026-05-06"]["ex_date"] == "2026-05-04"
    assert ab["2026-05-20"]["ex_date"] is None and ab["2026-05-20"]["matura"] == "incasso"


# ------------------------------------------------------------------ (G3) maturazione nel tratto ufficiale

def test_stacco_dopo_la_cucitura_non_crea_il_buco(monkeypatch):
    rec = _recon(_D + ["2026-03-06"], [1000, 1000, 950, 950, 950], [1000] * 5, div=[0, 0, 0, 0, 50],
                 maturati=[0, 0, 50, 50, 50], primo_trade="2026-03-01")
    d = rec["dates"]
    snaps = [{"date": d[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": d[2], "nav_total_eur": 1450, "invested_eur": 950, "cash_eur": 500},
             {"date": d[3], "nav_total_eur": 1450, "invested_eur": 950, "cash_eur": 500},
             {"date": d[4], "nav_total_eur": 1500, "invested_eur": 950, "cash_eur": 550}]
    out = _payload(monkeypatch, rec, snaps)
    assert all(abs(x) < 1e-9 for x in _r(out))
    assert [c["date"] for c in out["crediti_dividendi_ufficiali"]] == [d[2], d[3]]
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 0 == rp["pnl_contabile_eur"] and rp["residuo_eur"] == 0


def test_credito_alla_cucitura_non_sposta_il_buco_al_primo_giorno_ufficiale(monkeypatch):
    rec = _recon(_D, [1000, 950, 950, 950], [1000] * 4, div=[0, 0, 0, 50], maturati=[0, 50, 50, 50],
                 primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1450, "invested_eur": 950, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 1450, "invested_eur": 950, "cash_eur": 500},
             {"date": _D[3], "nav_total_eur": 1500, "invested_eur": 950, "cash_eur": 550}]
    out = _payload(monkeypatch, rec, snaps)
    assert all(abs(x) < 1e-9 for x in _r(out))
    assert out["riconciliazione_pnl"]["pnl_performance_esatto_eur"] == 0


def test_ultimo_snapshot_prima_dell_incasso_dichiara_il_credito(monkeypatch):
    rec = _recon(_D[:3], [1000, 1000, 950], [1000] * 3, maturati=[0, 0, 50], primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 1450, "invested_eur": 950, "cash_eur": 500}]
    out = _payload(monkeypatch, rec, snaps)
    rp = out["riconciliazione_pnl"]
    assert rp["pnl_performance_esatto_eur"] == 0 and rp["pnl_contabile_eur"] == -50
    assert _voce(rp, "dividendi_maturati_non_incassati")["importo_eur"] == 50
    assert rp["residuo_eur"] == 0 and rp["stato"] == "riconciliato_entro_tolleranza"


# ------------------------------------------------------------------ (G4) stato complessivo

def test_stato_complessivo_e_il_peggiore_delle_voci(monkeypatch):
    rec = _recon(_D[:3], [1000] * 3, [1000] * 3, primo_trade="2026-03-01")
    snaps = [{"date": _D[1], "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": _D[2], "nav_total_eur": 6500, "invested_eur": 6000, "cash_eur": 500}]
    rp = _payload(monkeypatch, rec, snaps)["riconciliazione_pnl"]
    assert rp["residuo_stato"] == "riconciliato_entro_tolleranza"
    assert rp["stato"] == "da_verificare"
    assert rp["soglia_ultimo_punto_pct"] == twr.ULTIMO_PUNTO_SOGLIA_PCT


def test_stato_complessivo_non_riconciliato_vince_su_da_verificare():
    assert twr._stato_complessivo({"residuo_stato": "non_riconciliato",
                                   "voci": [{"stato": "da_verificare"}]}) == "non_riconciliato"
    assert twr._stato_complessivo({"residuo_stato": "riconciliato_entro_tolleranza", "voci": [],
                                   "controllo_cassa": {"stato": "incoerente"}}) == "incoerente"


# ------------------------------------------------------------------ (G2) statistiche senza il punto base

def test_statistiche_escludono_il_punto_base_al_costo():
    rets = [0.5] + [0.0] * 25
    dates = ["2026-03-01"] + ["2026-03-%02d" % (i + 2) for i in range(26)]
    con = twr._twr_metrics(dates, rets, 0.02, 0)
    senza = twr._twr_metrics(dates, rets, 0.02, 1)
    assert con["vol_annual_pct"] > 0 and senza["vol_annual_pct"] == 0
    assert senza["twr_total_pct"] == con["twr_total_pct"] == 50.0      # il totale resta dal costo
    assert senza["index"] == con["index"]


def test_payload_dichiara_il_punto_base_escluso_dalle_statistiche(monkeypatch):
    n = 25
    d = ["2026-03-%02d" % (i + 2) for i in range(n)]
    # costo 1000, prima chiusura 500 (-50% nel primo r, dal costo), poi oscillazioni piccole
    nav = [500.0 + 5 * (i % 3) for i in range(n)]
    out = _payload(monkeypatch, _recon(d, nav, [1000.0] * n, primo_trade="2026-03-01"))
    assert out["indice_statistiche_da"] == 1 and out["base"]["escluso_dalle_statistiche"] is True
    assert out["statistiche_nota"]
    assert out["metrics"]["twr_total_pct"] < -45                  # il totale resta dal costo
    assert out["metrics"]["max_drawdown_pct"] > -5               # il -50% del punto base e' fuori
    assert out["metrics"]["vol_annual_pct"] < 30


def test_advanced_metrics_e_tearsheet_saltano_il_punto_base(monkeypatch):
    import bellomberg.portfolio.advanced_metrics as am
    import bellomberg.portfolio.portfolio_tearsheet as ts
    import bellomberg.market_data.benchmark_series as bsm
    import bellomberg.market_data.market_inputs as mi
    n = 40
    dates = ["2026-02-01"] + ["2026-03-%02d" % (i + 1) if i < 31 else "2026-04-%02d" % (i - 30) for i in range(n - 1)]
    idx = [100.0, 200.0] + [200.0 * (1 + 0.001 * ((i % 3) - 1)) for i in range(n - 2)]
    p = {"dates": dates, "twr_index": idx, "indice_statistiche_da": 1}
    monkeypatch.setattr(twr, "compute_twr_payload", lambda *a, **k: p)
    monkeypatch.setattr(bsm, "compute_benchmark_series", lambda ticker=None, twr_payload=None: {"error": "spento"})
    monkeypatch.setattr(mi, "get_risk_free", lambda c: .02)
    m = am.portfolio_metrics()
    assert m["best_day_pct"] < 1.0, m["best_day_pct"]          # il +100% del punto base e' fuori
    t = ts.compute_tearsheet(twr_payload=p, metrics={"x": 1})
    assert t["period"]["start"] == dates[1]
    mesi = t["monthly"] if isinstance(t["monthly"], list) else t["monthly"].get("months", [])
    assert mesi and all(abs(x["return_pct"]) < 5 for x in mesi), t["monthly"]


# ------------------------------------------------------------------ attribuzione (F4 / G1)

def _attrib(tmp_path, monkeypatch, prezzi, feed, fattori, base="aggiustati", period="MTD", end="2026-07-03",
            tickers=("QQSYN.MI",), riempiti=None):
    import bellomberg.portfolio.portfolio_attribution as pat
    import bellomberg.portfolio.portfolio_sectors as ps
    import bellomberg.storage.negozi_privati as np_
    monkeypatch.setattr(ps, "CACHE_PATH", str(tmp_path / "sector_cache.json"))
    negozio = tmp_path / "prezzi_speciali.json"
    negozio.write_text('{"senza_yfinance": []}', encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_PREZZI", str(negozio))
    prezzi.attrs["fattore_adj"] = fattori
    prezzi.attrs["base_prezzi"] = base
    prezzi.attrs["riempiti_ffill"] = riempiti or {}
    monkeypatch.setattr(pat, "_download_prices_for_history", lambda *a, **k: prezzi)
    monkeypatch.setattr(pa, "_feed_giornaliero", lambda tk: feed.get(tk, {}))
    trades = [{"ticker": t, "action": "BUY", "quantita": 10, "prezzo": 100,
               "valuta": "EUR", "data": "2026-06-15"} for t in tickers]
    return pat.compute_attribution(period=period, end_date=end, trades=trades,
                                   fx=pd.DataFrame(), fetch=lambda tk: {"sector": None, "industry": None})


_IDX3 = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"])


def test_attribuzione_feed_riscalato_col_fattore_di_yahoo(tmp_path, monkeypatch):
    # chiusure aggiustate (90 dove il prezzo vero era 100, fattore 0,9 da Adj Close/Close):
    # il feed (non aggiustato) del 02/07 vale 110 -> 99 sulla base aggiustata
    prezzi = pd.DataFrame({"QQSYN.MI": [90.0, None, 108.9]}, index=_IDX3)
    out = _attrib(tmp_path, monkeypatch, prezzi,
                  {"QQSYN.MI": {"2026-07-02": (110.0, "EUR", "fontefinta")}},
                  {"QQSYN.MI": {"2026-07-01": 0.9}})
    assert out.get("error") is None, out.get("error")
    assert out["portfolio_return_pct"] == pytest.approx(21.0, abs=1e-6)
    assert out["excluded"] == []
    assert out["prezzi_da_feed_vivo"] == [{"ticker": "QQSYN.MI", "n_giorni": 1, "primo": "2026-07-02",
                                           "ultimo": "2026-07-02", "fonti": ["fontefinta"],
                                           "fattori_aggiustamento": [0.9]}]


def test_attribuzione_feed_in_ritardo_non_contamina_il_fattore(tmp_path, monkeypatch):
    # il feed del 01/07 riporta la chiusura del GIORNO PRIMA (95, come polygon): con il fattore
    # dal feed il 02/07 varrebbe 110 x 100/95; col fattore di Yahoo (titolo senza dividendi = 1) vale 110
    prezzi = pd.DataFrame({"QQSYN.MI": [100.0, None, 121.0]}, index=_IDX3)
    out = _attrib(tmp_path, monkeypatch, prezzi,
                  {"QQSYN.MI": {"2026-07-01": (95.0, "EUR", "polygon"), "2026-07-02": (110.0, "EUR", "polygon")}},
                  {"QQSYN.MI": {"2026-07-01": 1.0, "2026-07-03": 1.0}})
    assert out["prezzi_da_feed_vivo"][0]["fattori_aggiustamento"] == [1.0]
    assert out["portfolio_return_pct"] == pytest.approx(21.0, abs=1e-6)
    by = {r["ticker"]: r for r in out["by_position"]}
    assert by["QQSYN.MI"]["contribution_pct"] == pytest.approx(21.0, abs=1e-3)


def test_attribuzione_festivita_di_una_seduta_tiene_il_ffill(tmp_path, monkeypatch):
    prezzi = pd.DataFrame({"QQSYN.MI": [100.0, 100.0, 121.0]}, index=_IDX3)
    out = _attrib(tmp_path, monkeypatch, prezzi,
                  {"QQSYN.MI": {"2026-07-02": (95.0, "EUR", "polygon")}},
                  {"QQSYN.MI": {"2026-07-01": 1.0}}, riempiti={"QQSYN.MI": {"2026-07-02": 1}})
    assert out["prezzi_da_feed_vivo"] is None
    assert out["portfolio_return_pct"] == pytest.approx(21.0, abs=1e-6)


def test_attribuzione_feed_dalla_seconda_seduta_riportata(tmp_path, monkeypatch):
    idx = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"])
    prezzi = pd.DataFrame({"QQSYN.MI": [100.0, 100.0, 100.0, 121.0]}, index=idx)
    out = _attrib(tmp_path, monkeypatch, prezzi,
                  {"QQSYN.MI": {"2026-07-02": (95.0, "EUR", "f"), "2026-07-03": (110.0, "EUR", "f")}},
                  {"QQSYN.MI": {"2026-07-01": 1.0}}, end="2026-07-06",
                  riempiti={"QQSYN.MI": {"2026-07-02": 1, "2026-07-03": 2}})
    assert out["prezzi_da_feed_vivo"][0]["primo"] == "2026-07-03" and out["prezzi_da_feed_vivo"][0]["n_giorni"] == 1


def test_attribuzione_buco_senza_feed_resta_all_ultimo_prezzo_noto_dichiarato(tmp_path, monkeypatch):
    idx = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"])
    prezzi = pd.DataFrame({"QQSYN.MI": [90.0, None, None, 99.0]}, index=idx)
    out = _attrib(tmp_path, monkeypatch, prezzi, {"QQSYN.MI": {"2026-07-02": (110.0, "EUR", "f")}},
                  {"QQSYN.MI": {"2026-07-01": 0.9}}, end="2026-07-06")
    # 01->02: 99/90 = +10% (riscalato); il 03/07 (nessuna chiusura ne' feed) resta a 99, dichiarato
    assert out["portfolio_return_pct"] == pytest.approx(10.0, abs=1e-6)
    assert out["excluded"] == []
    assert out["prezzi_fermi"] == [{"ticker": "QQSYN.MI", "n_giorni": 1, "primo": "2026-07-03",
                                    "ultimo": "2026-07-03"}]


def test_attribuzione_senza_fattore_di_yahoo_non_usa_il_feed(tmp_path, monkeypatch):
    prezzi = pd.DataFrame({"QQSYN.MI": [90.0, None, 108.9], "ZZTEST.MI": [100.0, 100.0, 100.0]}, index=_IDX3)
    out = _attrib(tmp_path, monkeypatch, prezzi, {"QQSYN.MI": {"2026-07-02": (110.0, "EUR", "f")}},
                  None, base="non_aggiustati", tickers=("QQSYN.MI", "ZZTEST.MI"))
    assert out["prezzi_da_feed_vivo"] is None
    assert out["excluded"] == [] and out["prezzi_fermi"][0]["ticker"] == "QQSYN.MI"
    assert any("Adj Close" in str(n) for n in out["notes"])
    by = {r["ticker"]: r for r in out["by_position"]}
    assert by["QQSYN.MI"]["contribution_pct"] == pytest.approx(900 / 1900 * 21.0, abs=1e-3)


def test_controllo_cassa_segnala_un_errore_nelle_formule_delle_voci():
    rec = _recon(["2026-03-02", "2026-03-03"], [1000, 1000], [1000, 1000])
    snaps = [{"date": "2026-03-02", "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500},
             {"date": "2026-03-03", "nav_total_eur": 1500, "invested_eur": 1000, "cash_eur": 500}]
    cuc = {"base_eur": 1500, "cassa_snapshot_eur": 500, "credito_dividendi_eur": 0}
    args = (["2026-03-01", "2026-03-02", "2026-03-03"], [1000, 1000, 1500], [1000, 0, 0],
            ["reconstructed", "reconstructed", "official"], [1000, 1500])
    ok = twr.build_pnl_reconciliation(*args, [0.0, 0.0], [100.0, 100.0, 100.0], snaps, rec, True, cuc)
    assert ok["residuo_eur"] == 0 and ok["controllo_cassa"]["stato"] == "coerente"
    ko = twr.build_pnl_reconciliation(*args, [0.0, 0.01], [100.0, 100.0, 101.0], snaps, rec, True, cuc)
    assert ko["residuo_eur"] == 15 and ko["cassa"]["non_spiegata_eur"] == 0
    assert ko["controllo_cassa"]["stato"] == "incoerente" and ko["stato"] == "incoerente"


def test_attribuzione_feed_su_un_buco_lungo(tmp_path, monkeypatch):
    idx = pd.bdate_range("2026-06-30", periods=45)
    prezzi = pd.DataFrame({"QQSYN.MI": [90.0] + [None] * 43 + [90.0]}, index=idx)
    feed = {"QQSYN.MI": {g.strftime("%Y-%m-%d"): (100.0, "EUR", "f") for g in idx[:44]}}
    out = _attrib(tmp_path, monkeypatch, prezzi, feed, {"QQSYN.MI": {"2026-06-30": 0.9}},
                  period="INCEPTION", end=idx[-1].strftime("%Y-%m-%d"))
    assert out.get("error") is None, out.get("error")
    assert out["excluded"] == [] and out["prezzi_da_feed_vivo"][0]["n_giorni"] == 43
    assert out["portfolio_return_pct"] == pytest.approx(0.0, abs=1e-6)


def _carino(giorni):
    """Oracolo indipendente: giorni = [{ticker: (peso, rendimento)}] -> contributi Carino in %."""
    import math
    rp = [sum(w * r for w, r in g.values()) for g in giorni]
    tot = math.prod(1 + r for r in rp) - 1
    k = (lambda r: 1.0 if abs(r) < 1e-12 else math.log1p(r) / r)
    out = {}
    for g, r_t in zip(giorni, rp):
        for tk, (w, r) in g.items():
            out[tk] = out.get(tk, 0.0) + k(r_t) / k(tot) * w * r * 100
    return out


def test_attribuzione_contributi_sul_percorso_riscalato(tmp_path, monkeypatch):
    prezzi = pd.DataFrame({"QQSYN.MI": [90.0, None, 99.0], "ZZTEST.MI": [100.0, 100.0, 110.0]}, index=_IDX3)
    out = _attrib(tmp_path, monkeypatch, prezzi, {"QQSYN.MI": {"2026-07-02": (110.0, "EUR", "f")}},
                  {"QQSYN.MI": {"2026-07-01": 0.9}, "ZZTEST.MI": {"2026-07-01": 1.0}},
                  tickers=("QQSYN.MI", "ZZTEST.MI"))
    # percorso riscalato di QQSYN: 90 -> 99 (feed 110 x 0,9) -> 99
    atteso = _carino([{"QQSYN.MI": (900 / 1900, 0.10), "ZZTEST.MI": (1000 / 1900, 0.0)},
                      {"QQSYN.MI": (990 / 1990, 0.0), "ZZTEST.MI": (1000 / 1990, 0.10)}])
    by = {r["ticker"]: r for r in out["by_position"]}
    assert by["QQSYN.MI"]["contribution_pct"] == pytest.approx(atteso["QQSYN.MI"], abs=1e-3)
    assert by["ZZTEST.MI"]["contribution_pct"] == pytest.approx(atteso["ZZTEST.MI"], abs=1e-3)


def test_registrazione_di_dividendo_in_eur_non_rietichetta_la_valuta_del_titolo():
    trades = [{"ticker": "ZZTEST.L", "action": "BUY", "valuta": "GBX"},
              {"ticker": "ZZTEST.L", "action": "DIVIDEND", "valuta": "EUR"}]
    labels = pa._currency_labels_for_tickers(["ZZTEST.L"], trades)
    assert labels["ZZTEST.L"].valore == "GBX"


def test_fra_due_stacchi_nella_finestra_si_sceglie_il_piu_vicino(nav):
    giorni = {"2026-04-01": 100, "2026-04-10": 100, "2026-05-15": 100, "2026-05-20": 100}
    trades = [_tr("BUY", 10, 100, "2026-04-01T10:00:00"), _tr("DIVIDEND", 10, 1, "2026-05-20T09:00:00")]
    rec = nav(trades, {"QQSYN.MI": giorni},
              dividendi={"QQSYN.MI": [("2026-04-10", 1.0), ("2026-05-15", 1.0)]})
    assert rec["dividendi_abbinati"][0]["ex_date"] == "2026-05-15"
    assert [x["ex_date"] for x in rec["dividendi_non_registrati"]] == ["2026-04-10"]


def test_dividendo_su_ticker_senza_acquisti_non_ferma_il_costo_storico_degli_altri():
    trades = [_tr("BUY", 10, 100, "2026-03-02"), _tr("DIVIDEND", 10, 1, "2026-03-03", ticker="ZZTEST")]
    out = pa.pl_fx_per_posizione(trades, "2026-03-05")
    assert "error" not in out, out.get("error")
    assert out["dividendi_senza_posizione"] == ["ZZTEST"]
    assert "QQSYN.MI" in out["per_ticker"] and "ZZTEST" not in out["per_ticker"]
    solo = pa.pl_fx_per_posizione([_tr("DIVIDEND", 10, 1, "2026-03-03", ticker="ZZTEST")], "2026-03-05")
    assert "error" in solo and solo["dividendi_senza_posizione"] == ["ZZTEST"]


def test_fattore_dall_ultimo_giorno_noto_con_stacco_prima_del_buco(tmp_path, monkeypatch):
    # 01/07 prima dello stacco (fattore 0,9), 02/07 stacco (fattore 1), 03/07 buco: il feed (110)
    # si riscala col fattore dell'ULTIMO giorno noto (1,0 -> 110), non del primo (0,9 -> 99)
    idx = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"])
    prezzi = pd.DataFrame({"QQSYN.MI": [90.0, 100.0, None, 110.0]}, index=idx)
    out = _attrib(tmp_path, monkeypatch, prezzi, {"QQSYN.MI": {"2026-07-03": (110.0, "EUR", "f")}},
                  {"QQSYN.MI": {"2026-07-01": 0.9, "2026-07-02": 1.0, "2026-07-06": 1.0}}, end="2026-07-06")
    assert out["prezzi_da_feed_vivo"][0]["fattori_aggiustamento"] == [1.0]
    assert out["portfolio_return_pct"] == pytest.approx((110 / 90 - 1) * 100, abs=1e-3)
    by = {r["ticker"]: r for r in out["by_position"]}
    assert by["QQSYN.MI"]["contribution_pct"] == pytest.approx((110 / 90 - 1) * 100, abs=1e-3)


def test_riconciliazione_attribuzione_twr_esclude_il_punto_base(tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_attribution as pat
    trades = [{"ticker": "QQSYN.MI", "action": "BUY", "quantita": 10, "prezzo": 100,
               "valuta": "EUR", "data": "2026-07-01T10:00:00"}]
    monkeypatch.setattr(pat, "_trade_history", lambda: trades)
    monkeypatch.setattr(pat, "_opening_positions", lambda: [])
    # candela del 30/06 (buffer del download): con INCEPTION la base dei pesi e' il 30/06, quindi
    # la finestra contiene il r del 01/07 = quello del punto base al costo
    prezzi = pd.DataFrame({"QQSYN.MI": [100.0, 100.0, 110.0]},
                          index=pd.to_datetime(["2026-06-30", "2026-07-01", "2026-07-02"]))
    # TWR ufficiale: punto base al costo il 30/06 (+50% dal costo storico) poi +10%
    tp = {"dates": ["2026-06-30", "2026-07-01", "2026-07-02"], "twr_index": [100.0, 150.0, 165.0],
          "indice_statistiche_da": 1}
    monkeypatch.setattr(twr, "compute_twr_payload", lambda *a, **k: tp)
    out = _attrib(tmp_path, monkeypatch, prezzi, {}, {"QQSYN.MI": {"2026-07-01": 1.0}},
                  period="INCEPTION", end="2026-07-02")
    # _attrib passa i trade iniettati: qui serve il percorso NON iniettato (riconciliazione dal TWR)
    out = pat.compute_attribution(period="INCEPTION", end_date="2026-07-02", force=True,
                                  fx=pd.DataFrame(), fetch=lambda tk: {"sector": None, "industry": None})
    rec = out["reconciliation"]
    assert rec["official_twr_pct"] == pytest.approx(10.0, abs=1e-3), rec
    assert rec["punto_base_escluso"] is True
    tp["indice_statistiche_da"] = 0
    out = pat.compute_attribution(period="INCEPTION", end_date="2026-07-02", force=True,
                                  fx=pd.DataFrame(), fetch=lambda tk: {"sector": None, "industry": None})
    assert out["reconciliation"]["official_twr_pct"] == pytest.approx(65.0, abs=1e-3)
