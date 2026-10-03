"""Bounded source replacement against an immutable, documented common model.

The acquiring server owns source authenticity. This pure module rechecks source
bytes, dated evidence, observation identity and the common compiler. A browser
document body, URL or claimed readiness flag is never an acquisition receipt.
No AI, DB, network, date rollover or implicit forecast renewal occurs here.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from .preparation_record_evidence import FIELDS


def _plain(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _source_ref(document):
    return {key: deepcopy(document.get(key)) for key in
            ('id', 'url', 'sha256', 'document_sha256', 'published_at', 'available_at')}


def _fact_parts(fact):
    observation = fact.get('observation') if isinstance(fact.get('observation'), dict) else fact
    return observation.get('val', observation.get('value')), observation.get('end'), observation.get('start')


def _fact_key(fact, document):
    _value, end, start = _fact_parts(fact)
    metadata = document.get('metadata') or {}
    return (fact.get('taxonomy'), fact.get('concept'), fact.get('unit'), start, end,
            fact.get('scope', metadata.get('scope')),
            fact.get('entity', metadata.get('entity')))


def verify_source_observation(document, record_pointer, *, expected, as_of, documents=()):
    """Prove one whole typed record or one existing normalized primary fact.

``expected`` contains field/driver/entity/period/unit/accounting_basis and may
contain value, taxonomy and concept. Fact pointers must name /facts/index;
typed pointers must name an exact seven-field observation in the source. The
result retains its proof format for ordinary prepare_method_inputs validation.
This is an offline proof, not a claim that caller-supplied bytes were acquired.
"""
    from .input_evidence import _pointer, same_entity_name
    from .input_preparation import _catalog, _day, _finite, _source_scale
    from .preparation_record_evidence import prove_reported_record
    result = {'status': 'blocked', 'reasons': [], 'origin_check': 'offline_reference_only'}
    try:
        if (not isinstance(expected, dict) or not FIELDS - {'value'} <= set(expected)
                or set(expected) - FIELDS - {'taxonomy', 'concept'}):
            raise ValueError('Expected observation metadata must explicitly identify measure, driver, legal entity, period, unit and accounting basis')
        cutoff = _day(as_of)
        if cutoff is None:
            raise ValueError('Exact information cutoff required')
        supplied = [deepcopy(document)]
        supplied.extend(deepcopy(row) for row in documents if row.get('id') != document.get('id'))
        catalog, issues, _ = _catalog(supplied, cutoff)
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        source = catalog[document['id']]
        parsed = json.loads(source['text'])
        observed = _pointer(parsed, record_pointer)
        if not isinstance(observed, dict):
            raise ValueError('Observation pointer must name the full source observation, not a scalar or unrelated leaf')
        metadata = {key: deepcopy(expected[key]) for key in FIELDS - {'value'}}
        if set(observed) == FIELDS:
            identity = tuple(observed[key] for key in sorted(FIELDS - {'value'}))
            rows = parsed.get('observations') if isinstance(parsed, dict) else None
            if isinstance(rows, list) and sum(isinstance(row, dict) and FIELDS <= set(row) and
                    tuple(row[key] for key in sorted(FIELDS - {'value'})) == identity for row in rows) != 1:
                raise ValueError('Ambiguous repeated typed observations; select a qualified source without conflicting identities')
            value = deepcopy(observed['value'])
            if _finite(value):
                scale = _source_scale(observed['unit'], expected['unit'])
                if scale is None:
                    raise ValueError('Source unit/currency does not reconcile to the consumed observation')
                value *= scale
            item = {'value': _plain(value), 'record_pointer': record_pointer,
                    'evidence_ids': [source['id']]}
            reason = prove_reported_record(item, [source], driver=expected['driver'], field=expected['field'],
                entity=expected['entity'], period=expected['period'], unit=expected['unit'], basis=expected['accounting_basis'])
            if reason:
                raise ValueError(reason)
        elif re.fullmatch(r'/facts/(?:0|[1-9][0-9]*)', record_pointer):
            normalizer = (source.get('metadata') or {}).get('normalizer')
            recognized = normalizer in ('statement_tables_v1', 'fdic_financials_v1', 'regulatory_pdf_v1', 'sec_parent_inline_v1')
            sec_facts = str(source['id']).startswith('xbrl-') and str(source['url']).startswith('https://data.sec.gov/api/xbrl/companyfacts/')
            if not (recognized or sec_facts):
                raise ValueError('A normalized fact requires its existing primary-source normalizer and complete source catalog')
            if not expected.get('taxonomy') or not expected.get('concept'):
                raise ValueError('Exact original primary taxonomy/concept locator required; equal units or values do not establish the measure')
            value, period, _start = _fact_parts(observed)
            if (not _finite(value) or period != expected['period']
                    or not same_entity_name(observed.get('entity', (source.get('metadata') or {}).get('entity', parsed.get('issuer'))), expected['entity'])):
                raise ValueError('Normalized source value, legal entity or economic date differs; no balance rollforward is inferred')
            if any(expected.get(key) is not None and expected[key] != observed.get(key) for key in ('taxonomy', 'concept')):
                raise ValueError('Normalized primary measure differs from the declared source locator')
            rows = parsed.get('facts')
            if not isinstance(rows, list) or sum(isinstance(row, dict) and _fact_key(row, source) == _fact_key(observed, source) for row in rows) != 1:
                raise ValueError('Ambiguous normalized observations with the same measure/entity/period/unit/scope')
            scale = _source_scale(observed.get('unit'), expected['unit'])
            if scale is None:
                raise ValueError('Normalized primary unit/currency differs from the consumed observation')
            target_value = value * scale
            parent = record_pointer + ('/observation' if isinstance(observed.get('observation'), dict) else '')
            item = {'value': target_value, 'evidence_ids': [source['id']], 'quoted_value': value,
                'quoted_unit': observed['unit'], 'evidence_pointer': {'value': parent + ('/val' if parent.endswith('/observation') else '/value'),
                    'unit': record_pointer + '/unit', 'period': parent + '/end'}}
            # Full economic concept/ledger checks run again in the complete
            # method plan. A leaf proof does not classify new NWC/claims.
            metadata.update(taxonomy=observed.get('taxonomy'), concept=observed.get('concept'))
        else:
            raise ValueError('Unsupported source observation contract; full typed record or existing normalized /facts/index required')
        if 'value' in expected and _plain(expected['value']) != _plain(item['value']):
            raise ValueError('Proposed observation value differs from the verified primary value')
        observation = {key: metadata[key] for key in FIELDS - {'value'}}
        observation['value'] = deepcopy(item['value'])
        result.update(status='verified', observation=observation, proposal_evidence=item,
                      source=_source_ref(source), source_pointer=record_pointer,
                      primary_measure={key: metadata[key] for key in ('taxonomy', 'concept') if key in metadata})
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        result['reasons'].append(str(exc))
    return result


def _record_key(record):
    return tuple(record.get(key) for key in ('driver', 'scenario', 'entity'))


def _merge_sources(previous, incoming, cutoff):
    from .input_preparation import _catalog
    from .trade_idea_model import _semantic
    old_catalog, old_issues, _ = _catalog(deepcopy(previous), cutoff)
    if old_issues:
        raise ValueError('; '.join(row['reason'] for row in old_issues))
    rows = {document['id']: deepcopy(document) for document in previous}
    for document in incoming:
        ident = document.get('id')
        rows[ident] = deepcopy(document)
    catalog, issues, provenance = _catalog(list(rows.values()), cutoff)
    if issues:
        raise ValueError('; '.join(row['reason'] for row in issues))
    for document in incoming:
        ident = document.get('id')
        if ident in old_catalog and _semantic(old_catalog[ident]) != _semantic(catalog[ident]):
            raise ValueError('Same source ID has different pinned content or economic metadata: ' + str(ident))
    return catalog, provenance


def _prior_primary_measure(item, previous):
    from .input_evidence import _pointer
    pointer = item.get('evidence_pointer')
    ids = item.get('evidence_ids') or []
    match = re.fullmatch(r'(/facts/(?:0|[1-9][0-9]*))/(?:value|observation/val)',
                         str(pointer.get('value'))) if isinstance(pointer, dict) else None
    if not match or len(ids) != 1 or ids[0] not in previous:
        raise ValueError('Normalized replacement requires the original unique primary fact locator; aggregate/reclassified bridges cannot be replaced by a single leaf')
    fact = _pointer(json.loads(previous[ids[0]]['text']), match[1])
    if not isinstance(fact, dict) or not fact.get('taxonomy') or not fact.get('concept'):
        raise ValueError('Original normalized primary measure identity missing')
    return {name: fact[name] for name in ('taxonomy', 'concept')}


def _preserve_current_plan(payload, plan, catalog):
    """A workspace estimate revision must survive its retained source-plan basis."""
    records = payload['acquisition_snapshot']['case']['records']
    original = payload.get('preparation', {}).get('proposal', {}).get('method_records')
    if not isinstance(original, list):
        raise ValueError('Original compiled preparation records are required to preserve current model inputs')
    index = {_record_key(record): record for record in original}
    preserved = []
    for record in records:
        key = _record_key(record)
        prior = index.get(key)
        if prior is None or any(record.get(name) != prior.get(name) for name in
                ('field', 'driver', 'scenario', 'entity', 'period', 'unit', 'accounting_basis')):
            raise ValueError('Current model metadata differs from the recorded preparation; an explicit structural requalification is required')
        scope = plan['model'] if record['scenario'] == 'model' else plan['scenarios'].get(record['scenario'], {})
        item = scope.get(record['driver'])
        if not isinstance(item, dict):
            raise ValueError('Current model driver is absent from the retained source plan')
        if record['value'] == item.get('value'):
            continue
        if record.get('kind') != 'analyst_estimate' or item.get('kind') != 'analyst_estimate':
            raise ValueError('A changed historical/guidance record has no matching retained source proof')
        ids = str(record.get('source_locator', '')).split(',')
        if not ids or not set(ids) <= set(catalog) or record.get('source_id') not in {catalog[ident]['url'] for ident in ids}:
            raise ValueError('Current analyst estimate has lost its explicit source basis')
        if record.get('valid_until') != item.get('valid_until'):
            raise ValueError('Current analyst estimate validity differs; source refresh cannot implicitly renew its expiry')
        item['value'] = deepcopy(record['value'])
        item['evidence_ids'] = ids
        item['rationale'] = 'Current explicit analyst estimate retained during factual source refresh. ' + str(record.get('rationale') or '')
        preserved.append(record['scenario'] + '.' + record['driver'])
    context = payload['acquisition_snapshot']['case'].get('analysis_context') or {}
    for key in ('scenario_rationale', 'analysis_rationale'):
        if key in context:
            plan[key] = deepcopy(context[key])
    return sorted(preserved)


def _automatic_updates(records, plan, previous, incoming):
    """Match exact source contracts; never guess a driver from a label/number."""
    from .input_evidence import _pointer
    updates = []
    for record in records:
        if record.get('kind') != 'historical':
            continue
        scope = plan['model'] if record['scenario'] == 'model' else plan['scenarios'].get(record['scenario'], {})
        item = scope.get(record['driver']) or {}
        matches = []
        for document in incoming:
            try:
                parsed = json.loads(document['text'])
            except (ValueError, TypeError, KeyError):
                continue
            observations = parsed.get('observations') if isinstance(parsed, dict) else None
            if isinstance(observations, list):
                for index, observed in enumerate(observations):
                    if isinstance(observed, dict) and set(observed) == FIELDS and all(
                            observed.get(key) == record.get(key) for key in FIELDS - {'value'}):
                        matches.append((document['id'], '/observations/' + str(index)))
            pointer = item.get('evidence_pointer')
            if not isinstance(pointer, dict) or len(item.get('evidence_ids') or []) != 1:
                continue
            match = re.fullmatch(r'(/facts/(?:0|[1-9][0-9]*))/(?:value|observation/val)', str(pointer.get('value')))
            if not match or not isinstance(parsed, dict) or not isinstance(parsed.get('facts'), list):
                continue
            old_document = previous.get(item['evidence_ids'][0])
            if not old_document:
                continue
            try:
                old_fact = _pointer(json.loads(old_document['text']), match[1])
            except (ValueError, TypeError, KeyError, IndexError):
                continue
            for index, observed in enumerate(parsed['facts']):
                if isinstance(observed, dict) and _fact_key(observed, document) == _fact_key(old_fact, old_document):
                    matches.append((document['id'], '/facts/' + str(index)))
        if len(matches) > 1:
            raise ValueError('Ambiguous primary replacement for driver: ' + record['driver'])
        if matches:
            updates.append({'driver': record['driver'], 'scenario': record['scenario'], 'entity': record['entity'],
                            'document_id': matches[0][0], 'record_pointer': matches[0][1]})
    return updates


def refresh_model_sources(payload, source_report, updates=None, *, as_of=None, output_dir=None, apply=False):
    """Preview or generate a source-derived revision with unchanged model date.

