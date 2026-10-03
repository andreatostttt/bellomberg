"""Family preparations reach their actual engines from explicit synthetic sources.

These are software and arithmetic proofs. They are deliberately synthetic and
do not claim the economic qualification of any live issuer or forecasts.
"""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.input_preparation import prepare_method_inputs
from bellomberg.valuation.preparation_methods import method_schema, SNAPSHOT_METHODS, FINITE_METHODS
from bellomberg.valuation.preparation_record_evidence import record_period
from bellomberg.valuation.sector_analysis import prepare_sector_analysis
from bellomberg.valuation.dcf_engine import generate_valuation


def family_factories():
    from test_sector_rab_drivers import rab_bundle
    from test_sector_bank_capital import bank_bundle
    from test_sector_nav_drivers import nav_bundle
    from test_insurance_valuation import insurance_bundle
    from test_insurance_life import life_bundle
    from test_real_estate_valuation import property_bundle
    from test_property_development import developer_bundle
    from test_resources_valuation import resource_bundle
    from test_development_valuation import development_bundle
    from test_sotp_documented import sotp_bundle
    from test_managed_care_integration import make_bundle
    return [bank_bundle, rab_bundle, lambda: nav_bundle(profile='cef'), lambda: nav_bundle(profile='dat'),
            insurance_bundle, life_bundle, property_bundle, developer_bundle, resource_bundle,
            development_bundle, sotp_bundle, lambda: make_bundle(kind='FY', count=10)]


def sourced_family(factory):
    from datetime import date
    from calendar import isleap
    original = factory()
    method = original['decision']['method_id']
    plan = {'model': {}, 'scenarios': {scenario: {} for scenario in ('bear', 'base', 'bull')},
            'scenario_rationale': {scenario: 'Explicit synthetic family arithmetic case, not live issuer qualification'
                                   for scenario in ('bear', 'base', 'bull')}}
    for row in original['case']['records']:
        scope = plan['model'] if row['scenario'] == 'model' else plan['scenarios'][row['scenario']]
        scope[row['driver']] = {'value': deepcopy(row['value'])}
    calendar = plan['model']['calendar']['value']
    prior_count = len(calendar.get('periods') or [])
    if method not in SNAPSHOT_METHODS | FINITE_METHODS | {'managed_care_distributable_equity'}:
        opening = date.fromisoformat(calendar['valuation_date'])
        first_year = opening.year + 1
        calendar['periods'] = [{'start': f'{year}-01-01', 'end': f'{year}-12-31'}
                               for year in range(first_year, first_year+10)]
        def extend(value):
            if isinstance(value, dict):
                return {key: extend(child) for key, child in value.items()}
            if isinstance(value, list) and len(value) == prior_count and all(type(v) in (int, float) for v in value):
                return value + [value[-1]] * (10-prior_count)
            if isinstance(value, list):
                return [extend(child) for child in value]
            return deepcopy(value)
        for drivers in plan['scenarios'].values():
            for name, item in drivers.items():
                if name not in ('terminal_ledger', 'continuing_economics'):
                    item['value'] = extend(item['value'])
                if name == 'capital.discount_periods':
                    item['value'] = list(range(1, 11))
        if method == 'regulated_rab':
            plan['model']['regime']['value']['review_end'] = f'{first_year+10}-12-31'
        if method == 'bank_residual_income':
            # Reconcile the deliberately extended synthetic bank book and cash
            # into its continuing ledger; repeating a two-year TV is invalid.
            from bellomberg.valuation.capital_inputs import assemble_capital
            from bellomberg.valuation.distributable_equity import project_distributable_equity
            from bellomberg.valuation.bank_adapter import EARNINGS
            legal_ids = [sub['id'] for sub in plan['model']['legal_structure']['value']['subsidiaries']]
            for drivers in plan['scenarios'].values():
                values = {key:item['value'] for key,item in drivers.items()}
                capital = assemble_capital(values, legal_ids)
                income = [sum(values[key][i]*sign for key,sign in EARNINGS.items()) for i in range(10)]
                ledger = project_distributable_equity(capital, income, list(range(first_year,first_year+10)))
                book = plan['model']['opening_common_equity']['value'] + sum(income) - sum(row['shareholder_net_distribution'] for row in ledger['rows'])
                tv = book * drivers['terminal_roe']['value'] / capital['ke']
                drivers['capital.terminal_equity']['value'] = tv
                tail = drivers['terminal_ledger']['value']
                terminal_income = book * drivers['terminal_roe']['value']
                parent_income = tail['capital']['parent_gaap_net_income'][0]
                for index, sub in enumerate(tail['capital']['subsidiaries']):
                    original_sub = capital['subsidiaries'][index]
                    gaap = original_sub['opening_gaap_equity'] + sum(original_sub['gaap_net_income']) - sum(original_sub['proposed_distribution']) + sum(original_sub['proposed_contribution'])
                    statutory = ledger['rows'][-1]['subsidiaries'][index]['closing_statutory_capital']
                    ni = terminal_income-parent_income
                    sub.update(opening_gaap_equity=gaap, opening_statutory_capital=statutory,
                               opening_gaap_to_statutory_equity=statutory-gaap,
                               gaap_net_income=[ni], proposed_distribution=[ni], permitted_distribution=[ni],
                               liquidity_before_transfers=[tail['liquidity_bridge'][sub['id']]['opening_cash']+ni])
                    tail['liquidity_bridge'][sub['id']]['operating_cash'] = [ni+3+2]
    schema, entities = method_schema(method, plan)
    perimeter = plan['model']['perimeter']['value']
    group = perimeter.get('entity', perimeter.get('consolidated_entity'))
    cutoff = original['case']['as_of']
    span = '|'.join(period['start']+'/'+period['end'] for period in calendar.get('periods', [])) or calendar['valuation_date']
    observations = []
    for scope, drivers in [('model', plan['model']), *plan['scenarios'].items()]:
        for name, item in drivers.items():
            field, unit, basis, timing, shape, owner = schema[name]
            if unit in ('money', 'price', 'money_per_policy'):
                unit = perimeter['currency'] + {'money': ' million', 'price': ' per share', 'money_per_policy': ' per policy'}[unit]
            historic = timing in ('opening', 'actuals') and name != 'capital.ke'
            if method in ('fund_nav', 'digital_asset_nav') and name in ('perimeter','calendar','policy','nav_target','target_basis'):
                historic = False
            if method == 'property_nav' and name in ('perimeter','calendar','policy','forward_year'):
                historic = False
            item.update(kind='historical' if historic else 'analyst_estimate', evidence_ids=['synthetic-primary'],
                rationale='Explicit synthetic reported observation or documented future judgment',
                valid_until=cutoff, valid_until_basis={'policy':'same_day','as_of':cutoff})
            if historic:
                observations.append({'field':field, 'driver':name, 'value':deepcopy(item['value']),
                    'entity':entities.get(name, group), 'period':record_period(timing,calendar,span,method=method),
                    'unit':unit, 'accounting_basis':basis})
                item['record_pointer'] = '/observations/' + str(len(observations)-1)
    text = json.dumps({'synthetic':True, 'observations':observations}, sort_keys=True, separators=(',',':'))
    documents = [{'id':'synthetic-primary','url':'https://example.org/synthetic/primary-statement',
        'published_at':cutoff,'text':text,'sha256':sha256(text.encode()).hexdigest()}]
    if method in ('bank_residual_income', 'insurance_pc_distributable_equity', 'insurance_life_distributable_equity'):
        cash_lines = []
        for scenario in plan['scenarios'].values():
            item = scenario['liquidity_bridge']
            item['facts'] = {}
            item['evidence_ids'].append('synthetic-cash')
            for entity, bridge in item['value'].items():
                quote = f"{entity} cash: {perimeter['currency']} {bridge['opening_cash']:g} million"
                period_quote = quote + ' as of ' + calendar['valuation_date']
                if period_quote not in cash_lines:
                    cash_lines.append(period_quote)
                item['facts'][entity] = {'evidence_ids':['synthetic-cash'], 'evidence_quote':quote,
                    'period_quote':period_quote, 'quoted_value':bridge['opening_cash'],
                    'quoted_unit':perimeter['currency']+' million'}
        cash_text = '\n'.join(cash_lines)
        documents.append({'id':'synthetic-cash','url':'https://example.org/synthetic/cash-statement',
            'published_at':cutoff, 'text':cash_text, 'sha256':sha256(cash_text.encode()).hexdigest()})
    profile = deepcopy(original['case']['sources']['profile'])
    bundle = prepare_sector_analysis(original['case']['ticker'], as_of=cutoff,
                                    providers={'profile':lambda *_a,**_k:deepcopy(profile)})
    return bundle, plan, documents


