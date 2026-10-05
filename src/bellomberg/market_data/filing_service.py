"""Orchestrazione I-20: profili revisionati, run persistenti, giudizio e indice derivato."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

from bellomberg.market_data.filing_judgment import citation_catalog, judge_filing

MAX_INDEX_CHUNKS = 200
MAX_INDEX_TEXT = 2000
CONTROLLO_LEGGERO = "controllato, nessun deposito nuovo"
GIORNI_RIFERIMENTO_NON_OK = 7
_PREDEFINITA = object()
_NON_CALCOLATA = object()  # impronta gia' tentata e fallita: execute non ritenta
# Vincolo dell'utente: AI solo da pulsante esplicito. I run periodici (run_due,
# run_programmato, pre-run del Consigliere) non chiamano mai il giudizio qualitativo.
SOLO_PULSANTE = "giudizio AI solo da pulsante (Verifica ora)"
# 2: modificati abbinati nei buchi tra blocchi uguali (filing_diff), citazioni rinumerate.
VERSIONE_GIUDIZIO = 2


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _evidence_key(run, result, judge_identity="disabled"):
    pair = result.get("coppia")
    if not pair:
        return None
    try:
        diff = result.get("confronto_corrente") or result.get("confronto_storico") or {}
        coverage = result.get("copertura") or {}
        freshness = result.get("freschezza") or {}
        raw = {"before": pair["prima"]["sha256"], "after": pair["dopo"]["sha256"],
               "profile": run["profile_sha256"], "version": run["profile_version"],
               "document_language": run["profile"]["lingua"],
               "judgment_language": run["judgment_language"], "judgment_version": VERSIONE_GIUDIZIO,
               "judge": judge_identity,
               "context": {"pair_scope": pair.get("ambito"), "pipeline_status": result.get("stato"),
                           "diff_status": diff.get("stato"), "historical": bool(result.get("confronto_storico")),
                           "coverage_status": coverage.get("stato"), "coverage_limits": coverage.get("limiti"),
                           "latest_unverified": result.get("ultimo_non_verificato"),
                           "freshness_status": freshness.get("stato")}}
        if any(not isinstance(v, str) or not v for v in (raw["before"], raw["after"], raw["document_language"], raw["judgment_language"])):
            return None
        return hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest()
    except (KeyError, TypeError):
        return None


def default_indexer(run, result, judgment, *, chroma_path=None):
    """Chroma e' solo indice ricostruibile; SQLite conserva l'evidenza intera."""
    if chroma_path is None:
        raise ValueError("chroma_path esplicito obbligatorio: non usare il Chroma vivo implicitamente")
    diff = result.get("confronto_corrente") or result.get("confronto_storico") or {}
    cited = citation_catalog(result) if diff.get("stato") in ("ok", "parziale") else {}
    findings = judgment.get("findings") or []
    if not cited and not findings:
        return {"status": "skipped", "reason": "nessun estratto o finding da indicizzare"}
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(path=str(chroma_path),
                                       settings=Settings(anonymized_telemetry=False, allow_reset=False))
    collection = client.get_or_create_collection("filing_diffs")
    ids, documents, metadata = [], [], []
    total = len(cited) + len(findings)
    remaining = MAX_INDEX_CHUNKS
    text_truncated = 0
    for citation_id, item in cited.items():
        if remaining <= 0:
            break
        remaining -= 1
        ids.append(f"filing-diff-{run['id']}-{citation_id}")
        documents.append(item["testo"][:MAX_INDEX_TEXT])
        text_truncated += len(item["testo"]) > MAX_INDEX_TEXT
        metadata.append({"kind": "diff", "ticker": run["ticker"], "run_id": int(run["id"]),
                         "citation_id": citation_id, "url": item["url"],
                         "sha256": item["sha256"], "offset_start": int(item["inizio"]),
                         "offset_end": int(item["fine"]),
                         "evidence_key": run.get("evidence_key") or "n.d."})
    for n, finding in enumerate(findings):
        if remaining <= 0:
            break
        remaining -= 1
        ids.append(f"filing-judgment-{run['id']}-{n}")
        document = finding["category"] + ": " + finding["assessment"]
        documents.append(document[:MAX_INDEX_TEXT])
        text_truncated += len(document) > MAX_INDEX_TEXT
        metadata.append({"kind": "judgment", "ticker": run["ticker"], "run_id": int(run["id"]),
                         "citations": ",".join(finding["citations"]),
                         "evidence_key": run.get("evidence_key") or "n.d."})
    collection.upsert(ids=ids, documents=documents, metadatas=metadata)
    truncated = total > len(ids) or text_truncated > 0
    return {"status": "parziale" if truncated else "ok",
            "reason": "indice limitato: chunk o testi troncati; SQLite conserva contenuto intero" if truncated else None,
            "count": len(ids), "total_chunks": total, "truncated_text_chunks": text_truncated,
            "diff_chunks": sum(m["kind"] == "diff" for m in metadata),
            "judgment_chunks": sum(m["kind"] == "judgment" for m in metadata)}


class FilingService:
    def __init__(self, store, archive_root, pipeline=None, judge=None, indexer=None, impronta_fn=_PREDEFINITA):
        self.store = store
        self.archive_root = Path(archive_root)
        if not self.archive_root.is_absolute():
            raise ValueError("archive_root deve essere assoluto")
        if impronta_fn is _PREDEFINITA:
            # Con pipeline iniettata (test, strumenti) niente rete SEC implicita per l'impronta.
            from bellomberg.market_data.filing_impronta import impronta_depositi
            impronta_fn = impronta_depositi if pipeline is None else None
        self.impronta_fn = impronta_fn
        if pipeline is None:
            from bellomberg.market_data.filing_numeri import numeri_per_coppia
            from bellomberg.market_data.filing_pipeline import esegui_profilo
            pipeline = partial(esegui_profilo, numeri_fn=numeri_per_coppia)
        self.pipeline = pipeline
        self.judge = judge or judge_filing
        self._default_judge = judge is None
        self.indexer = indexer if indexer is not None else partial(
            default_indexer, chroma_path=Path(store.db_path).resolve().parent / "chroma")

    def queue(self, ticker, trigger="manual", language=None):
        """Claim persistente, atomico. Il profilo e' congelato nel run queued."""
        return self.store.start_run(ticker, trigger, language=language)

    def _impronta(self, profilo, riferimento=None):
        """Impronta dei depositi o None (assente o non calcolabile: run completo)."""
        if self.impronta_fn is None:
            return None
        try:
            return self.impronta_fn(profilo, riferimento=riferimento)
        except Exception:
            return _NON_CALCOLATA

    def execute(self, run_id, impronta=None):
        run = self.store.claim_execution(run_id)
        result = None
        judgment = {"status": "skipped", "findings": [], "reason": "giudizio qualitativo disabilitato", "model": None, "usage": None}
        index = {"status": "skipped", "reason": "nessun estratto indicizzabile"}
        evidence_key = None
        try:
            archive = self.archive_root / run["ticker"] / "documents"
            archive.mkdir(parents=True, exist_ok=True)
            if impronta is None:  # prima della pipeline: un deposito arrivato durante il run resta "nuovo"
                impronta = self._impronta(run["profile"])
            result = self.pipeline(run["profile"], archivio=archive)
            if not isinstance(result, dict) or result.get("stato") not in ("ok", "parziale", "errore", "non_disponibile"):
                raise ValueError("pipeline: risultato o stato non valido")
            if impronta is not None and impronta is not _NON_CALCOLATA:
                result = {**result, "impronta_depositi": impronta}
            evidence_key = _evidence_key(run, result)
            if run["qualitative_enabled"] and run["trigger"] != "manual":
                # Mai il giudice qui: si riusa solo un giudizio ok/parziale gia' chiesto dal pulsante.
                judgment = {"status": "skipped", "findings": [], "reason": SOLO_PULSANTE, "model": None, "usage": None}
                diff = result.get("confronto_corrente") or result.get("confronto_storico")
                if diff and diff.get("stato") in ("ok", "parziale") and diff.get("cambiamenti"):
                    try:
                        identita = "injected"
                        if self._default_judge:
                            from bellomberg.core.llm_client import modello
                            identita = modello("action_extractor")  # sola lettura della configurazione
                        chiave = _evidence_key(run, result, identita)
                        previous = self.store.find_evidence(chiave) if chiave else None
                    except Exception:
                        previous = None
                    if previous and previous["judgment"]["status"] in ("ok", "parziale") and all(
                            f.get("citations") and set(f["citations"]) <= set(citation_catalog(result))
                            for f in previous["judgment"].get("findings", [])):
                        evidence_key = chiave
                        judgment = {**previous["judgment"], "reused_from_run": previous["id"]}
            elif run["qualitative_enabled"]:
                diff = result.get("confronto_corrente") or result.get("confronto_storico")
                if not diff or diff.get("stato") not in ("ok", "parziale"):
                    judgment = {"status": "skipped", "findings": [], "reason": "diff non confrontabile", "model": None, "usage": None}
                elif not diff.get("cambiamenti"):
                    judgment = {"status": "skipped", "findings": [], "reason": "nessun cambiamento testuale", "model": None, "usage": None}
                else:
                    judge_model = None
                    if self._default_judge:
                        from bellomberg.core.llm_client import modello
                        judge_model = modello("action_extractor")
                    evidence_key = _evidence_key(run, result, judge_model or "injected")
                    previous = self.store.find_evidence(evidence_key) if evidence_key else None
                    if previous and previous["judgment"]["status"] in ("ok", "parziale"):
                        judgment = {**previous["judgment"], "reused_from_run": previous["id"]}
                    else:
                        judgment = (self.judge(result, model=judge_model, language=run["judgment_language"])
                                    if self._default_judge else self.judge(result))
                        if not isinstance(judgment, dict) or judgment.get("status") not in ("ok", "parziale", "errore", "skipped"):
                            raise ValueError("judge: risultato non valido")
                    available = set(citation_catalog(result))
                    for finding in judgment.get("findings", []):
                        if not finding.get("citations") or any(c not in available for c in finding["citations"]):
                            raise ValueError("judge: citation_id assente dall'evidenza corrente")
            try:
                index = self.indexer({**run, "evidence_key": evidence_key}, result, judgment)
                if not isinstance(index, dict) or index.get("status") not in ("ok", "parziale", "skipped", "errore"):
                    raise ValueError("indexer: risultato non valido")
            except Exception as exc:
                index = {"status": "errore", "reason": f"{type(exc).__name__}: {exc}"}
            status = result["stato"]
            # Un indice ausiliario solo limitato (il contenuto intero resta in SQLite)
            # non rende parziale il confronto: il limite si dichiara nel motivo.
            if judgment["status"] == "errore" or index["status"] == "errore":
                status = "parziale" if status == "ok" else status
            motivi = [str(x) for x in result.get("motivi", [])[:5]]
            if index["status"] in ("errore", "parziale") and index.get("reason"):
                motivi.append(f"indice di ricerca {index['status']}: {index['reason']}")
            reason = "; ".join(motivi) or None
            return self.store.finish_run(run_id, status=status, reason=reason, evidence_key=evidence_key,
                                         result=result, judgment=judgment, index=index)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            try:
                _canonical(result)
            except (TypeError, ValueError, OverflowError):
                result = None
            return self.store.finish_run(run_id, status="errore", reason=reason, evidence_key=evidence_key,
                                         result=result, judgment={"status": "errore", "findings": [], "reason": reason,
                                                                   "model": None, "usage": None}, index=index)

    def run(self, ticker, trigger="manual"):
        return self.execute(self.queue(ticker, trigger)["id"])

    @staticmethod
    def _riferimento_valido(ultimo, now=None):
        """Riferimento valido: run completo senza guasti transitori (fonte in errore, indice 6-K
        illeggibile, download interrotto) in nessuna variante. L'incompletezza stabile (rettifica
        /A esclusa, max_documenti) non forza il run completo: con le stesse impronte vale come
        ogni non-ok, cioe' solo se recente (7 giorni)."""
        from bellomberg.market_data.filing_pipeline import incompleto_transitorio
        if not ultimo or ultimo["status"] not in ("ok", "parziale", "non_disponibile"):
            return False
        result, judgment = ultimo.get("result") or {}, ultimo.get("judgment") or {}
        # Varianti dei run precedenti senza "transitorio": vale "completo" (prudente).
        if incompleto_transitorio(result) or any(v.get("transitorio", not v.get("completo"))
                                                 for v in result.get("varianti") or []):
            return False  # lo stato del run e' quello della variante primaria: le altre vanno controllate
        if ultimo["status"] == "ok":
            return True
        if judgment.get("status") == "errore" or (ultimo.get("index") or {}).get("status") == "errore":
            return False
        try:  # data malformata o senza fuso: non e' un riferimento (mai eccezioni con il run in coda)
            fatto = datetime.fromisoformat(ultimo["finished_at"])
            return (fatto.tzinfo is not None and (now or datetime.now(timezone.utc)) - fatto
                    <= timedelta(days=GIORNI_RIFERIMENTO_NON_OK))
        except (TypeError, ValueError):
            return False

    def run_programmato(self, ticker):
        """Run periodico: senza depositi nuovi dall'ultimo run completo registra solo il controllo.

        Run completo se c'e' un deposito nuovo, se il profilo e' cambiato, se l'ultimo
        run completo non e' un riferimento valido o se l'impronta non e' calcolabile.
        RunAlreadyActive/RunNotDue di start_run si propagano.
        """
        from bellomberg.market_data.filing_impronta import stessi_depositi
        run = self.queue(ticker, "scheduled")
        if self.impronta_fn is None:
            return self.execute(run["id"])
        try:
            # Un run in errore non e' un riferimento: vale l'ultimo run completo senza errori
            # (sha del profilo, completezza e regola dei 7 giorni restano controllati sotto).
            ultimo = self.store.ultimo_run_completo(ticker, senza_errori=True)
            recenti = [r for r in self.store.list_runs(ticker, limit=2) if r["id"] != run["id"]]
        except Exception:  # il run e' gia' in coda: mai lasciarlo orfano
            return self.execute(run["id"])
        rif = ((ultimo or {}).get("result") or {}).get("impronta_depositi")
        # Per sondare basta l'ultimo controllo leggero dopo il run completo: ha gia' esaminato i 6-K piu' nuovi.
        sonda = rif
        if ultimo and recenti and (recenti[0].get("result") or {}).get("run_riferimento") == ultimo["id"]:
            sonda = recenti[0]["result"].get("impronta_depositi") or rif
        impronta = self._impronta(run["profile"], riferimento=sonda)
        if (impronta is not None and impronta is not _NON_CALCOLATA and stessi_depositi(rif, impronta)
                and ultimo["profile_sha256"] == run["profile_sha256"] and self._riferimento_valido(ultimo)):
            self.store.claim_execution(run["id"])
            return self.store.finish_run(run["id"], status="skipped", reason=CONTROLLO_LEGGERO, result={
                "controllo_leggero": True, "impronta_depositi": impronta, "run_riferimento": ultimo["id"]})
        return self.execute(run["id"], impronta=impronta)

    def recupera_orfani(self, *, eta_max_ore=2, now=None):
        """Run rimasti 'queued/running' oltre eta_max_ore: processo morto. Recovery esplicito."""
        now = now or datetime.now(timezone.utc)
        recuperati = []
        for r in self.store.active_runs():
            if datetime.fromisoformat(r["started_at"]) + timedelta(hours=eta_max_ore) <= now:
                try:
                    self.store.recover_run(r["id"], f"run orfano oltre {eta_max_ore} h (processo terminato)")
                    recuperati.append(r["id"])
                except RuntimeError:
                    pass  # concluso nel frattempo
        return recuperati

    def run_due(self, now=None, *, tickers=None, max_workers=1, escludi=None, stop_event=None):
        """Run periodici dei profili dovuti; `escludi` = titoli esclusi dal controllo dall'utente.

        `stop_event` (spegnimento del backend, REV_G2b A2): nessun titolo nuovo parte, quelli in
        corso non si attendono e tornano «interrotto». Thread DAEMON propri, non ThreadPoolExecutor:
        i thread di concurrent.futures si attendono all'uscita dell'interprete e un download appeso
        teneva vivo il processo (misura: 43 s dopo stop())."""
        import queue
        import threading
        from bellomberg.storage.filing_store import RunAlreadyActive, RunNotDue
        dovuti = [d["ticker"] for d in self.store.next_due(now)
                  if (tickers is None or d["ticker"] in tickers) and d["ticker"] not in (escludi or ())]

        def uno(ticker):
            try:
                return self.run_programmato(ticker)
            except (RunAlreadyActive, RunNotDue) as exc:
                return {"ticker": ticker, "status": "skipped", "reason": str(exc)}
            except Exception as exc:
                return {"ticker": ticker, "status": "errore", "reason": f"{type(exc).__name__}: {exc}"}

        def fermato():
            return stop_event is not None and stop_event.is_set()

        def interrotto(ticker):
            return {"ticker": ticker, "status": "interrotto",
                    "reason": "spegnimento del backend: controllo non eseguito o non atteso"}

        if max_workers <= 1:
            return [interrotto(t) if fermato() else uno(t) for t in dovuti]
        esiti = [None] * len(dovuti)
        coda = queue.Queue()
        for voce in enumerate(dovuti):
            coda.put(voce)

        def lavoratore():
            while not fermato():
                try:
                    i, t = coda.get_nowait()
                except queue.Empty:
                    return
                esiti[i] = uno(t)

        fili = [threading.Thread(target=lavoratore, name=f"filing-due-{n}", daemon=True)
                for n in range(min(max_workers, len(dovuti)))]
        for f in fili:
            f.start()
        while any(f.is_alive() for f in fili) and not fermato():
            for f in fili:
                f.join(0.2)
        return [e if e is not None else interrotto(t) for e, t in zip(esiti, dovuti)]

    def status(self, ticker):
        p = self.store.get_profile(ticker)
        if p:
            p = {**p, "next_due_at": self.store.next_due_at(ticker)}
        runs = self.store.list_runs(ticker)
        active = next((r for r in runs if r["status"] in ("queued", "running")), None)
        latest = runs[0] if runs else None
        # Stessa selezione della scheda del Consigliere: il run del confronto descrive lo stato;
        # controllo leggero ed errore successivo si dichiarano a parte.
        completo, errore = self.store.confronto_ed_errore(ticker)
        controllo = None
        if latest and self._leggero(latest):
            # Ultimo run = controllo leggero: lo stato e' quello del run del confronto, il controllo
            # si dichiara a parte. Un ultimo run in errore resta invece lo stato (errore visibile).
            controllo = {"at": latest["finished_at"] or latest["started_at"], "esito": latest["reason"]}
            latest = completo or latest
        return {"ticker": ticker, "profile": p, "runs": [self._summary(r) for r in runs],
                "active_run": self._summary(active) if active else None,
                "status": latest["status"] if latest else ("ready" if p else "non_disponibile"),
                "reason": latest["reason"] if latest else (None if p else "profilo assente"),
                "last_attempt": latest["started_at"] if latest else None,
                "ultimo_completo": self._summary(completo), "ultimo_controllo": controllo,
                "ultimo_errore": ({"id": errore["id"], "at": errore["finished_at"], "reason": errore["reason"]}
                                  if errore else None)}

    @staticmethod
    def _leggero(run):
        return bool((run.get("result") or {}).get("controllo_leggero"))

    @staticmethod
    def _summary(run):
        if run is None:
            return None
        return {**{k: run[k] for k in ("id", "ticker", "status", "reason", "started_at", "finished_at", "trigger", "profile_version", "judgment_language")},
                "controllo_leggero": FilingService._leggero(run)}

    def detail(self, run_id):
        return self.store.get_run(run_id)
