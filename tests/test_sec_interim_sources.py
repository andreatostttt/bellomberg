"""Byte-bound admission of an interim SEC filing, never by form alone."""

from hashlib import sha256

import pytest

from bellomberg.valuation.sec_interim_sources import verify_sec_interim_candidate


def _candidate(path, *, report_date="2025-06-30", issuer="EXAMPLE MATERIALS INC", **changes):
    candidate = {
        "emittente_id": "CIK:0001234567",
        "issuer": issuer,
        "form": "6-K",
        "report_date": report_date,
        "accession": "0001234567-25-000001",
        "filed_date": "2025-10-01",
        "url": "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/interim.htm",
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }
    candidate.update(changes)
    return candidate


def _filing(tmp_path, *, duration="six", end="June 30, 2025", issuer="Example Materials, Inc.",
            statement=True, announcement=False):
    path = tmp_path / "interim.htm"
    title = "Consolidated Condensed Interim Financial Statements" if statement else "Results announcement"
    tables = ("<h2>CONSOLIDATED CONDENSED INTERIM INCOME STATEMENTS</h2>"
              "<p>Revenue 100</p>"
              "<h2>CONSOLIDATED CONDENSED INTERIM STATEMENTS OF OTHER COMPREHENSIVE INCOME</h2>"
              if statement else "<p>Management will discuss results in a conference call.</p>")
    intro = ("This report announces a conference call. " if announcement else "This report contains ")
    path.write_text(
        '<html lang="en"><body>'
        f'<h1>{issuer}</h1><p>{intro}{issuer} {title} for the '
        f'{duration}-month period ended {end}.</p>{tables}</body></html>',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("duration,end,report_date,start,kind", [
    ("three", "March 31, 2025", "2025-03-31", "2025-01-01", "trimestrale"),
    ("six", "June 30, 2025", "2025-06-30", "2025-01-01", "semestrale"),
    ("nine", "September 30, 2025", "2025-09-30", "2025-01-01", "nove_mesi"),
])
def test_consolidated_interim_requires_original_byte_proof(
    tmp_path, duration, end, report_date, start, kind,
):
    path = _filing(tmp_path, duration=duration, end=end)
    result = verify_sec_interim_candidate(_candidate(path, report_date=report_date), path)

    assert result["stato"] == "verificato", result
    assert result["metadati"]["emittente_id"] == "CIK:0001234567"
    assert result["metadati"]["tipo"] == kind
    assert result["metadati"]["periodo_inizio"] == start
    assert result["metadati"]["periodo_fine"] == report_date
    assert result["metadati"]["report_date"] == report_date
    assert result["sha256"] == sha256(path.read_bytes()).hexdigest()
    assert result["sezioni"]["conto_economico"]["stato"] == "ok"
    for proof in result["prove_verifica"].values():
        assert proof["sha256"] == result["sha256"]


@pytest.mark.parametrize("change", [
    {"emittente_id": "CIK:0007654321"},
    {"accession": "0001234567-25-000002"},
    {"url": "https://www.sec.gov/Archives/edgar/data/7654321/000123456725000001/interim.htm"},
    {"url": "https://example.com/Archives/edgar/data/1234567/000123456725000001/interim.htm"},
    {"form": "20-F"},
])
def test_catalog_identity_and_sec_location_must_agree(tmp_path, change):
    path = _filing(tmp_path)
    assert verify_sec_interim_candidate(_candidate(path, **change), path)["stato"] == "unverifiable"


def test_different_issuer_in_bytes_cannot_borrow_catalog_identity(tmp_path):
    path = _filing(tmp_path, issuer="Different Materials, Inc.")
    assert verify_sec_interim_candidate(_candidate(path), path)["stato"] == "unverifiable"


def test_catalog_period_must_match_explicit_duration_in_bytes(tmp_path):
    path = _filing(tmp_path)
    assert verify_sec_interim_candidate(_candidate(path, report_date="2025-09-30"), path)["stato"] == "unverifiable"


def test_changed_source_bytes_fail_receipt_hash_before_classification(tmp_path):
    path = _filing(tmp_path)
    candidate = _candidate(path)
    path.write_bytes(path.read_bytes() + b"<!-- changed -->")
    assert verify_sec_interim_candidate(candidate, path)["stato"] == "unverifiable"


def test_notice_without_statements_is_not_financial(tmp_path):
    path = _filing(tmp_path, statement=False, announcement=True)
    assert verify_sec_interim_candidate(_candidate(path), path)["stato"] == "not_financial"


def test_notice_referring_to_other_financial_statement_format_is_unverifiable(tmp_path):
    path = _filing(tmp_path, statement=False, announcement=True)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "</body>", "<p>Separate financial statements are attached elsewhere.</p></body>"),
        encoding="utf-8")
    assert verify_sec_interim_candidate(_candidate(path), path)["stato"] == "unverifiable"


@pytest.mark.parametrize("header", [
    "CONSOLIDATED STATEMENTS OF INCOME",
    "CONSOLIDATED INCOME STATEMENTS",
    "CONSOLIDATED CASH FLOW STATEMENTS",
    "CONSOLIDATED BALANCE SHEETS",
])
def test_press_release_with_statement_header_but_unknown_bundle_is_unverifiable(tmp_path, header):
    path = _filing(tmp_path, statement=False, announcement=True)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "</body>", f"<h2>{header}</h2><p>Revenue 100</p></body>"),
        encoding="utf-8")
    assert verify_sec_interim_candidate(_candidate(path), path)["stato"] == "unverifiable"


def test_sec_acceptance_time_is_retained_but_malformed_time_is_rejected(tmp_path):
    path = _filing(tmp_path)
    accepted = "2025-10-01T14:21:17.042Z"
    result = verify_sec_interim_candidate(_candidate(path, accepted_at=accepted), path)
    assert result["stato"] == "verificato"
    assert result["accepted_at"] == accepted
    assert result["metadati"]["accepted_at"] == accepted
    invalid = _candidate(path, accepted_at="2025-02-30T14:20:30Z")
    assert verify_sec_interim_candidate(invalid, path)["stato"] == "unverifiable"


def test_form_alone_is_unverifiable(tmp_path):
    path = tmp_path / "interim.htm"
    path.write_text('<html lang="en"><body><h1>Example Materials, Inc.</h1>'
                    '<p>FORM 6-K</p></body></html>', encoding="utf-8")
    assert verify_sec_interim_candidate(_candidate(path), path)["stato"] == "unverifiable"


def test_financial_title_without_tables_is_unverifiable(tmp_path):
    path = _filing(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "CONSOLIDATED CONDENSED INTERIM INCOME STATEMENTS", "EARNINGS RELEASE"), encoding="utf-8")
    assert verify_sec_interim_candidate(_candidate(path), path)["stato"] == "unverifiable"
