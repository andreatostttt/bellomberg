"""The actual native round context scopes a targeted consultation without altering research rounds."""
from copy import deepcopy
from types import SimpleNamespace

from bellomberg.agents.specialists import base
from test_trade_idea_live_consultation import board


FULL_R1 = "R1: scrivi l'analisi completa del dominio. "
TARGETED_R1 = "R1 targeted consultation:"


def context(board, round_n, *, marker=None, desk="quant"):
    board.current_round = round_n
    cls = type("ContextDesk", (base.Specialist,), {"name": desk, "system_prompt": "Native context proof."})
    specialist = cls(board, client=SimpleNamespace())
    specialist._task_context = None if marker is None else {
        "consultation": marker, "question": "Discuss only terminal FCFF economics in three requested points.",
        "draft_assumptions": {"economic_driver": "explicit archived draft"}, "evidence_refs": ["source-document"]}
    return specialist._build_round_context(round_n)


def test_targeted_r1_keeps_context_but_replaces_generic_domain_report_requirement(board):
    before = deepcopy(board.data)
    normal = context(board, 1)
    targeted = context(board, 1, marker=True)
    assert FULL_R1 in normal and FULL_R1 not in targeted
    assert TARGETED_R1 in targeted
    assert normal.split(FULL_R1)[0] == targeted.split(TARGETED_R1)[0]
    assert "specific question" in targeted and "draft assumptions" in targeted
    assert "requested format" in targeted
    assert board.data == before


def test_normal_r1_retains_complete_domain_research(board):
    actual = context(board, 1)
    assert FULL_R1 in actual and TARGETED_R1 not in actual
    assert "Book reale accettato" in actual and "Consumed model drivers" in actual


def test_consultation_marker_does_not_change_r0_context(board):
    assert context(board, 0, marker=True) == context(board, 0)


def test_consultation_marker_does_not_change_r2_context(board):
    assert context(board, 2, marker=True) == context(board, 2)


def test_fundamentals_normal_r1_still_authors_and_consults_before_compiling(board):
    actual = context(board, 1, desk="fundamentals")
    assert "get_candidate_model_inputs" in actual and "submit_candidate_model_plan" in actual
    assert "ask_specialist" in actual and "get_valuation" in actual
    assert TARGETED_R1 not in actual


def test_truthy_nonboolean_consultation_marker_cannot_change_normal_r1(board):
    assert context(board, 1, marker=1) == context(board, 1)
