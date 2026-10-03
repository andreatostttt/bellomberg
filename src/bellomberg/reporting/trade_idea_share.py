"""Explicit private/shared exports; no SMTP, portfolio lookup or AI is available.

The shared surface is rebuilt from the common engine's public projection. Free
committee prose is deliberately excluded: its financial sections can contain
personal portfolio information too. The public PDF is a model extract, never a
second claim that the complete investment research has been performed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from pypdf import PdfReader
from pypdf.generic import IndirectObject

from .trade_idea_delivery import _candidate_workbooks, verify_trade_idea_manifest
from .valuation_delivery import save_manifest


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _exclusions(run, result, model_count):
    return [
        {"category": "personal_context", "label": "Book, personal sizing, trades, decisions and routing", "count": 1},
        {"category": "pm_content", "label": "PM view, notes, feedback and objections", "count":
         int(bool(run.get("pm_view") or result.get("pm_view"))) + len(result.get("history_review") or [])},
        {"category": "committee_prose", "label": "Original narrative and committee replies with mixed private context", "count":
         sum(len(section.get("paragraphs") or []) for section in result.get("dossier") or []) + len(result.get("objections") or [])},
        {"category": "local_metadata", "label": "Local paths, private run IDs, author metadata and original attachments", "count": model_count},
        {"category": "original_annotations", "label": "Original comments, hidden sheets, names, properties and links are not copied", "count": model_count},
    ]


def _private_terms(run, result, extra=()):
    """Defense in depth, in addition to the projection; never used as a redactor."""
    terms = []
    def strings(value):
        if isinstance(value, str):
            if len(value.strip()) >= 12:
                terms.append(value.strip())
        elif isinstance(value, dict):
            for item in value.values():
                strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                strings(item)
    for key in ("pm_view", "private_content", "portfolio", "book", "pm_feedback", "notes", "history"):
        strings(run.get(key))
    for key in ("pm_view", "pm_view_response", "history_review", "proposal"):
        strings(result.get(key))
    strings(extra)
    return list(dict.fromkeys(terms))


_PRIVATE_PATH = re.compile(r"(?i)(?:\b[A-Z]:[\\/]|(?:file|vscode|plugin)://|\\\\[^\s\\]+[\\/]|/Users/|/home/|/tmp/)")


def _check_text(text, private_terms, surface):
    if _PRIVATE_PATH.search(text):
        raise ValueError("Private/local path found in shared " + surface)
    normalized = re.sub(r"\s+", " ", text).casefold()
    for term in private_terms:
        if re.sub(r"\s+", " ", term).casefold() in normalized:
            raise ValueError("Private content found in shared " + surface)


def inspect_shared_workbook(path, *, private_terms=(), allowed_urls=()):
    """Inspect every OPC part, including channels that are absent from a preview."""
    allowed_urls = set(allowed_urls)
    counts = {"parts": 0, "cells": 0, "comments": 0, "hidden_sheets": 0,
              "defined_names": 0, "properties": 0, "links": 0, "formulas": 0}
    with ZipFile(path) as archive:
        for name in archive.namelist():
            counts["parts"] += 1
            lower = name.lower()
            if any(token in lower for token in ("externallinks/", "vbaproject", "customxml/", "embeddings/",
                                                 "connections.xml", "querytables/")):
                raise ValueError("Unapproved dependency in shared workbook")
            data = archive.read(name)
            if not (lower.endswith(".xml") or lower.endswith(".rels") or lower.endswith(".vml")):
                raise ValueError("Opaque part is not explicitly included in shared workbook")
            text = data.decode("utf-8")
            _check_text(text, private_terms, "workbook part")
            node = ET.fromstring(data)
            _check_text("".join(node.itertext()), private_terms, "decoded workbook text")
            _check_text(json.dumps([item.attrib for item in node.iter()]), private_terms, "workbook attributes")
            for item in node.iter():
                tag = item.tag.rsplit("}", 1)[-1]
                if tag == "c": counts["cells"] += 1
                elif tag == "f":
                    counts["formulas"] += 1
                    formula = item.text or ""
                    if re.search(r"(?i)\b(?:WEBSERVICE|RTD|DDE|HYPERLINK)\s*\(|\[[^\]]+\]", formula):
                        raise ValueError("External/active formula in shared workbook")
                elif tag == "comment": counts["comments"] += 1
                elif tag == "definedName": counts["defined_names"] += 1
                elif tag == "sheet" and item.get("state", "visible") != "visible":
                    counts["hidden_sheets"] += 1
                elif tag == "Relationship" and item.get("TargetMode") == "External":
                    counts["links"] += 1
                    if (not item.get("Type", "").endswith("/hyperlink")
                            or item.get("Target") not in allowed_urls):
                        raise ValueError("Unapproved external link in shared workbook")
                if lower.startswith("docprops/"):
                    counts["properties"] += 1
                    if tag in ("creator", "lastModifiedBy") and item.text not in (None, "", "openpyxl", "Bellomberg Research"):
                        raise ValueError("Private author metadata in shared workbook")
            if lower == "docprops/custom.xml":
                raise ValueError("Custom properties are not included in shared workbooks")
    if not counts["formulas"]:
        raise ValueError("Shared model has no live formulas")
    return {"status": "passed", **counts}


def inspect_shared_pdf(path, *, private_terms=(), allowed_urls=()):
    reader = PdfReader(str(path))
    allowed_urls = set(allowed_urls)
    _check_text(json.dumps(dict(reader.metadata or {}), default=str), private_terms, "PDF metadata")
    # Metadata, outlines and dormant action dictionaries need inspection even
    # when they do not appear in selectable page text.
    object_keys = {(identifier, generation) for generation, objects in reader.xref.items() for identifier in objects}
    object_keys.update((identifier, 0) for identifier in reader.xref_objStm)
    for identifier, generation in sorted(object_keys):
        if identifier == 0:
            continue
        item = reader.get_object(IndirectObject(identifier, generation, reader))
        _check_text(str(item), private_terms, "PDF object")
        if hasattr(item, "get"):
            if item.get("/AA") or item.get("/EmbeddedFiles") or item.get("/JavaScript"):
                raise ValueError("Active or embedded content in shared PDF")
            if item.get("/S") in ("/JavaScript", "/Launch", "/GoToR", "/SubmitForm", "/ImportData"):
                raise ValueError("Active content in shared PDF")
            if item.get("/Type") == "/Metadata" and hasattr(item, "get_data"):
                _check_text(item.get_data().decode("utf-8"), private_terms, "PDF XML metadata")
    links = 0
    for page in reader.pages:
        _check_text(page.extract_text() or "", private_terms, "PDF text")
        for annotation in page.get("/Annots", []):
            item = annotation.get_object()
            _check_text(str(item), private_terms, "PDF annotation")
            action = item.get("/A")
            if action:
                action = action.get_object()
                if action.get("/S") != "/URI" or action.get("/URI") not in allowed_urls:
                    raise ValueError("Unapproved action or link in shared PDF")
                links += 1
    if reader.trailer["/Root"].get("/OpenAction") or reader.trailer["/Root"].get("/AcroForm"):
        raise ValueError("Active content is not included in shared PDF")
    if not reader.pages or not all(bool(page.extract_text()) for page in reader.pages):
        raise ValueError("Shared PDF must contain selectable text on every page")
    return {"status": "passed", "pages": len(reader.pages), "links": links,
            "text_selectable": all(bool(page.extract_text()) for page in reader.pages)}


def _models(run, result, valuation_results, valuation_attempts, roots):
    if (result.get("ticker") != run.get("ticker") or
            result.get("run_id", run.get("id")) != run.get("id", run.get("run_id"))):
        raise ValueError("Export identity differs from accepted research")
    checked = _candidate_workbooks(run["ticker"], valuation_results, valuation_attempts, roots,
                                   result.get("valuation_refs") or ())
    reasons = [row.get("reason", "Invalid candidate model") for row in checked["valuations"] if row.get("status") != "ready"]
    models = []
    if not checked["attachments"]:
        reasons.append("No valid reviewed economic Excel model is available")
    from bellomberg.valuation.trade_idea_model import public_model_payload
    for row in checked["valuations"]:
        if row.get("status") != "ready":
            continue
        payload = json.loads(Path(row["path"]).with_suffix(".payload.json").read_text(encoding="utf-8"))
        projection = public_model_payload(payload)
        if projection.get("status") != "ready":
            reasons.extend(projection.get("reasons") or ["Public model provenance is not qualified"])
        models.append({"row": row, "payload": payload, "projection": projection})
    return models, list(dict.fromkeys(reasons))


def preview_shareable_package(run, result, valuation_results=None, valuation_attempts=(), *, model_roots):
    """No files created, no private values returned; preparation is deterministic."""
    models, reasons = _models(run, result, valuation_results, valuation_attempts, model_roots)
    return {"status": "blocked" if reasons else "ready", "reasons": reasons,
            "included_sections": ["Public economic model", "Model-derived PDF extract", "Public sources", "Version inventory"],
            "exclusions": _exclusions(run, result, len(models)),
            "model_preflight": [{"status": item["projection"].get("status"),
                                  "method": item["row"].get("method"),
                                  "source_count": len(item["projection"].get("sources") or [])} for item in models],
            "report_kind": "shareable_model_extract",
            "limitation": "The extract excludes original committee prose and personal compatibility. It is not the complete research dossier."}


def _archive_members(path, files, documents):
    """All members supplied explicitly; names and content cannot collide."""
    names = [Path(file).name for file in files] + list(documents)
    if len(set(names)) != len(names):
        raise ValueError("Export members have duplicate names")
    temporary = Path(path).with_name(Path(path).name + "." + uuid4().hex + ".tmp")
    try:
        with ZipFile(temporary, "x", compression=ZIP_DEFLATED) as archive:
            for file in files:
                archive.writestr(Path(file).name, Path(file).read_bytes())
            for name, value in documents.items():
                archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_private_package(manifest, *, output_path):
    verify_trade_idea_manifest(manifest)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    inventory = {"schema": "trade-idea-private-package/1", "scope": "private",
        "run_id": manifest["run_id"], "ticker": manifest["ticker"],
        "cutoff": manifest.get("cutoff"), "judgment": manifest["judgment"],
        "complete_package_status": manifest.get("complete_package_status", "legacy_not_attested"),
        "sources": manifest.get("source_inventory") or [],
        "versions": [{key: item.get(key) for key in ("name", "kind", "sha256", "snapshot_id", "generation_id", "valuation_date", "method")}
                     for item in manifest["artifacts"]]}
    _archive_members(path, manifest["attachments"], {"inventory.json": inventory})
    # Detect a mutation during archive assembly as well as before it.
    verify_trade_idea_manifest(manifest)
    with ZipFile(path) as archive:
        for item in manifest["artifacts"]:
            if sha256(archive.read(Path(item["path"]).name)).hexdigest() != item["sha256"]:
                raise ValueError("Private export bytes differ from the sealed package")
    return {"scope": "private", "path": str(path.resolve()), "name": path.name,
            "sha256": sha256(path.read_bytes()).hexdigest(), "status": "ready"}


def build_shareable_package(run, result, valuation_results=None, valuation_attempts=(), *, output_dir,
                            model_roots, language="it", private_terms=()):
    if language not in ("it", "en"):
        raise ValueError("Unsupported shared report language")
    models, reasons = _models(run, result, valuation_results, valuation_attempts, model_roots)
    if reasons:
        raise ValueError("Shared export blocked: " + "; ".join(reasons))
    source_fingerprint = _digest({"ticker": run["ticker"], "language": language,
        "models": [{"workbook": item["row"]["workbook_sha256"], "projection": item["projection"]} for item in models]})
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory/"share-manifest.json"
    if manifest_path.exists():
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        if saved.get("source_fingerprint") != source_fingerprint:
            raise ValueError("Shared package sealed for a different model generation")
        verify_shareable_package(saved, private_terms=_private_terms(run, result, private_terms))
        return saved
    lock = directory/".share-build.lock"
    with lock.open("x", encoding="utf-8"):
        pass
    try:
        from bellomberg.valuation.trade_idea_model import prepare_shareable_model, model_exhibits, candidate_model_usability
        from .trade_idea_report import build_model_extract
        private = _private_terms(run, result, private_terms)
        shared_models, sources, artifacts, checks = [], [], [], []
        for index, item in enumerate(models, 1):
            shared = prepare_shareable_model(item["payload"], directory/f"model-{index}")
            if not shared.get("path") or candidate_model_usability(shared).get("usable") is not True:
                raise ValueError("Public common-engine model is invalid")
            source_rows = item["projection"].get("sources") or []
            sources.extend(source_rows)
            urls = [row["url"] for row in source_rows if row.get("url")]
            check = inspect_shared_workbook(shared["path"], private_terms=private, allowed_urls=urls)
            exhibits = model_exhibits(shared, workbook_path=shared["path"])
            if exhibits.get("status") != "complete":
                raise ValueError("Public common-engine exhibits are not bound to the regenerated workbook")
            shared_models.append({**shared, "model_exhibits": exhibits})
            artifacts.append({"kind": "xlsx", "name": Path(shared["path"]).name,
                "path": str(Path(shared["path"]).resolve()), "sha256": sha256(Path(shared["path"]).read_bytes()).hexdigest(),
                "snapshot_id": shared.get("snapshot_id"), "generation_id": shared.get("generation_id"),
                "valuation_date": shared.get("valuation_date"), "method": shared.get("method")})
            checks.append({"kind": "xlsx", **check})
        unique_sources = {json.dumps(row, sort_keys=True, ensure_ascii=False): row for row in sources}
        source_inventory = {"schema": "public-source-inventory/1", "sources": list(unique_sources.values())}
        _check_text(json.dumps(source_inventory, ensure_ascii=False), private, "source inventory")
        urls = [row["url"] for row in sources if row.get("url")]
        pdf = build_model_extract(run["ticker"], shared_models, sources, output_path=directory/"model-extract.pdf", language=language)
        checks.append({"kind": "pdf", **inspect_shared_pdf(pdf["path"], private_terms=private, allowed_urls=urls)})
        artifacts.insert(0, {"kind": "pdf", "name": "model-extract.pdf", "path": pdf["path"], "sha256": pdf["sha256"]})
        version_inventory = {"schema": "public-model-versions/1", "ticker": run["ticker"],
            "language": language, "report_kind": "shareable_model_extract",
            "versions": [{key: item.get(key) for key in ("name", "kind", "sha256", "snapshot_id", "generation_id", "valuation_date", "method")}
                         for item in artifacts]}
        _check_text(json.dumps(version_inventory, ensure_ascii=False), private, "version inventory")
        package_path = directory/"shareable-package.zip"
        _archive_members(package_path, [item["path"] for item in artifacts], {
            "source-inventory.json": source_inventory, "version-inventory.json": version_inventory})
        manifest = {"schema": "trade-idea-shareable-package/1", "scope": "shareable", "status": "ready",
            "report_kind": "shareable_model_extract", "source_fingerprint": source_fingerprint,
            "created_at": datetime.now(timezone.utc).isoformat(), "exclusions": _exclusions(run, result, len(models)),
            "privacy_checks": checks, "artifacts": artifacts, "path": str(package_path), "name": package_path.name,
            "sha256": sha256(package_path.read_bytes()).hexdigest()}
        save_manifest(manifest_path, manifest)
        verify_shareable_package(manifest)
        return manifest
    finally:
        lock.unlink(missing_ok=True)


def verify_shareable_package(manifest, *, private_terms=()):
    if (manifest.get("schema") != "trade-idea-shareable-package/1" or manifest.get("scope") != "shareable"
            or manifest.get("status") != "ready" or manifest.get("report_kind") != "shareable_model_extract"):
        raise ValueError("Invalid shared package receipt")
    artifacts = manifest.get("artifacts") or []
    if (sum(item.get("kind") == "pdf" for item in artifacts) != 1
            or not any(item.get("kind") == "xlsx" for item in artifacts)
            or any(item.get("kind") not in ("pdf", "xlsx") for item in artifacts)):
        raise ValueError("Shared package requires exactly one PDF and economic Excel models")
    if any(Path(item["name"]).name != item["name"] or "/" in item["name"] or "\\" in item["name"] for item in artifacts):
        raise ValueError("Invalid shared ZIP member name")
    package_path = Path(manifest["path"])
    if sha256(package_path.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Shared ZIP changed after seal")
    expected = {item["name"] for item in artifacts} | {"source-inventory.json", "version-inventory.json"}
    if len(expected) != len(artifacts) + 2:
        raise ValueError("Shared ZIP member names collide")
    with ZipFile(package_path) as archive:
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("Unexpected shared ZIP members")
        sources = json.loads(archive.read("source-inventory.json"))
        versions = json.loads(archive.read("version-inventory.json"))
        if (sources.get("schema") != "public-source-inventory/1"
                or versions.get("schema") != "public-model-versions/1"
                or versions.get("report_kind") != "shareable_model_extract"):
            raise ValueError("Invalid public inventories")
        wanted_versions = [{key: item.get(key) for key in ("name", "kind", "sha256", "snapshot_id", "generation_id", "valuation_date", "method")}
                           for item in artifacts]
        if versions.get("versions") != wanted_versions:
            raise ValueError("Public version inventory differs from sealed artifacts")
        allowed_urls = [row["url"] for row in sources.get("sources") or [] if row.get("url")]
        _check_text(json.dumps(sources, ensure_ascii=False), private_terms, "source inventory")
        _check_text(json.dumps(versions, ensure_ascii=False), private_terms, "version inventory")
        for item in artifacts:
            digest = sha256(Path(item["path"]).read_bytes()).hexdigest()
            if digest != item["sha256"] or sha256(archive.read(item["name"])).hexdigest() != digest:
                raise ValueError("Shared attachment changed after seal")
            if item["kind"] == "xlsx":
                inspect_shared_workbook(item["path"], private_terms=private_terms, allowed_urls=allowed_urls)
            else:
                inspect_shared_pdf(item["path"], private_terms=private_terms, allowed_urls=allowed_urls)
    return True
