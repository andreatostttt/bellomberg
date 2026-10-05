import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { Fragment, useEffect, useState } from 'react';
import { frase } from '@/lib/frase';
import {
  LayoutDashboard, FileText, MessageSquare, CheckSquare, ClipboardList,
  Settings as SettingsIcon, TrendingUp, Activity, Zap, Cpu, Wallet, Newspaper, PieChart, LineChart, Waves, Radar, ArrowLeftRight, Globe, Star, FileSpreadsheet, FileSearch
} from 'lucide-react';
import { Bellomberg } from '@/lib/api';
import { version as appVersion } from '../../package.json';
import type { PortfolioSnapshot } from '@/lib/api';
import SettingsPanel from './SettingsPanel';
import { useLingua, useT } from '@/i18n/provider';
import { fmtDataBreve, fmtOra, fmtNum } from '@/lib/format';
import { startGlobalPriceRefresh } from '../lib/price-refresh';

import BadgeFiling from './BadgeFiling';
import { PAGE_DESTINATIONS, SETTINGS_DESTINATION, localizeDestination } from '../lib/navigation';
import AppearanceMenu from './AppearanceMenu';
import NewInterfaceBoundary from './NewInterfaceBoundary';
import './shell-modern.css';

const icons: Record<string, typeof LayoutDashboard> = {
  dashboard: LayoutDashboard, performance: LineChart, watchlist: Star,
  market: Globe, news: Newspaper, fundamentals: FileSpreadsheet, filing: FileSearch,
  factors: PieChart, montecarlo: Cpu, vol: Waves, edge: Radar,
  chat: MessageSquare, agents: Activity, progress: TrendingUp, memos: FileText,
  decisions: CheckSquare, trades: Wallet, movements: ArrowLeftRight, mandato: ClipboardList,
};
const KEY_SETTINGS = SETTINGS_DESTINATION.key;

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const i = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(i);
  }, [intervalMs]);
  return now;
}

// 206-UI: telemetria VERA (engine dal model, conteggio agenti, stato run) - ora inline nel footer
const fmtModel = (m: string) =>
  m.replace('claude-', '').replace(/-\d{8}$/, '').toUpperCase().replace(/-/g, ' ');

