import assert from 'node:assert/strict';
import test from 'node:test';
import { squarify } from '../../src/lib/treemap.ts';
import { giudicaRischio } from '../../src/lib/rischio-livello.ts';
import { notiziePerTitoli } from '../../src/lib/notizie-titoli.ts';
import { eventiImportanti, trimestraliPortafoglio } from '../../src/lib/eventi-importanti.ts';
import { quotaCassa } from '../../src/pages/dashboard/quota-cassa.ts';

test('allocazione: cassa illeggibile = n.d., cassa negativa = investito oltre il 100% (mai «100% investito» muto)', () => {
  const ok = quotaCassa(2000, 10000);
  assert.ok(close(ok.pesoCassa!, 20)); assert.ok(close(ok.investito!, 80)); assert.equal(ok.negativa, false);
  assert.deepEqual(quotaCassa(null, 10000), { pesoCassa: null, investito: null, negativa: false });
  assert.deepEqual(quotaCassa(Number.NaN, 10000), { pesoCassa: null, investito: null, negativa: false });
  assert.deepEqual(quotaCassa(500, null), { pesoCassa: null, investito: null, negativa: false });
  const neg = quotaCassa(-500, 10000);
  assert.ok(close(neg.pesoCassa!, -5)); assert.ok(close(neg.investito!, 105)); assert.equal(neg.negativa, true);
});

const close = (a: number, b: number, eps = 1e-9) => Math.abs(a - b) < eps;

test('treemap: le tessere riempiono il quadrato senza sovrapporsi e l\'area segue il peso', () => {
  // pesi del portafoglio reale del 02/10/2026 (11 titoli + liquidità, in % del patrimonio)
  const pesi = [19.2, 13.5, 12.4, 10.3, 9.5, 9.4, 6.0, 5.7, 5.3, 3.8, 2.8, 1.9];
  const tiles = squarify(pesi.map((weight, i) => ({ item: `T${i}`, weight })), 0, 0, 100, 100);
  assert.equal(tiles.length, pesi.length);
  const totale = pesi.reduce((s, p) => s + p, 0);
  const area = tiles.reduce((s, t) => s + t.w * t.h, 0);
  assert.ok(close(area, 10000, 1e-6), `area totale ${area}`);
  for (const t of tiles) {
    assert.ok(close(t.w * t.h, (t.weight / totale) * 10000, 1e-6), `${t.item}: area proporzionale al peso`);
    assert.ok(t.x >= -1e-9 && t.y >= -1e-9 && t.x + t.w <= 100 + 1e-9 && t.y + t.h <= 100 + 1e-9, `${t.item} dentro il quadrato`);
  }
  for (let i = 0; i < tiles.length; i++) for (let j = i + 1; j < tiles.length; j++) {
    const a = tiles[i], b = tiles[j];
    const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    assert.ok(overlapX <= 1e-6 || overlapY <= 1e-6, `${a.item} e ${b.item} non si sovrappongono`);
  }
  const peggiore = Math.max(...tiles.map(t => Math.max(t.w / t.h, t.h / t.w)));
  assert.ok(peggiore < 4, `tessere vicine al quadrato (rapporto peggiore ${peggiore.toFixed(2)})`);
});

test('treemap: pesi nulli, negativi o non numerici non diventano tessere', () => {
  assert.deepEqual(squarify([]), []);
  const tiles = squarify([{ item: 'a', weight: 0 }, { item: 'b', weight: -3 }, { item: 'c', weight: Number.NaN }, { item: 'd', weight: 5 }]);
  assert.deepEqual(tiles.map(t => t.item), ['d']);
  assert.ok(close(tiles[0].w * tiles[0].h, 10000));
});

