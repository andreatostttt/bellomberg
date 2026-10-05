"""W1 (04/10/2026, Opus 5.5): guardia di copertura e gemello USA CABLATI nei wrapper dei tool.

C1 ha scritto `copertura.copertura_usa/copertura_opzioni`, IT1 i lettori italiani (eMarket
SDIR, Borsa Italiana), C3 il negozio dei gemelli USA. Qui si prova il CABLAGGIO: che i tool
veri che il comitato e la chat chiamano (agent_tools.tool_*, chat_tools.dispatch) chiedano
la copertura PRIMA di ogni fornitore. La misura sono CONTATORI su ogni fornitore USA
(Polygon, IBKR, yfinance, Finnhub, SEC, Quiver) e sul trasporto `requests.get`: su un `.MI`
inventato devono restare a zero. Ticker e numeri INVENTATI; nessuna rete, nessun DB.
"""
import collections
import json
import shutil
import types

import pytest

from bellomberg.agents import agent_tools as at
from bellomberg.agents import chat_tools as ct
from bellomberg.storage import negozi_privati as np_

FORNITORI_USA = ("polygon_summary", "polygon_expirations", "polygon_chain", "ibkr",
                 "yfinance", "finnhub_insider", "sec_insider", "quiver_congress",
                 "quiver_lobbying", "quiver_gov", "requests.get")


def _book_finto(monkeypatch, tmp_path):
    """DB sqlite in tmp_path con posizioni INVENTATE: ZZEUR (EUR, senza suffisso), ZZUSD
    (USD), ZZCHIUSA (chiusa). `memory_db.SQLITE_PATH` puntato li'."""
    import sqlite3
    from bellomberg.storage import memory_db
    db = tmp_path / "book_finto.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")   # come il DB vero: connect_sqlite non lo riconverte
    con.execute("CREATE TABLE positions (ticker TEXT, valuta TEXT, is_active INTEGER, nome TEXT)")
    con.executemany("INSERT INTO positions VALUES (?,?,?,?)",
                    [("ZZEUR", "EUR", 1, None), ("ZZUSD", "USD", 1, None), ("ZZCHIUSA", "EUR", 0, None),
                     ("QQNOMEDB.MI", "EUR", 1, "Societa Sintetica Spa")])
    con.commit()
    con.close()
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(db))
    # la fixture autouse di conftest sostituisce valuta_dal_book col finto «book non letto»:
    # qui si rimette la funzione VERA, puntata sul DB finto in tmp_path
    from bellomberg.market_data import copertura
    monkeypatch.setattr(copertura, "valuta_dal_book", copertura.valuta_dal_book_vera)
    return db


