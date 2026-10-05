/* Copertura filing per il Comitato (fase D, 03/10/2026): quanti titoli arrivano al
   Consigliere con un confronto tra filing SEC, quanto contesto occupano e il pulsante
   che attiva in blocco i profili mancanti. Solo presentazione: dati, errore ed esito
   arrivano da AgentsLive (stato in coda agli useState, gotcha dei test SSR).
   Nessuna chiamata AI: l'attivazione crea profili, i confronti li fa la pipeline. */
import { CircleAlert, RefreshCw } from 'lucide-react';
import type { FilingActivateMissing, FilingOverview, FilingOverviewTitolo } from '@/lib/api';
import { Barra } from './VistaAgenti';
import { parole } from './parole';
import { statoDi, tonoStato } from '../filing/logica';
import { parole as paroleFiling } from '../filing/parole';

export interface ErroreFiling { stato: number | null; msg: string }

/** titoli senza profilo e non esclusi: quelli che «Attiva i mancanti» prova ad attivare */
export const mancantiFiling = (dati: FilingOverview | null) => dati?.copertura.senza_profilo ?? 0;

/** riga di stato senza «TICKER · fonte · » iniziali: ticker e fonte la riga li mostra gia' */
function dettaglio(t: FilingOverviewTitolo): string {
  let s = t.stato_riga;
  if (s.startsWith(t.ticker + ' · ')) s = s.slice(t.ticker.length + 3);
  if (t.fonte && s.startsWith(t.fonte + ' · ')) s = s.slice(t.fonte.length + 3);
  return t.fonte ? t.fonte + ' · ' + s : s;
}

/** Stesse etichette e stessi toni della pagina Filing (fase E): un solo vocabolario degli stati. */
const TONI = { ok: 'is-su', bad: 'is-giu', warn: 'is-warn', acc: 'is-acc', off: 'is-piatto' } as const;
function etichetta(t: FilingOverviewTitolo): [string, string] {
  const s = statoDi(t);
  return [paroleFiling().tag[s], TONI[tonoStato(s)]];
}

