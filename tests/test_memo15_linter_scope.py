"""Scoped receipt tracing and visible linter failure; entirely synthetic."""
from copy import deepcopy
import json

import pytest

from bellomberg.agents import trade_idea as ti
from bellomberg.reporting import memo_linter as ml
from test_trade_idea_v4_pipeline import _gate_board, _gate_result, _section, TICKER, CUTOFF


def scoped_case(tool='get_fundamentals', multiple=False):
    board, result = _gate_board(), _gate_result()
    current = json.loads(board.tool_receipts[0]['output'])['data']
    payload = {'latest_period': current,
               'history': {'period_end': '2022-12-31', 'net_income_eur_m': 887.63}}
    if multiple:
        payload['same_period'] = {'period_end': '2026-09-01', 'free_cash_flow_eur_m': -321.45}
    board.tool_receipts[0].update(tool=tool, output=json.dumps({'data': payload}))
    result['evidence'][0].update(source='[src: ' + tool + '] synthetic',
                                 summary='Revenue 1234.5 EUR million')
    return board, result


@pytest.mark.parametrize('tool', ['get_fundamentals', 'get_financial_history'])
def test_old_period_number_does_not_reenter_v4_claim_pool(tool):
    board, result = scoped_case(tool)
    raw = deepcopy(board.tool_receipts)
    _section(result, 'business')['paragraphs'] = [
        "Nell'ultimo periodo 2026-09-01 l'utile netto e' 887,63 milioni di euro."]
    bound, numeric = ti._bound_evidence_details(result, board, TICKER, CUTOFF)
    assert result['evidence'][0]['id'] in numeric
    assert bound[result['evidence'][0]['id']]['output'] == raw[0]['output']
    gaps = ti._numeric_claim_gaps(result, board, TICKER, CUTOFF, {})
    assert any('dossier.business[0]: valore 887,63 milioni' in gap for gap in gaps), gaps
    assert board.tool_receipts == raw


@pytest.mark.parametrize('multiple', [False, True])
def test_current_period_numbers_keep_units_and_signs(multiple):
    board, result = scoped_case(multiple=multiple)
    if multiple:
        _section(result, 'business')['paragraphs'].append('Free cash flow -321,45 milioni di euro.')
    assert ti._numeric_claim_gaps(result, board, TICKER, CUTOFF, {}) == []
    if multiple:
        _section(result, 'business')['paragraphs'][-1] = 'Free cash flow 321,45 milioni di euro.'
        assert any('321,45 milioni' in gap for gap in
                   ti._numeric_claim_gaps(result, board, TICKER, CUTOFF, {}))


def test_historical_contract_and_receipt_remain_unchanged():
    board, result = scoped_case()
    board.execution_policy = None
    # Legacy still consumes the original receipt. This fix does not upgrade paid runs.
    result = {'summary': 'Historical net income 887.63 USD [src: get_fundamentals].',
              'evidence': result['evidence'],
              'dossier': [{'key': 'executive', 'evidence_ids': ['ev1'], 'paragraphs': [], 'tables': []}]}
    raw = deepcopy(board.tool_receipts)
    assert ti._numeric_claim_gaps(result, board, TICKER, CUTOFF, {}) == []
    assert board.tool_receipts == raw


@pytest.mark.parametrize('check', ['_check_nav_percentages', '_check_scenario_sum',
    '_check_cash_quadrature', '_check_src_coverage', '_check_drawdown_duplicati'])
def test_each_broken_check_is_visible_even_without_other_warnings(monkeypatch, check):
    def fail(*args):
        raise ValueError('PRIVATE_PAYLOAD_MUST_NOT_LEAK')
    monkeypatch.setattr(ml, check, fail)
    result = ml.build_linter_block('Synthetic memo with no numeric claim.', {})
    assert 'CHECK_UNAVAILABLE' in result
    assert 'ValueError' in result
    assert 'PRIVATE_PAYLOAD_MUST_NOT_LEAK' not in result


@pytest.mark.parametrize('check', ['_check_nav_percentages', '_check_src_coverage'])
def test_failure_does_not_hide_other_checks_or_change_memo(monkeypatch, check):
    def fail(*args):
        raise RuntimeError('SYNTHETIC')
    monkeypatch.setattr(ml, check, fail)
    memo = '## Tabella Scenari\n| Caso | Prob |\n| --- | --- |\n| A | 80% |\n| B | 80% |'
    original = memo
    block = ml.build_linter_block(memo, {})
    assert 'CHECK_UNAVAILABLE' in block and '160%' in block
    assert memo == original


def test_clean_weekly_memo_stays_without_warnings():
    assert ml.build_linter_block('Synthetic clean memo.', {}) == ''


def test_unavailable_notice_follows_output_language(monkeypatch):
    def fail(*args):
        raise ValueError('SYNTHETIC')
    monkeypatch.setattr(ml, '_check_scenario_sum', fail)
    block = ml.build_linter_block('Synthetic memo.', {}, language='en')
    assert 'CHECK_UNAVAILABLE' in block and 'scenario' in block.lower()
    assert 'not performed' in block
