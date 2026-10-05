import type {
  FilingActivateMissing, FilingAiEstimate, FilingAiProposal, FilingAiProposalSalvata, FilingContextPreview,
  FilingListing, FilingOverview, FilingOverviewTitolo, FilingProposal, FilingRunDetail, FilingStatoUi,
} from '@/lib/api';
import type { Lingua } from '@/i18n/lingua';
import type { Filtro, GRUPPI, Ordine, raggruppa } from './logica';

/** Dati della pagina Filing passati alle viste (tutto lo stato vive in FilingPage). */
export interface DatiFiling {
  lingua: Lingua;
  ambito: 'portafoglio' | 'preferiti';
  ordine: Ordine;
  overview: FilingOverview | null;
  overviewErr: { stato: number | null; msg: string } | null;
  sel: string | null;
  titolo: FilingOverviewTitolo | null;
  stato: FilingStatoUi | null;
  listing: FilingListing | null;
  /** lettura di /filing/{t} fallita: motivo dichiarato, mai «caricamento» infinito */
  listingErr: string | null;
  runId: number | null;
  detail: FilingRunDetail | null;
  /** lettura del run fallita: motivo dichiarato (le cifre del filing non spariscono in silenzio) */
  detailErr: string | null;
  preview: FilingContextPreview | null;
  previewErr: string | null;
  proposta: FilingProposal | null;
  propostaErr: string | null;
  scelta: string | null;
  filtro: Filtro;
  mostraTabelle: boolean;
  lavoro: string | null;
  avviso: { tono: 'ok' | 'bad'; testo: string } | null;
  esitoMancanti: FilingActivateMissing | null;
  primiInCorso: boolean;
  profiloAperto: boolean;
  profileJson: string;
  enabled: boolean;
  intervalHours: string;
  qualitative: boolean;
  aiAperta: boolean;
  aiUrl: string;
  aiAltri: string;
  aiStima: FilingAiEstimate | null;
  aiEsito: FilingAiProposal | FilingAiProposalSalvata | null;
  aiErrore: string | null;
  aiSostituisci: boolean;
  manuale: string | null;
  conferma: 'scollega' | null;
  copiato: boolean;
  /** ricerca dei candidati SEC/ESEF avviata (titolo aperto dall'utente o pulsante) */
  ricercaFonti: boolean;
  /** fase F: esito dell'ultima ricerca dei PDF sul sito chiesta dall'utente */
  ricercaPdf: { trovati: boolean; motivi: string[] } | null;
  /** cambiamenti mostrati (pagine da 100: un titolo ESEF ne ha migliaia) */
  quanti: number;
  gruppi: ReturnType<typeof raggruppa>;
  ordineGruppi: typeof GRUPPI;
}

export interface AzioniFiling {
  ambito: (a: 'portafoglio' | 'preferiti') => void;
  ordine: (o: Ordine) => void;
  scegli: (ticker: string) => void;
  rivedi: () => void;
  riprovaElenco: () => void;
  autoRefresh: () => unknown;
  attivaMancanti: () => unknown;
  chiudiEsito: () => void;
  filtro: (f: Filtro) => void;
  tabelle: () => void;
  altri: () => void;
  verifica: () => unknown;
  profilo: () => void;
  scelta: (id: string) => void;
  attiva: (s?: { cik?: string; lei?: string }) => unknown;
  rifiuta: () => unknown;
  cercaFonti: () => void;
  manuale: (v: string | null) => void;
  attivaManuale: () => unknown;
  escludi: (escluso: boolean) => unknown;
  conferma: (c: 'scollega' | null) => void;
  scollega: () => unknown;
  profileJson: (v: string) => void;
  enabled: (v: boolean) => void;
  intervalHours: (v: string) => void;
  qualitative: (v: boolean) => void;
  salvaProfilo: () => unknown;
  run: (id: number) => void;
  copia: () => void;
  apriAi: () => void;
  cercaPdf: () => unknown;
  stimaTrovati: () => unknown;
  aiUrl: (v: string) => void;
  aiAltri: (v: string) => void;
  aiStima: () => unknown;
  aiProponi: (riprova?: boolean) => unknown;
  aiSalva: (modo: 'nuovo' | 'variante' | 'sostituisci') => unknown;
  aiScarta: () => unknown;
}
