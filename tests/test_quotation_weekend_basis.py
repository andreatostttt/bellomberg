"""A weekend accounting close may cite only the proved prior Nasdaq session."""
from copy import deepcopy
from hashlib import sha256

import pytest


OPENING = '2026-07-26'  # Sunday; the accounting date never moves.
FRIDAY = '2026-07-24'


def _sec_listing(tmp_path, *, ticker='SYNX', exchange='The Nasdaq Global Select Market'):
    from bellomberg.valuation.quotation_evidence import listing_identity_document

    raw = (f'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="issuer">'
           f'Synthetic Issuer Inc</ix:nonNumeric>'
           f'<ix:nonNumeric name="dei:Security12bTitle" contextRef="stock">'
           f'Common Stock</ix:nonNumeric>'
           f'<ix:nonNumeric name="dei:TradingSymbol" contextRef="stock">'
           f'{ticker}</ix:nonNumeric>'
           f'<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="stock">'
           f'{exchange}</ix:nonNumeric>').encode()
    digest = sha256(raw).hexdigest()
    path = tmp_path / 'primary.html'
    path.write_bytes(raw)
    primary = {'id': digest, 'document_sha256': digest, 'archive_path': str(path),
               'text': raw.decode(), 'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
               'published_at': '2026-08-01', 'metadata': {'issuer': 'Synthetic Issuer Inc',
               'report_date': OPENING, 'form': '10-Q', 'emittente_id': 'CIK:0000000123',
               'accession': '0000000123-26-000001'}}
    listing = listing_identity_document(primary, raw, ticker=ticker, on=OPENING)['documents'][0]
    return primary, listing


def _quote(basis):
    return {'financial_currency': 'USD', 'quote_currency': 'USD', 'quote_unit': 'USD',
            'quote_units_per_currency': 1., 'financial_to_quote_rate': 1.,
            'shares_per_quote': 1., 'share_class': 'Common Stock', 'price': 100.,
            'price_as_of': FRIDAY, 'price_date_basis': basis}


def test_weekend_quote_preserves_real_price_day_and_binds_sec_nasdaq_listing(tmp_path):
    from bellomberg.valuation.quotation_evidence import (
        historical_quote_document, nasdaq_weekend_quote_basis,
        verify_nasdaq_weekend_quote_basis, validate_quote_date_basis)

    primary, listing = _sec_listing(tmp_path)
    observed_day, basis = nasdaq_weekend_quote_basis(
        listing, primary, ticker='SYNX', opening_date=OPENING)
    assert observed_day == FRIDAY
    assert basis == {'policy': 'nasdaq_weekend_previous_friday/1',
                     'accounting_date': OPENING, 'exchange': 'The Nasdaq Global Select Market',
                     'listing_document_id': listing['id']}
    seen = []
    def fetch(ticker, day):
        seen.append((ticker, day))
        return {'symbol': ticker, 'date': day, 'currency': 'USD', 'close': 100.}
    price = historical_quote_document('SYNX', on=observed_day, as_of='2026-09-29', fetch=fetch)
    assert price['status'] == 'ready' and seen == [('SYNX', FRIDAY)]
    assert price['documents'][0]['metadata']['price_as_of'] == FRIDAY
    assert validate_quote_date_basis(_quote(basis), OPENING) is None
    assert verify_nasdaq_weekend_quote_basis(
        _quote(basis), listing, primary, ticker='SYNX', opening_date=OPENING) == FRIDAY


@pytest.mark.parametrize('fault', ['wrong_exchange', 'wrong_issuer', 'changed_bytes',
                                    'weekday_opening', 'other_ticker'])
def test_weekend_basis_needs_exact_raw_sec_nasdaq_identity(tmp_path, fault):
    from bellomberg.valuation.quotation_evidence import nasdaq_weekend_quote_basis

    primary, listing = _sec_listing(tmp_path, exchange='The New York Stock Exchange'
                                    if fault == 'wrong_exchange' else 'The Nasdaq Global Select Market')
    if fault == 'wrong_issuer':
        primary['metadata']['issuer'] = 'Another Issuer Inc'
    elif fault == 'changed_bytes':
        (tmp_path / 'primary.html').write_bytes(b'changed')
    with pytest.raises(ValueError):
        nasdaq_weekend_quote_basis(listing, primary,
            ticker='OTHER' if fault == 'other_ticker' else 'SYNX',
            opening_date='2026-07-27' if fault == 'weekday_opening' else OPENING)


@pytest.mark.parametrize('fault', ['no_basis', 'wrong_policy', 'fake_opening',
                                    'wrong_price_date', 'wrong_exchange', 'wrong_listing_id',
                                    'cross_currency', 'ratio', 'extra_field'])
