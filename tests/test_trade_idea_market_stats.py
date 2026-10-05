"""Test di trade_idea_market_stats (Lotto 3 Trade Idea, costruttore L2, Opus 5.5).

Serie SINTETICHE con seed, ticker inventati (ZZ*/QQ*), nessuna rete, nessun DB.
La fixture condivisa tests/fixtures/market_pack_synth.json si rigenera con
    python tests/test_trade_idea_market_stats.py
e un test controlla che il file sul disco coincida col generatore.
"""
import json
import math
import re
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from scipy import stats as st

from bellomberg.reporting import trade_idea_market_stats as M
from bellomberg.reporting.trade_idea_market_stats import market_stats

FIXTURE = Path(__file__).parent / "fixtures" / "market_pack_synth.json"
SEED = 20261004


# ------------------------------------------------------------ generatori

def _bdays(start: date, n: int):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


_NATIVE = {"method": "native", "fx_ticker": None, "note": "gia in EUR"}


def _se(ticker, dates, values, *, field="adj_close", ccy="EUR", status="ok", reason=None,
        conv=_NATIVE, native=None):
    if field == "level":
        conv = {"method": "none_level", "fx_ticker": None, "note": "livello in punti"}
    return {"ticker": ticker, "currency": ccy, "native_currency": native or ccy,
            "price_field": field, "source": "sintetico", "status": status, "reason": reason,
            "dates": [d.isoformat() for d in dates], "values": list(values), "conversion": conv}


def _prices(rets, p0=100.0):
    out = [p0]
    for r in rets:
        out.append(out[-1] * (1 + r))
    return out


def _pack(cand, bench=None, vix=None, peers=None, **extra):
    series = {"candidate": cand}
    if bench is not None:
        series["benchmark"] = bench
    if vix is not None:
        series["vix"] = vix
    p = {"version": 1, "status": "ready", "reason": None, "as_of": None, "provider": "sintetico",
         "base_currency": "EUR", "series": series, "peers": peers or []}
    p.update(extra)
    return p


