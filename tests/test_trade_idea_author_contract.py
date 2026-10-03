"""An author can read the exact compiler contracts before spending on a plan."""
from copy import deepcopy
import json
import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists import base
from bellomberg.core.paths import PROJECT_ROOT
from bellomberg.valuation.preparation_methods import method_guide
from test_trade_idea_collaborative_guards import board_with_inputs


def test_method_contract_is_complete_paged_and_read_only(tmp_path, monkeypatch):
    board = board_with_inputs(tmp_path)
    before = deepcopy(board.data)
    monkeypatch.setattr(base, '_tetto_tool_result', lambda: 1800)
    offset, fragments = 0, []
    while True:
        result = trade_idea.handle_trade_idea_review_tool(board, 'fundamentals',
            'get_candidate_model_inputs', {'scope': 'base', 'contract_section': 'method', 'offset': offset})
        assert result['ok'], result
        assert len(json.dumps(result, ensure_ascii=False)) + 256 <= 1800
        fragments.append(result['text'])
        assert result['next_offset'] > offset
        offset = result['next_offset']
        if result['complete']:
            break
    guide = (PROJECT_ROOT / 'docs/guide/sector-valuations.md').read_text(encoding='utf-8')
    assert ''.join(fragments) == method_guide('operating_fcff', guide)
    assert board.data == before
    assert not board.valuation_results


def test_author_discovers_contract_and_evidence_rules(tmp_path):
    board = board_with_inputs(tmp_path)
    result = trade_idea.handle_trade_idea_review_tool(board, 'fundamentals',
        'get_candidate_model_inputs', {'scope': 'model'})
    assert 'method' in result['contract_sections']
    assert 'opening_nwc' in result['contract_sections']
    for section in ('evidence', 'shares', 'accounting'):
        result = trade_idea.handle_trade_idea_review_tool(board, 'fundamentals',
            'get_candidate_model_inputs', {'scope': 'model', 'contract_section': section})
        assert result['ok'] and result['text']


def test_contract_invalid_requests_do_not_fall_back_to_driver_manifest(tmp_path):
    board = board_with_inputs(tmp_path)
    for fields in ({'contract_section': 'unknown'}, {'contract_section': 'method', 'offset': -1},
                   {'contract_section': 'method', 'drivers': ['wacc']},
                   {'contract_section': 'method', 'offset': True},
                   {'contract_section': 'method', 'offset': 10**8}):
        result = trade_idea.handle_trade_idea_review_tool(board, 'fundamentals',
            'get_candidate_model_inputs', {'scope': 'base', **fields})
        assert result['ok'] is False


def test_draft_validation_reports_real_missing_proof_without_mutation(tmp_path):
    board = board_with_inputs(tmp_path)
    board.data['_model_input_draft'] = deepcopy(board.data['_model_input_basis']['plan'])
    del board.data['_model_input_draft']['model']['opening_nwc']
    before = deepcopy(board.data)
    result = trade_idea.handle_trade_idea_review_tool(board, 'fundamentals',
        'get_candidate_model_inputs', {'scope': 'model', 'contract_section': 'draft_validation'})
    assert result['ok'] and result['complete']
    report = json.loads(result['text'])
    assert any(row['field'] == 'opening_nwc' and row['code'] == 'missing_driver' for row in report['issues'])
    assert report['workbook_created'] is False
    assert board.data == before
    assert not list(tmp_path.rglob('*.xlsx'))


def test_share_contract_exposes_existing_class_proof_consumed_by_compiler(tmp_path):
    from datetime import date
    from test_statement_shares_class_capital import _class_sources, TITLE, ISSUER, OPENING
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    from bellomberg.valuation.input_preparation import _catalog, _compile
    board = board_with_inputs(tmp_path / 'board')
    source, tagged, _ = _class_sources(tmp_path)
    document = normalize_statement_shares(source, [tagged])['documents'][0]
    report = board.source_qualification['source_report']
    report['documents'] = [source, tagged, document]
    report['selection'] = {'selected_document_id': source['id'], 'opening_date': OPENING}
    result = trade_idea.handle_trade_idea_review_tool(board, 'fundamentals',
        'get_candidate_model_inputs', {'scope': 'model', 'contract_section': 'shares'})
    assert result['ok'] and result['complete']
    recipe = json.loads(result['text'])['reported_share_sources'][0]
    assert recipe['document_id'] == document['id']
    item = {'value': 600., 'kind': 'historical', 'evidence_ids': [recipe['document_id']],
        'calculation': recipe['calculation'], 'rationale': 'Synthetic dated Class A count, not diluted.',
        'valid_until': '2026-09-01', 'valid_until_basis': {'policy': 'same_day', 'as_of': '2026-09-01'}}
    catalog, issues, _ = _catalog(report['documents'], date(2026, 9, 1))
    assert not issues
    records, issues, _ = _compile({'model': {'shares': item}, 'scenarios': {s: {} for s in ('bear', 'base', 'bull')}},
        {'shares': ('shares', 'million shares', 'common', 'opening', 'number', 'model')}, {},
        {'entity': ISSUER, 'currency': 'USD', 'share_class': TITLE}, {'valuation_date': OPENING},
        None, catalog, date(2026, 9, 1))
    assert not issues and len(records) == 1


