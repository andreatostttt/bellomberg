// Review of Andrea's PR #11 (Preferiti Nuova), 05/10/2026 (Claude Opus 5.5).
// Every test here failed on the PR code before the review fixes.
// WatchlistPage state order: 0 favs, 1 quotes, 2 quoteErr, 3 loading, 4 err, 5 notes, 6 savedNote,
// 7 noteErrors, 8 savingNotes, 9 sel, 10 settore, 11 ordine, 12 testo, 13 filing, 14 filingErr,
// 15 quotesAt, 16 profiloAperto, 17 avviso, 18 aggiungo.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();

/** SSR of the real page and views; setters record into `values` so async handlers can be observed. */
function pagina(seed = {}, { api = {}, stub = {} } = {}) {
  let state = 0;
  const values = {};
  const hooks = { ...React, useEffect() {}, useRef: current => ({ current }),
    useState(initial) {
      const i = state++;
      if (!(i in values)) values[i] = i in seed ? seed[i] : typeof initial === 'function' ? initial() : initial;
      return [values[i], v => { values[i] = typeof v === 'function' ? v(values[i]) : v; }];
    } };
  const carica = creaCaricatore({ stub: {
    react: hooks, 'react-router-dom': { useNavigate: () => () => {} },
    '@/lib/api': { Bellomberg: api }, ...stub,
  } });
  const lingua = carica('i18n/lingua.ts');
  const Page = carica('pages/WatchlistPage.tsx').default;
  return { values, render: selected => {
    state = 0;
    lingua.impostaLinguaCorrente(selected);
    return renderToStaticMarkup(React.createElement(Page));
  } };
}

const FAV = [{ ticker: 'SYNTH.X', name: 'Synthetic issuer', sector: 'Synthetic sector', note: '', added_at: '2026-01-02T00:00:00' }];
const QUOTE = { 'SYNTH.X': { ticker: 'SYNTH.X', name: 'Synthetic issuer', price: 12, prev_close: 10, currency: 'EUR' } };

// /filings?ambito=preferiti leaves out every favorite that is also in the portfolio (filing_routes.overview):
// a ticker missing from that answer is NOT «not monitored», it is a status the answer did not carry.
test('a favorite absent from the filing answer is declared n/a, never «not monitored»', () => {
  const { render } = pagina({ 0: FAV, 1: QUOTE, 13: {} });
  const it = render('it'), en = render('en');
  assert.doesNotMatch(it, /Nessun monitoraggio|Attiva in Filing|non segue ancora/);
  assert.doesNotMatch(en, /Not monitored|Activate in Filing|does not follow/);
  assert.match(it, /Stato del filing n\.d\./);
  assert.match(en, /Filing status N\/A/);
  assert.match(it, /portafoglio/); assert.match(en, /portfolio/);
});

// Yahoo answers recommendationKey «none» when no analyst covers the name: that is not a rating.
test('a «none» analyst recommendation reads as no coverage, not as a rating', () => {
  const q = { 'SYNTH.X': { ...QUOTE['SYNTH.X'], recommendation: 'none' } };
  const { render } = pagina({ 0: FAV, 1: q });
  for (const html of [render('it'), render('en')]) assert.doesNotMatch(html, />none</);
  assert.match(render('en'), /Analyst rating<\/span><span class="pf-tk-v">N\/A<\/span><span class="pf-tk-s">no coverage/);
});

// While the quotes are still being read the row must not already say «n.d.».
test('a row whose quote is still pending does not claim n/a', () => {
  const { render } = pagina({ 0: FAV, 1: {}, 2: {}, 3: true });
  for (const [lang, nd] of [['it', /n\.d\./], ['en', /N\/A/]]) {
    const riga = render(lang).match(/<button type="button" class="pf-row"[\s\S]*?<\/button>/)[0];
    assert.doesNotMatch(riga, nd, lang);
    assert.match(riga, /…/);
  }
});

// The time shown is when the app READ the quotes (backend cache up to 5 minutes), not a market timestamp.
test('the quotes time is labelled as a read time', () => {
  const { render } = pagina({ 0: FAV, 1: QUOTE, 15: '14:02' });
  assert.match(render('it'), /Quotazioni lette alle 14:02/);
  assert.match(render('en'), /Quotes read at 14:02/);
});

// Yahoo dividendYield is the forward (indicated) annual yield, not a trailing-12-months figure.
test('the dividend tile does not claim a trailing 12 months yield', () => {
  const { render } = pagina({ 0: FAV, 1: QUOTE });
  assert.doesNotMatch(render('en'), /trailing 12 months/);
  assert.doesNotMatch(render('it'), /Dividendo<\/span>[\s\S]{0,120}ultimi 12 mesi/);
});

// Adding a favorite whose quote read fails left the detail on «Reading the quote…» forever.
test('adding a favorite whose quote read fails declares the missing quote', async () => {
  let viste = null;
  const api = {
    mktQuote: () => Promise.reject(new Error('Synthetic quote outage')),
    favAdd: async () => ({ ok: true }),
    filingOverviewAmbito: () => new Promise(() => {}),
  };
  const stub = {
    '@/components/ModernPage': { __esModule: true, default: ({ render }) => render() },
    './preferiti/VistaPreferiti': { __esModule: true, default: props => { viste = props; return null; } },
  };
  const { values, render } = pagina({ 0: [], 1: {}, 2: {} }, { api, stub });
  render('en');
  await viste.onAdd({ symbol: 'SYNTH.Y', name: 'Synthetic Y', exchange: 'SYN', type: 'EQUITY' });
  assert.ok(values[0].some(f => f.ticker === 'SYNTH.Y'), 'favorite added');
  assert.match(values[2]['SYNTH.Y'] || '', /Synthetic quote outage/);
});
