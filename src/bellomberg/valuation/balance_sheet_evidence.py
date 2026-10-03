"""Reconcile supported classified balances and note leaves, without economic judgments."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path
import json
import re

from .balance_detail_evidence import (
    _contexts, _observation, _validated_packet, extract_balance_detail_packet,
    normalize_balance_details,
)
from .statement_table_evidence import _json

NORMALIZER = 'balance_sheet_v1'
PREFIX = 'balance-sheet-'
_TOTAL = 'us-gaap:LiabilitiesAndStockholdersEquity'


def _explicit_dimensional_note(facts, contexts, namespaces, meta):
    """Distinguish scoped asset disclosures from a consolidated balance.

    Unknown/malformed contexts remain candidates and therefore fail closed.
    Reported balance subtotals are never dismissed as an accessory asset note.
    """
    totals = {'us-gaap:'+name for name in ('AssetsCurrent', 'AssetsNoncurrent',
        'LiabilitiesCurrent', 'LiabilitiesNoncurrent', 'Liabilities', 'StockholdersEquity',
        'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
        'LiabilitiesAndStockholdersEquity')}
    if (any(n.get('name') in totals for n in facts)
            or namespaces.get('xbrldi') != 'http://xbrl.org/2006/xbrldi'):
        return False
    on = date.fromisoformat(meta['report_date'])
    cik = meta['emittente_id'].removeprefix('CIK:')
    current_asset = False
    for fact in facts:
        context = contexts.get(fact.get('contextref'))
        if context is None:
            return False
        entities = context.find_all('xbrli:entity', recursive=False)
        periods = context.find_all('xbrli:period', recursive=False)
        if (len(entities) != 1 or len(periods) != 1
                or len(context.find_all(True, recursive=False)) != 2):
            return False
        entity, period = entities[0], periods[0]
        identifiers = entity.find_all('xbrli:identifier', recursive=False)
        segments = entity.find_all('xbrli:segment', recursive=False)
        instants = period.find_all('xbrli:instant', recursive=False)
        if (len(identifiers) != 1 or len(segments) > 1 or len(instants) != 1
                or len(entity.find_all(True, recursive=False)) != 1+len(segments)
                or len(period.find_all(True, recursive=False)) != 1
                or identifiers[0].get('scheme') != 'http://www.sec.gov/CIK'
                or identifiers[0].get_text(strip=True).zfill(10) != cik):
            return False
        try:
            instant = date.fromisoformat(instants[0].get_text(strip=True))
        except ValueError:
            return False
        if instant > on:
            return False
        if not segments:
            # Risk/other disclosures may refer to the parent company, while
            # every Assets fact must still prove its different perimeter.
            if fact.get('name') == 'us-gaap:Assets':
                return False
            continue
        members = segments[0].find_all('xbrldi:explicitmember', recursive=False)
        if not members or len(segments[0].find_all(True, recursive=False)) != len(members):
            return False
        axes = set()
        for member in members:
            axis = member.get('dimension', '')
            value = member.get_text(strip=True)
            if member.find(True) or axis in axes:
                return False
            for qname in (axis, value):
                if not re.fullmatch(r'[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*', qname):
                    return False
                if not re.fullmatch(r'https?://[^\s]+', namespaces.get(qname.split(':')[0], '')):
                    return False
            axes.add(axis)
        current_asset |= fact.get('name') == 'us-gaap:Assets' and instant == on
    return current_asset


def extract_balance_sheet_packet(source, raw):
    """Select the full table from verified raw bytes; retain contexts and note binding."""
    from bs4 import BeautifulSoup
    details = extract_balance_detail_packet(source, raw)
    soup = BeautifulSoup(raw, 'html.parser')
    packet = {k: deepcopy(v) for k, v in details.items()
              if k not in ('format', 'sha256', 'contexts', 'units', 'tables')}
    context_nodes = {n['id']: n for n in soup.find_all('xbrli:context')}
    tables, excluded, refs, units = [], [], set(), set()
    for index, table in enumerate(soup.find_all('table')):
        facts = table.find_all('ix:nonfraction')
        names = {n.get('name') for n in facts}
        # A partial candidate is an unsupported balance, not an absent disclosure.
        if table.find_parent('table') or not ({'us-gaap:Assets', _TOTAL} & names):
            continue
        item = {'index': index, 'html': str(table)}
        if _explicit_dimensional_note(facts, context_nodes, details['namespaces'], source['metadata']):
            excluded.append({**item, 'reason': 'explicit_dimensional_note',
                             'context_refs': sorted({n.get('contextref') for n in facts})})
        else:
            tables.append(item)
        refs.update(n.get('contextref') for n in facts)
        units.update(n.get('unitref') for n in facts)
    if len(tables)+len(excluded) > 20 or any(len(t['html']) > 512_000 for t in tables+excluded):
        raise ValueError('balance sheet layout exceeds bounded extraction')
    all_contexts = {key: str(node) for key, node in context_nodes.items()}
    all_units = {n['id']: str(n) for n in soup.find_all('xbrli:unit')}
    if not refs <= all_contexts.keys() or not units <= all_units.keys():
        raise ValueError('balance sheet inline context or unit missing')
    packet.update(format=NORMALIZER, tables=tables, note_packet_sha256=details['sha256'],
        contexts={k: all_contexts[k] for k in sorted(refs)},
        units={k: all_units[k] for k in sorted(units)})
    if excluded:
        packet['excluded_tables'] = excluded
    packet['sha256'] = sha256(_json(packet).encode()).hexdigest()
    return packet


def _validate_excluded_tables(source, packet, meta):
    """Recompile the scope proof too; rehashing a discarded balance is not proof."""
    from bs4 import BeautifulSoup
    excluded = packet.get('excluded_tables', [])
    if not isinstance(excluded, list) or len(packet['tables'])+len(excluded) > 20:
        raise ValueError('invalid excluded balance table inventory')
    if not excluded:
        return
    indexes = [item['index'] for item in packet['tables']+excluded]
    if any(type(index) is not int or index < 0 for index in indexes) or len(set(indexes)) != len(indexes):
        raise ValueError('excluded balance table identity is ambiguous')
    contexts = {key: BeautifulSoup(xml, 'html.parser').find('xbrli:context')
                for key, xml in packet['contexts'].items()}
    compact = lambda s: re.sub(r'\s+', '', s)
    source_text = compact(source['text'])
    for item in excluded:
        if not isinstance(item.get('html'), str) or len(item['html']) > 512_000:
            raise ValueError('excluded balance table layout exceeds bounds')
        tables = BeautifulSoup(item['html'], 'html.parser').find_all('table')
        if len(tables) != 1 or compact(tables[0].get_text(' ', strip=True)) not in source_text:
            raise ValueError('excluded balance table differs from primary text')
        facts = tables[0].find_all('ix:nonfraction')
        if (item.get('reason') != 'explicit_dimensional_note'
                or item.get('context_refs') != sorted({n.get('contextref') for n in facts})
                or any(n.get('unitref') not in packet['units'] for n in facts)
                or not _explicit_dimensional_note(facts, contexts, packet['namespaces'], meta)):
            raise ValueError('excluded balance table scope proof differs')


def _caption_facts(cells, packet, *, cik, on):
    """Preserve descriptive equity figures in labels without adding them as USD.

    Share concepts retain their unit checks. An explicitly reported allowance
    belongs to its same-context net receivable; it is preserved, never added.
    """
    from bs4 import BeautifulSoup
    nodes = cells[0].find_all('ix:nonfraction') if cells else []
    monetary = []
    shares = {'us-gaap:'+name for name in ('CommonStockSharesAuthorized',
        'CommonStockSharesIssued', 'CommonStockSharesOutstanding', 'TreasuryStockCommonShares')}
    for node in nodes:
        unit = BeautifulSoup(packet['units'][node['unitref']], 'html.parser').find('xbrli:unit')
        if unit is None or unit.get('id') != node['unitref']:
            raise ValueError('caption fact unit identity changed')
        measures = [m.get_text(strip=True) for m in unit.find_all('xbrli:measure')]
        if node.get('name') in shares:
            valid = not unit.find('xbrli:divide') and measures == ['xbrli:shares']
        elif node.get('name') == 'us-gaap:CommonStockParOrStatedValuePerShare':
            numerator, denominator = unit.find('xbrli:unitnumerator'), unit.find('xbrli:unitdenominator')
            valid = (unit.find('xbrli:divide') is not None and numerator is not None and denominator is not None
                and len(measures) == 2 and re.fullmatch(r'iso4217:[A-Z]{3}', measures[0])
                and measures[0][-3:] not in ('XXX', 'XTS') and measures[1] == 'xbrli:shares'
                and [m.get_text(strip=True) for m in numerator.find_all('xbrli:measure')] == measures[:1]
                and [m.get_text(strip=True) for m in denominator.find_all('xbrli:measure')] == measures[1:])
        elif node.get('name') == 'us-gaap:AllowanceForDoubtfulAccountsReceivableCurrent':
            context = BeautifulSoup(packet['contexts'][node['contextref']], 'html.parser')
            instant = context.find('xbrli:instant')
            period = instant.get_text(strip=True) if instant else ''
            if (date.fromisoformat(period) > date.fromisoformat(on)
                    or node['contextref'] not in _contexts(packet, cik, period)):
                raise ValueError('receivable allowance caption scope or date differs')
            related = [fact for cell in cells[1:] for fact in cell.find_all('ix:nonfraction')
                       if fact.get('contextref') == node['contextref']]
            if (len(related) != 1 or related[0].get('name') not in (
                    'us-gaap:AccountsReceivableNetCurrent', 'us-gaap:ReceivablesNetCurrent')):
                raise ValueError('allowance caption requires its reported net receivable')
            observation = _observation(node, packet, period)
            parent = _observation(related[0], packet, period)
            if observation['unit'] != parent['unit'] or any(x['end'] == period for x in monetary):
                raise ValueError('allowance caption currency or period is ambiguous')
            monetary.append({**observation, 'treatment': 'already_in_reported_net_amount',
                             'related_reported_tag': parent['reported_tag']})
            valid = True
        else:
            valid = False
        if not valid:
            raise ValueError('unsupported concept or unit in balance caption')
    return {id(node) for node in nodes}, monetary


def _class_axis_member(node, packet, cik, on):
    """Accept only an opening, single-axis SEC common-share capital context."""
    from bs4 import BeautifulSoup
    if (node.get('name') != 'us-gaap:CommonStockValue'
            or packet['namespaces'].get('xbrldi') != 'http://xbrl.org/2006/xbrldi'):
        return None
    key = node.get('contextref')
    xml = packet['contexts'].get(key)
    if not xml:
        raise ValueError('common-share class context missing')
    context = BeautifulSoup(xml, 'html.parser').find('xbrli:context')
    if context is None or context.get('id') != key:
        raise ValueError('common-share class context identity changed')
    period = context.find('xbrli:period', recursive=False)
    instant = period.find('xbrli:instant', recursive=False) if period else None
    if instant is None:
        raise ValueError('common-share class requires an instant context')
    if instant.get_text(strip=True) != on:
        return None
    entity = context.find('xbrli:entity', recursive=False)
    identifiers = entity.find_all('xbrli:identifier', recursive=False) if entity else []
    segments = entity.find_all('xbrli:segment', recursive=False) if entity else []
    if (len(context.find_all('xbrli:entity', recursive=False)) != 1
            or len(context.find_all('xbrli:period', recursive=False)) != 1
            or len(period.find_all('xbrli:instant', recursive=False)) != 1
            or len(identifiers) != 1 or len(segments) != 1
            or identifiers[0].get('scheme') != 'http://www.sec.gov/CIK'
            or identifiers[0].get_text(strip=True).zfill(10) != cik
            or context.find(['xbrli:scenario', 'xbrldi:typedmember'])):
        raise ValueError('common-share class issuer, date or dimension differs')
    members = segments[0].find_all('xbrldi:explicitmember', recursive=False)
    if len(members) != 1 or len(segments[0].find_all(True, recursive=False)) != 1:
        raise ValueError('one explicit common-share class dimension required')
    member = members[0].get_text(strip=True)
    if members[0].get('dimension') != 'us-gaap:StatementClassOfStockAxis' or ':' not in member:
        raise ValueError('StatementClassOfStockAxis member required')
    prefix, local = member.split(':', 1)
    if not (member == 'us-gaap:CommonClassAMember' or
            local in ('OrdinarySharesMember', 'CommonClassXMember')
            and prefix not in ('us-gaap', 'dei', 'xbrli', 'xbrldi', 'ix')
            and re.fullmatch(r'https?://[^\s]+', packet['namespaces'].get(prefix, ''))):
        raise ValueError('unsupported ordinary, A or X share class member')
    return member


def _rows(source, packet, digest, contexts, on, *, class_axis=False, cik=None):
    from bs4 import BeautifulSoup
    if len(packet['tables']) != 1:
        raise ValueError('unique complete balance table required')
    item = packet['tables'][0]
    table = BeautifulSoup(item['html'], 'html.parser').find('table')
    compact = lambda s: re.sub(r'\s+', '', s)
    if table is None or compact(table.get_text(' ', strip=True)) not in compact(source['text']):
        raise ValueError('balance sheet table differs from primary text')
    rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
    tagged = [i for i, row in enumerate(rows) if row.find('ix:nonfraction')]
    if not tagged or len(rows) > 500:
        raise ValueError('balance sheet rows missing or excessive')
    observations, disclosures = [], []
    for index in range(tagged[0], tagged[-1]+1):
        row = rows[index]
        cells = row.find_all(['td', 'th'], recursive=False)
        captions, caption_disclosures = _caption_facts(cells, packet, cik=cik, on=on)
        nodes = [node for node in row.find_all('ix:nonfraction') if id(node) not in captions]
        # Section headings are allowed; a printed but untagged numeric cell is not.
        for cell in cells[1:]:
            copy = BeautifulSoup(str(cell), 'html.parser')
            for fact in copy.find_all('ix:nonfraction'):
                fact.decompose()
            if re.search(r'[0-9]|^\s*[-\u2013\u2014]\s*$', copy.get_text(' ', strip=True)):
                raise ValueError('untagged numeric balance cell')
        if not nodes:
            if re.search(r'[0-9]', row.get_text(' ', strip=True)):
                raise ValueError('untagged numeric balance row')
            continue
        selected = [n for n in nodes if n.get('contextref') in contexts]
        class_member = None
        if class_axis and not selected:
            class_candidates = [(n, member) for n in nodes
                if (member := _class_axis_member(n, packet, cik, on)) is not None]
            if len(class_candidates) == 1:
                selected = [class_candidates[0][0]]
                class_member = class_candidates[0][1]
        if len(selected) != 1:
            raise ValueError('one consolidated opening fact required for every balance row')
        node = selected[0]
        proof = {'source_document_id': source['id'], 'packet_sha256': digest,
                 'table_index': item['index'], 'row_index': index}
        label = cells[0].get_text(' ', strip=True) if cells else ''
        if class_member is not None:
            title = re.sub(r'[\s\u00a0\ufffd]+', ' ', label).strip().casefold()
            expected = {'OrdinarySharesMember': 'ordinary shares',
                        'CommonClassAMember': 'class a ordinary shares',
                        'CommonClassXMember': 'class x ordinary shares'}
            if not title.startswith(expected[class_member.split(':', 1)[1]]):
                raise ValueError('reported share-class caption differs from XBRL member')
        if node.get('name') == 'us-gaap:CommitmentsAndContingencies':
            if node.get('xsi:nil') not in ('true', '1') or node.get('continuedat'):
                raise ValueError('unsupported monetary contingency balance row')
            disclosures.append({'reported_tag': node['name'], 'label': label, 'value_exact': None,
                'end': on, 'proof': proof, 'status': 'not_quantified_requires_separate_review'})
        else:
            observation = _observation(node, packet, on)
            if caption_disclosures:
                observation['caption_disclosures'] = [{**fact, 'proof': dict(proof)}
                                                      for fact in caption_disclosures]
            if (class_member is not None and observation['explicit_zero']
                    and node.get_text('', strip=True) not in ('-', '\u2013', '\u2014', '\ufffd')):
                raise ValueError('class capital zero requires a reported dash and fixed-zero tag')
            # Inline XBRL may report a positive debit/contra-account amount,
            # while its printed balance cell explicitly subtracts it. Retain
            # both representations; never flip an already-negative fact twice.
            cell_text = compact(node.find_parent(['td', 'th']).get_text(' ', strip=True))
            fact_text = compact(node.get_text(' ', strip=True))
            if cell_text == '(' + fact_text + ')' and Decimal(observation['value_exact']) > 0:
                observation['inline_value_exact'] = observation['value_exact']
                observation['value_exact'] = '-' + observation['value_exact']
                observation['presentation_sign'] = 'negative_parentheses'
            if class_member is not None:
                observation['reported_concept'] = observation['reported_tag']
                observation['reported_tag'] += '|us-gaap:StatementClassOfStockAxis=' + class_member
                observation['class_member'] = class_member
                observation['class_axis'] = 'us-gaap:StatementClassOfStockAxis'
                proof['class_context_id'] = node['contextref']
            observations.append({**observation, 'proof': proof, 'label': label})
    return observations, disclosures


def _reconcile(facts, notes):
    tags = [f['reported_tag'] for f in facts]
    if len(tags) != len(set(tags)) or not facts:
        raise ValueError('duplicate or missing balance fact')
    if len({f['unit'] for f in facts}) != 1:
        raise ValueError('balance sheet currencies differ')
    by_tag = dict(zip(tags, facts))
    required = ['us-gaap:'+t for t in ('AssetsCurrent', 'Assets', 'LiabilitiesCurrent',
                                      'Liabilities', 'StockholdersEquity', 'LiabilitiesAndStockholdersEquity')]
    if not set(required) <= by_tag.keys():
        raise ValueError('classified balance totals missing; unsupported layout')
    positions = [tags.index(t) for t in required]
    if positions != sorted(positions) or positions[-1] != len(tags)-1:
        raise ValueError('balance totals order or closing coverage unsupported')
    def group(tag, start):
        parent = by_tag[tag]; children = facts[start:tags.index(tag)]
        if not children or sum((Decimal(f['value_exact']) for f in children), Decimal(0)) != Decimal(parent['value_exact']):
            raise ValueError('reported balance components do not reconcile: '+tag)
        return {'parent': parent, 'components': children}
    ca = group(required[0], 0)
    assets = group(required[1], positions[0])
    cl = group(required[2], positions[1]+1)
    liabilities = group(required[3], positions[2])
    equity_start = positions[3]+1
    mezzanine_tag = 'us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount'
    total_equity_tag = 'us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'
    minority_tags = {'us-gaap:MinorityInterest', 'us-gaap:NonredeemableNoncontrollingInterest'}
    closing_children = [liabilities['parent']]
    if mezzanine_tag in by_tag:
        if tags.index(mezzanine_tag) != equity_start:
            raise ValueError('redeemable noncontrolling capital is outside its reported section')
        closing_children.append(by_tag[mezzanine_tag])
        equity_start += 1
    equity = group(required[4], equity_start)
    equity_groups = [equity]
    equity_closing = equity['parent']
    closing_position = positions[4]+1
    if total_equity_tag in by_tag:
        minorities = minority_tags & by_tag.keys()
        if (len(minorities) != 1 or tags.index(total_equity_tag) != positions[4]+2
                or tags[positions[4]+1] not in minorities):
            raise ValueError('reported noncontrolling and total equity rows are incomplete or reordered')
        minority = by_tag[next(iter(minorities))]
        total_equity = by_tag[total_equity_tag]
        if Decimal(total_equity['value_exact']) != Decimal(equity_closing['value_exact'])+Decimal(minority['value_exact']):
            raise ValueError('parent and noncontrolling equity do not reconcile')
        equity_groups.append({'parent': total_equity, 'components': [equity_closing, minority]})
        equity_closing = total_equity
        closing_position += 2
    elif minority_tags & by_tag.keys():
        raise ValueError('noncontrolling interests require a reported total equity')
    if positions[5] != closing_position:
        raise ValueError('uncovered row between equity and closing balance')
    closing_children.append(equity_closing)
    value = lambda tag: Decimal(by_tag[tag]['value_exact'])
    if (value(required[1]) != value(_TOTAL)
            or value(_TOTAL) != sum((Decimal(f['value_exact']) for f in closing_children), Decimal(0))):
        raise ValueError('assets, liabilities and equity do not reconcile')
    if mezzanine_tag in by_tag or total_equity_tag in by_tag:
        equity_groups.append({'parent': by_tag[_TOTAL], 'components': closing_children})
    decompositions = {}
    for g in [ca, cl, *notes]:
        tag = g['parent']['reported_tag']
        if tag in decompositions:
            raise ValueError('duplicate balance decomposition')
        decompositions[tag] = g
    leaves, used = [], set()
    def expand(fact, side, ancestors):
        tag = fact['reported_tag']
        if tag in ancestors:
            raise ValueError('cycle in balance decomposition')
        if tag in decompositions:
            if tag in used:
                raise ValueError('balance decomposition counted twice')
            used.add(tag); g = decompositions[tag]
            if any(fact[k] != g['parent'][k] for k in ('reported_tag', 'namespace', 'value_exact', 'unit', 'end')):
                raise ValueError('note parent differs from balance parent')
            for child in g['components']:
                expand(child, side, ancestors+[tag])
        else:
            leaves.append({**fact, 'accounting_side': side, 'reported_ancestors': ancestors,
                           'economic_classification': 'unreviewed', 'model_treatment': None})
    for side, g in [('asset', assets), ('liability', liabilities)]:
        for child in g['components']:
            expand(child, side, [g['parent']['reported_tag']])
        if sum(Decimal(f['value_exact']) for f in leaves if f['accounting_side'] == side) != Decimal(g['parent']['value_exact']):
            raise ValueError('balance leaves do not reconcile')
    if set(decompositions) != used or len({f['reported_tag'] for f in leaves}) != len(leaves):
        raise ValueError('unlinked note decomposition or duplicate balance leaf')
    return [ca, assets, cl, liabilities, *equity_groups], leaves


def _mezzanine_class_layout(packet):
    """Identify the reported US-GAAP layout, never an issuer or sector proxy."""
    from bs4 import BeautifulSoup
    if len(packet['tables']) != 1:
        return False
    table = BeautifulSoup(packet['tables'][0]['html'], 'html.parser')
    names = [n.get('name') for n in table.find_all('ix:nonfraction')]
    required = {'us-gaap:AssetsNoncurrent', 'us-gaap:LiabilitiesNoncurrent',
        'us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount',
        'us-gaap:CommonStockValue', 'us-gaap:MinorityInterest',
        'us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
        _TOTAL}
    return required <= set(names) and 'us-gaap:Liabilities' not in names


def _reconcile_mezzanine_class(facts, notes):
    """Reconcile reported current/noncurrent liabilities and capital separately."""
    if notes:
        raise ValueError('class-axis balance with note decompositions requires separate review')
    tags = [fact['reported_tag'] for fact in facts]
    if not tags or len(tags) != len(set(tags)) or len({f['unit'] for f in facts}) != 1:
        raise ValueError('duplicate class-aware balance fact or inconsistent currency')
    required = ['us-gaap:' + tag for tag in (
        'AssetsCurrent', 'AssetsNoncurrent', 'Assets', 'LiabilitiesCurrent',
        'LiabilitiesNoncurrent', 'RedeemableNoncontrollingInterestEquityCarryingAmount',
        'StockholdersEquity', 'MinorityInterest',
        'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
        'LiabilitiesAndStockholdersEquity')]
    if not set(required) <= set(tags) or 'us-gaap:Liabilities' in tags:
        raise ValueError('reported class-axis balance totals missing or layout differs')
    positions = [tags.index(tag) for tag in required]
    if (positions != sorted(positions) or positions[-1] != len(tags)-1
            or positions[2] != positions[1]+1 or positions[5] != positions[4]+1
            or positions[7] != positions[6]+1 or positions[8] != positions[7]+1
            or positions[9] != positions[8]+1):
        raise ValueError('class-axis balance row order or closing coverage differs')
    by_tag = dict(zip(tags, facts))
    value = lambda fact: Decimal(fact['value_exact'])

    def group(parent, children, role):
        if not children or sum((value(f) for f in children), Decimal(0)) != value(parent):
            raise ValueError('reported class-axis components do not reconcile: ' + role)
        return {'parent': parent, 'components': children, 'structural_role': role}

    current_assets = group(by_tag[required[0]], facts[:positions[0]], 'Current assets')
    noncurrent_assets = group(by_tag[required[1]], facts[positions[0]+1:positions[1]],
                              'Noncurrent assets')
    assets = group(by_tag[required[2]], [current_assets['parent'], noncurrent_assets['parent']],
                   'Total assets')
    current_liabilities = group(by_tag[required[3]], facts[positions[2]+1:positions[3]],
                                'Current liabilities')
    noncurrent_liabilities = group(by_tag[required[4]], facts[positions[3]+1:positions[4]],
                                   'Noncurrent liabilities')
    equity_children = facts[positions[5]+1:positions[6]]
    classes = [f for f in equity_children if f.get('reported_concept') == 'us-gaap:CommonStockValue']
    if (len(classes) != 3 or {f.get('class_member', '').split(':')[-1] for f in classes}
            != {'OrdinarySharesMember', 'CommonClassAMember', 'CommonClassXMember'}
            or any(f.get('reported_concept') == 'us-gaap:CommonStockValue' for f in facts
                   if f not in equity_children)):
        raise ValueError('complete ordinary, A and X class capital rows required')
    equity_parent = group(by_tag[required[6]], equity_children, 'Parent shareholders equity')
    total_equity = group(by_tag[required[8]],
                         [equity_parent['parent'], by_tag[required[7]]], 'Total equity')
    closing_parts = [current_liabilities['parent'], noncurrent_liabilities['parent'],
                     by_tag[required[5]], total_equity['parent']]
    closing = group(by_tag[required[9]], closing_parts,
                    'Liabilities, redeemable interests and equity')
    if value(assets['parent']) != value(closing['parent']):
        raise ValueError('reported assets differ from closing capital balance')

    leaves = []
    for side, total, sections in (
        ('asset', assets, (current_assets, noncurrent_assets)),
        ('liability', None, (current_liabilities, noncurrent_liabilities)),
    ):
        for section in sections:
            for fact in section['components']:
                ancestors = ([total['parent']['reported_tag']] if total else [])
                ancestors.append(section['parent']['reported_tag'])
                leaves.append({**fact, 'accounting_side': side,
                               'reported_ancestors': ancestors,
                               'economic_classification': 'unreviewed',
                               'model_treatment': None})
    if (len({f['reported_tag'] for f in leaves}) != len(leaves)
            or sum((value(f) for f in leaves if f['accounting_side'] == 'asset'), Decimal(0))
               != value(assets['parent'])
            or sum((value(f) for f in leaves if f['accounting_side'] == 'liability'), Decimal(0))
               != value(current_liabilities['parent'])+value(noncurrent_liabilities['parent'])):
        raise ValueError('class-axis asset or liability leaves do not reconcile')
    liabilities_calculation = {
        'operation': 'sum_reported_components',
        'operands': [current_liabilities['parent'], noncurrent_liabilities['parent']],
        'value_exact': str(value(current_liabilities['parent'])+value(noncurrent_liabilities['parent'])),
        'unit': current_liabilities['parent']['unit'], 'end': current_liabilities['parent']['end'],
        'limitation': 'Calculated arithmetic sum; no reported aggregate liabilities tag.'}
    return ([current_assets, noncurrent_assets, assets, current_liabilities,
             noncurrent_liabilities, equity_parent, total_equity, closing],
            leaves, liabilities_calculation)


def normalize_balance_sheet(source):
    """Recompile reported coverage. This is neither an NWC definition nor a model."""
    try:
        if source.get('filing_verification') is not None:
            from .pdf_balance_evidence import normalize_pdf_balance
            return normalize_pdf_balance(source)
        if (source.get('statement_table_fields') is not None
                and not source.get('balance_sheet_fields')
                and (source.get('metadata') or {}).get('form') in ('6-K', '6-K/A', '20-F', '20-F/A')):
            from .printed_balance_evidence import normalize_printed_balance
            return normalize_printed_balance(source)
        meta, cik, packet, digest = _validated_packet(source, 'balance_sheet_fields', NORMALIZER)
        _validate_excluded_tables(source, packet, meta)
        if not packet['tables']:
            if packet.get('excluded_tables'):
                raise ValueError('only dimensional asset notes; consolidated balance coverage not established')
            return {'status': 'not_applicable', 'documents': [], 'issues': [],
                    'reason': 'No supported tagged balance table; reported balance coverage not established.'}
        if source['balance_detail_fields']['sha256'] != packet['note_packet_sha256']:
            raise ValueError('balance sheet note packet changed')
        class_axis = _mezzanine_class_layout(packet)
        if class_axis:
            # Rehashed cell/context geometry cannot certify a different class.
            if extract_balance_sheet_packet(source, Path(source['archive_path']).read_bytes()) != source['balance_sheet_fields']:
                raise ValueError('class-axis balance packet differs from original SEC bytes')
        details = normalize_balance_details(source)
        if details['status'] not in ('ready', 'not_applicable'):
            raise ValueError('balance sheet notes cannot be recompiled: '+repr(details['issues']))
        notes = json.loads(details['documents'][0]['text'])['groups'] if details['documents'] else []
        contexts = _contexts(packet, cik, meta['report_date'])
        with localcontext() as ctx:
            ctx.prec = 256
            facts, disclosures = _rows(source, packet, digest, contexts, meta['report_date'],
                                       class_axis=class_axis, cik=cik)
            if class_axis:
                groups, leaves, liabilities_calculation = _reconcile_mezzanine_class(facts, notes)
            else:
                groups, leaves = _reconcile(facts, notes)
        body = {'issuer': meta['issuer'], 'report_date': meta['report_date'],
            'groups': groups, 'components': leaves, 'nonmonetary_disclosures': disclosures,
            'reported_balance_reconciled': True, 'economic_classification_approved': False}
        if class_axis:
            body['liabilities_calculation'] = liabilities_calculation
        text = _json(body)
        document = {'id': PREFIX+source['id'], 'document_sha256': source['id'], 'url': source['url'],
            'published_at': source['published_at'], 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'origin': NORMALIZER, 'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'],
                'entity': meta['issuer'], 'scope': 'consolidated', 'report_date': meta['report_date'],
                'limitation': 'Reported balance and available note leaves only; no economic classification, complete working capital, forecasts or method records.'}}
        return {'status': 'ready', 'documents': [document], 'issues': []}
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError, OSError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def collect_balance_sheet(source, archive_root):
    from pathlib import Path
    try:
        path = Path(source['archive_path']).resolve()
        if not path.is_relative_to(Path(archive_root).resolve()):
            raise ValueError('balance sheet source outside verified archive')
        raw = path.read_bytes()
        if source.get('filing_verification') is not None:
            from .pdf_statement_evidence import extract_pdf_statement_packet
            packet = extract_pdf_statement_packet(source, raw)
            result = normalize_balance_sheet({**source, 'statement_table_fields':packet})
            result.update(packets={}, detail_packets={},
                statement_packets={source['id']:packet} if result['status'] == 'ready' else {})
            return result
        packet = extract_balance_sheet_packet(source, raw)
        details = extract_balance_detail_packet(source, raw)
        result = normalize_balance_sheet({**source, 'balance_sheet_fields': packet, 'balance_detail_fields': details})
        result['packets'] = {source['id']: packet} if result['status'] == 'ready' else {}
        result['detail_packets'] = {source['id']: details} if result['status'] == 'ready' else {}
        return result
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'status': 'incomplete', 'documents': [], 'packets': {}, 'detail_packets': {},
                'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}
