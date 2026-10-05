import { Fragment, useEffect, useRef, useState } from 'react';
import { ChevronDown, RefreshCw, Search, Star } from 'lucide-react';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { Segmenti } from '@/components/nuova/Card';
import { dayKeyFromTs, dayParts, hhmm, problemText, themeKeyOf, themeLabelOf, tsOf,
  type FeedItem, type NewsProblem, type SentBucket } from './calcoli';
import DettaglioNotizia, { IconaNotizia, MisuraRilevanza, PastigliaTono, type PesoTitolo } from './DettaglioNotizia';
import RitmoFlusso from './RitmoFlusso';
import RadarTemi from './RadarTemi';
import { parole } from './parole';

const fmt = (v: number, d = 1) => v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d });

export interface FiltriFlusso {
  period: string; themes: string[]; sent: SentBucket[]; rel: number; tickers: string[]; q: string; favOnly: boolean;
}
export interface AzioniFiltri {
  setPeriod: (v: string) => void; toggleTheme: (k: string) => void; toggleSent: (k: SentBucket) => void;
  setRel: (v: number) => void; toggleTicker: (t: string) => void; setQ: (v: string) => void;
  setFavOnly: (v: boolean) => void; reset: () => void;
}

/** Menu a tendina con caselle (Tema, Titolo, Tono). */
function MenuScelta<T extends string>({ etichetta, opzioni, scelti, onToggle, vuoto }: {
  etichetta: string;
  opzioni: Array<{ id: T; testo: string; n?: number }>;
  scelti: T[];
  onToggle: (id: T) => void;
  vuoto?: string;
}) {
  const [aperto, setAperto] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!aperto) return;
    const fuori = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setAperto(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setAperto(false); };
    document.addEventListener('mousedown', fuori); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', fuori); document.removeEventListener('keydown', esc); };
  }, [aperto]);
  const primo = scelti.length === 1 ? opzioni.find(o => o.id === scelti[0])?.testo.split(' · ')[0] : null;
  return (
    <div className="news-dd" ref={root}>
      <button type="button" className={'news-dd-btn' + (scelti.length ? ' is-set' : '')} aria-haspopup="true" aria-expanded={aperto} onClick={() => setAperto(a => !a)}>
        {scelti.length ? <>{etichetta} · <b>{primo ?? scelti.length}</b></> : etichetta}<ChevronDown size={14} aria-hidden="true" />
      </button>
      {aperto && (
        <div className="news-menu" role="group" aria-label={etichetta}>
          <div className="mh">{etichetta}</div>
          {opzioni.length === 0 && <p className="news-ahelp">{vuoto}</p>}
          {opzioni.map(o => (
            <label key={o.id}>
              <input type="checkbox" checked={scelti.includes(o.id)} onChange={() => onToggle(o.id)} />
              <span>{o.testo}</span>{o.n != null && <span className="c num">{o.n}</span>}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export default function VistaFlusso(p: {
  feed: FeedItem[];
  filtered: FeedItem[];
  feedLoad: boolean;
  feedErr: NewsProblem | null;
  refreshing: boolean;
  onRetry: () => void;
  onPoll: () => void;
  filtri: FiltriFlusso;
  azioni: AzioniFiltri;
  themeOptions: Array<{ key: string; count: number }>;
  tickerOptions: Array<{ ticker: string; count: number }>;
  favSet: Set<string>;
  pesi: Map<string, PesoTitolo>;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onToggleFav: (ticker: string, nome?: string) => void;
  favBusy: boolean;
  favError: string | null;
  nuove: Set<number>;
  ultimaVisita: number | null;
  aiSubito: boolean;
  onAiSubito: () => void;
}) {
  const w = parole();
  const { filtri: f, azioni: a } = p;
  const list = useRef<HTMLDivElement>(null);
  const selected = p.selectedId != null ? p.feed.find(n => n.id === p.selectedId) ?? null : null;
  const attivi = (f.period !== 'all' ? 1 : 0) + f.themes.length + f.sent.length + (f.rel > 0 ? 1 : 0) + f.tickers.length + (f.q ? 1 : 0) + (f.favOnly ? 1 : 0);
  // la riga scelta da radar o avviso entra nella parte visibile dell'elenco
  useEffect(() => {
    if (p.selectedId == null) return;
    const el = list.current?.querySelector<HTMLElement>(`[data-news-id="${p.selectedId}"]`);
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [p.selectedId]);

  const sorted = [...p.filtered].sort((x, y) => tsOf(y) - tsOf(x));
  const gruppi: Array<[string, FeedItem[]]> = [];
  for (const n of sorted) {
    const k = dayKeyFromTs(tsOf(n));
    const last = gruppi[gruppi.length - 1];
    if (last && last[0] === k) last[1].push(n); else gruppi.push([k, [n]]);
  }
  const conNuove = p.nuove.size > 0 && sorted.some(n => p.nuove.has(n.id));
  let separatore = false;

  const corpoElenco = () => {
    if (p.feedErr) return (
      <div className="news-empty"><p className="news-inline-err">{problemText(p.feedErr)}</p>
        <button type="button" className="bbn-btn" onClick={p.onRetry}><RefreshCw size={14} />{w.retry}</button></div>
    );
    if (p.feed.length === 0 && (p.feedLoad || p.refreshing)) return <div className="news-empty news-loading" aria-busy="true"><RefreshCw size={18} className="spin" aria-hidden="true" /><span>{w.loading}</span></div>;
    if (p.feed.length === 0) return (
      <div className="news-empty"><b>{w.feedEmpty}</b><span>{w.feedEmptyHint}</span>
        <button type="button" className="bbn-btn is-primary" onClick={p.onPoll} disabled={p.refreshing}><RefreshCw size={14} className={p.refreshing ? 'spin' : undefined} />{w.pollProviders}</button></div>
    );
    if (sorted.length === 0) return (
      <div className="news-empty"><Search size={26} aria-hidden="true" /><b>{w.noMatch}</b><span>{w.noMatchHint}</span>
        <button type="button" className="bbn-btn" onClick={a.reset}>{w.reset}</button></div>
    );
    return gruppi.map(([k, items]) => {
      const parti = dayParts(k, w.today, w.yesterday);
      return (
        <Fragment key={k}>
          <div className="news-day">{parti ? <>{parti.head}{parti.date && <span> · {parti.date}</span>}</> : w.undated}<span className="bbn-grow" /><span className="num">{items.length}</span></div>
          {items.map(n => {
            const nuova = p.nuove.has(n.id);
            const sep = conNuove && !nuova && !separatore && p.ultimaVisita != null;
            if (sep) separatore = true;
            const tk = (n.ticker_mentioned || '').toUpperCase();
            const peso = tk ? p.pesi.get(tk) : undefined;
            const rel = n.relevance ?? 0;
            return (
              <Fragment key={n.id}>
                {sep && <div className="news-seen" role="separator">{w.seen(hhmm(p.ultimaVisita!))}</div>}
                <button type="button" data-news-id={n.id} aria-current={n.id === p.selectedId}
                  className={'news-row' + (rel >= 8 ? ' is-hi' : '') + (nuova ? ' is-new' : '')}
                  onClick={() => p.onSelect(n.id)} title={n.title_original || n.title}>
                  <span className="tm num">{hhmm(tsOf(n))}</span>
                  <IconaNotizia item={n} nome={peso?.nome} />
                  <span className="tx">
                    <b>{n.title}</b>
                    <span>
                      {tk ? <><em>{tk}</em>{peso ? ` · ${w.ofPortfolio(fmt(peso.peso) + '%')}` : ''}</> : <em>{themeLabelOf(themeKeyOf(n))}</em>}
                      {' · '}{n.source || n.provider || '—'}
                      {tk && p.favSet.has(tk) && <Star size={11} className="news-fav-mark" aria-label={w.favorite} />}
                    </span>
                  </span>
                  <PastigliaTono sentiment={n.sentiment} />
                  <MisuraRilevanza r={n.relevance} />
                </button>
              </Fragment>
            );
          })}
        </Fragment>
      );
    });
  };

  return (
    <>
      <div className="news-overview">
        <RitmoFlusso feed={p.feed} />
        <RadarTemi feed={p.feed} favSet={p.favSet} themes={f.themes} onToggleTheme={a.toggleTheme}
          selectedId={p.selectedId} onSelect={p.onSelect} />
      </div>

      <div className="news-filters" role="search">
        <label className="news-search"><Search size={15} aria-hidden="true" />
          <input type="search" value={f.q} onChange={e => a.setQ(e.target.value)} placeholder={w.search} aria-label={w.search} />
        </label>
        <Segmenti etichetta={w.period} valore={f.period} onChange={a.setPeriod}
          opzioni={(['today', '24h', '3days', 'week', 'all'] as const).map(id => ({ id, testo: w.periods[id] }))} />
        <MenuScelta etichetta={w.theme} scelti={f.themes} onToggle={a.toggleTheme}
          opzioni={p.themeOptions.map(t => ({ id: t.key, testo: themeLabelOf(t.key), n: t.count }))} />
        <MenuScelta etichetta={w.ticker} scelti={f.tickers} onToggle={a.toggleTicker}
          opzioni={p.tickerOptions.slice(0, 40).map(t => ({ id: t.ticker, testo: p.pesi.get(t.ticker)?.nome ? `${t.ticker} · ${p.pesi.get(t.ticker)!.nome}` : t.ticker, n: t.count }))} />
        <MenuScelta<SentBucket> etichetta={w.tone} scelti={f.sent} onToggle={a.toggleSent}
          opzioni={[{ id: 'pos', testo: w.positive }, { id: 'neu', testo: w.neutral }, { id: 'neg', testo: w.negative }]} />
        <span title={w.relevanceHint}>
          <Segmenti<'0' | '5' | '8'> etichetta={w.relevance} valore={String(f.rel >= 8 ? 8 : f.rel >= 5 ? 5 : 0) as '0' | '5' | '8'}
            onChange={v => a.setRel(Number(v))}
            opzioni={[{ id: '0', testo: w.relAll }, { id: '5', testo: '5+' }, { id: '8', testo: '8+' }]} />
        </span>
        <button type="button" className="news-fav-btn" aria-pressed={f.favOnly} onClick={() => a.setFavOnly(!f.favOnly)}
          title={p.favSet.size === 0 ? w.favNone : undefined}>
          <Star size={14} aria-hidden="true" />{w.favOnly}
        </button>
        <span className="bbn-grow" />
        <span className="news-fcount num">{w.countOf(p.filtered.length, p.feed.length)}</span>
        {attivi > 0 && <button type="button" className="bbn-link" onClick={a.reset}>{w.reset}</button>}
      </div>

      <div className="news-flow">
        <section className="bbn-card news-list-card" aria-labelledby="news-list-h">
          <header className="bbn-card-head">
            <h2 id="news-list-h">{w.flow}</h2><span className="bbn-card-note">{w.flowNote}</span>
            <span className="bbn-grow" />
            {p.feedLoad && <RefreshCw size={14} className="spin news-muted" aria-label={w.loading} />}
          </header>
          <div className="news-list" ref={list}>{corpoElenco()}</div>
        </section>
        <DettaglioNotizia item={selected} feed={p.feed} pesi={p.pesi} favSet={p.favSet} onToggleFav={p.onToggleFav}
          favBusy={p.favBusy} favError={p.favError} onSelect={p.onSelect} aiSubito={p.aiSubito} onAiSubito={p.onAiSubito} />
      </div>
    </>
  );
}
