import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bell, Sparkles, X } from 'lucide-react';
import { useT } from '@/i18n/provider';
import { NEWS_ALERT_EVENT, useNewsAlerts, type NewsAlertItem } from '@/hooks/useNewsAlerts';
import IconaTitolo from './nuova/IconaTitolo';
import { parole } from '@/pages/news/parole';

/** Chiave e evento con cui un avviso chiede alla pagina Notizie di aprire una notizia
 *  (ed eventualmente di avviarne il riassunto AI, che resta un'azione esplicita). */
export const NEWS_SELECT_KEY = 'bb:newsSelect';
export const NEWS_SELECT_EVENT = 'bb:news-select';
export interface RichiestaNotizia { id: number; ai?: boolean }

export function apriNotizia(navigate: (to: string) => void, richiesta: RichiestaNotizia) {
  try { sessionStorage.setItem(NEWS_SELECT_KEY, JSON.stringify(richiesta)); } catch { /* storage non disponibile */ }
  navigate('/news');
  // se la pagina è già aperta riceve l'evento; altrimenti legge la richiesta al montaggio
  setTimeout(() => window.dispatchEvent(new CustomEvent<RichiestaNotizia>(NEWS_SELECT_EVENT, { detail: richiesta })), 0);
}

const DURATA_MS = 12_000;

/** Avvisi sulle notizie forti: polling globale (useNewsAlerts) + avviso in basso a destra,
 *  su qualunque pagina. Restano al massimo tre avvisi; passarci sopra ferma la chiusura. */
export default function AvvisiNotizie() {
  useT(); // si ridisegna al cambio lingua
  const w = parole();
  const navigate = useNavigate();
  const [avvisi, setAvvisi] = useState<NewsAlertItem[]>([]);
  const timer = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const chiudi = useCallback((id: number) => {
    setAvvisi(a => a.filter(x => x.id !== id));
    const t = timer.current.get(id); if (t) clearTimeout(t); timer.current.delete(id);
  }, []);
  const programma = useCallback((id: number, ms = DURATA_MS) => {
    const t = timer.current.get(id); if (t) clearTimeout(t);
    timer.current.set(id, setTimeout(() => chiudi(id), ms));
  }, [chiudi]);
  const apri = useCallback((id: number, ai = false) => { chiudi(id); apriNotizia(navigate, { id, ai }); }, [chiudi, navigate]);
  useNewsAlerts(id => apriNotizia(navigate, { id }));
  useEffect(() => {
    const on = (e: Event) => {
      const it = (e as CustomEvent<NewsAlertItem>).detail;
      if (!it) return;
      setAvvisi(a => [it, ...a.filter(x => x.id !== it.id)].slice(0, 3));
      programma(it.id);
    };
    window.addEventListener(NEWS_ALERT_EVENT, on);
    const timers = timer.current;
    return () => { window.removeEventListener(NEWS_ALERT_EVENT, on); timers.forEach(clearTimeout); };
  }, [programma]);
  if (avvisi.length === 0) return null;
  return (
    <div className="news-toasts bbn-font" role="region" aria-label={w.alerts}>
      {avvisi.map(it => (
        <div key={it.id} className="news-toast" role="status"
          onMouseEnter={() => { const t = timer.current.get(it.id); if (t) clearTimeout(t); }}
          onMouseLeave={() => programma(it.id, 4000)}>
          {it.ticker ? <IconaTitolo ticker={it.ticker} dimensione="sm" /> : <span className="bbn-ico is-sm news-toast-ico"><Bell size={15} /></span>}
          <div className="tx">
            <span className="k"><Bell size={12} aria-hidden="true" />{w.alertKicker(it.relevance ?? 0)}</span>
            <b>{it.title}</b>
            <span className="s">{[it.ticker, it.provider].filter(Boolean).join(' · ')}</span>
          </div>
          <button type="button" className="bbn-icon-btn x" aria-label={w.alertClose} onClick={() => chiudi(it.id)}><X size={14} /></button>
          <div className="acts">
            <button type="button" className="bbn-btn is-primary" onClick={() => apri(it.id)}>{w.alertOpen}</button>
            <button type="button" className="bbn-btn" onClick={() => apri(it.id, true)}><Sparkles size={13} />{w.aiButton}</button>
          </div>
        </div>
      ))}
    </div>
  );
}
