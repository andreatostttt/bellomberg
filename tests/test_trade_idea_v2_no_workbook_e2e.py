"""V2 ordinary research through paid-response recovery and real PDF/MIME.

Only provider/source/SMTP transports are frozen. This preserves the separate
legacy integration test and uses its Excel tripwires and temporary database.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from pypdf import PdfReader

from bellomberg.agents import trade_idea
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V2, ROLE_EFFORT_V2
from test_trade_idea_no_workbook_e2e import no_workbook_case, _native_rows, _provider_round
from test_trade_idea_report_v2 import editorial_fixture
from _smtp_cattura import smtp
from test_trade_idea_source_research import frozen_clock
from test_trade_idea_store import db_path, migrated


def test_v2_research_four_resumes_paid_capo_crash_and_exact_delivery_recovery(no_workbook_case, monkeypatch):
    case = no_workbook_case
    case.request['execution_policy'] = EXECUTION_POLICY_V2
    case.request['language'] = 'it'
    for role, effort in ROLE_EFFORT_V2.items():
        case.request['models'][role]['reasoning_effort'] = effort
        case.request['catalog_snapshot']['models'][role]['supported_efforts'] = ['low', 'medium', 'max']
    # Keep the fixture's genuine source/evidence receipts; replace only the
    # synthetic Capo prose by substantive short research for the V2 renderer.
    synthetic_client = trade_idea.OpenRouterClient
    _, editorial = editorial_fixture('rejected')

    class Client(synthetic_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            original_stream = self.messages.stream

            def stream(**call):
                underlying = original_stream(**call)

                class Stream:
                    def __enter__(self):
                        underlying.__enter__()
                        return self

                    def __exit__(self, *args):
                        return underlying.__exit__(*args)

                    def get_final_message(self):
                        response = underlying.get_final_message()
                        payload = json.loads(response.content[0].text)
                        for section in payload['dossier']:
                            short = next(item for item in editorial['dossier'] if item['key'] == section['key'])
                            section['title'] = short['title']
                            section['paragraphs'] = [text.replace('[src: archivio_demo]', '[evidence: archive]')
                                                     for text in short['paragraphs']]
                            section['tables'], section['charts'] = [], []
                            if section['key'] == 'executive':
                                section['paragraphs'] = ['Il comitato respinge la tesi di immunita al ciclo: '
                                    'la continuita dei programmi protegge i volumi esistenti, ma i rinnovi '
                                    'restano negoziabili. Il fornitore deve dimostrare ritorni sul capitale '
                                    'dopo investimenti e concessioni commerciali. [evidence: archive]']
                            if section['key'] == 'decision':
                                section['paragraphs'] = ['Nessuna proposta operativa: la tesi di immunita '
                                    'al ciclo e respinta. Una revisione richiede incassi verificati, '
                                    'rinnovi senza erosione dei margini e recupero dello sviluppo. '
                                    'La mancanza di consensus non viene colmata con un prezzo inventato. '
                                    '[evidence: archive]']
                        response.content[0].text = '```json\n' + json.dumps(payload) + '\n```'
                        return response

                return Stream()

            self.messages.stream = stream

    monkeypatch.setattr(trade_idea, 'OpenRouterClient', Client)
    original_capo = trade_idea.run_trade_idea_capo
    crash = {'pending': True, 'triggered': 0}

    def capo_then_crash(*args, **kwargs):
        result = original_capo(*args, **kwargs)
        if crash['pending']:
            crash.update(pending=False, triggered=crash['triggered'] + 1)
            raise RuntimeError('Intentional offline crash after paid validated Capo before completion checkpoint')
        return result

    monkeypatch.setattr(trade_idea, 'run_trade_idea_capo', capo_then_crash)
    current = case.current
    parent = current.create_run(case.request, idempotency_key='v2-native-parent')['run']['id']
    accepted = deepcopy(current.get_accepted_request(parent))
    histories, saved, source_count = [], {}, None
    ident = parent
    for round_n in (0, 1, 2):
        case.state['stop_after'] = round_n
        before = len(case.providers)
        detail = case.execute(ident, 'v2-round-' + str(round_n))
        assert detail['run']['technical_status'] == 'incomplete', detail['run']['reason']
        assert 'native R' + str(round_n) in detail['run']['reason']
        if round_n:
            # Paid desk material survives the failure as a labelled partial result.
            assert detail['result']['judgment'] == 'incomplete' and detail['result']['proposal'] is None
            assert any(section['key'].startswith('desk_') for section in detail['result']['dossier'])
        assert all(_provider_round(call) not in range(round_n) for call in case.providers[before:])
        assert not case.smtp[0] and not case.prohibited
        source_count = source_count if source_count is not None else len(case.transport.requests)
        assert len(case.transport.requests) == source_count
        histories.append(ident)
        saved[ident] = _native_rows(case.database, ident)
        ident = current.create_continuation(ident, idempotency_key='v2-resume-' + str(round_n),
                                            authorize_new_requests=True)['run']['id']
    case.state['stop_after'] = None
    before_capo = len(case.providers)
    failed = case.execute(ident, 'v2-paid-capo')
    assert failed['run']['technical_status'] == 'incomplete'
    assert 'Intentional offline crash after paid validated Capo' in failed['run']['reason']
    assert crash['triggered'] == 1 and case.state['capo_calls'] == 1
    assert failed['result']['judgment'] == 'incomplete' and failed['result']['proposal'] is None
    assert failed['email']['status'] == 'blocked' and not case.smtp[0]
    assert len(case.providers) == before_capo + 1
    assert case.providers[-1]['max_tokens'] == 32768
    assert case.providers[-1]['thinking'] == {'type': 'effort', 'effort': 'low'}
    specialist_calls = [call for call in case.providers if _provider_round(call) in (0, 1, 2)]
    red_calls = [call for call in case.providers if
        (call.get('response_format') or {}).get('json_schema', {}).get('name') == 'trade_idea_committee_review']
    assert specialist_calls and red_calls
    assert {_provider_round(call) for call in specialist_calls} == {0, 1, 2}
    for role, calls in (('specialist', specialist_calls), ('red_team', red_calls)):
        assert all(call['model'] == case.request['models'][role]['model'] for call in calls)
        assert all(call['thinking'] == {'type': 'effort', 'effort': 'medium'} for call in calls)
        assert all(call['max_tokens'] == 128000 for call in calls)
    with sqlite3.connect(case.database) as conn:
        frozen_request = conn.execute("SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='capo_request_saved'", (ident,)).fetchall()
        assert len(frozen_request) == 1
        cost_rows_before = conn.execute('SELECT * FROM trade_idea_costs ORDER BY request_id').fetchall()
    saved[ident] = _native_rows(case.database, ident)
    histories.append(ident)
    sealed = deepcopy(failed['progress']['checkpoint']['data']['_research_thesis'])
    paid_count = len(case.providers)
    final_id = current.create_continuation(ident, idempotency_key='v2-paid-recovery',
                                          authorize_new_requests=True)['run']['id']
    final = case.execute(final_id, 'v2-final')
    assert final['run']['technical_status'] == 'completed', final['run']['reason']
    assert final['run']['execution_policy'] == EXECUTION_POLICY_V2
    assert final['result']['judgment'] == 'rejected' and final['result']['proposal'] is None
    assert final['result']['valuation_refs'] == [] and final['result']['model_review'] is None
    assert final['run']['destination']['kind'] == 'research'
    assert final['states']['analysis'] == 'complete' and final['states']['artifacts'] == 'ready'
    assert len(case.providers) == paid_count and case.state['capo_calls'] == 1
    assert len(case.transport.requests) == source_count
    assert final['progress']['checkpoint']['data']['_research_thesis'] == sealed
    review = final['progress']['checkpoint']['data']['_research_review']
    assert set(review['desks']) == set(trade_idea.TRADE_IDEA_DESKS)
    assert all(row['round'] == 2 and row['research_ref'] == review['research_ref'] for row in review['desks'].values())
    assert review['red_team']['research_ref'] == review['research_ref']
    assert all(row['response'] for row in review['objections'])
    assert any(row.get('success') is False and row.get('tool') == 'acquire_company_source'
               for row in final['progress']['checkpoint']['tool_receipts'])
    manifest = deepcopy(final['artifacts'])
    assert manifest['completion_contract'] == 'research-memo-pdf/1'
    assert manifest['complete_package_status'] == 'ready'
    assert manifest['pdf_quality']['execution_policy'] == EXECUTION_POLICY_V2
    assert manifest['pdf_quality']['content_integrity'] == 'complete'
    assert manifest['pdf_quality']['analytical_words'] < 4000
    assert {item['kind'] for item in manifest['artifacts']} == {'pdf'}
    assert final['email']['status'] == 'accepted' and len(case.smtp[0]) == 1
    attachment, = case.smtp[0][0].iter_attachments()
    pdf = Path(manifest['artifacts'][0]['path'])
    assert attachment.get_payload(decode=True) == pdf.read_bytes()
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(pdf).pages)
    assert 'immunita' in text and 'consensus' in text.lower()
    # The delivered memo carries the complete desk annex, measured on the same PDF as routing.
    assert 'Analisi integrale dei desk e del Red Team' in text
    assert 'annex' in manifest['pdf_quality']['section_pages']
    assert 'annex' in final['progress']['report_quality']['section_pages']
    assert case.smtp[0][0]['Message-ID'] == manifest['message_id']
    assert final['cost']['requests'] == paid_count
    assert Decimal(final['cost']['charged_usd']) == Decimal('0.001') * paid_count
    assert final['cost']['unknown_requests'] == 0 and final['cost']['reserved_usd'] == '0'
    with sqlite3.connect(case.database) as conn:
        assert conn.execute('SELECT * FROM trade_idea_costs ORDER BY request_id').fetchall() == cost_rows_before
        assert conn.execute("SELECT count(*) FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (final_id,)).fetchone()[0] == 1
    saved_final = _native_rows(case.database, final_id)
    for receipt in manifest['exact_artifact_receipts']:
        path = Path(receipt['path'])
        assert path.is_relative_to(case.root)
        path.unlink()
    for _ in range(2):
        trade_idea.deliver_trade_idea(current, final_id, output_dir=case.root/'v2-final', send_email=True)
        recovered = current.get_run(final_id)
        assert recovered['artifacts'] == manifest and recovered['states']['artifacts'] == 'ready'
        for receipt in manifest['exact_artifact_receipts']:
            assert sha256(Path(receipt['path']).read_bytes()).hexdigest() == receipt['sha256']
    assert len(case.smtp[0]) == 1 and len(case.providers) == paid_count
    assert _native_rows(case.database, final_id) == saved_final
    assert all(_native_rows(case.database, run_id) == snapshot for run_id, snapshot in saved.items())
    assert current.get_accepted_request(parent) == accepted
    assert not case.prohibited and not list(case.root.rglob('*.xlsx'))
    (case.root/'v2-no-workbook-proof.json').write_text(json.dumps({
        'run_ids': histories + [final_id], 'provider_requests': paid_count,
        'capo_dispatches': case.state['capo_calls'], 'injected_crashes': crash['triggered'],
        'source_requests': source_count, 'smtp_accepted_messages': len(case.smtp[0]),
        'captured_specialist_calls': len(specialist_calls), 'captured_red_calls': len(red_calls),
        'wire_policy_verified': {'specialists': 'medium/128000', 'red_team': 'medium/128000', 'capo': 'low/32768'},
        'forbidden_calls': dict(case.prohibited), 'recovered_twice': True,
        'quality': manifest['pdf_quality'], 'cost': final['cost']}, indent=2), encoding='utf-8')
