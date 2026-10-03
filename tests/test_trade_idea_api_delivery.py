"""Authenticated API -> durable package/retry -> actual intercepted MIME bytes.

The committee/provider replay is separate; these cases exercise its real storage
and delivery service with common-engine workbooks, never the personal database.
"""
from hashlib import sha256
import json
from pathlib import Path
import smtplib
import socket
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from test_trade_idea_delivery import smtp
from test_documented_dcf_contract import contract_tools, _call
from test_valuation_snapshot_persistence import db
from test_trade_idea_store import request as acceptance, all_checks
from trade_idea_fixtures import research_result, bind_workbook

_SOCKET_CONNECT = socket.socket.connect


@pytest.fixture
def delivery_api(db, tmp_path, monkeypatch, contract_tools, smtp):
    from bellomberg.api import trade_idea_routes as routes, bellomberg_api as api
    from bellomberg.agents import trade_idea
    from bellomberg.core import paths
    from bellomberg.storage.trade_idea_store import TradeIdeaStore
    from tools.migrations import migra_trade_idea
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    migra_trade_idea.migra(db.db_path, apply=True)
    store = TradeIdeaStore(db.db_path, mandate_loader=lambda: "a" * 64)
    with db._conn() as connection:
        connection.execute("INSERT INTO cash_state(singleton_id,balance_cents,updated_at,source,version) "
                           "VALUES(1,1000000,'2026-09-27T20:00:00','synthetic',1)")
    def self_pipe_only(sock, address):
        # Windows asyncio implements its private socketpair over ephemeral
        # loopback. Permit precisely that stdlib frame, never API/provider I/O.
        caller = sys._getframe(1)
        if (caller.f_code.co_name == "_fallback_socketpair"
                and Path(caller.f_code.co_filename).resolve() == Path(socket.__file__).resolve()
                and address[0] == "127.0.0.1"):
            return _SOCKET_CONNECT(sock, address)
        pytest.fail("Unexpected network outside asyncio private self-pipe")
    monkeypatch.setattr(socket.socket, "connect", self_pipe_only)
    monkeypatch.setattr(routes, "_store", lambda *a, **k: store)
    for owner in (routes, paths):
        monkeypatch.setattr(owner, "REPORT_DIR", tmp_path)
        monkeypatch.setattr(owner, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(api, "_SESSIONS", {"synthetic-session": time.time() + 3600})
    app = FastAPI()
    routes.install_trade_idea_routes(app, api.require_session, db_path=db.db_path)
    client = TestClient(app, headers={"X-BB-Token": "synthetic-session"})
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)

    def complete(judgment):
        body = acceptance("SYNTH-EXT")
        body.update(view_text="Switching costs make this business immune to a downturn.", language="en")
        run = store.create_run(body, idempotency_key="delivery-" + judgment)["run"]
        result = bind_workbook(research_result(judgment, run_id=run["id"]), workbook)
        result.pop("destination")
        if result["proposal"]:
            result["proposal"].update(eur_amount=100.0, sizing_source="measured synthetic sizing receipt")
        token = store.claim_run(run["id"])
        store.update_progress(run["id"], token, "result", {"data_cutoff": "2026-09-27T20:00:00Z"})
        store.finish_run(run["id"], token, result, "completed")
        store.route_result(run["id"], all_checks())
        return store.get_run(run["id"])

    yield client, store, workbook, complete, tmp_path, smtp, trade_idea
    client.close()


@pytest.mark.parametrize("judgment", ["favorable", "rejected", "incomplete"])
def test_service_package_api_download_and_mime_have_identical_bytes(delivery_api, judgment):
    client, store, workbook, complete, folder, smtp_state, service = delivery_api
    detail = complete(judgment)
    run_id = detail["run"]["id"]
    expected_destination = detail["run"]["destination"]
    assert expected_destination["kind"] == ("dcn" if judgment == "favorable" else "research"), expected_destination
    service.deliver_trade_idea(store, run_id, valuation_results=[workbook], output_dir=folder / run_id)
    response = client.get(f"/trade-ideas/runs/{run_id}")
    assert response.status_code == 200, response.text
    public = response.json()
    assert public["email"]["status"] == "accepted"
    assert public["result"]["judgment"] == judgment
    assert public["run"]["destination"] == expected_destination
    assert public["run"]["memo_id"] == expected_destination["memo_id"]
    assert isinstance(public["artifacts"], list) and len(public["artifacts"]) == 2
    manifest = store.get_run(run_id)["artifacts"]
    assert manifest["pdf_quality"]["analytical_pages"] >= 10
    message = smtp_state[0][0]
    assert str(message["To"]) in ("pm@example.invalid", "receiver@example.invalid")
    mime = {part.get_filename(): part.get_payload(decode=True) for part in message.iter_attachments()}
    for artifact in public["artifacts"]:
        response = client.get(artifact["download_url"])
        assert response.status_code == 200
        assert response.content == mime[artifact["name"]]
        assert sha256(response.content).hexdigest() == artifact["sha256"]
        assert client.get(artifact["download_url"], headers={"X-BB-Token": "expired"}).status_code == 401
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(manifest["attachments"][0]).pages)
    assert workbook["generation_id"] in pdf_text and workbook["valuation_date"] in pdf_text
    assert service.deliver_trade_idea(store, run_id, output_dir=folder / run_id)["status"] == "accepted"
    assert len(smtp_state[0]) == 1
    assert store.get_run(run_id)["run"]["destination"] == expected_destination


