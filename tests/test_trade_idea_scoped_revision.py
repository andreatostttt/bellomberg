"""A bounded common-model review never rewrites untouched sourced drivers."""
from copy import deepcopy
from hashlib import sha256

import pytest

from bellomberg.valuation.trade_idea_model import prepare, revise
from test_trade_idea_economic import qualified, _operating_plan


@pytest.fixture(scope='module')
def prior(tmp_path_factory):
    folder = tmp_path_factory.mktemp('scoped-model')
    sources = qualified(folder)
    payload = prepare(sources, lambda *_: deepcopy(_operating_plan()), folder / 'before')
    assert payload['valuation_usability']['usable']
    return sources, payload


def request(scopes=None):
    return {'rationale': 'Explicit synthetic discount-rate review', 'evidence_ids': ['annual-1'],
            'analysis_context': {'review_scope': {'base': ['wacc']} if scopes is None else scopes}}


def test_scoped_review_requests_only_selected_driver_and_freezes_the_rest(prior, tmp_path):
    sources, before = prior
    unchanged = deepcopy(before)
    old_bytes = before['workbook_sha256']
    calls = []
    def reviewer(dossier, contract):
        stage = contract.get('preparation_stage')
        assert stage and stage['scope'] == 'base' and stage['drivers'] == ['wacc']
        assert set(contract['schema']) == {'wacc'}
        assert 'before_plan' not in dossier['preparation_context']
        assert dossier['completed_plan']['model'] == before['preparation']['proposal']['plan']['model']
        calls.append(deepcopy(stage))
        item = deepcopy(before['preparation']['proposal']['plan']['scenarios']['base']['wacc'])
        item.update(value=.12, rationale='Explicit synthetic higher discount rate')
        return {'drivers': {'wacc': item}, 'rationale': 'Retain the operating path; revise the discount rate.'}
    after = revise(before, request(), qualification=sources, propose=reviewer, output_dir=tmp_path / 'after')
    assert after['preparation']['status'] == 'prepared', after.get('error')
    assert after['input_consumption']['status'] == 'complete'
    plan = after['preparation']['proposal']['plan']
    original = before['preparation']['proposal']['plan']
    assert plan['model'] == original['model']
    for scope, values in original['scenarios'].items():
        for name, item in values.items():
            if (scope, name) != ('base', 'wacc'):
                assert plan['scenarios'][scope][name] == item
    assert plan['scenarios']['base']['wacc']['value'] == .12
    assert len(calls) == 1
    assert before == unchanged
    assert after['generation_id'] != before['generation_id']
    from pathlib import Path
    assert sha256(Path(before['path']).read_bytes()).hexdigest() == old_bytes
    lineage = after['preparation']['provenance']['trade_idea_revision']['scoped_review']
    assert lineage['human_approved'] is False
    assert lineage['requested_drivers'] == {'base': ['wacc']}
    assert len(lineage['responses']) == 1


@pytest.mark.parametrize('scope', [None, {}, {'model': ['shares']}, {'other': ['wacc']},
    {'base': []}, {'base': ['unknown']}, {'base': ['wacc', 'wacc']}, {'base': ['net_debt']}])
def test_invalid_or_factual_scope_is_rejected_before_provider(prior, tmp_path, scope):
    sources, before = prior
    changes = request()
    changes['analysis_context']['review_scope'] = scope
    calls = []
    with pytest.raises(ValueError, match='scoped|scope|historical|estimate'):
        revise(before, changes, qualification=sources,
               propose=lambda *_: calls.append('forbidden'), output_dir=tmp_path / 'forbidden')
    assert calls == []
    assert not (tmp_path / 'forbidden').exists()


def test_scoped_previous_generation_is_immutable_evidence_not_current_output(prior, tmp_path):
    from bellomberg.valuation.dcf_quality import normalize_valuation_payload
    sources, before = prior
    captured = {}
    def reviewer(dossier, _contract):
        captured.update(deepcopy(dossier['preparation_context']['before']))
        return {'drivers': {'wacc': deepcopy(before['preparation']['proposal']['plan']['scenarios']['base']['wacc'])},
                'rationale': 'Confirm the existing source-bound discount rate without changing any numeric judgment.'}
    after = revise(before, request(), qualification=sources, propose=reviewer, output_dir=tmp_path / 'after')
    assert after['valuation_usability']['usable'], after['valuation_usability']['reasons']
    history = after['preparation']['provenance']['trade_idea_revision']['scoped_review']['context']['before']
    assert history == captured
    assert history['generation_id'] == before['generation_id'] != after['generation_id']
    assert history['fair_value_base'] == before['fair_value_base']
    invalid = deepcopy(after)
    invalid['calculation_details']['generation_id'] = before['generation_id']
    blocked = normalize_valuation_payload(invalid)
    assert not blocked['valuation_usability']['usable']
    assert blocked['fair_value_base'] is None
    assert blocked['preparation']['provenance']['trade_idea_revision']['scoped_review']['context']['before'] == captured
    assert any('calculation_details.generation_id' in reason for reason in blocked['valuation_usability']['reasons'])


