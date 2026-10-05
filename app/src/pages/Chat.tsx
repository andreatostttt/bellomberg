import { useT } from '@/i18n/provider';
import { localizePayload } from '@/lib/api-presentation';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import { fmtNum } from '@/lib/format';
import { frase } from '@/lib/frase';
import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import Card, { Segmenti } from '@/components/nuova/Card';
import {
  Bellomberg, AgentInfo, EnginesInfo, ChatSession, ChatMessage, MandatoMeta,
  requestHeaders, clearSessionAndReload,
} from '@/lib/api';
import { Send, Plus, Trash2, Square, Search, Wrench, PanelRightOpen, X, Lightbulb } from 'lucide-react';
import ConfirmDialog from '@/components/ConfirmDialog';
import ChatSuggestions from '@/components/ChatSuggestions';
import NastroEsecuzione, { ToolCall, FlowPoint, fmtKB, fmtMs } from '@/components/NastroEsecuzione';
import IconaDesk from './chat/IconaDesk';
import { parole } from './chat/parole';
import './chat-nuova.css';

/* ════════════════════════════════════════════════════════════
   F3 v3 "DESK CONVERSAZIONALE" (Opus 5, 26/07)
   Impianto scelto dal PM sui mockup (mockup_f3_chat/F3_MIX.png):
   desk + archivio a sinistra · nastro d'esecuzione e conversazione
   al centro · catena di custodia, arsenale e telemetria a destra.

   La pagina consuma lo stream PER INTERO. Prima di oggi finivano
   in console.log — e la pagina scriveva a mano "sonnet-4-6":
     meta            -> modello VERO, n. strumenti dell'agente, cap iterazioni
     tool_use_start  -> nome, tool_id, iterazione, istante
     tool_result     -> ESITO, byte tornati, anteprima grezza
     done            -> ok/interrotta, iterazioni, token
   Nessuna stringa di modello e' cablata: si legge dal payload.
   ════════════════════════════════════════════════════════════ */

/** Ponte 26/07: l'archivio del PM contiene 16 conversazioni di due specialisti
 *  ritirati (politics, news) che /agents/list non elenca piu' — dalla pagina erano
 *  IRRAGGIUNGIBILI. Finche' il backend non espone l'archivio completo
 *  (richiesta nel ponte: GET /chat/sessions?all=1) questi due id restano qui,
 *  dichiarati in pagina come "LEGACY · SOLA LETTURA". */
const AGENTI_RITIRATI = ['politics', 'news'];

interface Segnale { pos: number; n: number }      // dove, nel testo, e' partita la chiamata n

interface RuntimeMessage extends Partial<ChatMessage> {
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  /** null = il backend DICHIARA token non noti (stream morto prima delle
   *  metriche): non è uno zero, e non si rende come zero (audit/24 B.13). */
  tokens?: { in: number | null; out: number | null };
  tokensStatus?: 'completo' | 'parziale';
  tokensMissing?: string[];
  costEur?: ChatCost | null;
  /* materiale dello stream (solo live: il backend non lo salva) */
  calls?: ToolCall[];
  marks?: Segnale[];
  flow?: FlowPoint[];
  meta?: { model?: string; n_tools_available?: number; max_tool_iterations?: number; mandato?: MandatoMeta | null };
  ok?: boolean;
  iterations?: number;
  durata?: number | null;
  storico?: boolean;                              // caricato dal DB: niente traccia
  errore?: string;
  erroreKind?: 'stopped' | 'stoppedShort' | 'incomplete' | 'network' | 'unknown';
  requestId?: number;
}

interface ChatCost {
  cost: number | null;
  status: string | null;
  model: string | null;
  cacheFields: string | null;
  fxSource: string | null;
  nota: string | null;
}

/** Titolo LETTERALE dal primo messaggio: e' quello che il PM ha scritto, ripulito.
 *  Provvisorio per costruzione — il titolo semantico lo scrive il backend (Haiku,
 *  0,00025 EUR a chat misurati) appena l'endpoint di rinomina esiste (voce nel ponte). */
export function titoloDaMessaggio(q: string, max = 54): string {
  let s = (q || '').replace(/\s+/g, ' ').trim();
  if (!s) return '';
  if (s === s.toUpperCase() && /[A-Z]{4}/.test(s)) s = s.toLowerCase();
  s = s.charAt(0).toUpperCase() + s.slice(1);
  const dom = s.indexOf('?');
  if (dom > 8 && dom <= max + 8) return s.slice(0, dom + 1);
  if (s.length <= max) return s;
  const taglio = s.slice(0, max);
  const sp = taglio.lastIndexOf(' ');
  return (sp > max * 0.55 ? taglio.slice(0, sp) : taglio) + '…';
}

const titoloGenerico = (t: string) => /^Chat (?:con|with) /i.test((t || '').trim());

/** Dettagli della risposta aperti o chiusi: una preferenza di chi guarda, non un dato. */
const CHIAVE_DETTAGLI = 'bellomberg.chat.details.v1';
function dettagliAperti(): boolean {
  try { return localStorage.getItem(CHIAVE_DETTAGLI) === 'open'; } catch { return false; }
}
type Scheda = 'nastro' | 'fonti' | 'costi';
/** Iniziale maiuscola per le etichette del catalogo scritte in minuscolo («in ascolto…»). */
const iniziale = (s: string) => s.charAt(0).toLocaleUpperCase() + s.slice(1);

function ChatPresentation({ render }: { render: () => React.ReactNode }) {
  return <>{render()}</>;
}

type ChatProblem = { kind: 'source' | 'create' | 'delete'; detail: string; id?: number };
function problemText(problem: ChatProblem) {
  return problem.kind === 'delete' ? tr('communications.sessionDeleteFailure', { a: problem.id ?? '?', b: problem.detail })
    : problem.kind === 'create' ? tr('communications.sessionCreateFailure', { a: problem.detail }) : problem.detail;
}
function responseError(m: RuntimeMessage) {
  if (m.erroreKind === 'stopped') return tr('communications.chatStopped');
  if (m.erroreKind === 'stoppedShort') return tr('communications.chatStoppedShort');
  if (m.erroreKind === 'incomplete') return tr('communications.chatIncomplete');
  if (m.erroreKind === 'unknown') return tr('communications.unknownError');
  if (m.erroreKind === 'network') return tr('communications.networkFailure', { a: m.errore || tr('communications.unknownError') });
  return m.errore;
}

