# -*- coding: utf-8 -*-
"""Soglia di quorum del Consigliere (KB Opus 5.5, regola fissata da main 04/10):
soglia = min(4, desk del roster), Fundamentals sempre obbligatorio. Con il roster di
produzione (6) e' la regola di Trade Idea (4/6); con un roster di 2 servono tutti e due.
Dati sintetici (ZZTEST, ZZREQ-*)."""
import pytest

from bellomberg.reporting.weekly_gaps import committee_gaps_summary, quorum_threshold

ROSTER6 = ["macro", "eventdesk", "crypto", "fundamentals", "quant", "options"]
CAPO_OK = {"memo": "## BLUF\nTesto sintetico ZZTEST.", "usage": {"complete": True, "stop_reason": "end_turn"}}


def _summary(roster, gaps):
    data = {desk: {1: "Report R1 sintetico " + desk, 2: "Replica R2 sintetica " + desk} for desk in roster}
    data["_red_team"] = {1: "Critica sintetica su ZZTEST."}
    data["_desk_gaps"] = {desk: {"message": "specialist response truncated", "desk": desk, "round": 1,
                                 "request_id": "ZZREQ-" + desk, "phase": "round_1"} for desk in gaps}
    for desk in gaps:
        data[desk] = {}
    store = {"roster": roster, "r2_specialists": [d for d in ("fundamentals", "quant", "options") if d in roster],
             "checkpoints": {}}
    return committee_gaps_summary(data, store, capo=CAPO_OK, language="it")


def test_soglia_effettiva():
    assert quorum_threshold(ROSTER6) == 4
    assert quorum_threshold(["fundamentals", "quant"]) == 2
    assert quorum_threshold(ROSTER6 + ["zzdesk"]) == 4


def test_roster_di_sei_quattro_su_sei_come_trade_idea():
    ok = _summary(ROSTER6, ["macro", "crypto"])
    assert ok["quorum"]["reached"] and ok["quorum"]["min_present"] == 4
    assert "Regola di quorum: Fundamentals più almeno 4 desk su 6." in ok["memo_markdown"]
    below = _summary(ROSTER6, ["macro", "crypto", "options"])
    assert below["status"] == "below_quorum" and not below["quorum"]["reached"]


def test_roster_di_due_servono_tutti():
    complete = _summary(["fundamentals", "quant"], [])
    assert complete["status"] == "complete" and complete["quorum"]["min_present"] == 2
    assert complete["memo_markdown"] == ""
    one_missing = _summary(["fundamentals", "quant"], ["quant"])
    assert one_missing["status"] == "below_quorum"
    assert ("Regola di quorum: Fundamentals più tutti i 2 desk (roster di 2 desk: servono tutti)."
            in one_missing["memo_markdown"])


@pytest.mark.parametrize("roster", [ROSTER6, ["fundamentals", "quant"]])
def test_fundamentals_resta_obbligatorio(roster):
    assert _summary(roster, ["fundamentals"])["status"] == "below_quorum"


def test_orchestrazione_usa_la_stessa_soglia():
    from types import SimpleNamespace
    from bellomberg.agents import weekly_lifecycle as wl
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    store = SimpleNamespace(context={"contract": {"roster": ["fundamentals", "quant"],
                                                  "analysis_mode": "fundamentals_research_v1"}})
    board = SimpleNamespace(data={"_desk_gaps": {}}, analysis_mode="fundamentals_research_v1")
    wl.quorum_still_reachable(board, store)          # due su due: raggiunto
    board.data["_desk_gaps"] = {"quant": {"message": "specialist response truncated"}}
    with pytest.raises(WeeklyRunBlocked, match="almeno 2 desk su 2"):
        wl.quorum_still_reachable(board, store)
    store.context["contract"]["roster"] = ROSTER6
    board.data["_desk_gaps"] = {d: {"message": "x"} for d in ("macro", "crypto")}
    wl.quorum_still_reachable(board, store)          # 4 su 6: raggiunto
    board.data["_desk_gaps"]["options"] = {"message": "x"}
    with pytest.raises(WeeklyRunBlocked, match="almeno 4 desk su 6"):
        wl.quorum_still_reachable(board, store)
