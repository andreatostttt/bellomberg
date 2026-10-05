import { useEffect, useMemo, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import type { NewsItem, Position } from '@/lib/api';
import { notiziePerTitoli } from '@/lib/notizie-titoli';
import { dettaglioLeggibile } from '@/lib/mandato';
import Card from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import { parole } from './parole';
import { nomeTitolo } from './ListaPosizioni';

function fa(iso: string | null, adesso: number) {
  const w = parole();
  if (!iso) return '';
  const t = Date.parse(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z');
  if (!Number.isFinite(t)) return '';
  const ore = Math.floor((adesso - t) / 3_600_000);
  if (ore < 1) return w.justNow;
  return ore < 24 ? `${ore} ${w.hoursAgo}` : `${Math.floor(ore / 24)} ${w.daysAgo}`;
}

/** Notizie sui titoli in portafoglio: una lettura all'apertura, nessun polling. */
export default function CardNotizie({ posizioni, onApri, onTutte }: {
  posizioni: Position[];
  onApri: (ticker: string) => void;
  onTutte: () => void;
}) {
  const w = parole();
  const [feed, setFeed] = useState<NewsItem[] | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [adesso] = useState(() => Date.now());
  useEffect(() => {
    let vivo = true;
    Bellomberg.newsFeed({ limit: 120, min_relevance: 6 })
      .then(r => { if (vivo) { setFeed(Array.isArray(r?.items) ? r.items : []); setErrore(null); } })
      .catch(error => { if (vivo) { setFeed(null); setErrore(dettaglioLeggibile(error)); } });
    return () => { vivo = false; };
  }, []);
  const perTicker = useMemo(() => new Map(posizioni.map(p => [p.ticker, p])), [posizioni]);
  const notizie = useMemo(() => feed ? notiziePerTitoli(feed, posizioni.map(p => ({ ticker: p.ticker, nome: p.nome })), 12, 3) : [], [feed, posizioni]);
  return (
    <Card titolo={w.news} className="bbn-news" data-testid="dashboard-news"
      azioni={<button type="button" className="bbn-link" onClick={onTutte}>{w.allNews}</button>}>
      {errore ? <p className="bbn-empty" role="status">{w.newsError} · {errore}</p>
        : feed === null ? <p className="bbn-empty" role="status" aria-busy="true">{w.newsLoading}</p>
        : !notizie.length ? <p className="bbn-empty">{w.newsEmpty}</p>
        : <ul className="bbn-scroll bbn-news-list">{notizie.map(n => {
          const p = perTicker.get(n.ticker);
          return (
            <li key={n.id} className="bbn-news-row">
              <button type="button" className="bbn-news-icon" onClick={() => onApri(n.ticker)} aria-label={`${w.openDetail} ${p ? nomeTitolo(p) : n.ticker}`}>
                <IconaTitolo ticker={n.ticker} nome={p?.nome} dimensione="sm" />
              </button>
              <div className="bbn-news-body">
                <b><span className={`bbn-mood is-${n.tono}`} role="img"
                  aria-label={n.tono === 'positiva' ? w.tonePositive : n.tono === 'negativa' ? w.toneNegative : w.toneNeutral} />{n.titolo}</b>
                {n.motivo && <p>{n.motivo}</p>}
                <p className="bbn-news-meta">{p ? nomeTitolo(p) : n.ticker}{n.fonte ? ` · ${n.fonte}` : ''}</p>
              </div>
              <time dateTime={n.quando || undefined}>{fa(n.quando, adesso)}</time>
            </li>
          );
        })}</ul>}
    </Card>
  );
}
