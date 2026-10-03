"""A crash after a validated Capo keeps the analysis but never ships a judgment or proposal."""
from bellomberg.agents import trade_idea
from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: F401
from test_trade_idea_delivery import smtp  # noqa: F401
from test_trade_idea_source_research import frozen_clock  # noqa: F401
from test_trade_idea_store import db_path, migrated  # noqa: F401


def test_post_capo_exception_salvage_is_incomplete_without_proposal_and_email(no_workbook_case, monkeypatch):
    case = no_workbook_case
    case.state.update(judgment='favorable', source_gaps=False)

    def boom(*args, **kwargs):
        raise RuntimeError('post-capo crash in numeric binding')
    monkeypatch.setattr(trade_idea, '_numeric_claim_gaps', boom)
    ident = case.current.create_run(case.request, idempotency_key='salvage-never-promotes')['run']['id']
    detail = case.execute(ident, 'salvage')
    assert detail['run']['technical_status'] == 'incomplete'
    assert detail['run']['destination'] in (None, 'research') or detail['run']['destination'].get('kind') != 'dcn'
    assert detail['result']['judgment'] == 'incomplete' and detail['result']['proposal'] is None
    assert any('post-capo crash' in gap for gap in detail['result']['data_gaps'])
    assert not case.smtp[0], [message['Subject'] for message in case.smtp[0]]
