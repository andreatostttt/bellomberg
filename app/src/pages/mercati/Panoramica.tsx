import { AlertTriangle, ArrowLeftRight, Building2, Droplet, Globe, Percent, RefreshCw, Star, TrendingUp } from 'lucide-react';
import type { FavCompany, MktMoverRow, MktMovers, MktOverview, MktOverviewRow } from '@/lib/api';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import type { Parole } from './parole';
import type { PrezzoPreferito } from './dati';
import {
  type Area, CardMercati, INDICI_CHIAVE, ORDINE_PAESI, RigaTitolo, areaDi, finito, gruppoMp, numero, prezzo, sigla, variazione,
} from './comuni';

export type Ambito = 'az' | 'all';

interface Props {
  w: Parole;
  ricerca: React.ReactNode;
  overview: MktOverview | null; ovErr: string | null; ovLoading: boolean;
  movers: MktMoverRow[] | null; movErr: string | null;
  moversInfo: Pick<MktMovers, 'paesi' | 'fonte' | 'motivo'> | null;
  preferiti: FavCompany[] | null; prefErr: string | null; prezziPref: Record<string, PrezzoPreferito>;
  auto: boolean; setAuto: (on: boolean) => void; aggiorna: () => void; aggiornato: Date | null;
  paese: string; setPaese: (cc: string) => void;
  area: Area; setArea: (a: Area) => void;
  ambito: Ambito; setAmbito: (a: Ambito) => void;
  onApri: (ticker: string) => void;
  onGestisciPreferiti: () => void;
}

/** Righe per colonna quando la card è larga e l'elenco va su due colonne (dall'alto in basso). */
const colonne = (n?: number) => ({ '--righe': Math.max(1, Math.ceil((n || 0) / 2)) } as React.CSSProperties);

