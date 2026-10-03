"""Synthetic primary layouts test issuer-price conversion; no real quote is mocked as observed."""
from copy import deepcopy
from hashlib import sha256
import json
import pytest
from test_trade_idea_nav_components_evidence import BODY
from bellomberg.valuation import fund_nav_quote_evidence as evidence

ON, DAY = '2026-06-30', '2026-09-28'
ENTITY, CLASS = 'Synthetic Holding Ltd.', 'Public Shares'

def primary(text, url, *, role):
    digest = sha256(text.encode()).hexdigest()
    return {'id': digest, 'document_sha256': digest, 'url': url, 'text': text, 'sha256': digest,
        'published_at': '2026-08-12', 'metadata': {'issuer': ENTITY, 'report_date': ON, 'role': role},
        'page_references': [{'pagina': 1, 'inizio': 0, 'fine': len(text), 'sha256': digest}]}

def sources():
    statement = primary(BODY + "Synthetic Holding Ltd.'s Public Shares commenced trading on the London Stock Exchange.\n",
                        'https://issuer.example.org/statement.pdf', role='statement')
    price = primary('''Synthetic Holding Ltd.
June 30, 2026
LISTING INFORMATION
ISIN: XS0000000000 Ticker SEDOL
LSE Main Market ($) SYNUSD LN ABC1235
LSE Main Market (£) SYN LN ABC1234
KEY FACTS
NAV per Share ($/£) $10.00 / £8.00
Price per Share ($/£) $7.00 / £5.60
Shares Outstanding 10
''', 'https://issuer.example.org/factsheet.pdf', role='price')
    unit = primary('GBX (for clarification, this acronym refers to GBP in pence), EUR, CHF.',
                   'https://docs.londonstockexchange.com/unit.pdf', role='unit')
    decimal = primary('In 1971, British currency moved to a decimal system with 100 pennies to the pound.',
                      'https://www.bankofengland.co.uk/museum/decimal', role='decimal')
    return statement, price, unit, decimal

def normalize(raw=None, **kwargs):
    return evidence.normalize_fund_quote(*(raw or sources()), ticker=kwargs.get('ticker', 'SYN.L'), entity=ENTITY, share_class=CLASS,
        listing_symbol='SYN', isin='XS0000000000', exchange='London Stock Exchange', on=ON, as_of=DAY,
        quote_currency=kwargs.get('quote_currency', 'GBP'), quote_unit=kwargs.get('quote_unit', 'GBX'))

def test_price_identity_and_decimal_conversion_are_explicit_source_bound_facts():
    source = sources()
    document = normalize(source)
    body = json.loads(document['text'])
    assert body['facts'] == [{'value': 560.0, 'unit': 'GBX per share', 'end': ON},
        {'value': 1, 'unit': 'shares per quote', 'end': ON}, {'value': 100, 'unit': 'GBX per GBP', 'end': ON}]
    assert body['price_observation']['value'] == 5.6
    assert body['price_observation']['unit'] == 'GBP per share'
    assert body['basis'] == 'issuer_reported_price_converted_to_quote_units'
    assert body['conversion']['operation'] == 'multiply_price_by_decimal_unit_factor'
    assert set(document['metadata']['source_document_ids'].values()) == {doc['id'] for doc in source}
    assert 'not an observed GBX close' in body['limitation']

@pytest.mark.parametrize('fault', ['price_date', 'other_symbol', 'other_isin', 'other_shares', 'other_nav',
    'hash', 'source_url', 'unit', 'decimal', 'listing_class', 'ambiguous_price'])
def test_wrong_date_security_units_class_or_pins_never_create_quote_facts(fault):
    raw = list(sources())
    index, old, new = {'price_date': (1, 'June 30, 2026', 'June 29, 2026'),
        'other_symbol': (1, 'SYN LN', 'OTHER LN'), 'other_isin': (1, 'XS0000000000', 'XS9999999999'),
        'other_shares': (1, 'Outstanding 10', 'Outstanding 11'), 'other_nav': (1, '$10.00', '$11.00'),
        'unit': (2, 'GBP in pence', 'USD in cents'), 'decimal': (3, '100 pennies', '10 pennies'),
        'listing_class': (0, 'Public Shares commenced', 'Private Shares commenced'),
        'ambiguous_price': (1, 'Price per Share ($/£) $7.00 / £5.60', 'Price per Share ($/£) $7.00 / £5.60\nPrice per Share ($/£) $7.00 / £5.60')}.get(fault, (1, '', ''))
    if fault == 'hash': raw[1]['sha256'] = '0'*64
    elif fault == 'source_url': raw[2]['url'] = 'https://attacker.example.org/unit.pdf'
    else:
        raw[index] = primary(raw[index]['text'].replace(old, new), raw[index]['url'], role=str(index))
    with pytest.raises(ValueError): normalize(raw)

