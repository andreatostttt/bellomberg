// 06/10/2026 (PNL-F): riga «Differenza col P&L contabile» in Altre metriche, vista Tutto, e statistiche
// che partono da `indice_statistiche_da` (punto base al costo escluso da drawdown, Sharpe, mensili).
// Payload SINTETICI (interi, nessun numero del book): differenza = performance − contabile dal payload,
// voci, cassa e residuo nel tooltip; stato non verde = detto in chiaro, mai un «residuo» che sembri a posto;
// residuo n.d. col motivo del backend; campo assente = nessuna riga / comportamento di prima.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
globalThis.window = globalThis.window || { setTimeout: () => 0, clearTimeout() {}, dispatchEvent() {}, addEventListener() {}, removeEventListener() {} };
const load = creaCaricatore({ stub: { '@/lib/api': { Bellomberg: {} } } });
const lingua = load('i18n/lingua.ts');
const VistaScheda = load('pages/performance/VistaScheda.tsx').default;

const date = ['2026-09-01', '2026-09-02', '2026-09-03'];
const verde = {
  pnl_performance_eur: 1200, pnl_performance_esatto_eur: 1200, pnl_contabile_eur: 1150,
  contabile: { data: '2026-09-03', non_realizzato_eur: 900, realizzato_eur: 200, dividendi_eur: 50 },
  voci: [
    { voce: 'arrotondamento_indice', importo_eur: 0, stato: 'misurata', nota: 'nota sintetica A' },
    { voce: 'ultimo_punto', importo_eur: -20, stato: 'misurata', nota: 'nota sintetica B' },
    { voce: 'voce_futura', importo_eur: 0, stato: 'misurata' },
  ],
  cassa: { variazione_osservata_eur: 300, variazione_attesa_eur: 230, versamenti_ledger_eur: 500, acquisti_meno_vendite_eur: 400,
    dividendi_incassati_eur: 130, interessi_eur: null, non_spiegata_eur: 70, nota: 'nota cassa' },
  controllo_cassa: { scarto_eur: 0, stato: 'coerente' },
  residuo_eur: 70, residuo_stato: 'riconciliato_entro_tolleranza', stato: 'riconciliato_entro_tolleranza',
  tolleranza_residuo_eur: 100, soglia_ultimo_punto_pct: 1, motivo_nd: null, nota: 'nota generale',
};
const fuori = { ...verde, residuo_eur: 470, residuo_stato: 'non_riconciliato', stato: 'non_riconciliato', pnl_performance_eur: 1600 };
const daVerificare = { ...verde, stato: 'da_verificare',
  voci: verde.voci.map(v => (v.voce === 'ultimo_punto' ? { ...v, importo_eur: -900, stato: 'da_verificare' } : v)) };
const incoerente = { ...verde, stato: 'incoerente', controllo_cassa: { scarto_eur: 3, stato: 'incoerente' } };
const ignoto = { ...verde, stato: 'stato_futuro' };
const nonRiconciliata = { ...verde, pnl_contabile_eur: null, contabile: null, voci: [], cassa: null, controllo_cassa: null,
  residuo_eur: null, residuo_stato: 'n.d.', stato: 'n.d.', motivo_nd: 'motivo sintetico del backend' };
const twrCon = (r, extra = {}) => {
  const t = { dates: date, twr_index: [100, 101, 102], values_eur: [1000, 1010, 1020], flows_eur: [0, 0, 0],
    regimes: date.map(() => 'official'), metrics: { risk_free_used: 0.03, twr_total_pct: 2 }, ...extra };
  if (r !== undefined) t.riconciliazione_pnl = r;
  return t;
};
const props = (r, periodo = 'Tutto', extra) => ({
  periodo, onPeriodo() {}, twr: { stato: 'ok', dati: twrCon(r, extra) }, spy: { stato: 'attesa' }, avanzate: { stato: 'attesa' },
  contabilita: { stato: 'ok', dati: { valoreMercato: 5000, costo: 4000, nonRealizzato: 900, realizzato: 200, dividendi: 50, cassa: 500 } },
  nav: { valore: 5500, posizioni: 3 }, attribuzione: { stato: 'attesa' }, nomi: {},
});
const html = (r, periodo, extra) => renderToStaticMarkup(React.createElement(VistaScheda, props(r, periodo, extra)));
const sp = '[\\s\\u00a0]';
/** tooltip (title) dell'etichetta della riga di riconciliazione */
const tooltip = (h, etichetta) => {
  const m = h.match(new RegExp('<span title="([^"]*)">' + etichetta + '</span><b><span data-qa="perf-pnl-recon"'));
  assert.ok(m, 'riga di riconciliazione assente');
  return m[1];
};
const stati = h => [...h.matchAll(/data-qa="perf-pnl-recon-stato">([^<]*)<\/p>/g)].map(m => m[1]);

