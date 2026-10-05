import { useT } from '@/i18n/provider';
/* ============================================================
   F4 — IL QUADRANTE (Opus 5, 26/07)

   Lo strumento-firma della plancia orbitale, scelto dal PM sui mockup
   (mockup_f4_agents/opzione_2.html). Un giro = la durata della run;
   mezzogiorno = lo start; senso orario. Un'orbita per desk, in ordine
   di accensione: la piu' esterna al primo che si e' acceso.

   DISCIPLINA (lezioni gia' pagate, vedi montecarlo-plancia.css):
   - l'SVG disegna 1:1 sul box misurato in pixel VERI: niente
     preserveAspectRatio="none", quindi nessuna scritta stirata;
   - il box si misura FUORI dal flusso (il padre e' position:relative,
     l'svg absolute inset 0), o il ResizeObserver va in retroazione;
   - ogni etichetta e' una PIASTRA la cui scatola e' calcolata sul testo
     che contiene (letter-spacing incluso: senza, il testo esce di 8px),
     e le piastre vicine vengono separate da `layoutLabels` PRIMA di
     essere disegnate. E' cosi' che si ottiene zero testo sovrapposto:
     non a occhio, per costruzione.

   26/07, apertura dell'audit frontend: le primitive che stavano QUI
   (geometria polare, misura del testo, piastre, sigilli) sono passate in
   `lib/svg-kit.ts` e `lib/Sigil.tsx`. Non erano di F4: erano solo nate
   qui. In questo file resta il QUADRANTE — cioe' la sola cosa che parla
   della run del comitato.
   ============================================================ */
import { useMemo } from 'react';
import type { Plancia, Desk } from '@/lib/plancia-data';
import { fmtClock, fmtDurShort } from '@/lib/plancia-data';
import { TAU, MONO, angleAt, pointAt, arcPath, plateBox, layoutLabels } from '@/lib/svg-kit';
import type { LabLine } from '@/lib/svg-kit';
import { Sigil } from '@/lib/Sigil';
import { useInterfaceTheme } from '@/components/InterfaceThemeProvider';

const DIAL_PALETTE = {
  modern: {
    glass0: '#dbeafe', glass1: '#eff6ff', glassClear: '#fff',
    bezel0: '#dbeafe', bezel1: '#f1f5f9', bezel2: '#e2e8f0',
    hub0: '#fff', hub1: '#f8fafc', hub2: '#f1f5f9',
    core: '#d97706', ringOuter: '#cbd5e1', ringInner: '#94a3b8',
    index: '#64748b', grid: '#e2e8f0', corona: '#cbd5e1', tickStrong: '#64748b',
    tickSoft: '#cbd5e1', minuteText: '#475569', minuteHalo: '#fff',
    phaseMuted: '#64748b', orbitBase: '#cbd5e1', node: '#fff',
    nodeStroke: '#64748b', bad: '#b91c1c', handShadow: '#64748b',
    hand: '#b45309', handTip: '#f59e0b', centerFill: '#fff',
    centerStroke: '#64748b', hubStroke: '#cbd5e1', hubInset1: '#e2e8f0',
    hubInset2: '#cbd5e1', muted: '#475569', text: '#0f172a',
    soft: '#526176', costLabel: '#475569', line: '#cbd5e1', warn: '#b45309', good: '#047857',
    start: '#1d4ed8', plate: '#fff', plateStroke: '#cbd5e1',
  },
  /* Nuova, tema scuro: stessa struttura della modern chiara, superfici
     slate-navy del contratto (--bbt-*), testi >= 4.5:1 sulla piastra. */
  modernDark: {
    glass0: '#6b93ff', glass1: '#2f62f5', glassClear: '#000',
    bezel0: '#3a4a64', bezel1: '#263349', bezel2: '#1a2436',
    hub0: '#202b3f', hub1: '#1a2436', hub2: '#151e2d',
    core: '#e7ae45', ringOuter: '#3a4a64', ringInner: '#53647f',
    index: '#75849b', grid: '#243149', corona: '#384861', tickStrong: '#75849b',
    tickSoft: '#384861', minuteText: '#9aa8bd', minuteHalo: '#151e2d',
    phaseMuted: '#9aa8bd', orbitBase: '#384861', node: '#1a2436',
    nodeStroke: '#75849b', bad: '#ff8597', handShadow: '#000',
    hand: '#f0c062', handTip: '#ffd98a', centerFill: '#202b3f',
    centerStroke: '#75849b', hubStroke: '#384861', hubInset1: '#151e2d',
    hubInset2: '#384861', muted: '#b4c0d2', text: '#f2f5fa',
    soft: '#9aa8bd', costLabel: '#9aa8bd', line: '#384861', warn: '#f0c062', good: '#4fd8a1',
    start: '#9db8ff', plate: '#202b3f', plateStroke: '#384861',
  },
} as const;
type DialPalette = { [K in keyof typeof DIAL_PALETTE.modern]: string };

