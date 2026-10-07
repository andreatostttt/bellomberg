"""Native producer/storage contracts: trusted policy, legacy and paid reservation."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bellomberg.agents.trade_idea import _checkpoint_contract
from bellomberg.core.mandato_pm import MANDATE_TEXT_POLICY as POLICY, MANDATE_TEXT_POLICY_KEY as KEY
from bellomberg.core.trade_idea_policy import role_effort
from bellomberg.storage import trade_idea_store as storage
from test_trade_idea_store import db_path, migrated, store
from test_trade_idea_paid_capo_checkpoint import v2_request, DESKS


def legacy_factory(monkeypatch):
    """Historical server factory: omit injection BEFORE INSERT, never rewrite rows."""
    tree = ast.parse(Path(storage.__file__).read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'TradeIdeaStore')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'create_run')
    removed = 0
    for node in ast.walk(method):
        if isinstance(node, ast.Dict):
            for i in reversed(range(len(node.keys))):
                key = node.keys[i]
                if isinstance(key, ast.Name) and key.id == 'MANDATE_TEXT_POLICY_KEY':
                    del node.keys[i]; del node.values[i]; removed += 1
    assert removed == 1
    scope = dict(vars(storage))
    exec(compile(ast.Module(body=[method], type_ignores=[]), storage.__file__, 'exec'), scope)
    monkeypatch.setattr(storage.TradeIdeaStore, 'create_run', scope['create_run'])


def accepted(migrated, policy, monkeypatch, legacy=False):
    payload = v2_request()
    payload['execution_policy'] = 'trade-idea-research/' + str(policy)
    for role, model in payload['models'].items():
        model['reasoning_effort'] = role_effort(payload, role)
    if legacy:
        legacy_factory(monkeypatch)
    current = store(migrated)
    ident = current.create_run(payload, idempotency_key='native-policy')['run']['id']
    board = SimpleNamespace(run_id=ident, budget_gate=SimpleNamespace(store=current),
                            source_admission=payload['source_qualification'])
    return current, ident, board, payload


def row_for(current, ident):
    with current._connect(read_only=True) as conn:
        return dict(current._row(conn, ident))


@pytest.mark.parametrize('policy', [2, 3, 4])
@pytest.mark.parametrize('legacy', [False, True])
def test_native_producer_equals_storage_and_reserves_exact_request(migrated, monkeypatch, policy, legacy):
    current, ident, board, payload = accepted(migrated, policy, monkeypatch, legacy)
    row = row_for(current, ident)
    contract = _checkpoint_contract(board)
    assert (KEY not in contract) if legacy else contract[KEY] == POLICY
    assert storage._native_checkpoint_contract(row, json.loads(row['request_json'])) == contract
    token = current.claim_run(ident)
    data = {desk: {'1': desk + ' first report', '2': desk + ' final report'} for desk in DESKS}
    data.update({key: {} for key in ('_research_thesis', '_research_review', '_sizing',
                                     '_decision_context', '_candidate_quote_initial')})
    cp = {'version': 1, 'contract': contract, 'data': data}
    current.update_progress(ident, token, 'capo', {'checkpoint': cp, 'checkpoint_sha256': storage._digest(cp)})
    body = {'model': payload['models']['capo']['model'], 'messages': [{'role': 'user', 'content': 'Frozen native request'}]}
    before = deepcopy(body)
    assert current.reserve_cost(ident, 'native-capo', 'capo', body['model'], '1',
        request_sha256=storage._digest(body), worker_token=token, capo_request_body=body)
    with current._connect(read_only=True) as conn:
        saved = json.loads(conn.execute("SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='capo_request_saved'", (ident,)).fetchone()[0])
    assert saved['request_body'] == before and body == before
    assert current.get_run(ident)['cost']['requests'] == 1


@pytest.mark.parametrize('policy', [2, 3, 4])
@pytest.mark.parametrize('bad', [None, '', False, {}, 'mandate-sizing-labels/2'])
def test_present_invalid_context_refused_and_request_field_cannot_supply_policy(migrated, monkeypatch, policy, bad):
    current, ident, board, payload = accepted(migrated, policy, monkeypatch)
    row = row_for(current, ident)
    context = json.loads(row['context_json']); context[KEY] = bad
    row['context_json'] = json.dumps(context)
    payload[KEY] = POLICY
    with pytest.raises(ValueError, match='Unsupported mandate text policy'):
        storage._native_checkpoint_contract(row, payload)


@pytest.mark.parametrize('policy', [2, 3, 4])
def test_user_request_cannot_upgrade_legacy_context(migrated, monkeypatch, policy):
    current, ident, board, payload = accepted(migrated, policy, monkeypatch, legacy=True)
    payload[KEY] = POLICY
    contract = storage._native_checkpoint_contract(row_for(current, ident), payload)
    assert KEY not in contract and contract == _checkpoint_contract(board)


@pytest.mark.parametrize('legacy', [False, True])
def test_paid_body_and_cost_survive_two_continuations_without_upgrade(migrated, monkeypatch, legacy):
    from test_trade_idea_paid_capo_checkpoint import case, child, rows, save_cp
    if legacy:
        legacy_factory(monkeypatch)
    current, parent, checkpoint, body, response = case(migrated)
    assert (KEY not in checkpoint['contract']) if legacy else checkpoint['contract'][KEY] == POLICY
    before_parent = rows(migrated, 'SELECT * FROM trade_idea_runs WHERE id=?', (parent,))
    before_costs = rows(migrated, 'SELECT * FROM trade_idea_costs')
    original_parent = parent
    for n in range(2):
        ident, token = child(current, parent, 'native-paid-resume-' + str(n))
        public = current.get_run(ident)['run']
        assert (KEY not in public) if legacy else public[KEY] == POLICY
        save_cp(current, ident, token, deepcopy(checkpoint))
        assert current.reusable_capo_response(ident, token) == response
        assert current.reusable_capo_response(ident, token) == response
        current.finish_run(ident, token, None, 'incomplete', reason='Synthetic repeat resume')
        parent = ident
    assert rows(migrated, 'SELECT * FROM trade_idea_costs') == before_costs
    assert rows(migrated, 'SELECT * FROM trade_idea_runs WHERE id=?', (original_parent,)) == before_parent
    saved = rows(migrated, "SELECT payload_json FROM trade_idea_events WHERE kind='capo_request_saved'")
    assert len(saved) == 1 and json.loads(saved[0]['payload_json'])['request_body'] == body


@pytest.mark.parametrize('policy', [2, 3, 4])
@pytest.mark.parametrize('fault', ['marker', 'hash'])
def test_exact_contract_and_checksum_still_block_tampering(migrated, monkeypatch, policy, fault):
    current, ident, board, payload = accepted(migrated, policy, monkeypatch)
    row = row_for(current, ident)
    cp = {'version': 1, 'contract': _checkpoint_contract(board), 'data': {}}
    if fault == 'marker':
        del cp['contract'][KEY]
    row['progress_json'] = json.dumps({'checkpoint': cp,
        'checkpoint_sha256': storage._digest(cp) if fault == 'marker' else '0' * 64})
    with pytest.raises(storage.RunConflict, match='checkpoint integrity or accepted contract differs'):
        storage._capo_request_bindings(row)
