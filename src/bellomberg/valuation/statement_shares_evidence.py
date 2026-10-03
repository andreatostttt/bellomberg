"""Narrow SEC common-share extraction with explicit source comparisons.

Accept a comparative unrounded row or a raw inline, dimension-bound opening
count with its reported precision. Never rewrite companyfacts or create a
valuation record; missing and conflicting same-date tags remain visible.
"""
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from .input_evidence import same_entity_name, structured_fact_proof
from .preparation_exhibits import _sec_parts

NORMALIZER = 'sec_statement_shares_v1'
PREFIX = 'statement-shares-'
_INTEGER = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)'
_MONTHS = ('January February March April May June July August September October November December').split()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _inline_statement_shares(source, tagged_documents, cik, accession, on, issuer):
    """Read a dated common-stock equity tag without inventing unrounded shares."""
    archive_path = source.get('archive_path')
    if archive_path is None:
        return None  # Older printed, unrounded statement receipts have no raw HTML.
    raw = Path(archive_path).read_bytes()
    from bs4 import BeautifulSoup
    from .balance_detail_evidence import _same_sec_inline_issuer

    soup = BeautifulSoup(raw, 'html.parser')
    nodes = [node for node in soup.find_all('ix:nonfraction')
             if node.get('name') == 'us-gaap:CommonStockSharesOutstanding']
    capital_nodes = [node for node in soup.find_all('ix:nonfraction')
                     if node.get('name') in ('us-gaap:CommonStockSharesIssued',
                                             'us-gaap:TreasuryStockCommonShares')]
    if not nodes and not capital_nodes:
        return None
    namespaces = {key[6:]: value for key, value in (soup.html.attrs if soup.html else {}).items()
                  if key.startswith('xmlns:')}
    for prefix, uri in (('ix', 'http://www.xbrl.org/2013/inlineXBRL'),
                        ('xbrli', 'http://www.xbrl.org/2003/instance'),
                        ('xbrldi', 'http://xbrl.org/2006/xbrldi')):
        if namespaces.get(prefix) != uri:
            raise ValueError('SEC inline share namespace differs')
    if (not re.fullmatch(r'https?://fasb.org/us-gaap/20\d{2}', namespaces.get('us-gaap', ''))
            or not re.fullmatch(r'https?://xbrl.sec.gov/dei/20\d{2}', namespaces.get('dei', ''))
            or not re.fullmatch(r'http://www.xbrl.org/inlineXBRL/transformation/\d{4}-\d{2}-\d{2}',
                                namespaces.get('ixt', ''))):
        raise ValueError('SEC inline accounting or transformation namespace differs')
    if any(key.startswith('xmlns:') and namespaces.get(key[6:]) != value
           for node in soup.find_all(True) for key, value in node.attrs.items()):
        raise ValueError('local namespace replacement in SEC inline shares')
    if sha256(raw).hexdigest() != source['document_sha256']:
        raise ValueError('inline statement raw bytes differ from sealed SEC filing')
    raw_claims = {}
    for name in ('dei:EntityRegistrantName', 'dei:EntityCentralIndexKey',
                 'dei:DocumentType', 'dei:DocumentPeriodEndDate'):
        raw_claims[name] = {node.get_text(' ', strip=True) for node in soup.find_all('ix:nonnumeric')
                            if node.get('name') == name}
        if len(raw_claims[name]) != 1:
            raise ValueError('unique SEC inline issuer, CIK, form and period claims required')
    registrant = next(iter(raw_claims['dei:EntityRegistrantName']))
    raw_cik = next(iter(raw_claims['dei:EntityCentralIndexKey']))
    form = next(iter(raw_claims['dei:DocumentType']))
    period_text = ' '.join(next(iter(raw_claims['dei:DocumentPeriodEndDate'])).split())
    period_text = re.sub(r'\s*,\s*', ', ', period_text)
    try:
        period = datetime.strptime(period_text, '%B %d, %Y').date()
    except ValueError as exc:
        raise ValueError('SEC inline document period is not an explicit calendar date') from exc
    if (not _same_sec_inline_issuer(registrant, issuer)
            or not re.fullmatch(r'[0-9]{1,10}', raw_cik) or raw_cik.zfill(10) != cik
            or form != source['metadata']['form'] or period != on):
        raise ValueError('SEC inline registrant, CIK, form or period differs from selected filing')
    titles = {' '.join(node.get_text(' ', strip=True).split()) for node in soup.find_all('ix:nonnumeric')
              if node.get('name') == 'dei:Security12bTitle'}
    if len(titles) != 1:
        raise ValueError('unique SEC common-share listing title required')
    share_title = next(iter(titles))
    if not re.fullmatch(r'Common Stock(?:, (?:\$[0-9]+(?:\.[0-9]+)? par value(?: per share)?'
                        r'|par value \$[0-9]+(?:\.[0-9]+)? per share))?',
                        share_title, re.I):
        listed_class = re.fullmatch(
            r'Class ([A-Z]) (ordinary shares|common stock), par value \$[0-9]+(?:\.[0-9]+)? per share',
            share_title, re.I)
        if listed_class is None:
            raise ValueError('SEC listed title is another share class or instrument')
        return _listed_class_capital(source, tagged_documents, soup, nodes + capital_nodes,
            cik, accession, on, issuer, share_title, listed_class)
    context_nodes, unit_nodes = soup.find_all('xbrli:context'), soup.find_all('xbrli:unit')
    contexts = {ctx.get('id'): ctx for ctx in context_nodes}
    units = {unit.get('id'): unit for unit in unit_nodes}
    fact_ids = [node.get('id') for node in nodes]
    if (len(contexts) != len(context_nodes) or len(units) != len(unit_nodes)
            or len(set(fact_ids)) != len(fact_ids)):
        raise ValueError('duplicate SEC inline context, unit or common-share fact identity')
    eligible = []
    for node in nodes:
        context = contexts.get(node.get('contextref'))
        if context is None:
            raise ValueError('inline common-share fact context missing')
        entity = context.find('xbrli:entity')
        period_node = context.find('xbrli:period')
        instant = period_node.find('xbrli:instant') if period_node else None
        identifier = entity.find('xbrli:identifier') if entity else None
        segment = entity.find('xbrli:segment') if entity else None
        members = segment.find_all('xbrldi:explicitmember') if segment else []
        if (entity is None or period_node is None or identifier is None or segment is None
                or len(context.find_all('xbrli:entity')) != 1
                or len(context.find_all('xbrli:period')) != 1
                or len(context.find_all('xbrli:instant')) != 1
                or len(context.find_all('xbrli:identifier')) != 1
                or len(context.find_all('xbrli:segment')) != 1
                or [child.name for child in context.find_all(recursive=False)]
                    != ['xbrli:entity', 'xbrli:period']
                or [child.name for child in entity.find_all(recursive=False)]
                    != ['xbrli:identifier', 'xbrli:segment']
                or [child.name for child in period_node.find_all(recursive=False)]
                    != ['xbrli:instant']
                or context.find(['xbrli:scenario', 'xbrldi:typedmember']) is not None):
            raise ValueError('opening common-share context has ambiguous issuer, period or dimensions')
        if instant is None:
            raise ValueError('inline common-share fact must be an instant, not a weighted period')
        if instant.get_text(strip=True) != on.isoformat():
            continue
        if (identifier is None or identifier.get('scheme') != 'http://www.sec.gov/CIK'
                or identifier.get_text(strip=True) != cik or segment is None
                or len(segment.find_all(True)) != 1 or len(members) != 1
                or members[0].get('dimension') != 'us-gaap:StatementEquityComponentsAxis'
                or members[0].get_text(strip=True) != 'us-gaap:CommonStockMember'):
            raise ValueError('opening common-share fact has different CIK, dimension or share class')
        unit = units.get(node.get('unitref'))
        if (unit is None or unit.find('xbrli:divide') is not None
                or [measure.get_text(strip=True) for measure in unit.find_all('xbrli:measure')]
                    != ['xbrli:shares']):
            raise ValueError('opening common-share fact is not denominated in shares')
        scale, decimals = node.get('scale'), node.get('decimals')
        if (scale not in ('0', '3', '6', '9')
                or not isinstance(decimals, str)
                or not re.fullmatch(r'(?:0|-[1-9]|-1[0-2]|INF)', decimals)
                or node.get('format') != 'ixt:num-dot-decimal' or node.get('sign') is not None
                or not re.fullmatch(_INTEGER, node.get_text(strip=True))):
            raise ValueError('unsupported inline share number, scale or declared precision')
        value = Decimal(node.get_text(strip=True).replace(',', '')) * (Decimal(10) ** int(scale))
        rounding_unit = 1 if decimals == 'INF' else 10 ** -int(decimals)
        if value <= 0 or value != value.to_integral_value() or value % rounding_unit:
            raise ValueError('positive common shares consistent with stated rounding required')
        eligible.append({'value': int(value), 'scale': int(scale), 'decimals': decimals,
                         'context_id': node['contextref'], 'fact_id': node.get('id')})
    if not eligible or len({(row['value'], row['scale'], row['decimals']) for row in eligible}) != 1:
        raise ValueError('one coherent dated common-share count required; no postdated or conflicting duplicate')
    observed = eligible[0]
    tag_id = 'xbrl-' + cik + '-' + accession
    matches = [doc for doc in tagged_documents if doc['id'] == tag_id]
    if len(matches) != 1:
        raise ValueError('same-filing companyfacts comparison required, even when its share tag is absent')
    tagged = matches[0]; tagged_raw = json.loads(tagged['text']); tm = tagged['metadata']
    if (tagged['sha256'] != sha256(tagged['text'].encode()).hexdigest()
            or tagged['url'] != 'https://data.sec.gov/api/xbrl/companyfacts/CIK' + cik + '.json'
            or tagged['published_at'] != source['published_at']
            or tm['emittente_id'] != 'CIK:' + cik or tm['accession'] != accession
            or str(tagged_raw['cik']).zfill(10) != cik
            or not _same_sec_inline_issuer(tagged_raw['issuer'], issuer)):
        raise ValueError('same-filing companyfacts identity differs from SEC inline statement')
    comparisons = []
    for index, fact in enumerate(tagged_raw['facts']):
        if (fact.get('taxonomy'), fact.get('concept')) != ('us-gaap', 'CommonStockSharesOutstanding'):
            continue
        obs = fact.get('observation') or {}
        if obs.get('end') != on.isoformat():
            continue
        if (fact.get('unit') != 'shares' or type(obs.get('val')) not in (int, float)
                or obs['val'] <= 0 or not float(obs['val']).is_integer()
                or obs.get('accn', '').replace('-', '') != accession
                or obs.get('filed') != source['published_at'] or 'start' in obs):
            raise ValueError('ambiguous same-date companyfacts share comparison')
        comparisons.append({'source_id': tag_id, 'pointer': f'/facts/{index}/observation/val',
                            'value': obs['val'], 'unit': 'shares', 'end': obs['end']})
    conflicts = [fact for fact in comparisons if fact['value'] != observed['value']]
    status = 'missing_same_date_tag' if not comparisons else 'conflict' if conflicts else 'consistent'
    basis = ('primary_inline_without_same_date_tag' if not comparisons else
             'primary_statement_over_conflicting_tags' if conflicts else
             'primary_statement_consistent_with_tags')
    precision_unit = 1 if observed['decimals'] == 'INF' else 10 ** -int(observed['decimals'])
    precision = {'decimals': int(observed['decimals']) if observed['decimals'] != 'INF' else 'INF',
                 'rounding_unit_shares': precision_unit,
                 'exact_legal_count': precision_unit == 1}
    payload = {'issuer': issuer, 'facts': [{'taxonomy': NORMALIZER,
        'concept': 'CommonStockSharesOutstanding', 'entity': issuer, 'share_class': 'Common Stock',
        'value': observed['value'], 'unit': 'shares', 'end': on.isoformat()}],
        'reported_precision': precision,
        'source_proof': {'source_document_id': source['id'],
            'inline_fact_ids': [row['fact_id'] for row in eligible],
            'context_ids': sorted({row['context_id'] for row in eligible}),
            'raw_sha256': source['document_sha256'], 'listed_share_title': share_title},
        'tag_comparison': {'status': status, 'selection_basis': basis,
            'observations': comparisons, 'conflicts': conflicts,
            'limitation': 'A missing or differing same-date companyfacts tag is not silently repaired; '
                          'the inline count retains its reported rounding precision.'}}
    encoded = _json(payload)
    doc = {'id': PREFIX + source['id'], 'document_sha256': source['document_sha256'],
        'url': source['url'], 'published_at': source['published_at'], 'text': encoded,
        'sha256': sha256(encoded.encode()).hexdigest(), 'metadata': {
            'normalizer': NORMALIZER, 'source_document_id': source['id'],
            'comparison_document_id': tag_id, 'entity': issuer, 'report_date': on.isoformat(),
            'share_class': 'Common Stock', 'share_title': share_title,
            'reported_precision': precision}, 'origin': NORMALIZER}
    return {'status': 'ready', 'documents': [doc], 'issues': [],
            'source_disagreement': bool(conflicts)}


