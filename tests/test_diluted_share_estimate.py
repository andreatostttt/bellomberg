"""Explicit analyst dilution proxy; primary SEC facts remain separate observations."""
from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json

import pytest

def _helper():
    name = 'bellomberg.valuation.diluted_share_estimate'
    assert importlib.util.find_spec(name) is not None, 'dilution proof helper is missing'
    return importlib.import_module(name)


CIK = '0000000999'
ACCESSION = '0000000999-25-000007'
OPENING = '2025-09-30'


def _source():
    filing_text = ('<html><ix:nonNumeric name="dei:EntityRegistrantName">SYNTHETIC INC'
                   '</ix:nonNumeric><ix:nonNumeric name="dei:Security12bTitle" contextRef="cover">'
                   'Common Stock</ix:nonNumeric><ix:nonNumeric name="dei:TradingSymbol" '
                   'contextRef="cover">SYN</ix:nonNumeric><ix:nonNumeric '
                   'name="dei:SecurityExchangeName" contextRef="cover">Nasdaq Stock Market'
                   '</ix:nonNumeric></html>')
    filing_id = sha256(filing_text.encode()).hexdigest()
    filing = {'id': filing_id, 'text': filing_text,
        'sha256': sha256(filing_text.encode()).hexdigest(), 'document_sha256': filing_id,
        'url': 'https://www.sec.gov/Archives/edgar/data/999/000000099925000007/interim.htm',
        'published_at': '2025-10-15', 'metadata': {'emittente_id': 'CIK:' + CIK,
            'issuer': 'SYNTHETIC INC', 'form': '10-Q', 'report_date': OPENING,
            'accession': ACCESSION}}

    def fact(concept, value, start=None):
        observation = {'end': OPENING, 'val': value, 'accn': ACCESSION,
                       'form': '10-Q', 'filed': filing['published_at']}
        if start:
            observation['start'] = start
        return {'taxonomy': 'us-gaap', 'concept': concept, 'unit': 'shares',
                'observation': observation}

    body = {'cik': CIK, 'issuer': 'Synthetic Inc.', 'facts': [
        fact('CommonStockSharesOutstanding', 100_000_000),
        fact('WeightedAverageNumberOfSharesOutstandingBasic', 99_000_000, '2025-07-01'),
        fact('WeightedAverageNumberOfDilutedSharesOutstanding', 101_000_000, '2025-07-01'),
        fact('WeightedAverageNumberOfSharesOutstandingBasic', 98_000_000, '2025-01-01'),
        fact('WeightedAverageNumberOfDilutedSharesOutstanding', 100_000_000, '2025-01-01')]}
    text = json.dumps(body, sort_keys=True)
    xbrl = {'id': 'xbrl-' + CIK + '-' + ACCESSION.replace('-', ''), 'text': text,
        'sha256': sha256(text.encode()).hexdigest(), 'document_sha256': sha256(text.encode()).hexdigest(),
        'url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK' + CIK + '.json',
        'published_at': filing['published_at'], 'metadata': {'emittente_id': 'CIK:' + CIK,
            'accession': ACCESSION.replace('-', '')}}
    return filing, xbrl


def _case():
    filing, xbrl = _source()
    from bellomberg.valuation.quotation_evidence import listing_identity_document
    listing = listing_identity_document(filing, filing['text'].encode('utf-8'),
                                        ticker='SYN', on=OPENING)['documents'][0]

    def fact(index, raw):
        root = '/facts/' + str(index)
        return {'value': raw / 1_000_000, 'evidence_ids': [xbrl['id']],
                'quoted_value': raw, 'quoted_unit': 'shares',
                'evidence_pointer': {'value': root + '/observation/val',
                    'unit': root + '/unit', 'period': root + '/observation/end'}}

    item = {'value': 100 * 101 / 99, 'kind': 'analyst_estimate',
        'evidence_ids': [xbrl['id']], 'rationale': 'Quarter award mix assumed applicable at opening.',
        'valid_until': '2025-10-16', 'valid_until_basis': {'policy': 'same_day', 'as_of': '2025-10-16'},
        'dilution_estimate': {'method': 'reported_dilution_ratio_proxy', 'facts': {
            'outstanding': fact(0, 100_000_000), 'weighted_basic': fact(1, 99_000_000),
            'weighted_diluted': fact(2, 101_000_000)},
            'applicability': 'Recent quarter award mix is assumed representative at opening.',
            'limitation': 'Period-average dilution is not an instant diluted count; '
                          'rounding, future awards and excluded instruments may differ.'}}
    return item, {filing['id']: filing, xbrl['id']: xbrl, listing['id']: listing}, [filing]


def _prove(item, catalog, filings, *, share_class='Common Stock'):
    return _helper().prove_diluted_share_estimate(item, catalog, entity='SYNTHETIC INC',
        opening_date=OPENING, sec_filings=filings, share_class=share_class)


def _mutate_raw(catalog, mutator, *, update_hash=True):
    xbrl = next(doc for doc in catalog.values() if doc['id'].startswith('xbrl-'))
    body = json.loads(xbrl['text'])
    mutator(body)
    xbrl['text'] = json.dumps(body, sort_keys=True)
    if update_hash:
        xbrl['sha256'] = sha256(xbrl['text'].encode()).hexdigest()


def test_proxy_reproves_shortest_same_filing_period_and_discloses_estimate():
    item, catalog, filings = _case()
    original = deepcopy((item, catalog, filings))
    assert _prove(item, catalog, filings) is None
    disclosure = _helper().diluted_share_disclosure(item)
    assert disclosure.startswith('PROXY/STIMA DILUZIONE:')
    for text in ('100', '101', '99', 'period', 'round', 'award'):
        assert text.lower() in disclosure.lower()
    assert _helper().diluted_share_policy()['method'] == 'reported_dilution_ratio_proxy'
    assert (item, catalog, filings) == original


