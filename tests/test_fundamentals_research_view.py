"""Fund reads saved research and observations, without model or provider work."""
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _alias_store_present(tmp_path, monkeypatch):
    # The Fund refresher resolves Yahoo symbols like the price updater; an absent
    # private alias store is a declared failure, so tests provide an empty valid one.
    import bellomberg.storage.negozi_privati as stores
    path = tmp_path / "alias_fonti.json"
    path.write_text('{"yfinance": {}}', encoding="utf-8")
    monkeypatch.setattr(stores, "PERCORSO_ALIAS", str(path))



NOW = datetime(2030, 3, 4, 12, tzinfo=timezone.utc)


def service():
    name = "bellomberg.market_data.fundamentals_view"
    assert importlib.util.find_spec(name) is not None, "saved research reader is missing"
    return importlib.import_module(name)


@pytest.fixture
def stored(tmp_path):
    db = tmp_path / "research.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
          CREATE TABLE positions(ticker TEXT, nome TEXT, quantita REAL, is_active INT);
          CREATE TABLE favorite_companies(ticker TEXT, name TEXT);
          CREATE TABLE position_prices(id INTEGER PRIMARY KEY, ticker TEXT, prezzo REAL,
            valuta TEXT, source TEXT, timestamp TEXT);
          CREATE TABLE trade_idea_runs(id TEXT, ticker TEXT, company_name TEXT, currency TEXT,
            request_json TEXT, result_json TEXT, context_json TEXT, progress_json TEXT,
            technical_status TEXT, created_at TEXT, updated_at TEXT, finished_at TEXT, memo_id INT);
          CREATE TABLE weekly_runs(memo_id INT, run_id TEXT, context_json TEXT,
            state_json TEXT, snapshot_json TEXT, updated_at TEXT);
          CREATE TABLE weekly_checkpoints(memo_id INT, stage TEXT, payload_json TEXT,
            payload_sha256 TEXT, created_at TEXT);
        """)
        conn.execute("INSERT INTO positions VALUES ('SYNTH.X','Synthetic Company',4,1)")
        conn.execute("INSERT INTO favorite_companies VALUES ('EMPTY.X','No observations')")
        conn.execute("INSERT INTO position_prices VALUES (1,'SYNTH.X',40,'USD','fixture-feed','2030-03-04T11:55:00Z')")
    cache = tmp_path / "consensus"
    cache.mkdir()
    return db, cache


def consensus(cache, **changes):
    value = {"ticker": "SYNTH.X", "identity_symbol": "SYNTH.X",
             "observation_kind": "market_consensus", "observation_contract": "market-consensus/1",
             "source": "Synthetic analyst provider", "currency": "USD",
             "currency_source": "provider profile.currency", "acquired_at": "2030-03-04T11:58:00Z",
             "data_as_of": None,
             "price_targets": {"mean": 50, "median": 49, "low": 42, "high": 61,
                               "number_of_analysts": 7}}
    value.update(changes)
    (cache / (sha256(b"SYNTH.X").hexdigest() + ".json")).write_text(json.dumps(value), encoding="utf-8")
    return value


def saved_trade(db, *, run_id, ticker="SYNTH.X", mode="fundamentals_research_v1",
                summary="New research estimate: 70 USD, scenario assumption from the saved run",
                finished="2030-03-03T11:00:00Z", **result_fields):
    result = {"ticker": ticker, "summary": summary, **result_fields}
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO trade_idea_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, ticker, "Synthetic Company", "USD", json.dumps({"analysis_mode": mode}),
             json.dumps(result), "{}", "{}", "completed", finished, finished, finished, 11))
    return result


def item(view, ticker="SYNTH.X"):
    return next(row for row in view["items"] if row["ticker"] == ticker)


def test_followed_titles_exist_without_any_models_and_reads_do_not_write(stored, monkeypatch):
    db, cache = stored
    consensus(cache)
    before = db.read_bytes()
    connections = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        assert "mode=ro" in str(args[0]) and kwargs.get("uri") is True
        conn = original(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    out = service().research_view(db, cache_dir=cache, now=NOW)
    row = item(out)
    assert out["status"] == "available" and out["count"] == 2
    assert row["quote"]["value"] == 40
    assert row["consensus"]["mean"] == 50
    assert row["consensus"]["data_as_of"] is None
    assert row["comparison"] == {"upside_pct": 25.0, "status": "available"}
    assert row["analysis"]["status"] == "missing"
    assert item(out, "EMPTY.X")["consensus"]["status"] == "missing"
    assert db.read_bytes() == before and len(connections) == 1


def test_active_short_is_followed_but_zero_or_inactive_positions_are_not(stored):
    db, cache = stored
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO positions VALUES (?,?,?,?)", [
            ("SHORT.X", "Active short", -2, 1), ("ZERO.X", "Closed", 0, 1),
            ("INACTIVE.X", "Inactive", 2, 0)])
    rows = service().research_view(db, cache_dir=cache, now=NOW)["items"]
    assert {row["ticker"] for row in rows} == {"SYNTH.X", "EMPTY.X", "SHORT.X"}


@pytest.mark.parametrize("changes,expected", [
    ({"currency": "EUR"}, "currency_mismatch"),
    ({"currency": None}, "currency_missing"),
    ({"identity_symbol": "OTHER.X"}, "identity_mismatch"),
    ({"acquired_at": "2030-01-01T12:00:00Z"}, "stale"),
    ({"acquired_at": None}, "date_missing"),
    ({"price_targets": {"mean": float("nan")}}, "data_missing"),
])
def test_noncomparable_consensus_never_fabricates_upside(stored, changes, expected):
    db, cache = stored
    consensus(cache, **changes)
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["comparison"] == {"upside_pct": None, "status": expected}


def test_quote_age_and_minor_currency_are_not_silently_converted(stored):
    db, cache = stored
    consensus(cache, currency="GBP")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE position_prices SET valuta='GBp'")
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["quote"]["currency"] == "GBp"
    assert row["comparison"] == {"upside_pct": None, "status": "currency_mismatch"}
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE position_prices SET valuta='GBP', timestamp='2030-02-01T10:00:00Z'")
    assert item(service().research_view(db, cache_dir=cache, now=NOW))["comparison"]["status"] == "stale"


def test_saved_trade_idea_prose_and_sources_remain_authentic(stored):
    db, cache = stored
    result = {"ticker": "SYNTH.X", "judgment": "watch", "summary": "Original committee conclusion",
              "dossier": [{"key": "assumptions", "title": "Assumptions", "paragraphs": ["Original disputed assumption"], "tables": []}],
              "scenarios": [{"name": "Bear", "analysis": "Original stress scenario", "evidence_ids": ["e1"]}],
              "risks": ["Original risk"], "catalysts": ["Original catalyst"], "data_gaps": ["Consensus unavailable at analysis"],
              "evidence": [{"id": "e1", "source": "Issuer filing", "url": "https://issuer.example/filing", "as_of": "2030-03-01"}]}
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO trade_idea_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("ti-saved", "SYNTH.X", "Synthetic Company", "USD",
                      json.dumps({"analysis_mode": "fundamentals_research_v1"}), json.dumps(result), "{}", "{}",
                      "completed", "2030-03-03T10:00:00Z", "2030-03-03T11:00:00Z", "2030-03-03T11:00:00Z", 11))
    row = item(service().research_view(db, cache_dir=cache, now=NOW, detail=True))
    assert row["analysis"]["content"] == result
    assert row["analysis"]["origin"] == "trade_idea"
    assert row["analysis"]["scope"] == "single_company"
    assert row["analysis"]["run_id"] == "ti-saved"
    assert row["consensus"]["status"] == "missing"


def test_malformed_or_missing_database_is_declared_without_creation(tmp_path, stored):
    absent = tmp_path / "absent.sqlite"
    out = service().research_view(absent, cache_dir=tmp_path / "absent-cache", now=NOW)
    assert out["status"] == "unavailable" and out["count"] is None
    assert not absent.exists() and not (tmp_path / "absent-cache").exists()
    db, cache = stored
    path = cache / (sha256(b"SYNTH.X").hexdigest() + ".json")
    path.write_text("{broken", encoding="utf-8")
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["consensus"]["status"] == "unavailable"
    assert row["comparison"]["upside_pct"] is None


def test_unknown_dates_and_analyst_count_are_not_inferred(stored):
    db, cache = stored
    consensus(cache, price_targets={"mean": 50, "high": 60, "low": 42})
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["consensus"]["number_of_analysts"] is None
    assert row["consensus"]["median"] is None
    assert row["consensus"]["data_as_of"] is None


def test_outdated_snapshot_cannot_be_refreshed_by_a_read(stored):
    db, cache = stored
    consensus(cache, acquired_at="2029-01-01T00:00:00Z")
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    out = service().research_view(db, cache_dir=cache, now=NOW)
    assert item(out)["consensus"]["status"] == "stale"
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before


@pytest.mark.parametrize("changes", [
    {"observation_kind": None, "observation_contract": None},
    {"observation_kind": "workbook_estimate"},
    {"observation_contract": "market-consensus/0"},
])
def test_legacy_or_other_observations_never_become_market_consensus(stored, changes):
    db, cache = stored
    consensus(cache, **changes, quote={"identity_symbol": "SYNTH.X", "value": 999,
        "currency": "USD", "source": "historical workbook", "observed_at": "2030-03-04T11:59:00Z"})
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["consensus"]["mean"] is None
    assert row["consensus"]["reason"] == "market_observation_required"
    assert row["quote"]["value"] == 40
    assert row["comparison"]["upside_pct"] is None
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before


def test_daily_targets_remain_valid_for_48_hours_and_quote_fetch_does_not_reset_them(stored):
    db, cache = stored
    consensus(cache, acquired_at="2030-03-03T11:58:00Z",
        quote={"identity_symbol": "SYNTH.X", "value": 40, "currency": "USD", "source": "fixture quote",
               "observed_at": "2030-03-04T11:59:00Z", "acquired_at": "2030-03-04T11:59:00Z"})
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["consensus"]["status"] == "available" and row["comparison"]["upside_pct"] == 25
    assert row["consensus"]["acquired_at"] == "2030-03-03T11:58:00Z"
    assert row["quote"]["acquired_at"] == "2030-03-04T11:59:00Z"
    consensus(cache, acquired_at="2030-03-02T11:59:00Z",
        quote={"identity_symbol": "SYNTH.X", "value": 40, "currency": "USD", "source": "fixture quote",
               "observed_at": "2030-03-04T11:59:00Z", "acquired_at": "2030-03-04T11:59:00Z"})
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["consensus"]["status"] == "stale"
    assert row["quote"]["status"] == "available" and row["comparison"]["upside_pct"] is None


def test_refresh_failure_preserves_observation_and_declares_attempt_separately(stored):
    db, cache = stored
    failure = {"status": "failed", "last_attempt_at": "2030-03-04T11:59:00Z",
               "last_success_at": "2030-03-04T11:58:00Z", "error": "Synthetic provider timeout"}
    consensus(cache, consensus_refresh=failure, quote_refresh=failure)
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["consensus"]["mean"] == 50
    assert row["consensus"]["refresh"] == failure
    assert row["quote"]["refresh"] == failure
    assert row["consensus"]["data_as_of"] is None


def test_legacy_run_cannot_hide_new_research_or_seed_the_current_universe(stored):
    db, cache = stored
    fresh = saved_trade(db, run_id="new-research", valuation={"fair_value_base": 999},
                        valuation_refs=[{"workbook": "legacy.xlsx"}])
    saved_trade(db, run_id="old-model", mode=None, summary="Old workbook fair value 999",
                finished="2030-03-04T11:59:00Z")
    saved_trade(db, run_id="legacy-only", ticker="LEGACY.X", mode=None,
                finished="2030-03-04T11:59:00Z")
    out = service().research_view(db, cache_dir=cache, now=NOW, detail=True)
    row = item(out)
    assert row["analysis"]["run_id"] == "new-research"
    assert row["analysis"]["summary"] == fresh["summary"]
    assert row["analysis"]["as_of"] == "2030-03-03T11:00:00Z"
    assert "valuation" not in row["analysis"]["content"]
    assert "valuation_refs" not in row["analysis"]["content"]
    assert "LEGACY.X" not in {entry["ticker"] for entry in out["items"]}
    assert row["consensus"]["mean"] is None and row["comparison"]["upside_pct"] is None


@pytest.mark.parametrize("status", ["unavailable", "not_applicable"])
def test_provider_unavailable_or_inapplicable_does_not_reuse_a_target(stored, status):
    db, cache = stored
    consensus(cache, consensus_status=status, consensus_reason="Synthetic coverage limitation")
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["consensus"]["status"] == status
    assert row["comparison"]["upside_pct"] is None


def test_followed_nonportfolio_title_can_use_an_exact_saved_provider_quote(stored):
    db, cache = stored
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM position_prices")
    consensus(cache, quote={"value": 40, "currency": "USD", "identity_symbol": "SYNTH.X",
        "source": "yfinance info.regularMarketPrice", "observed_at": "2030-03-04T11:50:00Z"})
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["quote"]["value"] == 40 and row["quote"]["source"] == "yfinance info.regularMarketPrice"
    assert row["comparison"]["upside_pct"] == 25


def test_provider_quote_with_different_symbol_is_not_used(stored):
    db, cache = stored
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM position_prices")
    consensus(cache, quote={"value": 400, "currency": "USD", "identity_symbol": "OTHER.X",
        "source": "yfinance info.regularMarketPrice", "observed_at": "2030-03-04T11:50:00Z"})
    row = item(service().research_view(db, cache_dir=cache, now=NOW))
    assert row["quote"]["value"] is None and row["comparison"]["upside_pct"] is None


def test_ordinary_consensus_acquisition_persists_provider_metadata(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from bellomberg.core import paths
    from bellomberg.market_data import consensus_estimates

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(consensus_estimates, "_CACHE", {})
    instrument = SimpleNamespace(analyst_price_targets={"current": 40, "mean": 50},
        info={"symbol": "SYNTH.X", "currency": "USD", "numberOfAnalystOpinions": 7},
        earnings_estimate=None, revenue_estimate=None, eps_trend=None, recommendations_summary=None)
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda ticker: instrument))
    value = consensus_estimates.get_consensus("SYNTH.X")
    assert value.get("currency") == "USD"
    assert value.get("identity_symbol") == "SYNTH.X"
    assert value.get("acquired_at")
    assert value.get("data_as_of") is None
    assert value["price_targets"].get("number_of_analysts") == 7
    saved = tmp_path / "consensus_cache" / (sha256(b"SYNTH.X").hexdigest() + ".json")
    assert json.loads(saved.read_text(encoding="utf-8")) == value


def test_weekly_report_remains_multicompany_and_tampered_snapshot_is_declared(stored):
    db, cache = stored
    from bellomberg.core.research_analysis import research_digest
    dossiers = {"SYNTH.X": {"ticker": "SYNTH.X", "documents": [{"id": "official-1", "url": "https://issuer.example/annual", "published_at": "2030-02-01"}], "issues": []},
                "EMPTY.X": {"ticker": "EMPTY.X", "documents": [], "issues": [{"reason": "Source unavailable"}]}}
    reports = {"fundamentals": "SYNTH.X: original observation. EMPTY.X: original uncertainty."}
    seal = {"analysis_mode": "fundamentals_research_v1", "version": 1,
            "dossiers": dossiers, "reports": reports, "tool_receipts": []}
    seal["dossier_sha256"] = research_digest({"dossiers": dossiers, "tool_receipts": []})
    seal["thesis_sha256"] = research_digest({"reports": reports, "dossier_sha256": seal["dossier_sha256"]})
    payload = {"data": {"_research_thesis": seal, "fundamentals": {"1": reports["fundamentals"], "2": "Final multi-company report, preserved whole."}}}
    snapshot = {"payload": payload, "sha256": research_digest(payload)}
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO weekly_runs VALUES (?,?,?,?,?,?)", (21, "weekly-saved",
            json.dumps({"contract": {"analysis_mode": "fundamentals_research_v1"}}),
            json.dumps({"status": "completed"}), json.dumps(snapshot), "2030-03-04T11:00:00Z"))
    out = service().research_view(db, cache_dir=cache, now=NOW, detail=True)
    for row in out["items"]:
        assert row["analysis"]["scope"] == "whole_committee_report"
        assert row["analysis"]["content"]["report"] == "Final multi-company report, preserved whole."
        assert row["analysis"]["reference"]["thesis_sha256"] == seal["thesis_sha256"]
    payload["data"]["fundamentals"]["2"] = "Changed after snapshot seal"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE weekly_runs SET snapshot_json=?", (json.dumps(snapshot),))
    assert item(service().research_view(db, cache_dir=cache, now=NOW))["analysis"]["status"] == "unavailable"


def test_archive_download_returns_only_unchanged_frozen_workbook(stored, tmp_path):
    import shutil
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.testclient import TestClient
    module = "bellomberg.api.fundamentals_routes"
    assert importlib.util.find_spec(module), "read-only Fund routes are missing"
    factory = importlib.import_module(module).create_fundamentals_router
    db, cache = stored
    folder = tmp_path / "archive"
    folder.mkdir()
    frozen = Path(__file__).parent / "fixtures" / "fundamentals" / "VAL_SYNTH_ARCHIVED.xlsx"
    if not frozen.exists():
        pytest.skip("Frozen private archive fixture is not distributed; no workbook is generated")
    original = frozen.read_bytes()
    copied = folder / frozen.name
    shutil.copyfile(frozen, copied)
    copied.with_suffix(".payload.json").write_text(json.dumps({"ticker": "SYNTH.X", "valuation_date": "2025-12-31",
        "workbook_sha256": sha256(original).hexdigest(), "generation_id": "older-generation"}), encoding="utf-8")

    def auth(request: Request):
        if request.headers.get("X-BB-Token") != "fixture-session":
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(factory(auth, db_provider=lambda: db, cache_dir=cache, roots=[folder]))
    with TestClient(app) as client:
        assert client.get("/fundamentals/archive").status_code == 401
        headers = {"X-BB-Token": "fixture-session"}
        listed = client.get("/fundamentals/archive", headers=headers).json()
        assert len(listed["items"]) == 1
        record = listed["items"][0]
        assert record["historical"] is True and record["as_of"] == "2025-12-31"
        response = client.get(record["download_path"], headers=headers)
        assert response.status_code == 200 and response.content == original
        assert response.headers["X-Valuation-Copy"] == "historical"
        copied.write_bytes(original + b"changed")
        assert client.get(record["download_path"], headers=headers).status_code == 409
        assert client.get("/fundamentals/archive/invalid/download", headers=headers).status_code == 404
    assert frozen.read_bytes() == original
