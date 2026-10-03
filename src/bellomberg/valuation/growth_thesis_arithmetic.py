"""Early engine checks on Growth's own forecasts; never rewrite its estimates."""
from .operating_adapter import operating_revenue_errors, segment_guidance_issues
from .dcf_quality import _finite


class GrowthArithmeticError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('growth thesis: revenue arithmetic: ' + str(issues))


def prove_growth_revenues(thesis, plan):
    issues = {}
    opening = plan['model']['historical_revenue']['value']
    calendar = plan['model']['calendar']['value']
    for scope, scenario in thesis['scenarios'].items():
        anchors = scenario['anchors']
        build, growth = anchors['revenue_build']['value'], anchors['revenue_growth']['value']
        if build.get('basis') == 'segment_guidance':
            errors = segment_guidance_issues(build, historical_revenue=opening,
                revenue_growth=growth, periods=calendar['periods'],
                valuation_date=calendar['valuation_date'],
                record_kind=anchors['revenue_build']['kind'],
                aggregate_kind=anchors['revenue_growth']['kind'])
            if errors:
                issues[scope] = {'errors': errors}
            continue
        errors = operating_revenue_errors(build, opening, growth)
        if errors:
            projected, targets = opening, []
            for rate in growth:
                projected *= 1 + rate
                targets.append(projected)
            issues[scope] = {'errors': errors, 'revenue_from_growth': targets}
            # Show a calculated option, not an automatic replacement. Growth
            # must explicitly adopt it or explain an unsupported reconciliation.
            if all(isinstance(build.get(key), list) and len(build[key]) == len(targets)
                   and all(_finite(x) for x in build[key]) for key in ('volume', 'utilization', 'other_revenue')):
                divisors = [v*u for v, u in zip(build['volume'], build['utilization'])]
                if all(value > 0 for value in divisors):
                    prices = [(revenue-other)/divisor for revenue, other, divisor in
                              zip(targets, build['other_revenue'], divisors)]
                    if all(_finite(value) and value >= 0 for value in prices):
                        issues[scope]['calculated_unit_price_option'] = {
                            'value': prices, 'basis': '(revenue_from_growth - other_revenue) / (volume * utilization)',
                            'limitation': 'Calculated from the prior estimates, not sourced prices or approval. '
                                          'Only the AI correction may explicitly adopt this option.'}
    if issues:
        raise GrowthArithmeticError(issues)