function useTelemetry() {
  const [tel, setTel] = useState<{ engine: string | null; engineMissing: boolean; total: number | null; done: number | null; running: boolean | null; ok: boolean }>(
    { engine: null, engineMissing: false, total: null, done: null, running: null, ok: true });
  useEffect(() => {
    let mounted = true;
    // Mai un 8 inventato: senza payload il conteggio è n.d. (regola 14/07)
    let total: number | null = null;
    let inFlight = false;
    Bellomberg.agentsList().then(r => {
      total = Array.isArray(r?.agents) ? r.agents.length : null;
      // ENGINE = motore del COMITATO (engines.committee_r1_r2, voce ponte 26/07);
      // agents[].model è il modello CHAT e qui mentirebbe. engines assente
      // (backend pre-riavvio) o import fallito = buco dichiarato, mai proxy.
      const eng = r?.engines;
      const m = eng?.committee_r1_r2 ? fmtModel(eng.committee_r1_r2)
        : eng?.committee_r1_r2_error ? 'ERR: ' + eng.committee_r1_r2_error
        : null;
      if (mounted) setTel(t => ({ ...t, engine: m, engineMissing: m === null,
        total: t.running === false ? total : t.total }));
    }).catch(() => {
      total = null;
      if (mounted) setTel(t => ({ ...t, engine: 'OFFLINE', engineMissing: false,
        total: t.running === false ? null : t.total }));
    });
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const live = await Bellomberg.agentsLive();
        if (!mounted) return;
        if (typeof live?.running !== 'boolean' || live.heartbeat === 'illeggibile') {
          setTel(t => ({ ...t, done: null, total: null, running: null, ok: true }));
        } else if (live.running) {
          const st = live.specialist_status;
          const valid = st != null && typeof st === 'object' && !Array.isArray(st)
            && Object.entries(st).every(([key, value]) => key.trim().length > 0
              && ['idle', 'running', 'done', 'error'].includes(value));
          const done = valid ? Object.values(st).filter(v => v === 'done').length : null;
          const tot = valid ? Object.keys(st).length : null;
          setTel(t => ({ ...t, done, total: tot, running: true, ok: true }));
        } else {
          setTel(t => ({ ...t, done: null, total, running: false, ok: true }));
        }
      } catch { if (mounted) setTel(t => ({ ...t, done: null, total: null, running: null, ok: false })); }
      finally { inFlight = false; }
    };
    poll();
    const i = setInterval(poll, 15000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return tel;
}

function useBackendHealth() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    let mounted = true;
    let inFlight = false;
    const ping = async () => {
      if (inFlight) return;
      inFlight = true;
      try { await Bellomberg.health(); if (mounted) setOk(true); }
      catch { if (mounted) setOk(false); }
      finally { inFlight = false; }
    };
    ping();
    const i = setInterval(ping, 15000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return ok;
}

function useFx() {
  const [fx, setFx] = useState<Record<string, number>>({});
  // Staleness DICHIARATA (regola 14/07): dopo il primo successo i fallimenti
  // erano muti e il nastro mostrava quote stantie sotto l'etichetta "FX 60s"
  const [fxAt, setFxAt] = useState<number | null>(null);
  const [fxErr, setFxErr] = useState(false);
  useEffect(() => {
    let mounted = true;
    let inFlight = false;
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const r = await Bellomberg.fx();
        if (mounted && r && r.rates && typeof r.rates === 'object') {
          const clean: Record<string, number> = {};
          for (const [k, v] of Object.entries(r.rates)) {
            if (typeof v === 'number' && isFinite(v)) clean[k] = v;
          }
          setFx(clean); setFxAt(Date.now()); setFxErr(false);
        }
      } catch { if (mounted) setFxErr(true); }
      finally { inFlight = false; }
    };
    tick();
    const i = setInterval(tick, 60000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return { fx, fxAt, fxErr };
}

function useGlobalPriceRefresh() {
  // partial = /prices/update ha dichiarato failed>0; undeclared = esito senza `failed` (mai verde)
  const [sync, setSync] = useState<{ state: 'sync' | 'ok' | 'partial' | 'undeclared' | 'error'; at: string | null; failed: number | null }>(
    { state: 'sync', at: null, failed: null });
  useEffect(() => {
    let mounted = true;
    const runner = startGlobalPriceRefresh<PortfolioSnapshot>({
      updatePrices: Bellomberg.updatePrices,
      readPortfolio: Bellomberg.portfolio,
      publish: (snapshot, outcome) => {
        if (!mounted) return;
        if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function'
            && typeof CustomEvent !== 'undefined') {
          window.dispatchEvent(new CustomEvent<PortfolioSnapshot>('bb:portfolio-snapshot', {
            detail: snapshot,
          }));
        }
        setSync({ state: outcome.failed === 0 ? 'ok' : outcome.failed == null ? 'undeclared' : 'partial',
          at: new Date().toISOString(), failed: outcome.failed });
      },
      isHidden: () => typeof document !== 'undefined' && Boolean(document.hidden),
      schedule: (callback, milliseconds) => setInterval(callback, milliseconds),
      cancel: handle => clearInterval(handle as ReturnType<typeof setInterval>),
      onError: () => { if (mounted) setSync(s => ({ ...s, state: 'error' })); },
    });
    const onVisibility = () => {
      if (!document.hidden) void runner.refresh();
    };
    const canListenVisibility = typeof document !== 'undefined'
      && typeof document.addEventListener === 'function';
    if (canListenVisibility) document.addEventListener('visibilitychange', onVisibility);
    return () => {
      mounted = false;
      if (canListenVisibility && typeof document.removeEventListener === 'function') {
        document.removeEventListener('visibilitychange', onVisibility);
      }
      runner.stop();
    };
  }, []);
  return sync;
}

/* Il pallino d'allarme sulla rotellina: e' la ragione per cui togliere
   le impostazioni dalle pagine e' un GUADAGNO e non una perdita. Prima
   il PM doveva aprire F11 per scoprire che il salvataggio notturno del
   database era in errore — e la pagina non glielo diceva comunque,
   perche' `LastTaskResult` non era reso da nessuna parte. Ora l'allarme
   lo raggiunge da qualunque pagina.

   Un lavoro SPENTO DI PROPOSITO non e' un guasto: il Consigliere e'
   disattivato dal 03/07 e porta ancora il suo vecchio esito. Contarlo
   qui accenderebbe il pallino per sempre, e un allarme sempre acceso
   e' un allarme spento. */
function useTaskAlarm() {
  // null = NON MISURATO (regola 14/07). Spegnere il pallino su un fetch
  // fallito direbbe "tutto a posto" senza saperlo: e' il fallback muto
  // proprio sull'oggetto che deve avvisare. Tre stati, non due.
  const [guasti, setGuasti] = useState<number | null>(null);
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const r = await Bellomberg.scheduledTasks();
        if (!mounted) return;
        setGuasti((r.tasks || []).filter((t: any) =>
          String(t.State).toLowerCase() !== 'disabled' &&
          t.LastTaskResult !== 0 && t.LastTaskResult != null).length);
      } catch {
        if (mounted) setGuasti(null);   // buco DICHIARATO, non "zero guasti"
      }
    };
    tick();
    const i = setInterval(tick, 60000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return guasti;
}

