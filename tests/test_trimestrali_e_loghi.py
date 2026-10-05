"""Trimestrali del portafoglio nel calendario e loghi dei titoli (02/10/2026).

Fatti misurati quel giorno sull'API vera (piano gratuito Finnhub):
- `/calendar/earnings` GLOBALE si ferma a 1500 voci e restituisce la coda della
  finestra: filtrata dopo, perdeva le trimestrali del portafoglio senza dirlo;
- `/calendar/earnings` e `/stock/profile2` rispondono 403 sui simboli non USA,
  e funzionano sugli ADR (nei test: simboli inventati come ACMPY, OMG).
Zero rete: trasporto finto per path, yfinance finto.
"""
import base64
import json
from datetime import date, timedelta

import pytest

from bellomberg.market_data import earnings_yf, finnhub_news, loghi_titoli


class _R:
    def __init__(self, status=200, payload=None, content=b"", content_type="application/json", text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.text = text or ("" if status == 200 else "HTTP %d" % status)

    def json(self):
        return self._payload


def _trasporto(monkeypatch, gestore):
    chiamate = []

    def _get(url, params=None, timeout=None, **k):
        chiamate.append((url, dict(params or {})))
        return gestore(url, dict(params or {}))
    monkeypatch.setattr(finnhub_news, "REQ_OK", True)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: "chiave-finta")
    monkeypatch.setattr(finnhub_news.requests, "get", _get)
    return chiamate


def _alias(tmp_path, monkeypatch, finnhub=None, yfinance=None):
    from bellomberg.storage import negozi_privati
    percorso = tmp_path / "alias_fonti.json"
    percorso.write_text(json.dumps({"finnhub": finnhub or {}, "sec": {}, "yfinance": yfinance or {},
                                    "correlazione": {}}), encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(percorso))


# ------------------------------------------------ trimestrali Finnhub per simbolo
def test_con_simboli_si_chiede_un_simbolo_per_volta_non_la_lista_globale(monkeypatch):
    def gestore(url, params):
        assert "symbol" in params, "la richiesta globale (troncata a 1500) non va usata"
        return _R(200, {"earningsCalendar": [{"symbol": params["symbol"], "date": "2026-10-27", "quarter": 3}]})
    chiamate = _trasporto(monkeypatch, gestore)

    out = finnhub_news.fetch_earnings_calendar(days_ahead=60, symbols=["AZZT", "KPA"])

    assert sorted(e["symbol"] for e in out) == ["AZZT", "KPA"]
    assert sorted(p["symbol"] for _, p in chiamate) == ["AZZT", "KPA"]


def test_l_evento_appartiene_al_simbolo_chiesto_anche_se_torna_la_quotazione_primaria(monkeypatch):
    primarie = {"KPA": "KAPPA.PA", "OMG": "OMEGA.MI"}
    _trasporto(monkeypatch, lambda url, params: _R(200, {"earningsCalendar": [
        {"symbol": primarie[params["symbol"]], "date": "2026-10-29", "quarter": 3}]}))

    out = finnhub_news.fetch_earnings_calendar(days_ahead=60, symbols=["KPA", "OMG"])

    assert sorted((e["symbol"], e["listed_symbol"]) for e in out) == [("KPA", "KAPPA.PA"), ("OMG", "OMEGA.MI")]


def test_la_risposta_riuscita_resta_in_cache_e_l_errore_no(monkeypatch):
    stato = {"codice": 429}

    def gestore(url, params):
        if stato["codice"] != 200:
            return _R(stato["codice"], text="rate limited")
        return _R(200, {"earningsCalendar": [{"symbol": "AZZT", "date": "2026-10-27"}]})
    chiamate = _trasporto(monkeypatch, gestore)

    motivo = []
    assert finnhub_news.fetch_earnings_calendar(days_ahead=30, symbols=["AZZT"], motivo=motivo) == []
    assert motivo and "429" in motivo[0]
    stato["codice"] = 200
    assert len(finnhub_news.fetch_earnings_calendar(days_ahead=30, symbols=["AZZT"])) == 1
    assert len(finnhub_news.fetch_earnings_calendar(days_ahead=30, symbols=["AZZT"])) == 1
    assert len(chiamate) == 2, "l'errore si riprova, il successo resta in cache"


