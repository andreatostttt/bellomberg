"""Impianto B (PM 04/10/2026): one display transform, shared by renderer and integrity check.

Synthetic data only: fictitious company, tools and numbers.
"""
import re

import pytest
from pypdf import PdfReader

from bellomberg.reporting import trade_idea_report as report
from bellomberg.reporting.trade_idea_report import _Sources, _clean, _localize_cell, build_trade_idea_report
from test_trade_idea_report_v2 import editorial_fixture

HASH = "ab12cd34" * 8


def _sources(result=None, language="it"):
    return _Sources(result or {"evidence": []}, None, language)


def test_citations_leave_the_prose_and_are_registered_in_first_appearance_order():
    sources = _sources()
    text = "Margine in salita [src: tool_alfa, 01/02/2026] e cassa [src: tool_beta; tool_alfa]."
    # Impianto M (PM 04/10/2026): no superscripts, the citation leaves the prose but is registered.
    assert _clean(text, sources, "it", plain=True) == "Margine in salita e cassa."
    assert sources.order == ["tool_alfa", "tool_beta"] and sources.dates["tool_alfa"] == ["01/02/2026"]
    assert _clean(text, _sources(), "it") == "Margine in salita e cassa."


def test_hashes_backticks_and_jargon_are_removed_or_translated():
    sources = _sources()
    text = (f"Giudizio WATCH, proposal=null. Il dossier sigillato (dossier {HASH}, tesi {HASH}) e' in "
            f"`needs_verification`; impronta ab12cd34... nota. Objection X: Answer: concessa. Status: conceded")
    shown = _clean(text, sources, "it", plain=True)
    assert "OSSERVARE" in shown and "nessuna proposta operativa" in shown
    assert not re.search(r"[0-9a-f]{8,}", shown) and "`" not in shown and "()" not in shown
    assert "da verificare" in shown and "Obiezione X: Risposta:" in shown and "Stato: concessa" in shown
    assert "Il dossier sigillato e' in" in shown
    # English keeps its words; only the technical tokens change.
    assert "WATCH" in _clean("WATCH, proposal=null", _sources(language="en"), "en", plain=True)


def test_a_hex_looking_word_without_letters_or_short_is_left_alone():
    sources = _sources()
    assert _clean("Ordini 12345678901234567890123456789012345 pezzi; cafe ok", sources, "it", plain=True) \
        == "Ordini 12345678901234567890123456789012345 pezzi; cafe ok"


@pytest.mark.parametrize("cell, expected", [
    ("243.17", "243,17"), ("48215", "48.215"), ("2024", "2024"), ("-7.214%", "-7,214%"),
    ("7.315.204.661", "7.315.204.661"), ("23.61 / 37.08", "23,61 / 37,08"), ("02/10/2026", "02/10/2026"),
    ("1234567.5", "1.234.567,5"), ("Q2 2026", "Q2 2026")])
def test_contract_dot_decimal_cells_print_in_italian(cell, expected):
    assert _localize_cell(cell, "it") == expected
    assert _localize_cell(cell, "en") == cell


def test_qualifiers_after_a_tool_stay_with_it_and_a_descriptive_citation_is_its_own_source():
    sources = _sources()
    shown = _clean("A [src: tool_alfa calcolo, percentuali, 03/04/2026] B [src: fatti verificati FRED]",
                   sources, "it", plain=True)
    assert sources.order == ["tool_alfa", "fatti verificati FRED"] and shown == "A B"


@pytest.mark.parametrize("prose, expected", [
    ("P/E 31.47 e P/B 6.82", "P/E 31,47 e P/B 6,82"),          # 2 decimals: never a thousands group
    ("EPS 3.8124 atteso", "EPS 3,8124 atteso"),                 # 4+ decimals
    ("quick ratio 0.613", "quick ratio 0,613"),                 # integer part zero
    ("debito 7.315.204.661 EUR", "debito 7.315.204.661 EUR"),   # Italian thousands untouched
    ("ricavi 2.871 milioni", "ricavi 2.871 milioni"),           # ambiguous 3 decimals, not a tool value
    ("EV/EBITDA 17.384 pieno", "EV/EBITDA 17,384 pieno"),       # ambiguous but IS a tool value
    ("vedi https://example.org/v1.25/doc", "vedi https://example.org/v1.25/doc"),
    ("versione v2.5 del modello", "versione v2.5 del modello")])
def test_italian_prose_converts_only_numbers_that_cannot_be_thousands(prose, expected):
    sources = _Sources({"evidence": []}, None, "it", facts={"fundamentals": {"ev_to_ebitda": 17.384}})
    assert _clean(prose, sources, "it", plain=True) == expected
    assert _clean(prose, _Sources({"evidence": []}, None, "en"), "en", plain=True) == prose


@pytest.mark.parametrize("cell", ["0700.HK", "US0378331005", "037833100", "6758.T", "0958"])
def test_cell_codes_are_never_renumbered(cell):
    assert _localize_cell(cell, "it") == cell


@pytest.mark.parametrize("prose", ["Nota 7.2 del bilancio", "Item 2.02 dell'8-K", "alle ore 14.15",
                                   "versione 2.0", "L'organico conta 2.500 dipendenti"])