export default function Chat() {
  const tr = useT();
  const [agentsRaw, setAgents] = useState<Awaited<ReturnType<typeof Bellomberg.agentsList>> | null>(null);
  const agents = useMemo(() => localizePayload(agentsRaw)?.agents || [], [agentsRaw, tr]);
  const [engines, setEngines] = useState<EnginesInfo | null>(null);
  const [agentsErr, setAgentsErr] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<AgentInfo | null>(null);
  const [ritirato, setRitirato] = useState<string | null>(null);   // sessione legacy aperta
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionsErr, setSessionsErr] = useState<ChatProblem | null>(null);
  const [activeSession, setActiveSession] = useState<number | null>(null);
  const [messages, setMessages] = useState<RuntimeMessage[]>([]);
  const [msgsErr, setMsgsErr] = useState<ChatProblem | null>(null);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [hot, setHot] = useState<number | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(dettagliAperti);
  const [pin, setPin] = useState<number | null>(null);
  const [daEliminare, setDaEliminare] = useState<ChatSession | null>(null);
  const [ambito, setAmbito] = useState<'desk' | 'tutti'>('desk');
  const [scheda, setScheda] = useState<Scheda>('nastro');

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const skipFetchRef = useRef<number | null>(null);
  const sendingRef = useRef(false);
  const requestRef = useRef(0);
  const suggerimentiRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => () => {
    ++requestRef.current;
    sendingRef.current = false;
    abortRef.current?.abort();
  }, []);

  useEffect(() => {
    setAgentsErr(null);
    Bellomberg.agentsList().then(r => {
      setAgents(r || null);
      setEngines(r?.engines || null);
      if (r?.agents?.length && !selectedAgent) setSelectedAgent(r.agents[0]);
    }).catch(e => {
      // Buco DICHIARATO (regola 14/07): rail vuota muta = falso "nessun agente"
      console.error('agentsList', e);
      setAgentsErr(leggiDetail(e?.response?.data?.detail || e?.message || String(e)));
    });
  }, []);

  /** L'archivio e' UNICO: tutte le conversazioni di tutti gli agenti, compresi i due
   *  ritirati. Una GET per agente (query indicizzata su specialist, nessun costo). */
  const caricaArchivio = async (ids: string[]) => {
    setSessionsErr(null);
    const esiti = await Promise.allSettled(
      ids.map(id => Bellomberg.chatListSessions(id, 100).then(r => r?.sessions || [])),
    );
    const buchi: string[] = [];
    const tutte: ChatSession[] = [];
    esiti.forEach((e, i) => {
      if (e.status === 'fulfilled') tutte.push(...e.value);
      else buchi.push(ids[i] + ': ' + ((e.reason as any)?.response?.data?.detail || (e.reason as any)?.message || String(e.reason)));
    });
    tutte.sort((a, b) => String(b.last_activity || b.started_at).localeCompare(String(a.last_activity || a.started_at)));
    setSessions(tutte);
    if (buchi.length) setSessionsErr({ kind: 'source', detail: buchi.join(' · ') });   // buco dichiarato, non archivio vuoto
  };

  useEffect(() => {
    if (!agents.length) return;
    caricaArchivio([...agents.map(a => a.id), ...AGENTI_RITIRATI]);
  }, [agents.length]);

  useEffect(() => {
    let alive = true;
    if (!activeSession) { setMessages([]); setMsgsErr(null); setMessagesLoading(false); return; }
    if (skipFetchRef.current === activeSession) { skipFetchRef.current = null; setMessagesLoading(false); return; }
    setMessages([]);
    setMessagesLoading(true);
    setMsgsErr(null);
    Bellomberg.chatGetSession(activeSession).then(s => {
      if (!alive) return;
      setMessages((s.messages || []).map(m => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        output_language: m.output_language ?? null,
        id: m.id,
        timestamp: m.timestamp,
        storico: true,          // dal DB: nessuna traccia degli strumenti, e la pagina lo dice
        // n.5 del lotto backend (58): i token dallo storico ARRIVANO (misure
        // dal DB). «Non consegnati per lo storico» era diventato falso: erano
        // consegnati, era questo mapping a buttarli (confutatore 03/08). La
        // dichiarazione resta per le sole righe dove il DB ha davvero null.
        tokens: typeof m.tokens_in === 'number' && typeof m.tokens_out === 'number'
          ? { in: m.tokens_in, out: m.tokens_out } : undefined,
      })));
    }).catch(e => {
      if (!alive) return;
      console.error('getSession', e);
      setMessages([]);
      setMsgsErr({ kind: 'source', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) });
    }).finally(() => { if (alive) setMessagesLoading(false); });
    return () => { alive = false; };
  }, [activeSession]);

  useEffect(() => {
    if (!scrollRef.current) return;
    if (messages.length === 0) scrollRef.current.scrollTop = 0;
    else scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length]);

  useEffect(() => {
    try { localStorage.setItem(CHIAVE_DETTAGLI, inspectorOpen ? 'open' : 'closed'); } catch { /* preferenza solo locale */ }
  }, [inspectorOpen]);

  /* ── selezione ──────────────────────────────────────────── */
  const apriSessione = (s: ChatSession) => {
    stopStream();
    const ag = agents.find(a => a.id === s.specialist);
    if (ag) { setSelectedAgent(ag); setRitirato(null); }
    else setRitirato(s.specialist);         // specialista ritirato: sola lettura
    setActiveSession(s.id);
    setPin(null); setHot(null);
  };

  const nuovaConversazione = () => {
    stopStream();
    // Niente riga a vuoto nel DB: la sessione nasce col primo messaggio, gia' titolata.
    setActiveSession(null); setMessages([]); setRitirato(null);
    setPin(null); setHot(null);
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const eliminaConfermata = async () => {
    const s = daEliminare;
    setDaEliminare(null);
    if (!s) return;
    try {
      await Bellomberg.chatDeleteSession(s.id);
      if (activeSession === s.id) { setActiveSession(null); setMessages([]); }
      setSessions(prev => prev.filter(x => x.id !== s.id));
    } catch (e: any) {
      console.error('deleteSession', e);
      setSessionsErr({ kind: 'delete', id: s.id, detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) });
    }
  };

  const stopStream = () => {
    const stoppedRequest = requestRef.current;
    ++requestRef.current;
    sendingRef.current = false;
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setMessages(prev => prev.map(m => m.requestId === stoppedRequest && m.streaming
      ? { ...m, streaming: false, ok: false, errore: tr('communications.chatStopped'), erroreKind: 'stopped' }
      : m));
  };

  /* ── invio + stream ─────────────────────────────────────── */
  const sendMessage = async (messageOverride?: string) => {
    const textToSend = (messageOverride ?? input).trim();
    if (!textToSend || !selectedAgent || streaming || sendingRef.current || messagesLoading || ritirato) return;
    const streamHeaders = requestHeaders();

    // La guardia precede anche la creazione HTTP della sessione.
    sendingRef.current = true;
    const requestId = ++requestRef.current;
    const isCurrent = () => requestRef.current === requestId;
    setStreaming(true);

    let sid = activeSession;
    if (!sid) {
      try {
        // titolo LETTERALE alla nascita: nessuna conversazione si chiama piu' "Chat con X"
        const s = await Bellomberg.chatCreateSession(selectedAgent.id, titoloDaMessaggio(textToSend));
        if (!isCurrent()) return;
        sid = s.session_id;
        skipFetchRef.current = sid;
        setActiveSession(sid);
      } catch (e: any) {
        if (!isCurrent()) return;
        setMsgsErr({ kind: 'create', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) });
        sendingRef.current = false;
        setStreaming(false);
        return;
      }
    }

    const userMsg = textToSend;
    if (!messageOverride) setInput('');
    setPin(null); setHot(null);
    setMessages(prev => [
      ...prev,
      { role: 'user', content: userMsg },
      { role: 'assistant', content: '', streaming: true, calls: [], marks: [], flow: [], durata: null, requestId },
    ]);
    setStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const t0 = performance.now();
    const ora = () => performance.now() - t0;
    let ultimoCampione = -1;

    /** Aggiorna la risposta di questa richiesta, mai l'ultimo messaggio di un altro desk. */
    const patch = (f: (m: RuntimeMessage) => RuntimeMessage) => setMessages(prev => {
      if (!isCurrent()) return prev;
      const index = prev.findIndex(m => m.role === 'assistant' && m.requestId === requestId);
      if (index < 0) return prev;
      const copy = [...prev];
      copy[index] = f(copy[index]);
      return copy;
    });

    try {
      const resp = await fetch(Bellomberg.chatStreamUrl(sid), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json', 'Accept': 'text/event-stream',
          // Language and token belong to this send, including the session-creation await.
          ...streamHeaders,
        },
        body: JSON.stringify({ message: userMsg }),
        signal: ctrl.signal,
      });
      if (resp.status === 401 || resp.status === 403) { clearSessionAndReload(); throw new Error('HTTP ' + resp.status); }
      if (!resp.ok || !resp.body) {
        const detail = await resp.json().catch(() => null);
        throw new Error('HTTP ' + resp.status + (typeof detail?.detail === 'string' ? ' — ' + detail.detail : ''));
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let terminalEvent = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!isCurrent()) { await reader.cancel(); break; }
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split(/\r?\n\r?\n/);
        buffer = events.pop() || '';

        for (const evt of events) {
          if (!evt.trim()) continue;
          let evtName = 'message';
          const dataLines: string[] = [];
          for (const line of evt.split(/\r?\n/)) {
            if (line.startsWith('event:')) evtName = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
          }
          if (dataLines.length === 0) continue;
          const dataStr = dataLines.join('\n');
          let payload: any = null;
          try { payload = JSON.parse(dataStr); } catch { payload = { text: dataStr }; }

          if (evtName === 'delta' && payload?.text) {
            patch(m => {
              const content = m.content + payload.text;
              const t = ora();
              // ritmo del testo campionato ogni ~120 ms: dato misurato, non decorazione
              const flow = (t - ultimoCampione > 120 || !m.flow?.length)
                ? [...(m.flow || []), { t, len: content.length }]
                : m.flow;
              if (flow !== m.flow) ultimoCampione = t;
              return { ...m, content, flow };
            });
          } else if (evtName === 'tool_use_start') {
            patch(m => {
              const n = (m.calls?.length || 0) + 1;
              const call: ToolCall = {
                n, id: String(payload?.tool_id ?? 'tool-' + n),
                name: String(payload?.tool_name ?? tr('communications.untitledTool')),
                iteration: Number(payload?.iteration ?? 1),
                t0: ora(),
              };
              return {
                ...m,
                calls: [...(m.calls || []), call],
                marks: [...(m.marks || []), { pos: m.content.length, n }],
              };
            });
          } else if (evtName === 'tool_result') {
            patch(m => ({
              ...m,
              calls: (m.calls || []).map(c => c.id === String(payload?.tool_id) ? {
                ...c,
                t1: ora(),
                ok: payload?.ok !== false,
                bytes: typeof payload?.result_size_bytes === 'number' ? payload.result_size_bytes : undefined,
                preview: typeof payload?.result_preview === 'string' ? payload.result_preview : undefined,
              } : c),
            }));
          } else if (evtName === 'meta') {
            patch(m => ({
              ...m,
              output_language: payload?.output_language === 'it' || payload?.output_language === 'en'
                ? payload.output_language : null,
              meta: {
                model: payload?.model, n_tools_available: payload?.n_tools_available,
                max_tool_iterations: payload?.max_tool_iterations,
                mandato: payload?.mandato ?? null,
              },
            }));
          } else if (evtName === 'done') {
            terminalEvent = true;
            const rawCost = payload?.cost_eur;
            const costEur: ChatCost | null = rawCost && typeof rawCost === 'object' ? {
              cost: typeof rawCost.cost === 'number' && Number.isFinite(rawCost.cost) ? rawCost.cost : null,
              status: typeof rawCost.status === 'string' ? rawCost.status : null,
              model: typeof rawCost.model === 'string' ? rawCost.model : null,
              cacheFields: typeof rawCost.cache_fields === 'string' ? rawCost.cache_fields : null,
              fxSource: typeof rawCost.fx_source === 'string' ? rawCost.fx_source : null,
              nota: typeof rawCost.nota === 'string' ? rawCost.nota : null,
            } : null;
            patch(m => ({
              ...m,
              streaming: false,
              durata: ora(),
              meta: typeof payload?.model === 'string'
                ? { ...m.meta, model: payload.model } : m.meta,
              ok: payload?.ok !== false,
              iterations: typeof payload?.iterations === 'number' ? payload.iterations : undefined,
              tokens: {
                in: typeof payload?.tokens_in === 'number' ? payload.tokens_in : null,
                out: typeof payload?.tokens_out === 'number' ? payload.tokens_out : null,
              },
              tokensStatus: payload?.tokens_status === 'completo' || payload?.tokens_status === 'parziale'
                ? payload.tokens_status : undefined,
              tokensMissing: Array.isArray(payload?.tokens_missing)
                ? payload.tokens_missing.filter((value: unknown): value is string => typeof value === 'string') : undefined,
              costEur,
            }));
          } else if (evtName === 'error') {
            terminalEvent = true;
            patch(m => ({ ...m, streaming: false, ok: false, errore: String(payload?.message || tr('communications.unknownError')),
              erroreKind: payload?.message ? undefined : 'unknown' }));
          }
        }
      }
      if (isCurrent() && !terminalEvent) {
        patch(m => ({ ...m, streaming: false, ok: false, durata: ora(), errore: tr('communications.chatIncomplete'), erroreKind: 'incomplete' }));
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        console.error('[Chat] network error', e);
        patch(m => ({ ...m, streaming: false, ok: false, durata: ora(), errore: e?.message || String(e), erroreKind: 'network' }));
      } else {
        patch(m => ({ ...m, streaming: false, durata: ora(), errore: tr('communications.chatStoppedShort'), erroreKind: 'stoppedShort' }));
      }
    } finally {
      if (isCurrent()) {
        sendingRef.current = false;
        setStreaming(false);
        abortRef.current = null;
      }
      if (isCurrent() && selectedAgent && sid) {
        Bellomberg.chatListSessions(selectedAgent.id, 100)
          .then(r => setSessions(prev => {
            const altre = prev.filter(s => s.specialist !== selectedAgent.id);
            const tutte = [...altre, ...(r?.sessions || [])];
            tutte.sort((a, b) => String(b.last_activity || b.started_at).localeCompare(String(a.last_activity || a.started_at)));
            return tutte;
          }))
          .catch((e: any) => {
            if (isCurrent()) setSessionsErr({ kind: 'source', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) });
          });
      }
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  /* ── derivati ───────────────────────────────────────────── */
  const ultimaRisposta = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === 'assistant') return messages[i];
    return null;
  }, [messages]);

  const calls = ultimaRisposta?.calls || [];
  const modello = ultimaRisposta?.meta?.model || selectedAgent?.model || engines?.chat || null;
  const nStrumenti = ultimaRisposta?.meta?.n_tools_available ?? null;
  const maxIter = ultimaRisposta?.meta?.max_tool_iterations ?? null;

  const archivio = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter(s =>
      (s.title || '').toLowerCase().includes(q) ||
      (agents.find(a => a.id === s.specialist)?.name || s.specialist).toLowerCase().includes(q));
  }, [sessions, query, agents]);

  const sessioneAttiva = sessions.find(s => s.id === activeSession) || null;
  const agenteCorrente = agents.find(agent => agent.id === selectedAgent?.id) || selectedAgent;
  const coloreAgente = agenteCorrente?.color || null;

  return (
    <NewInterfaceBoundary language={linguaCorrente()}>
    <ChatPresentation render={() => {
      const w = parole();
      const idDesk = ritirato || agenteCorrente?.id || '';
      // Una sessione di un desk ritirato resta aperta in sola lettura con il desk
      // attivo ancora selezionato: l'intestazione deve dire di chi è la conversazione.
      const nomeHeader = ritirato ? iniziale(ritirato) : agenteCorrente?.name || tr('communications.none');
      const sessioniDesk = archivio.filter(s => s.specialist === idDesk);
      const visibili = ambito === 'tutti' ? archivio : sessioniDesk;
      const threadTitle = sessioneAttiva?.title || (activeSession ? titoloDaMessaggio(messages.find(m => m.role === 'user')?.content || '') : '') || tr('communications.newConversation');
      const modelHeader = ultimaRisposta?.meta?.model || modello;
      const modelHeaderLabel = ultimaRisposta?.meta?.model
        ? tr('communications.responseModel') : tr('communications.configuredModel');
      const apriFonti = (n: number) => { setPin(n); setScheda('fonti'); setInspectorOpen(true); };
      return <div className="f3d chat-modern bbn-chat bbn-font" data-chat-page="" data-details={inspectorOpen ? 'open' : 'closed'}>
      {/* ── intestazione: agente, conversazione, stato dell'ultima risposta ── */}
      <header className="bbn-chat-bar chat-modern-agent-header" data-chat-agent={ritirato ? undefined : agenteCorrente?.id}>
        <IconaDesk id={idDesk} colore={coloreAgente} ritirato={!!ritirato} className="chat-modern-avatar" />
        <div className="chat-modern-agent-copy">
          <h1>{nomeHeader}{ritirato && <span className="bbn-warn-pill">{w.retiredDesk}</span>}</h1>
          <p>
            <span>{ritirato ? w.readOnly : agenteCorrente?.role || tr('communications.selectToStart')}</span>
            {' · '}<b title={threadTitle}>{threadTitle}</b>
          </p>
        </div>
        <span className="bbn-grow" />
        <div className="bbn-chat-status" aria-live="polite">
          <span className="bbn-chat-status-model chat-modern-model" title={modelHeader || undefined}>
            {modelHeaderLabel} <b>{modelHeader || tr('communications.modelUnavailable')}</b>
          </span>
          <i className="bbn-chat-sep" />
          <span className="chat-modern-status"><StatoRisposta m={ultimaRisposta} streaming={streaming} /></span>
          {ultimaRisposta?.durata != null && <span className="num bbn-chat-status-time">{fmtMs(ultimaRisposta.durata)}</span>}
          {ultimaRisposta && !ultimaRisposta.streaming && !ultimaRisposta.storico && <>
            <i className="bbn-chat-sep" /><CostoBreve m={ultimaRisposta} />
          </>}
        </div>
        <button type="button" className={'bbn-btn chat-inspector-toggle' + (inspectorOpen ? ' is-on' : '')}
                aria-expanded={inspectorOpen} aria-controls="chat-inspector-content" title={w.detailsHint}
                onClick={() => setInspectorOpen(o => !o)}>
          <PanelRightOpen size={17} aria-hidden="true" /> {w.details}
          {calls.length > 0 && <span className="bbn-chat-count num">{calls.length}</span>}
        </button>
        <button type="button" className="bbn-btn is-primary nuova" onClick={nuovaConversazione}
                title={tr('communications.newConversationTitle')} aria-label={tr('communications.newConversationAccessible')}>
          <Plus size={16} aria-hidden="true" /> {w.newConversation}
        </button>
      </header>

      <div className={'body bbn-chat-grid' + (inspectorOpen ? ' has-details' : '')}>
        {/* ══ sinistra: desk + conversazioni ══ */}
        <div className="cL">
          <Card className="bbn-chat-desks" titolo={tr('communications.deskHeading')}
                conteggio={`${agents.length} ${tr('communications.specialists')}`}>
            {agentsErr && agents.length === 0 ? (
              <p className="dec ko bbn-chat-alert">
                <b>{tr('communications.agentsMissing')}</b> — {agentsErr}{tr('communications.agentsBackendError')}
              </p>
            ) : <div className="bbn-chat-desk-list">{agents.map(a => {
              const attivo = a.id === agenteCorrente?.id && !ritirato;
              const n = sessions.filter(s => s.specialist === a.id).length;
              return <button key={a.id} type="button" data-desk-id={a.id} title={a.name} aria-pressed={attivo}
                             className={'dk' + (attivo ? ' on' : '')}
                             onClick={() => { setSelectedAgent(a); setRitirato(null); nuovaConversazione(); }}>
                <IconaDesk id={a.id} colore={a.color} dimensione="sm" className="chat-modern-specialist-avatar" />
                <span className="bbn-chat-desk-copy">
                  <span className="nm">{a.name}</span>
                  <span className="rl">{a.role}</span>
                </span>
                {n > 0 && <span className="tl num" title={tr('communications.conversationsWith', { a: n, b: a.name })}>{fmtNum(n, 0)}</span>}
              </button>;
            })}</div>}
          </Card>

          <Card className="bbn-chat-archive" titolo={w.conversations} conteggio={fmtNum(visibili.length, 0)}
                azioni={<Segmenti etichetta={w.scope} valore={ambito} onChange={setAmbito}
                  opzioni={[{ id: 'desk', testo: w.scopeDesk }, { id: 'tutti', testo: w.scopeAll }]} />}>
            <label className="srch">
              <Search size={15} aria-hidden="true" />
              <input value={query} onChange={e => setQuery(e.target.value)}
                     placeholder={iniziale(tr('communications.searchConversations'))} aria-label={tr('communications.searchArchive')} />
            </label>
            <div className="arch bbn-scroll">
              {sessionsErr && (
                <p className="dec ko bbn-chat-alert">
                  <b>{tr('communications.partialArchive')}</b> — {problemText(sessionsErr)}{tr('communications.partialArchiveNote')}
                </p>
              )}
              {visibili.length === 0 && !sessionsErr ? (
                <p className="vuoto bbn-empty">
                  {query ? <>{tr('communications.noConversationFor')} <b>“{query}”</b>.</> : <>{tr('communications.noArchivedConversations')}</>}
                </p>
              ) : (
                <ArchivioConversazioni righe={visibili} agents={agents} tutti={ambito === 'tutti'}
                  attiva={activeSession} onApri={apriSessione} onElimina={setDaEliminare} />
              )}
            </div>
          </Card>
        </div>

        {/* ══ centro: conversazione + composer ══ */}
        <section className="cM bbn-card bbn-chat-main" aria-label={threadTitle}>
          <div className="conv" ref={scrollRef}>
            <div className="bbn-chat-lane">
              {ritirato && <p className="dec warn bbn-chat-alert"><b>{w.retiredDesk}</b> — {w.retiredBanner(iniziale(ritirato))}</p>}
              {msgsErr && (
                <p className="dec ko bbn-chat-alert">
                  <b>{tr('communications.conversationUnavailable')}</b> — {problemText(msgsErr)}{tr('communications.conversationUnavailableNote')}
                </p>
              )}
              {!agenteCorrente && !ritirato && !agentsErr && (
                <p className="vuoto bbn-empty">{tr('communications.selectSpecialist')}</p>
              )}
              {messagesLoading && <p className="vuoto bbn-empty" role="status">{tr('communications.loadingConversation')}</p>}
              {messages.length === 0 && agenteCorrente && !msgsErr && !messagesLoading && (
                <Ingresso agente={agenteCorrente}
                          disabled={streaming || messagesLoading || !!ritirato} onPrompt={p => sendMessage(p)} />
              )}
              {messages.map((m, i) => (
                <Turno key={m.id ?? 'live-' + i} m={m} agente={agenteCorrente} ritirato={ritirato}
                       hot={hot} pin={pin} onHot={setHot} onApri={apriFonti} />
              ))}
            </div>
          </div>

          <div className="comp">
            <div className="bbn-chat-lane">
              {messages.length > 0 && agenteCorrente && !ritirato && <details className="chat-followups" ref={suggerimentiRef}>
                <summary><Lightbulb size={15} aria-hidden="true" /> {w.suggestions}</summary>
                <ChatSuggestions agent={agenteCorrente.id} name={agenteCorrente.name}
                  disabled={streaming || messagesLoading} onPrompt={p => {
                    if (suggerimentiRef.current) suggerimentiRef.current.open = false;
                    void sendMessage(p);
                  }} />
              </details>}
              <div className="field">
                <textarea id="chat-message" ref={inputRef} value={input} rows={2}
                          onChange={e => setInput(e.target.value)} onKeyDown={onKey}
                          disabled={!agenteCorrente || streaming || messagesLoading || !!ritirato}
                          aria-label={tr('communications.askPlaceholderShort', { a: agenteCorrente?.name || '' })}
                          placeholder={ritirato
                            ? tr('communications.retiredPlaceholder', { a: ritirato })
                            : agenteCorrente
                              ? tr('communications.askPlaceholderShort', { a: agenteCorrente.name })
                              : tr('communications.selectToStart')} />
                {streaming ? (
                  <button type="button" className="send stop bbn-btn" onClick={stopStream}><Square size={13} aria-hidden="true" /> {tr('communications.stop')}</button>
                ) : (
                  <button type="button" className="send bbn-btn is-primary" onClick={() => sendMessage()}
                          disabled={!agenteCorrente || !input.trim() || messagesLoading || !!ritirato}>
                    <Send size={14} aria-hidden="true" /> {tr('communications.send')}
                  </button>
                )}
              </div>
              <p className="chat-modern-composer-hint">
                <kbd>{tr('communications.enterKey')}</kbd> {tr('communications.enterToSend')} <span>·</span> <kbd>{tr('communications.shiftEnterKey')}</kbd> {tr('communications.shiftEnterNewline')}
              </p>
            </div>
          </div>
        </section>

        {/* ══ destra, su richiesta: nastro, fonti, costi ══ */}
        <section id="chat-inspector-content" className={'cR bbn-card bbn-chat-details' + (inspectorOpen ? ' is-expanded' : ' is-collapsed')}
                 aria-label={w.details} hidden={!inspectorOpen}>
          <header className="bbn-card-head">
            <h2>{w.details}</h2>
            <span className="bbn-grow" />
            <button type="button" className="bbn-icon-btn chat-inspector-close" title={w.detailsClose} aria-label={w.detailsClose}
                    onClick={() => setInspectorOpen(false)}><X size={16} aria-hidden="true" /></button>
          </header>
          <div className="bbn-chat-details-sum">
            <StatoRisposta m={ultimaRisposta} streaming={streaming} />
            {ultimaRisposta?.durata != null && <b className="num">{fmtMs(ultimaRisposta.durata)}</b>}
            {calls.length > 0 && <span>{w.tools(calls.length)}</span>}
            {ultimaRisposta && !ultimaRisposta.streaming && !ultimaRisposta.storico && <CostoBreve m={ultimaRisposta} />}
          </div>
          <div className="bbn-chat-details-tabs">
            <Segmenti etichetta={w.detailsTabs} valore={scheda} onChange={setScheda} opzioni={[
              { id: 'nastro', testo: w.tabTape, title: tr('communications.tapeTitle') },
              { id: 'fonti', testo: w.tabSources, title: tr('communications.custodyChain') },
              { id: 'costi', testo: w.tabCosts, title: tr('communications.responseTelemetry') },
            ]} />
          </div>
          <div className="bbn-chat-details-body bbn-scroll">
            {!ultimaRisposta && <p className="bbn-empty chat-section-empty">{w.detailsEmpty}</p>}
            <div className="bbn-chat-pane" data-pane="nastro" hidden={scheda !== 'nastro' || !ultimaRisposta}>
              <NastroEsecuzione
                calls={calls}
                flow={ultimaRisposta?.flow || []}
                durata={ultimaRisposta?.durata ?? null}
                streaming={!!ultimaRisposta?.streaming}
                disponibile={!!ultimaRisposta && !ultimaRisposta.storico}
                motivoAssenza={!ultimaRisposta ? tr('communications.noReplyForTape') : undefined}
                hot={hot} onHot={setHot} pin={pin} onPin={setPin}
              />
              {ultimaRisposta && !ultimaRisposta.storico && <p className="flow"><Flusso m={ultimaRisposta} /></p>}
            </div>
            <div className="bbn-chat-pane p3 cy" data-pane="fonti" hidden={scheda !== 'fonti' || !ultimaRisposta}>
              <div className="cat">
                <Catena calls={calls} m={ultimaRisposta} hot={hot} pin={pin} onHot={setHot} onPin={setPin} />
                {nStrumenti != null && (
                  <div className="ars">
                    <div className="h">
                      {w.toolsUsed(calls.length, nStrumenti, agenteCorrente?.name || tr('communications.agentUpper'))}
                    </div>
                    <div className="grid">
                      {Array.from({ length: Math.min(nStrumenti, 60) }, (_, k) => (
                        <i key={k} className={k < calls.length ? 'on' : ''} />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
            <div className="bbn-chat-pane" data-pane="costi" hidden={scheda !== 'costi' || !ultimaRisposta}>
              <Telemetria m={ultimaRisposta} maxIter={maxIter} modello={modello} />
            </div>
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={!!daEliminare}
        title={tr('communications.deleteQuestion')}
        intro={tr('communications.deleteIntro')}
        rows={daEliminare ? [
          { k: tr('communications.conversationUpper'), v: '#' + daEliminare.id + ' · ' + daEliminare.title },
          { k: tr('communications.specialistUpper'), v: (agents.find(a => a.id === daEliminare.specialist)?.name || daEliminare.specialist).toUpperCase() },
          { k: tr('communications.messagesUpper'), v: daEliminare.msg_count != null ? fmtNum(daEliminare.msg_count, 0) : tr('communications.unavailable'), tone: 'crimson' },
        ] : []}
        confirmLabel={tr('communications.deleteUpper')}
        tone="crimson"
        onConfirm={eliminaConfermata}
        onCancel={() => setDaEliminare(null)}
      />
      </div>;
    }} />
    </NewInterfaceBoundary>
  );
}

/* ════════════════════════ pezzi ════════════════════════ */

function dataIt(s?: string) {
  if (!s) return tr('communications.dateUnavailable');
  const d = new Date(String(s).replace(' ', 'T'));
  if (isNaN(d.getTime())) return String(s);
  return d.toLocaleDateString(localeDi(linguaCorrente()), { day: '2-digit', month: '2-digit', year: '2-digit' });
}

function gruppoData(s?: string) {
  if (!s) return tr('communications.noDate');
  const d = new Date(String(s).replace(' ', 'T'));
  if (isNaN(d.getTime())) return tr('communications.noDate');
  const gg = Math.floor((Date.now() - d.getTime()) / 864e5);
  if (gg <= 0) return tr('communications.today');
  if (gg <= 1) return tr('communications.yesterday');
  if (gg <= 7) return tr('communications.lastWeek');
  if (gg <= 31) return tr('communications.lastMonth');
  return tr('communications.earlier');
}

/** Conversazioni del desk (o di tutti i desk), già ordinate per attività, divise per
 *  giorno. In «Tutti» ogni riga porta l'icona del suo desk; i desk ritirati la pastiglia
 *  «Sola lettura». La riga si apre con un clic, il cestino chiede conferma. */
function ArchivioConversazioni({ righe, agents, tutti, attiva, onApri, onElimina }: {
  righe: ChatSession[]; agents: AgentInfo[]; tutti: boolean; attiva: number | null;
  onApri: (s: ChatSession) => void; onElimina: (s: ChatSession) => void;
}) {
  const tr = useT();
  const w = parole();
  const out: JSX.Element[] = [];
  let gruppo = '';
  righe.forEach(s => {
    const quando = s.last_activity || s.started_at;
    const g = gruppoData(quando);
    if (g !== gruppo) { gruppo = g; out.push(<p key={'g-' + s.id} className="bbn-chat-day">{frase(g)}</p>); }
    // The list endpoint already supplies the first question; no detail fetch is needed.
    const preview = (s as ChatSession & { first_user_message?: string | null }).first_user_message;
    const ag = agents.find(a => a.id === s.specialist);
    const legacy = !ag;
    const vuota = s.msg_count === 0;
    const generico = titoloGenerico(s.title);
    const date = new Date(String(quando).replace(' ', 'T'));
    const days = Math.floor((Date.now() - date.getTime()) / 864e5);
    const relative = !Number.isFinite(days) ? dataIt(s.started_at)
      : days <= 0 ? date.toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' })
      : new Intl.RelativeTimeFormat(localeDi(linguaCorrente()), { numeric: 'auto' }).format(-days, 'day');
    out.push(
      <div className="sess-wrap" key={s.id}>
        <button type="button" className={'sess' + (attiva === s.id ? ' on' : '')} onClick={() => onApri(s)}
                aria-current={attiva === s.id ? 'true' : undefined}>
          <span className="ttl" title={s.title}>{vuota && generico ? tr('communications.unusedConversation') : s.title}</span>
          <time className="meta num" title={dataIt(quando)}>{relative}</time>
          <span className="chat-session-preview">
            {tutti && <IconaDesk id={s.specialist} colore={ag?.color} ritirato={legacy} dimensione="xs" />}
            {legacy && <span className="bbn-warn-pill">{w.readOnly}</span>}
            <span title={preview || undefined}>{preview || (vuota ? tr('communications.unusedConversation') : tr('communications.previewUnavailable'))}</span>
          </span>
        </button>
        <button type="button" className="del bbn-icon-btn" title={tr('communications.delete')} aria-label={tr('communications.deleteAccessible', { a: s.id })}
                onClick={e => { e.stopPropagation(); onElimina(s); }}><Trash2 size={14} aria-hidden="true" /></button>
      </div>,
    );
  });
  return <>{out}</>;
}

function StatoRisposta({ m, streaming }: { m: RuntimeMessage | null; streaming: boolean }) {
  const tr = useT();
  if (streaming) return <span className="bbn-pill is-acc"><i className="bbn-chat-pulse" aria-hidden="true" />{iniziale(tr('communications.listening'))}</span>;
  if (!m) return <span className="bbn-pill is-piatto">{iniziale(tr('communications.noResponseRunning'))}</span>;
  if (m.errore) return <span className="bbn-pill is-giu ko" title={responseError(m)}><i className="bbn-chat-dot" aria-hidden="true" />{iniziale(responseError(m) || '')}</span>;
  if (m.storico) return <span className="bbn-pill is-piatto">{iniziale(tr('communications.archivedConversation'))}</span>;
  if (m.ok === false) return <span className="bbn-pill is-giu ko"><i className="bbn-chat-dot" aria-hidden="true" />{iniziale(tr('communications.streamError'))}</span>;
  return <span className="bbn-pill is-su"><i className="bbn-chat-dot" aria-hidden="true" />
    {iniziale(tr('communications.streamComplete'))}{m.iterations != null && tr(m.iterations === 1 ? 'communications.streamIterationsOne' : 'communications.streamIterationsMany', { a: m.iterations })}
  </span>;
}

function Flusso({ m }: { m: RuntimeMessage | null }) {
  const tr = useT();
  const flow = m?.flow || [];
  if (flow.length < 3) return <span className="chat-modern-note">{iniziale(tr('communications.flowNoSamples').toLocaleLowerCase())}</span>;
  const car = flow[flow.length - 1].len;
  const sec = (flow[flow.length - 1].t - flow[0].t) / 1000;
  return (
    <>
      <span className="chat-modern-flowlabel">{frase(tr('communications.flow'))}</span>{' '}
      <span>
        {tr('communications.flowMeasured', { a: car.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }), b: sec.toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 1, useGrouping: true }) })}
        {sec > 0 && tr('communications.flowRate', { a: Math.round(car / sec).toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }) })}
      </span>
      {m?.tokens && (
        <>
          {' · '}
          {m.tokensStatus === 'parziale' ? <>
            <span>in {m.tokens.in?.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }) ?? tr('communications.unavailable')}
              {' · '}out {m.tokens.out?.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }) ?? tr('communications.unavailable')}</span>
            {' '}<small className="chat-modern-telemetry-note">{tr('communications.tokensUsagePartial')}</small>
          </> : <span>{m.tokens.in != null && m.tokens.out != null
            ? `in ${m.tokens.in.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true })} · out ${m.tokens.out.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true })}`
            : tr('communications.tokensMissingPartial')}</span>}
        </>
      )}
    </>
  );
}

