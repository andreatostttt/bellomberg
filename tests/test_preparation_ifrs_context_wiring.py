"""New IFRS forms cannot replace paid forms or caller-owned excerpts."""
from copy import deepcopy

import pytest

from bellomberg.valuation import preparation_sections
from test_preparation_sections import _budgeted, _large_case


def alternative(source, context, contract):
    result = deepcopy(context)
    result['ifrs_test_marker'] = True
    return result


def test_new_ifrs_form_is_checked_only_after_old_paid_forms(tmp_path, monkeypatch):
    source, contract, calls = _large_case(), {'schema': {}}, []
    proposer = _budgeted(tmp_path, calls=calls, context_length=1000000)
    proposer(source, contract)
    monkeypatch.setattr(preparation_sections, 'select_ifrs_fcff_context',
                        lambda *args: pytest.fail('old paid form must win'), raising=False)
    assert proposer.prepare_context(source, contract, source_dossier=source) == source
    assert len(calls) == 1


def test_new_ifrs_form_only_when_needed_and_replay_only_when_cached(tmp_path, monkeypatch):
    source, contract = _large_case(), {'schema': {}}
    proposer = _budgeted(tmp_path)
    monkeypatch.setattr(preparation_sections, 'select_ifrs_fcff_context', alternative, raising=False)
    monkeypatch.setattr(proposer, '_prompt_bytes', lambda request:
        100 if 'ifrs_test_marker' in request['messages'][0]['content'] else 10000000)
    view = proposer.prepare_context(source, contract, source_dossier=source)
    assert view['ifrs_test_marker'] is True
    assert view['completed_plan'] == source['completed_plan']
    assert proposer.prepare_context(source, contract, source_dossier=source,
                                   allow_selection=False, allow_cached_selection=True) == source
    assert proposer.summary()['requests'] == 0
    proposer(view, contract)  # Synthetic provider only.
    proposer.metadata = lambda _: pytest.fail('cached IFRS form must replay before pricing')
    assert proposer.prepare_context(source, contract, source_dossier=source,
                                   allow_selection=False, allow_cached_selection=True) == view
    assert proposer.summary()['requests'] == 1


def test_small_old_form_and_explicit_selection_keep_priority(tmp_path, monkeypatch):
    source, contract = _large_case(), {'schema': {}}
    proposer = _budgeted(tmp_path, context_length=1000000)
    monkeypatch.setattr(preparation_sections, 'select_ifrs_fcff_context', alternative, raising=False)
    assert proposer.prepare_context(source, contract, source_dossier=source) == source
    monkeypatch.setattr(preparation_sections, 'select_ifrs_fcff_context',
                        lambda *args: pytest.fail('explicit selection cannot be changed'), raising=False)
    assert proposer.prepare_context(source, contract, source_dossier=source, allow_selection=False) == source


def test_ifrs_candidate_cannot_hide_a_record_cited_in_completed_plan(tmp_path, monkeypatch):
    from test_preparation_structured_projection import sample
    from bellomberg.valuation.preparation_view import select_stage_view
    source, contract = _large_case(), {'schema': {}}
    source['documents'].extend(sample()['documents'])
    source['completed_plan']['model']['earlier'] = {
        'evidence_ids': ['statement-tables-primary'], 'record_pointer': '/facts/0'}
    def projected(source, context, contract):
        view = select_stage_view(context, 'model')
        view['ifrs_test_marker'] = True
        return view
    proposer = _budgeted(tmp_path)
    monkeypatch.setattr(preparation_sections, 'select_ifrs_fcff_context', projected)
    monkeypatch.setattr(proposer, '_prompt_bytes', lambda request:
        100 if 'ifrs_test_marker' in request['messages'][0]['content'] else 10000000)
    with pytest.raises(ValueError, match='context'):
        proposer.prepare_context(source, contract, source_dossier=source)
    assert proposer.summary()['requests'] == 0


def test_two_filing_alternative_preserves_paid_requests_and_resume(tmp_path, monkeypatch):
    from bellomberg.valuation import sec_preparation_sections
    source, contract = _large_case(), {'schema': {}}
    proposer = _budgeted(tmp_path)
    def two_filings(source, context, contract):
        view = deepcopy(context)
        view['two_filing_test_marker'] = True
        return view
    monkeypatch.setattr(sec_preparation_sections, 'select_sec_two_filing_context', two_filings, raising=False)
    monkeypatch.setattr(proposer, '_prompt_bytes', lambda request:
        100 if 'two_filing_test_marker' in request['messages'][0]['content'] else 10000000)
    view = proposer.prepare_context(source, contract, source_dossier=source)
    assert view['two_filing_test_marker'] is True
    assert view['completed_plan'] == source['completed_plan']
    assert proposer.summary()['requests'] == 0
    assert proposer.prepare_context(source, contract, source_dossier=source,
                                   allow_selection=False, allow_cached_selection=True) == source
    proposer(view, contract)
    proposer.metadata = lambda _: pytest.fail('exact paid form must replay without pricing')
    assert proposer.prepare_context(source, contract, source_dossier=source,
                                   allow_selection=False, allow_cached_selection=True) == view


def test_old_paid_request_does_not_enter_two_filing_selector(tmp_path, monkeypatch):
    from bellomberg.valuation import sec_preparation_sections
    source, contract = _large_case(), {'schema': {}}
    proposer = _budgeted(tmp_path, context_length=1000000)
    proposer(source, contract)
    monkeypatch.setattr(sec_preparation_sections, 'select_sec_two_filing_context',
                        lambda *args: pytest.fail('original paid form must win'), raising=False)
    assert proposer.prepare_context(source, contract, source_dossier=source) == source
