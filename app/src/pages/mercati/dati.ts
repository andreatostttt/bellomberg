import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bellomberg, type FavCompany, type MktFinancials, type MktHolders, type MktMoverRow, type MktMovers,
  type MktNewsItem, type MktOverview, type MktQuote,
} from '@/lib/api';
import { leggiDetail } from '@/lib/quota';
import { caricaLoghi } from '@/lib/loghi-remoti';

export const erroreFonte = (e: any) => leggiDetail(e?.response?.data?.detail) || leggiDetail(e?.message) || '—';

const CHIAVE_AUTO = 'bb:mercati:auto';
const OGNI_MS = 120_000;

function leggiAuto(): boolean {
  try { return localStorage.getItem(CHIAVE_AUTO) !== 'false'; } catch { return true; }
}

export interface PrezzoPreferito { price: number | null; change: number | null }

/** Dati della panoramica: cruscotto (/market/overview), classifica dei più mossi
 *  (/market/movers), preferiti con i loro prezzi. Si ricaricano ogni 2 minuti se
 *  l'aggiornamento automatico è acceso e la pagina è visibile. Le letture partono
 *  solo mentre la panoramica è in vista (`attivo`). */
export function useCruscotto(attivo: boolean, paese: string) {
  const [overview, setOverview] = useState<MktOverview | null>(null);
  const [ovErr, setOvErr] = useState<string | null>(null);
  const [ovLoading, setOvLoading] = useState(false);
  const [movers, setMovers] = useState<MktMoverRow[] | null>(null);
  const [moversInfo, setMoversInfo] = useState<Pick<MktMovers, 'paesi' | 'fonte' | 'motivo'> | null>(null);
  const [movErr, setMovErr] = useState<string | null>(null);
  const [preferiti, setPreferiti] = useState<FavCompany[] | null>(null);
  const [prefErr, setPrefErr] = useState<string | null>(null);
  const [prezziPref, setPrezziPref] = useState<Record<string, PrezzoPreferito>>({});
  const [auto, setAutoState] = useState<boolean>(leggiAuto);
  const [giro, setGiro] = useState(0);
  const [aggiornato, setAggiornato] = useState<Date | null>(null);

  // cruscotto: al cambio paese, a ogni giro
  useEffect(() => {
    if (!attivo) return;
    let vivo = true;
    setOvLoading(true); setOvErr(null);
    Bellomberg.mktOverview(paese)
      .then(d => { if (vivo) { setOverview(d); setAggiornato(new Date()); } })
      .catch(e => { if (vivo) { setOverview(null); setOvErr(erroreFonte(e)); } })
      .finally(() => { if (vivo) setOvLoading(false); });
    return () => { vivo = false; };
  }, [attivo, paese, giro]);

  // più mossi: a ogni giro (non dipende dal paese)
  useEffect(() => {
    if (!attivo) return;
    let vivo = true;
    setMovErr(null);
    Bellomberg.mktMovers()
      .then(d => { if (vivo) { setMovers(d.azioni || []); setMoversInfo({ paesi: d.paesi, fonte: d.fonte, motivo: d.motivo ?? null }); } })
      // giro fallito: la classifica VECCHIA non resta a schermo come se fosse attuale (regola 14/07)
      .catch(e => { if (vivo) { setMovers(null); setMoversInfo(null); setMovErr(erroreFonte(e)); } });
    return () => { vivo = false; };
  }, [attivo, giro]);

  // preferiti: una lettura all'avvio e a ogni giro; servono anche alla scheda del titolo
  useEffect(() => {
    let vivo = true;
    Bellomberg.favorites()
      .then(r => { if (vivo) { setPreferiti(r.favorites || []); setPrefErr(null); } })
      .catch(e => { if (vivo) { setPreferiti(null); setPrefErr(erroreFonte(e)); } });
    return () => { vivo = false; };
  }, [giro]);

  const tickerPref = (preferiti || []).map(f => f.ticker).join(',');
  useEffect(() => {
    if (!attivo || !tickerPref) return;
    let vivo = true;
    const lista = tickerPref.split(',').slice(0, 16);
    Promise.allSettled(lista.map(t => Bellomberg.mktQuote(t))).then(esiti => {
      if (!vivo) return;
      const out: Record<string, PrezzoPreferito> = {};
      esiti.forEach((esito, i) => {
        const q = esito.status === 'fulfilled' ? esito.value : null;
        const p = q?.price ?? null, pc = q?.prev_close ?? null;
        out[lista[i]] = { price: p, change: p != null && pc ? ((p / pc) - 1) * 100 : null };
      });
      setPrezziPref(out);
    });
    return () => { vivo = false; };
  }, [attivo, tickerPref, giro]);

  // loghi delle azioni in vista
  const tickerLoghi = [...(overview?.azioni || []), ...(movers || [])].map(r => r.ticker).concat(tickerPref ? tickerPref.split(',') : []).join(',');
  useEffect(() => { if (attivo && tickerLoghi) caricaLoghi(tickerLoghi.split(',')); }, [attivo, tickerLoghi]);

  // aggiornamento automatico: solo con la pagina visibile
  useEffect(() => {
    if (!attivo || !auto) return;
    const id = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      setGiro(g => g + 1);
    }, OGNI_MS);
    // fuori dal browser (test SSR) il timer non deve tenere vivo il processo
    (id as unknown as { unref?: () => void })?.unref?.();
    return () => clearInterval(id);
  }, [attivo, auto]);

  const setAuto = useCallback((on: boolean) => {
    setAutoState(on);
    try { localStorage.setItem(CHIAVE_AUTO, on ? 'true' : 'false'); } catch { /* preferenza locale */ }
  }, []);
  const aggiorna = useCallback(() => setGiro(g => g + 1), []);

  return { overview, ovErr, ovLoading, movers, moversInfo, movErr, preferiti, setPreferiti, prefErr, prezziPref, auto, setAuto, aggiorna, aggiornato };
}

