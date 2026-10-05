import { useEffect, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, Copy, ExternalLink, Globe, Minus, RefreshCw, Sparkles, Star } from 'lucide-react';
import { externalWebUrl } from '../../../electron/security';
import { Bellomberg, type ArticleSummaryResult } from '@/lib/api';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import { dayKeyFromTs, errorDetail, hhmm, originalLanguage, sentBucket, themeKeyOf, themeLabelOf, tsOf, type FeedItem } from './calcoli';
import { parole } from './parole';

export interface PesoTitolo { peso: number; nome: string }

const fmt = (v: number, d = 1) => v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d });

export function IconaNotizia({ item, nome, dimensione = 'sm' }: { item: FeedItem; nome?: string; dimensione?: 'sm' | 'md' }) {
  const tk = (item.ticker_mentioned || '').toUpperCase();
  if (tk) return <IconaTitolo ticker={tk} nome={nome} dimensione={dimensione} />;
  return <span className={`bbn-ico is-${dimensione} news-ico-ctx`} aria-hidden="true"><Globe size={dimensione === 'md' ? 18 : 15} /></span>;
}

export function PastigliaTono({ sentiment, prefisso = false }: { sentiment: string | null | undefined; prefisso?: boolean }) {
  const w = parole();
  const b = sentBucket(sentiment);
  // singolare per la pastiglia della riga: «Positiva», «Neutra», «Negativa»
  const sing = b === 'pos' ? w.tonePill_pos : b === 'neg' ? w.tonePill_neg : w.tonePill_neutral;
  const Icon = b === 'pos' ? ArrowUpRight : b === 'neg' ? ArrowDownRight : Minus;
  return (
    <span className={'bbn-pill ' + (b === 'pos' ? 'is-su' : b === 'neg' ? 'is-giu' : 'is-piatto')}>
      <Icon size={13} aria-hidden="true" />{prefisso ? w.toneLabel(sing.toLowerCase()) : sing}
    </span>
  );
}

export function MisuraRilevanza({ r }: { r: number | null | undefined }) {
  const w = parole();
  const v = r ?? 0;
  return (
    <span className={'news-rel' + (v >= 8 ? ' is-hi' : '')} title={w.relOf(v)} aria-label={w.relOf(v)}>
      <b className="num">{v > 0 ? v : '–'}</b>
      <span className="m" aria-hidden="true">{[2, 4, 6, 8, 10].map(x => <i key={x} className={v >= x - 1 ? 'on' : undefined} />)}</span>
    </span>
  );
}

/* ── riassunto AI ──────────────────────────────────────────────────
   Solo su richiesta: all'apertura si legge la cache (GET, nessun costo);
   il POST parte soltanto dal pulsante. Le richieste in volo restano qui
   anche se si cambia notizia, così tornando si vede che stanno lavorando. */
type StatoAI = { s: 'checking' } | { s: 'idle' } | { s: 'running' } | { s: 'result'; r: ArticleSummaryResult } | { s: 'failed'; detail: string };
const inVolo = new Map<string, Promise<ArticleSummaryResult>>();

