// Contract probe for the synthetic page fixture control endpoint. This uses
// only an ephemeral localhost server and never launches the application.
const assert = require('node:assert/strict');
const { performance } = require('node:perf_hooks');
const { startFixture } = require('./pages-modern-fixtures.cjs');

async function run() {
  const fixture = startFixture(null);
  await new Promise((resolve, reject) => {
    fixture.server.once('error', reject);
    fixture.server.listen(0, '127.0.0.1', resolve);
  });
  const address = fixture.server.address();
  const origin = `http://127.0.0.1:${address.port}`;
  const json = async (path, init) => {
    const response = await fetch(origin + path, init);
    return { status: response.status, body: await response.json() };
  };

  try {
    const configured = await json('/__fixture', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        clearAll: true,
        setRead: { '/probe/read': { body: { value: 'synthetic read' }, delayMs: 120 } },
        setWrite: {
          '/probe/write': { status: 201, body: { value: 'synthetic write' }, delayMs: 80 },
          '/probe/configured-error': { status: 503, body: { detail: 'Synthetic configured error.' } },
        },
      }) });
    assert.equal(configured.status, 200);
    assert.deepEqual(configured.body.readOverrides, ['/probe/read'], 'clearAll is applied before read overrides');
    assert.deepEqual(configured.body.writeOverrides, ['/probe/write', '/probe/configured-error'],
      'clearAll is applied before write overrides');

    const readStarted = performance.now();
    const read = await json('/probe/read');
    assert.ok(performance.now() - readStarted >= 100, 'fixture read delay is applied');
    assert.equal(read.status, 200);
    assert.deepEqual(read.body, { value: 'synthetic read' });

    const writeStarted = performance.now();
    const write = await json('/probe/write', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ synthetic: true }) });
    assert.ok(performance.now() - writeStarted >= 65, 'fixture write delay is applied');
    assert.equal(write.status, 201);
    assert.deepEqual(write.body, { value: 'synthetic write' });

    const configuredError = await json('/probe/configured-error', { method: 'DELETE' });
    assert.equal(configuredError.status, 503, 'an explicit synthetic error response is returned as configured');
    assert.deepEqual(configuredError.body, { detail: 'Synthetic configured error.' });

    const unknownRead = await json('/probe/unexpected-read');
    assert.equal(unknownRead.status, 404);
    const unknownWrite = await json('/probe/unexpected-write', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ synthetic: true }) });
    assert.equal(unknownWrite.status, 403);

    const state = await json('/__fixture');
    assert.deepEqual(state.body.unexpectedGets, ['/probe/unexpected-read']);
    assert.deepEqual(state.body.unexpectedWrites, [
      { method: 'POST', route: '/probe/unexpected-write', status: 403 },
    ]);
    assert.equal(state.body.unexpectedWrites.some(item => item.route === '/probe/configured-error'), false,
      'configured error writes are not classified as unexpected');
    assert.equal(state.body.writes.some(item => item.route === '/probe/write'), true,
      'the fixture retains its mutation audit separately from unexpected writes');
    process.stdout.write('pages-modern fixture contract PASS: clearAll ordering, read/write delays, configured errors, unexpected GET/403 write audit\n');
  } finally {
    await new Promise(resolve => fixture.server.close(resolve));
  }
}

run().catch(error => {
  process.stderr.write(String(error?.stack || error) + '\n');
  process.exitCode = 1;
});
