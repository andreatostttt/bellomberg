import assert from 'node:assert/strict';
import test from 'node:test';
import { CHIP_ALTRI, COUNTRIES_AVAILABLE, chipDelPaese, conteggioPerChip, passaFiltroPaese } from '../../src/lib/calendario-paesi.ts';

// G8 seguito (04/10/2026): nessuna trimestrale del portafoglio sparisce per il filtro paese.
const DEFAULT = ['US', 'EU', 'IT', 'DE'];

test('portfolio earnings always pass the country filter, whatever the country', () => {
  for (const country of ['FR', 'UK', 'NL', 'JP', 'HK', 'CH', '?']) {
    assert.equal(passaFiltroPaese({ type: 'Earnings', ticker: 'ZZTEST.XX', country }, DEFAULT), true, country);
  }
});

test('macro events still follow the country filter', () => {
  assert.equal(passaFiltroPaese({ type: 'Macro Release', country: 'JP' }, DEFAULT), false);
  assert.equal(passaFiltroPaese({ type: 'Macro Release', country: 'US' }, DEFAULT), true);
  assert.equal(passaFiltroPaese({ type: 'Macro Release', country: 'JP' }, []), true);
});

test('countries without a chip and "?" go to the Others chip', () => {
  assert.ok(COUNTRIES_AVAILABLE.includes(CHIP_ALTRI));
  for (const c of ['NL', 'CH', 'HK', 'BE', '?']) assert.equal(chipDelPaese(c), CHIP_ALTRI, c);
  assert.equal(chipDelPaese('it'), 'IT');
  assert.equal(passaFiltroPaese({ type: 'Macro Release', country: 'HK' }, [CHIP_ALTRI]), true);
  assert.equal(passaFiltroPaese({ type: 'Macro Release', country: 'US' }, [CHIP_ALTRI]), false);
  assert.deepEqual(conteggioPerChip([{ country: 'HK' }, { country: '?' }, { country: 'US' }]), { [CHIP_ALTRI]: 2, US: 1 });
});

test('the news page wires the calendar filter and counts through these rules', async () => {
  const { readFile } = await import('node:fs/promises');
  const pagina = await readFile(new URL('../../src/pages/NewsPage.tsx', import.meta.url), 'utf8');
  assert.match(pagina, /if \(!passaFiltroPaese\(e, econCountrySel\)\) return false;/);
  assert.doesNotMatch(pagina, /econCountrySel\.includes\(/);
  assert.match(pagina, /conteggioPerChip\(/);
  const agenda = await readFile(new URL('../../src/pages/news/VistaAgenda.tsx', import.meta.url), 'utf8');
  assert.match(agenda, /from '@\/lib\/calendario-paesi'/);
  assert.match(agenda, /w\.countryUnknown/);
});
