import { ChevronLeft, FileText, Languages, LayoutGrid, LineChart, Loader2, Newspaper, Star, Users } from 'lucide-react';
import { externalWebUrl } from '../../../electron/security';
import type { FinBlock, MktFinancials, MktHolders, MktNewsItem, MktQuote } from '@/lib/api';
import { useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import TvChartPanel from '@/components/TvChartPanel';
import FilingRiepilogo from '@/components/FilingRiepilogo';
import { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import type { Parole } from './parole';
import { CardMercati, compatto, finito, numero, prezzo, variazione } from './comuni';

export type SchedaFin = 'income' | 'balance' | 'cashflow';
export type SchedaLato = 'news' | 'filing';
export type SchedaAz = 'own' | 'profile';

function tempoFa(p?: string | number) {
  if (p == null) return '';
  const t = typeof p === 'number' ? (p > 2e10 ? p : p * 1000) : Date.parse(p);
  if (!isFinite(t)) return '';
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 60) return tr('ui.minutes_ago', { n: m });
  const h = Math.round(m / 60);
  if (h < 48) return tr('ui.hours_ago', { n: h });
  return tr('ui.days_ago', { n: Math.round(h / 24) });
}
const gv = (r: Record<string, any>, ...keys: string[]) => { for (const k of keys) if (r[k] != null) return r[k]; return null; };
const pct = (v: any) => v == null ? '—' : numero(Number(v) <= 1 ? Number(v) * 100 : Number(v), 1) + '%';

function TabellaBilancio({ b, w }: { b?: FinBlock; w: Parole }) {
  const voci = Object.keys(b?.rows || {});
  if (!b || !voci.length) return <p className="mk-empty">{tr('ui.no_data')}</p>;
  return (
    <table className="mk-tbl">
      <thead><tr><th>{w.rowLabel}</th>{b.years.map((y, i) => <th key={i}>{y}</th>)}</tr></thead>
      <tbody>
        {voci.map(l => (
          <tr key={l}>
            <td>{l}</td>
            {b.rows[l].map((v, i) => (
              <td key={i} className={'num' + (v != null && v < 0 ? ' mk-dn' : '')}>{v == null ? '—' : l.includes('EPS') ? numero(v) : compatto(v, 1)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface Props {
  w: Parole;
  tk: string;
  ricerca: React.ReactNode;
  onIndietro: () => void;
  quote: MktQuote | null; quoteErr: string | null;
  news: MktNewsItem[] | null; newsErr: string | null; newsAvvisi: string[] | null;
  fin: MktFinancials | null; finErr: string | null;
  holders: MktHolders | null; holdersErr: string | null;
  fav: boolean | null; favReadErr: string | null; favWriteErr: string | null; toggleFav: () => void;
  traduzione: { titoli: string[]; costo: number | null; cached: boolean; completo: boolean } | null; traduco: boolean; tradErr: string | null;
  traduci: () => void; mostraOriginale: boolean; setMostraOriginale: (v: boolean) => void;
  tabFin: SchedaFin; setTabFin: (t: SchedaFin) => void;
  tabLato: SchedaLato; setTabLato: (t: SchedaLato) => void;
  tabAz: SchedaAz; setTabAz: (t: SchedaAz) => void;
}

export default function SchedaTitolo(p: Props) {
  useT();
  const { w, tk, quote: q } = p;
  const nd = tr('settings.nd');
  const px = q?.price, pc = q?.prev_close;
  const chg = finito(px) && pc ? ((px / pc) - 1) * 100 : null;
  const range = finito(q?.low_52w) && finito(q?.high_52w) && finito(px) && q!.high_52w! > q!.low_52w!
    ? Math.min(100, Math.max(0, ((px - q!.low_52w!) / (q!.high_52w! - q!.low_52w!)) * 100)) : null;
  const valuta = q?.currency ? ' ' + q.currency : '';
  const s = w.stats;
  const stats: Array<[string, string, string?]> = [
    [s.cap, compatto(q?.market_cap)], [s.pe, numero(q?.pe)], [s.fpe, numero(q?.fwd_pe)], [s.eps, numero(q?.eps)],
    [s.beta, numero(q?.beta)], [s.div, finito(q?.div_yield) ? numero(q!.div_yield! > 0.5 ? q!.div_yield! : q!.div_yield! * 100) + '%' : '—'],
    [s.vol, compatto(q?.volume, 1)], [s.avgVol, compatto(q?.avg_volume, 1)],
    [s.evEbitda, numero(q?.ev_ebitda)], [s.peg, numero(q?.peg)],
    [s.pb, numero(q?.pb)], [s.fcf, finito(q?.fcf) && q?.market_cap ? numero((q.fcf / q.market_cap) * 100, 1) + '%' : '—'],
    [s.short, finito(q?.short_pct_float) ? numero(q!.short_pct_float! * 100, 1) + '%' : '—'],
    [s.target, numero(q?.target_mean), finito(q?.target_mean) && finito(px) ? (q!.target_mean! >= px ? 'mk-up' : 'mk-dn') : undefined],
  ];
  const sub = [tk, q?.exchange, q?.sector, q?.industry].filter(Boolean).join(' · ');

  return (
    <>
      <div className="mk-top">
        <button type="button" className="bbn-link mk-back" onClick={p.onIndietro}><ChevronLeft size={18} aria-hidden="true" />{w.back}</button>
        <span className="bbn-grow" />
        {p.ricerca}
        <span className="bbn-grow" />
      </div>

      <div className="mk-dhead">
        <IconaTitolo ticker={tk} nome={q?.name} dimensione="lg" />
        <div className="mk-dtitle">
          <b>{q?.name || tk}</b>
          <span>{sub}</span>
        </div>
        <div className="mk-dprice">
          {q === null ? <Loader2 size={20} className="mk-spin" aria-label="…" />
            : p.quoteErr ? <span className="mk-inline-err" role="alert">{w.quoteError(p.quoteErr)}</span>
            : <>
              <span className="num mk-dprice-v">{prezzo(px)}<small>{valuta}</small></span>
              <PastigliaVariazione valore={chg} grande lampo={px ?? null} title={w.previousClose}><span className="num">{variazione(chg)}</span></PastigliaVariazione>
            </>}
        </div>
        <span className="bbn-grow" />
        {q?.recommendation && (
          <span className="mk-consensus">{w.consensus(q.recommendation)}{finito(q.target_mean) ? ' · ' + w.target(numero(q.target_mean) + valuta) : ''}</span>
        )}
        <button type="button" className={'bbn-btn mk-fav-btn' + (p.fav ? ' is-on' : '')} onClick={p.toggleFav} disabled={p.fav == null}
          aria-pressed={!!p.fav} title={p.fav ? w.removeFavoriteTitle : w.addFavoriteTitle} data-azione="preferito">
          <Star size={15} fill={p.fav ? 'currentColor' : 'none'} aria-hidden="true" />{p.fav ? w.inFavorites : w.addFavorite}
        </button>
      </div>
      {(p.favReadErr || p.favWriteErr) && (
        <p className="mk-inline-err" role="alert">{p.favWriteErr ? w.favoriteUnconfirmed(p.favWriteErr) : w.favoriteReadFailed(p.favReadErr!)}</p>
      )}

      <div className="mk-dgrid">
        <CardMercati tinta="idx" icona={<LineChart size={18} />} titolo={w.chart} className="mk-c-chart">
          <div className="mk-chart">
            <TvChartPanel ticker={tk} fill defaultRange={5} defaultInterval={4} timeSelects pageRecovery />
          </div>
        </CardMercati>

        <CardMercati tinta="mov" icona={<Newspaper size={18} />} titolo={p.tabLato === 'news' ? w.news : w.newsAll} className="mk-c-news"
          azioni={<>
            {p.tabLato === 'news' && !!p.news?.length && linguaCorrente() !== 'en' && (p.traduzione && !p.mostraOriginale
              ? <button type="button" className="bbn-link mk-tr-btn" onClick={() => p.setMostraOriginale(true)}>{w.showOriginal}</button>
              : <button type="button" className="bbn-link mk-tr-btn" onClick={p.traduci} disabled={p.traduco} title={w.translateHint} data-azione="traduci">
                  {p.traduco ? <Loader2 size={14} className="mk-spin" aria-hidden="true" /> : <Languages size={14} aria-hidden="true" />}
                  {p.traduco ? w.translating : p.traduzione ? w.showTranslated : w.translate}
                </button>)}
            <Segmenti etichetta={w.newsTabs} valore={p.tabLato} onChange={p.setTabLato}
              opzioni={[{ id: 'news', testo: w.news }, { id: 'filing', testo: w.newsAll }]} />
          </>}>
          <div className="mk-body">
            {p.tabLato === 'filing' ? <div className="mk-filing"><FilingRiepilogo key={tk} ticker={tk} /></div>
              : p.news === null ? <p className="mk-empty"><Loader2 size={13} className="mk-spin" aria-hidden="true" /> {w.newsLoading}</p>
              : p.newsErr ? <p className="mk-empty mk-err">{w.newsError(p.newsErr)}</p>
              : p.news.length === 0 ? (p.newsAvvisi
                ? <p className="mk-empty mk-err" role="alert">{w.newsError(p.newsAvvisi.join('; '))}</p>
                : <p className="mk-empty">{w.newsEmpty}</p>)
              : <>
                {p.newsAvvisi && <p className="mk-empty mk-err" role="alert">{w.newsPartial(p.newsAvvisi.join('; '))}</p>}
                {p.tradErr && <p className="mk-empty mk-err" role="alert">{p.tradErr.startsWith('not_configured:')
                  ? w.translateNotConfigured(p.tradErr.slice('not_configured:'.length)) : w.translateFailed(p.tradErr)}</p>}
                {p.news.map((n, i) => {
                  const tradotto = p.traduzione && !p.mostraOriginale ? p.traduzione.titoli[i] : null;
                  return (
                    <a key={i} className="mk-nw" href={externalWebUrl(n.link || '') || undefined} target="_blank" rel="noreferrer"
                      title={tradotto ? n.title : undefined} lang={tradotto ? undefined : 'en'}>
                      <b>{tradotto || n.title}</b>
                      <span>{[n.publisher, tempoFa(n.published)].filter(Boolean).join(' · ')}</span>
                      {n.match === 'nome' && <span className="mk-err">{w.newsByName(n.match_query || '?')}</span>}
                    </a>
                  );
                })}
                {p.traduzione && !p.mostraOriginale && !p.traduzione.completo && (
                  <p className="mk-source">{w.translatePartial}{' '}
                    <button type="button" className="bbn-link" onClick={p.traduci} disabled={p.traduco}>{w.retry}</button></p>
                )}
                <p className="mk-source">{p.traduzione && !p.mostraOriginale
                  ? w.translatedNote(p.traduzione.costo != null ? '€ ' + numero(p.traduzione.costo, 4) : null) : w.sourceOriginal}
                  {' · '}{w.newsSource(Array.from(new Set(p.news.map(n => n.source === 'yahoo_ticker_news' ? w.newsSourceTicker
                    : n.source === 'yahoo_search' ? w.newsSourceSearch : w.newsSourceUnknown))).join(', '))}</p>
              </>}
          </div>
        </CardMercati>

        <CardMercati tinta="stk" icona={<LayoutGrid size={18} />} titolo={w.keyData} className="mk-c-key">
          <div className="mk-body">
            <dl className="mk-stats">
              {stats.map(([k, v, tono]) => <div key={k}><dt>{k}</dt><dd className={'num' + (tono ? ' ' + tono : '')}>{v}</dd></div>)}
            </dl>
            {range != null && (
              <div className="mk-range">
                <span>{w.low52} <b className="num">{numero(q?.low_52w)}</b></span>
                <span className="mk-range-tr" aria-hidden="true"><i style={{ left: range.toFixed(1) + '%' }} /></span>
                <span>{w.high52} <b className="num">{numero(q?.high_52w)}</b></span>
              </div>
            )}
          </div>
        </CardMercati>

        <CardMercati tinta="rt" icona={<FileText size={18} />} titolo={w.financials} className="mk-c-fin"
          azioni={<Segmenti etichetta={w.financialsTabs} valore={p.tabFin} onChange={p.setTabFin}
            opzioni={[{ id: 'income', testo: w.income, title: w.incomeTitle }, { id: 'balance', testo: w.balance, title: w.balanceTitle }, { id: 'cashflow', testo: w.cashflow, title: w.cashflowTitle }]} />}>
          <div className="mk-body">
            {p.fin === null ? <p className="mk-empty"><Loader2 size={13} className="mk-spin" aria-hidden="true" /> {w.financialsLoading}</p>
              : p.fin.error || !p.fin.statements
                ? <p className="mk-empty">{p.finErr !== null ? `${w.financialsError}: ${p.finErr}` : p.fin.error || tr('ui.no_data')}</p>
                : <><TabellaBilancio b={p.fin.statements[p.tabFin]} w={w} /><p className="mk-source">{w.sourceOriginal}</p></>}
          </div>
        </CardMercati>

        <CardMercati tinta="fx" icona={<Users size={18} />} titolo={w.holders} className="mk-c-own"
          azioni={<Segmenti etichetta={w.holdersTabs} valore={p.tabAz} onChange={p.setTabAz}
            opzioni={[{ id: 'own', testo: w.ownership }, { id: 'profile', testo: w.profile, title: w.companyProfile }]} />}>
          <div className="mk-body">
            {p.tabAz === 'profile'
              ? q?.summary ? <><p className="mk-profile">{q.summary}</p><p className="mk-source">{w.sourceOriginal}</p></> : <p className="mk-empty">{w.profileEmpty}</p>
              : <Azionariato w={w} holders={p.holders} err={p.holdersErr} nd={nd} />}
          </div>
        </CardMercati>
      </div>
    </>
  );
}

function Azionariato({ w, holders, err, nd }: { w: Parole; holders: MktHolders | null; err: string | null; nd: string }) {
  if (holders === null) return <p className="mk-empty">{w.holdersLoading}</p>;
  const loc = localeDi(linguaCorrente());
  return (
    <>
      {err ? <div className="mk-empty mk-err">{tr('ui.ownership_error', { error: err })}</div>
        : holders.major.length === 0 ? <div className="mk-empty">{nd}</div>
        : holders.major.map((m, i) => {
          const conta = String(m.label).toLowerCase().includes('count');
          const val = typeof m.value === 'number' ? m.value : null;
          const quota = !conta && val != null ? Math.min(100, val <= 1 ? val * 100 : val) : null;
          return (
            <div key={i} className="mk-ob">
              <span>{w.ownershipLabels[String(m.label)] || String(m.label)}</span>
              <b className="num">{val == null ? String(m.value ?? '—') : conta ? Math.round(val).toLocaleString(loc) : pct(val)}</b>
              {quota != null && <span className="mk-ob-tr" aria-hidden="true"><i style={{ width: quota + '%' }} /></span>}
            </div>
          );
        })}
      <h3 className="mk-subh">{w.topHolders}</h3>
      {holders.institutional.length > 0
        ? holders.institutional.map((r, i) => (
          <div key={i} className="mk-hold">
            <b>{String(gv(r, 'holder') ?? '—')}</b>
            <span className="num">{pct(gv(r, 'pctheld', 'pct_held', 'pctout', 'pct_out'))}</span>
            <span className="num">{compatto(Number(gv(r, 'shares')), 1)}</span>
          </div>
        ))
        : <div className={'mk-empty' + (err ? ' mk-err' : '')}>{err ? tr('ui.ownership_failed') : nd}</div>}
    </>
  );
}
