/** Prossimi eventi della dashboard: solo quelli importanti (importanza 4 o 5),
 *  una volta sola ciascuno, in ordine di data. La card ne mostra quanti ne entrano.
 *  Le date ricavate da una finestra abituale arrivano con `date_estimated` e
 *  restano dichiarate («data stimata»), senza un orario che non conosciamo. */
export interface EventoGrezzo {
  date: string;
  time?: string | null;
  title: string;
  importance: number;
  country?: string | null;
  date_estimated?: boolean;
  type?: string | null;
  ticker?: string | null;
}
export interface EventoImportante {
  data: string;
  ora: string | null;
  titolo: string;
  importanza: number;
  paese: string | null;
  stimata: boolean;
}

const eTrimestrale = (evento: EventoGrezzo) => evento.type === 'Earnings';

export function eventiImportanti(eventi: EventoGrezzo[], oggiISO: string, massimo = 8): EventoImportante[] {
  const visti = new Set<string>();
  return [...eventi]
    .filter(evento => evento && typeof evento.date === 'string' && typeof evento.title === 'string'
      && evento.date >= oggiISO && Number(evento.importance) >= 4 && !eTrimestrale(evento))
    .sort((a, b) => a.date.localeCompare(b.date) || String(a.time || '').localeCompare(String(b.time || '')))
    .filter(evento => {
      const chiave = evento.title.trim().toLowerCase();
      if (visti.has(chiave)) return false;
      visti.add(chiave);
      return true;
    })
    .slice(0, massimo)
    .map(evento => ({
      data: evento.date,
      ora: evento.date_estimated ? null : evento.time || null,
      titolo: evento.title.replace(/^(USA|US):\s*/i, '').replace(/^\p{Ll}/u, c => c.toUpperCase()),
      importanza: evento.importance,
      paese: evento.country || null,
      stimata: !!evento.date_estimated,
    }));
}

/** Trimestrale di un titolo in portafoglio: la prossima per ticker, in ordine di data. */
export interface Trimestrale {
  data: string;
  ticker: string;
  stimata: boolean;
  /** «Pre-market» / «After-close» dal provider; null se ignoto o con data stimata */
  ora: string | null;
}

export function trimestraliPortafoglio(eventi: EventoGrezzo[], oggiISO: string, massimo = 6): Trimestrale[] {
  const visti = new Set<string>();
  return [...eventi]
    .filter(evento => evento && eTrimestrale(evento) && typeof evento.date === 'string' && evento.date >= oggiISO
      && typeof evento.ticker === 'string' && evento.ticker.trim() !== '')
    .sort((a, b) => a.date.localeCompare(b.date))
    .filter(evento => {
      const chiave = evento.ticker!.trim().toUpperCase();
      if (visti.has(chiave)) return false;
      visti.add(chiave);
      return true;
    })
    .slice(0, massimo)
    .map(evento => ({ data: evento.date, ticker: evento.ticker!.trim().toUpperCase(), stimata: !!evento.date_estimated,
      ora: evento.date_estimated || !evento.time || /^tbd$/i.test(evento.time.trim()) ? null : evento.time.trim() }));
}
