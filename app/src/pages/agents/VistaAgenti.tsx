import type { ReactNode } from 'react';
import { AlertCircle, Brain, Check, CircleAlert, CirclePause, FileText, Hourglass, ListChecks, RefreshCw, ShieldAlert, Wrench } from 'lucide-react';
import IconaDesk, { coloreDesk } from '../chat/IconaDesk';

/* Presentazione della pagina Agenti in diretta (palette --bbn-*). Solo vernice:
   i dati arrivano già calcolati da AgentsLive, che tiene polling, avvio e stop. */

export type StatoDesk = 'run' | 'think' | 'ok' | 'ko' | 'nd' | 'wait' | 'stale';
export type StatoTappa = 'done' | 'now' | 'wait' | 'skip' | 'ko';
export type StatoRound = 'ok' | 'on' | 'ko' | 'off' | '';

/** Icona tonda del desk (come in Chat) con l'anello di stato. */
export function AnelloDesk({ id, colore, stato, grande = false }: {
  id: string; colore?: string | null; stato: StatoDesk; grande?: boolean;
}) {
  const c = grande ? 34 : 28, r = grande ? 32 : 26;
  const giro = stato === 'run' || stato === 'think' || stato === 'stale';
  const tratteggio = stato === 'wait' || stato === 'nd' ? '2 6' : giro ? undefined : `${2 * Math.PI * r} 0`;
  return (
    <span className={`ag-ring is-${stato}${grande ? ' is-lg' : ''}`} data-stato={stato}>
      <svg className="ag-ring-svg" viewBox={`0 0 ${c * 2} ${c * 2}`} aria-hidden="true">
        <circle className="trk" cx={c} cy={c} r={r} />
        <circle className="arc" cx={c} cy={c} r={r} strokeDasharray={tratteggio} />
      </svg>
      <IconaDesk id={id} colore={colore} dimensione="md" />
      {stato === 'ok' && <span className="ag-ring-badge"><Check size={11} strokeWidth={3} /></span>}
      {stato === 'ko' && <span className="ag-ring-badge"><AlertCircle size={11} strokeWidth={3} /></span>}
      {stato === 'nd' && <span className="ag-ring-badge">!</span>}
    </span>
  );
}

export function Kpi({ etichetta, valore, sotto, tono, nd = false }: {
  etichetta: string; valore: ReactNode; sotto?: ReactNode; tono?: 'warn' | 'bad'; nd?: boolean;
}) {
  return (
    <div className="ag-kpi">
      <span className="l">{etichetta}</span>
      <span className={'v num' + (nd ? ' is-nd' : '')}>{valore}</span>
      {sotto != null && <span className={'s' + (tono ? ' is-' + tono : '')}>{sotto}</span>}
    </div>
  );
}

export interface Tappa { id: string; nome: string; sotto: string; stato: StatoTappa }

export function Tappe({ tappe, etichetta }: { tappe: Tappa[]; etichetta: string }) {
  return (
    <ol className="ag-steps" aria-label={etichetta}>
      {tappe.map(t => (
        <li key={t.id} className={'ag-step is-' + t.stato} data-tappa={t.id} data-stato={t.stato}
          aria-current={t.stato === 'now' ? 'step' : undefined}>
          <span className="b">{t.stato === 'done' && <Check size={12} strokeWidth={3.5} />}</span>
          <span className="n">{t.nome}</span>
          <span className="d">{t.sotto}</span>
        </li>
      ))}
    </ol>
  );
}

export interface VistaDesk {
  id: string; nome: string; ruolo: string; colore: string; stato: StatoDesk;
  pastiglia: string; titolo: string;
  fare: { icona: 'tool' | 'think' | 'file' | 'ko' | 'wait' | 'pause'; testo: string; codice: boolean; sotto: string };
  round: StatoRound[]; dur: string; chiamate: string; costo: string; costoNd: boolean; dettaglio: string;
  esito: string;
}

