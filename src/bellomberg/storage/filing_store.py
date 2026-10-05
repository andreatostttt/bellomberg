"""Archivio SQLite I-20. Lo schema del DB vivo si installa solo esplicitamente."""
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS filing_profiles (
      id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, version INTEGER NOT NULL,
      profile_json TEXT NOT NULL, profile_sha256 TEXT NOT NULL,
      enabled INTEGER NOT NULL, interval_hours INTEGER NOT NULL,
      qualitative_enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(ticker, version))""",
    "CREATE INDEX IF NOT EXISTS idx_filing_profiles_ticker ON filing_profiles(ticker, version)",
    """CREATE TABLE IF NOT EXISTS filing_runs (
      id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, profile_version INTEGER NOT NULL,
      profile_sha256 TEXT NOT NULL, profile_json TEXT NOT NULL,
      qualitative_enabled INTEGER NOT NULL, judgment_language TEXT NOT NULL,
      trigger TEXT NOT NULL,
      started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
      reason TEXT, evidence_key TEXT, result_json TEXT, judgment_json TEXT,
      index_json TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_filing_runs_active ON filing_runs(ticker) WHERE status IN ('queued','running')",
    "CREATE INDEX IF NOT EXISTS idx_filing_runs_ticker ON filing_runs(ticker,id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_filing_runs_evidence ON filing_runs(evidence_key,id DESC)",
    "CREATE TRIGGER IF NOT EXISTS filing_profiles_no_update BEFORE UPDATE ON filing_profiles BEGIN SELECT RAISE(ABORT,'filing profile immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS filing_profiles_no_delete BEFORE DELETE ON filing_profiles BEGIN SELECT RAISE(ABORT,'filing profile immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS filing_runs_final_immutable BEFORE UPDATE ON filing_runs WHEN OLD.status NOT IN ('queued','running') BEGIN SELECT RAISE(ABORT,'filing run immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS filing_runs_no_delete BEFORE DELETE ON filing_runs BEGIN SELECT RAISE(ABORT,'filing run immutable'); END",
)
TABLES = ("filing_profiles", "filing_runs")
GUARDS = ("filing_profiles_no_update", "filing_profiles_no_delete",
          "filing_runs_final_immutable", "filing_runs_no_delete")


ORE_RITENTATIVO = 2  # primo nuovo tentativo dopo un run in errore (anche orfani recuperati)
_ULTIMI = "SELECT started_at,status,profile_sha256 FROM filing_runs WHERE ticker=? ORDER BY id DESC LIMIT 8"


def _scadenza(ultimi, interval_hours, profilo_sha256=None):
    """Prossimo run dovuto dopo gli ultimi run [(started_at, status, profile_sha256)], dal piu' recente.

    Dopo n errori consecutivi: 2, 4, 8... ore (2 × 2^(n-1)), mai oltre l'intervallo del profilo.
    Profilo cambiato dopo l'ultimo run (es. variante IR aggiunta): dovuto subito.
    """
    if profilo_sha256 is not None and len(ultimi[0]) > 2 and ultimi[0][2] != profilo_sha256:
        return datetime.fromisoformat(ultimi[0][0])
    errori = 0
    for riga in ultimi:
        stato = riga[1]
        if stato != "errore":
            break
        errori += 1
    ore = interval_hours if not errori else min(interval_hours, ORE_RITENTATIVO * 2 ** (errori - 1))
    return datetime.fromisoformat(ultimi[0][0]) + timedelta(hours=ore)


class RunAlreadyActive(RuntimeError):
    pass


class RunNotDue(RuntimeError):
    pass


def ensure_schema(conn):
    """Solo migrazione esplicita; non chiamata dal costruttore né dalle letture."""
    for statement in SCHEMA:
        conn.execute(statement)


def _schema_signature(conn):
    from bellomberg.storage.sqlite_checks import schema_fingerprint
    keys = {kind + ":" + name for kind, name, table in conn.execute(
        "SELECT type,name,tbl_name FROM sqlite_master") if table in TABLES}
    return {key: value for key, value in schema_fingerprint(conn).items() if key in keys}


def _expected_signature():
    with sqlite3.connect(":memory:") as conn:
        ensure_schema(conn)
        return _schema_signature(conn)


_FORME_SEC = ("10-K", "10-Q", "20-F", "40-F", "6-K")
_CHIAVI_VARIANTE = {"tipo", "forme_sec", "sezioni", "verifica", "sezioni_salta_indice",
                    "sezioni_intero", "periodo_regola", "stesso_periodo"}
