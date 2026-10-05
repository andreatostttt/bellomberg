/* Colonna destra del dettaglio: Numeri chiave (righe come il Centro di comando) e Cosa vede il Consigliere. */
import { Copy } from 'lucide-react';
import { scalaBreve, deltaNumero, idCitati, periodoBreve } from './logica';
import { parole } from './parole';
import type { AzioniFiling, DatiFiling } from './tipi';
import { fmt, Sezione } from './VistaFiling';

const INVERSI = new Set(['scorte', 'debito']);

export function CardNumeri({ d }: { d: DatiFiling }) {
  const w = parole(), lingua = d.lingua === 'en' ? 'en' : 'it';
  const r = d.detail?.result, n = r?.numeri;
  const profilo = d.listing?.profile?.profile as { fonti?: string[]; varianti?: { fonti?: string[] }[] } | undefined;
  const soloIr = !!profilo && JSON.stringify(profilo.fonti) === '["ir"]' && !profilo.varianti;
  const tipo = n?.variante || r?.variante;
  const fine = (k: 'prima' | 'dopo') => r?.coppia?.[k]?.metadati?.periodo_fine;
  // numeri da un'altra variante (es. annuale ESEF accanto alla semestrale IR): periodi senza etichetta di tipo
  // numeri da un'altra variante (es. annuale accanto al 6-K): periodi di QUELLA variante (prova reale 04/10/2026)
  const altra = n?.variante ? (r?.varianti ?? []).find(v => v.tipo === n.variante)?.coppia_periodi : null;
  const periodo = (k: 'prima' | 'dopo') => n?.variante
    ? (altra ? periodoBreve(altra[k === 'prima' ? 0 : 1], n.variante) : null)
    : periodoBreve(fine(k), tipo);
  const voci = n?.stato === 'ok' ? n.voci ?? [] : [];
  const etichetta = n?.variante ? w.figVariant(n.variante) : tipo ? w.figPeriodo[tipo] ?? null : null;
  const vuoto = !d.titolo?.profilo || d.stato === 'senza_fonte' || d.stato === 'non_attivo' || d.stato === 'escluso' || d.stato === 'da_confermare';
  // titoli senza profilo (da confermare, senza fonte, esclusi): nessuna sezione Numeri, come nel mockup v2
  if (vuoto && !soloIr) return null;
  const nota = [n?.confronto === 'trimestre_precedente' ? w.figNoteQoq : n?.fonte?.includes('SEC') ? w.figNoteSec : n?.fonte?.includes('xbrl.org') || n?.fonte?.includes('ESEF') ? w.figNoteEsef : null,
    etichetta].filter(Boolean).join(' · ');
  return (
    <Sezione titolo={w.figures} nota={voci.length ? nota : null} className="fl-num" data-filing-numeri={voci.length}
      piede={voci.length ? w.figSource(n?.fonte || w.unknown, n?.valuta || '', n?.confronto === 'trimestre_precedente') : null}>
      {voci.length ? <div className="fl-kn" role="table" aria-label={w.figures}>
        <div className="fl-kn-h" role="row"><span role="columnheader"><span className="fl-sr">{w.figures}</span></span>
          <span role="columnheader">{periodo('prima') || w.advBefore}</span><span role="columnheader">{periodo('dopo') || w.advAfter}</span><span role="columnheader">Δ</span></div>
        {voci.map(v => {
          const delta = deltaNumero(v.delta_pct, lingua, INVERSI.has(v.voce));
          return <div key={v.voce} className="fl-kn-r" role="row" data-voce={v.voce}>
            <span role="cell">{w.figVoce[v.voce] || v.voce.replace(/_/g, ' ')}</span>
            <span role="cell" className="a num">{w.cifraBreve(scalaBreve(v.prima, lingua))}</span><span role="cell" className="b num">{w.cifraBreve(scalaBreve(v.dopo, lingua))}</span>
            <span role="cell"><span className={`fl-d is-${delta.tono}`}>{delta.testo}</span></span>
          </div>;
        })}
      </div> : <p className="fl-empty">{soloIr ? w.figIr : !r ? w.figAfterFirst : n && n.stato !== 'ok' ? `${w.figNone}${n.motivo ? `: ${n.motivo}` : ''}` : w.figNone}</p>}
    </Sezione>
  );
}

/** Evidenzia ticker, etichette di riga e citazioni nel testo esatto del contesto (il testo non cambia). */
const TESTA = /^([A-Z0-9][A-Z0-9.\-]*(?= · )|numeri(?= )|figures(?= )|[+~−↔-] [^«:\[]+?(?= «))/;
function Riga({ testo }: { testo: string }) {
  const m = TESTA.exec(testo), resto = m ? testo.slice(m[1].length) : testo;
  return <>{m && <span className="k">{m[1]}</span>}{resto.split(/(\[[^\]]*\])/g).map((p, i) =>
    p.startsWith('[') ? <span key={i} className="s">{p}</span> : <span key={i}>{p}</span>)}</>;
}

export function CardConsigliere({ d, a }: { d: DatiFiling; a: AzioniFiling }) {
  const w = parole(), p = d.preview, s = d.stato;
  const fuori = d.ambito === 'preferiti' || d.titolo?.nel_contesto === false;
  const citati = p ? idCitati(p.testo).length : 0;
  const totale = d.titolo?.cambiamenti ?? 0;
  const voci = d.detail?.result?.numeri?.stato === 'ok' ? d.detail.result.numeri.voci?.length ?? 0 : 0;
  const stale = !!p && /NON AGGIORNATO|NOT UPDATED/.test(p.testo);
  const piede = fuori ? null : s === 'proposta_ai' ? w.consFootAi : !d.titolo?.profilo || s === 'da_confermare' || s === 'senza_fonte' || s === 'non_attivo' ? w.consFootMissing
    : stale || s === 'errore' || s === 'in_corso' ? w.consFootStale : w.consFoot;
  return (
    <Sezione titolo={w.cons} nota={fuori ? null : w.consNote} className="fl-cons" data-filing-consigliere={p?.caratteri ?? 0}
      azioni={p?.testo ? <button type="button" className="bbn-link" data-filing-azione="copia" onClick={a.copia}><Copy size={14} aria-hidden="true" />{d.copiato ? w.copied : w.copy}</button> : null}
      piede={piede}>
      {fuori ? <p className="fl-note">{w.consNotIn}</p>
        : d.previewErr ? <p className="fl-note is-bad" role="alert">{w.consError}: {d.previewErr}</p>
        : !p ? <p className="fl-empty">{w.loading}</p>
        : <>
          <div className="fl-cons-meta">
            <span className="bbn-chip"><b>{fmt(d.lingua, p.caratteri)}</b> {w.consCharsL}</span>
            {totale > 0 && <span className="bbn-chip"><b>{citati}</b> {w.consChangesL(totale)}</span>}
            {voci > 0 && <span className="bbn-chip"><b>{voci}</b> {w.consFiguresL(voci)}</span>}
            {p.omessi > 0 && <span className="bbn-chip is-warn">{w.consOmitted(p.omessi)}</span>}
            {citati > 0 && <span className="bbn-chip">{w.consCites}</span>}
            {stale && <span className="bbn-chip">{w.consPrevious}</span>}
          </div>
          <pre className="fl-ctx" data-filing-testo="1">{p.testo.split('\n').map((riga, i) => <span key={i}>{i > 0 && '\n'}<Riga testo={riga} /></span>)}</pre>
        </>}
    </Sezione>
  );
}
