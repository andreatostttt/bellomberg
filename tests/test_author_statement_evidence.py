"""Native raw-bound statement views, with no implicit economic decisions."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def statement_case(tmp_path):
    from test_balance_sheet_evidence import balance_raw
    from test_balance_detail_evidence import source
    from bellomberg.valuation.trade_idea_model import source_fingerprint
    raw = balance_raw().replace(b'</html>', b'''<ix:nonnumeric name="dei:Security12bTitle" contextref="now">Common Stock</ix:nonnumeric>
<ix:nonnumeric name="dei:TradingSymbol" contextref="now">SYNTH</ix:nonnumeric>
<ix:nonnumeric name="dei:SecurityExchangeName" contextref="now">TEST</ix:nonnumeric></html>''')
    primary = source(raw)
    primary.pop('balance_detail_fields')
    path = tmp_path / (primary['id'] + '.html')
    path.write_bytes(raw)
    primary['archive_path'] = str(path)
    qualification = {'ticker': 'SYNTH', 'as_of': '2026-10-02', 'status': 'research_required',
        'identity': {'ticker': 'SYNTH', 'currency': 'USD', 'exchange': 'TEST'},
        'source_report': {'documents': [primary]}, 'coverage': {}}
    qualification['fingerprint'] = source_fingerprint(qualification)
    plan = {'model': {'calendar': {'value': {'valuation_date': '2025-12-31'}},
        'perimeter': {'value': {'entity': 'Synthetic Industrial Issuer', 'currency': 'USD',
                              'share_class': 'Common Stock'}}}, 'scenarios': {}}
    return qualification, plan, primary, tmp_path


def test_existing_balance_normalizer_exposes_components_without_authoring_any_driver(statement_case):
    from bellomberg.valuation.author_statement_evidence import statement_basis, merge_statement_evidence
    from bellomberg.valuation.input_preparation import _catalog
    from datetime import date
    qualification, plan, primary, root = statement_case
    before = deepcopy((qualification, plan))
    result = statement_basis(qualification, plan, archive_root=root)
    assert result['status'] in ('ready', 'partial'), result.get('issues')
    assert result['view']['economic_decisions_applied'] is False
    assert result['view']['opening_date'] == '2025-12-31'
    assert result['view']['available_contracts']['opening_nwc']['operation'] == 'balance_sheet_nwc'
    assert result['view']['sources']['balance_sheet']['status'] == 'ready'
    assert not set(result['view']['driver_basis']) & {'opening_nwc', 'shares', 'net_debt', 'equity_adjustments'}
    assert len(json.dumps(result['view'], ensure_ascii=False)) <= 15000
    overlay = merge_statement_evidence(qualification, plan, result['receipt'], archive_root=root)
    catalog, issues, _ = _catalog(overlay['source_report']['documents'], date.fromisoformat(qualification['as_of']))
    assert not issues, issues
    balance = next(doc for doc in catalog.values() if (doc.get('metadata') or {}).get('normalizer') == 'balance_sheet_v1')
    payload = json.loads(balance['text'])
    assert payload['reported_balance_reconciled'] and payload['economic_classification_approved'] is False
    assert payload['components'] and all(row['end'] == '2025-12-31' for row in payload['components'])
    for _ in range(2):
        replay = statement_basis(qualification, plan, archive_root=root, existing=result['receipt'])
        assert replay == result
    assert (qualification, plan) == before


@pytest.mark.parametrize('fault', ['raw', 'receipt', 'date', 'issuer', 'currency'])
def test_changed_raw_or_author_context_cannot_reuse_statement_receipt(statement_case, fault):
    from bellomberg.valuation.author_statement_evidence import statement_basis, merge_statement_evidence
    qualification, plan, primary, root = statement_case
    result = statement_basis(qualification, plan, archive_root=root)
    assert result.get('receipt'), result
    receipt = deepcopy(result['receipt'])
    if fault == 'raw':
        Path(primary['archive_path']).write_bytes(b'changed original')
    elif fault == 'receipt':
        receipt['documents'][0]['text'] += ' changed'
    elif fault == 'date':
        plan['model']['calendar']['value']['valuation_date'] = '2025-09-30'
    elif fault == 'issuer':
        plan['model']['perimeter']['value']['entity'] = 'Unrelated issuer'
    else:
        plan['model']['perimeter']['value']['currency'] = 'EUR'
    with pytest.raises(ValueError):
        merge_statement_evidence(qualification, plan, receipt, archive_root=root)


def test_unsupported_statement_layout_retains_an_explicit_gap(statement_case):
    from bellomberg.valuation.author_statement_evidence import statement_basis
    qualification, plan, _primary, root = statement_case
    result = statement_basis(qualification, plan, archive_root=root)
    assert result['view']['sources']['statement_tables']['status'] != 'ready'
    assert result['view']['sources']['statement_tables']['issues']
    assert result['view']['driver_basis'] == {}
    assert 'historical_revenue' in result['view']['missing_driver_basis']
    assert not plan['model'].get('historical_revenue')


def test_author_driver_changes_do_not_invalidate_the_source_only_receipt(statement_case):
    from bellomberg.valuation.author_statement_evidence import statement_basis, merge_statement_evidence
    qualification, plan, _primary, root = statement_case
    observed = statement_basis(qualification, plan, archive_root=root)
    plan['model']['historical_revenue'] = {'value': 42., 'kind': 'historical',
        'evidence_ids': [qualification['source_report']['documents'][0]['id']]}
    plan['scenarios'] = {'base': {'net_debt': {'value': 999.}}}
    restored = statement_basis(qualification, plan, archive_root=root, existing=observed['receipt'])
    assert restored == observed
    assert merge_statement_evidence(qualification, plan, observed['receipt'], archive_root=root)['fingerprint']


def test_statement_overlay_composes_with_independently_verified_quotation(statement_case):
    from bellomberg.valuation.author_statement_evidence import statement_basis, merge_statement_evidence
    from bellomberg.valuation.author_quotation import quotation_basis, merge_quotation_evidence
    qualification, plan, _primary, root = statement_case
    before = deepcopy((qualification, plan))
    quotes = []
    def quote(ticker, on):
        quotes.append((ticker, on))
        return {'symbol': ticker, 'date': on, 'close': 43.25, 'currency': 'USD',
                'source': 'offline frozen daily quotation'}
    observed = quotation_basis(qualification, plan, archive_root=root, price_fetch=quote)
    assert observed['status'] == 'ready', observed
    quoted = merge_quotation_evidence(qualification, plan, observed['receipt'], archive_root=root)
    statements = statement_basis(qualification, plan, archive_root=root)
    composed = merge_statement_evidence(qualification, plan, statements['receipt'],
        archive_root=root, overlay=quoted)
    assert composed['source_report']['derived_quotation'] == quoted['source_report']['derived_quotation']
    assert composed['source_report']['derived_statements']['receipt_sha256'] == statements['receipt']['sha256']
    assert (qualification, plan) == before
    assert quotes == [('SYNTH', '2025-12-31')]


@pytest.mark.parametrize('wrong_day', [False, True])
def test_nested_inline_period_whitespace_keeps_exact_date_and_issuer_checks(tmp_path, wrong_day):
    from test_statement_shares_inline import _sources
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    source, tagged, path = _sources(tmp_path)
    raw = path.read_text().replace('>July 26, 2026</ix:nonNumeric>',
        '> <ix:nonNumeric name="dei:CurrentFiscalYearEndDate">July ' + ('27' if wrong_day else '26') +
        '</ix:nonNumeric> , 2026</ix:nonNumeric>')
    path.write_text(raw, encoding='utf8')
    source.update(id=sha256(path.read_bytes()).hexdigest(), document_sha256=sha256(path.read_bytes()).hexdigest())
    result = normalize_statement_shares(source, [tagged])
    if wrong_day:
        assert result['status'] == 'incomplete'
        assert 'period differs' in str(result['issues'])
    else:
        assert result['status'] == 'ready', result['issues']


@pytest.mark.parametrize('title,accepted', [
    ('Common Stock, par value $0.00001 per share', True),
    ('Preferred Stock, par value $0.00001 per share', False),
    ('American Depositary Shares, par value $0.00001 per share', False),
])
def test_common_stock_par_value_word_order_does_not_admit_other_instruments(tmp_path, title, accepted):
    from test_statement_shares_inline import _sources
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    source, tagged, path = _sources(tmp_path)
    raw = path.read_text().replace('Common Stock, $0.001 par value per share', title)
    path.write_text(raw, encoding='utf8')
    digest = sha256(path.read_bytes()).hexdigest()
    source.update(id=digest, document_sha256=digest)
    result = normalize_statement_shares(source, [tagged])
    assert (result['status'] == 'ready') is accepted, result['issues']


@pytest.mark.parametrize('fault', [None, 'other_issuer', 'cik', 'text', 'not_sec'])
def test_sec_bound_inc_typography_keeps_nwc_and_bridge_identity_guards(fault):
    from test_balance_working_capital import case, prove
    from test_balance_sheet_evidence import balance_raw
    from test_balance_detail_evidence import source
    from test_balance_component_bridge import bridge_case, prove as prove_bridge
    from bellomberg.valuation.balance_sheet_evidence import extract_balance_sheet_packet, normalize_balance_sheet
    original, _ledger, item = case()
    bridge, _ = bridge_case()
    raw = balance_raw().replace(b'Synthetic Industrial Issuer', b'Synthetic Industrial Issuer, Inc.')
    raw = raw.replace(b'iso4217:USD', b'iso4217:EUR')
    origin = source(raw)
    origin['metadata']['issuer'] = 'Synthetic Industrial Issuer Inc'
    origin['balance_sheet_fields'] = extract_balance_sheet_packet(origin, raw)
    normalized = normalize_balance_sheet(origin)
    assert normalized['status'] == 'ready', normalized
    ledger = normalized['documents'][0]
    item['evidence_ids'] = [origin['id'], ledger['id']]
    item['calculation']['balance_document_id'] = ledger['id']
    for choice in item['calculation']['classifications'] + item['calculation']['nonmonetary_review']:
        choice['evidence_ids'] = [origin['id']]
    bridge['evidence_ids'] = [origin['id'], ledger['id']]
    for term in bridge['calculation']['terms']:
        term['evidence_ids'] = [ledger['id']]
    entity = 'Synthetic Industrial Issuer, Inc.'
    if fault == 'other_issuer':
        entity = 'Other Industrial Issuer, Inc.'
    elif fault == 'cik':
        origin['metadata']['emittente_id'] = 'CIK:0000000456'
    elif fault == 'text':
        origin['text'] += ' unsealed modification'
    elif fault == 'not_sec':
        origin['url'] = origin['url'].replace('www.sec.gov', 'unrelated.invalid')
    assert (prove(item, [origin, ledger], entity=entity) is None) is (fault is None)
    assert (prove_bridge(bridge, [origin, ledger], entity=entity) is None) is (fault is None)


def _cached_statement_facts(primary):
    return {'cik': 123, 'entityName': primary['metadata']['issuer'], 'facts': {'us-gaap': {
        'Revenues': {'label': 'Synthetic annual revenue', 'units': {'USD': [{
            'start': '2025-01-01', 'end': '2025-12-31', 'val': 120_000,
            'accn': primary['metadata']['accession'], 'filed': primary['published_at'],
            'form': '10-K', 'fy': 2025, 'fp': 'FY'}]}}}}}


def test_local_companyfacts_cache_is_pinned_before_two_replays(statement_case, monkeypatch):
    from bellomberg.valuation.author_statement_evidence import statement_basis
    from bellomberg.market_data import sec_xbrl
    qualification, plan, primary, root = statement_case
    cache = root/'local-cache'
    cache.mkdir()
    path = cache/'CIK0000000123.json'
    response = _cached_statement_facts(primary)
    path.write_text(json.dumps(response), encoding='utf8')
    monkeypatch.setattr(sec_xbrl, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(sec_xbrl, '_fetch_companyfacts',
        lambda _cik: pytest.fail('Local source evidence must never invoke acquisition'))
    initial = statement_basis(qualification, plan, archive_root=root)
    assert initial['view']['sources']['companyfacts']['status'] == 'ready'
    assert initial['view']['driver_basis']['historical_revenue']['value'] == .12
    path.write_text('{"cik":456,"entityName":"Mutated cache"}', encoding='utf8')
    for _ in range(2):
        restored = statement_basis(qualification, plan, archive_root=root, existing=initial['receipt'])
        assert restored == initial
    altered = deepcopy(initial['receipt'])
    altered['companyfacts']['response']['entityName'] = 'Unrelated issuer'
    assert statement_basis(qualification, plan, archive_root=root, existing=altered)['status'] == 'incomplete'


def test_companyfacts_receipt_roundtrips_sorted_checkpoint_json_without_moving_locators(statement_case):
    from bellomberg.valuation.author_statement_evidence import statement_basis
    qualification, plan, primary, root = statement_case
    response = _cached_statement_facts(primary)
    revenue = response['facts']['us-gaap']['Revenues']
    response['facts']['us-gaap'] = {'ZOtherReportedObservation': deepcopy(revenue), 'Revenues': revenue}
    observed = statement_basis(qualification, plan, archive_root=root,
        companyfacts_fetch=lambda _: deepcopy(response))
    serialized = json.loads(json.dumps(observed['receipt'], sort_keys=True))
    restored = statement_basis(qualification, plan, archive_root=root, existing=serialized,
        companyfacts_fetch=lambda _: pytest.fail('Replay must never read a different response'))
    assert restored == observed


@pytest.mark.parametrize('fault', [None, 'amount', 'duplicate', 'missing', 'issuer', 'url', 'period', 'seal'])
def test_legacy_companyfacts_order_requires_exact_reproved_membership(statement_case, fault):
    from bellomberg.valuation.author_statement_evidence import _preserve_companyfacts_order
    from bellomberg.valuation.valuation_sources import company_facts_documents
    qualification, _plan, primary, _root = statement_case
    response = _cached_statement_facts(primary)
    revenue = response['facts']['us-gaap']['Revenues']
    response['facts']['us-gaap'] = {'ZOtherReportedObservation': deepcopy(revenue), 'Revenues': revenue}
    native = lambda payload: company_facts_documents([primary], as_of=qualification['as_of'], fetch=lambda _: payload)
    legacy = native(response)
    rebuilt = native(json.loads(json.dumps(response, sort_keys=True)))
    assert legacy['documents'][0]['text'] != rebuilt['documents'][0]['text']
    prior = deepcopy(legacy['documents'][0])
    body = json.loads(prior['text'])
    if fault == 'amount':
        body['facts'][0]['observation']['val'] += 1
    elif fault == 'duplicate':
        body['facts'].append(deepcopy(body['facts'][0]))
    elif fault == 'missing':
        body['facts'].pop()
    elif fault == 'issuer':
        body['issuer'] = 'Different issuer'
    elif fault == 'url':
        prior['url'] += '?different-source'
    elif fault == 'period':
        body['facts'][0]['observation']['end'] = '2024-12-31'
    if fault is not None:
        prior['text'] = json.dumps(body)
        if fault != 'seal':
            prior['sha256'] = sha256(prior['text'].encode()).hexdigest()
    if fault is None:
        result = _preserve_companyfacts_order(rebuilt, {'documents': [prior]})
        assert result == legacy
    else:
        with pytest.raises(ValueError):
            _preserve_companyfacts_order(rebuilt, {'documents': [prior]})


@pytest.mark.parametrize('fault', ['cik', 'issuer', 'accession', 'filed', 'future_end'])
def test_companyfacts_cannot_substitute_another_issuer_filing_or_period(statement_case, fault):
    from bellomberg.valuation.author_statement_evidence import statement_basis
    qualification, plan, primary, root = statement_case
    response = _cached_statement_facts(primary)
    observation = response['facts']['us-gaap']['Revenues']['units']['USD'][0]
    if fault == 'cik':
        response['cik'] = 456
    elif fault == 'issuer':
        response['entityName'] = 'Unrelated issuer'
    elif fault == 'accession':
        observation['accn'] = '0000000123-26-999999'
    elif fault == 'filed':
        observation['filed'] = '2026-03-01'
    else:
        observation['end'] = '2027-12-31'
    result = statement_basis(qualification, plan, archive_root=root,
        companyfacts_fetch=lambda _cik: deepcopy(response))
    assert 'historical_revenue' not in result['view']['driver_basis']
    assert result['issues']


def test_frozen_6936_two_real_filings_expose_recompilable_2025_facts(tmp_path, monkeypatch):
    from bellomberg.valuation import author_quotation
    from bellomberg.valuation.author_statement_evidence import statement_basis, merge_statement_evidence
    from bellomberg.valuation.input_preparation import _catalog
    from datetime import date
    configured = os.environ.get('BELLOMBERG_TEST_PRIVATE_FIXTURE_ROOT')
    if not configured:
        pytest.skip('Private frozen fixtures not configured: BELLOMBERG_TEST_PRIVATE_FIXTURE_ROOT')
    fixture_root = Path(configured)
    base = fixture_root / '2026-10-02-uber-15usd/context-quote-closeout'
    proof_file = base / 'test_frozen_author_quotation.py'
    frozen_file = base / 'live/frozen-terminal-6936.json'
    facts_path = fixture_root / '2026-10-02-uber-20usd/statements/frozen-companyfacts-0001543151.json'
    if not all(path.exists() for path in (proof_file, frozen_file, facts_path)):
        pytest.skip('Private frozen live receipts are intentionally outside the repository')
    raw = frozen_file.read_bytes()
    assert sha256(raw).hexdigest() == 'c6e2786153d52204da9a4161842c4c345cc320256182f83b81c7a38044896def'
    frozen = json.loads(raw.decode('utf-8-sig'))
    progress = json.loads(frozen['run']['progress_json'])
    plan = deepcopy(progress['checkpoint']['data']['_model_input_draft'])
    original_plan = deepcopy(plan)
    captured = {}
    original = author_quotation.quotation_basis
    def capture(qualification, prior_plan, **kwargs):
        captured.setdefault('qualification', deepcopy(qualification))
        captured.setdefault('archive_root', kwargs['archive_root'])
        return original(qualification, prior_plan, **kwargs)
    spec = importlib.util.spec_from_file_location('frozen_quote_materialization_for_statements', proof_file)
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
    with monkeypatch.context() as patch:
        patch.setattr(author_quotation, 'quotation_basis', capture)
        proof.test_frozen_uber_exact_author_date_and_class_with_synthetic_quote(tmp_path, patch)
    qualification = captured['qualification']
    before = deepcopy(qualification)
    facts_raw = facts_path.read_bytes()
    assert sha256(facts_raw).hexdigest() == 'ec2bd2412cd4b765c79e6475257f6c93fa5b07c9fe6dab95f4d24699ebb66dc7'
    requests = []
    def cached_facts(cik):
        requests.append(cik)
        return json.loads(facts_raw)
    result = statement_basis(qualification, plan, archive_root=captured['archive_root'], companyfacts_fetch=cached_facts)
    (tmp_path/'statement-evidence-diagnostic.json').write_text(json.dumps(result['view'], indent=2), encoding='utf8')
    from bellomberg.valuation.preparation_fresh_historical import _revenue
    from bellomberg.valuation.input_preparation import _fact_proof
    docs = {doc['id']: deepcopy(doc) for doc in qualification['source_report']['documents']}
    docs[result['receipt']['context']['primary_document_id']].update(result['receipt']['primary_additions'])
    docs.update({doc['id']: doc for doc in result['documents']})
    identity_diagnostic = {}
    primary = docs[result['receipt']['context']['primary_document_id']]
    for entity in (plan['model']['perimeter']['value']['entity'], primary['metadata']['issuer']):
        try:
            observed = _revenue(docs, entity, '2025-12-31', 'USD', qualification['as_of'], sec_filings=[primary])
            identity_diagnostic[entity] = {'driver': observed, 'proof': _fact_proof('historical_revenue', observed,
                [docs[ident] for ident in observed['evidence_ids']] + [primary], 'USD million', '2025-12-31',
                expected_entity=entity)}
        except ValueError as exc:
            identity_diagnostic[entity] = {'error': str(exc)}
    (tmp_path/'identity-diagnostic.json').write_text(json.dumps(identity_diagnostic, indent=2), encoding='utf8')
    assert result['status'] in ('ready', 'partial'), result.get('issues')
    assert result['view']['opening_date'] == '2025-12-31'
    assert result['view']['sources']['balance_sheet']['status'] == 'ready', result['view']
    assert result['view']['sources']['companyfacts']['status'] == 'ready', result['view']
    assert result['view']['sources']['statement_shares']['status'] == 'incomplete'
    assert 'context' in str(result['view']['sources']['statement_shares']['issues'])
    share_facts = result['view']['reported_share_observations']['facts']
    assert {row['role'] for row in share_facts} == {'outstanding', 'weighted_basic', 'weighted_diluted'}
    assert len(share_facts) == 3
    assert result['view']['reported_balance']['total_components'] == 24
    assert result['view']['reported_balance']['omitted_components'] == 0
    assert 'historical_revenue' in result['view']['driver_basis'], result['view']
    assert len(json.dumps(result['view'], ensure_ascii=False)) <= 9000
    overlay = merge_statement_evidence(qualification, plan, result['receipt'], archive_root=captured['archive_root'])
    catalog, issues, _ = _catalog(overlay['source_report']['documents'], date(2026, 10, 2))
    assert not issues, issues
    from bellomberg.valuation.diluted_share_estimate import _bound_fact, prove_diluted_share_estimate, METHOD
    terms = {}
    for row in share_facts:
        root_pointer = row['pointer']
        item = {'value': row['reported_value'] / 1_000_000, 'evidence_ids': [row['document_id']],
            'quoted_value': row['reported_value'], 'quoted_unit': row['unit'], 'evidence_pointer': {
                'value': root_pointer + '/observation/val', 'unit': root_pointer + '/unit',
                'period': root_pointer + '/observation/end'}}
        assert _bound_fact(row['role'], item, catalog, plan['model']['perimeter']['value']['entity'],
            '2025-12-31', [primary])['source_id'] == row['document_id']
        terms[row['role']] = item
    from bellomberg.valuation.quotation_evidence import listing_identity_document
    final_primary = catalog[primary['id']]
    listing = listing_identity_document(final_primary, Path(final_primary['archive_path']).read_bytes(),
        ticker='UBER', on='2025-12-31')
    assert listing['status'] == 'ready', listing
    catalog[listing['documents'][0]['id']] = listing['documents'][0]
    proxy = {'value': terms['outstanding']['value'] * terms['weighted_diluted']['value'] / terms['weighted_basic']['value'],
        'kind': 'analyst_estimate', 'rationale': 'OFFLINE TEST ONLY: explicit period dilution ratio proxy; no real adoption.',
        'evidence_ids': [share_facts[0]['document_id'], final_primary['id'], listing['documents'][0]['id']],
        'valid_until': qualification['as_of'], 'valid_until_basis': {'policy': 'same_day', 'as_of': qualification['as_of']},
        'dilution_estimate': {'method': METHOD, 'facts': terms,
            'applicability': 'Synthetic test judgment using the original same-filing basic/diluted pair.',
            'limitation': 'Period-weighted proxy only, not a reported opening diluted count; award mix, averaging, rounding and future vesting remain risks.'}}
    problem = prove_diluted_share_estimate(proxy, catalog,
        entity=plan['model']['perimeter']['value']['entity'], opening_date='2025-12-31',
        sec_filings=[final_primary], share_class=plan['model']['perimeter']['value']['share_class'])
    (tmp_path/'diluted-share-full-proof.json').write_text(json.dumps({'problem': problem,
        'offline_test_only': True, 'draft_adopted': False, 'proxy': proxy}, indent=2), encoding='utf8')
    assert problem is None, problem
    assert result['view']['available_contracts']['opening_nwc']['operation'] == 'balance_sheet_nwc'
    assert len(qualification['source_report']['documents']) == 2
    assert len(overlay['source_report']['documents']) > 2
    for _ in range(2):
        replay = statement_basis(qualification, plan, archive_root=captured['archive_root'], existing=result['receipt'],
            companyfacts_fetch=lambda _cik: pytest.fail('Pinned proof must not read mutable cache again'))
        assert replay == result
    assert qualification == before and plan == original_plan
    assert set(result['view']['driver_basis']).isdisjoint({'opening_nwc', 'shares', 'net_debt', 'equity_adjustments'})
    assert all(doc['metadata']['report_date'] == '2025-12-31' for doc in result['documents']
        if doc['metadata'].get('normalizer'))
    assert requests == ['0001543151']
