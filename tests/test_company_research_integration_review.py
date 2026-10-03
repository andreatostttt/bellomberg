"""Independent integration regressions: frozen bytes and temporary run storage."""
from copy import deepcopy
from functools import partial
import json
from types import SimpleNamespace

import pytest

from test_company_source_research import (
    FrozenTransport, IDENTITY, SITE, PAGE, DOCUMENT, TEXT, html,
)


def test_native_long_ir_page_keeps_links_and_pagination_inside_tool_cap(tmp_path):
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.agents.specialists.base import Blackboard, Specialist, _tetto_tool_result
    transport = FrozenTransport({'/investors/': (
        b'<html><body><p>' + b'Official investor relations navigation. ' * 700
        + b'</p><a href="report.html">Annual financial statements</a></body></html>', 'text/html')})
    session = ResearchSession(run_id='bounded-navigation-review', ticker='SYNTH',
        identity=deepcopy(IDENTITY), as_of='2026-09-28', admission_fingerprint='a' * 64,
        archive_root=tmp_path / 'archive', run_dir=tmp_path / 'run', issuer_website=SITE,
        download=transport)
    board = Blackboard()
    board.company_source_session = lambda ticker: session
    desk = Specialist(board, client=object())
    result = desk._execute_meta_tool('open_company_source', {'ticker': 'SYNTH', 'url': PAGE})
    assert result['ok'] is True and result['financial_evidence'] is False
    assert len(transport.requests) == 1
    # This is the exact JSON framing and final limit in Specialist._run_loop.
    delivered = json.dumps(result, default=str, ensure_ascii=False)[:_tetto_tool_result()]
    assert '"next_offset":' in delivered, 'Native framing discarded the pagination pointer'
    assert DOCUMENT in delivered, 'Native framing discarded the official report link'


def test_weekly_source_pin_cannot_regress_when_callbacks_arrive_out_of_order(tmp_path):
    from bellomberg.agents.company_research_tools import bind_weekly_company_research
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.agents.specialists.base import Blackboard
    saved, snapshots = {}, []
    store = SimpleNamespace(run_id='weekly-concurrent-revisions',
        context={'research_started_at': '2026-09-28T12:00:00+00:00'},
        db=SimpleNamespace(db_path=str(tmp_path / 'book.sqlite')),
        get=lambda key: deepcopy(saved.get(key)))
    store.complete = lambda key, value, board: saved.setdefault(key, deepcopy(value))
    store.save_snapshot = lambda board: snapshots.append(deepcopy(board.data))
    transport = FrozenTransport({
        '/investors/report.html': (html(TEXT), 'text/html'),
        '/investors/second.html': (html(TEXT.replace('Revenue 100', 'Revenue 102')), 'text/html'),
    })
    board = Blackboard()
    bind_weekly_company_research(board, store,
        identity_resolver=lambda ticker: deepcopy(IDENTITY),
        profile_provider=lambda *a, **k: {'status': 'ok', 'data': {'info': {'website': SITE}}},
        session_factory=partial(ResearchSession, download=transport))
    session = board.company_source_session('SYNTH')
    assert session.acquire({'url': DOCUMENT})['ok']
    first = session.snapshot()
    assert session.acquire({'url': SITE + '/investors/second.html'})['ok']
    second = session.snapshot()
    assert first['revision_id'] != second['revision_id']
    # Deterministic legal thread interleaving: A obtained snapshot A, B then
    # acquired and saved B, and finally A resumes its delayed callback.
    board.on_company_sources_changed(session, second)
    board.on_company_sources_changed(session, first)
    assert board.data['_company_research']['SYNTH']['revision_id'] == second['revision_id']
    assert snapshots[-1]['_company_research']['SYNTH']['revision_id'] == second['revision_id']
    assert len(transport.requests) == 2


