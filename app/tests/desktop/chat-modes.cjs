// Isolated Chat / Classic-vs-Modern Electron integration fixture.
// Every app response comes from this ephemeral HTTP server. No Python backend,
// real LLM, private portfolio, credential, or operational API is contacted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const MARK = 'CHAT_MODES_RESULT ';
const HANDSHAKE = 'CHAT_MODES_FIXTURE ';
const FIXTURE_TOKEN = 'synthetic-chat-fixture-token';
// Optional dark-mode QA overrides (unset = previous behaviour):
// BELLOMBERG_CHAT_THEME=dark (Nuova Scuro), BELLOMBERG_CHAT_DIST=dist-dir, BELLOMBERG_CHAT_CAPTURE_DIR=dir.
const DIST_DIR = process.env.BELLOMBERG_CHAT_DIST || 'dist';
const QA_THEME = process.env.BELLOMBERG_CHAT_THEME === 'dark' ? 'dark' : 'light';
const VIEWPORTS = [[1920, 1080], [2560, 1440], [3440, 1440], [5120, 1440], [1280, 800], [1160, 800], [900, 700]];

function fixtureAgents() {
  return [
    { id: 'capo', name: 'Synthetic Lead', role: 'Portfolio lead', color: '#1455ff', model: 'fixture-model' },
    { id: 'macro', name: 'Synthetic Macro', role: 'Macro analyst', color: '#008f63', model: 'fixture-model' },
    { id: 'options', name: 'Synthetic Options', role: 'Options analyst', color: '#8b5cf6', model: 'fixture-model' },
    { id: 'quant', name: 'Synthetic Quant', role: 'Quantitative analyst', color: '#c52943', model: 'fixture-model' },
    { id: 'fundamentals', name: 'Synthetic Fundamentals', role: 'Company analyst', color: '#d97706', model: 'fixture-model' },
    { id: 'crypto', name: 'Synthetic Crypto', role: 'Digital assets', color: '#0891b2', model: 'fixture-model' },
    { id: 'eventdesk', name: 'Synthetic Events', role: 'Event analyst', color: '#64748b', model: 'fixture-model' },
  ];
}

function longMarkdown() {
  const paragraph = 'This historical answer is deliberately long enough to exercise wrapping, reading width, and independent conversation scrolling across desktop, ultrawide, and compact windows. The values below are synthetic and do not describe a real portfolio or issuer.';
  return [
    '## Synthetic archived analysis',
    ...Array.from({ length: 14 }, (_, i) => `### Evidence section ${i + 1}\n\n${paragraph} **Scenario ${i + 1}** remains labeled as fixture data. A source boundary stays explicit, and the paragraph can wrap without widening the conversation rail.`),
    ['| Fixture field | Value | Meaning |', '|:--|--:|:--|', '| Tokens | 1,024 | Historical synthetic measure |', '| Cost | n.d. | No cost supplied for this archived row |'].join('\n'),
    '> This content exists solely to test long-message reading and layout.',
  ].join('\n\n');
}

function longToolPreview() {
  return JSON.stringify({
    fixture: 'synthetic-chat-modes',
    as_of: '2026-09-29T12:00:00Z',
    base_currency: 'EUR',
    positions: [
      { ticker: 'ZZTEST', quantity: 43, market_value_eur: 9524.62, weight_pct: 24.8, return_ytd_pct: 8.1, sector: 'technology', source: 'synthetic_fixture' },
      { ticker: 'ACME', quantity: 18, market_value_eur: 8438.76, weight_pct: 22.0, return_ytd_pct: 6.4, sector: 'technology', source: 'synthetic_fixture' },
      { ticker: 'ZZBETA.X', quantity: 12, market_value_eur: 5101.20, weight_pct: 13.3, return_ytd_pct: 3.2, sector: 'financials', source: 'synthetic_fixture' },
      { ticker: 'SPY', quantity: 9, market_value_eur: 4837.50, weight_pct: 12.6, return_ytd_pct: 5.7, sector: 'broad_market', source: 'synthetic_fixture' },
    ],
    reconciliation: {
      holdings_count: 4,
      observed_total_eur: 27902.08,
      prior_total_eur: 27418.33,
      daily_change_eur: 184.57,
      excluded_cash_eur: 10000,
      cached_fields: ['quantity', 'market_value_eur', 'weight_pct', 'return_ytd_pct'],
      source_boundary: 'Only deterministic synthetic fixture rows; no account or market API was contacted.',
      preview_end_marker: 'PREVIEW END: every value above is synthetic.',
    },
  }, null, 2);
}

function makeSessions() {
  const stamp = '2026-09-29T12:00:00Z';
  return new Map([
    [101, { id: 101, specialist: 'capo', title: 'Synthetic archived valuation discussion', started_at: stamp,
      last_activity: stamp, msg_count: 2, messages: [
        { id: 1001, role: 'user', content: 'Show the synthetic long-form archive state.', timestamp: stamp },
        { id: 1002, role: 'assistant', content: longMarkdown(), timestamp: stamp, tokens_in: 1024, tokens_out: 512 },
      ] }],
    [901, { id: 901, specialist: 'politics', title: 'Legacy fixture discussion', started_at: stamp,
      last_activity: stamp, msg_count: 2, messages: [
        { id: 9011, role: 'user', content: 'Legacy synthetic request.', timestamp: stamp },
        { id: 9012, role: 'assistant', content: 'Legacy session is read-only synthetic fixture content.', timestamp: stamp, tokens_in: null, tokens_out: 0 },
      ] }],
    [902, { id: 902, specialist: 'news', title: 'Legacy fixture news thread', started_at: stamp,
      last_activity: stamp, msg_count: 1, messages: [
        { id: 9021, role: 'assistant', content: 'Retired news specialist, fixture only.', timestamp: stamp, tokens_in: null, tokens_out: null },
      ] }],
  ]);
}

