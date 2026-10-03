"""Bounded orchestration of an acquired raw catalog, without discovery or AI."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import urljoin


def assemble_commodity_catalog(bundle, report, *, as_of):
    """Identify three original roles, then invoke the existing raw-byte prover."""
    from .commodity_trust_evidence import NORMALIZER, normalize_commodity_trust
    from .input_preparation import _catalog, _day
    result = deepcopy(report)
    receipt = {'contract': 'commodity_trust_raw_catalog/1', 'status': 'blocked', 'reasons': [],
               'discovery': 'not_performed', 'source_plan_supplied': False}
    result['commodity_trust_raw_catalog'] = receipt
    documents = result.get('documents') or []
    if any((doc.get('metadata') or {}).get('normalizer') == NORMALIZER for doc in documents if isinstance(doc, dict)):
        receipt.update(status='existing_normalization_requires_reproof')
        return result
    try:
        if not isinstance(documents, list) or not 3 <= len(documents) <= 8:
            raise ValueError('A bounded acquired catalog of 3–8 original primary documents is required')
        catalog, issues, _ = _catalog(documents, _day(as_of))
        if issues:
            raise ValueError('; '.join(row['reason'] for row in issues))
        originals = [doc for doc in catalog.values() if not (doc.get('metadata') or {}).get('normalizer')]
        raw = {}
        for doc in originals:
            content = Path(doc['archive_path']).read_bytes()
            if not content or len(content) > 20_000_000 or sha256(content).hexdigest() != doc.get('document_sha256'):
                raise ValueError('Raw primary bytes differ from their acquired SHA or exceed the source bound')
            raw[doc['id']] = content
        products = [doc for doc in originals if not raw[doc['id']].startswith(b'%PDF-') and
                    b'application/ld+json' in raw[doc['id']] and b'keyFundFacts-closingPrice' in raw[doc['id']] and
                    b'keyFundFacts-thirtyDayMedianBidAskSpread' in raw[doc['id']]]
        if len(products) != 1:
            raise ValueError('Unique original issuer product with dated market metrics required; missing/ambiguous product role')
        product = products[0]
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw[product['id']], 'html.parser')
        links = {}
        for anchor in soup.find_all('a', href=True):
            url = urljoin(product['url'], anchor['href'])
            links.setdefault(url, []).append(anchor.get_text(' ', strip=True))
        from pypdf import PdfReader
        statements, prospectuses = [], []
        for doc in originals:
            if doc['url'] not in links or not raw[doc['id']].startswith(b'%PDF-'):
                continue
            reader = PdfReader(BytesIO(raw[doc['id']]))
            if not 1 <= len(reader.pages) <= 120:
                raise ValueError('Bounded complete primary PDF required')
            cover = ' '.join((reader.pages[0].extract_text() or '').split())
            period = re.findall(r'For the quarterly period ended ([A-Za-z]+ \d{1,2}, \d{4})', cover)
            if len(period) == 1 and re.search(r'\bFORM\s+10-Q\b', cover, re.I):
                day = datetime.strptime(period[0], '%B %d, %Y').date().isoformat()
                statements.append((doc, day))
            if any(re.search(r'\bprospectus\b', label, re.I) for label in links[doc['url']]):
                prospectuses.append(doc)
        if len(statements) != 1 or len(prospectuses) != 1:
            raise ValueError('Unique linked quarterly statement and prospectus required; missing/ambiguous primary roles')
        statement, on = statements[0]
        info = bundle['case'].get('info') or {}
        entity = info.get('longName') or info.get('shortName')
        compiled = normalize_commodity_trust(statement, product, prospectuses[0],
            ticker=bundle['case']['ticker'], entity=entity, on=on, as_of=as_of)
        if compiled['status'] != 'ready':
            raise ValueError('; '.join(compiled['issues']))
        result['documents'] = list(catalog.values()) + compiled['documents']
        receipt.update(status='ready', roles={'statement': statement['id'], 'product': product['id'],
            'prospectus': prospectuses[0]['id']}, economic_opening_date=on,
            normalized_document_id=compiled['documents'][0]['id'])
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        receipt['reasons'].append(str(exc))
    return result
