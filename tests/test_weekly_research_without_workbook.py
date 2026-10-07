"""Ordinary research weekly: no workbook producer, honest gaps and exact recovery."""
from pathlib import Path
from importlib import import_module
import json
import sys

import pytest

from test_cablaggio_consigliere_multi import run_offline, _DeskFinto, _allegati, _html
from test_weekly_recovery import _store
from bellomberg.agents import consigliere_multi as cm
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked


MODE = 'fundamentals_research_v1'
_REAL_EXTRACT = cm.MemoryDB.extract_and_save_decisions


@pytest.fixture
def research_weekly(run_offline, monkeypatch):
    from bellomberg.valuation import preparation_runtime, dcf_engine, preparation_service
    # Exercise the real publication gate, not the generic wiring fixture's stub.
    monkeypatch.delitem(sys.modules, 'bellomberg.agents.action_validator')
    import_module('bellomberg.agents.action_validator')
    monkeypatch.setattr(cm, '_weekly_contract', run_offline.native_weekly_contract)
    calls = {'prepare_binding': 0, 'compiler': 0, 'coverage': 0, 'reports': []}

    def forbidden(key):
        def fail(*args, **kwargs):
            calls[key] += 1
            pytest.fail('Research invoked forbidden workbook stage: ' + key)
        return fail

    monkeypatch.setattr(preparation_runtime, 'bind_installation_preparer', forbidden('prepare_binding'))
    monkeypatch.setattr(preparation_service, 'collect_and_prepare', forbidden('compiler'))
    monkeypatch.setattr(dcf_engine, 'generate_valuation', forbidden('compiler'))
    monkeypatch.setattr(cm, '_ensure_portfolio_valuations', forbidden('coverage'))
    from bellomberg.storage import memory_db
    with memory_db.MemoryDB()._conn() as conn:
        for ticker in ('SYNTH-A', 'SYNTH-B'):
            conn.execute('INSERT INTO positions(ticker,nome,quantita,prezzo_medio,valuta) VALUES(?,?,?,?,?)',
                         (ticker, 'Synthetic issuer ' + ticker, 1, 10, 'EUR'))
    monkeypatch.setattr(memory_db.MemoryDB, 'get_portfolio_summary', lambda self: {
        'n_positions': 2, 'positions': [{'ticker': ticker, 'nome': 'Synthetic issuer ' + ticker,
            'quantita': 1, 'prezzo_medio': 10, 'valuta': 'EUR', 'tipo': 'operating'}
            for ticker in ('SYNTH-A', 'SYNTH-B')]})
    monkeypatch.setattr(cm, '_try_correlation_matrix', lambda positions: None)

    def report(self, round_n):
        calls['reports'].append((self.name, round_n))
        text = ('Research report for SYNTH-A and SYNTH-B. Official statements unavailable: '
                'documented limitation. Consensus for SYNTH-B n.d.; no fair value claimed. '
                'Assumption: margin recovery remains uncertain. Bear: pressure persists. '
                'Base: operating discipline. Bull: demand improves. RESEARCH; no proposed purchase. ')
        self.bb.write(self.name, round_n, text)
        self.run_result_status = 'complete'
        return text

    monkeypatch.setattr(_DeskFinto, 'run', report)
    # The shared fixture only replaces provider/portfolio dependencies. Rendering
    # below is the production PDF renderer and delivery is the production MIME path.
    monkeypatch.delitem(sys.modules, 'bellomberg.reporting.pdf_institutional')
    renderer = import_module('bellomberg.reporting.pdf_institutional')
    monkeypatch.setattr(renderer, 'REPORT_DIR', Path(cm.REPORT_DIR))
    return run_offline, calls


def test_new_weekly_reaches_real_pdf_and_simulated_smtp_without_any_workbook(research_weekly):
    observed, calls = research_weekly
    result = cm.run_multi_agent(send_email=True)
    store = _store()
    assert store.context['contract']['analysis_mode'] == MODE
    assert store.context['contract']['publication_gate_policy'] == 'research-evidence-v1'
    assert 'instrument_natures' in store.context['contract']
    assert result['status'] == 'completed'
    assert result['analytical_status'] == 'complete'
    assert result['delivery_status'] == 'sent'
    assert store.get('research_dossier') is not None
    assert set(store.get('research_dossier')['dossiers']) == {'SYNTH-A', 'SYNTH-B'}
    assert store.get('valuation_coverage') is None
    assert all(calls[key] == 0 for key in ('prepare_binding', 'compiler', 'coverage'))
    assert observed.catturato['bb'].valuation_results == {}
    assert {row['kind'] for row in result['artifacts']} == {'Markdown', 'PDF'}
    pdf = next(Path(row['path']) for row in result['artifacts'] if row['kind'] == 'PDF')
    assert pdf.read_bytes().startswith(b'%PDF-') and pdf.stat().st_size > 1000
    assert len(observed.inviati) == 1
    assert all(not name.endswith('.xlsx') for name in _allegati(observed.inviati[0]))
    assert 'consensus' in _html(observed.inviati[0]).lower()
    assert not list(Path(cm.REPORT_DIR).rglob('*.xlsx'))


