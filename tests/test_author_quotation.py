"""Exact author quotation evidence; frozen providers and real proof compiler."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_preparation_fresh_listing_independent_review import listing_catalog, TICKER, ON, DAY, ENTITY, TITLE
from test_trade_idea_economic import qualified, _operating_plan


@pytest.fixture
def case(tmp_path):
    primary, _listing, _price = listing_catalog(tmp_path)
    qualification = qualified(tmp_path / "accepted")
    assert qualification["status"] == "qualified"
    qualification["source_report"].pop("source_plan")
    qualification["source_report"]["documents"].append(primary)
    from bellomberg.valuation.trade_idea_model import source_fingerprint
    qualification["fingerprint"] = source_fingerprint(qualification)
    plan = _operating_plan()
    plan["model"].pop("quotation")
    # The author explicitly selects the same fictional ordinary class proved
    # by this fixture's raw listing; no production share-class alias is inferred.
    plan["model"]["perimeter"]["value"]["share_class"] = TITLE
    before = deepcopy((qualification, plan))
    calls = []
    def fetch(ticker, on):
        calls.append((ticker, on))
        return {"symbol": ticker, "date": on, "close": 10., "currency": "EUR"}
    return SimpleNamespace(qualification=qualification, plan=plan, primary=primary,
        root=tmp_path, fetch=fetch, calls=calls, before=before)


def test_exact_date_basis_is_consumed_by_real_compiler_and_shared_renderer(case, tmp_path):
    from bellomberg.valuation.author_quotation import quotation_basis, merge_quotation_evidence
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from bellomberg.valuation.input_preparation import prepare_method_inputs

    result = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert result["status"] == "ready", result.get("issues")
    assert case.calls == [(TICKER, ON)]
    assert result["driver"]["value"]["price_as_of"] == ON
    assert result["driver"]["value"]["price"] == 10.
    assert (case.qualification, case.plan) == case.before
    authored = deepcopy(case.plan)
    authored["model"]["quotation"] = deepcopy(result["driver"])
    overlay = merge_quotation_evidence(case.qualification, authored, result["receipt"], archive_root=case.root)
    assert overlay["fingerprint"] != case.qualification["fingerprint"]
    documents = overlay["source_report"]["documents"]
    prepared = prepare_method_inputs(overlay["bundle"], documents=documents,
        propose=lambda *_: authored, source_report=overlay["source_report"])
    assert prepared["status"] == "prepared", prepared["issues"]
    workbook = prepare_and_generate(overlay["bundle"], documents=documents,
        propose=lambda *_: authored, source_report=overlay["source_report"], output_dir=tmp_path / "model")
    assert workbook["ok"] is True, workbook.get("error")
    assert Path(workbook["path"]).is_file()
    assert workbook["preparation"]["proposal"]["plan"]["model"]["quotation"] == result["driver"]
    ids = {doc["id"] for doc in documents}
    assert "price-" + TICKER + "-" + ON in ids and "listing-" + case.primary["id"] in ids
    assert (case.qualification, case.plan) == case.before


def test_a_different_authored_class_remains_a_real_compiler_error(case, tmp_path):
    from bellomberg.valuation.author_quotation import quotation_basis, merge_quotation_evidence
    from bellomberg.valuation.preparation_service import prepare_and_generate

    case.plan["model"]["perimeter"]["value"]["share_class"] = "A different preferred class"
    observed = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert observed["status"] == "ready"
    authored = deepcopy(case.plan)
    authored["model"]["quotation"] = observed["driver"]
    overlay = merge_quotation_evidence(case.qualification, authored, observed["receipt"], archive_root=case.root)
    result = prepare_and_generate(overlay["bundle"], documents=overlay["source_report"]["documents"],
        propose=lambda *_: authored, source_report=overlay["source_report"], output_dir=tmp_path / "invalid")
    assert result["ok"] is False and "classe" in result["error"]


def test_recovery_twice_uses_success_receipt_without_provider_or_changed_grant(case):
    from bellomberg.valuation.author_quotation import quotation_basis, merge_quotation_evidence

    first = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert first["status"] == "ready", first
    for _ in range(2):
        restored = quotation_basis(case.qualification, case.plan, archive_root=case.root,
            existing=deepcopy(first["receipt"]), price_fetch=lambda *_: pytest.fail("Repeated exact quote"),
            allow_acquire=False)
        assert restored == first
        overlay = merge_quotation_evidence(case.qualification, case.plan, restored["receipt"], archive_root=case.root)
        assert len({doc["id"] for doc in overlay["source_report"]["documents"]}) == len(overlay["source_report"]["documents"])
    assert case.calls == [(TICKER, ON)]
    assert (case.qualification, case.plan) == case.before


@pytest.mark.parametrize("fault", ["wrong_day", "wrong_symbol", "wrong_currency", "missing_price", "timeout"])
def test_missing_or_wrong_quote_is_explicit_and_never_a_previous_close_or_zero(case, fault):
    from bellomberg.valuation.author_quotation import quotation_basis

    def fetch(ticker, on):
        case.calls.append((ticker, on))
        if fault == "timeout":
            raise TimeoutError("Frozen read-only transport timeout")
        return {"symbol": "OTHER" if fault == "wrong_symbol" else ticker,
            "date": "2025-12-30" if fault == "wrong_day" else on,
            "currency": "USD" if fault == "wrong_currency" else "EUR",
            "close": None if fault == "missing_price" else 10.}
    result = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=fetch)
    assert result["status"] == "incomplete" and result["issues"]
    assert not result.get("driver") and not result.get("receipt")
    assert (case.qualification, case.plan) == case.before
    # A new explicit read may retry; no internal retry and no ambiguous-cost
    # gate is invented for this free idempotent historical-price lookup.
    fixed = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert fixed["status"] == "ready", fixed
    assert len(case.calls) == 2


@pytest.mark.parametrize("fault", ["changed_raw", "missing_raw", "different_entity", "ambiguous_primary"])
def test_invalid_primary_stops_before_any_quote_lookup(case, fault):
    from bellomberg.valuation.author_quotation import quotation_basis
    from bellomberg.valuation.trade_idea_model import source_fingerprint

    if fault == "changed_raw":
        Path(case.primary["archive_path"]).write_bytes(b"changed raw")
    elif fault == "missing_raw":
        Path(case.primary["archive_path"]).unlink()
    elif fault == "different_entity":
        case.plan["model"]["perimeter"]["value"]["entity"] = "UNRELATED"
    else:
        other = deepcopy(case.primary)
        other["id"] = "second-primary"
        case.qualification["source_report"]["documents"].append(other)
        case.qualification["fingerprint"] = source_fingerprint(case.qualification)
    result = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert result["status"] == "incomplete" and result["issues"]
    assert case.calls == []


@pytest.mark.parametrize("fault", ["price", "receipt", "raw", "calendar"])
def test_pinned_derived_evidence_rejects_tamper_or_changed_author_date(case, fault):
    from bellomberg.valuation.author_quotation import quotation_basis, merge_quotation_evidence

    result = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert result["status"] == "ready"
    receipt = deepcopy(result["receipt"])
    if fault == "price":
        cache = Path(receipt["quote_cache"]["path"])
        item = json.loads(cache.read_text())
        item["report"]["documents"][0]["text"] += " "
        cache.write_text(json.dumps(item), encoding="utf-8")
    elif fault == "receipt":
        receipt["driver"]["value"]["price"] = 999.
    elif fault == "raw":
        Path(case.primary["archive_path"]).write_bytes(b"different raw bytes")
    else:
        case.plan["model"]["calendar"]["value"]["valuation_date"] = "2025-12-30"
    with pytest.raises(ValueError):
        merge_quotation_evidence(case.qualification, case.plan, receipt, archive_root=case.root)
    assert case.calls == [(TICKER, ON)]


def _board(case):
    events = []
    board = SimpleNamespace(source_qualification=case.qualification,
        source_admission=deepcopy(case.qualification), current_round=1, model_phase="building",
        run_scope="trade_idea", run_id="offline-derived-quotation", target_ticker=TICKER,
        valuation_results={}, data={"_model_input_draft": deepcopy(case.plan),
            "_source_research": {"archive_root": str(case.root)},
            "_model_input_basis": {"plan": {}, "source_fingerprint": case.qualification["fingerprint"],
                                   "provenance": "No archived plan"}},
        quotation_price_fetch=case.fetch, persist_run_checkpoint=lambda *args: events.append(args))
    return board, events


def test_native_input_tool_exposes_explicit_basis_without_changing_paid_source_contract(case):
    from bellomberg.agents import trade_idea

    board, events = _board(case)
    answer = trade_idea._handle_candidate_plan_tool(board, "fundamentals", "get_candidate_model_inputs",
        {"scope": "model", "drivers": ["quotation"]})
    assert answer["ok"] is True
    driver = answer["basis"]["drivers"]["quotation"]["input"]
    assert driver["value"]["price_as_of"] == ON
    assert "quotation" not in board.data["_model_input_draft"]["model"], "Observed basis is not author adoption"
    assert board.source_qualification == case.before[0] == board.source_admission
    assert case.calls == [(TICKER, ON)] and events
    for document_id in driver["evidence_ids"]:
        viewed = trade_idea._read_candidate_source(board, "fundamentals", {"document_id": document_id})
        assert viewed["ok"] and viewed["complete"], viewed
    again = trade_idea._handle_candidate_plan_tool(board, "fundamentals", "get_candidate_model_inputs",
        {"scope": "model", "drivers": ["quotation"]})
    assert again["basis"]["drivers"]["quotation"] == answer["basis"]["drivers"]["quotation"]
    assert case.calls == [(TICKER, ON)]


def test_existing_validation_tool_discovers_missing_quote_without_changing_the_draft(case):
    from bellomberg.agents import trade_idea
    board, _events = _board(case)
    before = deepcopy(board.data['_model_input_draft'])
    result = trade_idea._handle_candidate_plan_tool(board, 'fundamentals', 'get_candidate_model_inputs',
        {'scope': 'model', 'contract_section': 'draft_validation'})
    assert result['ok'] and result['complete'], result
    diagnostic = json.loads(result['text'])
    assert diagnostic['quotation_basis']['status'] == 'ready'
    assert 'drivers=[quotation]' in diagnostic['quotation_next_action']
    assert diagnostic['status'] != 'prepared', 'Discovery must not adopt the observed driver'
    assert board.data['_model_input_draft'] == before and 'quotation' not in before['model']
    assert board.source_qualification == case.before[0] and case.calls == [(TICKER, ON)]


def test_late_research_qualification_consumes_same_derived_receipt_and_retains_research_contract(case, tmp_path):
    from bellomberg.agents.trade_idea import _consultation_source_fingerprint
    from bellomberg.valuation.trade_idea_model import (research_admission, research_source_qualification,
        qualify_authored_research)
    from bellomberg.valuation.author_quotation import quotation_basis
    from test_trade_idea_economic import providers_for

    admission = research_admission(TICKER, case.qualification['identity'], DAY,
        archive_root=case.root, providers=providers_for())
    assert admission['status'] == 'research_required', admission.get('reasons')
    snapshot = {'status': 'ready', 'run_id': 'frozen-research', 'ticker': TICKER, 'as_of': DAY,
        'revision_id': 'one', 'revision_sha256': 'a' * 64,
        'parent_grant_fingerprint': admission['fingerprint'],
        'documents': deepcopy(case.qualification['source_report']['documents']), 'document_receipts': []}
    current = research_source_qualification(admission, snapshot)
    before = deepcopy((admission, snapshot, current, case.plan))
    pin = _consultation_source_fingerprint(SimpleNamespace(source_qualification=current))
    observed = quotation_basis(current, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert observed['status'] == 'ready', observed.get('issues')
    authored = deepcopy(case.plan)
    authored['model']['quotation'] = deepcopy(observed['driver'])
    for _ in range(2):
        qualified = qualify_authored_research(admission, snapshot, authored, archive_root=case.root,
            quotation_receipt=observed['receipt'])
        assert qualified['status'] == 'qualified', qualified.get('reasons')
        assert qualified['research_revision'] == current['research_revision']
        assert _consultation_source_fingerprint(SimpleNamespace(source_qualification=qualified)) == pin
        assert set(observed['driver']['evidence_ids']) <= {doc['id'] for doc in qualified['source_report']['documents']}
    assert (admission, snapshot, current, case.plan) == before
    assert case.calls == [(TICKER, ON)]


def test_shared_weekly_preparation_reuses_quote_without_hiding_missing_balance(case, monkeypatch, tmp_path):
    from bellomberg.valuation import quotation_evidence, preparation_service, valuation_sources
    from bellomberg.valuation.author_quotation import quotation_basis

    observed = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    assert observed['status'] == 'ready'
    authored = deepcopy(case.plan)
    authored['model']['quotation'] = deepcopy(observed['driver'])
    monkeypatch.setattr(valuation_sources, 'collect_documents', lambda *_a, **_k: {
        'status': 'ready', 'documents': [deepcopy(case.primary)], 'issues': [], 'coverage': {}})
    monkeypatch.setattr(valuation_sources, 'company_facts_documents', lambda *_a, **_k: {
        'status': 'ready', 'documents': [deepcopy(doc) for doc in case.qualification['source_report']['documents']
            if doc['id'] != case.primary['id']], 'issues': []})
    cache_native = quotation_evidence.cached_historical_quote_document
    seen = []
    def cache(*args, **kwargs):
        assert kwargs['quote_currency'] == 'EUR' and kwargs['context']['exchange'] == 'TEST'
        kwargs['fetch'] = case.fetch
        result = cache_native(*args, **kwargs)
        seen.append(deepcopy(result))
        return result
    monkeypatch.setattr(quotation_evidence, 'cached_historical_quote_document', cache)
    for attempt in range(3):
        result = preparation_service.collect_and_prepare(case.qualification['bundle'], archive_root=case.root,
            propose=lambda *_: deepcopy(authored), output_dir=tmp_path / ('weekly-' + str(attempt)))
        # This raw fixture proves its listed class, not a complete printed
        # balance. The ordinary source gate must retain that separate gap.
        assert result['ok'] is False and not result.get('path')
        acquisition = result['preparation']['provenance']['source_acquisition']
        assert acquisition['balance_sheet']['status'] == 'incomplete'
        assert acquisition['components']['historical_quote']['status'] == 'ready'
        assert acquisition['components']['historical_quote']['cache_receipt'] == seen[-1]['cache_receipt']
    assert len(seen) == 3 and seen[0] == seen[1] == seen[2]
    # TI and the common collector pin different explicit selection contexts;
    # within the weekly context two recoveries perform no additional fetch.
    assert case.calls == [(TICKER, ON), (TICKER, ON)]
