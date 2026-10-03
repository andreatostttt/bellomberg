"""Independent SQLite writers and uncertain research jobs retain exact identity."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import sqlite3
from uuid import uuid4

import pytest

from test_trade_idea_store import db_path, migrated, request, result, store
from bellomberg.storage.trade_idea_workspace import WorkspaceEvents


@pytest.fixture
def events(migrated):
    current=store(migrated)
    run=current.create_run(request(),idempotency_key=str(uuid4()))['run']['id']
    token=current.claim_run(run)
    current.finish_run(run,token,result(proposal=False),'incomplete',reason='Declared synthetic recovery fixture')
    return WorkspaceEvents(current),run


def apply(events,run,compute,key=None):
    return events.apply(run,'simulate',{'test':'recovery'},compute,
                        request_id=key or str(uuid4()),generation_id='synthetic-generation')


def test_slow_compute_does_not_hold_database_write_lock(events):
    archive,run=events
    writes=[]
    def compute(_):
        def independent_writer():
            with sqlite3.connect(archive.store.db_path,timeout=.15) as conn:
                conn.execute('UPDATE cash_state SET version=version+1 WHERE singleton_id=1')
            writes.append(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(independent_writer).result(timeout=2)
        return {'status':'ready'}
    assert apply(archive,run,compute)['data']['status']=='ready'
    assert writes==[1]


def test_ready_receipt_recovers_exact_artifact_without_recompute(events,tmp_path,monkeypatch):
    archive,run=events; key=str(uuid4()); calls=[]
    artifact=tmp_path/'exact.xlsx'; artifact.write_bytes(b'declared synthetic artifact')
    def compute(_):
        calls.append(1)
        return {'status':'ready','path':str(artifact),'sha256':sha256(artifact.read_bytes()).hexdigest()}
    publish=archive._publish
    monkeypatch.setattr(archive,'_publish',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('crash after durable result')))
    with pytest.raises(RuntimeError,match='crash'):
        apply(archive,run,compute,key)
    assert archive.pending(run)[0]['status']=='ready_to_recover'
    monkeypatch.setattr(archive,'_publish',publish)
    recovered=archive.recover(run,key)
    assert recovered['data']['path']==str(artifact) and calls==[1]
    assert apply(archive,run,compute,key)==recovered and calls==[1]
    assert len(archive.list(run))==1
    assert not archive.pending(run)


def test_crash_before_durable_result_never_repeats_compute(events,tmp_path):
    archive,run=events; key=str(uuid4()); calls=[]
    def interrupted(_):
        calls.append(1); (tmp_path/'partial.xlsx').write_bytes(b'partial synthetic artifact')
        raise SystemExit('abrupt shutdown')
    with pytest.raises(SystemExit):apply(archive,run,interrupted,key)
    with pytest.raises(ValueError,match='interrupted|pending|interrott'):
        apply(WorkspaceEvents(archive.store),run,interrupted,key)
    assert calls==[1] and not archive.list(run)
    assert archive.pending(run)[0]['status']=='pending_or_interrupted'
    with pytest.raises(ValueError,match='interrupted'):
        archive.recover(run,key)


def test_recovery_rejects_modified_artifact(events,tmp_path,monkeypatch):
    archive,run=events; key=str(uuid4()); path=tmp_path/'exact.zip'; path.write_bytes(b'first')
    publish=archive._publish
    monkeypatch.setattr(archive,'_publish',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):
        apply(archive,run,lambda _:{'path':str(path),'sha256':sha256(b'first').hexdigest()},key)
    path.write_bytes(b'changed'); monkeypatch.setattr(archive,'_publish',publish)
    with pytest.raises(ValueError,match='changed|modified'):
        apply(archive,run,lambda _:pytest.fail('recomputed tampered recovery'),key)
    assert not archive.list(run)