function Catena({ calls, m, hot, pin, onHot, onPin }: {
  calls: ToolCall[]; m: RuntimeMessage | null;
  hot: number | null; pin: number | null;
  onHot: (n: number | null) => void; onPin: (n: number | null) => void;
}) {
  const tr = useT();
  if (!m) return (
    <div className="catvuoto">
      {tr('communications.chainNoResponse')}
      <div className="nota1">{tr('communications.chainHint')}</div>
    </div>
  );
  if (m.storico) return (
    <div className="catvuoto">
      <b>{tr('communications.traceUnsaved')}</b> — {tr('communications.chainHistoryMissing')}
    </div>
  );
  if (calls.length === 0) return (
    <div className="catvuoto">
      {m.streaming ? <>{tr('communications.awaitingTools')}</> : (
        <><span className="ko">{tr('communications.tapeNoTools')}</span> — {tr('communications.chainModelOnly')} <b>{tr('communications.readAgainstTools')}</b>.</>
      )}
    </div>
  );
  const sel = pin ?? hot;
  return (
    <>
      {calls.map(c => (
        <div key={c.id}
             className={'ev' + (sel === c.n ? ' hot' : '') + (c.ok === false ? ' ko' : c.ok == null ? ' run' : '')}
             onMouseEnter={() => onHot(c.n)} onMouseLeave={() => onHot(null)}
             onClick={() => onPin(pin === c.n ? null : c.n)}>
          <span className="ix num" aria-label={'[' + c.n + ']'}>{c.n}</span>
          <span className="tn">{c.name}</span>
          <span className="kb num">{fmtKB(c.bytes) ?? (c.ok == null ? tr('communications.inProgressEllipsis') : tr('communications.unavailable'))}
            {' · '}{c.t1 != null ? fmtMs(c.t1 - c.t0) : tr('communications.sinceStart', { a: fmtMs(c.t0) ?? tr('communications.unavailable') })}</span>
          <span className="arg">{tr('communications.iteration')} {c.iteration}</span>
          {c.ok === false && (
            <span className="warn">{tr('communications.toolFailureWarning')}</span>
          )}
          {sel === c.n && c.preview && <div className="prev"><small>{tr('communications.originalSource')}</small><div>{c.preview}</div></div>}
        </div>
      ))}
    </>
  );
}

