"""Bulk earnings: errors, local omission and remote coverage stay distinguishable."""
import json

import pytest

from bellomberg.agents import chat_tools
from bellomberg.market_data import finnhub_news


def _payload(monkeypatch, events, reasons=()):
    def fetch(days_ahead=14, motivo=None):
        if motivo is not None:
            motivo.extend(reasons)
        return events
    monkeypatch.setattr(finnhub_news, "fetch_earnings_calendar", fetch)
    return chat_tools.dispatch("get_earnings_calendar", {"days_ahead": 21})


@pytest.mark.parametrize("reason", ["403 forbidden", "429 rate limit", "network unavailable"])
def test_bulk_failure_is_not_a_successful_empty_calendar(monkeypatch, reason):
    result = _payload(monkeypatch, [], [reason])
    assert result["error"]
    data = result["data"]
    assert data["status"] == "unavailable"
    assert data["fonti_mute"]["finnhub"] == reason
    assert data["items"] == [] and data["count"] == 0
    assert data["coverage_complete"] is False


def test_valid_empty_bulk_does_not_claim_complete_market_coverage(monkeypatch):
    result = _payload(monkeypatch, [])
    assert "error" not in result
    data = result["data"]
    assert data["status"] == "received"
    assert data["scope"] == "global_exploratory"
    assert data["coverage_complete"] is False
    assert data["returned_count"] == data["omitted_count"] == 0
    assert data["local_truncated"] is False
    assert data["days_ahead"] == 21


@pytest.mark.parametrize("size,returned,omitted,status", [
    (1, 1, 0, "received"), (100, 100, 0, "received"), (103, 100, 3, "partial"),
])
def test_bulk_reports_measured_local_cut_and_provider_date_quality(monkeypatch, size, returned, omitted, status):
    events = [{"symbol": f"SYN{i}", "date": "2026-10-20", "eps_estimate": -0.5} for i in range(size)]
    data = _payload(monkeypatch, events)["data"]
    assert data["items"] == events[:returned]
    assert data["count"] == size
    assert data["returned_count"] == returned
    assert data["omitted_count"] == omitted
    assert data["local_truncated"] is bool(omitted)
    assert data["status"] == status and data["coverage_complete"] is False
    assert data["date_confirmation"] == "issuer_not_verified"
    assert data["coverage_note"] and data["next_step"]


def test_partial_bulk_retains_rows_and_exposes_reason(monkeypatch):
    events = [{"symbol": "SYN", "date": "2026-10-20"}]
    result = _payload(monkeypatch, events, ["partial provider response"])
    assert "error" not in result
    data = result["data"]
    assert data["items"] == events and data["status"] == "partial"
    assert data["fonti_mute"]["finnhub"] == "partial provider response"


def test_bulk_quality_survives_the_chat_prefix_limit(monkeypatch):
    events = [{"symbol": f"SYN{i}", "date": "2026-10-20", "eps_actual": None,
               "eps_estimate": -0.5, "revenue_actual": None, "revenue_estimate": 0,
               "hour": "amc", "year": 2026, "quarter": 3} for i in range(103)]
    result = _payload(monkeypatch, events, ["provider partial"])
    encoded = json.dumps(result, default=str, ensure_ascii=False)
    assert len(encoded) > 12000  # Existing chat_engine limit, not a new truncation.
    prefix = encoded[:8000]
    for required in ('"coverage_complete": false', '"omitted_count": 3',
                     '"date_confirmation": "issuer_not_verified"', '"status": "partial"',
                     '"fonti_mute": {"finnhub": "provider partial"}'):
        assert required in prefix


@pytest.mark.parametrize("response", [None, {}, [], {"earningsCalendar": None},
    {"earningsCalendar": {}}, {"earningsCalendar": [None]},
    {"earningsCalendar": [{"symbol": "SYN"}, "bad-row"]}])
def test_bulk_invalid_shape_is_declared_not_equated_to_empty(monkeypatch, response):
    monkeypatch.setattr(finnhub_news, "_api_get", lambda *a, **kw: response)
    reasons = []
    assert finnhub_news.fetch_earnings_calendar(motivo=reasons) == []
    assert reasons


def test_bulk_valid_empty_response_has_no_failure_reason(monkeypatch):
    monkeypatch.setattr(finnhub_news, "_api_get", lambda *a, **kw: {"earningsCalendar": []})
    reasons = []
    assert finnhub_news.fetch_earnings_calendar(motivo=reasons) == []
    assert reasons == []


def test_bulk_real_adapter_keeps_values_and_reports_shape_error_to_dispatcher(monkeypatch):
    responses = iter([{"earningsCalendar": [{"symbol": "SYN", "date": "2026-10-20",
        "epsEstimate": -0.5, "revenueEstimate": 0, "year": 2026, "quarter": 3}]}, {}])
    monkeypatch.setattr(finnhub_news, "_api_get", lambda *a, **kw: next(responses))
    result = chat_tools.dispatch("get_earnings_calendar", {})
    row = result["data"]["items"][0]
    assert row["eps_estimate"] == -0.5 and row["revenue_estimate"] == 0
    assert row["year"] == 2026 and row["quarter"] == 3
    assert result["data"]["coverage_complete"] is False
    failed = chat_tools.dispatch("get_earnings_calendar", {})
    assert failed["error"] and failed["data"]["status"] == "unavailable"
