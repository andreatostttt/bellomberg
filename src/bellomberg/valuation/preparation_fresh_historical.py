"""Free source-first opening assembly, independent of a previous valuation.

Only exact observations are copied. The ordinary compiler rechecks their source,
scope, units and dates. Unsupported reconciliations remain named gaps; this is
neither a forecast generator nor permission to replace a missing claim with zero.
"""
from calendar import monthrange
from copy import deepcopy
from datetime import date, timedelta
import json
from math import isclose
import re


def _issuer_key(value):
    """Full legal name, case/spacing and punctuation only; no suffix aliases."""
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r'[^\w]', '', value.casefold(), flags=re.UNICODE)


def _body(document):
    try:
        value = json.loads(document['text'])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, KeyError):
        return {}


def _historical_source_issues(report, catalog):
    """Classify only proved limitations unrelated to missing opening evidence.

    The caller supplies the recompiled catalog. All original issues remain in
    the dossier, including unread call material for the prospective analyst.
    """
    coverage = report.get('coverage') or {}
    selected = (report.get('selection') or {}).get('selected_document_ids') or []
    blocking, limitations = [], []
    for issue in report.get('issues', []):
        scope = None
        if (issue == {'source': 'catalog', 'reason': 'document limit reached; coverage partial'}
                and coverage.get('catalog_checked') is True and coverage.get('catalog_status') == 'ok'
                and coverage.get('selection_policy') == 'opening_annual_comparative'
                and type(coverage.get('limited')) is int and coverage['limited'] > 0
                and type(coverage.get('max_documents')) is int and coverage['max_documents'] > 0
                and coverage.get('accepted') == coverage['max_documents']):
            scope = 'bounded_filing_catalog'
        elif isinstance(issue, dict) and issue.get('source') == 'SEC companyfacts':
            match = re.fullmatch(r'no tagged facts for filing ([0-9]{18})', issue.get('reason', ''))
            originals = [ident for ident in selected if ident in catalog and match and
                str((catalog[ident].get('metadata') or {}).get('accession') or '').replace('-', '') == match[1]]
            if len(originals) == 1 and any(
                    (doc.get('metadata') or {}).get('normalizer') == 'statement_tables_v1'
                    and doc['metadata'].get('source_document_id') == originals[0] for doc in catalog.values()):
                scope = 'printed_statements_replace_unavailable_tags'
        elif (isinstance(issue, dict) and issue.get('source') == 'SEC earnings releases'
                and issue.get('reason') == 'Explicitly referenced call material not acquired'):
            earnings = report.get('earnings_releases') or {}
            for reference in earnings.get('referenced_materials', []):
                proof = reference.get('source_reference') or {}
                original = catalog.get(proof.get('document_id'), {})
                meta = original.get('metadata') or {}
                try:
                    from .preparation_exhibits import _sec_parts
                    cik, accession, _ = _sec_parts(original.get('url', ''))
                    selected_issuers = {(catalog[ident].get('metadata') or {}).get('emittente_id')
                                        for ident in selected if ident in catalog}
                    sec_earnings = (meta.get('form') in ('8-K', '8-K/A', '6-K', '6-K/A')
                        and meta.get('emittente_id') == 'CIK:' + cik.zfill(10)
                        and selected_issuers == {meta['emittente_id']}
                        and str(meta.get('accession') or '').replace('-', '') == accession)
                except (ValueError, TypeError, KeyError):
                    sec_earnings = False
                start, end = proof.get('char_start'), proof.get('char_end_exclusive')
                if (reference.get('url') == issue.get('url') and reference.get('status') == 'not_acquired'
                        and issue in earnings.get('issues', []) and original and sec_earnings
                        and proof.get('text_sha256') == original.get('sha256')
                        and type(start) is int and type(end) is int and 0 <= start < end <= len(original['text'])
                        and original['text'][start:end] == proof.get('quote')
                        and isinstance(issue.get('url'), str) and issue['url'] in proof['quote']):
                    scope = 'unread_call_material'
                    break
        if scope:
            limitations.append({'scope': scope, 'issue': deepcopy(issue),
                'limitation': 'Retained source limitation; no missing guidance or call content is inferred. '
                    'Opening facts and complete balance still require independent primary proofs.'})
        else:
            blocking.append(issue)
    return blocking, limitations


