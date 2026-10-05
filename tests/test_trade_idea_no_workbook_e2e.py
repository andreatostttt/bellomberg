"""Ordinary Trade Idea research, native committee and delivery; no Excel calls.

Provider/HTTP/SMTP transports are frozen. Financial guards, source ingestion,
checkpoint storage, real PDF rendering and destination routing are not replaced.
Run only with tools/testing/offline_pytest.py under Python -I.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.company_source_research import ResearchSession
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.valuation import trade_idea_model as model
from test_company_source_research import FrozenTransport, html, SITE, PAGE
from _smtp_cattura import smtp
from test_trade_idea_economic import IDENTITY, providers_for
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_pm_sources import TEXT
from test_trade_idea_source_research import frozen_clock
from test_trade_idea_store import db_path, migrated, store
from test_sector_analysis import DAY
from trade_idea_fixtures import research_result


FORBIDDEN_TOOLS = {
    'get_valuation', 'get_candidate_model_inputs', 'submit_candidate_model_plan',
    'review_candidate_model', 'read_candidate_model_consultation',
}

RESEARCH_VALUATION_PARAGRAPHS = [
    'The synthetic committee evaluates the business from the admitted annual statement and the independent desk reports. '
    'It asks whether customer qualification supports returns through renewals, whether cash collection confirms revenue, '
    'and whether reinvestment merely preserves the existing franchise or creates additional economic capacity. These are '
    'research questions with explicit evidentiary limits. This frozen engineering fixture does not estimate an intrinsic '
    'value or claim that qualitative analysis removes uncertainty about price. It preserves the distinction between a '
    'company that deserves further attention and an allocation that meets the portfolio mandate. [evidence: archive]',
    'Analyst consensus is unavailable from the frozen provider. The report therefore contains no consensus target, '
    'analyst count, target range or inferred upside. An observed share price remains a market observation, never a substitute '
    'for the missing target. The committee can still reach a documented judgment about competitive position and downside '
    'mechanisms. If consensus later becomes available, it would be compared with the same listing and currency, using its '
    'actual provider and data date. Its arrival would add a reference point rather than settle the investment debate. [evidence: price]',
    'The central assumption is that installed programs retain customers while replacement decisions remain contestable. '
    'This is an independent hypothesis, not management guidance. Evidence of concessions at renewals, unrecovered engineering '
    'spending or weakening collection would challenge it. Evidence of customer acceptance with adequate returns after '
    'development investment would support it. The report records those observable conditions so a later analyst can explain '
    'what changed. It does not convert the assumption into a mandatory forecast schedule or infer accounting balances that '
    'the admitted materials do not reconcile. [evidence: archive]',
    'The disagreement with an optimistic market narrative concerns value capture rather than the existence of switching '
    'friction. A supplier can be difficult to replace within an active program and still face strong bargaining pressure '
    'when a platform is redesigned. The committee separates those horizons and examines who finances development, tooling '
    'and working capital. It treats a favorable demand description as management commentary until the statements and '
    'customer evidence support cash returns. Missing commentary dates remain a source limitation and cannot be repaired by '
    'assuming that an undated page describes current trading. [evidence: archive]',
    'No workbook is constructed, reviewed, attached or required in this research mode. The shared dossier and thesis '
    'references bind the independent reports, Red Team objections and final replies to the same evidence. The deliverable '
    'is the research memo and its PDF, with source references and declared gaps. A rejection can be a complete analytical '
    'conclusion; a favorable conclusion still needs the ordinary quote, cash, mandate, risk and sizing checks before a '
    'proposal reaches Decisions. A transport failure, empty response or damaged checkpoint remains a technical failure '
    'rather than being described as a business judgment. [evidence: archive]',
]

RESEARCH_SCENARIO_PARAGRAPH = (
    'The bear, base and bull cases describe distinct operating mechanisms and the evidence that would distinguish them. '
    'They are not probability weighted price targets. The bear case combines concessions with cash absorption; the base '
    'case preserves existing programs with continuing reinvestment; the bull case requires demonstrated gains from '
    'engineering content and reuse. An unavailable analyst consensus supplies no missing numerical anchor. The monitoring '
    'plan instead records renewal terms, collection, inventory and investment returns so the thesis can be updated from '
    'observations. No DCF calculation or synthetic spreadsheet is hidden in these scenarios. [evidence: archive]')


def _provider_round(call):
    context = str(call['messages'][0].get('content'))
    for number, marker in ((0, 'R0: independently collect'),
                           (1, 'R1: write your independent domain analysis'),
                           (2, 'R2: answer the material')):
        if marker in context or 'Round ' + str(number) + '.' in context:
            return number
    return None


def _native_rows(database, run_id):
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        return {
            'run': dict(conn.execute('SELECT * FROM trade_idea_runs WHERE id=?', (run_id,)).fetchone()),
            'costs': [dict(row) for row in conn.execute(
                'SELECT * FROM trade_idea_costs WHERE run_id=? ORDER BY request_id', (run_id,))],
        }


@pytest.fixture
def no_workbook_case(migrated, tmp_path, monkeypatch, smtp):
    from bellomberg.agents import consigliere_multi, chat_tools
    from bellomberg.agents.specialists import base
    from bellomberg.core import current_facts, llm_client, llm_pricing, mandato_pm, paths
    from bellomberg.core.llm_client import Usage
    from bellomberg.storage import classificazione
    from bellomberg.valuation import dcf_engine, input_preparation, preparation_ai, preparation_service

    prohibited, provider_calls, tool_calls, boards = Counter(), [], [], []

    def forbidden(label):
        def denied(*_args, **_kwargs):
            prohibited[label] += 1
            pytest.fail('No-workbook research invoked ' + label)
        return denied

    for owner, names in (
        (trade_idea, ('bind_trade_idea_preparer', '_complete_saved_model_authoring',
                     '_build_fundamentals_candidate', '_compile_fundamentals_candidate')),
        (model, ('build_from_plan', 'prepare', 'revise')),
        (dcf_engine, ('generate_valuation', '_generate_valuation_legacy')),
        (preparation_service, ('prepare_and_generate', 'collect_and_prepare')),
        (input_preparation, ('prepare_method_inputs',)),
        (preparation_ai, ('configured_proposer',)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden(owner.__name__ + '.' + name))

    providers = providers_for()
    original_profile = providers['profile']

    def profile(*args, **kwargs):
        result = original_profile(*args, **kwargs)
        result['data']['info']['website'] = SITE
        return result

    providers['profile'] = profile
    monkeypatch.setattr(model, '_free_providers', lambda *_args: providers)
    priced = _priced_request(budget='30')
    admission = model.research_admission(IDENTITY['ticker'], IDENTITY, DAY,
        archive_root=tmp_path, providers=providers, analysis_mode=RESEARCH_ANALYSIS_MODE)
    assert admission['status'] == 'research_required', admission
    assert admission['source_report']['documents'] == []
    authorization = {'accepted': True, 'source_fingerprint': admission['fingerprint'],
                     'activities': ['committee'], 'max_revision_rounds': 0}

    registry_path = tmp_path / 'veicoli.json'
    registry_path.write_text(json.dumps({
        'SYNTH-EXT': {'tipo': 'operating', 'provenienza': 'dichiarato',
                      'classe_size': 'single', 'settore_policy': 'synthetic'},
        'SYNTH-PEER': {'tipo': 'etf', 'provenienza': 'dichiarato',
                       'classe_size': 'veicolo'}}), encoding='utf-8')
    registry = classificazione.carica_veicoli(str(registry_path))
    assert registry['origine'] not in ('assente', 'illeggibile')
    monkeypatch.setattr(classificazione, 'carica_veicoli', lambda *_a, **_k: registry)
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('SYNTH-EXT',1,100,'EUR',1)")
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('SYNTH-PEER',99,100,'EUR',1)")
        conn.execute('UPDATE cash_state SET balance_cents=100000 WHERE singleton_id=1')
    current = store(migrated)
    request = {**priced, 'analysis_mode': RESEARCH_ANALYSIS_MODE,
        'ticker': IDENTITY['ticker'], 'company_name': IDENTITY['name'],
        'exchange': IDENTITY['exchange'], 'currency': IDENTITY['currency'], 'language': 'en',
        'view_text': 'Switching costs make this business immune to a downturn.',
        'source_qualification': admission, 'authorization': authorization}

    url, missing_url = SITE + '/investors/report.html', SITE + '/investors/undated.html'
    raw = html(TEXT)
    document_id = sha256(raw).hexdigest()
    transport = FrozenTransport({
        '/investors/': (b'<html><body><a href="report.html">Annual financial statements</a>'
                       b'<a href="undated.html">Management commentary</a></body></html>', 'text/html'),
        '/investors/report.html': (raw, 'text/html'),
        '/investors/undated.html': (html('Synthetic issuer management commentary. '
            'Revenue growth remains uncertain. No publication date or statement period is supplied.'), 'text/html'),
    })
    monkeypatch.setattr(paths, 'MODELS_DIR', tmp_path / 'models')
    monkeypatch.setattr(paths, 'REPORT_DIR', tmp_path)
    monkeypatch.setattr(current_facts, 'current_facts_block', lambda: 'Frozen offline company context')
    mandate = mandato_pm.profilo_esempio()
    monkeypatch.setattr(mandato_pm, 'carica', lambda: mandate)
    monkeypatch.setattr(llm_pricing, '_fx_usd_to_eur', lambda: (0.9, 'frozen_test_fx'))
    monkeypatch.setattr(chat_tools, '_compatta_portfolio_live', lambda value: deepcopy(value))
    state = {'judgment': 'rejected', 'stop_after': None, 'source_gaps': True, 'capo_calls': 0,
             'truncate': None, 'no_reply': None, 'provider_failure': None, 'capo_failure': None}

    def dossier_payload():
        result = research_result(state['judgment'])
        for key in ('run_id', 'run_type', 'pm_view', 'destination'):
            result.pop(key, None)
        result['valuation_refs'] = []
        result['model_review'] = None
        result['evidence'] = [
            {'id': 'archive', 'source': '[src: get_filing_changes] frozen issuer filing observations',
             'as_of': DAY, 'summary': 'Revenue 100 EUR million in the frozen annual statement.',
             'url': url},
            {'id': 'price', 'source': '[src: get_price_live] frozen observed daily close',
             'as_of': DAY, 'summary': 'Price 100 EUR.', 'url': None},
            {'id': 'financials', 'source': '[src: get_fundamentals] frozen annual financial observations',
             'as_of': DAY, 'summary': 'Revenue 100 EUR million.', 'url': url},
        ]
        result['data_gaps'] = ['Analyst consensus is unavailable; no analyst target or AI fair value is inferred.']
        if state['source_gaps']:
            result['data_gaps'].append('Undated management commentary could not be admitted; the dated annual statement remains available.')
        for section in result['dossier']:
            section['evidence_ids'] = ['archive', 'price', 'financials']
            section['paragraphs'] = [paragraph.replace('[src: synthetic_archive]', '[evidence: archive]')
                                     for paragraph in section['paragraphs']]
            section['tables'], section['charts'] = [], []
            if section['key'] == 'valuation':
                section['title'] = 'Independent thesis, assumptions and unavailable analyst consensus'
                section['paragraphs'] = list(RESEARCH_VALUATION_PARAGRAPHS)
            elif section['key'] == 'scenarios':
                section['paragraphs'][-1] = RESEARCH_SCENARIO_PARAGRAPH
            elif section['key'] == 'executive':
                section['paragraphs'][3] = (
                    'The research conclusion follows the operating evidence and the committee debate. Qualification '
                    'within an installed customer program offers some protection, but renewal terms determine how much '
                    'of that benefit accrues to the supplier. The committee therefore asks which contracts support '
                    'repeatable cash returns and which merely defer a competitive repricing. It keeps the bear, central '
                    'and bull mechanisms distinct without assigning invented target prices or probabilities. Analyst '
                    'consensus is unavailable in this fixture. That absence is disclosed alongside the independent '
                    'judgment and does not imply that a company analysis requires an AI fair value. [evidence: archive]')
                section['paragraphs'][4] = section['paragraphs'][4].replace(
                    'Conversely, a rejected investment idea may still have a complete and valid valuation model worth preserving.',
                    'A rejected investment idea can still be a completed, documented company analysis worth preserving.')
            elif section['key'] == 'financial_quality':
                section['paragraphs'] = [paragraph.replace(
                    'The model remains incomplete wherever the necessary classification evidence is unavailable.',
                    'The classification remains unresolved wherever its supporting evidence is unavailable.').replace(
                    'The valuation bridge must identify financial debt, leases, minority claims and non-operating assets consistently with the chosen cash-flow method.',
                    'The balance-sheet review distinguishes financial debt, leases, minority claims and non-operating assets, with their actual reporting perimeter.').replace(
                    'or describe a partial accounting bridge as a complete model.',
                    'or describe a partial accounting reconciliation as complete evidence.') for paragraph in section['paragraphs']]
            elif section['key'] == 'decision':
                section['paragraphs'] = [paragraph.replace(
                    'whether the gap blocks valuation, implementation or both.',
                    'whether the gap limits the analytical conclusion, implementation or both.').replace(
                    'This distinction matters because a valid workbook may already exist even when a personal allocation decision cannot be made. Conversely, a coherent qualitative thesis does not justify inventing numerical assumptions to create a workbook.',
                    'This distinction matters because a complete research dossier may already exist even when a personal allocation decision cannot be made. A coherent qualitative thesis does not justify inventing missing facts or a price target.').replace(
                    'a usable or explicitly incomplete model and a correctly delivered package.',
                    'explicit assumptions and source gaps, and a correctly delivered research package.') for paragraph in section['paragraphs']]
            elif section['key'] == 'red_team':
                section['paragraphs'] = [paragraph.replace(
                    "The principal valuation remains tied to the candidate's own operating drivers and claim structure.",
                    "The principal thesis remains tied to the candidate's own operating drivers and claim structure.").replace(
                    'the mere existence of a completed valuation.', 'the mere existence of a completed analysis.').replace(
                    'support a conditional valuation,', 'support a conditional business assessment,') for paragraph in section['paragraphs']]
        for scenario in result['scenarios']:
            scenario['evidence_ids'] = ['archive', 'price']
        for objection in result['objections']:
            objection['evidence_ids'] = ['archive', 'price']
        if result['proposal']:
            result['proposal'].update(action='ADD', eur_amount=100.0,
                sizing_source='Measured common sizing engine', rationale='Conditional proposal after verified risk and cash checks.')
        return result

    def prior_tools(messages):
        return [block for row in messages if isinstance(row.get('content'), list)
                for block in row['content'] if isinstance(block, dict) and block.get('type') == 'tool_use']

    def completed(messages, name, input_):
        return any(row.get('name') == name and row.get('input') == input_ for row in prior_tools(messages))

    def response(kwargs, content, serial, stop_reason='end_turn'):
        return SimpleNamespace(id='offline-research-response-' + str(serial), model=kwargs['model'],
            stop_reason=stop_reason, content=content,
            usage=Usage(input_tokens=120, output_tokens=180, cache_read_input_tokens=0,
                cache_creation_input_tokens=0, cost_usd=0.001))

    def challenge():
        return {'decisive_questions': ['Which dated evidence would invalidate this independent company thesis?'],
            'objections': [{'id': 'challenge-' + desk, 'desk': desk,
                'category': 'risk' if desk == 'quant' else 'interpretation', 'material': True,
                'objection': 'The synthetic ' + desk + ' thesis needs explicit counterevidence and uncertainty.',
                'evidence_refs': ['read_company_dossier'],
                'requested_change': 'Explain the supported inference and any unavailable evidence.'}
                for desk in trade_idea.TRADE_IDEA_DESKS]}

    class SyntheticStream:
        def __init__(self, kwargs, serial):
            self.kwargs, self.serial = kwargs, serial
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def get_final_message(self):
            state['capo_calls'] += 1
            if state['capo_failure'] == 'stream_disconnected':
                raise RuntimeError('Provider stream disconnected after partial output')
            text = '{broken' if state['capo_failure'] == 'malformed' else json.dumps(dossier_payload())
            return response(self.kwargs, [SimpleNamespace(type='text', text=text)], self.serial)

    class Messages:
        def create(self, **kwargs):
            provider_calls.append(deepcopy(kwargs))
            serial = len(provider_calls)
            if state['provider_failure'] == 'incomplete_response':
                return response(kwargs, [SimpleNamespace(type='text', text='Partial visible output')],
                                serial, 'max_tokens')
            if state['provider_failure']:
                raise RuntimeError(state['provider_failure'])
            available = {row['name'] for row in kwargs.get('tools') or []}
            assert not available.intersection(FORBIDDEN_TOOLS), available.intersection(FORBIDDEN_TOOLS)
            context = str(kwargs['messages'][0].get('content'))
            round_n = _provider_round(kwargs)
            system = str(kwargs.get('system'))
            desk = next((name for name in trade_idea.TRADE_IDEA_DESKS
                         if ('You are the ' + name + ' desk' in system
                             or 'as the ' + name + ' desk' in system)), None)
            is_red = (kwargs.get('response_format') or {}).get('json_schema', {}).get('name') == 'trade_idea_committee_review'
            steps = []
            if desk == 'fundamentals' and round_n == 0:
                steps = [
                    ('search_company_sources', {'ticker': IDENTITY['ticker'], 'url': PAGE, 'query': 'Annual'}),
                    ('open_company_source', {'ticker': IDENTITY['ticker'], 'url': url}),
                    ('acquire_company_source', {'ticker': IDENTITY['ticker'], 'source': {'url': url}}),
                ]
                if state['source_gaps']:
                    steps.append(('acquire_company_source', {'ticker': IDENTITY['ticker'], 'source': {'url': missing_url}}))
                steps += [('read_company_dossier', {'ticker': IDENTITY['ticker'], 'section': 'document', 'document_id': document_id}),
                          ('get_consensus_estimates', {'ticker': IDENTITY['ticker']})]
            elif desk:
                steps = [('read_company_dossier', {'ticker': IDENTITY['ticker'], 'section': 'catalog'})]
                if desk == 'fundamentals' and round_n == 1:
                    steps.extend([('get_filing_changes', {'ticker': IDENTITY['ticker']}),
                                  ('get_fundamentals', {'ticker': IDENTITY['ticker']})])
                if round_n == 2 and desk != state['no_reply']:
                    steps.append(('respond_trade_idea_objection', {
                        'objection_id': 'challenge-' + desk,
                        'response': 'The shared dated annual statement supports this limited inference; unavailable consensus and remaining economic uncertainty are explicit.',
                        'state': 'answered', 'evidence_refs': ['read_company_dossier'], 'model_revision_id': None}))
            next_step = next(((name, input_) for name, input_ in steps
                              if not completed(kwargs['messages'], name, input_)), None)
            if next_step and kwargs.get('tool_choice') != {'type': 'none'}:
                name, input_ = next_step
                assert name in available, (desk, name, sorted(available))
                return response(kwargs, [SimpleNamespace(type='tool_use', name=name, input=input_,
                    id='offline-research-tool-' + str(serial))], serial, 'tool_use')
            text = (json.dumps(challenge()) if is_red else
                ('The ' + str(desk) + ' desk records its ' +
                 {0: 'source reconnaissance', 1: 'independent thesis', 2: 'final objection review'}.get(round_n, 'research') + '. ' +
                 ('The shared admitted annual statement supports a conditional business thesis. '
                 'Observed facts, management guidance, independent assumptions and analyst consensus remain distinct. '
                 'Consensus is unavailable and the undated commentary is not financial evidence. '
                 'Renewal economics, cash conversion, reinvestment and downside conditions remain uncertain. '
                 'No workbook or mandatory fair value is required [src: read_company_dossier]. ' * 5)))
            if state['truncate'] == (desk, round_n):
                # Known-cost truncation of one desk's answer (no visible end of turn).
                return response(kwargs, [SimpleNamespace(type='text', text=text[:200])], serial, 'max_tokens')
            return response(kwargs, [SimpleNamespace(type='text', text=text)], serial)

        def stream(self, **kwargs):
            provider_calls.append(deepcopy(kwargs))
            return SyntheticStream(kwargs, len(provider_calls))

    class Client:
        def __init__(self, **_kwargs):
            import httpx
            self._http = SimpleNamespace(timeout=httpx.Timeout(450))
            self.messages = Messages()

    for owner in (base, llm_client, trade_idea):
        monkeypatch.setattr(owner, 'OpenRouterClient', Client)

    def dispatch(name, input_, **_kwargs):
        tool_calls.append((name, deepcopy(input_)))
        if name in FORBIDDEN_TOOLS:
            return forbidden('tool:' + name)()
        if name == 'get_consensus_estimates':
            return {'ok': True, 'data': {'ticker': IDENTITY['ticker'], 'as_of': DAY,
                'status': 'unavailable', 'price_targets': None,
                'reason': 'The frozen consensus provider supplied no analyst target.'}, '_source': 'frozen consensus'}
        return {'ok': True, 'data': {'ticker': IDENTITY['ticker'], 'as_of': DAY,
            'price': 100, 'px': 100, 'price_asof': DAY, 'currency': 'EUR', 'status': 'ready',
            'revenue': 100, 'annual_statement_text': TEXT, 'filing_url': url,
            'source': 'yfinance daily Close (not an intraday quote)'},
            '_source': 'Frozen synthetic provider observations', '_timestamp': DAY + 'T12:00:00Z'}

    monkeypatch.setattr(chat_tools, 'dispatch', dispatch)
    book = {'positions': [
        {'ticker': 'SYNTH-EXT', 'quantita': 1, 'valuta': 'EUR', 'valore_mercato_eur': 100, 'valore_mercato': 100},
        {'ticker': 'SYNTH-PEER', 'quantita': 99, 'valuta': 'EUR', 'valore_mercato_eur': 9900, 'valore_mercato': 9900}],
        'cash_source': 'sqlite:cash_state', 'cash_disponibile_eur': 1000, 'stale_positions': [], 'fx_incomplete': []}
    risk = {'portfolio': {'var_99_1d_pct': -1.0},
        'per_asset': {ticker: {'vol_annual_pct': 20.0} for ticker in ('SYNTH-EXT', 'SYNTH-PEER')},
        'correlation': {'tickers': ['SYNTH-EXT', 'SYNTH-PEER'], 'matrix': [[1.0, 0.2], [0.2, 1.0]]},
        'fx_conversion': {'local_declared': []}, 'skipped_tickers': []}
    stress = {'stress_scenario': 'gfc_2008', 'stress_fallback': False, 'returns_basis': 'EUR',
        'stress_meta': {'window_loss_pct': -5.0, 'real_history': ['SYNTH-EXT', 'SYNTH-PEER'],
                       'proxied': {}, 'zero_filled_days': {}}}

    def session_factory(**kwargs):
        return ResearchSession(**kwargs, download=transport)

    def round_runner(board, number):
        boards.append(board)
        consigliere_multi.run_round(board, number)
        if number == state['stop_after']:
            raise RuntimeError('Intentional offline crash after native R' + str(number) + ' checkpoint')

    def execute(run_id, name, *, risk_loader=None, stress_loader=None):
        return trade_idea.execute_trade_idea(run_id, store=current,
            lock_path=tmp_path / 'paid.lock', output_dir=tmp_path / name,
            portfolio_loader=lambda: deepcopy(book), mandate_loader=lambda: deepcopy(mandate),
            risk_loader=risk_loader or (lambda: deepcopy(risk)),
            stress_loader=stress_loader or (lambda: deepcopy(stress)),
            candidate_metrics_loader=lambda *_args: {'status': 'not_applicable'},
            preparer_binder=forbidden('preparer_binder'),
            source_qualifier=lambda *_a, **_k: model.recheck_accepted_sources(admission,
                IDENTITY['ticker'], IDENTITY, archive_root=tmp_path, allow_historical=True),
            source_session_factory=session_factory, document_archive_root=tmp_path,
            round_runner=round_runner, catalog_fetcher=lambda: deepcopy(priced['catalog_snapshot']),
            isolated_tool_dispatcher=dispatch, isolated_facts_loader=lambda: 'Frozen offline facts only')

    yield SimpleNamespace(current=current, request=request, execute=execute, state=state,
        prohibited=prohibited, providers=provider_calls, tools=tool_calls, boards=boards,
        transport=transport, smtp=smtp, root=tmp_path, database=migrated, admission=admission,
        book=book)
    assert not prohibited, dict(prohibited)
    assert not list(tmp_path.rglob('*.xlsx')), 'Research produced an Excel artifact'


def _assert_complete(case, detail, *, judgment, destination):
    from pypdf import PdfReader
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    assert detail['result']['judgment'] == judgment
    assert detail['run']['destination']['kind'] == destination
    assert detail['result']['valuation_refs'] == []
    assert detail['result']['model_review'] is None
    assert detail['states']['analysis'] == 'complete'
    assert detail['states']['artifacts'] == 'ready'
    manifest = detail['artifacts']
    assert manifest['completion_contract'] == 'research-memo-pdf/1'
    assert manifest['complete_package_status'] == 'ready'
    assert {item['kind'] for item in manifest['artifacts']} == {'pdf'}
    assert manifest['model_status'] != 'ready'
    assert manifest['source_inventory']
    assert any(row['url'] == SITE + '/investors/report.html' for row in manifest['source_inventory'])
    assert detail['email']['status'] == 'accepted'
    assert detail['email']['receipt']['smtp_state'] == 'accepted'
    assert len(case.smtp[0]) == 1
    message = case.smtp[0][0]
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1 and attachments[0].get_content_type() == 'application/pdf'
    pdf_bytes = attachments[0].get_payload(decode=True)
    pdf_path = Path(manifest['artifacts'][0]['path'])
    assert pdf_bytes == pdf_path.read_bytes()
    assert sha256(pdf_bytes).hexdigest() == manifest['expected_hashes'][str(pdf_path)]
    assert message['Message-ID'] == manifest['message_id']
    reader = PdfReader(pdf_path)
    assert manifest['pdf_quality']['status'] == 'ready'
    assert manifest['pdf_quality']['analytical_pages'] >= 10
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'consensus' in text.lower() and 'unavailable' in text.lower()
    assert any(name == 'get_consensus_estimates' for name, _ in case.tools)
    assert not case.prohibited
    assert not list(case.root.rglob('*.xlsx'))
    assert detail['cost']['unknown_requests'] == 0
    assert detail['cost']['reserved_usd'] == '0'
    assert detail['cost']['requests'] == len(case.providers)
    assert Decimal(detail['cost']['charged_usd']) == Decimal('0.001') * len(case.providers)
    assert detail['cost']['by_phase']['model_preparation']['requests'] == 0
    assert detail['cost']['by_phase']['model_revision']['requests'] == 0
    assert case.state['capo_calls'] == 1
    checkpoint = detail['progress']['checkpoint']
    assert checkpoint['data']['_research_thesis']['analysis_mode'] == RESEARCH_ANALYSIS_MODE
    assert checkpoint['data']['_research_thesis']['dossier_sha256']
    assert checkpoint['data']['_research_thesis']['thesis_sha256']
    seal = checkpoint['data']['_research_thesis']
    review = detail['progress']['research_review']
    assert review == checkpoint['data']['_research_review']
    assert set(review['desks']) == set(trade_idea.TRADE_IDEA_DESKS)
    assert len({row['report_sha256'] for row in review['desks'].values()}) == len(trade_idea.TRADE_IDEA_DESKS)
    assert all(row['round'] == 2 and row['research_ref'] == review['research_ref']
               for row in review['desks'].values())
    assert review['red_team']['research_ref'] == review['research_ref']
    assert review['research_ref']['thesis_sha256'] == seal['thesis_sha256']
    assert review['research_ref']['dossier_sha256'] == seal['dossier_sha256']
    assert seal['dossiers'][IDENTITY['ticker']]['documents']
    readers = [row for row in checkpoint['tool_receipts'] if row['tool'] == 'read_company_dossier']
    assert readers and all(row['success'] for row in readers)
    assert any(row['input'].get('section') == 'document' for row in readers)
    assert all(row['status'] == 'complete' for row in checkpoint['specialist_checkpoints'].values())
    return deepcopy(manifest)


def test_rejected_research_partial_sources_three_resumes_and_exact_delivery_recovery(no_workbook_case):
    case = no_workbook_case
    current = case.current
    parent = current.create_run(case.request, idempotency_key='research-without-workbook')['run']['id']
    original_request = deepcopy(current.get_accepted_request(parent))
    with sqlite3.connect(case.database) as conn:
        original_decisions = conn.execute('SELECT * FROM decisions ORDER BY id').fetchall()
    case.state['stop_after'] = 0
    first = case.execute(parent, 'parent')
    assert first['run']['technical_status'] == 'incomplete', first['run']['reason']
    assert 'native R0' in first['run']['reason'], first['run']['reason']
    assert not case.prohibited and not case.smtp[0]
    source_requests = len(case.transport.requests)
    assert source_requests > 0
    saved_parent = _native_rows(case.database, parent)
    first_count = len(case.providers)
    child = current.create_continuation(parent, idempotency_key='research-first-resume',
        authorize_new_requests=True)['run']['id']
    case.state['stop_after'] = 1
    second = case.execute(child, 'child')
    assert 'native R1' in second['run']['reason'], second['run']['reason']
    assert len(case.providers) > first_count
    assert all(_provider_round(call) != 0 for call in case.providers[first_count:])
    assert len(case.transport.requests) == source_requests
    saved_child = _native_rows(case.database, child)
    second_count = len(case.providers)
    reviewed_id = current.create_continuation(child, idempotency_key='research-second-resume',
        authorize_new_requests=True)['run']['id']
    case.state['stop_after'] = 2
    reviewed = case.execute(reviewed_id, 'reviewed')
    assert 'native R2' in reviewed['run']['reason'], reviewed['run']['reason']
    assert len(case.providers) > second_count
    assert all(_provider_round(call) not in (0, 1) for call in case.providers[second_count:])
    assert len(case.transport.requests) == source_requests
    assert not case.smtp[0] and case.state['capo_calls'] == 0
    saved_reviewed = _native_rows(case.database, reviewed_id)
    third_count = len(case.providers)
    saved_seal = deepcopy(reviewed['progress']['checkpoint']['data']['_research_thesis'])
    saved_reviews = deepcopy(reviewed['progress']['checkpoint']['data']['_desk_research_reviews'])
    final_id = current.create_continuation(reviewed_id, idempotency_key='research-third-resume',
        authorize_new_requests=True)['run']['id']
    case.state['stop_after'] = None
    final = case.execute(final_id, 'final')
    manifest = _assert_complete(case, final, judgment='rejected', destination='research')
    assert len(case.providers) == third_count + 1
    assert all(_provider_round(call) is None for call in case.providers[third_count:])
    assert final['progress']['checkpoint']['data']['_research_thesis'] == saved_seal
    assert final['progress']['checkpoint']['data']['_desk_research_reviews'] == saved_reviews
    assert len(case.transport.requests) == source_requests
    assert any('Undated' in gap for gap in final['result']['data_gaps'])
    assert final['result']['proposal'] is None
    assert any(receipt.get('success') is False and receipt.get('tool') == 'acquire_company_source'
               for receipt in final['progress']['checkpoint']['tool_receipts'])
    saved_final = _native_rows(case.database, final_id)
    provider_count = len(case.providers)
    for receipt in manifest['exact_artifact_receipts']:
        path = Path(receipt['path'])
        assert path.is_relative_to(case.root)
        path.unlink()
    for _ in range(2):
        trade_idea.deliver_trade_idea(current, final_id, output_dir=case.root / 'final', send_email=True)
        recovered = current.get_run(final_id)
        assert recovered['artifacts'] == manifest
        assert recovered['states']['artifacts'] == 'ready'
        for receipt in manifest['exact_artifact_receipts']:
            assert sha256(Path(receipt['path']).read_bytes()).hexdigest() == receipt['sha256']
    assert len(case.providers) == provider_count and len(case.smtp[0]) == 1
    assert len(case.transport.requests) == source_requests
    assert _native_rows(case.database, parent) == saved_parent
    assert _native_rows(case.database, child) == saved_child
    assert _native_rows(case.database, reviewed_id) == saved_reviewed
    assert _native_rows(case.database, final_id) == saved_final
    assert current.get_accepted_request(parent) == original_request
    for run_id in (parent, child, reviewed_id, final_id):
        assert current.get_accepted_request(run_id)['analysis_mode'] == RESEARCH_ANALYSIS_MODE
        assert current.get_accepted_request(run_id)['authorization'] == case.request['authorization']
    with sqlite3.connect(case.database) as conn:
        decisions = conn.execute('SELECT * FROM decisions ORDER BY id').fetchall()
        assert decisions[:len(original_decisions)] == original_decisions
        # research/3 (PM 03/10, f6a837d): una ripresa interrotta che ha gia' report pagati chiude
        # come pacchetto parziale instradato a research. Garanzia conservata: una sola decisione
        # per run della catena, tutte RESEARCH (mai operative), nessuna riscrittura delle precedenti.
        added = conn.execute('SELECT id, action FROM decisions WHERE id > ? ORDER BY id',
                             (original_decisions[-1][0] if original_decisions else 0,)).fetchall()
        chain = (parent, child, reviewed_id, final_id)
        assert len(decisions) == len(original_decisions) + len(chain)
        assert [action for _, action in added] == ['RESEARCH'] * len(chain)
        assert [current.get_run(run_id)['run']['destination']['decision_id']
                for run_id in chain] == [ident for ident, _ in added]
        assert conn.execute('SELECT count(*) FROM memos WHERE id=?', (final['run']['memo_id'],)).fetchone()[0] == 1
    (case.root / 'research-no-workbook-proof.json').write_text(json.dumps({
        'run_ids': [parent, child, reviewed_id, final_id], 'provider_requests': provider_count,
        'source_requests': source_requests, 'forbidden_calls': dict(case.prohibited),
        'smtp_accepted_messages': len(case.smtp[0]), 'manifest': manifest,
        'recovered_twice': True, 'costs': final['cost']}, indent=2), encoding='utf-8')


def test_favorable_research_reaches_decisions_without_workbook_or_bypassed_checks(no_workbook_case):
    case = no_workbook_case
    case.state.update(judgment='favorable', source_gaps=False)
    ident = case.current.create_run(case.request, idempotency_key='favorable-without-workbook')['run']['id']
    detail = case.execute(ident, 'favorable')
    _assert_complete(case, detail, judgment='favorable', destination='dcn')
    checks = detail['progress']['routing_checks']
    for name in ('identity_verified', 'evidence_sufficient', 'red_team_complete', 'capo_valid',
                 'mandate_valid', 'sizing_valid', 'history_context_sent', 'candidate_price_revalidated', 'fx_revalidated'):
        assert checks[name] is True, (name, checks)
    assert detail['progress']['checkpoint']['data']['_numeric_claim_gaps'] == []
    with sqlite3.connect(case.database) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute('SELECT * FROM decisions WHERE id=?',
            (detail['run']['destination']['decision_id'],)).fetchone())
        assert row['ticker'] == IDENTITY['ticker'] and row['action'] == 'ADD'
        assert row['status'] == 'PENDING' and row['eur_amount'] == 100.0
        assert conn.execute('SELECT COUNT(*) FROM trade_history').fetchone()[0] == 0


@pytest.mark.parametrize('foreign_book,missing_fx,crash_at', [
    (True, False, None), (True, True, None), (False, False, 'first_tool'),
    (False, False, 'macro_r2'), (False, False, 'capo'), (False, False, 'first_tool_prices')],
    ids=['qualified_foreign_book', 'missing_foreign_fx', 'crash_after_tool',
         'crash_after_macro_r2', 'crash_after_capo', 'updated_prices_reverified'])
def test_favorable_research_operational_checks_fx_and_crash_resume(
        no_workbook_case, monkeypatch, foreign_book, missing_fx, crash_at):
    """Garanzie vive del vecchio test positivo col workbook (positive_replay, ora in quarantena):
    controlli operativi reali con book estero (FX qualificata o assente), crash del processo
    dopo un checkpoint durevole con ripresa che non ripaga tool, round o Capo, ripresa con
    prezzi aggiornati autorizzata e riverificata, recupero della consegna senza nuove spese."""
    from trade_idea_evolution_fixtures import historical_fx_engines
    case = no_workbook_case
    case.state.update(judgment='favorable', source_gaps=False)
    current = case.current
    loaders = {}
    if foreign_book:
        with sqlite3.connect(case.database) as conn:
            conn.execute("UPDATE positions SET valuta='USD' WHERE ticker='SYNTH-PEER'")
        case.book['positions'][1].update(valuta='USD', fx_to_eur=1 / 1.1, fx_source='live', peso_pct=99)
        case.book['positions'][0]['peso_pct'] = 1
        day = datetime.now(timezone.utc).date()
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        risk_loader, stress_loader, historical_calls, engine_outputs = historical_fx_engines(
            monkeypatch, case.root, case.book, day.isoformat(), missing_fx=missing_fx)
        loaders = {'risk_loader': risk_loader, 'stress_loader': stress_loader}
    run_id = current.create_run(case.request, idempotency_key='favorable-checks')['run']['id']
    if crash_at is None:
        detail = case.execute(run_id, 'favorable', **loaders)
    else:
        class SimulatedProcessCrash(BaseException):
            pass
        update_progress = current.update_progress
        crashes = []

        def crash_after_durable_checkpoint(selected_id, token, phase, progress):
            update_progress(selected_id, token, phase, progress)
            checkpoint = progress.get('checkpoint') or {}
            data = checkpoint.get('data') or {}
            trigger = (bool(checkpoint.get('tool_receipts')) if crash_at in ('first_tool', 'first_tool_prices') else
                'macro:2' in data.get('_completed_stages', {}) if crash_at == 'macro_r2' else
                bool(data.get('_capo_completed')))
            if trigger and not crashes:
                crashes.append(len(case.providers))
                raise SimulatedProcessCrash('Offline process loss after committed checkpoint')

        with monkeypatch.context() as patch:
            patch.setattr(current, 'update_progress', crash_after_durable_checkpoint)
            with pytest.raises(SimulatedProcessCrash):
                case.execute(run_id, 'crashed', **loaders)
        assert len(crashes) == 1 and not case.smtp[0]
        current.interrupt_run(run_id, reason='Offline owner process confirmed dead')
        parent_detail = current.get_run(run_id)
        parent_id = run_id
        refresh_options = {}
        if crash_at == 'first_tool_prices':
            with sqlite3.connect(case.database) as conn:
                for ticker in ('SYNTH-EXT', 'SYNTH-PEER'):
                    conn.execute('INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) '
                                 'VALUES(?,?,?,?,?)', (ticker, 100, 'EUR', 'frozen current price',
                                                       datetime.now(timezone.utc).isoformat()))
            refresh_options['authorize_price_refresh'] = True
        resumed = current.create_continuation(parent_id, idempotency_key='explicit-resume',
            authorize_new_requests=True, **refresh_options)
        run_id = resumed['run']['id']
        assert resumed['cost']['requests'] == crashes[0]
        detail = case.execute(run_id, 'resumed', **loaders)
        repeated = current.create_continuation(parent_id, idempotency_key='second-resume',
            authorize_new_requests=True)
        assert repeated['created'] is False and repeated['run']['id'] == run_id
        assert repeated['cost']['requests'] == len(case.providers)
        original = current.get_run(parent_id)
        for key in ('run', 'progress', 'result', 'cost'):
            assert original[key] == parent_detail[key]
        if crash_at == 'capo':
            assert len(case.providers) == crashes[0], 'Resume must not buy another Capo'
        if crash_at in ('first_tool', 'first_tool_prices'):
            assert sum(row['tool'] == 'read_company_dossier' and row['specialist'] == 'macro'
                       and row['round'] == 0 for row in detail['progress']['checkpoint']['tool_log']) == 1
    _assert_complete(case, detail, judgment='favorable', destination='research' if missing_fx else 'dcn')
    checks = detail['progress']['routing_checks']
    if missing_fx:
        assert checks['sizing_valid'] is False
        assert 'sizing_valid' in detail['run']['destination']['reason']
    else:
        for name in ('identity_verified', 'evidence_sufficient', 'red_team_complete', 'capo_valid',
                     'mandate_valid', 'sizing_valid', 'history_context_sent',
                     'candidate_price_revalidated', 'fx_revalidated'):
            assert checks[name] is True, (name, checks)
    if crash_at == 'first_tool_prices':
        refreshed = checks['price_refresh_verification']
        assert all(value is True for value in refreshed['measurements'].values())
        assert refreshed['evidence']['proposal'] == detail['result']['proposal']
        assert refreshed['before']['current_context_sha256'] == refreshed['after']['current_context_sha256']
        assert (refreshed['before']['price_inputs_sha256'] !=
                detail['run']['continuation']['price_refresh']['accepted_price_inputs_sha256'])
    directory = case.root / ('resumed' if crash_at else 'favorable')
    before_calls, extra_sends = len(case.providers), []
    trade_idea.deliver_trade_idea(current, run_id, output_dir=directory, send_email=False,
        send=lambda *_a, **_k: extra_sends.append('unexpected'))
    from bellomberg.storage.trade_idea_store import RunConflict
    with trade_idea.exclusive_paid_run(directory / '.delivery.lock'):
        with pytest.raises(RunConflict, match='delivery recovery'):
            trade_idea.deliver_trade_idea(current, run_id, output_dir=directory, send_email=False)
    assert len(case.providers) == before_calls and extra_sends == [] and len(case.smtp[0]) == 1
    assert current.get_run(run_id)['artifacts'] == detail['artifacts']
    if foreign_book:
        assert engine_outputs['risk']['fx_conversion']['qualified'] is (not missing_fx)
        assert engine_outputs['stress']['fx_conversion']['qualified'] is (not missing_fx)
        assert any(symbols == ['EURUSD=X'] for symbols, _ in historical_calls)
        if missing_fx:
            assert engine_outputs['simulations'] == 0
        else:
            assert engine_outputs['stress']['stress_meta']['fx_conversion']['qualified'] is True
            assert any(str(options.get('start', '')).startswith('2008') for _, options in historical_calls)
            assert engine_outputs['simulations'] == 1



@pytest.mark.parametrize('provider_failure', ['HTTP 503 service unavailable', 'HTTP 504 provider timeout',
    'provider disconnected after dispatch', 'incomplete_response'])
def test_research_provider_failure_classes_stop_without_result_or_email(no_workbook_case, provider_failure):
    """Garanzie vive del vecchio replay del comitato col workbook (pipeline, ora in quarantena):
    un guasto del provider non diventa mai un giudizio, non si ritenta a pagamento, il costo
    incerto resta trattenuto e dichiarato, nessuna email parte."""
    case = no_workbook_case
    case.state['provider_failure'] = provider_failure
    run_id = case.current.create_run(case.request, idempotency_key='provider-failure')['run']['id']
    detail = case.execute(run_id, 'failure')
    assert detail['run']['technical_status'] == 'incomplete'
    assert detail['states']['analysis'] == 'incomplete'
    assert detail['email']['status'] == 'blocked' and detail['email']['attempts'] == 0
    assert not case.smtp[0] and case.state['capo_calls'] == 0
    assert detail['cost']['requests'] == len(case.providers)
    assert detail['run']['destination']['kind'] in ('none', 'research')
    assert not detail['result'] or (detail['result']['judgment'] == 'incomplete'
                                    and detail['result']['proposal'] is None)
    if provider_failure == 'incomplete_response':
        # Troncamento a costo noto (research/3, PM 03/10): ogni desk diventa lacuna
        # dichiarata, il comitato va sotto quorum e si ferma prima di Red Team e Capo.
        assert 'below quorum' in detail['run']['reason']
        assert len(case.providers) == len(trade_idea.TRADE_IDEA_DESKS)
        assert detail['cost']['unknown_requests'] == 0
        assert Decimal(detail['cost']['charged_usd']) == Decimal('0.001') * len(case.providers)
    else:
        assert detail['result'] is None
        assert provider_failure in detail['run']['reason']
        assert len(case.providers) == 1, 'an ambiguous paid failure is never retried'
        assert detail['cost']['unknown_requests'] == 1
        assert Decimal(detail['cost']['unknown_reserved_usd']) > 0
        assert detail['cost']['remaining_known_usd'] is None


@pytest.mark.parametrize('capo_failure', ['malformed', 'stream_disconnected'])
def test_research_capo_failure_keeps_desk_work_but_never_a_judgment_or_email(no_workbook_case, capo_failure):
    """Capo malformato o stream interrotto (CONTEXT 03/10 punto 4): giudizio incomplete,
    proposta nulla, causa nei data_gaps, instradamento research, email bloccata."""
    case = no_workbook_case
    case.state['capo_failure'] = capo_failure
    run_id = case.current.create_run(case.request, idempotency_key='capo-failure')['run']['id']
    detail = case.execute(run_id, 'capo-failure')
    assert case.state['capo_calls'] == 1
    assert detail['run']['technical_status'] == 'incomplete'
    assert detail['result']['judgment'] == 'incomplete' and detail['result']['proposal'] is None
    assert detail['result']['data_gaps'][0].startswith('Capo output invalid or unavailable')
    assert detail['run']['destination']['kind'] == 'research'
    assert detail['email']['status'] == 'blocked' and detail['email']['attempts'] == 0
    assert not case.smtp[0]
    reports = detail['progress']['reports']
    assert {row['specialist'] for row in reports} == set(trade_idea.TRADE_IDEA_DESKS)
    assert detail['progress']['red_team']['report']
    assert detail['cost']['requests'] == len(case.providers)
    if capo_failure == 'malformed':
        assert detail['progress']['routing_checks']['capo_valid'] is False
        assert detail['cost']['unknown_requests'] == 0
    else:
        assert 'stream disconnected' in detail['run']['reason']
        assert detail['cost']['unknown_requests'] == 1
        assert detail['cost']['remaining_known_usd'] is None
