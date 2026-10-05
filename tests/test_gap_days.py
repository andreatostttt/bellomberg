"""Gap-days 17/09 — scomposizione del P&L finestra per seduta di borsa.

Offline: chiusure/fx/live iniettati, attesi CALCOLATI A MANO. Niente rete.
"""
import pytest

from bellomberg.portfolio.gap_days import split_sessions

FX1 = {"2026-09-14": 1.0, "2026-09-15": 1.0, "2026-09-16": 1.0}


def test_due_sedute_con_stub_riconciliato():
    """AAA qty 10: chiusure 14->100, 15->90; live 95; baseline (snapshot lun 11:07) 99.
    15/09: 10*(90-100) = -100. Oggi: 10*(95-90) = +50. Stub: 10*(100-99) = +10.
    Check finestra: 10*(95-99) = -40 = -100 + 50 + 10."""
    out = split_sessions(
        sessions=["2026-09-14", "2026-09-15"],
        today="2026-09-16",
        closes={"AAA": {"2026-09-14": 100.0, "2026-09-15": 90.0}},
        baseline={"AAA": 99.0},
        qty={"AAA": 10.0},
        live={"AAA": 95.0},
        fx={"AAA": dict(FX1)},
    )
    assert out["error"] is None
    assert out["days"]["2026-09-15"]["pnl_eur"] == pytest.approx(-100.0)
    assert out["days"]["2026-09-15"]["forming"] is False
    assert out["days"]["2026-09-16"]["pnl_eur"] == pytest.approx(50.0)
    assert out["days"]["2026-09-16"]["forming"] is True
    assert out["stub_eur"] == pytest.approx(10.0)
    assert out["window_check_eur"] == pytest.approx(-40.0)
    assert out["unpriced"] == []


def test_chiusura_mancante_dichiarata_e_today_su_ancora():
    """BBB qty 5: 14->50, 15 n.d., live 60, baseline 48.
    Nessuna gamba chiusa-chiusa; today su ancora del 14: 5*(60-50) = +50.
    Stub: 5*(50-48) = +10. BBB dichiarato missing sul 15/09."""
    out = split_sessions(
        sessions=["2026-09-14", "2026-09-15"],
        today="2026-09-16",
        closes={"BBB": {"2026-09-14": 50.0, "2026-09-15": None}},
        baseline={"BBB": 48.0},
        qty={"BBB": 5.0},
        live={"BBB": 60.0},
        fx={"BBB": dict(FX1)},
    )
    assert out["error"] is None
    assert out["days"]["2026-09-15"]["pnl_eur"] == pytest.approx(0.0)
    assert out["days"]["2026-09-15"]["missing"] == ["BBB"]
    assert out["days"]["2026-09-16"]["pnl_eur"] == pytest.approx(50.0)
    assert out["stub_eur"] == pytest.approx(10.0)
    assert out["window_check_eur"] == pytest.approx(60.0)


def test_seduta_finalizzata_gamba_chiusa_piu_drift_live():
    """AAA: 14->100, 15->110, 16 chiusa a 121; live 121.5, baseline 99.
    15/09: +100. 16/09: 10*(121-110)=+110 piu' drift 10*(121.5-121)=+5 -> +115,
    forming False (chiusura presente). Stub +10, check +225 = 10*(121.5-99)."""
    out = split_sessions(
        sessions=["2026-09-14", "2026-09-15", "2026-09-16"],
        today="2026-09-16",
        closes={"AAA": {"2026-09-14": 100.0, "2026-09-15": 110.0,
                        "2026-09-16": 121.0}},
        baseline={"AAA": 99.0},
        qty={"AAA": 10.0},
        live={"AAA": 121.5},
        fx={"AAA": {"2026-09-14": 1.0, "2026-09-15": 1.0, "2026-09-16": 1.0}},
    )
    assert out["days"]["2026-09-15"]["pnl_eur"] == pytest.approx(100.0)
    assert out["days"]["2026-09-16"]["pnl_eur"] == pytest.approx(115.0)
    assert out["days"]["2026-09-16"]["forming"] is False
    assert out["window_check_eur"] == pytest.approx(225.0)


def test_acquisto_in_finestra_ancorato_al_costo():
    """AAA: 6 vecchie (baseline snapshot 99) + 4 comprate in finestra @105.
    Chiuse 14->100, 15->110; live 115. qty_sod il 15: 6.
    15/09: 6*(110-100) = +60. Oggi: 6*(115-110) + 4*(115-105) = 30+40 = +70.
    Stub solo vecchie: 6*(100-99) = +6. Check: 96 + 40 = 136."""
    out = split_sessions(
        sessions=["2026-09-14", "2026-09-15"],
        today="2026-09-16",
        closes={"AAA": {"2026-09-14": 100.0, "2026-09-15": 110.0}},
        baseline={"AAA": 99.0},
        qty={"AAA": 10.0},
        live={"AAA": 115.0},
        fx={"AAA": {"2026-09-14": 1.0, "2026-09-15": 1.0, "2026-09-16": 1.0}},
        qty_sod={"AAA": {"2026-09-15": 6.0}},
        adds={"AAA": [{"qty": 4.0, "price": 105.0, "fx": 1.0}]},
    )
    assert out["error"] is None
    assert out["days"]["2026-09-15"]["pnl_eur"] == pytest.approx(60.0)
    assert out["days"]["2026-09-16"]["pnl_eur"] == pytest.approx(70.0)
    assert out["stub_eur"] == pytest.approx(6.0)
    assert out["window_check_eur"] == pytest.approx(136.0)


def test_senza_chiusure_nome_escluso_dichiarato():
    out = split_sessions(
        sessions=["2026-09-14", "2026-09-15"],
        today="2026-09-16",
        closes={"CCC": {"2026-09-14": None, "2026-09-15": None}},
        baseline={"CCC": 10.0},
        qty={"CCC": 3.0},
        live={"CCC": 11.0},
        fx={"CCC": dict(FX1)},
    )
    assert out["unpriced"] == ["CCC"]
    assert out["window_check_eur"] == pytest.approx(0.0)
