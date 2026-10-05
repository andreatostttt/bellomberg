from datetime import datetime, timezone

from bellomberg.agents import filing_context as fc

INTESTAZIONE = "ARCHIVIO FILING [src: get_filing_changes]: test"


def _cit(testo, sezione="rischi"):
    return {"url": "https://www.sec.gov/Archives/x.htm", "sha256": "a" * 64, "sezione": sezione,
            "inizio": 0, "fine": len(testo), "pagine_fisiche": [], "testo": testo}


def _run(cambiamenti, *, fine="2026-10-03T08:00:00+00:00", variante="trimestrale"):
    meta = lambda d: {"metadati": {"periodo_fine": d}}
    return {"id": 7, "status": "ok", "finished_at": fine, "reason": None,
            "result": {"stato": "ok", "variante": variante,
                       "coppia": {"prima": meta("2025-09-28"), "dopo": meta("2026-09-27")},
                       "numeri": {"stato": "ok", "voci": [{"voce": "ricavi", "delta_pct": 15.0},
                                                          {"voce": "utile netto", "delta_pct": -3.2}]},
                       "confronto_corrente": {"stato": "ok", "cambiamenti": cambiamenti}}}


PROFILO_SEC = {"cik": "0009990001", "varianti": [{"tipo": "trimestrale", "forme_sec": ["10-Q"]}]}


def test_punteggio_pesi_tipo_lunghezza():
    lungo = "x" * 400
    assert fc.punteggio({"tipo": "aggiunto", "dopo": _cit(lungo, "rischi")}) == 3.0
    assert fc.punteggio({"tipo": "aggiunto", "dopo": _cit(lungo, "gestione")}) == 2.0
    assert fc.punteggio({"tipo": "rimosso", "prima": _cit("x" * 200, "rischio_mercato")}) == 0.5
    quasi = fc.punteggio({"tipo": "modificato", "prima": _cit("a" * 400, "rischi"),
                          "dopo": _cit("a" * 399 + "b", "rischi")})
    assert 0 < quasi < 0.1


def test_riga_di_stato_date_non_trimestri_e_novita():
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=_run([]), ultimo=None, escluso=False,
                         freschezza={"stato": "aggiornato", "motivo": None},
                         novita_dopo=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert s["stato"].startswith("NOVA.DE · SEC 10-Q CIK 0009990001 · trimestre al 27/09/2026 vs 28/09/2025")
    assert "aggiornato" in s["stato"] and "NOVITÀ" in s["stato"] and "Q3" not in s["stato"]
    assert s["numeri"] == "numeri ricavi +15,0% · utile netto −3,2%"
    assert s["gruppo"] == 2  # nessun cambiamento: invariato anche se nuovo


def test_cambiamenti_ordinati_limitati_e_citati():
    cambi = [{"tipo": "aggiunto", "dopo": _cit("minore " * 10, "rischio_mercato")},
             {"tipo": "aggiunto", "dopo": _cit("Nuovo rischio " * 40, "rischi")},
             {"tipo": "modificato", "prima": _cit("Ricavi cresciuti del 12% " * 10, "gestione"),
              "dopo": _cit("Ricavi cresciuti del 15% " * 10, "gestione")}]
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=_run(cambi), ultimo=None, escluso=False,
                         freschezza=None, novita_dopo=None)
    righe = [c["riga"] for c in s["cambiamenti"]]
    assert righe[0].startswith("+ rischi «Nuovo rischio") and righe[0].endswith("[C2-dopo]")
    # rischio_mercato corto (0,175) batte il modificato quasi identico (~0,05)
    assert righe[1].startswith("+ rischio_mercato «") and righe[1].endswith("[C1-dopo]")
    assert righe[2].startswith("~ gestione «") and "→" in righe[2] and righe[2].endswith("[C3-prima, C3-dopo]")
    assert s["limite"] == 2 and s["gruppo"] == 1
    assert all(len(r.split("«", 1)[1].split("»", 1)[0]) <= 221 for r in righe)


def test_freschezza_dichiarata_e_non_disponibili():
    lenta = fc.scheda_da_run("KORE.MI", profilo=PROFILO_SEC, run=_run([]), ultimo=None, escluso=False,
                             freschezza={"stato": "non_aggiornato", "motivo": "aggiornamento oltre 60 s (in corso)"},
                             novita_dopo=None)
    assert "NON AGGIORNATO: aggiornamento oltre 60 s (in corso)" in lenta["stato"]
    assert "confronto del 03/10" in lenta["stato"]
    senza = fc.scheda_da_run("ACME.PA", profilo=None, run=None, ultimo=None, escluso=False,
                             freschezza=None, novita_dopo=None)
    assert senza["gruppo"] == 3 and "non disponibile: nessun profilo" in senza["stato"]
    escluso = fc.scheda_da_run("ACME.PA", profilo=None, run=None, ultimo=None, escluso=True,
                               freschezza=None, novita_dopo=None)
    assert "escluso dal controllo" in escluso["stato"]


def _scheda(t, gruppo, n, lunghezza=300):
    return {"ticker": t, "gruppo": gruppo, "peso": float(n), "limite": 4 if gruppo == 0 else 2,
            "stato": f"{t} · stato", "numeri": f"numeri {t}", "altra_variante": None,
            "totale_cambiamenti": n,
            "cambiamenti": [{"riga": f"+ rischi «{'x' * lunghezza}» [C{i}-dopo]", "punteggio": 1.0, "pos": i}
                            for i in range(1, n + 1)]}


def test_impagina_ordine_budget_e_troncamenti():
    schede = [_scheda("ZED.MI", 3, 0), _scheda("BBB.DE", 1, 5), _scheda("AAA.DE", 0, 6), _scheda("CCC.PA", 2, 0)]
    out = fc.impagina(schede, max_caratteri=2_000, intestazione=INTESTAZIONE)
    testo = out["testo"]
    assert [t for t in ("AAA.DE", "BBB.DE", "CCC.PA", "ZED.MI")] == sorted(
        ("AAA.DE", "BBB.DE", "CCC.PA", "ZED.MI"), key=testo.index)
    assert out["caratteri"] == len(testo) <= 2_000
    for t in ("AAA.DE", "BBB.DE", "CCC.PA", "ZED.MI"):
        assert f"{t} · stato" in testo                     # righe di stato mai tolte
    assert "get_filing_changes(AAA.DE" in testo and "get_filing_changes(BBB.DE" in testo
    assert testo.rstrip().splitlines()[-1].startswith("TRONCAMENTI:")
    assert out["righe"]["AAA.DE"]["testo"] in testo
    # i cambiamenti tolti partono dal fondo: AAA (primo) conserva almeno quanti ne conserva BBB
    assert testo.count("[C", testo.index("AAA.DE"), testo.index("BBB.DE")) >= \
        testo.count("[C", testo.index("BBB.DE"), testo.index("CCC.PA"))


