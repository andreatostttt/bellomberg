"""Bounded primary-source reading; no live issuer/provider or workbook."""
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea_sources as sources
from bellomberg.agents.company_research_tools import company_source_tools
from bellomberg.market_data.lettore_trimestrali import estrai_testo
from test_company_source_research import FrozenTransport, IDENTITY, SITE, session


URL = SITE + '/report.pdf'
TEXT = 'Synthetic issuer\nPublished on 2026-09-09\nYear ended 2025-12-31\nRevenue 125.50 USD'


def pdf(*, blank=False, painted=False, pages=1):
    from reportlab.pdfgen.canvas import Canvas
    from pypdf import PdfReader, PdfWriter
    buffer = BytesIO(); canvas = Canvas(buffer)
    for number in range(pages):
        for index, line in enumerate(TEXT.splitlines()):
            canvas.drawString(40, 760 - index * 20, line)
        canvas.drawString(40, 660, f'Physical page {number + 1}')
        canvas.showPage()
    if painted:
        canvas.rect(10, 10, 80, 80, fill=1); canvas.showPage()
    canvas.save()
    if not blank: return buffer.getvalue()
    writer = PdfWriter(); writer.append(PdfReader(BytesIO(buffer.getvalue())))
    writer.add_blank_page(width=595, height=842)
    result = BytesIO(); writer.write(result); return result.getvalue()


def test_structurally_empty_page_is_measured_without_relabelling_unreadable_pages(tmp_path):
    raw = pdf(blank=True)
    transport = FrozenTransport({'/report.pdf': (raw, 'application/pdf')})
    current = session(tmp_path, transport)
    result = current.acquire({'url': URL})
    assert result['ok'], result
    doc = current.snapshot()['documents'][0]
    assert doc['extraction_coverage']['pagine'] == 2
    assert doc['extraction_coverage']['pagine_vuote_verificate'] == [2]
    assert doc['extraction_coverage']['pagine_senza_testo'] == []
    assert doc['document_sha256'] == sha256(raw).hexdigest()
    assert estrai_testo('old.pdf', contenuto=raw)['pagine_senza_testo'] == [2]


def test_pdf_page_navigation_reads_exact_window_without_claiming_complete_evidence(tmp_path):
    raw = pdf(pages=8, painted=True)
    current = session(tmp_path, FrozenTransport({'/report.pdf': (raw, 'application/pdf')}))
    view = current.open_page(URL, page=7, max_chars=40)
    assert view['page'] == 7 and view['pages'] == 9
    assert len(view['text']) <= 40 and view['financial_evidence'] is False
    tail = current.open_page(URL, page=7, offset=view['next_offset'], max_chars=12000)
    assert 'Physical page 7' in view['text'] + tail['text']
    assert tail['next_page'] == 8 and tail['next_offset'] is None
    unreadable = current.open_page(URL, page=9)
    assert unreadable['ok'] is False and unreadable['reason'] == 'page_requires_ocr_or_visual_verification'
    assert current.acquire({'url': URL})['ok'] is False
    tool = next(item for item in company_source_tools() if item['name'] == 'open_company_source')
    assert tool['input_schema']['properties']['page']['minimum'] == 1


def article(*, conflict=False):
    return ('<html><body><nav>' + 'Menu ' * 600 +
        '<time datetime="2025-01-01">January 1, 2025</time></nav><main>'
        '<section><header><h1>Synthetic issuer annual results</h1>'
        '<time datetime="2026-09-09T10:00:00Z">September 9, 2026</time>' +
        ('<time datetime="2026-09-10">September 10, 2026</time>' if conflict else '') +
        '</header><p>Year ended 2025-12-31</p><p>Synthetic issuer financial statements.</p></section>'
        '<aside><h2>Related articles</h2><time datetime="2024-01-01">January 1, 2024</time></aside>'
        '</main></body></html>').encode()


