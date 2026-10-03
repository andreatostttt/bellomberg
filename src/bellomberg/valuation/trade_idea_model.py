"""Free source qualification and exact common-model preparation for Trade Idea.

There is no authorization factory, AI client or portfolio access here. The caller
owns the run grant and supplies a budgeted proposer only after qualification.
"""
from copy import deepcopy
from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _semantic(value):
    """Pins economic evidence, excluding storage location and retrieval clock."""
    volatile = {'downloaded_at', 'retrieved_at', 'fetched_at', 'verified_at', 'freshness_reference_local_date', 'acquisition_fingerprint', 'archive_path', 'local_path',
                'file_path', 'cache_path', 'output_dir', 'path', 'download_path'}
    if isinstance(value, dict):
        return {key: _semantic(child) for key, child in value.items() if key not in volatile}
    if isinstance(value, (list, tuple)):
        return [_semantic(child) for child in value]
    return value


def _source_pin(result):
    """The unchanged economic projection used by fingerprints and diagnostics."""
    report = result.get('source_report') or {}
    documents = sorted(report.get('documents') or [], key=lambda row: row.get('id', ''))
    source_pin = [{key: doc.get(key) for key in ('id', 'url', 'sha256', 'document_sha256',
                   'published_at', 'available_at', 'metadata')} for doc in documents]
    pinned = {'identity': result.get('identity'), 'as_of': result.get('as_of'),
        'snapshot': {key: value for key, value in (result.get('bundle') or {}).items() if key != 'snapshot_id'},
        'sources': source_pin, 'source_basis': (result.get('coverage') or {}).get('sources'),
        'source_plan': report.get('source_plan'), 'reasons': result.get('reasons')}
    if result.get('status') == 'preparation_required' or report.get('historical_preparation_plan') is not None:
        pinned['historical_preparation'] = {'status': result.get('status'),
            'plan': report.get('historical_preparation_plan'), 'admission': report.get('historical_preparation'),
            'selection': report.get('selection'), 'source_gaps': report.get('source_gaps'),
            'balance_sheet': report.get('balance_sheet'), 'issues': report.get('issues'),
            'coverage': report.get('coverage'),
            'content_hashes': [{'id': doc.get('id'),
                'sha256': sha256(doc['text'].encode('utf-8')).hexdigest()
                if isinstance(doc.get('text'), str) else None} for doc in documents]}
    if result.get('analysis_mode') is not None:
        pinned['analysis_mode'] = result['analysis_mode']
    if result.get('research_admission') is not None or result.get('research_revision') is not None:
        pinned['research'] = {'status': result.get('status'),
            'admission': result.get('research_admission'), 'revision': result.get('research_revision'),
            'content_hashes': [{'id': doc.get('id'),
                'sha256': sha256(doc['text'].encode('utf-8')).hexdigest()
                if isinstance(doc.get('text'), str) else None} for doc in documents]}
    return _semantic(pinned)


def source_fingerprint(result):
    """Recheckable semantic fingerprint of the exact qualification result."""
    return _digest(_source_pin(result))


def _free_providers(ticker, identity, as_of):
    """Public profile only. Never load private vehicle or method-input archives."""
    import yfinance as yf
    info = yf.Ticker(ticker).info
    retrieved = datetime.now(timezone.utc)
    source = {"status": "ok", "source_id": "yahoo public issuer profile", "as_of": retrieved.date().isoformat(),
              "retrieved_at": retrieved.isoformat(),
              "data": {"info": info, "vehicle_registry": None}}
    return {"profile": lambda *_args, **_kwargs: deepcopy(source)}


def _identity_problem(ticker, identity, info):
    if not isinstance(identity, dict) or identity.get("status") != "confirmed" or identity.get("ticker") != ticker:
        return "Identita esatta non confermata per il titolo scelto"
    if not identity.get("name") or not identity.get("exchange") or not identity.get("currency"):
        return "Emittente, borsa e valuta della quotazione devono essere confermati"
    symbol = info.get("symbol")
    if symbol is not None and symbol != ticker:
        return "Il profilo documentale appartiene a un simbolo diverso"
    profile_name = info.get('longName') or info.get('shortName')
    canonical_name = lambda value: re.sub(r'[^\w]', '', value.casefold()) if isinstance(value, str) else ''
    if not canonical_name(profile_name):
        return 'Emittente del profilo pubblico non verificabile'
    if canonical_name(profile_name) != canonical_name(identity['name']):
        return 'Emittente del profilo diverso dall identita confermata; nessun alias inferito'
    exchange = info.get('fullExchangeName') or info.get('exchange')
    if not isinstance(exchange, str) or not exchange.strip():
        return 'Borsa del profilo pubblico non verificabile: fonte di quotazione esplicita richiesta'
    if exchange != identity['exchange']:
        return 'Borsa del profilo diversa dall identita confermata; nessun alias di mercato inferito'
    currency = info.get("currency")
    if currency == "GBp":
        currency = "GBX"
    if currency and currency != identity["currency"]:
        return "Valuta del profilo diversa dalla quotazione accettata"
    return None


def _quotation_identity_problem(identity, plan):
    """Bind a sourced quote to the accepted instrument unit, never its accounts."""
    model = plan.get('model') if isinstance(plan, dict) else None
    item = model.get('quotation') if isinstance(model, dict) else None
    quote = item.get('value') if isinstance(item, dict) else None
    if not isinstance(quote, dict):
        return None  # The method proof compiler diagnoses a missing quotation.
    canonical = lambda value: 'GBX' if value == 'GBp' else value
    accepted_unit = canonical((identity or {}).get('currency'))
    quote_unit = canonical(quote.get('quote_unit'))
    accepted_currency = 'GBP' if accepted_unit == 'GBX' else accepted_unit
    if quote_unit != accepted_unit or quote.get('quote_currency') != accepted_currency:
        return ('quotation: sourced quote unit/currency differs from the confirmed instrument '
                f'({quote_unit}/{quote.get("quote_currency")} versus {accepted_unit}/{accepted_currency}); '
                'a primary report cannot replace the accepted quotation identity')
    return None


def _reported_issuer_identity_problem(bundle, identity, plan, catalog):
    """Require exact reported issuer names, or a recompiled primary name pair."""
    if bundle.get('decision', {}).get('method_id') != 'property_nav' or not isinstance(plan, dict):
        return None
    from .property_nav_requirements import REPORTED_POLICY
    model = plan.get('model') or {}
    if (model.get('policy') or {}).get('value') != REPORTED_POLICY:
        return None
    entity = ((model.get('perimeter') or {}).get('value') or {}).get('entity')
    confirmed = (identity or {}).get('name')
    canonical = lambda value: re.sub(r'[^\w]', '', value.casefold()) if isinstance(value, str) else ''
    if canonical(entity) and canonical(entity) == canonical(confirmed):
        return None
    from .preparation_reported_property import reported_property_name_binding
    evidence_ids = (model.get('quotation') or {}).get('evidence_ids') or []
    if reported_property_name_binding(entity, confirmed, catalog, evidence_ids=evidence_ids):
        return None
    return ('Reported property primary issuer differs from the confirmed security identity; '
            'an exact recompiled primary legal-name and registration binding is required')


def _current_quotation_evidence(bundle, plan, as_of, *, observation_only=False):
    """Verify a separate observed quote before spending; retain the model date."""
    from .market_quote import build_market_quote, _day, _freshness
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    quote = ((plan or {}).get('model', {}).get('quotation') or {}).get('value')
    if observation_only:
        # Compare an acquired quote only with its own observed unit. This is
        # not a statement about reporting currency, ADR ratios or model FX;
        # those remain mandatory sourced inputs to the later compiler.
        unit = (bundle.get('case', {}).get('info') or {}).get('currency')
        quote = {'quote_unit': unit, 'quote_currency': unit, 'financial_currency': unit}
    block = build_market_quote(bundle, quote, {})
    basis = {key: block.get(key) for key in ('contract', 'status', 'source_id', 'source_status',
        'acquired_as_of', 'information_cutoff', 'symbol', 'info_symbol', 'exchange', 'exchange_timezone',
        'quote_source_name', 'currency', 'price', 'observed_at', 'observed_local_date', 'freshness_policy')}
    profile = bundle.get('case', {}).get('sources', {}).get('profile') or {}
    basis['retrieved_at'] = profile.get('retrieved_at')
    basis['timestamp_policy'] = 'observed_at <= retrieved_at <= verification_clock_utc; daily_economic_cutoff_is_separate'
    acquired, cutoff = _day(block.get('acquired_as_of')), _day(as_of)
    if acquired is None or cutoff is None:
        return 'Current quotation: exact observation/acquisition/cutoff dates required', basis
    def utc_timestamp(value):
        try:
            stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
            return stamp.astimezone(timezone.utc) if stamp is not None and stamp.utcoffset() is not None else None
        except (ValueError, OverflowError):
            return None
    observation = utc_timestamp(block.get('observed_at'))
    retrieval = utc_timestamp(profile.get('retrieved_at'))
    verified = datetime.now(timezone.utc)
    basis['verified_at'] = verified.isoformat()
    if observation is None or retrieval is None:
        basis['status'] = 'source_unavailable'
        return 'Current quotation requires an aware UTC acquisition receipt; a daily cutoff cannot manufacture a historical acquisition', basis
    if observation > retrieval or retrieval > verified:
        basis['status'] = 'source_unavailable'
        return 'Current quotation timestamp is future: observed_at must not exceed retrieved_at or the real verification clock', basis
    if retrieval.date() != acquired or retrieval.date() > cutoff:
        basis['status'] = 'source_unavailable'
        return 'Current quotation acquisition timestamp differs from its acquired date or exceeds the declared cutoff', basis
    # The daily information cutoff is UTC; exchange local dates are used only
    # for freshness. An observed local next day is not a future UTC instant.
    try:
        zone = ZoneInfo(block['exchange_timezone'])
        reference = min(datetime(cutoff.year, cutoff.month, cutoff.day, 23, 59, 59, 999999, tzinfo=timezone.utc), verified)
        observed_local = observation.astimezone(zone).date()
        reference_local = reference.astimezone(zone).date()
        scoped = deepcopy(bundle)
        scoped['case']['as_of'] = reference_local.isoformat()
        scoped['case']['sources']['profile']['as_of'] = retrieval.astimezone(zone).date().isoformat()
        local_block = build_market_quote(scoped, quote, {})
    except (ZoneInfoNotFoundError, TypeError, ValueError, OverflowError):
        basis['status'] = 'source_unavailable'
        return 'Current quotation requires a valid exchange timezone for observed-date freshness', basis
    # Shared weekly quote behavior remains unchanged. The scoped call checks
    # the actual currency, price, venue/profile and its local acquisition day.
    local_block.update(acquired_as_of=acquired.isoformat(), information_cutoff=cutoff.isoformat())
    basis.update({key: local_block.get(key) for key in ('status','observed_at','observed_local_date')})
    basis['cutoff_timezone'] = 'UTC'
    basis['freshness_reference_local_date'] = reference_local.isoformat()
    if local_block['status'] not in ('ok', 'fx_not_rolled'):
        return 'Current quotation unavailable before preparation: '+local_block['message'], basis
    if _freshness(observed_local, reference_local) != 'ok':
        basis['status'] = 'stale'
        return 'Current quotation is stale at the qualification cutoff; the historical model remains dated and unchanged', basis
    basis['status'] = 'verified_observed_price'
    basis['comparison_status'] = ('fx_not_rolled' if local_block['status'] == 'fx_not_rolled'
                                  else 'not_assessed_before_model')
    basis['comparison_qualified'] = False
    basis['comparison_reason'] = ('Current FX comparison is not qualified; model conversion remains historical'
        if local_block['status'] == 'fx_not_rolled' else 'Fair-value comparison is assessed only after the exact model is generated')
    if observation_only:
        basis['valuation_currency_contract'] = 'not_assessed; observed quote identity only'
    basis['basis'] = 'Acquired regularMarketPrice with exact timestamp/timezone; no daily-close or intraday guarantee is inferred'
    return None, basis