def test_impagina_portafoglio_enorme_supera_dichiarando():
    schede = [_scheda(f"T{i:02d}.MI", 2, 0) for i in range(80)]
    out = fc.impagina(schede, max_caratteri=500, intestazione=INTESTAZIONE)
    assert all(f"T{i:02d}.MI · stato" in out["testo"] for i in range(80))
    assert "oltre il budget" in out["testo"].splitlines()[-1]


# --- oltre il brief: raccolta dall'archivio reale, conteggio esatto, casi limite ---

import json
import sqlite3

from bellomberg.storage.filing_store import FilingStore, ensure_schema

PROFILO_ARCHIVIO = {"emittente_id": "CIK:9990001", "cik": "0009990001", "lingua": "en",
                    "tipo": "trimestrale", "perimetro": "consolidato", "forme_sec": ["10-Q"],
                    "verifica": {"lingua": "English", "tipo": "quarterly", "perimetro": "consolidated"},
                    "sezioni": {"rischi": {"inizio": "Risk Factors", "fine": "End"}}}


def _archivio(tmp_path):
    path = tmp_path / "filing.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    store.set_profile("NOVA.DE", {**PROFILO_ARCHIVIO, "ticker": "NOVA.DE"}, interval_hours=24)
    run = store.start_run("NOVA.DE")
    store.finish_run(run["id"], status="ok",
                     result=_run([{"tipo": "aggiunto", "dopo": _cit("Nuovo rischio export " * 5)}])["result"])
    store.set_profile("KORE.MI", {**PROFILO_ARCHIVIO, "ticker": "KORE.MI", "cik": "0009990002"})
    run = store.start_run("KORE.MI")
    store.finish_run(run["id"], status="errore", reason="SEC non raggiungibile: timeout")
    store.set_profile("ACME.PA", {**PROFILO_ARCHIVIO, "ticker": "ACME.PA", "cik": "0009990003"})
    return path


def test_schede_filing_da_archivio_reale(tmp_path):
    path = _archivio(tmp_path)
    pref = tmp_path / "pref.json"
    pref.write_text(json.dumps({"esclusi": ["ACME.PA"], "rifiutati": {}}), encoding="utf-8")
    schede = fc.schede_filing(["NOVA.DE", "KORE.MI", "ACME.PA", "NOVA.DE", "ZETA.MI"],
                              db_path=path, pref_path=pref)
    per = {s["ticker"]: s for s in schede}
    assert [s["ticker"] for s in schede] == ["NOVA.DE", "KORE.MI", "ACME.PA", "ZETA.MI"]
    nova = per["NOVA.DE"]
    assert nova["gruppo"] == 1 and nova["totale_cambiamenti"] == 1
    assert nova["stato"].startswith("NOVA.DE · SEC 10-Q CIK 0009990001 · trimestre al 27/09/2026")
    assert " · aggiornato" in nova["stato"]          # run appena concluso, entro 24 h
    kore = per["KORE.MI"]
    assert kore["gruppo"] == 3
    assert "non disponibile: SEC non raggiungibile: timeout" in kore["stato"]
    assert "escluso dal controllo" in per["ACME.PA"]["stato"]
    assert "nessun profilo" in per["ZETA.MI"]["stato"]
    out = fc.impagina(schede, intestazione=INTESTAZIONE)
    assert out["testo"].index("NOVA.DE") < out["testo"].index("KORE.MI")


def test_schede_filing_novita_e_freschezza_dichiarata(tmp_path):
    path = _archivio(tmp_path)
    schede = fc.schede_filing(["NOVA.DE"], db_path=path, pref_path=tmp_path / "assente.json",
                              novita_dopo=datetime(2020, 1, 1, tzinfo=timezone.utc),
                              freschezza={"NOVA.DE": {"stato": "non_aggiornato", "motivo": "in coda"}})
    assert schede[0]["gruppo"] == 0 and schede[0]["limite"] == 4
    assert "NON AGGIORNATO: in coda" in schede[0]["stato"] and "NOVITÀ" in schede[0]["stato"]


def test_schede_filing_archivio_assente_e_preferenze_illeggibili(tmp_path):
    pref = tmp_path / "pref.json"
    pref.write_text("{rotto", encoding="utf-8")
    schede = fc.schede_filing(["NOVA.DE", "KORE.MI"], db_path=tmp_path / "manca.sqlite", pref_path=pref)
    assert len(schede) == 2
    assert all(s["gruppo"] == 3 and "non disponibile: archivio filing: " in s["stato"] for s in schede)


def test_impagina_conteggio_esatto_a_ogni_budget():
    schede = [_scheda("AAA.DE", 0, 6), _scheda("BBB.DE", 1, 5, lunghezza=40), _scheda("CCC.PA", 2, 0)]
    for budget in (300, 999, 1_000, 1_001, 2_345, 9_999, 10_000, 14_000):
        out = fc.impagina(schede, max_caratteri=budget, intestazione=INTESTAZIONE)
        assert out["caratteri"] == len(out["testo"])
        piede = out["testo"].splitlines()[-1]
        dichiarati = piede.split("; ", 1)[1].split("/", 1)[0].replace(".", "")
        assert int(dichiarati) == len(out["testo"])
        if "oltre il budget" not in piede:
            assert len(out["testo"]) <= budget


def test_impagina_senza_troncamenti_e_righe_numeri():
    out = fc.impagina([_scheda("AAA.DE", 1, 2)], intestazione=INTESTAZIONE)
    assert out["omessi_totali"] == 0 and "numeri AAA.DE" in out["testo"]
    assert out["testo"].splitlines()[-1].startswith("TRONCAMENTI: 0 cambiamenti omessi")


