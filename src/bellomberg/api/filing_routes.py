"""Session protected filing archive and explicit refresh endpoints."""
import logging
import ipaddress
import re
import sqlite3
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query

from bellomberg.core.api_presentation import PresentationJSONResponse
from bellomberg.core.language import text as _text


def default_service():
    from bellomberg.core.paths import DATA_DIR, SQLITE_PATH
    from bellomberg.market_data.filing_service import FilingService
    from bellomberg.storage.filing_store import FilingStore
    return FilingService(FilingStore(SQLITE_PATH), DATA_DIR / "filing_archive")


def _service(factory):
    try:
        return factory()
    except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        raise HTTPException(503, _text(
            "Archivio filing non disponibile: " + str(exc),
            "Filing archive unavailable: " + str(exc))) from exc


def _ticker(value):
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,29}", value):
        raise HTTPException(422, _text("Ticker non valido", "Invalid ticker"))
    return value


def _preferenze():
    from bellomberg.storage import filing_preferenze
    try:
        return filing_preferenze.carica()
    except ValueError as exc:
        raise HTTPException(503, _text("Preferenze filing illeggibili: " + str(exc),
                                       "Filing preferences unreadable: " + str(exc))) from exc


def _tickers_portafoglio_default():
    from bellomberg.storage.memory_db import MemoryDB
    return [p["ticker"] for p in MemoryDB().get_portfolio()]


_log = logging.getLogger(__name__)


def _ritenta_6k_con(attivazione, service, ticker):
    """Profili 20-F automatici: ritenta la variante 6-K; l'esito va nel log, mai in silenzio."""
    try:
        esito = attivazione.completa_6k(service.store, ticker)
    except Exception as exc:
        esito = {"esito": "errore", "motivo": f"{type(exc).__name__}: {exc}"}
    if esito.get("esito") not in ("non_applicabile", "aggiunto"):
        _log.warning("filing %s: variante 6-K non aggiunta (%s): %s", ticker, esito.get("esito"), esito.get("motivo"))
    return esito


def _url_ir(valore):
    """URL esplicito di un documento o di una pagina IR: http(s), host pubblico, senza credenziali.
    Il DNS si verifica al download (public_only)."""
    if not isinstance(valore, str) or not valore.strip() or len(valore) > 2000:
        raise HTTPException(422, _text("URL mancante o troppo lungo", "Missing or too long URL"))
    valore = valore.strip()
    try:
        parti = urlsplit(valore)
        host = (parti.hostname or "").lower()
        parti.port
    except ValueError:
        raise HTTPException(422, _text("URL non valido", "Invalid URL"))
    if parti.scheme not in ("http", "https") or not host or parti.username is not None or parti.password is not None:
        raise HTTPException(422, _text("URL non valido: serve http(s), senza credenziali",
                                       "Invalid URL: http(s) without credentials required"))
    try:
        pubblico = ipaddress.ip_address(host).is_global
    except ValueError:
        pubblico = "." in host and not host.endswith((".localhost", ".local"))
    if not pubblico:
        raise HTTPException(422, _text("URL non valido: host non pubblico", "Invalid URL: non-public host"))
    return valore


def _nome_default(ticker):
    from bellomberg.market_data.filing_identita import nome_emittente
    try:
        return nome_emittente(ticker)
    except Exception:
        return None


def _nome_con_motivo(ticker, nome_fn=None):
    """(nome, None) oppure (None, motivo vero) per la proposta AI (REV_G2b B1): mai il ticker come
    nome. Stesse fonti di filing_identita.nome_emittente (portafoglio, poi Yahoo), con il perche'."""
    if nome_fn is not None:
        out = nome_fn(ticker)
        nome, motivo = out if isinstance(out, tuple) else (out, None)
        nome = nome.strip() if isinstance(nome, str) and nome.strip() else None
        return nome, None if nome else (motivo or "nome dell'emittente non disponibile")
    motivi = []
    try:
        from bellomberg.storage.memory_db import MemoryDB
        for p in MemoryDB().get_portfolio():
            if p.get("ticker") == ticker and (p.get("nome") or "").strip():
                return p["nome"].strip(), None
        motivi.append("nessun nome in portafoglio")
    except Exception as exc:
        motivi.append(f"portafoglio illeggibile ({type(exc).__name__}: {exc})"[:160])
    try:
        from bellomberg.market_data.news_aggregator import _nome_emittente_yahoo
        nome = _nome_emittente_yahoo(ticker)
        if isinstance(nome, str) and nome.strip():
            return nome.strip(), None
        motivi.append("Yahoo senza nome per il titolo")
    except Exception as exc:
        motivi.append(f"Yahoo non disponibile ({type(exc).__name__}: {exc})"[:160])
    return None, "; ".join(motivi)


def _ultima_run_default():
    from bellomberg.agents.filing_context import ultima_run_comitato
    from bellomberg.storage.memory_db import MemoryDB
    return ultima_run_comitato(MemoryDB().get_recent_memos(5))


def _nomi_portafoglio_default():
    """Nomi delle posizioni (solo archivio locale, mai rete)."""
    from bellomberg.storage.memory_db import MemoryDB
    return {p["ticker"]: (str(p.get("nome") or "").strip() or None) for p in MemoryDB().get_portfolio()
            if p.get("ticker")}


def _preferiti_default():
    """(ticker, nome) dei preferiti da favorite_companies; errori: elenco vuoto, nel log."""
    from bellomberg.core.paths import SQLITE_PATH
    from bellomberg.storage import memory_db
    try:
        conn = memory_db.connect_sqlite(SQLITE_PATH)
        try:
            righe = conn.execute("SELECT ticker, name FROM favorite_companies").fetchall()
        finally:
            conn.close()
    except Exception as exc:
        _log.warning("filing: preferiti non leggibili: %s", exc)
        return []
    return [(r[0], r[1]) for r in righe]


def _coppie_preferiti(valori):
    """Preferiti normalizzati in (TICKER, nome|None), ticker validi e senza duplicati."""
    out = {}
    for v in valori or []:
        if isinstance(v, dict):
            t, n = v.get("ticker"), v.get("nome") or v.get("name")
        elif isinstance(v, (tuple, list)) and v:
            t, n = v[0], (v[1] if len(v) > 1 else None)
        else:
            t, n = v, None
        t = str(t or "").strip().upper()
        if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,29}", t) and t not in out:
            out[t] = str(n).strip() if isinstance(n, str) and n.strip() else None
    return list(out.items())


