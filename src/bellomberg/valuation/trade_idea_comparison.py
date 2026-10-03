"""Comparisons retain economic units and dates; missing bases never become zeros."""
from copy import deepcopy
from decimal import Decimal
import json
import math


def _finite(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def _finite_delta(before, after):
    value = after - before
    return value if _finite(value) else None


def _contract(model, driver):
    records = [r for r in model.get("acquisition_snapshot", {}).get("case", {}).get("records", [])
               if r.get("scenario") == "model" and r.get("driver") == driver]
    return records[0].get("value") if len(records) == 1 and isinstance(records[0].get("value"), dict) else None


def _contract_basis(value, driver):
    if driver != "quotation" or not isinstance(value, dict):
        return value
    basis = {k: v for k, v in value.items() if k not in ("price", "price_as_of")}
    # Only the explicit pence/pounds presentation conversion is interchangeable.
    if (basis.get("quote_currency"), basis.get("quote_unit"), basis.get("quote_units_per_currency")) == ("GBP", "GBX", 100):
        basis.update(quote_unit="GBP", quote_units_per_currency=1)
    return basis


def _basis_issues(before, after):
    reasons = []
    for field in ("ticker", "valuation_date", "financial_currency"):
        if not before.get(field) or not after.get(field): reasons.append(field + " unavailable")
        elif before[field] != after[field]: reasons.append(field + " differs")
    for field in ("method_id", "method_version"):
        left, right = ((m.get("valuation_decision") or {}).get(field) for m in (before, after))
        if not left or not right: reasons.append("economic " + field + " unavailable")
        elif left != right: reasons.append("economic " + field + " differs")
    for driver in ("perimeter", "quotation", "calendar", "policy"):
        left, right = (_contract(m, driver) for m in (before, after))
        if driver in ("perimeter", "quotation") and (not left or not right):
            reasons.append(driver + " contract unavailable or ambiguous")
        elif _contract_basis(left, driver) != _contract_basis(right, driver):
            reasons.append(driver + " economic basis differs")
    for label, model in (("source", before), ("target", after)):
        perimeter, quote = _contract(model, "perimeter"), _contract(model, "quotation")
        if perimeter and (any(not isinstance(perimeter.get(k), str) or not perimeter[k].strip()
                              for k in ("entity", "currency", "share_class"))
                          or perimeter.get("currency") != model.get("financial_currency")):
            reasons.append(label + " perimeter incomplete or inconsistent")
        if quote:
            textual = ("financial_currency", "quote_currency", "quote_unit", "share_class")
            numeric = ("quote_units_per_currency", "financial_to_quote_rate", "shares_per_quote")
            valid_units = (quote.get("quote_unit") == quote.get("quote_currency") and quote.get("quote_units_per_currency") == 1
                           or (quote.get("quote_currency"), quote.get("quote_unit"), quote.get("quote_units_per_currency")) == ("GBP", "GBX", 100))
            if (any(not isinstance(quote.get(k), str) or not quote[k].strip() for k in textual)
                    or any(not _finite(quote.get(k)) or quote[k] <= 0 for k in numeric)
                    or not valid_units or quote.get("quote_unit") != model.get("currency")
                    or quote.get("financial_currency") != model.get("financial_currency")
                    or perimeter and quote.get("share_class") != perimeter.get("share_class")):
                reasons.append(label + " quotation incomplete or inconsistent")
    return reasons


def normalize(value, unit):
    if not (type(value) in (int,float) or isinstance(value,list) and all(type(x) in (int,float) for x in value)):
        return value,unit
    if unit in ("ratio","percent","%"):
        factor = 100 if unit == "ratio" else 1
        return ([x*factor for x in value] if isinstance(value,list) else value*factor),"%"
    if unit == "GBX":
        return ([x/100 for x in value] if isinstance(value,list) else value/100),"GBP"
    return value,unit


def differences(before, after):
    """Record-level economic identity is part of comparability, not a footnote."""
    def index(model):
        return {(r.get("scenario"),r.get("driver")):r for r in
            model.get("acquisition_snapshot",{}).get("case",{}).get("records",[])}
    first,second = index(before),index(after)
    rows = []
    for key in sorted(set(first)|set(second)):
        left,right = first.get(key),second.get(key)
        if left == right: continue
        reasons = []
        if left is None or right is None:
            reasons.append("input absent from one version")
        else:
            for field in ("field","unit","period","entity","accounting_basis"):
                if left.get(field)!=right.get(field): reasons.append(field+" differs")
            if (isinstance(left.get("value"), dict) or isinstance(right.get("value"), dict)) and (
                    _contract_basis(left.get("value"), key[1]) != _contract_basis(right.get("value"), key[1])):
                reasons.append("structured economic basis differs")
        a,b,unit = None,None,None
        if left: a,unit = normalize(left.get("value"),left.get("unit"))
        if right: b,right_unit = normalize(right.get("value"),right.get("unit"))
        if left and right and unit==right_unit:
            reasons = [r for r in reasons if r!="unit differs"]
        for value in (a, b):
            if value is None or type(value) in (int, float, bool) and not _finite(value) or (
                    isinstance(value, list) and any(not _finite(x) for x in value)):
                reasons.append("finite values unavailable")
                break
        delta = None
        if not reasons and _finite(a) and _finite(b):
            delta = _finite_delta(a, b)
            if delta is None: reasons.append("finite delta unavailable")
        elif not reasons and isinstance(a,list) and isinstance(b,list):
            if len(a) != len(b): reasons.append("vector periods differ")
            else:
                delta = [_finite_delta(x, y) for x,y in zip(a,b)]
                if any(value is None for value in delta):
                    delta = None
                    reasons.append("finite delta unavailable")
        rows.append({"scenario":key[0],"driver":key[1],"before":deepcopy(left),"after":deepcopy(right),
            "normalized_before":a,"normalized_after":b,"unit":unit,"delta":delta,
            "comparability":"not_comparable" if reasons else "same_basis","reasons":reasons})
    return rows


def model_comparison(before, after):
    reasons = _basis_issues(before, after)
    values = []
    for scenario in ("bear","base","bull"):
        left,unit = normalize(before.get("fair_value_"+scenario),before.get("currency"))
        right,other = normalize(after.get("fair_value_"+scenario),after.get("currency"))
        why = reasons + (["quotation currency differs"] if unit!=other else [])
        valid = not why and _finite(left) and _finite(right)
        delta = _finite_delta(left, right) if valid else None
        if valid and delta is None:
            why.append("finite delta unavailable")
            valid = False
        values.append({"scenario":scenario,"before":left,"after":right,"unit":unit,
            "before_date":before.get("valuation_date"),"after_date":after.get("valuation_date"),
            "delta":delta,"comparability":"same_basis" if valid else "not_comparable",
            "reasons":why or ([] if valid else ["finite values unavailable"])})
    return {"source_generation":before["generation_id"],"target_generation":after["generation_id"],
        "values":values,"drivers":differences(before,after),"reasons":reasons}


def cost_breakdown(store, run_id, events):
    with store._connect(read_only=True) as conn:
        run = store._row(conn,run_id)
        summary = store._cost_summary(conn,run_id,run["budget_limit_usd"])
        rows = [dict(r) for r in conn.execute("SELECT * FROM trade_idea_costs WHERE run_id=? ORDER BY created_at,request_id",(run_id,))]
    groups = {}
    for row in rows:
        phase = row["role"]
        group = groups.setdefault(phase,{"phase":phase,"charged_usd":Decimal(0),"reserved_usd":Decimal(0),
            "unknown_requests":0,"released_requests":0,"requests":0,"cache_read_tokens":0,"cache_write_tokens":0,
            "cache_observations":0,"models":set()})
        group["requests"]+=1; group["models"].add(row["model"])
        if row["status"]=="charged": group["charged_usd"]+=Decimal(row["charged_usd"])
        if row["status"]=="reserved": group["reserved_usd"]+=Decimal(row["reserved_usd"])
        if row["status"]=="unknown": group["unknown_requests"]+=1
        if row["status"]=="released": group["released_requests"]+=1
        usage = json.loads(row["usage_json"]) if row["usage_json"] else {}
        if any(k in usage for k in ("cache_read_input_tokens","cache_creation_input_tokens")):
            group["cache_observations"]+=1
            group["cache_read_tokens"]+=usage.get("cache_read_input_tokens") or 0
            group["cache_write_tokens"]+=usage.get("cache_creation_input_tokens") or 0
    for group in groups.values():
        group["models"] = sorted(group["models"])
        for key in ("charged_usd","reserved_usd"): group[key]=str(group[key])
        if not group["cache_observations"]: group.update(cache_read_tokens=None,cache_write_tokens=None)
    actions = {}
    for event in events:
        actions[event["kind"]]=actions.get(event["kind"],0)+1
    return {**summary,"phases":list(groups.values()),"deterministic_actions":actions,
        "workspace_paid_requests":sum(1 for row in rows if row["role"].split(":",1)[0] in ("workspace","chat","refresh")),
        "workspace_cost_basis":"count of workspace/chat/refresh entries in the actual run ledger",
        "unresolved":[{k:row[k] for k in ("request_id","role","model","status","reserved_usd","reason")}
            for row in rows if row["status"] in ("unknown","reserved")],"estimated_duration":None}
