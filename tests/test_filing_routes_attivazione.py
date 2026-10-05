import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bellomberg.api.filing_routes import create_filing_router
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema

RIEPILOGO = {"attivati": ["NOVA.DE"], "da_confermare": ["KORE.DE"], "senza_fonte": [], "esclusi": [],
             "gia_attivi": [], "errori": []}


def _client(tmp_path, attivazione, monkeypatch, tickers=None, **kw):
    monkeypatch.setattr("bellomberg.storage.filing_preferenze._path", lambda p=None: tmp_path / "p.json")
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    service = FilingService(FilingStore(path), tmp_path / "archive",
                            pipeline=lambda *a, **k: {"stato": "non_disponibile", "motivi": [],
                                                      "confronto_corrente": None, "confronto_storico": None},
                            indexer=lambda *a: {"status": "skipped"})

    def auth(request: Request):
        if request.headers.get("X-BB-Token") != "test":
            raise HTTPException(401, "sessione richiesta")

    app = FastAPI()
    app.include_router(create_filing_router(
        auth, service_factory=lambda: service, attivazione=attivazione,
        tickers_portafoglio=lambda: tickers or ["NOVA.DE", "KORE.DE"],
        proponi_fn=lambda t, **k: {"ticker": t, "nome": "Nova", "sec": {"stato": "univoco", "candidati": [], "motivo": ""}}, **kw))
    client = TestClient(app)
    client.headers.update({"X-BB-Token": "test"})
    return client, service


def test_nuove_route_richiedono_sessione(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, SimpleNamespace(), monkeypatch)
    client.headers.pop("X-BB-Token")
    assert client.get("/filings/NOVA.DE/proposal").status_code == 401
    assert client.post("/filings/NOVA.DE/activate", json={}).status_code == 401
    assert client.post("/filings/activate-missing").status_code == 401
    assert client.post("/filings/NOVA.DE/exclude", json={"escluso": True}).status_code == 401


def test_activate_missing_usa_il_portafoglio_e_avvia_i_run(tmp_path, monkeypatch):
    visti, dopo = {}, []

    def attiva_mancanti(store, tickers, **k):
        visti["tickers"] = tickers
        return RIEPILOGO

    att = SimpleNamespace(attiva_mancanti=attiva_mancanti, attiva=None,
                          completa_6k=lambda store, t, **k: dopo.append(t) or {"esito": "non_applicabile"})
    client, service = _client(tmp_path, att, monkeypatch)
    r = client.post("/filings/activate-missing")
    assert r.status_code == 200 and r.json() == RIEPILOGO
    assert visti["tickers"] == ["NOVA.DE", "KORE.DE"]
    assert dopo == ["NOVA.DE"]  # in background, solo per gli attivati


def test_activate_con_cik(tmp_path, monkeypatch):
    chiamate = []

    def attiva(store, ticker, cik=None, **k):
        chiamate.append((ticker, cik))
        return {"ticker": ticker, "esito": "da_confermare", "motivo": "x"}

    client, _ = _client(tmp_path, SimpleNamespace(attiva=attiva, attiva_mancanti=None, completa_6k=None), monkeypatch)
    r = client.post("/filings/KORE.DE/activate", json={"cik": "0009990011"})
    assert r.status_code == 200 and r.json()["esito"] == "da_confermare"
    assert chiamate == [("KORE.DE", "0009990011")]


def test_activate_avvia_primo_run_in_background(tmp_path, monkeypatch):
    from bellomberg.market_data.filing_profili_auto import profilo_sec

    def attiva(store, ticker, cik=None, **k):
        store.set_profile(ticker, profilo_sec(ticker, cik="9990001", sec_ticker="NOVA", nome="Nova Inc.",
                                              origine="nome", forme={"10-Q"}), interval_hours=24)
        return {"ticker": ticker, "esito": "attivato", "profilo_versione": 1}

    att = SimpleNamespace(attiva=attiva, attiva_mancanti=None, completa_6k=lambda *a, **k: {"esito": "non_applicabile"})
    client, service = _client(tmp_path, att, monkeypatch)
    assert client.post("/filings/NOVA.DE/activate", json={}).status_code == 200
    runs = service.store.list_runs("NOVA.DE")
    assert len(runs) == 1 and runs[0]["status"] == "non_disponibile"
    assert runs[0]["trigger"] == "scheduled"  # I4: l'attivazione non e' il pulsante del giudizio AI