def test_scoped_output_cannot_overwrite_previous_workbook(prior):
    from pathlib import Path
    sources, before = prior
    original = Path(before['path']).read_bytes()
    calls = []
    with pytest.raises(ValueError, match='directory.*previous workbook'):
        revise(before, request(), qualification=sources, propose=lambda *_: calls.append('forbidden'),
               output_dir=Path(before['path']).parent)
    assert calls == []
    assert Path(before['path']).read_bytes() == original


def test_manual_revision_with_stale_seed_requires_fresh_preparation(prior, tmp_path):
    sources, before = prior
    records = deepcopy(before['acquisition_snapshot']['case']['records'])
    next(row for row in records if (row['scenario'], row['driver']) == ('base', 'wacc'))['value'] = .12
    manual = revise(before, {**request(), 'method_records': records, 'analysis_context': {}},
                    output_dir=tmp_path / 'manual')
    calls = []
    with pytest.raises(ValueError, match='previous model records'):
        revise(manual, request({'bull': ['wacc']}), qualification=sources,
               propose=lambda *_: calls.append('forbidden'), output_dir=tmp_path / 'scoped')
    assert calls == []


def test_operating_scope_requires_atomic_terminal_before_provider(prior, tmp_path):
    sources, before = prior
    calls = []
    with pytest.raises(ValueError, match='terminal_bridge.*atomic'):
        revise(before, request({'base': ['gross_margin']}), qualification=sources,
               propose=lambda *_: calls.append('forbidden'), output_dir=tmp_path / 'after')
    assert calls == []


def test_operating_and_terminal_review_are_merged_atomically(prior, tmp_path):
    sources, before = prior
    calls = []
    def reviewer(_dossier, contract):
        stage = contract['preparation_stage']
        calls.append(stage['drivers'])
        values = deepcopy(before['preparation']['proposal']['plan']['scenarios']['base'])
        values['gross_margin']['value'] = [value + .01 for value in values['gross_margin']['value']]
        revenue = before['preparation']['proposal']['plan']['model']['historical_revenue']['value']
        for growth in values['revenue_growth']['value']:
            revenue *= 1 + growth
        values['terminal_bridge']['value']['normalized_ebit'] += revenue * .01
        return {'drivers': {name: values[name] for name in stage['drivers']},
                'rationale': 'Explicit synthetic margin review and matching terminal; retain all other judgments.'}
    after = revise(before, request({'base': ['gross_margin', 'terminal_bridge']}),
                   qualification=sources, propose=reviewer, output_dir=tmp_path / 'after')
    assert after['preparation']['status'] == 'prepared'
    assert calls == [['gross_margin', 'terminal_bridge']]
    plan = after['preparation']['proposal']['plan']
    assert plan['scenarios']['base']['gross_margin']['value'][0] == pytest.approx(.41)
    assert plan['scenarios']['bear'] == before['preparation']['proposal']['plan']['scenarios']['bear']


