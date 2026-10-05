/* Logica pura della pagina Filing (fase E, 03/10/2026): gruppi, striscia del Comitato,
   rimedi agli errori e presentazione dei cambiamenti. Nessun dato viene alterato: i filtri
   agiscono sulla vista (le citazioni restano quelle del confronto). */
import type { FilingCitation, FilingGruppoUi, FilingProposal, FilingOverview, FilingOverviewTitolo, FilingStatoUi } from '@/lib/api';

export const GRUPPI = ['novita', 'da_sistemare', 'aggiornati', 'senza_fonte'] as const satisfies readonly FilingGruppoUi[];
export type Ordine = 'novita' | 'az';

/** Stato e gruppo del titolo; un backend precedente alla fase E ricade sui campi storici. */
export function statoDi(t: FilingOverviewTitolo): FilingStatoUi {
  if (t.stato) return t.stato;
  if (t.escluso) return 'escluso';
  if (!t.profilo) return 'senza_fonte';
  if (t.novita) return 'novita';
  if (!t.ultimo_confronto) return 'primo_confronto';
  return t.gruppo === 2 ? 'invariato' : 'aggiornato';
}
export function gruppoDi(t: FilingOverviewTitolo): FilingGruppoUi {
  if (t.gruppo_ui) return t.gruppo_ui;
  const s = statoDi(t);
  return s === 'novita' ? 'novita' : s === 'senza_fonte' || s === 'escluso' || s === 'non_attivo' ? 'senza_fonte'
    : s === 'errore' || s === 'da_confermare' || s === 'proposta_ai' ? 'da_sistemare' : 'aggiornati';
}

const PRIORITA: Partial<Record<FilingStatoUi, number>> = {
  errore: 0, da_confermare: 1, proposta_ai: 2, in_corso: 0, aggiornato: 1, primo_confronto: 2, invariato: 3,
  senza_fonte: 0, non_attivo: 1, escluso: 2,
};
const etichetta = (t: FilingOverviewTitolo) => (t.nome || t.ticker).toLocaleLowerCase();
const tempo = (v?: string | null) => { const n = v ? Date.parse(v) : NaN; return Number.isFinite(n) ? n : 0; };

export function raggruppa(titoli: FilingOverviewTitolo[], ordine: Ordine) {
  const confronta = (a: FilingOverviewTitolo, b: FilingOverviewTitolo) => {
    if (ordine === 'az') return etichetta(a).localeCompare(etichetta(b)) || a.ticker.localeCompare(b.ticker);
    return (PRIORITA[statoDi(a)] ?? 9) - (PRIORITA[statoDi(b)] ?? 9)
      || (b.cambiamenti ?? 0) - (a.cambiamenti ?? 0)
      || tempo(b.ultimo_confronto) - tempo(a.ultimo_confronto)
      || a.ticker.localeCompare(b.ticker);
  };
  return GRUPPI.map(gruppo => ({ gruppo, titoli: titoli.filter(t => gruppoDi(t) === gruppo).sort(confronta) }))
    .filter(g => g.titoli.length > 0);
}

/** Conteggi della striscia «Contesto per il Comitato» e quote (%) della barra. */
export function striscia(o: FilingOverview) {
  const c = o.copertura;
  const daConfermare = o.titoli.filter(t => statoDi(t) === 'da_confermare').length;
  const esclusi = c.esclusi;
  const senzaFonte = o.titoli.filter(t => gruppoDi(t) === 'senza_fonte' && statoDi(t) !== 'escluso').length;
  const daAggiornare = c.non_aggiornati + (c.senza_confronto ?? 0);
  const totale = c.totale;
  const q = (n: number) => totale > 0 ? Math.round(n / totale * 1000) / 10 : 0;
  return { aggiornati: c.aggiornati, daAggiornare, daConfermare, senzaFonte, esclusi, totale,
    quote: { aggiornati: q(c.aggiornati), daAggiornare: q(daAggiornare), daConfermare: q(daConfermare), senzaFonte: q(senzaFonte) } };
}

export type Rimedio = 'profilo' | 'riprova' | 'ocr' | 'riprova_profilo';
/** Cosa può fare l'utente davanti all'errore di un controllo. */
export function rimedio(reason?: string | null): Rimedio {
  const r = String(reason || '').toLowerCase();
  if (/ocr|senza testo|without text|no text/.test(r)) return 'ocr';
  if (/(sezion|section|intestazion|heading).*(non trovat|not found|assent|missing)|(non trovat|not found).*(sezion|section)/.test(r)) return 'profilo';
  if (/download|timeout|timed out|http \d|rete|network|connession|connection|503|502|429|temporane/.test(r)) return 'riprova';
  return 'riprova_profilo';
}

