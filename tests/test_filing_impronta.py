import sqlite3

import pytest

from bellomberg.market_data.filing_impronta import impronta_depositi, stessi_depositi
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import SCHEMA, FilingStore, RunNotDue, ensure_schema

OGGI = "2026-10-03"
PROFILO = {"ticker": "KORE.MI", "emittente_id": "CIK:0009990002", "lingua": "en", "tipo": "annuale",
           "perimetro": "consolidato", "cik": "0009990002", "fonti": ["sec"],
           "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
           "sezioni": {"rischi": {"inizio": "Risk Factors", "fine": "Properties"}},
           "varianti": [{"tipo": "annuale", "forme_sec": ["20-F"]}, {"tipo": "semestrale", "forme_sec": ["6-K"]}]}


def _catalogo(*righe):
    docs = [{"form": f, "filed_date": d, "accession": a} for f, d, a in righe]
    docs.sort(key=lambda r: (r["filed_date"], r["accession"]), reverse=True)
    return lambda ticker, cik=None: {"stato": "ok", "documenti": docs}


def V(relazione, esaminato=None):
    return {"relazione": relazione, "esaminato_fino_a": esaminato or relazione}


def _allegato(acc, relazione):
    # forma di sec_edgar.allegati_filing: seq, descrizione, tipo, ixbrl, url, dimensione
    nome = "interim-report.htm" if relazione else "press-release.htm"
    return [{"seq": 1, "nome": nome, "url": f"https://www.sec.gov/Archives/edgar/data/9990002/{acc}/{nome}",
             "tipo": "6-K", "descrizione": "interim report" if relazione else "press release",
             "ixbrl": relazione, "dimensione": 1000}]


def test_impronta_per_variante_e_rettifiche():
    cat = _catalogo(("20-F", "2026-03-01", "0009990002-26-000010"), ("6-K", "2026-08-01", "0009990002-26-000020"),
                    ("20-F/A", "2026-04-01", "0009990002-26-000011"), ("SC 13G", "2026-09-01", "x"),
                    ("6-K/A", "2026-09-02", "0009990002-26-000030"))
    imp = impronta_depositi(PROFILO, catalogo_fn=cat, allegati_fn=lambda c, a: _allegato(a, True), oggi=OGGI)
    assert imp == {"fonte": "sec", "varianti": {"annuale": V("0009990002-26-000011"), "semestrale": V("0009990002-26-000020")}}
    assert impronta_depositi({**PROFILO, "cik": None}, catalogo_fn=cat) is None


def test_solo_profili_sec_puri_hanno_il_controllo_leggero():
    cat = _catalogo(("20-F", "2026-03-01", "A1"))
    # fonti assenti = anche IR/ESEF: pagine non coperte dall'elenco SEC, quindi run completo
    senza_fonti = {k: v for k, v in PROFILO.items() if k != "fonti"}
    assert impronta_depositi(senza_fonti, catalogo_fn=cat) is None
    assert impronta_depositi({**PROFILO, "fonti": ["sec", "ir"]}, catalogo_fn=cat) is None


def test_catalogo_in_errore_solleva():
    with pytest.raises(ValueError):
        impronta_depositi(PROFILO, catalogo_fn=lambda t, cik=None: {"stato": "errore", "documenti": [], "motivi": ["x"]})


def test_profilo_senza_forme_segue_la_pipeline():
    profilo = {k: v for k, v in PROFILO.items() if k != "varianti"}
    cat = _catalogo(("20-F", "2026-03-01", "A1"), ("10-Q", "2026-05-01", "Q1"))
    assert impronta_depositi(profilo, catalogo_fn=cat) == {"fonte": "sec", "varianti": {"annuale": V("A1")}}
    trimestrale = {**profilo, "tipo": "trimestrale"}
    assert impronta_depositi(trimestrale, catalogo_fn=cat) == {"fonte": "sec", "varianti": {"trimestrale": V("Q1")}}


