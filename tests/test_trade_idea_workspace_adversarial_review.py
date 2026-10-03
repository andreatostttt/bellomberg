"""Independent review probes: no workspace production files are modified here."""
from datetime import datetime, timezone
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from test_trade_idea_workspace import research, action, db


def variant(workspace, run, model):
    return action(workspace, run, model, "simulate", {
        "label": "Independent review scenario", "rationale": "Explicit personal discount-rate sensitivity",
        "changes": [{"scenario": "base", "driver": "wacc", "value": .12}]})["data"]["model"]


def test_monitor_cannot_silently_move_to_another_generation(research):
    workspace, current, run, model = research
    workspace.clock = lambda: datetime.fromisoformat(model["valuation_date"]).replace(tzinfo=timezone.utc)
    alternative = variant(workspace, run, model)
    threshold = (model["fair_value_base"] + alternative["fair_value_base"]) / 2
    rule = action(workspace, run, model, "monitor", {
        "metric": "fair_value_base", "operator": "lt", "threshold": threshold, "active": True})
    baseline = action(workspace, run, model, "monitor_check", {"monitor_id": rule["id"]})
    assert baseline["data"]["alert"] is False
    with pytest.raises(ValueError, match="generation|version|bound"):
        action(workspace, run, alternative, "monitor_check", {"monitor_id": rule["id"]})


def test_frozen_earnings_cannot_be_revised_against_another_generation(research):
    workspace, current, run, model = research
    workspace.clock = lambda: datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
    frozen = action(workspace, run, model, "earnings_prepare", {"event_date": "2027-02-01"})
    expected = next(row for row in frozen["data"]["expectations"] if row["driver"] == "revenue")
    alternative = variant(workspace, run, model)
    workspace.clock = lambda: datetime(2027, 2, 2, 12, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="generation|version|bound"):
        action(workspace, run, alternative, "earnings_review", {
            "preparation_id": frozen["id"],
            "observations": [dict(expected, value=.35, published_at="2027-02-01", source="https://example.org/results")],
            "changes": [{"scenario": "base", "driver": "wacc", "value": .14}],
            "rationale": "A revision must follow the exact frozen model"})


def test_unknown_earnings_url_is_not_a_verified_observed_result(research):
    workspace, current, run, model = research
    workspace.clock = lambda: datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
    frozen = action(workspace, run, model, "earnings_prepare", {"event_date": "2027-02-01"})
    expected = next(row for row in frozen["data"]["expectations"] if row["driver"] == "revenue")
    workspace.clock = lambda: datetime(2027, 2, 2, 12, tzinfo=timezone.utc)
    try:
        reviewed = action(workspace, run, model, "earnings_review", {
            "preparation_id": frozen["id"],
            "observations": [dict(expected, value=.99, published_at="2027-02-01",
                                  source="https://nonexistent.example.invalid/forged-results")],
        })
    except ValueError:
        return
    proof = reviewed["data"]
    assert proof.get("observation_status") in ("unverified_pm_input", "insufficient_evidence"), proof
    assert not proof.get("differences"), "Unverified PM numbers were recorded as observed results"


def test_variant_sidecar_tampering_cannot_remain_a_ready_model(research):
    workspace, current, run, model = research
    alternative = variant(workspace, run, model)
    sidecar_path = Path(alternative["path"]).with_suffix(".payload.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["fair_value_bear"] = 987654321.
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    selected = workspace.view(run, alternative["generation_id"])
    assert selected["model"]["status"] == "unavailable", selected["model"]


def test_baseline_model_check_is_not_later_error_evidence(research):
    workspace, current, run, model = research
    workspace.clock = lambda: datetime.fromisoformat(model["valuation_date"]).replace(tzinfo=timezone.utc)
    rule = action(workspace, run, model, "monitor", {
        "metric": "fair_value_base", "operator": "lt", "threshold": model["fair_value_base"] / 2,
        "active": True})
    checked = action(workspace, run, model, "monitor_check", {"monitor_id": rule["id"]})
    assert checked["data"]["observed_at"] == model["valuation_date"]
    with pytest.raises(ValueError, match="later|new|subsequent|observation"):
        action(workspace, run, model, "error_attribution", {
            "category": "data", "evidence_event_id": checked["id"],
            "rationale": "The monitor checked the unchanged initial model, not subsequent facts"})


def test_simulation_idempotence_survives_concurrent_requests(research, monkeypatch):
    workspace, current, run, model = research
    computations = []
    recalculate = workspace._recalculate
    def measured(*args, **kwargs):
        computations.append(1)
        return recalculate(*args, **kwargs)
    monkeypatch.setattr(workspace, "_recalculate", measured)
    identity = str(uuid4())
    data = {"label": "Concurrent review", "rationale": "One deterministic recalculation",
            "changes": [{"scenario": "base", "driver": "wacc", "value": .12}]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: action(workspace, run, model, "simulate", data, identity), range(4)))
    assert len(computations) == 1
    assert all(result == results[0] for result in results)
    assert len(workspace.events.list(run)) == 1
    assert current.get_run(run)["cost"]["requests"] == 0
