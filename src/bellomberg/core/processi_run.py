"""Riconoscimento di un processo «run del Consigliere» dalla FORMA della sua riga di comando.

R-8 C6 (06/10/2026, Opus 5.5). Due controlli cercavano una SOTTOSTRINGA in qualunque processo:
- il guardiano «run del Consigliere in corso» (esef_sito.consigliere_in_corso) scattava su
  shell, editor, pytest o py_compile che nominano il file -> rinvio con una frase falsa;
- lo STOP del backend (bellomberg_api.cancel_all_consigliere, scansione degli orfani) cercava
  «consigliere_multi.py», ma la API lancia la run come `python -u -m
  bellomberg.agents.consigliere_multi`: dopo un riavvio del backend (handle persi) la run viva
  non veniva MAI trovata, e lo STOP non la fermava; ucciso invece un `py_compile` qualunque.
Una funzione sola, pura, per entrambi.

Forme riconosciute (le tre vie da cui parte una run):
- python [opzioni] -m bellomberg.agents.consigliere_multi [...]   (backend, /consigliere/run)
- python [opzioni] <...>/consigliere_multi.py [...]               (task schedulato, a mano)
- bellomberg-committee[.exe] [...] oppure python <...>/bellomberg-committee[.exe|-script.py]
  (console-script di pyproject)
«python» = python, python3, pythonw, py (launcher di Windows), con o senza .exe.
"""
import os

MODULO_RUN = "bellomberg.agents.consigliere_multi"
_SCRIPT_RUN = "consigliere_multi.py"
_COMANDO = "bellomberg-committee"
# opzioni dell'interprete che si mangiano anche l'argomento successivo
_OPZIONI_CON_VALORE = ("-X", "-W", "-Q")


def _nome(s):
    return os.path.basename(str(s).replace("\\", "/")).lower()


def _e_interprete(nome):
    radice = nome[:-4] if nome.endswith(".exe") else nome
    return radice in ("python", "pythonw", "py") or (
        radice.startswith("python") and radice[6:].replace(".", "").isdigit())


def _e_comando(nome):
    return nome in (_COMANDO, _COMANDO + ".exe", _COMANDO + "-script.py")


def e_run_consigliere(argv):
    """True solo se `argv` (lista come psutil.Process.cmdline()) E' una run del Consigliere."""
    if not argv or not isinstance(argv, (list, tuple)):
        return False
    primo = _nome(argv[0])
    if _e_comando(primo):
        return True
    if not _e_interprete(primo):
        return False
    args, i = [str(a) for a in argv[1:]], 0
    while i < len(args) and args[i].startswith("-") and args[i] not in ("-m", "-c", "-"):
        i += 2 if args[i] in _OPZIONI_CON_VALORE else 1
    if i >= len(args):
        return False   # solo opzioni: REPL. «-c codice» e «-» (stdin) non sono -m ne' uno script noto
    if args[i] == "-m":
        return i + 1 < len(args) and args[i + 1] == MODULO_RUN
    bersaglio = _nome(args[i])
    return bersaglio == _SCRIPT_RUN or _e_comando(bersaglio)