export type Categoria = 'rischi' | 'gestione' | 'contenziosi' | 'altre';
/** Stesse regole del peso delle sezioni nel contesto (agents/filing_context._peso). */
export function categoriaSezione(sezione?: string | null): Categoria {
  const s = String(sezione || '').toLowerCase();
  if ((s.includes('risk') || s.includes('rischi')) && !s.includes('market') && !s.includes('mercato')) return 'rischi';
  if (s.includes('legal') || s.includes('contenzios')) return 'contenziosi';
  if (['md&a', 'management', 'gestione'].some(k => s.includes(k))) return 'gestione';
  return 'altre';
}

export function pagineCitazione(c?: Pick<FilingCitation, 'pagine_fisiche'> | null): string | null {
  const p = (c?.pagine_fisiche || []).filter(n => Number.isFinite(n));
  if (!p.length) return null;
  const lo = Math.min(...p), hi = Math.max(...p);
  return lo === hi ? `p. ${lo}` : `p. ${lo}–${hi}`;
}

export interface Cambiamento {
  id: string; pos: number; tipo: string; sezione: string; categoria: Categoria;
  prima?: FilingCitation; dopo?: FilingCitation; pagina: string | null; url: string | null; citazioni: string[];
}

/** Frase di soli numeri: tabella impaginata come testo nei PDF (rinvio fase C). */
function tabella(testo?: string) {
  const t = String(testo || '');
  const cifre = (t.match(/\d/g) || []).length, lettere = (t.match(/\p{L}/gu) || []).length;
  return cifre >= 6 && cifre / Math.max(1, cifre + lettere) >= 0.5;
}
/** Numero d'elenco isolato («3.», «(iv)») spostato: rumore dell'impaginazione. */
const soloNumeroElenco = (testo?: string) => /^\s*\(?([0-9]{1,3}|[ivxlc]{1,6})[.)]?\s*$/i.test(String(testo || ''));

export function presentaCambiamenti(lista: { tipo: string; prima?: FilingCitation; dopo?: FilingCitation }[], _inEvidenza: string[]) {
  const visibili: Cambiamento[] = [], tabelle: Cambiamento[] = [];
  let elenchiNascosti = 0;
  lista.forEach((c, i) => {
    const pos = i + 1, id = `C${pos}`;
    if (c.tipo === 'spostato' && soloNumeroElenco(c.prima?.testo) && soloNumeroElenco(c.dopo?.testo)) { elenchiNascosti++; return; }
    const sezione = c.dopo?.sezione || c.prima?.sezione || '';
    const fonte = c.dopo || c.prima;
    const voce: Cambiamento = {
      id, pos, tipo: c.tipo, sezione, categoria: categoriaSezione(sezione), prima: c.prima, dopo: c.dopo,
      pagina: pagineCitazione(fonte), url: fonte?.url || null,
      citazioni: [c.prima ? `${id}-prima` : null, c.dopo ? `${id}-dopo` : null].filter((x): x is string => !!x),
    };
    const testi = [c.prima?.testo, c.dopo?.testo].filter(Boolean);
    if (testi.length && testi.every(t => tabella(t))) tabelle.push(voce); else visibili.push(voce);
  });
  return { visibili, tabelle, elenchiNascosti };
}

export type Filtro = 'evidenza' | Exclude<Categoria, 'altre'> | 'tutti';
const PRIMI = 5;
export function filtraCambiamenti(lista: Cambiamento[], filtro: Filtro, inEvidenza: string[]): Cambiamento[] {
  if (filtro === 'tutti') return lista;
  if (filtro === 'evidenza') {
    const per = new Map(lista.map(c => [c.id, c]));
    const scelti = inEvidenza.map(id => per.get(id)).filter((c): c is Cambiamento => !!c);
    return scelti.length ? scelti : lista.slice(0, PRIMI);
  }
  return lista.filter(c => c.categoria === filtro);
}

