import { useLayoutEffect, useRef, useState } from 'react';
import { useT } from '@/i18n/provider';
import { frase } from '@/lib/frase';
import { linguaCorrente, localeDi } from '@/i18n/lingua';

/* ════════════════════════════════════════════════════════════
   NASTRO D'ESECUZIONE — strumento-firma di F3 (Opus 5, 26/07)

   Disegna come e' nata la risposta: l'asse e' il tempo MISURATO
   in pagina, ogni tacca e' una chiamata a uno strumento (larghezza
   = durata, altezza = byte tornati, colore = esito), la traccia
   ambra e' il ritmo del testo che arriva.

   Materia prima: gli eventi SSE tool_use_start / tool_result /
   delta / done, che prima di oggi finivano in console.log.

   Regole di casa rispettate:
   - SVG misurato in PIXEL REALI (useBox): mai preserveAspectRatio
     "none", nessuna scritta stirata;
   - interattivo: hover/click danno la lettura numerica esatta;
   - i tempi sono misurati DAL CLIENT e la pagina lo scrive: il
     backend non manda timestamp;
   - sulle risposte storiche la traccia NON esiste (il backend
     salva solo il testo) e il buco si DICHIARA.
   ════════════════════════════════════════════════════════════ */

export interface ToolCall {
  n: number;               // progressivo nella risposta
  id: string;              // tool_id: appaia tool_use_start e tool_result
  name: string;
  iteration: number;
  t0: number;              // ms dall'inizio della risposta
  t1?: number;             // arrivo del risultato
  ok?: boolean;
  bytes?: number;
  preview?: string;
  arg?: string;
}

export interface FlowPoint { t: number; len: number }

/* Colori dai token della palette (--bbn-*): lo stesso SVG vale per Chiaro e Scuro.
   Esito ok verde, errore rosso, in corso blu; la traccia del testo e' blu. */
const OK = 'var(--bbn-good)', KO = 'var(--bbn-bad)', RUN = 'var(--bbn-accent)';
const PAINT = { axis: 'var(--bbn-line)', tick: 'var(--bbn-muted)', label: 'var(--bbn-muted)', halo: 'var(--bbn-text)' } as const;


export function fmtKB(b?: number) {
  if (b == null) return null;
  if (b < 1024) return b.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }) + ' B';
  return (b / 1024).toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 1, useGrouping: true }) + ' KB';
}
export function fmtMs(ms?: number) {
  if (ms == null) return null;
  return ms < 1000 ? Math.round(ms) + ' ms'
    : (ms / 1000).toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 1, useGrouping: true }) + ' s';
}

interface NastroProps {
  calls: ToolCall[];
  flow: FlowPoint[];
  durata: number | null;             // ms totali della risposta (null = ancora in corso)
  streaming: boolean;
  disponibile: boolean;              // false = risposta storica: traccia mai salvata
  hot: number | null;
  onHot: (n: number | null) => void;
  pin: number | null;
  onPin: (n: number | null) => void;
  motivoAssenza?: string;
}

export default function NastroEsecuzione(props: NastroProps) {
  return <CompactTape {...props} />;
}

