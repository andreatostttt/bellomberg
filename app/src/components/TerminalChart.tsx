import { useEffect, useRef, useState } from 'react';
import type { OhlcBar } from '@/lib/api';
import { useT } from '@/i18n/provider';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { useInterfaceTheme } from '@/components/InterfaceThemeProvider';

/**
 * UI v3 T2 - TerminalChart: candele/area professionali su lightweight-charts (motore TradingView OSS).
 * Import DINAMICO: se il pacchetto non e' installato in app/, il componente degrada con istruzione
 * invece di rompere il dev server. Compatibile v4 (addCandlestickSeries) e v5 (addSeries(CandlestickSeries)).
 */
type Mode = 'candle' | 'area' | 'line' | 'baseline';

/** Optional route-owned viewport memory for presentation-boundary recovery. */
export interface TerminalChartViewState {
  seriesKey: string;
  visibleRange: { from: number; to: number } | null;
}

/* serie aggiuntiva sovrapposta al pannello prezzi (es. cost basis tratteggiato
   in F2): punti {t: epoch s UTC, v: valore}, stile linea, label in legenda */
export interface ChartOverlay {
  /* v: null = gap (whitespace lightweight-charts): la linea si interrompe —
     per overlay a tratti (es. run di carry del benchmark ufficiale in F2) */
  points: { t: number; v: number | null }[];
  color: string;
  dashed?: boolean;
  label?: string;
  /* false = niente badge valore sull'asse per questo overlay (default true, invariato) */
  lastValue?: boolean;
}

const MODERN_PALETTE = {
  grid: '#e8edf5', text: '#63718a', border: '#dbe3ef',
  up: '#008f63', down: '#c52943', line: '#1455ff', crosshair: 'rgba(20,85,255,0.32)',
  crosshairLabel: '#f7f9fc', areaTop: 'rgba(20,85,255,0.16)', areaBottom: 'rgba(20,85,255,0.01)',
  baselineUp: 'rgba(0,143,99,0.18)', baselineUpFade: 'rgba(0,143,99,0.02)',
  baselineDown: 'rgba(197,41,67,0.18)', baselineDownFade: 'rgba(197,41,67,0.02)',
  volumeUp: 'rgba(0,143,99,0.24)', volumeDown: 'rgba(197,41,67,0.24)',
  sma20: '#d97706', sma50: '#7357d5', vwap: 'rgba(20,85,255,0.56)', rsi: '#7357d5',
};
/* Dark Nuova: same roles as MODERN_PALETTE on the --bbt-chart-* surfaces. */
const MODERN_DARK_PALETTE: typeof MODERN_PALETTE = {
  grid: '#243149', text: '#9aa8bd', border: '#2f3d55',
  up: '#34c98f', down: '#f0647a', line: '#6b93ff', crosshair: 'rgba(107,147,255,0.45)',
  crosshairLabel: '#2a3a55', areaTop: 'rgba(107,147,255,0.24)', areaBottom: 'rgba(107,147,255,0.02)',
  baselineUp: 'rgba(52,201,143,0.22)', baselineUpFade: 'rgba(52,201,143,0.02)',
  baselineDown: 'rgba(240,100,122,0.22)', baselineDownFade: 'rgba(240,100,122,0.02)',
  volumeUp: 'rgba(52,201,143,0.30)', volumeDown: 'rgba(240,100,122,0.30)',
  sma20: '#f0a640', sma50: '#a48bff', vwap: 'rgba(107,147,255,0.6)', rsi: '#a48bff',
};
type Palette = typeof MODERN_PALETTE;

/* Live series handles, so a Light/Dark change recolours in place. */
interface SeriesHandles {
  mainKind: Mode; main: any; vol: any; sma20: any; sma50: any; vwap: any; rsi: any;
  overlays: any[];
}

function chartOptions(P: Palette) {
  return {
    layout: { textColor: P.text },
    grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
    rightPriceScale: { borderColor: P.border },
    timeScale: { borderColor: P.border },
    crosshair: {
      vertLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
      horzLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
    },
  };
}