def _listed_class_capital(source, tagged_documents, soup, nodes, cik, accession, on,
                          issuer, share_title, listed_class):
    """Derive only the SEC-listed class's dated issued-minus-treasury count."""
    from .balance_detail_evidence import _same_sec_inline_issuer

    letter = listed_class[1].upper()
    share_class = 'Class ' + letter + ' ' + listed_class[2].lower()
    member = 'us-gaap:CommonClass' + letter + 'Member'
    context_nodes, unit_nodes = soup.find_all('xbrli:context'), soup.find_all('xbrli:unit')
    contexts = {node.get('id'): node for node in context_nodes}
    units = {node.get('id'): node for node in unit_nodes}
    fact_ids = [node.get('id') for node in nodes]
    raw_ids = [node.get('id') for node in soup.find_all(id=True)]
    if (None in contexts or len(contexts) != len(context_nodes)
            or None in units or len(units) != len(unit_nodes)
            or None in fact_ids or len(set(fact_ids)) != len(fact_ids)
            or len(set(raw_ids)) != len(raw_ids)):
        raise ValueError('duplicate or missing SEC class context, unit or fact identity')
    groups = {'us-gaap:CommonStockSharesIssued': [],
              'us-gaap:TreasuryStockCommonShares': [],
              'us-gaap:CommonStockSharesOutstanding': []}
    for node in nodes:
        context = contexts.get(node.get('contextref'))
        if context is None:
            raise ValueError('SEC class fact context missing')
        class_members = [item for item in context.find_all('xbrldi:explicitmember')
                         if item.get('dimension') == 'us-gaap:StatementClassOfStockAxis']
        if not any(item.get_text(strip=True) == member for item in class_members):
            continue  # Other share classes never supply this listed-class count.
        if len(context.find_all('xbrldi:explicitmember')) > 1:
            continue  # A more dimensional rollforward is not the class-only balance.
        periods = context.find_all('xbrli:period')
        instants = context.find_all('xbrli:instant')
        if len(periods) != 1 or len(instants) != 1:
            raise ValueError('listed class fact period is ambiguous')
        if instants[0].get_text(strip=True) != on.isoformat():
            continue
        entities = context.find_all('xbrli:entity')
        identifiers = context.find_all('xbrli:identifier')
        segments = context.find_all('xbrli:segment')
        if (len(entities) != 1 or len(identifiers) != 1 or len(segments) != 1
                or len(class_members) != 1 or len(context.find_all('xbrldi:explicitmember')) != 1
                or context.find(['xbrli:scenario', 'xbrldi:typedmember']) is not None
                or [child.name for child in context.find_all(recursive=False)]
                    != ['xbrli:entity', 'xbrli:period']
                or [child.name for child in entities[0].find_all(recursive=False)]
                    != ['xbrli:identifier', 'xbrli:segment']
                or [child.name for child in periods[0].find_all(recursive=False)] != ['xbrli:instant']
                or segments[0].find_all(recursive=False) != class_members
                or identifiers[0].get('scheme') != 'http://www.sec.gov/CIK'
                or identifiers[0].get_text(strip=True).zfill(10) != cik):
            raise ValueError('listed class fact has ambiguous CIK, date or dimensions')
        unit = units.get(node.get('unitref'))
        if (unit is None or unit.find('xbrli:divide') is not None
                or [measure.get_text(strip=True) for measure in unit.find_all('xbrli:measure')]
                    != ['xbrli:shares']):
            raise ValueError('listed class capital fact is not denominated in shares')
        scale, decimals = node.get('scale'), node.get('decimals')
        if (scale not in ('0', '3', '6', '9') or not isinstance(decimals, str)
                or not re.fullmatch(r'(?:0|-[1-9]|-1[0-2]|INF)', decimals)
                or node.get('format') != 'ixt:num-dot-decimal' or node.get('sign') is not None
                or not re.fullmatch(_INTEGER, node.get_text(strip=True))):
            raise ValueError('unsupported listed class capital value, scale or precision')
        value = Decimal(node.get_text(strip=True).replace(',', '')) * (Decimal(10) ** int(scale))
        rounding_unit = 1 if decimals == 'INF' else 10 ** -int(decimals)
        if value < 0 or value != value.to_integral_value() or value % rounding_unit:
            raise ValueError('listed class capital is not an explicit nonnegative share count')
        groups[node['name']].append({'fact_id': node['id'], 'context_id': node['contextref'],
            'unit_ref': node['unitref'], 'value': int(value), 'unit': 'shares',
            'scale': int(scale), 'decimals': decimals, 'rounding_unit_shares': rounding_unit})
    def unique(concept):
        rows = groups[concept]
        if not rows or len({(row['context_id'], row['unit_ref'], row['value'], row['scale'],
                             row['decimals']) for row in rows}) != 1:
            raise ValueError('unique coherent listed class issued and treasury facts required')
        return rows[0], sorted(row['fact_id'] for row in rows)
    issued, issued_ids = unique('us-gaap:CommonStockSharesIssued')
    treasury, treasury_ids = unique('us-gaap:TreasuryStockCommonShares')
    if (issued['context_id'] != treasury['context_id'] or issued['unit_ref'] != treasury['unit_ref']
            or issued['value'] <= 0 or issued['value'] <= treasury['value']):
        raise ValueError('listed class issued minus treasury must be positive, same-context shares')
    outstanding = issued['value'] - treasury['value']
    direct = groups['us-gaap:CommonStockSharesOutstanding']
    if any(row['value'] != outstanding for row in direct):
        raise ValueError('direct listed-class outstanding fact conflicts with issued less treasury')
    tag_id = 'xbrl-' + cik + '-' + accession
    matches = [doc for doc in tagged_documents if doc['id'] == tag_id]
    if len(matches) != 1:
        raise ValueError('same-filing companyfacts comparison required for listed class')
    tagged = matches[0]; tagged_raw = json.loads(tagged['text']); metadata = tagged['metadata']
    if (tagged['sha256'] != sha256(tagged['text'].encode()).hexdigest()
            or tagged['url'] != 'https://data.sec.gov/api/xbrl/companyfacts/CIK' + cik + '.json'
            or tagged['published_at'] != source['published_at']
            or metadata['emittente_id'] != 'CIK:' + cik or metadata['accession'] != accession
            or str(tagged_raw['cik']).zfill(10) != cik
            or not _same_sec_inline_issuer(tagged_raw['issuer'], issuer)):
        raise ValueError('same-filing companyfacts identity differs from listed class')
    comparisons = []
    for index, fact in enumerate(tagged_raw['facts']):
        if (fact.get('taxonomy'), fact.get('concept')) != ('us-gaap', 'CommonStockSharesOutstanding'):
            continue
        observation = fact.get('observation') or {}
        if observation.get('end') != on.isoformat():
            continue
        if (fact.get('unit') != 'shares' or type(observation.get('val')) not in (int, float)
                or observation['val'] <= 0 or not float(observation['val']).is_integer()
                or observation.get('accn', '').replace('-', '') != accession
                or observation.get('filed') != source['published_at'] or 'start' in observation):
            raise ValueError('ambiguous same-date companyfacts listed-class comparison')
        comparisons.append({'source_id': tag_id, 'pointer': f'/facts/{index}/observation/val',
                            'value': observation['val'], 'unit': 'shares', 'end': observation['end']})
    conflicts = [row for row in comparisons if row['value'] != outstanding]
    status = 'missing_same_date_tag' if not comparisons else 'conflict' if conflicts else 'consistent'
    basis = ('primary_inline_without_same_date_tag' if not comparisons else
             'primary_statement_over_conflicting_tags' if conflicts else
             'primary_statement_consistent_with_tags')
    def operand(row, ids):
        return {'fact_ids': ids, 'raw_fact_pointers': ['//*[@id="' + ident + '"]' for ident in ids],
                'context_id': row['context_id'], 'value': row['value'], 'unit': row['unit'],
                'scale': row['scale'], 'decimals': row['decimals']}
    calculation = {'operation': 'issued_minus_treasury',
        'issued': operand(issued, issued_ids), 'treasury': operand(treasury, treasury_ids)}
    def precision(row):
        return {'decimals': row['decimals'], 'rounding_unit_shares': row['rounding_unit_shares']}
    uncertainty = sum(Decimal(0) if row['decimals'] == 'INF' else
                      Decimal(row['rounding_unit_shares']) / 2 for row in (issued, treasury))
    reported_precision = {'calculation': 'issued_minus_treasury',
        'operands': {'issued': precision(issued), 'treasury': precision(treasury)},
        'maximum_rounding_error_shares': format(uncertainty, 'f'),
        'exact_legal_count': issued['decimals'] == treasury['decimals'] == 'INF'}
    payload = {'issuer': issuer, 'facts': [{'taxonomy': NORMALIZER,
        'concept': 'CommonStockSharesOutstanding', 'entity': issuer, 'share_class': share_class,
        'value': outstanding, 'unit': 'shares', 'end': on.isoformat()}],
        'reported_precision': reported_precision,
        'source_proof': {'source_document_id': source['id'], 'raw_sha256': source['document_sha256'],
            'listed_share_title': share_title, 'calculation': calculation,
            'matching_outstanding_fact_ids': sorted(row['fact_id'] for row in direct)},
        'tag_comparison': {'status': status, 'selection_basis': basis,
            'observations': comparisons, 'conflicts': conflicts,
            'limitation': 'The count is derived from listed-class issued and treasury facts. '
                          'Other classes and postdated cover counts are excluded; exchangeable claims remain separate.'}}
    encoded = _json(payload)
    doc = {'id': PREFIX + source['id'], 'document_sha256': source['document_sha256'],
        'url': source['url'], 'published_at': source['published_at'], 'text': encoded,
        'sha256': sha256(encoded.encode()).hexdigest(), 'metadata': {
            'normalizer': NORMALIZER, 'source_document_id': source['id'],
            'comparison_document_id': tag_id, 'entity': issuer, 'report_date': on.isoformat(),
            'share_class': share_class, 'share_title': share_title,
            'reported_precision': reported_precision}, 'origin': NORMALIZER}
    return {'status': 'ready', 'documents': [doc], 'issues': [],
            'source_disagreement': bool(conflicts)}