function startFixture() {
  const requests = [];
  const sessions = makeSessions();
  const state = { nextId: 1000, nextStreamMode: 'full', releaseHeld: false, heldWaiters: [], abortedStreams: 0,
    unexpectedWrites: [], unexpectedGets: [], streamCount: 0 };
  const positions = [
    { ticker: 'ZZTEST' }, { ticker: 'ACME' }, { ticker: 'ZZBETA.X' }, { ticker: 'SPY' },
    { ticker: 'ACME' },
  ];
  const sendJson = (res, status, body) => {
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.end(JSON.stringify(body));
  };
  const readBody = async req => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    if (!raw) return null;
    try { return JSON.parse(raw); } catch { return { invalidJson: raw.slice(0, 120) }; }
  };
  const sortedSessions = agent => [...sessions.values()].filter(s => s.specialist === agent)
    .map(({ messages, ...summary }) => summary)
    .sort((a, b) => String(b.last_activity).localeCompare(String(a.last_activity)));
  const streamEvent = (res, name, body) => res.write(`event: ${name}\ndata: ${JSON.stringify(body)}\n\n`);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const route = url.pathname;
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-BB-Token,X-BB-Language');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
    if (req.method === 'OPTIONS') { res.statusCode = 204; res.end(); return; }

    if (route === '/__fixture') {
      const input = req.method === 'POST' ? await readBody(req) : null;
      if (input?.streamMode) state.nextStreamMode = String(input.streamMode);
      if (input && Object.hasOwn(input, 'releaseHeld')) {
        state.releaseHeld = !!input.releaseHeld;
        if (state.releaseHeld) for (const release of state.heldWaiters.splice(0)) release();
      }
      if (input?.restoreArchive) sessions.set(101, makeSessions().get(101));
      if (req.method === 'POST' && input?.resetStreamMode) state.nextStreamMode = 'full';
      sendJson(res, 200, { requests: requests.map(item => ({ ...item })), streamCount: state.streamCount,
        abortedStreams: state.abortedStreams, held: state.heldWaiters.length > 0,
        unexpectedWrites: [...state.unexpectedWrites], unexpectedGets: [...state.unexpectedGets],
        remainingSessionIds: [...sessions.keys()] });
      return;
    }

    if (req.method === 'GET' && route === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      // Keep the Electron fixture wholly offline: use the bundled page and
      // remove its optional Google Fonts hints/stylesheet before serving it.
      const html = fs.readFileSync(path.join(root, DIST_DIR, 'index.html'), 'utf8')
        .replace(/<link rel="preconnect" href="https:\/\/fonts\.googleapis\.com">\s*/g, '')
        .replace(/<link rel="preconnect" href="https:\/\/fonts\.gstatic\.com"[^>]*>\s*/g, '')
        .replace(/<link href="https:\/\/fonts\.googleapis\.com\/css[^"]*" rel="stylesheet">\s*/g, '')
        .replace(/ https:\/\/fonts\.googleapis\.com/g, '')
        .replace(/ https:\/\/fonts\.gstatic\.com/g, '');
      res.end(html);
      return;
    }
    if (req.method === 'GET' && route.startsWith('/assets/')) {
      const assetRoot = path.resolve(root, DIST_DIR, 'assets');
      const assetPath = path.resolve(root, DIST_DIR, decodeURIComponent(route.slice(1)));
      if (!assetPath.startsWith(assetRoot + path.sep)) { sendJson(res, 403, { detail: 'Forbidden' }); return; }
      let asset;
      try { asset = fs.readFileSync(assetPath); } catch { sendJson(res, 404, { detail: 'Asset not found' }); return; }
      const ext = path.extname(assetPath);
      res.setHeader('Content-Type', ext === '.js' ? 'text/javascript; charset=utf-8'
        : ext === '.css' ? 'text/css; charset=utf-8' : ext === '.svg' ? 'image/svg+xml'
          : ext === '.woff2' ? 'font/woff2' : 'application/octet-stream');
      res.setHeader('Cache-Control', 'no-store'); res.end(asset); return;
    }

    const input = await readBody(req);
    const hasToken = req.headers['x-bb-token'] === FIXTURE_TOKEN;
    const isFixtureMutation = (req.method === 'POST' && (route === '/prices/update' || route === '/chat/sessions' || /^\/chat\/sessions\/\d+\/stream$/.test(route)))
      || (req.method === 'PUT' && route === '/preferences')
      || (req.method === 'DELETE' && /^\/chat\/sessions\/\d+$/.test(route));
    const record = { method: req.method, route, query: Object.fromEntries(url.searchParams), input,
      hasSyntheticToken: hasToken, tokenPresent: typeof req.headers['x-bb-token'] === 'string',
      language: req.headers['x-bb-language'] || null };
    requests.push(record);

    if (!['GET', 'POST', 'PUT', 'DELETE'].includes(req.method)) {
      if (req.method !== 'OPTIONS') state.unexpectedWrites.push(`${req.method} ${route}`);
      sendJson(res, 405, { detail: 'Method blocked by Chat fixture' }); return;
    }
    if (req.method !== 'GET' && !isFixtureMutation) {
      state.unexpectedWrites.push(`${req.method} ${route}`);
      sendJson(res, 403, { detail: 'Non-Chat write blocked by Chat fixture' }); return;
    }
    if (isFixtureMutation && !hasToken) { sendJson(res, 401, { detail: 'Fixture token missing' }); return; }

    if (req.method === 'POST' && route === '/prices/update') {
      sendJson(res, 200, { ok: true, source: 'synthetic fixture', updated: 0 }); return;
    }
    if (req.method === 'PUT' && route === '/preferences') {
      sendJson(res, 200, { language: input?.language === 'it' ? 'it' : 'en', selected: true, source: 'preferences' }); return;
    }

    if (req.method === 'POST' && route === '/chat/sessions') {
      const id = state.nextId++;
      const stamp = new Date().toISOString();
      const session = { id, specialist: input?.agent_id || 'capo', title: input?.title || 'Synthetic new conversation',
        started_at: stamp, last_activity: stamp, msg_count: 0, messages: [] };
      sessions.set(id, session);
      sendJson(res, 200, { session_id: id, agent_id: session.specialist, agent_name: 'Synthetic agent', title: session.title }); return;
    }
    if (req.method === 'DELETE' && /^\/chat\/sessions\/\d+$/.test(route)) {
      const id = Number(route.split('/').at(-1));
      sessions.delete(id);
      sendJson(res, 200, { ok: true, deleted: id }); return;
    }
    const streamMatch = route.match(/^\/chat\/sessions\/(\d+)\/stream$/);
    if (req.method === 'POST' && streamMatch) {
      state.streamCount++;
      const mode = state.nextStreamMode;
      state.nextStreamMode = 'full';
      res.statusCode = 200;
      res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
      res.setHeader('Cache-Control', 'no-cache, no-transform');
      res.setHeader('Connection', 'keep-alive');
      res.flushHeaders?.();
      let aborted = false;
      res.on('close', () => { if (!res.writableEnded && !aborted) { aborted = true; state.abortedStreams++; } });
      streamEvent(res, 'meta', { model: 'fixture-model', n_tools_available: 4, max_tool_iterations: 3, output_language: 'en' });
      if (mode !== 'held' && mode !== 'abort') await pause(20);
      if (mode === 'held' || mode === 'abort') {
        streamEvent(res, 'delta', { text: 'Fixture stream is waiting for the mode or abort check. ' });
        if (mode === 'abort') {
          await new Promise(resolve => { const timer = setTimeout(resolve, 25000); res.once('close', () => { clearTimeout(timer); resolve(); }); });
          if (res.writableEnded || aborted) return;
        } else {
          await new Promise(resolve => {
            if (state.releaseHeld) { resolve(); return; }
            state.heldWaiters.push(resolve);
            const timer = setTimeout(() => { state.heldWaiters = state.heldWaiters.filter(x => x !== resolve); resolve(); }, 20000);
            const original = resolve;
            const wrapped = () => { clearTimeout(timer); original(); };
            const at = state.heldWaiters.indexOf(resolve);
            if (at >= 0) state.heldWaiters[at] = wrapped;
          });
          if (res.destroyed || aborted) return;
          streamEvent(res, 'delta', { text: 'The same request continued after the presentation changed. ' });
        }
      } else {
        streamEvent(res, 'delta', { text: 'Synthetic answer. ' });
      }
      if (mode === 'error') {
        streamEvent(res, 'error', { message: 'Synthetic stream error for integration QA.' });
        res.end(); return;
      }
      if (mode === 'incomplete') {
        streamEvent(res, 'delta', { text: 'The fixture closes without a terminal event.' });
        res.end(); return;
      }
      if (mode === 'no-call') {
        streamEvent(res, 'delta', { text: 'The fixture declares that the model was not called.' });
        streamEvent(res, 'done', { ok: false, session_id: Number(streamMatch[1]), model: 'fixture-model', iterations: 0,
          tokens_in: 0, tokens_out: 0, tokens_status: 'completo', tokens_missing: [],
          cost_eur: { cost: 0, status: 'nessuna_chiamata', model: 'fixture-model', breakdown: null,
            fx_rate: null, fx_source: null, cache_ttl: 3600, cache_fields: 'assenti', nota: 'Synthetic fixture: no model call was made.' } });
        res.end(); return;
      }
      if (!['abort'].includes(mode)) {
        streamEvent(res, 'tool_use_start', { tool_id: 'fixture-tool-1', tool_name: 'fixture_portfolio_read', iteration: 1 });
        streamEvent(res, 'delta', { text: 'The result includes a [src: fixture_portfolio_read] reference.' });
        const preview = longToolPreview();
        streamEvent(res, 'tool_result', { tool_id: 'fixture-tool-1', ok: true, result_size_bytes: Buffer.byteLength(preview),
          result_preview: preview });
        const metrics = mode === 'partial'
          ? { tokens_in: null, tokens_out: 0, tokens_status: 'parziale', tokens_missing: ['in', 'cache_read', 'cache_write'],
            cost_eur: { cost: null, status: 'non_calcolato: cache usage missing', model: 'fixture-model', breakdown: null,
              fx_rate: null, fx_source: null, cache_ttl: null, cache_fields: 'assenti', nota: 'Synthetic partial fixture.' } }
          : mode === 'zero'
            ? { tokens_in: 0, tokens_out: 0, tokens_status: 'completo', tokens_missing: [],
              cost_eur: { cost: 0, status: 'ok', model: 'fixture-model', breakdown: { input: 0, output: 0, total: 0 },
                fx_rate: 1, fx_source: 'synthetic', cache_ttl: 3600, cache_fields: 'ok', nota: null } }
            : { tokens_in: 1234, tokens_out: 567, tokens_status: 'completo', tokens_missing: [],
              cost_eur: { cost: 0.012345, status: 'ok', model: 'fixture-model', breakdown: { input: 0.007, output: 0.005345, total: 0.012345 },
                fx_rate: 1.08, fx_source: 'synthetic', cache_ttl: 3600, cache_fields: 'ok', nota: null } };
        streamEvent(res, 'done', { ok: true, session_id: Number(streamMatch[1]), model: 'fixture-model', iterations: 1, ...metrics });
      }
      res.end(); return;
    }

    if (req.method === 'GET') {
      let body;
      if (route === '/health') body = { status: 'ok', brand: 'Synthetic Chat Fixture', version: 'chat-modes-test' };
      else if (route === '/auth/status') body = { configured: true, default_pin: false };
      else if (route === '/agents/list') {
        body = { agents: fixtureAgents(), engines: { chat: 'fixture-model', committee_r1_r2: 'fixture-engine' } };
      }
      else if (route === '/agents/live') body = { running: false, heartbeat: 'ok', specialist_status: {}, usage_total: { cost_eur: null } };
      else if (route === '/portfolio') body = { source: 'synthetic fixture', n_positions: positions.length, positions,
        totale_valore_mercato_eur: 123456.78, cash_disponibile_eur: 10000, timestamp: '2026-09-29T12:00:00Z' };
      else if (route === '/mandato') body = { dichiarato: true, causa: null, dettaglio: null, campi: {}, campi_mancanti: [], valori: {}, origine: 'synthetic fixture', impronta: null, dichiarato_il: null, errori: [] };
      else if (route === '/preferences') body = { language: 'en', selected: true, source: 'preferences' };
      else if (route === '/tasks/scheduled') body = { tasks: [] };
      else if (route === '/fx') body = { rates: { EUR: 1, USD: 0.92, GBP: 1.18 } };
      else if (route === '/news/alerts/unnotified') body = { count: 0, items: [] };
      else if (/^\/chat\/[^/]+\/sessions$/.test(route)) body = { sessions: sortedSessions(route.split('/')[2]) };
      else if ((/^\/chat\/sessions\/\d+$/.test(route))) {
        const session = sessions.get(Number(route.split('/').at(-1)));
        if (!session) { sendJson(res, 404, { detail: 'Synthetic session not found' }); return; }
        body = session;
      }
      else {
        state.unexpectedGets.push(`${req.method} ${route}`);
        // Other shell widgets can request read-only resources during boot. They
        // receive harmless empty synthetic JSON and cannot reach any host.
        body = {};
      }
      sendJson(res, 200, body); return;
    }
    sendJson(res, 404, { detail: 'Unexpected synthetic route' });
  });
  return { server, requests, state };
}

