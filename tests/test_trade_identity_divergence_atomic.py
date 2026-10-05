"""Trade Entry: verifica d'identita' e divergenza manuale nella STESSA transazione del trade.

L'anteprima (POST /trade/preview) e' sola lettura: un annullo del PM non deve
lasciare righe nelle tabelle append-only. La conferma (POST /trade) scrive
trade, verifica e divergenza insieme, oppure nulla.
Solo ticker e numeri sintetici.
"""
from datetime import datetime, timedelta

import pytest

from bellomberg.storage import memory_db


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


def _isin(prefix="XS00000000"):
    base = prefix + "1"
    for digit in "0123456789":
        if memory_db._isin_checksum_valid(base + digit):
            return base + digit
    raise AssertionError("nessuna cifra di controllo valida")


ISIN = _isin()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", _no_chroma)
    db = memory_db.MemoryDB(str(tmp_path / "data" / "test.db"), str(tmp_path / "chroma"))
    import bellomberg.api.bellomberg_api as api
    from bellomberg.cli import price_updater
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "_notify_model_tracking", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte", lambda _c: (1.0, "live"))
    db.apply_cash_movement("DEPOSIT", 10000)
    past = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    with db._conn() as conn:
        conn.execute("INSERT INTO decisions(timestamp,action,ticker,status) "
                     "VALUES (?,'BUY','ZZPROP','PENDING')", (past,))
        conn.execute("INSERT INTO decisions(timestamp,action,ticker,status,assessment_status,"
                     "proposal_ticker) VALUES (?,'BUY','ZZPROP','PENDING','BLOCKED','ZZPROP')", (past,))
    return db, api


def _counts(db):
    with db._conn() as conn:
        one = lambda q: conn.execute(q).fetchone()[0]
        return {"trades": one("SELECT COUNT(*) FROM trade_history"),
                "identities": one("SELECT COUNT(*) FROM instrument_identity_verifications"),
                "events": one("SELECT COUNT(*) FROM decision_events"),
                "cash": one("SELECT balance_cents FROM cash_state WHERE singleton_id=1")}


def _identity():
    return {"isin": ISIN, "source": "prospetto sintetico", "verified_at": "2026-09-01T10:00:00",
            "reason": "stesso strumento su due sedi"}


def _linked(api, **extra):
    return api.TradeIn(ticker="ZZEXEC", action="BUY", quantita=1, prezzo=10, valuta="EUR",
                       linked_decision_id=1, instrument_identity=_identity(), **extra)


def test_anteprima_con_verifica_identita_non_scrive_nulla(env):
    db, api = env
    before = _counts(db)
    preview = api.preview_trade(_linked(api))
    assert preview["instrument_identity"]["isin"] == ISIN
    assert preview["instrument_identity"]["proposed_ticker"] == "ZZPROP"
    assert preview["instrument_identity"]["execution_ticker"] == "ZZEXEC"
    # il PM annulla: nessuna riga nuova, in nessuna tabella
    assert _counts(db) == before


def test_conferma_scrive_trade_e_verifica_insieme(env):
    db, api = env
    before = _counts(db)
    preview = api.preview_trade(_linked(api))
    out = api.log_trade(_linked(api, preview_id=preview["preview_id"]))
    after = _counts(db)
    assert after["trades"] == before["trades"] + 1
    assert after["identities"] == before["identities"] + 1
    assert after["events"] == before["events"] + 1  # TRADE_LINKED
    assert out["instrument_identity"]["isin"] == ISIN
    with db._conn() as conn:
        row = conn.execute("SELECT proposed_ticker,execution_ticker,isin FROM "
                           "instrument_identity_verifications").fetchone()
        linked = conn.execute("SELECT linked_decision_id FROM trade_history").fetchone()[0]
    assert tuple(row) == ("ZZPROP", "ZZEXEC", ISIN) and linked == 1


def test_conferma_fallita_non_lascia_la_verifica(env, monkeypatch):
    db, api = env
    preview = api.preview_trade(_linked(api))
    before = _counts(db)

    def boom(self, *a, **k):
        raise RuntimeError("guasto sintetico dopo la verifica")
    monkeypatch.setattr(memory_db.MemoryDB, "_apply_position_replay", boom)
    monkeypatch.setattr(memory_db.MemoryDB, "log_trade", boom)
    with pytest.raises(api.HTTPException):
        api.log_trade(_linked(api, preview_id=preview["preview_id"]))
    assert _counts(db) == before