def test_6k_senza_relazione_non_e_un_deposito_nuovo():
    cat = _catalogo(("20-F", "2026-03-01", "A1"), ("6-K", "2026-08-01", "R1"),
                    ("6-K", "2026-09-01", "P1"), ("6-K", "2026-09-15", "P2"))
    relazioni = {"R1"}
    sondati = []

    def allegati(cik, acc):
        sondati.append(acc)
        return _allegato(acc, acc in relazioni)

    rif = {"fonte": "sec", "varianti": {"annuale": V("A1"), "semestrale": V("R1")}}
    imp = impronta_depositi(PROFILO, riferimento=rif, catalogo_fn=cat, allegati_fn=allegati, oggi=OGGI)
    assert imp["varianti"]["semestrale"] == V("R1", "P2") and stessi_depositi(rif, imp)  # solo comunicati nuovi
    assert sondati == ["P2", "P1"]          # sondati solo i 6-K piu' nuovi del riferimento
    sondati.clear()
    assert impronta_depositi(PROFILO, riferimento=imp, catalogo_fn=cat, allegati_fn=allegati, oggi=OGGI) == imp
    assert sondati == []                    # gia' esaminati: nessuna sonda
    relazioni.add("P1")
    imp = impronta_depositi(PROFILO, riferimento=rif, catalogo_fn=cat, allegati_fn=allegati, oggi=OGGI)
    assert imp["varianti"]["semestrale"] == V("P1", "P2") and sondati == ["P2", "P1"]
    assert not stessi_depositi(rif, imp)


def test_riferimento_senza_relazione_e_forma_precedente():
    cat = _catalogo(("6-K", "2026-09-01", "P1"), ("6-K", "2026-09-15", "P2"))
    sondati = []

    def allegati(cik, acc):
        sondati.append(acc)
        return _allegato(acc, False)

    imp = impronta_depositi(PROFILO, catalogo_fn=cat, allegati_fn=allegati, oggi=OGGI)
    assert imp["varianti"]["semestrale"] == {"relazione": None, "esaminato_fino_a": "P2"} and sondati == ["P2", "P1"]
    sondati.clear()
    assert impronta_depositi(PROFILO, riferimento=imp, catalogo_fn=cat, allegati_fn=allegati, oggi=OGGI) == imp
    assert sondati == []                    # nessun 6-K piu' nuovo: zero sonde
    vecchia = {"fonte": "sec", "varianti": {"annuale": None, "semestrale": "R0"}}   # forma della prima versione
    assert impronta_depositi(PROFILO, riferimento=vecchia, catalogo_fn=cat, allegati_fn=allegati, oggi=OGGI) == imp
    assert sondati == ["P2", "P1"] and not stessi_depositi(vecchia, imp)


def test_troppi_6k_nuovi_o_indice_illeggibile_sollevano():
    righe = [("6-K", "2026-09-%02d" % (i % 28 + 1), f"P{i:03d}") for i in range(90)] + [("6-K", "2026-01-01", "R1")]
    cat = _catalogo(*righe)
    rif = {"fonte": "sec", "varianti": {"annuale": V(None), "semestrale": V("R1")}}
    with pytest.raises(ValueError):
        impronta_depositi(PROFILO, riferimento=rif, catalogo_fn=cat, allegati_fn=lambda c, a: _allegato(a, False), oggi=OGGI)

    def rotto(cik, acc):
        raise ConnectionError("indice")
    with pytest.raises(ValueError):
        impronta_depositi(PROFILO, catalogo_fn=_catalogo(("6-K", "2026-09-01", "P1")), allegati_fn=rotto, oggi=OGGI)


def _allegati_relazione(cik, acc):
    # ogni 6-K dei test di servizio contiene una relazione
    return _allegato(acc, True)


def _servizio(tmp_path, catalogo, risultato=None, indice=None, allegati=None):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    store.set_profile("KORE.MI", PROFILO, interval_hours=24)
    chiamate = []

    def pipeline(profilo, archivio):
        chiamate.append(profilo["ticker"])
        return risultato[0] if isinstance(risultato, list) else (risultato or {
            "stato": "ok", "motivi": [], "confronto_corrente": {"stato": "ok", "cambiamenti": []}})

    svc = FilingService(store, tmp_path / "arch", pipeline=pipeline,
                        indexer=lambda *a: indice or {"status": "skipped"},
                        impronta_fn=lambda p, riferimento=None: impronta_depositi(
                            p, riferimento=riferimento, catalogo_fn=catalogo[0],
                            allegati_fn=allegati or _allegati_relazione, oggi=OGGI))
    return store, svc, chiamate


