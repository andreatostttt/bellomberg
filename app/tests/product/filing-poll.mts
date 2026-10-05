import assert from 'node:assert/strict';
import test from 'node:test';
import { FILING_POLL_MS, aggiornamentoInCorso, pollFiling } from '../../src/lib/filing-poll.ts';

const timers = () => {
  const attivi = new Map<number, () => void>();
  let n = 0;
  return {
    attivi,
    set: (fn: () => void, ms: number) => { assert.equal(ms, FILING_POLL_MS); attivi.set(++n, fn); return n; },
    clear: (id: number) => { attivi.delete(id); },
  };
};

test('polls /filings every 10 s only while the refresh manager is running', () => {
  assert.equal(FILING_POLL_MS, 10_000);
  assert.equal(aggiornamentoInCorso({ aggiornamento: { status: 'running' } } as any), true);
  for (const dati of [null, { aggiornamento: null }, { aggiornamento: { status: 'success' } }, { aggiornamento: { status: 'idle' } }])
    assert.equal(aggiornamentoInCorso(dati as any), false);

  const t = timers();
  let letture = 0;
  const ferma = pollFiling(true, () => { letture += 1; }, t.set, t.clear);
  assert.equal(t.attivi.size, 1);
  [...t.attivi.values()][0](); [...t.attivi.values()][0]();
  assert.equal(letture, 2);
  ferma();                                   // stato cambiato o pagina smontata
  assert.equal(t.attivi.size, 0);

  const inattivo = pollFiling(false, () => { letture += 1; }, t.set, t.clear);
  assert.equal(t.attivi.size, 0);
  inattivo();
});

// 04/10/2026 (G9b, contratto G2b): proposta AI come lavoro sul server, letta fino all'esito vero.
test('attendiPropostaAi: done subito, in_corso letto fino all esito, job_id assente e stop dichiarati', async () => {
  const { attendiPropostaAi, FilingAiJobError } = await import('../../src/lib/filing-ai-job.ts');
  const subito = await attendiPropostaAi({ avvia: async () => ({ stato: 'done' }), leggi: async () => { throw new Error('no'); }, attesa: async () => {} });
  assert.equal(subito.stato, 'done');
  const stati = [{ stato: 'in_corso', job_id: 'j1', secondi: 40 }, { stato: 'error', dettaglio: 'x' }];
  const visti: unknown[] = [], letti: string[] = [];
  const fine = await attendiPropostaAi({ avvia: async () => ({ stato: 'in_corso', job_id: 'j1', secondi: 20 }),
    leggi: async (id: string) => { letti.push(id); return stati.shift()!; }, attesa: async () => {}, onAvanzamento: e => visti.push(e) });
  assert.equal(fine.stato, 'error'); assert.deepEqual(letti, ['j1', 'j1']); assert.equal(visti.length, 2);
  await assert.rejects(attendiPropostaAi({ avvia: async () => ({ stato: 'in_corso' }), leggi: async () => ({ stato: 'done' }), attesa: async () => {} }),
    (e: unknown) => e instanceof FilingAiJobError && e.code === 'missing_job');
  await assert.rejects(attendiPropostaAi({ avvia: async () => ({ stato: 'in_corso', job_id: 'j2' }), leggi: async () => ({ stato: 'done' }),
    attesa: async () => {}, fermato: () => true }), (e: unknown) => e instanceof FilingAiJobError && e.code === 'stopped');
});

// Revisione G9b R1: anche la proposta AI ha una fine garantita e dichiarata.
test('R1 attendiPropostaAi: 404 = job_lost, backend down = unreachable, in_corso senza fine = timeout dichiarato', async () => {
  const { attendiPropostaAi, FilingAiJobError } = await import('../../src/lib/filing-ai-job.ts');
  const avvia = async () => ({ stato: 'in_corso', job_id: 'jx' });
  await assert.rejects(attendiPropostaAi({ avvia, attesa: async () => {},
    leggi: async () => { throw { isAxiosError: true, response: { status: 404 } }; } }), (e: unknown) => e instanceof FilingAiJobError && e.code === 'job_lost');
  await assert.rejects(attendiPropostaAi({ avvia, attesa: async () => {},
    leggi: async () => { throw { isAxiosError: true, request: {} }; } }), (e: unknown) => e instanceof FilingAiJobError && e.code === 'unreachable');
  let t = 0, letture = 0;
  await assert.rejects(attendiPropostaAi({ avvia, attesa: async (ms: number) => { t += ms; }, ora: () => t, maxMs: 30_000,
    leggi: async () => { letture += 1; return { stato: 'in_corso', job_id: 'jx' }; } }),
    (e: unknown) => e instanceof FilingAiJobError && e.code === 'timeout');
  assert.equal(letture, 9);
});
