"""Independent fresh-source probes: real compiler, no paid/client/archive fallback."""
from copy import deepcopy
from hashlib import sha256
import json
import socket
import sqlite3

import pytest

from bellomberg.valuation.trade_idea_model import qualification
from bellomberg.valuation.preparation_fresh_historical import assemble_fresh_historical
from test_preparation_fresh_historical import sources, change_body, _fresh_bundle as _bundle
from test_sector_analysis import DAY
from test_trade_idea_economic import providers_for

IDENTITY = {'ticker': 'SYNTH-EXT', 'name': 'SYNTH-GROUP', 'exchange': 'TEST',
            'currency': 'EUR', 'status': 'confirmed'}

def guard(monkeypatch):
    counts = {'network': 0, 'sqlite': 0, 'seed_reads': 0, 'collector': 0}
    def no_network(*args, **kwargs):
        counts['network'] += 1
        pytest.fail('Independent source qualification attempted a network transport')
    def no_sqlite(*args, **kwargs):
        counts['sqlite'] += 1
        pytest.fail('Independent source qualification attempted a SQLite connection')
    def no_seed(*args, **kwargs):
        counts['seed_reads'] += 1
        pytest.fail('Fresh source qualification consulted an archived model or prior source plan')
    monkeypatch.setattr(socket, 'create_connection', no_network)
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(sqlite3, 'connect', no_sqlite)
    return counts, no_seed

def qualify(report, tmp_path, monkeypatch, *, profile_name=None, expected_collectors=1):
    counts, no_seed = guard(monkeypatch)
    providers = providers_for()
    original_profile = providers['profile']
    def profile(*args, **kwargs):
        result = original_profile(*args, **kwargs)
        result['data']['info'].update(symbol='SYNTH-EXT', longName=profile_name or IDENTITY['name'], currency='EUR',
                                    fullExchangeName='TEST', exchange='TEST')
        return result
    providers['profile'] = profile
    def collector(*args, **kwargs):
        counts['collector'] += 1
        return deepcopy(report)
    result = qualification('SYNTH-EXT', IDENTITY, DAY, archive_root=tmp_path,
        providers=providers, collector=collector, historical_seed_loader=no_seed)
    assert counts == {'network': 0, 'sqlite': 0, 'seed_reads': 0, 'collector': expected_collectors}
    return result

def test_fresh_collector_without_source_plan_or_old_seed_has_positive_control(tmp_path, monkeypatch):
    report = sources()
    assert 'source_plan' not in report
    result = qualify(report, tmp_path, monkeypatch)
    assert result['status'] == 'qualified', result['reasons']
    assert result['source_report']['historical_source_assembly']['source_origin'] == 'current_free_catalog_without_previous_model'
    assert result['source_report']['fresh_historical_assembly']['forecast_values_reused'] is False

def test_other_primary_issuer_cannot_value_the_confirmed_ticker(tmp_path, monkeypatch):
    report = sources()
    def other(body):
        body['issuer'] = 'OTHER-GROUP'
        for row in body['records']+body['facts']:
            row['entity'] = 'OTHER-GROUP'
    change_body(report, other)
    report['documents'][0]['metadata']['issuer'] = 'OTHER-GROUP'
    result = qualify(report, tmp_path, monkeypatch)
    assert result['status'] == 'blocked', (
        'Confirmed SYNTH-GROUP/SYNTH-EXT was qualified using OTHER-GROUP financials and unchanged ticker price/listing',
        result['source_report'].get('source_plan', {}).get('model', {}).get('perimeter'))

def test_changed_profile_cannot_relabel_the_identity_already_confirmed_by_pm(tmp_path, monkeypatch):
    report = sources()
    def other(body):
        body['issuer'] = 'OTHER-GROUP'
        for row in body['records']+body['facts']:
            row['entity'] = 'OTHER-GROUP'
    change_body(report, other)
    report['documents'][0]['metadata']['issuer'] = 'OTHER-GROUP'
    result = qualify(report, tmp_path, monkeypatch, profile_name='OTHER-GROUP', expected_collectors=0)
    assert result['status'] == 'blocked', (
        'Primary/profile agreed with one another but silently relabelled the separately confirmed identity name',
        result['identity'], result['source_report'].get('source_plan', {}).get('model', {}).get('perimeter'))

def test_close_must_agree_with_its_exact_dated_fact_and_unit():
    report = sources()
    price = report['documents'][1]
    body = json.loads(price['text'])
    body['facts'][0]['value'] = 999.
    price['text'] = json.dumps(body)
    price['sha256'] = sha256(price['text'].encode()).hexdigest()
    result = assemble_fresh_historical(_bundle(), report, method_id='operating_fcff', as_of=DAY)
    assert result['fresh_historical_assembly']['status'] == 'blocked'
    assert 'quotation' in ' '.join(result['fresh_historical_assembly']['reasons'])

def test_listing_class_cannot_be_a_different_issuers_security():
    report = sources()
    listing = report['documents'][2]
    body = json.loads(listing['text'])
    body['listing']['issuer'] = 'OTHER-GROUP'
    listing['metadata']['issuer'] = 'OTHER-GROUP'
    listing['text'] = json.dumps(body)
    listing['sha256'] = sha256(listing['text'].encode()).hexdigest()
    result = assemble_fresh_historical(_bundle(), report, method_id='operating_fcff', as_of=DAY)
    assert result['fresh_historical_assembly']['status'] == 'blocked', 'Different issuer listing was not bound to the selected financial entity'

def test_provider_origin_label_cannot_relabel_another_tickers_price_url():
    report = sources()
    report['documents'][1]['url'] = 'https://finance.yahoo.com/quote/OTHER/history/'
    result = assemble_fresh_historical(_bundle(), report, method_id='operating_fcff', as_of=DAY)
    assert result['fresh_historical_assembly']['status'] == 'blocked', (
        'A caller origin label accepted a primary price URL for OTHER while all JSON symbols claimed SYNTH-EXT')

def test_diluted_share_conflict_cannot_be_resolved_by_first_source_selection():
    report = sources()
    def conflict(body):
        other = deepcopy(body['records'][1])
        other['value'] = 11.
        body['records'].append(other)
    change_body(report, conflict)
    result = assemble_fresh_historical(_bundle(), report, method_id='operating_fcff', as_of=DAY)
    assert result['fresh_historical_assembly']['status'] == 'blocked'
    assert 'shares' in ' '.join(result['fresh_historical_assembly']['reasons'])

def test_supplied_old_plan_cannot_fill_a_missing_fresh_opening_claim():
    report = sources()
    change_body(report, lambda body: body['records'].pop())
    report['source_plan'] = {'model': {'equity_adjustments': {'value': 0, 'kind': 'analyst_estimate'}}}
    result = assemble_fresh_historical(_bundle(), report, method_id='operating_fcff', as_of=DAY)
    assert result['fresh_historical_assembly']['status'] == 'blocked'
    assert 'source_plan' not in result
    assert 'equity_adjustments' in ' '.join(result['fresh_historical_assembly']['reasons'])
