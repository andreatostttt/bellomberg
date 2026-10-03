"""PM 03/10/2026: the memo carries every analyst's complete final report after the Capo synthesis."""
import json

from pypdf import PdfReader

from bellomberg.agents import trade_idea
from bellomberg.reporting import trade_idea_report
from bellomberg.reporting.trade_idea_report import build_trade_idea_report
from test_trade_idea_report_v2 import editorial_fixture


def _long_report(desk, paragraphs=40):
    return "\n\n".join(
        f"{desk.capitalize()} paragraph {index}: the desk weighs revenue durability, cash conversion and "
        f"balance sheet risk with sourced evidence [src: tool_{desk}] and explains how unit {index} of the "
        "analysis changes the committee view, including the competing interpretation and its falsifier."
        + (" **Key point:** margins hold." if index % 7 == 0 else "")
        + ("\n- bullet detail for the reader" if index % 5 == 0 else "")
        for index in range(paragraphs))


def _data(missing=()):
    data = {desk: {"1": "Round one " + desk, "2": _long_report(desk)}
            for desk in trade_idea.TRADE_IDEA_DESKS if desk not in missing}
    data["_red_team"] = {"1": json.dumps({"verdict": "The thesis underestimates renewal risk.",
                                          "objections": [{"id": "OBJ-1", "text": "Renewals are negotiable."}]})}
    data["_objections"] = [{"objection": {"id": "OBJ-1", "desk": "fundamentals", "category": "thesis",
                                          "material": True, "objection": "Renewals are negotiable.",
                                          "requested_change": "Quantify renewal pricing."},
                            "response": "Renewal pricing is documented in the filing.", "state": "answered"}]
    data["_decisive_questions"] = ["Do renewals keep their margin?"]
    return data


def test_annex_renders_every_final_report_in_full_and_is_integrity_checked(tmp_path):
    run, result = editorial_fixture()
    annex = trade_idea._desk_annex(_data(missing=("crypto",)))
    assert sum(len(desk["text"]) for desk in annex["desks"]) > 42000
    run["desk_annex"] = annex
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "annex.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"
    text = "\n".join(page.extract_text() or "" for page in PdfReader(artifact["path"]).pages)
    flat = " ".join(text.split())
    for desk in ("macro", "fundamentals", "options"):
        assert f"{desk.capitalize()} paragraph 39:" in flat
    assert "Report finale non disponibile" in flat
    assert "Renewal pricing is documented in the filing." in flat
    assert '{"verdict"' not in flat and "The thesis underestimates renewal risk." in flat
    assert "**" not in flat
    # The annex does not inflate the Capo's analytical pages.
    assert artifact["quality"]["section_pages"]["annex"] > max(
        page for key, page in artifact["quality"]["section_pages"].items() if key in trade_idea_report.DOSSIER_KEYS)


def test_a_dropped_annex_block_is_caught_as_lost_text(tmp_path, monkeypatch):
    run, result = editorial_fixture()
    run["desk_annex"] = trade_idea._desk_annex(_data())
    import sys
    original = trade_idea_report._annex_blocks

    def renderer_drops_one(annex, language):
        blocks = original(annex, language)
        # The renderer silently loses one block; the integrity check still gets the full list.
        if sys._getframe(1).f_code.co_name == "_render_company_memo":
            return [row for row in blocks if row[0] != "annex.quant.12"]
        return blocks
    monkeypatch.setattr(trade_idea_report, "_annex_blocks", renderer_drops_one)
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "lost.pdf")
    assert artifact["quality"]["content_integrity"] == "incomplete"
    assert "annex.quant.12" in artifact["quality"]["integrity_missing"]


def test_annex_comes_from_the_persisted_checkpoint_shape():
    data = json.loads(json.dumps(_data()))  # round keys become strings, as in the stored checkpoint
    annex = trade_idea._desk_annex(data)
    assert [desk["status"] for desk in annex["desks"]] == ["ready"] * len(trade_idea.TRADE_IDEA_DESKS)
    assert all(str(desk["round"]) == "2" for desk in annex["desks"])
    # No checkpoint is an annex gap, not a claim that every desk failed.
    assert "unavailable" in trade_idea._desk_annex(None) and "desks" not in trade_idea._desk_annex(None)


def test_pipe_tables_become_tables_bold_is_drawn_and_wrapped_dashes_are_not_lost(tmp_path):
    run, result = editorial_fixture()
    table = ("Sintesi dei desk:\n| Desk | Vista finale | Evidenza |\n|---|---|---|\n"
             "| Macro | Neutrale con rischio tassi elevato sul ciclo europeo | [src: macro_tool] |\n"
             "| Quant | Volatilita' implicita sopra la media storica | [src: quant_tool] |\n"
             "Fine della tabella con **conclusione** in grassetto.")
    result["dossier"][0]["paragraphs"].append(table)
    run["desk_annex"] = trade_idea._desk_annex(_data())
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "tables.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    reader = PdfReader(artifact["path"])
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "| Desk |" not in text and "Vista finale" in text and "**" not in text
    fonts = {str(font) for page in reader.pages
             for font in ((page.get("/Resources") or {}).get("/Font") or {}).values()
             for font in [font.get_object().get("/BaseFont")]}
    assert any("Bold" in font or "bd" in font.lower() for font in fonts), fonts
    from bellomberg.reporting.trade_idea_report import _normalized_text
    # A prose dash that wraps to the start of a printed line is content, not a list marker.
    assert _normalized_text("- conferma attesa", rendered=True) == _normalized_text("testo - conferma attesa")[5:]


def test_chart_accepts_an_unambiguous_decimal_comma_and_refuses_thousand_separators():
    import pytest
    from bellomberg.reporting.trade_idea_report import _chart
    section = {"tables": [{"columns": ["Anno", "Ricavi"], "rows": [["2024", "1250,2"], ["2025", "1310,7"]]}]}
    chart = {"table_index": 0, "value_columns": [1], "label_column": 0, "kind": "bar", "title": "Ricavi"}
    assert _chart(section, chart, 400, "Helvetica") is not None
    section["tables"][0]["rows"][0][1] = "1.250,2"
    with pytest.raises(ValueError):
        _chart(section, chart, 400, "Helvetica")
