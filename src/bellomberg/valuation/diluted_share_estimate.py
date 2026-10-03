"""Prove an explicit diluted-share *estimate* from bound SEC observations.

The period EPS denominators are weighted averages, never opening balances. This
helper authenticates the three inputs and the arithmetic without relabeling the
result as a historical SEC fact or selecting the proxy for the analyst.
"""
from datetime import date
from hashlib import sha256
from math import isclose, isfinite
from pathlib import Path
import json
import re

from .input_evidence import same_sec_bound_issuer


METHOD = 'reported_dilution_ratio_proxy'
_CONCEPTS = {
    'outstanding': {('us-gaap', 'CommonStockSharesOutstanding'),
                    ('dei', 'EntityCommonStockSharesOutstanding')},
    'weighted_basic': {('us-gaap', 'WeightedAverageNumberOfSharesOutstandingBasic')},
    'weighted_diluted': {('us-gaap', 'WeightedAverageNumberOfDilutedSharesOutstanding')},
}
_ITEM_FIELDS = {'value', 'kind', 'evidence_ids', 'rationale', 'valid_until',
                'valid_until_basis', 'dilution_estimate'}
_FACT_FIELDS = {'value', 'evidence_ids', 'quoted_value', 'quoted_unit', 'evidence_pointer'}
_POINTER = re.compile(r'/facts/(0|[1-9][0-9]*)/observation/val\Z')


def _number(value):
    return type(value) in (int, float) and isfinite(value)


def _day(value):
    if not isinstance(value, str):
        raise ValueError('SEC share period must be an ISO date')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('SEC share period must be canonical')
    return parsed


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _bound_fact(name, supplied, catalog, entity, opening_date, sec_filings):
    if not isinstance(supplied, dict) or set(supplied) != _FACT_FIELDS:
        raise ValueError(name + ': fact fields incomplete or unused')
    ids, pointers = supplied['evidence_ids'], supplied['evidence_pointer']
    if (not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str)
            or ids[0] not in catalog or not isinstance(pointers, dict)
            or set(pointers) != {'value', 'unit', 'period'}):
        raise ValueError(name + ': one original SEC fact and three pointers required')
    source = catalog[ids[0]]
    if (not isinstance(source, dict) or not ids[0].startswith('xbrl-')
            or source.get('id') != ids[0] or not isinstance(source.get('text'), str)
            or source.get('sha256') != sha256(source['text'].encode('utf-8')).hexdigest()):
        raise ValueError(name + ': original SEC XBRL source SHA mismatch')
    match = _POINTER.fullmatch(str(pointers['value']))
    if not match:
        raise ValueError(name + ': normalized SEC observation pointer required')
    root, index = '/facts/' + match[1], int(match[1])
    if (pointers['unit'] != root + '/unit'
            or pointers['period'] != root + '/observation/end'):
        raise ValueError(name + ': value, unit and period must share one SEC fact')
    raw = json.loads(source['text'])
    if not isinstance(raw, dict) or not isinstance(raw.get('facts'), list):
        raise ValueError(name + ': SEC XBRL facts missing')
    fact = raw['facts'][index]
    observation = fact.get('observation') if isinstance(fact, dict) else None
    if (not isinstance(observation, dict)
            or (fact.get('taxonomy'), fact.get('concept')) not in _CONCEPTS[name]
            or fact.get('unit') != 'shares' or supplied['quoted_unit'] != 'shares'
            or not _number(observation.get('val')) or observation['val'] <= 0
            or not _number(supplied['quoted_value'])
            or supplied['quoted_value'] != observation['val']
            or not _number(supplied['value']) or supplied['value'] <= 0
            or not isclose(supplied['value'], observation['val'] / 1_000_000,
                           rel_tol=1e-12, abs_tol=1e-9)
            or observation.get('end') != opening_date
            or not same_sec_bound_issuer(source, raw, fact, entity, sec_filings)):
        raise ValueError(name + ': amount, unit, date, concept or SEC filing identity differs')
    start = observation.get('start')
    if name == 'outstanding':
        if start is not None:
            raise ValueError('outstanding must be an instant at opening')
    else:
        if start is None or _day(start) > _day(opening_date):
            raise ValueError(name + ': weighted-average duration required')
    return {'source_id': ids[0], 'source': source, 'body': raw, 'fact': fact,
            'index': index, 'value': supplied['value'], 'start': start,
            'accession': observation['accn']}


