"""The ordinary authenticated resume route carries an explicit selected receipt."""
import pytest

from bellomberg.api import trade_idea_routes as routes
from test_trade_idea_run_controls_api import admission, db_path, migrated


@pytest.mark.parametrize("acknowledged", [True, False])
def test_resume_carries_exact_truncated_request_only_after_cost_consent(admission, monkeypatch, acknowledged):
    client, current, _, _, _, workers = admission
    calls = []
    monkeypatch.setattr(current, "get_run", lambda _: {"recovery": {}})
    monkeypatch.setattr(routes, "active_paid_reason", lambda *_a, **_k: None)
    def create(parent, **kwargs):
        calls.append((parent, kwargs))
        return {"run": {"id": "synthetic-child", "technical_status": "accepted"}, "created": True}
    monkeypatch.setattr(current, "create_continuation", create)
    selected = "a" * 32
    response = client.post("/trade-ideas/runs/synthetic-parent/resume", json={
        "idempotency_key": "explicit-report-recovery", "cost_acknowledged": acknowledged,
        "recover_truncated_request_id": selected})
    if not acknowledged:
        assert response.status_code == 428 and calls == workers == []
    else:
        assert response.status_code == 202, response.text
        assert calls == [("synthetic-parent", {"idempotency_key": "explicit-report-recovery",
            "authorize_new_requests": True, "recover_truncated_request_id": selected})]
        assert workers == ["synthetic-child"]


def test_resume_cannot_supply_a_server_owned_recovery_descriptor(admission):
    client, current, _, _, _, workers = admission
    response = client.post("/trade-ideas/runs/synthetic-parent/resume", json={
        "idempotency_key": "forged", "cost_acknowledged": True,
        "specialist_response_recovery": {"replacement_max_tokens": 128000}})
    assert response.status_code == 422 and workers == []


def test_recovery_request_remains_authenticated(admission):
    client, current, _, _, _, workers = admission
    response = client.post("/trade-ideas/runs/synthetic-parent/resume", json={
        "idempotency_key": "unauthenticated", "cost_acknowledged": True,
        "recover_truncated_request_id": "a" * 32}, headers={"X-BB-Token": "expired"})
    assert response.status_code == 401 and workers == []


@pytest.mark.parametrize('price_refresh', [False, True])
@pytest.mark.parametrize('selected', [None, 'b' * 32])
def test_explicit_price_refresh_is_forwarded_only_when_true_and_can_combine_with_report(admission, monkeypatch, price_refresh, selected):
    client, current, _, _, _, workers = admission
    calls = []
    monkeypatch.setattr(current, 'get_run', lambda _: {'recovery': {}})
    monkeypatch.setattr(routes, 'active_paid_reason', lambda *_a, **_k: None)
    def create(parent, **kwargs):
        calls.append((parent, kwargs))
        return {'run': {'id': 'price-child', 'technical_status': 'accepted'}, 'created': True}
    monkeypatch.setattr(current, 'create_continuation', create)
    body = {'idempotency_key': 'price-consent', 'cost_acknowledged': True,
        'authorize_price_refresh': price_refresh}
    if selected:
        body['recover_truncated_request_id'] = selected
    response = client.post('/trade-ideas/runs/selected-parent/resume', json=body)
    assert response.status_code == 202, response.text
    expected = {'idempotency_key': 'price-consent', 'authorize_new_requests': True,
        'recover_truncated_request_id': selected}
    if price_refresh:
        expected['authorize_price_refresh'] = True
    assert calls == [('selected-parent', expected)] and workers == ['price-child']


def test_price_refresh_requires_cost_consent_and_preserves_store_accounting_blocks(admission, monkeypatch):
    client, current, _, _, _, workers = admission
    calls = []
    monkeypatch.setattr(current, 'get_run', lambda _: {'recovery': {}})
    monkeypatch.setattr(routes, 'active_paid_reason', lambda *_a, **_k: None)
    def blocked(*args, **kwargs):
        calls.append((args, kwargs))
        raise routes.RunConflict('unknown request cost remains unresolved')
    monkeypatch.setattr(current, 'create_continuation', blocked)
    body = {'idempotency_key': 'cannot-override', 'cost_acknowledged': False,
        'authorize_price_refresh': True}
    assert client.post('/trade-ideas/runs/selected-parent/resume', json=body).status_code == 428
    assert calls == workers == []
    body['cost_acknowledged'] = True
    response = client.post('/trade-ideas/runs/selected-parent/resume', json=body)
    assert response.status_code == 409 and 'unknown request cost' in response.text
    assert len(calls) == 1 and workers == []


@pytest.mark.parametrize('bad', ['true', 1, None])
def test_price_refresh_consent_must_be_an_explicit_boolean(admission, bad):
    client, _, _, _, _, workers = admission
    response = client.post('/trade-ideas/runs/selected-parent/resume', json={
        'idempotency_key': 'bad-price-consent', 'cost_acknowledged': True,
        'authorize_price_refresh': bad})
    assert response.status_code == 422 and workers == []