def normalize_statement_shares(source, tagged_documents):
    """Recompile from original catalog texts; source dates and hashes stay intact."""
    try:
        meta = source['metadata']; cik, accession, _ = _sec_parts(source['url'])
        cik = cik.zfill(10); on = date.fromisoformat(meta['report_date'])
        text = source['text']; issuer = meta['issuer']
        if (meta['emittente_id'] != 'CIK:' + cik or meta['accession'].replace('-', '') != accession
                or meta['form'] not in ('10-K', '10-Q', '10-K/A', '10-Q/A')
                or not isinstance(issuer, str) or not issuer.strip()
                or on.isoformat() != meta['report_date'] or on > date.fromisoformat(source['published_at'])
                or source['sha256'] != sha256(text.encode()).hexdigest()
                or not re.fullmatch(r'[0-9a-f]{64}', source['id'])
                or source['document_sha256'] != source['id']):
            raise ValueError('statement filing identity, period or text hash differs')
        # Whitespace only: do not strip labels, digits, signs or source qualifiers.
        body = ' '.join(text.split())
        sections = re.findall(r'Consolidated (?:Statements of Financial Condition|Balance Sheets) '
            r'\(In Thousands, Except Share and Per Share Data\) (.*?) '
            r'See notes to consolidated financial statements\.', body, re.I)
        eligible = []
        for section in sections:
            header = re.match(r'('+'|'.join(_MONTHS)+r') ([0-9]{1,2}), ([0-9]{4}) ([0-9]{4}) Assets\b', section, re.I)
            if not header:
                continue
            month = [m.lower() for m in _MONTHS].index(header[1].lower()) + 1
            days = [date(int(header[n]), month, int(header[2])).isoformat() for n in (3, 4)]
            if days[0] != on.isoformat() or days[1] >= days[0]:
                continue
            # Exact wording excludes issued-only, treasury, weighted averages,
            # class-specific counts and numbers scaled by the monetary header.
            rows = re.findall(r'(?:^|[; ])(Common stock, \$[0-9.]+ par value; '+_INTEGER+
                r' shares authorized; ('+_INTEGER+r') shares and ('+_INTEGER+
                r') shares issued and outstanding, respectively)\b', section, re.I)
            if len(rows) != 1 or len(re.findall(r'\bcommon stock\b', section, re.I)) != 1:
                continue
            row, first, second = rows[0]
            literal_row = re.escape(row).replace(r'\ ', r'\s+')
            if (re.search(r'\bclass\s+[a-z0-9]+\b', section, re.I)
                    or not re.search(r'^[ \t]*'+literal_row+r'(?=\s|$)', text, re.I | re.M)):
                continue
            eligible.append((days, row, [int(v.replace(',', '')) for v in (first, second)]))
        if len(eligible) != 1:
            if not eligible:
                inline = _inline_statement_shares(source, tagged_documents, cik, accession, on, issuer)
                if inline is not None:
                    return inline
            raise ValueError('unambiguous unrounded common-share row and ordered comparative dates required')
        days, row, values = eligible[0]
        par_value = re.match(r'Common stock, (\$[0-9.]+) par value;', row, re.I)[1]
        if any(v <= 0 for v in values):
            raise ValueError('positive reported common shares required')
        tag_id = 'xbrl-' + cik + '-' + accession
        matches = [doc for doc in tagged_documents if doc['id'] == tag_id]
        if len(matches) != 1:
            raise ValueError('same-filing companyfacts comparison required')
        tagged = matches[0]; raw = json.loads(tagged['text']); tm = tagged['metadata']
        if (tagged['sha256'] != sha256(tagged['text'].encode()).hexdigest()
                or tagged['url'] != 'https://data.sec.gov/api/xbrl/companyfacts/CIK' + cik + '.json'
                or tagged['published_at'] != source['published_at']
                or tm['emittente_id'] != 'CIK:' + cik or tm['accession'] != accession
                or str(raw['cik']).zfill(10) != cik or not same_entity_name(raw['issuer'], issuer)):
            raise ValueError('tag comparison source identity differs from statement')
        observations = []
        for index, fact in enumerate(raw['facts']):
            if (fact.get('taxonomy'), fact.get('concept')) != ('us-gaap', 'CommonStockSharesOutstanding'):
                continue
            obs = fact['observation']
            if obs.get('end') != on.isoformat():
                continue
            if (fact['unit'] != 'shares' or type(obs['val']) not in (int, float)
                    or obs['val'] <= 0 or not float(obs['val']).is_integer()
                    or obs.get('accn', '').replace('-', '') != accession or obs.get('filed') != source['published_at']
                    or 'start' in obs):
                raise ValueError('ambiguous tagged opening common shares')
            observations.append({'source_id': tag_id, 'pointer': f'/facts/{index}/observation/val',
                                 'value': obs['val'], 'unit': 'shares', 'end': obs['end']})
        if not observations:
            raise ValueError('same-date common-share tag comparison unavailable')
        conflicts = [obs for obs in observations if obs['value'] != values[0]]
        payload = {'issuer': issuer, 'facts': [
            {'taxonomy': NORMALIZER, 'concept': 'CommonStockSharesOutstanding', 'entity': issuer,
             'share_class': 'Common Stock', 'value': value, 'unit': 'shares', 'end': day}
            for day, value in zip(days, values)],
            'source_proof': {'source_document_id': source['id'], 'statement_row': row,
                'ordered_dates': days, 'units_basis': 'In Thousands, Except Share and Per Share Data'},
            'tag_comparison': {'status': 'conflict' if conflicts else 'consistent',
                'observations': observations, 'conflicts': conflicts,
                'limitation': 'A source disagreement is not an issuer correction. Explicit source selection must remain disclosed.'}}
        encoded = _json(payload)
        doc = {'id': PREFIX + source['id'], 'document_sha256': source['document_sha256'],
            'url': source['url'], 'published_at': source['published_at'], 'text': encoded,
            'sha256': sha256(encoded.encode()).hexdigest(), 'metadata': {
                'normalizer': NORMALIZER, 'source_document_id': source['id'], 'comparison_document_id': tag_id,
                'entity': issuer, 'report_date': on.isoformat(), 'share_class': 'Common Stock',
                'share_title': 'Common Stock, ' + par_value + ' par value'}, 'origin': NORMALIZER}
        return {'status': 'ready', 'documents': [doc], 'issues': [], 'source_disagreement': bool(conflicts)}
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def is_statement_shares(document):
    metadata = document.get('metadata')
    return (str(document.get('id', '')).startswith(PREFIX)
            or isinstance(metadata, dict) and metadata.get('normalizer') == NORMALIZER)