@pytest.fixture(scope='module')
def extreme_discount_model(tmp_path_factory):
    from test_trade_idea_historical_admission import preparation_case
    folder = tmp_path_factory.mktemp('extreme-scoped')
    sources, full = preparation_case(folder)
    # Explicit PM policy 2026-10-02: price divergence alone never blocks.
    # Keep the extreme synthetic WACC unchanged throughout both reviews.
    for values in full['scenarios'].values():
        values['wacc']['value'] = .9
    def staged(_dossier, contract):
        stage = contract['preparation_stage']
        values = full['model'] if stage['scope'] == 'model' else full['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                'rationale': 'Synthetic extreme discounting; accounting and immutable opening facts retained.'}
    payload = prepare(sources, staged, folder / 'before')
    assert payload['preparation']['status'] == 'prepared'
    assert payload['input_consumption']['status'] == 'complete'
    assert payload['valuation_usability']['usable'] is True
    return sources, payload


def test_two_scoped_reviews_recompile_extreme_model_without_rebuying_history(extreme_discount_model, tmp_path):
    sources, before = extreme_discount_model
    frozen = deepcopy(before)
    calls = []
    current = before
    for number, scope in enumerate(('base', 'bull')):
        def reviewer(_dossier, contract):
            stage = contract['preparation_stage']
            calls.append((stage['scope'], stage['drivers']))
            assert stage['scope'] == scope and stage['drivers'] == ['wacc']
            return {'drivers': {'wacc': deepcopy(current['preparation']['proposal']['plan']['scenarios'][scope]['wacc'])},
                    'rationale': 'Synthetic review explicitly retains discounting; price divergence is informational.'}
        after = revise(current, request({scope: ['wacc']}), qualification=sources,
                       propose=reviewer, output_dir=tmp_path / ('after-' + str(number)))
        assert after['preparation']['status'] == 'prepared'
        assert after['valuation_usability']['usable'] is True
        assert after['historical_preparation'] == before['historical_preparation']
        current = after
    assert calls == [('base', ['wacc']), ('bull', ['wacc'])]
    assert before == frozen


def test_ignored_requested_value_stops_before_next_scope(extreme_discount_model, tmp_path):
    sources, before = extreme_discount_model
    changes = request({'base': ['wacc'], 'bull': ['wacc']})
    row = deepcopy(next(row for row in before['acquisition_snapshot']['case']['records']
                       if (row['scenario'], row['driver']) == ('base', 'wacc')))
    row['value'] = .91
    changes['method_records'] = [row]
    calls = []
    def reviewer(_dossier, contract):
        scope = contract['preparation_stage']['scope']
        calls.append(scope)
        return {'drivers': {'wacc': deepcopy(before['preparation']['proposal']['plan']['scenarios'][scope]['wacc'])},
                'rationale': 'Deliberately ignore an explicitly requested synthetic value.'}
    with pytest.raises(ValueError, match='ignored.*requested value'):
        revise(before, changes, qualification=sources, propose=reviewer, output_dir=tmp_path / 'after')
    assert calls == ['base']
    assert not (tmp_path / 'after').exists()


@pytest.mark.parametrize('fault', ['truncated', 'missing', 'extra', 'bad_citation', 'guidance'])
def test_invalid_response_stops_without_replacing_original(prior, tmp_path, fault):
    sources, before = prior
    original = deepcopy(before)
    calls = []
    def reviewer(_dossier, _contract):
        calls.append('synthetic response')
        item = deepcopy(before['preparation']['proposal']['plan']['scenarios']['base']['wacc'])
        answer = {'drivers': {'wacc': item}, 'rationale': 'Synthetic reviewed rate.'}
        if fault == 'truncated':
            raise ValueError('incomplete AI response: max_tokens')
        if fault == 'missing': answer['drivers'] = {}
        if fault == 'extra': answer['drivers']['net_debt'] = {'value': 123}
        if fault == 'bad_citation': item['evidence_ids'] = ['unseen-source']
        if fault == 'guidance': item['kind'] = 'company_guidance'
        return answer
    with pytest.raises(ValueError):
        revise(before, request(), qualification=sources, propose=reviewer, output_dir=tmp_path / 'after')
    assert len(calls) == 1
    assert before == original
    assert not (tmp_path / 'after').exists()


def test_invalid_terminal_merge_stops_before_next_scenario(prior, tmp_path):
    sources, before = prior
    calls = []
    def reviewer(_dossier, contract):
        stage = contract['preparation_stage']
        calls.append(stage['scope'])
        item = deepcopy(before['preparation']['proposal']['plan']['scenarios']['base']['terminal_bridge'])
        item['value']['normalized_ebit'] += 1
        return {'drivers': {'terminal_bridge': item}, 'rationale': 'Deliberately incoherent terminal.'}
    with pytest.raises(ValueError, match='FCFF forecast arithmetic'):
        revise(before, request({'base': ['terminal_bridge'], 'bull': ['wacc']}),
               qualification=sources, propose=reviewer, output_dir=tmp_path / 'after')
    assert calls == ['base']
    assert not (tmp_path / 'after').exists()


def test_tampered_prior_seed_is_rejected_before_provider(prior, tmp_path):
    sources, saved = prior
    before = deepcopy(saved)
    before['preparation']['review_basis']['seed']['plan']['model']['shares']['value'] += 1
    calls = []
    with pytest.raises(ValueError):
        revise(before, request(), qualification=sources,
               propose=lambda *_: calls.append('forbidden'), output_dir=tmp_path / 'after')
    assert calls == []


def test_changed_sources_require_source_refresh_instead_of_scoped_reuse(prior, tmp_path):
    from bellomberg.valuation.trade_idea_model import source_fingerprint
    sources, before = prior
    sources = deepcopy(sources)
    doc = sources['source_report']['documents'][0]
    doc['text'] += '\nAdditional synthetic publication.'
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    sources['fingerprint'] = source_fingerprint(sources)
    calls = []
    with pytest.raises(ValueError, match='source|Source|fonti|Fonti'):
        revise(before, request(), qualification=sources,
               propose=lambda *_: calls.append('forbidden'), output_dir=tmp_path / 'after')
    assert calls == []


def test_citations_must_be_visible_after_provider_projection(prior, tmp_path):
    sources, before = prior
    calls = []
    class Reviewer:
        def prepare_context(self, view, _contract, **_kwargs):
            view['documents'] = [doc for doc in view['documents'] if doc['id'] != 'annual-1']
            return view
        def __call__(self, _view, _contract):
            calls.append('one synthetic response')
            return {'drivers': {'wacc': deepcopy(before['preparation']['proposal']['plan']['scenarios']['base']['wacc'])},
                    'rationale': 'Deliberately cite a source omitted from the actual request.'}
    with pytest.raises(ValueError):
        revise(before, request(), qualification=sources, propose=Reviewer(), output_dir=tmp_path / 'after')
    assert calls == ['one synthetic response']
    assert not (tmp_path / 'after').exists()


def test_qualified_native_checkpoint_and_paid_replay_stay_intact(tmp_path):
    import json
    import sqlite3
    from types import SimpleNamespace
    from test_preparation_ai import _proposer
    from test_preparation_growth_thesis import _thesis
    sources, plan = qualified(tmp_path), _operating_plan()
    calls = []
    def synthetic_provider(**wire):
        contract = json.loads(wire['messages'][0]['content'])['contract']
        stage = contract['preparation_stage']
        calls.append((stage['scope'], tuple(stage['drivers'])))
        if stage.get('purpose') == 'growth_thesis':
            answer = _thesis(plan, contract)
            for scope, scenario in answer['growth_thesis']['scenarios'].items():
                for name, link in scenario['links'].items():
                    link['evidence_ids'] = deepcopy(plan['scenarios'][scope][name]['evidence_ids'])
        else:
            values = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
            answer = {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                      'rationale': 'Explicit fictional provider response; no network or production ledger.'}
        return SimpleNamespace(id='synthetic-' + str(len(calls)), model=wire['model'],
            provider='synthetic', stop_reason='end_turn', usage=SimpleNamespace(cost_usd=.01),
            content=[SimpleNamespace(type='text', text=json.dumps(answer))])
    proposer = _proposer(tmp_path, limit=1000, call=synthetic_provider)
    proposer.metadata = lambda _: {'id': 'synthetic/model', 'context_length': 1000000,
        'pricing': {'prompt': '0.00001', 'completion': '0.00002'}}
    before = prepare(sources, proposer, tmp_path / 'before')
    assert before['preparation']['status'] == 'prepared'
    assert before['historical_preparation']['status'] == 'historical_qualified'
    def rows():
        with sqlite3.connect(proposer.path.as_uri() + '?mode=ro', uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            return dict(db.execute('SELECT key, response FROM requests'))
    original_rows, original_calls = rows(), len(calls)
    after = revise(before, request(), qualification=sources, propose=proposer, output_dir=tmp_path / 'after')
    assert calls[original_calls:] == [('base', ('wacc',))]
    assert after['historical_preparation'] == before['historical_preparation']
    assert after['preparation']['growth_thesis_status'] == 'superseded_by_explicit_model_revision'
    assert {key: rows()[key] for key in original_rows} == original_rows
    recorded = rows()
    proposer.call = lambda **_: pytest.fail('exact scoped response must replay without another provider')
    proposer.metadata = lambda _: pytest.fail('exact scoped response must replay without pricing lookup')
    replay = revise(before, request(), qualification=sources, propose=proposer, output_dir=tmp_path / 'replay')
    assert replay['preparation']['proposal']['plan'] == after['preparation']['proposal']['plan']
    assert replay['preparation']['provenance']['trade_idea_revision']['scoped_review'] == after['preparation']['provenance']['trade_idea_revision']['scoped_review']
    assert rows() == recorded