function RiassuntoAI({ item, subito, onSubito }: { item: FeedItem; subito: boolean; onSubito: () => void }) {
  const w = parole();
  const id = item.id, chiave = `${id}:${linguaCorrente()}`;
  const fonte = item.source || item.provider || '—';
  const [st, setSt] = useState<StatoAI>({ s: 'checking' });
  useEffect(() => {
    let vivo = true;
    const volo = inVolo.get(chiave);
    if (volo) {
      setSt({ s: 'running' });
      volo.then(r => { if (vivo) setSt({ s: 'result', r }); }, e => { if (vivo) setSt({ s: 'failed', detail: errorDetail(e) || '' }); });
    } else {
      Bellomberg.newsArticleSummary(id)
        .then(r => { if (vivo) setSt(r.status === 'done' ? { s: 'result', r } : { s: 'idle' }); })
        .catch(() => { if (vivo) setSt({ s: 'idle' }); });
    }
    return () => { vivo = false; };
  }, [chiave, id]);
  // «Riassunto AI» premuto su un avviso: parte una volta sola, e solo se non c'è già un riassunto salvato
  useEffect(() => {
    if (!subito || st.s === 'checking') return;
    onSubito();
    if (st.s === 'idle') avvia(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subito, st.s]);
  const avvia = (regenerate = false) => {
    if (inVolo.has(chiave)) return;
    const p = Bellomberg.newsArticleSummaryRun(id, regenerate);
    inVolo.set(chiave, p);
    p.finally(() => inVolo.delete(chiave)).catch(() => {});
    setSt({ s: 'running' });
    p.then(r => setSt({ s: 'result', r }), e => setSt({ s: 'failed', detail: errorDetail(e) || '' }));
  };
  const testa = (extra?: React.ReactNode) => (
    <div className="news-ai-head"><span className="t"><Sparkles size={14} aria-hidden="true" />{w.aiTitle}</span><span className="bbn-grow" />{extra}</div>
  );
  if (st.s === 'checking') return <section className="news-ai" aria-busy="true">{testa()}<div className="news-sk" /></section>;
  if (st.s === 'running') return (
    <section className="news-ai is-running" aria-live="polite" aria-busy="true">
      {testa()}
      <p className="news-ai-run"><RefreshCw size={13} className="spin" aria-hidden="true" />{w.aiSteps(fonte).join(' · ')}…</p>
      <div className="news-sk" style={{ width: '92%' }} /><div className="news-sk" style={{ width: '78%' }} /><div className="news-sk" style={{ width: '85%' }} />
    </section>
  );
  if (st.s === 'failed' || (st.s === 'result' && st.r.status === 'error')) {
    const detail = st.s === 'failed' ? st.detail : (st.r as { detail: string }).detail;
    return (
      <section className="news-ai is-fail" aria-live="polite">
        {testa()}
        <p className="news-ai-msg"><b>{w.aiFailed}</b>{detail ? ` ${detail}` : ''}</p>
        <div><button type="button" className="bbn-btn" onClick={() => avvia(false)}><RefreshCw size={14} />{w.aiRetry}</button></div>
      </section>
    );
  }
  if (st.s === 'result' && st.r.status === 'not_configured') return (
    <section className="news-ai is-fail" aria-live="polite">
      {testa()}
      <p className="news-ai-msg"><b>{w.aiNotConfigured}.</b> {w.aiNotConfiguredHint(st.r.variable)}</p>
    </section>
  );
  if (st.s === 'result' && st.r.status === 'unreadable') {
    const r = st.r;
    const motivo = r.reason === 'no_url' ? w.aiNoUrl : (w.aiReasons[r.reason]?.(fonte) ?? r.detail);
    return (
      <section className="news-ai is-fail" aria-live="polite">
        {testa()}
        <p className="news-ai-msg"><b>{w.aiUnreadable}</b> {motivo} {w.aiNoCredits}</p>
        {r.reason !== 'no_url' && r.reason !== 'unsafe_url' && (
          <div><button type="button" className="bbn-btn" onClick={() => avvia(false)}><RefreshCw size={14} />{w.aiRetry}</button></div>
        )}
      </section>
    );
  }
  if (st.s === 'result' && st.r.status === 'done') {
    const r = st.r;
    const costo = r.cost_eur != null ? `${fmt(r.cost_eur, 4)} €` : r.cost_usd != null ? `${fmt(r.cost_usd, 4)} $` : `${w.notDeclared} (${r.cost_status})`;
    const creato = new Date(r.created_at).getTime();
    return (
      <section className="news-ai is-done" aria-live="polite">
        {testa(<button type="button" className="bbn-link" onClick={() => avvia(true)}>{w.aiRegenerate}</button>)}
        <div className="news-ai-out">
          <p>{r.summary}</p>
          {r.points.length > 0 && <ul>{r.points.map((p, i) => <li key={i}>{p}</li>)}</ul>}
          {r.key_numbers.length > 0 && <>
            <h4>{w.aiKeyNumbers}</h4>
            <div className="news-ai-nums">{r.key_numbers.map((k, i) => <span key={i} className="bbn-pill is-piatto">{k}</span>)}</div>
          </>}
          {r.portfolio && <><h4>{w.aiForPortfolio}</h4><p>{r.portfolio}</p></>}
        </div>
        <div className="news-ai-foot">
          <span>{w.aiModel} <b>{r.model}</b></span>
          <span>{w.aiCost} <b className="num">{costo}</b></span>
          <span>{w.aiWords(r.words, fmt(r.duration_s, 1))}</span>
          {isFinite(creato) && <span>{w.aiSaved(hhmm(creato))}</span>}
        </div>
      </section>
    );
  }
  return (
    <section className="news-ai">
      {testa()}
      <div className="news-ai-idle">
        <p>{w.aiIntro(fonte)}</p>
        <button type="button" className="bbn-btn news-ai-go" onClick={() => avvia(false)}><Sparkles size={14} />{w.aiButton}</button>
      </div>
    </section>
  );
}

/* ── dettaglio ───────────────────────────────────────────────────── */
export default function DettaglioNotizia({ item, feed, pesi, favSet, onToggleFav, favBusy, favError, onSelect, aiSubito, onAiSubito }: {
  item: FeedItem | null;
  feed: FeedItem[];
  pesi: Map<string, PesoTitolo>;
  favSet: Set<string>;
  onToggleFav: (ticker: string, nome?: string) => void;
  favBusy: boolean;
  favError: string | null;
  onSelect: (id: number) => void;
  aiSubito: boolean;
  onAiSubito: () => void;
}) {
  const w = parole();
  const [orig, setOrig] = useState(false);
  const [copiato, setCopiato] = useState(false);
  useEffect(() => { setOrig(false); setCopiato(false); }, [item?.id]);
  if (!item) return (
    <section className="bbn-card news-det is-empty" aria-live="polite">
      <div className="news-empty"><b>{w.noSelection}</b><span>{w.noSelectionHint}</span></div>
    </section>
  );
  const tk = (item.ticker_mentioned || '').toUpperCase();
  const peso = tk ? pesi.get(tk) : undefined;
  const ts = tsOf(item);
  const oggi = dayKeyFromTs(Date.now());
  const giorno = dayKeyFromTs(ts) === oggi ? w.today : dayKeyFromTs(ts) === dayKeyFromTs(Date.now() - 86_400_000) ? w.yesterday
    : ts ? new Date(ts).toLocaleDateString(localeDi(linguaCorrente()), { day: 'numeric', month: 'long' }) : w.undated;
  const href = externalWebUrl(item.url || '') || undefined;
  const same = tk ? feed.filter(x => x.id !== item.id && (x.ticker_mentioned || '').toUpperCase() === tk)
    .sort((a, b) => tsOf(b) - tsOf(a)).slice(0, 4) : [];
  const nOggi = tk ? feed.filter(x => (x.ticker_mentioned || '').toUpperCase() === tk && dayKeyFromTs(tsOf(x)) === oggi).length : 0;
  const b = sentBucket(item.sentiment);
  const generated = item.summary_status === 'available' || item.summary_status === 'legacy';
  // con la sintesi generata il titolo è riscritto: l'originale della fonte resta consultabile
  const originale = item.title_original || '';
  const mostraOriginale = generated && !!originale && originale !== item.title;
  const copia = async () => {
    if (!href) return;
    try { await navigator.clipboard.writeText(href); setCopiato(true); setTimeout(() => setCopiato(false), 2000); } catch { /* clipboard negata */ }
  };
  return (
    <section className="bbn-card news-det" aria-label={item.title} aria-live="polite">
      <div className="news-det-top">
        <IconaNotizia item={item} nome={peso?.nome} dimensione="md" />
        <div className="who">
          <b>{tk ? (peso?.nome || tk) : themeLabelOf(themeKeyOf(item))}</b>
          <span>{tk ? (peso ? `${tk} · ${w.ofPortfolio(fmt(peso.peso) + '%')}` : tk) : w.contextNews}</span>
        </div>
        {tk && (
          <button type="button" className="news-star" aria-pressed={favSet.has(tk)} disabled={favBusy}
            onClick={() => onToggleFav(tk, peso?.nome)} title={favError || undefined}>
            <Star size={14} aria-hidden="true" />{favSet.has(tk) ? w.favorite : w.addFavorite}
          </button>
        )}
      </div>
      <div className="news-det-body">
        {favError && <p className="news-inline-err" role="status">{w.favError}: {favError}</p>}
        <div className="meta">{w.at(giorno, hhmm(ts))} · {item.source || item.provider || '—'}{item.provider && item.source && item.provider !== item.source ? ` · ${w.via(item.provider)}` : ''}</div>
        <h2>{item.title}</h2>
        <div className="pills">
          <PastigliaTono sentiment={item.sentiment} prefisso />
          <span className={'bbn-pill ' + ((item.relevance ?? 0) >= 8 ? 'news-pill-warn' : 'is-piatto')}>{w.relPill(item.relevance ?? 0)}</span>
          <span className="bbn-pill news-pill-acc">{themeLabelOf(themeKeyOf(item))}</span>
        </div>
        {generated && item.snippet && (
          <div className="news-why">
            <div className="k">{w.whyMatters}</div><p>{item.snippet}</p>
            <div className="o">{tr('newsdesk.originalSummary', { language: originalLanguage(item.summary_language) })}</div>
          </div>
        )}
        <div className="news-sum">
          <div className="k">
            {w.summary}<span className="muted"> · {generated || item.summary_status ? tr('newsdesk.originalSource') : tr('newsdesk.receivedUnknown')}</span>
            {mostraOriginale && <><span className="bbn-grow" /><button type="button" className="bbn-link" onClick={() => setOrig(o => !o)}>{orig ? w.hideOriginal : w.showOriginal}</button></>}
          </div>
          {!generated && item.snippet && <p>{item.snippet}</p>}
          {generated && item.snippet_original && <p>{item.snippet_original}</p>}
          {item.summary_note && <p className="muted small">{item.summary_note}</p>}
          {orig && mostraOriginale && <div className="orig">{originale}</div>}
        </div>
        <div className="news-tiles">
          <div className="news-tile"><span className="k">{w.weight}</span><span className="v num">{peso ? fmt(peso.peso) + '%' : '—'}</span><span className="s">{tk || w.noTicker}</span></div>
          <div className="news-tile"><span className="k">{w.toneScore}</span>
            <span className={'v num' + (b === 'pos' ? ' is-up' : b === 'neg' ? ' is-down' : '')}>
              {item.sentiment_score != null ? `${item.sentiment_score > 0 ? '+' : ''}${fmt(item.sentiment_score, 2)}` : '—'}
            </span><span className="s">{w.toneRange}</span></div>
          <div className="news-tile"><span className="k">{w.todayOnTicker}</span><span className="v num">{tk ? nOggi : '—'}</span><span className="s">{tk ? w.inLoadedFeed : ''}</span></div>
        </div>
        {item.id != null && <RiassuntoAI key={`${item.id}:${linguaCorrente()}`} item={item} subito={aiSubito} onSubito={onAiSubito} />}
        {same.length > 0 && (
          <div className="news-related">
            <div className="k">{w.moreOn(tk)}</div>
            {same.map(x => (
              <button key={x.id} type="button" className="news-rrow" onClick={() => onSelect(x.id)}>
                <span className="tm num">{hhmm(tsOf(x))}</span><b>{x.title}</b><MisuraRilevanza r={x.relevance} />
              </button>
            ))}
          </div>
        )}
        <div className="news-det-actions">
          {href
            ? <a className="bbn-btn is-primary" href={href} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} />{w.openArticle}</a>
            : <span className="muted small">{w.noLink}</span>}
          {href && <button type="button" className="bbn-btn" onClick={copia}><Copy size={14} />{copiato ? w.copied : w.copyLink}</button>}
        </div>
      </div>
    </section>
  );
}