# Fase C: solo nei profili ESEF a blocchi, la variante IR infrannuale (PDF) accanto all'annuale.
_CHIAVI_VARIANTE_IR = {"fonti", "ir_urls", "lingua", "proposta_ai"}


def unisci_variante(profilo, variante):
    """Profilo completo di una variante: base senza `varianti`, variante sopra, verifica fusa."""
    base = {k: v for k, v in profilo.items() if k != "varianti"}
    out = {**base, **{k: v for k, v in variante.items() if k != "verifica"}}
    out["verifica"] = {**base.get("verifica", {}), **variante.get("verifica", {})}
    if "fonti" in variante and "esef" not in variante["fonti"]:
        out.pop("esef_modo", None)  # variante IR di un profilo ESEF: PDF, non text block
    return out


def _validate_profile(ticker, profile, interval_hours):
    def public_host(host):
        if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host  # DNS risolto e verificato dal downloader prima di ogni GET

    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,29}", ticker):
        raise ValueError("ticker non valido")
    if not isinstance(profile, dict) or profile.get("ticker") != ticker:
        raise ValueError("ticker del profilo mancante o differente")
    for key in ("emittente_id", "lingua", "tipo", "perimetro"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError(f"profilo: {key} obbligatorio")
    if not re.fullmatch(r"(?:CIK:[0-9]+|LEI:[A-Za-z0-9]+|EMITTENTE:.+)", profile["emittente_id"]):
        raise ValueError("emittente_id non valido")
    if profile["tipo"] not in ("annuale", "semestrale", "trimestrale", "nove_mesi"):
        raise ValueError("tipo relazione non valido")
    if not re.fullmatch(r"[a-z]{2}", profile["lingua"]):
        raise ValueError("lingua non valida")
    if not isinstance(profile.get("verifica"), dict) or not isinstance(profile.get("sezioni"), dict):
        raise ValueError("verifica e sezioni obbligatorie")
    for key in ("sezioni_intero", "sezioni_salta_indice"):
        if key in profile and type(profile[key]) is not bool:
            raise ValueError(f"{key} deve essere booleano")
    if "esef_modo" in profile:
        # Fase B: confronto sui text block dello xBRL-JSON; le sezioni sono i concetti IFRS.
        if profile["esef_modo"] != "blocchi":
            raise ValueError("esef_modo non valido")
        if not profile["emittente_id"].startswith("LEI:") or profile.get("fonti") != ["esef"]:
            raise ValueError("esef_modo richiede emittente LEI e fonti [esef]")
        if profile["tipo"] != "annuale":
            raise ValueError("esef_modo: solo relazioni annuali")
        if "varianti" in profile:
            # Fase C: l'annuale ESEF piu' infrannuali da PDF IR (proposta AI accettata).
            varianti = profile["varianti"] if isinstance(profile["varianti"], list) else []
            esef_v = [v for v in varianti if isinstance(v, dict) and v.get("fonti", ["esef"]) == ["esef"]]
            ir_v = [v for v in varianti if isinstance(v, dict) and v.get("fonti") == ["ir"]]
            if (len(esef_v) != 1 or esef_v[0].get("tipo") != "annuale" or len(esef_v) + len(ir_v) != len(varianti)
                    or any(v.get("tipo") == "annuale" for v in ir_v)):
                raise ValueError("esef_modo: varianti ammesse solo come annuale ESEF piu' infrannuali IR")
    if not profile["sezioni"] and profile.get("sezioni_intero") is not True and "esef_modo" not in profile:
        raise ValueError("verifica e sezioni obbligatorie")
    if "periodo_regola" in profile and profile["periodo_regola"] != "piu_recente":
        raise ValueError("periodo_regola non valida")
    if "stesso_periodo" in profile and profile["stesso_periodo"] != "piu_lungo":
        raise ValueError("stesso_periodo non valido")
    if "forme_sec" in profile and (not isinstance(profile["forme_sec"], list) or not profile["forme_sec"]
                                   or any(f not in _FORME_SEC for f in profile["forme_sec"])):
        raise ValueError("forme_sec non valide")
    for key in ("lingua", "tipo", "perimetro"):
        pat = profile["verifica"].get(key)
        if not isinstance(pat, str) or not pat:
            raise ValueError(f"verifica.{key} obbligatoria")
    for key, pat in profile["verifica"].items():
        if key not in ("lingua", "tipo", "perimetro", "emittente", "periodo") or not isinstance(pat, str) or len(pat) > 2000:
            raise ValueError(f"verifica.{key} non valida")
        re.compile(pat)
    for name, section in profile["sezioni"].items():
        if not isinstance(name, str) or not name or not isinstance(section, dict) or set(section) != {"inizio", "fine"}:
            raise ValueError("regola sezione non valida")
        for pat in section.values():
            if not isinstance(pat, str) or not pat or len(pat) > 2000:
                raise ValueError("regex sezione non valida")
            re.compile(pat)
    if "fonti" in profile and (not isinstance(profile["fonti"], list) or any(f not in ("sec", "esef", "ir") for f in profile["fonti"])):
        raise ValueError("fonti non valide")
    urls = profile.get("ir_urls", [])
    if not isinstance(urls, list):
        raise ValueError("ir_urls deve essere una lista")
    for url in urls:
        if not isinstance(url, str):
            raise ValueError("ir_urls non validi")
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not public_host(parts.hostname) or parts.username or parts.password:
            raise ValueError("URL IR non valido")
    hosts = profile.get("host_documenti", [])
    if not isinstance(hosts, list) or any(not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", h) or not public_host(h.lower()) for h in hosts):
        raise ValueError("host_documenti non validi")
    if "varianti" in profile:
        varianti = profile["varianti"]
        if not isinstance(varianti, list) or not 1 <= len(varianti) <= 3:
            raise ValueError("varianti: da 1 a 3")
        tipi = [v.get("tipo") if isinstance(v, dict) else None for v in varianti]
        if len(set(tipi)) != len(tipi):
            raise ValueError("varianti: tipi ripetuti")
        for v in varianti:
            ammesse = _CHIAVI_VARIANTE | (_CHIAVI_VARIANTE_IR if "esef_modo" in profile else set())
            if not isinstance(v, dict) or not set(v) <= ammesse or "tipo" not in v:
                raise ValueError("variante non valida")
            if "verifica" in v and not isinstance(v["verifica"], dict):
                raise ValueError("variante: verifica non valida")
            _validate_profile(ticker, unisci_variante(profile, v), interval_hours)
    if type(interval_hours) is not int or not 1 <= interval_hours <= 8760:
        raise ValueError("interval_hours fuori intervallo")
    raw = _json(profile)
    if len(raw) > 100_000:
        raise ValueError("profilo troppo grande")
    return raw


class FilingStore:
    def __init__(self, db_path):
        self.db_path = os.fspath(db_path)
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(f"DB filing assente: {self.db_path}")
        with self._connect(check=False) as conn:
            actual, expected = _schema_signature(conn), _expected_signature()
            if any(actual.get(key) != value for key, value in expected.items()):
                raise RuntimeError("schema filing assente o incompatibile: eseguire migrazione esplicita")

    @contextmanager
    def _connect(self, check=True):
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(f"DB filing assente: {self.db_path}")
        from bellomberg.storage import memory_db
        conn = memory_db.connect_sqlite(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def set_profile(self, ticker, profile, enabled=True, interval_hours=168, qualitative_enabled=False):
        raw = _validate_profile(ticker, profile, interval_hours)
        if type(enabled) is not bool or type(qualitative_enabled) is not bool:
            raise ValueError("enabled e qualitative_enabled devono essere booleani")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prev = conn.execute("SELECT * FROM filing_profiles WHERE ticker=? ORDER BY version DESC LIMIT 1", (ticker,)).fetchone()
            if prev and (prev["profile_sha256"], prev["enabled"], prev["interval_hours"], prev["qualitative_enabled"]) == (digest, int(enabled), interval_hours, int(qualitative_enabled)):
                return self._profile(prev)
            version = prev["version"] + 1 if prev else 1
            cur = conn.execute("INSERT INTO filing_profiles(ticker,version,profile_json,profile_sha256,enabled,interval_hours,qualitative_enabled,created_at) VALUES(?,?,?,?,?,?,?,?)",
                               (ticker, version, raw, digest, int(enabled), interval_hours, int(qualitative_enabled), _now()))
            return self._profile(conn.execute("SELECT * FROM filing_profiles WHERE id=?", (cur.lastrowid,)).fetchone())

    @staticmethod
    def _profile(row):
        if row is None:
            return None
        return {"ticker": row["ticker"], "version": row["version"], "profile": json.loads(row["profile_json"]),
                "profile_sha256": row["profile_sha256"], "enabled": bool(row["enabled"]),
                "interval_hours": row["interval_hours"], "qualitative_enabled": bool(row["qualitative_enabled"]),
                "created_at": row["created_at"]}

    def get_profile(self, ticker):
        with self._connect() as conn:
            return self._profile(conn.execute("SELECT * FROM filing_profiles WHERE ticker=? ORDER BY version DESC LIMIT 1", (ticker,)).fetchone())

    def list_profiles(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT p.* FROM filing_profiles p JOIN (SELECT ticker,MAX(version) v FROM filing_profiles GROUP BY ticker) latest ON p.ticker=latest.ticker AND p.version=latest.v ORDER BY p.ticker").fetchall()
            return [self._profile(r) for r in rows]

    def start_run(self, ticker, trigger="manual", language=None):
        if trigger not in ("manual", "scheduled"):
            raise ValueError("trigger non valido")
        from bellomberg.core.language import capture_language
        language = capture_language(language)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            p = conn.execute("SELECT * FROM filing_profiles WHERE ticker=? ORDER BY version DESC LIMIT 1", (ticker,)).fetchone()
            if not p or (trigger == "scheduled" and not p["enabled"]):
                raise ValueError("profilo assente o scheduler disabilitato")
            if conn.execute("SELECT 1 FROM filing_runs WHERE ticker=? AND status IN ('queued','running')", (ticker,)).fetchone():
                raise RunAlreadyActive("run già attivo: recovery esplicito necessario se il processo è terminato")
            if trigger == "scheduled":
                ultimi = conn.execute(_ULTIMI, (ticker,)).fetchall()
                if ultimi and _scadenza(ultimi, p["interval_hours"], p["profile_sha256"]) > datetime.now(timezone.utc):
                    raise RunNotDue("run schedulato non ancora dovuto: deadline ricontrollata nel claim atomico")
            cur = conn.execute("INSERT INTO filing_runs(ticker,profile_version,profile_sha256,profile_json,qualitative_enabled,judgment_language,trigger,started_at,status) VALUES(?,?,?,?,?,?,?,?,'queued')",
                               (ticker, p["version"], p["profile_sha256"], p["profile_json"], p["qualitative_enabled"], language, trigger, _now()))
            return self.get_run_from_conn(conn, cur.lastrowid)

    @staticmethod
    def get_run_from_conn(conn, run_id):
        row = conn.execute("SELECT * FROM filing_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        out = {k: row[k] for k in ("id", "ticker", "profile_version", "profile_sha256", "judgment_language", "trigger", "started_at", "finished_at", "status", "reason", "evidence_key")}
        out["profile"] = json.loads(row["profile_json"])
        out["qualitative_enabled"] = bool(row["qualitative_enabled"])
        out.update(result=json.loads(row["result_json"]) if row["result_json"] else None,
                   judgment=json.loads(row["judgment_json"]) if row["judgment_json"] else None,
                   index=json.loads(row["index_json"]) if row["index_json"] else None)
        return out

    def get_run(self, run_id):
        with self._connect() as conn:
            return self.get_run_from_conn(conn, run_id)

    def claim_execution(self, run_id):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("UPDATE filing_runs SET status='running' WHERE id=? AND status='queued'", (run_id,))
            if cur.rowcount != 1:
                raise RuntimeError("run già preso, concluso o inesistente")
            return self.get_run_from_conn(conn, run_id)

    def list_runs(self, ticker, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit non valido")
        with self._connect() as conn:
            ids = conn.execute("SELECT id FROM filing_runs WHERE ticker=? ORDER BY id DESC LIMIT ?", (ticker, limit)).fetchall()
            return [self.get_run_from_conn(conn, r[0]) for r in ids]

    def ultimo_run_completo(self, ticker, *, senza_errori=False):
        """Run concluso piu' recente che non e' un controllo leggero, senza limite di finestra.

        `senza_errori`: esclude anche i run in errore (riferimento del controllo leggero)."""
        esclusi = "('queued','running','errore')" if senza_errori else "('queued','running')"
        with self._connect() as conn:
            row = conn.execute(f"SELECT id FROM filing_runs WHERE ticker=? AND status NOT IN {esclusi} "
                               "AND (result_json IS NULL OR json_extract(result_json,'$.controllo_leggero') IS NULL) "
                               "ORDER BY id DESC LIMIT 1", (ticker,)).fetchone()
            return self.get_run_from_conn(conn, row[0]) if row else None

    def confronto_ed_errore(self, ticker):
        """(run del confronto, errore successivo): la selezione comune a tutti i lettori.

        Run del confronto: ultimo run concluso, non leggero, con un confronto (corrente o
        storico); senza, l'ultimo run completo. Errore: l'ultimo run completo, se e' in errore
        e successivo al run del confronto (altrimenti None, anche senza confronto).
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM filing_runs WHERE ticker=? AND status NOT IN ('queued','running') "
                "AND result_json IS NOT NULL AND json_extract(result_json,'$.controllo_leggero') IS NULL "
                "AND (json_extract(result_json,'$.confronto_corrente') IS NOT NULL "
                "OR json_extract(result_json,'$.confronto_storico') IS NOT NULL) ORDER BY id DESC LIMIT 1",
                (ticker,)).fetchone()
            confronto = self.get_run_from_conn(conn, row[0]) if row else None
        completo = self.ultimo_run_completo(ticker)
        errore = completo if (confronto and completo and completo["status"] == "errore"
                              and completo["id"] > confronto["id"]) else None
        return confronto or completo, errore

    def coppia_al(self, ticker, istante):
        """Coppia (sha prima, sha dopo) del run del confronto corrente a `istante` (datetime
        con fuso): ultimo run con un confronto concluso entro quell'istante. None se assente."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT finished_at, json_extract(result_json,'$.coppia.prima.sha256'), "
                "json_extract(result_json,'$.coppia.dopo.sha256') FROM filing_runs WHERE ticker=? "
                "AND status NOT IN ('queued','running') AND result_json IS NOT NULL "
                "AND json_extract(result_json,'$.controllo_leggero') IS NULL "
                "AND (json_extract(result_json,'$.confronto_corrente') IS NOT NULL "
                "OR json_extract(result_json,'$.confronto_storico') IS NOT NULL) ORDER BY id DESC",
                (ticker,)).fetchall()
        for fatto, prima, dopo in rows:
            try:
                t = datetime.fromisoformat(fatto)
            except (TypeError, ValueError):
                continue
            if t.tzinfo is not None and t <= istante:
                return (prima, dopo)
        return None

    def finish_run(self, run_id, *, status, reason=None, evidence_key=None, result=None, judgment=None, index=None):
        if status not in ("ok", "parziale", "errore", "non_disponibile", "skipped"):
            raise ValueError("status finale non valido")
        values = tuple(_json(v) if v is not None else None for v in (result, judgment, index))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("UPDATE filing_runs SET finished_at=?,status=?,reason=?,evidence_key=?,result_json=?,judgment_json=?,index_json=? WHERE id=? AND status IN ('queued','running')",
                               (_now(), status, reason, evidence_key, *values, run_id))
            if cur.rowcount != 1:
                raise RuntimeError("run non attivo o già concluso")
            return self.get_run_from_conn(conn, run_id)

    def recover_run(self, run_id, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("motivo recovery obbligatorio")
        return self.finish_run(run_id, status="errore", reason="recovery esplicito: " + reason)

    def find_evidence(self, evidence_key):
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM filing_runs WHERE evidence_key=? AND judgment_json IS NOT NULL AND status IN ('ok','parziale') ORDER BY id DESC LIMIT 1", (evidence_key,)).fetchone()
            return self.get_run_from_conn(conn, row[0]) if row else None

    def next_due(self, now=None):
        now = now or datetime.now(timezone.utc)
        if isinstance(now, str):
            now = datetime.fromisoformat(now)
        if now.tzinfo is None:
            raise ValueError("now deve avere timezone")
        due = []
        with self._connect() as conn:
            for p in self.list_profiles():
                if not p["enabled"]:
                    continue
                ultimi = conn.execute(_ULTIMI, (p["ticker"],)).fetchall()
                last = ultimi[0] if ultimi else None
                active = conn.execute("SELECT 1 FROM filing_runs WHERE ticker=? AND status IN ('queued','running')", (p["ticker"],)).fetchone()
                next_at = _scadenza(ultimi, p["interval_hours"], p["profile_sha256"]) if last else None
                if not active and (next_at is None or next_at <= now):
                    due.append({"ticker": p["ticker"], "next_due": next_at.isoformat() if next_at else None,
                                "last_attempt": last[0] if last else None})
        return due

    def next_due_at(self, ticker):
        profile = self.get_profile(ticker)
        if not profile or not profile["enabled"]:
            return None
        with self._connect() as conn:
            ultimi = conn.execute(_ULTIMI, (ticker,)).fetchall()
        return _scadenza(ultimi, profile["interval_hours"], profile["profile_sha256"]).isoformat() if ultimi else _now()

    def active_runs(self):
        """Run queued/running: id, ticker, status, started_at, trigger."""
        with self._connect() as conn:
            rows = conn.execute("SELECT id,ticker,status,started_at,trigger FROM filing_runs "
                                "WHERE status IN ('queued','running') ORDER BY id").fetchall()
            return [dict(r) for r in rows]
