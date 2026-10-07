"""Calendar function AST only; never import the API lifecycle."""
import ast
from datetime import date
from pathlib import Path

import pytest
from bellomberg.core import current_facts as cf

SOURCE = Path(__file__).resolve().parents[1] / 'src/bellomberg/api/bellomberg_api.py'


def calendar(today, days):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_hardcoded_economic_calendar')
    scope = {'_api_text': lambda it, en: en}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['_hardcoded_economic_calendar'](today, days)


@pytest.mark.parametrize('day,expected', [(date(2026, 10, 28), '19:00 CET'), (date(2026, 7, 29), '20:00 CEST'), (date(2026, 12, 9), '20:00 CET')])
def test_fomc_event_time(day, expected):
    events = calendar(day, 0)
    event = next(e for e in events if 'FOMC' in e['title'])
    assert event['time'] == expected


def test_cpi_existing_estimate_uses_date_timezone():
    event = next(e for e in calendar(date(2026, 3, 1), 30) if 'CPI / Core' in e['title'])
    assert event['date_estimated'] is True  # March is absent from the existing official table.
    assert event['time'] == '13:30 CET'


def test_cpi_october_official_date_single_no_later_estimate():
    events = [e for e in calendar(date(2026, 10, 1), 30) if 'CPI / Core' in e['title']]
    assert len(events) == 1 and events[0]['date'] == '2026-10-14'
    assert events[0]['date_estimated'] is False and 'bls.gov' in events[0]['source']
    assert not [e for e in calendar(date(2026, 10, 15), 10) if 'CPI / Core' in e['title']]


def test_cpi_unknown_year_is_estimate_and_ppi_stays_estimate():
    cpi = next(e for e in calendar(date(2027, 3, 1), 30) if 'CPI / Core' in e['title'])
    assert cpi['date_estimated'] is True
    ppi = next(e for e in calendar(date(2026, 10, 1), 30) if 'PPI Release' in e['title'])
    assert ppi['date_estimated'] is True and ppi['date'] == '2026-10-14'


def test_fomc_missing_timezone_is_explicit(monkeypatch):
    def missing(name):
        raise cf.ZoneInfoNotFoundError(name)
    monkeypatch.setattr(cf, 'ZoneInfo', missing)
    event = next(e for e in calendar(date(2026, 10, 28), 0) if 'FOMC' in e['title'])
    assert '14:00 America/New_York' in event['time'] and 'Roma n.d.' in event['time']
