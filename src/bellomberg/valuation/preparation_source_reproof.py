"""Re-prove unchanged historical observations on newly acquired source versions.

Raw hashes are never declared equivalent. Each replacement retains both source
pins, requires an exact source identity and rebinds unique dated typed facts.
The caller still runs the complete ordinary method compiler on the new plan.
"""
from copy import deepcopy
import json
import re
from urllib.parse import urlsplit


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _replace(value, mapping):
    if isinstance(value, dict):
        return {key: _replace(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace(child, mapping) for child in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def _fact_identity(fact):
    if not isinstance(fact, dict) or not all(key in fact for key in ('taxonomy', 'concept', 'unit')):
        raise ValueError('Historical reproof requires a typed primary measure and explicit unit')
    observation = fact.get('observation', fact)
    if not isinstance(observation, dict) or not observation.get('end'):
        raise ValueError('Historical reproof requires an exact end and full duration when reported')
    result = {key: deepcopy(value) for key, value in fact.items() if key not in ('proof', 'value', 'observation')}
    if 'observation' in fact:
        result['observation'] = {key: deepcopy(value) for key, value in observation.items() if key != 'val'}
    return _json(result)


def _facts(old, current):
    old_body, new_body = json.loads(old['text']), json.loads(current['text'])
    originals, replacements = old_body.get('facts'), new_body.get('facts')
    if not isinstance(originals, list) or not originals or not isinstance(replacements, list):
        raise ValueError('Historical reproof requires complete normalized fact arrays')
    positions = {}
    for index, fact in enumerate(replacements):
        positions.setdefault(_fact_identity(fact), []).append((index, fact))
    mapping = {}
    for index, original in enumerate(originals):
        matches = positions.get(_fact_identity(original), [])
        if len(matches) != 1:
            raise ValueError('Historical measure/entity/unit/full period missing or ambiguous in current source')
        target, observed = matches[0]
        old_value = (original.get('observation') or {}).get('val') if 'observation' in original else original.get('value')
        new_value = (observed.get('observation') or {}).get('val') if 'observation' in observed else observed.get('value')
        if _json(old_value) != _json(new_value):
            raise ValueError('Historical observed amount changed; a new economic bridge is required')
        mapping[index] = target
    return mapping


def reprove_historical_plan(plan, old_catalog, current_catalog, *, source_pin):
    """Return a new plan and auditable source replacements; never mutate inputs."""
    from .preparation_historical_sources import _mentioned_ids
    used = _mentioned_ids(plan, set(old_catalog))
    mappings, positions, receipts = {}, {}, []

    def resolve(ident, trail=()):
        if ident in mappings:
            return mappings[ident]
        if ident in trail:
            raise ValueError('Cyclic source dependency in historical reproof')
        old = old_catalog[ident]
        if ident in current_catalog and source_pin(old) == source_pin(current_catalog[ident]):
            mappings[ident] = ident
            return ident
        meta = old.get('metadata') or {}
        normalizer = meta.get('normalizer')
        kind = ('normalized' if normalizer in {'sec_parent_inline_v1', 'fdic_financials_v1', 'sec_statement_shares_v1'}
                else 'listing' if ident.startswith('listing-') else 'raw_sec' if meta.get('emittente_id', '').startswith('CIK:')
                and meta.get('form') and not normalizer else None)
        if kind is None:
            raise ValueError('Changed historical source format has no exact reproof contract: ' + ident)
        parent = meta.get('source_document_id') or meta.get('primary_document_id')
        if kind == 'listing':
            parent = ident[len('listing-'):]
        mapped_parent = None
        if parent:
            if parent not in old_catalog:
                raise ValueError('Original primary dependency missing from historical catalog')
            if normalizer != 'fdic_financials_v1':
                mapped_parent = resolve(parent, (*trail, ident))
        expected_meta = _replace(meta, mappings)
        for key in ('source_document_id', 'primary_document_id', 'catalog_reconciliation', 'as_of', 'reference_cutoff'):
            expected_meta.pop(key, None)
        candidates = []
        for candidate in current_catalog.values():
            cm = deepcopy(candidate.get('metadata') or {})
            candidate_parent = cm.get('source_document_id') or cm.get('primary_document_id')
            if kind == 'listing':
                candidate_parent = candidate['id'][len('listing-'):] if candidate['id'].startswith('listing-') else None
            for key in ('source_document_id', 'primary_document_id', 'catalog_reconciliation', 'as_of', 'reference_cutoff'):
                cm.pop(key, None)
            if cm != expected_meta or candidate.get('published_at') != old.get('published_at'):
                continue
            if mapped_parent is not None and candidate_parent != mapped_parent:
                continue
            if normalizer == 'fdic_financials_v1':
                if any(urlsplit(doc['url']).hostname != 'api.fdic.gov' or urlsplit(doc['url']).path != '/banks/financials'
                       for doc in (old, candidate)):
                    continue
            elif candidate['url'] != old['url']:
                continue
            if kind in ('raw_sec', 'listing') and (candidate['text'] != old['text'] or candidate['sha256'] != old['sha256']):
                continue
            if kind == 'raw_sec' and (not all(meta.get(key) for key in ('emittente_id', 'issuer', 'form', 'report_date', 'accession'))
                    or urlsplit(old['url']).hostname != 'www.sec.gov'):
                continue
            candidates.append(candidate)
        if len(candidates) != 1:
            raise ValueError('Historical source identity/current primary missing or ambiguous: ' + ident)
        current = candidates[0]
        fact_positions = _facts(old, current) if kind == 'normalized' else {}
        # Statement share conflicts and source selection are economic evidence,
        # not incidental provenance; retain them in full after primary rebinding.
        if normalizer == 'sec_statement_shares_v1':
            old_body = _replace(json.loads(old['text']), mappings)
            new_body = json.loads(current['text'])
            if old_body != new_body:
                raise ValueError('Share statement selection or conflicting tagged observations changed')
        mappings[ident], positions[ident] = current['id'], fact_positions
        if current['id'] != ident or source_pin(old) != source_pin(current):
            receipts.append({'old_source_id': ident, 'new_source_id': current['id'],
                'old_text_sha256': old['sha256'], 'new_text_sha256': current['sha256'],
                'old_document_sha256': old.get('document_sha256'), 'new_document_sha256': current.get('document_sha256'),
                'basis': 'exact_full_primary_text_and_filing_identity' if kind == 'raw_sec' else
                         'exact_listing_contract_and_reproved_primary' if kind == 'listing' else
                         'unique_measure_entity_scope_unit_full_period_and_unchanged_amount',
                'fact_rebindings': [{'old_index': key, 'new_index': value} for key, value in sorted(fact_positions.items())]})
        return current['id']

    for ident in sorted(used):
        resolve(ident)

    def rewrite(value):
        if not isinstance(value, (dict, list)):
            return _replace(value, mappings)
        if isinstance(value, list):
            return [rewrite(child) for child in value]
        ids = value.get('evidence_ids')
        local = positions.get(ids[0], {}) if isinstance(ids, list) and len(ids) == 1 else {}
        result = {}
        for key, child in value.items():
            if key in ('value', 'rationale'):
                result[key] = deepcopy(child)
            elif key == 'evidence_pointer' and isinstance(child, dict):
                pointers = {}
                for name, pointer in child.items():
                    match = re.fullmatch(r'/facts/(0|[1-9][0-9]*)(/.*)', str(pointer))
                    pointers[name] = ('/facts/' + str(local[int(match[1])]) + match[2]
                                      if match and int(match[1]) in local else pointer)
                result[key] = pointers
            elif key == 'calculation' and isinstance(child, dict) and child.get('type') == 'statement_shares':
                result[key] = rewrite(child)
                index = child.get('fact_index')
                if index in local:
                    result[key]['fact_index'] = local[index]
            else:
                result[key] = rewrite(child)
        return result
    return rewrite(plan), receipts
