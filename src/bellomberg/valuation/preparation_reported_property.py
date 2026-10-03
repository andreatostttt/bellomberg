"""Source-first reported property NAV opening, without a previous model.

The reviewed primary compiler proves every historical observation. The free
assembly validates the complete ledger but never supplies asset shocks or NAV
targets. Other editions and stabilized property economics retain explicit gaps.
"""
from copy import deepcopy
from datetime import date
import json
from math import isfinite


def _item(value, ident, as_of, *, historical, pointer=None):
    item = {'value': deepcopy(value), 'kind': 'historical' if historical else 'analyst_estimate',
            'evidence_ids': [ident], 'valid_until': as_of,
            'valid_until_basis': {'policy': 'same_day', 'as_of': as_of},
            'rationale': 'Exact reported primary snapshot.' if historical else
                         'Declared reported-NAV scope and snapshot convention; no prospective amounts.'}
    if pointer is not None:
        item['record_pointer'] = pointer
    return item


def reported_property_name_binding(entity, confirmed, catalog, *, evidence_ids=()):
    """Use a legal name pair only from an already recompiled primary catalog.

    The reviewed normalizer derives the pair and registration from original
    public PDF bytes. No caller-supplied alias list or legal suffix rule applies.
    """
    from .property_reported_sources import NORMALIZER
    selected = set(evidence_ids)
    for document in catalog.values():
        metadata = document.get('metadata') or {}
        if metadata.get('normalizer') != NORMALIZER or (selected and document['id'] not in selected):
            continue
        try:
            legal = json.loads(document['text']).get('legal_identity')
            primary = catalog[metadata['source_document_id']]
            if (not isinstance(legal, dict) or legal.get('contract') != 'primary_legal_identity/1'
                    or legal.get('entity') != entity or metadata.get('entity') != entity
                    or legal.get('primary_document_id') != primary['id']
                    or legal.get('primary_document_sha256') != primary.get('document_sha256')
                    or legal.get('primary_text_sha256') != primary['sha256']
                    or legal.get('registration') != {'jurisdiction': 'BE', 'number': '0417.199.869'}
                    or not isinstance(legal.get('names'), list)
                    or entity not in legal['names'] or confirmed not in legal['names']
                    or not legal.get('proofs')):
                continue
            return {'contract': 'verified_primary_name_binding/1',
                'entity': entity, 'confirmed_name': confirmed,
                'normalized_document_id': document['id'], 'primary_document_id': primary['id'],
                'primary_document_sha256': primary['document_sha256'],
                'primary_text_sha256': primary['sha256'],
                'registration': deepcopy(legal['registration']), 'proofs': deepcopy(legal['proofs'])}
        except (ValueError, TypeError, KeyError):
            continue
    return None


