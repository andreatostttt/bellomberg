import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

export type Tono = 'su' | 'giu' | 'piatto';
export const tonoDi = (value: number | null | undefined): Tono =>
  value == null || !Number.isFinite(value) || value === 0 ? 'piatto' : value > 0 ? 'su' : 'giu';

/** Pastiglia di variazione: verde se sale, rossa se scende, neutra se ferma o n.d.
 *  `lampo` riaccende un breve bagliore ogni volta che il valore osservato cambia
 *  (il prezzo si è mosso); con prefers-reduced-motion il CSS lo spegne. */
export default function PastigliaVariazione({ valore, children, grande = false, lampo, title, className = '' }: {
  valore: number | null | undefined;
  children: ReactNode;
  grande?: boolean;
  /** valore da osservare per il lampo (es. il prezzo); se assente, nessun lampo */
  lampo?: number | null;
  title?: string;
  className?: string;
}) {
  const tono = tonoDi(valore);
  const precedente = useRef(lampo);
  const [flash, setFlash] = useState<'su' | 'giu' | null>(null);
  useEffect(() => {
    const prima = precedente.current;
    precedente.current = lampo;
    if (lampo == null || prima == null || lampo === prima || !Number.isFinite(lampo) || !Number.isFinite(prima)) return;
    setFlash(lampo > prima ? 'su' : 'giu');
    const timer = window.setTimeout(() => setFlash(null), 900);
    return () => window.clearTimeout(timer);
  }, [lampo]);
  return (
    <span className={`bbn-pill is-${tono}${grande ? ' is-large' : ''}${flash ? ` flash-${flash}` : ''}${className ? ' ' + className : ''}`}
      title={title} data-tone={tono}>
      {tono !== 'piatto' && <span className="bbn-pill-arrow" aria-hidden="true">{tono === 'su' ? '▲' : '▼'}</span>}
      {children}
    </span>
  );
}
