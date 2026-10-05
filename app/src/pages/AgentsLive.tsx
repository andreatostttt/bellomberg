import type { CSSProperties, ReactNode } from 'react';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
/* ============================================================
   F4 AGENTI IN DIRETTA — la run del consigliere (redesign Nuova, 02/10/2026)

   Stesso impianto della Dashboard e della Chat agenti (palette --bbn-*):
     · box della run in alto: stato, durata, costo (n.d. dichiarato, «~» se
       parziale), chiamate, avvio/stop, e le tappe Avvio → R0 → R1 → R2 →
       Sintesi → Memo con la frase del presente;
     · una card per desk (icona della Chat con l'anello di stato) che dice cosa
       fa adesso, e la card del Capo con red team, riflessione e tabella azioni;
     · a destra un pannello a schede: chiamate, costi, strumenti, ticker.
   Il quadrante orbitale e il cielo animato non ci sono piu': i dati che
   portavano (finestre, fasi, tool_log, cache, durate) stanno nelle tappe,
   nelle card e nel pannello.

   La logica della run non cambia: avvio con RunConfirmDialog, polling di
   /agents/live ogni 1,5 s, stop con cancel + reset + rilettura, heartbeat
   illeggibile tenuto distinto da «nessuna run» (N7), un solo verdetto per
   agente dove vince il peggiore, n.d. mai mostrato come zero.
   ============================================================ */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '@/i18n/provider';
import ModernPage from '@/components/ModernPage';
import { leggiDetail } from '@/lib/quota';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, AgentInfo, AgentsLiveState, EnginesInfo, FilingActivateMissing, FilingOverview, UsageBySpecialist, UsageTotal } from '@/lib/api';
import RunConfirmDialog from '@/components/RunConfirmDialog';
import WeeklyRecoveryPanel from '@/components/WeeklyRecoveryPanel';
import { useInterfaceTheme } from '@/components/InterfaceThemeProvider';
import { useBox } from '@/lib/useBox';
import { derivePlancia, engineShort, fmtDurShort, type Desk } from '@/lib/plancia-data';
import { conservaDettaglioRun, dettaglioLeggibile, statusHttp } from '@/lib/mandato';
import { frase } from '@/lib/frase';
import { Segmenti } from '@/components/nuova/Card';
import { ArrowUpRight, Check, CircleAlert, FileText, Lightbulb, Play, Square, TriangleAlert, WifiOff } from 'lucide-react';
import IconaDesk, { coloreDesk } from './chat/IconaDesk';
import { AnelloDesk, Barra, CardCapo, CardDesk, Kpi, Tappe, type StatoDesk, type StatoRound, type Tappa, type VistaCapo, type VistaDesk } from './agents/VistaAgenti';
import { parole } from './agents/parole';
import CoperturaFiling, { mancantiFiling, type ErroreFiling } from './agents/CoperturaFiling';
import { aggiornamentoInCorso, pollFiling } from '@/lib/filing-poll';
import './agents-nuova.css';

type SchedaPannello = 'chiamate' | 'strumenti' | 'ticker' | 'filing';
/** iniziale maiuscola per le voci di catalogo scritte in minuscolo (frase() tocca solo il MAIUSCOLO) */
const maiuscola = (s: string) => s ? s.charAt(0).toLocaleUpperCase() + s.slice(1) : s;

const ACTIVE_RUN_KEY = 'bellomberg_active_run';
const POLL_INTERVAL_MS = 1500;

/* ── formattatori: convenzione della lingua corrente in tutta la pagina ─
   null/NaN => "n.d.": il dato NON e' calcolabile e va dichiarato. Uno
   "0,00 EUR" al posto di un buco e' il bug peggiore su un pannello costi
   (regola no-fallback-silenziosi): qui non deve MAI comparire. */
const fmtEur = (v: number | null | undefined) =>
  v == null || !isFinite(v) ? tr('activity.unavailable')
    : new Intl.NumberFormat(localeDi(linguaCorrente()), { style: 'currency', currency: 'EUR',
        minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: true }).format(v);
const fmtN = (v: number | null | undefined, d = 0) =>
  v == null || !isFinite(v) ? tr('activity.unavailable')
    : new Intl.NumberFormat(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: true }).format(v);
const fmtTok = (v: number | null | undefined) =>
  v == null ? tr('activity.unavailable') : v >= 1000 ? fmtN(v / 1000, 1) + 'k' : fmtN(v);

/* status !== 'ok' = BUCO, esattamente come cost_eur null. status assente =
   heartbeat di una run vecchia -> nessun giudizio, in nessuno dei due sensi. */
const isHoleStatus = (s?: string | null) => s != null && s !== 'ok';
/* "e' andato KO" != "non so quanto e' costato": api_error/usage_unknown sono
   fallimenti dell'agente; model_unknown/pricing_unavailable sono solo costi
   non calcolabili su una chiamata riuscita. Il marchio KO va solo ai primi. */
const isErrorStatus = (s?: string | null) => s === 'api_error' || s === 'usage_unknown';

function statusDescriptions(): Record<string, string> { return {
  api_error: tr('activity.apiFailed'),
  usage_unknown: tr('activity.usageMissing'),
  model_unknown: tr('activity.modelUnpriced'),
  pricing_unavailable: tr('activity.pricingMissing'),
}; }

/* ── UN SOLO VERDETTO PER AGENTE, e vince il peggiore ────────────────────
   Prima la stessa card mostrava un badge verde "ok" (da specialist_status,
   che dice solo "ha consegnato il report") e un costo ambra (da
   usage.status = api_error, che dice "ha sbattuto contro l'API"). Sono due
   domande diverse, ma a schermo deve arrivare una risposta sola. */
type Verdict = { k: string; cls: string; t: string };
type StopNotice = { issues: { source: string; detail: string | null }[]; running: boolean | null };
const phaseText = (key: string) => key === 'SINTESI' ? tr('activity.synthesis')
  : key === 'FRA ROUND' ? tr('activity.betweenRounds') : key;
function verdictOf(d: Pick<Desk, 'statusRun' | 'statusUsage' | 'cost'>): Verdict {
  const STATUS_DESC = statusDescriptions();
  if (isErrorStatus(d.statusUsage))
    /* «ha consegnato il report» solo se e' done: un desk KO in un round prima
       puo' essere in corsa ORA (review F45), e allora si dicono i due fatti */
    return { k: 'KO', cls: 'ko',
      t: `${STATUS_DESC[d.statusUsage!] || tr('activity.declaredError')}${
        d.statusRun === 'running' ? tr('activity.earlierRound', {a: fmtEur(d.cost)})
        : d.statusRun === 'done' ? tr('activity.deliveredDespite', {a: fmtEur(d.cost)})
        : tr('activity.spent', {a: fmtEur(d.cost)})}` };
  if (isHoleStatus(d.statusUsage))
    return { k: 'n.d.', cls: 'amc',
      t: STATUS_DESC[d.statusUsage!] || tr('activity.backendAnomaly', {a: d.statusUsage!}) };
  if (d.statusRun === 'done') return { k: 'OK', cls: 'okc', t: tr('activity.reportDelivered') };
  if (d.statusRun === 'running') return { k: 'RUN', cls: 'amc', t: tr('activity.executing') };
  if (d.statusRun === 'error') return { k: 'KO', cls: 'ko', t: tr('activity.specialistError') };
  return { k: '—', cls: 'nd', t: tr('activity.statusNotDeclared') };
}

/* buchi da dichiarare sul totale. NB: unpriced_agents del backend mescola DUE
   casi (base.py: cost_eur is None OPPURE partial) — chi non ha alcun costo e
   chi ne ha uno incompleto. Vanno nominati separatamente, o la nota
   contraddice la cifra che le sta accanto. */
