"""Local statement normalization for an explicit author calendar.

Only existing raw-bound normalizers and proof compilers are used. Their facts
are a readable basis, never an automatic working-capital or EV/equity choice.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _catalog(documents, cutoff):
    from .input_preparation import _catalog as native_catalog
    catalog, issues, provenance = native_catalog(documents, date.fromisoformat(cutoff))
    if issues:
        raise ValueError('Statement catalog cannot be recompiled: ' + repr(issues))
    return catalog, provenance


def _summary(component):
    return {'status': component.get('status'), 'document_ids': [d['id'] for d in component.get('documents', [])],
        'issues': deepcopy(component.get('issues') or [])}


def _view(catalog, primary, context, components, driver_basis, saved_verified, issues):
    from .balance_working_capital import balance_nwc_policy
    from .diluted_share_estimate import diluted_share_policy
    reported_issues = [issue for component in components.values() for issue in component.get('issues', [])]
    view = {'status': 'statement_evidence_only', 'opening_date': context['opening_date'],
        'primary_document_id': primary['id'], 'economic_decisions_applied': False,
        'sources': {key: _summary(value) for key, value in components.items()},
        'driver_basis': deepcopy(driver_basis), 'verified_saved_drivers': saved_verified,
        'missing_driver_basis': [name for name in ('historical_revenue', 'opening_nwc', 'shares',
            'net_debt', 'equity_adjustments') if name not in driver_basis],
        'available_contracts': {}, 'issues': [deepcopy(issue) for issue in issues if issue not in reported_issues],
        'next_actions': [
            'Source IDs and pointers are exact. Saved decisions are unchanged.',
            'NWC: classify every component and nonmonetary item, citing original narrative; no automatic treatment.',
            'Shares: explicitly justify dilution; weighted averages are not an instant denominator.',
            'Net debt/equity: author the signed bridge and exclusions; avoid double counts and assumed zero liabilities.',
            'Explicitly submit/reuse completed drivers.']}
    policy = balance_nwc_policy(list(catalog.values()))
    if policy is not None:
        view['available_contracts']['opening_nwc'] = policy
    view['available_contracts']['shares'] = diluted_share_policy()
    for document in components['balance_sheet'].get('documents', []):
        body = json.loads(document['text'])
        rows = body['components']
        view['reported_balance'] = {'document_id': document['id'], 'sha256': document['sha256'],
            'pointer': '/components', 'total_components': len(rows), 'omitted_components': 0,
            'columns': ['reported_tag', 'value_exact', 'unit', 'accounting_side'],
            'components': [[row.get(key) for key in ('reported_tag', 'value_exact', 'unit', 'accounting_side')]
                for row in rows],
            'row_pointer': 'The zero-based row index maps exactly to /components/<index> in the source JSON.',
            'nonmonetary_disclosures': {'pointer': '/nonmonetary_disclosures',
                'count': len(body.get('nonmonetary_disclosures') or []),
                'items': [{'index': index, **{key: row.get(key) for key in ('reported_tag', 'label')}}
                    for index, row in enumerate(body.get('nonmonetary_disclosures') or [])]},
            'classification_approved': False}
    for document in components['statement_shares'].get('documents', []):
        body = json.loads(document['text'])
        view['reported_shares'] = {'document_id': document['id'], 'sha256': document['sha256'],
            'facts': [{'pointer': '/facts/' + str(index), **deepcopy(row)}
                for index, row in enumerate(body.get('facts') or []) if row.get('end') == context['opening_date']],
            'tag_comparison': deepcopy(body.get('tag_comparison')),
            'reported_precision': deepcopy(body.get('reported_precision')),
            'limitation': 'Observed outstanding class/count only; dilution and economic adoption remain unapproved.'}
    from .diluted_share_estimate import _CONCEPTS
    observed_shares = []
    for document in components['companyfacts'].get('documents', []):
        for index, fact in enumerate(json.loads(document['text']).get('facts', [])):
            observation = fact.get('observation') or {}
            if observation.get('end') != context['opening_date'] or fact.get('unit') != 'shares':
                continue
            for role, concepts in _CONCEPTS.items():
                if (fact.get('taxonomy'), fact.get('concept')) not in concepts:
                    continue
                if (role == 'outstanding') != ('start' not in observation):
                    continue
                observed_shares.append({'role': role, 'document_id': document['id'],
                    'pointer': '/facts/' + str(index), 'reported_value': observation.get('val'),
                    'unit': fact['unit'], 'start': observation.get('start'), 'end': observation['end'],
                    'concept': fact['concept']})
    view['reported_share_observations'] = {'facts': observed_shares,
        'limitation': 'Raw same-filing observations. Outstanding is an instant; basic/diluted are weighted averages. No diluted denominator or proxy is selected.'}
    # Tool callers receive this bounded view, not the private extraction packets.
    balance = view.get('reported_balance') or {}
    while len(json.dumps(view, ensure_ascii=False)) > 8950 and balance.get('components'):
        balance['components'].pop()
        balance['omitted_components'] += 1
    if len(json.dumps(view, ensure_ascii=False)) > 9000:
        raise ValueError('Statement evidence view exceeds its explicit local read limit; use document paging')
    return view


def _companyfacts(cik, existing, fetch):
    """Pin the native JSON response; replay never consults mutable cache/network."""
    if existing is not None:
        pinned = deepcopy(existing.get('companyfacts'))
        if pinned is None:
            return None
        if _digest(pinned['response']) != pinned['sha256'] or pinned['cik'] != cik:
            raise ValueError('Pinned SEC companyfacts response or issuer changed')
        return pinned
    if fetch is None:
        from pathlib import Path
        from bellomberg.market_data.sec_xbrl import CACHE_DIR
        path = Path(CACHE_DIR) / ('CIK' + cik + '.json')
        if not path.is_file():
            return None
        response = json.loads(path.read_text(encoding='utf8'))
    else:
        response = fetch(cik)
    if not isinstance(response, dict) or str(response.get('cik', '')).zfill(10) != cik:
        raise ValueError('Local SEC companyfacts response unavailable or wrong issuer')
    # Checkpoints sort object keys. Normalize new responses in that same order
    # before native collection assigns stable /facts/N locators.
    response = json.loads(json.dumps(response, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return {'cik': cik, 'response': deepcopy(response), 'sha256': _digest(response),
        'source': 'SEC_companyfacts_native_cached_response'}


def _preserve_companyfacts_order(report, existing):
    """Reprove every legacy fact, then keep its paid text and array indices."""
    if existing is None:
        return report
    fresh = report.get('documents') or []
    saved = [row for row in existing.get('documents', []) if str(row.get('id', '')).startswith('xbrl-')]
    by_id = {row['id']: row for row in saved}
    if (len(by_id) != len(saved) or len(fresh) != len(saved)
            or set(by_id) != {row['id'] for row in fresh}):
        raise ValueError('Pinned SEC companyfacts document set differs from native reproof')
    proven = []
    for current in fresh:
        prior = by_id[current['id']]
        if (not isinstance(prior.get('text'), str)
                or sha256(prior['text'].encode()).hexdigest() != prior.get('sha256')
                or {k:v for k,v in prior.items() if k not in ('text', 'sha256')} !=
                   {k:v for k,v in current.items() if k not in ('text', 'sha256')}):
            raise ValueError('Pinned SEC companyfacts source identity or text seal differs')
        old_body, new_body = json.loads(prior['text']), json.loads(current['text'])
        old_facts, new_facts = old_body.pop('facts', None), new_body.pop('facts', None)
        canonical = lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(',', ':'))
        if (old_body != new_body or not isinstance(old_facts, list) or not isinstance(new_facts, list)
                or sorted(map(canonical, old_facts)) != sorted(map(canonical, new_facts))):
            raise ValueError('Pinned SEC companyfacts observations differ from native reproof')
        proven.append(deepcopy(prior))
    return {**report, 'documents': proven}


def statement_basis(qualification, plan, *, archive_root, existing=None, companyfacts_fetch=None):
    """Recompile available facts; return private receipt plus a bounded public view."""
    from .author_quotation import _context
    from .balance_sheet_evidence import collect_balance_sheet
    from .balance_detail_evidence import collect_balance_details
    from .statement_table_evidence import collect_statement_tables
    from .statement_shares_evidence import normalize_statement_shares
    from .preparation_fresh_historical import _revenue
    from .valuation_sources import company_facts_documents
    from .balance_detail_evidence import _same_sec_inline_issuer
    try:
        quote_context, _listing, originals, currency, entity = _context(qualification, plan, archive_root)
        context = {**quote_context, 'policy': 'author_statement_evidence/1'}
        if existing is not None and (existing.get('context') != context or existing.get('sha256') !=
                _digest({key: value for key, value in existing.items() if key != 'sha256'})):
            raise ValueError('Saved statement receipt or its author/source context changed')
        primary = deepcopy(next(doc for doc in originals if doc['id'] == context['primary_document_id']))
        cik = primary['metadata']['emittente_id'].removeprefix('CIK:')
        companyfacts = _companyfacts(cik, existing, companyfacts_fetch)
        if companyfacts is not None and not _same_sec_inline_issuer(
                companyfacts['response'].get('entityName'), primary['metadata']['issuer']):
            raise ValueError('SEC companyfacts issuer differs from the verified primary')
        packets = {}
        components = {}
        components['companyfacts'] = (company_facts_documents([primary], as_of=context['as_of'],
            fetch=lambda _cik: deepcopy(companyfacts['response'])) if companyfacts is not None else
            {'status': 'incomplete', 'documents': [], 'issues': [{'source': 'SEC companyfacts',
             'reason': 'Local same-filing companyfacts response absent; no network or substituted filing requested'}]})
        components['companyfacts'] = _preserve_companyfacts_order(components['companyfacts'], existing)
        components['statement_tables'] = collect_statement_tables([primary], archive_root)
        packet = components['statement_tables'].get('packets', {}).get(primary['id'])
        if packet is not None:
            primary['statement_table_fields'] = packets['statement_table_fields'] = deepcopy(packet)
        components['balance_details'] = collect_balance_details(primary, archive_root)
        packet = components['balance_details'].get('packets', {}).get(primary['id'])
        if packet is not None:
            primary['balance_detail_fields'] = packets['balance_detail_fields'] = deepcopy(packet)
        components['balance_sheet'] = collect_balance_sheet(primary, archive_root)
        for output, field in (('packets', 'balance_sheet_fields'), ('detail_packets', 'balance_detail_fields'),
                              ('statement_packets', 'statement_table_fields')):
            packet = components['balance_sheet'].get(output, {}).get(primary['id'])
            if packet is not None:
                primary[field] = packets[field] = deepcopy(packet)
        components['statement_shares'] = normalize_statement_shares(primary,
            [*originals, *components['companyfacts'].get('documents', [])])
        documents = [deepcopy(doc) for component in components.values() for doc in component.get('documents', [])]
        for document in components['balance_sheet'].get('documents', []):
            facts = json.loads(document['text'])['components']
            if any(row.get('unit') != currency or row.get('end') != context['opening_date'] for row in facts):
                raise ValueError('Reported balance currency/date differs from the saved author perimeter/calendar')
        original_map = {doc['id']: deepcopy(doc) for doc in originals}
        original_map[primary['id']] = primary
        for doc in documents:
            if doc['id'] in original_map and any(original_map[doc['id']].get(key) != doc.get(key)
                    for key in ('text', 'sha256', 'document_sha256', 'metadata', 'url', 'published_at')):
                raise ValueError('Normalized statement would replace existing evidence: ' + doc['id'])
            original_map[doc['id']] = doc
        catalog, _provenance = _catalog(list(original_map.values()), context['as_of'])
        issues = [deepcopy(issue) for component in components.values() for issue in component.get('issues', [])]
        driver_basis, saved_verified = {}, []
        try:
            driver_basis['historical_revenue'] = _revenue(catalog, entity, context['opening_date'], currency,
                context['as_of'], sec_filings=[catalog[primary['id']]])
        except (ValueError, KeyError, TypeError) as exc:
            # The receipt binds source bytes and calendar, not mutable author
            # choices. Submitted observations remain the compiler's concern.
            issues.append({'source': 'historical_revenue', 'reason': str(exc)})
        view = _view(catalog, primary, context, components, driver_basis, saved_verified, issues)
        status = ('partial' if issues else 'ready') if documents else 'incomplete'
        receipt = {'context': context, 'companyfacts': companyfacts, 'primary_additions': packets, 'documents': documents,
            'view': view, 'status': status}
        receipt['sha256'] = _digest(receipt)
        if existing is not None and existing != receipt:
            raise ValueError('Saved statement proof differs from the same recompiled original bytes')
        return {'status': status, 'view': view, 'documents': deepcopy(documents), 'receipt': receipt, 'issues': issues}
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as exc:
        issues = [{'source': 'author statement evidence', 'reason': type(exc).__name__ + ': ' + str(exc)}]
        return {'status': 'incomplete', 'documents': [], 'issues': issues,
            'view': {'status': 'incomplete', 'economic_decisions_applied': False, 'driver_basis': {},
                     'available_contracts': {}, 'sources': {}, 'issues': issues}}


def merge_statement_evidence(qualification, plan, receipt, *, archive_root, overlay=None):
    """Add reverified normalized facts, optionally after a separately verified quote."""
    from .trade_idea_model import source_fingerprint
    result = statement_basis(qualification, plan, archive_root=archive_root, existing=receipt)
    if not result.get('receipt'):
        raise ValueError('Saved statement evidence cannot be reverified: ' + repr(result['issues']))
    base = deepcopy(qualification if overlay is None else overlay)
    if overlay is not None:
        if overlay.get('fingerprint') != source_fingerprint(overlay):
            raise ValueError('Composed quotation overlay fingerprint differs')
        def unchanged(value):
            copy = deepcopy(value)
            copy.pop('fingerprint', None)
            report = copy.get('source_report') or {}
            report.pop('documents', None)
            report.pop('derived_quotation', None)
            (copy.get('coverage') or {}).pop('document_provenance', None)
            return copy
        if unchanged(qualification) != unchanged(overlay):
            raise ValueError('Composed evidence changed the accepted issuer, grant or research context')
    # The native catalog makes optional fields explicit and drops presentation
    # labels; compare its verified form on both sides, never raw dict shape.
    documents, _ = _catalog(base['source_report']['documents'], qualification['as_of'])
    originals, _ = _catalog(qualification['source_report']['documents'], qualification['as_of'])
    for original in originals.values():
        if documents.get(original['id']) != original:
            raise ValueError('Composed evidence changed an original document')
    primary_id = receipt['context']['primary_document_id']
    documents[primary_id].update(deepcopy(receipt['primary_additions']))
    for doc in result['documents']:
        previous = documents.get(doc['id'])
        if previous is not None and previous != doc:
            raise ValueError('Composed statement document conflicts with existing evidence')
        documents[doc['id']] = deepcopy(doc)
    catalog, provenance = _catalog(list(documents.values()), qualification['as_of'])
    base['source_report']['documents'] = list(catalog.values())
    base['source_report']['selection'] = {'policy': 'explicit_author_calendar/1',
        'opening_date': receipt['context']['opening_date'], 'selected_document_id': primary_id,
        'selected_document_ids': [primary_id]}
    base['source_report']['derived_statements'] = {'receipt_sha256': receipt['sha256'],
        'parent_source_fingerprint': qualification['fingerprint'],
        'status': result['status'], 'economic_decisions_applied': False}
    base.setdefault('coverage', {})['document_provenance'] = provenance
    base['fingerprint'] = source_fingerprint(base)
    return base
