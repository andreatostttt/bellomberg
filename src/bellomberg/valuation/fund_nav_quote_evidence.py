"""Dated issuer PDF prices converted to explicit quote units, never invented closes."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import calendar
import json
import re
from urllib.parse import urlsplit

from .document_evidence import source_dates, valid_source_url, verify_page_references
from .fund_nav_statement import normalize_fund_statement

NORMALIZER = 'fund_nav_quotation_pdf_v1'
PREFIX = 'fund-nav-quote-'
LIMITATION = ('Issuer-reported price per share at the factsheet date, converted from GBP to GBX with two primary unit definitions; '
    'not an observed GBX close, no intraday timestamp, no issuer NAV FX assumption, no current-price refresh or dilution approval.')

def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def _source(source, cutoff):
    if (not isinstance(source, dict) or not isinstance(source.get('text'), str)
            or sha256(source['text'].encode()).hexdigest() != source.get('sha256')
            or not re.fullmatch('[0-9a-f]{64}', str(source.get('id')))
            or source.get('document_sha256') != source['id'] or not valid_source_url(source.get('url'))):
        raise ValueError('exact original source identity, text and byte hashes required')
    if source.get('page_references'):
        verify_page_references(source['text'], source['page_references'])
    return source_dates(source, cutoff)

def _pin(source, quote):
    if source['text'].count(quote) != 1:
        raise ValueError('unique original source excerpt required')
    start = source['text'].index(quote)
    page = next((row for row in (source.get('page_references') or [])
                 if row['inizio'] <= start and start+len(quote) <= row['fine']), None)
    return {'source_document_id': source['id'], 'start': start, 'end': start+len(quote),
            'page': page['pagina'] if page else None, 'quote': quote, 'sha256': sha256(quote.encode()).hexdigest()}

def _one(pattern, text):
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1:
        raise ValueError('one exact issuer factsheet observation required: '+pattern)
    return matches[0]

def normalize_fund_quote(statement_source, price_source, unit_source, decimal_source, *, ticker, entity, share_class,
                         listing_symbol, isin, exchange, on, as_of, quote_currency, quote_unit):
    cutoff, opening = date.fromisoformat(as_of), date.fromisoformat(on)
    if opening > cutoff or (quote_currency, quote_unit) != ('GBP', 'GBX') or exchange != 'London Stock Exchange':
        raise ValueError('supported declared GBP/GBX London listing and ordered source dates required')
    if (not all(isinstance(value, str) and value.strip() for value in (ticker, entity, share_class, listing_symbol))
            or not re.fullmatch('[A-Z]{2}[A-Z0-9]{9}[0-9]', isin)
            or re.search(r'\b(?:depositary|depository|ADR|ADS|units|preferred|preference)\b', share_class, re.I)):
        raise ValueError('exact ordinary/public share identity required; no depositary or cross-class ratio')
    if ticker != listing_symbol+'.L':
        raise ValueError('declared London provider ticker must bind the exact primary listing symbol; no proxy ticker')
    sources = {'statement': statement_source, 'price': price_source, 'unit': unit_source, 'decimal': decimal_source}
    dates = {key: _source(source, cutoff) for key, source in sources.items()}
    if (urlsplit(unit_source['url']).hostname != 'docs.londonstockexchange.com'
            or urlsplit(decimal_source['url']).hostname not in ('bankofengland.co.uk', 'www.bankofengland.co.uk')):
        raise ValueError('primary exchange and central-bank decimal definitions required')
    normalized = normalize_fund_statement(statement_source)
    if normalized['status'] != 'ready':
        raise ValueError('complete class-attributed statement required: '+repr(normalized['issues']))
    facts = json.loads(normalized['documents'][0]['text'])['facts']
    selected = [fact for fact in facts if fact['end'] == on and fact.get('share_class') == share_class]
    def financial(concept):
        matches = [fact for fact in selected if fact['concept'] == concept and fact['entity'] == entity]
        if len(matches) != 1:
            raise ValueError('exact selected class/date financial observation required')
        return matches[0]
    shares, nav = financial('ClassSharesOutstanding'), financial('ClassNavPerShare')
    if nav['unit'] != 'USD per share':
        raise ValueError('supported dual USD/GBP factsheet layout required')
    statement = statement_source['text']
    listing = re.search(re.escape(share_class)+r'[^.]{0,300}(?:commenced trading|admitted to[^.]{0,100})[^.]{0,300}'+re.escape(exchange), statement)
    if listing is None:
        raise ValueError('primary statement must identify the selected class as listed on the declared exchange')
    price_text = price_source['text']
    words = lambda value: ' '.join(re.findall('[a-z0-9]+', value.casefold()))
    if words(entity) not in words(price_text[:6000]):
        raise ValueError('exact issuer identity absent from factsheet')
    day_text = calendar.month_name[opening.month]+r'\s+'+str(opening.day)+r',\s*'+str(opening.year)
    header = _one(r'^'+day_text+r'\s*$', price_text[:1000])
    identity = _one(r'^ISIN:\s*'+re.escape(isin)+r'\s+Ticker\s+SEDOL\s*$', price_text)
    security = _one(r'^LSE Main Market \(£\)\s+'+re.escape(listing_symbol)+r'\s+LN\s+([A-Z0-9]{7})\s*$', price_text)
    pattern = r'^{} per Share \(\$/£\)\s+\$(\d+\.\d+)\s*/\s*£(\d+\.\d+)\s*$'
    price_row, nav_row = _one(pattern.format('Price'), price_text), _one(pattern.format('NAV'), price_text)
    count_row = _one(r'^Shares Outstanding\s+([0-9]+(?:,[0-9]{3})*)\s*$', price_text)
    count = int(count_row[1].replace(',', ''))
    if count != shares['value'] or Decimal(nav_row[1]) != Decimal(str(nav['value'])):
        raise ValueError('factsheet NAV and issued shares do not reconcile to the selected financial class/date')
    compatible = [fact for fact in facts if fact['end'] == on and fact['concept'] == 'ClassSharesOutstanding' and fact['value'] == count]
    if len(compatible) != 1:
        raise ValueError('factsheet share count cannot distinguish the financial share class')
    unit_quote = 'GBX (for clarification, this acronym refers to GBP in pence)'
    if unit_source['text'].count(unit_quote) != 1:
        raise ValueError('explicit primary GBX/GBP-pence definition required')
    decimal_quote = '100 pennies to the pound'
    if decimal_source['text'].count(decimal_quote) != 1:
        raise ValueError('explicit primary decimal GBP factor required')
    observed, factor = Decimal(price_row[2]), Decimal(100)
    transformed = observed*factor
    if observed <= 0 or Decimal(str(float(transformed))) != transformed:
        raise ValueError('positive exactly representable converted issuer price required')
    pins = {'economic_date': _pin(price_source, header[0]), 'isin': _pin(price_source, identity[0]),
        'listing': _pin(price_source, security[0]), 'price': _pin(price_source, price_row[0]),
        'reported_nav': _pin(price_source, nav_row[0]), 'shares': _pin(price_source, count_row[0]),
        'listed_class': _pin(statement_source, listing[0]), 'gbx_definition': _pin(unit_source, unit_quote),
        'decimal_definition': _pin(decimal_source, decimal_quote)}
    payload = {'basis': 'issuer_reported_price_converted_to_quote_units', 'ticker': ticker, 'entity': entity,
        'share_class': share_class, 'exchange': exchange, 'isin': isin, 'listing_symbol': listing_symbol,
        'sedol': security[1], 'on': on, 'financial_currency': nav['unit'].split(' per share')[0],
        'quote_currency': quote_currency, 'quote_unit': quote_unit,
        'price_observation': {'value': float(observed), 'unit': 'GBP per share',
            'end': on, 'source_document_id': price_source['id'], 'time_basis': 'issuer as-of date; intraday time not disclosed'},
        'issuer_reported_nav': {'USD': float(Decimal(nav_row[1])), 'GBP': float(Decimal(nav_row[2]))},
        'conversion': {'operation': 'multiply_price_by_decimal_unit_factor', 'input_decimal': str(observed),
            'factor': 100, 'output_decimal': str(transformed), 'source_document_ids': [unit_source['id'], decimal_source['id']]},
        'facts': [{'value': float(transformed), 'unit': 'GBX per share', 'end': on},
                  {'value': 1, 'unit': 'shares per quote', 'end': on}, {'value': 100, 'unit': 'GBX per GBP', 'end': on}],
        'source_document_ids': {key: value['id'] for key, value in sources.items()}, 'proofs': pins, 'limitation': LIMITATION}
    # The latest actual raw-source receipt anchors derived availability. Every
    # contributing source remains separately pinned and recompiled by catalog.
    anchor_key = max(sources, key=lambda key: (dates[key]['available_at'], (sources[key].get('retrieval') or {}).get('retrieved_at', '')))
    anchor, encoded = sources[anchor_key], _json(payload)
    return {'id': PREFIX+sha256(encoded.encode()).hexdigest(), 'url': anchor['url'], **deepcopy(dates[anchor_key]),
        'document_sha256': anchor['document_sha256'], 'text': encoded, 'sha256': sha256(encoded.encode()).hexdigest(),
        'metadata': {'normalizer': NORMALIZER, 'source_document_ids': payload['source_document_ids'],
            'ticker': ticker, 'entity': entity, 'share_class': share_class, 'listing_symbol': listing_symbol, 'isin': isin,
            'exchange': exchange, 'on': on, 'report_date': on, 'as_of': as_of,
            'financial_currency': nav['unit'].split(' per share')[0], 'quote_currency': quote_currency,
            'quote_unit': quote_unit, 'ticker_binding_basis': 'declared_London_provider_symbol_plus_.L'}}
