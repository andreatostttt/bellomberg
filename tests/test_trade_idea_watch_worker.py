"""Worker dei trigger Trade Idea /4 (TI-RESEARCH-PIPELINE, Opus 5.5).

DB in tmp_path, quotazioni ed email finte, nessuna rete. Ticker e prezzi inventati.
"""
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bellomberg.core.trade_idea_watch import Quote
from bellomberg.market_data import trade_idea_watch_worker as worker
from bellomberg.storage import trade_idea_store, trade_idea_watch_store
from bellomberg.storage.trade_idea_watch_store import SCHEMA_MISSING

TODAY = date(2031, 3, 12)          # mercoledi'
POLICY = {"execution_policy": "trade-idea-research/4", "analysis_mode": "fundamentals_research_v1"}
REF = {"status": "ready", "price": "123.45", "price_asof": "2031-03-10", "currency": "EUR",
       "source": "yfinance daily Close (not an intraday quote)"}

BASE_SCHEMA = """
CREATE TABLE memos(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, title TEXT,
                   full_markdown TEXT, pdf_path TEXT, appendix_path TEXT, dcf_files TEXT,
                   notes TEXT, output_language TEXT);
CREATE TABLE decisions(id INTEGER PRIMARY KEY, memo_id INTEGER, timestamp TEXT NOT NULL,
                       action TEXT NOT NULL, ticker TEXT, eur_amount REAL, timing TEXT,
                       confidence TEXT, rationale TEXT, status TEXT DEFAULT 'PENDING',
                       pm_feedback TEXT, veto INTEGER DEFAULT 0, veto_reason TEXT,
                       veto_at TEXT, veto_revoked_at TEXT, outcome_pct REAL,
                       outcome_eur REAL, outcome_notes TEXT, closed_at TEXT,
                       archive_override INTEGER);
CREATE TABLE position_prices(id INTEGER PRIMARY KEY, ticker TEXT NOT NULL,
                             prezzo REAL NOT NULL, valuta TEXT, source TEXT, timestamp TEXT);
"""


def trig(kind, value, what="motivo inventato"):
    body = {"kind": kind, "date": None, "price_level": None, "condition": None, "what": what}
    body[{"date": "date", "price": "price_level", "condition": "condition"}[kind]] = value
    return body


def make_db(tmp_path, *, watch_schema=True):
    path = tmp_path / "isolated.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
        trade_idea_store.ensure_schema(conn)
        if watch_schema:
            trade_idea_watch_store.ensure_schema(conn)
    return path


_SEQ = {"n": 0}


def add_run(path, *, ticker="ZZTEST", triggers=None, judgment="watch", created="2031-03-01T10:00:00.000000Z",
            reference=REF, status="completed", currency="EUR"):
    _SEQ["n"] += 1
    n = _SEQ["n"]
    run_id = f"run-zz-{n}"
    triggers = triggers if triggers is not None else [trig("date", "2031-06-01"), trig("price", 150.0)]
    result = {"judgment": judgment, "review_triggers": triggers}
    progress = {"candidate_quote_receipts": {"final": reference}} if reference else {}
    with sqlite3.connect(path) as conn:
        memo = conn.execute("INSERT INTO memos(timestamp,title) VALUES(?,?)", (created, f"memo {n}")).lastrowid
        dec = conn.execute("INSERT INTO decisions(memo_id,timestamp,action,ticker,rationale,status) "
                           "VALUES(?,?,?,?,?,'PENDING')", (memo, created, "RESEARCH", ticker, "origine")).lastrowid
        final = status not in ("accepted", "running")
        conn.execute(
            "INSERT INTO trade_idea_runs(id,idempotency_key,request_sha256,request_json,ticker,currency,"
            "language,view_text,view_origin,models_json,catalog_snapshot_json,budget_limit_usd,context_json,"
            "technical_status,phase,progress_json,result_json,worker_token,destination_kind,"
            "destination_decision_id,memo_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'it','vista','pm','{}','{}','1','{}',?,?,?,?,?,?,?,?,?,?)",
            (run_id, f"key-{n}", "0" * 64, json.dumps(POLICY), ticker, currency, status, "done",
             json.dumps(progress), json.dumps(result) if final else None,
             None if status != "running" else "tok",
             "research" if final else "none", dec if final else None, memo if final else None,
             created, created))
    return run_id, dec


def q(price, *, ticker="ZZTEST", asof="2031-03-11", status="ok", currency="EUR"):
    return Quote(ticker=ticker, price=Decimal(price), currency=currency, asof=asof,
                 source="finta", status=status, reason=None if status == "ok" else "finta")


class FakeQuotes:
    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def __call__(self, ticker, *, today, currency):
        self.calls.append((ticker, today, currency))
        value = self.prices[ticker]
        return value if isinstance(value, Quote) else q(value, ticker=ticker, currency=currency)


