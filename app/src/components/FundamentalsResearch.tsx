import { useEffect, useRef, useState, type ReactNode } from 'react';
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
const heading = 'fr-h';
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
  return refresh?.status === 'failed' ? <p className="fr-note is-warn" role="status">
    {tr('fundamentals.researchRefreshFailed')} · {dateText(refresh.last_attempt_at, tr)}
    {refresh.error && <> · {refresh.error}</>}
  </p> : null;
}

function Prose({text}: {text: string}) {
  return <div className="fr-prose">
    <ReactMarkdown components={{a: ({href, children}) => safeLink(href)
      ? <a href={href} target="_blank" rel="noopener noreferrer" className="fr-link">{children}</a>
      : <span>{children}</span>}}>{text}</ReactMarkdown>
  </div>;
}

function Sources({sources}: {sources: FundResearchSource[]}) {
  const tr = useT();
  return <section className="fr-sec"><h3 className={heading}>{tr('fundamentals.researchSources')}</h3>
    {!sources.length && <p className="fr-note">{tr('fundamentals.researchSourcesMissing')}</p>}
    {sources.map((source, index) => <div key={source.id || index} className="fr-source">
      {safeLink(source.url) ? <a href={safeLink(source.url)} target="_blank" rel="noopener noreferrer" className="fr-link">
        {source.source || source.metadata?.title || source.id || source.url}<ExternalLink size={12} aria-hidden="true"/></a>
        : <span>{source.source || source.id || tr('fundamentals.researchSourceUnavailable')}</span>}
      <div className="fr-note">{tr('fundamentals.researchProviderDate')}: {dateText(source.as_of || source.published_at, tr)}</div>
      {source.summary && <p className="fr-text">{source.summary}</p>}
    </div>)}
  </section>;
}

function Detail({company}: {company: FundResearchCompany}) {
  const tr = useT(), analysis = company.analysis, content = analysis.content;
  const judgment: Record<string, TranslationKey> = {favorable: 'fundamentals.researchFavorable', rejected: 'fundamentals.researchRejected', watch: 'fundamentals.researchWatch', incomplete: 'fundamentals.researchIncomplete'};
  const consensus = company.consensus;
  return <div className="fr-detail">
    <header className="fr-detail-head">
      <h2>{company.ticker} <span>{company.name}</span></h2>
      <div className="fr-note">{tr('fundamentals.researchAnalysisDate')}: {dateText(analysis.as_of, tr)}</div>
    </header>
    <section className="fr-sec">
      <h3 className={heading}>{tr('fundamentals.researchConsensus')}</h3>
      <p className="fr-note">{tr('fundamentals.researchConsensusNote')}</p>
      <div className="fr-facts num">
        <span className="fr-k">{tr('fundamentals.researchMean')}</span><span>{valueText(consensus.mean, tr)} {currencyText(consensus.mean, consensus.currency, tr)}</span>
        <span className="fr-k">{tr('fundamentals.researchMedian')}</span><span>{valueText(consensus.median, tr)}</span>
        <span className="fr-k">{tr('fundamentals.researchRange')}</span><span>{valueText(consensus.low, tr)} — {valueText(consensus.high, tr)}</span>
        <span className="fr-k">{tr('fundamentals.researchAnalysts')}</span><span>{valueText(consensus.number_of_analysts, tr, 0)}</span>
        <span className="fr-k">{tr('fundamentals.quoteSource')}</span><span>{consensus.source || tr('fundamentals.f001')}</span>
        <span className="fr-k">{tr('fundamentals.researchAcquired')}</span><span>{dateText(consensus.acquired_at, tr)}</span>
        <span className="fr-k">{tr('fundamentals.researchProviderDate')}</span><span>{dateText(consensus.data_as_of, tr)}</span>
      </div>
      {consensus.status !== 'available' && <p className="fr-note is-warn">{statusText(consensus.status, tr)}</p>}
      {consensus.reason && <p className="fr-note">{consensus.reason === 'market_observation_required'
        ? tr('fundamentals.researchMarketRequired') : consensus.reason}</p>}
      <RefreshFailure refresh={consensus.refresh}/>
      <p className="fr-note">{tr('fundamentals.researchFreshness')}</p>
      <p className="fr-note">{tr('fundamentals.researchPriceSource')}: {company.quote.source || tr('fundamentals.f001')} · {dateText(company.quote.observed_at, tr)}</p>
      {company.quote.acquired_at && <p className="fr-note">{tr('fundamentals.researchPriceAcquired')}: {dateText(company.quote.acquired_at, tr)}</p>}
      <RefreshFailure refresh={company.quote.refresh}/>
    </section>
    <section className="fr-sec">
      <h3 className={heading}>{tr('fundamentals.researchAiOpinion')}</h3>
      <p className="fr-note">{tr('fundamentals.researchAiEstimatesNote')}</p>
      {analysis.status !== 'available' ? <p className="fr-note">{statusText(analysis.status, tr)}</p> : <>
        <p className="fr-note">{analysis.origin === 'weekly' ? tr('fundamentals.researchWeekly') : tr('fundamentals.researchTradeIdea')}
          {analysis.technical_status && <> · {analysis.technical_status === 'completed' ? tr('fundamentals.researchCompleted') : tr('fundamentals.researchRunIncomplete')}</>}</p>
        {analysis.judgment && <div className="fr-judgment">{judgment[analysis.judgment] ? tr(judgment[analysis.judgment]) : analysis.judgment}</div>}
        {analysis.scope === 'whole_committee_report' && <p className="fr-callout is-warn">{tr('fundamentals.researchWholeReport')}</p>}
        {analysis.summary && <Prose text={analysis.summary}/>}
        {content?.report && <Prose text={content.report}/>}
      </>}
    </section>
    {content?.dossier?.map(section => <section key={section.key} className="fr-sec">
      <h3 className={heading}>{section.title}</h3>
      {section.paragraphs.map((paragraph, index) => <Prose key={index} text={paragraph}/>)}
      {section.tables?.map((table, index) => <div key={index} className="fr-dossier-table">
        <p className="fr-note">{table.title} · {table.unit} · {table.period}</p>
        <table className="fr-mini-table num"><thead><tr>{table.columns.map((column, i) => <th key={i}>{column}</th>)}</tr></thead>
          <tbody>{table.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c}>{cell}</td>)}</tr>)}</tbody></table>
        <p className="fr-note">{tr('fundamentals.quoteSource')} {table.source}</p>
      </div>)}
    </section>)}
    {!!content?.scenarios?.length && <section className="fr-sec"><h3 className={heading}>{tr('fundamentals.researchScenarios')}</h3>
      {content.scenarios.map((scenario, index) => <div key={index} className="fr-scenario"><h4>{scenario.name}</h4><Prose text={scenario.analysis}/></div>)}
    </section>}
    {([
      ['fundamentals.researchRisks', content?.risks], ['fundamentals.researchCatalysts', content?.catalysts],
      ['fundamentals.researchInvalidation', content?.invalidation], ['fundamentals.researchMonitor', content?.review_conditions],
      ['fundamentals.researchGaps', content?.data_gaps?.map(gap => typeof gap === 'string' ? gap : gap.reason || tr('fundamentals.f001'))],
    ] as [TranslationKey, string[] | undefined][]).map(([key, values]) => !!values?.length && <section key={key} className="fr-sec">
      <h3 className={heading}>{tr(key)}</h3><ul className="fr-list">{values.map((value, index) => <li key={index}>{value}</li>)}</ul>
    </section>)}
    {analysis.status === 'available' && <Sources sources={content?.evidence || content?.documents || []}/>}
  </div>;
}

