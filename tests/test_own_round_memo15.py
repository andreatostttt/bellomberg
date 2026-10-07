"""P0-5: native prompts retain exact own-round evidence; all inputs synthetic."""
from copy import deepcopy
import pytest

from bellomberg.agents.specialists import base
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE, seal_research_thesis


@pytest.fixture
def actor(tmp_path, monkeypatch):
    board = base.Blackboard(heartbeat_path=tmp_path / 'heartbeat.json')
    board.data['quant'] = {0: 'OWN_R0_SYNTHETIC', 1: 'OWN_R1_SYNTHETIC', 2: 'OWN_R2_LATEST'}
    board.data['macro'] = {0: 'PEER_R0', 1: 'PEER_R1'}
    desk = base.Specialist.__new__(base.Specialist)
    desk.name, desk.blackboard = 'quant', board
    monkeypatch.setattr(desk, '_build_memory_block', lambda: 'SYNTHETIC_MEMORY')
    monkeypatch.setattr(desk, 'compute_score', lambda: None)
    return desk


def trade_idea(actor, monkeypatch):
    from bellomberg.agents import trade_idea as ti
    b = actor.blackboard
    b.run_scope, b.target_ticker = 'trade_idea', 'ZZSYN'
    b.independent_round = 1
    monkeypatch.setattr(ti, 'candidate_model_context', lambda *a, **k: {'synthetic': True})
    monkeypatch.setattr(ti, 'pm_constraints_text', lambda *a: ('SYNTHETIC_PM', None))


@pytest.mark.parametrize('round_n,expected,wrong', [(1, 'OWN_R0_SYNTHETIC', 'OWN_R1_SYNTHETIC'),
                                                   (2, 'OWN_R1_SYNTHETIC', 'OWN_R0_SYNTHETIC')])
def test_weekly_exact_previous_round(actor, round_n, expected, wrong):
    p = actor._build_round_context(round_n)
    assert expected in p
    assert wrong not in p and 'OWN_R2_LATEST' not in p
    assert 'OWN PREVIOUS ROUND' in p and 'same run' in p


@pytest.mark.parametrize('missing', [None, '', '   ', {'invalid': 'shape'}])
def test_missing_previous_is_declared_without_other_round_fallback(actor, missing):
    actor.blackboard.data['quant'][0] = missing
    p = actor._build_round_context(1)
    assert 'OWN PREVIOUS ROUND' in p and 'unavailable' in p
    assert 'OWN_R1_SYNTHETIC' not in p and 'OWN_R2_LATEST' not in p


def test_legacy_ti_r2_includes_own_r1_in_addition_to_recon(actor, monkeypatch):
    trade_idea(actor, monkeypatch)
    actor.blackboard.current_round = 2
    p = actor._build_round_context(2)
    assert 'OWN_R1_SYNTHETIC' in p and 'OWN_R0_SYNTHETIC' in p
    assert 'OWN_R2_LATEST' not in p


@pytest.mark.parametrize('research', [False, True])
@pytest.mark.parametrize('round_n', [0, 1])
def test_ti_early_rounds_remain_independent(actor, monkeypatch, research, round_n):
    trade_idea(actor, monkeypatch)
    actor.blackboard.current_round = round_n
    if research:
        actor.blackboard.analysis_mode = RESEARCH_ANALYSIS_MODE
    p = actor._build_round_context(round_n)
    assert 'PEER_R1' not in p and 'OWN_R1_SYNTHETIC' not in p
    assert ('OWN_R0_SYNTHETIC' in p) == (round_n == 1)


def test_weekly_research_r2_reuses_seal_once_and_preserves_paid_state(actor):
    b = actor.blackboard
    b.analysis_mode = RESEARCH_ANALYSIS_MODE
    seal_research_thesis(b, desks=['quant', 'macro'])
    before = deepcopy(b.data)
    first = actor._build_round_context(2)
    second = actor._build_round_context(2)
    assert first == second and b.data == before
    assert first.count('OWN_R1_SYNTHETIC') == 1
    assert 'OWN PREVIOUS ROUND' not in first


def test_mutated_sealed_report_is_rejected(actor):
    b = actor.blackboard
    b.analysis_mode = RESEARCH_ANALYSIS_MODE
    seal_research_thesis(b, desks=['quant', 'macro'])
    b.data['quant'][1] = 'CHANGED_AFTER_SEAL'
    with pytest.raises(ValueError, match='changed after'):
        actor._build_round_context(2)


def test_report_bodies_share_existing_budget_and_own_truncation_is_explicit(actor, monkeypatch):
    monkeypatch.setattr(base, 'MAX_CHAR_BLACKBOARD', 101)
    actor.blackboard.data['quant'][0] = '\u03a9' * 130
    actor.blackboard.data['macro'] = {0: '\u0394' * 90}
    p = actor._build_round_context(1)
    assert p.count('\u03a9') == 101 and p.count('\u0394') == 0
    own = p.split('OWN PREVIOUS ROUND', 1)[1].split('BLACKBOARD:', 1)[0]
    assert '101 SU 130' in own
    assert 'nessun tool garantisce questo round esatto' in own
    assert 'ask_specialist' not in own
    assert 'NE LEGGI 0 SU 90' in p


def test_short_own_report_leaves_measured_remaining_budget_to_peers(actor, monkeypatch):
    monkeypatch.setattr(base, 'MAX_CHAR_BLACKBOARD', 101)
    actor.blackboard.data['quant'][0] = '\u03a9' * 30
    actor.blackboard.data['macro'] = {0: '\u0394' * 90}
    p = actor._build_round_context(1)
    assert p.count('\u03a9') == 30 and p.count('\u0394') == 71
    assert 'NE LEGGI 71 SU 90' in p


def test_gap_never_reads_history_or_mutates_round_reports(actor):
    b = actor.blackboard
    b.data['quant'].pop(0)
    b.memory_db = type('NoHistory', (), {
        'build_pm_binding_block': lambda self: '',
        'get_specialist_reports': lambda self, *a: pytest.fail('historical lookup forbidden')})()
    before = deepcopy(b.data['quant'])
    assert 'unavailable' in actor._build_round_context(1)
    assert b.data['quant'] == before


def test_own_report_text_is_verbatim_even_if_it_quotes_recovery_diagnostics(actor):
    text = "Prova sintetica: nessuno dei tool di cui disponi restituisce il report di un desk. Fine."
    actor.blackboard.data['quant'][0] = text
    assert text in actor._build_round_context(1)