function Telemetria({ m, maxIter, modello }: {
  m: RuntimeMessage | null; maxIter: number | null; modello: string | null;
}) {
  const tr = useT();
  const iter = m?.iterations ?? null;
  const cap = maxIter ?? 8;
  const fmt = (n: number) => n.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true });
  return (
    <dl className="tel bbn-stats">
      <div className="bbn-chat-stat-wide"><CostoRisposta m={m} /></div>
      <div className="telrow">
        <dt>{tr('communications.toolIterations')}</dt>
        {iter != null ? <dd className="num">{iter} / {maxIter ?? '?'}</dd> : <dd className="nd">{tr('communications.unavailable')}</dd>}
        <span className="iter" aria-hidden="true">
          {Array.from({ length: Math.min(cap, 12) }, (_, k) => <i key={k} className={iter != null && k < iter ? 'on' : ''} />)}
        </span>
      </div>
      <div className="telrow">
        <dt>{tr('communications.duration')}</dt>
        {m?.durata != null ? <dd className="num">{fmtMs(m.durata)}</dd> : <dd className="nd">{tr('communications.unavailable')}</dd>}
      </div>
      <div className="telrow">
        <dt>{tr('communications.tokenInOut')}</dt>
        {m?.tokens
          ? (m.tokensStatus === 'parziale'
              ? <dd className="num">{m.tokens.in != null ? fmt(m.tokens.in) : tr('communications.unavailable')}
                  {' / '}{m.tokens.out != null ? fmt(m.tokens.out) : tr('communications.unavailable')}
                  <small className="chat-modern-telemetry-note">{tr('communications.tokensUsagePartial')}</small>
                </dd>
              : m.tokens.in != null && m.tokens.out != null
              ? <dd className="num">{fmt(m.tokens.in)} / {fmt(m.tokens.out)}</dd>
              : <dd className="nd">{tr('communications.tokenMetricsLost')}</dd>)
          : <dd className="nd">{!m ? tr('communications.unavailable')
              : m.storico ? tr('communications.tokensNotDelivered')
              /* «a fine risposta» SOLO mentre lo stream e' vivo: e' l'unico stato
                 in cui i token arriveranno davvero. A stream chiuso senza `done`
                 (STOP, rete caduta, troncamento) prometteva un numero su una
                 risposta gia' finita (confutatore 03/08, BASSA (a) — MASTER
                 §9-unquadragies-octies). La frase e' al PRESENTE di proposito:
                 «non arriveranno mai» sarebbe falsa nella finestra fra l'evento
                 `error` e il `done` che lo segue sempre (fra i due il backend fa
                 una scrittura SQLite e un to_thread: non e' un istante). */
              : m.streaming ? tr('communications.tokensAtEnd')
                : tr('communications.tokensNoDone')}</dd>}
      </div>
      {/* Il motore e' un DATO, non una nota: sta in una riga come gli altri. */}
      {modello && <div className="telrow"><dt>{tr(m?.meta?.model ? 'communications.responseModel' : 'communications.configuredModel')}</dt><dd className="bbn-chat-model">{modello}</dd></div>}
    </dl>
  );
}