def assemble_reported_property(bundle, report, *, as_of):
    """Return a proof-bound historical plan or an explicit method/source gap."""
    from .input_preparation import _catalog, _day, prepare_method_inputs
    from .preparation_fresh_historical import _issuer_key
    from .property_nav_requirements import REPORTED_POLICY, REPORTED_SCHEMA
    from .property_reported_sources import SOURCE_URL, normalize_reported_property
    from .property_reported_nav import project_reported
    from .preparation_record_evidence import FIELDS
    result = deepcopy(report)
    receipt = {'contract': 'reported_property_historical_sources/1', 'status': 'blocked',
               'reasons': [], 'historical_drivers': [], 'forecast_values_reused': False,
               'archive_reads': 'none_by_contract', 'source_origin': 'current_explicit_primary_catalog'}
    result['reported_property_historical_assembly'] = receipt
    if bundle.get('decision', {}).get('method_id') != 'property_nav':
        receipt.update(status='unsupported', reasons=['Reported property NAV assembly applies only to property_nav'])
        return result
    try:
        cutoff = _day(as_of)
        catalog, issues, _ = _catalog(result.get('documents') or [], cutoff)
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        primaries = [document for document in catalog.values() if document['url'] == SOURCE_URL
                     and not (document.get('metadata') or {}).get('normalizer')]
        if len(primaries) != 1:
            raise ValueError('No unique reviewed reported-property primary in the acquired catalog; automatic discovery and other editions remain unqualified')
        primary = primaries[0]
        profile = bundle['case'].get('info') or {}
        entity = (primary.get('metadata') or {}).get('issuer')
        confirmed = profile.get('longName') or profile.get('shortName')
        name_mismatch = not _issuer_key(entity) or _issuer_key(entity) != _issuer_key(confirmed)
        if name_mismatch:
            receipt['fatal_identity_mismatch'] = True
        on = (primary.get('metadata') or {}).get('report_date')
        if not cutoff or not _day(on) or _day(on) > cutoff:
            raise ValueError('Verified reported-property opening before the information cutoff required')
        compiled = normalize_reported_property(primary, entity=entity, on=on, as_of=as_of)
        if compiled.get('status') != 'ready' or len(compiled.get('documents') or []) != 1:
            raise ValueError('Primary reported-property facts cannot be recompiled: '+repr(compiled.get('issues')))
        document = compiled['documents'][0]
        originals = [row for row in catalog.values() if row['id'] != document['id']]
        catalog, issues, _ = _catalog([*originals, document], cutoff)
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        if name_mismatch:
            binding = reported_property_name_binding(entity, confirmed, catalog,
                evidence_ids=[document['id']])
            if not binding:
                raise ValueError('Reported property primary issuer differs from the confirmed security profile; no inferred alias or archived repair')
            receipt.pop('fatal_identity_mismatch', None)
            receipt['legal_identity_binding'] = binding
        observations = json.loads(document['text'])['observations']
        required = {'quotation', 'shares', 'balance_sheet', 'property_reconciliation',
                    'epra_bridge', 'claims', 'restrictions', 'development'}
        if (len(observations) != len(required) or
                {row.get('driver') for row in observations if isinstance(row, dict)} != required):
            raise ValueError('Complete unique reported-property historical inventory required')
        plan = {'model': {}, 'scenarios': {name: {} for name in ('bear', 'base', 'bull')},
                'scenario_rationale': {name: 'Historical source qualification only; explicit asset shocks and NAV targets remain for the authorized preparer.'
                                       for name in ('bear', 'base', 'bull')}}
        for index, observation in enumerate(observations):
            driver = observation['driver']
            field, unit, basis, *_ = REPORTED_SCHEMA[driver]
            if (set(observation) != FIELDS or observation['entity'] != entity or observation['period'] != on
                    or observation['field'] != field or observation['unit'] != unit or observation['accounting_basis'] != basis):
                raise ValueError('Primary reported-property observation has a different scope, period or accounting contract: '+driver)
            plan['model'][driver] = _item(observation['value'], document['id'], as_of,
                historical=True, pointer='/observations/'+str(index))
        values = {driver: item['value'] for driver, item in plan['model'].items()}
        quote = values['quotation']
        positive = lambda value: type(value) in (int, float) and isfinite(value) and value > 0
        if not positive(values['shares']) or any(not positive(quote.get(key)) for key in
                ('price', 'financial_to_quote_rate', 'quote_units_per_currency', 'shares_per_quote')):
            raise ValueError('Positive observed shares, price and exact conversion factors required')
        values['perimeter'] = {'entity': entity, 'currency': quote['financial_currency'], 'share_class': quote['share_class']}
        values['calendar'] = {'valuation_date': on, 'periods': [], 'discount_convention': 'snapshot'}
        values['policy'] = deepcopy(REPORTED_POLICY)
        for driver in ('perimeter', 'calendar', 'policy'):
            plan['model'][driver] = _item(values[driver], primary['id'], as_of, historical=False)
        problems = []
        balances = project_reported(values, {}, lambda driver, reason: problems.append(driver+': '+reason),
                                    historical_only=True)
        if problems or not balances:
            raise ValueError('Reported-property economic reconciliation failed: '+'; '.join(problems))
        # The ordinary preparer rechecks the exact typed primary contracts.
        # Future sensitivity/target omissions are preserved, never filled here.
        checked = prepare_method_inputs(bundle, documents=list(catalog.values()),
            propose=lambda *_: deepcopy(plan), source_report={**result, 'source_plan': plan})
        blocking = [row['reason'] for row in checked['issues'] if row.get('field') in
                    required | {'documents', 'perimeter', 'calendar', 'policy', 'proposal'}]
        if blocking:
            raise ValueError('; '.join(blocking))
        receipt.update(status='ready', historical_drivers=sorted(required), economic_opening_date=on,
            information_cutoff=as_of, reconciliations=balances['reconciliations'],
            model_conventions=['Consolidated reported IFRS assets and liabilities', 'Reported EPRA NTA',
                               'Dated snapshot only; no forecast amounts or automatic NAV targets'],
            limitation='Reviewed edition and primary proof only. Future asset shocks and NAV targets require explicit analyst judgment; no rent/FFO/AFFO or future covenant-compliance forecast is qualified.')
        result.update(source_plan=plan, documents=list(catalog.values()), preparation_ready=True,
                      fresh_historical_assembly=deepcopy(receipt))
        result['reported_property_collector_readiness'] = {
            'standard_collector_ready': report.get('preparation_ready'), 'scoped_ready': True,
            'basis': 'All exact reported NAV historical contracts and economic reconciliations verified; corporate annual revenue and SEC comparisons do not define this variant'}
        result['selection'] = {'policy': 'verified_reported_property_snapshot', 'opening_date': on,
            'selected_document_id': primary['id'], 'selected_document_ids': [primary['id'], document['id']],
            'excluded_document_ids': sorted(set(catalog)-{primary['id'], document['id']})}
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        receipt['reasons'].append(str(exc))
        if receipt.get('fatal_identity_mismatch'):
            result['fresh_historical_assembly'] = deepcopy(receipt)
    return result
