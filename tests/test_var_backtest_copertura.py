"""
Voce 7 b (04/10, Opus 5.5) — backtest del VaR: perimetro DICHIARATO e verdetto a due assi.

Difetto misurato (R6 b): una colonna presente ma TUTTA VUOTA (download fallito) contava come
nome coperto: il backtest girava su 18 nomi su 27 con `excluded_tickers: []` e il verdetto
cambiava fra due giri con lo stesso book. Qui:
  1) colonna vuota / storia insufficiente -> esclusa, dichiarata con motivo e peso;
  2) peso escluso oltre soglia -> verdetto "NON AFFIDABILE" invece di PASS/FAIL;
  3) verdetto su due assi (copertura / indipendenza) e combinato che dice QUALE asse fallisce;
  4) poche eccezioni attese -> p esatti (binomiale; indipendenza condizionata) e bassa potenza.

Ticker e numeri INVENTATI, serie sintetiche con seed, nessuna rete (yfinance stubbato).
"""
import itertools

import numpy as np
import pandas as pd
import pytest
from scipy import stats as st

import bellomberg.portfolio.var_backtest as vb


# ------------------------------------------------------------------ pipeline offline

def _fake_db(pesi):
    class _DB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": t, "valuta": "EUR", "valore_mercato": v}
                                  for t, v in pesi.items()],
                    "totale_valore_mercato_eur": float(sum(pesi.values()))}
    return _DB


def _prezzi(n_days, tickers, seed, vuote=(), corte=None):
    """Prezzi sintetici in formato yf.download multi-ticker. `vuote` = colonne tutte NaN
    (download fallito); `corte` = {ticker: n} con solo le ultime n quotazioni valide."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n_days)
    data = {}
    for t in tickers:
        px = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.011, size=n_days))
        if t in vuote:
            px = np.full(n_days, np.nan)
        elif corte and t in corte:
            px[:n_days - corte[t]] = np.nan
        data[("Close", t)] = px
    df = pd.DataFrame(data, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def _gira(monkeypatch, tmp_path, pesi, prezzi):
    import bellomberg.storage.negozi_privati as np_
    alias = tmp_path / "alias_fonti.json"
    alias.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(alias))
    monkeypatch.setattr("yfinance.download", lambda *a, **k: prezzi.copy())
    monkeypatch.setattr("bellomberg.storage.memory_db.MemoryDB", _fake_db(pesi))
    out = vb.backtest_var(window=252, period="3y")
    assert "error" not in out, out.get("error")
    return out


PESI = {"ZZAAA": 50000.0, "ZZBBB": 30000.0, "ZZCCC": 15000.0, "ZZDDD": 5000.0}


def test_colonna_vuota_esclusa_e_dichiarata_col_peso(monkeypatch, tmp_path):
    prezzi = _prezzi(800, list(PESI), seed=7, vuote=("ZZDDD",))
    out = _gira(monkeypatch, tmp_path, PESI, prezzi)
    assert out["excluded_tickers"] == ["ZZDDD"], out["excluded_tickers"]
    det = out["excluded_detail"]
    assert det == [{"ticker": "ZZDDD", "motivo": "serie vuota (download fallito)",
                    "obs_valide": 0, "peso_pct": 5.0}], det
    assert out["excluded_weight_pct"] == 5.0
    assert out["n_tickers_tested"] == 3 and out["n_tickers_perimeter"] == 4
    assert out["reliable"] is True
    assert out["var95"]["verdict"] in ("PASS", "FAIL")


def test_stesso_book_colonna_vuota_o_assente_stesso_risultato(monkeypatch, tmp_path):
    """Colonna vuota e colonna assente sono LO STESSO buco: stesso campione, stessi
    conteggi, stesso verdetto, stessa dichiarazione (prima la vuota sembrava coperta)."""
    con_vuota = _prezzi(800, list(PESI), seed=7, vuote=("ZZDDD",))
    senza = con_vuota.drop(columns=[("Close", "ZZDDD")])
    a = _gira(monkeypatch, tmp_path, PESI, con_vuota)
    b = _gira(monkeypatch, tmp_path, PESI, senza)
    for k in ("n_obs_series", "n_obs_tested", "excluded_tickers", "excluded_weight_pct"):
        assert a[k] == b[k], k
    assert a["excluded_detail"][0]["motivo"] != b["excluded_detail"][0]["motivo"]
    for conf in ("var95", "var99"):
        assert a[conf]["kupiec_pof"] == b[conf]["kupiec_pof"]
        assert a[conf]["verdict"] == b[conf]["verdict"]


def test_storia_insufficiente_esclusa_con_motivo(monkeypatch, tmp_path):
    prezzi = _prezzi(800, list(PESI), seed=7, corte={"ZZDDD": 10})
    out = _gira(monkeypatch, tmp_path, PESI, prezzi)
    assert out["excluded_tickers"] == ["ZZDDD"]
    d = out["excluded_detail"][0]
    assert d["obs_valide"] == 9 and "storia insufficiente" in d["motivo"], d


def test_peso_escluso_oltre_soglia_non_affidabile(monkeypatch, tmp_path):
    """ZZCCC (15%) vuota: oltre la soglia del 10% il verdetto NON e' PASS/FAIL."""
    prezzi = _prezzi(800, list(PESI), seed=7, vuote=("ZZCCC",))
    out = _gira(monkeypatch, tmp_path, PESI, prezzi)
    assert out["excluded_weight_pct"] == 15.0
    assert out["reliable"] is False
    for conf in ("var95", "var99"):
        assert out[conf]["verdict"] == "NON AFFIDABILE", out[conf]["verdict"]
        assert "15.0%" in out[conf]["verdict_detail"], out[conf]["verdict_detail"]
        assert "nomi esclusi" in out[conf]["verdict_detail"], out[conf]["verdict_detail"]
        # i numeri restano riportati: il buco si dichiara, non si nasconde il calcolo
        assert out[conf]["coverage"]["verdict"] in ("PASS", "FAIL")