def _item(value, ids, cutoff, *, historical=True, **proof):
    return dict(value=deepcopy(value), kind='historical' if historical else 'analyst_estimate',
        evidence_ids=sorted(set(ids)), rationale=(
            'Exact dated primary observation; recompiled before any paid preparation.' if historical else
            'Explicit modeling convention for free source qualification; no forecast value or investment judgment.'),
        valid_until=cutoff, valid_until_basis={'policy': 'same_day', 'as_of': cutoff}, **proof)


def _unique(candidates, driver):
    if not candidates:
        raise ValueError(driver + ': no complete same-entity, same-date primary observation')
    values = [row['value'] for row in candidates]
    if any(not isclose(values[0], value, rel_tol=1e-12, abs_tol=1e-10) for value in values[1:]):
        raise ValueError(driver + ': conflicting primary observations; explicit reconciliation required')
    # Identical observations remain in the immutable catalog. Source selection
    # is deterministic only after equality, never a resolution of disagreement.
    return sorted(candidates, key=lambda row: (row['evidence_ids'], row.get('record_pointer', '')))[0]


def _typed(driver, descriptor, catalog, entity, on, currency, cutoff):
    from .input_preparation import _source_scale, _finite
    from .preparation_record_evidence import FIELDS, prove_reported_record
    field, raw_unit, basis = descriptor[:3]
    unit = currency + ' million' if raw_unit == 'money' else raw_unit
    candidates = []
    for document in catalog.values():
        records = _body(document).get('records', [])
        if not isinstance(records, list):
            continue
        for index, row in enumerate(records):
            if (not isinstance(row, dict) or set(row) != FIELDS or row.get('driver') != driver
                    or row.get('entity') != entity or row.get('period') != on
                    or not _finite(row.get('value'))):
                continue
            scale = _source_scale(row['unit'], unit)
            if scale is None:
                continue
            item = _item(row['value'] * scale, [document['id']], cutoff,
                         record_pointer='/records/' + str(index))
            if prove_reported_record(item, [document], driver=driver, field=field,
                    entity=entity, period=on, unit=unit, basis=basis) is None:
                candidates.append(item)
    return candidates


def _revenue(catalog, entity, on, currency, cutoff, *, sec_filings=()):
    from .input_evidence import revenue_concepts, same_entity_name, same_sec_bound_issuer
    from .input_preparation import _fact_proof, _source_scale, _finite
    candidates, observations, seen = [], [], set()
    for document in catalog.values():
        body = _body(document)
        facts = body.get('facts', [])
        if not isinstance(facts, list):
            continue
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict) or (fact.get('taxonomy'), fact.get('concept')) not in revenue_concepts():
                continue
            # Deduplicate only byte-equivalent normalized facts within the same
            # immutable source. Different provenance or accounting annotations
            # remain distinct and can never be used to erase a conflict.
            identity = (document['id'], json.dumps(fact, sort_keys=True, ensure_ascii=False))
            if identity in seen:
                continue
            seen.add(identity)
            observation = fact.get('observation', fact)
            fact_entity = fact.get('entity', body.get('issuer', (document.get('metadata') or {}).get('entity')))
            if (not same_entity_name(fact_entity, entity)
                    and not same_sec_bound_issuer(document, body, fact, entity, sec_filings)):
                continue
            value = observation.get('val') if 'observation' in fact else observation.get('value')
            scale = _source_scale(fact.get('unit'), currency + ' million')
            if scale is None or not _finite(value):
                continue
            root = '/facts/' + str(index)
            parent = root + '/observation' if 'observation' in fact else root
            item = _item(value * scale, [document['id']], cutoff,
                quoted_value=value, quoted_unit=fact['unit'], evidence_pointer={
                    'value': parent + ('/val' if 'observation' in fact else '/value'),
                    'unit': root + '/unit', 'period': parent + '/end'})
            try:
                start, end = date.fromisoformat(observation['start']), date.fromisoformat(observation['end'])
            except (ValueError, TypeError, KeyError):
                continue
            if start > end or end > date.fromisoformat(on):
                continue
            observations.append((start, end, item))
            if end.isoformat() == on and _fact_proof('historical_revenue', item, [document, *sec_filings], currency + ' million', on,
                           expected_entity=entity) is None:
                candidates.append(item)
    if len(observations) > 256:
        raise ValueError('historical_revenue: excessive ambiguous observations; explicit period selection required')
    # The existing proof compiler checks issuer, accession, concept, currencies,
    # full durations and primary normalized provenance for every operand.
    combinations = []
    for current_start, current_end, current in observations:
        if current_end.isoformat() != on:
            continue
        for annual_start, annual_end, annual in observations:
            annual_days = (annual_end - annual_start).days + 1
            if not 364 <= annual_days <= 371 or current_start != annual_end + timedelta(days=1):
                continue
            for prior_start, prior_end, prior in observations:
                if prior_start != annual_start or prior_end >= annual_end:
                    continue
                combinations.append((annual, current, prior))
                if len(combinations) > 1024:
                    raise ValueError('historical_revenue: excessive ambiguous TTM combinations; explicit primary period selection required')
    for annual, current, prior in combinations:
        terms = {name: {key: deepcopy(row[key]) for key in
                 ('evidence_ids', 'evidence_pointer', 'quoted_value', 'quoted_unit')}
                 for name, row in (('annual', annual), ('current_ytd', current), ('prior_ytd', prior))}
        ids = sorted({ident for row in terms.values() for ident in row['evidence_ids']})
        item = _item(annual['value'] + current['value'] - prior['value'], ids, cutoff,
            calculation={'operation': 'trailing_twelve_months', 'terms': terms})
        if _fact_proof('historical_revenue', item, [catalog[ident] for ident in ids] + list(sec_filings),
                       currency + ' million', on, expected_entity=entity) is None:
            candidates.append(item)
    return _unique(candidates, 'historical_revenue')


