"""Reject an inconsistent FCFF terminal before spending on another scenario."""
from copy import deepcopy

import pytest

from test_input_preparation import _bundle, _documents, _operating_plan


def test_partial_forecast_is_not_completed_with_defaults():
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan = _operating_plan()
    for scenario in plan['scenarios'].values():
        del scenario['da_tan_pct']
    before = deepcopy(plan)
    assert forecast_arithmetic(plan) == {}
    assert plan == before


def test_arithmetic_exposes_revenue_denominator_and_preserves_source_plan():
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan = _operating_plan()
    before = deepcopy(plan)
    result = forecast_arithmetic(plan)
    assert result['bear']['final_year']['tangible_depreciation'] == 5
    assert result['bear']['final_year']['ebit'] == 15
    assert result['bear']['research_normalization_adjustment'] == 0
    assert plan == before


def test_bad_terminal_blocks_before_next_scenario():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    plan = _operating_plan()
    plan['scenarios']['bear']['terminal_bridge']['value']['normalized_ebit'] = 20
    calls = []
    def proposed(dossier, contract):
        stage = contract['preparation_stage']
        calls.append(stage['scope'])
        values = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                'rationale': 'Synthetic sourced scenario.'}
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=StagedProposer(proposed))
    assert result['status'] == 'incomplete'
    assert 'base' not in calls and 'bull' not in calls
    assert 'FCFF forecast arithmetic' in str(result['issues'])


def test_invalid_saved_terminal_blocks_before_any_request():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.preparation_seed import make_seed
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    captured = {}
    def capture(dossier, contract):
        captured.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
        return _operating_plan()
    prepare_method_inputs(_bundle(), documents=_documents(), propose=capture)
    plan = _operating_plan()
    plan['scenarios']['bear']['terminal_bridge']['value']['normalized_ebit'] = 20
    seed = make_seed(captured['dossier'], captured['contract'], plan)
    with pytest.raises(ValueError, match='FCFF forecast arithmetic'):
        StagedProposer(lambda *args: pytest.fail('invalid prior work must not spend'), seed=seed)(
            captured['dossier'], captured['contract'])


def _context():
    from bellomberg.valuation.operating_adapter import SCHEMA
    plan = _operating_plan()
    for scope in plan['scenarios']:
        del plan['scenarios'][scope]['terminal_bridge']
    return ({'method_id': 'operating_fcff', 'completed_plan': plan},
            {'schema': {'terminal_bridge': SCHEMA['terminal_bridge']},
             'preparation_stage': {'scope': 'bear', 'drivers': ['terminal_bridge']}})


def test_new_prompt_explains_engine_denominators_and_reuses_after_restart(tmp_path):
    from test_preparation_ai import _proposer
    dossier, contract = _context()
    original = deepcopy(dossier)
    p = _proposer(tmp_path)
    view = p.prepare_context(dossier, contract, source_dossier=dossier)
    assert view['fcff_engine_semantics']['da_tan_pct_denominator'] == 'current-period revenue'
    assert view['derived_fcff_forecasts']['bear']['final_year']['ebit'] == 15
    answer = p(view, contract)
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail('duplicate paid request'))
    restarted.metadata = lambda _: pytest.fail('cached answer must not need live pricing')
    replay = restarted.prepare_context(dossier, contract, source_dossier=dossier)
    assert replay == view and restarted(replay, contract) == answer
    assert dossier == original and restarted.summary()['requests'] == 1


def test_original_paid_prompt_remains_exact_and_is_not_repaid(tmp_path):
    from test_preparation_ai import _proposer
    dossier, contract = _context()
    p = _proposer(tmp_path)
    answer = p(dossier, contract)  # Literal pre-guidance request.
    p.call = lambda **_: pytest.fail('legacy paid request repeated')
    p.metadata = lambda _: pytest.fail('legacy paid request repriced')
    view = p.prepare_context(dossier, contract, source_dossier=dossier)
    assert view == dossier and p(view, contract) == answer
    assert p.summary()['requests'] == 1


def test_inconsistent_operating_revenue_blocks_before_terminal_or_next_scenario():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    plan = _operating_plan()
    plan['scenarios']['bear']['revenue_build']['value']['unit_price'][0] *= 1.000000002
    calls = []
    def proposed(dossier, contract):
        stage = contract['preparation_stage']
        calls.append((stage['scope'], list(stage['drivers'])))
        values = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                'rationale': 'Synthetic operating assumptions.'}
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=StagedProposer(proposed))
    assert result['status'] == 'incomplete'
    assert not any(scope in ('base','bull') or names == ['terminal_bridge'] for scope,names in calls)
    assert 'revenue_build' in str(result['issues'])