function runner() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-chat-modes-'));
  const captureDirectory = process.env.BELLOMBERG_CHAT_CAPTURE_DIR
    ? path.resolve(process.env.BELLOMBERG_CHAT_CAPTURE_DIR)
    : path.join(root, '..', 'outputs', 'chat-modern');
  fs.mkdirSync(captureDirectory, { recursive: true });
  const { server, requests, state } = startFixture();
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', async () => {
      const origin = `http://127.0.0.1:${server.address().port}`;
      const fixture = path.join(temporary, 'renderer-entry.cjs');
      const handshakePath = path.join(temporary, 'renderer-handshake.json');
      const tracePath = path.join(temporary, 'renderer-trace.log');
      fs.writeFileSync(fixture, `
        const fs = require('node:fs');
        const { app } = require('electron');
        const config = ${JSON.stringify({ temporary, captureDirectory, origin, handshakePath, tracePath,
          baseline: process.env.BELLOMBERG_CHAT_BASELINE === '1', theme: QA_THEME,
          modeWaitMs: process.env.BELLOMBERG_CHAT_BASELINE ? 2500 : 12000 })};
        const trace = (stage, extra = {}) => { const item = { stage, pid: process.pid, ready: app.isReady(), ...extra }; fs.appendFileSync(config.tracePath, JSON.stringify(item) + '\\n'); console.log(${JSON.stringify(HANDSHAKE)} + JSON.stringify(item)); };
        app.setPath('userData', require('node:path').join(config.temporary, 'userdata'));
        app.disableHardwareAcceleration();
        fs.writeFileSync(config.handshakePath, JSON.stringify({ pid: process.pid, entry: __filename, userData: app.getPath('userData'), origin: config.origin }));
        trace('entry', { entry: __filename, origin: config.origin });
        app.whenReady().then(() => require(${JSON.stringify(__filename)}).renderer(config))
          .catch(error => { console.error('CHAT_MODES_FIXTURE_ERROR ' + (error?.stack || error)); app.exit(1); });
      `, { mode: 0o600 });
      let output = '';
      let child;
      try {
        const env = { ...process.env };
        delete env.ELECTRON_RUN_AS_NODE;
        delete env.BELLOMBERG_BACKEND_DIR;
        delete env.BELLOMBERG_PYTHON;
        child = spawn(require('electron'), [fixture], {
          cwd: temporary, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
        });
        child.stdout.on('data', chunk => { output += chunk; });
        child.stderr.on('data', chunk => { output += chunk; });
        const timer = setTimeout(() => child.kill(), 180000);
        let code, signal;
        try {
          ({ code, signal } = await new Promise((done, fail) => {
            child.once('error', fail);
            child.once('close', (exitCode, closeSignal) => done({ code: exitCode, signal: closeSignal }));
          }));
        } finally { clearTimeout(timer); }
        const fixtureState = { requests: requests.map(item => ({ ...item })), streamCount: state.streamCount,
          abortedStreams: state.abortedStreams, unexpectedWrites: state.unexpectedWrites, unexpectedGets: state.unexpectedGets };
        fs.writeFileSync(path.join(temporary, 'electron.log'), output, { mode: 0o600 });
        fs.writeFileSync(path.join(temporary, 'requests.json'), JSON.stringify(fixtureState, null, 2), { mode: 0o600 });
        const handshakeLine = output.split(/\r?\n/).find(item => item.startsWith(HANDSHAKE));
        assert.ok(handshakeLine && fs.existsSync(handshakePath), `Temporary Electron entry did not start. evidence=${temporary}\n${output.slice(-5000)}`);
        const handshake = JSON.parse(fs.readFileSync(handshakePath, 'utf8'));
        assert.equal(handshake.pid, child.pid, 'isolated Electron child started the fixture entry');
        assert.equal(fs.realpathSync(handshake.entry), fs.realpathSync(fixture), 'Electron executed the temporary harness entry');
        assert.equal(handshake.userData, path.join(temporary, 'userdata'), 'Electron uses temporary userData');
        const line = output.split(/\r?\n/).find(item => item.startsWith(MARK));
        assert.ok(line, `Electron Chat smoke returned no result (exit ${code}, signal ${signal}). Evidence: ${temporary}\n${output.slice(-8000)}`);
        const result = JSON.parse(line.slice(MARK.length));
        assert.equal(code, 0, JSON.stringify({ ...result, signal, temporary }));
        assert.equal(result.ok, true, JSON.stringify({ ...result, temporary }));
        const appRequests = requests.filter(item => item.route !== '/__fixture');
        assert.ok(appRequests.some(item => item.route === '/agents/list'), 'renderer loaded synthetic agents');
        assert.ok(appRequests.some(item => item.route === '/chat/capo/sessions'), 'renderer loaded synthetic archive');
        assert.ok(appRequests.some(item => item.route === '/portfolio'), 'Chat suggestions loaded synthetic portfolio');
        assert.ok(appRequests.filter(item => item.method !== 'GET').every(item => item.hasSyntheticToken),
          'all Chat writes and streams used the synthetic session token');
        assert.ok(appRequests.filter(item => item.method !== 'GET').every(item =>
          (item.method === 'POST' && (item.route === '/prices/update' || item.route === '/chat/sessions' || /^\/chat\/sessions\/\d+\/stream$/.test(item.route)))
          || (item.method === 'PUT' && item.route === '/preferences')
          || (item.method === 'DELETE' && /^\/chat\/sessions\/\d+$/.test(item.route))),
        'only Chat actions and isolated synthetic preferences/price refresh writes reached this fixture');
        assert.deepEqual(state.unexpectedWrites, [], 'fixture rejected and recorded no non-Chat writes');
        assert.deepEqual(state.unexpectedGets, [], `only explicitly modeled GET routes were used: ${state.unexpectedGets.join(', ')}`);
        const summary = { ok: result.ok, capturedAt: new Date().toISOString(), scenarios: result.scenarios,
          viewportReports: result.viewportReports, captures: result.captures, requestCount: appRequests.length,
          streamCount: state.streamCount, abortedStreams: state.abortedStreams, consoleErrors: result.consoleErrors,
          tapeResizeEvidence: result.tapeResizeEvidence,
          rightRailScrollEvidence: result.rightRailScrollEvidence,
          archiveTableEvidence: result.tableEvidence,
          toolPreviewEvidence: result.resultPreviewEvidence,
          metricEvidence: { zeroTokens: result.zeroEvidence, zeroCost: result.zeroCost, noCallCost: result.noCallCost,
            positiveCost: result.positiveCost, partialCost: result.partialCost },
          networkBlocked: result.blockedExternalRequests, tokenEvidence: { syntheticTokenRequests: appRequests.filter(item => item.hasSyntheticToken).length,
            recordedTokenValues: false }, artifactDirectory: captureDirectory };
        fs.writeFileSync(path.join(captureDirectory, 'summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'requests-redacted.json'), JSON.stringify(fixtureState, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'electron.log'), output, { mode: 0o600 });
        for (const name of ['failure.log', 'failure-requests-redacted.json']) {
          const stale = path.join(captureDirectory, name);
          if (fs.existsSync(stale)) fs.unlinkSync(stale);
        }
        console.log(JSON.stringify({ temporary, ...summary }));
        resolve();
      } catch (error) {
        fs.writeFileSync(path.join(temporary, 'electron.log'), output, { mode: 0o600 });
        fs.writeFileSync(path.join(temporary, 'requests.json'), JSON.stringify(requests, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'failure.log'), output, { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'failure-requests-redacted.json'), JSON.stringify({
          requests: requests.map(item => ({ ...item })), unexpectedWrites: state.unexpectedWrites,
          unexpectedGets: state.unexpectedGets, streamCount: state.streamCount, abortedStreams: state.abortedStreams,
        }, null, 2), { mode: 0o600 });
        reject(error);
      } finally {
        // Stop only the child this fixture spawned; never discover or terminate
        // any separately running Bellomberg process.
        if (child && child.exitCode === null && child.signalCode === null) child.kill();
        server.closeAllConnections?.(); server.close();
      }
    });
  });
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-chat-modes';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const scenarios = [];
  const consoleErrors = [];
  const blockedExternalRequests = [];
  let window;
  const js = async (fn, ...args) => {
    const result = await window.webContents.executeJavaScript(`(async()=>{try{return await (${fn.toString()})(...${JSON.stringify(args)})}catch(error){return {__chatQaThrown:String(error?.stack||error)}}})()`);
    if (result && typeof result === 'object' && result.__chatQaThrown) throw new Error(`Renderer assertion script failed: ${result.__chatQaThrown}`);
    return result;
  };
  const wait = async (fn, label, timeout = 12000, ...args) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      if (await js(fn, ...args)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`${label}: ${await js(() => document.body.innerText.slice(0, 2400))}`);
  };
  const click = selector => js(s => {
    const element = document.querySelector(s);
    if (!element) throw new Error('Missing selector: ' + s);
    element.click();
  }, selector);
  const fill = (selector, value) => js((s, v) => {
    const element = document.querySelector(s);
    if (!element) throw new Error('Missing input: ' + s);
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')?.set;
    setter ? setter.call(element, v) : (element.value = v);
    element.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: v }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  }, selector, value);
  const key = (selector, keyName, extra = {}) => js((s, k, x) => {
    const el = document.querySelector(s);
    if (!el) throw new Error('Missing keyboard target: ' + s);
    el.focus();
    const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...x });
    el.dispatchEvent(event);
    return { defaultPrevented: event.defaultPrevented, value: el.value };
  }, selector, keyName, extra);
  const fixture = async body => {
    const response = await fetch(config.origin + '/__fixture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    if (!response.ok) throw new Error('Fixture control failed: ' + response.status);
    return response.json();
  };
  const state = async () => (await fetch(config.origin + '/__fixture')).json();
  const count = async (method, route) => (await state()).requests.filter(item => item.method === method && item.route === route).length;
  const waitCount = async (method, route, minimum) => {
    const end = Date.now() + 10000;
    while (Date.now() < end) {
      const n = await count(method, route);
      if (n >= minimum) return n;
      await new Promise(resolve => setTimeout(resolve, 40));
    }
    throw new Error(`Timed out waiting for ${method} ${route} request count >= ${minimum}`);
  };
  const waitFixture = async (predicate, label, timeout = 12000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const current = await state();
      if (predicate(current)) return current;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Timed out waiting for fixture state: ${label}`);
  };
  const settle = async () => {
    await js(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    await new Promise(resolve => setTimeout(resolve, 100));
  };
  const capture = async name => {
    await settle();
    fs.writeFileSync(path.join(config.captureDirectory, name), (await window.capturePage(undefined, { stayHidden: true })).toPNG());
  };
  const readModeState = () => js(() => {
    const chat = document.querySelector('.f3d');
    const modern = document.querySelector('.chat-modern');
    const textarea = chat?.querySelector('.comp textarea');
    window.__chatQaIds ||= new WeakMap();
    window.__chatQaNextId ||= 0;
    const identity = node => {
      if (!node) return null;
      if (!window.__chatQaIds.has(node)) window.__chatQaIds.set(node, ++window.__chatQaNextId);
      return window.__chatQaIds.get(node);
    };
    return { mode: (document.documentElement.getAttribute('data-bb-theme') === 'dark' ? 'modern' : 'classic'),
      modernRoot: !!modern, classicRoot: !!chat, chatNodeId: identity(chat), textareaNodeId: identity(textarea),
      draft: textarea?.value ?? null, disabled: textarea?.disabled ?? null,
      composerVisible: !!textarea && textarea.getBoundingClientRect().height > 0 && getComputedStyle(textarea).visibility !== 'hidden' };
  });
  // Classica was removed (2026-10-02): the harness keeps its two presentations
  // as the two themes — 'classic' paints Light and 'modern' paints Dark.
  const switchMode = async mode => {
    const theme = mode === 'modern' ? 'dark' : 'light';
    if (await js(() => document.querySelector('.bb-interface-menu-toggle')?.getAttribute('aria-expanded') === 'false')) await click('.bb-interface-menu-toggle');
    await click(`[data-theme-choice="${theme}"]`);
    await wait(choice => document.querySelector(`[data-theme-choice="${choice}"]`)?.getAttribute('aria-pressed') === 'true',
      `global ${theme} theme`, config.modeWaitMs, theme);
    await settle();
  };
  const readContrast = () => js(() => {
    const rgb = text => {
      const s = String(text || '').trim();
      let m = s.match(/^rgba?\(\s*([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i);
      if (m) return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] ? (m[4].endsWith('%') ? Number(m[4].slice(0,-1))/100 : Number(m[4])) : 1];
      m = s.match(/^#([\da-f]{3}|[\da-f]{6})$/i);
      if (!m) return null;
      const h = m[1].length === 3 ? m[1].split('').map(c => c+c).join('') : m[1];
      return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16), 1];
    };
    const luminance = c => {
      const v = c.map(x => { x /= 255; return x <= .04045 ? x/12.92 : ((x+.055)/1.055)**2.4; });
      return .2126*v[0] + .7152*v[1] + .0722*v[2];
    };
    const bgFor = el => {
      for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
        const c = rgb(getComputedStyle(node).backgroundColor);
        if (c && c[3] >= .98) return { color: c, source: node.className || node.tagName };
      }
      return { color: rgb(getComputedStyle(document.documentElement).backgroundColor) || [255,255,255,1], source: 'document' };
    };
    const selectors = ['.conv .prose', '.comp textarea', '.cL .bbn-card-head h2', '.cL .sess .ttl', '.cR .ev .tn', '.chat-modern-cost'];
    return selectors.flatMap(selector => [...document.querySelectorAll(selector)].filter(el => {
      const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
    }).slice(0, selector === '.conv .prose' ? 1 : 2).map(el => {
      const fg = rgb(getComputedStyle(el).color), bg = bgFor(el);
      if (!fg || !bg.color) return { selector, text: el.textContent.trim().slice(0,60), contrast: null, foreground: getComputedStyle(el).color, background: bg };
      const l1 = luminance(fg), l2 = luminance(bg.color), ratio = (Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05);
      return { selector, text: el.textContent.trim().slice(0,60), contrast: Number(ratio.toFixed(2)), foreground: getComputedStyle(el).color,
        background: `rgb(${bg.color.slice(0,3).join(',')})`, backgroundSource: bg.source, fontSize: getComputedStyle(el).fontSize };
    }));
  });
  const geometry = () => js(() => {
    const chat = document.querySelector('.f3d');
    const textarea = chat?.querySelector('.comp textarea');
    const box = el => { if (!el) return null; const r=el.getBoundingClientRect(); return { left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight }; };
    const rect=box(chat), comp=box(textarea);
    return { viewport:{width:innerWidth,height:innerHeight}, mode:document.documentElement.getAttribute('data-bb-theme') === 'dark'?'modern':'classic', chat:rect,
      composer:comp, document:{scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,
        scrollHeight:document.documentElement.scrollHeight,clientHeight:document.documentElement.clientHeight},
      horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1 || !!(rect && (rect.left < -1 || rect.right > innerWidth+1)),
      composerVisible:!!textarea && comp.height>0 && getComputedStyle(textarea).visibility!=='hidden' };
  });
  const readTapeMeasurement = () => js(() => {
    const chart = document.querySelector('.chat-modern-tape-chart');
    const svg = chart?.querySelector('svg');
    const rect = chart?.getBoundingClientRect();
    const svgWidth = svg ? Number(svg.getAttribute('width')) : 0;
    return { chartWidth: rect?.width || 0, clientWidth: chart?.clientWidth || 0,
      svgWidth, viewBox: svg?.getAttribute('viewBox') || null,
      usable: !!chart && !!svg && rect.width > 0 && svgWidth > 0 && Math.abs(chart.clientWidth - svgWidth) <= 4 };
  });
  const assertTapeMeasurement = async label => {
    await wait(() => {
      const chart = document.querySelector('.chat-modern-tape-chart');
      const svg = chart?.querySelector('svg');
      return !!chart && !!svg && chart.clientWidth > 0
        && Math.abs(chart.clientWidth - Number(svg.getAttribute('width'))) <= 4;
    }, label, 8000);
    const measurement = await readTapeMeasurement();
    assert.ok(measurement.usable, `${label}: measured tape width matches its visible box: ${JSON.stringify(measurement)}`);
    return measurement;
  };

  try {
    window = new BrowserWindow({ show: false, width: 1440, height: 1000,
      webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true,
        nodeIntegration: false, sandbox: true, backgroundThrottling: false,
        additionalArguments: ['--bellomberg-launch-id=synthetic-chat-modes', '--bellomberg-api-port=' + new URL(config.origin).port] } });
    window.webContents.on('console-message', (_event, level, message) => { if (level >= 2) consoleErrors.push(String(message)); });
    window.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*','https://*/*','ws://*/*','wss://*/*'] }, (details, callback) => {
      const allowed = details.url.startsWith(config.origin + '/');
      if (!allowed) blockedExternalRequests.push(details.url);
      callback({ cancel: !allowed });
    });
    await window.loadURL(config.origin + '/#/chat');
    await new Promise(resolve => setTimeout(resolve, 300));
    await js((token, theme) => {
      localStorage.setItem('bellomberg_token_v1', token);
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-chat-modes');
      localStorage.setItem('bellomberg.lingua', 'en');
      localStorage.setItem('bellomberg.interface-theme.v1', 'light');
      if (theme === 'dark') localStorage.setItem('bellomberg.interface-theme.v1', 'dark');
      else localStorage.removeItem('bellomberg.interface-theme.v1');
    }, FIXTURE_TOKEN, config.theme || 'light');
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await wait(() => !!document.querySelector('[data-testid="appearance-menu"]') && !!document.querySelector('.f3d'), 'Chat Classic mount');
    // Nuova (2026-10-02): the archive is one list per desk with a This desk | All switch, no per-agent groups.
    await wait(() => document.querySelectorAll('.dk').length >= 6 && document.querySelectorAll('.sess').length >= 1
      && document.querySelectorAll('.bbn-chat-archive .bbn-seg button').length === 2, 'agents and desk archive loaded');
    await wait(() => !document.querySelector('.f3d .comp textarea')?.disabled, 'Classic Chat composer ready');
    assert.equal(await js(() => document.querySelector('[data-theme-choice="light"]')?.getAttribute('aria-pressed')), 'true');
    scenarios.push('Classic defaults to the existing Chat presentation and synthetic agent/archive requests load');

    // Agent changes use the live synthetic roster and preserve one shared Chat owner.
    const macroArchiveReadsBefore = await count('GET', '/chat/macro/sessions');
    const streamsBeforeAgentSwitch = (await state()).streamCount;
    await js(name => {
      const agent = [...document.querySelectorAll('.f3d .dk')].find(button => button.querySelector('.nm')?.textContent.trim() === name);
      if (!agent) throw new Error('Synthetic agent is missing: ' + name);
      agent.click();
    }, 'Synthetic Macro');
    await wait(() => [...document.querySelectorAll('.f3d .dk')].some(button =>
      button.classList.contains('on') && button.querySelector('.nm')?.textContent.trim() === 'Synthetic Macro'),
    'fixture agent selection changes the active specialist');
    assert.equal(await count('GET', '/chat/macro/sessions'), macroArchiveReadsBefore,
      'selecting an already-loaded synthetic agent does not refetch its archive');
    assert.equal((await state()).streamCount, streamsBeforeAgentSwitch, 'agent selection does not send a Chat prompt');
    await js(name => {
      const agent = [...document.querySelectorAll('.f3d .dk')].find(button => button.querySelector('.nm')?.textContent.trim() === name);
      if (!agent) throw new Error('Synthetic agent is missing: ' + name);
      agent.click();
    }, 'Synthetic Lead');
    await wait(() => [...document.querySelectorAll('.f3d .dk')].some(button =>
      button.classList.contains('on') && button.querySelector('.nm')?.textContent.trim() === 'Synthetic Lead'),
    'fixture lead agent is selected again');
    scenarios.push('Synthetic fixture agent selection changes the active specialist without archive reloads or stream writes');

    // Archive search, selection, history, retired specialist, and mode state.
    await fill('.f3d .srch input', 'valuation');
    await wait(() => document.querySelectorAll('.f3d .sess').length === 1, 'archive title search filters sessions');
    await click('.f3d .sess');
    await wait(() => document.querySelector('.f3d .conv .prose')?.textContent.toLowerCase().includes('synthetic archived analysis'), 'selected archived messages loaded');
    await wait(() => !!document.querySelector('.f3d .conv .prose table'), 'historical GFM table renders from the synthetic archive');
    const tableEvidence = await js(() => {
      const table = document.querySelector('.f3d .conv .prose table');
      return { rows: table?.querySelectorAll('tr').length ?? 0, text: table?.innerText || '' };
    });
    assert.ok(tableEvidence.rows >= 3 && /Historical synthetic measure/.test(tableEvidence.text),
      `long Markdown includes an actual GFM table: ${JSON.stringify(tableEvidence)}`);
    const historyText = await js(() => document.querySelector('.f3d .conv')?.innerText || '');
    assert.ok(historyText.toLowerCase().includes('evidence section 14'), 'long historical Markdown is loaded into the shared conversation owner');
    window.setContentSize(1920, 1080);
    await settle();
    if (!config.baseline) await capture('chat-classic-long-1920x1080-early.png');
    if (config.baseline) {
      await capture('chat-baseline-classic-1920x1080.png');
    }
    let identityBefore = await readModeState();
    await switchMode('modern');
    let identityAfter = await readModeState();
    assert.equal(identityAfter.chatNodeId, identityBefore.chatNodeId, 'Chat root DOM node survives a global presentation change');
    assert.equal(identityAfter.textareaNodeId, identityBefore.textareaNodeId, 'Chat composer DOM node survives a global presentation change');
    assert.ok(await js(text => document.querySelector('.f3d .conv')?.innerText.toLowerCase().includes(text.toLowerCase()), 'Evidence section 14'), 'archived conversation state survives mode change');
    if (!config.baseline) await capture('chat-modern-long-1920x1080-early.png');
    await switchMode('classic');
    await click('.bbn-chat-archive .bbn-seg button:nth-child(2)');
    await fill('.f3d .srch input', 'legacy fixture discussion');
    await wait(() => document.querySelectorAll('.f3d .sess').length === 1, 'legacy archive search');
    await click('.f3d .sess');
    // The Classic status bar (.bar .ko) is gone: the header names the retired desk and says read-only.
    await wait(() => /politics/i.test(document.querySelector('.f3d .chat-modern-agent-header h1')?.innerText || '')
      && /READ ONLY|SOLA LETTURA/i.test(document.querySelector('.f3d .chat-modern-agent-header')?.innerText || ''), 'retired agent selected read-only');
    assert.equal(await js(() => document.querySelector('.f3d .comp textarea')?.disabled), true, 'retired specialist composer is disabled');
    await click('.f3d .srch input');
    await fill('.f3d .srch input', '');
    await click('.bbn-chat-archive .bbn-seg button:nth-child(1)');
    await click('.f3d button.nuova');
    await wait(() => document.querySelector('.f3d .comp textarea') && !document.querySelector('.f3d .comp textarea').disabled, 'new conversation restores active composer');
    scenarios.push('archive search/select, long history, legacy read-only, and Classic/Modern state continuity');

    // Draft state and Enter semantics. Shift+Enter remains a draft and issues no request.
    const beforeShiftStreams = await count('POST', '/chat/sessions/1000/stream');
    await fill('.f3d .comp textarea', 'draft survives interface switch');
    const shift = await key('.f3d .comp textarea', 'Enter', { shiftKey: true });
    assert.equal(shift.defaultPrevented, false, 'Shift+Enter remains available for a newline');
    const draftRef = await readModeState();
    await switchMode('modern');
    const draftModern = await readModeState();
    assert.equal(draftModern.draft, 'draft survives interface switch', 'draft text stays in the shared owner');
    assert.equal(draftModern.textareaNodeId, draftRef.textareaNodeId, 'draft composer is not remounted');
    assert.equal(await count('POST', '/chat/sessions/1000/stream'), beforeShiftStreams, 'Shift+Enter never sends a request');
    await switchMode('classic');
    await fill('.f3d .comp textarea', 'zero metric stream');
    await fixture({ streamMode: 'zero' });
    await key('.f3d .comp textarea', 'Enter');
    await waitCount('POST', '/chat/sessions', 1);
    await waitCount('POST', '/chat/sessions/1000/stream', 1);
    await wait(() => document.querySelector('.f3d .comp textarea')?.disabled === false
      && /0\s*\/\s*0/.test(document.querySelector('.f3d .tel')?.innerText || ''), 'zero tokens remain visible after a completed stream', 15000);
    const zeroEvidence = await js(() => ({ telemetry: document.querySelector('.f3d .tel')?.innerText || '',
      archiveText: document.querySelector('.f3d .conv')?.innerText || '', cost: document.querySelector('.chat-modern-cost')?.innerText || null }));
    await click('.f3d .ev');
    await wait(() => !!document.querySelector('.f3d .ev.hot'), 'custody pin selects the tool card');
    assert.match(await js(() => document.querySelector('.f3d .cat')?.innerText || ''), /fixture_portfolio_read/);
    scenarios.push('Enter sends once; SSE tool tape, custody pin, and measured zero tokens/cost render');

    // The modern cost view must preserve a genuine zero and distinguish an absent value.
    await switchMode('modern');
    // Nuova: response details open on request (header button) and show one section at a time.
    if (await js(() => document.querySelector('.chat-inspector-toggle')?.getAttribute('aria-expanded') !== 'true')) await click('.chat-inspector-toggle');
    await click('.bbn-chat-details-tabs .bbn-seg button:nth-child(2)');
    await wait(() => document.querySelector('.f3d .cR .cat')?.getBoundingClientRect().height > 0, 'details open on Sources');
    assert.ok(await js(() => !!document.querySelector('.chat-modern-tape-event[aria-pressed="true"]')),
      'the selected custody pin is retained by the compact Modern tape');
    const resultPreviewEvidence = await js(() => {
      const preview = document.querySelector('.f3d .cR .cat .ev.hot .prev');
      const content = preview?.querySelector('div');
      const text = content?.textContent || '';
      const tail = 'PREVIEW END: every value above is synthetic.';
      const tailOffset = text.indexOf(tail);
      const range = document.createRange();
      if (content?.firstChild && tailOffset >= 0) {
        range.setStart(content.firstChild, tailOffset);
        range.setEnd(content.firstChild, tailOffset + tail.length);
      }
      if (preview) preview.scrollTop = preview.scrollHeight;
      const box = preview?.getBoundingClientRect();
      const tailBox = tailOffset >= 0 ? range.getBoundingClientRect() : null;
      const tailVisible = !!box && !!tailBox && tailBox.top >= box.top && tailBox.bottom <= box.bottom;
      const evidence = { exists: !!preview, textLength: text.length, includesFixture: text.includes('"fixture": "synthetic-chat-modes"'),
        includesHolding: text.includes('"ticker": "ZZTEST"') && text.includes('"market_value_eur"'),
        includesTail: tailOffset >= 0, overflowY: preview ? getComputedStyle(preview).overflowY : null,
        scrollHeight: preview?.scrollHeight ?? 0, clientHeight: preview?.clientHeight ?? 0,
        scrollWidth: preview?.scrollWidth ?? 0, clientWidth: preview?.clientWidth ?? 0,
        scrollTop: preview?.scrollTop ?? 0, tailVisible };
      if (preview) preview.scrollTop = 0;
      return evidence;
    });
    assert.ok(resultPreviewEvidence.exists && resultPreviewEvidence.includesFixture
      && resultPreviewEvidence.includesHolding && resultPreviewEvidence.includesTail,
    `long tool preview contains synthetic portfolio rows and an end marker: ${JSON.stringify(resultPreviewEvidence)}`);
    assert.equal(resultPreviewEvidence.overflowY, 'auto', 'Modern preview is vertically scrollable while Classic styling remains unchanged');
    assert.ok(resultPreviewEvidence.scrollHeight > resultPreviewEvidence.clientHeight
      && resultPreviewEvidence.scrollTop > 0 && resultPreviewEvidence.tailVisible,
    `the full long tool preview tail can be reached by scrolling: ${JSON.stringify(resultPreviewEvidence)}`);
    assert.ok(resultPreviewEvidence.scrollWidth <= resultPreviewEvidence.clientWidth + 1,
      `long preview text wraps without horizontal overflow: ${JSON.stringify(resultPreviewEvidence)}`);
    let tapeResizeEvidence = [];
    let rightRailScrollEvidence = null;
    await click('.bbn-chat-details-tabs .bbn-seg button:nth-child(1)');
    for (const [width, height] of VIEWPORTS) {
      window.setContentSize(width, height);
      await new Promise(resolve => setTimeout(resolve, 120));
      await switchMode('classic');
      await switchMode('modern');
      tapeResizeEvidence.push({ viewport: { width, height }, measurement: await assertTapeMeasurement(`Modern tape after resize and mode round-trip at ${width}x${height}`) });
      if (width === 900) {
        rightRailScrollEvidence = [];
        for (const [index, pane] of [[1, 'nastro'], [2, 'fonti'], [3, 'costi']]) {
          await click(`.bbn-chat-details-tabs .bbn-seg button:nth-child(${index})`);
          rightRailScrollEvidence.push(await js(name => {
            const el = document.querySelector(`.f3d .cR [data-pane="${name}"]`);
            el?.scrollIntoView({ block: 'nearest' });
            const r = el?.getBoundingClientRect();
            return { name, visible: !!r && r.height > 0 && r.top < innerHeight && r.bottom > 0, text: (el?.innerText || '').slice(0, 40) };
          }, pane));
        }
        await click('.bbn-chat-details-tabs .bbn-seg button:nth-child(1)');
        assert.ok(rightRailScrollEvidence.every(panel => panel.visible && panel.text),
          `tape, sources and costs are each reachable from the details tabs at 900x700: ${JSON.stringify(rightRailScrollEvidence)}`);
      }
    }
    window.setContentSize(1440, 1000);
    await switchMode('classic');
    await switchMode('modern');
    await wait(() => !!document.querySelector('.chat-modern-cost'), 'modern cost field is visible');
    const zeroCost = await js(() => ({ text: document.querySelector('.chat-modern-cost')?.innerText || '',
      status: document.querySelector('.chat-modern-cost')?.getAttribute('data-cost-status') || null }));
    assert.equal(zeroCost.status, 'measured', `measured zero cost is not treated as missing: ${JSON.stringify(zeroCost)}`);
    assert.match(zeroCost.text, /0/);
    await switchMode('classic');
    await click('.f3d button.nuova');
    await fixture({ streamMode: 'no-call' });
    await fill('.f3d .comp textarea', 'synthetic no-call usage');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1001/stream', 1);
    await wait(() => !document.querySelector('.f3d .comp textarea')?.disabled
      && document.querySelector('.f3d .conv')?.innerText.includes('model was not called'), 'no-call metric state settles', 15000);
    await switchMode('modern');
    const noCallCost = await js(() => ({ text: document.querySelector('.chat-modern-cost')?.innerText || '',
      status: document.querySelector('.chat-modern-cost')?.getAttribute('data-cost-status') || null }));
    assert.equal(noCallCost.status, 'no-call', `backend no-call status takes priority over incomplete cache metadata: ${JSON.stringify(noCallCost)}`);
    assert.match(noCallCost.text, /no model request|not called/i);
    assert.match(noCallCost.text, /€0/);
    await switchMode('classic');
    await click('.f3d button.nuova');
    await fill('.f3d .comp textarea', 'positive cost precision');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1002/stream', 1);
    await wait(() => !document.querySelector('.f3d .comp textarea')?.disabled
      && document.querySelector('.f3d .tel')?.innerText.includes('1,234'), 'positive token usage after second stream', 15000);
    await switchMode('modern');
    await wait(() => document.querySelector('.chat-modern-cost')?.getAttribute('data-cost-status') === 'measured', 'positive measured cost state');
    const positiveCost = await js(() => document.querySelector('.chat-modern-cost')?.innerText || '');
    assert.match(positiveCost, /0[.,]012345|0[.,]01235/, `cost precision is preserved in modern display: ${positiveCost}`);
    await switchMode('classic');

    // Force a Modern-only child render failure while preserving Chat owner state.
    await fill('.f3d .comp textarea', 'draft survives local Modern recovery');
    const boundaryConversationBefore = await js(() => document.querySelector('.f3d .conv')?.innerText || '');
    assert.match(boundaryConversationBefore, /positive cost precision/i, 'positive answer is present before the Modern fault');
    const boundaryReadsBefore = (await state()).requests
      .filter(item => item.method === 'GET' && (item.route === '/agents/list' || /^\/chat\/[^/]+\/sessions$/.test(item.route)))
      .map(item => `${item.method} ${item.route}`).sort();
    const boundaryStreamCount = await count('POST', '/chat/sessions/1002/stream');
    await js(() => {
      const original = Intl.NumberFormat;
      window.__chatQaIntlNumberFormatOriginal = original;
      window.__chatQaIntlNumberFormatFault = true;
      Intl.NumberFormat = new Proxy(original, {
        construct(target, args, newTarget) {
          const options = args[1];
          if (window.__chatQaIntlNumberFormatFault && options?.style === 'currency'
            && options?.currency === 'EUR' && options?.maximumFractionDigits === 6) {
            throw new Error('Synthetic Modern cost-render failure');
          }
          return Reflect.construct(target, args, newTarget);
        },
      });
    });
    await click('[data-theme-choice="dark"]');
    await wait(() => !!document.querySelector('[data-theme-recover="light"]'), 'Modern local boundary catches its cost renderer failure', 10000);
    assert.match(await js(() => document.querySelector('.bb-interface-recovery')?.innerText || ''), /Synthetic Modern cost-render failure/);
    assert.equal(await count('POST', '/chat/sessions/1002/stream'), boundaryStreamCount,
      'a Modern render failure does not resend the completed answer');
    // With one presentation the cost renderer runs in both themes: the fault is
    // cleared first, then "Back to Light" must re-render the same Chat owner.
    await js(() => {
      window.__chatQaIntlNumberFormatFault = false;
      if (window.__chatQaIntlNumberFormatOriginal) Intl.NumberFormat = window.__chatQaIntlNumberFormatOriginal;
      delete window.__chatQaIntlNumberFormatOriginal;
      delete window.__chatQaIntlNumberFormatFault;
    });
    await click('[data-theme-recover="light"]');
    await wait(() => document.querySelector('[data-theme-choice="light"]')?.getAttribute('aria-pressed') === 'true'
      && !!document.querySelector('.f3d .comp textarea'), 'local boundary recovers a healthy Chat surface in Light');
    const recoveredBoundaryState = await readModeState();
    const recoveredConversation = await js(() => document.querySelector('.f3d .conv')?.innerText || '');
    assert.equal(recoveredBoundaryState.mode, 'classic');
    assert.equal(recoveredBoundaryState.modernRoot, true, 'Light and Dark share the one Chat presentation');
    assert.equal(recoveredBoundaryState.draft, 'draft survives local Modern recovery', 'draft remains in the Chat owner across local recovery');
    assert.match(recoveredConversation, /positive cost precision/i, 'completed user message survives local recovery');
    assert.match(recoveredConversation, /Synthetic answer/i, 'completed assistant response survives local recovery');
    const boundaryReadsAfter = (await state()).requests
      .filter(item => item.method === 'GET' && (item.route === '/agents/list' || /^\/chat\/[^/]+\/sessions$/.test(item.route)))
      .map(item => `${item.method} ${item.route}`).sort();
    assert.deepEqual(boundaryReadsAfter, boundaryReadsBefore, 'local recovery does not reload agents or the archive');
    assert.equal(await count('POST', '/chat/sessions/1002/stream'), boundaryStreamCount,
      'local recovery does not duplicate the completed stream');
    scenarios.push('a render failure in Dark recovers to Light without losing draft/messages or reloading agents/archive/stream');

    // Tool flow error and partial metrics are deterministic synthetic events.
    await click('.f3d button.nuova');
    await fixture({ streamMode: 'error' });
    await fill('.f3d .comp textarea', 'synthetic error stream');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1003/stream', 1);
    await wait(() => /Synthetic stream error/.test(document.querySelector('.f3d .conv')?.innerText || ''), 'SSE error state is surfaced', 15000);
    await click('.f3d button.nuova');
    await fixture({ streamMode: 'partial' });
    await fill('.f3d .comp textarea', 'synthetic partial usage');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1004/stream', 1);
    await wait(() => /tokens|token|parzial/i.test(document.querySelector('.f3d .tel')?.innerText || ''), 'partial metrics settle');
    await switchMode('modern');
    const partialCost = await js(() => ({ text: document.querySelector('.chat-modern-cost')?.innerText || '', status: document.querySelector('.chat-modern-cost')?.getAttribute('data-cost-status') || '' }));
    assert.equal(partialCost.status, 'partial', `explicit backend partial cost status stays partial: ${JSON.stringify(partialCost)}`);
    assert.doesNotMatch(partialCost.text, /€\s*0(?:[.,]0+)?\b/, 'null cost is never rendered as zero');
    scenarios.push('stream error and partial/missing usage states retain explicit error or n.d. semantics');

    // A held response continues once across a mode flip; abort stops only its request.
    await switchMode('classic');
    await click('.f3d button.nuova');
    await fixture({ streamMode: 'held', releaseHeld: false });
    const beforeHeld = await count('POST', '/chat/sessions/1005/stream');
    await fill('.f3d .comp textarea', 'mode switch while streaming');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1005/stream', beforeHeld + 1);
    await wait(() => document.querySelector('.f3d .conv')?.innerText.includes('waiting for the mode'), 'held stream emits pre-switch text');
    const heldNode = await readModeState();
    // The tape chart is mounted in both themes, so a theme flip re-observes
    // nothing: what must hold is that the live stream survives Dark and back.
    await switchMode('modern');
    await wait(() => !!document.querySelector('.f3d .comp .send.stop'), 'Dark keeps the live stream and Stop control', 10000);
    assert.equal(await count('POST', '/chat/sessions/1005/stream'), beforeHeld + 1, 'switching to Dark does not duplicate the in-flight stream');
    await switchMode('classic');
    await wait(() => !!document.querySelector('.f3d .comp .send.stop'), 'Light keeps the live stream and Stop control', 10000);
    const heldAfterRecovery = await readModeState();
    assert.equal(heldAfterRecovery.mode, 'classic');
    assert.equal(heldAfterRecovery.chatNodeId, heldNode.chatNodeId, 'the Chat owner is not remounted by the theme flips');
    assert.equal(heldAfterRecovery.disabled, true, 'composer remains disabled while the original stream is active');
    assert.match(await js(() => document.querySelector('.f3d .conv')?.innerText || ''), /waiting for the mode/,
      'partial assistant response survives the theme flips');
    assert.equal(await count('POST', '/chat/sessions/1005/stream'), beforeHeld + 1,
      'returning to Light keeps exactly one original stream request');
    await fixture({ releaseHeld: true });
    await wait(() => document.querySelector('.f3d .conv')?.innerText.includes('continued after the presentation changed'), 'held stream continues after mode change', 15000);
    await wait(() => document.querySelector('.f3d .comp textarea')?.disabled === false, 'held stream terminal event settles', 15000);
    await click('.f3d button.nuova');
    await fixture({ streamMode: 'abort' });
    await fill('.f3d .comp textarea', 'abort this synthetic stream');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1006/stream', 1);
    await wait(() => !!document.querySelector('.f3d .comp .send.stop'), 'Stop control while synthetic stream is active');
    await click('.f3d .comp .send.stop');
    await waitFixture(current => current.abortedStreams >= 1, 'AbortController closes synthetic stream', 15000);
    await wait(() => document.querySelector('.f3d .comp textarea')?.disabled === false, 'abort settles composer', 15000);
    const modeWriteCount = await count('POST', '/chat/sessions/1006/stream');
    assert.equal(modeWriteCount, 1, 'abort did not resend the streamed message');
    scenarios.push('a held SSE stream continues once across Dark and back to Light; Stop aborts exactly one request');

    // Portfolio suggestions: GET-only load, case-insensitive ticker filtering, dedupe, refresh, send.
    await click('.f3d button.nuova');
    await fill('.f3d .comp textarea', 'open position suggestions');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1007/stream', 1);
    await wait(() => !document.querySelector('.f3d .comp textarea')?.disabled, 'prepares suggestion row');
    await click('.f3d details.chat-followups summary');
    await wait(() => document.querySelector('[aria-label="Filter portfolio tickers"]'), 'portfolio suggestion tickers loaded');
    let portfolioGets = await count('GET', '/portfolio');
    await fill('[aria-label="Filter portfolio tickers"]', 'acm');
    await wait(() => [...document.querySelectorAll('.portfolio-question-tickers button')].length === 1, 'ticker filter matches only ACME');
    assert.equal(await js(() => [...document.querySelectorAll('.portfolio-question-tickers .chat-position-ticker')].map(b => b.textContent.trim()).join(',')), 'ACME', 'duplicate portfolio symbols are deduplicated before filtering');
    await click('.portfolio-question-header button');
    await waitCount('GET', '/portfolio', portfolioGets + 1);
    // The refresh shows the loading line until the response lands: wait for the list to return.
    await wait(() => !!document.querySelector('[aria-label="Filter portfolio tickers"]'), 'portfolio tickers reloaded');
    await fill('[aria-label="Filter portfolio tickers"]', 'ZZTEST');
    const tickerStreamBefore = await count('POST', '/chat/sessions/1007/stream');
    await click('.portfolio-question-tickers button');
    await waitCount('POST', '/chat/sessions/1007/stream', tickerStreamBefore + 1);
    await wait(() => document.querySelector('.f3d .conv')?.innerText.includes('ZZTEST'), 'ticker action composes a real synthetic prompt');
    assert.equal(await count('POST', '/chat/sessions/1007/stream'), tickerStreamBefore + 1, 'ticker prompt launches one stream in the active session');
    scenarios.push('portfolio ticker list loads and refreshes with GET, filters/deduplicates, and ticker click sends one prompt');

    await wait(() => document.querySelector('.f3d .comp textarea')?.disabled === false
      && document.querySelector('.f3d .comp textarea')?.value === '', 'portfolio follow-up stream settles before incomplete-stream case');
    await fixture({ streamMode: 'incomplete' });
    const incompleteStreamsBefore = await count('POST', '/chat/sessions/1007/stream');
    await fill('.f3d .comp textarea', 'synthetic incomplete stream');
    await click('.f3d .comp .send');
    await waitCount('POST', '/chat/sessions/1007/stream', incompleteStreamsBefore + 1);
    await wait(() => /The fixture closes without a terminal event/.test(document.querySelector('.f3d .conv')?.innerText || '')
      && /incomplete response|stream ended without server confirmation/i.test(document.querySelector('.f3d .conv')?.innerText || ''),
    'SSE ending without a terminal event displays its incomplete warning');
    assert.equal(await js(() => document.querySelector('.f3d .comp textarea')?.disabled), false,
      'composer returns to an enabled state after an incomplete stream');
    await fill('.f3d .comp textarea', 'composer remains usable after an incomplete stream');
    assert.equal(await js(() => document.querySelector('.f3d .comp textarea')?.value),
      'composer remains usable after an incomplete stream', 'incomplete stream leaves the composer editable');
    await fill('.f3d .comp textarea', '');
    assert.equal(await count('POST', '/chat/sessions/1007/stream'), incompleteStreamsBefore + 1,
      'incomplete stream produced only its single request');
    scenarios.push('SSE closing without done/error shows an incomplete warning and leaves the composer usable');

    // Delete confirmation remains the only route to a synthetic DELETE.
    await switchMode('classic');
    await fill('.f3d .srch input', 'valuation');
    await wait(() => !!document.querySelector('.f3d .sess-wrap'), 'deleted-session candidate visible');
    const deleteCountBefore = await count('DELETE', '/chat/sessions/101');
    await click('.f3d .sess-wrap .del');
    await wait(() => !!document.querySelector('.cfm-modal[role="alertdialog"]'), 'delete requires confirmation');
    await click('.cfm-modal .cfm-btn.go');
    await waitCount('DELETE', '/chat/sessions/101', deleteCountBefore + 1);
    await wait(() => ![...document.querySelectorAll('.f3d .sess')].some(el => el.innerText.includes('Synthetic archived valuation discussion')), 'deleted fixture session leaves archive');
    const deleteRequest = (await state()).requests.find(item => item.method === 'DELETE' && item.route === '/chat/sessions/101');
    assert.equal(deleteRequest?.hasSyntheticToken, true, 'confirmed delete carries only fixture authentication');
    scenarios.push('archive delete requires the confirmation dialog and uses authenticated synthetic Chat DELETE');

    // Layout evidence and paired empty/long state renders for all required sizes.
    // To keep the screenshots meaningful, the long view is the selected archive;
    // the empty view is a fresh desk, with both modes represented at each size.
    // Restore the deleted archive row before the isolated render sweep.
    await fixture({ resetStreamMode: true, restoreArchive: true });
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await wait(() => !!document.querySelector('.f3d') && !document.querySelector('[data-theme-recover="light"]'), 'healthy Chat reloads for the viewport sweep', 12000);
    await fill('.f3d .srch input', 'valuation');
    await wait(() => document.querySelectorAll('.f3d .sess').length === 1, 'restore long archive for render captures');
    const viewportReports = [];
    const captures = [];
    for (const [width, height] of VIEWPORTS) {
      window.setContentSize(width, height);
      await new Promise(resolve => setTimeout(resolve, 160));
      for (const mode of ['classic', 'modern']) {
        await switchMode(mode);
        if (mode === 'modern') {
          await wait(() => !!document.querySelector('.f3d .chat-modern-intro'), 'empty Modern welcome after mode switch');
          const switchedWelcome = await js(() => {
            const conv = document.querySelector('.f3d .conv');
            const headline = conv?.querySelector('.chat-modern-intro .big');
            const box = conv?.getBoundingClientRect();
            const within = el => { const r = el?.getBoundingClientRect(); return !!r && r.top >= box.top - 1 && r.bottom <= box.bottom + 1; };
            return { scrollTop: conv?.scrollTop ?? -1, headlineVisible: within(headline) };
          });
          assert.equal(switchedWelcome.scrollTop, 0, `switching to Modern with an empty conversation starts at the top: ${JSON.stringify(switchedWelcome)}`);
          assert.ok(switchedWelcome.headlineVisible,
            `Modern hero is visible after a Classic-to-Modern empty-state switch: ${JSON.stringify(switchedWelcome)}`);
        }
        await click('.f3d .sess');
    await wait(() => document.querySelector('.f3d .conv .prose')?.textContent.toLowerCase().includes('evidence section 14'), `long archive ready for ${mode} ${width}x${height} capture`);
        const long = await geometry();
        const contrast = await readContrast();
        assert.equal(long.viewport.width, width, 'capture uses requested content width');
        assert.equal(long.viewport.height, height, 'capture uses requested content height');
        assert.ok(long.composerVisible, `${mode} Chat composer remains visible at ${width}x${height}: ${JSON.stringify(long)}`);
        assert.equal(long.horizontalOverflow, false, `${mode} Chat has no horizontal page/root overflow at ${width}x${height}: ${JSON.stringify(long)}`);
        assert.ok(contrast.filter(row => row.selector === '.conv .prose').every(row => row.contrast == null || row.contrast >= 4.5),
          `${mode} long prose contrast is readable at ${width}x${height}: ${JSON.stringify(contrast)}`);
        const longName = `chat-${mode}-long-${width}x${height}.png`;
        await capture(longName); captures.push(longName);
        await click('.f3d button.nuova');
        await wait(() => document.querySelector('.f3d .conv .intro'), 'empty desk for render captures');
        await settle();
        if (mode === 'modern') {
          const welcomeAtTop = await js(() => {
            const conv = document.querySelector('.f3d .conv');
            const headline = conv?.querySelector('.chat-modern-intro .big');
            const box = conv?.getBoundingClientRect();
            const within = el => { const r = el?.getBoundingClientRect(); return !!r && r.top >= box.top - 1 && r.bottom <= box.bottom + 1; };
            return { scrollTop: conv?.scrollTop ?? -1, headlineVisible: within(headline),
              promptCount: conv?.querySelectorAll('.chat-modern-prompts .research-questions button').length ?? 0,
              scrollHeight: conv?.scrollHeight ?? 0, clientHeight: conv?.clientHeight ?? 0 };
          });
          assert.equal(welcomeAtTop.scrollTop, 0, `Modern empty view starts at the conversation top: ${JSON.stringify(welcomeAtTop)}`);
          assert.ok(welcomeAtTop.headlineVisible,
            `Modern welcome headline is visible at the top: ${JSON.stringify(welcomeAtTop)}`);
          assert.equal(welcomeAtTop.promptCount, 3, 'all three agent prompt cards render in the Modern welcome view');
          const welcomeReachability = await js(() => {
            const conv = document.querySelector('.f3d .conv');
            const box = conv?.getBoundingClientRect();
            const reveal = el => {
              if (!conv || !el) return false;
              const r = el.getBoundingClientRect();
              const c = conv.getBoundingClientRect();
              if (r.top < c.top) conv.scrollTop -= c.top - r.top;
              else if (r.bottom > c.bottom) conv.scrollTop += r.bottom - c.bottom;
              const after = el.getBoundingClientRect(), viewport = conv.getBoundingClientRect();
              return after.top >= viewport.top - 1 && after.bottom <= viewport.bottom + 1;
            };
            const finalPrompt = conv?.querySelector('.chat-modern-prompts .research-questions button:last-child');
            const portfolio = conv?.querySelector('.chat-modern-prompts .portfolio-question-header');
            const portfolioFilter = conv?.querySelector('.chat-modern-prompts input[aria-label="Filter portfolio tickers"]');
            const finalPromptVisible = reveal(finalPrompt);
            const portfolioVisible = reveal(portfolio);
            const portfolioFilterVisible = reveal(portfolioFilter);
            return { scrollTop: conv?.scrollTop ?? -1, scrollHeight: conv?.scrollHeight ?? 0, clientHeight: conv?.clientHeight ?? 0,
              finalPromptVisible, portfolioVisible, portfolioFilterVisible };
          });
          assert.ok(welcomeReachability.scrollHeight >= welcomeReachability.clientHeight,
            `Modern welcome content stays within the .conv scroll owner: ${JSON.stringify(welcomeReachability)}`);
          assert.ok(welcomeReachability.finalPromptVisible && welcomeReachability.portfolioVisible && welcomeReachability.portfolioFilterVisible,
            `last prompt and portfolio suggestions remain reachable in .conv: ${JSON.stringify(welcomeReachability)}`);
          await js(() => { const conv = document.querySelector('.f3d .conv'); if (conv) conv.scrollTop = 0; });
          await settle();
        }
        const empty = await geometry();
        assert.ok(empty.composerVisible, `${mode} composer visible on empty state at ${width}x${height}`);
        assert.equal(empty.horizontalOverflow, false, `${mode} empty state has no horizontal overflow at ${width}x${height}`);
        const emptyName = `chat-${mode}-empty-${width}x${height}.png`;
        await capture(emptyName); captures.push(emptyName);
        viewportReports.push({ viewport: { width, height }, mode, long, contrast,
          empty: { composerVisible: empty.composerVisible, horizontalOverflow: empty.horizontalOverflow,
            document: empty.document, chat: empty.chat } });
      }
    }
    scenarios.push('empty and long-message render captures cover seven desktop, ultrawide, and compact viewport sizes');

    const finalState = await state();
    assert.equal(finalState.unexpectedWrites.length, 0, 'no non-Chat write escaped the synthetic fixture');
    assert.ok(consoleErrors.some(message => message.includes('Synthetic Modern cost-render failure')),
      'the local Chat boundary handled the deliberate Modern cost-render failure');
    assert.deepEqual(consoleErrors.filter(message => !message.includes('Synthetic Modern cost-render failure')), [],
    `no unrelated renderer console errors occurred: ${consoleErrors.join(' | ')}`);
    assert.deepEqual(blockedExternalRequests, [], `renderer attempted no external network requests: ${blockedExternalRequests.join(', ')}`);
    const result = { ok: true, scenarios, viewportReports, captures, consoleErrors, blockedExternalRequests,
      tapeResizeEvidence, rightRailScrollEvidence, tableEvidence, resultPreviewEvidence,
      zeroEvidence, zeroCost, noCallCost, positiveCost, partialCost,
      fixtureEvidence: { streamCount: finalState.streamCount, abortedStreams: finalState.abortedStreams,
        unexpectedGets: finalState.unexpectedGets, unexpectedWrites: finalState.unexpectedWrites } };
    console.log(MARK + JSON.stringify(result));
  } catch (error) {
    console.error('CHAT_MODES_RENDERER_ERROR ' + (error?.stack || error));
    app.exit(1);
    throw error;
  } finally {
    if (window && !window.isDestroyed()) window.destroy();
    app.exit(0);
  }
}

if (require.main === module) runner().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
module.exports = { renderer, startFixture, longMarkdown };
