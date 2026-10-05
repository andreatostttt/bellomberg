"""Documento ESEF dai text block dello xBRL-JSON (fase B, task 3). Dati sintetici."""
import hashlib
import json

import pytest

from bellomberg.market_data import filing_esef
from bellomberg.market_data.filing_diff import confronta_documenti
from tests.filing_esef_sintetici import LEI_KORE, LEI_NOVA, json_nova, xbrl_json

URL = "https://filings.xbrl.org/x/nova.json"


def _profilo(lingua="en"):
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    return profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua=lingua)


def _doc(tmp_path, raw, anno=2025, lingua="en", catalogo=None):
    p = tmp_path / f"nova-{anno}-{hashlib.sha256(raw).hexdigest()[:8]}.json"
    p.write_bytes(raw)
    catalogo = catalogo if catalogo is not None else {"period_end": f"{anno}-12-31", "language": lingua}
    return filing_esef.documento_esef(p, url=URL, profilo=_profilo(lingua), catalogo=catalogo), p


# --- testo dei blocchi --------------------------------------------------------------------------

def test_html_non_spezza_le_parole_e_separa_i_paragrafi():
    t = filing_esef.testo_html('<div><p>Nova<span class="_ _3"></span>tech S.p.A.</p><p>Second&nbsp;line</p>'
                               '<table><tr><td>a</td><td>b</td></tr></table></div>')
    assert t == "Novatech S.p.A.\nSecond line"  # tabelle escluse (numeri dai fatti XBRL)


def test_blocchi_solo_ifrs_senza_dimensioni_sul_periodo_corrente():
    b = filing_esef.blocchi(json.loads(json_nova(2025)))
    concetti = [x["concetto"] for x in b["blocchi"]]
    assert "ifrs-full:DisclosureOfLeasesExplanatory" not in concetti          # dimensionale
    assert "nova:DisclosureOfSpecialItemsExplanatory" not in concetti         # estensione
    assert "ifrs-full:DisclosureOfGoingConcernExplanatory" not in concetti    # comparativo
    assert b["periodo"] == ("2025-01-01", "2025-12-31") and b["lingue"] == {"en"}
    assert b["entita"] == {LEI_NOVA}


def test_nomi_di_sezione_e_priorita():
    b = filing_esef.blocchi(json.loads(json_nova(2025)))
    sezioni = [x["sezione"] for x in b["blocchi"]]
    assert sezioni[0] == "rischi finanziari"
    assert "contenziosi e passività potenziali" in sezioni
    assert sezioni[-1] == "principio: revenue"
    assert "nota: treasury shares" in sezioni


def test_frasi_ripetute_restano_solo_nella_nota_piu_specifica():
    credito = "<p>Credit risk is managed with limits.</p><p>Exposures are reviewed monthly.</p>"
    raw = json.loads(xbrl_json(2025, blocchi={
        "DisclosureOfFinancialRiskManagementExplanatory": credito + "<p>Interest rate risk is hedged.</p>",
        "DisclosureOfCreditRiskExplanatory": credito,  # annidato nel precedente
        "DisclosureOfSummaryOfSignificantAccountingPoliciesExplanatory": "<p>Policies text is long enough here.</p>",
        "DescriptionOfAccountingPolicyForDerecognitionOfFinancialInstrumentsExplanatory": "<p>Policies text is long enough here.</p>",
    }))
    b = filing_esef.blocchi(raw)
    per = {x["sezione"]: x["testo"] for x in b["blocchi"]}
    assert per["rischio di credito"] == "Credit risk is managed with limits.\nExposures are reviewed monthly."
    assert per["rischi finanziari"] == "Interest rate risk is hedged."
    politiche = [x for x in b["blocchi"] if x["sezione"].startswith("principio")]
    assert len(politiche) == 1
    assert len(b["scartati"]) == 1 and "gia' presente in" in b["scartati"][0]["motivo"]


def test_ripetizioni_nella_stessa_nota_restano():
    raw = json.loads(xbrl_json(2025, blocchi={
        "DisclosureOfFinancialRiskManagementExplanatory": "<p>See note 3.</p><p>Risk A.</p><p>See note 3.</p>"}))
    assert filing_esef.blocchi(raw)["blocchi"][0]["testo"] == "See note 3.\nRisk A.\nSee note 3."


