/** Loghi delle società dal backend (GET /market/logos: profilo Finnhub o icona del sito
 *  dell'emittente, cache lato backend in data/loghi).
 *
 *  Uno store di modulo: la dashboard chiede i loghi di tutte le posizioni in poche richieste
 *  (al massimo 40 ticker ciascuna, il limite del backend), ogni IconaTitolo legge il proprio
 *  ticker. Un ticker già chiesto non si richiede (anche se è senza logo: `null` con il motivo
 *  dichiarato dal backend), un errore di rete lascia il ticker libero per il prossimo tentativo.
 *  Senza logo l'icona usa il ripiego dichiarato: le iniziali su colore stabile. */
import { useSyncExternalStore } from 'react';
import { Bellomberg } from '@/lib/api';

export interface LogoRemoto { logo: string | null; motivo: string | null; fonte: string | null }
const VUOTO: LogoRemoto = { logo: null, motivo: null, fonte: null };
/** Limite di ticker per richiesta dichiarato da GET /market/logos. */
export const MAX_TICKER_PER_RICHIESTA = 40;

const loghi = new Map<string, LogoRemoto>();
const inCorso = new Set<string>();
const ascoltatori = new Set<() => void>();
let versione = 0;

function avvisa() {
  versione++;
  for (const ascolta of ascoltatori) ascolta();
}

function caricaBlocco(blocco: string[]): Promise<void> {
  for (const t of blocco) inCorso.add(t);
  return Bellomberg.marketLogos(blocco)
    .then(risposta => {
      for (const t of blocco) {
        const url = risposta?.logos?.[t];
        const logo = typeof url === 'string' && url.startsWith('data:image/') ? url : null;
        loghi.set(t, { logo, motivo: logo ? null : (risposta?.motivi?.[t] ?? null), fonte: logo ? (risposta?.fonti?.[t] ?? null) : null });
      }
      avvisa();
    })
    .catch(() => { /* rete o backend giù: si riprova al prossimo caricamento */ })
    .finally(() => { for (const t of blocco) inCorso.delete(t); });
}

export function caricaLoghi(tickers: string[]): Promise<void> {
  const mancanti = [...new Set(tickers.map(t => t.trim().toUpperCase()).filter(Boolean))]
    .filter(t => !loghi.has(t) && !inCorso.has(t));
  if (!mancanti.length) return Promise.resolve();
  const blocchi: Promise<void>[] = [];
  for (let i = 0; i < mancanti.length; i += MAX_TICKER_PER_RICHIESTA) blocchi.push(caricaBlocco(mancanti.slice(i, i + MAX_TICKER_PER_RICHIESTA)));
  return Promise.all(blocchi).then(() => undefined);
}

export function logoRemoto(ticker: string): LogoRemoto {
  return loghi.get(ticker.toUpperCase()) ?? VUOTO;
}

const iscrivi = (ascolta: () => void) => { ascoltatori.add(ascolta); return () => { ascoltatori.delete(ascolta); }; };

/** Il logo remoto del ticker (logo null finché non c'è, con il motivo se il backend lo dichiara). */
export function useLogoRemoto(ticker: string): LogoRemoto {
  useSyncExternalStore(iscrivi, () => versione, () => versione);
  return logoRemoto(ticker);
}

/** Solo per i test. */
export function _svuotaLoghi() { loghi.clear(); inCorso.clear(); avvisa(); }