test('it · vista Tutto, riconciliato entro tolleranza: differenza, residuo, voci e cassa nel tooltip', () => {
  lingua.impostaLinguaCorrente('it');
  const h = html(verde);
  assert.match(h, new RegExp('<span data-qa="perf-pnl-recon" data-stato="riconciliato_entro_tolleranza"><span class="up-t">\\+50,00' + sp + '€</span><small>residuo \\+70,00' + sp + '€</small>'));
  assert.deepEqual(stati(h), []);
  const t = tooltip(h, 'Differenza col contabile');
  assert.match(t, new RegExp('Rendimento dall’inizio \\+1\\.200,00' + sp + '€ − P&amp;L contabile al 3 set \\+1\\.150,00' + sp + '€'));
  assert.match(t, new RegExp('Prezzi dell’ultimo punto: −20,00' + sp + '€ — nota sintetica B'));
  assert.match(t, new RegExp('Arrotondamento dell’indice: 0,00' + sp + '€'));
  // voce che la pagina non conosce: il codice del backend, mai un nome inventato
  assert.match(t, new RegExp('voce_futura: 0,00' + sp + '€'));
  assert.match(t, new RegExp('Cassa del tratto ufficiale: osservata \\+300,00' + sp + '€, attesa \\+230,00' + sp + '€ .*non spiegata \\+70,00' + sp + '€, resta nel residuo'));
  assert.match(t, new RegExp('Residuo misurato: \\+70,00' + sp + '€ \\(tolleranza ±100,00' + sp + '€\\)'));
});

test('it · stato non verde: la parola accanto alla cifra e la frase sotto, mai «residuo»', () => {
  lingua.impostaLinguaCorrente('it');
  const casi = [
    [fuori, 'non riconciliato', [new RegExp('^Non riconciliato: residuo \\+470,00' + sp + '€ oltre la tolleranza di ±100,00' + sp + '€\\.$')]],
    [daVerificare, 'da verificare', [/^Da verificare: Prezzi dell’ultimo punto oltre la soglia del 1,0% delle posizioni\.$/]],
    [incoerente, 'cassa incoerente', [new RegExp('^Controllo cassa incoerente: il residuo differisce di \\+3,00' + sp + '€ dalla cassa non spiegata\\.$')]],
    [ignoto, 'stato_futuro', [/^Stato della riconciliazione non riconosciuto: stato_futuro\.$/]],
  ];
  for (const [r, parola, frasi] of casi) {
    const h = html(r);
    const riga = h.match(/data-qa="perf-pnl-recon" data-stato="[^"]*">(.*?)<\/span><\/b>/)[1];
    assert.match(riga, new RegExp('<small>' + parola + '</small>'), parola);
    assert.doesNotMatch(riga, /residuo/, parola);
    const s = stati(h);
    assert.equal(s.length, frasi.length, parola);
    frasi.forEach((f, i) => assert.match(s[i], f, parola));
  }
  // la voce da verificare è segnata anche nel tooltip
  assert.match(tooltip(html(daVerificare), 'Differenza col contabile'), new RegExp('Prezzi dell’ultimo punto: −900,00' + sp + '€ \\(da verificare\\)'));
});

test('it · residuo n.d.: riga dichiarata col motivo del backend, nessuna cifra', () => {
  lingua.impostaLinguaCorrente('it');
  const h = html(nonRiconciliata);
  assert.match(h, /<p class="perf-state" role="status" data-qa="perf-pnl-recon">Differenza col P&amp;L contabile non riconciliata: motivo sintetico del backend<\/p>/);
  assert.doesNotMatch(h, /residuo /);
  assert.match(html({ ...nonRiconciliata, motivo_nd: null }), /non riconciliata: —<\/p>/);
});

