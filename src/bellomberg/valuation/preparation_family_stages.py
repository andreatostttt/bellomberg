"""Bounded staged preparation using each intrinsic method's consumed schema."""
from copy import deepcopy


class FamilyStagedProposer:
    """The caller supplies the authorized proposer; no grant/client is created."""
    def __init__(self, proposer, *, schema_seed=None, drivers_per_stage=6):
        if not callable(proposer) or type(drivers_per_stage) is not int or not 1 <= drivers_per_stage <= 12:
            raise ValueError('Authorized proposer and bounded driver partition required')
        self.proposer, self.schema_seed = proposer, deepcopy(schema_seed)
        self.drivers_per_stage = drivers_per_stage

    def __call__(self, dossier, contract):
        from .preparation_methods import method_schema
        from .input_preparation import _calendar, _catalog, _compile, _day
        from .preparation_ai import verify_visible_citations
        method = contract['method_id']
        schema, entities = method_schema(method, self.schema_seed)
        plan = {'model': {}, 'scenarios': {name: {} for name in contract['scenarios']}, 'scenario_rationale': {}}
        cutoff = _day(dossier['as_of'])
        catalog, issues, _ = _catalog(dossier['documents'], cutoff)
        if issues:
            raise ValueError('Source catalog invalid before family preparation')

        def verify():
            perimeter, calendar, span, issues = _calendar(plan, cutoff, method=method)
            if not issues:
                _, issues, _ = _compile(plan, schema, entities, perimeter, calendar, span, catalog, cutoff, method=method)
                issues = [row for row in issues if row['code'] != 'missing_driver']
            if issues:
                raise ValueError('Invalid completed family stage: ' + '; '.join(row['reason'] for row in issues))

        def stage(scope, names):
            if not names:
                return
            narrowed = deepcopy(contract)
            narrowed.update(schema={name: deepcopy(schema[name]) for name in names},
                family_preparation=True, preparation_stage={'scope': scope, 'drivers': list(names),
                    'consumption': 'Only these exact driver paths; missing evidence remains an explicit source gap'})
            context = deepcopy(dossier)
            context['completed_plan'] = deepcopy(plan)
            if self.schema_seed is not None:
                context['qualified_driver_topology'] = {
                    'drivers': sorted(schema),
                    'basis': 'Free historical qualification identified these consumed driver paths; forecast values must be proposed anew'}
            answer = self.proposer(context, narrowed)
            drivers = answer.get('drivers') if isinstance(answer, dict) else None
            rationale = answer.get('rationale') if isinstance(answer, dict) else None
            if not isinstance(drivers, dict) or set(drivers) != set(names) or any(
                    not isinstance(drivers[name], dict) or 'value' not in drivers[name] for name in names):
                raise ValueError('Incomplete family stage ' + scope + ': ' + str(rationale or 'missing drivers'))
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError('Family stage rationale missing: ' + scope)
            # Contract values may themselves contain independently acquired
            # child bundles (SOTP). Their source labels are data, not citations
            # made by this proposer. The exact whole value is checked by the
            # compiler and every child is subsequently gated by its own engine.
            verify_visible_citations({name: {key: value for key, value in driver.items() if key != 'value'}
                                      for name, driver in drivers.items()}, context, dossier)
            (plan['model'] if scope == 'model' else plan['scenarios'][scope]).update(deepcopy(drivers))
            if scope != 'model':
                plan['scenario_rationale'][scope] = (plan['scenario_rationale'].get(scope, '') + '\n' + rationale).strip()
            verify()

        opening = [name for name, spec in schema.items() if spec[-1] == 'model']
        structure = [name for name in ('perimeter', 'calendar', 'quotation', 'legal_structure') if name in opening]
        if method == 'property_nav':
            structure.extend(name for name in ('policy', 'forward_year') if name in opening)
        stage('model', structure)
        # The opening topology is qualified, not inferred from a company label.
        if method.startswith('insurance_'):
            schema, entities = method_schema(method, plan)
        for offset in range(0, len(opening := [name for name, spec in schema.items() if spec[-1] == 'model' and name not in structure]), self.drivers_per_stage):
            stage('model', opening[offset:offset+self.drivers_per_stage])
        prospective = [name for name, spec in schema.items() if spec[-1] == 'scenario' and spec[3] != 'terminal']
        terminal = [name for name, spec in schema.items() if spec[-1] == 'scenario' and spec[3] == 'terminal']
        for scope in contract['scenarios']:
            for group in (prospective, terminal):
                for offset in range(0, len(group), self.drivers_per_stage):
                    stage(scope, group[offset:offset+self.drivers_per_stage])
        if method == 'exposure_analysis':
            plan.pop('scenario_rationale', None)
            plan['analysis_rationale'] = 'Observed identity, NAV exposure holdings, replication, dated fees, risks, liquidity and quote; no intrinsic company fair value.'
        return plan


def family_response_format(contract):
    """Transport shape follows the actual family descriptor, with exact proof options."""
    def obj(properties, required=None):
        return {'type': 'object', 'properties': properties, 'additionalProperties': False,
                'required': list(properties) if required is None else required}
    text, number = {'type': 'string', 'minLength': 1}, {'type': 'number'}
    ids = {'type': 'array', 'items': text, 'minItems': 1}
    values = {'number': number, 'path': {'type': 'array', 'items': number, 'minItems': 1},
              'text': text, 'contract': {'type': 'object'}}
    drivers = {}
    for name, descriptor in contract['schema'].items():
        timing, shape = descriptor[3:5]
        kinds = ['historical'] if timing in ('opening', 'actuals') else ['company_guidance', 'analyst_estimate']
        if name in ('perimeter', 'calendar', 'capital.ke') or contract['method_id'] in ('digital_asset_nav', 'property_nav') and name in ('policy', 'forward_year', 'nav_target', 'target_basis'):
            kinds = ['analyst_estimate']
        base = {'value': values[shape], 'kind': {'enum': kinds}, 'evidence_ids': ids,
                'rationale': text, 'valid_until': text,
                'valid_until_basis': {'anyOf': [obj({'policy': {'const': 'same_day'}, 'as_of': text}), text]}}
        required = list(base)
        if 'historical' in kinds or 'company_guidance' in kinds:
            base.update(record_pointer=text, evidence_quote=text, quoted_value=number, quoted_unit=text,
                period_quote=text, evidence_pointer=obj({key: text for key in ('value', 'unit', 'period')}),
                calculation={'type': 'object'}, facts={'type': 'object'})
        if name == 'liquidity_bridge':
            base['facts'] = {'type': 'object'}
        drivers[name] = {'anyOf': [obj(base, required), {'type': 'null'}]}
    schema = obj({'drivers': obj(drivers), 'rationale': text})
    return {'type': 'json_schema', 'json_schema': {'name': 'family_preparation_stage', 'strict': False, 'schema': schema}}
