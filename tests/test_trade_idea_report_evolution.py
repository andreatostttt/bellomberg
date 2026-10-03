"""Decision dossier and complete-package checks with isolated synthetic models."""
from pathlib import Path

import pytest

from trade_idea_fixtures import research_result, run_record, bind_workbook
from test_documented_dcf_contract import contract_tools, _call
from test_valuation_snapshot_persistence import db
from test_trade_idea_delivery import smtp


@pytest.mark.parametrize('language', ['it', 'en'])
def test_commodity_observations_are_visible_in_research_and_model_extract(tmp_path, language):
    """Fictional observations check actual PDF content, locators and limits."""
    from pypdf import PdfReader
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report, build_model_extract
    packet = {'status': 'complete', 'intrinsic_value_applicable': False, 'exhibits': [
        {'id': 'commodity_accounting', 'title': 'Reported NAV accounting', 'kind': 'table',
         'columns': ['Measure', 'Value', 'Unit'], 'rows': [['accounting_nav_per_share', 83.137, 'USD per entitled share']],
         'cell_refs': [[None, "'Summary'!D11", None]], 'unit': 'mixed', 'currency': None,
         'period': '2026-06-30', 'scenario': 'observed'},
        {'id': 'commodity_market_costs', 'title': 'Dated market and historical cost observations', 'kind': 'table',
         'columns': ['Measure', 'Value', 'Unit', 'Period'],
         'rows': [['historical_annualized_expense_ratio', .0037, 'annualized historical fraction', '2026-01-01/2026-06-30'],
                  ['median_spread_ratio', .0001, '30-day median fraction', '2026-09-25'],
                  ['daily_volume', 12345, 'quoted units', '2026-09-25']],
         'cell_refs': [[None, "'Summary'!D17", None, None], [None, "'Summary'!D18", None, None], [None, "'Summary'!D19", None, None]],
         'unit': 'mixed', 'currency': None, 'period': 'separately_dated', 'scenario': 'observed'}],
        'unavailable_exhibits': [{'id': 'investment_leverage', 'reason': 'Investment leverage is not separately established.'},
                                {'id': 'future_expenses', 'reason': 'Future costs are unavailable.'}]}
    model = {'ticker': 'SYNTH-EXT', 'status': 'ready', 'method': 'exposure_analysis', 'model_kind': 'exposure',
             'snapshot_id': 'synthetic-snapshot', 'generation_id': 'synthetic-generation',
             'workbook_sha256': 'a'*64, 'valuation_date': '2026-06-30', 'information_cutoff': '2026-09-28',
             'interpretation': 'Explicitly fictional commodity-trust observations for a software test.',
             'model_values': {}, 'model_exhibits': packet}
    research = build_trade_idea_report(run_record(), research_result('rejected'), valuations=[model],
                                      output_path=tmp_path/'research.pdf', language=language)
    extract = build_model_extract('SYNTH-EXT', [model], [], output_path=tmp_path/'extract.pdf', language=language)
    for artifact in (research, extract):
        text = ' '.join('\n'.join(page.extract_text() or '' for page in PdfReader(artifact['path']).pages).split())
        for locator in ("'Summary'!D11", "'Summary'!D17", "'Summary'!D18", "'Summary'!D19"):
            assert locator in text
        assert '83.137' in text and '12,345' in text
        assert '0.37%' in text and '0.01%' in text
        assert '2026-06-30' in text and '2026-09-25' in text
        assert 'n.d.' in text
        assert ('NAV contabile storico per quota' if language == 'it' else 'Historical accounting NAV per share') in text
        assert ('Leva d\'investimento' if language == 'it' else 'Investment leverage') in text
        assert ('Costi futuri' if language == 'it' else 'Future expenses') in text
        assert ('non e una stima di fair value' if language == 'it' else 'not a fair-value estimate') in text
        if language == 'it':
            assert 'Reported NAV accounting' not in text and 'annualized historical fraction' not in text


def test_pdf_without_economic_workbook_is_partial_package(tmp_path):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, verify_trade_idea_manifest
    manifest = prepare_trade_idea_delivery(run_record(), research_result('rejected'), {},
        [{'ticker': 'SYNTH-EXT', 'reason': 'Missing audited cash-flow input'}],
        output_dir=tmp_path, model_roots=[tmp_path], language='en')
    assert manifest['pdf_quality']['status'] == 'ready'
    assert manifest['artifact_status'] == 'partial'
    assert manifest['complete_package_status'] == 'incomplete'
    assert 'Excel' in ' '.join(manifest['complete_package_reasons'])
    assert verify_trade_idea_manifest(manifest)
    with pytest.raises(ValueError, match='complete'):
        verify_trade_idea_manifest(manifest, require_complete=True)


