"""The native compiler menu uses its ticker-only contract without mutating shared tools."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from bellomberg.agents import chat_tools
from bellomberg.agents.specialists import base
from test_trade_idea_live_consultation import board


def valuation(tools):
    return next((tool for tool in tools if tool["name"] == "get_valuation"), None)


def menu(board, *, desk="fundamentals", scope="trade_idea", round_n=1, phase="building"):
    board.run_scope, board.current_round, board.model_phase = scope, round_n, phase
    cls = type("SchemaDesk", (base.Specialist,), {"name": desk})
    return cls(board, client=SimpleNamespace())._build_tools_schema()


def test_fundamentals_building_exposes_only_native_compiler_arguments(board):
    actual = valuation(menu(board))
    original = valuation(chat_tools.get_tools_for_agent("fundamentals"))
    assert actual["input_schema"] == {
        "type": "object", "properties": {"ticker": original["input_schema"]["properties"]["ticker"]},
        "required": ["ticker"], "additionalProperties": False,
    }
    description = actual["description"]
    assert description.index("submit_candidate_model_plan.plan.scenario_rationale") < description.index("get_valuation(ticker)")
    assert "before" in description and "compile" in description


def test_private_valuation_node_cannot_mutate_shared_registry_or_other_nodes(board):
    original_tools = chat_tools.get_tools_for_agent("fundamentals")
    original = valuation(original_tools)
    registry_before = deepcopy(chat_tools.TOOL_DEFINITIONS)
    actual_tools = menu(board)
    actual = valuation(actual_tools)
    assert actual is not original
    actual["input_schema"]["properties"]["ticker"]["description"] = "private mutation"
    actual["description"] = "private mutation"
    assert chat_tools.TOOL_DEFINITIONS == registry_before
    assert valuation(menu(board))["input_schema"]["properties"]["ticker"] == original["input_schema"]["properties"]["ticker"]
    for node in original_tools:
        if node["name"] not in {"get_valuation", "add_guidance", "add_research_note"}:
            assert next(item for item in actual_tools if item["name"] == node["name"]) is node


@pytest.mark.parametrize("settings", [
    {"scope": "weekly"},
    {"round_n": 0},
    {"round_n": 2},
    {"phase": "reviewing"},
    {"desk": "macro"},
])
def test_other_desks_rounds_and_phases_keep_the_native_registry_schema(board, settings):
    desk = settings.get("desk", "fundamentals")
    original = valuation(chat_tools.get_tools_for_agent(desk))
    assert valuation(menu(board, **settings)) is original