def test_fonte_senza_forme_e_variante_altra():
    profilo = {"cik": "0009990009"}
    run = _run([])
    run["result"]["varianti"] = [
        {"tipo": "trimestrale", "primaria": True, "coppia_periodi": ["2025-09-28", "2026-09-27"]},
        {"tipo": "annuale", "primaria": False, "coppia_periodi": ["2024-12-31", "2025-12-31"],
         "confronto": {"stato": "ok", "cambiamenti": [{"tipo": "aggiunto"}] * 9}}]
    s = fc.scheda_da_run("NOVA.DE", profilo=profilo, run=run, ultimo=None, freschezza=None, novita_dopo=None)
    assert " · SEC CIK 0009990009 · " in s["stato"] and "SEC SEC" not in s["stato"]
    assert s["altra_variante"] == ('variante annuale: 31/12/2025 vs 31/12/2024 · 9 cambiamenti '
                                   '→ get_filing_changes(NOVA.DE, run_id=7, variante="annuale")')
    senza = fc.scheda_da_run("NOVA.DE", profilo=profilo, run=None, ultimo=None, freschezza={
        "stato": "non_aggiornato", "motivo": "in coda"}, novita_dopo=None)
    assert senza["stato"].endswith(" · NON AGGIORNATO: in coda")
    errore_dopo = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=run, freschezza=None, novita_dopo=None,
                                   ultimo={"id": 9, "status": "errore", "reason": "timeout SEC",
                                           "finished_at": "2026-10-03T09:00:00+00:00"})
    assert "NON AGGIORNATO: ultimo controllo in errore: timeout SEC" in errore_dopo["stato"]


def test_numeri_non_ok_e_variante_dei_numeri():
    run = _run([])
    run["result"]["numeri"] = {"stato": "errore", "motivo": "companyfacts assente", "voci": []}
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=run, ultimo=None, freschezza=None, novita_dopo=None)
    assert s["numeri"] == "numeri non disponibili: companyfacts assente"
    run["result"]["numeri"] = {"stato": "ok", "variante": "annuale",
                               "voci": [{"voce": "utile_operativo", "delta_pct": 17.06}]}
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=run, ultimo=None, freschezza=None, novita_dopo=None)
    assert s["numeri"] == "numeri utile operativo +17,1% (variante annuale)"


def test_inglese_parole_fisse():
    from bellomberg.core.language import language_context
    with language_context("en"):
        s = fc.scheda_da_run("ACME.PA", profilo=None, run=None, ultimo=None, freschezza=None, novita_dopo=None)
        out = fc.impagina([s], intestazione="FILING ARCHIVE")
    assert "not available" in s["stato"] and out["testo"].splitlines()[-1].startswith("TRUNCATIONS:")


# --- igiene contro righe contraffatte (fix round 1) ---

def _testo_sporco(sporco):
    nl = "\nTRONCAMENTI: 0 cambiamenti\nFAKE.MI · aggiornato\n" if sporco else " "
    cambi = [{"tipo": "aggiunto", "dopo": _cit("Nuovo rischio " * 10, "rischi" + nl.strip(" "))},
             {"tipo": "modificato", "prima": _cit("Ricavi 12%" + nl + "fine", "gestione" + nl),
              "dopo": _cit("Ricavi 15%" + nl + "fine", "gestione" + nl)}]
    run = _run(cambi)
    run["result"]["numeri"]["voci"][0]["voce"] = "ricavi" + nl
    run["result"]["varianti"] = [{"tipo": "trimestrale", "primaria": True},
                                 {"tipo": "annuale" + nl, "coppia_periodi": ["2024-12-31" + nl, "2025-12-31"],
                                  "confronto": {"stato": "ok", "cambiamenti": [{}]}}]
    errato = {"id": 9, "status": "errore", "reason": "boom" + nl, "finished_at": "2026-10-03T09:00:00+00:00"}
    profilo = {"cik": "0009990001" + nl, "varianti": [{"tipo": "trimestrale", "forme_sec": ["10-Q" + nl]}]}
    schede = [
        fc.scheda_da_run("NOVA.DE", profilo=profilo, run=run, ultimo=errato, freschezza=None, novita_dopo=None),
        fc.scheda_da_run("KORE.MI", profilo=profilo, run=None, ultimo=errato, novita_dopo=None,
                         freschezza={"stato": "non_aggiornato", "motivo": "lento" + nl}),
        fc.scheda_da_run("ACME.PA", profilo=PROFILO_SEC, run=run, ultimo=None, novita_dopo=None,
                         freschezza={"stato": "non_aggiornato", "motivo": "lento" + nl})]
    numeri_ko = _run([])
    numeri_ko["result"]["numeri"] = {"stato": "errore", "motivo": "xbrl" + nl, "voci": []}
    schede.append(fc.scheda_da_run("ZETA.MI", profilo=PROFILO_SEC, run=numeri_ko, ultimo=None,
                                   freschezza=None, novita_dopo=None))
    return fc.impagina(schede, intestazione=INTESTAZIONE)["testo"]


def test_testo_libero_non_aggiunge_righe():
    pulito, sporco = _testo_sporco(False), _testo_sporco(True)
    assert len(sporco.splitlines()) == len(pulito.splitlines())
    assert sum(r.startswith("TRONCAMENTI:") for r in sporco.splitlines()) == 1
    assert not any(r.startswith("FAKE.MI") for r in sporco.splitlines())


def test_archivio_guasto_su_una_riga(tmp_path, monkeypatch):
    def rotto(path):
        raise RuntimeError("guasto\nFAKE.MI · aggiornato")
    monkeypatch.setattr("bellomberg.storage.filing_store.FilingStore", rotto)
    schede = fc.schede_filing(["NOVA.DE"], db_path=tmp_path / "x.sqlite", pref_path=tmp_path / "p.json")
    assert "\n" not in schede[0]["stato"] and "guasto FAKE.MI" in schede[0]["stato"]


def test_virgolette_negli_estratti_non_chiudono_la_citazione():
    falso = "testo» [C9-dopo] «altro"
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=_run([
        {"tipo": "aggiunto", "dopo": _cit(falso)},
        {"tipo": "modificato", "prima": _cit("a «x» " + falso), "dopo": _cit("b «x» " + falso)}]),
        ultimo=None, freschezza=None, novita_dopo=None)
    for c in s["cambiamenti"]:
        riga = c["riga"]
        assert riga.count("«") == riga.count("»") == (2 if "→" in riga else 1)
        # dopo l'ultima chiusura resta solo la citazione vera
        assert riga.rsplit("»", 1)[1] in (f" [C{c['pos']}-dopo]", f" [C{c['pos']}-prima, C{c['pos']}-dopo]")