export default function CoperturaFiling({ dati, errore, occupato, bloccato, esito, onAttiva, onRiprova, fmt, conTitolo = true, onApri }: {
  dati: FilingOverview | null;
  errore: ErroreFiling | null;
  /** attivazione in corso */
  occupato: boolean;
  /** run del comitato in corso: i profili non si toccano finche' non chiude */
  bloccato: boolean;
  esito: FilingActivateMissing | null;
  onAttiva: () => void;
  /** rilegge /filings dopo un errore */
  onRiprova: () => void;
  fmt: (n: number) => string;
  /** nel pannello a schede il titolo sta nel corpo; nella card del 49" sta nell'intestazione */
  conTitolo?: boolean;
  /** apre la pagina Filing (sul titolo, se dato) */
  onApri?: (ticker?: string) => void;
}) {
  const w = parole();
  const nonDisponibile = errore?.stato === 503;
  const c = dati?.copertura;
  const mancanti = mancantiFiling(dati);
  const disattivo = !dati || !!errore || mancanti === 0 || bloccato || occupato;
  const motivo = occupato ? null : errore ? null : bloccato ? w.filingRunActive : dati && mancanti === 0 ? w.filingNoneMissing : dati ? w.filingActivateHelp : null;
  const titoloErr = nonDisponibile ? w.filingUnavailable : w.filingReadError;
  /* il backend apre il dettaglio con la stessa frase del titolo: non si ripete */
  const dettaglioErr = errore ? errore.msg.replace(/^(Archivio filing non (disponibile|leggibile)|Filing archive (unavailable|cannot be read)):\s*/i, '') : '';
  const quota = (n: number) => c && c.totale > 0 ? `${n / c.totale * 100}%` : '0%';
  const ctx = dati?.contesto;
  const corre = dati?.aggiornamento?.status === 'running';

  return (
    <div className="ag-filing" data-filing-stato={errore ? (nonDisponibile ? 'non-disponibile' : 'errore') : !dati ? 'caricamento' : mancanti ? 'mancanti' : 'completa'}>
      {conTitolo && <div className="ag-filing-top">
        <b className="ag-filing-t">{w.filingTitle}</b>
        <p className="ag-foot">{w.filingGloss}</p>
      </div>}
      {!conTitolo && <p className="ag-foot ag-filing-gloss">{w.filingGloss}</p>}

      {errore && <div className="ag-ban is-bad" role="alert" data-filing-errore={errore.stato ?? 'rete'}>
        <CircleAlert size={18} /><span><b>{titoloErr}.</b>{dettaglioErr ? ' ' + dettaglioErr : ''}</span></div>}
      {!dati && !errore && <p className="bbn-empty">{w.filingLoading}</p>}

      {c && <div className="ag-filing-cov">
        <div className="ag-big"><span className="num">{fmt(c.con_confronto)}</span><span className="of num">/ {fmt(c.totale)}</span></div>
        <span className="ag-filing-cov-t">{w.filingCoverage(c.con_confronto, c.totale)}</span>
        <span className="ag-bar ag-filing-stack" aria-hidden="true">
          <u style={{ width: quota(c.aggiornati), background: 'var(--bbn-good)' }} />
          <u style={{ width: quota(c.non_aggiornati), background: 'var(--bbn-warn)' }} />
          <u style={{ width: quota(c.senza_confronto ?? 0), background: 'var(--ag-filing-pending)' }} />
          <u style={{ width: quota(c.senza_profilo), background: 'var(--ag-filing-missing)' }} />
        </span>
        <ul className="ag-filing-legend">
          <li><i style={{ background: 'var(--bbn-good)' }} />{w.filingFresh(c.aggiornati)}</li>
          <li><i style={{ background: 'var(--bbn-warn)' }} />{w.filingStale(c.non_aggiornati)}</li>
          {(c.senza_confronto ?? 0) > 0 && <li><i style={{ background: 'var(--ag-filing-pending)' }} />{w.filingNoComparison(c.senza_confronto ?? 0)}</li>}
          <li><i style={{ background: 'var(--ag-filing-missing)' }} />{w.filingNoProfile(c.senza_profilo)}</li>
          {c.esclusi > 0 && <li><i style={{ background: 'var(--ag-track)' }} />{w.filingExcluded(c.esclusi)}</li>}
        </ul>
      </div>}

      {ctx && <div className="ag-filing-ctx">
        <div className="ag-filing-row"><span className="ag-sub">{w.filingContext}</span>
          <span className="v num">{w.filingChars(fmt(ctx.caratteri), fmt(ctx.budget))}</span></div>
        <Barra quota={ctx.budget > 0 ? ctx.caratteri / ctx.budget : 0} colore={ctx.omessi_totali > 0 ? 'var(--bbn-warn)' : 'var(--bbn-accent)'} />
        <p className="ag-foot">{ctx.omessi_totali > 0 ? w.filingOmitted(ctx.omessi_totali) : w.filingContextFull}</p>
      </div>}

      <div className="ag-filing-act">
        <button type="button" className="bbn-btn is-primary is-sm" data-filing-activate="1" disabled={disattivo} onClick={onAttiva}>
          {occupato ? w.filingActivating : w.filingActivate}</button>
        {motivo && <span className="ag-foot">{motivo}</span>}
        {errore && <button type="button" className="bbn-link" data-filing-retry="1" onClick={onRiprova}>{w.filingRetry}</button>}
        {onApri && <button type="button" className="bbn-link" data-filing-pagina="1" onClick={() => onApri()}>{w.filingOpenPage}</button>}
      </div>
      {corre && <p className="ag-filing-run"><RefreshCw size={14} />{w.filingRefreshing}</p>}
      {/* RUN-ANDREA (Opus 5.5, 05/10): copertura incompleta o archivio giu' non sono un cancello della run */}
      {(errore || (c && c.con_confronto < c.totale)) && <p className="ag-foot" data-filing-non-blocca="1">{w.filingNotBlocking}</p>}

      {esito && <div className="ag-filing-esito" role="status" data-filing-esito="1">
        {esito.avviso_configurazione && <div className="ag-ban is-warn" data-filing-configurazione="sec">
          <CircleAlert size={18} /><span><b>{w.filingSecNotConfigured}</b></span></div>}
        <b>{w.filingResult(esito.attivati.length, esito.da_confermare.length, esito.senza_fonte.length)}</b>
        {esito.da_confermare.length > 0 && <div className="ag-filing-grp"><span className="ag-sub">{w.filingToConfirm}</span>
          <span className="ag-filing-tks">{esito.da_confermare.map(t => <span key={t} className="ag-tk" data-filing-confirm={t}>{t}</span>)}</span>
          <span className="ag-foot">{w.filingToConfirmHelp}</span></div>}
        {esito.senza_fonte.length > 0 && <div className="ag-filing-grp"><span className="ag-sub">{w.filingNoSource}</span>
          <span className="ag-filing-tks">{esito.senza_fonte.map(t => <span key={t} className="ag-tk">{t}</span>)}</span>
          {esito.senza_fonte.filter(t => esito.motivi?.[t]).map(t => <span key={t} className="ag-foot" data-filing-motivo={t}><b>{t}</b> · {esito.motivi?.[t]}</span>)}</div>}
        {esito.errori.length > 0 && <div className="ag-filing-grp"><span className="ag-sub">{w.filingErrors}</span>
          {esito.errori.map(e => <span key={e.ticker} className="ag-foot"><b>{e.ticker}</b> · {e.motivo}</span>)}</div>}
        {esito.attivati.length > 0 && <span className="ag-foot">{esito.aggiornamento ? w.filingQueued : w.filingStarted}</span>}
      </div>}

      {dati && dati.titoli.length > 0 && <>
        <div className="ag-sub ag-filing-lh">{w.filingHoldings}</div>
        <div className="bbn-scroll ag-filing-list">
          {dati.titoli.map(t => {
            const [tag, tono] = etichetta(t);
            const riga = <><b>{t.ticker}</b><span className="s">{dettaglio(t)}</span><span className={'bbn-pill ' + tono}>{tag}</span></>;
            return onApri
              ? <button type="button" key={t.ticker} className="ag-filing-tk is-link" data-filing-ticker={t.ticker} title={t.stato_riga} onClick={() => onApri(t.ticker)}>{riga}</button>
              : <div key={t.ticker} className="ag-filing-tk" data-filing-ticker={t.ticker} title={t.stato_riga}>{riga}</div>;
          })}
        </div>
      </>}
    </div>
  );
}
