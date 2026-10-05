# -*- coding: utf-8 -*-
"""ISIN automatico dei titoli `.MI` (W1 handoff-3, 05/10/2026, Opus 5.5; decisione PM 05/10:
«per tutti, nessun problema a scaricare nessuna trimestrale»).

PERCHE' ESISTE. I lettori italiani (Borsa Italiana eventi, eMarket SDIR internal dealing e
depositi) trovano l'ISIN nel negozio CONFERMATO dal PM (data/isin_it.json) o in quello
AUTOMATICO (data/isin_it_auto.json, scritto SOLO da `borsa_italiana.risolvi_isin` dopo la
verifica sulla scheda ufficiale). Chi scarica la repo non ha nessuno dei due: senza questo
modulo ogni `.MI` rispondeva «ticker_non_mappato». Qui, PRIMA del lettore, la voce mancante
si risolve una volta su Borsa Italiana; dalla seconda chiamata la legge il negozio automatico.

UN SOLO POSTO per agent_tools, chat_tools e l'API (l'API non importa agent_tools per questo;
agent_tools ri-esporta gli stessi nomi per compatibilita').

INTERFACCIA (dichiarata a main, non cambiarla senza avvisare):
  nome_emittente_it(ticker, *, db_path=None) -> (nome | None, fonte | None, motivo_assenza | None)
  assicura_isin_it(ticker, *, db_path=None, nome=None, fonte_nome=None) -> None | dict risoluzione_isin
      `nome` esplicito (es. identita' confermata della Trade Idea, D4) salta book e fornitore
      prezzi e va con `fonte_nome` che lo dichiara (senza = ValueError); vuoto o uguale al
      simbolo = 'nome_assente', nessuna richiesta.
      None = non c'e' niente da risolvere: voce gia' presente (confermato o automatico), negozio
      ILLEGGIBILE (lo dichiara il lettore, non si scavalca un negozio rotto del PM) o simbolo
      non nella forma SIMBOLO.MI (il lettore dichiara il proprio KO).
      dict = {stato, errore, motivo, isin, salvato, negozio, nome_usato, fonte_nome[, memoria]}
      stato 'ok' SOLO se dopo la risoluzione la voce e' DAVVERO leggibile dal negozio
      (misurato rileggendolo): verificato ma non salvato = 'KO' errore 'voce_non_salvata'.
  nome_noto(ticker, ris=None, *, db_path=None) -> nome | None  (SENZA rete: per lo SDIR)
  con_risoluzione(payload, ris) -> payload (aggiunge `risoluzione_isin`; esito non ok -> anche
      nell'`error` gia' presente, perche' il KO «ticker_non_mappato» del lettore da solo non dice
      il perche').

IL NOME (mai dal simbolo): 1) nome della posizione ATTIVA nel book (DB, sola lettura via
`copertura.valuta_dal_book`); 2) longName/shortName del fornitore prezzi (yfinance), DICHIARATO
in `fonte_nome`. Nessuno dei due = 'non_trovato' errore 'nome_assente', nessuna richiesta a Borsa.
Il nome trova solo i candidati: la PROVA e' la scheda di Borsa (criterio di risolvi_isin).

MEMORIA NEGATIVA (niente martellamento di rete): un esito non ok si scrive in
<borsa_italiana.CACHE_DIR>/isin_tentativi_auto.json con l'ora del tentativo; fino alla scadenza
lo stesso ticker NON rifa' rete (ne' yfinance ne' Borsa) e il payload dice «risoluzione gia'
tentata il ..., esito ..., nuovo tentativo dopo ...». Scadenze DICHIARATE:
  TTL_NEGATIVO_S (72 h): non_trovato / ambiguo / nome assente — l'emittente non cambia ogni ora;
  TTL_KO_S (6 h): KO (rete, HTTP, layout, voce non salvata) — guasti transitori.
Il tentativo memorizzato decade PRIMA della scadenza se il book ora ha per quel ticker un nome
DIVERSO da quello usato (controllo senza rete): il PM che compila la posizione non aspetta 72 h.
Memoria illeggibile = si riprova e il payload lo dice (`memoria`).

LATENZA (review RV-W1 R5, DICHIARATA): la PRIMA chiamata per un .MI senza voce fa la rete di
risolvi_isin DENTRO la richiesta del tool/endpoint: fino a ~30 richieste HTTP con pausa 1 s e
timeout 25 s ciascuna (tetto teorico ~13 min, misurato da RV-W1 con _scarica finto), piu'
yfinance get_info senza timeout nostro. Le risoluzioni sono in fila (lock unico). Dalla seconda
chiamata: voce nel negozio automatico o memoria negativa, nessuna rete. Nessun tetto di tempo qui:
interrompere a meta' lascerebbe una scrittura del negozio in corso fuori dal lock.

giorni_validi(giorni) -> bool: chi chiama non risolve (niente rete) per una richiesta che il
lettore SDIR respingerebbe comunque per parametro. L'orologio e' quello di
`borsa_italiana.adesso_utc` (wall-clock: la memoria vive fra processi); un'ora di tentativo nel
futuro (orologio spostato) invalida la voce invece di allungarla.
"""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from bellomberg.market_data import borsa_italiana as _bi

