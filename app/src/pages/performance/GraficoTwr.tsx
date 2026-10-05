import { useMemo, useRef, useState } from 'react';
import { mediaMobile, sottAcqua } from './calcoli';
import { dataBreve, num, pct } from './formato';
import { parole } from './parole';

const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
/** viewBox fisso: l'SVG si allunga in larghezza (preserveAspectRatio none, tratti non scalati). */
const W = 1000, H = 320;

function percorso(valori: (number | null)[], x: (i: number) => number, y: (v: number) => number) {
  let d = '', aperto = false;
  valori.forEach((v, i) => {
    if (!finito(v)) { aperto = false; return; }
    d += (aperto ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1);
    aperto = true;
  });
  return d;
}

/** Indice TWR del periodo con SPY tratteggiato (ribasato sulla base del periodo) e medie mobili.
 *  Il drawdown resta nel tooltip e nel riquadro «Max drawdown» (fascia rossa tolta su richiesta, 02/10/2026). */
export default function GraficoTwr({ date, indice, spy, base, mostraSpy, mostraMedie, ricostruitaFino }: {
  date: string[];
  indice: number[];
  /** SPY allineato a `date` (stessa lunghezza, null dove manca) */
  spy: (number | null)[] | null;
  base: number;
  mostraSpy: boolean;
  mostraMedie: boolean;
  /** primo giorno della serie ufficiale: prima la serie è ricostruita */
  ricostruitaFino: string | null;
}) {
  const w = parole();
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const dati = useMemo(() => {
    const d = date.slice(base), v = indice.slice(base);
    // SPY ribasato sul primo punto comune del periodo, sulla scala dell'indice
    let s: (number | null)[] | null = null;
    if (spy) {
      const parte = spy.slice(base);
      const k = parte.findIndex(finito);
      if (k >= 0 && finito(v[k]) && (parte[k] as number) > 0) {
        const f = (v[k] as number) / (parte[k] as number);
        s = parte.map(x => (finito(x) ? x * f : null));
      }
    }
    return { d, v, s, m20: mediaMobile(indice, 20).slice(base), m50: mediaMobile(indice, 50).slice(base), uw: sottAcqua(indice.slice(base)) };
  }, [date, indice, spy, base]);
  const n = dati.v.length;
  if (n < 2) return <p className="perf-state">{w.unavailable}</p>;
  const visibili = [...dati.v, ...(mostraSpy && dati.s ? dati.s : []), ...(mostraMedie ? [...dati.m20, ...dati.m50] : [])].filter(finito);
  const lo = Math.min(...visibili), hi = Math.max(...visibili), pad = (hi - lo) * 0.12 || 1;
  const x = (i: number) => 8 + i * (W - 16) / (n - 1);
  const y = (v: number) => 12 + (hi + pad - v) * (H - 24) / (hi - lo + 2 * pad);
  const linea = percorso(dati.v, x, y);
  const muovi = (clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r || r.width <= 0) return;
    const k = Math.round(((clientX - r.left) / r.width * W - 8) / ((W - 16) / (n - 1)));
    setHover(Math.max(0, Math.min(n - 1, k)));
  };
  const tacche = [0, 1, 2, 3, 4].map(t => Math.round(t * (n - 1) / 4));
  const mostraRicostruita = !!ricostruitaFino && ricostruitaFino > dati.d[0];
  return (
    <div ref={ref} className="perf-chart-wrap" onMouseMove={e => muovi(e.clientX)} onMouseLeave={() => setHover(null)}>
      <div className="perf-chart">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={w.chartLabel}>
          <defs>
            <linearGradient id="perfGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="var(--bbn-chart-top)" /><stop offset="1" stopColor="var(--bbn-chart-bottom)" />
            </linearGradient>
          </defs>
          <path d={`${linea}L${x(n - 1)} ${H}L${x(0)} ${H}Z`} fill="url(#perfGrad)" />
          {mostraMedie && <><path className="perf-sma20" d={percorso(dati.m20, x, y)} /><path className="perf-sma50" d={percorso(dati.m50, x, y)} /></>}
          {mostraSpy && dati.s && <path className="perf-spy" d={percorso(dati.s, x, y)} />}
          <path className="perf-line" d={linea} />
        </svg>
      </div>
      {hover != null && (
        <>
          <span className="perf-cross" style={{ left: `${x(hover) / W * 100}%` }} />
          <div className="perf-tip" style={{ left: `clamp(80px, ${x(hover) / W * 100}%, calc(100% - 80px))` }}>
            <b>{num(dati.v[hover], 2)}</b>
            <span>{dataBreve(dati.d[hover])}</span>
            {mostraSpy && dati.s && finito(dati.s[hover]) && <span>SPY {num(dati.s[hover], 2)}</span>}
            <span>{w.maxDd} {pct(dati.uw[hover], 2, false)}</span>
          </div>
        </>
      )}
      <div className="perf-axis">{tacche.map((i, k) => <span key={k}>{dataBreve(dati.d[i])}</span>)}</div>
      <div className="perf-key">
        <span><i className="is-line" />{w.portfolio}</span>
        {mostraSpy && dati.s && <span><i className="is-spy" />{w.spyEur}</span>}
        {mostraMedie && <><span><i className="is-sma20" />{w.sma20}</span><span><i className="is-sma50" />{w.sma50}</span></>}
        {mostraRicostruita && <span className="perf-key-end">{w.reconstructedUntil(dataBreve(ricostruitaFino))}</span>}
      </div>
    </div>
  );
}
