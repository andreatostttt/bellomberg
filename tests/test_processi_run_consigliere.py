"""Riconoscimento della run del Consigliere dalla FORMA della riga di comando (R-8 C6, 06/10, Opus 5.5).

Prima: sottostringa «consigliere_multi» su qualunque processo (shell, editor, pytest -> falso
«run in corso») e, nello STOP del backend, sottostringa «consigliere_multi.py» mentre la API
lancia la run con `-m bellomberg.agents.consigliere_multi` (orfano mai trovato: STOP che non
ferma una run dopo un riavvio del backend). Solo argv costruiti e processi finti: nessun kill vero.
"""
from types import SimpleNamespace

import pytest

from bellomberg.core.processi_run import e_run_consigliere

PY = "C:/Users/zz/AppData/Local/Python/python.exe"

CASI = [
    # come la lancia il backend (bellomberg_api, /consigliere/run)
    ([PY, "-u", "-m", "bellomberg.agents.consigliere_multi", "--outcome", "x.json", "--task-id", "t"], True),
    ([PY, "-B", "-X", "utf8", "-m", "bellomberg.agents.consigliere_multi"], True),
    ([PY, "-W", "ignore", "-u", "-m", "bellomberg.agents.consigliere_multi", "--resume-memo-id", "9"], True),
    (["C:/Windows/py.exe", "-3", "-m", "bellomberg.agents.consigliere_multi"], True),
    (["pythonw.exe", "-m", "bellomberg.agents.consigliere_multi"], True),
    # script (task schedulato: `python consigliere_multi.py`)
    (["python.exe", "consigliere_multi.py"], True),
    (["python", "-B", "D:\\progetto\\src\\bellomberg\\agents\\consigliere_multi.py", "--no-email"], True),
    # console-script
    (["C:/Py/Scripts/bellomberg-committee.exe", "--delivery-only"], True),
    ([PY, "C:/Py/Scripts/bellomberg-committee.exe"], True),
    ([PY, "C:/Py/Scripts/bellomberg-committee-script.py"], True),
    # NON sono run
    (["C:/Program Files/Git/bin/bash.exe", "-c", "grep -n consigliere_multi src && git log"], False),
    (["powershell.exe", "-Command", "Get-Content consigliere_multi.py"], False),
    (["Code.exe", "D:/progetto/src/bellomberg/agents/consigliere_multi.py"], False),
    ([PY, "-c", "import bellomberg.agents.consigliere_multi; print('consigliere_multi')"], False),
    ([PY, "-m", "pytest", "tests/test_cablaggio_consigliere_multi.py"], False),
    ([PY, "-m", "py_compile", "src/bellomberg/agents/consigliere_multi.py"], False),
    ([PY, "-m", "bellomberg.agents.consigliere_multi_vecchio"], False),
    ([PY, "tools/leggi_consigliere_multi.py"], False),
    ([PY, "-u", "-m", "bellomberg.api.bellomberg_api"], False),
    ([PY], False),
    ([PY, "-u"], False),
    (["notepad.exe", "bellomberg-committee.txt"], False),
    ([PY, "tools/bellomberg-committee-prova.py"], False),
    (["C:/vecchi/bellomberg-committee-old.exe"], False),
    ([PY, "-", "consigliere_multi.py"], False),
    ([PY, "-c", "consigliere_multi.py"], False),   # il testo dopo -c e' codice, non lo script
    ([], False),
    (None, False),
    ({"0": PY}, False),          # non una lista: False, mai un'eccezione
]


@pytest.mark.parametrize("argv, atteso", CASI)
def test_forma_della_riga_di_comando(argv, atteso):
    assert e_run_consigliere(argv) is atteso


# --- STOP del backend: la scansione degli orfani usa lo stesso riconoscimento

class _Proc:
    def __init__(self, pid, name, cmdline):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "cmdline": cmdline}
        self.uccisi = 0

    def kill(self):
        self.uccisi += 1


def _cancel_all_con(monkeypatch, procs):
    import psutil
    import bellomberg.api.bellomberg_api as api
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: list(procs))
    monkeypatch.setattr(api, "_CONSIGLIERE_PROCS", {}, raising=False)
    api._CONSIGLIERE_PROCS.clear()
    monkeypatch.setattr(api.run_state, "runs", {})
    return api.cancel_all_consigliere()


def test_stop_trova_l_orfano_lanciato_dal_backend_con_meno_m(monkeypatch):
    run = _Proc(4101, "python.exe", [PY, "-u", "-m", "bellomberg.agents.consigliere_multi", "--outcome", "x"])
    r = _cancel_all_con(monkeypatch, [run])
    assert run.uccisi == 1 and r["n_killed"] == 1
    assert r["killed"][0]["pid"] == 4101


def test_stop_trova_il_console_script(monkeypatch):
    run = _Proc(4102, "bellomberg-committee.exe", ["C:/Py/Scripts/bellomberg-committee.exe"])
    _cancel_all_con(monkeypatch, [run])
    assert run.uccisi == 1


def test_stop_non_uccide_cio_che_non_e_una_run(monkeypatch):
    innocenti = [
        _Proc(4201, "python.exe", [PY, "-m", "py_compile", "src/bellomberg/agents/consigliere_multi.py"]),
        _Proc(4202, "python.exe", [PY, "-m", "pytest", "tests/test_cablaggio_consigliere_multi.py"]),
        _Proc(4203, "bash.exe", ["bash.exe", "-c", "grep consigliere_multi.py"]),
        _Proc(4204, "python.exe", [PY, "-u", "-m", "bellomberg.api.bellomberg_api"]),
    ]
    r = _cancel_all_con(monkeypatch, innocenti)
    assert [p.uccisi for p in innocenti] == [0, 0, 0, 0] and r["n_killed"] == 0
