"""Ordinary startup and historical-model routes must not revive Excel work."""
import pytest
from test_trade_idea_store import db_path, migrated


def test_startup_and_tracking_never_construct_paid_model_runtime(tmp_path, monkeypatch):
    from bellomberg.valuation import valuation_automation_installation as installation
    touched = []
    def forbidden():
        touched.append('runtime')
        pytest.fail('Archive mode cannot instantiate the model runtime')
    monkeypatch.setattr(installation, 'installation_runtime', forbidden)
    startup = installation.start_installation(tmp_path / 'does-not-exist.db')
    assert startup['runner'] is None and startup['state']['status'] == 'archived'
    assert installation.notify_tracking(tmp_path / 'does-not-exist.db', 'TEST', 'portfolio')['status'] == 'archived'
    assert installation.notify_tracking(tmp_path / 'does-not-exist.db', 'TEST', 'watchlist')['status'] == 'archived'
    assert touched == [] and not list(tmp_path.iterdir())


@pytest.mark.parametrize('action', ['refresh', 'variants', 'lock'])
def test_archived_workbook_mutations_are_refused_before_storage(action, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api.valuation_automation_routes import create_valuation_automation_router
    def forbidden():
        pytest.fail('Archive mutation reached storage or worker')
    app = FastAPI()
    app.include_router(create_valuation_automation_router(lambda: 'offline',
        db_provider=forbidden, roots=[tmp_path], automation_provider=forbidden))
    with TestClient(app) as client:
        reply = client.post('/valuation/models/TEST/' + action, json={})
    assert reply.status_code == 409 and reply.json()['detail']['code'] == 'excel_archived'


def test_legacy_trade_idea_resume_and_worker_leave_history_unchanged(migrated, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api import trade_idea_routes as routes
    from bellomberg.agents import trade_idea
    from bellomberg.storage.trade_idea_store import RunConflict
    from test_trade_idea_store import store, request
    current = store(migrated)
    rid = current.create_run(request(), idempotency_key='archived-run')['run']['id']
    before = current.get_run(rid)
    def forbidden(*_a, **_k):
        pytest.fail('Archived analysis attempted a continuation or worker claim')
    monkeypatch.setattr(routes, '_store', lambda *_a, **_k: current)
    monkeypatch.setattr(routes, '_spawn_worker', forbidden)
    monkeypatch.setattr(current, 'create_continuation', forbidden)
    monkeypatch.setattr(current, 'claim_run', forbidden)
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: 'offline', db_path=migrated,
        source_archive_root=migrated.parent / 'archive')
    with TestClient(app) as client:
        reply = client.post('/trade-ideas/runs/' + rid + '/resume',
            json={'cost_acknowledged': True, 'idempotency_key': 'archived-child'})
    assert reply.status_code == 409 and 'archiv' in reply.text.lower()
    with pytest.raises(RunConflict, match='archiv'):
        trade_idea.execute_trade_idea(rid, store=current)
    assert current.get_run(rid) == before and current.list_runs()['total'] == 1


def test_chat_cannot_reactivate_the_archived_workbook_tool(monkeypatch):
    from bellomberg.agents import chat_tools
    from bellomberg.valuation import sector_analysis, dcf_engine
    def forbidden(*_a, **_k):
        pytest.fail('Chat acquired sources or generated an archived workbook')
    monkeypatch.setattr(sector_analysis, 'prepare_sector_analysis', forbidden)
    monkeypatch.setattr(dcf_engine, 'generate_valuation', forbidden)
    for agent in ('fundamentals', 'capo'):
        assert 'get_valuation' not in {tool['name'] for tool in chat_tools.get_tools_for_agent(agent)}
        assert chat_tools.dispatch('get_valuation', {'ticker': 'TEST'}, caller='chat:' + agent)['status'] == 'archived'
    assert chat_tools.dispatch('get_valuation', {'ticker': 'TEST'})['status'] == 'archived'


@pytest.mark.parametrize('endpoint', ['actions', 'actions/old-action/recover'])
def test_legacy_workspace_cannot_generate_or_recover_model_actions(tmp_path, endpoint):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bellomberg.api.trade_idea_workspace_routes import install_trade_idea_workspace_routes
    calls = []
    class ForbiddenWorkspace:
        def __getattr__(self, _name):
            calls.append(_name)
            raise RuntimeError('Archived workspace mutation reached service, store or compiler')
    app = FastAPI()
    install_trade_idea_workspace_routes(app, lambda: 'offline',
        db_path=tmp_path / 'absent.db', workspace=ForbiddenWorkspace())
    with TestClient(app) as client:
        reply = client.post('/trade-ideas/runs/historical/workspace/' + endpoint,
            json={'request_id': 'offline-action', 'generation_id': 'historical-generation',
                'kind': 'simulate', 'data': {}})
    assert reply.status_code == 409, reply.text
    assert reply.json()['detail']['code'] == 'excel_archived'
    assert calls == [] and not list(tmp_path.iterdir())