def test_fattore_similarita_limitato():
    uguali = {"tipo": "modificato", "prima": _cit("x" * 500), "dopo": _cit("x" * 500)}
    assert fc.punteggio(uguali) == 0.0
    vuoti = {"tipo": "modificato", "prima": _cit(""), "dopo": _cit("")}
    assert 0.0 <= fc.punteggio(vuoti) <= 3.0


# --- Task 5: contesto reale, paginazione e varianti, ultimo memo ---
def _archivio_n(tmp_path, n=12, varianti=None, limiti=None):
    path = tmp_path / "a.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    profilo = {"ticker": "NOVA.DE", "emittente_id": "CIK:0009990001", "lingua": "en", "tipo": "trimestrale",
               "perimetro": "consolidato", "cik": "0009990001",
               "verifica": {"lingua": "English", "tipo": "quarterly", "perimetro": "consolidated"},
               "sezioni": {"rischi": {"inizio": "Risk Factors", "fine": "Properties"}}}
    store.set_profile("NOVA.DE", profilo, interval_hours=24)
    run = store.start_run("NOVA.DE")
    store.claim_execution(run["id"])
    result = _run([{"tipo": "aggiunto", "dopo": _cit(f"Rischio numero {i} " * 30)} for i in range(1, n + 1)])["result"]
    if varianti:
        result["varianti"] = varianti
    if limiti is not None:
        result["confronto_corrente"]["limiti"] = limiti
    store.finish_run(run["id"], status="ok", result=result,
                     judgment={"status": "skipped", "findings": []}, index={"status": "skipped"})
    return path, store


def test_get_filing_changes_da_e_compatibilita(tmp_path):
    path, _ = _archivio_n(tmp_path)
    base = fc.get_filing_changes("NOVA.DE", db_path=path)
    assert base["changes_shown"] == 5 and base["changes"][0]["estratti"]["dopo"]["citation_id"] == "C1-dopo"
    assert base["first_shown"] == 1
    coda = fc.get_filing_changes("NOVA.DE", db_path=path, da=11)
    assert [c["estratti"]["dopo"]["citation_id"] for c in coda["changes"]] == ["C11-dopo", "C12-dopo"]
    assert coda["first_shown"] == 11 and coda["changes_total"] == 12


def test_get_filing_changes_variante(tmp_path):
    annuale = {"tipo": "annuale", "primaria": False, "coppia_periodi": ["2024-12-31", "2025-12-31"],
               "confronto": {"stato": "ok", "cambiamenti": [{"tipo": "aggiunto", "dopo": _cit("Rischio annuale " * 30)}]}}
    path, _ = _archivio_n(tmp_path, varianti=[annuale])
    v = fc.get_filing_changes("NOVA.DE", db_path=path, variante="annuale")
    assert v["variant"] == "annuale" and v["changes_total"] == 1
    assert v["changes"][0]["estratti"]["dopo"]["testo"].startswith("Rischio annuale")
    assert fc.get_filing_changes("NOVA.DE", db_path=path, variante="semestrale")["status"] == "non_disponibile"
    assert fc.get_filing_changes("NOVA.DE", db_path=path, variante="semestrale")["reason"] == "variante assente"


def test_get_filing_changes_parametri_non_validi_sono_errore(tmp_path):
    path, _ = _archivio_n(tmp_path, n=2)
    for kw in ({"da": 0}, {"da": -3}, {"da": "abc"}, {"da": 1.5}, {"da": True}, {"variante": "x"}):
        assert fc.get_filing_changes("NOVA.DE", db_path=path, **kw)["status"] == "errore"


def test_contesto_reale_include_tutti_i_titoli(tmp_path):
    path, _ = _archivio_n(tmp_path, n=80)
    testo = fc.committee_filing_context(["NOVA.DE", "ACME.PA"], db_path=path,
                                        pref_path=tmp_path / "pref.json")
    assert "NOVA.DE · SEC" in testo and "ACME.PA · non disponibile: nessun profilo" in testo
    assert "get_filing_changes(NOVA.DE" in testo and testo.splitlines()[-1].startswith("TRONCAMENTI:")
    assert len(testo) <= fc.MAX_CARATTERI


def _leggero(store, ticker="NOVA.DE"):
    run = store.start_run(ticker)
    store.claim_execution(run["id"])
    return store.finish_run(run["id"], status="skipped", reason="controllato, nessun deposito nuovo",
                            result={"controllo_leggero": True, "run_riferimento": 1})


def test_controlli_leggeri_non_nascondono_il_confronto(tmp_path):
    path, store = _archivio_n(tmp_path, n=3)
    for _ in range(25):
        leggero = _leggero(store)
    cambi = fc.get_filing_changes("NOVA.DE", db_path=path)
    assert cambi["changes_total"] == 3 and cambi["run_id"] == 1
    assert cambi["last_check"] == {"at": leggero["finished_at"], "esito": "nessun deposito nuovo"}
    scheda = fc.schede_filing(["NOVA.DE"], db_path=path, pref_path=tmp_path / "p.json")[0]
    assert scheda["totale_cambiamenti"] == 3
    assert f"aggiornato (controllato il {fc._giorno(leggero['finished_at'])}, nessun deposito nuovo)" in scheda["stato"]


def test_senza_controlli_leggeri_niente_last_check(tmp_path):
    path, _ = _archivio_n(tmp_path, n=1)
    assert "last_check" not in fc.get_filing_changes("NOVA.DE", db_path=path)


def test_ultima_run_esclude_segnaposto_e_corrente_e_converte_fuso():
    memos = [{"id": 9, "timestamp": "2026-10-03T10:00:00", "full_markdown": "[IN PROGRESS]"},
             {"id": 8, "timestamp": "2026-10-02T09:00:00", "full_markdown": "[IN PROGRESS]"},
             {"id": 7, "timestamp": "2026-09-26T09:00:00", "full_markdown": "# Memo"}]
    quando = fc.ultima_run_comitato(memos, escludi_id=9)
    assert quando.tzinfo is not None
    assert quando == datetime.fromisoformat("2026-09-26T09:00:00").astimezone().astimezone(timezone.utc)
    assert fc.ultima_run_comitato([]) is None


def _budget_base(s):
    """Caratteri della sola base (primi `limite` cambiamenti): nessuno spazio per il riempimento."""
    sola = {**s, "cambiamenti": s["cambiamenti"][:s["limite"]]}
    return fc.impagina([sola], max_caratteri=10 ** 6, intestazione=INTESTAZIONE)["caratteri"]