def _quotation(catalog, ticker, on, currency, cutoff, *, entity=None, primary_id=None):
    from .input_preparation import _quotation_proof
    from urllib.parse import quote as url_quote
    listings = []
    for document in catalog.values():
        body, meta = _body(document), document.get('metadata') or {}
        listing = body.get('listing') or {}
        listing_identity = (document.get('origin') == 'SEC_listing_unit_identity'
            or document['id'].startswith('listing-')
            or meta.get('normalizer') == 'foreign_ordinary_listing_v1')
        source_id = meta.get('source_document_id') or document['id'].removeprefix('listing-')
        source_entity = (catalog.get(source_id, {}).get('metadata') or {}).get('issuer')
        bound_entity = entity or source_entity
        issuer_claims = [value for value in (meta.get('issuer'), listing.get('issuer'), source_entity) if value]
        issuer_matches = (bound_entity and (primary_id is None or source_id == primary_id)
                          and source_id in catalog and issuer_claims
                          and all(_issuer_key(value) == _issuer_key(bound_entity) for value in issuer_claims))
        if (listing_identity and meta.get('ticker') == ticker
                and meta.get('report_date') == on and listing.get('status') == 'verified'
                and listing.get('symbol') == ticker and listing.get('shares_per_quote') == 1
                and meta.get('basis') == 'one_listed_ordinary_share_is_one_share_of_the_same_class'
                and meta.get('share_class') == listing.get('title') and listing.get('exchange') and issuer_matches):
            listings.append(document)
    if len(listings) != 1:
        raise ValueError('quotation: unique dated close and exact listed share-class identity required')
    listing = listings[0]
    parent_id = (listing.get('metadata') or {}).get('source_document_id') or listing['id'].removeprefix('listing-')
    if (listing.get('metadata') or {}).get('normalizer') != 'foreign_ordinary_listing_v1':
        from .quotation_evidence import verify_sec_listing
        verify_sec_listing(listing, catalog[parent_id], ticker=ticker, on=on)
    quote_on, price_basis = on, None
    if date.fromisoformat(on).weekday() >= 5:
        from .quotation_evidence import sec_us_weekend_quote_basis
        quote_on, price_basis = sec_us_weekend_quote_basis(
            listing, catalog[parent_id], ticker=ticker, opening_date=on)
    prices = []
    for document in catalog.values():
        body = _body(document)
        observation = body.get('observation') or {}
        if (document['id'] == 'price-' + ticker + '-' + quote_on
                and document.get('url') == 'https://finance.yahoo.com/quote/' + url_quote(ticker, safe='') + '/history/'
                and body.get('symbol') == ticker and observation.get('symbol') == ticker
                and observation.get('date') == quote_on):
            prices.append(document)
    if len(prices) != 1:
        raise ValueError('quotation: unique dated close and exact listed share-class identity required')
    price = prices[0]
    observed = _body(price)['observation']
    quote_currency = observed.get('currency')
    if not isinstance(quote_currency, str) or len(quote_currency) != 3 or not quote_currency.isupper():
        raise ValueError('quotation: subunit quote requires an explicit unit bridge; no currency identity default')
    fx = None
    if quote_currency != currency:
        matches = [document for document in catalog.values() if
            (document.get('metadata') or {}).get('normalizer') in ('ecb_reference_fx_v1', 'ecb_reference_cross_fx_v1')
            and document['metadata'].get('financial_currency') == currency
            and document['metadata'].get('quote_currency') == quote_currency
            and document['metadata'].get('report_date') == on]
        if len(matches) != 1:
            raise ValueError('quotation: unique recompiled same-day FX bridge required; no spot or identity fallback')
        fx = matches[0]
    value = dict(financial_currency=currency, quote_currency=quote_currency, quote_unit=quote_currency,
        quote_units_per_currency=1., financial_to_quote_rate=1., shares_per_quote=1.,
        share_class=listing['metadata']['share_class'], price=observed.get('close'), price_as_of=quote_on)
    if price_basis:
        value['price_date_basis'] = price_basis
    facts = {}
    pairs = [('price', price), ('shares_per_quote', listing)]
    if fx:
        pairs.append(('financial_to_quote_rate', fx))
        value['financial_to_quote_rate'] = _body(fx)['facts'][0]['value']
    for key, source in pairs:
        row = _body(source)['facts'][0]
        facts[key] = dict(evidence_ids=[source['id']], quoted_value=row['value'], quoted_unit=row['unit'],
            evidence_pointer={'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'})
    sources = [source for _, source in pairs]
    result = _item(value, [source['id'] for source in sources], cutoff, facts=facts)
    if price_basis:
        result['rationale'] += (' Explicit exchange weekend policy ' + price_basis['policy'] + ': accounting date ' + on
            + '; actual unadjusted close observed on ' + quote_on + '. No accounting balance rollforward.')
    problem = _quotation_proof(result, sources, expected_entity=entity, expected_ticker=ticker,
                              opening_date=on, sec_filings=[catalog[parent_id]], sec_primary_id=parent_id)
    if problem:
        raise ValueError('quotation: ' + problem)
    return result


