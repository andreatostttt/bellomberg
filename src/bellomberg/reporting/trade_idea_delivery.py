"""A per-run, hashed PDF/Excel package with recoverable SMTP outcomes."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from zipfile import BadZipFile

from bellomberg.reporting import email_sender
from bellomberg.reporting.trade_idea_report import build_trade_idea_report
from bellomberg.reporting.valuation_delivery import build_manifest, save_manifest
from bellomberg.core.research_analysis import (
    RESEARCH_ANALYSIS_MODE, RESEARCH_COMPLETION_CONTRACT, is_research_mode)
from bellomberg.core.trade_idea_policy import execution_policy


def _now():
    return datetime.now(timezone.utc).isoformat()


def _candidate_workbooks(ticker, results, attempts, roots, valuation_refs=()):
    """Accept only explicitly supplied candidate generations, never filesystem globbing."""
    if isinstance(results, dict):
        candidates = [(key, value) for key, value in results.items()]
    elif isinstance(results, list):
        candidates = [(value.get("ticker"), value) for value in results if isinstance(value, dict)]
    else:
        candidates = []
    rows, attachments, hashes, issues = [], [], {}, []
    references = {reference["generation_id"]: reference for reference in valuation_refs}
    seen_digests = set()
    included_generations = set()
    for key, result in candidates:
        if key != ticker or not isinstance(result, dict) or result.get("ticker", key) != ticker:
            issues.append("Out-of-scope valuation excluded: " + str(key))
            continue
        checked = build_manifest({ticker: result}, roots=roots, attempts=())
        for row in checked["valuations"]:
            if row.get("status") == "ready":
                try:
                    sidecar = json.loads(Path(row["path"]).with_suffix(".payload.json").read_text(encoding="utf-8"))
                    if not isinstance(sidecar, dict) or not isinstance(sidecar.get("valuation_decision") or {}, dict):
                        raise ValueError("Invalid valuation basis metadata")
                except (OSError, ValueError) as exc:
                    row.update(status="metadata_unreadable", artifact_status="unavailable",
                               reason="Workbook basis metadata unavailable: " + type(exc).__name__,
                               email_included=False)
                    rows.append(row)
                    continue
                fields = ("valuation_date", "currency", "price", "valuation_basis",
                          "fair_value_bear", "fair_value_base", "fair_value_bull",
                          "fair_value_weighted", "fair_value_final", "fair_value_nav")
                if (any(result.get(field) != sidecar.get(field) for field in fields)
                        or (result.get("valuation_decision") or {}).get("as_of")
                        != (sidecar.get("valuation_decision") or {}).get("as_of")):
                    row.update(status="model_basis_mismatch", artifact_status="unavailable",
                               reason="Model date, currency, values or assumptions differ from the workbook sidecar",
                               email_included=False)
                    rows.append(row)
                    continue
                reference = references.get(row["generation_id"])
                if (reference is None or any(reference.get(field) != row.get(field)
                        for field in ("snapshot_id", "generation_id", "valuation_date"))):
                    row.update(status="analysis_mismatch", artifact_status="unavailable",
                               reason="Workbook generation/date was not reviewed in the analytical result",
                               email_included=False)
                    rows.append(row)
                    continue
                digest = row["workbook_sha256"]
                if digest in seen_digests:
                    continue
                row["interpretation"] = reference["interpretation"]
                row["model_values"] = {field: result.get(field) for field in
                    ("currency", "price", "fair_value_bear", "fair_value_base", "fair_value_bull",
                     "fair_value_weighted", "fair_value_final", "fair_value_nav", "valuation_basis")}
                row["information_cutoff"] = (result.get("valuation_decision") or {}).get("as_of")
                path = row["path"]
                from bellomberg.valuation.trade_idea_model import model_exhibits
                try:
                    from openpyxl.utils.exceptions import InvalidFileException
                    exhibits = model_exhibits(sidecar, workbook_path=path)
                except (OSError, ValueError, TypeError, KeyError, BadZipFile, InvalidFileException) as exc:
                    exhibits = {"status": "blocked", "reasons": ["Workbook unreadable: " + type(exc).__name__]}
                if exhibits.get("status") != "complete":
                    row.update(status="model_dossier_unbound", artifact_status="unavailable", email_included=False,
                               reason="Exact model exhibits unavailable: " + "; ".join(exhibits.get("reasons") or []))
                    rows.append(row)
                    continue
                row["model_exhibits"] = exhibits
                seen_digests.add(digest)
                attachments.append(path)
                hashes[path] = digest
                included_generations.add(row["generation_id"])
            rows.append(row)
    for generation_id in sorted(set(references) - included_generations):
        if any(row.get("generation_id") == generation_id and row.get("status") != "ready" for row in rows):
            continue
        rows.append({"ticker": ticker, **references[generation_id], "status": "analysis_model_missing",
                     "artifact_status": "unavailable", "email_included": False,
                     "reason": "Reviewed Excel generation was not delivered: " + generation_id})
    if not rows:
        reasons = [str(a.get("reason") or a.get("error")) for a in attempts
                   if isinstance(a, dict) and a.get("ticker") == ticker and (a.get("reason") or a.get("error"))]
        rows = [{"ticker": ticker, "status": "incomplete", "artifact_status": "unavailable",
                 "reason": "; ".join(reasons) or "No verified candidate workbook was produced",
                 "email_included": False}]
    return {"valuations": rows, "attachments": attachments, "expected_hashes": hashes, "issues": issues}


def validate_research_package_scope(manifest):
    """Reject a mixed-mode package before any artifact is read or restored."""
    if (not is_research_mode(manifest)
            or manifest.get('completion_contract') != RESEARCH_COMPLETION_CONTRACT
            or manifest.get('model_status') != 'not_required'
            or any(manifest.get(key) for key in ('valuations', 'attempts', 'valuation_results', 'valuation_generations'))
            or any(row.get('kind') != 'pdf' for row in manifest.get('artifacts') or [])
            or any(row.get('kind') != 'pdf' for row in manifest.get('exact_artifact_receipts') or [])):
        raise ValueError('Research-only package contains an unexpected workbook or model activity')


def assess_trade_idea_completion(manifest):
    """Assess legacy or new packages without changing their immutable receipts."""
    reasons = []
    if manifest.get('judgment') == 'incomplete':
        reasons.append('The Capo analysis is incomplete; archived desk material is not a final memo')
    quality = manifest.get("pdf_quality") or {}
    if (execution_policy(manifest) is not None
            and (quality.get('execution_policy') != execution_policy(manifest)
                 or quality.get('content_integrity') != 'complete')):
        reasons.append('PDF quality policy or rendered content integrity differs from the accepted run')
    if quality.get("status") != "ready":
        reasons.extend(quality.get("reasons") or ["Substantive research PDF is incomplete"])
    workbooks = [item for item in manifest.get("artifacts") or []
                 if item.get("kind") == "xlsx" and item.get("status") == "ready"]
    research = (is_research_mode(manifest)
        and manifest.get('completion_contract') == RESEARCH_COMPLETION_CONTRACT)
    if research:
        try:
            validate_research_package_scope(manifest)
        except ValueError as exc:
            reasons.append(str(exc))
    elif not workbooks or manifest.get("model_status") != "ready":
        reasons.append("Economic Excel workbook is absent, invalid or differs from the reviewed generation")
    if manifest.get("completion_contract") and manifest.get("complete_package_status") != "ready":
        reasons.extend(manifest.get("complete_package_reasons") or [])
    reasons = list(dict.fromkeys(reasons))
    return {"status": "incomplete" if reasons else "ready", "reasons": reasons}


def preserve_trade_idea_artifacts(manifest, *, allowed_roots):
    """Seal exact bytes before the first immutable manifest publication."""
    from bellomberg.reporting.exact_artifacts import preserve_exact_artifacts
    rows = [{key: item[key] for key in ("path", "sha256", "kind")} for item in manifest["artifacts"]]
    for item in manifest["artifacts"]:
        if item["kind"] == "xlsx":
            sidecar = Path(item["path"]).with_suffix(".payload.json")
            rows.append({"path": str(sidecar), "sha256": sha256(sidecar.read_bytes()).hexdigest(),
                         "kind": "model_payload"})
    directory = Path(manifest["manifest_path"]).parent
    manifest["exact_artifact_receipts"] = preserve_exact_artifacts(rows, directory / ".exact-artifacts",
        allowed_roots=[directory, *allowed_roots])


def verify_exact_artifact_receipts(manifest):
    """Recovery may create only the exact files in this package inventory."""
    rows = manifest.get("exact_artifact_receipts")
    if rows is None:
        return
    if not isinstance(rows, list):
        raise ValueError("Exact artifact inventory is malformed")
    expected = {}
    for item in manifest.get("artifacts") or ():
        path = str(Path(item["path"]).resolve())
        expected[path] = (item["kind"], item["sha256"])
        if item["kind"] == "xlsx":
            expected[str(Path(path).with_suffix(".payload.json"))] = ("model_payload", None)
    actual = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Exact artifact inventory is malformed")
        path, digest = str(Path(row["path"]).resolve()), row.get("sha256")
        if (path in actual or path not in expected or row.get("kind") != expected[path][0]
                or not isinstance(digest, str) or len(digest) != 64
                or any(letter not in "0123456789abcdef" for letter in digest)
                or expected[path][1] is not None and digest != expected[path][1]):
            raise ValueError("Exact artifact recovery inventory differs from the attested package")
        actual.add(path)
    if actual != set(expected):
        raise ValueError("Exact artifact recovery inventory is incomplete")


def prepare_trade_idea_delivery(run, result, valuation_results=None, valuation_attempts=(), *,
                                output_dir, model_roots, language="it"):
    """Prepare a candidate-only package. Caller persists it before claiming SMTP."""
    run_id = str(run.get("id") or run.get("run_id") or result["run_id"])
    ticker = run["ticker"]
    if result.get("ticker") != ticker or result.get("run_id", run_id) != run_id:
        raise ValueError("Delivery identity differs from the accepted run")
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "delivery.json"
    # Recovery is allowed only through the durable original manifest. It must
    # never rewrite a completed report or select another generation on retry.
    if manifest_path.exists():
        raise ValueError("Delivery package already exists; recover the saved manifest")
    research = is_research_mode(run)
    if research and (result.get('valuation_refs') or valuation_results or valuation_attempts):
        raise ValueError('Research-only package cannot contain workbook claims or model activity')
    checked = ({'valuations': [], 'attachments': [], 'expected_hashes': {}, 'issues': []}
        if research else _candidate_workbooks(ticker, valuation_results, valuation_attempts, model_roots,
                                   result.get("valuation_refs") or ()))
    pdf = build_trade_idea_report(run, result, output_path=directory / "trade-idea.pdf",
                                  valuations=checked["valuations"], language=language)
    attachments = [pdf["path"], *checked["attachments"]]
    hashes = {pdf["path"]: pdf["sha256"], **checked["expected_hashes"]}
    artifacts = [{"id": "pdf", "kind": "pdf", "name": "trade-idea.pdf", "path": pdf["path"],
                  "sha256": pdf["sha256"], "status": pdf["status"], "reason": pdf["reason"]}]
    for index, path in enumerate(checked["attachments"], 1):
        model = next(row for row in checked["valuations"] if row.get("path") == path and row.get("status") == "ready")
        artifacts.append({"id": f"excel-{index}", "kind": "xlsx", "name": Path(path).name,
                          "path": path, "sha256": hashes[path], "status": "ready", "reason": "",
                          "snapshot_id": model["snapshot_id"], "generation_id": model["generation_id"],
                          "valuation_date": model["valuation_date"], "method": model.get("method")})
    omissions = [row["reason"] for row in checked["valuations"] if row.get("status") != "ready"]
    manifest = {"schema_version": 1, "run_type": "trade_idea", "run_id": run_id,
                **({'execution_policy': execution_policy(run)} if execution_policy(run) is not None else {}),
                "ticker": ticker, "judgment": result["judgment"], "summary": result["summary"],
                "destination": dict(run.get("destination") or result.get("destination") or
                                    {"kind": "none", "reason": ""}),
                "cutoff": run.get("cutoff") or result.get("cutoff") or run.get("started_at"),
                "language": language, "created_at": _now(), "manifest_path": str(manifest_path),
                "attachments": attachments, "expected_hashes": hashes, "artifacts": artifacts,
                "valuations": checked["valuations"], "attempts": list(valuation_attempts),
                "source_inventory": [{key: item.get(key) for key in ("id", "source", "as_of", "url")}
                                     for item in result.get("evidence") or []],
                "issues": checked["issues"], "omissions": omissions, "pdf_quality": pdf["quality"],
                "artifact_status": "ready" if pdf["status"] == "ready" and checked["attachments"] and not omissions else "partial",
                "model_status": "ready" if checked["attachments"] and not omissions else "incomplete",
                "email_status": "ready", "message_id": f"<trade-idea-{run_id}@bellomberg.local>",
                "result_sha256": sha256(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                                    allow_nan=False).encode()).hexdigest()}
    if research:
        manifest.update(analysis_mode=RESEARCH_ANALYSIS_MODE,
            completion_contract=RESEARCH_COMPLETION_CONTRACT,
            model_status='not_required',
            artifact_status='ready' if pdf['status'] == 'ready' else 'partial')
    completion = assess_trade_idea_completion(manifest)
    manifest.update(completion_contract=RESEARCH_COMPLETION_CONTRACT if research else "pdf-and-economic-workbook/2",
                    complete_package_status=completion["status"], complete_package_reasons=completion["reasons"],
                    email_status='ready' if completion['status'] == 'ready' else 'blocked')
    preserve_trade_idea_artifacts(manifest, allowed_roots=model_roots)
    save_manifest(manifest_path, manifest)
    return manifest


def verify_trade_idea_manifest(manifest, *, require_complete=False):
    if is_research_mode(manifest):
        validate_research_package_scope(manifest)
    verify_exact_artifact_receipts(manifest)
    if manifest.get("run_type") != "trade_idea" or manifest.get("schema_version") != 1:
        raise ValueError("Invalid Trade Idea delivery manifest")
    attachments, hashes = manifest.get("attachments"), manifest.get("expected_hashes")
    if (not isinstance(attachments, list) or not attachments or len(attachments) != len(set(attachments))
            or not isinstance(hashes, dict) or set(attachments) != set(hashes)):
        raise ValueError("Manifest does not attest every attachment")
    artifacts = manifest.get("artifacts", [])
    if {a["path"] for a in artifacts} != set(attachments):
        raise ValueError("Manifest artifact and attachment inventories differ")
    if not any(a.get("kind") == "pdf" for a in artifacts):
        raise ValueError("Trade Idea requires a PDF attachment")
    for artifact in artifacts:
        if artifact.get("sha256") != hashes.get(artifact["path"]):
            raise ValueError("Manifest artifact hash mismatch")
    for path in attachments:
        if sha256(Path(path).read_bytes()).hexdigest() != hashes[path]:
            raise ValueError("Attachment changed after manifest validation: " + Path(path).name)
    for row in manifest.get("exact_artifact_receipts") or ():
        if row["kind"] == "model_payload" and sha256(Path(row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Model payload changed after manifest validation: " + Path(row["path"]).name)
    if require_complete:
        completion = assess_trade_idea_completion(manifest)
        if completion["status"] != "ready":
            raise ValueError("Trade Idea package is not complete: " + "; ".join(completion["reasons"]))
    return True


def recover_trade_idea_delivery(manifest_path, *, run_id, ticker, result=None):
    """Recover the exact original package after a persistence/send interruption."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("run_id") != run_id or manifest.get("ticker") != ticker:
        raise ValueError("Saved delivery belongs to another run")
    if result is not None:
        expected = sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if manifest.get("result_sha256") != expected:
            raise ValueError("Saved delivery belongs to a different analytical result")
    verify_trade_idea_manifest(manifest)
    return manifest