const ICONA_FARE = { tool: Wrench, think: Brain, file: FileText, ko: CircleAlert, wait: Hourglass, pause: CirclePause };
const PASTIGLIA: Record<StatoDesk, string> = {
  run: 'is-acc', think: 'is-acc', ok: 'is-su', ko: 'is-giu', nd: 'is-warn', wait: 'is-piatto', stale: 'is-warn',
};

export function CardDesk({ d }: { d: VistaDesk }) {
  const Icona = ICONA_FARE[d.fare.icona];
  return (
    <article className={'bbn-card ag-desk is-' + d.stato} data-agente={d.id} data-esito={d.esito} title={d.titolo}>
      <div className="ag-desk-h">
        <AnelloDesk id={d.id} colore={d.colore} stato={d.stato} />
        <span className="nm"><b>{d.nome}</b><span>{d.ruolo}</span></span>
        <span className={'bbn-pill ' + PASTIGLIA[d.stato]}>
          {(d.stato === 'run' || d.stato === 'think') && <i className="ag-pulse" />}{d.pastiglia}
        </span>
      </div>
      <div className={'ag-doing' + (d.stato === 'ko' ? ' is-ko' : '')}>
        <span className="a"><Icona size={15} />{d.fare.codice ? <code>{d.fare.testo}</code> : <span>{d.fare.testo}</span>}</span>
        <span className="b">{d.fare.sotto}</span>
      </div>
      <div className="ag-desk-f" title={d.dettaglio}>
        <span className="ag-rds">{d.round.map((r, i) => <i key={i} className={r ? 'is-' + r : undefined}>R{i}</i>)}</span>
        <b className="num">{d.dur}</b>
        <span>{d.chiamate}</span>
        <span className={'c num' + (d.costoNd ? ' is-nd' : '')}>{d.costo}</span>
      </div>
    </article>
  );
}

export interface VistaCapo {
  stato: StatoDesk; titolo: string; sotto: string; pastiglia: string; colore: string | null;
  stadi: { id: string; nome: string; stato: 'ok' | 'on' | 'wait'; nota: string }[];
  azioni?: ReactNode;
}

const ICONA_STADIO: Record<string, typeof ShieldAlert> = { _red_team: ShieldAlert, _reflection: RefreshCw, _action_table: ListChecks };

export function CardCapo({ c, nome }: { c: VistaCapo; nome: string }) {
  return (
    <article className={'bbn-card ag-capo is-' + c.stato} data-agente="capo" data-stato={c.stato}>
      <div className="ag-capo-top">
        <AnelloDesk id="capo" colore={c.colore} stato={c.stato} />
        <span className="nm"><b>{nome} · {c.titolo}</b><span>{c.sotto}</span></span>
        <span className={'bbn-pill ' + PASTIGLIA[c.stato]}>{c.stato === 'run' && <i className="ag-pulse" />}{c.pastiglia}</span>
        {c.azioni}
      </div>
      <div className="ag-stages">
        {c.stadi.map(s => {
          const Icona = s.stato === 'ok' ? Check : ICONA_STADIO[s.id] || ListChecks;
          return (
            <div key={s.id} className={'ag-stage is-' + s.stato} data-stadio={s.id}>
              <Icona size={16} /><span>{s.nome}</span><em>{s.nota}</em>
            </div>
          );
        })}
      </div>
    </article>
  );
}

export interface Riga { k: string; v: number | null; label: string; colore: string }

/** Barra orizzontale su fondo raised; v null = buco dichiarato, mai una barra a zero spacciata. */
export function Barra({ quota, colore }: { quota: number; colore: string }) {
  return <span className="ag-bar"><u style={{ width: `${Math.max(0, Math.min(1, quota)) * 100}%`, background: colore }} /></span>;
}

export function coloreBarra(colore: string | null | undefined) { return coloreDesk(colore); }
