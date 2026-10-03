"""Common preparation -> existing engine workflow for committee and queued jobs."""
from copy import deepcopy


def merge_research_sources(report, snapshot):
    """Retain admitted raw documents even when automatic extraction is partial.

    The run service has verified these documents; economic qualification remains
    the compiler's responsibility. No readiness flag or diagnostic is cleared.
    """
    result = deepcopy(report)
    documents = {}
    for doc in result.get('documents', []):
        if doc['id'] in documents:
            raise ValueError('Duplicate acquired document ID: ' + doc['id'])
        documents[doc['id']] = deepcopy(doc)
    for doc in snapshot.get('documents', []):
        previous = documents.get(doc['id'])
        if previous is not None and any(previous.get(key) != doc.get(key) for key in
                ('sha256', 'document_sha256', 'url', 'published_at')):
            raise ValueError('Research document conflicts with acquired document: ' + doc['id'])
        if previous is None:
            documents[doc['id']] = deepcopy(doc)
    result['documents'] = list(documents.values())
    result['research_revision'] = {key: deepcopy(snapshot[key]) for key in (
        'run_id', 'ticker', 'as_of', 'revision_id', 'revision_sha256',
        'parent_grant_fingerprint', 'document_receipts') if key in snapshot}
    if documents and result.get('status') == 'incomplete':
        result['status'] = 'partial'
    return result


def prepare_and_generate(bundle, *, documents, propose, output_dir=None, source_report=None,
                         prior_preparation=None, reuse_prepared=None, preparation_context=None):
    from .input_preparation import prepare_method_inputs
    from .dcf_engine import generate_valuation, _write_payload_sidecar
    if prior_preparation is not None:
        from .preparation_basis import BasisProposer, retain_prior_documents
        retained = retain_prior_documents({**deepcopy(source_report or {}), 'documents': documents}, prior_preparation)
        documents = retained['documents']
        if source_report is not None or 'retained_review_evidence' in retained:
            source_report = retained
        propose = BasisProposer(propose, prior_preparation)
    review_basis = {}
    def capture(dossier, contract):
        if preparation_context is not None:
            if not isinstance(preparation_context, dict):
                raise ValueError('Preparation context must be an explicit object')
            # The same context is shown to every staged call and retained in
            # the exact review basis. Source documents remain unmodified.
            dossier['preparation_context'] = deepcopy(preparation_context)
        review_basis.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
        return propose(dossier, contract)
    prepared = prepare_method_inputs(bundle, documents=documents, propose=capture, source_report=source_report)
    selection = getattr(propose, 'preparation_selection', None)
    if selection is not None:
        prepared['provenance']['preparation_selection'] = deepcopy(selection)
    if prepared['status'] == 'prepared' and review_basis:
        from .preparation_seed import make_seed, _digest
        review_basis['seed'] = make_seed(review_basis['dossier'], review_basis['contract'], prepared['proposal']['plan'])
        review_basis['version'] = 1
        lineage = getattr(propose, 'refresh_lineage', None)
        if lineage is not None:
            if lineage.get('plan_sha256') != _digest(prepared['proposal']['plan']):
                raise ValueError('refresh lineage differs from compiled plan')
            prepared['provenance']['refresh_review'] = deepcopy(lineage)
    candidate = prepared["bundle"]
    # Supply exact author/source provenance before the workbook is rendered.
    # Reopening an already calculated XLSX would discard formula caches.
    receipt = {key: deepcopy(prepared[key]) for key in ("status", "issues", "proposal", "provenance")}
    if prepared['status'] == 'prepared' and review_basis:
        import json
        from .preparation_ai import _json
        receipt['review_basis'] = json.loads(_json(review_basis))
    if (prepared['status'] == 'prepared' and not prepared['issues'] and reuse_prepared is not None
            and (selection or {}).get('mode') == 'unchanged_context'
            and candidate['snapshot_id'] == prior_preparation.get('source_snapshot_id')):
        reused = reuse_prepared(candidate)
        if reused is not None:
            return reused  # Keep the verified generation and its original preparation receipt.
    if prepared["issues"]:
        from uuid import uuid4
        from .dcf_quality import normalize_valuation_payload
        result = normalize_valuation_payload({"ticker": candidate["case"]["ticker"], "ok": False,
            "snapshot_id": candidate["snapshot_id"], "generation_id": str(uuid4()),
            "valuation_decision": deepcopy(candidate["decision"]),
            "acquisition_snapshot": candidate, "acquisition_tasks": deepcopy(candidate["acquisition_tasks"]),
            "input_consumption": {"status": "incomplete", "consumed_fields": [], "consumed_records": []},
            "error": "Preparazione incompleta: " + "; ".join(issue["reason"] for issue in prepared["issues"]),
            "exclude_from_action_table": True}, expected_decision=candidate["decision"],
            as_of=candidate["case"]["as_of"])
    else:
        result = generate_valuation(candidate["case"]["ticker"], prepared_bundle=candidate,
                                    output_dir=output_dir, preparation_evidence=receipt)
    # The exact plan is part of the recorded generation, never an approval.
    result["preparation"] = receipt
    result["acquisition_tasks"].extend(deepcopy(prepared["issues"]))
    if result.get("path"):
        result = _write_payload_sidecar(result)
    return result


def collect_and_prepare(bundle, *, archive_root, propose, filing_results=(), output_dir=None,
                        catalog=None, download=None, source_report=None, prior_preparation=None,
                        reuse_prepared=None, research_sources=None):
    """Explicit preparation entry point; callers own paid-work authorization."""
    from .input_preparation import has_approved_inputs
    from .sector_analysis import validate_bundle
    bundle = validate_bundle(bundle, bundle["case"]["ticker"])
    if has_approved_inputs(bundle):
        return prepare_and_generate(bundle, documents=[], propose=propose, output_dir=output_dir)
    from .preparation_sources import collect_preparation_evidence
    if research_sources is not None:
        if (research_sources.get('ticker') != bundle['case']['ticker']
                or research_sources.get('as_of') != bundle['case']['as_of']):
            raise ValueError('Run research sources differ from model ticker or cutoff')
        filing_results = [*deepcopy(filing_results), *deepcopy(research_sources.get('filing_results', []))]
    report = deepcopy(source_report) if source_report is not None else collect_preparation_evidence(
        bundle["case"]["ticker"], as_of=bundle["case"]["as_of"], archive_root=archive_root,
        filing_results=filing_results, catalog=catalog, download=download,
        financial_currency=(bundle['case'].get('info') or {}).get('financialCurrency'),
        quote_identity=deepcopy(bundle['case'].get('info') or {}),
        method_id=bundle['decision'].get('method_id'))
    if research_sources is not None:
        report = merge_research_sources(report, research_sources)
    return prepare_and_generate(bundle, documents=report["documents"], propose=propose,
                                source_report=report, output_dir=output_dir, prior_preparation=prior_preparation,
                                reuse_prepared=reuse_prepared)
