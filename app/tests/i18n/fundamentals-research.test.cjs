const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

function fixture(ticker = 'SYNTH.X') {
  return { ticker, name: 'Synthetic Company',
    quote: { value: 40, currency: 'USD', source: 'fixture quote feed', observed_at: '2030-03-04T11:55:00Z', status: 'available' },
    consensus: { mean: 50, median: 49, low: 42, high: 61, number_of_analysts: 7,
      currency: 'USD', source: 'Synthetic analyst provider', acquired_at: '2030-03-04T11:58:00Z',
      data_as_of: null, status: 'available' },
    comparison: { upside_pct: 25, status: 'available' },
    analysis: { status: 'available', origin: 'trade_idea', scope: 'single_company', as_of: '2030-03-03T11:00:00Z',
      technical_status: 'completed', summary: 'Original independent AI conclusion', judgment: 'watch', content: null },
  };
}

function retained(api) {
  let state = 0, effectIndex = 0, refIndex = 0, memoIndex = 0;
  let clock = 0, timerId = 0;
  const timers = new Map();
  const withTimers = action => {
    const originalSet = globalThis.setInterval, originalClear = globalThis.clearInterval;
    globalThis.setInterval = (callback, delay) => { const id = ++timerId; timers.set(id, {callback, delay, next: clock + delay}); return id; };
    globalThis.clearInterval = id => timers.delete(id);
    try { return action(); } finally { globalThis.setInterval = originalSet; globalThis.clearInterval = originalClear; }
  };
  const values = [], refs = [], effects = [], deps = [], cleanups = [], nodes = [], memos = [];
  const instrument = kind => (type, props, key) => { nodes.push({type, props}); return JSX[kind](type, props, key); };
  const load = creaCaricatore({stub: {
    react: {...React, useCallback(callback, next) { const at = memoIndex++, before = memos[at];
      if (!before || next.some((value, i) => !Object.is(value, before.deps[i]))) memos[at] = {deps: next, callback};
      return memos[at].callback; }, useEffect(effect, next) { const at = effectIndex++, before = deps[at];
      if (!before || !next || next.some((value, i) => !Object.is(value, before[i]))) effects.push(() => { cleanups[at]?.(); cleanups[at] = effect(); });
      deps[at] = next; }, useRef(value) { return refs[refIndex++] ||= {current: value}; },
      useState(value) { const at = state++; if (!(at in values)) values[at] = typeof value === 'function' ? value() : value;
        return [values[at], next => { values[at] = typeof next === 'function' ? next(values[at]) : next; }]; }},
    'react/jsx-runtime': {...JSX, jsx: instrument('jsx'), jsxs: instrument('jsxs')},
    '@/lib/api': {Bellomberg: api, API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({'X-BB-Token': 'synthetic-session'})},
  }});
  const language = load('i18n/lingua.ts'), Page = load('pages/FundamentalsPage.tsx').default;
  const render = selected => { state = effectIndex = refIndex = memoIndex = 0; effects.length = nodes.length = 0;
    language.impostaLinguaCorrente(selected); return renderToStaticMarkup(React.createElement(Page)); };
  render.effects = async () => { withTimers(() => { for (const effect of effects) effect(); }); await new Promise(resolve => setImmediate(resolve)); };
  render.nodes = nodes;
  render.settle = async selected => { let html; for (let i = 0; i < 3; i++) { html = render(selected); await render.effects(); } return html; };
  render.advance = async milliseconds => { clock += milliseconds; for (const timer of [...timers.values()]) {
    while (timer.next <= clock) { timer.next += timer.delay; timer.callback(); }
  } await new Promise(resolve => setImmediate(resolve)); };
  render.timerCount = () => timers.size;
  render.unmount = () => withTimers(() => cleanups.forEach(cleanup => cleanup?.()));
  return render;
}