def test_rinvio_omessi_porta_alla_pagina_ampia():
    cambi = [{"tipo": "aggiunto", "dopo": _cit(f"rischio {i} " * 5)} for i in range(200)]
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=_run(cambi), ultimo=None, escluso=False,
                         freschezza=None, novita_dopo=None)
    testo = fc.impagina([s], intestazione=INTESTAZIONE)["testo"]
    # il desk legge proprio i successivi per importanza (k gia' mostrati, base + riempimento)
    k = testo.count("» [C")
    assert k > 2
    assert (f'altri {200 - k} cambiamenti omessi → get_filing_changes(NOVA.DE, run_id=7, ordine="punteggio", '
            f'da={k + 1}, max_changes=20)') in testo


def test_get_filing_changes_max_changes(tmp_path):
    path, _ = _archivio_n(tmp_path)
    tutti = fc.get_filing_changes("NOVA.DE", db_path=path, max_changes=20)
    assert tutti["changes_shown"] == min(20, tutti["changes_total"]) and tutti["changes_shown"] > 5
    for kw in ({"max_changes": 0}, {"max_changes": 21}, {"max_changes": "5"}, {"max_changes": True},
               {"max_changes": 2.0}):
        assert fc.get_filing_changes("NOVA.DE", db_path=path, **kw)["status"] == "errore", kw


# --- difetti trovati sui filing reali (prova reale fase D) ---

def _run_diff(diff, profilo=None, variante="trimestrale"):
    run = _run([], variante=variante)
    run["result"]["confronto_corrente"] = diff
    if profilo is not None:
        run["profile"] = profilo
    return run


def test_coppie_non_abbinate_dichiarate_nella_riga_di_stato():
    # 10-Q/20-F reali: oltre 10.000 coppie residue il diff non abbina i modificati,
    # che diventano un rimosso e un aggiunto quasi identici: il Consigliere deve saperlo.
    diff = {"stato": "ok", "sezioni_confrontate": ["rischi"],
            "limiti": ["Confronto testuale; rilevanza economica non valutata.",
                       "Troppe coppie: estratti non identici elencati separatamente."],
            "cambiamenti": [{"tipo": "rimosso", "prima": _cit("Kore export rule " * 30)},
                            {"tipo": "aggiunto", "dopo": _cit("Kore export rules " * 30)}]}
    s = fc.scheda_da_run("KORE.MI", profilo=PROFILO_SEC, run=_run_diff(diff), ultimo=None, escluso=False,
                         freschezza={"stato": "aggiornato", "motivo": None}, novita_dopo=None)
    assert "rimossi e aggiunti non abbinati" in s["stato"] and "riformulato" in s["stato"]
    senza = fc.scheda_da_run("KORE.MI", profilo=PROFILO_SEC, run=_run_diff({**diff, "limiti": diff["limiti"][:1]}),
                             ultimo=None, escluso=False, freschezza=None, novita_dopo=None)
    assert "non abbinati" not in senza["stato"]



PROFILO_SEZIONI = {"cik": "0009990002", "varianti": [
    {"tipo": "annuale", "forme_sec": ["20-F"], "sezioni": {"gestione": {}, "rischi": {}}}]}


def test_confronto_parziale_dichiara_le_sezioni_non_confrontate():
    # 20-F reale impaginato come relazione annuale: una sezione non riconosciuta.
    diff = {"stato": "parziale", "sezioni_confrontate": ["rischi"], "limiti": [],
            "cambiamenti": [{"tipo": "aggiunto", "dopo": _cit("Acme new risk " * 40)}]}
    s = fc.scheda_da_run("ACME.AS", profilo=PROFILO_SEZIONI, run=_run_diff(diff, PROFILO_SEZIONI, "annuale"), ultimo=None,
                         escluso=False, freschezza=None, novita_dopo=None)
    assert "confronto parziale: non confrontate gestione" in s["stato"]
    assert s["gruppo"] == 1


def test_nessuna_sezione_confrontata_dichiarata():
    # Nessuna sezione riconosciuta: zero cambiamenti non significa "invariato", la riga lo dice.
    diff = {"stato": "parziale", "sezioni_confrontate": [], "limiti": [], "cambiamenti": []}
    s = fc.scheda_da_run("ACME.AS", profilo=PROFILO_SEZIONI, run=_run_diff(diff, PROFILO_SEZIONI, "annuale"), ultimo=None,
                         escluso=False, freschezza=None, novita_dopo=None)
    assert "confronto parziale: nessuna sezione confrontata" in s["stato"]
    # senza le sezioni del profilo resta la dichiarazione generica
    t = fc.scheda_da_run("ACME.AS", profilo=PROFILO_SEC, run=_run_diff({**diff, "sezioni_confrontate": ["rischi"]}),
                         ultimo=None, escluso=False, freschezza=None, novita_dopo=None)
    assert "confronto parziale" in t["stato"] and t["gruppo"] == 2



def test_get_filing_changes_dichiara_i_limiti_del_diff(tmp_path):
    # Filing reali: il limite delle coppie non abbinate deve arrivare anche allo strumento dei desk.
    from bellomberg.market_data.filing_diff import LIMITE_COPPIE
    annuale = {"tipo": "annuale", "primaria": False, "coppia_periodi": ["2024-12-31", "2025-12-31"],
               "confronto": {"stato": "ok", "limiti": ["Confronto testuale."],
                             "cambiamenti": [{"tipo": "aggiunto", "dopo": _cit("Rischio annuale " * 30)}]}}
    path, _ = _archivio_n(tmp_path, varianti=[annuale], limiti=["Confronto testuale.", LIMITE_COPPIE])
    g = fc.get_filing_changes("NOVA.DE", db_path=path)
    assert g["diff_limits"] == ["Confronto testuale.", LIMITE_COPPIE] and g["unpaired_changes"] is True
    v = fc.get_filing_changes("NOVA.DE", db_path=path, variante="annuale")
    assert v["diff_limits"] == ["Confronto testuale."] and v["unpaired_changes"] is False


def test_in_attesa_del_primo_confronto_mostra_tutte_le_forme():
    # Profilo automatico appena attivato (prova reale): la fonte elenca tutte le varianti, non solo l'annuale.
    profilo = {"cik": "0009990003", "varianti": [{"tipo": "annuale", "forme_sec": ["10-K"]},
                                                 {"tipo": "trimestrale", "forme_sec": ["10-Q"]}]}
    s = fc.scheda_da_run("NOVA", profilo=profilo, run=None, ultimo=None, escluso=False,
                         freschezza={"stato": "non_aggiornato", "motivo": "aggiornamento oltre 60 s (in corso)"},
                         novita_dopo=None)
    assert s["stato"].startswith("NOVA · SEC 10-K/10-Q CIK 0009990003 · non disponibile: in attesa del primo confronto")