def statement_share_proof(driver, item, evidence, unit, period, expected_entity, scale):
    """Explicitly select a primary statement; never silently repair a bad tag."""
    try:
        if driver not in ('shares', 'capital.shares_m') or len(evidence) != 1 or not is_statement_shares(evidence[0]):
            raise ValueError('statement shares source allowed only for a single common-share observation')
        if any(k in item for k in ('evidence_pointer', 'quoted_value', 'quoted_unit', 'evidence_quote', 'period_quote', 'facts')):
            raise ValueError('statement shares require explicit source selection, without mixed proof fields')
        calc = item.get('calculation'); raw = json.loads(evidence[0]['text'])
        if (not isinstance(calc, dict) or set(calc) != {'type', 'fact_index', 'selection_basis', 'acknowledged_conflicts'}
                or calc['type'] != 'statement_shares' or type(calc['fact_index']) is not int or calc['fact_index'] != 0):
            raise ValueError('explicit opening statement share selection required')
        comparison = raw['tag_comparison']
        basis = ('primary_inline_without_same_date_tag' if comparison['status'] == 'missing_same_date_tag'
                 else 'primary_statement_over_conflicting_tags' if comparison['conflicts']
                 else 'primary_statement_consistent_with_tags')
        if calc['selection_basis'] != basis or calc['acknowledged_conflicts'] != comparison['conflicts']:
            raise ValueError('all conflicting share observations must be acknowledged explicitly')
        fact = raw['facts'][0]
        proof = {'value': item.get('value'), 'evidence_ids': item.get('evidence_ids'),
            'evidence_pointer': {'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'},
            'quoted_value': fact['value'], 'quoted_unit': fact['unit']}
        return structured_fact_proof(proof, evidence, unit, period, scale=scale,
            expected_entity=expected_entity, allowed_concepts={(NORMALIZER, 'CommonStockSharesOutstanding')})
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return str(exc)


