"""Authenticated read-only Fund views; no provider, job or workbook generation."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from bellomberg.market_data.fundamentals_view import research_view


def _archive(roots):
    records, notices = [], []
    for index, folder in enumerate(roots):
        root = Path(folder).resolve()
        if not root.is_dir():
            notices.append("archive_directory_unavailable")
            continue
        for candidate in sorted(root.rglob("*.xlsx")):
            path = candidate.resolve()
            if not path.is_file() or not path.is_relative_to(root):
                continue
            identity = sha256((str(index) + ":" + candidate.relative_to(root).as_posix()).encode()).hexdigest()
            payload, reason, digest = {}, None, None
            sidecar = path.with_suffix(".payload.json")
            try:
                if sidecar.exists():
                    if not sidecar.resolve().is_relative_to(root):
                        raise ValueError("metadata outside trusted archive")
                    payload = json.loads(sidecar.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("invalid archived metadata")
                digest = sha256(path.read_bytes()).hexdigest()
                if payload.get("workbook_sha256") and digest != payload["workbook_sha256"]:
                    reason = "archive_hash_mismatch"
            except (OSError, ValueError):
                reason = "archive_unavailable"
            records.append({"id": identity, "file": path.name, "ticker": payload.get("ticker"),
                "as_of": payload.get("valuation_date") or payload.get("generated_at") or payload.get("_timestamp"),
                "file_modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "historical": True, "available": reason is None, "reason": reason,
                "integrity": "recorded_hash" if payload.get("workbook_sha256") and reason is None else "unattested",
                "download_path": "/fundamentals/archive/" + identity + "/download",
                "_path": path, "_sha256": digest})
    return records, notices


def create_fundamentals_router(require_session, *, db_provider, cache_dir, roots, refresh_provider=None):
    """Paths come from application configuration, never request parameters."""
    allowed = tuple(Path(root).resolve() for root in roots)
    router = APIRouter(prefix="/fundamentals", tags=["fundamentals"],
                       dependencies=[Depends(require_session)])

    @router.get("/research")
    def research(response: Response):
        response.headers["Cache-Control"] = "no-store"
        view = research_view(db_provider(), cache_dir=cache_dir)
        if refresh_provider is not None:
            view['market_refresh'] = refresh_provider()
        return view

    @router.get("/research/{ticker}")
    def company(ticker: str, response: Response):
        if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_-]{0,39}", ticker):
            raise HTTPException(422, "invalid_ticker")
        response.headers["Cache-Control"] = "no-store"
        view = research_view(db_provider(), cache_dir=cache_dir, ticker=ticker, detail=True)
        if view["status"] != "available":
            raise HTTPException(503, {"code": "research_unavailable", "notices": view["notices"]})
        if not view["items"]:
            raise HTTPException(404, "company_not_followed")
        return view["items"][0]

    @router.get("/archive")
    def archive(response: Response):
        response.headers["Cache-Control"] = "no-store"
        records, notices = _archive(allowed)
        return {"items": [{key: value for key, value in row.items() if not key.startswith("_")} for row in records],
                "notices": notices}

    @router.get("/archive/{artifact_id}/download")
    def archive_download(artifact_id: str):
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise HTTPException(404, "archived_workbook_not_found")
        records, _ = _archive(allowed)
        item = next((row for row in records if row["id"] == artifact_id), None)
        if item is None:
            raise HTTPException(404, "archived_workbook_not_found")
        if not item["available"]:
            raise HTTPException(409, item["reason"])
        try:
            path = item["_path"].resolve(strict=True)
            if not any(path.is_relative_to(root) for root in allowed):
                raise ValueError("archive root changed")
            contents = path.read_bytes()
            if sha256(contents).hexdigest() != item["_sha256"]:
                raise ValueError("archive changed during download")
        except (OSError, ValueError):
            raise HTTPException(409, "archived_workbook_changed")
        return Response(contents, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Cache-Control": "no-store", "X-Valuation-Copy": "historical",
                     "Content-Disposition": "attachment; filename*=UTF-8''" + quote(item["file"])})

    return router
