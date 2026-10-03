"""Raw-only primary admission is separate from economic sufficiency."""
from copy import deepcopy
from hashlib import sha256

import pytest


def qualified_raw(tmp_path, *, fault=None, route='default'):
    from test_commodity_trust_evidence import sources, ENTITY, TICKER, DAY
    from test_trade_idea_economic import synthetic_observed_quote_info
    from bellomberg.valuation.trade_idea_model import qualification
    docs = sources(tmp_path)
    if fault == 'missing_prospectus':
        docs.pop()
    elif fault == 'raw_changed':
        from pathlib import Path
        Path(docs[0]['archive_path']).write_bytes(b'Changed original source')
    elif fault == 'ambiguous_product':
        from pathlib import Path
        extra = deepcopy(docs[1]); path = tmp_path/'other-product.html'
        path.write_bytes(Path(extra['archive_path']).read_bytes()+b'\n<!--different primary-->')
        extra.update(id=sha256(path.read_bytes()).hexdigest(), document_sha256=sha256(path.read_bytes()).hexdigest(),
                     archive_path=str(path), url='https://example.org/issuer/other-product')
        docs.append(extra)
    elif fault == 'wrong_entity':
        entity = 'Other Gold Trust'
    entity = 'Other Gold Trust' if fault == 'wrong_entity' else ENTITY
    info = {'symbol': TICKER, 'longName': entity, 'financialCurrency': 'USD', 'quoteType': 'ETF'}
    info.update(synthetic_observed_quote_info(TICKER, DAY, currency='USD', exchange='TEST'))
    profile = {'status': 'ok', 'as_of': DAY, 'retrieved_at': DAY+'T12:00:00+00:00',
               'source_id': 'https://example.org/synthetic-chart', 'data': {'info': info}}
    identity = {'ticker': TICKER, 'name': entity, 'exchange': 'TEST', 'currency': 'USD', 'status': 'confirmed'}
    def no_seed(*_a, **_k):
        pytest.fail('No old model may qualify raw commodity primary documents')
    kwargs = {'documents': docs} if route == 'default' else {'source_report': {'documents': docs}}
    return qualification(TICKER, identity, DAY, archive_root=tmp_path/'archive',
        providers={'profile': lambda *_a, **_k: deepcopy(profile)}, historical_seed_loader=no_seed, **kwargs)


@pytest.mark.parametrize('route', ['default', 'source_report'])
def test_raw_only_catalog_automatically_compiles_without_plan_or_normalized_input(tmp_path, route):
    from bellomberg.valuation.trade_idea_model import prepare, model_exhibits
    q = qualified_raw(tmp_path, route=route)
    assert q['status'] == 'qualified', q['reasons']
    receipt = q['source_report']['commodity_trust_raw_catalog']
    assert receipt['status'] == 'ready' and receipt['discovery'] == 'not_performed'
    assert set(receipt['roles']) == {'product', 'statement', 'prospectus'}
    if route == 'default':
        assert q['source_report']['trade_idea_document_catalog']['purpose'] == 'commodity_trust_raw_historical_reproof'
    calls = []
    def proposer(*_a):
        calls.append('explicit deterministic observed plan')
        return deepcopy(q['source_report']['source_plan'])
    model = prepare(q, proposer, tmp_path/'model')
    assert model['analysis_usability']['usable'] and len(calls) == 1
    assert model_exhibits(model)['status'] == 'complete'
    assert model['market_quote']['status'] == 'ok'
    assert model.get('fair_value_base') is None


@pytest.mark.parametrize('fault', ['missing_prospectus', 'raw_changed', 'ambiguous_product', 'wrong_entity'])
def test_incomplete_ambiguous_changed_or_unbound_raw_catalog_stops_before_preparation(tmp_path, fault):
    q = qualified_raw(tmp_path, fault=fault)
    assert q['status'] == 'blocked' and q['reasons']
    assert 'source_plan' not in q['source_report']


def test_verified_filing_catalog_retains_raw_without_granting_economic_readiness(tmp_path):
    from test_commodity_trust_evidence import sources, TICKER, DAY
    from bellomberg.valuation.preparation_historical_sources import collect_trade_idea_sources
    docs = sources(tmp_path)
    filings = [{'ticker': TICKER, 'candidati': [{'stato': 'verificato', 'url': doc['url'],
        'path': doc['archive_path'], 'sha256': doc['document_sha256'], 'filed_date': doc['published_at'],
        'metadati': deepcopy(doc['metadata'])} for doc in docs]}]
    def no_seed(*_a, **_k):
        pytest.fail('Raw source collection cannot depend on a valuation archive')
    report = collect_trade_idea_sources(TICKER, as_of=DAY, archive_root=tmp_path,
        method_id='exposure_analysis', filing_results=filings, seed_loader=no_seed,
        catalog=lambda *_: {'stato': 'ok', 'documenti': [], 'motivi': []})
    assert len(report['documents']) == 3
    assert not report['preparation_ready'] and 'source_plan' not in report
    assert report['trade_idea_document_catalog']['purpose'] == 'commodity_trust_raw_historical_reproof'
    assert all(doc.get('archive_path') and not doc['metadata'].get('normalizer') for doc in report['documents'])
