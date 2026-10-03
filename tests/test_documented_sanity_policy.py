"""All-scenario informational price distance preserves strict FCFF attestation."""
from copy import deepcopy

import pytest
from openpyxl import load_workbook

from bellomberg.core.language import language_context
from bellomberg.valuation import dcf_engine, dcf_quality, documented_inputs
from test_sector_operating_drivers import bundle_for, operating_records


POLICY = 'documented_operating_base_v1'


def stress_bundle(*, symbol='SYNTH-STRESS', profile='software', base_block=False, factor=1.):
    records = operating_records()
    for row in records:
        if row['driver'] == 'quotation':
            row['value']['price'] = (40. if base_block else 25.) * factor
            row['value']['shares_per_quote'] = factor
        if row['driver'] == 'equity_adjustments':
            row['value'] = {'bear': -110., 'base': 0., 'bull': 400.}[row['scenario']]
    return bundle_for(records, symbol=symbol, profile=profile)


@pytest.fixture(scope='module')
def stress_payload(tmp_path_factory):
    return dcf_engine.generate_valuation('SYNTH-STRESS', prepared_bundle=stress_bundle(),
                                        output_dir=str(tmp_path_factory.mktemp('sanity-stress')))


def assert_blocked(payload):
    result = dcf_quality.normalize_valuation_payload(payload)
    assert not result['valuation_usability']['usable'], result['valuation_usability']
    assert result['fair_value_base'] is None


@pytest.mark.parametrize('profile,symbol', [('software', 'SYNTH-STRESS'),
                                           ('manufacturing', 'OUTSIDE-POLICY')])