def _candidati(proposta):
    """Numero di candidati (SEC + ESEF) di una proposta di collegamento."""
    if not isinstance(proposta, dict):
        return None
    return sum(len((proposta.get(k) or {}).get("candidati") or []) for k in ("sec", "esef"))


def _registra_esito_sicuro(ticker, esito, motivo=None, candidati=None):
    """Esito dell'attivazione nelle preferenze; un errore di scrittura non annulla l'attivazione (log)."""
    from bellomberg.storage import filing_preferenze
    try:
        filing_preferenze.registra_esito(ticker, esito, motivo=motivo, candidati=candidati)
    except (ValueError, OSError) as exc:
        _log.warning("filing %s: esito %s non salvato nelle preferenze: %s", ticker, esito, exc)


def _controllo_giornaliero(stato):
    stato = stato if isinstance(stato, dict) else {}
    return {"attivo": bool(stato.get("auto_enabled")), "forzato_spento_da_env": bool(stato.get("auto_forzato_spento")),
            "prossimo_at": stato.get("next_run_at"), "ultimo_fine_at": stato.get("finished_at"),
            # variabile env del gestore non valida: controllo spento, qui il nome e il valore
            "errore_configurazione": stato.get("config_error")}


_ERRORI_ARCHIVIO = (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError)
# «Proponi con AI» (R10 d): attesa massima della POST prima di rispondere «in_corso» (sotto i 30 s
# del client dell'app); il lavoro continua in un thread e il risultato si legge dal job.
ATTESA_AI_S = 20.0
MAX_JOB_AI = 50
# REV_G2b A7: oltre questo un lavoro «in_corso» e' dichiarato scaduto (il client OpenRouter puo'
# durare fino a 600 s x 3 tentativi); il thread puo' finire dopo: l'esito resta comunque in cache.
SCADENZA_JOB_AI_S = 2_100


def _archivio_illeggibile(exc):
    return HTTPException(503, _text("Archivio filing non leggibile: " + str(exc),
                                    "Filing archive cannot be read: " + str(exc)))


def _preferenze_illeggibili(exc):
    return HTTPException(503, _text("Preferenze filing illeggibili: " + str(exc),
                                    "Filing preferences unreadable: " + str(exc)))


