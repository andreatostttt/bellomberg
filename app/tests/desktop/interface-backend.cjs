// Opt-in, authenticated renderer check against an ALREADY running local backend.
// Only GET requests are forwarded. Operational POSTs are deliberately refused.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const sessionFile = process.env.BELLOMBERG_INTERFACE_SESSION_FILE;
if (!sessionFile) throw new Error('Set BELLOMBERG_INTERFACE_SESSION_FILE to a private JSON file with a current authenticated token.');
const token = JSON.parse(fs.readFileSync(sessionFile, 'utf8')).token;
assert.equal(typeof token, 'string');
assert.ok(token.length > 0);
const backend = new URL(process.env.BELLOMBERG_INTERFACE_API_URL || 'http://127.0.0.1:8765');
assert.equal(backend.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(backend.hostname), 'session is only sent to a local backend');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-interface-backend-'));
const captureDirectory = process.env.BELLOMBERG_INTERFACE_BACKEND_CAPTURE_DIR
  ? path.resolve(process.env.BELLOMBERG_INTERFACE_BACKEND_CAPTURE_DIR) : temporary;
fs.mkdirSync(captureDirectory, { recursive: true });
const requests = [];
const received = {};
const failures = [];
const allowed = /^(\/health|\/preferences|\/mandato|\/portfolio(?:\/.*)?|\/decisions|\/agents\/(?:list|live)|\/fx|\/tasks\/scheduled|\/market\/(?:ohlc|overview|quote)(?:\/.*)?)$/;

const server = http.createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-BB-Token,X-BB-Language');
  res.setHeader('Content-Type', 'application/json');
  if (req.method === 'OPTIONS') { res.end('{}'); return; }
  const route = new URL(req.url, 'http://127.0.0.1').pathname;
  requests.push({ method: req.method, url: req.url });
  if (req.method !== 'GET') {
    res.statusCode = 409;
    res.end(JSON.stringify({ detail: 'Verifica sicura: operazione intercettata; nessun dato modificato.' }));
    return;
  }
  if (!allowed.test(route)) {
    res.statusCode = 503;
    res.end(JSON.stringify({ detail: 'Endpoint fuori dal perimetro della verifica dashboard: ' + route }));
    return;
  }
  try {
    const response = await fetch(new URL(req.url, backend), {
      headers: { 'X-BB-Token': token, 'X-BB-Language': req.headers['x-bb-language'] || 'it' },
      signal: AbortSignal.timeout(65_000),
    });
    const body = await response.text();
    res.statusCode = response.status;
    if (!response.ok) failures.push({ url: req.url, status: response.status });
    else { try { received[route] = JSON.parse(body); } catch {} }
    res.end(body);
  } catch (error) {
    failures.push({ url: req.url, error: error.message });
    res.statusCode = 502; res.end(JSON.stringify({ detail: error.message }));
  }
});