def test_peso_escluso_sotto_soglia_resta_affidabile(monkeypatch, tmp_path):
    pesi = {"ZZAAA": 50000.0, "ZZBBB": 31000.0, "ZZCCC": 10000.0, "ZZDDD": 9000.0}
    prezzi = _prezzi(800, list(pesi), seed=7, vuote=("ZZDDD",))
    out = _gira(monkeypatch, tmp_path, pesi, prezzi)
    assert out["excluded_weight_pct"] == 9.0 and out["reliable"] is True
    assert out["var95"]["verdict"] in ("PASS", "FAIL")


def test_chiavi_esistenti_del_payload_restano(monkeypatch, tmp_path):
    out = _gira(monkeypatch, tmp_path, PESI, _prezzi(800, list(PESI), seed=3))
    assert out["excluded_tickers"] == [] and out["excluded_weight_pct"] == 0.0
    for k in ("timestamp", "window", "period", "n_obs_series", "n_obs_tested",
              "returns_basis", "sample_meta", "skipped_positions", "declared_scope",
              "official_var"):
        assert k in out, k
    for conf in ("var95", "var99"):
        for k in ("kupiec_pof", "christoffersen_ind", "conditional_coverage", "verdict"):
            assert k in out[conf], (conf, k)
    assert "note" in out["var99"]


# ------------------------------------------------------------------ due assi

def _sequenza(n, posizioni):
    e = np.zeros(n, dtype=int)
    e[list(posizioni)] = 1
    return e


def _assi(exc, alpha):
    kup = vb.kupiec_pof(len(exc), int(exc.sum()), alpha)
    ind = vb.christoffersen_independence(exc)
    return vb.verdetto_assi(kup, ind, alpha)


