import assert from 'node:assert/strict';
import test from 'node:test';
import { allineaBenchmark } from '../../src/lib/curva-benchmark.ts';

test('SPY riparte dal valore quota del primo giorno comune e riporta i buchi', () => {
  const date = ['2026-05-08', '2026-05-11', '2026-05-12', '2026-05-13'];
  const quota = [100, 101, 102, 104];
  const out = allineaBenchmark(date, quota, ['2026-05-11', '2026-05-13'], [50, 55]);
  assert.ok(out);
  assert.equal(out.da, 1);
  assert.ok(Number.isNaN(out.valori[0]));
  [101, 101, 111.1].forEach((v, i) => assert.ok(Math.abs(out.valori[i + 1] - v) < 1e-9, `${out.valori[i + 1]} ≈ ${v}`));
  assert.ok(Math.abs(out.variazionePct - 10) < 1e-9);
  assert.ok(Math.abs(out.variazioneQuotaPct - (104 / 101 - 1) * 100) < 1e-9);
});

test('serie assente, incoerente o senza giorni comuni: nessuna linea', () => {
  assert.equal(allineaBenchmark(['2026-01-01', '2026-01-02'], [100, 101], undefined, undefined), null);
  assert.equal(allineaBenchmark(['2026-01-01', '2026-01-02'], [100, 101], ['2026-01-01'], [1, 2]), null);
  assert.equal(allineaBenchmark(['2026-01-01', '2026-01-02'], [100, 101], ['2026-03-01'], [10]), null);
});
