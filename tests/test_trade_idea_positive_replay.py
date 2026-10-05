"""Offline positive Trade Idea replay: contratto Excel archiviato e recupero research al riavvio.

Il replay favorevole del comitato col workbook e' in quarantena in
archive/private/attic/tests_excel_archiviato_20261005/test_trade_idea_positive_replay_legacy.py (Excel archiviato 03/10);
le sue garanzie vive sono riportate in ricerca in test_trade_idea_no_workbook_e2e.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_store import all_checks, db_path, migrated, store
from trade_idea_fixtures import research_result


@pytest.fixture(autouse=True)
def frozen_usage_fx(monkeypatch):
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "_fx_usd_to_eur", lambda: (0.9, "offline_frozen_fx"))


def test_favorable_excel_committee_is_archived_and_refused_before_any_paid_work(migrated, tmp_path):
    # I test del comitato favorevole col workbook sono in archive/private/attic/tests_excel_archiviato_20261005/
    # test_trade_idea_positive_replay_legacy.py; le garanzie vive sono riportate in ricerca
    # in test_trade_idea_no_workbook_e2e.py. Qui il contratto attuale della run Excel.
    from test_trade_idea_pipeline import _assert_excel_run_refused_before_work
    s = store(migrated)
    accepted = _priced_request(budget="30")
    accepted.update(ticker="SYNTH-EXT", currency="EUR", language="en",
        view_text="Switching costs make this business immune to a downturn.")
    run_id = s.create_run(accepted, idempotency_key="full-positive-committee")["run"]["id"]
    _assert_excel_run_refused_before_work(s, run_id, tmp_path)


def _research_request(tmp_path, *, budget):
    """Richiesta accettata come la crea oggi l'API: research, grant di solo comitato."""
    from copy import deepcopy
    from bellomberg.valuation import trade_idea_model as model
    from test_trade_idea_economic import IDENTITY
    from test_trade_idea_pm_sources import _profile_providers
    day = datetime.now(timezone.utc).date().isoformat()
    admission = model.research_admission(IDENTITY["ticker"], deepcopy(IDENTITY), day,
        archive_root=tmp_path / "admission", providers=_profile_providers(day),
        analysis_mode=RESEARCH_ANALYSIS_MODE)
    assert admission["status"] == "research_required", admission["reasons"]
    return {**_priced_request(budget=budget), "analysis_mode": RESEARCH_ANALYSIS_MODE,
        "ticker": IDENTITY["ticker"], "company_name": IDENTITY["name"],
        "exchange": IDENTITY["exchange"], "currency": IDENTITY["currency"],
        "source_qualification": admission,
        "authorization": {"accepted": True, "source_fingerprint": admission["fingerprint"],
                          "activities": ["committee"], "max_revision_rounds": 0}}


@pytest.mark.parametrize("quote_drift", [False, True])
def test_crash_after_result_before_route_revalidates_without_paid_replay(
        migrated, tmp_path, monkeypatch, quote_drift):
    # Portato in modalita' ricerca (Excel archiviato 03/10): il recupero al riavvio di una
    # run conclusa ma non ancora instradata e' vivo per le run research. Garanzia conservata:
    # rivalidazione della quotazione senza alcuna nuova richiesta pagata; quotazione cambiata
    # -> instradamento prudenziale a research.
    from bellomberg.api import trade_idea_routes as routes
    from bellomberg.core import paths

    monkeypatch.setattr(paths, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(paths, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(routes, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(routes, "REPORT_DIR", tmp_path)
    s = store(migrated)
    accepted = _research_request(tmp_path, budget="5")
    accepted.update(language="en",
        view_text="Switching costs make this business immune to a downturn.")
    run_id = s.create_run(accepted, idempotency_key="crash-window-" + str(quote_drift))["run"]["id"]
    assert s.get_run(run_id)["run"]["analysis_mode"] == RESEARCH_ANALYSIS_MODE
    result = research_result("favorable", run_id=run_id)
    result.update(valuation_refs=[], model_review=None)
    result.pop("destination")
    result["proposal"].update(eur_amount=100.0, sizing_source="Prior measured sizing")
    token = s.claim_run(run_id)
    day = datetime.now(timezone.utc).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    quote = {"status": "ready", "ticker": "SYNTH-EXT", "price": "100",
        "price_asof": day.isoformat(),
        "source": "yfinance daily Close (not an intraday quote)",
        "currency": "EUR", "currency_basis": "accepted_identity"}
    fx = {"valid": True, "observations": [], "sampled_at": datetime.now(timezone.utc).isoformat()}
    checks = {**all_checks(), "candidate_price_revalidated": True,
              "fx_revalidated": True, "research_reviewed": True}
    s.update_progress(run_id, token, "result", {
        "routing_checks": checks,
        # Qualita' attestata come la scrive oggi il renderer (regola unica per contratto,
        # trade_idea_policy.report_quality_sufficient), qualunque sia la policy accettata.
        "report_quality": {"status": "ready", "analytical_pages": 11, "analytical_words": 4200,
                           "execution_policy": s.get_run(run_id)["run"].get("execution_policy"),
                           "content_integrity": "complete"},
        "candidate_quote_receipts": {"initial": quote, "final": quote},
        "fx_receipts": {"initial": fx, "final": fx}})
    s.finish_run(run_id, token, result, "completed")
    # No decision exists yet: this is the exact crash window after finish_run.
    assert s.get_run(run_id)["run"]["destination"]["kind"] == "none"
    monkeypatch.setattr(routes, "TradeIdeaStore", lambda *_: s)
    monkeypatch.setattr(routes, "_worker_state", lambda _: (None, False))
    sent = []
    def no_paid_delivery(store, received_id, **kwargs):
        sent.append(received_id)
    observed = {**quote, "price": "101"} if quote_drift else quote
    recovery = routes.recover_orphan_runs(migrated, delivery=no_paid_delivery,
        quote_sampler=lambda _: observed,
        portfolio_loader=lambda: {"positions": [], "fx_incomplete": [], "stale_positions": []})
    detail = s.get_run(run_id)
    assert recovery["errors"] == []
    assert sent == [run_id]
    assert detail["run"]["destination"]["kind"] == ("research" if quote_drift else "dcn")
    assert detail["run"]["technical_status"] == ("incomplete" if quote_drift else "completed")
    if quote_drift:
        assert "Quotazione candidato cambiata" in detail["run"]["reason"]
    # Startup replay never issues a provider reservation or LLM request.
    assert detail["cost"]["requests"] == 0
