"""Core puro dei trigger Trade Idea (TI-RESEARCH-PIPELINE, Opus 5.5). Ticker e prezzi inventati."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from bellomberg.core.trade_idea_watch import (
    ROW_KEYS, Outcome, Quote, WatchRow, decision_fields, eligibility, email_message,
    evaluate, rows_from_result,
)

TODAY = date(2031, 3, 12)          # mercoledi'
RUN = {"id": "run-zz-1", "ticker": "ZZTEST", "currency": "EUR"}
REF = {"status": "ready", "price": "123.45", "price_asof": "2031-03-10", "currency": "EUR",
       "source": "yfinance daily Close (not an intraday quote)"}


def trig(kind, value, what="motivo inventato"):
    body = {"kind": kind, "date": None, "price_level": None, "condition": None, "what": what}
    body[{"date": "date", "price": "price_level", "condition": "condition"}[kind]] = value
    return body


def result(*triggers, judgment="watch"):
    return {"judgment": judgment, "review_triggers": list(triggers)}


def row(id=1, kind="price", *, ticker="ZZTEST", date=None, price_level=None, currency="EUR",
        direction=None, condition=None, reminder_date=None, status="active", run_id="run-zz-1"):
    return WatchRow(id=id, run_id=run_id, ticker=ticker, kind=kind, date=date, price_level=price_level,
                    price_currency=currency, direction=direction, condition=condition,
                    reminder_date=reminder_date, what="cosa inventata", status=status)


def quote(price="150.00", *, currency="EUR", asof="2031-03-11", status="ok", ticker="ZZTEST"):
    return Quote(ticker=ticker, price=Decimal(price), currency=currency, asof=asof,
                 source="test", status=status, reason=None)


def one(rows, quotes=None, active=frozenset(), today=TODAY):
    out = evaluate(rows, today=today, quotes=quotes or {}, active_run_tickers=active)
    assert len(out) == len(rows) and all(isinstance(o, Outcome) for o in out)
    return out[0]


# ---- rows_from_result -------------------------------------------------------

def test_righe_hanno_esattamente_le_chiavi_dello_store():
    rows = rows_from_result(result(trig("date", "2031-06-01"), trig("price", 150.0),
                                   trig("condition", "esito gara inventata")),
                            run=RUN, reference=REF, today=TODAY)
    assert [tuple(r) for r in rows] == [ROW_KEYS] * 3
    assert [r["trigger_index"] for r in rows] == [0, 1, 2]


def test_direzione_dedotta_dal_riferimento():
    up, down = rows_from_result(result(trig("price", 150.0), trig("price", 100.0)),
                                run=RUN, reference=REF, today=TODAY)
    assert (up["direction"], up["status"]) == ("at_or_above", "active")
    assert (down["direction"], down["status"]) == ("at_or_below", "active")
    assert up["price_currency"] == "EUR" and up["reference_price"] == 123.45
    assert up["reference_asof"] == "2031-03-10"


def test_livello_uguale_al_riferimento_bloccato():
    (r,) = rows_from_result(result(trig("price", 123.45)), run=RUN, reference=REF, today=TODAY)
    assert r["status"] == "blocked" and r["direction"] is None and "indeterminata" in r["status_reason"]


@pytest.mark.parametrize("reference,word", [
    (None, "assente"),
    ({**REF, "status": "unavailable"}, "non pronto"),
    ({**REF, "currency": "USD"}, "valuta del riferimento"),
    ({**REF, "price": "abc"}, "non numerico"),
])
def test_prezzo_senza_riferimento_usabile_bloccato_dichiarato(reference, word):
    (r,) = rows_from_result(result(trig("price", 150.0)), run=RUN, reference=reference, today=TODAY)
    assert r["status"] == "blocked" and r["direction"] is None and word in r["status_reason"]


def test_valuta_della_run_assente_blocca():
    (r,) = rows_from_result(result(trig("price", 150.0)), run={**RUN, "currency": None},
                            reference=REF, today=TODAY)
    assert r["status"] == "blocked" and "valuta della run assente" in r["status_reason"]


def test_condizione_promemoria_alla_prima_data_futura():
    rows = rows_from_result(result(trig("date", "2031-09-01"), trig("date", "2031-05-02"),
                                   trig("date", "2031-01-01"), trig("condition", "cond inventata")),
                            run=RUN, reference=None, today=TODAY)
    assert rows[3]["reminder_date"] == "2031-05-02" and rows[3]["status"] == "active"


def test_condizione_promemoria_oggi_incluso():
    rows = rows_from_result(result(trig("date", TODAY.isoformat()), trig("condition", "c")),
                            run=RUN, reference=None, today=TODAY)
    assert rows[1]["reminder_date"] == TODAY.isoformat()


def test_condizione_senza_data_futura_undated_mai_data_inventata():
    rows = rows_from_result(result(trig("date", "2031-01-01"), trig("condition", "c")),
                            run=RUN, reference=None, today=TODAY)
    assert rows[1]["status"] == "undated" and rows[1]["reminder_date"] is None
    assert "nessun" in rows[1]["status_reason"]


def test_data_passata_al_popolamento_resta_attiva_con_nota():
    (r,) = rows_from_result(result(trig("date", "2031-01-01")), run=RUN, reference=None, today=TODAY)
    assert r["status"] == "active" and "gia' passata" in r["status_reason"]
    (f,) = rows_from_result(result(trig("date", "2031-12-01")), run=RUN, reference=None, today=TODAY)
    assert f["status_reason"] is None


@pytest.mark.parametrize("bad", [
    {"kind": "price", "date": "2031-01-01", "price_level": 1.0, "condition": None, "what": "x"},
    {"kind": "date", "date": "2031-02-30", "price_level": None, "condition": None, "what": "x"},
    {"kind": "price", "date": None, "price_level": -1.0, "condition": None, "what": "x"},
    {"kind": "price", "price_level": 1.0, "what": "x"},
])
def test_trigger_non_conforme_al_contratto_rifiutato(bad):
    with pytest.raises(ValueError, match="ReviewTriggerV4"):
        rows_from_result(result(bad), run=RUN, reference=REF, today=TODAY)


def test_run_non_idonea_rifiutata_e_motivata():
    assert eligibility(result(trig("date", "2031-06-01"))) is None
    assert "rejected" in eligibility(result(trig("date", "2031-06-01"), judgment="rejected"))
    assert "non /4" in eligibility({"judgment": "watch"})
    with pytest.raises(ValueError):
        rows_from_result(result(trig("date", "2031-06-01"), judgment="favorable"),
                         run=RUN, reference=REF, today=TODAY)
    with pytest.raises(ValueError, match="vuoti"):
        rows_from_result(result(), run=RUN, reference=REF, today=TODAY)


def test_today_deve_essere_una_data():
    with pytest.raises(TypeError):
        rows_from_result(result(trig("date", "2031-06-01")), run=RUN, reference=REF,
                         today=datetime(2031, 3, 12))
    with pytest.raises(TypeError):
        evaluate([], today="2031-03-12", quotes={}, active_run_tickers=frozenset())


# ---- evaluate: date ---------------------------------------------------------

def test_data_scatta_il_giorno_stesso_non_prima():
    assert one([row(kind="date", date="2031-03-12")]).verdict == "fire"
    assert one([row(kind="date", date="2031-03-13")]).verdict == "wait"
    late = one([row(kind="date", date="2031-03-01")])
    assert late.verdict == "fire" and "gia' passata" in late.reason


# ---- evaluate: prezzi -------------------------------------------------------

def test_attraversamento_al_rialzo():
    r = row(price_level=140.0, direction="at_or_above")
    assert one([r], {"ZZTEST": quote("140.00")}).verdict == "fire"
    assert one([r], {"ZZTEST": quote("139.99")}).verdict == "wait"


def test_attraversamento_al_ribasso():
    r = row(price_level=90.0, direction="at_or_below")
    assert one([r], {"ZZTEST": quote("90.00")}).verdict == "fire"
    assert one([r], {"ZZTEST": quote("90.01")}).verdict == "wait"
    assert one([r], {"ZZTEST": quote("150.00")}).verdict == "wait"


def test_osservazione_porta_prezzo_valuta_data_fonte():
    o = one([row(price_level=140.0, direction="at_or_above")], {"ZZTEST": quote("150.00")})
    assert o.observation["price"] == "150.00" and o.observation["currency"] == "EUR"
    assert o.observation["asof"] == "2031-03-11" and o.observation["source"] == "test"


def test_valuta_diversa_non_scatta():
    o = one([row(price_level=140.0, direction="at_or_above")], {"ZZTEST": quote("150.00", currency="GBX")})
    assert o.verdict == "blocked" and "valuta" in o.reason


def test_quota_stale_dichiarata_non_scatta():
    o = one([row(price_level=140.0, direction="at_or_above")], {"ZZTEST": quote("150.00", status="stale")})
    assert o.verdict == "stale"


def test_freschezza_ricalcolata_anche_se_lo_status_dice_ok():
    # status ok ma asof di una settimana prima: il core non si fida e dichiara stale
    o = one([row(price_level=140.0, direction="at_or_above")],
            {"ZZTEST": quote("150.00", asof="2031-03-03")})
    assert o.verdict == "stale" and "freschezza" in o.reason


def test_quota_ieri_lavorativo_e_fresca_lunedi():
    monday = date(2031, 3, 17)
    o = one([row(price_level=140.0, direction="at_or_above")],
            {"ZZTEST": quote("150.00", asof="2031-03-14")}, today=monday)
    assert o.verdict == "fire"


def test_quota_assente_o_in_errore_unavailable():
    r = row(price_level=140.0, direction="at_or_above")
    assert one([r], {}).verdict == "unavailable"
    assert one([r], {"ZZTEST": quote(status="data_missing")}).verdict == "unavailable"
    assert one([r], {"ZZTEST": quote(ticker="QQSYN.MI")}).verdict == "unavailable"


def test_prezzo_senza_direzione_bloccato():
    assert one([row(price_level=140.0, direction=None)], {"ZZTEST": quote()}).verdict == "blocked"


# ---- evaluate: condition, deferred, righe non attive -------------------------

def test_condizione_promemoria_alla_data():
    assert one([row(kind="condition", condition="c", reminder_date="2031-03-12")]).verdict == "remind"
    assert one([row(kind="condition", condition="c", reminder_date="2031-03-20")]).verdict == "wait"
    assert one([row(kind="condition", condition="c", reminder_date=None)]).verdict == "blocked"


def test_run_attiva_rinvia_lo_scatto():
    active = frozenset({"ZZTEST"})
    d = one([row(kind="date", date="2031-03-12")], active=active)
    assert d.verdict == "deferred" and "fire" in d.reason
    p = one([row(price_level=140.0, direction="at_or_above")], {"ZZTEST": quote()}, active=active)
    assert p.verdict == "deferred"
    c = one([row(kind="condition", condition="c", reminder_date="2031-03-12")], active=active)
    assert c.verdict == "deferred"
    # altro ticker attivo: nessun rinvio
    assert one([row(kind="date", date="2031-03-12")], active=frozenset({"QQSYN.MI"})).verdict == "fire"


def test_righe_non_attive_dichiarate_non_scartate():
    rows = [row(1, kind="date", date="2031-03-01", status="fired"),
            row(2, kind="condition", condition="c", status="undated"),
            row(3, kind="date", date="2031-03-01")]
    out = evaluate(rows, today=TODAY, quotes={}, active_run_tickers=frozenset())
    assert [(o.row_id, o.verdict) for o in out] == [(1, "blocked"), (2, "blocked"), (3, "fire")]
    assert "status=undated" in out[1].reason


# ---- testi -----------------------------------------------------------------

FIRED = [row(5, kind="date", date="2031-03-12"), row(6, price_level=140.0, direction="at_or_above")]
REM = [row(7, kind="condition", condition="gara inventata", reminder_date="2031-03-12")]
OBS = {6: {"price": "1234.50", "currency": "EUR", "asof": "2031-03-11", "source": "FONTE GREZZA",
           "bar_label": "chiusura inventata"}}


@pytest.mark.parametrize("language", ["it", "en"])
def test_decision_fields(language):
    f = decision_fields(RUN, FIRED, REM, OBS, language=language)
    assert set(f) == {"action", "eur_amount", "confidence", "timing", "rationale"}
    assert (f["action"], f["eur_amount"], f["confidence"]) == ("RESEARCH", None, "BASSA")
    assert f["rationale"].startswith("[TRIGGER] ")
    price = "1.234,50 EUR" if language == "it" else "1,234.50 EUR"
    assert price in f["timing"] and "gara inventata" in f["timing"] and len(f["timing"]) <= 2000
    assert ("Trigger scattati" if language == "it" else "Triggers fired") in f["timing"]


def test_decision_fields_rifiuta_vuoto_e_altra_run():
    with pytest.raises(ValueError):
        decision_fields(RUN, [], [], {}, language="it")
    with pytest.raises(ValueError):
        decision_fields(RUN, [row(9, kind="date", date="2031-03-12", run_id="altra")], [], {}, language="it")
    with pytest.raises(ValueError):
        decision_fields(RUN, FIRED, [], {}, language="fr")


def test_timing_tagliato_dichiarato():
    long_rows = [WatchRow(id=i, run_id="run-zz-1", ticker="ZZTEST", kind="date", date="2031-03-12",
                          price_level=None, price_currency=None, direction=None, condition=None,
                          reminder_date=None, what="x" * 1500, status="active") for i in range(3)]
    f = decision_fields(RUN, long_rows, [], {}, language="it")
    assert len(f["timing"]) <= 2000 and f["timing"].endswith("[testo tagliato]")


@pytest.mark.parametrize("language", ["it", "en"])
def test_email_message(language):
    subject, body = email_message(RUN, FIRED, REM, 4321, language=language, observations=OBS)
    price = "1.234,50 EUR" if language == "it" else "1,234.50 EUR"
    assert "ZZTEST" in subject and "#4321" in body and price in body
    _, body2 = email_message(RUN, [], REM, 4321, language=language)
    assert "gara inventata" in body2
    _, body3 = email_message(RUN, FIRED, [], 1, language=language)
    assert ("osservazione n.d." if language == "it" else "observation n/a") in body3


# ---- rilievi RV-P-B (unita', valute, testi, data passata) -------------------

@pytest.mark.parametrize("level,blocked", [(24.0, True), (24.69, False), (25.0, False), (617.0, False), (617.25, False), (618.0, True)])
def test_banda_livello_riferimento_contro_unita_diverse(level, blocked):
    # riferimento 123.45: banda 0,2-5 = 24,69 .. 617,25
    (r,) = rows_from_result(result(trig("price", level)), run=RUN, reference=REF, today=TODAY)
    assert (r["status"] == "blocked") is blocked
    if blocked:
        assert "unita' diversa" in r["status_reason"] and r["direction"] is None


def test_gbx_livello_in_sterline_bloccato_dichiarato():
    ref = {**REF, "price": "1250.0", "currency": "GBX"}
    (r,) = rows_from_result(result(trig("price", 13.0)), run={**RUN, "ticker": "ZZSYN.L", "currency": "GBX"},
                            reference=ref, today=TODAY)
    assert r["status"] == "blocked" and "pence" in r["status_reason"]


@pytest.mark.parametrize("currencies,blocked", [(["USD"], True), (["EUR", "USD"], True), (["EUR"], False), ([], False)])
def test_valuta_degli_scenari_diversa_dalla_run_blocca(currencies, blocked):
    res = {**result(trig("price", 150.0), trig("date", "2031-06-01")),
           "scenarios": [{"name": "base", "currency": c} for c in currencies]}
    price, day = rows_from_result(res, run=RUN, reference=REF, today=TODAY)
    assert (price["status"] == "blocked") is blocked and day["status"] == "active"
    if blocked:
        assert "scenari" in price["status_reason"]


@pytest.mark.parametrize("language", ["it", "en"])
def test_testo_pm_senza_codici_ne_fonte_grezza(language):
    f = decision_fields(RUN, FIRED, [], OBS, language=language)
    _, body = email_message(RUN, FIRED, [], 1, language=language, observations=OBS)
    for testo in (f["timing"], f["rationale"], body):
        assert "at_or_above" not in testo and "FONTE GREZZA" not in testo and "2031-03" not in testo
        assert "chiusura inventata" in testo
        assert ("al rialzo: prezzo ≥ livello" if language == "it" else "upward: price ≥ level") in testo
        assert ("11/03/2031" if language == "it" else "11 Mar 2031") in testo
        assert ("140,00 EUR" if language == "it" else "140.00 EUR") in testo


def test_barra_assente_dichiarata_e_decimali_mai_persi():
    obs = {6: {"price": "0.123456", "currency": "EUR", "asof": "2031-03-11", "source": "x"}}
    f = decision_fields(RUN, [FIRED[1]], [], obs, language="it")
    assert "0,123456 EUR" in f["timing"] and "barra n.d." in f["timing"]


def test_osservazione_porta_bar_label():
    q = Quote(ticker="ZZTEST", price=Decimal("150"), currency="EUR", asof="2031-03-11", source="s",
              status="ok", reason=None, bar_label="etichetta")
    o = one([row(price_level=140.0, direction="at_or_above")], {"ZZTEST": q})
    assert o.observation["bar_label"] == "etichetta" and o.observation["source"] == "s"


@pytest.mark.parametrize("language", ["it", "en"])
def test_data_passata_detta_nel_testo(language):
    late = row(5, kind="date", date="2031-02-15")
    (o,) = evaluate([late], today=TODAY, quotes={}, active_run_tickers=frozenset())
    assert o.verdict == "fire" and o.observation["late_days"] == 25
    f = decision_fields(RUN, [late], [], {5: o.observation}, language=language)
    _, body = email_message(RUN, [late], [], 1, language=language, observations={5: o.observation})
    word = "gia' passata" if language == "it" else "already past"
    assert word in f["timing"] and word in body
    (ontime,) = evaluate([row(5, kind="date", date="2031-03-12")], today=TODAY, quotes={},
                         active_run_tickers=frozenset())
    assert ontime.observation["late_days"] == 0
    assert word not in decision_fields(RUN, [late], [], {5: ontime.observation}, language=language)["timing"]