RESEARCH_ADMISSION = 'trade_idea_research_admission/1'


def research_admission(ticker, identity, as_of, *, archive_root, providers=None,
                       documents=(), document_sources=(), accepted_document_receipt=None,
                       analysis_mode=None):
    """Admit bounded primary research; historical/model sufficiency is assessed later.

    A catalog is an index, not a requirement that the issuer already has a
    normalized balance sheet. Supplied documents still require real admission.
    No historical archive, model, paid proposer or source search is invoked.
    """
    from .sector_analysis import prepare_sector_analysis
    from .input_preparation import _catalog, _day
    from .preparation_methods import supported_methods, method_requirements
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    if analysis_mode not in (None, RESEARCH_ANALYSIS_MODE):
        raise ValueError('Unsupported analysis mode')
    research_only = analysis_mode == RESEARCH_ANALYSIS_MODE
    reasons, catalog, provenance, quote_basis = [], {}, {}, {}
    research_gaps = []
    bundle, receipt = None, None
    try:
        if (_day(as_of) is None or not isinstance(ticker, str)
                or not ticker or ticker != ticker.strip().upper()):
            raise ValueError('Exact ticker and ISO research cutoff required')
        bundle = prepare_sector_analysis(ticker, as_of=as_of,
            providers=providers if providers is not None else _free_providers(ticker, identity, as_of))
        problem = _identity_problem(ticker, identity, bundle['case'].get('info') or {})
        if problem:
            reasons.append(problem)
        decision = bundle['decision']
        if not research_only and decision.get('decision_status') != 'resolved':
            reasons.append('Metodo economico non risolto: ' + str(decision.get('decision_status')))
        if not research_only and decision.get('method_id') not in supported_methods():
            reasons.append('Preparazione documentata del metodo non disponibile: ' + str(decision.get('method_id')))
        problem, quote_basis = _current_quotation_evidence(bundle, None, as_of, observation_only=True)
        if problem:
            (research_gaps if research_only else reasons).append(problem)
        supplied = list(documents)
        if (document_sources or accepted_document_receipt is not None) and not reasons:
            from bellomberg.agents.trade_idea_sources import ingest_document_sources, verify_document_receipt
            website = (bundle['case'].get('info') or {}).get('website')
            admitted = (verify_document_receipt(accepted_document_receipt, ticker, identity, as_of,
                        archive_root=archive_root, issuer_website=website,
                        **({'allow_partial': True} if research_only else {}))
                if accepted_document_receipt is not None else
                ingest_document_sources(ticker, identity, as_of, document_sources,
                        archive_root=archive_root, issuer_website=website,
                        **({'allow_partial': True} if research_only else {})))
            receipt = admitted['receipt']
            supplied.extend(admitted['documents'])
            if research_only:
                research_gaps.extend('PM document unavailable: ' + row['reason']
                    for row in receipt['documents'] if row.get('status') != 'verified')
        if supplied:
            catalog, issues, provenance = _catalog(supplied, _day(as_of))
            reasons.extend(row['reason'] for row in issues)
        requirements = (method_requirements(decision['method_id'])
            if not research_only and decision.get('method_id') in supported_methods() else {})
    except Exception as exc:
        reasons.append(type(exc).__name__ + ': ' + str(exc))
        requirements = {}
    result = {'status': 'blocked' if reasons else 'research_required', 'reasons': list(dict.fromkeys(reasons)),
        'ticker': ticker, 'as_of': as_of,
        'identity': {key: (identity or {}).get(key) for key in ('ticker', 'name', 'exchange', 'currency', 'status')},
        'method_id': (bundle or {}).get('decision', {}).get('method_id'), 'bundle': bundle,
        'research_admission': {'contract': RESEARCH_ADMISSION,
            'scope': 'public_primary_same_issuer', 'economic_qualification': 'not_assessed'},
        'source_report': {'status': 'research_required', 'documents': list(catalog.values()),
            'preparation_ready': False, 'issues': [],
            'limitation': 'Catalog evidence only; the author must supply complete sourced historical facts and assumptions before model compilation'},
        'coverage': {'method_id': (bundle or {}).get('decision', {}).get('method_id'),
            'method_version': requirements.get('method_version'), 'source_requirements': requirements.get('fields', []),
            'preparer': 'available' if requirements else 'unavailable', 'economic_qualification': 'not_assessed',
            'document_provenance': provenance, 'sources': {'basis': 'research_identity_and_observed_quote',
                'current_quotation': quote_basis, 'historical_facts': 'not_assessed'}}}
    if research_only:
        result['analysis_mode'] = analysis_mode
        result['research_admission']['contract'] = 'fundamentals_research_admission/1'
        result['source_report'].update(issues=[{'reason': gap} for gap in research_gaps],
            limitation='Official documents support independent research; missing observations remain explicit. No model compilation or mandatory fair value.')
        result['coverage'].update(preparer='not_required', source_requirements=[], method_version=None)
    if receipt is not None:
        result['document_receipt'] = receipt
        result['coverage']['sources']['pm_document_receipt'] = {'contract': receipt['contract'],
            'fingerprint': receipt['fingerprint']}
    result['fingerprint'] = source_fingerprint(result)
    return result


def validate_research_admission(admission):
    """Validate the immutable research grant basis without downloading anything."""
    from .sector_analysis import validate_bundle
    from .preparation_methods import supported_methods
    from bellomberg.core.research_analysis import is_research_mode
    research_only = is_research_mode(admission)
    if (not isinstance(admission, dict) or admission.get('status') != 'research_required'
            or admission.get('research_admission') != {'contract': 'fundamentals_research_admission/1' if research_only else RESEARCH_ADMISSION,
                'scope': 'public_primary_same_issuer', 'economic_qualification': 'not_assessed'}
            or admission.get('research_revision') is not None
            or admission.get('fingerprint') != source_fingerprint(admission)):
        raise ValueError('Research admission identity/integrity differs')
    bundle = validate_bundle(admission.get('bundle'), admission.get('ticker'))
    if (bundle['case'].get('as_of') != admission.get('as_of')
            or not research_only and bundle['decision'].get('decision_status') != 'resolved'
            or bundle['decision'].get('method_id') != admission.get('method_id')
            or not research_only and admission.get('method_id') not in supported_methods()):
        raise ValueError('Research admission method/cutoff differs')
    problem = _identity_problem(admission['ticker'], admission.get('identity'), bundle['case'].get('info') or {})
    if problem:
        raise ValueError(problem)
    problem, _ = _current_quotation_evidence(bundle, None, admission['as_of'], observation_only=True)
    if problem and not research_only:
        raise ValueError(problem)
    return bundle


def research_source_qualification(admission, snapshot):
    """Read a server-verified append-only revision without changing the grant."""
    validate_research_admission(admission)
    empty = (isinstance(snapshot, dict) and snapshot.get('status') == 'empty'
        and snapshot.get('revision_id') is None and snapshot.get('revision_sha256') is None
        and snapshot.get('documents') == []
        and re.fullmatch(r'[0-9a-f]{64}', str(snapshot.get('session_sha256', ''))))
    if (not isinstance(snapshot, dict) or snapshot.get('ticker') != admission['ticker']
            or snapshot.get('as_of') != admission['as_of']
            or snapshot.get('parent_grant_fingerprint') != admission['fingerprint']
            or not isinstance(snapshot.get('run_id'), str) or not snapshot['run_id']
            or not empty and not re.fullmatch(r'[0-9a-f]{64}', str(snapshot.get('revision_sha256', '')))):
        raise ValueError('Research source revision differs from the authorized issuer/cutoff/grant')
    if empty:
        return deepcopy(admission)
    from .input_preparation import _catalog, _day
    from .preparation_service import merge_research_sources
    report = merge_research_sources(admission['source_report'], snapshot)
    documents = report.get('documents') or []
    catalog, issues, provenance = _catalog(documents, _day(admission['as_of'])) if documents else ({}, [], {})
    if issues:
        raise ValueError('Research source integrity failed: ' + '; '.join(row['reason'] for row in issues))
    current = deepcopy(admission)
    current['source_report'] = {**report, 'documents': list(catalog.values())}
    current['research_revision'] = {key: deepcopy(snapshot.get(key)) for key in (
        'run_id', 'ticker', 'as_of', 'revision_id', 'revision_sha256', 'parent_grant_fingerprint')}
    current['coverage']['document_provenance'] = provenance
    current['fingerprint'] = source_fingerprint(current)
    return current