@pytest.fixture
def rete(monkeypatch, tmp_path):
    """Ogni fornitore sostituito da una spia che CONTA e registra il simbolo ricevuto."""
    conta = collections.Counter()
    simboli = collections.defaultdict(list)
    risposte = {"polygon_summary": lambda t: {"data_source": "polygon finto", "ticker": t},
                "emarket": lambda t: {"ticker": t, "stato": "ok", "comunicazioni": [],
                                      "letto_il": "2026-10-01T10:00:00+00:00"}}

    def spia(nome, valore):
        def f(*a, **k):
            conta[nome] += 1
            t = a[0] if a else (k.get("ticker") or k.get("underlying"))
            simboli[nome].append(t)
            if nome in risposte:
                return risposte[nome](t)
            return valore(t) if callable(valore) else valore
        return f

    import requests
    monkeypatch.setattr(requests, "get", spia("requests.get", None))
    from bellomberg.market_data import (emarket_sdir, finnhub_news, polygon_data, quiver_data,
                                        sec_edgar)
    monkeypatch.setattr(polygon_data, "polygon_available", lambda: True)
    monkeypatch.setattr(polygon_data, "get_options_summary_polygon", spia("polygon_summary", None))
    monkeypatch.setattr(polygon_data, "get_option_expirations",
                        spia("polygon_expirations", lambda t: {"ticker": t, "expirations": ["2026-12-18"]}))
    monkeypatch.setattr(polygon_data, "get_options_chain",
                        spia("polygon_chain", lambda t: {"ticker": t, "contracts": []}))
    monkeypatch.setattr(at, "_get_ibkr_options", spia("ibkr", None))
    monkeypatch.setattr(at, "YFINANCE_AVAILABLE", True)
    info_finte = {}

    class TickerFinto:
        """`.options` = fornitore di OPZIONI (contato come USA); `.get_info()` = nome
        dell'emittente (contato a parte: non e' una chiamata di dato USA)."""
        def __init__(self, t):
            self.t = t

        @property
        def options(self):
            conta["yfinance"] += 1
            simboli["yfinance"].append(self.t)
            return []

        def get_info(self):
            conta["yfinance_info"] += 1
            simboli["yfinance_info"].append(self.t)
            return info_finte.get(self.t, {})
    monkeypatch.setattr(at, "yf", types.SimpleNamespace(Ticker=TickerFinto))
    monkeypatch.setattr(finnhub_news, "fetch_insider_trades", spia("finnhub_insider", []))
    monkeypatch.setattr(sec_edgar, "get_insider_trades", spia("sec_insider", []))
    monkeypatch.setattr(quiver_data, "quiver_available", lambda: True)
    monkeypatch.setattr(quiver_data, "get_congress_trades",
                        spia("quiver_congress", lambda t: {"ticker": t, "n": 1, "trades": [{"x": 1}]}))
    monkeypatch.setattr(quiver_data, "get_lobbying",
                        spia("quiver_lobbying", lambda t: {"ticker": t, "n": 1}))
    monkeypatch.setattr(quiver_data, "get_gov_contracts",
                        spia("quiver_gov", lambda t: {"ticker": t, "n": 1}))
    # handoff-3: l'internal dealing passa dall'instradatore sdir.py (eMarket / 1INFO): la spia
    # "emarket" sta li' (nome storico del contatore)
    from bellomberg.market_data import sdir as _sdir
    originali = {"emarket": _sdir.get_internal_dealing}
    monkeypatch.setattr(_sdir, "get_internal_dealing", spia("emarket", None))
    # negozio dei gemelli: assente di default (le prove che lo vogliono lo scrivono)
    monkeypatch.setattr(np_, "PERCORSO_GEMELLI", str(tmp_path / "gemelli_assente.json"))
    # book finto per la valuta delle posizioni (valuta_dal_book): mai il DB vero
    _book_finto(monkeypatch, tmp_path)
    # negozio dei veicoli (natura ETF/societa'): mai quello vero in data/
    from bellomberg.storage import classificazione as _cl
    monkeypatch.setattr(_cl, "PERCORSO_VEICOLI", str(tmp_path / "veicoli_assente.json"))
    # negozi ISIN in tmp_path (assenti) e risoluzione ISIN: spia con contatore, mai rete
    from bellomberg.market_data import borsa_italiana as _bi
    monkeypatch.setattr(_bi, "PERCORSO_ISIN", str(tmp_path / "isin_assente.json"))
    monkeypatch.setattr(_bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_auto_assente.json"))
    monkeypatch.setattr(_bi, "CACHE_DIR", str(tmp_path / "cache_it"))
    risposte["risolvi"] = lambda t, nome: {"ticker": t, "stato": "non_trovato", "errore": "nome_non_nel_listino",
                                           "motivo": "finto: %r non nel listino" % nome, "isin": None,
                                           "salvato": False, "negozio": None}

    def risolvi_spia(ticker, *, nome=None):
        conta["risolvi"] += 1
        simboli["risolvi"].append((ticker, nome))
        return risposte["risolvi"](ticker, nome)
    monkeypatch.setattr(_bi, "risolvi_isin", risolvi_spia)
    # orchestratore VERO (isin_automatico): i finti autouse di conftest si tolgono, cosi' si
    # prova anche il cablaggio _risolvi -> borsa_italiana.risolvi_isin (qui la spia) e il nome
    # dal fornitore prezzi passa dal yfinance FINTO sopra (get_info contato a parte)
    from bellomberg.market_data import isin_automatico as _ia
    monkeypatch.setattr(_ia, "_risolvi", _ia._risolvi_vero)
    monkeypatch.setattr(_ia, "_nome_dal_fornitore", _ia._nome_dal_fornitore_vero)
    monkeypatch.setattr(_ia, "_yf_ticker", TickerFinto)
    return types.SimpleNamespace(conta=conta, simboli=simboli, risposte=risposte, info=info_finte,
                                 tmp=tmp_path, mp=monkeypatch, originali=originali)


def _usa_zero(conta):
    return {k: conta[k] for k in FORNITORI_USA if conta[k]}


def _negozio_esempio(rete):
    """Il negozio d'esempio TRACCIATO (voci inventate: ZZIDX.MI confermato stesso_indice ->
    QQSYN per opzioni/notizie; ZZBETA.DE proposto; ZZGAMMA.L confermato a paniere;
    ZZDELTA.MI rifiutato), copiato in tmp_path."""
    import importlib.resources as ir
    src = ir.files("bellomberg.resources.examples") / np_.ESEMPIO_GEMELLI
    dest = rete.tmp / "gemelli_usa.json"
    with ir.as_file(src) as f:
        shutil.copy(str(f), str(dest))
    rete.mp.setattr(np_, "PERCORSO_GEMELLI", str(dest))
    return dest


# ---------------------------------------------------------------- .MI inventato: zero chiamate

TOOL_SU_TICKER = [
    ("agent options", lambda t: at.tool_get_options_data(t)),
    ("agent congress", lambda t: at.tool_get_congress_trades(t)),
    ("agent lobbying", lambda t: at.tool_get_lobbying(t)),
    ("agent gov", lambda t: at.tool_get_gov_contracts(t)),
    ("agent insider", lambda t: at.tool_get_insider_trades(t)),
    ("chat options", lambda t: ct.dispatch("get_options_data", {"ticker": t})),
    ("chat expirations", lambda t: ct.dispatch("get_option_expirations_polygon", {"ticker": t})),
    ("chat chain", lambda t: ct.dispatch("get_options_chain_polygon", {"ticker": t})),
    ("chat congress", lambda t: ct.dispatch("get_congress_trades", {"ticker": t})),
    ("chat lobbying", lambda t: ct.dispatch("get_lobbying", {"ticker": t})),
    ("chat gov", lambda t: ct.dispatch("get_gov_contracts", {"ticker": t})),
    ("chat insider", lambda t: ct.dispatch("get_insider_trades", {"ticker": t})),
]


@pytest.mark.parametrize("nome,chiama", TOOL_SU_TICKER, ids=[n for n, _ in TOOL_SU_TICKER])
def test_mi_inventato_nessuna_chiamata_ai_fornitori_usa(rete, nome, chiama):
    out = chiama("QQSYN.MI")
    assert _usa_zero(rete.conta) == {}, (nome, dict(rete.conta), out)
    assert isinstance(out, dict)


@pytest.mark.parametrize("nome,chiama", [x for x in TOOL_SU_TICKER if "insider" not in x[0]],
                         ids=[n for n, _ in TOOL_SU_TICKER if "insider" not in n])
def test_estero_e_non_coperto_dichiarato_non_uno_zero(rete, nome, chiama):
    out = chiama("QQSYN.DE")
    assert _usa_zero(rete.conta) == {}
    err = str(out.get("error", ""))
    assert err.startswith("non coperto") or "non coperto" in err, (nome, out)
    dati = out.get("data", out)
    assert dati.get("copertura") == "non_coperto"
    assert dati.get("gemello_usa", {}).get("stato") == "negozio_assente", dati


def test_opzioni_mi_dicono_idem_e_nessun_tentativo_ibkr(rete):
    out = at.tool_get_options_data("QQSYN.MI")
    assert "IDEM" in out["error"] and "IBKR" in out["error"], out
    assert rete.conta["ibkr"] == 0 and rete.conta["polygon_summary"] == 0


def test_quiver_estero_non_coperto_anche_senza_chiave(rete):
    """Il «non coperto» e' vero anche senza QUIVER_API_KEY: la guardia sta PRIMA della chiave."""
    from bellomberg.market_data import quiver_data
    rete.mp.setattr(quiver_data, "quiver_available", lambda: False)
    out = at.tool_get_lobbying("QQSYN.MI")
    assert "non coperto" in out["error"] and "QUIVER_API_KEY" not in out["error"], out


def test_crypto_non_coperta_e_nessun_gemello(rete):
    out = at.tool_get_options_data("ZZC-USD")
    assert "non coperto" in out["error"]
    assert out["gemello_usa"]["stato"] == "non_applicabile"
    assert _usa_zero(rete.conta) == {}


# ---------------------------------------------------------------- insider: .MI -> SDIR (eMarket / 1INFO)

def test_insider_mi_va_su_emarket_e_mai_su_finnhub_sec(rete):
    rete.risposte["emarket"] = lambda t: {
        "ticker": t, "stato": "ok", "comunicazioni": [{"data": "2026-09-30", "soggetto": "Zeno Fittizio",
                                                       "operazioni": [{"prezzo": 1.23}]}],
        "letto_il": "2026-10-01T10:00:00+00:00", "isin": "ITZZ00000000", "sdir": "1INFO-SDIR",
        "instradamento": {"regola": "finta", "scelta": "oneinfo"}, "oneinfo_ndg": 99}
    for out in (at.tool_get_insider_trades("QQSYN.MI", days=60),
                ct.dispatch("get_insider_trades", {"ticker": "QQSYN.MI", "days": 60})["data"]):
        assert out["source"] == "sdir (internal dealing): 1INFO-SDIR", out
        assert out["sdir"] == "1INFO-SDIR" and out["instradamento"]["scelta"] == "oneinfo"
        assert out["oneinfo_ndg"] == 99
        assert out["count"] == 1 and out["comunicazioni"][0]["soggetto"] == "Zeno Fittizio"
        assert "error" not in out
        assert "NON interrogate" in out["fonti_usa"]
    assert rete.simboli["emarket"] == ["QQSYN.MI", "QQSYN.MI"]
    assert rete.conta["finnhub_insider"] == rete.conta["sec_insider"] == 0


@pytest.mark.parametrize("stato,errore_atteso", [("KO", True), ("non_coperto", True),
                                                 ("vuoto_misurato", False), ("STALE", False)])
def test_insider_mi_dichiara_gli_stati_della_fonte(rete, stato, errore_atteso):
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": stato, "errore": "rete",
                                          "motivo": "richiesta fallita: ConnectionError",
                                          "comunicazioni": [], "letto_il": "2026-09-01T08:00:00+00:00",
                                          "stato_originale": "KO" if stato == "STALE" else None}
    out = at.tool_get_insider_trades("QQSYN.MI")
    assert out["stato"] == stato
    assert ("error" in out) is errore_atteso, out
    if errore_atteso:
        assert stato in out["error"] and "ConnectionError" in out["error"]
    if stato == "STALE":
        assert out["avviso"].startswith("STALE") and "2026-09-01" in out["avviso"]
    assert rete.conta["finnhub_insider"] == rete.conta["sec_insider"] == 0