def _shortest_comparable_period(body, *, accession, opening_date):
    """Find the shortest same-filing basic/diluted pair, rejecting duplicates."""
    pairs = {}
    for index, fact in enumerate(body['facts']):
        if not isinstance(fact, dict) or fact.get('unit') != 'shares':
            continue
        concept = (fact.get('taxonomy'), fact.get('concept'))
        name = next((role for role in ('weighted_basic', 'weighted_diluted')
                     if concept in _CONCEPTS[role]), None)
        if name is None:
            continue
        observation = fact.get('observation')
        if (not isinstance(observation, dict) or observation.get('accn') != accession
                or observation.get('end') != opening_date
                or not isinstance(observation.get('start'), str)):
            continue
        start = _day(observation['start'])
        duration = (_day(opening_date) - start).days + 1
        if not 1 <= duration <= 371:
            raise ValueError('weighted-average duration is outside one fiscal year')
        pairs.setdefault(start, {}).setdefault(name, []).append(index)
    complete = {start: roles for start, roles in pairs.items()
                if all(role in roles for role in ('weighted_basic', 'weighted_diluted'))}
    if not complete:
        raise ValueError('same-filing comparable weighted basic/diluted pair absent')
    shortest_start = max(complete)
    selected = complete[shortest_start]
    if any(len(selected[role]) != 1 for role in ('weighted_basic', 'weighted_diluted')):
        raise ValueError('shortest weighted share period is ambiguous')
    return shortest_start.isoformat(), selected


def _listed_single_class(catalog, primary, share_class, opening_date):
    """Reprove the SEC listing and reject a classed aggregate denominator."""
    from bs4 import BeautifulSoup
    from .quotation_evidence import verify_sec_listing
    if not _text(share_class):
        raise ValueError('an explicit listed share class is required')
    listing = catalog.get('listing-' + primary['id'])
    if not isinstance(listing, dict):
        raise ValueError('SEC listing class proof missing from original catalog')
    ticker = (listing.get('metadata') or {}).get('ticker')
    if not _text(ticker):
        raise ValueError('SEC listing ticker missing')
    proven = verify_sec_listing(listing, primary, ticker=ticker, on=opening_date)
    title = json.loads(proven['text'])['listing']['title']
    if title != share_class:
        raise ValueError('SEC listing class differs from model perimeter')
    if re.search(r'\bclass\s+[a-z0-9]+\b', title, re.I):
        raise ValueError('aggregate companyfacts shares cannot prove a listed share class')
    raw = (Path(primary['archive_path']).read_bytes() if primary.get('archive_path') is not None
           else primary['text'].encode('utf-8'))
    if sha256(raw).hexdigest() != primary['document_sha256']:
        raise ValueError('SEC primary raw bytes changed during class proof')
    soup = BeautifulSoup(raw, 'html.parser')
    ordinary_titles = {node.get_text(' ', strip=True) for node in soup.find_all('ix:nonnumeric')
        if node.get('name') == 'dei:Security12bTitle' and re.search(
            r'\b(common (?:stock|shares)|ordinary shares)\b', node.get_text(' ', strip=True), re.I)}
    if ordinary_titles != {title}:
        raise ValueError('multiple SEC ordinary listing classes cannot bind aggregate companyfacts')
    for node in soup.find_all('xbrldi:explicitmember'):
        if str(node.get('dimension', '')).endswith('StatementClassOfStockAxis') and \
                node.get_text(' ', strip=True) != 'us-gaap:CommonStockMember':
            raise ValueError('class-specific SEC equity dimension forbids aggregate denominator')