def test_finestra_locale_dichiarata_come_non_abbinati():
    from bellomberg.market_data.filing_diff import LIMITE_FINESTRA
    diff = {"stato": "ok", "sezioni_confrontate": ["rischi"], "limiti": [LIMITE_FINESTRA],
            "cambiamenti": [{"tipo": "aggiunto", "dopo": _cit("Kore export rules " * 30)}]}
    s = fc.scheda_da_run("KORE.MI", profilo=PROFILO_SEC, run=_run_diff(diff), ultimo=None, escluso=False,
                         freschezza=None, novita_dopo=None)
    assert "rimossi e aggiunti non abbinati" in s["stato"]


def test_get_filing_changes_ordine_per_punteggio(tmp_path):
    # Filing reali: centinaia di cambiamenti; il desk deve poter leggere i piu' importanti per primi.
    path, store = _archivio_n(tmp_path, n=1)
    r = store.list_runs("NOVA.DE", limit=1)[0]
    lunghezze = [50, 400, 120, 400, 10]  # aggiunti in rischi: punteggio ~ lunghezza
    cambi = [{"tipo": "aggiunto", "dopo": _cit("x" * k)} for k in lunghezze]
    run = store.start_run("NOVA.DE")
    store.claim_execution(run["id"])
    result = {**r["result"], "confronto_corrente": {"stato": "ok", "cambiamenti": cambi}}
    store.finish_run(run["id"], status="ok", result=result,
                     judgment={"status": "skipped", "findings": []}, index={"status": "skipped"})
    g = fc.get_filing_changes("NOVA.DE", db_path=path, ordine="punteggio", max_changes=3)
    ids = [c["estratti"]["dopo"]["citation_id"] for c in g["changes"]]
    assert ids == ["C2-dopo", "C4-dopo", "C3-dopo"] and g["order"] == "punteggio"
    assert g["changes"][0]["rank"] == 1 and g["changes"][0]["score"] == 3.0
    coda = fc.get_filing_changes("NOVA.DE", db_path=path, ordine="punteggio", da=4)
    assert [c["estratti"]["dopo"]["citation_id"] for c in coda["changes"]] == ["C1-dopo", "C5-dopo"]
    doc = fc.get_filing_changes("NOVA.DE", db_path=path)
    assert doc["order"] == "documento" and doc["changes"][0]["estratti"]["dopo"]["citation_id"] == "C1-dopo"
    for bad in ("importanza", 3, True):
        assert fc.get_filing_changes("NOVA.DE", db_path=path, ordine=bad)["status"] == "errore"


def test_troncamenti_con_separatore_delle_migliaia():
    # Filing reali: 3.762 cambiamenti omessi; il numero segue lo stesso formato dei caratteri.
    cambi = [{"tipo": "aggiunto", "dopo": _cit(f"r{i}")} for i in range(1205)]
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=_run(cambi), ultimo=None, escluso=False,
                         freschezza=None, novita_dopo=None)
    # budget per la sola base (2 mostrati), come nel caso reale con molti titoli
    testo = fc.impagina([s], max_caratteri=_budget_base(s), intestazione=INTESTAZIONE)["testo"]
    assert "TRONCAMENTI: 1.203 cambiamenti omessi su 1 titolo;" in testo
    assert "altri 1.203 cambiamenti omessi →" in testo


def test_riga_variante_con_separatore_delle_migliaia():
    tanti = [{"tipo": "aggiunto", "dopo": _cit("Rischio annuale")}] * 1421
    annuale = {"tipo": "annuale", "primaria": False, "coppia_periodi": ["2024-12-31", "2025-12-31"],
               "confronto": {"stato": "ok", "cambiamenti": tanti}}
    run = _run([])
    run["result"]["varianti"] = [annuale]
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=run, ultimo=None, escluso=False,
                         freschezza=None, novita_dopo=None)
    assert "· 1.421 cambiamenti →" in s["altra_variante"]


# --- revisione finale (I1): NOVITÀ solo per una coppia di documenti diversa ---

from datetime import timedelta


def _coppia(prima, dopo):
    return {"prima": {"sha256": prima * 64, "metadati": {"periodo_fine": "2025-09-28"}},
            "dopo": {"sha256": dopo * 64, "metadati": {"periodo_fine": "2026-09-27"}}}


def _confronto(store, coppia, ticker="NOVA.DE"):
    result = _run([{"tipo": "aggiunto", "dopo": _cit("Nuovo rischio export " * 30)}])["result"]
    result["coppia"] = coppia
    run = store.start_run(ticker)
    store.claim_execution(run["id"])
    return store.finish_run(run["id"], status="ok", result=result)


def test_novita_solo_se_la_coppia_cambia_dopo_l_ultimo_memo(tmp_path):
    path, store = _archivio_n(tmp_path, n=0)
    _confronto(store, _coppia("a", "b"))
    memo = datetime.now(timezone.utc)
    # stesso confronto ricalcolato dopo il memo (rifacimento settimanale, /A, profilo cambiato...)
    _confronto(store, _coppia("a", "b"))
    pref = tmp_path / "p.json"
    s = fc.schede_filing(["NOVA.DE"], db_path=path, pref_path=pref, novita_dopo=memo)[0]
    assert "NOVITÀ" not in s["stato"] and s["gruppo"] == 1 and s["limite"] == 2
    # documento nuovo dopo il memo: novità
    _confronto(store, _coppia("b", "c"))
    s = fc.schede_filing(["NOVA.DE"], db_path=path, pref_path=pref, novita_dopo=memo)[0]
    assert "NOVITÀ" in s["stato"] and s["gruppo"] == 0 and s["limite"] == 4


def test_novita_senza_confronto_prima_del_memo(tmp_path):
    path, store = _archivio_n(tmp_path, n=0)
    prima = datetime.now(timezone.utc) - timedelta(days=1)
    s = fc.schede_filing(["NOVA.DE"], db_path=path, pref_path=tmp_path / "p.json", novita_dopo=prima)[0]
    assert "NOVITÀ" in s["stato"]  # nessun confronto al momento del memo: primo confronto, nuovo


