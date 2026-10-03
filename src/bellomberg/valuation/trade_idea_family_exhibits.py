"""Method-native exhibits from retained common-engine scenario values."""
from math import isfinite


def _finite(value):
    return type(value) in (int, float) and isfinite(value)


def _flatten(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _flatten(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _flatten(child, (*path, index))
    elif _finite(value):
        yield path, value


def _unit(key):
    if any(token in key for token in ('ratio', 'probability', 'ownership', 'mlr', 'margin', 'discount_factor')):
        return 'ratio', False
    if 'shares' in key:
        return 'million shares', False
    if key in ('year', 'period'):
        return 'year / period', False
    if any(token in key for token in ('production', 'reserves', 'units', 'members')):
        return 'method-native physical unit', False
    return 'million', True


def family_exhibits(payload, base, baseline, add):
    """Never force a method's equity/asset economics into an FCFF EV bridge."""
    method = payload.get('method')
    currency = payload.get('financial_currency')
    if method == 'property_nav' and base.get('reported_nav_contract') == 'reported_property_nav_snapshot/1':
        _reported_property_exhibits(payload, base, baseline, add)
        return
    specs = {
        'bank_residual_income': ('closing_common_equity', 'opening_tangible_equity', 'residual_income_value', 'cash_equity_value', 'terminal_common_equity_value'),
        'regulated_rab': ('continuing.closing_rab', 'continuing.closing_cash', 'continuing.closing_debt', 'continuing.shareholder_net_distribution'),
        'insurance_pc_distributable_equity': ('terminal_equity',),
        'insurance_life_distributable_equity': ('terminal_equity',),
        'managed_care_distributable_equity': ('capital.equity_value', 'capital.discounted_terminal_equity', 'capital.terminal_equity'),
        'fund_nav': ('components.gross_assets', 'components.cash', 'components.debt', 'components.preferred', 'components.other_liabilities', 'components.accrued_fees', 'components.distributions_payable', 'components.tax', 'components.equity_adjustments', 'common_equity_nav'),
        'digital_asset_nav': ('components.gross_assets', 'components.cash', 'components.debt', 'components.preferred', 'components.other_liabilities', 'components.accrued_fees', 'components.distributions_payable', 'components.tax', 'components.equity_adjustments', 'common_equity_nav'),
        'property_nav': ('nav.components.gross_assets', 'nav.components.cash', 'nav.components.debt', 'nav.components.preferred', 'nav.components.other_liabilities', 'nav.components.accrued_fees', 'nav.components.distributions_payable', 'nav.components.tax', 'nav.components.equity_adjustments', 'nav.common_equity_nav', 'cash_noi', 'ffo', 'affo'),
        'property_development_fcff': ('opening_excess_cash', 'debt_settlement_value', 'tax_shield_pv', 'expected_financing_cost_pv', 'terminal_value'),
        'resources_asset_dcf': ('opening_excess_cash', 'debt_settlement_value', 'tax_shield_pv', 'expected_financing_cost_pv', 'terminal_value'),
        'development_rnpv': (),
        'mixed_business_sotp': ('central_cost_pv', 'equity_value'),
    }
    values = {'.'.join(map(str, key)): value for key, value in _flatten(base)}
    keys = list(specs.get(method, ()))
    if method == 'development_rnpv':
        keys = [key for key in values if key.startswith('outcomes.') and key.endswith('value_contribution')]
    if method == 'mixed_business_sotp':
        keys = [key for key in values if key.startswith('segments.') and key.endswith('owned_equity_value')] + keys
        keys += [key for key in values if key.startswith('parent_claims.')]
    rows, refs = [], []
    for key in keys:
        if key not in values:
            continue
        value = values[key]
        # Managed care's raw builder retains the capital rows, not its subtotal.
        # A subtotal with no retained authoritative cell is a declared absence.
        try:
            ref = baseline('base.' + key, value)
        except ValueError:
            continue
        label = key
        if (method == 'fund_nav' and base.get('components_basis') == 'incremental_claim_deductions'
                and key in ('components.accrued_fees', 'components.distributions_payable')):
            label = key + ' — additional deduction after covered aggregate claims; economic stock not disaggregated'
        rows.append([label, value]); refs.append([None, ref])
    if rows:
        amount_unit = 'per share' if method == 'development_rnpv' else 'million'
        add('valuation_bridge', 'Riconciliazioni e claims del metodo', 'table', ['Misura', 'Valore'], rows, refs,
            unit=amount_unit, currency=currency, scenario='base',
            explanation='Method-native amounts at their stated periods; totals and claims retain their original signs. No inferred enterprise value.')

    tables = []
    for prefix in ('rows', 'ledger', 'capital.rows'):
        node = base
        for part in prefix.split('.'):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, list) and node and all(isinstance(row, dict) for row in node):
            tables.append((prefix, node))
    for owner in ('assets', 'projects'):
        for ident, asset in (base.get(owner) or {}).items():
            if isinstance(asset, dict) and isinstance(asset.get('rows'), list):
                tables.append((owner + '.' + str(ident) + '.rows', asset['rows']))
    for prefix, table in tables:
        candidates = ('revenue', 'total_revenue', 'premium', 'medical_costs', 'net_income', 'shareholder_net_distribution',
                      'discounted_shareholder_flow', 'closing_statutory_capital', 'closing_rab', 'production', 'remaining_reserves',
                      'collections', 'start_costs', 'end_receipts', 'closing_cash', 'funding_at_start', 'funding_at_end')
        selected = [key for key in candidates if all(_finite(row.get(key)) for row in table)][:8]
        if not selected:
            continue
        rows, refs = [], []
        for index, row in enumerate(table):
            label = row.get('year') or (row.get('period') or {}).get('end') if isinstance(row.get('period'), dict) else row.get('year') or index+1
            line, bindings = [label], [None]
            for key in selected:
                value = row[key]
                line.append(value); bindings.append(baseline(f'base.{prefix}.{index}.{key}', value))
            rows.append(line); refs.append(bindings)
        units = [None, *[_unit(key)[0] for key in selected]]
        currencies = [None, *[currency if _unit(key)[1] else None for key in selected]]
        add('drivers', 'Percorsi economici e capitale', 'table', ['Periodo', *selected], rows, refs,
            unit='per column', column_units=units, column_currencies=currencies,
            currency=None, period=[row[0] for row in rows], scenario='base',
            explanation='Exact retained annual ledger; physical quantities, ratios and monetary flows carry separate units.')
        break

    # Snapshot families expose their dated asset/claim components as drivers.
    if not tables and rows:
        add('drivers', 'Driver patrimoniali alla data del modello', 'table', ['Misura', 'Valore'], rows, refs,
            unit='per share' if method == 'development_rnpv' else 'million', currency=currency, scenario='base',
            explanation='Dated observed assets and claims; no fabricated forecast horizon.')


def _reported_property_exhibits(payload, base, baseline, add):
    """A reported NTA bridge and explicit value shocks are dated NAV exhibits."""
    currency = payload.get('financial_currency')
    keys = ('ifrs_parent_equity_reconstructed', 'epra_nta_reconstructed',
            'adjusted_nta', 'development_overrun_deduction')
    rows = [[key, base[key]] for key in keys]
    refs = [[None, baseline('base.'+key, base[key])] for key in keys]
    add('valuation_bridge', 'Patrimonio IFRS, NTA e rettifiche di scenario', 'table', ['Misura', 'Valore'],
        rows, refs, unit='million', currency=currency, scenario='base',
        explanation='Complete reported asset and liability ledger, parent equity and EPRA NTA adjustments; scenario changes are explicit research sensitivities.')
    reconciliation_keys = ('assets', 'liabilities', 'parent_equity', 'property', 'held_for_sale',
                           'portfolio_memorandum', 'epra_nta', 'pipeline')
    columns = ('calculated', 'reported', 'residual', 'rounding_bound')
    rows, refs = [], []
    for key in reconciliation_keys:
        entry = base['reconciliations'][key]
        rows.append([key, *[entry[column] for column in columns]])
        refs.append([None, *[baseline('base.reconciliations.'+key+'.'+column, entry[column]) for column in columns]])
    add('drivers', 'Riconti primari e precisione pubblicata', 'table', ['Misura', *columns], rows, refs,
        unit='million', currency=currency, scenario='base',
        explanation='Independent complete-ledger, property, EPRA and pipeline reconciliations with their exact residuals and published-precision bounds. JV property is memorandum, not an additional asset.')
    impact_keys = ('standing', 'development', 'land', 'held_for_sale', 'participations')
    rows, refs = [], []
    for scenario in ('bear', 'base', 'bull'):
        result = payload['calculation_details']['scenarios'][scenario]
        values = [result['asset_impacts'][key] for key in impact_keys]
        values += [result['development_overrun_deduction'], result['adjusted_nta'], result['fair_value_per_share']]
        paths = ['asset_impacts.'+key for key in impact_keys] + ['development_overrun_deduction', 'adjusted_nta', 'fair_value_per_share']
        rows.append([scenario, *values])
        refs.append([None, *[baseline(scenario+'.'+path, value) for path, value in zip(paths, values)]])
    add('sensitivity', 'Sensibilita NTA: valori immobiliari e costo residuo', 'table',
        ['Scenario', *impact_keys, 'cost_overrun_deduction', 'adjusted_nta', 'value_per_share'], rows, refs,
        unit='per column', column_units=[None, *(['million']*7), 'per share'],
        column_currencies=[None, *([currency]*8)], currency=None,
        explanation='Scoped property-value shocks and explicit development-cost overruns; these are not reported EPRA metrics, rent forecasts, FFO/AFFO or future covenant compliance.')
