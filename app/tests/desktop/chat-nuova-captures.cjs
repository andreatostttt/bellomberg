// Offline Electron captures of the Nuova Chat at 1920, 2560, 3440 and 5120 px, light and dark:
// empty, streaming (details closed and open), completed (sources, costs), archive and read-only.
// Every response comes from the chat-modes fixture; no backend, model or market API is contacted.
// BB_CHAT_CAPTURE=dir sets the output folder; BB_CHAT_SIZES=1920x1080,2560x1440 narrows the sizes.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '../..');

async function run() {
  const { server } = require('./chat-modes.cjs').startFixture();
  const original = server.listeners('request')[0];
  server.removeAllListeners('request');
  server.on('request', (req, res) => {
    const route = new URL(req.url, 'http://localhost').pathname;
    const send = body => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); };
    if (req.method === 'GET' && route === '/preferences') return send({ language: 'it', selected: true, source: 'preferences' });
    if (req.method === 'GET' && route === '/agents/list') return send({ agents: [
      ['capo', 'Capo', 'Analista senior PM', '#ff9500'], ['macro', 'Macro', 'Stratega macroeconomico', '#ffc760'],
      ['options', 'Options Flow', 'Flussi opzioni', '#a78bfa'], ['quant', 'Quant', 'Analista quantitativo', '#00e5ff'],
      ['fundamentals', 'Fundamentals', 'Analista fondamentale', '#00ff95'], ['crypto', 'Crypto', 'Specialista cripto', '#fbbf24'],
      ['eventdesk', 'Event Desk', 'Eventi (notizie + geopolitica)', '#f472b6'],
    ].map(([id, name, role, color]) => ({ id, name, role, color, model: 'fixture-model' })), engines: { chat: 'fixture-model' } });
    if (req.method === 'GET' && route === '/portfolio') return send({ source: 'synthetic fixture', n_positions: 4, positions: [
      { ticker: 'ZZTEST', nome: 'Zeta Test Synthetic', peso_pct: 24.8, prezzo_live: 202, prev_close: 200 },
      { ticker: 'ACME', nome: 'Acme Synthetic', peso_pct: 32, prezzo_live: 398, prev_close: 400 },
      { ticker: 'ZZBETA.X', nome: 'Beta Holdings Synthetic', peso_pct: 13.3, prezzo_live: 500, prev_close: 498 },
      { ticker: 'SPY', nome: 'SPDR S&P 500', peso_pct: 12.6, prezzo_live: null, prev_close: null },
    ] });
    return original(req, res);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-chat-nuova-'));
  const directory = path.resolve(process.env.BB_CHAT_CAPTURE || path.join(root, '../outputs/chat-captures'));
  fs.mkdirSync(directory, { recursive: true });
  const sizes = (process.env.BB_CHAT_SIZES || '1920x1080,2560x1440,3440x1440,5120x1440').split(',').map(s => s.split('x').map(Number));
  const config = { origin: `http://127.0.0.1:${server.address().port}`, temporary, directory, sizes };
  const entry = path.join(temporary, 'entry.cjs');
  fs.writeFileSync(entry, `require('electron').app.setPath('userData',${JSON.stringify(path.join(temporary, 'userdata'))});require('electron').app.whenReady().then(()=>require(${JSON.stringify(__filename)}).renderer(${JSON.stringify(config)})).catch(e=>{console.error(e);require('electron').app.exit(1)})`);
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(require('electron'), [entry], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = ''; child.stdout.on('data', b => log += b); child.stderr.on('data', b => log += b);
  const timer = setTimeout(() => child.kill(), 600000);
  try {
    const code = await new Promise(r => child.once('close', r));
    fs.writeFileSync(path.join(directory, 'electron.log'), log);
    console.log(log.slice(-3000)); assert.equal(code, 0);
  } finally { clearTimeout(timer); server.closeAllConnections(); server.close(); }
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-chat-modes';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const win = new BrowserWindow({ show: false, width: 1920, height: 1080, useContentSize: true, enableLargerThanScreen: true,
    webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true, sandbox: true, backgroundThrottling: false,
      additionalArguments: ['--bellomberg-launch-id=synthetic-chat-modes', '--bellomberg-api-port=' + new URL(config.origin).port] } });
  win.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*'] }, (d, cb) => cb({ cancel: !d.url.startsWith(config.origin + '/') }));
  const js = (fn, ...args) => win.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const wait = async (fn, label, ...args) => { for (let i = 0; i < 300; i++) { if (await js(fn, ...args)) return; await pause(50); } throw new Error('Timed out: ' + label); };
  const click = (s, i = 0) => js((s, i) => { const el = document.querySelectorAll(s)[i]; if (!el) throw Error('Missing ' + s); el.click(); }, s, i);
  const fill = (s, v) => js((s, v) => { const el = document.querySelector(s); const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
    Object.getOwnPropertyDescriptor(proto.prototype, 'value').set.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true })); }, s, v);
  const fixture = body => fetch(config.origin + '/__fixture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const shot = async name => { await pause(350);
    const overflow = await js(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false, 'horizontal overflow in ' + name);
    fs.writeFileSync(path.join(config.directory, name + '.png'), (await win.webContents.capturePage()).toPNG()); };
  const details = async open => { const now = await js(() => document.querySelector('.chat-inspector-toggle').getAttribute('aria-expanded') === 'true');
    if (now !== open) await click('.chat-inspector-toggle'); await pause(150); };
  try {
    await win.loadURL(config.origin + '/#/chat');
    await js(() => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-chat-fixture-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-chat-modes');
      localStorage.setItem('bellomberg.lingua', 'it');
      localStorage.setItem('bellomberg.chat.details.v1', 'closed');
    });
    for (const theme of ['light', 'dark']) {
      await js(t => localStorage.setItem('bellomberg.interface-theme.v1', t), theme);
      await new Promise(r => { win.webContents.once('did-finish-load', r); win.webContents.reload(); });
      await wait(() => document.querySelectorAll('.f3d .dk').length === 7, 'desks');
      for (const [w, h] of config.sizes) {
        win.setContentSize(w, h); await pause(300);
        const tag = `${theme}-${w}x${h}`;
        await click('.f3d .dk'); await wait(() => !document.querySelector('.f3d .comp textarea')?.disabled, 'composer');
        await wait(() => document.querySelectorAll('.portfolio-question-tickers button').length > 0, 'tickers');
        await shot(`${tag}-1-empty`);
        await fixture({ streamMode: 'held', releaseHeld: false });
        await fill('.f3d .comp textarea', 'Concordi con l’ultima run che dice di ridurre ACME?');
        await click('.f3d .comp .send');
        await wait(() => !!document.querySelector('.f3d .comp .send.stop'), 'stop');
        await wait(() => document.querySelector('.f3d .conv .prose')?.textContent.includes('Fixture stream'), 'stream text');
        await shot(`${tag}-2-streaming`);
        await details(true); await shot(`${tag}-3-streaming-details`);
        await fixture({ releaseHeld: true });
        await wait(() => !document.querySelector('.f3d .comp .send.stop'), 'done');
        await click('.bbn-chat-details-tabs button', 1); await shot(`${tag}-4-completed-sources`);
        await click('.bbn-chat-details-tabs button', 2); await shot(`${tag}-5-completed-costs`);
        await click('.bbn-chat-details-tabs button', 0);
        await details(false); await shot(`${tag}-6-completed`);
        await click('.bbn-chat-archive .bbn-seg button', 1);
        await fill('.f3d .srch input', 'valuation');
        await wait(() => document.querySelectorAll('.f3d .sess').length === 1, 'archive search');
        await click('.f3d .sess');
        await wait(() => !!document.querySelector('.f3d .conv .prose table'), 'archive loaded');
        await shot(`${tag}-7-archive`);
        await fill('.f3d .srch input', '');
        await wait(() => [...document.querySelectorAll('.f3d .sess .ttl')].some(e => e.textContent === 'Legacy fixture discussion'), 'legacy row');
        await js(() => [...document.querySelectorAll('.f3d .sess')].find(e => e.querySelector('.ttl').textContent === 'Legacy fixture discussion').click());
        await wait(() => document.querySelector('.f3d .comp textarea')?.disabled === true && /Legacy session/.test(document.querySelector('.f3d .conv').innerText), 'read only');
        await shot(`${tag}-8-readonly`);
        await click('.bbn-chat-archive .bbn-seg button', 0);
        await click('.f3d button.nuova');
      }
    }
    console.log('CHAT_NUOVA_CAPTURES_OK ' + fs.readdirSync(config.directory).filter(f => f.endsWith('.png')).length);
  } catch (e) { console.error(e); try { fs.writeFileSync(path.join(config.directory, 'failure.png'), (await win.webContents.capturePage()).toPNG()); } catch {} win.destroy(); app.exit(1); return; }
  win.destroy(); app.exit(0);
}
if (require.main === module) run().catch(e => { console.error(e); process.exitCode = 1; });
module.exports = { renderer };
