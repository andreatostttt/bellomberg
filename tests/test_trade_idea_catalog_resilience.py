"""The free model catalog cannot end a paid run on a network blip; changed content still blocks."""
from urllib.error import URLError

import pytest

from bellomberg.agents import trade_idea
from test_trade_idea_paid_capo_checkpoint import db_path, migrated, case, v2_request, save_cp  # noqa: F401
from test_trade_idea_policy_runtime import catalog


def _gate(migrated, key, fetcher):
    current, _, cp, *_ = case(migrated)
    ident = current.create_run(v2_request(), idempotency_key=key)['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    return trade_idea.TradeIdeaBudgetGate(current, ident, token, catalog(), catalog_fetcher=fetcher)


def _capo_call():
    return {'model': trade_idea.model_for_role('capo'), 'max_tokens': 32768,
            'thinking': {'type': 'effort', 'effort': 'low'}, 'system': 'S',
            'messages': [{'role': 'user', 'content': 'U'}]}


def test_unreachable_catalog_uses_the_accepted_snapshot_and_declares_it(migrated):
    def offline():
        raise URLError('temporary DNS failure')
    gate = _gate(migrated, 'catalog-offline', offline)
    request_id, _ = gate._reserve(_capo_call(), 'capo')
    assert request_id
    assert gate.catalog_notices and 'snapshot accettato' in gate.catalog_notices[0]['notice']


def test_changed_catalog_content_still_blocks_before_spending(migrated):
    def changed():
        raise ValueError('modello Trade Idea assente dal catalogo')
    gate = _gate(migrated, 'catalog-changed', changed)
    with pytest.raises(ValueError):
        gate._reserve(_capo_call(), 'capo')
    assert not gate.catalog_notices
