"""Normal desk tools expose retained source failures without repeating HTTP."""
from copy import deepcopy
from functools import partial
import json
from threading import RLock
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.company_source_research import ResearchSession
from bellomberg.agents.specialists.base import Specialist
from test_company_source_research import FrozenTransport, html, TEXT, DOCUMENT, SITE
from test_trade_idea_source_research import admitted, frozen_clock


def read_catalog(desk, ticker):
    offset, fragments = 0, []
    while True:
        page = desk._execute_meta_tool('read_company_dossier',
            {'ticker': ticker, 'section': 'catalog', 'offset': offset})
        assert page['ok'], page
        fragments.append(page['text'])
        if page['complete']:
            return json.loads(''.join(fragments))
        assert page['next_offset'] > offset
        offset = page['next_offset']


def idea_desk(tmp_path, transport):
    from test_trade_idea_economic import providers_for
    providers = providers_for()
    profile = providers['profile']
    def issuer_profile(*args, **kwargs):
        result = profile(*args, **kwargs)
        result['data']['info']['website'] = SITE
        return result
    providers['profile'] = issuer_profile
    admission = admitted(tmp_path / 'archive', providers=providers)
    assert admission['status'] == 'research_required'
    # The real admission fixes identity, observed quote and original grant.
    board = SimpleNamespace(run_scope='trade_idea', run_id='source-failure-recovery',
        source_admission=admission, source_qualification=deepcopy(admission),
        data={}, _lock=RLock(), current_round=0, model_roots=[tmp_path / 'run'],
        valuation_results={}, budget_gate=SimpleNamespace(
            store=SimpleNamespace(get_run=lambda run_id: {'run': {}}),
            wrap_client=lambda client, **kwargs: client))
    board.persist_run_checkpoint = lambda event: None
    factory = partial(ResearchSession, download=transport)
    trade_idea._bind_trade_idea_source_research(board, archive_root=tmp_path / 'archive',
        artifact_dir=tmp_path / 'run', session_factory=factory)
    return Specialist(board, client=object()), board


def test_trade_idea_normal_dossier_and_source_tools_recover_missing_metadata_explicitly(tmp_path):
    text = TEXT + '\nAn older report was as of 2024-12-31.\n'
    transport = FrozenTransport({'/investors/report.html': (html(text), 'text/html')})
    desk, board = idea_desk(tmp_path, transport)
    original = deepcopy(board.source_admission)
    ticker = original['ticker']
    rejected = desk._execute_meta_tool('acquire_company_source', {'ticker': ticker, 'source': {'url': DOCUMENT}})
    assert rejected['ok'] is False and rejected['status'] == 'needs_verification'
    dossier = read_catalog(desk, ticker)
    assert dossier['documents'] == [] and dossier['records_count'] == 0
    diagnostic = dossier['acquisition_diagnostics'][0]
    assert diagnostic['url'] == DOCUMENT and 'Reporting period' in diagnostic['reason']
    assert 'open_company_source' in diagnostic['recovery_instruction']
    navigation = desk._execute_meta_tool('open_company_source', {'ticker': ticker, 'url': diagnostic['url']})
    assert navigation['ok'] is True and navigation['replayed'] is True
    assert 'Year ended 2025-12-31' in navigation['text']
    request = {'url': DOCUMENT, 'report_date': '2025-12-31', 'report_date_quote': 'Year ended 2025-12-31'}
    accepted = desk._execute_meta_tool('acquire_company_source', {'ticker': ticker, 'source': request})
    assert accepted['ok'] is True, accepted
    current = read_catalog(desk, ticker)
    assert len(current['documents']) == 1 and current['records_count'] == 0
    assert current['acquisition_diagnostics'][0]['status'] == 'retained_rejection_with_verified_bytes'
    assert board.source_admission == original
    assert board.data['_source_research']['parent_grant_fingerprint'] == original['fingerprint']
    assert desk._execute_meta_tool('acquire_company_source', {'ticker': ticker, 'source': request})['replayed']
    assert len(transport.requests) == 1


def test_trade_idea_dossier_exposes_pending_request_and_never_dispatches_it_again(tmp_path):
    class Crash(BaseException):
        pass
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(args[0])
        raise Crash()
    desk, board = idea_desk(tmp_path, interrupted)
    ticker = board.source_admission['ticker']
    with pytest.raises(Crash):
        desk._execute_meta_tool('open_company_source', {'ticker': ticker, 'url': DOCUMENT})
    for _ in range(2):
        dossier = read_catalog(desk, ticker)
        diagnostic = dossier['acquisition_diagnostics'][0]
        assert diagnostic['status'] == 'pending_request' and diagnostic['url'] == DOCUMENT
        assert 'no automatic repeat' in diagnostic['reason']
        assert 'metadata' in diagnostic['recovery_instruction'].lower()
        assert dossier['documents'] == [] and dossier['records_count'] == 0
    assert calls == [DOCUMENT]
