"""Closed opt-in contract for reported property NAV, distinct from stabilized NOI."""

REPORTED_POLICY = {
    'contract': 'reported_property_nav_snapshot', 'version': '1',
    'basis': 'EPRA_NTA',
    'jv_basis': 'equity_method_carrying_no_property_double_count',
    'development_basis': 'reported_carrying_no_future_profit',
    'claims_basis': 'reported_diluted_NAV_and_entitled_shares',
    'sensitivity_basis': 'property_value_shocks_not_reported_EPRA_metrics',
}

REPORTED_SCHEMA = {
    'perimeter': ('valuation_perimeter', 'contract', 'scope', 'opening', 'contract', 'model'),
    'calendar': ('valuation_perimeter', 'contract', 'calendar', 'opening', 'contract', 'model'),
    'quotation': ('quotation_units', 'contract', 'quotation', 'opening', 'contract', 'model'),
    'shares': ('diluted_shares', 'million shares', 'valuation', 'opening', 'number', 'model'),
    'policy': ('property_nav', 'contract', 'scope', 'future', 'contract', 'model'),
    'balance_sheet': ('property_nav', 'contract', 'IFRS', 'opening', 'contract', 'model'),
    'property_reconciliation': ('property_nav', 'contract', 'IFRS', 'opening', 'contract', 'model'),
    'epra_bridge': ('property_nav', 'contract', 'EPRA_NTA', 'opening', 'contract', 'model'),
    'claims': ('property_claims', 'contract', 'IFRS', 'opening', 'contract', 'model'),
    'restrictions': ('property_restrictions', 'contract', 'disclosed', 'opening', 'contract', 'model'),
    'development': ('property_development', 'contract', 'disclosed', 'opening', 'contract', 'model'),
    'asset_shocks': ('property_sensitivity', 'contract', 'valuation', 'future', 'contract', 'scenario'),
    'nav_target': ('valuation_target', 'ratio', 'valuation', 'future', 'number', 'scenario'),
    'target_basis': ('valuation_target', 'text', 'valuation', 'future', 'text', 'scenario'),
}

REPORTED_FIELDS = (
    ('valuation_perimeter', 'Dated consolidated property NAV perimeter and accounting scope.'),
    ('diluted_shares', 'Reported entitled and diluted share denominator, separately reconciled.'),
    ('quotation_units', 'Same-date reported quotation, units and explicit currency conversion.'),
    ('valuation_target', 'Explicit scenario equity NAV target and sensitivity basis; no default parity.'),
    ('property_nav', 'Complete reported assets/liabilities, property/JV and IFRS to EPRA NTA reconciliations.'),
    ('property_claims', 'Dilution, minorities, hybrids and all balance-sheet claims counted once.'),
    ('property_restrictions', 'Reported covenants, guarantees and encumbrance restrictions; no automatic future compliance.'),
    ('property_development', 'Reported development carrying amount, committed cost to come and funding disclosures.'),
    ('property_sensitivity', 'Explicit analyst asset/JV and development-cost sensitivities distinct from EPRA metrics.'),
)


def reported_policy_selected(records):
    """An exact unique model record is the only selector; malformed variants fail legacy."""
    if not isinstance(records, (list, tuple)):
        return False
    rows = [r for r in records if isinstance(r, dict) and r.get('driver') == 'policy'
            and r.get('scenario') == 'model']
    return len(rows) == 1 and rows[0].get('value') == REPORTED_POLICY
