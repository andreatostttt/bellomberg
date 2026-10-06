"""_sigillo_dati.py — il SIGILLO della cartella dati vera (06/10/2026, Opus 5.5).

Perche' esiste (rapporto LOCK del 06/10): durante una run pagata del PM i test lanciati da
un worktree con la junction `data` -> cartella dati vera vedevano il lock VERO della run
(`committee_paid_run.lock`, un 409 in un test) e il lifespan dei TestClient eseguiva
`recover_orphan_runs` sul DB VERO con `sqlite3.connect` grezzo, fuori dal tripwire (b) del
conftest (che copre solo `memory_db.connect_sqlite` e lo store del watch). La run e' caduta
per «database is locked»: la causa non e' provata, ma l'esposizione si'.

Due presidi, di natura diversa:
  1. `prepara_ambiente` (chiamata in TESTA a tests/conftest.py, prima di ogni import di
     bellomberg): BELLOMBERG_DATA_DIR verso una cartella tmp di SESSIONE. `core.paths` e
     `memory_db` fissano i percorsi all'import, quindi da li' in poi DATA_DIR, SQLITE_PATH,
     DB_DIR, il lock della run pagata, l'heartbeat, i journal, trade_ideas/ e
     consensus_cache nascono nel tmp. Sandbox per costruzione.
  2. `Sigillo`: un AUDIT HOOK di processo (sys.addaudithook) che vede `sqlite3.connect`,
     ogni `open` (anche os.open e le aperture da C che passano dagli eventi di audit), e
     rename/replace/remove/mkdir/rmdir/copyfile. Un evento che tocca la cartella dati VERA
     (confronto su realpath: la junction non inganna) e' una VIOLAZIONE: registrata, e in
     modalita' tripwire sollevata con BaseException (passa gli `except Exception`). Copre
     i thread e la collection: il hook e' del processo, non della fixture.
     Eccezione unica e dichiarata: un test col marker `legge_db_vero` puo' LEGGERE (sqlite
     con `mode=ro`/`immutable=1`, file aperti in sola lettura). Scrivere mai.

LIMITE DICHIARATO: i SOTTOPROCESSI non ereditano il hook. Ereditano l'ambiente (la cartella
di sessione): un test che costruisce un ambiente da zero, o lancia Python senza PYTHONPATH
(l'install editable porta al checkout principale), esce dal sigillo. Il sigillo li CONTA
(evento `subprocess.Popen` con un env esplicito senza la cartella di sessione) e li scrive
nel rapporto, senza fermarli.
"""
import os
import sys
import tempfile
import threading
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote

VARIABILE = "BELLOMBERG_DATA_DIR"
# cartelle vere in piu' (separate da os.pathsep): la leva dei test del sigillo, che passano una
# cartella FINTA come se fosse quella di produzione
VARIABILE_EXTRA = "BELLOMBERG_SIGILLO_PROTETTE"
# «conta» = fase di misura (registra e lascia fare); default «tripwire»
VARIABILE_MODALITA = "BELLOMBERG_SIGILLO"
MARKER = "legge_db_vero"

_FLAG_SCRITTURA = (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
_EVENTI_MUTANTI = {
    # evento -> indici degli argomenti che sono percorsi
    "os.rename": (0, 1), "os.remove": (0,), "os.mkdir": (0,), "os.rmdir": (0,),
    "os.truncate": (0,), "shutil.copyfile": (1,), "shutil.copytree": (1,),
    "shutil.rmtree": (0,), "shutil.move": (0, 1), "os.symlink": (1,), "os.link": (1,),
    "os.chmod": (0,), "os.utime": (0,),
}


class DatiVeriToccati(BaseException):
    """Un test ha toccato la cartella dati VERA (DB del PM, lock della run pagata, heartbeat...).

    BaseException e non Exception, come `conftest.ProduzioneToccata` e
    `_spia_scritture.ScritturaInProduzione`: i punti che aprono il DB stanno spesso dentro
    `try/except Exception` e un'eccezione normale verrebbe inghiottita."""


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p)))


def _reale(p) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(p)))


def _forme(p) -> set:
    """Le forme confrontabili di un percorso: assoluta (com'e' scritta, junction compresa) e
    reale (junction risolta). Una delle due basta a riconoscerlo."""
    forme = {_norm(p)}
    try:
        forme.add(_reale(p))
    except (OSError, ValueError):
        pass
    return forme


