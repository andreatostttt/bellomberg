// macOS/Linux desktop contracts (recon/mac.md A7-A9, 12/09): the backend interpreter is resolved
// without a shell, the dialogs name exactly what is missing, closing the last window on macOS
// keeps the owned backend alive, and the header is the window drag region. Real TypeScript is
// transpiled and run in a sandbox: no Electron process, no Python, no network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const vm = require('node:vm');
const ts = require('typescript');
const { EventEmitter } = require('node:events');
const root = path.resolve(__dirname, '../..');

function loadModule(file) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const scope = { exports: {}, URL, console }; vm.runInNewContext(js, scope); return scope.exports;
}
const security = loadModule('electron/security.ts');
const mainSource = fs.readFileSync(path.join(root, 'electron/main.ts'), 'utf8');
const mainTree = ts.createSourceFile('main.ts', mainSource, ts.ScriptTarget.Latest, true);
const timeoutDeclaration = mainTree.statements
  .flatMap(statement => ts.isVariableStatement(statement) ? [...statement.declarationList.declarations] : [])
  .find(declaration => declaration.name.getText(mainTree) === 'BACKEND_START_TIMEOUT_MS');
const backendStartTimeoutMs = Number(timeoutDeclaration?.initializer?.getText(mainTree).replaceAll('_', ''));
assert.ok(Number.isFinite(backendStartTimeoutMs), 'backend startup timeout is declared');

function functionText(name) {
  const node = mainTree.statements.find(s => ts.isFunctionDeclaration(s) && s.name?.text === name);
  assert.ok(node, 'real function located: ' + name);
  return node.getText(mainTree);
}
function handlerText(event) {
  let found = null;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(mainTree) === 'app.on'
      && ts.isStringLiteral(node.arguments[0]) && node.arguments[0].text === event) found = node.arguments[1].getText(mainTree);
    ts.forEachChild(node, visit);
  }
  visit(mainTree);
  assert.ok(found, 'real app.on handler located: ' + event);
  return found;
}
function run(text, context) {
  const js = ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInContext(js, context);
}
function handler(event, scope) {
  const context = vm.createContext(scope);
  run('globalThis.handler = ' + handlerText(event), context);
  return context.handler;
}

const VENV = '/synthetic/backend/.venv/bin/python';

// Il launcher .command si esegue davvero con /bin/bash: ha senso solo su macOS. Altrove il test e'
// SALTATO e lo dichiara (prima falliva su Windows per lo spawn di /bin/bash, mascherando il resto).
// 05/10: tools/macos/ non e' nell'ALLOWLIST dell'export, quindi nel repo pubblico il launcher non
// esiste e su macos-source lo spawn usciva 127 («No such file or directory»). Senza il file il test
// e' SALTATO e lo dichiara, come vol-atlas.cjs per tools/qa/vol_browser.py.
const LAUNCHER_MACOS = path.resolve(root, '../tools/macos/Avvia Bellomberg.command');
const SOLO_MACOS = process.platform !== 'darwin'
  ? `launcher macOS: piattaforma ${process.platform}, /bin/bash e .command non applicabili`
  : (fs.existsSync(LAUNCHER_MACOS) ? false : 'tools/macos/Avvia Bellomberg.command is not in this tree (not published)');
