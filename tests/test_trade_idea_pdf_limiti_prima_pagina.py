"""Voce 9 (impianto A, scelto 05/10/2026): le lacune dichiarate (data_gaps) in PRIMA PAGINA.

Blocco «LIMITI DI QUESTA ANALISI — N lacune dichiarate» fra l'intestazione e «1. Raccomandazione»;
il sottoparagrafo «Dati mancanti e limiti» della Decisione diventa un rinvio (o l'elenco completo
quando le lacune superano il tetto di 15 della prima pagina). Vale anche per il risultato incompleto
del Capo caduto (nessuna sezione «decision») e per il renderer legacy. La numerazione che il Capo
antepone («1. ») si toglie in stampa E nell'ispettore d'integrita' (stessa trasformazione).

Dati SINTETICI: ticker, societa', lacune e numeri inventati.
"""
import re
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from bellomberg.agents import trade_idea
from bellomberg.reporting.trade_idea_report import build_trade_idea_report
from test_trade_idea_report_v2 import editorial_fixture

TICKER = "QQSYN.MI"
HEAD = "LIMITI DI QUESTA ANALISI"
DOC_GAP = "nessun bilancio o documento ufficiale"


def _fixture(gaps, policy="trade-idea-research/2"):
    run, result = editorial_fixture()
    run.update(ticker=TICKER, execution_policy=policy)
    run["identity"]["name"] = "Societa Sintetica QQ"
    result["ticker"] = TICKER
    result["data_gaps"] = list(gaps)
    return run, result


def _pages(path):
    return [" ".join((page.extract_text() or "").split()) for page in PdfReader(path).pages]


def _build(tmp_path, run, result, name="memo.pdf"):
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / name)
    return artifact, _pages(artifact["path"])


def _decision_ref(result):
    keys = [d["key"] for d in result["dossier"]]
    index = keys.index("decision")
    return f"{4 + index}.{len(result['dossier'][index]['paragraphs']) + 1}"


GAPS = ["Semestrale sintetica al 30/06 non ancora pubblicata: margini del primo semestre non verificati.",
        "Prezzo obiettivo del consenso non disponibile per QQSYN.MI.",
        "Incassi successivi alla chiusura non documentati dalla societa' sintetica."]


def test_complete_memo_prints_the_gaps_on_page_one_before_the_recommendation(tmp_path):
    run, result = _fixture(GAPS)
    artifact, pages = _build(tmp_path, run, result)
    assert artifact["status"] == "ready", artifact["reason"]
    first = pages[0]
    assert HEAD + " — 3 lacune dichiarate" in first
    head, rec = first.index(HEAD), first.index("1. Raccomandazione")
    assert head < rec
    for letter, gap in zip("abc", GAPS):
        position = first.index(f"({letter}) {gap}")
        assert head < position < rec
    # Decisione: rinvio alla prima pagina, il testo delle lacune non si ripete.
    whole = " ".join(pages)
    assert f"{_decision_ref(result)} Dati mancanti e limiti" in whole
    assert "Elencate in prima pagina, «Limiti di questa analisi»." in whole
    for gap in GAPS:
        assert whole.count(gap) == 1, gap


def test_no_gaps_means_no_block(tmp_path):
    run, result = _fixture([])
    artifact, pages = _build(tmp_path, run, result)
    assert artifact["status"] == "ready", artifact["reason"]
    whole = " ".join(pages)
    assert HEAD not in whole and "Dati mancanti e limiti" not in whole


