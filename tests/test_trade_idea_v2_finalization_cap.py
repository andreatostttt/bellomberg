"""A V2 Capo finalization grant is admitted with its own cap, never below the accepted one."""
import pytest

from bellomberg.agents import trade_idea
from test_trade_idea_paid_capo_checkpoint import db_path, migrated, case, v2_request, save_cp  # noqa: F401
from test_trade_idea_policy_runtime import gate


def _budget(migrated, key):
    current, _, cp, *_ = case(migrated)
    ident = current.create_run(v2_request(), idempotency_key=key)['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    return gate(current, ident, token)


def _call(cap):
    return {'model': trade_idea.model_for_role('capo'), 'max_tokens': cap,
            'thinking': {'type': 'effort', 'effort': 'low'}, 'system': 'S',
            'messages': [{'role': 'user', 'content': 'U'}]}


def test_v2_finalization_grant_cap_is_not_refused_as_a_policy_mismatch(migrated):
    budget = _budget(migrated, 'v2-final-cap')
    grant = {'max_tokens': 32768, 'thinking': {'type': 'effort', 'effort': 'low'},
             'context_projection': 'sealed_all_rounds_red_team_dedup_v1'}
    budget.capo_finalization = lambda: grant
    try:
        budget._reserve(_call(32768), 'capo')
    except ValueError as exc:
        assert 'output cap differs' not in str(exc), exc


def test_v2_finalization_call_must_use_exactly_the_granted_cap(migrated):
    budget = _budget(migrated, 'v2-final-mismatch')
    grant = {'max_tokens': 32768, 'thinking': {'type': 'effort', 'effort': 'low'},
             'context_projection': 'sealed_all_rounds_red_team_dedup_v1'}
    budget.capo_finalization = lambda: grant
    with pytest.raises(ValueError):
        budget._reserve(_call(20000), 'capo')
