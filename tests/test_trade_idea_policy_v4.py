"""trade-idea-research/4 (PM, Lotto 2): the investment-memo contract.

The /2-/3 prompts and the historical result model/schema are frozen by sha256:
paid runs are resumed by comparing the request sha256 and parsed == model_dump().
All data below is synthetic (ticker ZZTEST.MI, invented numbers).
"""
import hashlib
import json
from copy import deepcopy

import pytest

from bellomberg.core import trade_idea_contract as contract
from bellomberg.core.trade_idea_contract import (
    CAPO_RESEARCH_INSTRUCTIONS_V4, TRADE_IDEA_RESULT_SCHEMA_V4, TradeIdeaResultV4,
    trade_idea_result_model, trade_idea_result_schema, validate_result)
from bellomberg.core.trade_idea_policy import (
    CURRENT_EXECUTION_POLICY, EXECUTION_POLICY_V3, EXECUTION_POLICY_V4, RESEARCH_POLICIES,
    output_cap, role_effort)

TICKER = "ZZTEST.MI"
V4 = {"analysis_mode": "fundamentals_research_v1", "execution_policy": EXECUTION_POLICY_V4}

# Measured on the unmodified file before the /4 change (HEAD 902bab1).
FROZEN_TEXT_SHA256 = {
    "CAPO_RESEARCH_INSTRUCTIONS": "0dda2caf08fe5aa17214d637487ad936b6bc1b096f1a7e63b39d2f7097f82aee",
    "CAPO_RESEARCH_INSTRUCTIONS_V2": "5a4a3101edb129d7767a013401fc008ea2098c9d1c64c38fc7f479c2236dad1b",
    "CAPO_RESEARCH_INSTRUCTIONS_V3": "d0703bcdef241c1e4bb15cf610296e2ecd6004deae8706e1335f70357c490d41",
    "CAPO_TRADE_IDEA_INSTRUCTIONS": "dc75548df08fec15532f4203f4ac66a2619ab0163269ef8fcf377dcf17243b63",
}
FROZEN_RESULT_SCHEMA_SHA256 = "2b312cf8a165255e182ddc9594300f59487682f6a11c4d2d27de299832dc7e25"
FROZEN_RESULT_MODEL_SCHEMA_SHA256 = "47081b872ba0cdcc13cafd740e11dc2fc9e0398efc1deb05fb57a8ed7cf2f447"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pytest.mark.parametrize("name", sorted(FROZEN_TEXT_SHA256))
def test_historical_prompts_are_byte_identical(name):
    assert _sha(getattr(contract, name)) == FROZEN_TEXT_SHA256[name]


def test_historical_result_schema_and_model_are_byte_identical():
    assert _sha(_canonical(contract.TRADE_IDEA_RESULT_SCHEMA)) == FROZEN_RESULT_SCHEMA_SHA256
    assert _sha(_canonical(contract.TradeIdeaResult.model_json_schema())) == FROZEN_RESULT_MODEL_SCHEMA_SHA256


def test_v4_is_current_with_v3_efforts_and_capo_room():
    assert CURRENT_EXECUTION_POLICY == EXECUTION_POLICY_V4 == "trade-idea-research/4"
    assert EXECUTION_POLICY_V4 in RESEARCH_POLICIES and EXECUTION_POLICY_V3 in RESEARCH_POLICIES
    roles = ("specialist", "red_team", "capo", "aux")
    v3 = {**V4, "execution_policy": EXECUTION_POLICY_V3}
    assert {r: role_effort(V4, r) for r in roles} == {r: role_effort(v3, r) for r in roles}
    assert output_cap(V4, "capo", 0) == 128000 and output_cap(V4, "specialist", 64000) == 64000


def test_model_and_schema_selection_by_policy():
    assert trade_idea_result_model(EXECUTION_POLICY_V4) is TradeIdeaResultV4
    for policy in (None, EXECUTION_POLICY_V3, "trade-idea-research/2"):
        assert trade_idea_result_model(policy) is contract.TradeIdeaResult
        assert trade_idea_result_schema(policy) is contract.TRADE_IDEA_RESULT_SCHEMA
    assert trade_idea_result_schema(EXECUTION_POLICY_V4) is TRADE_IDEA_RESULT_SCHEMA_V4
    with pytest.raises(ValueError, match="Unknown Trade Idea execution policy"):
        trade_idea_result_model("trade-idea-research/99")


