"""Free historical proof assembly from fresh acquisitions and common archives.

Previous forecasts, PM prose, approvals, grant journals and portfolio records are
not reused. Only observed drivers and the declared immutable opening structure
can survive, after recompilation against the current source catalog. A new
balance date or changed source bytes requires a new economic bridge.
"""
from copy import deepcopy
import json
from pathlib import Path
import re


STRUCTURE = frozenset({'perimeter', 'calendar', 'legal_structure', 'forward_year'})


def archived_common_candidates(ticker, method_id, opening_date, *, archive_root):
    """Read exact common sidecars in product directories, without DB/network I/O."""
    from bellomberg.core.paths import REPORT_DIR
    roots = tuple(dict.fromkeys((Path(archive_root).resolve(), Path(REPORT_DIR).resolve())))
    safe_tickers = {re.sub(pattern, '_', ticker) for pattern in (r'[^A-Za-z0-9_-]', r'[^A-Za-z0-9]')}
    paths = set()
    for root in roots:
        if root.is_dir():
            for safe_ticker in safe_tickers:
                paths.update(root.rglob('*' + safe_ticker + '*.payload.json'))
    candidates = []
    for path in sorted(paths):
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                continue
            payload = json.loads(path.read_text(encoding='utf-8'))
            if (payload.get('ticker') == ticker and payload.get('method') == method_id
                    and (opening_date is None or payload.get('valuation_date') == opening_date)):
                candidates.append(payload)
        except (OSError, ValueError, TypeError):
            continue
    return candidates


