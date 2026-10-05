/* Colonna sinistra del dettaglio (mockup v2): Cambiamenti oppure lo stato da sistemare (in corso, errore,
   collegamento da confermare, senza fonte, escluso), la proposta AI o il profilo avanzato. */
import { Ban, Check, CircleAlert, ExternalLink, FileText, Info, Link2, Loader2, PlusSquare, RefreshCw, Sparkles, TriangleAlert } from 'lucide-react';
import { externalWebUrl } from '../../../electron/security';
import { Segmenti } from '@/components/nuova/Card';
import { diffParole, filtraCambiamenti, motivoSenzaConfronto, presentaCambiamenti, propostaEsef, quando, rimedio, trimestreSuTrimestre, type Cambiamento, type Filtro } from './logica';
import { parole } from './parole';
import type { AzioniFiling, DatiFiling } from './tipi';
import { Pill, Sezione } from './VistaFiling';
import CardAi from './CardAi';
import Profilo from './Profilo';

export const PAGINA = 100;
const TONO_TIPO: Record<string, string> = { aggiunto: 'ok', rimosso: 'bad', modificato: 'warn', spostato: 'off' };

function Testo({ c }: { c: Cambiamento }) {
  if (c.tipo === 'modificato' && c.prima?.testo && c.dopo?.testo) {
    return <p className="fl-q">{diffParole(c.prima.testo, c.dopo.testo).map((x, i) =>
      x.t === '-' ? <del key={i}>{x.s}</del> : x.t === '+' ? <ins key={i}>{x.s}</ins> : <span key={i}>{x.s}</span>)}</p>;
  }
  if (c.tipo === 'rimosso') return <p className="fl-q"><del>{c.prima?.testo}</del></p>;
  if (c.tipo === 'aggiunto') return <p className="fl-q"><ins>{c.dopo?.testo}</ins></p>;
  return <p className="fl-q">{c.dopo?.testo || c.prima?.testo}</p>;
}

const SEGNO: Record<string, string> = { aggiunto: '+', rimosso: '−', modificato: '~', spostato: '↔' };

function Voce({ c }: { c: Cambiamento }) {
  const w = parole(), url = externalWebUrl(c.url || '');
  return (
    <article className="fl-chg" data-filing-cambiamento={c.id} data-tipo={c.tipo}>
      <span className={`fl-k is-${TONO_TIPO[c.tipo] || 'off'}`} title={w.kind[c.tipo] || c.tipo}>
        <span aria-hidden="true">{SEGNO[c.tipo] || '·'}</span><span className="fl-sr">{w.kind[c.tipo] || c.tipo}</span></span>
      <div className="fl-chg-c">
        <div className="fl-chg-h">
          <span className="fl-sez">{c.sezione}</span>
          {c.pagina && <span className="fl-pg">{c.pagina}</span>}
        </div>
        <Testo c={c} />
        <div className="fl-cit">
          {c.citazioni.map(id => { const x = id.endsWith('-prima') ? c.prima : c.dopo;
            return <code key={id} title={w.citeHint(x?.sha256 || '—', x?.inizio != null && x?.fine != null ? `${x.inizio}–${x.fine}` : '—')}>{id}</code>; })}
          <span className="bbn-grow" />
          {url && <a className="bbn-link" href={url} target="_blank" rel="noreferrer">{w.openDoc}<ExternalLink size={13} aria-hidden="true" /></a>}
        </div>
      </div>
    </article>
  );
}

