"""D9 (voce 9b): la frase dei numeric_claim_gaps in data_gaps e' UNA, leggibile, col totale vero.

La lista tecnica (blackboard `_numeric_claim_gaps`, gate evidence_sufficient) resta intatta:
qui si prova solo la voce che il PM legge nel memo.
"""
from bellomberg.agents import trade_idea
from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: F401
from _smtp_cattura import smtp  # noqa: F401
from test_trade_idea_source_research import frozen_clock  # noqa: F401
from test_trade_idea_store import db_path, migrated  # noqa: F401

LEGACY = ": fonte numerica/tabella senza receipt o EvidenceID verificati"
DOSSIER = [{"key": "desk_crypto", "title": "Desk Crypto"}, {"key": "valuation", "title": "Valutazione"}]


def test_cinque_righe_identiche_diventano_una_voce_col_conteggio():
    gaps = ["summary" + LEGACY] * 5
    frase = trade_idea._numeric_gaps_phrase(gaps, {"dossier": DOSSIER})
    assert frase == ("Numeri non attestati da una fonte verificata, quindi non usabili per "
                     "l'operativita': 5 (sintesi 5).")
    assert frase.count("sintesi") == 1
    assert "EvidenceID" not in frase and "receipt" not in frase


def test_totale_vero_oltre_i_primi_cinque_e_ordine_di_comparsa():
    gaps = (["summary" + LEGACY] * 5 + ["dossier.desk_crypto[0]" + LEGACY] * 2
            + ["dossier.valuation.table[1]: valore 123,45 non attestato dalla fonte citata"])
    frase = trade_idea._numeric_gaps_phrase(gaps, {"dossier": DOSSIER})
    assert ": 8 (sintesi 5, sezione «Desk Crypto» 2, sezione «Valutazione» 1)." in frase


def test_nomi_leggibili_per_le_posizioni_v4():
    gaps = ["pillars[0].thesis: cifre senza EvidenceID verificati nel campo o nella sezione",
            "pillars[0].risk: valore 7% non attestato dalle evidence_ids del campo",
            "scenarios[2].drivers[1]: cifre senza EvidenceID verificati nel campo o nella sezione",
            "variant_view[0].consensus: valore 1.5 non attestato dalle evidence_ids della riga",
            "proposal.rationale" + LEGACY, "pm_view_response" + LEGACY, "horizon.label" + LEGACY]
    frase = trade_idea._numeric_gaps_phrase(gaps, {})
    assert ("(pilastro 1 2, scenario 3 1, stima divergente dal consenso 1 1, proposta operativa 1, "
            "risposta alla tua tesi 1, orizzonte 1)") in frase
    assert ": 7 (" in frase


def test_posizione_sconosciuta_resta_visibile_con_la_chiave_tecnica():
    gaps = ["campo_nuovo[3]" + LEGACY, "dossier.desk_zz[0]" + LEGACY, "testo senza separatore"]
    frase = trade_idea._numeric_gaps_phrase(gaps, {"dossier": DOSSIER})
    assert "posizione non riconosciuta «campo_nuovo[3]» 1" in frase
    # Sezione senza titolo nel memo: la chiave tecnica, non un nome inventato.
    assert "sezione «desk_zz» 1" in frase
    assert "posizione non riconosciuta «testo senza separatore» 1" in frase
    assert ": 3 (" in frase


def test_lista_al_tetto_dichiara_almeno():
    gaps = ["summary" + LEGACY] * trade_idea._NUMERIC_GAPS_CAP
    frase = trade_idea._numeric_gaps_phrase(gaps, {})
    assert ": almeno 20 (sintesi 20); il conteggio si ferma al tetto di 20 dell'elenco tecnico." in frase
    under = trade_idea._numeric_gaps_phrase(gaps[:19], {})
    assert "almeno" not in under and "tetto" not in under


def test_cablaggio_execute_mette_una_sola_voce_e_lascia_la_lista_tecnica(no_workbook_case, monkeypatch):
    case = no_workbook_case
    technical = ["summary" + LEGACY] * 5 + ["dossier.desk_crypto[0]" + LEGACY] * 2
    monkeypatch.setattr(trade_idea, "_numeric_claim_gaps", lambda *args, **kwargs: list(technical))
    ident = case.current.create_run(case.request, idempotency_key="frase-unica")["run"]["id"]
    detail = case.execute(ident, "d9frase")
    voci = [gap for gap in detail["result"]["data_gaps"] if "Numeri non attestati" in gap]
    assert len(voci) == 1, detail["result"]["data_gaps"]
    assert ": 7 (sintesi 5, " in voci[0]
    assert not any("fonte numerica/tabella" in gap for gap in detail["result"]["data_gaps"])
    # La lista tecnica del gate resta quella, completa e non deduplicata.
    assert detail["progress"]["checkpoint"]["data"]["_numeric_claim_gaps"] == technical
    assert detail["progress"]["routing_checks"]["evidence_sufficient"] is False