def prove_diluted_share_estimate(item, catalog, *, entity, opening_date, sec_filings,
                                 share_class=None):
    """Return None only for a recompiled, explicitly limited analyst proxy."""
    try:
        if (not isinstance(item, dict) or set(item) != _ITEM_FIELDS
                or item.get('kind') != 'analyst_estimate' or not _text(item.get('rationale'))
                or not _text(item.get('valid_until')) or item.get('valid_until_basis') is None
                or not _number(item.get('value')) or item['value'] <= 0):
            raise ValueError('diluted-share proxy requires a complete analyst estimate')
        if (not isinstance(catalog, dict) or not isinstance(sec_filings, (list, tuple))
                or not _text(entity)):
            raise ValueError('original SEC catalog and entity required')
        _day(opening_date)
        estimate = item['dilution_estimate']
        if (not isinstance(estimate, dict)
                or set(estimate) != {'method', 'facts', 'applicability', 'limitation'}
                or estimate['method'] != METHOD or not _text(estimate['applicability'])
                or not _text(estimate['limitation'])):
            raise ValueError('explicit dilution applicability and limitations required')
        terms = estimate['facts']
        if not isinstance(terms, dict) or set(terms) != set(_CONCEPTS):
            raise ValueError('opening outstanding plus weighted basic/diluted facts required')
        declared = item['evidence_ids']
        if (not isinstance(declared, list) or not declared
                or any(not isinstance(ident, str) or ident not in catalog for ident in declared)
                or len(set(declared)) != len(declared)):
            raise ValueError('unique original evidence IDs required')
        proven = {name: _bound_fact(name, terms[name], catalog, entity, opening_date, sec_filings)
                  for name in ('outstanding', 'weighted_basic', 'weighted_diluted')}
        if not {row['source_id'] for row in proven.values()} <= set(declared):
            raise ValueError('proxy operand sources absent from driver citations')
        if (len({row['source_id'] for row in proven.values()}) != 1
                or len({row['accession'] for row in proven.values()}) != 1
                or len({row['index'] for row in proven.values()}) != 3
                or proven['weighted_basic']['start'] != proven['weighted_diluted']['start']):
            raise ValueError('proxy observations require one filing and comparable period')
        source = proven['outstanding']['source']
        accession = proven['outstanding']['accession']
        matching_filings = [filing for filing in sec_filings if isinstance(filing, dict)
            and (filing.get('metadata') or {}).get('report_date') == opening_date
            and (filing.get('metadata') or {}).get('emittente_id') == 'CIK:' +
                str(proven['outstanding']['body']['cik']).zfill(10)
            and str((filing.get('metadata') or {}).get('accession', '')).replace('-', '') ==
                accession.replace('-', '')]
        if (len(matching_filings) != 1 or matching_filings[0]['id'] not in catalog
                or catalog[matching_filings[0]['id']] != matching_filings[0]):
            raise ValueError('selected opening SEC filing is not unique in original catalog')
        if share_class is not None:
            _listed_single_class(catalog, matching_filings[0], share_class, opening_date)
        start, pair = _shortest_comparable_period(proven['outstanding']['body'],
            accession=accession, opening_date=opening_date)
        if (start != proven['weighted_basic']['start']
                or pair['weighted_basic'] != [proven['weighted_basic']['index']]
                or pair['weighted_diluted'] != [proven['weighted_diluted']['index']]):
            raise ValueError('more recent comparable weighted-share period exists')
        outstanding = proven['outstanding']['value']
        basic = proven['weighted_basic']['value']
        diluted = proven['weighted_diluted']['value']
        if diluted <= basic or not isclose(item['value'], outstanding * diluted / basic,
                                          rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError('positive diluted ratio and exact disclosed proxy arithmetic required')
    except (ValueError, KeyError, TypeError, IndexError, OverflowError,
            ZeroDivisionError, OSError) as exc:
        return str(exc)
    return None


def diluted_share_disclosure(item):
    """Source-facing record prefix; this is never an observed diluted count."""
    estimate = item.get('dilution_estimate') if isinstance(item, dict) else None
    if not isinstance(estimate, dict) or estimate.get('method') != METHOD:
        raise ValueError('verified diluted-share proxy required for disclosure')
    facts = estimate['facts']
    if set(facts) != set(_CONCEPTS):
        raise ValueError('diluted-share proxy disclosure requires all operands')
    outstanding, basic, diluted = (facts[name]['value'] for name in
                                   ('outstanding', 'weighted_basic', 'weighted_diluted'))
    return ('PROXY/STIMA DILUZIONE: ' + str(outstanding) + ' million opening outstanding × '
            + str(diluted) + ' million period-weighted diluted / ' + str(basic)
            + ' million period-weighted basic = ' + str(item['value']) + ' million estimated '
            'diluted denominator. This is not a reported instant diluted count; period averaging, '
            'reported rounding, award mix, excluded awards and future vesting may differ; '
            'quoted share counts do not prove exact legal-share precision. '
            'Applicability: ' + estimate['applicability'] + '. Limitation: ' + estimate['limitation'] + '.\n')


def diluted_share_policy():
    """Optional prompt contract: explicit choice, exact sources, no automatic fallback."""
    return {'method': METHOD, 'kind': 'analyst_estimate',
        'optional': True,
        'formula': 'opening outstanding million shares * shortest same-filing period-weighted diluted / period-weighted basic',
        'facts': ['outstanding', 'weighted_basic', 'weighted_diluted'],
        'units': "Each facts[role].value is in million shares; quoted_value is the original raw count in shares and quoted_unit is 'shares'.",
        'source_rule': 'Each fact uses one original accession-bound SEC XBRL observation with raw value, unit, period pointers and issuer/CIK proof. Opening outstanding is an instant; weighted basic and diluted share the shortest comparable duration ending at opening (at most one fiscal year). The original SEC listing must prove the same single unclassed ordinary equity as the model perimeter; an aggregate companyfacts count cannot prove a specific Class A/B denominator.',
        'judgment_rule': 'Explain why the observed period dilution ratio reasonably represents opening award mix; disclose averaging, rounding, excluded awards, future vesting and any source gap. If not defensible, return null. Never call the proxy a reported instant count or infer zero dilution.'}