def _sotto(p, base) -> bool:
    a, b = _norm(p), _norm(base)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def _checkout_principale(radice: str) -> Optional[str]:
    """In un worktree `.git` e' un FILE «gitdir: <principale>/.git/worktrees/<nome>»: la
    cartella dati del checkout principale e' quella vera anche se il worktree non ha la
    junction. Nel checkout principale `.git` e' una cartella: la radice stessa."""
    dotgit = os.path.join(radice, ".git")
    if os.path.isdir(dotgit):
        return radice
    try:
        with open(dotgit, encoding="utf-8") as fh:
            riga = fh.read().strip()
    except OSError:
        return None
    if not riga.startswith("gitdir:"):
        return None
    gitdir = riga[len("gitdir:"):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(radice, gitdir)
    common = gitdir
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as fh:
            c = fh.read().strip()
        common = c if os.path.isabs(c) else os.path.join(gitdir, c)
    except OSError:
        pass
    return os.path.dirname(os.path.normpath(common))


def _valore_dotenv(percorso: str, nome: str) -> Optional[str]:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return None
    try:
        return (dotenv_values(percorso) or {}).get(nome)
    except Exception:
        return None


def cartelle_vere(radice: str, environ: Dict[str, str],
                  tmp_sistema: Optional[str] = None) -> List[str]:
    """Le cartelle dati da proteggere, PRIMA che il conftest sposti BELLOMBERG_DATA_DIR:
    - `<radice>/data` (la junction del checkout o del worktree);
    - `<checkout principale>/data` (un worktree senza junction raggiunge comunque il vero
      attraverso l'install editable del principale);
    - BELLOMBERG_DATA_DIR dell'ambiente e del .env di quei due alberi, se dichiarate;
    - le cartelle in BELLOMBERG_SIGILLO_PROTETTE.
    Una cartella che non esiste si protegge lo stesso: puo' nascere durante la sessione."""
    candidati: List[str] = [os.path.join(radice, "data")]
    principale = _checkout_principale(radice)
    alberi = [radice] + ([principale] if principale and _norm(principale) != _norm(radice) else [])
    if len(alberi) > 1:
        candidati.append(os.path.join(alberi[1], "data"))
    # quella dell'ambiente conta come VERA solo fuori dal tmp di sistema: sotto il tmp e' la
    # cartella di sessione ereditata da un pytest padre (o data da chi lancia la suite)
    dall_ambiente = (environ.get(VARIABILE) or "").strip()
    if dall_ambiente and os.path.isabs(dall_ambiente) and _sotto(dall_ambiente, tmp_sistema or tempfile.gettempdir()):
        dall_ambiente = ""
    dichiarate = [dall_ambiente]
    dichiarate += [_valore_dotenv(os.path.join(a, ".env"), VARIABILE) or "" for a in alberi]
    for valore in dichiarate:
        valore = (valore or "").strip()
        if valore:
            p = os.path.expanduser(valore)
            candidati.append(p if os.path.isabs(p) else os.path.join(radice, p))
    for extra in (environ.get(VARIABILE_EXTRA, "") or "").split(os.pathsep):
        if extra.strip():
            candidati.append(extra.strip())
    visti, uscita = set(), []
    for c in candidati:
        k = _norm(c)
        if k not in visti:
            visti.add(k)
            uscita.append(os.path.abspath(c))
    return uscita


def prepara_ambiente(environ: Dict[str, str], radice: str,
                     tmp_sistema: Optional[str] = None) -> Dict[str, Any]:
    """Fissa BELLOMBERG_DATA_DIR in `environ` PRIMA dell'import di bellomberg.

    Si tiene il valore gia' presente SOLO se sta sotto il tmp di sistema e fuori da ogni
    cartella vera (e' il caso dei sottoprocessi pytest lanciati dalla suite, che ereditano
    la cartella di sessione del padre). Altrimenti si crea una cartella di sessione nuova.
    Ritorna {protette, sessione, ereditata}."""
    tmp = os.path.realpath(tmp_sistema or tempfile.gettempdir())
    protette = cartelle_vere(radice, environ, tmp)
    attuale = (environ.get(VARIABILE) or "").strip()
    if attuale and os.path.isabs(attuale) and _sotto(os.path.realpath(attuale), tmp) and not any(
            _sotto(attuale, p) or (_forme(attuale) & _forme(p)) for p in protette):
        os.makedirs(attuale, exist_ok=True)
        # realpath: core.paths risolve la cartella dati col .resolve(); su macOS (/var -> /private/var)
        # o con TEMP in nome corto (ANDREA~1) il confronto d'avvio altrimenti cadrebbe (R-VELOCE 06/10).
        return {"protette": protette, "sessione": os.path.realpath(attuale), "ereditata": True}
    sessione = os.path.realpath(tempfile.mkdtemp(prefix="bellomberg_dati_sessione_", dir=tmp))
    environ[VARIABILE] = sessione
    return {"protette": protette, "sessione": sessione, "ereditata": False}


def percorso_sqlite(database) -> Optional[tuple]:
    """(percorso, sola_lettura) dal parametro di `sqlite3.connect`, URI compresi
    («file:C:/x.db?mode=ro», «file:///C:/x.db?immutable=1»). None per i DB in memoria."""
    if isinstance(database, (bytes, bytearray)):
        database = os.fsdecode(bytes(database))
    s = os.fspath(database) if not isinstance(database, str) else database
    if s in ("", ":memory:") or s.startswith("file::memory:"):
        return None
    if not s.startswith("file:"):
        return s, False
    corpo, _, query = s[len("file:"):].partition("?")
    corpo = unquote(corpo.split("#", 1)[0])
    if corpo.startswith("//"):
        corpo = corpo[2:]
        if not corpo.startswith("/"):            # file://host/... : l'host si scarta
            corpo = "/" + corpo.split("/", 1)[1] if "/" in corpo else corpo
    if len(corpo) >= 3 and corpo[0] == "/" and corpo[2] == ":":   # /C:/... su Windows
        corpo = corpo[1:]
    if "mode=memory" in query:
        return None
    parametri = dict(kv.partition("=")[::2] for kv in query.split("&") if kv)
    sola_lettura = parametri.get("mode") == "ro" or parametri.get("immutable") == "1"
    return corpo, sola_lettura


class Sigillo:
    def __init__(self, protette: Iterable[str], tripwire: bool = True):
        self.protette = [os.path.abspath(p) for p in protette]
        self._prefissi = sorted({f for p in self.protette for f in _forme(p)})
        self.tripwire = bool(tripwire)
        self.attivo = False
        self.violazioni: List[Dict[str, Any]] = []
        self.sottoprocessi: List[Dict[str, Any]] = []
        self.sessione: Optional[str] = None
        self.test_corrente: Optional[str] = None
        self.lettura_consentita = False
        self._dentro = threading.local()
        self._cache: Dict[str, bool] = {}

    # ---- classificazione ---------------------------------------------------
    def tocca_il_vero(self, percorso) -> bool:
        """Vero se il percorso sta in una cartella dati vera (assoluto o reale)."""
        if percorso is None or isinstance(percorso, int):
            return False
        try:
            if isinstance(percorso, (bytes, bytearray)):
                percorso = os.fsdecode(bytes(percorso))
            chiave = os.fspath(percorso)
        except TypeError:
            return False
        cwd = os.getcwd() if not os.path.isabs(chiave) else ""
        k = cwd + "|" + chiave
        esito = self._cache.get(k)
        if esito is None:
            esito = False
            for forma in _forme(chiave):
                for pre in self._prefissi:
                    if forma == pre or forma.startswith(pre.rstrip(os.sep) + os.sep):
                        esito = True
                        break
                if esito:
                    break
            if len(self._cache) > 20000:
                self._cache.clear()
            self._cache[k] = esito
        return esito

    def esamina(self, evento: str, args: tuple) -> Optional[Dict[str, Any]]:
        """L'evento di audit, se tocca il vero: {op, percorso, lettura}. None altrimenti."""
        if evento == "sqlite3.connect":
            if not args:
                return None
            try:
                ps = percorso_sqlite(args[0])
            except TypeError:
                return None
            if ps is None or not self.tocca_il_vero(ps[0]):
                return None
            return {"op": "sqlite3.connect", "percorso": str(args[0]), "lettura": ps[1]}
        if evento == "open":
            percorso, modo, flag = (tuple(args) + (None, None, None))[:3]
            if not self.tocca_il_vero(percorso):
                return None
            scrive = (isinstance(flag, int) and bool(flag & _FLAG_SCRITTURA)) or (
                isinstance(modo, str) and any(c in modo for c in "wax+"))
            return {"op": "open", "percorso": str(percorso), "lettura": not scrive,
                    "modo": modo}
        indici = _EVENTI_MUTANTI.get(evento)
        if indici:
            for i in indici:
                if i < len(args) and self.tocca_il_vero(args[i]):
                    return {"op": evento, "percorso": str(args[i]), "lettura": False}
        return None

    def _sottoprocesso(self, args: tuple):
        # subprocess.Popen: (executable, args, cwd, env). env=None eredita la sessione.
        env = args[3] if len(args) > 3 else None
        if env is None or self.sessione is None:
            return
        try:
            valore = env.get(VARIABILE) if hasattr(env, "get") else None
        except Exception:
            valore = None
        if valore and _sotto(valore, tempfile.gettempdir()) and not self.tocca_il_vero(valore):
            return
        self.sottoprocessi.append({"test": self.test_corrente or "<fuori da un test>",
                                   "comando": str(args[1])[:200],
                                   VARIABILE: valore if valore is not None else "<assente>"})

    # ---- hook ------------------------------------------------------------------
    def _hook(self, evento, args):
        if not self.attivo:
            return
        if evento != "sqlite3.connect" and evento != "open" and evento not in _EVENTI_MUTANTI \
                and evento != "subprocess.Popen":
            return
        if getattr(self._dentro, "si", False):
            return
        self._dentro.si = True
        try:
            if evento == "subprocess.Popen":
                self._sottoprocesso(args)
                return
            v = self.esamina(evento, args)
        finally:
            self._dentro.si = False
        if v is None:
            return
        if v["lettura"] and self.lettura_consentita:
            return
        v["test"] = self.test_corrente or "<fuori da un test: import/collection>"
        v["thread"] = threading.current_thread().name
        self.violazioni.append(v)
        if self.tripwire:
            raise DatiVeriToccati(
                "un test ha toccato la CARTELLA DATI VERA: %s %s%s (test: %s, thread %s). "
                "La suite gira su BELLOMBERG_DATA_DIR di sessione (%s): usa tmp_path o i "
                "percorsi di bellomberg.core.paths. Lettura voluta del DB vero: marker "
                "@pytest.mark.%s e connessione 'file:...?mode=ro'." % (
                    v["op"], v["percorso"], " (sola lettura)" if v["lettura"] else " (SCRITTURA)",
                    v["test"], v["thread"], self.sessione, MARKER))

    def installa(self):
        """Il hook di audit non si toglie: si installa una volta e si spegne con `attivo`."""
        if not getattr(self, "_installato", False):
            sys.addaudithook(self._hook)
            self._installato = True
        self.attivo = True
        return self

    def disinstalla(self):
        self.attivo = False

    def violazioni_di(self, nodeid: str, da: int = 0) -> List[Dict[str, Any]]:
        return [v for v in self.violazioni[da:] if v["test"] == nodeid]

    # ---- rapporto ----------------------------------------------------------------
    def rapporto(self) -> str:
        righe = ["SIGILLO DATI VERI (%s): %d violazioni da %d test; cartella di sessione %s"
                 % ("TRIPWIRE" if self.tripwire else "CONTA",
                    len(self.violazioni), len({v["test"] for v in self.violazioni}),
                    self.sessione)]
        righe.append("  cartelle protette: " + "; ".join(self.protette))
        per = {}
        for v in self.violazioni:
            k = (v["test"], v["op"], v["percorso"], v["lettura"])
            per[k] = per.get(k, 0) + 1
        for (test, op, percorso, lettura), n in sorted(per.items()):
            righe.append("  %3d x %-16s %-9s %s  [%s]" % (
                n, op, "lettura" if lettura else "SCRITTURA", percorso, test))
        righe.append("  sottoprocessi con ambiente esplicito SENZA la cartella di sessione: %d"
                     % len(self.sottoprocessi))
        for s in self.sottoprocessi[:50]:
            righe.append("    %s  %s=%s  %s" % (s["test"], VARIABILE, s[VARIABILE], s["comando"]))
        righe.append("  LIMITE: i sottoprocessi non hanno il hook; ereditano solo l'ambiente.")
        return "\n".join(righe)


_DI_SESSIONE: Optional[Sigillo] = None


def sigillo_di_sessione(protette: Iterable[str], tripwire: bool, sessione: str) -> Sigillo:
    """UNO per processo. Il conftest puo' essere importato due volte (come plugin `conftest` e
    come modulo `tests.conftest` dai test che ne importano i nomi): un secondo hook con un suo
    stato non saprebbe quale test gira ne' leggerebbe il marker."""
    global _DI_SESSIONE
    if _DI_SESSIONE is None:
        _DI_SESSIONE = Sigillo(protette, tripwire=tripwire)
        _DI_SESSIONE.sessione = sessione
        _DI_SESSIONE.installa()
    return _DI_SESSIONE
