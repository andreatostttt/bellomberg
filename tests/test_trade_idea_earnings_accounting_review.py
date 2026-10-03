"""Independent accounting review: consolidated and parent earnings differ."""
from copy import deepcopy
from hashlib import sha256
import json
import pytest

from bellomberg.valuation.preparation_service import prepare_and_generate
from bellomberg.valuation.trade_idea_earnings_facts import model_earnings_expectations, prove_earnings_fact
from test_trade_idea_families import family_factories, sourced_family


def source(entity, start, end, value, published, *, currency='EUR', concept='NetIncomeLoss'):
    body = {'synthetic': True, 'issuer': entity, 'facts': [
        {'taxonomy': 'us-gaap', 'concept': concept, 'unit': currency,
         'observation': {'start': start, 'end': end, 'val': value}}]}
    text = json.dumps(body, sort_keys=True)
    return {'id': 'xbrl-0000000001-000000000127000001',
            'url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json',
            'text': text, 'sha256': sha256(text.encode()).hexdigest(), 'published_at': published}


@pytest.mark.parametrize('index', [4, 5, 11])
def test_parent_attributed_income_cannot_satisfy_consolidated_income(tmp_path, index):
    bundle, plan, documents = sourced_family(family_factories()[index])
    perimeter = plan['model']['perimeter']['value']
    entity = perimeter.get('consolidated_entity', perimeter.get('entity'))
    documents.append(source(entity, '2025-01-01', '2025-12-31', 20000000., bundle['case']['as_of'], currency=perimeter['currency']))
    payload = prepare_and_generate(bundle, documents=documents, propose=lambda *_: deepcopy(plan),
                                   output_dir=str(tmp_path/'model'), source_report={'source_plan': plan})
    assert payload['valuation_usability']['usable'], payload.get('error')
    expectations = model_earnings_expectations(payload)
    assert expectations['status'] == 'ready', expectations['reasons']
    expected = next(row for row in expectations['expectations'] if row['driver'] == 'consolidated_gaap_net_income')
    # Even an equal reported amount cannot repair a different attribution. The
    # FASB taxonomy identifies NetIncomeLoss as parent-attributed and ProfitLoss
    # as including noncontrolling interests; no equality-of-values inference.
    document = source(entity, expected['period_start'], expected['period_end'],
                      expected['value']*1000000., '2027-02-01', currency=perimeter['currency'])
    submitted = {key: expected[key] for key in ('driver', 'entity', 'period', 'unit')}
    submitted.update(value=expected['value'], source=document['url'], published_at=document['published_at'])
    with pytest.raises(ValueError, match='unique'):
        prove_earnings_fact({'documents': [document]}, expected, submitted, as_of='2027-02-02')
