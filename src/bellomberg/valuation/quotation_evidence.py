"""Explicit listing-unit identity and dated unadjusted price observations."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path
import json
import re
from urllib.parse import quote


NASDAQ_WEEKEND_PRICE_POLICY = 'nasdaq_weekend_previous_friday/1'
NASDAQ_WEEKEND_CALENDAR_SOURCE = 'https://www.nasdaq.com/market-activity/stock-market-holiday-schedule'
NYSE_WEEKEND_PRICE_POLICY = 'nyse_weekend_previous_friday/1'
NYSE_WEEKEND_CALENDAR_SOURCE = 'https://www.nyse.com/trade/hours-calendars'
_NASDAQ_MARKETS = frozenset({'The Nasdaq Global Select Market', 'The Nasdaq Global Market',
                             'The Nasdaq Capital Market'})
_NYSE_MARKETS = frozenset({'New York Stock Exchange', 'The New York Stock Exchange'})
_WEEKEND_POLICIES = {NASDAQ_WEEKEND_PRICE_POLICY: _NASDAQ_MARKETS,
                     NYSE_WEEKEND_PRICE_POLICY: _NYSE_MARKETS}


def _weekend_friday(opening_date):
    try:
        opening = date.fromisoformat(opening_date) if isinstance(opening_date, str) else None
    except ValueError:
        opening = None
    if opening is None or opening.isoformat() != opening_date or opening.weekday() not in (5, 6):
        raise ValueError('US exchange prior close requires an exact Saturday or Sunday accounting date')
    return (opening - timedelta(days=opening.weekday() - 4)).isoformat()


def validate_quote_date_basis(value, opening_date):
    """Check the explicit date exception; ordinary same-day contracts stay strict."""
    if not isinstance(value, dict):
        raise ValueError('Quotation contract required')
    if 'price_date_basis' not in value:
        if value.get('price_as_of') != opening_date:
            raise ValueError('Price and opening balances require the same date without an explicit nontrading-day basis')
        return None
    basis = value['price_date_basis']
    if (not isinstance(basis, dict) or set(basis) != {
            'policy', 'accounting_date', 'exchange', 'listing_document_id'}
            or not isinstance(basis['policy'], str) or basis['policy'] not in _WEEKEND_POLICIES
            or basis['accounting_date'] != opening_date
            or not isinstance(basis['exchange'], str)
            or basis['exchange'] not in _WEEKEND_POLICIES[basis['policy']]
            or not isinstance(basis['listing_document_id'], str)
            or not re.fullmatch(r'listing-[0-9a-f]{64}', basis['listing_document_id'])):
        raise ValueError('Explicit exchange weekend price-date basis is incomplete or unsupported')
    if value.get('price_as_of') != _weekend_friday(opening_date):
        raise ValueError('Weekend accounting date requires the real immediately preceding Friday close')
    if (any(value.get(key) != 'USD' for key in ('financial_currency', 'quote_currency', 'quote_unit'))
            or any(value.get(key) != 1 for key in
                   ('financial_to_quote_rate', 'quote_units_per_currency', 'shares_per_quote'))):
        raise ValueError('Weekend exception supports only one USD ordinary share without an FX or unit bridge')
    return None


def sec_us_weekend_quote_basis(listing, primary, *, ticker, opening_date):
    """Derive the prior Friday from one verified Nasdaq or NYSE ordinary class."""
    quote_date = _weekend_friday(opening_date)
    from .preparation_exhibits import _sec_parts
    if not isinstance(primary, dict):
        raise ValueError('Original SEC primary required for weekend price basis')
    cik, accession, _ = _sec_parts(primary.get('url'))
    metadata = primary.get('metadata') or {}
    if (primary.get('id') != primary.get('document_sha256')
            or metadata.get('emittente_id') != 'CIK:' + cik.zfill(10)
            or metadata.get('accession', '').replace('-', '') != accession
            or metadata.get('form') not in ('10-Q', '10-K')
            or metadata.get('report_date') != opening_date):
        raise ValueError('Weekend price basis requires the exact SEC opening filing identity')
    proven = verify_sec_listing(listing, primary, ticker=ticker, on=opening_date)
    identity = json.loads(proven['text'])['listing']
    exchange = identity['exchange']
    policies = [policy for policy, markets in _WEEKEND_POLICIES.items() if exchange in markets]
    if len(policies) != 1:
        raise ValueError('Weekend price basis requires an SEC-proved Nasdaq or NYSE ordinary listing')
    return quote_date, {'policy': policies[0],
                        'accounting_date': opening_date, 'exchange': exchange,
                        'listing_document_id': proven['id']}


def verify_sec_us_weekend_quote_basis(value, listing, primary, *, ticker, opening_date):
    """Rebind the optional date exception to the same original filing and class."""
    validate_quote_date_basis(value, opening_date)
    if not isinstance(value, dict) or 'price_date_basis' not in value:
        raise ValueError('Explicit weekend price-date basis required')
    quote_date, expected = sec_us_weekend_quote_basis(
        listing, primary, ticker=ticker, opening_date=opening_date)
    if value['price_date_basis'] != expected or value.get('share_class') != json.loads(listing['text'])['listing']['title']:
        raise ValueError('Weekend price basis differs from the SEC-proved listed share class')
    return quote_date


def nasdaq_weekend_quote_basis(listing, primary, *, ticker, opening_date):
    """Preserve the original Nasdaq-only contract for existing callers."""
    quote_date, basis = sec_us_weekend_quote_basis(listing, primary, ticker=ticker, opening_date=opening_date)
    if basis['policy'] != NASDAQ_WEEKEND_PRICE_POLICY:
        raise ValueError('Nasdaq-only price basis requires an SEC-proved Nasdaq listing')
    return quote_date, basis


def verify_nasdaq_weekend_quote_basis(value, listing, primary, *, ticker, opening_date):
    quote_date = verify_sec_us_weekend_quote_basis(value, listing, primary, ticker=ticker, opening_date=opening_date)
    if value['price_date_basis']['policy'] != NASDAQ_WEEKEND_PRICE_POLICY:
        raise ValueError('Nasdaq-only price basis requires the Nasdaq policy')
    return quote_date


def verify_sec_corporation_name_binding(listing, primary, profile, *, ticker, on):
    """Bind only SEC `Corp.` to profile `Corporation` for one proved Nasdaq class."""
    from bs4 import BeautifulSoup
    from .preparation_exhibits import _sec_parts

    if not isinstance(profile, dict) or not isinstance(primary, dict):
        raise ValueError('SEC primary and confirmed public profile required')
    sec_name = (primary.get('metadata') or {}).get('issuer')
    profile_name = profile.get('longName') or profile.get('shortName')
    suffix = re.compile(r'(?P<stem>.+?)\s+(?P<suffix>corp\.?|corporation\.?)$', re.I)
    sec_match = suffix.fullmatch(sec_name) if isinstance(sec_name, str) else None
    profile_match = suffix.fullmatch(profile_name) if isinstance(profile_name, str) else None
    if (not sec_match or not profile_match
            or sec_match['stem'].casefold() != profile_match['stem'].casefold()
            or sec_match['suffix'].rstrip('.').casefold() != 'corp'
            or profile_match['suffix'].rstrip('.').casefold() != 'corporation'):
        raise ValueError('Only an exact legal stem with terminal SEC Corp./profile Corporation is supported')
    if (profile.get('symbol') != ticker or profile.get('fullExchangeName') != 'NasdaqGS'
            or profile.get('currency') != 'USD'
            or profile.get('financialCurrency', 'USD') != 'USD'):
        raise ValueError('Confirmed NasdaqGS ticker and USD public profile required')
    cik, accession, _ = _sec_parts(primary.get('url'))
    metadata = primary.get('metadata') or {}
    digest = primary.get('document_sha256')
    if (not isinstance(digest, str) or primary.get('id') != digest
            or metadata.get('emittente_id') != 'CIK:' + cik.zfill(10)
            or str(metadata.get('accession') or '').replace('-', '') != accession
            or metadata.get('form') not in ('10-Q', '10-K')
            or metadata.get('report_date') != on):
        raise ValueError('SEC filing CIK, accession, form, raw hash or accounting date differs')
    proven = verify_sec_listing(listing, primary, ticker=ticker, on=on)
    sec_exchange = json.loads(proven['text'])['listing']['exchange']
    if sec_exchange != 'The Nasdaq Global Select Market':
        raise ValueError('SEC listing is not the Nasdaq Global Select Market class confirmed by NasdaqGS')
    archive_path = primary.get('archive_path')
    try:
        raw = Path(archive_path).read_bytes() if archive_path is not None else primary['text'].encode('utf8')
    except (OSError, KeyError, AttributeError) as exc:
        raise ValueError('Original SEC raw bytes unavailable for CIK binding') from exc
    if sha256(raw).hexdigest() != digest:
        raise ValueError('Original SEC raw bytes changed during CIK binding')
    soup = BeautifulSoup(raw, 'html.parser')
    raw_ciks = {node.get_text(' ', strip=True) for node in soup.find_all('ix:nonnumeric')
                if node.get('name') == 'dei:EntityCentralIndexKey'}
    if (len(raw_ciks) != 1 or not all(re.fullmatch(r'[0-9]{1,10}', value) for value in raw_ciks)
            or next(iter(raw_ciks)).zfill(10) != cik.zfill(10)):
        raise ValueError('Raw SEC central index key differs from filing URL and metadata')
    return {'policy': 'sec_nasdaq_corporation_suffix/1', 'sec_issuer': sec_name,
            'profile_name': profile_name, 'cik': cik.zfill(10),
            'ticker': ticker, 'opening_date': on,
            'primary_document_id': primary['id'], 'primary_raw_sha256': digest,
            'listing_document_id': proven['id'], 'sec_exchange': sec_exchange,
            'profile_exchange': 'NasdaqGS', 'quote_currency': 'USD'}


def listing_identity(raw, ticker):
    """An ordinary share maps 1:1 only within the same verified listing class.

    ADRs, preferred shares, debt, units and ambiguous cover rows do not receive a
    ratio. This is a disclosed unit identity, never a guessed economic input.
    """
    from bs4 import BeautifulSoup
    if isinstance(raw, (bytes, bytearray)) and raw.startswith(b'%PDF-'):
        return {'status': 'incomplete', 'reason':
            'PDF statements do not prove an inline HTML listing class; independent listing evidence required'}
    soup = BeautifulSoup(raw, "html.parser")
    contexts = {}
    names = {"dei:Security12bTitle": "title", "dei:TradingSymbol": "symbol",
             "dei:SecurityExchangeName": "exchange"}
    for node in soup.find_all("ix:nonnumeric"):
        key, context = names.get(node.get("name")), node.get("contextref")
        if key and context:
            contexts.setdefault(context, {}).setdefault(key, set()).add(node.get_text(" ", strip=True))
    matches = []
    for context, fields in contexts.items():
        if set(fields) != set(names.values()) or any(len(v) != 1 for v in fields.values()):
            continue
        item = {key: next(iter(values)) for key, values in fields.items()}
        if item["symbol"] != ticker or not item["exchange"]:
            continue
        title = item["title"].lower()
        if (not re.search(r"\b(common (?:stock|shares)|ordinary shares)\b", title)
            or re.search(r"\b(depositary|depository|adr|ads|preferred|preference|units|notes|bonds)\b", title)):
            continue
        matches.append({**item, "context_id": context})
    if len(matches) != 1:
        return {"status": "incomplete", "reason": "ordinary listing class missing or ambiguous; ratio requires evidence"}
    return {"status": "verified", **matches[0], "shares_per_quote": 1,
            "basis": "one_listed_ordinary_share_is_one_share_of_the_same_class"}


def historical_quote_document(ticker, *, on, as_of, fetch=None, include_identity=False):
    try:
        day = date.fromisoformat(on)
        if day > date.fromisoformat(as_of):
            raise ValueError("price date after information cutoff")
        if fetch is None:
            import yfinance as yf
            def fetch(symbol, target):
                instrument = yf.Ticker(symbol)
                rows = instrument.history(start=target, end=(date.fromisoformat(target) + timedelta(days=1)).isoformat(),
                                          auto_adjust=False, actions=False, raise_errors=True)
                metadata = instrument.history_metadata
                if len(rows) != 1:
                    raise ValueError("no unique unadjusted close for requested date")
                observed = {"symbol": metadata.get("symbol"), "currency": metadata.get("currency"),
                            "date": rows.index[0].date().isoformat(), "close": float(rows.iloc[0]["Close"])}
                if include_identity:
                    observed['identity'] = {key: metadata.get(key) for key in
                        ('symbol', 'longName', 'exchangeName', 'fullExchangeName', 'instrumentType', 'currency')}
                    observed['identity_observed_on'] = datetime.now(timezone.utc).date().isoformat()
                return observed
        raw = fetch(ticker, on)
        value, currency = raw.get("close"), raw.get("currency")
        if raw.get("symbol") != ticker or raw.get("date") != on:
            raise ValueError("price identity/date differs from request")
        if type(value) not in (int, float) or not isfinite(value) or value <= 0:
            raise ValueError("observed close unavailable")
        if not isinstance(currency, str) or not (re.fullmatch("[A-Z]{3}", currency) or currency == "GBp"):
            raise ValueError("observed quote currency unavailable")
        text = json.dumps({"symbol": ticker, "observation": raw,
                           "facts": [{"value": value, "unit": currency + " per share", "end": on}]},
                          ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        document = {"id": "price-" + ticker + "-" + on, "published_at": on,
                    "url": "https://finance.yahoo.com/quote/" + quote(ticker, safe="") + "/history/",
                    "text": text, "sha256": sha256(text.encode()).hexdigest(),
                    "origin": "yfinance_unadjusted_close", "metadata": {"ticker": ticker, "price_as_of": on},
                    "extraction_coverage": {"status": "single_unadjusted_close", "missing_day_policy": "no_fallback"}}
        return {"status": "ready", "documents": [document], "issues": []}
    except Exception as exc:
        return {"status": "incomplete", "documents": [],
                "issues": [{"source": "yfinance historical close", "reason": type(exc).__name__ + ": " + str(exc)}]}


def cached_historical_quote_document(ticker, *, on, as_of, archive_root, context,
                                     quote_currency, fetch=None, include_identity=False,
                                     existing=None, allow_acquire=True):
    """Cache only a complete exact observation, with its native proof and context.

    A failed free read creates no success receipt. A later explicit tool call
    may repeat that idempotent read; there is no automatic retry or prior close.
    """
    from copy import deepcopy
    from bellomberg.reporting.exact_artifacts import _create_exact

    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode('utf8')
    try:
        if not isinstance(context, dict) or not context or not isinstance(quote_currency, str):
            raise ValueError('Explicit listing/calendar context and observed quote currency required')
        key = {'policy': 'exact_historical_close_cache/1',
            'dataset': 'yfinance.history:unadjusted_close/1', 'ticker': ticker,
            'on': on, 'as_of': as_of, 'quote_currency': quote_currency,
            'include_identity': include_identity, 'context': deepcopy(context)}
        root = Path(archive_root).resolve()
        path = root / 'historical-quote-receipts' / (sha256(encode(key)).hexdigest() + '.json')
        if not path.resolve().is_relative_to(root):
            raise ValueError('Historical quote receipt escaped the explicit archive')

        def verify(body):
            if not isinstance(body, dict) or body.get('key') != key:
                raise ValueError('Historical quote receipt context differs')
            report = body.get('report')
            if (not isinstance(report, dict) or report.get('status') != 'ready'
                    or len(report.get('documents') or []) != 1):
                raise ValueError('Historical quote receipt is not a complete observation')
            observed = json.loads(report['documents'][0]['text'])['observation']
            if observed.get('currency') != quote_currency:
                raise ValueError('Historical observed currency differs from the accepted quotation identity')
            rebuilt = historical_quote_document(ticker, on=on, as_of=as_of,
                fetch=lambda *_: deepcopy(observed), include_identity=include_identity)
            if rebuilt != report:
                raise ValueError('Historical quote receipt cannot be recompiled exactly')
            return report

        if existing is not None:
            if (not isinstance(existing, dict) or Path(existing.get('path', '')).resolve() != path
                    or existing.get('key') != key):
                raise ValueError('Pinned historical quote cache differs from the requested context')
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != existing.get('sha256'):
                raise ValueError('Pinned historical quote cache bytes changed')
            report = verify(json.loads(raw))
        elif path.is_file():
            raw = path.read_bytes()
            report = verify(json.loads(raw))
        else:
            if not allow_acquire:
                raise ValueError('Exact historical quote receipt unavailable; explicit acquisition required')
            report = historical_quote_document(ticker, on=on, as_of=as_of,
                fetch=fetch, include_identity=include_identity)
            if report.get('status') != 'ready':
                return report
            body = {'key': key, 'report': report}
            verify(body)
            raw = encode(body)
            _create_exact(path, raw, sha256(raw).hexdigest())
        return {**deepcopy(report), 'cache_receipt': {
            'path': str(path), 'sha256': sha256(raw).hexdigest(), 'key': key}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [
            {'source': 'exact historical close', 'reason': type(exc).__name__ + ': ' + str(exc)}]}


def listing_identity_document(source, raw, *, ticker, on):
    if sha256(raw).hexdigest() != source.get("document_sha256"):
        raise ValueError("listing document bytes differ from verified primary source")
    if (source.get("metadata") or {}).get("report_date") != on:
        raise ValueError("listing evidence must belong to the opening-period filing")
    identity = listing_identity(raw, ticker)
    if identity["status"] != "verified":
        return {"status": "incomplete", "documents": [], "issues": [identity]}
    text = json.dumps({"listing": identity, "facts": [{"value": 1, "unit": "shares per quote", "end": on}]},
                      ensure_ascii=False, separators=(",", ":"))
    document = {"id": "listing-" + source["id"], "url": source["url"],
                "published_at": source["published_at"], "text": text,
                "sha256": sha256(text.encode()).hexdigest(), "document_sha256": source["document_sha256"],
                "origin": "SEC_listing_unit_identity", "metadata": {"ticker": ticker,
                    "share_class": identity["title"], "basis": identity["basis"], "report_date": on},
                "extraction_coverage": {"status": "derived_unit_identity", "context_id": identity["context_id"],
                    "limitation": "same listed ordinary class only; no ADR ratio or cross-class conversion"}}
    return {"status": "ready", "documents": [document], "issues": []}


def verify_sec_listing(document, primary, *, ticker, on):
    """Reprove a fresh SEC listing against the same sealed original bytes.

    A full raw HTML text representation is acceptable only when its byte hash
    is the original document hash. Extracted/normalized text cannot replace a
    missing archive, and a declared archive path never falls back on text.
    """
    if not isinstance(document, dict) or not isinstance(primary, dict):
        raise ValueError('Original primary and SEC listing document required')
    digest = primary.get('document_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Original primary raw SHA256 required for SEC listing')
    metadata = primary.get('metadata')
    if not isinstance(metadata, dict):
        raise ValueError('Original primary issuer and report metadata required')
    archive_path = primary.get('archive_path')
    try:
        if archive_path is not None:
            if not isinstance(archive_path, str) or not archive_path:
                raise ValueError('Declared original archive path is invalid')
            raw = Path(archive_path).read_bytes()
        else:
            text = primary.get('text')
            if not isinstance(text, str):
                raise ValueError('Full original raw HTML bytes unavailable')
            raw = text.encode('utf8')
    except OSError as exc:
        raise ValueError('Original archive cannot be read; no normalized-text fallback') from exc
    if sha256(raw).hexdigest() != digest:
        raise ValueError('Original SEC listing bytes differ from the sealed raw SHA256')
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, 'html.parser')
    canonical = lambda value: re.sub(r'[^\w]', '', value.casefold()) if isinstance(value, str) else ''
    issuers = {canonical(node.get_text(' ', strip=True)) for node in soup.find_all('ix:nonnumeric')
               if node.get('name') == 'dei:EntityRegistrantName'}
    issuer = canonical(metadata.get('issuer'))
    if not issuer or issuers != {issuer}:
        raise ValueError('Raw SEC registrant differs from the full primary issuer or is ambiguous')
    compiled = listing_identity_document(primary, raw, ticker=ticker, on=on)
    if compiled.get('status') != 'ready' or len(compiled.get('documents') or []) != 1:
        raise ValueError('Original raw SEC ordinary listing cannot be recompiled: ' + repr(compiled.get('issues')))
    expected = compiled['documents'][0]
    for key in ('id', 'url', 'published_at', 'text', 'sha256', 'document_sha256',
                'metadata', 'extraction_coverage'):
        if document.get(key) != expected.get(key):
            raise ValueError('SEC listing proof differs from the original primary: ' + key)
    if 'origin' in document and document['origin'] != expected['origin']:
        raise ValueError('SEC listing proof has a different explicit origin')
    return expected