def test_insider_mi_tabella_isin_mancante_e_errore_dichiarato_col_lettore_vero(rete, monkeypatch):
    """Senza stub su eMarket: il lettore VERO, col negozio ticker->ISIN assente, dice KO."""
    from bellomberg.market_data import borsa_italiana, sdir
    monkeypatch.setattr(sdir, "get_internal_dealing", rete.originali["emarket"])  # instradatore vero
    monkeypatch.setattr(borsa_italiana, "PERCORSO_ISIN", str(rete.tmp / "isin_assente.json"))
    monkeypatch.setattr(borsa_italiana, "PERCORSO_ISIN_AUTO", str(rete.tmp / "isin_auto_assente.json"))
    monkeypatch.setattr(borsa_italiana, "CACHE_DIR", str(rete.tmp / "cache_it"))
    out = at.tool_get_insider_trades("QQSYN.MI")
    assert out["stato"] == "KO" and "error" in out, out
    assert _usa_zero(rete.conta) == {}


def test_insider_estero_non_mi_non_va_su_emarket(rete):
    out = at.tool_get_insider_trades("QQSYN.DE")
    assert "non coperto" in out["error"]
    assert rete.conta["emarket"] == 0 and _usa_zero(rete.conta) == {}


# ---------------------------------------------------------------- USA: si interroga, mai muto

def test_ticker_usa_passa_ai_fornitori(rete):
    out = at.tool_get_options_data("ZZTEST")
    assert out["data_source"] == "polygon finto" and rete.simboli["polygon_summary"] == ["ZZTEST"]
    at.tool_get_insider_trades("ZZTEST")
    assert rete.simboli["finnhub_insider"] == ["ZZTEST"] and rete.simboli["sec_insider"] == ["ZZTEST"]
    assert at.tool_get_lobbying("ZZTEST")["ticker"] == "ZZTEST"


def test_errore_polygon_non_e_piu_scartato_in_silenzio(rete):
    rete.risposte["polygon_summary"] = lambda t: {"error": "HTTP 503 finto"}
    out = at.tool_get_options_data("ZZTEST")
    assert rete.conta["ibkr"] == 1   # su un ticker USA il ripiego IBKR resta
    assert "HTTP 503 finto" in out.get("polygon_non_usato", ""), out


def test_insider_usa_sec_muta_e_un_errore_non_zero_insider(rete):
    from bellomberg.market_data import finnhub_news, sec_edgar

    def finnhub_muto(t, days=30, motivo=None):
        motivo.append("HTTP 401 finto")
        return []

    def sec_muta(t, days=30, motivo=None, **k):
        motivo.append("CIK non risolto finto")
        return []
    rete.mp.setattr(finnhub_news, "fetch_insider_trades", finnhub_muto)
    rete.mp.setattr(sec_edgar, "get_insider_trades", sec_muta)
    out = at.tool_get_insider_trades("ZZTEST")
    assert out["fonti_mute"] == {"finnhub": "HTTP 401 finto", "sec_edgar": "CIK non risolto finto"}
    assert "CIK non risolto finto" in out["error"] and out["count"] == 0