function mainSeriesOptions(kind: Mode, P: Palette) {
  if (kind === 'candle') return {
    upColor: P.up, downColor: P.down, borderUpColor: P.up, borderDownColor: P.down,
    wickUpColor: P.up, wickDownColor: P.down,
  };
  if (kind === 'line') return { color: P.line };
  if (kind === 'baseline') return {
    topLineColor: P.up, topFillColor1: P.baselineUp, topFillColor2: P.baselineUpFade,
    bottomLineColor: P.down, bottomFillColor1: P.baselineDownFade, bottomFillColor2: P.baselineDown,
  };
  return { lineColor: P.line, topColor: P.areaTop, bottomColor: P.areaBottom };
}

const volumeData = (bars: OhlcBar[], P: Palette) => bars.map(b => ({
  time: b.t as any, value: b.v, color: b.c >= b.o ? P.volumeUp : P.volumeDown,
}));

function sma(bars: OhlcBar[], n: number) {
  const out: { time: number; value: number }[] = [];
  let acc = 0;
  for (let i = 0; i < bars.length; i++) {
    acc += bars[i].c;
    if (i >= n) acc -= bars[i - n].c;
    if (i >= n - 1) out.push({ time: bars[i].t, value: acc / n });
  }
  return out;
}

// VWAP rolling 30 barre (richiede volumi; tipico prezzo (H+L+C)/3)
function vwap(bars: OhlcBar[], n = 30) {
  const out: { time: number; value: number }[] = [];
  for (let i = 0; i < bars.length; i++) {
    let pv = 0, vv = 0;
    for (let k = Math.max(0, i - n + 1); k <= i; k++) {
      const tp = (bars[k].h + bars[k].l + bars[k].c) / 3;
      pv += tp * bars[k].v; vv += bars[k].v;
    }
    if (vv > 0) out.push({ time: bars[i].t, value: pv / vv });
  }
  return out;
}

// RSI 14 di Wilder
function rsi14(bars: OhlcBar[], n = 14) {
  const out: { time: number; value: number }[] = [];
  if (bars.length <= n) return out;
  let g = 0, l = 0;
  for (let i = 1; i <= n; i++) {
    const d = bars[i].c - bars[i - 1].c;
    if (d >= 0) g += d; else l -= d;
  }
  let ag = g / n, al = l / n;
  out.push({ time: bars[n].t, value: al === 0 ? 100 : 100 - 100 / (1 + ag / al) });
  for (let i = n + 1; i < bars.length; i++) {
    const d = bars[i].c - bars[i - 1].c;
    ag = (ag * (n - 1) + Math.max(0, d)) / n;
    al = (al * (n - 1) + Math.max(0, -d)) / n;
    out.push({ time: bars[i].t, value: al === 0 ? 100 : 100 - 100 / (1 + ag / al) });
  }
  return out;
}

const fmtPx = (v: number) =>
  v >= 1000 ? v.toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 0 })
  : v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: v >= 10 ? 2 : 4,
      maximumFractionDigits: v >= 10 ? 2 : 4, useGrouping: false });

