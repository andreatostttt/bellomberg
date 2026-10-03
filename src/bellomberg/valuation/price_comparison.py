"""Read historical price-distance flags under the current informational policy.

Only the exact former comparison output is recognized. This is a view: saved
files, economic values, source evidence and unrelated exclusions are untouched.
"""
from copy import deepcopy
from math import isfinite


def _old_comparison(check, fair_value, price):
    if not isinstance(check, dict) or check.get('error') or check.get('issues'):
        return False
    try:
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and isfinite(v) and v > 0 for v in (fair_value, price)):
            return False
        ratio = fair_value / price
        distance = abs(ratio - 1.)
        if not isfinite(ratio) or not isfinite((ratio - 1.) * 100):
            return False
        # Historical recognition only: these thresholds never judge new models.
        severity = 'BLOCK' if distance > .5 else 'WARN' if distance > .4 else 'OK'
        if severity == 'OK':
            return False
        expected = {'status': 'ok', 'ratio': round(ratio, 2),
                    'upside_pct': round((ratio - 1.) * 100, 1),
                    'severity': severity, 'exclude_from_action_table': severity == 'BLOCK'}
        for key, value in expected.items():
            actual = check.get(key)
            if isinstance(value, bool):
                if type(actual) is not bool or actual != value:
                    return False
            elif isinstance(value, (int, float)):
                if isinstance(actual, bool) or not isinstance(actual, (int, float)) or actual != value:
                    return False
            elif actual != value:
                return False
        if severity == 'BLOCK':
            templates = (
                'VAL SOSPETTA: fair value %.0f diverge %.0f%% dal prezzo %.0f (ratio %.2f). '
                'Rivedere growth/margini/WACC/shares/net_debt PRIMA di fidarsi. ESCLUSO dalla ACTION TABLE.',
                'SUSPECT VALUATION: fair value %.0f diverges %.0f%% from price %.0f (ratio %.2f). '
                'Review growth/margins/WACC/shares/net_debt BEFORE relying on it. EXCLUDED from ACTION TABLE.')
        else:
            templates = (
                'VAL da verificare: fair value %.0f diverge %.0f%% dal prezzo %.0f (ratio %.2f). Trattare con cautela.',
                'VALUATION needs verification: fair value %.0f diverges %.0f%% from price %.0f (ratio %.2f). Use with caution.')
        return check.get('headline') in {t % (fair_value, distance * 100, price, ratio) for t in templates}
    except (OverflowError, TypeError, ValueError):
        return False


def refresh_price_comparison(payload):
    """Return a copy with only provable historical distance flags reinterpreted.

    Missing/hidden fair values cannot be reconstructed here. Full documentary,
    source, quotation and calculator checks still run at the consumer boundary.
    """
    result = deepcopy(payload) if isinstance(payload, dict) else {}
    sanity = result.get('sanity')
    if not isinstance(sanity, dict) or result.get('error'):
        return result
    from .dcf_engine import sanity_check

    price = result.get('price')
    changed = []
    checks = sanity.get('scenario_checks')
    headline_value = next((result.get(key) for key in ('fair_value_final', 'fair_value_weighted',
                  'fair_value_blend', 'fair_value_base') if result.get(key) is not None), None)
    recognized = {id(sanity)} if _old_comparison(sanity, headline_value, price) else set()
    if isinstance(checks, dict):
        recognized.update(id(checks[s]) for s in ('bear', 'base', 'bull')
                          if _old_comparison(checks.get(s), result.get('fair_value_' + s), price))

    def other_failure(node):
        if isinstance(node, list):
            return any(other_failure(child) for child in node)
        if not isinstance(node, dict):
            return False
        if (node.get('error') or node.get('issues') or node.get('valuation_flagged') is True
                or node.get('flagged') is True or node.get('usable') is False
                or node.get('status') in ('BLOCK', 'KO', 'error', 'blocked', 'INCOMPLETA', 'incomplete', 'n/d')):
            return True
        if id(node) not in recognized and (node.get('exclude_from_action_table') is True
                                           or node.get('severity') == 'BLOCK'):
            return True
        return any(other_failure(child) for child in node.values())

    # A distance flag never authorizes clearing another failure, even when it
    # appears inside sanity rather than at the payload root.
    if other_failure(sanity):
        return result
    if isinstance(checks, dict):
        for scenario in ('bear', 'base', 'bull'):
            check = checks.get(scenario)
            scenario_value = result.get('fair_value_' + scenario)
            if _old_comparison(check, scenario_value, price):
                check.update(sanity_check(scenario_value, price))
                changed.append(scenario)
    if _old_comparison(sanity, headline_value, price):
        old_headline = sanity['headline']
        sanity.update(sanity_check(headline_value, price))
        changed.append('overall')
        # The legacy writer set these together. An unrelated root exclusion or
        # technical error remains authoritative and must not be cleared.
        if (result.get('sanity_headline') == old_headline and not result.get('error')
                and not result.get('exclude_from_action_table')):
            if result.get('valuation_flagged') is True:
                result.pop('valuation_flagged')
            result['sanity_headline'] = None
    if changed:
        result['price_comparison_policy'] = 'informational_distance_v1'
        result['price_comparison_reclassified'] = changed
    return result
