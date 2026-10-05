import type { Position } from '@/lib/api';

/* P&L DAILY dai campi backend (contratto (28) 23/07): prev_close in VALUTA DI
   QUOTAZIONE, fx_to_eur gia' con GBX dentro. Campo assente (backend vecchio)
   o null = n.d. DICHIARATO — mai 0. Formula UNICA condivisa F1/F2. */

export const dayPct = (p: Position) => (p.prezzo_live != null && p.prev_close != null && p.prev_close > 0)
  ? (p.prezzo_live / p.prev_close - 1) * 100 : null;

export const dayEur = (p: Position) => (p.prezzo_live != null && p.prev_close != null && p.fx_to_eur != null)
  ? (p.prezzo_live - p.prev_close) * (p.quantita || 0) * p.fx_to_eur : null;

/* P&L GG dell'INTERO portafoglio (chiarimento PM 23/07): la % SOLO a copertura
   completa, sul valore investito di ieri — mai una % calcolata su un pezzo di
   book. dayTot con posizioni scoperte = somma PARZIALE, flag dichiarato.
   FINESTRA (17/09): `prev_close` e' l'ultimo snapshot del giorno precedente
   CON DATI — se l'updater salta un giorno di borsa, il confronto copre piu'
   sedute e non deve chiamarsi "GG". `asOfISO` e' la data del live (snapshot
   piu' recente, non l'orologio: a notte fonda la data locale e' gia' domani).
   Senza `asOfISO` nessuna rivendicazione sulla finestra. I NUMERI restano
   quelli di prima: la finestra cambia solo l'etichetta, mai il totale. */
export function computeDailyPnl(positions: Position[], asOfISO?: string | null): {
  dayTot: number | null; dayPartial: boolean; dayTotPct: number | null;
  multiDay: boolean; windowLabel: string | null;
} {
  const vals = positions.map(dayEur).filter((v): v is number => v != null);
  const dayTot = vals.length ? vals.reduce((s, v) => s + v, 0) : null;
  const dayPartial = vals.length > 0 && vals.length < positions.length;
  const prevInvested = positions.reduce((s, p) => {
    const v = (p.prev_close != null && p.fx_to_eur != null) ? p.prev_close * (p.quantita || 0) * p.fx_to_eur : null;
    return v != null ? s + v : s;
  }, 0);
  const dayTotPct = (dayTot != null && !dayPartial && prevInvested > 0) ? (dayTot / prevInvested) * 100 : null;
  // --- finestra: inizio = prima data con prev_close fra le posizioni coperte,
  // fine = data del live. Multi-day se in (inizio, fine] cade piu' di un giorno
  // feriale lun-ven (il weekend ven->lun resta UNA seduta). Festivi infrasettimanali
  // senza dati scattano conservativi: la finestra si dichiara, non si nasconde.
  const dataDi = (iso: string | null | undefined): string | null => {
    const d = typeof iso === 'string' ? iso.slice(0, 10) : '';
    return /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
  };
  let multiDay = false;
  let windowLabel: string | null = null;
  const fine = dataDi(asOfISO);
  if (fine != null) {
    let inizio: string | null = null;
    for (const p of positions) {
      if (p.prev_close == null) continue;
      const d = dataDi(p.prev_close_ts ?? null);
      if (d != null && (inizio == null || d < inizio)) inizio = d;
    }
    if (inizio != null && inizio < fine) {
      let sedute = 0;
      const t = Date.parse(inizio + 'T00:00:00Z');
      const f = Date.parse(fine + 'T00:00:00Z');
      if (isFinite(t) && isFinite(f)) {
        for (let g = t + 86400000, n = 0; g <= f && n < 370; g += 86400000, n++) {
          const dow = new Date(g).getUTCDay();
          if (dow >= 1 && dow <= 5) sedute++;
        }
      }
      if (sedute > 1) {
        multiDay = true;
        const fmt = (d: string) => d.slice(8, 10) + '/' + d.slice(5, 7);
        windowLabel = fmt(inizio) + '→' + fmt(fine);
      }
    }
  }
  return { dayTot, dayPartial, dayTotPct, multiDay, windowLabel };
}

/* Fine finestra per computeDailyPnl: lo snapshot prezzi piu' recente, non
   l'orologio (a notte fonda la data locale e' gia' domani mentre i prezzi
   sono di ieri). `as_of` a runtime porta `price_snapshots_newest`; il tipo
   in api.ts lo dichiara stringa, quindi lettura difensiva per forma. */
export function liveAsOf(snap: { timestamp?: string | null; as_of?: unknown } | null | undefined): string | null {
  const a = snap?.as_of as { price_snapshots_newest?: unknown } | string | null | undefined;
  const newest = typeof a === 'object' && a !== null ? a.price_snapshots_newest : null;
  if (typeof newest === 'string' && newest.length >= 10) return newest;
  if (typeof a === 'string' && a.length >= 10) return a;
  return snap?.timestamp ?? null;
}