// stato della sincronizzazione prezzi -> chiave del catalogo (prima: l'enum interno in maiuscolo,
// SYNC/ERROR in inglese anche in italiano). API PING/LIVE/DOWN, ONLINE/OFFLINE, RUN, STALE, CONFIG
// restano sigle da terminale: sono le etichette neutre dichiarate in tests/i18n/dizionari.test.cjs.
// Integrazione G9b+G9c: ogni stato del pallino prezzi ha la sua parola (partial/undeclared di G9b
// non ricadono su «ok»); `satisfies` fa fallire tsc se uno stato nuovo resta senza chiave.
const PX_STATE = {
  sync: 'shell.px_sync', ok: 'shell.px_ok', partial: 'shell.px_partial', undeclared: 'shell.px_undeclared', error: 'shell.px_error',
} as const satisfies Record<'sync' | 'ok' | 'partial' | 'undeclared' | 'error', string>;

function marketStatus(now: Date) {
  const dayUTC = now.getUTCDay();
  const minUTC = now.getUTCHours() * 60 + now.getUTCMinutes();
  const weekday = dayUTC >= 1 && dayUTC <= 5;
  const nyse = weekday && minUTC >= 14*60+30 && minUTC < 21*60;
  const lse  = weekday && minUTC >= 8*60 && minUTC < 16*60+30;
  const eu   = weekday && minUTC >= 8*60 && minUTC < 16*60+30;
  const hk   = weekday && minUTC >= 1*60+30 && minUTC < 8*60;
  return [
    { label: 'NY', live: nyse, color: nyse ? '#21e0a0' : '#3d4763' },
    { label: 'LN', live: lse,  color: lse  ? '#21e0a0' : '#3d4763' },
    { label: 'MI', live: eu,   color: eu   ? '#21e0a0' : '#3d4763' },
    { label: 'HK', live: hk,   color: hk   ? '#21e0a0' : '#3d4763' },
  ];
}

