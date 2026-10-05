import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import type { Position } from '@/lib/api';
import { dayPct } from '@/lib/dailypl';
import { fmtNum, fmtPct } from '@/lib/format';
import { squarify } from '@/lib/treemap';
import Card, { Segmenti } from '@/components/nuova/Card';
import { parole } from './parole';
import { nomeTitolo } from './ListaPosizioni';
import { quotaCassa } from './quota-cassa';
import type { Metrica } from './ListaPosizioni';

export type VistaAllocazione = 'titoli' | 'regioni' | 'heatmap';
const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const SPAN = { totale: 30, oggi: 3 } as const;

/** Lato in px del quadrato della treemap: le etichette si decidono sui pixel
 *  veri della tessera, non sulla sua quota, perché lo stesso 2% è largo 50 px
 *  a 1280 e 120 px a 3440. Finché non è misurato non si mostra nessuna etichetta. */
function useLatoPx(): [number, (el: HTMLElement | null) => void] {
  const [lato, setLato] = useState(0);
  const [nodo, setNodo] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!nodo || typeof ResizeObserver !== 'function') return;
    const osserva = new ResizeObserver(([voce]) => setLato(voce.contentRect.width));
    osserva.observe(nodo);
    return () => osserva.disconnect();
  }, [nodo]);
  return [lato, useCallback((el: HTMLElement | null) => setNodo(el), [])];
}

/** Quante righe di etichetta entrano in una tessera di w×h px (padding 6/7 px):
 *  2 = sigla e peso, 1 = solo la sigla, 0 = niente (resta il tooltip). */
function righeEtichetta(w: number, h: number, sigla: string, peso: string): 0 | 1 | 2 {
  const largo = (testo: string, px: number) => testo.length * px + 14;
  if (w < largo(sigla, 8.6)) return 0;
  if (h >= 44 && w >= largo(peso, 7.6)) return 2;
  return h >= 28 ? 1 : 0;
}

/** Colore della tessera: verde/rosso con intensità proporzionale alla variazione. */
function riempimento(v: number | null, span: number) {
  if (v == null) return { background: 'var(--bbn-raised)', debole: true };
  const a = Math.min(1, Math.abs(v) / span) * 0.7 + 0.3;
  return { background: `rgba(var(${v >= 0 ? '--bbn-heat-up' : '--bbn-heat-down'}), ${a.toFixed(3)})`, debole: a < 0.6 };
}