def test_copertura_indeterminata_passa_col_simbolo_intatto_e_lo_dichiara(rete):
    out = at.tool_get_options_data("ZZT.B")
    assert rete.simboli["polygon_summary"] == ["ZZT.B"]
    assert out["copertura"]["copertura"] == "indeterminato", out


# ---------------------------------------------------------------- gemelli USA (opt-in)

def test_senza_proxy_usa_il_gemello_confermato_e_solo_segnalato(rete):
    _negozio_esempio(rete)
    out = at.tool_get_options_data("ZZIDX.MI")
    assert "non coperto" in out["error"]
    g = out["gemello_usa"]
    assert g["stato"] == "confermato" and g["simbolo"] == "QQSYN" and "proxy_usa=true" in g["motivo"]
    assert _usa_zero(rete.conta) == {}


def test_con_proxy_usa_si_interroga_il_gemello_con_l_etichetta_in_testa(rete):
    _negozio_esempio(rete)
    out = at.tool_get_options_data("ZZIDX.MI", proxy_usa=True)
    assert rete.simboli["polygon_summary"] == ["QQSYN"]
    assert list(out)[0] == "proxy", list(out)
    assert out["proxy"]["etichetta"].startswith("PROXY:") and out["proxy"]["ticker_book"] == "ZZIDX.MI"
    assert out["proxy"]["ticker_dati"] == "QQSYN"
    r = ct.dispatch("get_options_data", {"ticker": "ZZIDX.MI", "proxy_usa": True})
    assert "PROXY:" in r["_source"] and list(r["data"])[0] == "proxy"
    r2 = ct.dispatch("get_options_chain_polygon", {"ticker": "ZZIDX.MI", "proxy_usa": True})
    assert rete.simboli["polygon_chain"] == ["QQSYN"] and "PROXY:" in r2["_source"]
    assert list(r2["data"])[0] == "proxy"


def test_proxy_usa_su_voce_proposta_non_usa_nessun_proxy(rete):
    _negozio_esempio(rete)
    out = at.tool_get_options_data("ZZBETA.DE", proxy_usa=True)
    assert "proxy_usa=true senza gemello confermato" in out["error"], out
    assert out["gemello_usa"]["stato"] == "proposto"
    assert _usa_zero(rete.conta) == {}


def test_proxy_usa_per_un_uso_non_ammesso_resta_sulla_fonte_italiana(rete):
    """ZZIDX.MI e' confermato per opzioni/notizie, NON per insider: niente gemello, eMarket."""
    _negozio_esempio(rete)
    out = at.tool_get_insider_trades("ZZIDX.MI", proxy_usa=True)
    assert out["source"].startswith("sdir (internal dealing)")
    assert out["gemello_usa"]["stato"] == "uso_non_ammesso", out
    assert rete.conta["finnhub_insider"] == rete.conta["sec_insider"] == 0


def test_gemello_a_paniere_non_si_interroga_come_sottostante_unico(rete):
    _negozio_esempio(rete)
    out = at.tool_get_lobbying("ZZGAMMA.L", proxy_usa=True)
    assert "paniere" in out["error"], out
    assert rete.conta["quiver_lobbying"] == 0


def test_negozio_gemelli_illeggibile_e_dichiarato(rete):
    p = rete.tmp / "gemelli_rotto.json"
    p.write_text("{non e' json", encoding="utf-8")
    rete.mp.setattr(np_, "PERCORSO_GEMELLI", str(p))
    out = at.tool_get_options_data("QQSYN.MI", proxy_usa=True)
    assert out["gemello_usa"]["stato"] == "negozio_illeggibile"
    assert "nessun proxy usato" in out["error"]
    assert _usa_zero(rete.conta) == {}


# ---------------------------------------------------------------- valuta dalla posizione (W1 residui)

def test_ticker_senza_suffisso_in_eur_nel_book_non_va_ai_fornitori_usa(rete):
    """ZZEUR e' nel book in EUR: il simbolo nudo e' proprio l'omonimo USA da evitare."""
    for out in (at.tool_get_options_data("ZZEUR"), at.tool_get_insider_trades("ZZEUR"),
                at.tool_get_lobbying("ZZEUR"),
                ct.dispatch("get_earnings_calendar", {"ticker": "ZZEUR"})):
        err = str(out.get("error", ""))
        assert "copertura indeterminata" in err and "EUR" in err, out
        dati = out.get("data", out)
        assert "EUR" in str(dati.get("copertura_nota")), out
    assert _usa_zero(rete.conta) == {} and rete.conta["emarket"] == 0


def test_ticker_usa_nel_book_e_fuori_book_dichiarano_la_valuta(rete):
    out = at.tool_get_options_data("ZZUSD")
    assert out["data_source"] == "polygon finto" and "USD" in out["copertura_nota"]
    out = at.tool_get_options_data("ZZTEST")   # fuori book: presunzione DICHIARATA
    assert "presunto" in out["copertura_nota"] and "ZZTEST" in out["copertura_nota"], out
    out = at.tool_get_options_data("ZZCHIUSA")  # posizione chiusa = fuori book
    assert "presunto" in out["copertura_nota"]


def test_valuta_dal_book_db_assente_e_illeggibile_dichiarati(tmp_path):
    from bellomberg.market_data import copertura
    valuta_dal_book = copertura.valuta_dal_book_vera
    a = valuta_dal_book("ZZTEST", db_path=str(tmp_path / "manca.db"))
    assert a["origine"] == "db_assente" and a["valuta"] is None and "presunto" in a["nota"]
    assert not (tmp_path / "manca.db").exists()   # mai creato vuoto
    rotto = tmp_path / "rotto.db"
    rotto.write_bytes(b"non e' un database sqlite" * 10)
    b = valuta_dal_book("ZZTEST", db_path=str(rotto))
    assert b["origine"] == "illeggibile" and "presunto" in b["nota"]


