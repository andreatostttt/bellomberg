"""Contratto CPI e calendario: dati sintetici, nessun provider o DB reale."""
import sys
from datetime import date
from types import ModuleType

import pytest
from bellomberg.core import current_facts as cf


def _live(monkeypatch, observations):
    fake = ModuleType("bellomberg.agents.agent_tools")
    def fetch(series, last_n=3):
        if series == "CPIAUCSL":
            if isinstance(observations, Exception):
                raise observations
            return {"observations": observations}
        return {"observations": []}
    fake._fred_fetch_series = fetch
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    return "\n".join(cf._live_numbers())


def _obs():
    return [{"date": "2030-12-01", "value": 80.0}] + [
        {"date": f"2031-{m:02d}-01", "value": 100.0 + m}
        for m in range(1, 13)] + [{"date": "2032-01-01", "value": 121.2}]


def test_missing_middle_month_still_compares_same_month(monkeypatch):
    obs = [o for o in _obs() if o["date"] != "2031-06-01"]
    text = _live(monkeypatch, obs)
    assert "+20.0% a/a" in text
    assert "2032-01-01" in text and "2031-01-01" in text


def test_missing_year_ago_is_unavailable(monkeypatch):
    text = _live(monkeypatch, [o for o in _obs() if o["date"] != "2031-01-01"])
    assert "CPI USA: n.d." in text
    assert "stesso mese" in text


def test_unsorted_input_uses_latest_date(monkeypatch):
    assert "+20.0% a/a" in _live(monkeypatch, list(reversed(_obs())))


def test_only_exact_pair_is_sufficient(monkeypatch):
    assert "+20.0% a/a" in _live(monkeypatch, [_obs()[1], _obs()[-1]])


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), ".", None, True])
def test_invalid_cpi_is_explicit(monkeypatch, value):
    obs = _obs()
    obs[-1]["value"] = value
    assert "CPI USA: n.d." in _live(monkeypatch, obs)


@pytest.mark.parametrize("extra", [
    {"date": "2032-01-01", "value": 999.0},
    {"date": "2032-01-02", "value": 121.2},
    {"date": "bad-date", "value": 100.0},
])
def test_ambiguous_month_or_invalid_date_is_unavailable(monkeypatch, extra):
    assert "CPI USA: n.d." in _live(monkeypatch, _obs() + [extra])


@pytest.mark.parametrize("obs", [[], RuntimeError("secret-querystring")])
def test_cpi_empty_or_failure_never_disappears(monkeypatch, obs):
    text = _live(monkeypatch, obs)
    assert "CPI USA: n.d." in text
    assert "secret-querystring" not in text


def test_cpi_definition_and_vintage_are_explicit(monkeypatch):
    text = _live(monkeypatch, _obs())
    assert "CPIAUCSL" in text and "destagionalizzato" in text
    assert "vintage n.d." in text


def test_overflow_ratio_is_unavailable(monkeypatch):
    assert "CPI USA: n.d." in _live(monkeypatch, [
        {"date": "2031-01-01", "value": 1e-300},
        {"date": "2032-01-01", "value": 1e300}])


def test_missing_timezone_data_is_declared(monkeypatch):
    def missing(name):
        raise cf.ZoneInfoNotFoundError(name)
    monkeypatch.setattr(cf, "ZoneInfo", missing)
    assert "ora Roma n.d." in cf._rome_time(date(2026, 10, 28), 14)


@pytest.mark.parametrize("day,expected", [(date(2026, 3, 11), "13:30 CET"),
    (date(2026, 10, 28), "13:30 CET"), (date(2026, 7, 14), "14:30 CEST")])
def test_cpi_rome_timezone(day, expected):
    assert cf._rome_time(day, 8, 30) == expected


def _block(monkeypatch, day):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return day
    monkeypatch.setattr(cf, "date", FrozenDate)
    monkeypatch.setattr(cf, "_live_numbers", lambda: [])
    monkeypatch.setattr(cf, "_BLOCK_CACHE", {"text": None, "ts": 0.0})
    return cf.current_facts_block()


@pytest.mark.parametrize("day,time", [(date(2026, 10, 28), "19:00 CET"),
    (date(2026, 7, 29), "20:00 CEST"), (date(2026, 12, 9), "20:00 CET")])
def test_fomc_rome_timezone(monkeypatch, day, time):
    row = next(r for r in _block(monkeypatch, day).splitlines() if r.startswith("- Prossima riunione FOMC"))
    assert time in row


def test_cpi_calendar_verified_and_exhaustion_explicit(monkeypatch):
    block = _block(monkeypatch, date(2026, 10, 7))
    assert "14/10/2026" in block and "14:30 CEST" in block
    assert "calendario cablato esaurito" in _block(monkeypatch, date(2027, 1, 1))


def test_current_sources_conflict_not_coerced(monkeypatch):
    block = _block(monkeypatch, date(2026, 10, 7))
    assert "ground truth" not in block and "Usa SEMPRE" not in block
    for word in ("definizione", "periodo", "vintage", "conflitto", "scelta silenziosa"):
        assert word in block
