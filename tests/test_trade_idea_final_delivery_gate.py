"""SMTP is reserved for qualified final research, including negative judgments."""
from copy import deepcopy
from hashlib import sha256

import pytest

from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE, RESEARCH_COMPLETION_CONTRACT
from bellomberg.reporting import trade_idea_delivery as delivery
from _smtp_cattura import smtp
from test_trade_idea_store import db_path, migrated, store, request, result, finish, all_checks


def research_manifest(tmp_path):
    pdf = tmp_path / 'qualified-delivery-fixture.pdf'
    pdf.write_bytes(b'%PDF delivery-boundary fixture; renderer quality supplied separately')
    digest = sha256(pdf.read_bytes()).hexdigest()
    return {'schema_version': 1, 'run_type': 'trade_idea', 'run_id': 'final-delivery-proof',
        'ticker': 'SYNTH', 'judgment': 'watch', 'summary': 'Qualified research with explicit uncertainty.',
        'analysis_mode': RESEARCH_ANALYSIS_MODE, 'completion_contract': RESEARCH_COMPLETION_CONTRACT,
        'model_status': 'not_required', 'language': 'en', 'created_at': '2026-10-03T00:00:00Z',
        'message_id': '<final-delivery-proof@example.invalid>', 'destination': {'kind': 'research'},
        'attachments': [str(pdf)], 'expected_hashes': {str(pdf): digest},
        'artifacts': [{'kind': 'pdf', 'status': 'ready', 'path': str(pdf), 'sha256': digest}],
        'pdf_quality': {'status': 'ready', 'reasons': []},
        'complete_package_status': 'ready', 'complete_package_reasons': []}


@pytest.mark.parametrize('failure', ['capo_incomplete', 'pdf_partial'])
def test_incomplete_capo_or_unqualified_pdf_never_reaches_smtp(tmp_path, smtp, failure):
    manifest = research_manifest(tmp_path)
    if failure == 'capo_incomplete':
        manifest['judgment'] = 'incomplete'
    else:
        manifest['pdf_quality'] = {'status': 'partial', 'reasons': ['Required decision section missing']}
    before = deepcopy(manifest)
    outcome = delivery.send_trade_idea_delivery(manifest)
    assert outcome['status'] == outcome['email_status'] == 'blocked'
    assert outcome['error'] and smtp[0] == []
    assert manifest == before


@pytest.mark.parametrize('judgment', ['watch', 'rejected'])
def test_qualified_nonpositive_research_still_sends_exact_mime(tmp_path, smtp, judgment):
    manifest = research_manifest(tmp_path)
    manifest['judgment'] = judgment
    outcome = delivery.send_trade_idea_delivery(manifest)
    assert outcome['status'] == 'accepted'
    assert len(smtp[0]) == 1
    attachment = next(smtp[0][0].iter_attachments()).get_payload(decode=True)
    assert sha256(attachment).hexdigest() == manifest['artifacts'][0]['sha256']


@pytest.mark.parametrize('judgment', ['incomplete', 'watch'])
def test_native_partial_package_is_archived_blocked_without_even_claiming_smtp(migrated, tmp_path, smtp, judgment):
    from bellomberg.agents.trade_idea import deliver_trade_idea
    current, accepted = store(migrated), request()
    # Z3b 05/10: qualificazione di ricerca VERA (research_required), non piu' il finto 'qualified'.
    from _trade_idea_research_request import research_request
    accepted = research_request(accepted, archive_root=tmp_path / 'research-archive')
    run_id = current.create_run(accepted, idempotency_key='native-partial')['run']['id']
    finish(current, run_id, result(judgment=judgment, proposal=False), status='incomplete')
    current.route_result(run_id, {**all_checks(), 'capo_valid': judgment != 'incomplete'})
    outcome = deliver_trade_idea(current, run_id, output_dir=tmp_path / 'partial')
    assert outcome['status'] == 'blocked'
    assert outcome['attempts'] == 0 and smtp[0] == []
    detail = current.get_run(run_id)
    assert detail['artifacts']['complete_package_status'] == 'incomplete'
    assert detail['artifacts']['email_status'] == 'blocked'
    assert detail['artifacts']['pdf_quality']['status'] == 'partial'
    with pytest.raises(ValueError, match='not complete'):
        current.claim_email(run_id, retry=True)
    assert current.get_run(run_id)['email']['attempts'] == 0
