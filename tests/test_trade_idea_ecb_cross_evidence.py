"""Synthetic primary CSVs test cross-rate proof boundaries, not a real quote."""
from copy import deepcopy
from datetime import date, datetime, timezone
from fractions import Fraction
from hashlib import sha256
import json

import pytest

from bellomberg.valuation import fx_evidence

ON = '2026-06-30'
AS_OF = '2026-09-28'


def primary(currency, value, *, retrieved='2026-09-28T07:00:00+00:00'):
    raw = ('KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT,UNIT_MULT\n'
           f'EXR.D.{currency}.EUR.SP00.A,D,{currency},EUR,SP00,A,{ON},{value},A,{currency},0\n')
    digest = sha256(raw.encode()).hexdigest()
    url = fx_evidence.reference_url(currency, 'EUR', ON)
    return {'id': digest, 'url': url, 'text': raw, 'sha256': digest, 'document_sha256': digest,
            'published_at': None, 'availability_basis': 'observed_download',
            'retrieval': {'url': url, 'document_sha256': digest, 'retrieved_at': retrieved}}


def cross(first=None, second=None, *, financial='USD', quote='GBP'):
    return fx_evidence.normalize_cross_fx(first or primary('USD', '1.25'), second or primary('GBP', '1.00'),
        financial_currency=financial, quote_currency=quote, on=ON, as_of=AS_OF)


def test_two_real_leg_identities_exact_division_and_current_availability():
    # These source bytes are synthetic; the test asserts the generic contract.
    first = primary('USD', '1.25', retrieved='2026-09-27T23:55:00+00:00')
    second = primary('GBP', '1.00')
    doc = cross(first, second)
    body = json.loads(doc['text'])
    assert body['facts'] == [{'value': .8, 'unit': 'GBP per USD', 'end': ON}]
    assert body['calculation']['operation'] == 'divide_quote_eur_by_financial_eur'
    ratio = Fraction(int(body['calculation']['exact_numerator']), int(body['calculation']['exact_denominator']))
    assert ratio == Fraction(4, 5)
    assert body['source_document_ids'] == {'financial': first['id'], 'quote': second['id']}
    assert body['observations']['financial']['OBS_VALUE'] == '1.25'
    assert body['observations']['quote']['OBS_VALUE'] == '1.00'
    assert doc['available_at'] == AS_OF and doc['published_at'] is None
    assert doc['url'] == second['url'] and doc['document_sha256'] == second['id']
    assert doc['retrieval'] == second['retrieval']
    assert 'reference' in body['limitation'].lower() and 'closing' in body['limitation'].lower()


def test_orientation_is_explicit_and_inverse_not_rounded():
    forward = cross()
    inverse = cross(primary('GBP', '1.00'), primary('USD', '1.25'), financial='GBP', quote='USD')
    assert json.loads(forward['text'])['facts'][0]['value'] == .8
    assert json.loads(inverse['text'])['facts'][0]['value'] == 1.25
    assert inverse['id'] != forward['id']


@pytest.mark.parametrize('fault', ['other_day', 'other_currency', 'wrong_unit', 'missing', 'zero', 'hash',
    'receipt', 'late', 'wrong_url', 'duplicate', 'published'])
def test_each_primary_leg_must_independently_prove_date_units_bytes_and_availability(fault):
    first, second = primary('USD', '1.25'), primary('GBP', '1.00')
    if fault in {'other_day', 'other_currency', 'wrong_unit', 'missing', 'zero', 'duplicate'}:
        replacements = {'other_day': (ON, '2026-06-29'), 'other_currency': ('GBP', 'CHF'),
            'wrong_unit': (',A,GBP,0', ',A,GBP,3'), 'missing': (',1.00,', ',,'), 'zero': (',1.00,', ',0,'),
            'duplicate': (second['text'], second['text'] + second['text'].splitlines()[1] + '\n')}
        second['text'] = second['text'].replace(*replacements[fault])
        digest = sha256(second['text'].encode()).hexdigest()
        second.update(id=digest, sha256=digest, document_sha256=digest)
        second['retrieval']['document_sha256'] = digest
    elif fault == 'hash':
        second['document_sha256'] = '0' * 64
    elif fault == 'receipt':
        del second['retrieval']
    elif fault == 'late':
        second['retrieval']['retrieved_at'] = '2026-09-29T00:00:00+00:00'
    elif fault == 'wrong_url':
        second['url'] = first['url']
    elif fault == 'published':
        second['published_at'] = ON
    with pytest.raises((ValueError, KeyError)):
        cross(first, second)


