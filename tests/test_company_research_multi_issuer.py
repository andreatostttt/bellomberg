"""Generic issuer paths: fictional entities, frozen transport, real ingestion."""
from copy import deepcopy
from io import BytesIO
import json
from threading import RLock
from types import SimpleNamespace

import pytest

from bellomberg.agents.company_research_tools import dispatch_company_source
from test_company_source_research import FrozenTransport, html, session


@pytest.mark.parametrize('ticker,name,currency,host,kind', [
    ('FROZEN-A', 'Frozen Alpha Corporation', 'USD', 'alpha.example.org', 'html'),
    ('FROZEN.MI', 'Frozen Beta Societa', 'EUR', 'beta.example.org', 'pdf'),
    ('FROZEN.L', 'Frozen Gamma plc', 'GBP', 'gamma.example.org', 'html'),
])
def test_distinct_issuer_websites_native_discovery_acquisition_and_resume(
        tmp_path, ticker, name, currency, host, kind):
    site = 'https://' + host
    report = '/investors/results.' + kind
    content = (name + '\nPublished on 2026-09-09\nYear ended 2025-12-31\n'
               'Financial statements. Fictional offline test document, not investment data.')
    if kind == 'pdf':
        from reportlab.pdfgen.canvas import Canvas
        stream = BytesIO()
        canvas = Canvas(stream)
        text = canvas.beginText(40, 800)
        for line in content.splitlines():
            text.textLine(line)
        canvas.drawText(text)
        canvas.save()
        raw, mime = stream.getvalue(), 'application/pdf'
    else:
        raw, mime = html(content), 'text/html'
    transport = FrozenTransport({
        '/': (('<html><body><a href="' + report + '">Annual financial statements</a>'
               '</body></html>').encode(), 'text/html'), report: (raw, mime)})
    identity = {'status': 'confirmed', 'ticker': ticker, 'name': name,
                'exchange': 'TEST', 'currency': currency}
    options = dict(ticker=ticker, identity=identity, issuer_website=site)
    research = session(tmp_path, transport, **options)
    acknowledged = []
    board = SimpleNamespace(current_round=1, valuation_results={}, _source_research_lock=RLock(),
        company_source_session=lambda selected: research if selected == ticker else None,
        on_company_sources_changed=lambda active, snapshot: acknowledged.append(deepcopy(snapshot)))
    found = dispatch_company_source(board, 'search_company_sources', {'ticker': ticker}, max_chars=12000)
    assert found['financial_evidence'] is False and found['links'][0]['url'] == site + report
    acquired = dispatch_company_source(board, 'acquire_company_source',
        {'ticker': ticker, 'source': {'url': found['links'][0]['url']}}, max_chars=12000)
    assert acquired['ok'] is True, acquired
    assert acknowledged[-1]['ticker'] == ticker
    document = acknowledged[-1]['documents'][0]
    assert name in document['text'] and document['url'] == site + report
    assert document['published_at'] == '2026-09-09'
    for _ in range(2):
        restored = session(tmp_path, transport, **options)
        snapshot = restored.snapshot(expected_revision_id=acquired['revision_id'])
        assert snapshot['documents'] == acknowledged[-1]['documents']
    assert len(transport.requests) == 2
    (tmp_path / 'issuer-proof.json').write_text(json.dumps({
        'fixture': 'fictional issuer, offline only', 'ticker': ticker, 'source_url': document['url'],
        'revision_id': acquired['revision_id'], 'get_count': len(transport.requests),
        'format': kind, 'resume_count': 2}), encoding='utf-8')
