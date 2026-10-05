// 03/10/2026 (filing fase E): il pannello filing di Mercati e Fondamentali è diventato un riepilogo di
// una riga con link alla pagina Filing; le funzioni (attivazione, verifica, profilo, proposta AI) sono
// nella pagina (tests/i18n/filing-page.test.cjs). Dati sintetici.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

const profilo = { ticker: 'SYNTH.X', version: 2, enabled: true, interval_hours: 24, qualitative_enabled: false,
  profile: { tipo: 'trimestrale', cik: '0009990001', forme_sec: ['10-Q'] } };
const stato = (extra = {}) => ({ ticker: 'SYNTH.X', status: 'ok', profile: profilo, runs: [], active_run: null,
  ultimo_completo: { id: 7, ticker: 'SYNTH.X', status: 'ok' }, ...extra });
const run = { id: 7, ticker: 'SYNTH.X', status: 'ok', finished_at: '2026-10-03T08:10:00', result: { stato: 'ok', variante: 'trimestrale',
  coppia: { prima: { metadati: { periodo_fine: '2025-09-30' } }, dopo: { metadati: { periodo_fine: '2026-09-30' } } },
  confronto_corrente: { stato: 'ok', cambiamenti: [{ tipo: 'aggiunto' }, { tipo: 'rimosso' }, { tipo: 'modificato' }] } } };

function riepilogo(api) {
  let si = 0, ei = 0;
  const states = [], deps = [], pending = [], calls = [];
  const spia = new Proxy({}, { get: (_t, nome) => (...args) => { calls.push(nome); return api[nome] ? api[nome](...args) : new Promise(() => {}); } });
  const load = creaCaricatore({ stub: {
    react: { ...React,
      useState(initial) { const at = si++; if (!(at in states)) states[at] = typeof initial === 'function' ? initial() : initial;
        return [states[at], v => { states[at] = typeof v === 'function' ? v(states[at]) : v; }]; },
      useEffect(fn, d) { const at = ei++, before = deps[at];
        if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) pending.push(fn); deps[at] = d; },
    },
    '@/lib/api': { Bellomberg: spia },
  } });
  const language = load('i18n/lingua.ts'), Component = load('components/FilingRiepilogo.tsx').default;
  const render = (ticker = 'SYNTH.X', lang = 'it') => { si = ei = 0; language.impostaLinguaCorrente(lang);
    return renderToStaticMarkup(React.createElement(Component, { ticker })); };
  const ready = async (ticker = 'SYNTH.X', lang = 'it') => {
    for (let i = 0; i < 3; i++) { render(ticker, lang); for (const fn of pending.splice(0)) fn(); await new Promise(r => setImmediate(r)); }
    return render(ticker, lang);
  };
  return { render, ready, calls };
}

test('una riga: documento, periodi, cambiamenti, data e link alla pagina Filing, in IT ed EN', async () => {
  const it = await riepilogo({ filingList: async () => stato(), filingRun: async () => run }).ready('SYNTH.X', 'it');
  assert.match(it, /SEC 10-Q · Q3 2026 vs Q3 2025 · 3 cambiamenti · confronto del 03\/10/);
  assert.match(it, /href="#\/filing\?t=SYNTH\.X"/); assert.match(it, /Apri in Filing/);
  assert.match(it, /data-stato="ok"/);
  const en = await riepilogo({ filingList: async () => stato(), filingRun: async () => run }).ready('SYNTH.X', 'en');
  assert.match(en, /3 changes/); assert.match(en, /Open in Filings/);
});

test('senza profilo, in corso, in errore e archivio illeggibile sono dichiarati', async () => {
  const nessuno = await riepilogo({ filingList: async () => stato({ profile: null, ultimo_completo: null }) }).ready();
  assert.match(nessuno, /nessun profilo: si attiva dalla pagina Filing/); assert.match(nessuno, /data-stato="off"/);
  const corre = await riepilogo({ filingList: async () => stato({ active_run: { id: 8, ticker: 'SYNTH.X', status: 'running' } }), filingRun: async () => run }).ready();
  assert.match(corre, /controllo in corso/);
  const errore = await riepilogo({ filingList: async () => stato({ ultimo_errore: { id: 9, at: null, reason: 'Sezione non trovata' } }), filingRun: async () => run }).ready('SYNTH.X', 'en');
  assert.match(errore, /latest check failed: Sezione non trovata/); assert.match(errore, /data-stato="bad"/);
  const rotto = await riepilogo({ filingList: async () => { throw { response: { data: { detail: 'Archivio filing non disponibile: guasto sintetico' } } }; } }).ready();
  assert.match(rotto, /guasto sintetico/); assert.match(rotto, /role="alert"/);
});

test('sola lettura: nessuna scrittura, nessuna AI; la risposta di un altro titolo non sostituisce quello scelto', async () => {
  const v = riepilogo({ filingList: async () => ({ ...stato(), ticker: 'OTHER.X' }) });
  const html = await v.ready('SYNTH.X');
  assert.match(html, /Carico lo stato dei filing/);
  assert.deepEqual([...new Set(v.calls)], ['filingList']);
});

test('run dell\'ultimo confronto illeggibile: dichiarato, non «in attesa del primo confronto»', async () => {
  const html = await riepilogo({ filingList: async () => stato(), filingRun: async () => { throw new Error('Run sintetico illeggibile'); } }).ready();
  assert.match(html, /esito dell’ultimo confronto non leggibile: Run sintetico illeggibile/);
  assert.doesNotMatch(html, /in attesa del primo confronto/);
  assert.match(html, /data-stato="bad"/);
});
