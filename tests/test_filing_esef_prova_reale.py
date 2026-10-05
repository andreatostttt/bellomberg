"""Correzioni dalla prova reale della fase B (2026-10-03). Dati sintetici che riproducono i casi osservati."""
import json
import sqlite3

import pytest
import requests

import bellomberg.storage.negozi_privati as np_
from bellomberg.market_data import esef, filing_attivazione as fa, filing_identita, sec_edgar
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_esef_sintetici import LEI_NOVA, riga_indice
from tests.filing_sec_sintetici import CIK_NOVA


# --- forma giuridica estesa sul repository ESEF («NOVA - SOCIETA' PER AZIONI») -------------

def test_societa_per_azioni_estesa_e_nome_identico(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    monkeypatch.setattr(np_, "PERCORSO_LEI", str(tmp_path / "lei.json"))

    class R:
        ok, status_code = True, 200

        def json(self):
            return {"data": [{"id": LEI_NOVA, "attributes": {"name": "NOVA - SOCIETA' PER AZIONI", "identifier": LEI_NOVA}}]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    r = esef.candidati_lei("NOVA.MI", "Nova S.p.A.", ritmo=lambda: None)
    assert r["stato"] == "univoco" and r["candidati"][0]["origine"] == "nome"


# --- SEC «univoca» senza depositi utili (ADR OTC: solo F-6 o esenzione 12g3-2(b)) --------------

def _elenco(monkeypatch, righe):
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", lambda: {"righe": righe, "origine": "cache", "motivo": None})
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})


ESEF_UNIVOCO = {"stato": "univoco", "motivo": "nome identico",
                "candidati": [{"lei": LEI_NOVA, "nome": "NOVA S.P.A.", "origine": "nome"}]}


def test_adr_senza_forme_non_e_una_fonte_sec(monkeypatch):
    _elenco(monkeypatch, [{"cik": CIK_NOVA, "ticker": "NOVAY", "nome": "Nova S.p.A./ADR"}])
    monkeypatch.setattr(esef, "candidati_lei", lambda *a, **k: ESEF_UNIVOCO)
    p = filing_identita.proponi("NOVA.MI", "Nova S.p.A.",
                                catalogo_fn=lambda t, cik=None: {"stato": "ok", "documenti": [{"form": "F-6"}]})
    assert p["sec"]["stato"] == "nessuno" and "10-K" in p["sec"]["motivo"]
    assert p["sec"]["scartati"][0]["cik"] == CIK_NOVA
    assert p["esef"]["stato"] == "univoco" and p["preferita"] == "esef"


def test_catalogo_sec_illeggibile_non_scarta_il_candidato_sec(monkeypatch):
    # Revisione G1: col suffisso il nome identico e' «da confermare» (non piu' univoco), quindi si
    # interroga anche l'ESEF; un catalogo illeggibile non scarta il candidato SEC.
    _elenco(monkeypatch, [{"cik": CIK_NOVA, "ticker": "NOVAY", "nome": "Nova S.p.A."}])
    monkeypatch.setattr(esef, "candidati_lei", lambda *a, **k: {"stato": "nessuno", "candidati": [], "motivo": "test"})

    def giu(t, cik=None):
        raise ConnectionError("rete giu'")

    p = filing_identita.proponi("NOVA.MI", "Nova S.p.A.", catalogo_fn=giu)
    assert p["sec"]["stato"] == "ambiguo" and [c["cik"] for c in p["sec"]["candidati"]] == [CIK_NOVA]
    assert p["preferita"] == "sec"


def test_titolo_usa_senza_suffisso_non_legge_il_catalogo(monkeypatch):
    _elenco(monkeypatch, [{"cik": CIK_NOVA, "ticker": "NOVA", "nome": "Nova Inc."}])
    p = filing_identita.proponi("NOVA", "Nova Inc.", catalogo_fn=lambda *a, **k: pytest.fail("catalogo non atteso"))
    assert p["sec"]["stato"] == "univoco" and p["preferita"] == "sec"


# --- SEC solo «nome simile» (un'omonima estera per un emittente italiano) e LEI esatto ---------------------------------

def test_sec_solo_simile_e_lei_esatto_preferisce_esef(monkeypatch):
    _elenco(monkeypatch, [{"cik": CIK_NOVA, "ticker": "NOVC", "nome": "Nova Chile S.A."}])
    monkeypatch.setattr(esef, "candidati_lei", lambda *a, **k: ESEF_UNIVOCO)
    p = filing_identita.proponi("NOVA.MI", "Nova S.p.A.")
    assert p["sec"]["stato"] == "ambiguo" and p["preferita"] == "esef"


