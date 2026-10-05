"""REV_G2b B3 (04/10/2026): memo illeggibili = NOVITA' n.d. dichiarata, mai «nessuna novita'» zitta."""
import inspect
from types import SimpleNamespace as NS

from bellomberg.agents import consigliere_multi as cm


def test_memo_illeggibili_dichiarati_nel_log_e_nel_contesto(monkeypatch):
    righe = []
    monkeypatch.setattr(cm, "_log", righe.append)

    def rotto(n):
        raise RuntimeError("database is locked")
    memos, errore = cm._memo_per_novita(NS(get_recent_memos=rotto))
    contesto = cm._con_novita("CONTESTO FILING", errore)
    assert memos == []
    assert contesto.startswith("NOVITÀ: n.d.") and "database is locked" in contesto
    assert contesto.endswith("CONTESTO FILING")
    assert any("NOVITÀ non determinabile" in r for r in righe)


def test_archivio_memo_assente_dichiarato(monkeypatch):
    monkeypatch.setattr(cm, "_log", lambda *a: None)
    memos, errore = cm._memo_per_novita(None)
    contesto = cm._con_novita("X", errore)
    assert memos == [] and contesto.startswith("NOVITÀ: n.d.")


def test_memo_leggibili_contesto_invariato():
    memos, errore = cm._memo_per_novita(NS(get_recent_memos=lambda n: [{"id": 1}]))
    contesto = cm._con_novita("X", errore)
    assert memos == [{"id": 1}] and contesto == "X"


def test_run_usa_l_helper_per_le_novita():
    sorgente = inspect.getsource(cm._run_multi_agent)
    assert "_memo_per_novita(db)" in sorgente and "_con_novita(" in sorgente and "_memos = []" not in sorgente
