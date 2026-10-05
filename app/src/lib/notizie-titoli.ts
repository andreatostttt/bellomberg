/** Notizie della dashboard: solo quelle che parlano DAVVERO di un titolo in portafoglio.
 *
 *  Il feed porta `ticker_mentioned`, ma a volte punta al titolo sbagliato (misurato
 *  il 02/10/2026: notizie su altre società marcate col ticker di una posizione). Qui il
 *  titolo si riconosce dall'INTESTAZIONE: la prima parola del nome societario o la
 *  radice del ticker, come parola intera; una prima parola condivisa da più titoli
 *  (es. «Bank of …») non basta. Il motivo «perché ti riguarda» si mostra
 *  solo quando il backend ha marcato lo stesso titolo, altrimenti parlerebbe d'altro. */
export interface TitoloNoto { ticker: string; nome?: string | null }
export interface NotiziaGrezza {
  id?: number | string;
  title: string;
  snippet?: string | null;
  source?: string | null;
  url?: string | null;
  published_at?: string | null;
  ticker_mentioned?: string | null;
  sentiment?: string | null;
}
export interface NotiziaTitolo {
  id: string;
  ticker: string;
  titolo: string;
  /** il «perché ti riguarda» del backend, solo se riferito a questo titolo */
  motivo: string | null;
  fonte: string | null;
  url: string | null;
  quando: string | null;
  tono: 'positiva' | 'negativa' | 'neutra';
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function chiaviTitolo(titolo: TitoloNoto): string[] {
  const radice = titolo.ticker.split('.')[0];
  const nome = (titolo.nome || '').trim();
  const prima = nome && nome !== titolo.ticker && nome.toLowerCase() !== 'none' ? nome.split(/\s+/)[0].replace(/[^\p{L}\p{N}&-]/gu, '') : '';
  return [...new Set([prima, radice].filter(chiave => chiave.length >= 3))];
}

export function notiziePerTitoli(notizie: NotiziaGrezza[], titoli: TitoloNoto[], massimo = 12, perTitolo = 3): NotiziaTitolo[] {
  const chiavi = titoli.map(titolo => ({ ticker: titolo.ticker, chiavi: chiaviTitolo(titolo) }));
  const uso = new Map<string, number>();
  for (const { chiavi: lista } of chiavi) for (const chiave of lista) uso.set(chiave.toLowerCase(), (uso.get(chiave.toLowerCase()) || 0) + 1);
  const regole = chiavi.map(({ ticker, chiavi: lista }) => ({
    ticker,
    regex: lista.filter(chiave => uso.get(chiave.toLowerCase()) === 1)
      .map(chiave => new RegExp(`(^|[^\\p{L}\\p{N}])${escape(chiave)}($|[^\\p{L}\\p{N}])`, 'iu')),
  })).filter(regola => regola.regex.length);
  const ordinate = [...notizie].sort((a, b) => String(b.published_at || '').localeCompare(String(a.published_at || '')));
  const conteggio = new Map<string, number>();
  const visti = new Set<string>();
  const out: NotiziaTitolo[] = [];
  for (const notizia of ordinate) {
    const titolo = (notizia.title || '').trim();
    if (!titolo || visti.has(titolo.toLowerCase())) continue;
    const regola = regole.find(r => r.regex.some(re => re.test(titolo)));
    if (!regola) continue;
    if ((conteggio.get(regola.ticker) || 0) >= perTitolo) continue;
    conteggio.set(regola.ticker, (conteggio.get(regola.ticker) || 0) + 1);
    visti.add(titolo.toLowerCase());
    const sentiment = String(notizia.sentiment || '').toLowerCase();
    out.push({
      id: String(notizia.id ?? `${regola.ticker}-${out.length}`),
      ticker: regola.ticker,
      titolo,
      motivo: notizia.ticker_mentioned === regola.ticker && notizia.snippet ? notizia.snippet : null,
      fonte: notizia.source || null,
      url: notizia.url || null,
      quando: notizia.published_at || null,
      tono: sentiment === 'bullish' ? 'positiva' : sentiment === 'bearish' ? 'negativa' : 'neutra',
    });
    if (out.length >= massimo) break;
  }
  return out;
}
