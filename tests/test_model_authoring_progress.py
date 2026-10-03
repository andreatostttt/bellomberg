"""Read-only author progress, using the native contract and recorded tool views."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents.model_authoring_progress import build_model_authoring_progress


@pytest.fixture
def author(tmp_path):
    from bellomberg.agents.trade_idea import _candidate_input_contract
    from test_trade_idea_collaborative_guards import board_with_inputs
    board = board_with_inputs(tmp_path)
    return board, _candidate_input_contract(board)


def tool_turn(name, inputs, output, call_id='call-1'):
    return [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': call_id,
            'name': name, 'input': inputs}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call_id,
            'content': json.dumps(output)}]},
    ]


def contract_page(section, start, end, total, digest='a' * 64, call_id='call-1'):
    return tool_turn('get_candidate_model_inputs', {'scope': 'model',
        'contract_section': section, 'offset': start}, {'ok': True,
        'status': 'compiler_contract_only', 'section': section, 'sha256': digest,
        'offset': start, 'next_offset': end, 'total_chars': total,
        'complete': end == total, 'text': 'x' * (end - start)}, call_id)


def test_progress_never_adopts_archived_inputs_or_calls_them_approved(author):
    board, contract = author
    before = deepcopy((board.data, board.source_qualification, board.valuation_results))
    progress = build_model_authoring_progress(board, contract=contract, remaining_turns=12)
    assert progress['kind'] == 'informational_not_approval'
    assert progress['remaining_turns'] == 12
    assert progress['draft']['present'] is False
    for scope, row in progress['draft']['scopes'].items():
        expected = sorted(key for key, descriptor in contract['schema'].items()
            if descriptor[-1] == ('model' if scope == 'model' else 'scenario'))
        assert row['stored'] == [] and row['missing'] == expected
    assert progress['admitted_document_ids'] == sorted(
        doc['id'] for doc in board.source_qualification['source_report']['documents'])
    assert 'No archived value is adopted' in progress['instructions']
    assert (board.data, board.source_qualification, board.valuation_results) == before
    assert progress == build_model_authoring_progress(board, contract=contract, remaining_turns=12)


def test_progress_reports_partial_zero_null_and_unexpected_without_financial_validation(author):
    board, contract = author
    board.data['_model_input_draft'] = {'model': {
        'opening_nwc': {'value': 0, 'kind': 'historical'},
        'shares': {'value': None}, 'unknown_driver': {'value': 99}},
        'scenarios': {'base': {'wacc': {'value': .1}}}}
    progress = build_model_authoring_progress(board, contract=contract)
    row = progress['draft']['scopes']['model']
    assert row['stored'] == ['opening_nwc', 'shares']
    assert row['unfilled'] == ['shares']
    assert row['unexpected'] == ['unknown_driver']
    assert 'opening_nwc' not in row['missing']
    assert 'historical_revenue' in row['missing']
    assert progress['draft']['scopes']['base']['stored'] == ['wacc']
    assert progress['draft']['proofs_validated'] is False
    assert progress['draft']['scenario_rationale_missing'] == contract['scenarios']


def test_progress_uses_supplied_native_schema_for_another_method(author):
    from bellomberg.valuation.preparation_methods import method_schema
    board, _ = author
    schema, _ = method_schema('exposure_analysis', {})
    contract = {'method_id': 'exposure_analysis', 'schema': schema, 'scenarios': []}
    progress = build_model_authoring_progress(board, contract=contract)
    assert set(progress['draft']['scopes']) == {'model'}
    assert progress['draft']['scopes']['model']['missing'] == sorted(schema)
    assert progress['draft']['scenario_rationale_missing'] == []
    assert progress['draft']['analysis_rationale_missing'] is True


def test_observed_contract_read_requires_all_pages_of_same_digest(author):
    board, contract = author
    messages = contract_page('method', 0, 8, 20)
    messages += contract_page('method', 12, 20, 20, call_id='call-2')
    partial = build_model_authoring_progress(board, contract=contract, messages=messages)
    assert partial['contract_reads']['method']['status'] == 'observed_partial'
    assert partial['contract_reads']['method']['next_offset'] == 8
    messages += contract_page('method', 8, 12, 20, call_id='call-3')
    complete = build_model_authoring_progress(board, contract=contract, messages=messages)
    assert complete['contract_reads']['method']['status'] == 'observed_complete'
    assert complete['contract_reads']['method']['current_source_revalidated'] is False
    # The suffix of a changed contract cannot borrow pages from the prior version.
    messages += contract_page('method', 12, 20, 20, digest='b' * 64, call_id='call-4')
    changed = build_model_authoring_progress(board, contract=contract, messages=messages)
    assert changed['contract_reads']['method']['status'] == 'observed_partial'
    assert changed['contract_reads']['method']['next_offset'] == 0


def test_saved_author_messages_are_used_but_other_specialist_reads_are_not(author):
    board, contract = author
    board.specialist_checkpoints = {
        'fundamentals:R1': {'messages': contract_page('evidence', 0, 20, 20)},
        'macro:R1': {'messages': contract_page('method', 0, 20, 20)},
    }
    progress = build_model_authoring_progress(board, contract=contract)
    assert set(progress['contract_reads']) == {'evidence'}
    explicit_empty = build_model_authoring_progress(board, contract=contract, messages=[])
    assert explicit_empty['contract_reads'] == {}


def test_failed_or_truncated_contract_page_is_not_a_completed_read(author):
    board, contract = author
    messages = tool_turn('get_candidate_model_inputs', {'scope': 'model',
        'contract_section': 'opening_nwc'}, {'ok': False,
        'error': 'No reported-balance NWC contract is available for these documents'})
    broken = contract_page('method', 0, 20, 20)
    broken[1]['content'][0]['content'] = broken[1]['content'][0]['content'][:-2] + '...[truncated]'
    messages += broken
    progress = build_model_authoring_progress(board, contract=contract, messages=messages)
    assert progress['contract_reads']['opening_nwc']['status'] == 'observed_error'
    assert 'No reported-balance NWC' in progress['contract_reads']['opening_nwc']['error']
    assert progress['contract_reads']['method']['status'] == 'unverified_output'
    assert 'source-dependent sections' in progress['instructions']


def test_consultations_depend_on_native_checks_not_presence_or_claimed_decision(author):
    board, contract = author
    board.data['_model_consultations'] = [
        {'id': 'one', 'desk': 'macro', 'status': 'complete', 'author_view_complete': True,
            'fundamentals_decision': {'decision': 'incorporated'}},
        {'id': 'old', 'desk': 'quant', 'status': 'complete', 'author_view_complete': True},
        {'id': 'new', 'desk': 'quant', 'status': 'complete', 'author_view_complete': False,
            'answer_read_offset': 10500},
    ]
    progress = build_model_authoring_progress(board, contract=contract,
        required_consultation_desks=['macro', 'quant', 'options'], consultation_checks={
            'one': {'current': True, 'received': True, 'decided': False},
            'old': {'current': False, 'received': True, 'decided': True},
            'new': {'current': True, 'received': False, 'decided': True}})
    rows = {row['id']: row for row in progress['consultations']['items']}
    assert rows['one']['received'] is True and rows['one']['decided'] is False
    assert rows['old']['current'] is False and rows['old']['decided'] is False
    assert rows['new']['received'] is False and rows['new']['decided'] is False
    assert rows['new']['next_read_offset'] == 10500
    assert progress['consultations']['missing_desks'] == ['options']
    assert progress['consultations']['undecided_ids'] == ['one']
    assert progress['consultations']['unreceived_ids'] == ['new']
    unverified = build_model_authoring_progress(board, contract=contract)
    assert all(row['received'] is None and row['decided'] is None
        for row in unverified['consultations']['items'])


def test_errors_remain_visible_and_are_never_turned_into_financial_values(author):
    board, contract = author
    board.data['_model_plan_last_error'] = {'status': 'historical_proofs_incomplete',
        'error': 'Shares proof missing'}
    board.data['_model_completion_error'] = 'Consultation outcomes missing'
    board.data['_model_compilation_attempts'] = [{'status': 'failed',
        'error': 'Operating arithmetic does not reconcile'}]
    messages = tool_turn('submit_candidate_model_plan', {},
        {'ok': False, 'error': 'Unknown driver or incomplete native envelope: model.shares'})
    progress = build_model_authoring_progress(board, contract=contract, messages=messages)
    assert 'incomplete native envelope' in progress['errors']['last_submission']['text']
    assert 'Shares proof missing' in progress['errors']['recorded_plan_error']['text']
    assert 'does not reconcile' in progress['errors']['last_compilation']['text']
    assert progress['draft']['present'] is False


def test_unreadable_submission_output_stays_explicitly_unverified(author):
    board, contract = author
    messages = tool_turn('submit_candidate_model_plan', {}, {'ok': True})
    messages[1]['content'][0]['content'] = '{"ok":...[truncated]'
    progress = build_model_authoring_progress(board, contract=contract, messages=messages)
    assert 'unverified_tool_output' in progress['errors']['last_submission']['text']
    assert not progress['draft']['present']


def test_partial_native_consultation_projection_does_not_infer_missing_checks(author):
    board, contract = author
    board.data['_model_consultations'] = [{'id': 'one', 'desk': 'macro',
        'status': 'complete', 'author_view_complete': True}]
    progress = build_model_authoring_progress(board, contract=contract,
        consultation_checks={'one': {'current': True}})
    row = progress['consultations']['items'][0]
    assert row['received'] is None and row['decided'] is None
    assert progress['consultations']['unverified_ids'] == ['one']


def test_exhausted_limit_is_informational_and_never_authorizes_extra_calls(author):
    board, contract = author
    progress = build_model_authoring_progress(board, contract=contract, remaining_turns=0)
    assert progress['remaining_turns'] == 0
    assert 'explicit native recovery' in progress['next_actions'][-1]
    assert 'Do not invent' in progress['instructions']
    assert 'approval' not in progress or progress['approval'] is not True
    assert not board.data.get('_model_input_draft')


@pytest.mark.parametrize('remaining', [True, -1, 1.5, '3'])
def test_invalid_remaining_turns_stay_unknown(author, remaining):
    board, contract = author
    progress = build_model_authoring_progress(board, contract=contract, remaining_turns=remaining)
    assert progress['remaining_turns'] is None and progress['remaining_turns_error']


def test_no_board_io_or_provider_is_needed_for_progress():
    board = SimpleNamespace(data={}, source_qualification={}, specialist_checkpoints={})
    progress = build_model_authoring_progress(board,
        contract={'schema': {}, 'scenarios': [], 'method_id': 'not_supplied'})
    assert progress['contract_status'] == 'unavailable'
    assert progress['admitted_document_ids'] == []
    assert progress['draft']['proofs_validated'] is False
