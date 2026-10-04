"""OpenRouter generation lookup: the measured cost of an interrupted request.

A request whose outcome is uncertain (timeout, dropped stream, missing usage)
keeps its whole reservation and blocks further spending. When the provider's
generation id is known, ``GET /api/v1/generation`` returns the billed total; this
module reads it and decides, without writing anything. A 404 is NOT a zero cost:
OpenRouter indexes generations with a delay (seen on 03/10/2026: 404 first, the
real bill minutes later), so it stays pending. No model is called here.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

GENERATION_URL = "https://openrouter.ai/api/v1/generation?id="
_GENERATION_ID = re.compile(r"gen-[0-9A-Za-z-]{1,123}")


def generation_id_from(receipt, error=None):
    """The provider id captured for THIS request (header, error or first stream chunk)."""
    candidates = [getattr(error, "generation_id", None)]
    if isinstance(receipt, dict):
        partial = receipt.get("partial_response") if isinstance(receipt.get("partial_response"), dict) else {}
        candidates += [receipt.get("generation_id"), receipt.get("response_id"), partial.get("id")]
    for value in candidates:
        if isinstance(value, str) and _GENERATION_ID.fullmatch(value):
            return value
    return None


def fetch_generation(generation_id, *, api_key=None, opener=urlopen, timeout=15):
    """One read-only lookup; HTTP statuses are returned as evidence, never raised."""
    if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
        raise ValueError("not an OpenRouter generation id")
    if api_key is None:
        from bellomberg.core.llm_client import chiave_api
        api_key = chiave_api()
    request = Request(GENERATION_URL + quote(generation_id, safe=""),
                      headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"})
    at = datetime.now(timezone.utc).isoformat()
    try:
        with opener(request, timeout=timeout) as response:
            status, raw = getattr(response, "status", 200), response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read() or b""
    except (URLError, OSError) as exc:
        return {"at": at, "generation_id": generation_id, "http_status": None,
                "error": type(exc).__name__ + ": " + str(exc)[:300], "body": None, "body_sha256": None}
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        body = {"unparsed": raw[:2000].decode("utf-8", "replace")}
    return {"at": at, "generation_id": generation_id, "http_status": status, "body": body,
            "body_sha256": sha256(raw).hexdigest() if raw else None}


def _same_model(requested, observed):
    # OpenRouter reports the dated variant: "anthropic/claude-opus-5.5-20260921".
    return isinstance(observed, str) and (observed == requested or observed.startswith(str(requested) + "-"))


def settlement_from_lookup(lookup, *, requested_model):
    """{'status': 'settled', 'charged_usd', 'usage', 'provider_generation'} or {'status': 'pending', 'reason'}."""
    if not isinstance(lookup, dict) or lookup.get("http_status") != 200:
        status = (lookup or {}).get("http_status")
        return {"status": "pending", "reason": (
            "generation not indexed yet (404): not a zero cost, retry later" if status == 404 else
            "lookup unavailable: " + str((lookup or {}).get("error") or "HTTP " + str(status)))}
    data = (lookup.get("body") or {}).get("data")
    if not isinstance(data, dict) or data.get("id") != lookup.get("generation_id"):
        return {"status": "pending", "reason": "lookup does not describe this generation"}
    if not _same_model(requested_model, data.get("model")):
        return {"status": "pending", "reason": "provider model " + str(data.get("model"))
                + " differs from the reserved " + str(requested_model)}
    try:
        total = Decimal(str(data.get("total_cost")))
    except (InvalidOperation, ValueError):
        return {"status": "pending", "reason": "provider total_cost missing or invalid"}
    if not total.is_finite() or total < 0:
        return {"status": "pending", "reason": "provider total_cost not a finite non-negative amount"}
    usage = {"cost_usd": str(total)}
    for key in ("tokens_prompt", "tokens_completion", "native_tokens_prompt", "native_tokens_completion",
                "native_tokens_reasoning"):
        if isinstance(data.get(key), int):
            usage[key] = data[key]
    return {"status": "settled", "charged_usd": str(total), "usage": usage, "provider_generation": data}
