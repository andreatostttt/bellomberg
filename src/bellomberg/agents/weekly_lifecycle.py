"""Checkpoint and delivery adapters used by the normal Consigliere entry point."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from bellomberg.storage.weekly_run_store import (
    CONTRACT_VERSION, WeeklyRunBlocked, WeeklyRunStore, digest, current_book_identity, costs_unresolved)
from bellomberg.core.research_analysis import (
    RESEARCH_ANALYSIS_MODE, is_research_mode, research_reference, research_digest)


def book_identity(db):
    """Read holdings and cash identity without fetching prices, FX or provider data."""
    return current_book_identity(db)


def require_existing_database(path):
    """Do not let a wrong configured path silently bootstrap an empty paid run."""
    import sqlite3
    from contextlib import closing
    target = Path(path).resolve()
    if not target.is_file():
        raise WeeklyRunBlocked("DB configurato assente: run bloccata prima di inizializzazione e AI")
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as conn:
            for table in ("positions", "cash_state", "memos"):
                conn.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchall()
    except sqlite3.Error as exc:
        raise WeeklyRunBlocked("DB configurato illeggibile o schema non riconosciuto: " + type(exc).__name__) from exc


def create_run(db, portfolio, mandate, contract, language):
    if (not isinstance(portfolio, dict) or portfolio.get("error") or
            not isinstance(portfolio.get("positions"), list) or
            isinstance(portfolio.get("n_positions"), bool) or
            portfolio.get("n_positions") != len(portfolio["positions"])):
        raise WeeklyRunBlocked("Fonte portfolio non verificata: nessuna richiesta AI consentita")
    memo_id = db.save_memo("[IN PROGRESS]", portfolio_nav_eur=portfolio.get("totale_valore_mercato_eur"),
                           title="Bellomberg Weekly - " + datetime.now().strftime("%d/%m/%Y"),
                           output_language=language)
    if not memo_id:
        raise WeeklyRunBlocked("Persistenza memo non confermata: nessuna richiesta AI consentita")
    context = {"contract_version": CONTRACT_VERSION, "portfolio": portfolio,
               "book_identity": book_identity(db), "mandate_sha256": digest(mandate),
               "contract": contract, "language": language,
               "research_started_at": datetime.now(timezone.utc).isoformat()}
    return WeeklyRunStore(db, memo_id, context=context)


def validate_resume(store, mandate, contract):
    if digest(mandate) != store.context["mandate_sha256"]:
        raise WeeklyRunBlocked("Mandato cambiato: analisi storica conservata, nuova autorizzazione necessaria")
    if contract != store.context["contract"]:
        raise WeeklyRunBlocked("Configurazione o contratto cambiato: checkpoint non riutilizzabile")
    if book_identity(store.db) != store.context["book_identity"]:
        raise WeeklyRunBlocked("Book cambiato: nessun mix tra report storici e portafoglio attuale; recupera solo la consegna")


def bind_blackboard(bb, store):
    bb.run_id = store.run_id
    bb.weekly_store = store
    store.blackboard = bb
    bb.specialist_checkpoints = {}
    store.restore(bb)
    mode = store.context['contract'].get('analysis_mode')
    if mode not in (None, RESEARCH_ANALYSIS_MODE):
        raise WeeklyRunBlocked('Unknown weekly analysis mode: archived context was preserved')
    bb.analysis_mode = mode
    if is_research_mode(bb):
        bb.research_tickers = sorted({row['ticker'] for row in store.context['portfolio']['positions']
                                      if isinstance(row, dict) and row.get('ticker')})
        validate_research_artifact_scope(bb, store.get('artifact_bundle'))
    bb.data["_research_context"] = {"research_started_at": store.context["research_started_at"],
                                     "portfolio": "original_run_snapshot",
                                     "operational_revalidation": "required_before_execution"}
    bb._weekly_error = None

    def persist(event, payload):
        store.save_snapshot(bb)

    def blocked():
        if bb._weekly_error is not None:
            raise bb._weekly_error

    def fail(error, *, desk=None, round_n=None, **kwargs):
        if bb._weekly_error is None:
            bb._weekly_error = error
        store.fail(error, desk=desk, round_n=round_n)
        store.save_snapshot(bb)

    def should_run(name, round_n):
        saved = store.get("desk:" + name + ":" + str(round_n))
        if saved is None:
            return True
        report = bb.read(name, round_n)
        if not isinstance(report, str) or sha256(report.encode("utf-8")).hexdigest() != saved["report_sha256"]:
            raise WeeklyRunBlocked("Report completo modificato: " + name + " R" + str(round_n))
        if round_n == 2 and is_research_mode(bb):
            validate_research_reviews(bb, [name])
        return False

    def complete(name, round_n, report, status):
        from bellomberg.agents.capo import _e_segnaposto
        report = report if isinstance(report, str) else bb.read(name, round_n)
        if status != "complete" or not isinstance(report, str) or not report.strip() or _e_segnaposto(report):
            raise WeeklyRunBlocked("Report incompleto " + name + " R" + str(round_n) + ": " + str(status))
        store.complete("desk:" + name + ":" + str(round_n),
                       {"report_sha256": sha256(report.encode("utf-8")).hexdigest(), "status": status}, bb)

    bb.persist_run_checkpoint = persist
    bb.raise_if_run_blocked = blocked
    bb.record_run_failure = fail
    bb.should_run_specialist = should_run
    bb.record_specialist_completion = complete


def validate_research_reviews(bb, desks):
    reference = research_reference(bb)
    for name in desks:
        saved = bb.data.get('_desk_research_reviews', {}).get(name)
        report = bb.read(name, 2)
        if (not isinstance(saved, dict) or saved.get('round') != 2
                or saved.get('research_ref') != reference or not isinstance(report, str)
                or saved.get('report_sha256') != research_digest(report)):
            raise WeeklyRunBlocked('Final desk review is not bound to the sealed research: ' + name)
    return reference


def build_weekly_delivery(bb, *, roots):
    if is_research_mode(bb):
        validate_research_artifact_scope(bb)
        return {'schema_version': 1, 'analysis_mode': RESEARCH_ANALYSIS_MODE,
            'completion_contract': 'research-memo-pdf/1', 'research_ref': research_reference(bb),
            'workbook_status': 'not_required', 'valuations': [], 'attempts': [],
            'attachments': [], 'expected_hashes': {}, 'email_status': 'not_attempted'}
    from bellomberg.reporting.valuation_delivery import build_manifest
    return build_manifest(bb.valuation_results, roots=roots, attempts=bb.valuation_attempts)


def validate_research_artifact_scope(bb, bundle=None):
    if not is_research_mode(bb):
        return
    if bb.valuation_results or bb.valuation_attempts or bb.valuation_generations:
        raise WeeklyRunBlocked('Research run unexpectedly contains workbook activity; preserve and review it')
    for artifact in (bundle or {}).get('artifacts', []):
        allowed = {'Markdown': '.md', 'PDF': '.pdf'}
        kind = artifact.get('kind')
        if kind not in allowed or Path(artifact.get('path', '')).suffix.lower() != allowed[kind]:
            raise WeeklyRunBlocked('Research archive contains unrequested workbook activity; restore blocked')


def weekly_email_body(bb, delivery):
    if is_research_mode(bb):
        reference = research_reference(bb)
        if delivery.get('research_ref') != reference:
            raise WeeklyRunBlocked('Delivery refers to a different research dossier')
        if getattr(bb, 'language', 'it') == 'en':
            return ('<p>Company research with explicit assumptions, source references and data gaps. '
                'Analyst consensus remains distinct from AI judgement; no AI fair value is required. '
                'The package contains the memo/PDF. Excel is not part of this analysis mode.</p>')
        return ('<p>Analisi societaria con assumption esplicite, fonti e dati mancanti dichiarati. '
            'Il consensus analisti resta distinto dal giudizio AI; nessun fair value AI obbligatorio. '
            'Il pacchetto contiene memo/PDF. Excel non previsto in questa modalita di analisi.</p>')
    from bellomberg.reporting.email_sender import corpo_valutazioni
    return corpo_valutazioni(bb.valuation_results, delivery['attachments'], delivery=delivery)


def validate_memo(memo, usage):
    from bellomberg.agents.capo import _e_segnaposto
    if (not isinstance(memo, str) or not memo.strip() or _e_segnaposto(memo)
            or not isinstance(usage, dict) or usage.get("error") or usage.get("complete") is not True
            or any(marker in memo for marker in ("[CAPO ERROR]", "[CAPO ERROR]:", "[MEMO COLLASSATO", "[CAPO] No output"))
            or usage.get("stop_reason") != "end_turn"):
        raise WeeklyRunBlocked("Capo non completo: " + str((usage or {}).get("error") or
                                                          (usage or {}).get("stop_reason") or "memo non valido"))


def file_receipt(path, *, kind):
    if not path or not Path(path).is_file():
        raise WeeklyRunBlocked(kind + " richiesto assente")
    content = Path(path).read_bytes()
    if not content or (kind == "PDF" and not content.startswith(b"%PDF-")):
        raise WeeklyRunBlocked(kind + " non valido")
    return {"path": str(Path(path).resolve()), "sha256": sha256(content).hexdigest(), "kind": kind}


def verify_receipts(receipts):
    for receipt in receipts:
        path = Path(receipt["path"])
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != receipt["sha256"]:
            raise WeeklyRunBlocked("Artefatto mancante o modificato: " + path.name)


def write_frozen_text(path, text):
    from bellomberg.reporting.exact_artifacts import _create_exact
    target = Path(path)
    content = text.encode("utf-8")
    expected = sha256(content).hexdigest()
    if target.is_file():
        if sha256(target.read_bytes()).hexdigest() != expected:
            raise WeeklyRunBlocked("Originale modificato: conservato senza sovrascrittura " + target.name)
        return
    try:
        _create_exact(target, content, expected)
    except (ValueError, OSError) as exc:
        raise WeeklyRunBlocked("Originale modificato o artefatto non persistito: " + str(exc)) from exc


def validate_delivery_manifest(delivery):
    if is_research_mode(delivery):
        if (delivery.get('completion_contract') != 'research-memo-pdf/1'
                or delivery.get('workbook_status') != 'not_required'
                or not isinstance(delivery.get('research_ref'), dict)
                or delivery.get('valuations') or delivery.get('attempts')
                or delivery.get('attachments') or delivery.get('expected_hashes')):
            raise WeeklyRunBlocked('Research delivery contract contains unrequested workbook activity')
        return
    missing = [row for row in delivery.get("valuations", []) if row.get("status") != "ready"]
    if missing:
        raise WeeklyRunBlocked("Workbook richiesto non consegnabile: " + "; ".join(
            str(row.get("ticker")) + ": " + str(row.get("reason", "n.d.")) for row in missing))


def preserve_delivery_bundle(store, bb, *, md_path, pdf_path, appendix_path, delivery, allowed_roots):
    """Archive exact reviewed bytes before delivery; never compile a replacement model."""
    from bellomberg.reporting.exact_artifacts import preserve_exact_artifacts
    receipts = [dict(file_receipt(md_path, kind="Markdown"), role="memo"),
                dict(file_receipt(pdf_path, kind="PDF"), role="memo")]
    if receipts[0]["sha256"] != sha256(store.get("memo_validated")["memo"].encode("utf-8")).hexdigest():
        raise WeeklyRunBlocked("Markdown diverso dal memo validato: originale conservato")
    if appendix_path:
        receipts.append(dict(file_receipt(appendix_path, kind="PDF"), role="appendix"))
    validate_delivery_manifest(delivery)
    for path, expected in delivery.get("expected_hashes", {}).items():
        item = file_receipt(path, kind="Excel")
        if item["sha256"] != expected:
            raise WeeklyRunBlocked("Workbook modificato: copia originale conservata")
        receipts.extend([item, file_receipt(Path(path).with_suffix('.payload.json'), kind="Excel metadata")])
    vault = Path(store.db.db_path).with_name("weekly-" + store.run_id + "-artifacts")
    saved = preserve_exact_artifacts(receipts, vault, allowed_roots=[*allowed_roots, vault])
    payload = {"vault": str(vault.resolve()), "artifacts": saved}
    store.complete("artifact_bundle", payload, bb)
    store.update(artifacts=saved, artifact_status="available")
    return payload


def restore_delivery_bundle(store, module):
    from bellomberg.reporting.exact_artifacts import restore_exact_artifacts
    bundle = store.get("artifact_bundle")
    if bundle is None:
        return None
    vault = Path(store.db.db_path).with_name("weekly-" + store.run_id + "-artifacts").resolve()
    if bundle.get("vault") != str(vault):
        raise WeeklyRunBlocked("Archivio artefatti non corrisponde alla run")
    try:
        result = restore_exact_artifacts(bundle["artifacts"], vault, allowed_roots=[
            module.MODELS_DIR, module.REPORT_DIR, module.RESEARCH_NOTES_DIR, vault])
    except (ValueError, OSError) as exc:
        raise WeeklyRunBlocked("Originale modificato o copia esatta assente: " + str(exc)) from exc
    store.update(artifact_recovery=result)
    return bundle


def render_pdf_once(store, module, builder, *, role="memo", **inputs):
    """Render privately, then publish one run-specific PDF without overwriting."""
    from tempfile import NamedTemporaryFile
    from bellomberg.reporting.exact_artifacts import (
        _create_exact, preserve_exact_artifacts, restore_exact_artifacts)
    root = Path(module.REPORT_DIR).resolve()
    destination = root / ("weekly_" + str(store.memo_id) + "_" + store.run_id + "_" + role + ".pdf")
    vault = Path(store.db.db_path).with_name("weekly-" + store.run_id + "-artifacts").resolve()
    key = "rendered_pdf:" + role
    saved = store.get(key)
    if saved is not None:
        if saved.get("path") != str(destination):
            raise WeeklyRunBlocked("Percorso PDF attestato diverso dalla run")
        restore_exact_artifacts([saved], vault, allowed_roots=[root, vault])
        return str(destination)
    if destination.exists():
        raise WeeklyRunBlocked("PDF originale non attestato: conservato senza sovrascrittura")
    root.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=root, prefix=".weekly-render-", suffix=".pdf", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        generated = builder(output_path=str(temporary), **inputs)
        if generated is None:
            return None
        if Path(generated).resolve() != temporary:
            raise WeeklyRunBlocked("Renderer non ha rispettato il percorso temporaneo della run")
        receipt = file_receipt(temporary, kind="PDF")
        content = temporary.read_bytes()
        _create_exact(destination, content, receipt["sha256"])
        receipt.update(path=str(destination), role=role)
        preserve_exact_artifacts([receipt], vault, allowed_roots=[root, vault])
        store.complete(key, receipt)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)


def deliver_once(store, module, attachments, *, body_extra, delivery, send_email):
    """Persist intent before SMTP; an ambiguous previous send is never repeated."""
    from bellomberg.reporting.valuation_delivery import record_email_outcome
    prior = store.status().get("email_delivery") or {}
    if prior.get("state") == "sent":
        delivery.update(prior.get("receipt") or {})
        return True
    if prior.get("state") in ("sending", "uncertain"):
        delivery.update(email_status="uncertain", smtp_state=prior.get("state"))
        store.update(delivery_status="uncertain")
        return False
    if not send_email:
        return False
    if store.get("decisions_finalized") is None:
        raise WeeklyRunBlocked("Decisioni non finalizzate: invio della consegna bloccato")
    if costs_unresolved(store.status().get("request_costs") or {}):
        raise WeeklyRunBlocked("Costi o richieste incerti: invio della consegna bloccato")
    bundle = store.get("artifact_bundle")
    if not bundle:
        raise WeeklyRunBlocked("Bundle artefatti attestato assente: invio bloccato")
    hashes = {row["path"]: row["sha256"] for row in bundle["artifacts"]}
    paths = [str(Path(path).resolve()) for path in attachments]
    if len(set(paths)) != len(paths) or any(path not in hashes for path in paths):
        raise WeeklyRunBlocked("Allegato non attestato nel bundle originale")
    expected = {path: hashes[path] for path in paths}
    verify_receipts([{"path": path, "sha256": value} for path, value in expected.items()])
    store.update(delivery_requested=True, delivery_status="sending",
                 email_delivery={"state": "sending", "started_at": datetime.now(timezone.utc).isoformat()})
    delivery["smtp_state"] = "not_started"
    sent = module._send_weekly_email(attachments, body_extra=body_extra, delivery=delivery,
                                    expected_hashes=expected, require_all_hashes=True)
    record_email_outcome(delivery, sent)
    status = "sent" if sent is True else "uncertain" if delivery.get("email_status") == "uncertain" else "not_sent"
    receipt = {key: delivery[key] for key in ("email_status", "smtp_state", "email_error", "mime_attachments")
               if key in delivery}
    store.update(delivery_status=status, email_delivery={"state": status, "receipt": receipt})
    return sent is True


def persist_usage_once(store, bb):
    """The original aggregate rows are replaced atomically by the complete snapshot."""
    with store.db._conn() as conn:
        conn.execute("DELETE FROM llm_usage WHERE memo_id=?", (store.memo_id,))
        for row in bb.usage_log:
            conn.execute("INSERT INTO llm_usage(memo_id,agent,round_n,model,tokens_in,tokens_out,cache_read,"
                         "cache_write,api_calls,duration_s,cost_eur,fx_rate,fx_source,cost_status,cache_ttl,output_language) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (store.memo_id, row.get("agent"), row.get("round"), row.get("model") or "n.d.",
                          row.get("in"), row.get("out"), row.get("cache_read"), row.get("cache_write"),
                          row.get("api_calls"), row.get("duration_s"), row.get("cost_eur"), row.get("fx_rate"),
                          row.get("fx_source"), row.get("status"), row.get("cache_ttl"), bb.language))


def finish_status(store, bb, *, pdf_path, md_path, appendix_path, delivery, delivery_path,
                  persistence_ok, sent):
    receipts = [file_receipt(md_path, kind="Markdown"), file_receipt(pdf_path, kind="PDF")]
    if appendix_path:
        receipts.append(file_receipt(appendix_path, kind="PDF"))
    validate_delivery_manifest(delivery)
    if not persistence_ok:
        raise WeeklyRunBlocked("Persistenza finale non confermata")
    receipts += [{"path": path, "sha256": sha, "kind": "Excel"}
                 for path, sha in delivery.get("expected_hashes", {}).items()]
    bundle = store.get("artifact_bundle")
    if bundle is not None:
        receipts = bundle["artifacts"]
    verify_receipts(receipts)
    journal = getattr(store, "request_journal", None)
    costs = journal.summary() if journal is not None else store.status().get("request_costs")
    uncertain_costs = costs_unresolved(costs or {})
    if costs is not None:
        store.update(request_costs=costs, artifacts=receipts, delivery_manifest=str(delivery_path))
    prior = store.status()
    delivery_state = "sent" if sent else "uncertain" if prior.get("delivery_status") in ("sending", "uncertain") else "pending"
    final_status = "incomplete" if uncertain_costs or prior.get("delivery_requested") and not sent else "completed"
    store.update(status=final_status, analytical_status="complete", artifact_status="available",
                 delivery_status=delivery_state, operational_status="blocked" if uncertain_costs else "requires_pm_review",
                 artifacts=receipts, delivery_manifest=str(delivery_path), phase="done", last_error=None)
    bb.mark_run_complete(technical_status=final_status,
                         reason=None if final_status == "completed" else
                         "Richieste o costi incerti conservati" if uncertain_costs else "Consegna richiesta non confermata")
    return store.status()


def recover_delivery(store, module, *, send_email=False):
    """Only local rendering/persistence/transport. No analytical function is invoked."""
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.reporting.valuation_delivery import save_manifest, record_email_outcome
    saved = store.get("memo_validated")
    if saved is None:
        raise WeeklyRunBlocked("Memo validato assente: serve una ripresa analitica autorizzata")
    memo = saved["memo"]
    validate_memo(memo, saved["capo_usage"])
    bb = Blackboard(memory_db=store.db, memo_id=store.memo_id, run_id=store.run_id)
    bind_blackboard(bb, store)
    restore_delivery_bundle(store, module)
    if is_research_mode(bb):
        from bellomberg.agents.company_research_tools import bind_weekly_company_research
        bind_weekly_company_research(bb, store)
    context = store.context
    render = store.get("render_context") or {}
    base = Path(module.RESEARCH_NOTES_DIR) / ("bellomberg_memo_" + str(store.memo_id))
    base.parent.mkdir(parents=True, exist_ok=True)
    md_path = str(base.with_suffix(".md"))
    write_frozen_text(md_path, memo)
    state = store.status()
    pdf_path = None
    appendix_path = None
    for receipt in state.get("artifacts", []):
        if receipt.get("kind") == "PDF":
            if receipt.get("role") == "appendix":
                verify_receipts([receipt])
                appendix_path = receipt["path"]
                continue
            if Path(receipt["path"]).is_file():
                verify_receipts([receipt])
                pdf_path = receipt["path"]
    if pdf_path is None:
        from bellomberg.reporting.pdf_institutional import build_institutional_memo
        pdf_path = render_pdf_once(store, module, build_institutional_memo,
            memo_markdown=memo, portfolio_data=context["portfolio"],
            risk_data=render.get("risk_data"), nav_history=render.get("nav_history"),
            sizing_data=bb.data.get("_sizing"), scoring_data=bb.data.get("_score_cache"),
            title_date=context["research_started_at"].split("T")[0])
    file_receipt(pdf_path, kind="PDF")
    delivery = build_weekly_delivery(bb, roots=[module.MODELS_DIR, module.REPORT_DIR])
    delivery.update(memo_id=store.memo_id, memo_sha256=sha256(memo.encode("utf-8")).hexdigest())
    path = str(base) + "_valuations.json"
    save_manifest(path, delivery)
    validate_delivery_manifest(delivery)
    preserve_delivery_bundle(store, bb, md_path=md_path, pdf_path=pdf_path,
        appendix_path=appendix_path, delivery=delivery,
        allowed_roots=[module.MODELS_DIR, module.REPORT_DIR, module.RESEARCH_NOTES_DIR])
    with store.db._conn() as conn:
        conn.execute("UPDATE memos SET full_markdown=?,pdf_path=?,dcf_files=? WHERE id=?",
                     (memo, str(pdf_path), json.dumps(delivery["attachments"]), store.memo_id))
    persist_usage_once(store, bb)
    # File recovery is allowed before missing decision/cost reconciliation;
    # SMTP is not allowed to bypass the normal analytical persistence gates.
    if store.get("decisions_finalized") is None:
        store.update(status="incomplete", analytical_status="complete", artifact_status="available",
                     delivery_status=state.get("delivery_status", "pending"), operational_status="blocked",
                     artifacts=store.get("artifact_bundle")["artifacts"],
                     delivery_manifest=path, phase="decisions_pending")
        return store.status()
    sent = deliver_once(store, module, [str(pdf_path), *([appendix_path] if appendix_path else []), *delivery["attachments"]],
        body_extra=weekly_email_body(bb, delivery),
        delivery=delivery, send_email=send_email)
    record_email_outcome(delivery, sent)
    save_manifest(path, delivery)
    return finish_status(store, bb, pdf_path=pdf_path, md_path=md_path, appendix_path=appendix_path,
                         delivery=delivery, delivery_path=path, persistence_ok=True, sent=sent)
