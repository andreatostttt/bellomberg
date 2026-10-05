import { useT } from '@/i18n/provider';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
// ============================================================
// BELLOMBERG — NOTIZIE in stile Nuova (02/10/2026; prima: News Wire v3 Obsidian)
// Questo componente resta il controller della rotta: letture, timer e stato
// vivono qui, le viste (Flusso, Agenda) ricevono solo dati. LOGICA ED ENDPOINT
// INTATTI dalla v3:
// Vista FLUSSO: nastro cronologico dal feed DB (get_feed), filtri client-side
// sul prefetch (limit 200), ritmo per ora e radar dei temi, dettaglio della
// notizia con riassunto AI SOLO su richiesta (pulsante, mai automatico).
// Vista AGENDA: briefing/macro/societario/calendario/global.
// Refresh pesante verso provider SOLO su bottone; AUTO 60s legge soltanto
// /news/feed (DB locale) e lo stato giro; l'agenda si rilegge ogni 5 minuti.
// FONTI MUTE: rese solo se il backend le dichiara (fonti_mute/avviso), mai
// dedotte client-side.
// ============================================================
import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { localizePayload } from '@/lib/api-presentation';
import { RefreshCw, TriangleAlert } from 'lucide-react';
import {
  Bellomberg,
  MacroNewsItem, CorporateEvent, GlobalNewsItem,
  BriefingData, EconomicEvent, FavCompany, FontiMuteFields, UltimoGiro, PortfolioSnapshot, NewsProviderBudget,
} from '@/lib/api';
import { createNewsRefreshWatcher, esitoGiro, NewsRefreshError } from '../lib/news-refresh';
import { conteggioPerChip, passaFiltroPaese } from '@/lib/calendario-paesi';
import { fmtNum } from '@/lib/format';
import type { NewsRefreshJob } from '../lib/news-refresh';
import ModernPage from '@/components/ModernPage';
import { Segmenti } from '@/components/nuova/Card';
import { NEWS_SELECT_EVENT, NEWS_SELECT_KEY, type RichiestaNotizia } from '@/components/AvvisiNotizie';
import {
  errorDetail, isHoliday, leggiUltimaVisita, matchesDatePresetCal, matchesDatePresetNews, nomeFonteMuta, motivoMuto,
  hhmm, problemText, salvaUltimaVisita, sentBucket, statoGiroInCorso, testoErroreRefresh, themeKeyOf, tsOf,
  type FeedItem, type NewsProblem, type SentBucket,
} from './news/calcoli';
import type { PesoTitolo } from './news/DettaglioNotizia';
import MenuAvvisi from './news/MenuAvvisi';
import PannelloFonti, { type Canale } from './news/PannelloFonti';
import VistaAgenda from './news/VistaAgenda';
import VistaFlusso from './news/VistaFlusso';
import { parole } from './news/parole';
import './news-nuova.css';