function Cambiamenti({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), r = d.detail?.result;
  const diff = r?.confronto_corrente || r?.confronto_storico;
  const esef = (d.listing?.profile?.profile as { esef_modo?: string } | undefined)?.esef_modo === 'blocchi' && (r?.variante ?? 'annuale') === 'annuale';
  // gli ID «C3» del contesto valgono solo per il run che il Consigliere cita
  const inEvidenza = d.runId != null && d.runId === d.titolo?.run_id ? d.preview?.in_evidenza ?? [] : [];
  const p = presentaCambiamenti(diff?.cambiamenti ?? [], inEvidenza);
  const lista = filtraCambiamenti(p.visibili, d.filtro, inEvidenza);
  const totale = (diff?.cambiamenti ?? []).length;
  const opzioni: { id: Filtro; testo: string }[] = esef
    ? [{ id: 'evidenza', testo: w.fHighlight }, { id: 'tutti', testo: w.fAll }]
    : [{ id: 'evidenza', testo: w.fHighlight }, { id: 'rischi', testo: w.fRisks }, { id: 'gestione', testo: w.fMgmt },
      { id: 'contenziosi', testo: w.fLegal }, { id: 'tutti', testo: w.fAll }];
  const senzaCoppia = d.stato === 'primo_confronto' ? motivoSenzaConfronto(d.titolo?.stato_riga) : null;
  const nonAbbinati = (diff?.misure?.cambiamenti ?? 0) > 0 && /non abbinat|not paired/i.test(d.titolo?.stato_riga || '');
  return (
    <Sezione titolo={esef ? w.changesIfrs : w.changes} nota={totale ? w.changesNote(totale) : null}
      className="fl-centro" data-filing-centro="cambiamenti"
      azioni={totale ? <Segmenti etichetta={w.filters} valore={d.filtro} onChange={a.filtro} opzioni={opzioni} /> : null}
      piede={d.filtro === 'evidenza' && inEvidenza.length ? w.changesFootHighlight : null}>
      {esef && r?.copertura?.stato === 'parziale' && <p className="fl-note is-warn"><TriangleAlert size={16} aria-hidden="true" />{w.partialEsef}</p>}
      {r && !r.confronto_corrente && r.confronto_storico && <p className="fl-note is-warn"><TriangleAlert size={16} aria-hidden="true" />{w.historical}</p>}
      {trimestreSuTrimestre(r) && <p className="fl-note is-warn" data-filing-sequenziale="1"><TriangleAlert size={16} aria-hidden="true" />{w.qoqNote}</p>}
      {r?.ultimo_non_verificato && <p className="fl-note is-warn" data-filing-non-verificato="1"><TriangleAlert size={16} aria-hidden="true" />{w.unverifiedLatest}</p>}
      {nonAbbinati && <p className="fl-note"><Info size={16} aria-hidden="true" />{w.unpaired}</p>}
      {d.listingErr && <p className="fl-note is-bad" role="alert" data-filing-errore="listing"><TriangleAlert size={16} aria-hidden="true" />{w.listingError(d.listingErr)}</p>}
      {d.listing?.ultimo_controllo?.at && <p className="fl-mini fl-leggero" data-filing-leggero="1">
        {w.lightCheck(quando(d.listing.ultimo_controllo.at, new Date(), d.lingua, w) || '')}</p>}
      {senzaCoppia ? <p className="fl-note is-warn" data-filing-senza-coppia="1"><TriangleAlert size={16} aria-hidden="true" />{w.noPair(senzaCoppia)}</p>
        : !d.detail && d.detailErr ? <p className="fl-note is-bad" role="alert" data-filing-errore="run"><TriangleAlert size={16} aria-hidden="true" />{w.detailError(d.detailErr)}</p>
        : !d.detail ? <p className="bbn-empty">{d.titolo?.ultimo_confronto ? w.loading : d.stato === 'primo_confronto' ? w.waitingFirst : w.noComparison}</p>
        : !diff ? <p className="bbn-empty">{d.stato === 'primo_confronto' ? w.waitingFirst : w.noComparison}</p>
        : !totale ? <p className="bbn-empty">{w.noChanges}</p>
        : !lista.length ? <p className="bbn-empty">{w.noChangesFilter}</p>
        : lista.slice(0, d.quanti).map(c => <Voce key={c.id} c={c} />)}
      {lista.length > d.quanti && <div className="fl-row-act fl-altri"><span className="fl-mini">{w.shownOf(d.quanti, lista.length)}</span>
        <button type="button" className="bbn-btn" data-filing-azione="altri" onClick={a.altri}>{w.showMore(Math.min(PAGINA, lista.length - d.quanti))}</button></div>}
      {d.filtro === 'tutti' && p.tabelle.length > 0 && <div className="fl-tabelle" data-filing-tabelle={p.tabelle.length}>
        <p className="fl-note"><Info size={16} aria-hidden="true" /><span><b>{w.tables(p.tabelle.length)}</b> · {w.tablesHint}</span>
          <button type="button" className="bbn-link" aria-expanded={d.mostraTabelle} onClick={a.tabelle}>{d.mostraTabelle ? w.hideTables : w.showTables}</button></p>
        {d.mostraTabelle && p.tabelle.map(c => <Voce key={c.id} c={c} />)}
      </div>}
      {d.filtro === 'tutti' && p.elenchiNascosti > 0 && <p className="fl-mini">{w.listNumbers(p.elenchiNascosti)}</p>}
    </Sezione>
  );
}