test('Avvia Bellomberg.command uses this checkout venv despite an inherited Python setting', { skip: SOLO_MACOS }, () => {
  const tempBin = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-launcher-'));
  const launcher = LAUNCHER_MACOS;
  const fakeNpm = path.join(tempBin, 'npm');
  fs.writeFileSync(fakeNpm, '#!/bin/sh\nprintf "%s\\n" "$BELLOMBERG_PYTHON"\n');
  fs.chmodSync(fakeNpm, 0o755);
  try {
    const result = spawnSync('/bin/bash', [launcher], {
      encoding: 'utf8',
      env: { ...process.env, PATH: tempBin + path.delimiter + process.env.PATH,
        BELLOMBERG_PYTHON: '/opt/homebrew/bin/python3' },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), path.resolve(root, '../.venv/bin/python'));
  } finally {
    fs.rmSync(tempBin, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------------------------
// pure resolution (electron/security.ts)
// ---------------------------------------------------------------------------------------------
test('macOS without BELLOMBERG_PYTHON prefers the backend virtual environment', () => {
  const choice = security.defaultPython('darwin', '/synthetic/backend', p => p === VENV);
  assert.deepEqual(JSON.parse(JSON.stringify(choice)), { python: VENV, source: 'venv', tried: [] });
});

test('macOS without a virtual environment falls back to python3 and names what it tried', () => {
  const choice = security.defaultPython('darwin', '/synthetic/backend/', () => false);
  assert.equal(choice.python, 'python3');
  assert.equal(choice.source, 'PATH');
  assert.deepEqual(Array.from(choice.tried), [VENV]);
});

test('Linux resolves like macOS', () => {
  assert.equal(security.defaultPython('linux', '/synthetic/backend', p => p === VENV).python, VENV);
  assert.equal(security.defaultPython('linux', '/synthetic/backend', () => false).python, 'python3');
});

test('Windows keeps its historical default and does not consult the venv', () => {
  let asked = 0;
  const choice = security.defaultPython('win32', 'C:\\backend', () => { asked++; return true; });
  assert.deepEqual(JSON.parse(JSON.stringify(choice)), { python: 'python', source: 'PATH', tried: [] });
  assert.equal(asked, 0);
});

// ---------------------------------------------------------------------------------------------
// wiring: startPythonBackend (electron/main.ts) on darwin
// ---------------------------------------------------------------------------------------------
function backendScope({ platform = 'darwin', env = {}, exists = () => false, scriptExists = true, alreadyUp = false, child: childPlan = 'up', alive = false, readyAfterMs = 500 } = {}) {
  const errors = []; const spawns = []; let killed = 0; let pings = 0; let now = 0;
  const context = vm.createContext({
    PROJECT_ROOT: '/synthetic/backend', API_PORT: 8765, quitting: false,
    BACKEND_START_TIMEOUT_MS: backendStartTimeoutMs,
    pythonBackend: alive ? { exitCode: null } : null, backendOwned: alive,
    console: { log() {}, error() {} }, process: { platform, env },
    Date: { now: () => now },
    path: path.posix, fs: { existsSync: p => p === '/synthetic/backend/bellomberg_api.py' ? scriptExists : exists(p) }, backendError(message) { errors.push(message); },
    defaultPython: security.defaultPython,
    pingBackend: async () => { pings++; return alreadyUp || (childPlan === 'up' && now >= readyAfterMs); },
    setTimeout(callback, delay = 0) { now += delay; callback(); },
    spawn(executable, args, options) {
      spawns.push({ executable, args, options });
      const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.exitCode = null;
      child.kill = () => { killed++; child.exitCode = 0; child.emit('exit', 0); };
      if (childPlan === 'enoent') queueMicrotask(() => child.emit('error', new Error('spawn python3 ENOENT')));
      if (childPlan === 'missing-module') queueMicrotask(() => {
        child.stderr.emit('data', Buffer.from('Traceback (most recent call last):\n  File "bellomberg_api.py", line 1\nModuleNotFoundError: No module named \'fastapi\'\n'));
        child.exitCode = 1; child.emit('exit', 1);
      });
      return child;
    },
  });
  for (const name of ['startPythonBackend', 'stopOwnedBackend']) run(functionText(name), context);
  return { context, errors, spawns, counts: () => ({ killed, pings }) };
}

test('darwin: python3 missing from the PATH is declared with the venv path that was tried', async () => {
  const scope = backendScope({ child: 'enoent' });
  await scope.context.startPythonBackend();
  assert.equal(scope.spawns.length, 1);
  assert.equal(scope.spawns[0].executable, 'python3');
  assert.equal(scope.spawns[0].options.shell, undefined, 'no shell');
  assert.equal(scope.errors.length, 1, JSON.stringify(scope.errors));
  const message = scope.errors[0];
  for (const needle of ['ENOENT', 'python3', 'PATH', VENV, 'BELLOMBERG_PYTHON']) assert.ok(message.includes(needle), needle + ' in: ' + message);
  assert.equal(scope.context.pythonBackend, null);
});

test('darwin: the backend virtual environment is used when it exists', async () => {
  const scope = backendScope({ exists: p => p === VENV });
  await scope.context.startPythonBackend();
  assert.equal(scope.spawns[0].executable, VENV);
  assert.equal(scope.errors.length, 0, JSON.stringify(scope.errors));
  assert.equal(scope.context.backendOwned, true);
});

test('darwin: a cold Python import can take longer than 30 seconds and still start', async () => {
  const scope = backendScope({ exists: p => p === VENV, readyAfterMs: 45_000 });
  await scope.context.startPythonBackend();
  assert.equal(scope.spawns[0].executable, VENV);
  assert.equal(scope.errors.length, 0, JSON.stringify(scope.errors));
  assert.equal(scope.counts().killed, 0, 'slow but healthy backend is not killed');
});

test('darwin: BELLOMBERG_PYTHON wins over the venv', async () => {
  const scope = backendScope({ env: { BELLOMBERG_PYTHON: '/opt/custom/bin/python' }, exists: () => true });
  await scope.context.startPythonBackend();
  assert.equal(scope.spawns[0].executable, '/opt/custom/bin/python');
  assert.equal(scope.errors.length, 0);
});

test('darwin: a BELLOMBERG_PYTHON path that does not exist is refused before spawning', async () => {
  const scope = backendScope({ env: { BELLOMBERG_PYTHON: '/opt/missing/bin/python' }, exists: () => false });
  await scope.context.startPythonBackend();
  assert.equal(scope.spawns.length, 0, 'nothing spawned');
  assert.equal(scope.errors.length, 1);
  assert.ok(scope.errors[0].includes('BELLOMBERG_PYTHON') && scope.errors[0].includes('/opt/missing/bin/python') && /non esiste/.test(scope.errors[0]), scope.errors[0]);
});

test('darwin: a backend that exits reports the interpreter and the last error line (what is missing)', async () => {
  const scope = backendScope({ exists: p => p === VENV, child: 'missing-module' });
  await scope.context.startPythonBackend();
  assert.equal(scope.errors.length, 1, JSON.stringify(scope.errors));
  const message = scope.errors[0];
  for (const needle of ['Codice di uscita / Exit code: 1', VENV, "ModuleNotFoundError: No module named 'fastapi'"]) assert.ok(message.includes(needle), needle + ' in: ' + message);
});

test('an owned backend that is still alive is neither pinged nor spawned again (macOS activate)', async () => {
  const scope = backendScope({ alive: true });
  await scope.context.startPythonBackend();
  assert.equal(scope.spawns.length, 0);
  assert.equal(scope.counts().pings, 0);
  assert.equal(scope.context.backendOwned, true, 'ownership kept');
});

// ---------------------------------------------------------------------------------------------
// wiring: app lifecycle handlers (A9)
// ---------------------------------------------------------------------------------------------
function lifecycle(platform) {
  const counts = { stopped: 0, quit: 0 };
  const scope = { process: { platform }, stopOwnedBackend() { counts.stopped++; }, app: { quit() { counts.quit++; } }, quitting: false };
  return { counts, scope };
}

test('darwin: closing the last window keeps the owned backend and the app alive', () => {
  const { counts, scope } = lifecycle('darwin');
  handler('window-all-closed', scope)();
  assert.deepEqual(counts, { stopped: 0, quit: 0 });
});

test('win32: closing the last window stops the owned backend and quits', () => {
  const { counts, scope } = lifecycle('win32');
  handler('window-all-closed', scope)();
  assert.deepEqual(counts, { stopped: 1, quit: 1 });
});

test('before-quit stops the owned backend on every platform', () => {
  for (const platform of ['darwin', 'win32', 'linux']) {
    const { counts, scope } = lifecycle(platform);
    const context = vm.createContext(scope);
    run('globalThis.handler = ' + handlerText('before-quit'), context);
    context.handler();
    assert.equal(counts.stopped, 1, platform);
    assert.equal(context.quitting, true, platform);
  }
});

test('activate with no window starts (or reuses) the backend and recreates the window', async () => {
  const counts = { started: 0, created: 0 };
  const scope = { BrowserWindow: { getAllWindows() { return []; } }, quitting: false,
    async startPythonBackend() { counts.started++; }, createWindow() { counts.created++; } };
  await handler('activate', scope)();
  assert.deepEqual(counts, { started: 1, created: 1 });
});

// ---------------------------------------------------------------------------------------------
// A7: drag region for titleBarStyle hiddenInset
// ---------------------------------------------------------------------------------------------
test('preload marks the document with the real Electron platform', () => {
  const source = fs.readFileSync(path.join(root, 'electron/preload.ts'), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  for (const platform of ['darwin', 'win32', 'linux']) {
    const document = { documentElement: { dataset: {} } }; const listeners = {};
    vm.runInNewContext(js, {
      exports: {}, document, window: { addEventListener(event, callback) { listeners[event] = callback; } },
      process: { platform, argv: ['--bellomberg-api-port=19876', '--bellomberg-launch-id=synthetic'] },
      require(name) { assert.equal(name, 'electron'); return { contextBridge: { exposeInMainWorld() {} } }; },
    });
    assert.equal(typeof listeners.DOMContentLoaded, 'function');
    listeners.DOMContentLoaded();
    assert.equal(document.documentElement.dataset.platform, platform);
  }
});

test('only macOS uses the app header as drag region and its controls stay clickable', () => {
  const css = fs.readFileSync(path.join(root, 'src/index.css'), 'utf8');
  const rules = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].map(m => [m[1].trim(), m[2]]);
  const drag = rules.filter(([, body]) => /-webkit-app-region\s*:\s*drag/.test(body)).map(([sel]) => sel);
  const noDrag = rules.filter(([, body]) => /-webkit-app-region\s*:\s*no-drag/.test(body)).map(([sel]) => sel).join(',');
  assert.deepEqual(drag, ['html[data-platform="darwin"] header'], 'only the macOS app header drags: ' + JSON.stringify(drag));
  for (const control of ['header button', 'header a', 'header input']) assert.ok(noDrag.includes(control), control + ' is no-drag');
  assert.ok(mainSource.includes("titleBarStyle: 'hiddenInset'"), 'main.ts still hides the native title bar (inset traffic lights)');
});
