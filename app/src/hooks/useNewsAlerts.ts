/**
 * BELLOMBERG - News Alert Hook
 *
 * Polla /news/alerts/unnotified ogni 60s (sole letture del DB locale: nessun
 * provider esterno, nessun modello AI). Per ogni notizia sopra la soglia di
 * rilevanza e nel perimetro scelto (titoli in portafoglio o preferiti):
 *  - avviso dentro l'app (evento `bb:news-alert`, reso da AvvisiNotizie);
 *  - notifica di sistema (Web Notification API, nativa in Electron) se
 *    attivata e se la finestra non è in primo piano.
 * Dopo l'avviso la notizia è marcata notified=1 nel backend, così non torna.
 *
 * Configurazione (localStorage, preferenze locali):
 * - 'bellomberg_alerts_enabled'        default 'true'
 * - 'bellomberg_alerts_min_relevance'  default '8'
 * - 'bellomberg_alerts_scope'          'portfolio' | 'favorites', default 'portfolio'
 * - 'bellomberg_alerts_system'         default 'true'
 */
import { useEffect, useRef } from 'react';
import { Bellomberg } from '@/lib/api';

const POLL_INTERVAL_MS = 60_000; // 1 minuto
const ENABLED_KEY = 'bellomberg_alerts_enabled';
const MIN_REL_KEY = 'bellomberg_alerts_min_relevance';
const SCOPE_KEY = 'bellomberg_alerts_scope';
const SYSTEM_KEY = 'bellomberg_alerts_system';
const SHOWN_IDS_KEY = 'bellomberg_alerts_shown_ids_v1';
export const NEWS_ALERT_EVENT = 'bb:news-alert';
export const NEWS_ALERT_SETTINGS_EVENT = 'bb:news-alerts-settings';

export type AlertScope = 'portfolio' | 'favorites';
export interface NewsAlertItem {
  id: number; title: string; snippet: string | null; url: string | null;
  provider: string | null; ticker: string | null; sentiment: string | null;
  relevance: number | null; published_at: string | null; pulled_at: string;
}

function leggi(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function scrivi(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* storage non disponibile */ }
  try { window.dispatchEvent(new CustomEvent(NEWS_ALERT_SETTINGS_EVENT)); } catch { /* SSR/test */ }
}

function getEnabled(): boolean {
  const v = leggi(ENABLED_KEY);
  return v === null ? true : v === 'true';
}

function getMinRelevance(): number {
  const v = leggi(MIN_REL_KEY);
  const n = v ? parseInt(v, 10) : 8;
  return Number.isFinite(n) ? n : 8;
}

function getScope(): AlertScope {
  return leggi(SCOPE_KEY) === 'favorites' ? 'favorites' : 'portfolio';
}

function getSystem(): boolean {
  const v = leggi(SYSTEM_KEY);
  return v === null ? true : v === 'true';
}

function getShownIds(): Set<number> {
  try {
    const v = leggi(SHOWN_IDS_KEY);
    if (!v) return new Set();
    return new Set(JSON.parse(v));
  } catch {
    return new Set();
  }
}

function addShownIds(ids: number[]) {
  const cur = getShownIds();
  ids.forEach(id => cur.add(id));
  // Keep last 500 only (rolling)
  const capped = Array.from(cur).slice(-500);
  try { localStorage.setItem(SHOWN_IDS_KEY, JSON.stringify(capped)); } catch { /* storage non disponibile */ }
}

export function selectNotificationBatch<T extends { id: number }>(
  items: T[], shownIds: Set<number>, limit = 3,
): T[] {
  return items.filter(item => !shownIds.has(item.id)).slice(0, limit);
}

/** Perimetro dell'avviso: 'portfolio' = notizie su un titolo citato; 'favorites' = solo i preferiti. */
export function inScope(item: { ticker: string | null }, scope: AlertScope, favorites: Set<string>): boolean {
  const tk = (item.ticker || '').toUpperCase();
  if (!tk) return false;
  return scope === 'portfolio' || favorites.has(tk);
}

async function requestPermission(): Promise<boolean> {
  if (!('Notification' in window)) return false;
  if (Notification.permission === 'granted') return true;
  if (Notification.permission === 'denied') return false;
  try {
    const p = await Notification.requestPermission();
    return p === 'granted';
  } catch {
    return false;
  }
}