def genera_fixture():
    """Pacchetto sintetico condiviso (L1/L2/L3): 5 anni, calendari sfasati,
    un buco, un peer corto (insufficiente), un peer in errore."""
    rng = random.Random(SEED)
    master = _bdays(date(2021, 9, 27), 1310)
    rb = [rng.gauss(0.0003, 0.011) for _ in master[1:]]
    rc = []
    for b in rb:
        noise = rng.gauss(0, 0.012)
        if rng.random() < 0.02:                         # salti: code grasse
            noise += rng.choice((-1, 1)) * rng.uniform(0.04, 0.08)
        rc.append(0.0001 + 1.3 * b + noise)
    # Cancello privacy (lista_privata): i valori della fixture non devono contenere sottostringhe
    # numeriche con separatore decimale lunghe >= 5 caratteri. Prezzi = INTERI (scala alta, nessun
    # punto), VIX = 1 decimale sotto 100 (al massimo 4 caratteri), multipli <= 4 caratteri.
    pb, pc = _prices(rb, 400000.0), _prices(rc, 25000.0)
    v, vix = 18.0, []
    for i in range(len(master)):
        if i:
            v = max(9.0, v - 150 * rb[i - 1] + rng.gauss(0, 0.8))
        vix.append(v)
    p1 = _prices([0.6 * b + rng.gauss(0, 0.013) for b in rb], 40000.0)
    p2 = _prices([0.9 * b + rng.gauss(0, 0.015) for b in rb], 300000.0)

    def cal(pred, prices, level=False):
        d, vals = [], []
        for x, p in zip(master, prices):
            if pred(x):
                d.append(x)
                vals.append(round(min(p, 99.9), 1) if level else int(round(p)))
        return d, vals

    it_hol = lambda x: not ((x.month, x.day) in ((8, 15), (12, 26), (4, 25), (5, 1)))
    us_hol = lambda x: not ((x.month, x.day) in ((7, 4), (1, 19), (11, 25), (2, 16)))
    cd, cv = cal(it_hol, pc)
    cv[400] = None                                      # buco del fornitore
    bd, bv = cal(us_hol, pb)
    vd, vv = cal(us_hol, vix, level=True)
    pd1, pv1 = cal(it_hol, p1)
    pd2, pv2 = cal(lambda x: x >= date(2024, 4, 1), p2)
    def fx(t, unit):
        return {"method": "fx_same_date", "fx_ticker": t, "fx_field": "Close", "rate_unit": unit,
                "operation": "prezzo nativo / cambio stesso giorno, senza fill",
                "n_dropped_no_fx": 0, "note": "sintetico"}
    return {
        "_generatore": "tests/test_trade_idea_market_stats.py::genera_fixture, seed %d" % SEED,
        "version": 1, "status": "partial",
        "reason": "un peer non scaricato (sintetico)",
        "as_of": "2026-10-02", "provider": "sintetico (random.Random)",
        "base_currency": "EUR", "benchmark_ticker": "ZZSPX",
        "peer_selection": {"method": "select_peer_comps",
                           "note": "selettore sintetico; scartato QQSCART.DE (market cap fuori banda)",
                           "rejected": [{"ticker": "QQSCART.DE", "reason": "market cap fuori banda"}]},
        "series": {
            "candidate": _se("ZZCAND.MI", cd, cv),
            "benchmark": dict(_se("ZZSPX", bd, bv, conv=fx("EURUSD=X", "USD per EUR"),
                              native="USD"), index_type="price"),
            "vix": _se("^ZZVIX", vd, vv, field="level", ccy="USD"),
        },
        "multiples": {
            "current": [
                {"ticker": "ZZCAND.MI", "role": "candidate", "ev_sales": 1.23, "ev_ebitda": 9.87,
                 "pe_trailing": 15.5, "pe_forward": 13.2, "price_to_book": 2.1, "status": "ok",
                 "reason": None, "missing": [], "source": "sintetico", "as_of": "2026-10-02"},
                {"ticker": "QQPEER1.PA", "role": "peer", "ev_sales": 2.0, "ev_ebitda": 11.0,
                 "pe_trailing": -4.0, "pe_forward": 18.0, "price_to_book": 1.4, "status": "ok",
                 "reason": None, "missing": [], "source": "sintetico", "as_of": "2026-10-02"},
                {"ticker": "QQPEER2.CO", "role": "peer", "ev_sales": 1.0, "ev_ebitda": 7.0,
                 "pe_trailing": 12.0, "pe_forward": None, "price_to_book": None, "status": "ok",
                 "reason": None, "missing": ["P/E forward assente (sintetico)", "P/B assente (sintetico)"],
                 "source": "sintetico", "as_of": "2026-10-02"},
                {"ticker": "QQPEER3.US", "role": "peer", "ev_sales": None, "ev_ebitda": None,
                 "pe_trailing": None, "pe_forward": None, "price_to_book": None, "status": "error",
                 "reason": "download fallito (sintetico)", "missing": [], "source": "sintetico",
                 "as_of": None},
            ],
            "history": {"ticker": "ZZCAND.MI", "status": "ok", "reason": None, "source": "sintetico",
                        "currency": "EUR",
                        "approximations": ["EV senza debito a breve (sintetico)",
                                           "EBITDA = EBIT + ammortamenti (sintetico)"],
                        "price_field": "Close (sintetico)",
                        "years": [{"fy": 2024, "fy_end_assumed": "2024-12-31", "close_date": "2024-12-31",
                                   "close_fy_end": 31.5, "pe": 14.1, "ev_sales": 1.1, "ev_ebitda": 8.8,
                                   "p_b": 2.2, "ev": 1234, "ebitda": 140, "missing": []},
                                  {"fy": 2023, "fy_end_assumed": "2023-12-31", "close_date": "2023-12-29",
                                   "close_fy_end": 27.2, "pe": None, "ev_sales": 0.9, "ev_ebitda": None,
                                   "p_b": 1.9, "ev": 1001,
                                   "missing": ["P/E n.d.: EPS diluito non positivo (sintetico)",
                                               "EV/EBITDA n.d.: ammortamenti assente (sintetico)"]}]},
            "peer_selection": {"method": "select_peer_comps",
                               "note": "selettore sintetico; scartato QQSCART.DE (market cap fuori banda)",
                               "rejected": [{"ticker": "QQSCART.DE", "reason": "market cap fuori banda"}]},
        },
        "peers": [
            _se("QQPEER1.PA", pd1, pv1),
            _se("QQPEER2.CO", pd2, pv2, conv=fx("EURDKK=X", "DKK per EUR"), native="DKK"),
            _se("QQPEER3.US", [], [], status="error", reason="download fallito (sintetico)"),
        ],
    }


