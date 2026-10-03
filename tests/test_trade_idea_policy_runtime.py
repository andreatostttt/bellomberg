"""Offline real budget/store integration; providers must never reach a socket."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea, red_team, chat_tools
from test_trade_idea_paid_capo_checkpoint import db_path, migrated, case, child, rows, v2_request, save_cp
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_capo_finalization_dispatch import research_board


def catalog():
    snapshot = _priced_request()['catalog_snapshot']
    for row in snapshot['models'].values():
        row['supported_efforts'] = ['low', 'medium', 'max']
    return snapshot


def gate(current, ident, token):
    return trade_idea.TradeIdeaBudgetGate(current, ident, token, catalog(), catalog_fetcher=catalog)


def test_native_reservation_freezes_exact_wire_before_any_transport(migrated):
    current, _, cp, *_ = case(migrated)
    ident = current.create_run(v2_request(), idempotency_key='native-reservation')['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    budget = gate(current, ident, token)
    kwargs = {'model': trade_idea.model_for_role('capo'), 'max_tokens': 32768,
        'thinking': {'type': 'effort', 'effort': 'low'}, 'system': 'Original system',
        'messages': [{'role': 'user', 'content': 'Original economic evidence'}]}
    request_id, safe = budget._reserve(kwargs, 'capo')
    import json
    receipt = rows(migrated, "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='capo_request_saved'", (ident,))
    assert len(receipt) == 1
    saved = json.loads(receipt[0]['payload_json'])
    assert saved['request_id'] == request_id
    assert saved['request_body'] == trade_idea.costruisci_corpo(**safe)
    for altered in ({**kwargs, 'max_tokens': 128000},
                    {**kwargs, 'thinking': {'type': 'effort', 'effort': 'max'}}):
        with pytest.raises(ValueError): budget._reserve(altered, 'capo')
    assert len(rows(migrated, 'SELECT * FROM trade_idea_costs WHERE run_id=?', (ident,))) == 1


def test_native_capo_consumes_paid_fence_before_history_or_prompt_and_never_double_charges(
        migrated, research_board, monkeypatch):
    current, parent, cp, _, response = case(migrated, record_public=True)
    ident, token = child(current, parent, 'native-resume')
    board = research_board
    board.data = deepcopy(cp['data'])
    board.data = {key: {int(n): value for n, value in content.items()} if key in (*trade_idea.TRADE_IDEA_DESKS, '_red_team') else content
                  for key, content in board.data.items()}
    board.run_id, board.pm_view = ident, current.get_run(ident)['run']['view_text']
    board.execution_policy = 'trade-idea-research/2'
    board.budget_gate = gate(current, ident, token)
    # The receipt fixture deliberately contains abbreviated desk text. This test
    # isolates native billing/replay/parsing, while committee gates have own tests.
    monkeypatch.setattr(trade_idea, '_require_final_desk_models', lambda _b: [])
    monkeypatch.setattr(red_team, 'motivo_critica_non_utilizzabile', lambda _r: None)
    def forbidden(*_a, **_k): pytest.fail('Paid response must be reused before dynamic prompt/provider')
    monkeypatch.setattr(chat_tools, '_compatta_portfolio_live', forbidden)
    monkeypatch.setattr(trade_idea, 'candidate_model_context', forbidden)
    monkeypatch.setattr(trade_idea, 'OpenRouterClient', forbidden)
    calls = []
    board.record_usage = lambda *args, **kwargs: calls.append((args, kwargs))
    board.write = lambda key, round_n, value: board.data.setdefault(key, {}).__setitem__(round_n, value)
    board.candidate_history = 'New history that differs from the original paid request'
    before = rows(migrated, 'SELECT * FROM trade_idea_costs')
    for _ in range(2):
        result = trade_idea.run_trade_idea_capo(board, portfolio={}, mandate={})
        assert result['judgment'] == 'watch' and result['run_id'] == ident
    assert board.data['_capo'][3] == response['content'][0]['text']
    assert all(args[3] == {} and kwargs['api_calls'] == 0 for args, kwargs in calls)
    assert rows(migrated, 'SELECT * FROM trade_idea_costs') == before
    assert len(rows(migrated, "SELECT id FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (ident,))) == 1