def test_verifica_non_richiesta_rifiutata_senza_scrivere(env):
    db, api = env
    before = _counts(db)
    with pytest.raises(api.HTTPException):
        api.preview_trade(api.TradeIn(ticker="ZZPROP", action="BUY", quantita=1, prezzo=10,
                                      valuta="EUR", linked_decision_id=1,
                                      instrument_identity=_identity()))
    assert _counts(db) == before


def _divergent(api, ticker="ZZPROP", identity=None, reason="eseguito comunque", **extra):
    return api.TradeIn(ticker=ticker, action="BUY", quantita=1, prezzo=10, valuta="EUR",
                       senza_decisione=True, instrument_identity=identity,
                       manual_divergence={"decision_id": 2, "reason": reason}, **extra)


def test_divergenza_anteprima_sola_lettura_e_conferma_atomica(env):
    db, api = env
    before = _counts(db)
    preview = api.preview_trade(_divergent(api))
    assert preview["manual_divergence"]["decision_id"] == 2
    assert preview["manual_divergence"]["reason"] == "eseguito comunque"
    assert _counts(db) == before
    out = api.log_trade(_divergent(api, preview_id=preview["preview_id"]))
    after = _counts(db)
    assert after["trades"] == before["trades"] + 1 and after["events"] == before["events"] + 1
    with db._conn() as conn:
        event = conn.execute("SELECT decision_id,event_type,reason,details_json FROM decision_events").fetchone()
    assert event["decision_id"] == 2 and event["event_type"] == "MANUAL_TRADE_DIVERGENCE_RECORDED"
    assert event["reason"] == "eseguito comunque"
    assert f'"trade_id": {out["trade_id"]}' in event["details_json"]
    assert out["manual_divergence"]["event_id"] > 0


def test_divergenza_con_ticker_diverso_scrive_verifica_trade_ed_evento_insieme(env):
    db, api = env
    before = _counts(db)
    preview = api.preview_trade(_divergent(api, ticker="ZZEXEC", identity=_identity()))
    assert preview["instrument_identity"]["proposed_ticker"] == "ZZPROP"
    assert _counts(db) == before
    api.log_trade(_divergent(api, ticker="ZZEXEC", identity=_identity(),
                             preview_id=preview["preview_id"]))
    after = _counts(db)
    assert (after["trades"], after["identities"], after["events"]) == (
        before["trades"] + 1, before["identities"] + 1, before["events"] + 1)


def test_divergenza_fallita_annulla_anche_il_trade(env, monkeypatch):
    db, api = env
    preview = api.preview_trade(_divergent(api, ticker="ZZEXEC", identity=_identity()))
    before = _counts(db)

    def boom(self, *a, **k):
        raise ValueError("guasto sintetico nella divergenza")
    monkeypatch.setattr(memory_db.MemoryDB, "record_manual_trade_divergence", boom)
    with pytest.raises(api.HTTPException):
        api.log_trade(_divergent(api, ticker="ZZEXEC", identity=_identity(),
                                 preview_id=preview["preview_id"]))
    assert _counts(db) == before


def test_divergenza_con_motivo_cambiato_dopo_anteprima_rifiutata(env):
    db, api = env
    preview = api.preview_trade(_divergent(api))
    before = _counts(db)
    with pytest.raises(api.HTTPException) as exc:
        api.log_trade(_divergent(api, reason="altro motivo", preview_id=preview["preview_id"]))
    assert exc.value.status_code == 409 and _counts(db) == before


def test_divergenza_su_proposta_non_bloccata_rifiutata_in_anteprima(env):
    db, api = env
    before = _counts(db)
    with pytest.raises(api.HTTPException):
        api.preview_trade(api.TradeIn(ticker="ZZPROP", action="BUY", quantita=1, prezzo=10,
                                      valuta="EUR", senza_decisione=True,
                                      manual_divergence={"decision_id": 1, "reason": "x"}))
    assert _counts(db) == before


