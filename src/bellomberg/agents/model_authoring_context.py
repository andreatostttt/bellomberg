"""Versioned request view of an otherwise unchanged native author transcript.

Only obsolete, informational progress ledgers are replaced by explicit hash
references. All research, tool arguments/results, reports and latest progress
stay verbatim. The caller owns durable pinning and must try exact paid replay
before using this view for a new request. This module performs no I/O.
"""
from copy import deepcopy
from hashlib import sha256
import json


PROGRESS_MARKER = "\n\nRECORDED MODEL WORK / MISSING AUTHOR ACTIONS:\n"
PROJECTION_CONTRACT = "model_authoring_context_projection/1"
_LEDGER_KEYS = frozenset({
    "contract", "kind", "contract_status", "method_id", "source_fingerprint",
    "remaining_turns", "remaining_turns_error", "admitted_document_ids",
    "contract_reads", "draft", "consultations", "errors", "instructions", "next_actions",
})


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False).encode("utf-8")).hexdigest()


def _bytes(value):
    return max(len(json.dumps(value, ensure_ascii=ascii_only, allow_nan=False).encode("utf-8"))
               for ascii_only in (False, True))


def _ledger_suffix(text):
    start = text.rfind(PROGRESS_MARKER)
    if start < 0:
        return None
    try:
        # A ledger followed by other prose is ambiguous; preserve it intact.
        value = json.loads(text[start + len(PROGRESS_MARKER):])
        if (not isinstance(value, dict) or set(value) != _LEDGER_KEYS
                or value.get("contract") != "model_authoring_progress/1"
                or value.get("kind") != "informational_not_approval"):
            return None
        _digest(value)  # reject non-finite/unsupported JSON even in old ledgers
    except (ValueError, TypeError):
        return None
    return start, value


def project_model_authoring_messages(messages):
    """Return a deterministic view and its receipt; never mutate raw messages.

    Input must already use the JSON-native checkpoint representation. A receipt
    does not authorize a new request, change an accounting record, or certify
    financial inputs. The complete original transcript remains authoritative.
    """
    if not isinstance(messages, list) or not messages:
        raise ValueError("Nonempty native message history required")
    try:
        _digest(messages)
    except (ValueError, TypeError) as exc:
        raise ValueError("Native message history requires finite JSON values") from exc
    projected = deepcopy(messages)
    ledgers = []
    for message_index, message in enumerate(messages):
        if (not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}
                or not isinstance(message.get("content"), (str, list))):
            raise ValueError("Invalid native message history shape")
        content = message["content"]
        if isinstance(content, list) and any(not isinstance(block, dict) for block in content):
            raise ValueError("Native message blocks must be JSON objects")
        if message["role"] != "user":
            continue
        texts = ([(None, content)] if isinstance(content, str) else
                 [(index, block["text"]) for index, block in enumerate(content)
                  if block.get("type") == "text" and isinstance(block.get("text"), str)])
        for block_index, text in texts:
            found = _ledger_suffix(text)
            if found is not None:
                start, value = found
                ledgers.append((message_index, block_index, text, start, value))
    latest_sha = _digest(ledgers[-1][4]) if ledgers else None
    replaced = []
    for message_index, block_index, text, start, value in ledgers[:-1]:
        ledger_sha = _digest(value)
        reference = {
            "contract": "model_authoring_progress_reference/1",
            "ledger_sha256": ledger_sha,
            "latest_ledger_sha256": latest_sha,
            "message_index": message_index, "block_index": block_index,
            "notice": ("Earlier informational progress only. Full snapshot is preserved in the original "
                       "native checkpoint. Use the latest recorded model-work ledger below. All tool "
                       "inputs, results, research and author decisions remain unchanged in this conversation."),
        }
        replacement = "\n\nARCHIVED MODEL WORK SNAPSHOT:\n" + json.dumps(
            reference, sort_keys=True, ensure_ascii=False, allow_nan=False)
        updated = text[:start] + replacement
        target = projected[message_index]
        if block_index is None:
            target["content"] = updated
        else:
            target["content"][block_index]["text"] = updated
        replaced.append({
            "message_index": message_index, "block_index": block_index,
            "start": start, "end": len(text), "ledger_sha256": ledger_sha,
            "original_text_sha256": sha256(text.encode("utf-8")).hexdigest(),
            "replacement_sha256": sha256(replacement.encode("utf-8")).hexdigest(),
        })
    return projected, {
        "contract": PROJECTION_CONTRACT,
        "original_messages_sha256": _digest(messages),
        "projected_messages_sha256": _digest(projected),
        "original_bytes": _bytes(messages), "projected_bytes": _bytes(projected),
        "replaced_ledgers": replaced, "retained_latest_ledger_sha256": latest_sha,
    }
