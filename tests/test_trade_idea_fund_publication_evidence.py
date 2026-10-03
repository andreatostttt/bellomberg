"""Primary publisher tool receipts remain labelled receipts, never raw HTML."""
from copy import deepcopy
from hashlib import sha256
import json
import pytest
from test_trade_idea_fund_quote_evidence import sources, ENTITY, CLASS, ON, DAY
from bellomberg.valuation import fund_nav_publication_evidence as evidence

URL = 'https://issuer.example.org/materials/'
STAMP = '2026-09-28T09:55:00+00:00'

def receipt_value():
    return {'contract': 'publisher_catalog_tool_receipt/1', 'source_url': URL,
        'tool_name': 'web.run', 'observed_at': STAMP, 'raw_primary_page_downloaded': False,
        'catalog_reference': 'turn1view0', 'link_id': 2,
        'catalog_tool_result': 'Materials ('+URL+')\n\ue200cite\ue202turn1view0\ue201\n'
            'L12:   * August 12, 2026 Semiannual Financial Statements 2026 '
            '\ue200cite\ue2022\u2020PDF\u2020issuer.example.org\ue201 L13: Other',
        'document_tool_result': ' ('+sources()[0]['url']+')\nContent type: application/pdf; '
            'Source: click({"ref_id":"turn1view0","id":2});\nL0@P0: '+ENTITY+'\n'}

