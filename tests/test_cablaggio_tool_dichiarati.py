"""W1 (04/10/2026, Opus 5.5): cablaggio nei tool di chat/desk delle cure del 2° giro.

- calendario eventi per i .MI da Borsa Italiana (IT1), stato dichiarato;
- Monte Carlo: new_alloc nello schema e nel dispatch, descrizione con lo stress fisso (C7);
- VaR backtest: descrizione e prompt quant con NON AFFIDABILE / due assi / verdict_detail (B7);
- Reddit spenta: niente include_reddit, timbro senza «+ Reddit», fonti_spente nel payload (F7/B2);
- prompt dei desk: il proxy USA solo se confermato dal PM e chiesto con proxy_usa (C3).
Ticker e numeri INVENTATI; nessuna rete, nessun DB.
"""
import datetime as dt

import pytest

from bellomberg.agents import chat_tools as ct


def _tool(nome):
    return next(t for t in ct.TOOL_DEFINITIONS if t["name"] == nome)


# ---------------------------------------------------------------- calendario .MI

@pytest.fixture
def borsa(monkeypatch, tmp_path):
    from bellomberg.market_data import borsa_italiana, finnhub_news
    from bellomberg.storage import classificazione as cl
    monkeypatch.setattr(cl, "PERCORSO_VEICOLI", str(tmp_path / "veicoli_assente.json"))
    chiamate = {"borsa": [], "finnhub": 0}
    risposta = {}

    def eventi(ticker, **k):
        assert not k, "il tool non deve passare un isin esplicito: l'ISIN viene dal negozio"
        chiamate["borsa"].append(ticker)
        return dict(risposta)

    def finnhub(*a, **k):
        chiamate["finnhub"] += 1
        return []
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", eventi)
    monkeypatch.setattr(borsa_italiana, "oggi_roma", lambda: dt.date(2026, 10, 4))
    monkeypatch.setattr(finnhub_news, "fetch_earnings_for_portfolio", finnhub)
    monkeypatch.setattr(finnhub_news, "fetch_earnings_calendar", finnhub)
    return chiamate, risposta


def test_calendario_mi_viene_da_borsa_italiana_con_la_finestra(borsa):
    chiamate, risposta = borsa
    ev = [{"data": "2026-09-01", "tipo": "cda", "descrizione": "passato"},
          {"data": "2026-10-10", "tipo": "cda", "descrizione": "dentro"},
          {"data": "2026-12-01", "tipo": "assemblea", "descrizione": "fuori"},
          {"data": None, "tipo": "altro", "descrizione": "senza data"}]
    risposta.update(stato="ok", eventi=ev, prossimo=ev[1], letto_il="2026-10-04T08:00:00+00:00")
    r = ct.dispatch("get_earnings_calendar", {"ticker": "qqsyn.mi", "days_ahead": 14})
    d = r["data"]
    assert chiamate == {"borsa": ["QQSYN.MI"], "finnhub": 0}
    assert [e["descrizione"] for e in d["items"]] == ["dentro"]
    assert d["count"] == 1 and d["eventi_letti"] == 4 and d["prossimo"]["descrizione"] == "dentro"
    assert d["finestra"] == {"dal": "2026-10-04", "al": "2026-10-18"}
    assert "error" not in r and "Borsa Italiana" in r["_source"]


@pytest.mark.parametrize("stato,errore,motivo", [
    ("KO", "ticker_non_mappato", "ISIN mancante: QQSYN.MI non e' nel negozio"),
    ("KO", "negozio_assente", "ISIN mancante: negozio assente"),
    ("KO", "rete", "richiesta fallita: ConnectionError"),
    ("non_coperto", None, "emittente non coperto"),
])
def test_calendario_mi_ko_e_un_errore_non_nessun_evento(borsa, stato, errore, motivo):
    chiamate, risposta = borsa
    risposta.update(stato=stato, errore=errore, motivo=motivo, eventi=[], prossimo=None)
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQSYN.MI"})
    assert motivo in r["error"] and stato in r["error"], r
    assert chiamate["finnhub"] == 0


def test_calendario_mi_stale_e_dichiarato_con_la_data_vera(borsa):
    _, risposta = borsa
    risposta.update(stato="STALE", stato_originale="KO", eventi=[], prossimo=None,
                    letto_il="2026-09-20T08:00:00+00:00")
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQSYN.MI"})
    assert "error" not in r
    assert r["data"]["avviso"].startswith("STALE") and "2026-09-20" in r["data"]["avviso"]


def test_calendario_mi_tabella_isin_mancante_col_lettore_vero(monkeypatch, tmp_path):
    """Lettore VERO: negozio ticker->ISIN assente = errore dichiarato, nessuna richiesta."""
    import requests
    from bellomberg.market_data import borsa_italiana
    from bellomberg.storage import classificazione as cl
    monkeypatch.setattr(cl, "PERCORSO_VEICOLI", str(tmp_path / "veicoli_assente.json"))
    monkeypatch.setattr(borsa_italiana, "PERCORSO_ISIN", str(tmp_path / "isin_assente.json"))
    monkeypatch.setattr(borsa_italiana, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_auto_assente.json"))
    monkeypatch.setattr(borsa_italiana, "CACHE_DIR", str(tmp_path / "cache"))
    rete = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: rete.append(a) or None)
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQSYN.MI"})
    assert "KO" in r["error"] and "ISIN" in r["error"], r
    assert rete == []


