"""Reported IFRS/EPRA NTA snapshot with explicit asset-value sensitivities.

This branch does not project rent, FFO, AFFO, cash generation or future covenant
compliance. Equity-method participations remain carrying equity, never a second
addition of their property memorandum. Development is carrying value, not profit.
"""
from copy import deepcopy
from .dcf_quality import SCENARIOS, _finite, _text
from .documented_inputs import bind_inputs, finish_documented
from .property_nav_requirements import REPORTED_SCHEMA

ASSETS = ('intangibles', 'investment_property', 'other_tangible', 'financial_fixed',
    'other_fixed_receivables', 'participations', 'held_for_sale', 'trade_receivables',
    'current_tax_other', 'cash', 'current_accruals')
LIABILITIES = ('noncurrent_provisions', 'noncurrent_debt', 'noncurrent_other_financial',
    'noncurrent_payables', 'deferred_tax', 'current_debt', 'current_other_financial',
    'current_payables', 'current_other_liabilities', 'current_accruals')
SHOCKS = ('standing', 'development', 'land', 'held_for_sale', 'participations',
    'development_cost_overrun')
TARGET_BASIS = 'EPRA_NTA_scoped_asset_sensitivity'


def numbers(value, keys, *, signed=False):
    return isinstance(value, dict) and set(value) == set(keys) and all(
        _finite(v) and (signed or v >= 0) for v in value.values())


def reconciliation(actual, reported, precision, count):
    """Rounded n summands and one reported total: each contributes half a unit."""
    residual = actual - reported
    tolerance = (count + 1) * precision / 2
    return {'calculated': actual, 'reported': reported, 'residual': residual,
            'rounding_bound': tolerance, 'within_reported_precision': abs(residual) <= tolerance + 1e-9}