The server supplies its acquired catalog, never a browser source_report. Each
explicit update contains driver/scenario/entity/document_id/record_pointer.
Without updates, only unique exact typed-record or normalized-fact identities
are matched. Aggregate/reclassified bridges remain an explicit unsupported gap.
New cutoff never extends unchanged driver validity. ``apply`` writes only the
new common generation to output_dir and keeps the original files immutable.
"""
    from .sector_analysis import validate_bundle, prepare_sector_analysis
    from .input_preparation import _day, prepare_method_inputs
    from .preparation_service import prepare_and_generate
    from .trade_idea_model import candidate_model_usability
    result = {'status': 'blocked', 'reasons': [], 'changes': [], 'contract': 'documented_source_refresh/1'}
    try:
        if not candidate_model_usability(payload)['usable']:
            raise ValueError('A complete exact common model is required for source replacement')
        before = validate_bundle(payload.get('acquisition_snapshot'), payload.get('ticker'))
        if before['snapshot_id'] != payload.get('snapshot_id') or not payload.get('generation_id'):
            raise ValueError('Original generation identity does not match the acquired model')
        cutoff = _day(as_of or before['case']['as_of'])
        if cutoff is None or cutoff < _day(before['case']['as_of']):
            raise ValueError('Source refresh requires an explicit current or later information cutoff')
        if not isinstance(source_report, dict) or set(source_report) - {'documents', 'acquisition_receipt', 'status', 'issues'}:
            raise ValueError('Only the server-acquired document catalog is accepted; supplied plans/readiness/qualification cannot attest themselves')
        incoming = source_report.get('documents')
        if not isinstance(incoming, list) or not incoming:
            raise ValueError('No newly acquired primary documents supplied')
        preparation = payload.get('preparation') or {}
        plan = deepcopy(preparation.get('proposal', {}).get('plan'))
        previous = preparation.get('review_basis', {}).get('dossier', {}).get('documents')
        if not isinstance(plan, dict) or not isinstance(previous, list):
            raise ValueError('Exact prior sourced plan and evidence catalog required')
        catalog, provenance = _merge_sources(previous, incoming, cutoff)
        preserved_estimates = _preserve_current_plan(payload, plan, catalog)
        incoming_ids = {document['id'] for document in incoming}
        records = before['case']['records']
        indices = {}
        for record in records:
            key = _record_key(record)
            if key in indices:
                raise ValueError('Original records have ambiguous driver/scenario/entity identity')
            indices[key] = record
        if updates is None:
            updates = _automatic_updates(records, plan, {document['id']: document for document in previous}, incoming)
        if not isinstance(updates, list) or not updates:
            raise ValueError('No unique supported primary replacements; aggregate/reclassified balances need an explicit complete proof')
        seen = set()
        for update in updates:
            if not isinstance(update, dict) or set(update) != {'driver', 'scenario', 'entity', 'document_id', 'record_pointer'}:
                raise ValueError('Source update must identify the exact driver/scenario/entity and full document observation pointer')
            key = _record_key(update)
            if key in seen or key not in indices:
                raise ValueError('Duplicate or unconsumed source replacement identity')
            seen.add(key)
            record = indices[key]
            if record.get('kind') != 'historical' or record['driver'] in ('calendar', 'perimeter', 'legal_structure'):
                raise ValueError('Source refresh can replace observed facts only; structural choices/forecast estimates require explicit analyst revision')
            ident = update['document_id']
            if ident not in incoming_ids or ident not in catalog:
                raise ValueError('Replacement document was not part of the server acquisition')
            expected = {name: deepcopy(record[name]) for name in FIELDS - {'value'}}
            scope = plan['model'] if record['scenario'] == 'model' else plan['scenarios'][record['scenario']]
            if re.fullmatch(r'/facts/(?:0|[1-9][0-9]*)', str(update['record_pointer'])):
                expected.update(_prior_primary_measure(scope[record['driver']],
                    {document['id']: document for document in previous}))
            proof = verify_source_observation(catalog[ident], update['record_pointer'], expected=expected,
                as_of=cutoff.isoformat(), documents=list(catalog.values()))
            if proof['status'] != 'verified':
                raise ValueError(record['driver'] + ': ' + '; '.join(proof['reasons']))
            scope[record['driver']] = {**deepcopy(proof['proposal_evidence']), 'kind': 'historical',
                'rationale': 'Observed source refresh from ' + catalog[ident]['url'] + ' at ' + update['record_pointer'],
                'valid_until': cutoff.isoformat(), 'valid_until_basis': {'policy': 'same_day', 'as_of': cutoff.isoformat()}}
            result['changes'].append({**deepcopy(update), 'field': record['field'], 'period': record['period'],
                'unit': record['unit'], 'accounting_basis': record['accounting_basis'],
                'before_value': deepcopy(record['value']), 'after_value': deepcopy(proof['observation']['value']),
                'before_source_id': record.get('source_id'), 'after_source': proof['source']})
        providers = {name: (lambda *_a, _source=deepcopy(source), **_k: deepcopy(_source))
                     for name, source in before['case']['sources'].items() if name != 'method_inputs'}
        bundle = prepare_sector_analysis(payload['ticker'], as_of=cutoff.isoformat(), providers=providers,
            user_context={'assumptions': deepcopy(before['case'].get('assumptions') or {}),
                          'analysis_context': deepcopy(before['case'].get('analysis_context') or {})})
        if bundle['decision'].get('method_id') != before['decision'].get('method_id'):
            raise ValueError('Source replacement changes economic method; a separate qualification is required')
        report = {'documents': list(catalog.values()), 'source_plan': deepcopy(plan)}
        checked = prepare_method_inputs(bundle, documents=report['documents'],
            propose=lambda *_: deepcopy(plan), source_report=report)
        if checked['status'] != 'prepared' or checked['issues']:
            raise ValueError('; '.join(row['reason'] for row in checked['issues']) or 'Replacement does not compile completely')
        if next(row['value']['valuation_date'] for row in checked['bundle']['case']['records'] if row['driver'] == 'calendar') != payload['valuation_date']:
            raise ValueError('Source replacement cannot roll valuation date without a complete new balance/rollforward')
        result.update(status='ready', reasons=[], information_cutoff=cutoff.isoformat(),
            valuation_date=payload['valuation_date'], previous_snapshot_id=payload['snapshot_id'],
            previous_generation_id=payload['generation_id'], source_provenance=provenance,
            plan_sha256=sha256(json.dumps(plan, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            acquisition_receipt=deepcopy(source_report.get('acquisition_receipt')),
            preserved_analyst_revisions=preserved_estimates,
            engine_validation={'status': 'not_assessed', 'requires_apply': True},
            limitations=['Offline proof: acquisition authenticity must come from the server collector.',
                          'Unchanged estimates and observed inputs keep their original expiry; no model-date rollover.',
                          'Preview proves the source and compiler; the common engine must still reconcile unchanged forecasts on apply.'])
        if apply:
            if not output_dir:
                raise ValueError('New generation output directory required')
            generated = prepare_and_generate(bundle, documents=report['documents'], propose=lambda *_: deepcopy(plan),
                source_report=report, output_dir=str(Path(output_dir)), preparation_context={
                    'task': 'deterministic_source_refresh', 'previous_snapshot_id': payload['snapshot_id'],
                    'previous_generation_id': payload['generation_id'], 'changes': deepcopy(result['changes'])})
            if not candidate_model_usability(generated)['usable']:
                raise ValueError('Refreshed common engine is unusable: ' + '; '.join(candidate_model_usability(generated)['reasons']))
            generated.setdefault('preparation', {}).setdefault('provenance', {})['source_refresh'] = {
                key: deepcopy(result[key]) for key in ('previous_snapshot_id', 'previous_generation_id', 'changes', 'plan_sha256')}
            from .dcf_engine import _write_payload_sidecar
            generated = _write_payload_sidecar(generated)
            if not candidate_model_usability(generated)['usable']:
                raise ValueError('Refreshed common sidecar became unusable after recording source lineage')
            result.update(status='applied', payload=generated, engine_validation={'status': 'complete'})
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, OverflowError) as exc:
        result['status'] = 'blocked'
        result['reasons'].append(str(exc))
    return result
