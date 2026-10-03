"""Explicit NYSE weekend dates use the same raw-SEC quote proof contract."""
from copy import deepcopy
import pytest

from test_quotation_weekend_basis import _sec_listing, _quote, OPENING, FRIDAY


def test_nyse_weekend_basis_preserves_real_date_and_reproves_listing(tmp_path):
    from bellomberg.valuation.quotation_evidence import (
        sec_us_weekend_quote_basis, verify_sec_us_weekend_quote_basis, validate_quote_date_basis,
        nasdaq_weekend_quote_basis)
    primary, listing = _sec_listing(tmp_path, exchange='New York Stock Exchange')
    day, basis = sec_us_weekend_quote_basis(listing, primary, ticker='SYNX', opening_date=OPENING)
    assert day == FRIDAY
    assert basis['policy'] == 'nyse_weekend_previous_friday/1'
    assert basis['exchange'] == 'New York Stock Exchange'
    assert validate_quote_date_basis(_quote(basis), OPENING) is None
    assert verify_sec_us_weekend_quote_basis(_quote(basis), listing, primary,
        ticker='SYNX', opening_date=OPENING) == FRIDAY
    with pytest.raises(ValueError):
        nasdaq_weekend_quote_basis(listing, primary, ticker='SYNX', opening_date=OPENING)


@pytest.mark.parametrize('fault', ['wrong_policy', 'wrong_exchange', 'wrong_day', 'cross_currency', 'class_change'])
def test_nyse_policy_does_not_cross_market_date_currency_or_class(tmp_path, fault):
    from bellomberg.valuation.quotation_evidence import sec_us_weekend_quote_basis, verify_sec_us_weekend_quote_basis
    primary, listing = _sec_listing(tmp_path, exchange='New York Stock Exchange')
    _, basis = sec_us_weekend_quote_basis(listing, primary, ticker='SYNX', opening_date=OPENING)
    value = _quote(deepcopy(basis))
    if fault == 'wrong_policy':
        value['price_date_basis']['policy'] = 'nasdaq_weekend_previous_friday/1'
    elif fault == 'wrong_exchange':
        value['price_date_basis']['exchange'] = 'NYSE Arca'
    elif fault == 'wrong_day':
        value['price_as_of'] = '2026-07-23'
    elif fault == 'cross_currency':
        value['financial_currency'] = 'EUR'
    else:
        value['share_class'] = 'Another share class'
    with pytest.raises(ValueError):
        verify_sec_us_weekend_quote_basis(value, listing, primary, ticker='SYNX', opening_date=OPENING)


def test_nyse_missing_friday_is_not_replaced_with_thursday():
    from bellomberg.valuation.quotation_evidence import historical_quote_document
    calls = []
    def fetch(ticker, on):
        calls.append(on)
        return {'symbol':ticker,'date':'2026-07-23','currency':'USD','close':100.}
    result = historical_quote_document('SYNX',on=FRIDAY,as_of='2026-09-29',fetch=fetch)
    assert result['status'] == 'incomplete' and result['documents'] == []
    assert calls == [FRIDAY]
