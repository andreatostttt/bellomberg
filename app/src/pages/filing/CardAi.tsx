/* Proposta AI da PDF IR (fase C) dentro la pagina: stima gratis → pulsante con costo massimo →
   sezioni verificate/scartate → salva / aggiungi variante / sostituisci, «Riprova (nuovo costo)».
   L'unica chiamata AI parte dal pulsante «Proponi con AI». */
import { Check, CircleAlert, Info, Loader2, Sparkles, X } from 'lucide-react';
import type { FilingAiProposalSalvata } from '@/lib/api';
import { parole } from './parole';
import type { AzioniFiling, DatiFiling } from './tipi';
import { Pill, Sezione } from './VistaFiling';

const euro = (v: number | null | undefined, lingua: string) => v == null ? null
  : '€ ' + new Intl.NumberFormat(lingua === 'en' ? 'en-US' : 'it-IT', { minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(v);

export default function CardAi({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), st = d.aiStima, e = d.aiEsito, L = d.lingua;
  const occupato = !!d.lavoro?.startsWith('ai-');
  const profilo = d.listing?.profile;
  const esef = (profilo?.profile as { esef_modo?: string } | undefined)?.esef_modo === 'blocchi';
  const verificate = e?.stato === 'done' ? e.verificate ?? [] : [];
  const scartate = e?.stato === 'done' ? e.scartate ?? [] : [];
  const tipo = e?.tipo === 'semestrale' ? w.aiSemestrale : e?.tipo === 'trimestrale' ? w.aiTrimestrale : e?.tipo || '';
  const costoMax = st?.stato === 'ok' ? euro(st.costo_max_eur, L) ?? (st.costo_max_usd != null ? `$ ${st.costo_max_usd.toFixed(4)}` : null) : null;
  const daRiverificare = !!(e as FilingAiProposalSalvata | null)?.da_riverificare;
  const fatto = e?.stato === 'done';
  const tok = (n?: number) => n == null ? '—' : new Intl.NumberFormat(L === 'en' ? 'en-US' : 'it-IT').format(n);
  const proponi = (
    <button type="button" className="bbn-btn is-ai" data-filing-azione="ai-proponi"
      disabled={occupato || (!st?.cache && costoMax == null)} onClick={() => a.aiProponi(false)}>
      {d.lavoro === 'ai-proposta' ? <Loader2 size={15} className="fl-spin" aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
      {d.lavoro === 'ai-proposta' ? w.aiProposing : st?.cache ? w.aiProposeCached : w.aiPropose(costoMax || '—')}</button>
  );
  /** Riquadro tratteggiato di Notizie: PDF, altri PDF, stima gratuita, pulsante sfumato col costo massimo. */
  const modulo = (
    <>
      {esef && <p>{w.aiInterimHint}</p>}
      <label className="fl-field"><span>{w.aiUrl}</span>
        <input type="url" value={d.aiUrl} placeholder="https://" onChange={ev => a.aiUrl(ev.target.value)} data-filing-input="ai-url" spellCheck={false} /></label>
      <label className="fl-field"><span>{w.aiOthers}</span>
        <textarea rows={2} value={d.aiAltri} onChange={ev => a.aiAltri(ev.target.value)} data-filing-input="ai-altri" spellCheck={false} /></label>
      <div className="fl-row-act">
        <button type="button" className="bbn-btn" data-filing-azione="ai-stima" disabled={occupato || !d.aiUrl.trim()} onClick={() => a.aiStima()}>
          {d.lavoro === 'ai-stima' ? <><Loader2 size={15} className="fl-spin" aria-hidden="true" />{w.aiEstimating}</> : w.aiEstimate}</button>
        {st?.stato === 'ok' && proponi}
      </div>
      {st?.stato === 'not_configured' && <p className="fl-note is-warn" data-filing-ai="not-configured">{w.aiNotConfigured(st.variabile || 'NEWS_SUMMARY_MODEL')}</p>}
      {st?.stato === 'ok' && <p data-filing-ai="stima">
        {st.cache ? w.aiCached : w.aiEstimateLine(st.pagine, tok(st.token_input_stimati), costoMax || '—')}
        {st.troncato > 0 && <> · {w.aiTruncated(st.troncato)}</>}{st.motivo ? ` · ${st.motivo}` : ''}</p>}
    </>
  );
  const periodo = e?.stato === 'done' && e.periodo ? `${e.periodo.inizio} → ${e.periodo.fine}` : null;
  return (
    <Sezione titolo={d.aiAperta && d.stato !== 'proposta_ai' ? w.aiInterim : null} className="fl-centro" data-filing-centro="proposta-ai"
      azioni={d.aiAperta && d.stato !== 'proposta_ai' ? <button type="button" className="bbn-link" onClick={a.apriAi}>{w.aiClose}</button> : null}>
      <div className="fl-stack">
        {!fatto && <div className="fl-ai">
          <div className="fl-ai-head"><span className="t"><Sparkles size={16} aria-hidden="true" />{w.aiNew}</span></div>
          <p>{w.aiIntro}</p>
          {modulo}
        </div>}
        {e?.stato === 'not_configured' && <p className="fl-note is-warn">{w.aiNotConfigured(e.variabile || 'NEWS_SUMMARY_MODEL')}</p>}
        {d.aiErrore && <p className="fl-note is-bad" role="alert"><CircleAlert size={16} aria-hidden="true" />{d.aiErrore}</p>}
        {e?.stato === 'in_corso' && <p className="fl-note" role="status" data-filing-ai="in-corso"><Loader2 size={16} className="fl-spin" aria-hidden="true" />
          {w.aiInProgress(e.secondi != null && Number.isFinite(e.secondi) ? String(Math.round(e.secondi)) : w.aiSecondsNd)}</p>}
        {e?.stato === 'refused' && <p className="fl-note is-warn" role="alert" data-filing-ai="rifiutata"><CircleAlert size={16} aria-hidden="true" />
          {w.aiRefused(e.motivo || w.aiReasonMissing)}
          {e.variabile ? ` · ${w.aiRefusedVar(e.variabile)}` : ''}
          {e.riprova_tra_s != null && Number.isFinite(e.riprova_tra_s) ? ` · ${w.aiRetryIn(String(Math.ceil(e.riprova_tra_s)))}` : ''}</p>}
        {(e?.stato === 'error' || e?.stato === 'refused') && e.riprovabile && <div className="fl-row-act" data-filing-ai="riprova">
          <span className="fl-mini">{e.riprova_paga === false ? w.aiRetryFreeHint : w.aiRetryHint}</span>
          <button type="button" className="bbn-btn" disabled={occupato} onClick={() => a.aiProponi(true)}>{e.riprova_paga === false ? w.aiRetryFree : w.aiRetry}</button></div>}
        {daRiverificare && <p className="fl-note is-warn">{w.aiStale}</p>}
        {fatto && <div className="fl-ai is-done" data-filing-ai="esito">
          <div className="fl-ai-head"><span className="t"><Sparkles size={16} aria-hidden="true" />{w.aiTitle}</span><span className="bbn-grow" />
            {(tipo || periodo) && <Pill tono="acc">{[tipo, periodo].filter(Boolean).join(' · ')}</Pill>}</div>
          <p>{w.aiIntro}</p>
          {verificate.map(s => <div key={s.nome} className="fl-sez-row" data-filing-sezione={s.nome}>
            <div><b>{s.nome}</b><span>{w.aiFromTo(s.inizio, s.fine)}</span></div>
            <Pill tono="ok"><Check size={13} aria-hidden="true" />{s.pagine?.length ? w.aiFound(`p. ${Math.min(...s.pagine)}${s.pagine.length > 1 ? `–${Math.max(...s.pagine)}` : ''}`) : w.aiFoundNoPage}</Pill>
          </div>)}
          {scartate.map(s => <div key={s.nome} className="fl-sez-row" data-filing-scartata={s.nome}>
            <div><b>{s.nome}</b><span>{s.motivo}</span></div>
            <Pill tono="bad"><X size={13} aria-hidden="true" />{w.aiSectionDiscarded}</Pill>
          </div>)}
          {!!e.avvisi?.length && <div className="fl-note"><Info size={16} aria-hidden="true" /><span><b>{w.aiWarnings}:</b> {e.avvisi.join(' · ')}</span></div>}
          {!!e.altri?.length && e.altri.map(o => <p key={o.url} className="fl-mini" data-filing-ai-altro={o.url}>
            {w.aiOthersCheck(o.url.split('?')[0].split('/').pop() || o.url)}: {o.stato === 'ok' ? `✓ ${o.sezioni_ok.join(', ')}` : o.motivi.join('; ')}
            {Object.keys(o.sezioni_mancanti || {}).length > 0 && ` · ✗ ${Object.entries(o.sezioni_mancanti).map(([k, v]) => `${k}: ${v}`).join('; ')}`}</p>)}
          {!e.salvabile && <p className="fl-note is-warn">{w.aiNothing}{e.motivi?.length ? ` ${e.motivi.join('; ')}` : ''}</p>}
          {d.aiSostituisci && <p className="fl-note is-warn" role="alert">{w.aiReplaceConfirm}</p>}
          <div className="fl-row-act">
            {e.salvabile && !daRiverificare && (esef
              ? <button type="button" className="bbn-btn is-primary" data-filing-azione="ai-variante" disabled={occupato} onClick={() => a.aiSalva('variante')}>{w.aiAddVariant(tipo)}</button>
              : d.aiSostituisci
                ? <button type="button" className="bbn-btn is-primary" data-filing-azione="ai-sostituisci" disabled={occupato} onClick={() => a.aiSalva('sostituisci')}>{w.aiReplace}</button>
                : <button type="button" className="bbn-btn is-primary" data-filing-azione="ai-salva" disabled={occupato} onClick={() => a.aiSalva('nuovo')}>{w.aiSave(verificate.length)}</button>)}
            {/* prima la stima gratuita: se il PDF all'indirizzo è cambiato la cache non vale e il pulsante mostra il costo */}
            {daRiverificare && <button type="button" className="bbn-btn" data-filing-azione="ai-riverifica" disabled={occupato || !d.aiUrl.trim()} onClick={() => a.aiStima()}>{w.aiEstimate}</button>}
            <button type="button" className="bbn-link" data-filing-azione="ai-scarta" disabled={occupato} onClick={() => a.aiScarta()}>{w.aiDiscard}</button>
          </div>
          <div className="fl-ai-foot">{[e.modello, e.costo_eur != null ? w.aiCost(euro(e.costo_eur, L) || '') : null].filter(Boolean).map(x => <span key={x}>{x}</span>)}
            <span>{w.aiFoot}</span></div>
        </div>}
        {fatto && <div className="fl-ai" data-filing-ai="altro-pdf">
          <div className="fl-ai-head"><span className="t"><Sparkles size={16} aria-hidden="true" />{w.aiOtherPdf}</span></div>
          {modulo}
        </div>}
      </div>
    </Sezione>
  );
}