def test_research_crash_then_two_resumes_keep_reports_and_do_not_send_twice(research_weekly, monkeypatch):
    observed, calls = research_weekly
    original = _DeskFinto.run
    failed = []

    def crash(self, round_n):
        if self.name == 'quant' and round_n == 1 and not failed:
            failed.append(True)
            raise RuntimeError('synthetic crash before quant R1')
        return original(self, round_n)

    monkeypatch.setattr(_DeskFinto, 'run', crash)
    with pytest.raises(RuntimeError, match='synthetic crash'):
        cm.run_multi_agent(send_email=True)
    store = _store()
    saved = store.get('desk:fundamentals:1')
    first = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=True)
    reports = list(calls['reports'])
    decisions = store.get('decisions_finalized')
    costs = first['request_costs']
    second = cm.run_multi_agent(resume_memo_id=store.memo_id, send_email=True)
    assert first['status'] == second['status'] == 'completed'
    assert calls['reports'] == reports and calls['reports'].count(('fundamentals', 1)) == 1
    assert store.get('desk:fundamentals:1') == saved
    assert store.get('decisions_finalized') == decisions and second['request_costs'] == costs
    assert len(observed.inviati) == 1
    assert all(calls[key] == 0 for key in ('prepare_binding', 'compiler', 'coverage'))


def test_research_publication_never_reads_val_and_explains_withdrawal(research_weekly, monkeypatch):
    monkeypatch.setattr(cm.MemoryDB, 'extract_and_save_decisions', _REAL_EXTRACT)
    validator = import_module('bellomberg.agents.action_validator')
    monkeypatch.setattr(validator, '_sanity_payload', lambda *a, **k: pytest.fail('Research read archived VAL'))
    memo = ('# Memo\n\n## BLUF\nSynthetic proposed purchase.\n\n## ACTION TABLE\n'
            '| Action | Ticker | EUR | Timing | Confidence | Rationale |\n'
            '|---|---|---|---|---|---|\n'
            '| BUY | SYNTH-A | 1234 | now | HIGH | Synthetic thesis |\n')
    monkeypatch.setattr(cm, 'run_capo', lambda *a, **k: (memo, {'complete': True, 'stop_reason': 'end_turn',
        'model': 'synthetic/offline', 'api_calls': 1, 'in': 10, 'out': 10,
        'input_tokens': 10, 'output_tokens': 10}))
    cm.run_multi_agent(send_email=False)
    store = _store()
    published = store.get('memo_validated')['publication']
    assert store.get('capo')['memo'] == memo
    assert published['assessments'][0]['status'] == 'CHECK_UNAVAILABLE'
    assert 'evidenze' in published['assessments'][0]['reason']
    assert 'GATE DI PUBBLICAZIONE' in published['memo_markdown']


def test_research_smtp_failure_preserves_analysis_and_pdf(research_weekly, monkeypatch):
    import bellomberg.reporting.email_sender as sender
    monkeypatch.setattr(sender.smtplib, 'SMTP_SSL', lambda *a, **k:
                        (_ for _ in ()).throw(OSError('synthetic SMTP unavailable')))
    result = cm.run_multi_agent(send_email=True)
    assert result['status'] == 'incomplete' and result['delivery_status'] == 'pending'
    assert result['analytical_status'] == 'complete' and result['artifact_status'] == 'available'
    assert all(Path(row['path']).is_file() for row in result['artifacts'])
    assert _store().get('memo_validated') is not None


