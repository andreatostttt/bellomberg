import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useT } from '@/i18n/provider';
import { useInterfaceTheme } from './InterfaceThemeProvider';
import './page-dialog.css';

export type ConfirmRow = { k: string; v: string; tone?: 'amber' | 'crimson' | 'cyan' };

type Props = {
  /** Opt in only for modernized page dialogs; shell/reference dialogs retain their presentation. */
  pagePresentation?: boolean;
  open: boolean;
  title: string;
  intro?: string;
  rows?: ConfirmRow[];
  warn?: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: 'amber' | 'crimson';
  /** 'nuova': the consumer palette (--bbn-*) of the restyled pages. */
  variant?: 'nuova';
  /** Optional summary shown above the rows (e.g. the order amount). */
  lead?: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Dialog di conferma per le azioni COSTOSE o IRREVERSIBILI (Lotto D dell'audit,
 * approvato dal PM 26/07). NON esegue nulla e non lancia niente: chiede e basta —
 * chi lo apre decide cosa fare sull'onConfirm. Regole di casa:
 *  - il focus di default sta su ANNULLA: un INVIO di troppo (es. dalla palette,
 *    dove bastava un tasto per spendere ~10-15$) ANNULLA, non conferma;
 *  - ESC annulla e NON propaga, cosi' la palette dietro resta aperta;
 *  - focus trappolato dentro il dialog finche' e' aperto, e restituito a chi l'aveva
 *    alla chiusura.
 */
export default function ConfirmDialog({
  open, title, intro, rows, warn, confirmLabel, cancelLabel,
  tone = 'amber', onConfirm, onCancel, pagePresentation = false, variant, lead,
}: Props) {
  const t = useT();
  const modern = pagePresentation;
  const { effective: theme } = useInterfaceTheme();
  const boxRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const prevFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    prevFocus.current = document.activeElement as HTMLElement | null;
    const t = setTimeout(() => cancelRef.current?.focus(), 10);
    return () => {
      clearTimeout(t);
      // il focus torna a chi l'aveva (bottone RUN, input della palette, form F7)
      try { prevFocus.current?.focus(); } catch { /* elemento smontato: nulla da restituire */ }
    };
  }, [open]);

  // The Appearance menu stays reachable above page confirmations: after a theme
  // change, keyboard focus returns to the safe default without remounting the
  // pending dialog or changing its original return-focus target.
  const previousTheme = useRef(theme);
  useEffect(() => {
    const changed = previousTheme.current !== theme;
    previousTheme.current = theme;
    if (open && pagePresentation && changed) cancelRef.current?.focus({ preventScroll: true });
  }, [open, pagePresentation, theme]);

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel(); return; }
    if (e.key !== 'Tab') return;
    // trappola: TAB gira solo fra i pulsanti del dialog
    const f = boxRef.current?.querySelectorAll<HTMLElement>('button:not([disabled])');
    if (!f || f.length === 0) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  const dialog = (
    <>
      <div className={'cfm-overlay' + (pagePresentation ? ' bb-page-confirm-owner' : '') + (modern ? ' bb-page-confirm-overlay' : '') + (variant === 'nuova' ? ' bbn-confirm-overlay' : '')} onClick={onCancel} />
      <div ref={boxRef} className={'cfm-modal ' + (tone === 'crimson' ? 'is-crimson' : 'is-amber') + (pagePresentation ? ' bb-page-confirm-owner' : '') + (modern ? ' bb-page-confirm' : '') + (variant === 'nuova' ? ' bbn-confirm' : '')}
           role="alertdialog" aria-modal="true" aria-labelledby="cfm-title" onKeyDown={onKeyDown}>
        <div className="cfm-head" id="cfm-title">{title}</div>
        {intro && <div className="cfm-intro">{intro}</div>}
        {lead}
        {rows && rows.length > 0 && (
          <div className="cfm-rows">
            {rows.map(r => (
              <div className="cfm-row" key={r.k}>
                <span className="k">{r.k}</span>
                <span className={'v' + (r.tone ? ' ' + r.tone : '')}>{r.v}</span>
              </div>
            ))}
          </div>
        )}
        {warn && <div className="cfm-warn">{warn}</div>}
        {/* ANNULLA per primo nel DOM = primo nell'ordine di TAB e primo a prendere il focus */}
        <div className="cfm-actions">
          <button ref={cancelRef} className="cfm-btn no" onClick={onCancel}>{cancelLabel ?? t('shell.cancel')}</button>
          <button className="cfm-btn go" onClick={onConfirm}>{confirmLabel}</button>
        </div>
        <div className="cfm-hint">{t('shell.confirm_keys')}</div>
      </div>
    </>
  );

  // Keep SSR/test rendering valid when there is no real body; browsers portal
  // the fixed overlay out of Modern's query-container containing block.
  if (typeof document === 'undefined' || !document.body) return dialog;
  return createPortal(dialog, document.body);
}