class FakeMail:
    def __init__(self, outcome=True):
        self.outcome = outcome
        self.sent = []

    def __call__(self, subject, body):
        self.sent.append((subject, body))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def giro(path, quotes, mail, today=TODAY, **kw):
    return worker.run_due(db_path=path, today=today, quote_fn=quotes, send_email=mail, **kw)


def rows(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM trade_idea_watch_triggers ORDER BY id")]


def decisions(path, *, origin=False):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out = [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id")]
    return out if origin else [d for d in out if d["rationale"] != "origine"]


@pytest.fixture(autouse=True)
def niente_rete(monkeypatch):
    import socket

    def _vietato(*a, **k):
        pytest.fail("rete vietata nei test del worker watch")
    monkeypatch.setattr(socket.socket, "connect", _vietato)


# ---- end-to-end ---------------------------------------------------------------

def test_end_to_end_import_scatto_voce_email_e_niente_doppioni(tmp_path):
    path = make_db(tmp_path)
    run_id, origin = add_run(path)
    mail = FakeMail()

    out1 = giro(path, FakeQuotes({"ZZTEST": "130.00"}), mail)
    assert out1["status"] == "ok", out1
    assert out1["ingest"]["inserted"] == 2 and out1["verdicts"] == {"wait": 2}
    assert decisions(path) == [] and mail.sent == []

    out2 = giro(path, FakeQuotes({"ZZTEST": "151.00"}), mail)
    assert out2["status"] == "ok", out2
    (entry,) = out2["fired"]
    assert entry["status"] == "fired" and entry["email"]["status"] == "sent"
    (voce,) = decisions(path)
    assert (voce["action"], voce["status"], voce["ticker"], voce["eur_amount"]) == \
        ("RESEARCH", "PENDING", "ZZTEST", None)
    assert voce["id"] == entry["decision_id"] and voce["rationale"].startswith("[TRIGGER]")
    assert "151" in voce["timing"] and "150" in voce["timing"]
    assert len(mail.sent) == 1 and f"#{voce['id']}" in mail.sent[0][1] and "ZZTEST" in mail.sent[0][0]
    by_kind = {r["kind"]: r for r in rows(path)}
    assert by_kind["price"]["status"] == "fired" and by_kind["price"]["email_status"] == "sent"
    assert by_kind["price"]["fired_decision_id"] == voce["id"]
    assert json.loads(by_kind["price"]["fired_observation_json"])["price"] == "151.00"
    assert by_kind["date"]["status"] == "active"

    out3 = giro(path, FakeQuotes({"ZZTEST": "152.00"}), mail)
    assert out3["fired"] == [] and out3["ingest"]["inserted"] == 0
    assert len(decisions(path)) == 1 and len(mail.sent) == 1

    # la data scatta il giorno stesso
    out4 = giro(path, FakeQuotes({}), mail, today=date(2031, 6, 1))
    assert [e["status"] for e in out4["fired"]] == ["fired"]
    assert len(decisions(path)) == 2 and len(mail.sent) == 2
    assert all(r["status"] == "fired" for r in rows(path))
    # la RESEARCH d'origine non e' toccata
    assert [d["status"] for d in decisions(path, origin=True) if d["id"] == origin] == ["PENDING"]


def test_data_il_giorno_prima_non_scatta(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-06-01")])
    out = giro(path, FakeQuotes({}), FakeMail(), today=date(2031, 5, 31))
    assert out["fired"] == [] and decisions(path) == []


def test_promemoria_condizione_diventa_voce(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-06-01"), trig("condition", "esito gara inventata")])
    mail = FakeMail()
    out = giro(path, FakeQuotes({}), mail, today=date(2031, 6, 1))
    (entry,) = out["fired"]
    assert len(entry["row_ids"]) == 2
    (voce,) = decisions(path)
    assert "esito gara inventata" in voce["timing"]


# ---- buchi dichiarati -----------------------------------------------------------

def test_schema_assente_dichiarato_e_tabella_non_creata(tmp_path):
    path = make_db(tmp_path, watch_schema=False)
    add_run(path)
    out = giro(path, FakeQuotes({}), FakeMail())
    assert out["status"] == "errore" and out["reason"] == SCHEMA_MISSING
    with sqlite3.connect(path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "trade_idea_watch_triggers" not in names
    assert worker.summary_lines(out) == [f"  [WATCH] KO dichiarato: {SCHEMA_MISSING}"]


def test_db_assente_dichiarato(tmp_path):
    out = giro(tmp_path / "manca.db", FakeQuotes({}), FakeMail())
    assert out["status"] == "errore" and "FileNotFoundError" in out["reason"]
    assert not (tmp_path / "manca.db").exists()


def test_prezzo_stale_solo_dichiarato(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    out = giro(path, FakeQuotes({"ZZTEST": q("160.00", asof="2031-03-05", status="stale")}), FakeMail())
    assert out["status"] == "attention" and out["fired"] == []
    assert [i["verdict"] for i in out["not_ok"]] == ["stale"]
    assert decisions(path) == []
    assert rows(path)[0]["last_check_status"] == "stale"


def test_ticker_con_run_attiva_rinviato(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    add_run(path, triggers=[], status="accepted", created="2031-03-11T10:00:00.000000Z")
    mail = FakeMail()
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), mail)
    assert out["fired"] == [] and out["not_ok"] == []
    (d,) = out["deferred"]
    assert (d["ticker"], d["days"], d["stuck"], d["state"]) == ("ZZTEST", 1, False, "in_corso")
    assert out["status"] == "ok"          # rinvio breve: normale, ma scritto nel log
    assert "rinviati: run Trade Idea" in "\n".join(worker.summary_lines(out))
    assert decisions(path) == [] and mail.sent == []
    assert rows(path)[0]["status"] == "active"


def test_run_bloccata_oltre_due_giorni_attenzione(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-01")])
    run_id, _ = add_run(path, triggers=[], status="running", created="2031-03-02T10:00:00.000000Z")
    out = giro(path, FakeQuotes({}), FakeMail(), today=date(2031, 3, 4))
    assert out["status"] == "ok" and out["deferred"][0]["days"] == 2
    out = giro(path, FakeQuotes({}), FakeMail(), today=date(2031, 3, 5))
    assert out["status"] == "attention" and out["deferred"][0]["stuck"] is True
    text = "\n".join(worker.summary_lines(out))
    assert f"ATTENZIONE ZZTEST: run Trade Idea {run_id} bloccata in running da 3 giorni" in text
    assert decisions(path) == []


def test_veto_annulla_niente_voce(tmp_path):
    path = make_db(tmp_path)
    _, origin = add_run(path, triggers=[trig("price", 150.0)])
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE decisions SET veto=1 WHERE id=?", (origin,))
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), FakeMail())
    assert out["cancelled_by_veto"] == 1 and out["fired"] == [] and decisions(path) == []
    assert rows(path)[0]["status"] == "cancelled"


def test_expired_non_annulla(tmp_path):
    path = make_db(tmp_path)
    _, origin = add_run(path, triggers=[trig("price", 150.0)])
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE decisions SET status='EXPIRED' WHERE id=?", (origin,))
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), FakeMail())
    assert out["cancelled_by_veto"] == 0 and len(decisions(path)) == 1


def test_run_nuova_supera_la_vecchia(tmp_path):
    path = make_db(tmp_path)
    old, _ = add_run(path, triggers=[trig("price", 150.0)])
    new, _ = add_run(path, triggers=[trig("price", 170.0)], created="2031-03-05T10:00:00.000000Z")
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), FakeMail())
    assert out["superseded"] == 1
    status = {r["run_id"]: r["status"] for r in rows(path)}
    assert status == {old: "superseded", new: "active"}
    assert out["fired"] == [] and decisions(path) == []