def test_research_smtp_disconnect_keeps_uncertainty_and_never_repeats_send(research_weekly, monkeypatch):
    from copy import deepcopy
    from test_cablaggio_consigliere_multi import _SMTP
    import bellomberg.reporting.email_sender as sender
    observed, calls = research_weekly
    class DisconnectAfterAcceptance(_SMTP):
        def send_message(self, message):
            super().send_message(message)
            raise sender.smtplib.SMTPServerDisconnected('synthetic disconnect after acceptance')
    monkeypatch.setattr(sender.smtplib, 'SMTP_SSL', DisconnectAfterAcceptance)
    first = cm.run_multi_agent(send_email=True)
    store = _store()
    original_receipt = deepcopy(first['email_delivery'])
    original_decisions = store.get('decisions_finalized')
    original_reports = list(calls['reports'])
    assert first['status'] == 'incomplete' and first['delivery_status'] == 'uncertain'
    assert original_receipt['state'] == 'uncertain' and original_receipt['receipt']['email_error'] == 'SMTPServerDisconnected'
    assert first['analytical_status'] == 'complete' and first['artifact_status'] == 'available'
    for _ in range(2):
        recovered = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True)
        assert recovered['status'] == 'incomplete' and recovered['delivery_status'] == 'uncertain'
        assert recovered['email_delivery'] == original_receipt
        assert recovered['request_costs'] == first['request_costs']
        assert store.get('decisions_finalized') == original_decisions
    assert len(observed.inviati) == 1 and calls['reports'] == original_reports
    assert all(calls[key] == 0 for key in ('prepare_binding', 'compiler', 'coverage'))


def test_research_tools_reject_direct_workbook_dispatch_without_calling_registry(monkeypatch):
    from bellomberg.agents.specialists.base import Blackboard, Specialist
    from bellomberg.agents import chat_tools
    board = Blackboard()
    board.analysis_mode = MODE
    desk = Specialist(board, client=object())
    desk.name = 'fundamentals'
    called = []
    monkeypatch.setattr(chat_tools, 'dispatch', lambda *a, **k: called.append(a))
    for name in ('get_valuation', 'build_dcf_model', 'get_candidate_model_inputs', 'submit_candidate_model_plan',
                 'review_candidate_model'):
        result = desk._execute_meta_tool(name, {'ticker': 'SYNTH-A'})
        assert result['ok'] is False and result['status'] == 'not_available_in_research_mode'
    assert called == []
    names = {tool['name'] for tool in desk._build_tools_schema()}
    assert not names.intersection({'get_valuation', 'get_candidate_model_inputs',
                                  'submit_candidate_model_plan', 'review_candidate_model'})


def test_research_fundamentals_does_not_run_a_valuation_score(monkeypatch):
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist
    from bellomberg.agents import specialist_scores
    board = Blackboard()
    board.analysis_mode = MODE
    monkeypatch.setattr(specialist_scores, 'fundamentals_score',
                        lambda **kwargs: pytest.fail('Research requested valuation score'))
    desk = FundamentalsSpecialist(board, client=object())
    assert desk.compute_score() is None
    assert 'fundamentals' in board.data['_score_errors']


def test_absent_mode_remains_legacy_delivery_contract():
    from bellomberg.agents.weekly_lifecycle import validate_delivery_manifest
    with pytest.raises(WeeklyRunBlocked, match='Workbook richiesto'):
        validate_delivery_manifest({'valuations': [{'ticker': 'SYNTH', 'status': 'missing'}]})


def test_research_dossier_reads_verified_documents_without_economic_compiler(monkeypatch):
    from types import SimpleNamespace
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.valuation import company_dossier as dossier
    from bellomberg.valuation.trade_idea_model import source_fingerprint
    from test_input_preparation import _documents
    from test_sector_analysis import DAY
    board = Blackboard()
    board.analysis_mode = MODE
    qualification = {'ticker': 'SYNTH-EXT', 'as_of': DAY, 'source_report': {'documents': _documents()}}
    qualification['fingerprint'] = source_fingerprint(qualification)
    board.source_qualification = qualification
    monkeypatch.setattr(dossier, 'dossier_from_qualification',
                        lambda *a: pytest.fail('research used economic model dossier compiler'))
    result = dossier.dossier_for_board(board, 'SYNTH-EXT')
    assert result['status'] == 'research' and len(result['documents']) == 1
    assert result['records'] == [] and result['generation_id'] is None
    assert all(issue.get('field') != 'model' for issue in result['issues'])


def test_research_seal_prevents_source_acquisition_even_without_a_model(monkeypatch):
    from bellomberg.agents.specialists.base import Blackboard, Specialist
    board = Blackboard()
    board.analysis_mode = MODE
    board.current_round = 1
    board.data['_research_thesis'] = {'dossiers': {'SYNTH-A': {'status': 'research'}}}
    board.company_source_session = lambda ticker: pytest.fail('sealed dossier allowed source dispatch')
    desk = Specialist(board, client=object())
    result = desk._execute_meta_tool('acquire_company_source', {'ticker': 'SYNTH-A',
        'source': {'url': 'https://issuer.example/annual.pdf'}})
    assert result['ok'] is False and 'sealed' in result['error']


