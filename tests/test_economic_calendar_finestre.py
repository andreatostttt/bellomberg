"""Calendario macro cablato: una release ricorrente per mese, date stimate dichiarate.

Il difetto (misurato il 02/10/2026 su /news/economic-calendar?days_ahead=30):
le release ricavate da una FINESTRA di giorni (CPI 10-15, PPI 11-16, retail
14-17, ISM, HICP flash >=28, IFO 24-26, PIL USA 26-30) venivano emesse su
OGNI giorno della finestra — CPI il 13, 14 e 15/10, PIL Q3 il 27, 28 e 29/10,
HICP il 28, 29 e 30/10. La deduplica su (data, titolo) non le vedeva.

Ora: al massimo un'occorrenza per release per mese (il primo giorno della
finestra) con `date_estimated: True`; le date esatte (banche centrali, OPEC,
NFP primo venerdi', claims settimanali, PMI Cina il 1) restano senza flag.
"""
from collections import Counter
from datetime import date

from bellomberg.core.language import language_context
from bellomberg.api import bellomberg_api

STIMATE = (
    "US CPI", "US PPI", "US Retail Sales", "ISM Manufacturing", "ISM Services",
    "Eurozone HICP Flash", "Germany IFO", "US GDP",
)
ESATTE = (
    "FOMC", "ECB", "BoE", "BoJ", "OPEC", "Nonfarm Payrolls",
    "Jobless Claims", "China NBS",
)


def _calendario(oggi, giorni):
    with language_context("en"):
        return bellomberg_api._hardcoded_economic_calendar(oggi, giorni)


def test_release_a_finestra_una_volta_per_mese_e_dichiarate_stimate():
    eventi = _calendario(date(2026, 10, 2), 30)

    stimati = [e for e in eventi if e["title"].startswith(STIMATE)]
    assert stimati, eventi
    per_mese = Counter((e["title"], e["date"][:7]) for e in stimati)
    doppi = {k: n for k, n in per_mese.items() if n > 1}
    assert not doppi, doppi
    assert all(e.get("date_estimated") is True for e in stimati), stimati

    date_per_titolo = {e["title"]: e["date"] for e in stimati}
    assert date_per_titolo["US CPI / Core CPI Release"] == "2026-10-13"
    assert date_per_titolo["US PPI Release"] == "2026-10-14"  # giorno dopo il CPI
    assert date_per_titolo["ISM Services PMI"] == "2026-10-05"  # 3o lavorativo
    assert date_per_titolo["US Retail Sales MoM"] == "2026-10-14"
    assert date_per_titolo["US GDP Q3 Advance Estimate"] == "2026-10-27"
    assert date_per_titolo["Eurozone HICP Flash Estimate"] == "2026-10-28"
    assert date_per_titolo["Germany IFO Business Climate"] == "2026-10-26"

    esatti = [e for e in eventi if any(k in e["title"] for k in ESATTE)]
    assert esatti, eventi
    assert all("date_estimated" not in e for e in esatti), esatti
    # ogni evento e' o stimato o esatto: nessuna release sfugge alla classificazione
    assert len(stimati) + len(esatti) == len(eventi)


def test_finestra_gia_iniziata_non_slitta_la_release_a_oggi():
    """Il 14/10 il CPI stimato (13/10) e' passato: non va riemesso il 14 o il 15."""
    eventi = _calendario(date(2026, 10, 14), 10)
    cpi = [e for e in eventi if e["title"].startswith("US CPI")]
    assert cpi == [], cpi


def test_ism_sul_primo_e_terzo_giorno_lavorativo():
    """Ottobre 2026 inizia di giovedi': le vecchie finestre (lun/mar 1-3, mar-gio
    3-5) non davano NESSUN ISM. Ora 1o e 3o giorno lavorativo di ogni mese."""
    eventi = _calendario(date(2026, 10, 1), 60)
    ism = sorted((e["date"], e["title"]) for e in eventi if e["title"].startswith("ISM"))
    assert ism == [
        ("2026-10-01", "ISM Manufacturing PMI"),
        ("2026-10-05", "ISM Services PMI"),
        ("2026-11-02", "ISM Manufacturing PMI"),
        ("2026-11-04", "ISM Services PMI"),
    ], ism