def test_activate_body_non_valido(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, SimpleNamespace(attiva=None, attiva_mancanti=None, completa_6k=None), monkeypatch)
    assert client.post("/filings/KORE.DE/activate", json={"cik": "abc"}).status_code == 422
    assert client.post("/filings/KORE.DE/activate", json={"cik": 123}).status_code == 422
    assert client.post("/filings/KORE.DE/activate", json={"altro": 1}).status_code == 422
    assert client.post("/filings/kore/activate", json={}).status_code == 422


def test_proposal_e_exclude(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, SimpleNamespace(attiva=None, attiva_mancanti=None, completa_6k=None), monkeypatch)
    assert client.post("/filings/NOVA.DE/exclude", json={"escluso": True}).json() == {"ticker": "NOVA.DE", "escluso": True}
    r = client.get("/filings/NOVA.DE/proposal").json()
    assert r["escluso"] is True and r["profilo_attivo"] is False and r["sec"]["stato"] == "univoco"
    assert client.post("/filings/NOVA.DE/exclude", json={"escluso": "si"}).status_code == 422


def test_preferenze_illeggibili_503(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, SimpleNamespace(attiva=None, attiva_mancanti=None, completa_6k=None), monkeypatch)
    (tmp_path / "p.json").write_text("{rotto")
    assert client.get("/filings/NOVA.DE/proposal").status_code == 503
    assert client.post("/filings/NOVA.DE/exclude", json={"escluso": True}).status_code == 503


def test_route_storiche_invariate(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, SimpleNamespace(attiva=None, attiva_mancanti=None, completa_6k=None), monkeypatch)
    assert client.get("/filings/NOVA.DE").status_code == 200


def test_verifica_ora_ritenta_la_variante_6k(tmp_path, monkeypatch):
    # Review finale: se la sonda 6-K e' fallita all'attivazione, «Verifica ora» la ritenta.
    from bellomberg.market_data.filing_profili_auto import profilo_sec
    tentati = []
    att = SimpleNamespace(attiva=None, attiva_mancanti=None,
                          completa_6k=lambda store, t, **k: tentati.append(t) or {"esito": "nessun_allegato"})
    client, service = _client(tmp_path, att, monkeypatch)
    service.store.set_profile("KORE.DE", profilo_sec("KORE.DE", cik="9990011", sec_ticker="KORE", nome="Kore Mining plc",
                                                     origine="nome", forme={"20-F"}), interval_hours=24)
    assert client.post("/filings/KORE.DE/refresh").status_code == 202
    assert tentati == ["KORE.DE"]


def _con_run(service, ticker="NOVA.DE"):
    from bellomberg.market_data.filing_profili_auto import profilo_sec
    service.store.set_profile(ticker, profilo_sec(ticker, cik="9990001", sec_ticker="NOVA", nome="Nova Inc.",
                                                  origine="nome", forme={"10-Q"}), interval_hours=24)
    run = service.store.start_run(ticker, trigger="manual")
    cit = {"url": "https://www.sec.gov/Archives/x.htm", "sha256": "a" * 64, "sezione": "rischi", "inizio": 0,
           "fine": 10, "pagine_fisiche": [], "testo": "Nuovo rischio " * 20}
    meta = lambda d: {"metadati": {"periodo_fine": d}}
    service.store.finish_run(run["id"], status="ok", result={
        "stato": "ok", "variante": "trimestrale",
        "coppia": {"prima": meta("2025-09-28"), "dopo": meta("2026-09-27")},
        "confronto_corrente": {"stato": "ok", "cambiamenti": [{"tipo": "aggiunto", "dopo": cit}]}})


def _vuoto():
    return SimpleNamespace(attiva=None, attiva_mancanti=None, completa_6k=None)