def test_weekly_64k_request_cannot_exceed_provider_completion_cap(tmp_path):
    import httpx
    import pytest
    from bellomberg.core.llm_client import OpenRouterClient, request_scope
    from bellomberg.core.request_journal import RequestJournal
    dispatches = []
    def send(request):
        dispatches.append(request)
        return httpx.Response(200, json={'id': 'unreachable-provider', 'model': 'test/review-model',
            'choices': [{'message': {'content': 'Synthetic result'}, 'finish_reason': 'stop'}],
            'usage': {'cost': 0.00003}})
    journal = RequestJournal(tmp_path / 'requests.sqlite', run_id='weekly-token-review',
        authorization={'scope': 'weekly', 'source': 'offline-review'}, authorized_usd='1',
        metadata=lambda model: {'id': model, 'context_length': 131072,
            'top_provider': {'max_completion_tokens': 32768},
            'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    client = OpenRouterClient(api_key='offline', trasporto=httpx.MockTransport(send))
    with request_scope(journal, phase='weekly', agent='fundamentals', round_n=1):
        with pytest.raises(ValueError, match='completion|cap|output'):
            client.messages.create(model='test/review-model', max_tokens=64000,
                                   messages=[{'role': 'user', 'content': 'Frozen source context'}])
    assert not dispatches
    assert journal.summary()['request_count'] == 0


def test_trade_idea_source_pin_and_dossier_cannot_regress_after_delayed_callback(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from threading import RLock
    from bellomberg.agents import trade_idea
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.valuation import trade_idea_model
    from test_sector_analysis import DAY
    from test_trade_idea_economic import IDENTITY as economic_identity, providers_for
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(DAY + 'T12:00:00+00:00').astimezone(tz or timezone.utc)
    monkeypatch.setattr(trade_idea_model, 'datetime', Clock)
    providers = providers_for()
    original = providers['profile']
    def profile(*args, **kwargs):
        result = original(*args, **kwargs)
        result['data']['info']['website'] = SITE
        return result
    providers['profile'] = profile
    admission = trade_idea_model.research_admission(economic_identity['ticker'], economic_identity, DAY,
        archive_root=tmp_path / 'archive', providers=providers)
    assert admission['status'] == 'research_required', admission['reasons']
    frozen_admission = deepcopy(admission)
    snapshots = []
    board = SimpleNamespace(run_id='trade-idea-concurrent-sources', source_admission=admission,
        source_qualification=deepcopy(admission), data={}, _lock=RLock(), current_round=0,
        model_roots=[tmp_path / 'run'], valuation_results={},
        budget_gate=SimpleNamespace(store=SimpleNamespace(get_run=lambda run_id: {'run': {}})))
    board.persist_run_checkpoint = lambda event: snapshots.append(deepcopy(board.data))
    transport = FrozenTransport({
        '/investors/report.html': (html(TEXT), 'text/html'),
        '/investors/second.html': (html(TEXT.replace('Revenue 100', 'Revenue 102')), 'text/html'),
    })
    trade_idea._bind_trade_idea_source_research(board, archive_root=tmp_path / 'archive',
        artifact_dir=tmp_path / 'run', session_factory=partial(ResearchSession, download=transport))
    session = board.company_source_session(economic_identity['ticker'])
    assert session.acquire({'url': DOCUMENT})['ok']
    first = session.snapshot()
    assert session.acquire({'url': SITE + '/investors/second.html'})['ok']
    second = session.snapshot()
    board.on_company_sources_changed(session, second)
    board.on_company_sources_changed(session, first)
    assert board.data['_source_research']['revision_id'] == second['revision_id']
    assert snapshots[-1]['_source_research']['revision_id'] == second['revision_id']
    assert len(board.source_qualification['source_report']['documents']) == 2
    assert board.source_admission == frozen_admission and len(transport.requests) == 2


def test_source_acquisition_waits_for_compiler_lock_before_get_or_admission():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, RLock
    from bellomberg.agents.company_research_tools import dispatch_company_source
    entered, downloaded = Event(), Event()
    lock = RLock()
    calls = []
    def acquire(source):
        calls.append(deepcopy(source))
        downloaded.set()
        return {'ok': True}
    session = SimpleNamespace(acquire=acquire, snapshot=lambda: {'ticker': 'SYNTH'})
    board = SimpleNamespace(current_round=1, valuation_results={}, _source_research_lock=lock,
        company_source_session=lambda ticker: session)
    def changed(*args):
        with lock:
            if board.valuation_results:
                raise ValueError('Acquired bytes arrived after model creation')
    board.on_company_sources_changed = changed
    def dispatch():
        entered.set()
        return dispatch_company_source(board, 'acquire_company_source',
            {'ticker': 'SYNTH', 'source': {'url': DOCUMENT}}, max_chars=12000)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lock:
            future = pool.submit(dispatch)
            assert entered.wait(timeout=2)
            # While the actual compiler owns this lock, no new GET/admission
            # can race its frozen source set. Give the competing thread a turn.
            downloaded.wait(timeout=0.2)
            board.valuation_results['SYNTH'] = {'generation_id': 'just-compiled'}
        result = future.result(timeout=2)
    assert result['ok'] is False
    assert not calls, 'The GET/document journal changed while its model was being compiled'


@pytest.mark.parametrize('first', ['acquisition', 'model'])
def test_common_native_dispatch_serializes_model_recording_and_source_admission(tmp_path, first):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, RLock
    from bellomberg.agents.company_research_tools import dispatch_company_source
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.agents.specialists.base import Specialist
    transport = FrozenTransport()
    entered, release, second_started = Event(), Event(), Event()
    records = []
    def download(*args, **kwargs):
        if first == 'acquisition':
            entered.set()
            assert release.wait(timeout=3)
        return transport(*args, **kwargs)
    session_args = dict(run_id='native-dispatch-' + first, ticker='SYNTH', identity=deepcopy(IDENTITY),
        as_of='2026-09-28', admission_fingerprint='a' * 64, archive_root=tmp_path / 'archive',
        run_dir=tmp_path / 'run', issuer_website=SITE, download=download)
    session = ResearchSession(**session_args)
    board = SimpleNamespace(current_round=1, valuation_results={}, _source_research_lock=RLock(),
        company_source_session=lambda ticker: session, data={})
    def changed(owner, snapshot):
        assert owner is session
        board.data['source_pin'] = snapshot['revision_id']
        records.append(('source', snapshot['revision_id']))
    board.on_company_sources_changed = changed
    desk = object.__new__(Specialist)
    desk.blackboard = board
    def compile_body(name, input_):
        assert name == 'get_valuation'
        if first == 'model':
            entered.set()
            assert release.wait(timeout=3)
        snapshot = session.snapshot(expected_revision_id=board.data.get('source_pin'))
        board.valuation_results['SYNTH'] = {'generation_id': 'frozen-compiler-result',
            'source_revision': snapshot['revision_id'], 'document_count': len(snapshot['documents'])}
        records.append(('model', snapshot['revision_id']))
        return {'ok': True}
    # The compiler body is simulated; its real native caller must keep the
    # source lock through both selecting the snapshot and recording the model.
    desk._execute_meta_tool_unlocked = compile_body
    def acquire():
        return dispatch_company_source(board, 'acquire_company_source',
            {'ticker': 'SYNTH', 'source': {'url': DOCUMENT}}, max_chars=12000)
    def model():
        return desk._execute_meta_tool('get_valuation', {'ticker': 'SYNTH'})
    def second():
        second_started.set()
        return model() if first == 'acquisition' else acquire()
    with ThreadPoolExecutor(max_workers=2) as pool:
        leading = pool.submit(acquire if first == 'acquisition' else model)
        assert entered.wait(timeout=2)
        trailing = pool.submit(second)
        assert second_started.wait(timeout=2)
        release.set()
        assert leading.result(timeout=3)['ok'] is True
        result = trailing.result(timeout=3)
    if first == 'acquisition':
        assert result['ok'] is True and [row[0] for row in records] == ['source', 'model']
        assert board.valuation_results['SYNTH']['document_count'] == 1
        assert len(transport.requests) == 1
    else:
        assert result['ok'] is False and [row[0] for row in records] == ['model']
        assert board.valuation_results['SYNTH']['document_count'] == 0
        assert not transport.requests
    # Reopening the actual durable service does not add a request or change the
    # source revision sealed in the model, even for a second replay.
    for _ in range(2):
        resumed = ResearchSession(**session_args).snapshot(expected_revision_id=board.data.get('source_pin'))
        assert resumed['revision_id'] == board.valuation_results['SYNTH']['source_revision']
    assert len(transport.requests) == (1 if first == 'acquisition' else 0)