function InCorso({ d }: { d: DatiFiling }) {
  const w = parole(), run = d.titolo?.run_attivo ?? d.listing?.active_run;
  const ora = new Date();
  const conf = d.titolo?.ultimo_confronto ? quando(d.titolo.ultimo_confronto, ora, d.lingua, w) : null;
  return (
    <Sezione titolo={w.runTitle} className="fl-centro" data-filing-centro="in-corso" piede={w.runFoot}>
      <ul className="fl-steps">
        <li><Check size={16} className="is-ok" aria-hidden="true" /><span>{w.runStarted(quando(run?.started_at, ora, d.lingua, w) || '—', w.triggers[run?.trigger || ''] || run?.trigger || '—')}</span></li>
        <li className="is-now"><span className="fl-spinner" aria-hidden="true" /><span>{w.runWorking}</span><small>{w.tag.in_corso}</small></li>
      </ul>
      <p className="fl-note is-acc"><Info size={16} aria-hidden="true" /><span>{w.runNote}{conf ? ` · ${w.figLast}: ${conf}` : ''}</span></p>
    </Sezione>
  );
}

function Errore({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), err = d.titolo?.ultimo_errore ?? (d.listing?.ultimo_errore ? { at: d.listing.ultimo_errore.at, reason: d.listing.ultimo_errore.reason } : null);
  const motivo = err?.reason || d.listing?.reason || w.unknown;
  const r = rimedio(motivo);
  const testo = r === 'profilo' ? w.errProfile : r === 'riprova' ? w.errRetry : r === 'ocr' ? w.errOcr : w.errGeneric;
  const conf = d.titolo?.ultimo_confronto ? quando(d.titolo.ultimo_confronto, new Date(), d.lingua, w) : null;
  return (
    <Sezione titolo={w.errTitle} className="fl-centro is-err" data-filing-centro="errore" data-filing-rimedio={r}
      piede={conf ? w.errFoot(conf) : w.errFootNone}>
      <p className="fl-note is-bad" role="alert"><CircleAlert size={16} aria-hidden="true" /><span>{motivo}</span></p>
      <div className="fl-what">
        <div><b>{w.errWhat}</b><span>{testo}</span></div>
        {r !== 'ocr' && <button type="button" className="bbn-btn" data-filing-azione="riprova" disabled={!!d.listing?.active_run} onClick={() => a.verifica()}>
          <RefreshCw size={15} aria-hidden="true" />{d.listing?.profile?.qualitative_enabled ? w.retryCheckAi : w.retryCheck}</button>}
        {(r === 'profilo' || r === 'riprova_profilo') && <button type="button" className="bbn-btn" data-filing-azione="profilo-avanzato" onClick={a.profilo}>{w.errProfileBtn}</button>}
        {r === 'ocr' && <button type="button" className="bbn-btn" onClick={() => a.escludi(true)}>{w.exclude}</button>}
      </div>
    </Sezione>
  );
}