def test_diagnostic_only_mime_calls_it_incomplete(tmp_path, smtp):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, send_trade_idea_delivery
    manifest = prepare_trade_idea_delivery(run_record(), research_result('rejected'),
        output_dir=tmp_path, model_roots=[tmp_path], language='en')
    assert send_trade_idea_delivery(manifest)['status'] == 'accepted'
    message = smtp[0][0]
    assert 'Incomplete package' in str(message['Subject'])
    assert 'Economic Excel workbook' in message.get_body(preferencelist=('plain',)).get_content()


def test_every_reviewed_generation_is_included_or_explicitly_incomplete(contract_tools, tmp_path):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    result = bind_workbook(research_result('rejected'), workbook)
    result['valuation_refs'].append(dict(result['valuation_refs'][0], generation_id='missing-reviewed-generation'))
    manifest = prepare_trade_idea_delivery(run_record(), result, {'SYNTH-EXT': workbook},
        output_dir=tmp_path/'delivery', model_roots=[tmp_path], language='en')
    assert any(a['kind'] == 'xlsx' for a in manifest['artifacts'])
    assert manifest['complete_package_status'] == 'incomplete'
    assert any(row.get('generation_id') == 'missing-reviewed-generation' and row['status'] == 'analysis_model_missing'
               for row in manifest['valuations'])


def test_rejected_idea_with_valid_excel_can_be_complete(contract_tools, tmp_path):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, verify_trade_idea_manifest
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    manifest = prepare_trade_idea_delivery(run_record(), bind_workbook(research_result('rejected'), workbook),
        {'SYNTH-EXT': workbook}, output_dir=tmp_path/'delivery', model_roots=[tmp_path], language='en')
    assert manifest['judgment'] == 'rejected'
    assert manifest['complete_package_status'] == 'ready'
    assert verify_trade_idea_manifest(manifest, require_complete=True)
    artifact = next(a for a in manifest['artifacts'] if a['kind'] == 'xlsx')
    assert artifact['generation_id'] == workbook['generation_id']
    assert artifact['snapshot_id'] == workbook['snapshot_id']


def test_all_negative_bar_values_keep_a_visible_zero_axis():
    from bellomberg.reporting.trade_idea_report import _chart, _register_fonts
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    font, _, _ = _register_fonts()
    table = {'columns':['Period','Cash flow'], 'rows':[['A','-30'],['B','-10']]}
    drawing = _chart({'tables':[table]}, {'table_index':0,'value_columns':[1],
        'label_column':0,'kind':'bar'}, 495, font)
    graph = next(item for item in drawing.contents if isinstance(item, VerticalBarChart))
    assert graph.data == [[-30., -10.]]
    assert graph.valueAxis.valueMin == -30.
    assert graph.valueAxis.valueMax == 0.


def test_workbook_reference_compaction_never_fills_a_gap():
    from bellomberg.reporting.trade_idea_report import _compact_refs
    refs = [["'base'!D8", "'base'!E8", "'base'!G8", "'base'!H8", "'base'!D26"],
            ["'base'!D8", "model_named_range", None]]
    compact = _compact_refs(refs)
    assert "'base'!D8:E8" in compact
    assert "'base'!G8:H8" in compact
    assert "'base'!D8:H8" not in compact
    assert "'base'!D26" in compact and 'model_named_range' in compact


def test_canonical_model_notices_are_localized_without_raw_statuses():
    from reportlab.lib.styles import ParagraphStyle
    from bellomberg.reporting.trade_idea_report import _valuation_basis, _model_gaps
    basis = 'Documented scenario valuation at opening balances/quotation cutoff; regenerate to change assumptions.'
    assert _valuation_basis(basis, 'it').startswith('Scenari documentati')
    assert _valuation_basis(basis, 'en') == basis
    accepted_prose = 'Original analyst prose that is not a canonical engine notice.'
    assert _valuation_basis(accepted_prose, 'it') == accepted_prose
    report = {'unavailable_exhibits': [{'id': 'implicit_expectations',
        'reason': 'Inverse model unavailable: current dated quotation is not usable; data_missing'}]}
    story = _model_gaps(report, {'small': ParagraphStyle('proof')}, 'it')
    text = story[0].getPlainText()
    assert 'Modello inverso non disponibile' in text
    assert 'data_missing' not in text and 'Inverse model' not in text


