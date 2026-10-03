"""Weekly binding → shared primary documents → actual preparation/workbook."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

from test_input_preparation import _bundle, _documents, _propose_operating
from test_sector_analysis import DAY
from test_valuation_automation import automation
from test_cablaggio_consigliere_multi import run_offline


class FrozenSession:
    def __init__(self, **kwargs):
        self.config = kwargs
        self.acquired = False

    def acquire(self, source):
        self.acquired = True
        return {'ok': True, 'document_id': 'annual-1'}

    def snapshot(self, *, expected_revision_id=None):
        if expected_revision_id is not None and (not self.acquired or expected_revision_id != 'frozen-revision'):
            raise ValueError('Expected source revision missing')
        return {'run_id': self.config['run_id'], 'ticker': self.config['ticker'],
            'as_of': self.config['as_of'], 'revision_id': 'frozen-revision' if self.acquired else None,
            'revision_sha256': 'a' * 64 if self.acquired else None,
            'parent_grant_fingerprint': self.config['admission_fingerprint'],
            'documents': _documents() if self.acquired else [], 'filing_results': []}


def test_weekly_binding_persists_identity_and_passes_acquired_documents_to_real_compiler(tmp_path, monkeypatch):
    from bellomberg.agents.company_research_tools import bind_weekly_company_research
    from bellomberg.agents.specialists.base import Blackboard, Specialist
    from bellomberg.valuation import preparation_sources
    from bellomberg.valuation.preparation_service import collect_and_prepare
    from bellomberg.valuation.company_dossier import dossier_for_board
    context = {'research_started_at': DAY + 'T12:00:00+00:00', 'portfolio': {}, 'language': 'it'}
    saved, observations = {}, {'identities': 0, 'prepares': 0, 'snapshots': 0}
    store = SimpleNamespace(run_id='weekly-source-fixture', context=context,
        db=SimpleNamespace(db_path=str(tmp_path / 'book.sqlite')), get=lambda key: deepcopy(saved.get(key)))
    store.complete = lambda key, payload, board: saved.setdefault(key, deepcopy(payload))
    store.save_snapshot = lambda board: observations.update(snapshots=observations['snapshots'] + 1)

    def identity(ticker):
        observations['identities'] += 1
        return {'status': 'confirmed', 'ticker': ticker, 'name': 'Synthetic Issuer',
                'exchange': 'SYNTHETIC', 'currency': 'EUR'}

    def collect(*args, **kwargs):
        return {'status': 'incomplete', 'preparation_ready': False, 'documents': [],
                'issues': [{'source': 'normalizer', 'reason': 'Synthetic layout requires author proof'}]}
    monkeypatch.setattr(preparation_sources, 'collect_preparation_evidence', collect)

    def prepare(bundle, **kwargs):
        observations['prepares'] += 1
        assert kwargs['research_sources']['documents'] == _documents()
        return collect_and_prepare(bundle, archive_root=tmp_path / 'archive', output_dir=tmp_path / 'models',
            propose=_propose_operating, research_sources=kwargs['research_sources'])

    board = Blackboard(valuation_preparer=prepare)
    board.current_round = 1
    profile = lambda *a, **k: {'status': 'ok', 'data': {'info': {'website': 'https://issuer.example'}}}
    bind_weekly_company_research(board, store, identity_resolver=identity,
        profile_provider=profile, session_factory=FrozenSession)
    desk = Specialist(board, client=object())
    empty = dossier_for_board(board, 'SYNTH-EXT')
    assert empty['status'] == 'research' and empty['records'] == [] and empty['documents'] == []
    result = desk._execute_meta_tool('acquire_company_source', {'ticker': 'SYNTH-EXT',
        'source': {'url': 'https://issuer.example/annual.pdf'}})
    assert result['ok'] and observations['snapshots'] == 1
    dossier = dossier_for_board(board, 'SYNTH-EXT')
    assert len(dossier['documents']) == 1 and dossier['records'] == []
    model = board.valuation_preparer(_bundle())
    assert model['valuation_usability']['usable'], model.get('error')
    assert Path(model['path']).is_file()
    assert model['preparation']['provenance']['source_acquisition']['preparation_ready'] is False
    assert model['preparation']['provenance']['source_acquisition']['issues'] == collect()['issues']
    assert observations['identities'] == 1
    # A new board reads the pinned issuer; it does not ask live identity again.
    resumed = Blackboard()
    bind_weekly_company_research(resumed, store,
        identity_resolver=lambda _: (_ for _ in ()).throw(AssertionError('identity refetched')),
        profile_provider=profile, session_factory=FrozenSession)
    assert resumed.company_source_session('SYNTH-EXT').config['identity'] == saved['company-source-identity:SYNTH-EXT']['identity']


def test_installation_preparer_uses_run_sources_inside_existing_budget(automation, monkeypatch):
    manager, observed, policy_path, tmp_path = automation
    from bellomberg.valuation import preparation_sources
    policy = json.loads(policy_path.read_text())
    policy['triggers'].append('committee')
    policy_path.write_text(json.dumps(policy), encoding='utf-8')
    monkeypatch.setattr(preparation_sources, 'collect_preparation_evidence', lambda *a, **k:
        {'status': 'incomplete', 'preparation_ready': False, 'documents': [],
         'issues': [{'source': 'opening', 'reason': 'Author must reconstruct'}]})
    sources = {'ticker': 'SYNTH-EXT', 'as_of': DAY, 'documents': _documents(),
               'filing_results': [], 'revision_sha256': 'a' * 64, 'parent_grant_fingerprint': 'b' * 64}
    prepare = manager.runtime.preparer_for('committee')
    result = prepare(_bundle(), research_sources=sources)
    assert result['valuation_usability']['usable'], result.get('error')
    assert Path(result['path']).is_file() and observed['paid']
    audit = manager.runtime.budget_audit()
    assert audit['state'] == 'reconciled' and audit['unresolved_requests'] == 0
    assert audit['authorized_usd'] == '2.50' and float(audit['known_cost_usd']) > 0
    assert json.loads(policy_path.read_text()) == policy


def test_weekly_status_allows_only_run_bound_durable_source_replay(run_offline):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store
    cm.run_multi_agent(send_email=False)
    store = _store()
    board = run_offline.catturato['bb']
    store.complete('company-source-identity:SYNTH', {'identity': {'ticker': 'SYNTH'}})
    board.specialist_checkpoints['fundamentals:R0'] = {'status': 'ready', 'inflight_tools': {
        'intent': {'name': 'acquire_company_source', 'input': {'ticker': 'SYNTH'}}}}
    store.save_snapshot(board)
    assert 'Esito tool incerto' not in str(store.status().get('blocked_reason'))
    board.specialist_checkpoints['fundamentals:R0']['inflight_tools']['intent']['name'] = 'get_valuation'
    store.save_snapshot(board)
    assert 'Esito tool incerto' in store.status()['blocked_reason']
    board.specialist_checkpoints['fundamentals:R0']['inflight_tools']['intent'] = {
        'name': 'acquire_company_source', 'input': {'ticker': 'UNBOUND'}}
    store.save_snapshot(board)
    assert 'Esito tool incerto' in store.status()['blocked_reason']


def test_normal_weekly_sources_to_real_excel_pdf_and_two_delivery_recoveries(run_offline, monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from functools import partial
    from importlib import import_module
    from hashlib import sha256
    import sys
    from bellomberg.agents import consigliere_multi as cm, company_research_tools as bindings, chat_tools, weekly_lifecycle
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.agents.specialists.base import Specialist
    from bellomberg.valuation import preparation_runtime, preparation_sources
    from bellomberg.valuation.preparation_service import collect_and_prepare
    from test_company_source_research import FrozenTransport, html, SITE, PAGE, DOCUMENT
    from test_input_preparation import _operating_plan
    from test_weekly_recovery import _store
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.storage.valuation_versions import ensure_schema
    with MemoryDB()._conn() as conn:
        ensure_schema(conn)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(DAY + 'T12:00:00+00:00').astimezone(tz or timezone.utc)
    monkeypatch.setattr(weekly_lifecycle, 'datetime', Clock)
    text = 'Synthetic issuer\nPublished on 2026-09-09\nYear ended 2025-12-31\n' + _documents()[0]['text']
    transport = FrozenTransport({'/investors/': (b'<html><body><a href="report.html">Annual report</a></body></html>', 'text/html'),
        '/investors/report.html': (html(text), 'text/html')})
    original_bind = bindings.bind_weekly_company_research
    def bind(board, store):
        return original_bind(board, store,
            identity_resolver=lambda ticker: {'status': 'confirmed', 'ticker': ticker,
                'name': 'Synthetic issuer', 'exchange': 'SYNTHETIC', 'currency': 'EUR'},
            profile_provider=lambda *a, **k: {'status': 'ok', 'data': {'info': {'website': SITE}}},
            session_factory=partial(ResearchSession, download=transport))
    monkeypatch.setattr(bindings, 'bind_weekly_company_research', bind)
    monkeypatch.setattr(preparation_sources, 'collect_preparation_evidence', lambda *a, **k:
        {'status': 'incomplete', 'preparation_ready': False, 'documents': [], 'issues': []})
    def propose(dossier, contract):
        ident = dossier['documents'][0]['id']
        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return ident if value == 'annual-1' else value
        return replace(_operating_plan())
    def prepare(bundle, **kwargs):
        return collect_and_prepare(bundle, archive_root=tmp_path / 'filing_archive',
            output_dir=cm.REPORT_DIR, propose=propose, research_sources=kwargs['research_sources'])
    monkeypatch.setattr(preparation_runtime, 'bind_installation_preparer', lambda trigger:
        {'preparer': prepare, 'state': {'status': 'enabled', 'triggers': ['committee']}})
    monkeypatch.setattr(chat_tools, 'REPORT_DIR', str(cm.REPORT_DIR))
    monkeypatch.setattr('bellomberg.core.paths.MODELS_DIR', cm.MODELS_DIR)
    calls = []
    class ResearchDesk:
        name = 'fundamentals'
        def __init__(self, board):
            self.board = board
        def run(self, round_n):
            calls.append(round_n)
            desk = Specialist(self.board, client=object())
            desk.name = self.name
            if round_n == 0:
                page = desk._execute_meta_tool('open_company_source', {'ticker': 'SYNTH-EXT', 'url': PAGE})
                assert page['ok'], page
                acquired = desk._execute_meta_tool('acquire_company_source', {'ticker': 'SYNTH-EXT',
                    'source': {'url': DOCUMENT}})
                assert acquired['ok'], acquired
            elif round_n == 1:
                desk._sector_bundles['SYNTH-EXT'] = _bundle()
                result = desk._execute_meta_tool('get_valuation', {'ticker': 'SYNTH-EXT'})
                payload = result.get('data', result)
                (tmp_path / 'source-workbook-result.json').write_text(json.dumps(payload, default=str), encoding='utf-8')
                assert payload['valuation_usability']['usable'], (payload.get('error'), payload.get('preparation'), payload.get('valuation_usability'))
                assert payload['_thesis_saved'].get('thesis_id'), payload.get('_thesis_saved')
            self.board.write(self.name, round_n, 'Offline statement-based report ' + 'evidence ' * 40)
            self.run_result_status = 'complete'
    monkeypatch.setattr(cm, 'SPECIALIST_ORDER', [ResearchDesk])
    monkeypatch.delitem(sys.modules, 'bellomberg.reporting.pdf_institutional')
    renderer = import_module('bellomberg.reporting.pdf_institutional')
    monkeypatch.setattr(renderer, 'REPORT_DIR', Path(cm.REPORT_DIR))
    first = cm.run_multi_agent(send_email=False)
    assert first['status'] == 'completed'
    assert {'Markdown', 'PDF', 'Excel', 'Excel metadata'} <= {row['kind'] for row in first['artifacts']}
    assert len(transport.requests) == 2 and calls == [0, 1, 2]
    saved_decisions = _store().get('decisions_finalized')
    originals = {row['path']: row['sha256'] for row in first['artifacts']}
    # These are the test's own temporary copies. Durable delivery restores the
    # exact bytes without asking the source service, renderer or AI again.
    for path in originals:
        assert Path(path).resolve().is_relative_to(tmp_path.resolve())
        Path(path).unlink()
    monkeypatch.setattr(cm, 'run_capo', lambda *a, **k: (_ for _ in ()).throw(AssertionError('AI repeated')))
    for attempt in range(2):
        result = cm.run_multi_agent(resume_memo_id=first['memo_id'], delivery_only=True, send_email=False)
        assert result['status'] == 'completed' and _store().get('decisions_finalized') == saved_decisions
        assert len(transport.requests) == 2 and calls == [0, 1, 2]
        for path, expected in originals.items():
            assert sha256(Path(path).read_bytes()).hexdigest() == expected