@pytest.mark.parametrize('financial,quote', [('EUR', 'GBP'), ('USD', 'EUR'), ('USD', 'USD'), ('GBX', 'USD'), (None, 'GBP')])
def test_cross_contract_has_two_distinct_non_eur_iso_currencies(financial, quote):
    with pytest.raises((ValueError, KeyError)):
        cross(financial=financial, quote=quote)


def test_cross_collector_requests_exact_official_legs_and_preserves_failures(tmp_path):
    calls = []
    def download(url, dest, **kwargs):
        currency = 'USD' if '.USD.' in url else 'GBP'
        source = primary(currency, '1.25' if currency == 'USD' else '1.00')
        path = tmp_path / (currency + '.csv')
        path.write_bytes(source['text'].encode())
        calls.append((url, dest, kwargs))
        return {'stato': 'ok', 'url_finale': url, 'path': str(path), 'sha256': source['id']}
    result = fx_evidence.collect_cross_fx_evidence(financial_currency='USD', quote_currency='GBP', on=ON, as_of=AS_OF,
        archive_root=tmp_path, download=download, now=datetime(2026, 9, 28, 7, tzinfo=timezone.utc))
    assert result['status'] == 'ready' and len(result['documents']) == 3
    assert [call[0] for call in calls] == [fx_evidence.reference_url('USD', 'EUR', ON), fx_evidence.reference_url('GBP', 'EUR', ON)]
    assert all(call[2] == {'host_consentiti': ['data-api.ecb.europa.eu'], 'public_only': True} for call in calls)
    assert result['documents'][-1]['metadata']['normalizer'] == fx_evidence.CROSS_NORMALIZER
    failed = fx_evidence.collect_cross_fx_evidence(financial_currency='USD', quote_currency='GBP', on=ON, as_of=AS_OF,
        archive_root=tmp_path, download=lambda *a, **k: {'stato': 'errore', 'motivo': 'primary missing'})
    assert failed['status'] == 'incomplete' and not failed['documents'] and failed['issues']


def test_cross_document_requires_both_sources_and_is_recompiled_at_catalog_boundary():
    from bellomberg.valuation.input_preparation import _catalog, _fact_proof
    first, second = primary('USD', '1.25'), primary('GBP', '1.00')
    doc = cross(first, second)
    catalog, issues, _ = _catalog([first, second, doc], date.fromisoformat(AS_OF))
    assert not issues and doc['id'] in catalog
    item = {'value': .8, 'quoted_value': .8, 'quoted_unit': 'GBP per USD', 'evidence_ids': [doc['id']],
            'evidence_pointer': {'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'}}
    assert _fact_proof('financial_to_quote_rate', item, [catalog[doc['id']]], 'GBP per USD', ON) is None
    for documents in ([first, doc], [second, doc], [doc]):
        catalog, issues, _ = _catalog(documents, date.fromisoformat(AS_OF))
        assert issues and doc['id'] not in catalog
    damaged = deepcopy(doc)
    body = json.loads(damaged['text'])
    body['facts'][0]['value'] = 123.0
    damaged['text'] = json.dumps(body)
    damaged['sha256'] = sha256(damaged['text'].encode()).hexdigest()
    catalog, issues, _ = _catalog([first, second, damaged], date.fromisoformat(AS_OF))
    assert issues and damaged['id'] not in catalog
