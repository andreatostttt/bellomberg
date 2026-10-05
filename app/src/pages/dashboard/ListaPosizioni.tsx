import type { Position } from '@/lib/api';
import { dayEur, dayPct } from '@/lib/dailypl';
import { fmtEUR, fmtNum, fmtPct } from '@/lib/format';
import Card, { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import { parole } from './parole';

export type Metrica = 'oggi' | 'totale';
const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const tono = (value: number | null) => value == null ? 'is-flat' : value > 0 ? 'is-up' : value < 0 ? 'is-down' : 'is-flat';

export const nomeTitolo = (p: Position) => p.nome && p.nome !== p.ticker && p.nome.toLowerCase() !== 'none' ? p.nome : p.ticker.split('.')[0];

export default function ListaPosizioni({ posizioni, metrica, onMetrica, dettagliata, onDettagliata, onApri }: {
  posizioni: Position[];
  metrica: Metrica;
  onMetrica: (metrica: Metrica) => void;
  dettagliata: boolean;
  onDettagliata: (on: boolean) => void;
  onApri: (ticker: string) => void;
}) {
  const w = parole();
  const righe = [...posizioni].sort((a, b) => (b.valore_mercato || 0) - (a.valore_mercato || 0));
  return (
    <Card titolo={w.positions} conteggio={righe.length} className="bbn-positions" data-testid="dashboard-positions"
      azioni={<>
        <Segmenti etichetta={w.changeView} valore={metrica} onChange={onMetrica}
          opzioni={[{ id: 'oggi', testo: w.todayToggle }, { id: 'totale', testo: w.totalToggle }]} />
        <button type="button" role="switch" aria-checked={dettagliata} className="bbn-switch" onClick={() => onDettagliata(!dettagliata)}>
          <i aria-hidden="true" />{w.detailed}
        </button>
      </>}>
      {!righe.length ? <p className="bbn-empty">{w.noPositions}</p> : dettagliata ? (
        <div className="bbn-scroll bbn-table-wrap">
          <table className="bbn-table num">
            <thead><tr>
              <th>{w.colTitle}</th><th>{w.colQty}</th><th>{w.colPrice}</th><th>{w.colTodayPct}</th><th>{w.colTodayEur}</th>
              <th>{w.colValue}</th><th>{w.colPlEur}</th><th>{w.colPlPct}</th><th>{w.colWeight}</th>
            </tr></thead>
            <tbody>{righe.map(p => {
              const dp = dayPct(p), de = dayEur(p);
              return (
                <tr key={p.ticker} data-ticker={p.ticker}>
                  <td><button type="button" className="bbn-link" onClick={() => onApri(p.ticker)} aria-label={`${w.openDetail} ${nomeTitolo(p)}`}>{p.ticker}</button></td>
                  <td>{finito(p.quantita) ? fmtNum(p.quantita, p.quantita % 1 ? 4 : 0) : fmtNum(null)}</td>
                  <td className={p.price_stale ? 'is-stale' : undefined} title={p.price_stale ? w.stalePrice : undefined}>{finito(p.prezzo_live) ? fmtNum(p.prezzo_live, 2) : fmtNum(null)}</td>
                  <td className={tono(finito(dp) ? dp : null)}>{fmtPct(finito(dp) ? dp : null)}</td>
                  <td className={tono(finito(de) ? de : null)}>{fmtEUR(finito(de) ? de : null, true)}</td>
                  <td>{fmtEUR(finito(p.valore_mercato) ? p.valore_mercato : null)}</td>
                  <td className={tono(finito(p.pl_eur) ? p.pl_eur : null)}>{fmtEUR(finito(p.pl_eur) ? p.pl_eur : null, true)}</td>
                  <td className={tono(finito(p.pl_pct) ? p.pl_pct : null)}>{fmtPct(finito(p.pl_pct) ? p.pl_pct : null)}</td>
                  <td>{finito(p.peso_pct) ? fmtNum(p.peso_pct, 1) + '%' : fmtNum(null)}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      ) : (
        <ul className="bbn-scroll bbn-rows">{righe.map(p => {
          const variazione = metrica === 'oggi' ? dayPct(p) : p.pl_pct;
          const v = finito(variazione) ? variazione : null;
          const nome = nomeTitolo(p);
          return (
            <li key={p.ticker}>
              <button type="button" className="bbn-row" data-ticker={p.ticker} onClick={() => onApri(p.ticker)} aria-label={`${w.openDetail} ${nome}`}>
                <IconaTitolo ticker={p.ticker} nome={p.nome} />
                <span className="bbn-row-name">
                  <b>{nome}</b>
                  <span>{p.ticker}{p.price_stale && <em className="bbn-stale"> · {w.stalePrice}</em>}</span>
                </span>
                <span className="bbn-row-value">
                  <b className="num">{fmtEUR(finito(p.valore_mercato) ? p.valore_mercato : null)}</b>
                  <PastigliaVariazione valore={v} lampo={metrica === 'oggi' && finito(p.prezzo_live) ? p.prezzo_live : null}>
                    <span className="num">{fmtPct(v)}</span>
                  </PastigliaVariazione>
                </span>
              </button>
            </li>
          );
        })}</ul>
      )}
    </Card>
  );
}
