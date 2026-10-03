"""Run-native research admission and late compilation, with frozen sources only."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.trade_idea_contract import validate_run_authorization
from bellomberg.valuation import trade_idea_model as model
from test_sector_analysis import DAY
from test_trade_idea_economic import IDENTITY, providers_for, _documents, _operating_plan
from test_trade_idea_explicit_author_plan import assert_new_workbook


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(DAY + 'T12:00:00+00:00').astimezone(tz or timezone.utc)
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    monkeypatch.setattr(trade_idea, 'datetime', FrozenDatetime)


def admitted(tmp_path, *, providers=None, documents=()):
    return model.research_admission(IDENTITY['ticker'], IDENTITY, DAY,
        archive_root=tmp_path, providers=providers or providers_for(), documents=documents)


def grant(qualification):
    return {'accepted': True, 'source_fingerprint': qualification['fingerprint'],
            'activities': ['committee', 'model_preparation'], 'max_revision_rounds': 0}


def snapshot(qualification):
    # The service owns byte acquisition/sealing. This pure compiler test uses
    # the existing frozen documentary compiler fixtures, never a live URL.
    return {'run_id': 'offline-research', 'ticker': IDENTITY['ticker'], 'as_of': DAY,
            'parent_grant_fingerprint': qualification['fingerprint'], 'revision_id': 1,
            'revision_sha256': 'a' * 64, 'documents': _documents(),
            'document_receipts': [], 'filing_results': [], 'status': 'verified'}


def test_empty_document_catalog_admits_research_without_certifying_history(tmp_path):
    qualification = admitted(tmp_path)
    assert qualification['status'] == 'research_required', qualification['reasons']
    assert qualification['source_report']['documents'] == []
    assert qualification['coverage']['economic_qualification'] == 'not_assessed'
    assert qualification['coverage']['sources']['current_quotation']['status'] == 'verified_observed_price'
    assert qualification['coverage']['sources']['current_quotation']['comparison_qualified'] is False
    assert validate_run_authorization(grant(qualification), qualification) == grant(qualification)
    checked = model.recheck_accepted_sources(qualification, IDENTITY['ticker'], IDENTITY,
                                            archive_root=tmp_path)
    assert checked == qualification
    with pytest.raises(ValueError, match='Fonti non qualificate|research|ricerca'):
        model.build_from_plan(qualification, _operating_plan(), tmp_path / 'forbidden-model')
    assert not list(tmp_path.rglob('*.xlsx'))


@pytest.mark.parametrize('fault', ['price', 'timestamp', 'currency', 'identity', 'source'])
def test_research_does_not_relax_identity_or_observed_quote_gates(tmp_path, fault):
    providers = providers_for()
    original = providers['profile']
    def profile(*args, **kwargs):
        source = original(*args, **kwargs)
        info = source['data']['info']
        if fault == 'price':
            info['regularMarketPrice'] = 0
        elif fault == 'timestamp':
            info.pop('regularMarketTime')
        elif fault == 'currency':
            info['currency'] = 'USD'
        elif fault == 'identity':
            info['symbol'] = 'OTHER'
        else:
            source['source_id'] = None
        return source
    providers['profile'] = profile
    qualification = admitted(tmp_path, providers=providers)
    assert qualification['status'] == 'blocked'
    assert qualification['reasons']
    with pytest.raises(ValueError):
        validate_run_authorization(grant(qualification), qualification)


def test_late_authored_plan_compiles_real_workbook_from_new_frozen_documents(tmp_path):
    qualification = admitted(tmp_path)
    initial, authorization = deepcopy(qualification), grant(qualification)
    sources, plan = snapshot(qualification), _operating_plan()
    compiled_sources = model.qualify_authored_research(qualification, sources, plan,
                                                      archive_root=tmp_path)
    assert compiled_sources['status'] == 'qualified', compiled_sources['reasons']
    assert compiled_sources['research_revision']['parent_grant_fingerprint'] == initial['fingerprint']
    assert compiled_sources['fingerprint'] != initial['fingerprint']
    payload = model.build_from_plan(compiled_sources, plan, tmp_path / 'model')
    assert_new_workbook(payload, plan)
    assert qualification == initial
    assert validate_run_authorization(authorization, initial) == authorization
    with pytest.raises(ValueError, match='fingerprint'):
        validate_run_authorization(authorization, compiled_sources)


@pytest.mark.parametrize('fault', ['parent_grant', 'ticker', 'document_hash', 'historical_amount'])
def test_late_research_keeps_lineage_and_financial_proof_guards(tmp_path, fault):
    qualification = admitted(tmp_path)
    sources, plan = snapshot(qualification), _operating_plan()
    if fault == 'parent_grant':
        sources['parent_grant_fingerprint'] = 'b' * 64
    elif fault == 'ticker':
        sources['ticker'] = 'OTHER'
    elif fault == 'document_hash':
        sources['documents'][0]['sha256'] = 'b' * 64
    else:
        plan['model']['historical_revenue']['value'] *= 2
    with pytest.raises(ValueError):
        model.qualify_authored_research(qualification, sources, plan, archive_root=tmp_path)
    assert not list(tmp_path.rglob('*.xlsx'))


def test_only_bound_source_service_intents_are_resumable():
    from bellomberg.storage.trade_idea_store import _checkpoint_resume_block
    checkpoint = {'contract': {'source_fingerprint': 'a' * 64},
        'data': {'_source_research': {'run_id': 'original-run', 'parent_grant_fingerprint': 'a' * 64}},
        'specialist_checkpoints': {'fundamentals:0': {'status': 'running',
            'inflight_tools': {'tool-id': {'name': 'acquire_company_source', 'input': {}}}}}}
    assert _checkpoint_resume_block(checkpoint) is None
    for name in ('get_valuation', 'add_guidance', 'unknown'):
        changed = deepcopy(checkpoint)
        changed['specialist_checkpoints']['fundamentals:0']['inflight_tools']['tool-id']['name'] = name
        assert _checkpoint_resume_block(changed) == 'tool_outcome_unresolved'
    checkpoint['data']['_source_research']['parent_grant_fingerprint'] = 'b' * 64
    assert _checkpoint_resume_block(checkpoint) == 'tool_outcome_unresolved'


def test_new_document_revision_preserves_prior_advice_and_author_decision():
    old_fingerprint = '1' * 64
    board = SimpleNamespace(data={}, tool_receipts=[], source_qualification={
        'fingerprint': '2' * 64, 'research_revision': {'revision_id': 2,
            'revision_sha256': '3' * 64, 'parent_grant_fingerprint': '4' * 64},
        'source_report': {'documents': [{'id': 'first-statement'}, {'id': 'second-statement'}]}})
    draft = {'wacc': {'value': 0.1, 'kind': 'assumption'}}
    original = {'id': 'before-new-document', 'requester': 'fundamentals', 'desk': 'macro',
        'question': 'How does this financing assumption change the cash-flow thesis?',
        'source_fingerprint': old_fingerprint, 'draft_assumptions': draft,
        'draft_sha256': trade_idea._plan_digest(draft), 'evidence_refs': ['first-statement'],
        'status': 'complete', 'response': 'Preserved prior answer and uncertainty.',
        'fundamentals_decision': {'consultation_id': 'before-new-document', 'decision': 'disagreed',
            'rationale': 'Explicit author dissent before the next source was acquired.'}}
    prior = deepcopy(original)
    current = {**deepcopy(original), 'id': 'after-new-document',
        'source_fingerprint': trade_idea._consultation_source_fingerprint(board),
        'evidence_refs': ['first-statement', 'second-statement'],
        'response': 'New answer after reading the additional primary statement.',
        'fundamentals_decision': {'consultation_id': 'after-new-document', 'decision': 'incorporated',
            'rationale': 'Decision on the revised documentary basis.'}}
    board.data = {'_source_research': {'evidence_fingerprints': [old_fingerprint]},
                  '_model_consultations': [original, current]}
    assert trade_idea._current_model_consultations(board) == [current]
    assert board.data['_model_consultations'][0] == prior
    board.data['_source_research']['evidence_fingerprints'] = []
    with pytest.raises(ValueError, match='source'):
        trade_idea._current_model_consultations(board)
