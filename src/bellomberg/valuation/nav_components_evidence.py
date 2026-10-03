"""Exhaustive, class-attributed balance-sheet NAV account evidence.

The optional incremental basis describes additional deductions after every
reported balance-sheet claim has already been deducted once. Its zero fee or
distribution deduction never asserts that the corresponding economic stock is
zero. Unallocated economic fee/payable stocks and off-balance dilution remain
explicit. No ticker registry, market value or analyst target lives here.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import re

from .document_evidence import source_dates
from .fund_nav_statement import normalize_fund_statement
from .nav_adapter import COMPONENTS

NORMALIZER = 'fund_nav_components_v1'
PREFIX = 'fund-nav-components-'
BASIS = 'incremental_claim_deductions'
COVERAGE_CONTRACT = 'nav_components_coverage/1'
_NUMBER = r'(?:\(?-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?\)?|—|–|-)'
_ROW = re.compile(r'^(.*?)\s+\$?\s*('+_NUMBER+r')\s+\$?\s*('+_NUMBER+r')\s*$')
_HEADINGS = {'Financial assets at fair value through profit or loss',
             'Financial liabilities at fair value through profit or loss'}
_CLASSIFICATION_KEYS = {'version', 'assets', 'liabilities', 'aggregate_coverage', 'class_policy', 'dash_policy', 'rationale'}
_CLAIMS = COMPONENTS - {'gross_assets', 'cash', 'equity_adjustments'}
LIMITATION = ('Incremental deductions after exhaustive reported balance-sheet claims and all reported class allocations. '
    'A zero additional fee/distribution deduction is not a zero fee/payable stock. Aggregate payables remain undisaggregated; '
    'publication, quotation, valuation targets and absence of off-balance dilution require separate proofs.')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _amount(token, dash_policy):
    if token in ('—', '–', '-'):
        if dash_policy != 'printed_dash_is_nil_balance':
            raise ValueError('source-printed dash requires its explicit nil-balance interpretation')
        return Decimal(0)
    if token.startswith('(') != token.endswith(')') or token.startswith('(-'):
        raise ValueError('unsupported amount spelling')
    value = Decimal(token.strip('()').replace(',', ''))
    if not value.is_finite() or Decimal(str(float(value))) != value:
        raise ValueError('amount is not exactly representable; no precision fallback')
    return -value if token.startswith('(') else value


def _accounts(body, *, page_start, page_number, classification):
    rows, sections, active, offset = [], {}, None, page_start
    for raw_line in body.splitlines(keepends=True):
        line = raw_line.strip()
        if line in ('Assets', 'Liabilities', 'Equity'):
            if line in sections:
                raise ValueError('duplicate balance-sheet section')
            active = line.lower()
            sections[line] = []
        elif active and line.startswith('Total '+active.capitalize()):
            active = None
        elif active and line and line not in _HEADINGS:
            match = _ROW.fullmatch(line)
            if not match:
                raise ValueError('unparsed account within exhaustive balance section: '+line)
            label, first, second = match.groups()
            label = re.sub(r'\s+[0-9]+(?:,[0-9]+)*$', '', label).strip()
            if not label or any(row['section'] == active and row['label'] == label for row in rows):
                raise ValueError('missing or duplicate balance-sheet account')
            amounts = [_amount(token, classification['dash_policy']) for token in (first, second)]
            if active != 'equity' and any(value < 0 for value in amounts):
                raise ValueError('negative asset/liability cannot be silently reclassified')
            start = offset + raw_line.index(line)
            row = {'section': active, 'label': label, 'values': [float(v) for v in amounts],
                'printed_values': [first, second], 'locator': {'page': page_number, 'start': start,
                'end': start+len(line), 'text': line}}
            rows.append(row)
            sections[active.capitalize()].append(row)
        offset += len(raw_line)
    if set(sections) != {'Assets', 'Liabilities', 'Equity'} or active is not None or any(not group for group in sections.values()):
        raise ValueError('all three complete balance sections and totals required')
    return rows, sections


def normalize_nav_components(source, *, classification, on, as_of, entity, share_class):
    """Return component evidence plus explicit diagnostics, never model approval."""
    result = {'status': 'incomplete', 'usable': False, 'method_records': [], 'diagnostic': None,
              'documents': [], 'issues': [], 'blocking_gaps': [], 'component_basis': BASIS+'/1'}
    try:
        opening, cutoff = date.fromisoformat(on), date.fromisoformat(as_of)
        if opening > cutoff or source.get('metadata', {}).get('issuer') != entity or source['metadata'].get('report_date') != on:
            raise ValueError('exact legal entity and economic statement date required')
        dates = source_dates(source, cutoff)
        if (not isinstance(classification, dict) or set(classification) != _CLASSIFICATION_KEYS
                or classification.get('version') != 'exhaustive_balance_sheet_accounts/1'
                or classification.get('class_policy') != 'exclude_all_other_reported_share_classes'
                or classification.get('dash_policy') != 'printed_dash_is_nil_balance'
                or not isinstance(classification.get('rationale'), str) or not classification['rationale'].strip()
                or any(not isinstance(classification.get(key), dict) for key in ('assets', 'liabilities', 'aggregate_coverage'))):
            raise ValueError('explicit supported exhaustive account/class/dash criteria required')
        observed = normalize_fund_statement(source)
        if observed['status'] != 'ready':
            raise ValueError('original class-attributed statement not verified: '+repr(observed['issues']))
        raw = json.loads(observed['documents'][0]['text'])
        current = [fact for fact in raw['facts'] if fact['end'] == on]
        selected = [fact for fact in current if fact['concept'] == 'ClassNetAssets' and fact.get('share_class') == share_class]
        if len(selected) != 1:
            raise ValueError('selected share class absent or ambiguous')
        selected = selected[0]
        page_number = selected['source_locator']['page']
        page = source['page_references'][page_number-1]
        body = source['text'][page['inizio']:page['fine']]
        rows, sections = _accounts(body, page_start=page['inizio'], page_number=page_number, classification=classification)
        for section, concept in (('Assets', 'TotalAssets'), ('Liabilities', 'TotalLiabilities'), ('Equity', 'TotalEquity')):
            for column in (0, 1):
                # Exact dated totals are already verified by normalize_fund_statement.
                totals = [fact for fact in raw['facts'] if fact['concept'] == concept and fact['source_locator']['column'] == column]
                if len(totals) != 1 or sum(Decimal(str(row['values'][column])) for row in sections[section]) != Decimal(str(totals[0]['value'])):
                    raise ValueError(section+' individual accounts do not close to the verified total in both columns')
        for section, key in (('Assets', 'assets'), ('Liabilities', 'liabilities')):
            accounts = {row['label'] for row in sections[section]}
            if set(classification[key]) != accounts:
                raise ValueError(section+' mapping must consume each account once, with no omitted/invented accounts')
            for row in sections[section]:
                category = classification[key][row['label']]
                valid = {'cash'} if section == 'Assets' and row['label'] == 'Cash and cash equivalents' else {'gross_assets'} if section == 'Assets' else _CLAIMS
                if category not in valid:
                    raise ValueError(row['label']+': account classification has unsupported sign or economic category')
                row['component'] = category
        coverage = classification['aggregate_coverage']
        claimed = set()
        aggregates = {}
        for label, categories in coverage.items():
            if (label not in classification['liabilities'] or classification['liabilities'][label] != 'other_liabilities'
                    or not isinstance(categories, list) or not categories or len(categories) != len(set(categories))
                    or not set(categories) <= {'accrued_fees', 'distributions_payable'} or claimed & set(categories)):
                raise ValueError('aggregate coverage must be explicit, disjoint and already deducted once as other liabilities')
            claimed.update(categories)
            row = next(row for row in sections['Liabilities'] if row['label'] == label)
            aggregates[label] = {'value': row['values'][0], 'covers': categories, 'locator': deepcopy(row['locator']),
                'limitation': 'Coverage assigns additional deductions; individual fee/distribution stocks are not disaggregated.'}
        direct_claims = set(classification['liabilities'].values())
        if not {'accrued_fees', 'distributions_payable'} <= direct_claims | claimed:
            raise ValueError('fees and distributions require explicit direct accounts or covered aggregates; absence is not stock zero')
        values = {key: Decimal(0) for key in COMPONENTS}
        for row in rows:
            if row['section'] != 'equity':
                values[row['component']] += Decimal(str(row['values'][0]))
        classes = [fact for fact in current if fact['concept'] == 'ClassNetAssets']
        values['equity_adjustments'] = -sum(Decimal(str(fact['value'])) for fact in classes if fact['share_class'] != share_class)
        common = values['gross_assets']+values['cash']-sum(values[key] for key in _CLAIMS)+values['equity_adjustments']
        if common != Decimal(str(selected['value'])):
            raise ValueError('component deductions and all class allocations do not reconcile to selected public NAV')
        shares = [fact for fact in current if fact['concept'] == 'ClassSharesOutstanding' and fact['share_class'] == share_class]
        if len(shares) != 1:
            raise ValueError('exact selected-class shares required')
        incremental = {key: float(value) for key, value in values.items()}
        currency = selected['unit']
        proof = {'contract': COVERAGE_CONTRACT, 'primary_document_id': source['id'], 'primary_text_sha256': source['sha256'],
            'primary_url': source['url'], 'primary_dates': deepcopy(dates),
            'statement_page': {'number': page_number, 'text': body, 'sha256': page['sha256']},
            'entity': entity, 'share_class': share_class, 'on': on, 'currency': currency,
            'classification': deepcopy(classification)}
        proof['normalization_sha256'] = sha256(_json({'coverage': proof, 'values': incremental}).encode()).hexdigest()
        component_value = {**{key: value/1e6 for key, value in incremental.items()}, 'basis': BASIS, 'coverage': proof}
        checks = {'every_balance_account_consumed_once': True, 'both_comparative_columns_closed': True,
                  'all_reported_classes_allocated': True, 'selected_class_nav_reconciled': True}
        diagnostic = {'incremental_components': incremental, 'economic_stock_components': {
            key: float(values[key]) if key in direct_claims and key not in claimed else None
            for key in ('accrued_fees', 'distributions_payable')},
            'covered_aggregates': aggregates, 'common_equity_nav': float(common),
            'nav_per_share': float(common/Decimal(str(shares[0]['value']))), 'class_allocations': deepcopy(classes),
            'limitation': LIMITATION}
        payload = {'component_value': component_value, 'checks': checks, 'diagnostic': diagnostic, 'account_rows': rows,
                   'limitation': LIMITATION}
        encoded = _json(payload)
        document = {'id': PREFIX+sha256(encoded.encode()).hexdigest(), 'url': source['url'], **dates,
            'document_sha256': source['document_sha256'], 'text': encoded, 'sha256': sha256(encoded.encode()).hexdigest(),
            'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'], 'entity': entity,
                'share_class': share_class, 'report_date': on, 'as_of': as_of, 'classification': deepcopy(classification)}}
        result.update(status='diagnostic_ready', diagnostic=diagnostic, account_rows=rows, checks=checks,
            documents=[document], component_value=component_value,
            blocking_gaps=['off_balance_dilution_not_proven', 'publication_requires_separate_proof',
                'quotation_requires_separate_proof', 'analyst_targets_require_explicit_assessment'],
            coverage_limitation=LIMITATION)
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
        result['issues'].append({'source': NORMALIZER, 'reason': str(exc)})
    return result


def verify_components_contract(value, *, entity, share_class, on, currency, as_of):
    """Recompile the small pinned statement page; return numeric inputs or error.

    The common preparer separately recompiles this same evidence from the full
    original primary document. This check prevents subsequent model input edits
    or a second claim deduction from silently changing that approved proof shape.
    """
    try:
        if not isinstance(value, dict) or set(value) != COMPONENTS | {'basis', 'coverage'} or value['basis'] != BASIS:
            raise ValueError('complete explicit incremental component contract required')
        proof = value['coverage']
        keys = {'contract', 'primary_document_id', 'primary_text_sha256', 'primary_url', 'primary_dates', 'statement_page',
                'entity', 'share_class', 'on', 'currency', 'classification', 'normalization_sha256'}
        if not isinstance(proof, dict) or set(proof) != keys or proof['contract'] != COVERAGE_CONTRACT:
            raise ValueError('exact component coverage contract required')
        if any(proof[key] != expected for key, expected in (('entity', entity), ('share_class', share_class), ('on', on), ('currency', currency))):
            raise ValueError('component coverage entity/class/date/currency differs')
        page = proof['statement_page']
        if (not isinstance(page, dict) or set(page) != {'number', 'text', 'sha256'} or type(page['number']) is not int
                or page['number'] < 1 or not isinstance(page['text'], str)
                or sha256(page['text'].encode()).hexdigest() != page['sha256']
                or any(not isinstance(proof[key], str) or not re.fullmatch('[0-9a-f]{64}', proof[key])
                       for key in ('primary_document_id', 'primary_text_sha256', 'normalization_sha256'))):
            raise ValueError('pinned original page/primary hashes required')
        from .document_evidence import valid_source_url
        if not valid_source_url(proof['primary_url']):
            raise ValueError('original primary source URL required')
        if not isinstance(proof['primary_dates'], dict) or set(proof['primary_dates']) != {'published_at', 'available_at', 'availability_basis', 'retrieval'}:
            raise ValueError('original source availability basis and receipt required')
        source = {'id': proof['primary_document_id'], 'document_sha256': proof['primary_document_id'],
            'url': proof['primary_url'], 'text': page['text'], 'sha256': page['sha256'],
            **deepcopy(proof['primary_dates']), 'metadata': {'issuer': entity, 'report_date': on},
            'page_references': [{'pagina': 1, 'inizio': 0, 'fine': len(page['text']), 'sha256': page['sha256']}]}
        expected = normalize_nav_components(source, classification=proof['classification'], on=on, as_of=as_of,
                                             entity=entity, share_class=share_class)
        if expected['status'] != 'diagnostic_ready':
            raise ValueError('component page not closed/recompilable: '+repr(expected['issues']))
        numeric = {key: value[key] for key in COMPONENTS}
        rebuilt = expected['component_value']
        if any(numeric[key] != rebuilt[key] for key in COMPONENTS):
            raise ValueError('component amount differs from exact exhaustive account/class coverage')
        original_values = {key: float(Decimal(str(numeric[key]))*Decimal(1000000)) for key in COMPONENTS}
        unhashed = {key: deepcopy(proof[key]) for key in keys - {'normalization_sha256'}}
        if proof['normalization_sha256'] != sha256(_json({'coverage': unhashed, 'values': original_values}).encode()).hexdigest():
            raise ValueError('component coverage normalization hash differs')
        return numeric, None
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
        return None, str(exc)


def verify_dilution_proof(proof, *, coverage, entity, on, as_of, source=None):
    """Return None only for an explicit dated absence of all dilutive claims.

    The preparer supplies the original full source to authenticate the exact
    page. The engine rechecks that pinned proof shape and negative disclosure;
    share counts or an empty balance-sheet category never substitute for it.
    """
    try:
        keys = {'primary_document_id', 'primary_text_sha256', 'page', 'entity', 'as_of', 'evidence_quote'}
        if not isinstance(proof, dict) or set(proof) != keys:
            raise ValueError('explicit complete off-balance dilution proof required')
        if (proof['primary_document_id'] != coverage['primary_document_id']
                or proof['primary_text_sha256'] != coverage['primary_text_sha256']
                or proof['entity'] != entity or proof['as_of'] != on):
            raise ValueError('dilution proof must bind the same primary, entity and economic date')
        page = proof['page']
        if (not isinstance(page, dict) or set(page) != {'number', 'text', 'sha256'}
                or type(page['number']) is not int or page['number'] < 1 or not isinstance(page['text'], str)
                or sha256(page['text'].encode()).hexdigest() != page['sha256']):
            raise ValueError('pinned original disclosure page required')
        if source is not None:
            from .document_evidence import verify_page_references
            source_dates(source, date.fromisoformat(as_of))
            verify_page_references(source['text'], source['page_references'])
            if (source['id'] != proof['primary_document_id'] or source['sha256'] != proof['primary_text_sha256']
                    or sha256(source['text'].encode()).hexdigest() != source['sha256']):
                raise ValueError('dilution source differs from original primary')
            reference = source['page_references'][page['number']-1]
            if source['text'][reference['inizio']:reference['fine']] != page['text'] or reference['sha256'] != page['sha256']:
                raise ValueError('dilution page differs from original complete PDF')
        quote = proof['evidence_quote']
        if not isinstance(quote, str) or not 0 < len(quote) <= 2000 or page['text'].count(quote) != 1 or entity not in quote:
            raise ValueError('unique contiguous issuer-specific dilution disclosure required')
        import calendar
        day = date.fromisoformat(on)
        dated = (on in quote or bool(re.search(r'\b'+calendar.month_name[day.month]+r'\s+'+str(day.day)+r',\s*'+str(day.year)+r'\b', quote)))
        negative = re.search(r'\bno\s+(?:issued\s+or\s+)?outstanding\s+([^.;]{1,1000})', quote, re.I)
        if (not dated or negative is None or any(not re.search(pattern, negative[1], re.I)
                for pattern in (r'\boptions?\b', r'\bwarrants?\b', r'\bconvertible\b'))
                or re.search(r'\b(?:except|excluding|subject\s+to)\b', quote, re.I)):
            raise ValueError('explicit same-date absence of outstanding options, warrants and convertible claims required')
        return None
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError, IndexError) as exc:
        return str(exc)
