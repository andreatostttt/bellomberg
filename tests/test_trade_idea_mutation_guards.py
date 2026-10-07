"""Test proposti dal banco mutazioni (solo nella copia esportata)."""
import asyncio
import io
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.llm_client import APIConnectionError
from test_trade_idea_paid_capo_checkpoint import db_path, migrated, case, v2_request, save_cp  # noqa: F401
from test_trade_idea_policy_runtime import gate, catalog
from test_trade_idea_capo_finalization_dispatch import research_board  # noqa: F401

V3 = {'analysis_mode': 'fundamentals_research_v1', 'execution_policy': 'trade-idea-research/3'}


# Q2 ---------------------------------------------------------------------------------
def test_p_q2_v3_short_complete_memo_qualifies():
    from bellomberg.core.trade_idea_policy import report_quality_sufficient
    short = {"status": "ready", "execution_policy": V3['execution_policy'],
             "analytical_pages": 3, "analytical_words": 1200, "content_integrity": "complete"}
    assert report_quality_sufficient(short)
    assert not report_quality_sufficient({**short, "content_integrity": "incomplete"})


# R10 --------------------------------------------------------------------------------
def test_p_r10_annex_pages_are_not_analytical_pages(tmp_path):
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    from test_trade_idea_report_v2 import editorial_fixture
    from test_trade_idea_desk_annex import _data
    run, result = editorial_fixture()
    plain = build_trade_idea_report(run, result, output_path=tmp_path / "plain.pdf")
    run, result = editorial_fixture()
    run["desk_annex"] = trade_idea._desk_annex(_data())
    annexed = build_trade_idea_report(run, result, output_path=tmp_path / "annex.pdf")
    assert annexed["quality"]["total_pages"] > plain["quality"]["total_pages"]
    assert annexed["quality"]["analytical_pages"] == plain["quality"]["analytical_pages"]


# R12 --------------------------------------------------------------------------------
def test_p_r12_summary_cut_mid_sentence_blocks(tmp_path):
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    from test_trade_idea_report_v2 import editorial_fixture
    run, result = editorial_fixture()
    result["summary"] = "Il giudizio resta prudente perche' la conversione di cassa e' incerta e il"
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "cut.pdf")
    assert artifact["status"] == "partial"
    assert any("summary" in reason for reason in artifact["quality"]["reasons"]), artifact["quality"]["reasons"]


# G2 ---------------------------------------------------------------------------------
def test_p_g2_preflight_refuses_capo_cap_not_below_context_length():
    from test_trade_idea_policy_v3 import _catalog
    doc = _catalog(128000)
    doc['models']['capo']['context_length'] = 128000
    checked = trade_idea.preflight_trade_idea(
        'ACME', 'Thesis', 'manual', '10', catalog_fetcher=lambda: doc,
        identity_resolver=lambda ticker: {'ticker': ticker, 'status': 'invalid', 'reason': 'offline test'},
        key_checker=lambda: None, mandate_loader=lambda: {}, active_checker=lambda: False,
        analysis_mode='fundamentals_research_v1', execution_policy=V3['execution_policy'])
    assert 'Tetto di output del Capo' in ' | '.join(checked.get('reasons') or [])