/** Dati della scheda di un titolo: quotazione, notizie, bilanci, azionariato,
 *  preferito (letto dall'elenco già caricato: nessuna lettura in più). */
export function useScheda(tk: string, preferiti: FavCompany[] | null, prefErr: string | null,
  setPreferiti: (f: FavCompany[] | null) => void) {
  const [quote, setQuote] = useState<MktQuote | null>(null);
  const [quoteErr, setQuoteErr] = useState<string | null>(null);
  const [news, setNews] = useState<MktNewsItem[] | null>(null);
  const [newsErr, setNewsErr] = useState<string | null>(null);
  // vie della ricerca notizie cadute o alias illeggibile: dichiarati dal backend, resi in pagina
  const [newsAvvisi, setNewsAvvisi] = useState<string[] | null>(null);
  const [fin, setFin] = useState<MktFinancials | null>(null);
  const [finErr, setFinErr] = useState<string | null>(null);
  const [holders, setHolders] = useState<MktHolders | null>(null);
  const [holdersErr, setHoldersErr] = useState<string | null>(null);
  const [favWriteErr, setFavWriteErr] = useState<string | null>(null);
  const [favOttimista, setFavOttimista] = useState<boolean | null>(null);
  const scrivo = useRef(false);
  // traduzione dei titoli delle notizie: SOLO dal pulsante (costa una chiamata AI)
  const [traduzione, setTraduzione] = useState<{ titoli: string[]; costo: number | null; cached: boolean; completo: boolean } | null>(null);
  const [traduco, setTraduco] = useState(false);
  const [tradErr, setTradErr] = useState<string | null>(null);
  const [mostraOriginale, setMostraOriginale] = useState(false);

  useEffect(() => {
    if (!tk) return;
    let vivo = true;
    setQuote(null); setNews(null); setQuoteErr(null); setNewsErr(null); setNewsAvvisi(null); setFavWriteErr(null); setFavOttimista(null);
    // degrado dichiarato: quotazione scheletro + errore reso, mai statistiche mute
    Bellomberg.mktQuote(tk).then(r => { if (vivo) setQuote(r); })
      .catch(e => { if (vivo) { setQuote({ ticker: tk, name: tk }); setQuoteErr(erroreFonte(e)); } });
    Bellomberg.mktNews(tk).then(r => { if (vivo) { setNews(r.items || []); setNewsAvvisi(r.errori?.length ? r.errori : null); } })
      .catch(e => { if (vivo) { setNews([]); setNewsErr(erroreFonte(e)); } });
    return () => { vivo = false; };
  }, [tk]);

  useEffect(() => {
    if (!tk) return;
    let vivo = true;
    setFin(null); setFinErr(null); setHolders(null); setHoldersErr(null);
    Bellomberg.mktFinancials(tk).then(r => { if (vivo) setFin(r); })
      .catch(e => { if (vivo) { setFinErr(erroreFonte(e)); setFin({ ticker: tk }); } });
    Bellomberg.mktHolders(tk).then(r => { if (vivo) setHolders(r); })
      .catch(e => { if (vivo) { setHolders({ ticker: tk, major: [], institutional: [] }); setHoldersErr(erroreFonte(e)); } });
    return () => { vivo = false; };
  }, [tk]);

  useEffect(() => { setTraduzione(null); setTradErr(null); setTraduco(false); setMostraOriginale(false); }, [tk]);
  const traduci = async () => {
    if (!news?.length || traduco) return;
    if (traduzione?.completo) { setMostraOriginale(false); return; }
    setTraduco(true); setTradErr(null);
    try {
      const r = await Bellomberg.mktNewsTranslate(news.map(n => n.title));
      if (r.status === 'done' && r.titles?.length === news.length) { setTraduzione({ titoli: r.titles, costo: r.cost_eur ?? null, cached: !!r.cached, completo: r.complete !== false }); setMostraOriginale(false); }
      else setTradErr(r.status === 'not_configured' ? `not_configured:${r.variable || 'NEWS_SUMMARY_MODEL'}` : (r.detail || '—'));
    } catch (e) { setTradErr(erroreFonte(e)); }
    finally { setTraduco(false); }
  };

  // stato preferito: sconosciuto (null) se l'elenco non è leggibile
  const fav: boolean | null = favOttimista ?? (preferiti == null ? null : preferiti.some(f => f.ticker === tk));
  const favReadErr = preferiti == null ? prefErr : null;

  const toggleFav = async () => {
    if (fav == null || !tk || scrivo.current) return;
    const prima = fav;
    scrivo.current = true;
    setFavOttimista(!prima); setFavWriteErr(null);
    try {
      if (prima) {
        await Bellomberg.favDel(tk);
        if (preferiti) setPreferiti(preferiti.filter(f => f.ticker !== tk));
      } else {
        const nuovo = { ticker: tk, name: quote?.name || '', sector: quote?.sector || '', industry: quote?.industry || '' };
        await Bellomberg.favAdd(nuovo);
        if (preferiti) setPreferiti([...preferiti, nuovo]);
      }
      setFavOttimista(null);
    } catch (e) {
      setFavOttimista(prima); setFavWriteErr(erroreFonte(e));
    } finally { scrivo.current = false; }
  };

  return { quote, quoteErr, news, newsErr, newsAvvisi, fin, finErr, holders, holdersErr, fav, favReadErr, favWriteErr, toggleFav,
    traduzione, traduco, tradErr, traduci, mostraOriginale, setMostraOriginale };
}
