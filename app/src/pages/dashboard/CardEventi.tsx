import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import type { EconomicEvent, Position } from '@/lib/api';
import { eventiImportanti, trimestraliPortafoglio } from '@/lib/eventi-importanti';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import Card, { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import { nomeTitolo } from './ListaPosizioni';
import { parole } from './parole';

const MINIMO = 3;
const MASSIMO = 8;
/** Le trimestrali guardano più avanti dei dati macro: escono di rado. */
const GIORNI_MACRO = 30;
const GIORNI_TRIMESTRALI = 60;
type Vista = 'calendario' | 'trimestrali';
const CHIAVE_VISTA = 'bb.dashboard.events';

function vistaSalvata(): Vista {
  try { return localStorage.getItem(CHIAVE_VISTA) === 'trimestrali' ? 'trimestrali' : 'calendario'; } catch { return 'calendario'; }
}

/** Prossimi eventi, con un selettore: il calendario economico (eventi importanti a
 *  30 giorni) o le trimestrali dei titoli in portafoglio (60 giorni). Ogni vista usa
 *  tutto lo spazio della card e ne mostra le righe che entrano intere, mai meno di tre. */
export default function CardEventi({ oggiISO, posizioni }: { oggiISO: string; posizioni: Position[] }) {
  const w = parole();
  const [eventi, setEventi] = useState<EconomicEvent[] | null | 'errore'>(null);
  const [vista, setVistaStato] = useState<Vista>(vistaSalvata);
  const setVista = (v: Vista) => {
    setVistaStato(v);
    try { localStorage.setItem(CHIAVE_VISTA, v); } catch { /* preferenza solo per questa sessione */ }
  };
  useEffect(() => {
    let vivo = true;
    Bellomberg.economicCalendar(GIORNI_MACRO, GIORNI_TRIMESTRALI)
      .then(r => { if (vivo) setEventi(Array.isArray(r?.items) ? r.items : []); })
      .catch(() => { if (vivo) setEventi('errore'); });
    return () => { vivo = false; };
  }, []);
  const macro = useMemo(() => Array.isArray(eventi) ? eventiImportanti(eventi, oggiISO, MASSIMO) : [], [eventi, oggiISO]);
  const perTicker = useMemo(() => new Map(posizioni.map(p => [p.ticker.toUpperCase(), p])), [posizioni]);
  // solo i titoli ancora in portafoglio: il calendario può essere più vecchio della posizione
  const trimestrali = useMemo(() => Array.isArray(eventi)
    ? trimestraliPortafoglio(eventi, oggiISO, MASSIMO).filter(t => perTicker.has(t.ticker)) : [], [eventi, oggiISO, perTicker]);
  const righe = vista === 'calendario' ? macro.length : trimestrali.length;

  const boxRef = useRef<HTMLUListElement>(null);
  const [visibili, setVisibili] = useState(MASSIMO);
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === 'undefined') return;
    // posizioni rispetto alla LISTA: offsetTop e' relativo al contenitore di scorrimento
    // della pagina, e con quello ogni riga risultava fuori (la card restava a 3 righe)
    const misura = () => {
      const fondo = box.getBoundingClientRect().top + box.clientHeight;
      const entrano = ([...box.children] as HTMLElement[]).filter(riga => riga.getBoundingClientRect().bottom <= fondo + 1).length;
      setVisibili(Math.max(MINIMO, entrano));
    };
    // misura con tutte le righe presenti, poi nasconde quelle che non entrano intere
    const ro = new ResizeObserver(() => { setVisibili(MASSIMO); requestAnimationFrame(misura); });
    ro.observe(box);
    return () => ro.disconnect();
  }, [righe, vista]);

  const locale = localeDi(linguaCorrente());
  const casella = (iso: string) => {
    const d = new Date(iso + 'T12:00:00');
    return (
      <span className="bbn-event-day"><b className="num">{d.getDate()}</b>
        <span>{iso === oggiISO ? w.todayShort : d.toLocaleDateString(locale, { month: 'short' })}</span></span>
    );
  };

  let corpo: React.ReactNode;
  if (eventi === 'errore') corpo = <p className="bbn-empty" role="status">{w.eventsError}</p>;
  else if (eventi === null) corpo = <p className="bbn-empty" aria-busy="true">…</p>;
  else if (vista === 'calendario') {
    corpo = !macro.length ? <p className="bbn-empty">{w.eventsEmpty}</p>
      : <ul className="bbn-event-list" ref={boxRef}>{macro.map((e, i) => (
        <li key={`${e.data}-${e.titolo}`} className="bbn-event" hidden={i >= visibili}>
          {casella(e.data)}
          <span className="bbn-event-body"><b>{e.titolo}</b>
            <small>{[e.paese, e.stimata ? null : e.ora].filter(Boolean).join(' · ')}
              {e.stimata && <>{e.paese ? ' · ' : ''}<em title={w.estimatedHint}>{w.estimatedDate}</em></>}</small></span>
        </li>
      ))}</ul>;
  } else {
    corpo = !trimestrali.length ? <p className="bbn-empty">{w.earningsEmpty}</p>
      : <ul className="bbn-event-list" ref={boxRef}>{trimestrali.map((t, i) => {
        const p = perTicker.get(t.ticker)!;
        return (
          <li key={t.ticker} className="bbn-event bbn-earning" data-ticker={t.ticker} hidden={i >= visibili}>
            {casella(t.data)}
            <span className="bbn-earning-body">
              <IconaTitolo ticker={p.ticker} nome={p.nome} dimensione="sm" />
              <span className="bbn-event-body"><b>{nomeTitolo(p)}</b>
                <small>{[w.earningsRow, t.ora].filter(Boolean).join(' · ')}
                  {t.stimata && <> · <em title={w.estimatedHint}>{w.estimatedDate}</em></>}</small></span>
            </span>
          </li>
        );
      })}</ul>;
  }

  return (
    <Card titolo={w.events} className="bbn-events" data-testid="dashboard-events"
      azioni={<Segmenti etichetta={w.eventsView} valore={vista} onChange={setVista}
        opzioni={[{ id: 'calendario', testo: w.calendar, title: w.eventsScope },
          { id: 'trimestrali', testo: <>{w.earnings}{trimestrali.length > 0 && <span className="bbn-seg-count num">{trimestrali.length}</span>}</>, title: w.earningsScope }]} />}>
      {corpo}
    </Card>
  );
}
