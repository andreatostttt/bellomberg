// Built Electron renderer against a synthetic HTTP server. No personal DB or provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const proof = path.resolve(process.env.TRADE_IDEA_DESKTOP_PROOF || fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-trade-idea-proof-')));
const MARK = 'TRADE_IDEA_DESKTOP_RESULT ';
const identity = { ticker: 'TEST.MI', name: 'Synthetic Test Company', exchange: 'MIL', currency: 'EUR', status: 'resolved' };
const models = [{ role: 'specialist', model: 'synthetic-specialist', reasoning_effort: 'medium' }, { role: 'capo', model: 'synthetic-chair', reasoning_effort: 'low' }];
const date = '2026-09-27T19:00:00Z';
const completed = (id, origin, destination, language = 'it') => ({
  id, ticker: 'TEST.MI', company_name: identity.name, exchange: 'MIL', currency: 'EUR',
  view_text: origin === 'favorite_note' ? 'My synthetic thesis' : '', view_origin: origin,
  technical_status: 'completed', phase: 'delivery', language, created_at: date, started_at: date,
  finished_at: '2026-09-27T19:10:00Z', usage: { cost_usd: 0, partial: false }, destination,
});
// Legacy saved runs retain their original artifact labels; new admission below is research-only.
const archived = [
  completed('fixture-favorable', 'favorite_note', { kind: 'dcn', decision_id: 9001, reason: 'Synthetic proposal' }),
  completed('fixture-rejected', 'manual', { kind: 'research', decision_id: 9002, reason: 'Synthetic rejection' }, 'en'),
  completed('fixture-incomplete', 'manual', { kind: 'research', decision_id: 9003, reason: 'Insufficient evidence' }),
];
archived[2].technical_status = 'incomplete';
const activeRun = {
  id: 'fixture-running', ticker: 'TEST.MI', company_name: identity.name, exchange: 'MIL', currency: 'EUR',
  view_text: 'My synthetic thesis', view_origin: 'favorite_note', technical_status: 'running', phase: 'red_team',
  language: 'it', created_at: date, started_at: date, usage: { cost_usd: 0, partial: true },
  destination: { kind: 'none', reason: 'Analysis in progress' },
  analysis_mode: 'fundamentals_research_v1', execution_policy: 'trade-idea-research/2',
};
const result = judgment => ({
  ticker: 'TEST.MI', judgment,
  summary: `Synthetic ${judgment} verdict [src: fixture].`,
  pm_view_response: 'The synthetic thesis was challenged [src: fixture].',
  pros: ['Synthetic positive evidence [src: fixture].'], cons: ['Synthetic counterevidence [src: fixture].'],
  risks: ['Synthetic risk [src: fixture].'], catalysts: ['Synthetic catalyst [src: fixture].'],
  invalidation: ['Synthetic invalidation [src: fixture].'],
  data_gaps: judgment === 'incomplete' ? ['Source unavailable [src: fixture].'] : [],
  scenarios: [{ name: 'Base', analysis: 'Synthetic scenario [src: fixture].', evidence_ids: ['fixture-1'] }],
  objections: [{ objection: 'Synthetic objection?', response: 'Synthetic response [src: fixture].', resolved: false, evidence_ids: ['fixture-1'] }],
  history_review: [{ kind: 'decision', id: 9001, response: 'Previous synthetic decision reviewed [src: fixture].' }],
  evidence: [{ id: 'fixture-1', source: 'Synthetic fixture', as_of: '2026-09-27', summary: 'No live provider.' }],
  dossier: [{ key: 'executive', title: 'Executive summary', paragraphs: ['Synthetic dossier [src: fixture].'], evidence_ids: ['fixture-1'], tables: [] }],
  proposal: judgment === 'favorable' ? { ticker: 'TEST.MI', action: 'BUY', eur_amount: null, timing: 'Review', confidence: 'LOW', rationale: 'Synthetic rationale [src: fixture].' } : null,
});