test('it · payload senza riconciliazione o vista non Tutto: nessuna riga', () => {
  lingua.impostaLinguaCorrente('it');
  for (const h of [html(undefined), html(null), html(verde, '1M'), html(fuori, 'YTD')]) {
    assert.doesNotMatch(h, /perf-pnl-recon/);
    assert.doesNotMatch(h, /Differenza col (P&amp;L )?contabile/);
  }
  assert.match(html(undefined), /P&amp;L totale<\/span><b><span class="up-t">\+1\.150,00/);
});

test('en · same row in English: in tolerance, not reconciled, n.d.', () => {
  lingua.impostaLinguaCorrente('en');
  try {
    const h = html(verde);
    assert.match(h, new RegExp('<span class="up-t">\\+50\\.00' + sp + '€</span><small>residual \\+70\\.00' + sp + '€</small>'));
    const t = tooltip(h, 'Gap vs accounting P&amp;L');
    assert.match(t, new RegExp('Return since inception \\+1,200\\.00' + sp + '€ − accounting P&amp;L at 3 Sept \\+1,150\\.00' + sp + '€'));
    assert.match(t, new RegExp('Latest-point prices: −20\\.00' + sp + '€'));
    assert.match(t, new RegExp('Measured residual: \\+70\\.00' + sp + '€ \\(tolerance ±100\\.00' + sp + '€\\)'));
    const f = html(fuori);
    assert.match(f, /<small>not reconciled<\/small>/);
    assert.equal(stati(f).length, 1);
    assert.match(stati(f)[0], new RegExp('^Not reconciled: residual \\+470\\.00' + sp + '€ beyond the ±100\\.00' + sp + '€ tolerance\\.$'));
    assert.match(html(nonRiconciliata), /data-qa="perf-pnl-recon">Difference from accounting P&amp;L not reconciled: motivo sintetico del backend<\/p>/);
    assert.doesNotMatch(html(undefined), /perf-pnl-recon/);
  } finally {
    lingua.impostaLinguaCorrente('it');
  }
});

// ── statistiche da indice_statistiche_da ──
// punto base al costo 100, prima chiusura 80 (perdita maturata prima del book), poi 88 e 84
const dateStat = ['2026-07-31', '2026-08-03', '2026-08-31', '2026-09-30'];
const twrStat = extra => ({ dates: dateStat, twr_index: [100, 80, 88, 84], values_eur: [1000, 800, 880, 840], flows_eur: [1000, 0, 0, 0],
  regimes: dateStat.map(() => 'reconstructed'), metrics: { risk_free_used: 0.03, twr_total_pct: -16 }, ...extra });
// SPY sullo stesso calendario: 100 al punto base, 110 alla prima chiusura, 121 a fine agosto
const spyStat = { stato: 'ok', dati: { date: dateStat, indice: [100, 110, 121, 121] } };
const htmlStat = (extra, spy = { stato: 'attesa' }) => renderToStaticMarkup(React.createElement(VistaScheda,
  { ...props(undefined), spy, twr: { stato: 'ok', dati: twrStat(extra) } }));
const tileDd = h => h.match(/Max drawdown.*?<b class="down-t">([^<]*)<\/b>/)[1];
const celle = (h, riga) => h.match(new RegExp(riga + '<\\/small><\\/td>(.*?)<\\/tr>'))[1].replace(/<span class="perf-mtd">[^<]*<\/span>/g, '')
  .match(/<td[^>]*>[^<]*<\/td>/g).map(c => c.replace(/<[^>]+>/g, ''));
const cellePortafoglio = h => celle(h, 'Portafoglio<small>TWR');

