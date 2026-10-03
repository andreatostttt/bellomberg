"""Opening observations stay fixed; equity value judgments follow cash forecasts."""
from copy import deepcopy

import pytest

from test_preparation_historical_first import _case
from test_preparation_growth_thesis import _thesis


@pytest.mark.parametrize('growth', (False, True))
@pytest.mark.parametrize('gap', (False, True))
def test_equity_adjustment_is_a_final_sourced_judgment_not_an_opening_fact(growth, gap):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    calls, histories = [], []
    for scope, amount in zip(('bear', 'base', 'bull'), (1., 2., 3.)):
        item = plan['scenarios'][scope]['equity_adjustments']
        for key in ('evidence_quote', 'quoted_value', 'quoted_unit', 'period_quote'):
            item.pop(key)
        item.update(value=None if gap and scope == 'bear' else amount, kind='analyst_estimate',
                    rationale='Recovery of separately identified non-operating assets; forecast taxes and reinvestment exclude this claim.')

    def propose(dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            calls.append('growth')
            assert 'equity_adjustments' in stage['link_drivers']
            assert 'net_debt' not in stage['link_drivers']
            return _thesis(plan, contract)
        scope, names = stage['scope'], stage['drivers']
        calls.append((scope, tuple(names)))
        if 'equity_adjustments' in names:
            if growth:
                assert names == ['terminal_bridge', 'equity_adjustments']
                assert 'tax_rate' in dossier['completed_plan']['scenarios'][scope]
            else:
                assert names == ['equity_adjustments']
                assert 'terminal_bridge' in dossier['completed_plan']['scenarios'][scope]
            assert 'equity_adjustment_policy' in stage
            assert histories and all(set(row) == {'net_debt'}
                                     for row in histories[0]['scenarios'].values())
        source = plan['model'] if scope == 'model' else plan['scenarios'][scope]
        return {'drivers': {name: deepcopy(source[name]) for name in names},
                'rationale': 'Documented synthetic step'}

    result = prepare_method_inputs(bundle, documents=documents, propose=StagedProposer(
        propose, historical_first=True, on_historical=lambda h, *_: histories.append(h),
        growth_thesis_first=growth))
    assert result['status'] == ('incomplete' if gap else 'prepared'), result['issues']
    assert len(histories) == 1
    assert all('equity_adjustments' not in row for row in histories[0]['scenarios'].values())
    assert [call for call in calls if isinstance(call, tuple) and call[1] == ('net_debt',)] == [
        ('bear', ('net_debt',))]
    assert all(row['net_debt'] == histories[0]['scenarios']['bear']['net_debt']
               for row in histories[0]['scenarios'].values())
    if not gap:
        for scope in ('bear', 'base', 'bull'):
            scenario_calls = [call for call in calls if isinstance(call, tuple) and call[0] == scope]
            assert scenario_calls[-1] == (scope, ('terminal_bridge', 'equity_adjustments')
                                         if growth else ('equity_adjustments',))
        records = result['proposal']['method_records']
        assert {row['kind'] for row in records if row['driver'] == 'equity_adjustments'} == {'analyst_estimate'}
