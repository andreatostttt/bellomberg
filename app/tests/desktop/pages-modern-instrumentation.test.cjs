const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const { installRuntimeInstrumentation } = require('./pages-modern-instrumentation.cjs');

function browser() {
  class BrowserTarget extends EventTarget {}
  const window = new BrowserTarget(), document = new BrowserTarget();
  const native = { intervalStarts: 0, intervalCancels: [], timeoutCancels: [] };
  document.querySelector = () => null;
  window.setInterval = () => { native.intervalStarts++; return 12; };
  window.clearInterval = id => native.intervalCancels.push(id);
  window.clearTimeout = id => native.timeoutCancels.push(id);
  const context = vm.createContext({ window, document, EventTarget: BrowserTarget, location: { hash: '#/qa' } });
  vm.runInContext(`(${installRuntimeInstrumentation.toString()})()`, context);
  return { window, document, native, runtime: window.__bbPagesRuntime };
}

test('controlled intervals never run until ticked, keep arguments, and cancel through either native API', async () => {
  const { window, native, runtime } = browser();
  let count = 0;
  const id = window.setInterval(amount => { count += amount; }, 1500, 3);
  assert.equal(native.intervalStarts, 0);
  assert.equal(count, 0);
  await runtime.tick([id]);
  assert.equal(count, 3);
  assert.equal(runtime.snapshot().intervals[0].ticks, 1);
  assert.match(runtime.snapshot().clock, /^fixture-controlled/);
  assert.match(runtime.snapshot().intervals[0].origin, /fixture-subscription-origin/);
  window.clearTimeout(id);
  await runtime.tick([id]);
  assert.equal(count, 3);
  assert.equal(runtime.snapshot().intervals.length, 0);
  window.clearInterval(12);
  assert.deepEqual(native.intervalCancels, [12]);
  window.clearTimeout(13);
  assert.deepEqual(native.timeoutCancels, [13]);
});

test('global listener deduplication, removal, once and callback objects retain native behavior', () => {
  const { window, runtime } = browser();
  let count = 0;
  function callback() { assert.equal(this, window); count++; }
  window.addEventListener('focus', callback);
  window.addEventListener('focus', callback);
  assert.equal(runtime.snapshot().listenerCounts['window:focus'], 1);
  assert.match(runtime.snapshot().listeners[0].origin, /fixture-subscription-origin/);
  assert.equal(typeof runtime.snapshot().listeners[0].createdAt, 'number');
  window.dispatchEvent(new Event('focus'));
  assert.equal(count, 1);
  window.removeEventListener('focus', callback);
  assert.equal(runtime.snapshot().listenerCounts['window:focus'], undefined);
  window.dispatchEvent(new Event('focus'));
  assert.equal(count, 1);
  const object = { handleEvent() { assert.equal(this, object); count++; } };
  window.addEventListener('focus', object, { once: true });
  window.dispatchEvent(new Event('focus'));
  window.dispatchEvent(new Event('focus'));
  assert.equal(count, 2);
  assert.equal(runtime.snapshot().listenerCounts['window:focus'], undefined);
});

test('aborted subscriptions disappear and document subscriptions are tracked independently', () => {
  const { window, document, runtime } = browser();
  const abort = new AbortController();
  const callback = () => {};
  window.addEventListener('focus', callback, { signal: abort.signal });
  document.addEventListener('focus', callback, true);
  abort.abort();
  assert.equal(runtime.snapshot().listenerCounts['window:focus'], undefined);
  const renewed = new AbortController();
  window.addEventListener('focus', callback, { signal: renewed.signal });
  assert.equal(runtime.snapshot().listenerCounts['window:focus'], 1);
  renewed.abort();
  assert.equal(runtime.snapshot().listenerCounts['document:focus'], 1);
  document.removeEventListener('focus', callback, true);
  assert.equal(runtime.snapshot().listeners.length, 0);
});

test('controlled async callbacks finish once and callback faults remain visible to the runner', async () => {
  const { window, runtime } = browser();
  let count = 0;
  const id = window.setInterval(async () => { await Promise.resolve(); count++; }, 10);
  await runtime.tick([id]);
  assert.equal(count, 1);
  window.clearInterval(id);
  const bad = window.setInterval(() => { throw new Error('Synthetic timer fault'); }, 10);
  await assert.rejects(runtime.tick([bad]), /Synthetic timer fault/);
  assert.equal(runtime.snapshot().faults.length, 1);
});
