"""Read saved company research and consensus without creating work or data.

This module deliberately does not construct MemoryDB, import agent tools or call
providers. Missing tables/caches remain visible; opening Fund never repairs them.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3

from bellomberg.valuation.market_quote import FRESHNESS_POLICY, _freshness
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE


CONTRACT = "fundamentals_research_view/1"
CONSENSUS_TTL_SECONDS = 48 * 3600


def _map(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _number(value, *, positive=False):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if math.isfinite(value) and (not positive or value > 0):
                return value
        except OverflowError:
            pass
    return None


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _json(value):
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("saved object expected")
    return parsed


def _quote(row, now):
    row = _map(row)
    value = _number(row.get("prezzo"), positive=True)
    observed = _stamp(row.get("timestamp"))
    status = "missing" if value is None else "date_missing" if observed is None else _freshness(
        observed.date().isoformat(), now.date().isoformat())
    if status == "ok":
        status = "available"
    if observed is not None and observed > now:
        status = "date_invalid"
    return {"value": value, "currency": _text(row.get("valuta")), "source": _text(row.get("source")),
            "observed_at": _text(row.get("timestamp")), "acquired_at": _text(row.get("acquired_at")),
            "status": status, "refresh": None,
            "freshness_policy": FRESHNESS_POLICY}


def _consensus(ticker, cache_dir, now):
    out = {"status": "missing", "source": None, "currency": None, "currency_source": None,
           "acquired_at": None, "data_as_of": None, "identity_symbol": None,
           "mean": None, "median": None, "low": None, "high": None,
           "number_of_analysts": None, "freshness_policy": "market_consensus_acquisition_48h; provider_date_if_supplied",
           "reason": None, "refresh": None, "_quote": None, "_quote_refresh": None}
    path = Path(cache_dir) / (sha256(ticker.encode("utf-8")).hexdigest() + ".json")
    if not path.is_file():
        return out
    try:
        raw = _json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out.update(status="unavailable", reason=type(exc).__name__)
        return out
    if (raw.get("observation_kind") != "market_consensus"
            or raw.get("observation_contract") != "market-consensus/1"):
        out["reason"] = "market_observation_required"
        return out
    out["refresh"] = _map(raw.get("consensus_refresh")) or None
    out["_quote_refresh"] = _map(raw.get("quote_refresh")) or None
    out["reason"] = _text(raw.get("consensus_reason"))
    for field in ("source", "currency", "currency_source", "acquired_at", "data_as_of", "identity_symbol"):
        out[field] = _text(raw.get(field))
    targets = _map(raw.get("price_targets"))
    quoted = _map(raw.get("quote"))
    if raw.get("ticker") == ticker and quoted.get("identity_symbol") == ticker:
        out["_quote"] = _quote({"prezzo": quoted.get("value"), "valuta": quoted.get("currency"),
            "source": quoted.get("source"), "timestamp": quoted.get("observed_at"),
            "acquired_at": quoted.get("acquired_at")}, now)
    for field in ("mean", "median", "low", "high"):
        out[field] = _number(targets.get(field), positive=True)
    count = targets.get("number_of_analysts")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        out["number_of_analysts"] = count
    acquired = _stamp(out["acquired_at"])
    if raw.get("ticker") != ticker or out["identity_symbol"] != ticker:
        out["status"] = "identity_mismatch"
    elif raw.get("consensus_status") in ("unavailable", "not_applicable"):
        out["status"] = raw["consensus_status"]
    elif out["mean"] is None:
        out["status"] = "data_missing"
    elif acquired is None:
        out["status"] = "date_missing"
    elif acquired > now:
        out["status"] = "date_invalid"
    elif (now - acquired).total_seconds() > CONSENSUS_TTL_SECONDS:
        out["status"] = "stale"
    else:
        out["status"] = "available"
    return out


def _comparison(quote, consensus):
    status = consensus["status"]
    if status == "available":
        status = quote["status"]
    if status == "available":
        unit = lambda currency: "GBX" if currency in ("GBp", "GBX") else currency
        if not quote["currency"] or not consensus["currency"]:
            status = "currency_missing"
        elif unit(quote["currency"]) != unit(consensus["currency"]):
            status = "currency_mismatch"
    upside = None
    if status == "available":
        upside = _number((consensus["mean"] / quote["value"] - 1) * 100)
        if upside is None:
            status = "data_missing"
        else:
            upside = round(upside, 2)
    return {"upside_pct": upside, "status": status}


def _analysis_missing():
    return {"status": "missing", "origin": None, "scope": None, "as_of": None,
            "run_id": None, "memo_id": None, "mode": None, "summary": None,
            "judgment": None, "content": None, "reference": None}


def _trade_analysis(row, *, detail):
    request = _json(row.get("request_json"))
    if request.get("analysis_mode") != RESEARCH_ANALYSIS_MODE:
        return None
    result = _json(row.get("result_json"))
    progress = _json(row.get("progress_json"))
    if result.get("ticker") != row["ticker"] or not _text(result.get("summary")):
        raise ValueError("saved research identity or summary missing")
    return {"status": "available", "origin": "trade_idea", "scope": "single_company",
            "as_of": row.get("finished_at") or row.get("updated_at"), "run_id": row["id"],
            "memo_id": row.get("memo_id"), "mode": request.get("analysis_mode"),
            "summary": result["summary"], "judgment": result.get("judgment"),
            "technical_status": row.get("technical_status"),
            "content": {key: value for key, value in result.items()
                        if key not in ("valuation", "valuation_refs")} if detail else None,
            "reference": progress.get("research_review")}


def _weekly_analyses(row, *, detail):
    from bellomberg.core.research_analysis import research_digest, RESEARCH_ANALYSIS_MODE
    context, snapshot = _json(row.get("context_json")), _json(row.get("snapshot_json"))
    if _map(context.get("contract")).get("analysis_mode") != RESEARCH_ANALYSIS_MODE:
        return {}
    payload = _map(snapshot.get("payload"))
    data = _map(payload.get("data"))
    seal = _map(data.get("_research_thesis"))
    dossiers, reports = _map(seal.get("dossiers")), _map(seal.get("reports"))
    if not dossiers:
        return {}
    valid = (snapshot.get("sha256") == research_digest(payload)
             and seal.get("analysis_mode") == RESEARCH_ANALYSIS_MODE and seal.get("version") == 1
             and seal.get("dossier_sha256") == research_digest({"dossiers": dossiers, "tool_receipts": seal.get("tool_receipts")})
             and seal.get("thesis_sha256") == research_digest({"reports": reports, "dossier_sha256": seal.get("dossier_sha256")}))
    desk = _map(data.get("fundamentals"))
    valid = valid and _text(reports.get("fundamentals")) and desk.get("1") == reports.get("fundamentals")
    final = _text(desk.get("2"))
    report = final or _text(reports.get("fundamentals"))
    valid = valid and report and not report.startswith("[ERROR")
    state = _json(row.get("state_json"))
    result = {}
    for ticker, dossier in dossiers.items():
        if not valid or _map(dossier).get("ticker") != ticker:
            result[ticker] = {**_analysis_missing(), "status": "unavailable", "origin": "weekly",
                              "as_of": row.get("updated_at"), "memo_id": row["memo_id"]}
            continue
        reference = {key: seal[key] for key in ("analysis_mode", "version", "dossier_sha256", "thesis_sha256")}
        result[ticker] = {"status": "available", "origin": "weekly", "scope": "whole_committee_report",
            "as_of": row.get("updated_at"), "run_id": row["run_id"], "memo_id": row["memo_id"],
            "mode": RESEARCH_ANALYSIS_MODE, "summary": None, "judgment": None,
            "technical_status": state.get("status"), "round": 2 if final else 1,
            "reference": reference,
            "content": {"report": report, "documents": dossier.get("documents") or [],
                        "data_gaps": dossier.get("issues") or [], "records": dossier.get("records") or []} if detail else None}
    return result


def research_view(db_path, *, cache_dir, now=None, ticker=None, detail=False):
    """Return observations for followed/analysed symbols from an existing DB.

    A SQLite URI with mode=ro is compulsory. An absent DB is not an empty book.
    The result carries cache acquisition and actual provider dates separately.
    """
    now = now or datetime.now(timezone.utc)
    notices = []
    out = {"contract": CONTRACT, "status": "unavailable", "count": None, "items": [],
           "read_at": now.isoformat(), "notices": notices}
    path = Path(db_path).resolve()
    if not path.is_file():
        notices.append("database_unavailable")
        return out
    rows, prices, analyses = {}, {}, {}
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, query in (
                ("positions", "SELECT ticker,nome AS name FROM positions WHERE is_active=1 AND quantita<>0"),
                ("favorite_companies", "SELECT ticker,name FROM favorite_companies"),
            ):
                if table not in tables:
                    notices.append(table + "_unavailable")
                    continue
                for row in conn.execute(query):
                    if _text(row["ticker"]):
                        rows[row["ticker"]] = {"ticker": row["ticker"], "name": row["name"]}
            if "trade_idea_runs" in tables:
                for raw in conn.execute("SELECT * FROM trade_idea_runs ORDER BY COALESCE(finished_at,updated_at) DESC"):
                    row = dict(raw)
                    symbol = row["ticker"]
                    try:
                        request = _json(row.get("request_json"))
                    except (ValueError, TypeError):
                        notices.append("saved_analysis_mode_unavailable:" + symbol)
                        continue
                    if request.get("analysis_mode") != RESEARCH_ANALYSIS_MODE:
                        continue
                    rows.setdefault(symbol, {"ticker": symbol, "name": row.get("company_name")})
                    if not row.get("result_json") or symbol in analyses:
                        continue
                    try:
                        analyses[symbol] = _trade_analysis(row, detail=detail)
                    except (ValueError, TypeError):
                        analyses[symbol] = {**_analysis_missing(), "status": "unavailable", "origin": "trade_idea"}
                        notices.append("saved_analysis_unavailable:" + symbol)
            if "position_prices" in tables:
                for raw in conn.execute("SELECT * FROM position_prices ORDER BY timestamp DESC,id DESC"):
                    prices.setdefault(raw["ticker"], dict(raw))
            if "weekly_runs" in tables:
                for raw in conn.execute("SELECT * FROM weekly_runs ORDER BY updated_at DESC"):
                    try:
                        weekly = _weekly_analyses(dict(raw), detail=detail)
                    except (ValueError, TypeError):
                        notices.append("weekly_analysis_unavailable:" + str(raw["memo_id"]))
                        continue
                    for symbol, analysis in weekly.items():
                        rows.setdefault(symbol, {"ticker": symbol, "name": None})
                        existing = analyses.get(symbol)
                        if existing is None or (analysis.get("as_of") or "") > (existing.get("as_of") or ""):
                            analyses[symbol] = analysis
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        notices.append("database_unavailable:" + type(exc).__name__)
        return out
    for symbol, row in sorted(rows.items()):
        if ticker is not None and symbol != ticker:
            continue
        quote = _quote(prices.get(symbol), now)
        consensus = _consensus(symbol, cache_dir, now)
        cached_quote = consensus.pop("_quote")
        quote_refresh = consensus.pop("_quote_refresh")
        if cached_quote and cached_quote["value"] is not None:
            existing_time, candidate_time = _stamp(quote["observed_at"]), _stamp(cached_quote["observed_at"])
            if quote["value"] is None or (candidate_time is not None and (existing_time is None or candidate_time > existing_time)):
                quote = cached_quote
        quote["refresh"] = quote_refresh
        out["items"].append({**row, "quote": quote, "consensus": consensus,
                             "comparison": _comparison(quote, consensus),
                             "analysis": analyses.get(symbol) or _analysis_missing()})
    out.update(status="available", count=len(out["items"]))
    return out