def _panoramica(tmp_path, monkeypatch, store, memo=None, tickers=("NOVA.DE",)):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api.filing_routes import create_filing_router
    from bellomberg.market_data.filing_service import FilingService
    monkeypatch.setattr("bellomberg.storage.filing_preferenze._path", lambda p=None: tmp_path / "p.json")
    app = FastAPI()
    app.include_router(create_filing_router(
        lambda: None, lambda: FilingService(store, tmp_path / "arch", pipeline=lambda *a, **k: None),
        tickers_portafoglio=lambda: list(tickers), ultima_run_fn=lambda: memo))
    return TestClient(app).get("/filings").json()


def test_novita_panoramica_stessa_regola(tmp_path, monkeypatch):
    path, store = _archivio_n(tmp_path, n=0)
    _confronto(store, _coppia("a", "b"))
    memo = datetime.now(timezone.utc)
    _confronto(store, _coppia("a", "b"))
    titolo = _panoramica(tmp_path, monkeypatch, store, memo)["titoli"][0]
    assert titolo["novita"] is False and titolo["gruppo"] == 1


# --- revisione finale (I2, M3): freschezza e fonte della panoramica dalla scheda ---

def test_panoramica_conta_la_freschezza_della_riga_di_stato(tmp_path, monkeypatch):
    path, store = _archivio_n(tmp_path, n=2)
    run = store.start_run("NOVA.DE")
    store.claim_execution(run["id"])
    store.finish_run(run["id"], status="errore", reason="SEC timeout")      # dopo il confronto
    prof = {**PROFILO_ARCHIVIO, "ticker": "KORE.MI", "cik": "0009990002"}
    store.set_profile("KORE.MI", prof, interval_hours=24)
    run = store.start_run("KORE.MI")
    store.claim_execution(run["id"])
    store.finish_run(run["id"], status="errore", reason="SEC timeout")      # primo run in errore
    out = _panoramica(tmp_path, monkeypatch, store, tickers=("NOVA.DE", "KORE.MI"))
    per = {t["ticker"]: t for t in out["titoli"]}
    assert "NON AGGIORNATO: ultimo controllo in errore" in per["NOVA.DE"]["stato_riga"]
    c = out["copertura"]
    assert c["aggiornati"] == 0 and c["non_aggiornati"] == 1 and c["senza_confronto"] == 1
    assert c["con_confronto"] == 1


def test_scheda_dichiara_freschezza_e_fonte():
    agg = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=_run([]), ultimo=None, novita_dopo=None,
                           freschezza={"stato": "aggiornato", "motivo": None})
    assert agg["fresco"] == "aggiornato" and agg["fonte"] == "SEC 10-Q CIK 0009990001"
    err = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=_run([]), novita_dopo=None,
                           freschezza={"stato": "aggiornato", "motivo": None},
                           ultimo={"id": 9, "status": "errore", "reason": "x", "finished_at": "2026-10-03T09:00:00+00:00"})
    assert err["fresco"] == "non_aggiornato"
    senza = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=None, ultimo=None, novita_dopo=None,
                             freschezza={"stato": "aggiornato", "motivo": None})
    assert senza["fresco"] == "senza_confronto" and senza["fonte"] == "SEC 10-Q CIK 0009990001"
    nessuno = fc.scheda_da_run("NOVA.DE", profilo=None, run=None, ultimo=None, novita_dopo=None, freschezza=None)
    assert nessuno["fresco"] is None and nessuno["fonte"] is None


def test_panoramica_fonte_dei_profili_a_varianti(tmp_path, monkeypatch):
    path, store = _archivio_n(tmp_path, n=1)
    prof = {**store.get_profile("NOVA.DE")["profile"]}
    prof.pop("forme_sec", None)
    prof["tipo"] = "annuale"  # tipo di base diverso dalla variante del confronto
    prof["varianti"] = [{"tipo": "trimestrale", "forme_sec": ["10-Q"]}, {"tipo": "annuale", "forme_sec": ["10-K"]}]
    store.set_profile("NOVA.DE", prof, interval_hours=24)
    titolo = _panoramica(tmp_path, monkeypatch, store)["titoli"][0]
    assert titolo["fonte"] == "SEC 10-Q CIK 0009990001"  # la variante del confronto, non tipo None
    assert titolo["stato_riga"].startswith("NOVA.DE · " + titolo["fonte"] + " · ")


# --- revisione finale (I3): il run del confronto nella riga e nei rinvii, run_id nel tool ---

def test_riga_e_rinvii_portano_il_run_del_confronto():
    cambi = [{"tipo": "aggiunto", "dopo": _cit(f"rischio {i} " * 5)} for i in range(6)]
    run = _run(cambi)
    run["result"]["varianti"] = [
        {"tipo": "trimestrale", "primaria": True},
        {"tipo": "annuale", "primaria": False, "coppia_periodi": ["2024-12-31", "2025-12-31"],
         "confronto": {"stato": "ok", "cambiamenti": [{"tipo": "aggiunto"}]}}]
    s = fc.scheda_da_run("NOVA.DE", profilo=PROFILO_SEC, run=run, ultimo=None, freschezza=None, novita_dopo=None)
    assert " · confronto del 03/10 · run 7 · " in s["stato"]
    testo = fc.impagina([s], max_caratteri=_budget_base(s), intestazione=INTESTAZIONE)["testo"]  # solo la base
    assert ('altri 4 cambiamenti omessi → get_filing_changes(NOVA.DE, run_id=7, ordine="punteggio", '
            'da=3, max_changes=20)') in testo
    assert 'get_filing_changes(NOVA.DE, run_id=7, variante="annuale")' in testo


def test_get_filing_changes_run_id_legge_quel_confronto(tmp_path):
    path, store = _archivio_n(tmp_path, n=3)          # run 1: 3 cambiamenti
    nuovo = _confronto(store, _coppia("a", "b"))       # run 2: 1 cambiamento, piu' recente
    assert fc.get_filing_changes("NOVA.DE", db_path=path)["run_id"] == nuovo["id"]
    vecchio = fc.get_filing_changes("NOVA.DE", db_path=path, run_id=1, ordine="punteggio", da=2, max_changes=20)
    assert vecchio["status"] == "ok" and vecchio["run_id"] == 1 and vecchio["changes_total"] == 3
    assert vecchio["changes_shown"] == 2 and vecchio["latest_run_id"] == nuovo["id"]
    assert "latest_run_id" not in fc.get_filing_changes("NOVA.DE", db_path=path, run_id=nuovo["id"])