def test_v4_prompt_has_no_inline_citation_instruction_and_the_memo_rules():
    text = CAPO_RESEARCH_INSTRUCTIONS_V4
    assert "[src:" not in text and "[evidence:" not in text
    assert "Do not write source tags, citation brackets or evidence markers inside the prose" in text
    for needle in ("conviction", "bear, base and bull", "below_starter true", "SIZING BAND",
                   "consensus is the sourced market consensus or null", "charts must be []",
                   "pros, cons, risks and invalidation must be []", "never an automatic order",
                   "12,5% and 1.250", "valuation_refs must be [] and model_review null"):
        assert needle in text, needle


def test_v4_schema_requires_every_new_field_on_the_wire():
    required = set(TRADE_IDEA_RESULT_SCHEMA_V4["required"])
    assert {"conviction", "horizon", "summary_evidence_ids", "pm_view_evidence_ids", "pillars",
            "variant_view", "risk_exits", "review_triggers", "proposal"} <= required
    defs = TRADE_IDEA_RESULT_SCHEMA_V4["$defs"]
    assert set(defs["ProposalV4"]["required"]) >= {"sizing", "eur_amount"}
    assert defs["VariantViewV4"]["required"] == list(defs["VariantViewV4"]["properties"])
    def keys(node):
        if isinstance(node, dict):
            yield from node
            for value in node.values():
                yield from keys(value)
        elif isinstance(node, list):
            for value in node:
                yield from keys(value)
    assert "default" not in set(keys(TRADE_IDEA_RESULT_SCHEMA_V4))


# --------------------------------------------------------------------------- fixture

PARA = ("Synthetic Testing Components (ZZTEST.MI) is an invented issuer used only to exercise "
        "the memo contract; this paragraph carries no claim about any real company.")


def v4_result(judgment="favorable", *, proposal=True):
    sections = [{"key": key, "title": key.replace("_", " ").title(), "paragraphs": [PARA],
                 "evidence_ids": ["ev1"], "tables": [], "charts": []}
                for key in contract.DOSSIER_KEYS]
    sections[4]["tables"] = [{"title": "Synthetic table", "columns": ["Metric", "Value"],
                              "rows": [["Margin", "12,5%"]], "source": "synthetic_tool",
                              "unit": "percent", "period": "FY synthetic"}]
    scenario = lambda name, prob, target: {
        "name": name, "analysis": PARA, "evidence_ids": ["ev1"], "probability_pct": prob,
        "price_target": target, "currency": "EUR", "drivers": ["Synthetic driver"],
        "falsifiers": ["Synthetic falsifier"], "method": "Synthetic multiple on invented earnings."}
    data = {
        "ticker": TICKER, "judgment": judgment, "summary": "Synthetic summary.",
        "pm_view_response": "Synthetic response to the PM thesis.",
        "pros": [], "cons": [], "risks": [], "catalysts": ["Synthetic event"], "invalidation": [],
        "data_gaps": [], "review_conditions": ["Synthetic condition"],
        "scenarios": [scenario("bear", 25, 8.4), scenario("base", 50, 11.2), scenario("bull", 25, 14.6)],
        "objections": [{"objection": "Synthetic objection.", "response": "Synthetic answer.",
                        "resolved": True, "evidence_ids": ["ev1"]}],
        "history_review": [], "valuation_refs": [],
        "evidence": [{"id": "ev1", "source": "[src: synthetic_tool] invented", "as_of": "2026-09-01",
                      "summary": "Invented evidence for the contract test.", "url": None}],
        "dossier": sections,
        "proposal": None, "decisive_questions": ["Synthetic question?"], "model_review": None,
        "conviction": "MEDIA", "horizon": {"months": 18, "label": "Diciotto mesi (sintetico)"},
        "summary_evidence_ids": ["ev1"], "pm_view_evidence_ids": ["ev1"],
        "pillars": [{"title": f"Pillar {i}", "thesis": "Synthetic thesis.", "evidence": "Synthetic evidence.",
                     "risk": "Synthetic risk.", "evidence_ids": ["ev1"]} for i in (1, 2)],
        "variant_view": [{"metric": "Revenue growth", "period": "FY synthetic", "unit": "%",
                          "consensus": None, "committee": 6.5, "rationale": "Committee estimate.",
                          "evidence_ids": ["ev1"]}],
        "risk_exits": [{"risk": "Synthetic risk.", "threshold": "Margin below an invented level",
                        "action": "reduce", "evidence_ids": ["ev1"]}],
        "review_triggers": [{"kind": "date", "date": "2026-11-15", "price_level": None,
                             "condition": None, "what": "Synthetic results date."}],
    }
    if judgment == "favorable" and proposal:
        data["proposal"] = {"ticker": TICKER, "action": "BUY", "eur_amount": 2400.0,
                            "timing": "Synthetic timing", "confidence": "MEDIA",
                            "rationale": "Synthetic rationale", "sizing_source": "synthetic band",
                            "sizing": {"amount_eur": 2400.0, "band_min_eur": 1800.0, "band_max_eur": 3100.0,
                                       "basis": "Inside the synthetic server band.", "below_starter": False}}
    return data