def test_calendario_usa_resta_su_finnhub(borsa):
    chiamate, _ = borsa
    ct.dispatch("get_earnings_calendar", {"ticker": "ZZTEST"})
    assert chiamate == {"borsa": [], "finnhub": 1}


# ---------------------------------------------------------------- Monte Carlo (C7)

def test_montecarlo_schema_e_descrizione_dicono_lo_stress_fisso():
    t = _tool("get_portfolio_montecarlo")
    assert t["input_schema"]["properties"]["new_alloc"]["type"] == "number"
    d = t["description"]
    for frase in ("NON e' la size proposta", "what_if_allocation", "added_weight_pct_effective",
                  "calibration_sample", "tail_reliability_warning", "stress_fisso"):
        assert frase in d, frase


def test_montecarlo_dispatch_passa_new_alloc(monkeypatch):
    from bellomberg.portfolio import portfolio_montecarlo
    visti = {}

    def finto(**k):
        visti.update(k)
        return {"what_if_allocation": {"origin": "parametro_chiamante"}}
    monkeypatch.setattr(portfolio_montecarlo, "run_monte_carlo", finto)
    r = ct.dispatch("get_portfolio_montecarlo", {"add_tickers": ["ZZTEST"], "new_alloc": 0.025})
    assert visti["new_alloc"] == 0.025 and visti["add_tickers"] == ["ZZTEST"]
    assert r["data"]["what_if_allocation"]["origin"] == "parametro_chiamante"
    ct.dispatch("get_portfolio_montecarlo", {"add_tickers": ["ZZTEST"]})
    assert visti["new_alloc"] is None   # omesso = stress fisso dichiarato dal modulo


# ---------------------------------------------------------------- VaR backtest (B7)

def test_var_backtest_descrizione_e_prompt_quant():
    d = _tool("get_var_backtest")["description"]
    for frase in ("NON AFFIDABILE", "verdict_detail", "coverage", "independence", "low_power",
                  "n.d.", "missing_weight_tested_window_pct", "partial_coverage_tested_window"):
        assert frase in d, frase
    assert "Verdetto PASS/FAIL per confidenza" not in d
    assert "PASS / FAIL / NON AFFIDABILE / n.d." in d and "NON e' un PASS" in d
    from bellomberg.agents.specialists.quant import QuantSpecialist
    sp = QuantSpecialist.system_prompt
    assert "verdict_detail" in sp and "NON AFFIDABILE" in sp
    assert "se e' n.d." in sp and "missing_weight_tested_window_pct" in sp
    assert "se il backtest e' FAIL, dillo nel memo" not in sp


# ---------------------------------------------------------------- Reddit spenta (F7/B2)

def test_macro_news_senza_reddit_e_fonti_spente_dichiarate(monkeypatch):
    from bellomberg.market_data import news_aggregator
    visti = {}

    def finto(**k):
        visti.update(k)
        return []
    monkeypatch.setattr(news_aggregator, "fetch_macro_news", finto)
    monkeypatch.setattr(news_aggregator, "providers_blocked", lambda: {})
    r = ct.dispatch("get_macro_news_by_topic", {})
    assert visti["include_reddit"] is False
    assert "Reddit" not in r["_source"]
    assert r["data"]["fonti_spente"]["reddit"].startswith("SPENTA")
    assert "fonti_mute" not in r["data"]   # una decisione non e' un guasto


# ---------------------------------------------------------------- proxy USA nei prompt (C3)

def test_prompt_options_non_fa_scegliere_il_proxy_al_modello():
    from bellomberg.agents.specialists.options import OptionsSpecialist
    sp = OptionsSpecialist.system_prompt
    assert "usa l'ADR americano o l'ETF settoriale come PROXY" not in sp
    assert "proxy_usa=true" in sp and "nessun proxy confermato dal PM" in sp
    assert "PROXY SOLO CONFERMATI DAL PM" in sp


def test_prompt_quant_proxy_fonti_solo_usa():
    from bellomberg.agents.specialists.quant import QuantSpecialist
    sp = QuantSpecialist.system_prompt
    assert "proxy_usa=true" in sp and "nessun proxy confermato dal PM" in sp


def test_proxy_usa_e_la_stessa_proprieta_nei_due_registri():
    from bellomberg.agents import agent_tools as at
    assert at.PROXY_USA_PROP is ct.PROXY_USA_PROP
    con = {t["name"] for t in ct.TOOL_DEFINITIONS if "proxy_usa" in t["input_schema"]["properties"]}
    assert con == {"get_options_data", "get_option_expirations_polygon", "get_options_chain_polygon",
                   "get_congress_trades", "get_lobbying", "get_gov_contracts", "get_insider_trades"}
    assert ct.PROXY_USA_PROP["default"] is False
