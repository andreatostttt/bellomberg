"""Admission rejection proof: native persisted rows only, never provider calls."""
from copy import deepcopy
import hashlib
import json

import pytest

from bellomberg.core.preprovider_receipt import (
    PreProviderEvidenceError, build_preprovider_rejection_receipt,
    validate_preprovider_rejection_receipt,
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def rejected():
    # Exact metadata captured 2026-10-02; request/run/model identifiers sanitized.
    metadata = {
        "reason": "in_flight_budget_exhausted",
        "limit_source": "openrouter_in_flight_budget",
        "remedy_hint": "Retry after your in-flight requests settle (see the Retry-After header). "
        "Adding credits at https://openrouter.ai/settings/credits raises your in-flight budget, "
        "up to a capped ceiling.",
        "headers": {"Retry-After": "120"}, "provider_name": None,
    }
    message = (
        "APIStatusError: HTTP 402: This request would exceed your available credits given "
        "your current in-flight requests. Retry after in-flight requests settle, or add credits. "
        "| metadata: " + json.dumps(metadata, ensure_ascii=False)
    )
    short_reason = "APIStatusError: " + message.removeprefix("APIStatusError: ")[:250]
    request_sha = hashlib.sha256(b"frozen request body").hexdigest()
    receipt = {
        "complete": False, "model": None, "partial_response": {},
        "partial_response_sha256": hashlib.sha256(b"{}").hexdigest(),
        "request_sha256": request_sha, "response_id": None, "status_code": 402,
    }
    row = {
        "request_id": "local-request", "run_id": "local-run", "role": "specialist:fundamentals",
        "model": "test/model", "reserved_usd": "1.548436250", "charged_usd": None,
        "status": "unknown", "usage_json": None, "receipt_json": encoded(receipt),
        "reason": short_reason, "created_at": "2026-10-02T17:06:37Z",
        "updated_at": "2026-10-02T17:06:38Z",
    }
    failure = {"desk": row["role"], "exception_type": "APIStatusError", "message": message,
               "phase": "committee", "request_id": row["request_id"], "round": 1}
    events = []
    for kind, payload in [
        ("cost_reserved", {"request_id": row["request_id"], "request_sha256": request_sha,
                           "model": row["model"], "role": row["role"], "max_usd": row["reserved_usd"]}),
        ("cost_unknown", {"charged_usd": None, "reason": short_reason, "request_id": row["request_id"]}),
        ("execution_failure", failure),
        ("execution_failure", {**failure, "desk": "fundamentals", "phase": "building"}),
    ]:
        events.append({"id": len(events) + 1, "run_id": row["run_id"], "kind": kind,
                       "payload_json": encoded(payload), "at": "2026-10-02T17:06:38Z"})
    return row, events, failure


def edit_payload(event, field, value):
    payload = json.loads(event["payload_json"])
    payload[field] = value
    event["payload_json"] = encoded(payload)


def edit_receipt(row, field, value):
    receipt = json.loads(row["receipt_json"])
    receipt[field] = value
    row["receipt_json"] = encoded(receipt)


def test_complete_authentic_metadata_classifies_without_fabricated_billing(rejected):
    row, events, failure = rejected
    before = deepcopy(rejected)
    proof = build_preprovider_rejection_receipt(row, events, failure=failure)
    assert proof["kind"] == "openrouter_pre_provider_rejection/v1"
    assert proof["classification"] == "rejected_before_provider"
    assert proof["transport_http_status"] is None
    assert proof["request_id"] == row["request_id"]
    assert proof["request_sha256"] == json.loads(row["receipt_json"])["request_sha256"]
    assert proof["original_unknown_receipt"] == json.loads(row["receipt_json"])
    assert len(json.dumps(proof["evidence"]["metadata"], ensure_ascii=False)) == 347
    assert len(proof["evidence"]["failure_event_sha256"]) == 2
    assert not {"billable", "usage", "cost_usd", "response_id"} & proof.keys()
    assert validate_preprovider_rejection_receipt(proof, row, events, failure=failure)
    assert rejected == before
    proof["classification"] = "invented"
    assert not validate_preprovider_rejection_receipt(proof, row, events, failure=failure)


def test_unrelated_primary_failure_is_not_authoritative(rejected):
    row, events, _ = rejected
    proof = build_preprovider_rejection_receipt(row, events, failure={"request_id": "another-request"})
    assert validate_preprovider_rejection_receipt(proof, row, events)


def test_receipt_validation_preserves_json_boolean_types(rejected):
    row, events, _ = rejected
    proof = build_preprovider_rejection_receipt(row, events)
    proof["original_unknown_receipt"]["complete"] = 0
    assert not validate_preprovider_rejection_receipt(proof, row, events)


@pytest.mark.parametrize("mutation", [
    lambda r, e, f: r.update(status="charged"),
    lambda r, e, f: r.update(charged_usd="0"),
    lambda r, e, f: r.update(usage_json="{}"),
    lambda r, e, f: r.update(reserved_usd="NaN"),
    lambda r, e, f: edit_receipt(r, "response_id", "gen-real"),
    lambda r, e, f: edit_receipt(r, "model", "real/provider"),
    lambda r, e, f: edit_receipt(r, "partial_response", {"usage": {"cost": 0}}),
    lambda r, e, f: edit_receipt(r, "partial_response_sha256", "0" * 64),
    lambda r, e, f: edit_receipt(r, "request_sha256", "0" * 64),
    lambda r, e, f: edit_receipt(r, "status_code", 500),
    lambda r, e, f: edit_receipt(r, "complete", True),
    lambda r, e, f: edit_receipt(r, "usage", {}),
    lambda r, e, f: edit_payload(e[0], "model", "other/model"),
    lambda r, e, f: edit_payload(e[0], "request_id", "different-request"),
    lambda r, e, f: edit_payload(e[0], "max_usd", "2"),
    lambda r, e, f: e[0].update(run_id="different-run"),
    lambda r, e, f: edit_payload(e[2], "exception_type", "TimeoutError"),
    lambda r, e, f: edit_payload(e[2], "message", f["message"][:-1]),
    lambda r, e, f: edit_payload(e[2], "message", f["message"].replace("HTTP 402:", "HTTP 500:")),
    lambda r, e, f: edit_payload(e[2], "message", f["message"].replace("openrouter_in_flight_budget", "provider_budget")),
    lambda r, e, f: edit_payload(e[2], "message", f["message"].replace('"provider_name": null', '"provider_name": "upstream"')),
    lambda r, e, f: edit_payload(e[2], "message", f["message"].replace('"provider_name": null', '"provider_name": null, "usage": {}')),
    lambda r, e, f: edit_payload(e[2], "message", f["message"].replace('"provider_name": null', '"provider_name": null, "provider_name": null')),
    lambda r, e, f: e.__setitem__(slice(2, None), []),
    lambda r, e, f: e.append({**e[0], "id": 5}),
    lambda r, e, f: e[2].update(id=1),
    lambda r, e, f: f.update(message="different error"),
])
def test_uncertain_or_contradictory_evidence_stays_unclassified(rejected, mutation):
    row, events, failure = rejected
    mutation(row, events, failure)
    with pytest.raises(PreProviderEvidenceError):
        build_preprovider_rejection_receipt(row, events, failure=failure)


def test_failure_string_alone_and_other_requests_cannot_authorize_release(rejected):
    row, events, failure = rejected
    for event in events[2:]:
        edit_payload(event, "request_id", "other-request")
    with pytest.raises(PreProviderEvidenceError):
        build_preprovider_rejection_receipt(row, events, failure=failure)
