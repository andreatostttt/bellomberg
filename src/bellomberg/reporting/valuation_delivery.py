"""Run receipts and exact-generation attachments; never infer identity from names."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile


def describe_result(ticker, result):
    result = result if isinstance(result, dict) else {}
    exposure = result.get("method") == "exposure_analysis"
    usability_key = "analysis_usability" if exposure else "valuation_usability"
    malformed = next((key for key in ("valuation_usability", usability_key, "_thesis_saved",
                                      "model_publication", "valuation_decision")
                      if key in result and not isinstance(result[key], dict)), None)
    usability = result.get(usability_key) if isinstance(result.get(usability_key), dict) else {}
    thesis = result.get("_thesis_saved") if isinstance(result.get("_thesis_saved"), dict) else {}
    publication = result.get("model_publication") if isinstance(result.get("model_publication"), dict) else {}
    decision = result.get("valuation_decision") if isinstance(result.get("valuation_decision"), dict) else {}
    reasons = usability.get("reasons")
    if malformed is None and "reasons" in usability and (
            not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons)):
        malformed = usability_key + ".reasons"
    usable = malformed is None and usability.get("usable") is True
    if exposure and malformed is None:
        from bellomberg.valuation.trade_idea_model import candidate_model_usability
        checked = candidate_model_usability(result)
        usable, reasons = checked["usable"], checked["reasons"]
    row = {"ticker": ticker, "snapshot_id": result.get("snapshot_id"),
            "generation_id": result.get("generation_id"), "path": result.get("path"),
            "thesis_id": thesis.get("thesis_id"),
            "method": decision.get("method_id"),
            "revision_origin": "reused" if result.get("reused") else "generated" if result.get("path") else "not_created",
            "valuation_date": result.get("valuation_date"),
            "status": "calculated" if usable else "incomplete",
            "reason": "" if usable else ("Metadati risultato non validi: " + malformed if malformed else
                                          result.get("error") or "; ".join(reasons or [])
                                          or "Valutazione incompleta; motivo non disponibile"),
            "model_publication": {"status": publication.get("status", "not_verified"),
                                  "reason": publication.get("reason", "Stato della versione corrente non verificato")},
            # publication_status is the v1 artifact field, retained for old receipts.
            "artifact_status": "not_verified", "publication_status": "not_verified", "email_included": False,
            "recorded_at": datetime.now(timezone.utc).isoformat()}
    if exposure:
        row.update(model_kind="exposure", objective="observed_exposure_analysis", intrinsic_value_applicable=False)
    return row


def _exposure_payload_digest(payload):
    """Bind the observational receipt to its exact acquired records and results."""
    keys = ("ticker", "method", "snapshot_id", "generation_id", "valuation_date", "currency", "price",
            "acquisition_snapshot", "valuation_decision", "analysis_usability", "exposure_analysis", "input_consumption")
    return sha256(json.dumps({key: payload.get(key) for key in keys}, sort_keys=True,
                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def build_manifest(results, *, roots, attempts=()):
    """Allow only usable, registered artifacts whose sidecar and bytes match the request."""
    allowed = [Path(root).resolve() for root in roots]
    rows, files, hashes = [], [], {}
    for ticker, result in sorted((results or {}).items()):
        row = describe_result(ticker, result)
        rows.append(row)
        if row["status"] == "incomplete":
            continue
        def reject(status, reason):
            row.update(status=status, reason=reason, artifact_status="unavailable", publication_status="unavailable")
        try:
            path = Path(row["path"]).resolve() if row["path"] else None
        except (OSError, ValueError, TypeError) as exc:
            reject("file_unreadable", "Percorso workbook invalido: " + type(exc).__name__)
            continue
        if path is None or not path.is_file():
            reject("file_missing", "Calcolo disponibile ma workbook assente")
            continue
        if not any(path.is_relative_to(root) for root in allowed):
            reject("outside_model_roots", "Workbook fuori dalle cartelle dei modelli")
            continue
        if not row["thesis_id"]:
            reject("not_registered", "Registrazione snapshot non confermata: " + str(result.get("_thesis_saved")))
            continue
        try:
            sidecar = json.loads(path.with_suffix(".payload.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            reject("metadata_missing", "Metadati workbook non leggibili: " + type(exc).__name__)
            continue
        exposure = result.get("method") == "exposure_analysis"
        sidecar_usability = sidecar.get("analysis_usability" if exposure else "valuation_usability") if isinstance(sidecar, dict) else None
        if not isinstance(sidecar_usability, dict):
            reject("metadata_mismatch", "Metadati utilizzabilita workbook non validi")
            continue
        if (not row["snapshot_id"] or not row["generation_id"]
                or any(sidecar.get(key) != row[key] for key in ("ticker", "snapshot_id", "generation_id"))
                or sidecar_usability.get("usable") is not True):
            reject("metadata_mismatch", "Ticker, snapshot, generazione o utilizzabilita discordanti")
            continue
        try:
            digest = sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            reject("file_unreadable", "Workbook non leggibile: " + type(exc).__name__)
            continue
        if digest != result.get("workbook_sha256") or digest != sidecar.get("workbook_sha256"):
            reject("file_modified", "Workbook modificato rispetto alla generazione; copia conservata")
            continue
        if exposure:
            from bellomberg.valuation.trade_idea_model import candidate_model_usability, model_exhibits
            checked = candidate_model_usability(sidecar)
            if (checked.get("kind") != "exposure" or not checked.get("usable")
                    or not row["valuation_date"] or sidecar.get("valuation_date") != row["valuation_date"]
                    or _exposure_payload_digest(result) != _exposure_payload_digest(sidecar)):
                reject("metadata_mismatch", "Identita, data o osservazioni del modello exposure discordanti")
                continue
            try:
                from openpyxl.utils.exceptions import InvalidFileException
                exhibits = model_exhibits(sidecar, workbook_path=path)
            except (OSError, ValueError, TypeError, KeyError, BadZipFile, InvalidFileException) as exc:
                reject("model_dossier_unbound", "Workbook exposure non leggibile: " + type(exc).__name__)
                continue
            if exhibits.get("status") != "complete":
                reject("model_dossier_unbound", "Prospetti exposure non verificati: " + "; ".join(exhibits.get("reasons") or []))
                continue
            row["analysis_payload_sha256"] = _exposure_payload_digest(sidecar)
        file = str(path)
        row.update(status="ready", reason="Generazione registrata e contenuto verificato",
                   artifact_status="available", publication_status="available", workbook_sha256=digest, path=file)
        files.append(file)
        hashes[file] = digest
    return {"schema_version": 1, "valuations": rows, "attempts": list(attempts),
            "attachments": files, "expected_hashes": hashes, "email_status": "not_attempted"}


def recover_manifest(memo_id, *, receipts_dir, roots, memo_sha256):
    """Recheck the one saved run receipt for this memo; never infer files by mtime."""
    matches = []
    for path in Path(receipts_dir).glob("bellomberg_*_valuations.json"):
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(source, dict) and source.get("memo_id") == memo_id:
            matches.append((path, source))
    if len(matches) != 1:
        result = build_manifest({}, roots=roots)
        result.update(memo_id=memo_id, issues=["Receipt originale assente o ambiguo: nessun Excel attestabile"])
        return result

    path, source = matches[0]
    if (not isinstance(memo_sha256, str) or len(memo_sha256) != 64
            or source.get("memo_sha256") != memo_sha256):
        result = build_manifest({}, roots=roots)
        result.update(memo_id=memo_id, source_manifest=str(path),
                      issues=["Testo del memo originale assente o diverso dal receipt: nessun Excel attestabile"])
        return result
    rows = source.get("valuations")
    hashes = source.get("expected_hashes")
    attachments = source.get("attachments")
    if (source.get("schema_version") != 1 or not isinstance(rows, list)
            or not isinstance(hashes, dict) or not isinstance(attachments, list)):
        result = build_manifest({}, roots=roots)
        result.update(memo_id=memo_id, source_manifest=str(path),
                      issues=["Receipt originale malformato: nessun Excel attestabile"])
        return result

    results, issues, skipped = {}, [], []
    counts = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("ticker"), str):
            ticker = row["ticker"]
            counts[ticker] = counts.get(ticker, 0) + 1
    for row in rows:
        if not isinstance(row, dict):
            issues.append("Riga valuation malformata nel receipt originale")
            continue
        if row.get("status") != "ready" or row.get("publication_status") != "available":
            skipped.append({**row, "email_included": False})
            continue
        ticker, file = row.get("ticker"), row.get("path")
        digest = row.get("workbook_sha256")
        if (not isinstance(ticker, str) or not ticker or counts.get(ticker) != 1
                or not isinstance(file, str) or file not in attachments
                or not isinstance(digest, str) or hashes.get(file) != digest
                or not row.get("snapshot_id") or not row.get("generation_id")
                or type(row.get("thesis_id")) is not int or row["thesis_id"] < 1):
            issues.append("Receipt incompleto o discordante per " + str(ticker))
            continue
        recovered = {"path": file, "snapshot_id": row["snapshot_id"],
                           "generation_id": row["generation_id"],
                           "workbook_sha256": digest,
                           "valuation_usability": {"usable": True},
                           "_thesis_saved": {"thesis_id": row["thesis_id"]},
                           "model_publication": {"status": "historical_receipt",
                               "reason": "Copia datata del pacchetto originale; versione corrente non riverificata"},
                           "reused": row.get("revision_origin") == "reused"}
        if row.get("method") == "exposure_analysis" or row.get("model_kind") == "exposure":
            try:
                sidecar = json.loads(Path(file).with_suffix(".payload.json").read_text(encoding="utf-8"))
                if (not isinstance(sidecar, dict) or not row.get("analysis_payload_sha256")
                        or _exposure_payload_digest(sidecar) != row["analysis_payload_sha256"]
                        or sidecar.get("valuation_date") != row.get("valuation_date")):
                    raise ValueError("Observational receipt differs from its exact model payload")
                recovered = {**sidecar, **recovered, "method": "exposure_analysis",
                             "valuation_usability": sidecar.get("valuation_usability")}
            except (OSError, ValueError, TypeError) as exc:
                issues.append("Receipt exposure non verificabile per " + ticker + ": " + type(exc).__name__)
                skipped.append({**row, "status": "metadata_mismatch", "artifact_status": "unavailable",
                                "publication_status": "unavailable", "email_included": False,
                                "reason": "Osservazioni originali non piu verificabili"})
                continue
        results[ticker] = recovered
    attempts = source.get("attempts")
    if not isinstance(attempts, list):
        issues.append("Tentativi originali non leggibili")
        attempts = []
    result = build_manifest(results, roots=roots, attempts=attempts)
    result["valuations"].extend(skipped)
    result.update(memo_id=memo_id, source_manifest=str(path), issues=issues)
    return result


def record_email_outcome(receipt, sent):
    """An assembled MIME is not evidence that SMTP delivered the message."""
    receipt["email_status"] = ("sent" if sent is True else
                               "uncertain" if receipt.get("email_status") == "uncertain" else
                               "package_failed" if receipt.get("email_status") == "package_failed"
                               else "not_sent")
    included = (set(receipt.get("mime_attachments") or []) & set(receipt.get("attachments") or [])
                if sent is True else set())
    for row in receipt.get("valuations", []):
        row["email_included"] = row.get("status") == "ready" and row.get("path") in included
    return receipt


def save_manifest(path, manifest):
    """A failed replacement leaves the previous receipt intact and raises to the caller."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