def test_revenue_check_preserves_engine_tolerance_and_rejects_a_saved_mismatch():
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan = _operating_plan()
    plan['scenarios']['base']['revenue_build']['value']['unit_price'][0] *= 1.0000000005
    assert forecast_arithmetic(plan)['base']['final_year']['revenue'] == 100
    plan['scenarios']['base']['revenue_build']['value']['unit_price'][0] *= 1.000000002
    before = deepcopy(plan)
    with pytest.raises(ValueError, match='base.*revenue_build'):
        forecast_arithmetic(plan)
    assert plan == before


def _accounting_bridge_case():
    """Independent synthetic operating judgments, not inferred from engine EBIT."""
    plan = _operating_plan(years=4)
    plan['model']['capdev_amortization_years']['value'] = 2
    revenue = [100., 120., 108., 129.6]
    depreciation = [10., 12., 9., 13.]
    capitalization = [4., 6., 9., 6.]
    opening_amortization = [2., 1., 0., 0.]
    # Two-year life includes the current cohort; opening runoff is separate.
    amortization = [4., 6., 7.5, 7.5]
    targets = {}
    for scope, starting_margin in (('bear', .40), ('base', .45), ('bull', .50)):
        reported_margin = [starting_margin + .01 * index for index in range(4)]
        operating_profit = [sales * (margin - .10 - .10)
                            for sales, margin in zip(revenue, reported_margin)]
        scenario = plan['scenarios'][scope]
        paths = {
            'revenue_growth': [0., .20, -.10, .20],
            'gross_margin': [margin + dep / sales
                             for margin, dep, sales in zip(reported_margin, depreciation, revenue)],
            'rnd_pct': [.10] * 4, 'sga_pct': [.10] * 4,
            'capdev_pct': [cap / sales for cap, sales in zip(capitalization, revenue)],
            'da_tan_pct': [dep / sales for dep, sales in zip(depreciation, revenue)],
            'tax_rate': [.25] * 4, 'capex_pct': [.08] * 4, 'nwc_pct': [.05] * 4,
            'opening_intangible_amortization': opening_amortization,
        }
        for driver, values in paths.items():
            scenario[driver]['value'] = deepcopy(values)
        scenario['revenue_build']['value']['unit_price'] = [sales / 10. for sales in revenue]
        scenario['terminal_bridge']['value'].update(
            normalized_ebit=operating_profit[-1], capitalized_research_adjustment=1.5)
        targets[scope] = {
            'revenue': revenue, 'operating_profit': operating_profit,
            'depreciation': depreciation, 'capitalization': capitalization,
            'amortization': amortization, 'opening_amortization': opening_amortization,
            'working_capital_change': [5., 1., -.6, 1.08],
        }
    return plan, targets


def _accounting_numbers(plan, scope):
    from bellomberg.valuation.dcf_buyside_v3 import _scenario_numbers
    scenario = {name: item['value'] for name, item in plan['scenarios'][scope].items()}
    return _scenario_numbers(
        {'documented_inputs': True,
         'capdev_amortization_years': plan['model']['capdev_amortization_years']['value']},
        scenario, plan['model']['historical_revenue']['value'],
        nwc0=plan['model']['opening_nwc']['value'])


