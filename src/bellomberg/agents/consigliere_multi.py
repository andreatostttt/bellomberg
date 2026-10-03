"""
BELLOMBERG - Personal AI Hedge Fund Terminal
Multi-agent consigliere v3 - with persistent memory (Phase 1 complete)

Pipeline:
1. PRIMING: portfolio (SQLite live) + macro + correlation matrix
2. INIT memo in DB (per riferimento dei specialisti)
3. Ripipeline (15/07): R0 recon su Sonnet (6 specialisti, parallelo) -> R1 analisi
   Opus a ONDATE di pipeline (macro+eventdesk -> crypto+fundamentals -> quant ->
   options: la validazione avviene nello stesso round) -> red team -> R2 SELETTIVO
   sequenziale (fundamentals -> quant -> options). 21 -> 15 agent-round.
4. CAPO synthesis (con full memory context)
5. Save memo + decision extraction (auto-popola tabella decisions)
6. PDF MEMO + PDF APPENDICE QUANT + DCF Excel (se generati)
7. EMAIL UNICA con allegati
"""
import os
import sys
import json
import glob
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from bellomberg.agents.bellomberg import print_banner, BRAND_NAME, VERSION
from bellomberg.agents.specialists import (
    Blackboard,
    MacroSpecialist, OptionsSpecialist, EventDeskSpecialist,
    FundamentalsSpecialist, CryptoSpecialist, QuantSpecialist,
)
from bellomberg.agents.capo import run_capo
from bellomberg.reporting.email_sender import email_configurata
from bellomberg.agents.agent_tools import tool_get_macro_dashboard, tool_quant_compute
from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
from bellomberg.core.language import capture_language, language_context, scoped_language
from bellomberg.storage.memory_db import MemoryDB, RESEARCH_NOTES_DIR


# Ripipeline 15/07 (dossier 03, ok PM esplicito): ORDINE DI PIPELINE — chi valida
# viene DOPO chi propone, cosi' la validazione avviene nello stesso round R1
# (prima Quant era PRIMA di Fundamentals: il 3° round completo esisteva solo per quello).
SPECIALIST_ORDER = [
    MacroSpecialist,          # regime e temi: il terreno di gioco
    EventDeskSpecialist,      # fusione News+Politics 15/07: eventi, catalyst, probabilita'
    CryptoSpecialist,
    FundamentalsSpecialist,   # propone i nomi (usa macro/eventi gia' in draft R1)
    QuantSpecialist,          # valida i numeri dei candidati NELLO STESSO round
    OptionsSpecialist,        # ondata DOPO Quant: strutture SOLO sui candidati validati
]

# R1 a ONDATE di pipeline: dentro l'ondata in parallelo, BARRIERA tra ondate
# (chi valida VEDE i draft R1 di chi propone). Quant e Options in ondate SEPARATE
# (review 15/07): Options struttura sui candidati GIA' validati da Quant — in
# parallelo avrebbe strutturato anche i bocciati. R2 SELETTIVO: replicano al red
# team solo i desk che decidono numeri e strutture; macro/eventdesk/crypto chiudono
# in R1 (chiude anche la voce P2 "Crypto declassato a R0+R1"). Run: 21 -> 15 round.
R1_STAGES = [["macro", "eventdesk"], ["crypto", "fundamentals"], ["quant"], ["options"]]
R2_SPECIALISTS = {"fundamentals", "quant", "options"}


def _classes_for_round(round_n):
    """Chi gira in questo round (R2 = solo il sottoinsieme selettivo)."""
    if round_n == 2:
        return [c for c in SPECIALIST_ORDER if c.name in R2_SPECIALISTS]
    return list(SPECIALIST_ORDER)


def _r1_pipeline_stages(classes):
    """Piano a ondate per R1. Difensivo: uno specialista fuori da R1_STAGES
    (comitato cambiato senza aggiornare le ondate) finisce in un'ondata finale
    DICHIARATA nel log, mai perso in silenzio."""
    name2cls = {c.name: c for c in classes}
    plan, covered = [], set()
    for stage in R1_STAGES:
        cur = [name2cls[n] for n in stage if n in name2cls]
        covered.update(c.name for c in cur)
        if cur:
            plan.append(cur)
    extra = [c for c in classes if c.name not in covered]
    if extra:
        _log("  [!] R1: specialisti fuori dalle ondate di pipeline (aggiungerli a R1_STAGES): "
             + ", ".join(c.name for c in extra))
        plan.append(extra)
    return plan


def _log(msg):
    print("[" + datetime.now().strftime("%H:%M:%S") + "] " + msg)


_MOTIVO_USCITA = {"testo": ""}   # letto dal gestore di crash del __main__: F4 vede il motivo, non «2»
_LAST_WEEKLY_OUTCOME = {}


def mandato_o_esci():
    """05/09 (criterio 5, spec §5): senza il MANDATO del PM il comitato NON parte — e lo dice
    PRIMA di costruire il blackboard e di pagare i desk, non al Capo dopo un'ora. Torna il
    mandato letto dal disco; assente/incompleto = messaggio con la causa e uscita 2."""
    from bellomberg.core import mandato_pm
    try:
        return mandato_pm.carica()
    except mandato_pm.MandatoMancante as e:
        _log("MANDATO NON DICHIARATO: " + str(e))
        _log("Il comitato non parte senza il mandato del PM: compila la pagina Mandato e Diario (F18) e rilancia.")
        _MOTIVO_USCITA["testo"] = "MANDATO NON DICHIARATO: " + str(e)[:160]
        raise SystemExit(2)


def _parallel_workers():
    """Fase 3 parallelizzazione: quanti specialisti insieme per round.
    Tarabile dal PM via .env (CONSIGLIERE_PARALLEL); 1 = sequenziale com'era.
    Default 4 (raccomandazione audit: 4-5; ogni agente ha il SUO prefisso di
    cache #200a, quindi il parallelismo non tocca il prompt caching)."""
    try:
        return max(1, int(os.getenv("CONSIGLIERE_PARALLEL", "4")))
    except Exception:
        return 4


def run_round(blackboard, round_n):
    from bellomberg.core.research_analysis import is_research_mode, research_reference, research_digest
    selected_language = capture_language(getattr(blackboard, "language", None))
    _log("=" * 60)
    _log("ROUND " + str(round_n))
    _log("=" * 60)
    blackboard.current_round = round_n
    # seed della cache scorer PRIMA dei thread: cosi' i thread non mutano le
    # CHIAVI ESTERNE di blackboard.data (solo la sotto-dict, un nome ciascuno)
    blackboard.data.setdefault("_score_cache", {})

    def _run_one(SpClass):
        name = getattr(SpClass, "name", SpClass.__name__)
        check_blocked = getattr(blackboard, "raise_if_run_blocked", None)
        if callable(check_blocked):
            check_blocked()
        should_run = getattr(blackboard, "should_run_specialist", None)
        if callable(should_run) and not should_run(name, round_n):
            return
        try:
            trade_model_ref = None
            research_ref = research_reference(blackboard) if round_n == 2 and is_research_mode(blackboard) else None
            if (getattr(blackboard, "run_scope", "weekly") == "trade_idea"
                    and not is_research_mode(blackboard)):
                if round_n == 1 and name == "fundamentals":
                    blackboard.model_phase = "building"
                    blackboard.independent_round = 0
                if round_n == 2:
                    from bellomberg.agents.trade_idea import _verified_candidate_valuations
                    refs = _verified_candidate_valuations(blackboard)
                    if len(refs) != 1:
                        raise RuntimeError("Exact usable workbook missing from final desk context")
                    trade_model_ref = {key: refs[0][key] for key in
                                       ("snapshot_id", "generation_id", "workbook_sha256")}
            with language_context(selected_language):
                sp = SpClass(blackboard)
                report = sp.run(round_n)
            if research_ref is not None:
                from bellomberg.agents.capo import _e_segnaposto
                if (research_reference(blackboard) != research_ref
                        or getattr(sp, 'run_result_status', None) != 'complete'
                        or not isinstance(report, str) or not report.strip()
                        or _e_segnaposto(report) or blackboard.read(name, 2) != report):
                    raise RuntimeError('Desk final report did not discuss the supplied sealed research')
                blackboard.data.setdefault('_desk_research_reviews', {})[name] = {
                    'round': 2, 'research_ref': research_ref, 'report_sha256': research_digest(report)}
            if trade_model_ref is not None:
                from bellomberg.agents.capo import _e_segnaposto
                from bellomberg.agents.trade_idea import _plan_digest, _verified_candidate_valuations
                refs = _verified_candidate_valuations(blackboard)
                if (len(refs) != 1 or any(refs[0][key] != value for key, value in trade_model_ref.items())
                        or getattr(sp, "run_result_status", None) != "complete"
                        or _e_segnaposto(report) or blackboard.read(name, 2) != report):
                    raise RuntimeError("Desk final report did not discuss the supplied exact workbook")
                blackboard.data.setdefault("_desk_model_reviews", {})[name] = {
                    "round": 2, "model_ref": trade_model_ref, "report_sha256": _plan_digest(report)}
            completed = getattr(blackboard, "record_specialist_completion", None)
            if callable(completed):
                completed(name, round_n, report, getattr(sp, "run_result_status", None))
        except Exception as e:
            _log("[!] " + name + " round " + str(round_n) + " failed: " + str(e))
            failed = getattr(blackboard, "record_run_failure", None)
            if callable(failed):
                failed(e, desk=name, round_n=round_n)
            try:
                blackboard.write(name, round_n, "[ERROR]: " + str(e))
            except Exception:
                pass
        if callable(check_blocked):
            check_blocked()

    classes = _classes_for_round(round_n)
    if getattr(blackboard, "run_scope", "weekly") == "trade_idea" and round_n == 1:
        # All independently researched domains are available before the writer
        # starts its actual questions and authors the common economic plan.
        classes = [cls for cls in classes if cls.name != "fundamentals"] + [
            cls for cls in classes if cls.name == "fundamentals"]
    if getattr(blackboard, "run_scope", "weekly") == "trade_idea" and round_n == 2:
        addressed = getattr(blackboard, "r2_specialists", set())
        classes = [cls for cls in SPECIALIST_ORDER if cls.name in addressed]
    if round_n == 2:
        # R2 SELETTIVO e SEQUENZIALE in ordine di pipeline (review 15/07): Quant deve
        # vedere la revisione R2 di Fundamentals e Options il verdetto R2 di Quant —
        # in parallelo un declassamento post-red-team non arriverebbe mai a valle.
        _log("R2 SELETTIVO in pipeline: " + " -> ".join(c.name for c in classes)
             + (" (repliche ai destinatari delle obiezioni)" if getattr(blackboard, "run_scope", "weekly") == "trade_idea"
                else " (macro/eventdesk/crypto chiudono in R1)"))
        for SpClass in classes:
            _run_one(SpClass)
        return

    workers = (1 if getattr(blackboard, "run_scope", "weekly") == "trade_idea"
               else _parallel_workers())
    if workers <= 1:
        # sequenziale = gia' in ordine di pipeline: chi valida vede chi propone
        for SpClass in classes:
            _run_one(SpClass)
        return
    from concurrent.futures import ThreadPoolExecutor
    if round_n == 1:
        # R1 a ONDATE di pipeline (ripipeline 15/07): dentro l'ondata in parallelo,
        # barriera tra ondate — Fundamentals vede i draft R1 di Macro/EventDesk,
        # Quant/Options vedono i candidati di Fundamentals nello stesso round.
        plan = _r1_pipeline_stages(classes)
        _log("R1 a ondate di pipeline: " + "  ->  ".join("+".join(c.name for c in stage) for stage in plan))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spec") as ex:
            for stage in plan:
                futs = [ex.submit(_run_one, SpClass) for SpClass in stage]
                for f in futs:
                    f.result()  # barriera di ondata (_run_one non propaga)
        return
    # R0 (recon) e R2 (selettivo): nessuna dipendenza interna al round -> tutti insieme.
    # NB semantica: in parallelo ogni specialista vede i report del round PRECEDENTE;
    # ask_specialist resta disponibile e best-effort durante il round.
    _log("Specialisti in parallelo: " + str(workers) + " worker (CONSIGLIERE_PARALLEL)")
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spec") as ex:
        futures = [ex.submit(_run_one, SpClass) for SpClass in classes]
        for f in futures:
            f.result()  # _run_one non propaga: .result() serve solo da barriera