def _scaduto(store, finito=False):
    # i run conclusi sono immutabili: il trigger si toglie solo per retrodatarli e si reinstalla
    guardia = next(s for s in SCHEMA if "filing_runs_final_immutable" in s)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER filing_runs_final_immutable")
        conn.execute("UPDATE filing_runs SET started_at='2026-01-01T00:00:00+00:00'"
                     + (", finished_at='2026-01-01T00:01:00+00:00'" if finito else ""))
        conn.execute(guardia)


def test_nessun_deposito_nuovo_niente_download_e_titolo_non_scaduto(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    primo = svc.run_programmato("KORE.MI")
    assert primo["status"] == "ok" and chiamate == ["KORE.MI"]
    assert primo["result"]["impronta_depositi"] == {"fonte": "sec", "varianti": {"annuale": V("A1"), "semestrale": V(None)}}
    _scaduto(store)
    leggero = svc.run_programmato("KORE.MI")
    assert chiamate == ["KORE.MI"]                       # nessun secondo download/confronto
    assert leggero["status"] == "skipped" and leggero["reason"] == "controllato, nessun deposito nuovo"
    assert leggero["trigger"] == "scheduled"
    assert leggero["result"] == {"controllo_leggero": True, "run_riferimento": primo["id"],
                                 "impronta_depositi": primo["result"]["impronta_depositi"]}
    assert store.next_due() == []                         # controllato oggi: non scaduto
    assert store.ultimo_run_completo("KORE.MI")["status"] == "ok"
    with pytest.raises(RunNotDue):
        svc.run_programmato("KORE.MI")


def test_deposito_nuovo_o_profilo_cambiato_fanno_il_run_completo(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    svc.run_programmato("KORE.MI")
    _scaduto(store)
    cat[0] = _catalogo(("20-F", "2026-03-01", "A1"), ("6-K", "2026-08-01", "B1"))
    assert svc.run_programmato("KORE.MI")["status"] == "ok" and len(chiamate) == 2
    _scaduto(store)
    store.set_profile("KORE.MI", {**PROFILO, "sezioni_salta_indice": True}, interval_hours=24)
    svc.run_programmato("KORE.MI")
    assert len(chiamate) == 3
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] == "skipped" and len(chiamate) == 3


def test_ultimo_run_completo_non_riuscito_rifa_il_run_completo(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat, risultato={"stato": "errore", "motivi": ["x"]})
    svc.run_programmato("KORE.MI")
    _scaduto(store)
    svc.run_programmato("KORE.MI")
    assert len(chiamate) == 2


def test_catalogo_in_errore_ripiega_sul_run_completo_e_verifica_ora_sempre_completa(tmp_path):
    def rotto(ticker, cik=None):
        raise ConnectionError("SEC giù")
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    svc.run_programmato("KORE.MI")
    _scaduto(store)
    cat[0] = rotto
    errore = svc.run_programmato("KORE.MI")
    assert len(chiamate) == 2 and errore["status"] == "ok" and "impronta_depositi" not in errore["result"]
    cat[0] = _catalogo(("20-F", "2026-03-01", "A1"))
    manuale = svc.execute(svc.queue("KORE.MI", trigger="manual")["id"])   # "Verifica ora"
    assert len(chiamate) == 3 and manuale["result"]["impronta_depositi"]["varianti"]["annuale"] == V("A1")
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] == "skipped"     # riferimento lasciato da "Verifica ora"
    assert len(chiamate) == 3