def project_reported(model, scenarios, problem, *, historical_only=False):
    """Pure calculator; contracts reject omissions, duplicate scopes and extra keys."""
    b, p, e, c, restrictions, development = (model[k] for k in
        ('balance_sheet', 'property_reconciliation', 'epra_bridge', 'claims', 'restrictions', 'development'))
    if (set(b) != {'assets', 'liabilities', 'reported_assets', 'reported_liabilities',
            'reported_parent_equity', 'reported_minority_equity', 'money_precision'} or
        not numbers(b.get('assets'), ASSETS) or not numbers(b.get('liabilities'), LIABILITIES) or
        not all(_finite(b.get(k)) and b[k] >= 0 for k in ('reported_assets', 'reported_liabilities',
            'reported_parent_equity', 'reported_minority_equity')) or
        type(b.get('money_precision')) not in (int,float) or b.get('money_precision') not in (0.000001, 0.001, 1.0)):
        problem('balance_sheet', 'Ledger IFRS completo e precisione di pubblicazione esplicita richiesti'); return {}
    precision = b['money_precision']; a = b['assets']; l = b['liabilities']
    assets, liabilities = sum(a.values()), sum(l.values())
    ifrs = assets - liabilities - b['reported_minority_equity']
    checks = {'assets': reconciliation(assets, b['reported_assets'], precision, len(a)),
        'liabilities': reconciliation(liabilities, b['reported_liabilities'], precision, len(l)),
        'parent_equity': reconciliation(ifrs, b['reported_parent_equity'], precision, len(a)+len(l)+1)}
    for name, check in checks.items():
        if not check['within_reported_precision']:
            problem('balance_sheet', name + ': residuo oltre il limite derivato dalle unita pubblicate')
    propkeys = {'standing', 'development', 'land', 'held_for_sale', 'jv_property_memorandum',
        'portfolio_fair_value', 'leasehold_rights_in_standing', 'jv_ownership', 'jv_basis'}
    if (set(p) != propkeys or not numbers({k:p.get(k) for k in propkeys-{'jv_ownership','jv_basis'}},
            propkeys-{'jv_ownership','jv_basis'}) or p.get('jv_basis') != 'equity_method_property_memorandum_only' or
        not isinstance(p.get('jv_ownership'), dict) or not p['jv_ownership'] or
        any(not _text(name) or not _text(value) for name,value in p['jv_ownership'].items())):
        problem('property_reconciliation', 'Perimetro, valori consolidati/JV, diritti d uso e ownership documentati richiesti'); return {}
    checks['property'] = reconciliation(p['standing']+p['development']+p['land'], a['investment_property'], precision, 3)
    checks['held_for_sale'] = reconciliation(p['held_for_sale'], a['held_for_sale'], precision, 1)
    checks['portfolio_memorandum'] = reconciliation(a['investment_property']+a['held_for_sale']+p['jv_property_memorandum'],
        p['portfolio_fair_value'], precision, 3)
    for name in ('property', 'held_for_sale', 'portfolio_memorandum'):
        if not checks[name]['within_reported_precision']: problem('property_reconciliation', name + ': perimetro non riconciliato')
    if p['leasehold_rights_in_standing'] > p['standing']:
        problem('property_reconciliation', 'Diritti d uso superiori al patrimonio esistente: nessuna ipotesi freehold implicita')
    if (set(e) != {'basis', 'adjustments', 'reported_nav', 'reported_per_share', 'per_share_decimals'} or
        e['basis'] != 'EPRA_NTA' or not numbers(e.get('adjustments'),
            ('deferred_tax_property', 'financial_instruments', 'intangibles'), signed=True) or
        not _finite(e.get('reported_nav')) or not _finite(e.get('reported_per_share')) or
        type(e.get('per_share_decimals')) is not int or not 0 <= e['per_share_decimals'] <= 6):
        problem('epra_bridge', 'Ponte EPRA NTA firmato e precisione per azione richiesti'); return {}
    if abs(e['adjustments']['intangibles'] + a['intangibles']) > precision:
        problem('epra_bridge', 'Intangibili non dedotti una volta nel NTA')
    nta = ifrs + sum(e['adjustments'].values())
    checks['epra_nta'] = reconciliation(nta, e['reported_nav'], precision, len(a)+len(l)+5)
    if not checks['epra_nta']['within_reported_precision']: problem('epra_bridge', 'NTA pubblicato non riconciliato al ledger e alle rettifiche')
    if (set(c) != {'entitled_shares_m', 'epra_diluted_shares_m', 'reported_diluted_ifrs_nav',
            'hybrid_claims', 'dilution_basis', 'post_balance_events'} or
        not all(_finite(c.get(k)) and c[k] >= 0 for k in
            ('entitled_shares_m','epra_diluted_shares_m','reported_diluted_ifrs_nav','hybrid_claims')) or
        c['dilution_basis'] != 'reported_EPRA_after_options_convertibles_other_equity' or
        not _text(c.get('post_balance_events')) or model['shares'] <= 0):
        problem('claims', 'Denominatore, NAV diluito, hybrids ed eventi successivi espliciti richiesti'); return {}
    if (c['hybrid_claims'] != 0 or abs(c['epra_diluted_shares_m'] - model['shares']) > 0.0000005 or
        abs(c['entitled_shares_m'] - model['shares']) > 0.0000005 or
        abs(c['reported_diluted_ifrs_nav'] - b['reported_parent_equity']) > precision):
        problem('claims', 'Diluizione o hybrids richiedono un ponte specifico: la parita non e presunta')
    reported_ratio = e['reported_nav'] / model['shares']
    if abs(reported_ratio-e['reported_per_share']) > 0.5*10**(-e['per_share_decimals'])+1e-9:
        problem('epra_bridge', 'NTA per azione non coerente con il denominatore e precisione riportati')
    if (set(restrictions) != {'covenants', 'guarantees', 'encumbrance', 'compliance_date'} or
        restrictions['compliance_date'] != model['calendar']['valuation_date'] or
        not _text(restrictions.get('encumbrance')) or not isinstance(restrictions.get('guarantees'), dict) or
        not restrictions['guarantees'] or any(not _finite(v) or v < 0 for v in restrictions['guarantees'].values()) or
        not isinstance(restrictions.get('covenants'), dict) or set(restrictions['covenants']) != {
            'interest_coverage','statutory_gearing','consolidated_gearing','non_prelet_development','subsidiary_debt'}):
        problem('restrictions', 'Covenants/garanzie/encumbrance datati completi richiesti'); return {}
    for name, covenant in restrictions['covenants'].items():
        if (not _text(name) or not isinstance(covenant,dict) or
            set(covenant) != {'observed', 'threshold', 'operator', 'basis'} or
            not _finite(covenant['observed']) or not _finite(covenant['threshold']) or
            covenant['operator'] not in ('<','<=','>','>=') or not _text(covenant['basis'])):
            problem('restrictions', 'Covenant incompleto o convenzione non supportata'); continue
        from operator import lt, le, gt, ge
        if not {'<':lt,'<=':le,'>':gt,'>=':ge}[covenant['operator']](covenant['observed'],covenant['threshold']):
            problem('restrictions', name + ': covenant osservato violato, nessuna compliance automatica')
    if (set(development) != {'pipeline_total', 'invested', 'cost_to_come', 'undrawn_confirmed_lines',
            'debt_maturities_to_end_next_year', 'funding_basis', 'pipeline_scope'} or
        not numbers({k:development.get(k) for k in set(development)-{'funding_basis','pipeline_scope'}},set(development)-{'funding_basis','pipeline_scope'}) or
        development['funding_basis'] != 'reported_aggregate_not_project_financing_or_future_profit' or
        development['pipeline_scope'] != 'reported_projects_excluding_energy_and_land_reserves'):
        problem('development', 'Pipeline, costo residuo e linee/scadenze documentati richiesti'); return {}
    checks['pipeline'] = reconciliation(development['invested']+development['cost_to_come'], development['pipeline_total'], 1.0, 2)
    if not checks['pipeline']['within_reported_precision']: problem('development', 'Pipeline non riconciliata a investito e cost to come')
    if historical_only:
        return {'reconciliations':deepcopy(checks),'ifrs_parent_equity_reconstructed':ifrs,
                'epra_nta_reconstructed':nta}
    results = {}
    for scenario in SCENARIOS:
        sc = scenarios[scenario]; shocks = sc['asset_shocks']
        if (not numbers(shocks, SHOCKS, signed=True) or
            any(shocks[k] <= -1 for k in SHOCKS if k != 'development_cost_overrun') or
            shocks['development_cost_overrun'] < 0 or sc['nav_target'] <= 0 or
            sc['target_basis'] != TARGET_BASIS):
            problem('asset_shocks', scenario + ': sensibilita/target completi motivati richiesti'); continue
        impacts = {k:p[k]*shocks[k] for k in ('standing','development','land','held_for_sale')}
        impacts['participations'] = a['participations']*shocks['participations']
        overrun = development['cost_to_come']*shocks['development_cost_overrun']
        adjusted_nta = nta+sum(impacts.values())-overrun
        if adjusted_nta <= 0: problem('asset_shocks', scenario + ': patrimonio rettificato non positivo')
        results[scenario] = {'fair_value_per_share': adjusted_nta*sc['nav_target']/model['shares'],
            'value_basis':'equity', 'reported_nav_contract':'reported_property_nav_snapshot/1',
            'ifrs_parent_equity_reconstructed':ifrs, 'epra_nta_reconstructed':nta,
            'adjusted_nta':adjusted_nta, 'nav_target':sc['nav_target'], 'asset_impacts':impacts,
            'development_overrun_deduction':overrun, 'reconciliations':deepcopy(checks),
            'valuation_basis':'Reported EPRA NTA with declared asset-value sensitivities; not rent/FFO/AFFO forecasts or future covenant compliance.',
            'jv_property_memorandum_excluded_from_nav':p['jv_property_memorandum'],
            'development_cost_to_come_memorandum':development['cost_to_come']}
    return results


def generate_reported_property(bundle, *, output_dir, metadata):
    bound = bind_inputs(bundle, REPORTED_SCHEMA, horizon='snapshot')
    results = {} if bound['issues'] else project_reported(bound['values']['model'],bound['values'],bound['problem'])
    from bellomberg.core.language import text as tr
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='property_nav',
        valuation_basis=tr('NAV NTA riportato ricostruito alla data di bilancio, con sensibilita esplicite sui valori patrimoniali. '
            'Nessuna previsione di canoni, FFO/AFFO o compliance futura; eventi successivi esclusi dalla fotografia.',
            'Reported NTA reconstructed at the balance-sheet date with explicit asset-value sensitivities. '
            'No rent, FFO/AFFO or future compliance forecast; subsequent events excluded from the snapshot.'))