def _v4(data):
    return validate_result(data, run_id="run-zz", ticker=TICKER, execution_policy=EXECUTION_POLICY_V4)


@pytest.mark.parametrize("judgment", ["favorable", "watch", "rejected", "incomplete"])
def test_v4_valid_result_roundtrips_exactly(judgment):
    data = v4_result(judgment)
    model = TradeIdeaResultV4.model_validate(json.loads(json.dumps(data)))
    assert TradeIdeaResultV4.model_validate(json.loads(json.dumps(model.model_dump()))).model_dump() == model.model_dump()
    out = _v4(json.dumps(data))
    assert {k: v for k, v in out.items() if k not in {"run_id", "run_type", "pm_view"}} == data


def test_v4_result_is_rejected_by_the_historical_contract():
    with pytest.raises(ValueError):
        validate_result(v4_result(), run_id="run-zz", ticker=TICKER)


def _rejected(data, match):
    with pytest.raises(ValueError, match=match):
        _v4(data)


def test_probabilities_must_sum_to_100_within_half_point():
    data = v4_result()
    data["scenarios"][1]["probability_pct"] = 50.4
    _v4(data)
    data["scenarios"][1]["probability_pct"] = 51
    _rejected(data, "sum to 100")


def test_scenarios_are_exactly_bear_base_bull():
    data = v4_result()
    data["scenarios"][2]["name"] = "base"
    _rejected(data, "bear, base and bull")
    data = v4_result()
    data["scenarios"] = data["scenarios"][:2]
    _rejected(data, "scenarios")


@pytest.mark.parametrize("trigger", [
    {"kind": "date", "date": None, "price_level": None, "condition": None, "what": "x"},
    {"kind": "date", "date": "2026-11-15", "price_level": 9.1, "condition": None, "what": "x"},
    {"kind": "date", "date": "2026-02-30", "price_level": None, "condition": None, "what": "x"},
    {"kind": "price", "date": None, "price_level": None, "condition": "x", "what": "x"},
    {"kind": "condition", "date": None, "price_level": None, "condition": None, "what": "x"},
])
def test_incoherent_review_triggers_are_rejected(trigger):
    data = v4_result()
    data["review_triggers"] = [trigger]
    _rejected(data, "Review trigger")


def test_coherent_price_and_condition_triggers_pass():
    data = v4_result()
    data["review_triggers"] = [
        {"kind": "price", "date": None, "price_level": 9.1, "condition": None, "what": "Synthetic level."},
        {"kind": "condition", "date": None, "price_level": None, "condition": "Synthetic contract renewal",
         "what": "Synthetic confirmation."}]
    _v4(data)


def test_watch_without_triggers_is_rejected():
    data = v4_result("watch")
    data["review_triggers"] = []
    _rejected(data, "watch judgment requires")
    data = v4_result("rejected")
    data["review_triggers"] = []
    _v4(data)