def test_provider_ticker_must_bind_exact_original_listing_symbol():
    with pytest.raises(ValueError, match='no proxy ticker'):
        normalize(ticker='OTHER.L')

def test_quote_catalog_recompiles_all_originals_and_normalized_numeric_facts():
    from bellomberg.valuation.input_preparation import _catalog, _day
    raw = list(sources())
    raw[3]['page_references'] = None  # HTML definitions have no PDF page map.
    document = normalize(raw)
    catalog, issues, _ = _catalog(raw+[document], _day(DAY))
    assert not issues
    assert document['id'] in catalog
    for dependency in raw:
        _catalog_result, missing, _ = _catalog([row for row in raw if row['id'] != dependency['id']]+[document], _day(DAY))
        assert missing, 'one missing original definition or observation must block normalization'
    altered = deepcopy(document)
    body = json.loads(altered['text'])
    body['facts'][0]['value'] += 1
    altered['text'] = json.dumps(body, sort_keys=True)
    altered['sha256'] = sha256(altered['text'].encode()).hexdigest()
    _catalog_result, forged, _ = _catalog(raw+[altered], _day(DAY))
    assert forged, 'resealed model-side quote must not replace the original source price'

def test_quote_proof_binds_the_financial_entity_class_currency_unit_and_ticker():
    from bellomberg.valuation.input_preparation import _catalog, _day, _quotation_proof
    from test_trade_idea_ecb_cross_evidence import primary as fx_primary
    from bellomberg.valuation.fx_evidence import normalize_cross_fx
    raw, document = list(sources()), normalize()
    legs = [fx_primary('USD', '1.25'), fx_primary('GBP', '1.00')]
    fx = normalize_cross_fx(*legs, financial_currency='USD', quote_currency='GBP', on=ON, as_of=DAY)
    catalog, issues, _ = _catalog(raw+legs+[document, fx], _day(DAY))
    assert not issues
    source = catalog[document['id']]
    body = json.loads(source['text'])
    def fact(index, doc=source):
        observation = json.loads(doc['text'])['facts'][index]
        return {'evidence_ids': [doc['id']], 'quoted_value': observation['value'], 'quoted_unit': observation['unit'],
            'evidence_pointer': {'value': f'/facts/{index}/value', 'unit': f'/facts/{index}/unit', 'period': f'/facts/{index}/end'}}
    # USD/GBP FX is independently required. Omitting it is always a blocker.
    value = {'price': 560., 'price_as_of': ON, 'share_class': CLASS, 'financial_currency': 'USD',
        'quote_currency': 'GBP', 'quote_unit': 'GBX', 'shares_per_quote': 1., 'quote_units_per_currency': 100.,
        'financial_to_quote_rate': .8}
    item = {'value': value, 'facts': {'price': fact(0), 'shares_per_quote': fact(1), 'quote_units_per_currency': fact(2)}}
    # A supported derived quote does not remove its independent FX obligation.
    assert _quotation_proof(item, [source], expected_entity=ENTITY, expected_ticker='SYN.L')
    fx_source = catalog[fx['id']]
    item['facts']['financial_to_quote_rate'] = fact(0, fx_source)
    assert _quotation_proof(item, [source, fx_source], expected_entity=ENTITY, expected_ticker='SYN.L') is None
    for key, wrong in [('share_class', 'Private Shares'), ('financial_currency', 'EUR'),
                       ('quote_currency', 'USD'), ('quote_unit', 'GBP'), ('price_as_of', '2026-06-29')]:
        altered = deepcopy(item)
        altered['value'][key] = wrong
        assert _quotation_proof(altered, [source, fx_source], expected_entity=ENTITY, expected_ticker='SYN.L'), key
    assert _quotation_proof(item, [source, fx_source], expected_entity='Other Holding Ltd.', expected_ticker='SYN.L')
    assert _quotation_proof(item, [source, fx_source], expected_entity=ENTITY, expected_ticker='OTHER.L')
