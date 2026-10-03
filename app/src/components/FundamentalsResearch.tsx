import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Download, ExternalLink, FileSpreadsheet, RefreshCw } from 'lucide-react';
import { Bellomberg, type FundResearchCompany, type FundResearchList, type FundResearchSource,
  type FundArchiveList, type FundArchiveItem, type FundObservationRefresh } from '@/lib/api';
import { archivedFundWorkbook } from '@/lib/fundamentals-research';
import { fmtNum } from '@/lib/format';
import { leggiDetail } from '@/lib/quota';
import { useT } from '@/i18n/provider';

type Translate = ReturnType<typeof useT>;
type TranslationKey = Parameters<Translate>[0];
const heading = 'text-[#ff8c00] uppercase text-[10px] tracking-[2px]';
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const valueText = (value: unknown, tr: Translate, digits = 2) => finite(value) ? fmtNum(value, digits) : tr('fundamentals.f001');
const dateText = (value: string | null | undefined, tr: Translate) => value || tr('fundamentals.f001');
const errorText = (error: any, tr: Translate) => leggiDetail(error?.response?.data?.detail ?? error?.message ?? error) || tr('fundamentals.errorUnknown');
const safeLink = (url?: string | null) => typeof url === 'string' && /^https?:\/\/[^\s<>]+$/i.test(url) ? url : undefined;
const currencyText = (value: unknown, currency: string | null, tr: Translate) => currency || (finite(value) ? tr('fundamentals.researchCurrencyMissing') : '');

function statusText(status: string | null | undefined, tr: Translate): string {
  const keys: Record<string, TranslationKey> = {available: 'fundamentals.researchAvailable', missing: 'fundamentals.researchMissing', stale: 'fundamentals.researchStale',
    unavailable: 'fundamentals.researchUnavailable', data_missing: 'fundamentals.researchMissing', date_missing: 'fundamentals.researchDateMissing',
    date_invalid: 'fundamentals.researchDateInvalid', identity_mismatch: 'fundamentals.researchIdentityMismatch',
    currency_missing: 'fundamentals.researchCurrencyMissing', currency_mismatch: 'fundamentals.researchCurrencyMismatch', source_unavailable: 'fundamentals.researchUnavailable',
    not_applicable: 'fundamentals.researchNotApplicable'};
  return keys[status || ''] ? tr(keys[status || '']) : status || tr('fundamentals.f001');
}

function RefreshFailure({refresh}: {refresh?: FundObservationRefresh | null}) {
  const tr = useT();
  return refresh?.status === 'failed' ? <p className="text-amber text-xs" role="status">
    {tr('fundamentals.researchRefreshFailed')} · {dateText(refresh.last_attempt_at, tr)}
    {refresh.error && <> · {refresh.error}</>}
  </p> : null;
}

function Prose({text}: {text: string}) {
  return <div className="text-xs leading-relaxed break-words space-y-2 [&_p]:mb-2 [&_ul]:list-disc [&_ul]:pl-4 [&_h2]:mt-4 [&_h3]:mt-3">
    <ReactMarkdown components={{a: ({href, children}) => safeLink(href)
      ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-cyan underline">{children}</a>
      : <span>{children}</span>}}>{text}</ReactMarkdown>
  </div>;
}

function Sources({sources}: {sources: FundResearchSource[]}) {
  const tr = useT();
  return <section className="space-y-2"><h3 className={heading}>{tr('fundamentals.researchSources')}</h3>
    {!sources.length && <p className="text-muted text-xs">{tr('fundamentals.researchSourcesMissing')}</p>}
    {sources.map((source, index) => <div key={source.id || index} className="border-t border-border pt-2 text-xs break-words">
      {safeLink(source.url) ? <a href={safeLink(source.url)} target="_blank" rel="noopener noreferrer" className="text-cyan inline-flex gap-1 items-center">
        {source.source || source.metadata?.title || source.id || source.url}<ExternalLink size={11}/></a>
        : <span>{source.source || source.id || tr('fundamentals.researchSourceUnavailable')}</span>}
      <div className="text-muted mt-1">{tr('fundamentals.researchProviderDate')}: {dateText(source.as_of || source.published_at, tr)}</div>
      {source.summary && <p className="mt-1">{source.summary}</p>}
    </div>)}
  </section>;
}

