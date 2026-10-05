import { useEffect, useState } from 'react';
import { Lightbulb } from 'lucide-react';
import { t as tr } from '@/i18n/t';
import { useT } from '@/i18n/provider';
import ModernPage from '@/components/ModernPage';
import { parole } from './mercati/parole';
import { useCruscotto, useScheda } from './mercati/dati';
import RicercaTitoli from './mercati/RicercaTitoli';
import Panoramica, { type Ambito } from './mercati/Panoramica';
import SchedaTitolo, { type SchedaAz, type SchedaFin, type SchedaLato } from './mercati/SchedaTitolo';
import type { Area } from './mercati/comuni';
import './mercati-nuova.css';

/** Mercati globali (palette Nuova): panoramica in una schermata (indici chiave, preferiti,
 *  sei card per categoria) e scheda del titolo, con ritorno alla panoramica.
 *  Gli stati restano tutti qui, chiamati sempre nello stesso ordine: le viste sono
 *  solo presentazione. */
export default function MarketPage() {
  useT();
  const w = parole();
  const [tk, setTk] = useState('');
  const [paese, setPaese] = useState('US');
  const [query, setQuery] = useState('');
  const dati = useCruscotto(!tk, paese);
  const scheda = useScheda(tk, dati.preferiti, dati.prefErr, dati.setPreferiti);
  const [area, setArea] = useState<Area>('am');
  const [ambito, setAmbito] = useState<Ambito>('az');
  const [tabFin, setTabFin] = useState<SchedaFin>('income');
  const [tabLato, setTabLato] = useState<SchedaLato>('news');
  const [tabAz, setTabAz] = useState<SchedaAz>('own');

  const apri = (t: string) => { setTk(t.toUpperCase()); setTabFin('income'); setTabLato('news'); setTabAz('own'); };

  // passaggio da altre pagine (Ctrl+K, Preferiti, Centro di comando): ticker diretto o testo da cercare
  useEffect(() => {
    try {
      const t = sessionStorage.getItem('bb:mktTicker');
      const qq = sessionStorage.getItem('bb:mktQuery');
      if (t) { sessionStorage.removeItem('bb:mktTicker'); apri(t); }
      else if (qq) { sessionStorage.removeItem('bb:mktQuery'); setQuery(qq); }
    } catch { /* sessionStorage non disponibile */ }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // la scheda si apre in cima
  useEffect(() => {
    if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return;
    document.querySelector('.bb-modern-main')?.scrollTo?.({ top: 0 });
  }, [tk]);

  const ricerca = (
    <RicercaTitoli key={query} w={w} iniziale={query} segnaposto={tk ? w.searchOther : w.searchHint} onApri={apri} autoFocus={!tk}
      suggeriti={(dati.overview?.azioni || []).slice(0, 8)} />
  );
  // scheda del titolo: accanto alla ricerca, l'analisi Trade Idea del titolo aperto
  const ricercaScheda = (
    <>
      {ricerca}
      <button type="button" className="bbn-btn is-sm mk-trade-idea" data-trade-idea={tk}
        onClick={() => { window.location.hash = `/agents/trade-idea?ticker=${encodeURIComponent(tk)}&source=market`; }}>
        <Lightbulb size={15} aria-hidden="true" />{tr('tradeidea.analyzeStock')}
      </button>
    </>
  );

  return (
    <ModernPage page="market" presentationBoundary={false} render={() => (
      <div className="bbn-mercati market-page bbn-font" data-view={tk ? 'detail' : 'overview'}>
        {tk
          ? <SchedaTitolo w={w} tk={tk} ricerca={ricercaScheda} onIndietro={() => setTk('')} {...scheda}
              tabFin={tabFin} setTabFin={setTabFin} tabLato={tabLato} setTabLato={setTabLato} tabAz={tabAz} setTabAz={setTabAz} />
          : <Panoramica w={w} ricerca={ricerca} {...dati} paese={paese} setPaese={setPaese} area={area} setArea={setArea}
              ambito={ambito} setAmbito={setAmbito} onApri={apri}
              onGestisciPreferiti={() => { window.location.hash = '#/watchlist'; }} />}
      </div>
    )} />
  );
}
