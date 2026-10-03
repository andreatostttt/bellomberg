"""One report-qualification rule per contract, and research orphans do not need workbooks."""
from types import SimpleNamespace

from bellomberg.api import trade_idea_routes as routes
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V2, report_quality_sufficient


def test_v2_short_complete_memo_qualifies_and_text_loss_does_not():
    short = {"status": "ready", "execution_policy": EXECUTION_POLICY_V2,
             "analytical_pages": 3, "analytical_words": 1200, "content_integrity": "complete"}
    assert report_quality_sufficient(short)
    assert not report_quality_sufficient({**short, "content_integrity": "incomplete"})
    assert not report_quality_sufficient({**short, "status": "partial"})


def test_legacy_contract_keeps_its_page_and_word_floor():
    legacy = {"status": "ready", "analytical_pages": 10, "analytical_words": 4000}
    assert report_quality_sufficient(legacy)
    assert not report_quality_sufficient({**legacy, "analytical_pages": 9})
    assert not report_quality_sufficient({**legacy, "analytical_words": 3999})
    assert not report_quality_sufficient(None)


def _detail(mode, checks, refs=()):
    quote = {"valid": True, "price": 10.0}
    fx = {"valid": True, "observations": []}
    return {"run": {"ticker": "ACME", "currency": "USD", "analysis_mode": mode},
            "result": {"valuation_refs": list(refs)},
            "progress": {"routing_checks": checks,
                         "report_quality": {"status": "ready", "execution_policy": EXECUTION_POLICY_V2,
                                            "analytical_pages": 2, "analytical_words": 900,
                                            "content_integrity": "complete"},
                         "candidate_quote_receipts": {"initial": quote, "final": quote},
                         "fx_receipts": {"initial": fx, "final": fx}}}


def test_research_orphan_is_revalidated_without_workbooks(monkeypatch):
    import bellomberg.agents.trade_idea as trade_idea
    import bellomberg.reporting.trade_idea_delivery as delivery
    monkeypatch.setattr(trade_idea, "_candidate_quote_matches", lambda a, b: a == b)
    monkeypatch.setattr(trade_idea, "_fx_receipt", lambda portfolio: {"valid": True, "observations": []})
    def no_workbooks(*args, **kwargs):
        raise AssertionError("research orphan must not read workbooks")
    monkeypatch.setattr(delivery, "_candidate_workbooks", no_workbooks)
    base = dict.fromkeys(("identity_verified", "evidence_sufficient", "red_team_complete", "capo_valid",
                          "mandate_valid", "sizing_valid", "history_context_sent",
                          "candidate_price_revalidated", "fx_revalidated"), True)
    current = SimpleNamespace(db_path=None)
    sample = lambda run: {"valid": True, "price": 10.0}
    research = {**base, "research_reviewed": True}
    checks, reason = routes._revalidate_orphan_route(
        current, _detail(RESEARCH_ANALYSIS_MODE, research), quote_sampler=sample, portfolio_loader=dict)
    assert reason is None and checks == research
    # A research run without its research attestation is never promoted.
    checks, reason = routes._revalidate_orphan_route(
        current, _detail(RESEARCH_ANALYSIS_MODE, base), quote_sampler=sample, portfolio_loader=dict)
    assert checks is None and "Attestazioni" in reason
    # Valuation references in a research result are refused, not silently ignored.
    checks, reason = routes._revalidate_orphan_route(
        current, _detail(RESEARCH_ANALYSIS_MODE, research, refs=[{"snapshot_id": "x"}]),
        quote_sampler=sample, portfolio_loader=dict)
    assert checks is None and "inattesi" in reason