@pytest.mark.parametrize("failure", ["failed", "uncertain"])
def test_email_recovery_uses_saved_package_without_new_analysis_or_routing(delivery_api, monkeypatch, failure):
    client, store, workbook, complete, folder, smtp_state, service = delivery_api
    run_id = complete("rejected")["run"]["id"]
    messages, capture = smtp_state
    original = capture.send_message
    def fail_once(self, message):
        monkeypatch.setattr(capture, "send_message", original)
        if failure == "uncertain":
            original(self, message)
            raise smtplib.SMTPServerDisconnected("Synthetic disconnect after DATA")
        raise smtplib.SMTPDataError(550, b"Synthetic rejection before acceptance")
    monkeypatch.setattr(capture, "send_message", fail_once)
    service.deliver_trade_idea(store, run_id, valuation_results=[workbook], output_dir=folder / run_id)
    before = store.get_run(run_id)
    assert before["email"]["status"] == failure
    assert service.deliver_trade_idea(store, run_id, output_dir=folder / run_id)["status"] == failure
    assert len(messages) == int(failure == "uncertain")
    retry = f"/trade-ideas/runs/{run_id}/email/retry"
    if failure == "uncertain":
        assert client.post(retry, json={}).status_code == 409
        response = client.post(retry, json={"acknowledge_uncertain": True})
    else:
        response = client.post(retry, json={})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert client.post(retry, json={"acknowledge_uncertain": True}).status_code == 409
    after = store.get_run(run_id)
    assert after["run"]["destination"] == before["run"]["destination"]
    assert after["result"] == before["result"] and after["artifacts"] == before["artifacts"]
    assert after["cost"]["requests"] == 0


def test_package_written_before_database_manifest_is_recovered_exactly(delivery_api):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery
    client, store, workbook, complete, folder, smtp_state, service = delivery_api
    detail = complete("rejected")
    run_id = detail["run"]["id"]
    manifest = prepare_trade_idea_delivery(detail["run"], detail["result"], [workbook],
        output_dir=folder / run_id, model_roots=[folder], language="en")
    assert store.get_run(run_id)["artifacts"] is None
    original = {path: Path(path).read_bytes() for path in manifest["attachments"]}
    service.deliver_trade_idea(store, run_id, output_dir=folder / run_id)
    restored = store.get_run(run_id)
    assert restored["artifacts"] == manifest
    assert restored["email"]["status"] == "accepted"
    assert {path: Path(path).read_bytes() for path in original} == original
    assert len(smtp_state[0]) == 1


def test_authorized_download_refuses_post_manifest_mutation(delivery_api):
    client, store, workbook, complete, folder, smtp_state, service = delivery_api
    run_id = complete("rejected")["run"]["id"]
    service.deliver_trade_idea(store, run_id, valuation_results=[workbook], output_dir=folder / run_id)
    manifest = store.get_run(run_id)["artifacts"]
    for artifact in manifest["artifacts"]:
        path = Path(artifact["path"])
        path.write_bytes(path.read_bytes() + b"mutated after send")
        assert client.get(f"/trade-ideas/runs/{run_id}/artifacts/{artifact['id']}").status_code == 409
        detail = client.get(f'/trade-ideas/runs/{run_id}').json()
        assert detail['states']['artifacts'] == 'unavailable'
        assert detail['completion']['status'] == 'incomplete'
        assert detail['artifact_availability']['reason']