def send_trade_idea_delivery(manifest, *, language=None):
    """Send an already claimed package once; durable retry decisions belong to store."""
    language = language or manifest.get("language", "it")
    receipt = {"status": "failed", "email_status": "failed", "message_id": manifest.get("message_id"),
               "attempted_at": _now(), "mime_attachments": []}
    try:
        verify_trade_idea_manifest(manifest, require_complete=True)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        receipt.update(status="blocked", email_status="blocked", error=str(exc), email_error=str(exc))
        return receipt
    it = language == "it"
    judgment_labels = {"favorable": "Favorevole", "rejected": "Respinta", "watch": "Da monitorare", "incomplete": "Incompleta"}
    judgment = judgment_labels.get(manifest["judgment"], manifest["judgment"]) if it else manifest["judgment"].title()
    destination = manifest.get("destination") or {"kind": "none", "reason": ""}
    route_labels = {"dcn": ("Proposta in DCN", "Pending DCN proposal"),
                    "research": ("In ricerca", "In Research"),
                    "none": ("Nessuna destinazione", "No destination")}
    route_label = route_labels.get(destination.get("kind"), route_labels["none"])[0 if it else 1]
    partial = manifest["pdf_quality"]["status"] != "ready"
    completion = assess_trade_idea_completion(manifest)
    subject = f"Trade Idea | {manifest['ticker']} | {str(manifest.get('cutoff') or manifest['created_at'])[:10]} | {judgment} | {route_label}"
    if partial:
        subject += " | " + ("Ricerca parziale" if it else "Partial research")
    if completion["status"] != "ready":
        subject += " | " + ("Pacchetto incompleto" if it else "Incomplete package")
    body = "<p>" + escape(manifest["summary"]) + "</p>"
    body += "<p><b>" + escape(route_label) + "</b>"
    if destination.get("reason"):
        body += ": " + escape(str(destination["reason"]))
    body += "</p>"
    body += "<p>Run: " + escape(manifest["run_id"]) + "</p>"
    if partial:
        body += "<p><b>" + ("Ricerca incompleta: " if it else "Incomplete research: ") + "</b>"
        body += escape("; ".join(manifest["pdf_quality"]["reasons"])) + "</p>"
    if manifest.get("omissions"):
        body += "<p><b>" + ("Excel esclusi / modello incompleto" if it else "Excluded Excel / incomplete model") + "</b></p><ul>"
        body += "".join("<li>" + escape(reason) + "</li>" for reason in manifest["omissions"]) + "</ul>"
    if completion["status"] != "ready":
        body += "<p><b>" + ("Pacchetto incompleto: " if it else "Incomplete package: ") + "</b>"
        body += escape("; ".join(completion["reasons"])) + "</p>"
    body += "<p>" + ("Nessun ordine automatico. Gli allegati sono le copie verificate della run."
                      if it else "No automatic orders. Attachments are the verified run artifacts.") + "</p>"
    sent = email_sender.invia_email_multi_allegati(manifest["attachments"], oggetto=subject,
        body_extra=body, message_title="Bellomberg Research / Trade Idea", message_id=manifest["message_id"],
        expected_hashes=manifest["expected_hashes"], require_all_hashes=True,
        delivery_receipt=receipt, language=language)
    status = "accepted" if sent else {"package_failed": "blocked"}.get(receipt.get("email_status"), receipt.get("email_status", "failed"))
    receipt.update(status=status, email_status=status, error=receipt.get("email_error"), finished_at=_now())
    if sent:
        receipt["accepted_at"] = receipt["finished_at"]
    return receipt