/* Keep each specialist's hue as its identity, while darkening only the
   presentation paint enough to read on white. Source data stays untouched. */
function accessibleDeskColor(color: string) {
  const match = /^#([0-9a-f]{6})$/i.exec(color);
  if (!match) return '#334155';
  const n = Number.parseInt(match[1], 16);
  const source = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  const linear = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const luminance = (scale: number) => {
    const [r, g, b] = source.map(v => linear(v * scale));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  let scale = 1;
  while (scale > 0.3 && luminance(scale) > 0.17) scale *= 0.92;
  return '#' + source.map(v => Math.round(v * scale).toString(16).padStart(2, '0')).join('');
}

/* Dark counterpart of accessibleDeskColor: the hue stays the desk's identity,
   but it is lightened (never darkened) until it reads on the dark plate. */
export function accessibleDeskColorDark(color: string) {
  const match = /^#([0-9a-f]{6})$/i.exec(color);
  if (!match) return '#c7d1df';
  const n = Number.parseInt(match[1], 16);
  const source = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  const linear = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const mix = (t: number) => source.map(v => Math.round(v + (255 - v) * t));
  const luminance = (rgb: number[]) => 0.2126 * linear(rgb[0]) + 0.7152 * linear(rgb[1]) + 0.0722 * linear(rgb[2]);
  let t = 0;
  while (t < 0.8 && luminance(mix(t)) < 0.3) t += 0.04;
  return '#' + mix(t).map(v => v.toString(16).padStart(2, '0')).join('');
}

function wrapPlateLines(lines: LabLine[], maxWidth: number): LabLine[] {
  const wrapped: LabLine[] = [];
  for (const line of lines) {
    const words = line.t.trim().split(/\s+/);
    let current = '';
    for (const word of words) {
      const candidate = current ? `${current} ${word}` : word;
      if (current && plateBox([{ ...line, t: candidate }]).w > maxWidth) {
        wrapped.push({ ...line, t: current });
        current = word;
      } else current = candidate;
    }
    if (current) wrapped.push({ ...line, t: current });
  }
  return wrapped;
}

/* staffa di tracciamento: quattro angoli, mai un rettangolo pieno */
function Bracket({ cx, cy, w, h, color, op = 0.75 }: {
  cx: number; cy: number; w: number; h: number; color: string; op?: number;
}) {
  const k = Math.min(w, h) * 0.3;
  const x0 = cx - w / 2, x1 = cx + w / 2, y0 = cy - h / 2, y1 = cy + h / 2;
  return <path fill="none" stroke={color} strokeOpacity={op} strokeWidth={1}
    d={`M${x0 + k} ${y0}H${x0}V${y0 + k} M${x1 - k} ${y0}H${x1}V${y0 + k}
        M${x0 + k} ${y1}H${x0}V${y1 - k} M${x1 - k} ${y1}H${x1}V${y1 - k}`} />;
}

/** blocco d'etichetta su piastra opaca: il testo vince sempre sul fondo */
function modernDeskLines(d: Desk, p: Plancia, koIds: string[], fmtEur: (n: number | null | undefined) => string, tr: ReturnType<typeof useT>, dark = false): LabLine[] {
  const C: DialPalette = dark ? DIAL_PALETTE.modernDark : DIAL_PALETTE.modern;
  const ko = koIds.includes(d.id), run = p.live && d.statusRun === 'running', err = !ko && d.statusRun === 'error';
  const deskColor = dark ? accessibleDeskColorDark(d.color) : accessibleDeskColor(d.color);
  const lines: LabLine[] = [
    { t: d.name.toUpperCase(), col: deskColor, size: 12.5, ls: 0.45},
    { t: tr('dashboard.dial_calls', {a: fmtDurShort(d.dur), b: d.nCalls, c: p.logTappato ? tr('dashboard.dial_log') : '', d: d.nTools}), col: C.muted, size: 11.5, ls: 0.2 },
    { t: tr('dashboard.dial_api_calls', {a: fmtEur(d.cost), b: d.apiCalls}), col: (ko ? C.warn : C.soft), size: 11.5, ls: 0.2 },
    { t: ko && run ? tr('dashboard.dial_previous_ko')
     : ko ? tr('dashboard.dial_api_ko')
     : run ? tr('dashboard.dial_running_calls', {a: d.nCalls})
     : err ? tr('dashboard.dial_specialist_ko')
     : d.statusRun === 'done' ? tr('dashboard.dial_no_api_error') : tr('dashboard.dial_result_missing'),
      col: ((ko || err) ? C.bad : run ? C.warn : d.statusRun === 'done' ? C.good : C.muted),
      size: 11.5, ls: 0.3 },
  ];
  return lines;
}

/** Reserve enough space even when all desk labels land on one side. */
export function modernDialMinimumHeight(p: Plancia, width: number, koIds: string[], fmtEur: (n: number | null | undefined) => string, tr: ReturnType<typeof useT>): number {
  const band = Math.max(156, Math.min(370, width * .2));
  const stack = p.desks.reduce((total, desk) => total + plateBox(wrapPlateLines(modernDeskLines(desk, p, koIds, fmtEur, tr), Math.max(92, band - 34)), 15).h + 18, 0);
  return Math.max(460, Math.ceil(stack + 28));
}

function Plate({ x, y, lines, align, lh = 11.5, stroke, num, palette = DIAL_PALETTE.modern }: {
  x: number; y: number; lines: LabLine[]; align: 'start' | 'end';
  lh?: number; stroke?: string; num?: number;
  palette?: DialPalette;
}) {
  const { w, h } = plateBox(lines, lh);
  const px = align === 'end' ? x - w - 6 : x - 6;
  return (
    <g>
      <rect x={px.toFixed(1)} y={(y - h / 2 - 4).toFixed(1)} width={(w + 12).toFixed(1)}
        height={(h + 8).toFixed(1)} fill={palette.plate} fillOpacity={0.98}
        stroke={stroke || palette.plateStroke} strokeOpacity={0.85} />
      {/* il numero d'orbita sta SEMPRE dal lato che guarda il quadrante: messo
          all'esterno usciva dal bordo dell'SVG e veniva tagliato di 9px
          (le piastre di destra arrivano gia' a filo del bordo) */}
      {num != null && (
        <text x={(align === 'end' ? px + w + 17 : px - 5).toFixed(1)} y={y.toFixed(1)}
          fontWeight={600} fill={palette.soft} fontSize={11} textAnchor={align === 'end' ? 'start' : 'end'}
          dominantBaseline="middle" fontFamily={MONO}>{num}</text>
      )}
      {lines.map((l, k) => (
        <text key={k} x={x.toFixed(1)} y={(y - h / 2 + lh * (k + 0.5)).toFixed(1)}
          fill={l.col} fillOpacity={l.op ?? 1} fontSize={l.size ?? 9}
          letterSpacing={l.ls ?? 0.4} textAnchor={align} dominantBaseline="middle"
          fontFamily={MONO}>{l.t}</text>
      ))}
    </g>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   IL QUADRANTE
   ══════════════════════════════════════════════════════════════════════ */
export interface QuadranteProps {
  p: Plancia;
  w: number; h: number;
  cursor: number;              // secondi puntati dalla lancetta
  pinned: boolean;
  onCursor: (t: number) => void;
  onPin: () => void;
  koIds: string[];
  fmtEur: (v: number | null | undefined) => string;
  costoRun: number | null | undefined;   // il verdetto che va nel nucleo
  koCost: number | null;                 // quanto di quel costo viene dai KO
  memoLabel: string;                     // "memo #47" oppure il buco dichiarato
}

export default function Quadrante({ p, w, h, cursor, pinned, onCursor, onPin, koIds, fmtEur,
  costoRun, koCost, memoLabel }: QuadranteProps) {
  const tr = useT();
  const dark = useInterfaceTheme().effective === 'dark';
  const C: DialPalette = dark ? DIAL_PALETTE.modernDark : DIAL_PALETTE.modern;
  const modernDeskColor = (color: string) => dark ? accessibleDeskColorDark(color) : accessibleDeskColor(color);
  const labelLh = 15;
  const g = useMemo(() => {
    const pad = 14;
    /* la fascia laterale porta la telemetria FUORI dal quadrante: larga
       quanto serve al blocco piu' largo, non di piu' */
    /* Modern reserves measured side gutters for its larger wrapped labels.
       The SVG still draws 1:1 in the same box; only the dial radius yields. */
    const band = Math.max(156, Math.min(370, w * 0.2));
    const R = Math.max(60, Math.min((h - 2 * pad) / 2, (w - 2 * band - 2 * pad) / 2));
    const rc = Math.max(30, Math.min(170, Math.min(R * 0.3, R - 48)));             // nucleo
    const rOut = Math.min(R - 10, Math.max(rc + 24, R * 0.7));
    const rIn = rc + (18);
    const n = Math.max(1, p.desks.length - 1);
    return { pad, band, R, rc, rOut, rIn, cx: w / 2, cy: h / 2, st: (rOut - rIn) / n,
             LX: band - 12, RX: w - band + 12 };
  }, [w, h, p.desks.length]);

  const S = p.scaleSec;
  const PT = (r: number, t: number) => pointAt(g.cx, g.cy, r, t, S);
  const arc = (r: number, t0: number, t1: number) => arcPath(g.cx, g.cy, r, t0, t1, S);
  const rOf = (i: number) => g.rOut - i * g.st;

  /* ── telemetria: una piastra per desk, spinta sulla fascia laterale.
        Il lato lo decide dove il desk ha CHIUSO (destra del quadrante ->
        piastra a destra), poi layoutLabels separa quelle dello stesso lato. */
  const labels = useMemo(() => {
    const mk = (d: Desk, i: number) => {
      const r = rOf(i);
      const tEnd = d.obs1 ?? 0;
      const [bx, by] = pointAt(g.cx, g.cy, r, tEnd, S);
      const right = bx >= g.cx;
      const ko = koIds.includes(d.id);
      const deskColor = modernDeskColor(d.color);
      const displayLines = wrapPlateLines(modernDeskLines(d, p, koIds, fmtEur, tr, dark), Math.max(92, g.band - 34));
      const box = plateBox(displayLines, labelLh);
      return { d, i, r, bx, by, right, ko, lines: displayLines, color: deskColor, y: by, h: box.h + 10 };
    };
    const all = p.desks.map(mk);
    const R = layoutLabels(all.filter(x => x.right), g.pad, h - g.pad, 8);
    const L = layoutLabels(all.filter(x => !x.right), g.pad, h - g.pad, 8);
    return [...R, ...L];
  }, [p.desks, g, h, S, koIds, fmtEur, tr, dark, C, labelLh, p.live, p.logTappato]);

  /* L'uscita anticipata sta DOPO tutti gli hook, mai in mezzo: al primo
     render useBox non ha ancora misurato (w=h=0) e con il return prima di
     `labels` React contava due numeri di hook diversi fra un render e
     l'altro — "Rendered more hooks than during the previous render", cioe'
     la pagina in schermata d'errore. Trovato dal collaudo, non a occhio. */
  if (w < 240 || h < 240) return <svg width={Math.max(0, w)} height={Math.max(0, h)} />;

  const live = p.live;
  const activeNow = p.windows.filter(x => cursor >= x.t0 && cursor <= x.t1);
  const [hx, hy] = PT(g.R - 6, cursor);

  const phasePlates = (() => {
        const items = p.phases.map(ph => {
          const dashed = !ph.measured;
          const [lx, ly] = PT(g.R - 66, (ph.t0 + ph.t1) / 2);
          const lines: LabLine[] = [
            { t: ph.k === 'SINTESI' ? tr('dashboard.synthesis') : ph.k === 'FRA ROUND' ? tr('dashboard.between_rounds') : ph.k, col: dashed ? C.muted : C.warn, size: 12, ls: 0.45},
            /* round APERTO: la fine non c'e' ancora, e non si scrive l'orologio come se fosse un estremo */
            { t: `${fmtClock(ph.t0)}→${ph.open ? tr('dashboard.progress_lower') : fmtClock(ph.t1)}`, col: C.muted, size: 11.5, ls: 0.2 },
          ];
          /* 11 e non 9,5: con l'interlinea stretta la riga da 9px e la riga
             degli orari si toccavano dentro la loro stessa piastra (misurato
             in collaudo: 16% di sovrapposizione su tutte e quattro le fasi) */
          const phaseLh = 15;
          const bw = plateBox(lines, phaseLh).w;
          return { ph, dashed, lx, lines, bw, phaseLh, y: ly,
            h: plateBox(lines, phaseLh).h + 8};
        });
        /* cluster transitivi per sovrapposizione orizzontale delle piastre */
        const capo_ = items.map((_, i) => i);
        const trova = (i: number): number => capo_[i] === i ? i : (capo_[i] = trova(capo_[i]));
        for (let i = 0; i < items.length; i++)
          for (let j = i + 1; j < items.length; j++)
            if (Math.abs(items[i].lx - items[j].lx) < (items[i].bw + items[j].bw) / 2 + 6)
              capo_[trova(i)] = trova(j);
        const gruppi = new Map<number, typeof items>();
        items.forEach((it, i) => {
          const k = trova(i);
          gruppi.set(k, [...(gruppi.get(k) || []), it]);
        });
        const ySep = new Map<string, number>();
        for (const gr of gruppi.values()) {
          if (gr.length < 2) continue;
          for (const it of layoutLabels(gr, g.pad + 20, h - g.pad - 20, 6)) ySep.set(it.ph.k, it.ly);
        }
    return items.map(it => ({ ...it, renderY: ySep.get(it.ph.k) ?? it.y }));
  })();
  const plateRects = [
    ...labels.map(label => {
      const box = plateBox(label.lines, labelLh), x = label.right ? g.RX - 6 : g.LX - box.w - 6;
      return { left: x, right: x + box.w + 12, top: label.ly - box.h / 2 - 4, bottom: label.ly + box.h / 2 + 4 };
    }),
    ...phasePlates.map(label => {
      const box = plateBox(label.lines, label.phaseLh);
      return { left: label.lx - label.bw / 2 - 6, right: label.lx + label.bw / 2 + 6,
        top: label.renderY - box.h / 2 - 4, bottom: label.renderY + box.h / 2 + 4 };
    }),
  ];

  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="dial"
      data-presentation="modern"
      onMouseMove={e => {
        if (pinned) return;
        const b = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
        const dx = e.clientX - b.left - g.cx, dy = e.clientY - b.top - g.cy;
        let th = Math.atan2(dx, -dy); if (th < 0) th += TAU;
        /* su una run VIVA il presente e' un tetto: oltre la lancetta non c'e'
           niente da leggere, e senza tetto la piastra scriveva ragionamenti e
           memo in minuti non ancora accaduti (review F45) */
        onCursor(Math.min((th / TAU) * S, live ? p.runSec : S));
      }}
      onClick={onPin}>
      <defs>
        {/* vetro: riflesso speculare in alto a sinistra, come su uno strumento vero */}
        <radialGradient id="f4glass" cx="32%" cy="24%" r="62%">
          <stop offset="0%" stopColor={C.glass0} stopOpacity={'.1'} />
          <stop offset="55%" stopColor={C.glass1} stopOpacity={'.035'} />
          <stop offset="100%" stopColor={C.glassClear} stopOpacity="0" />
        </radialGradient>
        {/* ghiera: luce da alto-sinistra, ombra in basso-destra */}
        <linearGradient id="f4bezel" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={C.bezel0} />
          <stop offset="42%" stopColor={C.bezel1} />
          <stop offset="100%" stopColor={C.bezel2} />
        </linearGradient>
        <radialGradient id="f4hub">
          <stop offset="0%" stopColor={C.hub0} />
          <stop offset="70%" stopColor={C.hub1} />
          <stop offset="100%" stopColor={C.hub2} />
        </radialGradient>
        <radialGradient id="f4core">
          <stop offset="0%" stopColor={C.core} stopOpacity={'.1'} />
          <stop offset="100%" stopColor={C.core} stopOpacity="0" />
        </radialGradient>
        <filter id="f4glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="3.2" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="f4soft" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="1.4" />
        </filter>
      </defs>

      {/* ── ghiera e vetro: la scatola dello strumento ───────────────────── */}
      <circle cx={g.cx} cy={g.cy} r={g.R + 9} fill="none" stroke={C.ringOuter} strokeWidth={1} strokeOpacity={0.5} />
      <circle cx={g.cx} cy={g.cy} r={g.R + 5} fill="none" stroke={C.ringInner} strokeWidth={1.6} strokeOpacity={0.32} />
      <circle cx={g.cx} cy={g.cy} r={g.R} fill="url(#f4glass)" />
      {/* tacche di indice ai quattro quarti: e' una ghiera lavorata, non un cerchio */}
      {[0, 0.25, 0.5, 0.75].map((f, i) => {
        const [a0, b0] = PT(g.R + 2, f * S), [a1, b1] = PT(g.R + 12, f * S);
        return <line key={i} x1={a0} y1={b0} x2={a1} y2={b1} stroke={C.index} strokeWidth={1.4} strokeOpacity={0.7} />;
      })}

      {/* ── graticola radiale, appena percepibile: da' profondita' al fondo ── */}
      {Array.from({ length: 12 }, (_, k) => {
        const t = (k / 12) * S;
        const [a0, b0] = PT(g.rc, t), [a1, b1] = PT(g.rOut + 8, t);
        return <line key={k} x1={a0.toFixed(1)} y1={b0.toFixed(1)} x2={a1.toFixed(1)} y2={b1.toFixed(1)}
          stroke={C.grid} strokeWidth={0.8} />;
      })}

      {/* ── corona dei minuti: tacche lavorate + numeri incisi ───────────── */}
      <circle cx={g.cx} cy={g.cy} r={g.R} fill="none" stroke={C.corona} strokeWidth={1} />
      {(() => {
        const out: React.ReactNode[] = [];
        const tickCount = Math.max(1, Math.min(60, Math.ceil(S / 60)));
        const step = S / tickCount;
        /* The end point is left out: it sits at the same position as the start. */
        for (let i = 0; i < tickCount; i++) {
          const s = Math.min(S, i * step);
                    const m = Math.round(s / 60);
          const big = i % Math.max(1, Math.ceil(tickCount / 12)) === 0;
          const [x0, y0] = PT(g.R - (big ? 13 : 6.5), s), [x1, y1] = PT(g.R, s);
          out.push(<line key={'t' + s} data-minute-tick="true"
            x1={x0.toFixed(1)} y1={y0.toFixed(1)} x2={x1.toFixed(1)} y2={y1.toFixed(1)}
            stroke={big ? C.tickStrong : C.tickSoft} strokeWidth={big ? 1.3 : 0.8} />);
          /* 0 e fine giro cadono sullo stesso punto: li' ci va la piastra di
             start, non due numeri sovrapposti */
          if (big && m > 0 && s < S - step * 0.6) {
            const [nx, ny] = PT(g.R - 26, s);
            const halfWidth = `${m}'`.length * 11 * .6 / 2 + 3;
            if (plateRects.some(rect => nx + halfWidth > rect.left && nx - halfWidth < rect.right
              && ny + 8 > rect.top && ny - 8 < rect.bottom)) continue;
            /* effetto inciso con UN SOLO nodo di testo: contorno scuro
               dipinto sotto il riempimento (paint-order). Con due <text>
               sovrapposti l'effetto era identico ma il collaudo li leggeva
               — giustamente — come due scritte una sull'altra. */
            out.push(
              <text key={'n' + s} data-tick-label="true"
                x={nx.toFixed(1)} y={ny.toFixed(1)} fill={C.minuteText}
                stroke={C.minuteHalo} strokeWidth={2} paintOrder="stroke" strokeLinejoin="round"
                fontSize={11} textAnchor="middle" dominantBaseline="middle"
                fontFamily={MONO}>{m}&#39;</text>);
          }
        }
        return out;
      })()}

      {/* ── le targhe di fase sono dipinte IN CODA all'SVG (v. prima della
          chiusura): a s150 la geometria si inverte (R−66 = 91 < nucleo
          rc = 112), le targhe cadono DENTRO il disco del nucleo e il disco,
          dipinto dopo, le copriva (misurato 1,02:1 — #2). Qui restano solo
          gli ARCHI, che sotto il nucleo ci possono stare. */}
      {p.phases.map(ph => {
        const dashed = !ph.measured;
        return (
          <g key={'arc-' + ph.k}>
            <path d={arc(g.R - 44, ph.t0, ph.t1)} fill="none"
              stroke={dashed ? C.phaseMuted : C.warn} strokeOpacity={dashed ? 0.5 : 0.6}
              strokeWidth={8} strokeDasharray={dashed ? '5 4' : undefined} />
            {[ph.t0, ph.t1].map((t, k) => {
              const [a0, b0] = PT(g.R - 53, t), [a1, b1] = PT(g.R - 35, t);
              return <line key={k} x1={a0.toFixed(1)} y1={b0.toFixed(1)} x2={a1.toFixed(1)} y2={b1.toFixed(1)}
                stroke={C.warn} strokeOpacity={0.55} strokeWidth={1} />;
            })}
          </g>
        );
      })}
      {/* ── le orbite ────────────────────────────────────────────────────── */}
      {p.desks.map((d, i) => {
        const r = rOf(i);
        const deskColor = modernDeskColor(d.color);
        const wins = p.windows.filter(x => x.a === d.id);
        const on = wins.some(x => cursor >= x.t0 && cursor <= x.t1);
        const dim = on ? 1 : 0.62;
        const ko = koIds.includes(d.id);
        const [bx, by] = PT(r, d.obs1 ?? 0);
        return (
          <g key={d.id}>
            {/* la scanalatura dell'orbita: due tratti, chiaro sopra e scuro
                sotto, cosi' l'anello si legge inciso e non disegnato */}
            <circle cx={g.cx} cy={g.cy} r={r + 0.6} fill="none" stroke={C.orbitBase} strokeWidth={1} />
            <circle cx={g.cx} cy={g.cy} r={r} fill="none" stroke={deskColor} strokeOpacity={0.12}
              strokeWidth={1} strokeDasharray="1.5 6" />
            {wins.map((x, k) => {
              /* finestra APERTA (desk dichiarato `running`): il tratto misurato e'
                 pieno fino all'ultima chiamata, da li' al presente e' TRATTEGGIATO —
                 il desk ragiona, ma dove sia non lo dice nessuno (F45, blocco A) */
              const tPieno = x.open && x.tCall != null ? x.tCall : x.t1;
              return (
                <g key={k}>
                  <path d={arc(r, x.t0, tPieno)} fill="none" stroke={deskColor}
                    strokeOpacity={0.2 * dim} strokeWidth={14} />
                  <path d={arc(r, x.t0, tPieno)} fill="none" stroke={deskColor}
                    strokeOpacity={0.95 * dim} strokeWidth={1.7} />
                  {x.open && x.t1 > tPieno + 0.5 && (
                    <path d={arc(r, tPieno, x.t1)} fill="none" stroke={deskColor}
                      strokeOpacity={0.7 * dim} strokeWidth={1.7} strokeDasharray="3 4" />
                  )}
                </g>
              );
            })}
            {/* una tacca radiale per CHIAMATA */}
            {p.calls.filter(c => c.a === d.id).map((c, k) => {
              const [x0, y0] = PT(r - 6.5, c.t), [x1, y1] = PT(r + 6.5, c.t);
              return <line key={k} x1={x0.toFixed(1)} y1={y0.toFixed(1)} x2={x1.toFixed(1)} y2={y1.toFixed(1)}
                stroke={deskColor} strokeOpacity={0.78 * dim} strokeWidth={0.9} />;
            })}
            {/* il corpo: dove ha chiuso, con la scia da cui e' arrivato */}
            <path d={arc(r, Math.max(wins[0]?.t0 ?? 0, (d.obs1 ?? 0) - S * 0.055), d.obs1 ?? 0)}
              fill="none" stroke={deskColor} strokeOpacity={0.95 * dim} strokeWidth={3.4}
              strokeLinecap="round" filter="url(#f4glow)" />
            {on && live && (
              <circle cx={bx.toFixed(1)} cy={by.toFixed(1)} r={11} fill="none" stroke={deskColor} strokeWidth={1}>
                <animate attributeName="r" values="9;22;9" dur="2.6s" repeatCount="indefinite" />
                <animate attributeName="stroke-opacity" values=".7;0;.7" dur="2.6s" repeatCount="indefinite" />
              </circle>
            )}
            <circle cx={bx.toFixed(1)} cy={by.toFixed(1)} r={9.5} fill={C.node}
              stroke={deskColor} strokeWidth={on ? 1.7 : 1.1} strokeOpacity={dim} />
            <Sigil id={d.id} color={deskColor} size={12} x={bx} y={by} />
            <Bracket cx={bx} cy={by} w={30} h={30} color={deskColor} op={on ? 0.9 : 0.42} />
            {ko && <circle cx={bx.toFixed(1)} cy={by.toFixed(1)} r={16} fill="none"
              stroke={C.bad} strokeWidth={1} strokeDasharray="2.5 3.5" />}
          </g>
        );
      })}

      {/* ── etichette di telemetria, gia' separate da layoutLabels ───────── */}
      {labels.map(L => (
        <g key={L.d.id}>
          <path d={`M${L.bx.toFixed(1)} ${L.by.toFixed(1)}
                    L${(L.right ? g.RX - 14 : g.LX + 14).toFixed(1)} ${L.ly.toFixed(1)}
                    L${(L.right ? g.RX : g.LX).toFixed(1)} ${L.ly.toFixed(1)}`}
            fill="none" stroke={L.color} strokeOpacity={0.48} strokeWidth={0.9} />
          <Plate x={L.right ? g.RX : g.LX} y={L.ly} lines={L.lines}
            align={L.right ? 'start' : 'end'} lh={labelLh} stroke={L.color}
            num={L.i + 1} palette={C} />
        </g>
      ))}

      {/* ── la lancetta: affusolata, con contrappeso oltre il mozzo ──────── */}
      <path d={arc(g.R - 6, Math.max(0, cursor - S * 0.075), cursor)} fill="none"
        stroke={C.hand} strokeOpacity={0.13} strokeWidth={22} />
      {(() => {
        const th = angleAt(cursor, S);
        const ux = Math.sin(th), uy = -Math.cos(th);      // versore lancetta
        const px = -uy, py = ux;                          // perpendicolare
        const tip = [g.cx + ux * (g.R - 8), g.cy + uy * (g.R - 8)];
        const tail = [g.cx - ux * (g.rc * 0.42), g.cy - uy * (g.rc * 0.42)];
        const b = 3.1;
        const poly = `${tip[0].toFixed(1)},${tip[1].toFixed(1)} ` +
          `${(g.cx + px * b).toFixed(1)},${(g.cy + py * b).toFixed(1)} ` +
          `${(tail[0] + px * b * 1.5).toFixed(1)},${(tail[1] + py * b * 1.5).toFixed(1)} ` +
          `${(tail[0] - px * b * 1.5).toFixed(1)},${(tail[1] - py * b * 1.5).toFixed(1)} ` +
          `${(g.cx - px * b).toFixed(1)},${(g.cy - py * b).toFixed(1)}`;
        return (
          <g>
            <polygon points={poly} fill={C.handShadow} fillOpacity={0.16} filter="url(#f4soft)"
              transform="translate(1.6,1.8)" />
            <polygon points={poly} fill={C.hand} fillOpacity={0.9} />
            <circle cx={tip[0].toFixed(1)} cy={tip[1].toFixed(1)} r={3.4} fill={C.handTip} filter="url(#f4glow)" />
          </g>
        );
      })()}
      <circle cx={g.cx} cy={g.cy} r={7} fill={C.centerFill} stroke={C.centerStroke} strokeWidth={1.2} />
      <circle cx={g.cx} cy={g.cy} r={2.4} fill={C.hand} fillOpacity={0.85} />

      {/* ── il nucleo: mozzo lavorato + verdetto ─────────────────────────── */}
      <circle cx={g.cx} cy={g.cy} r={g.rc + 26} fill="url(#f4core)" />
      <circle cx={g.cx} cy={g.cy} r={g.rc} fill="url(#f4hub)" stroke={C.hubStroke} strokeWidth={1} />
      <circle cx={g.cx} cy={g.cy} r={g.rc - 5} fill="none" stroke={C.hubInset1} strokeWidth={1} strokeOpacity={0.6} />
      <circle cx={g.cx} cy={g.cy} r={g.rc - 9} fill="none" stroke={C.hubInset2} strokeWidth={1} />

      {/* ── marca dello start a mezzogiorno: una tacca incisa, non una piastra
             (li' sopra passano i corridoi delle due piastre HUD alte) ────── */}
      <g>
        <line x1={g.cx} y1={g.cy - g.R - 4} x2={g.cx} y2={g.cy - g.R + 16}
          stroke={C.start} strokeOpacity={0.85} strokeWidth={1.6} />
        <text x={g.cx} y={g.cy - g.R + 27} fill={C.start} fillOpacity={0.95} fontSize={11.5}
          letterSpacing={1.2} textAnchor="middle" fontFamily={MONO}>{tr('dashboard.start')}</text>
      </g>

      {/* ── il nucleo: il verdetto della run, dove nient'altro puo' finire ── */}
      <g>
        <text x={g.cx} y={g.cy - 34} fill={C.costLabel} fontSize={11.5} letterSpacing={1.4}
          textAnchor="middle" fontFamily={MONO}>{tr('dashboard.run_cost')}</text>
        <text x={g.cx} y={g.cy - 6} fill={C.text} fontSize={26} fontWeight={300}
          textAnchor="middle" fontFamily={MONO}>{fmtEur(costoRun)}</text>
        {koCost != null && koCost > 0 && (
          <text x={g.cx} y={g.cy + 12} fill={C.warn} fontSize={11.5} textAnchor="middle" fontFamily={MONO}>
            {tr('dashboard.of_which')} {fmtEur(koCost)} {tr('dashboard.from_failed_desks')}
          </text>
        )}
        <line x1={g.cx - 58} y1={g.cy + 24} x2={g.cx + 58} y2={g.cy + 24} stroke={C.line} />
        <text x={g.cx} y={g.cy + 40} fill={C.muted} fontSize={11.5} textAnchor="middle" fontFamily={MONO}>
          {memoLabel}
        </text>
        {/* il totale VERO quando e' dichiarato (N8): a run viva `calls` sono le
            ultime 50 ricevute e scrivere quel numero come totale era il bug;
            a tappato SENZA totale (contratto monco) niente fallback zitto su
            calls.length: «totale n.d.» dichiarato (review 31/08) */}
        <text x={g.cx} y={g.cy + 54} fill={C.muted} fontSize={11} textAnchor="middle" fontFamily={MONO}>
          {fmtDurShort(p.runSec)} · {p.logTappato && p.nCallsTot == null
            ? tr('dashboard.calls_total_missing') : tr((p.nCallsTot ?? p.calls.length) === 1 ? 'dashboard.calls_count_one' : 'dashboard.calls_count', {a: p.nCallsTot ?? p.calls.length})}{p.logTappato ? tr('dashboard.calls_recent', {a: p.calls.length}) : ''}
        </text>
        {/* quante ne sono state fatte fino al cursore: cambia col mouse.
            La forma tappata «N del log fino a MM:SS» e' corta APPOSTA: col
            suffisso « nel log» la riga sbordava di ~10-17px per lato sotto
            l'anello a s150 (review 31/08, metrica svg-kit 0,6 em) */}
        <text x={g.cx} y={g.cy + 70} fontWeight={600} fill={C.soft} fontSize={11} textAnchor="middle" fontFamily={MONO}>
          {p.logTappato
            ? tr('dashboard.calls_until', {a: p.calls.filter(c => c.t <= cursor).length, b: fmtClock(cursor)})
            : tr('dashboard.calls_at_minute', {a: p.calls.filter(c => c.t <= cursor).length, b: fmtClock(cursor)})}
          {activeNow.length > 0 && tr('dashboard.working_count', {a: activeNow.length})}
        </text>
      </g>

      {/* ── LE TARGHE DI FASE, dipinte per ULTIME (#2, blocco B 31/08) ──────
          Il difetto aveva DUE meccanismi, entrambi misurati a s150: (1) la
          geometria si inverte (R−66 = 91 < nucleo rc = 112) e le targhe
          cadevano DENTRO il disco del nucleo, che essendo dipinto dopo le
          COPRIVA — R0 a 1,02:1 con nominale 9,43; (2) due targhe contigue
          possono coprirsi FRA LORO vicino a mezzogiorno. Cura: (1) targhe in
          coda all'SVG (niente sotto cui sparire — gli archi restano al loro
          posto nella pila); (2) le targhe che si sovrappongono in orizzontale
          formano un gruppo e `layoutLabels` le separa in verticale; chi non
          collide non si muove di un pixel. L'ordine del DOM resta quello
          delle fasi (il cancello legge [data-fase] in ordine). */}
      {phasePlates.map(({ ph, dashed, lx, lines, bw, phaseLh, renderY }) => (
          <g key={ph.k} data-fase={ph.k}>
            <Plate x={lx - bw / 2} y={renderY} lines={lines} align="start" lh={phaseLh}
              stroke={dashed ? C.ringOuter : C.warn}
              palette={C} />
          </g>
        ))}
    </svg>
  );
}