# ------------------------------------------------ ripartizione Finnhub / yfinance
def test_titoli_senza_adr_vanno_alla_riserva_yfinance_senza_nota_di_alias(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    _alias(tmp_path, monkeypatch, finnhub={"OMEGA.DE": "OMG", "GAMMA.MI": "GAMMA.MI"})
    negozio = {"origine": "fixture", "motivo": None, "veicoli": {
        "OMEGA.DE": {"tipo": "operating"}, "GAMMA.MI": {"tipo": "operating"},
        "ACME.MI": {"tipo": "bank"}, "ZZETF": {"tipo": "etf"}, "ALFA": {"tipo": "operating"}}}
    posizioni = [{"ticker": t} for t in ("OMEGA.DE", "GAMMA.MI", "ACME.MI", "ZZETF", "ALFA")]

    finnhub, riserva, nota = bellomberg_api._tickers_earnings_con_riserva(posizioni, negozio)

    assert finnhub == ["OMEGA.DE", "ALFA"]
    assert riserva == ["GAMMA.MI", "ACME.MI"], "alias con suffisso o assente: yfinance, non scarto"
    assert nota is None


def test_endpoint_unisce_finnhub_e_yfinance_con_ticker_fonte_e_finestra_dedicata(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import classificazione, memory_db
    oggi = date.today()
    _alias(tmp_path, monkeypatch, finnhub={"OMEGA.DE": "OMG"}, yfinance={"LAMBDA.FRA": "LAMBDA.F"})
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"positions": [{"ticker": "OMEGA.DE"}, {"ticker": "LAMBDA.FRA"}]}})())
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {"origine": "fixture", "motivo": None, "veicoli": {
        "OMEGA.DE": {"tipo": "operating"}, "LAMBDA.FRA": {"tipo": "operating"}}})
    _trasporto(monkeypatch, lambda url, params: _R(200, {"earningsCalendar": [
        {"symbol": "OMG", "date": (oggi + timedelta(days=21)).isoformat(), "quarter": 3, "year": 2026, "hour": "bmo"}]}))
    letti = []

    def calendario(simbolo):
        letti.append(simbolo)
        return {"Earnings Date": [oggi + timedelta(days=41), oggi + timedelta(days=45)]}
    monkeypatch.setattr(earnings_yf, "_leggi_calendario", calendario)

    corto = bellomberg_api.get_economic_calendar(days_ahead=30)
    lungo = bellomberg_api.get_economic_calendar(days_ahead=30, earnings_days_ahead=60)

    trim = lambda out: {e["ticker"]: e for e in out["items"] if e.get("type") == "Earnings"}
    assert set(trim(corto)) == {"OMEGA.DE"}, "a 30 giorni la trimestrale a +41 resta fuori"
    eventi = trim(lungo)
    assert eventi["OMEGA.DE"]["source"] == "finnhub" and eventi["OMEGA.DE"]["title"].startswith("OMEGA.DE Earnings")
    assert eventi["LAMBDA.FRA"]["source"] == "yfinance" and eventi["LAMBDA.FRA"]["date_estimated"] is True
    assert letti and set(letti) == {"LAMBDA.F"}, "yfinance usa l'alias dichiarato"
    assert not any("alias Finnhub mancante" in v for v in (lungo["fonti_mute"] or {}).values())


# ------------------------------------------------ yfinance
@pytest.mark.parametrize("date_grezze, attesa", [
    ([date(2026, 10, 30)], {"date": "2026-10-30", "stimata": False}),
    ([date(2026, 11, 3), date(2026, 11, 7)], {"date": "2026-11-03", "stimata": True}),
    ([date(2026, 7, 28)], None),
    ([], None),
])
def test_yfinance_prossima_trimestrale(monkeypatch, date_grezze, attesa):
    monkeypatch.setattr(earnings_yf, "_leggi_calendario", lambda s: {"Earnings Date": date_grezze})
    assert earnings_yf.prossima_trimestrale("X.MI", date(2026, 10, 2)) == attesa


def test_yfinance_che_cade_lo_dichiara_e_non_resta_in_cache(monkeypatch):
    def guasto(simbolo):
        raise ConnectionError("rete giu'")
    monkeypatch.setattr(earnings_yf, "_leggi_calendario", guasto)
    motivo = []
    assert earnings_yf.prossima_trimestrale("X.MI", date(2026, 10, 2), motivo=motivo) is None
    assert motivo and "ConnectionError" in motivo[0]
    monkeypatch.setattr(earnings_yf, "_leggi_calendario", lambda s: {"Earnings Date": [date(2026, 10, 30)]})
    assert earnings_yf.prossima_trimestrale("X.MI", date(2026, 10, 2))["date"] == "2026-10-30"