def _record_capo_usage(blackboard, usage, duration_s):
    """Propagate the Capo's measured application-call count, including retries."""
    calls = usage.get("api_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 1:
        raise ValueError("Capo api_calls non consegnato o non valido: nessun conteggio inventato")
    return blackboard.record_usage("capo", 3, usage.get("model"), usage,
        duration_s=duration_s, api_calls=calls, cache_ttl=None,
        status="api_error" if usage.get("error") else "ok")


def _record_side_usage(blackboard, agent, usage):
    """Registra nel conto della run le chiamate LLM 'laterali' (#44/finding 4).

    reflection (#210) e action_table_extract (#200c) fanno UNA chiamata Sonnet vera
    ciascuna, ma non passavano da record_usage: non esistevano ne' in usage_log, ne'
    in llm_usage, ne' nel totale mostrato al PM — che si presentava COMPLETO pur
    essendo strutturalmente incompleto (fallback silenzioso, regola PM 14\07).

    usage = il dict riempito via usage_out dal modulo chiamato. status "skipped" (o
    dict vuoto) = NESSUNA chiamata partita -> niente da registrare, non e' un buco.
    Best-effort: un errore del contatore non deve MAI far cadere la run.
    """
    try:
        if not isinstance(usage, dict) or not usage:
            return None
        status = usage.get("status") or "usage_unknown"
        if status == "skipped":
            return None
        model = usage.get("model")
        if not model:
            # chiamata partita ma modello ignoto: il costo NON e' calcolabile e lo si
            # dichiara (mai uno 0,00 di comodo) -> record_usage -> model_unknown.
            status = "usage_unknown" if status == "ok" else status
        entry = blackboard.record_usage(
            agent, 1, model, usage,
            duration_s=usage.get("duration_s"),
            api_calls=int(usage.get("api_calls") or 0),
            cache_ttl=None,  # nessun prompt caching in queste due chiamate
            status=status)
        # 21/07 (minore run #45): formato costi uniforme alle righe agente — prima
        # qui usciva il float grezzo (0.013723463109396698) accanto agli "0,19 EUR".
        _c = (entry or {}).get("cost_eur")
        _c_s = ("%.2f EUR" % _c).replace(".", ",") if isinstance(_c, (int, float)) else str(_c)
        _log(agent + " usage: in=" + str(usage.get("in")) + " out=" + str(usage.get("out"))
             + " status=" + str((entry or {}).get("status"))
             + " costo=" + _c_s)
        return entry
    except Exception as e:
        _log("[!] " + str(agent) + " usage non registrato (procedo): " + str(e))
        return None


def _collect_dcf_files(start_time, valuation_results=None):
    if valuation_results is not None:
        from bellomberg.reporting.valuation_delivery import build_manifest
        return build_manifest(valuation_results, roots=[MODELS_DIR, REPORT_DIR])["attachments"]
    # audit/11 §5: niente early-return se manca models/ — scartava in silenzio anche i
    # report/VAL_*.xlsx dal glob sotto (glob su dir inesistente ritorna gia' [])
    dcf_files = []
    for path in (glob.glob(os.path.join(str(MODELS_DIR), "DCF_*.xlsx"))
                 + glob.glob(os.path.join(str(MODELS_DIR), "VAL_*.xlsx"))
                 + glob.glob(os.path.join(str(REPORT_DIR), "VAL_*.xlsx"))):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if mtime >= start_time:
                dcf_files.append(path)
        except Exception:
            continue
    # C1 16/07 (cintura oltre alla sovrascrittura giornaliera di dcf_engine): UN file
    # per ticker per run — se per qualunque via ne restano due (es. base + _FLAGGED),
    # si allega solo il piu' recente. Ticker dal nome file; fuori pattern = tenuto.
    import re as _re
    # review 16/07: stamp OPZIONALE — i canonici (VAL_TICKER.xlsx) e i loro _FLAGGED
    # devono entrare nel dedup, non finire nel 'resto' senza raggruppamento.
    _pat = _re.compile(r"^(?:VAL|DCF)_(.+?)(?:_(?:\d{8}(?:_\d{4})?|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}))?(?:_FLAGGED)?\.xlsx$")
    best = {}
    resto = []
    for path in dcf_files:
        m = _pat.match(os.path.basename(path))
        if not m:
            resto.append(path)
            continue
        k = m.group(1).upper()
        if k not in best or os.path.getmtime(path) > os.path.getmtime(best[k]):
            best[k] = path
    dedup = sorted(list(best.values()) + resto)
    if len(dedup) < len(dcf_files):
        print(f"[DCF] dedup allegati: {len(dcf_files)} file -> {len(dedup)} (uno per ticker)")
    return dedup


def _try_correlation_matrix(positions):
    if not positions:
        return None
    from bellomberg.storage.negozi_privati import carica_alias
    alias = carica_alias()
    if alias["origine"] in ("assente", "illeggibile"):
        _log("  [!] Correlation matrix skipped: alias_fonti %s: %s" % (
            alias["origine"], alias["motivo"] or "motivo n.d."))
        return None
    # Proxy SOLO per la correlazione; output rietichettato coi simboli reali.
    proxy_map = alias["alias"]["correlazione"]
    top = sorted([p for p in positions if p.get("peso_pct")],
                  key=lambda x: x["peso_pct"] or 0, reverse=True)[:8]
    reali = [str(p.get("ticker") or "").strip().upper() for p in top]
    reali = [t for t in reali if t]
    if not reali:
        return None
    data_by_real = {t: proxy_map.get(t, t) for t in reali}
    real_by_proxy = {}
    for reale, proxy in data_by_real.items():
        if proxy in real_by_proxy and real_by_proxy[proxy] != reale:
            _log("  [!] Correlation matrix skipped: reverse mapping ambiguo, %s e %s "
                 "usano lo stesso proxy %s" % (real_by_proxy[proxy], reale, proxy))
            return None
        real_by_proxy[proxy] = reale
    tickers = [data_by_real[t] for t in reali]

    def _relabel(obj):
        if isinstance(obj, dict):
            return {real_by_proxy.get(k, k): _relabel(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [real_by_proxy.get(x, x) if isinstance(x, str) else _relabel(x) for x in obj]
        return obj

    try:
        result = tool_quant_compute("correlation_matrix", tickers=tickers, period="6mo")
        if result and "matrix" in result:
            used = [p for p in real_by_proxy
                    if real_by_proxy[p] != p and (p in str(result["matrix"]) or p in tickers)]
            if used:
                _log("  Correlation: dati via proxy dichiarati " +
                     ", ".join(f"{real_by_proxy[p]}<-{p}" for p in sorted(set(used) & set(real_by_proxy))))
            return _relabel(result["matrix"])
    except Exception as e:
        _log("  [!] Correlation matrix fail: " + str(e))
    return None


def _portfolio_priming_log(portfolio):
    n = portfolio.get("n_positions", 0)
    totale = portfolio.get("totale_valore_mercato_eur")
    if totale is None:
        motivo = ", ".join(portfolio.get("fx_incomplete") or []) or "causa n.d."
        return f"  Portfolio: {n} positions, EUR n.d. (FX incompleto: {motivo})"
    return f"  Portfolio: {n} positions, EUR {totale:,.0f}"


def _send_weekly_email(attachments, body_extra="", *, delivery=None,
                       expected_hashes=None, require_all_hashes=False):
    """Il booleano del mittente e' l'esito: False non e' un invio riuscito.
    body_extra: HTML con l'esito delle valutazioni e i modelli allegati (audit 11/09)."""
    if not email_configurata():
        _log("[!] Email not configured")
        return False
    _log("Sending Bellomberg email with all attachments")
    try:
        from bellomberg.reporting.email_sender import invia_email_multi_allegati
        sent = invia_email_multi_allegati(
            pdf_paths=attachments,
            oggetto="[BELLOMBERG] Weekly Research - " + datetime.now().strftime("%d/%m/%Y"),
            body_extra=body_extra,
            **({"expected_hashes": expected_hashes if expected_hashes is not None else delivery["expected_hashes"],
                "delivery_receipt": delivery, "require_all_hashes": require_all_hashes}
               if delivery is not None else {}),
        )
        if sent is True:
            _log("Email sent (" + str(len(attachments)) + " files)")
            return True
        _log("[!] Email NON inviata: mittente ha restituito esito negativo (vedi log SMTP/allegati)")
    except Exception as e:
        _log("[!] Email error: " + str(e))
    return False


def _ensure_portfolio_valuations(blackboard, portfolio):
    from bellomberg.agents.company_research_tools import source_research_guard
    with source_research_guard(blackboard):
        return _ensure_portfolio_valuations_locked(blackboard, portfolio)


def _ensure_portfolio_valuations_locked(blackboard, portfolio):
    """Cover unrequested DB holdings after R1, without retrying any desk attempt."""
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists.base import TOOL_LOG_OUTPUT_MAX

    positions = (portfolio or {}).get("positions") or []
    tickers = list(dict.fromkeys(p["ticker"].strip().upper() for p in positions
        if isinstance(p, dict) and isinstance(p.get("ticker"), str) and p["ticker"].strip()))
    seen = {str(t).strip().upper() for t in blackboard.valuation_results}
    seen.update(str(a.get("ticker") or "").strip().upper() for a in blackboard.valuation_attempts)
    coverage = {"portfolio_tickers": tickers, "already_requested": [t for t in tickers if t in seen],
                "requested": [], "preparation_unavailable": {}}
    blackboard.data["_valuation_coverage"] = coverage
    if not tickers:
        coverage.update(status="unavailable", reason="portfolio_empty_or_unavailable")
        _log("[KO] get_valuation: portafoglio vuoto/non disponibile; nessun titolo dedotto")
        return
    state = blackboard.data.get("_valuation_preparation") or {}
    for ticker in tickers:
        if ticker in seen:
            continue
        preparer = blackboard.valuation_preparer if state.get("status") == "enabled" else None
        reason = state.get("reason") or "preparer_unavailable"
        if state.get("tickers") and ticker not in state["tickers"]:
            preparer, reason = None, "ticker_not_authorized"
        if preparer is None:
            coverage["preparation_unavailable"][ticker] = reason
        _log("[committee-orchestrator] -> get_valuation(" + ticker + ")")
        blackboard.mark_specialist_start("committee-orchestrator", blackboard.current_round)
        try:
            result = chat_tools.dispatch("get_valuation", {"ticker": ticker},
                caller="committee-orchestrator", valuation_preparer=preparer)
            payload = result.get("data", result) if isinstance(result, dict) else result
            if not isinstance(payload, dict):
                raise ValueError("get_valuation returned invalid result object")
            usability = payload.get("valuation_usability")
            if usability is not None and (not isinstance(usability, dict)
                    or type(usability.get("usable")) is not bool):
                raise ValueError("get_valuation returned invalid valuation_usability")
        except Exception as exc:
            payload = {"ok": False, "error": type(exc).__name__ + ": " + str(exc),
                       "exclude_from_action_table": True}
        payload = {**payload, "request_origin": "committee-orchestrator"}
        usable = (payload.get("valuation_usability") or {}).get("usable") is True
        if preparer is None and not usable:
            payload["preparation"] = {"status": "disabled", "reason": reason}
            payload["error"] = (str(payload.get("error") or "Valutazione incompleta")
                                + "; preparazione automatica non disponibile: " + reason)
        coverage["requested"].append(ticker)
        output = json.dumps(payload, default=str, ensure_ascii=False)
        blackboard.tool_log.append({"specialist": "committee-orchestrator", "round": blackboard.current_round,
            "tool": "get_valuation", "input": str({"ticker": ticker}),
            "time": datetime.now().strftime("%H:%M:%S"), "output": output[:TOOL_LOG_OUTPUT_MAX],
            "output_tappato": len(output) > TOOL_LOG_OUTPUT_MAX, "output_chars": len(output)})
        blackboard.record_valuation(ticker, payload, "committee-orchestrator")
        blackboard.mark_specialist_done("committee-orchestrator")
        _log("[committee-orchestrator] " + ticker + ": "
             + ("calcolata" if usable
                else "KO - " + str(payload.get("error") or "valutazione incompleta")))
    coverage["status"] = "checked"


from bellomberg.agents.trade_idea import paid_run_exclusive


def _weekly_contract(*, analysis_mode='fundamentals_research_v1'):
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    if analysis_mode not in (None, RESEARCH_ANALYSIS_MODE):
        raise ValueError('Unknown weekly analysis mode')
    from bellomberg.core.llm_client import modello_o_buco
    models = {"capo": modello_o_buco("capo"), "red_team": modello_o_buco("red_team"),
              "reflection": modello_o_buco("reflection"), "action_extractor": modello_o_buco("action_extractor")}
    for cls in SPECIALIST_ORDER:
        for round_n in (0, 1, 2):
            models[cls.name + ":" + str(round_n)] = modello_o_buco("consigliere", cls.name, round_n)
    contract = {"models": models, "roster": [cls.name for cls in SPECIALIST_ORDER],
                "r1_stages": R1_STAGES, "r2_specialists": sorted(R2_SPECIALISTS)}
    if analysis_mode is not None:
        contract['analysis_mode'] = analysis_mode
    return contract


def _weekly_terminal_heartbeat(store):
    from pathlib import Path
    from bellomberg.agents.specialists.base import scrivi_file_atomico
    path = Path(Blackboard.HEARTBEAT_PATH)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    result = store.status()
    state.update(result, run_scope="weekly", running=False,
                 message=((result.get("last_error") or result.get("first_error") or {}).get("message")
                          if result["status"] != "completed" else "Analisi e artefatti verificati"))
    ok, error = scrivi_file_atomico(str(path), json.dumps(state, ensure_ascii=False, default=str))
    if not ok:
        _log("[!] Stato finale non scritto nel heartbeat: " + str(error))


@scoped_language
@paid_run_exclusive
def run_multi_agent(*, resume_memo_id=None, delivery_only=False,
                    authorize_new_ai=False, send_email=True):
    """Normal run, explicit continuation, or artifact-only recovery of one memo."""
    from pathlib import Path
    from bellomberg.agents.weekly_lifecycle import create_run, validate_resume, recover_delivery, require_existing_database
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked, WeeklyRunStore, digest, costs_unresolved
    from bellomberg.core.request_journal import RequestJournal
    from bellomberg.core.llm_client import request_scope
    _LAST_WEEKLY_OUTCOME.clear()
    if delivery_only and resume_memo_id is None:
        raise WeeklyRunBlocked("Seleziona esplicitamente il memo da recuperare")
    from bellomberg.storage import memory_db as _memory_db
    require_existing_database(_memory_db.SQLITE_PATH)
    db = MemoryDB()  # A failed database is a hard prerequisite, before any tool/provider.
    if resume_memo_id is None:
        mandate = mandato_o_esci()
        portfolio = db.get_portfolio_summary()
        store = create_run(db, portfolio, mandate, _weekly_contract(), capture_language())
    else:
        store = WeeklyRunStore(db, resume_memo_id)
        if store.context['contract'].get('analysis_mode') is None:
            # Historical contracts remain unchanged. Continuing one must not
            # reactivate its author/preparer/compiler through the ordinary UI.
            if not delivery_only:
                raise WeeklyRunBlocked('Run legacy in archivio: consentito solo il recupero esplicito '
                    'della consegna gia persistita (delivery_only); nessuna nuova AI o generazione Excel')
            if store.get('memo_validated') is None:
                raise WeeklyRunBlocked('Memo validato assente: run legacy in archivio, '
                    'ripresa analitica ed Excel non autorizzati')
    selected = store.context["language"]
    try:
        with store.claim(mode="delivery" if delivery_only else "resume" if resume_memo_id else "new"):
            try:
                with language_context(selected):
                    if delivery_only:
                        result = recover_delivery(store, sys.modules[__name__], send_email=send_email)
                    elif resume_memo_id is not None and store.get("decisions_finalized") is not None:
                        # Idempotent second continuation: verify/recover delivery without any AI.
                        result = recover_delivery(store, sys.modules[__name__], send_email=send_email)
                    else:
                        if resume_memo_id is not None:
                            if not authorize_new_ai:
                                raise WeeklyRunBlocked("Ripresa analitica richiede autorizzazione esplicita per le fasi mancanti")
                            validate_resume(store, mandato_o_esci(),
                                _weekly_contract(analysis_mode=store.context['contract'].get('analysis_mode')))
                        journal_path = Path(db.db_path).with_name("weekly-" + store.run_id + "-requests.sqlite")
                        if store.status().get("request_journal_path") and not journal_path.is_file():
                            raise WeeklyRunBlocked("Registro richieste mancante: nessun nuovo invio autorizzabile")
                        store.update(request_journal_path=str(journal_path))
                        store.request_journal = RequestJournal(
                            journal_path,
                            run_id=store.run_id, authorization={"scope": "weekly", "memo_id": store.memo_id,
                                "context_sha256": digest(store.context), "authorized_usd": None})
                        if costs_unresolved(store.request_journal.summary()):
                            raise WeeklyRunBlocked("Richieste o costi incerti: ripresa analitica bloccata")
                        with request_scope(store.request_journal, phase="weekly"):
                            result = _run_multi_agent(store, db, send_email=send_email)
            except BaseException as exc:
                try:
                    if getattr(store, "blackboard", None) is not None:
                        store.save_snapshot(store.blackboard)
                    store.fail(exc)
                    if getattr(store, "request_journal", None) is not None:
                        store.update(request_costs=store.request_journal.summary())
                except Exception as state_error:
                    _log("[!] Persistenza errore run fallita: " + str(state_error))
                raise
    except BaseException:
        _LAST_WEEKLY_OUTCOME.update(store.status())
        _weekly_terminal_heartbeat(store)
        raise
    result = store.status()
    _weekly_terminal_heartbeat(store)
    _LAST_WEEKLY_OUTCOME.update(result)
    return result


def _run_multi_agent(store, db, *, send_email=True):
    from bellomberg.core.llm_client import request_scope
    start_time = datetime.now()
    print_banner()
    mandato_o_esci()   # 05/09 (criterio 5): niente mandato = niente run, dichiarato subito
    # log dai valori VERI (doc-fix audit/21 App. C: diceva "All agents on
    # claude-opus-4-8" ma R0 e' Sonnet dal 15/07 — il log ora non puo' mentire)
    try:
        # 05/09 (ordine PM): i modelli vivono nel .env, uno per desk; qui la base R1/R2,
        # R0 e Capo, con «n.d. (VARIABILE assente)» al posto di un nome inventato.
        from bellomberg.core.llm_client import modello_o_buco as _mob
        _log(f"Starting weekly run | R1/R2 {_mob('consigliere')} | R0 {_mob('consigliere', round_n=0)}"
             f" | Capo {_mob('capo')} | memory-aware")
        # audit 11/09 (Fable 5.1): l'intestazione diceva il modello BASE (glm-5.3-flash) mentre
        # tre desk giravano su un override per desk (glm-5.3), proprio quelli usciti a 0 char.
        # Gli override si dichiarano qui, una volta, cosi' il log non mente sul modello.
        _base = {r: _mob("consigliere", round_n=r) for r in (0, 1, 2)}
        _override = []
        for _sp in SPECIALIST_ORDER:
            for _r in (0, 1, 2):
                try:
                    _m = _mob("consigliere", getattr(_sp, "name", None), _r)
                except Exception:
                    continue
                if _m != _base.get(_r):
                    _override.append(f"{getattr(_sp, 'name', '?')} R{_r}={_m}")
        if _override:
            _log("  Override modelli per desk: " + ", ".join(_override))
    except Exception:
        _log("Starting weekly run | memory-aware (model strings non importabili)")

    # Persistence, run identity and the original book have already been verified.
    portfolio = store.context["portfolio"]
    memo_id = store.memo_id
    macro = None
    correlation_matrix = None

    # BLACKBOARD con DB + memo_id
    from bellomberg.core.research_analysis import is_research_mode, seal_research_thesis, research_reference
    from bellomberg.valuation.preparation_runtime import preparation_status_text
    if is_research_mode(store.context['contract']):
        preparation_binding = {'preparer': None, 'state': {'status': 'not_required',
            'analysis_mode': store.context['contract']['analysis_mode'],
            'reason': 'Company research produces memo/PDF without workbook preparation'}}
    else:
        from bellomberg.valuation.preparation_runtime import bind_installation_preparer
        preparation_binding = bind_installation_preparer("committee")
    bb = Blackboard(memory_db=db, memo_id=memo_id, valuation_preparer=preparation_binding["preparer"])
    bb.data["_valuation_preparation"] = preparation_binding["state"]
    _log('Analisi societaria documentata: pacchetto memo/PDF, Excel non previsto'
         if is_research_mode(store.context['contract']) else
         preparation_status_text(preparation_binding["state"], language=bb.language))
    # totale report atteso (ripipeline: R0+R1 tutti, R2 selettivo) -> heartbeat/UI
    bb.expected_reports = len(SPECIALIST_ORDER) * 2 + len(_classes_for_round(2))
    # chi replica in R2: per gli ALTRI il report R1 e' il finale e va persistito
    # come tale (v. Blackboard.write) — senza questo la memoria li perderebbe
    bb.r2_specialists = set(R2_SPECIALISTS)
    from bellomberg.agents.weekly_lifecycle import bind_blackboard
    bind_blackboard(bb, store)
    from bellomberg.agents.company_research_tools import bind_weekly_company_research
    bind_weekly_company_research(bb, store)
    bb.request_journal = store.request_journal
    _priming = store.get("priming")
    if _priming is None:
        try:
            macro = tool_get_macro_dashboard()
            _log("  Macro: " + str(len(macro.get("indicators", {}))) + " indicators")
        except Exception as exc:
            _log("  Macro non disponibile: " + str(exc))
            macro = None
        if portfolio.get("positions"):
            correlation_matrix = _try_correlation_matrix(portfolio["positions"])
        # I-20: fotografia read-only dell'archivio per i ticker realmente presenti nel DB.
        # Il contesto non e' un desk e non altera expected_reports o i round.
        try:
            from bellomberg.agents.filing_context import committee_filing_context
            _filing_tickers = [p.get("ticker") for p in (portfolio or {}).get("positions", [])
                               if isinstance(p, dict) and p.get("ticker")]
            bb.data["_filing_context"] = committee_filing_context(_filing_tickers)
        except Exception as exc:
            bb.data["_filing_context"] = ("ARCHIVIO FILING NON DISPONIBILE: "
                                          + type(exc).__name__ + ": " + str(exc)[:200])

        # HEALTH-CHECK PRE-RUN (audit/07 §3, P1): il giorno del memo #42 var_contribution
        # rispondeva "insufficient history: 0 obs" e la run e' partita comunque, senza
        # decomposizione del rischio e senza che nessuno lo sapesse. Ora i tool chiave
        # vengono pingati PRIMA dei round; i KO finiscono nel log E nel prompt del Capo
        # (stesso pattern del guardrail beta / guardie Polymarket).
        tool_health = {"ok": [], "ko": []}

        def _probe(name, fn):
            try:
                r = fn()
                if isinstance(r, dict) and r.get("error"):
                    tool_health["ko"].append(name + " -> " + str(r["error"])[:160])
                elif r is None or (hasattr(r, "__len__") and len(r) == 0):
                    tool_health["ko"].append(name + " -> risposta vuota")
                else:
                    tool_health["ok"].append(name)
                return r
            except Exception as e:
                tool_health["ko"].append(name + " -> " + type(e).__name__ + ": " + str(e)[:160])
                return None

        _log("HEALTH-CHECK pre-run dei tool del comitato")
        if portfolio and portfolio.get("n_positions"):
            tool_health["ok"].append("portfolio (" + str(portfolio["n_positions"]) + " posizioni)")
        else:
            tool_health["ko"].append("portfolio -> DB vuoto o illeggibile")
        if macro and macro.get("indicators"):
            tool_health["ok"].append("macro_dashboard (" + str(len(macro["indicators"])) + " indicatori)")
        else:
            tool_health["ko"].append("macro_dashboard -> vuoto/KO")
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_var_contribution
            _probe("var_contribution", lambda: compute_var_contribution())
        except Exception as e:
            tool_health["ko"].append("var_contribution -> import: " + str(e)[:120])
        try:
            from bellomberg.portfolio.advanced_metrics import portfolio_metrics
            _probe("portfolio_metrics", lambda: portfolio_metrics())
        except Exception as e:
            tool_health["ko"].append("portfolio_metrics -> import: " + str(e)[:120])
        try:
            from bellomberg.market_data.news_aggregator import get_feed
            _probe("news_feed", lambda: get_feed(limit=5))
        except Exception as e:
            tool_health["ko"].append("news_feed -> import: " + str(e)[:120])
        # Audit 11/09 (Fable 5.1, run 10/09 memo #53): Polymarket era irraggiungibile (TLS) per
        # tutta la run e `_tool_health.ko` restava vuoto — il tool rende un dict con count 0 e
        # `fetch_warnings`, che `_probe` legge come risposta sana. Una sonda dedicata: KO con la
        # causa quando ogni endpoint ha fallito, cosi' il Capo lo legge nell'HEALTH-CHECK.
        try:
            from bellomberg.agents.agent_tools import tool_get_polymarket_events
            _pm = tool_get_polymarket_events("fed", max_results=1)
            _pm = _pm if isinstance(_pm, dict) else {}
            _fw = [str(w) for w in (_pm.get("fetch_warnings") or [])]
            if _pm.get("error"):
                tool_health["ko"].append("polymarket -> " + str(_pm["error"])[:160])
            elif _fw and not (_pm.get("results") or []):
                tool_health["ko"].append("polymarket -> " + "; ".join(_fw)[:200])
            else:
                tool_health["ok"].append("polymarket (%d risultati)" % len(_pm.get("results") or []))
        except Exception as e:
            tool_health["ko"].append("polymarket -> " + type(e).__name__ + ": " + str(e)[:120])
        # F5 (riallineamento 23/07, audit/20): riconciliazione NAV come allarme di
        # PRIMA CLASSE — una run che parte con NAV live lontano dallo snapshot
        # ufficiale lo DICHIARA al Capo (stesso canale dei tool KO), mai zitta.
        try:
            from bellomberg.portfolio.twr_engine import build_recon_note, _load_snapshots, RECON_TOLERANCE_PCT
            _snaps = _load_snapshots()
            _nav_live = float((portfolio or {}).get("nav_total_eur") or 0)
            _rec = build_recon_note(_nav_live, _snaps)
            if _rec is None:
                tool_health["ko"].append(
                    "riconciliazione_nav -> n.d. (nessuno snapshot ufficiale in nav_snapshots)")
            elif _rec.get("breach"):
                tool_health["ko"].append(
                    "riconciliazione_nav -> FUORI TOLLERANZA: NAV live "
                    f"{_rec['nav_live_eur']:.0f} EUR vs snapshot {_rec['last_snapshot_date']} "
                    f"{_rec['last_snapshot_nav_eur']:.0f} EUR (delta {_rec['delta_pct']:+.2f}%, "
                    f"soglia {RECON_TOLERANCE_PCT}%) — dichiarare nel memo, non usare il NAV come certo")
            elif _rec.get("delta_pct") is None:
                tool_health["ko"].append(
                    "riconciliazione_nav -> delta non calcolabile (snapshot NAV a 0)")
            else:
                tool_health["ok"].append(
                    "riconciliazione_nav (delta " + f"{_rec['delta_pct']:+.2f}%" + ")")
        except Exception as e:
            tool_health["ko"].append("riconciliazione_nav -> " + type(e).__name__ + ": " + str(e)[:120])
        for _l in tool_health["ok"]:
            _log("  [OK] " + _l)
        for _l in tool_health["ko"]:
            _log("  [KO] " + _l)
        # SONDA DEI MODELLI (audit 11/09, Fable 5.1, run 10/09 memo #53): il modello del red
        # team era respinto da OpenRouter (HTTP 403, gate 18+) e lo si e' scoperto alle 16:15,
        # 45 minuti dopo lo start, a R0 e R1 gia' pagati. Una call da pochi token per ogni slug
        # distinto del .env PRIMA del Round 0: l'esito e' dichiarato (log + `_tool_health` ->
        # prompt del Capo + heartbeat) e la run NON si ferma — i desk hanno il loro modello,
        # il red team resta best-effort. Model string mai cambiate qui.
        try:
            from bellomberg.core.llm_client import sonda_modelli, righe_log_sonda, modello_o_buco as _mob2
            _richieste = [("consigliere", None, 0), ("consigliere", None, 1), ("capo", None, None),
                          ("red_team", None, None), ("reflection", None, None),
                          ("action_extractor", None, None)]
            _richieste += [("consigliere", getattr(_sp, "name", None), _rn)
                           for _sp in SPECIALIST_ORDER for _rn in (0, 1, 2)]
            _slugs = []
            for _f, _a, _r in _richieste:
                try:
                    _s = _mob2(_f, _a, _r)
                except Exception:
                    continue
                if _s and not str(_s).startswith("n.d."):
                    _slugs.append(str(_s))
            _log("SONDA modelli configurati (%d slug distinti)" % len(dict.fromkeys(_slugs)))
            with request_scope(store.request_journal, phase="model_probe", agent="_probe"):
                _esiti = sonda_modelli(_slugs)
            for _l in righe_log_sonda(_esiti):
                _log("  " + _l)
            for _s, _e in _esiti.items():
                if _e.get("usage") is not None or _e.get("request_id") is not None:
                    _probe_usage = dict(_e.get("usage") or {})
                    _probe_usage.update(model=_s, api_calls=1, duration_s=_e.get("durata_s"),
                                        cost_usd=_e.get("cost_usd"),
                                        status="ok" if _e.get("ok") else "api_error")
                    _record_side_usage(bb, "_probe:" + _s, _probe_usage)
                if not _e.get("ok"):
                    tool_health["ko"].append("modello " + _s + " -> " + str(_e.get("motivo"))[:160])
        except Exception as e:
            _log("  [!] sonda modelli non eseguita (proseguo): " + type(e).__name__ + ": " + str(e)[:120])
        _request_summary = store.request_journal.summary()
        if _request_summary["unknown_requests"]:
            raise RuntimeError("Sonda con richiesta o costo incerto: nessun nuovo dispatch; riconciliare il journal")
        bb.data["_tool_health"] = tool_health

        # FRESHNESS CHECK (audit/07 §2-3, P1 + regola PM "mai fallback, precisione"):
        # confronta i dati esterni con lo snapshot della run precedente e marca STALE
        # i valori fermi (funding +10,95% identico per 4 memo) o con osservazione
        # vecchia (il "DXY in risalita" del #42 era un FRED di 11 giorni prima).
        freshness_report = None
        try:
            from bellomberg.core.freshness import check_and_update
            _cur = {}
            for _k, _ind in ((macro or {}).get("indicators") or {}).items():
                if isinstance(_ind, dict) and _ind.get("value") is not None:
                    # prefisso = fonte dichiarata (P1 14/07: le serie native hanno
                    # 'src' ONS/Eurostat/IMF/BCB; quelle FRED restano 'fred:')
                    _src = str(_ind.get("src") or "fred").lower()
                    _cur[_src + ":" + _k] = {"value": _ind.get("value"), "obs_date": _ind.get("date")}
            try:
                from bellomberg.agents.agent_tools import tool_get_hyperliquid_intel
                _hl = tool_get_hyperliquid_intel()
                for _row in (_hl.get("top_10_perps_by_oi") or []):
                    if _row.get("asset") in ("BTC", "ETH", "SOL", "HYPE") \
                            and _row.get("funding_annualized_pct") is not None:
                        _cur["hl_funding:" + _row["asset"]] = {
                            "value": _row["funding_annualized_pct"], "obs_date": None}
            except Exception as _he:
                _log("  [!] freshness: hyperliquid non raggiungibile (" + str(_he)[:80] + ")")
            if _cur:
                freshness_report = check_and_update(_cur)
                _log("Freshness: " + str(freshness_report["checked"]) + " dati esterni, "
                     + str(len(freshness_report["stale"])) + " STALE")
                for _s in freshness_report["stale"]:
                    _log("  [STALE] " + _s)
                bb.data["_freshness"] = freshness_report
        except Exception as e:
            _log("[!] freshness check skipped: " + str(e))

        # SCOREKEEPER #190/#211 (Fase 2 loop che apprende): ricalcolo PRE-RUN in
        # puro codice dell'esito di mercato delle call passate; il blocco TRACK
        # RECORD entra nella memoria del Capo e degli specialisti via memory_db.
        # Guarded: se fallisce, le memorie usano lo snapshot precedente (o niente).
        _progress_scorecard = None
        _progress_score_error = None
        try:
            from bellomberg.agents.scorekeeper import compute_scorecard
            _sc = compute_scorecard(db=db, force=True)
            _progress_scorecard = _sc
            bb.data["_scorekeeper"] = {k: _sc[k] for k in
                                        ("computed_at", "overall", "by_action",
                                         "by_confidence", "by_specialist", "n_unmeasurable",
                                         "n_directional_candidates", "n_fetch_fail", "degraded")}
            _ov = _sc.get("overall") or {}
            _log("Scorekeeper #190: " + str(_ov.get("n", 0)) + " call misurate, hit-rate "
                 + str(_ov.get("hit_rate_pct")) + "%, edge medio "
                 + str(_ov.get("avg_edge_pct")) + "% (" + str(_sc.get("n_unmeasurable", 0))
                 + " non misurabili)")
        except Exception as e:
            _progress_score_error = type(e).__name__ + ": " + str(e)
            _log("[!] scorekeeper skipped: " + str(e))

        store.complete("priming", {"macro": macro, "correlation_matrix": correlation_matrix,
            "freshness_report": freshness_report, "tool_health": tool_health,
            "scorecard": _progress_scorecard, "score_error": _progress_score_error}, bb)
    else:
        macro = _priming["macro"]
        correlation_matrix = _priming["correlation_matrix"]
        freshness_report = _priming["freshness_report"]
        tool_health = _priming["tool_health"]
        _progress_scorecard = _priming["scorecard"]
        _progress_score_error = _priming["score_error"]

    # ROUND 0 (recon) + ROUND 1 (draft)
    for r in [0, 1]:
        store.update(phase="round_" + str(r))
        run_round(bb, r)

    # AUTO299: a desk omitting the tool must not silently omit the DB holdings.
    # Existing attempts (including failures) are never automatically retried.
    if is_research_mode(bb):
        store.update(phase='research_dossier')
        sealed = seal_research_thesis(bb, desks=[cls.name for cls in SPECIALIST_ORDER])
        if store.get('research_dossier') is None:
            store.complete('research_dossier', sealed, bb)
        elif store.get('research_dossier') != sealed:
            raise RuntimeError('Research dossier checkpoint differs from the accepted committee seal')
    elif store.get("valuation_coverage") is None:
        _ensure_portfolio_valuations(bb, portfolio)
        store.complete("valuation_coverage", {"complete": True}, bb)

    # RED TEAM (#181): TRA R1 e R2 (voce P1 "tardivo": prima girava DOPO l'R2 e
    # gli specialisti non potevano mai replicare). Attacca i draft R1; la critica
    # entra nella blackboard di R2 (whitelist) + nel DB via Blackboard.write.
    if store.get("red_team") is None:
        store.update(phase="red_team")
        try:
            from bellomberg.agents.red_team import run_red_team
            _log("=" * 60)
            _log("RED TEAM (devil's advocate) challenge sui draft R1")
            with request_scope(store.request_journal, phase="red_team", agent="_red_team", round_n=1):
                crit = run_red_team(bb, portfolio_data=portfolio, memory_db=db)
            if crit:
                _log("Red team critica: " + str(len(crit)) + " chars (visibile in R2)")
        except Exception as e:
            _log("[!] Red team skipped: " + str(e))
            bb.record_run_failure(e, desk="_red_team", round_n=1)
            raise

        bb.raise_if_run_blocked()
        from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
        _red_report = bb.data.get("_red_team", {}).get(1) or crit
        _red_reason = motivo_critica_non_utilizzabile(_red_report)
        if _red_reason or "CRITICA TRONCATA" in str(_red_report):
            raise RuntimeError(_red_reason or str(_red_report))
        red_payload = {'report': _red_report}
        if is_research_mode(bb):
            red_payload['research_ref'] = research_reference(bb)
        store.complete("red_team", red_payload, bb)
    if is_research_mode(bb) and store.get('red_team').get('research_ref') != research_reference(bb):
        raise RuntimeError('Red Team does not refer to the sealed research dossier')

    # ROUND 2 (cross-review + replica al red team)
    store.update(phase="round_2")
    run_round(bb, 2)
    if is_research_mode(bb):
        from bellomberg.agents.weekly_lifecycle import validate_research_reviews
        validate_research_reviews(bb, [cls.name for cls in _classes_for_round(2)])

    _synthesis = store.get("synthesis_context")
    if _synthesis is None:
        # MOTORE DI SIZING (#184): rischio + limiti deterministici vol x correlazione PRIMA del Capo
        risk_data = None
        sizing_context = None
        try:
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            risk_data = compute_portfolio_risk()
            if isinstance(risk_data, dict) and risk_data.get("error"):
                _log("  [!] risk metrics: " + str(risk_data.get("error"))); risk_data = None
        except Exception as e:
            _log("[!] risk metrics skipped: " + str(e))
        # REPLAY STRESS GFC per il budget #187 (best-effort: se manca, il buco e'
        # dichiarato dal sizing e il vincolo NON viene applicato — mai numeri di ripiego)
        stress_data = None
        try:
            from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
            stress_data = run_monte_carlo(horizon_days=252, n_sims=1000, method="block_bootstrap",
                                          stress_scenario="gfc_2008", seed=7)
            if isinstance(stress_data, dict) and stress_data.get("error"):
                _log("  [!] replay GFC per budget: " + str(stress_data["error"])); stress_data = None
            elif stress_data is not None:
                _sm = stress_data.get("stress_meta") or {}
                _log("Replay GFC per budget #187: window_loss "
                     + str(_sm.get("window_loss_pct")) + "% ("
                     + str(len(_sm.get("proxied") or {})) + " proxy dichiarati)"
                     + (" [FALLBACK: replay non disponibile]" if stress_data.get("stress_fallback") else ""))
        except Exception as e:
            _log("[!] replay GFC per budget skipped: " + str(e))
        try:
            from bellomberg.portfolio.sizing_engine import compute_sizing, format_for_capo
            _sz = compute_sizing(portfolio, risk_data, stress_data=stress_data)
            if _sz and not _sz.get("error"):
                sizing_context = format_for_capo(_sz)
                bb.data["_sizing"] = _sz
                _log("Sizing engine: " + str(_sz["summary"]["n_positions"]) + " nomi, dispiegabile EUR "
                     + "{:,.0f}".format(_sz["summary"]["deployable_from_cash_eur"]))
                _bud = (_sz["summary"].get("stress_var_budget") or {})
                if _bud.get("binding"):
                    _log("  [BUDGET #187] VINCOLA: capacita' ridotta a EUR "
                         + "{:,.0f}".format(_bud.get("additional_capacity_eur") or 0))
        except Exception as e:
            _log("[!] Sizing engine skipped: " + str(e))

        # GUARDRAIL BETA (13/07): verdetto di riconciliazione dei 3 motori nel contesto
        # del Capo — nel memo #42 un beta artefatto (0,04) ha deciso da solo il "no hedge".
        try:
            from bellomberg.portfolio.advanced_metrics import reconcile_betas
            _rb = reconcile_betas()
            if isinstance(_rb, dict) and _rb.get("verdict"):
                bb.data["_beta_reconcile"] = _rb
                _line = ("\n\n=== GUARDRAIL BETA (riconciliazione 3 motori) ===\n"
                         "verdetto: " + str(_rb["verdict"])
                         + " | beta: " + str(_rb.get("betas"))
                         + (" | consenso: " + str(_rb["beta_consensus"])
                            if _rb.get("beta_consensus") is not None else "")
                         + "\nREGOLA: se il verdetto e' UNRELIABLE, il beta NON e' un argomento "
                           "decisionale valido (vietati verdetti di hedge basati sul beta) "
                           "finche' non riconciliato.")
                sizing_context = (sizing_context or "") + _line
                _log("Guardrail beta: " + str(_rb["verdict"]) + " " + str(_rb.get("betas")))
        except Exception as e:
            _log("[!] guardrail beta skipped: " + str(e))

        # CRUSCOTTO SCORING (#186b): sintesi degli score deterministici per il Capo + memo
        scoring_context = None
        try:
            from bellomberg.agents.specialist_scores import format_scoreboard, collect_scoreboard
            scoring_context = format_scoreboard(bb.data.get("_score_cache"))
            _nsc = len(collect_scoreboard(bb.data.get("_score_cache")))
            if scoring_context:
                _log("Scoreboard: " + str(_nsc) + " score deterministici raccolti per il memo")
        except Exception as e:
            _log("[!] Scoreboard skipped: " + str(e))

        # HEALTH-CHECK -> prompt del Capo: i tool KO vanno DICHIARATI nel memo
        if tool_health["ko"]:
            _hline = ("\n\n=== HEALTH-CHECK PRE-RUN: TOOL NON DISPONIBILI ===\n- "
                      + "\n- ".join(tool_health["ko"])
                      + "\nREGOLA: questi dati NON hanno alimentato la run. Il memo DEVE "
                        "dichiarare esplicitamente il pezzo mancante; VIETATE affermazioni "
                        "che presuppongono quei tool (es. decomposizione VaR se "
                        "var_contribution e' KO).")
            sizing_context = (sizing_context or "") + _hline
            _log("Health-check: " + str(len(tool_health["ko"])) + " tool KO dichiarati al Capo")

        # FRESHNESS -> prompt del Capo: i dati stantii vanno dichiarati nel memo
        try:
            from bellomberg.core.freshness import format_for_capo as _fmt_fresh
            _fline = _fmt_fresh(freshness_report)
            if _fline:
                sizing_context = (sizing_context or "") + _fline
                _log("Freshness: " + str(len(freshness_report["stale"])) + " dati STALE dichiarati al Capo")
        except Exception as e:
            _log("[!] freshness inject skipped: " + str(e))

        store.complete("synthesis_context", {"risk_data": risk_data, "sizing_context": sizing_context,
                                             "scoring_context": scoring_context}, bb)
    else:
        risk_data = _synthesis["risk_data"]
        sizing_context = _synthesis["sizing_context"]
        scoring_context = _synthesis["scoring_context"]

    _capo_saved = store.get("capo")
    if _capo_saved is None:
        store.update(phase="capo")
        # CAPO synthesis (con memoria persistente)
        _log("=" * 60)
        _log("CAPO synthesis (memory-aware)")
        _log("=" * 60)
        bb.mark_specialist_start("capo", 3)
        _capo_t0 = time.perf_counter()
        if not is_research_mode(bb):
            sizing_context = (sizing_context or "") + "\n\n" + preparation_status_text(
                bb.data["_valuation_preparation"], language=bb.language)
        from bellomberg.core.llm_client import request_scope
        with request_scope(store.request_journal, phase="capo", agent="capo", round_n=3):
            memo, capo_usage = run_capo(bb, portfolio_data=portfolio, memory_db=db, sizing_context=sizing_context, scoring_context=scoring_context)
        _capo_dur = time.perf_counter() - _capo_t0
        # Il Capo entra nel conto costi come gli specialisti. cache_ttl=None: non usa
        # prompt caching (capo.py, messages.create senza cache_control).
        # Se run_capo ha restituito il dict d'errore (0/0 token), la riga nasce api_error:
        # un Capo morto si dichiara, non sparisce dai costi.
        _record_capo_usage(bb, capo_usage, _capo_dur)
        bb.mark_specialist_done("capo")

        from bellomberg.agents.weekly_lifecycle import validate_memo
        validate_memo(memo, capo_usage)
        capo_payload = {'memo': memo, 'usage': capo_usage}
        if is_research_mode(bb):
            capo_payload['research_ref'] = research_reference(bb)
        store.complete("capo", capo_payload, bb)
    else:
        memo, capo_usage = _capo_saved["memo"], _capo_saved["usage"]

    _validated = store.get("memo_validated")
    if _validated is None:
        # MEMO LINTER (audit/07 P2, v1 SOLO FLAG): check deterministici su % del NAV,
        # somma scenari, quadratura dry powder, tag [src]. Gira PRIMA del validator
        # cosi' analizza il memo pulito; numeri del Capo intoccati.
        try:
            from bellomberg.reporting.memo_linter import build_linter_block
            _lblock = build_linter_block(memo, portfolio)
            if _lblock:
                memo = memo + "\n\n" + _lblock
                _log("MEMO LINTER: " + str(_lblock.count("\n- ")) + " avvertimenti aggiunti al memo")
            else:
                _log("MEMO LINTER: nessun avvertimento")
        except Exception as e:
            _log("[!] MEMO LINTER skipped: " + str(e))

        # ACTION VALIDATOR #191 (v1 SOLO FLAG, scelta PM 13/07): appende avvertimenti al
        # memo (sizing sforato, riproposte mai eseguite); i numeri del Capo restano
        # intoccati e un guasto del validator non tocca la run.
        try:
            from bellomberg.agents.action_validator import build_validator_block
            _vblock = build_validator_block(memo, bb.data.get("_sizing"), db, exclude_memo_id=memo_id)
            if _vblock:
                memo = memo + "\n\n" + _vblock
                _log("ACTION VALIDATOR: " + str(_vblock.count("\n- ")) + " avvertimenti aggiunti al memo")
            else:
                _log("ACTION VALIDATOR: nessun avvertimento")
            # #204b HARD (Lotto C verita' dei numeri, ok PM 23/07; riprogettato dopo
            # review): qui SOLO la DETECT (blocco dichiarato nel memo/PDF) — l'APPLY
            # al registro decisioni sta DOPO extract_and_save_decisions, piu' sotto
            # (le decisioni a questo punto NON esistono ancora in DB).
            from bellomberg.agents.action_validator import detect_sanity_exclusions
            _xblock, _sanity_pairs = detect_sanity_exclusions(memo)
            if _xblock:
                memo = memo + "\n\n" + _xblock
                _log("ACTION VALIDATOR: " + str(_xblock.count("\n- ")) + " righe su modelli BLOCK dichiarate nel memo")
        except Exception as e:
            _log("[!] ACTION VALIDATOR skipped: " + str(e))
            _sanity_pairs = []

        # QUALITA' DATI nel memo (21/07, lezione run #46): i 24 STALE erano dichiarati al
        # Capo ma ASSENTI dal memo — l'obbligo di dichiarazione non puo' dipendere dalla
        # disciplina dell'LLM: come linter/validator, il blocco lo appende il CODICE.
        # (regenerate_memo non ha il freshness_report post-mortem: la' il blocco manca,
        # dichiarato in questo commento.)
        try:
            from bellomberg.core.freshness import format_for_memo as _fmt_fresh_memo
            _fblock = _fmt_fresh_memo(freshness_report)
            if _fblock:
                memo = memo + "\n\n" + _fblock
                _log("QUALITA' DATI: " + str(len(freshness_report["stale"]))
                     + " STALE dichiarati nel memo (blocco automatico)")
        except Exception as e:
            _log("[!] blocco QUALITA' DATI skipped: " + str(e))

        store.complete("memo_validated", {"memo": memo, "capo_usage": capo_usage,
                                           "sanity_pairs": _sanity_pairs}, bb)
    else:
        memo, capo_usage = _validated["memo"], _validated["capo_usage"]
        _sanity_pairs = _validated["sanity_pairs"]
    store.update(analytical_status="complete", phase="artifacts")
    # Save memo markdown archivio
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
    md_path = os.path.join(RESEARCH_NOTES_DIR,
                            "bellomberg_memo_" + str(memo_id) + ".md")
    from bellomberg.agents.weekly_lifecycle import write_frozen_text
    write_frozen_text(md_path, memo)
    _log("Markdown archive: " + md_path)

    _reflection = store.get("reflection")
    if _reflection is None:
        # REFLECTION #210 (post-run, Sonnet): lezione sintetica ancorata agli esiti
        # dello scorekeeper, salvata per il priming della PROSSIMA run. Best-effort.
        _lesson = None
        _progress_reflection_status = "unavailable"
        try:
            from bellomberg.agents.reflection import generate_lesson
            # #44/finding 4: questa chiamata Sonnet e' REALE e prima di oggi non entrava
            # nel conto della run -> il totale si presentava completo mentendo per
            # omissione (fallback silenzioso, regola PM 14\07). usage_out la fa uscire.
            # try/finally come il ramo _action_table (erano asimmetrici): _record_side_usage
            # stava DENTRO il try, quindi se generate_lesson sollevava DOPO aver bruciato i
            # token (es. _save_lessons su disco pieno) l'except sotto ingoiava tutto e quei
            # token sparivano dal conto — mentre _refl_usage, mutato in-place, li aveva gia'.
            # Spesa avvenuta e nota = spesa contata (principio contabile 1).
            _refl_usage = {}
            try:
                with request_scope(store.request_journal, phase="reflection", agent="_reflection"):
                    _lesson = generate_lesson(memo, memo_id=memo_id, usage_out=_refl_usage)
            finally:
                _record_side_usage(bb, "_reflection", _refl_usage)
            if _lesson:
                _progress_reflection_status = "generated"
                _log("Reflection #210: lezione salvata per la prossima run ("
                     + str(len(_lesson)) + " char)")
            else:
                _progress_reflection_status = "not_generated"
                _log("Reflection #210: nessuna lezione (dichiarato nel log del modulo)")
        except Exception as e:
            _log("[!] reflection skipped: " + str(e))

        store.complete("reflection", {"lesson": _lesson, "status": _progress_reflection_status}, bb)
    else:
        _lesson, _progress_reflection_status = _reflection["lesson"], _reflection["status"]

    # Blackboard JSON archive
    debug_path = md_path.replace(".md", "_blackboard.json")
    try:
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump({"data": bb.data, "tool_log": bb.tool_log, "language": bb.language,
                       "valuation_results": bb.valuation_results,
                       "valuation_attempts": bb.valuation_attempts},
                      f, indent=2, default=str)
    except Exception:
        pass

    # PDF MEMO PRINCIPALE (#183 istituzionale Aurum-style, fallback al builder classico)
    from bellomberg.agents.weekly_lifecycle import (
        restore_delivery_bundle, preserve_delivery_bundle, deliver_once, render_pdf_once, WeeklyRunBlocked)
    _artifact_bundle = restore_delivery_bundle(store, sys.modules[__name__])
    _saved_artifacts = (_artifact_bundle or {}).get("artifacts", [])
    pdf_memo_path = next((row["path"] for row in _saved_artifacts
                         if row["kind"] == "PDF" and row.get("role") == "memo"), None)
    _render_saved = store.get("render_context")
    nav_history = _render_saved.get("nav_history") if _render_saved is not None else None
    if _render_saved is not None:
        risk_data = _render_saved.get("risk_data")
    if _render_saved is None and risk_data is None:
        try:
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            risk_data = compute_portfolio_risk()
            if isinstance(risk_data, dict) and risk_data.get("error"):
                _log("  [!] risk metrics: " + str(risk_data.get("error")))
                risk_data = None
        except Exception as e:
            _log("[!] risk metrics for PDF skipped: " + str(e))
    if _render_saved is None:
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_nav_history
            nav_history = compute_nav_history()
            if isinstance(nav_history, dict) and nav_history.get("error"):
                nav_history = None
        except Exception as e:
            _log("[!] nav history for PDF skipped: " + str(e))
        store.complete("render_context", {"risk_data": risk_data, "nav_history": nav_history}, bb)
    if pdf_memo_path is None:
        try:
            from bellomberg.reporting.pdf_institutional import build_institutional_memo
            pdf_memo_path = render_pdf_once(store, sys.modules[__name__], build_institutional_memo,
                memo_markdown=memo, portfolio_data=portfolio,
                risk_data=risk_data, nav_history=nav_history,
                sizing_data=bb.data.get("_sizing"),
                scoring_data=bb.data.get("_score_cache"),
                title_date=store.context["research_started_at"].split("T")[0])
            if pdf_memo_path:
                _log("PDF MEMO (istituzionale): " + pdf_memo_path)
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            store.fail(e, phase="render_pdf")
            _log("[!] PDF istituzionale error: " + str(e))
    if not pdf_memo_path:  # fallback al builder classico (mai senza PDF)
        try:
            from bellomberg.reporting.pdf_report import build_pdf_report
            pdf_memo_path = render_pdf_once(store, sys.modules[__name__], build_pdf_report,
                memo_markdown=memo, portfolio_data=portfolio,
                macro_data=macro, options_data_dict={})
            if pdf_memo_path:
                _log("PDF MEMO (classico fallback): " + pdf_memo_path)
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            store.fail(e, phase="render_pdf_fallback")
            _log("[!] PDF memo fallback error: " + str(e))

    # PDF APPENDICE QUANT
    pdf_appendix_path = next((row["path"] for row in _saved_artifacts
                             if row["kind"] == "PDF" and row.get("role") == "appendix"), None)
    if _artifact_bundle is None:
        try:
            try:
                from bellomberg.reporting.charts_quant import build_quant_appendix_v2 as build_quant_appendix  # #178
            except ImportError:
                from bellomberg.reporting.charts_agent import build_quant_appendix
            pdf_appendix_path = render_pdf_once(store, sys.modules[__name__], build_quant_appendix, role="appendix",
                blackboard=bb, portfolio_data=portfolio,
                macro_data=macro, options_data_dict={},
                correlation_data=correlation_matrix)
            if pdf_appendix_path:
                _log("PDF APPENDICE: " + pdf_appendix_path)
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            _log("[!] PDF appendix error: " + str(e))

    # DCF EXCEL FILES
    from bellomberg.reporting.valuation_delivery import save_manifest, record_email_outcome
    from bellomberg.agents.weekly_lifecycle import build_weekly_delivery, weekly_email_body
    delivery = build_weekly_delivery(bb, roots=[MODELS_DIR, REPORT_DIR])
    delivery["memo_id"] = memo_id
    from hashlib import sha256
    delivery["memo_sha256"] = sha256(memo.encode("utf-8")).hexdigest()
    delivery_path = md_path.replace(".md", "_valuations.json")
    save_manifest(delivery_path, delivery)
    from bellomberg.agents.weekly_lifecycle import validate_delivery_manifest
    validate_delivery_manifest(delivery)
    dcf_files = delivery["attachments"]
    if dcf_files:
        _log("DCF Excel files: " + str(len(dcf_files)))

    from bellomberg.agents.weekly_lifecycle import file_receipt
    file_receipt(pdf_memo_path, kind="PDF")
    preserve_delivery_bundle(store, bb, md_path=md_path, pdf_path=pdf_memo_path,
        appendix_path=pdf_appendix_path, delivery=delivery,
        allowed_roots=[MODELS_DIR, REPORT_DIR, RESEARCH_NOTES_DIR])
    _persistence_ok = False
    # UPDATE memo in DB con paths finali + chunks ChromaDB
    if db and memo_id:
        try:
            # Update memo row con paths e full markdown
            with db._conn() as conn:
                conn.execute("""UPDATE memos SET full_markdown=?, pdf_path=?, appendix_path=?,
                                dcf_files=?, capo_tokens_in=?, capo_tokens_out=? WHERE id=?""",
                              (memo, pdf_memo_path, pdf_appendix_path,
                               json.dumps(dcf_files), capo_usage["input_tokens"],
                               capo_usage["output_tokens"], memo_id))
            # Re-embed in ChromaDB
            if db.col_memos:
                chunks = db._chunk_markdown(memo)
                if chunks:
                    # audit/11 §2: con gli stessi id chromadb add() MANTIENE il documento
                    # vecchio -> il chunk 0 restava '[IN PROGRESS]' per sempre. upsert.
                    db.col_memos.upsert(
                        documents=chunks,
                        metadatas=[{"memo_id": memo_id, "chunk_idx": i} for i in range(len(chunks))],
                        ids=["memo_" + str(memo_id) + "_chunk_" + str(i) for i in range(len(chunks))]
                    )
            # Extract & save decisions from ACTION TABLE
            # #44/finding 4: dentro c'e' una chiamata Sonnet REALE (#200c estrazione
            # strutturata) che prima non entrava nel conto della run. Registrata QUI,
            # cioe' PRIMA di save_llm_usage sotto, altrimenti finirebbe nell'heartbeat
            # ma non nel DB.
            _at_usage = {}
            try:
                with request_scope(store.request_journal, phase="action_extraction", agent="_action_table"):
                    decision_ids = db.replace_memo_decisions(memo_id, memo, usage_out=_at_usage)
                # Link newly extracted proposals to the exact generation reviewed
                # by this committee. Missing metadata is declared, never backfilled.
                try:
                    with db._conn() as conn:
                        candidates = conn.execute("SELECT id,ticker FROM decisions WHERE memo_id=?", (memo_id,)).fetchall()
                    for decision_id, ticker in candidates:
                        result = bb.valuation_results.get(str(ticker).upper())
                        if result and result.get("snapshot_id"):
                            db.link_valuation_snapshot(result["snapshot_id"], generation_id=result["generation_id"],
                                                       decision_id=decision_id)
                except Exception as exc:
                    _log("[!] Collegamento snapshot valutazione/decisione non salvato: " + str(exc))
                    raise
                _log("Decisions extracted from ACTION TABLE: " + str(len(decision_ids)))
            finally:
                _record_side_usage(bb, "_action_table", _at_usage)
            # #204b HARD fase APPLY (dopo il salvataggio: ora le righe ESISTONO)
            try:
                if _sanity_pairs:
                    from bellomberg.agents.action_validator import apply_sanity_exclusions
                    _n_x = apply_sanity_exclusions(db, memo_id, _sanity_pairs)
                    _log("ACTION VALIDATOR: esclusioni HARD applicate al registro: "
                         + str(_n_x) + "/" + str(len(_sanity_pairs)))
                    if _n_x < len(_sanity_pairs):
                        raise RuntimeError("Esclusioni HARD non confermate nel registro: "
                                           + str(len(_sanity_pairs) - _n_x))
            except Exception as _xe:
                _log("[!] esclusioni HARD non applicate: " + str(_xe))
                raise
            store.complete("decisions_finalized", {"ids": decision_ids}, bb)
            _persistence_ok = True
        except Exception as e:
            _log("[!] DB memo finalize failed: " + str(e))
            raise
        # Consumo LLM riga per riga (agente+round). Try separato: le colonne
        # memos.capo_tokens_in/out restano (le leggono altri), questo e' il dettaglio.
        try:
            from bellomberg.agents.weekly_lifecycle import persist_usage_once
            persist_usage_once(store, bb)
            _log("LLM usage rows saved: " + str(len(bb.usage_log)))
        except Exception as e:
            _log("[!] save_llm_usage failed: " + str(e))
            raise

    # EMAIL
    all_attachments = []
    if pdf_memo_path: all_attachments.append(pdf_memo_path)
    if pdf_appendix_path: all_attachments.append(pdf_appendix_path)
    all_attachments.extend(dcf_files)
    # Audit 11/09 (Fable 5.1): il corpo dell'email dichiara OGNI valutazione chiesta dai desk
    # (FV o n.d. col motivo) e se manca l'Excel lo dice — prima il piede prometteva "DCF
    # Excel models" anche con zero .xlsx e il FV n.d. restava solo dentro il PDF.
    try:
        _corpo_email = weekly_email_body(bb, delivery)
    except Exception as _ce:
        _corpo_email = ("<p>[esito valutazioni non costruito: %s: %s]</p>"
                        % (type(_ce).__name__, str(_ce)[:120]))
        _log("[!] corpo email valutazioni non costruito: " + type(_ce).__name__ + ": " + str(_ce)[:120])
    sent = deliver_once(store, sys.modules[__name__], all_attachments,
        body_extra=_corpo_email, delivery=delivery, send_email=send_email)
    record_email_outcome(delivery, sent)
    save_manifest(delivery_path, delivery)

    from bellomberg.agents.weekly_lifecycle import finish_status
    outcome = finish_status(store, bb, pdf_path=pdf_memo_path, md_path=md_path,
        appendix_path=pdf_appendix_path, delivery=delivery, delivery_path=delivery_path,
        persistence_ok=_persistence_ok, sent=sent)
    # Progressi: conserva la misura già acquisita nella run. Nessun ricalcolo,
    # retrodatazione o ulteriore chiamata LLM. Schema assente = buco dichiarato.
    try:
        from bellomberg.agents.score_history import record_completed_run
        _progress_result = record_completed_run(
            db, bb, scorecard=_progress_scorecard, score_error=_progress_score_error,
            lesson=_lesson, reflection_status=_progress_reflection_status)
        _log("Progressi agenti: " + _progress_result["reason"])
    except Exception as e:
        _log("[!] Progressi agenti NON registrati: " + type(e).__name__ + ": " + str(e))
    elapsed = (datetime.now() - start_time).total_seconds()
    _log("=" * 60)
    _log(BRAND_NAME + " v" + VERSION + " - " + outcome["status"].upper() + " in " + str(int(elapsed)) + "s")
    _log("Capo tokens: in=" + str(capo_usage["input_tokens"]) + " out=" + str(capo_usage["output_tokens"]))
    # Costo della run: UN SOLO PUNTO DI VERITA'. Prima qui si RICALCOLAVA il totale con
    # una regola propria (somma per entry) mentre la UI leggeva quello del blackboard
    # (somma per agente): sulla stessa run 0,58 EUR a schermo e 1,12 EUR nel log, senza
    # una riga che spiegasse il delta. Ora il log NON calcola piu' nulla: chiede a
    # bb._usage_aggregates() lo STESSO totale che finisce nell'heartbeat e nella UI, e
    # lo stampa. Se le due cifre divergeranno ancora sara' un bug dell'aggregatore, non
    # due contabilita' parallele. Lock preso (RLock) per uno snapshot consistente.
    try:
        from bellomberg.core.llm_pricing import format_eur
        with bb._lock:
            _by, _total = bb._usage_aggregates()
        _unpriced = list(_total.get("unpriced_agents") or [])
        _errors = list(_total.get("error_agents") or [])
        # partial e' la chiave in contratto; se assente, l'informazione equivalente e'
        # la presenza di non prezzabili (v2: partial <=> almeno una entry senza costo).
        _partial = _total.get("partial", bool(_unpriced))
        _line = "Costo run: " + format_eur(_total.get("cost_eur"))
        if _partial:
            _line += " (PARZIALE: e' un MINIMO, non il costo pieno)"
        if _unpriced:
            _line += " - non prezzabili: " + ", ".join(str(a) for a in _unpriced)
        if _errors:
            # lista SEPARATA: "non so quanto" != "e' andato KO"
            _line += " - in errore: " + ", ".join(str(a) for a in _errors)
        _line += " [FX: " + str(_total.get("fx_source")) + "]"
        _log(_line)
    except Exception as e:
        # Best-effort: il buco si DICHIARA, ma la run finisce lo stesso.
        _log("[!] Costo run non calcolabile (aggregazione fallita): " + str(e))
    _log("Memo ID in DB: #" + str(memo_id))
    _log("Decisioni auto-estratte salvate nel DB (tabella decisions): GET /decisions o la pagina Decisions dell'app")
    return outcome


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run or explicitly recover Consigliere")
    parser.add_argument("--resume-memo-id", type=int)
    parser.add_argument("--delivery-only", action="store_true")
    parser.add_argument("--authorize-new-ai", action="store_true")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--outcome")
    parser.add_argument("--task-id")
    args = parser.parse_args()

    def _write_cli_outcome(outcome):
        if args.outcome:
            from bellomberg.agents.specialists.base import scrivi_file_atomico
            ok, error = scrivi_file_atomico(args.outcome, json.dumps(
                {**outcome, "task_id": args.task_id}, ensure_ascii=False, default=str))
            if not ok:
                raise RuntimeError("Esito finale non persistito: " + str(error))
    try:
        outcome = run_multi_agent(resume_memo_id=args.resume_memo_id, delivery_only=args.delivery_only,
                                   authorize_new_ai=args.authorize_new_ai, send_email=not args.no_email)
        _write_cli_outcome(outcome)
        if outcome.get("status") != "completed":
            raise SystemExit(2)
    except BaseException as e:
        _write_cli_outcome(_LAST_WEEKLY_OUTCOME or {"status": "failed", "error": str(e),
            "analytical_status": "incomplete", "artifact_status": "unknown", "delivery_status": "not_attempted"})
        if not _LAST_WEEKLY_OUTCOME:
            # Heartbeat ONESTO anche su crash (bug PM 15/07): senza questo, una run
            # morta a meta' lascia current_run.json su "running: true" per sempre e
            # Agents Live mostra una run fantasma a ogni apertura dell'app.
            try:
                # 27/08: stessa via atomica del heartbeat (temporaneo + os.replace):
                # anche qui un open("w") diretto poteva lasciare al lettore un file
                # a meta' (review del lotto heartbeat). Blocco __main__: non coperto
                # da test, dichiarato.
                from bellomberg.agents.specialists.base import Blackboard as _B, scrivi_file_atomico as _sfa
                _ok, _err = _sfa(_B.HEARTBEAT_PATH, json.dumps({
                    "running": False,
                    "message": "Run TERMINATA con errore (vedi data/consigliere_run.log): "
                               + ((_MOTIVO_USCITA["testo"] if isinstance(e, SystemExit) and _MOTIVO_USCITA["testo"]
                                   else str(e))[:200]),
                    "finished_at": datetime.now().isoformat(timespec="seconds"), "status": "failed"}))
                if not _ok:
                    # l'esito non si ignora (review 27/08): un heartbeat di crash non
                    # scritto lascia F4 su «running: true» — almeno lo dice il log
                    print("[!] heartbeat di crash NON scritto (F4 restera' su running): "
                          + str(_err), flush=True)
            except Exception:
                pass
        raise  # rc != 0 preservato per il watchdog della API
