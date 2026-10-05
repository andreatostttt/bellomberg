"""Ammissione HTTP research (grant esatto e conferma di spesa) e contratto Excel archiviato.

La vecchia ricerca nativa con compilazione reale del workbook e' in quarantena in
archive/private/attic/tests_excel_archiviato_20261005/test_trade_idea_research_runtime_legacy.py (Excel archiviato 03/10).
"""
from copy import deepcopy
import json

from bellomberg.agents import trade_idea
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.valuation import trade_idea_model as model
from test_trade_idea_source_research import frozen_clock, grant
from test_trade_idea_economic import IDENTITY, providers_for
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_store import db_path, migrated, store


def test_http_research_admission_requires_exact_grant_and_cost_confirmation(migrated, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api import trade_idea_routes as routes
    current, workers = store(migrated), []
    priced = _priced_request(budget='30')
    # Contratto attuale (Excel archiviato 03/10): l'ammissione HTTP e' solo research con
    # policy di esecuzione corrente, quindi il catalogo deve confermare gli effort richiesti.
    for row in priced['catalog_snapshot']['models'].values():
        row['supported_efforts'] = ['low', 'medium', 'max']
    monkeypatch.setattr(model, '_free_providers', lambda *_args: providers_for())
    monkeypatch.setattr(routes, 'datetime', model.datetime)
    modes = []

    def preflight(ticker, view, origin, budget, **options):
        modes.append(options.get('analysis_mode'))
        return trade_idea.preflight_trade_idea(ticker, view, origin, budget,
            catalog_fetcher=lambda: priced['catalog_snapshot'], key_checker=lambda: None,
            mandate_loader=lambda: {}, active_checker=options['active_checker'],
            identity_resolver=options.get('identity_resolver', lambda _: deepcopy(IDENTITY)),
            **{key: value for key, value in options.items() if key in
               ('source_qualifier', 'archive_root', 'document_sources',
                'analysis_mode', 'execution_policy')})

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
        assert checked.json()['analysis_mode'] == RESEARCH_ANALYSIS_MODE
        assert checked.json()['preparation'] == {'required': False, 'paid': False, 'status': 'not_required'}
        assert 'source_report' not in checked.text
        # Grant research esatto: solo comitato, nessuna preparazione/revisione Excel.
        accepted_grant = {**grant(qualification), 'activities': ['committee']}
        request = {**body, 'authorization': accepted_grant, 'cost_acknowledged': False,
                   'idempotency_key': 'ordinary-research'}
        assert client.post('/trade-ideas/runs', json=request).status_code == 428
        request['cost_acknowledged'] = True
        invalid = deepcopy(request)
        invalid['authorization']['activities'] = ['committee', 'model_preparation']
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
    assert modes and set(modes) == {RESEARCH_ANALYSIS_MODE}
    saved = current.get_accepted_request(run_id)
    assert saved['analysis_mode'] == RESEARCH_ANALYSIS_MODE
    assert saved['source_qualification']['source_report']['documents'] == []
    assert saved['source_qualification']['status'] == 'research_required'
    assert saved['source_qualification']['analysis_mode'] == RESEARCH_ANALYSIS_MODE
    assert saved['authorization'] == accepted_grant
    assert current.get_run(run_id)['cost']['requests'] == 0
    receipts = list((tmp_path / 'trade_idea_preflights' / 'diagnostics').glob('*.json'))
    # Native start must have run the real source-admission recheck, not merely
    # trusted a public DTO or accepted an empty catalog without a signed grant.
    assert any(json.loads(path.read_text(encoding='utf-8'))['status'] == 'verified' for path in receipts)


def test_pre_research_native_model_run_is_refused_before_any_paid_work(migrated, tmp_path, monkeypatch):
    # La vecchia ricerca nativa con compilazione reale del workbook e' in archive/private/attic/tests_excel_archiviato_20261005/
    # test_trade_idea_research_runtime_legacy.py. Una run accettata con quel contratto
    # (research_required senza analysis_mode, grant con model_preparation) oggi e' rifiutata.
    from test_trade_idea_pipeline import _assert_excel_run_refused_before_work
    monkeypatch.setattr(model, '_free_providers', lambda *_args: providers_for())
    priced = _priced_request(budget='30')
    checked = trade_idea.preflight_trade_idea(IDENTITY['ticker'], 'Frozen sourced thesis', 'manual', '30',
        catalog_fetcher=lambda: priced['catalog_snapshot'], identity_resolver=lambda _: deepcopy(IDENTITY),
        active_checker=lambda: False, key_checker=lambda: None, mandate_loader=lambda: {}, archive_root=tmp_path)
    assert checked['ok'] and checked['source_qualification']['status'] == 'research_required'
    admission = checked['_source_qualification']
    assert admission.get('analysis_mode') is None
    current = store(migrated)
    request = {**priced, 'ticker': IDENTITY['ticker'], 'company_name': IDENTITY['name'],
        'exchange': IDENTITY['exchange'], 'currency': IDENTITY['currency'], 'language': 'en',
        'view_text': 'Frozen sourced thesis', 'source_qualification': admission, 'authorization': grant(admission)}
    run_id = current.create_run(request, idempotency_key='native-primary-research')['run']['id']
    _assert_excel_run_refused_before_work(current, run_id, tmp_path)
