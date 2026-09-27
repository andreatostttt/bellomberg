"""Public-model lifecycle on an empty profile, with raw fixtures and no network.

The actual committee coverage hook, dispatcher, preparation compiler, workbook,
SQLite publications, HTTP download and MIME builder run here. Only external
acquisition, provider replies, clock and SMTP transport are simulated.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import inspect
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from bellomberg.agents import chat_tools, consigliere_multi
from bellomberg.agents.specialists.base import Blackboard
from bellomberg.storage import memory_db, valuation_versions
from bellomberg.valuation import preparation_runtime, sector_analysis
from test_input_preparation import _documents, _operating_plan, DAY
from test_preparation_runtime import _policy
from test_sector_analysis import providers_for
from test_sector_valuation_api import endpoint
from test_valuation_automation_routes import _client, _get


@pytest.fixture
def public_profile(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Public lifecycle attempted a real network connection")
    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        # Windows asyncio builds its wakeup socketpair over loopback. Permit
        # that exact stdlib call, while forbidding every application connection.
        caller = inspect.currentframe().f_back
        if caller.f_code is getattr(socket, "_fallback_socketpair", lambda: None).__code__:
            return original_connect(sock, address)
        forbidden()
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(tmp_path / "public.db"))
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setattr(chat_tools, "REPORT_DIR", root)
    from bellomberg.core import paths
    from bellomberg.valuation import dcf_engine
    monkeypatch.setattr(paths, "MODELS_DIR", root)
    monkeypatch.setattr(dcf_engine, "REPORT_DIR", root)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(DAY).replace(tzinfo=tz)
    monkeypatch.setattr(valuation_versions, "datetime", FrozenDateTime)
    db = memory_db.MemoryDB()
    from bellomberg.storage.valuation_jobs import ensure_schema as jobs_schema
    with db._conn() as conn:
        valuation_versions.ensure_schema(conn)
        jobs_schema(conn)
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 0
    versions = valuation_versions.ValuationVersions(db, roots=[root])
    paid, acquisitions = [], []
    source_state = {"documents": _documents()}
    original_acquire = sector_analysis.prepare_sector_analysis
    def acquire(ticker, **kwargs):
        if kwargs.get("user_context", {}).get("method_records"):
            return original_acquire(ticker, **kwargs)
        bundle = original_acquire(ticker, as_of=DAY, providers=providers_for("software"),
                                  user_context=kwargs.get("user_context"))
        assert bundle["case"]["records"] == []  # No prepared personal inputs.
        return bundle
    monkeypatch.setattr(sector_analysis, "prepare_sector_analysis", acquire)
    def collect(ticker, **kwargs):
        acquisitions.append((ticker, kwargs))
        documents = deepcopy(source_state["documents"])
        return {"status": "ready" if documents else "incomplete", "documents": documents,
                "issues": [] if documents else [{"reason": "Synthetic original source unavailable"}]}
    monkeypatch.setattr("bellomberg.valuation.preparation_sources.collect_preparation_evidence", collect)
    plan = _operating_plan()
    def call(**kwargs):
        request = json.loads(kwargs["messages"][0]["content"])
        paid.append(request)
        stage = request["contract"].get('preparation_refresh') or request["contract"]["preparation_stage"]
        values = plan["model"] if stage["scope"] == "model" else plan["scenarios"][stage["scope"]]
        if 'preparation_refresh' in request['contract']:
            from test_preparation_refresh import _compact_answer
            answer = {'reviews': {name: {'action': 'replace', 'driver': deepcopy(values[name])}
                                  for name in stage['driver_hashes']},
                      'rationale': 'Synthetic current source reviewed and supported economics reaffirmed'}
            answer = _compact_answer(answer, stage['compact_wire'])
        else:
            answer = {"drivers": {name: deepcopy(values[name]) for name in stage["drivers"]},
                      "rationale": "Synthetic sourced business case"}
        return SimpleNamespace(id="synthetic-" + str(len(paid)), model=kwargs["model"],
            provider="synthetic", stop_reason="end_turn", usage=SimpleNamespace(cost_usd=.01),
            content=[SimpleNamespace(type="text", text=json.dumps(answer))])
    def factory(journal, *, authorized_usd):
        from bellomberg.valuation.preparation_ai import BudgetedProposer
        return BudgetedProposer(journal, authorized_usd=authorized_usd, model="synthetic/model",
            max_tokens=16000, thinking={"type": "adaptive"}, call=call,
            metadata=lambda model: {"id": model, "context_length": 100000,
                "pricing": {"prompt": "0.000001", "completion": "0.000001"}})
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(_policy(tickers=["SYNTH-EXT"])), encoding="utf-8")
    runtime = preparation_runtime.PreparationRuntime(policy, data_root=tmp_path / "data",
        archive_root=tmp_path / "sources", output_dir=root, proposer_factory=factory)
    monkeypatch.setattr(preparation_runtime, "installation_runtime", lambda: runtime)
    def run():
        board = Blackboard(memory_db=db)
        binding = preparation_runtime.bind_installation_preparer("committee")
        board.valuation_preparer = binding["preparer"]
        board.data["_valuation_preparation"] = binding["state"]
        board.current_round = 1
        consigliere_multi._ensure_portfolio_valuations(board,
            {"n_positions": 1, "positions": [{"ticker": "SYNTH-EXT"}]})
        return board, board.valuation_results["SYNTH-EXT"]
    return SimpleNamespace(db=db, versions=versions, root=root, runtime=runtime,
        paid=paid, acquisitions=acquisitions, source_state=source_state, run=run)


def test_empty_public_profile_reaches_app_download_and_exact_mime(public_profile, monkeypatch, record_property):
    profile = public_profile
    board, result = profile.run()
    assert result["valuation_usability"]["usable"], result
    assert result["model_publication"]["status"] == "published", result
    assert result["preparation"]["provenance"]["origin"] == "automatic_non_approved"
    assert result["input_consumption"]["status"] == "complete"
    assert len(profile.paid) > 1 and len(profile.acquisitions) == 1
    assert profile.runtime.budget_audit()["request_count"] == len(profile.paid)
    path = Path(result["path"])
    raw = path.read_bytes()
    assert raw[:2] == b"PK" and sha256(raw).hexdigest() == result["workbook_sha256"]
    model = next(row for row in endpoint(profile.root, model_db=profile.db)["models"]
                 if row["current_generation"])
    assert model["ticker"] == "SYNTH-EXT" and model["generation_id"] == result["generation_id"]
    client = _client(profile.db.db_path, [profile.root])
    assert client.get(model["current_download"]).status_code == 401
    response = _get(client, model["current_download"])
    assert response.status_code == 200 and response.content == raw
    assert response.headers["x-valuation-generation"] == result["generation_id"]
    from bellomberg.reporting import email_sender
    from bellomberg.reporting.valuation_delivery import build_manifest, record_email_outcome
    receipt = build_manifest(board.valuation_results, roots=[profile.root], attempts=board.valuation_attempts)
    messages = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, *args): pass
        def send_message(self, message): messages.append(message)
    monkeypatch.setattr(email_sender, "email_configurata", lambda: True)
    monkeypatch.setattr(email_sender, "EMAIL_FROM", "fixture@example.org")
    monkeypatch.setattr(email_sender, "EMAIL_TO", "fixture@example.org")
    monkeypatch.setattr(email_sender, "EMAIL_PASSWORD", "synthetic-unused", raising=False)
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", SMTP)
    sent = email_sender.invia_email_multi_allegati(receipt["attachments"],
        expected_hashes=receipt["expected_hashes"], delivery_receipt=receipt)
    assert sent and len(messages) == 1
    parts = [part for part in messages[0].walk() if part.get_filename()]
    assert len(parts) == 1 and parts[0].get_payload(decode=True) == raw
    record_email_outcome(receipt, sent)
    assert receipt["valuations"][0]["email_included"]
    count = len(profile.paid)
    with profile.db._conn() as conn:
        generations_before = conn.execute("SELECT count(*) FROM valuation_publications").fetchone()[0]
    _, repeated = profile.run()
    assert repeated["valuation_usability"]["usable"], repeated
    assert len(profile.paid) == count
    assert Path(result["path"]).read_bytes() == raw
    with profile.db._conn() as conn:
        generations_after = conn.execute("SELECT count(*) FROM valuation_publications").fetchone()[0]
    record_property("simulated_provider_initial_requests", count)
    record_property("simulated_provider_repeat_requests", len(profile.paid) - count)
    record_property("publications_initial", generations_before)
    record_property("publications_after_repeat", generations_after)
    record_property("same_workbook_generation", repeated["generation_id"] == result["generation_id"])
    assert repeated.get("reused") and repeated["generation_id"] == result["generation_id"]
    assert generations_after == generations_before


def test_legacy_model_is_rebuilt_without_deleting_history(public_profile):
    profile = public_profile
    legacy_path = profile.root / "VAL_SYNTH_EXT.xlsx"
    legacy_bytes = b"legacy incompatible synthetic workbook"
    legacy_path.write_bytes(legacy_bytes)
    legacy_id = profile.db.save_valuation_thesis("SYNTH-EXT", fair_value=999., engine="legacy")
    with profile.db._conn() as conn:
        original = tuple(conn.execute("SELECT * FROM valuation_theses WHERE id=?", (legacy_id,)).fetchone())
    _, result = profile.run()
    assert result["valuation_usability"]["usable"], result
    assert not result.get("reused") and result["model_publication"]["status"] == "published"
    assert result["fair_value_base"] != 999. and Path(result["path"]) != legacy_path
    assert legacy_path.read_bytes() == legacy_bytes
    with profile.db._conn() as conn:
        assert tuple(conn.execute("SELECT * FROM valuation_theses WHERE id=?", (legacy_id,)).fetchone()) == original
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 2


def test_missing_original_source_stays_explicit_without_provider_or_workbook(public_profile):
    profile = public_profile
    profile.source_state["documents"] = []
    board, result = profile.run()
    assert not result["valuation_usability"]["usable"]
    assert result["preparation"]["issues"] and not profile.paid
    assert not list(profile.root.glob("*.xlsx"))
    from bellomberg.reporting.valuation_delivery import build_manifest
    receipt = build_manifest(board.valuation_results, roots=[profile.root], attempts=board.valuation_attempts)
    assert receipt["attachments"] == []
    assert profile.versions.current("SYNTH-EXT")["latest_attempt"]["status"] == "incomplete"


@pytest.mark.parametrize("damage", ["workbook_changed", "sidecar_missing"])
def test_damaged_current_rebuilds_from_sources_and_preserves_old_artifact(public_profile, damage):
    profile = public_profile
    _, original = profile.run()
    assert original["valuation_usability"]["usable"], original
    path = Path(original["path"])
    if damage == "workbook_changed":
        path.write_bytes(path.read_bytes() + b"synthetic user edit")
    else:
        path.with_suffix(".payload.json").unlink()
    damaged_bytes = path.read_bytes()
    assert not profile.versions.current("SYNTH-EXT")["artifact"]["available"]
    count = len(profile.paid)
    _, rebuilt = profile.run()
    assert rebuilt.get("valuation_usability", {}).get("usable"), rebuilt
    assert rebuilt["model_publication"]["status"] == "published"
    assert rebuilt["generation_id"] != original["generation_id"]
    assert Path(rebuilt["path"]) != path and path.read_bytes() == damaged_bytes
    assert len(profile.paid) == count  # Exact raw-source requests remain paid-cache hits.
    selection = rebuilt["preparation"]["provenance"]["preparation_selection"]
    assert selection["mode"] == "fresh" and selection["source_generation_id"] == original["generation_id"]
    assert selection["reason"] == "prior_artifact_unavailable"
    with profile.db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_publications").fetchone()[0] == 2


def test_valid_locked_current_survives_subsequent_committee_generation(public_profile):
    profile = public_profile
    _, original = profile.run()
    assert original["valuation_usability"]["usable"], original
    profile.versions.set_locked("SYNTH-EXT", True)
    raw = Path(original["path"]).read_bytes()
    count = len(profile.paid)
    _, candidate = profile.run()
    assert candidate["valuation_usability"]["usable"], candidate
    assert len(profile.paid) == count
    current = profile.versions.current("SYNTH-EXT")
    assert current["locked"] and current["current_generation"] == original["generation_id"]
    assert Path(original["path"]).read_bytes() == raw
    assert candidate.get("reused") or candidate["model_publication"]["status"] == "held"


def test_damaged_current_with_missing_new_sources_cannot_borrow_old_research(public_profile):
    profile = public_profile
    _, original = profile.run()
    assert original["valuation_usability"]["usable"], original
    path = Path(original["path"])
    raw = path.read_bytes() + b"synthetic user edit"
    path.write_bytes(raw)
    profile.source_state["documents"] = []
    count = len(profile.paid)
    _, failed = profile.run()
    assert not failed["valuation_usability"]["usable"], failed
    assert failed["preparation"]["issues"] and failed.get("path") is None
    assert len(profile.paid) == count and path.read_bytes() == raw
    current = profile.versions.current("SYNTH-EXT")
    assert current["current_generation"] == original["generation_id"]
    assert current["latest_attempt"]["status"] == "incomplete"
    assert not current["artifact"]["available"]


def test_recompiled_cache_revalidates_sidecar_consumption(public_profile):
    profile = public_profile
    _, original = profile.run()
    path = Path(original['path'])
    raw = path.read_bytes()
    sidecar = path.with_suffix('.payload.json')
    payload = json.loads(sidecar.read_text(encoding='utf-8'))
    payload['input_consumption']['status'] = 'incomplete'
    sidecar.write_text(json.dumps(payload), encoding='utf-8')
    count = len(profile.paid)
    _, rebuilt = profile.run()
    assert rebuilt['valuation_usability']['usable'], rebuilt
    assert not rebuilt.get('reused') and rebuilt['generation_id'] != original['generation_id']
    assert len(profile.paid) == count and path.read_bytes() == raw
    assert 'Cache ricompilata non riutilizzabile' in rebuilt['cache_note']


def test_recompiled_cache_cannot_adopt_a_concurrent_head(public_profile, monkeypatch):
    from bellomberg.valuation import preparation_sources, dcf_engine
    profile = public_profile
    _, original = profile.run()
    collect = preparation_sources.collect_preparation_evidence
    winners = []
    def interleaved(*args, **kwargs):
        winner = dcf_engine.generate_valuation('SYNTH-EXT', prepared_bundle=original['acquisition_snapshot'],
                                              output_dir=profile.root)
        assert profile.db.save_valuation_thesis('SYNTH-EXT', valuation_payload=winner) is not None
        published = profile.versions.publish(winner['snapshot_id'], winner['generation_id'],
                                            expected_current_generation=original['generation_id'])
        assert published['status'] == 'published'
        winners.append(winner)
        return collect(*args, **kwargs)
    monkeypatch.setattr(preparation_sources, 'collect_preparation_evidence', interleaved)
    _, candidate = profile.run()
    assert candidate['valuation_usability']['usable'], candidate
    assert not candidate.get('reused') and candidate['model_publication']['status'] == 'superseded'
    assert profile.versions.current('SYNTH-EXT')['current_generation'] == winners[0]['generation_id']


def test_new_document_with_same_raw_profile_requires_review(public_profile):
    profile = public_profile
    _, original = profile.run()
    old_bytes = Path(original['path']).read_bytes()
    document = profile.source_state['documents'][0]
    document['text'] += '\nAdditional current disclosure: the issuer reaffirmed its reported balances.'
    document['sha256'] = sha256(document['text'].encode()).hexdigest()
    count = len(profile.paid)
    _, updated = profile.run()
    assert updated['valuation_usability']['usable'], updated
    assert not updated.get('reused') and updated['generation_id'] != original['generation_id']
    requests = profile.paid[count:]
    assert [item['contract']['preparation_refresh']['scope'] for item in requests] == ['model', 'bear', 'base', 'bull']
    assert updated['preparation']['provenance']['preparation_selection']['mode'] == 'review'
    assert updated['preparation']['review_basis']['dossier']['documents'][0]['sha256'] == document['sha256']
    assert Path(original['path']).read_bytes() == old_bytes
