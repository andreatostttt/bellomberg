"""Ordinary specialist source tools, with no provider/network calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest


class Session:
    def __init__(self):
        self.calls = []

    def open_page(self, url, **kwargs):
        self.calls.append(('open', url, kwargs))
        return {'ok': True, 'evidence': False, 'text': 'Issuer investor relations'}

    def search(self, **kwargs):
        self.calls.append(('search', kwargs))
        return {'ok': True, 'links': []}

    def acquire(self, source):
        self.calls.append(('acquire', source))
        return {'ok': True, 'document_id': 'annual'}

    def snapshot(self):
        return {'documents': [], 'revision_id': 'sealed-test-revision'}


def test_sources_are_native_specialist_tools_and_acquisition_updates_shared_board():
    from bellomberg.agents.specialists.base import Blackboard, Specialist
    session = Session()
    board = Blackboard()
    board.current_round = 1
    board.company_source_session = lambda ticker: session if ticker == 'SYNTH' else None
    changes = []
    board.on_company_sources_changed = lambda owner, snapshot: changes.append(deepcopy(snapshot))
    desk = Specialist(board, client=object())
    desk.name = 'fundamentals'
    names = {tool['name'] for tool in desk._build_tools_schema()}
    assert {'open_company_source', 'search_company_sources', 'acquire_company_source'} <= names
    result = desk._execute_meta_tool('open_company_source', {'ticker': 'SYNTH', 'url': 'https://issuer.example/ir'})
    assert result['ok'] and result['evidence'] is False
    result = desk._execute_meta_tool('acquire_company_source', {'ticker': 'SYNTH', 'source': {'url': 'https://issuer.example/report.pdf'}})
    assert result['ok'] and len(changes) == 1
    assert [call[0] for call in session.calls] == ['open', 'acquire']


def test_consultation_and_model_review_cannot_change_sources():
    from bellomberg.agents.specialists.base import Blackboard, Specialist
    session = Session()
    board = Blackboard()
    board.current_round = 1
    board.company_source_session = lambda ticker: session
    desk = Specialist(board, client=object())
    desk._task_context = {'consultation': True}
    result = desk._execute_meta_tool('acquire_company_source', {'ticker': 'SYNTH', 'source': {'url': 'https://issuer.example/report.pdf'}})
    assert result['ok'] is False and not session.calls
    desk._task_context = None
    board.current_round = 2
    result = desk._execute_meta_tool('acquire_company_source', {'ticker': 'SYNTH', 'source': {'url': 'https://issuer.example/report.pdf'}})
    assert result['ok'] is False and not session.calls


def test_research_documents_survive_incomplete_normalization_without_false_readiness():
    from bellomberg.valuation.preparation_service import merge_research_sources
    from test_input_preparation import _documents
    docs = _documents()
    report = {'documents': [], 'status': 'incomplete', 'preparation_ready': False,
              'issues': [{'source': 'balance_sheet', 'reason': 'Needs authored statement proof'}]}
    snapshot = {'documents': docs, 'revision_id': 'sealed', 'ticker': 'SYNTH',
                'as_of': '2026-09-10', 'parent_grant_fingerprint': 'initial-authorization'}
    merged = merge_research_sources(report, snapshot)
    assert merged['documents'] == docs and merged['preparation_ready'] is False
    assert merged['issues'] == report['issues'] and report['documents'] == []
    assert merged['research_revision']['parent_grant_fingerprint'] == 'initial-authorization'
    assert merge_research_sources(merged, snapshot)['documents'] == docs
    conflicting = deepcopy(docs[0]); conflicting['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='document'):
        merge_research_sources({**report, 'documents': [conflicting]}, snapshot)
