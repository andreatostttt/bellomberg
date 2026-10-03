"""New issuer source qualification: no prior model, forecast, network or DB."""
from copy import deepcopy
from hashlib import sha256
import json

from bellomberg.valuation.preparation_fresh_historical import assemble_fresh_historical
from test_input_preparation import _bundle
from test_sector_analysis import DAY, providers_for


def sources():
    entity, on = 'SYNTH-GROUP', '2025-12-31'
    records = []
    for driver, field, value, unit, basis in (
        ('opening_nwc', 'historical_financials', 12., 'EUR million', 'operating'),
        ('shares', 'diluted_shares', 10., 'million shares', 'valuation'),
        ('net_debt', 'enterprise_equity_bridge', 3., 'EUR million', 'valuation'),
        ('equity_adjustments', 'enterprise_equity_bridge', 2., 'EUR million', 'valuation')):
        records.append(dict(driver=driver, field=field, value=value, entity=entity,
                            period=on, unit=unit, accounting_basis=basis))
    text = json.dumps({'issuer': entity, 'records': records, 'facts': [
        {'taxonomy': 'us-gaap', 'concept': 'RevenueFromContractWithCustomerExcludingAssessedTax',
         'unit': 'EUR', 'entity': entity,
         'observation': {'start': '2025-01-01', 'end': on, 'val': 100000000.}}]})
    primary = dict(id='fresh-primary', url='https://example.org/issuer/annual.json',
        published_at='2026-02-01', text=text, sha256=sha256(text.encode()).hexdigest(),
        metadata={'issuer': entity, 'report_date': on, 'financial_currency': 'EUR', 'form': '10-K'})
    financial = deepcopy(primary)
    financial.update(id='fresh-financial', url='https://example.org/issuer/financial-observations.json')
    raw = ('<html><body>'
        '<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="cover">'+entity+'</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:Security12bTitle" contextRef="cover">Common Stock</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:TradingSymbol" contextRef="cover">SYNTH-EXT</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="cover">TEST</ix:nonNumeric>'
        '</body></html>').encode()
    primary.update(url='https://example.org/issuer/annual.html', text=raw.decode(),
        sha256=sha256(raw).hexdigest(), document_sha256=sha256(raw).hexdigest())
    price_text = json.dumps({'symbol': 'SYNTH-EXT', 'observation': {
        'symbol': 'SYNTH-EXT', 'currency': 'EUR', 'date': on, 'close': 10.},
        'facts': [{'value': 10., 'unit': 'EUR per share', 'end': on}]})
    price = dict(id='price-SYNTH-EXT-'+on, url='https://finance.yahoo.com/quote/SYNTH-EXT/history/',
        published_at=on, text=price_text, sha256=sha256(price_text.encode()).hexdigest(),
        origin='yfinance_unadjusted_close', metadata={'ticker': 'SYNTH-EXT', 'price_as_of': on})
    from bellomberg.valuation.quotation_evidence import listing_identity_document
    listing = listing_identity_document(primary, raw, ticker='SYNTH-EXT', on=on)['documents'][0]
    return dict(preparation_ready=True, documents=[primary, price, listing, financial],
        selection={'opening_date': on, 'selected_document_id': primary['id']})


def change_body(report, edit):
    document = next(row for row in report['documents'] if row['id']=='fresh-financial')
    body = json.loads(document['text']); edit(body)
    document['text'] = json.dumps(body)
    document['sha256'] = sha256(document['text'].encode()).hexdigest()


def _fresh_bundle():
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    providers = providers_for()
    original = providers['profile']
    def profile(*args, **kwargs):
        result = original(*args, **kwargs)
        result['data']['info'].update(longName='SYNTH-GROUP', symbol='SYNTH-EXT', currency='EUR')
        return result
    providers['profile'] = profile
    return prepare_sector_analysis('SYNTH-EXT', as_of=DAY, providers=providers)