def qualify_authored_research(admission, snapshot, plan, *, archive_root, quotation_receipt=None,
                              statement_receipt=None):
    """Apply the existing economic proof gate after primary research and authoring."""
    current = research_source_qualification(admission, snapshot)
    admitted = current
    if not isinstance(plan, dict):
        raise ValueError('Explicit complete author plan required after research')
    if quotation_receipt is not None:
        from .author_quotation import merge_quotation_evidence
        current = merge_quotation_evidence(current, plan, quotation_receipt, archive_root=archive_root)
    if statement_receipt is not None:
        from .author_statement_evidence import merge_statement_evidence
        current = merge_statement_evidence(admitted, plan, statement_receipt,
            archive_root=archive_root, overlay=current)
    sources = admission['bundle']['case']['sources']
    providers = {name: (lambda *_a, value=source, **_k: deepcopy(value)) for name, source in sources.items()}
    report = {**deepcopy(current['source_report']), 'source_plan': deepcopy(plan)}
    qualified = qualification(admission['ticker'], admission['identity'], admission['as_of'],
        archive_root=archive_root, providers=providers, source_report=report,
        historical_seed_loader=lambda *_a, **_k: [])
    if qualified['status'] != 'qualified':
        raise ValueError('Historical model proof after research failed: ' + '; '.join(qualified['reasons']))
    qualified['research_admission'] = deepcopy(admission['research_admission'])
    qualified['research_revision'] = deepcopy(current.get('research_revision') or {
        key: snapshot.get(key) for key in ('run_id', 'ticker', 'as_of', 'revision_id',
            'revision_sha256', 'parent_grant_fingerprint')})
    qualified['fingerprint'] = source_fingerprint(qualified)
    return qualified


def _source_problems(bundle, report, catalog):
    """Use verified method observations, never URL counts or downloaded_at."""
    from .input_preparation import prepare_method_inputs
    from .preparation_methods import method_schema
    method = bundle["decision"].get("method_id")
    problems = []
    evidence = report.get("source_plan")
    if isinstance(evidence, dict):
        from math import isfinite
        positive = lambda value: type(value) in (int, float) and isfinite(value) and value > 0
        scopes = [('model', evidence.get('model') or {}), *(evidence.get('scenarios') or {}).items()]
        for scope, drivers in scopes:
            for driver, item in drivers.items():
                if not isinstance(item, dict):
                    continue
                if driver in ('shares', 'capital.shares_m') and not positive(item.get('value')):
                    problems.append(scope+'.'+driver+': positive finite share denominator required before preparation')
                if driver == 'historical_revenue' and method == 'operating_fcff' and not positive(item.get('value')):
                    problems.append(scope+'.'+driver+': positive finite operating revenue base required before preparation')
                if driver == 'quotation':
                    quote = item.get('value') or {}
                    if not isinstance(quote, dict) or any(not positive(quote.get(key)) for key in
                            ('price', 'shares_per_quote', 'financial_to_quote_rate', 'quote_units_per_currency')):
                        problems.append(scope+'.quotation: positive finite price and exact conversion factors required before preparation')
        # An archived or deterministic sourced plan can prove availability by
        # recompiling the actual proofs without calling an AI provider.
        checked = prepare_method_inputs(bundle, documents=list(catalog.values()),
                                        propose=lambda *_: deepcopy(evidence), source_report=report)
        historical = set()
        try:
            schema, _ = method_schema(method, evidence)
            historical = {driver for driver, spec in schema.items()
                          if spec[3] in ("opening", "actuals") and driver not in
                          {"perimeter", "calendar", "policy", "capital.ke", "nav_target", "target_basis"}}
            historical.update(driver for scope in [evidence.get('model') or {}, *(evidence.get('scenarios') or {}).values()]
                              for driver, item in scope.items() if isinstance(item, dict) and item.get('kind') == 'historical')
            if method == 'fund_nav' and ((evidence.get('model', {}).get('components') or {}).get('value') or {}).get('basis') == 'incremental_claim_deductions':
                # This policy carries an observed off-balance dilution proof.
                # It must be verified before spending, despite its estimate kind.
                historical.add('policy')
            if method == 'operating_fcff':
                # Their legacy scenario timing is not their economic date.
                # Pending history proves net debt before Growth. The separate
                # equity bridge may be a sourced economic judgment after the
                # forecast; a legacy already-qualified historical bridge stays
                # locked to its original opening observations.
                pending_history = ((report.get('historical_preparation') or {}).get('status')
                                   == 'required')
                opening_bridges = ('net_debt',) if pending_history else (
                    'net_debt', 'equity_adjustments')
                historical.update(opening_bridges)
                for driver in opening_bridges:
                    values = []
                    for scenario in ('bear', 'base', 'bull'):
                        item = (evidence.get('scenarios', {}).get(scenario) or {}).get(driver)
                        if not isinstance(item, dict) or item.get('kind') != 'historical':
                            problems.append(scenario+'.'+driver+': opening equity bridge requires a verified historical observation, not an analyst estimate or missing zero')
                        else:
                            values.append(item.get('value'))
                    if len(values) == 3 and any(value != values[0] for value in values[1:]):
                        problems.append(driver+': opening historical balance differs across scenarios')
                if pending_history:
                    equity = [(evidence.get('scenarios', {}).get(scenario) or {}).get('equity_adjustments')
                              for scenario in ('bear', 'base', 'bull')]
                    if any(isinstance(item, dict) and item.get('kind') == 'historical' for item in equity):
                        historical.add('equity_adjustments')
                        if not all(isinstance(item, dict) and item.get('kind') == 'historical'
                                   for item in equity):
                            problems.append('equity_adjustments: historical and estimated scenario bridges cannot mix')
                        elif any(item.get('value') != equity[0].get('value') for item in equity[1:]):
                            problems.append('equity_adjustments: opening historical balance differs across scenarios')
        except ValueError as exc:
            problems.append(str(exc))
        for row in checked["issues"]:
            if row.get("field") in historical or row.get("field") in ("documents", "calendar", "perimeter", "legal_structure"):
                problems.append(row["reason"])
        if method == 'property_nav':
            from .property_nav_requirements import REPORTED_POLICY
            values = {key: row.get('value') for key, row in (evidence.get('model') or {}).items()
                      if isinstance(row, dict)}
            required_snapshot = {'balance_sheet', 'property_reconciliation', 'epra_bridge',
                                 'claims', 'restrictions', 'development', 'shares', 'calendar'}
            if values.get('policy') == REPORTED_POLICY and required_snapshot <= set(values):
                from .property_reported_nav import project_reported
                try:
                    project_reported(values, {}, lambda driver, reason: problems.append(driver+': '+reason),
                                     historical_only=True)
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    problems.append('Reported property opening reconciliation failed: '+str(exc))
        if method == 'exposure_analysis':
            from .trade_idea_commodity_exposure import selected, project
            values = {key: row.get('value') for key, row in (evidence.get('model') or {}).items() if isinstance(row, dict)}
            if selected(values):
                observed = project(values, ticker=bundle['case']['ticker'], instrument=bundle['decision']['instrument'], cutoff=bundle['case']['as_of'])
                problems.extend(observed['issues'])
        if checked["status"] in ("unsupported",) or not historical:
            problems.extend(row["reason"] for row in checked["issues"])
        if not problems:
            estimated_opening = {driver: item for driver, item in (evidence.get('model') or {}).items()
                                 if isinstance(item, dict) and driver in historical
                                 and item.get('kind') == 'analyst_estimate' and 'dilution_estimate' in item}
            basis = {"basis": "recompiled_historical_proofs", "historical_drivers": sorted(historical - estimated_opening.keys()),
                     "prepared_plan_available": checked["status"] == "prepared"}
            if estimated_opening:
                from .diluted_share_estimate import diluted_share_disclosure
                basis['estimated_opening_drivers'] = {
                    driver: {'kind': item['kind'], 'historical_operands_verified': True,
                             'limitation': diluted_share_disclosure(item)}
                    for driver, item in estimated_opening.items()}
            return [], basis
    elif report.get("preparation_ready") is True and method in ("operating_fcff", "bank_residual_income", "fund_nav"):
        # Collector readiness describes document availability. It cannot prove
        # a complete working-capital bridge, the legal capital ledger or claims
        # deducted from NAV. Those observations need a recompiled sourced plan.
        normalizers = {(doc.get("metadata") or {}).get("normalizer") for doc in catalog.values()}
        originals = [doc for doc in catalog.values() if not (doc.get("metadata") or {}).get("normalizer")
                     and (doc.get("metadata") or {}).get("report_date")]
        quotes = [doc for doc in catalog.values() if str(doc.get("id", "")).startswith("price-")]
        if not originals:
            problems.append("Bilancio primario con periodo economico verificato assente")
        if not quotes:
            problems.append("Prova di quotazione alla data del modello assente")
        if not report.get("selection", {}).get("opening_date"):
            problems.append("Data economica dei saldi iniziali non verificata")
        if method == "bank_residual_income" and not (
                report.get("bank_regulatory_sources", {}).get("status") == "ready"
                and report.get("parent_regulatory_sources", {}).get("status") == "ready"
                or "sec_parent_inline_v1" in normalizers):
            problems.append("Capitale bancario e bilancio parent-only non qualificati")
        if method == "operating_fcff" and report.get("balance_sheet", {}).get("status") == "incomplete":
            problems.append("Stato patrimoniale completo non riconciliato")
        for row in report.get("source_gaps", []):
            problems.append(str(row.get("reason") if isinstance(row, dict) else row))
        problems.append("Documenti acquisiti, ma riconciliazione economica storica non qualificata: serve un source_plan con prove effettive del metodo")
        current_history = report.get('fresh_historical_assembly') or report.get('historical_source_assembly') or {}
        problems.extend(current_history.get('reasons') or [])
    else:
        problems.append("Prove storiche del metodo non ancora qualificate; la presenza dei documenti non basta")
    if not catalog:
        problems.append("Nessun documento verificabile acquisito")
    return list(dict.fromkeys(problems)), {"basis": "insufficient_method_observations"}


