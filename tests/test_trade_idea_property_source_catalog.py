"""A verified primary catalog is evidence, never economic readiness by itself."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from bellomberg.valuation.preparation_historical_sources import collect_trade_idea_sources
from bellomberg.valuation.property_reported_sources import SOURCE_URL


def filing(tmp_path):
    raw = b"Synthetic primary statement; it is not the reviewed public PDF edition."
    path = tmp_path / "primary.txt"
    path.write_bytes(raw)
    candidate = {"stato": "verificato", "url": SOURCE_URL, "path": str(path),
        "sha256": sha256(raw).hexdigest(), "filed_date": "2026-07-31",
        "metadati": {"issuer": "SYNTH-REPORTED-PROP", "form": "public_document",
                     "report_date": "2026-06-30"}}
    return {"ticker": "SYNTH-REPORTED-PROP", "candidati": [candidate], "motivi": []}


def no_archived_model(*_a, **_k):
    pytest.fail("Current document acquisition may not depend on an archived valuation")


def test_trade_idea_retains_verified_reported_property_raw_for_separate_economic_proof(tmp_path):
    supplied = filing(tmp_path)
    calls = []
    def catalog(ticker):
        calls.append(ticker)
        return {"stato": "errore", "documenti": [], "motivi": ["Synthetic SEC coverage unavailable"]}
    def unavailable(*_a, **_k):
        pytest.fail("SEC facts, market references and historical Yahoo close do not define this NAV snapshot")
    result = collect_trade_idea_sources(supplied["ticker"], as_of="2026-09-28", archive_root=tmp_path,
        method_id="property_nav", financial_currency="EUR", filing_results=[supplied],
        catalog=catalog, seed_loader=no_archived_model, facts_fetch=unavailable, price_fetch=unavailable,
        download=unavailable)
    assert calls == [supplied["ticker"]]
    assert not result["preparation_ready"] and "source_plan" not in result
    assert result["status"] == "partial"  # The SEC coverage gap remains visible.
    assert len(result["documents"]) == result["coverage"]["accepted"] == 1
    document = result["documents"][0]
    assert Path(document["archive_path"]).is_relative_to(tmp_path)
    assert sha256(Path(document["archive_path"]).read_bytes()).hexdigest() == document["document_sha256"]
    assert "Synthetic primary statement" in document["text"]
    assert result["trade_idea_document_catalog"]["economic_qualification"] == "not_assessed"
    assert result["issues"] and supplied["candidati"][0]["stato"] == "verificato"


@pytest.mark.parametrize("fault", ["changed_raw", "publication_after_cutoff", "outside_archive"])
def test_trade_idea_catalog_cannot_retain_unverified_property_bytes(tmp_path, fault):
    supplied = filing(tmp_path)
    candidate = supplied["candidati"][0]
    if fault == "changed_raw":
        Path(candidate["path"]).write_bytes(b"Changed primary")
    elif fault == "publication_after_cutoff":
        candidate["filed_date"] = "2026-09-29"
    else:
        candidate["path"] = str(tmp_path.parent / "outside-primary.txt")
    result = collect_trade_idea_sources(supplied["ticker"], as_of="2026-09-28", archive_root=tmp_path,
        method_id="property_nav", filing_results=[supplied], seed_loader=no_archived_model,
        catalog=lambda *_: {"stato": "ok", "documenti": [], "motivi": []})
    assert not result["documents"] and not result["preparation_ready"]
    assert result["coverage"]["accepted"] == 0 and result["coverage"]["excluded"] == 1
    assert result["issues"] and "source_plan" not in result


def test_retaining_primary_document_does_not_qualify_a_fake_reported_property_edition(tmp_path):
    from bellomberg.valuation.preparation_reported_property import assemble_reported_property
    from test_property_reported_nav import synthetic_bundle
    supplied = filing(tmp_path)
    report = collect_trade_idea_sources(supplied["ticker"], as_of="2026-09-28", archive_root=tmp_path,
        method_id="property_nav", filing_results=[supplied], seed_loader=no_archived_model,
        catalog=lambda *_: {"stato": "ok", "documenti": [], "motivi": []})
    result = assemble_reported_property(synthetic_bundle(), report, as_of="2026-09-28")
    assert result["reported_property_historical_assembly"]["status"] == "blocked"
    assert not result["preparation_ready"] and "source_plan" not in result


@pytest.mark.parametrize("method,url", [("property_nav", "https://example.org/other-property-primary"),
                                      ("operating_fcff", SOURCE_URL)])
def test_unrelated_sources_and_methods_keep_the_existing_common_collector(tmp_path, monkeypatch, method, url):
    from bellomberg.valuation import preparation_sources
    supplied = filing(tmp_path)
    supplied["candidati"][0]["url"] = url
    calls = []
    def common(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return {"status": "incomplete", "preparation_ready": False, "documents": [], "issues": []}
    monkeypatch.setattr(preparation_sources, "collect_preparation_evidence", common)
    result = collect_trade_idea_sources(supplied["ticker"], as_of="2026-09-28", archive_root=tmp_path,
        method_id=method, filing_results=[deepcopy(supplied)], seed_loader=lambda *_a, **_k: [])
    assert len(calls) == 1 and calls[0][1]["filing_results"] == [supplied]
    assert not result["documents"] and not result["preparation_ready"]
    assert "trade_idea_document_catalog" not in result
