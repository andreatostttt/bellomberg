"""Dosaggio del budget dei provider news (02/10/2026): NewsAPI e TheNewsAPI non devono
finire il tetto giornaliero in serata. Il budget si sblocca a rate nella finestra "pace";
oltre la quota del momento il limiter dichiara SKIP_PACING, che non spegne il provider."""
from datetime import datetime

import pytest

import bellomberg.market_data.news_aggregator as na

LIM = {"daily": 99, "cooldown": 6 * 3600, "disable": 12 * 3600, "pace": (6, 23)}


def test_allowance_grows_from_a_small_reserve_to_the_daily_cap():
    day = datetime(2026, 10, 2)
    before = na.pacing_allowance(LIM, day.replace(hour=3))
    start = na.pacing_allowance(LIM, day.replace(hour=6))
    middle = na.pacing_allowance(LIM, day.replace(hour=14, minute=30))
    end = na.pacing_allowance(LIM, day.replace(hour=23))
    late = na.pacing_allowance(LIM, day.replace(hour=23, minute=50))
    assert before == start == round(99 * na.NEWS_PACE_BURST)
    assert start < middle < end
    assert end == late == 99
    # a meta' finestra resta circa meta' del budget non ancora sbloccato
    assert 50 <= middle <= 60


def test_unpaced_provider_has_no_allowance():
    assert na.pacing_allowance({"daily": 800, "cooldown": 1, "disable": 1}, datetime(2026, 10, 2, 12)) is None


def test_next_slot_is_when_the_allowance_reaches_the_next_call():
    when = datetime(2026, 10, 2, 12)
    allowed = na.pacing_allowance(LIM, when)
    assert na.pacing_next_slot(LIM, allowed - 1, when) is None          # chiamata possibile ora
    nxt = na.pacing_next_slot(LIM, allowed, when)
    assert nxt is not None and nxt > when
    assert na.pacing_allowance(LIM, nxt) >= allowed + 1
    assert na.pacing_next_slot(LIM, 99, when) is None                   # tetto: domani, non oggi


@pytest.fixture
def stato(monkeypatch):
    state = {}
    monkeypatch.setattr(na, "_news_rate_state", lambda: state)
    monkeypatch.setattr(na, "_adesso", lambda: datetime(2026, 10, 2, 12))
    monkeypatch.setattr(na.time, "time", lambda: datetime(2026, 10, 2, 12).timestamp())
    real = na.datetime

    class Fisso(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 10, 2, 12)
    monkeypatch.setattr(na, "datetime", Fisso)
    return state


def test_status_declares_pacing_over_the_current_allowance_but_not_below(stato):
    allowed = na.pacing_allowance(na.NEWS_PROVIDER_LIMITS["newsapi"], datetime(2026, 10, 2, 12))
    stato["newsapi"] = {"day": "2026-10-02", "count": allowed - 1}
    assert na.provider_status("newsapi", "query nuova") is None
    stato["newsapi"]["count"] = allowed
    assert na.provider_status("newsapi", "query nuova") == "SKIP_PACING"
    stato["newsapi"]["count"] = 99
    assert na.provider_status("newsapi", "query nuova") == "SKIP_BUDGET"


def test_pacing_is_not_a_muted_source_and_budget_is_reported(stato, monkeypatch):
    allowed = na.pacing_allowance(na.NEWS_PROVIDER_LIMITS["newsapi"], datetime(2026, 10, 2, 12))
    stato["newsapi"] = {"day": "2026-10-02", "count": allowed}
    monkeypatch.setattr(na, "_chiavi_provider", lambda: {})
    assert "newsapi" not in na.providers_blocked()
    budget = na.providers_budget()
    assert budget["newsapi"]["used"] == allowed and budget["newsapi"]["allowed_now"] == allowed
    assert budget["newsapi"]["next_call_at"] > "2026-10-02T12:00"
    assert budget["gnews"]["allowed_now"] is None and budget["gnews"]["pace"] is None
    # un altro giorno nello stato non conta come consumo di oggi
    stato["thenewsapi"] = {"day": "2026-10-01", "count": 80}
    assert na.providers_budget()["thenewsapi"]["used"] == 0