TTL_NEGATIVO_S = 72 * 3600
# Versione delle REGOLE che hanno prodotto un tentativo memorizzato: una voce di un'altra
# versione non vale (si riprova, dichiarato). v2 (05/10, fix P1 MF): la v1 scartava come
# «simbolo» nomi veri uguali al simbolo base (es. «Qqsyn» per QQSYN.MI) e li memorizzava 72 h nome_assente.
MEMORIA_VERSIONE = 2
TTL_KO_S = 6 * 3600
# review RV-W1 R7: KO STRUTTURALI (il sito e' cambiato o la ricerca non sta nei tetti di
# risolvi_isin): riprovarli ogni 6 h rifarebbe ~30 richieste per ticker all'infinito
TTL_STRUTTURALE_S = 24 * 3600
ERRORI_STRUTTURALI = frozenset({"listino_troncato", "schede_troncate", "layout_cambiato",
                                "url_vietato", "parametro", "esito_malformato", "stato_inatteso"})
# review RV-W1 R5 (decisione main): BUDGET di tempo della risoluzione. Superato = KO
# 'tempo_esaurito' DICHIARATO; la risoluzione CONTINUA in un thread (che tiene il lock e
# scrive negozio o memoria): la chiamata dopo trova il risultato. Interattivi (chat, API) 20 s,
# desk del comitato 120 s. None = nessun budget (attesa piena).
BUDGET_INTERATTIVO_S = 20.0
BUDGET_DESK_S = 120.0
_BUDGET: ContextVar = ContextVar("budget_risoluzione_isin", default=BUDGET_INTERATTIVO_S)
NOME_FILE_TENTATIVI = "isin_tentativi_auto.json"
# None = <borsa_italiana.CACHE_DIR>/isin_tentativi_auto.json, risolto a OGNI chiamata (i test
# ridirigono CACHE_DIR o questo percorso)
PERCORSO_TENTATIVI: Optional[str] = None

_SIMBOLO_MI = re.compile(r"[A-Z0-9]+\.MI")
# review RV-W1 R3: il negozio automatico e' UN file scritto read-modify-write da risolvi_isin:
# un lock PER TICKER lasciava due .MI diversi scriverlo insieme (voce di A persa dopo l'«ok» di A,
# o PermissionError di os.replace su Windows). Un lock solo, rientrante, per tutto il processo:
# le risoluzioni sono in fila (una tantum per ticker, e Borsa vuole comunque le pause).
# LIMITE DICHIARATO: fra PROCESSI (API + run + scheduler) non c'e' mutua esclusione; una voce
# persa cosi' si ritrova alla chiamata dopo (nuova risoluzione), mai un ISIN sbagliato.
_LOCK_RISOLUZIONE = threading.RLock()

FONTE_NOME_BOOK = "nome della posizione nel book (DB)"
FONTE_NOME_FORNITORE = "nome dal fornitore prezzi (yfinance %s)"


# ------------------------------------------------------------ nome dell'emittente
def _yf_ticker(simbolo: str):
    """Unico punto che tocca yfinance (i test lo sostituiscono)."""
    import yfinance as yf
    return yf.Ticker(simbolo)