def test_run_due_usa_il_controllo_leggero(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    assert [r["status"] for r in svc.run_due()] == ["ok"]
    _scaduto(store)
    assert [r["status"] for r in svc.run_due()] == ["skipped"] and len(chiamate) == 1


def test_ultimo_run_completo_oltre_la_finestra_di_20(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    svc.run_programmato("KORE.MI")
    for _ in range(25):
        _scaduto(store)
        svc.run_programmato("KORE.MI")
    assert len(chiamate) == 1
    assert store.ultimo_run_completo("KORE.MI")["result"]["confronto_corrente"]["stato"] == "ok"
    assert store.ultimo_run_completo("NOVA.DE") is None


def test_contesto_consigliere_ignora_i_controlli_leggeri(tmp_path):
    from bellomberg.agents.filing_context import get_filing_changes, schede_filing
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    completo = svc.run_programmato("KORE.MI")
    for _ in range(21):                                   # oltre la finestra list_runs(limit=20)
        _scaduto(store)
        svc.run_programmato("KORE.MI")
    assert len(chiamate) == 1
    assert get_filing_changes("KORE.MI", db_path=store.db_path)["run_id"] == completo["id"]
    scheda = schede_filing(["KORE.MI"], db_path=store.db_path, pref_path=tmp_path / "p.json")[0]
    assert scheda["gruppo"] == 2 and "aggiornato" in scheda["stato"]   # confronto presente, controllo di oggi


SEC_OK = {"nome": "SEC EDGAR", "stato": "ok", "motivi": []}


def _parziale(**extra):
    return {"stato": "parziale", "motivi": ["x"], "fonti": [SEC_OK], "candidati": [], "ultimo_non_verificato": False,
            "confronto_corrente": {"stato": "ok", "cambiamenti": []}, **extra}


def test_riferimento_con_guasto_transitorio_rifa_il_run_completo(tmp_path):
    casi = [
        # catalogo della pipeline in errore: SEC in errore, nessun candidato
        {"stato": "non_disponibile", "motivi": ["SEC EDGAR: ConnectionError"], "candidati": [],
         "fonti": [{"nome": "SEC EDGAR", "stato": "errore", "motivi": ["ConnectionError"]}]},
        # download fallito: candidato non verificato e confronto solo storico
        _parziale(ultimo_non_verificato=True, confronto_corrente=None, confronto_storico={"stato": "ok"},
                  candidati=[{"stato": "non_verificato", "errore_acquisizione": True, "motivi": ["timeout"]}]),
        # indice 6-K illeggibile durante la pipeline
        _parziale(fonti=[{**SEC_OK, "indici_non_letti": 1}]),
        # variante secondaria con un download interrotto
        _parziale(varianti=[{"tipo": "annuale", "completo": True}, {"tipo": "semestrale", "completo": False}]),
    ]
    for n, risultato in enumerate(casi):
        cartella = tmp_path / str(n)
        cartella.mkdir()
        store, svc, chiamate = _servizio(cartella, [_catalogo(("20-F", "2026-03-01", "A1"))], risultato=risultato)
        svc.run_programmato("KORE.MI")
        _scaduto(store)
        assert svc.run_programmato("KORE.MI")["status"] != "skipped" and len(chiamate) == 2, n


def test_indice_limitato_non_rende_parziale_e_resta_riferimento(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat, indice={"status": "parziale", "reason": "indice limitato"})
    assert svc.run_programmato("KORE.MI")["status"] == "ok"
    _scaduto(store); _scaduto(store, finito=True)
    assert svc.run_programmato("KORE.MI")["status"] == "skipped" and len(chiamate) == 1


def test_parziale_per_limiti_stabili_resta_riferimento_ma_scade_in_7_giorni(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat, risultato={
        "stato": "parziale", "motivi": ["limite"], "confronto_corrente": {"stato": "ok", "cambiamenti": []}})
    assert svc.run_programmato("KORE.MI")["status"] == "parziale"
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] == "skipped" and len(chiamate) == 1
    _scaduto(store, finito=True)                    # riferimento non-ok vecchio di oltre 7 giorni
    assert svc.run_programmato("KORE.MI")["status"] == "parziale" and len(chiamate) == 2


def test_6k_senza_relazione_controllo_successivo_senza_sonde(tmp_path):
    cat = [_catalogo(("6-K", "2026-09-01", "P1"), ("6-K", "2026-09-15", "P2"))]
    sondati = []

    def comunicati(cik, acc):
        sondati.append(acc)
        return _allegato(acc, False)

    store, svc, chiamate = _servizio(tmp_path, cat, allegati=comunicati)
    svc.run_programmato("KORE.MI")
    assert sondati == ["P2", "P1"]
    sondati.clear()
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] == "skipped" and sondati == [] and len(chiamate) == 1
    cat[0] = _catalogo(("6-K", "2026-09-01", "P1"), ("6-K", "2026-09-15", "P2"), ("6-K", "2026-09-20", "P3"))
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] == "skipped" and sondati == ["P3"]
    sondati.clear()
    _scaduto(store)                                  # P3 gia' esaminato dal controllo leggero precedente
    assert svc.run_programmato("KORE.MI")["status"] == "skipped" and sondati == []