# ---- email ---------------------------------------------------------------------

def test_email_fallita_registrata_e_ritentata(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), FakeMail(False))
    assert out["status"] == "attention"
    assert out["fired"][0]["email"]["status"] == "failed" and "False" in out["fired"][0]["email"]["error"]
    r = rows(path)[0]
    assert (r["email_status"], r["email_attempts"]) == ("failed", 1)
    mail = FakeMail()
    out2 = giro(path, FakeQuotes({}), mail)
    assert [e["status"] for e in out2["email_retry"]] == ["sent"] and len(mail.sent) == 1
    assert "nuovo invio" in mail.sent[0][0]
    assert len(decisions(path)) == 1
    assert (rows(path)[0]["email_status"], rows(path)[0]["email_attempts"]) == ("sent", 2)


def test_email_eccezione_non_annulla_la_voce_e_tetto_tentativi(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    boom = FakeMail(OSError("smtp finto"))
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), boom)
    assert out["fired"][0]["email"]["error"] == "send_email ha sollevato OSError"
    assert len(decisions(path)) == 1
    for _ in range(4):
        out = giro(path, FakeQuotes({}), boom)
    assert len(boom.sent) == worker.EMAIL_MAX_ATTEMPTS
    assert rows(path)[0]["email_attempts"] == worker.EMAIL_MAX_ATTEMPTS


