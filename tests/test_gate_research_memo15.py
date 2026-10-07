"""Research publication policy: synthetic proposals, no live data."""
import pytest
from copy import deepcopy
from hashlib import sha256
import json

from bellomberg.agents import action_validator as av
from bellomberg.agents import consigliere_multi as cm

CONTRACT = {'analysis_mode': 'fundamentals_research_v1',
            'publication_gate_policy': 'research-evidence-v1'}
CHECKS = {key: {'status': 'AVAILABLE', 'source': 'synthetic frozen receipt'}
          for key in ('research_seal', 'mandate', 'risk', 'sizing')}
MEMO = ('# Memo\n\n## BLUF\nSynthetic research recommendation.\n\n## ACTION TABLE\n'
        '| Action | Ticker | EUR | Timing | Confidence | Rationale |\n'
        '|---|---|---|---|---|---|\n'
        '| BUY | ZZTEST | 1234 | now | HIGH | Synthetic thesis |\n')


def _sealed_sources(receipt_change=None, dossier_change=None):
    from bellomberg.core.research_analysis import research_digest, _thesis_identity
    body = 'Synthetic issuer source text.'
    doc = {'id': 'DOC-SYNTH', 'url': 'https://issuer.example/report',
           'published_at': '2026-10-06', 'text': body, 'sha256': sha256(body.encode()).hexdigest()}
    dossier = {'ticker': 'ZZTEST', 'status': 'research', 'source_fingerprint': 'SYNTH-FP',
               'research_as_of': '2026-10-07', 'documents': [doc], 'records': [], 'issues': [],
               'research_complete': False, 'approval_status': 'not_PM_approved'}
    if dossier_change:
        dossier.update(dossier_change)
    receipt = {'tool': 'read_company_dossier', 'input': {'ticker': 'ZZTEST', 'section': 'document',
                'document_id': 'DOC-SYNTH'}, 'success': True, 'truncated': False,
               'output': json.dumps({'ok': True, 'source_fingerprint': 'SYNTH-FP',
                   'document_id': 'DOC-SYNTH', 'sha256': doc['sha256'], 'url': doc['url'],
                   'published_at': doc['published_at'], 'text': body})}
    if receipt_change:
        receipt.update(receipt_change)
    sealed = {'analysis_mode': CONTRACT['analysis_mode'], 'version': 1, 'dossiers': {'ZZTEST': dossier},
              'reports': {'fundamentals': 'Synthetic committee thesis for ZZTEST.'}, 'tool_receipts': [receipt]}
    sealed['dossier_sha256'] = research_digest({'dossiers': sealed['dossiers'], 'tool_receipts': sealed['tool_receipts']})
    sealed['thesis_sha256'] = research_digest(_thesis_identity(sealed))
    return sealed


def _bound_checks(sealed=None, memo=MEMO):
    from bellomberg.core.research_analysis import build_research_action_bindings
    sealed = sealed or _sealed_sources()
    reference = {key: sealed[key] for key in ('analysis_mode', 'version', 'dossier_sha256', 'thesis_sha256')}
    return {**deepcopy(CHECKS), 'research_seal': {'status': 'AVAILABLE', 'source': reference},
            'run_identity': {'run_id': 'synthetic-run-a', 'cutoff': '2026-10-07T10:00:00+00:00'},
            'row_thesis_evidence_binding': build_research_action_bindings(sealed,
                run_id='synthetic-run-a', memo_markdown=memo, cutoff='2026-10-07T10:00:00+00:00')}


def test_corporate_research_is_operative_without_val_or_pm_approval():
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT,
                                   research_checks=_bound_checks())[0]
    assert result['status'] == 'OPERATIVE'
    assert 'provenienza' in result['reason'] and 'verita' in result['reason']


