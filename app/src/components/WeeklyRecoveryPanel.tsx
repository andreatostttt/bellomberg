import { useEffect, useRef, useState } from 'react';
import { ChevronRight, CircleAlert, History } from 'lucide-react';
import { useT } from '@/i18n/provider';
import { Bellomberg, type WeeklyRecovery } from '@/lib/api';
import ConfirmDialog from '@/components/ConfirmDialog';

/** Recupero delle run settimanali (Trade Idea) dentro il box della run di Agenti in diretta:
 *  riquadro a scomparsa in stile Nuova (classi ag-recovery*, palette --bbn-*). */
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
  const costs = run ? [
    [t('tradeidea.recoveryKnown'), money(run.request_costs?.known_cost_usd)],
    [t('tradeidea.recoveryReserved'), money(run.request_costs?.reserved_usd)],
    [t('tradeidea.recoveryRemaining'), money(run.request_costs?.remaining_known_usd)],
    [t('tradeidea.recoveryActualTotal'), money(run.request_costs?.cost_usd)],
    [t('tradeidea.recoveryCap'), money(run.request_costs?.authorized_usd)],
  ] : [];
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="ag-recovery">
    <summary className="ag-recovery-head">
      <History size={15} aria-hidden="true" /><span>{t('tradeidea.recoveryWeekly')}</span>
      <ChevronRight size={15} aria-hidden="true" className="ag-recovery-chev" />
    </summary>
    {open && <div className="ag-recovery-body">
      <label className="ag-recovery-pick"><span>{t('tradeidea.recoverySelect')}</span>
        <select value={selected} disabled={busy} onChange={event => { setSelected(event.target.value); setAccepted(false); }}>
          <option value="">—</option>
          {runs.map(row => <option key={row.memo_id} value={row.memo_id}>#{row.memo_id} · {row.status}</option>)}
        </select>
      </label>
      {runs.length === 0 && !error && <p className="ag-recovery-note">{t('tradeidea.recoveryEmpty')}</p>}
      {run && <>
        <p className="ag-recovery-status">{[run.status, run.analytical_status, run.artifact_status, run.delivery_status].filter(Boolean).join(' · ')}</p>
        {run.first_error && <p className="ag-recovery-note is-bad">{[run.first_error.phase, run.first_error.desk, run.first_error.message, run.first_error.request_id].filter(Boolean).join(' · ')}</p>}
        <dl className="ag-recovery-costs">
          {costs.map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="num">{v}</dd></div>)}
        </dl>
        {(run.blocked_reason || run.reason) && <p className="ag-recovery-note is-warn">{t('tradeidea.recoveryBlocked')}: {run.blocked_reason || run.reason}</p>}
        {run.remaining_work && <p className="ag-recovery-note">{run.remaining_work.join(' · ')}</p>}
        <div className="ag-recovery-act">
          {run.resume_available && <button type="button" className="bbn-btn is-primary" disabled={disabled || busy} onClick={() => setAsk(true)}>{t('tradeidea.recoveryContinue')}</button>}
          {run.delivery_recovery_available && <button type="button" className="bbn-btn" disabled={disabled || busy} onClick={() => void act(true)}>{t('tradeidea.recoveryFiles')}</button>}
        </div>
        <p className="ag-recovery-note">{t('tradeidea.recoveryFilesHint')}</p>
      </>}
      {accepted && <p className="ag-recovery-note is-good" role="status">{t('tradeidea.recoveryAccepted')}</p>}
      {error && <p className="ag-recovery-note is-bad" role="alert"><CircleAlert size={15} aria-hidden="true" />{error}</p>}
      <ConfirmDialog open={ask} title={t('tradeidea.recoveryConfirm')} intro={t('tradeidea.recoveryConfirmIntro')}
        rows={[{ k: t('tradeidea.recoverySelect'), v: selected }]} confirmLabel={t('tradeidea.recoveryConfirm')}
        onConfirm={() => void act(false)} onCancel={() => setAsk(false)} />
    </div>}
  </details>;
}