function ShellSurface({ render }: { render: () => React.ReactNode }) {
  return <>{render()}</>;
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const language = useLingua(), t = useT();
  const nav = PAGE_DESTINATIONS.map(entry => ({ ...localizeDestination(entry, language), icon: icons[entry.id] }));
  const now = useNow();
  const health = useBackendHealth();
  const { fx, fxAt, fxErr } = useFx();
  const priceSync = useGlobalPriceRefresh();
  const tel = useTelemetry();
  const guasti = useTaskAlarm();
  const [cfgOpen, setCfgOpen] = useState(false);
  const markets = marketStatus(now);
  const navigate = useNavigate();
  const location = useLocation();
  const current = nav.find(n => location.pathname.startsWith(n.to));

  // Function keys use the same registry as the menu and command palette.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      if (!/^F\d+$/.test(e.key)) return;
      if (e.key === KEY_SETTINGS) {
        e.preventDefault();
        setCfgOpen(o => !o);
        return;
      }
      const target = nav.find(n => n.key === e.key);
      if (target) {
        e.preventDefault();
        navigate(target.to);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [navigate]);

  // la palette (CTRL+K) apre lo stesso pannello: un solo posto, due strade
  useEffect(() => {
    const onOpen = () => setCfgOpen(true);
    window.addEventListener('bb:settings', onOpen as EventListener);
    return () => window.removeEventListener('bb:settings', onOpen as EventListener);
  }, []);

  /* Col pannello aperto tutto cio' che sta DIETRO e' inerte: non si legge
     (c'e' il velo sopra) e non si deve poter navigare col tab. La fascia in
     alto NO: ci vive la rotellina, che e' anche il bottone per richiudere.
     Il marcatore serve anche al cancello (qa_app.py), che altrimenti
     misurerebbe il contrasto della pagina dietro il velo e riferirebbe
     difetti che non esistono. */
  const dietro = cfgOpen ? { 'data-inerte': '', 'aria-hidden': true } : {};

  const timeStr = fmtOra(now, language);
  const dateStr = fmtDataBreve(now, language);
  const tzOffset = now.getTimezoneOffset();

  const settingsTitle = guasti == null
    ? t('shell.settings_unknown', { key: KEY_SETTINGS })
    : guasti > 0
      ? t('shell.settings_failed', { key: KEY_SETTINGS, n: guasti })
      : `${t('shell.settings')} (${KEY_SETTINGS})`;

  return (
    <div className="bb-layout-frame" data-mode="modern">
      <AppearanceMenu />
      <NewInterfaceBoundary language={language}>
        <ShellSurface render={() => (
        <div className="bb-modern-shell">
          <header className="bb-modern-header">
              <>
                <div className="bb-modern-brand">
                  <strong>{current?.label ?? nav[0]?.label}</strong>
                  <span>{current?.group ?? nav[0]?.group}</span>
                </div>
                <div className="bb-modern-markets" aria-label={t('shell.markets')}>
                  {markets.map(m => (
                    <span key={m.label} className={'bb-modern-market' + (m.live ? ' is-live' : '')} title={`${m.label} ${m.live ? t('shell.market_open') : t('shell.market_closed')}`}>
                      <i aria-hidden="true" />{m.label}
                    </span>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => window.dispatchEvent(new Event('bb:palette'))}
                  className="bb-modern-search"
                  title={t('shell.palette')}
                >
                  <span aria-hidden="true">⌕</span>
                  <span>{frase(t('shell.search'))}</span>
                  <kbd>CTRL+K</kbd>
                </button>
                {/* Un solo pallino: verde se backend e prezzi sono a posto, rosso se uno dei due
                    è giù, giallo mentre si verifica. Il dettaglio sta nel tooltip. */}
                <div className="bb-modern-statuses">
                  {(() => {
                    const stato = health === false || priceSync.state === 'error' ? 'is-bad'
                      : health && priceSync.state === 'ok' ? 'is-good' : 'is-pending';
                    const api = `API ${health === null ? 'PING' : health ? 'LIVE' : 'DOWN'}`;
                    const esitoPx = priceSync.state === 'partial' ? ` · ${t('shell.prices_failed', { n: String(priceSync.failed) })}`
                      : priceSync.state === 'undeclared' ? ` · ${t('shell.prices_undeclared')}` : '';
                    const px = `PX ${t(PX_STATE[priceSync.state])}${esitoPx}${priceSync.at ? ` · ${t('shell.last_snapshot')} ${priceSync.at}` : ''}`;
                    return <span className={'bb-modern-status bb-modern-status-dot ' + stato} role="status" title={`${api}\n${px}`}
                      aria-label={`${t('shell.data_status')}: ${api}, ${px}`}><i aria-hidden="true" /></span>;
                  })()}
                </div>
                <div className="bb-modern-clock" title={`UTC${tzOffset <= 0 ? '+' : '-'}${Math.abs(Math.round(tzOffset / 60))}`}>
                  <span>{dateStr}</span><strong>{timeStr}</strong>
                </div>
                <button
                  type="button"
                  onClick={() => setCfgOpen(o => !o)}
                  title={settingsTitle}
                  aria-label={t('shell.settings')}
                  aria-expanded={cfgOpen}
                  className="bb-modern-settings"
                >
                  <SettingsIcon size={15} />
                  {guasti == null ? <span className="bb-settings-alarm" aria-label={t('shell.settings_unknown', { key: KEY_SETTINGS })} />
                    : guasti > 0 ? <span className="bb-settings-alarm is-error" aria-label={t('shell.settings_failed', { key: KEY_SETTINGS, n: guasti })} /> : null}
                </button>
              </>
          </header>

          <nav {...dietro} aria-label={t('shell.modules')} className="bb-modern-sidebar">
              <>
                <div className="bb-modern-sidebar-brand"><div><strong>BELLOMBERG</strong></div></div>
                <div className="bb-modern-nav-scroll">
                  {nav.map(({ to, label, group, icon: Icon, key }, index) => (
                    <Fragment key={to}>
                      {(index === 0 || nav[index - 1].group !== group) && <div className="bb-modern-nav-group">{group}</div>}
                      <NavLink
                        to={to}
                        title={`${key} · ${label} · ${group}`}
                        aria-label={`${key} · ${label} · ${group}`}
                        className={({ isActive }) => 'bb-modern-nav-link' + (isActive ? ' is-active' : '')}
                      >
                        <Icon size={13} aria-hidden="true" />
                        <span>{label}</span>
                        {to === '/filing' && <BadgeFiling etichetta={n => t('shell.filing_new', { n })} />}
                      </NavLink>
                    </Fragment>
                  ))}
                </div>
                <button onClick={() => setCfgOpen(true)} title={`${KEY_SETTINGS} · ${t('shell.settings')}`}
                  aria-label={`${KEY_SETTINGS} · ${t('shell.settings')}`} className="bb-modern-config">
                  <SettingsIcon size={14} aria-hidden="true" /><span>CONFIG</span><kbd>{KEY_SETTINGS}</kbd>
                  {guasti == null ? <i className="bb-settings-alarm" aria-label={t('shell.settings_unknown', { key: KEY_SETTINGS })} />
                    : guasti > 0 ? <i className="bb-settings-alarm is-error" aria-label={t('shell.settings_failed', { key: KEY_SETTINGS, n: guasti })} /> : null}
                </button>
              </>
          </nav>

          {/* I cambi servono a chi guarda i mercati: la fascia vive solo nella pagina Mercati. */}
          {location.pathname.startsWith('/market') && <div {...dietro} className="bb-modern-fx">
              <>
                <span className="bb-modern-fx-title">FX / EUR</span>
                <div className="bb-modern-fx-rates">
                  {Object.entries(fx).length === 0 ? (
                    <span className={fxErr ? 'bb-modern-fx-error' : ''}>{fxErr ? t('shell.fx_error') : t('shell.fx_waiting')}</span>
                  ) : Object.entries(fx).map(([currency, rate]) => {
                    const safe = typeof rate === 'number' && isFinite(rate);
                    return <span key={currency} className="bb-modern-fx-rate"><span>{currency}/EUR</span><strong>{safe ? fmtNum(rate, currency === 'GBX' ? 5 : 4) : '—'}</strong></span>;
                  })}
                </div>
                {fxErr && fxAt != null
                  ? <span className="bb-modern-fx-meta is-stale" title={t('shell.fx_stale')}>STALE {Math.max(1, Math.round((now.getTime() - fxAt) / 60000))}M</span>
                  : <span className="bb-modern-fx-meta">FX 60s</span>}
              </>
          </div>}

          <main {...dietro} className={location.pathname === '/dashboard' ? 'bb-modern-main' : 'bb-modern-main bb-modern-main-classic-content'}>
            <div className="relative p-4 h-full overflow-y-auto">{children}</div>
          </main>

          <footer {...dietro} className="bb-modern-footer">
              <>
                {/* the footer identifies the actual frontend version (Classica showed "v{version} OBSIDIAN"; release test f13) */}
                <span className="bb-modern-footer-brand">BELLOMBERG · v{appVersion} OBSIDIAN</span>
                <span>{t('shell.engine')} <strong>{tel.engineMissing ? t('shell.engine_restart') : tel.engine ?? t('shell.unavailable')}</strong></span>
                <span>{t('shell.agents')} <strong>{tel.running
                  ? `${tel.done ?? t('shell.unavailable')}/${tel.total ?? t('shell.unavailable')} RUN`
                  : tel.total == null ? t('shell.unavailable') : `${tel.total} ${t('shell.ready')}`}</strong></span>
                <span className={tel.running ? 'is-live' : tel.ok ? 'is-warning' : 'is-error'}>
                  {tel.running ? <><Zap size={10} aria-hidden="true" /> {t('shell.live_run')}</>
                    : tel.ok && tel.running === null ? t('shell.unavailable')
                      : tel.ok ? <><Zap size={10} aria-hidden="true" /> ONLINE</> : 'OFFLINE'}
                </span>
                <span>{t('shell.session')} {now.toISOString().slice(0, 10)}</span>
                <span className="bb-modern-footer-workspace">{t('shell.workspace')}</span>
              </>
          </footer>
        </div>
        )} />
      </NewInterfaceBoundary>
      {/* macOS/Electron: the header is a window-drag region and Electron applies
          app regions in document order, so the earlier no-drag selector above was
          swallowed by the later header drag. This later, click-through no-drag
          area under the Appearance button keeps Chiaro/Scuro clickable. */}
      <div className="bb-interface-mode-drag-exclusion" aria-hidden="true" />
      <SettingsPanel open={cfgOpen} onClose={() => setCfgOpen(false)} />
    </div>
  );
}
