import { useEffect, useRef, useState } from 'react';
import { useT } from '@/i18n/provider';
import { Bellomberg, type WeeklyRecovery } from '@/lib/api';
import ConfirmDialog from '@/components/ConfirmDialog';

export default function WeeklyRecoveryPanel({ disabled, onStarted }: {
  disabled: boolean; onStarted: (taskId: string) => void;
}) {
  const t = useT(), lock = useRef(false);
  const [open, setOpen] = useState(false), [runs, setRuns] = useState<WeeklyRecovery[]>([]);
  const [selected, setSelected] = useState(''), [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const refresh = async () => {
      try { const result = await Bellomberg.weeklyRecoveries(); if (alive) { setRuns(result.runs); setError(null); } }
      catch (e: any) { if (alive) setError(String(e?.response?.data?.detail || e?.message || e)); }
    };
    void refresh(); const timer = setInterval(refresh, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [open]);
  const run = runs.find(row => String(row.memo_id) === selected);
  const act = async (files: boolean) => {
    if (!run || lock.current || disabled) return;
    lock.current = true; setBusy(true); setAsk(false); setError(null);
    try {
      const result = await Bellomberg.recoverConsigliere(run.memo_id, files);
      setAccepted(true); onStarted(result.task_id);
    } catch (e: any) { setError(String(e?.response?.data?.detail || e?.message || e)); }
    finally { lock.current = false; setBusy(false); }
  };
  const money = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? String(value) : t('tradeidea.unknown');
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="warn">
    <summary>{t('tradeidea.recoveryWeekly')}</summary>
    {open && <div>
      <label>{t('tradeidea.recoverySelect')}{' '}
        <select value={selected} disabled={busy} onChange={event => { setSelected(event.target.value); setAccepted(false); }}>
          <option value="">—</option>
          {runs.map(row => <option key={row.memo_id} value={row.memo_id}>#{row.memo_id} · {row.status}</option>)}
        </select>
      </label>
      {runs.length === 0 && !error && <p>{t('tradeidea.recoveryEmpty')}</p>}
      {run && <>
        <p>{[run.status, run.analytical_status, run.artifact_status, run.delivery_status].filter(Boolean).join(' · ')}</p>
        {run.first_error && <p>{[run.first_error.phase, run.first_error.desk, run.first_error.message, run.first_error.request_id].filter(Boolean).join(' · ')}</p>}
        <p>{t('tradeidea.recoveryKnown')}: {money(run.request_costs?.known_cost_usd)} · {t('tradeidea.recoveryReserved')}: {money(run.request_costs?.reserved_usd)} · {t('tradeidea.recoveryRemaining')}: {money(run.request_costs?.remaining_known_usd)}</p>
        <p>{t('tradeidea.recoveryActualTotal')}: {money(run.request_costs?.cost_usd)} · {t('tradeidea.recoveryCap')}: {money(run.request_costs?.authorized_usd)}</p>
        {(run.blocked_reason || run.reason) && <p>{t('tradeidea.recoveryBlocked')}: {run.blocked_reason || run.reason}</p>}
        {run.remaining_work && <p>{run.remaining_work.join(' · ')}</p>}
        {run.resume_available && <button className="go" disabled={disabled || busy} onClick={() => setAsk(true)}>{t('tradeidea.recoveryContinue')}</button>}
        {run.delivery_recovery_available && <button className="go" disabled={disabled || busy} onClick={() => void act(true)}>{t('tradeidea.recoveryFiles')}</button>}
        <p>{t('tradeidea.recoveryFilesHint')}</p>
      </>}
      {accepted && <p role="status">{t('tradeidea.recoveryAccepted')}</p>}
      {error && <p role="alert">{error}</p>}
      <ConfirmDialog open={ask} title={t('tradeidea.recoveryConfirm')} intro={t('tradeidea.recoveryConfirmIntro')}
        rows={[{ k: t('tradeidea.recoverySelect'), v: selected }]} confirmLabel={t('tradeidea.recoveryConfirm')}
        onConfirm={() => void act(false)} onCancel={() => setAsk(false)} />
    </div>}
  </details>;
}
