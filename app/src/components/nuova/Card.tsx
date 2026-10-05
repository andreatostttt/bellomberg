import { useId } from 'react';
import type { ReactNode } from 'react';

/** Card della palette consumer: titolo a sinistra, azioni a destra, nessun bordo pesante. */
export default function Card({ titolo, conteggio, azioni, className = '', children, ...rest }: {
  titolo: ReactNode;
  conteggio?: ReactNode;
  azioni?: ReactNode;
  className?: string;
  children: ReactNode;
} & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  const id = useId();
  return (
    <section className={'bbn-card' + (className ? ' ' + className : '')} aria-labelledby={id} {...rest}>
      <header className="bbn-card-head">
        <h2 id={id}>{titolo}</h2>
        {conteggio != null && <span className="bbn-card-count">{conteggio}</span>}
        <span className="bbn-grow" />
        {azioni}
      </header>
      {children}
    </section>
  );
}

/** Interruttore a segmenti (Oggi | Totale, periodi del grafico, ...). */
export function Segmenti<T extends string>({ valore, opzioni, onChange, etichetta, className = '' }: {
  valore: T;
  opzioni: ReadonlyArray<{ id: T; testo: ReactNode; title?: string }>;
  onChange: (id: T) => void;
  etichetta: string;
  className?: string;
}) {
  return (
    <div className={'bbn-seg' + (className ? ' ' + className : '')} role="group" aria-label={etichetta}>
      {opzioni.map(opzione => (
        <button key={opzione.id} type="button" aria-pressed={valore === opzione.id} title={opzione.title}
          className={valore === opzione.id ? 'is-on' : undefined} onClick={() => onChange(opzione.id)}>{opzione.testo}</button>
      ))}
    </div>
  );
}