def test_valuta_dal_book_e_in_sola_lettura(rete):
    """Il file del DB non cambia dopo la lettura (dimensione e mtime)."""
    import os
    from bellomberg.market_data.copertura import valuta_dal_book
    from bellomberg.storage import memory_db
    st = os.stat(memory_db.SQLITE_PATH)
    with pytest.raises(Exception):
        # query_only: la connessione della funzione non puo' scrivere (provato sulla via vera)
        c = memory_db.connect_sqlite(memory_db.SQLITE_PATH)
        c.execute("PRAGMA query_only=ON")
        c.execute("DELETE FROM positions")
    assert valuta_dal_book("zzeur ")["valuta"] == "EUR"
    st2 = os.stat(memory_db.SQLITE_PATH)
    assert (st.st_size, st.st_mtime_ns) == (st2.st_size, st2.st_mtime_ns)


# ---------------------------------------------------------------- calendario e scadenza guidance

def test_calendario_estero_non_mi_non_va_su_finnhub(rete, monkeypatch):
    from bellomberg.market_data import finnhub_news
    chiamate = []
    monkeypatch.setattr(finnhub_news, "fetch_earnings_for_portfolio",
                        lambda *a, **k: chiamate.append(a) or [])
    monkeypatch.setattr(finnhub_news, "fetch_earnings_calendar",
                        lambda *a, **k: chiamate.append(a) or [])
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQSYN.DE"})
    assert "non coperto" in r["error"] and chiamate == []
    assert r["data"]["gemello_usa"]["stato"] == "non_applicabile"


def test_scadenza_guidance_mi_dal_calendario_borsa(monkeypatch):
    import datetime as dt
    from bellomberg.market_data import borsa_italiana
    monkeypatch.setattr(borsa_italiana, "oggi_roma", lambda: dt.date(2026, 10, 4))
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", lambda t, **k: {
        "stato": "ok", "eventi": [{"data": "2026-10-04", "tipo": "risultati"},
                                  {"data": "2026-10-20", "tipo": "dividendo"},
                                  {"data": "2026-11-12", "tipo": "risultati"}]})
    assert ct._prossima_trimestrale("QQSYN.MI") == (
        "2026-11-12", "prossimi risultati dal calendario Borsa Italiana", None)
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", lambda t, **k: {
        "stato": "KO", "errore": "ticker_non_mappato", "motivo": "ISIN mancante: QQSYN.MI",
        "eventi": []})
    vu, fonte, nota = ct._prossima_trimestrale("QQSYN.MI")
    assert vu is None and fonte is None and "KO" in nota and "ISIN mancante" in nota


def test_scadenza_guidance_estero_non_interroga_finnhub(rete, monkeypatch):
    from bellomberg.market_data import finnhub_news
    monkeypatch.setattr(finnhub_news, "next_earnings_date",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Finnhub chiamato")))
    vu, fonte, nota = ct._prossima_trimestrale("QQSYN.DE")
    assert vu is None and "non coperto" in nota


def test_add_guidance_dichiara_perche_la_scadenza_e_il_fallback(monkeypatch):
    from bellomberg.storage import memory_db
    visti = {}

    class DBFinto:
        def add_guidance(self, **k):
            visti.update(k)
            return {"ok": True}
    monkeypatch.setattr(memory_db, "MemoryDB", DBFinto)
    monkeypatch.setattr(ct, "_prossima_trimestrale",
                        lambda t: (None, None, "calendario Borsa Italiana KO: ISIN mancante"))
    r = ct.dispatch("add_guidance", {"ticker": "QQSYN.MI", "metric": "revenue",
                                     "period": "FY2026", "value_mid": 1.0,
                                     "source_doc": "doc finto", "source_date": "2026-10-01"})
    assert visti["valid_until"] is None
    assert r["data"]["scadenza_nota"] == "calendario Borsa Italiana KO: ISIN mancante"


# ---------------------------------------------------------------- eventi societari .MI

def test_eventi_societari_mi_hanno_la_fonte_borsa_italiana(rete, monkeypatch):
    import datetime as dt
    from bellomberg.market_data import borsa_italiana, finnhub_news, sec_edgar
    monkeypatch.setattr(borsa_italiana, "oggi_roma", lambda: dt.date(2026, 10, 4))
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", lambda t, **k: {
        "stato": "ok", "url": "https://www.borsaitaliana.it/finto",
        "eventi": [{"data": "2026-10-20", "tipo": "risultati", "descrizione": "Cda risultati Q3"},
                   {"data": "2025-01-10", "tipo": "cda", "descrizione": "vecchio"}]})
    monkeypatch.setattr(finnhub_news, "fetch_company_news", lambda *a, **k: [])
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: "suffisso estero finto")
    d = ct.dispatch("get_corporate_events_for_ticker", {"ticker": "QQSYN.MI", "days": 30})["data"]
    assert d["fonti"]["borsa_italiana"].startswith("interrogata: 1 eventi"), d["fonti"]
    assert "natura del titolo non nota" in d["fonti"]["borsa_italiana"]   # negozio veicoli assente
    assert [e["title"] for e in d["events"] if e["provider"] == "Borsa Italiana"] == ["Cda risultati Q3"]


@pytest.mark.parametrize("stato,inizio", [("KO", "MUTA"), ("non_coperto", "NON COPRE")])
def test_eventi_societari_mi_borsa_ko_dichiarata(rete, monkeypatch, stato, inizio):
    from bellomberg.market_data import borsa_italiana, finnhub_news, sec_edgar
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", lambda t, **k: {
        "stato": stato, "motivo": "motivo finto %s" % stato, "eventi": []})
    monkeypatch.setattr(finnhub_news, "fetch_company_news", lambda *a, **k: [])
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: "suffisso estero finto")
    d = ct.dispatch("get_corporate_events_for_ticker", {"ticker": "QQSYN.MI"})["data"]
    assert d["fonti"]["borsa_italiana"].startswith(inizio), d["fonti"]
    assert "motivo finto" in d["fonti"]["borsa_italiana"]


def test_eventi_societari_usa_non_interrogano_borsa(rete, monkeypatch):
    from bellomberg.market_data import borsa_italiana, finnhub_news
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Borsa su un USA")))
    monkeypatch.setattr(finnhub_news, "fetch_company_news", lambda *a, **k: [])
    d = ct.dispatch("get_corporate_events_for_ticker", {"ticker": "ZZTEST"})["data"]
    assert "borsa_italiana" not in d["fonti"]


