"""Rebinding does not turn equal numbers with different source meaning into proof."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.preparation_historical_sources import _pin
from bellomberg.valuation.preparation_source_reproof import reprove_historical_plan


def normalized(ident, facts):
    text = json.dumps({'facts': facts}, sort_keys=True)
    return {'id': ident, 'url': 'https://api.fdic.gov/banks/financials?filters=fixture-' + ident,
        'published_at': None, 'available_at': '2026-09-28', 'text': text,
        'sha256': sha256(text.encode()).hexdigest(), 'document_sha256': ident,
        'metadata': {'normalizer': 'fdic_financials_v1', 'source_document_id': 'raw-' + ident,
            'entity': 'SYNTH-BANK', 'scope': 'bank_institution', 'cert': 123, 'rssd': 456,
            'parent_rssd': 789, 'report_date': '2026-06-30', 'reported_top_holder': 'SYNTH-PARENT',
            'cet1_reporting_flags': {'INSFDIC': 1, 'IBA': 0, 'CBLRIND': 0}}}


def fact(concept='EQ', value=100):
    return {'taxonomy': 'fdic-ris', 'concept': concept, 'value': value, 'unit': 'USD thousand',
        'end': '2026-06-30', 'entity': 'SYNTH-BANK', 'scope': 'bank_institution'}


def plan():
    return {'model': {'opening': {'kind': 'historical', 'value': .1, 'evidence_ids': ['old'],
        'evidence_pointer': {'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'},
        'quoted_value': 100, 'quoted_unit': 'USD thousand', 'rationale': 'Preserve this input'}}, 'scenarios': {}}


def catalog(document):
    primary_id = document['metadata']['source_document_id']
    return {document['id']: document, primary_id: {'id': primary_id, 'url': document['url'],
        'text': 'Synthetic complete original FDIC response', 'metadata': None}}


def test_new_primary_hash_and_reordered_facts_require_explicit_semantic_rebinding():
    old = normalized('old', [fact(), fact('RBCT1C', 90)])
    new = normalized('new', [fact('RBCT1C', 90), fact()])
    original = plan()
    rebuilt, receipt = reprove_historical_plan(original, catalog(old), catalog(new), source_pin=_pin)
    assert original == plan()
    item = rebuilt['model']['opening']
    assert item['value'] == .1 and item['quoted_value'] == 100
    assert item['evidence_ids'] == ['new']
    assert item['evidence_pointer'] == {'value': '/facts/1/value', 'unit': '/facts/1/unit', 'period': '/facts/1/end'}
    assert receipt[0]['old_document_sha256'] != receipt[0]['new_document_sha256']
    assert receipt[0]['fact_rebindings'] == [{'old_index': 0, 'new_index': 1}, {'old_index': 1, 'new_index': 0}]


@pytest.mark.parametrize('fault', ['concept', 'unit', 'entity', 'scope', 'end', 'start', 'value',
    'duplicate', 'conflicting_duplicate', 'parent', 'capital_regime'])
def test_wrong_measure_scope_duration_amount_or_ambiguous_fact_remains_a_gap(fault):
    old = normalized('old', [fact()])
    changed = fact()
    if fault in ('concept', 'unit', 'entity', 'scope', 'end', 'start', 'value'):
        changed[fault] = {'concept': 'LIAB', 'unit': 'USD million', 'entity': 'OTHER-BANK',
            'scope': 'parent_only', 'end': '2026-03-31', 'start': '2026-01-01', 'value': 101}[fault]
    facts = [changed]
    if fault == 'duplicate': facts.append(deepcopy(changed))
    elif fault == 'conflicting_duplicate': facts.append(fact(value=101))
    new = normalized('new', facts)
    if fault == 'parent': new['metadata']['parent_rssd'] = 999
    elif fault == 'capital_regime': new['metadata']['cet1_reporting_flags']['CBLRIND'] = 1
    with pytest.raises(ValueError):
        reprove_historical_plan(plan(), catalog(old), catalog(new), source_pin=_pin)


def test_raw_primary_rebinding_requires_whole_identical_text_and_same_filing_identity():
    text = 'Synthetic full primary statement with an explicit issuer/date and unchanged disclosures.'
    old = {'id': 'old', 'url': 'https://www.sec.gov/Archives/edgar/data/123/456/statement.htm',
        'published_at': '2026-08-20', 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
        'document_sha256': 'a'*64, 'metadata': {'emittente_id': 'CIK:0000000123',
            'issuer': 'SYNTH-PARENT', 'form': '10-K', 'report_date': '2026-06-30', 'accession': '456'}}
    new = deepcopy(old)
    new.update(id='new', document_sha256='b'*64)
    rebuilt, receipt = reprove_historical_plan(plan(), {'old': old}, {'new': new}, source_pin=_pin)
    assert rebuilt['model']['opening']['evidence_ids'] == ['new']
    assert receipt[0]['basis'] == 'exact_full_primary_text_and_filing_identity'
    for change in ('text', 'issuer', 'publication', 'accession'):
        altered = deepcopy(new)
        if change == 'text':
            altered['text'] += ' Additional claim.'
            altered['sha256'] = sha256(altered['text'].encode()).hexdigest()
        elif change == 'publication': altered['published_at'] = '2026-08-21'
        else: altered['metadata'][change] = 'changed'
        with pytest.raises(ValueError):
            reprove_historical_plan(plan(), {'old': old}, {'new': altered}, source_pin=_pin)