def qualification(ticker, identity, as_of, *, archive_root, providers=None,
                  source_report=None, collector=None, documents=(), historical_seed_loader=None):
    """Free, repeatable source gate; returns the exact bundle and source fingerprint."""
    from .sector_analysis import prepare_sector_analysis
    from .input_preparation import _catalog, _day
    from .preparation_methods import supported_methods
    cutoff = _day(as_of)
    reasons = []
    bundle, report, catalog, provenance = None, {}, {}, {}
    documents_consumed = False
    historical_pending = False
    try:
        if cutoff is None:
            raise ValueError("Cutoff informativo ISO richiesto")
        if not isinstance(ticker, str) or not ticker.strip() or ticker != ticker.strip().upper():
            raise ValueError("Ticker canonico esatto richiesto")
        bundle = prepare_sector_analysis(ticker, as_of=as_of,
            providers=providers if providers is not None else _free_providers(ticker, identity, as_of))
        info = bundle["case"].get("info") or {}
        problem = _identity_problem(ticker, identity, info)
        if problem:
            reasons.append(problem)
        decision = bundle["decision"]
        if decision.get("decision_status") != "resolved":
            reasons.append("Metodo economico non risolto: " + str(decision.get("decision_status")))
        if decision.get("method_id") not in supported_methods():
            reasons.append("Preparazione documentata del metodo non disponibile: " + str(decision.get("method_id")))
        if source_report is None and not reasons:
            from .preparation_historical_sources import collect_trade_idea_sources
            collector_args = {'seed_loader': historical_seed_loader, 'issuer_name': identity['name']} if collector is None else {}
            if collector is None and decision.get('method_id') == 'exposure_analysis' and documents:
                collector_args['raw_documents'] = list(documents)
                documents_consumed = True
            if collector is not None:
                # Only an explicitly declared server collector protocol receives
                # the profile website. Generic **kwargs wrappers and fixtures keep
                # their existing contract; a browser claim cannot curate hosts.
                from inspect import signature
                if 'issuer_website' in signature(collector).parameters:
                    collector_args['issuer_website'] = info.get('website')
            report = (collector or collect_trade_idea_sources)(ticker, as_of=as_of,
                archive_root=archive_root, financial_currency=info.get("financialCurrency"),
                method_id=decision.get("method_id"), **collector_args)
            if not isinstance(report.get('source_plan'), dict):
                from .preparation_historical_sources import assemble_historical_sources
                report = assemble_historical_sources(bundle, report, archive_root=archive_root,
                    seed_loader=historical_seed_loader)
        else:
            report = deepcopy(source_report or {})
            if decision.get('method_id') == 'exposure_analysis' and not isinstance(report.get('source_plan'), dict):
                from .preparation_historical_sources import assemble_historical_sources
                report['documents'] = list(report.get('documents') or []) + list(documents)
                documents_consumed = True
                report = assemble_historical_sources(bundle, report, archive_root=archive_root,
                    seed_loader=historical_seed_loader)
        if decision.get('method_id') == 'operating_fcff' and not isinstance(report.get('source_plan'), dict):
            from .preparation_fresh_historical import assemble_fresh_historical
            report = assemble_fresh_historical(bundle, report, method_id='operating_fcff', as_of=as_of)
        historical_pending = ((report.get('historical_preparation') or {}).get('status') == 'required'
            and isinstance(report.get('historical_preparation_plan'), dict)
            and not isinstance(report.get('source_plan'), dict))
        source_plan = report.get('historical_preparation_plan') if historical_pending else report.get('source_plan')
        supplied = list(report.get("documents") or []) + ([] if documents_consumed else list(documents))
        catalog, issues, provenance = _catalog(supplied, cutoff)
        reasons.extend(row["reason"] for row in issues)
        if not catalog:
            for issue in report.get('issues') or []:
                if isinstance(issue, dict) and issue.get('reason'):
                    detail = re.sub(r'(?i)\b[A-Z]:[\\/][^;\r\n]+', '[archivio locale]', str(issue['reason']))
                    reasons.append('Acquisizione ' + str(issue.get('source') or 'fonti') + ': ' + detail[:500])
        quote_problem = _quotation_identity_problem(identity, source_plan)
        if quote_problem:
            reasons.append(quote_problem)
        issuer_problem = _reported_issuer_identity_problem(bundle, identity, source_plan, catalog)
        if issuer_problem:
            reasons.append(issuer_problem)
        if not reasons:
            if historical_pending:
                source_problems, source_basis = [], {'basis': 'verified_documents_for_historical_preparation',
                    'missing_historical_drivers': report['historical_preparation']['missing_drivers'],
                    'catalog_warnings': deepcopy(report.get('issues') or []),
                    'limitation': 'Document admission only; historical classifications and balances must compile before forecasts'}
            else:
                source_problems, source_basis = _source_problems(bundle, report, catalog)
            reasons.extend(source_problems)
        else:
            source_basis = {"basis": "not_assessed_after_prior_failure"}
        if isinstance(source_plan, dict):
            current_problem, current_basis = _current_quotation_evidence(bundle, source_plan, as_of)
            source_basis['current_quotation'] = current_basis
            if current_problem:
                reasons.append(current_problem)
        report["documents"] = list(catalog.values())
        from .preparation_methods import method_requirements
        requirements = method_requirements(decision['method_id'], report.get('source_plan')) if decision.get('method_id') else {}
        coverage = {"method_id": decision.get("method_id"), "method_version": requirements.get("method_version"),
                    "source_requirements": requirements.get("fields", []), "sources": source_basis,
                    "preparer": "available" if decision.get("method_id") in supported_methods() else "unavailable",
                    "economic_qualification": "pending_historical_proofs" if historical_pending else "not_assessed", "engine": decision.get("support_status"),
                    "document_provenance": provenance}
    except Exception as exc:
        reasons.append(type(exc).__name__ + ": " + str(exc))
        coverage = {"economic_qualification": "not_assessed"}
    identity_pin = {key: (identity or {}).get(key) for key in ("ticker", "name", "exchange", "currency", "status")}
    result = {"status": "blocked" if reasons else "preparation_required" if historical_pending else "qualified", "reasons": list(dict.fromkeys(reasons)),
              "ticker": ticker, "as_of": as_of, "identity": identity_pin,
              "method_id": (bundle or {}).get("decision", {}).get("method_id"),
              "bundle": bundle, "source_report": report, "coverage": coverage}
    result["fingerprint"] = source_fingerprint(result)
    return result


def recheck_accepted_sources(accepted, ticker, identity, *, archive_root, allow_historical=False):
    """Countercheck exact research sources, never acquire or redate replacements.

    Historical mode is for an explicitly authorized native continuation. It
    preserves the original research date; current book/quote/FX and operational
    approval remain the separate routing checks owned by the orchestrator.
    """
    from .input_preparation import _catalog, _day
    from .sector_analysis import validate_bundle
    if (not isinstance(accepted, dict) or accepted.get('ticker') != ticker
            or accepted.get('identity') != identity
            or accepted.get('status') not in ('qualified', 'preparation_required', 'research_required')
            or accepted.get('fingerprint') != source_fingerprint(accepted)):
        raise ValueError('Accepted source snapshot identity/integrity check failed')
    cutoff = _day(accepted.get('as_of'))
    today = datetime.now(timezone.utc).date()
    if (type(allow_historical) is not bool or cutoff is None or cutoff > today
            or not allow_historical and cutoff != today):
        raise ValueError('Accepted source cutoff expired; a fresh preflight and authorization are required')
    bundle = validate_bundle(accepted.get('bundle'), ticker)
    if bundle['case'].get('as_of') != accepted['as_of']:
        raise ValueError('Accepted source bundle cutoff differs')
    report = accepted.get('source_report') or {}
    documents = report.get('documents') or []
    research = accepted.get('status') == 'research_required'
    if not isinstance(documents, list) or not documents and not research or any(
            not isinstance(doc, dict) or not isinstance(doc.get('id'), str) for doc in documents):
        raise ValueError('Accepted source document catalog integrity check failed')
    root = Path(archive_root).resolve()
    selection = report.get('selection') or {}
    required_raw = set(selection.get('selected_document_ids') or [])
    required_raw.update(selection[key] for key in
        ('selected_document_id', 'annual_document_id', 'comparative_document_id') if selection.get(key))
    required_raw.update(document['id'] for document in documents if any(key in document for key in
        ('filing_verification', 'inline_parent_fields', 'statement_table_fields',
         'balance_detail_fields', 'balance_sheet_fields')))
    if not required_raw.issubset({doc['id'] for doc in documents}):
        raise ValueError('Accepted source selection is absent from its catalog')
    for document in documents:
        path = document.get('archive_path')
        if document['id'] in required_raw and not path:
            raise ValueError('Accepted selected primary archive is unavailable')
        if path is not None:
            try:
                archived = Path(path).resolve()
                if not archived.is_relative_to(root):
                    raise ValueError('Accepted primary archive escaped the source archive')
                raw_hash = sha256(archived.read_bytes()).hexdigest()
            except OSError as exc:
                raise ValueError('Accepted primary archive is missing or unreadable') from exc
            if raw_hash != document.get('document_sha256'):
                raise ValueError('Accepted primary archive bytes changed')
    # Normalizers may read primaries: validate their containment before _catalog.
    catalog, issues, _ = _catalog(documents, cutoff) if documents else ({}, [], {})
    if issues or not catalog and not research:
        raise ValueError('Accepted source document catalog integrity check failed')
    sources = bundle['case'].get('sources')
    if not isinstance(sources, dict) or not isinstance(sources.get('profile'), dict):
        raise ValueError('Accepted source provider envelopes are unavailable')
    receipt = accepted.get('document_receipt')
    if receipt is not None:
        from bellomberg.agents.trade_idea_sources import verify_document_receipt
        from bellomberg.core.research_analysis import is_research_mode
        verify_document_receipt(receipt, ticker, identity, accepted['as_of'], archive_root=root,
            issuer_website=(bundle['case'].get('info') or {}).get('website'),
            **({'allow_partial': True} if is_research_mode(accepted) else {}))
    if research:
        validate_research_admission(accepted)
        return deepcopy(accepted)
    providers = {name: (lambda *_a, value=source, **_k: deepcopy(value))
                 for name, source in sources.items()}
    checked = qualification(ticker, identity, accepted['as_of'], archive_root=root,
        providers=providers, source_report=report, historical_seed_loader=lambda *_a, **_k: [])
    if receipt is not None:
        checked['document_receipt'] = deepcopy(receipt)
        pin = (accepted.get('coverage', {}).get('sources') or {}).get('pm_document_receipt')
        if not isinstance(pin, dict) or pin.get('fingerprint') != receipt.get('fingerprint'):
            raise ValueError('Accepted PM document receipt coverage differs')
        checked['coverage'].setdefault('sources', {})['pm_document_receipt'] = deepcopy(pin)
        checked['fingerprint'] = source_fingerprint(checked)
    return checked


