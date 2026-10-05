import assert from 'node:assert/strict';
import test from 'node:test';
import { createNewsRefreshWatcher, esitoGiro, NewsRefreshError } from '../../src/lib/news-refresh.ts';

const running = (id = 'job-1') => ({ id, status: 'running' as const });
const success = (id = 'job-1') => ({ id, status: 'success' as const });

test('starts one refresh and polls only until success', async () => {
  let starts = 0;
  let reads = 0;
  const waits: number[] = [];
  const states = [running(), success()];
  const watcher = createNewsRefreshWatcher({
    start: async () => { starts += 1; return { accepted: true, job: running() }; },
    readStatus: async () => states[reads++],
    wait: async (ms) => { waits.push(ms); },
  });

  const result = await watcher.run();
  assert.equal(starts, 1);
  assert.equal(reads, 2);
  assert.deepEqual(waits, [2000, 2000]);
  assert.equal(result.status, 'success');
});

test('follows an already-running job when the trigger is rejected', async () => {
  let starts = 0;
  const watcher = createNewsRefreshWatcher({
    start: async () => { starts += 1; return { accepted: false, job: running('existing') }; },
    readStatus: async () => success('existing'),
    wait: async () => {},
  });

  const result = await watcher.run();
  assert.equal(starts, 1);
  assert.equal(result.id, 'existing');
  assert.equal(result.joined, true, 'a refresh we did not start is declared as joined');
});

test('accepted:false with a finished job is rejected: the OLD result is never shown as the new one', async () => {
  let reads = 0;
  const old = { id: 'job-old', status: 'success' as const, finished_at: '2026-10-04T08:00:00', result: { saved: 7, skipped_duplicates: 2 } };
  const watcher = createNewsRefreshWatcher({
    start: async () => ({ accepted: false, job: old }),
    readStatus: async () => { reads += 1; return old; },
    wait: async () => {},
  });
  await assert.rejects(watcher.run(), (e: unknown) => e instanceof NewsRefreshError && e.code === 'rejected' && e.job?.id === 'job-old');
  assert.equal(reads, 0);
});

test('a refresh we started is not marked as joined; a response without the accepted flag is a protocol error', async () => {
  const mine = createNewsRefreshWatcher({ start: async () => ({ accepted: true, job: running() }), readStatus: async () => success(), wait: async () => {} });
  assert.equal((await mine.run()).joined, false);
  const old = createNewsRefreshWatcher({ start: async () => ({ fetched: 3, saved: 1 } as any), readStatus: async () => success(), wait: async () => {} });
  await assert.rejects(old.run(), (e: unknown) => e instanceof NewsRefreshError && e.code === 'bad_status');
});

test('esitoGiro: missing counts stay null (n/a), never 0', () => {
  assert.deepEqual(esitoGiro({ id: 'j', status: 'success', result: { saved: 4, skipped_duplicates: 0, fetched: 9, not_classified: 2 } }), { saved: 4, duplicates: 0, fetched: 9, notClassified: 2 });
  assert.deepEqual(esitoGiro({ id: 'j', status: 'success', result: {} }), { saved: null, duplicates: null, fetched: null, notClassified: null });
  assert.deepEqual(esitoGiro(null), { saved: null, duplicates: null, fetched: null, notClassified: null });
});

test('contract G3: polls the followed job by id; interrupted is a declared final state, not a protocol error', async () => {
  const asked: string[] = [];
  const watcher = createNewsRefreshWatcher({
    start: async () => ({ accepted: false, reason: 'already_running', job: running('job-g3') }),
    readStatus: async (id: string) => { asked.push(id); return { id: 'job-g3', status: 'interrupted', error: 'backend fermato' }; },
    wait: async () => {},
  });
  const job = await watcher.run();
  assert.deepEqual(asked, ['job-g3']);
  assert.equal(job.status, 'interrupted'); assert.equal(job.joined, true);
});

test('rejects missing, changed, idle, and unexpected job states', async () => {
  const cases = [
    { start: { accepted: true, job: { id: null, status: 'running' } } },
    { start: { accepted: true, job: running() }, read: success('other') },
    { start: { accepted: true, job: { id: 'job-1', status: 'idle' } } },
    { start: { accepted: true, job: running() }, read: { id: 'job-1', status: 'paused' } },
  ];
  for (const item of cases) {
    const watcher = createNewsRefreshWatcher({
      start: async () => item.start as any,
      readStatus: async () => (item.read || success()) as any,
      wait: async () => {},
    });
    await assert.rejects(watcher.run());
  }
});

// Revisione G9b R1: la lettura ha una fine garantita e dichiarata.
test('R1: job gone (404) = job_lost, backend down = unreachable, endless running = declared timeout', async () => {
  const lost = createNewsRefreshWatcher({ start: async () => ({ accepted: true, job: running('job-x') }),
    readStatus: async () => { throw { isAxiosError: true, response: { status: 404, data: { detail: 'sconosciuto' } } }; }, wait: async () => {} });
  await assert.rejects(lost.run(), (e: unknown) => e instanceof NewsRefreshError && e.code === 'job_lost');
  const down = createNewsRefreshWatcher({ start: async () => ({ accepted: true, job: running('job-y') }),
    readStatus: async () => { throw { isAxiosError: true, request: {}, message: 'Network Error' }; }, wait: async () => {} });
  await assert.rejects(down.run(), (e: unknown) => e instanceof NewsRefreshError && e.code === 'unreachable');
  let clock = 0, reads = 0;
  const endless = createNewsRefreshWatcher({ start: async () => ({ accepted: true, job: running('job-z') }),
    readStatus: async () => { reads += 1; return running('job-z'); }, wait: async (ms) => { clock += ms; }, now: () => clock, maxMs: 60_000 });
  await assert.rejects(endless.run(), (e: unknown) => e instanceof NewsRefreshError && e.code === 'timeout' && e.minuti === 1 && e.job?.id === 'job-z');
  assert.equal(reads, 29, 'reads until the cap, then stops');
});
