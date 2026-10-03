"""Explicit candidate checkpoints, never paid-cache aliases or human approvals.

Hashes bind a candidate to its source context and contract; they do not prove who
authored it. Callers retain original responses and revision provenance separately.
The staged preparer must recompile every supplied driver before continuing.
"""
from copy import deepcopy
from hashlib import sha256


def _digest(value):
    from .preparation_ai import _json
    return sha256(_json(value).encode('utf-8')).hexdigest()


def make_seed(dossier, contract, plan):
    """Package a candidate for later validation; this does not accept its values."""
    from .preparation_ai import _model_dossier
    return {'version': 1, 'dossier_sha256': _digest(_model_dossier(dossier)),
            'contract_sha256': _digest(contract), 'plan_sha256': _digest(plan),
            'plan': deepcopy(plan)}


def restore_seed(seed, dossier, contract):
    from .preparation_ai import _model_dossier, verify_visible_citations
    if (not isinstance(seed, dict) or set(seed) != {
            'version', 'dossier_sha256', 'contract_sha256', 'plan_sha256', 'plan'}
            or type(seed['version']) is not int or seed['version'] != 1):
        raise ValueError('invalid proposal seed envelope')
    for key, value in (('dossier', _model_dossier(dossier)), ('contract', contract), ('plan', seed['plan'])):
        if seed[key + '_sha256'] != _digest(value):
            raise ValueError('proposal seed ' + key + ' identity changed')
    plan = deepcopy(seed['plan'])
    scopes = set(contract['scenarios'])
    if (not isinstance(plan, dict) or set(plan) != {'model', 'scenarios', 'scenario_rationale'}
            or not isinstance(plan['model'], dict)
            or not isinstance(plan['scenarios'], dict) or set(plan['scenarios']) != scopes
            or any(not isinstance(value, dict) for value in plan['scenarios'].values())
            or not isinstance(plan['scenario_rationale'], dict)
            or set(plan['scenario_rationale']) != {scope for scope, values in plan['scenarios'].items() if values}
            or any(not isinstance(value, str) or not value.strip() for value in plan['scenario_rationale'].values())):
        raise ValueError('invalid proposal seed plan or rationale')
    for values in [plan['model'], *plan['scenarios'].values()]:
        if any(not isinstance(value, dict) or 'value' not in value for value in values.values()):
            raise ValueError('invalid proposal seed driver')
        verify_visible_citations(values, dossier, dossier)
    return plan


def verify_prefix(plan, stages):
    """Reject partial or out-of-order groups before any missing stage can spend."""
    gap = False
    for scope, names in stages:
        present = set(names) & plan['scenarios'][scope].keys()
        if present and (gap or present != set(names)):
            raise ValueError('proposal seed must end at a complete consecutive stage')
        gap = gap or not present