def test_fallisce_solo_indipendenza_e_lo_dice():
    """1000 giorni, 50 eccezioni (= attese al 95%) tutte a coppie consecutive."""
    pos = [i for s in range(10, 1000, 40) for i in (s, s + 1)][:50]
    v = _assi(_sequenza(1000, pos), 0.05)
    assert v["coverage"]["verdict"] == "PASS"
    assert v["independence"]["verdict"] == "FAIL"
    assert v["verdict"] == "FAIL"
    assert v["verdict_detail"].startswith("FAIL sull'asse indipendenza:"), v["verdict_detail"]
    assert "copertura PASS" in v["verdict_detail"]
    assert v["low_power"] is False


def test_fallisce_solo_copertura_e_lo_dice():
    """1000 giorni, 97 eccezioni (quasi il doppio delle 50 attese) con 10 coppie: le coppie
    sono quelle attese per caso (~x^2/n), quindi niente grappoli ne' anti-grappoli.
    (Il passo fisso NON va: zero coppie su 100 e' "troppo regolare" e Christoffersen,
    bilaterale, lo rifiuta.)"""
    pos = sorted({s + o for s in range(3, 1000, 100) for o in (0, 1)}
                 | set(list(range(40, 1000, 12))[:80]))
    v = _assi(_sequenza(1000, pos), 0.05)
    assert v["coverage"]["verdict"] == "FAIL"
    assert v["independence"]["verdict"] == "PASS"
    assert v["verdict"] == "FAIL"
    assert v["verdict_detail"].startswith("FAIL sull'asse copertura:"), v["verdict_detail"]
    assert "indipendenza PASS" in v["verdict_detail"]


# 49 eccezioni su 1000 (attese 50) con 3 coppie (~attese per caso)
_COERENTE = sorted({100, 101, 400, 401, 700, 701} | set(list(range(13, 1000, 22))[:44]))


def test_entrambi_gli_assi_passano():
    v = _assi(_sequenza(1000, _COERENTE), 0.05)
    assert (v["coverage"]["verdict"], v["independence"]["verdict"], v["verdict"]) == \
        ("PASS", "PASS", "PASS")


# ------------------------------------------------------------------ p esatti

def test_poche_eccezioni_attese_usa_binomiale_esatto():
    """495 giorni al 99%: 4,95 eccezioni attese -> bassa potenza dichiarata, p binomiale."""
    exc = _sequenza(495, [40, 160, 250, 330, 470, 480, 490])  # 7 eccezioni
    v = _assi(exc, 0.01)
    atteso = st.binomtest(7, 495, 0.01).pvalue
    assert v["low_power"] is True and v["expected_exceptions"] == 5.0
    assert v["coverage"]["p_value_binomial_exact"] == pytest.approx(atteso, abs=1e-4)
    assert v["coverage"]["p_value"] == pytest.approx(atteso, abs=1e-4)
    assert v["coverage"]["p_value"] != v["coverage"]["p_value_chi2"]
    assert "binomiale esatto" in v["coverage"]["test"]
    assert "bassa potenza" in v["verdict_detail"]


def test_molte_eccezioni_attese_resta_chi2():
    v = _assi(_sequenza(1000, _COERENTE), 0.05)
    assert "p_value_binomial_exact" not in v["coverage"]
    assert v["coverage"]["p_value"] == v["coverage"]["p_value_chi2"]
    assert "bassa potenza" not in v["verdict_detail"]


def test_indipendenza_esatta_contro_enumerazione_completa():
    n = 9
    for x in range(1, n):
        for obs in range(0, x + 1):
            tot = cnt = 0
            for comb in itertools.combinations(range(n), x):
                e = _sequenza(n, comb)
                tot += 1
                cnt += int(((e[:-1] == 1) & (e[1:] == 1)).sum() >= obs)
            assert vb.indipendenza_esatta(n, x, obs) == pytest.approx(cnt / tot, abs=1e-12), (x, obs)