@pytest.fixture(scope="module")
def synth():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ------------------------------------------------------------ fixture

def test_fixture_su_disco_uguale_al_generatore(synth):
    assert synth == json.loads(json.dumps(genera_fixture()))


def test_fixture_senza_numeri_lunghi_con_separatore():
    """Proprieta' per il cancello privacy (lista_privata): nessun token numerico con punto o
    virgola oltre 4 caratteri, cosi' nessuna cifra del book (>= 5 caratteri) puo' comparire per
    caso nei float casuali. Si misura la forma, mai la lista."""
    t = FIXTURE.read_text(encoding="utf-8")
    toks = re.findall(r"\d+[.,]\d+", t)
    assert toks and max(len(x) for x in toks) <= 4


def test_fixture_end_to_end_ogni_blocco_dichiara_n_e_date(synth):
    out = market_stats(synth, window_years=5, language="it")
    assert out["status"] == "partial"                    # peer corto + peer in errore
    for b in ("moments", "qq", "histogram", "vol_cone", "drawdown", "acf_abs", "acf_returns",
              "beta", "rolling_beta", "beta_daily", "vix_sensitivity"):
        blk = out[b]
        assert blk["status"] == "ok", (b, blk)
        soglia = 200 if b in ("beta", "rolling_beta") else 1000      # settimanali
        assert blk["n_obs"] > soglia and blk["start"] and blk["end"], b
    assert out["beta"]["frequency"] == out["rolling_beta"]["frequency"] == "weekly"
    assert out["beta_daily"]["frequency"] == "daily"
    assert out["beta_daily"]["asynchronous_closes"] is True       # listino EUR contro listino USD
    assert out["series"]["benchmark"]["index_type"] == "price"
    ph_b = [l for l in out["price_history"]["lines"] if l["role"] == "benchmark"][0]
    assert ph_b["index_type"] == "price"
    # il buco del candidato toglie UN rendimento giornaliero dalle coppie (beta e VIX)
    assert out["beta"]["alignment"]["n_daily_excluded_gaps"] == 1
    assert out["vix_sensitivity"]["alignment"]["n_excluded_gaps"] == 1
    assert out["window"]["n_missing_closes"] == 1
    assert out["beta"]["benchmark"] == "ZZSPX"
    assert abs(out["beta"]["beta"] - 1.3) < 0.2           # settimanale: ~260 osservazioni
    assert abs(out["beta_daily"]["beta"] - 1.3) < 0.1     # fixture sincrona: anche il giornaliero
    assert out["vix_sensitivity"]["lag0"]["slope"] < 0
    peers = {p["ticker"]: p for p in out["peers"]}
    assert peers["QQPEER1.PA"]["status"] == "ok"
    assert peers["QQPEER2.CO"]["status"] == "insufficient"
    assert peers["QQPEER2.CO"]["n_obs"] > 0 and peers["QQPEER2.CO"]["years_observed"] < 3
    assert peers["QQPEER3.US"]["status"] == "unavailable"
    assert "download fallito" in peers["QQPEER3.US"]["reason"]
    assert any("1 chiusure mancanti" in g for g in out["gaps"])
    assert any("Calendari diversi" in g for g in out["gaps"])
    assert "QQSCART.DE" in out["peer_selection"]["note"]
    assert not any("non convertita" in g or "non dichiarata" in g for g in out["gaps"])
    json.dumps(out)                                      # serializzabile


def test_price_history_base_100_e_righe_dichiarate(synth):
    out = market_stats(synth, window_years=5)
    ph = out["price_history"]
    assert ph["status"] == "ok" and ph["base"] == 100
    lines = {(l["role"], l["ticker"]): l for l in ph["lines"]}
    c = lines[("candidate", "ZZCAND.MI")]
    b = lines[("benchmark", "ZZSPX")]
    assert c["index"][0] == 100.0 and b["index"][0] == 100.0
    assert c["total_return_pct"] == round(c["index"][-1] - 100, 2)
    # oracolo: rendimento totale dal pacchetto, fra prima data valida comune e ultima
    cs = synth["series"]["candidate"]
    vals = [(d, v) for d, v in zip(cs["dates"], cs["values"]) if v is not None and d >= c["base_date"]]
    assert c["total_return_pct"] == pytest.approx((vals[-1][1] / vals[0][1] - 1) * 100, abs=0.01)
    assert b["base_date"] in set(cs["dates"])
    assert lines[("peer", "QQPEER2.CO")]["status"] == "insufficient"
    assert "index" not in lines[("peer", "QQPEER2.CO")]
    assert lines[("peer", "QQPEER3.US")]["status"] == "unavailable"


