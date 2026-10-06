"""SIGILLO della cartella dati vera (06/10/2026, Opus 5.5 — rapporto LOCK).

Il 06/10 una run pagata del PM e' caduta per «database is locked» mentre girava una suite da
un worktree con la junction `data` -> cartella dati vera: i test vedevano il lock VERO della
run (un 409 in un test) e il lifespan dei TestClient eseguiva `recover_orphan_runs` sul DB
VERO con sqlite3 grezzo. Questi test provano che non puo' piu' succedere:
  1. la cartella dati della suite e' una cartella tmp di SESSIONE, fissata prima dell'import
     (DATA_DIR, SQLITE_PATH, lock della run pagata, heartbeat, trade_ideas/, consensus_cache);
  2. il sigillo (audit hook) fa cadere ogni sqlite3.connect / apertura in scrittura / rename
     dentro la cartella vera, anche via junction e via URI; il marker `legge_db_vero`
     consente SOLO la lettura;
  3. una violazione in un THREAD fa fallire il test in teardown (spia e sigillo);
  4. il lifespan di TestClient(api.app) risolve solo la cartella di sessione.

La cartella «vera» qui e' sempre FINTA: creata in tmp e passata come se fosse la produzione
(BELLOMBERG_SIGILLO_PROTETTE / costruttore del Sigillo). Mai la vera.
"""
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading

import pytest

import _sigillo_dati as sg

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(RADICE, "tests")
SRC = os.path.join(RADICE, "src")


@pytest.fixture
def vera_finta(tmp_path):
    d = tmp_path / "dati_veri_finti"
    d.mkdir()
    db = d / "consigliere.db"
    sqlite3.connect(str(db)).close()     # PRIMA di proteggerla: il DB «del PM» esiste
    return d


@pytest.fixture
def sigillo(vera_finta):
    s = sg.Sigillo([str(vera_finta)], tripwire=True)
    s.sessione = "<test>"
    s.installa()
    try:
        yield s
    finally:
        s.disinstalla()


def _junction(link, bersaglio):
    if os.name != "nt":
        os.symlink(bersaglio, link, target_is_directory=True)
        return
    esito = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(bersaglio)],
                           capture_output=True, encoding="utf-8", errors="replace")
    if esito.returncode != 0:
        pytest.fail("junction non creata: %s" % esito.stdout + esito.stderr)


# ---------------------------------------------------------------------------
# 1. il sigillo: sqlite, file, rename, junction, URI, marker, thread
# ---------------------------------------------------------------------------

def test_sqlite_connect_grezzo_alla_cartella_vera_cade(sigillo, vera_finta):
    with pytest.raises(sg.DatiVeriToccati):
        sqlite3.connect(str(vera_finta / "consigliere.db"))
    assert [v["op"] for v in sigillo.violazioni] == ["sqlite3.connect"]


def test_un_altro_db_nella_cartella_vera_cade_lo_stesso(sigillo, vera_finta):
    """Non solo consigliere.db: journal, store di trade idea, filing… tutta la cartella."""
    with pytest.raises(sg.DatiVeriToccati):
        sqlite3.connect(str(vera_finta / "weekly-zz-requests.sqlite"))
    assert not (vera_finta / "weekly-zz-requests.sqlite").exists()


def test_la_junction_non_inganna(sigillo, vera_finta, tmp_path):
    link = tmp_path / "albero" / "data"
    link.parent.mkdir()
    _junction(link, vera_finta)
    with pytest.raises(sg.DatiVeriToccati):
        sqlite3.connect(str(link / "consigliere.db"))
    with pytest.raises(sg.DatiVeriToccati):
        open(link / "committee_paid_run.lock", "a+b")


def test_uri_in_sola_lettura_senza_marker_cade(sigillo, vera_finta):
    uri = "file:%s?mode=ro" % str(vera_finta / "consigliere.db").replace("\\", "/")
    with pytest.raises(sg.DatiVeriToccati):
        sqlite3.connect(uri, uri=True)
    assert sigillo.violazioni[-1]["lettura"] is True