def collect_trade_idea_sources(ticker, *, as_of, archive_root, financial_currency=None,
                              method_id=None, seed_loader=None, raw_documents=(), issuer_name=None, **collector_options):
    """Use the free common collector, reconfirming archived financial identities.

Foreign 6-K notices cannot be called financial statements from a SEC catalog
entry alone. A prior common preparation may supply its already proved metadata
only when a new public download has the exact original raw SHA. The ordinary
live SEC reconciliation and statement normalizers still run afterwards.
"""
    from .preparation_sources import collect_preparation_evidence
    from .preparation_seed import _digest
    from .document_evidence import valid_source_url
    from urllib.parse import urlsplit
    if issuer_name is not None and '.' in ticker and collector_options.get('catalog') is None:
        from bellomberg.market_data.sec_edgar import get_filing_catalog
        # The qualifier supplies this only after reconciling the confirmed
        # issuer with the current public profile. No foreign-symbol stripping.
        collector_options['catalog'] = lambda symbol: get_filing_catalog(symbol, issuer_name=issuer_name)
    supplied = list(collector_options.pop('filing_results', ()))
    if method_id == 'exposure_analysis' and raw_documents:
        # Server-owned acquisition envelopes retain real availability receipts.
        # No publication date, source plan or normalized fact is fabricated.
        from .input_preparation import _catalog, _day
        catalog, issues, _ = _catalog(list(raw_documents), _day(as_of))
        return {'status': 'partial' if issues else 'ready', 'documents': deepcopy(list(raw_documents)),
            'issues': issues, 'preparation_ready': False, 'selection': {},
            'trade_idea_document_catalog': {'contract': 'trade_idea_primary_catalog/1',
                'purpose': 'commodity_trust_raw_historical_reproof', 'economic_qualification': 'not_assessed',
                'limitation': 'Server-acquired raw catalog only; the method assembler must identify unique linked primary roles and recompile their complete economic proof.'}}
    if method_id in ('property_nav', 'exposure_analysis'):
        from .property_reported_sources import SOURCE_URL
        requested = any(isinstance(result, dict) and result.get('ticker') == ticker and
            isinstance(result.get('candidati'), list) and
            any(isinstance(candidate, dict) and (method_id == 'exposure_analysis' or candidate.get('url') == SOURCE_URL) and
                candidate.get('stato') in ('verificato', 'duplicato')
                for candidate in result['candidati'])
            for result in supplied)
        if requested:
            # This source family has its own closed-ledger proof. Keep the
            # common byte-verified primary and archive path for recompilation;
            # SEC annual revenue and a separate Yahoo historical close do not
            # establish a reported NAV snapshot. Acquisition grants no readiness.
            from .valuation_sources import collect_documents
            report = collect_documents(ticker, as_of=as_of, archive_root=archive_root,
                filing_results=supplied, catalog=collector_options.get('catalog'),
                download=collector_options.get('download'), max_documents=8 if method_id == 'exposure_analysis' else 4,
                selection_policy='latest' if method_id == 'exposure_analysis' else 'opening_annual_comparative')
            report.update(preparation_ready=False, selection={},
                acquired_document_index=[{key: deepcopy(doc[key]) for key in
                    ('id', 'url', 'published_at', 'sha256', 'document_sha256', 'metadata') if key in doc}
                    for doc in report['documents']],
                trade_idea_document_catalog={'contract': 'trade_idea_primary_catalog/1',
                    'purpose': 'commodity_trust_raw_historical_reproof' if method_id == 'exposure_analysis' else 'reported_property_historical_reproof',
                    'economic_qualification': 'not_assessed', 'archived_model_reads': 0,
                    'limitation': 'Verified raw documents only. Exact edition, legal identity, historical contracts and accounting reconciliations must pass the separate method-specific assembler.'})
            return report
    loader = seed_loader or archived_common_candidates
    candidates = loader(ticker, method_id, None, archive_root=archive_root)
    sources = {}
    for payload in candidates:
        prep = payload.get('preparation') or {}
        basis = prep.get('review_basis') or {}
        plan = prep.get('proposal', {}).get('plan')
        if (payload.get('ticker') != ticker or payload.get('method') != method_id
                or prep.get('status') != 'prepared' or not isinstance(plan, dict)
                or basis.get('seed', {}).get('plan_sha256') != _digest(plan)
                or payload.get('acquisition_snapshot', {}).get('case', {}).get('as_of', '') > as_of):
            continue
        for document in basis.get('dossier', {}).get('documents') or []:
            metadata = document.get('metadata') or {}
            url = document.get('url')
            if (not valid_source_url(url) or urlsplit(url).hostname != 'www.sec.gov'
                    or metadata.get('form') not in ('10-K', '10-Q', '20-F', '6-K')
                    or not metadata.get('report_date') or not document.get('published_at')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(document.get('document_sha256')))
                    or metadata.get('form') == '6-K' and metadata.get('tipo') not in
                       ('annuale', 'semestrale', 'trimestrale', 'nove_mesi')):
                continue
            key = (url, document['document_sha256'])
            sources[key] = document
    ordered = sorted(sources.values(), key=lambda row: ((row.get('metadata') or {})['report_date'], row['published_at']), reverse=True)
    selected, failures = [], []
    download = collector_options.get('download')
    if download is None:
        from bellomberg.market_data.lettore_trimestrali import scarica_documento
        def download(url, dest_dir):
            return scarica_documento(url, dest_dir, host_consentiti={'www.sec.gov'}, public_only=True)
    from hashlib import sha256
    destination = Path(archive_root).resolve() / 'verified_historical_filings'
    for document in ordered[:4]:
        try:
            fetched = download(document['url'], str(destination))
            path = Path(fetched['path']).resolve()
            if (fetched.get('stato') != 'ok' or not path.is_relative_to(Path(archive_root).resolve())
                    or fetched.get('sha256') != document['document_sha256']
                    or sha256(path.read_bytes()).hexdigest() != document['document_sha256']):
                raise ValueError('Current primary download differs from archived verified bytes')
            keys = ('emittente_id', 'issuer', 'form', 'report_date', 'accession', 'tipo',
                    'periodo_fine', 'periodo_inizio', 'perimetro', 'lingua')
            selected.append({'stato': 'verificato', 'url': document['url'], 'path': str(path),
                'sha256': document['document_sha256'], 'filed_date': document['published_at'],
                'metadati': {key: deepcopy(document['metadata'][key]) for key in keys if key in document['metadata']}})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            failures.append({'source': document['url'], 'reason': str(exc)})
    if selected:
        supplied.append({'ticker': ticker, 'candidati': selected, 'motivi': []})
    report = collect_preparation_evidence(ticker, as_of=as_of, archive_root=archive_root,
        financial_currency=financial_currency, method_id=method_id, filing_results=supplied, **collector_options)
    report['archived_financial_identity_reconfirmation'] = {
        'status': 'ready' if selected and not failures else 'partial' if selected else 'unavailable',
        'primary_documents_reverified': [row['sha256'] for row in selected], 'issues': failures,
        'scope': 'Exact raw financial filings re-downloaded; live catalog and ordinary source normalizers still required. No old forecast or approval reused.'}
    return report


def _pin(document):
    from .trade_idea_model import _semantic
    # Current acquisition dates/receipts remain those of the collector. Source
    # text, legal/date/scope metadata and normalizer packets must still agree.
    pinned = {key: document.get(key) for key in
        ('id', 'url', 'text', 'sha256', 'document_sha256', 'published_at', 'metadata', 'page_references',
         'filing_verification', 'pdf_form_fields', 'inline_parent_fields', 'statement_table_fields',
         'balance_detail_fields', 'balance_sheet_fields')}
    metadata = deepcopy(document.get('metadata') or {})
    if metadata.get('normalizer'):
        # Normalizers record the information cutoff at which unchanged dated
        # observations were recompiled. The actual period, scope, primary pins,
        # disclosure text and values remain part of the comparison.
        metadata.pop('as_of', None)
        metadata.pop('reference_cutoff', None)
    # This receipt repeats identity fields already checked by the live SEC
    # reconciliation. A new equivalent acquisition may not need to add fields.
    metadata.pop('catalog_reconciliation', None)
    pinned['metadata'] = metadata
    return _semantic(pinned)