function showNotification(item: NewsAlertItem, onOpen: (id: number) => void) {
  if (!('Notification' in window)) return;
  if (Notification.permission !== 'granted') return;
  const tone = item.sentiment === 'bullish' ? '▲' : item.sentiment === 'bearish' ? '▼' : '•';
  const tickerTag = item.ticker ? `${item.ticker} · ` : '';
  const relTag = item.relevance ? `${item.relevance}/10` : '';
  const body = [tone, relTag, item.provider].filter(Boolean).join(' · ');
  try {
    const n = new Notification(`${tickerTag}${item.title.slice(0, 110)}`, {
      body,
      icon: '/icon.png',
      tag: `bellomberg-news-${item.id}`,
      requireInteraction: false,
      silent: false,
    });
    // il clic riporta in primo piano l'app e apre la notizia nella pagina Notizie
    n.onclick = () => {
      try { window.focus(); } catch { /* finestra non focalizzabile */ }
      onOpen(item.id);
      n.close();
    };
    setTimeout(() => { try { n.close(); } catch { /* già chiusa */ } }, 8000);
  } catch (e) {
    console.warn('[alerts] Notification failed:', e);
  }
}

export function useNewsAlerts(onOpen: (id: number) => void) {
  const stopped = useRef(false);
  const open = useRef(onOpen);
  open.current = onOpen;
  useEffect(() => {
    stopped.current = false;
    let timer: any = null;

    if (getEnabled() && getSystem()) {
      requestPermission().catch(() => {});
    }

    const poll = async () => {
      if (stopped.current) return;
      if (!getEnabled()) return;
      try {
        const minRel = getMinRelevance();
        const res = await Bellomberg.newsAlertsUnnotified(minRel, 10);
        if (!res?.items?.length) return;
        const scope = getScope();
        let favorites = new Set<string>();
        if (scope === 'favorites') {
          const f = await Bellomberg.favorites();
          favorites = new Set((f.favorites || []).map(x => (x.ticker || '').toUpperCase()).filter(Boolean));
        }
        const inPerimeter = res.items.filter(it => inScope(it, scope, favorites));
        const outside = res.items.filter(it => !inScope(it, scope, favorites)).map(i => i.id);
        const toShow = selectNotificationBatch(inPerimeter, getShownIds());
        // fuori perimetro o già mostrate: si marcano lo stesso, non devono riproporsi
        const alreadyShown = inPerimeter.filter(it => !toShow.includes(it) && getShownIds().has(it.id)).map(i => i.id);
        if (outside.length || alreadyShown.length) {
          await Bellomberg.newsAlertsMarkNotified([...outside, ...alreadyShown]).catch(() => {});
        }
        if (toShow.length === 0) return;
        // Al massimo 3 avvisi per giro (anti-spam): gli altri restano non notificati
        // sul backend e verranno ripresi dal giro successivo.
        const fuori = typeof document !== 'undefined' && (document.hidden || !document.hasFocus());
        for (const it of toShow) {
          window.dispatchEvent(new CustomEvent<NewsAlertItem>(NEWS_ALERT_EVENT, { detail: it }));
          if (getSystem() && fuori) showNotification(it, id => open.current(id));
        }
        addShownIds(toShow.map(i => i.id));
        await Bellomberg.newsAlertsMarkNotified(toShow.map(i => i.id)).catch(() => {});
      } catch {
        // silent - polling errors shouldn't spam console
      }
    };

    // First poll after 5s (give app time to settle)
    timer = setTimeout(() => {
      poll();
      timer = setInterval(poll, POLL_INTERVAL_MS);
    }, 5000);

    return () => {
      stopped.current = true;
      if (timer) {
        clearTimeout(timer);
        clearInterval(timer);
      }
    };
  }, []);
}

// Impostazioni (menu «Avvisi» della pagina Notizie)
export const NewsAlertsSettings = {
  isEnabled: getEnabled,
  setEnabled(v: boolean) { scrivi(ENABLED_KEY, String(v)); },
  getMinRelevance,
  setMinRelevance(n: number) { scrivi(MIN_REL_KEY, String(n)); },
  getScope,
  setScope(s: AlertScope) { scrivi(SCOPE_KEY, s); },
  isSystem: getSystem,
  setSystem(v: boolean) { scrivi(SYSTEM_KEY, String(v)); if (v) requestPermission().catch(() => {}); },
  permissionState(): NotificationPermission | 'unsupported' {
    if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported';
    return Notification.permission;
  },
  requestPermission,
};