def test_col_marker_la_sola_lettura_passa_la_scrittura_no(sigillo, vera_finta):
    sigillo.lettura_consentita = True
    db = str(vera_finta / "consigliere.db").replace("\\", "/")
    sqlite3.connect("file:%s?mode=ro" % db, uri=True).close()
    sqlite3.connect("file:///%s?immutable=1" % db, uri=True).close()
    with open(vera_finta / "consigliere.db", "rb") as fh:
        fh.read(1)
    assert sigillo.violazioni == []
    with pytest.raises(sg.DatiVeriToccati):
        sqlite3.connect("file:%s?mode=rw" % db, uri=True)
    with pytest.raises(sg.DatiVeriToccati):
        sqlite3.connect(db)                       # niente URI = lettura E scrittura
    with pytest.raises(sg.DatiVeriToccati):
        open(vera_finta / "nuovo.json", "w")
    assert not (vera_finta / "nuovo.json").exists()


def test_scritture_su_file_di_ogni_via_cadono_prima_di_avvenire(sigillo, vera_finta, tmp_path):
    fuori = tmp_path / "fuori.txt"
    fuori.write_text("x", encoding="utf-8")
    bersaglio = vera_finta / "current_run.json"
    with pytest.raises(sg.DatiVeriToccati):
        os.open(str(bersaglio), os.O_WRONLY | os.O_CREAT)
    with pytest.raises(sg.DatiVeriToccati):
        bersaglio.write_text("{}", encoding="utf-8")
    with pytest.raises(sg.DatiVeriToccati):
        os.replace(str(fuori), str(bersaglio))
    with pytest.raises(sg.DatiVeriToccati):
        os.makedirs(str(vera_finta / "trade_ideas" / "run-zz"))
    with pytest.raises(sg.DatiVeriToccati):
        os.remove(str(vera_finta / "consigliere.db"))
    assert not bersaglio.exists() and fuori.exists()
    assert (vera_finta / "consigliere.db").exists()
    assert not (vera_finta / "trade_ideas").exists()


def test_la_violazione_passa_attraverso_except_exception(sigillo, vera_finta):
    assert issubclass(sg.DatiVeriToccati, BaseException)
    assert not issubclass(sg.DatiVeriToccati, Exception)
    with pytest.raises(sg.DatiVeriToccati):
        try:
            sqlite3.connect(str(vera_finta / "consigliere.db"))
        except Exception:
            pass


def test_il_tmp_e_la_memoria_restano_liberi(sigillo, tmp_path):
    sqlite3.connect(str(tmp_path / "libero.db")).close()
    sqlite3.connect(":memory:").close()
    sqlite3.connect("file::memory:?cache=shared", uri=True).close()
    (tmp_path / "libero.txt").write_text("ok", encoding="utf-8")
    assert sigillo.violazioni == []


def test_la_violazione_in_un_thread_resta_registrata_col_thread(sigillo, vera_finta):
    def lavoro():
        try:
            sqlite3.connect(str(vera_finta / "consigliere.db"))
        except BaseException:
            pass                                 # come muore un'eccezione in un worker
    t = threading.Thread(target=lavoro, name="worker-finto")
    t.start()
    t.join()
    assert [(v["op"], v["thread"]) for v in sigillo.violazioni] == [
        ("sqlite3.connect", "worker-finto")]


def test_in_modalita_conta_registra_e_lascia_fare(vera_finta):
    s = sg.Sigillo([str(vera_finta)], tripwire=False)
    s.installa()
    try:
        sqlite3.connect(str(vera_finta / "consigliere.db")).close()
    finally:
        s.disinstalla()
    assert len(s.violazioni) == 1


@pytest.mark.parametrize("uri, atteso", [
    ("file:C:/x/consigliere.db?mode=ro", ("C:/x/consigliere.db", True)),
    ("file:///C:/x/consigliere.db?immutable=1", ("C:/x/consigliere.db", True)),
    ("file://localhost/C:/x/a%20b.db?mode=rw", ("C:/x/a b.db", False)),
    ("file:data/consigliere.db?mode=ro", ("data/consigliere.db", True)),
    ("C:/x/consigliere.db", ("C:/x/consigliere.db", False)),
    (":memory:", None), ("file::memory:", None), ("file:zz?mode=memory", None),
])
def test_percorso_sqlite_legge_gli_uri(uri, atteso):
    assert sg.percorso_sqlite(uri) == atteso


