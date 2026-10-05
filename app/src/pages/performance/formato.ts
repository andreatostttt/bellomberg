import { linguaCorrente, localeDi } from '@/i18n/lingua';

/** Formati della pagina Performance: valore assente = «—» (mai 0, mai «n.d.» in mezzo ai numeri),
 *  segno meno tipografico «−» come nella Dashboard. */
export const VUOTO = '—';
const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const numero = (v: number, dec: number) =>
  Math.abs(v).toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: true });
/** Il segno segue il valore MOSTRATO: −0,000001 arrotondato a due decimali è 0,00, senza segno. */
const segno = (v: number, conPiu: boolean, dec: number) => {
  const r = Number(v.toFixed(dec));
  return r < 0 ? '−' : conPiu && r > 0 ? '+' : '';
};

export const num = (v: number | null | undefined, dec = 2, conPiu = false) =>
  finito(v) ? segno(v, conPiu, dec) + numero(v, dec) : VUOTO;
export const pct = (v: number | null | undefined, dec = 2, conPiu = true) =>
  finito(v) ? segno(v, conPiu, dec) + numero(v, dec) + '%' : VUOTO;
export const punti = (v: number | null | undefined, dec = 2) =>
  finito(v) ? segno(v, true, dec) + numero(v, dec) + ' pt' : VUOTO;
export const euro = (v: number | null | undefined, dec = 2, conPiu = false) =>
  finito(v) ? segno(v, conPiu, dec) + numero(v, dec) + ' €' : VUOTO;
export const dataBreve = (iso: string | null | undefined) =>
  iso ? new Intl.DateTimeFormat(localeDi(linguaCorrente()), { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(iso.slice(0, 10) + 'T00:00:00Z')) : VUOTO;
