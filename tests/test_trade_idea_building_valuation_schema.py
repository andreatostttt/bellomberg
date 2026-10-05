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
    # Contratto attuale (Excel archiviato, commit 1326312): get_valuation e' fuori dal menu
    # di ogni desk, quindi in building non esiste piu' il nodo privato "solo ticker".
    # Garanzia conservata: il compilatore Excel non torna raggiungibile dal menu di building.
    assert valuation(chat_tools.get_tools_for_agent("fundamentals")) is None
    actual_tools = menu(board)
    assert valuation(actual_tools) is None
    assert any(tool["name"] == "get_valuation" for tool in chat_tools.TOOL_DEFINITIONS)


def test_private_valuation_node_cannot_mutate_shared_registry_or_other_nodes(board):
    # get_valuation archiviato (vedi sopra): resta la garanzia che costruire il menu di
    # building non tocchi il registro condiviso e riusi gli stessi nodi degli altri tool.
    original_tools = chat_tools.get_tools_for_agent("fundamentals")
    registry_before = deepcopy(chat_tools.TOOL_DEFINITIONS)
    actual_tools = menu(board)
    assert valuation(actual_tools) is None
    assert chat_tools.TOOL_DEFINITIONS == registry_before
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
    # Excel archiviato: nessun desk/round/fase riceve get_valuation (prima il confronto
    # "is original" passava anche con None da entrambe le parti).
    assert valuation(chat_tools.get_tools_for_agent(desk)) is None
    assert valuation(menu(board, **settings)) is None