def test_fresh_corporation_name_requires_raw_cik_and_exact_confirmed_listing(tmp_path):
    from test_sec_corporation_name_binding import _documents, TICKER, OPENING, SEC_NAME
    from bellomberg.valuation.quotation_evidence import historical_quote_document
    listing, primary, profile, _ = _documents(tmp_path)
    price = historical_quote_document(TICKER, on='2026-07-24', as_of='2026-09-29',
        fetch=lambda ticker, on: {'symbol': ticker, 'date': on, 'currency': 'USD', 'close': 100.})['documents'][0]
    body = json.dumps({'issuer': SEC_NAME, 'facts': [{
        'taxonomy': 'us-gaap', 'concept': 'Revenues', 'unit': 'USD', 'entity': SEC_NAME,
        'observation': {'start': '2025-07-27', 'end': OPENING, 'val': 100_000_000}}]})
    financial = {'id': 'synthetic-financial', 'url': 'https://example.org/financial.json',
        'text': body, 'sha256': sha256(body.encode()).hexdigest(), 'published_at': '2026-08-01'}
    report = {'preparation_ready': True, 'documents': [primary, listing, price, financial],
        'selection': {'opening_date': OPENING, 'selected_document_id': primary['id'],
                      'selected_document_ids': [primary['id']]}, 'issues': []}
    bundle = _fresh_bundle()
    bundle['case'].update(ticker=TICKER, info=profile)
    result = assemble_fresh_historical(bundle, report, method_id='operating_fcff', as_of='2026-09-29')
    receipt = result['fresh_historical_assembly']
    assert not receipt.get('fatal_identity_mismatch'), receipt
    assert receipt['issuer_name_binding']['sec_issuer'] == SEC_NAME
    assert receipt['issuer_name_binding']['profile_name'] == profile['longName']
    assert receipt['issuer_name_binding']['primary_document_id'] == primary['id']
    assert not result.get('source_plan')  # Name proof cannot supply missing balances.
    bundle['case']['info']['longName'] = 'Other Corporation'
    blocked = assemble_fresh_historical(bundle, report, method_id='operating_fcff', as_of='2026-09-29')
    assert blocked['fresh_historical_assembly']['fatal_identity_mismatch']


def run(report):
    return assemble_fresh_historical(_fresh_bundle(), report, method_id='operating_fcff', as_of=DAY)


def sec_issuer_punctuation():
    """Synthetic filing and companyfacts for one accession, without private data."""
    issuer, display = 'SYNTHETIC INDUSTRIAL ISSUER INC', 'Synthetic Industrial Issuer, Inc.'
    cik, accession, filed, on = '0000000001', '000000000126000001', '2026-02-15', '2025-12-31'
    raw = '<html><body>synthetic annual filing</body></html>'
    digest = sha256(raw.encode()).hexdigest()
    primary = dict(id=digest,
        url='https://www.sec.gov/Archives/edgar/data/1/' + accession + '/synthetic.htm',
        published_at=filed, text=raw, sha256=digest, document_sha256=digest,
        metadata={'issuer': issuer, 'emittente_id': 'CIK:' + cik,
                  'accession': '0000000001-26-000001', 'form': '10-K', 'report_date': on})
    fact = {'taxonomy': 'us-gaap', 'concept': 'RevenueFromContractWithCustomerExcludingAssessedTax',
            'unit': 'USD', 'observation': {'val': 100_000_000, 'start': '2025-01-01', 'end': on,
                                        'accn': '0000000001-26-000001', 'filed': filed, 'form': '10-K'}}
    text = json.dumps({'cik': cik, 'issuer': display, 'facts': [fact]})
    xbrl = dict(id='xbrl-' + cik + '-' + accession,
        url='https://data.sec.gov/api/xbrl/companyfacts/CIK' + cik + '.json',
        published_at=filed, text=text, sha256=sha256(text.encode()).hexdigest(),
        metadata={'emittente_id': 'CIK:' + cik, 'accession': accession})
    return primary, xbrl


