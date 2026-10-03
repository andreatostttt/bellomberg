"""Preparation contracts reuse the exact schemas consumed by the family engines.

This catalog describes software coverage. It never qualifies an issuer or supplies
an economic value, forecast, legal perimeter or missing observation.
"""
from copy import deepcopy


SNAPSHOT_METHODS = frozenset({"fund_nav", "digital_asset_nav", "property_nav", "exposure_analysis"})
FINITE_METHODS = frozenset({"property_development_fcff", "resources_asset_dcf", "development_rnpv"})


def _selection_records(plan):
    if not isinstance(plan, dict):
        return []
    return [{'driver': driver, 'scenario': scenario, 'value': item.get('value')}
            for scenario, scope in [('model', plan.get('model') or {}), *(plan.get('scenarios') or {}).items()]
            if isinstance(scope, dict) for driver, item in scope.items() if isinstance(item, dict)]


def method_requirements(method, plan=None, *, records=()):
    """Software requirements for an exact contract variant; never source approval."""
    from .method_registry import requirements_for_records
    return requirements_for_records(method, _selection_records(plan) if plan is not None else records)


def method_schema(method, plan=None):
    """Return schema and driver entities, using the declared legal structure only."""
    modules = {
        "operating_fcff": ("operating_adapter", "SCHEMA"),
        "bank_residual_income": ("bank_adapter", "SCHEMA"),
        "regulated_rab": ("rab_adapter", "SCHEMA"),
        "fund_nav": ("nav_adapter", "FUND"),
        "digital_asset_nav": ("nav_adapter", "DIGITAL"),
        "insurance_pc_distributable_equity": ("insurance_adapter", "SCHEMA"),
        "insurance_life_distributable_equity": ("insurance_adapter", "SCHEMA"),
        "property_nav": ("real_estate_adapter", "SCHEMA"),
        "property_development_fcff": ("property_development_adapter", "SCHEMA"),
        "resources_asset_dcf": ("resources_adapter", "SCHEMA"),
        "development_rnpv": ("development_adapter", "SCHEMA"),
        "mixed_business_sotp": ("sotp_adapter", "SCHEMA"),
        "exposure_analysis": ("trade_idea_exposure", "SCHEMA"),
    }
    if method == "managed_care_distributable_equity":
        return managed_schema(plan)
    if method == 'property_nav':
        from .property_nav_requirements import REPORTED_SCHEMA, reported_policy_selected
        if reported_policy_selected(_selection_records(plan)):
            return deepcopy(REPORTED_SCHEMA), {}
    if method == 'exposure_analysis':
        from .trade_idea_commodity_exposure import selected_records, SCHEMA as COMMODITY_SCHEMA
        if selected_records(_selection_records(plan)):
            return deepcopy(COMMODITY_SCHEMA), {}
    if method not in modules:
        raise ValueError("No documented preparation schema for method: " + str(method))
    from importlib import import_module
    module, symbol = modules[method]
    schema = deepcopy(getattr(import_module("bellomberg.valuation." + module), symbol))
    entities = {}
    if method in ("bank_residual_income", "insurance_pc_distributable_equity", "insurance_life_distributable_equity") and plan is not None:
        from .input_preparation import _bank_schema
        perimeter = ((plan.get("model", {}).get("perimeter") or {}).get("value") or {})
        expanded, entities, issues = _bank_schema(plan, perimeter)
        if issues:
            raise ValueError("; ".join(row["reason"] for row in issues))
        schema.update({key: value for key, value in expanded.items() if key.startswith("capital.")})
        if method.startswith("insurance_"):
            from .insurance_adapter import PC_SCHEMA, LIFE_SCHEMA
            products = LIFE_SCHEMA if "life" in method else PC_SCHEMA
            legal = plan["model"]["legal_structure"]["value"]
            for index, sub in enumerate(legal["subsidiaries"]):
                for key, definition in products.items():
                    name = f"insurance.{index}.{key}"
                    schema[name] = definition
                    entities[name] = sub["id"]
    return schema, entities