def _suffissi_listino() -> set:
    """Suffissi di mercato riconoscibili dopo il simbolo: registro mercati.MERCATI (.MI, .DE, ...)
    piu' «IM» (forma Bloomberg di Milano)."""
    from bellomberg.market_data.mercati import MERCATI
    return {k.lstrip(".").upper() for k in MERCATI} | {"IM"}


def e_il_simbolo(nome: Any, ticker: str) -> bool:
    """Il «nome» e' RICONOSCIBILMENTE un simbolo di mercato, cioe' il simbolo base seguito da un
    suffisso di listino («QQSYN.MI», «qqsyn mi», «QQSYN IM»)? Fix P1 della misura finale MF
    (05/10): il nome UGUALE al simbolo base («Qqsyn», «QQSYN» per QQSYN.MI) e' un nome
    LEGITTIMO e passa (caso misurato su piu' emittenti veri del listino): l'ISIN resta comunque
    provato dalla scheda di Borsa col Codice Alfanumerico. Confronto sul testo grezzo (maiuscolo,
    punti -> spazi), mai sul nome normalizzato."""
    t = str(ticker or "").strip().upper()
    parti = str(nome or "").upper().replace(".", " ").split()
    base = t.split(".")[0]
    return len(parti) == 2 and parti[0] == base and parti[1] in _suffissi_listino()


# motivo di assenza TRANSITORIO (rete, rate limit, DB lockato): memoria corta (TTL_KO_S), non 72 h
_TRANSITORIO = "[transitorio] "


# yfinance get_info non ha un timeout nostro: si esegue in un thread daemon e si aspetta al
# massimo TIMEOUT_YF_S (vincolo main 05/10: niente attese appese per sempre)
TIMEOUT_YF_S = 15.0


def _info_con_timeout(ticker: str) -> Any:
    esito: Dict[str, Any] = {}

    def _leggi():
        try:
            esito["info"] = _yf_ticker(ticker).get_info()
        except BaseException as e:   # riconsegnata a chi aspetta, mai persa
            esito["errore"] = e
    th = threading.Thread(target=_leggi, daemon=True, name="isin-yf-%s" % ticker)
    th.start()
    th.join(TIMEOUT_YF_S)
    if th.is_alive():
        raise TimeoutError("yfinance senza risposta")
    if "errore" in esito:
        raise esito["errore"]
    return esito.get("info")


