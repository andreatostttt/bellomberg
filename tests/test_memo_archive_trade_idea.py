"""Archivio memo (voce 8, decisione PM 04/10): i memo Trade Idea entrano nell'archivio
di default con etichetta, ma il memo precedente del Capo resta solo del Consigliere.
DB temporaneo con lo schema vero; titoli e run id inventati."""
import pytest
from fastapi.testclient import TestClient

from test_persistence import db  # noqa: F401  (fixture: MemoryDB in tmp_path, senza chroma)

RUN_OK = "0f0f0f0f-1111-4222-8333-444455556666"   # UUID inventato
BODY = "Testo sintetico del memo di prova, abbastanza lungo da superare la soglia dei cento caratteri. " * 2


def _seed(db):
    """Due memo del Consigliere, poi un Trade Idea valido e uno con notes malformate."""
    w1 = db.save_memo(BODY, title="ZZWEEK uno")
    w2 = db.save_memo(BODY, title="ZZWEEK due")
    ti = db.save_memo(BODY, title="Trade Idea — ZZTEST — ZZGIUDIZIO")
    bad = db.save_memo(BODY, title="Trade Idea — QQSYN.MI — ZZGIUDIZIO")
    with db._conn() as conn:
        conn.execute("UPDATE memos SET notes=? WHERE id=?", ("trade_idea:" + RUN_OK, ti))
        conn.execute("UPDATE memos SET notes=? WHERE id=?", ("trade_idea:non-un-uuid", bad))
    return w1, w2, ti, bad


def test_archivio_include_trade_idea_di_default_con_etichetta_e_run_id(db):
    w1, w2, ti, bad = _seed(db)
    rows = db.get_archive_memos(limit=10)
    assert [r["id"] for r in rows] == [bad, ti, w2, w1]
    by = {r["id"]: r for r in rows}
    assert (by[ti]["kind"], by[ti]["label"]) == ("trade_idea", "Trade Idea")
    assert by[ti]["trade_idea_run_id"] == RUN_OK
    assert by[ti]["trade_idea_provenance_error"] is None
    assert (by[w1]["kind"], by[w1]["label"]) == ("consigliere", "Consigliere")
    assert by[w1]["trade_idea_run_id"] is None
    # notes TI malformate: la riga resta, il guasto e' DICHIARATO
    assert by[bad]["kind"] == "trade_idea"
    assert by[bad]["trade_idea_run_id"] is None
    assert "non valido" in by[bad]["trade_idea_provenance_error"]


def test_archivio_opt_out_e_offset(db):
    w1, w2, ti, bad = _seed(db)
    assert [r["id"] for r in db.get_archive_memos(limit=10, include_trade_ideas=False)] == [w2, w1]
    assert [r["id"] for r in db.get_archive_memos(limit=2, offset=1)] == [ti, w2]


def test_metodo_vecchio_e_memoria_del_capo_restano_senza_trade_idea(db):
    w1, w2, ti, bad = _seed(db)
    assert [r["id"] for r in db.get_recent_memos(6)] == [w2, w1]
    ctx = db.build_capo_memory_context(max_chars=99999)
    # memos[0] e' il segnaposto della run in corso: il precedente e' w1
    assert "ZZWEEK uno" in ctx
    assert "ZZTEST" not in ctx and "QQSYN.MI" not in ctx


@pytest.fixture
def client(db, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    monkeypatch.setattr(api, "get_db", lambda: db)
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        yield TestClient(api.app, base_url="http://127.0.0.1")
    finally:
        api.app.dependency_overrides.clear()


def test_route_memos_default_include_campi_additivi(db, client):
    w1, w2, ti, bad = _seed(db)
    memos = client.get("/memos?limit=10").json()["memos"]
    assert [m["id"] for m in memos] == [bad, ti, w2, w1]
    t = next(m for m in memos if m["id"] == ti)
    assert t["label"] == "Trade Idea" and t["kind"] == "trade_idea"
    assert t["trade_idea_run_id"] == RUN_OK
    # campi esistenti invariati
    for key in ("id", "title", "timestamp", "notes", "pdf_path", "has_content",
                "pdf_available", "appendix_available"):
        assert key in t
    assert "full_markdown" not in t
    assert t["pdf_available"] is False


def test_route_memos_opt_out_comportamento_vecchio(db, client):
    w1, w2, ti, bad = _seed(db)
    memos = client.get("/memos?limit=10&include_trade_ideas=false").json()["memos"]
    assert [m["id"] for m in memos] == [w2, w1]
    assert all(m["label"] == "Consigliere" for m in memos)


def test_pdf_del_memo_trade_idea_rimanda_alla_rotta_verificata(db, client):
    w1, w2, ti, bad = _seed(db)
    r = client.get(f"/memos/{ti}/pdf", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == f"/trade-ideas/runs/{RUN_OK}/artifacts/pdf"
    assert client.get(f"/memos/{bad}/pdf", follow_redirects=False).status_code == 409