def _sec_punctuation_catalog():
    from datetime import date
    from bellomberg.valuation.input_preparation import _catalog

    primary, xbrl = sec_issuer_punctuation()
    catalog, issues, _ = _catalog([primary, xbrl], date(2026, 9, 29))
    assert not issues
    return primary, xbrl, catalog


def test_sec_inc_display_punctuation_preserves_currency():
    """A bound companyfacts issuer must not disappear solely for Inc typography."""
    from bellomberg.valuation.preparation_fresh_historical import _currency

    primary, _, catalog = _sec_punctuation_catalog()
    entity, on = primary['metadata']['issuer'], primary['metadata']['report_date']
    assert _currency(catalog, primary, entity, on) == 'USD'


def test_sec_inc_display_punctuation_preserves_revenue():
    from bellomberg.valuation.preparation_fresh_historical import _revenue

    primary, xbrl, catalog = _sec_punctuation_catalog()
    entity, on = primary['metadata']['issuer'], primary['metadata']['report_date']
    revenue = _revenue(catalog, entity, on, 'USD', '2026-09-29',
                       sec_filings=[catalog[primary['id']]])
    assert revenue['value'] == 100.
    assert revenue['evidence_ids'] == [xbrl['id']]


def test_sec_inc_display_punctuation_preserves_compiler_proof():
    from bellomberg.valuation.input_preparation import _fact_proof

    primary, xbrl, catalog = _sec_punctuation_catalog()
    entity, on = primary['metadata']['issuer'], primary['metadata']['report_date']
    revenue = {'value': 100., 'evidence_ids': [xbrl['id']], 'quoted_value': 100_000_000,
               'quoted_unit': 'USD', 'evidence_pointer': {
                   'value': '/facts/0/observation/val', 'unit': '/facts/0/unit',
                   'period': '/facts/0/observation/end'}}
    assert _fact_proof('historical_revenue', revenue, [catalog[xbrl['id']], catalog[primary['id']]],
                       'USD million', on, expected_entity=entity) is None


def test_sec_inc_display_requires_original_filing_in_compiler():
    from bellomberg.valuation.input_preparation import _fact_proof

    primary, xbrl, catalog = _sec_punctuation_catalog()
    item = {'value': 100., 'evidence_ids': [xbrl['id']], 'quoted_value': 100_000_000,
            'quoted_unit': 'USD', 'evidence_pointer': {
                'value': '/facts/0/observation/val', 'unit': '/facts/0/unit',
                'period': '/facts/0/observation/end'}}
    assert _fact_proof('historical_revenue', item, [catalog[xbrl['id']]], 'USD million',
                       primary['metadata']['report_date'],
                       expected_entity=primary['metadata']['issuer']) is not None


def test_sec_inc_display_rejects_unbound_cik_accession_and_distinct_issuer():
    from datetime import date
    import pytest
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_fresh_historical import _currency

    for mismatch in ('cik', 'accession', 'issuer'):
        primary, xbrl = sec_issuer_punctuation()
        body = json.loads(xbrl['text'])
        if mismatch == 'cik':
            body['cik'] = '0000000002'
        elif mismatch == 'accession':
            body['facts'][0]['observation']['accn'] = '0000000001-26-000002'
        else:
            body['issuer'] = 'Distinct Industrial Issuer, Inc.'
        xbrl['text'] = json.dumps(body)
        xbrl['sha256'] = sha256(xbrl['text'].encode()).hexdigest()
        catalog, issues, _ = _catalog([primary, xbrl], date(2026, 9, 29))
        assert not issues, mismatch
        with pytest.raises(ValueError, match='unique primary financial currency'):
            _currency(catalog, catalog[primary['id']], primary['metadata']['issuer'],
                      primary['metadata']['report_date'])


def test_sec_inc_display_rejects_changed_source_hashes():
    from datetime import date
    from bellomberg.valuation.input_preparation import _catalog

    for damaged in ('xbrl_text', 'filing_bytes'):
        primary, xbrl = sec_issuer_punctuation()
        if damaged == 'xbrl_text':
            xbrl['text'] += ' '
        else:
            primary['document_sha256'] = 'f' * 64
        _, issues, _ = _catalog([primary, xbrl], date(2026, 9, 29))
        assert issues, damaged
        assert issues[0]['code'] in ('hash_mismatch', 'document_identity_mismatch')