function Collegamento({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), p = d.proposta;
  const esef = propostaEsef(p);
  const opz = !p ? [] : esef
    ? (p.esef?.candidati ?? []).map(c => ({ id: c.lei, nome: c.nome, sotto: `LEI ${c.lei}`, origine: c.origine }))
    : p.sec.candidati.map(c => ({ id: c.cik, nome: `${c.ticker} · ${c.nome}`, sotto: `CIK ${c.cik}`, origine: c.origine }));
  const etich = (o: string) => o === 'nome' || o === 'negozio' ? w.sameName : o === 'alias' ? w.alias : o === 'ticker' ? w.ticker : w.similar;
  return (
    <Sezione titolo={w.linkTitle} className="fl-centro" data-filing-centro="collegamento"
      piede={w.linkFoot(d.titolo?.ticker || '')}>
      {d.propostaErr ? <p className="fl-note is-bad" role="alert">{w.linkError(d.propostaErr)}</p>
        : !p ? (d.ricercaFonti ? <p className="fl-note"><Loader2 size={16} className="fl-spin" aria-hidden="true" />{w.linkSearching}</p>
          : <div className="fl-stack"><p className="fl-note"><Info size={16} aria-hidden="true" />{w.offNotTried}</p>
            <div><button type="button" className="bbn-btn" data-filing-azione="cerca" onClick={a.cercaFonti}>{w.search_}</button></div></div>)
        : <div className="fl-stack">
          <p className="fl-p">{opz.length > 1 ? (esef ? w.linkEsef : w.linkSec) : w.linkOne}</p>
          <div role="radiogroup" aria-label={w.candidatesL} className="fl-stack">
            {opz.map(o => <label key={o.id} className={'fl-opt' + (d.scelta === o.id ? ' is-sel' : '')} data-filing-candidato={o.id}>
              <input type="radio" name="fl-candidato" checked={d.scelta === o.id} onChange={() => a.scelta(o.id)} />
              <span><b>{o.nome}</b><span>{o.sotto}</span></span>
              <Pill tono={o.origine === 'nome_simile' ? 'off' : 'ok'}>{etich(o.origine)}</Pill>
            </label>)}
          </div>
          {(p.sec.motivo || p.esef?.motivo) && <p className="fl-mini">{esef ? p.esef?.motivo : p.sec.motivo}</p>}
          <div className="fl-row-act">
            <button type="button" className="bbn-btn is-primary" data-filing-azione="conferma" disabled={!d.scelta || d.lavoro === 'attiva'}
              onClick={() => d.scelta && a.attiva(esef ? { lei: d.scelta } : { cik: d.scelta })}>{d.lavoro === 'attiva' ? w.activating : w.confirm}</button>
            {opz.length > 0 && <button type="button" className="bbn-link" data-filing-azione="nessuno" title={w.noneHint} disabled={d.lavoro === 'rifiuta'} onClick={() => a.rifiuta()}>{w.none}</button>}
          </div>
        </div>}
    </Sezione>
  );
}

/** Fase F: i PDF IR trovati sul sito, gia' pronti per la stima gratuita (la proposta AI resta un clic). */
function PdfTrovati({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), t = d.titolo!, p = t.pdf_ir!;
  const host = (u?: string | null) => { try { return u ? new URL(u).hostname : ''; } catch { return ''; } };
  const nome = (u: string) => decodeURIComponent(u.split('?')[0].split('/').pop() || u);
  const at = quando(p.at ?? null, new Date(), d.lingua, w);
  const riga = (k: string, doc: NonNullable<typeof p.precedente>) => (
    <div className="fl-sez-row" data-filing-pdf={doc.url}>
      <div><b>{k} · {w.pdfTipo[doc.tipo] || doc.tipo} {doc.periodo.slice(0, 4)}</b>
        <span>{externalWebUrl(doc.url) ? <a className="bbn-link" href={externalWebUrl(doc.url)!} target="_blank" rel="noreferrer">{nome(doc.url)}<ExternalLink size={13} aria-hidden="true" /></a> : nome(doc.url)}</span></div>
    </div>);
  return (
    <div className="fl-ai" data-filing-pdf-trovati="1">
      <div className="fl-ai-head"><span className="t"><Sparkles size={16} aria-hidden="true" />{w.pdfTitle}</span><span className="bbn-grow" />
        {at && <span className="fl-mini">{w.pdfAt(at)}</span>}</div>
      <p>{w.pdfText(host(p.sito) || host(p.ultimo.url))}</p>
      {riga(w.pdfLatest, p.ultimo)}
      {p.precedente ? riga(w.pdfPrevious, p.precedente) : <p className="fl-mini">{w.pdfNoPrevious}</p>}
      <div className="fl-row-act">
        <button type="button" className="bbn-btn is-primary" data-filing-azione="pdf-stima" disabled={!!d.lavoro} onClick={() => a.stimaTrovati()}>
          {d.lavoro === 'ai-stima' ? <><Loader2 size={15} className="fl-spin" aria-hidden="true" />{w.aiEstimating}</> : w.pdfEstimate}</button>
      </div>
    </div>
  );
}

