"""Dated common-engine expectations and exact primary earnings comparisons.

No forecast driver is promoted to a reported observation. The acquiring server
owns the source catalog; these pure helpers recompile its proofs and compare an
exact legal entity, monetary unit, accounting measure and complete duration.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from math import isclose, isfinite
from pathlib import Path
import re


CONTRACT = 'common_earnings_facts/1'
EBIT_CONCEPTS = frozenset({('us-gaap', 'OperatingIncomeLoss'),
                         ('ifrs-full', 'ProfitLossFromOperatingActivities'),
                         ('reported-statement', 'label:Operating income'),
                         ('reported-statement', 'label:Operating profit'),
                         ('reported-statement', 'label:Operating income (loss)')})


def _finite(value):
    return type(value) in (int, float) and isfinite(value)


def _duration(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Complete exact earnings duration start/end required')
    start, end = value.split('/')
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError('Earnings duration is reversed')
    return start, end


def _facts(document):
    try:
        parsed = json.loads(document['text'])
    except (ValueError, TypeError, KeyError):
        return None, []
    if not isinstance(parsed, dict):
        return None, []
    normalizer = (document.get('metadata') or {}).get('normalizer')
    sec = (str(document.get('id', '')).startswith('xbrl-') and
           str(document.get('url', '')).startswith('https://data.sec.gov/api/xbrl/companyfacts/'))
    if not (sec or normalizer == 'statement_tables_v1'):
        return parsed, []
    rows = parsed.get('facts')
    return parsed, rows if isinstance(rows, list) else []


def _fact_identity(fact, document, parsed):
    observation = fact.get('observation') if isinstance(fact.get('observation'), dict) else fact
    meta = document.get('metadata') or {}
    sec = str(document['id']).startswith('xbrl-')
    return {'taxonomy': fact.get('taxonomy'), 'concept': fact.get('concept'),
            'unit': fact.get('unit'), 'entity': fact.get('entity', meta.get('entity', parsed.get('issuer'))),
            'scope': fact.get('scope', meta.get('scope', 'consolidated' if sec else None)),
            'start': observation.get('start'), 'end': observation.get('end'),
            'value': observation.get('val', observation.get('value'))}


def _source_ids(value):
    if isinstance(value, dict):
        result = set(value.get('evidence_ids') or [])
        for key, child in value.items():
            if key != 'evidence_ids':
                result.update(_source_ids(child))
        return result
    if isinstance(value, list):
        return set().union(*(_source_ids(child) for child in value)) if value else set()
    return set()


def _measure_binding(documents, allowed, entity, unit, *, source_ids=None, taxonomy=None):
    """Bind semantic vocabulary to actual already-qualified primary documents."""
    from .input_evidence import same_entity_name
    from .input_preparation import _source_scale
    pairs, bindings = set(), []
    for document in documents:
        if source_ids is not None and document['id'] not in source_ids:
            continue
        parsed, facts = _facts(document)
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict):
                continue
            identity = _fact_identity(fact, document, parsed)
            pair = (identity['taxonomy'], identity['concept'])
            if (pair not in allowed or taxonomy is not None and pair[0] != taxonomy or
                    not same_entity_name(identity['entity'], entity) or identity['scope'] != 'consolidated' or
                    _source_scale(identity['unit'], unit) is None or not identity['start'] or not identity['end']):
                continue
            _duration(identity['start'] + '/' + identity['end'])
            pairs.add(pair)
            bindings.append({'document_id': document['id'], 'source': document['url'],
                             'source_pointer': '/facts/' + str(index), 'sha256': document['sha256'],
                             'taxonomy': pair[0], 'concept': pair[1], 'scope': identity['scope']})
    if len(pairs) != 1:
        return None, [], ('No exact primary semantic definition bound to the model' if not pairs else
                          'Multiple primary accounting concepts remain; explicit definition selection required')
    taxonomy, concept = next(iter(pairs))
    return {'taxonomy': taxonomy, 'concept': concept, 'scope': 'consolidated'}, bindings, None


def model_earnings_expectations(payload):
    """Return exact native calculated outputs or a method-specific gap.