test('rischio: livello dal rapporto con SPY, ripiego assoluto dichiarato, mai inventato', () => {
  assert.deepEqual(giudicaRischio(30.58, 14.03).livello, 'alto');
  assert.ok(close(giudicaRischio(30.58, 14.03).rapporto!, 30.58 / 14.03));
  assert.equal(giudicaRischio(14, 14).livello, 'medio');
  assert.equal(giudicaRischio(17.5, 14).livello, 'medio', '1,25× è ancora medio');
  assert.equal(giudicaRischio(11, 14).livello, 'basso');
  assert.deepEqual(giudicaRischio(18, null), { livello: 'medio', rapporto: null, base: 'assoluta' });
  assert.equal(giudicaRischio(25, undefined).livello, 'alto');
  assert.deepEqual(giudicaRischio(null, 14), { livello: null, rapporto: null, base: null });
  assert.deepEqual(giudicaRischio(Number.NaN, 14), { livello: null, rapporto: null, base: null });
});

test('notizie: il titolo si riconosce dall\'intestazione, non dal tag sbagliato del feed', () => {
  const titoli = [
    { ticker: 'ZETA.DE', nome: 'Zetacorp (A)' }, { ticker: 'KEPL.DE', nome: 'Kepler Technology' },
    { ticker: 'NOVA.DE', nome: 'Novacom' }, { ticker: 'ACME.MI', nome: 'None' },
  ];
  const feed = [
    { id: 1, title: 'Kepler: nessuna visibilità sulla fine della carenza', snippet: 'ZETA.DE 12,5% del book: …', ticker_mentioned: 'ZETA.DE', published_at: '2026-10-01T18:59', sentiment: 'bullish' },
    { id: 2, title: 'Adobe cede il 33% in 12 mesi', snippet: 'ZETA.DE …', ticker_mentioned: 'ZETA.DE', published_at: '2026-10-01T19:32', sentiment: 'bearish' },
    { id: 3, title: 'Zetacorp più solida per crescita AI', snippet: 'ZETA.DE al 12,5% del book', ticker_mentioned: 'ZETA.DE', published_at: '2026-10-01T19:37', sentiment: 'bullish' },
    { id: 4, title: 'Chipmaker NOVA vola', snippet: 'NOVA.DE 6,5% del book', ticker_mentioned: 'NOVA.DE', published_at: '2026-10-01T20:00', sentiment: 'bullish' },
    { id: 5, title: 'Keplerneedle startup raises funds', snippet: null, ticker_mentioned: 'KEPL.DE', published_at: '2026-10-01T21:00', sentiment: 'neutral' },
    { id: 6, title: 'ACME alza il dividendo', snippet: 'ACME.MI pesa il 3,5%', ticker_mentioned: 'ACME.MI', published_at: '2026-10-01T08:00', sentiment: 'bearish' },
  ];
  const out = notiziePerTitoli(feed, titoli);
  assert.deepEqual(out.map(n => [n.ticker, n.titolo.slice(0, 12)]), [
    ['NOVA.DE', 'Chipmaker NO'], ['ZETA.DE', 'Zetacorp più'], ['KEPL.DE', 'Kepler: ness'], ['ACME.MI', 'ACME alza il'],
  ]);
  const kepler = out.find(n => n.ticker === 'KEPL.DE')!;
  assert.equal(kepler.motivo, null, 'il motivo del backend parla di Zetacorp: non va sotto Kepler');
  assert.equal(out.find(n => n.ticker === 'ZETA.DE')!.motivo, 'ZETA.DE al 12,5% del book');
  assert.equal(kepler.tono, 'positiva');
  assert.equal(out.find(n => n.ticker === 'ACME.MI')!.tono, 'negativa');
});

test('notizie: una prima parola condivisa da più titoli non basta ad assegnare la notizia', () => {
  const titoli = [{ ticker: 'BAC', nome: 'Bank of America' }, { ticker: 'BIRG.IR', nome: 'Bank of Ireland' }];
  const feed = [
    { id: 1, title: 'Bank stocks slide on rate fears', published_at: '2026-10-01T10:00' },
    { id: 2, title: 'BAC raises its dividend', published_at: '2026-10-01T11:00' },
  ];
  assert.deepEqual(notiziePerTitoli(feed, titoli).map(n => [n.ticker, n.id]), [['BAC', '2']]);
});