def test_nota_ristrutturata_tra_due_anni_non_crea_aggiunti(tmp_path):
    """Caso reale (sonda): rischio di credito a se' nel 2024, dentro i rischi finanziari nel 2025."""
    credito = "<p>The Group monitors credit exposure to retailers.</p><p>Collateral is requested above thresholds.</p>"
    altro = "<p>The Group is exposed to interest rate risk.</p>"
    r24, _ = _doc(tmp_path, xbrl_json(2024, blocchi={"DisclosureOfCreditRiskExplanatory": credito,
                                                    "DisclosureOfFinancialRiskManagementExplanatory": altro}), anno=2024)
    r25, _ = _doc(tmp_path, xbrl_json(2025, blocchi={"DisclosureOfFinancialRiskManagementExplanatory": altro + credito}))
    prima, dopo = filing_esef.allinea_sezioni(r24["documento"], r25["documento"])
    diff = confronta_documenti(prima, dopo)
    testi = [(c.get("dopo") or c.get("prima"))["testo"] for c in diff["cambiamenti"] if c["tipo"] != "spostato"]
    assert not any("credit exposure" in t or "Collateral" in t for t in testi), diff["cambiamenti"]


def test_tetto_del_documento_in_ordine_di_priorita(monkeypatch):
    monkeypatch.setattr(filing_esef, "MAX_CARATTERI_DOC", 400)
    raw = json.loads(xbrl_json(2025, blocchi={
        "DisclosureOfFinancialRiskManagementExplanatory": "<p>" + "Risk words here. " * 15 + "</p>",
        "DescriptionOfAccountingPolicyForRevenueExplanatory": "<p>" + "Policy words. " * 30 + "</p>",
        "DisclosureOfContingentLiabilitiesExplanatory": "<p>Short claim note.</p>",
    }))
    testo, sezioni, esclusi = filing_esef.componi(filing_esef.blocchi(raw)["blocchi"])
    assert sezioni["rischi finanziari"]["stato"] == "ok"
    assert sezioni["contenziosi e passività potenziali"]["stato"] == "ok"
    assert sezioni["principio: revenue"] == {"stato": "non_disponibile", "motivo": "oltre il limite del documento"}
    assert esclusi == ["principio: revenue"]
    assert len(testo) <= 400


# --- documento e prove ----------------------------------------------------------------------------

def test_documento_con_offset_letterali_e_sha_dei_byte(tmp_path):
    raw = json_nova(2025)
    r, p = _doc(tmp_path, raw)
    assert r["stato"] == "ok", r
    doc = r["documento"]
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest() and doc["url"] == URL
    assert doc["metadati"] == {"emittente_id": f"LEI:{LEI_NOVA}", "lingua": "en", "tipo": "annuale",
                               "perimetro": "consolidato", "periodo_inizio": "2025-01-01", "periodo_fine": "2025-12-31"}
    s = doc["sezioni"]["rischi finanziari"]
    assert "commodity prices" in doc["estrazione"]["testo"][s["inizio"]:s["fine"]]
    assert "treasury" in doc["estrazione"]["testo"][s["inizio"]:s["fine"]]
    assert set(doc["prove_verifica"]) >= {"emittente", "periodo", "lingua", "tipo", "perimetro"}
    assert doc["prove_verifica"]["emittente"]["supporto"] == "xbrl-json"


def test_lei_diverso_rifiutato(tmp_path):
    r, _ = _doc(tmp_path, json_nova(2025, entita=LEI_KORE))
    assert r["stato"] == "non_verificato" and "LEI" in r["motivi"][0]


def test_periodo_diverso_dal_catalogo_rifiutato(tmp_path):
    r, _ = _doc(tmp_path, json_nova(2025), catalogo={"period_end": "2024-12-31"})
    assert r["stato"] == "non_verificato" and "comparativo" in r["motivi"][0]


def test_altra_lingua_non_applicabile(tmp_path):
    raw = json_nova(2025, lingua="it")
    r, _ = _doc(tmp_path, raw, catalogo={"period_end": "2025-12-31", "language": "en"})
    assert r["stato"] == "non_applicabile" and "lingua" in r["motivi"][0]


