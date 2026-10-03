"""An explicit amended ceiling travels through the ordinary authenticated route."""
import pytest

from bellomberg.api import trade_idea_routes as routes
from test_trade_idea_run_controls_api import admission, db_path, migrated


@pytest.mark.parametrize('acknowledged', [False, True])
def test_explicit_resume_budget_requires_cost_consent(admission, monkeypatch, acknowledged):
    client, current, _, _, _, workers = admission
    calls = []
    monkeypatch.setattr(current, 'get_run', lambda _: {'run': {'analysis_mode': 'fundamentals_research_v1'}, 'recovery': {}})
    monkeypatch.setattr(routes, 'active_paid_reason', lambda *_a, **_k: None)
    def create(parent, **kwargs):
        calls.append((parent, kwargs))
        return {'run': {'id': 'amended-child', 'technical_status': 'accepted'}, 'created': True}
    monkeypatch.setattr(current, 'create_continuation', create)
    response = client.post('/trade-ideas/runs/selected-parent/resume', json={
        'idempotency_key': 'explicit-total-ceiling', 'cost_acknowledged': acknowledged,
        'budget_limit_usd': '15'})
    if not acknowledged:
        assert response.status_code == 428 and calls == workers == []
    else:
        assert response.status_code == 202, response.text
        assert calls == [('selected-parent', {'idempotency_key': 'explicit-total-ceiling',
            'authorize_new_requests': True, 'recover_truncated_request_id': None,
            'budget_limit_usd': '15'})]
        assert workers == ['amended-child']


def test_amended_budget_does_not_override_an_unknown_cost_block(admission, monkeypatch):
    client, current, _, _, _, workers = admission
    monkeypatch.setattr(current, 'get_run', lambda _: {'run': {'analysis_mode': 'fundamentals_research_v1'}, 'recovery': {}})
    monkeypatch.setattr(routes, 'active_paid_reason', lambda *_a, **_k: None)
    def reject(*_args, **_kwargs):
        raise routes.BudgetBlocked('unresolved provider requests')
    monkeypatch.setattr(current, 'create_continuation', reject)
    response = client.post('/trade-ideas/runs/selected-parent/resume', json={
        'idempotency_key': 'explicit-total-ceiling', 'cost_acknowledged': True,
        'budget_limit_usd': '15'})
    assert response.status_code == 409 and 'unresolved provider requests' in response.text
    assert workers == []


@pytest.mark.parametrize('extra', [{'budget_limit_usd': True},
                                  {'budget_amendment': {'authorize_budget_increase': True}}])
def test_resume_cannot_forge_a_budget_grant_or_use_boolean_as_money(admission, extra):
    client, _, _, _, _, workers = admission
    response = client.post('/trade-ideas/runs/selected-parent/resume', json={
        'idempotency_key': 'forged-budget-grant', 'cost_acknowledged': True, **extra})
    assert response.status_code == 422 and workers == []


@pytest.mark.parametrize('acknowledged', [False, True])
def test_selected_held_rejection_is_forwarded_only_after_cost_consent(admission, monkeypatch, acknowledged):
    client, current, _, _, _, workers = admission
    calls = []
    monkeypatch.setattr(current, 'get_run', lambda _: {'run': {'analysis_mode': 'fundamentals_research_v1'}, 'recovery': {}})
    monkeypatch.setattr(routes, 'active_paid_reason', lambda *_a, **_k: None)
    def create(parent, **kwargs):
        calls.append((parent, kwargs))
        return {'run': {'id': 'held-child', 'technical_status': 'accepted'}, 'created': True}
    monkeypatch.setattr(current, 'create_continuation', create)
    response = client.post('/trade-ideas/runs/selected-parent/resume', json={
        'idempotency_key': 'selected-held-rejection', 'cost_acknowledged': acknowledged,
        'budget_limit_usd': '15', 'resume_with_held_rejection_request_id': 'a' * 32})
    if not acknowledged:
        assert response.status_code == 428 and calls == workers == []
    else:
        assert response.status_code == 202, response.text
        assert calls == [('selected-parent', {'idempotency_key': 'selected-held-rejection',
            'authorize_new_requests': True, 'recover_truncated_request_id': None,
            'budget_limit_usd': '15', 'resume_with_held_rejection_request_id': 'a' * 32})]
        assert workers == ['held-child']


def test_client_cannot_supply_held_rejection_evidence(admission):
    client, _, _, _, _, workers = admission
    response = client.post('/trade-ideas/runs/selected-parent/resume', json={
        'idempotency_key': 'forged-held-evidence', 'cost_acknowledged': True,
        'held_rejection': {'request_id': 'a' * 32, 'proof': {'billable': False}}})
    assert response.status_code == 422 and workers == []
