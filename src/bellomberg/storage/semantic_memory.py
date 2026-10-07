"""Read-time operational semantic memory; archive remains separate."""
from collections import Counter
from hashlib import sha256
from math import isfinite

POLICY = "weekly-published-chunks/1"

def search_operational_memos(db, query, n_results=5):
    """Verify retrieved chunks against published SQLite text; never reindex/fill gaps."""
    valid_limit = type(n_results) is int and n_results > 0
    limit = min(n_results, 50) if valid_limit else 0
    coverage = {"requested": n_results, "effective_limit": limit, "retrieved": 0,
                "returned": 0, "excluded": 0, "exclusion_reasons": {},
                "candidate_scope": "retrieved_chunks_only", "universe_verified": False}
    out = {"query": query, "count": 0, "memos": [], "status": "ok",
           "memory_scope": "weekly_operational", "policy": POLICY, "coverage": coverage}

    def unavailable(reason):
        out.update(status="unavailable", reason=reason, count=0, memos=[])
        coverage["returned"] = 0
        return out

    if not valid_limit:
        return unavailable("invalid_result_limit")
    if not isinstance(query, str) or not query.strip():
        return unavailable("invalid_query")
    if db.col_memos is None:
        return unavailable("vector_collection_unavailable")
    try:
        from .weekly_memory import WeeklyMemoryReader
        from .memory_db import _fingerprint_mandato_corrente, _etichetta_mandato_storico
        reader = WeeklyMemoryReader(db)
        fingerprint = _fingerprint_mandato_corrente()
    except Exception:
        return unavailable("publication_lookup_unavailable")
    try:
        result = db.col_memos.query(query_texts=[query], n_results=limit)
    except Exception:
        return unavailable("vector_query_unavailable")
    fields = ("ids", "metadatas", "documents", "distances")
    if not isinstance(result, dict) or any(
            not isinstance(result.get(key), list) or len(result[key]) != 1
            or not isinstance(result[key][0], list) for key in fields):
        return unavailable("vector_response_invalid")
    ids, metas, documents, distances = (result[key][0] for key in fields)
    if len({len(items) for items in (ids, metas, documents, distances)}) != 1 or len(ids) > limit:
        return unavailable("vector_response_invalid")
    coverage["retrieved"] = len(ids)
    duplicate_ids = Counter(x for x in ids if isinstance(x, str))
    chunk_cache = {}
    for chunk_id, meta, document, distance in zip(ids, metas, documents, distances):
        reason = None
        mid = meta.get("memo_id") if isinstance(meta, dict) else None
        idx = meta.get("chunk_idx") if isinstance(meta, dict) else None
        if (type(mid) is not int or mid <= 0 or type(idx) is not int or idx < 0
                or not isinstance(chunk_id, str) or chunk_id != f"memo_{mid}_chunk_{idx}"):
            reason = "chunk_identity_invalid"
        elif duplicate_ids[chunk_id] != 1:
            reason = "duplicate_chunk_identity"
        elif not isinstance(document, str) or not document:
            reason = "chunk_content_invalid"
        elif distance is not None and (type(distance) not in (int, float) or not isfinite(distance)):
            reason = "distance_invalid"
        else:
            completion = reader.completion(mid)
            if not completion:
                reason = "memo_not_operational"
            else:
                body = reader.rows[mid]["full_markdown"]
                if mid not in chunk_cache:
                    chunk_cache[mid] = db._chunk_markdown(body)
                chunks = chunk_cache[mid]
                if idx >= len(chunks) or document != chunks[idx]:
                    reason = "published_chunk_mismatch"
                else:
                    out["memos"].append({"chunk_id": chunk_id, "memo_id": mid,
                        "content": _etichetta_mandato_storico(body, fingerprint) + "\n" + document,
                        "distance": distance, "memory_completion": completion,
                        "published_memo_sha256": sha256(body.encode()).hexdigest(),
                        "published_chunk_sha256": sha256(document.encode()).hexdigest(),
                        "provenance": "exact_published_chunk"})
        if reason:
            coverage["excluded"] += 1
            reasons = coverage["exclusion_reasons"]
            reasons[reason] = reasons.get(reason, 0) + 1
    coverage["returned"] = out["count"] = len(out["memos"])
    if coverage["excluded"] or n_results != limit:
        out["status"] = "partial"
    return out