def share_conflict_disclosure(evidence):
    parts = []
    for doc in evidence:
        if is_statement_shares(doc):
            raw = json.loads(doc['text']); fact = raw['facts'][0]
            precision = raw.get('reported_precision')
            calculation = raw.get('source_proof', {}).get('calculation')
            if isinstance(calculation, dict) and calculation.get('operation') == 'issued_minus_treasury':
                issued, treasury = calculation['issued'], calculation['treasury']
                parts.append('SOURCE CALCULATION: '+fact['share_class']+' at '+fact['end']+': '
                    +str(issued['value'])+' issued minus '+str(treasury['value'])+' treasury = '
                    +str(fact['value'])+' shares; original SEC inline fact IDs '
                    +','.join(issued['fact_ids'])+' and '+','.join(treasury['fact_ids'])
                    +'. This is a derived listed-class count, not a reported outstanding tag. ')
                if not precision['exact_legal_count']:
                    parts.append('REPORTED PRECISION: issued and treasury operand decimals '
                        +str(precision['operands']['issued']['decimals'])+' and '
                        +str(precision['operands']['treasury']['decimals'])
                        +'; maximum rounding error '+precision['maximum_rounding_error_shares']
                        +' shares; not an exact legal count. ')
            elif isinstance(precision, dict) and not precision.get('exact_legal_count'):
                parts.append('REPORTED PRECISION: '+str(fact['value'])+' shares at '+fact['end']
                    +' rounded to '+str(precision['rounding_unit_shares'])
                    +' shares; not an exact legal count. ')
            if raw['tag_comparison']['status'] == 'missing_same_date_tag':
                parts.append('SOURCE COVERAGE: same-date SEC companyfacts common-share tag absent; '
                    'the selected raw inline statement is independently proved, with no tag imputed. ')
            if raw['tag_comparison']['conflicts']:
                parts.append('SOURCE DISAGREEMENT: selected primary statement '+str(fact['value'])+' '+fact['unit']+
                    ' at '+fact['end']+'; conflicting SEC tags '+_json(raw['tag_comparison']['conflicts'])+
                    '. This selection is not an issuer correction or PM approval. ')
    return ''.join(parts)


