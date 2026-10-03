"""Ordinary read and recovery hooks preserve the author draft and admitted grant."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_author_statement_evidence import statement_case


def board_for(case):
    qualification, plan, _primary, root = case
    qualification['method_id'] = 'operating_fcff'
    from bellomberg.valuation.trade_idea_model import source_fingerprint
    qualification['fingerprint'] = source_fingerprint(qualification)
    events = []
    board = SimpleNamespace(source_qualification=qualification,
        data={'_source_research': {'archive_root': str(root)}, '_model_input_draft': plan},
        persist_run_checkpoint=lambda event: events.append(event))
    return board, events


def test_native_statement_read_pins_evidence_once_and_preserves_grant_and_draft(statement_case):
    from bellomberg.agents.model_authoring_evidence import acquire_board_statements, board_author_evidence_view
    board, events = board_for(statement_case)
    original = deepcopy((board.source_qualification, board.data['_model_input_draft']))
    first = acquire_board_statements(board)
    assert first['reported_balance']['components']
    for _ in range(2):
        assert acquire_board_statements(board) == first
        overlay = board_author_evidence_view(board)
        assert any((d.get('metadata') or {}).get('normalizer') == 'balance_sheet_v1'
                   for d in overlay['source_report']['documents'])
    assert len(events) == 1 and len(board.data['_author_statements']['receipts']) == 1
    assert (board.source_qualification, board.data['_model_input_draft']) == original


def test_corrupt_statement_pin_is_not_silently_replaced(statement_case):
    from bellomberg.agents.model_authoring_evidence import acquire_board_statements, board_author_evidence_view
    board, events = board_for(statement_case)
    acquire_board_statements(board)
    board.data['_author_statements']['receipts'][0]['view']['opening_date'] = '2025-01-01'
    with pytest.raises(ValueError):
        acquire_board_statements(board)
    with pytest.raises(ValueError):
        board_author_evidence_view(board)
    assert len(events) == 1


def test_evidence_checkpoint_failure_restores_original_board_data(statement_case):
    from bellomberg.agents.model_authoring_evidence import acquire_board_statements
    board, _events = board_for(statement_case)
    before = deepcopy(board.data)
    def failed(_event):
        raise OSError('Offline durable write failure')
    board.persist_run_checkpoint = failed
    with pytest.raises(OSError):
        acquire_board_statements(board)
    assert board.data == before


def test_repeated_read_replays_pinned_response_without_reading_mutable_cache(statement_case, monkeypatch):
    from bellomberg.agents.model_authoring_evidence import acquire_board_statements
    from bellomberg.valuation import author_statement_evidence as evidence
    board, events = board_for(statement_case)
    acquire_board_statements(board)
    original = evidence._companyfacts
    calls = []
    def replay(cik, existing, fetch):
        assert existing is not None
        calls.append(cik)
        return original(cik, existing, fetch)
    monkeypatch.setattr(evidence, '_companyfacts', replay)
    acquire_board_statements(board)
    assert len(calls) == 1 and len(events) == 1


def test_missing_driver_offset_zero_reads_observed_facts_without_adopting_them(tmp_path, monkeypatch):
    import json
    from bellomberg.agents import trade_idea, model_authoring_evidence
    from bellomberg.agents.specialists import base
    from test_trade_idea_collaborative_guards import board_with_inputs
    board = board_with_inputs(tmp_path)
    board.data['_model_input_basis']['plan']['model'].pop('shares', None)
    original = deepcopy(board.data)
    evidence = {'status': 'statement_evidence_only', 'economic_decisions_applied': False,
                'reported_share_observations': {'facts': [{'value': 100, 'label': 'Synthetic only'}]}}
    monkeypatch.setattr(model_authoring_evidence, 'acquire_board_statements', lambda b: deepcopy(evidence))
    monkeypatch.setattr(model_authoring_evidence, 'board_author_evidence_view', lambda b: b.source_qualification)
    monkeypatch.setattr(base, '_tetto_tool_result', lambda: 1800)
    offset, chunks = 0, []
    while True:
        result = trade_idea.handle_trade_idea_review_tool(board, 'fundamentals',
            'get_candidate_model_inputs', {'scope': 'model', 'drivers': ['shares'], 'offset': offset})
        assert result['ok'], result
        assert result['status'] == 'statement_input_page'
        assert len(json.dumps(result, ensure_ascii=False)) + 256 <= 1800
        chunks.append(result['input_json_fragment'])
        offset = result['next_offset']
        if result['complete']:
            break
    parsed = json.loads(''.join(chunks))
    assert parsed['statement_evidence'] == evidence
    assert not parsed['basis']['drivers']
    assert board.data['_model_input_basis'] == original['_model_input_basis']
    assert board.data.get('_model_input_draft') == original.get('_model_input_draft')
