"""The new research contract pins real reports/documents without model artefacts."""
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace

import pytest

from bellomberg.core import research_analysis as research


def board(monkeypatch):
    from bellomberg.valuation import company_dossier
    dossiers = {'TEST': {'ticker': 'TEST', 'contract': 'company_dossier/1',
        'documents': [], 'records': [], 'status': 'research_required',
        'issues': [{'reason': 'Official statements unavailable'}]}}
    monkeypatch.setattr(company_dossier, 'dossier_for_board', lambda _bb, ticker: deepcopy(dossiers[ticker]))
    reports = {'fundamentals': 'Business uncertainty; consensus n.d.; assumptions explicitly conditional.',
               'quant': 'Risk and sizing are separate from research completion.'}
    bb = SimpleNamespace(analysis_mode=research.RESEARCH_ANALYSIS_MODE,
        data={}, target_ticker='TEST', _lock=RLock(), tool_receipts=[],
        read=lambda desk, round_n: reports.get(desk) if round_n == 1 else None)
    return bb, dossiers, reports


def test_mode_is_explicit_and_legacy_is_not_reclassified():
    assert not research.is_research_mode({})
    assert not research.is_research_mode(SimpleNamespace(data={}))
    assert research.is_research_mode({'analysis_mode': research.RESEARCH_ANALYSIS_MODE})


def test_sealed_thesis_preserves_missing_sources_and_excludes_fake_models(monkeypatch):
    bb, dossiers, reports = board(monkeypatch)
    sealed = research.seal_research_thesis(bb, desks=tuple(reports))
    assert sealed['dossiers'] == dossiers and sealed['reports'] == reports
    assert sealed['analysis_mode'] == research.RESEARCH_ANALYSIS_MODE
    reference = research.research_reference(bb)
    assert len(reference['dossier_sha256']) == len(reference['thesis_sha256']) == 64
    assert 'model_ref' not in reference and 'generation_id' not in reference
    assert research.seal_research_thesis(bb, desks=tuple(reports)) == sealed
    assert research.research_context(bb)['research_ref'] == reference


@pytest.mark.parametrize('mutation', ['report', 'document', 'seal'])
def test_research_discussion_cannot_inherit_mutated_thesis(monkeypatch, mutation):
    bb, dossiers, reports = board(monkeypatch)
    research.seal_research_thesis(bb, desks=tuple(reports))
    if mutation == 'report':
        reports['fundamentals'] = 'Changed thesis'
    elif mutation == 'document':
        dossiers['TEST']['documents'] = [{'id': 'new'}]
    else:
        bb.data['_research_thesis']['reports']['fundamentals'] = 'Tampered stored report'
    with pytest.raises(ValueError, match='(differs|changed|integrity)'):
        research.research_reference(bb)


@pytest.mark.parametrize('text', ['', None, '[ERROR provider timeout]'])
def test_empty_or_failed_research_is_not_sealed_as_success(monkeypatch, text):
    bb, _, reports = board(monkeypatch)
    reports['fundamentals'] = text
    with pytest.raises(ValueError, match='(incomplete|unavailable)'):
        research.seal_research_thesis(bb, desks=tuple(reports))


def test_weekly_empty_book_has_explicit_empty_company_scope(monkeypatch):
    bb, _, reports = board(monkeypatch)
    bb.run_scope, bb.target_ticker, bb.research_tickers = 'weekly', None, []
    sealed = research.seal_research_thesis(bb, desks=tuple(reports))
    assert sealed['dossiers'] == {}
    assert sealed['scope_status'] == 'no_company_in_scope'
    assert research.research_reference(bb)['thesis_sha256'] == sealed['thesis_sha256']
