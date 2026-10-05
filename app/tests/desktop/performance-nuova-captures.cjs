// Offline Electron captures of the Nuova Performance page (Scheda and Rischio) at 1920, 2560, 3440
// and 5120 px, light and dark, plus the «Metodo e fonti» panel. Every /portfolio read is a synthetic,
// deterministic fixture built here (shaped like the real payloads, no real portfolio data); no backend,
// model or market API is contacted. Requires a built `dist/` (npx vite build).
// BB_PERF_CAPTURE=dir sets the output folder; BB_PERF_SIZES=1920x1080,2560x1440 narrows the sizes.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '../..');

/** Sedute lun-ven dall'8 maggio al 2 ottobre 2026 e un indice TWR che passa per punti fissi. */
function serie() {
  const dates = [];
  for (let d = new Date(Date.UTC(2026, 4, 8)); d <= new Date(Date.UTC(2026, 9, 2)); d.setUTCDate(d.getUTCDate() + 1)) {
    const w = d.getUTCDay(); if (w && w < 6) dates.push(d.toISOString().slice(0, 10));
  }
  const n = dates.length;
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5;
  const build = (anchors, noise) => {
    const out = new Array(n);
    for (let k = 0; k < anchors.length - 1; k++) {
      const [i0, v0] = anchors[k], [i1, v1] = anchors[k + 1];
      for (let i = i0; i <= i1; i++) out[i] = v0 + (v1 - v0) * (i - i0) / (i1 - i0);
    }
    let z = 0; return out.map((v, i) => { z = z * 0.55 + rnd() * noise; return anchors.some(a => a[0] === i) ? v : v + z; });
  };
  const twr = build([[0, 100], [13, 107.5], [20, 119.8], [32, 122.8], [n - 64, 112.19], [59, 96.65], [80, 106.2], [91, 101.2], [n - 1, 111.42]], 2.1)
    .map(v => Math.round(v * 10000) / 10000);
  seed = 31;
  const spy = build([[0, 100], [40, 103.9], [60, 102.7], [n - 1, 109.47]], 0.55);
  const flows = dates.map((_, i) => (i === 27 ? 600 : i === 82 ? 400 : 0));
  const values = []; let v = 8000;
  for (let i = 0; i < n; i++) { if (i > 0) v = v * twr[i] / twr[i - 1] + flows[i]; values.push(Math.round(v * 100) / 100); }
  return { dates, twr, spy, flows, values };
}