def test_qualified_source_reader_is_shared_by_all_desks_without_model_write(tmp_path):
    board = board_with_inputs(tmp_path)
    document = board.source_qualification['source_report']['documents'][0]
    before = deepcopy((board.source_qualification, board.data))
    readings = [trade_idea._read_candidate_source(board, desk, {'document_id': document['id']})
                for desk in ('fundamentals', 'macro', 'options', 'quant', 'crypto', 'eventdesk')]
    assert all(reading['ok'] for reading in readings), readings
    assert all(reading == readings[0] for reading in readings)
    assert (board.source_qualification, board.data) == before


def test_same_canonical_facts_reach_tradeidea_and_weekly_from_real_compiler(tmp_path):
    from types import SimpleNamespace
    from bellomberg.valuation import company_dossier
    from bellomberg.valuation.trade_idea_model import build_from_plan
    from test_trade_idea_economic import qualified, _operating_plan
    qualification = qualified(tmp_path)
    original = deepcopy(qualification)
    payload = build_from_plan(qualification, _operating_plan(), tmp_path / 'workbook')
    assert payload['preparation']['status'] == 'prepared'
    idea = SimpleNamespace(source_qualification=qualification, target_ticker=qualification['ticker'],
                           valuation_results={})
    weekly = SimpleNamespace(valuation_results={qualification['ticker']: payload})
    left = company_dossier.dossier_for_board(idea, qualification['ticker'])
    right = company_dossier.dossier_for_board(weekly, qualification['ticker'])
    factual = lambda dossier: [row for row in dossier['records'] if row['record']['kind'] == 'historical']
    assert factual(left) and factual(left) == factual(right)
    assert left['research_complete'] is right['research_complete'] is False
    assert all(row['sources'] for row in factual(left))
    idea.valuation_results[qualification['ticker']] = payload
    compiled_idea = company_dossier.dossier_for_board(idea, qualification['ticker'])
    assert compiled_idea['generation_id'] == payload['generation_id']
    assert compiled_idea['records'] == right['records']
    assert original == qualification


@pytest.mark.parametrize('field', ['value', 'unit', 'currency', 'period', 'entity', 'hash'])
def test_shared_dossier_rejects_mutated_observation_or_provenance(tmp_path, field):
    from bellomberg.valuation import company_dossier
    from bellomberg.valuation.trade_idea_model import build_from_plan
    from test_trade_idea_economic import qualified, _operating_plan
    qualification = qualified(tmp_path)
    payload = build_from_plan(qualification, _operating_plan(), tmp_path / 'workbook')
    corrupted = deepcopy(payload)
    if field == 'hash':
        corrupted['preparation']['review_basis']['dossier']['documents'][0]['sha256'] = '0' * 64
    else:
        row = next(row for row in corrupted['acquisition_snapshot']['case']['records']
                   if row['kind'] == 'historical')
        row[field] = 999 if field == 'value' else 'altered'
    with pytest.raises(ValueError):
        company_dossier.dossier_from_payload(corrupted)


def test_shared_dossier_missing_sources_remain_explicit_without_provider(tmp_path):
    from types import SimpleNamespace
    from bellomberg.valuation import company_dossier
    result = company_dossier.read_company_dossier(SimpleNamespace(valuation_results={}),
                                                  {'ticker': 'SYNTH-EXT'}, 1800)
    assert result['ok'] is False and result['status'] == 'unavailable'
    assert result['reason'] and result['requires_acquisition'] is True


def test_historical_continuation_rechecks_original_research_without_redating(tmp_path):
    from bellomberg.valuation.trade_idea_model import recheck_accepted_sources
    from test_trade_idea_economic import qualified
    qualification = qualified(tmp_path)
    original = deepcopy(qualification)
    with pytest.raises(ValueError, match='cutoff expired'):
        recheck_accepted_sources(qualification, qualification['ticker'], qualification['identity'],
                                 archive_root=tmp_path)
    checked = recheck_accepted_sources(qualification, qualification['ticker'], qualification['identity'],
                                      archive_root=tmp_path, allow_historical=True)
    assert checked['fingerprint'] == qualification['fingerprint']
    assert checked['as_of'] == original['as_of'] and qualification == original