export type Tratto = { t: '=' | '-' | '+'; s: string };
const MAX_PAROLE = 600;
/** Diff a parole (LCS) per i «modificato»; oltre la soglia, un tolto e un aggiunto. */
export function diffParole(prima: string, dopo: string): Tratto[] {
  const a = prima.match(/\s*\S+/g) || [], b = dopo.match(/\s*\S+/g) || [];
  if (a.length > MAX_PAROLE || b.length > MAX_PAROLE) return [{ t: '-', s: prima }, { t: '+', s: dopo }];
  const n = a.length, m = b.length, k = (x: string) => x.trim();
  const L: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = k(a[i]) === k(b[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: Tratto[] = [];
  const push = (t: Tratto['t'], s: string) => { const u = out[out.length - 1]; if (u && u.t === t) u.s += s; else out.push({ t, s }); };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (k(a[i]) === k(b[j]) && a[i] === b[j]) { push('=', a[i]); i++; j++; }
    else if (k(a[i]) === k(b[j])) { push('-', a[i]); push('+', b[j]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) { push('-', a[i]); i++; }
    else { push('+', b[j]); j++; }
  }
  while (i < n) push('-', a[i++]);
  while (j < m) push('+', b[j++]);
  return out;
}

export type Tono = 'ok' | 'bad' | 'warn' | 'acc' | 'off';
export function tonoStato(s: FilingStatoUi): Tono {
  if (s === 'errore') return 'bad';
  if (s === 'da_confermare' || s === 'proposta_ai') return 'warn';
  if (s === 'in_corso' || s === 'novita') return 'acc';
  if (s === 'aggiornato' || s === 'invariato') return 'ok';
  return 'off';
}

const nf = (lingua: 'it' | 'en', d: number) => new Intl.NumberFormat(lingua === 'en' ? 'en-US' : 'it-IT', { minimumFractionDigits: d, maximumFractionDigits: d });
/** Cifra in scala breve: il numero formattato nella lingua e la scala (bn/mn/k), senza testo.
 *  Il suffisso («Mld», «B»…) viene dal catalogo (filingPage.num_*): vedi `w.cifraBreve` in parole.ts. */
export type ScalaBreve = 'bn' | 'mn' | 'k' | null;
export function scalaBreve(v: number | null | undefined, lingua: 'it' | 'en'): { testo: string; scala: ScalaBreve } | null {
  if (v == null || !Number.isFinite(v)) return null;
  const a = Math.abs(v);
  const [div, scala]: [number, ScalaBreve] = a >= 1e9 ? [1e9, 'bn'] : a >= 1e6 ? [1e6, 'mn'] : a >= 1e3 ? [1e3, 'k'] : [1, null];
  const x = v / div, d = Math.abs(x) >= 100 || div === 1 ? 0 : 2;
  return { testo: nf(lingua, d).format(x), scala };
}

/** Variazione % con tono; `inverso`: per scorte e debito un aumento non è una buona notizia. */
export function deltaNumero(pct: number | null | undefined, lingua: 'it' | 'en' = 'it', inverso = false): { testo: string; tono: 'up' | 'dn' | 'fl' } {
  if (pct == null || !Number.isFinite(pct)) return { testo: '—', tono: 'fl' };
  const segno = pct > 0 ? '+' : pct < 0 ? '−' : '';
  const testo = segno + nf(lingua, 1).format(Math.abs(pct)) + '%';
  if (Math.abs(pct) < 1) return { testo, tono: 'fl' };
  const buono = inverso ? pct < 0 : pct > 0;
  return { testo, tono: buono ? 'up' : 'dn' };
}

/** «Q3 2026», «H1 2026», «FY 2025», «9M 2026»; senza tipo la data intera. */
export function periodoBreve(fine?: string | null, tipo?: string | null): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(fine || ''));
  if (!m) return null;
  const [, y, mm, dd] = m, mese = Number(mm);
  if (tipo === 'annuale') return `FY ${y}`;
  if (tipo === 'trimestrale') return `Q${Math.ceil(mese / 3)} ${y}`;
  if (tipo === 'semestrale') return `H${mese <= 6 ? 1 : 2} ${y}`;
  if (tipo === 'nove_mesi') return `9M ${y}`;
  return `${dd}/${mm}/${y}`;
}
type Lato = { metadati?: Record<string, string> } | undefined;
export function coppiaBreve(coppia: { prima?: Lato; dopo?: Lato } | null | undefined, tipo?: string | null): string | null {
  const dopo = periodoBreve(coppia?.dopo?.metadati?.periodo_fine, tipo), prima = periodoBreve(coppia?.prima?.metadati?.periodo_fine, tipo);
  return dopo && prima ? `${dopo} vs ${prima}` : dopo;
}