function payloads() {
  const s = serie(), n = s.dates.length, last = s.dates[n - 1];
  const POS = [['ALFA.DE', 'Alfa Semiconductors', 'Technology', 22.75, 36.3], ['BETA.FRA', 'Beta Digital Assets', 'Crypto', 7.08, 4.1],
    ['GAMMA.MI', 'Gamma Leisure', 'Consumer Cyclical', 0.38, 4.3], ['DELTA.DE', 'Delta Energy', 'Energy', 0.11, 0.4], ['EPSI.DE', 'Epsilon Mining', 'Basic Materials', -0.06, 0.3],
    ['ZETA.MI', 'Zeta Bank', 'Financial Services', -0.07, 0.1], ['ETA.MI', 'Eta Micro', 'Technology', -1.84, 4.8],
    ['THETA.DE', 'Theta Chips', 'Technology', -2.12, 4.6], ['IOTA.DE', 'Iota Networks', 'Technology', -4.24, 12.4], ['KAPPA.DE', 'Kappa Search', 'Communication Services', -4.44, 31.9]];
  const buckets = {};
  for (const [t, , b, c] of POS) { (buckets[b] ||= { bucket: b, contribution_pct: 0, tickers: [] }); buckets[b].contribution_pct += c; buckets[b].tickers.push(t); }
  let peak = -Infinity; const uw = s.twr.map(x => (peak = Math.max(peak, x), (x / peak - 1) * 100));
  const mc = stress => {
    const base = 9100.00;
    if (stress === 'gfc_2008') return { base_nav_eur: base, percentiles_eur: { p5: base - 916, p50: base - 916 }, median_return_pct: -15.29, prob_loss_10pct: 100, es_95_eur: -916,
      method_description: 'Synthetic FHS', stress_meta: { applied: 'gfc_2008', fallback: false, window_loss_eur: -916, window_loss_pct: -15.29,
        proxied: { 'BETA.FRA': 'PROXY', 'GAMMA.MI': 'PROXY', 'IOTA.DE': 'PROXY' }, basis: 'Synthetic replay basis' } };
    if (stress === 'covid_2020') return null; // capture the per-scenario error state
    const shock = stress === 'shock_3sigma';
    return { base_nav_eur: base, percentiles_eur: { p5: base * (shock ? 0.7678 : 0.8609), p50: base * (shock ? 0.886 : 0.9917) },
      median_return_pct: shock ? -11.4 : -0.83, prob_loss_10pct: shock ? 57.06 : 13.3, es_95_eur: shock ? -1555 : -1023, method_description: 'Synthetic FHS' };
  };
  return {
    '/portfolio/analytics/twr': { dates: s.dates, twr_index: s.twr, regimes: s.dates.map(d => (d < '2026-09-10' ? 'reconstructed' : 'official')),
      values_eur: s.values, flows_eur: s.flows, regime_summary: { official_since: '2026-09-10', n_official_days: 17, n_reconstructed_days: n - 17, seamless_transition: true },
      metrics: { twr_total_pct: (s.twr[n - 1] / 100 - 1) * 100, twr_annualized_pct: 29.6, max_drawdown_pct: Math.min(...uw), current_drawdown_pct: uw[n - 1],
        vol_annual_pct: 40.61, sharpe: 0.68, risk_free_used: 0.032, irr_annual_pct: 15.53, irr_basis: 'synthetic' },
      copertura: { primo_trade: '2026-05-08', primo_snapshot: '2026-09-10', ultimo_snapshot: last, official_since: '2026-09-10',
        n_trade_prima_del_primo_snapshot: 20, giorni_senza_snapshot: 1, nota: 'Nota sintetica di copertura.' },
      reconciliation: { nav_live_eur: s.values[n - 1], last_snapshot_date: last, last_snapshot_nav_eur: s.values[n - 1], delta_pct: 0, note: '' },
      notes: ['Nota di servizio sintetica.'], methodology: 'TWR GIPS (sintetico).' },
    '/portfolio/analytics/benchmark': { ticker: 'SPY', dates: s.dates, index: s.spy, carried_flags: s.dates.map(() => false), coverage_pct: 91, carried_days: 10, src: 'synthetic_series' },
    '/portfolio/analytics/nav_history': { dates: s.dates, nav_eur: s.values.map(v => v * 0.9), cost_basis_eur: s.values.map(() => 9250.00), pnl_eur: s.values.map(() => -81.20),
      realized_sales_eur: s.values.map(() => 412.50), dividend_income_eur: s.values.map(() => 0), cash_eur: 850.00, nav_total_eur: s.values, first_trade_date: '2026-05-08', tickers: POS.map(p => p[0]), n_days: n },
    '/portfolio/metrics/advanced': { sortino: 0.8, calmar: 1.32, benchmark: { beta: 1.58, alpha_annual_pct: -12.94 }, benchmark_ticker: 'SPY', benchmark_alignment: 'eur', risk_free_used: 0.032 },
    '/portfolio/attribution': { period: { label: '3M', base_day: '2026-07-03', end: last, n_trading_days: 63 }, portfolio_return_pct: 16.92,
      by_position: POS.map(([ticker, , , c, w]) => ({ ticker, contribution_pct: c, local_pct: c, fx_pct: 0, cross_pct: 0, avg_weight_pct: w, currency: 'EUR' })),
      by_bucket: Object.values(buckets), by_currency: [{ currency: 'EUR', contribution_pct: 16.92, fx_contribution_pct: 0, tickers: POS.map(p => p[0]) }],
      totals: { local_pct: 16.92, fx_pct: 0, cross_pct: 0 }, excluded: [{ ticker: 'IOTA.DE', reasons: { qty_negativa: 11 }, days_excluded: 11, days_total: 105, partial: true }],
      reconciliation: { official_twr_pct: 11.42, delta_pp: 5.5 }, notes: ['Nota sintetica di attribuzione.'], basis: 'Contributi Carino (sintetico).' },
    '/portfolio/risk': { nav_eur: 9120.00, lookback_days: 249, portfolio: { vol_annual_pct: 30.75, sharpe: 2.67, var_95_1d_pct: -2.76, var_99_1d_pct: -3.87, var_95_1d_eur: -166, var_99_1d_eur: -232, beta_vs_spy: 0.87, max_dd_1y_pct: -18.71 },
      benchmark: { ticker: 'SPY', vol_annual_pct: 14.05, sharpe: 1.63, var_95_1d_pct: -1.39, var_99_1d_pct: -2.01, beta_vs_spy: 1, max_dd_1y_pct: -6.91, n_obs: 234 },
      per_asset: {}, correlation: { tickers: [], matrix: [] }, alerts: [], n_assets_analyzed: 11, skipped_tickers: [] },
    '/portfolio/tearsheet': { drawdowns: { top: [
      { start_date: '2026-06-22', trough_date: '2026-07-29', depth_pct: -21.31, days_to_trough: 27, recovery_date: null, days_total: 72, open: true },
      { start_date: '2026-06-01', trough_date: '2026-06-11', depth_pct: -11.81, days_to_trough: 8, recovery_date: '2026-06-19', days_total: 14, open: false },
      { start_date: '2026-05-14', trough_date: '2026-05-19', depth_pct: -7.23, days_to_trough: 3, recovery_date: '2026-05-25', days_total: 7, open: false }],
      current: { start_date: '2026-06-22', trough_date: '2026-07-29', depth_pct: -21.31, days_to_trough: 27, days_total: 72, open: true, current_dd_pct: uw[n - 1] }, n_episodes_total: 4 } },
    '/portfolio/analytics/concentration': { by_ticker: { hhi: 1249, classification: 'diversified', effective_n: 8.01, top_5_pct: 71.3, top_holdings: [] },
      by_region: { hhi: 7288, classification: 'concentrated', weights_pct: { EU: 83.8, US: 16.2 } }, by_currency: { hhi: 10000, classification: 'concentrated', weights_pct: { EUR: 100 } }, interpretation: {} },
    '/portfolio/analytics/var_contribution': { portfolio_var_pct_daily: 3.31, portfolio_var_eur_daily: 198.76, portfolio_vol_annual_pct: 31.95, confidence_level: 0.05, z_alpha: 1.645, lookback_days: 252, n_assets: 11,
      methodology: 'Component VaR (sintetico).', items: [['BETA.FRA', 14.11, 86.42, 43.48], ['ALFA.DE', 14.57, 43.92, 22.09], ['IOTA.DE', 10.63, 14.97, 7.53], ['ETA.MI', 6.94, 14.06, 7.07],
        ['THETA.DE', 6.68, 13.97, 7.03], ['KAPPA.DE', 21.31, 13.52, 6.8], ['GAMMA.MI', 10.63, 5.42, 2.73], ['ZETA.MI', 5.72, 2.83, 1.42], ['EPSI.DE', 3.13, 2.46, 1.24], ['DELTA.DE', 4.19, -1, -0.5]]
        .map(([ticker, weight_pct, component_var_eur, contribution_pct_of_total_var]) => ({ ticker, weight_pct, component_var_eur, contribution_pct_of_total_var, component_var_pct: 0, marginal_var_pct_per_1pct_weight: 0.05 })) },
    '/portfolio': { positions: POS.map(([ticker, nome]) => ({ ticker, nome, quantita: 1, prezzo_corrente: 100, valore_mercato_eur: 100, valuta: 'EUR' })),
      n_positions: 11, nav_total_eur: 10150.00, cash_disponibile_eur: 850.00, cash_source: 'synthetic' },
    mc,
  };
}