def _currency(catalog, primary, entity, on):
    """Financial currency comes from reported revenue, not trading currency."""
    from .input_evidence import revenue_concepts, same_entity_name, same_sec_bound_issuer
    found = set()
    for document in catalog.values():
        body = _body(document)
        for fact in body.get('facts', []) if isinstance(body.get('facts'), list) else []:
            if not isinstance(fact, dict):
                continue
            observation = fact.get('observation', fact)
            if ((fact.get('taxonomy'), fact.get('concept')) in revenue_concepts()
                    and observation.get('end') == on
                    and (same_entity_name(fact.get('entity', body.get('issuer')), entity)
                         or same_sec_bound_issuer(document, body, fact, entity, (primary,)))):
                unit = fact.get('unit')
                if isinstance(unit, str) and len(unit.split()[0]) == 3:
                    found.add(unit.split()[0])
    declared = (primary.get('metadata') or {}).get('financial_currency')
    if declared:
        found.add(declared)
    if len(found) != 1 or not next(iter(found)).isascii() or not next(iter(found)).isupper():
        raise ValueError('perimeter: unique primary financial currency required; quote currency is not a substitute')
    return found.pop()


def _has_opening_share_observation(catalog, entity, on, cutoff, sec_filings, *, share_class=None):
    """Require an actual dated count before paying to reconcile its class/basis."""
    from .input_evidence import same_entity_name, same_sec_bound_issuer, opening_share_concepts
    from .input_preparation import _fact_proof, _finite, _source_scale
    from .statement_shares_evidence import (is_statement_shares, normalize_statement_shares,
                                             share_selection_problem)
    concepts = opening_share_concepts() | {
        ('sec_statement_shares_v1', 'CommonStockSharesOutstanding'),
        ('reported-statement', 'CommonStockSharesOutstanding')}
    quoted_class = ' '.join(share_class.split()) if isinstance(share_class, str) else None
    class_specific = bool(quoted_class and re.match(r'Class [A-Z] (?:ordinary shares|common stock)\b',
                                                      quoted_class, re.I))
    for document in catalog.values():
        body = _body(document)
        for index, fact in enumerate(body.get('facts', []) if isinstance(body.get('facts'), list) else []):
            if not isinstance(fact, dict) or (fact.get('taxonomy'), fact.get('concept')) not in concepts:
                continue
            observation = fact.get('observation', fact)
            if not isinstance(observation, dict) or observation.get('end') != on or observation.get('start'):
                continue
            value = observation.get('val') if 'observation' in fact else observation.get('value')
            scale = _source_scale(fact.get('unit'), 'million shares')
            if scale is None or not _finite(value) or value <= 0:
                continue
            if is_statement_shares(document):
                metadata = document.get('metadata') or {}
                source_id = metadata.get('source_document_id')
                source = catalog.get(source_id)
                if (index != 0 or source is None or source not in sec_filings
                        or metadata.get('report_date') != on):
                    continue
                if metadata.get('share_class') != 'Common Stock' and (
                        not quoted_class or quoted_class.casefold() !=
                        ' '.join(str(metadata.get('share_title', '')).split()).casefold()):
                    continue
                normalized = normalize_statement_shares(source, list(catalog.values()))
                if normalized.get('status') != 'ready' or len(normalized.get('documents', [])) != 1:
                    continue
                expected = normalized['documents'][0]
                if any(document.get(key) != expected.get(key) for key in
                       ('id', 'url', 'text', 'sha256', 'document_sha256', 'metadata')):
                    continue
                comparison = body.get('tag_comparison') or {}
                conflicts = comparison.get('conflicts')
                if not isinstance(conflicts, list):
                    continue
                basis = ('primary_inline_without_same_date_tag'
                         if comparison.get('status') == 'missing_same_date_tag' else
                         'primary_statement_over_conflicting_tags' if conflicts else
                         'primary_statement_consistent_with_tags')
                item = _item(value * scale, [document['id']], cutoff, calculation={
                    'type': 'statement_shares', 'fact_index': 0,
                    'selection_basis': basis, 'acknowledged_conflicts': conflicts})
                if (_fact_proof('shares', item, [document], 'million shares', on,
                                expected_entity=entity) is None
                        and share_selection_problem(item, catalog, entity, on,
                                                    share_class or metadata['share_title']) is None):
                    return True
                continue
            if class_specific:
                continue  # An unclassified companyfacts count cannot identify the listed class.
            if document['id'].startswith('xbrl-'):
                if same_sec_bound_issuer(document, body, fact, entity, sec_filings):
                    return True
                continue
            claims = [claim for claim in (fact.get('entity'), body.get('issuer'),
                (document.get('metadata') or {}).get('issuer')) if claim is not None]
            if not claims or not all(same_entity_name(claim, entity) for claim in claims):
                continue
            root = '/facts/' + str(index)
            parent = root + '/observation' if 'observation' in fact else root
            item = _item(value * scale, [document['id']], cutoff, quoted_value=value,
                quoted_unit=fact['unit'], evidence_pointer={
                    'value': parent + ('/val' if 'observation' in fact else '/value'),
                    'unit': root + '/unit', 'period': parent + '/end'})
            if _fact_proof('shares', item, [document, *sec_filings], 'million shares', on,
                           expected_entity=entity) is None:
                return True
    return False