def test_sec_inc_display_rejects_conflicting_financial_currencies():
    from datetime import date
    import pytest
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_fresh_historical import _currency

    primary, xbrl = sec_issuer_punctuation()
    body = json.loads(xbrl['text'])
    other = deepcopy(body['facts'][0])
    other['unit'] = 'EUR'
    body['facts'].append(other)
    xbrl['text'] = json.dumps(body)
    xbrl['sha256'] = sha256(xbrl['text'].encode()).hexdigest()
    catalog, issues, _ = _catalog([primary, xbrl], date(2026, 9, 29))
    assert not issues
    with pytest.raises(ValueError, match='unique primary financial currency'):
        _currency(catalog, catalog[primary['id']], primary['metadata']['issuer'],
                  primary['metadata']['report_date'])


def test_complete_new_issuer_without_prior_model_qualifies_historical_only():
    original = sources(); before = deepcopy(original)
    result = run(original)
    assert original == before
    assert result['fresh_historical_assembly']['status'] == 'ready', result['fresh_historical_assembly']
    plan = result['source_plan']
    assert plan['model']['historical_revenue']['value'] == 100.
    assert plan['model']['shares']['value'] == 10.
    assert set(plan['scenarios']['base']) == {'net_debt', 'equity_adjustments'}
    assert all(row['kind'] == 'historical' for row in plan['scenarios']['base'].values())
    assert 'wacc' not in plan['scenarios']['base']
    assert result['fresh_historical_assembly']['previous_model_archive_reads'] == 'none_by_contract'


def test_missing_opening_claim_is_not_zero_or_an_estimate():
    report = sources()
    change_body(report, lambda body: body['records'].pop())
    result = run(report)
    assert 'source_plan' not in result
    assert 'equity_adjustments' in ' '.join(result['fresh_historical_assembly']['reasons'])


def test_conflicting_same_period_sources_block_not_first_wins():
    report = sources()
    def conflict(body):
        row = deepcopy(body['records'][2]); row['value'] = 99.; body['records'].append(row)
    change_body(report, conflict)
    result = run(report)
    assert 'source_plan' not in result
    assert 'net_debt' in ' '.join(result['fresh_historical_assembly']['reasons'])


def test_interim_revenue_never_becomes_full_year():
    report = sources()
    change_body(report, lambda body: body['facts'][0]['observation'].update(start='2025-07-01'))
    result = run(report)
    assert 'source_plan' not in result
    assert 'historical_revenue' in ' '.join(result['fresh_historical_assembly']['reasons'])


def test_wrong_issuer_claim_and_absent_listing_are_explicit_gaps():
    report = sources()
    change_body(report, lambda body: body['records'][2].update(entity='OTHER-GROUP'))
    report['documents'].pop(2)
    result = run(report)
    assert 'source_plan' not in result
    reasons = ' '.join(result['fresh_historical_assembly']['reasons'])
    assert 'net_debt' in reasons and 'quotation' in reasons


def test_absent_collector_or_unimplemented_method_cannot_be_ready():
    report = sources(); report['preparation_ready'] = False
    assert run(report)['fresh_historical_assembly']['status'] == 'blocked'
    result = assemble_fresh_historical(_bundle(), sources(), method_id='bank_residual_income', as_of=DAY)
    assert result['fresh_historical_assembly']['status'] == 'unsupported'
    assert 'source_plan' not in result


def test_nonpositive_share_count_is_not_a_qualified_opening():
    report = sources()
    change_body(report, lambda body: body['records'][1].update(value=-10.))
    result = run(report)
    assert 'source_plan' not in result
    assert 'shares' in ' '.join(result['fresh_historical_assembly']['reasons'])


