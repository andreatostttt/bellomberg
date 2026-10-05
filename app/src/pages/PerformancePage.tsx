import { useT } from '@/i18n/provider';
import { localizePayload } from '@/lib/api-presentation';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bellomberg, NavHistory, DrawdownsResult, LiquidityResult,
  ConcentrationResult, VarContributionResult, TwrPayload,
  AdvancedMetrics, PortfolioSnapshot, AttributionPayload, BenchmarkPayload,
  PortfolioRisk, TearsheetPayload, MonteCarloResult,
} from '@/lib/api';
import type { AttributionPeriod } from '@/lib/api';
import { computeDailyPnl, liveAsOf } from '@/lib/dailypl';
import { portfolioValues } from '@/lib/portfolio-values';
import ModernPage from '@/components/ModernPage';
import { Segmenti } from '@/components/nuova/Card';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import { RichiesteUltime, periodoAttribuzione, pnlGiornalieri } from './performance/calcoli';
import type { Periodo } from './performance/calcoli';
import { euro, num, pct } from './performance/formato';
import MetodoFonti from './performance/MetodoFonti';
import type { SezioneMetodo } from './performance/MetodoFonti';
import { parole } from './performance/parole';
import type { IdScenario } from './performance/parole';
import VistaScheda from './performance/VistaScheda';
import type { Contabilita, Stato } from './performance/VistaScheda';
import VistaRischio, { SCENARI } from './performance/VistaRischio';
import './performance-nuova.css';

/* ============================================================
   Performance in stile Nuova (02/10/2026). Questo componente resta il
   controller della rotta: tutte le letture, i timer e lo stato vivono qui,
   le due viste (Scheda, Rischio) ricevono solo dati. Lo stesso plumbing di
   prima: TWR, NAV, benchmark ufficiale con fallback dichiarato, attribuzione,
   metriche avanzate; in piu' rischio, tearsheet e scenari Monte Carlo.
   ============================================================ */

type BenchReason = { kind: 'restart' } | { kind: 'endpoint'; detail: string };
type BenchFailure = { kind: 'source'; detail: string } | { kind: 'empty' }
  | { kind: 'fallback'; why: BenchReason; detail: string | null; stage: 'series' | 'alignment' | 'request' };
type SourceProblem = { error?: string; clientMissingReason?: boolean };
// Only the client-authored absence marker is localized; source details stay verbatim.
function requestFailure(error: { message?: string } | null | undefined): SourceProblem {
  return { error: error?.message || 'request failed', clientMissingReason: !error?.message };
}
const sourceProblemText = (value: SourceProblem) => value.clientMissingReason
  ? tr('dashboard.client_request_failed') : value.error;

// data 'YYYY-MM-DD' -> epoch SECONDI UTC
const dayT = (d: string) => Math.floor(Date.parse(d + 'T00:00:00Z') / 1000);
// epoch qualsiasi -> mezzanotte UTC del giorno (allineamento del fallback CALC)
const dayKey = (t: number) => Math.floor(t / 86400) * 86400;
const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Payload grezzo -> stato della card: in attesa, errore dichiarato (testo della fonte), dati. */
function stato<T extends { error?: string }>(v: T | SourceProblem | null | undefined): Stato<T> {
  if (v == null) return { stato: 'attesa' };
  if ((v as SourceProblem).error) return { stato: 'errore', testo: sourceProblemText(v as SourceProblem) || '' };
  return { stato: 'ok', dati: v as T };
}