def test_panoramica_e_anteprima_coerenti(tmp_path, monkeypatch):
    client, service = _client(tmp_path, _vuoto(), monkeypatch, tickers=["NOVA.DE", "ACME.PA"],
                              ultima_run_fn=lambda: datetime(2000, 1, 1, tzinfo=timezone.utc))
    _con_run(service)
    pan = client.get("/filings").json()
    assert pan["copertura"]["totale"] == 2 and pan["copertura"]["senza_profilo"] == 1
    assert pan["copertura"]["con_confronto"] == 1 and pan["copertura"]["esclusi"] == 0
    assert pan["contesto"]["budget"] == 14_000 and pan["aggiornamento"] is None
    nova = next(t for t in pan["titoli"] if t["ticker"] == "NOVA.DE")
    assert nova["profilo"] and nova["fonte"].startswith("SEC 10-Q") and nova["ultimo_confronto"]
    assert nova["gruppo"] == 0 and nova["novita"] is True and nova["stato_riga"].startswith("NOVA.DE · ")
    acme = next(t for t in pan["titoli"] if t["ticker"] == "ACME.PA")
    assert acme["profilo"] is False and acme["ultimo_confronto"] is None and acme["novita"] is False
    prev = client.get("/filings/NOVA.DE/context-preview").json()
    assert prev["testo"].startswith("NOVA.DE · ") and prev["caratteri"] == len(prev["testo"])
    assert prev["contesto_totale"]["budget"] == 14_000 and prev["nota"]
    assert client.get("/filings/ZZZ.MI/context-preview").status_code == 404
    assert client.get("/filings/nova/context-preview").status_code == 422


def test_panoramica_esclusi_e_stato_manager(tmp_path, monkeypatch):
    stato = {"status": "idle", "interval_seconds": 3600}
    client, _ = _client(tmp_path, _vuoto(), monkeypatch, tickers=["NOVA.DE", "ACME.PA"],
                        ultima_run_fn=lambda: None, stato_aggiornamento=lambda: stato)
    assert client.post("/filings/ACME.PA/exclude", json={"escluso": True}).status_code == 200
    pan = client.get("/filings").json()
    assert pan["copertura"]["esclusi"] == 1 and pan["aggiornamento"] == stato
    assert next(t for t in pan["titoli"] if t["ticker"] == "ACME.PA")["escluso"] is True


def test_activate_missing_passa_dal_manager(tmp_path, monkeypatch):
    chiamate, seriali = [], []
    att = SimpleNamespace(attiva_mancanti=lambda store, tickers, **k: RIEPILOGO, attiva=None,
                          completa_6k=lambda store, t, **k: seriali.append(t) or {"esito": "non_applicabile"})
    client, service = _client(tmp_path, att, monkeypatch,
                              avvia_aggiornamento=lambda s: chiamate.append(s) or {"accepted": True, "job": {}})
    r = client.post("/filings/activate-missing").json()
    assert r["attivati"] == ["NOVA.DE"] and chiamate == ["activation"]
    assert seriali == ["NOVA.DE"]  # la variante 6-K resta nel background
    assert service.store.list_runs("NOVA.DE") == []  # nessun run seriale: lo fa il manager
    assert "aggiornamento" not in r


def test_manager_occupato_in_coda(tmp_path, monkeypatch):
    att = SimpleNamespace(attiva_mancanti=lambda store, tickers, **k: RIEPILOGO, attiva=None,
                          completa_6k=lambda *a, **k: {"esito": "non_applicabile"})
    client, _ = _client(tmp_path, att, monkeypatch,
                        avvia_aggiornamento=lambda s: {"accepted": False, "job": {}},
                        stato_aggiornamento=lambda: {"status": "running"})
    assert client.post("/filings/activate-missing").json()["aggiornamento"] == "in coda: subito dopo il controllo in corso"


def test_activate_singolo_passa_dal_manager(tmp_path, monkeypatch):
    chiamate = []
    att = SimpleNamespace(attiva=lambda store, t, cik=None, **k: {"ticker": t, "esito": "attivato"},
                          attiva_mancanti=None, completa_6k=lambda *a, **k: {"esito": "non_applicabile"})
    client, service = _client(tmp_path, att, monkeypatch,
                              avvia_aggiornamento=lambda s: chiamate.append(s) or {"accepted": True, "job": {}})
    assert client.post("/filings/NOVA.DE/activate", json={}).status_code == 200
    assert chiamate == ["activation"] and service.store.list_runs("NOVA.DE") == []


def test_activate_preferenze_illeggibili_503(tmp_path, monkeypatch):
    att = SimpleNamespace(attiva=lambda *a, **k: (_ for _ in ()).throw(AssertionError("non va chiamata")),
                          attiva_mancanti=None, completa_6k=None)
    client, _ = _client(tmp_path, att, monkeypatch)
    (tmp_path / "p.json").write_text("{rotto")
    assert client.post("/filings/NOVA.DE/activate", json={}).status_code == 503


