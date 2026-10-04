"""Explicit reconciliation of uncertain request costs (PM decision 04/10/2026).

An uncertain outcome blocks all further spending until its cost is known. This
service asks OpenRouter for the measured bill of the exact generation captured
for that request and, only with ``apply=True`` (the PM's confirmation), records
it. A missing generation id, a 404 (not yet indexed) or a model mismatch stays
unknown and is reported; nothing is ever assumed to be free. No model is called.
"""
from hashlib import sha256
import json

from bellomberg.core.generation_lookup import fetch_generation, generation_id_from, settlement_from_lookup


def _lookup(generation_id, fetch):
    lookup = fetch(generation_id)
    lookup_sha256 = sha256(json.dumps(lookup, sort_keys=True, ensure_ascii=False,
                                      default=str).encode("utf-8")).hexdigest()
    return lookup, lookup_sha256


def reconcile_trade_idea_costs(store, run_id, *, apply=False, fetch=fetch_generation):
    """Outcome per unknown request of the run chain; writes only when ``apply``."""
    outcomes = []
    for row in store.unknown_costs(run_id):
        receipt = json.loads(row.get("receipt_json") or "{}")
        outcome = {"run_id": row["run_id"], "request_id": row["request_id"], "role": row["role"],
                   "model": row["model"], "reserved_usd": row["reserved_usd"]}
        generation_id = generation_id_from(receipt)
        if generation_id is None:
            outcomes.append({**outcome, "status": "pending",
                             "reason": "no provider generation id was captured for this request"})
            continue
        lookup, lookup_sha256 = _lookup(generation_id, fetch)
        decision = settlement_from_lookup(lookup, requested_model=row["model"])
        outcome.update(generation_id=generation_id, lookup_sha256=lookup_sha256, http_status=lookup.get("http_status"))
        if decision["status"] != "settled":
            outcomes.append({**outcome, "status": "pending", "reason": decision["reason"]})
            continue
        outcome.update(charged_usd=decision["charged_usd"])
        if apply:
            settled_receipt = {**receipt, "response_id": generation_id, "model": row["model"],
                               "accounting_source": "OpenRouter GET /api/v1/generation",
                               "provider_generation": decision["provider_generation"],
                               "lookup_sha256": lookup_sha256}
            store.reconcile_cost(row["run_id"], row["request_id"], charged_usd=decision["charged_usd"],
                                 usage=decision["usage"], receipt=settled_receipt)
            outcomes.append({**outcome, "status": "settled"})
        else:
            outcomes.append({**outcome, "status": "settleable"})
    return {"run_id": run_id, "apply": bool(apply), "outcomes": outcomes,
            "settled": sum(item["status"] == "settled" for item in outcomes),
            "pending": sum(item["status"] == "pending" for item in outcomes)}


def reconcile_weekly_journal(journal_path, *, apply=False, fetch=fetch_generation):
    """Same rule for the weekly Consigliere request journal."""
    from bellomberg.core.request_journal import RequestJournal
    outcomes = []
    for row in RequestJournal.unknown_requests(journal_path):
        outcome = {"request_id": row["request_id"], "model": row["model"]}
        generation_id = generation_id_from(row["receipt"])
        if generation_id is None:
            outcomes.append({**outcome, "status": "pending",
                             "reason": "no provider generation id was captured for this request"})
            continue
        lookup, lookup_sha256 = _lookup(generation_id, fetch)
        decision = settlement_from_lookup(lookup, requested_model=row["model"])
        outcome.update(generation_id=generation_id, lookup_sha256=lookup_sha256, http_status=lookup.get("http_status"))
        if decision["status"] != "settled":
            outcomes.append({**outcome, "status": "pending", "reason": decision["reason"]})
            continue
        outcome.update(charged_usd=decision["charged_usd"])
        if apply:
            RequestJournal.settle_unknown(journal_path, row["request_id"], charged_usd=decision["charged_usd"],
                                          generation_id=generation_id,
                                          provider_generation=decision["provider_generation"],
                                          lookup_sha256=lookup_sha256)
            outcomes.append({**outcome, "status": "settled"})
        else:
            outcomes.append({**outcome, "status": "settleable"})
    return {"journal": str(journal_path), "apply": bool(apply), "outcomes": outcomes,
            "settled": sum(item["status"] == "settled" for item in outcomes),
            "pending": sum(item["status"] == "pending" for item in outcomes)}
