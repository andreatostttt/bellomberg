"""EV/equity bridges can cite actual leaf components of the verified balance."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from bellomberg.valuation.input_preparation import _fact_proof
from test_balance_working_capital import case


def bridge_case():
    primary, ledger, _ = case()
    body = json.loads(ledger['text'])
    terms = []
    for tag, sign in [('us-gaap:LongTermDebtNoncurrent', 1),
                      ('us-gaap:CashAndCashEquivalentsAtCarryingValue', -1)]:
        index, fact = next((i, f) for i, f in enumerate(body['components']) if f['reported_tag'] == tag)
        root = f'/components/{index}'
        terms.append({'coefficient': sign, 'evidence_ids': [ledger['id']],
            'quoted_value': float(Decimal(fact['value_exact'])), 'quoted_unit': fact['unit'],
            'evidence_pointer': {'value': root+'/value_exact', 'unit': root+'/unit', 'period': root+'/end'}})
    item = {'value': sum(t['coefficient']*t['quoted_value']/1e6 for t in terms),
            'evidence_ids': [ledger['id'], primary['id']],
            'calculation': {'operation': 'sum', 'terms': terms}}
    return item, [primary, ledger]


def prove(item, documents, **kwargs):
    return _fact_proof(kwargs.get('driver', 'net_debt'), item, documents,
        kwargs.get('unit', 'EUR million'), kwargs.get('period', '2025-12-31'),
        expected_entity=kwargs.get('entity', 'Synthetic Industrial Issuer'))


def test_recompiled_balance_leaf_sum_is_accepted_without_changing_source_or_plan():
    item, docs = bridge_case()
    prior = deepcopy((item, docs))
    assert prove(item, docs) is None
    assert (item, docs) == prior
    item['value'] *= -1
    for term in item['calculation']['terms']:
        term['coefficient'] *= -1
    assert prove(item, docs, driver='equity_adjustments') is None


@pytest.mark.parametrize('change', [
    'amount', 'quote', 'unit', 'period', 'entity', 'duplicate', 'sign',
    'missing_primary', 'ledger_tamper', 'cross_pointer', 'unsupported_driver', 'extra_proof',
])
def test_balance_bridge_rejects_unproved_or_conflicting_components(change):
    item, docs = bridge_case()
    kwargs = {}
    term = item['calculation']['terms'][0]
    if change == 'amount': item['value'] += 1
    elif change == 'quote': term['quoted_value'] += 1
    elif change == 'unit': kwargs['unit'] = 'USD million'
    elif change == 'period': kwargs['period'] = '2026-01-01'
    elif change == 'entity': kwargs['entity'] = 'Other Issuer'
    elif change == 'duplicate': item['calculation']['terms'].append(deepcopy(term))
    elif change == 'sign': term['coefficient'] = -1
    elif change == 'missing_primary': docs.pop(0)
    elif change == 'ledger_tamper': docs[1]['text'] += ' '
    elif change == 'cross_pointer': term['evidence_pointer']['unit'] = '/components/0/unit'
    elif change == 'unsupported_driver': kwargs['driver'] = 'historical_revenue'
    elif change == 'extra_proof': item['quoted_value'] = item['value']
    assert prove(item, docs, **kwargs) is not None
