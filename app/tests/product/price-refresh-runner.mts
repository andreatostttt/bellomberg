import assert from 'node:assert/strict';
import test from 'node:test';
import { startGlobalPriceRefresh } from '../../src/lib/price-refresh.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

function harness() {
  let hidden = false;
  let scheduled: (() => void) | null = null;
  let cancelled = false;
  const order: string[] = [];
  const errors: unknown[] = [];
  const runner = startGlobalPriceRefresh({
    updatePrices: async () => { order.push('updatePrices'); },
    readPortfolio: async () => { order.push('readPortfolio'); return { nav: 10 }; },
    publish: () => { order.push('publish'); },
    isHidden: () => hidden,
    schedule: (callback, ms) => {
      assert.equal(ms, 60_000);
      scheduled = callback;
      return 7;
    },
    cancel: id => { assert.equal(id, 7); cancelled = true; },
    onError: error => errors.push(error),
  });
  return { runner, order, errors, setHidden: (value: boolean) => { hidden = value; },
    tick: () => scheduled?.(), get cancelled() { return cancelled; } };
}

test('runs immediately, schedules every minute, and publishes in order', async () => {
  const h = harness();
  await h.runner.firstRefresh;
  assert.deepEqual(h.order, ['updatePrices', 'readPortfolio', 'publish']);
  h.order.length = 0;
  h.tick();
  await h.runner.refresh();
  assert.deepEqual(h.order, ['updatePrices', 'readPortfolio', 'publish']);
  h.runner.stop();
  assert.equal(h.cancelled, true);
});

test('does not overlap an unfinished refresh', async () => {
  const update = deferred<void>();
  let calls = 0;
  const h = startGlobalPriceRefresh({
    updatePrices: async () => { calls += 1; await update.promise; },
    readPortfolio: async () => ({ nav: 10 }),
    publish: () => {},
    isHidden: () => false,
    schedule: _callback => 1,
    cancel: () => {},
  });
  const first = h.refresh();
  const second = h.refresh();
  assert.equal(calls, 1);
  update.resolve();
  await Promise.all([first, second]);
  h.stop();
});

test('skips hidden windows and refreshes when called after foreground return', async () => {
  const h = harness();
  await h.runner.firstRefresh;
  h.runner.stop();
  let hidden = true;
  let callback: (() => void) | null = null;
  let updates = 0;
  const foreground = startGlobalPriceRefresh({
    updatePrices: async () => { updates += 1; },
    readPortfolio: async () => ({ nav: 10 }),
    publish: () => {},
    isHidden: () => hidden,
    schedule: cb => { callback = cb; return 2; },
    cancel: () => {},
  });
  await foreground.firstRefresh;
  assert.equal(updates, 0);
  hidden = false;
  await foreground.refresh();
  assert.equal(updates, 1);
  assert.equal(typeof callback, 'function');
  foreground.stop();
});

test('stop prevents late publication and a failed tick does not stop later ticks', async () => {
  const read = deferred<{ nav: number }>();
  let fail = true;
  let publishes = 0;
  let callback: (() => void) | null = null;
  const runner = startGlobalPriceRefresh({
    updatePrices: async () => { if (fail) { fail = false; throw new Error('temporary'); } },
    readPortfolio: async () => read.promise,
    publish: () => { publishes += 1; },
    isHidden: () => false,
    schedule: cb => { callback = cb; return 3; },
    cancel: () => {},
  });
  await runner.firstRefresh;
  assert.equal(publishes, 0);
  const pending = runner.refresh();
  runner.stop();
  read.resolve({ nav: 20 });
  await pending;
  assert.equal(publishes, 0);
  callback?.();
  assert.equal(publishes, 0);
});

test('a 200 with failed>0 is a partial update, an undeclared result is unknown (never a plain success)', async () => {
  const { priceUpdateOutcome } = await import('../../src/lib/price-refresh.ts');
  assert.deepEqual(priceUpdateOutcome({ updated: 5, failed: 0, details: [] }), { failed: 0 });
  assert.deepEqual(priceUpdateOutcome({ updated: 3, failed: 2, details: [] }), { failed: 2 });
  assert.deepEqual(priceUpdateOutcome({ failed: ['ZZTEST', 'ACME.MI'] }), { failed: 2 });
  assert.deepEqual(priceUpdateOutcome({ updated: 3 }), { failed: null });
  assert.deepEqual(priceUpdateOutcome(undefined), { failed: null });
  const published: unknown[] = [];
  const runner = startGlobalPriceRefresh({
    updatePrices: async () => ({ updated: 1, failed: 3 }),
    readPortfolio: async () => ({ nav: 1 }),
    publish: (_s, outcome) => { published.push(outcome); },
    isHidden: () => false, schedule: () => 1, cancel: () => {},
  });
  await runner.firstRefresh; runner.stop();
  assert.deepEqual(published, [{ failed: 3 }]);
});