def _nome_dal_fornitore(ticker: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(nome, campo, motivo_assenza) dal fornitore prezzi. RETE: il conftest lo neutralizza.
    Un motivo che inizia con `_TRANSITORIO` e' un guasto (eccezione), non un'assenza."""
    try:
        info = _info_con_timeout(ticker)
    except ImportError:
        return None, None, "yfinance non disponibile"
    except TimeoutError:
        return None, None, _TRANSITORIO + "yfinance: nessuna risposta in %s s" % TIMEOUT_YF_S
    except Exception as e:
        return None, None, _TRANSITORIO + "yfinance: %s" % type(e).__name__
    if not isinstance(info, dict):
        return None, None, "yfinance: info non leggibile (%s)" % type(info).__name__
    scartati = []
    for campo in ("longName", "shortName"):
        n = str(info.get(campo) or "").strip()
        if n and e_il_simbolo(n, ticker):
            scartati.append("%s=%r e' il simbolo, non un nome" % (campo, n))
            continue
        if n:
            return n, campo, None
    return None, None, "yfinance senza longName/shortName" + (" (%s)" % "; ".join(scartati) if scartati else "")


def _nome_dal_book(ticker: str, db_path: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(nome | None, motivo) dalla posizione attiva nel book, sola lettura, senza rete."""
    from bellomberg.market_data import copertura as _cop
    vb = _cop.valuta_dal_book(ticker) if db_path is None else _cop.valuta_dal_book(ticker, db_path=db_path)
    nome = str(vb.get("nome") or "").strip() or None
    if nome:
        return nome, ""
    origine = vb.get("origine")
    if origine == "posizione":
        return None, "posizione nel book senza nome"
    if origine == "fuori_book":
        return None, "non e' una posizione attiva del book (o e' senza valuta)"
    if origine == "illeggibile":   # DB lockato o guasto: transitorio
        return None, _TRANSITORIO + "book non leggibile (illeggibile)"
    return None, "book non leggibile (%s)" % origine


def nome_emittente_it(ticker: Any, *, db_path: Optional[str] = None
                      ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(nome | None, fonte | None, motivo_assenza | None). Prima il nome della posizione nel
    book (DB, sola lettura), poi il nome che il fornitore prezzi restituisce (yfinance
    longName/shortName; un valore uguale al simbolo e' scartato e detto). Nessuno dei due =
    None col motivo: MAI un nome dal simbolo. Il nome del book e' la dichiarazione del PM e si
    usa com'e'. Un motivo che contiene «[transitorio]» = guasto, non assenza."""
    t = str(ticker or "").strip().upper()
    nome, perche_book = _nome_dal_book(t, db_path)
    if nome:
        return nome, FONTE_NOME_BOOK, None
    nome, campo, perche_yf = _nome_dal_fornitore(t)
    if nome:
        return nome, FONTE_NOME_FORNITORE % campo, None
    return None, None, "%s; %s" % (perche_book, perche_yf)


# ------------------------------------------------------------ memoria negativa
def _percorso_tentativi() -> str:
    return PERCORSO_TENTATIVI or os.path.join(_bi.CACHE_DIR, NOME_FILE_TENTATIVI)


def _leggi_tentativi() -> Tuple[Dict[str, Any], Optional[str]]:
    p = _percorso_tentativi()
    try:
        with open(p, encoding="utf-8") as fh:
            dati = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as e:
        return {}, "memoria dei tentativi illeggibile (%s): si riprova" % type(e).__name__
    if not isinstance(dati, dict):
        return {}, "memoria dei tentativi illeggibile (non e' un oggetto): si riprova"
    return dati, None


def _scrivi_tentativo(ticker: str, voce: Dict[str, Any]) -> Optional[str]:
    """Leggi-modifica-scrivi SOTTO LOCK fra thread E processi (helper di IT1
    `borsa_italiana.aggiorna_json_bloccato`, review RV-W1 R3): due processi (API, run, scheduler)
    non si cancellano piu' i tentativi a vicenda. Memoria illeggibile = si riscrive (e' solo una
    cache). Ritorna il motivo se non scritto."""
    def _metti(dati: Dict[str, Any]) -> Dict[str, Any]:
        dati[ticker] = voce
        return dati
    ok, motivo = _bi.aggiorna_json_bloccato(_percorso_tentativi(), _metti, illeggibile_si_riscrive=True)
    if ok:
        return None
    return "memoria dei tentativi non scritta (%s): il prossimo uso riprovera' la rete" % motivo


def _ttl(ris: Dict[str, Any]) -> int:
    if ris.get("stato") != "KO":
        return TTL_NEGATIVO_S
    return TTL_STRUTTURALE_S if ris.get("errore") in ERRORI_STRUTTURALI else TTL_KO_S


@contextmanager
def budget_risoluzione(secondi: Optional[float]):
    """Budget di tempo per le risoluzioni fatte DENTRO il blocco (None = nessun budget)."""
    token = _BUDGET.set(secondi)
    try:
        yield
    finally:
        _BUDGET.reset(token)


def budget_per_chiamante(caller: Optional[str]) -> float:
    """chat_tools.dispatch: i desk del comitato e il red team hanno il budget lungo, il resto
    (chat del PM, API) quello interattivo."""
    c = str(caller or "")
    return BUDGET_DESK_S if c.startswith(("specialista", "red-team")) else BUDGET_INTERATTIVO_S


def _dal_ricordo(ticker: str, db_path: Optional[str], nome_esplicito: Optional[str] = None
                 ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(ris dal tentativo memorizzato ancora valido | None, nota sulla memoria | None)."""
    dati, motivo = _leggi_tentativi()
    if motivo:
        return None, motivo
    v = dati.get(ticker)
    if not isinstance(v, dict) or not isinstance(v.get("ris"), dict):
        return None, None
    if v.get("versione") != MEMORIA_VERSIONE:
        return None, ("tentativo memorizzato con regole precedenti (versione %r, attuale %d): si riprova"
                      % (v.get("versione"), MEMORIA_VERSIONE))
    try:
        tentato = datetime.fromisoformat(str(v.get("tentato_il")))
        ttl = int(v.get("ttl_s"))
    except (TypeError, ValueError, OverflowError):
        return None, "tentativo memorizzato illeggibile: si riprova"
    if not 0 < ttl <= max(TTL_NEGATIVO_S, TTL_KO_S):
        return None, "tentativo memorizzato con scadenza fuori misura (%r s): si riprova" % ttl
    adesso = _bi.adesso_utc()
    try:
        if tentato > adesso:
            return None, "tentativo memorizzato con ora nel futuro (%s): si riprova" % v.get("tentato_il")
        scade = tentato + timedelta(seconds=ttl)
        if adesso >= scade:
            return None, None
    except (TypeError, OverflowError):   # ora senza fuso nel file / data ai limiti: non confrontabile
        return None, "tentativo memorizzato senza fuso orario: si riprova"
    ris = dict(v["ris"])
    if nome_esplicito is not None:
        if nome_esplicito != ris.get("nome_usato"):
            return None, "tentativo del %s fatto col nome %r: ora il nome passato e' %r, si riprova" % (
                v.get("tentato_il"), ris.get("nome_usato"), nome_esplicito)
    else:
        nome_db, _p = _nome_dal_book(ticker, db_path)
        if nome_db and nome_db != ris.get("nome_usato"):
            return None, "tentativo del %s fatto col nome %r: il book ora dice %r, si riprova" % (
                v.get("tentato_il"), ris.get("nome_usato"), nome_db)
    ris["memoria"] = {"tentato_il": v.get("tentato_il"), "riprova_dopo": scade.isoformat(timespec="seconds"),
                      "ttl_s": ttl, "percorso": _percorso_tentativi()}
    ris["motivo"] = ("risoluzione gia' tentata il %s, esito %s (nessuna nuova richiesta fino al %s): %s"
                     % (v.get("tentato_il"), ris.get("stato"), ris["memoria"]["riprova_dopo"], ris.get("motivo")))
    return ris, None


# ------------------------------------------------------------ risoluzione
def _risolvi(ticker: str, nome: str) -> Dict[str, Any]:
    """Unico punto che chiama la rete di Borsa Italiana (i test lo sostituiscono)."""
    return _bi.risolvi_isin(ticker, nome=nome)


def _esito(stato, errore, motivo, *, isin=None, salvato=False, negozio=None, nome=None, fonte=None):
    return {"stato": stato, "errore": errore, "motivo": motivo, "isin": isin, "salvato": salvato,
            "negozio": negozio, "nome_usato": nome, "fonte_nome": fonte}


def assicura_isin_it(ticker: Any, *, db_path: Optional[str] = None, nome: Optional[str] = None,
                     fonte_nome: Optional[str] = None, budget_s: Any = "contesto"
                     ) -> Optional[Dict[str, Any]]:
    """Vedi il docstring del modulo. Da chiamare PRIMA del lettore (get_eventi_societari,
    get_internal_dealing, get_data_deposito): con esito ok il lettore trova la voce automatica.
    `nome` esplicito (es. l'identita' confermata della Trade Idea) salta book e fornitore
    prezzi; va con `fonte_nome` che lo DICHIARA (senza = ValueError: niente nome anonimo)."""
    if nome is not None:
        nome = str(nome).strip()
        if not str(fonte_nome or "").strip():
            raise ValueError("assicura_isin_it: nome esplicito senza fonte_nome dichiarata")
    t = str(ticker or "").strip().upper()
    if not _SIMBOLO_MI.fullmatch(t):
        return None
    voce, err, _mot = _bi.voce_ticker_o_auto(t)
    if voce is not None or err == "negozio_illeggibile":
        return None
    budget = _BUDGET.get() if budget_s == "contesto" else budget_s
    with _LOCK_IN_CORSO:
        lav = _IN_CORSO.get(t)
        gia_in_corso = lav is not None
        if lav is None:   # UNA sola risoluzione in volo per ticker (thread daemon)
            lav = {"evento": threading.Event(), "ris": None,
                   "avviata_il": _bi.adesso_utc().isoformat(timespec="seconds")}
            _IN_CORSO[t] = lav
            threading.Thread(target=_lavora, args=((t, db_path, nome, fonte_nome), lav), daemon=True,
                             name="isin-auto-%s" % t).start()
    if lav["evento"].wait(budget):
        ris = lav["ris"]
        if gia_in_corso and isinstance(ris, dict):
            ris = dict(ris, gia_in_corso_dal=lav["avviata_il"])
        return ris
    if gia_in_corso:
        motivo = ("risoluzione gia' in corso dal %s (avviata da un'altra chiamata): budget di %s s "
                  "superato, nessuna seconda risoluzione; riprova fra qualche minuto" % (lav["avviata_il"], budget))
    else:
        motivo = ("risoluzione ISIN su Borsa Italiana ancora in corso (avviata il %s): budget di %s s "
                  "superato, il tool non aspetta oltre; la risoluzione continua in background (tetto "
                  "dichiarato: tetto_teorico_s()) e scrive il suo esito: riprova fra qualche minuto"
                  % (lav["avviata_il"], budget))
    return _esito("KO", "tempo_esaurito", motivo)


_LOCK_IN_CORSO = threading.Lock()
_IN_CORSO: Dict[Any, Dict[str, Any]] = {}


def _lavora(argomenti, lav) -> None:
    t, db_path, nome, fonte_nome = argomenti
    try:
        lav["ris"] = _assicura_sotto_lock(t, db_path, nome, fonte_nome)
    except Exception as e:   # mai un thread morto zitto: KO dichiarato col tipo
        lav["ris"] = _esito("KO", type(e).__name__, "risoluzione ISIN fallita (%s)" % type(e).__name__)
    finally:
        with _LOCK_IN_CORSO:
            _IN_CORSO.pop(t, None)
        lav["evento"].set()


def _assicura_sotto_lock(t: str, db_path: Optional[str], nome: Optional[str],
                         fonte_nome: Optional[str]) -> Optional[Dict[str, Any]]:
    with _LOCK_RISOLUZIONE:
        # un'altra chiamata concorrente puo' averla appena risolta
        voce, err, _mot = _bi.voce_ticker_o_auto(t)
        if voce is not None or err == "negozio_illeggibile":
            return None
        ricordo, nota_memoria = _dal_ricordo(t, db_path, nome)
        if ricordo is not None:
            return ricordo
        if nome is None:
            nome, fonte_nome, perche = nome_emittente_it(t, db_path=db_path)
        else:
            perche = "nome esplicito vuoto (%s)" % fonte_nome
            if nome and e_il_simbolo(nome, t):
                nome, perche = "", "nome esplicito uguale al simbolo (%s): il simbolo non e' un nome" % fonte_nome
        if not nome:
            # review RV-W1 R1: un guasto TRANSITORIO (yfinance in errore, book lockato) e' KO con
            # memoria corta, non «non_trovato» per 72 h
            ris = _esito("KO" if _TRANSITORIO in (perche or "") else "non_trovato", "nome_assente",
                         "ISIN non risolvibile: nome dell'emittente non disponibile (%s)" % perche)
        else:
            try:
                r = _risolvi(t, nome)
            except Exception as e:
                r = {"stato": "KO", "errore": type(e).__name__,
                     "motivo": "risoluzione ISIN fallita (%s)" % type(e).__name__}
            if not isinstance(r, dict):
                r = {"stato": "KO", "errore": "esito_malformato",
                     "motivo": "risolvi_isin ha restituito %s" % type(r).__name__}
            ris = _esito(r.get("stato"), r.get("errore"), r.get("motivo"), isin=r.get("isin"),
                         salvato=bool(r.get("salvato")), negozio=r.get("negozio"), nome=nome, fonte=fonte_nome)
            if ris["stato"] == "ok":
                # MISURA, non fiducia: la voce dev'essere leggibile dal negozio come la leggera' il lettore
                dopo, err2, mot2 = _bi.voce_ticker_o_auto(t)
                if dopo is None:
                    ris.update(stato="KO", errore="voce_non_salvata",
                               motivo="ISIN %s verificato su Borsa Italiana ma la voce non e' leggibile dal "
                                      "negozio (%s: %s); motivo del risolutore: %s"
                                      % (ris["isin"], err2, mot2, r.get("motivo")))
                elif dopo.get("isin") != ris["isin"]:
                    ris.update(stato="KO", errore="voce_incoerente",
                               motivo="ISIN verificato %s ma il negozio %s dice %s"
                                      % (ris["isin"], dopo.get("negozio"), dopo.get("isin")))
            elif ris["stato"] not in ("non_trovato", "ambiguo", "KO"):
                ris.update(stato="KO", errore="stato_inatteso",
                           motivo="risolvi_isin: stato inatteso %r (%s)" % (r.get("stato"), r.get("motivo")))
        if ris["stato"] != "ok":
            adesso = _bi.adesso_utc()
            ttl = _ttl(ris)
            non_scritta = _scrivi_tentativo(t, {"versione": MEMORIA_VERSIONE,
                                                "tentato_il": adesso.isoformat(timespec="seconds"),
                                                "ttl_s": ttl, "ris": ris})
            ris = dict(ris)
            ris["memoria"] = {"tentato_il": adesso.isoformat(timespec="seconds"),
                              "riprova_dopo": (adesso + timedelta(seconds=ttl)).isoformat(timespec="seconds"),
                              "ttl_s": ttl, "percorso": _percorso_tentativi()}
            if non_scritta:
                ris["memoria"]["nota"] = non_scritta
        if nota_memoria:
            ris.setdefault("memoria", {})["nota_lettura"] = nota_memoria
        return ris


def nome_noto(ticker: Any, ris: Optional[Dict[str, Any]] = None, *,
              db_path: Optional[str] = None) -> Optional[str]:
    """Nome dell'emittente gia' noto SENZA rete, per l'instradamento SDIR (sdir.py, misura su
    1INFO): quello usato dalla risoluzione appena fatta, altrimenti quello della posizione nel
    book. None = sdir usa i nomi del negozio automatico (lo dichiara lui)."""
    if isinstance(ris, dict) and ris.get("nome_usato"):
        return ris["nome_usato"]
    return _nome_dal_book(str(ticker or "").strip().upper(), db_path)[0]


def tetto_teorico_s() -> float:
    """Tetto TEORICO (s) di una risoluzione in background, dai limiti DICHIARATI dei moduli:
    yfinance (TIMEOUT_YF_S) + ogni richiesta di risolvi_isin (pagine del listino per le iniziali
    provate, schede, menu eMarket) a timeout pieno piu' la pausa. Il thread e' daemon: alla
    chiusura del processo non resta appeso; il lock si rilascia anche in eccezione (with)."""
    iniziali = int(_bi.MAX_LETTERE)
    richieste = iniziali * int(_bi.MAX_PAGINE_LISTINO) + int(_bi.MAX_SCHEDE) + 1
    return float(TIMEOUT_YF_S + richieste * (float(_bi.TIMEOUT_S) + float(_bi.PAUSA_S)))


def giorni_validi(giorni: Any) -> bool:
    """Stessa regola del lettore (emarket_sdir / sdir): intero 1..GIORNI_MAX, mai bool."""
    from bellomberg.market_data import emarket_sdir as _em
    return not isinstance(giorni, bool) and isinstance(giorni, int) and 1 <= giorni <= _em.GIORNI_MAX


def risoluzione_ok(ris: Optional[Dict[str, Any]]) -> bool:
    return isinstance(ris, dict) and ris.get("stato") == "ok"


def con_risoluzione(payload: Any, ris: Optional[Dict[str, Any]]) -> Any:
    """Mette `risoluzione_isin` nel payload; se la risoluzione NON e' ok il motivo entra
    anche nell'errore (il KO «ticker_non_mappato» del lettore da solo non dice il perche')."""
    if ris is None or not isinstance(payload, dict):
        return payload
    payload["risoluzione_isin"] = ris
    if not risoluzione_ok(ris) and payload.get("error"):
        payload["error"] = "%s | risoluzione ISIN %s: %s" % (payload["error"], ris.get("stato"), ris.get("motivo"))
    return payload