/** Stato del costo di una risposta: misurato, parziale, errore, nessuna chiamata o n.d.
 *  Un costo non misurato non diventa mai zero. */
function statoCosto(m: RuntimeMessage | null) {
  const cost = m?.costEur || null;
  const status = cost?.status?.toLowerCase() || '';
  const measured = cost?.cost != null;
  const statusDeclaresUsageGap = status.startsWith('parziale') || status === 'usage_unknown'
    || (status.startsWith('non_calcolato') && /cache|usage|token|utilizzo/i.test(status));
  const partial = statusDeclaresUsageGap || (!measured && !status && m?.tokensStatus === 'parziale');
  const failed = status.startsWith('errore') || status === 'api_error';
  const noCall = status.startsWith('nessuna_chiamata');
  const state = noCall ? 'no-call' : failed ? 'error' : partial ? 'partial' : measured ? 'measured' : 'unavailable';
  const reason = noCall ? 'communications.costNoCall'
    : failed ? (measured ? 'communications.costRecordedBeforeError' : 'communications.costMeasurementFailed')
    : partial ? (measured ? 'communications.costUsagePartial' : 'communications.costPartialUnavailable')
    : measured && (cost?.nota || cost?.cacheFields === 'assenti') ? 'communications.costUsageDetailsPartial'
    : measured ? null
    : status.includes('cambio usd') || cost?.fxSource === 'n.d.' ? 'communications.costFxUnavailable'
    : status === 'model_unknown' || status === 'pricing_unavailable' ? 'communications.costPricingUnavailable'
    : status.includes('modello non risolto') ? 'communications.costModelUnavailable'
    : status.startsWith('non_calcolato') ? 'communications.costUsageUnavailable'
    : status.startsWith('errore') ? 'communications.costMeasurementFailed'
    : 'communications.costUnavailableNote';
  const value = measured
    ? new Intl.NumberFormat(localeDi(linguaCorrente()), {
        style: 'currency', currency: 'EUR', minimumFractionDigits: 2, maximumFractionDigits: 6,
      }).format(cost!.cost as number)
    : null;
  return { state, reason, value } as const;
}