@pytest.mark.parametrize("amount, band_min, band_max, eur", [
    (3200.0, 1800.0, 3100.0, 3200.0),   # above the band
    (1500.0, 1800.0, 3100.0, 1500.0),   # below a non-empty band without below_starter
    (2400.0, 1800.0, 3100.0, 2500.0),   # eur_amount differs from sizing
])
def test_sizing_outside_band_is_rejected(amount, band_min, band_max, eur):
    data = v4_result()
    data["proposal"]["eur_amount"] = eur
    data["proposal"]["sizing"].update(amount_eur=amount, band_min_eur=band_min, band_max_eur=band_max)
    _rejected(data, "Sizing|eur_amount")


def test_below_starter_only_for_empty_band_at_band_max_with_basis():
    data = v4_result()
    sizing = {"amount_eur": 900.0, "band_min_eur": 1800.0, "band_max_eur": 900.0,
              "basis": "Band empty: the maximum allowed is below the synthetic starter.", "below_starter": True}
    data["proposal"].update(eur_amount=900.0, sizing=sizing)
    _v4(deepcopy(data))
    bad = deepcopy(data)
    bad["proposal"]["sizing"]["amount_eur"] = bad["proposal"]["eur_amount"] = 700.0
    _rejected(bad, "band maximum")
    bad = deepcopy(data)
    bad["proposal"]["sizing"]["basis"] = "   "
    _rejected(bad, "basis")
    bad = deepcopy(data)
    bad["proposal"]["sizing"].update(band_min_eur=600.0)
    _rejected(bad, "empty sizing band")
    bad = deepcopy(data)
    bad["proposal"]["sizing"]["below_starter"] = False
    _rejected(bad, "outside the server band")


def test_price_targets_are_ordered_and_share_one_currency():
    data = v4_result()
    data["scenarios"][0]["price_target"] = 11.9          # bear above base
    _rejected(data, "ordered bear <= base <= bull")
    data = v4_result()
    data["scenarios"][2]["price_target"] = 10.3          # bull below base
    _rejected(data, "ordered bear <= base <= bull")
    data = v4_result()
    data["scenarios"][1]["currency"] = "USD"
    _rejected(data, "one currency")
    data = v4_result()
    data["scenarios"] = list(reversed(data["scenarios"]))  # order of the list is free
    _v4(data)


def test_proposal_confidence_must_equal_conviction():
    data = v4_result()
    data["proposal"]["confidence"] = "ALTA"
    _rejected(data, "confidence must equal the memo conviction")
    data["conviction"] = "ALTA"
    _v4(data)


def test_proposal_requires_sizing():
    data = v4_result()
    del data["proposal"]["sizing"]
    _rejected(data, "sizing")


def test_charts_and_flat_lists_are_rejected():
    data = v4_result()
    data["dossier"][4]["charts"] = [{"table_index": 0, "kind": "bar", "label_column": 0, "value_columns": [1]}]
    _rejected(data, "charts")
    for field in ("pros", "cons", "risks", "invalidation"):
        data = v4_result()
        data[field] = ["Synthetic item"]
        _rejected(data, field)


def test_missing_conviction_and_unresolved_ids_are_rejected():
    data = v4_result("rejected")
    del data["conviction"]
    _rejected(data, "conviction")
    data = v4_result()
    data["pillars"][0]["evidence_ids"] = ["ghost"]
    _rejected(data, "Unresolved evidence")
    data = v4_result()
    data["variant_view"][0]["evidence_ids"] = []
    _rejected(data, "evidence_ids")
    data = v4_result()
    data["pillars"] = data["pillars"][:1]
    _rejected(data, "pillars")


@pytest.mark.parametrize("policy", [None, EXECUTION_POLICY_V3])
def test_a_v3_fixture_result_still_validates_unchanged(policy):
    from trade_idea_fixtures import research_result
    stored = research_result("favorable")
    wire = {k: v for k, v in stored.items() if k not in {"run_id", "run_type", "pm_view", "destination"}}
    out = validate_result(wire, run_id="run-zz", ticker="SYNTH-EXT", execution_policy=policy)
    assert {k: v for k, v in out.items() if k not in {"run_id", "run_type", "pm_view"}} == wire
    with pytest.raises(ValueError):
        validate_result(wire, run_id="run-zz", ticker="SYNTH-EXT", execution_policy=EXECUTION_POLICY_V4)