def test_the_capo_numbering_is_removed_in_print_and_the_inspector_stays_ready(tmp_path):
    # Sequenza 1, 2, 3, 4 (anche con la parentesi): e' la numerazione del Capo, si toglie.
    numbered = [f"{i}. {gap}" for i, gap in enumerate(GAPS, 1)] + ["4) Rating sintetico assente."]
    run, result = _fixture(numbered)
    artifact, pages = _build(tmp_path, run, result)
    # L'ispettore confronta la STESSA trasformazione della stampa: niente «testo non integro».
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"
    first = pages[0]
    assert "(a) Semestrale sintetica" in first and "(d) Rating sintetico assente." in first
    assert "1. Semestrale" not in first and "(a) 1." not in first and "4)" not in first
    # Una cifra iniziale che non e' una numerazione resta: «3,5%» non perde nulla.
    run, result = _fixture(["3,5% dei ricavi sintetici senza fonte."])
    artifact, pages = _build(tmp_path, run, result, "cifra.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    assert "(a) 3,5% dei ricavi sintetici senza fonte." in pages[0]


def test_twenty_gaps_print_fifteen_and_a_declared_line_then_the_full_list(tmp_path):
    gaps = [f"Lacuna sintetica numero {i:02d} del titolo inventato." for i in range(1, 21)]
    run, result = _fixture(gaps)
    artifact, pages = _build(tmp_path, run, result)
    assert artifact["status"] == "ready", artifact["reason"]
    first = pages[0]
    assert HEAD + " — 20 lacune dichiarate" in first
    ref = _decision_ref(result)
    line = f"… e altre 5 lacune: elenco completo in §{ref}."
    assert line in first and first.index(line) < first.index("1. Raccomandazione")
    for i in range(1, 16):
        assert f"numero {i:02d} " in first
    for i in range(16, 21):
        assert f"numero {i:02d} " not in first
    # Il paragrafo della Decisione porta l'elenco COMPLETO (mai un taglio silenzioso).
    whole = " ".join(pages)
    section = whole[whole.index(f"{ref} Dati mancanti e limiti"):]
    for gap in gaps:
        assert gap in section, gap
    assert "Elencate in prima pagina" not in whole


def test_repeated_identical_gaps_are_merged_and_counted(tmp_path):
    same = "Fonti numeriche sintetiche non attestate da una ricevuta."
    run, result = _fixture([same, GAPS[0], same, same])
    artifact, pages = _build(tmp_path, run, result)
    assert artifact["status"] == "ready", artifact["reason"]
    first = pages[0]
    assert HEAD + " — 2 lacune dichiarate (4 voci; le ripetute sono accorpate e contate con ×n)" in first
    assert f"(a) {same} (×3)" in first and f"(b) {GAPS[0]}" in first
    assert " ".join(pages).count(same) == 1


def _board(documents):
    data = {desk: {1: f"Tesi sintetica del desk {desk}: testo inventato per la prova.",
                   2: f"Revisione sintetica del desk {desk}: il limite resta dichiarato."}
            for desk in trade_idea.TRADE_IDEA_DESKS}
    data["_research_thesis"] = {"dossiers": {TICKER: {"documents": documents}}}
    return SimpleNamespace(data=data, orari_report={}, analysis_mode="fundamentals_research_v1",
                           target_ticker=TICKER, get_latest=lambda *_a: None)


def _capo_down(policy, extra=()):
    run = {"id": "11111111-1111-4111-8111-111111111111", "ticker": TICKER, "view_text": "View sintetica del PM",
           "language": "it", "analysis_mode": "fundamentals_research_v1", "execution_policy": policy,
           "cutoff": "2026-09-10T12:00:00Z", "identity": {"name": "Societa Sintetica QQ", "exchange": "Borsa inventata",
                                                          "currency": "EUR"}}
    result = trade_idea._incomplete_capo_result(run, _board([]), "Capo sintetico caduto")
    result["data_gaps"].extend(extra)
    return run, result


def test_incomplete_capo_result_declares_the_missing_official_documents():
    _, result = _capo_down("trade-idea-research/3")
    assert DOC_GAP in result["data_gaps"][0]
    assert result["data_gaps"][1].startswith("Output Capo non valido")
    run = {"id": "11111111-1111-4111-8111-111111111111", "ticker": TICKER, "view_text": "v", "language": "it",
           "analysis_mode": "fundamentals_research_v1", "execution_policy": "trade-idea-research/3"}
    covered = trade_idea._incomplete_capo_result(run, _board([{"id": "doc-sintetico"}]), "x")
    assert not any(DOC_GAP in gap for gap in covered["data_gaps"])


@pytest.mark.parametrize("policy", ["trade-idea-research/3", "trade-idea-research/4"])
def test_capo_down_without_decision_section_still_prints_the_block(tmp_path, policy):
    run, result = _capo_down(policy)
    assert "decision" not in [d["key"] for d in result["dossier"]]
    artifact, pages = _build(tmp_path, run, result)
    first = pages[0]
    assert HEAD + " — 2 lacune dichiarate" in first
    rec = first.index("1. Raccomandazione")
    assert first.index(DOC_GAP) < rec and first.index("Output Capo non valido") < rec
    # Nessuna lacuna fra i testi persi dal PDF (il pacchetto incompleto resta «partial» per altri motivi).
    assert not [n for n in artifact["quality"]["integrity_missing"] if n.startswith("data_gaps")]


def test_capo_down_with_twenty_gaps_lists_them_all_in_the_run_data_note(tmp_path):
    extra = [f"Lacuna sintetica aggiuntiva {i:02d}." for i in range(1, 19)]
    run, result = _capo_down("trade-idea-research/3", extra)
    artifact, pages = _build(tmp_path, run, result)
    number = 4 + len(result["dossier"])
    first = pages[0]
    line = f"… e altre 5 lacune: elenco completo in §{number}.1."
    assert HEAD + " — 20 lacune dichiarate" in first and line in first
    whole = " ".join(pages)
    section = whole[whole.index(f"{number}.1 Dati mancanti e limiti — elenco completo"):]
    for gap in result["data_gaps"]:
        assert gap[:60] in section, gap
    # Nessuna lacuna fra i testi persi dal PDF (il pacchetto incompleto resta «partial» per altri motivi).
    assert not [n for n in artifact["quality"]["integrity_missing"] if n.startswith("data_gaps")]


def test_pipeline_gaps_are_counted_on_page_one_and_stay_in_the_run_data_note(tmp_path):
    run, result = _fixture(GAPS)
    run["facts"] = {"gaps": ["Fondamentali sintetici non disponibili.", "Consenso sintetico non disponibile."]}
    artifact, pages = _build(tmp_path, run, result)
    first, whole = pages[0], " ".join(pages)
    found = re.search(r"\+ (\d+) lacune della pipeline dati \(dati che la run non ha procurato\): §(\d+)\.1\.", first)
    assert found and first.index(found.group(0)) < first.index("1. Raccomandazione")
    number = int(found.group(2))
    assert number == 4 + len(result["dossier"])
    note = whole[whole.index(f"{number}.1 Dati che la run non ha procurato"):]
    note = note[:note.index("Allegato B")]
    assert int(found.group(1)) == len(re.findall(r"\([a-z]\) ", note)) >= 2
    assert "Fondamentali sintetici non disponibili." in note


def test_legacy_renderer_prints_every_gap_before_the_contents(tmp_path):
    numbered = ["1. Semestrale sintetica non pubblicata.", "2. Prezzo sintetico non osservato."]
    run, result = _fixture(numbered + [f"Lacuna legacy sintetica {i:02d}." for i in range(1, 19)])
    run.pop("execution_policy")
    artifact, pages = _build(tmp_path, run, result)
    assert HEAD not in pages[0]  # la copertina e' un disegno fisso: il blocco apre la pagina 2
    second = pages[1]
    assert HEAD + " — 20 lacune dichiarate" in second
    assert second.index(HEAD) < second.index("Sommario")
    assert "(a) Semestrale sintetica non pubblicata." in second and "1. Semestrale" not in second
    for i in range(1, 19):
        assert f"Lacuna legacy sintetica {i:02d}." in second
    assert "… e altre" not in second


# ---------------------------------------------------------------- revisione R-9 (06/10/2026)
def test_leading_numbers_that_are_not_a_capo_sequence_stay_intact(tmp_path):
    gaps = ["12. dicembre 2025: assemblea sintetica non ancora verbalizzata.",
            "3) trimestre sintetico non ancora pubblicato.",
            "2025 bilancio sintetico non depositato.",
            "1.200 milioni di debito sintetico senza fonte."]
    run, result = _fixture(gaps)
    artifact, pages = _build(tmp_path, run, result)
    assert artifact["status"] == "ready", artifact["reason"]
    for letter, gap in zip("abcd", gaps):
        assert f"({letter}) {gap}" in pages[0], gap


@pytest.mark.parametrize("count, line", [(15, None), (16, "… e un'altra lacuna: elenco completo in §")])
def test_the_cap_edge_fifteen_fit_sixteen_declare_one_more(tmp_path, count, line):
    gaps = [f"Lacuna breve sintetica {i:02d}." for i in range(1, count + 1)]
    run, result = _fixture(gaps)
    artifact, pages = _build(tmp_path, run, result)
    assert artifact["status"] == "ready", artifact["reason"]
    first = pages[0]
    for i in range(1, 16):
        assert f"sintetica {i:02d}." in first
    if line is None:
        assert "… e" not in first and "Elencate in prima pagina" in " ".join(pages)
    else:
        assert line + _decision_ref(result) + "." in first and "sintetica 16." not in first


def test_long_gaps_are_capped_by_characters_and_the_recommendation_stays_on_page_one(tmp_path):
    filler = ("il dato sintetico non e' stato procurato dalla run e la sezione relativa resta senza verifica "
              "indipendente; servono il documento ufficiale, la data di osservazione e il confronto con la fonte "
              "secondaria prima di qualunque uso operativo della cifra inventata ") * 2
    gaps = [f"Lacuna lunga {i:02d}: {filler.strip()}." for i in range(1, 16)]
    assert all(len(g) > 300 for g in gaps)
    run, result = _fixture(gaps)
    artifact, pages = _build(tmp_path, run, result)
    assert artifact["status"] == "ready", artifact["reason"]
    first = pages[0]
    assert "1. Raccomandazione" in first
    found = re.search(r"… e altre (\d+) lacune: elenco completo in §" + re.escape(_decision_ref(result)) + r"\.", first)
    assert found, first[:3000]
    shown = 15 - int(found.group(1))
    assert 1 <= shown < 15
    assert f"Lacuna lunga {shown:02d}:" in first and f"Lacuna lunga {shown + 1:02d}:" not in first
    whole = " ".join(pages)
    section = whole[whole.index(f"{_decision_ref(result)} Dati mancanti e limiti"):]
    for i in range(1, 16):
        assert f"Lacuna lunga {i:02d}:" in section


def test_an_empty_gap_is_declared_not_a_blank_line(tmp_path):
    run, result = _fixture(["", "", GAPS[0]])
    _, pages = _build(tmp_path, run, result)
    first = pages[0]
    assert "(a) (lacuna senza testo nel risultato) (×2)" in first and f"(b) {GAPS[0]}" in first


def test_v4_pipeline_count_matches_the_note_with_market_pack_missing_and_an_unknown_section(tmp_path):
    from test_trade_idea_report_v4 import v4_result, v4_run
    run, result = v4_run(), v4_result()
    run.pop("market_pack", None)
    result["data_gaps"] = list(GAPS)
    result["dossier"].append({"key": "extra_sintetico", "title": "Approfondimento sintetico",
                              "paragraphs": ["Testo sintetico aggiuntivo per la prova del conteggio."],
                              "evidence_ids": [], "tables": [], "charts": []})
    artifact, pages = _build(tmp_path, run, result)
    first, whole = pages[0], " ".join(pages)
    found = re.search(r"\+ (\d+) lacune della pipeline dati \(dati che la run non ha procurato\): §(\d+)\.1\.", first)
    assert found, first[:3000]
    number = int(found.group(2))
    assert number == 4 + len(result["dossier"])
    note = whole[whole.index(f"{number}.1 Dati che la run non ha procurato"):]
    note = note[:note.index("Allegato B")]
    assert int(found.group(1)) == len(re.findall(r"\([a-z]\) ", note))
    assert "Pacchetto dati di mercato non disponibile" in note and "Approfondimento sintetico" in note