@pytest.mark.parametrize('fault', [
    'wrong_kind', 'missing_applicability', 'missing_limitation', 'wrong_method',
    'value_mismatch', 'term_mismatch', 'zero_basic', 'not_diluted',
    'longer_period', 'date_mismatch', 'instant_with_start', 'unit_mismatch',
    'concept_mismatch', 'issuer_mismatch', 'cik_mismatch', 'accession_mismatch',
    'filing_mismatch', 'altered_text_hash', 'wrong_pointer', 'missing_source',
    'bool_quoted', 'too_long_duration', 'wrong_share_class', 'missing_listing',
    'other_common_class', 'class_axis',
])
def test_proxy_rejects_unproved_or_mislabeled_claims(fault):
    item, catalog, filings = _case()
    facts = item['dilution_estimate']['facts']
    if fault == 'wrong_kind':
        item['kind'] = 'historical'
    elif fault == 'missing_applicability':
        item['dilution_estimate']['applicability'] = ''
    elif fault == 'missing_limitation':
        item['dilution_estimate']['limitation'] = ''
    elif fault == 'wrong_method':
        item['dilution_estimate']['method'] = 'instant_diluted'
    elif fault == 'value_mismatch':
        item['value'] += .1
    elif fault == 'term_mismatch':
        facts['weighted_basic']['value'] += .1
    elif fault == 'zero_basic':
        _mutate_raw(catalog, lambda body: body['facts'][1]['observation'].update(val=0))
        facts['weighted_basic'].update(value=0, quoted_value=0)
    elif fault == 'not_diluted':
        _mutate_raw(catalog, lambda body: body['facts'][2]['observation'].update(val=99_000_000))
        facts['weighted_diluted'].update(value=99, quoted_value=99_000_000)
    elif fault == 'longer_period':
        facts['weighted_basic'] = {**facts['weighted_basic'],
            'value': 98, 'quoted_value': 98_000_000,
            'evidence_pointer': {**facts['weighted_basic']['evidence_pointer'],
                'value': '/facts/3/observation/val', 'unit': '/facts/3/unit',
                'period': '/facts/3/observation/end'}}
        facts['weighted_diluted'] = {**facts['weighted_diluted'],
            'value': 100, 'quoted_value': 100_000_000,
            'evidence_pointer': {**facts['weighted_diluted']['evidence_pointer'],
                'value': '/facts/4/observation/val', 'unit': '/facts/4/unit',
                'period': '/facts/4/observation/end'}}
        item['value'] = 100 * 100 / 98
    elif fault == 'date_mismatch':
        _mutate_raw(catalog, lambda body: body['facts'][2]['observation'].update(start='2025-07-02'))
    elif fault == 'instant_with_start':
        _mutate_raw(catalog, lambda body: body['facts'][0]['observation'].update(start='2025-07-01'))
    elif fault == 'unit_mismatch':
        _mutate_raw(catalog, lambda body: body['facts'][2].update(unit='thousand shares'))
    elif fault == 'concept_mismatch':
        _mutate_raw(catalog, lambda body: body['facts'][2].update(concept='WeightedAverageNumberOfSharesOutstandingBasic'))
    elif fault == 'issuer_mismatch':
        _mutate_raw(catalog, lambda body: body.update(issuer='Other Inc.'))
    elif fault == 'cik_mismatch':
        _mutate_raw(catalog, lambda body: body.update(cik='0000000998'))
    elif fault == 'accession_mismatch':
        _mutate_raw(catalog, lambda body: body['facts'][1]['observation'].update(accn='0000000999-25-000008'))
    elif fault == 'filing_mismatch':
        filings[0]['metadata']['accession'] = '0000000999-25-000008'
    elif fault == 'altered_text_hash':
        _mutate_raw(catalog, lambda body: body['facts'][0]['observation'].update(val=101_000_000),
                    update_hash=False)
    elif fault == 'wrong_pointer':
        facts['weighted_basic']['evidence_pointer']['unit'] = '/facts/1/observation/val'
    elif fault == 'missing_source':
        del catalog[facts['outstanding']['evidence_ids'][0]]
    elif fault == 'bool_quoted':
        facts['weighted_basic']['quoted_value'] = True
    elif fault == 'too_long_duration':
        _mutate_raw(catalog, lambda body: [body['facts'][idx]['observation'].update(
            start='2024-01-01') for idx in (1, 2)])
    elif fault == 'wrong_share_class':
        assert _prove(item, catalog, filings, share_class='Class A Common Stock') is not None
        return
    elif fault == 'missing_listing':
        del catalog['listing-' + filings[0]['id']]
    elif fault in ('other_common_class', 'class_axis'):
        insertion = ('<ix:nonNumeric name="dei:Security12bTitle" contextRef="other">'
                     'Class B Common Stock</ix:nonNumeric>' if fault == 'other_common_class' else
                     '<xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
                     'custom:ClassBCommonStockMember</xbrldi:explicitMember>')
        filing = filings[0]
        original_id = filing['id']
        filing['text'] = filing['text'].replace('</html>', insertion + '</html>')
        filing['id'] = sha256(filing['text'].encode()).hexdigest()
        filing['sha256'] = filing['document_sha256'] = filing['id']
        del catalog[original_id]
        catalog[filing['id']] = filing
        from bellomberg.valuation.quotation_evidence import listing_identity_document
        listing = listing_identity_document(filing, filing['text'].encode(),
                                            ticker='SYN', on=OPENING)['documents'][0]
        del catalog['listing-' + original_id]
        catalog[listing['id']] = listing
    assert _prove(item, catalog, filings) is not None