test('statistiche: con indice_statistiche_da drawdown e mensili partono dalla prima chiusura; titolo e totale dal costo', () => {
  lingua.impostaLinguaCorrente('it');
  const con = htmlStat({ indice_statistiche_da: 1 }), senza = htmlStat({});
  const titolo = h => h.match(/data-qa="perf-return"[^>]*>(.*?)<\/div>/)[1].replace(/<[^>]+>/g, '');
  assert.equal(titolo(con), titolo(senza));
  assert.match(titolo(con), /^−16,00%/);
  // drawdown: dal costo 100 → 80 = −20%; dalla prima chiusura picco 88 → 84 = −4,55%
  assert.equal(tileDd(senza), '−20,00%');
  assert.equal(tileDd(con), '−4,55%');
  // mensili: senza il campo agosto si misura dal costo (88/100 − 1); con il campo luglio non è
  // misurato e agosto parte dalla prima chiusura (88/80 − 1 = +10%)
  const s = cellePortafoglio(senza), c = cellePortafoglio(con);
  assert.equal(s[7], '−12,0%');
  assert.equal(c[6], '–'); assert.equal(c[7], '+10,0%');
  // SPY sulla stessa base: con il campo agosto parte dalla prima chiusura (121/110 − 1 = +10%);
  // senza il campo resta come prima (121/100 − 1 = +21%)
  assert.equal(celle(htmlStat({ indice_statistiche_da: 1 }, spyStat), 'SPY<small>in euro, total return')[7], '+10,0%');
  assert.equal(celle(htmlStat({}, spyStat), 'SPY<small>in euro, total return')[7], '+21,0%');
  // valore illeggibile: comportamento di prima (dichiarato in Metodo e fonti da PerformancePage)
  assert.equal(tileDd(htmlStat({ indice_statistiche_da: 7 })), '−20,00%');
});

// ── cablaggio (montaggio vero dei componenti, non solo gli helper) ──
// Grafico: il tooltip del drawdown (stato hover seminato) parte da daStat
function graficoConHover(hover, daStat) {
  const carica = creaCaricatore({ stub: { react: { ...React, useState: () => [hover, () => {}] } } });
  carica('i18n/lingua.ts').impostaLinguaCorrente('it');
  const GraficoTwr = carica('pages/performance/GraficoTwr.tsx').default;
  const h = renderToStaticMarkup(React.createElement(GraficoTwr, { date: dateStat, indice: [100, 80, 88, 84], spy: null, base: 0,
    daStat, mostraSpy: false, mostraMedie: false, ricostruitaFino: null }));
  return h.match(/<div class="perf-tip"[^>]*>.*?<span>Max drawdown ([^<]*)<\/span>/)[1];
}
test('grafico montato: il drawdown del tooltip parte dalla prima chiusura con indice_statistiche_da', () => {
  assert.equal(graficoConHover(3, 0), '−16,00%'); // dal costo 100
  assert.equal(graficoConHover(3, 1), '−4,55%');  // dalla prima chiusura: picco 88
  assert.equal(graficoConHover(0, 1), '—');       // il punto base non ha drawdown misurato
});

// Pagina Performance montata (slot useState come in dashboard.test.cjs: 5 TWR, 11 vista)
function paginaRischio(twr) {
  let i = 0;
  const seed = { 5: twr, 11: 'risk' };
  const carica = creaCaricatore({ stub: {
    react: { ...React, useState(initial) { const k = i++; return [k in seed ? seed[k] : typeof initial === 'function' ? initial() : initial, () => {}]; } },
    'react-router-dom': { useNavigate: () => () => {} }, '@/lib/api': { Bellomberg: {} }, 'lightweight-charts': {},
    '@/components/RunConfirmDialog': { default: () => null, __esModule: true },
  } });
  carica('i18n/lingua.ts').impostaLinguaCorrente('it');
  return renderToStaticMarkup(React.createElement(carica('pages/PerformancePage.tsx').default));
}
test('pagina montata: la distribuzione del P&L giornaliero esclude la seduta dal punto base al costo', () => {
  const d5 = ['2026-07-31', '2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06'];
  const twr = extra => ({ dates: d5, twr_index: [100, 80, 88, 84, 86], values_eur: [1000, 800, 880, 840, 860], flows_eur: [1000, 0, 0, 0, 0],
    regimes: d5.map(() => 'reconstructed'), metrics: { risk_free_used: 0.03 }, ...extra });
  const senza = paginaRischio(twr({})), con = paginaRischio(twr({ indice_statistiche_da: 1 }));
  assert.match(senza, new RegExp('Su 4 sedute, .*Seduta peggiore: 3 ago, −200' + sp + '€'));
  assert.match(con, new RegExp('Su 3 sedute, .*Seduta peggiore: 5 ago, −40' + sp + '€'));
});