Each value is checked against the retained common-engine baseline and has its
actual workbook cell. A metric with no normalized accounting definition remains
available for a whole typed primary contract only, with that limitation visible.
EBITDA is deliberately absent until a qualified issuer definition is supplied.
"""
    result = {key: payload.get(key) for key in ('snapshot_id', 'generation_id', 'valuation_date', 'workbook_sha256')}
    result.update(contract=CONTRACT, status='blocked', reasons=[], expectations=[], unavailable_metrics=[])
    from .trade_idea_model import model_exhibits
    from .input_preparation import _catalog, _day
    from .input_evidence import revenue_concepts
    packet = model_exhibits(payload)
    if packet['status'] != 'complete':
        result['reasons'] = list(packet['reasons'])
        return result
    if payload.get('method') != 'operating_fcff':
        from .trade_idea_earnings_family_facts import native_earnings_expectations
        return native_earnings_expectations(payload, packet, result)
    try:
        case = payload['acquisition_snapshot']['case']
        records = case['records']
        model = {row['driver']: row for row in records if row.get('scenario') == 'model'}
        calendar, perimeter = model['calendar']['value'], model['perimeter']['value']
        entity, currency = perimeter['entity'], perimeter['currency']
        unit = currency + ' million'
        periods = calendar['periods']
        basis = payload.get('preparation', {}).get('review_basis', {}).get('dossier') or {}
        catalog, issues, _ = _catalog(basis.get('documents') or [], _day(case['as_of']))
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        plan = payload.get('preparation', {}).get('proposal', {}).get('plan') or {}
        revenue_item = plan.get('model', {}).get('historical_revenue') or {}
        revenue_measure, revenue_sources, revenue_gap = _measure_binding(list(catalog.values()), revenue_concepts(),
            entity, unit, source_ids=_source_ids(revenue_item))
        scenario = {row['driver']: row['value'] for row in records if row.get('scenario') == 'base'}
        # A model research capitalization adjustment changes operating profit.
        # It cannot be compared with a reported statutory line without a bridge.
        adjusted = any(value != 0 for value in scenario.get('capdev_pct', []))
        if adjusted:
            ebit_measure, ebit_sources, ebit_gap = None, [], 'Model research capitalization requires an explicit reported-to-model EBIT bridge'
        else:
            ebit_measure, ebit_sources, ebit_gap = _measure_binding(list(catalog.values()), EBIT_CONCEPTS,
                entity, unit, taxonomy=revenue_measure['taxonomy'] if revenue_measure else None)
        rows = payload['calculation_details']['scenarios']['base']['rows']
        from .trade_idea_exhibits import _baselines
        from openpyxl import load_workbook
        path = Path(payload['path'])
        if sha256(path.read_bytes()).hexdigest() != payload['workbook_sha256']:
            raise ValueError('Workbook changed while freezing earnings expectations')
        workbook = load_workbook(path, data_only=False, read_only=True)
        try:
            baselines = _baselines(workbook)
        finally:
            workbook.close()
        visible = next((entry for entry in packet['exhibits'] if entry['id'] == 'drivers'), {})
        for metric, measure, source_bindings, gap in (
                ('revenue', revenue_measure, revenue_sources, revenue_gap),
                ('ebit', ebit_measure, ebit_sources, ebit_gap)):
            values = rows.get(metric)
            if not isinstance(values, list) or len(values) != len(periods):
                result['unavailable_metrics'].append({'driver': metric, 'reason': 'Exact model output path absent or calendar mismatch'})
                continue
            for index, (period, value) in enumerate(zip(periods, values)):
                duration = period['start'] + '/' + period['end']
                _duration(duration)
                engine_path = 'base.rows.' + metric + '.' + str(index)
                baseline = baselines.get(engine_path)
                if not _finite(value) or not baseline or not isclose(value, baseline[0], rel_tol=1e-10, abs_tol=1e-9):
                    raise ValueError('Earnings engine/workbook baseline mismatch: ' + engine_path)
                display_cell = None
                if metric in visible.get('columns', []) and index < len(visible.get('cell_refs', [])):
                    display_cell = visible['cell_refs'][index][visible['columns'].index(metric)]
                result['expectations'].append({'field': 'operating_actuals', 'driver': metric,
                    'entity': entity, 'period': duration, 'period_start': period['start'], 'period_end': period['end'],
                    'unit': unit, 'accounting_basis': 'reported_revenue' if metric == 'revenue' else 'common_engine_operating_ebit',
                    'scenario': 'base', 'kind': 'engine_calculated_forecast', 'value': value,
                    'cell': baseline[1], 'display_cell': display_cell, 'forecast_index': index,
                    'model_period': duration, 'primary_measure': measure,
                    'source_binding': {'engine_path': engine_path, 'cell': baseline[1],
                                       'primary_definitions': source_bindings},
                    'actual_proof_status': 'exact_normalized_or_typed_contract' if measure else 'typed_contract_only',
                    'limitation': gap, 'as_of': case['as_of']})
        result['unavailable_metrics'].append({'driver': 'ebitda', 'reason': 'No qualified issuer EBITDA accounting definition; no proxy inferred'})
        result['status'] = 'ready' if result['expectations'] else 'unavailable'
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        result['reasons'].append(str(exc))
        result['expectations'] = []
    return result


def prove_earnings_fact(report, expected, submitted, *, as_of):
    """Prove one actual from a server source catalog or raise an explicit gap.

