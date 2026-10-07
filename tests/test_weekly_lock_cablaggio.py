"""La weekly resta dentro il lock della run pagata (R-CASCATA 07/10: un helper inserito fra i
decoratori e `def run_multi_agent` aveva spostato su di lui `@scoped_language` e
`@paid_run_exclusive`, e la weekly girava senza lock)."""
from contextlib import contextmanager

import pytest


def test_weekly_entra_nel_lock_della_run_pagata_prima_di_ogni_lavoro(monkeypatch):
    import bellomberg.agents.consigliere_multi as cm
    import bellomberg.agents.trade_idea as ti

    entrate = []

    @contextmanager
    def occupato():
        entrate.append("lock")
        raise ti.PaidRunBusy("un'altra run pagata e' attiva")
        yield  # pragma: no cover

    monkeypatch.setattr(ti, "exclusive_paid_run", occupato)
    monkeypatch.setattr(cm, "_run_multi_agent",
                        lambda *a, **k: pytest.fail("la weekly e' partita senza il lock"), raising=False)
    with pytest.raises(ti.PaidRunBusy):
        cm.run_multi_agent()
    assert entrate == ["lock"]


def test_arresto_del_preriscaldamento_non_prende_il_lock(monkeypatch):
    import bellomberg.agents.consigliere_multi as cm
    import bellomberg.agents.trade_idea as ti

    @contextmanager
    def vietato():
        pytest.fail("l'arresto del preriscaldamento ha preso il lock della run pagata")
        yield  # pragma: no cover

    monkeypatch.setattr(ti, "exclusive_paid_run", vietato)
    cm._ferma_preriscaldamento()