/** Fase F: coppia trimestre su trimestre (emittente SEC nuovo, manca lo stesso trimestre dell'anno prima). */
export function trimestreSuTrimestre(r: { coppia?: { regola?: string } | null } | null | undefined): boolean {
  return r?.coppia?.regola === 'sequenziale';
}

/** «oggi 08:10», «ieri», «29 set»; `soloGiorno` per l'elenco, «ora» entro due minuti. */
export function quando(v: string | null | undefined, ora: Date, lingua: 'it' | 'en', w: { today: string; yesterday: string; now: string }, soloGiorno = false): string | null {
  const t = v ? Date.parse(v) : NaN;
  if (!Number.isFinite(t)) return null;
  const d = new Date(t), loc = lingua === 'en' ? 'en-GB' : 'it-IT';
  if (soloGiorno && Math.abs(ora.getTime() - t) < 120_000) return w.now;
  const giorno = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((giorno(ora) - giorno(d)) / 86_400_000);
  const hm = new Intl.DateTimeFormat(loc, { hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
  const base = diff === 0 ? w.today : diff === 1 ? w.yesterday
    : new Intl.DateTimeFormat(loc, { day: 'numeric', month: 'short' }).format(d).replace('.', '');
  return soloGiorno ? base : `${base} ${hm}`;
}

/** Numero di sezioni del profilo (della variante, se il profilo ne ha). */
export function sezioniProfilo(profilo: Record<string, unknown> | null | undefined, tipo?: string | null): number {
  if (!profilo) return 0;
  const varianti = Array.isArray(profilo.varianti) ? profilo.varianti as { tipo?: string; sezioni?: object }[] : [];
  const v = varianti.find(x => x?.tipo === tipo);
  const sezioni = (v?.sezioni ?? profilo.sezioni) as object | undefined;
  return sezioni && typeof sezioni === 'object' ? Object.keys(sezioni).length : 0;
}

/** ID dei cambiamenti citati nella riga del Consigliere, nell'ordine in cui compaiono. */
export function idCitati(testo: string): string[] {
  return [...new Set([...testo.matchAll(/\b(C\d+)-(?:prima|dopo)\b/g)].map(m => m[1]))];
}

/** Motivo per cui un titolo con profilo non ha un confronto («nessun confronto: …» della riga di
 *  stato), null se e' davvero in attesa del primo (prova reale 04/10/2026: repository ESEF fermo). */
export function motivoSenzaConfronto(statoRiga?: string | null): string | null {
  const m = /(?:nessun confronto|no comparison):\s*(.+?)(?:\s·\s(?:NON AGGIORNATO|NOT UPDATED).*)?$/.exec(String(statoRiga || ''));
  return m ? m[1].trim() : null;
}

/** Motivo breve per il riquadro «Freschezza» (fase F): senza prefisso della fonte né parentesi,
 *  al massimo ~48 caratteri («repository fermo al FY2022»); il motivo intero resta nella nota. */
export function motivoBreve(motivo: string): string {
  const m = motivo.replace(/^(?:ESEF|SEC|IR)\s*:\s*/i, '').replace(/\s*\([^)]*\)\s*$/, '')
    .replace(/\ball['’]esercizio\s+/i, 'al ').replace(/\bat (?:the )?fiscal year\s+/i, 'at ').trim();
  return m.length > 48 ? m.slice(0, 47).trimEnd() + '…' : m;
}

/** La proposta di collegamento va per ESEF? (preferita dal backend; senza, ESEF se la SEC non ha candidati). */
export function propostaEsef(p: FilingProposal | null | undefined): boolean {
  if (!p) return false;
  if (p.preferita === 'esef') return true;
  if (p.preferita === 'sec') return false;
  return (!p.sec.candidati.length && !!p.esef?.candidati.length) || (p.preferita === undefined && p.sec.stato === 'nessuno');
}

/** URL da salvare col profilo IR: il PDF verificato, gli altri PDF gia' verificati con la proposta
 *  (anche riaperta dall'archivio) e quelli scritti ora; al massimo 5 (limite del backend). */
export function urlDaSalvare(esito: { url?: string | null; altri?: { url: string }[] | null }, url: string, altriTesto: string): string[] {
  const scritti = altriTesto.split(/\s+/).map(u => u.trim()).filter(Boolean);
  return [...new Set([esito.url || url.trim(), ...(esito.altri ?? []).map(a => a.url), ...scritti].filter(Boolean))].slice(0, 5) as string[];
}
