/* Contatore della voce Decisioni nel menu (05/10/2026): decisioni ancora da prendere
   (stato PENDING, la stessa lettura del pulsante «Vedi decisioni» della Dashboard).
   Si rilegge ogni 2 minuti e quando si cambia pagina. Una lettura fallita o una risposta
   senza l'elenco NON spegne il contatore (sarebbe uguale a «nessuna decisione», fallback
   muto vietato dalla regola 14/07): mostra N.D. dichiarato. Review PR #14 (Opus 5.5).
   Componente a parte come BadgeFiling. */
import { useEffect, useState } from 'react';
import { Bellomberg } from '@/lib/api';

const OGNI_MS = 2 * 60 * 1000;

type Lettura = { stato: 'attesa' } | { stato: 'ok'; n: number } | { stato: 'ignota' };

export default function BadgeDecisioni({ percorso, etichetta, ignota, nd }: {
  percorso: string; etichetta: (n: number) => string; ignota: string; nd: string;
}) {
  const [lettura, setLettura] = useState<Lettura>({ stato: 'attesa' });
  useEffect(() => {
    let vivo = true;
    const leggi = () => {
      Promise.resolve().then(() => Bellomberg.decisions('PENDING', -1))
        .then(r => { if (vivo) setLettura(Array.isArray(r?.decisions) ? { stato: 'ok', n: r.decisions.length } : { stato: 'ignota' }); })
        .catch(() => { if (vivo) setLettura({ stato: 'ignota' }); });
    };
    leggi();
    const id = setInterval(leggi, OGNI_MS);
    return () => { vivo = false; clearInterval(id); };
  }, [percorso]);
  if (lettura.stato === 'ignota') {
    return <b className="bb-nav-count is-unknown" data-decisions-badge="nd" title={ignota} aria-label={ignota}>{nd}</b>;
  }
  return lettura.stato === 'ok' && lettura.n > 0
    ? <b className="bb-nav-count" data-decisions-badge={lettura.n} aria-label={etichetta(lettura.n)}>{lettura.n}</b> : null;
}