const ora = (d: Date) => d.toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export default function Panoramica(p: Props) {
  const { w, overview: ov, ovErr, ovLoading, onApri } = p;
  const vuoto = ovLoading && !ov ? w.emptyLoading : ovErr ? w.emptyError : w.empty;
  const elenco = (righe: MktOverviewRow[] | undefined, conSigla: boolean, unita = '') => (
    righe && righe.length
      ? righe.map(r => <RigaTitolo key={r.ticker} riga={r} sigla={conSigla ? sigla(r) : undefined} unita={unita} onApri={onApri} />)
      : <p className="mk-empty">{vuoto}</p>
  );
  const paesi = [...ORDINE_PAESI.filter(cc => !ov?.countries || ov.countries.includes(cc)),
    ...(ov?.countries || []).filter(cc => !ORDINE_PAESI.includes(cc))];
  const indici = ov?.indici || [];

  return (
    <>
      <div className="mk-top">
        <h1>{w.title}</h1>
        <span className="bbn-grow" />
        {p.ricerca}
        <span className="bbn-grow" />
        <span className="bbn-chip mk-updated">
          <i className={'mk-dot' + (p.auto ? ' is-live' : '')} aria-hidden="true" />
          {p.aggiornato ? w.updatedAt(ora(p.aggiornato)) : w.notUpdated}
        </span>
        <button type="button" className="bbn-switch" role="switch" aria-checked={p.auto} title={w.autoHint} onClick={() => p.setAuto(!p.auto)}>
          <i aria-hidden="true" />{w.auto}
        </button>
        <button type="button" className="bbn-btn" onClick={p.aggiorna} disabled={ovLoading}>
          <RefreshCw size={15} className={ovLoading ? 'mk-spin' : undefined} aria-hidden="true" />{ovLoading ? w.refreshing : w.refresh}
        </button>
      </div>

      {ovErr && !ovLoading && (
        <div className="mk-note is-bad" role="alert">
          <AlertTriangle size={16} aria-hidden="true" />
          <span className="mk-note-txt">{w.overviewError(ovErr)}</span>
          <button type="button" className="bbn-link" onClick={p.aggiorna}>{w.retry}</button>
        </div>
      )}

      <div className="mk-tiles">
        {INDICI_CHIAVE.map(t => {
          const r = indici.find(x => x.ticker === t);
          return (
            <button key={t} type="button" className="mk-tile" data-ticker={t} onClick={() => onApri(t)}>
              <span className="mk-tile-k">{r?.name ?? t}</span>
              <span className="mk-tile-reg">{sigla(r ?? { ticker: t, name: t })}</span>
              <span className="mk-tile-v num">{finito(r?.price) ? numero(r!.price) : '—'}</span>
              <PastigliaVariazione valore={r?.change_pct} lampo={r?.price ?? null}><span className="num">{variazione(r?.change_pct)}</span></PastigliaVariazione>
            </button>
          );
        })}
      </div>

      <div className="mk-favbar is-fav">
        <span className="mk-favbar-lab"><span className="mk-ci" aria-hidden="true"><Star size={16} /></span>{w.favorites}</span>
        <div className="mk-favs">
          {p.prefErr && p.preferiti == null && <span className="mk-inline-err">{w.favoritesError(p.prefErr)}</span>}
          {p.preferiti && p.preferiti.length > 0 && p.preferiti.map(f => {
            const pr = p.prezziPref[f.ticker];
            return (
              <button key={f.ticker} type="button" className="mk-fav" data-ticker={f.ticker} onClick={() => onApri(f.ticker)} title={f.name || f.ticker}>
                <IconaTitolo ticker={f.ticker} nome={f.name} dimensione="xs" />
                <b>{f.name || f.ticker}</b>
                <span className="mk-fav-px num">{prezzo(pr?.price)}</span>
                <PastigliaVariazione valore={pr?.change} lampo={pr?.price ?? null}><span className="num">{variazione(pr?.change)}</span></PastigliaVariazione>
              </button>
            );
          })}
          {(p.preferiti ? p.preferiti.length === 0 : !!p.prefErr) && <>
            {p.preferiti && <span className="mk-favs-hint">{w.favoritesSuggested}</span>}
            {/* nessun elenco fisso: le azioni più grandi del paese scelto, come le manda il provider */}
            {(ov?.azioni || []).slice(0, 8).map(r => (
              <button key={r.ticker} type="button" className="mk-fav is-suggested" data-ticker={r.ticker} onClick={() => onApri(r.ticker)}>
                <IconaTitolo ticker={r.ticker} nome={r.name} dimensione="xs" /><b>{r.ticker}</b>
              </button>
            ))}
          </>}
        </div>
        <button type="button" className="bbn-link" onClick={p.onGestisciPreferiti}>{w.favoritesManage}</button>
      </div>

      <div className="mk-row3">
        <CardMercati tinta="idx" icona={<Globe size={18} />} titolo={w.indices}
          azioni={<Segmenti etichetta={w.regions} valore={p.area} onChange={p.setArea} className="mk-seg-aree"
            opzioni={(['am', 'eu', 'as'] as const).map(a => ({ id: a, testo: w.regionNames[a] }))} />}>
          {/* tutte le aree sono nel DOM: sugli schermi bassi si vede solo quella scelta,
              su quelli alti tutte e tre raggruppate (il CSS nasconde il selettore) */}
          <div className="mk-body is-gruppi is-aree">
            {indici.length ? (['am', 'eu', 'as'] as const).map(a => {
              const righe = indici.filter(r => areaDi(r.ticker) === a);
              return (
                <div key={a} className={'mk-area mk-gblock' + (a === p.area ? ' is-on' : '')} style={colonne(righe.length)}>
                  <div className="mk-grp mk-area-lab">{w.regionNames[a]}</div>
                  <div className="mk-righe">{righe.map(r => <RigaTitolo key={r.ticker} riga={r} sigla={sigla(r)} onApri={onApri} />)}</div>
                </div>
              );
            }) : elenco(undefined, true)}
          </div>
        </CardMercati>

        <CardMercati tinta="stk" icona={<Building2 size={18} />} titolo={w.equities}
          azioni={<Segmenti etichetta={w.equitiesByCountry} valore={p.paese} onChange={p.setPaese} className="mk-seg-paesi"
            opzioni={paesi.map(cc => ({ id: cc, testo: cc, title: w.countryNames[cc] || cc }))} />}>
          <div className="mk-body is-lista" style={colonne(ov?.azioni?.length)}>
            {!ov?.azioni?.length && ov?.azioni_fonte && ov.azioni_fonte.stato !== 'ok'
              ? <p className="mk-empty is-declared" role="status" data-stato="azioni-non-disponibili">{w.equitiesOff(ov.azioni_fonte.motivo)}</p>
              : elenco(ov?.azioni, false)}
          </div>
        </CardMercati>

        <CardMercati tinta="mov" icona={<TrendingUp size={18} />} titolo={w.movers}
          azioni={<Segmenti etichetta={w.moversScope} valore={p.ambito} onChange={p.setAmbito}
            opzioni={[{ id: 'az', testo: w.moversStocks, title: w.moversStocksHint }, { id: 'all', testo: w.moversAll, title: w.moversAllHint }]} />}>
          <div className="mk-body"><PiuMossi {...p} /></div>
        </CardMercati>
      </div>

      <div className="mk-row3">
        <CardMercati tinta="cmd" icona={<Droplet size={18} />} titolo={w.commodities}>
          <div className="mk-body is-gruppi">
            {ov?.commodities?.length ? (['met', 'ene', 'agr'] as const).map(g => {
              const righe = ov.commodities.filter(r => gruppoMp(r.ticker) === g);
              return righe.length ? (
                <div key={g} className="mk-gblock">
                  <div className="mk-grp">{w.commodityGroups[g]}</div>
                  {elenco(righe, true)}
                </div>
              ) : null;
            }) : elenco(undefined, true)}
          </div>
        </CardMercati>
        <CardMercati tinta="fx" icona={<ArrowLeftRight size={18} />} titolo={w.currencies}>
          <div className="mk-body is-lista" style={colonne(ov?.valute?.length)}>{elenco(ov?.valute, true)}</div>
        </CardMercati>
        <CardMercati tinta="rt" icona={<Percent size={18} />} titolo={w.ratesFutures}>
          <div className="mk-body is-gruppi">
            <div className="mk-gblock">
              <div className="mk-grp">{w.rates} <span>· {w.yieldNote}</span></div>
              {elenco(ov?.obbligazioni, true, '%')}
            </div>
            <div className="mk-gblock">
              <div className="mk-grp">{w.futures}</div>
              {elenco(ov?.futures, true)}
            </div>
          </div>
        </CardMercati>
      </div>
    </>
  );
}