def test_all_scenarios_keep_divergence_informational_for_documented_operating(tmp_path, profile, symbol):
    result = dcf_engine.generate_valuation(symbol, prepared_bundle=stress_bundle(symbol=symbol, profile=profile),
                                         output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'], result['valuation_usability']
    assert result['fair_value_base'] == pytest.approx(14.13)
    sanity = result['sanity']
    assert sanity['policy'] == POLICY
    assert sanity['severity'] == 'OK' and sanity['exclude_from_action_table'] is False
    assert sanity['ratio'] == .57 and sanity['upside_pct'] == -43.5
    assert 'divergence_informational' not in sanity['scenario_checks']['base']
    for scenario in ('bear', 'bull'):
        check = sanity['scenario_checks'][scenario]
        assert check['severity'] == 'OK' and check['exclude_from_action_table'] is False
        assert check['divergence_informational'] is True
        assert check['reading'] and check['headline'] is None


def test_large_base_divergence_does_not_block_documented_model(tmp_path):
    result = dcf_engine.generate_valuation('SYNTH-STRESS', prepared_bundle=stress_bundle(base_block=True),
                                         output_dir=str(tmp_path))
    assert result['sanity']['severity'] == 'OK'
    assert result['sanity']['exclude_from_action_table'] is False
    assert result['valuation_usability']['usable'], result['valuation_usability']
    assert result['fair_value_base'] == pytest.approx(14.13)


@pytest.mark.parametrize('language', ['it', 'en'])
def test_workbook_explains_all_scenarios_distance_without_exclusion(tmp_path, language):
    with language_context(language):
        result = dcf_engine.generate_valuation('SYNTH-STRESS', prepared_bundle=stress_bundle(),
                                             output_dir=str(tmp_path))
    wb = load_workbook(result['path'], data_only=False)
    text = '\n'.join(str(c.value) for row in wb['Summary'] for c in row if c.value is not None)
    assert ('Bear, base e bull' if language == 'it' else 'Bear, base and bull') in text
    assert ('informativo' if language == 'it' else 'informational') in text
    assert ('dati, ipotesi e calcoli' if language == 'it' else 'Data, assumption and calculation') in text
    assert 'VAL SOSPETTA' not in text and 'SUSPECT VALUATION' not in text
    assert any(c.data_type == 'f' for row in wb['base'] for c in row)
    wb.close()


@pytest.mark.parametrize('policy', [None, 'unknown-policy'])
def test_absent_or_unknown_policy_cannot_bypass_numeric_attestation(stress_payload, policy):
    candidate = deepcopy(stress_payload)
    if policy is None:
        candidate['sanity'].pop('policy', None)
    else:
        candidate['sanity']['policy'] = policy
    assert_blocked(candidate)


@pytest.mark.parametrize('marker', [False, None, 'true', 1])
def test_informational_marker_must_be_boolean_true(stress_payload, marker):
    candidate = deepcopy(stress_payload)
    candidate['sanity']['scenario_checks']['bear']['divergence_informational'] = marker
    assert_blocked(candidate)


@pytest.mark.parametrize('scenario', ['bear', 'base', 'bull'])
@pytest.mark.parametrize('bad', [None, True, '14.13', float('nan'), float('inf'), 0., -1., .004])
def test_invalid_raw_scenario_fv_cannot_be_promoted_by_policy(stress_payload, scenario, bad):
    candidate = deepcopy(stress_payload)
    candidate['calculation_details']['scenarios'][scenario]['fair_value_per_share'] = bad
    assert_blocked(candidate)


@pytest.mark.parametrize('scenario', ['bear', 'base', 'bull'])
@pytest.mark.parametrize('bad', [None, True, '14.13', float('nan'), float('inf'), 0., -1., 999.])
def test_invalid_or_unreconciled_quoted_scenario_fv_blocks(stress_payload, scenario, bad):
    candidate = deepcopy(stress_payload)
    candidate['fair_value_' + scenario] = bad
    assert_blocked(candidate)


@pytest.mark.parametrize('scenario', ['bear', 'base', 'bull'])
@pytest.mark.parametrize('bad', [None, True, float('nan'), float('inf'), 0., -1., 123456.])
def test_original_engine_bridge_fv_must_be_positive_and_match_scenario(stress_payload, scenario, bad):
    candidate = deepcopy(stress_payload)
    candidate['calculation_details']['scenarios'][scenario]['valuation_bridge']['fair_value_per_share'] = bad
    assert_blocked(candidate)


@pytest.mark.parametrize('fault', ['missing_bridge', 'missing_bridge_fv', 'malformed_bridge'])
def test_original_engine_bridge_fv_cannot_be_omitted(stress_payload, fault):
    candidate = deepcopy(stress_payload)
    calculated = candidate['calculation_details']['scenarios']['bear']
    if fault == 'missing_bridge': calculated.pop('valuation_bridge')
    elif fault == 'missing_bridge_fv': calculated['valuation_bridge'].pop('fair_value_per_share')
    else: calculated['valuation_bridge'] = []
    assert_blocked(candidate)


@pytest.mark.parametrize('fault', ['missing_scenario', 'extra_scenario', 'malformed_scenario',
    'missing_checks', 'extra_check', 'base_marker', 'record_adapter', 'method', 'price',
    'financial_currency', 'quote_unit', 'quote_factor', 'overall_severity', 'overall_ratio',
    'overall_upside', 'overall_exclude', 'upside', 'stress_status', 'stress_ratio',
    'stress_upside', 'stress_severity', 'stress_exclude'])
def test_policy_requires_original_numeric_structural_and_quotation_attestation(stress_payload, fault):
    candidate = deepcopy(stress_payload)
    sanity = candidate['sanity']; checks = sanity['scenario_checks']
    scenarios = candidate['calculation_details']['scenarios']
    if fault == 'missing_scenario': scenarios.pop('bear')
    elif fault == 'extra_scenario': scenarios['fourth'] = deepcopy(scenarios['base'])
    elif fault == 'malformed_scenario': scenarios['bear'] = []
    elif fault == 'missing_checks': checks.pop('bear')
    elif fault == 'extra_check': checks['fourth'] = deepcopy(checks['base'])
    elif fault == 'base_marker': checks['base']['divergence_informational'] = True
    elif fault == 'record_adapter': candidate['analytical_quality']['record_adapter'] = 1
    elif fault == 'method': candidate['method'] = 'fund_nav'
    elif fault == 'price': candidate['price'] = 10.
    elif fault == 'financial_currency': candidate['financial_currency'] = 'USD'
    elif fault == 'quote_unit': candidate['currency'] = 'GBX'
    elif fault == 'quote_factor':
        record = next(r for r in candidate['acquisition_snapshot']['case']['records'] if r['driver'] == 'quotation')
        record['value']['shares_per_quote'] = 2.
    elif fault.startswith('overall_'):
        key = fault.removeprefix('overall_')
        sanity[key if key != 'upside' else 'upside_pct'] = {'severity':'WARN', 'ratio':True, 'upside':0., 'exclude':True}[key]
        if key == 'exclude': sanity['exclude_from_action_table'] = sanity.pop('exclude')
    elif fault == 'upside': candidate['upside_pct'] = 0.
    else:
        key = fault.removeprefix('stress_')
        checks['bear'][key if key not in ('upside', 'exclude') else {'upside':'upside_pct', 'exclude':'exclude_from_action_table'}[key]] = {
            'status':'n/d', 'ratio':True, 'upside':0., 'severity':'WARN', 'exclude':1}[key]
    assert_blocked(candidate)


@pytest.mark.parametrize('fault', ['valuation_flagged', 'usable', 'flagged', 'status', 'generation_id',
                                  'nested_exclude', 'nested_generation', 'other_path_exclude', 'stale', 'source'])
def test_stress_exception_does_not_skip_other_flags_children_or_provenance(stress_payload, fault):
    candidate = deepcopy(stress_payload)
    stress = candidate['sanity']['scenario_checks']['bear']
    if fault == 'valuation_flagged': stress['valuation_flagged'] = True
    elif fault == 'usable': stress['usable'] = False
    elif fault == 'flagged': stress['flagged'] = True
    elif fault == 'status': stress['status'] = 'KO'
    elif fault == 'generation_id': stress['generation_id'] = 'other-generation'
    elif fault == 'nested_exclude': stress['detail'] = {'exclude_from_action_table': True}
    elif fault == 'nested_generation': stress['detail'] = {'generation_id': 'other-generation'}
    elif fault == 'other_path_exclude': candidate['stress_copy'] = {**deepcopy(stress), 'exclude_from_action_table': True}
    elif fault == 'stale': candidate['acquisition_snapshot']['case']['records'][0]['valid_until'] = '2020-01-01'
    elif fault == 'source': candidate['input_consumption']['consumed_records'][0]['source_id'] = 'https://other.example/input'
    assert_blocked(candidate)


@pytest.mark.parametrize('alias', ['root_dotted', 'sanity_dotted', 'shared_root_dotted'])
def test_dotted_key_alias_cannot_impersonate_exact_stress_node(stress_payload, alias):
    candidate = deepcopy(stress_payload)
    stress = candidate['sanity']['scenario_checks']['bear']
    if alias == 'sanity_dotted':
        candidate['sanity']['scenario_checks.bear'] = {'exclude_from_action_table': True}
    else:
        if alias == 'shared_root_dotted':
            stress['exclude_from_action_table'] = True
        candidate['sanity.scenario_checks.bear'] = stress if alias == 'shared_root_dotted' else {
            'exclude_from_action_table': True}
    assert_blocked(candidate)


def test_localized_explanations_do_not_control_attestation(stress_payload):
    candidate = deepcopy(stress_payload)
    for check in [candidate['sanity'], *candidate['sanity']['scenario_checks'].values()]:
        check['headline'] = 'Descrizione tradotta / translated explanation'
        check['reading'] = 'Testo libero / free text'
    candidate['valuation_usability'] = {'usable': True, 'reasons': [], 'missing_fields': []}
    assert dcf_quality.assess_valuation_usability(candidate)['usable']


def test_quotation_conversion_is_attested_before_rounding(tmp_path):
    result = dcf_engine.generate_valuation('SYNTH-STRESS', prepared_bundle=stress_bundle(factor=10000.),
                                         output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'], result['valuation_usability']
    assert result['fair_value_base'] == pytest.approx(141322.31)
    assert result['sanity']['ratio'] == .57


@pytest.mark.parametrize('scenario', ['bear', 'base', 'bull'])
@pytest.mark.parametrize('bad', [None, True, 0., -1., .004])
def test_finish_documented_keeps_invalid_scenario_globally_blocking(tmp_path, stress_payload, scenario, bad):
    from bellomberg.valuation.operating_adapter import SCHEMA
    source = stress_bundle()
    bound = documented_inputs.bind_inputs(source, SCHEMA)
    scenarios = deepcopy(stress_payload['calculation_details']['scenarios'])
    scenarios[scenario]['fair_value_per_share'] = bad
    metadata = {k: deepcopy(stress_payload[k]) for k in ('valuation_decision', 'snapshot_id', 'generation_id',
                                                        'acquisition_snapshot')}
    result = documented_inputs.finish_documented(source, bound, scenarios, metadata=metadata,
                                                 output_dir=str(tmp_path), engine='operating')
    assert result['sanity']['severity'] == 'BLOCK'
    assert result['sanity']['exclude_from_action_table'] is True
    assert_blocked(result)


def test_finish_documented_keeps_incoherent_original_bridge_globally_blocking(tmp_path, stress_payload):
    from bellomberg.valuation.operating_adapter import SCHEMA
    source = stress_bundle()
    bound = documented_inputs.bind_inputs(source, SCHEMA)
    scenarios = deepcopy(stress_payload['calculation_details']['scenarios'])
    scenarios['bear']['valuation_bridge']['fair_value_per_share'] = None
    metadata = {k: deepcopy(stress_payload[k]) for k in ('valuation_decision', 'snapshot_id', 'generation_id',
                                                        'acquisition_snapshot')}
    result = documented_inputs.finish_documented(source, bound, scenarios, metadata=metadata,
                                                 output_dir=str(tmp_path), engine='operating')
    assert result['sanity']['severity'] == 'BLOCK'
    assert result['sanity']['exclude_from_action_table'] is True
    assert_blocked(result)