def test_aggregate_da_bridge_preserves_independent_operating_profit_in_every_period():
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan, targets = _accounting_bridge_case()
    before = deepcopy(plan)
    forecasts = forecast_arithmetic(plan)
    for scope, target in targets.items():
        numbers = _accounting_numbers(plan, scope)
        expected_ebitda = [op + dep + cap for op, dep, cap in zip(
            target['operating_profit'], target['depreciation'], target['capitalization'])]
        expected_ebit = [op + cap - amort for op, cap, amort in zip(
            target['operating_profit'], target['capitalization'], target['amortization'])]
        expected_tax = [max(ebit, 0.) * .25 for ebit in expected_ebit]
        expected_fcff = [op + dep - tax - sales * .08 - delta
                         for op, dep, tax, sales, delta in zip(
                             target['operating_profit'], target['depreciation'], expected_tax,
                             target['revenue'], target['working_capital_change'])]
        assert numbers['revenue'] == pytest.approx(target['revenue'])
        assert numbers['capitalized_development'] == pytest.approx(target['capitalization'])
        assert numbers['tangible_depreciation'] == pytest.approx(target['depreciation'])
        assert numbers['research_amortization'] == pytest.approx(target['amortization'])
        assert numbers['ebitda'] == pytest.approx(expected_ebitda)
        assert numbers['ebit'] == pytest.approx(expected_ebit)
        assert numbers['cash_tax'] == pytest.approx(expected_tax)
        assert numbers['ufcf'] == pytest.approx(expected_fcff)
        assert forecasts[scope]['final_year']['ebit'] == pytest.approx(expected_ebit[-1])
        assert forecasts[scope]['research_normalization_adjustment'] == pytest.approx(1.5)
        assert (forecasts[scope]['final_year']['ebit'] + 1.5
                == pytest.approx(target['operating_profit'][-1]))

        partial = deepcopy(plan)
        partial['scenarios'][scope]['gross_margin']['value'] = [
            margin - .10 * dep / sales for margin, dep, sales in zip(
                plan['scenarios'][scope]['gross_margin']['value'],
                target['depreciation'], target['revenue'])]
        wrong = _accounting_numbers(partial, scope)
        residual = [.10 * dep for dep in target['depreciation']]
        assert wrong['ebitda'] == pytest.approx([value - gap
                                               for value, gap in zip(expected_ebitda, residual)])
        assert wrong['ebit'] == pytest.approx([value - gap
                                             for value, gap in zip(expected_ebit, residual)])
        assert wrong['ufcf'] == pytest.approx([value - gap * .75
                                             for value, gap in zip(expected_fcff, residual)])
    assert plan == before


@pytest.mark.parametrize('scope', ['bear', 'base', 'bull'])
def test_terminal_self_consistency_cannot_prove_the_independent_operating_profit(scope):
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan, targets = _accounting_bridge_case()
    target = targets[scope]
    scenario = plan['scenarios'][scope]
    scenario['gross_margin']['value'] = [margin - .10 * dep / sales
                                         for margin, dep, sales in zip(
                                             scenario['gross_margin']['value'],
                                             target['depreciation'], target['revenue'])]
    before = deepcopy(plan)
    with pytest.raises(ValueError, match=scope + '.*EBIT normalizzato'):
        forecast_arithmetic(plan)
    assert plan == before

    numbers = _accounting_numbers(plan, scope)
    # Rebuilding the terminal from the same erroneous EBIT passes local checks;
    # it still fails the independent economic identity. No production fix here.
    scenario['terminal_bridge']['value']['normalized_ebit'] = (
        numbers['ebit'][-1] + target['amortization'][-1] - target['capitalization'][-1])
    before = deepcopy(plan)
    accepted = forecast_arithmetic(plan)[scope]
    normalized = accepted['final_year']['ebit'] + accepted['research_normalization_adjustment']
    assert normalized == pytest.approx(target['operating_profit'][-1] - 1.3)
    assert normalized != pytest.approx(target['operating_profit'][-1])
    assert plan == before


@pytest.mark.parametrize('error', ['already_net_of_capitalization', 'opening_amortization_in_costs'])
def test_gross_research_and_separate_opening_amortization_prevent_double_counting(error):
    plan, targets = _accounting_bridge_case()
    before = deepcopy(plan)
    wrong_plan = deepcopy(plan)
    for scope, target in targets.items():
        correct = _accounting_numbers(plan, scope)
        shift = (target['capitalization'] if error == 'already_net_of_capitalization'
                 else [-value for value in target['opening_amortization']])
        wrong_plan['scenarios'][scope]['rnd_pct']['value'] = [
            .10 - delta / sales for delta, sales in zip(shift, target['revenue'])]
        wrong = _accounting_numbers(wrong_plan, scope)
        assert wrong['research_amortization'] == pytest.approx(target['amortization'])
        assert wrong['ebitda'] == pytest.approx([value + delta
                                               for value, delta in zip(correct['ebitda'], shift)])
        assert wrong['ebit'] == pytest.approx([value + delta
                                             for value, delta in zip(correct['ebit'], shift)])
        assert wrong['ufcf'] == pytest.approx([value + delta * .75
                                             for value, delta in zip(correct['ufcf'], shift)])
    assert plan == before