function CostoRisposta({ m }: { m: RuntimeMessage | null }) {
  const tr = useT();
  const { state, reason, value } = statoCosto(m);
  return (
    <div className="chat-modern-cost" data-cost-status={state}>
      <span>{tr('communications.responseCost')}</span>
      <b className={'num' + (value == null ? ' nd' : '')}>{value ?? tr('communications.unavailable')}</b>
      {reason && <small className="chat-modern-telemetry-note">{tr(reason)}</small>}
    </div>
  );
}

/** Il costo in una parola, per l'intestazione: valore misurato o «costo n.d.», con il motivo nel title. */
function CostoBreve({ m }: { m: RuntimeMessage }) {
  const tr = useT();
  const { state, reason, value } = statoCosto(m);
  return <span className={'bbn-chat-cost num' + (value == null ? ' is-na' : '')} data-cost-state={state}
               title={reason ? tr(reason) : tr('communications.responseCost')}>{value ?? parole().costNa}</span>;
}

/* ── un turno della conversazione ── */
function Turno({ m, agente, ritirato, hot, pin, onHot, onApri }: {
  m: RuntimeMessage; agente: AgentInfo | null; ritirato: string | null;
  hot: number | null; pin: number | null;
  onHot: (n: number | null) => void; onApri: (n: number) => void;
}) {
  const tr = useT();
  if (m.role === 'user') {
    return (
      <div className="pmq">
        <div className="q">{m.content}</div>
        <div className="tag">{parole().you}{m.timestamp && <> · <time className="num">{dataIt(m.timestamp)} {oraIt(m.timestamp)}</time></>}</div>
      </div>
    );
  }
  const nomiUsati = new Set((m.calls || []).map(c => c.name));
  const idDesk = ritirato || agente?.id || '';
  return (
    <div className="turn">
      <div className="ansh">
        <IconaDesk id={idDesk} colore={agente?.color} ritirato={!!ritirato} dimensione="xs" />
        <span className="nm">{ritirato ? iniziale(ritirato) : agente?.name || frase(tr('communications.agentUpper'))}</span>
        {m.timestamp && <span className="when num">{dataIt(m.timestamp)} {oraIt(m.timestamp)}</span>}
        {m.streaming && <span className="chip a"><i className="bbn-chat-pulse" aria-hidden="true" />{iniziale(tr('communications.writing'))}</span>}
        <span className="chip n">{tr(m.output_language === 'it' ? 'communications.originalOutputIt'
          : m.output_language === 'en' ? 'communications.originalOutputEn' : 'communications.originalOutputUnknown')}</span>
        {!!m.calls?.length && (
          <span className="chip c">
            {frase(tr(m.calls.length === 1 ? 'communications.signedToolOne' : 'communications.signedToolMany', { a: m.calls.length }))}
          </span>
        )}
        {!m.streaming && !m.storico && !m.calls?.length && (
          <span className="chip w">{frase(tr('communications.noToolsUpper'))}</span>
        )}
        {m.tokens && (m.tokens.out != null
          ? <span className="chip n">{tr(m.tokens.out === 1 ? 'activity.tokenCountOne' : 'communications.tokenCount', { a: m.tokens.out.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }) })}</span>
          : <span className="chip w">{frase(tr('communications.tokensUnknownUpper'))}</span>)}
      </div>
      <div className="bbn-chat-body"><Corpo m={m} hot={hot} pin={pin} onHot={onHot} onApri={onApri} nomiUsati={nomiUsati} /></div>
      <div className="time" title={m.meta?.mandato?.impronta}>
        {m.meta?.mandato
          ? tr('communications.mandateOrigin', {a: m.meta.mandato.impronta.slice(0, 8), b: m.meta.mandato.origine, c: m.meta.mandato.dichiarato_il || tr('communications.unavailable')})
          : tr('communications.mandateOriginMissing')}
      </div>
      {m.errore && (
        <p className="dec ko bbn-chat-alert">
          <b>{frase(tr('communications.interruptedUpper'))}</b> — {responseError(m)}{tr('communications.partialResponse')}
        </p>
      )}
    </div>
  );
}