def test_deposito_senza_lingua_nel_nome_usa_la_sua_unica_lingua(tmp_path):
    r, _ = _doc(tmp_path, json_nova(2025, lingua="it"), catalogo={"period_end": "2025-12-31", "language": None})
    assert r["stato"] == "ok" and r["documento"]["metadati"]["lingua"] == "it"


def test_nessun_text_block_non_verificato(tmp_path):
    r, _ = _doc(tmp_path, xbrl_json(2021, blocchi={}, numeri={"Revenue": 1.0}), anno=2021)
    assert r["stato"] == "non_verificato" and "FY2022" in r["motivi"][0]


def test_json_illeggibile_non_verificato(tmp_path):
    r, _ = _doc(tmp_path, b"{non json")
    assert r["stato"] == "non_verificato"


# --- confronto con il motore filing_diff -------------------------------------------------------

def test_due_anni_confrontati_con_lo_stesso_motore(tmp_path):
    a, _ = _doc(tmp_path, json_nova(2024), anno=2024)
    b, _ = _doc(tmp_path, json_nova(2025), anno=2025)
    prima, dopo = filing_esef.allinea_sezioni(a["documento"], b["documento"])
    diff = confronta_documenti(prima, dopo)
    assert diff["stato"] == "ok", diff["motivi"]
    tipi = {(c["tipo"], (c.get("dopo") or c.get("prima"))["sezione"]) for c in diff["cambiamenti"]}
    assert ("modificato", "rischi finanziari") in tipi
    assert ("aggiunto", "rischi finanziari") in tipi
    assert ("aggiunto", "nota: treasury shares") in tipi            # nota nuova = testo aggiunto
    for c in diff["cambiamenti"]:
        for lato, doc in (("prima", prima), ("dopo", dopo)):
            if lato in c:
                cit = c[lato]
                assert doc["estrazione"]["testo"][cit["inizio"]:cit["fine"]] == cit["testo"]


# --- numeri -------------------------------------------------------------------------------------

def test_numeri_della_coppia_dal_json_archiviato(tmp_path):
    a, pa = _doc(tmp_path, json_nova(2024), anno=2024)
    b, pb = _doc(tmp_path, json_nova(2025), anno=2025)
    coppia = {"prima": {"path": str(pa), "metadati": a["documento"]["metadati"]},
              "dopo": {"path": str(pb), "metadati": b["documento"]["metadati"]}}
    n = filing_esef.numeri_esef(coppia)
    assert n["stato"] == "ok" and n["valuta"] == "EUR" and n["fonte"].startswith("ESEF")
    ricavi = next(v for v in n["voci"] if v["voce"] == "ricavi")
    assert ricavi["prima"] == 1000e6 and ricavi["dopo"] == 1150e6 and ricavi["delta_pct"] == 15.0


def test_numeri_voce_assente_in_un_anno_esclusa(tmp_path):
    a, pa = _doc(tmp_path, xbrl_json(2024, blocchi={"DisclosureOfFinancialRiskManagementExplanatory": "<p>The Group x y z.</p>"},
                                     numeri={"Revenue": 10.0}), anno=2024)
    b, pb = _doc(tmp_path, xbrl_json(2025, blocchi={"DisclosureOfFinancialRiskManagementExplanatory": "<p>The Group x y z.</p>"},
                                     numeri={"Revenue": 12.0, "ProfitLoss": 3.0}), anno=2025)
    coppia = {"prima": {"path": str(pa), "metadati": a["documento"]["metadati"]},
              "dopo": {"path": str(pb), "metadati": b["documento"]["metadati"]}}
    n = filing_esef.numeri_esef(coppia)
    assert [v["voce"] for v in n["voci"]] == ["ricavi"]


def test_numeri_json_mancante_dichiarato(tmp_path):
    coppia = {"prima": {"path": str(tmp_path / "x.json"), "metadati": {"periodo_inizio": "2024-01-01", "periodo_fine": "2024-12-31"}},
              "dopo": {"path": str(tmp_path / "y.json"), "metadati": {"periodo_inizio": "2025-01-01", "periodo_fine": "2025-12-31"}}}
    with pytest.raises(OSError):
        filing_esef.numeri_esef(coppia)
