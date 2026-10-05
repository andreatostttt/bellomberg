import { t as tr, traduci, type Chiave } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import type { NewsItem } from '@/lib/api';
import { NewsRefreshError } from '@/lib/news-refresh';
import type { NewsRefreshJob } from '@/lib/news-refresh';

/* Funzioni pure della pagina Notizie: tempo, tono, temi, ritmo per ora,
   posizioni del radar, motivi delle fonti mute, ultima visita. */

export type FeedItem = NewsItem & {
  headline_it?: string | null;
  why_matters?: string | null;
  title_original?: string | null;
  snippet_original?: string | null;
  _materiality?: number | null;
};

export type SentBucket = 'pos' | 'neu' | 'neg';

export type NewsProblem = {
  operation: 'feed' | 'refresh' | 'favorites' | 'scheduler' | 'providers' | 'briefing' | 'macro' | 'corporate' | 'global' | 'calendar';
  detail: string | null;
};

export function errorDetail(error: any): string | null {
  // Integrazione G9b+G9c: ogni NewsRefreshError passa da testoErroreRefresh (tutti i codici).
  if (error instanceof NewsRefreshError) return testoErroreRefresh(error);
  return leggiDetail(error?.response?.data?.detail ?? error?.message ?? error) || null;
}

export function problemText(problem: NewsProblem): string {
  return tr(`newsdesk.error_${problem.operation}`) + ': ' + (problem.detail || tr('newsdesk.errorUnknown'));
}

/** Ora HH:MM di un istante ISO del job; null se assente/illeggibile. */
function oraJob(iso: string | null | undefined): string | null {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' }) : null;
}

/** Chi ha avviato il giro che stiamo seguendo (timer, avvio del backend, richiesta manuale). */
export function avviatoDa(job: NewsRefreshJob | null | undefined): string {
  const k = job?.trigger;
  return k === 'schedule' || k === 'startup' || k === 'manual' ? tr(`newsPage.refreshTrigger_${k}` as Chiave) : tr('newsPage.refreshTrigger_unknown');
}

/** Riga di stato di un giro in corso: «in corso dalle HH:MM», e se non l'abbiamo avviato noi lo dice. */
export function statoGiroInCorso(job: NewsRefreshJob | null | undefined, joined: boolean): string {
  const h = oraJob(job?.started_at) ?? tr('newsPage.refreshTimeNd');
  return joined ? tr('newsPage.refreshJoined', { chi: avviatoDa(job), h }) : tr('newsPage.refreshRunning', { h });
}

/** Testo per l'utente di un errore del refresh News (codici di NewsRefreshError, v. lib/news-refresh). */
export function testoErroreRefresh(error: unknown): string | null {
  if (!(error instanceof NewsRefreshError)) return errorDetail(error);
  const job = error.job;
  if (error.code === 'rejected') {
    const stato = job?.status === 'idle' || job?.status === 'success' || job?.status === 'error'
      ? tr(`newsPage.refreshStatus_${job.status}` as Chiave) : (job?.status || tr('newsPage.refreshTimeNd'));
    return tr('newsPage.refreshRejected', { stato, quando: oraJob(job?.finished_at) ?? tr('newsPage.refreshTimeNd') });
  }
  if (error.code === 'job_error') return tr('newsPage.refreshJobError', { dettaglio: job?.error || tr('newsdesk.errorUnknown') });
  if (error.code === 'interrupted') return tr('newsPage.refreshInterrupted', { dettaglio: job?.error || tr('newsdesk.errorUnknown') });
  if (error.code === 'job_lost') return tr('newsPage.refreshJobLost');
  if (error.code === 'unreachable') return tr('newsPage.refreshUnreachable');
  if (error.code === 'timeout') return tr('newsPage.refreshTimeout', { min: error.minuti ?? tr('newsPage.refreshTimeNd'), h: oraJob(job?.started_at) ?? tr('newsPage.refreshTimeNd') });
  // risposta senza il campo `accepted`: si cita il NOME del campo (neutro), non una frase tecnica
  if (error.code === 'bad_status') return tr('newsPage.refreshProtocol', { dettaglio: error.params.field ?? error.code });
  // codici del watcher (G9c): newsdesk.refreshJob_<codice>, con i parametri (id, stato)
  return tr(`newsdesk.refreshJob_${error.code}` as Chiave, error.params);
}

export function originalLanguage(language?: string | null): string {
  return language === 'it' ? tr('newsdesk.languageIt') : language === 'en' ? tr('newsdesk.languageEn') : tr('newsdesk.languageUnknown');
}

