"""Independent raw-bound listing reproof; declared synthetic HTML, no transport."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import socket
import sqlite3

import pytest

from bellomberg.valuation.input_preparation import _catalog
from bellomberg.valuation.preparation_fresh_historical import _quotation
from bellomberg.valuation.quotation_evidence import listing_identity_document, historical_quote_document
from bellomberg.valuation.quotation_evidence import verify_sec_listing

TICKER, ENTITY, ON, DAY = 'SYNTH-EXT', 'SYNTH-GROUP', '2025-12-31', '2026-09-10'
TITLE = 'Common Stock, par value $0.10 per share'

@pytest.fixture(autouse=True)
def transports(monkeypatch):
    counts = {'network': 0, 'sqlite': 0, 'paid': 0}
    def block(name):
        def refused(*args, **kwargs):
            counts[name] += 1
            pytest.fail(name + ' forbidden in independent listing reproof')
        return refused
    monkeypatch.setattr(socket, 'getaddrinfo', block('network'))
    monkeypatch.setattr(socket.socket, 'connect', block('network'))
    monkeypatch.setattr(sqlite3, 'connect', block('sqlite'))
    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', block('paid'))
    monkeypatch.setattr(anthropic, 'AsyncAnthropic', block('paid'))
    yield
    assert counts == {'network': 0, 'sqlite': 0, 'paid': 0}

def listing_catalog(tmp_path):
    raw = ('<html><body>'
           '<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="cover">'+ENTITY+'</ix:nonNumeric>'
           '<ix:nonNumeric name="dei:Security12bTitle" contextRef="cover">'+TITLE+'</ix:nonNumeric>'
           '<ix:nonNumeric name="dei:TradingSymbol" contextRef="cover">'+TICKER+'</ix:nonNumeric>'
           '<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="cover">TEST</ix:nonNumeric>'
           '</body></html>').encode()
    raw_hash = sha256(raw).hexdigest()
    path = tmp_path / (raw_hash + '.html')
    path.write_bytes(raw)
    primary = {'id': 'synthetic-raw-primary', 'url': 'https://example.org/declared-synthetic/filing.html',
        'published_at': '2026-02-01', 'archive_path': str(path), 'document_sha256': raw_hash,
        'text': raw.decode(), 'sha256': raw_hash,
        'metadata': {'issuer': ENTITY, 'report_date': ON, 'form': '10-K'}}
    generated = listing_identity_document(primary, raw, ticker=TICKER, on=ON)
    assert generated['status'] == 'ready'
    listing = generated['documents'][0]
    price = historical_quote_document(TICKER, on=ON, as_of=DAY, fetch=lambda *_:
        {'symbol': TICKER, 'currency': 'EUR', 'date': ON, 'close': 10.})['documents'][0]
    return primary, listing, price

def test_original_raw_html_listing_has_a_positive_same_class_unit_control(tmp_path):
    primary, listing, price = listing_catalog(tmp_path)
    catalog, issues, _ = _catalog([primary, listing, price], date.fromisoformat(DAY))
    assert not issues, issues
    result = _quotation(catalog, TICKER, ON, 'EUR', DAY, entity=ENTITY, primary_id=primary['id'])
    assert result['value']['share_class'] == TITLE
    assert result['value']['shares_per_quote'] == 1

def test_resealed_ordinary_class_cannot_replace_the_same_primarys_raw_listing(tmp_path):
    primary, listing, price = listing_catalog(tmp_path)
    source_bytes = open(primary['archive_path'], 'rb').read()
    listing = deepcopy(listing)
    body = json.loads(listing['text'])
    body['listing']['title'] = 'Class B Ordinary Shares, par value $0.01 per share'
    listing['metadata']['share_class'] = body['listing']['title']
    listing['text'] = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
    listing['sha256'] = sha256(listing['text'].encode()).hexdigest()
    # Raw bytes and their receipt remain sealed: only the purported normalized
    # class and its internally consistent text hash have changed.
    assert sha256(source_bytes).hexdigest() == primary['document_sha256'] == listing['document_sha256']
    catalog, issues, _ = _catalog([primary, listing, price], date.fromisoformat(DAY))
    if issues:
        assert listing['id'] not in catalog
        return
    try:
        result = _quotation(catalog, TICKER, ON, 'EUR', DAY, entity=ENTITY, primary_id=primary['id'])
    except ValueError:
        return
    pytest.fail('Raw '+TITLE+' was replaced by '+result['value']['share_class']+' while source bytes/hash stayed unchanged')


@pytest.mark.parametrize('representation', ['archive', 'exact_raw_text'])
def test_helper_recompiles_the_same_raw_bytes_after_catalog_normalization(tmp_path, representation):
    primary, listing, price = listing_catalog(tmp_path)
    if representation == 'exact_raw_text':
        primary.pop('archive_path')
    catalog, issues, _ = _catalog([primary, listing, price], date.fromisoformat(DAY))
    assert not issues
    rebuilt = verify_sec_listing(catalog[listing['id']], catalog[primary['id']], ticker=TICKER, on=ON)
    assert rebuilt['text'] == listing['text']
    assert rebuilt['document_sha256'] == primary['document_sha256']


@pytest.mark.parametrize('fault', ['missing_archive', 'changed_archive', 'transformed_text',
    'wrong_raw_issuer', 'ambiguous_raw_issuer', 'forged_context', 'wrong_explicit_origin', 'missing_raw_sha'])
def test_helper_rejects_unavailable_changed_or_false_original_proof(tmp_path, fault):
    from pathlib import Path
    primary, listing, _ = listing_catalog(tmp_path)
    if fault == 'missing_archive':
        primary['archive_path'] = str(tmp_path / 'missing.html')
    elif fault == 'changed_archive':
        Path(primary['archive_path']).write_bytes(b'<html>different raw bytes</html>')
    elif fault == 'transformed_text':
        primary.pop('archive_path')
        primary['text'] = 'Extracted ordinary share text without original ix contexts'
        primary['sha256'] = sha256(primary['text'].encode()).hexdigest()
    elif fault == 'wrong_raw_issuer':
        primary['metadata']['issuer'] = 'OTHER-GROUP'
    elif fault == 'ambiguous_raw_issuer':
        raw = Path(primary['archive_path']).read_bytes() + b'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="other">OTHER-GROUP</ix:nonNumeric>'
        Path(primary['archive_path']).write_bytes(raw)
        primary['text'] = raw.decode()
        primary['document_sha256'] = primary['sha256'] = sha256(raw).hexdigest()
    elif fault == 'forged_context':
        listing['extraction_coverage']['context_id'] = 'self-attested-alternative-context'
    elif fault == 'wrong_explicit_origin':
        listing['origin'] = 'self-attested'
    else:
        primary.pop('document_sha256')
    with pytest.raises(ValueError):
        verify_sec_listing(listing, primary, ticker=TICKER, on=ON)