def apply_revision(seed, dossier, contract, revision, *, view):
    """Merge explicit scenario edits; return an unvalidated candidate and lineage.

    References address only the original seed, never edits in this revision.
    The caller must retain the AI response and revalidate with StagedProposer;
    this function neither approves a plan nor makes a paid-cache entry.
    """
    from .preparation_ai import verify_visible_citations
    original = restore_seed(seed, dossier, contract)
    if (not isinstance(revision, dict) or set(revision) != {
            'source_plan_sha256', 'replacements', 'reuse', 'scenario_rationale'}
            or revision['source_plan_sha256'] != seed['plan_sha256']
            or not isinstance(revision['replacements'], dict)
            or not isinstance(revision['reuse'], list)
            or not isinstance(revision['scenario_rationale'], dict)):
        raise ValueError('invalid proposal revision envelope or source identity')
    schema = contract['schema']
    if contract.get('bank_dynamic_capital'):
        from .input_preparation import _bank_schema
        perimeter = original['model'].get('perimeter', {}).get('value')
        if not isinstance(perimeter, dict):
            raise ValueError('proposal revision requires bank perimeter')
        schema, _, issues = _bank_schema(original, perimeter)
        if issues:
            raise ValueError('proposal revision requires valid bank legal structure')
    allowed = {name for name, descriptor in schema.items() if descriptor[-1] == 'scenario'}
    candidate = deepcopy(original)
    targets, replaced, reused = set(), [], []

    def valid_scope(scope):
        return isinstance(scope, str) and scope in original['scenarios']

    def assign(scope, name, item):
        if not valid_scope(scope) or not isinstance(name, str) or name not in allowed:
            raise ValueError('unknown revision scenario or driver')
        if (scope, name) in targets:
            raise ValueError('duplicate revision target')
        previous = original['scenarios'][scope].get(name)
        if previous is not None and previous.get('kind') == 'historical':
            raise ValueError('revision cannot overwrite an existing historical driver')
        if not isinstance(item, dict) or not {
                'value', 'kind', 'evidence_ids', 'rationale', 'valid_until', 'valid_until_basis'} <= set(item):
            raise ValueError('revision requires a complete driver envelope')
        verify_visible_citations({name: item}, view, dossier)
        targets.add((scope, name))
        candidate['scenarios'][scope][name] = deepcopy(item)
        return {'scope': scope, 'driver': name,
                'previous_driver_sha256': _digest(previous) if previous is not None else None,
                'driver_sha256': _digest(item)}

    for scope, values in revision['replacements'].items():
        if not valid_scope(scope) or not isinstance(values, dict):
            raise ValueError('invalid revision replacements')
        for name, item in values.items():
            replaced.append(assign(scope, name, item))
    for reference in revision['reuse']:
        if (not isinstance(reference, dict) or set(reference) != {
                'scope', 'source_scope', 'driver', 'source_driver_sha256', 'rationale'}
                or not valid_scope(reference['source_scope'])
                or not valid_scope(reference['scope'])
                or reference['scope'] == reference['source_scope']
                or not isinstance(reference['driver'], str)
                or reference['driver'] not in allowed
                or not isinstance(reference['rationale'], str) or not reference['rationale'].strip()):
            raise ValueError('invalid explicit driver reuse reference')
        source = original['scenarios'][reference['source_scope']].get(reference['driver'])
        if source is None or _digest(source) != reference['source_driver_sha256']:
            raise ValueError('reuse requires an unchanged driver in the original seed')
        item = deepcopy(source)
        item['rationale'] = reference['rationale']
        lineage = assign(reference['scope'], reference['driver'], item)
        reused.append({**lineage, 'source_scope': reference['source_scope'],
                       'source_driver_sha256': _digest(source)})
    affected = {scope for scope, _ in targets}
    rationales = revision['scenario_rationale']
    if (not affected or set(rationales) != affected
            or any(not isinstance(value, str) or not value.strip() for value in rationales.values())):
        raise ValueError('revision needs a complete rationale for exactly the affected scenarios')
    candidate['scenario_rationale'].update(deepcopy(rationales))
    result = make_seed(dossier, contract, candidate)
    return result, {'source_plan_sha256': seed['plan_sha256'], 'plan_sha256': result['plan_sha256'],
                    'revision_sha256': _digest(revision), 'replaced_drivers': replaced,
                    'reused_drivers': reused, 'validation_required': True, 'human_approved': False}


