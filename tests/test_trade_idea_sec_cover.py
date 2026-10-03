"""SEC cover metadata: frozen public bytes, real ingestion and no network."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from bellomberg.agents import trade_idea_sources as sources
from bellomberg.agents.company_source_research import ResearchSession
from bellomberg.market_data import lettore_trimestrali
from test_company_source_research import FrozenTransport, html


FIXTURES = Path(__file__).parent / 'fixtures' / 'sec_cover'
FROZEN_SHA = '1b96b259139fbe986b1ca12a12e13f9e8ca8c6d393a40d9e581c574856861279'
FROZEN_URL = 'https://www.sec.gov/Archives/edgar/data/1543151/000154315126000032/uber-20260630.htm'
FROZEN_IDENTITY = {'status': 'confirmed', 'ticker': 'UBER', 'name': 'Uber Technologies, Inc',
                   'exchange': 'XNYS', 'currency': 'USD'}
SYNTH_IDENTITY = {'status': 'confirmed', 'ticker': 'SYNTH', 'name': 'Synthetic issuer Corporation',
                  'exchange': 'XNYS', 'currency': 'USD'}
SYNTH_URL = 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/annual.html'
COVER = ('UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nFORM 10-Q\n'
    'For the quarterly period ended June 30, 2026\n'
    'Synthetic issuer Corporation\n(Exact name of registrant as specified in its charter)\n'
    'Consolidated balance sheets as of June 30, 2026 and December 31, 2025.\n'
    'Shares outstanding as of July 31, 2026.\nThe previous report was as of March 31, 2026.\n')


def frozen_raw():
    raw = b''.join((FIXTURES / f'uber-20260630.part-{number}.html').read_bytes()
                   for number in (1, 2))
    assert len(raw) == 2419544 and sha256(raw).hexdigest() == FROZEN_SHA
    return raw


def catalog_for(url=SYNTH_URL, identity=SYNTH_IDENTITY, **changes):
    entry = dict(ticker=identity['ticker'], url=url, issuer=identity['name'],
        emittente_id='CIK:0000000123', form='10-Q', accession='0000000123-26-000001',
        filed_date='2026-08-05', report_date='2026-06-30')
    entry.update(changes)
    return {'stato': 'ok', 'fonte': 'SEC EDGAR', 'documenti': [entry], 'motivi': []}


def admit(tmp_path, *, raw=None, text=COVER, identity=SYNTH_IDENTITY, url=SYNTH_URL,
          catalog=None, claims=None):
    transport = FrozenTransport({urlsplit(url).path: (raw if raw is not None else html(text), 'text/html')})
    catalog = deepcopy(catalog or catalog_for(url, identity))
    result = sources.ingest_document_sources(identity['ticker'], identity, '2026-10-02',
        [{'url': url, **(claims or {})}], archive_root=tmp_path, download=transport,
        publication_catalog=lambda *_args, **_kwargs: deepcopy(catalog))
    return result, transport


def test_frozen_sec_filing_admitted_from_cover_and_reverified_twice_without_retrieval(tmp_path):
    raw = frozen_raw()
    catalog = json.loads((FIXTURES / 'uber-catalog.json').read_text(encoding='utf-8'))
    result, transport = admit(tmp_path, raw=raw, url=FROZEN_URL, identity=FROZEN_IDENTITY, catalog=catalog)
    receipt = result['receipt']
    row = receipt['documents'][0]
    assert row['sha256'] == FROZEN_SHA and Path(row['path']).read_bytes() == raw
    assert row['bytes'] == 2419544 and row['status'] == 'verified'
    assert row['metadati']['report_date'] == '2026-06-30' and row['filed_date'] == '2026-08-05'
    assert row['metadati']['form'] == '10-Q'
    proof = row['metadati']['pm_source_verification']
    assert proof['proofs']['report_date']['quote'] == 'For the quarterly period ended June 30, 2026'
    assert 'UBER TECHNOLOGIES, INC.' in proof['proofs']['issuer']['quote']
    text = result['documents'][0]['text']
    assert text.index('UBER TECHNOLOGIES, INC.') < 2000
    assert 'Condensed Consolidated Balance Sheets' in text
    assert sha256(text.encode()).hexdigest() == row['text_sha256']
    assert proof['security_identity_verified'] is False
    for _ in range(2):
        replay = sources.verify_document_receipt(receipt, 'UBER', FROZEN_IDENTITY,
            '2026-10-02', archive_root=tmp_path)
        assert replay['receipt'] == receipt and replay['documents'] == result['documents']
    assert len(transport.requests) == 1
    Path(row['path']).write_bytes(raw + b'changed after admission')
    with pytest.raises(ValueError, match='bytes|digest|changed'):
        sources.verify_document_receipt(receipt, 'UBER', FROZEN_IDENTITY, '2026-10-02', archive_root=tmp_path)


@pytest.mark.parametrize('fault', [None, 'raw_bytes', 'text_sha', 'locator', 'extraction_version'])
def test_legacy_accepted_sec_receipt_replays_exact_text_and_proofs(tmp_path, fault):
    raw = frozen_raw()
    receipt = json.loads((FIXTURES / 'uber-accepted-legacy.json').read_text(encoding='utf-8'))
    row = receipt['documents'][0]
    root = tmp_path / 'pm-public-documents'
    (root / 'publication-receipts').mkdir(parents=True)
    row['path'] = str(root / row['path'])
    Path(row['path']).write_bytes(raw)
    publication = row['publication_receipt']
    publication['path'] = str(root / publication['path'])
    publication_bytes = json.dumps({key: publication[key] for key in
        ('contract', 'source_url', 'catalog_status', 'entry')}, sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()
    assert sha256(publication_bytes).hexdigest() == publication['sha256']
    Path(publication['path']).write_bytes(publication_bytes)
    assert receipt['fingerprint'] == sources._receipt_fingerprint(receipt)
    assert 'text_extraction' not in row['metadati']['pm_source_verification']
    new_text = lettore_trimestrali.estrai_testo('frozen.html', contenuto=raw)['testo']
    assert row['text_sha256'] != sha256(new_text.encode()).hexdigest()
    if fault == 'raw_bytes':
        Path(row['path']).write_bytes(raw + b'changed')
    elif fault == 'text_sha':
        row['text_sha256'] = sha256(new_text.encode()).hexdigest()
    elif fault == 'locator':
        row['metadati']['pm_source_verification']['proofs']['issuer']['start'] += 1
    elif fault == 'extraction_version':
        row['metadati']['pm_source_verification']['text_extraction'] = 'unknown/99'
    if fault:
        receipt['fingerprint'] = sources._receipt_fingerprint(receipt)
        with pytest.raises(ValueError):
            sources.verify_document_receipt(receipt, 'UBER', FROZEN_IDENTITY, '2026-10-02', archive_root=tmp_path)
    else:
        original = deepcopy(receipt)
        for _ in range(2):
            replay = sources.verify_document_receipt(receipt, 'UBER', FROZEN_IDENTITY, '2026-10-02', archive_root=tmp_path)
            assert replay['receipt'] == original == receipt
            assert replay['documents'][0]['sha256'] == row['text_sha256']
            assert sha256(replay['documents'][0]['text'].encode()).hexdigest() == row['text_sha256']


@pytest.mark.parametrize('form,phrase,period', [
    ('10-Q', 'For the quarterly period ended June 30, 2026', '2026-06-30'),
    ('10-K', 'For the fiscal year ended December 31, 2025', '2025-12-31'),
    ('20-F', 'For the year ended December 31, 2025', '2025-12-31'),
])
def test_primary_sec_cover_period_has_priority_over_comparative_dates(tmp_path, form, phrase, period):
    text = COVER.replace('FORM 10-Q', 'FORM ' + form).replace(
        'For the quarterly period ended June 30, 2026', phrase)
    result, _ = admit(tmp_path, text=text, catalog=catalog_for(form=form, report_date=period))
    row = result['receipt']['documents'][0]
    assert row['metadati']['form'] == form and row['metadati']['report_date'] == period
    assert row['metadati']['pm_source_verification']['proofs']['report_date']['quote'] == phrase


@pytest.mark.parametrize('manual', [False, True])
def test_conflicting_sec_cover_periods_remain_ambiguous_even_with_manual_locator(tmp_path, manual):
    text = COVER.replace('Synthetic issuer Corporation',
        'For the quarterly period ended March 31, 2026\nSynthetic issuer Corporation', 1)
    claims = {'report_date': '2026-06-30', 'report_date_quote':
        'For the quarterly period ended June 30, 2026'} if manual else None
    with pytest.raises(sources.SourceIngestionError) as error:
        admit(tmp_path, text=text, claims=claims)
    assert 'contradictory SEC cover' in error.value.receipt['documents'][0]['reason']


@pytest.mark.parametrize('fault', ['second_registrant', 'different_registrant', 'catalog_form'])
def test_sec_registrant_and_form_are_not_overridden_by_name_mentions_or_optional_claims(tmp_path, fault):
    text, catalog = COVER, catalog_for()
    if fault == 'second_registrant':
        text += '\nOther issuer Corporation\n(Exact name of registrant as specified in its charter)\n'
    elif fault == 'different_registrant':
        text = COVER.replace('Synthetic issuer Corporation', 'Other issuer Corporation')
        text += '\nA customer of Synthetic issuer Corporation.\n'
    else:
        catalog['documenti'][0]['form'] = '10-K'
    with pytest.raises(sources.SourceIngestionError) as error:
        admit(tmp_path, text=text, catalog=catalog, claims={'report_date': '2026-06-30',
            'report_date_quote': 'For the quarterly period ended June 30, 2026',
            'issuer_quote': 'Synthetic issuer Corporation' if fault != 'different_registrant'
                else 'A customer of Synthetic issuer Corporation.'})
    assert any(word in error.value.receipt['documents'][0]['reason'] for word in ('registrant', 'SEC cover'))


def test_frozen_sec_admission_crash_and_two_resumes_reuse_document_and_catalog(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass
    raw = frozen_raw()
    transport = FrozenTransport({urlsplit(FROZEN_URL).path: (raw, 'text/html')})
    catalog = json.loads((FIXTURES / 'uber-catalog.json').read_text(encoding='utf-8'))
    calls = []
    def catalog_provider(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(catalog)
    options = dict(run_id='frozen-sec-cover', ticker='UBER', identity=FROZEN_IDENTITY,
        as_of='2026-10-02', admission_fingerprint='a' * 64, archive_root=tmp_path / 'archive',
        run_dir=tmp_path / 'run', download=transport, publication_catalog=catalog_provider)
    research = ResearchSession(**options)
    append = research._append
    def crash_after_verification(kind, payload):
        if kind == 'source_accepted':
            raise Crash()
        return append(kind, payload)
    monkeypatch.setattr(research, '_append', crash_after_verification)
    with pytest.raises(Crash):
        research.acquire({'url': FROZEN_URL})
    recovered = ResearchSession(**options)
    accepted = recovered.acquire({'url': FROZEN_URL})
    assert accepted['ok'] is True, accepted
    snapshot = recovered.snapshot(expected_revision_id=accepted['revision_id'])
    again = ResearchSession(**options)
    assert again.acquire({'url': FROZEN_URL})['replayed'] is True
    assert again.snapshot(expected_revision_id=accepted['revision_id']) == snapshot
    assert snapshot['parent_grant_fingerprint'] == 'a' * 64 and len(snapshot['documents']) == 1
    assert len(snapshot['revisions']) == 1
    assert len(transport.requests) == len(calls) == 1


def test_rejected_frozen_request_stays_immutable_explicit_locator_revision_uses_cached_bytes(tmp_path, monkeypatch):
    raw = frozen_raw()
    transport = FrozenTransport({urlsplit(FROZEN_URL).path: (raw, 'text/html')})
    catalog = json.loads((FIXTURES / 'uber-catalog.json').read_text(encoding='utf-8'))
    legacy = json.loads((FIXTURES / 'uber-source-rejected-baseline.json').read_text(encoding='utf-8'))
    request_fingerprint = legacy.pop('request_fingerprint')
    assert request_fingerprint == sha256(json.dumps(legacy['source'], ensure_ascii=False,
        sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
    assert 'key' not in legacy
    legacy['key'] = request_fingerprint  # Restore the exact native historical payload in memory.
    calls = []
    def catalog_provider(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(catalog)
    options = dict(run_id='frozen-sec-explicit-recovery', ticker='UBER', identity=FROZEN_IDENTITY,
        as_of='2026-10-02', admission_fingerprint='b' * 64, archive_root=tmp_path / 'archive',
        run_dir=tmp_path / 'run', download=transport, publication_catalog=catalog_provider)
    research = ResearchSession(**options)
    # Reproduce the two previous extraction rules, then pin the entire actual
    # rejection against the receipt saved by the RED baseline implementation.
    with monkeypatch.context() as old_parser:
        old_parser.setattr(lettore_trimestrali._TestoHTML, '_MUTI',
            tuple(tag for tag in lettore_trimestrali._TestoHTML._MUTI if tag != 'ix:header'))
        old_parser.setattr(sources, '_primary_sec_cover', lambda _text: None)
        rejected = research.acquire({'url': FROZEN_URL})
    assert rejected['status'] == 'needs_verification' and rejected['ok'] is False
    assert 'Identical' in rejected['recovery_instruction']
    assert 'open_company_source' in rejected['recovery_instruction']
    rejected_events = [p for p in (tmp_path / 'run').rglob('events/*.json')
        if json.loads(p.read_text(encoding='utf-8'))['kind'] == 'source_rejected']
    assert len(rejected_events) == 1
    rejected_path = rejected_events[0]
    original_bytes = rejected_path.read_bytes()
    assert json.loads(original_bytes)['payload'] == legacy
    recovered = ResearchSession(**options)
    replayed = recovered.acquire({'url': FROZEN_URL})
    assert replayed == {**rejected, 'replayed': True}
    from bellomberg.agents.specialists.base import Specialist
    from test_company_dossier_recovery import read_catalog
    board = SimpleNamespace(current_round=1, valuation_results={},
        company_source_session=lambda ticker: recovered)
    desk = Specialist(board, client=object())
    dossier = read_catalog(desk, 'UBER')
    diagnostic = dossier['acquisition_diagnostics'][0]
    assert diagnostic['url'] == FROZEN_URL
    assert diagnostic['reason'] == legacy['receipt']['documents'][0]['reason']
    assert 'report_date_quote' in diagnostic['recovery_instruction']
    navigation = desk._execute_meta_tool('open_company_source', {'ticker': 'UBER', 'url': diagnostic['url']})
    assert navigation['ok'] is True and navigation['replayed'] is True
    assert 'For the quarterly period ended June 30, 2026' in navigation['text']
    corrected = {'url': FROZEN_URL, 'report_date': '2026-06-30',
        'report_date_quote': 'For the quarterly period ended June 30, 2026'}
    accepted = desk._execute_meta_tool('acquire_company_source', {'ticker': 'UBER', 'source': corrected})
    assert accepted['ok'] is True and accepted['replayed'] is False, accepted
    snapshot = recovered.snapshot(expected_revision_id=accepted['revision_id'])
    assert len(snapshot['revisions']) == 1 and len(snapshot['documents']) == 1
    assert snapshot['documents'][0]['document_sha256'] == FROZEN_SHA
    assert snapshot['documents'][0]['url'] == FROZEN_URL
    for _ in range(2):
        again = ResearchSession(**options)
        assert again.acquire(corrected) == {**accepted, 'replayed': True}
        assert again.snapshot(expected_revision_id=accepted['revision_id']) == snapshot
        assert again.acquire({'url': FROZEN_URL}) == replayed
    assert rejected_path.read_bytes() == original_bytes
    assert snapshot['parent_grant_fingerprint'] == 'b' * 64
    assert len(transport.requests) == len(calls) == 1