async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const fixture = path.join(temporary, 'renderer.cjs');
  fs.writeFileSync(fixture, `
    const { app, BrowserWindow } = require('electron');
    const fs = require('node:fs');
    const path = require('node:path');
    app.setPath('userData', path.join(${JSON.stringify(temporary)}, 'user-data'));
    app.disableHardwareAcceleration();
    const finish = result => { console.log('BB_INTERFACE_BACKEND_RESULT ' + JSON.stringify(result)); app.exit(result.ok ? 0 : 1); };
    app.whenReady().then(async () => {
      const w = new BrowserWindow({ width: 1920, height: 1080, show: false,
        webPreferences: { preload: ${JSON.stringify(path.join(root, 'dist-electron/preload.mjs'))},
          sandbox: true, contextIsolation: true, nodeIntegration: false,
          additionalArguments: ['--bellomberg-launch-id=interface-backend-test', '--bellomberg-api-port=${port}'] } });
      const evaluate = source => w.webContents.executeJavaScript(source);
      const waitFor = async (source, label) => {
        for (let i = 0; i < 300; i++) {
          if (await evaluate(source)) return;
          await new Promise(resolve => setTimeout(resolve, 100));
        }
        throw new Error(label + ': route=' + await evaluate('location.hash + "; heading=" + (document.querySelector("h1")?.innerText||"")'));
      };
      const screenshot = async name => { fs.writeFileSync(path.join(${JSON.stringify(captureDirectory)}, name + '.png'), (await w.webContents.capturePage()).toPNG()); };
      const clickText = async text => {
        const success = await evaluate('(()=>{const e=Array.from(document.querySelectorAll("button")).find(b=>b.textContent.trim().toLowerCase()==='+JSON.stringify(text.toLowerCase())+');if(!e)return false;e.click();return true;})()');
        if (!success) throw new Error('Missing button: ' + text);
      };
      await w.loadFile(${JSON.stringify(path.join(root, 'dist/index.html'))}, { hash: '/dashboard' });
      const token = JSON.parse(fs.readFileSync(${JSON.stringify(sessionFile)}, 'utf8')).token;
      await evaluate('localStorage.setItem("bellomberg_token_v1",'+JSON.stringify(token)+');localStorage.setItem("bellomberg_unlocked_v1",JSON.stringify({ts:Date.now()}));localStorage.setItem("bellomberg_last_launch_id","interface-backend-test");localStorage.setItem("bellomberg.lingua","it");localStorage.setItem("bellomberg_alerts_enabled","false");');
      await new Promise(resolve=>{ w.webContents.once('did-finish-load', resolve); w.webContents.reload(); });
      await waitFor('!!document.querySelector(".f1c .blot tbody tr")', 'real Classic portfolio');
      await waitFor('!!document.querySelector(".mkchart canvas")', 'real market chart');
      await waitFor('Array.from(document.querySelectorAll(".ab .k")).some(e=>e.textContent.includes("Beta"))', 'real risk metrics');
      await new Promise(resolve => setTimeout(resolve, 2500));
      const classic = await evaluate('({positions:Array.from(document.querySelectorAll(".f1c .blot tbody tr td.tk")).map(e=>e.textContent),values:Array.from(document.querySelectorAll(".f1c .blot tbody tr")).map(e=>["quantity","day-percent","day-profit","profit","return-percent"].map(field=>e.querySelector("[data-field="+field+"]")?.textContent)),quota:document.querySelector(".navv .v")?.textContent,decisionPanelPresent:!!document.querySelector(".f1c .dec"),decisionCards:document.querySelectorAll(".f1c .dec").length,agents:document.querySelectorAll(".dk").length})');
      await screenshot('classic-real-1920');
      await clickText('Nuova');
      await waitFor('!!document.querySelector(".dashboard-modern") && !!document.querySelector(".mkchart canvas")', 'real new dashboard');
      await new Promise(resolve => setTimeout(resolve, 1000));
      const modern = await evaluate('({positions:Array.from(document.querySelectorAll(".dashboard-modern .blot tbody tr td.tk")).map(e=>(e.querySelector(".modern-ticker-link")||e).textContent),values:Array.from(document.querySelectorAll(".dashboard-modern .blot tbody tr")).map(e=>["quantity","day-percent","day-profit","profit","return-percent"].map(field=>e.querySelector("[data-field="+field+"]")?.textContent)),quota:document.querySelector(".navv .v")?.textContent,decisionPanelPresent:!!document.querySelector(".dashboard-modern .modern-decisions"),dashboardDecisionCards:document.querySelectorAll(".dashboard-modern .dec").length,agents:document.querySelectorAll(".dk").length,heatCells:document.querySelectorAll(".dashboard-modern .modern-heatmap-grid .hcell").length,columns:Array.from(document.querySelectorAll(".dashboard-modern .blot th")).map(e=>({text:e.textContent,display:getComputedStyle(e).display})),bodyWidth:document.body.scrollWidth,viewport:innerWidth})');
      await screenshot('modern-real-1920');
      w.setSize(3440,1440); await new Promise(resolve=>setTimeout(resolve,900)); await screenshot('modern-real-3440');
      for (const size of [[1440,900],[1280,800],[1024,768]]) {
        w.setSize(size[0],size[1]); await new Promise(resolve=>setTimeout(resolve,500)); await screenshot('modern-real-'+size[0]);
      }
      await evaluate('Array.from(document.querySelectorAll(".bb-modern-nav-scroll a")).find(a=>a.getAttribute("href")==="#/decisions")?.click()');
      await waitFor('location.hash==="#/decisions" && /^(Decisions|Decisioni)$/i.test(document.querySelector("h1")?.innerText.trim()||"")', 'real Decisions route remains reachable from the sidebar');
      await waitFor('!!document.querySelector(".space-y-4 .grid")', 'real Decisions page content');
      const decisionRoute = await evaluate('({route:location.hash,heading:document.querySelector("h1")?.innerText.trim()||"",tableRows:document.querySelectorAll(".space-y-4 .grid table tbody tr").length,modernDashboardVisible:!!document.querySelector(".dashboard-modern")})');
      await screenshot('decisions-real-1920');
      await evaluate('Array.from(document.querySelectorAll(".bb-modern-nav-scroll a")).find(a=>a.getAttribute("href")==="#/dashboard")?.click()');
      await waitFor('location.hash==="#/dashboard" && !!document.querySelector(".dashboard-modern")', 'return to Modern dashboard from Decisions route');
      await clickText('Avvia consigliere');
      await waitFor('!!document.querySelector("[role=alertdialog]")', 'real committee confirmation');
      const dialog = await evaluate('({text:document.querySelector("[role=alertdialog]").textContent,cancelFocus:document.activeElement?.classList.contains("no")})');
      await evaluate('document.querySelector(".cfm-btn.no").click()');
      await waitFor('!document.querySelector("[role=alertdialog]")', 'cancel committee');
      await clickText('Aggiorna prezzi');
      await waitFor('document.body.innerText.includes("Verifica sicura: operazione intercettata")', 'price failure visibly handled');
      await clickText('Classica');
      await waitFor('!document.querySelector(".dashboard-modern") && !!document.querySelector(".f1c")', 'return to Classic');
      finish({ok:true,classic,modern,decisionRoute,dialog,screenshots:${JSON.stringify(captureDirectory)}});
    }).catch(error=>finish({ok:false,error:error.message}));
  `);
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.BELLOMBERG_BACKEND_DIR;
  delete env.BELLOMBERG_PYTHON;
  let output = '';
  const child = spawn(require('electron'), [fixture], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { output += chunk; });
  const timeout = setTimeout(() => child.kill(), 140_000);
  const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); });
  clearTimeout(timeout);
  // Full logs and authenticated browser data are ephemeral and private.
  fs.writeFileSync(path.join(temporary, 'output.log'), output, { mode: 0o600 });
  const line = output.split(/\r?\n/).find(value => value.startsWith('BB_INTERFACE_BACKEND_RESULT '));
  assert.ok(line, 'Renderer evidence missing: ' + temporary);
  const result = JSON.parse(line.slice('BB_INTERFACE_BACKEND_RESULT '.length));
  assert.equal(code, 0, JSON.stringify(result));
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(result.classic.positions, result.modern.positions);
  assert.deepEqual(result.classic.values, result.modern.values, 'mode only changes presentation');
  assert.equal(result.classic.quota, result.modern.quota);
  assert.equal(result.modern.positions.length, received['/portfolio'].positions.length);
  assert.equal(result.classic.decisionPanelPresent, true, 'Classic retains its pending-decisions dashboard panel');
  assert.ok(result.classic.decisionCards > 0, 'Classic displays its pending decision rows');
  assert.equal(result.modern.decisionPanelPresent, false, 'Modern omits its pending-decisions dashboard panel');
  assert.equal(result.modern.dashboardDecisionCards, 0, 'Modern has no pending-decision rows in its dashboard DOM');
  assert.match(result.decisionRoute.heading.trim(), /^(Decisions|Decisioni)$/i, 'the standalone Decisions route remains navigable');
  assert.equal(result.decisionRoute.route, '#/decisions');
  assert.equal(result.decisionRoute.modernDashboardVisible, false,
    'the standalone Decisions route renders independently of the dashboard');
  assert.equal(result.classic.agents, received['/agents/list'].agents.length,
    'Classic renders the complete agent list returned by the global API endpoint');
  assert.equal(result.modern.agents, 0, 'Modern omits the AI Desk agent list while the global agents endpoint remains available');
  assert.ok(received['/decisions'].decisions.length > 0, 'global Decisions GET remains available');
  assert.ok(result.modern.columns.every(column => column.display !== 'none'), 'all position columns remain available');
  assert.equal(requests.filter(request => request.method === 'POST' && request.url === '/consigliere/run').length, 0);
  const required = ['/portfolio', '/decisions', '/portfolio/analytics/twr', '/portfolio/analytics/nav_history', '/portfolio/risk', '/agents/list', '/agents/live', '/market/ohlc', '/fx'];
  for (const endpoint of required) assert.ok(received[endpoint], 'Real successful endpoint: ' + endpoint);
  const evidence = {
    backend: backend.origin, successfulEndpoints: required, failures,
    portfolioSource: received['/portfolio'].source ?? null,
    positions: result.modern.positions.length, agents: { globalEndpoint: received['/agents/list'].agents.length,
      classicDashboard: result.classic.agents, modernDashboard: result.modern.agents },
    pendingDecisions: { classicDashboardPanel: result.classic.decisionCards,
      modernDashboardPanel: result.modern.dashboardDecisionCards,
      globalEndpoint: received['/decisions'].decisions.length,
      standaloneRouteTableRows: result.decisionRoute.tableRows }, priceAction: 'intercepted, visible error',
    committeeAction: 'real heartbeat/cost confirmation, cancelled',
    forwardedMutations: 0, screenshots: captureDirectory,
  };
  fs.writeFileSync(path.join(captureDirectory, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; }).finally(() => {
  server.close();
  fs.rmSync(path.join(temporary, 'user-data'), { recursive: true, force: true });
});