// selectedExtra: optional panel shown under the detail of the selected company
// (the Fund page uses it for the per-ticker filing summary).
export default function FundamentalsResearch({ selectedExtra }: { selectedExtra?: (ticker: string) => ReactNode } = {}) {
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

  const refreshStatus = list?.market_refresh?.status;
  return <div className="fr-root">
    <header className="fr-head">
      <div className="fr-title"><h1>{tr('fundamentals.researchTitle')}</h1>
        <p>{tr('fundamentals.researchSubtitle')}</p></div>
      <div className="fr-actions"><button type="button" className="bbn-btn"
        aria-label={tr('fundamentals.researchReadAgain')} title={tr('fundamentals.researchReadAgainHint')}
        disabled={listReading || detailReading} onClick={() => { readList.current(); readDetail.current(); }}>
        <RefreshCw size={15} aria-hidden="true"/>{tr('fundamentals.researchReadAgain')}</button>
      <button type="button" className="bbn-btn" aria-controls="fund-excel-archive" aria-expanded={archiveOpen}
        onClick={() => setArchiveOpen(open => !open)}><FileSpreadsheet size={15} aria-hidden="true"/>{tr('fundamentals.researchArchive')}</button>
      </div>
    </header>
    <p className="fr-note">{tr('fundamentals.researchAutoUpdate')}</p>
    {list?.market_refresh && <p className={'fr-note' + (['error', 'waiting'].includes(list.market_refresh.status) ? ' is-warn' : '')} role="status">
      {tr(refreshStatus === 'error' ? 'fundamentals.researchRefreshFailed'
        : refreshStatus === 'waiting' ? 'fundamentals.researchRefreshWaiting'
        : ['stopped', 'stopping'].includes(list.market_refresh.status) ? 'fundamentals.researchRefreshStopped'
        : ['starting', 'refreshing'].includes(list.market_refresh.status) ? 'fundamentals.researchRefreshRunning' : 'fundamentals.researchRefreshActive')}
      {list.market_refresh.status === 'waiting' && list.market_refresh.next_retry_at && <>
        {' · '}{tr('fundamentals.researchNextRetry')}: {dateText(list.market_refresh.next_retry_at, tr)}</>}
      {list.market_refresh.last_completed_at && <> · {tr('fundamentals.researchLastCycle')}: {dateText(list.market_refresh.last_completed_at, tr)}</>}
      {list.market_refresh.error && !/^market_update_[a-z_]+$/.test(list.market_refresh.error) && <> · {list.market_refresh.error}</>}
      {!!list.market_refresh.notices?.length && <span title={list.market_refresh.notices.join(' | ')}>
        {' · '}{tr('fundamentals.researchRefreshNotices')}: {list.market_refresh.notices.length}</span>}
    </p>}
    {error && <div role="alert" className="fr-callout is-bad">{error}</div>}
    {!list && !error && <p className="fr-note">{tr('fundamentals.researchLoading')}</p>}
    {!!list?.notices.length && <p className="fr-note is-warn">{tr('fundamentals.researchPartial')}</p>}
    {list && <div className="fr-grid">
      <section className="bbn-card fr-list-card">
        <div className="fr-table-wrap">
          <table className="fr-table num"><thead><tr>
            {[tr('fundamentals.researchCompany'), tr('fundamentals.researchPrice'), tr('fundamentals.researchConsensus'), tr('fundamentals.researchUpside')].map(label => <th key={label}>{label}</th>)}
          </tr></thead><tbody>{list.items.map(item => <tr key={item.ticker} className={item.ticker === selected ? 'is-on' : undefined}>
            <td><button type="button" className="fr-ticker" aria-label={item.ticker} aria-pressed={item.ticker === selected} onClick={() => setSelected(item.ticker)}>{item.ticker}</button>
              <div className="fr-name" title={item.name || ''}>{item.name}</div></td>
            <td>{valueText(item.quote.value, tr)} <span className="fr-ccy">{currencyText(item.quote.value, item.quote.currency, tr)}</span>
              {item.quote.status !== 'available' && <div className="fr-cell-note is-warn">{statusText(item.quote.status, tr)}</div>}</td>
            <td>{valueText(item.consensus.mean, tr)} <span className="fr-ccy">{currencyText(item.consensus.mean, item.consensus.currency, tr)}</span>
              {item.consensus.status !== 'available' && <div className="fr-cell-note is-warn">{statusText(item.consensus.status, tr)}</div>}</td>
            <td className={item.comparison.status === 'available' && finite(item.comparison.upside_pct)
              ? item.comparison.upside_pct > 0 ? 'is-up' : item.comparison.upside_pct < 0 ? 'is-down' : undefined : undefined}>
              {item.comparison.status === 'available' && finite(item.comparison.upside_pct)
              ? `${item.comparison.upside_pct > 0 ? '+' : ''}${fmtNum(item.comparison.upside_pct, 1)}%` : tr('fundamentals.f001')}
              {item.comparison.status !== 'available' && <div className="fr-cell-note">{statusText(item.comparison.status, tr)}</div>}</td>
          </tr>)}</tbody></table>
        </div>
        {!list.items.length && <p className="bbn-empty">{tr('fundamentals.researchEmpty')}</p>}
      </section>
      <aside className="bbn-card fr-detail-card">
        {detailError && <p role="alert" className="fr-callout is-bad">{detailError}</p>}
        {detail?.ticker === selected ? <Detail company={detail}/> : !detailError && <p className="fr-note">{selected ? tr('fundamentals.researchDetailLoading') : tr('fundamentals.f019')}</p>}
        {selected && <div className="fr-extra">{selectedExtra?.(selected)}</div>}
      </aside>
    </div>}
    {archiveOpen && <section id="fund-excel-archive" className="bbn-card fr-archive">
      <header className="bbn-card-head"><h2>{tr('fundamentals.researchArchive')}</h2></header>
      <div className="fr-archive-body">
        <p className="fr-note">{tr('fundamentals.researchArchiveNote')}</p>
        {archiveError && <p role="alert" className="fr-callout is-bad">{archiveError}</p>}
        {!archive && !archiveError && <p className="fr-note">{tr('fundamentals.researchArchiveLoading')}</p>}
        {!!archive?.notices.length && <p className="fr-note is-warn">{tr('fundamentals.archiveUnavailable')}</p>}
        {archive && !archive.items.length && !archive.notices.length && <p className="fr-note">{tr('fundamentals.researchArchiveEmpty')}</p>}
        {archive?.items.map(item => <div key={item.id} className="fr-archive-row">
          <div className="fr-archive-file"><b>{item.ticker || tr('fundamentals.f001')}</b> · {dateText(item.as_of, tr)} · {item.file}
            <p className="fr-note">{tr('fundamentals.researchHistorical')} · {item.integrity === 'recorded_hash' ? tr('fundamentals.researchArchiveVerified') : tr('fundamentals.researchArchiveUnattested')}</p></div>
          {item.available ? <button type="button" disabled={!!downloadBusy} onClick={() => void download(item)} className="bbn-btn"><Download size={15} aria-hidden="true"/>{tr('fundamentals.researchDownload')}</button>
            : <span className="bbn-warn-pill">{tr('fundamentals.archiveUnavailable')}</span>}
        </div>)}
      </div>
    </section>}
  </div>;
}
