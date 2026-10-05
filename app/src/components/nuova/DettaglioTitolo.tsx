import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import type { Position } from '@/lib/api';
import { dayEur, dayPct } from '@/lib/dailypl';
import { fmtEUR, fmtNum, fmtPct } from '@/lib/format';
import TvChartPanel from '@/components/TvChartPanel';
import IconaTitolo from './IconaTitolo';
import PastigliaVariazione from './PastigliaVariazione';

const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export interface TestiDettaglio {
  detailOf: string; close: string; openMarkets: string; today: string;
  value: string; totalPl: string; weight: string; quantity: string; stalePrice: string;
}

/** Pannello laterale del titolo: prezzo, variazione, candele (TvChartPanel) e numeri
 *  della posizione. Esc o il velo lo chiudono; il focus torna a chi l'ha aperto. */
export default function DettaglioTitolo({ posizione, nome, testi, onChiudi, onApriMercati }: {
  posizione: Position | null;
  nome: string;
  testi: TestiDettaglio;
  onChiudi: () => void;
  onApriMercati: (ticker: string) => void;
}) {
  const chiudiRef = useRef<HTMLButtonElement>(null);
  const aperto = !!posizione;
  useEffect(() => {
    if (!aperto) return;
    const prima = document.activeElement as HTMLElement | null;
    chiudiRef.current?.focus();
    return () => { try { prima?.focus(); } catch { /* elemento smontato */ } };
  }, [aperto]);
  if (!posizione) return null;
  const p = posizione;
  const dp = dayPct(p), de = dayEur(p);
  const valuta = p.valuta === 'EUR' ? '€' : p.valuta;
  return (
    <div className="bbn-drawer-root" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onChiudi(); } }}>
      <div className="bbn-scrim" onClick={onChiudi} aria-hidden="true" />
      <aside className="bbn-drawer" role="dialog" aria-modal="true" aria-label={`${testi.detailOf} ${nome}`} data-testid="stock-detail">
        <header className="bbn-drawer-head">
          <IconaTitolo ticker={p.ticker} nome={p.nome} dimensione="lg" />
          <div className="bbn-drawer-title"><b>{nome}</b><span>{p.ticker} · {p.valuta}</span></div>
          <button ref={chiudiRef} type="button" className="bbn-icon-btn" onClick={onChiudi} aria-label={testi.close} title={testi.close}><X size={18} aria-hidden="true" /></button>
        </header>
        <div className="bbn-drawer-price">
          <span className="num">{finito(p.prezzo_live) ? `${fmtNum(p.prezzo_live, 2)} ${valuta}` : fmtNum(null)}</span>
          {p.price_stale && <span className="bbn-warn-pill">{testi.stalePrice}</span>}
          <PastigliaVariazione valore={finito(de) ? de : finito(dp) ? dp : null} grande lampo={finito(p.prezzo_live) ? p.prezzo_live : null}>
            <span className="num">{fmtEUR(finito(de) ? de : null, true)}{finito(dp) ? ` · ${fmtPct(dp)}` : ''} {testi.today}</span>
          </PastigliaVariazione>
        </div>
        <div className="bbn-drawer-chart">
          <TvChartPanel ticker={p.ticker} height={340} defaultRange={5} defaultInterval={4} timeSelects />
        </div>
        <dl className="bbn-stats">
          <div><dt>{testi.value}</dt><dd className="num">{fmtEUR(finito(p.valore_mercato) ? p.valore_mercato : null)}</dd></div>
          <div><dt>{testi.totalPl}</dt><dd className={'num ' + (finito(p.pl_eur) ? (p.pl_eur > 0 ? 'is-up' : p.pl_eur < 0 ? 'is-down' : '') : '')}>
            {fmtEUR(finito(p.pl_eur) ? p.pl_eur : null, true)}{finito(p.pl_pct) ? ` · ${fmtPct(p.pl_pct)}` : ''}</dd></div>
          <div><dt>{testi.weight}</dt><dd className="num">{finito(p.peso_pct) ? fmtNum(p.peso_pct, 1) + '%' : fmtNum(null)}</dd></div>
          <div><dt>{testi.quantity}</dt><dd className="num">{finito(p.quantita) ? fmtNum(p.quantita, p.quantita % 1 ? 4 : 0) : fmtNum(null)}</dd></div>
        </dl>
        <button type="button" className="bbn-link bbn-drawer-link" onClick={() => onApriMercati(p.ticker)}>{testi.openMarkets} →</button>
      </aside>
    </div>
  );
}