@pytest.mark.parametrize('change,reason', [({'success': False}, 'success'),
    ({'truncated': True}, 'truncated'), ({'input': {'ticker': 'ZZOTHER'}}, 'ticker'),
    ({'output': '{broken'}, 'JSON'),
    ({'tool': 'get_company_financials', 'output': json.dumps({'source_verified': False, '_source': 'synthetic', 'as_of': '2026-10-06'})}, 'verificata'),
    ({'tool': 'get_company_financials', 'output': json.dumps({'ok': False, '_source': 'synthetic', 'as_of': '2026-10-06'})}, 'KO'),
    ({'tool': 'get_company_financials', 'output': json.dumps({'stale': True, '_source': 'synthetic', 'as_of': '2026-10-06'})}, 'STALE'),
    ({'tool': 'get_company_financials', 'output': json.dumps({'_source': 'synthetic', 'as_of': '2026-10-08'})}, 'cutoff')])
def test_bad_sealed_receipt_never_opens_gate(change, reason):
    checks = _bound_checks(_sealed_sources(receipt_change=change))
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT, research_checks=checks)[0]
    assert result['status'] == 'CHECK_UNAVAILABLE'
    assert reason in result['reason']


@pytest.mark.parametrize('tamper', ['run', 'memo', 'row', 'hash', 'ticker', 'row_hash', 'action'])
def test_binding_identity_cannot_be_reused(tamper):
    checks = _bound_checks()
    envelope = checks['row_thesis_evidence_binding']
    if tamper == 'run':
        checks['run_identity']['run_id'] = 'synthetic-run-b'
    elif tamper == 'memo':
        return_result = av.assess_action_table(MEMO.replace('1234', '4321'), publication_contract=CONTRACT,
                                               research_checks=checks)[0]
        assert return_result['status'] == 'CHECK_UNAVAILABLE'
        return
    elif tamper == 'hash':
        envelope['sha256'] = 'broken'
    else:
        key, value = {'row': ('row_index', 99), 'ticker': ('ticker', 'ZZOTHER'),
                      'row_hash': ('row_sha256', '0' * 64), 'action': ('action', 'ADD')}[tamper]
        envelope['rows'][0][key] = value
        from bellomberg.core.research_analysis import research_digest
        envelope['sha256'] = research_digest({key: value for key, value in envelope.items() if key != 'sha256'})
    assert av.assess_action_table(MEMO, publication_contract=CONTRACT,
                                 research_checks=checks)[0]['status'] == 'CHECK_UNAVAILABLE'


def test_mixed_rows_and_duplicate_tickers_have_distinct_bindings():
    memo = MEMO + '| ADD | ZZTEST | 2345 | now | HIGH | Second proposal |\n' + '| BUY | ZZOTHER | 3456 | now | HIGH | Missing issuer |\n'
    checks = _bound_checks(memo=memo)
    results = av.assess_action_table(memo, publication_contract=CONTRACT, research_checks=checks)
    assert [row['status'] for row in results] == ['OPERATIVE', 'OPERATIVE', 'CHECK_UNAVAILABLE']
    assert results[0]['research_binding']['row_index'] != results[1]['research_binding']['row_index']
    assert results[0]['research_binding']['row_sha256'] != results[1]['research_binding']['row_sha256']
    published = cm.build_publication_snapshot(memo, results, withdrawal_warning=True)['memo_markdown']
    table = published.split('## ACTION TABLE')[1]
    assert '| BUY | ZZTEST |' in table and '| ADD | ZZTEST |' in table and '| BUY | ZZOTHER |' not in table
    assert 'Le proposte BUY/ADD elencate in PROPOSTE NON OPERATIVE restano non eseguibili' in published


def test_disclosed_future_event_does_not_become_future_source_availability():
    receipt = {'tool': 'get_company_financials', 'output': json.dumps({'_source': 'synthetic',
        'observed_at': '2026-10-06', 'earnings_date': '2026-11-01', 'period': 'FY2027',
        'assumption': 'Forward scenario, not an observed result'})}
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT,
                                   research_checks=_bound_checks(_sealed_sources(receipt_change=receipt)))[0]
    assert result['status'] == 'OPERATIVE'