def test_nei_test_il_book_vero_non_si_apre_mai(monkeypatch):
    """Con la fixture autouse di conftest attiva, i wrapper NON aprono il DB del book: la
    nota e' quella del finto e connect_sqlite non viene chiamata."""
    from bellomberg.storage import memory_db
    aperture = []
    vera = memory_db.connect_sqlite
    monkeypatch.setattr(memory_db, "connect_sqlite", lambda *a, **k: aperture.append(a) or vera(*a, **k))
    g = at.guardia_fonte_usa("ZZTEST", "insider_form4", "insider", "s")
    assert g["nota_valuta"] == "test: book non letto"
    assert aperture == []


# ---------------------------------------------------------------- FRED: la chiave non esce

CHIAVE_FINTA = "QQSEGRETOFINTO123"


@pytest.fixture
def fred_403(monkeypatch):
    import requests
    from bellomberg.core import config
    monkeypatch.setattr(config, "FRED_API_KEY", CHIAVE_FINTA)
    monkeypatch.setattr(at, "_FRED_CACHE", {})

    class Risposta:
        status_code = 403

        def raise_for_status(self):
            raise requests.HTTPError(
                "403 Client Error: Forbidden for url: https://api.stlouisfed.org/fred/series/"
                "observations?series_id=ZZSERIE&api_key=%s&file_type=json" % CHIAVE_FINTA,
                response=self)
    monkeypatch.setattr(at._req, "get", lambda *a, **k: Risposta())


def test_fred_errore_ha_tipo_e_codice_mai_la_chiave(fred_403):
    r = at._fred_fetch_series("ZZSERIE", 4)
    assert CHIAVE_FINTA not in str(r)
    # integrazione 04/10: formato del ripulitore comune G3, «Tipo (HTTP codice): messaggio senza chiave»
    assert r["error"].startswith("FRED fetch ZZSERIE: HTTPError (HTTP 403)"), r


def test_fred_macro_rates_riusa_la_funzione_e_non_espone_la_chiave(fred_403):
    from bellomberg.market_data import macro_rates
    r = macro_rates.get_eu_hy_credit(last_n=4)
    assert CHIAVE_FINTA not in str(r) and "HTTP 403" in str(r)
    r2 = at.tool_get_macro_indicator("ZZSERIE", last_n=4)
    assert CHIAVE_FINTA not in str(r2)


def test_fred_eccezione_di_rete_solo_il_tipo(monkeypatch):
    import requests
    from bellomberg.core import config
    monkeypatch.setattr(config, "FRED_API_KEY", CHIAVE_FINTA)
    monkeypatch.setattr(at, "_FRED_CACHE", {})

    def esplode(*a, **k):
        raise requests.ConnectionError("Max retries exceeded with url: /fred?api_key=" + CHIAVE_FINTA)
    monkeypatch.setattr(at._req, "get", esplode)
    r = at._fred_fetch_series("ZZSERIE", 4)
    # integrazione 04/10: ripulitore comune G3 (tipo + messaggio senza chiave), non solo il tipo
    assert r["error"].startswith("FRED fetch ZZSERIE: ConnectionError") and CHIAVE_FINTA not in str(r)


@pytest.mark.parametrize("testo", [
    "x?api_key=%s&y=1" % CHIAVE_FINTA, "x?apikey=%s" % CHIAVE_FINTA,
    "x?token=%s " % CHIAVE_FINTA, "x?API_TOKEN=%s'" % CHIAVE_FINTA, "x&key=%s" % CHIAVE_FINTA])
def test_mascheramento_chiavi_in_qualunque_testo(testo):
    out = at._senza_chiavi(testo)
    assert CHIAVE_FINTA not in out and "***" in out


def test_native_cached_maschera_la_chiave():
    def esplode():
        raise RuntimeError("fallito su https://x.example/serie?api_key=" + CHIAVE_FINTA)
    r = at._native_cached("zz_nativa_finta_%d" % id(esplode), esplode)
    assert CHIAVE_FINTA not in r["error"] and "***" in r["error"]


# ---------------------------------------------------------------- regole IT2 (main 04/10)

@pytest.fixture
def veicoli_finti(monkeypatch, tmp_path):
    """Negozio dei veicoli in tmp_path: QQETF.MI etf, QQETC.MI commodity, QQSPA.MI operating."""
    from bellomberg.storage import classificazione as cl
    p = tmp_path / "veicoli.json"
    p.write_text(json.dumps({
        "QQETF.MI": {"tipo": "etf", "provenienza": "dichiarato"},
        "QQETC.MI": {"tipo": "commodity", "provenienza": "dichiarato"},
        "QQSPA.MI": {"tipo": "operating", "provenienza": "dichiarato"}}), encoding="utf-8")
    monkeypatch.setattr(cl, "PERCORSO_VEICOLI", str(p))
    from bellomberg.market_data import borsa_italiana
    chiamate = []
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", lambda t, **k: chiamate.append(t) or {
        "stato": "ok", "eventi": [], "prossimo": None})
    return chiamate


@pytest.mark.parametrize("ticker,tipo", [("QQETF.MI", "ETF"), ("QQETC.MI", "COMMODITY")])
def test_etf_mi_nessuna_chiamata_a_borsa_calendario(veicoli_finti, ticker, tipo):
    r = ct.dispatch("get_earnings_calendar", {"ticker": ticker})
    assert r["error"] == "non coperto: strumento non societario (%s), nessun calendario emittente" % tipo
    assert veicoli_finti == []


def test_etf_mi_eventi_societari_e_scadenza_non_interrogano_borsa(veicoli_finti, monkeypatch):
    from bellomberg.market_data import finnhub_news, sec_edgar
    monkeypatch.setattr(finnhub_news, "fetch_company_news", lambda *a, **k: [])
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: "suffisso estero finto")
    d = ct.dispatch("get_corporate_events_for_ticker", {"ticker": "QQETF.MI"})["data"]
    assert d["fonti"]["borsa_italiana"] == ("NON INTERROGATA (apposta): non coperto: strumento "
                                            "non societario (ETF), nessun calendario emittente")
    vu, fonte, nota = ct._prossima_trimestrale("QQETF.MI")
    assert vu is None and "strumento non societario (ETF)" in nota
    assert veicoli_finti == []