def test_divergenza_richiede_trade_manuale(env):
    db, api = env
    with pytest.raises(api.HTTPException):
        api.preview_trade(api.TradeIn(ticker="ZZPROP", action="BUY", quantita=1, prezzo=10,
                                      valuta="EUR", linked_decision_id=1,
                                      manual_divergence={"decision_id": 2, "reason": "x"}))


# ── Seguito revisione (REV_G9a) ──────────────────────────────────────────────

def _blocked_today_late(db):
    from datetime import date
    with db._conn() as conn:
        return conn.execute(
            "INSERT INTO decisions(timestamp,action,ticker,status,assessment_status,proposal_ticker) "
            "VALUES (?,'BUY','ZZLATE','PENDING','BLOCKED','ZZLATE')",
            (date.today().isoformat() + "T23:59:59",)).lastrowid


def test_divergenza_solo_giorno_stessa_data_della_proposta_accettata(env):
    # trade «solo giorno»: l'ora 12:00 e' una convenzione, l'ordine nella giornata
    # non e' noto -> confronto per sola data (come il legame esplicito)
    from datetime import date
    db, api = env
    did = _blocked_today_late(db)
    before = _counts(db)
    preview = api.preview_trade(api.TradeIn(
        ticker="ZZLATE", action="BUY", quantita=1, prezzo=10, valuta="EUR", senza_decisione=True,
        data=date.today().isoformat(), manual_divergence={"decision_id": did, "reason": "stesso giorno"}))
    assert preview["manual_divergence"]["decision_id"] == did
    assert _counts(db) == before


def test_divergenza_con_ora_misurata_prima_della_proposta_resta_rifiutata(env):
    from datetime import date
    db, api = env
    did = _blocked_today_late(db)
    with pytest.raises(api.HTTPException):
        api.preview_trade(api.TradeIn(
            ticker="ZZLATE", action="BUY", quantita=1, prezzo=10, valuta="EUR", senza_decisione=True,
            data=date.today().isoformat() + "T00:00:01",
            manual_divergence={"decision_id": did, "reason": "prima"}))


def test_endpoint_verifica_isin_autonomo_rimosso(env):
    _db, api = env
    routes = {(getattr(r, "path", ""), m) for r in api.app.routes for m in (getattr(r, "methods", None) or ())}
    assert ("/instrument-identities/verify", "POST") not in routes
    assert ("/decisions/{decision_id}/manual-divergence", "POST") in routes


def test_endpoint_divergenza_su_trade_esistente_atomico_e_idempotente(env):
    db, api = env
    out = api.log_trade(api.TradeIn(ticker="ZZPROP", action="BUY", quantita=1, prezzo=10,
                                    valuta="EUR", senza_decisione=True))
    tid = int(out["trade_id"])
    before = _counts(db)
    first = api.record_manual_trade_divergence(2, api.ManualTradeDivergenceIn(trade_id=tid, reason="motivo"))
    after = _counts(db)
    assert after["events"] == before["events"] + 1
    again = api.record_manual_trade_divergence(2, api.ManualTradeDivergenceIn(trade_id=tid, reason="motivo"))
    assert again["event"]["id"] == first["event"]["id"] and _counts(db) == after
    for bad in ((2, "altro motivo"), (1, "motivo")):  # contenuto diverso; proposta non BLOCKED
        with pytest.raises(api.HTTPException) as exc:
            api.record_manual_trade_divergence(bad[0], api.ManualTradeDivergenceIn(trade_id=tid, reason=bad[1]))
        assert exc.value.status_code == 409
    assert _counts(db) == after


def test_db_bloccato_503_bilingue_e_anteprima_non_consumata(env):
    import sqlite3
    db, api = env
    preview = api.preview_trade(_linked(api))
    before = _counts(db)
    lock = sqlite3.connect(db.db_path, timeout=0)
    lock.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(api.HTTPException) as exc:
            api.log_trade(_linked(api, preview_id=preview["preview_id"]))
    finally:
        lock.rollback(); lock.close()
    assert exc.value.status_code == 503
    assert "database is locked" not in str(exc.value.detail)
    assert _counts(db) == before
    out = api.log_trade(_linked(api, preview_id=preview["preview_id"]))  # stessa anteprima, ora passa
    assert int(out["trade_id"]) > 0 and _counts(db)["trades"] == before["trades"] + 1
