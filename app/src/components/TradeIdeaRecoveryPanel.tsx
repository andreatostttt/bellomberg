import { useRef, useState } from 'react';
import { useT } from '@/i18n/provider';
import ConfirmDialog from '@/components/ConfirmDialog';
import { TradeIdeas, type TradeIdeaDetail } from '@/lib/tradeIdeas';
import { researchOnly } from '@/components/TradeIdeaSourceReadiness';

type ResumeSelection = { runId: string; requestId?: string; failedRequestId?: string; priceRefresh: boolean };

export default function TradeIdeaRecoveryPanel({ detail, onChanged }: {
  detail: TradeIdeaDetail; onChanged: (runId: string) => void;
}) {
  const t = useT();
  const [ask, setAsk] = useState<ResumeSelection | null>(null), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false), keys = useRef<Record<string, string>>({});
  const id = detail.run.id || detail.run.run_id;
  const researchMode = researchOnly(detail.run);
  const recovery = detail.recovery;
  const reportRecovery = recovery?.can_complete_truncated_response ? recovery.truncated_response_recovery : null;
  const failedRecovery = recovery?.can_retry_failed_response ? recovery.failed_response_recovery : null;
  const responseRecovery = failedRecovery || reportRecovery;
  const priceRefresh = researchMode && recovery?.price_refresh_required === true && recovery.can_continue_with_price_refresh === true;
  const tail = recovery?.remaining_work;
  const cost = detail.cost as (Record<string, unknown> | null | undefined);
  if (!id || !recovery || ['accepted', 'running'].includes(detail.run.technical_status || detail.run.status || '')) return null;
  const canContinue = researchMode && Boolean(recovery.can_continue || responseRecovery || priceRefresh)
    && (!recovery.price_refresh_required || priceRefresh);
  const selected = ask?.runId === id && ask.requestId === reportRecovery?.request_id
    && ask.failedRequestId === failedRecovery?.request_id
    && ask.priceRefresh === priceRefresh && canContinue;
  const money = (key: string) => typeof cost?.[key] === 'string' || typeof cost?.[key] === 'number'
    ? String(cost[key]) : t('tradeidea.unknown');
  const act = async (kind: 'continue' | 'files') => {
    if (lock.current || kind === 'continue' && (!selected || !ask)) return;
    lock.current = true; setBusy(true); setAsk(null); setError(null);
    try {
      if (kind === 'continue') {
        if (!ask) return;
        const selection = ask.runId + ':' + (ask.failedRequestId ? 'failed:' + ask.failedRequestId : ask.requestId || 'stages') + ':' + (ask.priceRefresh ? 'prices' : 'original');
        const key = keys.current[selection] ||= crypto.randomUUID();
        const result = await TradeIdeas.resume(ask.runId, key, ask.requestId, ask.priceRefresh, ask.failedRequestId);
        onChanged(result.run_id);
      } else {
        await TradeIdeas.recoverDelivery(id); onChanged(id);
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { lock.current = false; setBusy(false); }
  };
  return <section className="ti-slab" aria-label={t('tradeidea.recoveryTitle')}>
    <h3>{t(researchMode ? 'tradeidea.recoveryTitle' : 'tradeidea.recoveryArchived')}</h3>
    {!researchMode && <p className="ti-muted">{t('tradeidea.recoveryArchivedHint')}</p>}
    {detail.states && <p>{Object.entries(detail.states).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>}
    {detail.primary_failure && <p role="alert">{[detail.primary_failure.phase, detail.primary_failure.desk,
      detail.primary_failure.message || detail.primary_failure.error, detail.primary_failure.request_id].filter(Boolean).join(' · ')}</p>}
    <p>{t('tradeidea.recoveryKnown')}: {money('charged_usd')} · {t('tradeidea.recoveryReserved')}: {money('unknown_reserved_usd')} · {t('tradeidea.recoveryRemaining')}: {money('remaining_after_holds_usd')}</p>
    <p>{t('tradeidea.recoveryCap')}: {money('budget_limit_usd')}</p>
    <p className="ti-muted">{t('tradeidea.recoveryOriginalBudget')}</p>
    {priceRefresh && <p>{t('tradeidea.recoveryPriceRefresh')}: {t('tradeidea.recoveryPriceRefreshIntro')}</p>}
    {recovery.blocked_reason && <p>{t('tradeidea.recoveryBlocked')}: {recovery.blocked_reason}</p>}
    {!recovery.checkpoint_available && <p>{t('tradeidea.recoveryNoCheckpoint')}</p>}
    {tail?.remaining && <p>{tail.remaining.join(' · ')}</p>}
    {tail?.feasibility && <p>{t('tradeidea.recoveryFeasibility')}: {tail.feasibility}{tail.reason ? ' · ' + tail.reason : ''}</p>}
    {tail?.largest_output_reservation_floor_usd != null && <p>{t('tradeidea.recoveryOutputFloor')}: {tail.largest_output_reservation_floor_usd}</p>}
    {detail.artifact_availability?.reason && <p role="alert">{detail.artifact_availability.reason}</p>}
    <div className="ti-detail-actions">
      {recovery.successor_run_id ? <button className="ti-button" onClick={() => onChanged(recovery.successor_run_id!)}>{t('tradeidea.recoveryOpenSuccessor')}</button>
        : canContinue && <button className="ti-button ti-primary" disabled={busy} onClick={() => setAsk({runId: id, requestId: reportRecovery?.request_id, failedRequestId: failedRecovery?.request_id, priceRefresh})}>{t(failedRecovery ? 'tradeidea.recoveryFailedResearch' : reportRecovery ? 'tradeidea.recoveryCompleteReport' : 'tradeidea.recoveryContinue')}</button>}
      {recovery.can_recover_delivery && <button className="ti-button" disabled={busy} onClick={() => void act('files')}>{busy ? t('tradeidea.recoveryBusy') : t(researchMode ? 'tradeidea.recoveryResearchFiles' : 'tradeidea.recoveryArchivedFiles')}</button>}
    </div>
    <p className="ti-muted">{t('tradeidea.recoveryFilesHint')}</p>
    {error && <p role="alert">{error}</p>}
    <ConfirmDialog open={Boolean(ask && selected)} title={t('tradeidea.recoveryConfirm')}
      intro={t(failedRecovery ? 'tradeidea.recoveryFailedResearchIntro' : reportRecovery ? 'tradeidea.recoveryCompleteReportIntro' : 'tradeidea.recoveryConfirmIntro') + (priceRefresh ? ' ' + t('tradeidea.recoveryPriceRefreshIntro') : '')}
      warn={t('tradeidea.recoveryOriginalBudget')} rows={[{ k: t('tradeidea.runId'), v: ask?.runId || id }, ...(responseRecovery ? [{ k: `${responseRecovery.desk} R${responseRecovery.round_n}`, v: responseRecovery.request_id }] : []), ...(priceRefresh ? [{ k: t('tradeidea.recoveryCap'), v: money('budget_limit_usd') }] : [])]}
      confirmLabel={t('tradeidea.recoveryConfirm')} onConfirm={() => void act('continue')} onCancel={() => setAsk(null)} />
  </section>;
}
