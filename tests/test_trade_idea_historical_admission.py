"""Document admission is distinct from proven history, with no live provider."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.preparation_fresh_historical import assemble_fresh_historical
from bellomberg.valuation.trade_idea_model import qualification, prepare, source_fingerprint
from test_sector_analysis import DAY
from test_trade_idea_economic import providers_for, _operating_plan, _documents
from test_preparation_fresh_historical import sources
from test_balance_sheet_evidence import balance_raw
from test_balance_working_capital import case


def history_case(tmp_path):
    """Complete synthetic filing bytes, before economic classification."""
    raw = balance_raw().replace(b'iso4217:USD', b'iso4217:EUR').replace(b'</html>',
        b'<ix:nonnumeric name="dei:Security12bTitle" contextref="now">Common Stock</ix:nonnumeric>'
        b'<ix:nonnumeric name="dei:TradingSymbol" contextref="now">SYNTH-EXT</ix:nonnumeric>'
        b'<ix:nonnumeric name="dei:SecurityExchangeName" contextref="now">TEST</ix:nonnumeric></html>')
    primary, ledger, nwc = case(raw)
    path = tmp_path / 'synthetic-filing.html'
    path.write_bytes(raw)
    primary['archive_path'] = str(path)
    entity = primary['metadata']['issuer']
    report = sources()
    price = next(d for d in report['documents'] if d['id'].startswith('price-'))
    financial = next(d for d in report['documents'] if d['id'] == 'fresh-financial')
    body = json.loads(financial['text'])
    body.update(issuer=entity, records=[])
    body['facts'][0]['entity'] = entity
    body['facts'].append({'taxonomy': 'us-gaap', 'concept': 'CommonStockSharesOutstanding',
        'unit': 'shares', 'entity': entity, 'observation': {'end': '2025-12-31', 'val': 10000000.}})
    financial['metadata']['issuer'] = entity
    financial['text'] = json.dumps(body)
    financial['sha256'] = sha256(financial['text'].encode()).hexdigest()
    from bellomberg.valuation.quotation_evidence import listing_identity_document
    listing = listing_identity_document(primary, raw, ticker='SYNTH-EXT', on='2025-12-31')['documents'][0]
    report.update(documents=[primary, ledger, financial, price, listing],
        balance_sheet={'status': 'ready', 'issues': []},
        selection={'opening_date': '2025-12-31', 'selected_document_id': primary['id'],
                   'selected_document_ids': [primary['id']]})
    providers = providers_for()
    old_profile = providers['profile']
    def profile(*args, **kwargs):
        result = old_profile(*args, **kwargs)
        result['data']['info']['longName'] = entity
        return result
    providers['profile'] = profile
    identity = dict(ticker='SYNTH-EXT', name=entity, exchange='TEST', currency='EUR', status='confirmed')
    return report, providers, identity, nwc


def admitted(tmp_path):
    report, providers, identity, nwc = history_case(tmp_path)
    result = qualification('SYNTH-EXT', identity, DAY, archive_root=tmp_path,
        providers=providers, source_report=report)
    return result, nwc


def test_fresh_documents_admit_historical_work_without_claiming_qualified(tmp_path):
    q, _ = admitted(tmp_path)
    assert q['status'] == 'preparation_required', q['reasons']
    assert q['reasons'] == []
    assert 'source_plan' not in q['source_report']
    assert q['coverage']['sources']['basis'] == 'verified_documents_for_historical_preparation'
    assert set(q['coverage']['sources']['missing_historical_drivers']) == {
        'opening_nwc', 'shares', 'net_debt', 'equity_adjustments'}
    assert q['coverage']['economic_qualification'] == 'pending_historical_proofs'


def test_catalog_history_limit_is_retained_without_rejecting_verified_selected_sources(tmp_path):
    report, providers, identity, _ = history_case(tmp_path)
    warning = {'source': 'catalog', 'reason': 'document limit reached; coverage partial'}
    report['issues'] = [warning]
    report['coverage'] = {'limited': 8, 'max_documents': 4, 'accepted': 4,
        'selection_policy': 'opening_annual_comparative', 'catalog_checked': True, 'catalog_status': 'ok'}
    q = qualification('SYNTH-EXT', identity, DAY, archive_root=tmp_path,
        providers=providers, source_report=report)
    assert q['status'] == 'preparation_required', q['reasons']
    assert q['coverage']['sources']['catalog_warnings'] == [warning]
    assert q['source_report']['issues'] == [warning]


@pytest.mark.parametrize('fault', ['missing_price', 'missing_revenue', 'missing_balance',
    'forged_balance', 'damaged_raw', 'source_gap', 'incomplete_acquisition', 'wrong_issuer',
    'missing_shares', 'weighted_average_shares', 'authorized_shares', 'stale_shares',
    'foreign_shares', 'nonpositive_shares', 'incomplete_balance', 'collector_issue', 'missing_selected'])
def test_unverified_documents_still_block_before_any_proposer(tmp_path, fault):
    report, providers, identity, _ = history_case(tmp_path)
    if fault == 'missing_price':
        report['documents'] = [d for d in report['documents'] if not d['id'].startswith('price-')]
    elif fault == 'missing_revenue':
        report['documents'] = [d for d in report['documents'] if d['id'] != 'fresh-financial']
    elif fault == 'missing_balance':
        report['documents'] = [d for d in report['documents'] if not d['id'].startswith('balance-sheet-')]
    elif fault == 'forged_balance':
        doc = next(d for d in report['documents'] if d['id'].startswith('balance-sheet-'))
        body = json.loads(doc['text']); body['components'][0]['value_exact'] = '999'
        doc['text'] = json.dumps(body); doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    elif fault == 'damaged_raw':
        (tmp_path / 'synthetic-filing.html').write_bytes(b'changed filing')
    elif fault == 'source_gap':
        report['source_gaps'] = [{'reason': 'Original narrative unavailable'}]
    elif fault == 'incomplete_acquisition':
        report['preparation_ready'] = False
    elif fault == 'incomplete_balance':
        report['balance_sheet'] = {'status': 'incomplete', 'issues': [{'reason': 'Source missing'}]}
    elif fault == 'collector_issue':
        report['issues'] = [{'reason': 'Source unavailable'}]
    elif fault == 'missing_selected':
        report['selection']['selected_document_ids'].append('missing-annual-filing')
    elif fault.endswith('shares'):
        doc = next(d for d in report['documents'] if d['id'] == 'fresh-financial')
        body = json.loads(doc['text'])
        fact = body['facts'][-1]
        if fault == 'missing_shares':
            body['facts'].pop()
        elif fault == 'weighted_average_shares':
            fact['concept'] = 'WeightedAverageNumberOfDilutedSharesOutstanding'
        elif fault == 'authorized_shares':
            fact['concept'] = 'CommonStockSharesAuthorized'
        elif fault == 'stale_shares':
            fact['observation']['end'] = '2024-12-31'
        elif fault == 'foreign_shares':
            fact['entity'] = 'Another issuer'
        else:
            fact['observation']['val'] = 0
        doc['text'] = json.dumps(body); doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    else:
        identity['name'] = 'Different issuer'
    q = qualification('SYNTH-EXT', identity, DAY, archive_root=tmp_path,
        providers=providers, source_report=report)
    assert q['status'] == 'blocked'
    with pytest.raises(ValueError, match='Fonti non qualificate'):
        prepare(q, lambda *_: pytest.fail('unverified source reached proposer'), tmp_path/'model')


def test_preparation_grant_must_cover_historical_work_and_exact_document_basis(tmp_path):
    from bellomberg.core.trade_idea_contract import validate_run_authorization
    q, _ = admitted(tmp_path)
    assert q['status'] == 'preparation_required', q['reasons']
    grant = dict(accepted=True, source_fingerprint=q['fingerprint'],
        activities=['model_preparation', 'committee'], max_revision_rounds=0)
    assert validate_run_authorization(grant, q) == grant
    with pytest.raises(ValueError, match='model_preparation'):
        validate_run_authorization({**grant, 'activities': ['committee']}, q)
    altered = deepcopy(q)
    altered['source_report']['selection']['selected_document_ids'] = []
    assert source_fingerprint(altered) != grant['source_fingerprint']
    with pytest.raises(ValueError):
        validate_run_authorization(grant, altered)
    altered = deepcopy(q)
    altered['source_report']['documents'][0]['text'] += ' TAMPERED'
    assert source_fingerprint(altered) != grant['source_fingerprint']
    with pytest.raises(ValueError):
        validate_run_authorization(grant, altered)


def preparation_case(tmp_path):
    report, providers, identity, nwc = history_case(tmp_path)
    extra = _documents()
    body = json.loads(extra[-1]['text'])
    for row in body['observations']:
        row['entity'] = identity['name']
    body['observations'][0]['value'] = -.012
    extra[-1]['text'] = json.dumps(body)
    extra[-1]['sha256'] = sha256(extra[-1]['text'].encode()).hexdigest()
    report['documents'].extend(extra)
    q = qualification('SYNTH-EXT', identity, DAY, archive_root=tmp_path,
        providers=providers, source_report=report)
    assert q['status'] == 'preparation_required', q['reasons']
    full = _operating_plan()
    full['model'].update(deepcopy(q['source_report']['historical_preparation_plan']['model']))
    full['model']['opening_nwc'] = nwc
    for drivers in full['scenarios'].values():
        drivers['net_debt']['value'] = -.012
    return q, full


def test_admitted_history_is_verified_before_forecasts_and_common_excel(tmp_path):
    q, full = preparation_case(tmp_path)
    before = deepcopy(q)
    calls = []
    def proposer(dossier, contract):
        stage = contract['preparation_stage']; scope = stage['scope']
        calls.append((scope, tuple(stage['drivers'])))
        if any(name in stage['drivers'] for name in ('capdev_amortization_years', 'revenue_growth')):
            checkpoint = json.loads((tmp_path/'model'/'historical-preparation.json').read_text())
            assert checkpoint['status'] == 'historical_qualified'
            assert checkpoint['admission_fingerprint'] == q['fingerprint']
        values = full['model'] if scope == 'model' else full['scenarios'][scope]
        return {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                'rationale': 'Offline synthetic source-linked economic reasoning'}
    payload = prepare(q, proposer, tmp_path/'model')
    assert payload['valuation_usability']['usable'] is True, payload.get('error')
    assert payload['preparation']['status'] == 'prepared'
    assert payload['historical_preparation']['status'] == 'historical_qualified'
    assert payload['historical_preparation']['admission_fingerprint'] == q['fingerprint']
    assert q == before
    assert calls and payload.get('path')


def test_missing_final_equity_judgment_preserves_checkpoint_without_excel(tmp_path):
    q, full = preparation_case(tmp_path)
    calls = []
    def proposer(_dossier, contract):
        stage = contract['preparation_stage']; scope = stage['scope']
        calls.extend(stage['drivers'])
        values = full['model'] if scope == 'model' else full['scenarios'][scope]
        return {'drivers': {name: None if name == 'equity_adjustments' else deepcopy(values[name])
                            for name in stage['drivers']}, 'rationale': 'Source claim unavailable'}
    payload = prepare(q, proposer, tmp_path/'model')
    assert payload['valuation_usability']['usable'] is False
    assert 'capdev_amortization_years' in calls and 'revenue_growth' in calls
    assert calls.index('equity_adjustments') > calls.index('revenue_growth')
    assert not payload.get('path')
    checkpoint = json.loads((tmp_path/'model'/'historical-preparation.json').read_text())
    assert checkpoint['status'] == 'historical_qualified'
    assert all('net_debt' in values and 'equity_adjustments' not in values
               for values in checkpoint['candidate']['plan']['scenarios'].values())


def test_pending_history_cannot_short_circuit_through_approved_archive(tmp_path):
    from bellomberg.valuation.input_preparation import ARCHIVE_SOURCE
    q, _ = admitted(tmp_path)
    q['bundle']['case']['sources']['method_inputs'] = {
        'source_id': ARCHIVE_SOURCE, 'status': 'ok', 'records': [{'driver': 'shares'}]}
    q['fingerprint'] = source_fingerprint(q)
    with pytest.raises(ValueError, match='approved inputs'):
        prepare(q, lambda *_: pytest.fail('approved archive skipped the history gate'), tmp_path/'model')
    assert not (tmp_path/'model').exists()


@pytest.mark.parametrize('fault', [None, 'missing_filing', 'weighted_average', 'other_issuer', 'wrong_date', 'wrong_accession'])
def test_opening_shares_inc_typography_requires_exact_sec_count_and_binding(fault):
    from bellomberg.valuation.input_preparation import _fact_proof
    from test_preparation_fresh_historical import sec_issuer_punctuation
    primary, xbrl = sec_issuer_punctuation()
    body = json.loads(xbrl['text'])
    fact = body['facts'][0]
    fact.update(concept='CommonStockSharesOutstanding', unit='shares')
    fact['observation'].pop('start')
    fact['observation']['val'] = 10000000
    if fault == 'weighted_average':
        fact['concept'] = 'WeightedAverageNumberOfDilutedSharesOutstanding'
    elif fault == 'other_issuer':
        body['issuer'] = 'Different Company Inc.'
    elif fault == 'wrong_date':
        fact['observation']['end'] = '2024-12-31'
    elif fault == 'wrong_accession':
        fact['observation']['accn'] = '0000000001-26-000002'
    xbrl['text'] = json.dumps(body); xbrl['sha256'] = sha256(xbrl['text'].encode()).hexdigest()
    item = dict(value=10., evidence_ids=[xbrl['id']], quoted_value=10000000, quoted_unit='shares',
        evidence_pointer={'value': '/facts/0/observation/val', 'unit': '/facts/0/unit',
                          'period': '/facts/0/observation/end'})
    result = _fact_proof('shares', item, [xbrl] if fault == 'missing_filing' else [xbrl, primary],
        'million shares', '2025-12-31', expected_entity=primary['metadata']['issuer'])
    assert (result is None) is (fault is None), result


def test_staged_and_final_compiler_use_selected_sec_filing_for_shares():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    from test_preparation_historical_first import _case, _offline
    from test_preparation_fresh_historical import sec_issuer_punctuation
    bundle, documents, plan = _case()
    primary, xbrl = sec_issuer_punctuation()
    body = json.loads(xbrl['text']); fact = body['facts'][0]
    fact.update(concept='CommonStockSharesOutstanding', unit='shares')
    fact['observation'].pop('start'); fact['observation']['val'] = 10000000
    xbrl['text'] = json.dumps(body); xbrl['sha256'] = sha256(xbrl['text'].encode()).hexdigest()
    documents.extend([primary, xbrl])
    plan['model']['perimeter']['value']['entity'] = primary['metadata']['issuer']
    item = plan['model']['shares']
    for key in ('evidence_quote', 'period_quote'):
        item.pop(key, None)
    item.update(value=10., evidence_ids=[xbrl['id']], quoted_value=10000000, quoted_unit='shares',
        evidence_pointer={'value': '/facts/0/observation/val', 'unit': '/facts/0/unit',
                          'period': '/facts/0/observation/end'})
    report = {'selection': {'selected_document_id': primary['id'], 'selected_document_ids': [primary['id']]}}
    checkpoints = []
    result = prepare_method_inputs(bundle, documents=documents, source_report=report,
        propose=StagedProposer(_offline(plan, []), historical_first=True,
            on_historical=lambda *_: checkpoints.append(True)))
    assert result['status'] == 'prepared', result['issues']
    assert checkpoints == [True]
    rows = result['bundle']['case']['records']
    assert any(row['driver'] == 'shares' and row['source_locator'] == xbrl['id'] for row in rows)


@pytest.mark.parametrize('fault', [None, 'manual', 'absent', 'wrong_admission', 'partial', 'changed_plan', 'wrong_snapshot', 'changed_records'])
def test_revision_of_admitted_history_requires_its_verified_checkpoint(tmp_path, fault):
    from bellomberg.valuation.trade_idea_model import revise
    q, plan = preparation_case(tmp_path)
    def stage(_dossier, contract):
        selected = contract['preparation_stage']
        scope = selected['scope']
        values = plan['model'] if scope == 'model' else plan['scenarios'][scope]
        return {'drivers': {name: deepcopy(values[name]) for name in selected['drivers']},
                'rationale': 'Synthetic reviewed assumptions'}
    before = prepare(q, stage, tmp_path/'before')
    assert before['valuation_usability']['usable']
    unchanged = deepcopy(q)
    if fault == 'absent':
        before.pop('historical_preparation')
    elif fault == 'wrong_admission':
        before['historical_preparation']['admission_fingerprint'] = '0' * 64
    elif fault == 'partial':
        before['historical_preparation']['status'] = 'required'
    elif fault == 'changed_plan':
        before['historical_preparation']['candidate']['plan']['model']['shares']['value'] += 1
    elif fault == 'wrong_snapshot':
        before['snapshot_id'] = 'unrelated-snapshot'
    elif fault == 'changed_records':
        from bellomberg.valuation.sector_analysis import revise_sector_analysis
        altered = deepcopy(before['acquisition_snapshot']['case']['records'])
        next(row for row in altered if row['driver'] == 'calendar')['value']['discount_convention'] = 'ACT/365F'
        before['acquisition_snapshot'] = revise_sector_analysis(before['acquisition_snapshot'], method_records=altered)
        before['snapshot_id'] = before['acquisition_snapshot']['snapshot_id']
    request = {'rationale': 'Explicit forecast-only review', 'evidence_ids': ['annual-1']}
    if fault not in (None, 'manual'):
        with pytest.raises(ValueError):
            revise(before, request, qualification=q,
                propose=lambda *_: pytest.fail('Invalid checkpoint reached provider'), output_dir=tmp_path/'after')
        assert not (tmp_path/'after').exists()
    else:
        plan['scenarios']['base']['wacc']['value'] = .12
        if fault == 'manual':
            request['method_records'] = deepcopy(before['acquisition_snapshot']['case']['records'])
            for row in request['method_records']:
                if row['driver'] == 'wacc' and row['scenario'] == 'base':
                    row.update(value=.12, rationale='Explicit reviewed discount-rate assumption')
        after = revise(before, request, qualification=q,
            propose=None if fault == 'manual' else stage, output_dir=tmp_path/'after')
        assert after['valuation_usability']['usable']
        assert after['generation_id'] != before['generation_id']
        assert after['historical_preparation']['admission_fingerprint'] == q['fingerprint']
        assert any(row['driver'] == 'wacc' and row['scenario'] == 'base' and row['value'] == .12
            for row in after['acquisition_snapshot']['case']['records'])
    assert q == unchanged


@pytest.mark.parametrize('mode', ['manual', 'manual_without_qualification', 'paid'])
def test_revision_cannot_change_checkpointed_calendar_before_generation(tmp_path, mode):
    from bellomberg.valuation.trade_idea_model import revise
    q, plan = preparation_case(tmp_path)
    def stage(_dossier, contract):
        spec = contract['preparation_stage']
        values = plan['model'] if spec['scope'] == 'model' else plan['scenarios'][spec['scope']]
        return {'drivers': {name: deepcopy(values[name]) for name in spec['drivers']},
                'rationale': 'Synthetic source-bound assumptions'}
    before = prepare(q, stage, tmp_path/'before')
    assert before['valuation_usability']['usable']
    unchanged = deepcopy(before)
    records = deepcopy(before['acquisition_snapshot']['case']['records'])
    calendar = next(row for row in records if row['driver'] == 'calendar')
    assert calendar['value']['discount_convention'] == 'annual_end'
    calendar['value']['discount_convention'] = 'ACT/365F'
    request = {'rationale': 'Explicit calendar change', 'evidence_ids': ['annual-1'],
               'method_records': [calendar] if mode == 'paid' else records}
    with pytest.raises(ValueError, match='checkpoint'):
        revise(before, request, qualification=None if mode == 'manual_without_qualification' else q,
            propose=(lambda *_: pytest.fail('Checkpoint mutation reached provider')) if mode == 'paid' else None,
            output_dir=tmp_path/'after')
    assert not (tmp_path/'after').exists()
    assert before == unchanged
