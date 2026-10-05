import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity } from 'lucide-react';
import { hourlyBuckets, hhmm, type FeedItem } from './calcoli';
import { parole } from './parole';

/** Curva morbida che non scavalca i punti (niente gobbe sotto lo zero). */
function smooth(pts: Array<[number, number]>): string {
  return pts.reduce((d, [x, y], i, a) => {
    if (!i) return `M${x},${y}`;
    const [x0, y0] = a[i - 1], [xp, yp] = a[i - 2] || a[i - 1], [xn, yn] = a[i + 1] || [x, y];
    const lo = Math.min(y0, y), hi = Math.max(y0, y);
    const c1y = Math.min(hi, Math.max(lo, y0 + (y - yp) / 6)), c2y = Math.min(hi, Math.max(lo, y - (yn - y0) / 6));
    return d + ` C${x0 + (x - xp) / 6},${c1y} ${x - (xn - x0) / 6},${c2y} ${x},${y}`;
  }, '');
}

/** Ritmo del flusso: notizie per ora nelle ultime 24 ore, aree impilate per tono. */
export default function RitmoFlusso({ feed }: { feed: FeedItem[] }) {
  const w = parole();
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // si ricalcola a ogni nuova lettura del flusso (60 s) e quindi scorre con l'ora
  const hours = useMemo(() => hourlyBuckets(feed), [feed]);
  const totals = hours.map(h => h.pos + h.neu + h.neg);
  const total = totals.reduce((a, b) => a + b, 0);
  const peakI = totals.reduce((m, v, i) => (v > totals[m] ? i : m), 0);
  const W = size.w, H = size.h, padT = 18, padB = 20, ih = Math.max(1, H - padT - padB);
  const max = Math.max(1, ...totals) * 1.12;
  const X = (i: number) => (i / 23) * W, Y = (v: number) => padT + ih - (v / max) * ih;
  const cum = hours.map(h => [h.pos, h.pos + h.neu, h.pos + h.neu + h.neg]);
  const layer = (k: number, prev: number) => {
    const top = cum.map((c, i) => [X(i), Y(c[k])] as [number, number]);
    const bot = cum.map((c, i) => [X(i), Y(prev < 0 ? 0 : c[prev])]).reverse();
    return smooth(top) + ' L' + bot.map(p => p.join(',')).join(' L') + ' Z';
  };
  const ora = (i: number) => hhmm(hours[i].start);
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(Math.max(0, Math.min(23, Math.round(((e.clientX - r.left) / Math.max(1, r.width)) * 23))));
  };
  const h = hover != null ? hours[hover] : null;
  return (
    <section className="bbn-card news-pulse" aria-labelledby="news-pulse-h">
      <header className="bbn-card-head">
        <span className="news-ci" aria-hidden="true"><Activity size={15} /></span>
        <h2 id="news-pulse-h">{w.pulse}</h2>
        <span className="bbn-card-note">{w.last24h}</span>
        <span className="bbn-grow" />
        <span className="news-lg"><i className="is-pos" />{w.positive}</span>
        <span className="news-lg"><i className="is-neu" />{w.neutral}</span>
        <span className="news-lg"><i className="is-neg" />{w.negative}</span>
      </header>
      <div className="news-pulse-chart" ref={box}>
        {total === 0 && <p className="news-chart-empty">{w.pulseEmpty}</p>}
        {W > 0 && H > 0 && total > 0 && (
          <svg width={W} height={H} role="img" aria-label={w.pulseAria(total, totals[peakI], ora(peakI))}
            onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
            <defs>
              {(['pos', 'neu', 'neg'] as const).map(k => (
                <linearGradient key={k} id={`news-g-${k}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" className={`news-stop-${k}`} stopOpacity=".85" />
                  <stop offset="1" className={`news-stop-${k}`} stopOpacity=".22" />
                </linearGradient>
              ))}
            </defs>
            {[0.5, 1].map(f => <line key={f} className="gl" x1={0} x2={W} y1={Y((max / 1.12) * f)} y2={Y((max / 1.12) * f)} />)}
            <line className="gl" x1={0} x2={W} y1={Y(0)} y2={Y(0)} />
            <path d={layer(2, 1)} fill="url(#news-g-neg)" />
            <path d={layer(1, 0)} fill="url(#news-g-neu)" />
            <path d={layer(0, -1)} fill="url(#news-g-pos)" />
            <path d={smooth(cum.map((c, i) => [X(i), Y(c[2])]))} className="tot" />
            <circle cx={X(peakI)} cy={Y(totals[peakI])} r={3.5} className="pk-dot" />
            <text className="pk" x={Math.max(40, Math.min(W - 40, X(peakI)))} y={Y(totals[peakI]) - 8} textAnchor="middle">{w.peak(totals[peakI], ora(peakI))}</text>
            <circle className="now-ring" cx={X(23)} cy={Y(totals[23])} r={4} />
            <circle className="now" cx={X(23)} cy={Y(totals[23])} r={4} />
            {[0, 3, 6, 9, 12, 15, 18, 21].map(i => (
              <text key={i} className="ax" x={X(i)} y={H - 4} textAnchor={i ? 'middle' : 'start'}>{ora(i)}</text>
            ))}
            <text className="ax is-now" x={W} y={H - 4} textAnchor="end">{w.now}</text>
            {hover != null && <line className="hov" x1={X(hover)} x2={X(hover)} y1={padT} y2={Y(0)} />}
          </svg>
        )}
        {h && hover != null && (
          <div className="news-tip" style={{ left: Math.max(70, Math.min(W - 70, X(hover))), top: Y(totals[hover]) - 8 }} role="status">
            <b>{w.tipHour(ora(hover), totals[hover])}</b>
            <span><i className="is-pos" />{h.pos} {w.positive.toLowerCase()}</span>
            <span><i className="is-neu" />{h.neu} {w.neutral.toLowerCase()}</span>
            <span><i className="is-neg" />{h.neg} {w.negative.toLowerCase()}</span>
          </div>
        )}
      </div>
    </section>
  );
}