function Detail({company}: {company: FundResearchCompany}) {
  const tr = useT(), analysis = company.analysis, content = analysis.content;
  const judgment: Record<string, TranslationKey> = {favorable: 'fundamentals.researchFavorable', rejected: 'fundamentals.researchRejected', watch: 'fundamentals.researchWatch', incomplete: 'fundamentals.researchIncomplete'};
  const consensus = company.consensus;
  return <div className="space-y-5">
    <header className="border-b border-border pb-3">
      <h2 className="text-gold text-base font-semibold">{company.ticker} <span className="text-muted font-normal">{company.name}</span></h2>
      <div className="text-[10px] text-muted mt-1">{tr('fundamentals.researchAnalysisDate')}: {dateText(analysis.as_of, tr)}</div>
    </header>
    <section className="space-y-2 text-xs">
      <h3 className={heading}>{tr('fundamentals.researchConsensus')}</h3>
      <p className="text-muted">{tr('fundamentals.researchConsensusNote')}</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span className="text-muted">{tr('fundamentals.researchMean')}</span><span>{valueText(consensus.mean, tr)} {currencyText(consensus.mean, consensus.currency, tr)}</span>
        <span className="text-muted">{tr('fundamentals.researchMedian')}</span><span>{valueText(consensus.median, tr)}</span>
        <span className="text-muted">{tr('fundamentals.researchRange')}</span><span>{valueText(consensus.low, tr)} — {valueText(consensus.high, tr)}</span>
        <span className="text-muted">{tr('fundamentals.researchAnalysts')}</span><span>{valueText(consensus.number_of_analysts, tr, 0)}</span>
        <span className="text-muted">{tr('fundamentals.quoteSource')}</span><span>{consensus.source || tr('fundamentals.f001')}</span>
        <span className="text-muted">{tr('fundamentals.researchAcquired')}</span><span>{dateText(consensus.acquired_at, tr)}</span>
        <span className="text-muted">{tr('fundamentals.researchProviderDate')}</span><span>{dateText(consensus.data_as_of, tr)}</span>
      </div>
      {consensus.status !== 'available' && <p className="text-amber">{statusText(consensus.status, tr)}</p>}
      {consensus.reason && <p className="text-muted">{consensus.reason === 'market_observation_required'
        ? tr('fundamentals.researchMarketRequired') : consensus.reason}</p>}
      <RefreshFailure refresh={consensus.refresh}/>
      <p className="text-muted text-[10px]">{tr('fundamentals.researchFreshness')}</p>
      <p className="text-muted">{tr('fundamentals.researchPriceSource')}: {company.quote.source || tr('fundamentals.f001')} · {dateText(company.quote.observed_at, tr)}</p>
      {company.quote.acquired_at && <p className="text-muted text-[10px]">{tr('fundamentals.researchPriceAcquired')}: {dateText(company.quote.acquired_at, tr)}</p>}
      <RefreshFailure refresh={company.quote.refresh}/>
    </section>
    <section className="space-y-2">
      <h3 className={heading}>{tr('fundamentals.researchAiOpinion')}</h3>
      <p className="text-muted text-[10px]">{tr('fundamentals.researchAiEstimatesNote')}</p>
      {analysis.status !== 'available' ? <p className="text-muted text-xs">{statusText(analysis.status, tr)}</p> : <>
        <p className="text-muted text-[10px]">{analysis.origin === 'weekly' ? tr('fundamentals.researchWeekly') : tr('fundamentals.researchTradeIdea')}
          {analysis.technical_status && <> · {analysis.technical_status === 'completed' ? tr('fundamentals.researchCompleted') : tr('fundamentals.researchRunIncomplete')}</>}</p>
        {analysis.judgment && <div className="text-gold text-xs">{judgment[analysis.judgment] ? tr(judgment[analysis.judgment]) : analysis.judgment}</div>}
        {analysis.scope === 'whole_committee_report' && <p className="border border-border p-2 text-xs text-amber">{tr('fundamentals.researchWholeReport')}</p>}
        {analysis.summary && <Prose text={analysis.summary}/>}
        {content?.report && <Prose text={content.report}/>}
      </>}
    </section>
    {content?.dossier?.map(section => <section key={section.key} className="space-y-2">
      <h3 className={heading}>{section.title}</h3>
      {section.paragraphs.map((paragraph, index) => <Prose key={index} text={paragraph}/>)}
      {section.tables?.map((table, index) => <div key={index} className="overflow-x-auto text-xs">
        <p className="text-muted mb-1">{table.title} · {table.unit} · {table.period}</p>
        <table className="w-full"><thead><tr>{table.columns.map((column, i) => <th key={i} className="text-left p-1 border-b border-border">{column}</th>)}</tr></thead>
          <tbody>{table.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} className="p-1 border-b border-border/50">{cell}</td>)}</tr>)}</tbody></table>
        <p className="text-muted mt-1">{tr('fundamentals.quoteSource')} {table.source}</p>
      </div>)}
    </section>)}
    {!!content?.scenarios?.length && <section className="space-y-2"><h3 className={heading}>{tr('fundamentals.researchScenarios')}</h3>
      {content.scenarios.map((scenario, index) => <div key={index}><h4 className="text-xs text-gold mb-1">{scenario.name}</h4><Prose text={scenario.analysis}/></div>)}
    </section>}
    {([
      ['fundamentals.researchRisks', content?.risks], ['fundamentals.researchCatalysts', content?.catalysts],
      ['fundamentals.researchInvalidation', content?.invalidation], ['fundamentals.researchMonitor', content?.review_conditions],
      ['fundamentals.researchGaps', content?.data_gaps?.map(gap => typeof gap === 'string' ? gap : gap.reason || tr('fundamentals.f001'))],
    ] as [TranslationKey, string[] | undefined][]).map(([key, values]) => !!values?.length && <section key={key} className="space-y-2">
      <h3 className={heading}>{tr(key)}</h3><ul className="list-disc pl-4 space-y-1 text-xs">{values.map((value, index) => <li key={index}>{value}</li>)}</ul>
    </section>)}
    {analysis.status === 'available' && <Sources sources={content?.evidence || content?.documents || []}/>}
  </div>;
}

