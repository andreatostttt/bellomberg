"""Date-only option expiry validation; no provider calls or financial thresholds."""
from datetime import date, timedelta
import re


def normalize_expiry(value):
    """Empty means absent; accept provider ISO or IB YYYYMMDD, reject other forms."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError('expiry_invalid')
    value = value.strip()
    if not re.fullmatch(r'(?:\d{4}-\d{2}-\d{2}|\d{8})', value):
        raise ValueError('expiry_invalid')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError('expiry_invalid') from None


def valid_expiry(value, *, today=None):
    normalized = normalize_expiry(value)
    if normalized is None:
        raise ValueError('expiry_missing')
    if date.fromisoformat(normalized) < (today or date.today()):
        raise ValueError('expiry_past')
    return normalized


def select_expiry(expirations, requested=None, *, today=None, min_days=0, compact=False):
    """Explicit dates never substitute; min_days applies only to default selection."""
    today = today or date.today()
    requested = normalize_expiry(requested)
    available = set()
    for value in expirations or []:
        try:
            available.add(valid_expiry(value, today=today))
        except ValueError:
            continue
    if requested is not None:
        chosen = valid_expiry(requested, today=today)
        if chosen not in available:
            raise ValueError('expiry_requested_unavailable: ' + chosen)
    else:
        candidates = sorted(e for e in available if date.fromisoformat(e) >= today + timedelta(days=min_days))
        if not candidates:
            raise ValueError('expiry_no_valid_available')
        chosen = candidates[0]
    return chosen.replace('-', '') if compact else chosen