function leggiRichiesta(): RichiestaNotizia | null {
  try {
    const raw = sessionStorage.getItem(NEWS_SELECT_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(NEWS_SELECT_KEY);
    const v = JSON.parse(raw);
    return typeof v?.id === 'number' ? v : null;
  } catch { return null; }
}

export default function NewsPage() {
  const tr = useT();
  const w = parole();
  // ---- vista: FLUSSO (default) / AGENDA ----
  const [view, setView] = useState<'wire' | 'desk'>('wire');

  // ---- FLUSSO: feed dal DB (get_feed) ----
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [feedLoad, setFeedLoad] = useState(false);
  const [feedErr, setFeedErr] = useState<NewsProblem | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // conteggi null = n.d. (campo assente nell'esito), mai 0; joined = giro non avviato da questo click
  const [refreshInfo, setRefreshInfo] = useState<{ saved: number | null; duplicates: number | null; notClassified: number | null; joined: boolean } | null>(null);
  const [refreshJoined, setRefreshJoined] = useState(false);
  const [refreshJob, setRefreshJob] = useState<NewsRefreshJob | null>(null);
  const refreshWatcherRef = useRef<{ stop: () => void } | null>(null);
  const [auto, setAuto] = useState(true);
  const [lastFeedAt, setLastFeedAt] = useState<Date | null>(null);
  const [favs, setFavs] = useState<FavCompany[]>([]);
  const [favsErr, setFavsErr] = useState<NewsProblem | null>(null);
  const [schedulerErr, setSchedulerErr] = useState<NewsProblem | null>(null);
  const [providersErr, setProvidersErr] = useState<NewsProblem | null>(null);
  const feedRead = useRef(0);
  const briefingRead = useRef(0);
  const cacheLanguage = useRef(linguaCorrente());

  // ---- filtri del flusso (combinabili, client-side sul prefetch) ----
  const [fPeriod, setFPeriod] = useState<string>('all');
  const [fThemes, setFThemes] = useState<string[]>([]);
  const [fSent, setFSent] = useState<SentBucket[]>([]);
  const [fRel, setFRel] = useState<number>(0);
  // preselect ticker dal Command Palette (sessionStorage 'bb:newsTicker')
  const [fTickers, setFTickers] = useState<string[]>(() => {
    const t = sessionStorage.getItem('bb:newsTicker');
    if (t) { sessionStorage.removeItem('bb:newsTicker'); return [t.toUpperCase()]; }
    return [];
  });

  // ---- AGENDA ----
  const [briefing, setBriefing] = useState<BriefingData | null>(null);
  const [briefingLoad, setBriefingLoad] = useState(false);
  const [briefingErr, setBriefingErr] = useState<NewsProblem | null>(null);

  const [rawMacro, setRawMacro] = useState<Awaited<ReturnType<typeof Bellomberg.newsMacro>> | null>(null);
  const macro = useMemo(() => localizePayload(rawMacro)?.items || [], [rawMacro, tr]);
  const [macroLoad, setMacroLoad] = useState(false);
  const [macroErr, setMacroErr] = useState<NewsProblem | null>(null);
  const [macroCategorySel, setMacroCategorySel] = useState<string[]>([]);
  const [macroDateSel, setMacroDateSel] = useState<string>('week');

  const [rawCorp, setRawCorp] = useState<Awaited<ReturnType<typeof Bellomberg.newsCorporateEvents>> | null>(null);
  const corp = useMemo(() => localizePayload(rawCorp)?.items || [], [rawCorp, tr]);
  const [corpLoad, setCorpLoad] = useState(false);
  const [corpErr, setCorpErr] = useState<NewsProblem | null>(null);
  const [corpDateSel, setCorpDateSel] = useState<string>('week');

  const [globalNews, setGlobalNews] = useState<GlobalNewsItem[]>([]);
  const [globalLoad, setGlobalLoad] = useState(false);
  const [globalErr, setGlobalErr] = useState<NewsProblem | null>(null);
  const [globalDateSel, setGlobalDateSel] = useState<string>('24h');

  const [econ, setEcon] = useState<EconomicEvent[]>([]);
  const [econLoad, setEconLoad] = useState(false);
  const [econErr, setEconErr] = useState<NewsProblem | null>(null);
  const [econCountrySel, setEconCountrySel] = useState<string[]>(['US', 'EU', 'IT', 'DE']);
  const [econImportanceSel, setEconImportanceSel] = useState<number[]>([4, 5]);
  const [econDateSel, setEconDateSel] = useState<string>('week');

  const [lastDeskAt, setLastDeskAt] = useState<Date | null>(null);

  // ---- FONTI MUTE (voce (25) backend, regola no-fallback-silenziosi) ----
  // declared=false finché NESSUN endpoint consumato espone fonti_mute/avviso:
  // in pagina si dichiara "n.d.", mai dedotto. Quando il campo arriva:
  // mute={} = dichiarato "nessun provider muto"; mute={prov:motivo} = buco dichiarato.
  const [fonti, setFonti] = useState<{ declared: boolean; mute: Record<string, string>; avviso: string | null }>(
    { declared: false, mute: {}, avviso: null });
  const takeFonti = useCallback((d: FontiMuteFields) => {
    // chiave ASSENTE = endpoint che non dichiara (stato invariato);
    // chiave presente (anche null) = dichiarazione del backend, resa com'e'.
    if (!('fonti_mute' in d) && !('avviso' in d)) return;
    setFonti({ declared: true, mute: d.fonti_mute || {}, avviso: d.avviso ?? null });
  }, []);

  // ---- FRESCHEZZA DEL FEED (ponte (68) backend). Tre verità DISTINTE: 'attesa' =
  // prima lettura in volo · 'errore' = endpoint non raggiunto · 'assente' =
  // risponde ma senza `ultimo_giro`. Il giro si rende con lo STATO e non solo con l'età.
  const [giro, setGiro] = useState<'attesa' | 'errore' | 'assente' | UltimoGiro>('attesa');
  const [nextRun, setNextRun] = useState<string | null>(null);
  const loadGiro = useCallback(async () => {
    try {
      const d = await Bellomberg.newsProviders();
      setProvidersErr(null);
      setGiro(d.ultimo_giro ?? 'assente');
      setRefreshJob(d.refresh_job ?? null);
      setBudget(d.budget ?? null);
      takeFonti(d);   // il payload porta ANCHE fonti_mute/avviso
    } catch (e) { setGiro('errore'); setProvidersErr({ operation: 'providers', detail: errorDetail(e) }); }
  }, [takeFonti]);
  // il «prossimo giro» è NextRunTime del task NewsFeed: task Disabled o formato
  // imprevisto → null, e in pagina si scrive n.d.
  const loadNextRun = useCallback(async () => {
    try {
      const d = await Bellomberg.scheduledTasks();
      setSchedulerErr(null);
      const t = (d.tasks || []).find((x: any) => x?.TaskName === 'Bellomberg-NewsFeed');
      const m = t && t.State !== 'Disabled' && typeof t.NextRunTime === 'string'
        ? t.NextRunTime.match(/(\d{2}:\d{2}):\d{2}$/) : null;
      setNextRun(m ? m[1] : null);
    } catch (e) { setNextRun(null); setSchedulerErr({ operation: 'scheduler', detail: errorDetail(e) }); }
  }, []);

  // ---- stato della presentazione Nuova (in coda: i test SSR seminano useState per indice) ----
  const [fQuery, setFQuery] = useState('');
  const [favOnly, setFavOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [aiRequestId, setAiRequestId] = useState<number | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [snapRaw, setSnap] = useState<PortfolioSnapshot | null>(null);
  const [favBusy, setFavBusy] = useState(false);
  const [favActionErr, setFavActionErr] = useState<string | null>(null);
  const [lastVisit] = useState(() => leggiUltimaVisita());
  const [budget, setBudget] = useState<Record<string, NewsProviderBudget> | null>(null);

  const providerIntervalLabel = refreshJob?.interval_seconds
    ? `${Math.round(refreshJob.interval_seconds / 60)} min`
    : null;
  // next_run_at arriva ISO dal job del backend, NextRunTime già HH:MM dallo scheduler
  const oraDa = (v: string | null | undefined) => {
    if (!v) return null;
    if (/^\d{2}:\d{2}$/.test(v)) return v;
    const t = Date.parse(v);
    return isFinite(t) ? hhmm(t) : v;
  };
  const providerNextRun = oraDa(refreshJob?.next_run_at) ?? nextRun ?? null;

  // ============================================================
  // LOADERS
  // ============================================================
  // Lettura LEGGERA dal DB locale (/news/feed): nessun provider esterno.
  const loadFeed = useCallback(async () => {
    const read = ++feedRead.current, language = linguaCorrente();
    setFeedLoad(true);
    try {
      const d = await Bellomberg.newsFeed({ limit: 200, min_relevance: 0 });
      if (read !== feedRead.current || language !== linguaCorrente()) return;
      const items = (d.items || []) as FeedItem[];
      setFeed(items);
      setFeedErr(null);
      setLastFeedAt(new Date());
      salvaUltimaVisita(items.reduce((m, n) => Math.max(m, tsOf(n)), 0));
      takeFonti(d);
    } catch (e) { if (read === feedRead.current && language === linguaCorrente()) setFeedErr({ operation: 'feed', detail: errorDetail(e) }); }
    finally { if (read === feedRead.current) setFeedLoad(false); }
  }, [takeFonti]);

  const loadFavs = useCallback(async () => {
    try { const d = await Bellomberg.favorites(); setFavs(d.favorites || []); setFavsErr(null); }
    catch (e) { setFavsErr({ operation: 'favorites', detail: errorDetail(e) }); }
  }, []);

  // Refresh provider: una POST di accettazione, poi solo GET dello stato condiviso.
  const heavyRefresh = useCallback(async () => {
    setRefreshing(true); setRefreshInfo(null); setFeedErr(null); setRefreshJoined(false);
    const watcher = createNewsRefreshWatcher({
      start: async () => {
        const r = await Bellomberg.newsFeedRefresh(1, true);
        takeFonti(r);
        setRefreshJoined(r?.accepted === false);
        return r;
      },
      // stato del SOLO job seguito (contratto G3); le fonti si rileggono a giro finito (loadGiro)
      readStatus: async id => (await Bellomberg.newsRefreshJob(id)).job,
      onStatus: setRefreshJob,
    });
    refreshWatcherRef.current = watcher;
    try {
      const job = await watcher.run();
      if (job.status === 'error') throw new NewsRefreshError('job_error', {}, job);
      if (job.status === 'interrupted') throw new NewsRefreshError('interrupted', {}, job);
      const esito = esitoGiro(job);
      setRefreshInfo({ saved: esito.saved, duplicates: esito.duplicates, notClassified: esito.notClassified, joined: job.joined });
      // il refresh manuale E' un giro: lo stato freschezza va riletto
      await Promise.all([loadFeed(), loadFavs(), loadGiro()]);
    } catch (e) { setFeedErr({ operation: 'refresh', detail: testoErroreRefresh(e) }); }
    finally {
      if (refreshWatcherRef.current === watcher) refreshWatcherRef.current = null;
      setRefreshing(false);
    }
  }, [loadFeed, loadFavs, loadGiro, takeFonti]);

  const loadBriefing = useCallback(async () => {
    const read = ++briefingRead.current, language = linguaCorrente();
    try {
      const d = await Bellomberg.briefingCurrent();
      if (read !== briefingRead.current || language !== linguaCorrente()) return;
      setBriefing(d); setBriefingErr(d.error ? { operation: 'briefing', detail: d.error } : null);
    } catch (e) { if (read === briefingRead.current && language === linguaCorrente()) setBriefingErr({ operation: 'briefing', detail: errorDetail(e) }); }
  }, []);
  const refreshBriefing = useCallback(async () => {
    const read = ++briefingRead.current, language = linguaCorrente();
    setBriefingLoad(true); setBriefingErr(null);
    try { const d = await Bellomberg.briefingRefresh();
      if (read !== briefingRead.current || language !== linguaCorrente()) return;
      setBriefing(d); if (d.error) setBriefingErr({ operation: 'briefing', detail: d.error }); }
    catch (e) { if (read === briefingRead.current && language === linguaCorrente()) setBriefingErr({ operation: 'briefing', detail: errorDetail(e) }); }
    finally { setBriefingLoad(false); }
  }, []);
  const loadMacro = useCallback(async (cats: string[] = []) => {
    setMacroLoad(true); setMacroErr(null);
    try {
      const d = await Bellomberg.newsMacro({
        categories: cats.length > 0 ? cats.join(',') : undefined,
        min_importance: 3, days: 2, max_per_topic: 3, include_reddit: false,
      });
      setRawMacro(d);
      takeFonti(d);
    } catch (e) { setMacroErr({ operation: 'macro', detail: errorDetail(e) }); }
    finally { setMacroLoad(false); }
  }, [takeFonti]);
  const loadCorporate = useCallback(async () => {
    setCorpLoad(true); setCorpErr(null);
    try { const d = await Bellomberg.newsCorporateEvents(30, 50); setRawCorp(d); takeFonti(d); }
    catch (e) { setCorpErr({ operation: 'corporate', detail: errorDetail(e) }); }
    finally { setCorpLoad(false); }
  }, [takeFonti]);
  const loadGlobal = useCallback(async () => {
    setGlobalLoad(true); setGlobalErr(null);
    try { const d = await Bellomberg.newsTopGlobal(30); setGlobalNews(d.items || []); takeFonti(d); }
    catch (e) { setGlobalErr({ operation: 'global', detail: errorDetail(e) }); }
    finally { setGlobalLoad(false); }
  }, [takeFonti]);
  const loadEcon = useCallback(async () => {
    setEconLoad(true); setEconErr(null);
    try { const d = await Bellomberg.economicCalendar(14); setEcon(d.items || []); }
    catch (e) { setEconErr({ operation: 'calendar', detail: errorDetail(e) }); }
    finally { setEconLoad(false); }
  }, []);
  const refreshAllDesk = useCallback(async () => {
    await Promise.all([loadBriefing(), loadMacro(macroCategorySel),
                       loadCorporate(), loadGlobal(), loadEcon()]);
    setLastDeskAt(new Date());
  }, [loadBriefing, loadMacro, loadCorporate, loadGlobal, loadEcon, macroCategorySel]);

  // ============================================================
  // EFFECTS
  // ============================================================
  // Language changes select existing local cache versions; these two GETs do
  // not pull providers or generate a new summary/briefing.
  useEffect(() => {
    const selected = linguaCorrente();
    if (cacheLanguage.current === selected) return;
    cacheLanguage.current = selected;
    loadFeed(); loadBriefing();
  }, [tr, loadFeed, loadBriefing]);
  // Mount + poll 5 min dell'agenda (nessun aumento di frequenza verso i provider esterni).
  useEffect(() => {
    loadFeed(); loadFavs(); refreshAllDesk(); loadGiro(); loadNextRun();
    const id = setInterval(() => { loadFeed(); refreshAllDesk(); loadGiro(); loadNextRun(); }, 5 * 60 * 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // AUTO 60s: SOLO letture leggere — get_feed (DB) e lo stato giro (file
  // json via /news/providers), nessun provider esterno.
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => { loadFeed(); loadGiro(); }, 60 * 1000);
    return () => clearInterval(id);
  }, [auto, loadFeed, loadGiro]);

  useEffect(() => () => refreshWatcherRef.current?.stop(), []);

  // cambio categorie macro -> ricarica server-side (skip primo render)
  const firstMacroRun = useRef(true);
  useEffect(() => {
    if (firstMacroRun.current) { firstMacroRun.current = false; return; }
    loadMacro(macroCategorySel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [macroCategorySel]);

  // Pesi in portafoglio per «% del portafoglio»: una lettura all'apertura, poi lo
  // snapshot che il runner di Layout pubblica (nessun polling in più).
  useEffect(() => {
    let vivo = true;
    Bellomberg.portfolio().then(p => { if (vivo) setSnap(p); }).catch(() => {});
    const onSnapshot = (event: Event) => {
      const snapshot = (event as CustomEvent<PortfolioSnapshot>).detail;
      if (snapshot) setSnap(snapshot);
    };
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return () => { vivo = false; };
    window.addEventListener('bb:portfolio-snapshot', onSnapshot);
    return () => { vivo = false; window.removeEventListener('bb:portfolio-snapshot', onSnapshot); };
  }, []);

  // Un avviso (in app o di sistema) chiede di aprire una notizia: si torna al flusso
  // senza filtri, così la notizia è visibile. Il riassunto AI parte solo se l'avviso
  // lo ha chiesto esplicitamente con il suo pulsante.
  useEffect(() => {
    const apri = (r: RichiestaNotizia | null) => {
      if (!r) return;
      setView('wire');
      setFPeriod('all'); setFThemes([]); setFSent([]); setFRel(0); setFTickers([]); setFQuery(''); setFavOnly(false);
      setSelectedId(r.id);
      setAiRequestId(r.ai ? r.id : null);
    };
    apri(leggiRichiesta());
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
    const on = (e: Event) => { leggiRichiesta(); apri((e as CustomEvent<RichiestaNotizia>).detail); };
    window.addEventListener(NEWS_SELECT_EVENT, on);
    return () => window.removeEventListener(NEWS_SELECT_EVENT, on);
  }, []);

  // ============================================================
  // DERIVED — FLUSSO
  // ============================================================
  const favSet = useMemo(
    () => new Set(favs.map(f => (f.ticker || '').toUpperCase()).filter(Boolean)),
    [favs]);

  const pesi = useMemo(() => {
    const m = new Map<string, PesoTitolo>();
    for (const p of snapRaw?.positions || []) {
      if (p.ticker && Number.isFinite(p.peso_pct)) m.set(p.ticker.toUpperCase(), { peso: p.peso_pct, nome: p.nome });
    }
    return m;
  }, [snapRaw]);

  const feedTickerCounts = useMemo(() => {
    const m: Record<string, number> = {};
    feed.forEach(n => {
      const t = (n.ticker_mentioned || '').toUpperCase();
      if (t) m[t] = (m[t] || 0) + 1;
    });
    return m;
  }, [feed]);

  const tickerOptions = useMemo(() => {
    const m = { ...feedTickerCounts };
    fTickers.forEach(t => { if (!(t in m)) m[t] = 0; });
    return Object.entries(m).map(([ticker, count]) => ({ ticker, count }))
      .sort((a, b) => b.count - a.count || a.ticker.localeCompare(b.ticker));
  }, [feedTickerCounts, fTickers]);

  const themeOptions = useMemo(() => {
    const counts: Record<string, number> = {};
    feed.forEach(n => { const k = themeKeyOf(n); counts[k] = (counts[k] || 0) + 1; });
    fThemes.forEach(k => { if (!(k in counts)) counts[k] = 0; });
    return Object.entries(counts).map(([key, count]) => ({ key, count })).sort((a, b) => b.count - a.count);
  }, [feed, fThemes]);

  const filtered = useMemo(() => {
    const q = fQuery.trim().toLowerCase();
    return feed.filter(n => {
      if (!matchesDatePresetNews(n.published_at || n.pulled_at, fPeriod)) return false;
      if (fThemes.length > 0 && !fThemes.includes(themeKeyOf(n))) return false;
      if (fSent.length > 0 && !fSent.includes(sentBucket(n.sentiment))) return false;
      if (fRel > 0 && (n.relevance ?? 0) < fRel) return false;
      const tk = (n.ticker_mentioned || '').toUpperCase();
      if (fTickers.length > 0 && !fTickers.includes(tk)) return false;
      if (favOnly && !(tk && favSet.has(tk))) return false;
      if (q && ![n.title, n.snippet, n.title_original, n.snippet_original, tk, n.source]
        .some(x => (x || '').toLowerCase().includes(q))) return false;
      return true;
    });
  }, [feed, fPeriod, fThemes, fSent, fRel, fTickers, favOnly, favSet, fQuery]);

  // Selezione: quella scelta se è nell'elenco filtrato, altrimenti la notizia più recente.
  const effectiveId = useMemo(() => {
    if (selectedId != null && filtered.some(n => n.id === selectedId)) return selectedId;
    let best: FeedItem | null = null;
    for (const n of filtered) if (!best || tsOf(n) > tsOf(best)) best = n;
    return best?.id ?? null;
  }, [selectedId, filtered]);

  // «Nuove dall'ultima visita»: più recenti della notizia più nuova vista l'ultima volta.
  const nuove = useMemo(() => {
    if (!lastVisit) return new Set<number>();
    return new Set(feed.filter(n => tsOf(n) > lastVisit.newestTs).map(n => n.id));
  }, [feed, lastVisit]);

  const stats = useMemo(() => {
    const tickers = new Set<string>(), providers = new Set<string>();
    let relSum = 0, relN = 0;
    for (const n of feed) {
      if (n.ticker_mentioned) tickers.add(n.ticker_mentioned.toUpperCase());
      if (n.provider) providers.add(n.provider);
      if (n.relevance != null) { relSum += n.relevance; relN++; }
    }
    return { tickers: tickers.size, providers: providers.size, avgRel: relN > 0 ? relSum / relN : 0, stored: feed.length };
  }, [feed]);

  // CANALI: provider visti nel feed (count + ultimo item) fusi con la dichiarazione
  // fonti_mute; un provider muto ASSENTE dal feed compare comunque a 0.
  const channels = useMemo<Canale[]>(() => {
    const m: Record<string, { count: number; last: number }> = {};
    for (const n of feed) {
      const p = (n.provider || '?').toLowerCase();
      const t = tsOf(n);
      if (!m[p]) m[p] = { count: 0, last: 0 };
      m[p].count++;
      if (t > m[p].last) m[p].last = t;
    }
    const out: Canale[] = Object.entries(m).map(([name, v]) => ({
      name, count: v.count, last: v.last, muteReason: fonti.mute[name] ?? null,
    }));
    for (const [p, motivo] of Object.entries(fonti.mute)) {
      if (!(p.toLowerCase() in m)) out.push({ name: p.toLowerCase(), count: 0, last: 0, muteReason: String(motivo) });
    }
    return out.sort((a, b) => b.count - a.count);
  }, [feed, fonti.mute]);

  const resetFilters = useCallback(() => {
    setFPeriod('all'); setFThemes([]); setFSent([]); setFRel(0); setFTickers([]); setFQuery(''); setFavOnly(false);
  }, []);
  const toggle = <T,>(set: React.Dispatch<React.SetStateAction<T[]>>) => (v: T) =>
    set(arr => (arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]));

  const toggleFav = useCallback(async (ticker: string, nome?: string) => {
    setFavBusy(true); setFavActionErr(null);
    try {
      if (favSet.has(ticker)) await Bellomberg.favDel(ticker);
      else await Bellomberg.favAdd({ ticker, ...(nome ? { name: nome } : {}) });
      await loadFavs();
    } catch (e) { setFavActionErr(errorDetail(e)); }
    finally { setFavBusy(false); }
  }, [favSet, loadFavs]);

  // ============================================================
  // DERIVED — AGENDA
  // ============================================================
  const filteredMacro = useMemo(() => {
    return macro.filter(n => matchesDatePresetNews(n.published_at, macroDateSel)).slice(0, 100) as MacroNewsItem[];
  }, [macro, macroDateSel]);

  const filteredCorp = useMemo(() => {
    return (corp as CorporateEvent[]).filter(e => matchesDatePresetNews(e.published_at, corpDateSel));
  }, [corp, corpDateSel]);

  const filteredGlobal = useMemo(() => {
    return globalNews.filter(n => matchesDatePresetNews(n.published_at, globalDateSel));
  }, [globalNews, globalDateSel]);

  const filteredEcon = useMemo(() => {
    return econ.filter(e => {
      if (isHoliday(e.title || '')) return false;
      if (!matchesDatePresetCal(e.date, econDateSel)) return false;
      if (econImportanceSel.length > 0 && !econImportanceSel.includes(e.importance)) return false;
      // G8: le trimestrali del portafoglio passano sempre; paesi senza chip e «?» = chip «Altri»
      if (!passaFiltroPaese(e, econCountrySel)) return false;
      return true;
    });
  }, [econ, econCountrySel, econImportanceSel, econDateSel]);

  const countryCounts = useMemo(() => {
    return conteggioPerChip(econ.filter(e => !isHoliday(e.title || '')));
  }, [econ]);

  const deskBusy = briefingLoad || macroLoad || corpLoad || globalLoad || econLoad;
  const nMute = Object.keys(fonti.mute).length;
  const giroDichiarato = typeof giro === 'object' ? giro : null;
  const giroParziale = giroDichiarato?.stato === 'degradato';
  const giroGuasto = giro === 'errore' || giro === 'assente' || (!!giroDichiarato && giroDichiarato.stato !== 'ok' && !giroParziale);
  const fontiTono = giroGuasto ? 'bad' : nMute > 0 || giroParziale ? 'warn' : !fonti.declared ? 'off' : 'ok';
  const bloccatiGiro = Object.entries(giroDichiarato?.providers_blocked || {}).map(([p, m]) => `${nomeFonteMuta(p)} (${motivoMuto(m)})`).join(', ');
  const oraDi = (d: Date | null) => d ? d.toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' }) : null;
  const aggiornato = oraDi(view === 'wire' ? lastFeedAt : lastDeskAt);

  // ============================================================
  // RENDER
  // ============================================================
  return (
    <ModernPage page="news" render={() => (
      <div className="bbn-notizie bbn-font" data-view={view}>
        <header className="news-top">
          <h1>{w.title}</h1>
          <span data-qa="news-view">
            <Segmenti<'wire' | 'desk'> etichetta={w.views} valore={view} onChange={setView} className="is-large"
              opzioni={[{ id: 'wire', testo: w.viewFlow }, { id: 'desk', testo: w.viewAgenda }]} />
          </span>
          {aggiornato && <span className="bbn-chip">{w.updatedAt(aggiornato)}</span>}
          {nuove.size > 0 && view === 'wire' && <span className="bbn-chip news-chip-new"><i aria-hidden="true" />{w.newSince(nuove.size)}</span>}
          {refreshing && refreshJob?.status === 'running' && <span className="bbn-chip" role="status" data-qa="news-refresh-running">{statoGiroInCorso(refreshJob, refreshJoined)}</span>}
          {refreshInfo && <span className="bbn-chip" data-qa="news-refresh-result">{refreshInfo.joined ? w.refreshJoinedDone + ' ' : ''}{w.refreshResult(fmtNum(refreshInfo.saved, 0), fmtNum(refreshInfo.duplicates, 0))}{refreshInfo.notClassified ? ` · ${w.refreshNotClassified(fmtNum(refreshInfo.notClassified, 0))}` : ''}</span>}
          {/* ESITO DELL'ULTIMO GIRO: quante lette, quante nuove, quando il prossimo — «nessuna novità»
              e «non sta girando» non devono sembrare la stessa cosa */}
          {giro !== 'attesa' && (
            <button type="button" className={'bbn-chip news-round' + (giroGuasto ? ' is-bad' : giroParziale ? ' is-warn' : '')}
              data-qa="news-round" onClick={() => setSourcesOpen(true)} title={w.roundChipHint}>
              <span className={`news-dot is-${giroGuasto ? 'bad' : giroParziale ? 'warn' : 'ok'}`} aria-hidden="true" />
              {giroDichiarato && giroDichiarato.timestamp
                ? <>{w.roundChip(hhmm(Date.parse(giroDichiarato.timestamp)), giroDichiarato.saved ?? null, giroDichiarato.fetched ?? null)}
                    {providerNextRun && <span className="nx"> · {w.roundChipNext(providerNextRun)}</span>}</>
                : w.roundChipNone}
            </button>
          )}
          <span className="bbn-grow" />
          <button type="button" className="bbn-switch" role="switch" aria-checked={auto} onClick={() => setAuto(a => !a)} title={w.autoHint}>
            <i />{w.auto}
          </button>
          <MenuAvvisi />
          <button type="button" className="bbn-btn" data-qa="news-sources" onClick={() => setSourcesOpen(true)}>
            <span className={`news-dot is-${fontiTono}`} aria-hidden="true" />{w.sources}
          </button>
          {view === 'wire' ? (
            <button type="button" className="bbn-btn is-primary" data-qa="news-refresh" onClick={heavyRefresh} disabled={refreshing} title={w.pollHint}>
              <RefreshCw size={14} className={refreshing ? 'spin' : undefined} />{refreshing ? w.pollingProviders : w.pollProviders}
            </button>
          ) : (
            <button type="button" className="bbn-btn is-primary" data-qa="news-refresh" onClick={refreshAllDesk} disabled={deskBusy}>
              <RefreshCw size={14} className={deskBusy ? 'spin' : undefined} />{deskBusy ? w.refreshingAgenda : w.refreshAgenda}
            </button>
          )}
        </header>

        {(schedulerErr || providersErr || favsErr) && (
          <div className="news-notes">
            {[schedulerErr, providersErr, favsErr].filter(Boolean).map(p => (
              <p key={p!.operation} className="news-note is-bad" role="status"><TriangleAlert size={15} aria-hidden="true" />{problemText(p!)}</p>
            ))}
          </div>
        )}

        {/* AVVISO COPERTURA: reso SOLO se il backend lo dichiara, mai dedotto */}
        {fonti.declared && (nMute > 0 || fonti.avviso) && (
          <p className="news-note is-warn" role="status">
            <TriangleAlert size={15} aria-hidden="true" />
            <span className="txt"><b>{w.coverage}</b>{' '}
              {nMute > 0 && w.coverageMute(Object.entries(fonti.mute).map(([p, m]) => `${nomeFonteMuta(p)} (${motivoMuto(m)})`).join(', '))}
              {fonti.avviso ? ` ${fonti.avviso}` : ''}
            </span>
            <button type="button" className="bbn-link" onClick={() => setSourcesOpen(true)}>{w.seeSources}</button>
          </p>
        )}

        {/* GIRO PARZIALE: lo stato del giro dei provider resta visibile anche quando il feed
            dichiara le fonti attive (risposte diverse, momenti diversi): mai solo nel pannello */}
        {giroParziale && !(fonti.declared && nMute > 0) && (
          <p className="news-note is-warn" role="status">
            <TriangleAlert size={15} aria-hidden="true" />
            <span className="txt">{w.roundPartialNote(bloccatiGiro)}</span>
            <button type="button" className="bbn-link" onClick={() => setSourcesOpen(true)}>{w.seeSources}</button>
          </p>
        )}

        {view === 'wire' ? (
          <VistaFlusso feed={feed} filtered={filtered} feedLoad={feedLoad} feedErr={feedErr} refreshing={refreshing}
            onRetry={loadFeed} onPoll={heavyRefresh}
            filtri={{ period: fPeriod, themes: fThemes, sent: fSent, rel: fRel, tickers: fTickers, q: fQuery, favOnly }}
            azioni={{ setPeriod: setFPeriod, toggleTheme: toggle(setFThemes), toggleSent: toggle(setFSent), setRel: setFRel,
              toggleTicker: toggle(setFTickers), setQ: setFQuery, setFavOnly, reset: resetFilters }}
            themeOptions={themeOptions} tickerOptions={tickerOptions} favSet={favSet} pesi={pesi}
            selectedId={effectiveId} onSelect={id => { setSelectedId(id); setAiRequestId(null); }}
            onToggleFav={toggleFav} favBusy={favBusy} favError={favActionErr}
            nuove={nuove} ultimaVisita={lastVisit?.at ?? null}
            aiSubito={aiRequestId != null && aiRequestId === effectiveId} onAiSubito={() => setAiRequestId(null)} />
        ) : (
          <VistaAgenda briefing={briefing} briefingLoad={briefingLoad} briefingErr={briefingErr} onRewrite={refreshBriefing}
            econ={filteredEcon} econLoad={econLoad} econErr={econErr} countryCounts={countryCounts}
            econDate={econDateSel} setEconDate={setEconDateSel} econImportance={econImportanceSel} setEconImportance={setEconImportanceSel}
            econCountries={econCountrySel} setEconCountries={setEconCountrySel}
            corp={filteredCorp} corpLoad={corpLoad} corpErr={corpErr} corpDate={corpDateSel} setCorpDate={setCorpDateSel}
            macro={filteredMacro} macroLoad={macroLoad} macroErr={macroErr} macroDate={macroDateSel} setMacroDate={setMacroDateSel}
            macroCategories={macroCategorySel} setMacroCategories={setMacroCategorySel}
            global={filteredGlobal} globalLoad={globalLoad} globalErr={globalErr} globalDate={globalDateSel} setGlobalDate={setGlobalDateSel}
            pesi={pesi} />
        )}

        <PannelloFonti aperto={sourcesOpen} onChiudi={() => setSourcesOpen(false)} giro={giro}
          intervallo={providerIntervalLabel} prossimo={providerNextRun} canali={channels} fonti={fonti} copertura={stats} budget={budget} />
      </div>
    )} />
  );
}