@pytest.mark.parametrize('factory', family_factories())
def test_sourced_family_preparation_reaches_actual_engine(tmp_path, factory):
    bundle, plan, documents = sourced_family(factory)
    from bellomberg.valuation.preparation_family_stages import FamilyStagedProposer
    from bellomberg.valuation.preparation_ai import response_format
    calls = []
    def staged(dossier, contract):
        stage = contract['preparation_stage']
        calls.append((stage['scope'], stage['drivers']))
        wire = response_format(contract)
        assert wire['json_schema']['name'] == 'family_preparation_stage'
        scope = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(scope[name]) for name in stage['drivers']},
                'rationale': 'Synthetic scoped family stage with complete visible historical proofs'}
    result = prepare_method_inputs(bundle, documents=documents,
        propose=FamilyStagedProposer(staged, schema_seed=plan))
    assert result['status'] == 'prepared', (bundle['decision']['method_id'], result['issues'])
    assert len(calls) >= 4
    payload = generate_valuation(bundle['case']['ticker'], prepared_bundle=result['bundle'], output_dir=str(tmp_path))
    assert payload['valuation_usability']['usable'], payload.get('error')
    assert payload['input_consumption']['status'] == 'complete'
    assert payload['workbook_sha256'] and payload['path'].endswith('.xlsx')
    from bellomberg.valuation.trade_idea_model import model_exhibits
    exhibits = model_exhibits(payload)
    assert exhibits['status'] == 'complete', (payload['method'], exhibits['reasons'])
    assert len(exhibits['exhibits'][0]['rows']) == 3
    assert exhibits['input_bindings']


def test_structured_historical_claim_cannot_be_fabricated_from_real_source_id(tmp_path):
    factory = family_factories()[3]  # digital-asset NAV, finite claims and dilution
    bundle, plan, documents = sourced_family(factory)
    plan['model']['components']['value']['cash'] += 12345
    result = prepare_method_inputs(bundle, documents=documents, propose=lambda *_:plan)
    assert result['status'] == 'incomplete'
    assert any(issue['field'] == 'components' and issue['code'] == 'unverified_fact' for issue in result['issues'])
