"""Research admission and real PDF packages never require a workbook."""
from copy import deepcopy
from pathlib import Path

import pytest

from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE, RESEARCH_COMPLETION_CONTRACT
from bellomberg.core.trade_idea_contract import validate_run_authorization
from bellomberg.reporting import trade_idea_delivery as delivery
from bellomberg.valuation import trade_idea_model as model
from test_trade_idea_economic import IDENTITY, providers_for
from test_sector_analysis import DAY
from trade_idea_fixtures import research_result


def test_research_admission_needs_no_sector_driver_plan(tmp_path, monkeypatch):
    providers = providers_for()
    result = model.research_admission(IDENTITY['ticker'], deepcopy(IDENTITY), DAY,
        archive_root=tmp_path, providers=providers, analysis_mode=RESEARCH_ANALYSIS_MODE)
    assert result['status'] == 'research_required'
    assert result['analysis_mode'] == RESEARCH_ANALYSIS_MODE
    assert result['coverage']['preparer'] == 'not_required'
    grant = {'accepted': True, 'source_fingerprint': result['fingerprint'],
        'activities': ['committee'], 'max_revision_rounds': 0}
    assert validate_run_authorization(grant, result) == grant
    model.validate_research_admission(result)
    tampered = deepcopy(result)
    tampered.pop('analysis_mode')
    with pytest.raises(ValueError):
        model.validate_research_admission(tampered)


def test_real_research_pdf_ready_with_no_workbook(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('Research delivery must not inspect or generate a workbook')
    monkeypatch.setattr(delivery, '_candidate_workbooks', forbidden)
    result = research_result('rejected', run_id='research-package')
    result['valuation_refs'] = []
    run = {'id': result['run_id'], 'ticker': result['ticker'],
        'analysis_mode': RESEARCH_ANALYSIS_MODE, 'language': 'en'}
    manifest = delivery.prepare_trade_idea_delivery(run, result,
        output_dir=tmp_path / 'delivery', model_roots=[tmp_path], language='en')
    assert manifest['completion_contract'] == RESEARCH_COMPLETION_CONTRACT
    assert manifest['model_status'] == 'not_required'
    assert manifest['complete_package_status'] == 'ready', manifest.get('complete_package_reasons')
    assert manifest['valuations'] == []
    assert [item['kind'] for item in manifest['artifacts']] == ['pdf']
    assert Path(manifest['attachments'][0]).is_file()
    assert delivery.verify_trade_idea_manifest(manifest, require_complete=True)
    assert not list(tmp_path.rglob('*.xlsx'))


def test_legacy_delivery_contract_still_requires_workbook():
    manifest = {'pdf_quality': {'status': 'ready'}, 'artifacts': [], 'model_status': 'incomplete'}
    assert delivery.assess_trade_idea_completion(manifest)['status'] == 'incomplete'


@pytest.mark.parametrize('activity', ['results', 'attempts', 'refs'])
def test_research_package_rejects_any_workbook_activity_before_pdf(tmp_path, monkeypatch, activity):
    monkeypatch.setattr(delivery, 'build_trade_idea_report',
        lambda *_a, **_k: pytest.fail('Unexpected workbook activity reached PDF rendering'))
    result = research_result('rejected', run_id='mixed-mode')
    result['valuation_refs'] = [{'generation_id': 'legacy'}] if activity == 'refs' else []
    run = {'id': result['run_id'], 'ticker': result['ticker'], 'analysis_mode': RESEARCH_ANALYSIS_MODE}
    with pytest.raises(ValueError, match='workbook|model'):
        delivery.prepare_trade_idea_delivery(run, result,
            valuation_results={'TEST': {'status': 'failed'}} if activity == 'results' else None,
            valuation_attempts=[{'status': 'failed'}] if activity == 'attempts' else [],
            output_dir=tmp_path, model_roots=[tmp_path])


@pytest.mark.parametrize('residue', ['unavailable_xlsx', 'attempts'])
def test_research_manifest_cannot_claim_complete_with_failed_excel_activity(residue):
    manifest = {'analysis_mode': RESEARCH_ANALYSIS_MODE,
        'completion_contract': RESEARCH_COMPLETION_CONTRACT,
        'pdf_quality': {'status': 'ready'}, 'model_status': 'not_required',
        'artifacts': [{'kind': 'xlsx', 'status': 'unavailable'}] if residue == 'unavailable_xlsx' else [],
        'attempts': [{'status': 'failed'}] if residue == 'attempts' else []}
    assert delivery.assess_trade_idea_completion(manifest)['status'] == 'incomplete'


@pytest.mark.parametrize('residue', ['unreferenced_result', 'persisted_xlsx', 'persisted_attempt'])
def test_recovery_rejects_model_residue_before_filtering_restoring_or_sending(tmp_path, monkeypatch, residue):
    from types import SimpleNamespace
    from bellomberg.agents import trade_idea
    from bellomberg.reporting import exact_artifacts
    result = research_result('rejected', run_id='mixed-recovery')
    result['valuation_refs'] = []
    run = {'id': result['run_id'], 'ticker': result['ticker'], 'language': 'en',
        'analysis_mode': RESEARCH_ANALYSIS_MODE, 'destination': {'kind': 'research'}}
    manifest = {'analysis_mode': RESEARCH_ANALYSIS_MODE, 'completion_contract': RESEARCH_COMPLETION_CONTRACT,
        'model_status': 'not_required', 'artifacts': [], 'valuations': [], 'attempts': [],
        'exact_artifact_receipts': [{'kind': 'xlsx', 'path': str(tmp_path / 'must-not-exist.xlsx')}]}
    if residue == 'persisted_xlsx':
        manifest['artifacts'] = [{'kind': 'xlsx', 'status': 'unavailable'}]
    if residue == 'persisted_attempt':
        manifest['attempts'] = [{'status': 'failed'}]
    detail = {'run': run, 'result': result, 'progress': {},
        'artifacts': None if residue == 'unreferenced_result' else manifest}
    def forbidden(*_a, **_k):
        pytest.fail('Research recovery filtered or restored unexpected model activity')
    monkeypatch.setattr(exact_artifacts, 'restore_exact_artifacts', forbidden)
    monkeypatch.setattr(delivery, 'verify_exact_artifact_receipts', forbidden)
    store = SimpleNamespace(get_run=lambda _id: deepcopy(detail))
    with pytest.raises(ValueError, match='model|workbook|Research'):
        trade_idea._deliver_trade_idea(store, run['id'], output_dir=tmp_path,
            valuation_results=[{'generation_id': 'unreferenced'}] if residue == 'unreferenced_result' else None,
            prepare=forbidden, send=forbidden)
    assert list(tmp_path.iterdir()) == []
