"""A full dated period is as precise as its ending date; no inferred mapping."""
from copy import deepcopy

import pytest

from test_preparation_historical_first import _case
from test_preparation_growth_thesis import _thesis


def _run(change):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    bundle, docs, plan = _case()
    received = []

    def propose(_dossier, contract):
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            answer = _thesis(plan, contract)
            change(answer['growth_thesis'], plan['model']['calendar']['value'])
            received.append(deepcopy(answer))
            return answer
        source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(source[name]) for name in stage['drivers']},
                'rationale': 'Documented test case'}
    staged = StagedProposer(propose, historical_first=True, on_historical=lambda *_: None,
                            growth_thesis_first=True)
    result = prepare_method_inputs(bundle, documents=docs, propose=staged)
    return result, staged, received


def _full_intervals(thesis, calendar):
    aliases = {p['end']: p['start']+'|'+p['end'] for p in calendar['periods']}
    def replace(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'periods': node[key] = [aliases[day] for day in value]
                else: replace(value)
        elif isinstance(node, list):
            for item in node: replace(item)
    replace(thesis)
    opening = calendar['valuation_date']
    thesis['historical_basis']['period'] = f'2022-01-01 to {opening} (reported history; TTM ending {opening})'


def test_exact_calendar_intervals_and_historical_coverage_are_preserved():
    result, staged, received = _run(_full_intervals)
    assert result['status'] == 'prepared', result['issues']
    assert staged.growth_thesis == received[0]['growth_thesis']


@pytest.mark.parametrize('fault', ('opening', 'future_qualifier', 'start', 'invalid_date',
                                   'forecast_start', 'forecast_end', 'duplicate_alias', 'order'))
def test_approximate_or_conflicting_ranges_are_rejected(fault):
    def change(thesis, calendar):
        _full_intervals(thesis, calendar)
        if fault == 'opening': thesis['historical_basis']['period'] = '2022-01-01 to 2026-01-01'
        elif fault == 'future_qualifier': thesis['historical_basis']['period'] += ' (through 2027-01-01)'
        elif fault == 'start': thesis['historical_basis']['period'] = '2026-01-01 to '+calendar['valuation_date']
        elif fault == 'invalid_date': thesis['historical_basis']['period'] = '2022-99-01 to '+calendar['valuation_date']
        else:
            periods = thesis['scenarios']['base']['links']['gross_margin']['periods']
            if fault == 'forecast_start': periods[0] = '2026-01-02|'+calendar['periods'][0]['end']
            elif fault == 'forecast_end': periods[0] = calendar['periods'][0]['start']+'|2027-01-01'
            elif fault == 'duplicate_alias': periods.append(calendar['periods'][0]['end'])
            elif fault == 'order': periods.reverse()
    result, _, _ = _run(change)
    assert result['status'] == 'incomplete'
    assert any('growth thesis:' in issue['reason'] for issue in result['issues'])
