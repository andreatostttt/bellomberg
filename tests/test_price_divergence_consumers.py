"""Consumer policy for price comparisons; synthetic tools and a fake Capo provider."""
from copy import deepcopy
import json

import pytest

from bellomberg.agents import capo, chat_tools
from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist
from test_capo_collasso import MEMO_VERO, _bb, _msg, _prepara
from test_sector_valuation_integration import (
    SYMBOL, documented_payload, isolated_tools, synthetic_bundle,
)


def test_flagged_valuation_names_actual_failure_without_inventing_price_gap(isolated_tools, monkeypatch):
    tools = isolated_tools
    bundle = synthetic_bundle()
    path = tools.directory / 'broken-contract.xlsx'
    path.write_bytes(b'synthetic unavailable workbook')
    payload = documented_payload(bundle, path)
    payload.update(valuation_flagged=True, error='Share count and quotation units do not reconcile')
    monkeypatch.setattr(tools.engine, 'generate_valuation', lambda *args, **kwargs: deepcopy(payload))

    result = tools.chat.dispatch('get_valuation', {'ticker': SYMBOL}, prepared_bundle=bundle)['data']

    assert result['valuation_usability']['usable'] is False
    assert result['valuation_flagged'] is True
    assert payload['error'] in result['_analyst_note']
    assert 'Fair value molto distante dal prezzo' not in result['_analyst_note']
    assert result['fair_value_weighted'] is None


def test_get_valuation_instructions_cover_every_scenario_and_preserve_real_gates():
    description = next(item['description'] for item in chat_tools.TOOL_DEFINITIONS
                       if item['name'] == 'get_valuation')
    assert 'scarto positivo o negativo' in description
    assert 'bear, base e bull' in description
    assert 'non calibrare' in description.lower()
    assert 'valuation_usability.usable=true' in description
    assert 'dati, fonti, unita' in description


def test_capo_receives_new_policy_beside_unchanged_historical_report(monkeypatch):
    calls = _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    board = _bb()
    legacy = 'Bull escluso dalla tabella per scarto FV/prezzo; nessun altro errore indicato.'
    board.data['macro'][2] = legacy

    memo, _ = capo.run_capo(board)

    assert MEMO_VERO in memo  # Native memo adds its date/mandate header.
    assert len(calls) == 1
    system = str(calls[0]['system'])
    assert 'SCARTO FAIR VALUE/PREZZO' in system
    assert 'bear, base e bull' in system
    assert 'scarto positivo o negativo' in system
    assert 'pura distanza dimostrata' in system
    assert 'rischio e sizing' in system
    assert legacy in str(calls[0]['messages'])
    assert board.data['macro'][2] == legacy


def test_fundamentals_keeps_author_view_and_technical_validation_separate():
    prompt = FundamentalsSpecialist.system_prompt
    assert 'scarto positivo o negativo' in prompt
    assert 'bear, base e bull' in prompt
    assert 'non calibrare' in prompt.lower()
    assert 'dati, fonti, unita' in prompt
    assert 'NON passare parametri legacy top-level' in prompt
    assert 'fonte URL letta, data/scadenza' in prompt


def _old_canonical_payload(fair_value):
    price = 100.0
    ratio = fair_value / price
    distance = abs(ratio - 1.0)
    headline = ('VAL SOSPETTA: fair value %.0f diverge %.0f%% dal prezzo %.0f (ratio %.2f). '
                'Rivedere growth/margini/WACC/shares/net_debt PRIMA di fidarsi. '
                'ESCLUSO dalla ACTION TABLE.') % (fair_value, distance * 100, price, ratio)
    return {'ticker': 'SYNTH', 'price': price, 'fair_value_base': fair_value,
            'valuation_flagged': True, 'sanity_headline': headline, '_timestamp': '2026-10-01T12:00:00',
            'sanity': {'status': 'ok', 'ratio': round(ratio, 2),
                       'upside_pct': round((ratio - 1) * 100, 1), 'severity': 'BLOCK',
                       'exclude_from_action_table': True, 'headline': headline}}


@pytest.mark.parametrize('fair_value', [1100.0, 10.0])
def test_action_validator_reinterprets_proven_historical_distance_without_writing(tmp_path, fair_value):
    from bellomberg.agents.action_validator import _canonical_sanity
    path = tmp_path / 'VAL_SYNTH_FLAGGED.payload.json'
    path.write_text(json.dumps(_old_canonical_payload(fair_value)), encoding='utf-8')
    original = path.read_bytes()

    severity, judged_at = _canonical_sanity('SYNTH', report_dir=tmp_path)

    assert severity == 'OK'
    assert judged_at == '2026-10-01'
    assert path.read_bytes() == original


@pytest.mark.parametrize('fault', ['technical_error', 'unattributed_flag', 'unrecognized_headline', 'hidden_value'])
def test_action_validator_preserves_independent_or_unproven_historical_block(tmp_path, fault):
    from bellomberg.agents.action_validator import _canonical_sanity
    payload = _old_canonical_payload(1100.0)
    if fault == 'technical_error':
        payload['error'] = 'Quotation and share units conflict'
    elif fault == 'unattributed_flag':
        payload['sanity_headline'] = 'Independent unresolved integrity check'
    elif fault == 'unrecognized_headline':
        payload['sanity']['headline'] = 'Independent math failure'
    else:
        payload['fair_value_base'] = None
    path = tmp_path / 'VAL_SYNTH_FLAGGED.payload.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    original = path.read_bytes()

    assert _canonical_sanity('SYNTH', report_dir=tmp_path)[0] == 'BLOCK'
    assert path.read_bytes() == original
