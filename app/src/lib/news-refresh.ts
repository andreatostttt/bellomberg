/** Contratto G3 (04/10/2026): `interrupted` = giro interrotto (es. backend fermato), esito dichiarato. */
export type NewsRefreshStatus = 'idle' | 'running' | 'success' | 'error' | 'interrupted' | string;

export interface NewsRefreshJob {
  id: string | null;
  status: NewsRefreshStatus;
  trigger?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  next_run_at?: string | null;
  interval_seconds?: number;
  duration_s?: number | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
}
export interface NewsRefreshResponse {
  accepted: boolean;
  /** con accepted:false: 'already_running' = un giro è già in corso (stesso job, si segue) */
  reason?: string | null;
  job: NewsRefreshJob | null;
}
/** GET /news/feed/refresh?job_id=<id> (contratto G3): stato di UN job; 404 se l'id è sconosciuto. */
export interface NewsRefreshJobResponse {
  job: NewsRefreshJob | null;
  scheduler?: Record<string, unknown> | null;
  timestamp?: string;
}

/** Esito del watcher: il job seguito e se e' stato AVVIATO da questa richiesta (`joined` = il backend
 *  ha rifiutato perche' un giro era gia' in corso e lo stiamo seguendo: si dichiara, non si spaccia). */
export type NewsRefreshOutcome = NewsRefreshJob & { joined: boolean };

/** Errori del watcher: un CODICE stabile + parametri, mai una frase (integrazione G9b+G9c).
 *  Il testo per l'utente lo sceglie la pagina dal catalogo i18n (news/calcoli.ts errorDetail /
 *  testoErroreRefresh): questo modulo resta senza i18n perche' i test node lo eseguono cosi' com'e'.
 *  `job` = il job a cui si riferisce l'errore (rifiuto, esito error/interrupted), se c'e'. */
export type NewsRefreshErrorCode = 'rejected' | 'missing_id' | 'changed_id' | 'unexpected_status' | 'bad_status'
  | 'already_started' | 'stopped' | 'failed' | 'job_error' | 'interrupted' | 'job_lost' | 'unreachable' | 'timeout';
export class NewsRefreshError extends Error {
  readonly code: NewsRefreshErrorCode;
  readonly params: Record<string, string>;
  readonly job: NewsRefreshJob | null;
  /** solo per 'timeout': minuti di attesa dopo cui la lettura è stata sospesa (G9b) */
  readonly minuti: number | null;
  constructor(code: NewsRefreshErrorCode, params: Record<string, string> = {}, job: NewsRefreshJob | null = null, minuti: number | null = null) {
    super(`news refresh ${code}${Object.keys(params).length ? ' ' + JSON.stringify(params) : ''}`);
    this.name = 'NewsRefreshError';
    this.code = code;
    this.params = params;
    this.job = job;
    this.minuti = minuti;
  }
}

/** Tetto della LETTURA lato client (il giro sul server non si ferma): un giro tipico dura 30-60 s
 *  con la classificazione; dopo 10 minuti di «running» lo stato si dichiara non più aggiornato. */
export const NEWS_REFRESH_MAX_MS = 10 * 60 * 1000;

/** Errore di una lettura di stato: 404 = job non più in memoria (backend riavviato, storia esaurita),
 *  nessuna risposta = backend irraggiungibile. Gli altri errori passano com'erano. */
export function erroreLetturaJob(error: unknown, job: NewsRefreshJob | null): unknown {
  const e = error as { response?: { status?: number }; request?: unknown; isAxiosError?: boolean } | null;
  if (e?.response?.status === 404) return new NewsRefreshError('job_lost', {}, job);
  if (e && !e.response && (e.isAxiosError || e.request)) return new NewsRefreshError('unreachable', {}, job);
  return error;
}

/** Conteggi dell'esito del giro: un campo assente resta null (n.d.), mai 0. */
export function esitoGiro(job: NewsRefreshJob | null | undefined): { saved: number | null; duplicates: number | null; fetched: number | null; notClassified: number | null } {
  const r = (job?.result || {}) as Record<string, unknown>;
  const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  return { saved: n(r.saved), duplicates: n(r.skipped_duplicates), fetched: n(r.fetched), notClassified: n(r.not_classified) };
}

