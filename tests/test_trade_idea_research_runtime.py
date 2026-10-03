"""Native research tools, ordinary worker, real workbook and repeated recovery."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.company_source_research import ResearchSession
from bellomberg.valuation import trade_idea_model as model
from test_company_source_research import FrozenTransport, html, SITE, PAGE
from test_trade_idea_pm_sources import TEXT, sourced_plan
from test_trade_idea_source_research import frozen_clock, grant
from test_trade_idea_economic import IDENTITY, providers_for
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_store import db_path, migrated, store
from test_trade_idea_historical_runtime import _worker_options
from test_sector_analysis import DAY
from trade_idea_evolution_fixtures import committee_review, review_tool_block
from trade_idea_fixtures import bind_workbook, research_result


def test_http_research_admission_requires_exact_grant_and_cost_confirmation(migrated, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api import trade_idea_routes as routes
    current, workers = store(migrated), []
    priced = _priced_request(budget='30')
    monkeypatch.setattr(model, '_free_providers', lambda *_args: providers_for())
    monkeypatch.setattr(routes, 'datetime', model.datetime)

    def preflight(ticker, view, origin, budget, **options):
        return trade_idea.preflight_trade_idea(ticker, view, origin, budget,
            catalog_fetcher=lambda: priced['catalog_snapshot'], key_checker=lambda: None,
            mandate_loader=lambda: {}, active_checker=options['active_checker'],
            identity_resolver=options.get('identity_resolver', lambda _: deepcopy(IDENTITY)),
            **{key: value for key, value in options.items() if key in
               ('source_qualifier', 'archive_root', 'document_sources')})

    monkeypatch.setattr(routes, '_store', lambda *_args, **_kwargs: current)
    monkeypatch.setattr(routes, 'paid_run_is_active', lambda: False)
    monkeypatch.setattr(routes, 'preflight_trade_idea', preflight)
    monkeypatch.setattr(routes, '_spawn_worker', lambda run_id, *_args, **_kwargs: workers.append(run_id))
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: 'offline-session', db_path=migrated, source_archive_root=tmp_path)
    body = {'ticker': IDENTITY['ticker'], 'pm_view': 'Frozen ordinary research',
            'view_source': 'manual', 'budget_limit_usd': '30'}
    with TestClient(app) as client:
        checked = client.post('/trade-ideas/preflight', json=body)
        assert checked.status_code == 200 and checked.json()['ok'], checked.text
        qualification = checked.json()['source_qualification']
        assert qualification['status'] == 'research_required'
        assert checked.json()['preparation'] == {'required': True, 'paid': True, 'status': 'research_required'}
        assert 'source_report' not in checked.text
        accepted_grant = grant(qualification)
        request = {**body, 'authorization': accepted_grant, 'cost_acknowledged': False,
                   'idempotency_key': 'ordinary-research'}
        assert client.post('/trade-ideas/runs', json=request).status_code == 428
        request['cost_acknowledged'] = True
        invalid = deepcopy(request)
        invalid['authorization']['activities'] = ['committee']
        assert client.post('/trade-ideas/runs', json=invalid).status_code == 428
        assert current.list_runs()['total'] == 0 and not workers
        refreshed = client.post('/trade-ideas/preflight', json=body)
        assert refreshed.json()['source_qualification']['fingerprint'] == qualification['fingerprint']
        invalid = deepcopy(request)
        invalid['authorization']['source_fingerprint'] = '0' * 64
        assert client.post('/trade-ideas/runs', json=invalid).status_code == 428
        assert current.list_runs()['total'] == 0 and not workers
        accepted = client.post('/trade-ideas/runs', json=request)
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()['run_id']
        duplicate = client.post('/trade-ideas/runs', json=request)
        assert duplicate.status_code == 202 and duplicate.json()['run_id'] == run_id
    assert workers == [run_id]
    saved = current.get_accepted_request(run_id)
    assert saved['source_qualification']['source_report']['documents'] == []
    assert saved['source_qualification']['status'] == 'research_required'
    assert saved['authorization'] == accepted_grant
    assert current.get_run(run_id)['cost']['requests'] == 0
    receipts = list((tmp_path / 'trade_idea_preflights' / 'diagnostics').glob('*.json'))
    # Native start must have run the real source-admission recheck, not merely
    # trusted a public DTO or accepted an empty catalog without a signed grant.
    assert any(json.loads(path.read_text(encoding='utf-8'))['status'] == 'verified' for path in receipts)


def test_empty_archive_native_research_real_compile_and_two_linked_resumes(migrated, tmp_path, monkeypatch):
    from bellomberg.agents import consigliere_multi, chat_tools
    from bellomberg.agents.specialists import base
    from bellomberg.core import current_facts, llm_client, llm_pricing, mandato_pm, paths
    from bellomberg.core.llm_client import Usage

    providers = providers_for()
    original_profile = providers['profile']
    def profile(*args, **kwargs):
        result = original_profile(*args, **kwargs)
        result['data']['info']['website'] = SITE
        return result
    providers['profile'] = profile
    monkeypatch.setattr(model, '_free_providers', lambda *_args: providers)
    priced = _priced_request(budget='30')
    checked = trade_idea.preflight_trade_idea(IDENTITY['ticker'], 'Frozen sourced thesis', 'manual', '30',
        catalog_fetcher=lambda: priced['catalog_snapshot'], identity_resolver=lambda _: deepcopy(IDENTITY),
        active_checker=lambda: False, key_checker=lambda: None, mandate_loader=lambda: {}, archive_root=tmp_path)
    assert checked['ok'] and checked['source_qualification']['status'] == 'research_required'
    admission = checked['_source_qualification']
    assert admission['source_report']['documents'] == []
    current = store(migrated)
    request = {**priced, 'ticker': IDENTITY['ticker'], 'company_name': IDENTITY['name'],
        'exchange': IDENTITY['exchange'], 'currency': IDENTITY['currency'], 'language': 'en',
        'view_text': 'Frozen sourced thesis', 'source_qualification': admission, 'authorization': grant(admission)}
    parent = current.create_run(request, idempotency_key='native-primary-research')['run']['id']
    initial_request = current.get_accepted_request(parent)
    with sqlite3.connect(migrated) as conn:
        original_decisions = conn.execute('SELECT * FROM decisions ORDER BY id').fetchall()

    url = SITE + '/investors/report.html'
    raw = html(TEXT)
    document_id = sha256(raw).hexdigest()
    plan = sourced_plan(document_id, DAY)
    transport = FrozenTransport({'/investors/': (
        b'<html><body><a href="report.html">Annual financial statements</a></body></html>', 'text/html'),
        '/investors/report.html': (raw, 'text/html')})
    provider_calls, boards, builds, deliveries = [], [], [], []
    capo_payload = research_result('rejected', run_id=parent)
    for key in ('run_id', 'run_type', 'pm_view', 'destination'):
        capo_payload.pop(key, None)
    real_build = model.build_from_plan
    def record_build(*args, **kwargs):
        result = real_build(*args, **kwargs)
        builds.append(result)
        return result
    monkeypatch.setattr(model, 'build_from_plan', record_build)
    monkeypatch.setattr(paths, 'MODELS_DIR', tmp_path)
    monkeypatch.setattr(paths, 'REPORT_DIR', tmp_path)
    monkeypatch.setattr(current_facts, 'current_facts_block', lambda: 'Frozen offline company context')
    monkeypatch.setattr(mandato_pm, 'carica', mandato_pm.profilo_esempio)
    monkeypatch.setattr(llm_pricing, '_fx_usd_to_eur', lambda: (0.9, 'frozen_test_fx'))
    monkeypatch.setattr(chat_tools, '_compatta_portfolio_live', lambda value: deepcopy(value))

    def message_state(messages):
        calls, conversations = set(), []
        for row in messages:
            if not isinstance(row.get('content'), list):
                continue
            for block in row['content']:
                name = block.get('name') if isinstance(block, dict) else getattr(block, 'name', None)
                if name:
                    calls.add(name)
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    try:
                        value = json.loads(block['content'])
                    except (ValueError, TypeError):
                        continue  # Native large tool results may carry an explicit truncation notice.
                    if isinstance(value, dict) and isinstance(value.get('consultation'), dict):
                        conversations.append(value['consultation'])
        return calls, conversations

    def response(kwargs, content, stop_reason='end_turn'):
        return SimpleNamespace(id='offline-paid-response-' + str(len(provider_calls)), model=kwargs['model'],
            stop_reason=stop_reason, content=content,
            usage=Usage(input_tokens=120, output_tokens=180, cache_read_input_tokens=0,
                cache_creation_input_tokens=0, cost_usd=0.001))

    class SyntheticStream:
        def __init__(self, kwargs): self.kwargs = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get_final_message(self):
            final = bind_workbook(deepcopy(capo_payload), builds[-1])
            return response(self.kwargs, [SimpleNamespace(type='text', text=json.dumps(final))])

    class Messages:
        def create(self, **kwargs):
            provider_calls.append(deepcopy(kwargs))
            context = str(kwargs['messages'][0].get('content'))
            author = 'You are the fundamentals desk' in str(kwargs.get('system'))
            calls, conversations = message_state(kwargs['messages'])
            tools = []
            if author and 'Round 0.' in context:
                steps = [
                    ('search_company_sources', {'ticker': IDENTITY['ticker'], 'url': PAGE, 'query': 'Annual'}),
                    ('open_company_source', {'ticker': IDENTITY['ticker'], 'url': url}),
                    ('acquire_company_source', {'ticker': IDENTITY['ticker'], 'source': {'url': url}}),
                    ('read_candidate_source', {'document_id': document_id, 'query': 'Revenue'}),
                ]
                step = next(((name, data) for name, data in steps if name not in calls), None)
                if step:
                    tools = [step]
            elif author and 'Round 1.' in context:
                if 'get_candidate_model_inputs' not in calls:
                    tools = [('get_candidate_model_inputs', {'scope': 'model'})]
                elif 'ask_specialist' not in calls:
                    tools = [('ask_specialist', {'specialist': desk,
                        'question': 'Which transmission mechanism can invalidate these explicitly sourced cash-flow assumptions?',
                        'draft_assumptions': {'wacc': deepcopy(plan['scenarios']['base']['wacc'])},
                        'evidence_refs': [document_id]}) for desk in trade_idea.TRADE_IDEA_DESKS if desk != 'fundamentals']
                elif 'submit_candidate_model_plan' not in calls:
                    assert len(conversations) == 5
                    tools = [('submit_candidate_model_plan', {'plan': deepcopy(plan),
                        'rationale': 'Explicit synthetic author plan from the admitted frozen annual statement.',
                        'consultation_decisions': [{'consultation_id': row['id'], 'decision': 'incorporated',
                            'rationale': 'The received peer answer supports the explicit scenario while preserving the stated uncertainty.'}
                            for row in conversations]})]
                elif 'get_valuation' not in calls:
                    tools = [('get_valuation', {'ticker': IDENTITY['ticker']})]
            elif 'read_company_dossier' not in calls:
                tools = [('read_company_dossier', {'ticker': IDENTITY['ticker'], 'section': 'catalog'})]
            review = review_tool_block(kwargs, builds[-1] if builds else None)
            if review:
                tools = [(review['name'], review['input'])]
            if kwargs.get('tool_choice') == {'type': 'none'}:
                tools = []
            if tools:
                content = [SimpleNamespace(type='tool_use', name=name, input=data,
                    id=f'offline-tool-{len(provider_calls)}-{index}') for index, (name, data) in enumerate(tools)]
            else:
                text = (json.dumps(committee_review()) if
                    (kwargs.get('response_format') or {}).get('json_schema', {}).get('name') == 'trade_idea_committee_review'
                    else ('The frozen primary annual statement supports the declared synthetic scenario; '
                    'economic risks and uncertainty remain explicit [src: read_company_dossier]. ' * 15))
                content = [SimpleNamespace(type='text', text=text)]
            return response(kwargs, content, 'tool_use' if tools else 'end_turn')

        def stream(self, **kwargs):
            provider_calls.append(deepcopy(kwargs))
            return SyntheticStream(kwargs)

    class Client:
        def __init__(self, **kwargs):
            import httpx
            self._http = SimpleNamespace(timeout=httpx.Timeout(450))
            self.messages = Messages()
    monkeypatch.setattr(base, 'OpenRouterClient', Client)
    monkeypatch.setattr(llm_client, 'OpenRouterClient', Client)
    monkeypatch.setattr(trade_idea, 'OpenRouterClient', Client)

    def session_factory(**kwargs):
        return ResearchSession(**kwargs, download=transport)

    stop_after = [0]
    def round_runner(board, number):
        boards.append(board)
        consigliere_multi.run_round(board, number)
        if number == stop_after[0]:
            raise RuntimeError(f'Intentional offline crash after native R{number} checkpoint')

    def execute(run_id, name):
        options = _worker_options(tmp_path)
        options['output_dir'] = tmp_path / name
        options['mandate_loader'] = mandato_pm.profilo_esempio
        options['delivery_sender'] = lambda manifest, language: deliveries.append(manifest['run_id']) or {
            'email_status': 'accepted', 'status': 'accepted', 'message_id': 'offline-delivery-only'}
        return trade_idea.execute_trade_idea(run_id, store=current, **options,
            source_qualifier=lambda *_a, **_k: model.recheck_accepted_sources(admission,
                IDENTITY['ticker'], IDENTITY, archive_root=tmp_path, allow_historical=True),
            preparer_binder=lambda *_a: (None, {'status': 'explicit_author_only'}),
            source_session_factory=session_factory, document_archive_root=tmp_path,
            round_runner=round_runner, catalog_fetcher=lambda: priced['catalog_snapshot'])

    first = execute(parent, 'parent')
    assert first['run']['technical_status'] == 'incomplete', first['run']['reason']
    assert 'native R0' in first['run']['reason'], first['run']['reason']
    assert len(transport.requests) == 2 and not builds
    assert first['cost']['requests'] == len(provider_calls) and first['cost']['unknown_requests'] == 0
    first_paid_calls = len(provider_calls)
    first_run = deepcopy(current.get_run(parent))
    child = current.create_continuation(parent, idempotency_key='source-resume-once',
                                       authorize_new_requests=True)['run']['id']
    stop_after[0] = 1
    second = execute(child, 'child')
    assert 'native R1' in second['run']['reason'], second['run']['reason']
    assert len(transport.requests) == 2 and len(builds) == 1
    assert len(provider_calls) > first_paid_calls
    assert builds[0]['valuation_usability']['usable'] is True
    workbook = Path(builds[0]['path'])
    assert workbook.is_file() and sha256(workbook.read_bytes()).hexdigest() == builds[0]['workbook_sha256']
    author_rows = deepcopy(second['progress']['checkpoint']['data']['_model_consultations'])
    assert len(author_rows) == 5 and all(row['fundamentals_decision']['decision'] == 'incorporated' for row in author_rows)
    second_paid_calls = len(provider_calls)
    grandchild = current.create_continuation(child, idempotency_key='source-resume-twice',
                                            authorize_new_requests=True)['run']['id']
    stop_after[0] = None
    third = execute(grandchild, 'grandchild')
    assert third['run']['technical_status'] == 'completed', third['run']['reason']
    assert third['result']['judgment'] == 'rejected'
    assert len(provider_calls) > second_paid_calls and len(transport.requests) == 2 and len(builds) == 1
    assert all('Round 0.' not in str(call['messages'][0]) and 'Round 1.' not in str(call['messages'][0])
               for call in provider_calls[second_paid_calls:])
    assert third['cost']['requests'] == len(provider_calls)
    assert third['cost']['unknown_requests'] == 0 and third['cost']['budget_limit_usd'] == '30'
    assert third['progress']['checkpoint']['data']['_model_consultations'] == author_rows
    assert third['progress']['valuation_results'][IDENTITY['ticker']]['generation_id'] == builds[0]['generation_id']
    manifest = deepcopy(third['artifacts'])
    assert manifest['complete_package_status'] == 'ready'
    assert {item['kind'] for item in manifest['artifacts']} == {'pdf', 'xlsx'}
    assert third['email']['status'] == 'accepted' and deliveries == [grandchild]
    assert all(row['status'] == 'complete' for row in third['progress']['checkpoint']['specialist_checkpoints'].values())
    final_provider_count = len(provider_calls)
    # Missing final artifacts are recovered byte-for-byte from the sealed vault.
    # The second recovery must preserve both the package and all paid work.
    for receipt in manifest['exact_artifact_receipts']:
        path = Path(receipt['path'])
        assert path.is_relative_to(tmp_path)
        path.unlink()
    for _ in range(2):
        trade_idea.deliver_trade_idea(current, grandchild, output_dir=tmp_path / 'grandchild', send_email=False)
        recovered = current.get_run(grandchild)
        assert recovered['artifacts'] == manifest
        assert recovered['states']['artifacts'] == 'ready'
        for receipt in manifest['exact_artifact_receipts']:
            assert sha256(Path(receipt['path']).read_bytes()).hexdigest() == receipt['sha256']
    assert len(provider_calls) == final_provider_count and deliveries == [grandchild]
    assert len(transport.requests) == 2 and len(builds) == 1
    assert current.get_accepted_request(parent) == initial_request
    assert current.get_run(parent)['progress'] == first_run['progress']
    for run_id in (parent, child, grandchild):
        assert current.get_run(run_id)['run']['authorization'] == grant(admission)
        assert current.get_run(run_id)['run']['source_qualification'] == admission
    with sqlite3.connect(migrated) as conn:
        decisions = conn.execute('SELECT * FROM decisions ORDER BY id').fetchall()
        assert decisions[:len(original_decisions)] == original_decisions
        assert len(decisions) == len(original_decisions) + 1
        assert conn.execute('SELECT count(*) FROM memos WHERE id=?', (third['run']['memo_id'],)).fetchone()[0] == 1
    (tmp_path / 'native-research-proof.json').write_text(json.dumps({
        'run_ids': [parent, child, grandchild], 'provider_requests': len(provider_calls),
        'source_gets': len(transport.requests), 'workbook_generations': len(builds),
        'fake_delivery_calls': len(deliveries), 'author_decisions_preserved': len(author_rows),
        'memo_id': third['run']['memo_id'], 'manifest': manifest,
        'root_grant_fingerprint': admission['fingerprint'],
        'recovered_twice': True}, ensure_ascii=False, indent=2), encoding='utf-8')
