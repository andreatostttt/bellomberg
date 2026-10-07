"""trade-idea-research/3 (PM 03/10/2026): Capo MEDIUM with 128000 output tokens and the V3 memo grid."""
from copy import deepcopy

import pytest

from bellomberg.agents import trade_idea
from test_trade_idea_capo_finalization_dispatch import research_board, capture, BeforeTransport  # noqa: F401

V2 = {'analysis_mode': 'fundamentals_research_v1', 'execution_policy': 'trade-idea-research/2'}
V3 = {'analysis_mode': 'fundamentals_research_v1', 'execution_policy': 'trade-idea-research/3'}


def test_v3_raises_only_the_capo_and_v2_stays_as_accepted():
    from bellomberg.core.trade_idea_policy import role_effort, output_cap, CURRENT_EXECUTION_POLICY
    # /4 (PM, Lotto 2) is now current; /3 stays accepted for resumes with these choices.
    assert CURRENT_EXECUTION_POLICY == 'trade-idea-research/4'
    assert {role: role_effort(V3, role) for role in trade_idea.MODEL_IDS} == {
        'specialist': 'medium', 'red_team': 'medium', 'capo': 'medium', 'aux': 'medium'}
    assert output_cap(V3, 'capo', 0) == 128000 and output_cap(V3, 'specialist', 64000) == 64000
    assert role_effort(V2, 'capo') == 'low' and output_cap(V2, 'capo', 0) == 32768


def test_v3_without_research_mode_is_rejected():
    from bellomberg.core.trade_idea_policy import execution_policy
    with pytest.raises(ValueError):
        execution_policy({'execution_policy': V3['execution_policy']})


def test_v3_capo_dispatch_sends_medium_full_room_and_the_v3_grid(research_board):
    from bellomberg.core.trade_idea_contract import CAPO_RESEARCH_INSTRUCTIONS_V2
    research_board.execution_policy = V3['execution_policy']
    research_board.budget_gate.reuse_capo_response = lambda: None
    original = deepcopy(research_board.data)
    calls = []
    with pytest.raises(BeforeTransport):
        capture(research_board, calls)
    assert calls[0]['thinking'] == {'type': 'effort', 'effort': 'medium'}
    assert calls[0]['max_tokens'] == 128000
    assert 'SECTION GRID' in calls[0]['system'] and 'Objection <ID>' in calls[0]['system']
    assert CAPO_RESEARCH_INSTRUCTIONS_V2 not in calls[0]['system']
    for desk in trade_idea.TRADE_IDEA_DESKS:
        assert calls[0]['messages'][0]['content'].count('Final review ' + desk) == 1
    assert research_board.data == original


def test_v3_pdf_quality_carries_the_real_policy_so_delivery_is_not_refused(tmp_path):
    from bellomberg.reporting.trade_idea_delivery import assess_trade_idea_completion
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    from test_trade_idea_report_v2 import editorial_fixture
    run, result = editorial_fixture()
    run['execution_policy'] = V3['execution_policy']
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / 'v3.pdf')
    assert artifact['status'] == 'ready', artifact['reason']
    assert artifact['quality']['execution_policy'] == V3['execution_policy']
    manifest = {**V3, 'completion_contract': 'research-memo-pdf/1', 'model_status': 'not_required',
                'judgment': 'watch', 'complete_package_status': 'ready', 'pdf_quality': artifact['quality']}
    assert assess_trade_idea_completion(manifest)['status'] == 'ready'


def _catalog(capo_max):
    rows = {}
    for role, model in trade_idea.MODEL_IDS.items():
        rows[role] = {'id': model, 'pricing': {'prompt': '0.000001', 'completion': '0.000002'},
                      'supported_efforts': ['low', 'medium', 'max'], 'context_length': 1000000,
                      'max_completion_tokens': capo_max if role == 'capo' else 128000}
    return {'models': rows}


# MOD-CAP (06/10, decisione PM): un Capo sopra il massimo del provider si ADATTA (non e' piu' un
# rifiuto del preflight); resta rifiutato un tetto che il catalogo non dichiara.
@pytest.mark.parametrize('capo_max, blocked', [(128000, False), (64000, False), (None, True)])
def test_preflight_refuses_a_capo_cap_above_the_provider_maximum_before_spending(capo_max, blocked):
    checked = trade_idea.preflight_trade_idea(
        'ACME', 'Thesis', 'manual', '10', catalog_fetcher=lambda: _catalog(capo_max),
        identity_resolver=lambda ticker: {'ticker': ticker, 'status': 'invalid', 'reason': 'offline test'},
        key_checker=lambda: None, mandate_loader=lambda: {}, active_checker=lambda: False,
        analysis_mode='fundamentals_research_v1', execution_policy=V3['execution_policy'])
    text = ' | '.join(checked.get('reasons') or [])
    assert ('Tetto di output del Capo' in text) is blocked, text
