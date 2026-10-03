"""Deterministic source-section selection for a recognized English IFRS layout.

No economic inputs or completeness verdicts are produced. Call after the common
source catalog has validated provenance. Unknown/ambiguous layouts retain the
whole source with an explicit issue; the normal context guard can still stop it.
This selector is deliberately separate from spending and paid-response replay.
"""
from datetime import date
from copy import deepcopy
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re


POLICY = 'ifrs_note_sections_v1'
_REQUIRED_POLICIES = frozenset({
    'property, plant and equipment', 'intangible assets', 'inventories',
    'trade and other receivables', 'current and deferred income tax', 'provisions',
    'trade and other payables', 'revenue recognition'})
_SELECTED_POLICIES = _REQUIRED_POLICIES | {
    'right-of-use assets and lease liabilities', 'impairment of non-financial assets',
    'cash and cash equivalents', 'equity', 'cost of sales and other selling expenses'}
_SELECTED_NOTES = frozenset({
    'segment information', 'receivables non-current, net', 'inventories, net',
    'receivables and prepayments, net', 'current tax assets and liabilities',
    'trade receivables, net', 'other liabilities', 'non-current provisions',
    'current allowances and provisions', 'cash flow disclosures'})
_REQUIRED_NOTES = {'segment information', 'inventories, net', 'trade receivables, net', 'cash flow disclosures'}
_MONTHS = {name: number for number, name in enumerate(
    ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'), 1)}
_PERIOD = re.compile(r'For\s+the\s+(?:three|six|nine|twelve)-month\s+period\s+ended\s+'
                     r'([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s*[-\u2013]\s*all\s+amounts', re.I)


def _line_pattern(title):
    return r'^[^\S\n]*' + r'[^\S\n]+'.join(re.escape(word) for word in title.split()) + r'[^\S\n]*$'


def _anchor(body, title):
    matches = list(re.finditer(_line_pattern(title), body, re.M | re.I))
    if len(matches) != 1:
        raise ValueError('missing or ambiguous heading: ' + title)
    return matches[0].start()


def _sections(body, start, end, *, numbered):
    prefix = r'[0-9]{1,3}' if numbered else '[A-Z]'
    pattern = re.compile(r'^(' + prefix + r')[^\S\n]{2,}([^\n]{2,150})$', re.M)
    matches = [m for m in pattern.finditer(body, start, end) if re.search(r'[A-Za-z]', m[2])]
    labels = [int(m[1]) if numbered else ord(m[1]) - ord('A') + 1 for m in matches]
    if not labels or labels != list(range(1, len(labels) + 1)):
        raise ValueError('missing, duplicate or unordered section labels')
    titles = [' '.join(m[2].split()).casefold() for m in matches]
    if len(set(titles)) != len(titles):
        raise ValueError('ambiguous repeated section title')
    return [(titles[i], m.start(), matches[i+1].start() if i+1 < len(matches) else end)
            for i, m in enumerate(matches)]


def _annual_ranges(body):
    general = _anchor(body, 'I. GENERAL INFORMATION')
    accounting = _anchor(body, 'II. ACCOUNTING POLICIES')
    risk = _anchor(body, 'III. FINANCIAL RISK MANAGEMENT')
    notes = _anchor(body, 'IV. OTHER NOTES TO THE CONSOLIDATED FINANCIAL STATEMENTS')
    if not general < accounting < risk < notes:
        raise ValueError('annual section boundaries out of order')
    policies = _sections(body, accounting, risk, numbered=False)
    financial = _sections(body, notes, len(body), numbered=True)
    if not _REQUIRED_POLICIES <= {title for title, _, _ in policies}:
        raise ValueError('required accounting-policy headings not identified')
    if not _REQUIRED_NOTES <= {title for title, _, _ in financial}:
        raise ValueError('required financial-note headings not identified')
    ranges = [(general, accounting, 'issuer_scope')]
    ranges.extend((start, end, 'accounting_policy') for title, start, end in policies if title in _SELECTED_POLICIES)
    tail = next((start for title, start, _ in financial if title == 'business combinations'), None)
    ranges.extend((start, end, 'financial_note') for title, start, end in financial
                  if title in _SELECTED_NOTES and (tail is None or start < tail))
    if tail is not None:
        ranges.append((tail, len(body), 'business_and_recent_developments'))
    return ranges


def _management_prefix(document, documents, as_of):
    metadata = document.get('metadata') or {}
    if metadata.get('form') != '6-K' or metadata.get('evidence_role') != 'foreign_current_report':
        return None
    if any((d.get('metadata') or {}).get('normalizer') == 'statement_tables_v1'
           and d['metadata'].get('source_document_id') == document['id'] for d in documents):
        return None  # The independently normalized primary report must stay whole.
    issuer = metadata.get('emittente_id')
    if not issuer:
        return None
    body = document['text']
    outlook = list(re.finditer(_line_pattern('Market Background and Outlook'), body, re.M | re.I))
    if len(outlook) != 1:
        return None
    statements = re.finditer(_line_pattern('Consolidated Condensed Interim Financial Statements'), body, re.M | re.I)
    for header in statements:
        period = _PERIOD.search(body, header.end(), header.end() + 600)
        if period is None or outlook[0].start() >= header.start():
            continue
        try:
            report_date = date(int(period[3]), _MONTHS[period[1].title()], int(period[2])).isoformat()
        except (KeyError, ValueError):
            continue
        if report_date > as_of:
            continue
        peers = []
        for source in documents:
            peer = source.get('metadata') or {}
            if (source['id'] == document['id'] or peer.get('emittente_id') != issuer
                    or peer.get('form') != '6-K' or peer.get('report_date') != report_date
                    or peer.get('perimetro') != 'consolidato'):
                continue
            normalized = [d for d in documents if (d.get('metadata') or {}).get('normalizer') == 'statement_tables_v1'
                          and d['metadata'].get('source_document_id') == source['id']
                          and d['metadata'].get('emittente_id') == issuer
                          and d['metadata'].get('report_date') == report_date]
            if len(normalized) == 1:
                peers.append(source['id'])
        if len(peers) == 1:
            return header.start(), report_date, peers[0]
        return None  # Do not skip an unresolved first dated statement to choose a later one.
    return None


def select_fcff_note_sections(dossier):
    """Build an exhaustive manifest without issuer IDs, dates or numeric offsets in rules.

    An older annual may be narrowed only with a same-issuer, later interim source
    retained whole and a management report whose statement header proves that
    period. Event dates never substitute for accounting periods. This qualifies
    document selection, not the sufficiency of those sources for a valuation.
    """
    if dossier.get('method_id') != 'operating_fcff':
        raise ValueError('section policy supports FCFF only')
    as_of = date.fromisoformat(dossier['as_of']).isoformat()
    documents = dossier['documents']
    ids, narrative = set(), []
    for document in documents:
        ident, body = document.get('id'), document.get('text')
        if not isinstance(ident, str) or not ident or ident in ids or not isinstance(body, str):
            raise ValueError('unique document IDs and source text required')
        ids.add(ident)
        if document.get('sha256') != sha256(body.encode('utf-8')).hexdigest():
            raise ValueError('source text SHA-256 mismatch: ' + ident)
        try:
            json.loads(body)
        except ValueError:
            narrative.append(document)
    management = {d['id']: pair for d in narrative if (pair := _management_prefix(d, documents, as_of)) is not None}
    manifest, issues, applied = [], [], False
    for document in narrative:
        ident, body, metadata = document['id'], document['text'], document.get('metadata') or {}
        ranges = [(0, len(body), 'complete_source')]
        rationale = 'Whole narrative retained; repeated blank lines only are compacted.'
        limitation = 'Source presence does not certify economic sufficiency.'
        if ident in management:
            end, period, primary = management[ident]
            ranges = [(0, end, 'complete_management_narrative')]
            rationale = 'Management narrative and outlook retained before the dated statement appendix; complete current source ' + primary + ' has the same issuer and reporting period ' + period + '.'
            limitation = 'The management financial/APM appendices are excluded. The separate current source is retained whole, not asserted byte-identical or economically sufficient.'
        elif metadata.get('form') == '20-F':
            try:
                if not isinstance(metadata.get('report_date'), str):
                    raise ValueError('annual reporting period missing')
                report_date = date.fromisoformat(metadata['report_date']).isoformat()
                current = [pair for d in narrative if d['id'] in management
                           and (d.get('metadata') or {}).get('emittente_id') == metadata.get('emittente_id')
                           and report_date < (pair := management[d['id']])[1]]
                if not current:
                    raise ValueError('later same-issuer current notes and dated management report not qualified')
                ranges = _annual_ranges(body)
                rationale = 'Recognized annual accounting policies and complete relevant note sections, with later same-issuer interim notes and management narrative retained separately; no economic figures supplied by the selector.'
                limitation = 'Annual business/risk/ESG text, financial-risk section and unselected financial notes are omitted. Later sources do not prove those omissions unchanged or immaterial. Request missing evidence or leave the driver incomplete; omission never means zero.'
            except (KeyError, ValueError) as exc:
                reason = str(exc)
                issues.append({'source_id': ident, 'code': 'section_selection_unavailable', 'reason': reason})
                limitation = 'Whole annual retained because automatic section selection failed: ' + reason
        applied |= ranges != [(0, len(body), 'complete_source')]
        excerpts = [{'theme': theme, 'char_start': start, 'char_end_exclusive': end,
                     'excerpt_sha256_utf8': sha256(body[start:end].encode('utf-8')).hexdigest()}
                    for start, end, theme in ranges]
        manifest.append({'source_id': ident, 'original_text_sha256_utf8': document['sha256'],
                         'original_document_sha256_bytes': document.get('document_sha256'),
                         'url': document.get('url'), 'published_at': document.get('published_at'),
                         'layout_projection': 'collapse_blank_lines_v1', 'excerpts': excerpts,
                         'selection_rationale': POLICY + ': ' + rationale, 'coverage_limitations': limitation})
    return {'policy': POLICY, 'manifest': manifest, 'issues': issues, 'selection_applied': applied,
            'semantic_coverage_certified': False}


IFRS_CONTEXT_POLICY = 'ifrs_fcff_source_pinned_v1'


class _SourceHashMismatch(ValueError):
    pass


def _canonical_day(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('noncanonical source date')
    return parsed


def _ifrs_source_set(dossier):
    """Recognize one selected annual/current/comparative printed-statement set."""
    from .statement_table_evidence import NORMALIZER, PREFIX

    cutoff = _canonical_day(dossier['as_of'])
    documents = dossier['documents']
    by_id = {}
    for document in documents:
        ident, body = document.get('id'), document.get('text')
        if not isinstance(ident, str) or not ident or ident in by_id or not isinstance(body, str):
            raise ValueError('unique source IDs and source text required')
        if document.get('sha256') != sha256(body.encode('utf-8')).hexdigest():
            raise _SourceHashMismatch('source text SHA-256 mismatch: ' + ident)
        by_id[ident] = document
    selection = (dossier.get('document_acquisition') or {}).get('selection') or {}
    if selection.get('policy') != 'opening_plus_latest_annual_and_available_prior_year_comparative':
        raise ValueError('selected annual/current/comparative policy absent')
    role_keys = ('annual_document_id', 'selected_document_id', 'comparative_document_id')
    role_ids = [selection.get(key) for key in role_keys]
    selected = selection.get('selected_document_ids')
    if (any(not isinstance(ident, str) or ident not in by_id for ident in role_ids)
            or len(set(role_ids)) != 3 or not isinstance(selected, list)
            or len(selected) != len(set(selected)) or not set(role_ids) <= set(selected)):
        raise ValueError('selected primary roles absent or ambiguous')
    annual, current, prior = (by_id[ident] for ident in role_ids)
    metadata = [document.get('metadata') or {} for document in (annual, current, prior)]
    issuers = {row.get('emittente_id') for row in metadata}
    if (len(issuers) != 1 or not re.fullmatch(r'CIK:\d{10}', next(iter(issuers)) or '')
            or [row.get('form') for row in metadata] != ['20-F', '6-K', '6-K']
            or any(row.get('perimetro') != 'consolidato' for row in metadata[1:])):
        raise ValueError('IFRS selected issuer/form/perimeter mismatch')
    periods = [_canonical_day(row['report_date']) for row in metadata]
    annual_day, current_day, prior_day = periods
    if (not prior_day < annual_day < current_day
            or prior_day.year + 1 != current_day.year
            or prior_day.strftime('%m-%d') != current_day.strftime('%m-%d')
            or selection.get('opening_date') != current_day.isoformat()):
        raise ValueError('IFRS annual/current/comparative periods do not reconcile')
    for document, period in zip((annual, current, prior), periods):
        published = _canonical_day(document['published_at'])
        if published < period or published > cutoff:
            raise ValueError('selected source not published by cutoff')
    normalized = {}
    for primary, period in zip((annual, current, prior), periods):
        meta = primary['metadata']
        accession = str(meta.get('accession') or '').replace('-', '')
        matches = [document for document in documents if
            (candidate := document.get('metadata') or {}).get('normalizer') == NORMALIZER
            and candidate.get('source_document_id') == primary['id']
            and candidate.get('emittente_id') == meta['emittente_id']
            and candidate.get('report_date') == period.isoformat()
            and candidate.get('accession') == accession
            and candidate.get('scope') == 'consolidated'
            and document['id'] == PREFIX + primary['id']
            and document.get('document_sha256') == primary['id']
            and document.get('url') == primary.get('url')
            and document.get('published_at') == primary.get('published_at')]
        if len(matches) != 1:
            raise ValueError('source-bound normalized statement not unique')
        normalized[primary['id']] = matches[0]
    body = current['text']
    outlook = list(re.finditer(_line_pattern('Market Background and Outlook'), body, re.M | re.I))
    headers = list(re.finditer(_line_pattern('Consolidated Condensed Interim Financial Statements'),
                               body, re.M | re.I))
    if len(outlook) != 1 or not headers:
        raise ValueError('current outlook and financial statement order unresolved')
    dated = []
    for header in headers:
        match = _PERIOD.search(body, header.end(), header.end() + 600)
        if match is None and header.start() < outlook[0].start():
            continue  # An undated contents heading cannot identify a reporting period.
        if (match is None or match[1].title() not in _MONTHS
                or date(int(match[3]), _MONTHS[match[1].title()], int(match[2])) != current_day):
            raise ValueError('current dated statement header differs from selected period')
        dated.append(header.start())
    if not dated or outlook[0].start() >= min(dated):
        raise ValueError('current outlook and dated statements not ordered')
    return annual, current, prior, normalized


def _annual_body_heading(body, title):
    """Require a prose-bearing body heading, not TOC/page or index occurrences."""
    candidates = []
    for heading in re.finditer(_line_pattern(title), body, re.M | re.I):
        lines = [line.strip() for line in body[heading.end():heading.end() + 1800].splitlines()
                 if line.strip()]
        if (not lines or re.fullmatch(r'(?:\d{1,4}|\d{1,2}\.[A-Z]|Item\s+\d{1,2})', lines[0], re.I)):
            continue
        if any(len(line) >= 90 and re.search(r'[.!?](?:\s|$)', line) for line in lines[:3]):
            candidates.append(heading.start())
    if len(candidates) != 1:
        raise ValueError('annual business/risk/management heading missing or ambiguous: ' + title)
    return candidates[0]


def _annual_growth_ranges(body):
    ranges = _annual_ranges(body)
    risk = _annual_body_heading(body, 'RISK FACTORS')
    business = _annual_body_heading(body, 'INFORMATION ON THE COMPANY')
    management = _annual_body_heading(body, 'Operating and Financial Review and Prospects')
    next_section = _annual_body_heading(body, 'Major Shareholders and Related Party Transactions')
    general = _anchor(body, 'I. GENERAL INFORMATION')
    if not risk < business < management < next_section < general:
        raise ValueError('annual business/risk/management sections out of order')
    return [(risk, business, 'annual_business_risks'),
            (business, management, 'annual_business_segments'),
            (management, next_section, 'annual_management_review'), *ranges]


def _verified_ifrs_ttm(context, annual, current, prior, normalized):
    """Reprove the completed opening against these three exact printed sources."""
    from .input_preparation import _fact_proof

    model = (context.get('completed_plan') or {}).get('model') or {}
    try:
        item = model['historical_revenue']
        opening = model['calendar']['value']['valuation_date']
        currency = model['quotation']['value']['financial_currency']
        entity = model['perimeter']['value']['entity']
        calculation = item['calculation']
        terms = calculation['terms']
        ids = [normalized[primary['id']]['id'] for primary in (annual, current, prior)]
        if (item.get('kind') != 'historical' or calculation.get('operation') != 'trailing_twelve_months'
                or set(terms) != {'annual', 'current_ytd', 'prior_ytd'}
                or any(terms[name].get('evidence_ids') != [ident] for name, ident in
                       zip(('annual', 'current_ytd', 'prior_ytd'), ids))
                or set(item['evidence_ids']) != set(ids) or len(item['evidence_ids']) != len(ids)
                or opening != current['metadata']['report_date'] or not isinstance(currency, str)
                or not isinstance(entity, str)):
            return False
        evidence = [normalized[primary['id']] for primary in (annual, current, prior)]
        return _fact_proof('historical_revenue', item, evidence, currency + ' million', opening,
                           expected_entity=entity) is None
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _ifrs_companion_excerpts(companion, primary):
    """Remove only long whitespace-equal blocks; pin both original byte sources."""
    left = list(re.finditer(r'\S+', companion['text']))
    right = list(re.finditer(r'\S+', primary['text']))
    if not left or not right:
        return [(0, len(companion['text']), 'complete_source')], []
    blocks = SequenceMatcher(None, [m.group() for m in left],
                             [m.group() for m in right], autojunk=False).get_matching_blocks()
    omitted, receipts = [], []
    for block in blocks:
        # Keep context around each discrepancy, including every changed number.
        if block.size < 128 or block.size <= 32:
            continue
        first, last = block.a + 16, block.a + block.size - 16
        p_first, p_last = block.b + 16, block.b + block.size - 16
        start, end = left[first].start(), left[last - 1].end()
        p_start, p_end = right[p_first].start(), right[p_last - 1].end()
        excerpt, source = companion['text'][start:end], primary['text'][p_start:p_end]
        if len(excerpt.encode('utf-8')) < 512:
            continue
        normalized = ' '.join(m.group() for m in left[first:last])
        if normalized != ' '.join(m.group() for m in right[p_first:p_last]):
            continue
        omitted.append((start, end))
        receipts.append({'companion_source_id': companion['id'],
            'companion_char_start': start, 'companion_char_end_exclusive': end,
            'companion_excerpt_sha256_utf8': sha256(excerpt.encode('utf-8')).hexdigest(),
            'primary_source_id': primary['id'], 'primary_char_start': p_start,
            'primary_char_end_exclusive': p_end,
            'primary_excerpt_sha256_utf8': sha256(source.encode('utf-8')).hexdigest(),
            'whitespace_normalized_sha256_utf8': sha256(normalized.encode('utf-8')).hexdigest(),
            'matched_tokens': last - first})
    if not omitted:
        return [(0, len(companion['text']), 'complete_source')], []
    retained, at = [], 0
    for start, end in omitted:
        if start > at:
            retained.append((at, start, 'distinct_companion_text'))
        at = end
    if at < len(companion['text']):
        retained.append((at, len(companion['text']), 'distinct_companion_text'))
    if not retained:
        return [(0, len(companion['text']), 'complete_source')], []
    return retained, receipts


def select_ifrs_fcff_context(dossier, context, contract):
    """Additional source-pinned IFRS view; old requests and selector remain unchanged."""
    stage = contract.get('preparation_stage') or {}
    scope, names = stage.get('scope'), stage.get('drivers')
    if dossier.get('method_id') != 'operating_fcff' or contract.get('method_id') != 'operating_fcff':
        return None
    nwc = scope == 'model' and names == ['opening_nwc']
    forecast = (scope in ('bear', 'base', 'bull', 'forecast')
                or scope == 'model' and isinstance(names, list) and names
                and not set(names) <= {'perimeter', 'calendar', 'quotation', 'historical_revenue',
                                       'opening_nwc', 'shares'})
    if not nwc and not forecast:
        return None
    try:
        annual, current, prior, normalized = _ifrs_source_set(dossier)
        if not _verified_ifrs_ttm(context, annual, current, prior, normalized):
            return None
        annual_ranges = (_annual_ranges(annual['text']) if nwc else
                         _annual_growth_ranges(annual['text']))
    except _SourceHashMismatch:
        raise
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    documents = dossier['documents']
    cutoff = _canonical_day(dossier['as_of'])
    manifest, deduplicated = [], []
    for document in documents:
        body = document['text']
        try:
            json.loads(body)
            continue
        except ValueError:
            pass
        ident, meta = document['id'], document.get('metadata') or {}
        ranges = annual_ranges if ident == annual['id'] else [(0, len(body), 'complete_source')]
        if (ident not in (annual['id'], current['id'], prior['id'])
                and meta.get('evidence_role') == 'foreign_current_report'
                and meta.get('form') == '6-K'
                and meta.get('emittente_id') == current['metadata']['emittente_id']
                and meta.get('event_date') == current['metadata']['report_date']
                and _canonical_day(document['published_at']) <= cutoff):
            ranges, removed = _ifrs_companion_excerpts(document, current)
            deduplicated.extend(removed)
        manifest.append({'source_id': ident, 'original_text_sha256_utf8': document['sha256'],
            'original_document_sha256_bytes': document.get('document_sha256'),
            'url': document.get('url'), 'published_at': document.get('published_at'),
            'layout_projection': 'collapse_blank_lines_v1',
            'excerpts': [{'theme': theme, 'char_start': start, 'char_end_exclusive': end,
                          'excerpt_sha256_utf8': sha256(body[start:end].encode('utf-8')).hexdigest()}
                         for start, end, theme in ranges],
            'selection_rationale': IFRS_CONTEXT_POLICY + ': recognized selected primaries and original char ranges.',
            'coverage_limitations': ('Accounting policies and identified notes only; annual business and risks require '
                'the broader forecast view.' if nwc and ident == annual['id'] else
                'Exact duplicate companion text may be omitted only where mapped to the complete current primary; '
                'all unmatched text remains.' if ident != annual['id'] else
                'Annual business, risks and management spans retained, but source presence alone does not certify '
                'economic sufficiency. Unselected annual sections are not zero or unchanged.')})
    from .preparation_view import select_stage_view
    view = select_stage_view(dossier, 'forecast', excerpt_manifest=manifest)
    for key, value in context.items():
        if key not in ('documents', 'stage_view', 'acquired_sources'):
            view[key] = deepcopy(value)
    metadata_only = []
    if nwc:
        row = next(doc for doc in view['documents'] if doc['id'] == prior['id'])
        projected_bytes = len(row['text'].encode('utf-8'))
        del row['text']
        descriptions = {item['id']: item for item in view['stage_view']['documents']}
        prior_view = descriptions[prior['id']]
        selected_bytes = prior_view['selected_source_utf8_bytes']
        prior_view.update(view='metadata_only', reason=(
            'Prior interim narrative excluded only after same-opening TTM reproof using this comparative normalized '
            'statement, annual and current normalized statements; complete source remains in the original catalog.'),
            selected_source_chars=0, excluded_source_chars=prior_view['original_text_chars'],
            selected_source_utf8_bytes=0, excluded_source_utf8_bytes=prior_view['original_text_bytes'],
            projected_text_utf8_bytes=0)
        prior_view.pop('excerpts', None)
        prior_view.pop('projected_text_sha256', None)
        summary = view['stage_view']['excerpt_projection']
        summary['removed_layout_utf8_bytes'] -= prior_view.get('removed_layout_utf8_bytes', 0)
        for field in ('layout_projection', 'removed_layout_chars', 'removed_layout_utf8_bytes',
                      'layout_limitation'):
            prior_view.pop(field, None)
        summary['projected_narrative_utf8_bytes'] -= projected_bytes
        summary['selected_source_utf8_bytes'] -= selected_bytes
        summary['omitted_source_utf8_bytes'] += selected_bytes
        metadata_only.append(prior['id'])
    view['stage_view']['automatic_selection'] = {
        'policy': IFRS_CONTEXT_POLICY, 'scope': scope,
        'annual_document_id': annual['id'], 'current_document_id': current['id'],
        'comparative_document_id': prior['id'], 'current_kept_complete': True,
        'normalized_statement_ids': {primary['id']: normalized[primary['id']]['id']
                                     for primary in (annual, current, prior)},
        'prior_narrative_metadata_only': metadata_only, 'deduplicated_blocks': deduplicated,
        'manifest_sha256': sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'selection_applied': True, 'semantic_coverage_certified': False,
        'coverage_limitations': ('Prior narrative is omitted only from this opening NWC view after reproof; '
            'all normalized observations remain. Business/guidance/risk coverage is not certified.' if nwc else
            'Business, risk and management passages are retained from the recognized annual plus the current '
            'primary; completeness of economic assumptions is not certified.')}
    return view