def _mentioned_ids(value, known):
    if isinstance(value, dict):
        result = set()
        for key, child in value.items():
            if key == 'evidence_ids' and isinstance(child, list):
                result.update(child)
            else:
                result.update(_mentioned_ids(child, known))
        return result
    if isinstance(value, list):
        return set().union(*(_mentioned_ids(child, known) for child in value)) if value else set()
    return {value} if isinstance(value, str) and value in known else set()


def _historical_plan(plan, cutoff):
    """Strip all forecast values; keep key-only hints for dynamic family schemas."""
    result = {'model': {}, 'scenarios': {scenario: {} for scenario in plan.get('scenarios', {})}}
    renewed, structures = [], []
    for scope, drivers in [('model', plan['model'])] + list(plan.get('scenarios', {}).items()):
        target = result['model'] if scope == 'model' else result['scenarios'][scope]
        for driver, item in drivers.items():
            if not isinstance(item, dict):
                continue
            historical = item.get('kind') == 'historical'
            if historical or scope == 'model' and driver in STRUCTURE:
                retained = {key: deepcopy(value) for key, value in item.items() if key != 'rationale'}
                retained['rationale'] = ('Observed source proof recompiled for the current acquisition.' if historical else
                    'Unchanged declared opening structure; historical evidence and model date rechecked against the current acquisition.')
                retained.update(valid_until=cutoff, valid_until_basis={'policy': 'same_day', 'as_of': cutoff})
                target[driver] = retained
                (renewed if historical else structures).append(scope + '.' + driver)
            elif driver.startswith(('segments.', 'adjustments_after_tax.')):
                # Only these names describe dynamic managed-care schema axes.
                # Empty envelopes cannot become valid forecasts or paid seeds.
                target[driver] = {}
    if result['scenarios']:
        result['scenario_rationale'] = {scenario: 'Historical source qualification only; prospective judgement is required from the run preparer.'
                                        for scenario in result['scenarios']}
    else:
        result['analysis_rationale'] = 'Free qualification of dated observed exposure contracts; corporate fair value does not apply.'
    return result, sorted(renewed), sorted(structures)