def test_email_al_tetto_dichiarata_a_ogni_giro_senza_nuovi_tentativi(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    giu = FakeMail(False)
    giro(path, FakeQuotes({"ZZTEST": "160.00"}), giu)
    giro(path, FakeQuotes({}), giu)
    out = giro(path, FakeQuotes({}), giu)                 # terzo tentativo: tetto raggiunto
    assert len(giu.sent) == worker.EMAIL_MAX_ATTEMPTS
    (voce,) = decisions(path)
    (row_id,) = [r["id"] for r in rows(path)]
    for _ in range(2):                                    # i giri dopo: dichiarato, mai ritentato
        out = giro(path, FakeQuotes({}), giu)
        assert out["status"] == "attention" and out["email_retry"] == []
        assert [e["id"] for e in out["email_exhausted"]] == [row_id]
        text = "\n".join(worker.summary_lines(out))
        assert (f"ATTENZIONE ZZTEST: avviso del trigger #{row_id} non consegnato dopo 3 tentativi: "
                f"controlla la voce #{voce['id']} in coda decisioni") in text
    assert len(giu.sent) == worker.EMAIL_MAX_ATTEMPTS


def test_email_non_configurata_dichiarata(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), FakeMail("not_configured"))
    assert out["fired"][0]["email"]["status"] == "not_configured" and out["status"] == "attention"
    assert rows(path)[0]["email_status"] == "not_configured"


def test_default_send_email_non_configurata(monkeypatch):
    from bellomberg.reporting import email_sender
    monkeypatch.setattr(email_sender, "EMAIL_FROM", "")
    monkeypatch.setattr(email_sender, "EMAIL_PASSWORD", "")
    monkeypatch.setattr(email_sender, "EMAIL_TO", "")
    assert worker.default_send_email("it")("ogg", "corpo") == "not_configured"


def test_default_send_email_passa_oggetto_e_lingua(monkeypatch):
    from bellomberg.reporting import email_sender
    seen = {}
    monkeypatch.setattr(email_sender, "email_configurata", lambda: True)
    monkeypatch.setattr(email_sender, "invia_briefing_email",
                        lambda body, oggetto=None, *, language=None: seen.update(b=body, o=oggetto, l=language) or True)
    assert worker.default_send_email("en")("ogg", "corpo") is True
    assert seen == {"b": "corpo", "o": "ogg", "l": "en"}


# ---- limiti di giro e cache -------------------------------------------------------

def test_limite_ticker_dichiarato(tmp_path):
    path = make_db(tmp_path)
    add_run(path, ticker="ZZAAA", triggers=[trig("price", 150.0)])
    add_run(path, ticker="ZZBBB", triggers=[trig("price", 150.0)])
    quotes = FakeQuotes({"ZZAAA": "140.00", "ZZBBB": "140.00"})
    out = giro(path, quotes, FakeMail(), max_tickers=1)
    assert [c[0] for c in quotes.calls] == ["ZZAAA"]
    assert "limite di 1 ticker" in out["quotes"]["ZZBBB"]["reason"]
    assert [(i["ticker"], i["verdict"]) for i in out["not_ok"]] == [("ZZBBB", "unavailable")]


