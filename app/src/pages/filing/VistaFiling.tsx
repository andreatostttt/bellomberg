/* Viste della pagina Filing (mockup v2, grammatica di Notizie): intestazione, avviso «Da sistemare»,
   contesto per il Comitato + budget, elenco titoli, dettaglio in una sola card.
   Solo presentazione: dati e azioni arrivano da FilingPage. */
import type { ReactNode } from 'react';
import { Bot, CircleAlert, FileSearch, Link2, List, Loader2, Plus, RefreshCw, Settings2, Sparkles, TriangleAlert, X } from 'lucide-react';
import { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import type { FilingOverviewTitolo } from '@/lib/api';
import { coppiaBreve, gruppoDi, motivoBreve, motivoSenzaConfronto, propostaEsef, quando, sezioniProfilo, statoDi, striscia, tonoStato, trimestreSuTrimestre } from './logica';
import { parole } from './parole';
import type { AzioniFiling, DatiFiling } from './tipi';
import Centro from './Centro';
import { CardConsigliere, CardNumeri } from './Lato';

/** Migliaia sempre separate (in italiano Intl non raggruppa i numeri di 4 cifre: 9.840, non 9840). */
export const fmt = (lingua: string, n: number) => new Intl.NumberFormat(lingua === 'en' ? 'en-US' : 'it-IT', { useGrouping: 'always' as unknown as boolean }).format(n); // lib TS precedente a Intl.NumberFormat v3

/** Sezione dentro una card (mockup v2): titolo, nota accanto, azioni a destra; nessuna riga divisoria.
    `piede` resta come riga muta in fondo alla sezione. */
export function Sezione({ titolo, nota, azioni, piede, children, className = '', ...rest }: {
  titolo: ReactNode; nota?: ReactNode; azioni?: ReactNode; piede?: ReactNode; children: ReactNode; className?: string;
} & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  return (
    <section className={`fl-sec ${className}`} {...rest}>
      {(titolo || azioni) && <header className="fl-sec-h">
        {titolo && <h3>{titolo}</h3>}
        {nota != null && nota !== '' && <span className="bbn-card-note">{nota}</span>}
        <span className="bbn-grow" />
        {azioni}
      </header>}
      {children}
      {piede && <p className="fl-foot">{piede}</p>}
    </section>
  );
}

/** Card della pagina (stile bbn-card di Notizie): icona tinta, titolo, nota; nessun piè. */
function Carta({ icona, titolo, nota, azioni, children, className = '', ...rest }: {
  icona?: ReactNode; titolo: ReactNode; nota?: ReactNode; azioni?: ReactNode; children: ReactNode; className?: string;
} & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  return (
    <section className={`bbn-card fl-card ${className}`} {...rest}>
      <header className="bbn-card-head fl-card-head">
        {icona && <span className="fl-ci" aria-hidden="true">{icona}</span>}
        <h2>{titolo}</h2>
        {nota != null && <span className="bbn-card-note">{nota}</span>}
        <span className="bbn-grow" />
        {azioni}
      </header>
      {children}
    </section>
  );
}

export const Pill = ({ tono, children, className, ...rest }: { tono: string; children: ReactNode } & React.HTMLAttributes<HTMLSpanElement>) =>
  <span className={`fl-pill is-${tono}${className ? ' ' + className : ''}`} {...rest}>{children}</span>;

function Intestazione({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), o = d.overview, c = o?.copertura, cg = o?.controllo_giornaliero;
  const ora = new Date();
  const corre = o?.aggiornamento?.status === 'running';
  const fine = o?.aggiornamento?.finished_at ?? cg?.ultimo_fine_at ?? null;
  const titoli = o?.titoli ?? [];
  const daConfermare = titoli.filter(t => statoDi(t) === 'da_confermare').length;
  const novita = titoli.filter(t => statoDi(t) === 'novita').length;
  const mancanti = d.ambito === 'portafoglio' ? (c?.senza_profilo ?? 0) : 0;
  return (
    <div className="fl-top">
      <h1>{w.title}</h1>
      <Segmenti etichetta={w.scope} valore={d.ambito} onChange={a.ambito} className="is-large"
        opzioni={[{ id: 'portafoglio', testo: w.portfolio }, { id: 'preferiti', testo: w.favorites }]} />
      {c && <span className="bbn-chip" data-filing-copertura="1"><b>{c.con_confronto}</b> {w.covered(c.con_confronto, c.totale)}</span>}
      <span className="bbn-chip" data-filing-controllo={corre ? 'running' : 'idle'}>
        <i className={'fl-dot' + (corre ? ' is-live' : o?.aggiornamento?.status === 'error' ? ' is-bad' : '')} />
        {corre ? w.checkRunning : fine ? w.checkedAt(quando(fine, ora, d.lingua, w) || '') : w.checkNever}
      </span>
      {novita > 0 && <span className="bbn-chip is-acc" data-filing-novita={novita}><i className="fl-dot is-acc" /><b>{novita}</b> {w.newChip(novita)}</span>}
      <span className="bbn-grow" />
      {cg && <button type="button" role="switch" aria-checked={cg.attivo} className="bbn-switch fl-switch" data-filing-azione="auto"
        disabled={cg.forzato_spento_da_env || d.lavoro === 'auto'} title={cg.forzato_spento_da_env ? w.dailyForced : w.dailyHint}
        onClick={() => a.autoRefresh()}><i />{w.daily}</button>}
      {daConfermare > 0 && <button type="button" className="bbn-btn" data-filing-azione="rivedi" onClick={a.rivedi}>
        <Link2 size={15} aria-hidden="true" />{w.review(daConfermare)}</button>}
      <button type="button" className="bbn-btn is-primary" data-filing-azione="attiva-mancanti"
        disabled={d.ambito !== 'portafoglio' || !o || mancanti === 0 || d.lavoro === 'mancanti'}
        title={d.ambito !== 'portafoglio' ? w.activateMissingFav : w.activateMissingHint} onClick={() => a.attivaMancanti()}>
        {d.lavoro === 'mancanti' ? <Loader2 size={15} className="fl-spin" aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />}
        {d.lavoro === 'mancanti' ? w.activatingMissing : w.activateMissing}
      </button>
    </div>
  );
}

/** Avviso giallo «Da sistemare» con i titoli da rivedere; «Rivedi» apre il successivo. */
function DaSistemare({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole();
  const lista = d.gruppi.find(g => g.gruppo === 'da_sistemare')?.titoli ?? [];
  if (!lista.length) return null;
  const MAX = 4, mostrati = lista.slice(0, MAX);
  const prossimo = () => {
    const i = lista.findIndex(t => t.ticker === d.sel);
    a.scegli(lista[(i + 1) % lista.length].ticker);
  };
  return (
    <div className="fl-note is-warn fl-fix" role="status" data-filing-sistemare={lista.length}>
      <TriangleAlert size={16} aria-hidden="true" />
      <span><b>{w.fixTitle}</b> {mostrati.map(t => `${t.nome || t.ticker}: ${w.fixWhy[statoDi(t)] || w.tag[statoDi(t)]}`).join(' · ')}
        {lista.length > MAX && ` · ${w.fixMore(lista.length - MAX)}`}.</span>
      <button type="button" className="bbn-link" data-filing-azione="rivedi-sistemare" onClick={prossimo}>{w.fixReview}</button>
    </div>
  );
}

function Panoramica({ d }: { d: DatiFiling }) {
  const w = parole(), o = d.overview;
  if (!o) return null;
  const s = striscia(o), ctx = o.contesto;
  const quota = ctx.budget > 0 ? Math.min(100, ctx.caratteri / ctx.budget * 100) : 0;
  const fav = d.ambito === 'preferiti';
  const riquadri: [string, string, number, string][] = [
    ['ok', w.tFresh, s.aggiornati, w.tFreshS],
    ['warn', w.tStale, s.daAggiornare, w.tStaleS],
    ['acc', w.tConfirm, s.daConfermare, w.tConfirmS],
    ['off', w.tNoSource, s.senzaFonte, s.esclusi > 0 ? `${w.tNoSourceS} · ${w.tExcludedS(s.esclusi)}` : w.tNoSourceS],
  ];
  return (
    <div className="fl-overview" aria-label={w.ctxTitle} data-filing-striscia="1">
      <Carta icona={<Bot size={16} />} titolo={w.ctxTitle} nota={fav ? w.ctxGlossFav : w.ctxGloss} className="fl-ctxcard">
        <div className="fl-card-body">
          <div className="fl-pstats">
            {riquadri.map(([tono, k, v, sub]) => <div key={k} className="fl-ps">
              <span className="k"><i className={`fl-dot is-${tono}`} />{k}</span><span className="v num">{v}</span><span className="s">{sub}</span>
            </div>)}
          </div>
        </div>
      </Carta>
      <Carta titolo={w.budget} nota={w.budgetNote} className="fl-budget" data-filing-budget={ctx.caratteri}>
        <div className="fl-card-body">
          <div className="fl-budget-v"><b className="num">{fmt(d.lingua, ctx.caratteri)}</b>
            <span className={ctx.omessi_totali > 0 ? 'is-warn' : undefined}>{w.budgetLine(fmt(d.lingua, ctx.budget), ctx.omessi_totali)}</span></div>
          <div className="fl-bar" aria-hidden="true"><i style={{ width: `${quota}%`, background: ctx.omessi_totali > 0 ? 'var(--bbn-warn)' : 'var(--fl-cons)' }} /></div>
        </div>
      </Carta>
    </div>
  );
}

function sottotitolo(t: FilingOverviewTitolo, w: ReturnType<typeof parole>): string {
  const s = statoDi(t);
  const base = t.documento || t.fonte || '';
  if (s === 'da_confermare') return [base.split(' ')[0] || null, t.attivazione?.candidati ? w.sub.candidates(t.attivazione.candidati) : null].filter(Boolean).join(' · ') || w.tag.da_confermare;
  if (s === 'senza_fonte') return t.pdf_ir ? w.sub.pdfFound : w.sub.noSource;
  if (s === 'non_attivo') return t.attivazione?.esito === 'scollegato' ? w.sub.unlinked : t.profilo ? w.sub.autoOff : w.sub.notActive;
  if (s === 'escluso') return w.sub.excluded;
  return base;
}

function Riga({ t, d, a }: { t: FilingOverviewTitolo; d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), s = statoDi(t), ora = new Date();
  const data = t.run_attivo ? w.now : quando(t.ultimo_confronto, ora, d.lingua, w, true);
  return (
    <button type="button" className={'fl-row' + (t.ticker === d.sel ? ' is-on' : '')} aria-pressed={t.ticker === d.sel}
      data-filing-ticker={t.ticker} data-filing-stato={s} onClick={() => a.scegli(t.ticker)}>
      <IconaTitolo ticker={t.ticker} nome={t.nome} dimensione="sm" />
      <span className="fl-row-name"><b>{t.nome || t.ticker}</b><span>{t.ticker} · {sottotitolo(t, w)}</span></span>
      <span className="fl-row-end">
        {s === 'novita' && (t.cambiamenti ?? 0) > 0
          ? <Pill tono="acc" className="fl-nuovi">{w.nuovi(t.cambiamenti ?? 0)}</Pill>
          : s === 'primo_confronto' && motivoSenzaConfronto(t.stato_riga)
            ? <Pill tono="warn" title={motivoSenzaConfronto(t.stato_riga) || undefined}>{w.noPairTag}</Pill>
            : <Pill tono={tonoStato(s)}>{w.tag[s]}</Pill>}
        <small>{data || '—'}</small>
      </span>
    </button>
  );
}

function Elenco({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), n = d.overview?.titoli.length;
  return (
    <Carta icona={<List size={16} />} titolo={w.list} nota={n != null ? w.listNote(n, d.ordine === 'az') : null} className="fl-list"
      azioni={<Segmenti etichetta={w.order} valore={d.ordine} onChange={a.ordine}
        opzioni={[{ id: 'novita', testo: w.orderNew }, { id: 'az', testo: w.orderAz }]} />}>
      <div className="fl-list-body">
        {d.overviewErr && <div className="fl-note is-bad" role="alert" data-filing-errore={d.overviewErr.stato ?? 'rete'}>
          <CircleAlert size={16} aria-hidden="true" /><span><b>{d.overviewErr.stato === 503 ? w.unavailable : w.readError}.</b> {d.overviewErr.msg}</span>
          <button type="button" className="bbn-link" onClick={a.riprovaElenco}>{w.retry}</button></div>}
        {!d.overview && !d.overviewErr && <p className="bbn-empty">{w.loading}</p>}
        {d.overview && d.overview.titoli.length === 0 && <p className="bbn-empty">{d.ambito === 'preferiti' ? w.listEmptyFav : w.listEmpty}</p>}
        {d.gruppi.map(g => <div key={g.gruppo} className="fl-grp" data-filing-gruppo={g.gruppo}>
          <h3>{w.groups[g.gruppo]}<span>{g.titoli.length}</span></h3>
          {g.titoli.map(t => <Riga key={t.ticker} t={t} d={d} a={a} />)}
        </div>)}
      </div>
    </Carta>
  );
}

type Riquadro = [string, ReactNode, string?, string?];

/** Quattro riquadri del dettaglio (etichetta, valore, riga sotto, tono), diversi per stato come nel mockup v2. */
function riquadri(d: DatiFiling): Riquadro[] {
  const w = parole(), t = d.titolo!, s = d.stato!, ora = new Date();
  const r = d.detail?.result, profilo = d.listing?.profile?.profile as Record<string, unknown> | undefined;
  const tipo = r?.variante || (profilo?.tipo as string | undefined);
  const diff = r?.confronto_corrente || r?.confronto_storico;
  const conf = coppiaBreve(r?.coppia ?? null, tipo);
  const eseguito = quando(d.detail?.finished_at ?? t.ultimo_confronto, ora, d.lingua, w);
  const prossimo = quando(t.prossimo_at ?? d.listing?.profile?.next_due_at ?? null, ora, d.lingua, w);
  const totSez = sezioniProfilo(profilo, tipo), fatte = diff?.sezioni_confrontate?.length;
  const copertura = totSez && fatte != null ? w.sections(Math.min(fatte, totSez), totSez) : r?.copertura?.stato === 'parziale' ? w.partial : r?.copertura?.stato || w.unknown;
  const sezioni = diff?.sezioni_confrontate?.length ? diff.sezioni_confrontate.join(', ') : undefined;
  // Fase F: senza coppia il riquadro dice «nessun confronto», mai «aggiornato» (repository ESEF fermo, emittente nuovo).
  // (motivo breve nel riquadro, per intero nella nota sopra i cambiamenti; un backend precedente non manda `freschezza`)
  const fresco = t.freschezza === 'senza_confronto' || motivoSenzaConfronto(t.stato_riga) || (t.freschezza == null && !t.ultimo_confronto) ? 'none'
    : t.freschezza === 'non_aggiornato' || t.stato_riga.includes('NON AGGIORNATO') || t.stato_riga.includes('NOT UPDATED') ? 'stale' : 'fresh';
  const doc = t.documento || t.fonte || w.unknown;
  if (s === 'in_corso') {
    const run = t.run_attivo ?? d.listing?.active_run;
    return [[w.source, doc, w.sFreeNoAi], [w.started, quando(run?.started_at, ora, d.lingua, w) || '—', w.triggers[run?.trigger || ''] || run?.trigger || undefined],
      [conf ? w.comparison : w.lastOk, conf || '—', eseguito ? w.sOf(eseguito) : undefined], [w.state, w.tag.in_corso, w.sChecking, 'warn']];
  }
  if (s === 'errore') {
    const err = t.ultimo_errore;
    const tentativo = quando(err?.at, ora, d.lingua, w);
    return [[w.source, doc, tentativo ? w.sTry(tentativo) : undefined], [w.lastOk, conf || '—', eseguito ? w.sOf(eseguito) : undefined],
      [w.outcome, w.tag.errore, err?.reason || undefined, 'bad'], [w.coverage, copertura, sezioni]];
  }
  if (s === 'da_confermare') {
    const esef = propostaEsef(d.proposta);
    const n = (esef ? d.proposta?.esef?.candidati.length : d.proposta?.sec.candidati.length) ?? t.attivazione?.candidati ?? null;
    return [[w.search, w.byName, (esef ? 'ESEF' : 'SEC') + w.toChoose], [w.candidatesL, n != null ? String(n) : '—', w.sCandidates],
      [w.cost, w.free, w.sNoAi], [w.consigliere, w.missingDeclared, w.sInRow]];
  }
  if (s === 'senza_fonte' || s === 'non_attivo' || s === 'escluso') {
    const p = d.proposta;
    return [['SEC', p ? (p.sec.stato === 'nessuno' ? '—' : p.sec.stato) : '—', p?.sec.motivo || (p ? w.sNoMatch : undefined)],
      ['ESEF', p?.esef ? (p.esef.stato === 'nessuno' ? '—' : p.esef.stato) : '—', p?.esef?.motivo || undefined],
      t.pdf_ir ? [w.offIr, w.offIrFound(t.pdf_ir.precedente ? 2 : 1), w.sOneClick, 'acc'] : [w.offIr, w.offIrNone, w.sOnRequest],
      [w.consigliere, w.missingDeclared, w.sInRow]];
  }
  if (s === 'proposta_ai' && d.aiEsito) {
    const e = d.aiEsito;
    const ver = e.verificate?.length ?? 0, tot = ver + (e.scartate?.length ?? 0);
    const anno = e.periodo?.fine?.slice(0, 4);
    const euro = e.costo_eur != null ? '€ ' + new Intl.NumberFormat(d.lingua === 'en' ? 'en-US' : 'it-IT', { minimumFractionDigits: 4 }).format(e.costo_eur) : w.unknown;
    const at = quando((e as { at?: string }).at, ora, d.lingua, w);
    return [[w.source, 'PDF IR', [e.tipo, anno].filter(Boolean).join(' ') || undefined], [w.model, e.modello || w.unknown, w.sOneCall],
      [w.cost, euro, at ? `${w.proposal.toLowerCase()} ${at}` : w.sLedger], [w.aiVerifiedL, w.aiVerifiedOf(ver, tot), w.sLocal]];
  }
  // fase F: emittente SEC nuovo, la riga sotto dichiara il confronto col trimestre precedente
  const sottoConf = [trimestreSuTrimestre(r) ? w.sQoq : null, eseguito ? w.sRun(eseguito) : null].filter(Boolean).join(' · ') || undefined;
  return [[w.source, doc, prossimo ? w.sNext(prossimo) : undefined], [w.comparison, conf || '—', sottoConf],
    fresco === 'none' ? [w.freshness, w.noPairTag, motivoSenzaConfronto(t.stato_riga) ? motivoBreve(motivoSenzaConfronto(t.stato_riga)!) : w.sWaitFirst, 'warn']
      : [w.freshness, fresco === 'fresh' ? w.fresh : w.stale, fresco === 'fresh' ? w.sFresh : w.sStale, fresco === 'fresh' ? 'ok' : 'warn'],
    [w.coverage, copertura, sezioni, r?.copertura?.stato === 'parziale' ? 'warn' : undefined]];
}

function Testata({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), t = d.titolo!, s = d.stato!;
  const profilo = d.listing?.profile;
  const p = profilo?.profile as { esef_modo?: string; cik?: string; lei?: string } | undefined;
  const esef = p?.esef_modo === 'blocchi';
  const conAi = !!profilo?.qualitative_enabled;
  const meta = [t.ticker, t.documento || t.fonte, p?.cik ? `CIK ${p.cik}` : p?.lei ? `LEI ${p.lei}` : null].filter(Boolean).join(' · ');
  return (
    <header className="fl-det-head" data-filing-testata={t.ticker}>
      <IconaTitolo ticker={t.ticker} nome={t.nome} dimensione="lg" />
      <div className="fl-det-t"><h2>{t.nome || t.ticker}</h2><span>{meta}</span></div>
      <span className="bbn-grow" />
      <div className="fl-det-act">
        {w.pill[s] && <Pill tono={tonoStato(s)} data-filing-pill={s}>{s === 'in_corso' && <i className="fl-dot is-live" />}{w.pill[s]}</Pill>}
        {esef && s !== 'in_corso' && s !== 'proposta_ai' && !d.aiAperta && <button type="button" className="bbn-link" data-filing-azione="ai-variante" title={w.aiInterimHint} onClick={a.apriAi}>
          <Sparkles size={14} aria-hidden="true" />{w.aiInterim}</button>}
        {/* sempre raggiungibile: senza profilo si scrive a mano in JSON, come nel vecchio pannello */}
        <button type="button" className="bbn-link" data-filing-azione="profilo" aria-expanded={d.profiloAperto} onClick={a.profilo}>
          <Settings2 size={14} aria-hidden="true" />{d.profiloAperto ? w.closeProfile : w.openProfile}</button>
        {profilo && s !== 'in_corso' && s !== 'proposta_ai' && <button type="button" className="bbn-btn" data-filing-azione="verifica"
          disabled={!!d.listing?.active_run || d.lavoro === 'verifica'} title={conAi ? w.checkNowAiHint : w.checkNowHint} onClick={() => a.verifica()}>
          <RefreshCw size={15} aria-hidden="true" className={d.lavoro === 'verifica' ? 'fl-spin' : undefined} />
          {s === 'errore' ? (conAi ? w.retryCheckAi : w.retryCheck) : conAi ? w.checkNowAi : w.checkNow}</button>}
      </div>
    </header>
  );
}

function EsitoMancanti({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), e = d.esitoMancanti!;
  return (
    <div className="fl-note is-acc fl-esito" role="status" data-filing-esito="1">
      {d.primiInCorso ? <Loader2 size={16} className="fl-spin" aria-hidden="true" /> : <FileSearch size={16} aria-hidden="true" />}
      <span><b>{w.bulkResult(e.attivati.length, e.da_confermare.length, e.senza_fonte.length)}</b>
        {e.errori.length > 0 && <> · {w.bulkErrors(e.errori.length)}: {e.errori.map(x => `${x.ticker} (${x.motivo})`).join(', ')}</>}
        {e.attivati.length > 0 && <> · {d.primiInCorso ? (e.aggiornamento ? w.bulkQueued : w.bulkFirst) : w.bulkDone}</>}</span>
      <button type="button" className="bbn-icon-btn" aria-label={w.close} onClick={a.chiudiEsito}><X size={14} /></button>
    </div>
  );
}

export default function VistaFiling({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole();
  return (
    <div className="bbn-filing bbn-font" data-ambito={d.ambito}>
      <Intestazione d={d} a={a} />
      {d.esitoMancanti && <EsitoMancanti d={d} a={a} />}
      <DaSistemare d={d} a={a} />
      {/* esiti delle azioni dell'intestazione senza un titolo aperto: altrimenti stanno nel dettaglio */}
      {d.avviso && !d.titolo && <p className={`fl-note is-${d.avviso.tono === 'ok' ? 'ok' : 'bad'}`} role={d.avviso.tono === 'ok' ? 'status' : 'alert'}
        data-filing-avviso={d.avviso.tono}>{d.avviso.testo}</p>}
      <Panoramica d={d} />
      <div className="fl-body">
        <Elenco d={d} a={a} />
        {d.titolo && d.stato ? <section className="bbn-card fl-det" data-filing-dettaglio={d.titolo.ticker} data-filing-gruppo-ui={gruppoDi(d.titolo)}>
          <Testata d={d} a={a} />
          <div className="fl-det-body">
            <div className="fl-tiles">
              {riquadri(d).map(([k, v, sub, tono]) => <div key={k} className="fl-tile">
                <span className="k">{k}</span><span className={'v' + (tono ? ` is-${tono}` : '')}>{v}</span>{sub && <span className="s" title={sub}>{sub}</span>}
              </div>)}
            </div>
            {d.avviso && <p className={`fl-note is-${d.avviso.tono === 'ok' ? 'ok' : 'bad'}`} role={d.avviso.tono === 'ok' ? 'status' : 'alert'}
              data-filing-avviso={d.avviso.tono}>{d.avviso.testo}</p>}
            <div className="fl-cols">
              <div className="fl-col fl-col-main"><Centro d={d} a={a} /></div>
              <div className="fl-col fl-col-side">
                <CardNumeri d={d} />
                <CardConsigliere d={d} a={a} />
              </div>
            </div>
          </div>
        </section> : <section className="bbn-card fl-det"><p className="bbn-empty">{d.overview ? w.pick : w.loading}</p></section>}
      </div>
    </div>
  );
}
