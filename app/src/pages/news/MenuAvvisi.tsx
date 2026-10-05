import { useEffect, useRef, useState } from 'react';
import { Bell } from 'lucide-react';
import { NEWS_ALERT_SETTINGS_EVENT, NewsAlertsSettings, type AlertScope } from '@/hooks/useNewsAlerts';
import { Segmenti } from '@/components/nuova/Card';
import { parole } from './parole';

const leggi = () => ({
  on: NewsAlertsSettings.isEnabled(), min: NewsAlertsSettings.getMinRelevance(),
  scope: NewsAlertsSettings.getScope(), system: NewsAlertsSettings.isSystem(),
  perm: NewsAlertsSettings.permissionState(),
});

/** Pulsante «Avvisi» con le impostazioni degli avvisi sulle notizie forti (preferenze locali). */
export default function MenuAvvisi() {
  const w = parole();
  const [aperto, setAperto] = useState(false);
  const [v, setV] = useState(leggi);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const sync = () => setV(leggi());
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
    window.addEventListener(NEWS_ALERT_SETTINGS_EVENT, sync);
    return () => window.removeEventListener(NEWS_ALERT_SETTINGS_EVENT, sync);
  }, []);
  useEffect(() => {
    if (!aperto) return;
    const fuori = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setAperto(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setAperto(false); };
    document.addEventListener('mousedown', fuori); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', fuori); document.removeEventListener('keydown', esc); };
  }, [aperto]);
  const soglia = String(v.min >= 10 ? 10 : v.min >= 9 ? 9 : 8) as '8' | '9' | '10';
  return (
    <div className="news-dd" ref={root}>
      <button type="button" className="bbn-btn news-alerts-btn" aria-haspopup="true" aria-expanded={aperto} onClick={() => setAperto(a => !a)}>
        <Bell size={14} aria-hidden="true" />{w.alerts}
        <span className={'news-count' + (v.on ? '' : ' is-off')}>{v.on ? (soglia === '10' ? '10' : `${soglia}+`) : '—'}</span>
      </button>
      {aperto && (
        <div className="news-menu news-alerts-menu" role="dialog" aria-label={w.alerts}>
          <div className="mh">{w.alertsHead}</div>
          <button type="button" className="bbn-switch news-arow" role="switch" aria-checked={v.on} onClick={() => NewsAlertsSettings.setEnabled(!v.on)}>
            <span>{w.alertsOn}</span><i />
          </button>
          <div className="news-arow"><span>{w.alertsMin}</span>
            <Segmenti<'8' | '9' | '10'> etichetta={w.alertsMin} valore={soglia} onChange={x => NewsAlertsSettings.setMinRelevance(Number(x))}
              opzioni={[{ id: '8', testo: '8+' }, { id: '9', testo: '9+' }, { id: '10', testo: '10' }]} />
          </div>
          <div className="news-arow"><span>{w.alertsScope}</span>
            <Segmenti<AlertScope> etichetta={w.alertsScope} valore={v.scope} onChange={x => NewsAlertsSettings.setScope(x)}
              opzioni={[{ id: 'portfolio', testo: w.scopePortfolio }, { id: 'favorites', testo: w.scopeFavorites }]} />
          </div>
          <button type="button" className="bbn-switch news-arow" role="switch" aria-checked={v.system} onClick={() => NewsAlertsSettings.setSystem(!v.system)}>
            <span>{w.alertsSystem}</span><i />
          </button>
          {v.system && v.perm === 'denied' && <p className="news-ahelp is-warn">{w.alertsDenied}</p>}
          <p className="news-ahelp">{w.alertsHelp}</p>
        </div>
      )}
    </div>
  );
}
