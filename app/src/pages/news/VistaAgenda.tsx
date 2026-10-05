import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Building2, CalendarDays, ExternalLink, FileText, Globe, RefreshCw } from 'lucide-react';
import { externalWebUrl } from '../../../electron/security';
import type { BriefingData, CorporateEvent, EconomicEvent, GlobalNewsItem, MacroNewsItem } from '@/lib/api';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import { economicNumber, frase, hhmm, originalLanguage, problemText, tsOf, type NewsProblem } from './calcoli';
import type { PesoTitolo } from './DettaglioNotizia';
import { parole } from './parole';

const fmt = (v: number, d = 1) => v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d });
// cambi e rendimenti sotto 10 con fino a 4 decimali (EUR/USD 1,1724), indici senza decimali
const prezzo = (v: number) => v.toLocaleString(localeDi(linguaCorrente()),
  v >= 1000 ? { maximumFractionDigits: 0 } : v < 10 ? { minimumFractionDigits: 2, maximumFractionDigits: 4 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const dataLunga = (iso: string, conGiorno = true) => {
  const d = new Date(iso.length <= 10 ? `${iso}T12:00:00` : iso);
  if (!isFinite(d.getTime())) return iso;
  const s = d.toLocaleDateString(localeDi(linguaCorrente()), conGiorno ? { weekday: 'long', day: 'numeric', month: 'long' } : { day: 'numeric', month: 'short' });
  return s.charAt(0).toUpperCase() + s.slice(1);
};

import { CHIP_ALTRI, COUNTRIES_AVAILABLE } from '@/lib/calendario-paesi';
export { COUNTRIES_AVAILABLE };
export const MACRO_CATEGORIES = ['rates', 'inflation', 'geopolitics', 'politics', 'em', 'commodities', 'crypto', 'corporate'];

function Stato({ errore, carica, vuoto, testoCarica, testoVuoto }: {
  errore: NewsProblem | null; carica: boolean; vuoto: boolean; testoCarica: string; testoVuoto: string;
}) {
  if (errore) return <p className="news-inline-err">{problemText(errore)}</p>;
  if (vuoto && carica) return <p className="news-muted-p">{testoCarica}</p>;
  if (vuoto) return <p className="news-muted-p">{testoVuoto}</p>;
  return null;
}

function RigaLink({ ora, titolo, sotto, url }: { ora: string; titolo: string; sotto: string; url: string | null }) {
  const href = externalWebUrl(url || '') || undefined;
  const corpo = <><span className="tm num">{ora}</span><span className="tx"><b>{titolo}</b><span>{sotto}</span></span>{href && <ExternalLink size={13} aria-hidden="true" />}</>;
  return href
    ? <a className="news-crow" href={href} target="_blank" rel="noopener noreferrer">{corpo}</a>
    : <div className="news-crow">{corpo}</div>;
}

export default function VistaAgenda(p: {
  briefing: BriefingData | null; briefingLoad: boolean; briefingErr: NewsProblem | null; onRewrite: () => void;
  econ: EconomicEvent[]; econLoad: boolean; econErr: NewsProblem | null; countryCounts: Record<string, number>;
  econDate: string; setEconDate: (v: string) => void;
  econImportance: number[]; setEconImportance: (v: number[]) => void;
  econCountries: string[]; setEconCountries: (v: string[]) => void;
  corp: CorporateEvent[]; corpLoad: boolean; corpErr: NewsProblem | null; corpDate: string; setCorpDate: (v: string) => void;
  macro: MacroNewsItem[]; macroLoad: boolean; macroErr: NewsProblem | null; macroDate: string; setMacroDate: (v: string) => void;
  macroCategories: string[]; setMacroCategories: (v: string[]) => void;
  global: GlobalNewsItem[]; globalLoad: boolean; globalErr: NewsProblem | null; globalDate: string; setGlobalDate: (v: string) => void;
  pesi: Map<string, PesoTitolo>;
}) {
  const w = parole();
  const [ctx, setCtx] = useState<'macro' | 'global'>('macro');
  const b = p.briefing;
  const unreadable = b?.error_code === 'briefing_cache_unreadable';
  const gen = b?.generated_at ? new Date(b.generated_at).getTime() : NaN;
  const indicatori = Object.entries(b?.macro_indicators || {}).slice(0, 8);
  const tokens = (b?.tokens_in ?? 0) + (b?.tokens_out ?? 0);
  // etichette del catalogo in maiuscolo (testata Obsidian) rese in sentence case
  const eta = b?.age_minutes != null
    ? (b.age_minutes < 60 ? tr('newsdesk.s038', { a: b.age_minutes }) : tr('newsdesk.s039', { a: Math.floor(b.age_minutes / 60) }))
    : tr('newsdesk.ageUnknown');
  const imp = p.econImportance.length === 0 ? 'all' : p.econImportance.includes(3) ? 'medium' : 'high';
  const byDate = new Map<string, EconomicEvent[]>();
  p.econ.forEach(e => { const a = byDate.get(e.date); if (a) a.push(e); else byDate.set(e.date, [e]); });
  const toggle = (arr: string[], v: string, set: (x: string[]) => void) => set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]);
  const periodiNews = (['today', '24h', 'week', 'all'] as const).map(id => ({ id, testo: w.periods[id] }));

  return (
    <div className="news-agenda">
      <section className="bbn-card news-brief" aria-labelledby="news-brief-h">
        <header className="bbn-card-head">
          <span className="news-ci" aria-hidden="true"><FileText size={15} /></span>
          <h2 id="news-brief-h">{w.briefing}</h2>
          {b?.slot_label && <span className="bbn-pill news-pill-acc">{b.slot_label}</span>}
          {isFinite(gen) && <span className="bbn-card-note">{w.briefingAt(hhmm(gen))} · {eta}{b?.lookback_hours ? ` · ${w.briefingHours(b.lookback_hours)}` : ''}</span>}
          {unreadable && <span className="bbn-warn-pill">{frase(tr('newsdesk.unreadableCache'))}</span>}
          {b?.stale && !unreadable && <span className="bbn-warn-pill">{w.briefingStale}</span>}
          <span className="bbn-grow" />
          <button type="button" className="bbn-btn" onClick={p.onRewrite} disabled={p.briefingLoad}>
            <RefreshCw size={14} className={p.briefingLoad ? 'spin' : undefined} />{p.briefingLoad ? w.briefingWriting : w.briefingRewrite}
          </button>
        </header>
        <div className="news-brief-body">
          {indicatori.length > 0 && (
            <div className="news-mtiles">
              {indicatori.map(([k, v]) => (
                <div key={k} className="news-tile">
                  <span className="k">{k}</span>
                  <span className="v num">{v.price != null ? prezzo(v.price) : '—'}</span>
                  <span className={'s num' + (v.change_pct == null ? '' : v.change_pct >= 0 ? ' is-up' : ' is-down')}>
                    {v.change_pct == null ? '—' : `${v.change_pct > 0 ? '+' : ''}${fmt(v.change_pct, 2)}%`}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="news-md">
            {p.briefingErr ? <p className="news-inline-err">{problemText(p.briefingErr)}</p>
              : p.briefingLoad && !b ? <p className="news-muted-p">{w.briefingLoading}</p>
              : unreadable ? <><p className="news-inline-err">{b?.error || frase(tr('newsdesk.unreadableCache'))}</p><ReactMarkdown remarkPlugins={[remarkGfm]}>{b?.briefing_md || ''}</ReactMarkdown></>
              : <ReactMarkdown remarkPlugins={[remarkGfm]}>{b?.briefing_md || w.briefingEmpty}</ReactMarkdown>}
          </div>
          {b?.generated_at && (
            <div className="news-brief-foot">
              {b.news_count != null && <span>{w.briefingNews(b.news_count)}</span>}
              {tokens > 0 && <span>{w.briefingTokens(tokens.toLocaleString(localeDi(linguaCorrente())))}</span>}
              {b.briefing_md && <span>{tr('newsdesk.originalBriefing', { language: originalLanguage(b.language) })}</span>}
            </div>
          )}
        </div>
      </section>

      <section className="bbn-card news-cal" aria-labelledby="news-cal-h">
        <header className="bbn-card-head">
          <span className="news-ci" aria-hidden="true"><CalendarDays size={15} /></span>
          <h2 id="news-cal-h">{w.calendar}</h2>
          <span className="bbn-card-note">{w.events(p.econ.length)}</span>
          {p.econLoad && <RefreshCw size={14} className="spin news-muted" aria-label={w.calLoading} />}
        </header>
        <div className="news-cal-filters">
          <Segmenti etichetta={w.period} valore={p.econDate} onChange={p.setEconDate}
            opzioni={(['today', 'tomorrow', 'week', '2weeks'] as const).map(id => ({ id, testo: w.calPeriods[id] }))} />
          <Segmenti<'high' | 'medium' | 'all'> etichetta={w.importance(5)} valore={imp}
            onChange={v => p.setEconImportance(v === 'high' ? [4, 5] : v === 'medium' ? [3, 4, 5] : [])}
            opzioni={[{ id: 'high', testo: w.impHigh }, { id: 'medium', testo: w.impMedium }, { id: 'all', testo: w.impAll }]} />
        </div>
        <div className="news-cal-filters">
          {COUNTRIES_AVAILABLE.map(c => (
            <button key={c} type="button" className="news-cchip" aria-pressed={p.econCountries.includes(c)}
              onClick={() => toggle(p.econCountries, c, p.setEconCountries)}>
              {c === CHIP_ALTRI ? w.calOther : c}{p.countryCounts[c] ? <span className="num"> {p.countryCounts[c]}</span> : null}
            </button>
          ))}
        </div>
        <div className="news-cal-scroll">
          <div className="news-chead"><span>{w.colTime}</span><span>{w.colArea}</span><span>{w.colEvent}</span><span /><span>{w.colPrev}</span><span>{w.colEst}</span><span>{w.colAct}</span></div>
          <Stato errore={p.econErr} carica={p.econLoad} vuoto={p.econ.length === 0} testoCarica={tr('newsdesk.calendarLoading')} testoVuoto={`${w.calEmpty}. ${w.calEmptyHint}`} />
          {!p.econErr && Array.from(byDate.entries()).map(([date, events]) => (
            <div key={date}>
              <div className="news-cday">{dataLunga(date)}</div>
              {events.map((e, i) => {
                const beat = e.actual != null && e.estimate != null && Number.isFinite(Number(e.actual)) && Number.isFinite(Number(e.estimate))
                  ? (Number(e.actual) > Number(e.estimate) ? 'up' : Number(e.actual) < Number(e.estimate) ? 'dn' : '') : '';
                const tk = (e.ticker || '').toUpperCase();
                return (
                  <div key={i} className={'news-cev' + (tk ? ' is-earn' : '')}>
                    <span className="tm num">{e.time || '—'}</span>
                    <span className="cc" title={(e.country || '').trim() === '?' ? w.countryUnknown : undefined}>{e.country}</span>
                    <span className="ti"><b>{e.title}</b>
                      {(e.date_estimated || tk) && <span>{[e.date_estimated ? w.dateEstimated : '', tk && e.source ? w.calSource(e.source) : '', tk ? `${tk} · ${w.inPortfolio}` : ''].filter(Boolean).join(' · ')}</span>}
                    </span>
                    <span className="imp" title={w.importance(e.importance)} aria-label={w.importance(e.importance)}>
                      {[1, 2, 3, 4, 5].map(x => <i key={x} className={e.importance >= x ? 'on' : undefined} />)}
                    </span>
                    <span className="num est">{e.previous != null ? `${economicNumber(e.previous)}${e.unit || ''}` : '—'}</span>
                    <span className="num">{e.estimate != null ? `${economicNumber(e.estimate)}${e.unit || ''}` : '—'}</span>
                    <span className={'num act' + (beat ? ` is-${beat}` : '')}>{e.actual != null ? `${economicNumber(e.actual)}${e.unit || ''}` : '—'}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </section>

      <section className="bbn-card news-corp" aria-labelledby="news-corp-h">
        <header className="bbn-card-head">
          <span className="news-ci" aria-hidden="true"><Building2 size={15} /></span>
          <h2 id="news-corp-h">{w.corporate}</h2>
          <span className="bbn-card-note">{w.corporateNote}</span>
          <span className="bbn-grow" />
          {p.corpLoad && <RefreshCw size={14} className="spin news-muted" aria-label={w.corporateLoading} />}
        </header>
        <div className="news-cal-filters"><Segmenti etichetta={w.period} valore={p.corpDate} onChange={p.setCorpDate} opzioni={periodiNews} /></div>
        <div className="news-side-scroll">
          <Stato errore={p.corpErr} carica={p.corpLoad} vuoto={p.corp.length === 0} testoCarica={w.corporateLoading} testoVuoto={w.corporateEmpty} />
          {!p.corpErr && p.corp.map((e, i) => {
            const tk = (e.ticker_mentioned || '').toUpperCase();
            const href = externalWebUrl(e.url || '') || undefined;
            const ts = tsOf(e);
            const origine = e.title_origin === 'bellomberg' && e.presentation_languages?.includes(linguaCorrente())
              ? tr('newsdesk.currentWording', { language: originalLanguage(linguaCorrente()) })
              : e.source_type === 'sec' ? tr('newsdesk.originalBellomberg', { language: originalLanguage(null) }) : tr('newsdesk.originalSource');
            const corpo = <>
              {tk ? <IconaTitolo ticker={tk} nome={p.pesi.get(tk)?.nome} dimensione="sm" /> : <span className="bbn-ico is-sm news-ico-ctx"><Building2 size={15} /></span>}
              <span className="nm"><b>{e.title}</b>
                {e.snippet && <span className="ex">{e.snippet}</span>}
                <span>{[tk, e.event_type, e.provider].filter(Boolean).join(' · ')}</span>
                <span className="og">{origine}{e.snippet && e.snippet_origin === 'source' ? ` · ${tr('newsdesk.sourceExcerpt')}` : ''}</span>
              </span>
              <span className="when"><b>{ts ? dataLunga(new Date(ts).toISOString(), false) : '—'}</b><span>{ts ? hhmm(ts) : ''}</span></span>
            </>;
            return href
              ? <a key={i} className="news-erow" href={href} target="_blank" rel="noopener noreferrer">{corpo}</a>
              : <div key={i} className="news-erow">{corpo}</div>;
          })}
        </div>
      </section>

      <section className="bbn-card news-ctx" aria-labelledby="news-ctx-h">
        <header className="bbn-card-head">
          <span className="news-ci" aria-hidden="true"><Globe size={15} /></span>
          <h2 id="news-ctx-h">{w.context}</h2>
          <span className="bbn-grow" />
          <Segmenti<'macro' | 'global'> etichetta={w.context} valore={ctx} onChange={setCtx}
            opzioni={[{ id: 'macro', testo: w.ctxMacro }, { id: 'global', testo: w.ctxGlobal }]} />
        </header>
        <div className="news-cal-filters">
          {ctx === 'macro'
            ? <Segmenti etichetta={w.period} valore={p.macroDate} onChange={p.setMacroDate} opzioni={periodiNews} />
            : <Segmenti etichetta={w.period} valore={p.globalDate} onChange={p.setGlobalDate} opzioni={periodiNews} />}
        </div>
        {ctx === 'macro' && (
          <div className="news-cal-filters">
            {MACRO_CATEGORIES.map(c => (
              <button key={c} type="button" className="news-cchip" aria-pressed={p.macroCategories.includes(c)}
                onClick={() => toggle(p.macroCategories, c, p.setMacroCategories)}>{w.categories[c]}</button>
            ))}
          </div>
        )}
        <div className="news-side-scroll">
          {ctx === 'macro' ? <>
            <Stato errore={p.macroErr} carica={p.macroLoad} vuoto={p.macro.length === 0} testoCarica={w.ctxLoading} testoVuoto={w.ctxEmpty} />
            {!p.macroErr && p.macro.map((n, i) => (
              <RigaLink key={i} ora={hhmm(tsOf(n))} titolo={n.title} url={n.url}
                sotto={[n.topic_label || (n.topic_category ? w.categories[n.topic_category] : ''), (n.tickers_affected || []).slice(0, 3).join(', '), n.source || n.provider].filter(Boolean).join(' · ')} />
            ))}
          </> : <>
            <Stato errore={p.globalErr} carica={p.globalLoad} vuoto={p.global.length === 0} testoCarica={w.ctxLoading} testoVuoto={w.ctxEmpty} />
            {!p.globalErr && p.global.map((n, i) => (
              <RigaLink key={i} ora={hhmm(tsOf(n))} titolo={n.title} url={n.url} sotto={[n.source, n.provider].filter(Boolean).join(' · ')} />
            ))}
          </>}
        </div>
      </section>
    </div>
  );
}