def test_main_article_header_excludes_navigation_and_related_dates_and_reverifies(tmp_path):
    current = session(tmp_path, FrozenTransport({'/report.html': (article(), 'text/html')}))
    result = current.acquire({'url': SITE + '/report.html'})
    assert result['ok'], result
    doc = current.snapshot()['documents'][0]
    assert doc['published_at'] == '2026-09-09'
    assert doc['metadata']['pm_source_verification']['publication_locator'] == 'html_article_header/1'
    assert 'January 1, 2024' in doc['text'], 'Source body is retained, not silently rewritten'
    assert current.acquire({'url': SITE + '/report.html'})['replayed'] is True


def test_conflicting_primary_article_dates_cannot_be_selected_by_claim(tmp_path):
    current = session(tmp_path, FrozenTransport({'/report.html': (article(conflict=True), 'text/html')}))
    result = current.acquire({'url': SITE + '/report.html', 'published_at': '2026-09-09',
                              'publication_quote': 'September 9, 2026'})
    assert result['ok'] is False
    assert 'Publication' in result['source']['documents'][0]['reason']


def test_pdf_byte_limit_is_finite_and_does_not_raise_html_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'MAX_BYTES', 100)
    monkeypatch.setattr(sources, 'MAX_PDF_BYTES', 10000)
    transport = FrozenTransport({'/report.pdf': (pdf(), 'application/pdf')})
    fetched = transport(URL, tmp_path, issuer_website=SITE)
    assert fetched['bytes'] > 100
    bad = FrozenTransport({'/report.pdf': (b'<html>' + b'x' * 101, 'application/pdf')})
    with pytest.raises(ValueError, match='PDF|limit'):
        bad(URL, tmp_path, issuer_website=SITE)
    too_big = FrozenTransport({'/report.pdf': (b'%PDF-' + b'x' * 10000, 'application/pdf')})
    with pytest.raises(ValueError, match='limit'):
        too_big(URL, tmp_path, issuer_website=SITE)


def test_page_receipt_binds_numeric_evidence_without_inventing_publication(tmp_path):
    from bellomberg.agents.trade_idea import _bound_evidence_details
    current = session(tmp_path, FrozenTransport({'/report.pdf': (pdf(), 'application/pdf')}))
    page = current.open_page(URL, page=1)
    assert page['observation_basis'] == 'exact_archived_page_read' and page['published_at'] is None
    board = SimpleNamespace(tool_receipts=[{'tool': 'open_company_source',
        'input': {'ticker': IDENTITY['ticker'], 'url': URL, 'page': 1},
        'output': json.dumps(page), 'success': True, 'truncated': False}])
    result = {'dossier': [{'evidence_ids': ['page1']}], 'evidence': [{'id': 'page1',
        'source': '[src: open_company_source]', 'as_of': page['observed_at'][:10], 'url': URL,
        'summary': 'Observed revenue 125.50 USD; publication date unverified.'}]}
    bound, numeric = _bound_evidence_details(result, board, IDENTITY['ticker'], page['observed_at'])
    assert 'page1' in bound and 'page1' in numeric
    result['evidence'][0]['summary'] = 'Revenue 987654321.22 USD not in this physical page.'
    assert _bound_evidence_details(result, board, IDENTITY['ticker'], page['observed_at'])[1] == set()
    result['evidence'][0]['url'] = SITE + '/different.pdf'
    assert _bound_evidence_details(result, board, IDENTITY['ticker'], page['observed_at'])[0] == {}


@pytest.mark.parametrize('replace', ['visible_conflict', 'bare_period', 'multiple_articles'])
def test_article_scope_does_not_infer_dates_or_override_ambiguous_identity(tmp_path, replace):
    body = article()
    if replace == 'visible_conflict':
        body = body.replace(b'datetime="2026-09-09T10:00:00Z"', b'datetime="2026-09-10T10:00:00Z"')
    elif replace == 'bare_period':
        body = body.replace(b'<time datetime="2026-09-09T10:00:00Z">September 9, 2026</time>',
                            b'<p>September 9, 2026</p>')
    else:
        body = body.replace(b'</main>', b'<h1>Another article</h1></main>')
    current = session(tmp_path, FrozenTransport({'/report.html': (body, 'text/html')}))
    result = current.acquire({'url': SITE + '/report.html'})
    assert result['ok'] is False
    assert 'Publication' in result['source']['documents'][0]['reason']