@pytest.mark.parametrize('all_stale', [False, True])
def test_real_guidance_payload_binds_current_rows_and_declares_stale_scope(all_stale):
    from datetime import date
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.agents.specialists.base import _trade_idea_tool_receipt_success
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args):
            return [{'metric': 'revenue', 'period': 'FY2027', 'status': 'active',
                     'valid_until': '2026-09-01' if all_stale else '2026-11-01'},
                    {'metric': 'eps', 'period': 'FY2025', 'status': 'active', 'valid_until': '2026-09-01'}]
    class DB:
        def _conn(self): return Connection()
    payload = MemoryDB.get_guidance(DB(), 'ZZTEST', today=date(2026, 10, 7))
    output = {'data': payload, '_source': 'registro guidance V6'}
    receipt = {'tool': 'get_guidance', 'output': json.dumps(output),
               'success': _trade_idea_tool_receipt_success(output, 'get_guidance')}
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT,
                                   research_checks=_bound_checks(_sealed_sources(receipt_change=receipt)))[0]
    assert result['status'] == ('CHECK_UNAVAILABLE' if all_stale else 'OPERATIVE')
    if not all_stale:
        dating = result['research_binding']['receipts'][0]['dating']
        assert [scope['path'] for scope in dating['eligible_scopes']] == ['$.data.active[0]']
        assert dating['excluded_scopes'][0]['path'] == '$.data.active[1]'
        assert 'STALE' in dating['excluded_scopes'][0]['reason']


@pytest.mark.parametrize('change', [{'ticker': 'ZZOTHER'}, {'documents': []}, {'status': 'unavailable'}])
def test_missing_or_different_issuer_sources_are_explicit(change):
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT,
        research_checks=_bound_checks(_sealed_sources(dossier_change=change)))[0]
    assert result['status'] == 'CHECK_UNAVAILABLE' and 'dossier' in result['reason']


def test_mutated_seal_is_rejected_before_building_binding():
    from bellomberg.core.research_analysis import build_research_action_bindings
    sealed = _sealed_sources()
    sealed['tool_receipts'][0]['output'] = '{}'
    with pytest.raises(ValueError, match='seal integrity'):
        build_research_action_bindings(sealed, run_id='synthetic-run-a', memo_markdown=MEMO, cutoff='2026-10-07')


@pytest.mark.parametrize('change', [{'published_at': '2026-10-08'},
    {'metadata': {'filing_bridge': {'verifica': 'non_verificato'}}},
    {'metadata': {'filing_bridge': {'corrente': False}}}, {'sha256': '0' * 64}])
def test_document_admission_cannot_be_invented_by_sealing(change):
    from bellomberg.core.research_analysis import research_digest, _thesis_identity
    sealed = _sealed_sources()
    sealed['dossiers']['ZZTEST']['documents'][0].update(change)
    sealed['dossier_sha256'] = research_digest({'dossiers': sealed['dossiers'], 'tool_receipts': sealed['tool_receipts']})
    sealed['thesis_sha256'] = research_digest(_thesis_identity(sealed))
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT, research_checks=_bound_checks(sealed))[0]
    assert result['status'] == 'CHECK_UNAVAILABLE' and 'fonti dossier' in result['reason']


def test_duplicate_binding_is_not_a_second_approval():
    from bellomberg.core.research_analysis import research_digest
    checks = _bound_checks()
    envelope = checks['row_thesis_evidence_binding']
    envelope['rows'].append(deepcopy(envelope['rows'][0]))
    envelope['sha256'] = research_digest({key: value for key, value in envelope.items() if key != 'sha256'})
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT, research_checks=checks)[0]
    assert result['status'] == 'CHECK_UNAVAILABLE' and 'ambiguo' in result['reason']