# ------------------------------------------------ loghi
PNG = b"\x89PNG\r\n\x1a\nfinto"


@pytest.fixture
def cartella_dati(tmp_path, monkeypatch):
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "dati")
    # profilo Yahoo finto: nessuna rete; i test del sito lo sostituiscono
    monkeypatch.setattr(loghi_titoli, "_sito_da_yahoo", lambda ticker: None)
    return tmp_path / "dati"


def test_logo_scaricato_una_volta_tramite_l_adr_dichiarato(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch, finnhub={"ACME.MI": "ACMPY"})

    def gestore(url, params):
        if url.endswith("/stock/profile2"):
            assert params["symbol"] == "ACMPY"
            return _R(200, {"name": "Acme SpA", "logo": "https://static2.finnhub.io/logo/ACMPY.png"})
        return _R(200, content=PNG, content_type="image/png")
    chiamate = _trasporto(monkeypatch, gestore)

    primo = loghi_titoli.loghi(["acme.mi"])
    secondo = loghi_titoli.loghi(["ACME.MI"])

    attesa = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert primo["logos"] == {"ACME.MI": attesa} and secondo["logos"] == {"ACME.MI": attesa}
    assert len(chiamate) == 2, "profilo + immagine, poi solo il disco"
    assert (cartella_dati / "loghi" / "ACME.MI.png").read_bytes() == PNG


