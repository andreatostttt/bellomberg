"""Nonhistorical limitations stay visible without masking missing opening proofs."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_trade_idea_historical_admission import history_case
from test_sector_analysis import DAY
from bellomberg.valuation.trade_idea_model import qualification


@pytest.mark.parametrize('fault', [None, 'unbound_reference', 'different_text', 'another_issue', 'non_sec_announcement'])
def test_unread_call_is_declared_but_does_not_erase_verified_opening(tmp_path, fault):
    report, providers, identity, _ = history_case(tmp_path)
    url = 'https://example.org/issuer/call.mp3'
    text = 'You may access the audio conference at: ' + url
    digest = sha256(text.encode()).hexdigest()
    primary = report['documents'][0]
    metadata = deepcopy(primary['metadata'])
    metadata['form'] = '8-K'
    doc = dict(id='call-announcement', url=primary['url'].rsplit('/', 1)[0] + '/announcement.htm',
        published_at='2026-02-01', text=text, sha256=digest, metadata=metadata)
    report['documents'].append(doc)
    issue = dict(source='SEC earnings releases', url=url,
        reason='Explicitly referenced call material not acquired')
    report['issues'] = [issue]
    report['earnings_releases'] = dict(status='incomplete', issues=[deepcopy(issue)],
        referenced_materials=[dict(url=url, status='not_acquired', source_reference=dict(
            document_id=doc['id'], text_sha256=digest, char_start=0,
            char_end_exclusive=len(text), quote=text))])
    if fault == 'unbound_reference':
        report['earnings_releases']['referenced_materials'][0]['source_reference']['document_id'] = 'absent'
    elif fault == 'different_text':
        report['earnings_releases']['referenced_materials'][0]['source_reference']['quote'] = 'fabricated'
    elif fault == 'another_issue':
        report['issues'].append(dict(source='SEC earnings releases', reason='download failed'))
    elif fault == 'non_sec_announcement':
        doc['url'] = 'https://example.org/issuer/announcement'
    q = qualification('SYNTH-EXT', identity, DAY, archive_root=tmp_path,
        providers=providers, source_report=report)
    if fault:
        assert q['status'] == 'blocked'
    else:
        assert q['status'] == 'preparation_required', q['reasons']
        assert q['source_report']['issues'] == [issue]
        limitation, = q['source_report']['historical_preparation']['source_limitations']
        assert limitation['issue'] == issue
        assert limitation['scope'] == 'unread_call_material'


@pytest.mark.parametrize('fault', [None, 'wrong_accession', 'missing_printed_source'])
def test_missing_companyfacts_requires_recompiled_printed_source_for_exact_filing(fault):
    from bellomberg.valuation.preparation_fresh_historical import _historical_source_issues
    accession = '000000000126000001'
    issue = dict(source='SEC companyfacts', reason='no tagged facts for filing ' + accession)
    report = dict(issues=[issue], selection={'selected_document_ids': ['filing']})
    catalog = {'filing': dict(id='filing', metadata={'accession': '0000000001-26-000001'}),
        'tables': dict(id='tables', metadata={'normalizer': 'statement_tables_v1',
                                            'source_document_id': 'filing'})}
    if fault == 'wrong_accession':
        catalog['filing']['metadata']['accession'] = '0000000001-26-000002'
    elif fault == 'missing_printed_source':
        catalog.pop('tables')
    blocking, limitations = _historical_source_issues(report, catalog)
    assert blocking == ([issue] if fault else [])
    assert bool(limitations) == (fault is None)
    if not fault:
        assert limitations[0]['scope'] == 'printed_statements_replace_unavailable_tags'


@pytest.mark.parametrize('concept,unit,value,accepted', [
    ('CommonStockSharesOutstanding', 'shares', 1000, True),
    ('WeightedAverageNumberOfDilutedSharesOutstanding', 'shares', 1000, False),
    ('CommonStockSharesAuthorized', 'shares', 1000, False),
    ('CommonStockSharesOutstanding', 'USD thousand', 1000, False),
    ('CommonStockSharesOutstanding', 'shares', 0, False)])
def test_printed_opening_count_uses_only_normalized_outstanding_shares(concept, unit, value, accepted):
    from bellomberg.valuation.preparation_fresh_historical import _has_opening_share_observation
    body = dict(issuer='SYNTH-GROUP', facts=[dict(taxonomy='reported-statement', concept=concept,
        entity='SYNTH-GROUP', unit=unit, value=value, end='2025-12-31')])
    text = json.dumps(body)
    doc = dict(id='tables', text=text, sha256=sha256(text.encode()).hexdigest(),
        metadata=dict(normalizer='statement_tables_v1', entity='SYNTH-GROUP'))
    assert _has_opening_share_observation({'tables': doc}, 'SYNTH-GROUP',
        '2025-12-31', DAY, ()) is accepted