def test_manifest_cannot_seal_a_stale_dcn_destination_after_demotion(delivery_api):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery
    client, store, workbook, complete, folder, smtp_state, service = delivery_api
    detail = complete("favorable")
    run_id = detail["run"]["id"]
    manifest = prepare_trade_idea_delivery(detail["run"], detail["result"], [workbook],
        output_dir=folder / run_id, model_roots=[folder], language="en")
    assert manifest["destination"]["kind"] == "dcn"
    store.demote_unreviewed_destination(run_id, "Synthetic report failure after routing")
    with pytest.raises(ValueError, match="destination"):
        store.save_manifest(run_id, manifest)
    assert store.get_run(run_id)["artifacts"] is None
    assert smtp_state[0] == []


def test_download_refuses_database_manifest_with_unattested_inventory(delivery_api):
    client, store, workbook, complete, folder, smtp_state, service = delivery_api
    run_id = complete('rejected')['run']['id']
    service.deliver_trade_idea(store, run_id, valuation_results=[workbook],
                              output_dir=folder / run_id, send_email=False)
    manifest = store.get_run(run_id)['artifacts']
    artifact = manifest['artifacts'][0]
    manifest['artifacts'][0]['name'] = 'unattested-name.pdf'
    with store._connect() as connection:
        connection.execute('UPDATE trade_idea_delivery SET manifest_json=? WHERE run_id=?',
                           (json.dumps(manifest), run_id))
    assert client.get(f"/trade-ideas/runs/{run_id}/artifacts/{artifact['id']}").status_code == 409
    detail = client.get(f'/trade-ideas/runs/{run_id}').json()
    assert detail['artifacts'] == []
    assert detail['states']['artifacts'] == 'unavailable'
    assert detail['completion']['status'] == 'incomplete'
    assert client.post(f'/trade-ideas/runs/{run_id}/email/retry', json={}).status_code == 409
    assert not smtp_state[0]


def test_native_delivery_recovery_endpoint_reuses_files_without_ai_or_email(delivery_api):
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery
    client, store, workbook, complete, folder, smtp_state, service = delivery_api
    detail = complete('rejected')
    run_id = detail['run']['id']
    manifest = prepare_trade_idea_delivery(detail['run'], detail['result'], [workbook],
        output_dir=folder / 'trade_ideas' / run_id, model_roots=[folder], language='en')
    sidecar = Path(workbook['path']).with_suffix('.payload.json')
    before = {path: Path(path).read_bytes() for path in [*manifest['attachments'], str(sidecar)]}
    for attempt in range(3):
        response = client.post(f'/trade-ideas/runs/{run_id}/delivery/recover', json={})
        assert response.status_code == 200, response.text
        assert response.json()['states']['artifacts'] == 'ready'
        assert {path: Path(path).read_bytes() for path in before} == before
        if attempt == 0:
            # Lose only the fixture's original artifacts, keeping the native vault.
            for path in before:
                artifact = Path(path)
                assert artifact.resolve().is_relative_to(folder.resolve())
                artifact.unlink()
    assert store.get_run(run_id)['result'] == detail['result']
    assert {path: Path(path).read_bytes() for path in before} == before
    assert not smtp_state[0]
    assert store.get_run(run_id)['cost']['requests'] == 0


def test_native_resume_endpoint_authorization_and_double_click_are_exact(delivery_api, monkeypatch):
    from bellomberg.api import trade_idea_routes as routes
    from test_trade_idea_store import _save_resume_checkpoint
    client, store, _workbook, _complete, _folder, smtp_state, _service = delivery_api
    parent = store.create_run(acceptance(), idempotency_key='api-resume-parent')['run']['id']
    token = store.claim_run(parent)
    _save_resume_checkpoint(store, parent, token)
    store.interrupt_run(parent, reason='Synthetic process crash')
    spawned = []
    monkeypatch.setattr(routes, '_spawn_worker', lambda run_id, *_a, **_k: spawned.append(run_id))
    endpoint = f'/trade-ideas/runs/{parent}/resume'
    body = {'idempotency_key': 'api-resume-child', 'cost_acknowledged': False}
    assert client.post(endpoint, json=body).status_code == 428
    assert spawned == []
    body['cost_acknowledged'] = True
    first = client.post(endpoint, json=body)
    second = client.post(endpoint, json=body)
    assert first.status_code == second.status_code == 202, (first.text, second.text)
    assert first.json()['run_id'] == second.json()['run_id']
    assert spawned == [first.json()['run_id']]
    assert store.get_run(parent)['run']['technical_status'] == 'interrupted'
    assert not smtp_state[0]