@pytest.mark.parametrize('language', ['it', 'en'])
def test_pdf_navigation_fonts_and_long_cover_fields(tmp_path, language):
    from pypdf import PdfReader
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    result, run = research_result('rejected'), run_record()
    result['summary'] = ('A long sourced cash conversion thesis with operating uncertainty. ' * 35).strip()
    result['risks'] = ['The customer programme can consume cash before any revenues are recognized. ' * 8]
    result['catalysts'] = ['Published annual results with reconciled operating cash flows. ' * 8]
    result['invalidation'] = ['Counterevidence on margins, customer renewals and net investment. ' * 8]
    run['identity']['name'] = 'Società di Componenti Industriali e Tecnologie di Precisione - International Holding Group'
    report = build_trade_idea_report(run, result, output_path=tmp_path/f'{language}.pdf', language=language)
    reader = PdfReader(report['path'])
    all_text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    assert result['summary'] in all_text.replace('\n', ' ')
    assert ('Segue nel dossier integrale.' if language == 'it' else 'Continued in the complete dossier.') in all_text
    assert reader.outline
    links = [annotation.get_object() for page in reader.pages for annotation in page.get('/Annots', [])]
    assert len([item for item in links if item.get('/Subtype') == '/Link' and item.get('/Dest')]) >= len(result['dossier'])
    embedded = []
    for page in reader.pages:
        for font_ref in page.get('/Resources', {}).get('/Font', {}).values():
            font = font_ref.get_object()
            descriptor = font.get('/FontDescriptor')
            if descriptor:
                descriptor = descriptor.get_object()
                embedded.append(any(key in descriptor for key in ('/FontFile', '/FontFile2', '/FontFile3')))
    assert embedded and all(embedded)
    fitz = pytest.importorskip('fitz')
    with fitz.open(report['path']) as document:
        for page in document:
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        x0, y0, x1, y1 = span['bbox']
                        assert -1 <= x0 < x1 <= page.rect.width+1
                        assert -1 <= y0 < y1 <= page.rect.height+1


def _registered_exposure(tmp_path):
    from copy import deepcopy
    from test_trade_idea_exposure import sourced_exposure
    from bellomberg.valuation.trade_idea_model import prepare
    q, plan = sourced_exposure(tmp_path)
    payload = prepare(q, lambda *_: deepcopy(plan), tmp_path/'model')
    payload['_thesis_saved'] = {'thesis_id': 1}  # Synthetic publication receipt; no DB involved.
    return payload


@pytest.mark.parametrize('language', ['it', 'en'])
def test_observational_excel_completes_package_without_a_fair_value(tmp_path, language):
    from pypdf import PdfReader
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, verify_trade_idea_manifest
    payload = _registered_exposure(tmp_path)
    run, result = run_record(), bind_workbook(research_result('rejected'), payload)
    run['ticker'] = result['ticker'] = payload['ticker']
    run['identity']['name'] = 'Synthetic observed fund - reporting wiring fixture'
    manifest = prepare_trade_idea_delivery(run, result, {payload['ticker']: payload},
        output_dir=tmp_path/'delivery', model_roots=[tmp_path], language=language)
    assert manifest['complete_package_status'] == 'ready', manifest['complete_package_reasons']
    assert manifest['pdf_quality']['analytical_pages'] >= 10
    assert manifest['pdf_quality']['analytical_words'] >= 4000
    assert verify_trade_idea_manifest(manifest, require_complete=True)
    row = manifest['valuations'][0]
    assert row['model_kind'] == 'exposure' and row['intrinsic_value_applicable'] is False
    assert row['analysis_payload_sha256']
    packet = row['model_exhibits']
    assert {exhibit['id'] for exhibit in packet['exhibits']} == {'exposure_metrics', 'holdings'}
    assert 'fair_value_base' not in packet['headline_values']
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(manifest['attachments'][0]).pages)
    assert ('Non applicabile' if language == 'it' else 'Not applicable') in text
    assert ('Esposizione e concentrazione osservate' if language == 'it' else 'Observed exposure and concentration') in text
    assert "'Exposure'!D8" in text and "'Summary'!D15" in text