def test_indipendenza_esatta_contro_permutazione():
    """Caso di forma R6 (5 eccezioni su 495, una coppia): p per permutazione con seed."""
    rng = np.random.default_rng(0)
    n, x, N = 495, 5, 20000
    cnt = 0
    for _ in range(N):
        e = np.zeros(n, int)
        e[rng.choice(n, x, replace=False)] = 1
        cnt += int(((e[:-1] == 1) & (e[1:] == 1)).sum() >= 1)
    assert vb.indipendenza_esatta(n, x, 1) == pytest.approx(cnt / N, abs=0.005)


def test_poche_eccezioni_indipendenza_usa_p_esatto():
    exc = _sequenza(495, [40, 41, 250, 330, 470])
    v = _assi(exc, 0.01)
    p = vb.indipendenza_esatta(495, 5, 1)
    assert v["independence"]["p_value_exact_conditional"] == pytest.approx(p, abs=1e-4)
    assert v["independence"]["p_value"] == pytest.approx(p, abs=1e-4)
    assert "esatto condizionato" in v["independence"]["test"]


# ------------------------------------------------------------------ review RV-R

def test_nome_quasi_vuoto_nella_finestra_testata_non_affidabile(monkeypatch, tmp_path):
    """ZZBBB (30%) ha solo le ULTIME 25 quotazioni su 800: supera min_obs, ma nei giorni
    testati manca quasi sempre. Il backtest NON puo' dirsi affidabile sul book."""
    prezzi = _prezzi(800, list(PESI), seed=7, corte={"ZZBBB": 25})
    out = _gira(monkeypatch, tmp_path, PESI, prezzi)
    assert out["excluded_weight_pct"] == 0.0
    assert out["missing_weight_tested_window_pct"] > 25.0, out["missing_weight_tested_window_pct"]
    assert "ZZBBB" in out["partial_coverage_tested_window"]
    assert out["reliable"] is False
    for conf in ("var95", "var99"):
        assert out[conf]["verdict"] == "NON AFFIDABILE"
        assert "ZZBBB" in out[conf]["verdict_detail"], out[conf]["verdict_detail"]


def test_nome_stantio_a_meta_non_affidabile(monkeypatch, tmp_path):
    prezzi = _prezzi(800, list(PESI), seed=7)
    prezzi.loc[prezzi.index[300]:, ("Close", "ZZBBB")] = np.nan
    out = _gira(monkeypatch, tmp_path, PESI, prezzi)
    assert out["missing_weight_tested_window_pct"] > 25.0
    assert out["reliable"] is False and out["var95"]["verdict"] == "NON AFFIDABILE"


def test_book_completo_peso_mancante_zero_misurato(monkeypatch, tmp_path):
    out = _gira(monkeypatch, tmp_path, PESI, _prezzi(800, list(PESI), seed=3))
    assert out["missing_weight_tested_window_pct"] == 0.0
    assert out["partial_coverage_tested_window"] == {} and out["reliable"] is True


def test_verdetto_sul_p_grezzo_non_arrotondato():
    """T=347, x=10 al 95%: p Kupiec vero 0.04997 (FAIL); arrotondato a 4 decimali = 0.05."""
    kup = vb.kupiec_pof(347, 10, 0.05)
    assert kup["p_value"] == 0.05 and kup["pass_5pct"] is False
    exc = _sequenza(347, range(3, 347, 35))
    assert int(exc.sum()) == 10
    v = vb.verdetto_assi(kup, vb.christoffersen_independence(exc), 0.05)
    assert v["coverage"]["verdict"] == "FAIL"
    assert v["verdict"] == "FAIL"