def test_societa_mi_interroga_borsa_e_natura_ignota_e_dichiarata(veicoli_finti):
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQSPA.MI"})
    assert "error" not in r and "natura_nota" not in r["data"]
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQIGNOTO.MI"})
    assert "natura del titolo non nota" in r["data"]["natura_nota"]
    assert veicoli_finti == ["QQSPA.MI", "QQIGNOTO.MI"]


def test_internal_dealing_vuoto_misurato_dice_la_categoria_non_gli_insider(rete):
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": "vuoto_misurato", "comunicazioni": [],
                                          "letto_il": "2026-10-01T10:00:00+00:00", "sdir": "1INFO-SDIR"}
    out = at.tool_get_insider_trades("QQSYN.MI", days=45)
    # handoff-3: la categoria e' quella dello SDIR scelto (qui 1INFO), non sempre eMarket
    assert out["esito"].startswith("nessuna comunicazione nella categoria internal dealing di 1INFO-SDIR")
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": "vuoto_misurato", "comunicazioni": []}
    assert "SDIR non dichiarato" in at.tool_get_insider_trades("QQSYN.MI", days=45)["esito"]
    out = at.tool_get_insider_trades("QQSYN.MI", days=45)
    assert "45 giorni" in out["esito"]
    testo = json.dumps(out, ensure_ascii=False).lower()
    assert "nessuna operazione degli insider" not in testo
    descr = next(t for t in ct.TOOL_DEFINITIONS if t["name"] == "get_insider_trades")["description"]
    assert "nessuna operazione degli insider" not in descr.lower()


def test_descrizioni_dei_tool_mi_dicono_il_vero():
    """handoff-3: insider .MI = SDIR scelto dall'instradatore (eMarket o 1INFO), ISIN risolto in
    automatico e dichiarato; calendario .MI = niente piu' «tabella mancante = errore» secco."""
    for schema in (ct.TOOL_DEFINITIONS, at.TOOLS_SCHEMA):
        d = next(t for t in schema if t["name"] == "get_insider_trades")["description"]
        assert "1INFO-SDIR" in d and "eMarket SDIR" in d and "risoluzione_isin" in d, d
        assert "instradamento" in d
    cal = next(t for t in ct.TOOL_DEFINITIONS if t["name"] == "get_earnings_calendar")["description"]
    assert "risoluzione_isin" in cal and "tabella ticker->ISIN mancante" not in cal


# ---------------------------------------------------------------- IT1: ISIN dal negozio automatico

def test_isin_automatico_e_detto_all_agente(rete, monkeypatch):
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": "ok", "comunicazioni": [],
                                          "voce_da": "automatico", "letto_il": "2026-10-01T10:00:00+00:00"}
    out = at.tool_get_insider_trades("QQSYN.MI")
    assert out["voce_da"] == "automatico" and "AUTOMATICAMENTE" in out["voce_da_nota"]
    import datetime as dt
    from bellomberg.market_data import borsa_italiana, finnhub_news, sec_edgar
    monkeypatch.setattr(borsa_italiana, "oggi_roma", lambda: dt.date(2026, 10, 4))
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", lambda t, **k: {
        "stato": "ok", "eventi": [], "prossimo": None, "voce_da": "automatico"})
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQSYN.MI"})
    assert "AUTOMATICAMENTE" in r["data"]["voce_da_nota"]
    monkeypatch.setattr(finnhub_news, "fetch_company_news", lambda *a, **k: [])
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: "suffisso estero finto")
    d = ct.dispatch("get_corporate_events_for_ticker", {"ticker": "QQSYN.MI"})["data"]
    assert "AUTOMATICAMENTE" in d["fonti"]["borsa_italiana"]
    # confermato: nessuna nota
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": "ok", "comunicazioni": [],
                                          "voce_da": "confermato"}
    assert "voce_da_nota" not in at.tool_get_insider_trades("QQSYN.MI")


# ---------------------------------------------------------------- ISIN dei .MI risolto in automatico (PM 05/10)

def _voce_auto_dopo_ok(rete, isin="ITZZ00000001"):
    """La risoluzione 'ok' finta scrive la voce nel negozio automatico finto, come la vera."""
    from bellomberg.market_data import borsa_italiana as _bi
    salvate = {}

    def ok(t, nome):
        salvate[t] = isin
        return {"ticker": t, "stato": "ok", "errore": None, "motivo": "verificato sulla scheda (finto)",
                "isin": isin, "salvato": True, "negozio": "automatico"}
    rete.risposte["risolvi"] = ok
    vera = _bi.voce_ticker_o_auto

    def voce(t):
        if t in salvate:
            return {"isin": salvate[t], "emarket": None, "negozio": "automatico"}, None, None
        return vera(t)
    rete.mp.setattr(_bi, "voce_ticker_o_auto", voce)
    return salvate


def test_mi_senza_voce_usa_il_nome_della_posizione_nel_db(rete):
    out = at.tool_get_insider_trades("QQNOMEDB.MI")
    assert rete.simboli["risolvi"] == [("QQNOMEDB.MI", "Societa Sintetica Spa")]
    r = out["risoluzione_isin"]
    assert r["fonte_nome"] == "nome della posizione nel book (DB)" and r["nome_usato"] == "Societa Sintetica Spa"
    assert rete.conta["yfinance_info"] == 0   # il DB basta: il fornitore prezzi non si chiede