def managed_schema(plan=None):
    """Managed-care has its own calendar and per-segment/per-entity descriptors."""
    from .managed_care_adapter import _descriptor
    structures = {
        "perimeter": ("valuation_perimeter", "contract", "scope", "future", "contract", "model"),
        "calendar": ("forecast_periods", "contract", "calendar", "future", "contract", "model"),
        "quotation": ("quotation_units", "contract", "quotation", "opening", "contract", "model"),
    }
    from .managed_care import ANNUAL_INPUTS
    from .distributable_equity import CAPITAL_FIELDS, SUB_FIELDS, SUB_PATHS, CASH_SIGNS
    names = {'actuals.' + key for key in ('premium', 'medical_costs', 'gna', 'net_income')}
    names.update(ANNUAL_INPUTS)
    names.update('capital.' + key for key in CAPITAL_FIELDS - {'subsidiaries', 'parent_cash_flows'})
    names.update('capital.parent_cash_flows.' + key for key in CASH_SIGNS)
    model = (plan or {}).get("model") or {}
    perimeter = (model.get("perimeter") or {}).get("value") or {}
    subsidiaries = perimeter.get('subsidiaries') or []
    names.update(f'capital.subsidiaries.{index}.' + key for index in range(len(subsidiaries)) for key in SUB_FIELDS - {'id'})
    scenarios = (plan or {}).get('scenarios') or {}
    segments, adjustments = set(), set()
    for drivers in scenarios.values():
        if not isinstance(drivers, dict):
            raise ValueError('Managed-care scenario must be a driver map')
        for name in drivers:
            if name.startswith('segments.'):
                segments.add(name.split('.')[1])
            elif name.startswith('adjustments_after_tax.'):
                adjustments.add(name.split('.')[1])
    if plan is not None and (not segments or not adjustments):
        raise ValueError('Managed-care needs explicit premium/MCR segments and after-tax reconciliation, including observed zero adjustments')
    names.update(f'segments.{name}.{key}' for name in segments for key in ('premium', 'mcr'))
    names.update('adjustments_after_tax.' + name for name in adjustments)
    entities = {}
    for driver in sorted(names):
            if driver in structures:
                continue
            descriptor = _descriptor(driver)
            if descriptor is None:
                raise ValueError("Managed-care driver not consumed: " + str(driver))
            field, unit, basis, timing = descriptor
            key = driver.split('.')[-1]
            shape = ('text' if unit == 'text' else 'path' if timing == 'annual' or timing == 'future'
                     and (key in SUB_PATHS or '.parent_cash_flows.' in driver or key in
                          ('parent_cash_minimum', 'parent_gaap_net_income', 'consolidation_adjustments', 'discount_periods'))
                     else 'number')
            structures[driver] = (field, unit, basis, timing, shape, 'model' if timing == 'actuals' else 'scenario')
            if ".subsidiaries." in driver:
                try:
                    entities[driver] = subsidiaries[int(driver.split(".")[2])]["id"]
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise ValueError("Managed-care subsidiary identity missing: " + driver) from exc
            elif field == "parent_ledger":
                entities[driver] = perimeter.get("parent_entity")
            else:
                entities[driver] = perimeter.get("consolidated_entity")
    return structures, entities


