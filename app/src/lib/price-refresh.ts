/** Esito dichiarato da POST /prices/update: `failed` = simboli non aggiornati; null = il backend
 *  non l'ha dichiarato (esito ignoto: il pallino non diventa verde). */
export interface PriceUpdateOutcome { failed: number | null }

/** Legge `failed` dalla risposta di /prices/update ({updated, failed, details}); HTTP 200 con
 *  failed>0 è un aggiornamento PARZIALE, non un successo. Assente/illeggibile = null, mai 0. */
export function priceUpdateOutcome(response: unknown): PriceUpdateOutcome {
  const failed = (response as { failed?: unknown } | null | undefined)?.failed;
  if (typeof failed === 'number' && Number.isFinite(failed) && failed >= 0) return { failed };
  if (Array.isArray(failed)) return { failed: failed.length };
  return { failed: null };
}

export interface GlobalPriceRefreshOptions<Snapshot> {
  updatePrices: () => Promise<unknown>;
  readPortfolio: () => Promise<Snapshot>;
  publish: (snapshot: Snapshot, outcome: PriceUpdateOutcome) => void;
  isHidden: () => boolean;
  schedule: (callback: () => void, milliseconds: number) => unknown;
  cancel: (handle: unknown) => void;
  onError?: (error: unknown) => void;
}
export interface GlobalPriceRefreshRunner {
  firstRefresh: Promise<void>;
  refresh: () => Promise<void>;
  stop: () => void;
}

export function startGlobalPriceRefresh<Snapshot>(
  options: GlobalPriceRefreshOptions<Snapshot>,
): GlobalPriceRefreshRunner {
  let stopped = false;
  let inFlight: Promise<void> | null = null;

  const refresh = (): Promise<void> => {
    if (stopped || options.isHidden()) return Promise.resolve();
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        const outcome = priceUpdateOutcome(await options.updatePrices());
        if (stopped) return;
        const snapshot = await options.readPortfolio();
        if (stopped) return;
        options.publish(snapshot, outcome);
      } catch (error) {
        if (!stopped) options.onError?.(error);
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  };

  const timer = options.schedule(() => { void refresh(); }, 60_000);
  const firstRefresh = refresh();

  return {
    firstRefresh,
    refresh,
    stop: () => {
      if (stopped) return;
      stopped = true;
      options.cancel(timer);
    },
  };
}
