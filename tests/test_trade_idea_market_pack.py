"""Test del pacchetto dati di mercato Trade Idea (Lotto 3, L1, Opus 5.5, 04/10/2026).

Tutti i fornitori sono finti: ticker e numeri INVENTATI (ZZ*/QQ*), nessuna rete.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from bellomberg.market_data import trade_idea_market_pack as tmp_mod
from bellomberg.market_data.trade_idea_market_pack import build_market_pack

TODAY = date(2026, 10, 2)


def _bdays(start: date, end_excl: date):
    d = start
    while d < end_excl:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _walk(seed: int, n: int, p0: float = 50.0):
    rnd = random.Random(seed)
    out, p = [], p0
    for _ in range(n):
        p *= math.exp(rnd.gauss(0.0002, 0.015))
        out.append(p)
    return out


class Fornitore:
    """fetch finto: specs[symbol] = dict(currency, first, last, const, holes, adj_mult, raise_)."""

    def __init__(self, specs):
        self.specs = specs
        self.calls = []

    def __call__(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        sp = self.specs.get(symbol)
        if sp is None:
            raise KeyError(symbol)
        if sp.get("raise_"):
            raise sp["raise_"]
        if sp.get("raw") is not None:
            return sp["raw"]
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        first = sp.get("first") or s
        last = sp.get("last") or (e + timedelta(days=3))     # anche oltre la fine: va tagliato
        days = list(_bdays(first, last + timedelta(days=1)))
        vals = ([sp["const"]] * len(days) if "const" in sp
                else _walk(sp.get("seed", 1), len(days), sp.get("p0", 50.0)))
        rows = []
        for d, v in zip(days, vals):
            hole = d in sp.get("holes", ())
            rows.append({"date": d.isoformat(),
                         "close": None if hole else v,
                         "adj_close": None if hole else v * sp.get("adj_mult", 1.0)})
        return {"symbol": sp.get("symbol", symbol), "currency": sp.get("currency", "USD"),
                "timezone": "X", "observations": rows}


def _base_specs():
    return {"ZZCAND.MI": {"currency": "EUR", "seed": 3, "adj_mult": 0.9},
            "^SP500TR": {"currency": "USD", "seed": 4, "p0": 4000.0},
            "^VIX": {"currency": "USD", "seed": 5, "p0": 18.0},
            "QQPEER1.PA": {"currency": "EUR", "seed": 6},
            "QQPEER2.L": {"currency": "GBp", "const": 1000.0}}


def _fx_specs():
    return {"EURUSD=X": {"currency": "USD", "const": 1.10},
            "EURGBP=X": {"currency": "GBP", "const": 0.80},
            "EURDKK=X": {"currency": "DKK", "const": 7.45}}


INFO = {
    "ZZCAND.MI": {"longName": "Zeta Sintetica S.p.A.", "enterpriseValue": 1200.0, "totalRevenue": 400.0, "ebitda": 100.0,
                  "trailingPE": 15.0, "forwardPE": 12.5, "priceToBook": 2.0,
                  "currency": "EUR", "financialCurrency": "EUR"},
    "QQPEER1.PA": {"longName": "Qu Uno SA", "enterpriseValue": 900.0, "totalRevenue": 300.0, "ebitda": -5.0,
                   "trailingPE": None, "forwardPE": 11.0, "currency": "EUR", "financialCurrency": "EUR"},
    "QQPEER2.L": {"longName": "Qu Due plc", "enterpriseValue": 500.0, "totalRevenue": 250.0, "ebitda": 50.0,
                  "trailingPE": 9.0, "currency": "GBp", "financialCurrency": "USD"},
}

HIST = {"_source": "XBRL finto", "units": {"revenue": "EUR", "eps_diluted": "EUR/shares", "equity": "EUR"},
        "items": {"eps_diluted": {2023: 2.0, 2024: -1.0},
                  "shares_diluted": {2023: 10.0, 2024: 10.0},
                  "revenue": {2023: 400.0, 2024: 420.0},
                  "operating_income": {2023: 60.0, 2024: 70.0},
                  "dep_amort": {2023: 40.0},
                  "cash": {2023: 50.0, 2024: 60.0},
                  "lt_debt": {2023: 150.0, 2024: 140.0},
                  "equity": {2023: 250.0, 2024: 0.0}}}


def _build(specs=None, fx=None, peers=("QQPEER1.PA", "QQPEER2.L"), info=None, hist=None,
           selector=None, **kw):
    f = Fornitore(specs or _base_specs())
    fxf = Fornitore(fx if fx is not None else _fx_specs())
    info = INFO if info is None else info

    def info_fetch(sym):
        return dict(info[sym])

    pack = build_market_pack(
        candidate={"ticker": "ZZCAND.MI", "name": "Zeta Sintetica"}, peers=list(peers) if peers else peers,
        today=TODAY, fetch=f, fx_fetch=fxf, info_fetch=info_fetch,
        history_loader=(lambda t, n: hist if hist is not None else HIST),
        peer_selector=selector or (lambda t, i: pytest.fail("selettore chiamato con lista PM")),
        resolve_symbols=lambda tks: {t: t for t in tks}, **kw)
    return pack, f, fxf


# ------------------------------------------------------------ finestra e campi

def test_finestra_cinque_anni_oggi_escluso_e_adj_close_esplicito():
    pack, f, _ = _build()
    assert pack["version"] == 1 and pack["base_currency"] == "EUR"
    assert f.calls[0] == ("ZZCAND.MI", "2021-10-02", "2026-10-02")
    c = pack["series"]["candidate"]
    assert c["dates"][0] >= "2021-10-02" and c["dates"][-1] < "2026-10-02"
    assert c["price_field"] == "adj_close" and c["source"] == "yfinance Adj Close"
    # il valore e' l'Adj Close (0,9 x Close nel finto), non il Close
    raw = f("ZZCAND.MI", "2021-10-02", "2026-10-02")["observations"]
    first = next(r for r in raw if r["date"] == c["dates"][0])
    assert c["values"][0] == pytest.approx(first["adj_close"])
    assert c["close_native"][0] == pytest.approx(first["close"])
    assert len(c["sha256"]) == 64 and len(pack["sha256"]) == 64
    assert pack["as_of"] == c["coverage"]["last"] == "2026-10-01"


def test_benchmark_unico_sp500_e_vix_livello_non_convertito():
    pack, _, _ = _build()
    b, v = pack["series"]["benchmark"], pack["series"]["vix"]
    assert b["ticker"] == "^SP500TR" == pack["benchmark_ticker"]
    assert "Total Return" in b["source"] and "proxy" not in b and b["index_type"] == "total_return"
    assert v["ticker"] == "^VIX" and v["price_field"] == "level"
    assert v["currency"] == "USD"
    assert v["conversion"]["method"] == "none_level"
    assert "close_native" not in v and "close_native" not in b


# ------------------------------------------------------------ conversione EUR

def test_conversione_eur_divide_per_il_cambio_della_stessa_data():
    pack, _, _ = _build()
    b = pack["series"]["benchmark"]
    raw = Fornitore(_base_specs())("^SP500TR", "2021-10-02", "2026-10-02")["observations"]
    by_date = {r["date"]: r["adj_close"] for r in raw}
    for d, val in list(zip(b["dates"], b["values"]))[:5]:
        assert val == pytest.approx(by_date[d] / 1.10)
    assert b["currency"] == "EUR" and b["native_currency"] == "USD"
    cv = b["conversion"]
    assert (cv["method"], cv["fx_ticker"], cv["fx_field"], cv["rate_unit"]) == (
        "fx_same_date", "EURUSD=X", "Close", "USD per EUR")
    assert cv["n_dropped_no_fx"] == 0
    assert "STESSA data" in cv["note"] and "nessun fill" in cv["note"]


def test_conversione_unita_minore_pence():
    pack, _, _ = _build()
    p2 = pack["peers"][1]
    assert p2["ticker"] == "QQPEER2.L"
    assert p2["values"] and all(v == pytest.approx(1000.0 / 100 / 0.80) for v in p2["values"])
    assert p2["conversion"]["fx_ticker"] == "EURGBP=X" and "divisa per 100" in p2["conversion"]["note"]
    assert p2["native_currency"] == "GBp" and p2["currency"] == "EUR"


def test_candidato_in_eur_non_convertito_ma_dichiarato():
    pack, _, fxf = _build()
    c = pack["series"]["candidate"]
    assert c["currency"] == "EUR" and c["native_currency"] == "EUR"
    assert c["conversion"]["method"] == "native"
    assert "EURUSD=X" in [x[0] for x in fxf.calls]          # solo per chi non e' in EUR
    assert not any(x[0] == "EUREUR=X" for x in fxf.calls)


def test_data_senza_cambio_diventa_buco_contato_senza_fill():
    fx = _fx_specs()
    giorno = date(2024, 3, 5)
    fx["EURUSD=X"]["holes"] = [giorno]
    pack, _, _ = _build(fx=fx)
    b = pack["series"]["benchmark"]
    i = b["dates"].index(giorno.isoformat())
    assert b["values"][i] is None
    assert b["values"][i - 1] is not None and b["values"][i + 1] is not None
    assert b["conversion"]["n_dropped_no_fx"] == 1
    assert "1 date senza cambio EURUSD=X" in b["reason"]
    assert pack["status"] == "partial"


def test_cambio_assente_serie_in_errore_nessun_ripiego_in_valuta_locale():
    fx = _fx_specs()
    fx["EURUSD=X"]["raise_"] = ConnectionError("giu'")
    specs = _base_specs()
    specs["^GSPC"] = {"currency": "USD", "seed": 4, "p0": 4000.0}
    pack, _, _ = _build(fx=fx, specs=specs)
    b = pack["series"]["benchmark"]
    assert b["status"] == "error" and b["values"] == [] and b["dates"] == []
    assert "EURUSD=X" in b["reason"] and "ConnectionError" in b["reason"]
    assert "nessun ripiego" in b["reason"]
    assert pack["status"] == "partial"


def test_cambio_con_valuta_sbagliata_rifiutato():
    fx = _fx_specs()
    fx["EURUSD=X"]["currency"] = "EUR"
    pack, _, _ = _build(fx=fx)
    assert pack["series"]["benchmark"]["status"] == "error"


# ------------------------------------------------------------ status delle serie

def test_storia_insufficiente_dichiarata():
    specs = _base_specs()
    specs["ZZCAND.MI"]["first"] = date(2024, 6, 3)          # ~2,3 anni
    pack, _, _ = _build(specs=specs)
    c = pack["series"]["candidate"]
    assert c["coverage"]["sufficient"] is False
    assert c["reason"].startswith("Candidato (ZZCAND.MI): storia insufficiente")
    assert "servono almeno 3 anni" in c["reason"]
    assert pack["status"] == "partial"


def test_storia_di_tre_anni_e_mezzo_sufficiente_ma_finestra_corta_dichiarata():
    specs = _base_specs()
    specs["ZZCAND.MI"]["first"] = date(2023, 3, 1)
    pack, _, _ = _build(specs=specs)
    c = pack["series"]["candidate"]
    assert c["coverage"]["sufficient"] is True
    assert "storia piu' corta della finestra richiesta" in c["reason"]


def test_serie_vecchia_stale():
    specs = _base_specs()
    specs["QQPEER1.PA"]["last"] = date(2026, 9, 10)
    pack, _, _ = _build(specs=specs)
    p = pack["peers"][0]
    assert p["status"] == "stale" and "ultimo prezzo al 10/09/2026" in p["reason"]


def test_buchi_del_fornitore_conservati_none():
    specs = _base_specs()
    specs["ZZCAND.MI"]["holes"] = [date(2025, 1, 6), date(2025, 1, 7)]
    pack, _, _ = _build(specs=specs)
    c = pack["series"]["candidate"]
    assert c["values"][c["dates"].index("2025-01-06")] is None
    assert c["coverage"]["n_missing"] == 2 and "2 chiusure mancanti" in c["reason"]


def test_valuta_non_dichiarata_errore_mai_dedotta():
    specs = _base_specs()
    specs["QQPEER1.PA"]["currency"] = None
    pack, _, _ = _build(specs=specs)
    assert pack["peers"][0]["status"] == "error"
    assert "valuta non dichiarata" in pack["peers"][0]["reason"]


def test_simbolo_diverso_dal_richiesto_errore():
    specs = _base_specs()
    specs["QQPEER1.PA"]["symbol"] = "ALTRO"
    pack, _, _ = _build(specs=specs)
    assert pack["peers"][0]["status"] == "error"


def test_download_candidato_fallito_pacchetto_unavailable():
    specs = _base_specs()
    specs["ZZCAND.MI"]["raise_"] = TimeoutError()
    pack, _, _ = _build(specs=specs)
    assert pack["status"] == "unavailable"
    assert pack["series"]["candidate"]["status"] == "error"
    assert "TimeoutError" in pack["reason"]


def test_mai_solleva_su_parametri_rotti():
    pack = build_market_pack(candidate="", peers=None, today=TODAY,
                             fetch=lambda *a: pytest.fail("rete"), fx_fetch=lambda *a: None)
    assert pack["status"] == "unavailable" and "ValueError" in pack["reason"]
    pack2, _, _ = _build(window_years=2)
    assert pack2["status"] == "unavailable"


# ------------------------------------------------------------ peer

def test_peer_dalla_lista_del_pm():
    pack, _, _ = _build()
    assert pack["peer_selection"]["method"] == "pm"
    assert [p["ticker"] for p in pack["peers"]] == ["QQPEER1.PA", "QQPEER2.L"]


def test_peer_dal_selettore_con_scarti_e_pe_dichiarato():
    chiamate = []

    def selector(t, info):
        chiamate.append((t, info.get("totalRevenue")))
        return ([{"name": "Peer Uno (QQPEER1.PA)"}, {"name": "senza ticker"}],
                "peer FIT deterministico [x] — criteri: size; tenuti 1; scartati: QQBIG.DE (size 30.0x fuori "
                "banda 0.1-10x), QQNEG.PA (margine EBITDA +20pp dal target)")

    pack, _, _ = _build(peers=None, selector=selector)
    assert chiamate == [("ZZCAND.MI", 400.0)]
    sel = pack["peer_selection"]
    assert sel["method"] == "select_peer_comps"
    assert pack["multiples"]["peer_selection"] is sel
    assert [p["ticker"] for p in pack["peers"]] == ["QQPEER1.PA"]
    assert {"ticker": "QQBIG.DE", "reason": "size 30.0x fuori banda 0.1-10x"} in sel["rejected"]
    assert len(sel["rejected"]) == 2
    assert "1 peer senza ticker leggibile" in sel["note"]
    assert "P/E del selettore" in sel["note"] and "NON e' usato" in sel["note"]


def test_selettore_senza_peer_dichiarato():
    pack, _, _ = _build(peers=[], selector=lambda t, i: ([], "NESSUN peer sopravvive al fit"))
    assert pack["peers"] == []
    assert any("nessun peer" in i["reason"] for i in pack["issues"])
    assert pack["status"] == "partial"


# ------------------------------------------------------------ multipli correnti

def test_multipli_correnti_pe_trailing_e_forward_separati():
    pack, _, _ = _build()
    cur = {m["ticker"]: m for m in pack["multiples"]["current"]}
    c = cur["ZZCAND.MI"]
    assert (c["pe_trailing"], c["pe_forward"]) == (15.0, 12.5)
    assert c["ev_sales"] == 3.0 and c["ev_ebitda"] == 12.0 and c["status"] == "ok"
    p1 = cur["QQPEER1.PA"]
    assert p1["pe_trailing"] is None and p1["pe_forward"] == 11.0     # mai il forward al posto del trailing
    assert p1["ev_ebitda"] is None
    assert any("EBITDA assente o non positivo" in m for m in p1["missing"])
    assert any("P/E trailing n.d." in m for m in p1["missing"])


def test_multipli_correnti_valute_diverse_non_calcolati():
    pack, _, _ = _build()
    p2 = {m["ticker"]: m for m in pack["multiples"]["current"]}["QQPEER2.L"]
    assert p2["ev_sales"] is None and p2["ev_ebitda"] is None
    assert any("valute" in m and "GBp" in m and "USD" in m for m in p2["missing"])
    assert p2["pe_trailing"] == 9.0


# ------------------------------------------------------------ multipli storici

def test_multipli_storici_formule_e_approssimazioni_dichiarate():
    specs = _base_specs()
    specs["ZZCAND.MI"] = {"currency": "EUR", "const": 20.0, "adj_mult": 0.9}
    pack, _, _ = _build(specs=specs)
    h = pack["multiples"]["history"]
    assert h["status"] == "ok" and h["currency"] == "EUR"
    assert any("SENZA debito a breve" in a for a in h["approximations"])
    assert any("EBITDA = risultato operativo (EBIT) + ammortamenti" in a for a in h["approximations"])
    assert any("31/12" in a for a in h["approximations"])
    y = {r["fy"]: r for r in h["years"]}
    r23 = y[2023]
    assert r23["close_fy_end"] == 20.0 and r23["close_date"] == "2023-12-29"   # Close, non Adj
    assert r23["pe"] == 10.0                                # 20 / 2
    assert r23["ev"] == 300.0                               # 20*10 + 150 - 50
    assert r23["ev_sales"] == 0.75 and r23["ebitda"] == 100.0 and r23["ev_ebitda"] == 3.0
    assert r23["p_b"] == 0.8
    r24 = y[2024]
    assert r24["pe"] is None and any("EPS diluito non positivo" in m for m in r24["missing"])
    assert r24["ev_ebitda"] is None and any("ammortamenti assente" in m for m in r24["missing"])
    assert r24["p_b"] is None and any("patrimonio netto non positivo" in m for m in r24["missing"])


def test_multipli_storici_valuta_bilancio_diversa_nd():
    hist = dict(HIST, units={"revenue": "USD", "eps_diluted": "USD/shares", "equity": "USD"})
    pack, _, _ = _build(hist=hist)
    h = pack["multiples"]["history"]
    assert h["status"] == "missing" and h["years"] == []
    assert "valuta del bilancio (USD) diversa" in h["reason"]


def test_multipli_storici_fonte_assente_dichiarata():
    pack, _, _ = _build(hist={"error": "nessun CIK"})
    h = pack["multiples"]["history"]
    assert h["status"] == "missing" and "nessun CIK" in h["reason"]
    assert any("nessun CIK" in i["reason"] for i in pack["issues"])


# ------------------------------------------------------------ contratto con L2

def test_pacchetto_letto_dalle_statistiche_senza_gap_di_valuta():
    from bellomberg.reporting.trade_idea_market_stats import market_stats
    pack, _, _ = _build()
    st = market_stats(pack, window_years=5, language="it")
    assert st["moments"]["status"] == "ok"
    assert st["beta"]["status"] == "ok"
    assert st["vix_sensitivity"]["status"] == "ok"
    assert not any("non convertita" in g for g in st["gaps"])
    assert not any("valute diverse" in g for g in st["gaps"])


def test_storia_insufficiente_arriva_insufficiente_alle_statistiche():
    from bellomberg.reporting.trade_idea_market_stats import market_stats
    specs = _base_specs()
    specs["ZZCAND.MI"]["first"] = date(2024, 6, 3)
    pack, _, _ = _build(specs=specs)
    st = market_stats(pack, window_years=5, language="it")
    assert st["moments"]["status"] == "insufficient"


def test_cambio_mancante_arriva_alle_statistiche_come_gap():
    from bellomberg.reporting.trade_idea_market_stats import market_stats
    fx = _fx_specs()
    fx["EURUSD=X"]["holes"] = [date(2024, 3, 5)]
    pack, _, _ = _build(fx=fx)
    st = market_stats(pack, window_years=5, language="it")
    assert any("cambio mancante" in g or "date tolte" in g for g in st["gaps"]), st["gaps"]


def test_forma_come_la_fixture_di_l2():
    import json
    from pathlib import Path
    fix = json.loads((Path(__file__).parent / "fixtures" / "market_pack_synth.json").read_text(encoding="utf-8"))
    pack, _, _ = _build()
    assert pack["version"] == fix["version"]
    assert set(fix) - {"_generatore"} <= set(pack)
    for role, entry in fix["series"].items():
        assert set(entry) <= set(pack["series"][role]), role
        assert set(entry["conversion"]) <= set(pack["series"][role]["conversion"]) | {"note"}
    assert set(fix["peers"][0]) <= set(pack["peers"][0])


# ------------------------------------------------------------ file accanto alla run

from bellomberg.market_data.trade_idea_market_pack import (  # noqa: E402
    load_market_pack, normalize_request_peers, run_market_pack, save_market_pack)


def test_salva_e_rilegge_verificando_lo_sha(tmp_path):
    pack, _, _ = _build()
    ref = save_market_pack(pack, tmp_path / "run" / "market-pack.json")
    assert set(ref) >= {"path", "sha256", "status", "as_of"}
    assert ref["status"] == pack["status"] and ref["as_of"] == pack["as_of"]
    assert not [p for p in (tmp_path / "run").iterdir() if p.name.endswith(".tmp")]
    assert load_market_pack(ref) == pack


def test_file_alterato_unavailable_dichiarato_mai_ricostruito(tmp_path):
    pack, _, _ = _build()
    ref = save_market_pack(pack, tmp_path / "market-pack.json")
    p = tmp_path / "market-pack.json"
    p.write_bytes(p.read_bytes().replace(b'"ready"', b'"READY"'))
    out = load_market_pack(ref)
    assert out["status"] == "unavailable" and "sha256 non corrisponde" in out["reason"]
    assert out["series"] == {} and out["peers"] == []


def test_file_assente_e_riferimento_rotto_dichiarati(tmp_path):
    pack, _, _ = _build()
    ref = save_market_pack(pack, tmp_path / "market-pack.json")
    (tmp_path / "market-pack.json").unlink()
    assert load_market_pack(ref)["status"] == "unavailable"
    assert "non valido" in load_market_pack({"path": 3})["reason"]
    assert load_market_pack(None) is None                     # run precedenti al Lotto 3


def test_run_market_pack_db_alternativo_senza_loader_niente_rete(tmp_path):
    ref = run_market_pack(run={"ticker": "ZZCAND.MI"}, cutoff="2026-10-02T10:00:00+00:00",
                          path=tmp_path / "m.json", loader=None, network_allowed=False)
    assert ref["status"] == "unavailable" and "nessun download" in ref["reason"]
    assert not (tmp_path / "m.json").exists()
    assert load_market_pack(ref)["reason"] == ref["reason"]


def test_run_market_pack_passa_peer_del_pm_e_cutoff(tmp_path):
    visti = {}

    def loader(**kw):
        visti.update(kw)
        return {"version": 1, "status": "ready", "as_of": "2026-10-01", "series": {}, "peers": []}

    ref = run_market_pack(run={"ticker": "ZZCAND.MI", "company_name": "Zeta", "peers": ["QQPEER1.PA"]},
                          cutoff="2026-10-02T10:00:00+00:00", path=tmp_path / "m.json", loader=loader)
    assert visti == {"candidate": {"ticker": "ZZCAND.MI", "name": "Zeta"}, "peers": ["QQPEER1.PA"],
                     "today": "2026-10-02"}
    assert ref["status"] == "ready" and load_market_pack(ref)["as_of"] == "2026-10-01"


def test_run_market_pack_loader_che_solleva_non_fa_cadere_la_run(tmp_path):
    def loader(**kw):
        raise RuntimeError("giu'")
    ref = run_market_pack(run={"ticker": "ZZCAND.MI"}, cutoff="2026-10-02", path=tmp_path / "m.json",
                          loader=loader)
    assert ref["status"] == "unavailable" and "RuntimeError" in ref["reason"]


def test_peer_del_pm_oltre_sei_fino_a_otto_tenuti():
    peers = ["QQP%d.PA" % i for i in range(8)]
    specs = _base_specs()
    for p in peers:
        specs[p] = {"currency": "EUR", "seed": 7}
    info = dict(INFO, **{p: {"currency": "EUR", "longName": "Peer %s" % p} for p in peers})
    pack, _, _ = _build(specs=specs, peers=peers, info=info)
    assert [p["ticker"] for p in pack["peers"]] == peers


def test_normalize_request_peers():
    assert normalize_request_peers(None, ticker="ZZCAND.MI") == []
    assert normalize_request_peers([" qqpeer1.pa "], ticker="ZZCAND.MI") == ["QQPEER1.PA"]
    for bad, why in ((["ZZCAND.MI"], "se stesso"), (["QQA.PA", "qqa.pa"], "ripetuto"),
                     (["QQ%d" % i for i in range(9)], "al massimo 8"), ("QQA", "lista"),
                     ([""], "non valido"), ([3], "testuale")):
        with pytest.raises(ValueError, match=why):
            normalize_request_peers(bad, ticker="ZZCAND.MI")


# ------------------------------------------------------------ cablaggio in agents/trade_idea.py
# Guardia STRUTTURALE (ast): le e2e di execute_trade_idea sono rosse gia' nel baseline
# e44e955 per cause estranee, quindi il cablaggio non e' provato end-to-end (dichiarato).

def _trade_idea_ast():
    import ast
    from pathlib import Path
    import bellomberg.agents.trade_idea as ti
    return ast.parse(Path(ti.__file__).read_text(encoding="utf-8"))


def _func(tree, name):
    import ast
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_cablaggio_execute_salva_il_riferimento_senza_rete_su_db_alternativo():
    import ast
    fn = _func(_trade_idea_ast(), "execute_trade_idea")
    assert "market_pack_loader" in [a.arg for a in fn.args.kwonlyargs]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "run_market_pack"]
    assert len(calls) == 1
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert kw["loader"] == "market_pack_loader"
    assert kw["network_allowed"] == "default_risk_db_matches"
    assert "PACK_FILENAME" in kw["path"] and "output_dir" in kw["path"]
    src = ast.unparse(fn)
    assert "blackboard.data['_market_pack'] = run_market_pack(" in src
    # il riferimento si salva PRIMA del checkpoint 'capo'
    assert src.index("run_market_pack(") < src.index("'capo', _progress(blackboard, 'capo'")


def test_cablaggio_renderer_riceve_il_pacchetto_riletto_nei_due_punti():
    import ast
    tree = _trade_idea_ast()
    for name in ("_preview_quality", "_deliver_trade_idea"):
        src = ast.unparse(_func(tree, name))
        assert "'market_pack': _load_market_pack(" in src, name
        assert "'_market_pack'" in src, name


# ------------------------------------------------------------ peer del PM nella richiesta della run

from test_trade_idea_store import db_path, migrated  # noqa: E402,F401  (fixture: SQLite in tmp_path)
from test_trade_idea_store import request as _ti_request, store as _ti_store  # noqa: E402


def test_store_salva_i_peer_del_pm_normalizzati_ed_esposti_nella_run(migrated):
    s = _ti_store(migrated)
    req = {**_ti_request(), "peers": [" qqpeer1.pa", "QQPEER2.L"]}
    run = s.create_run(req, idempotency_key="peers-1")["run"]
    assert run["peers"] == ["QQPEER1.PA", "QQPEER2.L"]
    assert s.get_accepted_request(run["id"])["peers"] == ["QQPEER1.PA", "QQPEER2.L"]


def test_store_senza_peer_richiesta_invariata(migrated):
    s = _ti_store(migrated)
    run = s.create_run(_ti_request(), idempotency_key="peers-0")["run"]
    assert run["peers"] is None
    assert "peers" not in s.get_accepted_request(run["id"])


@pytest.mark.parametrize("bad", [[], ["TEST"], ["QQA", "qqa"], ["QQ%d" % i for i in range(9)], "QQA"])
def test_store_rifiuta_peer_invalidi(migrated, bad):
    with pytest.raises(ValueError, match="peers"):
        _ti_store(migrated).create_run({**_ti_request(), "peers": bad}, idempotency_key="peers-x")


def test_rotta_binding_invariato_senza_peer_e_diverso_con_peer():
    from bellomberg.api.trade_idea_routes import PreflightBody, _preflight_binding
    kw = {"db_path": "x.db", "language": "it"}
    base = PreflightBody(ticker="ZZCAND.MI", budget_limit_usd="5")
    vuoto = PreflightBody(ticker="ZZCAND.MI", budget_limit_usd="5", peers=[])
    con = PreflightBody(ticker="ZZCAND.MI", budget_limit_usd="5", peers=["qqpeer1.pa"])
    assert _preflight_binding(base, "s", **kw) == _preflight_binding(vuoto, "s", **kw)
    assert _preflight_binding(con, "s", **kw) != _preflight_binding(base, "s", **kw)
    with pytest.raises(ValueError):
        _preflight_binding(PreflightBody(ticker="ZZCAND.MI", budget_limit_usd="5", peers=["zzcand.mi"]),
                           "s", **kw)


def test_rotta_peer_oltre_otto_rifiutati_dal_modello():
    from pydantic import ValidationError
    from bellomberg.api.trade_idea_routes import PreflightBody
    with pytest.raises(ValidationError):
        PreflightBody(ticker="ZZCAND.MI", peers=["QQ%d" % i for i in range(9)])


def test_cablaggio_rotta_start_porta_i_peer_nella_richiesta_e_nel_retry():
    import ast
    from pathlib import Path
    import bellomberg.api.trade_idea_routes as r
    src = ast.unparse(ast.parse(Path(r.__file__).read_text(encoding="utf-8")))
    assert "request['peers'] = _body_peers(body)" in src
    assert "(run.get('peers') or []) == _body_peers(body)" in src


def test_rotta_binding_senza_peer_identico_alla_formula_precedente():
    """Oracolo congelato: senza peer il binding e' quello di prima del Lotto 3
    (i preflight gia' in memoria e i retry esistenti restano validi)."""
    import json
    from decimal import Decimal
    from hashlib import sha256
    from pathlib import Path
    from bellomberg.api.trade_idea_routes import (PreflightBody, _preflight_binding, normalize_budget,
                                                  normalize_sources)
    body = PreflightBody(ticker="zzcand.mi", budget_limit_usd="5")
    bound = {"db_path": str(Path("x.db").resolve()),
             "session_sha256": sha256(b"s").hexdigest(),
             "ticker": "ZZCAND.MI", "view_text": "", "view_origin": "manual", "language": "it",
             "budget": str(Decimal(normalize_budget("5")).normalize()),
             "documents": normalize_sources([])}
    atteso = sha256(json.dumps(bound, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("utf-8")).hexdigest()
    assert _preflight_binding(body, "s", db_path="x.db", language="it") == atteso


def test_peer_che_dopo_l_alias_e_il_candidato_scartato_dichiarato():
    specs = _base_specs()
    specs["ZZC.DE"] = specs.pop("ZZCAND.MI")
    specs["ZZC.DE"]["currency"] = "EUR"
    info = dict(INFO, **{"ZZC.DE": INFO["ZZCAND.MI"]})
    f = Fornitore(specs)
    pack = build_market_pack(
        candidate="ZZC.FRA", peers=["ZZC.DE", "QQPEER1.PA", "QQPEER1.XX"], today=TODAY, fetch=f,
        fx_fetch=Fornitore(_fx_specs()), info_fetch=lambda s: dict(info[s]),
        history_loader=lambda t, n: HIST, peer_selector=lambda t, i: pytest.fail("selettore"),
        resolve_symbols=lambda tks: {t: {"ZZC.FRA": "ZZC.DE", "QQPEER1.XX": "QQPEER1.PA"}.get(t, t) for t in tks})
    assert pack["series"]["candidate"]["ticker"] == "ZZC.DE"
    assert [p["ticker"] for p in pack["peers"]] == ["QQPEER1.PA"]
    assert [m["ticker"] for m in pack["multiples"]["current"] if m["role"] == "peer"] == ["QQPEER1.PA"]
    rej = {r["ticker"]: r["reason"] for r in pack["peer_selection"]["rejected"]}
    assert "stesso simbolo Yahoo del candidato" in rej["ZZC.DE"]
    assert "doppione" in rej["QQPEER1.XX"]
    assert any("peer scartato" in i["reason"] for i in pack["issues"]) and pack["status"] == "partial"


def test_cablaggio_rotta_start_valueerror_di_create_run_diventa_422():
    import ast
    from pathlib import Path
    import bellomberg.api.trade_idea_routes as r
    src = ast.unparse(ast.parse(Path(r.__file__).read_text(encoding="utf-8")))
    i = src.index("accepted = current.create_run(request")
    blocco = src[i:i + 400]
    assert "except ValueError as exc:" in blocco and "HTTPException(422" in blocco


def test_total_return_assente_ripiego_su_prezzo_dichiarato_proxy():
    specs = _base_specs()
    specs["^SP500TR"]["raise_"] = ConnectionError()
    specs["^GSPC"] = {"currency": "USD", "seed": 4, "p0": 4000.0}
    pack, f, _ = _build(specs=specs)
    b = pack["series"]["benchmark"]
    assert b["ticker"] == "^GSPC" == pack["benchmark_ticker"] and b["status"] == "ok"
    assert b["index_type"] == "price"
    assert "PROXY" in b["proxy"] and "senza dividendi" in b["source"] and "include i dividendi" in b["source"]
    assert any(i["symbol"] == "^GSPC" and "PROXY" in i["reason"] for i in pack["issues"])
    assert pack["status"] == "partial"
    assert [c[0] for c in f.calls][:3] == ["ZZCAND.MI", "^SP500TR", "^GSPC"]


def test_total_return_presente_niente_ripiego():
    pack, f, _ = _build()
    assert "^GSPC" not in [c[0] for c in f.calls]


def test_peer_stesso_emittente_altro_listino_o_isin_scartato_dichiarato():
    specs = _base_specs()
    specs["QQISIN0001"] = {"currency": "USD", "seed": 9}
    info = dict(INFO, QQISIN0001={"longName": "ZETA SINTETICA SPA", "currency": "USD"})
    pack, f, _ = _build(specs=specs, peers=["QQISIN0001", "QQPEER1.PA"], info=info)
    assert [p["ticker"] for p in pack["peers"]] == ["QQPEER1.PA"]
    rej = {r["ticker"]: r["reason"] for r in pack["peer_selection"]["rejected"]}
    assert "stesso emittente del candidato" in rej["QQISIN0001"]
    assert "QQISIN0001" not in [c[0] for c in f.calls]          # niente download del doppione
    assert [m["ticker"] for m in pack["multiples"]["current"]] == ["ZZCAND.MI", "QQPEER1.PA"]


def test_peer_senza_nome_controllo_emittente_dichiarato_non_eseguito():
    info = dict(INFO, **{"QQPEER1.PA": {k: v for k, v in INFO["QQPEER1.PA"].items() if k != "longName"}})
    pack, _, _ = _build(info=info)
    assert [p["ticker"] for p in pack["peers"]] == ["QQPEER1.PA", "QQPEER2.L"]
    assert any(i["symbol"] == "QQPEER1.PA" and "non eseguito" in i["reason"] for i in pack["issues"])