test('notizie: massimo per titolo e in totale, senza doppioni', () => {
  const titoli = [{ ticker: 'KEPL.DE', nome: 'Kepler Technology' }];
  const feed = Array.from({ length: 6 }, (_, i) => ({ id: i, title: `Kepler notizia ${i % 4}`, published_at: `2026-10-0${i + 1}` }));
  const out = notiziePerTitoli(feed, titoli, 12, 3);
  assert.equal(out.length, 3);
  assert.equal(new Set(out.map(n => n.titolo)).size, 3);
});

test('eventi: importanza 4 e 5, una volta sola, in ordine, date stimate dichiarate', () => {
  const eventi = [
    { date: '2026-10-02', time: '14:30 CET', title: 'USA: occupazione non agricola', importance: 5, country: 'US' },
    { date: '2026-10-08', time: '14:30 CET', title: 'USA: richieste sussidio', importance: 3, country: 'US' },
    { date: '2026-10-13', time: '14:30 CET', title: 'USA: pubblicazione CPI', importance: 5, country: 'US', date_estimated: true },
    { date: '2026-10-14', time: '14:30 CET', title: 'USA: pubblicazione CPI', importance: 5, country: 'US', date_estimated: true },
    { date: '2026-10-28', time: '20:00 CET', title: 'Decisione tassi FOMC', importance: 5, country: 'US' },
    { date: '2026-10-30', time: '06:00 CET', title: 'Decisione tassi BoJ', importance: 4, country: 'JP' },
    { date: '2026-09-30', time: '10:00 CET', title: 'Evento passato', importance: 5, country: 'EU' },
  ];
  const out = eventiImportanti(eventi, '2026-10-02');
  assert.deepEqual(out.map(e => e.titolo), ['Occupazione non agricola', 'Pubblicazione CPI', 'Decisione tassi FOMC', 'Decisione tassi BoJ']);
  assert.equal(out[1].stimata, true);
  assert.equal(out[1].ora, null, 'una data stimata non ha un orario');
  assert.equal(out[0].ora, '14:30 CET');
  assert.equal(eventiImportanti(eventi, '2026-10-02', 2).length, 2);
});

test('trimestrali del portafoglio: fuori dagli eventi macro, la prossima per titolo, in ordine di data', () => {
  const eventi = [
    { date: '2026-10-02', time: '14:30 CET', title: 'USA: occupazione non agricola', importance: 5, type: 'Macro' },
    { date: '2026-10-27', time: 'After-close', title: 'ZETA.DE Earnings Q3 2026', importance: 4, type: 'Earnings', ticker: 'ZETA.DE' },
    { date: '2026-10-23', time: 'TBD', title: 'OMEGA.DE Earnings', importance: 4, type: 'Earnings', ticker: 'omega.de', date_estimated: true },
    { date: '2027-01-27', time: 'TBD', title: 'OMEGA.DE Earnings', importance: 4, type: 'Earnings', ticker: 'OMEGA.DE' },
    { date: '2026-09-30', time: 'TBD', title: 'OLD Earnings', importance: 4, type: 'Earnings', ticker: 'OLD' },
    { date: '2026-10-29', time: 'TBD', title: 'Earnings senza ticker', importance: 4, type: 'Earnings' },
  ];
  assert.deepEqual(eventiImportanti(eventi, '2026-10-02').map(e => e.titolo), ['Occupazione non agricola'],
    'le trimestrali (importanza 4) non occupano le righe degli eventi macro');
  assert.deepEqual(trimestraliPortafoglio(eventi, '2026-10-02'), [
    { data: '2026-10-23', ticker: 'OMEGA.DE', stimata: true, ora: null },
    { data: '2026-10-27', ticker: 'ZETA.DE', stimata: false, ora: 'After-close' },
  ]);
  assert.equal(trimestraliPortafoglio(eventi, '2026-10-02', 1).length, 1);
});