function useAttribution(enabled: boolean, period: AttributionPeriod) {
  const tr = useT();
  const [dataRaw, setData] = useState<AttributionPayload | null>(null);
  const data = useMemo(() => localizePayload(dataRaw), [dataRaw, tr]);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<{ kind: 'missing' } | { kind: 'source'; detail: string } | null>(null);
  const err = failure?.kind === 'missing' ? tr('dashboard.attribution_endpoint_missing')
    : failure?.kind === 'source' ? failure.detail : null;
  useEffect(() => {
    if (!enabled) { setData(null); setFailure(null); setLoading(false); return; }
    let m = true;
    setLoading(true); setFailure(null); setData(null);
    Bellomberg.attribution(period)
      .then(d => { if (m) setData(d); })
      .catch(e => {
        if (!m) return;
        const st = e?.response?.status;
        setFailure(st === 404 ? { kind: 'missing' }
          : { kind: 'source', detail: String(e?.response?.data?.detail || e?.message || e) });
      })
      .finally(() => { if (m) setLoading(false); });
    return () => { m = false; };
  }, [period, enabled]);
  return { data, loading, err };
}

export default function PerformancePage() {
  const tr = useT();
  const w = parole();
  const [navHistRaw, setNavHist] = useState<NavHistory | null>(null);
  // slot conservati: i test SSR seminano useState per indice (drawdowns non si legge piu';
  // liquidity si', di nuovo: tabella Liquidabilita' ripristinata nella vista Rischio)
  const [drawdownsRaw] = useState<DrawdownsResult | null>(null);
  const [liquidityRaw, setLiquidity] = useState<LiquidityResult | SourceProblem | null>(null);
  const [concentrationRaw, setConcentration] = useState<ConcentrationResult | null>(null);
  const [varContribRaw, setVarContrib] = useState<VarContributionResult | null>(null);
  const [twrRaw, setTwr] = useState<TwrPayload | null>(null);
  const [advRaw, setAdv] = useState<AdvancedMetrics | null>(null);
  void drawdownsRaw;
  const liquidity = useMemo(() => localizePayload(liquidityRaw), [liquidityRaw, tr]);
  const navHist = useMemo(() => localizePayload(navHistRaw), [navHistRaw, tr]);
  const concentration = useMemo(() => localizePayload(concentrationRaw), [concentrationRaw, tr]);
  const varContrib = useMemo(() => localizePayload(varContribRaw), [varContribRaw, tr]);
  const twr = useMemo(() => localizePayload(twrRaw), [twrRaw, tr]);
  const adv = useMemo(() => localizePayload(advRaw), [advRaw, tr]);
  const [loading, setLoading] = useState(false);
  const [loadFailure, setLoadFailure] = useState<{ detail: string | null } | null>(null);
  const error = loadFailure ? loadFailure.detail || tr('dashboard.client_load_failed') : null;
  const [range, setRange] = useState<Periodo>('3M');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [view, setView] = useState<'tearsheet' | 'risk'>('tearsheet');
  const attribution = useAttribution(view === 'tearsheet', periodoAttribuzione(range));

  const [rischioRaw, setRischio] = useState<PortfolioRisk | SourceProblem | null>(null);
  const [tearRaw, setTear] = useState<TearsheetPayload | SourceProblem | null>(null);

  const loadAll = async (force = false) => {
    setLoading(true); setLoadFailure(null);
    try {
      const [nh, cc, vc, tw, am, rk, ts, lq] = await Promise.all([
        Bellomberg.navHistory(force).catch(e => requestFailure(e) as NavHistory),
        Bellomberg.concentration().catch(e => requestFailure(e) as ConcentrationResult),
        Bellomberg.varContribution().catch(e => requestFailure(e) as VarContributionResult),
        Bellomberg.twr(force).catch(e => requestFailure(e) as TwrPayload),
        Bellomberg.metricsAdvanced().catch(e => requestFailure(e) as AdvancedMetrics),
        Bellomberg.portfolioRisk(force).catch(e => requestFailure(e)),
        Bellomberg.tearsheet(force).catch(e => requestFailure(e)),
        Bellomberg.liquidity().catch(e => requestFailure(e)),
      ]);
      setNavHist(nh as NavHistory);
      setConcentration(cc as ConcentrationResult);
      setVarContrib(vc as VarContributionResult);
      setTwr(tw as TwrPayload);
      setAdv(am as AdvancedMetrics);
      setRischio(rk);
      setTear(ts);
      setLiquidity(lq);
      setLastUpdated(new Date());
    } catch (e: any) {
      setLoadFailure({ detail: e?.message || null });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(false); }, []);
  // Auto-refresh every 5 minutes (force=false uses backend cache, refreshing every 10min anyway)
  useEffect(() => {
    const i = setInterval(() => loadAll(false), 5 * 60 * 1000);
    return () => clearInterval(i);
  }, []);

  // P&L in vetrina: il runner persistente in Layout pubblica il nuovo snapshot;
  // resta una rilettura leggera per i dati della pagina e per compatibilità.
  // Lettura fallita: lo snapshot VECCHIO non resta a schermo come attuale (08b S3, revisione G9b).
  // Il guasto vive nello STESSO slot (i test SSR seminano gli useState per indice).
  const [snapRaw, setSnap] = useState<PortfolioSnapshot | { guasto: string } | null>(null);
  const snapLetto = snapRaw && !('guasto' in snapRaw) ? snapRaw : null;
  const snapGuasto = snapRaw && 'guasto' in snapRaw ? snapRaw.guasto : null;
  const snap = useMemo(() => localizePayload(snapLetto), [snapLetto, tr]);
  useEffect(() => {
    let m = true;
    const read = () => Bellomberg.portfolio().then(p => { if (m) setSnap(p); })
      .catch(e => { if (m) setSnap({ guasto: e?.message || tr('dashboard.client_request_failed') }); });
    read();
    const a = setInterval(() => { if (!document.hidden) read(); }, 30 * 1000);
    return () => { m = false; clearInterval(a); };
  }, []);
  useEffect(() => {
    const onSnapshot = (event: Event) => {
      const snapshot = (event as CustomEvent<PortfolioSnapshot>).detail;
      if (snapshot) setSnap(snapshot);
    };
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
    window.addEventListener('bb:portfolio-snapshot', onSnapshot);
    return () => window.removeEventListener('bb:portfolio-snapshot', onSnapshot);
  }, []);
  // formula condivisa F1/F2 (lib/dailypl): n.d. dichiarato, % solo a copertura completa
  const daily = snap && Array.isArray(snap.positions) ? computeDailyPnl(snap.positions, liveAsOf(snap)) : null;
  const dayTot = daily?.dayTot ?? null;

  // BENCHMARK UFFICIALE BACKEND (voce (38)): SPY in EUR total-return, gia' allineato al
  // calendario TWR (carry contato) e rebased 100. 404 = backend pre-riavvio: torna lo
  // SPY×EURUSD client-side ETICHETTATO come provvisorio (mai zitto, regola 14/07).
  const [benchRaw, setBench] = useState<BenchmarkPayload | null>(null);
  const bench = useMemo(() => localizePayload(benchRaw), [benchRaw, tr]);
  const [benchFb, setBenchFb] = useState<{ t: number; v: number }[] | null>(null);
  const [benchFailure, setBenchFailure] = useState<BenchFailure | null>(null);
  const benchErr = benchFailure?.kind === 'source' ? benchFailure.detail
    : benchFailure?.kind === 'empty' ? tr('dashboard.benchmark_empty')
    : benchFailure?.kind === 'fallback' ? tr('dashboard.benchmark_fallback_ko', {
      reason: benchFailure.why.kind === 'restart' ? tr('dashboard.benchmark_restart')
        : tr('dashboard.benchmark_endpoint_ko', { detail: benchFailure.why.detail }),
      detail: benchFailure.detail ?? tr(benchFailure.stage === 'alignment' ? 'dashboard.benchmark_alignment_empty' : 'dashboard.benchmark_series_empty'),
    }) : null;
  useEffect(() => {
    let m = true;
    const loadFallback = (why: BenchReason) => {
      Promise.all([Bellomberg.ohlc('SPY', '1y', '1d'), Bellomberg.ohlc('EURUSD=X', '1y', '1d')])
        .then(([spy, fx]) => {
          if (!m) return;
          if (spy.error || !spy.bars?.length || fx.error || !fx.bars?.length) {
            setBenchFailure({ kind: 'fallback', why, detail: spy.error || fx.error || null, stage: 'series' }); return;
          }
          const fxByDay = new Map<number, number>();
          for (const b of fx.bars) fxByDay.set(dayKey(b.t), b.c);
          const out: { t: number; v: number }[] = [];
          let lastFx: number | null = null;
          for (const b of spy.bars) {
            const k = dayKey(b.t);
            const f = fxByDay.get(k);
            if (f != null && f > 0) lastFx = f;
            if (lastFx != null) out.push({ t: k, v: b.c / lastFx });
          }
          if (out.length < 2) { setBenchFailure({ kind: 'fallback', why, detail: null, stage: 'alignment' }); return; }
          setBenchFb(out); setBenchFailure(null);
        })
        .catch(e => { if (m) setBenchFailure({ kind: 'fallback', why, detail: String(e?.message || e), stage: 'request' }); });
    };
    Bellomberg.benchmark('SPY')
      .then(r => {
        if (!m) return;
        if (r.error || !r.dates?.length || !r.index?.length) {
          setBenchFailure(r.error ? { kind: 'source', detail: r.error } : { kind: 'empty' }); return;
        }
        setBench(r); setBenchFailure(null);
      })
      .catch(e => {
        if (!m) return;
        const st = e?.response?.status;
        loadFallback(st === 404 ? { kind: 'restart' } : { kind: 'endpoint', detail: String(st || e?.message || e) });
      });
    return () => { m = false; };
  }, []);
  const benchIsOfficial = !!bench;

  // Ramo UFFICIALE: serie gia' allineata e rebased dal backend. Ramo FALLBACK: allineamento
  // client-side al calendario TWR (carry-forward) + rebase sulla prima data.
  const benchOnTwr = useMemo(() => {
    if (bench?.dates && bench.index && bench.dates.length >= 2) {
      const out: { d: string; r: number }[] = [];
      for (let i = 0; i < bench.dates.length; i++) {
        const v = Number(bench.index[i]);
        if (isFinite(dayT(bench.dates[i])) && isFinite(v)) out.push({ d: bench.dates[i], r: v });
      }
      return out.length >= 2 ? out : null;
    }
    if (!benchFb || !twr || twr.error || !twr.dates || twr.dates.length < 2) return null;
    const out: { d: string; v: number }[] = [];
    let j = 0; let last: number | null = null;
    for (const d of twr.dates) {
      const t = dayT(d);
      if (!isFinite(t)) continue;
      while (j < benchFb.length && benchFb[j].t <= t) { last = benchFb[j].v; j++; }
      if (last != null) out.push({ d, v: last });
    }
    if (out.length < 2 || !(out[0].v > 0)) return null;
    return out.map(p => ({ d: p.d, r: p.v / out[0].v * 100 }));
  }, [bench, benchFb, twr]);

  // ── Rischio: scenari Monte Carlo, caricati solo con la vista Rischio aperta ──
  const [scenariRaw, setScenari] = useState<Partial<Record<IdScenario, MonteCarloResult | SourceProblem>>>({});
  const [metodoAperto, setMetodoAperto] = useState(false);
  // Una sola richiesta in volo per scenario (anche cambiando vista) e conta solo l'ultima:
  // un Monte Carlo puo' durare fino a 120 s e il backend non fonde le richieste identiche.
  const scenariInVolo = useRef(new RichiesteUltime<IdScenario>());
  const caricaScenario = (id: IdScenario, force = false) => {
    const n = scenariInVolo.current.apri(id, force);
    if (n == null) return;
    setScenari(s => ({ ...s, [id]: undefined }));
    Bellomberg.portfolioMonteCarlo({ horizon_days: 21, n_sims: 5000, method: 'fhs', stress: id, force, sample_paths_n: 1 })
      .then(r => { if (scenariInVolo.current.chiudi(id, n)) setScenari(s => ({ ...s, [id]: r })); })
      .catch(e => { if (scenariInVolo.current.chiudi(id, n)) setScenari(s => ({ ...s, [id]: requestFailure(e) })); });
  };
  useEffect(() => {
    if (view === 'risk') SCENARI.forEach(id => { if (!scenariRaw[id]) caricaScenario(id); });
  }, [view]);

  // ── dati per le viste ──
  const twrStato = stato<TwrPayload>(twr);
  const spyStato: Stato<{ date: string[]; indice: number[] }> = benchOnTwr
    ? { stato: 'ok', dati: { date: benchOnTwr.map(p => p.d), indice: benchOnTwr.map(p => p.r) } }
    : benchErr ? { stato: 'errore', testo: benchErr } : { stato: 'attesa' };
  const contabilita: Stato<Contabilita> = (() => {
    const s = stato<NavHistory>(navHist);
    if (s.stato !== 'ok') return s;
    const n = s.dati, ultimo = (a?: number[]) => (a && a.length ? a[a.length - 1] : null);
    if (!n.nav_eur?.length) return { stato: 'ok', dati: { valoreMercato: null, costo: null, nonRealizzato: null, realizzato: null, dividendi: null,
      cassa: finito(n.cash_eur) ? n.cash_eur : null, nota: tr('dashboard.nav_no_series') } };
    return { stato: 'ok', dati: {
      valoreMercato: ultimo(n.nav_eur), costo: ultimo(n.cost_basis_eur), nonRealizzato: ultimo(n.pnl_eur),
      realizzato: ultimo(n.realized_sales_eur), dividendi: ultimo(n.dividend_income_eur), cassa: finito(n.cash_eur) ? n.cash_eur : null,
    } };
  })();
  const attribuzione: Stato<AttributionPayload> = attribution.err ? { stato: 'errore', testo: attribution.err }
    : attribution.loading || !attribution.data ? { stato: 'attesa' } : { stato: 'ok', dati: attribution.data };
  const nomi = useMemo(() => Object.fromEntries((snap?.positions || []).map(p => [p.ticker, p.nome || p.ticker])), [snap]);
  const valori = snap ? portfolioValues(snap) : null;
  const pnlStato = (() => {
    const s = stato<TwrPayload>(twr);
    if (s.stato !== 'ok') return s as Stato<never>;
    // flussi mancanti: niente «|| []» che li spaccerebbe per zero (regola 14/07)
    const p = pnlGiornalieri(s.dati.dates || [], s.dati.values_eur || [], s.dati.flows_eur, s.dati.twr_index || []);
    if (p.disallineata) return { stato: 'errore' as const, testo: w.distFlowsMisaligned };
    return { stato: 'ok' as const, dati: p };
  })();
  const tearStato = stato<TearsheetPayload>(tearRaw);
  const drawdownStato: Stato<NonNullable<TearsheetPayload['drawdowns']>> = tearStato.stato !== 'ok' ? tearStato
    : tearStato.dati.drawdowns ? { stato: 'ok', dati: tearStato.dati.drawdowns } : { stato: 'errore', testo: tr('dashboard.no_drawdowns') };
  const scenari = Object.fromEntries(SCENARI.map(id => [id, stato<MonteCarloResult>(scenariRaw[id])])) as Record<IdScenario, Stato<MonteCarloResult>>;

  // avvisi: cio' che prima erano banner sempre a vista, ora in Metodo e fonti
  const avvisi: string[] = [];
  for (const [src, data] of [
    ['/portfolio/tearsheet', tearRaw], ['/portfolio/analytics/concentration', concentrationRaw],
    ['/portfolio/analytics/var_contribution', varContribRaw], ['/portfolio/metrics/advanced', advRaw], ['/portfolio/risk', rischioRaw],
    ['/portfolio/analytics/liquidity', liquidityRaw],
  ] as Array<[string, SourceProblem | null]>) {
    if (data?.error) avvisi.push(w.dataUnavailable(src, (data.clientMissingReason ? '' : tr('dashboard.service_detail') + ': ') + sourceProblemText(data)));
  }
  if (adv?.risk_free_note) avvisi.push(String(adv.risk_free_note));
  if (twr?.reconciliation?.breach) avvisi.push(twr.reconciliation.note);
  if (valori?.note) avvisi.push(valori.note);

  const sezioniMetodo: SezioneMetodo[] = [];
  if (avvisi.length) sezioniMetodo.push({ titolo: w.mWarn, avviso: true, testo: avvisi });
  if (twr && !twr.error) {
    sezioniMetodo.push({ titolo: w.mTwr, testo: twr.methodology ? [twr.methodology] : [], voci: [
      [w.mBase, twr.dates?.[0] ?? '—'],
      [w.mOfficialSince, twr.regime_summary?.official_since ?? '—'],
      [w.mGaps, String(twr.copertura?.giorni_senza_snapshot ?? '—')],
    ] });
    if (twr.copertura) sezioniMetodo.push({ titolo: w.mCoverage, testo: twr.copertura.nota ? [twr.copertura.nota] : [], voci: [
      [w.mFirstTrade, twr.copertura.primo_trade ?? '—'], [w.mFirstSnap, twr.copertura.primo_snapshot ?? '—'],
      [w.mTradesBefore, String(twr.copertura.n_trade_prima_del_primo_snapshot ?? '—')],
    ] });
    if (twr.reconciliation) sezioniMetodo.push({ titolo: w.mRecon, testo: twr.reconciliation.note ? [twr.reconciliation.note] : [], voci: [
      [w.mNavLive, euro(twr.reconciliation.nav_live_eur)],
      [w.mLastSnap(twr.reconciliation.last_snapshot_date), euro(twr.reconciliation.last_snapshot_nav_eur)],
      [w.mDelta, pct(twr.reconciliation.delta_pct)],
    ] });
  }
  sezioniMetodo.push({ titolo: w.mBench, testo: benchErr ? [benchErr] : [], voci: bench ? [
    [w.mSource, String(bench.src || 'benchmark_series') + (benchIsOfficial ? '' : ' · CALC')],
    [w.mCoveragePct, bench.coverage_pct != null ? pct(bench.coverage_pct, 1, false) : '—'],
    [w.mCarry, String(bench.carried_days ?? '—')],
  ] : benchOnTwr ? [[w.mSource, 'SPY × EURUSD · CALC']] : [] });
  const att = attribution.data;
  if (att && !att.error) sezioniMetodo.push({ titolo: w.mAttr, testo: [...(att.basis ? [att.basis] : []), ...(att.notes || [])], voci: [
    ...(att.excluded || []).map(x => [w.mExcluded(x.ticker), w.mExcludedDays(x.days_excluded, x.days_total,
      (x.reason_details?.map(r => r.label) ?? Object.keys(x.reasons)).join(', '))] as [string, string]),
    ...(att.reconciliation?.delta_pp != null ? [[w.mVsTwr, `${num(att.reconciliation.delta_pp, 2, true)} pt`] as [string, string]] : []),
  ] });
  if (rischioRaw && !(rischioRaw as SourceProblem).error) sezioniMetodo.push({ titolo: w.mRisk,
    testo: varContrib && !varContrib.error && varContrib.methodology ? [varContrib.methodology] : [],
    voci: [[w.mLookback, w.mLookbackDays((rischioRaw as PortfolioRisk).lookback_days)]] });
  const scenMeta = SCENARI.map(id => scenariRaw[id]).filter((m): m is MonteCarloResult => !!m && !(m as SourceProblem).error);
  if (scenMeta.length) sezioniMetodo.push({ titolo: w.mScen, testo: [...new Set(scenMeta.flatMap(m => [m.method_description, m.stress_meta?.basis]).filter((t): t is string => !!t))] });
  if (twr?.notes?.length) sezioniMetodo.push({ titolo: w.mNotes, testo: twr.notes });

  const ora = lastUpdated ? lastUpdated.toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' }) : null;
  const aggiorna = () => { loadAll(true); if (view === 'risk') SCENARI.forEach(id => caricaScenario(id, true)); };

  return (
    <ModernPage page="performance" render={() => (
      <div className="bbn-perf performance-page">
        <header className="perf-top">
          <h1>{w.title}</h1>
          <span className="perf-today" title={w.dayPnlHint}>
            {daily?.multiDay && daily.windowLabel ? w.dayPnlWindow(daily.windowLabel) : w.dayPnl}
            <b className="num">{euro(dayTot, 2, true)}</b>
            {daily?.dayTotPct != null && !daily.multiDay && <PastigliaVariazione valore={daily.dayTotPct}>{pct(daily.dayTotPct)}</PastigliaVariazione>}
            {daily?.dayPartial && <span className="is-partial">{w.dayPnlPartial}</span>}
            {snapGuasto && <span className="is-partial" role="status">{w.dayPnlReadFailed(snapGuasto)}</span>}
          </span>
          <span className="bbn-grow" />
          <span data-qa="perf-view">
            <Segmenti<'tearsheet' | 'risk'> etichetta={w.views} valore={view} onChange={setView} className="is-large"
              opzioni={[{ id: 'tearsheet', testo: w.viewSheet }, { id: 'risk', testo: w.viewRisk }]} />
          </span>
          <button type="button" className="bbn-btn" data-qa="perf-method" onClick={() => setMetodoAperto(true)}>
            {avvisi.length > 0 && <span className="perf-warn-dot" title={w.methodWarn} />}{w.method}
          </button>
          <button type="button" className="bbn-btn" data-qa="perf-refresh" onClick={aggiorna} disabled={loading}
            title={ora ? w.updatedAt(ora) : undefined}>
            ↻ {loading ? w.refreshing : ora ?? w.refresh}
          </button>
        </header>
        {error && <p className="perf-state is-error" role="status"><b>{w.unavailable}</b> · {error}</p>}
        {loading && !navHist && <p className="perf-state" role="status">{tr('dashboard.nav_reconstruction')}</p>}
        {navHist?.error && (
          <p className="perf-state is-error" role="status">
            <b>{tr('dashboard.nav_history_missing')}</b> {sourceProblemText(navHist)}<br />
            <span className="text-muted">
              {tr('dashboard.nav_record_trades', { page: tr('nav.trades') })} <code>{tr('dashboard.import_trades_command')}</code>{tr('dashboard.import_trades_steps', { page: tr('nav.trades') })}
            </span>
          </p>
        )}
        {view === 'tearsheet'
          ? <VistaScheda periodo={range} onPeriodo={setRange} twr={twrStato} spy={spyStato} avanzate={stato<AdvancedMetrics>(adv)}
              contabilita={contabilita} nav={{ valore: valori?.nav ?? null, posizioni: snap ? (snap.n_positions || snap.positions?.length || 0) : null }}
              attribuzione={attribuzione} nomi={nomi} />
          : <VistaRischio rischio={stato<PortfolioRisk>(rischioRaw)} pnl={pnlStato} contributi={stato<VarContributionResult>(varContrib)}
              concentrazione={stato<ConcentrationResult>(concentration)} drawdown={drawdownStato} liquidita={stato<LiquidityResult>(liquidity)} scenari={scenari}
              onRiprovaScenario={id => caricaScenario(id, true)} nomi={nomi} />}
        <MetodoFonti aperto={metodoAperto} onChiudi={() => setMetodoAperto(false)} sezioni={sezioniMetodo} />
      </div>
    )} />
  );
}
