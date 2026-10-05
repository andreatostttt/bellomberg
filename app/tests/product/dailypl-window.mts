import assert from 'node:assert/strict';
import test from 'node:test';
import { computeDailyPnl } from '../../src/lib/dailypl.ts';

const pos = (prevCloseTs: string | null) => ({
  ticker: 'ZETA.DE',
  nome: 'Zetacorp (A)',
  quantita: 10,
  prezzo_medio: 300,
  prezzo_live: 310,
  valuta: 'EUR',
  valore_mercato: 3100,
  pl_eur: 100,
  pl_pct: 3.33,
  peso_pct: 50,
  prev_close: 300,
  prev_close_ts: prevCloseTs,
  fx_to_eur: 1.0,
});

// Il caso vero del 17/09: prev_close fermo al 14/09 mattina, live del 16/09 sera
// (martedi' 15/09 zero snapshot). Il numero resta, ma non deve chiamarsi "GG".
test('gap di un giorno di borsa: finestra multi-day dichiarata, numeri invariati', () => {
  const r = computeDailyPnl([pos('2026-09-14 09:07:59')], '2026-09-16 22:25:59');
  assert.equal(r.dayTot, 100);
  assert.equal(r.multiDay, true);
  assert.equal(r.windowLabel, '14/09→16/09');
});

// Giornata normale: prev del giorno prima -> resta un GG, nessuna etichetta.
test('daily normale: nessuna finestra', () => {
  const r = computeDailyPnl([pos('2026-09-15 18:00:00')], '2026-09-16 22:25:59');
  assert.equal(r.dayTot, 100);
  assert.equal(r.multiDay, false);
  assert.equal(r.windowLabel, null);
});

// Venerdi' -> lunedi' e' UNA sola seduta: non deve scattare il multi-day.
test('weekend ven->lun: resta daily', () => {
  const r = computeDailyPnl([pos('2026-09-11 18:00:00')], '2026-09-14 10:00:00');
  assert.equal(r.multiDay, false);
  assert.equal(r.windowLabel, null);
});

// Senza asOf nessuna rivendicazione sulla finestra (compatibilita' chiamanti vecchi).
test('senza asOf: nessun flag di finestra', () => {
  const r = computeDailyPnl([pos('2026-09-14 09:07:59')]);
  assert.equal(r.dayTot, 100);
  assert.equal(r.multiDay, false);
  assert.equal(r.windowLabel, null);
});

// La fine finestra e' lo snapshot piu' recente, non l'orologio: a notte fonda
// la data locale e' gia' domani mentre i prezzi sono di ieri.
test('liveAsOf: preferisce lo snapshot piu recente, ripiega sul timestamp', async () => {
  const { liveAsOf } = await import('../../src/lib/dailypl.ts');
  assert.equal(
    liveAsOf({ timestamp: '2026-09-17T00:31:35', as_of: { price_snapshots_newest: '2026-09-16 22:31:02' } }),
    '2026-09-16 22:31:02',
  );
  assert.equal(liveAsOf({ timestamp: '2026-09-17T00:31:35' }), '2026-09-17T00:31:35');
  assert.equal(liveAsOf(null), null);
});
