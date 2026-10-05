/** The ordered destinations shared by the menu, shortcuts, routes and manual. */
import { linguaCorrente, type Lingua } from '../i18n/lingua';
import { tDinamica } from '../i18n/t';
export type Destination = {
  id: string; to: string; short: string; label: string; group: string;
  key: string; kind: 'page' | 'settings';
};
// Sesto campo opzionale: tasto fisso. Filing (fase E, 03/10/2026) sta in Ricerca ma prende F20,
// cosi' i tasti delle pagine gia' esistenti non cambiano.
const destinations: ReadonlyArray<readonly [string, string, string, string, string, string?]> = [
  ['dashboard', '/dashboard', 'DASH', 'Command Center', 'Portafoglio'],
  ['performance', '/performance', 'PERF', 'Performance', 'Portafoglio'],
  ['watchlist', '/watchlist', 'FAVS', 'Watchlist', 'Portafoglio'],
  ['market', '/market', 'MKT', 'Global Markets', 'Ricerca'],
  ['news', '/news', 'NEWS', 'News Desk', 'Ricerca'],
  ['fundamentals', '/fundamentals', 'FUND', 'Fundamentals', 'Ricerca'],
  ['filing', '/filing', 'FILE', 'Filings', 'Ricerca', 'F20'],
  ['factors', '/factors', 'FCTR', 'Factor Lab', 'Rischio'],
  ['montecarlo', '/backtest', 'MTC', 'Monte Carlo', 'Rischio'],
  ['vol', '/vol', 'VOLS', 'Vol Deck', 'Rischio'],
  ['edge', '/edge', 'EDGE', 'Edge Scanner', 'Rischio'],
  ['chat', '/chat', 'CHAT', 'Agent Chat', 'Comitato'],
  ['agents', '/agents', 'LIVE', 'Agents Live', 'Comitato'],
  ['progress', '/agent-progress', 'SCORE', 'Progressi agenti', 'Comitato'],
  ['memos', '/memos', 'MEMO', 'Memo Archive', 'Comitato'],
  ['decisions', '/decisions', 'DECN', 'Decisioni', 'Comitato'],
  ['trades', '/trades', 'TRADE', 'Trade Entry', 'Operazioni'],
  ['movements', '/movements', 'MOVES', 'Movimenti', 'Operazioni'],
  ['mandato', '/mandato', 'MNDT', 'Mandato e Diario', 'Mandato'],
  ['settings', '/settings', 'CONFIG', 'Impostazioni', 'Sistema'],
];
let progressivo = 0;
export const NAVIGATION: readonly Destination[] = destinations.map(([id, to, short, label, group, fisso]) => ({
  id, to, short, label, group, key: fisso ?? `F${++progressivo}`, kind: id === 'settings' ? 'settings' : 'page',
}));
export const PAGE_DESTINATIONS = NAVIGATION.filter(entry => entry.kind === 'page');
export const SETTINGS_DESTINATION = NAVIGATION.find(entry => entry.kind === 'settings')!;
const GROUP_KEYS: Record<string, string> = {
  Portafoglio: 'portfolio', Ricerca: 'research', Rischio: 'risk', Comitato: 'committee',
  Operazioni: 'operations', Mandato: 'mandate', Sistema: 'system',
};
export function localizeDestination(entry: Destination, language: Lingua = linguaCorrente()): Destination {
  return { ...entry, label: tDinamica(language, `nav.${entry.id}`),
    group: tDinamica(language, `nav.${GROUP_KEYS[entry.group]}_group`) };
}
export function pageKey(path: string): string {
  const entry = NAVIGATION.find(item => item.to === path);
  if (!entry) throw new Error(`Destinazione non registrata: ${path}`);
  return entry.key;
}
