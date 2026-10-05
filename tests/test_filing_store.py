import sqlite3

import pytest

from bellomberg.storage.filing_store import FilingStore, RunNotDue, ensure_schema


def profile(ticker="ABC"):
    return {"ticker": ticker, "emittente_id": "CIK:123", "lingua": "en", "tipo": "annuale",
            "perimetro": "consolidato", "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
            "sezioni": {"rischi": {"inizio": "Risks", "fine": "End"}}, "fonti": []}


def store(tmp_path):
    path = tmp_path / "filing.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    return FilingStore(path)


def test_schema_is_never_created_implicitly(tmp_path):
    path = tmp_path / "none.sqlite"
    with pytest.raises(FileNotFoundError):
        FilingStore(path)
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated(a)")
    with pytest.raises(RuntimeError, match="schema filing assente"):
        FilingStore(path)


def test_schema_signature_rejects_missing_atomic_claim_index(tmp_path):
    s = store(tmp_path)
    with sqlite3.connect(s.db_path) as conn:
        conn.execute("DROP INDEX idx_filing_runs_active")
    with pytest.raises(RuntimeError, match="schema filing assente o incompatibile"):
        FilingStore(s.db_path)


def test_profile_revision_claim_and_crash_recovery(tmp_path):
    s = store(tmp_path)
    p = s.set_profile("ABC", profile(), qualitative_enabled=True)
    assert p["version"] == 1
    assert s.set_profile("ABC", profile(), qualitative_enabled=True)["version"] == 1
    queued = s.start_run("ABC")
    assert queued["status"] == "queued" and queued["profile_version"] == 1
    s.set_profile("ABC", profile(), interval_hours=24)
    assert s.get_run(queued["id"])["profile_version"] == 1
    with pytest.raises(RuntimeError, match="run già attivo"):
        s.start_run("ABC")
    s.claim_execution(queued["id"])
    with pytest.raises(RuntimeError):
        s.claim_execution(queued["id"])
    done = s.recover_run(queued["id"], "processo terminato")
    assert done["status"] == "errore" and "recovery esplicito" in done["reason"]
    assert s.start_run("ABC")["profile_version"] == 2


def test_due_uses_last_attempt_even_after_failure(tmp_path):
    s = store(tmp_path)
    s.set_profile("ABC", profile(), interval_hours=24)
    assert [d["ticker"] for d in s.next_due()] == ["ABC"]
    row = s.start_run("ABC")
    s.finish_run(row["id"], status="errore", reason="network")
    assert s.next_due() == []
    assert s.list_runs("ABC")[0]["reason"] == "network"