def test_weekend_date_contract_rejects_implicit_or_forged_carry(tmp_path, fault):
    from bellomberg.valuation.quotation_evidence import (
        nasdaq_weekend_quote_basis, validate_quote_date_basis,
        verify_nasdaq_weekend_quote_basis)

    primary, listing = _sec_listing(tmp_path)
    _, basis = nasdaq_weekend_quote_basis(listing, primary, ticker='SYNX', opening_date=OPENING)
    value = _quote(deepcopy(basis))
    if fault == 'no_basis':
        value.pop('price_date_basis')
    elif fault == 'wrong_policy':
        value['price_date_basis']['policy'] = 'previous_weekday'
    elif fault == 'fake_opening':
        value['price_date_basis']['accounting_date'] = '2026-07-25'
    elif fault == 'wrong_price_date':
        value['price_as_of'] = '2026-07-23'
    elif fault == 'wrong_exchange':
        value['price_date_basis']['exchange'] = 'The New York Stock Exchange'
    elif fault == 'wrong_listing_id':
        value['price_date_basis']['listing_document_id'] = 'listing-' + '0' * 64
    elif fault == 'cross_currency':
        value['financial_currency'] = 'EUR'
    elif fault == 'ratio':
        value['shares_per_quote'] = 2.
    else:
        value['price_date_basis']['unconsumed'] = True
    with pytest.raises(ValueError):
        verify_nasdaq_weekend_quote_basis(value, listing, primary,
                                          ticker='SYNX', opening_date=OPENING)
    if fault != 'wrong_listing_id':
        with pytest.raises(ValueError):
            validate_quote_date_basis(value, OPENING)


def test_weekday_legacy_contract_remains_exact_and_friday_missing_is_not_backfilled(tmp_path):
    from bellomberg.valuation.quotation_evidence import (
        historical_quote_document, validate_quote_date_basis)

    quote = _quote({})
    quote.pop('price_date_basis')
    quote['price_as_of'] = OPENING
    assert validate_quote_date_basis(quote, OPENING) is None
    quote['price_as_of'] = FRIDAY
    with pytest.raises(ValueError):
        validate_quote_date_basis(quote, OPENING)
    missing = historical_quote_document('SYNX', on=FRIDAY, as_of='2026-09-29',
        fetch=lambda *_: {'symbol': 'SYNX', 'date': '2026-07-23',
                          'currency': 'USD', 'close': 100.})
    assert missing['status'] == 'incomplete' and missing['documents'] == []


def test_common_operating_binder_keeps_sunday_model_date_and_friday_observation(tmp_path):
    from bellomberg.valuation.documented_inputs import bind_inputs
    from bellomberg.valuation.operating_adapter import SCHEMA
    from bellomberg.valuation.quotation_evidence import nasdaq_weekend_quote_basis
    from test_sector_operating_drivers import DAY, SPAN, operating_records

    primary, listing = _sec_listing(tmp_path)
    _, basis = nasdaq_weekend_quote_basis(listing, primary, ticker='SYNX', opening_date=OPENING)
    rows = operating_records()
    periods = [{'start': '2026-07-27', 'end': '2027-07-26'},
               {'start': '2027-07-27', 'end': '2028-07-26'}]
    span = '|'.join(p['start'] + '/' + p['end'] for p in periods)
    for row in rows:
        if row['unit'] == 'EUR million':
            row['unit'] = 'USD million'
        if row['period'] == SPAN:
            row['period'] = span
        elif row['period'] == '2025-12-31':
            row['period'] = OPENING
        if row['driver'] == 'calendar':
            row['value'] = {'valuation_date': OPENING, 'periods': periods,
                            'discount_convention': 'annual_end'}
        if row['driver'] == 'quotation':
            row['value'] = _quote(basis)
            row['value']['share_class'] = 'ordinary'
        if row['driver'] == 'perimeter':
            row['value']['currency'] = 'USD'
    bundle = {'case': {'records': rows, 'as_of': DAY, 'assumptions': {}},
              'analysis_context': {'scenario_rationale': {
                  name: 'Explicit synthetic scenario' for name in ('bear', 'base', 'bull')}},
              'decision': {'method_id': 'operating_fcff'}}
    bound = bind_inputs(bundle, SCHEMA)
    assert not bound['issues'], bound['issues']
    assert bound['calendar']['valuation_date'] == OPENING
    assert bound['quotation']['price_as_of'] == FRIDAY
    assert bound['values']['model']['quotation']['price_date_basis'] == basis
