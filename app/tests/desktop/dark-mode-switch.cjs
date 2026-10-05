// Nuova Light/Dark switch audit. For every routed page it modifies safe UI
// state (text fields, tabs, scroll), flips Light -> Dark -> Light through the
// real selector and proves that the page root, chart nodes, field values,
// tabs and scroll survive, that no request/timer/stream is started by the
// switch, and that a synthetic in-flight read completes exactly once while the
// theme changes. All HTTP is served by the ephemeral synthetic fixture; other
// origins are blocked. No action buttons are pressed except Performance's
// read-only refresh; advisor run/stop/reset and agent messages are never used.
//
// Env: BB_SWITCH_DIST (default dist), BB_SWITCH_CAPTURE_DIR, BB_SWITCH_ONLY=ids
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { startFixture } = require('./pages-modern-fixtures.cjs');
const { PAGES } = require('./pages-modern.cjs');

const root = path.resolve(__dirname, '../..');
const MARK = 'DARK_SWITCH_RESULT ';
const ROUTES = [...PAGES, { id: 'chat', route: '/chat' }];

function run() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-dark-switch-'));
  const captureDirectory = path.resolve(process.env.BB_SWITCH_CAPTURE_DIR || path.join(root, '..', 'outputs', 'dark-mode', 'switch'));
  fs.mkdirSync(captureDirectory, { recursive: true });
  const only = (process.env.BB_SWITCH_ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
  // Chat archives are empty here (Chat streaming has its own harness, chat-modes.cjs).
  const chatArchives = Object.fromEntries(['capo', 'macro', 'quant', 'politics', 'news'].map(agent => [`/chat/${agent}/sessions`, []]));
  const { server, unexpectedGets, writes } = startFixture(root, { distDir: process.env.BB_SWITCH_DIST || 'dist', reads: chatArchives });
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', async () => {
      const origin = `http://127.0.0.1:${server.address().port}`;
      const entry = path.join(temporary, 'electron-entry.cjs');
      const config = { temporary, captureDirectory, origin, only };
      fs.writeFileSync(entry, `
        const { app } = require('electron');
        const config = ${JSON.stringify(config)};
        app.setPath('userData', require('node:path').join(config.temporary, 'userdata'));
        app.disableHardwareAcceleration();
        app.whenReady().then(() => require(${JSON.stringify(__filename)}).renderer(config))
          .catch(error => { console.error('DARK_SWITCH_ERROR ' + (error?.stack || error)); app.exit(1); });
      `, { mode: 0o600 });
      let child, output = '';
      try {
        const env = { ...process.env };
        for (const key of ['ELECTRON_RUN_AS_NODE', 'BELLOMBERG_BACKEND_DIR', 'BELLOMBERG_PYTHON', 'BELLOMBERG_DESKTOP_API_URL']) delete env[key];
        child = spawn(require('electron'), [entry], { cwd: temporary, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
        child.stdout.on('data', chunk => { output += chunk; });
        child.stderr.on('data', chunk => { output += chunk; });
        const timer = setTimeout(() => child.kill(), 600000);
        const { code } = await new Promise((done, fail) => {
          child.once('error', fail);
          child.once('close', exitCode => done({ code: exitCode }));
        }).finally(() => clearTimeout(timer));
        fs.writeFileSync(path.join(captureDirectory, 'electron.log'), output, { mode: 0o600 });
        const line = output.split(/\r?\n/).find(item => item.startsWith(MARK));
        assert.ok(line, `no result (exit ${code})\n${output.slice(-6000)}`);
        const result = JSON.parse(line.slice(MARK.length));
        result.network = { unexpectedGets: [...new Set(unexpectedGets)], writes: writes.map(w => `${w.method} ${w.route}`) };
        fs.writeFileSync(path.join(captureDirectory, 'summary.json'), JSON.stringify(result, null, 2), { mode: 0o600 });
        console.log(JSON.stringify({ ok: result.ok, pages: result.pages.length, failures: result.failures,
          inflight: result.inflight && { route: result.inflight.route, delta: result.inflight.delta, busySeen: result.inflight.busySeen },
          captureDirectory, unexpectedGets: result.network.unexpectedGets }, null, 1));
        assert.equal(code, 0);
        assert.equal(result.ok, true, JSON.stringify(result.failures));
        assert.deepEqual(result.network.unexpectedGets, []);
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        if (child && child.exitCode === null && child.signalCode === null) child.kill();
        server.closeAllConnections?.(); server.close();
      }
    });
  });
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-dark-switch';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const pause = (ms = 180) => new Promise(r => setTimeout(r, ms));
  const window = new BrowserWindow({ show: false, width: 1920, height: 1080, useContentSize: true,
    webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true, nodeIntegration: false,
      sandbox: true, backgroundThrottling: false,
      additionalArguments: ['--bellomberg-launch-id=synthetic-dark-switch', '--bellomberg-api-port=' + new URL(config.origin).port] } });
  const blocked = [];
  window.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => {
    const allowed = details.url.startsWith(config.origin + '/');
    if (!allowed) blocked.push(details.url);
    callback({ cancel: !allowed });
  });
  const consoleErrors = [];
  window.webContents.on('console-message', (_e, level, message) => { if (level >= 3) consoleErrors.push(String(message).slice(0, 300)); });
  const js = (fn, ...args) => window.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const waitFor = async (predicate, label, timeout = 15000, ...args) => {
    const until = Date.now() + timeout;
    while (Date.now() < until) { if (await js(predicate, ...args)) return; await pause(50); }
    const where = await js(() => ({ hash: location.hash, main: !!document.querySelector('main'),
      relative: !!document.querySelector('main .relative'), body: document.body.innerText.slice(0, 300) }));
    throw new Error('timeout: ' + label + ' ' + JSON.stringify(where));
  };
  const settle = () => js(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 250)))));
  const fixture = async (body) => (await fetch(config.origin + '/__fixture', body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)).json();
  const counts = async () => (await fixture()).requests.filter(r => !r.route.startsWith('/assets/') && !r.route.startsWith('/vendor/') && r.route !== '/')
    .reduce((out, r) => { const key = `${r.method} ${r.route}`; out[key] = (out[key] || 0) + 1; return out; }, {});
  const delta = (a, b) => Object.fromEntries([...new Set([...Object.keys(a), ...Object.keys(b)])]
    .filter(k => (a[k] || 0) !== (b[k] || 0)).map(k => [k, (b[k] || 0) - (a[k] || 0)]));
  const setTheme = async (theme) => {
    await js(t => document.querySelector(`[data-theme-choice="${t}"]`)?.click(), theme);
    await waitFor(t => (document.documentElement.getAttribute('data-bb-theme') || 'light') === t
      && document.querySelector(`[data-theme-choice="${t}"]`)?.getAttribute('aria-pressed') === 'true', 'theme ' + theme, 5000, theme);
    await settle();
  };
  const readState = () => js(() => {
    window.__qaIds ||= new WeakMap(); window.__qaNext ||= 0;
    const id = n => { if (!n) return null; if (!window.__qaIds.has(n)) window.__qaIds.set(n, ++window.__qaNext); return window.__qaIds.get(n); };
    const main = document.querySelector('main');
    const pageRoot = document.querySelector('main .bb-page[data-page]') || document.querySelector('main .relative')?.firstElementChild;
    const scrollers = [...(main?.querySelectorAll('*') || [])].filter(el => el.scrollTop > 0).slice(0, 6);
    return {
      route: location.hash, theme: document.documentElement.getAttribute('data-bb-theme') || 'light',
      shellId: id(document.querySelector('.bb-modern-shell')), rootId: id(pageRoot),
      charts: [...(main?.querySelectorAll('canvas, svg[class*="chart"], .js-plotly-plot, .tv-lightweight-charts') || [])].slice(0, 30).map(id),
      fields: [...(main?.querySelectorAll('input:not([type=password]), textarea, select') || [])].slice(0, 40).map(el => `${el.id || el.name || el.placeholder || el.getAttribute('aria-label') || el.tagName}=${el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value}`),
      tabs: [...(main?.querySelectorAll('[role="tab"][aria-selected="true"]') || [])].map(el => el.id || el.textContent.trim()),
      pressed: [...(main?.querySelectorAll('button[aria-pressed="true"]') || [])].map(el => el.dataset.deskId || el.textContent.trim()).slice(0, 20),
      // Chat's idle inspector collapses in Dark; only the shared conversation scroll is invariant.
      scroll: (main?.querySelector('.f3d') ? scrollers.filter(el => el.classList.contains('conv')) : scrollers).map(el => [id(el), Math.round(el.scrollTop)]),
      busy: main?.querySelectorAll('[aria-busy="true"]').length || 0,
      boundary: !!document.querySelector('.bb-interface-recovery'),
    };
  });

  await window.loadURL(config.origin + '/#/dashboard');
  await pause(300);
  await js(() => {
    localStorage.setItem('bellomberg_token_v1', 'synthetic-fixture-token');
    localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
    localStorage.setItem('bellomberg_last_launch_id', 'synthetic-dark-switch');
    localStorage.setItem('bellomberg.lingua', 'en');
        localStorage.removeItem('bellomberg.interface-theme.v1');
  });
  await new Promise(r => { window.webContents.once('did-finish-load', r); window.webContents.reload(); });
  await waitFor(() => !!document.querySelector('[data-testid="interface-theme-switcher"]') && !!document.querySelector('main .relative'), 'Nuova shell');
  // Count timers, listeners and network primitives created while the theme flips.
  await js(() => {
    const c = window.__qaCounts = { setInterval: 0, addEventListener: 0, fetch: 0, xhr: 0, eventSource: 0, webSocket: 0 };
    window.__qaCounting = false;
    const wrap = (obj, key, name) => { const orig = obj[key]; obj[key] = function (...a) { if (window.__qaCounting) c[name]++; return orig.apply(this, a); }; };
    wrap(window, 'setInterval', 'setInterval'); wrap(window, 'fetch', 'fetch');
    wrap(EventTarget.prototype, 'addEventListener', 'addEventListener');
    wrap(XMLHttpRequest.prototype, 'open', 'xhr');
    const ES = window.EventSource; if (ES) window.EventSource = function (...a) { if (window.__qaCounting) c.eventSource++; return new ES(...a); };
    const WS = window.WebSocket; window.WebSocket = function (...a) { if (window.__qaCounting) c.webSocket++; return new WS(...a); };
  });

  // The app-level news-alert poller starts with a delayed setTimeout and then
  // a 60 s setInterval. Wait for its first poll so that one-time start-up
  // cannot be mistaken for work caused by a theme switch.
  {
    const until = Date.now() + 90000;
    while (Date.now() < until && !((await counts())['GET /news/alerts/unnotified'] > 0)) await pause(500);
    await pause(500);
  }
  // A 60 s poll can still land inside a switch window: it is attributed to the
  // poller (route + one XHR) and recorded, never hidden.
  const BACKGROUND_POLL = 'GET /news/alerts/unnotified';
  // Shell pollers (health, telemetry, alerts) run on their own timers. A switch
  // window that only contains them is measured again: requests caused by the
  // theme would repeat deterministically, coincidences do not.
  const SHELL_POLLERS = new Set([BACKGROUND_POLL, 'GET /health', 'GET /agents/live']);

  const pages = [], failures = [];
  const targets = config.only.length ? ROUTES.filter(p => config.only.includes(p.id)) : ROUTES;
  for (const page of targets) {
    await js(r => { location.hash = '#' + r; }, page.route);
    await waitFor(r => location.hash === '#' + r && !!document.querySelector('main .relative'), page.route, 15000, page.route);
    await pause(700); await settle();
    // Safe state edits: type into the first free-text field, pick the last unselected tab, scroll.
    const edits = await js(() => {
      const done = [];
      const main = document.querySelector('main');
      const field = [...main.querySelectorAll('input[type="text"], input[type="search"], input:not([type]), textarea')]
        .find(el => !el.disabled && !el.readOnly && el.offsetParent);
      if (field) {
        const proto = field.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(field, 'QA-DARK draft');
        field.dispatchEvent(new Event('input', { bubbles: true }));
        done.push('typed:' + (field.id || field.placeholder || field.tagName));
      }
      const tab = [...main.querySelectorAll('[role="tab"][aria-selected="false"]')].filter(el => !el.disabled && el.offsetParent).at(-1);
      if (tab) { tab.click(); done.push('tab:' + (tab.id || tab.textContent.trim())); }
      const scroller = [...main.querySelectorAll('*')].find(el => el.scrollHeight > el.clientHeight + 120 && /(auto|scroll)/.test(getComputedStyle(el).overflowY));
      if (scroller) { scroller.scrollTop = 90; done.push('scroll'); }
      return done;
    });
    await pause(600); await settle();
    let entry, before, dark, after, checks, attempts = 0;
    const retriedFor = [];
    while (true) {
    attempts++;
    before = await readState();
    const countsBefore = await counts();
    await js(() => { Object.keys(window.__qaCounts).forEach(k => { window.__qaCounts[k] = 0; }); window.__qaCounting = true; });
    await setTheme('dark');
    dark = await readState();
    const image = await window.capturePage(undefined, { stayHidden: true });
    fs.writeFileSync(path.join(config.captureDirectory, `${page.id}-after-switch-dark.png`), image.toPNG());
    await setTheme('light');
    after = await readState();
    const created = await js(() => { window.__qaCounting = false; return { ...window.__qaCounts }; });
    const requestDelta = delta(countsBefore, await counts());
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    const background = requestDelta[BACKGROUND_POLL] || 0;
    const themeRequests = Object.fromEntries(Object.entries(requestDelta).filter(([k]) => k !== BACKGROUND_POLL));
    checks = {
      themePainted: dark.theme === 'dark' && after.theme === 'light' && before.theme === 'light',
      shellStable: before.shellId === dark.shellId && dark.shellId === after.shellId,
      rootStable: before.rootId === dark.rootId && dark.rootId === after.rootId,
      chartsStable: same(before.charts, dark.charts) && same(dark.charts, after.charts),
      fieldsStable: same(before.fields, dark.fields) && same(dark.fields, after.fields),
      tabsStable: same(before.tabs, dark.tabs) && same(dark.tabs, after.tabs),
      pressedStable: same(before.pressed, dark.pressed) && same(dark.pressed, after.pressed),
      scrollStable: same(before.scroll, dark.scroll) && same(dark.scroll, after.scroll),
      noRequests: Object.keys(themeRequests).length === 0 && background <= 1,
      noNetworkPrimitives: created.fetch === 0 && created.xhr <= background && created.eventSource === 0 && created.webSocket === 0,
      noNewIntervals: created.setInterval === 0,
      noBoundary: !before.boundary && !dark.boundary && !after.boundary,
    };
    entry = { id: page.id, route: page.route, edits, checks, created, requestDelta, backgroundPoll: background, attempts,
      charts: before.charts.length, fields: before.fields.length, tabs: before.tabs, ok: Object.values(checks).every(Boolean), retriedFor };
    const onlyPollers = Object.keys(requestDelta).length > 0 && Object.keys(requestDelta).every(k => SHELL_POLLERS.has(k))
      && created.fetch === 0 && created.eventSource === 0 && created.webSocket === 0 && created.setInterval === 0;
    if (entry.ok || !onlyPollers || attempts >= 3) break;
    retriedFor.push(requestDelta);
    await pause(1500);
    }
    if (!entry.ok) {
      entry.detail = { before, dark, after };
      failures.push({ id: page.id, failed: Object.entries(checks).filter(([, v]) => !v).map(([k]) => k) });
    }
    pages.push(entry);
  }

  // In-flight synthetic read: Performance refresh, delayed by the fixture,
  // must survive two theme flips and complete exactly once.
  let inflight = null;
  if (!config.only.length || config.only.includes('performance')) {
    await js(() => { location.hash = '#/performance'; });
    await waitFor(() => !!document.querySelector('.bb-page-performance'), 'performance');
    await pause(900); await settle();
    const clickRefresh = () => js(() => {
      const button = document.querySelector('main [data-page="performance"] [data-qa="perf-refresh"]:not(:disabled)');
      if (!button) return false; button.click(); return true;
    });
    const c0 = await counts();
    assert.ok(await clickRefresh(), 'Performance refresh button');
    await pause(1500);
    const discovered = Object.keys(delta(c0, await counts())).filter(k => k.startsWith('GET '));
    assert.ok(discovered.length > 0, 'refresh issues reads');
    const routeKey = discovered[0], route = routeKey.slice(4);
    const body = await (await fetch(config.origin + route)).json();
    await fixture({ setRead: { [route]: { body, delayMs: 4000 } } });
    const c1 = await counts();
    assert.ok(await clickRefresh(), 'Performance refresh button (delayed)');
    await pause(400);
    const busyBefore = await readState();
    await setTheme('dark');
    const busyDark = await readState();
    fs.writeFileSync(path.join(config.captureDirectory, 'performance-inflight-dark.png'), (await window.capturePage(undefined, { stayHidden: true })).toPNG());
    await setTheme('light');
    await setTheme('dark');
    await pause(4500); await settle();
    const done = await readState();
    const d = delta(c1, await counts());
    await fixture({ clearReads: [route] });
    await setTheme('light');
    inflight = { route: routeKey, delta: d[routeKey] || 0, allDeltas: d, busySeen: busyBefore.busy > 0 || busyDark.busy > 0,
      rootStable: busyBefore.rootId === busyDark.rootId && busyDark.rootId === done.rootId, finishedBusy: done.busy };
    if (inflight.delta !== 1 || !inflight.rootStable) failures.push({ id: 'performance-inflight', inflight });
  }

  const result = { ok: failures.length === 0 && blocked.length === 0, pages, failures, inflight, blockedExternal: blocked, consoleErrors };
  console.log(MARK + JSON.stringify(result));
  window.destroy();
  app.exit(0);
}

if (require.main === module) run().catch(error => { console.error(error?.stack || error); process.exitCode = 1; });
module.exports = { renderer };
