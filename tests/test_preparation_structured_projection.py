"""Economic facts survive prompt compaction; unavailable proofs cannot be cited."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.preparation_ai import verify_visible_citations
from bellomberg.valuation.preparation_view import compact_structured_evidence, select_stage_view


def document(ident, body, metadata=None):
    text = json.dumps(body) if isinstance(body, dict) else body
    return {'id': ident, 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'metadata': metadata or {}}


def sample():
    facts = [{'taxonomy': 'reported-statement', 'concept': 'Revenue', 'label': 'Net sales',
              'value': value, 'unit': 'USD thousand', 'end': date, 'start': date[:4] + '-01-01',
              'entity': 'Synthetic', 'scope': 'consolidated', 'section': None,
              'proof': {'source_document_id': 'primary', 'cell_text': str(value), 'row_index': i}}
             for i, (value, date) in enumerate([(120, '2025-12-31'), (54, '2026-06-30')])]
    return {'ticker': 'SYNTH', 'method_id': 'operating_fcff', 'documents': [
        document('primary', 'Primary report'),
        document('statement-tables-primary', {'issuer': 'Synthetic', 'facts': facts},
                 {'normalizer': 'statement_tables_v1', 'source_document_id': 'primary'}),
        document('xbrl-0000000123-accession', {'cik': 123, 'facts': [
            {'taxonomy': 'us-gaap', 'concept': 'Assets', 'label': 'Assets', 'unit': 'USD',
             'observation': {'val': 80, 'end': '2025-12-31', 'accn': 'accession', 'form': '20-F', 'fy': 2025}}]},
                 {'emittente_id': 'CIK:0000000123', 'accession': 'accession'}),
        document('balance-sheet-primary', {'groups': [{'value': 20, 'proof': {'row_index': 9}}],
                                           'components': [{'value': 7, 'label': 'cash', 'proof': {'row_index': 8}}]},
                 {'normalizer': 'balance_sheet_v1', 'source_document_id': 'primary'}),
        document('unknown', {'facts': facts}),
    ]}


def test_projection_preserves_facts_dates_units_indices_and_declares_shared_metadata():
    source = sample()
    original = deepcopy(source)
    incoming = select_stage_view(source, 'model')
    before = deepcopy(incoming)
    view = compact_structured_evidence(source, incoming)
    assert source == original and incoming == before
    body = json.loads(view['documents'][1]['text'])
    assert [f['value'] for f in body['facts']] == [120, 54]
    for i, fact in enumerate(body['facts']):
        for key in ('concept', 'label', 'value', 'unit', 'start', 'end'):
            assert fact[key] == json.loads(source['documents'][1]['text'])['facts'][i][key]
        assert 'proof' not in fact and 'taxonomy' not in fact
    receipt = view['stage_view']['documents'][1]['structured_projection']
    assert receipt['shared_fact_metadata'] == {'taxonomy': 'reported-statement', 'entity': 'Synthetic',
                                              'scope': 'consolidated', 'section': None}
    assert receipt['retained_facts'] == 2 and receipt['omitted_facts'] == 0
    assert view['documents'][1]['sha256'] == source['documents'][1]['sha256']
    xbrl = json.loads(view['documents'][2]['text'])['facts'][0]
    assert xbrl['label'] == 'Assets' and xbrl['observation'] == {'val': 80, 'end': '2025-12-31'}
    assert view['documents'][4] == source['documents'][4]
    assert json.loads(view['documents'][3]['text'])['components'] == [{'value': 7, 'label': 'cash'}]
    assert compact_structured_evidence(source, view) == view  # No second projection of changed text.


def test_projected_pointers_are_visible_but_omitted_proofs_and_whole_records_are_not():
    source = sample()
    view = compact_structured_evidence(source, select_stage_view(source, 'model'))
    for pointer in ('/facts/1/value', '/facts/1/unit', '/facts/1/end'):
        verify_visible_citations({'evidence_ids': ['statement-tables-primary'],
                                 'evidence_pointer': {'value': pointer}}, view, source)
    for pointer in ('/facts/1/proof/row_index', '/facts/1/taxonomy'):
        with pytest.raises(ValueError, match='pointer'):
            verify_visible_citations({'evidence_ids': ['statement-tables-primary'],
                                     'evidence_pointer': {'value': pointer}}, view, source)
    with pytest.raises(ValueError, match='record_pointer'):
        verify_visible_citations({'evidence_ids': ['statement-tables-primary'],
                                 'record_pointer': '/facts/1'}, view, source)


def test_nonuniform_metadata_remains_and_unbound_documents_are_not_compacted():
    source = sample()
    body = json.loads(source['documents'][1]['text'])
    body['facts'][1]['scope'] = 'parent'
    source['documents'][1] = document('statement-tables-primary', body, source['documents'][1]['metadata'])
    view = compact_structured_evidence(source, select_stage_view(source, 'model'))
    assert [f['scope'] for f in json.loads(view['documents'][1]['text'])['facts']] == ['consolidated', 'parent']
    source['documents'][1]['metadata']['source_document_id'] = 'missing'
    view = compact_structured_evidence(source, select_stage_view(source, 'model'))
    assert view['documents'][1] == source['documents'][1]


def test_corrupt_source_hash_fails_closed():
    source = sample()
    view = select_stage_view(source, 'model')
    source['documents'][1]['text'] += ' '
    with pytest.raises(ValueError, match='SHA-256'):
        compact_structured_evidence(source, view)


@pytest.mark.parametrize('disclosure', [
    {'unit_exception': 'Shares are counts, not the monetary scale used by this table.'},
    {'section_subtotal': {'reported_dashes': ['Derivative financial instruments'], 'terms': [2, 3]}},
    {'unrecognized_disclosure': {'economic_meaning': 'Unknown fields must survive.',
                                'proof': {'row_index': 1}}},
])
def test_proof_with_economic_or_unknown_disclosure_remains_whole(disclosure):
    source = sample()
    original_doc = source['documents'][1]
    body = json.loads(original_doc['text'])
    body['facts'][0]['proof'].update(disclosure)
    source['documents'][1] = document(original_doc['id'], body, original_doc['metadata'])
    view = compact_structured_evidence(source, select_stage_view(source, 'model'))
    projected = json.loads(view['documents'][1]['text'])
    assert projected['facts'][0]['proof'] == body['facts'][0]['proof']
    assert 'proof' not in projected['facts'][1]


def test_conflicting_xbrl_filing_metadata_stays_visible():
    source = sample()
    original_doc = source['documents'][2]
    body = json.loads(original_doc['text'])
    fact = deepcopy(body['facts'][0])
    fact['observation'].update(accn='another-accession', form='6-K', filed='2026-08-01')
    body['facts'][0]['observation']['filed'] = '2026-03-01'
    body['facts'].append(fact)
    source['documents'][2] = document(original_doc['id'], body, original_doc['metadata'])
    view = compact_structured_evidence(source, select_stage_view(source, 'model'))
    observations = [f['observation'] for f in json.loads(view['documents'][2]['text'])['facts']]
    for i, observation in enumerate(observations):
        for key in ('accn', 'form', 'filed'):
            assert observation[key] == body['facts'][i]['observation'][key]
