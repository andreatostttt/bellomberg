// 04/10/2026 (G9b): POST /news/feed/refresh risponde subito {accepted, job}. La pagina Notizie e la
// palette comandi dicono «in corso» finché il job gira, l'esito vero (conteggi, n.d. se assenti) solo
// a giro finito, e accepted:false col motivo: mai l'esito del giro VECCHIO come se fosse nuovo.
// Si clicca davvero il pulsante: il jsx-runtime è avvolto per catturare l'onClick (nessun browser).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const runtime = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.window = globalThis.window || { setTimeout: () => 0, clearTimeout() {}, dispatchEvent() {}, addEventListener() {}, removeEventListener() {} };
const tick = () => new Promise(resolve => setImmediate(resolve));

function statiDi(file, nome) {
  const source = fs.readFileSync(path.resolve(__dirname, '../../src', file), 'utf8');
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const fn = tree.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === nome);
  const nomi = [];
  (function visita(node) {
    if (ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name) && node.initializer && ts.isCallExpression(node.initializer)
      && node.initializer.expression.getText(tree) === 'useState') nomi.push(node.name.elements[0].name.text);
    ts.forEachChild(node, visita);
  })(fn);
  return nomi;
}

/** Monta un componente con hook simulati, API finta e cattura degli onClick per `scegli(props)`. */
function monta(file, nome, seed, responses, scegli) {
  const stateNames = statiDi(file, nome);
  let index = 0, memoIndex = 0, refIndex = 0, effectIndex = 0;
  const values = {}, memos = [], refs = [], effectDeps = [], effects = [], requests = [], clic = [];
  for (const [k, v] of Object.entries(seed)) { assert.ok(stateNames.includes(k), k); values[stateNames.indexOf(k)] = v; }
  const memo = (compute, deps) => {
    const at = memoIndex++, old = memos[at];
    if (!old || !deps || deps.some((value, i) => !Object.is(value, old.deps[i]))) memos[at] = { deps, value: compute() };
    return memos[at].value;
  };
  const api = new Proxy({}, { get: (_t, name) => async (...args) => {
    requests.push(name);
    if (name in responses) return responses[name](...args);
    return new Promise(() => {});
  } });
  const cattura = f => (type, props, ...rest) => { if (props && typeof props.onClick === 'function' && scegli(props)) clic.push(props.onClick); return f(type, props, ...rest); };
  const load = creaCaricatore({ stub: {
    react: { ...React, useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps), useLayoutEffect() {},
      useState(initial) { const at = index++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
        return [values[at], v => { values[at] = typeof v === 'function' ? v(values[at]) : v; }]; },
      useRef(current) { return refs[refIndex++] ||= { current }; },
      useEffect(effect, deps) { const at = effectIndex++, before = effectDeps[at];
        if (!before || !deps || deps.some((v, i) => !Object.is(v, before[i]))) effects.push(effect);
        effectDeps[at] = deps; },
    },
    'react/jsx-runtime': { ...runtime, jsx: cattura(runtime.jsx), jsxs: cattura(runtime.jsxs) },
    'react-router-dom': { useNavigate: () => () => {} },
    '@/lib/api': { Bellomberg: api, API_BASE: '' },
    '../lib/api': { Bellomberg: api, API_BASE: '' },
    'react-markdown': props => React.createElement('div', {}, props.children), 'remark-gfm': () => {},
  } });
  const language = load('i18n/lingua.ts'), Component = load(file).default;
  return {
    requests, values, stateNames, clic,
    stato(k) { return values[stateNames.indexOf(k)]; },
    render(lang = 'it') { index = memoIndex = refIndex = effectIndex = 0; effects.length = 0; clic.length = 0;
      language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Component)); },
  };
}

const vecchio = { id: 'job-old', status: 'success', trigger: 'schedule', started_at: '2026-10-04T08:00:00', finished_at: '2026-10-04T08:01:00', result: { saved: 7, skipped_duplicates: 2 } };
const inCorso = (id, trigger = 'manual') => ({ id, status: 'running', trigger, started_at: '2026-10-04T09:30:00', finished_at: null, result: null });

