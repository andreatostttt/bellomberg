"""Only a raw SEC CIK and Nasdaq listing can bind Corp to Corporation."""
from copy import deepcopy
from hashlib import sha256

import pytest


OPENING = '2026-07-26'
TICKER = 'ATLS'
SEC_NAME = 'Atlas Manufacturing CORP'
PROFILE_NAME = 'Atlas Manufacturing Corporation'


def _documents(tmp_path, *, on=OPENING):
    from bellomberg.valuation.quotation_evidence import listing_identity_document

    raw = (f'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="issuer">'
           f'{SEC_NAME}</ix:nonNumeric>'
           f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="issuer">'
           f'0000000123</ix:nonNumeric>'
           f'<ix:nonNumeric name="dei:Security12bTitle" contextRef="stock">'
           f'Common Stock</ix:nonNumeric>'
           f'<ix:nonNumeric name="dei:TradingSymbol" contextRef="stock">'
           f'{TICKER}</ix:nonNumeric>'
           f'<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="stock">'
           f'The Nasdaq Global Select Market</ix:nonNumeric>').encode()
    digest = sha256(raw).hexdigest()
    path = tmp_path / 'primary.html'
    path.write_bytes(raw)
    primary = {'id': digest, 'document_sha256': digest, 'archive_path': str(path),
               'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
               'text': raw.decode(), 'published_at': '2026-08-01',
               'metadata': {'issuer': SEC_NAME, 'report_date': on, 'form': '10-Q',
                            'emittente_id': 'CIK:0000000123', 'accession': '0000000123-26-000001'}}
    listing = listing_identity_document(primary, raw, ticker=TICKER, on=on)['documents'][0]
    profile = {'symbol': TICKER, 'longName': PROFILE_NAME,
               'currency': 'USD', 'financialCurrency': 'USD',
               'fullExchangeName': 'NasdaqGS'}
    return listing, primary, profile, path


def test_sec_corporation_suffix_binding_preserves_both_names_and_raw_cik(tmp_path):
    from bellomberg.valuation.quotation_evidence import verify_sec_corporation_name_binding

    listing, primary, profile, _ = _documents(tmp_path)
    receipt = verify_sec_corporation_name_binding(
        listing, primary, profile, ticker=TICKER, on=OPENING)
    assert receipt['policy'] == 'sec_nasdaq_corporation_suffix/1'
    assert receipt['sec_issuer'] == SEC_NAME
    assert receipt['profile_name'] == PROFILE_NAME
    assert receipt['cik'] == '0000000123'
    assert receipt['listing_document_id'] == listing['id']
    assert receipt['primary_document_id'] == primary['id']
    assert receipt['opening_date'] == OPENING
    assert receipt['quote_currency'] == 'USD'


def test_sec_corporation_name_binding_is_not_a_weekend_only_exception(tmp_path):
    from bellomberg.valuation.quotation_evidence import verify_sec_corporation_name_binding

    weekday = '2026-07-22'
    listing, primary, profile, _ = _documents(tmp_path, on=weekday)
    profile.pop('financialCurrency')  # Public quote currency still explicit.
    receipt = verify_sec_corporation_name_binding(
        listing, primary, profile, ticker=TICKER, on=weekday)
    assert receipt['opening_date'] == weekday


@pytest.mark.parametrize('fault', [
    'other_stem', 'other_suffix', 'other_infix', 'profile_symbol', 'profile_exchange',
    'profile_quote_currency', 'profile_financial_currency', 'raw_cik',
    'metadata_cik', 'url_cik', 'listing_ticker', 'listing_exchange', 'raw_tamper',
])
def test_sec_corporation_suffix_requires_same_raw_issuer_cik_listing_and_profile(tmp_path, fault):
    from bellomberg.valuation.quotation_evidence import verify_sec_corporation_name_binding

    listing, primary, profile, path = _documents(tmp_path)
    listing, primary, profile = deepcopy(listing), deepcopy(primary), deepcopy(profile)
    if fault == 'other_stem':
        profile['longName'] = 'Atlas Holdings Corporation'
    elif fault == 'other_suffix':
        profile['longName'] = 'Atlas Manufacturing LLC'
    elif fault == 'other_infix':
        profile['longName'] = 'Atlas-Manufacturing Corporation'
    elif fault == 'profile_symbol':
        profile['symbol'] = 'OTHER'
    elif fault == 'profile_exchange':
        profile['fullExchangeName'] = 'NasdaqGM'
    elif fault == 'profile_quote_currency':
        profile['currency'] = 'EUR'
    elif fault == 'profile_financial_currency':
        profile['financialCurrency'] = 'EUR'
    elif fault == 'raw_cik':
        from bellomberg.valuation.quotation_evidence import listing_identity_document

        old = path.read_bytes()
        changed = old.replace(b'0000000123</ix:nonNumeric>', b'0000000456</ix:nonNumeric>')
        path.write_bytes(changed)
        digest = sha256(changed).hexdigest()
        primary['document_sha256'] = primary['id'] = digest
        primary['text'] = changed.decode()
        listing = listing_identity_document(primary, changed, ticker=TICKER, on=OPENING)['documents'][0]
    elif fault == 'metadata_cik':
        primary['metadata']['emittente_id'] = 'CIK:0000000456'
    elif fault == 'url_cik':
        primary['url'] = primary['url'].replace('/data/123/', '/data/456/')
    elif fault == 'listing_ticker':
        listing['metadata']['ticker'] = 'OTHER'
    elif fault == 'listing_exchange':
        from bellomberg.valuation.quotation_evidence import listing_identity_document

        changed = path.read_bytes().replace(b'The Nasdaq Global Select Market', b'The Nasdaq Global Market')
        path.write_bytes(changed)
        digest = sha256(changed).hexdigest()
        primary['document_sha256'] = primary['id'] = digest
        primary['text'] = changed.decode()
        listing = listing_identity_document(primary, changed, ticker=TICKER, on=OPENING)['documents'][0]
    elif fault == 'raw_tamper':
        path.write_bytes(b'changed after verification')
    with pytest.raises(ValueError):
        verify_sec_corporation_name_binding(
            listing, primary, profile, ticker=TICKER, on=OPENING)
