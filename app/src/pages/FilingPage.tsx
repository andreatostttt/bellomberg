/* Pagina Filing (fase E, 03/10/2026): copertura dei filing per il Comitato, titoli in quattro gruppi
   e dettaglio con Cambiamenti, Numeri chiave e Cosa vede il Consigliere. Tutto lo stato sta qui
   (viste presentazionali; nuovi useState sempre in coda: i test SSR seminano per nome/indice).
   AI solo da pulsante: la pagina non chiama mai ai-proposal né «Verifica ora» da sola; la ricerca
   dei collegamenti (SEC/ESEF, gratis) parte solo quando l'utente apre un titolo da sistemare. */
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import ModernPage from '@/components/ModernPage';
import {
  Bellomberg, type FilingActivateMissing, type FilingAiEstimate, type FilingAiProposal, type FilingAiProposalSalvata,
  type FilingContextPreview, type FilingListing, type FilingOverview, type FilingProposal, type FilingRunDetail,
} from '@/lib/api';
import { leggiDetail } from '@/lib/quota';
import { leggiNumero } from '@/lib/cassa';
import { attendiPropostaAi, FilingAiJobError } from '@/lib/filing-ai-job';
import { FILING_EVENTO } from '@/components/BadgeFiling';
import { useLingua } from '@/i18n/provider';
import { GRUPPI, propostaEsef, raggruppa, statoDi, urlDaSalvare, type Filtro, type Ordine } from './filing/logica';
import { parole } from './filing/parole';
import VistaFiling from './filing/VistaFiling';
import { PAGINA } from './filing/Centro';
import type { AzioniFiling, DatiFiling } from './filing/tipi';
import './filing-nuova.css';

const errore = (e: any) => leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) || String(e);
const statoHttp = (e: any): number | null => typeof e?.response?.status === 'number' ? e.response.status : null;
const avvisaMenu = () => { try { window.dispatchEvent(new Event(FILING_EVENTO)); } catch { /* SSR */ } };
const POLL_MS = 5000;
const ATTESA_PRIMI_MS = 120_000;