def share_selection_problem(item, catalog, entity, period, share_class):
    relevant = []
    for doc in catalog.values():
        if not is_statement_shares(doc):
            continue
        meta = doc['metadata']
        if meta['report_date'] != period or not same_entity_name(meta['entity'], entity):
            continue
        raw = json.loads(doc['text'])
        if doc['id'] in item.get('evidence_ids', []):
            quoted_class = share_class.casefold()
            valid = quoted_class in (meta['share_class'].casefold(), meta['share_title'].casefold())
            if not valid and meta['share_class'] != 'Common Stock':
                quoted_class = ' '.join(share_class.split()).casefold()
                valid = quoted_class in (' '.join(meta['share_class'].split()).casefold(),
                                         ' '.join(meta['share_title'].split()).casefold())
            if not valid:
                return 'statement share class differs from the model perimeter'
        # _catalog has recompiled every observation from its own original bytes.
        # Two receipts can differ in raw-byte identity while proving the same
        # statement. Ignore only that identity; keep URL, dates, metadata, every
        # fact, the comparative row and the full tag disagreement identical.
        raw['source_proof'].pop('source_document_id')
        identity = _json({'url': doc['url'], 'published_at': doc['published_at'],
            'available_at': doc.get('available_at'), 'availability_basis': doc.get('availability_basis'),
            'metadata': {key: value for key, value in meta.items() if key != 'source_document_id'},
            'body': raw})
        relevant.append((doc['id'], identity, bool(raw['tag_comparison']['conflicts'])))
    error = 'conflicting share sources: explicit reconciled statement selection required'
    if relevant and any(identity != relevant[0][1] for _, identity, _ in relevant):
        return error
    if any(conflict for _, _, conflict in relevant) and not any(
            item.get('evidence_ids') == [ident] for ident, _, _ in relevant):
        return error
    return None