# ---------------------------------------------------------------------------
# 2. la cartella di sessione: si decide PRIMA dell'import e governa i percorsi
# ---------------------------------------------------------------------------

def test_prepara_ambiente_sposta_una_cartella_vera(vera_finta):
    env = {sg.VARIABILE: str(vera_finta), sg.VARIABILE_EXTRA: str(vera_finta)}
    esito = sg.prepara_ambiente(env, RADICE)
    try:
        assert not esito["ereditata"]
        assert env[sg.VARIABILE] == esito["sessione"]
        assert not sg._sotto(esito["sessione"], str(vera_finta))
        assert any(sg._norm(p) == sg._norm(str(vera_finta)) for p in esito["protette"])
    finally:
        os.rmdir(esito["sessione"])


def test_prepara_ambiente_fuori_dal_tmp_e_vera_e_si_sposta(tmp_path):
    fuori = os.path.join(os.path.splitdrive(RADICE)[0] + os.sep, "zz_dati_che_non_esistono")
    env = {sg.VARIABILE: fuori}
    esito = sg.prepara_ambiente(env, RADICE)
    try:
        assert env[sg.VARIABILE] == esito["sessione"] != fuori
        assert sg._norm(fuori) in [sg._norm(p) for p in esito["protette"]]
        assert not os.path.exists(fuori)
    finally:
        os.rmdir(esito["sessione"])


def test_prepara_ambiente_eredita_la_sessione_del_padre(tmp_path):
    padre = tmp_path / "sessione_del_padre"
    env = {sg.VARIABILE: str(padre)}
    esito = sg.prepara_ambiente(env, RADICE)
    assert esito["ereditata"] and env[sg.VARIABILE] == str(padre) and padre.is_dir()


def test_prepara_ambiente_protegge_junction_e_checkout_principale():
    protette = [sg._norm(p) for p in sg.cartelle_vere(RADICE, {})]
    assert sg._norm(os.path.join(RADICE, "data")) in protette
    principale = sg._checkout_principale(RADICE)
    assert principale and sg._norm(os.path.join(principale, "data")) in protette


def _guardia_locale():
    """Un sigillo in sola MISURA sulle cartelle vere calcolate da capo (non quelle del conftest):
    la prova non deve fidarsi del codice che prova."""
    g = sg.Sigillo(sg.cartelle_vere(RADICE, {}), tripwire=False)
    g.installa()
    return g


def test_la_suite_gira_sulla_cartella_di_sessione():
    """In QUESTO processo: ogni percorso fissato all'import sta nella cartella dell'ambiente
    (sotto il tmp di sistema), nessuno in una cartella vera."""
    import tempfile
    from bellomberg.core import paths
    from bellomberg.storage import memory_db
    from bellomberg.api import trade_idea_routes
    sessione = os.environ.get(sg.VARIABILE, "")
    assert sessione and sg._sotto(os.path.realpath(sessione), os.path.realpath(tempfile.gettempdir())), sessione
    vere = sg.Sigillo(sg.cartelle_vere(RADICE, {}))
    for nome, valore in (("paths.DATA_DIR", paths.DATA_DIR), ("paths.SQLITE_PATH", paths.SQLITE_PATH),
                         ("memory_db.DB_DIR", memory_db.DB_DIR),
                         ("memory_db.SQLITE_PATH", memory_db.SQLITE_PATH),
                         ("trade_idea_routes.DATA_DIR", trade_idea_routes.DATA_DIR)):
        assert not vere.tocca_il_vero(valore), (nome, valore)
        assert sg._sotto(valore, sessione), (nome, valore)
    from tests import conftest
    s = getattr(conftest, "_SIGILLO", None)
    assert s is not None and s.attivo and s.tripwire, "il sigillo di sessione non e' acceso"