def test_multipli_mediana_peer_validi_e_scarti_dichiarati(synth):
    m = market_stats(synth, window_years=5)["multiples"]
    assert m["status"] == "ok" and m["n_peers_ok"] == 2
    assert m["peer_median"]["ev_sales"] == {"value": 1.5, "n": 2, "n_excluded_non_positive_or_invalid": 0}
    assert m["peer_median"]["pe_trailing"]["value"] == 12.0
    assert m["peer_median"]["pe_trailing"]["n_excluded_non_positive_or_invalid"] == 1
    assert m["peer_median"]["pe_forward"]["n"] == 1
    assert m["history"]["years"][0]["fy"] == 2024
    assert m["history_approximations"] == ["EV senza debito a breve (sintetico)",
                                           "EBITDA = EBIT + ammortamenti (sintetico)"]
    assert m["history_status"] == "ok"


def test_multipli_assenti_dichiarati():
    rng = np.random.default_rng(21)
    out = market_stats(_single(list(rng.normal(0, 0.01, 1300))), window_years=5)
    assert out["multiples"]["status"] == "unavailable"
    assert "non contiene multipli" in out["multiples"]["reason"]


def test_gap_numeri_localizzati_e_ticker_non_ripetuto(synth):
    it = market_stats(synth, window_years=5, language="it")["gaps"]
    en = market_stats(synth, window_years=5, language="en")["gaps"]
    cal_it = next(g for g in it if g.startswith("Calendari diversi"))
    cal_en = next(g for g in en if g.startswith("Different calendars"))
    assert re.search(r"\d\.\d{3} e \d\.\d{3} chiusure, \d\.\d{3} date comuni", cal_it), cal_it
    assert re.search(r"\d,\d{3} and \d,\d{3} closes, \d,\d{3} common dates", cal_en), cal_en
    corto = next(g for g in it if g.startswith("Peer QQPEER2.CO: storia insufficiente"))
    assert re.search(r"\(\d,\d+ anni, \d+ osservazioni", corto), corto
    assert not any("2.5 anni" in g or re.search(r"\d\.\d+ anni", g) for g in it)
    err = next(g for g in it if "QQPEER3.US" in g)
    assert err.count("QQPEER3.US") == 1, err


def test_fixture_inglese(synth):
    out = market_stats(synth, window_years=5, language="en")
    assert any("Different calendars" in g for g in out["gaps"])


# ------------------------------------------------------------ curtosi / JB

def _single(rets, start=date(2020, 1, 6)):
    d = _bdays(start, len(rets) + 1)
    return _pack(_se("ZZNORM", d, _prices(rets)))


def test_normale_eccesso_vicino_zero():
    rng = np.random.default_rng(7)
    out = market_stats(_single(list(rng.normal(0, 0.01, 1300))), window_years=5)
    k = out["moments"]["daily"]["excess_kurtosis"]
    assert abs(k) < 0.5, k


def test_student_t_eccesso_positivo():
    rng = np.random.default_rng(8)
    out = market_stats(_single(list(rng.standard_t(4, 1300) * 0.008)), window_years=5)
    assert out["moments"]["daily"]["excess_kurtosis"] > 1.5


def test_jb_e_momenti_uguali_a_scipy_sui_log_rendimenti():
    rng = np.random.default_rng(9)
    pk = _single(list(rng.standard_t(5, 1300) * 0.01))
    out = market_stats(pk, window_years=5)
    p = np.array(pk["series"]["candidate"]["values"])
    lr = np.diff(np.log(p))
    d = out["moments"]["daily"]
    assert d["n_obs"] == len(lr)
    assert d["jb_stat"] == pytest.approx(st.jarque_bera(lr).statistic, rel=1e-6)
    assert d["excess_kurtosis"] == pytest.approx(st.kurtosis(lr), abs=1e-4)
    assert d["std_pct"] == pytest.approx(np.std(lr, ddof=1) * 100, abs=1e-4)


