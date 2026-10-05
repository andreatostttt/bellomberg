/** Icona di un titolo: logo locale → logo dal backend (vedi loghi-remoti.ts) → iniziali su colore stabile.
 *
 *  Nessuna mappa di ticker nel codice: i loghi locali stanno in src/assets/logos/ (file
 *  personali, fuori dal repository; Vite li conosce in fase di build, vedi il README nella
 *  cartella) e gli altri arrivano a runtime da GET /market/logos. Il ripiego dichiarato,
 *  quando nessuna fonte ha un logo, sono le iniziali dell'emittente su un colore stabile. */
import { LOGHI_LOCALI } from '@/assets/logos';

export type FonteIcona =
  | { tipo: 'locale'; url: string }
  | { tipo: 'iniziali'; testo: string; colore: string };

export function iniziali(nome: string | null | undefined, ticker: string): string {
  const pulito = (nome && nome !== ticker && nome.toLowerCase() !== 'none' ? nome : ticker.split('.')[0])
    .replace(/\(.*?\)/g, ' ').replace(/[^\p{L}\p{N} ]/gu, ' ').trim();
  const parole = pulito.split(/\s+/).filter(Boolean);
  if (parole.length >= 2) return (parole[0][0] + parole[1][0]).toUpperCase();
  return (parole[0] || ticker).slice(0, 2).toUpperCase();
}

/** Colore stabile dal ticker: stessa tonalità a ogni avvio, contrasto ≥ 4.5:1 col bianco. */
export function coloreStabile(ticker: string): string {
  let h = 0;
  for (const c of ticker) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360} 46% 31%)`;
}

export function fonteIcona(ticker: string, nome?: string | null): FonteIcona {
  const chiave = ticker.toUpperCase();
  const radice = chiave.split('.')[0];
  const locale = LOGHI_LOCALI[chiave] || LOGHI_LOCALI[radice];
  if (locale) return { tipo: 'locale', url: locale };
  return { tipo: 'iniziali', testo: iniziali(nome, ticker), colore: coloreStabile(chiave) };
}
