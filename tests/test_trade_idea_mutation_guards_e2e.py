"""Test proposti (e2e) dal banco mutazioni (solo nella copia esportata)."""
from bellomberg.agents import trade_idea
from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: F401
from test_trade_idea_delivery import smtp  # noqa: F401
from test_trade_idea_source_research import frozen_clock  # noqa: F401
from test_trade_idea_store import db_path, migrated  # noqa: F401


def test_p_gap_capo_sees_declared_gap_objections_flagged_and_annex_printed(no_workbook_case):
    case = no_workbook_case
    case.state['truncate'] = ('crypto', 1)
    ident = case.current.create_run(case.request, idempotency_key='p-gap')['run']['id']
    detail = case.execute(ident, 'pgap')
    assert detail['result']['judgment'] != 'incomplete', detail['run']['reason']
    # D8: the Capo prompt states the declared gap.
    assert any('[DECLARED GAP]' in str(call.get('messages')) for call in case.providers)
    # D11: objections addressed to the gap desk carry the gap reason, with no completion turn.
    data = detail['progress']['checkpoint']['data']
    crypto = [row for row in data['_objections'] if row['objection']['desk'] == 'crypto']
    assert crypto and all(row.get('unanswered') == 'addressed desk unavailable (declared gap)' for row in crypto)



def test_p_salvage_keeps_capo_analysis_and_routes_to_research(no_workbook_case, monkeypatch):
    case = no_workbook_case
    case.state.update(judgment='favorable', source_gaps=False)

    def boom(*args, **kwargs):
        raise RuntimeError('post-capo crash in numeric binding')
    monkeypatch.setattr(trade_idea, '_numeric_claim_gaps', boom)
    ident = case.current.create_run(case.request, idempotency_key='p-salvage')['run']['id']
    detail = case.execute(ident, 'psalvage')
    assert detail['result']['judgment'] == 'incomplete' and detail['result']['proposal'] is None
    # S2: the first candidate (the Capo's own analysis) is the one persisted, not the desk fallback.
    assert not any(s['key'].startswith('desk_') for s in detail['result']['dossier'])
    # S4: the labelled partial is routed to research.
    assert (detail['run']['destination'] or {}).get('kind') == 'research', detail['run']['destination']
