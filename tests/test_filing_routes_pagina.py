"""Fase E, Task 1-2: panoramica per la pagina Filing ed endpoint nuovi (dati sintetici, nessuna rete)."""
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bellomberg.api.filing_routes import create_filing_router
from bellomberg.market_data import filing_proposta_ai as fp
from bellomberg.market_data.filing_profili_auto import profilo_esef, profilo_sec
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage import filing_preferenze as fpref
from bellomberg.storage.filing_store import FilingStore, ensure_schema

LEI_ORSA = "999900ORSA0000000001"
URL_IR = "https://ir.acme.example/h1-2026.pdf"
SHA_A, SHA_B = "a" * 64, "b" * 64


def _proponi_vietata(*a, **k):
    raise AssertionError("nessuna proposta di collegamento (rete) dalla panoramica")


@pytest.fixture
def env(tmp_path, monkeypatch):
    pref = tmp_path / "p.json"
    monkeypatch.setattr("bellomberg.storage.filing_preferenze._path", lambda p=None: pref)
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    service = FilingService(FilingStore(path), tmp_path / "archive",
                            pipeline=lambda *a, **k: {"stato": "non_disponibile", "motivi": [],
                                                      "confronto_corrente": None, "confronto_storico": None},
                            indexer=lambda *a: {"status": "skipped"})
    stato = {"status": "idle", "auto_enabled": True, "auto_forzato_spento": False,
             "next_run_at": "2026-10-03T12:00:00", "finished_at": "2026-10-03T11:00:00"}
    ns = NS(service=service, pref=pref, stato=stato, attivazione=NS(attiva=None, attiva_mancanti=None,
                                                                    completa_6k=lambda *a, **k: {"esito": "x"}),
            proponi=_proponi_vietata, auto=[], tmp_path=tmp_path)

    def auth(request: Request):
        if request.headers.get("X-BB-Token") != "test":
            raise HTTPException(401, "sessione richiesta")

    def client(**kw):
        def imposta(attivo):
            ns.auto.append(attivo)
            if attivo and stato.get("auto_forzato_spento"):
                raise ValueError("env")
            stato["auto_enabled"] = attivo
            return dict(stato)

        opzioni = dict(service_factory=lambda: service, attivazione=ns.attivazione,
                       tickers_portafoglio=lambda: ["NOVA.DE", "ACME.PA"],
                       proponi_fn=lambda t, **k: ns.proponi(t, **k),
                       ultima_run_fn=lambda: datetime(2000, 1, 1, tzinfo=timezone.utc),
                       stato_aggiornamento=lambda: dict(stato), imposta_auto=imposta,
                       nomi_titoli_fn=lambda: {"NOVA.DE": "Nova AG"},
                       tickers_preferiti=lambda: [("NOVA.DE", "Nova AG"), ("ORSA.MI", "Orsa SpA"), "kore.mi"])
        opzioni.update(kw)
        app = FastAPI()
        app.include_router(create_filing_router(auth, **opzioni))
        c = TestClient(app)
        c.headers.update({"X-BB-Token": "test"})
        return c

    ns.client = client
    return ns


def _profilo_sec(service, ticker="NOVA.DE", origine="nome"):
    return service.store.set_profile(ticker, profilo_sec(ticker, cik="9990001", sec_ticker="NOVA", nome="Nova AG",
                                                         origine=origine, forme={"10-Q"}), interval_hours=24)


def _citazione(testo, sezione="rischi"):
    return {"url": "https://www.sec.gov/Archives/x.htm", "sha256": "c" * 64, "sezione": sezione, "inizio": 0,
            "fine": 10, "pagine_fisiche": [], "testo": testo}


def _confronto(service, ticker="NOVA.DE", cambiamenti=None):
    run = service.store.start_run(ticker, trigger="manual")
    meta = lambda d: {"metadati": {"periodo_fine": d}, "sha256": d * 2}
    cambiamenti = cambiamenti if cambiamenti is not None else [
        {"tipo": "aggiunto", "dopo": _citazione("Nuovo rischio di credito " * 20)}]
    return service.store.finish_run(run["id"], status="ok", result={
        "stato": "ok", "variante": "trimestrale",
        "coppia": {"prima": meta("2025-09-28"), "dopo": meta("2026-09-27")},
        "confronto_corrente": {"stato": "ok", "cambiamenti": cambiamenti}})


