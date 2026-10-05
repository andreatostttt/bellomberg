/// <reference types="vite/client" />
/** Loghi locali disponibili in fase di build: TICKER (o radice) → URL del file.
 *  Vite risolve il glob al momento della build: aggiungere un file qui accanto
 *  (vedi README.md) e ricompilare, oppure lasciarlo raccogliere dall'HMR in sviluppo. */
const moduli = import.meta.glob('./*.{svg,png}', { eager: true, query: '?url', import: 'default' }) as Record<string, string>;

export const LOGHI_LOCALI: Record<string, string> = Object.fromEntries(Object.entries(moduli)
  .map(([file, url]) => [file.split('/').pop()!.replace(/\.(svg|png)$/i, '').toUpperCase(), url]));