# ------------------------------------------------------------ allineamento per data

def _beta_pack(drop_c, drop_b, n=1300, beta=1.5):
    rng = np.random.default_rng(11)
    master = _bdays(date(2020, 1, 6), n)
    lb = rng.normal(0, 0.008, n - 1)
    pb = 100 * np.exp(np.concatenate([[0], np.cumsum(lb)]))
    pc = 50 * np.exp(np.concatenate([[0], np.cumsum(beta * lb)]))
    keep_c = [i for i in range(n) if i not in drop_c]
    keep_b = [i for i in range(n) if i not in drop_b]
    return _pack(_se("ZZC", [master[i] for i in keep_c], [float(pc[i]) for i in keep_c]),
                 _se("ZZB", [master[i] for i in keep_b], [float(pb[i]) for i in keep_b]))


def test_beta_noto_con_calendari_sfasati_allineato_per_data():
    drop_c = set(range(30, 1300, 23))                    # festivi del candidato
    drop_b = set(range(41, 1300, 29))                    # festivi del benchmark
    out = market_stats(_beta_pack(drop_c, drop_b), window_years=5)
    b = out["beta"]
    assert b["status"] == "ok"
    assert abs(b["beta"] - 1.5) < 0.05, b["beta"]
    assert b["frequency"] == "weekly" and b["window_unit"] == "settimane"
    al = b["alignment"]
    assert al["n_candidate_prices"] == 1300 - len(drop_c)
    assert al["n_benchmark_prices"] == 1300 - len(drop_b)
    assert al["n_common_dates"] == 1300 - len(drop_c | drop_b)
    assert al["n_common_daily_returns"] == al["n_common_dates"] - 1    # festivi singoli ammessi
    assert al["n_daily_excluded_gaps"] == 0
    assert al["n_weekly_returns"] == b["n_obs"] == al["n_weeks"] - 1
    assert abs(out["beta_daily"]["beta"] - 1.5) < 0.05
    assert out["beta_daily"]["n_obs"] == al["n_common_daily_returns"]
    rb = out["rolling_beta"]
    assert rb["window"] == 52
    assert rb["n_windows"] == b["n_obs"] - M.BETA_WINDOW + 1
    assert all(abs(x - 1.5) < 0.1 for x in rb["beta"])
    assert rb["last_date"] == rb["dates"][-1] and rb["last_stale"] is False
    assert any("Calendari diversi" in g for g in out["gaps"])


def _async_pack(n=1310, beta=1.2, seed=31):
    """Candidato europeo che reagisce il GIORNO DOPO al benchmark USA (chiusure asincrone)."""
    rng = np.random.default_rng(seed)
    master = _bdays(date(2020, 1, 6), n)
    rb = rng.normal(0.0003, 0.01, n - 1)
    rc = np.concatenate([[0.0], beta * rb[:-1]]) + rng.normal(0, 0.004, n - 1)
    return _pack(_se("ZZEU.MI", master, _prices(list(rc))),
                 dict(_se("ZZUS", master, _prices(list(rb)), conv={"method": "fx_same_date",
                                                                   "n_dropped_no_fx": 0},
                          native="USD"), index_type="total_return"))


def test_beta_settimanale_neutralizza_le_chiusure_asincrone():
    out = market_stats(_async_pack(), window_years=5)
    b, d = out["beta"], out["beta_daily"]
    assert abs(d["beta"]) < 0.2, d["beta"]                 # giornaliero distorto verso zero
    assert b["beta"] == pytest.approx(1.2, abs=0.25), b["beta"]
    assert d["asynchronous_closes"] is True
    assert "asincrone" in d["label"]
    assert "chiusure asincrone" in b["frequency_reason"]