def managed_calendar(plan, cutoff):
    from datetime import timedelta
    from .input_preparation import _day, _issue, _text
    model = plan.get('model') or {}
    perimeter = (model.get('perimeter') or {}).get('value')
    calendar = (model.get('calendar') or {}).get('value')
    issues = []
    required = {'currency', 'consolidated_entity', 'parent_entity', 'subsidiaries', 'share_class', 'share_basis'}
    if (not isinstance(perimeter, dict) or set(perimeter) != required or
            any(not _text(perimeter.get(key)) for key in required - {'subsidiaries'})):
        return {}, {}, None, [_issue('perimeter', 'managed_perimeter', 'Complete managed-care legal/consolidated perimeter required')]
    subsidiaries = perimeter['subsidiaries']
    if (not isinstance(subsidiaries, list) or not subsidiaries or any(not isinstance(sub, dict)
            or set(sub) != {'id', 'regime'} or not _text(sub['id']) or not _text(sub['regime']) for sub in subsidiaries)):
        issues.append(_issue('perimeter', 'managed_legal_entities', 'Exact regulated legal subsidiaries required'))
    keys = {'valuation_date', 'day_count', 'actuals_kind', 'actuals_start', 'fiscal_periods', 'years', 'actuals_year'}
    if not isinstance(calendar, dict) or set(calendar) != keys:
        return perimeter, {}, None, issues + [_issue('calendar', 'managed_calendar', 'Exact managed-care fiscal and actuals calendar required')]
    opening, start = _day(calendar['valuation_date']), _day(calendar['actuals_start'])
    if not opening or not start or start > opening or opening > cutoff or calendar['day_count'] != 'ACT/365F':
        issues.append(_issue('calendar', 'managed_cutoff', 'Dated actuals and ACT/365F convention must precede the information cutoff'))
    years, periods = calendar['years'], calendar['fiscal_periods']
    if (not isinstance(years, list) or len(years) != 10 or any(type(year) is not int for year in years)
            or not isinstance(periods, list) or len(periods) != len(years)):
        return perimeter, calendar, None, issues + [_issue('calendar', 'horizon', 'Managed care: 10 explicit fiscal years required')]
    previous = None
    for index, period in enumerate(periods):
        first = _day(period.get('start')) if isinstance(period, dict) else None
        end = _day(period.get('end')) if isinstance(period, dict) else None
        if (not first or not end or period.get('year') != years[index] or period.get('payment_date') != period.get('end')
                or end < first or previous and first != previous + timedelta(days=1)
                or not 364 <= (end-first).days+1 <= 371):
            issues.append(_issue('calendar', 'managed_periods', 'Whole contiguous fiscal years, year IDs and dated payments required'))
        previous = end
    if calendar['actuals_kind'] not in ('FY', 'interim') or type(calendar['actuals_year']) is not int:
        issues.append(_issue('calendar', 'managed_actuals', 'Explicit FY/interim source accounting scope required'))
    if opening and calendar['actuals_kind'] == 'FY' and (opening >= _day(periods[0]['start'])
            or _day(periods[0]['start']) != opening + timedelta(days=1)):
        issues.append(_issue('calendar', 'managed_opening', 'FY opening must immediately precede forecast fiscal years'))
    if opening and calendar['actuals_kind'] == 'interim' and not (_day(periods[0]['start']) <= start <= opening < _day(periods[0]['end'])):
        issues.append(_issue('calendar', 'managed_opening', 'Interim actuals must belong to the first forecast fiscal year'))
    normalized = deepcopy(perimeter)
    normalized['entity'] = perimeter['consolidated_entity']
    span = '|'.join(period['start'] + '/' + period['end'] for period in periods) if not issues else None
    return normalized, calendar, span, issues


def supported_methods():
    from .method_registry import _METHODS
    # Registry remains the source of method identity. Exposure uses its own
    # observed-holdings contract and must never be presented as intrinsic DCF.
    return tuple(method for method, data in _METHODS.items()
                 if data.get("record_adapter") or method in ("managed_care_distributable_equity", "exposure_analysis"))


def horizon(method):
    if method in SNAPSHOT_METHODS:
        return {"kind": "snapshot", "target_annual_periods": 0}
    if method in FINITE_METHODS:
        return {"kind": "finite_asset_life", "target_annual_periods": None}
    return {"kind": "continuing_business", "target_annual_periods": 10}


def method_guide(method, text):
    headings = {
        "operating_fcff": "## Operating FCFF", "bank_residual_income": "## Bank and balance-sheet",
        "managed_care_distributable_equity": "## Managed care", "regulated_rab": "## Regulated networks",
        "fund_nav": "## Funds, investment holdings and digital-asset NAV", "digital_asset_nav": "## Funds, investment holdings and digital-asset NAV",
        "insurance_pc_distributable_equity": "## Property/casualty insurance", "insurance_life_distributable_equity": "## Life insurance",
        "property_nav": "## Stabilized property owners and equity REITs", "property_development_fcff": "## Finite property development",
        "resources_asset_dcf": "## Producing resource assets", "development_rnpv": "## Conditional licensed development (rNPV)", "mixed_business_sotp": "## Mixed businesses: documented equity SOTP",
        "exposure_analysis": "## ETF",
    }
    heading = headings.get(method)
    if heading and heading in text:
        start = text.index(heading)
        end = text.find("\n## ", start + len(heading))
        return text[start:end if end >= 0 else len(text)]
    raise ValueError("Installed economic guide has no section for " + str(method))