# G10 --------------------------------------------------------------------------------
def test_p_g10_non_lock_error_is_attempted_once(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    attempts = []

    def broken(*a, **k):
        attempts.append(1)
        raise sqlite3.OperationalError("no such table: trade_idea_costs")
    with pytest.raises(sqlite3.OperationalError):
        trade_idea.TradeIdeaBudgetGate.__new__(trade_idea.TradeIdeaBudgetGate)._durable(broken)
    assert attempts == [1]


# G12 --------------------------------------------------------------------------------
def test_p_g12_unknown_marking_goes_through_durable():
    g = trade_idea.TradeIdeaBudgetGate.__new__(trade_idea.TradeIdeaBudgetGate)
    g.run_id, g._requests = "run", {"req": {"request_sha256": "a" * 64, "role": "capo"}}
    seen = []
    g._durable = lambda write, *a, **k: seen.append(write.__name__)

    class Store:
        def mark_cost_unknown(self, *a, **k):
            raise AssertionError("must go through _durable")
    g.store = Store()
    try:
        g._unknown("req", RuntimeError("boom"))
    except AssertionError:
        raise
    except Exception:
        pass
    assert seen[:1] == ["mark_cost_unknown"]


# G16 --------------------------------------------------------------------------------
def _connect_error():
    exc = APIConnectionError('ConnectError: connection refused')
    exc.transport_phase = 'connect'
    return exc


def _capo_call():
    return {'model': trade_idea.model_for_role('capo'), 'max_tokens': 32768,
            'thinking': {'type': 'effort', 'effort': 'low'}, 'system': 'S',
            'messages': [{'role': 'user', 'content': 'U'}]}


def _gate(migrated, key):
    current, _, cp, *_ = case(migrated)
    ident = current.create_run(v2_request(), idempotency_key=key)['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    return gate(current, ident, token), ident


class Third(Exception):
    pass


def test_p_g16_no_third_attempt_fails_fast(migrated, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    budget, _ = _gate(migrated, 'p-g16')
    calls = []

    class Inner:
        def create(self, **call):
            calls.append(call)
            if len(calls) <= 2:
                raise _connect_error()
            raise Third()
    with pytest.raises(APIConnectionError):
        trade_idea._BudgetedMessages(Inner(), budget, 'capo').create(**_capo_call())
    assert len(calls) == 2


# G17 / G18 --------------------------------------------------------------------------
def test_p_g17_g18_stream_retries_once_and_released_needs_no_settlement(migrated, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    budget, ident = _gate(migrated, 'p-g17')
    budget._reuse = lambda kwargs, role: None
    calls = []

    class Inner:
        def stream(self, **call):
            calls.append(call)
            if len(calls) <= 2:
                raise _connect_error()
            raise Third()
    wrapped = trade_idea._BudgetedStream(Inner(), budget, 'capo', _capo_call())
    with pytest.raises(APIConnectionError):
        wrapped.__enter__()
    assert len(calls) == 2
    assert wrapped.reconciled is True and wrapped.unknown_marked is False


# G19 / G20 --------------------------------------------------------------------------
def test_p_g19_read_timeout_is_not_marked_connect():
    import httpx
    from bellomberg.core.llm_client import OpenRouterClient, APITimeoutError

    def handler(request):
        raise httpx.ReadTimeout('read timed out', request=request)
    client = OpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(handler))
    with pytest.raises(APITimeoutError) as info:
        client._invia({'model': 'x', 'messages': []})
    assert getattr(info.value, 'transport_phase', None) is None


@pytest.mark.parametrize('error, phase', [('connect', 'connect'), ('read_timeout', None)])
def test_p_g20_async_client_marks_only_connect(error, phase):
    import httpx
    from bellomberg.core.llm_client import AsyncOpenRouterClient

    def handler(request):
        if error == 'connect':
            raise httpx.ConnectTimeout('connect timed out', request=request)
        raise httpx.ReadTimeout('read timed out', request=request)
    client = AsyncOpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(handler))
    with pytest.raises(Exception) as info:
        asyncio.run(client._invia({'model': 'x', 'messages': []}))
    assert getattr(info.value, 'transport_phase', None) == phase


# D1 ---------------------------------------------------------------------------------
def test_p_d1_three_present_desks_are_below_quorum():
    desks = trade_idea.TRADE_IDEA_DESKS
    others = [d for d in desks if d != 'fundamentals']
    gaps = {d: {'message': 'truncated'} for d in others[:len(desks) - 3]}
    board = SimpleNamespace(data={'_desk_gaps': gaps}, read=lambda desk, r: 'report ' + desk)
    with pytest.raises(RuntimeError, match='quorum'):
        trade_idea._committee_quorum(board)
    gaps = {d: {'message': 'truncated'} for d in others[:len(desks) - 4]}
    board = SimpleNamespace(data={'_desk_gaps': gaps}, read=lambda desk, r: 'report ' + desk)
    present, missing = trade_idea._committee_quorum(board)
    assert len(present) == 4


# D3 / D4 ----------------------------------------------------------------------------
def _checks(monkeypatch, data):
    from bellomberg.core.trade_idea_contract import DOSSIER_KEYS
    for name in ('_verified_candidate_valuations', '_require_final_research', '_quality_sufficient',
                 '_candidate_quote_matches', '_bound_evidence', '_sizing_valid'):
        monkeypatch.setattr(trade_idea, name, lambda *a, **k: True)
    board = SimpleNamespace(data=data, analysis_mode='fundamentals_research_v1', get_latest=lambda name: None)
    run = {'ticker': 'T', 'company_name': 'T Inc', 'exchange': 'X', 'started_at': '2026-10-01'}
    result = {'ticker': 'T', 'dossier': [{'key': k} for k in DOSSIER_KEYS]}
    return trade_idea._operational_checks(run, result, board, {}, {}, {}, {'ticker': 'T'}, {})['evidence_sufficient']


def test_p_d3_d4_each_gap_alone_makes_evidence_insufficient(monkeypatch):
    assert _checks(monkeypatch, {}) is True
    assert _checks(monkeypatch, {'_desk_gaps': {'crypto': {'message': 'x'}}}) is False
    assert _checks(monkeypatch, {'_objections': [{'objection': {'material': True}, 'unanswered': 'x'}]}) is False


# D5 ---------------------------------------------------------------------------------
def test_p_d5_run_level_failures_are_never_desk_gaps():
    from bellomberg.storage.trade_idea_store import BudgetBlocked
    assert trade_idea._desk_local_failure(RuntimeError('specialist response truncated: stop_reason=max_tokens')) is True
    assert trade_idea._desk_local_failure(BudgetBlocked('run is not active')) is False
    assert trade_idea._desk_local_failure(RuntimeError('Provider cost unresolved; no new request permitted')) is False
    assert trade_idea._desk_local_failure(ValueError('Research seal integrity differs')) is False


# D9 / D10 ---------------------------------------------------------------------------
def _reseal(board, missing):
    from bellomberg.core import research_analysis
    board.data.pop('_research_thesis', None)
    present = list(trade_idea.TRADE_IDEA_DESKS[1:])
    return research_analysis.seal_research_thesis(board, desks=present, missing=missing)


def test_p_d9_desk_neither_sealed_nor_declared_blocks(research_board):
    _reseal(research_board, None)
    with pytest.raises(RuntimeError, match='neither sealed nor declared'):
        trade_idea._require_final_research(research_board)


def test_p_d10_missing_reports_are_part_of_the_seal(research_board):
    from bellomberg.core import research_analysis
    _reseal(research_board, {trade_idea.TRADE_IDEA_DESKS[0]: 'truncated'})
    research_analysis.research_reference(research_board)
    research_board.data['_research_thesis']['missing_reports'] = {trade_idea.TRADE_IDEA_DESKS[0]: 'rewritten later'}
    with pytest.raises(ValueError, match='integrity'):
        research_analysis.research_reference(research_board)


# A3 ---------------------------------------------------------------------------------
def test_p_a3_empty_or_no_report_desk_is_declared_missing():
    from test_trade_idea_desk_annex import _data
    data = _data()
    data['macro'] = {'1': '', '2': '   '}
    annex = trade_idea._desk_annex(data)
    assert {d['desk']: d['status'] for d in annex['desks']}['macro'] == 'missing'


# T6 ---------------------------------------------------------------------------------
def test_p_t6_orphan_with_incomplete_text_is_not_promoted(monkeypatch):
    from bellomberg.api import trade_idea_routes as routes
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    from test_trade_idea_quality_contract import _detail
    import bellomberg.reporting.trade_idea_delivery as delivery
    monkeypatch.setattr(trade_idea, "_candidate_quote_matches", lambda a, b: a == b)
    monkeypatch.setattr(trade_idea, "_fx_receipt", lambda portfolio: {"valid": True, "observations": []})
    monkeypatch.setattr(delivery, "_candidate_workbooks", lambda *a, **k: [])
    checks = dict.fromkeys(("identity_verified", "evidence_sufficient", "red_team_complete", "capo_valid",
                            "mandate_valid", "sizing_valid", "history_context_sent", "research_reviewed",
                            "candidate_price_revalidated", "fx_revalidated"), True)
    detail = _detail(RESEARCH_ANALYSIS_MODE, checks)
    detail['progress']['report_quality']['content_integrity'] = 'incomplete'
    got, reason = routes._revalidate_orphan_route(SimpleNamespace(db_path=None), detail,
        quote_sampler=lambda run: {"valid": True, "price": 10.0}, portfolio_loader=dict)
    assert got is None and "Qualita'" in reason


# F5 ---------------------------------------------------------------------------------
def test_p_f5_unresolvable_alias_is_declared_not_the_raw_symbol(monkeypatch):
    pytest.importorskip('yfinance')
    from bellomberg.market_data import consensus_estimates as ce
    import yfinance

    def no_alias(ticker):
        raise OSError('alias store unavailable')
    monkeypatch.setattr(ce, 'provider_symbol', no_alias)
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: pytest.fail('raw symbol sent: ' + symbol))
    ce._CACHE.clear()
    out = ce.get_consensus('ZZTEST')
    assert 'alias' in str(out.get('error'))


# V1 ---------------------------------------------------------------------------------
def test_p_v1_catalog_without_supported_efforts_is_refused():
    rows = []
    for role in trade_idea.MODEL_IDS:
        rows.append({'id': trade_idea.model_for_role(role), 'context_length': 1000000,
                     'top_provider': {'max_completion_tokens': 128000},
                     'pricing': {'prompt': '0.000001', 'completion': '0.000002'},
                     'supported_parameters': ['reasoning', 'reasoning_effort', 'max_tokens', 'tools',
                                              'tool_choice', 'response_format'],
                     'reasoning': {'supported_efforts': []}})

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    with pytest.raises(ValueError, match='ragionamento'):
        trade_idea.fetch_model_catalog(opener=lambda req, timeout: Resp(json.dumps({'data': rows}).encode()),
                                       use_cache=False)


# G7 ---------------------------------------------------------------------------------
def test_p_g7_v3_capo_call_must_use_the_v3_cap(migrated):
    current, _, cp, *_ = case(migrated)
    payload = v2_request()
    payload['execution_policy'] = V3['execution_policy']
    payload['models']['capo']['reasoning_effort'] = 'medium'
    ident = current.create_run(payload, idempotency_key='p-g7')['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    budget = gate(current, ident, token)
    call = {**_capo_call(), 'thinking': {'type': 'effort', 'effort': 'medium'}}
    with pytest.raises(ValueError, match='differs from the accepted execution policy'):
        budget._reserve(call, 'capo')


def test_p_g7_v3_capo_cap_adattato_al_tetto_del_contratto(migrated):
    # MOD-CAP (06/10, decisione PM): la policy chiede 128000, il modello del contratto ne accetta
    # 65536 -> il Capo parte a 65536 (il cap della policy ADATTATO); ogni altro cap resta rifiutato.
    current, _, cp, *_ = case(migrated)
    payload = v2_request()
    payload['execution_policy'] = V3['execution_policy']
    payload['models']['capo']['reasoning_effort'] = 'medium'
    ident = current.create_run(payload, idempotency_key='p-g7-tetto')['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    snapshot = catalog()
    snapshot['models']['capo']['max_completion_tokens'] = 65536      # contratto del gate
    budget = trade_idea.TradeIdeaBudgetGate(current, ident, token, snapshot, catalog_fetcher=lambda: snapshot)
    call = {**_capo_call(), 'thinking': {'type': 'effort', 'effort': 'medium'}}
    with pytest.raises(ValueError, match='differs from the accepted execution policy'):
        budget._reserve({**call, 'max_tokens': 40000}, 'capo')
    # Il cap adattato supera il controllo della policy; si ferma solo piu' avanti, al confronto col
    # checkpoint del Capo di questo caso sintetico (salvato a 32768): non e' il controllo in prova.
    from bellomberg.storage.trade_idea_store import RunConflict
    with pytest.raises(RunConflict, match='Capo request checkpoint'):
        budget._reserve({**call, 'max_tokens': 65536}, 'capo')


# T2 ---------------------------------------------------------------------------------
def test_p_t2_v3_finalization_grant_keeps_medium_and_full_room(migrated, monkeypatch):
    import test_trade_idea_paid_capo_checkpoint as paid
    monkeypatch.setattr(paid, 'POLICY', V3['execution_policy'])
    original = paid.v2_request

    def v3_request():
        payload = original()
        payload['models']['capo']['reasoning_effort'] = 'medium'
        return payload
    monkeypatch.setattr(paid, 'v2_request', v3_request)
    current, parent, _, body, _ = paid.case(migrated, public=' ', stop_reason='max_tokens')
    child = current.create_continuation(parent, idempotency_key='v3-grant', authorize_new_requests=True,
                                        capo_finalization_request_id='original-paid-capo')
    accepted = current.accepted_capo_finalization(child['run']['id'])
    assert accepted['thinking'] == {'type': 'effort', 'effort': 'medium'}
    assert accepted['max_tokens'] >= body['max_tokens']


def test_p_g7_finalizzazione_capo_al_cap_adattato(migrated):
    # R-MOD F3 (06/10): la finalizzazione autorizzata del Capo (128000) con un modello del contratto
    # a tetto 65536 parte a 65536 (adattato); ogni altro cap resta rifiutato dalla guardia.
    current, _, cp, *_ = case(migrated)
    payload = v2_request()
    payload['execution_policy'] = V3['execution_policy']
    payload['models']['capo']['reasoning_effort'] = 'medium'
    ident = current.create_run(payload, idempotency_key='p-g7-fin')['run']['id']
    token = current.claim_run(ident)
    save_cp(current, ident, token, cp)
    snapshot = catalog()
    snapshot['models']['capo']['max_completion_tokens'] = 65536
    budget = trade_idea.TradeIdeaBudgetGate(current, ident, token, snapshot, catalog_fetcher=lambda: snapshot)
    thinking = {'type': 'effort', 'effort': 'medium'}
    budget.capo_finalization = lambda: {'max_tokens': 128000, 'thinking': thinking}
    call = {**_capo_call(), 'thinking': thinking}
    with pytest.raises(ValueError, match='Authorized finalization permits only'):
        budget._reserve({**call, 'max_tokens': 40000}, 'capo')
    try:
        budget._reserve({**call, 'max_tokens': 65536}, 'capo')
    except Exception as exc:     # si ferma piu' avanti (checkpoint sintetico), non sulla finalizzazione
        assert 'finalization' not in str(exc), exc
