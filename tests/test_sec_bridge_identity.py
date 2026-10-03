"""SEC opening bridges retain the same accession-bound identity proof as revenue."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.input_preparation import _fact_proof
from test_preparation_fresh_historical import sec_issuer_punctuation


def _case():
    primary, doc = sec_issuer_punctuation()
    body = json.loads(doc['text'])
    first = body['facts'][0]
    first['concept'] = 'LongTermDebtNoncurrent'
    first['observation'].pop('start')
    second = deepcopy(first)
    second['concept'] = 'CashAndCashEquivalentsAtCarryingValue'
    second['observation']['val'] = 25_000_000
    body['facts'].append(second)
    doc['text'] = json.dumps(body)
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    terms = [{'coefficient': sign, 'evidence_ids': [doc['id']],
              'quoted_value': fact['observation']['val'], 'quoted_unit': 'USD',
              'evidence_pointer': {'value': f'/facts/{i}/observation/val',
                                   'unit': f'/facts/{i}/unit', 'period': f'/facts/{i}/observation/end'}}
             for i, (fact, sign) in enumerate(zip(body['facts'], (1, -1)))]
    item = {'value': 75., 'evidence_ids': [doc['id'], primary['id']],
            'calculation': {'operation': 'sum', 'terms': terms}}
    return item, [primary, doc]


def _prove(item, docs, driver='net_debt'):
    return _fact_proof(driver, item, docs, 'USD million', '2025-12-31',
                       expected_entity='SYNTHETIC INDUSTRIAL ISSUER INC')


@pytest.mark.parametrize('driver', ('net_debt', 'equity_adjustments'))
def test_opening_sum_accepts_bound_inc_punctuation_only(driver):
    item, docs = _case()
    original = deepcopy((item, docs))
    assert _prove(item, docs, driver) is None
    assert (item, docs) == original


@pytest.mark.parametrize('fault', ('missing_primary', 'cik', 'accession', 'issuer', 'hash',
                                    'form', 'filed', 'duration', 'period', 'report_date', 'other_driver'))
def test_bridge_identity_does_not_relax_source_or_period_proofs(fault):
    item, docs = _case()
    doc = docs[-1]
    body = json.loads(doc['text'])
    observation = body['facts'][0]['observation']
    if fault == 'missing_primary': docs.pop(0)
    elif fault == 'cik': body['cik'] = '0000000002'
    elif fault == 'accession': observation['accn'] = '0000000001-26-000002'
    elif fault == 'issuer': body['issuer'] = 'Distinct Industrial Issuer, Inc.'
    elif fault == 'hash': docs[0]['document_sha256'] = 'f' * 64
    elif fault == 'form': observation['form'] = '10-Q'
    elif fault == 'filed': observation['filed'] = '2026-02-14'
    elif fault == 'duration': observation['start'] = '2025-01-01'
    elif fault == 'period': observation['end'] = '2025-11-30'
    elif fault == 'report_date': docs[0]['metadata']['report_date'] = '2025-11-30'
    doc['text'] = json.dumps(body)
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    assert _prove(item, docs, 'other_driver' if fault == 'other_driver' else 'net_debt')


@pytest.mark.parametrize('fault', ('cik', 'accession', 'hash', 'report_date', 'duration', 'missing_primary'))
def test_exact_display_name_cannot_bypass_bridge_accession_proof(fault):
    item, docs = _case()
    doc = docs[-1]
    body = json.loads(doc['text'])
    body['issuer'] = 'SYNTHETIC INDUSTRIAL ISSUER INC'
    if fault == 'cik': body['cik'] = '0000000002'
    elif fault == 'accession': body['facts'][0]['observation']['accn'] = '0000000001-26-000002'
    elif fault == 'hash': docs[0]['document_sha256'] = 'f' * 64
    elif fault == 'report_date': docs[0]['metadata']['report_date'] = '2025-11-30'
    elif fault == 'duration': body['facts'][0]['observation']['start'] = '2025-01-01'
    elif fault == 'missing_primary': docs.pop(0)
    doc['text'] = json.dumps(body)
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    assert _prove(item, docs)
