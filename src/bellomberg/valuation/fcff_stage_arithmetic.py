"""Read-only FCFF arithmetic on complete forecast paths; never infer a driver."""
from .dcf_quality import _finite


PATHS = ('revenue_growth', 'gross_margin', 'rnd_pct', 'sga_pct', 'capdev_pct',
         'da_tan_pct', 'tax_rate', 'capex_pct', 'nwc_pct', 'opening_intangible_amortization')
TERMINAL_KEYS = {'normalized_ebit', 'capitalized_research_adjustment', 'cycle_adjustment',
                 'expiring_product_loss', 'replacement_product_income', 'other_adjustment'}

SEMANTICS = {
    'version': 1,
    'da_tan_pct_denominator': 'current-period revenue',
    'ratio_denominators': 'gross_margin, rnd_pct, sga_pct, capdev_pct, da_tan_pct, capex_pct and nwc_pct multiply current-period revenue; tax_rate multiplies positive EBIT. Never supply a PPE-based depreciation rate as da_tan_pct.',
    'ebitda': 'revenue * (gross_margin - rnd_pct - sga_pct + capdev_pct)',
    'ebit': 'EBITDA - revenue * da_tan_pct - research_amortization',
    'classification': 'Reconcile reported accounting to these engine buckets. Reported gross profit, R&D and SG&A may already deduct tangible depreciation: remove every overlap exactly once before the separate total D&A row. Historical allocations require source proof; an undisclosed allocation may only be an explicit prospective analyst estimate, never reported history. Reconcile operating leases and non-research amortization explicitly. Do not double-count expenses, invent a reclassification or change the engine convention.',
    'research_basis': 'rnd_pct is current-period gross research expense before subtracting capitalized research C. Do not supply research already net of C and then add C again. Exclude research amortization modeled separately from expense buckets. opening_intangible_amortization is only runoff of research assets existing before the forecast, not non-research intangible amortization. It is added to amortization of forecast capitalization, including the current cohort under the engine convention. For the O+D+C and O+C-A identities, O must be on the declared fully-expensed current-research basis, with tangible depreciation deducted and separately modeled research amortization excluded. Published operating income is not automatically that basis. If starting costs are net of capitalization or include research amortization, disclose the numerical accounting bridge for each period, preserve the underlying operating judgment and declare any missing split. Never infer an unsupported split or alter estimates automatically.',
    'operating_income_bridge': 'For each forecast period let O be the operating profit on the declared reported-equivalent basis, D total tangible depreciation, C capitalized research and A research amortization. The required identities are EBITDA = O + D + C and EBIT = O + C - A. With cash expense buckets the explicit tangible D&A removed from gross profit, R&D and SG&A must sum to D; a partial gross-profit add-back while leaving all other expenses reported leaves a residual, not an exact aggregate bridge. Alternatively gross_margin may be an explicitly labelled aggregate engine bucket = reported-equivalent gross margin + D/revenue, with R&D and SG&A left reported. That bucket is not reported gross margin, not a physical COGS allocation and not management guidance. Explain the selected mapping and allocation uncertainty in the driver and scenario rationales; preserve the reported-equivalent operating-profit judgment unless explicitly revising it. Never infer missing allocations or patch numerical estimates automatically.',
    'fcff': 'EBIT - max(EBIT,0)*tax_rate + depreciation + research_amortization - capex - capitalized_development - change_in_working_capital',
    'terminal': 'Use final-year engine EBIT plus the six-field terminal bridge; research_normalization_adjustment is computed. Cycle/expiry/replacement/other adjustments are sourced judgments, never balancing plugs. On the normalized-year basis NOPAT = normalized EBIT*(1-tax_rate) and reinvestment = NOPAT*g/RONIC. FIRST continuing-year FCFF = (NOPAT - reinvestment)*(1+g), and terminal value = FIRST continuing-year FCFF/(WACC-g). Distinguish the normalized year from the first continuing year; omitting (1+g) understates the engine terminal value. Reconcile the explicit-to-continuing transition and state unsupported gaps.',
}


def with_engine_guidance(dossier, contract):
    """Explain existing calculations on a prompt copy without revising any input."""
    from copy import deepcopy
    if ((contract.get('preparation_stage') or {}).get('scope') not in ('bear', 'base', 'bull')
            or not isinstance(dossier.get('completed_plan'), dict)):
        return dossier
    projected = deepcopy(dossier)
    projected['fcff_engine_semantics'] = deepcopy(SEMANTICS)
    arithmetic = forecast_arithmetic(dossier['completed_plan'])
    if arithmetic:
        projected['derived_fcff_forecasts'] = arithmetic
    return projected


def forecast_arithmetic(plan):
    """Called after ordinary driver compilation, including before resuming a seed."""
    from .dcf_buyside_v3 import _scenario_numbers
    from .operating_adapter import operating_revenue_errors, operating_revenue_path, terminal_bridge_errors
    opening = plan['model']
    needed = {'historical_revenue', 'opening_nwc', 'capdev_amortization_years', 'calendar'}
    if not needed <= opening.keys():
        return {}
    values = {name: opening[name]['value'] for name in needed}
    count = len(values['calendar']['periods'])
    result = {}
    for scope, drivers in plan['scenarios'].items():
        if not set(PATHS) <= drivers.keys():
            continue
        scenario = {name: item['value'] for name, item in drivers.items()}
        if any(not isinstance(scenario[name], list) or len(scenario[name]) != count
               or any(not _finite(value) for value in scenario[name]) for name in PATHS):
            raise ValueError('FCFF forecast arithmetic: incomplete or nonfinite paths in ' + scope)
        revenue = scenario.get('revenue_build')
        if revenue is not None and (not isinstance(revenue, dict) or revenue.get('basis') != 'segment_guidance'):
            issues = operating_revenue_errors(revenue, values['historical_revenue'], scenario['revenue_growth'])
            if issues:
                raise ValueError('FCFF forecast arithmetic: ' + scope + ': revenue_build: ' + '; '.join(issues))
        numbers = _scenario_numbers({'documented_inputs': True,
            'capdev_amortization_years': values['capdev_amortization_years']}, scenario,
            values['historical_revenue'], nwc0=values['opening_nwc'],
            revenue_path=operating_revenue_path(revenue) if revenue is not None else None)
        if any(not _finite(value) for row in numbers.values() for value in row):
            raise ValueError('FCFF forecast arithmetic: nonfinite calculation in ' + scope)
        if 'terminal_bridge' in scenario:
            terminal = scenario['terminal_bridge']
            if (not isinstance(terminal, dict) or set(terminal) != TERMINAL_KEYS
                    or any(not _finite(value) for value in terminal.values())
                    or terminal['expiring_product_loss'] < 0 or terminal['replacement_product_income'] < 0):
                raise ValueError('FCFF forecast arithmetic: invalid terminal bridge in ' + scope)
            issues = terminal_bridge_errors(numbers, scenario)
            if issues:
                raise ValueError('FCFF forecast arithmetic: ' + scope + ': ' + '; '.join(issues))
        result[scope] = {'final_year': {name: row[-1] for name, row in numbers.items()},
            'research_normalization_adjustment': numbers['research_amortization'][-1]
                - numbers['revenue'][-1] * scenario['capdev_pct'][-1]}
    return result
