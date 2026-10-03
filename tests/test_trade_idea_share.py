"""Offline F12 exports: rebuilt public models, explicit members and privacy channels.

All issuers, sources and private canaries are synthetic. The tests certify export
wiring and exclusion; they do not certify a real issuer or investment thesis.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
from zipfile import ZipFile

import pytest

from trade_idea_fixtures import bind_workbook, research_result, run_record
from test_trade_idea_economic import qualified
from test_trade_idea_economic import _operating_plan


CANARY = 'PRIVATE_PM_CANARY_927415_DO_NOT_SHARE'


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Shared export attempted a network connection')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


@pytest.fixture
def public_model(tmp_path):
    from bellomberg.valuation.trade_idea_model import prepare
    payload = prepare(qualified(tmp_path), lambda *_: deepcopy(_operating_plan()), tmp_path/'private-model')
    assert payload['valuation_usability']['usable']
    # Synthetic registered receipt: this suite tests reporting, not DB publication.
    payload['_thesis_saved'] = {'thesis_id': 1}
    return payload


def _rewrite_part(path, name, transform):
    with ZipFile(path) as source:
        parts = {part: source.read(part) for part in source.namelist()}
    parts[name] = transform(parts.get(name, b''))
    with ZipFile(path, 'w') as target:
        for part, value in parts.items():
            target.writestr(part, value)


def _reseal(payload):
    payload['workbook_sha256'] = sha256(Path(payload['path']).read_bytes()).hexdigest()
    Path(payload['path']).with_suffix('.payload.json').write_text(
        json.dumps({key: value for key, value in payload.items() if key != 'path'}, ensure_ascii=False), encoding='utf-8')


@pytest.mark.parametrize('language', ['it', 'en'])
def test_observed_exposure_exports_a_public_pair_without_private_content_or_fair_value(tmp_path, language):
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from test_trade_idea_exposure import sourced_exposure
    from bellomberg.valuation.trade_idea_model import prepare, candidate_model_usability
    from bellomberg.reporting.trade_idea_share import preview_shareable_package, build_shareable_package, verify_shareable_package
    q, plan = sourced_exposure(tmp_path)
    payload = prepare(q, lambda *_: deepcopy(plan), tmp_path/'private-model')
    payload['_thesis_saved'] = {'thesis_id': 1}
    workbook = load_workbook(payload['path'])
    workbook.properties.creator = CANARY
    workbook['Summary']['Z1'] = CANARY
    workbook.create_sheet('Private holdings note').sheet_state = 'hidden'
    workbook['Private holdings note']['A1'] = CANARY
    workbook.save(payload['path']); workbook.close()
    payload['preparation']['provenance']['pm_note'] = CANARY
    _reseal(payload)
    before = Path(payload['path']).read_bytes()
    run, result = run_record(), bind_workbook(research_result('rejected'), payload)
    run['ticker'] = result['ticker'] = payload['ticker']
    run['pm_view'] = CANARY
    result['summary'] += ' ' + CANARY
    models = {payload['ticker']: payload}
    preview = preview_shareable_package(run, result, models, model_roots=[tmp_path])
    assert preview['status'] == 'ready', preview['reasons']
    manifest = build_shareable_package(run, result, models, output_dir=tmp_path/'share',
        model_roots=[tmp_path], language=language, private_terms=[CANARY])
    assert verify_shareable_package(manifest, private_terms=[CANARY])
    artifacts = {item['kind']: item for item in manifest['artifacts']}
    assert set(artifacts) == {'pdf', 'xlsx'}
    assert artifacts['xlsx']['generation_id'] != payload['generation_id']
    shared_path = Path(artifacts['xlsx']['path'])
    shared = json.loads(shared_path.with_suffix('.payload.json').read_text(encoding='utf-8'))
    assert candidate_model_usability(shared)['kind'] == 'exposure'
    assert candidate_model_usability(shared)['usable']
    assert shared['exposure_analysis']['gross_exposure'] == payload['exposure_analysis']['gross_exposure']
    assert shared['exposure_analysis']['net_exposure'] == payload['exposure_analysis']['net_exposure']
    assert not shared['valuation_usability']['usable']
    assert not any(key.startswith('fair_value') and value is not None for key, value in shared.items())
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(artifacts['pdf']['path']).pages)
    assert ('Non applicabile' if language == 'it' else 'Not applicable') in text
    assert CANARY not in text
    with ZipFile(manifest['path']) as archive:
        assert set(archive.namelist()) == {artifacts['pdf']['name'], artifacts['xlsx']['name'], 'source-inventory.json', 'version-inventory.json'}
        for name in archive.namelist():
            if name.endswith('.xlsx'):
                with ZipFile(shared_path) as workbook_archive:
                    assert all(CANARY.encode() not in workbook_archive.read(part) for part in workbook_archive.namelist())
            else:
                assert CANARY.encode() not in archive.read(name)
    assert Path(payload['path']).read_bytes() == before


@pytest.mark.parametrize('language', ['it', 'en'])
def test_shared_pair_and_explicit_zip_exclude_original_private_channels(public_model, tmp_path, language, monkeypatch):
    from openpyxl import load_workbook
    from openpyxl.comments import Comment
    from openpyxl.workbook.defined_name import DefinedName
    from pypdf import PdfReader
    from bellomberg.reporting.trade_idea_share import (
        preview_shareable_package, build_shareable_package, verify_shareable_package)
    payload = public_model
    workbook = load_workbook(payload['path'])
    workbook.properties.creator = CANARY
    workbook['Summary']['Z1'] = CANARY
    workbook['Summary']['E8'].comment = Comment(CANARY, CANARY)
    workbook['Summary']['Z2'].hyperlink = 'file:///C:/Users/private/book.xlsx'
    workbook.create_sheet('Private context').sheet_state = 'hidden'
    workbook['Private context']['A1'] = CANARY
    workbook.defined_names.add(DefinedName(CANARY, attr_text="'Summary'!$Z$1"))
    workbook.save(payload['path']); workbook.close()
    payload['preparation']['provenance']['pm_note'] = CANARY
    _reseal(payload)
    original_bytes = Path(payload['path']).read_bytes()
    run, result = run_record(), bind_workbook(research_result('rejected'), payload)
    run.update(pm_view=CANARY, portfolio={'position': CANARY})
    result['summary'] += ' ' + CANARY
    result['dossier'][0]['paragraphs'].append(CANARY)
    result['objections'][0]['response'] += ' ' + CANARY
    preview = preview_shareable_package(run, result, {'SYNTH-EXT': payload}, model_roots=[tmp_path])
    assert preview['status'] == 'ready', preview['reasons']
    assert {row['category'] for row in preview['exclusions']} == {
        'personal_context', 'pm_content', 'committee_prose', 'local_metadata', 'original_annotations'}
    assert CANARY not in json.dumps(preview)
    shared = build_shareable_package(run, result, {'SYNTH-EXT': payload},
        output_dir=tmp_path/'share', model_roots=[tmp_path], language=language, private_terms=[CANARY])
    assert verify_shareable_package(shared, private_terms=[CANARY])
    assert shared['report_kind'] == 'shareable_model_extract'
    assert all(check['status'] == 'passed' for check in shared['privacy_checks'])
    xlsx = next(item for item in shared['artifacts'] if item['kind'] == 'xlsx')
    assert xlsx['snapshot_id'] != payload['snapshot_id']
    assert xlsx['generation_id'] != payload['generation_id']
    assert xlsx['sha256'] != payload['workbook_sha256']
    with ZipFile(shared['path']) as archive:
        assert set(archive.namelist()) == {item['name'] for item in shared['artifacts']} | {
            'source-inventory.json', 'version-inventory.json'}
        versions = json.loads(archive.read('version-inventory.json'))
        assert {item['kind'] for item in versions['versions']} == {'pdf', 'xlsx'}
        assert all(CANARY.encode() not in archive.read(name) for name in archive.namelist())
        assert all(b'C:\\Users' not in archive.read(name) for name in archive.namelist())
    with ZipFile(xlsx['path']) as archive:
        assert all(CANARY.encode() not in archive.read(name) for name in archive.namelist())
        generated_comments = [archive.read(name) for name in archive.namelist() if name.startswith('xl/comments/')]
        assert generated_comments and any(b'https://example.org/issuer/annual-report' in part for part in generated_comments)
        assert all(b'PUBLIC_ENTITY_' in part for part in generated_comments)
    workbook = load_workbook(xlsx['path'], data_only=False)
    assert all(sheet.sheet_state == 'visible' for sheet in workbook)
    assert workbook['Summary']['E8'].data_type == 'f'
    workbook.close()
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(next(
        item['path'] for item in shared['artifacts'] if item['kind'] == 'pdf')).pages)
    assert ('Estratto condivisibile del modello' if language == 'it' else 'Shareable model extract') in text
    assert "'Summary'!E8" in text
    assert ('not the complete research dossier' in text) if language == 'en' else ('non è il dossier di ricerca completo' in text)
    assert CANARY not in text
    assert Path(payload['path']).read_bytes() == original_bytes
    # Recovery reuses the sealed shared generation; no recomputation or AI is needed.
    from bellomberg.valuation import trade_idea_model
    monkeypatch.setattr(trade_idea_model, 'prepare_shareable_model', lambda *_: pytest.fail('Shared model regenerated on recovery'))
    again = build_shareable_package(run, result, {'SYNTH-EXT': payload},
        output_dir=tmp_path/'share', model_roots=[tmp_path], language=language, private_terms=[CANARY])
    assert again == shared


def test_shared_preview_blocks_missing_excel_without_creating_files(tmp_path):
    from bellomberg.reporting.trade_idea_share import preview_shareable_package, build_shareable_package
    preview = preview_shareable_package(run_record(), research_result(), model_roots=[tmp_path])
    assert preview['status'] == 'blocked'
    assert 'Excel' in ' '.join(preview['reasons'])
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match='blocked'):
        build_shareable_package(run_record(), research_result(), output_dir=tmp_path/'shared', model_roots=[tmp_path])
    assert not (tmp_path/'shared').exists()


def test_shared_projection_blocks_unqualified_public_provenance(public_model, tmp_path):
    from bellomberg.reporting.trade_idea_share import preview_shareable_package
    payload = public_model
    payload['preparation']['review_basis']['dossier']['documents'][0]['url'] = 'file:///C:/private/source.pdf'
    _reseal(payload)
    result = bind_workbook(research_result(), payload)
    preview = preview_shareable_package(run_record(), result, {'SYNTH-EXT': payload}, model_roots=[tmp_path])
    assert preview['status'] == 'blocked'
    assert preview['reasons']


@pytest.mark.parametrize('channel', ['cell', 'comment', 'hidden', 'defined_name', 'core_property',
                                     'application_property', 'custom_property', 'external_link', 'active_formula'])
def test_workbook_privacy_scan_covers_nonvisible_and_active_channels(tmp_path, channel):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.workbook.defined_name import DefinedName
    from bellomberg.reporting.trade_idea_share import inspect_shared_workbook
    workbook = Workbook()
    workbook.active['A1'] = '=1+1'
    if channel == 'cell': workbook.active['B1'] = CANARY
    elif channel == 'comment': workbook.active['A1'].comment = Comment(CANARY, 'Synthetic reviewer')
    elif channel == 'hidden':
        sheet = workbook.create_sheet('Hidden'); sheet.sheet_state = 'hidden'; sheet['A1'] = CANARY
    elif channel == 'defined_name': workbook.defined_names.add(DefinedName(CANARY, attr_text="'Sheet'!$A$1"))
    elif channel == 'core_property': workbook.properties.description = CANARY
    elif channel == 'external_link': workbook.active['B1'].hyperlink = 'https://example.org/private-link'
    elif channel == 'active_formula': workbook.active['B1'] = '=WEBSERVICE("https://example.org/public")'
    path = tmp_path/'scan.xlsx'; workbook.save(path); workbook.close()
    if channel == 'application_property':
        _rewrite_part(path, 'docProps/app.xml', lambda value: value.replace(b'</Properties>', ('<Company>'+CANARY+'</Company></Properties>').encode()))
    elif channel == 'custom_property':
        _rewrite_part(path, 'docProps/custom.xml', lambda _: ('<Properties><property>'+CANARY+'</property></Properties>').encode())
    with pytest.raises(ValueError):
        inspect_shared_workbook(path, private_terms=[CANARY], allowed_urls=['https://example.org/public'])


@pytest.mark.parametrize('channel', ['metadata', 'outline', 'annotation', 'xml_metadata', 'attachment'])
def test_pdf_privacy_scan_covers_metadata_and_nonpage_channels(tmp_path, channel):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject, ArrayObject, FloatObject, DecodedStreamObject
    from bellomberg.reporting.trade_idea_share import inspect_shared_pdf
    writer = PdfWriter(); writer.add_blank_page(width=595, height=842)
    if channel == 'metadata': writer.add_metadata({'/Subject': CANARY})
    elif channel == 'outline': writer.add_outline_item(CANARY, 0)
    elif channel == 'annotation':
        writer.add_annotation(0, DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
            NameObject('/Subtype'): NameObject('/Text'), NameObject('/Contents'): TextStringObject(CANARY),
            NameObject('/Rect'): ArrayObject([FloatObject(n) for n in (0, 0, 30, 30)])}))
    elif channel == 'xml_metadata':
        stream = DecodedStreamObject(); stream.set_data(('<metadata>'+CANARY+'</metadata>').encode())
        stream[NameObject('/Type')] = NameObject('/Metadata'); stream[NameObject('/Subtype')] = NameObject('/XML')
        writer._root_object[NameObject('/Metadata')] = writer._add_object(stream)
    elif channel == 'attachment': writer.add_attachment('private.txt', CANARY.encode())
    path = tmp_path/'scan.pdf'; writer.write(path)
    with pytest.raises(ValueError): inspect_shared_pdf(path, private_terms=[CANARY])


def test_private_zip_keeps_the_exact_sealed_pdf_and_excel(public_model, tmp_path):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery
    from bellomberg.reporting.trade_idea_share import build_private_package
    result = bind_workbook(research_result('rejected'), public_model)
    manifest = prepare_trade_idea_delivery(run_record(), result, {'SYNTH-EXT': public_model},
        output_dir=tmp_path/'delivery', model_roots=[tmp_path], language='en')
    package = build_private_package(manifest, output_path=tmp_path/'private.zip')
    with ZipFile(package['path']) as archive:
        assert set(archive.namelist()) == {Path(path).name for path in manifest['attachments']} | {'inventory.json'}
        for artifact in manifest['artifacts']:
            assert archive.read(Path(artifact['path']).name) == Path(artifact['path']).read_bytes()
            assert sha256(archive.read(Path(artifact['path']).name)).hexdigest() == artifact['sha256']


@pytest.mark.parametrize('language', ['it', 'en'])
def test_actual_reported_property_shared_pair_rebuilds_primary_nav_and_excludes_private_channels(tmp_path, language):
    """Actual archived WDP statements; illustrative sensitivities, no recommendation."""
    from openpyxl import load_workbook
    from openpyxl.comments import Comment
    from openpyxl.workbook.defined_name import DefinedName
    from pypdf import PdfReader
    from bellomberg.reporting.trade_idea_share import preview_shareable_package, build_shareable_package, verify_shareable_package
    from bellomberg.valuation.trade_idea_model import candidate_model_usability
    configured = os.environ.get('BELLOMBERG_TEST_WDP_SOURCE_MODEL')
    if not configured:
        pytest.skip('External WDP model fixture not configured: BELLOMBERG_TEST_WDP_SOURCE_MODEL')
    source = Path(configured)
    if not source.is_file():
        pytest.skip('Configured external actual-primary WDP model payload absent')
    payload = json.loads(source.read_text(encoding='utf8'))
    original = Path(payload.get('path') or str(source).removesuffix('.payload.json')+'.xlsx')
    if not original.is_file():
        pytest.skip('External actual-primary reported property workbook absent')
    payload['path'] = str(original)
    original_sha = sha256(original.read_bytes()).hexdigest()
    private = tmp_path/original.name
    private.write_bytes(original.read_bytes())
    payload['path'] = str(private)
    payload['_thesis_saved'] = {'thesis_id': 1}  # Isolated publication receipt, no personal DB.
    workbook = load_workbook(private)
    workbook.properties.creator = CANARY
    workbook.properties.description = CANARY
    workbook['Summary']['Z1'] = CANARY
    workbook['Summary']['E8'].comment = Comment(CANARY, CANARY)
    workbook.create_sheet('Private context').sheet_state = 'hidden'
    workbook['Private context']['A1'] = CANARY
    workbook.defined_names.add(DefinedName(CANARY, attr_text="'Summary'!$Z$1"))
    workbook.save(private); workbook.close()
    payload['preparation']['provenance']['pm_note'] = CANARY
    _reseal(payload)
    private_sha = sha256(private.read_bytes()).hexdigest()
    run, result = run_record(), bind_workbook(research_result('rejected'), payload)
    run['ticker'] = result['ticker'] = payload['ticker']
    run.update(pm_view=CANARY, portfolio={'position': CANARY})
    result['summary'] += CANARY
    result['dossier'][0]['paragraphs'].append(CANARY)
    models = {payload['ticker']: payload}
    preview = preview_shareable_package(run,result,models,model_roots=[tmp_path])
    assert preview['status'] == 'ready', preview['reasons']
    assert CANARY not in json.dumps(preview)
    manifest = build_shareable_package(run,result,models,output_dir=tmp_path/'share',
        model_roots=[tmp_path],language=language,private_terms=[CANARY])
    assert verify_shareable_package(manifest,private_terms=[CANARY])
    artifacts = {item['kind']:item for item in manifest['artifacts']}
    assert set(artifacts) == {'pdf','xlsx'}
    shared_path = Path(artifacts['xlsx']['path'])
    shared = json.loads(shared_path.with_suffix('.payload.json').read_text(encoding='utf8'))
    assert candidate_model_usability(shared)['usable']
    assert shared['generation_id'] != payload['generation_id']
    assert shared['snapshot_id'] != payload['snapshot_id']
    assert shared['workbook_sha256'] != payload['workbook_sha256']
    assert shared['valuation_date'] == '2026-06-30'
    for scenario in ('bear','base','bull'):
        assert shared['fair_value_'+scenario] == payload['fair_value_'+scenario]
    with ZipFile(shared_path) as archive:
        assert all(CANARY.encode() not in archive.read(name) for name in archive.namelist())
    with ZipFile(manifest['path']) as archive:
        assert set(archive.namelist()) == {item['name'] for item in artifacts.values()} | {'source-inventory.json','version-inventory.json'}
        for name in archive.namelist():
            if not name.endswith('.xlsx'):
                assert CANARY.encode() not in archive.read(name)
        sources = json.loads(archive.read('source-inventory.json'))
        assert any(row['url'] == 'https://wdp.eu/en/actions/site-module/asset-download/download?id=336401' for row in sources['sources'])
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(artifacts['pdf']['path']).pages)
    assert ('Estratto condivisibile del modello' if language=='it' else 'Shareable model extract') in text
    assert CANARY not in text
    assert "'base'!B5" in text
    assert ('Sensibilita NTA' if language=='it' else 'NTA sensitivity') in text
    assert 'WACC' not in text and 'terminal EBIT' not in text
    assert 'ifrs_parent_equity_reconstructed' not in text
    assert sha256(private.read_bytes()).hexdigest() == private_sha
    assert sha256(original.read_bytes()).hexdigest() == original_sha
    (tmp_path/'reported-property-share-receipt.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8')
