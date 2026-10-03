"""Unreadable PM pointers remain explicit gaps, never accepted source evidence."""
from copy import deepcopy

import pytest

from bellomberg.agents import trade_idea_sources as sources
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.valuation import trade_idea_model as model
from test_trade_idea_pm_sources import IDENTITY, URL, WEBSITE, TEXT, transport, _profile_providers


def test_unparseable_pm_document_allows_research_but_stays_unverified(tmp_path):
    download, calls = transport('Unreadable financial document: no issuer or date proof')
    admitted = sources.ingest_document_sources(IDENTITY['ticker'], IDENTITY, '2026-09-28',
        [{'url': URL}], archive_root=tmp_path, issuer_website=WEBSITE,
        download=download, allow_partial=True)
    receipt = admitted['receipt']
    assert receipt['contract'] == 'fundamentals-research-pm-documents/1'
    assert receipt['documents'][0]['status'] == 'needs_verification'
    assert receipt['documents'][0]['reason']
    assert admitted['documents'] == [] and calls
    with pytest.raises(ValueError):
        sources.verify_document_receipt(receipt, IDENTITY['ticker'], IDENTITY,
            '2026-09-28', archive_root=tmp_path, issuer_website=WEBSITE)
    qualification = model.research_admission(IDENTITY['ticker'], IDENTITY, '2026-09-28',
        archive_root=tmp_path, providers=_profile_providers('2026-09-28'),
        accepted_document_receipt=receipt, analysis_mode=RESEARCH_ANALYSIS_MODE)
    assert qualification['status'] == 'research_required', qualification['reasons']
    assert qualification['source_report']['documents'] == []
    assert qualification['source_report']['issues']
    assert qualification['document_receipt'] == receipt
    assert model.recheck_accepted_sources(qualification, IDENTITY['ticker'], IDENTITY,
        archive_root=tmp_path, allow_historical=True) == qualification
    altered = deepcopy(receipt)
    altered['documents'][0]['request']['url'] = 'https://issuer.example.org/other'
    with pytest.raises(ValueError, match='fingerprint'):
        sources.verify_document_receipt(altered, IDENTITY['ticker'], IDENTITY,
            '2026-09-28', archive_root=tmp_path, issuer_website=WEBSITE, allow_partial=True)


def test_partial_contract_still_rechecks_verified_primary_bytes(tmp_path):
    download, _calls = transport(TEXT)
    admitted = sources.ingest_document_sources(IDENTITY['ticker'], IDENTITY, '2026-09-28',
        [{'url': URL}], archive_root=tmp_path, issuer_website=WEBSITE,
        download=download, allow_partial=True)
    assert admitted['documents']
    from pathlib import Path
    Path(admitted['receipt']['documents'][0]['path']).write_bytes(b'Changed primary')
    with pytest.raises(ValueError, match='SHA256|bytes'):
        sources.verify_document_receipt(admitted['receipt'], IDENTITY['ticker'], IDENTITY,
            '2026-09-28', archive_root=tmp_path, issuer_website=WEBSITE, allow_partial=True)


def test_legacy_unparseable_document_still_blocks_admission(tmp_path):
    download, _calls = transport('Unparseable historical document')
    with pytest.raises(sources.SourceIngestionError):
        sources.ingest_document_sources(IDENTITY['ticker'], IDENTITY, '2026-09-28',
            [{'url': URL}], archive_root=tmp_path, issuer_website=WEBSITE, download=download)