@pytest.mark.parametrize('bridge_reference', [False, True])
def test_verified_observed_download_is_not_a_missing_publication_date(bridge_reference):
    from bellomberg.core.research_analysis import research_digest, _thesis_identity
    sealed = _sealed_sources()
    doc = sealed['dossiers']['ZZTEST']['documents'][0]
    doc.update(published_at=None, availability_basis='observed_download', available_at='2026-10-06',
               document_sha256='a' * 64, retrieval={'url': doc['url'], 'document_sha256': 'a' * 64,
                                                   'retrieved_at': '2026-10-06T10:00:00+00:00'})
    if bridge_reference:
        from bellomberg.agents.ponte_filing_dossier import CONTRATTO
        doc.pop('retrieval')
        doc.pop('text')
        doc['metadata'] = {'filing_bridge': {'contract': CONTRATTO,
                            'run_concluso_il': '2026-10-06T10:00:00+00:00', 'corrente': True}}
    payload = json.loads(sealed['tool_receipts'][0]['output'])
    payload['published_at'] = None
    sealed['tool_receipts'][0]['output'] = json.dumps(payload)
    sealed['dossier_sha256'] = research_digest({'dossiers': sealed['dossiers'], 'tool_receipts': sealed['tool_receipts']})
    sealed['thesis_sha256'] = research_digest(_thesis_identity(sealed))
    result = av.assess_action_table(MEMO, publication_contract=CONTRACT, research_checks=_bound_checks(sealed))[0]
    assert result['status'] == 'OPERATIVE'
    dating = result['research_binding']['receipts'][0]['dating']
    assert dating['economic_dates'] == []
    assert dating['document_availability']['availability_basis'] == 'observed_download'


@pytest.mark.parametrize('mixed', [False, True])
def test_real_register_pdf_and_recovery_keep_positive_corporate_binding(tmp_path, monkeypatch, mixed):
    from types import SimpleNamespace
    import pypdf
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.agents.weekly_lifecycle import create_run
    from bellomberg.core import research_analysis as ra
    from bellomberg.valuation import company_dossier
    from bellomberg.reporting import pdf_institutional as pdf, charts_institutional as ci
    memo = MEMO + ('| BUY | ZZOTHER | 2345 | now | HIGH | Missing issuer |\n' if mixed else '')
    def no_chroma(self):
        self.chroma_client = self.col_memos = self.col_decisions = self.col_feedback = None
    monkeypatch.setattr(MemoryDB, '_init_chroma', no_chroma)
    db = MemoryDB(db_path=str(tmp_path / 'gate.sqlite'), chroma_path=str(tmp_path / 'chroma'))
    store = create_run(db, {'positions': [], 'n_positions': 0}, {'synthetic': 'mandate'}, CONTRACT, 'it')
    store.request_journal = None
    sealed = _sealed_sources()
    store.complete('research_dossier', sealed)
    store.complete('synthesis_context', {'risk_data': {'portfolio': {'var_95_1d_pct': -1.2}}})
    board = SimpleNamespace(analysis_mode=CONTRACT['analysis_mode'], run_scope='weekly',
        data={'_research_thesis': sealed, '_sizing': {'summary': {'synthetic': 'available'}}},
        valuation_results={}, read=lambda desk, round_n: sealed['reports'][desk])
    monkeypatch.setattr(company_dossier, 'dossier_for_board', lambda bb, ticker: sealed['dossiers'][ticker])
    monkeypatch.setattr(av, '_sanity_payload', lambda *a, **k: pytest.fail('positive research read VAL'))
    first = cm._publication_gate(board, db, store, store.memo_id, memo, memo, None, [])
    assert first['decisions_error'] is None
    assert first['publication']['assessments'][0]['status'] == 'OPERATIVE'
    assert first['publication']['source_markdown'] == memo
    with db._conn() as conn:
        row = conn.execute("SELECT assessment_status,status FROM decisions WHERE memo_id=? AND ticker='ZZTEST'", (store.memo_id,)).fetchone()
    assert tuple(row) == ('OPERATIVE', 'PENDING')
    saved = store.get('research_action_bindings')
    monkeypatch.setattr(ra, 'research_reference', lambda *a: pytest.fail('recovery reread live reference'))
    monkeypatch.setattr(company_dossier, 'dossier_for_board', lambda *a: pytest.fail('recovery reread dossier'))
    board.tool_receipts = [{'success': False}]  # post-seal material cannot replace the frozen inventory
    second = cm._publication_gate(board, db, store, store.memo_id, memo, memo, None, [])
    assert second['decision_ids'] == first['decision_ids']
    assert second['publication'] == first['publication']
    assert store.get('research_action_bindings') == saved
    monkeypatch.setattr(ci, 'DIR', str(tmp_path / 'charts'))
    monkeypatch.setattr(pdf, 'REPORT_DIR', str(tmp_path))
    path = pdf.build_institutional_memo(first['publication']['memo_markdown'],
                                     output_path=str(tmp_path / ('mixed.pdf' if mixed else 'positive.pdf')), title_date='2026-10-07')
    text = ' '.join(page.extract_text() for page in pypdf.PdfReader(path).pages)
    assert 'ZZTEST' in text
    if mixed:
        assert first['publication']['assessments'][1]['status'] == 'CHECK_UNAVAILABLE'
        assert 'GATE DI PUBBLICAZIONE' in pypdf.PdfReader(path).pages[0].extract_text()
        print('R1A_SYNTHETIC_PREVIEW=' + str(path))
    else:
        assert 'PROPOSTE NON OPERATIVE' not in text