The exact primary concept is frozen with the model. Full duration matching is
mandatory: an interim/YTD balance, another issuer, debt or an unrelated amount
cannot satisfy annual revenue merely by sharing its unit or numerical value.
"""
    from .input_preparation import _catalog, _day, _source_scale
    from .input_evidence import same_entity_name
    from .preparation_record_evidence import FIELDS
    from .trade_idea_source_refresh import verify_source_observation
    if not isinstance(report, dict) or not isinstance(expected, dict) or not isinstance(submitted, dict):
        raise ValueError('Frozen model expectation and server primary catalog required')
    start, end = _duration(expected.get('period'))
    if expected.get('period_start', start) != start or expected.get('period_end', end) != end:
        raise ValueError('Frozen earnings duration metadata disagrees')
    if expected.get('kind') != 'engine_calculated_forecast' or not expected.get('cell'):
        raise ValueError('An exact common-engine output is required; standalone assumptions are not reported earnings expectations')
    if any(submitted.get(key) != expected.get(key) for key in ('driver', 'entity', 'period', 'unit')):
        raise ValueError('Submitted actual measure, legal entity, duration or monetary unit differs from the frozen model')
    if not _finite(submitted.get('value')):
        raise ValueError('Finite actual value required')
    catalog, issues, _ = _catalog(report.get('documents') or [], _day(as_of))
    if issues:
        raise ValueError('; '.join(row['reason'] for row in issues))
    measure = expected.get('primary_measure')
    typed = {key: deepcopy(expected.get(key)) for key in FIELDS - {'value'}}
    if any(value is None for value in typed.values()):
        raise ValueError('Frozen common-engine measure/accounting metadata is incomplete')
    matches = []
    normalized_candidates = []
    for document in catalog.values():
        if document['url'] != submitted.get('source') or document.get('published_at') != submitted.get('published_at'):
            continue
        parsed, facts = _facts(document)
        if parsed is None:
            continue
        observations = parsed.get('observations')
        if isinstance(observations, list):
            for index, observation in enumerate(observations):
                if not isinstance(observation, dict) or set(observation) != FIELDS:
                    continue
                proof = verify_source_observation(document, '/observations/' + str(index),
                    expected={**typed, 'value': submitted['value']}, as_of=as_of, documents=list(catalog.values()))
                if proof['status'] == 'verified':
                    matches.append(proof)
        if not isinstance(measure, dict) or set(measure) != {'taxonomy', 'concept', 'scope'}:
            continue
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict):
                continue
            identity = _fact_identity(fact, document, parsed)
            if (identity['taxonomy'] != measure['taxonomy'] or identity['concept'] != measure['concept'] or
                    identity['scope'] != measure['scope'] or not same_entity_name(identity['entity'], expected['entity']) or
                    identity['start'] != start or identity['end'] != end):
                continue
            scale = _source_scale(identity['unit'], expected['unit'])
            if scale is None:
                continue
            normalized_candidates.append((document, index, identity, scale))
    if len(normalized_candidates) > 1:
        raise ValueError('Ambiguous primary earnings facts have the same accounting measure, full duration, entity and unit')
    for document, index, identity, scale in normalized_candidates:
        if not _finite(identity['value']) or not isclose(identity['value'] * scale, submitted['value'], rel_tol=1e-10, abs_tol=1e-9):
            continue
        matches.append({'status': 'verified', 'origin_check': 'offline_reference_only',
                'observation': {**typed, 'value': identity['value'] * scale},
                'source': {key: deepcopy(document.get(key)) for key in
                           ('id', 'url', 'sha256', 'document_sha256', 'published_at', 'available_at')},
                'source_pointer': '/facts/' + str(index), 'primary_measure': deepcopy(measure),
                'duration': {'start': start, 'end': end}, 'contract': CONTRACT})
    if len(matches) != 1:
        raise ValueError('No unique verified primary earnings fact matches the frozen accounting measure, full duration, entity, unit and value')
    return matches[0]