def test_fresh_interim_revenue_reconciles_annual_plus_current_minus_prior():
    report = sources()
    on = '2026-06-30'
    report['selection']['opening_date'] = on
    for document in report['documents']:
        document['id'] = document['id'].replace('2025-12-31', on)
        document['text'] = document['text'].replace('2025-12-31', on)
        document['sha256'] = sha256(document['text'].encode()).hexdigest()
        document['published_at'] = on if document.get('origin') == 'yfinance_unadjusted_close' else '2026-08-15'
        for key in ('report_date', 'price_as_of'):
            if key in document['metadata']:
                document['metadata'][key] = on
    change_body(report, lambda body: body.update(facts=[]))
    from test_input_evidence_semantics import _ttm_case
    _, documents = _ttm_case()
    report['documents'].extend(documents)
    result = run(report)
    assert result['fresh_historical_assembly']['status'] == 'ready', result['fresh_historical_assembly']
    revenue = result['source_plan']['model']['historical_revenue']
    assert revenue['value'] == 115.
    assert revenue['calculation']['operation'] == 'trailing_twelve_months'


def test_fresh_foreign_listing_uses_recompiled_same_day_fx():
    from datetime import date
    import pytest
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_fresh_historical import _quotation
    from test_foreign_listing_evidence import inputs, normalize
    from test_fx_evidence import source, normalize as normalize_fx, ON, AS_OF
    primary, annual, quote = inputs()
    listing = normalize(primary, annual, quote)['documents'][0]
    fx_raw = source(); fx = normalize_fx(fx_raw)
    catalog, issues, _ = _catalog([primary, annual, quote, listing, fx_raw, fx], date.fromisoformat(AS_OF))
    assert not issues
    result = _quotation(catalog, 'SYN.MI', ON, 'USD', AS_OF)
    assert result['value']['financial_to_quote_rate'] == .8
    assert result['value']['quote_currency'] == 'EUR'
    assert result['facts']['financial_to_quote_rate']['evidence_ids'] == [fx['id']]
    del catalog[fx['id']]
    with pytest.raises(ValueError, match='FX'):
        _quotation(catalog, 'SYN.MI', ON, 'USD', AS_OF)


def test_repeated_identical_ttm_facts_do_not_multiply_compiler_work(monkeypatch):
    from bellomberg.valuation import input_preparation
    from bellomberg.valuation.preparation_fresh_historical import _revenue
    from test_input_evidence_semantics import _ttm_case
    _, documents = _ttm_case()
    for document in documents:
        body = json.loads(document['text'])
        body['facts'] = [deepcopy(fact) for fact in body['facts'] for _ in range(85)]
        document['text'] = json.dumps(body)
        document['sha256'] = sha256(document['text'].encode()).hexdigest()
    original = input_preparation._fact_proof
    calls = []
    def counted(*args, **kwargs):
        calls.append(args[1])
        assert len(calls) < 64, 'Repeated equal observations multiply real compiler proofs'
        return original(*args, **kwargs)
    monkeypatch.setattr(input_preparation, '_fact_proof', counted)
    item = _revenue({doc['id']: doc for doc in documents}, 'SYNTH-GROUP', '2026-06-30', 'EUR', DAY)
    assert item['value'] == 115.
    assert len(calls) < 8


def test_distinct_ambiguous_ttm_candidates_are_blocked_before_enumeration():
    import pytest
    from bellomberg.valuation.preparation_fresh_historical import _revenue
    from test_input_evidence_semantics import _ttm_case
    _, documents = _ttm_case()
    for document in documents:
        body = json.loads(document['text'])
        body['facts'] = [dict(deepcopy(fact), distinct_source_annotation=str(i))
                         for fact in body['facts'] for i in range(85)]
        document['text'] = json.dumps(body)
        document['sha256'] = sha256(document['text'].encode()).hexdigest()
    with pytest.raises(ValueError, match='ambiguous TTM combinations'):
        _revenue({doc['id']: doc for doc in documents}, 'SYNTH-GROUP', '2026-06-30', 'EUR', DAY)