def assemble_fresh_historical(bundle, source_report, *, method_id, as_of):
    """Return a complete historical source plan only after common revalidation."""
    from .input_preparation import _catalog, _day, prepare_method_inputs
    from .preparation_methods import method_schema
    result = deepcopy(source_report)
    result.pop('source_plan', None)
    result.pop('historical_preparation_plan', None)
    result.pop('historical_preparation', None)
    receipt = dict(status='blocked', reasons=[], contract='fresh_historical_sources/1',
        source_origin='current_free_acquisition', forecast_values_reused=False,
        archive_reads='pinned_current_primary_bytes_only_if_required',
        previous_model_archive_reads='none_by_contract', historical_drivers=[], model_conventions=[], driver_gaps=[])
    result['fresh_historical_assembly'] = receipt
    if method_id != 'operating_fcff':
        receipt.update(status='unsupported', reasons=[method_id +
            ': fresh legal/entity-specific bridge not implemented; existing exact archived reproof remains available'])
        return result
    try:
        cutoff = _day(as_of)
        opening = (result.get('selection') or {}).get('opening_date')
        if not cutoff or not _day(opening) or _day(opening) > cutoff:
            raise ValueError('Current collector has no verified economic opening before the information cutoff')
        if result.get('preparation_ready') is not True:
            raise ValueError('Current free acquisition is incomplete; no missing observation may be inferred')
        catalog, issues, _ = _catalog(result.get('documents') or [], cutoff)
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        # The common catalog deliberately strips descriptive origin labels.
        # Restore only that collector label for selection; numerical/source
        # validation still consumes the catalog's rechecked text and metadata.
        for document in result['documents']:
            if document['id'] in catalog and 'origin' in document:
                catalog[document['id']]['origin'] = document['origin']
        primary = catalog[result['selection']['selected_document_id']]
        sec_filings = [catalog[ident] for ident in result['selection'].get('selected_document_ids', [])
                       if ident in catalog]
        entity = (primary.get('metadata') or {}).get('issuer')
        if not isinstance(entity, str) or not entity.strip():
            raise ValueError('perimeter: exact primary issuer identity required')
        profile = bundle['case'].get('info') or {}
        confirmed = profile.get('longName') or profile.get('shortName')
        if not _issuer_key(confirmed) or _issuer_key(entity) != _issuer_key(confirmed):
            try:
                from .quotation_evidence import verify_sec_corporation_name_binding
                listing = catalog['listing-' + primary['id']]
                receipt['issuer_name_binding'] = verify_sec_corporation_name_binding(
                    listing, primary, profile, ticker=bundle['case']['ticker'], on=opening)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                receipt['fatal_identity_mismatch'] = True
                raise ValueError('perimeter: primary issuer differs from the confirmed security profile; '
                    'no verified SEC name binding: ' + str(exc)) from exc
        currency = _currency(catalog, primary, entity, opening)
        if receipt.get('issuer_name_binding') and currency != 'USD':
            raise ValueError('perimeter: SEC corporation binding requires reported USD financial facts')
        plan = dict(model={}, scenarios={name: {} for name in ('bear', 'base', 'bull')},
            scenario_rationale={name: 'Historical observations only; prospective judgment remains for the authorized preparer.'
                                for name in ('bear', 'base', 'bull')})
        try:
            quote = _quotation(catalog, bundle['case']['ticker'], opening, currency, as_of,
                               entity=entity, primary_id=primary['id'])
            plan['model']['quotation'] = quote
        except (ValueError, TypeError, KeyError) as exc:
            receipt['reasons'].append(str(exc))
        share_class = plan['model'].get('quotation', {}).get('value', {}).get('share_class')
        if share_class:
            plan['model']['perimeter'] = _item(dict(entity=entity, currency=currency,
                share_class=share_class), [primary['id']], as_of, historical=False)
        start = _day(opening) + timedelta(days=1)
        periods = []
        for _ in range(10):
            next_start = date(start.year + 1, start.month, min(start.day, monthrange(start.year + 1, start.month)[1]))
            periods.append(dict(start=start.isoformat(), end=(next_start-timedelta(days=1)).isoformat()))
            start = next_start
        plan['model']['calendar'] = _item(dict(valuation_date=opening, periods=periods,
            discount_convention='annual_end'), [primary['id']], as_of, historical=False)
        receipt['model_conventions'] = ['Consolidated issuer / exact listed class',
            'Ten explicit annual forecast periods after the opening; annual-end discount convention; no forecast amounts']
        schema, _ = method_schema(method_id)
        required = {'historical_revenue', 'opening_nwc', 'shares', 'net_debt', 'equity_adjustments'}
        for driver in sorted(required):
            unmapped = False
            try:
                if driver == 'historical_revenue':
                    item = _revenue(catalog, entity, opening, currency, as_of, sec_filings=sec_filings)
                else:
                    candidates = _typed(driver, schema[driver], catalog, entity, opening, currency, as_of)
                    declared = [row for doc in catalog.values() for row in
                        (_body(doc).get('records') if isinstance(_body(doc).get('records'), list) else [])
                        if isinstance(row, dict) and row.get('driver') == driver]
                    unmapped = not candidates and not declared
                    item = _unique(candidates, driver)
                if driver in ('shares', 'historical_revenue') and item['value'] <= 0:
                    raise ValueError(driver + ': positive opening observation required for the operating model')
                if schema[driver][-1] == 'model':
                    plan['model'][driver] = item
                else:
                    for scenario in plan['scenarios'].values():
                        scenario[driver] = deepcopy(item)
                receipt['historical_drivers'].append(driver)
            except (ValueError, TypeError, KeyError) as exc:
                receipt['reasons'].append(str(exc))
                receipt['driver_gaps'].append({'driver': driver,
                    'code': 'unmapped_records' if unmapped else 'invalid_observation'})
        if receipt['reasons']:
            gaps = receipt['driver_gaps']
            if (gaps and len(receipt['reasons']) == len(gaps)
                    and all(row['code'] == 'unmapped_records' and row['driver'] in
                        {'opening_nwc', 'shares', 'net_debt', 'equity_adjustments'} for row in gaps)):
                # Admission permits a bounded attempt to classify existing primary
                # evidence. It never certifies the missing amounts or a model.
                from .input_preparation import has_approved_inputs
                if has_approved_inputs(bundle):
                    raise ValueError('Pending history conflicts with approved inputs; qualify their complete historical proofs separately')
                selected = result['selection'].get('selected_document_ids')
                if (not isinstance(selected, list) or primary['id'] not in selected
                        or any(not isinstance(ident, str) or ident not in catalog for ident in selected)
                        or len(selected) != len(set(selected))):
                    raise ValueError('Historical preparation requires every selected filing in the verified catalog')
                blocking_issues, source_limitations = _historical_source_issues(result, catalog)
                if result.get('source_gaps') or blocking_issues:
                    raise ValueError('Historical preparation requires complete acquired sources; explicit source gaps remain')
                if (result.get('balance_sheet') or {}).get('status') != 'ready':
                    raise ValueError('Historical preparation requires a ready balance acquisition report')
                from .balance_sheet_evidence import normalize_balance_sheet, NORMALIZER
                ledgers = [doc for doc in catalog.values() if (doc.get('metadata') or {}).get('normalizer') == NORMALIZER
                    and doc['metadata'].get('entity') == entity and doc['metadata'].get('report_date') == opening]
                rebuilt = normalize_balance_sheet(primary)
                if (len(ledgers) != 1 or rebuilt.get('status') != 'ready'
                        or any(ledgers[0].get(key) != rebuilt['documents'][0].get(key) for key in
                            ('id', 'text', 'sha256', 'metadata', 'url', 'document_sha256', 'published_at'))):
                    raise ValueError('Historical preparation requires a unique complete balance recompiled from the selected filing')
                if any(row['driver'] == 'shares' for row in gaps) and not _has_opening_share_observation(
                        catalog, entity, opening, as_of, sec_filings, share_class=share_class):
                    raise ValueError('Historical preparation requires a positive same-date outstanding share observation; authorized or weighted-average counts are not opening shares')
                checked = prepare_method_inputs(bundle, documents=list(catalog.values()),
                    propose=lambda *_: deepcopy(plan), source_report=result)
                invalid = [row['reason'] for row in checked['issues'] if row.get('code') != 'missing_driver']
                if invalid:
                    raise ValueError('; '.join(invalid))
                result['historical_preparation_plan'] = plan
                result['historical_preparation'] = {'status': 'required',
                    'contract': 'documented_historical_preparation/1',
                    'source_limitations': source_limitations,
                    'missing_drivers': sorted(row['driver'] for row in gaps),
                    'basis': 'Verified primary, currency, quotation, revenue and complete reported balance; economic classifications remain unqualified'}
            return result
        checked = prepare_method_inputs(bundle, documents=list(catalog.values()),
            propose=lambda *_: deepcopy(plan), source_report=result)
        blocking = [row['reason'] for row in checked['issues'] if row.get('field') in
                    required | {'quotation', 'documents', 'perimeter', 'calendar', 'proposal'}]
        if blocking:
            raise ValueError('; '.join(blocking))
        result['source_plan'] = plan
        result['documents'] = list(catalog.values())
        receipt.update(status='ready', economic_opening_date=opening, information_cutoff=as_of,
            limitation='Only directly supported fresh operating observations. Complex NWC, dilution, debt/equity claims and FX need their explicit reconciliations; no implicit zeros or archived forecasts.')
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        receipt['reasons'].append(str(exc))
    return result