def test_preferita():
    sim = {"stato": "ambiguo", "candidati": [{"origine": "nome_simile"}]}
    nome = {"stato": "ambiguo", "candidati": [{"origine": "nome"}, {"origine": "nome"}]}
    amb = {"stato": "ambiguo", "candidati": [{"lei": LEI_NOVA}]}
    assert filing_identita.preferita({"sec": sim, "esef": ESEF_UNIVOCO}) == "esef"
    assert filing_identita.preferita({"sec": sim, "esef": amb}) == "esef"  # revisione: da confermare
    assert filing_identita.preferita({"sec": nome, "esef": ESEF_UNIVOCO}) == "sec"
    assert filing_identita.preferita({"sec": {"stato": "nessuno"}, "esef": amb}) == "esef"
    assert filing_identita.preferita({"sec": {"stato": "nessuno"}, "esef": {"stato": "nessuno"}}) is None
    assert filing_identita.preferita({"sec": {"stato": "errore"}, "esef": ESEF_UNIVOCO}) is None


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


INDICE = lambda lei: {"righe": [riga_indice(2, 2024), riga_indice(4, 2025)], "origine": "rete", "motivo": None}  # noqa: E731


def test_attiva_esef_se_la_sec_e_solo_simile(store, tmp_path):
    def proponi(t, rifiutati=frozenset()):
        p = {"ticker": t, "nome": "Nova S.p.A.", "esef": ESEF_UNIVOCO,
             "sec": {"stato": "ambiguo", "motivo": "nome simile",
                     "candidati": [{"cik": CIK_NOVA, "ticker": "NOVC", "nome": "Nova Chile", "origine": "nome_simile"}]}}
        return {**p, "preferita": filing_identita.preferita(p)}

    r = fa.attiva(store, "NOVA.MI", proponi_fn=proponi, indice_fn=INDICE, pref_path=tmp_path / "p.json")
    assert r["esito"] == "attivato" and r["fonte"] == "esef"


def test_attiva_sec_senza_forme_ripiega_sull_esef(store, tmp_path, monkeypatch):
    monkeypatch.setattr(filing_identita, "proponi_esef", lambda t, n, rifiutati=frozenset(): ESEF_UNIVOCO)

    def proponi(t, rifiutati=frozenset()):
        return {"ticker": t, "nome": "Nova S.p.A.", "preferita": "sec",
                "sec": {"stato": "univoco", "motivo": "nome", "candidati": [{"cik": CIK_NOVA, "ticker": "NOVAY",
                                                                           "nome": "Nova S.p.A.", "origine": "nome"}]}}

    r = fa.attiva(store, "NOVA.MI", proponi_fn=proponi, indice_fn=INDICE, pref_path=tmp_path / "p.json",
                  catalogo_fn=lambda t, cik=None: {"stato": "ok", "documenti": [{"form": "F-6"}]})
    assert r["esito"] == "attivato" and r["fonte"] == "esef"
    assert store.get_profile("NOVA.MI")["profile"]["lei"] == LEI_NOVA


# --- suffisso del nome file che non e' la lingua («<emittente>-2025-12-31-0-IT.json», testo in inglese) ---

def test_suffisso_unico_per_esercizio_non_decide_la_lingua(monkeypatch, tmp_path):
    from bellomberg.market_data import filing_pipeline
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    from tests.filing_esef_sintetici import json_nova
    from tests.test_filing_pipeline import rete
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    monkeypatch.setattr(esef, "attendi_esef", lambda **k: None)
    righe = [riga_indice(2, 2024, lingua="IT"), riga_indice(4, 2025, lingua="IT")]
    monkeypatch.setattr(esef, "indice_depositi", lambda lei, **k: {"righe": righe, "origine": "rete", "motivo": None})
    rete(monkeypatch, {righe[0]["json_url"]: json_nova(2024), righe[1]["json_url"]: json_nova(2025)})
    profilo = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="it")
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "a", oggi="2026-10-03")
    assert out["stato"] == "ok", out["motivi"]
    assert out["coppia"]["dopo"]["metadati"]["lingua"] == "en"
    from bellomberg.market_data.filing_esef import scegli_lingua
    assert scegli_lingua(righe, "en") == "en"


# --- note fatte solo di frasi ripetute altrove: non sono sezioni mancanti ---------------------

def test_note_tutte_ripetute_non_rendono_parziale_il_confronto(tmp_path):
    from bellomberg.market_data import filing_esef
    from bellomberg.market_data.filing_diff import confronta_documenti
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    from tests.filing_esef_sintetici import xbrl_json
    profilo = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en")
    debito = "<p>The Group has a term loan of EUR 500 million.</p>"

    def doc(anno, blocchi):
        p = tmp_path / f"{anno}.json"
        p.write_bytes(xbrl_json(anno, blocchi=blocchi))
        return filing_esef.documento_esef(p, url="https://filings.xbrl.org/x.json", profilo=profilo,
                                          catalogo={"period_end": f"{anno}-12-31"})["documento"]

    # 2025: la nota sui titoli di debito e' fatta solo di frasi gia' in altre note (scartata);
    # 2024: e' una nota a se'. Il testo non sparisce: e' spostato nella nota nuova.
    bond = "<p>The Group issued no bonds.</p>"
    a = doc(2024, {"DisclosureOfBorrowingsExplanatory": debito, "DisclosureOfDebtSecuritiesExplanatory": bond + debito})
    b = doc(2025, {"DisclosureOfBorrowingsExplanatory": debito, "DisclosureOfFinanceCostExplanatory": bond,
                   "DisclosureOfDebtSecuritiesExplanatory": bond + debito})
    assert [s["sezione"] for s in b["esef"]["scartati"]] == ["nota: debt securities"]
    assert "nota: debt securities" not in b["sezioni"]
    diff = confronta_documenti(*filing_esef.allinea_sezioni(a, b))
    assert diff["stato"] == "ok", diff["motivi"]
    assert not [c for c in diff["cambiamenti"] if c["tipo"] in ("aggiunto", "rimosso")], diff["cambiamenti"]