def test_get_filing_changes_run_id_non_valido_e_errore(tmp_path):
    path, store = _archivio_n(tmp_path, n=2)
    leggero = _leggero(store)
    store.set_profile("KORE.MI", {**PROFILO_ARCHIVIO, "ticker": "KORE.MI", "cik": "0009990002"})
    altro = _confronto(store, _coppia("c", "d"), ticker="KORE.MI")
    attivo = store.start_run("NOVA.DE")
    for bad in (0, -1, True, "1", 1.0, 999, leggero["id"], altro["id"], attivo["id"]):
        out = fc.get_filing_changes("NOVA.DE", db_path=path, run_id=bad)
        assert out["status"] == "errore" and out["reason"], bad


def test_dispatch_filing_changes_run_id(tmp_path, monkeypatch):
    from bellomberg.agents import chat_tools
    path, store = _archivio_n(tmp_path, n=3)
    _confronto(store, _coppia("a", "b"))
    monkeypatch.setattr("bellomberg.agents.filing_context.SQLITE_PATH", path)
    schema = next(t for t in chat_tools.TOOL_DEFINITIONS if t["name"] == "get_filing_changes")
    assert schema["input_schema"]["properties"]["run_id"]["type"] == "integer"
    assert chat_tools.dispatch("get_filing_changes", {"ticker": "NOVA.DE", "run_id": 1})["data"]["changes_total"] == 3
    assert chat_tools.dispatch("get_filing_changes", {"ticker": "NOVA.DE", "run_id": "1"})["data"]["run_id"] == 1
    for bad in ("abc", 0, 2.5, 99):
        assert chat_tools.dispatch("get_filing_changes", {"ticker": "NOVA.DE", "run_id": bad})["data"]["status"] == "errore"


# --- D2: riempimento del budget per punteggio dopo la base per titolo ---

def _scheda_punteggi(t, gruppo, punteggi, lunghezza=600):
    s = _scheda(t, gruppo, len(punteggi), lunghezza)
    s["run_id"] = 7
    s["cambiamenti"] = [{"riga": f"+ rischi «{t} {'x' * lunghezza}» [C{i}-dopo]", "punteggio": p, "pos": i}
                        for i, p in enumerate(punteggi, 1)]
    s["cambiamenti"].sort(key=lambda c: (-c["punteggio"], c["pos"]))
    return s


def _mostrati(testo, t):
    return sum(1 for r in testo.splitlines() if r.startswith(f"+ rischi «{t} "))


def test_riempimento_del_budget_equo_e_deterministico():
    # NOVA ha 30 cambiamenti tutti piu' importanti dei 5 di KORE: non deve prendersi tutto lo spazio.
    schede = [_scheda_punteggi("NOVA.DE", 1, [3.0 - i * 0.01 for i in range(30)]),
              _scheda_punteggi("KORE.MI", 1, [1.0] * 5), _scheda("ACME.PA", 2, 0)]
    out = fc.impagina(schede, max_caratteri=14_000, intestazione=INTESTAZIONE)
    testo = out["testo"]
    assert out["caratteri"] == len(testo) <= 14_000
    nova, kore = _mostrati(testo, "NOVA.DE"), _mostrati(testo, "KORE.MI")
    assert kore == 5 and 2 < nova < 30                     # oltre la base 2, KORE completo
    assert "get_filing_changes(KORE.MI" not in testo
    assert (f'altri {30 - nova} cambiamenti omessi → get_filing_changes(NOVA.DE, run_id=7, ordine="punteggio", '
            f'da={nova + 1}, max_changes=20)') in testo
    # il prossimo cambiamento di NOVA non entrava: il budget e' davvero pieno
    assert len(testo) + len(schede[0]["cambiamenti"][nova]["riga"]) + 1 > 14_000 - 40
    # righe per titolo ancora in ordine di punteggio (prefisso della lista ordinata)
    righe = [r for r in testo.splitlines() if r.startswith("+ rischi «NOVA.DE ")]
    assert righe == [c["riga"] for c in schede[0]["cambiamenti"][:nova]]
    assert fc.impagina(schede, max_caratteri=14_000, intestazione=INTESTAZIONE)["testo"] == testo


def test_riempimento_a_giri_quota_per_titolo():
    # NOVA sempre piu' importante, ma nel giro ognuno aggiunge al piu' `limite` (2) oltre la base.
    schede = [_scheda_punteggi("NOVA.DE", 1, [3.0] * 30, lunghezza=100),
              _scheda_punteggi("KORE.MI", 1, [1.0] * 5, lunghezza=100)]
    ampio = fc.impagina(schede, max_caratteri=1_000_000, intestazione=INTESTAZIONE)["testo"]
    assert _mostrati(ampio, "NOVA.DE") == 30 and _mostrati(ampio, "KORE.MI") == 5
    # lunghezza della sola base: stesse schede con i soli primi 2 cambiamenti disponibili
    sola_base = [{**s, "cambiamenti": s["cambiamenti"][:2]} for s in schede]
    base = fc.impagina(sola_base, max_caratteri=1_000_000, intestazione=INTESTAZIONE)["caratteri"]
    riga = len(schede[0]["cambiamenti"][0]["riga"]) + 1
    testo = fc.impagina(schede, max_caratteri=base + 4 * riga + 10, intestazione=INTESTAZIONE)["testo"]
    # primo giro completo (2 + 2), poi NOVA non entra e ci si ferma
    assert (_mostrati(testo, "NOVA.DE"), _mostrati(testo, "KORE.MI")) == (4, 4)


def test_budget_piccolo_resta_la_base_tagliata():
    schede = [_scheda_punteggi("NOVA.DE", 0, [3.0] * 30), _scheda_punteggi("KORE.MI", 1, [1.0] * 5)]
    testo = fc.impagina(schede, max_caratteri=2_000, intestazione=INTESTAZIONE)["testo"]
    assert _mostrati(testo, "NOVA.DE") <= 4 and _mostrati(testo, "KORE.MI") <= 2
    assert len(testo) <= 2_000


def test_riempimento_veloce_su_portafoglio_grande():
    import time
    schede = [_scheda_punteggi(f"T{i:02d}.MI", i % 3, [((i * 7 + j * 13) % 97) / 10 for j in range(700)],
                               lunghezza=40) for i in range(80)]
    inizio = time.perf_counter()
    out = fc.impagina(schede, intestazione=INTESTAZIONE)
    assert time.perf_counter() - inizio < 1.0
    assert out["caratteri"] == len(out["testo"]) <= 14_000
