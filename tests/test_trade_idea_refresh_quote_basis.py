"""Workspace refresh preserves the verified quotation basis and acquisition time."""
from datetime import datetime, timezone
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from test_trade_idea_workspace import research, action, db


@pytest.mark.parametrize("fault", ["future_observation", "missing_receipt", "wrong_venue",
                                  "regularMarketTime", "regularMarketPrice", "currency", "exchangeTimezoneName"])
def test_price_refresh_rejects_unproved_observation_before_model_generation(research, monkeypatch, fault):
    workspace, current, run, model = research
    workspace.clock = lambda: datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    observation = {"status": "ok", "source_id": "https://example.org/quote", "as_of": "2026-09-10",
        "retrieved_at": "2026-09-10T12:00:00+00:00", "data": {"info": {
            "symbol": model["ticker"], "regularMarketPrice": 12.,
            "regularMarketTime": datetime(2026, 9, 10, 10, tzinfo=timezone.utc).timestamp(),
            "currency": "EUR", "fullExchangeName": "TEST", "exchangeTimezoneName": "UTC"}}}
    if fault == "future_observation":
        observation["data"]["info"]["regularMarketTime"] = datetime(2026, 9, 10, 23, 59, tzinfo=timezone.utc).timestamp()
    elif fault == "missing_receipt":
        observation.pop("retrieved_at")
    elif fault == "wrong_venue":
        observation["data"]["info"]["fullExchangeName"] = "DIFFERENT VENUE"
    else:
        observation["data"]["info"].pop(fault)
    workspace.price_fetcher = lambda _: observation
    called = []
    def forbidden_generation(*args, **kwargs):
        called.append(True)
        raise AssertionError("Unproved price reached model generation")
    monkeypatch.setattr("bellomberg.valuation.dcf_engine.generate_valuation", forbidden_generation)
    with pytest.raises(ValueError, match="quotation|venue"):
        action(workspace, run, model, "refresh", {"kind": "price"})
    assert called == []
    assert current.get_run(run)["cost"]["requests"] == 0


@pytest.mark.parametrize("commodity", [False, True])
def test_exposure_price_refresh_preserves_observed_snapshot_without_fair_value(tmp_path, commodity):
    from bellomberg.valuation.trade_idea_model import prepare
    from bellomberg.valuation.trade_idea_workspace import ResearchWorkspace
    from test_trade_idea_exposure import sourced_exposure
    from test_trade_idea_commodity_exposure import sourced
    qualification, plan = (sourced if commodity else sourced_exposure)(tmp_path)
    model = prepare(qualification, lambda *_: deepcopy(plan), tmp_path / 'original')
    assert model['analysis_usability']['usable']
    # Previously saved exposure models have no separate market_quote packet.
    if commodity:
        model.pop('market_quote', None)
    original = deepcopy(model)
    workbook = Path(model['path'])
    sidecar = workbook.with_suffix('.payload.json')
    hashes = [sha256(path.read_bytes()).hexdigest() for path in (workbook, sidecar)]
    observation = deepcopy(model['acquisition_snapshot']['case']['sources']['profile'])
    day = observation['as_of']
    observed = datetime.fromisoformat(day + 'T12:01:00+00:00')
    acquired = datetime.fromisoformat(day + 'T12:02:00+00:00')
    observation['retrieved_at'] = acquired.isoformat()
    price = 12. if commodity else 101.
    observation['source_id'] = 'https://example.org/synthetic/observed-quote'
    observation['data']['info'].update(regularMarketPrice=price, regularMarketTime=observed.timestamp())
    workspace = object.__new__(ResearchWorkspace)
    workspace.clock = lambda: acquired
    calls = []
    def fetch(ticker):
        calls.append(ticker)
        return deepcopy(observation)
    workspace.price_fetcher = fetch
    refreshed = workspace._refresh({}, model, {'kind': 'price'}, tmp_path / 'refreshed')
    assert refreshed['status'] == 'ready', refreshed.get('issues')
    assert refreshed['delta']['after'] == price
    assert refreshed['delta']['before_date'] == ('2026-09-25' if commodity else day)
    assert refreshed['updated_components'] == ['observed_price', 'price_move_since_valuation']
    candidate = refreshed['model']
    assert candidate['generation_id'] != model['generation_id']
    assert candidate['valuation_date'] == model['valuation_date']
    assert candidate['price'] == model['price']
    assert candidate['exposure_analysis'] == model['exposure_analysis']
    assert candidate['intrinsic_value_applicable'] is False
    assert all(candidate.get('fair_value_' + scenario) is None for scenario in ('bear', 'base', 'bull'))
    assert all(refreshed['quote'].get('upside_' + scenario + '_pct') is None for scenario in ('bear', 'base', 'bull'))
    stored = json.loads(Path(candidate['path']).with_suffix('.payload.json').read_text(encoding='utf-8'))
    assert stored['market_quote']['price'] == price
    assert calls == [model['ticker']]
    assert model == original
    assert hashes == [sha256(path.read_bytes()).hexdigest() for path in (workbook, sidecar)]
