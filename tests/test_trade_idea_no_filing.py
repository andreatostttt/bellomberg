"""Trade Idea without any official filing: the run starts and the gap is DECLARED.

Ordine PM 05/10: le run devono partire anche se non c'e' il filing. Preflight e
avvio non dipendono dai bilanci (test_research_admission_api, partial=False/True);
qui si prova che una run la cui ricerca R0 non ammette alcun documento arriva a
un esito con PDF e che il risultato dichiara il buco per mano del SERVER, non del
solo Capo. Ticker, emittente e numeri sono sintetici.
"""
from copy import deepcopy
from types import SimpleNamespace
import threading

from bellomberg.agents import trade_idea
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: F401 (fixture)
from test_trade_idea_economic import IDENTITY
from _smtp_cattura import smtp  # noqa: F401 (fixture of the fixture)
from test_trade_idea_store import db_path, migrated  # noqa: F401 (fixtures of the fixture)

GAP_MARK = "nessun bilancio o documento ufficiale"


def _board(dossiers, mode=RESEARCH_ANALYSIS_MODE, sealed=True):
    data = {'_research_thesis': {'dossiers': dossiers}} if sealed else {}
    return SimpleNamespace(analysis_mode=mode, target_ticker='SYNTH-NOFILE', data=data,
                           _lock=threading.Lock())


def test_gap_declared_only_when_the_sealed_dossier_has_no_document():
    empty = trade_idea.official_documents_gaps(_board({'SYNTH-NOFILE': {'documents': []}}))
    assert len(empty) == 1 and GAP_MARK in empty[0]
    assert trade_idea.official_documents_gaps(
        _board({'SYNTH-NOFILE': {'documents': [{'id': 'doc-synthetic'}]}})) == []
    # Legacy (non-research) runs are untouched.
    assert trade_idea.official_documents_gaps(
        _board({'SYNTH-NOFILE': {'documents': []}}, mode=None)) == []


def test_missing_seal_or_dossier_is_declared_not_assumed_covered():
    for board in (_board({}, sealed=False), _board({'OTHER-SYNTH': {'documents': [{'id': 'x'}]}})):
        gaps = trade_idea.official_documents_gaps(board)
        assert len(gaps) == 1 and 'non verificabile' in gaps[0]


def test_run_without_any_official_document_completes_and_declares_the_gap(no_workbook_case):
    case = no_workbook_case
    # Every issuer page answers 404: preflight admitted no document and R0 finds none.
    case.transport.bodies = {path: (b'not found', 'text/html', 404, {}) for path in case.transport.bodies}
    assert case.admission['source_report']['documents'] == []
    ident = case.current.create_run(deepcopy(case.request), idempotency_key='synthetic-no-filing')['run']['id']
    detail = case.execute(ident, 'no-filing')
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    seal = detail['progress']['checkpoint']['data']['_research_thesis']
    assert seal['dossiers'][IDENTITY['ticker']]['documents'] == []
    receipts = detail['progress']['checkpoint']['tool_receipts']
    assert any(row.get('tool') == 'acquire_company_source' and row.get('success') is False for row in receipts)
    assert not any(row.get('tool') == 'acquire_company_source' and row.get('success') for row in receipts)
    gaps = [gap for gap in detail['result']['data_gaps'] if GAP_MARK in gap]
    assert len(gaps) == 1, detail['result']['data_gaps']
    manifest = detail['artifacts']
    assert manifest['complete_package_status'] == 'ready'
    # Voce 9 (06/10/2026): result['data_gaps'] is now printed by every renderer (memo: page one, before
    # "1. Raccomandazione"; legacy: first text page, before the contents), this gap included. Proved in
    # tests/test_trade_idea_pdf_limiti_prima_pagina.py; this test does not reopen the PDF.
