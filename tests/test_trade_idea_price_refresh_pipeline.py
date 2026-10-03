"""Final price-only revalidation: real sizing, frozen markets, no paid activity."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea as trade
from bellomberg.core import mandato_pm
from bellomberg.storage import classificazione
from test_sizing_dal_mandato import BOOK, RISK
from test_trade_idea_store import result as paper_result


REQUIRED_MEASUREMENTS = {
    "portfolio_reloaded", "risk_recomputed", "stress_recomputed", "sizing_recomputed",
    "candidate_price_verified", "fx_verified", "proposal_valid",
}
CONTEXT_FIELDS = ("accepted_context_sha256", "current_context_sha256", "price_inputs_sha256")


class FrozenContextStore:
    """Only context observation is substituted; no routing or persistence is allowed."""

    def __init__(self, changed_field=None):
        self.changed_field = changed_field
        self.observations = []
        self.context = {key: trade._plan_digest({"frozen": key}) for key in CONTEXT_FIELDS}

    def price_refresh_context(self, run_id):
        assert run_id == "final-price-refresh-fixture"
        sample = deepcopy(self.context)
        sample["observed_at"] = "2026-10-02T15:00:0" + str(len(self.observations)) + "+00:00"
        if self.observations and self.changed_field:
            sample[self.changed_field] = trade._plan_digest({"changed": self.changed_field})
        self.observations.append(deepcopy(sample))
        return sample

    def __getattr__(self, name):
        raise AssertionError("Final measurement must not route or persist: " + name)


@pytest.fixture(autouse=True)
def forbid_paid_or_delivery_activity(monkeypatch):
    from bellomberg.reporting import trade_idea_delivery
    attempts = []

    def forbidden(*_args, **_kwargs):
        attempts.append("forbidden activity")
        raise AssertionError("No provider, Capo, delivery or email during final measurements")

    monkeypatch.setattr(trade.OpenRouterClient, "__init__", forbidden)
    monkeypatch.setattr(trade, "run_trade_idea_capo", forbidden)
    monkeypatch.setattr(trade, "deliver_trade_idea", forbidden)
    monkeypatch.setattr(trade_idea_delivery, "send_trade_idea_delivery", forbidden)
    yield attempts
    assert attempts == []


@pytest.fixture
def final_inputs(monkeypatch):
    """Extend the existing synthetic sizing book; do not replace financial controls."""
    example = classificazione.carica_veicoli(classificazione.ESEMPIO_VEICOLI)
    assert example["origine"] not in ("assente", "illeggibile")
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda *_a, **_k: deepcopy(example))
    portfolio = deepcopy(BOOK)
    portfolio["positions"] = [row for row in portfolio["positions"] if row["ticker"] != "ACME.MI"]
    portfolio.update(cash_source="sqlite:cash_state", fx_incomplete=[], stale_positions=[])
    for row in portfolio["positions"]:
        row.update(quantita=100, valuta="EUR", prezzo_corrente=row["valore_mercato_eur"] / 100)
    portfolio["positions"][0].update(valuta="GBP", fx_to_eur=1.2, fx_source="live")
    risk = deepcopy(RISK)
    risk.update(portfolio={"var_99_1d_pct": -1.0}, skipped_tickers=[],
                fx_conversion={"qualified": True, "base_currency": "EUR", "local_declared": []})
    stress = {"stress_scenario": "gfc_2008", "stress_fallback": False,
        "fx_conversion": {"qualified": True, "base_currency": "EUR"},
        "stress_meta": {"window_loss_pct": -5.0, "proxied": {}, "zero_filled_days": {},
            "fx_conversion": {"qualified": True, "base_currency": "EUR"}}}
    metrics = {"status": "measured", "vol_annual_pct": 20.0, "avg_corr": 0.2,
        "compared_tickers": [row["ticker"] for row in portfolio["positions"]],
        "source": "Frozen synthetic observations", "observations": 250}
    quote = {"status": "ready", "ticker": "ACME.MI", "price": "12.50", "currency": "EUR",
        "currency_basis": "accepted_identity", "price_asof": datetime.now(timezone.utc).date().isoformat(),
        "source": "yfinance daily Close (not an intraday quote)", "sampled_at": "frozen-fixture"}
    return SimpleNamespace(portfolio=portfolio, risk=risk, stress=stress, metrics=metrics,
        quote=quote, mandate=mandato_pm.profilo_esempio(), result=paper_result("ACME.MI"))


def _verify(inputs, *, store=None, monkeypatch=None, raising=None, omit_risk_loaders=False):
    calls, observations = Counter(), {}
    current = store or FrozenContextStore()

    def load(name):
        def loader(*args):
            calls[name] += 1
            if raising == name:
                raise RuntimeError("Frozen unavailable " + name)
            if name == "metrics":
                assert args == (inputs.portfolio, "ACME.MI", (inputs.quote or {}).get("currency"))
            return deepcopy(getattr(inputs, name))
        return loader

    if monkeypatch is not None:
        native_sizing, native_valid, native_fx = trade._compute_sizing, trade._sizing_valid, trade._fx_receipt

        def sizing(*args, **kwargs):
            calls["native_sizing"] += 1
            observations["sizing_inputs"] = deepcopy((args, kwargs))
            return native_sizing(*args, **kwargs)

        def valid(*args):
            calls["native_sizing_valid"] += 1
            return native_valid(*args)

        def fx(*args):
            calls["native_fx"] += 1
            return native_fx(*args)

        monkeypatch.setattr(trade, "_compute_sizing", sizing)
        monkeypatch.setattr(trade, "_sizing_valid", valid)
        monkeypatch.setattr(trade, "_fx_receipt", fx)
    before = deepcopy(inputs.result)
    receipt = trade._final_price_refresh_verification(current, "final-price-refresh-fixture", inputs.result,
        portfolio_loader=load("portfolio"), risk_loader=None if omit_risk_loaders else load("risk"),
        stress_loader=None if omit_risk_loaders else load("stress"), mandate=inputs.mandate,
        candidate_metrics_loader=load("metrics"), candidate_quote=deepcopy(inputs.quote),
        default_risk_db_matches=False)
    assert inputs.result == before, "A final measurement cannot rewrite the Capo's proposal or research"
    assert receipt["version"] == 1 and receipt["run_id"] == "final-price-refresh-fixture"
    assert REQUIRED_MEASUREMENTS <= receipt["measurements"].keys()
    assert all(type(value) is bool for value in receipt["measurements"].values())
    assert receipt["evidence_sha256"] == trade._plan_digest(receipt["evidence"])
    return receipt, current, calls, observations


def test_final_price_refresh_reloads_once_and_uses_real_sizing_without_rewriting_proposal(final_inputs, monkeypatch):
    receipt, current, calls, seen = _verify(final_inputs, monkeypatch=monkeypatch)
    assert all(receipt["measurements"].values()), receipt
    assert calls == Counter(portfolio=1, risk=1, stress=1, metrics=1,
        native_sizing=1, native_sizing_valid=1, native_fx=1)
    assert len(current.observations) == 2
    assert receipt["before"]["observed_at"] != receipt["after"]["observed_at"]
    assert all(receipt["before"][key] == receipt["after"][key] for key in CONTEXT_FIELDS)
    evidence = receipt["evidence"]
    assert set(evidence) == {"portfolio", "risk", "stress", "sizing", "candidate_quote", "fx_receipt", "proposal"}
    for key in ("portfolio", "risk", "stress"):
        assert evidence[key] == getattr(final_inputs, key)
    assert evidence["candidate_quote"] == final_inputs.quote
    assert evidence["proposal"] == final_inputs.result["proposal"]
    assert evidence["fx_receipt"]["valid"] is True
    assert seen["sizing_inputs"][0] == (final_inputs.portfolio, "ACME.MI", final_inputs.mandate)
    assert seen["sizing_inputs"][1]["risk_data"] == final_inputs.risk
    assert seen["sizing_inputs"][1]["stress_data"] == final_inputs.stress
    candidate = evidence["sizing"]["candidates"][0]
    assert candidate["ticker"] == "ACME.MI" and candidate["class_fallback"] is False
    assert candidate["vol_estimated"] is False and candidate["sector_remaining_eur"] > 1000
    assert candidate["max_add_eur"] > final_inputs.result["proposal"]["eur_amount"]
    frozen_evidence = deepcopy(evidence)
    final_inputs.portfolio["positions"][0]["valore_mercato_eur"] += 999
    final_inputs.result["proposal"]["eur_amount"] += 999
    assert receipt["evidence"] == frozen_evidence, "Receipt must not alias mutable loader or Capo objects"


@pytest.mark.parametrize("changed_field", CONTEXT_FIELDS)
def test_final_price_refresh_context_race_never_certifies_operation(final_inputs, changed_field):
    receipt, current, calls, _ = _verify(final_inputs, store=FrozenContextStore(changed_field))
    assert all(receipt["measurements"][key] for key in REQUIRED_MEASUREMENTS)
    assert receipt["measurements"]["context_stable"] is False
    assert receipt["before"][changed_field] != receipt["after"][changed_field]
    assert receipt.get("reason")
    assert len(current.observations) == 2
    assert calls == Counter(portfolio=1, risk=1, stress=1, metrics=1)


@pytest.mark.parametrize("fault,failed_measurement", [
    ("risk", "risk_recomputed"), ("stress", "stress_recomputed"),
    ("fx", "fx_verified"), ("quote", "candidate_price_verified"),
    ("quote_identity", "candidate_price_verified"), ("metrics", "proposal_valid"),
    ("amount", "proposal_valid"), ("stress_limit", "proposal_valid"),
    ("stale_portfolio", "portfolio_reloaded"),
])
def test_final_price_refresh_preserves_financial_failures(final_inputs, fault, failed_measurement):
    if fault in ("risk", "stress"):
        setattr(final_inputs, fault, {"error": "Frozen missing " + fault})
    elif fault == "fx":
        final_inputs.portfolio["positions"][0]["fx_source"] = "fallback"
    elif fault == "quote":
        final_inputs.quote = {"status": "unavailable", "ticker": "ACME.MI", "reason": "Missing quote"}
    elif fault == "quote_identity":
        final_inputs.quote["ticker"] = "OTHER"
    elif fault == "metrics":
        final_inputs.metrics = {"status": "unavailable", "reason": "No measured candidate history"}
    elif fault == "amount":
        final_inputs.result["proposal"]["eur_amount"] = final_inputs.portfolio["cash_disponibile_eur"] + 1
    elif fault == "stress_limit":
        final_inputs.stress["stress_meta"]["window_loss_pct"] = -99.0
    else:
        final_inputs.portfolio["stale_positions"] = [final_inputs.portfolio["positions"][0]["ticker"]]
    receipt, _, _, _ = _verify(final_inputs)
    assert receipt["measurements"][failed_measurement] is False, receipt
    assert not all(receipt["measurements"].values())
    assert receipt["evidence"]["proposal"] == final_inputs.result["proposal"]
    if fault in ("amount", "stress_limit"):
        assert receipt["measurements"]["sizing_recomputed"] is True
        assert receipt["measurements"]["risk_recomputed"] is True
        assert receipt["measurements"]["stress_recomputed"] is True


@pytest.mark.parametrize("name", ["portfolio", "risk", "stress", "metrics"])
def test_final_price_refresh_loader_failure_is_explicit_and_does_not_retry(final_inputs, name):
    receipt, _, calls, _ = _verify(final_inputs, raising=name)
    assert calls[name] == 1
    assert not all(receipt["measurements"].values())
    diagnostic = str(receipt)
    assert "Frozen unavailable " + name in diagnostic


def test_final_price_refresh_alternate_db_cannot_fall_through_to_global_risk(final_inputs, monkeypatch):
    from bellomberg.portfolio import portfolio_risk, portfolio_montecarlo
    forbidden_calls = []

    def forbidden(*_a, **_k):
        forbidden_calls.append("global database risk")
        raise AssertionError("Alternate DB must not call global risk or stress")

    monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk", forbidden)
    monkeypatch.setattr(portfolio_montecarlo, "run_monte_carlo", forbidden)
    receipt, _, calls, _ = _verify(final_inputs, omit_risk_loaders=True)
    assert receipt["measurements"]["risk_recomputed"] is False
    assert receipt["measurements"]["stress_recomputed"] is False
    assert receipt["measurements"]["proposal_valid"] is False
    assert calls == Counter(portfolio=1, metrics=1)
    assert forbidden_calls == []
    assert "loader isolato richiesto" in str(receipt["evidence"])