def source_validation_receipt(accepted, checked, *, mode, status):
    """Bounded structural diff with hashed values; no source text or private context."""
    # Unknown keys are hashed as well: a JSON key can itself contain private data.
    known = set(('bundle case info sources profile data decision coverage source_report identity '
        'ticker name exchange currency status as_of method_id reasons fingerprint documents metadata '
        'source_plan historical_preparation_plan historical_preparation selection issues source_gaps '
        'snapshot source_basis plan admission content_hashes balance_sheet current_quotation symbol longName shortName fullExchangeName regularMarketPrice '
        'regularMarketTime exchangeTimezoneName quoteSourceName financialCurrency published_at available_at '
        'sha256 document_sha256 id snapshot_id').split())
    rows, total = [], 0
    missing = object()
    def pin(value):
        return {'type': 'missing'} if value is missing else {
            'type': type(value).__name__, 'sha256': _digest(value)}
    def visit(before, after, path):
        nonlocal total
        if before is not missing and after is not missing and _digest(before) == _digest(after):
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                segment = key if key in known else 'key-sha256-' + sha256(key.encode()).hexdigest()
                visit(before.get(key, missing), after.get(key, missing), path + '/' + segment)
        elif isinstance(before, list) and isinstance(after, list):
            for index in range(max(len(before), len(after))):
                visit(before[index] if index < len(before) else missing,
                      after[index] if index < len(after) else missing, path + '/' + str(index))
        else:
            total += 1
            if len(rows) < 100:
                rows.append({'field': path, 'accepted': pin(before), 'checked': pin(after)})
    candidate_available = isinstance(checked, dict)
    accepted = accepted if isinstance(accepted, dict) else {}
    checked = checked if isinstance(checked, dict) else {}
    diff_status = 'available' if candidate_available else 'unavailable'
    if candidate_available:
        try:
            visit(_source_pin(accepted), _source_pin(checked), '')
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
            rows, total, diff_status = [], 0, 'malformed_qualification'
    def fingerprint(value):
        pin = value.get('fingerprint')
        return pin if isinstance(pin, str) and re.fullmatch(r'[0-9a-f]{64}', pin) else None
    return {'contract': 'trade_idea_source_validation/1', 'mode': mode, 'status': status,
        'accepted_fingerprint': fingerprint(accepted), 'checked_fingerprint': fingerprint(checked),
        'candidate_available': candidate_available, 'diff_status': diff_status,
        'changed_fields': rows, 'changed_field_count': total if diff_status == 'available' else None,
        'truncated': total > len(rows), 'consumed_basis': (
            'accepted_immutable_snapshot' if mode == 'accepted_snapshot' else 'explicit_injected_qualifier')
            if status == 'verified' else None}


