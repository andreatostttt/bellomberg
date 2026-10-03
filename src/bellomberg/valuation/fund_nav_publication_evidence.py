"""An archived publisher catalog tool observation, explicitly not original HTML.

The acquiring caller must archive actual tool results and verify their receipt.
This deterministic normalizer checks the catalog-to-click-to-original-PDF chain;
it never promotes a PM date assertion or a failed HTTP body to primary evidence.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from .fund_nav_quote_evidence import _source, _json
from .fund_nav_statement import normalize_fund_statement

NORMALIZER = 'fund_nav_publication_catalog_v1'
PREFIX = 'fund-nav-publication-'
RECEIPT_CONTRACT = 'publisher_catalog_tool_receipt/1'
LIMITATION = ('Archived normalized publisher catalog and exact PDF-click tool results, not original HTML; '
    'publisher calendar date is not a UTC publication timestamp. Availability remains the observed retrieval day. '
    'The original PDF separately proves the selected common-equity NAV class; no dilution or target approval.')


def normalize_fund_publication(catalog_source, statement_source, *, entity, share_class, on, as_of):
    from datetime import date
    cutoff, opening = date.fromisoformat(as_of), date.fromisoformat(on)
    catalog_dates, statement_dates = _source(catalog_source, cutoff), _source(statement_source, cutoff)
    receipt = json.loads(catalog_source['text'])
    expected = {'contract', 'source_url', 'tool_name', 'observed_at', 'raw_primary_page_downloaded',
                'catalog_reference', 'link_id', 'catalog_tool_result', 'document_tool_result'}
    if (not isinstance(receipt, dict) or set(receipt) != expected
            or receipt['contract'] != RECEIPT_CONTRACT or receipt['tool_name'] != 'web.run'
            or receipt['raw_primary_page_downloaded'] is not False
            or receipt['source_url'] != catalog_source['url']
            or type(receipt['link_id']) is not int or receipt['link_id'] < 0
            or not re.fullmatch(r'turn\d+view\d+', str(receipt['catalog_reference']))
            or not all(isinstance(receipt[key], str) and receipt[key] for key in
                       ('catalog_tool_result', 'document_tool_result', 'observed_at'))):
        raise ValueError('complete actual publisher catalog tool receipt required; never a raw-HTML claim')
    if (catalog_dates['availability_basis'] != 'observed_download'
            or receipt['observed_at'] != (catalog_source.get('retrieval') or {}).get('retrieved_at')):
        raise ValueError('tool observation timestamp must match its archived acquisition receipt')
    catalog_text, pdf_text = receipt['catalog_tool_result'], receipt['document_tool_result']
    reference, link = receipt['catalog_reference'], receipt['link_id']
    if (catalog_text.count('('+catalog_source['url']+')') != 1
            or '\ue200cite\ue202'+reference+'\ue201' not in catalog_text
            or not pdf_text.startswith(' ('+statement_source['url']+')\n')
            or 'Content type: application/pdf;' not in pdf_text
            or 'Source: click('+json.dumps({'ref_id': reference, 'id': link}, separators=(',', ':'))+');' not in pdf_text):
        raise ValueError('catalog reference and exact PDF URL must bind the same recorded link click')
    issuer_host, pdf_host = urlsplit(catalog_source['url']).hostname, urlsplit(statement_source['url']).hostname
    if (not issuer_host or not pdf_host or (pdf_host != issuer_host and not pdf_host.endswith('.'+issuer_host))):
        raise ValueError('PDF must be served by the same declared publisher or its subdomain')
    if entity not in pdf_text[:3000]:
        raise ValueError('exact legal entity missing from the linked PDF tool header')
    pattern = (r'L\d+:\s+\*\s+([A-Za-z]+ \d{1,2}, \d{4}) ((?:(?!L\d+:).)*?)'
               r'\ue200cite\ue202'+str(link)+r'\u2020PDF\u2020([^\ue201]+)\ue201')
    rows = list(re.finditer(pattern, catalog_text))
    if len(rows) != 1:
        raise ValueError('one unique dated publisher catalog entry required for the exact PDF link')
    row = rows[0]
    published = datetime.strptime(row[1], '%B %d, %Y').date()
    if (row[3] != pdf_host or not re.search(r'\bFinancial Statements\b', row[2], re.I)
            or re.findall(r'\b20\d{2}\b', row[2]) != [str(opening.year)]
            or not opening <= published <= cutoff):
        raise ValueError('publisher entry date, report year or linked host differs from the economic snapshot')
    statement = normalize_fund_statement(statement_source)
    if statement['status'] != 'ready':
        raise ValueError('complete original class-attributed financial statement required')
    facts = json.loads(statement['documents'][0]['text'])['facts']
    selected = [fact for fact in facts if fact['entity'] == entity and fact['end'] == on
                and fact.get('share_class') == share_class
                and fact['concept'] in ('ClassNetAssets', 'ClassSharesOutstanding', 'ClassNavPerShare')]
    if len(selected) != 3 or {fact['concept'] for fact in selected} != {'ClassNetAssets', 'ClassSharesOutstanding', 'ClassNavPerShare'}:
        raise ValueError('publication must bind the exact net-assets, share count and NAV of the selected class/date')
    if share_class != 'Public Shares':
        raise ValueError('supported published common-equity class must be explicit; no other-class substitution')
    if statement_dates['published_at'] not in (None, published.isoformat()):
        raise ValueError('publisher catalog and independently known original PDF publication dates disagree')
    publication = {'publisher': entity, 'publication_date': published.isoformat(), 'valuation_date': on,
                   'basis': 'common_equity_net', 'share_class': share_class}
    payload = {'publication': publication, 'publication_date_basis': 'publisher_catalog_calendar_date',
        'raw_primary_page_downloaded': False, 'statement_source_document_id': statement_source['id'],
        'statement_text_sha256': statement_source['sha256'], 'net_class_facts': selected,
        'catalog_evidence': {'source_document_id': catalog_source['id'], 'source_url': catalog_source['url'],
            'tool_name': receipt['tool_name'], 'observed_at': receipt['observed_at'],
            'catalog_reference': reference, 'link_id': link, 'entry': row[0],
            'entry_sha256': sha256(row[0].encode()).hexdigest(), 'linked_pdf_url': statement_source['url']},
        'source_document_ids': {'catalog': catalog_source['id'], 'statement': statement_source['id']},
        'limitation': LIMITATION}
    encoded = _json(payload)
    return {'id': PREFIX+sha256(encoded.encode()).hexdigest(), 'url': catalog_source['url'],
        **deepcopy(catalog_dates), 'document_sha256': catalog_source['document_sha256'],
        'text': encoded, 'sha256': sha256(encoded.encode()).hexdigest(),
        'metadata': {'normalizer': NORMALIZER, 'source_document_ids': payload['source_document_ids'],
            'entity': entity, 'share_class': share_class, 'on': on, 'report_date': on, 'as_of': as_of,
            'origin': 'archived_publisher_catalog_tool_receipt', 'raw_primary_page_downloaded': False}}
