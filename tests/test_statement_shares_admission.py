"""A recompiled inline count admits bounded share classification, never a proxy."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest

from test_statement_shares_inline import OPENING, ISSUER, _sources


def _catalog_sources(tmp_path, *, tag_value=None):
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares

    source, tagged, path = _sources(tmp_path, tag_value=tag_value)
    normalized = normalize_statement_shares(source, [tagged])
    assert normalized['status'] == 'ready', normalized
    doc = normalized['documents'][0]
    catalog, issues, _ = _catalog([source, tagged, doc], date(2026, 9, 1))
    assert not issues
    return catalog, source, tagged, doc, path


@pytest.mark.parametrize('tag_value', [None, 24_100_000_000])
def test_share_gate_accepts_only_recompiled_single_opening_statement_with_exact_conflict_receipt(tmp_path, tag_value):
    from bellomberg.valuation.preparation_fresh_historical import _has_opening_share_observation

    catalog, source, _, doc, _ = _catalog_sources(tmp_path, tag_value=tag_value)
    assert _has_opening_share_observation(catalog, ISSUER, OPENING, '2026-09-01',
        [catalog[source['id']]])
    assert json.loads(catalog[doc['id']]['text'])['tag_comparison']['conflicts'] == (
        [] if tag_value is None else [{'source_id': 'xbrl-0000000123-000000012326000001',
            'pointer': '/facts/2/observation/val', 'value': tag_value,
            'unit': 'shares', 'end': OPENING}])


@pytest.mark.parametrize('fault', ['missing_parent', 'changed_raw', 'changed_normalized_receipt'])
def test_share_gate_reproves_raw_and_rejects_unselected_or_changed_statement(tmp_path, fault):
    from bellomberg.valuation.preparation_fresh_historical import _has_opening_share_observation

    catalog, source, _, doc, path = _catalog_sources(tmp_path)
    filings = [catalog[source['id']]]
    if fault == 'missing_parent':
        filings = []
    elif fault == 'changed_raw':
        path.write_bytes(path.read_bytes() + b'changed after catalog')
    else:
        changed = deepcopy(catalog[doc['id']]); body = json.loads(changed['text'])
        body['reported_precision']['exact_legal_count'] = True
        changed['text'] = json.dumps(body)
        changed['sha256'] = sha256(changed['text'].encode()).hexdigest()
        catalog[doc['id']] = changed
    assert not _has_opening_share_observation(catalog, ISSUER, OPENING, '2026-09-01', filings)


@pytest.mark.parametrize('present', [True, False])
def test_operating_stage_advises_exact_statement_proof_only_when_verified_source_is_visible(tmp_path, present):
    from bellomberg.valuation.preparation_ai import StagedProposer

    catalog, source, tagged, doc, _ = _catalog_sources(tmp_path)
    documents = [source, tagged, doc] if present else [source, tagged]
    dossier = {'ticker': 'SYNX', 'method_id': 'operating_fcff', 'as_of': '2026-09-01',
        'documents': documents, 'document_acquisition': {'selection': {
            'selected_document_id': source['id'], 'selected_document_ids': [source['id']],
            'opening_date': OPENING}}}
    contract = {'method_id': 'operating_fcff', 'scenarios': ['bear', 'base', 'bull'],
        'schema': {'shares': ('shares', 'million shares', 'common', 'opening', 'number', 'model')}}
    captured = []
    class CapturedStage(Exception):
        pass
    def capture(context, narrowed):
        captured.append((context, narrowed))
        raise CapturedStage()
    with pytest.raises(CapturedStage):
        StagedProposer(capture)(dossier, contract)
    stage = captured[0][1]['preparation_stage']
    assert stage['drivers'] == ['shares']
    if present:
        hint = stage['statement_share_source']
        assert hint['document_id'] == doc['id']
        assert hint['calculation'] == {'type': 'statement_shares', 'fact_index': 0,
            'selection_basis': 'primary_inline_without_same_date_tag', 'acknowledged_conflicts': []}
        assert hint['reported_precision']['rounding_unit_shares'] == 1_000_000
        assert doc['id'] in {d['id'] for d in captured[0][0]['documents']}
    else:
        assert 'statement_share_source' not in stage


def test_bank_legacy_share_hint_is_unchanged_without_inline_source():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_input_preparation_bank import _documents, _propose
    from test_sector_analysis import DAY, providers_for

    captured = []
    def capture(dossier, contract):
        stage = contract['preparation_stage']
        if 'capital.shares_m' in stage['drivers']:
            captured.append(deepcopy(stage))
        plan = _propose(dossier, contract)
        scope = stage['scope']
        values = plan['model'] if scope == 'model' else plan['scenarios'][scope]
        return {'drivers': {name: values[name] for name in stage['drivers']},
                'rationale': 'Synthetic documented bank case'}

    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    prepared = prepare_method_inputs(bundle, documents=_documents(),
        propose=StagedProposer(capture, drivers_per_stage=12))
    assert prepared['status'] == 'prepared', prepared['issues']
    expected = {
        'statement_source': 'If a sec_statement_shares_v1 document is present, select its current-date fact explicitly with calculation={type: statement_shares, fact_index: 0, selection_basis: primary_statement_over_conflicting_tags (or primary_statement_consistent_with_tags), acknowledged_conflicts: exact tag_comparison.conflicts list}. Cite only that document in evidence_ids; do not also supply pointer/quote fields. The value is the observed count scaled into millions, never a weighted average.',
        'disagreement': 'Explain the primary-statement selection and the conflicting SEC tags. Their discrepancy remains visible, not silently corrected or certified by the issuer. A class-specific statement cannot replace another share class.'}
    assert captured and all(stage['bank_requested_contracts']['capital.shares_m'] == expected
                            and 'statement_share_source' not in stage for stage in captured)