def _titolo(r, ticker):
    return next(t for t in r["titoli"] if t["ticker"] == ticker)


def _proposta_in_cache(ticker="ACME.PA", sha=SHA_A, url=URL_IR, creata_il="2026-10-02T10:00:00+00:00",
                       versione=None, fallita=False):
    dati = {"sha256": sha, "url": url, "ticker": ticker, "nome": "Acme SA", "tipo": "semestrale", "lingua": "fr",
            "periodo": "2026-06-30", "verificate": ["rischi", "gestione"], "scartate": [], "salvabile": True,
            "motivi": [], "avvisi": [], "regole": {}, "altri": [], "modello": "nova/flash", "costo_eur": 0.002,
            "costo_usd": 0.003, "creata_il": creata_il,
            "versione_verifica": fp.VERSIONE_VERIFICA if versione is None else versione, "proposta": {}}
    if fallita:
        dati = {"sha256": sha, "ticker": ticker, "url": url, "verificate": [], "fallita": True, "dettaglio": "x"}
    fp._salva(dati)
    return dati


# ------------------------------------------------------------------------------- Task 1: panoramica

def test_panoramica_campi_nuovi(env):
    _profilo_sec(env.service)
    _confronto(env.service)
    r = env.client().get("/filings").json()
    assert r["ambito"] == "portafoglio"
    assert r["controllo_giornaliero"] == {"attivo": True, "forzato_spento_da_env": False,
                                          "prossimo_at": "2026-10-03T12:00:00", "ultimo_fine_at": "2026-10-03T11:00:00",
                                          "errore_configurazione": None}
    nova = _titolo(r, "NOVA.DE")
    assert (nova["stato"], nova["gruppo_ui"], nova["nome"]) == ("novita", "novita", "Nova AG")
    assert nova["documento"] == "SEC 10-Q" and nova["cambiamenti"] == 1 and nova["nel_contesto"] is True
    assert nova["run_attivo"] is None and nova["ultimo_errore"] is None and nova["attivazione"] is None
    assert nova["proposta_ai"] is None and nova["prossimo_at"]
    # campi di prima invariati
    assert nova["gruppo"] == 0 and nova["novita"] is True and nova["profilo"] is True
    acme = _titolo(r, "ACME.PA")
    assert (acme["stato"], acme["gruppo_ui"], acme["documento"], acme["nome"]) == (
        "non_attivo", "senza_fonte", None, None)
    assert acme["prossimo_at"] is None and acme["cambiamenti"] == 0
    # fase F: la freschezza della riga arriva alla pagina (None senza profilo)
    assert nova["freschezza"] in ("aggiornato", "non_aggiornato") and acme["freschezza"] is None


def test_panoramica_senza_manager(env):
    r = env.client(stato_aggiornamento=None).get("/filings").json()
    assert r["aggiornamento"] is None
    assert r["controllo_giornaliero"] == {"attivo": False, "forzato_spento_da_env": False, "prossimo_at": None,
                                          "ultimo_fine_at": None, "errore_configurazione": None}


def test_ambito_non_valido_422_e_preferenze_illeggibili_503(env):
    c = env.client()
    assert c.get("/filings?ambito=tutti").status_code == 422
    env.pref.write_text("{rotto")
    assert c.get("/filings").status_code == 503
    assert c.get("/filings?ambito=preferiti").status_code == 503
    assert env.pref.read_text() == "{rotto"


def test_preferiti_meno_portafoglio_e_contesto_del_portafoglio(env):
    env.service.store.set_profile("ORSA.MI", profilo_esef("ORSA.MI", lei=LEI_ORSA, nome="Orsa SpA", origine="nome",
                                                          lingua="it"), interval_hours=24)
    c = env.client()
    pf = c.get("/filings").json()
    r = c.get("/filings?ambito=preferiti").json()
    assert r["ambito"] == "preferiti" and [t["ticker"] for t in r["titoli"]] == ["ORSA.MI", "KORE.MI"]
    assert r["contesto"] == pf["contesto"] and r["copertura"]["totale"] == 2
    orsa = _titolo(r, "ORSA.MI")
    assert orsa["nel_contesto"] is False and orsa["nome"] == "Orsa SpA"
    assert (orsa["stato"], orsa["documento"]) in (("primo_confronto", "ESEF annuale"),
                                                  ("primo_confronto", "ESEF annual"))
    assert _titolo(r, "KORE.MI")["stato"] == "non_attivo"


