"""Exact chart identity confirmation; all transports are fictional JSON."""
from copy import deepcopy
import io
import json

import pytest

from bellomberg.agents.trade_idea import resolve_ticker_identity
from bellomberg.valuation.trade_idea_model import _identity_problem


TICKER = 'SYNTH-ID'
META = {'symbol': TICKER, 'instrumentType': 'EQUITY', 'currency': 'EUR',
        'longName': 'Synthetic Legal Issuer plc', 'shortName': 'Synthetic issuer',
        'fullExchangeName': 'Synthetic Main Market', 'exchangeName': 'TEST'}


def _resolve(meta=None, *, chart=None, search=None):
    calls = []
    search = search or {'quotes': [{'symbol': TICKER, 'quoteType': 'EQUITY',
        'shortname': 'Synthetic brand', 'longname': 'Old search display',
        'exchDisp': 'TEST display', 'exchange': 'TEST', 'currency': 'EUR'}]}
    chart = chart if chart is not None else {'chart': {'result': [{'meta': deepcopy(META if meta is None else meta)}], 'error': None}}
    def opener(request, **kwargs):
        calls.append(request.full_url)
        payload = search if '/search?' in request.full_url else chart
        return io.StringIO(json.dumps(payload))
    return resolve_ticker_identity(TICKER, opener=opener), calls


def test_confirmed_fields_come_from_the_exact_chart_and_match_profile_contract():
    identity, calls = _resolve()
    assert identity['status'] == 'confirmed', identity
    assert identity['name'] == META['longName']
    assert identity['exchange'] == META['fullExchangeName']
    assert identity['currency'] == META['currency']
    assert _identity_problem(TICKER, identity, dict(META)) is None
    assert len(calls) == 2


@pytest.mark.parametrize('missing', ['longName', 'fullExchangeName', 'currency', 'instrumentType'])
def test_search_display_is_not_a_replacement_for_a_missing_chart_identity_field(missing):
    meta = deepcopy(META)
    meta.pop(missing)
    if missing == 'longName':
        meta.pop('shortName')
    identity, _ = _resolve(meta)
    assert identity['status'] == 'unverified', identity


def test_profile_short_name_and_exchange_fallback_are_literal_fields_without_aliases():
    meta = deepcopy(META)
    meta.pop('longName')
    meta.pop('fullExchangeName')
    meta['exchange'] = 'EXACT-CODE'
    identity, _ = _resolve(meta)
    assert identity['status'] == 'confirmed', identity
    assert identity['name'] == meta['shortName'] and identity['exchange'] == 'EXACT-CODE'
    assert _identity_problem(TICKER, identity, meta) is None
    assert _identity_problem(TICKER, identity, dict(meta, exchange='Other market'))


@pytest.mark.parametrize('field,value', [('symbol', 'OTHER-ID'), ('instrumentType', 'ETF')])
def test_a_chart_for_another_security_or_instrument_type_cannot_confirm_the_search(field, value):
    meta = dict(META, **{field: value})
    identity, _ = _resolve(meta)
    assert identity['status'] == 'unverified', identity


@pytest.mark.parametrize('results', [[], [{'meta': META}, {'meta': META}]])
def test_empty_or_multiple_chart_results_do_not_silently_choose_the_first(results):
    identity, _ = _resolve(chart={'chart': {'result': results, 'error': None}})
    assert identity['status'] == 'unverified', identity


def test_chart_error_prevents_confirmation_even_when_metadata_remains_in_response():
    identity, _ = _resolve(chart={'chart': {'result': [{'meta': META}],
        'error': {'code': 'Not Found', 'description': 'Declared synthetic failure'}}})
    assert identity['status'] == 'unverified', identity


def test_gbp_pence_unit_is_explicitly_canonicalized_without_changing_exchange():
    identity, _ = _resolve(dict(META, currency='GBp'))
    assert identity['status'] == 'confirmed' and identity['currency'] == 'GBX', identity
    assert identity['exchange'] == META['fullExchangeName']


def test_duplicate_search_display_names_do_not_replace_a_unique_exact_symbol_and_type():
    rows = [{'symbol': TICKER, 'quoteType': 'EQUITY', 'shortname': name,
             'exchDisp': display} for name, display in [('Brand', 'TEST'), ('Full display', 'Other display')]]
    identity, _ = _resolve(search={'quotes': rows})
    assert identity['status'] == 'confirmed', identity
    assert identity['name'] == META['longName'] and identity['exchange'] == META['fullExchangeName']