def assemble_historical_sources(bundle, report, *, archive_root, seed_loader=None):
    """Attach a historical-only plan, or retain an explicit pre-paid source gap."""
    from .input_preparation import _catalog, _day, prepare_method_inputs
    from .sector_analysis import validate_bundle
    from .preparation_methods import method_schema
    from .trade_idea_model import candidate_model_usability
    result = deepcopy(report)
    assembly = {'status': 'blocked', 'reasons': [], 'forecast_values_reused': False,
                'human_approval_reused': False, 'contract': 'free_historical_sources/1'}
    result['historical_source_assembly'] = assembly
    try:
        ticker = bundle['case']['ticker']
        method = bundle['decision']['method_id']
        cutoff = _day(bundle['case']['as_of'])
        from .preparation_fresh_historical import assemble_fresh_historical
        result = assemble_fresh_historical(bundle, result, method_id=method, as_of=bundle['case']['as_of'])
        if method == 'property_nav':
            from .preparation_reported_property import assemble_reported_property
            result = assemble_reported_property(bundle, result, as_of=bundle['case']['as_of'])
        if method == 'exposure_analysis':
            from .trade_idea_commodity_exposure import assemble_sources
            result = assemble_sources(bundle, result, as_of=bundle['case']['as_of'])
        assembly = result['historical_source_assembly']
        if result.get('fresh_historical_assembly', {}).get('fatal_identity_mismatch') is True:
            assembly['reasons'].extend(result['fresh_historical_assembly'].get('reasons') or [])
            return result
        if isinstance(result.get('source_plan'), dict) and result.get('fresh_historical_assembly', {}).get('status') == 'ready':
            assembly.update(status='ready', source_origin='current_free_catalog_without_previous_model',
                historical_drivers=result['fresh_historical_assembly']['historical_drivers'],
                economic_opening_date=result['fresh_historical_assembly']['economic_opening_date'],
                information_cutoff=bundle['case']['as_of'],
                limitation=result['fresh_historical_assembly']['limitation'])
            return result
        opening = result.get('selection', {}).get('opening_date')
        if _day(opening) is None:
            raise ValueError('Current collector has no uniquely verified economic opening date')
        if result.get('preparation_ready') is not True:
            raise ValueError('Current free acquisition is incomplete; a previous model cannot repair its source gaps')
        catalog, issues, _ = _catalog(result.get('documents') or [], cutoff)
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        loader = seed_loader or archived_common_candidates
        candidates = loader(ticker, method, opening, archive_root=archive_root)
        if not isinstance(candidates, (list, tuple)):
            raise ValueError('Historical seed loader must return common payloads, not an approval or browser plan')
        candidates = sorted(candidates, key=lambda candidate: (
            candidate.get('acquisition_snapshot', {}).get('case', {}).get('as_of', ''),
            candidate.get('generation_id', '')), reverse=True)
        candidate_failures = []
        for payload in candidates:
            try:
                prior = validate_bundle(payload.get('acquisition_snapshot'), ticker)
                preparation = payload.get('preparation') or {}
                plan = preparation.get('proposal', {}).get('plan')
                basis = preparation.get('review_basis') or {}
                if (not candidate_model_usability(payload)['usable'] or payload.get('method') != method
                        or payload.get('valuation_date') != opening or prior['case']['as_of'] > bundle['case']['as_of']
                        or preparation.get('status') != 'prepared' or not isinstance(plan, dict)
                        or not isinstance(basis.get('dossier', {}).get('documents'), list)):
                    raise ValueError('Prior common model has no matching complete sourced opening preparation')
                from .preparation_seed import _digest
                if (basis.get('seed', {}).get('plan_sha256') != _digest(plan)
                        or basis['dossier'].get('ticker') != ticker or basis['dossier'].get('method_id') != method):
                    raise ValueError('Prior sourced plan identity differs from its recorded review basis')
                old_catalog, old_issues, _ = _catalog(basis['dossier']['documents'], _day(prior['case']['as_of']))
                if old_issues:
                    raise ValueError('; '.join(row['reason'] for row in old_issues))
                sourced, renewed, structures = _historical_plan(plan, bundle['case']['as_of'])
                used = _mentioned_ids(sourced, set(old_catalog))
                missing = sorted(used - set(catalog))
                changed = sorted(ident for ident in used & set(catalog) if _pin(old_catalog[ident]) != _pin(catalog[ident]))
                if missing or changed:
                    from .preparation_source_reproof import reprove_historical_plan
                    try:
                        sourced, reproof = reprove_historical_plan(sourced, old_catalog, catalog, source_pin=_pin)
                    except ValueError as exc:
                        raise ValueError('Historical evidence not reconfirmed by current acquisition; missing=' + repr(missing)
                            + ', changed=' + repr(changed) + '; reproof failed: ' + str(exc)) from exc
                    used = _mentioned_ids(sourced, set(catalog))
                else:
                    reproof = []
                checked = prepare_method_inputs(bundle, documents=list(catalog.values()),
                    propose=lambda *_: deepcopy(sourced), source_report=result)
                schema, _ = method_schema(method, sourced)
                observed = {driver for scope in [sourced['model'], *sourced['scenarios'].values()]
                            for driver, item in scope.items() if item.get('kind') == 'historical'}
                required = {driver for driver, definition in schema.items() if definition[3] in ('opening', 'actuals')
                            and driver not in STRUCTURE | {'capital.ke', 'policy', 'nav_target', 'target_basis'}}
                if method == 'operating_fcff':
                    required.update(('net_debt', 'equity_adjustments'))
                blocking = [row['reason'] for row in checked['issues'] if row.get('field') in
                            observed | required | {'documents', 'calendar', 'perimeter', 'legal_structure'}]
                if blocking or not observed or not required <= observed:
                    raise ValueError('; '.join(blocking) or 'Required historical method observations are absent')
                result['source_plan'] = sourced
                result['documents'] = list(catalog.values())
                assembly.update(status='ready', reasons=[], information_cutoff=bundle['case']['as_of'],
                    economic_opening_date=opening, historical_drivers=renewed, unchanged_structure=structures,
                    source_ids=sorted(used), historical_validity_policy='same_day explicitly renewed only after exact source/period/proof recompilation',
                    prior_cutoff=prior['case']['as_of'], source_origin='current_free_acquisition',
                    source_reproof=reproof,
                    limitation='Observed historical proofs and opening structure only; all prospective judgments remain for the run preparer.')
                return result
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                candidate_failures.append(str(exc))
        raise ValueError('; '.join(dict.fromkeys(candidate_failures)) or
                         'No matching documented common historical bridge in the product source/model archive')
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        assembly['reasons'].append(str(exc))
        assembly['reasons'].extend(result.get('fresh_historical_assembly', {}).get('reasons') or [])
    return result