def test_preferiti_illeggibili_elenco_vuoto(env):
    def rotti():
        raise sqlite3.OperationalError("no such table: favorite_companies")
    r = env.client(tickers_preferiti=rotti).get("/filings?ambito=preferiti")
    assert r.status_code == 200 and r.json()["titoli"] == []


def test_esito_di_activate_salvato_e_mostrato_senza_rete(env):
    proposta = {"sec": {"stato": "ambiguo", "candidati": [{"cik": "0009990011"}, {"cik": "0009990012"}]}}
    env.attivazione.attiva = lambda store, t, cik=None, **k: {"ticker": t, "esito": "da_confermare",
                                                             "motivo": "2 emittenti", "proposta": proposta}
    c = env.client()
    assert c.post("/filings/ACME.PA/activate", json={}).status_code == 200
    acme = _titolo(c.get("/filings").json(), "ACME.PA")  # proponi_fn vietata: nessuna rete
    assert (acme["stato"], acme["gruppo_ui"]) == ("da_confermare", "da_sistemare")
    assert acme["attivazione"]["esito"] == "da_confermare" and acme["attivazione"]["candidati"] == 2
    assert acme["attivazione"]["motivo"] == "2 emittenti" and acme["attivazione"]["at"]


def test_esiti_di_activate_missing(env):
    env.attivazione.attiva_mancanti = lambda store, tickers, **k: {
        "attivati": [], "da_confermare": [], "senza_fonte": ["ACME.PA"], "esclusi": [], "gia_attivi": ["NOVA.DE"],
        "scollegati": [], "errori": [{"ticker": "KORE.MI", "motivo": "rete giu'"}]}
    c = env.client()
    assert c.post("/filings/activate-missing").status_code == 200
    esiti = fpref.esiti(fpref.carica(env.pref))
    assert esiti["ACME.PA"]["esito"] == "senza_fonte" and "NOVA.DE" not in esiti
    assert esiti["KORE.MI"] == {**esiti["KORE.MI"], "esito": "errore", "motivo": "rete giu'"}
    assert _titolo(c.get("/filings").json(), "ACME.PA")["stato"] == "senza_fonte"


def test_run_attivo_e_in_corso(env):
    _profilo_sec(env.service)
    _confronto(env.service)
    run = env.service.store.start_run("NOVA.DE", trigger="manual")
    nova = _titolo(env.client().get("/filings").json(), "NOVA.DE")
    assert nova["stato"] == "in_corso" and nova["run_attivo"] == {"id": run["id"], "started_at": run["started_at"],
                                                                  "trigger": "manual"}


def test_errore_dopo_un_confronto_valido(env):
    _profilo_sec(env.service)
    _confronto(env.service)
    r2 = env.service.store.start_run("NOVA.DE", trigger="manual")
    env.service.store.finish_run(r2["id"], status="errore", reason="HTTPError: 503")
    nova = _titolo(env.client(ultima_run_fn=lambda: datetime.now(timezone.utc)).get("/filings").json(), "NOVA.DE")
    assert nova["stato"] == "errore" and nova["ultimo_errore"]["reason"] == "HTTPError: 503"
    assert nova["ultimo_errore"]["at"] and nova["cambiamenti"] == 1


def test_primo_confronto_in_errore_e_in_attesa(env):
    _profilo_sec(env.service)
    c = env.client()
    attesa = _titolo(c.get("/filings").json(), "NOVA.DE")
    assert attesa["stato"] == "primo_confronto" and attesa["freschezza"] == "senza_confronto"
    r = env.service.store.start_run("NOVA.DE", trigger="manual")
    env.service.store.finish_run(r["id"], status="errore", reason="sezione non trovata: rischi")
    nova = _titolo(c.get("/filings").json(), "NOVA.DE")
    assert nova["stato"] == "errore" and "sezione non trovata" in nova["ultimo_errore"]["reason"]


def test_escluso_con_profilo(env):
    _profilo_sec(env.service)
    _confronto(env.service)
    fpref.imposta_escluso("NOVA.DE", True, path=env.pref)
    nova = _titolo(env.client().get("/filings").json(), "NOVA.DE")
    assert (nova["stato"], nova["gruppo_ui"]) == ("escluso", "senza_fonte")