test('News page: accepted:false with a finished job shows the rejection, never the old round counts', async () => {
  const p = monta('pages/NewsPage.tsx', 'NewsPage', {}, {
    newsFeedRefresh: async () => ({ accepted: false, job: vecchio }),
    newsRefreshJob: async () => ({ job: vecchio }),
  }, props => props['data-qa'] === 'news-refresh');
  p.render('it');
  assert.equal(p.clic.length, 1, 'refresh button found');
  p.clic[0](); await tick(); await tick();
  const html = p.render('it');
  assert.doesNotMatch(html, /Salvate 7 notizie/);
  assert.match(html, /il backend non ha avviato il giro/);
  assert.equal(p.stato('refreshInfo'), null);
});

test('News page: a running job shows «in progress», the result only once the job is done (n/a when a count is missing)', async () => {
  const letture = [inCorso('job-9'), { ...inCorso('job-9'), status: 'success', finished_at: '2026-10-04T09:31:00', result: { saved: 3 } }];
  const p = monta('pages/NewsPage.tsx', 'NewsPage', {}, {
    newsFeedRefresh: async () => ({ accepted: true, job: inCorso('job-9') }),
    newsRefreshJob: async () => ({ job: letture.shift() ?? letture[0] }),
  }, props => props['data-qa'] === 'news-refresh');
  const attese = [];
  const originale = globalThis.window.setTimeout;
  globalThis.window.setTimeout = (fn) => { attese.push(fn); return 0; };
  try {
    p.render('it');
    p.clic[0](); await tick();
    let html = p.render('it');
    assert.match(html, /Giro dei provider in corso dalle/);
    assert.doesNotMatch(html, /Salvate/);
    while (attese.length) { attese.shift()(); await tick(); await tick(); }
    html = p.render('it');
    assert.match(html, /Salvate 3 notizie nuove, n\.d\. già presenti/);
    const en = p.render('en');
    assert.match(en, /Saved 3 new items, n\/a already stored/);
  } finally { globalThis.window.setTimeout = originale; }
});