def test_beta_settimanale_a_mano():
    """Oracolo indipendente: ultima data della settimana, settimane consecutive, cov/var ddof=1."""
    pk = _async_pack(seed=32)
    out = market_stats(pk, window_years=5)
    c, bnch = pk["series"]["candidate"], pk["series"]["benchmark"]
    last = {}
    for ds, vc, vb in zip(c["dates"], c["values"], bnch["values"]):
        d = date.fromisoformat(ds)
        last[d.isocalendar()[:2]] = (vc, vb)
    keys = sorted(last)
    ra = [last[keys[i]][0] / last[keys[i - 1]][0] - 1 for i in range(1, len(keys))]
    rb = [last[keys[i]][1] / last[keys[i - 1]][1] - 1 for i in range(1, len(keys))]
    cov = np.cov(ra, rb)
    assert out["beta"]["beta"] == pytest.approx(cov[0, 1] / cov[1, 1], abs=1e-4)
    assert out["beta"]["n_obs"] == len(ra)


def _gap_pack(gap_from, gap_len, n=1300, none=False):
    rng = np.random.default_rng(33)
    master = _bdays(date(2020, 1, 6), n)
    lb = rng.normal(0, 0.008, n - 1)
    pb = list(100 * np.exp(np.concatenate([[0], np.cumsum(lb)])))
    pc = list(50 * np.exp(np.concatenate([[0], np.cumsum(1.5 * lb)])))
    if none:
        cd, cv = master, [None if gap_from <= i < gap_from + gap_len else float(v) for i, v in enumerate(pc)]
    else:
        keep = [i for i in range(n) if not gap_from <= i < gap_from + gap_len]
        cd, cv = [master[i] for i in keep], [float(pc[i]) for i in keep]
    return _pack(_se("ZZC", cd, cv), _se("ZZB", master, [float(x) for x in pb]),
                 vix=_se("^ZZV", master, [20 + i % 7 for i in range(n)], field="level", ccy="USD"))


