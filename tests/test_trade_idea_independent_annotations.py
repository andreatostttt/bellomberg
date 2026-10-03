"""Independent financial annotations must reach the real committee context."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from bellomberg.agents import trade_idea


def test_candidate_context_preserves_separate_annotations_without_promoting_old_status():
    annotations = [{'scope': 'bear', 'review': {'limitation': 'Synthetic uncalibrated stress'}}]
    current = {'generation_id': 'synthetic-current', 'preparation': {},
               'analytical_quality': {'independent_review_annotations': deepcopy(annotations)}}
    board = SimpleNamespace(target_ticker='SYNTH', valuation_results={'SYNTH': current},
                            source_qualification={}, data={}, tool_receipts=[])
    with patch.object(trade_idea, '_verified_candidate_valuations', return_value=[]):
        context = trade_idea.candidate_model_context(board)
    report = context['independent_review_annotations']
    assert report['status'] == 'separate_review_annotations_not_PM_economic_approval'
    assert report['items'] == annotations
    report['items'][0]['review']['limitation'] = 'Synthetic edited copy'
    assert current['analytical_quality']['independent_review_annotations'] == annotations


def test_candidate_context_declares_annotations_missing():
    board = SimpleNamespace(target_ticker='SYNTH', valuation_results={}, source_qualification={},
                            data={}, tool_receipts=[])
    with patch.object(trade_idea, '_verified_candidate_valuations', return_value=[]):
        report = trade_idea.candidate_model_context(board)['independent_review_annotations']
    assert report['status'] == 'unavailable' and report['items'] == []