export default function CardAllocazione({ posizioni, patrimonio, liquidita, motivoNd, vista, onVista, metrica, onApri }: {
  posizioni: Position[];
  patrimonio: number | null;
  liquidita: number | null;
  /** perché cassa o patrimonio sono n.d. (portfolioValues().note): si mostra, mai un peso 0 muto */
  motivoNd?: string | null;
  vista: VistaAllocazione;
  onVista: (vista: VistaAllocazione) => void;
  metrica: Metrica;
  onApri: (ticker: string) => void;
}) {
  const w = parole();
  const [regioni, setRegioni] = useState<Record<string, number> | null | 'errore'>(null);
  const [latoPx, misuraLato] = useLatoPx();
  useEffect(() => {
    let vivo = true;
    Bellomberg.concentration()
      .then(r => { if (vivo) setRegioni(r?.by_region?.weights_pct && typeof r.by_region.weights_pct === 'object' ? r.by_region.weights_pct : 'errore'); })
      .catch(() => { if (vivo) setRegioni('errore'); });
    return () => { vivo = false; };
  }, []);

  const nav = finito(patrimonio) && patrimonio > 0 ? patrimonio : null;
  const pesi = useMemo(() => {
    if (!nav) return [];
    return [...posizioni].filter(p => finito(p.valore_mercato) && p.valore_mercato > 0)
      .sort((a, b) => b.valore_mercato - a.valore_mercato)
      .map(p => ({ p, peso: (p.valore_mercato / nav) * 100 }));
  }, [posizioni, nav]);
  const { pesoCassa, investito, negativa: cassaNegativa } = quotaCassa(liquidita, nav);
  const cassaVisibile = pesoCassa != null && pesoCassa > 0 ? pesoCassa : null;

  // Concentrazione dai pesi delle sole posizioni: numero effettivo = (Σw)² / Σw².
  // Normalizzato sulla somma, così non dipende dalla base dei pesi (NAV o investito)
  // e non supera mai il numero di titoli (con 1 / Σw² e pesi sul NAV, 10 titoli
  // al 32% del patrimonio risultavano «come 105 titoli»).
  const pesiPosizioni = posizioni.map(p => p.peso_pct).filter(finito).filter(v => v > 0);
  const sommaPesi = pesiPosizioni.reduce((s, v) => s + v, 0);
  const sommaQuadrati = pesiPosizioni.reduce((s, v) => s + v ** 2, 0);
  const nEffettivo = sommaQuadrati > 0 ? sommaPesi ** 2 / sommaQuadrati : null;
  const primi5 = [...pesiPosizioni].sort((a, b) => b - a).slice(0, 5).reduce((s, v) => s + v, 0);
  const valute = posizioni.reduce<Record<string, number>>((out, p) => {
    if (finito(p.valore_mercato) && p.valore_mercato > 0) out[p.valuta || '?'] = (out[p.valuta || '?'] || 0) + p.valore_mercato;
    return out;
  }, {});
  const totaleValute = Object.values(valute).reduce((s, v) => s + v, 0);
  const valutaPrincipale = Object.entries(valute).sort((a, b) => b[1] - a[1])[0];

  let corpo: React.ReactNode;
  if (!nav && posizioni.length) corpo = <p className="bbn-empty" role="status">{w.allocationNd(motivoNd)}</p>;
  else if (!nav || !pesi.length) corpo = <p className="bbn-empty">{w.noPositions}</p>;
  else if (vista === 'titoli') {
    const top = pesi.slice(0, 4), resto = pesi.slice(4);
    const segmenti = [
      ...top.map(({ p, peso }) => ({ chiave: p.ticker, nome: nomeTitolo(p), peso })),
      ...(resto.length ? [{ chiave: 'altri', nome: w.othersStocks(resto.length), peso: resto.reduce((s, r) => s + r.peso, 0) }] : []),
      ...(cassaVisibile != null ? [{ chiave: 'cassa', nome: w.cash, peso: cassaVisibile }] : []),
    ];
    // con cassa negativa le posizioni superano il 100% del patrimonio: il disegno si riscala
    // per non sovrapporre gli archi, la legenda e il centro restano sui pesi veri
    const totaleSegmenti = segmenti.reduce((s, x) => s + x.peso, 0);
    const scala = totaleSegmenti > 100 ? 100 / totaleSegmenti : 1;
    const mix = [100, 75, 55, 38, 22];
    const colore = (i: number, chiave: string) => chiave === 'cassa' ? 'var(--bbn-raised)'
      : `color-mix(in srgb, var(--bbn-accent) ${mix[Math.min(i, mix.length - 1)]}%, var(--bbn-card))`;
    const R = 62, C = 2 * Math.PI * R;
    let acc = 0;
    corpo = (
      <div className="bbn-alloc-body">
        <div className="bbn-donut">
          <svg viewBox="0 0 150 150" aria-hidden="true">
            {segmenti.map((s, i) => {
              const len = (s.peso * scala / 100) * C;
              const el = <circle key={s.chiave} r={R} cx={75} cy={75} fill="none" stroke={colore(i, s.chiave)} strokeWidth={20}
                strokeDasharray={`${Math.max(0, len - 2)} ${C}`} strokeDashoffset={-acc} transform="rotate(-90 75 75)" />;
              acc += len;
              return el;
            })}
          </svg>
          <div className="bbn-donut-center"><b className="num">{investito == null ? fmtNum(null) : `${fmtNum(investito, 0)}%`}</b><span>{w.invested_}</span></div>
        </div>
        <ul className="bbn-legend">{segmenti.map((s, i) => (
          <li key={s.chiave}><i style={{ background: colore(i, s.chiave) }} /><span>{s.nome}</span><b className="num">{fmtNum(s.peso, 1)}%</b></li>
        ))}</ul>
      </div>
    );
  } else if (vista === 'regioni') {
    corpo = regioni === null ? <p className="bbn-empty" aria-busy="true">…</p>
      : regioni === 'errore' || !Object.keys(regioni).length ? <p className="bbn-empty" role="status">{w.regionsMissing}</p>
      : <ul className="bbn-regions">{Object.entries(regioni).sort((a, b) => b[1] - a[1]).map(([regione, peso]) => (
        <li key={regione}><span>{regione}</span><span className="bbn-bar"><i style={{ width: `${Math.max(0, Math.min(100, peso))}%` }} /></span><b className="num">{fmtNum(peso, 0)}%</b></li>
      ))}</ul>;
  } else {
    const voci = [
      ...pesi.map(({ p, peso }) => ({ chiave: p.ticker, etichetta: p.ticker.split('.')[0], nome: nomeTitolo(p), peso,
        variazione: metrica === 'oggi' ? (finito(dayPct(p)) ? dayPct(p) as number : null) : finito(p.pl_pct) ? p.pl_pct : null, posizione: true })),
      ...(cassaVisibile != null ? [{ chiave: 'cassa', etichetta: w.cash, nome: w.cash, peso: cassaVisibile, variazione: null, posizione: false }] : []),
    ];
    const tessere = squarify(voci.map(item => ({ item, weight: item.peso })), 0, 0, 100, 100);
    const span = SPAN[metrica];
    const lato = latoPx;
    corpo = (
      <div className="bbn-heat-wrap">
        <div className="bbn-treemap-stage">
        <div className="bbn-treemap" role="list" aria-label={w.heatmap} ref={misuraLato}>
          {tessere.map(t => {
            const fill = riempimento(t.item.variazione, span);
            const titolo = `${t.item.nome} · ${fmtNum(t.item.peso, 1)}% ${w.ofPortfolio}${t.item.variazione != null ? ` · ${fmtPct(t.item.variazione)}` : ''}`;
            const peso = `${fmtNum(t.item.peso, 1)}%`;
            const righe = righeEtichetta(t.w / 100 * lato, t.h / 100 * lato, t.item.etichetta, peso);
            const etichetta = righe > 0 && <><b>{t.item.etichetta}</b>{righe === 2 && <small className="num">{peso}</small>}</>;
            const stile = { left: `${t.x}%`, top: `${t.y}%`, width: `${t.w}%`, height: `${t.h}%`, background: fill.background };
            return t.item.posizione
              ? <button key={t.item.chiave} type="button" role="listitem" className={'bbn-tile' + (fill.debole ? ' is-weak' : '') + (righe ? '' : ' is-bare')} style={stile}
                  title={titolo} aria-label={titolo} onClick={() => onApri(t.item.chiave)}>
                  {etichetta}
                </button>
              : <div key={t.item.chiave} role="listitem" className={'bbn-tile is-cash' + (righe ? '' : ' is-bare')} style={stile} title={titolo} aria-label={titolo}>
                  {etichetta}
                </div>;
          })}
        </div>
        </div>
        <div className="bbn-heat-key">
          <span>{w.heatArea}</span>
          <span>{metrica === 'oggi' ? w.heatColorToday : w.heatColorTotal}</span>
          <i aria-hidden="true" />
          <div className="num"><span>{fmtPct(-span, false, 0)}</span><span>0</span><span>{fmtPct(span, true, 0)}</span></div>
        </div>
      </div>
    );
  }

  return (
    <Card titolo={w.allocation} className="bbn-alloc" data-testid="dashboard-allocation"
      azioni={<Segmenti etichetta={w.allocationView} valore={vista} onChange={onVista}
        opzioni={[{ id: 'titoli', testo: w.byStock }, { id: 'regioni', testo: w.byRegion }, { id: 'heatmap', testo: w.heatmap }]} />}>
      <div className="bbn-alloc-main">{corpo}</div>
      {nav && pesi.length > 0 && <div className="bbn-chips">
        {pesoCassa == null && <span className="bbn-warn-pill" role="status">{w.cashWeightNd(motivoNd)}</span>}
        {cassaNegativa && pesoCassa != null && <span className="bbn-warn-pill" role="status">{w.cashNegative(fmtNum(pesoCassa, 1) + '%')}</span>}
        {nEffettivo != null && <span className="bbn-chip" title={w.diversifiedFormula}>{w.diversifiedAs(fmtNum(nEffettivo, 0))}</span>}
        {pesiPosizioni.length > 5 && <span className="bbn-chip">{w.top5} <b className="num">{fmtNum(primi5, 0)}%</b></span>}
        {valutaPrincipale && totaleValute > 0 && <span className="bbn-chip">{w.inCurrency(fmtNum((valutaPrincipale[1] / totaleValute) * 100, 0) + '%', valutaPrincipale[0])}</span>}
      </div>}
    </Card>
  );
}
