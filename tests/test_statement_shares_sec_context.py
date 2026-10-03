"""Statement shares keep their exact single-source proof when SEC filings are present."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest

from test_statement_shares_class_capital import _class_sources, TITLE, ISSUER, OPENING


CUTOFF = date(2026, 9, 1)


def _fixture(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    source, tagged, _ = _class_sources(tmp_path)
    doc = normalize_statement_shares(source, [tagged])["documents"][0]
    catalog, issues, _ = _catalog([source, tagged, doc], CUTOFF)
    assert not issues
    item = {"value": 600., "kind": "historical", "evidence_ids": [doc["id"]],
        "calculation": {"type": "statement_shares", "fact_index": 0,
            "selection_basis": "primary_inline_without_same_date_tag", "acknowledged_conflicts": []},
        "rationale": "Synthetic observed Class A count; no point-in-time diluted claim.",
        "valid_until": CUTOFF.isoformat(),
        "valid_until_basis": {"policy": "same_day", "as_of": CUTOFF.isoformat()}}
    return source, doc, catalog, item


def _compile_shares(item, catalog, filings, *, share_class=TITLE):
    from bellomberg.valuation.input_preparation import _compile
    return _compile({"model": {"shares": item}, "scenarios": {s: {} for s in ("bear", "base", "bull")}},
        {"shares": ("shares", "million shares", "common", "opening", "number", "model")}, {},
        {"entity": ISSUER, "currency": "USD", "share_class": share_class},
        {"valuation_date": OPENING}, None, catalog, CUTOFF, method="operating_fcff", sec_filings=filings)


def test_statement_share_recipe_accepts_selected_sec_filing_context(tmp_path):
    from bellomberg.valuation.input_preparation import _selected_sec_filings
    source, doc, catalog, item = _fixture(tmp_path)
    report = {"selection": {"selected_document_id": source["id"], "selected_document_ids": [source["id"]]}}
    filings = _selected_sec_filings(catalog, report, "operating_fcff")
    assert [f["id"] for f in filings] == [source["id"]]
    original = deepcopy((item, catalog, filings))
    rows, issues, _ = _compile_shares(item, catalog, filings)
    assert not issues, issues
    assert len(rows) == 1 and rows[0]["value"] == 600.
    assert rows[0]["kind"] == "historical"
    assert (item, catalog, filings) == original
    assert "issued minus" in rows[0]["rationale"].lower()


@pytest.mark.parametrize("fault,reason", [
    ("extra_explicit_source", "single common-share observation"),
    ("mixed_pointer", "without mixed proof fields"),
    ("missing_selection", "explicit opening statement share selection required"),
    ("wrong_value", "valore"),
])
def test_statement_share_integration_does_not_relax_authored_proofs(tmp_path, fault, reason):
    source, doc, catalog, item = _fixture(tmp_path)
    if fault == "extra_explicit_source":
        item["evidence_ids"].append(source["id"])
    elif fault == "mixed_pointer":
        item["evidence_pointer"] = {"value": "/facts/0/value", "unit": "/facts/0/unit", "period": "/facts/0/end"}
    elif fault == "missing_selection":
        del item["calculation"]
    else:
        item["value"] = 601.
    rows, issues, _ = _compile_shares(item, catalog, [catalog[source["id"]]])
    assert not rows and len(issues) == 1, issues
    assert reason in issues[0]["reason"], issues


def test_raw_tag_share_evidence_still_uses_selected_filing(tmp_path):
    from test_statement_shares_inline import _sources
    from bellomberg.valuation.input_preparation import _catalog
    source, tagged, _ = _sources(tmp_path, tag_value=24_147_000_000)
    body = json.loads(tagged["text"])
    body["facts"][2]["observation"]["form"] = source["metadata"]["form"]
    tagged["text"] = json.dumps(body)
    tagged["sha256"] = sha256(tagged["text"].encode()).hexdigest()
    catalog, issues, _ = _catalog([source, tagged], CUTOFF)
    assert not issues
    item = {"value": 24147., "kind": "historical", "evidence_ids": [tagged["id"]],
        "evidence_pointer": {"value": "/facts/2/observation/val", "unit": "/facts/2/unit", "period": "/facts/2/observation/end"},
        "quoted_value": 24_147_000_000, "quoted_unit": "shares", "rationale": "Synthetic observed common shares.",
        "valid_until": CUTOFF.isoformat(), "valid_until_basis": {"policy": "same_day", "as_of": CUTOFF.isoformat()}}
    rows, issues, _ = _compile_shares(item, catalog, [catalog[source["id"]]], share_class="Common Stock")
    assert not issues and len(rows) == 1, issues
    rows, issues, _ = _compile_shares(item, catalog, [], share_class="Common Stock")
    assert not rows and len(issues) == 1
    assert "entita SEC normalizzata" in issues[0]["reason"]


def test_dilution_proxy_branch_keeps_its_native_class_guard():
    from test_diluted_share_estimate import _case, OPENING as proxy_opening
    from bellomberg.valuation.input_preparation import _catalog, _compile
    item, catalog, filings = _case()
    catalog, issues, _ = _catalog(list(catalog.values()), date(2025, 10, 16))
    assert not issues, issues
    filings = [catalog[f["id"]] for f in filings]
    plan = {"model": {"shares": item}, "scenarios": {s: {} for s in ("bear", "base", "bull")}}
    schema = {"shares": ("shares", "million shares", "common", "opening", "number", "model")}
    perimeter = {"entity": "SYNTHETIC INC", "currency": "USD", "share_class": "Common Stock"}
    args = (plan, schema, {}, perimeter, {"valuation_date": proxy_opening}, None, catalog, date(2025, 10, 16))
    rows, issues, _ = _compile(*args, method="operating_fcff", sec_filings=filings)
    assert not issues and len(rows) == 1, issues
    assert rows[0]["kind"] == "analyst_estimate"
    perimeter["share_class"] = "Class A Common Stock"
    rows, issues, _ = _compile(*args, method="operating_fcff", sec_filings=filings)
    assert not rows and len(issues) == 1
    assert issues[0]["code"] == "unverified_dilution_estimate"
