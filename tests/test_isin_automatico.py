"""ISIN automatico dei .MI (W1 handoff-3, 05/10/2026, Opus 5.5): market_data/isin_automatico.py.

Si prova l'orchestratore VERO (nome dell'emittente -> risolvi_isin -> negozio automatico ->
memoria negativa) con i due punti di rete sostituiti da spie che CONTANO: `_yf_ticker`
(fornitore prezzi) e `_risolvi` (Borsa Italiana). Negozi, memoria e DB in tmp_path. Ticker,
nomi e ISIN INVENTATI (ISIN con cifra di controllo valida, calcolata qui). Nessuna rete:
guardia sul socket.
"""
import json
import socket
import sqlite3
import types
from datetime import datetime, timedelta, timezone

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import copertura
from bellomberg.market_data import isin_automatico as ia


@pytest.fixture(autouse=True)
def _niente_rete(monkeypatch):
    vero = socket.socket.connect

    def vietato(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and addr[0] in ("127.0.0.1", "::1"):
            return vero(self, addr, *a, **k)
        raise AssertionError("rete vietata nei test dell'ISIN automatico: %r" % (addr,))
    monkeypatch.setattr(socket.socket, "connect", vietato)


def isin_sintetico(base):
    """`base` di 11 caratteri + la cifra di controllo che lo rende valido."""
    for c in "0123456789":
        if bi.isin_valido(base + c):
            return base + c
    raise AssertionError(base)


ISIN_A = isin_sintetico("ITQQSYN0000")
ISIN_B = isin_sintetico("ITQQSYN0001")


class Orologio:
    def __init__(self):
        self.t = datetime(2031, 3, 1, 9, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


@pytest.fixture
def amb(monkeypatch, tmp_path):
    """Negozi ISIN e memoria in tmp_path; book finto; spie su fornitore prezzi e risolutore."""
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(tmp_path / "isin_it.json"))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache_it"))
    monkeypatch.setattr(ia, "PERCORSO_TENTATIVI", None)   # default: dentro CACHE_DIR (tmp)
    orologio = Orologio()
    monkeypatch.setattr(bi, "adesso_utc", orologio)
    book = {}
    monkeypatch.setattr(copertura, "valuta_dal_book", lambda t, db_path=None: (
        {"valuta": "EUR", "origine": "posizione", "nome": book[t], "nota": "finto"} if t in book
        else {"valuta": None, "origine": "fuori_book", "nome": None, "nota": "finto"}))
    conta = {"info": 0, "risolvi": 0}
    info = {}
    chiamate = []

    class TickerFinto:
        def __init__(self, t):
            self.t = t

        def get_info(self):
            conta["info"] += 1
            v = info.get(self.t, {})
            if isinstance(v, Exception):
                raise v
            return v
    monkeypatch.setattr(ia, "_yf_ticker", TickerFinto)
    monkeypatch.setattr(ia, "_nome_dal_fornitore", ia._nome_dal_fornitore_vero)
    risposta = {"f": lambda t, nome: {"stato": "non_trovato", "errore": "nome_non_nel_listino",
                                      "motivo": "finto: nessuna riga", "isin": None, "salvato": False}}

    def risolvi(t, nome):
        conta["risolvi"] += 1
        chiamate.append((t, nome))
        return risposta["f"](t, nome)
    monkeypatch.setattr(ia, "_risolvi", risolvi)
    return types.SimpleNamespace(tmp=tmp_path, book=book, info=info, conta=conta, chiamate=chiamate,
                                 risposta=risposta, orologio=orologio, mp=monkeypatch)


def _ok_che_salva(isin=ISIN_A):
    """Come la vera: dopo la verifica scrive la voce nel negozio automatico (scrittore VERO)."""
    def f(t, nome):
        adesso = bi.adesso_utc().isoformat(timespec="seconds")
        salvato, motivo = bi._scrivi_voce_auto(t, {"isin": isin, "emarket": None, "emarket_motivo": "finto",
                                                   "origine": bi.ORIGINE_AUTO % adesso, "verificato_il": adesso,
                                                   "nome_cercato": nome})
        return {"stato": "ok", "errore": None, "motivo": motivo, "isin": isin, "salvato": salvato,
                "negozio": "automatico"}
    return f


# ------------------------------------------------------------------ il nome
def test_nome_dal_book_prima_del_fornitore(amb):
    amb.book["QQSYN.MI"] = "Sintetica Industrie Spa"
    amb.info["QQSYN.MI"] = {"longName": "Altro Nome S.p.A."}
    assert ia.nome_emittente_it("qqsyn.mi") == ("Sintetica Industrie Spa", ia.FONTE_NOME_BOOK, None)
    assert amb.conta["info"] == 0   # il DB basta: nessuna rete


@pytest.mark.parametrize("info,campo", [({"longName": "Sintetica S.p.A.", "shortName": "SINT"}, "longName"),
                                        ({"shortName": "SINTETICA"}, "shortName")])
def test_nome_dal_fornitore_dichiarato(amb, info, campo):
    amb.info["QQSYN.MI"] = info
    nome, fonte, perche = ia.nome_emittente_it("QQSYN.MI")
    assert nome == info[campo] and fonte == "nome dal fornitore prezzi (yfinance %s)" % campo and perche is None


@pytest.mark.parametrize("info,frase", [({}, "senza longName/shortName"),
                                        ({"longName": "   "}, "senza longName/shortName"),
                                        (RuntimeError("https://x.invalid/?k=QQSEGRETO"), "yfinance: RuntimeError")])
def test_nome_assente_motivo_dichiarato_mai_dal_simbolo(amb, info, frase):
    amb.info["QQSYN.MI"] = info
    nome, fonte, perche = ia.nome_emittente_it("QQSYN.MI")
    assert (nome, fonte) == (None, None)
    assert frase in perche and "posizione attiva" in perche and "QQSEGRETO" not in perche


def test_nome_yfinance_non_installato(amb, monkeypatch):
    def manca(t):
        raise ImportError("no yfinance")
    monkeypatch.setattr(ia, "_yf_ticker", manca)
    assert "yfinance non disponibile" in ia.nome_emittente_it("QQSYN.MI")[2]


# ------------------------------------------------------------------ assicura_isin_it
def test_nome_assente_non_trovato_senza_risolutore(amb):
    r = ia.assicura_isin_it("QQSYN.MI")
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_assente"
    assert "nome dell'emittente non disponibile" in r["motivo"]
    assert amb.conta["risolvi"] == 0 and r["nome_usato"] is None


def test_voce_gia_nel_negozio_confermato_nessuna_rete(amb):
    (amb.tmp / "isin_it.json").write_text(json.dumps({"QQSYN.MI": {"isin": ISIN_A, "emarket": None}}),
                                          encoding="utf-8")
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    assert ia.assicura_isin_it("QQSYN.MI") is None
    assert amb.conta == {"info": 0, "risolvi": 0}


def test_negozio_confermato_vince_sull_automatico_e_non_si_tocca(amb):
    conf = json.dumps({"QQSYN.MI": {"isin": ISIN_A, "emarket": None}})
    (amb.tmp / "isin_it.json").write_text(conf, encoding="utf-8")
    bi._scrivi_voce_auto("QQSYN.MI", {"isin": ISIN_B, "emarket": None, "origine": bi.ORIGINE_AUTO % "x",
                                      "verificato_il": "x"})
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    assert ia.assicura_isin_it("QQSYN.MI") is None
    voce, _e, _m = bi.voce_ticker_o_auto("QQSYN.MI")
    assert voce["isin"] == ISIN_A and voce["negozio"] == "confermato"
    assert (amb.tmp / "isin_it.json").read_text(encoding="utf-8") == conf   # mai sovrascritto
    assert amb.conta["risolvi"] == 0


def test_voce_gia_nel_negozio_automatico_nessuna_rete(amb):
    bi._scrivi_voce_auto("QQSYN.MI", {"isin": ISIN_B, "emarket": None, "origine": bi.ORIGINE_AUTO % "x",
                                      "verificato_il": "x"})
    assert ia.assicura_isin_it("QQSYN.MI") is None and amb.conta == {"info": 0, "risolvi": 0}


def test_negozio_confermato_illeggibile_non_si_scavalca(amb):
    (amb.tmp / "isin_it.json").write_text("{rotto", encoding="utf-8")
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    assert ia.assicura_isin_it("QQSYN.MI") is None and amb.conta["risolvi"] == 0


@pytest.mark.parametrize("t", ["QQSYN", "QQSYN.DE", "", None, "QQ SYN.MI"])
def test_simbolo_non_mi_niente_da_risolvere(amb, t):
    assert ia.assicura_isin_it(t) is None and amb.conta == {"info": 0, "risolvi": 0}


def test_ok_col_nome_del_book_voce_davvero_nel_negozio(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    amb.risposta["f"] = _ok_che_salva()
    r = ia.assicura_isin_it("QQSYN.MI")
    assert r["stato"] == "ok" and r["isin"] == ISIN_A and r["salvato"] is True
    assert r["nome_usato"] == "Sintetica Spa" and r["fonte_nome"] == ia.FONTE_NOME_BOOK
    assert amb.chiamate == [("QQSYN.MI", "Sintetica Spa")]
    assert "memoria" not in r   # un esito ok non si memorizza come negativo
    voce, _e, _m = bi.voce_ticker_o_auto("QQSYN.MI")
    assert voce["negozio"] == "automatico" and voce["isin"] == ISIN_A
    assert ia.assicura_isin_it("QQSYN.MI") is None and amb.conta["risolvi"] == 1   # secondo uso: niente rete


def test_ok_dichiarato_ma_voce_non_scritta_e_KO(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    amb.risposta["f"] = lambda t, nome: {"stato": "ok", "isin": ISIN_A, "salvato": True, "negozio": "automatico",
                                         "motivo": None, "errore": None}
    r = ia.assicura_isin_it("QQSYN.MI")
    assert r["stato"] == "KO" and r["errore"] == "voce_non_salvata" and ISIN_A in r["motivo"]
    assert r["memoria"]["ttl_s"] == ia.TTL_KO_S


def test_ok_ma_negozio_con_altro_isin_e_KO(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    amb.risposta["f"] = lambda t, nome: (_ok_che_salva(ISIN_B)(t, nome), {"stato": "ok", "isin": ISIN_A,
                                                                         "salvato": True})[1]
    r = ia.assicura_isin_it("QQSYN.MI")
    assert r["stato"] == "KO" and r["errore"] == "voce_incoerente"


def test_eccezione_del_risolutore_solo_il_tipo(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"

    def esplode(t, nome):
        raise ConnectionError("https://www.borsaitaliana.it/?QQSEGRETO")
    amb.risposta["f"] = esplode
    r = ia.assicura_isin_it("QQSYN.MI")
    assert r["stato"] == "KO" and r["errore"] == "ConnectionError" and "QQSEGRETO" not in json.dumps(r)


def test_stato_inatteso_del_risolutore_e_KO(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    amb.risposta["f"] = lambda t, nome: {"stato": "forse"}
    assert ia.assicura_isin_it("QQSYN.MI")["errore"] == "stato_inatteso"


# ------------------------------------------------------------------ memoria negativa
def test_memoria_negativa_niente_rete_fino_alla_scadenza(amb):
    amb.info["QQSYN.MI"] = {"longName": "Sintetica S.p.A."}
    r1 = ia.assicura_isin_it("QQSYN.MI")
    assert r1["stato"] == "non_trovato" and amb.conta == {"info": 1, "risolvi": 1}
    assert r1["memoria"]["ttl_s"] == ia.TTL_NEGATIVO_S
    p = amb.tmp / "cache_it" / ia.NOME_FILE_TENTATIVI
    assert p.exists()   # dentro CACHE_DIR
    amb.orologio.t += timedelta(hours=71)
    r2 = ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta == {"info": 1, "risolvi": 1}   # ne' yfinance ne' Borsa
    assert r2["motivo"].startswith("risoluzione gia' tentata il 2031-03-01T09:00:00+00:00, esito non_trovato")
    assert "finto: nessuna riga" in r2["motivo"] and r2["memoria"]["riprova_dopo"] == "2031-03-04T09:00:00+00:00"
    amb.orologio.t += timedelta(hours=1)   # 72 h: scaduta
    ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta == {"info": 2, "risolvi": 2}


def test_memoria_del_ko_scade_prima(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    amb.risposta["f"] = lambda t, nome: {"stato": "KO", "errore": "rete", "motivo": "listino: Timeout"}
    assert ia.assicura_isin_it("QQSYN.MI")["memoria"]["ttl_s"] == ia.TTL_KO_S
    amb.orologio.t += timedelta(seconds=ia.TTL_KO_S - 1)
    assert "gia' tentata" in ia.assicura_isin_it("QQSYN.MI")["motivo"] and amb.conta["risolvi"] == 1
    amb.orologio.t += timedelta(seconds=1)
    ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta["risolvi"] == 2


def test_memoria_del_nome_assente_decade_se_il_book_ora_ha_il_nome(amb):
    r1 = ia.assicura_isin_it("QQSYN.MI")
    assert r1["errore"] == "nome_assente" and amb.conta == {"info": 1, "risolvi": 0}
    assert "gia' tentata" in ia.assicura_isin_it("QQSYN.MI")["motivo"] and amb.conta["info"] == 1
    amb.book["QQSYN.MI"] = "Sintetica Spa"   # il PM compila la posizione: non aspetta 72 h
    r3 = ia.assicura_isin_it("QQSYN.MI")
    assert amb.chiamate == [("QQSYN.MI", "Sintetica Spa")]
    assert "il book ora dice 'Sintetica Spa'" in r3["memoria"]["nota_lettura"]


def test_memoria_con_lo_stesso_nome_resta_valida(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    ia.assicura_isin_it("QQSYN.MI")
    ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta["risolvi"] == 1


def test_memoria_per_ticker(amb):
    amb.book.update({"QQSYN.MI": "Sintetica Spa", "QQALT.MI": "Altra Sintetica Spa"})
    ia.assicura_isin_it("QQSYN.MI")
    ia.assicura_isin_it("QQALT.MI")
    assert amb.conta["risolvi"] == 2


def test_memoria_con_ora_nel_futuro_non_vale(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    ia.assicura_isin_it("QQSYN.MI")
    amb.orologio.t -= timedelta(days=2)   # orologio tornato indietro
    r = ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta["risolvi"] == 2 and "nel futuro" in r["memoria"]["nota_lettura"]


def test_memoria_illeggibile_si_riprova_e_si_dice(amb):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    p = amb.tmp / "cache_it" / ia.NOME_FILE_TENTATIVI
    p.parent.mkdir(parents=True)
    p.write_text("{non json", encoding="utf-8")
    r = ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta["risolvi"] == 1 and "illeggibile (JSONDecodeError)" in r["memoria"]["nota_lettura"]
    # riscritta pulita: il secondo uso la legge
    ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta["risolvi"] == 1


def test_memoria_non_scrivibile_dichiarata(amb, monkeypatch):
    amb.book["QQSYN.MI"] = "Sintetica Spa"

    def no(*a, **k):
        raise PermissionError("C:/percorso/QQSEGRETO")
    monkeypatch.setattr(ia.os, "replace", no)
    r = ia.assicura_isin_it("QQSYN.MI")
    assert "non scritta" in r["memoria"]["nota"] and "PermissionError" in r["memoria"]["nota"] and "QQSEGRETO" not in json.dumps(r)


# ------------------------------------------------------------------ con_risoluzione
def test_con_risoluzione():
    assert ia.con_risoluzione({"a": 1}, None) == {"a": 1}
    ko = {"stato": "non_trovato", "motivo": "perche' X"}
    p = ia.con_risoluzione({"error": "internal dealing KO: ticker_non_mappato"}, ko)
    assert p["risoluzione_isin"] is ko
    assert p["error"] == "internal dealing KO: ticker_non_mappato | risoluzione ISIN non_trovato: perche' X"
    ok = ia.con_risoluzione({"error": "altro guasto"}, {"stato": "ok", "motivo": "m"})
    assert ok["error"] == "altro guasto"
    senza_errore = ia.con_risoluzione({}, ko)
    assert "error" not in senza_errore and senza_errore["risoluzione_isin"] is ko
    assert ia.con_risoluzione(["lista"], ko) == ["lista"]


def test_agent_tools_ri_esporta_gli_stessi_nomi():
    from bellomberg.agents import agent_tools as at
    assert at.assicura_isin_it is ia.assicura_isin_it
    assert at.nome_emittente_it is ia.nome_emittente_it
    assert at.con_risoluzione is ia.con_risoluzione


# ------------------------------------------------------------------ valuta_dal_book: nome
def _db(tmp_path, con_nome):
    p = tmp_path / ("book_%s.db" % con_nome)
    con = sqlite3.connect(str(p))
    if con_nome:
        con.execute("CREATE TABLE positions (ticker TEXT, valuta TEXT, is_active INTEGER, nome TEXT)")
        con.execute("INSERT INTO positions VALUES ('QQSYN.MI','EUR',1,' Sintetica Spa ')")
        con.execute("INSERT INTO positions VALUES ('QQCHIUSA.MI','EUR',0,'Chiusa Spa')")
    else:
        con.execute("CREATE TABLE positions (ticker TEXT, valuta TEXT, is_active INTEGER)")
        con.execute("INSERT INTO positions VALUES ('QQSYN.MI','EUR',1)")
    con.commit()
    con.close()
    return str(p)


def test_valuta_dal_book_porta_il_nome(tmp_path):
    db = _db(tmp_path, True)
    vb = copertura.valuta_dal_book_vera("qqsyn.mi", db_path=db)
    assert vb["valuta"] == "EUR" and vb["nome"] == "Sintetica Spa"
    assert copertura.valuta_dal_book_vera("QQCHIUSA.MI", db_path=db)["nome"] is None   # solo attive


def test_valuta_dal_book_db_senza_colonna_nome_legge_la_valuta(tmp_path):
    vb = copertura.valuta_dal_book_vera("QQSYN.MI", db_path=_db(tmp_path, False))
    assert vb["valuta"] == "EUR" and vb["origine"] == "posizione" and vb["nome"] is None


def test_valuta_dal_book_db_assente_ha_nome_none(tmp_path):
    vb = copertura.valuta_dal_book_vera("QQSYN.MI", db_path=str(tmp_path / "manca.db"))
    assert vb["origine"] == "db_assente" and vb["nome"] is None


def test_nome_emittente_col_db_esplicito(tmp_path, monkeypatch):
    """db_path passa fino alla lettura vera (come fa l'API col suo DB)."""
    monkeypatch.setattr(copertura, "valuta_dal_book", copertura.valuta_dal_book_vera)
    assert ia.nome_emittente_it("QQSYN.MI", db_path=_db(tmp_path, True))[0] == "Sintetica Spa"


# ------------------------------------------------------------------ endpoint API
def test_api_insider_mi_risolve_col_nome_del_suo_db(db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import bellomberg.api.bellomberg_api as api
    from bellomberg.market_data import sdir
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(tmp_path / "isin_it.json"))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    with sqlite3.connect(db.db_path) as con:
        con.execute("INSERT INTO positions (ticker, nome, quantita, valuta, is_active) "
                    "VALUES ('QQSYN.MI', 'Sintetica Spa', 10, 'EUR', 1)")
    chiamate = []

    def risolvi(t, nome):
        chiamate.append((t, nome))
        return _ok_che_salva()(t, nome)
    monkeypatch.setattr(ia, "_risolvi", risolvi)
    letti, letti_nome = [], []

    def idl(ticker, *, giorni=180, nome=None):
        letti_nome.append(nome)
        voce, _e, _m = bi.voce_ticker_o_auto(ticker)
        letti.append(voce)
        return {"ticker": ticker.upper(), "stato": "vuoto_misurato", "comunicazioni": [],
                "voce_da": voce and voce["negozio"]}
    monkeypatch.setattr(sdir, "get_internal_dealing", idl)
    monkeypatch.setattr(api, "get_db", lambda: db)
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        out = TestClient(api.app, base_url="http://127.0.0.1").get("/news/insider-trades/qqsyn.mi").json()
    finally:
        api.app.dependency_overrides.clear()
    assert chiamate == [("QQSYN.MI", "Sintetica Spa")]
    assert out["risoluzione_isin"]["stato"] == "ok" and out["risoluzione_isin"]["fonte_nome"] == ia.FONTE_NOME_BOOK
    assert letti[0]["isin"] == ISIN_A and out["internal_dealing"]["voce_da"] == "automatico"
    assert out["error"] is None and out["source"] == "sdir"
    assert letti_nome == ["Sintetica Spa"]   # il nome noto passa all'instradatore SDIR


def test_api_insider_mi_ko_dichiara_la_risoluzione(db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import bellomberg.api.bellomberg_api as api
    from bellomberg.market_data import sdir
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(tmp_path / "isin_it.json"))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(sdir, "get_internal_dealing", lambda t, *, giorni=180, nome=None: {
        "ticker": t.upper(), "stato": "KO", "errore": "negozio_assente", "motivo": "ISIN mancante",
        "comunicazioni": []})
    monkeypatch.setattr(api, "get_db", lambda: db)
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        out = TestClient(api.app, base_url="http://127.0.0.1").get("/news/insider-trades/QQSYN.MI").json()
    finally:
        api.app.dependency_overrides.clear()
    # fuori dal book e fornitore neutralizzato (conftest): nome assente, dichiarato nell'errore
    assert out["risoluzione_isin"]["errore"] == "nome_assente"
    assert out["error"].startswith("ISIN mancante | risoluzione ISIN non_trovato: ")


from test_persistence import db  # noqa: E402,F401  (fixture: MemoryDB in tmp_path, senza chroma)


# ------------------------------------------------------------------ nome esplicito (per D4)
def test_nome_esplicito_salta_book_e_fornitore(amb):
    amb.book["QQSYN.MI"] = "Nome Del Book Spa"
    amb.info["QQSYN.MI"] = {"longName": "Nome Fornitore Spa"}
    r = ia.assicura_isin_it("QQSYN.MI", nome="Identita Confermata Spa", fonte_nome="confirmed_identity")
    assert amb.chiamate == [("QQSYN.MI", "Identita Confermata Spa")] and amb.conta["info"] == 0
    assert r["nome_usato"] == "Identita Confermata Spa" and r["fonte_nome"] == "confirmed_identity"


def test_nome_esplicito_senza_fonte_rifiutato(amb):
    with pytest.raises(ValueError, match="fonte_nome"):
        ia.assicura_isin_it("QQSYN.MI", nome="Sintetica Spa")
    assert amb.conta == {"info": 0, "risolvi": 0}


@pytest.mark.parametrize("nome", ["", "   ", "QQSYN.MI", "qqsyn mi", "QQSYN IM"])
def test_nome_esplicito_vuoto_o_simbolo_non_cerca(amb, nome):
    r = ia.assicura_isin_it("QQSYN.MI", nome=nome, fonte_nome="confirmed_identity")
    assert r["errore"] == "nome_assente" and amb.conta == {"info": 0, "risolvi": 0}
    assert "confirmed_identity" in r["motivo"]


def test_nome_esplicito_memoria_vale_finche_il_nome_e_lo_stesso(amb):
    ia.assicura_isin_it("QQSYN.MI", nome="Sintetica Spa", fonte_nome="confirmed_identity")
    r2 = ia.assicura_isin_it("QQSYN.MI", nome="Sintetica Spa", fonte_nome="confirmed_identity")
    assert amb.conta["risolvi"] == 1 and "gia' tentata" in r2["motivo"]
    r3 = ia.assicura_isin_it("QQSYN.MI", nome="Sintetica Holding Spa", fonte_nome="confirmed_identity")
    assert amb.conta["risolvi"] == 2 and "nome passato" in r3["memoria"]["nota_lettura"]


# ------------------------------------------------------------------ review RV-W1 (R1-R4)
@pytest.mark.parametrize("guasto", ["yfinance", "book"])
def test_guasto_transitorio_del_nome_e_KO_con_memoria_corta(amb, monkeypatch, guasto):
    if guasto == "yfinance":
        amb.info["QQSYN.MI"] = TimeoutError("rate limit")
    else:
        monkeypatch.setattr(copertura, "valuta_dal_book", lambda t, db_path=None: {
            "valuta": None, "origine": "illeggibile", "nome": None, "nota": "finto: DB lockato"})
    r = ia.assicura_isin_it("QQSYN.MI")
    assert r["stato"] == "KO" and r["errore"] == "nome_assente" and "[transitorio]" in r["motivo"]
    assert r["memoria"]["ttl_s"] == ia.TTL_KO_S


def test_fornitore_senza_nome_resta_non_trovato_72h(amb):
    amb.info["QQSYN.MI"] = {}
    r = ia.assicura_isin_it("QQSYN.MI")
    assert r["stato"] == "non_trovato" and r["memoria"]["ttl_s"] == ia.TTL_NEGATIVO_S


@pytest.mark.parametrize("info", [{"shortName": "QQSYN.MI"}, {"longName": "qqsyn.mi"},
                                  {"longName": "QQSYN MI"}])
def test_nome_del_fornitore_uguale_al_simbolo_scartato(amb, info):
    amb.info["QQSYN.MI"] = info
    nome, fonte, perche = ia.nome_emittente_it("QQSYN.MI")
    assert nome is None and "e' il simbolo, non un nome" in perche
    ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta["risolvi"] == 0


def test_nome_vero_con_forma_giuridica_non_scartato(amb):
    amb.info["QQSYN.MI"] = {"shortName": "QQSYN", "longName": "Qqsyn S.p.A."}
    assert ia.nome_emittente_it("QQSYN.MI")[0] == "Qqsyn S.p.A."


def test_due_ticker_in_parallelo_non_perdono_voci(amb):
    import threading
    import time as _t
    amb.book.update({"QQAAA.MI": "Aaa Sintetica Spa", "QQBBB.MI": "Bbb Sintetica Spa"})
    isin = {"QQAAA.MI": ISIN_A, "QQBBB.MI": ISIN_B}
    import json as _json

    def dump_lento(obj, fh, **k):   # finestra FRA la lettura e la sostituzione del negozio
        _t.sleep(0.2)
        return _json.dump(obj, fh, **k)
    amb.mp.setattr(bi, "json", types.SimpleNamespace(load=_json.load, dump=dump_lento,
                                                     dumps=_json.dumps, loads=_json.loads,
                                                     JSONDecodeError=_json.JSONDecodeError))
    amb.risposta["f"] = lambda t, nome: _ok_che_salva(isin[t])(t, nome)
    esiti = {}
    th = [threading.Thread(target=lambda t=t: esiti.__setitem__(t, ia.assicura_isin_it(t))) for t in isin]
    for x in th:
        x.start()
    for x in th:
        x.join()
    assert all(e["stato"] == "ok" for e in esiti.values()), esiti
    voci = bi.carica_isin_auto(bi.PERCORSO_ISIN_AUTO)["voci"]
    assert {k: v["isin"] for k, v in voci.items()} == isin


@pytest.mark.parametrize("ttl", [10 ** 30, 0, -5, 10 ** 9])
def test_memoria_con_ttl_assurdo_non_esplode(amb, ttl):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    p = amb.tmp / "cache_it" / ia.NOME_FILE_TENTATIVI
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"QQSYN.MI": {"versione": ia.MEMORIA_VERSIONE, "tentato_il": "2031-03-01T08:00:00+00:00", "ttl_s": ttl,
                                          "ris": {"stato": "non_trovato", "nome_usato": "Sintetica Spa"}}}),
                 encoding="utf-8")
    r = ia.assicura_isin_it("QQSYN.MI")
    assert amb.conta["risolvi"] == 1 and "si riprova" in r["memoria"]["nota_lettura"]


@pytest.mark.parametrize("giorni", [0, -1, "x", 10 ** 6])   # _internal_dealing_it converte prima con int()
def test_giorni_non_validi_niente_risoluzione(amb, giorni):
    from bellomberg.agents import agent_tools as at
    from bellomberg.market_data import sdir
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    amb.mp.setattr(sdir, "get_internal_dealing", lambda t, *, giorni=180, nome=None: {
        "ticker": t, "stato": "KO", "errore": "parametro", "motivo": "giorni non validi", "comunicazioni": []})
    out = at._internal_dealing_it("QQSYN.MI", giorni, {"motivo": "finto"})
    assert amb.conta["risolvi"] == 0 and "risoluzione_isin" not in out
    assert ia.giorni_validi(90) and not ia.giorni_validi(giorni)


# ------------------------------------------------------------------ review RV-W1 (mutanti sopravvissuti MB-ME)
def _sdir_spia(amb):
    from bellomberg.market_data import sdir
    nomi = []

    def idl(ticker, *, giorni=180, nome=None):
        nomi.append(nome)
        return {"ticker": ticker, "stato": "vuoto_misurato", "comunicazioni": [], "sdir": "eMarket SDIR"}
    amb.mp.setattr(sdir, "get_internal_dealing", idl)
    return nomi


def test_agente_passa_allo_sdir_il_nome_del_fornitore_usato(amb):
    """Fuori book: il nome che arriva allo SDIR e' quello usato dalla risoluzione (yfinance)."""
    from bellomberg.agents import agent_tools as at
    nomi = _sdir_spia(amb)
    amb.info["QQSYN.MI"] = {"longName": "Sintetica Fornitore S.p.A."}
    amb.risposta["f"] = _ok_che_salva()
    out = at._internal_dealing_it("QQSYN.MI", 30, {"motivo": "finto"})
    assert out["risoluzione_isin"]["fonte_nome"] == "nome dal fornitore prezzi (yfinance longName)"
    assert nomi == ["Sintetica Fornitore S.p.A."]


def test_agente_passa_allo_sdir_il_nome_del_book_senza_risoluzione(amb):
    from bellomberg.agents import agent_tools as at
    nomi = _sdir_spia(amb)
    bi._scrivi_voce_auto("QQSYN.MI", {"isin": ISIN_A, "emarket": None, "origine": bi.ORIGINE_AUTO % "x",
                                      "verificato_il": "x"})
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    at._internal_dealing_it("QQSYN.MI", 30, {"motivo": "finto"})
    assert nomi == ["Sintetica Spa"] and amb.conta == {"info": 0, "risolvi": 0}


def test_stesso_ticker_in_parallelo_una_sola_risoluzione(amb):
    import threading
    import time as _t
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    ok = _ok_che_salva()
    cancello = threading.Event()   # la risoluzione finisce solo quando TUTTI sono in attesa

    def lento(t, nome):
        cancello.wait(10)
        return ok(t, nome)
    amb.risposta["f"] = lento
    esiti = []
    # stessa richiesta: UNA risoluzione in volo, il risultato condiviso
    th = [threading.Thread(target=lambda: esiti.append(ia.assicura_isin_it("QQSYN.MI"))) for _ in range(3)]
    # richiesta diversa (db_path) per lo stesso ticker: si aggancia alla stessa risoluzione
    th.append(threading.Thread(target=lambda: esiti.append(ia.assicura_isin_it("QQSYN.MI", db_path="altro.db"))))
    for x in th:
        x.start()
    fine = _t.monotonic() + 10   # tutti agganciati alla risoluzione in volo (thread isin-auto vivo)
    while _t.monotonic() < fine and not (len(ia._IN_CORSO) == 1 and all(x.is_alive() for x in th)):
        _t.sleep(0.01)
    _t.sleep(0.5)
    cancello.set()
    for x in th:
        x.join()
    assert amb.conta["risolvi"] == 1
    assert [e["stato"] for e in esiti] == ["ok"] * 4
    assert sum(1 for e in esiti if e.get("gia_in_corso_dal")) == 3


def test_sotto_lock_si_ricontrolla_il_negozio(amb):
    """Chi arriva al lock dopo che un altro ha scritto la voce non rifa' la rete."""
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    bi._scrivi_voce_auto("QQSYN.MI", {"isin": ISIN_A, "emarket": None, "origine": bi.ORIGINE_AUTO % "x",
                                      "verificato_il": "x"})
    assert ia._assicura_sotto_lock("QQSYN.MI", None, None, None) is None
    assert amb.conta["risolvi"] == 0


def test_chat_riga_fonte_dichiara_il_nome_usato(amb, monkeypatch):
    from bellomberg.agents import chat_tools as ct
    from bellomberg.storage import classificazione as _cl
    monkeypatch.setattr(_cl, "PERCORSO_VEICOLI", str(amb.tmp / "veicoli_assente.json"))
    monkeypatch.setattr(bi, "get_eventi_societari", lambda t, **k: {
        "stato": "vuoto_misurato", "eventi": [], "voce_da": "automatico"})
    amb.info["QQSYN.MI"] = {"longName": "Sintetica Fornitore S.p.A."}
    amb.risposta["f"] = _ok_che_salva()
    _ev, riga, ok, _ko = ct._fonte_borsa_italiana("QQSYN.MI", 30)
    assert ok == 1
    assert "ISIN risolto ora su Borsa Italiana col nome 'Sintetica Fornitore S.p.A.'" in riga
    assert "yfinance longName" in riga


# ------------------------------------------------------------------ budget (R5) e KO strutturali (R7)
def test_budget_superato_KO_dichiarato_e_la_risoluzione_continua(amb):
    import time as _t
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    ok = _ok_che_salva()

    def lento(t, nome):
        _t.sleep(0.6)
        return ok(t, nome)
    amb.risposta["f"] = lento
    r = ia.assicura_isin_it("QQSYN.MI", budget_s=0.1)
    assert r["stato"] == "KO" and r["errore"] == "tempo_esaurito" and "riprova" in r["motivo"]
    assert "memoria" not in r   # il tempo esaurito non si memorizza: l'esito vero arriva dal thread
    with ia.budget_risoluzione(5):
        r2 = ia.assicura_isin_it("QQSYN.MI")   # si aggancia alla risoluzione in volo
    assert r2["stato"] == "ok" and amb.conta["risolvi"] == 1
    assert ia.assicura_isin_it("QQSYN.MI") is None


def test_budget_dal_contesto_e_per_chiamante():
    assert ia.budget_per_chiamante("specialista-run:quant") == ia.BUDGET_DESK_S
    assert ia.budget_per_chiamante("red-team") == ia.BUDGET_DESK_S
    assert ia.budget_per_chiamante(None) == ia.BUDGET_INTERATTIVO_S
    assert ia.budget_per_chiamante("chat:fundamentals") == ia.BUDGET_INTERATTIVO_S
    assert ia._BUDGET.get() == ia.BUDGET_INTERATTIVO_S
    with ia.budget_risoluzione(None):
        assert ia._BUDGET.get() is None
    assert ia._BUDGET.get() == ia.BUDGET_INTERATTIVO_S


def test_chat_dispatch_passa_il_budget_del_chiamante(amb, monkeypatch):
    from bellomberg.agents import chat_tools as ct
    from bellomberg.agents import agent_tools as at
    visti = []
    monkeypatch.setattr(at, "insider_dichiarati",
                        lambda *a, **k: (visti.append(ia._BUDGET.get()) or ({}, "finto")))
    ct.dispatch("get_insider_trades", {"ticker": "QQSYN.MI"}, caller="specialista-run:quant")
    ct.dispatch("get_insider_trades", {"ticker": "QQSYN.MI"})
    assert visti == [ia.BUDGET_DESK_S, ia.BUDGET_INTERATTIVO_S]


def test_tool_agente_insider_ha_budget_desk(amb, monkeypatch):
    from bellomberg.agents import agent_tools as at
    visti = []
    monkeypatch.setattr(at, "insider_dichiarati",
                        lambda *a, **k: (visti.append(ia._BUDGET.get()) or ({}, "finto")))
    at.tool_get_insider_trades("QQSYN.MI")
    assert visti == [ia.BUDGET_DESK_S]


@pytest.mark.parametrize("errore,ttl", [("listino_troncato", "TTL_STRUTTURALE_S"),
                                        ("layout_cambiato", "TTL_STRUTTURALE_S"),
                                        ("schede_troncate", "TTL_STRUTTURALE_S"),
                                        ("rete", "TTL_KO_S"), ("http", "TTL_KO_S")])
def test_KO_strutturali_scadenza_lunga(amb, errore, ttl):
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    amb.risposta["f"] = lambda t, nome: {"stato": "KO", "errore": errore, "motivo": "finto"}
    assert ia.assicura_isin_it("QQSYN.MI")["memoria"]["ttl_s"] == getattr(ia, ttl)


# ------------------------------------------------------------------ thread della risoluzione (vincoli main)
def test_yfinance_appeso_ha_un_timeout(amb, monkeypatch):
    import threading
    import time as _t
    sblocca = threading.Event()

    class Appeso:
        def __init__(self, t):
            pass

        def get_info(self):
            sblocca.wait(5)
            return {"longName": "Troppo Tardi Spa"}
    monkeypatch.setattr(ia, "_yf_ticker", Appeso)
    monkeypatch.setattr(ia, "TIMEOUT_YF_S", 0.2)
    t0 = _t.monotonic()
    nome, fonte, perche = ia.nome_emittente_it("QQSYN.MI")
    sblocca.set()
    assert nome is None and "nessuna risposta in 0.2 s" in perche and "[transitorio]" in perche
    assert _t.monotonic() - t0 < 3


def test_thread_della_risoluzione_e_daemon_e_dichiarato(amb):
    import threading
    import time as _t
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    visti = []

    def lento(t, nome):
        visti.extend(th for th in threading.enumerate() if th.name == "isin-auto-QQSYN.MI")
        _t.sleep(0.3)
        return {"stato": "non_trovato", "errore": "nome_non_nel_listino", "motivo": "finto"}
    amb.risposta["f"] = lento
    r = ia.assicura_isin_it("QQSYN.MI", budget_s=5)
    assert r["stato"] == "non_trovato" and len(visti) == 1 and visti[0].daemon is True


def test_seconda_chiamata_durante_la_risoluzione_non_ne_avvia_unaltra(amb):
    import threading
    import time as _t
    amb.book["QQSYN.MI"] = "Sintetica Spa"
    via = threading.Event()

    def lento(t, nome):
        via.wait(5)
        return _ok_che_salva()(t, nome)
    amb.risposta["f"] = lento
    r1 = ia.assicura_isin_it("QQSYN.MI", budget_s=0.1)
    # chiave diversa (nome esplicito) ma STESSO ticker: nessuna seconda risoluzione, dichiarata
    r2 = ia.assicura_isin_it("QQSYN.MI", nome="Altro Nome Spa", fonte_nome="test", budget_s=0.1)
    assert r1["errore"] == r2["errore"] == "tempo_esaurito"
    assert "risoluzione gia' in corso dal " in r2["motivo"]
    via.set()
    r3 = ia.assicura_isin_it("QQSYN.MI", budget_s=5)
    assert amb.conta["risolvi"] == 1
    assert r3 is None or (r3["stato"] == "ok" and r3.get("gia_in_corso_dal"))


def test_lock_rilasciato_anche_in_eccezione(amb, monkeypatch):
    def esplode(t, db_path=None):
        raise RuntimeError("C:/percorso/QQSEGRETO")
    monkeypatch.setattr(copertura, "valuta_dal_book", esplode)
    r = ia.assicura_isin_it("QQSYN.MI", budget_s=5)
    assert r["stato"] == "KO" and r["errore"] == "RuntimeError" and "QQSEGRETO" not in json.dumps(r)
    assert ia._LOCK_RISOLUZIONE.acquire(timeout=1)   # libero: nessun lock rimasto preso
    ia._LOCK_RISOLUZIONE.release()
    assert ia._IN_CORSO == {}
    monkeypatch.setattr(copertura, "valuta_dal_book", lambda t, db_path=None: {
        "valuta": "EUR", "origine": "posizione", "nome": "Sintetica Spa", "nota": "finto"})
    assert ia.assicura_isin_it("QQSYN.MI", budget_s=5)["nome_usato"] == "Sintetica Spa"


def test_tetto_teorico_dichiarato_e_finito():
    from bellomberg.market_data import borsa_italiana as b
    atteso = ia.TIMEOUT_YF_S + (b.MAX_LETTERE * b.MAX_PAGINE_LISTINO + b.MAX_SCHEDE + 1) * (b.TIMEOUT_S + b.PAUSA_S)
    assert ia.tetto_teorico_s() == atteso and 0 < atteso < 3600


def test_negozi_isin_della_suite_sono_in_tmp(tmp_path):
    """conftest autouse: nessun test legge data/isin_it.json della macchina."""
    from bellomberg.market_data import borsa_italiana as b
    for p in (b.PERCORSO_ISIN, b.PERCORSO_ISIN_AUTO, b.CACHE_DIR, ia.PERCORSO_TENTATIVI):
        assert str(tmp_path) in str(p), p


@pytest.mark.parametrize("giorni", [0, -3, 100000])
def test_api_giorni_non_validi_niente_risoluzione(db, monkeypatch, giorni):
    from fastapi.testclient import TestClient
    import bellomberg.api.bellomberg_api as api
    from bellomberg.market_data import sdir
    chiamate = []
    monkeypatch.setattr(ia, "_risolvi", lambda t, nome: chiamate.append(t) or {"stato": "non_trovato"})
    monkeypatch.setattr(sdir, "get_internal_dealing", lambda t, *, giorni=180, nome=None: {
        "ticker": t.upper(), "stato": "KO", "errore": "parametro", "motivo": "giorni non validi",
        "comunicazioni": []})
    with sqlite3.connect(db.db_path) as con:
        con.execute("INSERT INTO positions (ticker, nome, quantita, valuta, is_active) "
                    "VALUES ('QQSYN.MI', 'Sintetica Spa', 10, 'EUR', 1)")
    monkeypatch.setattr(api, "get_db", lambda: db)
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        out = TestClient(api.app, base_url="http://127.0.0.1").get(
            "/news/insider-trades/QQSYN.MI?days=%d" % giorni).json()
    finally:
        api.app.dependency_overrides.clear()
    assert chiamate == [] and "risoluzione_isin" not in out and out["error"] == "giorni non validi"


# ------------------------------------------------------------------ fix P1 MF: nome uguale al simbolo BASE
@pytest.mark.parametrize("ticker,nome", [("QQA2A.MI", "Qqa2a"), ("QQENI.MI", "QQENI"), ("QQENEL.MI", "Qqenel")])
def test_nome_uguale_al_simbolo_base_e_legittimo(amb, ticker, nome):
    """Nome uguale al simbolo base («Qqa2a» per QQA2A.MI, «QQENI» per QQENI.MI): si cerca, decide la scheda."""
    amb.risposta["f"] = _ok_che_salva()
    r = ia.assicura_isin_it(ticker, nome=nome, fonte_nome="test")          # esplicito
    assert r["stato"] == "ok" and amb.chiamate[-1] == (ticker, nome)
    assert not ia.e_il_simbolo(nome, ticker)


@pytest.mark.parametrize("fonte", ["book", "fornitore"])
def test_nome_base_dal_book_o_dal_fornitore_si_usa(amb, fonte):
    if fonte == "book":
        amb.book["QQENI.MI"] = "QQENI"
    else:
        amb.info["QQENI.MI"] = {"shortName": "QQENI"}
    assert ia.nome_emittente_it("QQENI.MI")[0] == "QQENI"
    ia.assicura_isin_it("QQENI.MI")
    assert amb.chiamate == [("QQENI.MI", "QQENI")]


@pytest.mark.parametrize("nome", ["QQSYN.MI", "qqsyn mi", "QQSYN IM", "QQSYN.DE"])
def test_simbolo_col_suffisso_di_listino_e_riconosciuto(nome):
    assert ia.e_il_simbolo(nome, "QQSYN.MI")


@pytest.mark.parametrize("nome", ["QQSYN", "Qqsyn S.p.A.", "QQSYN SPA", "QQSYN Holding", "Altro.MI"])
def test_non_e_il_simbolo(nome):
    assert not ia.e_il_simbolo(nome, "QQSYN.MI")


def test_memoria_di_versione_precedente_non_vale(amb):
    """Le memorie nome_assente scritte dalla regola v1 (che scartava il nome uguale al simbolo base) si ignorano."""
    amb.book["QQENI.MI"] = "QQENI"
    p = amb.tmp / "cache_it" / ia.NOME_FILE_TENTATIVI
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"QQENI.MI": {"tentato_il": "2031-03-01T08:00:00+00:00", "ttl_s": ia.TTL_NEGATIVO_S,
                                          "ris": {"stato": "non_trovato", "errore": "nome_assente",
                                                  "nome_usato": None}}}), encoding="utf-8")
    r = ia.assicura_isin_it("QQENI.MI")
    assert amb.chiamate == [("QQENI.MI", "QQENI")]
    assert "regole precedenti (versione None, attuale %d)" % ia.MEMORIA_VERSIONE in r["memoria"]["nota_lettura"]
    scritto = json.loads(p.read_text(encoding="utf-8"))["QQENI.MI"]
    assert scritto["versione"] == ia.MEMORIA_VERSIONE


def test_risoluzioni_di_ticker_diversi_in_fila_non_in_parallelo(amb):
    """Il lock unico mette in fila le risoluzioni (una sola sequenza di richieste a Borsa per
    volta: le pause di risolvi_isin valgono solo dentro una risoluzione)."""
    import threading
    import time as _t
    amb.book.update({"QQAAA.MI": "Aaa Sintetica Spa", "QQBBB.MI": "Bbb Sintetica Spa"})
    in_volo, massimo, guardia = [0], [0], threading.Lock()

    def lento(t, nome):
        with guardia:
            in_volo[0] += 1
            massimo[0] = max(massimo[0], in_volo[0])
        _t.sleep(0.3)
        with guardia:
            in_volo[0] -= 1
        return {"stato": "non_trovato", "errore": "nome_non_nel_listino", "motivo": "finto"}
    amb.risposta["f"] = lento
    th = [threading.Thread(target=ia.assicura_isin_it, args=(t,), kwargs={"budget_s": 5})
          for t in ("QQAAA.MI", "QQBBB.MI")]
    for x in th:
        x.start()
    for x in th:
        x.join()
    assert amb.conta["risolvi"] == 2 and massimo[0] == 1