def create_filing_router(require_session, service_factory=default_service, *, attivazione=None,
                         tickers_portafoglio=None, proponi_fn=None, avvia_aggiornamento=None,
                         stato_aggiornamento=None, contesto_fn=None, ultima_run_fn=None, nome_fn=None,
                         tickers_preferiti=None, nomi_titoli_fn=None, imposta_auto=None, attesa_ai_s=ATTESA_AI_S):
    router = APIRouter(prefix="/filings", tags=["filings"],
                       dependencies=[Depends(require_session)],
                       default_response_class=PresentationJSONResponse)
    # Nomi: stessa fonte del portafoglio. Portafoglio iniettato (test, strumenti) senza nomi: nessun nome.
    if nomi_titoli_fn is None:
        nomi_titoli_fn = _nomi_portafoglio_default if tickers_portafoglio is None else dict

    @router.get("/runs/{run_id}")
    def detail(run_id: int):
        if run_id < 1:
            raise HTTPException(422, _text("ID run non valido", "Invalid run ID"))
        try:
            run = _service(service_factory).detail(run_id)
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise HTTPException(503, _text("Archivio filing non leggibile: " + str(exc),
                                            "Filing archive cannot be read: " + str(exc))) from exc
        if run is None:
            raise HTTPException(404, _text("Run filing assente", "Filing run not found"))
        return run

    def _contesto(service):
        """Contesto del Consigliere sul portafoglio, dallo stesso archivio delle route."""
        from bellomberg.agents import filing_context as fc
        tickers = list(dict.fromkeys(tickers_portafoglio()))
        try:
            novita = (ultima_run_fn or _ultima_run_default)()
        except Exception as exc:  # novità non determinabili: nessun titolo marcato nuovo
            _log.warning("filing: ultima run del comitato non disponibile: %s", exc)
            novita = None
        db_path = service.store.db_path
        contesto = (contesto_fn or fc.contesto_dettaglio)(tickers, db_path=db_path, novita_dopo=novita)
        return tickers, novita, db_path, contesto

    def _nomi():
        try:
            nomi = nomi_titoli_fn() or {}
            return {str(k): v for k, v in dict(nomi).items()}
        except Exception as exc:  # nomi solo descrittivi: mai un errore della pagina
            _log.warning("filing: nomi dei titoli non disponibili: %s", exc)
            return {}

    def _dettagli(service, pref, tickers, schede):
        """Campi della pagina per titolo (stato canonico, documento, run, errore, esito, proposta AI)."""
        from bellomberg.market_data import filing_proposta_ai as fp
        from bellomberg.market_data.filing_stato_ui import documento, stato_ui
        from bellomberg.market_data import esef_sito
        from bellomberg.storage import filing_preferenze as fpref
        store = service.store
        profili = {p["ticker"]: p for p in store.list_profiles()}
        attivi = {}
        for r in store.active_runs():
            attivi.setdefault(r["ticker"], {"id": r["id"], "started_at": r["started_at"], "trigger": r.get("trigger")})
        proposte, scartate, esiti = fp.proposte_in_cache(), fpref.proposte_scartate(pref), fpref.esiti(pref)
        out = {}
        for t in tickers:
            scheda, riga = schede[t], profili.get(t)
            scollegato = bool(riga) and fpref.e_scollegato(pref, t, riga["version"])
            ultima = fp.ultima_proposta(t, scartate=scartate, proposte=proposte)
            proposta = None
            if ultima is not None and not fp.accettata(ultima, (riga or {}).get("profile")):
                proposta = {"sha256": ultima["sha256"], "url": ultima.get("url"), "at": ultima.get("creata_il"),
                            "salvabile": bool(ultima.get("salvabile")),
                            "verificate": len(ultima.get("verificate") or [])}
            escluso = t in pref["esclusi"]
            stato, gruppo_ui = stato_ui(scheda=scheda, profilo_attivo=riga is not None, run_attivo=attivi.get(t),
                                        automatico=bool(riga and riga.get("enabled", True)),
                                        ultimo_errore=scheda.get("ultimo_errore"), esito=esiti.get(t),
                                        proposta_ai=proposta, escluso=escluso, scollegato=scollegato)
            usabile = riga is not None and not scollegato
            out[t] = {"stato": stato, "gruppo_ui": gruppo_ui,
                      "documento": documento(riga["profile"]) if usabile else None,
                      "cambiamenti": int(scheda.get("totale_cambiamenti") or 0),
                      "run_attivo": attivi.get(t), "ultimo_errore": scheda.get("ultimo_errore"),
                      "attivazione": esiti.get(t), "proposta_ai": proposta,
                      "prossimo_at": store.next_due_at(t) if usabile and riga["enabled"] else None,
                      # fase F: PDF IR trovati sul sito (cache dell'ultima esplorazione, nessuna rete)
                      "pdf_ir": None if usabile or escluso else esef_sito.pdf_trovati(t)}
        return out

    @router.get("")
    def overview(ambito: str = Query(default="portafoglio")):
        from bellomberg.agents import filing_context as fc
        if ambito not in ("portafoglio", "preferiti"):
            raise HTTPException(422, _text("Ambito non valido: portafoglio o preferiti",
                                           "Invalid scope: portafoglio or preferiti"))
        service = _service(service_factory)
        pref = _preferenze()
        # Contesto e budget sono sempre quelli del portafoglio: il Consigliere vede solo quello.
        tickers_pf, novita, db_path, contesto = _contesto(service)
        nomi = _nomi()
        try:
            if ambito == "portafoglio":
                tickers = tickers_pf
                # Le schede vengono dal contesto già costruito; contesto_fn esterni senza schede: si leggono qui.
                elenco = contesto.get("schede") if isinstance(contesto, dict) else None
                if elenco is None:
                    elenco = fc.schede_filing(tickers, db_path=db_path, novita_dopo=novita)
            else:
                try:
                    preferiti = _coppie_preferiti((tickers_preferiti or _preferiti_default)())
                except Exception as exc:
                    _log.warning("filing: preferiti non disponibili: %s", exc)
                    preferiti = []
                nel_pf = set(tickers_pf)
                tickers = [t for t, _ in preferiti if t not in nel_pf]
                nomi = {**{t: n for t, n in preferiti if n}, **nomi}
                elenco = fc.schede_filing(tickers, db_path=db_path, novita_dopo=novita) if tickers else []
            schede = {s["ticker"]: s for s in elenco}
            rotta = next((s["guasto"] for s in schede.values() if s.get("guasto")), None)
            if rotta:
                raise RuntimeError(rotta)
            tickers = [t for t in tickers if t in schede]
            dettagli = _dettagli(service, pref, tickers, schede)
            titoli = []
            for t in tickers:
                # Tutto dalla scheda: freschezza, fonte e confronto sono quelli della riga di stato.
                scheda, escluso = schede[t], t in pref["esclusi"]
                titoli.append({
                    "ticker": t, "gruppo": scheda["gruppo"], "stato_riga": scheda["stato"],
                    "fonte": scheda.get("fonte"), "ultimo_confronto": scheda.get("confronto_at"),
                    "run_id": scheda.get("run_id"),
                    "novita": scheda["gruppo"] == 0, "profilo": scheda.get("fresco") is not None,
                    "escluso": escluso, "_fresco": scheda.get("fresco"),
                    "nome": nomi.get(t), **dettagli[t], "nel_contesto": ambito == "portafoglio"})
        except _ERRORI_ARCHIVIO as exc:
            raise _archivio_illeggibile(exc) from exc
        copertura = {
            "totale": len(titoli),
            "con_confronto": sum(1 for t in titoli if t["ultimo_confronto"]),
            "aggiornati": sum(1 for t in titoli if t["_fresco"] == "aggiornato"),
            "non_aggiornati": sum(1 for t in titoli if t["_fresco"] == "non_aggiornato"),
            "senza_confronto": sum(1 for t in titoli if t["_fresco"] == "senza_confronto"),
            "senza_profilo": sum(1 for t in titoli if not t["profilo"] and not t["escluso"]),
            "esclusi": sum(1 for t in titoli if t["escluso"])}
        for t in titoli:
            # fase F: il riquadro «Freschezza» distingue «nessun confronto» da «aggiornato»
            t["freschezza"] = t.pop("_fresco")
        stato = stato_aggiornamento() if stato_aggiornamento else None
        return {"ambito": ambito, "titoli": titoli, "copertura": copertura,
                "contesto": {"caratteri": contesto["caratteri"], "budget": contesto["budget"],
                             "omessi_totali": contesto["omessi_totali"]},
                "aggiornamento": stato, "controllo_giornaliero": _controllo_giornaliero(stato)}

    @router.get("/novita")
    def novita_badge():
        """Badge del menu: titoli del portafoglio con novità (schede, senza il testo del contesto)."""
        from bellomberg.agents import filing_context as fc
        service = _service(service_factory)
        tickers = list(dict.fromkeys(tickers_portafoglio()))
        try:
            novita = (ultima_run_fn or _ultima_run_default)()
        except Exception as exc:  # novità non determinabili: nessun titolo marcato nuovo
            _log.warning("filing: ultima run del comitato non disponibile: %s", exc)
            novita = None
        try:
            schede = fc.schede_filing(tickers, db_path=service.store.db_path, novita_dopo=novita)
            rotta = next((s["guasto"] for s in schede if s.get("guasto")), None)
            if rotta:
                raise RuntimeError(rotta)
        except _ERRORI_ARCHIVIO as exc:
            raise _archivio_illeggibile(exc) from exc
        nuovi = [s["ticker"] for s in schede if s["gruppo"] == 0]
        return {"n": len(nuovi), "tickers": nuovi}

    @router.put("/auto-refresh")
    def auto_refresh(body: dict = Body(...)):
        """Interruttore del controllo giornaliero: runtime + preferenze (letto all'avvio)."""
        from bellomberg.storage import filing_preferenze
        if not isinstance(body, dict) or set(body) != {"attivo"} or type(body["attivo"]) is not bool:
            raise HTTPException(422, _text("Campo attivo mancante o non booleano",
                                           "Missing or non-boolean 'attivo' field"))
        if imposta_auto is None:
            raise HTTPException(503, _text("Controllo giornaliero non disponibile",
                                           "Daily check not available"))
        _preferenze()
        try:
            esito = imposta_auto(body["attivo"])
        except ValueError as exc:
            # il gestore nomina la variabile non valida; altrimenti e' l'interruttore env
            motivo = str(exc) if "FILING_AUTO_REFRESH" in str(exc) else f"FILING_AUTO_REFRESH_ENABLED ({exc})"
            raise HTTPException(409, _text("Controllo giornaliero disattivato dalla configurazione: " + motivo,
                                           "Daily check disabled by configuration: " + motivo)) from exc
        try:
            filing_preferenze.imposta_controllo_giornaliero(body["attivo"])
        except (ValueError, OSError) as exc:
            # scelta non salvata: il runtime torna com'era, cosi' interruttore e preferenze non divergono
            try:
                imposta_auto(not body["attivo"])
            except ValueError:
                pass
            raise _preferenze_illeggibili(exc) from exc
        stato = stato_aggiornamento() if stato_aggiornamento else esito
        return {"attivo": body["attivo"], "controllo_giornaliero": _controllo_giornaliero(stato)}

    @router.get("/{ticker}/context-preview")
    def context_preview(ticker: str):
        ticker = _ticker(ticker)
        service = _service(service_factory)
        if ticker not in tickers_portafoglio():
            raise HTTPException(404, _text("titolo non in portafoglio: il Consigliere non lo vede",
                                           "ticker not in portfolio: the Consigliere does not see it"))
        try:
            _, _, _, contesto = _contesto(service)
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise HTTPException(503, _text("Archivio filing non leggibile: " + str(exc),
                                           "Filing archive cannot be read: " + str(exc))) from exc
        riga = contesto["righe"].get(ticker, {"testo": "", "caratteri": 0, "omessi": 0})
        schede = contesto.get("schede") if isinstance(contesto, dict) else None
        if schede is None:
            from bellomberg.agents import filing_context as fc
            try:
                schede = fc.schede_filing([ticker], db_path=service.store.db_path)
            except _ERRORI_ARCHIVIO as exc:
                raise _archivio_illeggibile(exc) from exc
        scheda = next((s for s in schede if s.get("ticker") == ticker), None) or {}
        # «In evidenza» = ordine del punteggio del contesto (lo stesso che vede il Consigliere).
        in_evidenza = [f"C{c['pos']}" for c in scheda.get("cambiamenti") or [] if isinstance(c.get("pos"), int)]
        return {"ticker": ticker, "testo": riga["testo"], "caratteri": riga["caratteri"],
                "omessi": riga["omessi"], "in_evidenza": in_evidenza,
                "contesto_totale": {"caratteri": contesto["caratteri"], "budget": contesto["budget"]},
                "nota": _text("freschezza calcolata dall'archivio; nella run dipende dall'aggiornamento pre-run",
                              "freshness computed from the archive; in a run it depends on the pre-run refresh")}

    @router.get("/{ticker}")
    def status(ticker: str):
        ticker = _ticker(ticker)
        try:
            return _service(service_factory).status(ticker)
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise HTTPException(503, _text("Archivio filing non leggibile: " + str(exc),
                                            "Filing archive cannot be read: " + str(exc))) from exc

    @router.post("/{ticker}/refresh", status_code=202)
    def refresh(ticker: str, background_tasks: BackgroundTasks):
        service = _service(service_factory)
        try:
            queued = service.queue(_ticker(ticker), trigger="manual")
        except ValueError as exc:
            raise HTTPException(422, _text("Richiesta filing non valida: " + str(exc),
                                            "Invalid filing request (technical detail): " + str(exc))) from exc
        except RuntimeError as exc:
            if "run gi" in str(exc) and "attivo" in str(exc):
                raise HTTPException(409, _text("Run filing gia attivo", "Filing run already active")) from exc
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                            "Filing archive unavailable: " + str(exc))) from exc
        except (FileNotFoundError, sqlite3.DatabaseError) as exc:
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                            "Filing archive unavailable: " + str(exc))) from exc
        # Profili 20-F automatici: la variante 6-K fallita all'attivazione si ritenta qui.
        background_tasks.add_task(_ritenta_6k_con, attivazione, service, queued["ticker"])
        background_tasks.add_task(service.execute, queued["id"])
        return {"run_id": queued["id"], "status": "queued"}

    @router.put("/{ticker}/profile")
    def profile(ticker: str, body: dict = Body(...)):
        ticker = _ticker(ticker)
        expected = {"profile", "enabled", "interval_hours", "qualitative_enabled"}
        if set(body) != expected:
            raise HTTPException(422, _text("Campi del profilo incompleti o sconosciuti",
                                                "Incomplete or unknown profile fields"))
        service = _service(service_factory)
        try:
            return service.store.set_profile(ticker, body["profile"],
                                             enabled=body["enabled"],
                                             interval_hours=body["interval_hours"],
                                             qualitative_enabled=body["qualitative_enabled"])
        except ValueError as exc:
            raise HTTPException(422, _text("Profilo filing non valido: " + str(exc),
                                            "Invalid filing profile (technical detail): " + str(exc))) from exc
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError) as exc:
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                            "Filing archive unavailable: " + str(exc))) from exc

    if attivazione is None:
        from bellomberg.market_data import filing_attivazione as attivazione
    if proponi_fn is None:
        from bellomberg.market_data.filing_identita import proponi as proponi_fn
    tickers_portafoglio = tickers_portafoglio or _tickers_portafoglio_default

    def _dopo_attivazione(service, tickers):
        if avvia_aggiornamento is not None:
            # Variante 6-K (solo 20-F), poi il manager (pool) esegue i primi confronti; se e' occupato
            # registra la richiesta e rilancia run_due appena finisce il lavoro in corso.
            for t in tickers:
                _ritenta_6k_con(attivazione, service, t)
            try:
                avvia_aggiornamento("activation")
            except Exception as exc:
                _log.warning("filing: aggiornamento post-attivazione non avviato: %s", exc)
            return
        # Variante 6-K (solo 20-F) e primo confronto, fuori dalla risposta HTTP. Run periodico:
        # il giudizio AI resta del solo pulsante "Verifica ora".
        for t in tickers:
            _ritenta_6k_con(attivazione, service, t)
            try:
                service.run_programmato(t)
            except Exception:
                pass  # run gia' attivo o archivio occupato: lo riprende il controllo periodico

    def _in_coda():
        """Manager gia' al lavoro: i profili nuovi partono appena finisce (solo informativo)."""
        try:
            occupato = avvia_aggiornamento is not None and stato_aggiornamento is not None \
                and (stato_aggiornamento() or {}).get("status") == "running"
        except Exception:
            occupato = False
        return {"aggiornamento": "in coda: subito dopo il controllo in corso"} if occupato else {}

    def _proposta_senza_rifiutati(ticker, pref):
        from bellomberg.market_data.filing_attivazione import senza_rifiutati
        from bellomberg.storage import filing_preferenze
        cik = frozenset(pref["rifiutati"].get(ticker, []))
        lei = filing_preferenze.rifiutati_lei(pref, ticker)
        out = proponi_fn(ticker, rifiutati=cik, rifiutati_lei=lei)
        return senza_rifiutati(out, cik, lei | cik) if isinstance(out, dict) else out

    @router.get("/{ticker}/proposal")
    def proposal(ticker: str):
        ticker = _ticker(ticker)
        service = _service(service_factory)
        pref = _preferenze()
        out = _proposta_senza_rifiutati(ticker, pref)
        return {**out, "profilo_attivo": service.store.get_profile(ticker) is not None,
                "escluso": ticker in pref["esclusi"]}

    @router.post("/{ticker}/search-pdf")
    def search_pdf(ticker: str):
        """Fase F, su richiesta: esplora il sito della societa' e sceglie i PDF IR (gratis, nessuna
        AI, nessun PDF scaricato). Al massimo un'esplorazione l'ora; mai durante la run del Consigliere."""
        from bellomberg.market_data import esef_sito
        ticker = _ticker(ticker)
        if esef_sito.consigliere_in_corso():
            raise HTTPException(409, _text("Run del Consigliere in corso: riprova dopo",
                                           "Consigliere run in progress: try again later"))
        if not esef_sito.esplorazione_in_corso(ticker):
            raise HTTPException(409, _text("Ricerca sul sito gia' in corso per questo titolo",
                                           "Website search already running for this security"))
        try:
            trovato = esef_sito.scopri(ticker, forza=True)
        except OSError as exc:
            raise HTTPException(503, _text("Cache dei siti non scrivibile: ", "Site cache not writable: ")
                                + type(exc).__name__) from exc
        finally:
            esef_sito.esplorazione_in_corso(ticker, inizio=False)
        return {"ticker": ticker, "pdf_ir": esef_sito.pdf_trovati(ticker), "sito": trovato.get("sito"),
                "pagine": len(trovato.get("pagine") or []), "motivi": (trovato.get("motivi") or [])[:5]}

    @router.post("/{ticker}/reject")
    def reject(ticker: str, body: dict = Body(...)):
        """«Nessuno dei due»: rifiuta i CIK o i LEI proposti e restituisce la nuova proposta."""
        from bellomberg.storage import filing_preferenze
        ticker = _ticker(ticker)
        chiave = next(iter(body), None) if isinstance(body, dict) and len(body) == 1 else None
        valori = body.get(chiave) if chiave in ("cik", "lei") else None
        valori = [valori] if isinstance(valori, str) else valori
        forma = r"\d{1,10}" if chiave == "cik" else r"[A-Z0-9]{20}"
        if not isinstance(valori, list) or not 1 <= len(valori) <= 20 or not all(
                isinstance(v, str) and re.fullmatch(forma, v) for v in valori):
            raise HTTPException(422, _text("Indicare cik oppure lei (valore o elenco) validi",
                                           "Provide valid cik or lei (value or list)"))
        _preferenze()
        try:
            for v in dict.fromkeys(valori):
                if chiave == "cik":
                    filing_preferenze.rifiuta(ticker, v)
                else:
                    filing_preferenze.rifiuta_lei(ticker, v)
        except (ValueError, OSError) as exc:
            raise _preferenze_illeggibili(exc) from exc
        proposta = _proposta_senza_rifiutati(ticker, _preferenze())
        n = _candidati(proposta) or 0
        if n:
            esito, motivo = "da_confermare", _text("collegamento rifiutato: restano altri candidati",
                                                   "link rejected: other candidates remain")
        elif any((proposta.get(k) or {}).get("stato") == "errore" for k in ("sec", "esef")):
            esito, motivo = "errore", "; ".join(str((proposta.get(k) or {}).get("motivo")) for k in ("sec", "esef")
                                               if (proposta.get(k) or {}).get("stato") == "errore")
        else:
            esito, motivo = "senza_fonte", _text("collegamento rifiutato: nessun altro candidato",
                                                 "link rejected: no other candidate")
        _registra_esito_sicuro(ticker, esito, motivo=motivo, candidati=n)
        return {"ticker": ticker, "proposta": proposta, "esito": esito}

    @router.post("/{ticker}/unlink")
    def unlink(ticker: str, body: dict = Body(default={})):
        """«Scollega»: rifiuta il CIK/LEI del profilo automatico e salva lo STESSO profilo disattivato
        (append-only, nessuna cancellazione)."""
        from bellomberg.storage import filing_preferenze
        ticker = _ticker(ticker)
        if not isinstance(body, dict) or body:
            raise HTTPException(422, _text("Nessun campo previsto", "No field expected"))
        service = _service(service_factory)
        pref = _preferenze()
        try:
            riga = service.store.get_profile(ticker)
            attivo = any(r["ticker"] == ticker for r in service.store.active_runs())
        except _ERRORI_ARCHIVIO as exc:
            raise _archivio_illeggibile(exc) from exc
        if riga is None:
            raise HTTPException(404, _text("Nessun profilo per il titolo", "No profile for this security"))
        profilo = riga["profile"]
        cik, lei = profilo.get("cik"), profilo.get("lei")
        if not (cik or lei) or profilo.get("origine_collegamento") in (None, "manuale", "proposta_ai"):
            raise HTTPException(422, _text(
                "Profilo senza collegamento automatico SEC/ESEF: niente da scollegare (usa «Escludi»)",
                "Profile without an automatic SEC/ESEF link: nothing to unlink (use «Exclude»)"))
        if filing_preferenze.e_scollegato(pref, ticker, riga["version"]):
            raise HTTPException(409, _text("Collegamento gia' annullato", "Link already removed"))
        if attivo:
            raise HTTPException(409, _text("Run filing gia attivo: riprova a run concluso",
                                           "Filing run already active: retry when it ends"))
        try:
            salvato = service.store.set_profile(ticker, profilo, enabled=False,
                                                interval_hours=riga["interval_hours"],
                                                qualitative_enabled=riga["qualitative_enabled"])
        except ValueError as exc:
            raise HTTPException(422, _text("Profilo non valido: " + str(exc),
                                           "Invalid profile (technical detail): " + str(exc))) from exc
        except _ERRORI_ARCHIVIO as exc:
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                           "Filing archive unavailable: " + str(exc))) from exc
        try:
            if cik:
                filing_preferenze.rifiuta(ticker, cik)
            else:
                filing_preferenze.rifiuta_lei(ticker, lei)
            filing_preferenze.imposta_scollegato(ticker, salvato["version"])
            filing_preferenze.registra_esito(ticker, "scollegato", motivo=(
                f"collegamento annullato (CIK {str(cik).zfill(10)})" if cik else f"collegamento annullato (LEI {lei})"))
        except (ValueError, OSError) as exc:
            raise _preferenze_illeggibili(exc) from exc
        return {"ticker": ticker, "esito": "scollegato", "versione": salvato["version"]}

    @router.post("/{ticker}/activate")
    def activate(ticker: str, background_tasks: BackgroundTasks, body: dict = Body(default={})):
        ticker = _ticker(ticker)
        cik = body.get("cik") if isinstance(body, dict) else None
        lei = body.get("lei") if isinstance(body, dict) else None
        if not isinstance(body, dict) or set(body) - {"cik", "lei"} or (
                cik is not None and not (isinstance(cik, str) and cik.isdigit())) or (
                lei is not None and not (isinstance(lei, str) and re.fullmatch(r"[A-Z0-9]{20}", lei))) or (
                cik is not None and lei is not None):
            raise HTTPException(422, _text("Richiesta di attivazione non valida", "Invalid activation request"))
        service = _service(service_factory)
        _preferenze()
        try:
            esito = (attivazione.attiva(service.store, ticker, lei=lei) if lei is not None
                     else attivazione.attiva(service.store, ticker, cik=cik))
        except ValueError as exc:
            raise HTTPException(422, _text("Attivazione non valida: " + str(exc),
                                           "Invalid activation (technical detail): " + str(exc))) from exc
        if esito.get("esito") != "gia_attivo":  # un profilo gia' presente non cambia l'ultimo esito
            _registra_esito_sicuro(ticker, esito.get("esito"), motivo=esito.get("motivo"),
                                   candidati=_candidati(esito.get("proposta")))
        if esito["esito"] == "attivato":
            background_tasks.add_task(_dopo_attivazione, service, [ticker])
            return {**esito, **_in_coda()}
        return esito

    @router.post("/activate-missing")
    def activate_missing(background_tasks: BackgroundTasks):
        service = _service(service_factory)
        _preferenze()
        riepilogo = attivazione.attiva_mancanti(service.store, tickers_portafoglio())
        for lista, esito in (("attivati", "attivato"), ("da_confermare", "da_confermare"),
                             ("senza_fonte", "senza_fonte"), ("esclusi", "escluso")):
            for t in riepilogo.get(lista) or []:
                _registra_esito_sicuro(t, esito)
        for e in riepilogo.get("errori") or []:
            _registra_esito_sicuro(e["ticker"], "errore", motivo=e.get("motivo"))
        if riepilogo["attivati"]:
            background_tasks.add_task(_dopo_attivazione, service, list(riepilogo["attivati"]))
            return {**riepilogo, **_in_coda()}
        return riepilogo

    @router.post("/{ticker}/exclude")
    def exclude(ticker: str, body: dict = Body(...)):
        ticker = _ticker(ticker)
        if not isinstance(body, dict) or set(body) != {"escluso"} or type(body["escluso"]) is not bool:
            raise HTTPException(422, _text("Campo escluso mancante o non booleano",
                                           "Missing or non-boolean 'escluso' field"))
        from bellomberg.storage import filing_preferenze
        try:
            filing_preferenze.imposta_escluso(ticker, body["escluso"])
        except ValueError as exc:
            raise HTTPException(503, _text("Preferenze filing illeggibili: " + str(exc),
                                           "Filing preferences unreadable: " + str(exc))) from exc
        return {"ticker": ticker, "escluso": body["escluso"]}

    # ---------------------------------------------------------------- fase E: proposta AI in sospeso
    @router.get("/{ticker}/ai-proposal")
    def ai_proposal_cached(ticker: str):
        """Ultima proposta AI in cache per il titolo: nessun download, nessuna chiamata al modello."""
        from bellomberg.market_data import filing_proposta_ai as fp
        from bellomberg.storage import filing_preferenze
        ticker = _ticker(ticker)
        pref = _preferenze()
        dati = fp.ultima_proposta(ticker, scartate=filing_preferenze.proposte_scartate(pref))
        if dati is None:
            raise HTTPException(404, _text("Nessuna proposta AI in cache per il titolo",
                                           "No cached AI proposal for this security"))
        try:
            riga = _service(service_factory).store.get_profile(ticker)
        except _ERRORI_ARCHIVIO as exc:
            raise _archivio_illeggibile(exc) from exc
        return {**fp.pubblica(dati, True), "at": dati.get("creata_il"),
                "da_riverificare": dati.get("versione_verifica") != fp.VERSIONE_VERIFICA,
                "accettata": fp.accettata(dati, (riga or {}).get("profile"))}

    @router.post("/{ticker}/ai-proposal/discard")
    def ai_proposal_discard(ticker: str, body: dict = Body(...)):
        """La proposta non e' piu' «in sospeso»; resta in cache (riaprirla non costa)."""
        from bellomberg.market_data import filing_proposta_ai as fp
        from bellomberg.storage import filing_preferenze
        ticker = _ticker(ticker)
        if (not isinstance(body, dict) or set(body) != {"sha256"} or not isinstance(body["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", body["sha256"])):
            raise HTTPException(422, _text("Campo sha256 mancante o non valido", "Missing or invalid sha256"))
        _preferenze()
        dati = fp.leggi_proposta(body["sha256"])
        if dati is None or dati.get("fallita") or dati.get("ticker") != ticker:
            raise HTTPException(404, _text("Proposta AI assente per questo titolo",
                                           "No AI proposal for this security"))
        try:
            filing_preferenze.scarta_proposta(body["sha256"])
        except (ValueError, OSError) as exc:
            raise _preferenze_illeggibili(exc) from exc
        return {"ticker": ticker, "sha256": body["sha256"], "scartata": True}

    # ---------------------------------------------------------------- fase C: proposta AI da PDF IR
    def _scarica_ai(service, url):
        from bellomberg.market_data import download_sicuro, lettore_trimestrali
        cartella = service.archive_root / "ai"
        # fase F: PDF al massimo 40 MB (la cartella si ripulisce a fine richiesta: _pulisci_ai)
        esito = lettore_trimestrali.scarica_documento(url, str(cartella),
                                                      host_consentiti={urlsplit(url).hostname.lower()},
                                                      public_only=True, max_bytes=download_sicuro.MAX_PDF)
        if esito.get("stato") != "ok":
            raise HTTPException(422, _text("Documento non scaricato: " + str(esito.get("motivo")),
                                           "Document not downloaded: " + str(esito.get("motivo"))))
        return esito

    def _pulisci_ai(service, usati):
        """Pulizia di filing_archive/ai a fine richiesta, tenendo TUTTI i PDF che la richiesta usa
        (revisione finale: pulire dopo ogni download toglieva il PDF principale gia' scaricato)."""
        from bellomberg.market_data import download_sicuro
        try:
            download_sicuro.pulisci_cartella(service.archive_root / "ai", tieni=[p for p in usati if p])
        except OSError:
            pass  # pulizia best-effort

    def _modello_ai():
        from bellomberg.core import llm_client as lc
        from bellomberg.market_data import filing_proposta_ai as fp
        try:
            modello = lc.modello(fp.LLM_FUNCTION)
            lc.chiave_api()  # senza chiave la chiamata non partirebbe: niente download inutili
            return modello, None
        except lc.ConfigurazioneLLMMancante as exc:
            return None, {"stato": "not_configured", "variabile": getattr(exc, "variabile", fp.MODEL_VARIABLE)}

    @router.get("/{ticker}/ai-estimate")
    def ai_estimate(ticker: str, url: str = Query(default="")):
        """Misure dell'input e costo massimo stimato. Nessuna chiamata al modello."""
        from bellomberg.market_data import filing_proposta_ai as fp
        ticker, url = _ticker(ticker), _url_ir(url)
        modello, mancante = _modello_ai()
        if mancante:
            return mancante  # nessun download senza modello o chiave
        service = _service(service_factory)
        documento = _scarica_ai(service, url)
        try:
            estratto = fp.estrai_input(documento["path"])
        except ValueError as exc:
            raise HTTPException(422, _text("Documento non utilizzabile: " + str(exc),
                                           "Document not usable: " + str(exc))) from exc
        finally:
            _pulisci_ai(service, [documento["path"]])
        base = {"stato": "ok", "ticker": ticker, "url": url, "sha256": estratto["sha256"],
                "pagine": estratto["pagine"], "caratteri_documento": estratto["caratteri_documento"],
                "caratteri_input": estratto["caratteri_input"], "righe": len(estratto["righe"]),
                "pagine_indice": [b["pagina"] for b in estratto["indice"]], "troncato": estratto["troncato"],
                "lingua_rilevata": estratto["lingua_rilevata"], "cache": False}
        salvata = fp.leggi_proposta(estratto["sha256"])
        if salvata is not None:
            return {**base, "cache": True, "modello": salvata.get("modello"), "costo_max_eur": 0,
                    "costo_max_usd": 0}
        return {**base, **fp.stima(estratto, modello=modello)}

    # Lavori «Proponi con AI» (R10 d): {job_id: job}; uno solo in corso per titolo.
    import contextvars
    import threading
    import time
    import uuid
    jobs_ai, jobs_lock = {}, threading.Lock()

    def _scaduto(job):
        return not job["fatto"].is_set() and time.monotonic() - job["inizio"] > SCADENZA_JOB_AI_S

    def _stato_job(job):
        if _scaduto(job):
            return {"stato": "error", "job_id": job["id"], "scaduto": True, "riprovabile": True,
                    "dettaglio": _text(f"lavoro AI oltre {SCADENZA_JOB_AI_S} s: scaduto. La chiamata puo' essere "
                                       "ancora in corso (e pagata): se arriva, l'esito resta in cache e riaprirlo "
                                       "non costa.",
                                       f"AI job over {SCADENZA_JOB_AI_S} s: expired. The call may still be running "
                                       "(and paid): if it arrives, the result stays cached and reopening it is free.")}
        if not job["fatto"].is_set():
            return {"stato": "in_corso", "job_id": job["id"], "ticker": job["ticker"], "url": job["url"],
                    "avviato_il": job["avviato_il"], "secondi": round(time.monotonic() - job["inizio"], 1)}
        if job["http"] is not None:  # documento non scaricato / non valido: errore dichiarato
            return {"stato": "error", "job_id": job["id"], "dettaglio": job["http"].detail,
                    "http_status": job["http"].status_code}
        return {**job["esito"], "job_id": job["id"]}

    def _avvia_job_ai(ticker, url, lavoro, chiave):
        """Lavoro in un thread daemon (contesto di lingua della richiesta). Lo stesso click (titolo,
        PDF, altri PDF, riprova) mentre gira riceve lo stesso job: nessun secondo download, nessuna
        seconda spesa. Un click diverso sullo stesso titolo parte, e la spesa si serializza per titolo
        in filing_proposta_ai (lock per titolo)."""
        from datetime import datetime, timezone
        with jobs_lock:
            for job in jobs_ai.values():
                if job["chiave"] == chiave and not job["fatto"].is_set() and not _scaduto(job):
                    return job
            job = {"id": "ai-" + uuid.uuid4().hex, "ticker": ticker, "url": url, "inizio": time.monotonic(),
                   "chiave": chiave,
                   "avviato_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "fatto": threading.Event(), "esito": None, "http": None}
            jobs_ai[job["id"]] = job
            while len(jobs_ai) > MAX_JOB_AI:  # i piu' vecchi conclusi escono (il risultato resta in cache)
                vecchio = next((k for k, j in jobs_ai.items() if j["fatto"].is_set()), None)
                if vecchio is None:
                    break
                jobs_ai.pop(vecchio)

        def esegui():
            try:
                job["esito"] = lavoro()
            except HTTPException as exc:
                job["http"] = exc
            except Exception as exc:  # mai un job appeso in «in_corso»: l'errore si dichiara
                _log.warning("filing %s: proposta AI non riuscita: %s: %s", ticker, type(exc).__name__, exc)
                job["esito"] = {"stato": "error", "dettaglio": f"{type(exc).__name__}: {exc}"[:400]}
            finally:
                job["fatto"].set()

        contesto = contextvars.copy_context()
        threading.Thread(target=contesto.run, args=(esegui,), name="bellomberg-filing-ai", daemon=True).start()
        return job

    @router.get("/{ticker}/ai-proposal/job/{job_id}")
    def ai_proposal_job(ticker: str, job_id: str):
        """Stato del lavoro «Proponi con AI»: in_corso, oppure lo stesso esito della POST."""
        ticker = _ticker(ticker)
        with jobs_lock:
            job = jobs_ai.get(job_id)
        if job is None or job["ticker"] != ticker:
            raise HTTPException(404, _text("Lavoro AI assente: di un altro titolo, oppure il backend e' stato "
                                           "riavviato (i lavori vivono in memoria); l'esito gia' pagato resta in "
                                           "cache: riaprire la proposta non costa",
                                           "AI job not found: for another security, or the backend restarted "
                                           "(jobs live in memory); a paid result stays cached: reopening is free"))
        return _stato_job(job)

    @router.post("/{ticker}/ai-proposal")
    def ai_proposal(ticker: str, body: dict = Body(...)):
        """UNICO percorso che chiama il modello (pulsante «Proponi con AI»). Attende al massimo
        `attesa_ai_s`: oltre risponde {"stato": "in_corso", "job_id"} e il lavoro prosegue (si legge
        da GET /{ticker}/ai-proposal/job/{job_id}); il client non mostra mai «errore» su una chiamata
        che il server sta pagando."""
        from bellomberg.market_data import filing_proposta_ai as fp
        ticker = _ticker(ticker)
        if not isinstance(body, dict) or "url" not in body or set(body) - {"url", "altri_url", "riprova"} or (
                "altri_url" in body and (not isinstance(body["altri_url"], list) or len(body["altri_url"]) > 4)) or (
                "riprova" in body and type(body["riprova"]) is not bool):
            raise HTTPException(422, _text("Campo url mancante o campi sconosciuti", "Missing url or unknown fields"))
        url = _url_ir(body["url"])
        altri_url = [u for u in dict.fromkeys(_url_ir(u) for u in body.get("altri_url", [])) if u != url]
        _, mancante = _modello_ai()
        if mancante:
            return mancante  # nessun download, nessun costo
        service = _service(service_factory)

        def lavoro():
            documento = _scarica_ai(service, url)
            nome, nome_motivo = _nome_con_motivo(ticker, nome_fn)  # B1: mai il ticker come nome
            altri = []
            try:
                for altro in altri_url:  # altri PDF dell'emittente (es. anno prima): solo verifica locale, mai AI
                    try:
                        altri.append({"url": altro, "path": _scarica_ai(service, altro)["path"]})
                    except HTTPException as exc:
                        altri.append({"url": altro, "path": None, "errore": exc.detail})
                return fp.proponi(ticker, nome=nome, path=documento["path"], url=url, altri=altri,
                                  riprova=body.get("riprova") is True, nome_motivo=nome_motivo)
            finally:
                _pulisci_ai(service, [documento["path"]] + [a["path"] for a in altri])

        job = _avvia_job_ai(ticker, url, lavoro, (ticker, url, tuple(altri_url), body.get("riprova") is True))
        if job["fatto"].wait(max(0.0, float(attesa_ai_s))):
            if job["http"] is not None:
                raise job["http"]  # come prima: documento non scaricato = 422 sincrono
            return {**job["esito"], "job_id": job["id"]}
        return _stato_job(job)

    @router.post("/{ticker}/ai-proposal/accept")
    def ai_proposal_accept(ticker: str, background_tasks: BackgroundTasks, body: dict = Body(...)):
        """Salva SOLO le sezioni verificate della proposta in cache (mai sezioni dal client)."""
        from bellomberg.market_data import filing_proposta_ai as fp
        ticker = _ticker(ticker)
        if (not isinstance(body, dict) or "sha256" not in body
                or set(body) - {"sha256", "ir_urls", "sostituisci", "aggiungi_variante"}
                or not isinstance(body["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", body["sha256"])
                or any(k in body and type(body[k]) is not bool for k in ("sostituisci", "aggiungi_variante"))
                or (body.get("sostituisci") and body.get("aggiungi_variante"))):
            raise HTTPException(422, _text("Richiesta non valida", "Invalid request"))
        dati = fp.leggi_proposta(body["sha256"])
        if dati is None:
            raise HTTPException(404, _text("Proposta AI assente per questo documento",
                                           "No AI proposal for this document"))
        if dati.get("ticker") != ticker:
            raise HTTPException(409, _text("Proposta fatta per un altro titolo: riaprila da questo titolo (nessun costo)",
                                           "Proposal made for another security: reopen it from this one (no cost)"))
        if dati.get("versione_verifica") != fp.VERSIONE_VERIFICA:
            raise HTTPException(409, _text("Proposta verificata con regole precedenti: riaprila (nessun costo)",
                                           "Proposal verified with older rules: reopen it (no cost)"))
        if not dati.get("salvabile") or not isinstance(dati.get("profilo"), dict):
            raise HTTPException(422, _text("Nessuna sezione verificata: niente da salvare",
                                           "No verified section: nothing to save"))
        ir_urls = body.get("ir_urls", [dati["url"]])
        if not isinstance(ir_urls, list) or not 1 <= len(ir_urls) <= 5:
            raise HTTPException(422, _text("ir_urls: da 1 a 5 URL", "ir_urls: 1 to 5 URLs"))
        ir_urls = list(dict.fromkeys(_url_ir(u) for u in ir_urls))
        if dati["url"] not in ir_urls:
            raise HTTPException(422, _text("ir_urls deve contenere il PDF verificato",
                                           "ir_urls must include the verified PDF"))
        service = _service(service_factory)
        profilo = {**dati["profilo"], "ticker": ticker, "ir_urls": ir_urls}
        try:
            attuale = service.store.get_profile(ticker)
            esef = attuale is not None and attuale["profile"].get("esef_modo") == "blocchi"
            if body.get("aggiungi_variante"):
                # Opzione B: infrannuale da PDF IR accanto all'annuale ESEF (conferma esplicita).
                if not esef:
                    raise HTTPException(422, _text("Variante IR solo su un profilo ESEF esistente",
                                                   "IR variant only on an existing ESEF profile"))
                nuovo = fp.variante_ir(attuale["profile"], profilo)
                salvato = service.store.set_profile(ticker, nuovo, enabled=attuale["enabled"],
                                                    interval_hours=attuale["interval_hours"],
                                                    qualitative_enabled=attuale["qualitative_enabled"])
                esito = "variante_aggiunta"
            else:
                if attuale is not None and body.get("sostituisci") is not True:
                    raise HTTPException(409, _text(
                        "Profilo esistente: conferma la sostituzione" + (
                            " o aggiungi la variante infrannuale al profilo ESEF" if esef else ""),
                        "Existing profile: confirm the replacement" + (
                            " or add the interim variant to the ESEF profile" if esef else "")))
                salvato = service.store.set_profile(ticker, profilo, enabled=True, interval_hours=fp.INTERVALLO_ORE,
                                                    qualitative_enabled=False)
                esito = "salvato"
        except ValueError as exc:
            raise HTTPException(422, _text("Profilo non valido: " + str(exc),
                                           "Invalid profile (technical detail): " + str(exc))) from exc
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError) as exc:
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                           "Filing archive unavailable: " + str(exc))) from exc
        background_tasks.add_task(_dopo_attivazione, service, [ticker])
        return {"ticker": ticker, "esito": esito, "versione": salvato["version"],
                "sezioni": sorted(profilo["sezioni"]), **_in_coda()}

    return router