def test_titolo_europeo_senza_adr_non_tocca_la_rete_e_si_ricorda(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    chiamate = _trasporto(monkeypatch, lambda url, params: pytest.fail("nessuna rete senza simbolo USA"))

    out = loghi_titoli.loghi(["GAMMA.MI"], oggi=date(2026, 10, 2))

    assert out["logos"] == {"GAMMA.MI": None} and "simbolo USA" in out["motivi"]["GAMMA.MI"]
    assert chiamate == []
    assenti = json.loads((cartella_dati / "loghi" / "_assenti.json").read_text())
    assert assenti["GAMMA.MI"]["il"] == "2026-10-02"


def test_un_guasto_di_passaggio_non_si_ricorda(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    stato = {"codice": 429}

    def gestore(url, params):
        if url.endswith("/stock/profile2"):
            return _R(stato["codice"], {"logo": "https://static2.finnhub.io/logo/ALFA.png"}, text="rate limited")
        return _R(200, content=PNG, content_type="image/png")
    _trasporto(monkeypatch, gestore)

    assert loghi_titoli.loghi(["ALFA"])["logos"]["ALFA"] is None
    stato["codice"] = 200
    assert loghi_titoli.loghi(["ALFA"])["logos"]["ALFA"].startswith("data:image/png")


@pytest.mark.parametrize("url", ["http://static2.finnhub.io/x.png", "https://evil.example/x.png",
                                 "https://finnhub.io.evil.example/x.png"])
def test_il_logo_si_scarica_solo_da_finnhub_in_https(tmp_path, monkeypatch, cartella_dati, url):
    _alias(tmp_path, monkeypatch)

    def gestore(u, params):
        if u.endswith("/stock/profile2"):
            return _R(200, {"logo": url})
        pytest.fail("download da un host non ammesso: %s" % u)
    _trasporto(monkeypatch, gestore)

    out = loghi_titoli.loghi(["ALFA"])
    assert out["logos"]["ALFA"] is None and "host" in out["motivi"]["ALFA"]


def test_contenuto_non_immagine_o_troppo_grande_e_scartato(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    risposte = iter([_R(200, content=b"<html>", content_type="text/html"),
                     _R(200, content=b"x" * (loghi_titoli.MAX_BYTE + 1), content_type="image/png")])

    def gestore(url, params):
        if url.endswith("/stock/profile2"):
            return _R(200, {"logo": "https://static2.finnhub.io/logo/A.png"})
        return next(risposte)
    _trasporto(monkeypatch, gestore)

    assert loghi_titoli.loghi(["ALFA"])["logos"]["ALFA"] is None
    assert loghi_titoli.loghi(["BETA"])["logos"]["BETA"] is None
    assert not list((cartella_dati / "loghi").glob("*.png"))


# ------------------------------------------------ loghi dal sito dell'emittente (04/10/2026)
def test_senza_logo_finnhub_si_usa_il_sito_del_profilo_senza_redirect(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    opzioni = []

    def gestore(url, params):
        if url.endswith("/stock/profile2"):
            return _R(200, {"name": "Zztest Inc", "logo": "", "weburl": "https://zztest.example/it/"})
        if url == "https://zztest.example/apple-touch-icon.png":
            return _R(404)
        if url == "https://zztest.example/favicon.ico":
            return _R(200, content=b"\x00\x00\x01\x00ico", content_type="image/x-icon")
        pytest.fail("host o percorso non ammesso: %s" % url)
    _trasporto(monkeypatch, gestore)
    originale = loghi_titoli.finnhub_news.requests.get

    def spia(url, **k):
        opzioni.append(k.get("allow_redirects"))
        return originale(url, **k)
    monkeypatch.setattr(loghi_titoli.finnhub_news.requests, "get", spia)

    out = loghi_titoli.loghi(["ZZTEST"])

    assert out["logos"]["ZZTEST"].startswith("data:image/x-icon;base64,")
    assert out["fonti"] == {"ZZTEST": "sito_emittente"} and out["motivi"] == {}
    assert False in opzioni, "il sito dell'emittente si legge senza seguire redirect"
    again = loghi_titoli.loghi(["ZZTEST"])
    assert again["fonti"] == {"ZZTEST": "sito_emittente"}


def test_titolo_estero_usa_il_sito_dal_profilo_yahoo(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    monkeypatch.setattr(loghi_titoli, "_sito_da_yahoo", lambda t: "www.acme-zz.example" if t == "ACME.MI" else None)

    def gestore(url, params):
        assert not url.endswith("/stock/profile2"), "nessun simbolo USA: Finnhub non si interroga"
        assert url.startswith("https://www.acme-zz.example/")
        return _R(200, content=PNG, content_type="image/png")
    _trasporto(monkeypatch, gestore)

    out = loghi_titoli.loghi(["ACME.MI"])

    assert out["logos"]["ACME.MI"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert out["fonti"]["ACME.MI"] == "sito_emittente"


def test_sito_senza_icona_e_assenza_dichiarata_e_ricordata(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    monkeypatch.setattr(loghi_titoli, "_sito_da_yahoo", lambda t: "https://zzb.example")
    chiamate = _trasporto(monkeypatch, lambda url, params: _R(200, content=b"<html>", content_type="text/html"))

    out = loghi_titoli.loghi(["ZZB.L"], oggi=date(2026, 10, 4))

    assert out["logos"] == {"ZZB.L": None} and "zzb.example" in out["motivi"]["ZZB.L"]
    assert "ZZB.L" not in out["fonti"]
    n = len(chiamate)
    assert loghi_titoli.loghi(["ZZB.L"], oggi=date(2026, 10, 5))["logos"]["ZZB.L"] is None
    assert len(chiamate) == n, "assenza certa: niente rete per 7 giorni"


def test_sito_irraggiungibile_non_si_ricorda(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    monkeypatch.setattr(loghi_titoli, "_sito_da_yahoo", lambda t: "zzc.example")
    stato = {"giu": True}

    def gestore(url, params):
        if stato["giu"]:
            raise ConnectionError("rete giu'")
        return _R(200, content=PNG, content_type="image/png")
    _trasporto(monkeypatch, gestore)

    primo = loghi_titoli.loghi(["ZZC.MI"])
    assert primo["logos"]["ZZC.MI"] is None and "ConnectionError" in primo["motivi"]["ZZC.MI"]
    stato["giu"] = False
    assert loghi_titoli.loghi(["ZZC.MI"])["logos"]["ZZC.MI"].startswith("data:image/png")


@pytest.mark.parametrize("sito", [None, "", "non un sito", "javascript:alert(1)"])
def test_profilo_senza_sito_valido_dichiara_l_assenza(tmp_path, monkeypatch, cartella_dati, sito):
    _alias(tmp_path, monkeypatch)
    monkeypatch.setattr(loghi_titoli, "_sito_da_yahoo", lambda t: sito)
    _trasporto(monkeypatch, lambda url, params: pytest.fail("nessuna rete senza un sito valido: %s" % url))

    out = loghi_titoli.loghi(["ZZD.MI"])

    assert out["logos"] == {"ZZD.MI": None} and "sito" in out["motivi"]["ZZD.MI"]


# ------------------------------------------------ G8 (04/10/2026): niente titoli spariti in silenzio
def _calendario_con(monkeypatch, tickers, calendario_yf, finnhub_payload=None):
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import classificazione, memory_db
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"positions": [{"ticker": t} for t in tickers]}})())
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {"origine": "fixture", "motivo": None, "veicoli": {
        t: {"tipo": "operating"} for t in tickers}})
    _trasporto(monkeypatch, lambda url, params: _R(200, finnhub_payload or {"earningsCalendar": []}))
    monkeypatch.setattr(earnings_yf, "_leggi_calendario", calendario_yf)
    return bellomberg_api.get_economic_calendar(days_ahead=30, earnings_days_ahead=60)


def test_titolo_in_riserva_senza_data_yfinance_e_dichiarato(tmp_path, monkeypatch):
    _alias(tmp_path, monkeypatch)
    out = _calendario_con(monkeypatch, ["LAMBDA.MI"], lambda s: {})
    assert not [e for e in out["items"] if e.get("ticker") == "LAMBDA.MI"]
    muti = out["fonti_mute"] or {}
    assert "LAMBDA.MI" in muti.get("yfinance earnings", ""), muti


def test_alias_fonti_illeggibile_resta_dichiarato_con_la_riserva(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import negozi_privati
    rotto = tmp_path / "alias_fonti.json"
    rotto.write_text("{non json", encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(rotto))
    negozio = {"origine": "fixture", "motivo": None, "veicoli": {"ACME.MI": {"tipo": "operating"}}}
    _, riserva, nota = bellomberg_api._tickers_earnings_con_riserva([{"ticker": "ACME.MI"}], negozio)
    assert riserva == ["ACME.MI"]
    assert nota and "alias_fonti illeggibile" in nota
    oggi = date.today()
    out = _calendario_con(monkeypatch, ["ACME.MI"], lambda s: {"Earnings Date": [oggi + timedelta(days=10)]})
    testo = " | ".join((out["fonti_mute"] or {}).values())
    assert "alias_fonti illeggibile" in testo and "yfinance" in out["fonti_mute"].get("yfinance earnings", "")


@pytest.mark.parametrize("ticker, paese", [("ZZJ.T", "JP"), ("ZZH.HK", "HK"), ("ACME.MI", "IT"),
                                           ("ZZD.DE", "DE"), ("ZZX.QQ", "?")])
def test_paese_della_trimestrale_dal_suffisso_o_sconosciuto(tmp_path, monkeypatch, ticker, paese):
    _alias(tmp_path, monkeypatch)
    oggi = date.today()
    out = _calendario_con(monkeypatch, [ticker], lambda s: {"Earnings Date": [oggi + timedelta(days=10)]})
    evento = [e for e in out["items"] if e.get("ticker") == ticker][0]
    assert evento["country"] == paese


# ------------------------------------------------ G8: loghi, motivo vero e niente IP letterali
def test_alias_illeggibile_per_i_loghi_e_dichiarato_e_non_ricordato(tmp_path, monkeypatch, cartella_dati):
    from bellomberg.storage import negozi_privati
    rotto = tmp_path / "alias_fonti.json"
    rotto.write_text("{non json", encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(rotto))
    _trasporto(monkeypatch, lambda url, params: pytest.fail("nessuna rete senza simbolo USA"))

    out = loghi_titoli.loghi(["GAMMA.MI"], oggi=date(2026, 10, 2))

    motivo = out["motivi"]["GAMMA.MI"]
    assert "alias_fonti illeggibile" in motivo and "nessun simbolo USA dichiarato" not in motivo
    assenti = cartella_dati / "loghi" / "_assenti.json"
    assert not assenti.exists() or "GAMMA.MI" not in json.loads(assenti.read_text())


@pytest.mark.parametrize("sito", ["https://127.0.0.1", "192.168.1.10", "https://10.0.0.5/it/",
                                  "https://0x7f.1/", "https://127.1", "https://[::1]/"])
def test_sito_con_ip_letterale_e_rifiutato(tmp_path, monkeypatch, cartella_dati, sito):
    _alias(tmp_path, monkeypatch)
    monkeypatch.setattr(loghi_titoli, "_sito_da_yahoo", lambda t: sito)
    _trasporto(monkeypatch, lambda url, params: pytest.fail("download verso un IP letterale: %s" % url))

    out = loghi_titoli.loghi(["ZZIP.MI"])

    assert out["logos"] == {"ZZIP.MI": None} and "sito" in out["motivi"]["ZZIP.MI"]


# ------------------------------------------------ G8: mai l'URL con la querystring nei motivi/log
_ERRORE_CON_URL = ConnectionError("HTTPSConnectionPool(host='finnhub.io', port=443): Max retries exceeded "
                                  "with url: /api/v1/stock/profile2?symbol=ZZQ&token=CHIAVEFINTA")


def test_errore_di_rete_finnhub_non_espone_la_querystring(monkeypatch, capsys):
    def _get(url, params=None, timeout=None, **k):
        raise _ERRORE_CON_URL
    monkeypatch.setattr(finnhub_news, "REQ_OK", True)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: "CHIAVEFINTA")
    monkeypatch.setattr(finnhub_news.requests, "get", _get)
    motivo = []
    assert finnhub_news._api_get("/stock/profile2", {"symbol": "ZZQ"}, motivo=motivo) is None
    testo = " ".join(motivo) + capsys.readouterr().out
    assert "ConnectionError" in testo and "/stock/profile2" in testo
    assert "CHIAVEFINTA" not in testo and "token=" not in testo


def test_errore_yfinance_trimestrale_non_espone_la_querystring(monkeypatch):
    def guasto(simbolo):
        raise _ERRORE_CON_URL
    monkeypatch.setattr(earnings_yf, "_leggi_calendario", guasto)
    motivo = []
    earnings_yf.prossima_trimestrale("ZZQ.MI", date(2026, 10, 2), motivo=motivo)
    assert "ConnectionError" in motivo[0] and "token=" not in motivo[0]


def test_errore_di_rete_dei_loghi_non_espone_la_querystring(tmp_path, monkeypatch, cartella_dati):
    _alias(tmp_path, monkeypatch)
    monkeypatch.setattr(loghi_titoli, "_sito_da_yahoo", lambda t: "zzq.example")

    def _get(url, params=None, timeout=None, **k):
        raise _ERRORE_CON_URL
    monkeypatch.setattr(finnhub_news.requests, "get", _get)
    out = loghi_titoli.loghi(["ZZQ.MI"])
    assert "ConnectionError" in out["motivi"]["ZZQ.MI"] and "token=" not in out["motivi"]["ZZQ.MI"]


# ------------------------------------------------ G8 seguito (rilievi 3 e 5 della revisione)
def test_corpo_non_200_di_finnhub_non_porta_la_chiave(monkeypatch, capsys):
    _trasporto(monkeypatch, lambda url, params: _R(
        403, text="blocked: https://finnhub.io/api/v1/stock/profile2?symbol=ZZQ&token=chiave-finta"))
    motivo = []
    assert finnhub_news._api_get("/stock/profile2", {"symbol": "ZZQ"}, motivo=motivo) is None
    testo = " ".join(motivo) + capsys.readouterr().out
    assert "HTTP 403" in testo and "chiave-finta" not in testo and "token=***" in testo


def test_carica_alias_che_solleva_tra_due_letture_e_dichiarato(monkeypatch):
    """Il ramo `except` di `_tickers_earnings_con_riserva`: la prima lettura degli alias
    (in `_tickers_earnings_da_negozio`) riesce, la seconda solleva (file riscritto a meta')."""
    from bellomberg.api import bellomberg_api
    from bellomberg.storage import negozi_privati
    letture = []

    def carica_alias():
        letture.append(1)
        if len(letture) > 1:
            raise OSError("file in scrittura")
        return {"origine": "fixture", "motivo": None, "alias": {"finnhub": {"ACME.MI": "ACMPY"}}}
    monkeypatch.setattr(negozi_privati, "carica_alias", carica_alias)
    negozio = {"origine": "fixture", "motivo": None, "veicoli": {"ACME.MI": {"tipo": "operating"}}}

    finnhub, riserva, nota = bellomberg_api._tickers_earnings_con_riserva([{"ticker": "ACME.MI"}], negozio)

    assert len(letture) == 2 and riserva == ["ACME.MI"] and finnhub == []
    assert nota and "alias_fonti illeggibile" in nota and "OSError" in nota