def _historical_driver_changes(before, after):
    """Compare fact identity and every proof, separating only plain author prose.

    Compiler disclosures can carry arithmetic/provenance inside rationale. Those
    strings, and any structured-looking rationale, remain frozen conservatively;
    only a plain, nonempty top-level comment may differ. Nested calculation and
    classification rationale is part of the historical proof and stays exact.
    """
    def protected(text):
        return (any(char in text for char in '{}[]=<>') or bool(re.search(
            r'\d|https?://|\b(?:proof|prova|calcolo|sum|somma|arithmetic|reconciliation)\b|'
            r'(?:NWC coverage|ANALYST CLASSIFICATION|BALANCE BRIDGE CLASSIFICATION|'
            r'SOURCE POINTER NORMALIZED|SOURCE CALCULATION|REPORTED PRECISION|'
            r'PROXY/STIMA DILUZIONE|Riconciliazione aggregata dei valori riportati)',
            text, re.IGNORECASE)))

    left, right = deepcopy(before), deepcopy(after)
    if isinstance(left, dict) and isinstance(right, dict):
        a, b = left.get('rationale'), right.get('rationale')
        if (isinstance(a, str) and a.strip() and isinstance(b, str) and b.strip()
                and not protected(a) and not protected(b)):
            left.pop('rationale'); right.pop('rationale')
    changes = []
    missing = object()
    def compare(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                compare(a.get(key, missing), b.get(key, missing), path + ('.' if path else '') + key)
        elif isinstance(a, list) and isinstance(b, list):
            for index in range(max(len(a), len(b))):
                compare(a[index] if index < len(a) else missing,
                        b[index] if index < len(b) else missing, path + '[' + str(index) + ']')
        elif a != b:
            changes.append(path or '<driver>')
    compare(left, right, '')
    return changes


class ExplicitPlanProposer:
    """Project an authored native plan into the existing preparation stages."""

    def __init__(self, plan):
        if not isinstance(plan, dict):
            raise ValueError('Piano esplicito completo richiesto')
        json.dumps(plan, allow_nan=False)
        self._plan = deepcopy(plan)
        self._values = self._driver_values(plan)

    @staticmethod
    def _driver_values(plan):
        if not isinstance(plan.get('model'), dict) or not isinstance(plan.get('scenarios'), dict):
            raise ValueError('Driver model e scenarios espliciti richiesti')
        def values(drivers):
            if not isinstance(drivers, dict) or any(not isinstance(name, str)
                    or not isinstance(item, dict) or 'value' not in item
                    for name, item in drivers.items()):
                raise ValueError('Ogni driver deve dichiarare il proprio valore esplicito')
            return {name: deepcopy(item['value']) for name, item in drivers.items()}
        return {'model': values(plan['model']),
                'scenarios': {scope: values(drivers) for scope, drivers in plan['scenarios'].items()}}

    def attest_values(self, plan):
        """Verify stage consumption, then retain every authored proof and rationale."""
        authored = json.dumps(self._values, sort_keys=True, allow_nan=False)
        assembled = json.dumps(self._driver_values(plan), sort_keys=True, allow_nan=False)
        if assembled != authored:
            raise ValueError('La preparazione non puo cambiare o omettere i valori del piano esplicito')
        return deepcopy(self._plan)

    def __call__(self, _dossier, contract):
        stage = contract.get('preparation_stage')
        if stage is None:
            return deepcopy(self._plan)
        if not isinstance(stage, dict) or not isinstance(stage.get('drivers'), list):
            raise ValueError('Identita dello stadio di preparazione richiesta')
        scope, names = stage.get('scope'), stage['drivers']
        if not names or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise ValueError('Driver esatti dello stadio richiesti')
        drivers = self._plan['model'] if scope == 'model' else self._plan['scenarios'][scope]
        selected = {name: deepcopy(drivers[name]) for name in names}
        if scope == 'model':
            rationales = [drivers[name].get('rationale') for name in names]
            if any(not isinstance(reason, str) or not reason.strip() for reason in rationales):
                raise ValueError('Motivazione esplicita di ogni driver model richiesta')
            rationale = '\n'.join(rationales)
        else:
            rationale = (self._plan.get('scenario_rationale') or {}).get(scope)
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError('Motivazione esplicita dello scenario richiesta')
        return {'drivers': selected, 'rationale': rationale}


def build_from_plan(qualification, plan, output_dir, *, author_context=None):
    """Compile Fundamentals' complete plan into a fresh common workbook, without AI."""
    if author_context is not None and not isinstance(author_context, dict):
        raise ValueError('Contesto autore strutturato richiesto')
    context = deepcopy(author_context) if author_context is not None else {}
    if 'model_author' in context:
        raise ValueError('Identita autore e stato di approvazione sono metadati del compilatore')
    proposer = ExplicitPlanProposer(plan)
    context['model_author'] = {'actor': 'fundamentals', 'basis': 'explicit_author_plan',
        'approval_status': 'not_PM_approved', 'plan_sha256': _digest(plan)}
    json.dumps(context, allow_nan=False)
    payload = prepare(qualification, proposer, output_dir, revision_context=context)
    usability = candidate_model_usability(payload)
    if not usability['usable']:
        issues = (payload.get('preparation') or {}).get('issues') or []
        reasons = [row.get('reason', str(row)) for row in issues] + usability['reasons']
        raise ValueError('Piano esplicito non compilabile o non ammissibile: ' + '; '.join(reasons))
    return payload


def prepare(qualification, propose, output_dir, *, revision_context=None):
    """Run the common compiler and workbook engine; no grant or paid call is created here."""
    explicit_plan = propose if isinstance(propose, ExplicitPlanProposer) else None
    historical_pending = qualification.get('status') == 'preparation_required'
    if qualification.get("status") not in ('qualified', 'preparation_required'):
        raise ValueError("Fonti non qualificate: " + "; ".join(qualification.get("reasons") or []))
    if not callable(propose):
        raise ValueError("Preparatore autorizzato della run richiesto")
    if not output_dir:
        raise ValueError("Cartella modello della run richiesta")
    if qualification.get('fingerprint') != source_fingerprint(qualification):
        raise ValueError('Fonti modificate dopo la qualifica accettata')
    initial_report = qualification['source_report']
    if historical_pending:
        from .input_preparation import has_approved_inputs
        if has_approved_inputs(qualification['bundle']):
            raise ValueError('Pending history cannot reuse approved inputs before historical verification')
        from .preparation_fresh_historical import assemble_fresh_historical
        verified = assemble_fresh_historical(qualification['bundle'], initial_report,
            method_id=qualification.get('method_id'), as_of=qualification['as_of'])
        if ((verified.get('historical_preparation') or {}).get('status') != 'required'
                or _digest(verified.get('historical_preparation_plan')) != _digest(initial_report.get('historical_preparation_plan'))):
            raise ValueError('Historical preparation documents changed or are no longer admissible')
    initial_plan = initial_report.get('historical_preparation_plan') if historical_pending else initial_report.get('source_plan')
    current_problem, _ = _current_quotation_evidence(qualification['bundle'], initial_plan, qualification['as_of'])
    if current_problem:
        raise ValueError('Fonti non qualificate: '+current_problem)
    from .preparation_service import prepare_and_generate
    from .preparation_ai import BudgetedProposer, StagedProposer
    from .preparation_seed import ScopedRevisionProposer
    report = deepcopy(initial_report)
    historical_checkpoint = {}
    scoped_revision = propose if isinstance(propose, ScopedRevisionProposer) else None
    def accept_history(plan, dossier, contract, *, persist=True):
        from .input_preparation import _catalog, _day
        from .preparation_seed import make_seed
        catalog, issues, _ = _catalog(report['documents'], _day(qualification['as_of']))
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        # A legacy qualified source plan already proves the opening equity
        # bridge. The new historical stage requests only net debt, so use the
        # frozen bridge when reviewing the partial checkpoint, without adding
        # a driver to the staged proposal itself.
        review_plan = plan if historical_pending else deepcopy(plan)
        frozen_bridge_changes = []
        if not historical_pending:
            for scope, values in initial_plan['scenarios'].items():
                prior_bridge = values['equity_adjustments']
                proposed_bridge = (plan.get('scenarios', {}).get(scope) or {}).get('equity_adjustments')
                if proposed_bridge is not None:
                    changes = _historical_driver_changes(prior_bridge, proposed_bridge)
                    if changes:
                        frozen_bridge_changes.append(scope + '.equity_adjustments: changed qualified opening bridge before Growth (' + ', '.join(changes) + ')')
                review_plan['scenarios'][scope]['equity_adjustments'] = deepcopy(prior_bridge)
        candidate = {**report, 'source_plan': review_plan}
        problems, basis = _source_problems(qualification['bundle'], candidate, catalog)
        problems.extend(frozen_bridge_changes)
        for driver, prior in initial_plan['model'].items():
            if driver == 'capdev_amortization_years':
                continue  # Prospective model judgment follows the historical checkpoint.
            if (plan.get('model', {}).get(driver) or {}).get('value') != prior.get('value'):
                problems.append(driver + ': historical preparation changed the admitted observation or convention')
        if not historical_pending:
            for scope, values in initial_plan['scenarios'].items():
                for driver in ('net_debt', 'equity_adjustments'):
                    if review_plan['scenarios'][scope][driver]['value'] != values[driver]['value']:
                        problems.append(scope + '.' + driver + ': changed qualified opening balance before Growth')
        quote_problem = _quotation_identity_problem(qualification.get('identity'), plan)
        if quote_problem:
            problems.append(quote_problem)
        if problems:
            raise ValueError('Historical preparation not qualified: ' + '; '.join(dict.fromkeys(problems)))
        checkpoint = {'contract': 'trade_idea_historical_checkpoint/1', 'status': 'historical_qualified',
            'admission_fingerprint': qualification['fingerprint'], 'source_basis': basis,
            'candidate': make_seed(dossier, contract, plan),
            'resume_policy': 'Replay exact journal responses and recompile history; this partial candidate is not a complete model or an authorization'}
        if not persist:
            historical_checkpoint.update(checkpoint)
            report['source_plan'] = deepcopy(review_plan)
            return
        destination = Path(output_dir) / 'historical-preparation.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        import os
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile('w', encoding='utf-8', dir=destination.parent, delete=False,
                prefix='.historical-', suffix='.tmp') as handle:
            temporary = Path(handle.name)
            try:
                json.dump(checkpoint, handle, ensure_ascii=False, allow_nan=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                handle.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        historical_checkpoint.update(checkpoint)
        report['source_plan'] = deepcopy(review_plan)

    if historical_pending:
        if isinstance(propose, StagedProposer):
            raise ValueError('Historical preparation requires the authorized base proposer, not an already staged delegate')
        if scoped_revision is not None:
            def propose(dossier, contract):
                return scoped_revision(dossier, contract, on_historical=lambda plan, ds, ct:
                    accept_history(plan, ds, ct, persist=False))
        else:
            propose = StagedProposer(propose, drivers_per_stage=12, historical_first=True, on_historical=accept_history,
                growth_thesis_first=isinstance(propose, BudgetedProposer))
    elif scoped_revision is not None and scoped_revision.checkpoint is not None:
        def propose(dossier, contract):
            return scoped_revision(dossier, contract, on_historical=lambda plan, ds, ct:
                accept_history(plan, ds, ct, persist=False))
    elif isinstance(propose, BudgetedProposer):
        if qualification.get('method_id') == 'operating_fcff':
            propose = StagedProposer(propose, drivers_per_stage=12, historical_first=True,
                on_historical=accept_history, growth_thesis_first=True)
        elif qualification.get('method_id') in ('bank_residual_income', 'fund_nav'):
            propose = StagedProposer(propose)
        else:
            from .preparation_family_stages import FamilyStagedProposer
            propose = FamilyStagedProposer(propose, schema_seed=qualification['source_report'].get('source_plan'))
    delegate = propose
    def checked_proposal(dossier, contract):
        plan = delegate(dossier, contract)
        if explicit_plan is not None:
            plan = explicit_plan.attest_values(plan)
        if historical_pending and not historical_checkpoint:
            raise ValueError('Historical checkpoint missing before forecast completion')
        from .input_preparation import _catalog, _day
        catalog, issues, _ = _catalog(report['documents'], _day(qualification['as_of']))
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        candidate_report = {**report, 'source_plan': plan}
        problems, _basis = _source_problems(qualification['bundle'], candidate_report, catalog)
        quote_problem = _quotation_identity_problem(qualification.get('identity'), plan)
        if quote_problem:
            problems.append(quote_problem)
        issuer_problem = _reported_issuer_identity_problem(qualification['bundle'],
            qualification.get('identity'), plan, catalog)
        if issuer_problem:
            problems.append(issuer_problem)
        if qualification.get('method_id') == 'operating_fcff' and isinstance(plan, dict):
            prior = report.get('source_plan') or {}
            for scenario in ('bear', 'base', 'bull'):
                for driver in ('net_debt', 'equity_adjustments'):
                    frozen = ((prior.get('scenarios') or {}).get(scenario) or {})
                    if historical_pending and driver not in frozen:
                        continue  # The equity judgment follows Growth; it was not checkpointed.
                    old = frozen.get(driver) or {}
                    proposed = ((plan.get('scenarios') or {}).get(scenario) or {}).get(driver) or {}
                    if proposed.get('value') != old.get('value'):
                        problems.append(scenario+'.'+driver+': preparer changed the qualified opening balance; retain the source observation separately')
        if historical_pending:
            prior = report['source_plan']
            for scope, values in [('model', prior['model']), *prior['scenarios'].items()]:
                current = plan['model'] if scope == 'model' else plan['scenarios'].get(scope, {})
                for driver, item in values.items():
                    changed = _historical_driver_changes(item, current.get(driver))
                    if changed:
                        problems.append(scope + '.' + driver + ': forecast changed verified historical identity/proof fields: ' + ', '.join(changed))
        if problems:
            raise ValueError('Prepared source observations differ from the qualified facts: '+'; '.join(dict.fromkeys(problems)))
        if scoped_revision is not None and historical_checkpoint:
            accept_history(historical_checkpoint['candidate']['plan'], dossier, contract)
        return plan
    for attribute in ('preparation_selection', 'refresh_lineage'):
        if hasattr(delegate, attribute):
            setattr(checked_proposal, attribute, getattr(delegate, attribute))
    payload = prepare_and_generate(qualification["bundle"], documents=report["documents"],
        propose=checked_proposal, output_dir=str(output_dir), source_report=report,
        preparation_context=revision_context)
    if historical_checkpoint:
        payload['historical_preparation'] = deepcopy(historical_checkpoint)
    growth_thesis = getattr(delegate, 'growth_thesis', None)
    if growth_thesis:
        payload.setdefault('preparation', {})['growth_thesis'] = deepcopy(growth_thesis)
        if getattr(delegate, 'growth_correction', None):
            payload['preparation']['growth_correction'] = deepcopy(delegate.growth_correction)
        payload['preparation']['growth_thesis_source_fingerprint'] = qualification['fingerprint']
        payload['preparation']['growth_thesis_generation_id'] = payload.get('generation_id')
    if historical_checkpoint or growth_thesis:
        if payload.get('path'):
            from .dcf_engine import _write_payload_sidecar
            payload = _write_payload_sidecar(payload)
    return payload


def _verify_review_facts(before_records, reviewed_records, *, partial=False, checkpoint_drivers=()):
    """Raw analyst reviews cannot replace observations or their pinned provenance.

    Source corrections use the source-refresh compiler. A qualified catalog or
    an evidence ID alone cannot attest a changed historical/guidance record.
    Paid requests may be partial, but their produced model must retain the
    complete original factual multiset, independently of record ordering.
    """
    if not isinstance(before_records, list) or not isinstance(reviewed_records, list):
        raise ValueError('Record completi del modello richiesti per congelare fatti e guidance')
    factual_kinds = {'historical', 'company_guidance'}
    def identity(row):
        return tuple(row.get(name) for name in ('field', 'driver', 'scenario', 'entity'))
    if partial:
        existing_slots = {identity(row) for row in before_records}
        requested_slots = [identity(row) for row in reviewed_records]
        if len(requested_slots) != len(set(requested_slots)):
            raise ValueError('Ogni slot della revisione deve avere una sola sostituzione esplicita')
        if any(slot not in existing_slots for slot in requested_slots):
            raise ValueError('I driver della revisione devono sostituire slot esistenti del modello precedente')
    before_facts = [row for row in before_records if row.get('kind') in factual_kinds
                    or (row.get('scenario'), row.get('driver')) in checkpoint_drivers]
    fact_identities = {identity(row) for row in before_facts}
    requested_facts = [row for row in reviewed_records
                       if row.get('kind') in factual_kinds or identity(row) in fact_identities]
    original = Counter(_digest(row) for row in before_facts)
    reviewed = Counter(_digest(row) for row in requested_facts)
    if (partial and reviewed - original) or (not partial and reviewed != original):
        raise ValueError('Fatti storici e company guidance immutabili nella revisione; '
                         'anche i driver del checkpoint sono immutabili: valori, convenzioni, identita '
                         'e prove richiedono un source refresh verificato')


def _verify_admitted_history_for_revision(payload, qualification, *, diagnostic_review=False):
    """A pending admission can review only the model that proved its history."""
    from .sector_analysis import validate_bundle
    from .preparation_seed import restore_seed
    from .input_preparation import _catalog, _day
    checkpoint = payload.get('historical_preparation') or {}
    if (qualification.get('method_id') != 'operating_fcff'
            or checkpoint.get('contract') != 'trade_idea_historical_checkpoint/1'
            or checkpoint.get('status') != 'historical_qualified'
            or checkpoint.get('admission_fingerprint') != qualification.get('fingerprint')
            or payload.get('ticker') != qualification['ticker']
            or (payload.get('valuation_usability', {}).get('usable') is not True and not (
                diagnostic_review and payload.get('preparation', {}).get('status') == 'prepared'
                and payload['preparation'].get('issues') == []
                and payload.get('input_consumption', {}).get('status') == 'complete'))
            or not payload.get('generation_id')):
        raise ValueError('Verified historical checkpoint bound to this admission required for revision')
    snapshot = validate_bundle(payload.get('acquisition_snapshot'), qualification['ticker'])
    if (snapshot['snapshot_id'] != payload.get('snapshot_id')
            or snapshot['decision'].get('method_id') != qualification['method_id']):
        raise ValueError('Historical checkpoint model snapshot differs from the reviewed generation')
    prepared = payload.get('preparation') or {}
    basis = prepared.get('review_basis') or {}
    historical = restore_seed(checkpoint.get('candidate'), basis.get('dossier'), basis.get('contract'))
    full = (prepared.get('proposal') or {}).get('plan') or {}
    for scope, values in [('model', historical['model']), *historical['scenarios'].items()]:
        current = full.get('model', {}) if scope == 'model' else (full.get('scenarios') or {}).get(scope, {})
        if any(current.get(driver) != item for driver, item in values.items()):
            raise ValueError('Historical checkpoint differs from the reviewed model plan')
        for driver, item in values.items():
            actual = [row for row in snapshot['case']['records']
                      if row.get('scenario') == scope and row.get('driver') == driver]
            if (len(actual) != 1 or actual[0].get('value') != item.get('value')
                    or actual[0].get('kind') != item.get('kind')
                    or actual[0].get('source_locator') != ','.join(item.get('evidence_ids') or [])):
                raise ValueError('Historical checkpoint differs from the reviewed model records')
    report = qualification['source_report']
    catalog, issues, _ = _catalog(report.get('documents') or [], _day(qualification['as_of']))
    if issues:
        raise ValueError('Historical revision sources no longer verify: ' + '; '.join(row['reason'] for row in issues))
    problems, _ = _source_problems(qualification['bundle'], {**report, 'source_plan': historical}, catalog)
    if problems:
        raise ValueError('Historical checkpoint no longer qualifies: ' + '; '.join(problems))


def revise(before_payload, changes, *, qualification=None, propose=None, output_dir):
    """Create a new common generation; original model and sources stay immutable."""
    from .sector_analysis import revise_sector_analysis
    from .dcf_engine import generate_valuation
    if not isinstance(changes, dict) or set(changes) - {"assumptions", "analysis_context", "method_records", "rationale", "evidence_ids"}:
        raise ValueError("Revisione strutturata con campi consumati richiesta")
    if not changes.get("rationale") or not changes.get("evidence_ids"):
        raise ValueError("Motivo e prove della revisione richiesti")
    if changes.get('assumptions'):
        raise ValueError('Ipotesi legacy non ammesse: specificare i driver nei method_records documentati')
    if not isinstance(changes['evidence_ids'], list) or any(not isinstance(ident, str) for ident in changes['evidence_ids']):
        raise ValueError('evidence_ids deve contenere gli ID documentali esatti')
    if changes.get('analysis_context') is not None and not isinstance(changes['analysis_context'], dict):
        raise ValueError('analysis_context deve essere strutturato')
    scoped = 'review_scope' in (changes.get('analysis_context') or {})
    if scoped and propose is None:
        raise ValueError('scoped review requires an authorized AI proposer and exact qualification')
    records = changes.get("method_records")
    if records is not None and not isinstance(records, list):
        raise ValueError("method_records deve essere una lista completa")
    # Reject non-finite or non-JSON review inputs before any paid call.
    json.dumps(changes, allow_nan=False)
    if records is not None and any(not isinstance(row, dict) or not
            {'field', 'driver', 'value', 'scenario', 'entity'} <= set(row) for row in records):
        raise ValueError('Record della revisione privo di driver, scenario, entity o valore esplicito')
    bundle = deepcopy(before_payload.get("acquisition_snapshot"))
    if not bundle:
        raise ValueError("Snapshot esatto del modello precedente assente")
    if scoped:
        from .sector_analysis import validate_bundle
        snapshot = validate_bundle(bundle, bundle['case']['ticker'])
        if snapshot['snapshot_id'] != before_payload.get('snapshot_id') or not before_payload.get('generation_id'):
            raise ValueError('scoped review requires the exact previous snapshot and generation')
        if before_payload.get('preparation', {}).get('status') != 'prepared' or before_payload['preparation'].get('issues'):
            raise ValueError('scoped review requires a complete previously compiled preparation')
        old_path = Path(before_payload.get('path') or '').resolve()
        if not old_path.is_file() or sha256(old_path.read_bytes()).hexdigest() != before_payload.get('workbook_sha256'):
            raise ValueError('scoped review previous workbook is missing or changed')
        if Path(output_dir).resolve() == old_path.parent:
            raise ValueError('scoped review output directory must preserve the previous workbook and sidecar')
    original_records = bundle.get('case', {}).get('records')
    checkpoint_plan = ((before_payload.get('historical_preparation') or {}).get('candidate') or {}).get('plan') or {}
    checkpoint_drivers = {('model', driver) for driver in checkpoint_plan.get('model', {})}
    checkpoint_drivers.update((scope, driver) for scope, values in checkpoint_plan.get('scenarios', {}).items()
                              for driver in values)
    if records is not None:
        _verify_review_facts(original_records, records, partial=propose is not None,
                             checkpoint_drivers=checkpoint_drivers)
    if qualification is not None:
        if qualification.get("status") not in ('qualified', 'preparation_required') or qualification["ticker"] != bundle["case"]["ticker"]:
            raise ValueError("Fonti della revisione non qualificate per lo stesso titolo")
        if qualification.get('fingerprint') != source_fingerprint(qualification):
            raise ValueError('Fonti modificate dopo la qualifica accettata della revisione')
        if qualification['status'] == 'preparation_required':
            _verify_admitted_history_for_revision(before_payload, qualification, diagnostic_review=scoped)
        ids = {doc["id"] for doc in qualification["source_report"].get("documents", [])}
        if not set(changes["evidence_ids"]) <= ids:
            raise ValueError("La revisione cita documenti non qualificati")
    else:
        basis = before_payload.get('preparation', {}).get('review_basis', {}).get('dossier', {})
        ids = {doc['id'] for doc in basis.get('documents', [])}
        if not set(changes['evidence_ids']) <= ids:
            raise ValueError('La revisione cita documenti non presenti nel modello precedente')
    if propose is not None:
        if qualification is None:
            raise ValueError("Revisione AI richiede fonti qualificate esatte")
        context = {'task': 'revise_exact_common_model',
            'before': {key: deepcopy(before_payload.get(key)) for key in
                       ('snapshot_id', 'generation_id', 'workbook_sha256', 'valuation_date', 'fair_value_base')},
            'before_method_records': deepcopy(bundle['case'].get('records') or []),
            'before_plan': deepcopy(before_payload.get('preparation', {}).get('proposal', {}).get('plan')),
            'reviewer_changes': deepcopy(changes),
            'policy': 'Consume the reviewer changes against the exact prior model and qualified evidence. Explicit numeric method_records are requested replacement values; never silently ignore them. Preserve historical facts and explain the revision in the three scenario rationales. No target-price calibration.'}
        revision_context = context
        scoped_proposer = None
        if scoped:
            from .preparation_seed import ScopedRevisionProposer
            context.pop('before_plan')
            context.pop('before_method_records')
            context['policy'] = ('Review exactly the current preparation_stage scope and driver list, using the '
                'exact previous model and qualified visible evidence. Explicit numeric method_records are '
                'requested replacements; never ignore them. Preserve every unrequested driver, historical '
                'fact, guidance and checkpoint. Return drivers and the complete rationale only for the '
                'current scenario; reconcile dependent terminal assumptions atomically. No target-price calibration.')
            prepared = before_payload.get('preparation') or {}
            basis = prepared.get('review_basis') or {}
            scoped_proposer = ScopedRevisionProposer(propose, basis=basis,
                plan=(prepared.get('proposal') or {}).get('plan'), snapshot=bundle,
                scopes=changes['analysis_context']['review_scope'], context=context,
                checkpoint=(before_payload.get('historical_preparation') or {}).get('candidate'),
                requested_records=records)
            propose = scoped_proposer
            # Preserve the historical seed's exact base. The new instructions
            # are recorded by the scoped wrapper in its provider view/lineage.
            revision_context = (basis.get('dossier') or {}).get('preparation_context')
        result = prepare(qualification, propose, output_dir, revision_context=revision_context)
        if scoped and result.get('preparation', {}).get('status') != 'prepared':
            raise ValueError(result.get('error') or 'scoped review did not compile a complete model')
        _verify_review_facts(original_records, result.get('acquisition_snapshot', {}).get('case', {}).get('records'),
                             checkpoint_drivers=checkpoint_drivers)
        if (scoped or result.get('valuation_usability', {}).get('usable')) and records is not None:
            _verify_requested_records(records, result)
        result.setdefault('preparation', {}).setdefault('provenance', {})['trade_idea_revision'] = {
            'previous_snapshot_id': before_payload.get('snapshot_id'),
            'previous_generation_id': before_payload.get('generation_id'),
            'previous_workbook_sha256': before_payload.get('workbook_sha256'),
            'rationale': changes['rationale'], 'evidence_ids': list(changes['evidence_ids']),
            'reviewer_changes_sha256': _digest(changes),
            'requested_drivers': [row.get('scenario', '') + '/' + row.get('driver', '') for row in records or []]}
        if scoped:
            result['preparation']['provenance']['trade_idea_revision']['scoped_review'] = deepcopy(scoped_proposer.lineage)
            if before_payload.get('preparation', {}).get('growth_thesis_history'):
                result['preparation']['growth_thesis_history'] = deepcopy(
                    before_payload['preparation']['growth_thesis_history'])
            if before_payload.get('preparation', {}).get('growth_thesis'):
                result['preparation'].setdefault('growth_thesis_history', []).append({
                    'thesis': deepcopy(before_payload['preparation']['growth_thesis']),
                    'source_fingerprint': before_payload['preparation'].get('growth_thesis_source_fingerprint'),
                    'previous_generation_id': before_payload.get('generation_id')})
            if result['preparation'].get('growth_thesis_history'):
                result['preparation']['growth_thesis_status'] = 'superseded_by_explicit_model_revision'
        if result.get('path'):
            from .dcf_engine import _write_payload_sidecar
            result = _write_payload_sidecar(result)
        return result
    revised = revise_sector_analysis(bundle, assumptions=changes.get("assumptions"),
        analysis_context=changes.get("analysis_context"), method_records=records)
    _verify_review_facts(original_records, revised.get('case', {}).get('records'),
                         checkpoint_drivers=checkpoint_drivers)
    result = generate_valuation(bundle["case"]["ticker"], prepared_bundle=revised, output_dir=str(output_dir))
    result['preparation'] = deepcopy(before_payload.get('preparation') or {})
    if result['preparation'].get('growth_thesis'):
        result['preparation'].setdefault('growth_thesis_history', []).append({
            'thesis': result['preparation'].pop('growth_thesis'),
            'source_fingerprint': result['preparation'].pop('growth_thesis_source_fingerprint', None),
            'previous_generation_id': result['preparation'].pop('growth_thesis_generation_id', before_payload.get('generation_id'))})
        result['preparation']['growth_thesis_status'] = 'superseded_by_explicit_model_revision'
    if before_payload.get('historical_preparation'):
        result['historical_preparation'] = deepcopy(before_payload['historical_preparation'])
    result['preparation'].setdefault('provenance', {})['trade_idea_revision'] = {"previous_snapshot_id": before_payload.get("snapshot_id"),
        "previous_generation_id": before_payload.get("generation_id"),
        "rationale": changes["rationale"], "evidence_ids": list(changes["evidence_ids"])}
    if result.get("path"):
        from .dcf_engine import _write_payload_sidecar
        result = _write_payload_sidecar(result)
    return result


def _verify_requested_records(requested, result):
    """A typed replacement cannot vanish silently during paid preparation."""
    # Consumption receipts hold record identity and evidence, not the values.
    # Values are pinned in the exact acquisition snapshot passed to the engine.
    consumed = result.get('acquisition_snapshot', {}).get('case', {}).get('records') or []
    def key(row):
        return tuple(row.get(name) for name in ('field', 'driver', 'scenario', 'entity'))
    index = {key(row): row for row in consumed}
    for row in requested:
        if not isinstance(row, dict) or not {'field', 'driver', 'value', 'scenario', 'entity'} <= set(row):
            raise ValueError('Record della revisione privo di driver, scenario, entity o valore esplicito')
        output = index.get(key(row))
        if output is None or output.get('value') != row['value']:
            raise ValueError('Driver richiesto dalla revisione non applicato: ' + str(row.get('driver')))


def model_exhibits(payload, *, workbook_path=None):
    from .trade_idea_exhibits import model_exhibits as render_exhibits
    return render_exhibits(payload, workbook_path=workbook_path)


def model_packet(payload, *, workbook_path=None):
    """Stable common packet for the dossier and research workspace."""
    return model_exhibits(payload, workbook_path=workbook_path)


def candidate_model_usability(payload):
    """Exposure is an explicit observational objective, never an FV fallback."""
    if payload.get('method') != 'exposure_analysis':
        value = payload.get('valuation_usability') or {}
        return {'usable': value.get('usable') is True, 'kind': 'intrinsic',
                'reasons': list(value.get('reasons') or [])}
    reasons = []
    try:
        from .sector_analysis import validate_bundle
        from .trade_idea_exposure import project_exposure, SCHEMA
        from .trade_idea_commodity_exposure import selected_records, SCHEMA as COMMODITY_SCHEMA, CONTRACT, bind as commodity_bind
        bundle = validate_bundle(payload.get('acquisition_snapshot'), payload.get('ticker'))
        decision = payload.get('valuation_decision') or {}
        if (bundle['decision'] != decision or decision.get('method_id') != 'exposure_analysis'
                or decision.get('decision_status') != 'resolved'
                or bundle['snapshot_id'] != payload.get('snapshot_id') or not payload.get('generation_id')):
            raise ValueError('Exposure method/acquisition/generation identity not confirmed')
        usability = payload.get('analysis_usability') or {}
        commodity = selected_records(bundle['case']['records'])
        if commodity:
            SCHEMA = COMMODITY_SCHEMA
            binding = commodity_bind(bundle)
            if binding['issues']:
                raise ValueError('; '.join(row['reason'] for row in binding['issues']))
        if usability.get('usable') is not True or usability.get('contract') != (CONTRACT if commodity else 'documented_exposure/1'):
            raise ValueError('Documented exposure contract incomplete: ' + '; '.join(usability.get('reasons') or []))
        records = bundle['case']['records']
        consumption = payload.get('input_consumption') or {}
        consumed = consumption.get('consumed_records') or []
        if (consumption.get('status') != 'complete' or consumption.get('unconsumed_fields') != []
                or len(consumed) != len(records) or len(records) != len(SCHEMA)
                or {row.get('driver') for row in records} != set(SCHEMA)):
            raise ValueError('Exposure records not consumed completely')
        for index, (row, record) in enumerate(zip(consumed, records)):
            if row.get('record_index') != index or any(row.get(key) != record.get(key) for key in
                    ('field', 'driver', 'scenario', 'entity', 'period', 'source_id')):
                raise ValueError('Exposure consumption identity differs from acquired observations')
        projected = project_exposure({row['driver']: row['value'] for row in records},
            ticker=bundle['case']['ticker'], instrument=decision['instrument'], valuation_date=payload.get('valuation_date'), cutoff=bundle['case']['as_of'])
        if projected.get('status') != 'complete' or projected != payload.get('exposure_analysis'):
            raise ValueError('Exposure reconciliations differ from the exact observed inputs')
        if any(key.startswith('fair_value') and value is not None for key, value in payload.items()):
            raise ValueError('Observed exposure must not claim a corporate fair value')
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        reasons.append(str(exc))
    return {'usable': not reasons, 'kind': 'exposure', 'reasons': reasons,
            'objective': 'observed_exposure_analysis', 'intrinsic_value_applicable': False}


def public_model_payload(payload):
    from .trade_idea_public import public_model_payload as project
    return project(payload)


def prepare_shareable_model(payload, output_dir):
    from .trade_idea_public import prepare_shareable_model as generate
    return generate(payload, output_dir)
