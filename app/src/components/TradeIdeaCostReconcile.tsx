import { useRef, useState } from 'react';
import { CircleAlert, ReceiptText } from 'lucide-react';
import { useT } from '@/i18n/provider';
import ConfirmDialog from '@/components/ConfirmDialog';
import { TradeIdeaApiError, TradeIdeas, type TradeIdeaCostOutcome, type TradeIdeaCostReconciliation, type TradeIdeaDetail } from '@/lib/tradeIdeas';

/** Numero di richieste dal costo incerto della catena di run, oppure null se il backend non lo dichiara. */
export function costiIncerti(detail: TradeIdeaDetail): number | null {
  const n = detail.cost?.unknown_requests;
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
}

/** Stati in cui la run NON è ferma: gli stessi di `inProgress` in TradeIdeaPage (in coda, in avvio,
 *  in arresto inclusi). Il backend rifiuta con 409 solo `running`, ma riconciliare a run non ferma
 *  leggerebbe costi ancora in movimento. */
const IN_CORSO = ['accepted', 'queued', 'pending', 'starting', 'running', 'stopping'];

/** Il pulsante compare solo su una run ferma con costi incerti (o bloccata da un costo non risolto). */
export function mostraRiconcilia(detail: TradeIdeaDetail): boolean {
  const status = detail.run.technical_status || detail.run.status || '';
  if (IN_CORSO.includes(status)) return false;
  return (costiIncerti(detail) ?? 0) > 0 || detail.recovery?.blocked_reason === 'provider_cost_unresolved';
}

/** «Riconcilia costi» (stile Nuova): anteprima gratuita, poi registrazione solo dopo la conferma esplicita. */
export default function TradeIdeaCostReconcile({ detail, onChanged }: {
  detail: TradeIdeaDetail; onChanged: (runId: string) => void;
}) {
  const t = useT(), lock = useRef(false);
  const [preview, setPreview] = useState<TradeIdeaCostReconciliation | null>(null);
  const [applied, setApplied] = useState<TradeIdeaCostReconciliation | null>(null);
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null);
  const [ask, setAsk] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = detail.run.id || detail.run.run_id;
  if (!id || (!mostraRiconcilia(detail) && !applied)) return null;
  const n = costiIncerti(detail);
  const shown = applied || preview;
  const ready = (preview?.outcomes || []).filter(o => o.status === 'settleable');

  const run = async (apply: boolean) => {
    if (lock.current) return;
    lock.current = true; setBusy(apply ? 'apply' : 'preview'); setAsk(false); setError(null);
    try {
      const result = await TradeIdeas.reconcileCosts(id, apply);
      if (apply) { setApplied(result); onChanged(id); } else { setPreview(result); setApplied(null); }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(e instanceof TradeIdeaApiError && e.status === 409
        ? t('tradeidea.costReconcileConflict', { error: message }) : t('tradeidea.costReconcileFailed', { error: message }));
    } finally { lock.current = false; setBusy(null); }
  };

  const stato = (o: TradeIdeaCostOutcome) => o.status === 'settled' ? t('tradeidea.costReconcileStatusSettled')
    : o.status === 'settleable' ? t('tradeidea.costReconcileStatusSettleable') : t('tradeidea.costReconcileStatusPending');
  const tono = (o: TradeIdeaCostOutcome) => o.status === 'pending' ? 'is-warn' : 'is-good';

  return <section className="bbn-card bbn-font tic-card" aria-label={t('tradeidea.costReconcileTitle')} data-trade-idea="costi-incerti">
    <header className="bbn-card-head"><ReceiptText size={17} aria-hidden="true" /><h2>{t('tradeidea.costReconcileTitle')}</h2>
      {n != null && <span className="bbn-warn-pill">{t('tradeidea.costReconcileCount', { n })}</span>}</header>
    <div className="tic-body">
      <p className="tic-note">{t('tradeidea.costReconcileIntro')}</p>
      <div className="tic-actions">
        <button type="button" className="bbn-btn" data-azione="riconcilia" disabled={busy !== null} onClick={() => void run(false)}>
          {busy === 'preview' ? t('tradeidea.costReconcileChecking') : t('tradeidea.costReconcileAction')}</button>
        {preview && !applied && ready.length > 0 && <button type="button" className="bbn-btn is-primary" data-azione="registra" disabled={busy !== null}
          onClick={() => setAsk(true)}>{busy === 'apply' ? t('tradeidea.costReconcileApplying') : t('tradeidea.costReconcileApply')}</button>}
      </div>
      {shown && <>
        <p className="tic-summary" role="status">{applied
          ? t('tradeidea.costReconcileSummary', { settled: applied.settled, pending: applied.pending })
          : t('tradeidea.costReconcilePreviewSummary', { ready: ready.length, pending: shown.pending })}
          {!applied && <span> · {t('tradeidea.costReconcilePreview')}</span>}</p>
        {applied && <p className="tic-note is-good">{t('tradeidea.costReconcileDone')}</p>}
        {shown.outcomes.length === 0 ? <p className="tic-note">{t('tradeidea.costReconcileNone')}</p> : <ul className="tic-list">
          {shown.outcomes.map(o => <li key={o.request_id} data-request={o.request_id} data-esito={o.status}>
            <div className="tic-row"><code>{o.request_id}</code><span className={'tic-pill ' + tono(o)}>{stato(o)}</span></div>
            {(o.role || o.model) && <span className="tic-note">{[o.role, o.model].filter(Boolean).join(' · ')}</span>}
            {o.charged_usd != null && <span className="tic-note num">{t('tradeidea.costReconcileCharged', { usd: String(o.charged_usd) })}</span>}
            {o.status === 'pending' && o.reserved_usd != null && <span className="tic-note num">{t('tradeidea.costReconcileReserved', { usd: String(o.reserved_usd) })}</span>}
            {/* il motivo di una richiesta ancora incerta si mostra sempre */}
            {o.status === 'pending' && <span className="tic-reason">{o.reason || t('tradeidea.costReconcileReasonMissing')}</span>}
          </li>)}
        </ul>}
      </>}
      {error && <p className="tic-note is-bad" role="alert"><CircleAlert size={15} aria-hidden="true" />{error}</p>}
    </div>
    <ConfirmDialog open={ask} title={t('tradeidea.costReconcileConfirmTitle')} intro={t('tradeidea.costReconcileConfirmIntro')}
      rows={[{ k: t('tradeidea.runId'), v: id }, { k: t('tradeidea.costReconcileConfirmRows'), v: ready.map(o => `${o.request_id} · ${o.charged_usd != null && String(o.charged_usd) !== '' ? `${o.charged_usd} USD` : t('tradeidea.costReconcileAmountMissing')}`).join(', ') }]}
      confirmLabel={t('tradeidea.costReconcileApply')} onConfirm={() => void run(true)} onCancel={() => setAsk(false)} />
  </section>;
}