test('News page: a round already running is followed and declared as not started by this click', async () => {
  const p = monta('pages/NewsPage.tsx', 'NewsPage', {}, {
    newsFeedRefresh: async () => ({ accepted: false, job: inCorso('job-t', 'schedule') }),
    newsRefreshJob: async () => ({ job: { ...inCorso('job-t', 'schedule'), status: 'success', result: { saved: 0, skipped_duplicates: 4 } } }),
  }, props => props['data-qa'] === 'news-refresh');
  const originale = globalThis.window.setTimeout;
  const attese = [];
  globalThis.window.setTimeout = (fn) => { attese.push(fn); return 0; };
  try {
    p.render('it');
    p.clic[0](); await tick();
    assert.match(p.render('it'), /Un giro era già in corso \(avviato da il timer del backend/);
    while (attese.length) { attese.shift()(); await tick(); await tick(); }
    assert.match(p.render('it'), /Esito del giro già in corso \(non avviato da questo clic\): Salvate 0 notizie nuove, 4 già presenti/);
  } finally { globalThis.window.setTimeout = originale; }
});

test('Command palette: NEWS UPDATED only after the job ends, with real counts; a rejection says why', async () => {
  const sceltaNews = props => props.role === 'option' && Array.isArray(props.children) && props.children[0]?.props?.children === 'NEWS';
  // 1) rifiuto: nessun «NOTIZIE AGGIORNATE»
  const r = monta('components/CommandPalette.tsx', 'CommandPalette', { open: true }, {
    portfolio: async () => ({ positions: [] }),
    newsFeedRefresh: async () => ({ accepted: false, job: vecchio }),
    newsRefreshJob: async () => ({ job: vecchio }),
  }, sceltaNews);
  r.render('it'); assert.equal(r.clic.length, 1, 'NEWS command found');
  r.clic[0](); await tick(); await tick();
  let html = r.render('it');
  assert.doesNotMatch(html, /NOTIZIE AGGIORNATE/);
  assert.match(html, /ERRORE: il backend non ha avviato il giro/);
  // 2) giro avviato: «in corso» poi l'esito vero
  const attese = [];
  const originale = globalThis.window.setTimeout;
  globalThis.window.setTimeout = (fn) => { attese.push(fn); return 0; };
  try {
    const letture = [{ ...inCorso('job-p'), status: 'success', finished_at: '2026-10-04T09:31:00', result: { saved: 2, skipped_duplicates: 5 } }];
    const p = monta('components/CommandPalette.tsx', 'CommandPalette', { open: true }, {
      portfolio: async () => ({ positions: [] }),
      newsFeedRefresh: async () => ({ accepted: true, job: inCorso('job-p') }),
      newsRefreshJob: async () => ({ job: letture[0] }),
    }, sceltaNews);
    p.render('it'); p.clic[0](); await tick();
    html = p.render('it');
    assert.match(html, /NOTIZIE: GIRO IN CORSO/);
    assert.doesNotMatch(html, /NOTIZIE AGGIORNATE/);
    attese.shift()(); await tick(); await tick();
    html = p.render('it');
    assert.match(html, /NOTIZIE AGGIORNATE: Salvate 2 notizie nuove, 5 già presenti/);
  } finally { globalThis.window.setTimeout = originale; }
});

test('News page (contract G3): an interrupted round is declared, not shown as a result; not_classified is reported', async () => {
  const run = async (finale) => {
    const p = monta('pages/NewsPage.tsx', 'NewsPage', {}, {
      newsFeedRefresh: async () => ({ accepted: true, job: inCorso('job-i') }),
      newsRefreshJob: async (id) => { assert.equal(id, 'job-i'); return { job: { ...inCorso('job-i'), ...finale } }; },
    }, props => props['data-qa'] === 'news-refresh');
    const attese = [], originale = globalThis.window.setTimeout;
    globalThis.window.setTimeout = (fn) => { attese.push(fn); return 0; };
    try {
      p.render('it'); p.clic[0](); await tick();
      while (attese.length) { attese.shift()(); await tick(); await tick(); }
      return p.render('it');
    } finally { globalThis.window.setTimeout = originale; }
  };
  const interrotto = await run({ status: 'interrupted', error: 'backend sintetico fermato', result: { saved: 2 } });
  assert.match(interrotto, /il giro è stato interrotto prima della fine/);
  assert.match(interrotto, /backend sintetico fermato/);
  assert.doesNotMatch(interrotto, /Salvate 2 notizie/);
  const parziale = await run({ status: 'success', result: { saved: 5, skipped_duplicates: 1, not_classified: 3 } });
  assert.match(parziale, /Salvate 5 notizie nuove, 1 già presenti · 3 non classificate/);
});

// Revisione G9b R1: la palette non resta bloccata su «GIRO IN CORSO»; dopo la scadenza il comando riparte.
test('Command palette: a round that never ends is declared after the cap and NEWS can be run again', async () => {
  const sceltaNews = props => props.role === 'option' && Array.isArray(props.children) && props.children[0]?.props?.children === 'NEWS';
  let avvii = 0, clock = 0;
  const p = monta('components/CommandPalette.tsx', 'CommandPalette', { open: true }, {
    portfolio: async () => ({ positions: [] }),
    newsFeedRefresh: async () => { avvii += 1; return { accepted: true, job: inCorso('job-eterno') }; },
    newsRefreshJob: async () => ({ job: inCorso('job-eterno') }),
  }, sceltaNews);
  const origT = globalThis.window.setTimeout, origNow = performance.now;
  const attese = [];
  globalThis.window.setTimeout = (fn, ms) => { attese.push(() => { clock += ms; fn(); }); return 0; };
  performance.now = () => clock;
  try {
    p.render('it'); p.clic[0](); await tick();
    let giri = 0;
    while (attese.length && giri++ < 1000) { attese.shift()(); await tick(); await tick(); }
    const html = p.render('it');
    assert.match(html, /ERRORE: stato non più aggiornato dal server dopo 10 minuti/);
    p.clic[0](); await tick();
    assert.equal(avvii, 2, 'the NEWS command is free again');
  } finally { globalThis.window.setTimeout = origT; performance.now = origNow; }
});