export default function FundamentalsResearch() {
  const tr = useT();
  const [list, setList] = useState<FundResearchList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<FundResearchCompany | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archive, setArchive] = useState<FundArchiveList | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [downloadBusy, setDownloadBusy] = useState<string | null>(null);
  const [listReading, setListReading] = useState(false), [detailReading, setDetailReading] = useState(false);
  const readList = useRef<() => void>(() => {}), readDetail = useRef<() => void>(() => {});

  useEffect(() => {
    let active = true, reading = false;
    const read = async () => {
      if (!active || reading) return;
      reading = true; setListReading(true);
      try {
        const value = await Bellomberg.fundamentalsResearch();
        if (!active) return;
        if (value.status !== 'available') { setError(tr('fundamentals.researchUnavailable')); return; }
        setList(value); setError(null);
        setSelected(before => value.items.some(item => item.ticker === before) ? before : value.items[0]?.ticker || null);
      } catch (error) { if (active) setError(errorText(error, tr)); }
      finally { reading = false; if (active) setListReading(false); }
    };
    readList.current = read;
    void read();
    const timer = setInterval(() => void read(), 60_000);
    return () => { active = false; clearInterval(timer); readList.current = () => {}; };
  }, [tr]);

  useEffect(() => {
    let active = true, reading = false;
    setDetail(before => before?.ticker === selected ? before : null); setDetailError(null); setDetailReading(false);
    if (!selected) return;
    const read = async () => {
      if (!active || reading) return;
      reading = true; setDetailReading(true);
      try {
        const value = await Bellomberg.fundamentalsCompany(selected);
        if (!active) return;
        if (value.ticker !== selected) { setDetailError(tr('fundamentals.researchIdentityMismatch')); return; }
        setDetail(value); setDetailError(null);
      } catch (error) { if (active) setDetailError(errorText(error, tr)); }
      finally { reading = false; if (active) setDetailReading(false); }
    };
    readDetail.current = read;
    void read();
    const timer = setInterval(() => void read(), 60_000);
    return () => { active = false; clearInterval(timer); readDetail.current = () => {}; };
  }, [selected, tr]);

  useEffect(() => {
    let active = true;
    if (archiveOpen && !archive) Bellomberg.fundamentalsArchive().then(value => {
      if (active) { setArchive(value); setArchiveError(null); }
    }).catch(error => { if (active) setArchiveError(errorText(error, tr)); });
    return () => { active = false; };
  }, [archiveOpen, archive, tr]);

  async function download(item: FundArchiveItem) {
    if (downloadBusy) return;
    setDownloadBusy(item.id); setArchiveError(null);
    try {
      const blob = await archivedFundWorkbook(item), url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = item.file; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setArchiveError(errorText(error, tr)); }
    finally { setDownloadBusy(null); }
  }

  return <div className="space-y-4">
    <div className="flex items-center justify-between gap-3">
      <div><h1 className="text-gold text-sm font-semibold tracking-wider">{tr('fundamentals.researchTitle')}</h1>
        <p className="text-muted text-[10px] mt-1">{tr('fundamentals.researchSubtitle')}</p></div>
      <div className="flex items-center gap-4"><button className="text-xs text-muted hover:text-gold inline-flex items-center gap-1 disabled:opacity-50"
        aria-label={tr('fundamentals.researchReadAgain')} title={tr('fundamentals.researchReadAgainHint')}
        disabled={listReading || detailReading} onClick={() => { readList.current(); readDetail.current(); }}>
        <RefreshCw size={13}/>{tr('fundamentals.researchReadAgain')}</button>
      <button className="text-xs text-muted hover:text-gold inline-flex items-center gap-1" aria-controls="fund-excel-archive" aria-expanded={archiveOpen}
        onClick={() => setArchiveOpen(open => !open)}><FileSpreadsheet size={13}/>{tr('fundamentals.researchArchive')}</button>
      </div>
    </div>
    <p className="text-muted text-[10px]">{tr('fundamentals.researchAutoUpdate')}</p>
    {list?.market_refresh && <p className={`text-[10px] ${['error', 'waiting'].includes(list.market_refresh.status) ? 'text-amber' : 'text-muted'}`} role="status">
      {tr(list.market_refresh.status === 'error' ? 'fundamentals.researchRefreshFailed'
        : list.market_refresh.status === 'waiting' ? 'fundamentals.researchRefreshWaiting'
        : ['stopped', 'stopping'].includes(list.market_refresh.status) ? 'fundamentals.researchRefreshStopped'
        : ['starting', 'refreshing'].includes(list.market_refresh.status) ? 'fundamentals.researchRefreshRunning' : 'fundamentals.researchRefreshActive')}
      {list.market_refresh.status === 'waiting' && list.market_refresh.next_retry_at && <>
        {' · '}{tr('fundamentals.researchNextRetry')}: {dateText(list.market_refresh.next_retry_at, tr)}</>}
      {list.market_refresh.last_completed_at && <> · {tr('fundamentals.researchLastCycle')}: {dateText(list.market_refresh.last_completed_at, tr)}</>}
      {list.market_refresh.error && !/^market_update_[a-z_]+$/.test(list.market_refresh.error) && <> · {list.market_refresh.error}</>}
    </p>}
    {error && <div role="alert" className="panel text-red-400 text-xs">{error}</div>}
    {!list && !error && <p className="text-muted text-xs">{tr('fundamentals.researchLoading')}</p>}
    {!!list?.notices.length && <p className="text-amber text-xs">{tr('fundamentals.researchPartial')}</p>}
    {list && <div className="grid grid-cols-1 xl:grid-cols-[minmax(400px,0.95fr)_minmax(0,1.35fr)] gap-4 items-start">
      <div className="panel overflow-x-auto">
        <table className="w-full text-xs"><thead><tr className="text-muted border-b border-border text-left">
          {[tr('fundamentals.researchCompany'), tr('fundamentals.researchPrice'), tr('fundamentals.researchConsensus'), tr('fundamentals.researchUpside')].map(label => <th key={label} className="p-2 font-normal">{label}</th>)}
        </tr></thead><tbody>{list.items.map(item => <tr key={item.ticker} className={`border-b border-border/50 ${item.ticker === selected ? 'bg-[#ff8c00]/5' : ''}`}>
          <td className="p-2"><button className="text-gold font-semibold text-left hover:underline" aria-label={item.ticker} onClick={() => setSelected(item.ticker)}>{item.ticker}</button>
            <div className="text-[10px] text-muted mt-1 max-w-[160px] truncate" title={item.name || ''}>{item.name}</div></td>
          <td className="p-2 whitespace-nowrap">{valueText(item.quote.value, tr)} <span className="text-muted text-[10px]">{currencyText(item.quote.value, item.quote.currency, tr)}</span>
            {item.quote.status !== 'available' && <div className="text-amber text-[10px] whitespace-normal">{statusText(item.quote.status, tr)}</div>}</td>
          <td className="p-2 whitespace-nowrap">{valueText(item.consensus.mean, tr)} <span className="text-muted text-[10px]">{currencyText(item.consensus.mean, item.consensus.currency, tr)}</span>
            {item.consensus.status !== 'available' && <div className="text-amber text-[10px] whitespace-normal">{statusText(item.consensus.status, tr)}</div>}</td>
          <td className="p-2 whitespace-nowrap">{item.comparison.status === 'available' && finite(item.comparison.upside_pct)
            ? `${item.comparison.upside_pct > 0 ? '+' : ''}${fmtNum(item.comparison.upside_pct, 1)}%` : tr('fundamentals.f001')}
            {item.comparison.status !== 'available' && <div className="text-muted text-[10px] whitespace-normal">{statusText(item.comparison.status, tr)}</div>}</td>
        </tr>)}</tbody></table>
        {!list.items.length && <p className="text-muted text-xs p-3">{tr('fundamentals.researchEmpty')}</p>}
      </div>
      <aside className="panel min-w-0">
        {detailError && <p role="alert" className="text-red-400 text-xs">{detailError}</p>}
        {detail?.ticker === selected ? <Detail company={detail}/> : !detailError && <p className="text-muted text-xs">{selected ? tr('fundamentals.researchDetailLoading') : tr('fundamentals.f019')}</p>}
      </aside>
    </div>}
    {archiveOpen && <section id="fund-excel-archive" className="panel space-y-3">
      <h2 className={heading}>{tr('fundamentals.researchArchive')}</h2><p className="text-xs text-muted">{tr('fundamentals.researchArchiveNote')}</p>
      {archiveError && <p role="alert" className="text-red-400 text-xs">{archiveError}</p>}
      {!archive && !archiveError && <p className="text-muted text-xs">{tr('fundamentals.researchArchiveLoading')}</p>}
      {!!archive?.notices.length && <p className="text-amber text-xs">{tr('fundamentals.archiveUnavailable')}</p>}
      {archive && !archive.items.length && !archive.notices.length && <p className="text-muted text-xs">{tr('fundamentals.researchArchiveEmpty')}</p>}
      {archive?.items.map(item => <div key={item.id} className="flex items-center justify-between gap-3 border-t border-border py-2 text-xs">
        <div className="break-all"><span className="text-gold">{item.ticker || tr('fundamentals.f001')}</span> · {dateText(item.as_of, tr)} · {item.file}
          <p className="text-muted text-[10px] mt-1">{tr('fundamentals.researchHistorical')} · {item.integrity === 'recorded_hash' ? tr('fundamentals.researchArchiveVerified') : tr('fundamentals.researchArchiveUnattested')}</p></div>
        {item.available ? <button disabled={!!downloadBusy} onClick={() => void download(item)} className="text-cyan inline-flex items-center gap-1 shrink-0 disabled:opacity-50"><Download size={12}/>{tr('fundamentals.researchDownload')}</button>
          : <span className="text-amber">{tr('fundamentals.archiveUnavailable')}</span>}
      </div>)}
    </section>}
  </div>;
}