def test_status_descrive_l_ultimo_run_completo_e_il_controllo_a_parte(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    completo = svc.run_programmato("KORE.MI")
    _scaduto(store)
    leggero = svc.run_programmato("KORE.MI")
    st = svc.status("KORE.MI")
    assert st["status"] == "ok" and st["reason"] is None and st["last_attempt"] == store.get_run(completo["id"])["started_at"]
    assert st["ultimo_completo"]["id"] == completo["id"] and st["ultimo_completo"]["controllo_leggero"] is False
    assert st["ultimo_controllo"] == {"at": leggero["finished_at"], "esito": "controllato, nessun deposito nuovo"}
    assert [r["controllo_leggero"] for r in st["runs"]] == [True, False]
    svc.execute(svc.queue("KORE.MI")["id"])
    assert svc.status("KORE.MI")["ultimo_controllo"] is None


def test_ok_con_variante_secondaria_interrotta_rifa_il_run_completo(tmp_path):
    ok_primaria = {"stato": "ok", "motivi": [], "fonti": [SEC_OK], "candidati": [], "ultimo_non_verificato": False,
                   "confronto_corrente": {"stato": "ok", "cambiamenti": []},
                   "varianti": [{"tipo": "annuale", "stato": "ok", "primaria": True, "completo": True},
                                {"tipo": "semestrale", "stato": "parziale", "primaria": False, "completo": False}]}
    store, svc, chiamate = _servizio(tmp_path, [_catalogo(("20-F", "2026-03-01", "A1"))], risultato=ok_primaria)
    assert svc.run_programmato("KORE.MI")["status"] == "ok"
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] == "ok" and len(chiamate) == 2


def test_data_di_fine_malformata_non_e_un_riferimento():
    base = {"status": "parziale", "result": _parziale(), "judgment": None, "index": None}
    for fine in ("non-una-data", "2026-10-01T00:00:00", None):        # anche senza fuso orario
        assert FilingService._riferimento_valido({**base, "finished_at": fine}) is False


# --- revisione Task 10: dopo un errore il riferimento e' l'ultimo run completo valido ---

