/* Rilettura di /filings durante un aggiornamento (fase D, revisione finale M5): ogni 10 s
   solo finche' il manager dichiara `running`; fermata appena cambia stato o la pagina si smonta. */
import type { FilingOverview } from './api';

export const FILING_POLL_MS = 10_000;

export const aggiornamentoInCorso = (dati: FilingOverview | null) => dati?.aggiornamento?.status === 'running';

/** avvia il polling se `attivo`; restituisce la funzione che lo ferma (cleanup di useEffect) */
export function pollFiling(attivo: boolean, leggi: () => void,
  imposta: (fn: () => void, ms: number) => unknown = setInterval,
  cancella: (id: any) => void = clearInterval): () => void {
  if (!attivo) return () => {};
  const id = imposta(leggi, FILING_POLL_MS);
  return () => cancella(id);
}