class ScopedRevisionProposer:
    """Review only explicit scenario estimates against an intact compiled model.

    The common dossier stays pinned to the original preparation. New review
    instructions belong to the exact provider view and recorded lineage, never
    to a silently rehashed historical checkpoint or a paid-cache alias.
    """
    def __init__(self, proposer, *, basis, plan, snapshot, scopes, context,
                 checkpoint=None, requested_records=None):
        if not callable(proposer):
            raise ValueError('scoped review requires an authorized proposer')
        self.proposer = proposer
        self.basis, self.prior_plan, self.snapshot = deepcopy((basis, plan, snapshot))
        self.scopes, self.context = deepcopy((scopes, context))
        self.checkpoint = deepcopy(checkpoint)
        self.requested = deepcopy(requested_records or [])
        self.lineage = None

    def __call__(self, dossier, contract, *, on_historical=None):
        from collections import Counter
        from .preparation_ai import StagedProposer, _model_dossier, verify_visible_citations
        from .preparation_view import select_stage_view
        from .input_preparation import _calendar, _catalog, _compile, _day, _selected_sec_filings
        from .fcff_stage_arithmetic import with_engine_guidance
        from .sector_analysis import revise_sector_analysis

        def forbidden(*_args):
            raise ValueError('scoped review requires a complete previously compiled plan')

        if contract.get('method_id') != 'operating_fcff' or contract.get('bank_dynamic_capital'):
            raise ValueError('scoped review supports the existing operating FCFF contract')
        prior_dossier, prior_contract = self.basis.get('dossier'), self.basis.get('contract')
        prior = restore_seed(self.basis.get('seed'), prior_dossier, prior_contract)
        if _digest(prior) != _digest(self.prior_plan):
            raise ValueError('scoped review seed differs from the previous model proposal')
        if (_digest(_model_dossier(dossier)) != _digest(_model_dossier(prior_dossier))
                or _digest(contract) != _digest(prior_contract)):
            raise ValueError('scoped review source context changed; verified source refresh required')
        seed = make_seed(dossier, contract, prior)
        cutoff = _day(dossier['as_of'])
        catalog, issues, _ = _catalog(dossier['documents'], cutoff)
        if issues:
            raise ValueError('scoped review requires a verified source catalog')
        filings = _selected_sec_filings(catalog, dossier.get('document_acquisition'), 'operating_fcff')
        primary_id = ((dossier.get('document_acquisition') or {}).get('selection') or {}).get('selected_document_id')

        def compiled(candidate):
            plan = StagedProposer(forbidden, seed=candidate)(dossier, contract)
            perimeter, calendar, span, errors = _calendar(plan, cutoff, method='operating_fcff')
            if errors:
                raise ValueError('scoped review calendar no longer compiles')
            records, errors, _ = _compile(plan, contract['schema'], {}, perimeter, calendar,
                span, catalog, cutoff, method='operating_fcff', ticker=dossier.get('ticker'),
                sec_filings=filings, sec_primary_id=primary_id)
            if errors:
                raise ValueError('scoped review model no longer compiles: ' + '; '.join(row['reason'] for row in errors))
            normalized = revise_sector_analysis(self.snapshot, method_records=records,
                analysis_context={'scenario_rationale': deepcopy(plan['scenario_rationale'])})
            if normalized['decision'].get('missing_fields') or any(
                    row.get('blocking') for row in normalized['decision'].get('issues', [])):
                raise ValueError('scoped review model has incomplete or invalid normalized records')
            return plan, normalized['case']['records']

        prior, original_records = compiled(seed)
        if Counter(map(_digest, original_records)) != Counter(map(_digest, self.snapshot['case']['records'])):
            raise ValueError('scoped review seed differs from the exact previous model records')
        if prior['scenario_rationale'] != self.snapshot.get('analysis_context', {}).get('scenario_rationale'):
            raise ValueError('scoped review seed differs from the exact previous model rationale')
        historical = None
        frozen = set()
        if self.checkpoint is not None:
            historical = restore_seed(self.checkpoint, prior_dossier, prior_contract)
            for scope, values in [('model', historical['model']), *historical['scenarios'].items()]:
                actual = prior['model'] if scope == 'model' else prior['scenarios'][scope]
                if any(actual.get(name) != item for name, item in values.items()):
                    raise ValueError('scoped review differs from its historical checkpoint')
                frozen.update((scope, name) for name in values)
        if (not isinstance(self.scopes, dict) or not self.scopes
                or any(scope not in contract['scenarios'] for scope in self.scopes)):
            raise ValueError('scoped review needs explicit existing scenario scopes')
        dependent = {'revenue_growth', 'revenue_build', 'gross_margin', 'rnd_pct', 'sga_pct',
                     'capdev_pct', 'da_tan_pct', 'opening_intangible_amortization'}
        for scope, names in self.scopes.items():
            if (not isinstance(names, list) or not names
                    or any(not isinstance(name, str) for name in names)
                    or len(set(names)) != len(names)):
                raise ValueError('scoped review needs unique explicit driver names')
            for name in names:
                descriptor = contract['schema'].get(name)
                previous = prior['scenarios'][scope].get(name) or {}
                if (not descriptor or descriptor[-1] != 'scenario'
                        or previous.get('kind') != 'analyst_estimate' or (scope, name) in frozen):
                    raise ValueError('scoped review can replace only existing non-checkpoint analyst estimates')
            if set(names) & dependent and 'terminal_bridge' not in names:
                raise ValueError('scoped operating review requires its terminal_bridge in the same atomic scope')

        def slot(row):
            return tuple(row.get(key) for key in ('field', 'driver', 'scenario', 'entity'))

        original_index = {slot(row): row for row in original_records}
        requested = {}
        for row in self.requested:
            old = original_index.get(slot(row))
            if old is None or slot(row) in requested:
                raise ValueError('scoped review requested record is missing or duplicated')
            if row['value'] != old['value'] and row['driver'] not in self.scopes.get(row['scenario'], []):
                raise ValueError('scoped review requested value lies outside the explicit scope')
            requested[slot(row)] = row
        if historical is not None:
            if not callable(on_historical):
                raise ValueError('scoped historical review requires verified checkpoint compilation')
            on_historical(deepcopy(historical), deepcopy(dossier), deepcopy(contract))

        responses = []
        for scope in contract['scenarios']:
            if scope not in self.scopes:
                continue
            names = self.scopes[scope]
            narrowed = deepcopy(contract)
            narrowed['schema'] = {name: deepcopy(contract['schema'][name]) for name in names}
            narrowed['preparation_stage'] = {'scope': scope, 'drivers': list(names),
                'purpose': 'scoped_revision', 'source_plan_sha256': seed['plan_sha256'],
                'response_shape': {'drivers': 'exactly the requested complete analyst estimate envelopes',
                    'rationale': 'complete scenario reasoning, including retained judgments and atomic terminal reconciliation'},
                'policy': 'Preserve every unrequested driver and all historical, guidance and checkpoint facts. '
                    'Return only drivers and rationale. No implicit edits or target-price calibration.'}
            view = select_stage_view(dossier, scope)
            view['completed_plan'] = deepcopy(seed['plan'])
            view['preparation_context'] = deepcopy(self.context)
            view = with_engine_guidance(view, narrowed)
            project = getattr(self.proposer, 'prepare_context', None)
            if callable(project):
                view = project(deepcopy(view), narrowed, source_dossier=dossier, allow_selection=True)
            answer = self.proposer(deepcopy(view), deepcopy(narrowed))
            if (not isinstance(answer, dict) or set(answer) != {'drivers', 'rationale'}
                    or not isinstance(answer['drivers'], dict) or set(answer['drivers']) != set(names)
                    or not isinstance(answer['rationale'], str) or not answer['rationale'].strip()
                    or any(not isinstance(item, dict) or item.get('kind') != 'analyst_estimate'
                           for item in answer['drivers'].values())):
                raise ValueError('invalid scoped review response; exactly the requested analyst estimates required')
            verify_visible_citations(answer['drivers'], view, dossier)
            revision = {'source_plan_sha256': seed['plan_sha256'],
                'replacements': {scope: deepcopy(answer['drivers'])}, 'reuse': [],
                'scenario_rationale': {scope: answer['rationale']}}
            candidate, merged = apply_revision(seed, dossier, contract, revision, view=view)
            _, records = compiled(candidate)
            index = {slot(row): row for row in records}
            for identity, requested_row in requested.items():
                if requested_row['scenario'] == scope and index[identity]['value'] != requested_row['value']:
                    raise ValueError('scoped review ignored the explicitly requested value: ' + requested_row['driver'])
            responses.append({'scope': scope, 'request_dossier_sha256': _digest(_model_dossier(view)),
                'request_contract_sha256': _digest(narrowed), 'response_sha256': _digest(answer),
                'response': deepcopy(answer), 'merge': merged})
            seed = candidate
        self.lineage = {'source_plan_sha256': self.basis['seed']['plan_sha256'],
            'plan_sha256': seed['plan_sha256'], 'requested_drivers': deepcopy(self.scopes),
            'context': deepcopy(self.context), 'responses': responses,
            'validation_required': False, 'human_approved': False}
        return deepcopy(seed['plan'])
