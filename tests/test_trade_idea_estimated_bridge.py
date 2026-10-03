"""Pending source admission proves opening facts before the economic equity bridge."""
from copy import deepcopy

from bellomberg.valuation.input_preparation import _catalog, _day, prepare_method_inputs
from bellomberg.valuation.trade_idea_model import _source_problems
from test_trade_idea_historical_admission import preparation_case


def _review(q, plan):
    report = {**q['source_report'], 'source_plan': plan}
    catalog, issues, _ = _catalog(report['documents'], _day(q['as_of']))
    assert not issues
    return _source_problems(q['bundle'], report, catalog)


def test_pending_checkpoint_admits_proved_net_debt_without_premature_equity_judgment(tmp_path):
    q, plan = preparation_case(tmp_path)
    for scenario in plan['scenarios'].values():
        del scenario['equity_adjustments']
    problems, basis = _review(q, plan)
    assert problems == []
    assert 'net_debt' in basis['historical_drivers']
    assert 'equity_adjustments' not in basis['historical_drivers']
    assert basis['prepared_plan_available'] is False


def test_pending_checkpoint_still_requires_net_debt_in_every_scenario(tmp_path):
    q, plan = preparation_case(tmp_path)
    for scenario in plan['scenarios'].values():
        del scenario['equity_adjustments']
    del plan['scenarios']['bull']['net_debt']
    problems, _basis = _review(q, plan)
    assert any('bull.net_debt' in reason for reason in problems)


def test_pending_plan_reproves_any_supplied_historical_equity_claim(tmp_path):
    q, plan = preparation_case(tmp_path)
    plan['scenarios']['bear']['equity_adjustments']['record_pointer'] = '/observations/99'
    problems, _basis = _review(q, plan)
    assert any('equity_adjustments' in reason for reason in problems)


def test_pending_final_plan_allows_sourced_scenario_equity_estimates(tmp_path):
    q, plan = preparation_case(tmp_path)
    for scope, scenario in plan['scenarios'].items():
        previous = scenario['equity_adjustments']
        scenario['equity_adjustments'] = {
            'value': {'bear': -1.0, 'base': 0.5, 'bull': 1.0}[scope],
            'kind': 'analyst_estimate', 'evidence_ids': previous['evidence_ids'],
            'rationale': 'Scenario-specific economic judgment after sourced operating forecasts.',
            'valid_until': previous['valid_until'],
            'valid_until_basis': deepcopy(previous['valid_until_basis'])}
    problems, basis = _review(q, plan)
    assert problems == []
    assert basis['prepared_plan_available'] is True
    compiled = prepare_method_inputs(q['bundle'], documents=q['source_report']['documents'],
        propose=lambda *_: deepcopy(plan), source_report={**q['source_report'], 'source_plan': plan})
    assert compiled['status'] == 'prepared', compiled['issues']


def test_unqualified_legacy_source_plan_still_requires_historical_equity_bridge(tmp_path):
    q, plan = preparation_case(tmp_path)
    report = {**q['source_report'], 'source_plan': plan}
    report.pop('historical_preparation', None)
    report.pop('historical_preparation_plan', None)
    for scenario in plan['scenarios'].values():
        item = scenario['equity_adjustments']
        item['kind'] = 'analyst_estimate'
        item.pop('record_pointer')
    catalog, issues, _ = _catalog(report['documents'], _day(q['as_of']))
    assert not issues
    problems, _basis = _source_problems(q['bundle'], report, catalog)
    assert any('equity_adjustments' in reason for reason in problems)
