"""Esecuzione dei pattern dei profili filing in un PROCESSO separato con tempo massimo (REV_G2b A1, C1).

PERCHE' (04/10/2026)
    Le regole di verifica e le intestazioni delle sezioni sono regex salvate nei profili (scritte
    a mano, o da proposte AI precedenti). Una regex lenta tiene il GIL: misura della revisione,
    thread principale fermo 87 s con «(a|a)+$», 2,85 s con «.*Outlook» su una riga di 18.000
    caratteri. Python non interrompe un `re` in corso e non si aggiungono dipendenze: i pattern
    dei profili si ESEGUONO solo nel processo figlio (solo `re` e `json`, nessun import del
    progetto), che restituisce gli esiti (posizioni, gruppi nominati, indici delle righe che
    combaciano). Il backend NON riesegue mai `re` su quei pattern (seconda revisione C1: provare
    e poi rieseguire teneva il GIL fino a 5 s per prova). Oltre il tempo il figlio si uccide e si
    solleva VerificaNonCompletata («verifica non completata: ...»). MAI un ripiego locale.

TETTI (revisione di sicurezza «resource-cap-bypass», 04/10/2026)
    - testo: MAX_CARATTERI (come il motore I-20); righe: MAX_CARATTERI in somma (inviate come un
      solo testo: la serializzazione non esplode con milioni di righe brevi);
    - operazioni per lotto: MAX_PATTERN, pattern al piu' MAX_LUNGHEZZA_PATTERN caratteri;
    - tempo per lotto: TEMPO_MAX_S; tempo TOTALE per verifica di un documento: `budget(...)`
      (anche l'attesa del lucchetto conta: le richieste in coda non sommano i loro tempi);
    - corrispondenze per finditer: MAX_CORRISPONDENZE (oltre = verifica non completata);
    - memoria del figlio: MEMORIA_MAX_BYTE (Windows: Job Object; altrove: RLIMIT_AS). Se il tetto
      di memoria non si applica lo dice `stato()` e il log; il tetto di tempo resta.

    Il figlio si riusa (si avvia una volta); gli ESITI sono in cache per (testo, operazione,
    pattern): la stessa esecuzione non si ripete nel giro e la cache non riesegue nulla.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import queue
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict

TEMPO_MAX_S = 5.0          # per lotto di operazioni sullo stesso testo
TEMPO_DOCUMENTO_S = 20.0   # tempo TOTALE delle prove per la verifica di un documento (budget)
TEMPO_AVVIO_S = 60.0       # avvio del processo figlio (non conta nel tempo dei pattern)
MAX_CARATTERI = 5_000_000  # testo (e somma delle righe) per lotto: come il limite del motore I-20
MAX_PATTERN = 64
MAX_LUNGHEZZA_PATTERN = 2_000  # come la validazione dei profili (filing_store)
MAX_CORRISPONDENZE = 10_000
MEMORIA_MAX_BYTE = 512 * 1024 * 1024
_CACHE_MAX = 2048
_log = logging.getLogger(__name__)

_FIGLIO = r"""
import json, re, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (int(sys.argv[1]), int(sys.argv[1])))
except Exception:
    pass  # Windows: il tetto lo mette il Job Object del padre
inp, out = sys.stdin.buffer, sys.stdout.buffer
out.write(b'{"esito": "pronto"}\n'); out.flush()
for riga in inp:
    m = json.loads(riga)
    testo, righe = m["testo"], (m["righe"].split("\n") if m["righe"] else [])
    risultati, esito = [], {"esito": "ok"}
    try:
        for tipo, p in m["ops"]:
            try:
                rx = re.compile(p, re.I | re.M if tipo != "righe" else re.I)
            except re.error as exc:
                esito = {"esito": "re.error", "pattern": p, "messaggio": str(exc)}
                break
            if tipo == "search":
                x = rx.search(testo)
                risultati.append(None if x is None else [x.start(), x.end(), x.groupdict()])
            elif tipo == "finditer":
                tutti = []
                for x in rx.finditer(testo):
                    if len(tutti) >= m["max"]:
                        esito = {"esito": "troppe", "pattern": p}
                        break
                    tutti.append([x.start(), x.end(), x.groupdict()])
                risultati.append(tutti)
            else:
                risultati.append([i for i, r in enumerate(righe) if rx.fullmatch(r)])
            if esito["esito"] != "ok":
                break
    except MemoryError:
        esito, risultati = {"esito": "memoria"}, []
    esito["risultati"] = risultati
    out.write((json.dumps(esito) + "\n").encode()); out.flush()