def test_tempo_esaurito_dichiarato(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    ticks = iter([0.0, 999.0, 999.0, 999.0])
    quotes = FakeQuotes({"ZZTEST": "160.00"})
    out = giro(path, quotes, FakeMail(), time_budget_s=10, clock=lambda: next(ticks))
    assert quotes.calls == [] and "tempo del giro esaurito" in out["quotes"]["ZZTEST"]["reason"]
    assert decisions(path) == []


def test_cache_del_giorno_evita_il_secondo_scarico(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    cache = tmp_path / "cache.json"
    quotes = FakeQuotes({"ZZTEST": "140.00"})
    giro(path, quotes, FakeMail(), cache_path=cache)
    out = giro(path, quotes, FakeMail(), cache_path=cache)
    assert len(quotes.calls) == 1 and out["quotes"]["ZZTEST"]["cached"] is True
    giro(path, quotes, FakeMail(), cache_path=cache, today=date(2031, 3, 13))
    assert len(quotes.calls) == 2


def test_cache_scade_e_non_riusa_orologio_indietro(tmp_path):
    cache_path = tmp_path / "c.json"
    t0 = datetime(2031, 3, 12, 9, 0, tzinfo=timezone.utc)
    c = worker.QuoteCache(cache_path, today=TODAY, now=lambda: t0)
    c.put(q("140.00"))
    c.save()
    assert worker.QuoteCache(cache_path, today=TODAY, now=lambda: t0 + timedelta(minutes=5)).get("ZZTEST", "EUR") is not None
    assert worker.QuoteCache(cache_path, today=TODAY, now=lambda: t0 + timedelta(hours=2)).get("ZZTEST", "EUR") is None
    assert worker.QuoteCache(cache_path, today=TODAY, now=lambda: t0 - timedelta(minutes=5)).get("ZZTEST", "EUR") is None
    assert worker.QuoteCache(cache_path, today=TODAY, now=lambda: t0).get("ZZTEST", "USD") is None


def test_cache_non_conserva_gli_errori_e_file_rotto_dichiarato(tmp_path):
    cache_path = tmp_path / "c.json"
    c = worker.QuoteCache(cache_path, today=TODAY)
    c.put(Quote("ZZTEST", None, None, None, None, "source_unavailable", "giu'"))
    c.save()
    assert not cache_path.exists()
    cache_path.write_text("{rotto", encoding="utf-8")
    assert "illeggibile" in worker.QuoteCache(cache_path, today=TODAY).error


# ---- fetch_quote: get_price_live, mai position_prices --------------------------

def _envelope(**data):
    base = {"ticker": "ZZTEST", "px": 151.25, "price_asof": "2031-03-11 00:00:00+01:00",
            "source": "yfinance daily Close (not an intraday quote)"}
    base.update(data)
    return {"_source": "finta", "data": base}


@pytest.fixture
def dispatch(monkeypatch):
    from bellomberg.agents import chat_tools
    box = {"calls": []}

    def fake(tool, payload, caller=None, **kw):
        box["calls"].append((tool, payload, caller))
        return box["value"]
    monkeypatch.setattr(chat_tools, "dispatch", fake)
    return box


def test_fetch_quote_chiusura_con_data_e_valuta_della_run(dispatch):
    dispatch["value"] = _envelope()
    quote = worker.fetch_quote("ZZTEST", today=TODAY, currency="EUR")
    assert dispatch["calls"] == [("get_price_live", {"ticker": "ZZTEST"}, "trade-idea-watch")]
    assert (quote.status, quote.price, quote.asof, quote.currency) == ("ok", Decimal("151.25"), "2031-03-11", "EUR")
    assert "valuta dalla run" in quote.source and quote.reason is None


@pytest.mark.parametrize("value,status,word", [
    ({"error": "giu'"}, "source_unavailable", "in errore"),
    (_envelope(ticker="ZZALTRO"), "unavailable", "identita'"),
    (_envelope(source="altro"), "unavailable", "fonte"),
    (_envelope(px=0), "unavailable", "prezzo"),
    (_envelope(price_asof="2031-03-05"), "stale", "freschezza stale"),
    (_envelope(price_asof=""), "data_missing", "freschezza data_missing"),
])
def test_fetch_quote_buchi_dichiarati(dispatch, value, status, word):
    dispatch["value"] = value
    quote = worker.fetch_quote("ZZTEST", today=TODAY, currency="EUR")
    assert quote.status == status and word in quote.reason


def test_fetch_quote_senza_valuta_non_chiama_il_tool(dispatch):
    quote = worker.fetch_quote("ZZTEST", today=TODAY, currency=None)
    assert quote.status == "unavailable" and dispatch["calls"] == []


def test_fetch_quote_timeout_dichiarato(monkeypatch):
    import threading
    from bellomberg.agents import chat_tools
    stop = threading.Event()
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: stop.wait(5))
    quote = worker.fetch_quote("ZZTEST", today=TODAY, currency="EUR", timeout_s=0.05)
    stop.set()
    assert quote.status == "source_unavailable" and "TimeoutError" in quote.reason


def test_giro_con_fetch_quote_vero_non_scrive_position_prices(tmp_path, dispatch):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    dispatch["value"] = _envelope()
    out = worker.run_due(db_path=path, today=TODAY, send_email=FakeMail())
    assert out["fired"][0]["status"] == "fired"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM position_prices").fetchone()[0] == 0


# ---- CLI e cablaggio nel PriceUpdater ------------------------------------------

def test_cli_due_e_status(tmp_path, monkeypatch, capsys):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2000-01-03")])
    log = tmp_path / "w.jsonl"
    monkeypatch.setattr(worker, "default_send_email", lambda language: FakeMail())
    code = worker.main(["--db", str(path), "--log", str(log), "--cache", str(tmp_path / "c.json"), "--due"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and out["fired"][0]["status"] == "fired"
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    code = worker.main(["--db", str(path), "--log", str(log), "--status"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and out["by_status"] == {"fired": 1}
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_cli_schema_assente_esce_1(tmp_path, capsys):
    path = make_db(tmp_path, watch_schema=False)
    code = worker.main(["--db", str(path), "--log", str(tmp_path / "w.jsonl"), "--status"])
    out = json.loads(capsys.readouterr().out.strip())
    assert code == 1 and SCHEMA_MISSING in out["reason"]


def test_run_from_updater_usa_i_percorsi_dati(tmp_path, monkeypatch):
    from bellomberg.core import paths
    path = make_db(tmp_path)
    monkeypatch.setattr(paths, "SQLITE_PATH", path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    seen = {}
    monkeypatch.setattr(worker, "run_due", lambda **kw: seen.update(kw) or {"status": "ok"})
    assert worker.run_from_updater() == {"status": "ok"}
    assert seen == {"db_path": path, "cache_path": tmp_path / "trade_idea_watch_quotes.json"}
    assert json.loads((tmp_path / "trade_idea_watch.jsonl").read_text(encoding="utf-8")) == {"status": "ok"}


def _updater(monkeypatch, watch):
    from bellomberg.cli import price_updater
    from bellomberg.market_data import iv_history
    monkeypatch.setattr(price_updater, "MemoryDB", lambda: object())
    seen = []
    monkeypatch.setattr(price_updater, "update_all_prices",
                        lambda db, **kw: seen.append("prezzi") or {"updated": 0, "failed": 0})
    monkeypatch.setattr(iv_history, "save_daily_snapshot", lambda: seen.append("iv") or {})
    monkeypatch.setattr(worker, "run_from_updater", watch)
    monkeypatch.setattr(sys, "argv", ["price_updater.py", "--no-ibkr", "--quiet"])
    price_updater.main()
    return seen


def test_price_updater_stampa_il_giro_anche_quiet(monkeypatch, capsys):
    calls = []
    _updater(monkeypatch, lambda: calls.append(1) or {
        "status": "ok", "checked": 2, "ingest": {"inserted": 0}, "fired": [
            {"status": "fired", "ticker": "ZZTEST", "decision_id": 7, "row_ids": [3],
             "email": {"status": "sent"}}]})
    printed = capsys.readouterr().out
    assert calls == [1]
    assert "[WATCH] ok: trigger controllati 2" in printed
    assert "[WATCH] ZZTEST: voce RESEARCH #7 (trigger [3]), email sent" in printed


def test_price_updater_errore_watch_dichiarato_solo_tipo(monkeypatch, capsys):
    def boom():
        raise ConnectionError("https://esempio.invalid/?apikey=SEGRETO")
    seen = _updater(monkeypatch, boom)       # nessuna eccezione esce da main()
    printed = capsys.readouterr().out
    assert seen == ["prezzi", "iv"], "prezzi e IV girano comunque"
    assert "price update starting" in printed
    assert "[WATCH] KO dichiarato (prezzi non toccati): ConnectionError" in printed
    assert "SEGRETO" not in printed


def test_summary_lines_dichiara_i_buchi():
    lines = worker.summary_lines({
        "status": "attention", "checked": 1, "ingest": {"inserted": 0, "errors": [
            {"run_id": "r1", "error": "ValueError", "reason": "trigger non valido"}]},
        "fired": [{"status": "deferred", "ticker": "ZZTEST", "row_ids": [1], "reason": "run attiva"}],
        "not_ok": [{"row_id": 2, "ticker": "ZZTEST", "verdict": "stale", "reason": "vecchia"}],
        "email_retry": [{"status": "failed", "decision_id": 9, "error": "giu'"}],
        "cache_error": "cache prezzi non salvata (OSError)"})
    text = "\n".join(lines)
    for word in ("deferred dichiarato: run attiva", "#2 ZZTEST stale: vecchia",
                 "import run r1 KO dichiarato", "nuovo invio email voce #9: failed", "cache prezzi non salvata"):
        assert word in text


# ---- etichetta della barra (decisione PM 04/10: intraday ammesso ma DICHIARATO) ----

@pytest.mark.parametrize("asof,label", [
    ("2031-03-11 00:00:00+01:00", "chiusura del 11/03"),
    ("2031-03-12 09:42:00-04:00", "prezzo intraday delle 14:42 ora di Roma (barra non chiusa)"),
    ("2031-03-12 00:00:00+01:00", "barra di oggi: potrebbe non essere la chiusura"),
])
def test_etichetta_barra_in_osservazione_voce_ed_email(tmp_path, dispatch, asof, label):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    dispatch["value"] = _envelope(price_asof=asof)
    mail = FakeMail()
    out = worker.run_due(db_path=path, today=TODAY, send_email=mail)
    assert out["fired"][0]["status"] == "fired", out
    (voce,) = decisions(path)
    assert label in voce["timing"] and label in voce["rationale"]
    assert label in mail.sent[0][1]
    obs = json.loads(rows(path)[0]["fired_observation_json"])
    assert obs["bar_label"] == label and label not in obs["source"]
    for testo in (voce["timing"], voce["rationale"], mail.sent[0][1]):
        assert "not an intraday" not in testo and "yfinance" not in testo   # fonte grezza solo nel JSON
        for other in ("chiusura del", "prezzo intraday", "barra di oggi"):
            if not label.startswith(other):
                assert other not in testo


def test_etichetta_barra_segue_la_lingua_del_giro(tmp_path, dispatch):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("price", 150.0)])
    dispatch["value"] = _envelope(price_asof="2031-03-12 00:00:00+01:00")
    mail = FakeMail()
    worker.run_due(db_path=path, today=TODAY, send_email=mail, language="en")
    (voce,) = decisions(path)
    assert "today's bar: may not be the close" in voce["timing"] and "barra di oggi" not in voce["timing"]
    assert "today's bar" in mail.sent[0][1]


def test_etichetta_barra_in_inglese():
    assert worker.bar_label("2031-03-12 09:05:00-04:00", TODAY, language="en") == \
        "intraday price at 14:05 Rome time (bar not closed)"
    # orario senza fuso: mai un fuso indovinato -> niente orario
    assert worker.bar_label("2031-03-12 09:05:00", TODAY) == "barra di oggi: potrebbe non essere la chiusura"
    assert worker.bar_label("2031-03-10", TODAY, language="en") == "close of 10/03"
    assert worker.bar_label("", TODAY) == "data della barra n.d."


# ---- rilievi RV-P-A / RV-P-B ------------------------------------------------------

def test_trigger_non_sorvegliati_dichiarati_a_ogni_giro(tmp_path):
    path = make_db(tmp_path)
    add_run(path, reference=None, triggers=[trig("price", 150.0), trig("condition", "esito gara inventata")])
    for day in (12, 13):
        out = giro(path, FakeQuotes({}), FakeMail(), today=date(2031, 3, day))
        assert out["status"] == "attention"
        assert sorted(r["status"] for r in out["attention_rows"]) == ["blocked", "undated"]
        (line,) = [l for l in worker.summary_lines(out) if "ATTENZIONE" in l]
        assert "ZZTEST: trigger non sorvegliati" in line and "price blocked" in line and "condition undated" in line


def test_veto_revocato_dichiarato(tmp_path):
    path = make_db(tmp_path)
    _, origin = add_run(path, triggers=[trig("price", 150.0)])
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE decisions SET veto=1 WHERE id=?", (origin,))
    giro(path, FakeQuotes({"ZZTEST": "140.00"}), FakeMail())
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE decisions SET veto=0, veto_revoked_at='2031-03-12T11:00:00' WHERE id=?", (origin,))
    out = giro(path, FakeQuotes({"ZZTEST": "160.00"}), FakeMail())
    assert out["status"] == "attention" and decisions(path) == []
    assert "veto_revoked" in "\n".join(worker.summary_lines(out))


def _fire_senza_email(path, kinds):
    """Simula un giro caduto fra fire e invio: righe fired, email not_attempted."""
    from bellomberg.core.trade_idea_watch import decision_fields
    store = trade_idea_watch_store.TradeIdeaWatchStore(path)
    store.ingest_pending(today=TODAY)
    (run_id,) = {r.run_id for r in store.active()}
    chosen = [r for r in store.active() if r.kind in kinds]
    fired = [r for r in chosen if r.kind != "condition"]
    reminders = [r for r in chosen if r.kind == "condition"]
    fields = decision_fields(store.run_info(run_id), fired, reminders, {}, language="it")
    return store, store.fire(run_id, [r.id for r in chosen], fields, {})


def test_ritento_di_un_promemoria_mai_tentato_dice_il_vero(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-06-01"), trig("condition", "esito gara inventata")])
    _, decision_id = _fire_senza_email(path, {"condition"})
    mail = FakeMail()
    out = giro(path, FakeQuotes({}), mail)
    (subject, body), = mail.sent
    assert "promemoria" in subject and "scattato" not in subject and subject.endswith("(in ritardo)")
    assert "Avviso in ritardo: non era partito" in body and "non e' riuscito" not in body
    assert "esito gara inventata" in body and f"#{decision_id}" in body
    assert out["email_retry"][0]["previous"] == {r["id"]: "not_attempted" for r in rows(path) if r["kind"] == "condition"}


def test_ritento_dopo_invio_rimasto_appeso_avvisa_del_possibile_doppione(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-01")])
    store, _ = _fire_senza_email(path, {"date"})
    (row_id,) = [r["id"] for r in rows(path)]
    assert store.claim_email([row_id]) is not None          # un giro prende l'invio e cade
    mail = FakeMail()
    giro(path, FakeQuotes({}), mail)
    assert mail.sent == []                                    # presa viva: nessun doppione
    late = trade_idea_watch_store.TradeIdeaWatchStore(
        path, clock=lambda: datetime.now(timezone.utc) + timedelta(seconds=worker.EMAIL_STALE_AFTER_S + 5))
    worker_out = worker.run_due(db_path=path, today=TODAY, quote_fn=FakeQuotes({}), send_email=mail,
                                store_clock=late._clock)
    (subject, body), = mail.sent
    assert subject.endswith("(nuovo invio)") and "potresti riceverlo due volte" in body
    assert worker_out["email_retry"][0]["status"] == "sent"


def test_giri_sovrapposti_una_sola_email(tmp_path):
    """RV-P-A 2: mentre il giro A sta inviando, il giro B non manda un secondo avviso."""
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-01")])
    inner = {}
    mail_b = FakeMail()

    def mail_a(subject, body):
        inner["out"] = giro(path, FakeQuotes({}), mail_b)    # il giro B parte durante l'invio di A
        return True
    out_a = giro(path, FakeQuotes({}), mail_a)
    assert out_a["fired"][0]["email"]["status"] == "sent"
    assert mail_b.sent == [] and inner["out"]["email_retry"] == []
    assert len(decisions(path)) == 1 and rows(path)[0]["email_attempts"] == 1


def test_email_fuori_budget_rinviata_senza_consumare_tentativi(tmp_path):
    path = make_db(tmp_path)
    for i in range(3):
        add_run(path, ticker=f"ZZT{i}", triggers=[trig("date", "2031-03-01")])
    now = {"t": 0.0}
    mail = FakeMail()

    def slow(subject, body):
        now["t"] += 30.0
        return mail(subject, body)
    out = giro(path, FakeQuotes({}), slow, time_budget_s=90, email_reserve_s=35, clock=lambda: now["t"])
    assert [e["email"]["status"] for e in out["fired"]] == ["sent", "sent", "postponed"]
    assert "tempo del giro esaurito" in out["fired"][2]["email"]["reason"]
    assert out["status"] == "attention"
    last = rows(path)[2]
    assert (last["email_status"], last["email_attempts"]) == ("not_attempted", 0)
    out2 = giro(path, FakeQuotes({}), mail)
    assert [r["status"] for r in out2["email_retry"]] == ["sent"] and mail.sent[-1][0].endswith("(in ritardo)")


def test_budget_email_anche_sui_ritenti(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-01")])
    giro(path, FakeQuotes({}), FakeMail(False))
    mail = FakeMail()
    out = giro(path, FakeQuotes({}), mail, time_budget_s=1, email_reserve_s=35)
    assert mail.sent == [] and out["email_retry"][0]["status"] == "postponed"
    assert rows(path)[0]["email_attempts"] == 1


# ---- prove del verificatore finale VF-P (mutanti W1b, W5, W7, W8, W9 sopravvissuti) ----

def _conf(path):
    with sqlite3.connect(path) as conn:
        return [r[0] for r in conn.execute("SELECT confidence FROM decisions WHERE rationale LIKE '[TRIGGER]%'")]


def test_w1b_il_motivo_del_capo_arriva_in_voce_ed_email(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-12", what="rivedere dopo la trimestrale inventata")])
    mail = FakeMail()
    giro(path, FakeQuotes({}), mail)
    (voce,) = decisions(path)
    assert "rivedere dopo la trimestrale inventata" in voce["timing"]
    assert "rivedere dopo la trimestrale inventata" in mail.sent[0][1]


def test_w5_il_promemoria_non_finisce_fra_i_trigger_scattati(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-12"), trig("condition", "esito gara inventata")])
    mail = FakeMail()
    giro(path, FakeQuotes({}), mail)
    (voce,) = decisions(path)
    scattati, _, promemoria = voce["timing"].partition("Promemoria")
    assert "esito gara inventata" not in scattati
    assert "esito gara inventata" in promemoria


def test_w7_la_voce_porta_la_confidenza_bassa(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-12")])
    giro(path, FakeQuotes({}), FakeMail())
    assert _conf(path) == ["BASSA"]


def test_w8_ritento_fallito_da_solo_mette_il_giro_in_attention(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-12")])
    giro(path, FakeQuotes({}), FakeMail(False))      # primo invio fallito
    out = giro(path, FakeQuotes({}), FakeMail(False))  # solo il ritento, di nuovo fallito
    assert out["fired"] == [] and out["not_ok"] == [] and out["email_exhausted"] == []
    assert [r["status"] for r in out["email_retry"]] == ["failed"]
    assert out["status"] == "attention"


def test_w9_l_email_dice_che_non_si_rilancia_nulla_e_non_si_spende(tmp_path):
    path = make_db(tmp_path)
    add_run(path, triggers=[trig("date", "2031-03-12")])
    mail = FakeMail()
    giro(path, FakeQuotes({}), mail)
    assert "Nessuna analisi rilanciata, nessuna spesa" in mail.sent[0][1]