def test_proposta_ai_in_sospeso_poi_scartata(env):
    _proposta_in_cache(sha=SHA_A, creata_il="2026-10-01T10:00:00+00:00")
    _proposta_in_cache(sha=SHA_B, creata_il="2026-10-02T10:00:00+00:00")
    _proposta_in_cache(sha="d" * 64, creata_il="2026-10-09T10:00:00+00:00", fallita=True)  # segnaposto: ignorato
    _proposta_in_cache(ticker="ORSA.MI", sha="e" * 64, creata_il="2026-10-09T10:00:00+00:00")
    c = env.client()
    acme = _titolo(c.get("/filings").json(), "ACME.PA")
    assert acme["stato"] == "proposta_ai" and acme["proposta_ai"] == {
        "sha256": SHA_B, "url": URL_IR, "at": "2026-10-02T10:00:00+00:00", "salvabile": True, "verificate": 2}
    assert c.post("/filings/ACME.PA/ai-proposal/discard", json={"sha256": SHA_B}).json() == {
        "ticker": "ACME.PA", "sha256": SHA_B, "scartata": True}
    assert _titolo(c.get("/filings").json(), "ACME.PA")["proposta_ai"]["sha256"] == SHA_A
    c.post("/filings/ACME.PA/ai-proposal/discard", json={"sha256": SHA_A})
    acme = _titolo(c.get("/filings").json(), "ACME.PA")
    assert acme["proposta_ai"] is None and acme["stato"] == "non_attivo"


def test_proposta_ai_accettata_non_in_sospeso(env):
    _proposta_in_cache(ticker="NOVA.DE")
    p = profilo_esef("NOVA.DE", lei=LEI_ORSA, nome="Nova AG", origine="nome", lingua="it")
    variante = {"ticker": "NOVA.DE", "fonti": ["ir"], "tipo": "semestrale", "ir_urls": [URL_IR], "lingua": "it",
                "sezioni": {"rischi": {"inizio": "Rischi", "fine": "Prospettive"}},
                "verifica": {"lingua": "il", "tipo": "semestrale", "perimetro": "consolidat"}}
    env.service.store.set_profile("NOVA.DE", fp.variante_ir(p, variante), interval_hours=24)
    nova = _titolo(env.client().get("/filings").json(), "NOVA.DE")
    assert nova["proposta_ai"] is None and nova["stato"] == "primo_confronto"
    assert nova["documento"] in ("ESEF annuale + IR semestrale", "ESEF annual + IR half-year")


# ------------------------------------------------------------------------------- Task 2: badge

def test_novita_badge_senza_testo_del_contesto(env, monkeypatch):
    from bellomberg.agents import filing_context as fc
    _profilo_sec(env.service)
    _confronto(env.service)

    def vietata(*a, **k):
        raise AssertionError("il badge non costruisce il testo del contesto")

    monkeypatch.setattr(fc, "contesto_dettaglio", vietata)
    monkeypatch.setattr(fc, "impagina", vietata)
    r = env.client().get("/filings/novita")
    assert r.status_code == 200 and r.json() == {"n": 1, "tickers": ["NOVA.DE"]}
    nessuna = env.client(ultima_run_fn=lambda: datetime.now(timezone.utc)).get("/filings/novita").json()
    assert nessuna == {"n": 0, "tickers": []}


def test_novita_richiede_sessione(env):
    c = env.client()
    c.headers.pop("X-BB-Token")
    for metodo, url, body in (("get", "/filings/novita", None), ("put", "/filings/auto-refresh", {"attivo": True}),
                              ("post", "/filings/NOVA.DE/reject", {"cik": "1"}), ("post", "/filings/NOVA.DE/unlink", {}),
                              ("get", "/filings/NOVA.DE/ai-proposal", None),
                              ("post", "/filings/NOVA.DE/ai-proposal/discard", {"sha256": SHA_A})):
        kw = {"json": body} if body is not None else {}
        assert getattr(c, metodo)(url, **kw).status_code == 401


# ------------------------------------------------------------------------------- interruttore

