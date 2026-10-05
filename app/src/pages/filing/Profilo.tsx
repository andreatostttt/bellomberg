/* Profilo avanzato: editor JSON del profilo, storico dei controlli e dettaglio tecnico del run
   (coppia, candidati, copertura, freschezza, giudizio AI con citazioni, indice). Tutto ciò che
   prima stava nel pannello del titolo resta qui. */
import { externalWebUrl } from '../../../electron/security';
import type { FilingCitation } from '@/lib/api';
import { quando } from './logica';
import { parole } from './parole';
import type { AzioniFiling, DatiFiling } from './tipi';
import { Sezione } from './VistaFiling';

function Link({ url }: { url?: string }) {
  const u = externalWebUrl(url || '');
  return u ? <a className="bbn-link fl-break" href={u} target="_blank" rel="noreferrer">{url}</a> : null;
}

export default function Profilo({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), L = d.listing, det = d.detail, r = det?.result, ora = new Date();
  const p = L?.profile?.profile as Record<string, unknown> | undefined;
  // collegamento automatico SEC/ESEF (il backend rifiuta con 422 i profili manuali o IR)
  // stessa regola di POST unlink: solo i collegamenti automatici (non manuali, non da proposta AI)
  const automatico = !!p && (!!p.cik || !!p.lei) && JSON.stringify(p.fonti) !== '["ir"]'
    && !['manuale', 'proposta_ai'].includes(String(p.origine_collegamento ?? 'manuale'));
  const diff = r?.confronto_corrente || r?.confronto_storico;
  const citazioni: Record<string, FilingCitation> = {};
  diff?.cambiamenti?.forEach((c, i) => { if (c.prima) citazioni[`C${i + 1}-prima`] = c.prima; if (c.dopo) citazioni[`C${i + 1}-dopo`] = c.dopo; });
  const pct = (v?: number | null) => v == null ? w.unknown : new Intl.NumberFormat(d.lingua === 'en' ? 'en-US' : 'it-IT', { style: 'percent', maximumFractionDigits: 1 }).format(v);
  const runs = [...(L?.runs ?? []), ...(L?.ultimo_completo && !L.runs?.some(x => x.id === L.ultimo_completo?.id) ? [L.ultimo_completo] : [])];
  return (
    <Sezione titolo={w.advTitle} nota={L?.profile ? `v${L.profile.version}` : null}
      className="fl-centro fl-profilo" data-filing-centro="profilo"
      azioni={<button type="button" className="bbn-link" onClick={a.profilo}>{w.closeProfile}</button>}>
      <div className="fl-stack">
        <p className="fl-mini">{L?.profile ? w.advHelp : w.advNone}</p>
        <label className="fl-field"><span>{w.advJson}</span>
          <textarea rows={10} value={d.profileJson} onChange={e => a.profileJson(e.target.value)} spellCheck={false} data-filing-input="profilo" /></label>
        <div className="fl-row-act">
          <label className="fl-check"><input type="checkbox" checked={d.enabled} onChange={e => a.enabled(e.target.checked)} />{w.advAuto}</label>
          <label className="fl-check">{w.advInterval}<input type="text" inputMode="numeric" autoComplete="off" data-filing-input="intervallo" value={d.intervalHours} onChange={e => a.intervalHours(e.target.value)} className="fl-num-in" /></label>
          <label className="fl-check"><input type="checkbox" checked={d.qualitative} onChange={e => a.qualitative(e.target.checked)} />{w.advQual}</label>
        </div>
        <div className="fl-row-act">
          <button type="button" className="bbn-btn is-primary" data-filing-azione="salva-profilo" disabled={d.lavoro === 'salva'} onClick={() => a.salvaProfilo()}>
            {d.lavoro === 'salva' ? w.advSaving : w.advSave}</button>
          {automatico && d.conferma !== 'scollega' && <button type="button" className="bbn-link" data-filing-azione="scollega" onClick={() => a.conferma('scollega')}>{w.advUnlink}</button>}
          <button type="button" className="bbn-link" data-filing-azione="escludi" onClick={() => a.escludi(true)}>{w.advExclude}</button>
        </div>
        {d.conferma === 'scollega' && <div className="fl-note is-warn" role="alert"><span>{w.advUnlinkConfirm}</span>
          <button type="button" className="bbn-btn" data-filing-azione="scollega-conferma" disabled={d.lavoro === 'scollega'} onClick={() => a.scollega()}>{w.yes}</button>
          <button type="button" className="bbn-link" onClick={() => a.conferma(null)}>{w.cancel}</button></div>}

        <label className="fl-field"><span>{w.advHistory}</span>
          {runs.length ? <select value={d.runId ?? ''} onChange={e => a.run(Number(e.target.value))} data-filing-input="storico">
            {runs.map(x => <option key={x.id} value={x.id}>#{x.id} · {x.controllo_leggero ? w.advLight : x.status} · {quando(x.started_at, ora, d.lingua, w) || w.unknown}</option>)}
          </select> : <span className="fl-mini">{w.advNoRuns}</span>}</label>

        {det && <div className="fl-tech" data-filing-run={det.id}>
          <p><b>{w.advRun} #{det.id}</b> · {det.status} · {w.triggers[det.trigger || ''] || det.trigger || w.unknown} · {quando(det.started_at, ora, d.lingua, w) || w.unknown}</p>
          {det.reason && <p className="fl-warn-t">{det.reason}</p>}
          {r?.controllo_leggero ? <p className="fl-mini">{w.advLight}</p> : r && <>
            {!!r.motivi?.length && <ul>{r.motivi.map((m, i) => <li key={i}>{m}</li>)}</ul>}
            <h4>{w.advCoverage}: {r.copertura?.stato || w.unknown}</h4>
            <p className="fl-mini">{r.copertura?.candidati_osservati ?? '—'} · {r.copertura?.documenti_tentati ?? '—'} / {r.copertura?.max_documenti ?? '—'}</p>
            {!!r.copertura?.limiti?.length && <ul>{r.copertura.limiti.map((m, i) => <li key={i}>{m}</li>)}</ul>}
            {!!r.fonti?.length && <><h4>{w.advSources}</h4><ul>{r.fonti.map((f, i) => <li key={i}>{f.nome || w.unknown}: {f.stato || w.unknown} {f.motivi?.join('; ')}</li>)}</ul></>}
            {r.ultimo_non_verificato && <p className="fl-warn-t">{w.unverifiedLatest}</p>}
            <h4>{w.advFresh}: {r.freschezza?.stato || w.unknown}</h4>
            {!!r.freschezza?.motivi?.length && <ul>{r.freschezza.motivi.map((m, i) => <li key={i}>{m}</li>)}</ul>}
            <p className="fl-mini">{w.advPeriod}: {r.freschezza?.ultimo_periodo || w.unknown} · {w.advNextReport}: {r.freschezza?.next_report_date || w.unknown}</p>
            {r.freschezza?.next_report_source && <Link url={r.freschezza.next_report_source} />}
            {r.coppia && <><h4>{w.advPair}: {r.coppia.ambito || w.unknown}</h4>
              {(['prima', 'dopo'] as const).map(k => <p key={k} className="fl-mini fl-break">{k === 'prima' ? w.advBefore : w.advAfter}: {r.coppia?.[k]?.metadati?.periodo_inizio || '—'}–{r.coppia?.[k]?.metadati?.periodo_fine || '—'} · {r.coppia?.[k]?.sha256 || '—'} <Link url={r.coppia?.[k]?.url} /></p>)}</>}
            {diff && <><h4>{w.advDiff}: {diff.stato} · {diff.misure?.cambiamenti ?? diff.cambiamenti?.length ?? 0}</h4>
              <p className="fl-mini">{w.advSections}: {diff.sezioni_confrontate?.length ? diff.sezioni_confrontate.join(', ') : w.unknown}</p>
              {(!!diff.motivi?.length || !!diff.limiti?.length) && <ul>{[...(diff.motivi ?? []), ...(diff.limiti ?? [])].map((m, i) => <li key={i}>{m}</li>)}</ul>}
              {Object.keys(diff.similarita_sezioni || {}).length > 0 && <details data-filing-similarita="1"><summary>{w.advSimilarity}</summary>
                <p className="fl-mini">{w.advNotJudgment}</p>
                {Object.entries(diff.similarita_sezioni || {}).map(([sez, m]) => <p key={sez} className="fl-mini">{sez}: Jaccard {pct(m.jaccard)} · {w.advCosine} {pct(m.coseno)}</p>)}
              </details>}
              {!!diff.segmenti_non_confrontabili?.length && <details><summary>{w.advUncomparable}: {diff.segmenti_non_confrontabili.length}</summary>
                {diff.segmenti_non_confrontabili.map((x, i) => <p key={i} className="fl-mini fl-break">{[x.prima?.testo, x.dopo?.testo].filter(Boolean).join(' → ')}</p>)}
              </details>}</>}
            {!!r.candidati?.length && <details><summary>{w.advCandidates}: {r.candidati.length}</summary>
              {r.candidati.map((c, i) => <p key={i} className="fl-mini fl-break">{c.fonte || w.unknown} · {c.stato} · {c.metadati?.periodo_fine || w.unknown}{c.sha256 ? ` · ${c.sha256}` : ''}{c.motivi?.length ? ` · ${c.motivi.join('; ')}` : ''} <Link url={c.url} /></p>)}
            </details>}
          </>}
          <h4>{w.advJudgment}</h4>
          {det.judgment ? <>
            <p className="fl-mini">{det.judgment.status}{det.judgment.model ? ` · ${det.judgment.model}` : ''}{det.judgment.coverage ? ` · ${det.judgment.coverage.shown ?? '—'}/${det.judgment.coverage.total ?? '—'}` : ''}</p>
            {det.judgment.reason && <p className="fl-warn-t">{det.judgment.reason}</p>}
            {det.judgment.findings?.map((f, i) => <div key={i} className="fl-finding"><b>{f.category || w.unknown}</b>: {f.assessment || w.unknown}
              {!!f.citations?.length && <p className="fl-mini">{w.advCitations}: {f.citations.map(id => citazioni[id]
                ? <span key={id} title={citazioni[id].testo}><code>{id}</code> {citazioni[id].sezione} </span>
                : <span key={id} className="fl-warn-t"><code>{id}</code> {w.advCitationMissing} </span>)}</p>}
            </div>)}
          </> : <p className="fl-mini">{w.advJudgmentNone}</p>}
          {det.index && <p className="fl-mini">{w.advIndex}: {det.index.status}{det.index.reason ? ` · ${det.index.reason}` : ''}</p>}
        </div>}
      </div>
    </Sezione>
  );
}
