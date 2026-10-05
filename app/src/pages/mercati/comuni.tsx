import type { ReactNode } from 'react';
import { useId } from 'react';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import type { MktOverviewRow } from '@/lib/api';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';

export const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Prezzo con i decimali che servono: 4 sotto 1, 3 sotto 10, altrimenti 2. */
export function prezzo(v: number | null | undefined): string {
  if (!finito(v)) return '—';
  const d = Math.abs(v) < 1 ? 4 : Math.abs(v) < 10 ? 3 : 2;
  return v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d });
}
export function numero(v: number | null | undefined, d = 2): string {
  return finito(v) ? v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
}
export function compatto(v: number | null | undefined, d = 2): string {
  return finito(v) ? Intl.NumberFormat(localeDi(linguaCorrente()), { notation: 'compact', maximumFractionDigits: d }).format(v) : '—';
}
export function variazione(v: number | null | undefined): string {
  return finito(v) ? (v > 0 ? '+' : '') + numero(v) + '%' : '—';
}

/** Card con titolo ben riconoscibile: icona colorata per categoria, titolo grande,
 *  azioni (segmenti, link) a destra. `tinta` sceglie il colore della categoria. */
export function CardMercati({ tinta, icona, titolo, azioni, className = '', children }: {
  tinta: 'idx' | 'stk' | 'mov' | 'cmd' | 'fx' | 'rt' | 'fav';
  icona: ReactNode;
  titolo: ReactNode;
  azioni?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section className={`bbn-card mk-card is-${tinta}${className ? ' ' + className : ''}`} aria-labelledby={id}>
      <header className="mk-card-head">
        <span className="mk-ci" aria-hidden="true">{icona}</span>
        <h2 id={id}>{titolo}</h2>
        <span className="bbn-grow" />
        {azioni}
      </header>
      {children}
    </section>
  );
}

/** Sigla neutra al posto del logo (indici, materie prime, valute, tassi). */
export function Sigla({ testo }: { testo: string }) {
  return <span className="mk-sigla" aria-hidden="true">{testo}</span>;
}

/** Riga cliccabile: icona o sigla, nome + ticker, prezzo, pastiglia. */
export function RigaTitolo({ riga, sigla, unita = '', onApri }: {
  riga: MktOverviewRow;
  sigla?: string;
  unita?: string;
  onApri: (ticker: string) => void;
}) {
  return (
    <button type="button" className="mk-row" data-ticker={riga.ticker} title={`${riga.name} · ${riga.ticker}`} onClick={() => onApri(riga.ticker)}>
      {sigla != null ? <Sigla testo={sigla} /> : <IconaTitolo ticker={riga.ticker} nome={riga.name} dimensione="xs" />}
      <span className="mk-row-name"><b>{riga.name}</b><span>{riga.ticker}</span></span>
      <span className="mk-row-px num">{finito(riga.price) ? prezzo(riga.price) + unita : '—'}</span>
      <PastigliaVariazione valore={riga.change_pct} lampo={riga.price ?? null}>
        <span className="num">{variazione(riga.change_pct)}</span>
      </PastigliaVariazione>
    </button>
  );
}

/** Sigle e aree geografiche dei simboli fissi del cruscotto (backend OVERVIEW_GLOBAL). */
export const SIGLE: Record<string, string> = {
  '^GSPC': 'US', '^IXIC': 'US', '^DJI': 'US', '^RUT': 'US', '^NYA': 'US', '^GSPTSE': 'CA', '^BVSP': 'BR', '^MXX': 'MX', '^MERV': 'AR', '^VIX': 'US',
  '^STOXX50E': 'EU', '^STOXX': 'EU', '^FTSE': 'UK', '^GDAXI': 'DE', '^FCHI': 'FR', 'FTSEMIB.MI': 'IT', '^IBEX': 'ES', '^AEX': 'NL', '^SSMI': 'CH', '^BFX': 'BE',
  '^N225': 'JP', '^HSI': 'HK', '000001.SS': 'CN', '399001.SZ': 'CN', '^BSESN': 'IN', '^KS11': 'KR', '^TWII': 'TW', '^AXJO': 'AU', '^STI': 'SG',
  'GC=F': 'Au', 'SI=F': 'Ag', 'PL=F': 'Pt', 'PA=F': 'Pd', 'HG=F': 'Cu', 'ALI=F': 'Al',
  'CL=F': 'WTI', 'BZ=F': 'BRN', 'NG=F': 'NG', 'HO=F': 'HO', 'RB=F': 'RB',
  'ZC=F': 'ZC', 'ZW=F': 'ZW', 'ZS=F': 'ZS', 'KC=F': 'KC', 'CC=F': 'CC', 'SB=F': 'SB', 'CT=F': 'CT', 'LE=F': 'LE',
  'EURUSD=X': '€$', 'GBPUSD=X': '£$', 'USDJPY=X': '$¥', 'EURCHF=X': '€F', 'EURGBP=X': '€£', 'BTC-USD': '₿', 'ETH-USD': 'Ξ',
  '^TNX': '10A', '^TYX': '30A', '^FVX': '5A', '^IRX': '3M',
  'ES=F': 'ES', 'NQ=F': 'NQ', 'YM=F': 'YM', 'RTY=F': 'RTY',
};
export const sigla = (r: MktOverviewRow) => SIGLE[r.ticker] ?? r.name.replace(/[^A-Za-z0-9]/g, '').slice(0, 3).toUpperCase();

export type Area = 'am' | 'eu' | 'as';
const AREE: Record<string, Area> = {
  '^GSPC': 'am', '^IXIC': 'am', '^DJI': 'am', '^RUT': 'am', '^NYA': 'am', '^GSPTSE': 'am', '^BVSP': 'am', '^MXX': 'am', '^MERV': 'am', '^VIX': 'am',
  '^STOXX50E': 'eu', '^STOXX': 'eu', '^FTSE': 'eu', '^GDAXI': 'eu', '^FCHI': 'eu', 'FTSEMIB.MI': 'eu', '^IBEX': 'eu', '^AEX': 'eu', '^SSMI': 'eu', '^BFX': 'eu',
  '^N225': 'as', '^HSI': 'as', '000001.SS': 'as', '399001.SZ': 'as', '^BSESN': 'as', '^KS11': 'as', '^TWII': 'as', '^AXJO': 'as', '^STI': 'as',
};
export const areaDi = (ticker: string): Area => AREE[ticker] ?? 'am';

/** Gruppi delle materie prime (simboli futures Yahoo); un simbolo sconosciuto va in «agricoli». */
export type GruppoMp = 'met' | 'ene' | 'agr';
const GRUPPI_MP: Record<string, GruppoMp> = {
  'GC=F': 'met', 'SI=F': 'met', 'PL=F': 'met', 'PA=F': 'met', 'HG=F': 'met', 'ALI=F': 'met',
  'CL=F': 'ene', 'BZ=F': 'ene', 'NG=F': 'ene', 'HO=F': 'ene', 'RB=F': 'ene',
};
export const gruppoMp = (ticker: string): GruppoMp => GRUPPI_MP[ticker] ?? 'agr';

/** Indici dei cinque riquadri in alto, nell'ordine. */
export const INDICI_CHIAVE = ['^GSPC', '^IXIC', 'FTSEMIB.MI', '^N225', '^VIX'];
/** Ordine dei paesi nel selettore (il backend li restituisce in ordine alfabetico). */
export const ORDINE_PAESI = ['US', 'IT', 'DE', 'FR', 'UK', 'JP', 'CN', 'IN', 'BR'];