def test_auto_refresh(env):
    c = env.client()
    for body in ({}, {"attivo": "si"}, {"attivo": 1}, {"attivo": True, "altro": 1}, []):
        assert c.put("/filings/auto-refresh", json=body).status_code == 422
    r = c.put("/filings/auto-refresh", json={"attivo": False})
    assert r.status_code == 200 and r.json()["attivo"] is False and r.json()["controllo_giornaliero"]["attivo"] is False
    assert fpref.controllo_giornaliero(env.pref) is False and env.auto == [False]
    assert c.put("/filings/auto-refresh", json={"attivo": True}).json()["controllo_giornaliero"]["attivo"] is True
    assert fpref.controllo_giornaliero(env.pref) is True


def test_auto_refresh_env_409_e_senza_manager_503(env):
    env.stato["auto_forzato_spento"] = True
    c = env.client()
    r = c.put("/filings/auto-refresh", json={"attivo": True})
    assert r.status_code == 409 and "FILING_AUTO_REFRESH_ENABLED" in r.json()["detail"]
    assert fpref.controllo_giornaliero(env.pref) is None  # nulla salvato
    assert env.client(imposta_auto=None).put("/filings/auto-refresh", json={"attivo": True}).status_code == 503
    env.pref.write_text("{rotto")
    env.stato["auto_forzato_spento"] = False
    assert c.put("/filings/auto-refresh", json={"attivo": False}).status_code == 503
    assert env.pref.read_text() == "{rotto"


def test_auto_refresh_scrittura_fallita_ripristina_il_runtime(env, monkeypatch):
    """Revisione finale: se la scelta non si salva, il runtime torna com'era (nessuna divergenza)."""
    from bellomberg.storage import filing_preferenze

    def _rotta(_attivo):
        raise OSError("disco pieno (sintetico)")
    monkeypatch.setattr(filing_preferenze, "imposta_controllo_giornaliero", _rotta)
    r = env.client().put("/filings/auto-refresh", json={"attivo": True})
    assert r.status_code == 503
    assert env.auto == [True, False]


def test_auto_refresh_col_manager_vero(env, monkeypatch):
    from bellomberg.market_data.filing_refresh import FilingRefreshManager
    monkeypatch.setenv("FILING_AUTO_REFRESH_ENABLED", "false")
    m = FilingRefreshManager(lambda: env.service)
    c = env.client(imposta_auto=m.imposta_auto, stato_aggiornamento=m.status)
    assert c.put("/filings/auto-refresh", json={"attivo": True}).status_code == 409
    r = c.get("/filings").json()["controllo_giornaliero"]
    assert r["attivo"] is False and r["forzato_spento_da_env"] is True


# ------------------------------------------------------------------------------- reject / unlink

def test_reject_cik_e_lei(env):
    visti = []

    def proponi(t, rifiutati=frozenset(), rifiutati_lei=frozenset()):
        visti.append((set(rifiutati), set(rifiutati_lei)))
        return {"ticker": t, "sec": {"stato": "ambiguo", "candidati": [{"cik": "0009990011"}, {"cik": "0009990012"}],
                                     "motivo": ""},
                "esef": {"stato": "nessuno", "candidati": [], "motivo": ""}}

    env.proponi = proponi
    c = env.client()
    r = c.post("/filings/KORE.MI/reject", json={"cik": "9990011"}).json()
    assert r["ticker"] == "KORE.MI" and r["esito"] == "da_confermare"
    # il rifiutato e' tolto anche se la proposta non lo filtra
    assert [x["cik"] for x in r["proposta"]["sec"]["candidati"]] == ["0009990012"]
    assert visti[-1] == ({"0009990011"}, set())
    r = c.post("/filings/KORE.MI/reject", json={"cik": ["0009990012"]}).json()
    assert r["esito"] == "senza_fonte" and r["proposta"]["sec"]["candidati"] == []
    r = c.post("/filings/KORE.MI/reject", json={"lei": [LEI_ORSA]}).json()
    assert visti[-1][1] == {LEI_ORSA}
    pref = fpref.carica(env.pref)
    assert pref["rifiutati"]["KORE.MI"] == ["0009990011", "0009990012"]
    assert fpref.rifiutati_lei(pref, "KORE.MI") == {LEI_ORSA}
    assert fpref.esiti(pref)["KORE.MI"]["esito"] == "senza_fonte"