def test_asse_nd_non_diventa_fail():
    exc = _sequenza(1000, _COERENTE)
    v = vb.verdetto_assi({"error": "campione vuoto"}, vb.christoffersen_independence(exc), 0.05)
    assert v["coverage"]["verdict"] == "n.d."
    assert v["verdict"] == "n.d.", v["verdict"]
    assert v["verdict_detail"].startswith("n.d.: asse copertura"), v["verdict_detail"]


# ------------------------------------------------------------------ niente look-ahead, segno giusto

def _eccezioni_a_mano(prezzi, pesi, window, alpha, segno="<"):
    """Il VaR del giorno t = percentile (lineare, come pandas) dei `window` rendimenti
    t-window..t-1; eccezione se il rendimento di t e' SOTTO il VaR. Libro completo: la
    media pesata per-giorno coincide con la somma pesata."""
    w = np.array(list(pesi.values()), float)
    w /= w.sum()
    ret = prezzi["Close"].pct_change(fill_method=None).dropna(how="all")
    port = (ret[list(pesi)] * w).sum(axis=1, min_count=1).dropna().values
    exc = []
    for i in range(window, len(port)):
        # segno ">" = lo specchio sbagliato: coda DESTRA (guadagni sopra il quantile 1-alpha)
        q = alpha * 100 if segno == "<" else (1 - alpha) * 100
        var = np.percentile(port[i - window:i], q)
        exc.append(int(port[i] < var) if segno == "<" else int(port[i] > var))
    return len(port), np.array(exc)


def test_niente_look_ahead_il_var_di_t_usa_solo_fino_a_t_meno_1(monkeypatch, tmp_path):
    prezzi = _prezzi(800, list(PESI), seed=9)
    out = _gira(monkeypatch, tmp_path, PESI, prezzi)
    n_series, _ = _eccezioni_a_mano(prezzi, PESI, 252, 0.05)
    assert out["n_obs_series"] == n_series
    for conf, alpha in (("var95", 0.05), ("var99", 0.01)):
        _, exc = _eccezioni_a_mano(prezzi, PESI, 252, alpha)
        k = out[conf]["kupiec_pof"]
        assert k["obs"] == out["n_obs_series"] - 252 == len(exc), (conf, k["obs"])
        assert k["exceptions"] == int(exc.sum()), (conf, k["exceptions"], int(exc.sum()))
        tr = out[conf]["christoffersen_ind"]["transitions"]
        n11 = int(((exc[:-1] == 1) & (exc[1:] == 1)).sum())
        assert tr["n11"] == n11, (conf, tr, n11)


def test_eccezione_e_la_perdita_sotto_il_var_non_il_guadagno(monkeypatch, tmp_path):
    """Serie con coda sinistra pesante (crolli rari, rialzi piccoli): le eccezioni vere
    (rendimento < VaR) e quelle col segno invertito sono numeri DIVERSI, quindi il test
    distingue davvero il segno."""
    rng = np.random.default_rng(21)
    n = 800
    idx = pd.bdate_range("2021-01-04", periods=n)
    data = {}
    for t in PESI:
        r = rng.normal(0.0005, 0.006, size=n)
        crolli = rng.random(n) < 0.04
        r[crolli] -= rng.uniform(0.03, 0.06, size=int(crolli.sum()))
        data[("Close", t)] = 100.0 * np.cumprod(1 + r)
    prezzi = pd.DataFrame(data, index=idx)
    prezzi.columns = pd.MultiIndex.from_tuples(prezzi.columns)
    out = _gira(monkeypatch, tmp_path, PESI, prezzi)
    for conf, alpha in (("var95", 0.05), ("var99", 0.01)):
        _, giuste = _eccezioni_a_mano(prezzi, PESI, 252, alpha, "<")
        _, rovesce = _eccezioni_a_mano(prezzi, PESI, 252, alpha, ">")
        assert int(giuste.sum()) != int(rovesce.sum()), "fixture non discrimina il segno"
        assert out[conf]["kupiec_pof"]["exceptions"] == int(giuste.sum()), conf
