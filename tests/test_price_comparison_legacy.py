"""A saved price-distance warning must not become a new technical failure."""
from copy import deepcopy
import json

import pytest

from bellomberg.valuation import dcf_engine, dcf_quality
from test_documented_sanity_policy import stress_bundle


def old_check(value, price):
    """Frozen historical writer contract, used only to construct saved input."""
    ratio = value / price
    distance = abs(ratio - 1)
    severity = 'BLOCK' if distance > .5 else 'WARN' if distance > .4 else 'OK'
    headline = None
    if severity == 'BLOCK':
        headline = ('VAL SOSPETTA: fair value %.0f diverge %.0f%% dal prezzo %.0f (ratio %.2f). '
                    'Rivedere growth/margini/WACC/shares/net_debt PRIMA di fidarsi. '
                    'ESCLUSO dalla ACTION TABLE.') % (value, distance * 100, price, ratio)
    elif severity == 'WARN':
        headline = ('VAL da verificare: fair value %.0f diverge %.0f%% dal prezzo %.0f '
                    '(ratio %.2f). Trattare con cautela.') % (value, distance * 100, price, ratio)
    return {'status': 'ok', 'ratio': round(ratio, 2), 'upside_pct': round((ratio - 1) * 100, 1),
            'severity': severity, 'exclude_from_action_table': severity == 'BLOCK',
            'headline': headline, 'reading': 'historical price comparison'}


@pytest.fixture
def historical_payload(tmp_path):
    result = dcf_engine.generate_valuation('SYNTH-LEGACY',
        prepared_bundle=stress_bundle(symbol='SYNTH-LEGACY', base_block=True), output_dir=str(tmp_path))
    assert result['fair_value_base'] == 14.13
    checks = {s: old_check(result['fair_value_' + s], result['price']) for s in ('bear', 'base', 'bull')}
    for s in ('bear', 'bull'):
        checks[s]['divergence_informational'] = True
    result['sanity'] = {**checks['base'], 'method_id': result['method'],
        'policy': 'documented_operating_base_v1', 'scenario_checks': checks}
    return result


def test_saved_distance_flags_are_reinterpreted_without_mutating_artifact(historical_payload):
    before = json.dumps(historical_payload, sort_keys=True)
    assert historical_payload['sanity']['severity'] == 'BLOCK'
    assert dcf_quality.assess_valuation_usability(historical_payload)['usable'] is True
    current = dcf_quality.normalize_valuation_payload(historical_payload)
    assert current['valuation_usability']['usable'] is True
    assert current['fair_value_base'] == 14.13
    assert current['sanity']['severity'] == 'OK'
    assert all(c['severity'] == 'OK' and c['exclude_from_action_table'] is False
               for c in current['sanity']['scenario_checks'].values())
    assert dcf_quality.normalize_valuation_payload(current) == current
    assert json.dumps(historical_payload, sort_keys=True) == before


@pytest.mark.parametrize('fault', ['ratio', 'quote', 'bridge', 'source', 'technical_flag', 'technical_error', 'headline'])
def test_legacy_view_never_clears_real_or_unexplained_failures(historical_payload, fault):
    candidate = deepcopy(historical_payload)
    if fault == 'ratio': candidate['sanity']['scenario_checks']['bull']['ratio'] += .1
    elif fault == 'quote': candidate['price'] *= 2
    elif fault == 'bridge': candidate['calculation_details']['scenarios']['base']['fair_value_per_share'] *= 2
    elif fault == 'source': candidate['analytical_quality']['issues'] = ['missing source']
    elif fault == 'technical_flag': candidate['valuation_flagged'] = True
    elif fault == 'technical_error': candidate['error'] = 'Invalid share count'
    else: candidate['sanity']['headline'] = 'Invalid share count'
    before = deepcopy(candidate)
    result = dcf_quality.normalize_valuation_payload(candidate)
    assert result['valuation_usability']['usable'] is False
    assert result['fair_value_base'] is None
    assert candidate == before
