"""Pure evidence validation for a documented OpenRouter admission rejection.

Inputs must be native rows/events obtained by the server. This module never
reads a database, dispatches a request, or changes accounting. A transport status
not preserved by the historical client remains unknown; no usage is fabricated.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


class PreProviderEvidenceError(ValueError):
    """The persisted evidence does not prove this specific admission rejection."""


def _require(condition, message):
    if not condition:
        raise PreProviderEvidenceError(message)


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON property in rejection evidence")
        result[key] = value
    return result


def _object(raw):
    _require(isinstance(raw, str), "Native JSON evidence must be a string")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError) as exc:
        raise PreProviderEvidenceError("Incomplete or invalid JSON evidence") from exc
    _require(isinstance(value, dict), "Native JSON evidence must be an object")
    return value


def _money(value):
    _require(isinstance(value, str), "Reservation must retain its native decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise PreProviderEvidenceError("Invalid reservation") from exc
    _require(amount.is_finite() and amount > 0, "Invalid reservation")
    return amount


def _failure_metadata(payload):
    _require(payload.get("exception_type") == "APIStatusError", "Not a native APIStatusError")
    message = payload.get("message")
    prefix = "APIStatusError: HTTP 402: "
    _require(isinstance(message, str) and message.startswith(prefix), "Error code is not 402")
    _require(message.count(" | metadata: ") == 1, "Missing or ambiguous error metadata")
    raw = message.split(" | metadata: ", 1)[1]
    # Historical _errore_da_corpo retained at most 400 characters. A valid JSON
    # fragment exactly at the cap is not enough to prove the suffix was complete.
    _require(len(raw) < 400, "Metadata may have been truncated")
    metadata = _object(raw)
    _require(metadata.get("reason") == "in_flight_budget_exhausted"
             and metadata.get("limit_source") == "openrouter_in_flight_budget",
             "Not the documented OpenRouter admission rejection")
    _require("provider_name" in metadata and metadata["provider_name"] is None,
             "Provider involvement is absent or contradictory")
    _require(set(metadata) <= {"reason", "limit_source", "remedy_hint", "headers", "provider_name"},
             "Unrecognized or contradictory error metadata")
    if "remedy_hint" in metadata:
        _require(isinstance(metadata["remedy_hint"], str), "Invalid remedy metadata")
    if "headers" in metadata:
        headers = metadata["headers"]
        _require(isinstance(headers, dict) and set(headers) <= {"Retry-After"},
                 "Unrecognized metadata headers")
        _require(all(isinstance(v, str) and v.isascii() and v.isdigit() for v in headers.values()),
                 "Invalid Retry-After metadata")
    return metadata


def build_preprovider_rejection_receipt(cost_row, events, *, failure=None):
    """Build a proof from a native unknown cost row and its persisted events.

