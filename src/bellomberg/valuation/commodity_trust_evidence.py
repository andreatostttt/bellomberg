"""Recompile a physical gold trust's published observations from original bytes.

This bounded source format proves an entire single-gold-asset/single-fee ledger,
historical expenses, primary risk disclosures and separately dated market data.
It supplies no fair value, geographic exposure, investment leverage or future fee
estimate. No ticker or numerical issuer observation is installed in this module.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

NORMALIZER = 'commodity_trust_primary/1'
PREFIX = 'commodity-trust-facts-'
ROLES = ('statement', 'product', 'prospectus')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _flat(value):
    return ' '.join(value.replace('\u00ae', '').split())


def _name(value):
    return ' '.join(re.findall(r'[a-z0-9]+', str(value).replace('\u00ae', '').casefold()))


def _date(value):
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError('Canonical ISO observation date required')
    return result


def _english_day(value):
    value = _flat(value)
    for pattern in ('%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise ValueError('Explicit English calendar date missing: ' + value)


def _raw(source, kind, cutoff):
    from .document_evidence import source_dates, valid_source_url
    if not isinstance(source, dict) or not isinstance(source.get('id'), str) or not valid_source_url(source.get('url')):
        raise ValueError('Original public source identity and URL required')
    if (source.get('metadata') or {}).get('normalizer'):
        raise ValueError('A normalized document cannot serve as an original primary')
    path = source.get('archive_path')
    if not isinstance(path, str) or not Path(path).is_file():
        raise ValueError('Archived original bytes required; extracted or caller-authored text is insufficient')
    raw = Path(path).read_bytes()
    if not raw or len(raw) > 20_000_000 or sha256(raw).hexdigest() != source.get('document_sha256'):
        raise ValueError('Original raw document SHA differs or bounded source size exceeded')
    text = source.get('text')
    if not isinstance(text, str) or sha256(text.encode()).hexdigest() != source.get('sha256'):
        raise ValueError('Original extracted-text SHA differs')
    dates = source_dates(source, cutoff)
    if kind == 'pdf':
        from pypdf import PdfReader
        if not raw.startswith(b'%PDF-'):
            raise ValueError('Original financial/prospectus PDF bytes required')
        reader = PdfReader(BytesIO(raw))
        if not 1 <= len(reader.pages) <= 120:
            raise ValueError('Bounded complete primary PDF required')
        raw_pages = [page.extract_text() or '' for page in reader.pages]
        pages = [_flat(page) for page in raw_pages]
        if any(not page for page in pages):
            raise ValueError('Every physical primary page must be extractable')
        return {'source': source, 'raw': raw, 'pages': pages, 'raw_pages': raw_pages, 'dates': dates}
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, 'html.parser')
    return {'source': source, 'raw': raw, 'pages': [_flat(soup.get_text(' ', strip=True))],
            'soup': soup, 'dates': dates}


def _pin(original, page, quote):
    body = original['pages'][page - 1]
    if quote not in body:
        raise ValueError('Exact raw-page quote required')
    source = original['source']
    return {'source_document_id': source['id'], 'url': source['url'],
            'document_sha256': sha256(original['raw']).hexdigest(),
            'source_text_sha256': source['sha256'], 'physical_page': page,
            'page_text_sha256': sha256(body.encode()).hexdigest(), 'quote': quote}


def _page(original, phrase):
    found = [(index + 1, body) for index, body in enumerate(original['pages']) if phrase in body]
    if len(found) != 1:
        raise ValueError('Unique complete statement page required: ' + phrase)
    return found[0]


def _row(body, label, count):
    number = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
    pattern = label + ''.join(r'\s+\$?\s*(' + number + r')' for _ in range(count))
    found = list(re.finditer(pattern, body, re.I))
    if len(found) != 1:
        raise ValueError('Unique complete numeric row required: ' + label)
    match = found[0]
    return [Decimal(value.replace(',', '')) for value in match.groups()], match[0]


def _unscaled_statement(body):
    # This format consumes absolute USD amounts. A different multiplier is a
    # separate source format, never an implicit conversion or unit default.
    if re.search(r'\b(?:amounts?|figures|values|financial statements)\b.{0,100}?\b(?:thousands?|millions?|billions?|hundreds?)\b'
                 r'|\b(?:in|expressed in|stated in|presented in)\s+(?:thousands?|millions?|billions?|hundreds?)\b'
                 r'|\$\s*\(?\s*0{3,}\b', body, re.I):
        raise ValueError('Scaled statement currency is outside the certified absolute-USD source format')


def _certified_reporting_context(financial, section, entity):
    # A unit declaration before the table is still part of its accounting
    # scope. Only the known navigational/issuer preamble is certified here.
    body = _page(financial, section)[1]
    preamble = _name(body.split(section, 1)[0])
    allowed = (r'(?:table of contents )?(?:part i financial information )?'
               r'(?:item 1 financial statements )?(?:' + re.escape(_name(entity)) + r')?')
    if re.fullmatch(allowed, preamble) is None:
        raise ValueError('Uncertified statement preamble or accounting currency declaration')
    # A gold-price quotation in USD cannot override a different reporting
    # currency declared elsewhere in the complete financial primary.
    declaration = (r'\b(?:reporting|presentation|functional|financial statements?|statement)\s+currency\b'
        r'|\bcurrency\s*(?::|=)'
        r'|\b(?:all\s+)?(?:amounts?|figures|values|financial statements)\s+'
        r'(?:(?:are|is)\s+)?(?:(?:expressed|stated|reported|presented|denominated)\s+)?'
        r'(?:in|:|=)\s+[A-Za-z .-]{0,40}\b(?:dollars?|euros?|pounds?|yen|USD|CAD|EUR|GBP|AUD|JPY)\b')
    if any(re.search(declaration, page, re.I) for page in financial['pages']):
        raise ValueError('Explicit accounting currency/units require a separately certified source format')


def _four_period_columns(body, opening, first_row, *, section=None):
    headings = list(re.finditer(r'for the three and six months ended ([A-Za-z]+ \d{1,2}, \d{4}) and (\d{4})', body, re.I))
    if (len(headings) != 1 or _english_day(headings[0][1]) != opening
            or int(headings[0][2]) >= opening.year):
        raise ValueError('Unique current and comparative three/six-month source dates required')
    heading = headings[0]
    if section is not None and not body[:heading.start()].strip().endswith(section):
        raise ValueError('Uncertified statement units or extra header before the dated table')
    tail = body[heading.end():]
    beginning = re.search(first_row, tail, re.I)
    if beginning is None:
        raise ValueError('Certified first table row required before selecting expense columns')
    header = tail[:beginning.start()]
    columns = re.fullmatch(r'[\s.]*Three Months Ended ([A-Za-z]+ \d{1,2},)\s+'
        r'Six Months Ended ([A-Za-z]+ \d{1,2},)\s+(\d{4})\s+(\d{4})\s+(\d{4})\s+(\d{4})\s*', header)
    if (columns is None or _english_day(columns[1] + ' ' + str(opening.year)) != opening
            or _english_day(columns[2] + ' ' + str(opening.year)) != opening
            or columns.groups()[2:] != (str(opening.year), heading[2], str(opening.year), heading[2])):
        raise ValueError('Exact dated current/prior three-month then six-month column order required')
    # Preserve the original evidence quote without the heading's punctuation.
    quote = header.strip().lstrip('.').strip()
    return heading, quote


def _risk(original, start, end, max_length=1800):
    found = []
    for index, body in enumerate(original['pages']):
        for match in re.finditer(start + r'.{1,' + str(max_length) + r'}?' + end, body, re.I):
            found.append((index + 1, match[0]))
    if not found:
        raise ValueError('Exact primary disclosure absent: ' + start)
    # Repeated disclosure is accepted only when its entire text agrees.
    if len({_name(quote) for _, quote in found}) > 1:
        raise ValueError('Conflicting primary disclosure: ' + start)
    page, quote = found[0]
    return quote, _pin(original, page, quote)


def _market(product, ticker, entity, cutoff):
    soup = product['soup']
    graphs = []
    for script in soup.find_all('script', type='application/ld+json'):
        parsed = json.loads(script.string or script.get_text())
        graphs.extend(parsed.get('@graph', []) if isinstance(parsed, dict) else [])
    funds = [row for row in graphs if isinstance(row, dict) and
             'InvestmentFund' in ([row.get('@type')] if isinstance(row.get('@type'), str) else row.get('@type', []))]
    if len(funds) != 1 or _name(funds[0].get('name')) != _name(entity):
        raise ValueError('Unique primary product fund entity required')
    identifiers = funds[0].get('identifier')
    if not isinstance(identifiers, list) or [row.get('value') for row in identifiers
            if isinstance(row, dict) and row.get('propertyID') == 'ticker'] != [ticker]:
        raise ValueError('Exact primary product ticker required')
    fund_id = funds[0].get('@id')
    nodes = [row for row in graphs if isinstance(row, dict) and (row.get('about') or {}).get('@id') == fund_id]
    properties = [value for row in nodes for value in row.get('additionalProperty', [])]
    nav_units = [row for row in properties if row.get('name') == 'NAV as of']
    benchmarks = [row for row in properties if row.get('name') == 'Reference Benchmark']
    if len(nav_units) != 1 or nav_units[0].get('unitText') != 'USD' or len(benchmarks) != 1 or not benchmarks[0].get('value'):
        raise ValueError('Explicit USD same-fund NAV unit and reference benchmark required')
    def visible(name, label, pattern):
        values = soup.select('[data-id="keyFundFacts-' + name + '-data"]')
        labels = soup.select('[data-id="keyFundFacts-' + name + '-label"]')
        observed = soup.select('[data-id="keyFundFacts-' + name + '-asOf"]')
        if len(values) != 1 or len(labels) != 1 or len(observed) != 1 or _flat(labels[0].get_text(' ', strip=True)) != label:
            raise ValueError('Unique original product metric/date required: ' + label)
        text = _flat(values[0].get_text(' ', strip=True))
        if not re.fullmatch(pattern, text):
            raise ValueError('Explicit source value/unit required: ' + label)
        stamp = re.sub(r'^as of\s+', '', _flat(observed[0].get_text(' ', strip=True)), flags=re.I)
        day = _english_day(stamp)
        if day > cutoff:
            raise ValueError('Market observation follows the acquisition cutoff')
        return text, day, {'source_document_id': product['source']['id'], 'url': product['source']['url'],
            'document_sha256': sha256(product['raw']).hexdigest(), 'source_text_sha256': product['source']['sha256'],
            'selector': 'data-id:keyFundFacts-' + name, 'label': label, 'observed_label': stamp, 'value_label': text}
    price, price_day, price_pin = visible('closingPrice', 'Closing Price', r'\$\d+(?:,\d{3})*(?:\.\d+)?')
    volume, volume_day, volume_pin = visible('consolidatedVolume', 'Daily Volume', r'\d+(?:,\d{3})*(?:\.\d+)?')
    spread, spread_day, spread_pin = visible('thirtyDayMedianBidAskSpread', '30 Day Median Bid/Ask Spread', r'\d+(?:\.\d+)?%')
    nav_day = _english_day(nav_units[0]['valueReference']['value'])
    if price_day != nav_day or volume_day != spread_day:
        raise ValueError('Same-currency issuer quote and same-date volume/median observation required')
    p = Decimal(price.replace('$', '').replace(',', ''))
    v = Decimal(volume.replace(',', ''))
    s = Decimal(spread.rstrip('%')) / 100
    if p <= 0 or v < 0 or not 0 <= s <= 1:
        raise ValueError('Primary market observations outside their domain')
    return {'price': float(p), 'price_date': price_day.isoformat(), 'volume': float(v),
            'market_date': volume_day.isoformat(), 'spread': float(s), 'benchmark': benchmarks[0]['value'],
            'pins': {'price': price_pin, 'volume': volume_pin, 'spread': spread_pin,
                     'currency': deepcopy(nav_units[0]), 'benchmark': deepcopy(benchmarks[0]),
                     'fund_identity': deepcopy(funds[0])}}


def normalize_commodity_trust(statement, product, prospectus, *, ticker, entity, on, as_of):
    """Return seven typed primary observations or an explicit source gap.

    Input roles are originals from the complete server catalog. Normalization is
    deterministic and must be rerun by the common catalog before accepting a
    record pointer. Publication remains the originals' measured availability.
    """
    try:
        opening, cutoff = _date(on), _date(as_of)
        if opening > cutoff or not isinstance(ticker, str) or not ticker or not isinstance(entity, str) or not _name(entity):
            raise ValueError('Exact acquired security identity and valid dated accounting scope required')
        originals = {role: _raw(source, 'html' if role == 'product' else 'pdf', cutoff)
                     for role, source in zip(ROLES, (statement, product, prospectus))}
        if len({row['source']['id'] for row in originals.values()}) != 3:
            raise ValueError('Three distinct archived primary sources required')
        hosts = {urlsplit(row['source']['url']).hostname for row in originals.values()}
        if len(hosts) != 1:
            raise ValueError('Issuer product, financial statement and linked prospectus must have the same primary origin')
        links = {urljoin(product['url'], anchor['href']) for anchor in originals['product']['soup'].find_all('a', href=True)}
        if statement['url'] not in links or prospectus['url'] not in links:
            raise ValueError('Exact financial and prospectus URLs must be linked by the archived issuer product')
        financial = originals['statement']
        cover = financial['pages'][0]
        original_cover = financial['raw_pages'][0].replace('\u00a0', ' ')
        identity_match = re.search(r'(?m)^([^\n\r()]{1,150})[ \t\r\n]*\(Exact name of registrant as specified in its charter\)', original_cover)
        if not identity_match or _name(identity_match[1]) != _name(entity):
            raise ValueError('Exact registered entity required on the original financial cover')
        end = re.search(r'For the quarterly period ended ([A-Za-z]+ \d{1,2}, \d{4})', cover)
        listing = re.search(r'Title of each class Trading Symbol\(s\) Name of each exchange on which registered Shares\s+(\S+)\s+NYSE Arca', cover)
        if not end or _english_day(end[1]) != opening or not listing or listing[1] != ticker:
            raise ValueError('Original financial reporting date and ordinary Shares/ticker listing required')
        physical = originals['prospectus']
        title_lines = [_flat(line) for line in physical['raw_pages'][0].splitlines() if _flat(line)]
        if not title_lines or _name(title_lines[0]) != _name(entity) or not re.search(
                r'under the ticker symbol [“\"]' + re.escape(ticker) + r'[”\"]', physical['pages'][0]):
            raise ValueError('Exact prospectus entity and quoted trust ticker required')
        page, balance = _page(financial, 'Statements of Assets and Liabilities (Unaudited)')
        _certified_reporting_context(financial, 'Statements of Assets and Liabilities (Unaudited)', entity)
        _unscaled_statement(balance)
        date_match = re.search(r'At ([A-Za-z]+ \d{1,2}, \d{4}) and ([A-Za-z]+ \d{1,2}, \d{4})', balance)
        if not date_match or _english_day(date_match[1]) != opening or _english_day(date_match[2]) >= opening:
            raise ValueError('Current and comparative balance-sheet dates must be explicit and descending')
        table_start = balance.index('Statements of Assets and Liabilities (Unaudited)') + len('Statements of Assets and Liabilities (Unaudited)')
        asset_start = re.search(r'\bAssets\b', balance[table_start:])
        header = balance[table_start:table_start + asset_start.start()] if asset_start else ''
        expected_header = (date_match[0] + ' ' + date_match[1] + ' ' + date_match[2])
        if _flat(header) != expected_header:
            raise ValueError('Exact unscaled balance-sheet header and dated column order required')
        rows = {}
        labels = {'gold': r'Investment in gold bullion, at fair value\(a\)', 'total_assets': r'Total Assets',
                  'sponsor_fee_payable': r'Sponsor[’\']s fee payable', 'total_liabilities': r'Total Liabilities',
                  'net_assets': r'Net Assets', 'shares': r'Shares issued and outstanding\(b\)'}
        pins = {'financial_identity': _pin(financial, 1, listing[0]),
                'report_date': _pin(financial, 1, end[0]), 'balance_dates': _pin(financial, page, date_match[0])}
        for name, label in labels.items():
            numbers, quote = _row(balance, label, 2)
            rows[name] = numbers[0]
            pins[name] = _pin(financial, page, quote)
        # Reject every extra caption/amount inside the accounting perimeter.
        asset_scope = re.search(r'Assets (Investment in gold bullion,.*?) Liabilities ', balance)
        liability_scope = re.search(r'Liabilities (Sponsor[’\']s fee payable.*?) Commitments and contingent liabilities', balance)
        if not asset_scope or not liability_scope:
            raise ValueError('Complete single-asset and single-fee accounting sections required')
        for scope, names in ((asset_scope[1], ('gold', 'total_assets')), (liability_scope[1], ('sponsor_fee_payable', 'total_liabilities'))):
            residual = scope
            for name in names:
                residual = residual.replace(pins[name]['quote'], '', 1)
            if residual.strip():
                raise ValueError('An additional or unclassified balance-sheet claim/asset is not supported by this complete scope')
        if (rows['gold'] != rows['total_assets'] or rows['sponsor_fee_payable'] != rows['total_liabilities']
                or rows['total_assets'] - rows['total_liabilities'] != rows['net_assets']
                or any(value < 0 for value in rows.values()) or rows['shares'] <= 0 or rows['net_assets'] <= 0):
            raise ValueError('The entire observed ledger must close without omitted liabilities or balancing assumptions')
        operations_page, operations = _page(financial, 'Statements of Operations (Unaudited)')
        _unscaled_statement(operations)
        heading, columns = _four_period_columns(operations, opening, r'\bExpenses\b',
            section='Statements of Operations (Unaudited)')
        sponsor, sponsor_quote = _row(operations, r'Sponsor[’\']s fee', 4)
        expenses, expense_quote = _row(operations, r'Total expenses', 4)
        highlights_page, highlights = _page(financial, '8 - Financial Highlights')
        _four_period_columns(highlights, opening, r'Net asset value per Share, beginning of period|Expenses\s*\(e\)')
        ratios = re.search(r'Expenses\s*\(e\)\s+([0-9.]+)%\s+([0-9.]+)%\s+([0-9.]+)%\s+([0-9.]+)%', highlights)
        annualized = re.search(r'\(e\) Percentage is annualized\.', highlights)
        if not ratios or not annualized or expenses[2] < sponsor[2]:
            raise ValueError('Reported annualized historical expense ratio and entire expense statement required')
        ratio = Decimal(ratios[3]) / 100
        if not 0 <= ratio <= 1:
            raise ValueError('Annualized historical cost outside domain')
        # Six months is a literal source duration; derive its inclusive beginning
        # from the explicit ending date, rather than assume a reporting calendar.
        previous_month = opening.month - 6
        previous_year = opening.year
        if previous_month <= 0:
            previous_month += 12; previous_year -= 1
        import calendar
        if opening.day != calendar.monthrange(opening.year, opening.month)[1]:
            raise ValueError('Six calendar months require an explicit month-end source date; no fiscal-start guess')
        previous_day = calendar.monthrange(previous_year, previous_month)[1]
        period_start = date(previous_year, previous_month, previous_day) + timedelta(days=1)
        contingent, contingent_pin = _risk(financial,
            r'The Trust’s maximum exposure under these arrangements is unknown as this would involve', r'have not yet occurred\.')
        notes_page, notes = _page(financial, '1 - Organization')
        sponsor_match = re.search(r'The Trust’s sponsor is (.*?), a Delaware limited liability company', notes)
        if not sponsor_match or 'as a New York trust.' not in notes or 'stated in U.S. dollars' not in notes:
            raise ValueError('Observed trust legal structure, sponsor and USD measurement required')
        market = _market(originals['product'], ticker, entity, cutoff)
        risks, risk_pins = {}, {}
        risk_patterns = {
            'issuer': (r'The Shares are not interests in nor obligations', r'Sponsor or the Trustee\.'),
            'custody': (r'The value of the Shares will be adversely affected if gold', r'corresponding loss\.'),
            'liquidity': (r'The lack of an active trading market for the Shares', r'disposition of your Shares\.'),
            'tracking': (r'The amount of gold represented by', r'Trust expenses\.'),
        }
        for name, (start, finish) in risk_patterns.items():
            risks[name], risk_pins[name] = _risk(physical, start, finish)
        risks['counterparty'], risk_pins['counterparty'] = contingent, contingent_pin
        holdings = {'assets': {'gold': float(rows['gold'])},
            'liabilities': {'sponsor_fee_payable': float(rows['sponsor_fee_payable'])},
            'reported': {key: float(rows[key]) for key in ('total_assets', 'total_liabilities', 'net_assets', 'shares')},
            'as_of': on, 'currency': 'USD'}
        values = {
            'identity': {'ticker': ticker, 'name': entity, 'instrument': 'etf', 'issuer': sponsor_match[1],
                         'legal_structure': 'New York trust', 'underlying': 'physical gold bullion'},
            'holdings': holdings,
            'terms': {'replication': 'direct_asset', 'benchmark': market['benchmark'], 'effective_date': on,
                      'investment_leverage': None, 'leverage_basis': 'not_separately_established'},
            'fees': {'period_start': period_start.isoformat(), 'period_end': on, 'sponsor_fees': float(sponsor[2]),
                     'total_expenses': float(expenses[2]), 'annualized_expense_ratio': float(ratio),
                     'annualization_basis': 'reported_annualized_historical_expense_ratio',
                     'future_expense_ratio': None, 'other_future_fees_ratio': None, 'contingent_expenses': contingent},
            'risks': risks,
            'liquidity': {'daily_volume': market['volume'], 'volume_unit': 'quoted units', 'spread_ratio': market['spread'],
                          'spread_basis': '30_day_median_bid_ask', 'spread_window_days': 30,
                          'observed_date': market['market_date'], 'provider': product['url']},
            'quotation': {'financial_currency': 'USD', 'quote_currency': 'USD', 'quote_unit': 'USD',
                          'quote_units_per_currency': 1., 'financial_to_quote_rate': 1., 'shares_per_quote': 1.,
                          'share_class': 'Shares', 'price': market['price'], 'price_as_of': market['price_date']}}
        from .trade_idea_commodity_exposure import SCHEMA, record_period
        observations = [{'driver': driver, 'field': SCHEMA[driver][0], 'unit': 'contract',
                         'accounting_basis': SCHEMA[driver][2], 'entity': entity,
                         'period': record_period(driver, value, {'valuation_date': on}), 'value': value}
                        for driver, value in values.items()]
        pins.update(expense_period=_pin(financial, operations_page, heading[0]),
                    expense_columns=_pin(financial, operations_page, columns),
                    sponsor_expense=_pin(financial, operations_page, sponsor_quote),
                    total_expense=_pin(financial, operations_page, expense_quote),
                    annualized_ratio=_pin(financial, highlights_page, ratios[0]),
                    annualization=_pin(financial, highlights_page, annualized[0]),
                    contingent=contingent_pin, sponsor=_pin(financial, notes_page, sponsor_match[0]))
        body = {'contract': NORMALIZER, 'ticker': ticker, 'entity': entity, 'on': on, 'as_of': as_of,
                'source_document_ids': {role: value['source']['id'] for role, value in originals.items()},
                'observations': observations, 'proofs': {'financial': pins, 'market': market['pins'], 'risks': risk_pins},
                'operations': {'expense_duration': 'six_calendar_months_ending_at_explicit_source_date',
                    'expense_start': period_start.isoformat(), 'expense_column': 2, 'ledger_column': 0,
                    'spread_percent_to_ratio': 'divide_by_100', 'quote_unit': 'USD per ordinary quoted Share'},
                'limitations': ['No fair value or target price.', 'No investment leverage inferred from NAV accounting.',
                    'No geographic exposure attribution.', 'Future and contingent costs are not estimated.',
                    'The 30-day median spread is not an instantaneous quote.',
                    'Accounting balances, historical expense duration and market observations keep their separate dates.']}
        encoded = _json(body)
        anchor = max(originals.values(), key=lambda row: row['dates']['available_at'])
        document = {'id': PREFIX + sha256(encoded.encode()).hexdigest(), 'url': anchor['source']['url'],
                    **deepcopy(anchor['dates']), 'document_sha256': anchor['source']['document_sha256'],
                    'text': encoded, 'sha256': sha256(encoded.encode()).hexdigest(),
                    'metadata': {'normalizer': NORMALIZER, 'source_document_id': statement['id'],
                                 'source_document_ids': body['source_document_ids'],
                                 'ticker': ticker, 'entity': entity, 'on': on, 'report_date': on, 'as_of': as_of}}
        return {'status': 'ready', 'documents': [document], 'issues': []}
    except (ValueError, TypeError, KeyError, OSError, IndexError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'field': 'documents', 'reason': str(exc)}]}