def original(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    digest = sha256(raw.encode()).hexdigest()
    return {'id': digest, 'document_sha256': digest, 'url': URL, 'text': raw, 'sha256': digest,
        'published_at': None, 'availability_basis': 'observed_download', 'available_at': DAY,
        'retrieval': {'url': URL, 'document_sha256': digest, 'retrieved_at': STAMP}}

def normalize(value=None, statement=None, **kw):
    return evidence.normalize_fund_publication(original(value or receipt_value()), statement or sources()[0],
        entity=ENTITY, share_class=CLASS, on=ON, as_of=kw.get('as_of', DAY))

def test_publication_calendar_and_net_class_basis_are_separately_proved():
    document = normalize()
    body = json.loads(document['text'])
    assert body['publication'] == {'publisher': ENTITY, 'publication_date': '2026-08-12',
        'valuation_date': ON, 'basis': 'common_equity_net', 'share_class': CLASS}
    assert body['publication_date_basis'] == 'publisher_catalog_calendar_date'
    assert body['raw_primary_page_downloaded'] is False
    assert document['published_at'] is None
    assert document['availability_basis'] == 'observed_download'
    assert body['statement_source_document_id'] == sources()[0]['id']
    assert 'not original HTML' in body['limitation']

def test_catalog_inline_neighbor_rows_cannot_supply_the_selected_link_date():
    value = receipt_value()
    neighbor = ('L10:   * September 15, 2026 August 2026 Fact Sheet '
                '\ue200cite\ue2020\u2020PDF\u2020issuer.example.org\ue201 ')
    value['catalog_tool_result'] = value['catalog_tool_result'].replace('L12:', neighbor+'L12:')
    document = normalize(value)
    assert json.loads(document['text'])['publication']['publication_date'] == '2026-08-12'

@pytest.mark.parametrize('fault', ['raw_claim', 'url', 'future', 'link', 'click_reference',
    'pdf_url', 'issuer', 'year', 'ambiguous', 'receipt_time', 'contract'])
def test_unbound_dates_pages_clicks_entities_or_raw_claims_are_blocked(fault):
    value = deepcopy(receipt_value())
    if fault == 'raw_claim': value['raw_primary_page_downloaded'] = True
    elif fault == 'url': value['source_url'] = 'https://attacker.example.org/materials/'
    elif fault == 'future': value['catalog_tool_result'] = value['catalog_tool_result'].replace('August 12, 2026', 'October 12, 2026')
    elif fault == 'link': value['link_id'] = 3
    elif fault == 'click_reference': value['document_tool_result'] = value['document_tool_result'].replace('turn1view0', 'turn2view0')
    elif fault == 'pdf_url': value['document_tool_result'] = value['document_tool_result'].replace('statement.pdf', 'other.pdf')
    elif fault == 'issuer': value['document_tool_result'] = value['document_tool_result'].replace(ENTITY, 'Other Fund Ltd.')
    elif fault == 'year': value['catalog_tool_result'] = value['catalog_tool_result'].replace('Statements 2026', 'Statements 2025')
    elif fault == 'ambiguous': value['catalog_tool_result'] += '\n'+value['catalog_tool_result']
    elif fault == 'receipt_time': value['observed_at'] = '2026-09-27T09:55:00+00:00'
    elif fault == 'contract': value['contract'] = 'unverified_pm_assertion/1'
    with pytest.raises(ValueError): normalize(value)

def test_publication_catalog_recompiles_both_originals_and_all_linked_claims():
    from bellomberg.valuation.input_preparation import _catalog, _day
    catalog_source, statement = original(receipt_value()), sources()[0]
    document = normalize()
    complete = [catalog_source, statement, document]
    catalog, issues, _ = _catalog(complete, _day(DAY))
    assert not issues
    assert document['id'] in catalog
    for missing in (catalog_source, statement):
        _result, issues, _ = _catalog([doc for doc in complete if doc['id'] != missing['id']], _day(DAY))
        assert issues, 'missing raw PDF or complete actual tool receipt must block normalized publication'
    altered = deepcopy(document)
    body = json.loads(altered['text'])
    body['publication']['publication_date'] = '2026-08-13'
    altered['text'] = json.dumps(body, sort_keys=True)
    altered['sha256'] = sha256(altered['text'].encode()).hexdigest()
    _result, issues, _ = _catalog([catalog_source, statement, altered], _day(DAY))
    assert issues, 'resealing publication date cannot replace the original publisher calendar row'

def test_publication_proof_uses_exact_original_nav_class_and_same_statement():
    from bellomberg.valuation.input_preparation import _catalog, _day, _fact_proof
    from bellomberg.valuation.fund_nav_preparation import prove_nav
    from bellomberg.valuation.fund_nav_statement import normalize_fund_statement
    catalog_source, statement = original(receipt_value()), sources()[0]
    document = normalize()
    nav = normalize_fund_statement(statement)['documents'][0]
    catalog, issues, _ = _catalog([catalog_source, statement, document, nav], _day(DAY))
    assert not issues
    item = {'value': json.loads(document['text'])['publication'], 'evidence_ids': [document['id']],
            'evidence_pointer': {'value': '/publication'}}
    perimeter = {'entity': ENTITY, 'share_class': CLASS, 'currency': 'USD'}
    observations = json.loads(nav['text'])['facts']
    index, fact = next((index, fact) for index, fact in enumerate(observations)
                      if fact['concept'] == 'ClassNavPerShare' and fact['share_class'] == CLASS and fact['end'] == ON)
    model = {'reported_nav_per_share': {'value': fact['value'], 'quoted_value': fact['value'],
        'quoted_unit': fact['unit'], 'evidence_ids': [nav['id']],
        'evidence_pointer': {'value': f'/facts/{index}/value', 'unit': f'/facts/{index}/unit', 'period': f'/facts/{index}/end'}}}
    def check(proof=item, selected=perimeter, selected_model=model):
        return prove_nav('publication', proof, [catalog[document['id']]], 'contract', ON, selected, selected_model, _fact_proof)
    assert check() is None
    assert check(selected={**perimeter, 'share_class': 'Special Voting Share'})
    assert check(selected_model={'reported_nav_per_share': {'evidence_ids': ['fund-nav-statement-other-primary']}})
    mixed = deepcopy(item)
    mixed['evidence_quote'] = 'Additional unsupported proof must not be silently ignored'
    assert check(mixed)
    changed = deepcopy(item)
    changed['value']['publication_date'] = '2026-08-13'
    assert check(changed)