_SONDA_PERCORSI = textwrap.dedent(r"""
    import json, os, sys
    sys.path[:0] = [{tests!r}, {src!r}]
    import _sigillo_dati as sg
    esito = sg.prepara_ambiente(os.environ, {radice!r})
    s = sg.Sigillo(esito["protette"], tripwire=True); s.sessione = esito["sessione"]; s.installa()
    from bellomberg.core import paths
    from bellomberg.storage import memory_db
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.agents import trade_idea
    from bellomberg.api import trade_idea_routes
    from bellomberg.api import bellomberg_api as api
    bb = Blackboard()                                  # scrive l'heartbeat alla costruzione
    with trade_idea.exclusive_paid_run():              # prende il lock della run pagata
        attiva = trade_idea.paid_run_is_active()
    print(json.dumps({{"sessione": esito["sessione"], "DATA_DIR": str(paths.DATA_DIR),
        "SQLITE_PATH": str(paths.SQLITE_PATH), "memory_db": memory_db.SQLITE_PATH,
        "DB_DIR": memory_db.DB_DIR, "heartbeat": Blackboard.HEARTBEAT_PATH,
        "lock": str(paths.DATA_DIR / "committee_paid_run.lock"),
        "trade_ideas": str(trade_idea_routes.DATA_DIR / "trade_ideas"),
        "consensus_cache": os.path.join(api.DB_DIR, "consensus_cache"),
        "api_sqlite": api.SQLITE_PATH, "violazioni": s.violazioni}}))
""")


def test_in_un_processo_nuovo_ogni_percorso_nasce_nella_sessione(vera_finta, tmp_path):
    """La prova del punto 1 nel modo in cui la vive la suite: un processo che PRIMA prepara
    l'ambiente e POI importa bellomberg. La cartella «vera» (finta) e' anche la
    BELLOMBERG_DATA_DIR dell'ambiente: senza il sigillo i percorsi finirebbero li'."""
    import json
    env = dict(os.environ)
    env[sg.VARIABILE] = str(vera_finta)
    env[sg.VARIABILE_EXTRA] = str(vera_finta)
    env["PYTHONPATH"] = SRC
    sonda = tmp_path / "sonda.py"
    sonda.write_text(_SONDA_PERCORSI.format(tests=TESTS, src=SRC, radice=RADICE), encoding="utf-8")
    esito = subprocess.run([sys.executable, "-B", str(sonda)], cwd=str(tmp_path), env=env,
                           capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    assert esito.returncode == 0, esito.stderr[-3000:]
    misura = json.loads(esito.stdout.strip().splitlines()[-1])
    sessione = misura.pop("sessione")
    assert misura.pop("violazioni") == []
    try:
        for nome, valore in misura.items():
            assert sg._sotto(valore, sessione), (nome, valore)
            assert not sg._sotto(valore, str(vera_finta)), (nome, valore)
        assert os.path.isfile(misura["heartbeat"]) and os.path.isfile(misura["lock"])
        assert sorted(os.listdir(vera_finta)) == ["consigliere.db"]
    finally:
        import shutil
        shutil.rmtree(sessione, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. il conftest vero, in una sessione pytest figlia: teardown e thread
# ---------------------------------------------------------------------------

_TEST_FIGLI = textwrap.dedent(r"""
    import os, sqlite3, threading, uuid
    import pytest

    VERA = os.environ["BELLOMBERG_SIGILLO_PROTETTE"]

    def test_tocca_il_vero_e_inghiotte():
        try:
            sqlite3.connect(os.path.join(VERA, "consigliere.db"))
        except BaseException:
            pass                       # anche un except largo: cade in teardown

    def test_tocca_il_vero_da_un_thread():
        t = threading.Thread(target=lambda: sqlite3.connect(os.path.join(VERA, "consigliere.db")))
        t.start(); t.join()

    def test_scrive_in_produzione_da_un_thread():
        bersaglio = os.path.join({research!r}, "_sigillo_canarino_%s.txt" % uuid.uuid4().hex)
        def scrivi():
            with open(bersaglio, "w") as fh:
                fh.write("x")
        t = threading.Thread(target=scrivi)
        t.start(); t.join()
        assert not os.path.exists(bersaglio)

    @pytest.mark.legge_db_vero
    def test_legge_il_vero_col_marker():
        db = os.path.join(VERA, "consigliere.db").replace("\\", "/")
        sqlite3.connect("file:%s?mode=ro" % db, uri=True).close()

    def test_pulito(tmp_path):
        sqlite3.connect(str(tmp_path / "x.db")).close()
""")


def test_il_conftest_fa_cadere_le_violazioni_in_teardown(vera_finta, tmp_path):
    research = os.path.join(RADICE, "research_notes")
    figlio = tmp_path / "test_figli_sigillo.py"
    figlio.write_text(_TEST_FIGLI.replace("{research!r}", repr(research)), encoding="utf-8")
    env = dict(os.environ)
    env[sg.VARIABILE_EXTRA] = str(vera_finta)
    env["PYTHONPATH"] = os.pathsep.join([TESTS, SRC])
    env.pop("BELLOMBERG_SPIA_SCRITTURE", None)
    env.pop(sg.VARIABILE_MODALITA, None)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    esito = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", str(figlio), "-p", "conftest", "-q", "-rA",
         "-p", "no:cacheprovider"],
        cwd=str(tmp_path), env=env, capture_output=True, encoding="utf-8", errors="replace",
        timeout=600)
    out = esito.stdout + esito.stderr
    assert esito.returncode != 0, out[-4000:]
    righe = {r.split(" ", 1)[1].split(" ", 1)[0].split("::")[-1]: r.split(" ", 1)[0]
             for r in out.splitlines()
             if r.startswith(("PASSED ", "FAILED ", "ERROR ")) and "::" in r}
    assert righe.get("test_tocca_il_vero_e_inghiotte") == "ERROR", out[-4000:]
    assert righe.get("test_tocca_il_vero_da_un_thread") == "ERROR", out[-4000:]
    assert righe.get("test_scrive_in_produzione_da_un_thread") == "ERROR", out[-4000:]
    assert righe.get("test_legge_il_vero_col_marker") == "PASSED", out[-4000:]
    assert righe.get("test_pulito") == "PASSED", out[-4000:]
    assert "SIGILLO: il test ha toccato la cartella dati VERA" in out
    assert "SPIA: il test ha provato a scrivere in produzione" in out
    assert sorted(os.listdir(vera_finta)) == ["consigliere.db"]
    assert not [f for f in os.listdir(research) if f.startswith("_sigillo_canarino_")] \
        if os.path.isdir(research) else True


