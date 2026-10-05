// Review of Andrea's PR #14 (Shell Nuova), Opus 5.5.
// 1) The Decisions badge in the sidebar: a failed read, or a reply without the list, must be a
//    declared gap (N.D.), not the same empty space as "no decisions to make" (rule 14/07).
//    Exercised through the real Layout, so the wiring of the labels is tested too.
// 2) The Dashboard run notice that the PR moves into the top bar: it must stay visible at
//    narrow widths and shrink instead of pushing search and system status out of the window.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { MemoryRouter } = require('react-router-dom');
const { creaCaricatore, ambienteBrowser, apiFinta, SRC } = require('./_carica.cjs');

function shellWithDecisions(decisions) {
  ambienteBrowser();
  const oldWindow = global.window;
  global.window = { addEventListener() {}, removeEventListener() {}, dispatchEvent() {} };
  const state = [], effects = [], deps = [], cleanups = [];
  let si = 0, ei = 0;
  const hooks = { ...React,
    useState(initial) {
      const at = si++;
      if (!(at in state)) state[at] = typeof initial === 'function' ? initial() : initial;
      return [state[at], value => { state[at] = typeof value === 'function' ? value(state[at]) : value; }];
    },
    useEffect(fn, values) {
      const at = ei++, before = deps[at];
      if (!before || !values || values.some((v, i) => !Object.is(v, before[i]))) effects.push(fn);
      deps[at] = values;
    },
  };
  const api = apiFinta();
  const Bellomberg = new Proxy({}, { get: (_t, name) => (name === 'decisions' ? decisions : api.Bellomberg[name]) });
  const load = creaCaricatore({ stub: { react: hooks, '@/lib/api': { ...api, Bellomberg },
    './SettingsPanel': { default: () => null, __esModule: true } } });
  const language = load('i18n/lingua.ts'), Layout = load('components/Layout.tsx').default;
  const render = lang => {
    si = ei = 0; effects.length = 0; language.impostaLinguaCorrente(lang);
    const previousError = console.error;
    console.error = (...args) => {
      if (!String(args[0]).includes('useLayoutEffect does nothing on the server')) previousError(...args);
    };
    try { return renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/dashboard'] }, React.createElement(Layout))); }
    finally { console.error = previousError; }
  };
  const oldSet = global.setInterval, oldClear = global.clearInterval;
  global.setInterval = () => 0; global.clearInterval = () => {};
  return {
    render,
    runEffects() { for (const effect of effects.splice(0)) { const c = effect(); if (c) cleanups.push(c); } },
    flush: () => new Promise(resolve => setImmediate(resolve)),
    close() { cleanups.splice(0).forEach(fn => fn()); global.setInterval = oldSet; global.clearInterval = oldClear; global.window = oldWindow; },
  };
}

const badge = html => (html.match(/<b [^>]*data-decisions-badge="[^"]*"[^>]*>[^<]*<\/b>/) || [''])[0];

for (const [name, decisions] of [
  ['a failed read', () => Promise.reject(new Error('synthetic decisions failure'))],
  ['a reply without the decisions list', () => Promise.resolve({ error: 'synthetic' })],
]) test(`sidebar Decisions badge declares ${name} as N.D. in both languages`, async () => {
  const shell = shellWithDecisions(decisions);
  try {
    shell.render('en');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    for (const [lang, nd, label] of [
      ['en', 'N/A', 'Decisions to make: count unavailable'],
      ['it', 'N.D.', 'Decisioni da prendere: conteggio non leggibile'],
    ]) {
      const b = badge(shell.render(lang));
      assert.match(b, /data-decisions-badge="nd"/, `${lang}: the gap is declared, not hidden: ${b}`);
      assert.ok(b.includes(`aria-label="${label}"`), `${lang}: ${b}`);
      assert.ok(b.includes(`>${nd}</b>`), `${lang}: ${b}`);
    }
  } finally { shell.close(); }
});

test('sidebar Decisions badge shows the pending count, and nothing when there are none', async () => {
  for (const [list, expected] of [[[{ id: 1 }, { id: 2 }, { id: 3 }], '3'], [[], null]]) {
    const shell = shellWithDecisions(() => Promise.resolve({ decisions: list }));
    try {
      shell.render('en');
      shell.runEffects();
      await shell.flush(); await shell.flush();
      const b = badge(shell.render('it'));
      if (expected == null) assert.equal(b, '', 'zero pending decisions: no badge');
      else {
        assert.match(b, new RegExp(`data-decisions-badge="${expected}"`));
        assert.ok(b.includes(`aria-label="${expected} decisioni da prendere"`), b);
      }
    } finally { shell.close(); }
  }
});

test('the run notice brought into the top bar stays visible and shrinks at narrow widths', () => {
  const css = fs.readFileSync(path.join(SRC, 'components/shell-modern.css'), 'utf8');
  const hides = [...css.matchAll(/([^{}]*\.bb-barra-ospite > header > :not\(([^)]*)\)[^{}]*)\{([^}]*)\}/g)]
    .filter(m => /display:\s*none/.test(m[3]));
  assert.ok(hides.length > 0, 'the narrow-width rule that hides the page context is still the one under test');
  for (const m of hides) assert.match(m[2], /\.bbn-run/, `narrow rule must spare the run notice: ${m[1].trim()}`);
  const shrink = css.match(/\.bb-barra-ospite > header > \.bbn-run\s*\{([^}]*)\}/);
  assert.ok(shrink, 'the run notice has its own rule in the top bar');
  assert.match(shrink[1], /flex:\s*0 1 auto/);
  assert.match(shrink[1], /min-width:\s*0/);
  const dashboard = fs.readFileSync(path.join(SRC, 'pages/Dashboard.tsx'), 'utf8');
  assert.match(dashboard, /className=\{'bbn-run'[^>]*title=\{runText\}/, 'a shortened notice keeps its full text in the tooltip');
});