# --- tabelle delle note: «minestre di numeri» che dominavano il punteggio (bilanci convertiti da PDF) -----

def test_tabelle_e_righe_numeriche_escluse_dal_testo():
    from bellomberg.market_data.filing_esef import testo_html
    t = testo_html("<p>Credit risk is monitored.</p><table><tr><td>Performing</td><td>4.0%</td><td>6,664</td></tr></table>"
                   "<div>2023 2024 1,234 (56) 7.8% 910</div><div>Exposure rose in 2024 to EUR 12 million.</div>")
    assert t == "Credit risk is monitored.\nExposure rose in 2024 to EUR 12 million."


# --- contesto: run completo concluso senza coppia, poi controllo leggero (repository fermo) -----------------

def test_scheda_dice_perche_manca_il_confronto_anche_dopo_un_controllo_leggero(tmp_path):
    from bellomberg.agents import filing_context as fc
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    st = FilingStore(path)
    st.set_profile("NOVA.MI", profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova", origine="nome", lingua="en"), interval_hours=24)
    run = st.start_run("NOVA.MI", "scheduled")
    st.claim_execution(run["id"])
    st.finish_run(run["id"], status="parziale", reason="ESEF: repository fermo all'esercizio FY2022",
                  result={"stato": "parziale", "motivi": [], "confronto_corrente": None, "confronto_storico": None},
                  judgment={"status": "skipped", "findings": [], "reason": "x", "model": None, "usage": None},
                  index={"status": "skipped"})
    from tests.test_filing_impronta import _scaduto
    _scaduto(st, finito=True)
    leggero = st.start_run("NOVA.MI", "scheduled")
    st.claim_execution(leggero["id"])
    st.finish_run(leggero["id"], status="skipped", reason="controllato, nessun deposito nuovo",
                  result={"controllo_leggero": True, "run_riferimento": run["id"]})
    scheda = fc.schede_filing(["NOVA.MI"], db_path=path, pref_path=tmp_path / "p.json")[0]
    assert "repository fermo all'esercizio FY2022" in scheda["stato"], scheda["stato"]
    assert "in attesa" not in scheda["stato"]


# --- punteggio: righe di tabella impaginate come testo (PDF) sotto la prosa ----------------------

def test_righe_di_tabella_valgono_meno_della_prosa():
    from bellomberg.agents import filing_context as fc
    tabella = ("Valore Saldo 01.01.2024 Variazioni Saldo 31.12.2024 Entrate Uscite Totale "
               "Contratti Nova 1.234 5.678 9.012 (12) 34 (5) (67) Aumento Riduzione del 5% ") * 3
    prosa = ("The Group could breach a covenant of the term loan if leverage rises above the agreed "
             "threshold, which would trigger early repayment and higher funding costs for the group. ") * 3
    url = "https://filings.xbrl.org/x.json"
    t = {"tipo": "aggiunto", "dopo": {"sezione": "rischi finanziari", "testo": tabella, "url": url}}
    p = {"tipo": "aggiunto", "dopo": {"sezione": "rischi finanziari", "testo": prosa, "url": url}}
    assert fc.punteggio(p) == 3.0
    assert fc.punteggio(t) < fc.punteggio(p) / 2


def test_frase_spezzata_su_due_righe_si_deduplica_come_la_segmenta_il_motore():
    """La stessa frase in due note, a capo in punti diversi (impaginazione PDF)."""
    from bellomberg.market_data import filing_esef
    from tests.filing_esef_sintetici import xbrl_json
    raw = json.loads(xbrl_json(2025, blocchi={
        "DisclosureOfContingentLiabilitiesExplanatory":
            "<div>The auditor noted that Nova had renewed</div><div>its supplier contracts for three years.</div>",
        "DisclosureOfProvisionsExplanatory":
            "<div>The auditor noted that Nova</div><div>had renewed its supplier contracts for three years.</div>"
            "<div>Provisions rose.</div>"}))
    b = filing_esef.blocchi(raw)
    testi = {x["sezione"]: x["testo"] for x in b["blocchi"]}
    assert sum("auditor" in t for t in testi.values()) == 1, testi
