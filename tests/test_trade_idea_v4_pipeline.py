"""trade-idea-research/4 in the pipeline: Capo dispatch, sizing band, numeric gate, store.

Offline and synthetic only (ticker ZZTEST.MI, invented numbers, no provider).
The /2-/3 Capo bodies are pinned by sha256: paid runs are resumed by comparing the
request sha256, so a /4 change must not move a single byte of them.
"""
import hashlib
import json
import sqlite3
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.trade_idea_contract import (
    CAPO_RESEARCH_INSTRUCTIONS_V3, CAPO_RESEARCH_INSTRUCTIONS_V4, TRADE_IDEA_RESULT_SCHEMA,
    TRADE_IDEA_RESULT_SCHEMA_V4)
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V3, EXECUTION_POLICY_V4
from bellomberg.storage import trade_idea_store as store_module
from test_trade_idea_capo_finalization_dispatch import research_board, BeforeTransport  # noqa: F401
from test_trade_idea_policy_v4 import TICKER, v4_result
from test_trade_idea_store import db_path, migrated, request, store  # noqa: F401
import test_trade_idea_paid_capo_checkpoint as paid

MODE = "fundamentals_research_v1"
HELD = "ZZHELD.MI"

# Measured on the unmodified agents/trade_idea.py (before the /4 pipeline change),
# with the research_board fixture and capture() of the finalization dispatch tests.
FROZEN_BODY_SHA256 = {
    "trade-idea-research/2": "8feaf317d7eb81b5f0f7192be9e46043ed0716997f47bf46cb8120be5b4579c3",
    "trade-idea-research/3": "6f7887ea8636f154ae604fb4f26cf55d6384e6583849e3d8828c8f36256f46ba",
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _capture(board, policy, *, sizing=None, portfolio=None, mandate=None):
    board.execution_policy = policy
    if sizing is not None:
        board.target_ticker = TICKER
    board.budget_gate.reuse_capo_response = lambda: None
    calls = []

    def before_transport(**kwargs):
        calls.append(deepcopy(kwargs))
        raise BeforeTransport()
    with pytest.raises(BeforeTransport):
        trade_idea.run_trade_idea_capo(
            board, portfolio=portfolio if portfolio is not None else
            {"positions": [], "cash": "explicitly unavailable"},
            mandate=mandate if mandate is not None else {}, decision_context={"records": []},
            sizing=sizing if sizing is not None else {"status": "unavailable", "reason": "No fabricated size"},
            client=SimpleNamespace(messages=SimpleNamespace(stream=before_transport)))
    return calls[0]


OTHERS = (("ZZHELD.MI", 15000.0), ("ZZONE.MI", 12000.0), ("ZZTWO.MI", 10000.0),
          ("ZZTHR.MI", 9000.0), ("ZZFOU.MI", 9000.0))          # invested 55000
HELD_VALUE = 4000.0


def measured_sizing(*, cash=5000.0, max_add=3100.0, starter=1800.0, sector=3500.0, stress=4000.0,
                    held=False, **row_overrides):
    """A synthetic common-engine output with every BUY limit MEASURED."""
    row = {"ticker": TICKER, "vol_annual_pct": 24.0, "vol_estimated": False, "max_add_eur": max_add,
           "suggested_starter_eur": starter, "class": "single", "class_fallback": False,
           "sector_policy": "synthetic_sector", "sector_remaining_eur": sector, **row_overrides}
    positions = [{"ticker": name, "current_eur": value, "remaining_capacity_eur": 2000.0,
                  "vol_estimated": False, "corr_estimated": False, "class_fallback": False, "class": "single"}
                 for name, value in OTHERS]
    if held:
        positions.append({"ticker": TICKER, "current_eur": HELD_VALUE, "remaining_capacity_eur": max_add,
                          "vol_estimated": False, "corr_estimated": False, "class_fallback": False,
                          "class": "single", **row_overrides})
    return {"summary": {"cash_buffer_eur": cash, "invested_capital_eur": 55000.0,
                        "stress_var_budget": {"additional_capacity_eur": stress}},
            "positions": positions, "candidates": [] if held else [row],
            "_trade_idea_measurements": {
                "risk_status": "measured", "stress_status": "measured", "candidate_status": "measured",
                "candidate_metrics": {"status": "measured", "compared_tickers": [name for name, _ in OTHERS]}}}


def _book_row(ticker, value, **overrides):
    """The REAL shape of a MemoryDB.get_portfolio_summary EUR row (memory_db.py ~2091):
    valore_mercato in EUR, valore_mercato_eur None, fx identity."""
    return {"ticker": ticker, "quantita": 10.0, "valuta": "EUR", "valore_mercato": value,
            "valore_mercato_eur": None, "fx_to_eur": 1.0, "fx_source": "identity", "price_stale": False,
            **overrides}


def book(cash=5000.0, held_value=None, **held_row):
    """The book the engine read: its raw cash is what the band uses (NAV = 55000 + cash)."""
    positions = [_book_row(name, value) for name, value in OTHERS]
    if held_value is not None:
        positions.append(_book_row(TICKER, held_value, **held_row))
    return {"positions": positions, "cash_source": "sqlite", "cash_disponibile_eur": cash}


PORTFOLIO = book()
# Synthetic mandate in the mandato_pm schema (bases as declared there): new position 3-6%
# of capital (NAV), cash floor 2% of capital, minimum position 1% of NAV, single cap 12%
# and top three 80% of INVESTED, at most 25 names, free cut up to 25% of a position with
# every cut condition switched on. NAV 60000: BUY 1800-3600, cash above floor 3800.
MANDATE = {"sizing": {"size_nuova_posizione_pct": [3, 6], "posizione_minima_pct": 1,
                      "cap_single_pct": 12, "cap_veicolo_pct": 20, "max_posizioni": 25, "top3_max_pct": 80},
           "cassa": {"cassa_minima_pct": 2},
           "disciplina": {"taglio_max_senza_condizioni_pct": [15, 25], "taglio_con_condizioni_oltre_pct": 60,
                          "condizioni_taglio_oltre": {"sharpe_12m_negativo": True, "nessun_catalyst_90g": True,
                                                      "tesi_smentita": True}}}


def mandate_with(section, **fields):
    """MANDATE with some fields of one section replaced (None = removed)."""
    changed = deepcopy(MANDATE)
    for key, value in fields.items():
        if value is None:
            changed[section].pop(key, None)
        else:
            changed[section][key] = value
    return changed


FREE_CUTS = mandate_with("disciplina", condizioni_taglio_oltre={"sharpe_12m_negativo": False,
                                                                "nessun_catalyst_90g": False,
                                                                "tesi_smentita": False})


# --------------------------------------------------------------------------- dispatch

@pytest.mark.parametrize("policy", sorted(FROZEN_BODY_SHA256))
def test_v2_and_v3_capo_bodies_stay_byte_identical(research_board, policy):
    body = _capture(research_board, policy)
    assert hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest() == FROZEN_BODY_SHA256[policy]
    assert "SIZING BAND" not in body["messages"][0]["content"]


def test_v4_dispatch_sends_the_v4_prompt_and_schema_without_inline_citation_rule(research_board):
    body = _capture(research_board, EXECUTION_POLICY_V4)
    system, content = body["system"], body["messages"][0]["content"]
    assert system.startswith(CAPO_RESEARCH_INSTRUCTIONS_V4)
    assert CAPO_RESEARCH_INSTRUCTIONS_V3 not in system
    assert "Ogni frase con una cifra quantitativa" not in system and "[evidence: id]" not in system
    assert "Nella prosa NON scrivere" in system
    # The Evidence.source rule is retained: the receipts binding reads it.
    assert "Per ogni Evidence.source usa il formato esatto '[src: nome_tool] fonte'" in system
    assert json.dumps(TRADE_IDEA_RESULT_SCHEMA_V4, ensure_ascii=False, separators=(",", ":")) in content
    assert json.dumps(TRADE_IDEA_RESULT_SCHEMA, ensure_ascii=False, separators=(",", ":")) not in content
    assert body["thinking"] == {"type": "effort", "effort": "medium"} and body["max_tokens"] == 128000
    # No measured sizing: the band is declared missing, no proposal admitted.
    assert "SIZING BAND: fascia non disponibile" in content and "proposal=null" in content
    assert ("TAGLIA DELLA PROPOSTA: la SIZING BAND del server applica le regole di questo mandato "
            "(size di una nuova posizione, peso massimo per posizione") in system
    assert "scrivile solo nei campi della stima stessa" in system


def test_v4_band_block_sits_after_the_sizing_block_with_the_exact_band(research_board):
    sizing = measured_sizing()
    content = _capture(research_board, EXECUTION_POLICY_V4, sizing=sizing,
                       portfolio=PORTFOLIO, mandate=MANDATE)["messages"][0]["content"]
    band = trade_idea._sizing_band(sizing, PORTFOLIO, TICKER, mandate=MANDATE)
    assert band["band_min_eur"] == 1800.0 and band["band_max_eur"] == 3100.0
    block = "SIZING BAND (server; la fascia vale SOLO per l'azione indicata come chiave"
    assert content.count(block) == 1
    assert json.dumps({"BUY": band}, ensure_ascii=False, separators=(",", ":")) in content
    assert "BUY: size nuova posizione e cassa minima del mandato" in content
    assert content.index("Sizing deterministico") < content.index(block) < content.index("Quotazione candidato")
    assert "fascia VUOTA" not in content


def test_v4_band_is_declared_unavailable_when_the_engine_estimates_volatility(research_board):
    sizing = measured_sizing(vol_estimated=True)
    content = _capture(research_board, EXECUTION_POLICY_V4, sizing=sizing,
                       portfolio=PORTFOLIO, mandate=MANDATE)["messages"][0]["content"]
    assert "SIZING BAND: fascia non disponibile (metriche stimate dal motore" in content
    assert "SIZING BAND (server" not in content


# --------------------------------------------------------------------------- band

def _band(sizing=None, portfolio=None, mandate=MANDATE, reasons=None):
    return trade_idea._sizing_band(sizing or measured_sizing(), portfolio or PORTFOLIO, TICKER, reasons,
                                   mandate=mandate)


def test_sizing_band_min_from_the_mandate_max_from_mandate_and_engine():
    band = _band(measured_sizing(starter=777.0))
    assert band == {"band_min_eur": 1800.0, "band_max_eur": 3100.0, "empty": False, "components": {
        "action": "BUY", "nav_eur": 60000.0, "position_minimum_eur": 600.0, "mandate_min_eur": 1800.0,
        "cash_above_minimum_eur": 3800.0, "capacity_eur": 3100.0, "stress_budget_eur": 4000.0,
        "sector_remaining_eur": 3500.0, "mandate_max_eur": 3600.0, "top3_room_eur": 85000.0,
        "cash_eur": 5000.0, "cash_minimum_eur": 1200.0, "engine_starter_eur_info": 777.0}}


def test_sizing_band_follows_the_current_mandate_values():
    band = _band(measured_sizing(max_add=9000.0), mandate=mandate_with("sizing", size_nuova_posizione_pct=[4, 5]))
    assert (band["band_min_eur"], band["band_max_eur"]) == (2400.0, 3000.0)


@pytest.mark.parametrize("limit", ["max_add", "sector", "stress"])
def test_sizing_band_max_is_the_tightest_measured_limit_and_can_be_empty(limit):
    band = _band(measured_sizing(**{limit: 900.0}))
    assert band["band_min_eur"] == 1800.0 and band["band_max_eur"] == 900.0 and band["empty"] is True


def test_sizing_band_cash_component_keeps_the_mandate_cash_floor_rounded_down():
    # cash 1900.7, NAV 56900.7, floor 2% = 1138.014 -> 762.686 usable -> 762 (never 763).
    band = _band(portfolio=book(1900.7))
    assert band["components"]["cash_above_minimum_eur"] == 762.0
    assert band["band_max_eur"] == 762.0 and band["band_min_eur"] == 1708.0 and band["empty"] is True
    # Without the floor the cash (1900) would have bound the band instead.
    assert _band(portfolio=book(1900.7), mandate=mandate_with("cassa", cassa_minima_pct=0))["band_max_eur"] == 1900.0


def test_sizing_band_below_the_mandate_minimum_position_is_declared_unavailable():
    reasons = []
    assert _band(measured_sizing(max_add=500.0), reasons=reasons) is None
    assert "sotto la posizione minima del mandato (600 EUR)" in reasons[0]


@pytest.mark.parametrize("mandate, reason", [
    (None, "mandato del PM non disponibile"),
    (mandate_with("sizing", size_nuova_posizione_pct=None), "non definisce la size di una nuova posizione"),
    (mandate_with("sizing", size_nuova_posizione_pct="1-2% del capitale"), "size nuova posizione del mandato illeggibile"),
    (mandate_with("sizing", size_nuova_posizione_pct=[6, 3]), "size nuova posizione del mandato illeggibile"),
    (mandate_with("sizing", size_nuova_posizione_pct=[True, 3]), "size nuova posizione del mandato illeggibile"),
    (mandate_with("cassa", cassa_minima_pct=None), "non definisce la cassa minima"),
    (mandate_with("cassa", cassa_minima_pct="5%"), "cassa minima del mandato illeggibile"),
    (mandate_with("sizing", posizione_minima_pct=None), "posizione minima del mandato assente"),
    (mandate_with("sizing", max_posizioni="25"), "max_posizioni del mandato illeggibile"),
    (mandate_with("sizing", top3_max_pct=150), "top3_max_pct del mandato illeggibile"),
])
def test_sizing_band_without_readable_mandate_rules_is_none_never_a_default(mandate, reason):
    reasons = []
    assert _band(mandate=mandate, reasons=reasons) is None
    assert any(reason in text for text in reasons), reasons


@pytest.mark.parametrize("make, broken", [
    (lambda: (measured_sizing(), book(float("nan"))), "cash_eur"),
    (lambda: (measured_sizing(max_add=float("inf")), book()), "capacity_eur"),
    (lambda: (measured_sizing(stress=float("nan")), book()), "stress_budget_eur"),
    (lambda: (measured_sizing(), {**book(), "cash_disponibile_eur": None}), "cash_eur"),
    # N8: an engine number written as text is refused, never parsed.
    (lambda: (measured_sizing(stress="4000"), book()), "stress_budget_eur"),
    (lambda: (measured_sizing(max_add="3100"), book()), "capacity_eur"),
    (lambda: (measured_sizing(), book("5000")), "cash_eur"),
])
def test_sizing_band_non_finite_or_text_components_are_declared_not_raised(make, broken):
    sizing, portfolio = make()
    reasons = []
    assert _band(sizing, portfolio, reasons=reasons) is None
    assert reasons[0] == "componenti non finiti o assenti: " + broken
    assert "fascia non disponibile (componenti non finiti" in trade_idea._sizing_band_block(
        sizing, portfolio, TICKER, MANDATE)


@pytest.mark.parametrize("mutation, reason", [
    (lambda s, p: s["candidates"][0].update(vol_estimated=True), "metriche stimate"),
    (lambda s, p: s["candidates"][0].update(class_fallback=True), "metriche stimate"),
    (lambda s, p: s["_trade_idea_measurements"].update(candidate_status="unavailable"), "candidato non misurate"),
    (lambda s, p: s["_trade_idea_measurements"].update(stress_status="unavailable"), "non misurati"),
    (lambda s, p: s["summary"]["stress_var_budget"].update(additional_capacity_eur=None), "stress_budget_eur"),
    (lambda s, p: s["candidates"][0].update(sector_remaining_eur=None), "sector_remaining_eur"),
    (lambda s, p: s["candidates"][0].update(sector_policy=None), "residuo di settore"),
    (lambda s, p: p.update(cash_source=None), "cassa"),
    (lambda s, p: p.update(stale_positions=[HELD]), "stale"),
    (lambda s, p: s.update(error="engine failed"), "errore"),
])
def test_sizing_band_is_none_with_a_declared_reason_when_not_measured(mutation, reason):
    sizing, portfolio = measured_sizing(), deepcopy(PORTFOLIO)
    mutation(sizing, portfolio)
    reasons = []
    assert _band(sizing, portfolio, reasons=reasons) is None
    assert any(reason in text for text in reasons), reasons


def test_buy_of_a_new_name_is_blocked_at_the_mandate_maximum_number_of_positions(research_board):
    reasons = []
    assert _band(mandate=mandate_with("sizing", max_posizioni=5), reasons=reasons) is None
    assert "il book ha gia' 5 posizioni, il massimo del mandato (5): nessun nuovo nome" in reasons[0]
    assert _band(mandate=mandate_with("sizing", max_posizioni=6)) is not None
    # Optional rule absent from the mandate: declared not applicable, never a default.
    band = _band(mandate=mandate_with("sizing", max_posizioni=None, top3_max_pct=None))
    assert band["components"]["rules_not_applicable"] == ["max_posizioni (non definito nel mandato)",
                                                         "top3_max_pct (non definito nel mandato)"]
    assert "top3_room_eur" not in band["components"]
    content = _capture(research_board, EXECUTION_POLICY_V4, sizing=measured_sizing(), portfolio=PORTFOLIO,
                       mandate=mandate_with("sizing", max_posizioni=None))["messages"][0]["content"]
    assert "regole del mandato non applicabili: max_posizioni (non definito nel mandato)" in content


def test_top_three_weight_bounds_the_band_on_invested_capital():
    # Others 15000/12000/10000/9000/9000 on invested 55000. Top three 50%:
    # max add = (27500 - 27000) / 0.5 = 1000 -> the BUY band is empty.
    band = _band(mandate=mandate_with("sizing", top3_max_pct=50))
    assert band["components"]["top3_room_eur"] == 1000.0 and band["band_max_eur"] == 1000.0 and band["empty"]
    reasons = []
    assert _band(mandate=mandate_with("sizing", top3_max_pct=45), reasons=reasons) is None
    assert "fascia nulla: il limite top3_room_eur" in reasons[0]
    # Top three already above 60%: only an addition large enough to dilute them is admitted.
    band = _band(measured_sizing(max_add=20000.0, sector=20000.0, stress=20000.0), book(cash=40000.0),
                 mandate=mandate_with("sizing", top3_max_pct=60, size_nuova_posizione_pct=[1, 20]))
    assert band["components"]["top3_min_eur"] == 6667.0 and band["band_min_eur"] == 6667.0
    assert band["band_max_eur"] == 15000.0
    # ADD: the held value counts in the top three.
    # (0.57 x 55000 - 27000 - 4000) / 0.43 = 813; the others already exceed 57% (min 9913).
    add = _held_band("ADD", mandate=mandate_with("sizing", top3_max_pct=57))
    assert add["components"]["top3_room_eur"] == 813.0 and add["band_max_eur"] == 813.0
    assert add["band_min_eur"] == 9913.0 and add["empty"] is True


HELD_BOOK = book(held_value=HELD_VALUE)


def _held_band(action, sizing=None, portfolio=None, mandate=MANDATE, reasons=None):
    return trade_idea._sizing_band(sizing or measured_sizing(held=True), portfolio or HELD_BOOK, TICKER,
                                   reasons, mandate=mandate, action=action)


def test_held_position_bands_for_add_trim_and_sell():
    # NAV 60000, value 4000 (EUR row in the real shape: valore_mercato only), minimum 600.
    # ADD: single cap 12% of INVESTED after the addition: (6600 - 4000) / 0.88 = 2954.
    add = _held_band("ADD")
    assert (add["band_min_eur"], add["band_max_eur"], add["empty"]) == (600.0, 2954.0, False)
    assert add["components"]["mandate_room_eur"] == 2954.0 and add["components"]["position_value_eur"] == 4000.0
    assert _held_band("ADD", measured_sizing(held=True, max_add=2000.0))["band_max_eur"] == 2000.0
    # TRIM: free cut 25% of the position, conditions switched on.
    trim = _held_band("TRIM")
    assert (trim["band_min_eur"], trim["band_max_eur"]) == (600.0, 1000.0)
    assert trim["components"]["free_cut_pct"] == 25.0 and "cash_above_minimum_eur" not in trim["components"]
    sell = _held_band("SELL")
    assert (sell["band_min_eur"], sell["band_max_eur"]) == (600.0, 4000.0)
    assert sell["conditions_required"] == ["nessun_catalyst_90g", "sharpe_12m_negativo", "tesi_smentita"]
    # No switched-on condition: a TRIM may cut up to (below) the whole and a SELL is free.
    assert _held_band("TRIM", mandate=FREE_CUTS)["band_max_eur"] == 4000.0
    assert "conditions_required" not in _held_band("SELL", mandate=FREE_CUTS)
    # No cash constraint on a sale, even with no operational cash source.
    assert _held_band("SELL", portfolio={**HELD_BOOK, "cash_source": None})["band_max_eur"] == 4000.0
    reasons = []
    assert _held_band("BUY", reasons=reasons) is None and "quella ADD" in reasons[0]
    reasons = []
    assert trade_idea._sizing_band(measured_sizing(), PORTFOLIO, TICKER, reasons, mandate=MANDATE,
                                   action="TRIM") is None
    assert "richiede una posizione esistente" in reasons[-1]


def test_add_cap_is_on_invested_capital_not_on_nav():
    # On NAV it would have been 12% x 60000 - 4000 = 3200.
    band = _held_band("ADD", measured_sizing(held=True, max_add=9000.0))
    assert band["band_max_eur"] == 2954.0


def test_cut_rules_are_read_from_the_mandate():
    for broken in ({"taglio_max_senza_condizioni_pct": None}, {"taglio_max_senza_condizioni_pct": "25%"},
                   {"condizioni_taglio_oltre": None}, {"condizioni_taglio_oltre": {"tesi_smentita": "si"}}):
        reasons = []
        assert _held_band("TRIM", mandate=mandate_with("disciplina", **broken), reasons=reasons) is None
        assert "regole di taglio del mandato" in reasons[0]


@pytest.mark.parametrize("portfolio, reason", [
    (book(held_value=HELD_VALUE, price_stale=True), "prezzo corrente e FX qualificati"),
    (book(held_value=HELD_VALUE, valuta="USD", valore_mercato_eur=HELD_VALUE, fx_source="cache"),
     "prezzo corrente e FX qualificati"),
    # A non-EUR row without valore_mercato_eur is never read through valore_mercato.
    (book(held_value=HELD_VALUE, valuta="USD", fx_source="live", fx_to_eur=0.9), "prezzo corrente e FX qualificati"),
    (book(held_value=None), "posizione non trovata"),
    ({**book(held_value=HELD_VALUE), "stale_positions": [TICKER]}, "posizioni stale"),
])
def test_held_position_without_a_qualified_value_has_no_band(portfolio, reason):
    for action in ("ADD", "TRIM", "SELL"):
        reasons = []
        assert _held_band(action, portfolio=portfolio, reasons=reasons) is None
        assert any(reason in text for text in reasons), (action, reasons)


def test_held_value_reads_the_real_book_row_shapes():
    assert trade_idea._held_value(book(held_value=4000.0), TICKER, []) == Decimal("4000.0")
    usd = book(held_value=1.0, valuta="USD", valore_mercato_eur=3600.0, fx_source="live", fx_to_eur=0.9)
    assert trade_idea._held_value(usd, TICKER, []) == Decimal("3600.0")


def test_add_band_needs_the_mandate_cap_of_the_class():
    reasons = []
    assert _held_band("ADD", mandate=mandate_with("sizing", cap_single_pct=None), reasons=reasons) is None
    assert "peso massimo per posizione (cap_single_pct)" in reasons[0]
    reasons = []
    assert _held_band("ADD", measured_sizing(held=True, **{"class": "other"}), reasons=reasons) is None
    assert "classe di sizing della posizione non riconosciuta" in reasons[0]
    # A full position (12% of invested reached) leaves no ADD band, declared.
    reasons = []
    assert _held_band("ADD", portfolio=book(held_value=6600.0), reasons=reasons) is None
    assert "mandate_room_eur" in reasons[0]


def _held_proposal(action, amount, band_min, band_max, below=False):
    result = _proposal_result(amount, band_min=band_min, band_max=band_max, below=below)
    result["proposal"]["action"] = action
    return result


def _held_valid(result, sizing=None, portfolio=None, mandate=MANDATE, accepted_band=None):
    sizing, portfolio = sizing or measured_sizing(held=True), portfolio or HELD_BOOK
    return (trade_idea._sizing_valid(result, sizing, portfolio, policy=EXECUTION_POLICY_V4, mandate=mandate,
                                     accepted_band=accepted_band),
            trade_idea._sizing_band_reason(result, sizing, portfolio, mandate, accepted_band))


def test_add_inside_and_outside_its_band():
    assert _held_valid(_held_proposal("ADD", 2000.0, 600.0, 2954.0)) == (True, None)
    # A BUY on a held position is checked as an ADD (routing normalizes it the same way).
    assert _held_valid(_held_proposal("BUY", 2000.0, 600.0, 2954.0)) == (True, None)
    assert _held_valid(_held_proposal("ADD", 2955.0, 600.0, 2955.0))[1].startswith("fascia copiata diversa")
    assert _held_valid(_held_proposal("ADD", 2955.0, 600.0, 2954.0)) == \
        (False, "importo fuori dalla fascia del motore")
    assert _held_valid(_held_proposal("ADD", 599.0, 600.0, 2954.0)) == \
        (False, "importo fuori dalla fascia del motore")
    # The BUY band of a new name is not the ADD band of a held one.
    assert _held_valid(_held_proposal("ADD", 2000.0, 1800.0, 2954.0))[1].startswith("fascia copiata diversa")


def test_trim_within_the_free_cut_and_sell_needs_the_mandate_conditions():
    assert _held_valid(_held_proposal("TRIM", 900.0, 600.0, 1000.0)) == (True, None)
    for amount in (1001.0, 500.0):
        assert _held_valid(_held_proposal("TRIM", amount, 600.0, 1000.0)) == \
            (False, "importo fuori dalla fascia del motore")
    # A whole sale with switched-on conditions cannot be verified by machine: research.
    valid, reason = _held_valid(_held_proposal("SELL", 4000.0, 600.0, 4000.0))
    assert valid is False and reason.startswith("vendita totale oltre il taglio senza condizioni del mandato")
    # Without conditions in the mandate the sale is the whole position.
    free = dict(mandate=FREE_CUTS)
    assert _held_valid(_held_proposal("SELL", 4000.0, 600.0, 4000.0), **free) == (True, None)
    assert _held_valid(_held_proposal("SELL", 3999.99, 600.0, 4000.0), **free) == (True, None)   # within 0.01
    assert _held_valid(_held_proposal("SELL", 3000.0, 600.0, 4000.0), **free) == \
        (False, "importo fuori dalla fascia del motore")
    assert _held_valid(_held_proposal("TRIM", 3500.0, 600.0, 4000.0), **free) == (True, None)
    assert _held_valid(_held_proposal("TRIM", 4000.0, 600.0, 4000.0), **free) == \
        (False, "importo fuori dalla fascia del motore")
    # A position below the mandate minimum: empty band, SELL of the whole with below_starter.
    tiny = book(held_value=450.0)
    assert _held_valid(_held_proposal("SELL", 450.0, 600.0, 450.0, below=True), portfolio=tiny, **free) == (True, None)
    assert _held_valid(_held_proposal("TRIM", 450.0, 600.0, 450.0, below=True), portfolio=tiny, **free)[0] is False
    # Not held: no sale band.
    assert _valid(_held_proposal("SELL", 1500.0, 600.0, 1500.0))[1].startswith(
        "fascia del motore non disponibile per SELL: l'azione SELL richiede una posizione esistente")
    hold = _held_proposal("HOLD", 900.0, 600.0, 1500.0)
    assert _held_valid(hold) == (False, "nessuna fascia del motore per l'azione HOLD")


def test_refresh_tolerance_applies_to_measured_value_amounts_only():
    """N4 (PM option A, 0.5%): SELL and empty band at their maximum, BUY at its minimum."""
    free = dict(mandate=FREE_CUTS)
    accepted = _held_band("SELL", mandate=FREE_CUTS)                  # 4000
    sell = _held_proposal("SELL", 4000.0, 600.0, 4000.0)
    assert _held_valid(sell, portfolio=book(held_value=4010.0), accepted_band=accepted, **free) == (True, None)
    assert _held_valid(sell, portfolio=book(held_value=4030.0), accepted_band=accepted, **free)[0] is False
    assert _held_valid(sell, portfolio=book(held_value=4010.0), **free)[0] is False       # no refresh, no tolerance
    accepted = _band(measured_sizing(sector=1000.0))                  # empty: 1800 > 1000
    empty = _proposal_result(1000.0, band_max=1000.0, below=True)
    assert _valid(empty, measured_sizing(sector=1004.0), accepted_band=accepted) == (True, None)
    assert _valid(empty, measured_sizing(sector=990.0), accepted_band=accepted)[0] is False
    # The band accepted as empty may become barely non-empty after the move: the copied
    # below_starter proposal stays the accepted one.
    accepted = _band(measured_sizing(sector=1795.0))                  # 1800 > 1795
    edge = _proposal_result(1795.0, band_max=1795.0, below=True)
    assert _valid(edge, measured_sizing(sector=1801.0), accepted_band=accepted) == (True, None)
    accepted = _band()                                                # 1800-3100
    at_min = _proposal_result(1800.0)
    assert _valid(at_min, portfolio=book(5100.0), accepted_band=accepted) == (True, None)   # min now 1803
    assert _valid(at_min, portfolio=book(5100.0))[0] is False
    assert _valid(_proposal_result(1790.0, band_min=1800.0), portfolio=book(5100.0),
                  accepted_band=accepted)[0] is False


def test_held_block_gives_add_and_trim_sell_bands(research_board):
    content = _capture(research_board, EXECUTION_POLICY_V4, sizing=measured_sizing(held=True),
                       portfolio=HELD_BOOK, mandate=MANDATE)["messages"][0]["content"]
    bands = {action: _held_band(action) for action in ("ADD", "TRIM", "SELL")}
    assert json.dumps(bands, ensure_ascii=False, separators=(",", ":")) in content
    assert "SELL: l'intera posizione, amount_eur = band_max_eur" in content
    assert "richiede TUTTE le condizioni del mandato nessun_catalyst_90g" in content
    stale = _capture(research_board, EXECUTION_POLICY_V4, sizing=measured_sizing(held=True),
                     portfolio=book(held_value=HELD_VALUE, price_stale=True), mandate=MANDATE)["messages"][0]["content"]
    assert "SIZING BAND: fascia non disponibile (ADD: posizione non valorizzata" in stale


# --------------------------------------------------------------------------- sizing_valid

def _proposal_result(amount, band_min=1800.0, band_max=3100.0, below=False):
    result = v4_result()
    result["proposal"]["eur_amount"] = amount
    result["proposal"]["sizing"].update(amount_eur=amount, band_min_eur=band_min, band_max_eur=band_max,
                                        below_starter=below)
    return result


def _valid(result, sizing=None, portfolio=None, accepted_band=None):
    sizing, portfolio = sizing or measured_sizing(), portfolio or PORTFOLIO
    return (trade_idea._sizing_valid(result, sizing, portfolio, policy=EXECUTION_POLICY_V4, mandate=MANDATE,
                                     accepted_band=accepted_band),
            trade_idea._sizing_band_reason(result, sizing, portfolio, MANDATE, accepted_band))


def test_v4_sizing_valid_inside_the_band():
    assert _valid(_proposal_result(2400.0)) == (True, None)
    assert _valid(_proposal_result(1800.0)) == (True, None)
    assert _valid(_proposal_result(3100.0)) == (True, None)
    # Copy within the 0.01 EUR tolerance.
    assert _valid(_proposal_result(2400.0, band_min=1800.004, band_max=3100.009)) == (True, None)
    # The mandate is passed: without it there is no band.
    assert trade_idea._sizing_valid(_proposal_result(2400.0), measured_sizing(), PORTFOLIO,
                                    policy=EXECUTION_POLICY_V4) is False


def test_v4_amount_has_no_tolerance_below_the_mandate_minimum():
    # The copy within 0.01 EUR is accepted, the amount 0.01 below the minimum is not.
    assert _valid(_proposal_result(1799.99, band_min=1799.99)) == (False, "importo fuori dalla fascia del motore")
    assert _valid(_proposal_result(3100.01, band_max=3100.01)) == (False, "importo fuori dalla fascia del motore")


@pytest.mark.parametrize("amount", [1500.0, 3100.5, 3200.0])
def test_v4_amount_outside_the_server_band_is_not_operational(amount):
    # The copied band claims room the engine does not give; amount inside the copy.
    result = _proposal_result(amount, band_min=1400.0, band_max=3300.0)
    valid, reason = _valid(result)
    assert valid is False and reason.startswith("fascia copiata diversa")
    honest = _proposal_result(amount)
    assert _valid(honest) == (False, "importo fuori dalla fascia del motore")


def test_v4_copied_band_different_from_the_engine_is_not_operational():
    valid, reason = _valid(_proposal_result(2400.0, band_max=3000.0))
    assert valid is False and reason.startswith("fascia copiata diversa da quella del motore")
    assert "3100" in reason


def test_price_refresh_compares_the_copy_with_the_accepted_band_and_the_amount_with_the_new_one():
    accepted = _band()                               # 1800-3100, given to the Capo
    remeasured = measured_sizing(max_add=3000.0)     # now 1800-3000
    assert _valid(_proposal_result(2400.0), remeasured, accepted_band=accepted) == (True, None)
    assert _valid(_proposal_result(3050.0), remeasured, accepted_band=accepted) == \
        (False, "importo fuori dalla fascia del motore")
    valid, reason = _valid(_proposal_result(2400.0, band_max=3000.0), remeasured, accepted_band=accepted)
    assert valid is False and reason.startswith("fascia copiata diversa da quella accettata")


def test_refresh_accepted_band_is_the_initial_band_and_is_wired_into_the_runner():
    import inspect
    run = {"ticker": TICKER, "analysis_mode": MODE, "execution_policy": EXECUTION_POLICY_V4}
    buy = _proposal_result(2400.0)
    assert trade_idea._refresh_accepted_band(run, buy, measured_sizing(), PORTFOLIO, MANDATE) == _band()
    trim = _held_proposal("TRIM", 900.0, 600.0, 1500.0)
    assert trade_idea._refresh_accepted_band(run, trim, measured_sizing(held=True), HELD_BOOK,
                                             MANDATE) == _held_band("TRIM")
    assert trade_idea._refresh_accepted_band({**run, "execution_policy": EXECUTION_POLICY_V3}, buy,
                                             measured_sizing(), PORTFOLIO, MANDATE) is None
    # Source guard (no offline e2e reaches the /4 price refresh): the runner passes the
    # band of its INITIAL sizing/portfolio to the final verification.
    source = inspect.getsource(trade_idea.execute_trade_idea)
    assert source.count("accepted_band=_refresh_accepted_band(run, result, sizing, portfolio, mandate))") == 1
    # The numeric gate of a /4 run receives the book and mandate (band components, N5).
    assert source.count('**({"portfolio": portfolio, "mandate": mandate}') == 1


def test_final_price_refresh_verification_passes_the_accepted_band(monkeypatch):
    accepted = _band()
    monkeypatch.setattr(trade_idea, "_measure_operational_risk", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(trade_idea, "_compute_sizing", lambda *a, **k: measured_sizing(max_add=3000.0))
    monkeypatch.setattr(trade_idea, "_fx_receipt", lambda portfolio: {"valid": True, "observations": []})
    context = {"accepted_context_sha256": "a", "current_context_sha256": "b", "price_inputs_sha256": "c"}
    fake_store = SimpleNamespace(price_refresh_context=lambda run_id: dict(context))
    def verify(result, band):
        return trade_idea._final_price_refresh_verification(
            fake_store, "run-zz", result, portfolio_loader=lambda: PORTFOLIO, mandate=MANDATE,
            candidate_quote={"status": "ready", "ticker": TICKER, "currency": "EUR"},
            accepted_band=band)["measurements"]["proposal_valid"]
    assert verify(_proposal_result(2400.0), accepted) is True
    assert verify(_proposal_result(2400.0, band_max=3000.0), accepted) is False
    assert verify(_proposal_result(3050.0), accepted) is False


def test_v4_below_starter_only_on_an_empty_band_at_its_maximum():
    empty = measured_sizing(max_add=900.0)
    assert _valid(_proposal_result(900.0, band_max=900.0, below=True), empty) == (True, None)
    assert _valid(_proposal_result(700.0, band_max=900.0, below=True), empty)[1] == \
        "importo fuori dalla fascia del motore"
    text = _proposal_result(900.0, band_max=900.0, below=True)
    text["proposal"]["sizing"]["below_starter"] = "true"
    assert _valid(text, empty)[0] is False
    junk = _proposal_result(2400.0)                  # a non-boolean flag is never accepted
    junk["proposal"]["sizing"]["below_starter"] = "false"
    assert _valid(junk) == (False, "importo fuori dalla fascia del motore")
    # below_starter on a non-empty server band is refused, even copied exactly at its maximum
    # (the server re-checks what the contract already forbids).
    assert _valid(_proposal_result(3100.0, below=True)) == (False, "importo fuori dalla fascia del motore")


def test_v4_without_a_measured_band_names_the_missing_measure():
    valid, reason = _valid(_proposal_result(2400.0), measured_sizing(vol_estimated=True))
    assert valid is False and reason.startswith("fascia del motore non disponibile per BUY: metriche stimate")


def test_the_historical_sizing_check_is_unchanged_for_a_v3_proposal():
    from trade_idea_fixtures import research_result
    result = research_result("favorable")
    result["proposal"].update(ticker=TICKER, action="BUY", eur_amount=3100.0)
    assert "sizing" not in result["proposal"]
    assert trade_idea._sizing_valid(result, measured_sizing(), PORTFOLIO) is True
    result["proposal"]["eur_amount"] = 3101.0
    assert trade_idea._sizing_valid(result, measured_sizing(), PORTFOLIO) is False
    # The historical check has no starter floor.
    result["proposal"]["eur_amount"] = 100.0
    assert trade_idea._sizing_valid(result, measured_sizing(), PORTFOLIO) is True


def test_operational_checks_carry_the_v4_band_receipt(monkeypatch):
    from bellomberg.core.trade_idea_contract import DOSSIER_KEYS
    for name in ("_verified_candidate_valuations", "_require_final_research", "_quality_sufficient",
                 "_candidate_quote_matches", "_bound_evidence"):
        monkeypatch.setattr(trade_idea, name, lambda *a, **k: True)
    board = SimpleNamespace(data={}, analysis_mode=MODE, get_latest=lambda name: None)
    run = {"ticker": TICKER, "company_name": "Synthetic", "exchange": "X", "started_at": "2026-10-01",
           "analysis_mode": MODE, "execution_policy": EXECUTION_POLICY_V4}
    result = _proposal_result(2400.0, band_max=3000.0)
    result["dossier"] = [{"key": key} for key in DOSSIER_KEYS]
    checks = trade_idea._operational_checks(run, result, board, measured_sizing(), PORTFOLIO, MANDATE,
                                            {"ticker": TICKER}, {})
    assert checks["sizing_valid"] is False
    assert checks["sizing_band"]["status"] == "rejected"
    assert checks["sizing_band"]["reason"].startswith("fascia copiata diversa")
    v3 = {**run, "execution_policy": EXECUTION_POLICY_V3}
    assert "sizing_band" not in trade_idea._operational_checks(v3, result, board, measured_sizing(),
                                                               PORTFOLIO, {}, {"ticker": TICKER}, {})


# --------------------------------------------------------------------------- numeric gate

CUTOFF = "2026-10-01T00:00:00+00:00"


def _gate_board(policy=EXECUTION_POLICY_V4):
    output = json.dumps({"data": {"as_of": "2026-09-01", "revenue_eur_m": 1234.5, "ebit_margin": 0.125,
                                  "consensus_growth_pct": 5.8, "net_debt_eur_m": 410.25}})
    receipt = {"success": True, "truncated": False, "input": {"ticker": TICKER},
               "tool": "get_fundamentals", "output": output}
    return SimpleNamespace(tool_receipts=[receipt], execution_policy=policy, analysis_mode=MODE, language="it")


def _gate_result():
    result = v4_result()
    result["evidence"][0]["source"] = "[src: get_fundamentals] synthetic fundamentals"
    business = next(section for section in result["dossier"] if section["key"] == "business")
    business["paragraphs"] = ["I ricavi sono 1.234,5 milioni di euro con un margine operativo del 12,5%; "
                              "il debito netto e' 410,3 milioni."]
    # A scenario's own estimates are exempt only inside that scenario's analysis/method.
    result["scenarios"][1]["analysis"] = ("Nello scenario base, stima del comitato, il prezzo obiettivo e' "
                                          "11,2 EUR con probabilita' del 50%.")
    result["variant_view"][0].update(consensus=5.8, rationale="La stima del comitato di 6,5% supera il consenso di 5,8%.")
    result["pillars"][0]["thesis"] = "Il margine del 12,5% regge."
    for index, pillar in enumerate(result["pillars"]):
        pillar["title"] = ("Primo", "Secondo")[index] + " pilastro sintetico"
    return result


def _unbind_first_pillar(result):
    """Pillar 0 and its memo section (executive) cite only an evidence without receipt."""
    result["evidence"].append({"id": "ev2", "source": "[src: unknown_tool] none", "as_of": "2026-09-01",
                               "summary": "Unbound evidence.", "url": None})
    result["pillars"][0]["evidence_ids"] = ["ev2"]
    next(section for section in result["dossier"] if section["key"] == "executive")["evidence_ids"] = ["ev2"]


def test_v4_gate_reads_italian_numbers_through_field_evidence_ids_and_exempts_estimates():
    assert trade_idea._numeric_claim_gaps(_gate_result(), _gate_board(), TICKER, CUTOFF, {}) == []


def test_v4_gate_flags_an_unattested_number_and_a_missing_consensus():
    result = _gate_result()
    result["pillars"][1]["risk"] = "Un calo al 17,3% di margine romperebbe la tesi."
    result["variant_view"][0]["consensus"] = 7.4
    gaps = trade_idea._numeric_claim_gaps(result, _gate_board(), TICKER, CUTOFF, {})
    assert any(gap.startswith("pillars[1].risk: valore 17,3%") for gap in gaps), gaps
    assert any(gap.startswith("variant_view[0].consensus: valore 7.4") for gap in gaps), gaps


def test_v4_gate_treats_committee_thresholds_as_criteria_and_risks_as_facts():
    """PM 04/10/2026: exit thresholds, falsifiers and trigger conditions are chosen by the committee."""
    result = _gate_result()
    result["risk_exits"][0].update(risk="Il margine scende al 17,3%.", threshold="Margine sotto il 17,3%")
    result["scenarios"][0]["falsifiers"] = ["Margine sotto il 17,3% per due trimestri"]
    gaps = trade_idea._numeric_claim_gaps(result, _gate_board(), TICKER, CUTOFF, {})
    assert any(gap.startswith("risk_exits[0].risk: valore 17,3%") for gap in gaps), gaps
    assert not any(".threshold" in gap or ".falsifiers" in gap for gap in gaps), gaps


def _section(result, key):
    return next(section for section in result["dossier"] if section["key"] == key)


def _gaps(result, board=None):
    return trade_idea._numeric_claim_gaps(result, board or _gate_board(), TICKER, CUTOFF, {})


def test_v4_estimates_are_exempt_only_inside_their_own_field():
    result = _gate_result()
    result["variant_view"][0].update(committee=47.3, rationale="La stima del comitato di 47,3% supera il consenso.")
    assert _gaps(result) == []
    _section(result, "business")["paragraphs"] = ["Il margine osservato nell'ultimo esercizio e' il 47,3%."]
    result["summary"] = "Lo scenario ottimistico vale 14,6 EUR."
    result["scenarios"][0]["analysis"] = "Nel pessimistico il prezzo e' 11,2 EUR."   # base's target
    gaps = _gaps(result)
    assert any(gap.startswith("dossier.business[0]: valore 47,3%") for gap in gaps), gaps
    assert any(gap.startswith("summary: valore 14,6 EUR") for gap in gaps), gaps
    assert any(gap.startswith("scenarios[0].analysis: valore 11,2 EUR") for gap in gaps), gaps


@pytest.mark.parametrize("text, attested", [
    ("Ricavi 1.234,5 milioni.", True),
    ("Ricavi 1,2345 miliardi.", True),
    ("Ricavi di 1.234,5 (milioni di euro).", True),            # bare token: the value as written
    ("Ricavi 1.234,5 miliardi.", False),                        # wrong declared scale
    ("Margine pari a 0,1 miliardi.", False),                    # a ratio never matches a scale word
    ("Ricavi 1.234.500 mila.", True),
    ("Ricavi 1.234.500 migliaia.", True),
    ("Ricavi 1,2345 Mrd.", True),
    ("Ricavi 1.234,5 Mrd.", False),
    ("Ricavi 1.234,5 bilioni.", False),
    ("Ricavi 1.234,5 trilioni.", False),
])
def test_v4_scale_words_compare_only_at_the_declared_scale(text, attested):
    result = _gate_result()
    _section(result, "business")["paragraphs"] = [text]
    assert (_gaps(result) == []) is attested, _gaps(result)


def test_v4_sign_multiples_and_percent_consensus():
    board = _gate_board()
    board.tool_receipts[0]["output"] = board.tool_receipts[0]["output"].replace("410.25", "-410.25")
    result = _gate_result()
    gaps = _gaps(result, board)
    assert any(gap.startswith("dossier.business[0]: valore 410,3 milioni") for gap in gaps), gaps
    _section(result, "business")["paragraphs"] = ["Debito netto di -410,3 milioni."]
    assert _gaps(result, board) == []
    # "12,9x" is 12,9, not 12.
    _section(result, "valuation")["paragraphs"] = ["Il titolo tratta a 12,9x gli utili."]
    assert any("valore 12,9x" in gap for gap in _gaps(result, board))
    board.tool_receipts[0]["output"] = board.tool_receipts[0]["output"].replace("1234.5", "12.9")
    _section(result, "business")["paragraphs"] = ["Debito netto di -410,3 milioni."]
    assert _gaps(result, board) == []
    # A percent consensus row matches a ratio receipt (5,8% vs 0.058), not the bare number.
    board = _gate_board()
    board.tool_receipts[0]["output"] = board.tool_receipts[0]["output"].replace("5.8", "0.058")
    assert _gaps(_gate_result(), board) == []
    result = _gate_result()
    result["variant_view"][0]["unit"] = "EUR"
    result["variant_view"][0]["rationale"] = "La stima del comitato supera il consenso."
    assert any(gap.startswith("variant_view[0].consensus") for gap in _gaps(result, board))


def test_v4_receipt_text_with_english_thousands_is_one_number():
    assert trade_idea._output_numbers("Revenue 1,234.5 million, margin 12.5") == [
        (Decimal("1234.5"), 6), (Decimal("12.5"), 0)]
    assert trade_idea._output_numbers('{"data": {"revenue_eur_m": 1234.5, "note": "debt -410.25"}}') == [
        (Decimal("1234.5"), 6), (Decimal("-410.25"), 0)]
    board = _gate_board()
    board.tool_receipts[0]["output"] = json.dumps(
        {"data": {"as_of": "2026-09-01", "text": "Revenue 1,234.5 million; EBIT margin 12.5%; "
                                                   "consensus growth 5.8; net debt 410.25 million"}})
    assert _gaps(_gate_result(), board) == []


@pytest.mark.parametrize("mutate, location", [
    (lambda r: r.update(decisive_questions=["Il margine del 63,7% reggera'?"]), "decisive_questions[0]"),
    (lambda r: r["proposal"].update(timing="Dopo la trimestrale, con il titolo a 9,87 EUR"), "proposal.timing"),
    (lambda r: r["horizon"].update(label="Orizzonte dei ricavi a 777 milioni"), "horizon.label"),
    (lambda r: r["variant_view"][0].update(metric="Crescita ricavi da 777 milioni"), "variant_view[0].metric"),
    (lambda r: _section(r, "business").update(title="Ricavi a 888 milioni"), "dossier.business.title"),
    (lambda r: _section(r, "valuation")["tables"][0].update(title="Ricavi: 555 milioni"),
     "dossier.valuation.table[0].title"),
    (lambda r: _section(r, "valuation")["tables"][0].update(columns=["Voce", "Ricavi 444 mln"]),
     "dossier.valuation.table[0].columns"),
])
def test_v4_every_text_field_is_checked(mutate, location):
    result = _gate_result()
    mutate(result)
    assert any(gap.startswith(location + ": valore") for gap in _gaps(result)), _gaps(result)


def test_v4_horizon_label_may_repeat_its_own_months():
    result = _gate_result()
    result["horizon"]["label"] = "18 mesi"
    assert _gaps(result) == []


def test_v4_criteria_are_checked_only_where_they_state_the_current_level():
    result = _gate_result()
    result["scenarios"][0]["falsifiers"] = ["Il margine scende sotto il 9%"]
    result["risk_exits"][0]["threshold"] = "Uscire se il debito supera 600 milioni"
    result["review_triggers"] = [{"kind": "condition", "date": None, "price_level": None,
                                  "condition": "Rinnovo del contratto oltre 3 anni", "what": "Conferma."}]
    assert _gaps(result) == []
    result["scenarios"][0]["falsifiers"] = ["Il margine, oggi al 47%, scende sotto il 9%"]
    result["risk_exits"][0]["threshold"] = "Debito attualmente a 777 milioni oltre 900 milioni"
    result["review_triggers"][0]["condition"] = "Il prezzo, che e' al 31,4 EUR, torna sotto 30 EUR"
    # (rule N2: only thresholds/targets are exempt in criteria)
    gaps = _gaps(result)
    assert any(gap.startswith("scenarios[0].falsifiers[0]: valore 47%") for gap in gaps), gaps
    assert not any("valore 9%" in gap for gap in gaps), gaps
    assert any(gap.startswith("risk_exits[0].threshold: valore 777 milioni") for gap in gaps), gaps
    assert any(gap.startswith("review_triggers[0].condition: valore 31,4 EUR") for gap in gaps), gaps


CRITERIA_CLAIMS = [   # rev_l2c: a current level hidden in a criterion must be attested (N2)
    ("Oggi, il margine vale 47,3%: uscire sotto il 40%", "47,3%"),
    ("Il margine, che oggi vale 47,3%, scende sotto il 40%", "47,3%"),
    ("Il margine (47,3% nell'ultimo esercizio) scende sotto il 40%", "47,3%"),
    ("Dal livello odierno del 47,3% a sotto il 40%", "47,3%"),
    ("Il titolo quota 31,4 EUR: uscire sotto 25 EUR", "31,4 EUR"),
    ("Il margine e' del 47,3% e scende sotto il 40%", "47,3%"),
    ("Margine registrato 47,3%; uscire sotto il 40%", "47,3%"),
    ("Oggi, il debito netto ammonta a 777 milioni; sopra 900 si rivede", "777 milioni"),
    ("Con un prezzo corrente di 31,4 EUR, sotto 30 EUR", "31,4 EUR"),
    ("Ridurre se il margine, oggi al 47%, scende sotto il 9%", "47%"),
    ("Uscire se il margine scende sotto il 40%. Il ROE e' 18,2%", "18,2%"),
]


@pytest.mark.parametrize("field", ["threshold", "falsifier", "condition"])
@pytest.mark.parametrize("text, claim", CRITERIA_CLAIMS)
def test_v4_criteria_exempt_only_thresholds_and_targets(field, text, claim):
    result = _gate_result()
    if field == "threshold":
        result["risk_exits"][0]["threshold"] = text
    elif field == "falsifier":
        result["scenarios"][0]["falsifiers"] = [text]
    else:
        result["review_triggers"] = [{"kind": "condition", "date": None, "price_level": None,
                                      "condition": text, "what": "Conferma."}]
    gaps = _gaps(result)
    flagged = [gap.split(": valore ")[1].split(" non attestato")[0] for gap in gaps if ": valore " in gap]
    assert flagged == [claim], gaps


@pytest.mark.parametrize("text", [
    "Margine sotto il 40%", "Uscire se il debito supera 600 milioni", "Prezzo al di sotto di 25 EUR",
    "Debito oltre 900 milioni", "Rivedere se il prezzo e' 30 EUR", "Almeno 3 trimestri di margine < 10%",
    "Ricavi superiori a 1.300 milioni", "Leva >= 3,5x",
])
def test_v4_thresholds_in_criteria_are_exempt(text):
    result = _gate_result()
    result["risk_exits"][0]["threshold"] = text
    assert _gaps(result) == []


@pytest.mark.parametrize("location, text", [
    ("proposal.timing", "Acquisto in 2 tranche nelle prossime 6 settimane"),
    ("proposal.timing", "Entro 10 giorni lavorativi"),
    ("horizon.label", "12-18 mesi"),
    ("horizon.label", "Da 12 a 24 mesi"),
    ("horizon.label", "1,5 anni"),
    ("decisive_questions", "Il margine reggera' sopra il 10%?"),
    ("decisive_questions", "La crescita superera' il 7% annuo?"),
    ("review_triggers.what", "Ridurre del 30% la posizione."),
    ("data_gaps", "Manca il dato del Q3: servono 2 trimestri di storia"),
])
def test_v4_plan_figures_are_admitted_in_plan_fields(location, text):
    result = _gate_result()
    _set_plan_field(result, location, text)
    assert _gaps(result) == []


@pytest.mark.parametrize("location, text, claim", [
    ("proposal.timing", "Acquisto con il margine al 47,3%", "47,3%"),
    ("horizon.label", "24-36 mesi", "24"),
    ("horizon.label", "36 mesi", "36"),
    ("horizon.label", "2 anni", "2"),
    ("horizon.label", "Fino al target di 99,1 EUR", None),          # a target: exempt (N2/N3)
    ("decisive_questions", "Il margine del 63,7% reggera'?", "63,7%"),
    ("review_triggers.what", "Il debito e' 777 milioni: ridurre del 30%", "777 milioni"),
    ("data_gaps", "Il ROE e' 18,2%: manca il dato 2025", "18,2%"),
])
def test_v4_plan_fields_still_attest_claims(location, text, claim):
    result = _gate_result()
    _set_plan_field(result, location, text)
    gaps = _gaps(result)
    if claim is None:
        assert gaps == []
    else:
        assert any(gap.startswith(location.split(".")[0]) and "valore " + claim in gap for gap in gaps), gaps


def _set_plan_field(result, location, text):
    if location == "proposal.timing":
        result["proposal"]["timing"] = text
    elif location == "horizon.label":
        result["horizon"]["label"] = text
    elif location == "decisive_questions":
        result["decisive_questions"] = [text]
    elif location == "review_triggers.what":
        result["review_triggers"] = [{"kind": "date", "date": "2026-11-15", "price_level": None,
                                      "condition": None, "what": text}]
    else:
        result["data_gaps"] = [text]


def test_v4_plan_figures_are_not_exempt_outside_plan_fields():
    result = _gate_result()
    result["pillars"][0]["thesis"] = "Il margine e' salito in 7 settimane."
    assert any(gap.startswith("pillars[0].thesis: valore 7") for gap in _gaps(result))


def test_v4_proposal_pool_is_only_the_proposal_and_the_candidate():
    """N5: the engine JSON of other positions is not a source for the proposal."""
    sizing = measured_sizing()
    result = _gate_result()
    gaps_of = lambda text: [gap for gap in trade_idea._numeric_claim_gaps(
        {**result, "proposal": {**result["proposal"], "rationale": text}}, _gate_board(), TICKER, CUTOFF,
        sizing, portfolio=PORTFOLIO, mandate=MANDATE) if gap.startswith("proposal.")]
    # Amount, copied band, candidate metrics (vol 24%, max add 3.100) and band components.
    assert gaps_of("Importo 2.400 EUR nella fascia 1.800-3.100 EUR, volatilita' 24%, "
                   "cassa sopra la minima 3.800 EUR, NAV 60.000 EUR.") == []
    # Values of OTHER positions (15.000, capacity 2.000) and engine totals are not.
    assert gaps_of("La prima posizione vale 15.000 EUR.")
    assert gaps_of("Spazio residuo di 2.000 EUR.")
    # Without the mandate the band components are not offered.
    assert [gap for gap in trade_idea._numeric_claim_gaps(
        {**result, "proposal": {**result["proposal"], "rationale": "NAV 60.000 EUR."}}, _gate_board(),
        TICKER, CUTOFF, sizing) if gap.startswith("proposal.")]


@pytest.mark.parametrize("text, token", [
    ("Ricavi a 2050 milioni.", "2050 milioni"), ("Il debito netto e' 1980 mln.", "1980 mln"),
    ("Il target e' 2045 € per azione.", "2045 €"), ("Capex di 2030 milioni di euro.", "2030 milioni"),
])
def test_v4_year_shaped_amounts_with_italian_units_are_read(text, token):
    assert [span[0] for span in trade_idea._quantity_spans_v4(text)] == [token]
    result = _gate_result()
    _section(result, "business")["paragraphs"] = [text]
    assert any("valore " + token in gap for gap in _gaps(result))


def test_v4_a_grouped_percentage_is_never_attested_by_x100():
    board = _gate_board()                                  # receipt has 410.25
    result = _gate_result()
    _section(result, "business")["paragraphs"] = ["Quota di mercato 41.025%."]
    assert any("valore 41.025%" in gap for gap in _gaps(result, board))
    # The Italian convention is not ambiguous: '1.250' is 1250 only.
    assert trade_idea._local_number_values("1.250", "it") == {(Decimal("1250"), 0)}
    assert trade_idea._local_number_values("1,250", "en") == {(Decimal("1250"), 0)}


def test_v4_gate_requires_evidence_ids_for_numbers_and_for_estimates():
    result = _gate_result()
    _unbind_first_pillar(result)
    result["scenarios"][0]["evidence_ids"] = []
    gaps = trade_idea._numeric_claim_gaps(result, _gate_board(), TICKER, CUTOFF, {})
    assert "pillars[0].thesis: cifre senza EvidenceID verificati nel campo o nella sezione" in gaps
    assert "scenarios[0]: stima del comitato senza metodo o evidence_ids" in gaps


def test_v4_prose_tag_is_not_a_source_and_v3_gate_keeps_inline_citations():
    result = _gate_result()
    _unbind_first_pillar(result)
    result["pillars"][0]["thesis"] = "Il margine del 12,5% regge [src: get_fundamentals]."
    gaps = trade_idea._numeric_claim_gaps(result, _gate_board(), TICKER, CUTOFF, {})
    assert any(gap.startswith("pillars[0].thesis") for gap in gaps)
    # The same /4 result through a /3 board uses the historical inline-tag gate.
    legacy = trade_idea._numeric_claim_gaps(_gate_result(), _gate_board(EXECUTION_POLICY_V3), TICKER, CUTOFF, {})
    assert any("fonte numerica/tabella senza receipt" in gap for gap in legacy)


# --------------------------------------------------------------------------- store

def v4_request(ticker=TICKER):
    # Z3b 05/10: qualificazione di ricerca VERA (research_required), non piu' il finto 'qualified'.
    from _trade_idea_research_request import research_request
    payload = research_request(request(ticker))
    payload["execution_policy"] = EXECUTION_POLICY_V4
    for selected in payload["models"].values():
        selected["reasoning_effort"] = "medium"
    return payload


def _new_run(current, key="v4-run", payload=None):
    ident = current.create_run(payload or v4_request(), idempotency_key=key)["run"]["id"]
    return ident, current.claim_run(ident)


def test_store_accepts_a_valid_v4_result_and_rejects_a_malformed_or_historical_one(migrated):
    current = store(migrated)
    ident, token = _new_run(current)
    with pytest.raises(ValueError):
        bad = v4_result()
        bad["scenarios"] = bad["scenarios"][:2]
        current.finish_run(ident, token, bad, "completed")
    from trade_idea_fixtures import research_result
    historical = research_result("watch")
    historical = {k: v for k, v in historical.items() if k not in ("run_id", "run_type", "pm_view", "destination")}
    historical["ticker"] = TICKER
    with pytest.raises(ValueError):
        current.finish_run(ident, token, historical, "completed")
    data = v4_result("watch")
    current.finish_run(ident, token, deepcopy(data), "completed")
    stored = current.get_run(ident)["result"]
    assert {k: v for k, v in stored.items() if k not in ("run_id", "run_type", "pm_view")} == data


def test_store_routes_a_v4_proposal_outside_the_band_to_research_with_the_reason(migrated):
    current = store(migrated)
    ident, token = _new_run(current)
    current.finish_run(ident, token, v4_result(), "completed")
    checks = {name: True for name in ("identity_verified", "evidence_sufficient", "red_team_complete",
                                      "capo_valid", "mandate_valid", "research_reviewed",
                                      "history_context_sent")}
    checks.update(sizing_valid=False, sizing_band={"status": "rejected",
                                                   "reason": "importo fuori dalla fascia del motore"})
    routed = current.route_result(ident, checks)
    assert routed["kind"] == "research"
    assert "Sizing /4: importo fuori dalla fascia del motore" in routed["reason"]


def test_store_v4_band_receipt_is_the_size_source(migrated):
    current = store(migrated)
    ident, token = _new_run(current)
    data = v4_result()
    data["proposal"]["sizing_source"] = None
    current.finish_run(ident, token, data, "completed")
    checks = {name: True for name in ("identity_verified", "evidence_sufficient", "red_team_complete",
                                      "capo_valid", "mandate_valid", "research_reviewed",
                                      "history_context_sent", "sizing_valid")}
    routed = current.route_result(ident, {**checks, "sizing_band": {"status": "ok", "reason": None}})
    assert "sizing source required" not in routed["reason"], routed


def test_store_v3_still_requires_the_sizing_source(migrated):
    from test_trade_idea_store import result as v3_result
    current = store(migrated)
    from _trade_idea_research_request import research_request
    payload = research_request(request("TEST"), archive_root=migrated.parent / "research-archive")
    payload["execution_policy"] = EXECUTION_POLICY_V3
    for selected in payload["models"].values():
        selected["reasoning_effort"] = "medium"
    ident, token = _new_run(current, "v3-run", payload)
    data = v3_result()
    data["proposal"]["sizing_source"] = None
    current.finish_run(ident, token, data, "completed")
    routed = current.route_result(ident, {"sizing_band": {"status": "ok", "reason": None}})
    assert "sizing source required" in routed["reason"]


MEMO_ROW = {"ticker": "SYNTH-EXT", "id": "run-zz", "view_text": "v", "created_at": "2026-10-04T00:00:00Z"}
# Measured on the unmodified store (HEAD) with trade_idea_fixtures.research_result.
FROZEN_MEMO_SHA256 = {
    ("it", "favorable"): "13fe44fedf468c2276d3203a5388c85bf482ebf95fb2090b1f55bb4d1dd5eec4",
    ("it", "watch"): "e64037bd46a21afc209d03327d6ef7ad6399c983ed9e1c506d8a7c6efc25be78",
    ("en", "favorable"): "b00c606af46e8177b8dda7d50f814e821ff3467e0d6756a98a62d2e0f10c4df3",
    ("en", "watch"): "03da562a957e451cf04683c7bc0458f80aa72fcd85740b0070ebc2adc2e67101",
}


@pytest.mark.parametrize("key", sorted(FROZEN_MEMO_SHA256))
def test_archive_memo_of_v3_results_is_unchanged(key):
    from trade_idea_fixtures import research_result
    text = store_module.TradeIdeaStore._memo_markdown({**MEMO_ROW, "language": key[0]}, research_result(key[1]))
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == FROZEN_MEMO_SHA256[key]


def test_archive_memo_of_a_v4_result_carries_the_memo_fields():
    data = v4_result()
    data["variant_view"][0].update(consensus=5.8, committee=6.5)
    data["risk_exits"][0].update(threshold="Margine sotto il livello sintetico", action="exit")
    text = store_module.TradeIdeaStore._memo_markdown({**MEMO_ROW, "language": "it"}, data)
    for needle in ("## Convinzione e orizzonte", "Convinzione: MEDIA | Orizzonte: 18 mesi (Diciotto mesi (sintetico))",
                   "## Pilastri della tesi", "### Pillar 1", "Rischio: Synthetic risk.",
                   "## Stime del comitato contro il consenso",
                   "- Revenue growth (FY synthetic, %): consenso 5.8; stima del comitato 6.5.",
                   "## Rischi, soglie e azioni", "Soglia: Margine sotto il livello sintetico | Azione: uscire",
                   "### Pessimistico", "Probabilita stimata dal comitato: 25% | Prezzo obiettivo stimato: 8.4 EUR",
                   "### Ottimistico", "- Falsificatore: Synthetic falsifier",
                   "## Trigger di revisione", "- date: 2026-11-15 — Synthetic results date."):
        assert needle in text, needle
    data["variant_view"][0]["consensus"] = None
    assert "consenso consenso non disponibile" in store_module.TradeIdeaStore._memo_markdown(
        {**MEMO_ROW, "language": "it"}, data)


def test_store_v4_routing_without_a_band_receipt_names_the_missing_check(migrated):
    current = store(migrated)
    ident, token = _new_run(current)
    current.finish_run(ident, token, v4_result(), "completed")
    routed = current.route_result(ident, {})
    assert routed["kind"] == "research"
    assert "Sizing /4: verifica della fascia del motore assente" in routed["reason"]


def test_server_incomplete_package_of_a_v4_run_is_declared_and_accepted_only_as_incomplete(
        migrated, research_board):
    current = store(migrated)
    ident, token = _new_run(current)
    run = {**current.get_run(ident)["run"]}
    assert run["execution_policy"] == EXECUTION_POLICY_V4
    package = trade_idea._incomplete_capo_result(run, research_board, "Capo output invalid (synthetic)")
    assert package["result_origin"] == store_module.SERVER_INCOMPLETE_ORIGIN
    assert package["judgment"] == "incomplete" and package["proposal"] is None
    # The historical gate (not the /4 one) reads its desk excerpts.
    research_board.execution_policy = EXECUTION_POLICY_V4
    assert isinstance(trade_idea._numeric_claim_gaps(package, research_board, run["ticker"], CUTOFF, {}), list)
    current.finish_run(ident, token, package, "incomplete", reason="synthetic")
    assert current.get_run(ident)["result"]["result_origin"] == "server_incomplete"


@pytest.mark.parametrize("mutation, policy", [
    ("judgment", EXECUTION_POLICY_V4), ("origin", EXECUTION_POLICY_V4), ("marker", EXECUTION_POLICY_V3)])
def test_server_incomplete_origin_cannot_smuggle_a_verdict_or_reach_another_policy(research_board, mutation, policy):
    run = {"id": "run-zz", "ticker": "TEST", "view_text": "Original PM view", "language": "it",
           "analysis_mode": MODE, "execution_policy": EXECUTION_POLICY_V4}
    package = trade_idea._incomplete_capo_result(run, research_board, "synthetic")
    source = {k: v for k, v in package.items() if k not in ("run_id", "run_type", "pm_view")}
    if mutation == "judgment":
        source["judgment"] = "watch"
    elif mutation == "origin":
        source["result_origin"] = "capo"
    with pytest.raises(ValueError):
        store_module.validate_run_result(source, run_id="run-zz", ticker="TEST",
                                         pm_view="Original PM view", policy=policy)


def test_server_incomplete_package_of_v3_runs_is_unchanged(research_board):
    run = {"id": "run-zz", "ticker": "TEST", "view_text": "Original PM view", "language": "it",
           "analysis_mode": MODE, "execution_policy": EXECUTION_POLICY_V3}
    assert "result_origin" not in trade_idea._incomplete_capo_result(run, research_board, "synthetic")


@pytest.mark.parametrize("shape", ["v4", "historical"])
def test_paid_v4_capo_response_is_reused_and_a_historical_shape_is_refused(migrated, monkeypatch, shape):
    from trade_idea_fixtures import research_result
    monkeypatch.setattr(paid, "v2_request", lambda: v4_request("TEST"))
    monkeypatch.setattr(paid, "research_result", (lambda judgment: {**v4_result(judgment), "ticker": "TEST"})
                        if shape == "v4" else research_result)
    current, original, _, _, response = paid.case(migrated, record_public=True)
    successor, token = paid.child(current, original, "resume-paid-v4")
    assert current.get_run(successor)["run"]["execution_policy"] == EXECUTION_POLICY_V4
    if shape == "v4":
        assert current.reusable_capo_response(successor, token) == response
    else:
        with pytest.raises(store_module.RunConflict, match="public contract"):
            current.reusable_capo_response(successor, token)
    assert current.get_run(successor)["cost"]["requests"] == 1


# --------------------------------------------------------------------------- Capo acceptance

def _stream_returning(text):
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_final_message(self):
            return response
    return SimpleNamespace(messages=SimpleNamespace(stream=lambda **_kwargs: Stream()))


@pytest.mark.parametrize("shape", ["v4", "historical"])
def test_v4_capo_response_is_validated_on_the_v4_model(research_board, shape):
    from trade_idea_fixtures import research_result
    research_board.execution_policy = EXECUTION_POLICY_V4
    research_board.budget_gate.reuse_capo_response = lambda: None
    research_board.write = lambda key, round_n, content: research_board.data.setdefault(key, {}).__setitem__(round_n, content)
    if shape == "v4":
        payload = {**v4_result("watch"), "ticker": "TEST"}
    else:
        payload = {k: v for k, v in research_result("watch").items()
                   if k not in ("run_id", "run_type", "pm_view", "destination")}
        payload.update(ticker="TEST", valuation_refs=[], model_review=None)
    client = _stream_returning(json.dumps(payload))
    if shape == "v4":
        result = trade_idea.run_trade_idea_capo(research_board, portfolio={}, mandate={}, client=client)
        assert {k: v for k, v in result.items() if k not in ("run_id", "run_type", "pm_view")} == payload
    else:
        with pytest.raises(ValueError):
            trade_idea.run_trade_idea_capo(research_board, portfolio={}, mandate={}, client=client)


# --------------------------------------------------------------------------- finalization grant

def test_v4_capo_finalization_grant_keeps_the_accepted_medium_effort(migrated, monkeypatch):
    monkeypatch.setattr(paid, "v2_request", lambda: v4_request("TEST"))
    current, parent, _, body, _ = paid.case(migrated, public=" ", stop_reason="max_tokens")
    child = current.create_continuation(parent, idempotency_key="v4-grant", authorize_new_requests=True,
                                        capo_finalization_request_id="original-paid-capo")
    accepted = current.accepted_capo_finalization(child["run"]["id"])
    assert accepted["thinking"] == {"type": "effort", "effort": "medium"}
    assert accepted["max_tokens"] == 128000


def test_v4_evidence_cited_only_by_a_memo_field_is_bound_to_its_receipt():
    board = _gate_board()
    board.tool_receipts.append({"success": True, "truncated": False, "input": {"ticker": TICKER},
                                "tool": "get_consensus", "output": json.dumps(
                                    {"data": {"as_of": "2026-09-02", "revenue_growth_consensus_pct": 5.9}})})
    result = _gate_result()
    result["evidence"].append({"id": "ev3", "source": "[src: get_consensus] synthetic consensus",
                               "as_of": "2026-09-02", "summary": "Consensus growth.", "url": None})
    result["variant_view"][0].update(consensus=5.9, evidence_ids=["ev3"],
                                     rationale="La stima del comitato supera il consenso.")
    assert trade_idea._numeric_claim_gaps(result, board, TICKER, CUTOFF, {}) == []
