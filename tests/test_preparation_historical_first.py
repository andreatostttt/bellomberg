"""Offline historical-first FCFF staging; no provider, network or runtime database."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import json

import pytest

from test_input_preparation import _bundle, _documents, _operating_plan


def _case():
    documents, plan = _documents(), _operating_plan()
    documents[0]['text'] += (' Net debt: EUR 3 million as of 2025-12-31.'
                             ' Equity adjustments: EUR 2 million as of 2025-12-31.')
    documents[0]['sha256'] = sha256(documents[0]['text'].encode()).hexdigest()
    for scope in ('bear', 'base', 'bull'):
        for driver, label, value in (('net_debt', 'Net debt', 3.),
                                     ('equity_adjustments', 'Equity adjustments', 2.)):
            item = plan['scenarios'][scope][driver]
            quote = f'{label}: EUR {int(value)} million'
            item.update(value=value, kind='historical', evidence_quote=quote,
                        quoted_value=value, quoted_unit='EUR million',
                        period_quote=quote + ' as of 2025-12-31')
    return _bundle(), documents, plan


def _offline(plan, calls):
    def propose(dossier, contract):
        stage = contract['preparation_stage']
        scope, names = stage['scope'], stage['drivers']
        calls.append((scope, tuple(names)))
        source = plan['model'] if scope == 'model' else plan['scenarios'][scope]
        return {'drivers': {name: deepcopy(source[name]) for name in names if name in source},
                'rationale': 'Synthetic source-bound step'}
    return propose


def test_historical_first_proves_all_opening_claims_before_forecasts_and_finishes():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    calls, snapshots = [], []

    def on_historical(historical, dossier, contract):
        snapshots.append((deepcopy(historical), list(calls)))
        assert set(historical['model']) == {'perimeter', 'calendar', 'quotation',
                                            'historical_revenue', 'opening_nwc', 'shares'}
        assert all(set(historical['scenarios'][scope]) == {'net_debt'}
                   for scope in ('bear', 'base', 'bull'))
        assert dossier['ticker'] == bundle['case']['ticker']
        assert contract['method_id'] == 'operating_fcff'
        checked = prepare_method_inputs(bundle, documents=documents,
                                        propose=lambda *_: deepcopy(historical))
        historical_drivers = {'quotation', 'historical_revenue', 'opening_nwc',
                              'shares', 'net_debt'}
        assert not [issue for issue in checked['issues'] if issue['field'] in historical_drivers]
        assert {(row['driver'], row['scenario']) for row in checked['proposal']['method_records']
                if row['driver'] in historical_drivers} == ({(name, 'model') for name in
                    ('quotation', 'historical_revenue', 'opening_nwc', 'shares')} |
                    {(name, scope) for scope in ('bear', 'base', 'bull')
                     for name in ('net_debt',)})
        historical['model']['historical_revenue']['value'] = -1  # Callback gets a copy.

    prepared = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(_offline(plan, calls), historical_first=True,
                               on_historical=on_historical))
    assert prepared['status'] == 'prepared', prepared['issues']
    assert len(snapshots) == 1
    historical_calls = [('model', ('perimeter', 'calendar', 'quotation')),
                        ('model', ('historical_revenue',)), ('model', ('opening_nwc',)),
                        ('model', ('shares',))] + [('bear', ('net_debt',))]
    assert calls[:len(historical_calls)] == historical_calls
    assert snapshots[0][1] == historical_calls
    assert calls[len(historical_calls)] == ('model', ('capdev_amortization_years',))
    assert prepared['proposal']['plan']['model']['historical_revenue']['value'] == 100.
    assert all(prepared['proposal']['plan']['scenarios'][scope]['net_debt']['value'] == 3.
               for scope in ('bear', 'base', 'bull'))


def test_historical_first_callback_exception_stops_before_forecast():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    calls = []

    def stop(*_):
        raise RuntimeError('historical review rejected')

    prepared = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(_offline(plan, calls), historical_first=True, on_historical=stop))
    assert prepared['status'] == 'incomplete'
    assert 'historical review rejected' in prepared['issues'][0]['reason']
    assert all(name != 'capdev_amortization_years' for _, names in calls for name in names)
    assert len(calls) == 5


@pytest.mark.parametrize('problem', ('missing', 'estimate'))
def test_historical_first_rejects_incomplete_or_divergent_bridge_before_forecast(problem):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer

    bundle, documents, plan = _case()
    if problem == 'estimate':
        item = plan['scenarios']['bear']['net_debt']
        item['kind'] = 'analyst_estimate'
        for key in ('evidence_quote', 'quoted_value', 'quoted_unit', 'period_quote'):
            item.pop(key)
    calls, callbacks = [], []
    propose = _offline(plan, calls)
    if problem == 'missing':
        def propose_missing(dossier, contract):
            answer = propose(dossier, contract)
            if contract['preparation_stage']['scope'] == 'bear':
                answer['drivers'].pop('net_debt')
            return answer
        proposer = propose_missing
    else:
        proposer = propose
    prepared = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(proposer, historical_first=True,
                               on_historical=lambda *args: callbacks.append(args)))
    assert prepared['status'] == 'incomplete'
    assert not callbacks
    assert all('capdev_amortization_years' not in names for _, names in calls)
    assert any(term in prepared['issues'][0]['reason'] for term in
               ('incomplete preparation stage', 'historical proofs', 'differs across scenarios'))


def test_historical_first_opt_in_is_explicit_and_fcff_only():
    from bellomberg.valuation.preparation_ai import StagedProposer

    with pytest.raises(ValueError, match='historical_first'):
        StagedProposer(lambda *_: None, historical_first=True)
    with pytest.raises(ValueError, match='on_historical'):
        StagedProposer(lambda *_: None, on_historical=lambda *_: None)
    proposer = StagedProposer(lambda *_: pytest.fail('provider must not run'),
                              historical_first=True, on_historical=lambda *_: None)
    with pytest.raises(ValueError, match='operating_fcff'):
        proposer({}, {'method_id': 'bank_residual_income'})


def test_historical_first_second_replay_reuses_exact_in_memory_journal_entries():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer, _json

    bundle, documents, plan = _case()
    calls, cache, checkpoints = [], {}, []
    original = _offline(plan, calls)

    def journal(dossier, contract):
        key = _json({'dossier': dossier, 'contract': contract})
        if key not in cache:
            cache[key] = original(dossier, contract)
        return deepcopy(cache[key])

    for _ in range(2):
        prepared = prepare_method_inputs(bundle, documents=documents,
            propose=StagedProposer(journal, historical_first=True,
                                   on_historical=lambda historical, *_: checkpoints.append(historical)))
        assert prepared['status'] == 'prepared', prepared['issues']
    assert len(checkpoints) == 2 and checkpoints[0] == checkpoints[1]
    assert len(calls) == len(cache)
    assert [names for scope, names in calls if scope == 'model'][:4] == [
        ('perimeter', 'calendar', 'quotation'), ('historical_revenue',),
        ('opening_nwc',), ('shares',)]


@pytest.mark.parametrize('selected', (True, False))
def test_historical_first_staged_compiler_requires_selected_sec_filing_for_inc_issuer(selected):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    from test_preparation_fresh_historical import sec_issuer_punctuation
    import json

    bundle, documents, plan = _case()
    primary, xbrl = sec_issuer_punctuation()
    body = json.loads(xbrl['text'])
    body['facts'][0]['unit'] = 'EUR'
    xbrl['text'] = json.dumps(body)
    xbrl['sha256'] = sha256(xbrl['text'].encode()).hexdigest()
    documents += [primary, xbrl]
    plan['model']['perimeter']['value']['entity'] = primary['metadata']['issuer']
    revenue = plan['model']['historical_revenue']
    for key in ('evidence_quote', 'period_quote'):
        revenue.pop(key)
    revenue.update(evidence_ids=[xbrl['id']], quoted_value=100_000_000,
                   quoted_unit='EUR', evidence_pointer={
                       'value': '/facts/0/observation/val', 'unit': '/facts/0/unit',
                       'period': '/facts/0/observation/end'})
    report = {'selection': {'selected_document_id': primary['id'],
                            'selected_document_ids': [primary['id']] if selected else []}}
    stages = []
    source = _offline(plan, stages)

    def stop_after_revenue(dossier, contract):
        if contract['preparation_stage']['drivers'] == ['opening_nwc']:
            return {'drivers': {}, 'rationale': 'Next historical source intentionally absent'}
        return source(dossier, contract)

    prepared = prepare_method_inputs(bundle, documents=documents, source_report=report,
        propose=StagedProposer(stop_after_revenue, historical_first=True,
                               on_historical=lambda *_: pytest.fail('incomplete historical plan')))
    assert prepared['status'] == 'incomplete'
    assert ('opening_nwc' if selected else 'entita SEC') in prepared['issues'][0]['reason']
    assert ('entita SEC' in prepared['issues'][0]['reason']) is not selected
    assert stages == [('model', ('perimeter', 'calendar', 'quotation')),
                      ('model', ('historical_revenue',))]


def _fake_paid_stages(plan, calls):
    def call(**wire):
        request = json.loads(wire['messages'][0]['content'])
        stage = request['contract']['preparation_stage']
        scope, names = stage['scope'], stage['drivers']
        calls.append((scope, tuple(names)))
        source = plan['model'] if scope == 'model' else plan['scenarios'][scope]
        answer = {'drivers': {name: deepcopy(source[name]) for name in names},
                  'rationale': 'Synthetic paid-response journal receipt'}
        return SimpleNamespace(id='historical-stage-' + str(len(calls)),
            model=wire['model'], provider='synthetic', stop_reason='end_turn',
            usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type='text', text=json.dumps(answer))])
    return call


def test_historical_first_real_journal_resume_recompiles_then_pays_only_forecast(tmp_path, monkeypatch):
    from bellomberg.valuation import input_preparation
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    from test_preparation_ai import _proposer

    bundle, documents, plan = _case()
    calls, checkpoints = [], []
    first_journal = _proposer(tmp_path, call=_fake_paid_stages(plan, calls))

    def crash(historical, *_):
        checkpoints.append(deepcopy(historical))
        raise RuntimeError('simulated crash after historical checkpoint')

    first = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(first_journal, historical_first=True, on_historical=crash))
    assert first['status'] == 'incomplete'
    assert 'simulated crash' in first['issues'][0]['reason']
    historical_calls = list(calls)
    assert len(historical_calls) == 5
    before = first_journal.summary()
    assert before['requests'] == len(historical_calls)
    assert before['unknown_requests'] == 0 and before['spent_usd'] == pytest.approx(.10)

    # Reopen the same disk journal. Every cached observation must still pass
    # the common compiler before a callback may admit a forecast request.
    recompiles = []
    compile_original = input_preparation._compile

    def observe_compile(candidate, *args, **kwargs):
        recompiles.append(deepcopy(candidate))
        return compile_original(candidate, *args, **kwargs)

    monkeypatch.setattr(input_preparation, '_compile', observe_compile)
    restarted = _proposer(tmp_path, call=_fake_paid_stages(plan, calls))

    def accept(historical, *_):
        checkpoints.append(deepcopy(historical))
        assert recompiles
        assert all(set(recompiles[-1]['scenarios'][scope]) == {'net_debt'}
                   for scope in ('bear', 'base', 'bull'))
        assert 'capdev_amortization_years' not in recompiles[-1]['model']
        assert calls == historical_calls  # No new provider request before forecast.

    second = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(restarted, historical_first=True, on_historical=accept))
    assert second['status'] == 'prepared', second['issues']
    assert checkpoints[0] == checkpoints[1]
    assert calls[:len(historical_calls)] == historical_calls
    assert all(stage not in historical_calls for stage in calls[len(historical_calls):])
    after = restarted.summary()
    assert after['requests'] == len(calls) and after['requests'] > before['requests']
    assert after['spent_usd'] == pytest.approx(.02 * len(calls))
    assert after['spent_usd'] <= after['authorized_usd'] and after['unknown_requests'] == 0


def test_historical_first_real_journal_source_change_is_new_and_bad_hash_stops_pre_provider(tmp_path):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    from test_preparation_ai import _proposer

    bundle, documents, plan = _case()
    calls = []
    journal = _proposer(tmp_path, call=_fake_paid_stages(plan, calls))
    stop = lambda *_: (_ for _ in ()).throw(RuntimeError('stop after historical'))
    first = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(journal, historical_first=True, on_historical=stop))
    assert first['status'] == 'incomplete' and len(calls) == 5
    initial_summary = journal.summary()

    damaged = deepcopy(documents)
    damaged[0]['text'] += ' Tampered without digest update.'
    reopened = _proposer(tmp_path, call=_fake_paid_stages(plan, calls))
    invalid = prepare_method_inputs(bundle, documents=damaged,
        propose=StagedProposer(reopened, historical_first=True, on_historical=stop))
    assert invalid['status'] == 'incomplete'
    assert any(issue['code'] == 'hash_mismatch' for issue in invalid['issues'])
    assert len(calls) == 5 and reopened.summary() == initial_summary

    changed = deepcopy(documents)
    changed[0]['text'] += ' Supplemental source narrative.'
    changed[0]['sha256'] = sha256(changed[0]['text'].encode()).hexdigest()
    changed_result = prepare_method_inputs(bundle, documents=changed,
        propose=StagedProposer(reopened, historical_first=True, on_historical=stop))
    assert changed_result['status'] == 'incomplete'
    assert 'stop after historical' in changed_result['issues'][0]['reason']
    assert len(calls) > 5 and reopened.summary()['requests'] > initial_summary['requests']
