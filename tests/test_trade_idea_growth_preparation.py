"""The application preparer requires and retains Growth before forecasts; no network."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bellomberg.valuation.trade_idea_model import prepare
from test_preparation_ai import _proposer
from test_preparation_growth_thesis import _thesis
from test_trade_idea_economic import qualified, _operating_plan
from test_trade_idea_historical_admission import preparation_case


@pytest.mark.parametrize('pending,missing', [(True, False), (False, False), (True, True)])
def test_budgeted_trade_idea_preparer_requires_growth_and_persists_it(tmp_path, pending, missing):
    q, plan = preparation_case(tmp_path) if pending else (qualified(tmp_path), _operating_plan())
    calls = []
    def call(**wire):
        contract = json.loads(wire['messages'][0]['content'])['contract']
        stage = contract['preparation_stage']
        if stage.get('purpose') == 'growth_thesis':
            calls.append('thesis')
            if pending:
                checkpoint = json.loads((tmp_path/'model'/'historical-preparation.json').read_text())
                assert checkpoint['status'] == 'historical_qualified'
            answer = {'growth_thesis': None} if missing else _thesis(plan, contract)
            if not missing:
                for scope, scenario in answer['growth_thesis']['scenarios'].items():
                    for name, link in scenario['links'].items():
                        link['evidence_ids'] = deepcopy(plan['scenarios'][scope][name]['evidence_ids'])
        else:
            calls.append((stage['scope'], tuple(stage['drivers'])))
            values = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
            answer = {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                      'rationale': 'Synthetic economic preparation through the application entrypoint'}
        return SimpleNamespace(id='synthetic-' + str(len(calls)), model=wire['model'],
            provider='synthetic', stop_reason='end_turn', usage=SimpleNamespace(cost_usd=.01),
            content=[SimpleNamespace(type='text', text=json.dumps(answer))])
    proposer = _proposer(tmp_path, limit=1000, call=call)
    proposer.metadata = lambda _: {'id': 'synthetic/model', 'context_length': 1000000,
        'pricing': {'prompt': '0.00001', 'completion': '0.00002'}}
    payload = prepare(q, proposer, tmp_path/'model')
    assert calls.count('thesis') == 1
    if missing:
        assert payload['valuation_usability']['usable'] is False
        assert not payload.get('path')
        assert ('model', ('capdev_amortization_years',)) not in calls
        return
    assert payload['valuation_usability']['usable'], payload.get('error')
    if not pending:
        checkpoint = json.loads((tmp_path/'model'/'historical-preparation.json').read_text())
        assert 'equity_adjustments' in checkpoint['source_basis']['historical_drivers']
        assert all('equity_adjustments' not in scenario
            for scenario in checkpoint['candidate']['plan']['scenarios'].values())
    assert ('model', ('capdev_amortization_years',)) not in calls
    assert calls.index('thesis') < next(i for i, entry in enumerate(calls)
        if isinstance(entry, tuple) and entry[0] in ('bear', 'base', 'bull')
        and entry[1] != ('net_debt',))
    thesis = payload['preparation']['growth_thesis']
    capdev = next(row for row in payload['acquisition_snapshot']['case']['records']
        if row['scenario'] == 'model' and row['driver'] == 'capdev_amortization_years')
    assert capdev['value'] == thesis['model_anchors']['capdev_amortization_years']['value']
    assert thesis['scenarios']['base']['anchors']['gross_margin']['value'] == plan['scenarios']['base']['gross_margin']['value']
    persisted = json.loads(Path(payload['path']).with_suffix('.payload.json').read_text(encoding='utf-8'))
    assert persisted['preparation']['growth_thesis'] == thesis
    assert payload['preparation']['growth_thesis_source_fingerprint'] == q['fingerprint']
    assert proposer.summary()['requests'] == len(calls)
    if pending:
        from bellomberg.agents.trade_idea import candidate_model_context
        from bellomberg.valuation.trade_idea_model import revise
        board = SimpleNamespace(target_ticker=q['ticker'], valuation_results={q['ticker']: payload},
            source_qualification=q, data={}, tool_receipts=[])
        context = candidate_model_context(board)
        assert context['growth_thesis'] == thesis
        records = deepcopy(payload['acquisition_snapshot']['case']['records'])
        changed = next(row for row in records if row['driver'] == 'gross_margin' and row['scenario'] == 'base')
        changed['value'][0] += .01
        changed['rationale'] = 'Explicit desk revision after the initial Growth assumptions'
        revised = revise(payload, dict(method_records=records, evidence_ids=['annual-1'],
            rationale=changed['rationale']), qualification=q, output_dir=tmp_path/'revised')
        assert revised['valuation_usability']['usable'], (revised.get('error'), revised['valuation_usability'])
        assert 'growth_thesis' not in revised['preparation']
        assert revised['preparation']['growth_thesis_history'][-1]['thesis'] == thesis
        assert revised['preparation']['growth_thesis_status'] == 'superseded_by_explicit_model_revision'
        board.valuation_results[q['ticker']] = revised
        assert candidate_model_context(board)['growth_thesis'] is None