@pytest.mark.parametrize('severity', [None, 'OK', 'WARN', 'BLOCK', 'INVALID'])
def test_research_ignores_legacy_sidecar_and_never_fabricates_evidence_approval(severity):
    result = av.assess_action_table(MEMO, sanity_exclusions=[
        {'ticker': 'ZZTEST', 'severity': severity}], publication_contract=CONTRACT)[0]
    assert result['status'] == 'CHECK_UNAVAILABLE'
    assert 'evidenze' in result['reason']
    assert 'non applicabile' in result['reason']
    assert result['senza_valutazione'] is False


def test_research_collector_and_detect_never_read_legacy_files(monkeypatch):
    monkeypatch.setattr(av, '_sanity_payload', lambda *a, **k: pytest.fail('read legacy VAL'))
    assert av.collect_sanity_exclusions(MEMO, publication_contract=CONTRACT) == []
    assert av.detect_sanity_exclusions(MEMO, publication_contract=CONTRACT) == ('', [])


def test_old_research_contract_keeps_legacy_assessment():
    old = {'analysis_mode': 'fundamentals_research_v1'}
    result = av.assess_action_table(MEMO, sanity_exclusions=[
        {'ticker': 'ZZTEST', 'severity': 'BLOCK'}], publication_contract=old)[0]
    assert result['status'] == 'BLOCKED'


@pytest.mark.parametrize('amount', ['n.d.', '0', '-20'])
def test_research_invalid_amount_stays_explicit(amount):
    result = av.assess_action_table(MEMO.replace('1234', amount),
                                   publication_contract=CONTRACT)[0]
    assert result['status'] == 'CHECK_UNAVAILABLE'
    assert 'importo' in result['reason']


def test_projection_warns_in_bluf_without_mutating_source():
    assessment = [{'row_index': 0, 'action': 'BUY', 'ticker': 'ZZTEST',
                   'status': 'CHECK_UNAVAILABLE', 'reason': 'evidenze non legate'}]
    result = cm.build_publication_snapshot(MEMO, assessment, source_markdown=MEMO,
                                          withdrawal_warning=True)
    assert result['source_markdown'] == MEMO
    bluf = result['memo_markdown'].split('## BLUF', 1)[1].split('## ACTION TABLE', 1)[0]
    assert 'GATE DI PUBBLICAZIONE' in bluf
    assert '| BUY | ZZTEST' not in result['memo_markdown'].split('## PROPOSTE')[0]


def test_hold_does_not_claim_withdrawal():
    result = cm.build_publication_snapshot(MEMO.replace('| BUY |', '| HOLD |'), [],
                                          withdrawal_warning=True)
    assert 'GATE DI PUBBLICAZIONE' not in result['memo_markdown']


@pytest.mark.parametrize('nature', sorted(av.NATURE_SENZA_VALUTAZIONE))
def test_existing_nature_exception_is_frozen_and_labelled(nature):
    contract = dict(CONTRACT, instrument_natures={'ZZTEST': {'valore': nature, 'fonte': 'registro_pm'}})
    result = av.assess_action_table(MEMO, publication_contract=contract,
                                   sanity_exclusions=[{'ticker': 'ZZTEST', 'severity': 'BLOCK'}],
                                   research_checks=CHECKS)[0]
    assert result['status'] == 'OPERATIVE' and result['senza_valutazione']
    assert 'non certificazione della tesi' in result['reason']