function fakeApi(rows, detail) {
  const calls = [];
  return { calls, fundamentalsResearch: async () => { calls.push('list'); return {status: 'available', count: rows.length, items: rows, notices: []}; },
    fundamentalsCompany: async ticker => { calls.push('detail:' + ticker); return detail || rows.find(row => row.ticker === ticker); },
    fundamentalsArchive: async () => { calls.push('archive'); return {items: [], notices: []}; },
    // Filing summary panel under the selected company: kept loading, outside these checks.
    filingList: () => new Promise(() => {}),
    valuationModels() { throw new Error('Fund must not open the old valuation workspace'); }};
}

test('Fund is a company/analyst-consensus view with zero workbook dependencies and safe detail navigation', async () => {
  const first = fixture(), second = fixture('EMPTY.X');
  second.consensus = {...first.consensus, mean: null, currency: null, status: 'missing'};
  second.comparison = {upside_pct: null, status: 'missing'};
  const detail = structuredClone(first);
  detail.analysis.content = {summary: first.analysis.summary, dossier: [{key: 'assumptions', title: 'Original assumptions',
      paragraphs: ['Original evidence-backed assumption'], tables: []}], scenarios: [{name: 'Bear', analysis: 'Original downside scenario'}],
    risks: ['Original business risk'], catalysts: ['Original catalyst'], data_gaps: ['Original disclosed gap'],
    evidence: [{id: 'e1', source: 'Issuer filing', as_of: '2030-03-01', url: 'https://issuer.example/filing', summary: 'Original evidence'},
      {id: 'unsafe', source: 'Unusable link', url: 'javascript:alert(1)'}]};
  const api = fakeApi([first, second], detail), render = retained(api);
  const it = await render.settle('it');
  assert.match(it, /Consensus analisti/); assert.match(it, /SYNTH.X/); assert.match(it, /EMPTY.X/);
  assert.match(it, /50,00/); assert.match(it, /\+25,0%/); assert.match(it, /USD/);
  assert.doesNotMatch(it, /AGGIORNA MODELLO|REFRESH MODEL|SBLOCCA|UNLOCK|variante personale|fair value|Fair value/);
  assert.match(it, /Original evidence-backed assumption/); assert.match(it, /Original downside scenario/);
  assert.match(it, /Original business risk/); assert.match(it, /Original disclosed gap/);
  assert.match(it, /href="https:\/\/issuer.example\/filing"/);
  assert.doesNotMatch(it, /href="javascript:/);
  assert.deepEqual(api.calls, ['list', 'detail:SYNTH.X']);
  const button = render.nodes.find(node => node.type === 'button' && node.props['aria-label'] === 'EMPTY.X');
  assert.ok(button); button.props.onClick();
  await render.settle('it');
  assert.ok(api.calls.includes('detail:EMPTY.X'));
});

test('missing source dates stay unknown and a stale or mismatched upside is never displayed', async () => {
  const row = fixture();
  row.consensus = {...row.consensus, status: 'stale', acquired_at: '2029-01-01T00:00:00Z', number_of_analysts: null};
  row.comparison = {upside_pct: 999, status: 'stale'};
  const render = retained(fakeApi([row]));
  const en = await render.settle('en');
  assert.match(en, /Analyst consensus/); assert.match(en, /Stale/); assert.match(en, /Provider date/);
  assert.match(en, /Provider date<\/span><span>n\/a/); assert.doesNotMatch(en, /999/);
  assert.match(en, /Acquired/); assert.match(en, /Synthetic analyst provider/);
});

test('archive is read only, loaded only on request, and offers no build or refresh', async () => {
  const api = fakeApi([fixture()]);
  api.fundamentalsArchive = async () => { api.calls.push('archive'); return {items: [{id: 'frozen', file: 'VAL_SYNTH_ARCHIVED.xlsx',
    ticker: 'SYNTH.X', historical: true, as_of: '2025-12-31', available: true, integrity: 'recorded_hash',
    download_path: '/fundamentals/archive/frozen/download'}], notices: []}; };
  const render = retained(api);
  await render.settle('en'); assert.ok(!api.calls.includes('archive'));
  render.nodes.find(node => node.type === 'button' && node.props['aria-controls'] === 'fund-excel-archive').props.onClick();
  const html = await render.settle('en');
  assert.equal(api.calls.filter(call => call === 'archive').length, 1);
  assert.match(html, /VAL_SYNTH_ARCHIVED.xlsx/); assert.match(html, /Historical/); assert.match(html, /2025-12-31/);
  assert.doesNotMatch(html, /REFRESH MODEL|REGENERATE|BUILD EXCEL|Personal variant/);
});

test('unavailable research stays an error, without claiming an empty followed universe', async () => {
  const api = fakeApi([]); api.fundamentalsResearch = async () => { throw new Error('Original read failure'); };
  const render = retained(api), html = await render.settle('en');
  assert.match(html, /Original read failure/);
  assert.doesNotMatch(html, /No followed companies|0 companies/);
});

test('weekly detail explicitly retains the whole multi-company report', async () => {
  const row = fixture(); row.analysis = {...row.analysis, origin: 'weekly', scope: 'whole_committee_report', judgment: null, summary: null,
    content: {report: 'SYNTH.X original view. SECOND.X original view.', documents: [{id: 'd1', url: 'https://issuer.example/annual', published_at: '2030-02-01'}], data_gaps: [{reason: 'Original document limitation'}]}};
  const render = retained(fakeApi([row])), html = await render.settle('en');
  assert.match(html, /Whole committee report/); assert.match(html, /SECOND.X original view/);
  assert.match(html, /Original document limitation/); assert.match(html, /https:\/\/issuer.example\/annual/);
});

test('polling reads saved data every minute and displays new AI estimates separately from market consensus', async () => {
  const first = fixture(), second = fixture('SECOND.X');
  second.consensus = {...second.consensus,mean:null,currency:null,status:'missing'};
  second.comparison = {upside_pct:null,status:'missing'};
  const api = fakeApi([first,second]), render = retained(api);
  await render.settle('en');
  render.nodes.find(node => node.type === 'button' && node.props['aria-label'] === 'SECOND.X').props.onClick();
  await render.settle('en');
  assert.equal(render.timerCount(),2);
  const before = api.calls.length;
  await render.advance(59999); assert.equal(api.calls.length,before);
  api.fundamentalsCompany = async ticker => { api.calls.push('detail:'+ticker); return {...second,analysis:{...second.analysis,
    summary:'New run estimate 70 USD — original saved AI assumption',as_of:'2030-03-04T12:00:00Z'}}; };
  await render.advance(1);
  const html = await render.settle('en');
  assert.equal(api.calls.length,before+2);
  assert.equal(api.calls.at(-1),'detail:SECOND.X');
  assert.match(html,/New run estimate 70 USD/); assert.match(html,/2030-03-04T12:00:00Z/);
  assert.match(html,/AI.*estimates|estimates.*AI/);
  assert.doesNotMatch(html.split('<tbody>')[1].split('</tbody>')[0],/70/);
  render.unmount(); assert.equal(render.timerCount(),0);
});

test('in-flight reads do not overlap or clear selected text, and a manual refresh reads cache only', async () => {
  const row = fixture(), api = fakeApi([row]), render = retained(api);
  await render.settle('en');
  let finishList, finishDetail;
  api.fundamentalsResearch = () => { api.calls.push('list'); return new Promise(resolve=>finishList=resolve); };
  api.fundamentalsCompany = ticker => { api.calls.push('detail:'+ticker); return new Promise(resolve=>finishDetail=resolve); };
  await render.advance(60000);
  assert.equal(typeof finishDetail,'function');
  let html=await render.settle('en');
  assert.match(html,/Original independent AI conclusion/);
  const count=api.calls.length;
  await render.advance(120000); assert.equal(api.calls.length,count);
  const refresh=render.nodes.find(node=>node.type==='button' && node.props['aria-label']==='Read saved data again');
  assert.ok(refresh); assert.equal(refresh.props.disabled,true);
  refresh.props.onClick(); await render.effects(); assert.equal(api.calls.length,count);
  finishList({status:'available',items:[row],count:1,notices:[]});
  finishDetail({...row,analysis:{...row.analysis,summary:'Newest saved conclusion'}});
  await new Promise(resolve=>setImmediate(resolve));
  html=await render.settle('en'); assert.match(html,/Newest saved conclusion/);
  render.unmount(); await render.advance(60000); assert.equal(api.calls.length,count);
});

test('late detail response cannot replace a new selection and unmount invalidates pending responses', async () => {
  const first=fixture(),second=fixture('SECOND.X'),api=fakeApi([first,second]),render=retained(api);
  await render.settle('en');
  let finishOld;
  api.fundamentalsCompany=ticker=>{api.calls.push('detail:'+ticker);return ticker===first.ticker
    ?new Promise(resolve=>finishOld=resolve):Promise.resolve({...second,analysis:{...second.analysis,summary:'Selected second company'}});};
  await render.advance(60000);assert.equal(typeof finishOld,'function');
  await render.settle('en');
  render.nodes.find(node=>node.type==='button'&&node.props['aria-label']==='SECOND.X').props.onClick();
  let html=await render.settle('en');assert.match(html,/Selected second company/);
  finishOld({...first,analysis:{...first.analysis,summary:'Late previous company'}});
  await new Promise(resolve=>setImmediate(resolve));
  html=await render.settle('en');assert.match(html,/Selected second company/);assert.doesNotMatch(html,/Late previous company/);
  let finishPending;
  api.fundamentalsCompany=()=>new Promise(resolve=>finishPending=resolve);
  await render.advance(60000);render.unmount();assert.equal(render.timerCount(),0);
  finishPending({...second,analysis:{...second.analysis,summary:'After unmount'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.doesNotMatch(render('en'),/After unmount/);
});

test('missing value and currency show one unavailable marker, while backend and provider failures stay visible', async () => {
  const row=fixture();row.quote={...row.quote,value:null,currency:null,status:'missing'};
  row.consensus={...row.consensus,mean:null,currency:null,status:'missing',reason:'market_observation_required',refresh:{status:'failed',error:'Synthetic target failure',last_attempt_at:'2030-03-04T12:00:00Z'}};
  row.comparison={upside_pct:null,status:'missing'};
  const api=fakeApi([row]);api.fundamentalsResearch=async()=>({status:'available',items:[row],count:1,notices:[],
    market_refresh:{status:'error',last_completed_at:null,error:'Synthetic worker failure'}});
  const render=retained(api),html=await render.settle('en');
  assert.doesNotMatch(html,/n\/a\s*<span[^>]*>n\/a/);
  assert.match(html,/Synthetic worker failure/);assert.match(html,/Synthetic target failure/);
  assert.match(html,/2030-03-04T12:00:00Z/);
  assert.doesNotMatch(html,/market_observation_required/);
  render.unmount();
});

test('provider retry waiting is explicit without exposing worker status codes as user copy', async () => {
  const row=fixture(),api=fakeApi([row]);
  let status={status:'waiting',next_retry_at:'2030-03-04T12:15:00Z',error:null};
  api.fundamentalsResearch=async()=>({status:'available',items:[row],count:1,notices:[],market_refresh:status});
  const render=retained(api);let html=await render.settle('en');
  assert.match(html,/Temporary provider limit: waiting to refresh/);assert.match(html,/2030-03-04T12:15:00Z/);
  status={status:'error',error:'market_update_partial'};
  await render.advance(60000);html=await render.settle('en');
  assert.match(html,/Latest data refresh failed/);assert.doesNotMatch(html,/market_update_partial/);
  render.unmount();
});