export default function FilingPage() {
  const lingua = useLingua();
  const w = parole();
  const [params, setParams] = useSearchParams();
  const [ambito, setAmbito] = useState<'portafoglio' | 'preferiti'>('portafoglio');
  const [ordine, setOrdine] = useState<Ordine>('novita');
  const [overview, setOverview] = useState<FilingOverview | null>(null);
  const [overviewErr, setOverviewErr] = useState<{ stato: number | null; msg: string } | null>(null);
  const [sel, setSel] = useState<string | null>(() => params.get('t'));
  const [listing, setListing] = useState<FilingListing | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [detail, setDetail] = useState<FilingRunDetail | null>(null);
  const [preview, setPreview] = useState<FilingContextPreview | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [proposta, setProposta] = useState<FilingProposal | null>(null);
  const [propostaErr, setPropostaErr] = useState<string | null>(null);
  const [scelta, setScelta] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<Filtro>('evidenza');
  const [mostraTabelle, setMostraTabelle] = useState(false);
  const [lavoro, setLavoro] = useState<string | null>(null);
  const [avviso, setAvviso] = useState<{ tono: 'ok' | 'bad'; testo: string } | null>(null);
  const [esitoMancanti, setEsitoMancanti] = useState<FilingActivateMissing | null>(null);
  const [attesaPrimi, setAttesaPrimi] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [profiloAperto, setProfiloAperto] = useState(false);
  const [profileJson, setProfileJson] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [intervalHours, setIntervalHours] = useState('24');
  const [qualitative, setQualitative] = useState(false);
  const [aiAperta, setAiAperta] = useState(false);
  const [aiUrl, setAiUrl] = useState('');
  const [aiAltri, setAiAltri] = useState('');
  const [aiStima, setAiStima] = useState<FilingAiEstimate | null>(null);
  const [aiEsito, setAiEsito] = useState<FilingAiProposal | FilingAiProposalSalvata | null>(null);
  const [aiErrore, setAiErrore] = useState<string | null>(null);
  const [aiSostituisci, setAiSostituisci] = useState(false);
  const [manuale, setManuale] = useState<string | null>(null);
  const [conferma, setConferma] = useState<'scollega' | null>(null);
  const [copiato, setCopiato] = useState(false);
  const [cercaFonti, setCercaFonti] = useState(0);
  const [quanti, setQuanti] = useState(PAGINA);
  // fase F (in coda: i test SSR indicizzano gli hook): esito dell'ultima ricerca dei PDF sul sito
  const [ricercaPdf, setRicercaPdf] = useState<{ trovati: boolean; motivi: string[] } | null>(null);
  // G9b 04/10 (in coda): letture fallite di profilo/storico e del run, dichiarate col motivo
  const [listingErr, setListingErr] = useState<string | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const caricato = useRef<string | null>(null);
  // titolo per cui si sta leggendo il lavoro della proposta AI: cambiato/chiuso = smetti di leggere
  const aiInLettura = useRef<string | null>(null);
  useEffect(() => () => { aiInLettura.current = null; }, []);
  // titolo aperto dall'utente (clic o link ?t= da Mercati/Fondamentali/Agenti): solo allora si cercano i candidati
  const sceltoDaUtente = useRef(!!params.get('t'));

  const titoli = overview?.titoli ?? [];
  const titolo = titoli.find(t => t.ticker === sel) ?? null;
  const stato = titolo ? statoDi(titolo) : null;

  // panoramica: all'avvio, al cambio di ambito e a ogni revisione (azioni, polling)
  useEffect(() => {
    let vivo = true;
    Bellomberg.filingOverviewAmbito(ambito)
      .then(o => { if (vivo) { setOverview(o); setOverviewErr(null); } })
      .catch(e => { if (vivo) setOverviewErr({ stato: statoHttp(e), msg: errore(e) }); });
    return () => { vivo = false; };
  }, [ambito, revision]);

  // titolo scelto: da ?t=, altrimenti il primo dell'ordine; mai una richiesta di rete in più
  useEffect(() => {
    if (!overview) return;
    if (sel && overview.titoli.some(t => t.ticker === sel)) return;
    const primo = raggruppa(overview.titoli, ordine)[0]?.titoli[0]?.ticker ?? null;
    sceltoDaUtente.current = false;
    setSel(primo);
  }, [overview, sel, ordine]);

  // polling solo mentre qualcosa lavora (run del titolo, manager, primi confronti dopo l'attivazione)
  const lavora = !!overview && (overview.titoli.some(t => t.run_attivo) || overview.aggiornamento?.status === 'running'
    || (attesaPrimi != null && Date.now() - attesaPrimi < ATTESA_PRIMI_MS)) || !!listing?.active_run;
  useEffect(() => {
    if (!lavora) return;
    const id = window.setTimeout(() => setRevision(n => n + 1), POLL_MS);
    return () => window.clearTimeout(id);
  }, [lavora, revision]);

  // cambio titolo: stato del dettaglio azzerato
  useEffect(() => {
    setListing(null); setRunId(null); setDetail(null); setPreview(null); setPreviewErr(null); setProposta(null);
    setPropostaErr(null); setScelta(null); setFiltro('evidenza'); setMostraTabelle(false); setAvviso(null);
    setProfiloAperto(false); setAiAperta(false); setAiUrl(''); setAiAltri(''); setAiStima(null); setAiEsito(null);
    setAiErrore(null); setAiSostituisci(false); setManuale(null); setConferma(null); setCopiato(false); setQuanti(PAGINA);
    setCercaFonti(0); // una ricerca chiesta per un titolo non vale per il successivo (revisione finale)
    setRicercaPdf(null);
    setListingErr(null); setDetailErr(null); // errori di lettura del titolo PRECEDENTE (revisione G9b R2)
    caricato.current = null;
  }, [sel]);

  // stato del titolo (profilo, run), anteprima del contesto
  useEffect(() => {
    let vivo = true;
    if (!sel) return () => { vivo = false; };
    Bellomberg.filingList(sel).then(row => {
      if (!vivo) return;
      setListing(row); setListingErr(null);
      // di default il run del confronto citato dal Consigliere (overview.run_id): «In evidenza» vale per quello
      setRunId(id => id != null && (row.runs?.some(r => r.id === id) || row.ultimo_completo?.id === id || id === titolo?.run_id) ? id
        : (titolo?.run_id ?? row.ultimo_completo?.id ?? row.runs?.find(r => !r.controllo_leggero)?.id ?? null));
      const chiave = row.profile ? `${sel}:${row.profile.version}` : `${sel}:none`;
      if (caricato.current !== chiave) {
        caricato.current = chiave;
        setProfileJson(row.profile ? JSON.stringify(row.profile.profile, null, 2) : '');
        setEnabled(row.profile?.enabled ?? true);
        setIntervalHours(row.profile ? String(row.profile.interval_hours) : '24');
        setQualitative(row.profile?.qualitative_enabled ?? false);
      }
    }).catch(e => { if (vivo) { setListing(null); setListingErr(errore(e)); } });
    return () => { vivo = false; };
  }, [sel, revision]);

  const nelContesto = titolo?.nel_contesto !== false && ambito === 'portafoglio';
  useEffect(() => {
    let vivo = true;
    if (!sel || !nelContesto) return () => { vivo = false; };
    Bellomberg.filingContextPreview(sel).then(p => { if (vivo) { setPreview(p); setPreviewErr(null); } })
      .catch(e => { if (vivo) { setPreview(null); setPreviewErr(errore(e)); } });
    return () => { vivo = false; };
  }, [sel, nelContesto, revision]);

  useEffect(() => {
    let vivo = true;
    if (runId == null) return () => { vivo = false; };
    Bellomberg.filingRun(runId).then(r => { if (vivo && r.ticker === sel) { setDetail(r); setDetailErr(null); } })
      .catch(e => { if (vivo) { setDetail(null); setDetailErr(errore(e)); } });
    return () => { vivo = false; };
  }, [sel, runId, revision]);

  // proposta AI in archivio (gratis: nessun download, nessuna chiamata al modello)
  const shaSalvata = titolo?.proposta_ai?.sha256 ?? null;
  useEffect(() => {
    let vivo = true;
    if (!sel || !shaSalvata) return () => { vivo = false; };
    Bellomberg.filingAiSaved(sel).then(p => { if (vivo) { setAiEsito(e => e ?? p); if (p.url) setAiUrl(u => u || p.url || ''); } })
      .catch(() => { /* nessuna proposta leggibile: la card resta sul modulo */ });
    return () => { vivo = false; };
  }, [sel, shaSalvata]);

  // candidati SEC/ESEF: solo per un titolo da sistemare aperto dall'utente, o su richiesta
  const daCollegare = stato === 'da_confermare' || stato === 'senza_fonte' || stato === 'non_attivo';
  useEffect(() => {
    let vivo = true;
    if (!sel || !daCollegare || (!sceltoDaUtente.current && cercaFonti === 0)) return () => { vivo = false; };
    setPropostaErr(null);
    Bellomberg.filingProposal(sel).then(p => {
      if (!vivo || p.ticker !== sel) return;
      setProposta(p);
      // candidato preselezionato solo se e' l'unico: con piu' emittenti la scelta e' esplicita
      const esef = propostaEsef(p);
      const lista = esef ? (p.esef?.candidati ?? []).map(c => c.lei) : p.sec.candidati.map(c => c.cik);
      setScelta(s => s ?? (lista.length === 1 ? lista[0] : null));
    }).catch(e => { if (vivo) setPropostaErr(errore(e)); });
    return () => { vivo = false; };
  }, [sel, daCollegare, cercaFonti]);

  const ricarica = () => { setRevision(n => n + 1); avvisaMenu(); };
  const esegui = async (nome: string, fn: () => Promise<string | null>) => {
    setLavoro(nome); setAvviso(null);
    try { const testo = await fn(); if (testo) setAvviso({ tono: 'ok', testo }); ricarica(); }
    catch (e) { setAvviso({ tono: 'bad', testo: w.actionError(errore(e)) }); }
    finally { setLavoro(null); }
  };

  const scegli = (t: string) => {
    sceltoDaUtente.current = true;
    setSel(t);
    setParams(p => { const n = new URLSearchParams(p); n.set('t', t); return n; }, { replace: true });
  };
  const rivedi = () => {
    const daConfermare = raggruppa(titoli, ordine).flatMap(g => g.titoli).filter(t => statoDi(t) === 'da_confermare');
    if (!daConfermare.length) return;
    const i = daConfermare.findIndex(t => t.ticker === sel);
    scegli(daConfermare[(i + 1) % daConfermare.length].ticker);
  };
  const urlAltri = () => [...new Set(aiAltri.split(/\s+/).map(u => u.trim()).filter(u => u && u !== aiUrl.trim()))].slice(0, 4);

  const azioni: AzioniFiling = {
    ambito: a => { setAmbito(a); setSel(null); setEsitoMancanti(null); },
    ordine: setOrdine,
    scegli,
    rivedi,
    riprovaElenco: () => setRevision(n => n + 1),
    autoRefresh: () => overview?.controllo_giornaliero && esegui('auto', async () => {
      await Bellomberg.filingAutoRefresh(!overview.controllo_giornaliero!.attivo); return null;
    }),
    attivaMancanti: () => esegui('mancanti', async () => {
      const out = await Bellomberg.filingActivateMissing();
      setEsitoMancanti(out);
      if (out.attivati.length) setAttesaPrimi(Date.now());
      return null;
    }),
    chiudiEsito: () => setEsitoMancanti(null),
    filtro: f => { setFiltro(f); setQuanti(PAGINA); },
    altri: () => setQuanti(n => n + PAGINA),
    tabelle: () => setMostraTabelle(v => !v),
    verifica: () => sel && esegui('verifica', async () => {
      const q = await Bellomberg.filingRefresh(sel); setRunId(q.run_id); return w.queued;
    }),
    profilo: () => { setProfiloAperto(v => !v); setAiAperta(false); setConferma(null); },
    scelta: setScelta,
    attiva: (s?: { cik?: string; lei?: string }) => sel && esegui('attiva', async () => {
      const out = await Bellomberg.filingActivate(sel, s);
      if (out.esito !== 'attivato') throw new Error(out.motivo || out.esito);
      setAttesaPrimi(Date.now()); return w.activated;
    }),
    rifiuta: () => sel && proposta && esegui('rifiuta', async () => {
      const esef = propostaEsef(proposta);
      const out = await Bellomberg.filingReject(sel, esef
        ? { lei: (proposta.esef?.candidati ?? []).map(c => c.lei) } : { cik: proposta.sec.candidati.map(c => c.cik) });
      setProposta(out.proposta); setScelta(null); return w.rejected;
    }),
    cercaFonti: () => setCercaFonti(n => n + 1),
    manuale: setManuale,
    attivaManuale: () => {
      const v = (manuale || '').trim().toUpperCase();
      if (/^\d{1,10}$/.test(v)) return azioni.attiva({ cik: v });
      if (/^[A-Z0-9]{20}$/.test(v)) return azioni.attiva({ lei: v });
      setAvviso({ tono: 'bad', testo: w.manualInvalid });
    },
    escludi: (escluso: boolean) => sel && esegui('escludi', async () => {
      await Bellomberg.filingExclude(sel, escluso); return escluso ? w.excludedOk : w.includedOk;
    }),
    conferma: setConferma,
    scollega: () => sel && esegui('scollega', async () => {
      await Bellomberg.filingUnlink(sel); setConferma(null); setProfiloAperto(false); return w.advUnlinked;
    }),
    profileJson: setProfileJson, enabled: setEnabled, intervalHours: setIntervalHours, qualitative: setQualitative,
    salvaProfilo: () => {
      if (!sel) return;
      let profile: Record<string, unknown>;
      try {
        const p = JSON.parse(profileJson);
        if (!p || typeof p !== 'object' || Array.isArray(p)) throw new Error();
        profile = p;
      } catch { setAvviso({ tono: 'bad', testo: w.advInvalidJson }); return; }
      // testo letto come nelle altre cifre del modulo (leggiNumero): niente <input type="number">, che in en-GB scarta la virgola
      const lettura = leggiNumero(intervalHours);
      const interval = lettura?.ok ? lettura.valore : NaN;
      if (!Number.isSafeInteger(interval) || interval < 1) { setAvviso({ tono: 'bad', testo: w.advInvalidInterval }); return; }
      return esegui('salva', async () => {
        await Bellomberg.filingSaveProfile(sel, { profile, enabled, interval_hours: interval, qualitative_enabled: qualitative });
        return w.advSaved;
      });
    },
    run: setRunId,
    copia: () => {
      if (!preview?.testo) return;
      navigator.clipboard?.writeText(preview.testo).then(() => setCopiato(true), () => setCopiato(false));
    },
    apriAi: () => { setAiAperta(v => !v); setProfiloAperto(false); },
    // fase F: ricerca dei PDF sul sito (gratis) e stima coi PDF trovati gia' compilati (gratis); la
    // proposta AI resta il pulsante «Proponi con AI»
    cercaPdf: async () => {
      if (!sel) return;
      setLavoro('cerca-pdf'); setAiErrore(null);
      try {
        const out = await Bellomberg.filingSearchPdf(sel);
        setRicercaPdf({ trovati: !!out.pdf_ir, motivi: out.motivi || [] });
        ricarica();
      } catch (e) { setRicercaPdf({ trovati: false, motivi: [errore(e)] }); }
      finally { setLavoro(null); }
    },
    stimaTrovati: async () => {
      const trovati = titolo?.pdf_ir;
      if (!sel || !trovati) return;
      setAiUrl(trovati.ultimo.url); setAiAltri(trovati.precedente?.url || '');
      setAiEsito(null); setAiSostituisci(false); setAiAperta(true); setProfiloAperto(false);
      setLavoro('ai-stima'); setAiErrore(null); setAiStima(null);
      try { setAiStima(await Bellomberg.filingAiEstimate(sel, trovati.ultimo.url)); }
      catch (e) { setAiErrore(w.aiError(errore(e))); }
      finally { setLavoro(null); }
    },
    aiUrl: v => { setAiUrl(v); setAiStima(null); setAiEsito(null); setAiSostituisci(false); },
    aiAltri: v => { setAiAltri(v); setAiEsito(null); setAiSostituisci(false); },
    // stima: gratis (download e misura); proposta: l'UNICA chiamata AI, solo da questo pulsante
    aiStima: async () => {
      if (!sel) return;
      setLavoro('ai-stima'); setAiErrore(null); setAiEsito(null);
      try { setAiStima(await Bellomberg.filingAiEstimate(sel, aiUrl.trim())); }
      catch (e) { setAiStima(null); setAiErrore(w.aiError(errore(e))); }
      finally { setLavoro(null); }
    },
    aiProponi: async (riprova = false) => {
      if (!sel) return;
      setLavoro('ai-proposta'); setAiErrore(null);
      const titoloAi = sel;
      aiInLettura.current = titoloAi;
      try {
        // il server lavora (e paga) anche oltre la risposta: si legge lo stato del job fino all'esito vero
        const out = await attendiPropostaAi({
          avvia: () => Bellomberg.filingAiProposal(titoloAi, aiUrl.trim(), urlAltri(), riprova),
          leggi: jobId => Bellomberg.filingAiProposalJob(titoloAi, jobId),
          onAvanzamento: parziale => { if (aiInLettura.current === titoloAi) setAiEsito(parziale); },
          fermato: () => aiInLettura.current !== titoloAi,
        });
        setAiEsito(out);
        if (out.stato === 'error') setAiErrore(w.aiError(out.dettaglio || ''));
        ricarica();
      } catch (e) {
        if (e instanceof FilingAiJobError) {
          // lettura finita senza esito: «in lavorazione da N s» non resta a schermo come se fosse vivo
          if (e.code !== 'stopped') setAiEsito(prev => prev?.stato === 'in_corso' ? null : prev);
          if (e.code === 'job_lost') setAiErrore(w.aiJobLost);
          else if (e.code === 'unreachable') setAiErrore(w.aiUnreachable);
          else if (e.code === 'timeout') setAiErrore(w.aiTimeout(String(e.minuti ?? '')));
          else if (e.code !== 'stopped') setAiErrore(w.aiError(e.message));
        } else setAiErrore(w.aiError(errore(e)));
      }
      finally { if (aiInLettura.current === titoloAi) aiInLettura.current = null; setLavoro(null); }
    },
    aiSalva: async (modo: 'nuovo' | 'variante' | 'sostituisci') => {
      if (!sel || !aiEsito?.sha256) return;
      const irUrls = urlDaSalvare(aiEsito, aiUrl, aiAltri);
      setLavoro('ai-salva'); setAiErrore(null);
      try {
        const out = await Bellomberg.filingAiAccept(sel, { sha256: aiEsito.sha256, ir_urls: irUrls,
          ...(modo === 'variante' ? { aggiungi_variante: true } : modo === 'sostituisci' ? { sostituisci: true } : {}) });
        setAvviso({ tono: 'ok', testo: out.esito === 'variante_aggiunta' ? w.aiVariantAdded(aiEsito.tipo || '') : w.aiSaved });
        setAiEsito(null); setAiStima(null); setAiAperta(false); setAiSostituisci(false); setAttesaPrimi(Date.now()); ricarica();
      } catch (e) {
        if (statoHttp(e) === 409 && modo === 'nuovo' && /sostitu|replace/i.test(errore(e))) setAiSostituisci(true);
        else setAiErrore(w.aiError(errore(e)));
      } finally { setLavoro(null); }
    },
    aiScarta: async () => {
      if (!sel) return;
      const sha = aiEsito?.sha256;
      setAiEsito(null); setAiStima(null); setAiSostituisci(false); setAiAperta(false);
      if (!sha) return;
      await esegui('ai-scarta', async () => { await Bellomberg.filingAiDiscard(sel, sha); return w.aiDiscarded; });
    },
  };

  const dati: DatiFiling = {
    lingua, ambito, ordine, overview, overviewErr, sel, titolo, stato, listing, listingErr, runId, detail, detailErr, preview, previewErr,
    proposta, propostaErr, scelta, filtro, mostraTabelle, lavoro, avviso, esitoMancanti,
    // primi confronti davvero in corso: finestra dopo l'attivazione e manager o run al lavoro
    primiInCorso: attesaPrimi != null && Date.now() - attesaPrimi < ATTESA_PRIMI_MS && !!esitoMancanti?.attivati.length
      && (overview?.aggiornamento?.status === 'running' || titoli.some(t => t.run_attivo)
        || titoli.some(t => esitoMancanti.attivati.includes(t.ticker) && statoDi(t) === 'primo_confronto' && !t.stato_riga.match(/nessun confronto|no comparison/))),
    profiloAperto, profileJson, enabled, intervalHours, qualitative, aiAperta, aiUrl, aiAltri, aiStima, aiEsito, aiErrore,
    aiSostituisci, manuale, conferma, copiato, quanti, ricercaPdf, ricercaFonti: daCollegare && (sceltoDaUtente.current || cercaFonti > 0), gruppi: overview ? raggruppa(overview.titoli, ordine) : [], ordineGruppi: GRUPPI,
  };
  return <ModernPage page="filing" render={() => <VistaFiling d={dati} a={azioni} />} />;
}