function CompactTape({ calls, flow, durata, streaming, disponibile, hot, onHot, pin, onPin,
  motivoAssenza }: NastroProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = chartRef.current;
    if (!element) return;
    const measure = () => setWidth(element.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const tr = useT();
  const paint = PAINT;
  const selected = calls.find(c => c.n === (pin ?? hot)) || null;
  const tMax = Math.max(durata ?? 0, ...calls.map(c => c.t1 ?? c.t0), ...flow.map(f => f.t), 1000);
  const plotW = Math.max(1, width - 28);
  const x = (ms: number) => 14 + plotW * Math.min(1, ms / tMax);
  const laneLastX: number[] = [];
  const lanes = calls.map(c => {
    const px = x(c.t0);
    let lane = laneLastX.findIndex(last => px - last >= 9);
    if (lane < 0) lane = laneLastX.length;
    laneLastX[lane] = px;
    return lane;
  });
  const laneCount = Math.min(4, Math.max(1, ...lanes.map(lane => lane + 1)));
  const maxRate = Math.max(...flow.map((f, i) => i ? (f.len - flow[i - 1].len) / Math.max(1, f.t - flow[i - 1].t) : 0), 1e-6);
  const textPath = flow.length > 2 ? flow.map((f, i) => {
    const rate = i ? (f.len - flow[i - 1].len) / Math.max(1, f.t - flow[i - 1].t) : 0;
    return (i ? 'L' : 'M') + ' ' + x(f.t) + ' ' + (53 - 7 * (rate / maxRate));
  }).join(' ') : '';
  const steps = [1, 2, 5, 10, 20, 30, 60, 120].find(step => tMax / 1000 / step <= 4) ?? 300;
  const ticks: number[] = [];
  for (let sec = 0; sec * 1000 <= tMax; sec += steps) ticks.push(sec);
  const state = !disponibile ? 'unavailable' : streaming ? 'inProgress' : 'complete';

  /* Modern tape is deliberately a compact measured timeline for the right rail. */
  return (
    <div className="tape chat-modern-tape" data-tape-state={state}>
      <div className="chat-modern-tape-head">
        <b>{tr('communications.tapeTitle')}</b>
        <span className="side">{!disponibile ? tr('communications.tapeUnavailable')
          : streaming ? tr('communications.tapeProgress')
          : durata != null ? tr('communications.tapeTotal', { a: fmtMs(durata) ?? '—' }) : '—'}</span>
      </div>
      <div className="chat-modern-tape-content">
        <div className="chat-modern-tape-chart" ref={chartRef}>
          {disponibile && width > 0 && (calls.length > 0 || streaming) && <svg width={width} height={66} aria-hidden="true">
            <line x1={14} y1={45} x2={width - 14} y2={45} style={{ stroke: paint.axis }} />
            {ticks.map(sec => <g key={sec}>
              <line x1={x(sec * 1000)} y1={42} x2={x(sec * 1000)} y2={48} style={{ stroke: paint.tick }} />
              <text x={x(sec * 1000)} y={63} style={{ fill: paint.label }} fontSize={11} fontWeight={600} textAnchor="middle">{sec}s</text>
            </g>)}
            {calls.map((c, i) => {
              const cy = 8 + (lanes[i] % laneCount) * 9;
              const color = c.ok === false ? KO : c.ok == null ? RUN : OK;
              return <g key={c.id}>
                {c.t1 != null && <line x1={x(c.t0)} y1={cy} x2={Math.max(x(c.t0) + 2, x(c.t1))} y2={cy}
                  style={{ stroke: color }} strokeWidth={3} strokeLinecap="round" opacity={selected?.n === c.n ? 1 : .6} />}
                <circle cx={x(c.t0)} cy={cy} r={selected?.n === c.n ? 4.5 : 3.5}
                  style={{ fill: color, stroke: selected?.n === c.n ? paint.halo : 'none' }} />
              </g>;
            })}
            {textPath && <path d={textPath} fill="none" style={{ stroke: RUN }} strokeWidth={1.75} />}
          </svg>}
        </div>
        {!disponibile ? (
          <div className="tapevuoto"><div><b>{frase(tr('communications.tapeUnavailableTitle'))}</b> — {motivoAssenza ?? tr('communications.tapeHistoryMissing')}</div></div>
        ) : calls.length === 0 && !streaming ? (
          <div className="tapevuoto"><div><b>{frase(tr('communications.tapeNoTools'))}</b> — {tr('communications.tapeModelOnly')}</div></div>
        ) : (
          <>
            {flow.length > 2 && <small className="chat-modern-tape-flow">
              {tr('communications.tapeText', { a: flow[flow.length - 1].len.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }) })}
            </small>}
            {calls.length > 0 && <div className="chat-modern-tape-events" aria-label={tr('communications.chainHint')}>
              {calls.map(c => {
                const selectedNow = (pin ?? hot) === c.n;
                const result = c.ok === false ? tr('communications.declaredError') : c.ok == null ? tr('communications.inProgress') : tr('communications.outcomeOk');
                const durationText = c.t1 != null ? fmtMs(c.t1 - c.t0) : tr('communications.inProgress');
                return <button key={c.id} type="button" className={'chat-modern-tape-event' + (selectedNow ? ' selected' : '') + (c.ok === false ? ' error' : '')}
                  aria-pressed={pin === c.n}
                  aria-label={tr('communications.callAccessible', { a: c.n, b: c.name, c: c.iteration, d: result }) +
                    (c.bytes != null ? tr('communications.bytesReturned', { a: fmtKB(c.bytes) ?? tr('communications.unavailable') }) : '') +
                    tr('communications.callDuration', { a: durationText ?? tr('communications.unavailable') })}
                  onMouseEnter={() => onHot(c.n)} onMouseLeave={() => onHot(null)}
                  onFocus={() => onHot(c.n)} onBlur={() => onHot(null)}
                  onClick={() => onPin(pin === c.n ? null : c.n)}>
                  <span className="chat-modern-tape-event-name">[{c.n}] {c.name}</span>
                  <span>{fmtKB(c.bytes) ?? (c.ok == null ? tr('communications.inProgressEllipsis') : tr('communications.unavailable'))}</span>
                  <span>{durationText}</span>
                </button>;
              })}
            </div>}
            <div className="chat-modern-tape-readout" aria-live="polite">
              {selected ? <>
                <b>[{selected.n}] {selected.name}</b>
                {pin === selected.n && <span className="pin"> {tr('communications.pinned')}</span>}
                {selected.arg && <span>{tr('communications.argument')}: {selected.arg}</span>}
                <span>{tr('communications.outcome')}: {selected.ok == null ? tr('communications.inProgressEllipsis') : selected.ok ? tr('communications.outcomeOk') : tr('communications.declaredError')}</span>
                <span>{tr('communications.returned')}: {fmtKB(selected.bytes) ?? tr('communications.unavailable')}</span>
                <span>{tr('communications.iteration')}: {selected.iteration} · {selected.t1 != null ? fmtMs(selected.t1 - selected.t0) : tr('communications.inProgress')}</span>
              </> : <><b>{frase(tr('communications.reading'))}</b><span>{tr('communications.tapeReadingHint')}</span></>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