def test_dopo_un_run_in_errore_il_controllo_leggero_usa_l_ultimo_riferimento_valido(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    primo = svc.run_programmato("KORE.MI")
    pipeline = svc.pipeline

    def rotta(profilo, archivio):
        chiamate.append("rotta")
        raise ConnectionError("rete")
    svc.pipeline = rotta
    assert svc.run("KORE.MI")["status"] == "errore"           # dopo i download, p.es. confronto fallito
    svc.pipeline = pipeline
    _scaduto(store)
    leggero = svc.run_programmato("KORE.MI")
    assert chiamate == ["KORE.MI", "rotta"]                   # nessun nuovo download
    assert leggero["status"] == "skipped" and leggero["result"]["run_riferimento"] == primo["id"]


def test_orfano_recuperato_poi_controllo_leggero(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    primo = svc.run_programmato("KORE.MI")
    _scaduto(store)
    orfano = svc.queue("KORE.MI", "scheduled")
    store.recover_run(orfano["id"], "run orfano oltre 2 h (processo terminato)")
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["result"]["run_riferimento"] == primo["id"]
    assert chiamate == ["KORE.MI"]


def test_dopo_un_errore_un_deposito_nuovo_fa_il_run_completo(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat)
    svc.run_programmato("KORE.MI")
    orfano = svc.queue("KORE.MI", "manual")
    store.recover_run(orfano["id"], "interrotto")
    cat[0] = _catalogo(("20-F", "2026-03-01", "A1"), ("20-F", "2026-09-01", "A2"))
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] == "ok" and chiamate == ["KORE.MI", "KORE.MI"]


# --- revisione Task 10 (giro 2): un errore dopo il confronto non lo nasconde ai lettori ---

_CONFRONTO = {"stato": "ok", "motivi": [], "confronto_corrente": {"stato": "ok", "cambiamenti": [
    {"tipo": "aggiunto", "dopo": {"url": "https://www.sec.gov/x.htm", "sha256": "a" * 64, "sezione": "rischi",
                                  "inizio": 0, "fine": 30, "pagine_fisiche": [], "testo": "Kore new risk " * 30}}]}}


def _ok_errore_leggero(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat, risultato=_CONFRONTO)
    r1 = svc.run_programmato("KORE.MI")
    pipeline = svc.pipeline

    def rotta(profilo, archivio):
        raise ConnectionError("rete giu'\nriga finta")
    svc.pipeline = rotta
    r2 = svc.run("KORE.MI")
    svc.pipeline = pipeline
    _scaduto(store)
    leggero = svc.run_programmato("KORE.MI")
    assert leggero["status"] == "skipped"
    return store, svc, r1, r2


def test_lettori_coerenti_dopo_errore_e_controllo_leggero(tmp_path):
    from bellomberg.agents import filing_context as fc
    store, svc, r1, r2 = _ok_errore_leggero(tmp_path)
    g = fc.get_filing_changes("KORE.MI", db_path=store.db_path)
    assert g["status"] == "ok" and g["run_id"] == r1["id"] and g["changes_total"] == 1
    assert g["last_error"]["at"] == r2["finished_at"] and "ConnectionError" in g["last_error"]["reason"]
    assert g["last_check"]["esito"] == "nessun deposito nuovo"
    st = svc.status("KORE.MI")
    assert st["status"] == "ok" and st["ultimo_completo"]["id"] == r1["id"]
    assert st["ultimo_errore"]["id"] == r2["id"] and "ConnectionError" in st["ultimo_errore"]["reason"]
    assert st["ultimo_controllo"]["esito"] == "controllato, nessun deposito nuovo"
    s = fc.schede_filing(["KORE.MI"], db_path=store.db_path)[0]
    assert s["totale_cambiamenti"] == 1
    assert "nessun deposito nuovo; ultimo errore il " in s["stato"] and "ConnectionError" in s["stato"]
    assert "\n" not in s["stato"]


def test_senza_errore_successivo_niente_last_error(tmp_path):
    from bellomberg.agents import filing_context as fc
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, _ = _servizio(tmp_path, cat, risultato=_CONFRONTO)
    svc.run_programmato("KORE.MI")
    assert "last_error" not in fc.get_filing_changes("KORE.MI", db_path=store.db_path)
    assert svc.status("KORE.MI")["ultimo_errore"] is None


def _giudice_sentinella(svc):
    giudizi = []

    def giudice(result):
        giudizi.append(1)
        return {"status": "ok", "findings": [], "model": "stub", "usage": {}}
    svc.judge, svc._default_judge = giudice, False
    return giudizi


def test_run_programmati_non_chiamano_mai_il_giudizio_ai(tmp_path):
    # Revisione finale I4: il giudizio AI solo dal pulsante (trigger manuale), mai nei run
    # periodici (run_due, run_programmato, pre-run), neanche con il giudizio attivo nel profilo.
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat, risultato=_CONFRONTO)
    store.set_profile("KORE.MI", PROFILO, interval_hours=24, qualitative_enabled=True)
    giudizi = _giudice_sentinella(svc)
    primo = svc.run_programmato("KORE.MI")
    assert primo["status"] == "ok" and chiamate == ["KORE.MI"] and giudizi == []
    assert primo["judgment"]["status"] == "skipped"
    assert primo["judgment"]["reason"] == "giudizio AI solo da pulsante (Verifica ora)"
    _scaduto(store)
    svc.impronta_fn = None                                 # senza impronta: run completo diretto
    assert svc.run_due()[0]["judgment"]["status"] == "skipped" and giudizi == []
    assert len(chiamate) == 2
    manuale = svc.run("KORE.MI")                           # "Verifica ora"
    assert manuale["trigger"] == "manual" and giudizi == [1] and manuale["judgment"]["status"] == "ok"