def test_references_and_italian_thousands_are_not_decimals(prose):
    sources = _Sources({"evidence": []}, None, "it", facts={"risk": {"beta": 2.5}, "x": 2500})
    assert _clean(prose, sources, "it", plain=True) == prose


@pytest.mark.parametrize("text, expected", [
    ("Apple WATCH Series 9", "Apple WATCH Series 9"),
    ("Objection handling is key", "Objection handling is key"),
    ("we conceded nothing", "we conceded nothing"),
    ("Giudizio: WATCH, proposal=null", "Giudizio: OSSERVARE, nessuna proposta operativa"),
    ("Objection INT-05 (fundamentals): x. Answer: conceded.", "Obiezione INT-05 (fundamentals): x. Risposta: concessa."),
    ("l`azienda e` solida, l`altra no", "l'azienda e' solida, l'altra no")])
def test_jargon_is_translated_only_in_its_technical_form(text, expected):
    assert _clean(text, _sources(), "it", plain=True) == expected


def test_citation_qualifiers_survive_in_the_source_list():
    sources = _sources()
    _clean("x [src: open_company_source 10-K 2025 p. 47] y [src: get_fundamentals, nota 12, 01/02/2026]",
           sources, "it", plain=True)
    assert sources.dates["open_company_source"] == ["10-K 2025 p. 47"]
    assert sources.dates["get_fundamentals"] == ["nota 12", "01/02/2026"]


def test_page_one_never_prints_a_half_citation_and_scenario_names_are_transformed(tmp_path):
    run, result = editorial_fixture()
    result["review_conditions"] = ["Verificare che il margine resti sopra la soglia del piano industriale del gruppo "
                                   "[src: tool_alfa, 2026-01-01] e che la cassa segua gli utili nei prossimi trimestri."]
    result["scenarios"][0]["name"] = "Caso base (EV/EBITDA 8.5x) [src: tool_alfa]"
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "cut.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    text = _extract(artifact["path"])
    assert "[src" not in text and "8,5x" in text


def test_memo_facts_failure_is_a_declared_gap_and_is_logged(monkeypatch, caplog):
    from bellomberg.agents import trade_idea
    from bellomberg.reporting import trade_idea_facts

    def broken(checkpoint, cutoff=None):
        raise ValueError("synthetic bug")
    monkeypatch.setattr(trade_idea_facts, "extract_facts", broken)
    facts = trade_idea._memo_facts({"tool_receipts": []}, None)
    assert facts == {"gaps": ["Estrazione dei dati numerici fallita: ValueError: synthetic bug"]}
    assert "facts extraction failed" in caplog.text


def test_memo_facts_reach_the_market_table(tmp_path):
    run, result = editorial_fixture()
    run["facts"] = {"currency": "EUR", "quote": {"price": 12.34, "date": "2026-01-02", "tool": "get_price_live"},
                    "consensus": {"target_mean": 15.5, "target_low": 10, "target_high": 20, "upside_pct": 25.6,
                                  "recommendations": {"strong_buy": 1, "buy": 2, "hold": 3, "sell": 0, "strong_sell": 0},
                                  "tool": "get_consensus_estimates"},
                    "gaps": ["Fondamentali sintetici non disponibili"]}
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "facts.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    flat = " ".join(_extract(artifact["path"]).split())
    assert "12,34 EUR" in flat and "15,50 EUR" in flat and "25,6%" in flat and "3/6" in flat
    assert "Fondamentali sintetici non disponibili" in _extract(artifact["path"])


def _extract(path):
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def test_memo_m_has_header_recommendation_section_sources_and_no_raw_citations(tmp_path):
    run, result = editorial_fixture()
    result["summary"] += f" Dossier {HASH} in `research_required`, WATCH."
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "b.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"
    text = _extract(artifact["path"])
    first = " ".join(PdfReader(artifact["path"]).pages[0].extract_text().split())
    assert "MEMO D'INVESTIMENTO" in first and "1. Raccomandazione" in first and "Osservare" in first
    assert "Condizioni per rivedere" in first and "2. Tesi in sintesi" in first
    assert "[src:" not in text and HASH not in text and "`" not in text
    flat = " ".join(text.split())
    assert "Fonti della sezione: archivio_demo" in flat and "Allegato B" in flat and "1. archivio_demo" in flat


def test_integrity_still_catches_a_renderer_that_drops_a_paragraph(tmp_path, monkeypatch):
    run, result = editorial_fixture()
    original = report._rich_flowables

    def drops_business(text, styles, width, show=None):
        return [] if "Aurora progetta" in str(text) else original(text, styles, width, show=show)
    monkeypatch.setattr(report, "_rich_flowables", drops_business)
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "lost.pdf")
    assert artifact["quality"]["content_integrity"] == "incomplete"
    assert any(name.startswith("business.") for name in artifact["quality"]["integrity_missing"])


def test_integrity_catches_a_renderer_that_prints_raw_citations(tmp_path, monkeypatch):
    run, result = editorial_fixture()
    monkeypatch.setattr(report, "_shown", lambda value, sources, language, cell=False: report._text(value))
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "raw.pdf")
    assert artifact["status"] == "partial"
    assert any("Marcatori di fonte crudi" in reason for reason in artifact["quality"]["reasons"])
