import { useMemo, useState } from 'react';
import { Radar } from 'lucide-react';
import { hhmm, radarLayout, sentBucket, themeCards, themeLabelOf, tsOf, type FeedItem } from './calcoli';
import { parole } from './parole';

const RC = 120, R0 = 12, RM = 112;
const rad = (ageMin: number) => R0 + (RM - R0) * Math.sqrt(Math.min(1, Math.max(0, ageMin / 1440)));
const P = (r: number, a: number): [number, number] => [RC + r * Math.cos((a * Math.PI) / 180), RC + r * Math.sin((a * Math.PI) / 180)];

/** Radar dei temi: spicchi per tema, raggio = età (centro = adesso, bordo = 24 ore),
 *  colore = tono, grandezza = rilevanza. Spicchio o voce della legenda filtrano il flusso;
 *  un punto apre la notizia nel dettaglio. */
export default function RadarTemi({ feed, favSet, themes, onToggleTheme, selectedId, onSelect }: {
  feed: FeedItem[];
  favSet: Set<string>;
  themes: string[];
  onToggleTheme: (key: string) => void;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const w = parole();
  const [hov, setHov] = useState<string | null>(null);
  const { sectors, points } = useMemo(() => radarLayout(feed), [feed]);
  const schede = useMemo(() => themeCards(feed, sectors), [feed, sectors]);
  const total = points.length;
  // «altro» raccoglie temi diversi: non è un filtro unico
  const filtrabile = (key: string) => key !== '__altro';
  const wedge = (a0: number, a1: number) => {
    if (a1 - a0 >= 359.9) return `M${RC},${RC - RM - 6} A${RM + 6},${RM + 6} 0 1 1 ${RC - 0.01},${RC - RM - 6} Z`;
    const [x0, y0] = P(RM + 6, a0), [x1, y1] = P(RM + 6, a1);
    return `M${RC},${RC} L${x0},${y0} A${RM + 6},${RM + 6} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1},${y1} Z`;
  };
  const hovSector = sectors.find(s => s.key === hov);
  // nome del tema sullo spicchio, solo dove c'è spazio; tagliato con il segno del taglio
  const etichetta = (k: string) => { const t = themeLabelOf(k); return t.length > 18 ? t.slice(0, 17) + '…' : t; };
  // nomi dei temi FUORI dal cerchio, a metà di ogni spicchio; sullo stesso lato si
  // distanziano in verticale, così spicchi stretti vicini non si sovrappongono
  const etichette = (() => {
    const out = sectors.map(s => {
      const am = (s.a0 + s.a1) / 2, c = Math.cos((am * Math.PI) / 180);
      const [x, y] = P(RM + 16, am), [x0, y0] = P(RM + 7, am), [x1, y1] = P(RM + 12, am);
      return { key: s.key, x, y, x0, y0, x1, y1, anchor: (c > 0.25 ? 'start' : c < -0.25 ? 'end' : 'middle') as 'start' | 'end' | 'middle' };
    });
    for (const lato of [out.filter(l => l.x >= RC), out.filter(l => l.x < RC)]) {
      lato.sort((a, b) => a.y - b.y);
      for (let i = 1; i < lato.length; i++) if (lato[i].y - lato[i - 1].y < 17) lato[i].y = lato[i - 1].y + 17;
    }
    return out;
  })();
  return (
    <section className="bbn-card news-radar" aria-labelledby="news-radar-h">
      <header className="bbn-card-head">
        <span className="news-ci" aria-hidden="true"><Radar size={15} /></span>
        <h2 id="news-radar-h">{w.radar}</h2>
        <span className="bbn-card-note">{w.radarNote}</span>
      </header>
      {total === 0 ? <p className="news-chart-empty is-block">{w.radarEmpty}</p> : (
        <div className="news-radar-body">
          <div className="news-radar-scope">
            <svg viewBox="-96 -22 432 284" role="img" aria-label={w.radarAria(total)} className={hov ? 'has-hov' : undefined}>
              <defs>
                <radialGradient id="news-rbg"><stop offset="0" className="news-stop-acc" stopOpacity=".16" /><stop offset="1" className="news-stop-acc" stopOpacity="0" /></radialGradient>
              </defs>
              <circle cx={RC} cy={RC} r={RM + 6} fill="url(#news-rbg)" />
              {sectors.map((s, i) => (
                <path key={s.key} d={wedge(s.a0, s.a1)}
                  className={'wedge' + (i % 2 ? ' is-alt' : '') + (themes.includes(s.key) ? ' is-on' : '') + (hov === s.key ? ' is-hov' : '') + (filtrabile(s.key) ? '' : ' is-static')}
                  onClick={filtrabile(s.key) ? () => onToggleTheme(s.key) : undefined}
                  onMouseEnter={() => setHov(s.key)} onMouseLeave={() => setHov(null)}>
                  <title>{`${themeLabelOf(s.key)}: ${w.radarCount(s.n)}`}</title>
                </path>
              ))}
              {([[60, '1 h'], [360, '6 h'], [1440, '24 h']] as Array<[number, string]>).map(([m, l]) => (
                <g key={l}>
                  <circle className={'ring' + (m < 1440 ? ' is-dash' : '')} cx={RC} cy={RC} r={rad(m)} />
                  <text className="rl" x={RC + rad(m) - 3} y={RC - 3} textAnchor="end">{l}</text>
                </g>
              ))}
              {[...points].sort((a, b) => b.ageMin - a.ageMin).map(p => {
                const rel = p.item.relevance ?? 0, rr = 1.6 + Math.max(1, rel) * 0.32;
                const [x, y] = P(rad(p.ageMin), p.angle);
                const tone = sentBucket(p.item.sentiment);
                const tk = (p.item.ticker_mentioned || '').toUpperCase();
                const id = p.item.id;
                return (
                  <g key={id ?? p.item.title} className={'blip is-' + tone + (id === selectedId ? ' is-sel' : '') + (hov && p.key !== hov ? ' is-dim' : '')}
                    onClick={() => id != null && onSelect(id)}>
                    {p.ageMin < 60 && <circle className="fresh" cx={x} cy={y} r={rr} />}
                    {rel >= 8 && <circle className="halo" cx={x} cy={y} r={rr + 2.4} />}
                    {tk && favSet.has(tk) && <circle className="favr" cx={x} cy={y} r={rr + (rel >= 8 ? 4.6 : 2.6)} />}
                    <circle className="c" cx={x} cy={y} r={rr} />
                    <title>{`${hhmm(tsOf(p.item))}${tk ? ' · ' + tk : ''} · ${p.item.title} · ${w.relOf(rel)}`}</title>
                  </g>
                );
              })}
              {etichette.map(l => (
                <g key={'l' + l.key} className={'wl' + (hov === l.key || themes.includes(l.key) ? ' is-on' : '')}>
                  <line x1={l.x0} y1={l.y0} x2={l.x1} y2={l.y1} />
                  <text x={l.x} y={l.y} textAnchor={l.anchor} dominantBaseline="middle">{etichetta(l.key)}</text>
                </g>
              ))}
              <circle cx={RC} cy={RC} r={3} className="center" />
            </svg>
            <div className="news-radar-sweep" aria-hidden="true" />
            <div className={'news-radar-cap' + (hovSector ? ' is-on' : '')} aria-hidden="true">
              {hovSector && <><b>{themeLabelOf(hovSector.key)}</b>{w.radarCount(hovSector.n)}</>}
            </div>
          </div>
          <div className="news-radar-legend">
            <div className="news-radar-rows">
              {schede.map(c => {
                const nome = themeLabelOf(c.key);
                const pct = (v: number) => (c.n ? Math.round((v / c.n) * 100) : 0);
                const delta = c.prev == null ? null : c.n - c.prev;
                const lastTs = c.last ? tsOf(c.last) : 0;
                return (
                  <div key={c.key} className={'news-lcard' + (hov === c.key ? ' is-hov' : '') + (themes.includes(c.key) ? ' is-on' : '')}
                    onMouseEnter={() => setHov(c.key)} onMouseLeave={() => setHov(null)}>
                    {filtrabile(c.key)
                      ? <button type="button" className="news-lhead" aria-pressed={themes.includes(c.key)} onClick={() => onToggleTheme(c.key)} title={w.radarFilter(nome)}>
                          <b>{nome}</b><span className="num">{w.radarCount(c.n)}</span>
                        </button>
                      : <div className="news-lhead"><b>{nome}</b><span className="num">{w.radarCount(c.n)}</span></div>}
                    <span className="news-tone" title={w.radarTonePct(pct(c.pos), pct(c.neg))} aria-label={w.radarTonePct(pct(c.pos), pct(c.neg))}>
                      <i className="is-pos" style={{ width: `${pct(c.pos)}%` }} /><i className="is-neu" style={{ width: `${100 - pct(c.pos) - pct(c.neg)}%` }} /><i className="is-neg" style={{ width: `${pct(c.neg)}%` }} />
                    </span>
                    <span className={'news-ldelta num' + (delta == null ? ' is-na' : delta > 0 ? ' is-up' : delta < 0 ? ' is-down' : '')}
                      title={delta == null ? w.radarNoHistory : w.radarDelta(delta, c.prev!)}>
                      {delta == null ? '—' : delta > 0 ? `↑ +${delta}` : delta < 0 ? `↓ ${delta}` : '= 0'}
                    </span>
                    {c.last && c.last.id != null && (
                      <button type="button" className="news-llast" onClick={() => onSelect(c.last!.id)} title={c.last.title}>
                        <span className="tm num">{w.radarLast} {hhmm(lastTs)}</span><span className="tt">{c.last.title}</span>
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