@pytest.mark.parametrize('language', ['it','en'])
def test_observational_excel_cannot_complete_a_short_research_dossier(tmp_path, language):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, verify_trade_idea_manifest
    payload = _registered_exposure(tmp_path)
    run, result = run_record(), bind_workbook(research_result('rejected'),payload)
    run['ticker'] = result['ticker'] = payload['ticker']
    for section in result['dossier']:
        section['paragraphs'] = ['Brief observed exposure statement; substantive research is missing.']
    manifest = prepare_trade_idea_delivery(run,result,{payload['ticker']:payload},
        output_dir=tmp_path/'delivery',model_roots=[tmp_path],language=language)
    assert manifest['model_status'] == 'ready'
    assert manifest['pdf_quality']['status'] == 'partial'
    assert manifest['pdf_quality']['analytical_pages'] < 10
    assert manifest['complete_package_status'] == 'incomplete'
    with pytest.raises(ValueError,match='not complete'):
        verify_trade_idea_manifest(manifest,require_complete=True)


@pytest.mark.parametrize('fault', ['intrinsic_false', 'fair_value', 'missing_contract', 'wrong_date', 'sidecar_observation', 'modified_excel', 'corrupt_excel'])
def test_observational_gate_does_not_relax_intrinsic_or_accept_an_unbound_exposure(tmp_path, fault):
    from copy import deepcopy
    import json
    from bellomberg.reporting.valuation_delivery import build_manifest
    payload = _registered_exposure(tmp_path)
    path = Path(payload['path'])
    if fault == 'intrinsic_false':
        payload['method'] = 'corporate_dcf'
    elif fault == 'fair_value':
        payload['fair_value_base'] = 100.
    elif fault == 'missing_contract':
        payload['analysis_usability'].pop('contract')
    elif fault == 'wrong_date':
        payload['valuation_date'] = '2026-09-09'
    elif fault == 'sidecar_observation':
        sidecar = json.loads(path.with_suffix('.payload.json').read_text(encoding='utf-8'))
        sidecar['exposure_analysis'] = deepcopy(sidecar['exposure_analysis'])
        sidecar['exposure_analysis']['gross_exposure'] = .9
        path.with_suffix('.payload.json').write_text(json.dumps(sidecar), encoding='utf-8')
    elif fault == 'modified_excel':
        path.write_bytes(path.read_bytes() + b'changed observations')
    elif fault == 'corrupt_excel':
        from hashlib import sha256
        path.write_bytes(b'Invalid workbook with a consistent byte hash')
        payload['workbook_sha256'] = sha256(path.read_bytes()).hexdigest()
        sidecar = json.loads(path.with_suffix('.payload.json').read_text(encoding='utf-8'))
        sidecar['workbook_sha256'] = payload['workbook_sha256']
        path.with_suffix('.payload.json').write_text(json.dumps(sidecar), encoding='utf-8')
    manifest = build_manifest({payload['ticker']: payload}, roots=[tmp_path])
    assert manifest['attachments'] == []
    assert manifest['valuations'][0]['status'] != 'ready'
    assert manifest['valuations'][0]['reason']


def test_observational_receipt_recovery_keeps_exact_acquisition_and_rejects_changed_sidecar(tmp_path):
    from hashlib import sha256
    import json
    from bellomberg.reporting.valuation_delivery import build_manifest, recover_manifest, save_manifest
    payload = _registered_exposure(tmp_path)
    original = build_manifest({payload['ticker']: payload}, roots=[tmp_path])
    original.update(memo_id=42, memo_sha256=sha256(b'Synthetic original observational memo').hexdigest())
    receipt = tmp_path/'receipts'/'bellomberg_20260928_0800_valuations.json'
    save_manifest(receipt, original)
    original_bytes = receipt.read_bytes()
    recovery = recover_manifest(42, receipts_dir=receipt.parent, roots=[tmp_path], memo_sha256=original['memo_sha256'])
    assert recovery['attachments'] == [payload['path']]
    assert recovery['valuations'][0]['analysis_payload_sha256'] == original['valuations'][0]['analysis_payload_sha256']
    sidecar_path = Path(payload['path']).with_suffix('.payload.json')
    sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
    sidecar['exposure_analysis']['gross_exposure'] = .9
    sidecar_path.write_text(json.dumps(sidecar), encoding='utf-8')
    changed = recover_manifest(42, receipts_dir=receipt.parent, roots=[tmp_path], memo_sha256=original['memo_sha256'])
    assert changed['attachments'] == [] and changed['issues']
    assert receipt.read_bytes() == original_bytes
