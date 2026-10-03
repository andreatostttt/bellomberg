"""Native common-model output monitors with explicit accounting boundaries.

Regulatory income, asset production and project collections keep their own
definitions. No statutory earnings or EBITDA proxy is inferred from them.
"""
from hashlib import sha256
from math import isclose
from pathlib import Path


# ProfitLoss precedes allocation to noncontrolling interests. US GAAP
# NetIncomeLoss belongs to the parent and is not interchangeable with it.
GROSS_CONSOLIDATED_INCOME = frozenset({('us-gaap', 'ProfitLoss'), ('ifrs-full', 'ProfitLoss')})
COMMON_INCOME = frozenset({('us-gaap', 'NetIncomeLossAvailableToCommonStockholdersBasic')})


def _native_specs(method, model, base):
    """Yield only calculated outputs, with the engine's exact full periods."""
    perimeter, calendar = model['perimeter'], model['calendar']
    entity = perimeter.get('consolidated_entity', perimeter.get('entity'))
    unit = perimeter['currency'] + ' million'
    periods = calendar.get('periods', calendar.get('fiscal_periods'))
    specs = []

    def add(driver, path, value, period, basis, *, owner=entity, measure=None, units=unit, limitation=None):
        specs.append(dict(driver=driver, engine_path=path, value=value, period=period,
                          entity=owner, unit=units, accounting_basis=basis, concepts=measure, limitation=limitation))

    if method in ('bank_residual_income', 'insurance_pc_distributable_equity',
                  'insurance_life_distributable_equity'):
        rows = base['rows']
        if len(rows) != len(periods):
            raise ValueError('Native legal-capital output calendar mismatch')
        bank = method == 'bank_residual_income'
        legal = model['legal_structure']
        for index, (row, period) in enumerate(zip(rows, periods)):
            prefix = f'base.rows.{index}.'
            add('common_net_income' if bank else 'consolidated_gaap_net_income',
                prefix+'consolidated_net_income', row['consolidated_net_income'], period,
                'net_income_available_to_common_after_preferred_and_minorities' if bank else
                    'model_income_reconciled_from_parent_and_legal_entity_GAAP_inputs_attribution_not_defined',
                measure=COMMON_INCOME if bank else None,
                limitation=None if bank else 'The model legal-entity income reconciliation does not define attribution to noncontrolling, parent or common shareholders; an exact typed accounting contract is required')
            add('parent_gaap_net_income', prefix+'parent_gaap_net_income', row['parent_gaap_net_income'],
                period, 'parent_only_GAAP_net_income', owner=legal['parent_entity'])
            for sub_index, sub in enumerate(row['subsidiaries']):
                add('subsidiary.'+sub['id']+'.statutory_net_income',
                    prefix+f'subsidiaries.{sub_index}.statutory_net_income', sub['statutory_net_income'],
                    period, 'legal_entity_GAAP_net_income_plus_explicit_GAAP_to_statutory_bridge', owner=sub['id'])
    elif method == 'managed_care_distributable_equity':
        rows = base['rows']
        if len(rows) != len(periods):
            raise ValueError('Managed-care full-fiscal-year output calendar mismatch')
        from .input_evidence import revenue_concepts
        for index, (row, period) in enumerate(zip(rows, periods)):
            for key, driver, basis, concepts in (
                ('premium', 'premium_revenue', 'model_premium_revenue_excluding_separate_premium_tax_and_investment_income', None),
                ('medical_costs', 'medical_costs', 'model_medical_costs_from_segment_premium_times_MCR_plus_explicit_other_costs', None),
                ('total_revenue', 'total_revenue', 'consolidated_GAAP_total_revenue', revenue_concepts()),
                ('net_income', 'consolidated_gaap_net_income',
                    'consolidated_profit_including_noncontrolling_interests_before_attribution', GROSS_CONSOLIDATED_INCOME)):
                add(driver, f'base.rows.{index}.{key}', row[key], period, basis, measure=concepts)
    elif method == 'regulated_rab':
        rows = base['rows']
        if len(rows) != len(periods):
            raise ValueError('Regulatory output calendar mismatch')
        for index, (row, period) in enumerate(zip(rows, periods)):
            for key, basis in (
                ('allowed_revenue', 'recognized_RAB_return_plus_regulatory_depreciation_allowed_opex_incentives_and_tax_allowance'),
                ('income_after_cash_tax', 'regulatory_revenue_less_cash_opex_book_depreciation_cash_interest_and_cash_tax_not_GAAP_IFRS_income')):
                add(key, f'base.rows.{index}.{key}', row[key], period, basis)
    elif method == 'property_nav':
        period = model['forward_year']
        policy = model['policy']
        for key, basis, concepts in (
            ('gaap_net_income', 'model_property_net_income_after_declared_costs_attribution_not_defined', None),
            ('cash_noi', 'property_cash_rent_and_recoveries_less_cash_opex_and_cash_lease_incentives', None),
            ('ffo', policy['ffo_basis'], None), ('affo', policy['affo_basis'], None)):
            add(key, 'base.'+key, base[key], period, basis, measure=concepts,
                limitation='The property model income calculation does not define attribution to noncontrolling, parent or common shareholders; an exact typed accounting contract is required'
                    if key == 'gaap_net_income' else None)
    elif method in ('property_development_fcff', 'resources_asset_dcf'):
        assets = method == 'resources_asset_dcf'
        owner_key = 'assets' if assets else 'projects'
        inventory = model[owner_key]
        if set(inventory) != set(base[owner_key]):
            raise ValueError('Native output asset/project perimeter mismatch')
        for identity in sorted(inventory):
            asset, rows = inventory[identity], base[owner_key][identity]['rows']
            if len(rows) != len(periods):
                raise ValueError('Finite asset/project output calendar mismatch')
            metrics = (('production', 'physical_production_in_the_declared_asset_volume_unit', asset['volume_unit']),
                       ('revenue', 'asset_physical_production_times_asset_price_not_consolidated_reported_revenue', unit),
                       ('operating_cost', 'asset_production_times_unit_cash_cost_plus_fixed_cash_cost', unit)) if assets else (
                       ('revenue', 'project_delivered_lots_times_price_per_lot_not_consolidated_reported_revenue', unit),
                       ('collections', 'project_opening_receivable_plus_sales_less_closing_receivable_cash_collections', unit))
            for index, (row, period) in enumerate(zip(rows, periods)):
                for key, basis, metric_unit in metrics:
                    add(('asset.' if assets else 'project.')+identity+'.'+key,
                        f'base.{owner_key}.{identity}.rows.{index}.{key}', row[key], period, basis,
                        owner=asset['taxpayer_entity'], units=metric_unit)
    return specs