export function economicNumber(value?: number | string | null): string {
  if (value == null) return '—';
  // Provider strings carry their own units/precision: never parse them here.
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true, maximumSignificantDigits: 21 })
    : String(value);
}

export function tsOf(n: { published_at?: string | null; pulled_at?: string | null }): number {
  for (const s of [n.published_at, n.pulled_at]) {
    if (s) { const t = new Date(s).getTime(); if (isFinite(t)) return t; }
  }
  return 0;
}

export function hhmm(ts: number): string {
  if (!ts) return '--:--';
  return new Date(ts).toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' });
}

export function dayKeyFromTs(ts: number): string {
  if (!ts) return 'nd';
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** «Oggi · venerdì 2 ottobre», «Ieri · …» o la data lunga; null per la chiave «nd». */
export function dayParts(key: string, today: string, yesterday: string): { head: string; date: string } | null {
  if (key === 'nd') return null;
  const d = new Date(`${key}T12:00:00`);
  const opts: Intl.DateTimeFormatOptions = { weekday: 'long', day: 'numeric', month: 'long' };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  const date = d.toLocaleDateString(localeDi(linguaCorrente()), opts);
  if (key === dayKeyFromTs(Date.now())) return { head: today, date };
  if (key === dayKeyFromTs(Date.now() - 86_400_000)) return { head: yesterday, date };
  return { head: date.charAt(0).toUpperCase() + date.slice(1), date: '' };
}

export function sentBucket(s: string | null | undefined): SentBucket {
  const v = (s || '').toLowerCase();
  if (v === 'bullish' || v === 'positive') return 'pos';
  if (v === 'bearish' || v === 'negative') return 'neg';
  return 'neu';
}

export function matchesDatePresetNews(iso: string | null | undefined, preset: string): boolean {
  if (!preset || preset === 'all') return true;
  if (!iso) return true;
  const t = new Date(iso).getTime();
  if (!isFinite(t)) return true;
  const ageH = (Date.now() - t) / 3600_000;
  if (preset === 'today') {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return t >= today.getTime();
  }
  if (preset === '24h') return ageH <= 24;
  if (preset === '3days') return ageH <= 72;
  if (preset === 'week') return ageH <= 168;
  return true;
}

export function matchesDatePresetCal(eventDate: string, preset: string): boolean {
  if (!preset || preset === 'all') return true;
  if (!eventDate) return false;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
  const weekEnd = new Date(today); weekEnd.setDate(today.getDate() + 7);
  const twoWeeksEnd = new Date(today); twoWeeksEnd.setDate(today.getDate() + 14);
  const ed = new Date(eventDate);
  if (preset === 'today') return ed.toDateString() === today.toDateString();
  if (preset === 'tomorrow') return ed.toDateString() === tomorrow.toDateString();
  if (preset === 'week') return ed >= today && ed <= weekEnd;
  if (preset === '2weeks') return ed >= today && ed <= twoWeeksEnd;
  return true;
}

const HOLIDAY_KEYWORDS = ['day of', 'eid ', 'arafa', 'independence', 'holiday', 'memorial', 'thanksgiving',
  'christmas', 'easter', 'new year', 'labour day', 'national day', 'bank holiday'];
export function isHoliday(title: string): boolean {
  const t = title.toLowerCase();
  return HOLIDAY_KEYWORDS.some(k => t.includes(k));
}

/* ── temi ─────────────────────────────────────────────────────────
   Valori scritti da news_aggregator.auto_pull_feed: fed / ecb / ukraine /
   middle_east / cpi / btc_etf / china / italy; '' = notizia su un titolo
   (__pf) o feed generale (__gen). Temi nuovi: etichetta dal codice. */
const THEME_KEYS: Record<string, Chiave> = {
  fed: 'newsPage.theme_fed', ecb: 'newsPage.theme_ecb', cpi: 'newsPage.theme_cpi', ukraine: 'newsPage.theme_ukraine',
  middle_east: 'newsPage.theme_middle_east', btc_etf: 'newsPage.theme_btc_etf', china: 'newsPage.theme_china',
  italy: 'newsPage.theme_italy', __pf: 'newsPage.theme___pf', __gen: 'newsPage.theme___gen', __altro: 'newsPage.theme___altro',
};

export function themeKeyOf(n: FeedItem): string {
  const th = (n.theme || '').trim();
  if (th) return th;
  return n.ticker_mentioned ? '__pf' : '__gen';
}

export function themeLabelOf(key: string): string {
  const chiave = THEME_KEYS[key];
  if (chiave) return traduci(linguaCorrente(), chiave);
  const raw = key.replace(/_/g, ' ');
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

/* ── ritmo per ora ────────────────────────────────────────────────
   24 secchi orari; l'ultimo è l'ora in corso. Per tono: [pos, neu, neg]. */
export interface OraFlusso { start: number; pos: number; neu: number; neg: number }

export function hourlyBuckets(feed: FeedItem[], now = Date.now()): OraFlusso[] {
  const h0 = new Date(now); h0.setMinutes(0, 0, 0);
  const first = h0.getTime() - 23 * 3600_000;
  const out: OraFlusso[] = Array.from({ length: 24 }, (_, i) => ({ start: first + i * 3600_000, pos: 0, neu: 0, neg: 0 }));
  for (const n of feed) {
    const t = tsOf(n);
    if (!t || t < first || t > now) continue;
    const i = Math.min(23, Math.floor((t - first) / 3600_000));
    out[i][sentBucket(n.sentiment)]++;
  }
  return out;
}

/* ── radar ────────────────────────────────────────────────────────
   Settori per tema (i 7 più presenti + «altro»), ampiezza proporzionale
   alla radice del conteggio con un minimo, così i temi piccoli restano
   leggibili. Raggio = età (al centro le più recenti, bordo = 24 ore). */
export interface SettoreRadar { key: string; n: number; a0: number; a1: number; pos: number; neu: number; neg: number }
export interface PuntoRadar { item: FeedItem; key: string; ageMin: number; angle: number }

/** Posizione stabile in [0, 1) da una stringa (FNV-1a + mescolamento finale): id vicini
 *  come «1003» e «1004» finiscono lontani, così i punti non si ammassano nello spicchio. */
export function hashJitter(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  h ^= h >>> 15; h = Math.imul(h, 0x2c1b3c6d); h ^= h >>> 12; h = Math.imul(h, 0x297a2d39); h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}

export function radarLayout(feed: FeedItem[], now = Date.now()): { sectors: SettoreRadar[]; points: PuntoRadar[] } {
  const items = feed.filter(n => { const t = tsOf(n); return t > 0 && now - t >= 0 && now - t < 86_400_000; });
  const counts: Record<string, number> = {};
  items.forEach(n => { const k = themeKeyOf(n); counts[k] = (counts[k] || 0) + 1; });
  const ordered = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const top = ordered.slice(0, 7).map(([k]) => k);
  const rest = ordered.slice(7).reduce((s, [, c]) => s + c, 0);
  const keys = rest > 0 ? [...top, '__altro'] : top;
  if (keys.length === 0) return { sectors: [], points: [] };
  const keyOf = (n: FeedItem) => { const k = themeKeyOf(n); return top.includes(k) ? k : '__altro'; };
  const sizes = keys.map(k => Math.sqrt(k === '__altro' ? rest : counts[k]));
  const tot = sizes.reduce((a, b) => a + b, 0);
  const minA = keys.length > 1 ? Math.min(26, 360 / keys.length) : 360;
  let raw = sizes.map(s => (s / tot) * 360);
  const extra = raw.reduce((a, v) => a + Math.max(0, minA - v), 0);
  const big = raw.reduce((a, v) => a + (v > minA ? v : 0), 0);
  raw = raw.map(v => (v < minA ? minA : v - (big > 0 ? extra * v / big : 0)));
  let acc = -90 - raw[0] / 2;
  const sectors: SettoreRadar[] = keys.map((key, i) => {
    const a0 = acc; acc += raw[i];
    return { key, n: key === '__altro' ? rest : counts[key], a0, a1: acc, pos: 0, neu: 0, neg: 0 };
  });
  const byKey = new Map(sectors.map(s => [s.key, s]));
  const points = items.map(item => {
    const key = keyOf(item), s = byKey.get(key)!;
    s[sentBucket(item.sentiment)]++;
    const j = hashJitter(String(item.id ?? item.title));
    return { item, key, ageMin: (now - tsOf(item)) / 60_000, angle: s.a0 + (s.a1 - s.a0) * (0.1 + 0.8 * j) };
  });
  return { sectors, points };
}

/* ── schede dei temi (legenda del radar) ──────────────────────────
   Per ogni settore del radar: notizie nelle 24 ore, confronto con le 24 ore
   precedenti (solo se il flusso caricato arriva davvero a 48 ore fa: con il
   prefetch di 200 notizie non è garantito, e un confronto su dati mancanti
   sarebbe un falso calo), tono e notizia più recente. */
export interface SchedaTema { key: string; n: number; prev: number | null; pos: number; neg: number; last: FeedItem | null }

export function themeCards(feed: FeedItem[], sectors: SettoreRadar[], now = Date.now()): SchedaTema[] {
  const keys = new Set(sectors.map(s => s.key));
  const keyOf = (n: FeedItem) => { const k = themeKeyOf(n); return keys.has(k) ? k : '__altro'; };
  const oldest = feed.reduce((m, n) => { const t = tsOf(n); return t > 0 && t < m ? t : m; }, now);
  const coperto = now - oldest >= 48 * 3600_000;
  const out = new Map<string, SchedaTema>(sectors.map(s => [s.key, { key: s.key, n: 0, prev: coperto ? 0 : null, pos: 0, neg: 0, last: null }]));
  for (const item of feed) {
    const t = tsOf(item); if (!t || t > now) continue;
    const c = out.get(keyOf(item)); if (!c) continue;
    const age = now - t;
    if (age < 86_400_000) {
      c.n++;
      const b = sentBucket(item.sentiment);
      if (b === 'pos') c.pos++; else if (b === 'neg') c.neg++;
      if (!c.last || t > tsOf(c.last)) c.last = item;
    } else if (age < 2 * 86_400_000 && c.prev != null) c.prev++;
  }
  return sectors.map(s => out.get(s.key)!);
}

/* ── fonti mute ─────────────────────────────────────────────────── */
// Si traducono i codici del limiter e i prefissi riconosciuti dei motivi di una fonte muta.
// Il suffisso dopo un prefisso riconosciuto resta come arriva dal backend.
const CODICI_MOTIVO: Record<string, Chiave> = {
  NEGOZIO_ASSENTE: 'newsdesk.muteStoreMissing',
  NEGOZIO_ILLEGGIBILE: 'newsdesk.muteStoreUnreadable',
  SENZA_CHIAVE: 'newsdesk.muteMissingKey',
  MODULO_ASSENTE: 'newsdesk.muteMissingModule',
};
// Le etichette del catalogo sono in maiuscolo (testata Obsidian): qui si scrive in sentence case.
// Le sigle restano maiuscole («Missing API key»).
const SIGLE = new Set(['API', 'SEC', 'ETF', 'BTC', 'AI', 'URL', 'RSS']);
export function frase(t: string): string {
  if (t !== t.toUpperCase()) return t;
  return t.split(/(\s+)/).map((p, i) => (SIGLE.has(p) ? p : i === 0 ? p.charAt(0) + p.slice(1).toLowerCase() : p.toLowerCase())).join('');
}
export function motivoMuto(m: unknown): string {
  const s = String(m);
  if (s === 'SKIP_BUDGET') return frase(tr('newsdesk.muteBudget'));
  if (s === 'SKIP_DISABLED') return frase(tr('newsdesk.muteSuspended'));
  const codice = /^(?:NEGOZIO_[A-Z]+|SENZA_CHIAVE|MODULO_ASSENTE)(?=:|$)/.exec(s)?.[0];
  if (codice && Object.prototype.hasOwnProperty.call(CODICI_MOTIVO, codice)) return frase(tr(CODICI_MOTIVO[codice])) + s.slice(codice.length);
  return s.replace('SKIP_', '');
}

// Le chiavi di fonti_mute sono nomi di provider o path finnhub e restano come arrivano;
// le due dei negozi privati sono identificatori del backend e si rendono col nome del catalogo.
const NOMI_FONTI_MUTE: Record<string, Chiave> = {
  'termini_news (negozio)': 'newsdesk.muteStoreTerms',
  'temi_titoli (negozio)': 'newsdesk.muteStoreTopics',
};
export function nomeFonteMuta(chiave: string): string {
  return Object.prototype.hasOwnProperty.call(NOMI_FONTI_MUTE, chiave) ? tr(NOMI_FONTI_MUTE[chiave]) : chiave;
}

/* ── ultima visita ─────────────────────────────────────────────────
   Si salva l'orario della notizia più recente vista in questa visita; alla
   visita dopo sono «nuove» le notizie più recenti di quel momento. Solo
   preferenza locale: senza storage (o alla prima visita) nessuna è nuova. */
const CHIAVE_VISITA = 'bellomberg.news.lastVisit.v1';
export interface UltimaVisita { newestTs: number; at: number }

export function leggiUltimaVisita(): UltimaVisita | null {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(CHIAVE_VISITA) : null;
    if (!raw) return null;
    const v = JSON.parse(raw);
    return typeof v?.newestTs === 'number' && typeof v?.at === 'number' ? v : null;
  } catch { return null; }
}

export function salvaUltimaVisita(newestTs: number): void {
  try {
    if (!newestTs || typeof localStorage === 'undefined') return;
    localStorage.setItem(CHIAVE_VISITA, JSON.stringify({ newestTs, at: Date.now() }));
  } catch { /* storage non disponibile: la funzione resta spenta */ }
}
