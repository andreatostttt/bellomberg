"""Independent process/thread claims and the public recovery state projection."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from threading import Event
from uuid import uuid4

import pytest

from test_trade_idea_store import db_path, migrated, request, store
from bellomberg.storage.trade_idea_workspace import WorkspaceEvents


@pytest.fixture
def archive(migrated):
    current = store(migrated)
    run = current.create_run(request('SYNTH-REVIEW'), idempotency_key=str(uuid4()))['run']['id']
    current.request_stop(run)
    return WorkspaceEvents(current), run


def apply(archive, run, key, compute):
    return archive.apply(run, 'simulate', {'synthetic': 'independent-concurrency'}, compute,
        request_id=key, generation_id='synthetic-generation')


def test_two_threads_publish_one_result_and_compute_once(archive):
    events, run = archive
    key, started, joined, release, calls = str(uuid4()), Event(), Event(), Event(), []
    def compute(_):
        calls.append(1); started.set()
        assert release.wait(5)
        return {'status': 'ready', 'synthetic': True}
    def second():
        joined.set()
        return apply(WorkspaceEvents(events.store), run, key, compute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(apply, events, run, key, compute)
        assert started.wait(5)
        other = pool.submit(second)
        assert joined.wait(5)
        release.set()
        assert first.result(timeout=5) == other.result(timeout=5)
    assert calls == [1] and len(events.list(run)) == 1


def test_independent_process_cannot_recompute_an_active_claim(archive, tmp_path):
    events, run = archive
    key, started, release, calls = str(uuid4()), Event(), Event(), []
    marker = tmp_path / 'must-not-compute.txt'
    src = Path(__file__).resolve().parents[1] / 'src'
    child = tmp_path / 'claim_probe.py'
    child.write_text('''from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from bellomberg.storage.trade_idea_store import TradeIdeaStore
from bellomberg.storage.trade_idea_workspace import WorkspaceEvents
store = TradeIdeaStore(sys.argv[2], mandate_loader=lambda: 'a'*64)
def forbidden(_):
    Path(sys.argv[5]).write_text('duplicate computation')
    raise AssertionError('Second process computed')
try:
    WorkspaceEvents(store).apply(sys.argv[3], 'simulate', {'synthetic':'independent-concurrency'},
        forbidden, request_id=sys.argv[4], generation_id='synthetic-generation')
except ValueError as exc:
    assert 'pending or interrupted' in str(exc), exc
    print('durable_claim_blocks_other_process')
else:
    raise AssertionError('Active claim was accepted by another process')
''', encoding='utf8')
    def compute(_):
        calls.append(1); started.set()
        assert release.wait(10)
        return {'status': 'ready', 'synthetic': True}
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(apply, events, run, key, compute)
        assert started.wait(5)
        try:
            probe = subprocess.run([sys.executable, str(child), str(src), events.store.db_path, run, key, str(marker)],
                capture_output=True, text=True, timeout=8)
            assert probe.returncode == 0, probe.stderr
            assert 'durable_claim_blocks_other_process' in probe.stdout
        finally:
            release.set()
        assert work.result(timeout=5)['data']['status'] == 'ready'
    assert calls == [1] and not marker.exists() and len(events.list(run)) == 1


def test_pending_projection_does_not_expose_result_paths_and_recover_uses_exact_sealed_bytes(archive, tmp_path, monkeypatch):
    events, run = archive
    key = str(uuid4())
    path = tmp_path / 'private-exact.xlsx'
    path.write_bytes(b'synthetic exact reviewed bytes')
    publisher = events._publish
    monkeypatch.setattr(events, '_publish', lambda *_a, **_k: (_ for _ in ()).throw(SystemExit('after ready')))
    with pytest.raises(SystemExit):
        apply(events, run, key, lambda _: {'status': 'ready', 'path': str(path),
            'sha256': sha256(path.read_bytes()).hexdigest(), 'private_note': 'PRIVATE_RESULT_CANARY'})
    pending = events.pending(run)
    assert pending and pending[0]['status'] == 'ready_to_recover'
    assert str(path) not in str(pending) and 'PRIVATE_RESULT_CANARY' not in str(pending)
    assert not events.list(run)
    monkeypatch.setattr(events, '_publish', publisher)
    recovered = events.recover(run, key)
    assert recovered['data']['path'] == str(path) and recovered['generation_id'] == 'synthetic-generation'
    assert events.pending(run) == []
    assert events.recover(run, key) == recovered