def test_panoramica_ultimo_confronto_e_quello_mostrato(tmp_path, monkeypatch):
    # Revisione: un errore dopo il confronto non rietichetta la data del confronto mostrato.
    client, service = _client(tmp_path, _vuoto(), monkeypatch, tickers=["NOVA.DE"], ultima_run_fn=lambda: None)
    _con_run(service)
    r1 = service.store.list_runs("NOVA.DE", limit=1)[0]
    r2 = service.store.start_run("NOVA.DE", trigger="manual")
    service.store.finish_run(r2["id"], status="errore", reason="rete")
    nova = client.get("/filings").json()["titoli"][0]
    assert nova["ultimo_confronto"] == r1["finished_at"]


def test_panoramica_e_anteprima_costruiscono_le_schede_una_volta(tmp_path, monkeypatch):
    from bellomberg.agents import filing_context as fc
    novita = datetime(2000, 1, 1, tzinfo=timezone.utc)
    client, service = _client(tmp_path, _vuoto(), monkeypatch, tickers=["NOVA.DE", "ACME.PA"],
                              ultima_run_fn=lambda: novita)
    _con_run(service)
    # riferimento indipendente: le schede costruite direttamente dall'archivio
    attese = {s["ticker"]: s for s in fc.schede_filing(["NOVA.DE", "ACME.PA"], db_path=service.store.db_path,
                                                       novita_dopo=novita)}
    chiamate, confronti = [], []
    originale = fc.schede_filing
    monkeypatch.setattr(fc, "schede_filing", lambda *a, **k: chiamate.append(1) or originale(*a, **k))
    conf = type(service.store).confronto_ed_errore
    monkeypatch.setattr(type(service.store), "confronto_ed_errore",
                        lambda self, t: confronti.append(t) or conf(self, t))
    pan = client.get("/filings").json()
    assert len(chiamate) == 1 and sorted(confronti) == ["ACME.PA", "NOVA.DE"]
    for t in pan["titoli"]:
        s = attese[t["ticker"]]
        assert (t["gruppo"], t["stato_riga"], t["fonte"], t["ultimo_confronto"], t["run_id"]) == (
            s["gruppo"], s["stato"], s.get("fonte"), s.get("confronto_at"), s.get("run_id"))
    chiamate.clear(), confronti.clear()
    assert client.get("/filings/NOVA.DE/context-preview").status_code == 200
    assert len(chiamate) == 1 and len(confronti) == 2


def test_contesto_dettaglio_restituisce_le_schede(tmp_path):
    from bellomberg.agents import filing_context as fc
    out = fc.contesto_dettaglio(["NOVA.DE", "NOVA.DE"], db_path=tmp_path / "manca.sqlite")
    assert [s["ticker"] for s in out["schede"]] == ["NOVA.DE"]


def test_overview_con_contesto_fn_senza_schede_usa_le_schede_dell_archivio(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, SimpleNamespace(), monkeypatch, contesto_fn=lambda tickers, **k: {"caratteri": 0, "budget": 1, "omessi_totali": 0, "righe": {}},
                        ultima_run_fn=lambda: None)
    r = client.get("/filings")
    assert r.status_code == 200, r.text
    assert [t["ticker"] for t in r.json()["titoli"]] == ["NOVA.DE", "KORE.DE"]


def test_activate_con_lei(tmp_path, monkeypatch):
    """Fase B: conferma del LEI con un clic; CIK e LEI insieme o LEI malformato = 422."""
    chiamate = []

    def attiva(store, ticker, cik=None, lei=None, **k):
        chiamate.append((ticker, cik, lei))
        return {"ticker": ticker, "esito": "da_confermare", "motivo": "x"}

    client, _ = _client(tmp_path, SimpleNamespace(attiva=attiva, attiva_mancanti=None, completa_6k=None), monkeypatch)
    r = client.post("/filings/NOVA.MI/activate", json={"lei": "999900NOVA0000000001"})
    assert r.status_code == 200 and chiamate == [("NOVA.MI", None, "999900NOVA0000000001")]
    assert client.post("/filings/NOVA.MI/activate", json={"lei": "999900nova0000000001"}).status_code == 422
    assert client.post("/filings/NOVA.MI/activate", json={"lei": "123"}).status_code == 422
    assert client.post("/filings/NOVA.MI/activate", json={"lei": 1}).status_code == 422
    assert client.post("/filings/NOVA.MI/activate",
                       json={"cik": "0009990001", "lei": "999900NOVA0000000001"}).status_code == 422
    assert len(chiamate) == 1