export default function TerminalChart({ bars, mode = 'candle', height = 360, fill = false, log = false, showSma = true, showVwap = false, showRsi = false, overlays, valueLegend = false, recoveryState }: {
  bars: OhlcBar[]; mode?: Mode; height?: number;
  /** fill: il grafico riempie il contenitore (altezza REATTIVA via ResizeObserver, stile TradingView) */
  fill?: boolean; log?: boolean; showSma?: boolean; showVwap?: boolean; showRsi?: boolean;
  /** overlays: serie linea sovrapposte (memoizzare nel caller: e' una dep dell'effetto) */
  overlays?: ReadonlyArray<ChartOverlay>;
  /** valueLegend: legenda a solo valore (serie NAV/TWR con o=h=l=c, la OHLC sarebbe rumore) */
  valueLegend?: boolean;
  /** Opt-in: retain the viewport when only a presenter is remounted. */
  recoveryState?: { current: TerminalChartViewState | null };
}) {
  const tr = useT();
  const locale = localeDi(linguaCorrente());
  const liveChart = useRef<any>(null);
  const refreshLegend = useRef<(() => void) | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState(false);
  const dark = useInterfaceTheme().effective === 'dark';
  const P: Palette = dark ? MODERN_DARK_PALETTE : MODERN_PALETTE;
  // The chart effect reads colours from refs: a Light/Dark change is applied
  // in place below, never by recreating the chart (zoom/crosshair survive).
  const paletteRef = useRef(P);
  paletteRef.current = P;
  const series = useRef<SeriesHandles | null>(null);
  const overlaysRef = useRef(overlays);
  overlaysRef.current = overlays;
  // Overlays whose points and styles are unchanged keep the chart; only their
  // colours may follow the theme (applied through applyOptions).
  const overlayShape = useRef(overlays);
  {
    const prev = overlayShape.current;
    const sameShape = prev === overlays || (!!prev && !!overlays && prev.length === overlays.length
      && prev.every((o, i) => o.points === overlays[i].points && o.dashed === overlays[i].dashed
        && o.lastValue === overlays[i].lastValue && o.label === overlays[i].label));
    if (!sameShape) overlayShape.current = overlays;
  }
  const structuralOverlays = overlayShape.current;

  useEffect(() => {
    liveChart.current?.applyOptions({ localization: { locale } });
    refreshLegend.current?.();
  }, [locale]);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || !bars.length) return;
    let chart: any = null;
    let ro: ResizeObserver | null = null;
    let dead = false;
    let cleanupExtra: (() => void) | null = null;
    const seriesKey = bars.map(bar => bar.t).join(',');
    const restoreRange = recoveryState?.current?.seriesKey === seriesKey
      ? recoveryState.current.visibleRange : null;

    (async () => {
      const P = paletteRef.current;
      const overlays = overlaysRef.current;
      const handles: SeriesHandles = { mainKind: mode, main: null, vol: null, sma20: null, sma50: null, vwap: null, rsi: null, overlays: [] };
      let lw: any;
      try {
        lw = await import('lightweight-charts');
      } catch {
        if (!dead) setErr(true);
        return;
      }
      if (dead || !boxRef.current) return;
      setErr(false);

      const hNow = () => (fill ? Math.max(200, el.clientHeight || height) : height);
      chart = lw.createChart(el, {
        localization: { locale: localeDi(linguaCorrente()) },
        width: el.clientWidth, height: hNow(),
        layout: {
          background: { color: 'transparent' }, textColor: P.text,
          fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, sans-serif', fontSize: 13,
          attributionLogo: false,
        },
        grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
        rightPriceScale: { borderColor: P.border, mode: log ? 1 : 0 },
        timeScale: { borderColor: P.border, rightOffset: 3, minBarSpacing: 2 },
        crosshair: {
          mode: 0,
          vertLine: { color: P.crosshair, width: 1 as any, style: 3, labelBackgroundColor: P.crosshairLabel },
          horzLine: { color: P.crosshair, width: 1 as any, style: 3, labelBackgroundColor: P.crosshairLabel },
        },
      });
      liveChart.current = chart;

      // v5: addSeries(lw.CandlestickSeries, opts) - v4: addCandlestickSeries(opts)
      const v5 = typeof chart.addSeries === 'function' && lw.CandlestickSeries;
      const mk = (kind: string, opts: any) =>
        v5 ? chart.addSeries(lw[kind + 'Series'], opts) : chart['add' + kind + 'Series'](opts);

      let main: any;
      if (mode === 'candle') {
        main = mk('Candlestick', {
          upColor: P.up, downColor: P.down, borderUpColor: P.up, borderDownColor: P.down,
          wickUpColor: P.up, wickDownColor: P.down,
        });
        main.setData(bars.map(b => ({ time: b.t as any, open: b.o, high: b.h, low: b.l, close: b.c })));
      } else if (mode === 'line') {
        main = mk('Line', { color: P.line, lineWidth: 2 as any });
        main.setData(bars.map(b => ({ time: b.t as any, value: b.c })));
      } else if (mode === 'baseline') {
        // P/L attorno allo zero: verde sopra, rosso sotto (BaselineSeries nativa)
        main = mk('Baseline', {
          baseValue: { type: 'price', price: 0 },
          topLineColor: P.up, topFillColor1: P.baselineUp, topFillColor2: P.baselineUpFade,
          bottomLineColor: P.down, bottomFillColor1: P.baselineDownFade, bottomFillColor2: P.baselineDown,
          lineWidth: 2 as any,
        });
        main.setData(bars.map(b => ({ time: b.t as any, value: b.c })));
        try {
          main.createPriceLine({ price: 0, color: 'rgba(102,115,142,0.5)', lineWidth: 1 as any, lineStyle: 3, axisLabelVisible: false });
        } catch {}
      } else {
        main = mk('Area', {
          lineColor: P.line, topColor: P.areaTop, bottomColor: P.areaBottom,
          lineWidth: 2 as any,
        });
        main.setData(bars.map(b => ({ time: b.t as any, value: b.c })));
      }

      // volumi: istogramma su scala overlay in basso
      handles.main = main;
      const hasVol = bars.some(b => b.v > 0);
      if (hasVol) {
        const vol = mk('Histogram', { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
        try { chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } }); } catch {}
        vol.setData(volumeData(bars, P));
        handles.vol = vol;
      }

      // overlays del caller (es. cost basis tratteggiato): lineStyle 2 = dashed;
      // punti con v=null diventano whitespace (la linea si interrompe, non ponte)
      for (const ov of overlays || []) {
        if (!ov.points.length) { handles.overlays.push(null); continue; }
        handles.overlays.push(mk('Line', {
          color: ov.color, lineWidth: 1.5 as any, lineStyle: ov.dashed ? 2 : 0,
          priceLineVisible: false, lastValueVisible: ov.lastValue !== false,
        }));
        handles.overlays[handles.overlays.length - 1].setData(ov.points.map(p => (p.v == null ? { time: p.t as any } : { time: p.t as any, value: p.v })));
      }

      // medie mobili
      if (showSma && bars.length > 22) (handles.sma20 = mk('Line', { color: P.sma20, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })).setData(sma(bars, 20) as any);
      if (showSma && bars.length > 55) (handles.sma50 = mk('Line', { color: P.sma50, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })).setData(sma(bars, 50) as any);

      // VWAP rolling (solo se ci sono volumi veri)
      if (showVwap && hasVol && bars.length > 5) {
        (handles.vwap = mk('Line', { color: P.vwap, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })).setData(vwap(bars) as any);
      }

      // RSI 14: pannello separato su v5 (panes); su v4 il pannello non esiste -> non mostrato
      if (showRsi && bars.length > 20 && v5) {
        try {
          const r = chart.addSeries(lw.LineSeries, {
            color: P.rsi, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: true,
            priceFormat: { type: 'custom', formatter: (v: number) => v.toFixed(0), minMove: 1 },
          }, 1);
          r.setData(rsi14(bars) as any);
          handles.rsi = r;
          try {
            r.createPriceLine({ price: 70, color: 'rgba(255,61,96,0.4)', lineWidth: 1 as any, lineStyle: 3, axisLabelVisible: false });
            r.createPriceLine({ price: 30, color: 'rgba(33,224,160,0.4)', lineWidth: 1 as any, lineStyle: 3, axisLabelVisible: false });
          } catch {}
          try { chart.panes()[1].setHeight(Math.max(56, Math.round(hNow() * 0.18))); } catch {}
        } catch {}
      }

      // legenda OHLC viva (senza re-render React: scrive nel DOM)
      const setLegend = (b: OhlcBar | null) => {
        const lg = legendRef.current;
        if (!lg) return;
        const P = paletteRef.current;
        const x = b || bars[bars.length - 1];
        const prev = b ? bars[Math.max(0, bars.indexOf(b) - 1)] : bars[Math.max(0, bars.length - 2)];
        if (valueLegend) {
          // serie a valore (NAV/TWR/P&L/underwater): valore + delta, mai piu' di 2 decimali
          const fv = (v: number) => Math.abs(v) >= 1000
            ? v.toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 0 })
            : v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          const d = prev ? x.c - prev.c : 0;
          const cl = d >= 0 ? P.up : P.down;
          lg.innerHTML =
            '<span style="color:' + cl + '">' + fv(x.c) + '</span>' +
            (prev && prev !== x ? ' <span style="color:' + cl + '">' + (d >= 0 ? '+' : '−') + fv(Math.abs(d)) + '</span>' : '');
          return;
        }
        const chg = prev && prev.c ? ((x.c / prev.c) - 1) * 100 : 0;
        const cls = x.c >= x.o ? P.up : P.down;
        lg.innerHTML =
          '<span style="color:' + P.text + '">O</span> ' + fmtPx(x.o) +
          ' <span style="color:' + P.text + '">H</span> ' + fmtPx(x.h) +
          ' <span style="color:' + P.text + '">L</span> ' + fmtPx(x.l) +
          ' <span style="color:' + P.text + '">C</span> <span style="font-weight:600;color:' + cls + '">' + fmtPx(x.c) + '</span>' +
          ' <span style="font-weight:600;color:' + (chg >= 0 ? P.up : P.down) + '">' + (chg >= 0 ? '+' : '') + chg.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%</span>' +
          (x.v ? ' <span style="color:' + P.text + '">V</span> ' + Intl.NumberFormat(localeDi(linguaCorrente()), { notation: 'compact' }).format(x.v) : '');
      };
      setLegend(null);
      refreshLegend.current = () => setLegend(null);
      series.current = handles;
      const byTime = new Map(bars.map(b => [b.t, b]));
      chart.subscribeCrosshairMove((param: any) => {
        const t = param?.time;
        setLegend(typeof t === 'number' ? (byTime.get(t) || null) : null);
      });

      if (restoreRange) {
        try { chart.timeScale().setVisibleLogicalRange(restoreRange); }
        catch { chart.timeScale().fitContent(); }
      } else chart.timeScale().fitContent();
      // ── cintura anti-zoom/resize (ctrl+scroll compreso) ──────────────────
      // 1) guardia taglie-zero: durante il reflow dello zoom il RO puo' sparare
      //    0px e incastrare il canvas — mai applicare misure degeneri;
      // 2) window.resize: lo zoom del browser lo emette anche quando il RO tace;
      // 3) matchMedia dppx: cambio devicePixelRatio → ri-applica e ri-binda.
      const applySize = () => {
        if (dead || !chart || !boxRef.current) return;
        const w = boxRef.current.clientWidth;
        const h = fill ? Math.max(200, boxRef.current.clientHeight) : height;
        if (w < 40 || h < 40) return;
        try {
          // Keep the user's visible dates fixed as the panel changes width;
          // otherwise lightweight-charts can expose extra history on the left.
          const range = chart.timeScale().getVisibleLogicalRange();
          chart.applyOptions(fill ? { width: w, height: h } : { width: w });
          if (range) chart.timeScale().setVisibleLogicalRange(range);
        } catch {}
      };
      // schedulato via rAF: mai layout sincrono dentro il RO (niente "loop completed"),
      // e una sola applicazione per frame anche sotto raffiche di resize/zoom
      let rafId = 0;
      const applySizeRaf = () => { cancelAnimationFrame(rafId); rafId = requestAnimationFrame(applySize); };
      ro = new ResizeObserver(applySizeRaf);
      ro.observe(el);
      window.addEventListener('resize', applySizeRaf);
      let dprMq: MediaQueryList | null = null;
      const onDpr = () => { applySizeRaf(); bindDpr(); };
      const bindDpr = () => {
        try { dprMq?.removeEventListener('change', onDpr); } catch {}
        try {
          dprMq = window.matchMedia('(resolution: ' + window.devicePixelRatio + 'dppx)');
          dprMq.addEventListener('change', onDpr);
        } catch { dprMq = null; }
      };
      bindDpr();
      cleanupExtra = () => {
        cancelAnimationFrame(rafId);
        window.removeEventListener('resize', applySizeRaf);
        try { dprMq?.removeEventListener('change', onDpr); } catch {}
      };
    })();

    // cleanup blindato: ogni passo isolato; la remove() e' DIFFERITA di un frame
    // perche' lightweight-charts puo' avere un draw interno gia' schedulato in raf —
    // rimuovere subito lo fa atterrare su un oggetto disposed (bug noto della lib)
    return () => {
      dead = true;
      if (liveChart.current === chart) {
        liveChart.current = null;
        refreshLegend.current = null;
        series.current = null;
      }
      try { ro?.disconnect(); } catch {}
      try { cleanupExtra?.(); } catch {}
      const dying = chart;
      chart = null;
      if (dying && recoveryState) {
        let visibleRange: { from: number; to: number } | null = null;
        try { visibleRange = dying.timeScale().getVisibleLogicalRange(); } catch {}
        recoveryState.current = { seriesKey, visibleRange };
      }
      if (dying) requestAnimationFrame(() => { try { dying.remove(); } catch {} });
    };
  }, [bars, mode, height, fill, log, showSma, showVwap, showRsi, structuralOverlays, valueLegend, recoveryState]);

  // Light/Dark (Nuova): recolour the live chart and series in place. No
  // remount, so visible range, crosshair, RSI pane and legend state survive.
  useEffect(() => {
    const chart = liveChart.current;
    const h = series.current;
    if (!chart || !h) return;
    try {
      const range = chart.timeScale().getVisibleLogicalRange();
      chart.applyOptions(chartOptions(P));
      h.main?.applyOptions(mainSeriesOptions(h.mainKind, P));
      h.vol?.setData(volumeData(bars, P));
      h.sma20?.applyOptions({ color: P.sma20 });
      h.sma50?.applyOptions({ color: P.sma50 });
      h.vwap?.applyOptions({ color: P.vwap });
      h.rsi?.applyOptions({ color: P.rsi });
      (overlays || []).forEach((ov, i) => { h.overlays[i]?.applyOptions({ color: ov.color }); });
      if (range) chart.timeScale().setVisibleLogicalRange(range);
    } catch {}
    refreshLegend.current?.();
    // bars/overlays are read only for colours; their changes rebuild above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [P, overlays]);

  const hStyle = fill ? { height: '100%', minHeight: 200 } : { height };
  if (!bars.length) {
    return <div className="flex items-center justify-center text-faint text-2xs font-mono" style={hStyle}>{tr('ui.no_data')}</div>;
  }
  if (err) {
    return <div className="flex items-center justify-center text-amber text-2xs font-mono px-6 text-center" style={hStyle}>{tr('ui.chart_engine_missing')}</div>;
  }
  return (
    <div className="relative" style={fill ? { height: '100%', display: 'flex', flexDirection: 'column', minHeight: 200 } : undefined}>
      <div ref={legendRef}
           className={'absolute top-1.5 left-2 z-10 font-mono text-2xs tabular-nums pointer-events-none' + ' terminal-legend-modern'}
           style={{ color: dark ? '#c7d1df' : '#334155', textShadow: dark ? '0 1px 2px rgba(0,0,0,.7)' : '0 1px 2px rgba(255,255,255,.8)' }} />
      <div className={'absolute top-1.5 right-14 z-10 font-mono text-3xs pointer-events-none flex gap-3' + ' terminal-indicators-modern'}
           style={{ textShadow: dark ? '0 1px 2px rgba(0,0,0,.7)' : '0 1px 2px rgba(255,255,255,.8)' }}>
        {showSma && <span style={{ color: P.sma20 }}>SMA20</span>}
        {showSma && <span style={{ color: P.sma50 }}>SMA50</span>}
        {showVwap && <span style={{ color: P.vwap }}>VWAP</span>}
        {showRsi && <span style={{ color: P.rsi }}>RSI14</span>}
        {(overlays || []).map((o, i) => o.label ? <span key={'ov' + i} style={{ color: o.color }}>{o.label}</span> : null)}
      </div>
      <div ref={boxRef} style={fill ? { flex: 1, minHeight: 200 } : { height }} />
    </div>
  );
}
