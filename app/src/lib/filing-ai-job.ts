/** Proposta AI dei filing come lavoro sul server (contratto G2b, 04/10/2026):
 *  POST /filings/{t}/ai-proposal attende al massimo ~20 s; se il lavoro non è finito risponde
 *  {stato: 'in_corso', job_id, ...} e il client legge GET /filings/{t}/ai-proposal/job/{job_id}
 *  finché lo stato non è più 'in_corso'. Il server continua (e paga) anche se la pagina si chiude:
 *  qui si ATTENDE l'esito vero invece di abbandonare a un timeout client. Nessuna dipendenza da React
 *  o dalla lingua: i testi li sceglie la pagina. */

export interface EsitoAiMinimo { stato: string; job_id?: string | null }

export type FilingAiJobErrorCode = 'missing_job' | 'stopped' | 'job_lost' | 'unreachable' | 'timeout';
export class FilingAiJobError extends Error {
  code: FilingAiJobErrorCode;
  /** solo per 'timeout': minuti dopo cui la lettura è stata sospesa */
  minuti: number | null;
  constructor(code: FilingAiJobErrorCode, message: string, minuti: number | null = null) {
    super(message);
    this.name = 'FilingAiJobError';
    this.code = code;
    this.minuti = minuti;
  }
}

/** Tetto della LETTURA lato client (il lavoro sul server non si ferma): la chiamata al modello in
 *  llm_client può durare 600 s × 3 tentativi più i download dei PDF; oltre 35 minuti di «in_corso»
 *  lo stato si dichiara non più aggiornato dal server. */
export const FILING_AI_MAX_MS = 35 * 60 * 1000;

export interface AttesaPropostaAi<E extends EsitoAiMinimo> {
  avvia: () => Promise<E>;
  leggi: (jobId: string) => Promise<E>;
  onAvanzamento?: (esito: E) => void;
  attesa?: (ms: number) => Promise<void>;
  intervalMs?: number;
  /** true = smetti di leggere (pagina chiusa, titolo cambiato): il lavoro sul server NON si ferma */
  fermato?: () => boolean;
  /** tetto della lettura (default FILING_AI_MAX_MS) e orologio monotonico (iniettabile nei test) */
  maxMs?: number;
  ora?: () => number;
}

const attesaDefault = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

export async function attendiPropostaAi<E extends EsitoAiMinimo>(o: AttesaPropostaAi<E>): Promise<E> {
  const attesa = o.attesa || attesaDefault;
  const intervallo = o.intervalMs ?? 3000;
  const maxMs = o.maxMs ?? FILING_AI_MAX_MS;
  const ora = o.ora || (() => performance.now());
  let esito = await o.avvia();
  if (esito.stato !== 'in_corso') return esito;
  const jobId = esito.job_id;
  if (typeof jobId !== 'string' || !jobId) throw new FilingAiJobError('missing_job', 'ai-proposal in progress without job_id');
  o.onAvanzamento?.(esito);
  const inizio = ora();
  for (;;) {
    await attesa(intervallo);
    if (o.fermato?.()) throw new FilingAiJobError('stopped', 'ai-proposal polling stopped');
    if (ora() - inizio >= maxMs) {
      throw new FilingAiJobError('timeout', `still in progress after ${Math.round(maxMs / 60000)} min`, Math.round(maxMs / 60000));
    }
    try { esito = await o.leggi(jobId); }
    catch (error) {
      const e = error as { response?: { status?: number }; request?: unknown; isAxiosError?: boolean } | null;
      if (e?.response?.status === 404) throw new FilingAiJobError('job_lost', 'ai-proposal job no longer known by the backend');
      if (e && !e.response && (e.isAxiosError || e.request)) throw new FilingAiJobError('unreachable', 'backend unreachable while polling');
      throw error;
    }
    if (esito.stato !== 'in_corso') return esito;
    o.onAvanzamento?.(esito);
  }
}