/** Stati finali di un giro: si smette di leggere e si mostra l'esito (interrupted compreso). */
export const STATI_FINALI = ['success', 'error', 'interrupted'];

interface NewsRefreshWatcherOptions {
  start: () => Promise<NewsRefreshResponse>;
  /** legge lo stato del job `id` (GET /news/feed/refresh?job_id=<id>) */
  readStatus: (id: string) => Promise<NewsRefreshJob | null | undefined>;
  wait?: (milliseconds: number) => Promise<void>;
  intervalMs?: number;
  onStatus?: (job: NewsRefreshJob) => void;
  /** tetto della lettura (default NEWS_REFRESH_MAX_MS) e orologio monotonico (iniettabile nei test) */
  maxMs?: number;
  now?: () => number;
}

const defaultWait = (milliseconds: number): Promise<void> =>
  new Promise(resolve => window.setTimeout(resolve, milliseconds));

function validateJob(job: NewsRefreshJob | null | undefined, expectedId?: string): NewsRefreshJob {
  if (!job || typeof job.id !== 'string' || !job.id) {
    throw new NewsRefreshError('missing_id', {}, job ?? null);
  }
  if (expectedId && job.id !== expectedId) {
    throw new NewsRefreshError('changed_id', { expected: expectedId, received: job.id }, job);
  }
  if (!['running', ...STATI_FINALI].includes(job.status)) {
    throw new NewsRefreshError('unexpected_status', { status: String(job.status) }, job);
  }
  return job;
}

export function createNewsRefreshWatcher(options: NewsRefreshWatcherOptions) {
  const wait = options.wait || defaultWait;
  const intervalMs = options.intervalMs ?? 2000;
  const maxMs = options.maxMs ?? NEWS_REFRESH_MAX_MS;
  const now = options.now || (() => performance.now());
  let stopped = false;
  let started = false;

  const run = async (): Promise<NewsRefreshOutcome> => {
    if (started) throw new NewsRefreshError('already_started');
    started = true;
    const response = await options.start();
    // accepted:false = il backend NON ha avviato un giro (gestore fermo/in arresto, o un giro gia' in
    // corso). Si segue solo un giro ancora in corso, dichiarandolo; altrimenti il job restituito e'
    // quello VECCHIO e il suo esito non va mostrato come se fosse il nuovo.
    if (typeof response?.accepted !== 'boolean') {
      throw new NewsRefreshError('bad_status', { field: 'accepted' }, response?.job ?? null);
    }
    const accepted = response.accepted;
    if (!accepted && response?.job?.status !== 'running') {
      throw new NewsRefreshError('rejected', { status: String(response?.job?.status ?? 'n/a') }, response?.job ?? null);
    }
    const initial = validateJob(response?.job);
    const joined = !accepted;
    options.onStatus?.(initial);
    const expectedId = initial.id as string;
    if (STATI_FINALI.includes(initial.status)) return { ...initial, joined };

    const inizio = now();
    let ultimo: NewsRefreshJob = initial;
    while (!stopped) {
      await wait(intervalMs);
      if (stopped) throw new NewsRefreshError('stopped');
      if (now() - inizio >= maxMs) {
        const min = Math.round(maxMs / 60000);
        throw new NewsRefreshError('timeout', { min: String(min) }, ultimo, min);
      }
      let letto: NewsRefreshJob | null | undefined;
      try { letto = await options.readStatus(expectedId); }
      catch (error) { throw erroreLetturaJob(error, ultimo); }
      const current = validateJob(letto, expectedId);
      ultimo = current;
      options.onStatus?.(current);
      if (STATI_FINALI.includes(current.status)) return { ...current, joined };
    }
    throw new NewsRefreshError('stopped');
  };

  return {
    run,
    stop: () => { stopped = true; },
  };
}
