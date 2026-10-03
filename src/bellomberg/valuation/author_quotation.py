"""Exact historical quotation evidence for an explicit, still-authored plan.

The accepted source grant and the draft are never modified. The same native
listing/price proof compiler serves author reads and final model preparation.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode('utf8')).hexdigest()


def _context(qualification, plan, archive_root):
    from .trade_idea_model import source_fingerprint
    from .preparation_fresh_historical import _issuer_key
    from .quotation_evidence import listing_identity_document, verify_sec_listing, sec_us_weekend_quote_basis

    if qualification.get('fingerprint') != source_fingerprint(qualification):
        raise ValueError('Accepted source fingerprint differs')
    model = plan.get('model') if isinstance(plan, dict) else None
    calendar = ((model or {}).get('calendar') or {}).get('value')
    perimeter = ((model or {}).get('perimeter') or {}).get('value')
    if not isinstance(calendar, dict) or not isinstance(perimeter, dict):
        raise ValueError('Save explicit model.calendar and model.perimeter before requesting quotation')
    on, entity, currency = calendar.get('valuation_date'), perimeter.get('entity'), perimeter.get('currency')
    if (not isinstance(on, str) or date.fromisoformat(on).isoformat() != on
            or on > qualification['as_of'] or not entity or not currency):
        raise ValueError('Exact author valuation date, issuer and financial currency required')
    ticker = qualification['ticker']
    identity = qualification.get('identity') or {}
    quote_currency = identity.get('currency')
    if not quote_currency or not identity.get('exchange'):
        raise ValueError('Accepted quotation currency and exchange required; no account-currency fallback')
    documents = (qualification.get('source_report') or {}).get('documents') or []
    primaries = [doc for doc in documents if doc.get('document_sha256')
        and (doc.get('metadata') or {}).get('form') in ('10-K', '10-Q', '20-F', '6-K')
        and (doc.get('metadata') or {}).get('report_date') == on
        and _issuer_key((doc.get('metadata') or {}).get('issuer')) == _issuer_key(entity)
        and not (doc.get('metadata') or {}).get('normalizer')]
    selection = (qualification.get('source_report') or {}).get('selection') or {}
    if selection.get('opening_date') == on and selection.get('selected_document_id'):
        primaries = [doc for doc in primaries if doc['id'] == selection['selected_document_id']]
    if len(primaries) != 1:
        raise ValueError('One admitted primary for the exact author issuer/date is required; ambiguous filings need explicit selection')
    primary = primaries[0]
    root = Path(archive_root).resolve()
    path = Path(primary.get('archive_path') or '').resolve()
    if not primary.get('archive_path') or not path.is_relative_to(root):
        raise ValueError('Original listing bytes require their verified path inside the explicit archive')
    raw = path.read_bytes()
    listing_report = listing_identity_document(primary, raw, ticker=ticker, on=on)
    if listing_report.get('status') != 'ready' or len(listing_report.get('documents') or []) != 1:
        raise ValueError('Original ordinary listing is incomplete: ' + repr(listing_report.get('issues')))
    listing = verify_sec_listing(listing_report['documents'][0], primary, ticker=ticker, on=on)
    quote_on, date_basis = on, None
    if date.fromisoformat(on).weekday() >= 5:
        if currency != 'USD' or quote_currency != 'USD':
            raise ValueError('Weekend quote needs the existing proved USD exchange policy; no implicit prior close')
        quote_on, date_basis = sec_us_weekend_quote_basis(listing, primary, ticker=ticker, opening_date=on)
    context = {'policy': 'author_exact_quotation/1', 'source_fingerprint': qualification['fingerprint'],
        'ticker': ticker, 'as_of': qualification['as_of'], 'calendar_sha256': _digest(calendar),
        'perimeter_sha256': _digest(perimeter), 'opening_date': on, 'quote_date': quote_on,
        'primary_document_id': primary['id'], 'primary_raw_sha256': primary['document_sha256'],
        'primary_text_sha256': primary['sha256'], 'listing_sha256': listing['sha256'],
        'listing_policy': 'SEC_listing_unit_identity/1', 'date_basis': date_basis,
        'exchange': identity['exchange'], 'quote_currency': quote_currency}
    return context, listing, documents, currency, entity


def _catalog_with(qualification, documents):
    from .input_preparation import _catalog
    by_id = {row['id']: deepcopy(row) for row in qualification['source_report']['documents']}
    for row in documents:
        previous = by_id.get(row['id'])
        if previous is not None and any(previous.get(key) != row.get(key) for key in
                ('url', 'published_at', 'text', 'sha256', 'document_sha256', 'metadata')):
            raise ValueError('Derived quotation would replace an admitted document: ' + row['id'])
        by_id.setdefault(row['id'], deepcopy(row))
    catalog, issues, provenance = _catalog(list(by_id.values()), date.fromisoformat(qualification['as_of']))
    if issues:
        raise ValueError('Derived quotation catalog rejected: ' + '; '.join(row['reason'] for row in issues))
    return catalog, provenance


def quotation_basis(qualification, plan, *, archive_root, price_fetch=None, existing=None, allow_acquire=True):
    """Return an observed basis; adoption remains an explicit author action."""
    from .quotation_evidence import cached_historical_quote_document
    from .preparation_fresh_historical import _quotation

    try:
        context, listing, _documents, currency, entity = _context(qualification, plan, archive_root)
        if existing is not None:
            if (not isinstance(existing, dict) or existing.get('context') != context
                    or existing.get('sha256') != _digest({k: v for k, v in existing.items() if k != 'sha256'})):
                raise ValueError('Pinned author quotation receipt or its context changed')
        quote = cached_historical_quote_document(context['ticker'], on=context['quote_date'],
            as_of=context['as_of'], archive_root=archive_root, context={key: value for key, value in context.items()
                if key != 'source_fingerprint'}, quote_currency=context['quote_currency'], fetch=price_fetch,
            existing=existing.get('quote_cache') if existing else None, allow_acquire=allow_acquire)
        if quote.get('status') != 'ready':
            return quote
        documents = [deepcopy(listing), *deepcopy(quote['documents'])]
        catalog, _provenance = _catalog_with(qualification, documents)
        driver = _quotation(catalog, context['ticker'], context['opening_date'], currency,
            context['as_of'], entity=entity, primary_id=context['primary_document_id'])
        receipt = {'context': context, 'driver': driver, 'documents': documents,
            'quote_cache': deepcopy(quote['cache_receipt'])}
        receipt['sha256'] = _digest(receipt)
        if existing is not None and receipt != existing:
            raise ValueError('Derived quotation differs from its saved native proof')
        return {'status': 'ready', 'driver': deepcopy(driver), 'documents': deepcopy(documents),
            'receipt': receipt, 'issues': [], 'instruction':
                'Observed historical quotation, not an adopted assumption. Read its document_ids, then explicitly submit this native driver or its exact reuse reference; no draft value was filled.'}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [
            {'source': 'author historical quotation', 'reason': type(exc).__name__ + ': ' + str(exc)}],
            'instruction': 'No substitute price, date, currency or share ratio was supplied. Correct the explicit source/date contract or request quotation again after a transient read failure.'}


def merge_quotation_evidence(qualification, plan, receipt, *, archive_root):
    """Reprove the pinned overlay without transport or changing the source grant."""
    from .trade_idea_model import source_fingerprint
    checked = quotation_basis(qualification, plan, archive_root=archive_root,
        existing=receipt, allow_acquire=False)
    if checked['status'] != 'ready':
        raise ValueError('Saved quotation evidence cannot be verified: ' + repr(checked['issues']))
    catalog, provenance = _catalog_with(qualification, checked['documents'])
    result = deepcopy(qualification)
    result['source_report']['documents'] = list(catalog.values())
    result['source_report']['derived_quotation'] = {'policy': 'author_exact_quotation/1',
        'receipt_sha256': receipt['sha256'], 'parent_source_fingerprint': qualification['fingerprint']}
    result.setdefault('coverage', {})['document_provenance'] = provenance
    result['fingerprint'] = source_fingerprint(result)
    return result


def board_quotation_view(blackboard):
    """Local read/validation view; accepted and paid-context fingerprints stay put."""
    qualification = blackboard.source_qualification
    state = blackboard.data.get('_author_quotation') or {}
    if not state.get('active_sha256'):
        return qualification
    receipt = next((row for row in state.get('receipts', [])
        if row.get('sha256') == state['active_sha256']), None)
    if receipt is None:
        raise ValueError('Pinned author quotation receipt is missing')
    if (blackboard.data.get('_source_research') or {}).get('qualified_fingerprint') == qualification['fingerprint']:
        return qualification  # Final qualification has already recompiled this receipt.
    return merge_quotation_evidence(qualification, blackboard.data.get('_model_input_draft'), receipt,
        archive_root=(blackboard.data.get('_source_research') or {})['archive_root'])


def acquire_board_quotation(blackboard):
    """Serve the ordinary input tool; never submit or approve the observed driver."""
    qualification = blackboard.source_qualification
    plan = blackboard.data.get('_model_input_draft')
    basis = blackboard.data.get('_model_input_basis') or {}
    if (basis.get('plan', {}).get('model', {}).get('quotation')
            and not basis.get('quotation_receipt_sha256')):
        return {'status': 'existing_archived_basis', 'issues': [],
            'instruction': 'The admitted input basis already contains quotation; no new price was acquired.'}
    root = (blackboard.data.get('_source_research') or {}).get('archive_root')
    if not root:
        return {'status': 'incomplete', 'issues': [{'source': 'author historical quotation',
            'reason': 'No explicit run-bound source archive is available'}]}
    state = blackboard.data.setdefault('_author_quotation', {'receipts': []})
    try:
        context, *_ = _context(qualification, plan, root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'status': 'incomplete', 'issues': [{'source': 'author historical quotation', 'reason': str(exc)}]}
    existing = next((row for row in state['receipts'] if row.get('context') == context), None)
    result = quotation_basis(qualification, plan, archive_root=root,
        price_fetch=getattr(blackboard, 'quotation_price_fetch', None), existing=existing)
    if result['status'] == 'ready':
        receipt = result['receipt']
        if existing is None:
            state['receipts'].append(deepcopy(receipt))
        state['active_sha256'] = receipt['sha256']
        basis = blackboard.data.setdefault('_model_input_basis', {'plan': {}})
        basis.setdefault('plan', {}).setdefault('model', {})['quotation'] = deepcopy(result['driver'])
        basis['quotation_receipt_sha256'] = receipt['sha256']
        basis['provenance'] = ('Archived input basis plus a separately sealed exact historical quotation. '
            'The accepted source grant and saved draft are unchanged; explicit author adoption is required.')
        persist = getattr(blackboard, 'persist_run_checkpoint', None)
        if callable(persist):
            persist('author_quotation_basis_observed')
    return result
