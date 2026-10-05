import { useEffect, useRef, useState } from 'react';
import { Loader2, Search } from 'lucide-react';
import { Bellomberg, type MktSearchHit } from '@/lib/api';
import { leggiDetail } from '@/lib/quota';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import type { Parole } from './parole';

const erroreFonte = (e: any) => leggiDetail(e?.response?.data?.detail) || leggiDetail(e?.message) || '—';

/** Ricerca globale dei titoli (Yahoo, via /market/search) con 300 ms di attesa dopo
 *  l'ultimo tasto. Frecce per scorrere, Invio apre, Esc chiude. Il testo cercato resta
 *  anche quando la ricerca fallisce o non trova nulla. */
export default function RicercaTitoli({ w, segnaposto, onApri, autoFocus = false, iniziale = '', suggeriti = [] }: {
  w: Parole;
  /** Proposte a campo vuoto, da dati di runtime (le azioni più grandi del paese scelto): nessun elenco fisso. */
  suggeriti?: Array<{ ticker: string; name?: string }>;
  segnaposto: string;
  onApri: (ticker: string) => void;
  autoFocus?: boolean;
  iniziale?: string;
}) {
  const [q, setQ] = useState(iniziale);
  const [hits, setHits] = useState<MktSearchHit[]>([]);
  const [aperto, setAperto] = useState(false);
  const [cerco, setCerco] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const [attivo, setAttivo] = useState(0);
  const [cercato, setCercato] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!q.trim()) { setHits([]); setErrore(null); setCerco(false); setCercato(false); return; }
    let vivo = true;
    setCerco(true); setErrore(null);
    const t = setTimeout(() => {
      Bellomberg.mktSearch(q.trim())
        .then(r => { if (vivo) { setHits(r.results || []); setAttivo(0); setAperto(true); setCercato(true); } })
        .catch(e => { if (vivo) { setHits([]); setErrore(erroreFonte(e)); setCercato(false); } })
        .finally(() => { if (vivo) setCerco(false); });
    }, 300);
    return () => { vivo = false; clearTimeout(t); };
  }, [q]);

  useEffect(() => { if (autoFocus) inputRef.current?.focus(); }, [autoFocus]);

  // un clic fuori chiude il menu
  useEffect(() => {
    if (!aperto || typeof document.addEventListener !== 'function') return;
    const fuori = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setAperto(false); };
    document.addEventListener('mousedown', fuori);
    return () => document.removeEventListener('mousedown', fuori);
  }, [aperto]);

  const vuoto = !q.trim();
  const voci: Array<{ symbol: string; name: string; exchange: string; type: string }> = vuoto
    ? suggeriti.map(s => ({ symbol: s.ticker, name: s.name || '', exchange: '', type: '' }))
    : hits;
  const apri = (sym: string) => { setQ(''); setHits([]); setAperto(false); setCercato(false); onApri(sym.toUpperCase()); };

  return (
    <div className="mk-search" ref={boxRef}>
      <Search size={16} className="mk-search-ico" aria-hidden="true" />
      <input
        ref={inputRef}
        value={q}
        aria-label={w.searchLabel}
        placeholder={segnaposto}
        autoComplete="off"
        role="combobox"
        aria-expanded={aperto}
        // il fuoco automatico all'apertura della pagina non apre il menu: lo apre un clic, un tasto o ↓
        onMouseDown={() => setAperto(true)}
        onChange={e => { setQ(e.target.value); setAperto(true); }}
        onKeyDown={e => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setAperto(true); setAttivo(i => Math.min(i + 1, Math.max(voci.length - 1, 0))); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setAttivo(i => Math.max(i - 1, 0)); }
          else if (e.key === 'Enter' && !vuoto && voci[attivo]) apri(voci[attivo].symbol);
          else if (e.key === 'Escape') { setAperto(false); setQ(''); }
        }}
      />
      {cerco ? <Loader2 size={15} className="mk-search-busy" aria-label="…" /> : <kbd>Ctrl K</kbd>}
      {errore !== null && <div role="alert" className="mk-search-err">{w.searchFailed(errore)}</div>}
      {errore === null && !cerco && !vuoto && cercato && hits.length === 0 && <div role="status" className="mk-search-err is-none">{w.searchNone}</div>}
      {aperto && errore === null && !cerco && ((vuoto && voci.length > 0) || (cercato && hits.length > 0)) && (
        <div className="mk-menu" role="listbox">
          <h6>{vuoto ? w.searchRecent : w.searchResults(q.trim())}</h6>
          {voci.map((h, i) => (
            <button key={h.symbol + h.exchange} type="button" role="option" aria-selected={i === attivo}
              className={'mk-hit' + (i === attivo ? ' is-act' : '')} data-ticker={h.symbol}
              onMouseEnter={() => setAttivo(i)} onMouseDown={e => e.preventDefault()} onClick={() => apri(h.symbol)}>
              <IconaTitolo ticker={h.symbol} nome={h.name} dimensione="xs" />
              <span className="mk-hit-name"><b>{h.symbol}</b>{h.name && <span>{h.name}{h.type ? ' · ' + h.type : ''}</span>}</span>
              {h.exchange && <span className="mk-hit-ex">{h.exchange}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