def test_unproven_nature_does_not_create_exception():
    contract = dict(CONTRACT, instrument_natures={'ZZTEST': {'valore': 'etf', 'fonte': 'ripiego'}})
    assert av.assess_action_table(MEMO, publication_contract=contract,
                                 research_checks=CHECKS)[0]['status'] == 'CHECK_UNAVAILABLE'


@pytest.mark.parametrize('missing', ['research_seal', 'mandate', 'risk', 'sizing'])
def test_nature_exception_cannot_hide_missing_run_controls(missing):
    contract = dict(CONTRACT, instrument_natures={'ZZTEST': {'valore': 'etf', 'fonte': 'registro_pm'}})
    checks = {key: value for key, value in CHECKS.items() if key != missing}
    result = av.assess_action_table(MEMO, publication_contract=contract, research_checks=checks)[0]
    assert result['status'] == 'CHECK_UNAVAILABLE'
    assert missing in result['reason']


def test_resume_keeps_old_policy_and_never_reads_nature_store(monkeypatch):
    from bellomberg.storage import classificazione
    monkeypatch.setattr(classificazione, 'carica_veicoli', lambda: pytest.fail('live nature on resume'))
    assert cm._resume_publication_contract(CONTRACT, {'analysis_mode': CONTRACT['analysis_mode']}) == {
        'analysis_mode': CONTRACT['analysis_mode']}
    sealed = dict(CONTRACT, instrument_natures={'ZZTEST': {'valore': 'etf', 'fonte': 'registro_pm'}})
    assert cm._resume_publication_contract(CONTRACT, sealed) == sealed


def test_nature_capture_is_a_new_run_snapshot(monkeypatch):
    from types import SimpleNamespace
    from bellomberg.storage import classificazione
    store = {'origine': 'synthetic', 'motivo': None, 'veicoli': {'ZZTEST': {'tipo': 'etf'}}}
    calls = []
    monkeypatch.setattr(classificazione, 'carica_veicoli', lambda: calls.append('read') or store)
    monkeypatch.setattr(classificazione, 'natura', lambda ticker, snapshot:
                        SimpleNamespace(as_dict=lambda: {'valore': snapshot['veicoli'][ticker]['tipo'],
                                                         'fonte': 'registro_pm'}))
    sealed = cm._capture_publication_natures(CONTRACT)
    store['veicoli']['ZZTEST']['tipo'] = 'operating'
    assert sealed['instrument_natures']['ZZTEST']['valore'] == 'etf'
    assert calls == ['read']
    assert 'instrument_natures' not in CONTRACT


def test_capture_failure_is_explicit_not_a_nature_default(monkeypatch):
    from bellomberg.storage import classificazione
    def broken():
        raise OSError('synthetic')
    monkeypatch.setattr(classificazione, 'carica_veicoli', broken)
    frozen = cm._capture_publication_natures(CONTRACT)
    assert frozen['instrument_natures_source'] == {'status': 'CHECK_UNAVAILABLE', 'reason': 'OSError'}
    assert av.assess_action_table(MEMO, publication_contract=frozen,
                                 research_checks=CHECKS)[0]['status'] == 'CHECK_UNAVAILABLE'


def test_cover_contains_withdrawal_notice(tmp_path, monkeypatch):
    import pypdf
    import bellomberg.reporting.pdf_institutional as pdf
    import bellomberg.reporting.charts_institutional as ci
    monkeypatch.setattr(ci, 'DIR', str(tmp_path / 'charts'))
    monkeypatch.setattr(pdf, 'REPORT_DIR', str(tmp_path))
    assessment = [{'row_index': 0, 'action': 'BUY', 'ticker': 'ZZTEST',
                   'status': 'CHECK_UNAVAILABLE', 'reason': 'evidenze non legate'}]
    memo = cm.build_publication_snapshot(MEMO, assessment, withdrawal_warning=True)['memo_markdown']
    path = pdf.build_institutional_memo(memo, output_path=str(tmp_path / 'memo.pdf'), title_date='2026-10-07')
    cover = pypdf.PdfReader(path).pages[0].extract_text()
    assert 'GATE DI PUBBLICAZIONE' in cover
    assert 'restano non eseguibili' in ' '.join(cover.split())