function costNotes(u?: UsageTotal | null, by?: Record<string, UsageBySpecialist> | null): string[] {
  if (!u) return [];
  const n: string[] = [];
  if (u.error) n.push(tr('activity.costAggregationError', { a: u.error }));
  const named = u.unpriced_agents || [];
  const noCost = named.filter(a => by?.[a] && by[a].cost_eur == null);
  const onlyPartial = named.filter(a => by?.[a] && by[a].cost_eur != null);
  const unknownKind = named.filter(a => !by?.[a]);
  if (noCost.length) n.push(tr('activity.costCannotCalculate', { a: noCost.join(', ') }));
  if (onlyPartial.length) n.push(tr('activity.partialCost', { a: onlyPartial.join(', ') }));
  if (unknownKind.length) n.push(tr('activity.missingOrPartialCost', { a: unknownKind.join(', ') }));
  if (!named.length && u.partial === true) n.push(tr('activity.partialTotal'));
  if (u.fx_source === 'fallback') n.push(tr('activity.fxFallback'));
  if (u.fx_source === 'n.d.') n.push(tr('activity.fxMissing'));
  return n;
}
const isPartial = (u?: UsageTotal | null) =>
  u?.partial != null ? u.partial === true : !!u?.unpriced_agents?.length;

/* ══════════════════════════════════════════════════════════════════════ */
export default function AgentsLive() {
  const tr = useT();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [engines, setEngines] = useState<EnginesInfo | undefined>(undefined);
  const [rosterErr, setRosterErr] = useState<string | null>(null);
  const [state, setState] = useState<AgentsLiveState | null>(null);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  /* N7 (blocco D): l'heartbeat letto male in QUESTO istante non e' «nessuna
     run» — il payload {running:false, heartbeat:"illeggibile"} non deve
     entrare in setState (cancellerebbe plancia e activeTaskId per un giro):
     resta l'ultimo stato buono e il buco si dichiara in vetrina CON LA SUA
     DURATA (da = primo poll illeggibile consecutivo, al = l'ultimo): senza,
     un buco di 15 minuti si leggeva come un glitch momentaneo (review 31/08). */
  const [hbIll, setHbIll] = useState<{ msg: string | null; da: number; al: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [cursor, setCursor] = useState<number | null>(null);
  const [pinned, setPinned] = useState(false);
  const [dialRef, dialBox] = useBox<HTMLDivElement>();

  useEffect(() => {
    Bellomberg.agentsList()
      .then(r => { setAgents(r?.agents || []); setEngines(r?.engines); setRosterErr(null); })
      .catch(e => setRosterErr(leggiDetail(e?.response?.data?.detail || e?.message || String(e))));
  }, []);

  useEffect(() => {
    let mounted = true, inFlight = false;
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const s = await Bellomberg.agentsLive();
        if (mounted && s) {
          /* chiave nuova dal 27/08 (ponte (90)); il fallback sul testo copre il
             backend vecchio, che comincia il message con «read error» */
          const illeggibile = s.heartbeat === 'illeggibile'
            || (s.running === false && typeof s.message === 'string' && s.message.startsWith('read error'));
          if (illeggibile) {
            const msg = s.message || null;
            setHbIll(prev => ({ msg, da: prev?.da ?? Date.now(), al: Date.now() }));
          }
          else { setState(s); setHbIll(null); }
          setLiveErr(null);
        }
      }
      /* l'errore di RETE azzera hbIll: «l'ultimo poll ha risposto …» sarebbe
         falso sotto un poll che non ha risposto affatto (review 31/08); se al
         ritorno della rete il file e' ancora illeggibile, hbIll torna da solo */
      catch (e: any) { if (mounted) { setLiveErr(leggiDetail(e?.response?.data?.detail || e?.message || String(e))); setHbIll(null); } }
      finally { inFlight = false; }
    };
    tick();
    const i = setInterval(tick, POLL_INTERVAL_MS);
    return () => { mounted = false; clearInterval(i); };
  }, []);

  const isRunning = state?.running === true;

  /* l'orologio serve SOLO a una run viva: la sua durata non e' nel payload */
  useEffect(() => {
    if (!isRunning) return;
    const i = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(i);
  }, [isRunning]);

  useEffect(() => {
    if (!state?.start_time || !isRunning) { setElapsed(0); return; }
    const start = new Date(state.start_time).getTime();
    if (!isFinite(start)) return;
    const i = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(i);
  }, [state?.start_time, isRunning]);

  const P0 = useMemo(() => derivePlancia(state, agents, engines, now), [state, agents, engines, now, tr]);
  /* Il tema non cambia i dati: i colori dei desk li normalizza IconaDesk
     (coloreDesk), uguali in Chiaro e in Scuro. Il memo resta per non spostare
     l'ordine degli hook. */
  const dark = useInterfaceTheme().effective === 'dark';
  const P = useMemo(() => P0, [P0, dark]);

  /* Il cursore: su una run VIVA insegue il presente; su una run chiusa parte
     dal minuto di massima attivita', che e' l'istante piu' informativo da
     mostrare per primo (e non un default arbitrario). */
  useEffect(() => {
    if (pinned || !P.ok) return;
    if (P.live) { setCursor(P.runSec); return; }
    if (cursor == null) {
      let best = 0, bestN = -1;
      for (let s = 0; s <= P.runSec; s += 30) {
        const n = P.windows.filter(w => s >= w.t0 && s <= w.t1).length;
        if (n > bestN) { bestN = n; best = s; }
      }
      setCursor(best);
    }
  }, [P.ok, P.live, P.runSec, P.windows, pinned, cursor]);

  /* ── comando della run: identico alla versione precedente ───────────── */
  const [activeTaskId, setActiveTaskId] = useState<string | null>(() => {
    try { return localStorage.getItem(ACTIVE_RUN_KEY); } catch { return null; }
  });
  const [stopping, setStopping] = useState(false);
  const [triggerMsg, setTriggerMsg] = useState<{ kind: 'starting' | 'active'; id?: string | null } | null>(null);
  const [askRun, setAskRun] = useState(false);   // Lotto D: la run costa, si conferma prima
  const [stopNotice, setStopNotice] = useState<StopNotice | null>(null);

  const triggerRun = async () => {
    setTriggerMsg({ kind: 'starting' }); setStopNotice(null);
    setState(prev => ({ ...(prev || {}), running: true,
      start_time: new Date().toISOString(), message: tr('activity.startShort') } as AgentsLiveState));
    try {
      const r = await Bellomberg.runConsigliere();
      const tid = r?.task_id || null;
      if (tid) { try { localStorage.setItem(ACTIVE_RUN_KEY, tid); } catch {} setActiveTaskId(tid); }
      setTriggerMsg({ kind: 'active', id: tid });
    } catch (e: any) {
      const detail = dettaglioLeggibile(e);
      setState(prev => prev ? { ...prev, running: false } : prev);
      setTriggerMsg(null);
      alert(tr('activity.startFailed', { a: detail }));
      if (statusHttp(e) === 428) { conservaDettaglioRun(detail); navigate('/mandato'); }
    }
  };

  const stopRun = async () => {
    if (!confirm(tr('activity.stopQuestion'))) return;
    setStopping(true);
    setStopNotice(null);
    const issues: StopNotice['issues'] = [];
    let tid = activeTaskId;
    if (!tid) {
      try { const a = await Bellomberg.consigliereActive(); if (a.active && a.task_id) tid = a.task_id; }
      catch (e: any) { issues.push({ source: '/consigliere/active', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) }); }
    }
    let terminal = false;
    try {
      if (tid) {
        const r = await Bellomberg.cancelConsigliere(tid);
        terminal = ['cancelled', 'completed', 'failed'].includes(r?.status);
        if (!terminal) issues.push({ source: '/consigliere/cancel/' + tid, detail: leggiDetail(r) || null });
      } else {
        const r = await Bellomberg.cancelAllConsigliere();
        terminal = r?.n_killed > 0 && Array.isArray(r.errors) && r.errors.length === 0;
        if (!terminal) issues.push({ source: '/consigliere/cancel_all', detail: leggiDetail(r?.errors?.length ? r.errors : r) || null });
      }
    } catch (e: any) {
      issues.push({ source: tid ? '/consigliere/cancel/' + tid : '/consigliere/cancel_all', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) });
    }
    // A failed cancellation must not erase the heartbeat or the last observed log.
    if (terminal && issues.length === 0) {
      try {
        const reset = await Bellomberg.resetAgentsLive();
        if (reset?.ok !== true) issues.push({ source: '/agents/live/reset', detail: leggiDetail(reset?.error || reset) || null });
      } catch (e: any) { issues.push({ source: '/agents/live/reset', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) }); }
    }
    let observedRunning: boolean | null = null;
    try {
      const observed = await Bellomberg.agentsLive();
      const unreadable = observed?.heartbeat === 'illeggibile'
        || (observed?.running === false && typeof observed.message === 'string' && observed.message.startsWith('read error'));
      if (!observed || unreadable || typeof observed.running !== 'boolean') {
        issues.push({ source: '/agents/live', detail: leggiDetail(observed?.message || observed) || null });
      } else {
        setState(observed); setHbIll(null); observedRunning = observed.running;
        if (!observed.running && issues.length === 0) {
          try { localStorage.removeItem(ACTIVE_RUN_KEY); } catch {}
          setActiveTaskId(null); setTriggerMsg(null); setElapsed(0);
        }
      }
    } catch (e: any) { issues.push({ source: '/agents/live', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) }); }
    setStopNotice({ issues, running: observedRunning });
    setStopping(false);
  };

  useEffect(() => {
    if (state && state.running === false && activeTaskId && !stopNotice?.issues.length) {
      try { localStorage.removeItem(ACTIVE_RUN_KEY); } catch {}
      setActiveTaskId(null);
    }
  }, [state?.running, activeTaskId, stopNotice]);


  /* ── presentazione (redesign 02/10/2026): quello che c'era sul quadrante sta
     ora nelle tappe, nelle card dei desk e nel pannello a schede. Hook nuovi in
     coda: i test i18n impostano lo stato per indice di useState. */
  const [scheda, setScheda] = useState<SchedaPannello>('chiamate');
  const [finitaQui, setFinitaQui] = useState(false);
  const eraViva = useRef<boolean | null>(null);
  useEffect(() => {
    if (state?.running == null) return;
    /* «completata» solo se la fine e' avvenuta sotto gli occhi del PM; aprire la
       pagina su una run gia' chiusa la mostra a riposo, con l'ultima run */
    if (eraViva.current === true && state.running === false) setFinitaQui(true);
    if (state.running === true) setFinitaQui(false);
    eraViva.current = state.running;
  }, [state?.running]);

  /* ── copertura filing (fase D, 03/10/2026): letta all'apertura e a fine run, quando
     cambiano le novita'; l'attivazione in blocco rilegge subito e dopo 15 s, il tempo
     dei primi confronti. Stato in coda: i test SSR seminano per indice. */
  const [filing, setFiling] = useState<FilingOverview | null>(null);
  const [filingErr, setFilingErr] = useState<ErroreFiling | null>(null);
  const [filingBusy, setFilingBusy] = useState(false);
  const [filingEsito, setFilingEsito] = useState<FilingActivateMissing | null>(null);
  const montata = useRef(true);
  useEffect(() => { montata.current = true; return () => { montata.current = false; }; }, []);
  const caricaFiling = async () => {
    try { const r = await Bellomberg.filingOverview(); if (montata.current) { setFiling(r); setFilingErr(null); } }
    catch (e: any) { if (montata.current) setFilingErr({ stato: statusHttp(e), msg: dettaglioLeggibile(e) }); }
  };
  useEffect(() => { caricaFiling(); }, [isRunning]);
  /* aggiornamento in corso (attivazione, controllo orario): rilettura ogni 10 s finche' dura */
  const filingCorre = aggiornamentoInCorso(filing);
  useEffect(() => pollFiling(filingCorre, () => { if (montata.current) caricaFiling(); }), [filingCorre]);
  const attivaFiling = async () => {
    setFilingBusy(true); setFilingEsito(null);
    try {
      const r = await Bellomberg.filingActivateMissing();
      if (!montata.current) return;
      setFilingEsito(r);
      await caricaFiling();
      if (r.attivati.length) setTimeout(() => { if (montata.current) caricaFiling(); }, 15000);
    } catch (e: any) { if (montata.current) setFilingErr({ stato: statusHttp(e), msg: dettaglioLeggibile(e) }); }
    finally { if (montata.current) setFilingBusy(false); }
  };

  /* ── costi e buchi ──────────────────────────────────────────────────── */
  const total = state?.usage_total;
  const notes = costNotes(total, state?.usage_by_specialist);
  const hasTotal = total?.cost_eur != null;
  const partial = hasTotal && isPartial(total);
  const koIds = total?.error_agents || [];
  // costo non dichiarato = n.d., mai 0 nella somma (08b S13, revisione G9b)
  const costoNoto = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  const koCostiNoti = koIds.map(id => state?.usage_by_specialist?.[id]?.cost_eur);
  const koCost = koCostiNoti.every(costoNoto) ? koCostiNoti.reduce((s, v) => s + (v as number), 0) : null;
  const costiAgenti = Object.values(state?.usage_by_specialist || {}).map(u => u.cost_eur);
  const agentiSenzaCosto = costiAgenti.filter(v => !costoNoto(v)).length;
  const sumAgents = costiAgenti.filter(costoNoto).reduce((s, v) => s + v, 0);
  const koVisibile = koCost == null ? koIds.length > 0 : koCost > 0;
  const tutti = [...P.desks, ...P.pending, ...P.stages];
  const lavoro = tutti.reduce((s, d) => s + (d.dur || 0), 0);
  /* il numero esiste SOLO quando e' calcolabile (N2): a run viva la somma delle
     durate e' un minimo che cala con l'orologio, non un parallelismo */
  const par = P.parStato === 'calcolato' && P.runSec > 0 && lavoro > 0 ? lavoro / P.runSec : null;

  /* heartbeat fermo (>600 s, stale_warning del backend) o illeggibile oltre la
     stessa soglia: «ragiona da X» sarebbe un'inferenza da uno stato che nessuno
     conferma piu' — si scrive il fatto: dichiarato, e fermo da quanto. */
  const hbIllDur = hbIll ? Math.max(0, (hbIll.al - hbIll.da) / 1000) : null;
  const hbIllLungo = hbIllDur != null && hbIllDur > 600;
  const fermoDa = isRunning && (P.stale || hbIllLungo) && state?.updated_at
    ? Math.max(0, (now - new Date(state.updated_at).getTime()) / 1000) : null;
  const hhmm = (iso?: string) => iso ? new Date(iso).toLocaleTimeString(localeDi(linguaCorrente()),
    { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : tr('activity.unavailable');
  const ora = (iso?: string) => iso ? new Date(iso).toLocaleTimeString(localeDi(linguaCorrente()),
    { hour: '2-digit', minute: '2-digit' }) : tr('activity.unavailable');
  const giorno = (iso?: string) => iso ? new Date(iso).toLocaleDateString(localeDi(linguaCorrente()),
    { day: 'numeric', month: 'short' }) : tr('activity.unavailable');

  /* ── i desk del comitato: in ordine di roster, cosi' le card non saltano ── */
  const isStage = (id: string) => id === 'capo' || id.startsWith('_');
  const byId = (id: string) => tutti.find(x => x.id === id);
  const ordine = new Map(agents.map((a, i) => [a.id, i]));
  const deskDellaRun = [...P.desks, ...P.pending].filter(d => !isStage(d.id));
  const deskElenco: Desk[] = (deskDellaRun.length ? deskDellaRun : agents.filter(a => !isStage(a.id)).map(a => ({
    id: a.id, name: a.name, role: a.role, color: a.color, onGrid: true, dur: null, apiCalls: 0, cost: null,
    tin: null, tout: null, cacheR: null, cacheW: null, nCalls: 0, nTools: 0, rounds: [], obs0: null, obs1: null,
  }))).slice().sort((a, b) => (ordine.get(a.id) ?? 999) - (ordine.get(b.id) ?? 999) || a.name.localeCompare(b.name));
  const rosterDi = (id: string) => agents.find(a => a.id === id);
  const nomeDi = (d: Pick<Desk, 'id' | 'name'>) => frase(rosterDi(d.id)?.name || d.name);
  const coloreDi = (id: string) => rosterDi(id)?.color || byId(id)?.color || null;
  const nDesk = deskElenco.length;
  const r2 = state?.r2_specialists;
  const pulisci = (input: string) => input === '{}' ? '' : input.replace(/[{}']/g, '').trim();

  return (
    <>
    <ModernPage page="agents" render={() => {
      const w = parole();
      const vivo = P.live;
      const nonInR2 = (id: string) => !!r2 && !r2.includes(id) && (!vivo || (P.round ?? 0) >= 2);

      const desks: VistaDesk[] = deskElenco.map(d => {
        const v = verdictOf(d);
        const corre = vivo && (d.statusRun === 'running' || P.running.includes(d.id));
        const mie = P.calls.filter(c => c.a === d.id);
        const ultima = mie[mie.length - 1];
        const fa = ultima ? Math.max(0, P.runSec - ultima.t) : null;
        const stato: StatoDesk = v.cls === 'ko' ? 'ko'
          : v.k === 'n.d.' ? 'nd'
          : corre ? (fermoDa != null ? 'stale' : ultima && (P.round == null || ultima.r === P.round) && fa != null && fa <= 20 ? 'run' : 'think')
          : d.statusRun === 'done' ? 'ok'
          : 'wait';
        const giri = [d.rounds.length ? w.rounds(d.rounds.length) : null, nonInR2(d.id) ? w.notInR2 : null,
          ultima ? w.at(ultima.hhmm.slice(0, 5)) : null].filter(Boolean).join(' · ');
        const fare: VistaDesk['fare'] =
          stato === 'run' && ultima ? { icona: 'tool', codice: true, testo: ultima.tool, sotto: w.doingAgo(pulisci(ultima.input), fmtDurShort(fa)) }
          : stato === 'think' ? (ultima && (P.round == null || ultima.r === P.round)
              ? { icona: 'think', codice: false, testo: w.thinkingSince(fmtDurShort(fa)), sotto: w.lastTool(ultima.tool) + (pulisci(ultima.input) ? ' · ' + pulisci(ultima.input) : '') }
              : { icona: 'think', codice: false, testo: w.noCallYet, sotto: w.noCallYetSub })
          : stato === 'stale' ? { icona: 'pause', codice: false, testo: w.staleDoing(fmtDurShort(fermoDa)), sotto: ultima ? w.lastTool(ultima.tool) : '' }
          : stato === 'ko' ? { icona: 'ko', codice: false, testo: w.koTitle, sotto: v.t }
          : stato === 'nd' ? { icona: 'file', codice: false, testo: d.statusRun === 'done' ? w.delivered : frase(v.k), sotto: v.t }
          : stato === 'ok' ? { icona: 'file', codice: false,
              testo: vivo && P.round != null && !nonInR2(d.id) ? w.deliveredRound(P.round) : w.delivered, sotto: giri }
          : { icona: 'wait', codice: false, testo: vivo ? w.waitingLive : w.waitingIdle, sotto: state ? (vivo ? '' : v.t) : '' };
        const piuAlto = d.rounds.length ? Math.max(...d.rounds) : -1;
        const round = [0, 1, 2].map((r): StatoRound => {
          if (r === 2 && nonInR2(d.id)) return 'off';
          if (stato === 'ko' && r === piuAlto) return 'ko';
          if (corre && r === P.round) return 'on';
          if (d.rounds.includes(r) || (vivo && P.round != null && r < P.round) || (!vivo && d.statusRun === 'done' && r <= piuAlto)) return 'ok';
          return '';
        });
        return {
          id: d.id, nome: nomeDi(d), ruolo: rosterDi(d.id)?.role || d.role, colore: coloreDi(d.id) || d.color, stato,
          pastiglia: { run: w.stWorking, think: w.stThinking, ok: w.stDone, ko: w.stKo, nd: w.stNd, wait: w.stWaiting, stale: w.stStale }[stato],
          titolo: v.t, fare, round, esito: v.k,
          dur: fmtDurShort(d.dur), chiamate: w.calls(d.nCalls),
          costo: d.cost == null ? tr('activity.unavailable') : (d.partial ? '~' : '') + fmtEur(d.cost), costoNd: d.cost == null,
          dettaglio: w.deskDetail(d.nTools, d.apiCalls, engineShort(d.id, engines, d.rounds)),
        };
      });
      const consegnati = deskElenco.filter(d => d.statusRun === 'done').length;

      /* ── il Capo ───────────────────────────────────────────────────── */
      const capoD = byId('capo');
      const capoV = capoD ? verdictOf(capoD) : null;
      const memoId = state?.memo_id ?? null;
      const chiusaConRun = !vivo && !!state?.start_time;
      const capoStato: StatoDesk = capoV?.cls === 'ko' ? 'ko'
        : vivo && P.capo === 'running' ? (fermoDa != null ? 'stale' : 'run')
        : chiusaConRun && (memoId != null || P.capo === 'done') ? 'ok'
        : 'wait';
      const vaiDecisioni = () => navigate('/decisions');
      /* la riga della run porta alla scheda Filing; sul 49" la card sta nella colonna delle chiamate */
      const apriFiling = () => {
        setScheda('filing');
        requestAnimationFrame(() => {
          const dove = [...document.querySelectorAll<HTMLElement>('.bbn-agents :is(.ag-panel, .ag-filing-wide)')].find(el => el.offsetParent !== null);
          dove?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        });
      };
      const capo: VistaCapo = {
        stato: capoStato, colore: coloreDi('capo'),
        titolo: capoStato === 'ko' ? w.koTitle
          : capoStato === 'run' || capoStato === 'stale' ? (P.capoT != null ? w.capoWriting(fmtDurShort(Math.max(0, P.runSec - P.capoT))) : w.capoWritingNoTime)
          : capoStato === 'ok' ? (memoId != null ? w.capoSaved(memoId) : w.pillDone)
          : chiusaConRun ? w.capoNoMemo
          : vivo ? w.capoWaiting : w.capoIdle,
        sotto: capoStato === 'ko' ? capoV!.t
          : capoStato === 'stale' ? w.capoWritingStale(fmtDurShort(fermoDa))
          : capoStato === 'run' ? (state?.updated_at ? w.capoWritingSub(ora(state.updated_at)) : tr('activity.capoStartMissing'))
          : capoStato === 'ok' ? w.capoSavedSub(fmtDurShort(capoD?.dur), ora(state?.completed_at))
          : chiusaConRun ? w.capoNoMemoSub
          : vivo ? w.capoWaitingSub(consegnati, nDesk) : '',
        pastiglia: capoStato === 'ko' ? w.stKo : capoStato === 'run' || capoStato === 'stale' ? w.capoPillWriting
          : capoStato === 'ok' ? w.capoPillDone : w.capoPillWaiting,
        stadi: ([['_red_team', w.stageRedTeam], ['_reflection', w.stageReflection], ['_action_table', w.stageActions]] as const).map(([id, nome]) => {
          const s = byId(id);
          return s?.dur != null ? { id, nome, stato: 'ok' as const, nota: fmtDurShort(s.dur) }
            : { id, nome, stato: 'wait' as const, nota: chiusaConRun && s ? w.stageNoDuration : w.stageAfter };
        }),
        azioni: capoStato === 'ok' && memoId != null ? <>
          <button type="button" className="bbn-btn is-primary is-sm" onClick={vaiDecisioni}><FileText size={15} />{w.openDecisions}</button>
          <button type="button" className="bbn-btn is-sm" onClick={() => navigate('/memos')}>{w.memoArchive}</button>
        </> : undefined,
      };

      /* ── le tappe ──────────────────────────────────────────────────── */
      const fase = (k: string) => P.phases.find(p => p.k === k);
      const arco = (p: { t0: number; t1: number }) => `${fmtDurShort(p.t0)} – ${fmtDurShort(p.t1)}`;
      const capoScrive = vivo && P.capo === 'running';
      const tappe: Tappa[] = [{ id: 'avvio', nome: w.stepStart,
        stato: state?.start_time ? 'done' : 'wait', sotto: state?.start_time ? ora(state.start_time) : w.stepWaiting }];
      for (const r of [0, 1, 2]) {
        const p = fase('R' + r);
        let t: Tappa = { id: 'r' + r, nome: w.stepRound(r), stato: 'wait', sotto: w.stepWaiting };
        if (vivo && capoScrive) t = p ? { ...t, stato: 'done', sotto: arco(p) } : { ...t, stato: P.round != null && r <= P.round ? 'done' : 'skip', sotto: P.round != null && r <= P.round ? w.stepDone : w.stepSkipped };
        else if (vivo && P.round != null) {
          if (r < P.round) t = { ...t, stato: 'done', sotto: p ? arco(p) : w.stepDone };
          else if (r === P.round) t = { ...t, stato: 'now', sotto: fermoDa != null ? w.stepStale(fmtDurShort(fermoDa)) : p ? w.stepNow(fmtDurShort(Math.max(0, P.runSec - p.t0))) : w.stepNowShort };
        } else if (vivo) {
          if (p) t = { ...t, stato: p.open ? 'now' : 'done', sotto: p.open ? w.stepNow(fmtDurShort(Math.max(0, P.runSec - p.t0))) : arco(p) };
        } else if (p) t = { ...t, stato: 'done', sotto: arco(p) };
        else if (state?.start_time) t = { ...t, stato: 'skip', sotto: w.stepSkipped };
        tappe.push(t);
      }
      const sintesi = fase('SINTESI');
      tappe.push(capoScrive
        ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'now', sotto: fermoDa != null ? w.stepStale(fmtDurShort(fermoDa))
            : P.capoT != null ? w.stepNow(fmtDurShort(Math.max(0, P.runSec - P.capoT))) : w.stepNowShort }
        : !vivo && sintesi ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'done', sotto: arco(sintesi) }
        : chiusaConRun && (memoId != null || P.capo === 'done') ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'done', sotto: w.stepDone }
        : chiusaConRun ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'skip', sotto: w.stepSkipped }
        : { id: 'sintesi', nome: w.stepSynthesis, stato: 'wait', sotto: w.stepWaiting });
      tappe.push(chiusaConRun && memoId != null
        ? { id: 'memo', nome: w.stepMemo, stato: 'done', sotto: `#${memoId} · ${ora(state?.completed_at)}` }
        : chiusaConRun ? { id: 'memo', nome: w.stepMemo, stato: 'skip', sotto: w.stepNoMemo }
        : { id: 'memo', nome: w.stepMemo, stato: 'wait', sotto: w.stepWaiting });

      /* ── la frase del presente (solo a run viva) ───────────────────── */
      const fraRound = P.phases.find(p => p.k === 'FRA ROUND' && p.open);
      const adesso: [string, string?] | null = !vivo ? null
        : fermoDa != null ? [w.nowStale(fmtDurShort(fermoDa)), w.nowStaleSub(hhmm(state?.updated_at))]
        : capoScrive ? [w.nowSynthesis, w.nowCapo]
        : P.round != null && P.running.length ? [w.nowRound(P.round), w.nowWorking(P.running.length, nDesk, consegnati)]
        : fraRound ? [w.nowBetween]
        : !P.calls.length ? [w.nowStarting]
        : P.round != null ? [w.nowRound(P.round), w.nowWorking(P.running.length, nDesk, consegnati)] : null;

      /* ── cifre del box ─────────────────────────────────────────────── */
      const chiamateTot = P.logTappato && P.nCallsTot == null ? null : (P.nCallsTot ?? P.calls.length);
      const costoTesto = (partial ? '~' : '') + fmtEur(total?.cost_eur);
      const costoTono = !hasTotal || partial || notes.length ? 'warn' as const : undefined;
      const kDurata = <Kpi etichetta={w.kDuration} valore={fmtDurShort(isRunning ? (elapsed || P.runSec) : P.runSec)}
        sotto={isRunning ? w.sClock : lavoro > 0 ? w.sWork(fmtDurShort(lavoro)) : tr('activity.durationMissing')} />;
      const kCosto = <Kpi etichetta={isRunning ? w.kCostSoFar : w.kCost} valore={costoTesto} nd={!hasTotal}
        sotto={!hasTotal ? w.sUnpriced : isRunning ? (partial ? w.sSoFarPartial : w.sSoFar)
          : koVisibile ? w.sKoSpent(fmtEur(koCost)) : partial ? w.sPartial : w.sComplete}
        tono={koVisibile && !isRunning ? 'warn' : costoTono} />;
      const kChiamate = <Kpi etichetta={w.kCalls} valore={chiamateTot ?? tr('activity.unavailable')} nd={chiamateTot == null}
        sotto={P.logTappato ? w.sLast50 : isRunning ? w.sDistinctTools(P.nToolsDistinct) : w.sTickers(P.tickers.length)} />;
      const kQuarta = par != null
        ? <Kpi etichetta={w.kParallelism} valore={fmtN(par, 2) + '×'} sotto={w.sTogether} />
        : <Kpi etichetta={w.kTickers} valore={P.tickers.length} sotto={isRunning ? w.sTickersSoFar : w.sDistinctTools(P.nToolsDistinct)} />;
      const kEsito = <Kpi etichetta={w.kOutcome} valore={koIds.length ? w.outcomeKo(koIds.length) : w.outcomeOk}
        sotto={koIds.length ? koIds.map(id => nomeDi({ id, name: id })).join(', ') : undefined} tono={koIds.length ? 'bad' : undefined} />;

      const cieco = !state || !!liveErr;
      const pillola = !state && !liveErr && !hbIll ? <span className="bbn-pill is-piatto"><i className="ag-dot" />{w.pillQuerying}</span>
        : cieco ? <span className="bbn-pill is-giu"><WifiOff size={13} />{w.pillUnreadable}</span>
        : isRunning ? <span className={'bbn-pill ' + (fermoDa != null ? 'is-warn' : 'is-acc')}><i className={fermoDa != null ? 'ag-dot' : 'ag-pulse'} />{fermoDa != null ? w.pillStuck : w.pillRunning}</span>
        : finitaQui ? <span className="bbn-pill is-su"><i className="ag-dot" />{memoId != null ? w.pillDone : w.pillEnded}</span>
        : <span className="bbn-pill is-piatto"><i className="ag-dot" />{w.pillIdle}</span>;
      const lingua = state?.language === 'it' ? tr('activity.originalRunIt') : state?.language === 'en' ? tr('activity.originalRunEn') : null;
      const meta = isRunning && state?.start_time ? [w.startedAt(ora(state.start_time)), lingua].filter(Boolean).join(' · ')
        : finitaQui && state?.start_time ? [w.runSpan(giorno(state.start_time), ora(state.start_time), ora(state.completed_at)), lingua].filter(Boolean).join(' · ')
        : '';
      const vista: 'cieco' | 'viva' | 'finita' | 'riposo' = cieco ? 'cieco' : isRunning ? 'viva' : finitaQui ? 'finita' : 'riposo';
      const titolo = vista === 'cieco' ? (state || liveErr || hbIll ? w.titleUnreadable : w.titleIdle)
        : vista === 'viva' ? (capoScrive ? w.titleSynthesis : w.titleLive)
        : vista === 'finita' ? (memoId != null ? w.titleDone : w.titleDoneNoMemo)
        : w.titleIdle;
      const descrizione = vista === 'cieco' ? (state || liveErr || hbIll ? w.descUnreadable(state?.updated_at ? hhmm(state.updated_at) : null) : w.descIdle(nDesk))
        : vista === 'viva' ? w.descLive
        : vista === 'finita' ? (memoId != null ? w.descDone(nDesk, P.phases.filter(p => /^R\d/.test(p.k)).length) : w.descDoneNoMemo)
        : w.descIdle(nDesk);
      const bottone = isRunning
        ? <button type="button" className="bbn-btn ag-stop" onClick={stopRun} disabled={stopping}
            title={activeTaskId ? tr('activity.stopTask', { a: activeTaskId }) : tr('activity.noTrackedTask')}>
            <Square size={15} />{stopping ? w.stopping : w.stop}</button>
        : vista === 'finita' && memoId != null
          ? <><button type="button" className="bbn-btn is-primary" onClick={vaiDecisioni}><FileText size={16} />{w.openDecisions}</button>
              <button type="button" className="bbn-btn ag-launch" onClick={() => setAskRun(true)}><Play size={15} />{w.newRun}</button></>
          : <button type="button" className="bbn-btn is-primary ag-launch" onClick={() => setAskRun(true)}><Play size={15} />{w.launch}</button>;
      const ultimaRun = vista === 'riposo' && state?.start_time
        ? <div className="ag-last">
            <span className="t">{w.lastRun} <span>· {w.runSpan(giorno(state.start_time), ora(state.start_time), state.completed_at ? ora(state.completed_at) : tr('activity.unavailable'))}{lingua ? ' · ' + lingua : ''}</span></span>
            <span className="bbn-grow" />
            <div className="ag-kpis">{kDurata}{kCosto}{kChiamate}{kQuarta}{kEsito}</div>
            {memoId != null && <button type="button" className="bbn-link ag-memo-link" onClick={vaiDecisioni}>{w.memo(memoId)} <ArrowUpRight size={14} /></button>}
          </div>
        : null;

      /* ── avvisi: i buchi in vetrina, non nei tooltip (regola PM 14/07) ── */
      const avvisi: { k: string; tono: 'bad' | 'warn' | 'good' | 'info'; icona: ReactNode; testo: ReactNode; azione?: ReactNode }[] = [];
      if (vista === 'finita') avvisi.push({ k: 'finita', tono: 'good', icona: <Check size={18} />,
        testo: <b>{w.done(ora(state?.completed_at), memoId)}</b>,
        azione: memoId != null ? <button type="button" className="bbn-link" onClick={vaiDecisioni}>{w.openDecisions} <ArrowUpRight size={14} /></button> : undefined });
      if (triggerMsg) avvisi.push({ k: 'avvio', tono: 'info', icona: <Play size={18} />,
        testo: frase(triggerMsg.kind === 'starting' ? tr('activity.starting') : tr('activity.runActiveEstimate', { a: triggerMsg.id ?? tr('activity.unavailable') })) });
      if (stopNotice) avvisi.push({ k: 'stop', tono: stopNotice.running === false && !stopNotice.issues.length ? 'good' : 'bad', icona: <Square size={18} />,
        testo: <><b>{tr(stopNotice.running === false ? 'activity.stopObservedIdle' : 'activity.stopUnconfirmed')}</b>
          {stopNotice.issues.map((issue, i) => <span key={i} className="ag-ban-line">{issue.source} — {issue.detail || tr('activity.errorNotDescribed')}</span>)}</> });
      if (liveErr) avvisi.push({ k: 'backend', tono: 'bad', icona: <WifiOff size={18} />,
        testo: <><b>{w.backendDown}</b> {w.backendDownSub(liveErr.replace(/[.\s]+$/, ''), state?.updated_at ? hhmm(state.updated_at) : null)}</> });
      if (isRunning && (state?.stale_warning || hbIllLungo)) avvisi.push({ k: 'fermo', tono: 'bad', icona: <TriangleAlert size={18} />,
        /* review 31/08: a poll illeggibile `stale_seconds` resta CONGELATO all'ultimo
           payload buono mentre l'orologio avanza — vince la misura che cresce */
        testo: <><b>{w.stuck}</b> {w.stuckSub(Math.max(1, Math.floor(Math.max(state?.stale_seconds || 0, fermoDa || 0) / 60)))}
          {/* mentre il Capo genera il memo nessuno scrive l'heartbeat: un consiglio di FERMARE senza dirlo costerebbe il memo */}
          {P.capo === 'running' && <> {tr('activity.capoHeartbeatNote')}</>}</> });
      if (hbIll) avvisi.push({ k: 'illeggibile', tono: 'warn', icona: <TriangleAlert size={18} />,
        testo: <><b>{frase(tr('activity.heartbeatUnreadableUpper') + (hbIllDur != null && hbIllDur >= 3 ? tr('activity.unreadableSince', { a: fmtDurShort(hbIllDur) }) : tr('activity.unreadableNow')))}.</b>{' '}
          {maiuscola(tr('activity.unreadableMessagePrefix'))}{hbIll.msg ?? tr('activity.unreadableHeartbeat')}{tr('activity.unreadableMessageSuffix')}{state
            ? <>{tr('activity.lastGoodState')}{state.updated_at ? tr('activity.heartbeatTime', { a: hhmm(state.updated_at) }) : ''}</>
            : <>{tr('activity.noneSinceOpen')}</>} {tr('activity.retryCadence')}</> });
      if (koIds.length > 0) avvisi.push({ k: 'ko', tono: 'bad', icona: <CircleAlert size={18} />,
        testo: <><b>{w.koBanner(koIds.length, nDesk || koIds.length)}</b>{' '}
          <span>{koIds.map(id => nomeDi({ id, name: id })).join(tr('movements.and'))} {tr(koIds.length === 1 ? 'activity.declareOne' : 'activity.declare')} <b>status api_error</b> {tr(koIds.length === 1 ? 'activity.reportAnywayOne' : 'activity.reportAnyway')}</span>.{' '}
          <span>{maiuscola(tr('activity.withinTotalPrefix'))} {fmtEur(total?.cost_eur)} {tr('activity.include')} <b>{fmtEur(koCost)}</b>
            {total?.cost_eur && koCost != null ? ` (${fmtN(koCost / total.cost_eur * 100, 0)}%)` : ''} {tr(koIds.length === 1 ? 'activity.spentByThemOne' : 'activity.spentByThem')}</span>.</> });
      /* «totale parziale» lo dice il titolo del banner: la nota del catalogo non si ripete */
      const altreNote = partial ? notes.filter(n => n !== tr('activity.partialTotal')) : notes;
      if (notes.length > 0) avvisi.push({ k: 'costi', tono: 'warn', icona: <TriangleAlert size={18} />,
        testo: <>{partial && <><b>{w.partialBanner}</b> </>}{altreNote.map(maiuscola).join(' — ')}</> });
      if (rosterErr) avvisi.push({ k: 'roster', tono: 'bad', icona: <CircleAlert size={18} />,
        testo: <><b>{frase(tr('activity.rosterMissing'))}.</b> {tr('activity.rosterErrorPrefix')}{rosterErr}{tr('activity.rosterErrorSuffix')}</> });

      /* ── pannello: chiamate, strumenti, ticker (i costi stanno sotto i desk) ── */
      const conteggioChiamate = P.logTappato ? w.last50Of(P.calls.length, P.nCallsTot) : w.allN(P.calls.length);
      const corpoChiamate = P.calls.length === 0 ? <p className="bbn-empty">{w.noCalls}</p> : (
        <div className="bbn-scroll ag-tape">
          <div className="ag-day">{isRunning ? w.callsNow : w.callsNewestFirst}</div>
          {[...P.calls].reverse().map((c, i) => (
            <div key={i} className={'ag-call' + (isRunning && P.runSec - c.t < 20 ? ' is-hot' : '')} title={tr('activity.wallClock', { a: c.hhmm, b: c.input })}>
              <IconaDesk id={c.a} colore={coloreDi(c.a)} dimensione="xs" />
              <span className="w"><code>{c.tool}</code><span>{nomeDi({ id: c.a, name: c.a })}{pulisci(c.input) ? ' · ' + pulisci(c.input) : ''}</span></span>
              <span className="t num">{fmtDurShort(c.t)}<span className="rd">R{c.r}</span></span>
            </div>
          ))}
        </div>
      );
      const righeToken: [string, number | null | undefined, string, string][] = [
        [maiuscola(tr('activity.freshInput')), total?.in, 'var(--bbn-accent)', 'input'],
        [maiuscola(tr('activity.output')), total?.out, 'var(--bbn-good)', 'output'],
        [maiuscola(tr('activity.cacheRead')), total?.cache_read, 'var(--ag-cache-read)', 'cache-read'],
        [maiuscola(tr('activity.cacheWrite')), total?.cache_write, 'var(--ag-cache-write)', 'cache-write'],
      ];
      const mxTok = Math.max(1, ...righeToken.map(r => r[1] || 0));
      const agentiCosto = [...deskElenco, ...P.stages.filter(s => s.id === 'capo')];
      const mxCosto = Math.max(0.0001, ...agentiCosto.map(a => a.cost || 0));
      const fx = total?.fx_source == null || total.fx_source === 'n.d.' ? tr('activity.unavailable') : total.fx_source;
      const corpoCosti = !total ? <p className="bbn-empty">{tr('activity.heartbeatMissing')} <b>usage_total</b>{tr('activity.runCostsMissing')}</p> : (
        <div className="ag-costs">
          <div className="ag-cost-tot"><div className="ag-big"><span className={'num' + (hasTotal ? '' : ' is-nd')}>{costoTesto}</span>
            {hasTotal && <span className={'bbn-pill ' + (partial ? 'is-warn' : 'is-su')}>{partial ? w.pillPartial : w.pillComplete}</span>}
            <span className="fx">{w.fx} <b className={total.fx_source === 'live' ? 'is-live' : 'is-warn'}>{fx}</b></span></div>
          <p className="ag-foot">
            {agentiSenzaCosto > 0 ? w.sumMissing(fmtEur(sumAgents), agentiSenzaCosto)
              : total.cost_eur != null && Math.abs(sumAgents - total.cost_eur) < 0.005 ? w.sumEqual(fmtEur(sumAgents)) : w.sumDiff(fmtEur(sumAgents), fmtEur(total.cost_eur))}
            {koIds.length > 0 && <> <span className="is-ko">{w.koInside(fmtEur(koCost), koIds.length)}</span></>}
            <br />{w.engines(engineShort(deskElenco[0]?.id || 'macro', engines, deskElenco[0]?.rounds), engineShort('capo', engines))}
          </p></div>
          <div className="ag-cost-tok"><div className="ag-sub">{w.tokens}</div>
            <div className="ag-bars">{righeToken.map(([k, v, c, kind]) => (
              <div className="ag-barrow" key={kind} data-metric={kind}><span className="k">{k}</span><Barra quota={(v || 0) / mxTok} colore={c} />
                <span className="v num" title={v != null ? tr(v === 1 ? 'activity.tokenCountOne' : 'communications.tokenCount', { a: fmtN(v) }) : tr('activity.notDeclared')}>{fmtTok(v)}</span></div>
            ))}</div>
            <p className="ag-foot">{total.cache_read && total.in ? w.cacheNote(fmtN(total.cache_read / total.in, 1)) : tr('activity.cacheMissingSentence')}</p></div>
          {agentiCosto.length > 0 && <div className="ag-cost-agt"><div className="ag-sub">{w.costPerAgent}</div>
            <div className="ag-bars" style={{ '--ag-righe': Math.ceil(agentiCosto.length / 2) } as CSSProperties}>{agentiCosto.map(a => {
              const ko = koIds.includes(a.id);
              const valore = a.cost != null ? (a.partial ? '~' : '') + fmtEur(a.cost) : a.id === 'capo' && P.capo === 'running' ? w.inProgressCost : tr('activity.unavailable');
              return <div className="ag-agc" key={a.id} title={tr('activity.agentTokenBreakdown', { a: nomeDi(a), b: fmtTok(a.tin), c: fmtTok(a.tout), d: fmtTok(a.cacheR), e: fmtTok(a.cacheW), f: [a.tin, a.tout, a.cacheR, a.cacheW].every(v => v != null) ? '' : tr('activity.partialTokens') })}>
                <IconaDesk id={a.id} colore={coloreDi(a.id) || a.color} dimensione="xs" /><span className="k">{nomeDi(a)}</span>
                <Barra quota={(a.cost || 0) / mxCosto} colore={ko ? 'var(--bbn-bad)' : 'var(--bbn-accent)'} />
                <span className={'v num' + (ko ? ' is-ko' : '') + (a.cost == null ? ' is-nd' : '')}>{valore}</span></div>;
            })}</div></div>}
        </div>
      );
      const deskDiStrumento = (id: string) => deskElenco.find(d => d.id === id);
      const mxTool = P.tools[0]?.n || 1;
      const corpoStrumenti = P.tools.length === 0 ? <p className="bbn-empty">{tr('activity.theLog')} <b>tool_log</b> {tr('activity.heartbeatLogEmpty')}</p> : (
        <div className="bbn-scroll ag-tools">
          <div className="ag-legend">{deskElenco.filter(d => d.nCalls > 0).map(d => <span key={d.id}><i style={{ background: coloreDesk(coloreDi(d.id) || d.color) }} />{nomeDi(d)}</span>)}</div>
          {P.tools.map(t => (
            <div className="ag-tool" key={t.tool} title={Object.entries(t.by).map(([a, n]) => `${a} ${n}`).join(' · ')}>
              <code>{t.tool}</code><span className="n num">{t.n}</span>
              <span className="ag-bar">{Object.entries(t.by).sort((a, b) => b[1] - a[1]).map(([a, n]) =>
                <u key={a} style={{ width: `${n / mxTool * 100}%`, background: coloreDesk(coloreDi(a) || deskDiStrumento(a)?.color) }} />)}</span>
            </div>
          ))}
          <p className="ag-foot">{P.logTappato ? w.toolsFootLive(P.calls.length, P.nCallsTot) : w.toolsFoot(P.nToolsDistinct, P.calls.length)}</p>
        </div>
      );
      const corpoTicker = P.tickers.length === 0 ? <p className="bbn-empty">{w.noTickers}</p> : (
        <div className="bbn-scroll ag-tickers">{P.tickers.map(x => <span key={x.k} className="ag-tk">{x.k}<b className="num">{x.n}</b></span>)}</div>
      );
      const filingVista = (conTitolo: boolean) => <CoperturaFiling dati={filing} errore={filingErr} occupato={filingBusy} bloccato={isRunning}
        esito={filingEsito} onAttiva={attivaFiling} onRiprova={caricaFiling} fmt={n => fmtN(n)} conTitolo={conTitolo}
        onApri={t => navigate(t ? `/filing?t=${encodeURIComponent(t)}` : '/filing')} />;
      const corpoFiling = <div className="bbn-scroll ag-filing-tab">{filingVista(true)}</div>;
      const corpi: Record<SchedaPannello, ReactNode> = { chiamate: corpoChiamate, strumenti: corpoStrumenti, ticker: corpoTicker, filing: corpoFiling };
      const conteggi: Record<SchedaPannello, string> = {
        chiamate: conteggioChiamate, strumenti: P.logTappato ? w.last50Of(P.calls.length, P.nCallsTot) : w.allN(P.nToolsDistinct),
        ticker: w.allN(P.tickers.length),
        filing: filing ? w.filingCount(filing.copertura.con_confronto, filing.copertura.totale) : '',
      };

      return (
        <div className="bbn-agents bbn-font" data-vista={vista} data-heartbeat={P.stale ? 'fermo' : 'ok'}>
          {/* ── box della run ── */}
          <section className="bbn-card ag-run" aria-live="polite">
            <div className="ag-run-top">
              <div className="ag-run-id">
                <AnelloDesk id="capo" colore={coloreDi('capo')} grande
                  stato={vista === 'viva' ? (fermoDa != null ? 'stale' : 'run') : vista === 'finita' && memoId != null ? 'ok' : 'wait'} />
                <div className="ag-run-copy">
                  <span className="k">{pillola}{meta && <span className="meta">{meta}</span>}</span>
                  <h1>{titolo}</h1>
                  <p>{descrizione}</p>
                  <p className="ag-filing-line" data-filing-line={filingErr ? 'errore' : filing ? (mancantiFiling(filing) ? 'mancanti' : 'completa') : 'caricamento'}>
                    <FileText size={14} />
                    <span>{filingErr ? w.filingLineDown : filing ? w.filingLine(filing.copertura.con_confronto, filing.copertura.totale, mancantiFiling(filing)) : w.filingLineLoading}</span>
                    <button type="button" className="bbn-link" data-filing-open="1" onClick={apriFiling}>{w.filingOpen} <ArrowUpRight size={14} /></button>
                  </p>
                </div>
              </div>
              {vista !== 'riposo' && <div className="ag-kpis">
                {vista === 'cieco' && !state ? null : <>{kDurata}{kCosto}{kChiamate}{kQuarta}</>}
              </div>}
              <div className="ag-run-act">
                <button type="button" className="bbn-btn" onClick={() => navigate('/agents/trade-idea')}><Lightbulb size={15} />{tr('tradeidea.openTradeIdea')}</button>
                {bottone}
              </div>
            </div>
            {ultimaRun}
            <Tappe tappe={tappe} etichetta={w.steps} />
            {adesso && <div className="ag-now"><span><b>{adesso[0]}</b>{adesso[1] ? ' · ' + adesso[1] : ''}</span>
              {capoScrive && fermoDa == null && <span className="muted">· {w.nowCapoNote}</span>}</div>}
            {/* ── recupero delle run settimanali (Trade Idea): stesso stato della run qui sopra ── */}
            <WeeklyRecoveryPanel disabled={isRunning} onStarted={tid => {
              try { localStorage.setItem(ACTIVE_RUN_KEY, tid); } catch {}
              setActiveTaskId(tid); setTriggerMsg({ kind: 'active', id: tid }); setStopNotice(null);
              setState(prev => ({ ...(prev || {}), running: true, start_time: new Date().toISOString(),
                message: tr('tradeidea.recoveryBusy') } as AgentsLiveState));
            }} />
          </section>

          {avvisi.length > 0 && <div className="ag-bans">
            {avvisi.map(a => <div key={a.k} className={'ag-ban is-' + a.tono} data-avviso={a.k} role={a.tono === 'bad' ? 'alert' : 'status'}>
              {a.icona}<span>{a.testo}</span>{a.azione && <span className="act">{a.azione}</span>}</div>)}
          </div>}

          {/* ── desk, Capo, costi (e sul 49" dove ha guardato) ── */}
          <div className="ag-desks" ref={dialRef} aria-label={w.desks}>
            <div className="ag-dgrid">{desks.map(d => <CardDesk key={d.id} d={d} />)}</div>
            <CardCapo c={capo} nome={w.capo} />
            <div className="ag-wide">
              <section className="bbn-card ag-panel-card ag-cost-card"><header className="bbn-card-head"><h2>{w.costsTitle}</h2></header>{corpoCosti}</section>
              <section className="bbn-card ag-panel-card ag-looked-card"><header className="bbn-card-head"><h2>{w.lookedTitle}</h2>
                <span className="bbn-card-count">{w.lookedCount(P.nToolsDistinct, P.tickers.length)}</span></header>
                <div className="ag-looked">{corpoStrumenti}{corpoTicker}</div></section>
            </div>
          </div>

          {/* ── pannello a schede (a destra); sul 49" solo Chiamate ── */}
          <section className="bbn-card ag-panel" aria-label={w.panel}>
            <header className="bbn-card-head">
              <Segmenti<SchedaPannello> valore={scheda} etichetta={w.panel} onChange={setScheda} opzioni={[
                { id: 'chiamate', testo: w.tabCalls },
                { id: 'strumenti', testo: w.tabTools }, { id: 'ticker', testo: w.tabTickers },
                { id: 'filing', testo: w.tabFiling },
              ]} />
              <span className="bbn-grow" />
              {conteggi[scheda] && <span className="bbn-card-note">{conteggi[scheda]}</span>}
            </header>
            {corpi[scheda]}
          </section>
          <section className="bbn-card ag-calls-wide" aria-label={w.callsTitle}>
            <header className="bbn-card-head"><h2>{w.callsTitle}</h2><span className="bbn-card-count">{conteggioChiamate}</span></header>
            {corpoChiamate}
          </section>
          <section className="bbn-card ag-filing-wide" aria-label={w.filingTitle}>
            <header className="bbn-card-head"><h2>{w.filingTitle}</h2>
              {filing && <span className="bbn-card-count">{w.filingCount(filing.copertura.con_confronto, filing.copertura.totale)}</span>}</header>
            {filingVista(false)}
          </section>
        </div>
      );
    }} />
    <RunConfirmDialog open={askRun} pagePresentation
      onConfirm={() => { setAskRun(false); triggerRun(); }}
      onCancel={() => setAskRun(false)} />
    </>
  );
}