def test_giudizio_fallito_si_ritenta_solo_dal_pulsante(tmp_path):
    cat = [_catalogo(("20-F", "2026-03-01", "A1"))]
    store, svc, chiamate = _servizio(tmp_path, cat, risultato=_CONFRONTO)
    store.set_profile("KORE.MI", PROFILO, interval_hours=24, qualitative_enabled=True)
    svc.run_programmato("KORE.MI")
    _scaduto(store)

    def rotto(result):
        raise RuntimeError("modello non disponibile")
    svc.judge, svc._default_judge = rotto, False
    assert svc.run("KORE.MI")["status"] == "errore"       # pulsante: giudizio fallito
    giudizi = _giudice_sentinella(svc)
    _scaduto(store)
    terzo = svc.run_programmato("KORE.MI")
    # nessun run completo forzato per ritentare il giudizio, nessuna chiamata AI
    assert terzo["status"] == "skipped" and giudizi == [] and len(chiamate) == 2


# --- revisione finale I5: incompletezza stabile (rettifica /A, max_documenti) non forza download ---

def _stabile(motivo):
    return _parziale(ultimo_non_verificato=True, confronto_corrente=None, confronto_storico={"stato": "ok"},
                     candidati=[{"stato": "non_verificato", "motivi": [motivo]}])


def test_incompletezza_stabile_resta_riferimento_con_la_regola_dei_7_giorni(tmp_path):
    casi = [_stabile("rettifica /A esclusa: versione corrente da verificare"),
            _stabile("ValueError: limite max_documenti=20 raggiunto: candidato non verificato"),
            _parziale(varianti=[{"tipo": "annuale", "completo": True, "transitorio": False},
                                {"tipo": "semestrale", "completo": False, "transitorio": False}])]
    for n, risultato in enumerate(casi):
        cartella = tmp_path / str(n)
        cartella.mkdir()
        store, svc, chiamate = _servizio(cartella, [_catalogo(("20-F", "2026-03-01", "A1"))], risultato=risultato)
        svc.run_programmato("KORE.MI")
        _scaduto(store)
        assert svc.run_programmato("KORE.MI")["status"] == "skipped" and len(chiamate) == 1, n
        _scaduto(store, finito=True)                # oltre 7 giorni: run completo come ogni non-ok
        assert svc.run_programmato("KORE.MI")["status"] != "skipped" and len(chiamate) == 2, n


def test_variante_con_guasto_transitorio_rifa_il_run_completo(tmp_path):
    risultato = _parziale(varianti=[{"tipo": "annuale", "completo": True, "transitorio": False},
                                    {"tipo": "semestrale", "completo": False, "transitorio": True}])
    store, svc, chiamate = _servizio(tmp_path, [_catalogo(("20-F", "2026-03-01", "A1"))], risultato=risultato)
    svc.run_programmato("KORE.MI")
    _scaduto(store)
    assert svc.run_programmato("KORE.MI")["status"] != "skipped" and len(chiamate) == 2


def test_incompleto_transitorio_distingue_i_marcatori():
    from bellomberg.market_data.filing_pipeline import esito_completo, incompleto_transitorio
    stabile = _stabile("rettifica /A esclusa: versione corrente da verificare")
    assert not esito_completo(stabile) and not incompleto_transitorio(stabile)
    for guasto in ({**stabile, "candidati": [{"stato": "non_verificato", "errore_acquisizione": True}]},
                   {**stabile, "fonti": [{**SEC_OK, "indici_non_letti": 2}]},
                   {**stabile, "fonti": [{**SEC_OK, "stato": "errore"}]}, None):
        assert incompleto_transitorio(guasto)


def test_esegui_profilo_dichiara_il_transitorio_per_variante(monkeypatch, tmp_path):
    from bellomberg.market_data import filing_pipeline
    esiti = {"annuale": _stabile("rettifica /A esclusa: versione corrente da verificare"),
             "semestrale": {**_parziale(), "candidati": [{"stato": "non_verificato", "errore_acquisizione": True}]}}
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", lambda p, **k: dict(esiti[p["tipo"]]))
    out = filing_pipeline.esegui_profilo(PROFILO, archivio=tmp_path)
    per = {v["tipo"]: v for v in out["varianti"]}
    assert per["annuale"]["completo"] is False and per["annuale"]["transitorio"] is False
    assert per["semestrale"]["transitorio"] is True