function oraIt(s?: string) {
  if (!s) return '';
  const d = new Date(String(s).replace(' ', 'T'));
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' });
}

/** Il corpo della risposta, spezzato nei punti in cui l'agente ha chiamato uno strumento:
 *  il richiamo [n] non e' decorativo, sta esattamente dove lo stream l'ha visto passare.
 *  Un clic sul richiamo apre i dettagli su quella chiamata. */
function Corpo({ m, hot, pin, onHot, onApri, nomiUsati }: {
  m: RuntimeMessage; hot: number | null; pin: number | null;
  onHot: (n: number | null) => void; onApri: (n: number) => void;
  nomiUsati: Set<string>;
}) {
  const tr = useT();
  const sel = pin ?? hot;
  if (!m.content && m.streaming) return <div className="prose"><span className="bbn-chat-caret" aria-label="…" /></div>;
  if (!m.content && !m.errore) return <div className="prose chat-modern-note">{tr('communications.emptyResponse')}</div>;

  const marks = m.marks || [];
  const pezzi: JSX.Element[] = [];
  let cur = 0;
  const tappe = [...new Set(marks.map(k => k.pos))].sort((a, b) => a - b);
  tappe.forEach(pos => {
    const testo = m.content.slice(cur, pos);
    if (testo.trim()) pezzi.push(<Prosa key={'t' + cur} md={testo} nomiUsati={nomiUsati} sel={sel} onHot={onHot} m={m} />);
    const qui = marks.filter(k => k.pos === pos);
    pezzi.push(
      <div className="refrow" key={'r' + pos}>
        {qui.map(k => {
          const c = (m.calls || []).find(x => x.n === k.n);
          return (
            <button type="button" key={k.n}
                    className={'ref' + (sel === k.n ? ' hot' : '') + (c?.ok === false ? ' ko' : '')}
                    onMouseEnter={() => onHot(k.n)} onMouseLeave={() => onHot(null)}
                    onClick={() => onApri(k.n)}
                    title={tr('communications.viewToolCall')}>
              <Wrench size={12} aria-hidden="true" /> {k.n} · {c?.name || '—'}
              {c?.bytes != null && <span className="chat-modern-refbytes"> · {fmtKB(c.bytes)}</span>}
            </button>
          );
        })}
      </div>,
    );
    cur = pos;
  });
  const coda = m.content.slice(cur);
  if (coda.trim() || pezzi.length === 0) {
    pezzi.push(<Prosa key={'t' + cur} md={coda} nomiUsati={nomiUsati} sel={sel} onHot={onHot} m={m} />);
  }
  if (m.streaming) pezzi.push(<span key="caret" className="bbn-chat-caret" aria-hidden="true" />);
  return <>{pezzi}</>;
}