"""


class VerificaNonCompletata(ValueError):
    """Esecuzione dei pattern non completata (tempo, tetti, processo): esito dichiarato, mai un blocco."""


class PatternTroppoLento(VerificaNonCompletata):
    """Un pattern del profilo supera il tempo massimo."""


class Corrispondenza:
    """Esito di una corrispondenza calcolato nel figlio, con l'interfaccia di `re.Match` che serve
    ai chiamanti (start/end/groupdict/[0]/[nome]): il backend non riesegue il pattern."""

    __slots__ = ("_testo", "_inizio", "_fine", "_gruppi")

    def __init__(self, testo, inizio, fine, gruppi):
        self._testo, self._inizio, self._fine, self._gruppi = testo, int(inizio), int(fine), dict(gruppi or {})

    def start(self):
        return self._inizio

    def end(self):
        return self._fine

    def groupdict(self):
        return dict(self._gruppi)

    def group(self, chiave=0):
        return self[chiave]

    def __getitem__(self, chiave):
        if chiave == 0:
            return self._testo[self._inizio:self._fine]
        return self._gruppi[chiave]


_lock = threading.Lock()
_stato = {"proc": None, "uscite": None, "memoria": None, "job": None}
_esiti: "OrderedDict[tuple, object]" = OrderedDict()
_locale = threading.local()


def stato():
    """Tetti attivi del processo di prova (memoria: 'job_object' / 'rlimit' / motivo dell'assenza)."""
    return {"tempo_max_s": TEMPO_MAX_S, "max_caratteri": MAX_CARATTERI, "max_pattern": MAX_PATTERN,
            "memoria_max_byte": MEMORIA_MAX_BYTE, "tetto_memoria": _stato["memoria"]}


@contextlib.contextmanager
def budget(totale_s):
    """Tempo TOTALE per tutte le esecuzioni fatte dentro il blocco (es. una verifica di documento):
    un profilo con molti pattern lenti non somma N volte TEMPO_MAX_S."""
    precedente = getattr(_locale, "scadenza", None)
    nuova = time.monotonic() + float(totale_s)
    _locale.scadenza = nuova if precedente is None else min(precedente, nuova)
    try:
        yield
    finally:
        _locale.scadenza = precedente


def _impronta(testo):
    return hashlib.sha256(testo.encode("utf-8", "surrogatepass")).hexdigest()


def _lettore(stream, uscite):
    for riga in iter(stream.readline, b""):
        uscite.put(riga)
    uscite.put(None)  # processo terminato


def _chiudi_job_locked():
    job = _stato.get("job")
    _stato["job"] = None
    if job and sys.platform == "win32":
        try:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
        except Exception:
            pass


def _chiudi_locked():
    proc = _stato["proc"]
    _stato["proc"], _stato["uscite"] = None, None
    if proc is not None:
        try:
            proc.kill()
            proc.wait(5)
        except Exception:
            pass
    _chiudi_job_locked()  # handle del Job Object chiuso a ogni riavvio del figlio (REV2 C4)


def _tetto_memoria_windows(proc):
    """Job Object con limite di memoria per processo (ctypes, nessuna dipendenza)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class BASE(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASE), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
    info = EXT()
    info.Basic.LimitFlags = 0x100 | 0x2000  # PROCESS_MEMORY | KILL_ON_JOB_CLOSE
    info.ProcessMemoryLimit = MEMORIA_MAX_BYTE
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(wintypes.HANDLE(job))
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
    handle = k32.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # SET_QUOTA | TERMINATE
    if not handle or not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
        errore = ctypes.get_last_error()
        if handle:
            k32.CloseHandle(wintypes.HANDLE(handle))
        k32.CloseHandle(wintypes.HANDLE(job))
        raise OSError(errore, "AssignProcessToJobObject")
    k32.CloseHandle(wintypes.HANDLE(handle))
    return job  # aperto finche' vive il figlio; chiuso da _chiudi_locked (KILL_ON_JOB_CLOSE)


def _figlio_locked():
    if _stato["proc"] is not None and _stato["proc"].poll() is None:
        return _stato["proc"], _stato["uscite"]
    _chiudi_locked()
    try:
        proc = subprocess.Popen([sys.executable, "-I", "-c", _FIGLIO, str(MEMORIA_MAX_BYTE)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:  # mai un ripiego all'esecuzione locale
        raise VerificaNonCompletata(f"verifica non completata: processo di prova dei pattern non avviato "
                                    f"({type(exc).__name__}: {exc})") from exc
    if sys.platform == "win32":
        try:
            _stato["job"] = _tetto_memoria_windows(proc)
            _stato["memoria"] = "job_object"
        except Exception as exc:
            _stato["memoria"] = f"non applicato: {type(exc).__name__}: {exc}"[:200]
            _log.warning("regex_sandbox: tetto di memoria del processo di prova NON applicato (resta il "
                         "tetto di tempo): %s", exc)
    else:
        _stato["memoria"] = "rlimit"
    uscite = queue.Queue()
    threading.Thread(target=_lettore, args=(proc.stdout, uscite), name="bellomberg-regex-sandbox",
                     daemon=True).start()
    try:
        pronto = uscite.get(timeout=TEMPO_AVVIO_S)
    except queue.Empty:
        pronto = None
    if pronto is None or json.loads(pronto).get("esito") != "pronto":
        proc.kill()
        _chiudi_job_locked()
        raise VerificaNonCompletata("verifica non completata: processo di prova dei pattern non avviato")
    _stato["proc"], _stato["uscite"] = proc, uscite
    return proc, uscite


def _limite(tempo_max_s):
    limite = TEMPO_MAX_S if tempo_max_s is None else float(tempo_max_s)
    scadenza = getattr(_locale, "scadenza", None)
    if scadenza is not None:
        residuo = scadenza - time.monotonic()
        if residuo <= 0:
            raise PatternTroppoLento("verifica non completata: tempo totale della prova dei pattern esaurito")
        limite = min(limite, residuo)
    return limite


def esegui(testo="", ops=(), *, righe=(), tempo_max_s=None):
    """Esegue nel figlio le operazioni [(tipo, pattern)] e ne restituisce gli esiti, nell'ordine:
    - "search": None o Corrispondenza (re.search su `testo`, flag I|M);
    - "finditer": lista di Corrispondenza (al piu' MAX_CORRISPONDENZE, oltre = non completata);
    - "righe": indici delle `righe` che combaciano per intero (fullmatch, flag I).
    `re.error` del pattern si risolleva qui come re.error; tempo e tetti: VerificaNonCompletata."""
    testo = testo or ""
    righe = [str(r) for r in righe]
    ops = [(t, p) for t, p in ops]
    if any(t not in ("search", "finditer", "righe") or not isinstance(p, str) for t, p in ops):
        raise ValueError("operazione o pattern non valido")
    if len(ops) > MAX_PATTERN:
        raise VerificaNonCompletata(f"verifica non completata: oltre {MAX_PATTERN} pattern in una prova")
    if any(len(p) > MAX_LUNGHEZZA_PATTERN for _, p in ops):
        raise VerificaNonCompletata(f"verifica non completata: pattern oltre {MAX_LUNGHEZZA_PATTERN} caratteri")
    if any("\n" in r for r in righe):
        raise ValueError("righe con a capo")
    righe_testo = "\n".join(righe)
    if len(testo) > MAX_CARATTERI or len(righe_testo) > MAX_CARATTERI:
        raise VerificaNonCompletata(f"verifica non completata: testo oltre {MAX_CARATTERI} caratteri")
    if not ops:
        return []
    chiave_testo = _impronta(testo)
    chiave_righe = _impronta(righe_testo + "\x00" + str(len(righe)))
    chiavi = [((chiave_righe if t == "righe" else chiave_testo), t, p) for t, p in ops]
    limite = _limite(tempo_max_s)
    inizio = time.monotonic()
    # l'attesa del lucchetto conta nel tempo: richieste in coda dietro a un figlio fermo non si sommano
    if not _lock.acquire(timeout=limite):
        raise PatternTroppoLento("verifica non completata: prova dei pattern occupata oltre il tempo massimo")
    try:
        mancanti = [i for i, k in enumerate(chiavi) if k not in _esiti]
        if mancanti:
            da_fare = [ops[i] for i in mancanti]
            proc, uscite = _figlio_locked()
            usa_testo = any(t != "righe" for t, _ in da_fare)
            usa_righe = any(t == "righe" for t, _ in da_fare)
            messaggio = json.dumps({"testo": testo if usa_testo else "", "righe": righe_testo if usa_righe else "",
                                    "ops": da_fare, "max": MAX_CORRISPONDENZE}, ensure_ascii=True) + "\n"
            residuo = max(0.05, limite - (time.monotonic() - inizio))
            try:
                proc.stdin.write(messaggio.encode("ascii"))
                proc.stdin.flush()
                risposta = uscite.get(timeout=residuo)
            except (OSError, ValueError):
                risposta = None
            except queue.Empty:
                _chiudi_locked()  # il figlio e' fermo dentro il pattern: si uccide, il prossimo riparte
                elenco = ", ".join(repr(p[:60]) for _, p in da_fare)[:300]
                raise PatternTroppoLento(f"verifica non completata: pattern troppo lento (oltre {limite:g} s): {elenco}")
            if risposta is None:
                _chiudi_locked()
                raise VerificaNonCompletata("verifica non completata: processo di prova dei pattern terminato "
                                            "(memoria oltre il tetto o errore)")
            esito = json.loads(risposta)
            if esito["esito"] == "re.error":
                raise re.error(esito.get("messaggio") or "regex non valida", esito.get("pattern"))
            if esito["esito"] == "troppe":
                raise VerificaNonCompletata(f"verifica non completata: oltre {MAX_CORRISPONDENZE} corrispondenze "
                                            "di un pattern")
            if esito["esito"] == "memoria":
                _chiudi_locked()
                raise VerificaNonCompletata("verifica non completata: memoria esaurita nella prova dei pattern")
            for i, valore in zip(mancanti, esito["risultati"]):
                _esiti[chiavi[i]] = valore
                _esiti.move_to_end(chiavi[i])
        risultati = [_esiti[k] for k in chiavi]
        while len(_esiti) > _CACHE_MAX:
            _esiti.popitem(last=False)
    finally:
        _lock.release()
    out = []
    for (tipo, _), valore in zip(ops, risultati):
        if tipo == "search":
            out.append(None if valore is None else Corrispondenza(testo, *valore))
        elif tipo == "finditer":
            out.append([Corrispondenza(testo, *v) for v in valore])
        else:
            out.append(list(valore))
    return out


def cerca(testo, pattern, **kw):
    return esegui(testo, [("search", pattern)], **kw)[0]


def trova_tutti(testo, pattern, **kw):
    return esegui(testo, [("finditer", pattern)], **kw)[0]


def righe_che_combaciano(righe, *patterns, **kw):
    """Indici (lista per pattern) delle righe che combaciano per intero."""
    return esegui("", [("righe", p) for p in patterns], righe=righe, **kw)


def verifica(testo=None, *, cerca=(), itera=(), righe=(), intere=(), tempo_max_s=None):
    """Compatibilita': esegue le stesse operazioni e scarta gli esiti (VerificaNonCompletata se non
    finisce). I chiamanti del backend usano esegui/cerca/trova_tutti/righe_che_combaciano."""
    ops = ([("search", p) for p in dict.fromkeys(cerca) if isinstance(p, str) and p]
           + [("finditer", p) for p in dict.fromkeys(itera) if isinstance(p, str) and p]
           + [("righe", p) for p in dict.fromkeys(intere) if isinstance(p, str) and p])
    try:
        esegui(testo or "", ops, righe=righe, tempo_max_s=tempo_max_s)
    except re.error:
        pass  # una regex non valida non e' un problema di tempo: la dichiara chi la usa
