"""Acquisition and compiler retain the true quote date for a weekend close."""
from copy import deepcopy
from datetime import date
import json

import pytest

from test_quotation_weekend_basis import OPENING, FRIDAY, _sec_listing


def _collected(tmp_path, monkeypatch, *, missing=False, exchange='The Nasdaq Global Select Market'):
    from bellomberg.valuation import valuation_sources, market_reference_evidence
    from bellomberg.valuation import preparation_earnings, balance_sheet_evidence, balance_detail_evidence
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence

    primary, _ = _sec_listing(tmp_path, exchange=exchange)
    primary['metadata']['form'] = '10-K'
    monkeypatch.setattr(valuation_sources, 'collect_documents', lambda *a, **k: {
        'status': 'ready', 'documents': [primary], 'issues': [], 'coverage': {}})
    monkeypatch.setattr(valuation_sources, 'company_facts_documents', lambda *a, **k: {
        'status': 'ready', 'documents': [{'id': 'synthetic-xbrl', 'text': '{}'}], 'issues': []})
    monkeypatch.setattr(preparation_earnings, 'collect_earnings_evidence', lambda *a, **k: {
        'status': 'ready', 'documents': [], 'issues': []})
    monkeypatch.setattr(market_reference_evidence, 'collect_market_references', lambda *a, **k: {
        'status': 'not_requested', 'documents': [], 'issues': []})
    monkeypatch.setattr(balance_detail_evidence, 'collect_balance_details', lambda *a, **k: {
        'status': 'ready', 'documents': [], 'packets': {}, 'issues': []})
    monkeypatch.setattr(balance_sheet_evidence, 'collect_balance_sheet', lambda *a, **k: {
        'status': 'ready', 'documents': [], 'packets': {}, 'detail_packets': {}, 'issues': []})
    calls = []
    def fetch(ticker, on):
        calls.append(on)
        if missing:
            raise ValueError('No exact Friday observation')
        return {'symbol': ticker, 'date': on, 'currency': 'USD', 'close': 100.}
    report = collect_preparation_evidence('SYNX', as_of='2026-09-29', archive_root=tmp_path,
        financial_currency='USD', method_id='operating_fcff', price_fetch=fetch)
    return report, primary, calls


def test_collector_keeps_accounting_date_and_fetches_explicit_friday(tmp_path, monkeypatch):
    report, _, calls = _collected(tmp_path, monkeypatch)
    assert calls == [FRIDAY]
    assert report['preparation_ready'], report['issues']
    assert report['selection']['opening_date'] == OPENING
    assert report['components']['historical_quote']['price_date_basis']['accounting_date'] == OPENING
    price = next(d for d in report['documents'] if d['id'].startswith('price-'))
    assert json.loads(price['text'])['observation']['date'] == FRIDAY
    assert price['published_at'] == FRIDAY


def test_collector_does_not_search_an_earlier_day_when_friday_missing(tmp_path, monkeypatch):
    report, _, calls = _collected(tmp_path, monkeypatch, missing=True)
    assert calls == [FRIDAY]
    assert not report['preparation_ready'] and not report['documents']
    assert any('No exact Friday observation' in str(i) for i in report['issues'])


def _compile_quote(quotation, catalog, primary, *, include_primary=True, selected_primary=True):
    from bellomberg.valuation.input_preparation import _compile
    from bellomberg.valuation.operating_adapter import SCHEMA
    return _compile({'model': {'quotation': quotation}, 'scenarios': {
        s: {} for s in ('bear', 'base', 'bull')}}, {'quotation': SCHEMA['quotation']}, {},
        {'entity': primary['metadata']['issuer'], 'currency': 'USD'},
        {'valuation_date': OPENING}, '', catalog, date(2026, 9, 29),
        method='operating_fcff', ticker='SYNX',
        sec_filings=[catalog[primary['id']]] if include_primary else [],
        sec_primary_id=primary['id'] if selected_primary else 'other-selected-primary')


def _prepared(tmp_path, monkeypatch, **kwargs):
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_fresh_historical import _quotation
    report, primary, _ = _collected(tmp_path, monkeypatch, **kwargs)
    documents = [d for d in report['documents'] if d['id'] != 'synthetic-xbrl']
    catalog, issues, _ = _catalog(documents, date(2026, 9, 29))
    assert not issues
    quotation = _quotation(catalog, 'SYNX', OPENING, 'USD', '2026-09-29',
        entity=primary['metadata']['issuer'], primary_id=primary['id'])
    return quotation, catalog, primary


def test_fresh_quotation_recompiles_friday_price_and_sunday_listing(tmp_path, monkeypatch):
    quotation, catalog, primary = _prepared(tmp_path, monkeypatch)
    assert quotation['value']['price_as_of'] == FRIDAY
    assert quotation['value']['price_date_basis']['accounting_date'] == OPENING
    assert FRIDAY in quotation['rationale'] and OPENING in quotation['rationale']
    rows, issues, _ = _compile_quote(quotation, catalog, primary)
    assert not issues, issues
    assert rows[0]['value'] == quotation['value']
    assert rows[0]['period'] == OPENING


def test_nyse_collector_and_compiler_preserve_market_specific_basis(tmp_path, monkeypatch):
    quotation, catalog, primary = _prepared(tmp_path, monkeypatch, exchange='New York Stock Exchange')
    assert quotation['value']['price_date_basis']['policy'] == 'nyse_weekend_previous_friday/1'
    rows, issues, _ = _compile_quote(quotation, catalog, primary)
    assert not issues and rows[0]['period'] == OPENING
    assert rows[0]['value']['price_as_of'] == FRIDAY


@pytest.mark.parametrize('fault', ['primary_omitted', 'altered_basis', 'altered_price_date', 'altered_archive',
                                  'record_pointer', 'other_selected_primary', 'other_price_ticker', 'other_price_url'])
def test_compiler_reproves_weekend_policy_from_selected_original(tmp_path, monkeypatch, fault):
    quotation, catalog, primary = _prepared(tmp_path, monkeypatch)
    quotation = deepcopy(quotation)
    if fault == 'altered_basis':
        quotation['value']['price_date_basis']['listing_document_id'] = 'listing-' + '0' * 64
    elif fault == 'altered_price_date':
        quotation['value']['price_as_of'] = '2026-07-23'
    elif fault == 'altered_archive':
        (tmp_path / 'primary.html').write_bytes(b'changed after fresh assembly')
    elif fault == 'record_pointer':
        quotation['record_pointer'] = '/records/0'
    elif fault in ('other_price_ticker', 'other_price_url'):
        from hashlib import sha256
        price = catalog['price-SYNX-' + FRIDAY]
        if fault == 'other_price_ticker':
            body = json.loads(price['text'])
            body['observation']['symbol'] = 'OTHER'
            price['text'] = json.dumps(body)
            price['sha256'] = sha256(price['text'].encode()).hexdigest()
        else:
            price['url'] = 'https://example.org/other-price'
    rows, issues, _ = _compile_quote(quotation, catalog, primary, include_primary=fault != 'primary_omitted',
                                   selected_primary=fault != 'other_selected_primary')
    assert not rows and any(i['code'] == 'unverified_fact' for i in issues), issues
