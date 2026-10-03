"""Production qualifier retrieves a historical bridge through its archive path."""
from copy import deepcopy
import json

from bellomberg.valuation.trade_idea_model import qualification, prepare
from bellomberg.valuation.preparation_historical_sources import assemble_historical_sources, _pin
from test_trade_idea_economic import qualified, IDENTITY, _operating_plan, _documents, DAY, providers_for


def archived_model(tmp_path):
    return prepare(qualified(tmp_path), lambda *_: deepcopy(_operating_plan()), tmp_path / 'common')


def report_for(payload):
    return {'preparation_ready': True, 'status': 'ready',
            'selection': {'opening_date': payload['valuation_date']},
            'documents': deepcopy(payload['preparation']['review_basis']['dossier']['documents'])}


def test_default_qualifier_finds_common_archive_without_supplied_plan(tmp_path):
    old = archived_model(tmp_path)
    calls = []
    def collector(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return report_for(old)
    result = qualification('SYNTH-EXT', IDENTITY, DAY, archive_root=tmp_path,
        providers=providers_for(), collector=collector)
    assert result['status'] == 'qualified', result['reasons']
    assert len(calls) == 1
    assembly = result['source_report']['historical_source_assembly']
    assert assembly['status'] == 'ready' and not assembly['forecast_values_reused']
    plan = result['source_report']['source_plan']
    assert all(set(drivers) == {'net_debt', 'equity_adjustments'} and
               all(item['kind'] == 'historical' for item in drivers.values())
               for drivers in plan['scenarios'].values())
    assert 'gross_margin' not in json.dumps(plan)
    assert plan['model']['historical_revenue']['value'] == old['preparation']['proposal']['plan']['model']['historical_revenue']['value']
    assert plan['model']['historical_revenue']['kind'] == 'historical'
    assert result['coverage']['sources']['prepared_plan_available'] is False


def test_explicit_server_collector_receives_profile_website_only(tmp_path):
    old = archived_model(tmp_path)
    received = []
    def collector(ticker, *, as_of, archive_root, financial_currency, method_id, issuer_website=None):
        received.append(issuer_website)
        return report_for(old)
    providers = providers_for()
    original = providers['profile']
    def profile(*args, **kwargs):
        source = deepcopy(original(*args, **kwargs))
        source['data']['info']['website'] = 'https://example.org/synthetic-issuer'
        return source
    providers['profile'] = profile
    result = qualification('SYNTH-EXT', IDENTITY, DAY, archive_root=tmp_path,
        providers=providers, collector=collector)
    assert result['status'] == 'qualified', result['reasons']
    assert received == ['https://example.org/synthetic-issuer']


def test_default_collector_receives_only_the_confirmed_profile_issuer(tmp_path, monkeypatch):
    from bellomberg.valuation import preparation_historical_sources as module
    old = archived_model(tmp_path)
    received = []
    def collector(ticker, **kwargs):
        received.append(kwargs.get('issuer_name'))
        return report_for(old)
    monkeypatch.setattr(module, 'collect_trade_idea_sources', collector)
    result = qualification('SYNTH-EXT', IDENTITY, DAY, archive_root=tmp_path,
        providers=providers_for())
    assert result['status'] == 'qualified', result['reasons']
    assert received == [IDENTITY['name']]


def test_native_collector_binds_foreign_catalog_to_confirmed_issuer_name(tmp_path, monkeypatch):
    from bellomberg.valuation import preparation_sources
    from bellomberg.market_data import sec_edgar
    from bellomberg.valuation.preparation_historical_sources import collect_trade_idea_sources
    calls = []
    def catalog(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return {'stato': 'errore', 'documenti': [], 'motivi': ['Name unavailable']}
    def collect(ticker, **kwargs):
        response = kwargs['catalog'](ticker)
        return {'documents': [], 'issues': response['motivi'], 'status': 'incomplete'}
    monkeypatch.setattr(sec_edgar, 'get_filing_catalog', catalog)
    monkeypatch.setattr(preparation_sources, 'collect_preparation_evidence', collect)
    collect_trade_idea_sources('SYNTH.MI', as_of=DAY, archive_root=tmp_path,
        issuer_name='Synthetic S.A.', seed_loader=lambda *_a, **_k: [])
    assert calls == [('SYNTH.MI', {'issuer_name': 'Synthetic S.A.'})]


def test_failed_source_acquisition_keeps_specific_reason_in_qualification(tmp_path):
    result = qualification('SYNTH-EXT', IDENTITY, DAY, archive_root=tmp_path,
        providers=providers_for(), source_report={'status': 'incomplete', 'documents': [],
            'issues': [{'source': 'SEC', 'reason': 'Synthetic primary HTTP 403'}]})
    assert result['status'] == 'blocked'
    assert any('Synthetic primary HTTP 403' in reason for reason in result['reasons'])


def test_historical_reconfirmation_does_not_renew_or_copy_forecasts(tmp_path):
    old = archived_model(tmp_path)
    canary = 'PRIVATE_PM_FORECAST_CANARY'
    old['preparation']['proposal']['plan']['scenarios']['base']['gross_margin']['rationale'] = canary
    from bellomberg.valuation.preparation_seed import _digest
    old['preparation']['review_basis']['seed']['plan_sha256'] = _digest(old['preparation']['proposal']['plan'])
    result = qualification('SYNTH-EXT', IDENTITY, '2026-09-11', archive_root=tmp_path,
        providers=providers_for(), collector=lambda *_a, **_k: report_for(old),
        historical_seed_loader=lambda *_a, **_k: [deepcopy(old)])
    assert result['status'] == 'qualified', result['reasons']
    plan = result['source_report']['source_plan']
    assert canary not in json.dumps(plan) and 'gross_margin' not in json.dumps(plan)
    assert all(item['valid_until'] == '2026-09-11' for item in plan['model'].values())
    assert old['preparation']['proposal']['plan']['scenarios']['base']['gross_margin']['valid_until'] == DAY


def test_new_balance_date_or_incomplete_collector_does_not_inherit_old_bridge(tmp_path):
    old = archived_model(tmp_path)
    for change in ({'selection': {'opening_date': '2026-06-30'}}, {'preparation_ready': False}):
        report = dict(report_for(old), **change)
        result = qualification('SYNTH-EXT', IDENTITY, DAY, archive_root=tmp_path,
            providers=providers_for(), collector=lambda *_a, **_k: deepcopy(report))
        assert result['status'] == 'blocked'
        assert result['source_report']['historical_source_assembly']['status'] == 'blocked'


def test_missing_or_changed_source_is_not_repaired_by_previous_model(tmp_path):
    old = archived_model(tmp_path)
    report = report_for(old)
    report['documents'] = []
    result = assemble_historical_sources(qualified(tmp_path)['bundle'], report,
        archive_root=tmp_path, seed_loader=lambda *_a, **_k: [old])
    assert result['historical_source_assembly']['status'] == 'blocked'
    report = report_for(old)
    report['documents'][0]['metadata'] = {'report_date': '2024-12-31'}
    result = assemble_historical_sources(qualified(tmp_path)['bundle'], report,
        archive_root=tmp_path, seed_loader=lambda *_a, **_k: [old])
    assert result['historical_source_assembly']['status'] == 'blocked'
    assert 'changed=' in result['historical_source_assembly']['reasons'][0]


def test_recompilation_cutoff_is_not_an_economic_change_but_publication_and_period_are():
    old = {'id': 'observed', 'url': 'https://example.org/synthetic', 'text': 'unchanged',
           'sha256': 'same-text', 'document_sha256': 'same-raw', 'published_at': '2026-09-10',
           'metadata': {'normalizer': 'statement_tables_v1', 'report_date': '2026-06-30',
                        'as_of': '2026-09-10', 'entity': 'SYNTH-EXT'}}
    current = deepcopy(old)
    current['metadata']['as_of'] = '2026-09-11'
    assert _pin(current) == _pin(old)
    current['published_at'] = '2026-09-11'
    assert _pin(current) != _pin(old)
    current = deepcopy(old)
    current['metadata']['report_date'] = '2026-09-30'
    assert _pin(current) != _pin(old)