def native_earnings_expectations(payload, packet, result):
    """Extend the public expectation contract without mutating its model."""
    from .trade_idea_earnings_facts import _duration, _finite, _measure_binding
    from .input_preparation import _catalog, _day
    from .trade_idea_exhibits import _baselines
    from openpyxl import load_workbook
    method = payload.get('method')
    unavailable = {
        'fund_nav': 'Dated investment-fund NAV is a balance-sheet measure; no corporate earnings forecast is calculated',
        'digital_asset_nav': 'Dated digital-asset NAV has no calculated corporate accounting earnings path',
        'exposure_analysis': 'Observed holdings and exposures have no intrinsic corporate earnings forecast',
        'development_rnpv': 'Contingent development outcome cash flows have no qualified accounting-earnings output definition',
        'mixed_business_sotp': 'Segment valuations need each exact child-model generation and earnings definition before freezing parent expectations',
    }
    if method in unavailable:
        result.update(status='unavailable', reasons=[unavailable[method]])
        return result
    if method == 'property_nav':
        from .property_nav_requirements import reported_policy_selected
        records = payload.get('acquisition_snapshot', {}).get('case', {}).get('records') or []
        if reported_policy_selected(records):
            result.update(status='unavailable', reasons=['Reported property NAV is a dated balance-sheet and EPRA NTA reconciliation; this variant calculates no corporate earnings forecast'])
            return result
    try:
        case = payload['acquisition_snapshot']['case']
        model = {row['driver']: row['value'] for row in case['records'] if row.get('scenario') == 'model'}
        base = (payload.get('calculation_details', {}).get('scenarios') or
                payload.get('managed_care', {}).get('scenarios') or {})['base']
        specs = _native_specs(method, model, base)
        if not specs:
            result.update(status='unavailable', reasons=['No retained native output definition for method '+str(method)])
            return result
        dossier = payload.get('preparation', {}).get('review_basis', {}).get('dossier') or {}
        catalog, issues, _ = _catalog(dossier.get('documents') or [], _day(case['as_of']))
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        path = Path(payload['path'])
        if sha256(path.read_bytes()).hexdigest() != payload['workbook_sha256']:
            raise ValueError('Workbook changed while freezing native earnings expectations')
        workbook = load_workbook(path, data_only=False, read_only=True)
        try:
            baselines = _baselines(workbook)
        finally:
            workbook.close()
        definitions, period_indexes = {}, {}
        for spec in specs:
            period, value, engine_path = spec['period'], spec['value'], spec['engine_path']
            duration = period['start']+'/'+period['end']
            _duration(duration)
            baseline = baselines.get(engine_path)
            if not _finite(value) or not baseline or not isclose(value, baseline[0], rel_tol=1e-10, abs_tol=1e-9):
                raise ValueError('Native earnings engine/workbook baseline mismatch: '+engine_path)
            key = (spec['driver'], spec['entity'], spec['unit'])
            if key not in definitions:
                definitions[key] = (_measure_binding(list(catalog.values()), spec['concepts'], spec['entity'], spec['unit'])
                    if spec['concepts'] else (None, [], spec['limitation'] or
                        'No automatic accounting proxy: a whole typed primary contract must prove this exact native measure'))
            measure, sources, gap = definitions[key]
            result['expectations'].append({'field': 'native_model_actuals', 'driver': spec['driver'],
                'entity': spec['entity'], 'period': duration, 'period_start': period['start'], 'period_end': period['end'],
                'unit': spec['unit'], 'accounting_basis': spec['accounting_basis'], 'scenario': 'base',
                'kind': 'engine_calculated_forecast', 'value': value, 'cell': baseline[1], 'display_cell': None,
                'forecast_index': period_indexes.setdefault(duration, len(period_indexes)),
                'model_period': duration, 'primary_measure': measure,
                'source_binding': {'engine_path': engine_path, 'cell': baseline[1], 'primary_definitions': sources},
                'actual_proof_status': 'exact_normalized_or_typed_contract' if measure else 'typed_contract_only',
                'limitation': gap, 'as_of': case['as_of']})
        result['unavailable_metrics'].append({'driver': 'ebitda', 'reason': 'No qualified issuer EBITDA definition; no native income or cash-flow proxy inferred'})
        if method == 'bank_residual_income':
            result['unavailable_metrics'].append({'driver': 'reported_roe', 'reason': 'A reported ROE needs its own average-equity and attribution definition; terminal ROE is an assumption'})
        result['status'] = 'ready'
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        result['reasons'].append(str(exc))
        result['expectations'] = []
    return result