@pytest.mark.parametrize('initial_documents', [False, True])
@pytest.mark.parametrize('acquire', [False, True])
def test_research_dossier_matches_real_session_canonical_catalog_and_keeps_pins(tmp_path, monkeypatch, initial_documents, acquire):
    from copy import deepcopy
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.valuation import company_dossier as dossier, input_preparation, trade_idea_model as model
    from test_company_source_research import FrozenTransport, SITE, DOCUMENT
    from test_input_preparation import _documents
    from test_trade_idea_economic import IDENTITY, providers_for
    from test_sector_analysis import DAY
    monkeypatch.setattr(input_preparation, 'prepare_method_inputs',
                        lambda *a, **k: pytest.fail('research attempted economic compilation'))
    admission = model.research_admission(IDENTITY['ticker'], IDENTITY, DAY,
        archive_root=tmp_path / 'archive', providers=providers_for(), analysis_mode=MODE,
        documents=_documents() if initial_documents else [])
    assert admission['status'] == 'research_required', admission
    transport = FrozenTransport()
    session = ResearchSession(run_id='canonical-research', ticker=IDENTITY['ticker'], identity=IDENTITY,
        as_of=DAY, admission_fingerprint=admission['fingerprint'], archive_root=tmp_path / 'archive',
        run_dir=tmp_path / 'run', issuer_website=SITE, download=transport)
    if acquire:
        assert session.acquire({'url': DOCUMENT})['ok'] is True
    snapshot = session.snapshot()
    qualification = model.research_source_qualification(admission, snapshot)
    if acquire:
        assert snapshot['documents'] != qualification['source_report']['documents']
    board = Blackboard()
    board.analysis_mode = MODE
    board.source_admission = admission
    board.source_qualification = qualification
    board.data['_source_research'] = {'revision_id': snapshot['revision_id']}
    board.company_source_session = lambda ticker: session
    result = dossier.dossier_for_board(board, IDENTITY['ticker'])
    assert result['documents'] == qualification['source_report']['documents']
    assert result['research_revision']['revision_sha256'] == snapshot['revision_sha256']
    assert len(result['documents']) == int(acquire) + int(initial_documents)
    assert result['status'] == ('research' if acquire or initial_documents else 'research_required')
    for fault in ('documents', 'revision'):
        changed = deepcopy(qualification)
        if fault == 'documents':
            if not changed['source_report']['documents']:
                changed['source_report']['documents'] = _documents()
            changed['source_report']['documents'][-1]['url'] = SITE + '/different-source.html'
        else:
            if not changed.get('research_revision'):
                changed['research_revision'] = {}
            changed['research_revision']['revision_sha256'] = '0' * 64
        changed['fingerprint'] = model.source_fingerprint(changed)
        board.source_qualification = changed
        with pytest.raises(ValueError, match='revision|source dossier'):
            dossier.dossier_for_board(board, IDENTITY['ticker'])
    assert len(transport.requests) == int(acquire)


@pytest.mark.parametrize('verified_document_available', [False, True])
def test_research_reader_retains_unverified_pm_document_gap_and_source(tmp_path, verified_document_available):
    from bellomberg.agents import trade_idea_sources as sources
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.valuation import company_dossier as dossier, trade_idea_model as model
    from test_input_preparation import _documents
    from test_trade_idea_pm_sources import IDENTITY, URL, WEBSITE, transport, _profile_providers
    download, calls = transport('Unreadable financial document: issuer/date cannot be verified')
    admitted = sources.ingest_document_sources(IDENTITY['ticker'], IDENTITY, '2026-09-28',
        [{'url': URL}], archive_root=tmp_path, issuer_website=WEBSITE, download=download, allow_partial=True)
    qualification = model.research_admission(IDENTITY['ticker'], IDENTITY, '2026-09-28',
        archive_root=tmp_path, providers=_profile_providers('2026-09-28'),
        documents=_documents() if verified_document_available else [],
        accepted_document_receipt=admitted['receipt'], analysis_mode=MODE)
    assert qualification['status'] == 'research_required'
    board = Blackboard()
    board.analysis_mode = MODE
    board.source_qualification = qualification
    result = dossier.dossier_for_board(board, IDENTITY['ticker'])
    assert all(issue in result['issues'] for issue in qualification['source_report']['issues'])
    gap = next(row for row in result['acquisition_diagnostics'] if row['url'] == URL)
    assert gap['status'] == 'needs_verification' and gap['financial_evidence'] is False
    assert gap['reason'] == admitted['receipt']['documents'][0]['reason']
    assert len(result['documents']) == int(verified_document_available)
    assert not any(document['url'] == URL for document in result['documents'])
    assert calls
