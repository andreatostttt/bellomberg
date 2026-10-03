"""Source-bound Growth thesis before operating FCFF forecasts, offline only."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from test_preparation_historical_first import _case
from test_preparation_ai import _proposer


ANCHORS = ('revenue_build', 'revenue_growth', 'gross_margin', 'capex_pct', 'nwc_pct')


def _thesis(plan, contract):
    years = [row['end'] for row in plan['model']['calendar']['value']['periods']]
    stage = contract['preparation_stage']
    names = stage['link_drivers']
    model_links = {name: {'periods': years,
                          'mechanism': name + ' follows the capitalized development investment case',
                          'impact': name + ' changes amortization and taxable operating profit',
                          'risk': 'The useful life may differ from the forecast',
                          'evidence_ids': ['annual-1']}
                   for name in stage['model_link_drivers']}
    return {'growth_thesis': {
        'historical_basis': {'period': plan['model']['calendar']['value']['valuation_date'],
                             'summary': 'Observed operating base before analyst forecasts',
                             'evidence_ids': ['annual-1']},
        'business_drivers': [{'name': 'Consolidated operating demand',
                              'basis': 'consolidated_driver',
                              'historical': 'Historical revenue is the observed base',
                              'guidance': 'No management forecast is asserted by this fixture',
                              'judgment': 'Demand and capacity determine the forward path',
                              'risk': 'Lower utilization can reduce cash conversion',
                              'evidence_ids': ['annual-1']}],
        'model_links': model_links,
        'model_anchors': {name: deepcopy(plan['model'][name]) for name in stage['model_link_drivers']},
        'scenarios': {scope: {
            'periods': years, 'thesis': scope + ' demand, margin and reinvestment path',
            'risk': 'Capacity and margin may diverge from the scenario',
            'links': {name: {'periods': years[-1:] if stage['driver_timings'][name] == 'terminal' else years,
                             'mechanism': scope + ' ' + name + ' follows business demand',
                             'impact': name + ' changes cash flow or equity value',
                             'risk': name + ' may differ from evidence-bound expectations',
                             'evidence_ids': ['annual-1']} for name in names},
            'anchors': {name: deepcopy(plan['scenarios'][scope][name]) for name in ANCHORS}}
            for scope in ('bear', 'base', 'bull')}}}


def _run(plan, *, missing=False, mismatch=False):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, _ = _case()
    calls = []
    def propose(dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            calls.append('thesis')
            if missing:
                return {'growth_thesis': None}
            thesis = _thesis(plan, contract)
            if mismatch:
                thesis['growth_thesis']['scenarios']['base']['anchors']['gross_margin']['value'][0] += .01
            return thesis
        scope, names = stage['scope'], stage['drivers']
        calls.append((scope, tuple(names)))
        source = plan['model'] if scope == 'model' else plan['scenarios'][scope]
        return {'drivers': {name: deepcopy(source[name]) for name in names},
                'rationale': 'Synthetic sourced stage'}
    staged = StagedProposer(propose, historical_first=True, on_historical=lambda *_: None,
                            growth_thesis_first=True)
    result = prepare_method_inputs(bundle, documents=documents, propose=staged)
    return result, staged, calls


def test_growth_thesis_precedes_forecast_and_is_consumed_in_compiled_drivers():
    _, _, plan = _case()
    result, staged, calls = _run(plan)
    assert result['status'] == 'prepared', result['issues']
    assert calls.index('thesis') > calls.index(('model', ('shares',)))
    assert ('model', ('capdev_amortization_years',)) not in calls
    assert calls.index('thesis') < next(i for i, call in enumerate(calls)
                                       if isinstance(call, tuple) and call[0] == 'base'
                                       and 'wacc' in call[1])
    assert staged.growth_thesis['scenarios']['base']['anchors']['gross_margin']['value'] == plan['scenarios']['base']['gross_margin']['value']
    row = next(row for row in result['proposal']['method_records']
               if row['scenario'] == 'base' and row['driver'] == 'gross_margin')
    assert row['value'] == plan['scenarios']['base']['gross_margin']['value']
    assert 'follows business demand' in row['rationale']
    assert 'changes cash flow' in result['proposal']['plan']['scenario_rationale']['base']
    opening = next(row for row in result['proposal']['method_records']
                   if row['scenario'] == 'model' and row['driver'] == 'capdev_amortization_years')
    assert 'capitalized development investment case' in opening['rationale']


def test_growth_thesis_changes_verified_driver_and_excel_cell(tmp_path):
    from openpyxl import load_workbook
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.preparation_service import prepare_and_generate

    results = []
    for variant, margin in enumerate((.4, .42)):
        bundle, documents, plan = _case()
        plan['scenarios']['base']['gross_margin']['value'][0] = margin
        def propose(_dossier, contract):
            stage = contract['preparation_stage']
            if stage.get('purpose') == 'growth_thesis':
                return _thesis(plan, contract)
            source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
            return {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                    'rationale': 'Synthetic sourced stage'}
        result = prepare_and_generate(bundle, documents=documents,
            propose=StagedProposer(propose, historical_first=True,
                on_historical=lambda *_: None, growth_thesis_first=True),
            output_dir=tmp_path / str(variant))
        assert result['valuation_usability']['usable'], result.get('error')
        wb = load_workbook(result['path'], data_only=False, read_only=True)
        try:
            results.append((result['preparation']['proposal']['plan']['scenarios']['base']['gross_margin']['value'][0],
                            wb['Assumptions']['D9'].value))
        finally:
            wb.close()
    assert results == [(.4, .4), (.42, .42)]


def test_common_fcff_opt_in_orders_history_thesis_model_judgment_without_checkpoint():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    calls = []
    def propose(_dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            calls.append('thesis')
            assert 'net_debt' not in stage['link_drivers']
            assert 'equity_adjustments' in stage['link_drivers']
            return _thesis(plan, contract)
        calls.append((stage['scope'], tuple(stage['drivers'])))
        source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                'rationale': 'Synthetic sourced stage'}
    staged = StagedProposer(propose, growth_thesis_first=True)
    result = prepare_method_inputs(bundle, documents=documents, propose=staged)
    assert result['status'] == 'prepared', result['issues']
    assert calls.index(('model', ('shares',))) < calls.index('thesis')
    assert calls.index(('bear', ('net_debt',))) < calls.index('thesis')
    assert ('model', ('capdev_amortization_years',)) not in calls
    assert 'capdev_amortization_years' in staged.growth_thesis['model_links']


def test_missing_capitalization_link_stops_before_model_judgment():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    calls = []
    def propose(_dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            thesis = _thesis(plan, contract)
            thesis['growth_thesis']['model_links'].pop('capdev_amortization_years')
            calls.append('thesis')
            return thesis
        calls.append((stage['scope'], tuple(stage['drivers'])))
        source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                'rationale': 'Synthetic sourced stage'}
    result = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(propose, historical_first=True, on_historical=lambda *_: None,
                               growth_thesis_first=True))
    assert result['status'] == 'incomplete'
    assert any('prospective model judgments not covered' in row['reason'] for row in result['issues'])
    assert ('model', ('capdev_amortization_years',)) not in calls


def test_capitalization_judgment_is_consumed_directly_from_thesis():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    calls = []
    def propose(_dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            thesis = _thesis(plan, contract)
            thesis['growth_thesis']['model_anchors']['capdev_amortization_years']['value'] += 1
            calls.append('thesis')
            return thesis
        calls.append((stage['scope'], tuple(stage['drivers'])))
        source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                'rationale': 'Synthetic sourced stage'}
    result = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(propose, historical_first=True, on_historical=lambda *_: None,
                               growth_thesis_first=True))
    assert result['status'] == 'prepared', result['issues']
    assert ('model', ('capdev_amortization_years',)) not in calls
    assert result['proposal']['plan']['model']['capdev_amortization_years']['value'] == plan['model']['capdev_amortization_years']['value'] + 1


@pytest.mark.parametrize('failure', ('missing', 'mismatch'))
def test_missing_thesis_stops_but_its_changed_judgment_drives_forecast(failure):
    _, _, plan = _case()
    result, staged, calls = _run(plan, missing=failure == 'missing', mismatch=failure == 'mismatch')
    assert 'thesis' in calls
    if failure == 'missing':
        assert result['status'] == 'incomplete'
        assert staged.growth_thesis is None
        assert not any(isinstance(call, tuple) and call[0] in ('bear', 'base', 'bull')
                       and 'wacc' in call[1] for call in calls)
    else:
        assert result['status'] == 'prepared', result['issues']
        expected = plan['scenarios']['base']['gross_margin']['value'][0] + .01
        assert result['proposal']['plan']['scenarios']['base']['gross_margin']['value'][0] == expected
        assert not any(isinstance(call, tuple) and 'gross_margin' in call[1] for call in calls)


def test_growth_thesis_uses_same_budgeted_journal_and_exact_replay(tmp_path):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    paid = []
    def call(**wire):
        contract = json.loads(wire['messages'][0]['content'])['contract']
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            answer = _thesis(plan, contract)
            paid.append('thesis')
        else:
            scope = stage['scope']
            source = plan['model'] if scope == 'model' else plan['scenarios'][scope]
            answer = {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                      'rationale': 'Synthetic journal stage'}
            paid.append((scope, tuple(stage['drivers'])))
        return SimpleNamespace(id='growth-stage-' + str(len(paid)), model=wire['model'],
            provider='synthetic', stop_reason='end_turn', usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type='text', text=json.dumps(answer))])
    journal = _proposer(tmp_path, limit=1000, call=call)
    journal.metadata = lambda _: {'id': 'synthetic/model', 'context_length': 1000000,
        'pricing': {'prompt': '0.00001', 'completion': '0.00002'}}
    first = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(journal, historical_first=True, on_historical=lambda *_: None,
                               growth_thesis_first=True))
    assert first['status'] == 'prepared', (first['issues'], paid[-1] if paid else None, len(paid))
    assert paid.count('thesis') == 1
    before = journal.summary()
    assert before['requests'] == len(paid) and before['spent_usd'] > 0
    replay = _proposer(tmp_path, limit=1000,
                       call=lambda **_: pytest.fail('paid call on exact journal replay'))
    replay.metadata = journal.metadata
    second = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(replay, historical_first=True, on_historical=lambda *_: None,
                               growth_thesis_first=True))
    assert second['status'] == 'prepared', second['issues']
    assert second['proposal']['plan'] == first['proposal']['plan']
    assert replay.summary() == before
