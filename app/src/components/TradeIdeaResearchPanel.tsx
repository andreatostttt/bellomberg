import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import { useLingua, useT } from '@/i18n/provider';
import { localeDi } from '@/i18n/lingua';
import { research as labels } from '@/i18n/it/tradeideaResearch';
import { leggiNumeroConSegno } from '@/lib/cassa';
import { TradeIdeas, type TradeIdeaRun } from '@/lib/tradeIdeas';
import { TradeIdeaResearch, researchArtifactBlob, type ResearchEvent, type ResearchObject, type ResearchState, type ResearchExportPreview } from '@/lib/tradeIdeaResearch';

type Label = keyof typeof labels;
type Tab = 'tools' | 'follow' | 'earnings' | 'delivery';
const object = (value: unknown): ResearchObject => value && typeof value === 'object' && !Array.isArray(value) ? value as ResearchObject : {};
const rows = (value: unknown): ResearchObject[] => Array.isArray(value) ? value.map(object) : [];
const words = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const failure = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function TradeIdeaResearchPanel({ runId }: { runId: string }) {
  const t = useT(), language = useLingua(), locale = localeDi(language);
  const tr = (key: Label) => t(`tradeIdeaResearch.${key}`);
  const includedLabels: Record<string, Label> = {'Public economic model':'publicModel','Model-derived PDF extract':'modelExtract','Public sources':'publicSources','Version inventory':'versionInventory'};
  const [state, setState] = useState<ResearchState | null>(null), [tab, setTab] = useState<Tab>('tools');
  const [generation, setGeneration] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [otherRuns, setOtherRuns] = useState<TradeIdeaRun[]>([]), [otherRun, setOtherRun] = useState(''), [otherGeneration, setOtherGeneration] = useState('');
  const [question, setQuestion] = useState(''), [input, setInput] = useState(''), [value, setValue] = useState('');
  const [valueLanguage, setValueLanguage] = useState(language), [variantLabel, setVariantLabel] = useState(''), [rationale, setRationale] = useState('');
  const [metric, setMetric] = useState('price'), [operator, setOperator] = useState('lt'), [threshold, setThreshold] = useState(''), [activated, setActivated] = useState(false);
  const [objection, setObjection] = useState(''), [reply, setReply] = useState(''), [replyId, setReplyId] = useState(''), [replyStatus, setReplyStatus] = useState('open');
  const [eventDate, setEventDate] = useState(''), [preparationId, setPreparationId] = useState(''), [metricIndex, setMetricIndex] = useState('0');
  const [observed, setObserved] = useState(''), [source, setSource] = useState(''), [published, setPublished] = useState(''), [revise, setRevise] = useState(false);
  const [sourceEvent, setSourceEvent] = useState('');
  const [category, setCategory] = useState('assumption'), [evidenceEvent, setEvidenceEvent] = useState(''), [errorRationale, setErrorRationale] = useState('');
  const [preview, setPreview] = useState<ResearchExportPreview | null>(null), [acknowledged, setAcknowledged] = useState(false);
  const lock = useRef(false), pending = useRef<{signature: string; id: string} | null>(null), latestRun = useRef(runId), loadSequence = useRef(0);
  latestRun.current = runId;
  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    const fresh = await TradeIdeaResearch.view(runId, generation || undefined);
    if (latestRun.current === runId && sequence === loadSequence.current) setState(fresh);
  }, [runId, generation]);
  useEffect(() => { let active = true; setError(''); void load().catch(e => { if (active) setError(failure(e)); }); return () => { active = false; }; }, [load]);
  useEffect(() => {
    setState(null); setGeneration(''); setPreview(null); setAcknowledged(false); pending.current = null;
    setQuestion(''); setInput(''); setValue(''); setOtherRun(''); setOtherGeneration(''); setReplyId(''); setPreparationId(''); setSourceEvent('');
    void TradeIdeas.list({limit: 100}).then(list => setOtherRuns(list.runs)).catch(e => setError(failure(e)));
  }, [runId]);
  const inputs = state?.model.editable || [];
  const selected = inputs.find(row => `${row.scenario}:${row.driver}` === input);
  const modelReady = state?.model.status === 'ready';
  const selectedGeneration = generation || state?.model.generation_id || '';
  const format = (item: unknown): string => {
    if (item == null) return tr('missing');
    if (typeof item === 'number') return new Intl.NumberFormat(locale, {maximumFractionDigits: 6}).format(item);
    if (typeof item === 'string') return item;
    if (typeof item === 'boolean') return String(item);
    if (Array.isArray(item)) return item.map(format).join('; ');
    return Object.entries(object(item)).map(([key, val]) => `${key}: ${format(val)}`).join(' · ');
  };
  const status = (item: unknown) => typeof item === 'string' && Object.prototype.hasOwnProperty.call(labels, item)
    ? tr(item as Label) : format(item);
  const metricName = (item: unknown) => {
    if(typeof item!=='string')return format(item);
    const parts=item.split('.'), leaf=parts.at(-1)!;
    return parts.length>2?`${status(leaf)} · ${parts.slice(1,-1).join('.')}`:status(item);
  };
  const versionName = (id:unknown) => {
    const index=state?.versions.findIndex(version=>version.generation_id===id)??-1;
    const version=index>=0?state?.versions[index]:null;
    return version?`${status(version.label)} ${index+1} · ${version.valuation_date}`:tr('unavailable');
  };
  const number = (text: string) => {
    const parsed = leggiNumeroConSegno(text, valueLanguage, language);
    if (!parsed || !parsed.ok) throw new Error(parsed && !parsed.ok ? parsed.motivo : tr('value'));
    return parsed.valore;
  };
  const changes = () => {
    if (!selected) throw new Error(tr('noInputs'));
    return [{scenario: selected.scenario, driver: selected.driver,
      value: Array.isArray(selected.value) ? value.split(';').map(number) : number(value)}];
  };
  const act = async (kind: string, data: ResearchObject) => {
    if (lock.current || !selectedGeneration) return;
    lock.current = true; setBusy(true); setError('');
    const signature = JSON.stringify([runId, selectedGeneration, kind, data]);
    if (pending.current?.signature !== signature) pending.current = {signature, id: crypto.randomUUID()};
    try {
      await TradeIdeaResearch.act(runId, selectedGeneration, kind, data, pending.current.id);
      pending.current = null;
      await load();
    } catch (e) { if (latestRun.current === runId) {setError(failure(e)); await load().catch(loadError=>setError(failure(e)+'; '+failure(loadError)));} }
    finally { lock.current = false; setBusy(false); }
  };
  const safely = (operation: () => void | Promise<void>) => { try { void operation(); } catch (e) { setError(failure(e)); } };
  const recover = async (requestId:string) => {
    if(lock.current)return;
    lock.current=true;setBusy(true);setError('');
    try {await TradeIdeaResearch.recover(runId,requestId);await load();}
    catch(e){setError(failure(e));}
    finally{lock.current=false;setBusy(false);}
  };
  const chooseInput = (key: string) => {
    setInput(key); const row = inputs.find(item => `${item.scenario}:${item.driver}` === key);
    setValue(row ? (Array.isArray(row.value) ? row.value : [row.value]).map(item => String(item).replace('.', language === 'it' ? ',' : '.')).join('; ') : '');
    setValueLanguage(language);
  };
  const download = async (event: ResearchEvent) => {
    try {
      const blob = await researchArtifactBlob(event), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = event.artifact!.name.replace(/[\\/]/g, '_'); document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError(failure(e)); }
  };
  const preparations = (state?.history || []).filter(event => event.kind === 'earnings_prepare' && event.generation_id === selectedGeneration);
  const preparation = preparations.find(event => String(event.id) === preparationId);
  const expectations = rows(preparation?.data.expectations), expectation = expectations[Number(metricIndex)];
  const monitors = (state?.history || []).filter(event => event.kind === 'monitor' && event.generation_id === selectedGeneration);
  const observedEvents = (state?.history || []).filter(event => ['refresh', 'earnings_review', 'monitor_check'].includes(event.kind));
  const button = (label: Label, fn: () => void, disabled = false) => <button type="button" className="ti-button ti-primary" disabled={busy || disabled} onClick={fn}>{tr(label)}</button>;
  const catalogs = (state?.history || []).filter(event=>event.kind==='acquire_sources' && event.generation_id===selectedGeneration && event.data.status==='ready');
  const selectedCatalog = catalogs.find(event=>String(event.id)===sourceEvent);
  const sourceChooser = <div className="ti-research-wide"><h4>{tr('sourceCatalog')}</h4><p>{tr('sourceHint')}</p>{button('acquireSources',()=>void act('acquire_sources',{}),!modelReady)}<label>{tr('sourceCatalog')}<select value={selectedCatalog?sourceEvent:''} onChange={e=>setSourceEvent(e.target.value)}><option value="">{tr('choose')}</option>{catalogs.map(event=><option value={event.id} key={event.id}>{format(event.data.acquired_as_of)} · {rows(event.data.documents).length}</option>)}</select></label>{selectedCatalog && <ul>{rows(selectedCatalog.data.documents).map((document,i)=><li key={i}>{format(document.published_at)} · {format(document.url)}</li>)}</ul>}</div>;
  const table = (headers: Label[], content: unknown[][]) => <div className="ti-table-wrap"><table><thead><tr>{headers.map((label,i) => <th key={i} scope="col">{tr(label)}</th>)}</tr></thead><tbody>{content.map((row,i) => <tr key={i}>{row.map((cell,j) => <td key={j}>{format(cell)}</td>)}</tr>)}</tbody></table></div>;
  const resultView = (event: ResearchEvent) => {
    const data = event.data, model = object(data.model);
    return <>
      <p className="ti-research-result-status">{status(data.status)}</p>
      {typeof data.answer === 'string' && <p className="ti-research-answer">{data.answer}</p>}
      {rows(data.citations).map((cite,i) => <p className="ti-field-note" key={i}>{format(cite.source)}{cite.sheet ? ` · ${format(cite.sheet)}!${format(cite.cell)}` : ''}{cite.driver ? ` · ${format(cite.driver)}` : ''}{cite.as_of ? ` · ${format(cite.as_of)}` : ''}{typeof cite.url === 'string' && /^https?:\/\//.test(cite.url) && <> · <a href={cite.url} target="_blank" rel="noreferrer">{tr('source')}</a></>}</p>)}
      {data.reason != null && <p>{format(data.reason)}</p>}
      {data.model != null && table(['method','date','currency','fairValue'], [[state?.model.method,model.valuation_date,model.currency,model.fair_value_base]])}
      {rows(data.changes).length > 0 && table(['driver','before','afterValue','unit'],rows(data.changes).map(row => [row.driver,row.before,row.after,row.unit]))}
      {rows(data.values).length > 0 && table(['result','before','afterValue','delta','unit','status'],rows(data.values).map(row => [row.scenario,row.before,row.after,row.delta,row.unit,status(row.comparability)]))}
      {rows(data.drivers).length > 0 && table(['driver','before','afterValue','delta','unit','status'],rows(data.drivers).map(row => [row.driver,row.normalized_before,row.normalized_after,row.delta,row.unit,status(row.comparability)]))}
      {data.analysis != null && <details><summary>{tr('details')}</summary>{Object.entries(object(data.analysis)).map(([key,item]) => <section key={key}><h5>{key}</h5><p>{tr('before')}: {format(object(item).before)}</p><p>{tr('afterValue')}: {format(object(item).after)}</p></section>)}</details>}
      {rows(data.ideas).map((idea,i) => <article className="ti-research-idea" key={i}><h4>{format(idea.ticker)} · {status(idea.judgment)}</h4><p>{format(idea.summary)}</p>{table(['method','price','date','currency'],[[object(idea.valuation).method,object(idea.valuation).price,object(idea.valuation).price_date,object(idea.valuation).currency]])}<p>{format(idea.catalysts)}</p><p>{format(idea.risks)}</p><p className="ti-field-note">{format(object(idea.book_compatibility).reason)}</p></article>)}
      {data.comparability != null && <p className="ti-field-note">{format(data.comparability)}</p>}
      {event.kind === 'refresh' && data.kind === 'price' && table(['before','afterValue','delta','date'],[[object(data.delta).before,object(data.delta).after,object(data.delta).change,object(data.delta).after_date]])}
      {data.source_refresh != null && <><p>{format(object(data.source_refresh).reasons)}</p>{table(['driver','before','afterValue','unit'],rows(object(data.source_refresh).changes).map(row=>[row.driver,row.before_value,row.after_value,row.unit]))}</>}
      {event.kind === 'acquire_sources' && <ul>{rows(data.documents).map((document,i)=><li key={i}>{format(document.published_at)} · {format(document.url)}</li>)}</ul>}
      {event.kind === 'monitor_check' && <p>{format(data.value)} · {format(data.observed_at)} · {format(data.source)}</p>}
      {rows(data.expectations).length > 0 && table(['driver','period','expected','unit','modelCell'],rows(data.expectations).map(row => [metricName(row.driver),row.period,row.value,row.unit,row.cell]))}
      {words(data.reasons).length > 0 && <p className="ti-field-note">{words(data.reasons).join('; ')}</p>}
      {rows(data.metrics_unavailable).length > 0 && <details><summary>{tr('unavailableMetrics')}</summary><ul>{rows(data.metrics_unavailable).map((row,i)=><li key={i}>{metricName(row.driver)}: {format(row.reason)}</li>)}</ul></details>}
      {data.consensus_status != null && <p className="ti-field-note">{tr('expectationsHint')}</p>}
      {rows(data.differences).length > 0 && table(['driver','period','expected','observed','delta','unit'],rows(data.differences).map(row => [metricName(row.driver),row.period,row.expected,row.observed,row.delta,row.unit]))}
      {rows(object(data.model_impact).values).length > 0 && table(['result','before','afterValue','delta','unit'],rows(object(data.model_impact).values).map(row => [row.scenario,row.before,row.after,row.delta,row.unit]))}
      {data.categories != null && <ul>{Object.entries(object(data.categories)).map(([key,entry]) => <li key={key}>{status(key)}: {status(object(entry).status)}</li>)}</ul>}
      {typeof data.matched_observations === 'number' && <p>{tr('matchedObservations')}: {format(data.matched_observations)}</p>}
      {rows(data.findings).length > 0 && table(['version','category','driver','expected','observed','delta'],rows(data.findings).map(row => [versionName(row.generation_id),status(row.category),metricName(object(row.evidence).driver),object(row.evidence).expected,object(row.evidence).observed,object(row.evidence).delta]))}
      {data.category != null && <p>{status(data.category)} · {format(data.rationale)}</p>}
      {data.text != null && <p>{format(data.text)}</p>}
      {rows(object(data.issues).reasons).length > 0 && <p>{format(object(data.issues).reasons)}</p>}
      {event.artifact && <button type="button" className="ti-button ti-quiet" onClick={() => void download(event)}><Download size={12}/>{tr('download')} · {event.artifact.kind.toUpperCase()}</button>}
    </>;
  };
  return <section className="ti-slab ti-research" aria-labelledby="ti-research-title">
    <div className="ti-slab-head"><div><h3 id="ti-research-title">{tr('title')}</h3><p>{tr('subtitle')}</p></div><button type="button" className="ti-button ti-quiet" aria-label={t('tradeidea.refresh')} disabled={busy} onClick={() => void load().catch(e => setError(failure(e)))}><RefreshCw size={14}/></button></div>
    {error && <div role="alert" className="ti-notice ti-error">{error}</div>}
    {busy && <p role="status">{tr('busy')}</p>}
    {state?.conclusions_status === 'review_required' && <p className="ti-notice">{tr('reviewRequired')}</p>}
    {state?.model.kind === 'exposure' && <p className="ti-field-note">{tr('exposureOnly')}</p>}
    {state && <label className="ti-research-field">{tr('version')}<select value={selectedGeneration} onChange={e => {setGeneration(e.target.value);setInput('');setValue('');setPreparationId('');setSourceEvent('');setPreview(null);setAcknowledged(false);}}>{state.versions.map((version,i) => <option key={version.generation_id} value={version.generation_id}>{status(version.label)} {i+1} · {version.valuation_date}</option>)}</select></label>}
    {!modelReady && <p className="ti-notice">{tr('unavailable')}: {state?.model.reason || tr('missing')}</p>}
    <div className="ti-research-tabs" role="tablist">{(['tools','follow','earnings','delivery'] as const).map(key => <button key={key} id={`ti-research-tab-${key}`} role="tab" aria-selected={tab===key} aria-controls={`ti-research-${key}`} tabIndex={tab===key?0:-1} onClick={() => setTab(key)} onKeyDown={e => {if (e.key==='ArrowRight'||e.key==='ArrowLeft') {e.preventDefault();const tabs:Tab[]=['tools','follow','earnings','delivery'];const next=tabs[(tabs.indexOf(key)+(e.key==='ArrowRight'?1:3))%4];setTab(next);document.getElementById(`ti-research-tab-${next}`)?.focus();}}}>{tr(key)}</button>)}</div>
    <div id={`ti-research-${tab}`} role="tabpanel" aria-labelledby={`ti-research-tab-${tab}`}>
      {tab==='tools' && <div className="ti-research-grid">
        <form onSubmit={e => {e.preventDefault();void act('question',{question});}}><label>{tr('question')}<textarea value={question} onChange={e => setQuestion(e.target.value)} rows={3} maxLength={3000}/></label><p className="ti-field-note">{tr('evidenceOnly')}</p>{button('ask',()=>void act('question',{question}),!modelReady||!question.trim())}</form>
        <div><h4>{tr('simulate')}</h4>{inputs.length ? <><label>{tr('input')}<select value={input} onChange={e=>chooseInput(e.target.value)}><option value="">{tr('choose')}</option>{inputs.map(row=><option key={`${row.scenario}:${row.driver}`} value={`${row.scenario}:${row.driver}`}>{row.scenario} · {row.driver} · {row.unit}</option>)}</select></label><label>{tr('value')}<input type="text" inputMode="decimal" value={value} onChange={e=>{setValue(e.target.value);setValueLanguage(language);}}/></label><p className="ti-field-note">{tr('pathHelp')}{selected && ` ${selected.unit} · ${selected.period}`}</p><label>{tr('label')}<input value={variantLabel} onChange={e=>setVariantLabel(e.target.value)} maxLength={120}/></label><label>{tr('rationale')}<textarea value={rationale} onChange={e=>setRationale(e.target.value)} rows={2}/></label>{button('simulate',()=>safely(()=>act('simulate',{label:variantLabel,rationale,changes:changes()})),!modelReady||!selected||!rationale.trim()||!variantLabel.trim())}</> : <p>{tr('noInputs')}</p>}</div>
        <div className="ti-research-wide"><h4>{tr('compare')}</h4><label>{tr('otherRun')}<select value={otherRun} onChange={e=>{setOtherRun(e.target.value);setOtherGeneration('');}}><option value="">{tr('choose')}</option>{otherRuns.map(run=><option value={run.id} key={run.id}>{run.ticker} · {run.created_at?.slice(0,10)} · {run.technical_status}</option>)}</select></label>{otherRun===runId && <label>{tr('version')}<select value={otherGeneration} onChange={e=>setOtherGeneration(e.target.value)}><option value="">{tr('committee')}</option>{state?.versions.map((v,i)=><option value={v.generation_id} key={v.generation_id}>{status(v.label)} {i+1} · {v.valuation_date}</option>)}</select></label>}<div className="ti-detail-actions">{button('compareRuns',()=>void act('compare_runs',{other_run_id:otherRun,other_generation_id:otherGeneration||undefined}),!modelReady||!otherRun)}{button('compareIdeas',()=>void act('compare_ideas',{run_ids:[otherRun]}),!modelReady||!otherRun)}</div></div>
      </div>}
      {tab==='follow' && <div className="ti-research-grid">
        {sourceChooser}<div className="ti-research-wide">{button('refreshDocuments',()=>void act('refresh',{kind:'documents',source_event_id:Number(sourceEvent)}),!modelReady||!selectedCatalog)}</div>
        <div><h4>{tr('refresh')}</h4><p>{tr('refreshHint')}</p>{button('refresh',()=>void act('refresh',{kind:'price'}),!modelReady)}</div>
        <div><h4>{tr('monitor')}</h4><label>{tr('metric')}<select value={metric} onChange={e=>setMetric(e.target.value)}><option value="price">{tr('price')}</option><option value="fair_value_base">{tr('fairValue')}</option></select></label><label>{tr('operator')}<select value={operator} onChange={e=>setOperator(e.target.value)}><option value="lt">{tr('below')}</option><option value="gt">{tr('above')}</option></select></label><label>{tr('threshold')}<input type="text" inputMode="decimal" value={threshold} onChange={e=>{setThreshold(e.target.value);setValueLanguage(language);}}/></label><label className="ti-check"><input type="checkbox" checked={activated} onChange={e=>setActivated(e.target.checked)}/>{tr('activate')}</label><p className="ti-field-note">{tr('monitorHint')}</p>{button('saveMonitor',()=>safely(()=>act('monitor',{metric,operator,threshold:number(threshold),active:activated})),!modelReady||!activated)}{monitors.map(m=><div key={m.id}><p>{format(m.data.metric)} {format(m.data.operator)} {format(m.data.threshold)} {format(m.data.currency)}</p>{button('checkMonitor',()=>void act('monitor_check',{monitor_id:m.id}),!modelReady)}</div>)}</div>
        <div><label>{tr('objection')}<textarea value={objection} onChange={e=>setObjection(e.target.value)} rows={3} maxLength={6000}/></label>{button('saveObjection',()=>void act('objection',{text:objection}),!modelReady||!objection.trim())}</div>
        <div><h4>{tr('priorObjections')}</h4>{state?.objections.map(row=><article key={row.id} className="ti-objection"><strong>{status(row.status)}</strong><p>{row.objection}</p>{row.responses.map(response=><blockquote key={response.id}>{response.text}</blockquote>)}</article>)}<label>{tr('objection')}<select value={replyId} onChange={e=>setReplyId(e.target.value)}><option value="">{tr('choose')}</option>{state?.objections.filter(row=>row.run_id===runId).map(row=><option key={row.id} value={row.id}>{row.objection.slice(0,100)}</option>)}</select></label><label>{tr('response')}<textarea value={reply} onChange={e=>setReply(e.target.value)} rows={2}/></label><label>{tr('status')}<select value={replyStatus} onChange={e=>setReplyStatus(e.target.value)}>{(['open','answered','resolved'] as const).map(s=><option key={s} value={s}>{tr(s)}</option>)}</select></label>{button('reply',()=>void act('objection_reply',{objection_id:Number(replyId),text:reply,status:replyStatus}),!modelReady||!replyId||!reply.trim())}</div>
      </div>}
      {tab==='earnings' && <div className="ti-research-grid">
        {sourceChooser}
        <div><h4>{tr('prepare')}</h4><label>{tr('eventDate')}<input type="date" value={eventDate} onChange={e=>setEventDate(e.target.value)}/></label><p>{tr('expectationsHint')}</p>{button('freeze',()=>void act('earnings_prepare',{event_date:eventDate}),!modelReady||!eventDate)}</div>
        <div><h4>{tr('after')}</h4><label>{tr('preparation')}<select value={preparationId} onChange={e=>{setPreparationId(e.target.value);setMetricIndex('0');}}><option value="">{tr('choose')}</option>{preparations.map(p=><option key={p.id} value={p.id}>{format(p.data.event_date)} · {p.at.slice(0,10)}</option>)}</select></label><label>{tr('observation')}<select value={metricIndex} onChange={e=>setMetricIndex(e.target.value)}>{expectations.map((row,i)=><option value={i} key={i}>{metricName(row.driver)} · {format(row.period)} · {format(row.unit)}</option>)}</select></label><label>{tr('observed')}<input type="text" inputMode="decimal" value={observed} onChange={e=>{setObserved(e.target.value);setValueLanguage(language);}}/></label><label>{tr('source')}<input type="url" value={source} onChange={e=>setSource(e.target.value)}/></label><label>{tr('published')}<input type="date" value={published} onChange={e=>setPublished(e.target.value)}/></label><label className="ti-check"><input type="checkbox" checked={revise} onChange={e=>setRevise(e.target.checked)}/>{tr('reviseForward')}</label>{revise && <><label>{tr('input')}<select value={input} onChange={e=>chooseInput(e.target.value)}><option value="">{tr('choose')}</option>{inputs.map(row=><option key={`${row.scenario}:${row.driver}`} value={`${row.scenario}:${row.driver}`}>{row.scenario} · {row.driver} · {row.unit}</option>)}</select></label><label>{tr('value')}<input type="text" inputMode="decimal" value={value} onChange={e=>{setValue(e.target.value);setValueLanguage(language);}}/></label><p className="ti-field-note">{tr('pathHelp')}{selected && ` ${selected.unit} · ${selected.period}`}</p><label>{tr('rationale')}<textarea value={rationale} onChange={e=>setRationale(e.target.value)} rows={2}/></label></>}{button('review',()=>safely(()=>act('earnings_review',{preparation_id:Number(preparationId),source_event_id:Number(sourceEvent),observations:[{...expectation,value:number(observed),source,published_at:published}],...(revise?{changes:changes(),rationale}:{})})),!modelReady||!expectation||!source||!published||!selectedCatalog||(revise&&(!selected||!rationale.trim())))}</div>
        <div className="ti-research-wide"><h4>{tr('errors')}</h4>{button('reviewErrors',()=>void act('error_review',{}),!modelReady)}<p>{tr('attributionHint')}</p><label>{tr('category')}<select value={category} onChange={e=>setCategory(e.target.value)}>{(['data','assumption','timing','judgment'] as const).map(key=><option key={key} value={key}>{tr(key)}</option>)}</select></label><label>{tr('evidenceEvent')}<select value={evidenceEvent} onChange={e=>setEvidenceEvent(e.target.value)}><option value="">{tr('choose')}</option>{observedEvents.map(event=><option key={event.id} value={event.id}>{event.at.slice(0,10)} · {event.kind} · {status(event.data.status)}</option>)}</select></label><label>{tr('rationale')}<textarea value={errorRationale} onChange={e=>setErrorRationale(e.target.value)} rows={2}/></label>{button('attribute',()=>void act('error_attribution',{category,evidence_event_id:Number(evidenceEvent),rationale:errorRationale}),!modelReady||!evidenceEvent||!errorRationale.trim())}</div>
      </div>}
      {tab==='delivery' && <div>
        <h4>{tr('costs')}</h4>{state?.cost && <>{table(['charged','reserved','remaining','unresolved'],[[state.cost.charged_usd,state.cost.reserved_usd,state.cost.remaining_known_usd,state.cost.unknown_requests]])}{table(['phase','charged','reserved','unresolved','cache'],state.cost.phases.map(phase=>[phase.phase,phase.charged_usd,phase.reserved_usd,phase.unknown_requests,phase.cache_read_tokens]))}{state.cost.unresolved.map(row=><p key={row.request_id}>{row.role} · {row.status} · {row.reason}</p>)}<p className="ti-field-note">{tr('deterministic')}: {Object.values(state.cost.deterministic_actions).reduce((sum,x)=>sum+x,0)} · {tr('evidenceOnly')}</p></>}
        <h4>{tr('exports')}</h4><p>{tr('exportHint')}</p><div className="ti-detail-actions">{button('private',()=>void act('export',{scope:'private'}),!modelReady)}{button('preview',()=>{setAcknowledged(false);setError('');void TradeIdeaResearch.preview(runId).then(setPreview).catch(e=>setError(failure(e)));},!modelReady)}</div>
        {preview && <div className="ti-notice"><strong>{status(preview.status)}</strong><p>{tr('sharedLimitation')}</p><h5>{tr('included')}</h5><ul>{preview.included_sections.map(item=><li key={item}>{includedLabels[item]?tr(includedLabels[item]):item}</li>)}</ul><h5>{tr('exclusions')}</h5><ul>{preview.exclusions.map(item=><li key={item.category}>{Object.prototype.hasOwnProperty.call(labels,item.category)?tr(item.category as Label):item.label} · {item.count}</li>)}</ul>{preview.reasons.map(reason=><p key={reason}>{reason}</p>)}<label className="ti-check"><input type="checkbox" checked={acknowledged} onChange={e=>setAcknowledged(e.target.checked)}/>{tr('acknowledge')}</label>{button('shareable',()=>void act('export',{scope:'shareable',exclusions_acknowledged:acknowledged}),preview.status!=='ready'||!acknowledged)}</div>}
      </div>}
    </div>
    {!!state?.pending_actions?.length && <section className="ti-research-pending"><h4>{tr('unfinishedActions')}</h4>{state.pending_actions.map(action=><article className="ti-notice" key={action.request_id}><strong>{action.kind} · {status(action.status)}</strong><p>{action.reason || tr('recoveryHint')}</p>{action.status==='ready_to_recover' && button('recoverSaved',()=>void recover(action.request_id))}</article>)}</section>}
    <div className="ti-research-history"><h4>{tr('recent')}</h4>{!state?.history.length && <p>{tr('noHistory')}</p>}{state?.history.slice().reverse().map((event,index)=><details key={event.id} open={index===0}><summary>{event.at.slice(0,16).replace('T',' ')} · {event.kind} · {status(event.data.status)}</summary>{resultView(event)}</details>)}</div>
  </section>;
}