async function run() {
  const { server } = require('./chat-modes.cjs').startFixture();
  const original = server.listeners('request')[0];
  const data = payloads();
  server.removeAllListeners('request');
  server.on('request', async (req, res) => {
    const url = new URL(req.url, 'http://localhost'), route = url.pathname;
    const send = (body, status = 200) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); };
    if (req.method === 'GET' && route === '/preferences') return send({ language: 'it', selected: true, source: 'preferences' });
    if (req.method === 'GET' && route === '/portfolio/montecarlo') {
      const body = data.mc(url.searchParams.get('stress') || 'none');
      return body ? send(body) : send({ detail: 'Synthetic replay failure' }, 500);
    }
    if (req.method === 'GET' && Object.hasOwn(data, route)) return send(data[route]);
    if (req.method !== 'GET' && route.startsWith('/portfolio')) return send({ detail: 'writes are not allowed in captures' }, 403);
    return original(req, res);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-perf-nuova-'));
  const directory = path.resolve(process.env.BB_PERF_CAPTURE || path.join(root, '../outputs/performance-captures'));
  fs.mkdirSync(directory, { recursive: true });
  const sizes = (process.env.BB_PERF_SIZES || '1920x1080,2560x1440,3440x1440,5120x1440').split(',').map(s => s.split('x').map(Number));
  const config = { origin: `http://127.0.0.1:${server.address().port}`, temporary, directory, sizes };
  const entry = path.join(temporary, 'entry.cjs');
  fs.writeFileSync(entry, `require('electron').app.setPath('userData',${JSON.stringify(path.join(temporary, 'userdata'))});require('electron').app.whenReady().then(()=>require(${JSON.stringify(__filename)}).renderer(${JSON.stringify(config)})).catch(e=>{console.error(e);require('electron').app.exit(1)})`);
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(require('electron'), [entry], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = ''; child.stdout.on('data', b => log += b); child.stderr.on('data', b => log += b);
  const timer = setTimeout(() => child.kill(), 900000);
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
  const wait = async (fn, label, ...args) => { for (let i = 0; i < 400; i++) { if (await js(fn, ...args)) return; await pause(50); } throw new Error('Timed out: ' + label); };
  const shot = async name => {
    await pause(500);
    const check = await js(() => ({ overflow: document.documentElement.scrollWidth > innerWidth,
      text: document.querySelector('main [data-page="performance"]')?.innerText || '' }));
    assert.equal(check.overflow, false, 'horizontal overflow in ' + name);
    assert.doesNotMatch(check.text, /NaN|undefined|⟦/, 'broken text in ' + name);
    const full = await js(() => Math.ceil(document.documentElement.scrollHeight));
    const [w, h] = win.getContentSize();
    if (full > h) { win.setContentSize(w, full); await pause(400); }
    fs.writeFileSync(path.join(config.directory, name + '.png'), (await win.webContents.capturePage()).toPNG());
    win.setContentSize(w, h);
    // the page scrolls inside <main>: capture the lower half too, then scroll back
    const scrolled = await js(() => {
      const box = [...document.querySelectorAll('main, main *')].find(el => el.scrollHeight > el.clientHeight + 40 && /auto|scroll/.test(getComputedStyle(el).overflowY));
      if (!box) return false; box.scrollTop = box.scrollHeight; return true;
    });
    if (scrolled) {
      await pause(400);
      fs.writeFileSync(path.join(config.directory, name + '-fondo.png'), (await win.webContents.capturePage()).toPNG());
      await js(() => { [...document.querySelectorAll('main, main *')].forEach(el => { el.scrollTop = 0; }); });
    }
  };
  const reload = () => new Promise(r => { win.webContents.once('did-finish-load', r); win.webContents.reload(); });
  const view = async label => {
    await js(l => [...document.querySelectorAll('main [data-qa="perf-view"] button')].find(b => b.innerText.trim() === l)?.click(), label);
    await wait(l => [...document.querySelectorAll('main [data-qa="perf-view"] button')].find(b => b.innerText.trim() === l)?.getAttribute('aria-pressed') === 'true', 'view ' + label, label);
  };
  try {
    await win.loadURL(config.origin + '/#/performance');
    await js(() => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-chat-fixture-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-chat-modes');
      localStorage.setItem('bellomberg.lingua', 'it');
    });
    for (const theme of ['light', 'dark']) {
      await js(t => localStorage.setItem('bellomberg.interface-theme.v1', t), theme);
      for (const [w, h] of config.sizes) {
        win.setContentSize(w, h);
        const tag = `${theme}-${w}x${h}`;
        await reload();
        await wait(() => !!document.querySelector('main .bbn-perf .perf-line') && !!document.querySelector('main [data-qa="perf-attribution"] .perf-arow:not(.perf-arow-head)'), 'scheda ' + tag);
        await shot(`scheda-${tag}`);
        await view('Rischio');
        await wait(() => document.querySelectorAll('main [data-qa="perf-scenario"] .perf-sc-v, main [data-qa="perf-scenario"] .is-error').length >= 4, 'scenari ' + tag);
        await shot(`rischio-${tag}`);
        if (w === 1920) {
          await js(() => document.querySelector('main [data-qa="perf-method"]')?.click());
          await wait(() => !!document.querySelector('.perf-method .bbn-drawer'), 'metodo');
          await shot(`metodo-${tag}`);
          await js(() => document.querySelector('.perf-method .bbn-icon-btn')?.click());
        }
        await view('Scheda');
      }
    }
    console.log('PERFORMANCE_NUOVA_CAPTURES_OK ' + fs.readdirSync(config.directory).filter(f => f.endsWith('.png')).length);
  } catch (e) { console.error(e); try { fs.writeFileSync(path.join(config.directory, 'failure.png'), (await win.webContents.capturePage()).toPNG()); } catch {} win.destroy(); app.exit(1); return; }
  win.destroy(); app.exit(0);
}
if (require.main === module) run().catch(e => { console.error(e); process.exitCode = 1; });
module.exports = { renderer, payloads };
