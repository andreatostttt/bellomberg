"""PM 04/10/2026, option A: prices and FX moving at most 0.5% keep an operational proposal valid."""
from bellomberg.agents import trade_idea
from bellomberg.core.trade_idea_policy import within_price_tolerance
from bellomberg.storage.trade_idea_store import _book_within_tolerance


def test_tolerance_boundary_is_half_a_percent_both_ways():
    assert within_price_tolerance("4.4", "4.422") and within_price_tolerance("4.4", "4.378")
    assert not within_price_tolerance("4.4", "4.43") and not within_price_tolerance("4.4", "4.37")
    assert not within_price_tolerance(None, "100") and not within_price_tolerance("0", "0")


def _quote(price, **change):
    return {"status": "ready", "ticker": "ACME", "price": price, "price_asof": "2026-10-05T10:00:00Z",
            "source": "yfinance", "currency": "EUR", "currency_basis": "EUR", **change}


def test_candidate_quote_tolerates_small_moves_but_never_identity_changes():
    assert trade_idea._candidate_quote_matches(_quote(100.0), _quote(100.4, price_asof="2026-10-05T10:15:00Z"))
    assert not trade_idea._candidate_quote_matches(_quote(100.0), _quote(101.0))
    assert not trade_idea._candidate_quote_matches(_quote(100.0), _quote(100.1, currency="USD"))
    assert not trade_idea._candidate_quote_matches(_quote(100.0), _quote(100.1, source="other"))
    assert not trade_idea._candidate_quote_matches(_quote(100.0), {**_quote(100.0), "status": "stale"})


def test_fx_observations_tolerate_small_rate_moves_only():
    base = [{"ticker": "ZETA", "currency": "USD", "rate": "0.9200", "source": "live"}]
    assert trade_idea._fx_observations_match(base, [{**base[0], "rate": "0.9230"}])
    assert not trade_idea._fx_observations_match(base, [{**base[0], "rate": "0.9300"}])
    assert not trade_idea._fx_observations_match(base, [{**base[0], "source": "cache"}])
    assert not trade_idea._fx_observations_match(base, [])


def _book(price, **change):
    return {"position": None, "trades_sha256": "t", "trades_count": 1, "portfolio_sha256": "p",
            "price_inputs_sha256": "h-" + str(price), "price_points": [["ZETA", price, "USD", "yfinance"]],
            "non_eur_active_currencies": ["USD"], "cash_state": [100, 1], **change}


def test_book_tolerance_for_new_runs_and_exact_match_for_historical_contexts():
    assert _book_within_tolerance(_book(100.0), _book(100.3))
    assert not _book_within_tolerance(_book(100.0), _book(101.0))
    assert not _book_within_tolerance(_book(100.0), _book(100.1, trades_count=2))  # a trade is never tolerated
    assert not _book_within_tolerance(_book(100.0), _book(100.1, cash_state=[90, 2]))
    historical = {key: value for key, value in _book(100.0).items() if key != "price_points"}
    assert _book_within_tolerance(historical, historical)
    assert not _book_within_tolerance(historical, {**historical, "price_inputs_sha256": "moved"})