The optional current primary failure is compared when it names this request;
another desk's primary failure cannot substitute for native execution events.
"""
    try:
        row = deepcopy(dict(cost_row))
    except (TypeError, ValueError) as exc:
        raise PreProviderEvidenceError("Missing native cost row") from exc
    for key in ("request_id", "run_id", "model", "role"):
        _require(isinstance(row.get(key), str) and bool(row[key].strip()), f"Missing {key}")
    _require(row.get("status") == "unknown", "Only an unknown request can be classified")
    _require(row.get("charged_usd") is None and row.get("usage_json") is None,
             "Cost or usage evidence already exists")
    reserved = _money(row.get("reserved_usd"))
    original = _object(row.get("receipt_json"))
    expected_keys = {"complete", "model", "partial_response", "partial_response_sha256",
                     "request_sha256", "response_id", "status_code"}
    _require(set(original) == expected_keys, "Unrecognized unknown receipt fields")
    _require(original["complete"] is False and original["model"] is None
             and original["response_id"] is None and original["partial_response"] == {},
             "Provider response evidence exists")
    _require(type(original["status_code"]) is int and original["status_code"] == 402,
             "Receipt error code is not 402")
    _require(original["partial_response_sha256"] == _digest({}), "Partial response hash differs")
    request_sha = original["request_sha256"]
    _require(isinstance(request_sha, str) and re.fullmatch(r"[0-9a-f]{64}", request_sha),
             "Missing exact request hash")
    _require(isinstance(events, (list, tuple)), "Missing native events")
    relevant = []
    for item in events:
        event = dict(item)
        if event.get("kind") not in {"cost_reserved", "cost_unknown", "cost_charged",
                                      "cost_released", "execution_failure"}:
            continue
        payload = _object(event.get("payload_json"))
        if payload.get("request_id") != row["request_id"]:
            continue
        _require(event.get("run_id") == row["run_id"], "Event run differs")
        _require(type(event.get("id")) is int and event["id"] > 0, "Invalid native event id")
        relevant.append((event, payload))
    ids = [event["id"] for event, _ in relevant]
    _require(len(ids) == len(set(ids)), "Duplicate native event id")
    relevant.sort(key=lambda item: item[0]["id"])
    reservations = [item for item in relevant if item[0]["kind"] == "cost_reserved"]
    unknowns = [item for item in relevant if item[0]["kind"] == "cost_unknown"]
    failures = [item for item in relevant if item[0]["kind"] == "execution_failure"]
    _require(len(reservations) == 1 and len(unknowns) == 1 and failures,
             "Missing or ambiguous native reservation/failure lineage")
    _require(not any(event["kind"] in {"cost_charged", "cost_released"} for event, _ in relevant),
             "A conflicting settlement already exists")
    reservation_event, reservation = reservations[0]
    unknown_event, unknown = unknowns[0]
    _require(reservation.get("request_sha256") == request_sha
             and reservation.get("model") == row["model"] and reservation.get("role") == row["role"]
             and _money(reservation.get("max_usd")) == reserved, "Reservation binding differs")
    _require(reservation_event["id"] < unknown_event["id"] < failures[0][0]["id"],
             "Native event ordering differs")
    _require(unknown.get("charged_usd") is None, "Unknown event contains a charge")
    first_message = failures[0][1].get("message")
    metadata = _failure_metadata(failures[0][1])
    for _, payload in failures:
        _require(_failure_metadata(payload) == metadata and payload.get("message") == first_message,
                 "Native failure events disagree")
        _require(payload.get("desk") in {row["role"], row["role"].removeprefix("specialist:")},
                 "Failure desk differs from reserved role")
    short_reason = "APIStatusError: " + first_message.removeprefix("APIStatusError: ")[:250]
    _require(unknown.get("reason") == short_reason and row.get("reason") == short_reason,
             "Unknown cost is not bound to the complete failure")
    if isinstance(failure, dict) and failure.get("request_id") == row["request_id"]:
        _require(_failure_metadata(failure) == metadata and failure.get("message") == first_message,
                 "Primary failure contradicts the native events")
    proof = {
        "kind": "openrouter_pre_provider_rejection/v1", "classification": "rejected_before_provider",
        "request_id": row["request_id"], "run_id": row["run_id"], "request_sha256": request_sha,
        "model": row["model"], "role": row["role"], "reserved_usd": row["reserved_usd"],
        "error_code": 402, "reason": metadata["reason"], "limit_source": metadata["limit_source"],
        "transport_http_status": None, "original_unknown_receipt": original,
        "evidence": {
            "cost_row_sha256": _digest(row), "reservation_event_sha256": _digest(reservation_event),
            "unknown_event_sha256": _digest(unknown_event),
            "failure_event_sha256": [_digest(event) for event, _ in failures],
            "metadata": metadata, "metadata_sha256": _digest(metadata),
        },
    }
    proof["proof_sha256"] = _digest(proof)
    return proof


def validate_preprovider_rejection_receipt(receipt, cost_row, events, *, failure=None):
    """Rebuild from server evidence; a caller-provided receipt is never authority."""
    try:
        expected = build_preprovider_rejection_receipt(cost_row, events, failure=failure)
        return _digest(receipt) == _digest(expected)
    except (PreProviderEvidenceError, TypeError, ValueError):
        return False