def test_reject_422_e_503(env):
    c = env.client()
    for body in ({}, {"cik": "abc"}, {"lei": "123"}, {"lei": LEI_ORSA.lower()}, {"cik": "1", "lei": LEI_ORSA},
                 {"cik": []}, {"cik": [1]}, {"altro": "1"}, {"cik": "1" * 11}):
        assert c.post("/filings/KORE.MI/reject", json=body).status_code == 422, body
    env.pref.write_text("{rotto")
    assert c.post("/filings/KORE.MI/reject", json={"cik": "1"}).status_code == 503


def test_proposal_passa_i_lei_rifiutati(env):
    fpref.rifiuta_lei("KORE.MI", LEI_ORSA, path=env.pref)
    visti = {}
    env.proponi = lambda t, **k: visti.update(k) or {"ticker": t, "sec": {"stato": "nessuno", "candidati": []}}
    assert env.client().get("/filings/KORE.MI/proposal").status_code == 200
    assert visti["rifiutati_lei"] == {LEI_ORSA}


def test_unlink_profilo_automatico(env):
    v1 = _profilo_sec(env.service)
    _confronto(env.service)
    c = env.client()
    r = c.post("/filings/NOVA.DE/unlink", json={})
    assert r.status_code == 200 and r.json() == {"ticker": "NOVA.DE", "esito": "scollegato", "versione": 2}
    nuovo = env.service.store.get_profile("NOVA.DE")
    assert nuovo["enabled"] is False and nuovo["profile"] == v1["profile"] and nuovo["version"] == 2
    pref = fpref.carica(env.pref)
    assert pref["rifiutati"]["NOVA.DE"] == ["0009990001"] and fpref.scollegati(pref) == {"NOVA.DE": 2}
    assert fpref.esiti(pref)["NOVA.DE"]["esito"] == "scollegato"
    nova = _titolo(c.get("/filings").json(), "NOVA.DE")
    assert (nova["stato"], nova["documento"], nova["profilo"]) == ("non_attivo", None, False)
    assert nova["cambiamenti"] == 0
    assert "collegamento annullato" in nova["stato_riga"] or "link removed" in nova["stato_riga"]
    prev = c.get("/filings/NOVA.DE/context-preview").json()
    assert "Nuovo rischio" not in prev["testo"] and prev["in_evidenza"] == []
    assert c.get("/filings/novita").json()["n"] == 0
    assert c.post("/filings/NOVA.DE/unlink").status_code == 409  # gia' scollegato
    # nessuna versione cancellata (append-only)
    with sqlite3.connect(env.service.store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM filing_profiles WHERE ticker='NOVA.DE'").fetchone()[0] == 2


def test_unlink_esef_rifiuta_il_lei(env):
    env.service.store.set_profile("ORSA.MI", profilo_esef("ORSA.MI", lei=LEI_ORSA, nome="Orsa SpA",
                                                          origine="confermato_utente", lingua="it"), interval_hours=24)
    assert env.client().post("/filings/ORSA.MI/unlink").status_code == 200
    pref = fpref.carica(env.pref)
    assert fpref.rifiutati_lei(pref, "ORSA.MI") == {LEI_ORSA} and "ORSA.MI" not in pref["rifiutati"]


def test_unlink_404_422_409(env):
    c = env.client()
    assert c.post("/filings/NOVA.DE/unlink", json={}).status_code == 404
    assert c.post("/filings/NOVA.DE/unlink", json={"x": 1}).status_code == 422
    ir = {"ticker": "ACME.PA", "emittente_id": "EMITTENTE:Acme", "nome": "Acme", "origine_collegamento": "proposta_ai",
          "fonti": ["ir"], "ir_urls": [URL_IR], "lingua": "fr", "tipo": "semestrale", "perimetro": "consolidato",
          "verifica": {"lingua": "le", "tipo": "semestriel", "perimetro": "consolid"},
          "sezioni": {"risques": {"inizio": "Risques", "fine": "Perspectives"}}}
    env.service.store.set_profile("ACME.PA", ir, interval_hours=24)
    assert c.post("/filings/ACME.PA/unlink").status_code == 422
    manuale = {k: v for k, v in profilo_sec("KORE.MI", cik="9990011", sec_ticker="KORE", nome="Kore", origine="x",
                                            forme={"10-K"}).items() if k != "origine_collegamento"}
    env.service.store.set_profile("KORE.MI", manuale, interval_hours=24)
    assert c.post("/filings/KORE.MI/unlink").status_code == 422
    _profilo_sec(env.service)
    env.service.store.start_run("NOVA.DE", trigger="manual")
    assert c.post("/filings/NOVA.DE/unlink").status_code == 409
    assert env.service.store.get_profile("NOVA.DE")["enabled"] is True
    assert fpref.carica(env.pref)["rifiutati"] == {}


# ------------------------------------------------------------------------------- proposta AI in cache

@pytest.fixture
def sentinelle(monkeypatch):
    from bellomberg.core import llm_client as lc

    def vietato(*a, **k):
        raise AssertionError("nessun download e nessuna chiamata AI")

    monkeypatch.setattr(lc, "OpenRouterClient", vietato)
    monkeypatch.setattr("bellomberg.market_data.lettore_trimestrali.scarica_documento", vietato)


def test_ai_proposal_in_cache(env, sentinelle):
    c = env.client()
    assert c.get("/filings/ACME.PA/ai-proposal").status_code == 404
    _proposta_in_cache(sha=SHA_A, versione=fp.VERSIONE_VERIFICA - 1)
    r = c.get("/filings/ACME.PA/ai-proposal")
    assert r.status_code == 200
    j = r.json()
    for k in ("stato", "sha256", "url", "tipo", "periodo", "verificate", "scartate", "salvabile", "motivi", "avvisi",
              "altri", "modello", "costo_eur", "costo_usd"):
        assert k in j, k
    assert j["stato"] == "done" and j["cached"] is True and j["sha256"] == SHA_A
    assert j["at"] == "2026-10-02T10:00:00+00:00" and j["da_riverificare"] is True and j["accettata"] is False
    assert c.get("/filings/NOVA.DE/ai-proposal").status_code == 404  # proposta di un altro titolo
    assert c.get("/filings/acme/ai-proposal").status_code == 422


def test_ai_proposal_scartata_e_tombstone_404(env, sentinelle):
    c = env.client()
    _proposta_in_cache(sha=SHA_A, fallita=True)
    assert c.get("/filings/ACME.PA/ai-proposal").status_code == 404
    (fp._cache_dir() / f"{'f' * 64}.json").write_text("{rotto")
    assert c.get("/filings/ACME.PA/ai-proposal").status_code == 404
    _proposta_in_cache(sha=SHA_B)
    fpref.scarta_proposta(SHA_B, path=env.pref)
    assert c.get("/filings/ACME.PA/ai-proposal").status_code == 404


def test_discard_422_404(env, sentinelle):
    c = env.client()
    for body in ({}, {"sha256": "x"}, {"sha256": SHA_A.upper()}, {"sha256": SHA_A, "altro": 1}):
        assert c.post("/filings/ACME.PA/ai-proposal/discard", json=body).status_code == 422
    assert c.post("/filings/ACME.PA/ai-proposal/discard", json={"sha256": SHA_A}).status_code == 404
    _proposta_in_cache(ticker="ORSA.MI", sha=SHA_A)
    assert c.post("/filings/ACME.PA/ai-proposal/discard", json={"sha256": SHA_A}).status_code == 404
    assert fpref.proposte_scartate(fpref.carica(env.pref)) == frozenset()


# ------------------------------------------------------------------------------- context-preview

def test_context_preview_in_evidenza_nell_ordine_della_scheda(env):
    from bellomberg.agents import filing_context as fc
    _profilo_sec(env.service)
    _confronto(env.service, cambiamenti=[
        {"tipo": "aggiunto", "dopo": _citazione("nota breve", sezione="altro")},
        {"tipo": "aggiunto", "dopo": _citazione("Nuovo rischio rilevante di liquidita' e di credito " * 30)},
        {"tipo": "rimosso", "prima": _citazione("Rischio eliminato dal documento " * 10)}])
    prev = env.client().get("/filings/NOVA.DE/context-preview").json()
    scheda = fc.schede_filing(["NOVA.DE"], db_path=env.service.store.db_path)[0]
    attesi = [f"C{c['pos']}" for c in scheda["cambiamenti"]]
    assert prev["in_evidenza"] == attesi and sorted(attesi) == ["C1", "C2", "C3"]
    assert attesi != ["C1", "C2", "C3"]  # ordine del punteggio, non della posizione
    assert env.client().get("/filings/ACME.PA/context-preview").json()["in_evidenza"] == []