def test_disabled_scheduler_allows_manual_and_final_rows_are_immutable(tmp_path):
    s = store(tmp_path)
    s.set_profile("ABC", profile(), enabled=False)
    assert s.next_due() == [] and s.next_due_at("ABC") is None
    with pytest.raises(ValueError, match="scheduler disabilitato"):
        s.start_run("ABC", "scheduled")
    row = s.start_run("ABC", "manual")
    done = s.finish_run(row["id"], status="ok", result={"stato": "ok"})
    assert done["result"] == {"stato": "ok"}
    with sqlite3.connect(s.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE filing_runs SET result_json='{}' WHERE id=?", (row["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM filing_profiles WHERE ticker='ABC'")


def test_scheduled_claim_rechecks_due_after_stale_due_list(tmp_path):
    s = store(tmp_path)
    s.set_profile("ABC", profile(), interval_hours=24)
    stale = s.next_due()
    assert stale[0]["ticker"] == "ABC"
    first = s.start_run("ABC", "scheduled", language="en")
    s.finish_run(first["id"], status="errore", reason="provider offline")
    with pytest.raises(RunNotDue, match="non ancora dovuto"):
        s.start_run(stale[0]["ticker"], "scheduled", language="en")


@pytest.mark.parametrize("url", ["http://localhost/filing", "http://127.0.0.1/report.pdf",
                                     "http://10.0.0.2/report", "http://metadata.local/filing"])
def test_profile_rejects_local_document_hosts(tmp_path, url):
    with pytest.raises(ValueError, match="URL IR non valido"):
        store(tmp_path).set_profile("ABC", {**profile(), "ir_urls": [url]})


@pytest.mark.parametrize("hours", [0, -1, True, 8761])
def test_invalid_interval_rejected(tmp_path, hours):
    with pytest.raises(ValueError):
        store(tmp_path).set_profile("ABC", profile(), interval_hours=hours)


# --- prova reale fase D: run in errore o orfani riprovano dopo 2 h, non dopo l'intervallo ---

def _retrodata(s, ore):
    from datetime import datetime, timedelta, timezone
    from bellomberg.storage.filing_store import SCHEMA
    guardia = next(x for x in SCHEMA if "filing_runs_final_immutable" in x)
    quando = (datetime.now(timezone.utc) - timedelta(hours=ore)).isoformat()
    with sqlite3.connect(s.db_path) as conn:
        conn.execute("DROP TRIGGER filing_runs_final_immutable")
        conn.execute("UPDATE filing_runs SET started_at=?", (quando,))
        conn.execute(guardia)
    return quando


def test_run_in_errore_riprova_dopo_due_ore(tmp_path):
    from datetime import datetime, timedelta
    s = store(tmp_path)
    s.set_profile("ABC", profile(), interval_hours=24)
    row = s.start_run("ABC", "scheduled")
    s.finish_run(row["id"], status="errore", reason="network")
    inizio = datetime.fromisoformat(s.get_run(row["id"])["started_at"])
    assert s.next_due(now=inizio + timedelta(hours=1, minutes=59)) == []
    assert [d["ticker"] for d in s.next_due(now=inizio + timedelta(hours=2))] == ["ABC"]
    assert s.next_due_at("ABC") == (inizio + timedelta(hours=2)).isoformat()
    _retrodata(s, 3)
    assert s.start_run("ABC", "scheduled")["status"] == "queued"


def test_orfano_recuperato_e_subito_dovuto(tmp_path):
    s = store(tmp_path)
    s.set_profile("ABC", profile(), interval_hours=24)
    row = s.start_run("ABC", "scheduled")
    _retrodata(s, 3)  # queued da 3 h: processo morto
    s.recover_run(row["id"], "run orfano oltre 2 h (processo terminato)")
    assert [d["ticker"] for d in s.next_due()] == ["ABC"]


@pytest.mark.parametrize("stato", ["ok", "parziale", "skipped", "non_disponibile"])
def test_run_concluso_senza_errore_mantiene_l_intervallo(tmp_path, stato):
    from datetime import datetime, timedelta
    s = store(tmp_path)
    s.set_profile("ABC", profile(), interval_hours=24)
    row = s.start_run("ABC", "scheduled")
    s.finish_run(row["id"], status=stato, reason="x")
    inizio = datetime.fromisoformat(s.get_run(row["id"])["started_at"])
    assert s.next_due(now=inizio + timedelta(hours=3)) == []
    assert [d["ticker"] for d in s.next_due(now=inizio + timedelta(hours=24))] == ["ABC"]
    _retrodata(s, 3)
    with pytest.raises(RunNotDue):
        s.start_run("ABC", "scheduled")


def test_intervallo_breve_resta_piu_breve_del_ritentativo(tmp_path):
    from datetime import datetime, timedelta
    s = store(tmp_path)
    s.set_profile("ABC", profile(), interval_hours=1)
    row = s.start_run("ABC", "scheduled")
    s.finish_run(row["id"], status="errore", reason="network")
    inizio = datetime.fromisoformat(s.get_run(row["id"])["started_at"])
    assert [d["ticker"] for d in s.next_due(now=inizio + timedelta(hours=1))] == ["ABC"]


def test_errori_consecutivi_attesa_crescente_fino_all_intervallo(tmp_path):
    from datetime import datetime, timedelta
    s = store(tmp_path)
    s.set_profile("ABC", profile(), interval_hours=24)
    attese = []
    for _ in range(5):
        _retrodata(s, 48)  # ogni tentativo e' dovuto
        row = s.start_run("ABC", "scheduled")
        s.finish_run(row["id"], status="errore", reason="network")
        inizio = datetime.fromisoformat(s.get_run(row["id"])["started_at"])
        attese.append((datetime.fromisoformat(s.next_due_at("ABC")) - inizio) / timedelta(hours=1))
    assert attese == [2, 4, 8, 16, 24]
    _retrodata(s, 48)
    ok = s.start_run("ABC", "scheduled")
    s.finish_run(ok["id"], status="ok")
    inizio = datetime.fromisoformat(s.get_run(ok["id"])["started_at"])
    assert (datetime.fromisoformat(s.next_due_at("ABC")) - inizio) / timedelta(hours=1) == 24