def test_mi_senza_voce_ne_db_usa_il_nome_del_fornitore_prezzi(rete):
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": "KO", "errore": "ticker_non_mappato",
                                          "motivo": "ISIN mancante (finto)", "comunicazioni": []}
    rete.info["QQSYN.MI"] = {"longName": "Sintetica Industrie S.p.A."}
    out = at.tool_get_insider_trades("QQSYN.MI")
    assert rete.simboli["risolvi"] == [("QQSYN.MI", "Sintetica Industrie S.p.A.")]
    assert out["risoluzione_isin"]["fonte_nome"] == "nome dal fornitore prezzi (yfinance longName)"
    # risoluzione fallita: dichiarata nell'errore, non un «nessun dato»
    assert "risoluzione ISIN non_trovato" in out["error"] and "non nel listino" in out["error"]
    assert rete.conta["yfinance"] == 0   # nessuna chiamata di OPZIONI


def test_mi_nome_assente_non_trovato_dichiarato_senza_rete(rete):
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": "KO", "errore": "ticker_non_mappato",
                                          "motivo": "ISIN mancante (finto)", "comunicazioni": []}
    out = at.tool_get_insider_trades("QQSYN.MI")
    r = out["risoluzione_isin"]
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_assente"
    assert "nome dell'emittente non disponibile" in r["motivo"] and r["nome_usato"] is None
    assert rete.conta["risolvi"] == 0
    assert "risoluzione ISIN non_trovato" in out["error"]


def test_mi_risolto_procede_e_dichiara_voce_automatica(rete):
    salvate = _voce_auto_dopo_ok(rete)
    rete.risposte["emarket"] = lambda t: {"ticker": t, "stato": "ok", "comunicazioni": [],
                                          "voce_da": "automatico"}
    out = at.tool_get_insider_trades("QQNOMEDB.MI")
    assert salvate == {"QQNOMEDB.MI": "ITZZ00000001"}
    assert out["risoluzione_isin"]["stato"] == "ok" and "error" not in out
    assert out["voce_da"] == "automatico" and "AUTOMATICAMENTE" in out["voce_da_nota"]
    # secondo uso: la voce e' nel negozio automatico -> nessuna nuova risoluzione (niente rete)
    out2 = at.tool_get_insider_trades("QQNOMEDB.MI")
    assert rete.conta["risolvi"] == 1 and "risoluzione_isin" not in out2


def test_mi_risoluzione_cablata_su_calendario_eventi_e_scadenza(rete, monkeypatch):
    import datetime as dt
    from bellomberg.market_data import borsa_italiana, finnhub_news, sec_edgar
    monkeypatch.setattr(borsa_italiana, "oggi_roma", lambda: dt.date(2026, 10, 4))
    monkeypatch.setattr(borsa_italiana, "get_eventi_societari", lambda t, **k: {
        "stato": "KO", "errore": "ticker_non_mappato", "motivo": "ISIN mancante", "eventi": []})
    monkeypatch.setattr(finnhub_news, "fetch_company_news", lambda *a, **k: [])
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: "suffisso estero finto")
    from bellomberg.market_data import isin_automatico as _ia
    import os

    def dimentica():   # ogni percorso si prova da solo: memoria dei tentativi vuota
        if os.path.exists(_ia.PERCORSO_TENTATIVI):
            os.remove(_ia.PERCORSO_TENTATIVI)
    r = ct.dispatch("get_earnings_calendar", {"ticker": "QQNOMEDB.MI"})
    assert r["data"]["risoluzione_isin"]["nome_usato"] == "Societa Sintetica Spa"
    assert "risoluzione ISIN non_trovato" in r["error"]
    assert rete.conta["risolvi"] == 1
    dimentica()
    d = ct.dispatch("get_corporate_events_for_ticker", {"ticker": "QQNOMEDB.MI"})["data"]
    assert "risoluzione ISIN non_trovato" in d["fonti"]["borsa_italiana"]
    assert rete.conta["risolvi"] == 2
    dimentica()
    vu, fonte, nota = ct._prossima_trimestrale("QQNOMEDB.MI")
    assert vu is None and "risoluzione ISIN non_trovato" in nota
    assert rete.conta["risolvi"] == 3
    # senza dimenticare: il quarto uso legge la memoria negativa, nessuna rete, e lo DICE
    vu, fonte, nota = ct._prossima_trimestrale("QQNOMEDB.MI")
    assert rete.conta["risolvi"] == 3 and "risoluzione gia' tentata il" in nota


def test_voce_gia_nel_negozio_nessuna_risoluzione(rete):
    import json as _json
    from bellomberg.market_data import borsa_italiana as _bi
    p = rete.tmp / "isin_conf.json"
    # ISIN INVENTATO con cifra di controllo valida (dall'esempio tracciato isin_it.example.json)
    p.write_text(_json.dumps({"ZZTEST.MI": {"isin": "ITZZTEST0001", "emarket": None}}), encoding="utf-8")
    rete.mp.setattr(_bi, "PERCORSO_ISIN", str(p))
    voce, err, mot = _bi.voce_ticker_o_auto("ZZTEST.MI")
    assert voce is not None and voce["negozio"] == "confermato", (err, mot)
    at.tool_get_insider_trades("ZZTEST.MI")
    assert rete.conta["risolvi"] == 0


def test_nei_test_risoluzione_isin_e_nome_non_fanno_rete(monkeypatch, tmp_path):
    """Senza la fixture `rete`: il finto di conftest neutralizza i DUE punti di rete
    dell'orchestratore (nome dal fornitore, _risolvi), NON borsa_italiana.risolvi_isin."""
    from bellomberg.market_data import borsa_italiana as _bi
    from bellomberg.market_data import isin_automatico as _ia
    monkeypatch.setattr(_bi, "PERCORSO_ISIN", str(tmp_path / "a.json"))
    monkeypatch.setattr(_bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "b.json"))
    nome, fonte, perche = at.nome_emittente_it("QQSYN.MI")
    assert (nome, fonte) == (None, None) and "non chiesto" in perche and "book" in perche
    assert _bi.risolvi_isin.__module__ == "bellomberg.market_data.borsa_italiana"   # intatta
    assert _ia._risolvi("QQSYN.MI", "X")["motivo"] == "test: risoluzione ISIN non eseguita"
    r = at.assicura_isin_it("QQSYN.MI")
    assert r["errore"] == "nome_assente" and "non chiesto" in r["motivo"]
    assert str(tmp_path) in r["memoria"]["percorso"]   # memoria dei tentativi mai in data/