def test_buco_di_dieci_sedute_non_entra_come_un_rendimento():
    for none in (False, True):
        out = market_stats(_gap_pack(500, 10, none=none), window_years=5)
        al = out["beta"]["alignment"]
        assert al["n_daily_excluded_gaps"] == 1, (none, al)
        assert al["n_common_daily_returns"] == al["n_common_dates"] - 2
        assert out["beta_daily"]["n_obs"] == al["n_common_daily_returns"]
        assert out["vix_sensitivity"]["alignment"]["n_excluded_gaps"] == 1
        # serie singola: niente rendimento a cavallo del vuoto
        assert out["moments"]["n_obs"] == 1300 - 10 - 2
        # due settimane intere senza data comune: buchi dichiarati, nessun rendimento a cavallo
        assert al["n_missing_weeks"] == 2                        # 10 sedute = 2 settimane intere
        assert al["n_weekly_returns"] == al["n_weeks"] - 2       # una sola discontinuita'
        assert out["beta"]["n_obs"] == al["n_weekly_returns"]
        # momenti settimanali/mensili: oracolo a mano sulle settimane con chiusure del candidato
        c = out_pack_dates(_gap_pack(500, 10, none=none))
        wk = sorted({(d - timedelta(days=d.weekday())).toordinal() // 7 for d in c})
        n_cons = sum(1 for i in range(1, len(wk)) if wk[i] - wk[i - 1] == 1)
        assert out["moments"]["weekly"]["n_obs"] == n_cons == len(wk) - 2
        assert any("settimane senza data comune" in g for g in out["gaps"])


def out_pack_dates(pk):
    c = pk["series"]["candidate"]
    return [date.fromisoformat(d) for d, v in zip(c["dates"], c["values"]) if v is not None]


def test_proxy_del_benchmark_dichiarato():
    pk = _beta_pack(set(), set())
    pk["series"]["benchmark"]["proxy"] = "indice di prezzo al posto del total return"
    pk["series"]["benchmark"]["index_type"] = "price"
    out = market_stats(pk, window_years=5)
    assert out["series"]["benchmark"]["proxy"].startswith("indice di prezzo")
    assert any(g.startswith("Benchmark (ZZB): PROXY: indice di prezzo") for g in out["gaps"])


def test_vix_lag1_solo_su_rendimenti_contigui():
    out = market_stats(_gap_pack(500, 10), window_years=5)
    vs = out["vix_sensitivity"]
    # un salto nella catena: lag1 perde il primo rendimento e quello dopo il vuoto
    assert vs["lag1"]["n_obs"] == vs["n_obs"] - 2


def test_ultimo_beta_mobile_vecchio_dichiarato():
    rng = np.random.default_rng(34)
    master = _bdays(date(2020, 1, 6), 1300)
    lb = rng.normal(0, 0.008, 1299)
    pb = 100 * np.exp(np.concatenate([[0], np.cumsum(lb)]))
    pc = 50 * np.exp(np.concatenate([[0], np.cumsum(1.5 * lb)]))
    pk = _pack(_se("ZZC", master, [float(x) for x in pc]),
               _se("ZZB", master[:-30], [float(x) for x in pb[:-30]]))
    out = market_stats(pk, window_years=5)
    rb = out["rolling_beta"]
    assert rb["last_stale"] is True and rb["last_date"] == master[-31].isoformat()
    assert any(g.startswith("Ultimo beta mobile al ") for g in out["gaps"])


def test_vix_pendenza_nota_su_date_comuni():
    rng = np.random.default_rng(12)
    n = 1300
    master = _bdays(date(2020, 1, 6), n)
    dv = rng.normal(0, 1.0, n - 1)
    vix = 20 + np.concatenate([[0], np.cumsum(dv)])
    lr_pct = -0.4 * dv + rng.normal(0, 0.05, n - 1)
    pc = 30 * np.exp(np.concatenate([[0], np.cumsum(lr_pct / 100)]))
    keep_v = [i for i in range(n) if i % 37 != 5]
    pk = _pack(_se("ZZC", master, [float(x) for x in pc]),
               vix=_se("^ZZV", [master[i] for i in keep_v], [float(vix[i]) for i in keep_v],
                       field="level", ccy="USD"))
    out = market_stats(pk, window_years=5)
    vs = out["vix_sensitivity"]
    assert vs["status"] == "ok"
    assert vs["lag0"]["slope"] == pytest.approx(-0.4, abs=0.02)
    assert vs["n_obs"] == len(keep_v) - 1 == vs["lag0"]["n_obs"]
    assert vs["lag1"]["n_obs"] == vs["n_obs"] - 1
    # nessun gap "non convertita" per il VIX (livello in punti)
    assert not any("non convertita" in g for g in out["gaps"])


# ------------------------------------------------------------ soglia 3 anni

def test_storia_corta_dichiarata_insufficiente_mai_calcolata():
    rng = np.random.default_rng(13)
    n = 650                                              # ~2,5 anni di borsa
    out = market_stats(_single(list(rng.normal(0, 0.01, n))), window_years=5)
    m = out["moments"]
    assert m["status"] == "insufficient"
    assert m["n_obs"] == n and m["years_observed"] < 3
    assert "daily" not in m and "excess_kurtosis" not in json.dumps(m)
    for b in ("qq", "vol_cone", "drawdown", "acf_abs"):
        assert out[b]["status"] == "insufficient"
    assert any("storia insufficiente" in g for g in out["gaps"])


def test_storia_lunga_ma_rada_insufficiente():
    """3,5 anni di calendario ma una chiusura ogni 3 sedute: troppo pochi rendimenti."""
    rng = np.random.default_rng(22)
    d = _bdays(date(2020, 1, 6), 910)[::3]
    pk = _pack(_se("ZZRADO", d, _prices(list(rng.normal(0, 0.01, len(d) - 1)))))
    out = market_stats(pk, window_years=5)
    assert out["window"]["years_observed"] > 3
    assert out["moments"]["status"] == "insufficient"
    assert out["moments"]["n_obs"] == len(d) - 1


def test_tre_anni_pieni_bastano():
    rng = np.random.default_rng(14)
    out = market_stats(_single(list(rng.normal(0, 0.01, 790))), window_years=5)
    assert out["moments"]["status"] == "ok"


def test_window_years_sotto_tre_rifiutato():
    rng = np.random.default_rng(15)
    with pytest.raises(ValueError):
        market_stats(_single(list(rng.normal(0, 0.01, 900))), window_years=2)


def test_finestra_taglia_la_storia_piu_vecchia():
    rng = np.random.default_rng(16)
    out = market_stats(_single(list(rng.normal(0, 0.01, 2600))), window_years=5)
    assert 4.9 < out["window"]["years_observed"] <= 5.0
    assert out["moments"]["n_obs"] < 1320


# ------------------------------------------------------------ Ljung-Box / ACF

def test_ljung_box_letto_al_lag_giusto():
    from statsmodels.stats.diagnostic import acorr_ljungbox
    rng = np.random.default_rng(17)
    # volatilita' a grappoli: |r| autocorrelato
    sig, r = 0.01, []
    for _ in range(1300):
        sig = 0.002 + 0.9 * sig + 0.05 * abs(r[-1] if r else 0)
        r.append(rng.normal(0, sig) / 3)
    pk = _single(r)
    out = market_stats(pk, window_years=5)
    p = np.array(pk["series"]["candidate"]["values"])
    a = np.abs(np.diff(np.log(p)))
    lb = out["acf_abs"]["ljung_box"]
    assert [x["lag"] for x in lb] == list(M.LJUNG_BOX_LAGS)
    for x in lb:
        ref = acorr_ljungbox(a, lags=[x["lag"]])
        assert x["stat"] == pytest.approx(float(ref["lb_stat"].iloc[0]), abs=1e-4), x
    assert lb[0]["stat"] != lb[-1]["stat"]
    acf = out["acf_abs"]
    assert acf["lags"] == list(range(1, 41)) and len(acf["values"]) == 40
    assert acf["band"] == pytest.approx(1.96 / math.sqrt(len(a)), abs=1e-4)


# ------------------------------------------------------------ cono e drawdown

def test_cono_coincide_con_std_campionaria_a_mano():
    rng = np.random.default_rng(18)
    r = list(rng.normal(0, 0.012, 1300))
    pk = _single(r)
    out = market_stats(pk, window_years=5)
    p = pk["series"]["candidate"]["values"]
    rs = [p[i] / p[i - 1] - 1 for i in range(1, len(p))]
    w = {x["window"]: x for x in out["vol_cone"]["windows"]}
    assert set(w) == set(M.CONE_WINDOWS)
    exp = np.std(rs[-21:], ddof=1) * math.sqrt(252) * 100
    assert w[21]["current_pct"] == pytest.approx(exp, abs=0.01)


def test_drawdown_noto():
    n = 860
    path = ([100 + 20 * i / 200 for i in range(200)] + [120 - 30 * i / 100 for i in range(100)]
            + [90 + 40 * i / (n - 301) for i in range(n - 300)])
    d = _bdays(date(2021, 1, 4), n)
    out = market_stats(_pack(_se("ZZDD", d, path)), window_years=5)
    dd = out["drawdown"]
    assert dd["status"] == "ok"
    assert dd["max_pct"] == pytest.approx(-25.0, abs=0.01)
    assert dd["top"][0]["depth_pct"] == pytest.approx(-25.0, abs=0.01)
    assert dd["top"][0]["start_date"] == d[200].isoformat()
    assert dd["top"][0]["trough_date"] == d[300].isoformat()


# ------------------------------------------------------------ fallback dichiarati

def test_pacchetto_unavailable_dichiarato_senza_eccezione():
    out = market_stats({"version": 1, "status": "unavailable", "reason": "rete giu'"}, window_years=5)
    assert out["status"] == "unavailable"
    assert out["beta"] == {"status": "unavailable", "reason": out["gaps"][0]}
    assert "rete giu'" in out["gaps"][0]


def test_benchmark_in_errore_e_valuta_non_convertita_dichiarati():
    rng = np.random.default_rng(19)
    d = _bdays(date(2020, 1, 6), 1300)
    pk = _pack(_se("ZZC", d, _prices(list(rng.normal(0, 0.01, 1299))), ccy="DKK"),
               _se("ZZB", [], [], status="error", reason="timeout"))
    out = market_stats(pk, window_years=5)
    assert out["beta"]["status"] == "unavailable" and "timeout" in out["beta"]["reason"]
    assert out["vix_sensitivity"]["status"] == "unavailable"
    assert any("non convertita" in g for g in out["gaps"])
    assert out["status"] == "partial"


def test_pacchetto_malformato_non_solleva():
    out = market_stats({"version": 1, "status": "ready",
                        "series": {"candidate": {"ticker": "ZZX", "status": "ok",
                                                 "dates": ["2020-01-02"], "values": []}}},
                       window_years=5)
    assert out["status"] == "unavailable"


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(genera_fixture(), indent=None, separators=(", ", ": ")) + "\n",
                       encoding="utf-8", newline="\n")
    print("scritta", FIXTURE)