async function runner() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-trade-idea-'));
  fs.mkdirSync(proof, { recursive: true });
  const requests = [];
  let active = false, stopped = false, retried = false, uncertainRetried = false, language = 'it', quoteStale = false, sourcesPartial = false;
  const server = http.createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-BB-Token,X-BB-Language');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,OPTIONS');
    if (req.method === 'OPTIONS') { res.end(); return; }
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : null;
    const url = new URL(req.url, 'http://127.0.0.1');
    const route = url.pathname;
    if (route === '/__fixture') {
      if (body?.language) language = body.language;
      if (body?.quote_stale !== undefined) quoteStale = body.quote_stale;
      if (body?.sources_partial !== undefined) sourcesPartial = body.sources_partial;
      if (body?.claim) { activeRun.technical_status = 'running'; activeRun.phase = 'red_team'; }
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ requests, active, stopped, retried, language })); return;
    }
    requests.push({ method: req.method, route, body, language: req.headers['x-bb-language'] });
    let out = {}, status = 200;
    if (route === '/health') out = { status: 'ok', version: 'synthetic' };
    else if (route === '/auth/status') out = { configured: true, default_pin: false };
    else if (route === '/preferences') {
      if (req.method === 'PUT' && body?.language) language = body.language;
      out = { language, selected: true, source: 'preferences' };
    } else if (route === '/portfolio') out = { positions: [], cash_disponibile_eur: null, cash_source: 'uninitialized' };
    else if (route === '/fx') out = { rates: { EUR: 1 } };
    else if (route === '/tasks/scheduled') out = { tasks: [] };
    // The shell's global price refresh (Layout -> lib/price-refresh) may attempt its pull; the fixture refuses it,
    // as in desktop/trade/f13/f18.
    else if (route === '/prices/update' && req.method === 'POST') { status = 409; out = { detail: 'Synthetic test: automatic price refresh intercepted' }; }
    else if (route === '/db/backups') out = { backups: [], count: 0 };
    else if (route === '/agents/list') out = { agents: [], engines: {} };
    else if (route === '/mandato') out = { dichiarato: true, causa: null, dettaglio: null, campi_mancanti: [], valori: {}, origine: 'synthetic', impronta: 'fixture', campi: [], errori: [], esempio: {} };
    else if (route === '/favorites') out = { favorites: [{ ticker: 'TEST.MI', name: identity.name, note: 'My synthetic thesis' }] };
    else if (route === '/trade-ideas/active') out = { run_id: active ? activeRun.id : null };
    else if (route === '/trade-ideas/preflight' && req.method === 'POST') {
      const ok = body?.ticker === 'TEST.MI' && Number.isFinite(body?.budget_limit_usd) && body.budget_limit_usd > 0;
      out = { ok, identity, models, analysis_mode: 'fundamentals_research_v1', execution_policy: 'trade-idea-research/2',
        authorization: { status: 'ok' }, budget: { status: ok ? 'ok' : 'blocked', limit_usd: body?.budget_limit_usd ?? null, estimated_cost_usd: null,
          model_prices: { specialist: { prompt: '0.00000125', completion: '0.00000425' }, capo: { prompt: '0.000004', completion: '0.00002' } } },
        catalog_snapshot: { checked_at: date, source: 'https://openrouter.ai/api/v1/models', account_access_verified: false },
        valuation: { status: 'not_required', kind: 'company_research' },
        source_qualification: { status: 'research_required', analysis_mode: 'fundamentals_research_v1', fingerprint: 'c'.repeat(64), reasons: [] },
        preparation: { required: false, paid: false, status: 'not_required' }, reasons: ok ? [] : ['Synthetic invalid input'] };
      out.source_qualification.coverage = { sources: { current_quotation: {
        status: quoteStale ? 'stale' : 'verified_observed_price', price: 123.456789, currency: 'EUR',
        observed_at: '2026-09-25T15:30:00Z', acquired_as_of: '2026-09-27', information_cutoff: '2026-09-27',
        source_id: 'synthetic-price-fixture', quote_source_name: 'Synthetic observed quote', exchange: 'Synthetic venue',
        freshness_policy: 'previous_weekday; holidays_not_modelled', comparison_status: 'fx_not_rolled', comparison_qualified: false,
      } } };
      if (sourcesPartial) { out.source_qualification.reasons = ['Synthetic source coverage remains partial']; out.source_qualification.coverage.sources.catalog_warnings = [{source: 'catalog', reason: 'document limit reached; coverage partial'}]; }
      if (quoteStale) { out.ok = false; out.source_qualification.status = 'blocked'; out.reasons = ['Synthetic stale current quotation']; }
    } else if (route === '/trade-ideas/runs' && req.method === 'POST') {
      const researchGrant = body?.authorization?.accepted === true && body?.authorization?.source_fingerprint === 'c'.repeat(64)
        && JSON.stringify(body?.authorization?.activities) === JSON.stringify(['committee']) && body?.authorization?.max_revision_rounds === 0;
      if (body?.cost_acknowledged !== true || !body?.idempotency_key || !researchGrant) { status = 422; out = { detail: 'Missing exact research authorization, cost acknowledgement or idempotency key' }; }
      else { active = true; stopped = false; activeRun.technical_status = 'accepted'; activeRun.phase = 'accepted'; out = { run_id: activeRun.id, status: 'accepted' }; }
    } else if (route === '/trade-ideas/runs') {
      const rows = [...(active || stopped ? [activeRun] : []), ...archived]
        .filter(run => (!url.searchParams.get('ticker') || run.ticker === url.searchParams.get('ticker'))
          && (!url.searchParams.get('status') || run.technical_status === url.searchParams.get('status')));
      out = { runs: rows.slice(0, Number(url.searchParams.get('limit') || 20)), total: rows.length };
    } else {
      const match = /^\/trade-ideas\/runs\/([^/]+)(?:\/(.*))?$/.exec(route);
      if (match) {
        const id = decodeURIComponent(match[1]), suffix = match[2];
        if (suffix === 'workspace') { status = 404; out = { detail: 'This synthetic legacy fixture has no model research workspace' }; }
        else if (suffix === 'stop' && req.method === 'POST') { active = false; stopped = true; out = { run_id: id, status: 'cancelled' }; }
        else if (suffix === 'email/retry' && req.method === 'POST') {
          if (id === 'fixture-incomplete' && body?.acknowledge_uncertain !== true) { status = 409; out = { detail: 'Explicit uncertain acknowledgement required' }; }
          else { if (id === 'fixture-incomplete') uncertainRetried = true; else retried = true; out = { status: 'accepted' }; }
        }
        else if (suffix?.startsWith('artifacts/')) { res.setHeader('Content-Type', 'application/octet-stream'); res.end('Synthetic artifact bytes'); return; }
        else if (!suffix && req.method === 'GET') {
          const run = id === activeRun.id ? activeRun : archived.find(item => item.id === id);
          if (!run) { status = 404; out = { detail: 'Synthetic run missing' }; }
          else {
            const working = id === activeRun.id && active;
            const canceled = id === activeRun.id && stopped;
            const judgment = id === 'fixture-favorable' ? 'favorable' : id === 'fixture-rejected' ? 'rejected' : 'incomplete';
            out = {
              run: canceled ? { ...run, technical_status: 'cancelled', finished_at: date, destination: { kind: 'none', reason: 'Stopped' } } : run,
              progress: { phase: run.phase, updated_at: date, specialists: [{ name: 'Fundamentals', round: 'R1', status: working ? 'running' : 'completed' }, { name: 'Red Team', round: 'R2', status: working ? 'queued' : 'completed' }], tools: [{ name: id === activeRun.id ? 'search_company_sources' : 'get_valuation', specialist: 'Fundamentals', status: 'ok' }], events: [{ at: date, message: 'Synthetic fixture only' }], reports: [{ specialist: 'Fundamentals', round: 1, text: 'Partial archived research survives cancellation.', status: 'completed' }] },
              result: working || canceled ? null : result(judgment),
              artifacts: working || canceled ? [] : id === 'fixture-incomplete' ? [{ id: 'pdf', kind: 'pdf', name: 'partial-research.pdf', status: 'partial', reason: 'Only partial research was available' }, { id: 'excel', kind: 'excel', name: 'model.xlsx', status: 'blocked', reason: 'No verified workbook' }] : [{ id: 'pdf', kind: 'pdf', name: 'research.pdf', status: 'ready' }],
              email: working || canceled ? { status: 'not_attempted' } : id === 'fixture-favorable' ? { status: 'accepted', accepted_at: date } : id === 'fixture-rejected' ? { status: retried ? 'accepted' : 'failed', error: retried ? null : 'Synthetic SMTP failure' } : { status: uncertainRetried ? 'accepted' : 'uncertain', reason: uncertainRetried ? null : 'Synthetic SMTP outcome is uncertain' },
            };
          }
        } else { status = 404; out = { detail: 'Unexpected synthetic route' }; }
      } else { status = 404; out = { detail: 'Unexpected synthetic route ' + route }; }
    }
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(out));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  assert.ok(server.address().port >= 8766);
  let output = '';
  try {
    const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn(require('electron'), [__filename, '--renderer', JSON.stringify({ temporary, origin, proof })], { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout.on('data', chunk => output += chunk); child.stderr.on('data', chunk => output += chunk);
    const timer = setTimeout(() => child.kill(), 65000);
    const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); }); clearTimeout(timer);
    const line = output.split(/\r?\n/).find(value => value.startsWith(MARK));
    assert.ok(line, output);
    const report = JSON.parse(line.slice(MARK.length));
    assert.equal(code, 0, JSON.stringify(report)); assert.equal(report.ok, true, JSON.stringify(report));
    assert.equal(requests.filter(request => request.route === '/trade-ideas/runs' && request.method === 'POST').length, 1);
    assert.ok(requests.filter(request => request.method === 'POST' || request.method === 'PUT').every(request => ['/trade-ideas/preflight', '/trade-ideas/runs', '/trade-ideas/runs/fixture-running/stop', '/trade-ideas/runs/fixture-rejected/email/retry', '/trade-ideas/runs/fixture-incomplete/email/retry', '/preferences'].includes(request.route)
      || (request.method === 'POST' && request.route === '/prices/update')));
    fs.writeFileSync(path.join(proof, 'qa.json'), JSON.stringify({ ...report, requests }, null, 2));
    console.log(JSON.stringify({ ...report, proof, requests: requests.length }));
  } finally {
    fs.writeFileSync(path.join(temporary, 'output.log'), output);
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  app.setPath('userData', path.join(config.temporary, 'userdata')); app.disableHardwareAcceleration();
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-trade-idea'; process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  let window; const scenarios = [], preloadErrors = [], blocked = [];
  const js = (fn, ...args) => window.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const wait = async (fn, label) => { const until = Date.now() + 9000; while (Date.now() < until) { if (await js(fn)) return; await new Promise(resolve => setTimeout(resolve, 50)); } throw Error('Timeout ' + label + ': ' + await js(() => document.body.innerText.slice(-1500))); };
  const click = selector => js(selector => document.querySelector(selector).click(), selector);
  const fill = (selector, value) => js((selector, value) => { const element = document.querySelector(selector); Object.getOwnPropertyDescriptor(element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })); }, selector, value);
  const requestLog = body => new Promise((resolve, reject) => { const request = http.request(config.origin + '/__fixture', { method: 'POST', headers: { 'Content-Type': 'application/json' } }, response => { let raw = ''; response.on('data', part => raw += part); response.on('end', () => resolve(JSON.parse(raw))); }); request.on('error', reject); request.end(JSON.stringify(body || {})); });
  const capture = async (name, selector) => { if (selector) { await js(selector => document.querySelector(selector).scrollIntoView({ block: 'start' }), selector); await new Promise(resolve => setTimeout(resolve, 300)); } await js(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }); fs.writeFileSync(path.join(config.proof, name), (await window.capturePage(undefined, { stayHidden: true })).toPNG()); };
  try {
    await app.whenReady();
    window = new BrowserWindow({ show: false, width: 1500, height: 1080, webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false, additionalArguments: ['--bellomberg-launch-id=synthetic-trade-idea', '--bellomberg-api-port=' + new URL(config.origin).port] } });
    window.webContents.on('preload-error', (_event, _path, error) => preloadErrors.push(String(error)));
    window.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => { const cancel = !details.url.startsWith(config.origin + '/'); if (cancel) blocked.push(details.url); callback({ cancel }); });
    await window.loadFile(path.join(root, 'dist/index.html'), { hash: '/agents/trade-idea?ticker=TEST.MI&source=favorites' });
    await js(() => { localStorage.setItem('bellomberg_token_v1', 'synthetic-token'); localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() })); localStorage.setItem('bellomberg_last_launch_id', 'synthetic-trade-idea'); });
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await wait(() => document.querySelector('.ti-page') && document.querySelector('#ti-view')?.value === 'My synthetic thesis', 'favorite prefill');
    assert.equal(await js(() => document.querySelector('#ti-budget').value), '');
    assert.equal(await js(() => document.querySelector('.ti-launch button').disabled), true);
    assert.match(await js(() => document.querySelector('.ti-form').innerText), /non cambiano la nota/);
    await capture('trade-idea-initial-it.png');
    scenarios.push('saved favorite note copied into optional PM view; no budget default or automatic POST');

    await fill('#ti-budget', '25,00');
    await wait(() => !document.querySelector('.ti-verify').disabled, 'valid locale decimal');
    await click('.ti-verify');
    await wait(() => document.querySelector('.ti-readiness-status.ok'), 'preflight');
    const preflight = (await requestLog()).requests.find(request => request.route === '/trade-ideas/preflight');
    assert.deepEqual(preflight.body, { ticker: 'TEST.MI', pm_view: 'My synthetic thesis', view_source: 'favorite_note', budget_limit_usd: 25 });
    assert.equal(await js(() => document.querySelector('.ti-launch button').disabled), false);
    assert.match(await js(() => [...document.querySelectorAll('.ti-catalog')].map(node => node.innerText).join(' ')), /1,25.*4,25/s);
    assert.match(await js(() => [...document.querySelectorAll('.ti-catalog')].map(node => node.innerText).join(' ')), /n\.d\./);
    assert.match(await js(() => document.querySelector('.ti-observed-quote').innerText), /123,456789 EUR/);
    assert.match(await js(() => document.querySelector('.ti-observed-quote').innerText), /non sono una garanzia di dato intraday/);
    assert.doesNotMatch(await js(() => document.querySelector('.ti-observed-quote').innerText), /cambio corrente verificato/);
    assert.match(await js(() => document.querySelector('.ti-observed-quote').innerText), /festività di borsa non sono modellate/);
    await capture('trade-idea-preflight-it.png');
    await requestLog({ sources_partial: true });
    await click('.ti-verify');
    await wait(() => [...document.querySelectorAll('.ti-catalog')].some(node => node.innerText.includes('Synthetic source coverage remains partial')), 'research source gap label');
    assert.equal(await js(() => document.querySelector('.ti-launch button').disabled), false);
    assert.match(await js(() => document.querySelector('.ti-readiness').innerText), /Analisi societaria · memo e PDF/);
    assert.match(await js(() => document.querySelector('.ti-readiness').innerText), /Nessuna costruzione, compilazione o rigenerazione Excel/);
    assert.doesNotMatch(await js(() => document.querySelector('.ti-readiness').innerText), /Preparazione dell.Excel|ricostruzione dello storico|Valutazione F14/);
    assert.match(await js(() => document.querySelector('.ti-readiness').innerText), /copertura del catalogo resta parziale/);
    await capture('trade-idea-sources-partial-it.png');
    scenarios.push('research admission keeps source gaps explicit and authorizes memo/PDF with no workbook preparation or revision');
    await click('.ti-document-sources summary');
    await click('.ti-document-sources > button');
    await fill('.ti-document-sources input[type=url]', 'https://example.org/synthetic-issuer/report');
    await wait(() => !document.querySelector('.ti-readiness-status.ok'), 'document edit invalidates qualification');
    assert.equal(await js(() => document.querySelector('.ti-launch button').disabled), true);
    await click('.ti-verify');
    await wait(() => document.querySelector('.ti-readiness-status.ok'), 'qualification with PM document');
    const sourcePreflight = (await requestLog()).requests.filter(request => request.route === '/trade-ideas/preflight').at(-1);
    assert.deepEqual(sourcePreflight.body.document_sources, [{url:'https://example.org/synthetic-issuer/report'}]);
    await capture('trade-idea-pm-source-it.png', '.ti-document-sources');
    await click('.ti-launch button');
    await wait(() => document.querySelector('.cfm-modal'), 'confirmation dialog');
    await wait(() => document.activeElement?.classList.contains('no'), 'safe keyboard focus');
    assert.match(await js(() => document.querySelector('.cfm-rows').innerText), /25,00/);
    await click('.cfm-btn.no');
    assert.equal((await requestLog()).requests.filter(request => request.route === '/trade-ideas/runs' && request.method === 'POST').length, 0);
    await click('.ti-launch button'); await click('.cfm-btn.go');
    await wait(() => document.querySelector('.ti-detail')?.innerText.includes('fixture-running'), 'running detail');
    assert.match(await js(() => document.querySelector('.ti-chip').textContent), /In coda/);
    assert.ok(await js(() => document.querySelector('.ti-detail-actions .ti-danger')));
    const starts = (await requestLog()).requests.filter(request => request.route === '/trade-ideas/runs' && request.method === 'POST');
    assert.deepEqual(starts[0].body.document_sources, sourcePreflight.body.document_sources);
    assert.equal(starts.length, 1); assert.equal(starts[0].body.cost_acknowledged, true); assert.equal(starts[0].body.budget_limit_usd, 25); assert.ok(starts[0].body.idempotency_key);
    assert.deepEqual(starts[0].body.authorization, { accepted: true, source_fingerprint: 'c'.repeat(64), activities: ['committee'], max_revision_rounds: 0 });
    assert.deepEqual(Object.keys(starts[0].body).sort(), ['authorization', 'budget_limit_usd', 'cost_acknowledged', 'document_sources', 'idempotency_key', 'pm_view', 'ticker', 'view_source']);
    await requestLog({ claim: true }); await click('.ti-detail-toolbar button');
    await wait(() => document.querySelector('.ti-chip')?.textContent.includes('In corso'), 'claimed running state');
    await capture('trade-idea-running-it.png', '.ti-history-detail');
    scenarios.push('preflight carries exact ticker/view/origin/25 USD and source prices; cancel causes no start; accepted then running remain stoppable');

    await click('.ti-detail-actions .ti-danger'); await wait(() => document.querySelector('.cfm-modal'), 'stop confirmation'); await click('.cfm-btn.go');
    await wait(() => document.querySelector('.ti-chip')?.textContent.includes('Annullata'), 'stopped detail');
    assert.equal((await requestLog()).requests.filter(request => request.route.endsWith('/stop')).length, 1);
    await click('.ti-report summary');
    assert.match(await js(() => document.querySelector('.ti-report').innerText), /Partial archived research survives cancellation/);
    scenarios.push('stop reaches endpoint; canceled run retains readable partial research without a verdict');

    await js(() => [...document.querySelectorAll('.ti-history-item')].find(item => item.innerText.includes('fixture-favorable') || item.innerText.includes('TEST.MI') && item.innerText.includes('Analisi conclusa')).click());
    await wait(() => document.querySelector('.ti-verdict-favorable'), 'favorable detail');
    assert.match(await js(() => document.querySelector('.ti-destination').innerText), /DCN/);
    assert.match(await js(() => document.querySelector('.ti-destination a').getAttribute('href')), /decision=9001/);
    assert.equal(await js(() => document.querySelector('.ti-email').textContent.includes('server SMTP')), true);
    assert.equal(await js(() => !!document.querySelector('.ti-delivery-grid button.ti-primary')), false);
    await capture('trade-idea-favorable-it.png', '.ti-history-detail');
    scenarios.push('favorable result, actionable destination link and SMTP acceptance wording');

    await js(() => [...document.querySelectorAll('.ti-history-item')].find(item => item.innerText.includes('fixture-rejected') || item.innerText.includes('In ricerca') && !item.classList.contains('selected')).click());
    await wait(() => document.querySelector('.ti-verdict-rejected'), 'rejected detail');
    assert.ok(await js(() => document.querySelector('.ti-delivery-grid button.ti-primary')));
    await click('.ti-delivery-grid button.ti-primary');
    await wait(() => document.querySelector('.ti-email')?.textContent.includes('server SMTP'), 'email retry accepted');
    assert.equal((await requestLog()).requests.filter(request => request.route.endsWith('/email/retry')).length, 1);
    scenarios.push('failed email retry sends the original package without a new analysis');

    await js(() => [...document.querySelectorAll('.ti-history-item')].find(item => item.innerText.includes('fixture-incomplete') || item.innerText.includes('In ricerca') && !item.classList.contains('selected')).click());
    await wait(() => document.querySelector('.ti-verdict-incomplete'), 'incomplete detail');
    assert.match(await js(() => document.querySelector('.ti-artifact').innerText), /parziale|partial/i);
    assert.match(await js(() => document.querySelector('.ti-artifact').innerText), /Only partial research was available/);
    assert.equal(await js(() => document.querySelectorAll('.ti-artifact button').length), 1);
    assert.equal(await js(() => !!document.querySelector('.ti-delivery-grid button.ti-primary')), true);
    await click('.ti-delivery-grid button.ti-primary');
    await wait(() => document.querySelector('.cfm-modal'), 'uncertain resend warning');
    assert.match(await js(() => document.querySelector('.cfm-modal').innerText), /due volte/);
    await click('.cfm-btn.no');
    assert.equal((await requestLog()).requests.filter(request => request.route.endsWith('/email/retry')).length, 1);
    await click('.ti-delivery-grid button.ti-primary');
    await click('.cfm-btn.go');
    await wait(() => document.querySelector('.ti-email')?.textContent.includes('server SMTP'), 'explicit uncertain resend');
    const uncertainRetry = (await requestLog()).requests.filter(request => request.route.endsWith('/email/retry'))[1];
    assert.equal(uncertainRetry.body.acknowledge_uncertain, true);
    await capture('trade-idea-incomplete-it.png', '.ti-history-detail');
    await capture('trade-idea-delivery-it.png', '.ti-delivery-grid');
    scenarios.push('partial PDF remains downloadable; uncertain resend requires separate warning and explicit acknowledgement');

    await requestLog({ language: 'en' });
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await wait(() => document.querySelector('.ti-page') && document.documentElement.lang === 'en', 'English UI');
    assert.match(await js(() => document.querySelector('.ti-hero h1').textContent), /Trade Idea/);
    await capture('trade-idea-english.png', '.ti-history-detail');
    await fill('#ti-budget', '25.00');
    await click('.ti-verify');
    await wait(() => document.querySelector('.ti-readiness-status.ok'), 'English observed quote');
    assert.match(await js(() => document.querySelector('.ti-observed-quote').innerText), /Observed market quote.*123\.456789 EUR/s);
    assert.match(await js(() => document.querySelector('.ti-observed-quote').innerText), /exchange holidays are not modelled/);
    await capture('trade-idea-observed-quote-en.png', '.ti-observed-quote');
    await requestLog({ quote_stale: true });
    await click('.ti-verify');
    await wait(() => document.querySelector('.ti-readiness-status.ko'), 'stale quote blocks launch');
    assert.equal(await js(() => document.querySelector('.ti-launch button').disabled), true);
    assert.match(await js(() => document.querySelector('.ti-observed-quote').innerText), /Current quote is unverified/);
    assert.doesNotMatch(await js(() => document.querySelector('.ti-observed-quote').innerText), /123\.456789 EUR/);
    await capture('trade-idea-stale-quote-en.png', '.ti-observed-quote');
    scenarios.push('research quote declares date and holiday limits; explicit server preflight refusal blocks launch and hides the unqualified price');
    for (const width of [1500, 900, 720]) { window.setSize(width, 1080); await new Promise(resolve => setTimeout(resolve, 120)); assert.equal(await js(() => document.documentElement.scrollWidth <= innerWidth), true, `horizontal overflow at ${width}`); }
    scenarios.push('English controls and responsive widths 1500/900/720 without page overflow');
    assert.deepEqual(preloadErrors, []);
    assert.ok(blocked.every(url => url.startsWith('https://fonts.googleapis.com/')), JSON.stringify(blocked));
    const preferences = window.webContents.getLastWebPreferences(); assert.ok(preferences.sandbox && preferences.contextIsolation && !preferences.nodeIntegration);
    console.log(MARK + JSON.stringify({ ok: true, scenarios, captures: fs.readdirSync(config.proof).filter(name => name.endsWith('.png')) }));
    app.exit(0);
  } catch (error) {
    console.log(MARK + JSON.stringify({ ok: false, error: String(error), scenarios, preloadErrors, blocked, page: window ? await js(() => document.body.innerText.slice(-2000)).catch(String) : null }));
    app.exit(1);
  }
}

if (process.argv.includes('--renderer')) renderer(JSON.parse(process.argv[process.argv.indexOf('--renderer') + 1]));
else runner().catch(error => { console.error(error); process.exitCode = 1; });