function Prosa({ md, nomiUsati, sel, onHot, m }: {
  md: string; nomiUsati: Set<string>; sel: number | null;
  onHot: (n: number | null) => void; m: RuntimeMessage;
}) {
  const tr = useT();
  // [src: tool] e' la convenzione anti-allucinazione di casa: resta un chip, e quando
  // il nome combacia con uno strumento chiamato davvero si accende insieme alla catena.
  const processed = md.replace(/\[src:\s*([^\]]+)\]/g, (_x, s) => '`__SRC__' + String(s).trim() + '`');
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code: ({ inline, children, ...rest }: any) => {
            const txt = String(children).replace(/\n$/, '');
            if (txt.startsWith('__SRC__')) {
              const src = txt.slice('__SRC__'.length);
              const call = (m.calls || []).find(c => c.name === src);
              const acceso = call != null && sel === call.n;
              return (
                <span className={'src' + (call ? ' link' : '') + (acceso ? ' hot' : '')}
                      onMouseEnter={() => call && onHot(call.n)}
                      onMouseLeave={() => call && onHot(null)}
                      title={call ? tr('communications.chainSource', { a: call.n }) : tr('communications.agentSource')}>
                  src: {src}
                </span>
              );
            }
            if (inline) return <code {...rest}>{children}</code>;
            return <code {...rest}>{children}</code>;
          },
        }}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}

function Ingresso({ agente, onPrompt, disabled }: {
  agente: AgentInfo; onPrompt: (p: string) => void; disabled: boolean;
}) {
  const tr = useT();
  return (
    <div className="intro chat-modern-intro">
      <h2 className="big">{tr('communications.introHeadline', { a: agente.name })}</h2>
      <p className="hint">{tr('communications.introHelper')}</p>
      <div className="chat-modern-prompts">
        <p className="bbn-chat-subhead">{tr('communications.promptGroup')}</p>
        <ChatSuggestions agent={agente.id} name={agente.name} disabled={disabled} onPrompt={onPrompt} />
      </div>
    </div>
  );
}