# ---------------------------------------------------------------------------
# 4. il lifespan di TestClient(api.app) risolve solo la cartella di sessione
# ---------------------------------------------------------------------------

def test_il_lifespan_risolve_solo_la_cartella_di_sessione(monkeypatch):
    from types import SimpleNamespace as NS
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api
    from bellomberg.api import trade_idea_routes
    from bellomberg.market_data import fund_market_worker
    visti = []
    vera_recover = trade_idea_routes.recover_orphan_runs
    vero_start = fund_market_worker.start_market_updates

    def recover(db_path, *a, **k):
        visti.append(("recover_orphan_runs", str(db_path)))
        return vera_recover(db_path, *a, **k)      # quella VERA, sotto il sigillo

    def start(db_path, cache_dir, *a, **k):
        visti.append(("fund_market_worker.db", str(db_path)))
        visti.append(("fund_market_worker.cache", str(cache_dir)))
        return vero_start(db_path, cache_dir, *a, **k)

    monkeypatch.setattr(trade_idea_routes, "recover_orphan_runs", recover)
    monkeypatch.setattr(fund_market_worker, "start_market_updates", start)
    monkeypatch.setattr(api, "news_refresh_manager", NS(start=lambda immediate=True: None,
                                                        stop=lambda: None))
    guardia = _guardia_locale()
    try:
        with TestClient(api.app, base_url="http://127.0.0.1:8765"):
            esito = api.app.state.trade_idea_recovery
    finally:
        guardia.disinstalla()
    assert [n for n, _ in visti] == ["recover_orphan_runs", "fund_market_worker.db",
                                     "fund_market_worker.cache"], visti
    for nome, percorso in visti:
        assert not guardia.tocca_il_vero(percorso), (nome, percorso)
    assert guardia.violazioni == [], guardia.violazioni
    sessione = os.environ.get(sg.VARIABILE, "")
    for nome, percorso in visti:
        assert sessione and sg._sotto(percorso, sessione), (nome, percorso, sessione)
    assert esito.get("status") is not None, esito