function SenzaFonte({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), p = d.proposta, trovati = d.titolo?.pdf_ir;
  return (
    <Sezione titolo={w.offTitle} className="fl-centro" data-filing-centro="senza-fonte" piede={w.offFoot}>
      <div className="fl-stack">
        <p className="fl-note"><Info size={16} aria-hidden="true" /><span>{p ? w.offText : w.offNotTried}</span></p>
        {d.propostaErr && <p className="fl-note is-bad" role="alert">{w.linkError(d.propostaErr)}</p>}
        {d.titolo?.attivazione?.motivo && !p && <p className="fl-mini">{d.titolo.attivazione.motivo}</p>}
        {trovati && <PdfTrovati d={d} a={a} />}
        {!trovati && d.ricercaPdf && <p className="fl-note is-warn" data-filing-pdf-nessuno="1"><TriangleAlert size={16} aria-hidden="true" />
          <span>{w.pdfNone}{d.ricercaPdf.motivi.length ? ` ${d.ricercaPdf.motivi.slice(0, 2).join(' · ')}` : ''}</span></p>}
        <div className="fl-row-act">
          {!p && <button type="button" className="bbn-btn" data-filing-azione="cerca" onClick={a.cercaFonti}>{w.search_}</button>}
          {!trovati && <button type="button" className="bbn-btn" data-filing-azione="cerca-pdf" disabled={!!d.lavoro} onClick={() => a.cercaPdf()}>
            {d.lavoro === 'cerca-pdf' ? <><Loader2 size={15} className="fl-spin" aria-hidden="true" />{w.pdfSearching}</> : w.pdfSearch}</button>}
          <button type="button" className="bbn-btn" data-filing-azione="sito-ir" onClick={a.apriAi}>{w.indicateIr}</button>
          <button type="button" className="bbn-link" data-filing-azione="manuale" onClick={() => a.manuale(d.manuale == null ? '' : null)}>{w.manual}</button>
          <button type="button" className="bbn-link" data-filing-azione="escludi" onClick={() => a.escludi(true)}>{w.exclude}</button>
        </div>
        {d.manuale != null && <div className="fl-row-act">
          <label className="fl-field"><span>{w.manualLabel}</span>
            <input value={d.manuale} onChange={e => a.manuale(e.target.value)} spellCheck={false} data-filing-input="manuale" /></label>
          <button type="button" className="bbn-btn is-primary" disabled={!d.manuale.trim() || d.lavoro === 'attiva'} onClick={() => a.attivaManuale()}>{w.manualGo}</button>
        </div>}
      </div>
    </Sezione>
  );
}

function Escluso({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole();
  return (
    <Sezione titolo={w.exclTitle} className="fl-centro" data-filing-centro="escluso" piede={w.consFootMissing}>
      <div className="fl-stack">
        <p className="fl-note"><Info size={16} aria-hidden="true" />{w.exclText}</p>
        <div><button type="button" className="bbn-btn" data-filing-azione="includi" disabled={d.lavoro === 'escludi'} onClick={() => a.escludi(false)}>{w.include}</button></div>
      </div>
    </Sezione>
  );
}

export default function Centro({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const s = d.stato;
  if (d.profiloAperto) return <Profilo d={d} a={a} />;
  if (d.aiAperta || s === 'proposta_ai') return <CardAi d={d} a={a} />;
  if (s === 'in_corso') return <InCorso d={d} />;
  if (s === 'errore') return <Errore d={d} a={a} />;
  if (s === 'da_confermare') return <Collegamento d={d} a={a} />;
  // la ricerca (gratis) ha trovato candidati per un titolo senza profilo: si confermano (prova reale 04/10/2026)
  const candidati = (d.proposta?.sec.candidati.length ?? 0) + (d.proposta?.esef?.candidati.length ?? 0);
  if ((s === 'senza_fonte' || s === 'non_attivo') && candidati > 0) return <Collegamento d={d} a={a} />;
  if (s === 'senza_fonte' || s === 'non_attivo') return <SenzaFonte d={d} a={a} />;
  if (s === 'escluso') return <Escluso d={d} a={a} />;
  return <Cambiamenti d={d} a={a} />;
}