function PiuMossi(p: Props) {
  const { w, movers, movErr, moversInfo, ambito, overview: ov, onApri } = p;
  if (movErr && !movers) return <p className="mk-empty">{w.moversError(movErr)}</p>;
  // stato dichiarato dal backend: paesi senza dati (con il motivo) e, se non c'è nulla, il motivo generale
  const paesiOff = Object.entries(moversInfo?.paesi || {}).filter(([, s]) => s && s.stato !== 'ok');
  const dichiarati = paesiOff.length > 0 && (
    <p className="mk-empty is-declared" role="status" data-stato="paesi-non-disponibili">
      {paesiOff.map(([cc, s]) => w.moversCountryOff(w.countryNames[cc] || cc, s.motivo)).join(' · ')}
    </p>
  );
  let insieme: Array<{ ticker: string; name: string; change_pct?: number | null; tipo: string }> =
    (movers || []).map(r => ({ ...r, tipo: w.countryNames[r.country] || r.country }));
  if (ambito === 'all' && ov) {
    insieme = insieme.concat(
      ov.indici.map(r => ({ ...r, tipo: w.kindIndex })),
      ov.commodities.map(r => ({ ...r, tipo: w.kindCommodity })),
      ov.valute.map(r => ({ ...r, tipo: w.kindFx })),
      ov.futures.map(r => ({ ...r, tipo: w.kindFuture })),
    );
  }
  const validi = insieme.filter(r => finito(r.change_pct));
  if (!validi.length) return <>
    <p className="mk-empty" role={moversInfo?.motivo ? 'status' : undefined} data-stato={moversInfo?.motivo ? 'classifica-non-disponibile' : undefined}>
      {movers == null ? w.emptyLoading : moversInfo?.motivo ? w.moversDeclared(moversInfo.motivo) : w.moversEmpty}</p>
    {dichiarati}
  </>;
  const ordinati = [...validi].sort((a, b) => b.change_pct! - a.change_pct!);
  const su = ordinati.filter(r => r.change_pct! > 0).slice(0, 8);
  const giu = ordinati.filter(r => r.change_pct! < 0).reverse().slice(0, 8);
  const max = Math.max(...validi.map(r => Math.abs(r.change_pct!)), 0.01);
  const colonna = (titolo: string, righe: typeof validi, tono: 'su' | 'giu') => (
    <div className="mk-mov-col">
      <h3 className="mk-mov-h"><span className={tono === 'su' ? 'mk-up' : 'mk-dn'} aria-hidden="true">{tono === 'su' ? '▲' : '▼'}</span> {titolo}</h3>
      {righe.length === 0 ? <p className="mk-empty">—</p> : righe.map(r => (
        <button key={r.ticker} type="button" className="mk-mv" data-ticker={r.ticker} onClick={() => onApri(r.ticker)} title={r.ticker}>
          <span className="mk-mv-name"><b>{r.name}</b><span>{r.tipo}</span></span>
          <PastigliaVariazione valore={r.change_pct}><span className="num">{variazione(r.change_pct)}</span></PastigliaVariazione>
          <span className="mk-mv-bar" aria-hidden="true"><i className={'is-' + tono} style={{ width: `${(Math.abs(r.change_pct!) / max) * 100}%` }} /></span>
        </button>
      ))}
    </div>
  );
  return <>
    <div className="mk-movers">{colonna(w.gainers, su, 'su')}{colonna(w.losers, giu, 'giu')}</div>
    {dichiarati}
    {moversInfo?.fonte && <p className="mk-mov-src">{w.moversSource(moversInfo.fonte)}</p>}
  </>;
}
