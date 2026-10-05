# -*- coding: utf-8 -*-
"""borsa_italiana.py — EVENTI SOCIETARI dei titoli italiani da Borsa Italiana (voce 6,
fonti italiane gratuite, 04/10/2026, Opus 5.5).

PERCHE' ESISTE. Per un `.MI` il calendario oggi passa da Finnhub, che riceve il simbolo senza
suffisso e sui nomi solo-Italia del piano free non ha dati: il calendario dei risultati dei
titoli italiani semplicemente non c'era. Borsa Italiana pubblica per ogni ISIN la pagina
«elenco completo eventi» (tabella HTML statica, gratuita, senza login).

MISURE DELLA SONDA (04/10/2026, richieste vere, poche):
  - `/borsa/azioni/elenco-completo-eventi.html?isin=<ISIN>&lang=it` -> 200, una `<table>` con
    intestazioni «Eventi Sottostante» / «Data Evento» e righe `<td><span class="t-text">`.
    Il parametro `mic` NON serve (stessa tabella senza). Date a DUE cifre: `dd/mm/yy`.
  - Le righe compaiono spesso DOPPIE (stesso evento, stessa data): si tolgono i doppioni e
    il numero si DICHIARA (`duplicati_rimossi`).
  - ISIN che Borsa non conosce -> 200 ma con un'ALTRA pagina (il listino A-Z): nessuna tabella
    eventi -> KO «pagina diversa», mai «nessun evento».
  - Pagina senza `isin` -> HTTP 500.
  - robots.txt: vietati `*page=` su questa lista, `*?filename` (i PDF), `*?ord=`/`*&ord=`.
    Quindi SOLO la prima pagina (le ~20 righe piu' recenti): LIMITE DICHIARATO nel payload.

CONTRATTO (lo legge chi cabla i tool — agent_tools/chat_tools — in un lotto successivo):
    get_eventi_societari(ticker, *, isin=None) -> {
        "ticker", "isin",
        "eventi": [{"data": "AAAA-MM-GG" | None, "tipo": str, "descrizione": str,
                    "data_grezza": str}],          # ordine della pagina (piu' recenti prima)
        "prossimo": {evento} | None,               # il primo con data >= oggi (ora di Roma)
        "fonte": "Borsa Italiana", "url": str,
        "stato": "ok" | "vuoto_misurato" | "KO" | "non_coperto" | "STALE",
        "errore": codice | None, "motivo": str | None,
        "letto_il": ISO UTC della lettura dalla fonte | None,
        "limiti": [str], "duplicati_rimossi": int, "date_illeggibili": int, "righe_scartate": int,
        "cache": {"stato": "fresca"|"scaduta_servita"|"nessuna", "eta_s": float|None},
    }
  - `vuoto_misurato` = la tabella C'E' (intestazioni trovate) e non ha righe: e' una misura.
  - `KO` = rete/HTTP/tabella assente/layout cambiato/ISIN mancante o malformato: `errore` dice
    quale. `eventi` e' [] e NON significa «nessun evento».
  - `STALE` = la fonte e' in guasto e si serve l'ultima lettura buona scaduta; `motivo` porta
    il guasto, `letto_il` la data VERA della lettura servita, `cache.eta_s` l'eta'.
  - `tipo` e' una classe grezza (risultati / presentazione_analisti / assemblea / dividendo /
    cda / altro) dedotta da parole del testo; `descrizione` resta il testo della pagina.

NEGOZIO PRIVATO della mappa ticker -> ISIN / id eMarket: `data/isin_it.json` (forma in
`resources/examples/isin_it.example.json`, valori inventati), letto col caricatore comune di
`storage/negozi_privati.carica` A OGNI CHIAMATA; `PERCORSO_ISIN` si ridirige (prove).
Negozio assente/illeggibile o ticker non mappato = KO DICHIARATO, mai una ricerca indovinata.
"""
import json
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from html import unescape
from typing import Any, Callable, Dict, List, Optional, Tuple

from bellomberg.core.paths import DATA_DIR
from bellomberg.storage import negozi_privati as _np

FONTE = "Borsa Italiana"
BASE = "https://www.borsaitaliana.it"
URL_EVENTI = BASE + "/borsa/azioni/elenco-completo-eventi.html?isin={isin}&lang=it"

PERCORSO_ISIN = str(DATA_DIR / "isin_it.json")
ESEMPIO_ISIN = "isin_it.example.json"
CACHE_DIR = str(DATA_DIR / "cache_fonti_it")
TTL_EVENTI_S = 12 * 3600            # gli eventi cambiano di rado: una lettura ogni 12 ore
TIMEOUT_S = 25
USER_AGENT = "Bellomberg/1.0 (ricerca personale, basso volume)"

LIMITE_PRIMA_PAGINA = ("solo la prima pagina della lista (le righe piu' recenti): robots.txt "
                       "di Borsa Italiana vieta la paginazione (page=)")

# robots.txt misurati il 04/10/2026 (Borsa Italiana ed eMarket Storage): i soli divieti che
# toccano gli URL che questi moduli costruiscono. Una richiesta che ci cade si RIFIUTA prima
# della rete (`URLVietato`), non si manda «tanto e' una».
_VIETATI = {
    "www.borsaitaliana.it": (
        re.compile(r"\?filename"),
        re.compile(r"[?&]ord="),
        # page= vietato sulle liste che il robots nomina (elenchi completi, documenti societari,
        # tutte le pagine «contratti»): la regola copre ANCHE qualche lista in piu' del robots,
        # mai di meno. Il Listino A-Z il robots non lo nomina: li' page= e' permesso (misura
        # 04/10, serve a risolvi_isin).
        re.compile(r"^/borsa/(?:(?:quotazioni/)?azioni/elenco-completo-[a-z-]+\.html|"
                   r"azioni/documenti/societa-quotate/[a-z-]+\.html|"
                   r"(?:quotazioni/indici|derivati/[a-z-]+|azioni|etf|azioni/mercato-serale|"
                   r"azioni/aim-italia|obbligazioni/mot/btp)/contratti\.html).*page="),
        re.compile(r"^/(?:media|avvisi-negoziazione|knockout)/.*\.pdf"),
        re.compile(r"^/(?:mediasource/borsa/db/pdf/|pdf/frame|borsa/searchengine|"
                   r"borsa/caratteristiche/view\.html|borsa/notizie/mf-dow-jones/|"
                   r"borsa/portafoglio/aggiungi/|borsa/pagina-personale/alerts/|bitApp/search\.bit)"),
    ),
    "www.emarketstorage.it": (
        re.compile(r"^/(?:index\.php/)?(?:search|admin|user|node/add|comment/reply|filter/tips)(?:/|$)"),
        re.compile(r"^/(?:core|profiles)/"),
        re.compile(r"^/(?:README\.txt|web\.config)"),
    ),
}


class URLVietato(ValueError):
    """L'URL cade in un Disallow del robots.txt misurato (o in un host non previsto)."""


def url_consentito(url: str) -> Tuple[bool, str]:
    """(consentito, motivo). Solo https verso gli host previsti; percorso+query contro i
    Disallow misurati. Un host non previsto e' un RIFIUTO: questi moduli non vanno altrove."""
    m = re.match(r"^https://([^/?#]+)(/[^#]*)?$", url or "")
    if not m:
        return False, "URL non https o malformato"
    host, resto = m.group(1).lower(), (m.group(2) or "/")
    regole = _VIETATI.get(host)
    if regole is None:
        return False, "host %s fuori dalle fonti previste" % host
    for r in regole:
        if r.search(resto):
            return False, "vietato da robots.txt di %s (regola %s)" % (host, r.pattern)
    return True, ""


MAX_REDIRECT = 5


def _scarica(url: str) -> Tuple[int, bytes, str]:
    """GET con User-Agent dichiarato. Torna (HTTP, corpo, url finale). Rifiuta PRIMA della rete
    gli URL vietati da robots. I redirect si seguono A MANO (allow_redirects=False): ogni salto
    si controlla PRIMA di richiederlo, cosi' un URL vietato non parte mai (review RV-C 04/10)."""
    from urllib.parse import urljoin
    import requests
    corrente = url
    for salto in range(MAX_REDIRECT + 1):
        ok, motivo = url_consentito(corrente)
        if not ok:
            raise URLVietato(motivo if salto == 0 else "redirect verso %s: %s" % (corrente, motivo))
        r = requests.get(corrente, headers={"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9"},
                         timeout=TIMEOUT_S, allow_redirects=False)
        dove = (getattr(r, "headers", None) or {}).get("Location")
        if r.status_code in (301, 302, 303, 307, 308) and dove:
            corrente = urljoin(corrente, dove)
            continue
        return r.status_code, r.content, corrente
    raise URLVietato("piu' di %d redirect a partire da %s" % (MAX_REDIRECT, url))


def adesso_utc() -> datetime:
    return datetime.now(timezone.utc)


def oggi_roma() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Rome")).date()
    except Exception:
        # senza dati IANA il giorno UTC sbaglia al massimo di un giorno a cavallo della
        # mezzanotte: e' l'unico uso (scegliere il «prossimo») e lo si dichiara nei limiti.
        return adesso_utc().date()


# ============================================================
# DATE ITALIANE
# ============================================================
_DATA_IT = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")


def data_it(testo: Any) -> Optional[str]:
    """`dd/mm/yy` o `dd/mm/yyyy` -> 'AAAA-MM-GG'; None se non e' una data VERA (31/02, 13 come
    mese, anno a 3 cifre, testo in piu'). L'anno a due cifre e' 2000+aa: le liste di Borsa
    riguardano eventi recenti e futuri; un evento del '99 non vi compare (limite dichiarato)."""
    if not isinstance(testo, str):
        return None
    m = _DATA_IT.match(testo.strip())
    if not m:
        return None
    g, mese, a = int(m.group(1)), int(m.group(2)), m.group(3)
    anno = 2000 + int(a) if len(a) == 2 else int(a)
    try:
        return date(anno, mese, g).isoformat()
    except ValueError:
        return None


# ============================================================
# NEGOZIO PRIVATO ticker -> ISIN / id eMarket
# ============================================================
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def isin_valido(isin: Any) -> bool:
    """Forma (2 lettere paese + 9 alfanumerici + cifra di controllo) E cifra di controllo
    (Luhn sulle cifre, lettere A=10..Z=35): un ISIN copiato male non si manda alla fonte."""
    if not isinstance(isin, str) or not _ISIN.match(isin):
        return False
    cifre = "".join(str(int(c, 36)) for c in isin[:-1])
    somma = 0
    for i, c in enumerate(reversed(cifre)):
        n = int(c)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        somma += n
    return (10 - somma % 10) % 10 == int(isin[-1])


def _valida_isin_it(grezzo: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    def _voce(k, v):
        if not isinstance(v, dict):
            raise ValueError("voce %r: serve un oggetto {isin, emarket}" % k)
        fuori = sorted(set(v) - {"isin", "emarket", "oneinfo"})
        if fuori:
            raise ValueError("voce %r: campo %r sconosciuto (ammessi isin, emarket, oneinfo)" % (k, fuori[0]))
        isin = _np.stringa_piena("%s.isin" % k, v.get("isin"))
        if not isin_valido(isin):
            raise ValueError("voce %r: %r non e' un ISIN valido (forma o cifra di controllo)" % (k, isin))
        if "emarket" not in v:
            # obbligatorio anche se null: ometterlo si leggerebbe «non su eMarket», che deve
            # essere una DICHIARAZIONE di chi scrive il negozio, non una dimenticanza.
            raise ValueError("voce %r: manca 'emarket' (id intero, oppure null = emittente non su "
                             "eMarket SDIR, dichiarato)" % k)
        em = v["emarket"]
        if em is not None and (isinstance(em, bool) or not isinstance(em, int) or em <= 0):
            raise ValueError("voce %r: 'emarket' dev'essere un intero positivo o null, non %r" % (k, em))
        voce = {"isin": isin, "emarket": em}
        # 'oneinfo' FACOLTATIVO (handoff-3, richiesta di oneinfo_sdir): ndg dell'emittente su
        # 1INFO-SDIR (intero) | null = DICHIARATO non su 1INFO | chiave ASSENTE = non dichiarato,
        # lo misura chi legge. Assente e null sono due cose diverse: la chiave si copia solo se c'e'.
        if "oneinfo" in v:
            oi = v["oneinfo"]
            if oi is not None and (isinstance(oi, bool) or not isinstance(oi, int) or oi <= 0):
                raise ValueError("voce %r: 'oneinfo' dev'essere un intero positivo o null, non %r" % (k, oi))
            voce["oneinfo"] = oi
        return voce
    return _np.mappa_canonica(grezzo, _voce, "simbolo")


def carica_isin_it(path: Optional[str] = None) -> Dict[str, Any]:
    """{"voci": {SIMBOLO: {"isin", "emarket": int|None}}, origine, motivo} col caricatore
    comune: assente/illeggibile = voci vuote e motivo scritto; una voce malformata rende
    illeggibile il negozio INTERO."""
    return _np.carica(path or PERCORSO_ISIN, ESEMPIO_ISIN, _valida_isin_it, "voci")


def voce_ticker(ticker: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """(voce, errore, motivo). Rilegge il negozio a ogni chiamata (PERCORSO_ISIN al momento)."""
    neg = carica_isin_it(PERCORSO_ISIN)
    if neg["origine"] in ("assente", "illeggibile"):
        return None, "negozio_%s" % neg["origine"], "mappa ticker->ISIN %s: %s" % (neg["origine"], neg["motivo"])
    chiave = (ticker or "").strip().upper()
    voce = neg["voci"].get(chiave)
    if voce is None:
        return None, "ticker_non_mappato", ("ticker %r assente da %s: aggiungi la voce con l'ISIN "
                                            "verificato (nessuna ricerca per nome)" % (chiave, PERCORSO_ISIN))
    return voce, None, None


# ------------------------------------------------------------
# NEGOZIO AUTOMATICO (decisione PM 04/10): data/isin_it_auto.json, scritto SOLO da
# risolvi_isin dopo la verifica sulla scheda di Borsa. Il negozio confermato dal PM ha la
# precedenza; chi legge dichiara SEMPRE da quale negozio viene la voce (`negozio`).
# ------------------------------------------------------------
PERCORSO_ISIN_AUTO = str(DATA_DIR / "isin_it_auto.json")
ORIGINE_AUTO = "verificato automaticamente su Borsa Italiana il %s"
_CAMPI_AUTO = ("isin", "emarket", "emarket_motivo", "origine", "verificato_il", "nome_cercato",
               "riga_listino", "scheda_url", "scheda_sha256", "oneinfo", "mic")


def _valida_isin_auto(grezzo: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    def _voce(k, v):
        if not isinstance(v, dict):
            raise ValueError("voce automatica %r: serve un oggetto" % k)
        fuori = sorted(set(v) - set(_CAMPI_AUTO))
        if fuori:
            raise ValueError("voce automatica %r: campo %r sconosciuto" % (k, fuori[0]))
        base = _valida_isin_it({k: {c: v.get(c) for c in ("isin", "emarket", "oneinfo")
                                    if c in v or c != "oneinfo"}})[k]
        origine = _np.stringa_piena("%s.origine" % k, v.get("origine"))
        if not origine.startswith("verificato automaticamente su Borsa Italiana il "):
            raise ValueError("voce automatica %r: origine %r non riconosciuta" % (k, origine))
        if "riga_listino" in v:   # facoltativo (voci vecchie senza): se c'e', testo pieno
            _np.stringa_piena("%s.riga_listino" % k, v["riga_listino"])
        if "mic" in v:            # facoltativo: mercato della riga del listino (MTAA, EXGM, BGEM...)
            if not isinstance(v["mic"], str) or not re.fullmatch(r"[A-Z0-9]{2,10}", v["mic"]):
                raise ValueError("voce automatica %r: 'mic' dev'essere una sigla maiuscola alfanumerica, "
                                 "non %r" % (k, v["mic"]))
        return dict(v, **base)
    return _np.mappa_canonica(grezzo, _voce, "simbolo")


def carica_isin_auto(path: Optional[str] = None) -> Dict[str, Any]:
    return _np.carica(path or PERCORSO_ISIN_AUTO, "(scritto da risolvi_isin, nessun esempio)",
                      _valida_isin_auto, "voci")


# ------------------------------------------------------------
# SCRITTURA CONCORRENTE (rilievo R3 di RV-W1, 05/10): due risoluzioni in parallelo (thread
# dell'API, run, scheduler: anche PROCESSI diversi) facevano leggi-modifica-scrivi senza
# lock -> una voce «ok, salvato» spariva, o os.replace falliva con PermissionError su Windows.
# Cura: lock DI FILE fra processi (<percorso>.lock, primo byte bloccato con msvcrt/fcntl: il
# sistema lo rilascia da solo se il processo muore, niente lock orfani) + lock di processo per
# percorso (fcntl non esclude i thread dello stesso processo); dentro il lock si RILEGGE il
# file dal disco, si unisce, si scrive temporaneo + os.replace con nuovi tentativi brevi.
# ------------------------------------------------------------
ATTESA_LOCK_S = 10.0
_LOCK_PROCESSO: Dict[str, Any] = {}
_LOCK_MAPPA = threading.Lock()


class RifiutoModifica(Exception):
    """La `modifica` passata ad aggiorna_json_bloccato rifiuta di scrivere: `motivo` va al
    chiamante cosi' com'e' (testo nostro, mai il testo di un'eccezione esterna)."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def _blocca_byte(fd: int) -> bool:
    """Un tentativo NON bloccante sul primo byte del file di lock. True = preso."""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
        return True
    except OSError:
        return False


def _sblocca_byte(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fd, fcntl.LOCK_UN, 1, 0)


def _replace_con_tentativi(tmp: str, percorso: str, attesa_s: float = 2.0) -> None:
    """os.replace; su Windows un LETTORE che ha il file aperto lo fa fallire con
    PermissionError: si riprova per al massimo `attesa_s`, poi l'errore sale."""
    fine = time.monotonic() + attesa_s
    while True:
        try:
            os.replace(tmp, percorso)
            return
        except PermissionError:
            if time.monotonic() >= fine:
                raise
            time.sleep(0.02)


def aggiorna_json_bloccato(percorso: str, modifica: Callable[[Dict[str, Any]], Dict[str, Any]], *,
                           attesa_s: float = ATTESA_LOCK_S,
                           illeggibile_si_riscrive: bool = False) -> Tuple[bool, Optional[str]]:
    """Leggi-modifica-scrivi di un file JSON (oggetto) sotto lock fra thread E processi.
    - lock: `<percorso>.lock` accanto al file; attesa al massimo `attesa_s` (orologio monotonico),
      poi (False, motivo) dichiarato: nessuna scrittura senza lock.
    - dentro il lock il file si RILEGGE dal disco: assente = {}; illeggibile (JSON rotto o non
      oggetto) = (False, motivo) e NON si sovrascrive, salvo `illeggibile_si_riscrive=True`
      (solo per file che sono cache, es. i tentativi di isin_automatico).
    - `modifica(dati) -> nuovi dati` (puo' sollevare RifiutoModifica(motivo) per non scrivere);
    - scrittura: temporaneo nella stessa cartella + os.replace con tentativi su PermissionError.
    Ritorna (True, None) | (False, motivo); gli errori portano solo il TIPO dell'eccezione."""
    assoluto = os.path.abspath(percorso)
    nome = os.path.basename(assoluto)
    with _LOCK_MAPPA:
        lock_proc = _LOCK_PROCESSO.setdefault(os.path.normcase(assoluto), threading.Lock())
    fine = time.monotonic() + attesa_s
    if not lock_proc.acquire(timeout=max(attesa_s, 0.0)):
        return False, "lock di %s non ottenuto in %.1f s (altra scrittura in corso): non scritto" % (nome, attesa_s)
    try:
        try:
            os.makedirs(os.path.dirname(assoluto), exist_ok=True)
            fd = os.open(assoluto + ".lock", os.O_CREAT | os.O_RDWR)
        except OSError as e:
            return False, "file di lock di %s non aperto: %s" % (nome, type(e).__name__)
        try:
            while not _blocca_byte(fd):
                if time.monotonic() >= fine:
                    return False, ("lock di %s non ottenuto in %.1f s (un altro processo sta scrivendo): "
                                   "non scritto" % (nome, attesa_s))
                time.sleep(0.02)
            try:
                return _leggi_modifica_scrivi(assoluto, nome, modifica, illeggibile_si_riscrive)
            finally:
                _sblocca_byte(fd)
        finally:
            os.close(fd)
    finally:
        lock_proc.release()


def _leggi_modifica_scrivi(assoluto: str, nome: str, modifica: Callable[[Dict[str, Any]], Dict[str, Any]],
                           illeggibile_si_riscrive: bool) -> Tuple[bool, Optional[str]]:
    """Il corpo di aggiorna_json_bloccato: va chiamato SOLO col lock preso."""
    dati: Dict[str, Any] = {}
    if os.path.exists(assoluto):
        try:
            with open(assoluto, encoding="utf-8") as fh:
                letto = json.load(fh)
            if not isinstance(letto, dict):
                raise ValueError("non oggetto")
            dati = letto
        except (OSError, ValueError) as e:
            if not illeggibile_si_riscrive:
                return False, "%s illeggibile (%s): non sovrascritto" % (nome, type(e).__name__)
    try:
        nuovi = modifica(dati)
    except RifiutoModifica as r:
        return False, r.motivo
    fdt, tmp = tempfile.mkstemp(dir=os.path.dirname(assoluto), suffix=".tmp")
    try:
        with os.fdopen(fdt, "w", encoding="utf-8") as fh:
            json.dump(nuovi, fh, ensure_ascii=False, indent=2)
        _replace_con_tentativi(tmp, assoluto)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "scrittura di %s fallita: %s" % (nome, type(e).__name__)
    return True, None


def _scrivi_voce_auto(simbolo: str, voce: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Aggiunge/aggiorna UNA voce del negozio automatico sotto lock (aggiorna_json_bloccato):
    il negozio si rilegge dal disco DENTRO il lock, quindi le voci scritte nel frattempo da
    altri thread o processi restano. Un negozio automatico ILLEGGIBILE (JSON rotto o voce
    malformata) non si sovrascrive (si perderebbero le altre voci): si rifiuta e si dichiara."""
    def _modifica(grezzo: Dict[str, Any]) -> Dict[str, Any]:
        try:
            _valida_isin_auto({k: v for k, v in grezzo.items() if not str(k).startswith("_")})
        except ValueError as e:
            raise RifiutoModifica("voce malformata (%s)" % type(e).__name__)
        grezzo[simbolo] = {k: voce[k] for k in _CAMPI_AUTO if k in voce}
        return grezzo
    ok, motivo = aggiorna_json_bloccato(PERCORSO_ISIN_AUTO, _modifica)
    if ok:
        return True, None
    if "illeggibile" in (motivo or "") or "malformata" in (motivo or ""):
        return False, "negozio automatico illeggibile, non sovrascritto: %s" % motivo
    return False, "negozio automatico non scritto: %s" % motivo


def voce_ticker_o_auto(ticker: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Come voce_ticker, ma se il ticker non e' nel negozio CONFERMATO cerca nel negozio
    AUTOMATICO. La voce porta sempre `negozio`: 'confermato' | 'automatico'.
    Negozio confermato ILLEGGIBILE = KO (non si scavalca un negozio rotto del PM); ASSENTE =
    si guarda l'automatico, e la nota lo dice."""
    conf = carica_isin_it(PERCORSO_ISIN)
    chiave = (ticker or "").strip().upper()
    if conf["origine"] == "illeggibile":
        return None, "negozio_illeggibile", "mappa ticker->ISIN illeggibile: %s" % conf["motivo"]
    if chiave in conf["voci"]:
        return dict(conf["voci"][chiave], negozio="confermato"), None, None
    auto = carica_isin_auto(PERCORSO_ISIN_AUTO)
    if auto["origine"] == "illeggibile":
        return None, "negozio_illeggibile", "negozio automatico illeggibile: %s" % auto["motivo"]
    if chiave in auto["voci"]:
        return dict(auto["voci"][chiave], negozio="automatico"), None, None
    if conf["origine"] == "assente" and auto["origine"] == "assente":
        return None, "negozio_assente", ("mappa ticker->ISIN assente: %s; negozio automatico assente "
                                         "(%s)" % (conf["motivo"], PERCORSO_ISIN_AUTO))
    return None, "ticker_non_mappato", ("ticker %r assente dal negozio confermato (%s) e da quello "
                                        "automatico (%s): risolvi_isin(ticker, nome=...) lo verifica "
                                        "su Borsa Italiana" % (chiave, PERCORSO_ISIN, PERCORSO_ISIN_AUTO))


def _campo_automatico(ticker: str, campo: str) -> Optional[str]:
    """Un campo della voce AUTOMATICA del ticker, senza rete. Per un ticker del negozio
    CONFERMATO solo se la voce automatica ha lo STESSO ISIN (il confermato ha la precedenza e
    non porta questi campi). None se manca, se il negozio e' illeggibile o il ticker non c'e'."""
    voce, _err, _mot = voce_ticker_o_auto(ticker)
    if voce is None:
        return None
    if voce["negozio"] == "automatico":
        return voce.get(campo)
    auto = carica_isin_auto(PERCORSO_ISIN_AUTO)
    va = auto["voci"].get((ticker or "").strip().upper())
    if va and va.get("isin") == voce.get("isin"):
        return va.get(campo)
    return None


def nome_listino(ticker: str) -> Optional[str]:
    """Nome della RIGA del Listino A-Z di Borsa Italiana con cui risolvi_isin ha verificato il
    titolo (campo `riga_listino` del negozio AUTOMATICO), SENZA rete. Serve a chi cerca
    l'emittente altrove col nome ufficiale del listino (sdir/1INFO: nome storico diverso dal
    nome societario). None: v. _campo_automatico (anche voce vecchia senza il campo)."""
    return _campo_automatico(ticker, "riga_listino")


def mercato_listino(ticker: str) -> Optional[str]:
    """Mercato (MIC) della riga del Listino A-Z verificata da risolvi_isin: 'MTAA' Euronext
    Milan, 'EXGM' Euronext Growth Milan, 'BGEM' Global Equity Market (azione ESTERA quotata
    in Italia). SENZA rete. None per le voci scritte prima del 05/10 (campo assente: non
    dedotto dall'URL della scheda) e nei casi di _campo_automatico."""
    return _campo_automatico(ticker, "mic")


# ============================================================
# CACHE SU FILE (TTL, STALE dichiarato)
# ============================================================
def _cache_path(chiave: str) -> str:
    return os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", chiave) + ".json")


def cache_leggi(chiave: str) -> Optional[Dict[str, Any]]:
    """{"salvato_ts", "risultato"} o None se assente/illeggibile."""
    try:
        with open(_cache_path(chiave), encoding="utf-8") as fh:
            c = json.load(fh)
        if isinstance(c, dict) and isinstance(c.get("risultato"), dict) and \
                isinstance(c.get("salvato_ts"), (int, float)):
            return c
    except (OSError, ValueError):
        pass
    return None


def cache_scrivi(chiave: str, risultato: Dict[str, Any]) -> None:
    """Scrittura atomica (temporaneo + os.replace): un file a meta' non si serve mai."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"salvato_ts": time.time(), "risultato": risultato}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(chiave))
    except OSError:
        pass  # la cache e' un'ottimizzazione: senza, la prossima chiamata rilegge la fonte


def eta_cache(c: Dict[str, Any]) -> Optional[float]:
    """Eta' in secondi; None se negativa (orologio tornato indietro): un'eta' non misurabile
    NON e' «fresca»."""
    eta = time.time() - float(c["salvato_ts"])
    return eta if eta >= 0 else None


def con_cache(chiave: str, ttl_s: float, leggi: Callable[[], Dict[str, Any]],
              salva: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Dict[str, Any]:
    """Cache fresca -> servita. Altrimenti `leggi()`; se la lettura e' ok/vuoto_misurato si
    salva. Se la lettura e' KO e c'e' una lettura buona SCADUTA -> la si serve come STALE col
    guasto nel motivo (mai in silenzio). KO senza cache -> il KO."""
    c = cache_leggi(chiave)
    if c is not None:
        eta = eta_cache(c)
        if eta is not None and eta < ttl_s:
            out = dict(c["risultato"])
            out["cache"] = {"stato": "fresca", "eta_s": round(eta, 1)}
            return out
    nuovo = leggi()
    if nuovo.get("stato") in ("ok", "vuoto_misurato"):
        # `salva(r)` False = lettura buona ma da ritentare (es. PDF non scaricati): non si
        # congela in cache per tutto il TTL
        if salva is None or salva(nuovo):
            cache_scrivi(chiave, {k: v for k, v in nuovo.items() if k != "cache"})
        nuovo["cache"] = {"stato": "nessuna", "eta_s": None}
        return nuovo
    if nuovo.get("stato") == "KO" and c is not None:
        vecchio = dict(c["risultato"])
        eta = eta_cache(c)
        vecchio["stato_originale"] = vecchio.get("stato")
        vecchio["stato"] = "STALE"
        vecchio["errore"] = nuovo.get("errore")
        vecchio["motivo"] = ("fonte in guasto (%s): servita l'ultima lettura buona del %s, eta' %s"
                             % (nuovo.get("motivo"), vecchio.get("letto_il"),
                                "%.0f s" % eta if eta is not None else "non misurabile (orologio indietro)"))
        vecchio["cache"] = {"stato": "scaduta_servita", "eta_s": round(eta, 1) if eta is not None else None}
        return vecchio
    nuovo["cache"] = {"stato": "nessuna", "eta_s": None}
    return nuovo


# ============================================================
# PARSER DELLA PAGINA EVENTI
# ============================================================
_TABELLA = re.compile(r"<table\b.*?</table>", re.S | re.I)
_RIGA = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELLA = re.compile(r"<t([dh])\b[^>]*>(.*?)</t\1>", re.S | re.I)


def _testo_cella(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


_CLASSI = (
    ("assemblea", re.compile(r"assemblea", re.I)),
    ("presentazione_analisti", re.compile(r"presentazione\s+analisti|analyst", re.I)),
    ("dividendo", re.compile(r"dividend|stacco", re.I)),
    ("risultati", re.compile(r"relazione|resoconto|informazioni\s+finanziarie|bilancio|trimestral|semestral", re.I)),
    ("cda", re.compile(r"\bcda\b|consiglio", re.I)),
)


def classe_evento(descrizione: str) -> str:
    """Classe grezza dal testo: vince la PRIMA classe che combacia nell'ordine sopra
    («Assemblea Bilancio» -> assemblea, «Cda Bilancio» -> risultati). Nessuna parola nota =
    'altro' (dichiarato, non indovinato): il testo vero resta in `descrizione`."""
    for nome, rx in _CLASSI:
        if rx.search(descrizione):
            return nome
    return "altro"


def parse_eventi(html: str) -> Dict[str, Any]:
    """Dalla pagina alla lista. Torna {"stato": ok|vuoto_misurato|KO, "eventi", "errore",
    "motivo", "duplicati_rimossi", "date_illeggibili"}.
    Tabella eventi = la <table> la cui riga d'intestazione nomina «Eventi» e «Data Evento».
    Assente -> KO 'tabella_assente'. Presente con 0 righe dati -> vuoto_misurato. Righe dati
    presenti ma nessuna con 2 celle -> KO 'layout_cambiato'. Date tutte illeggibili -> KO."""
    tab = None
    for t in _TABELLA.findall(html or ""):
        intest = " ".join(_testo_cella(c) for _, c in _CELLA.findall(" ".join(
            r for r in _RIGA.findall(t) if re.search(r"<th\b", r, re.I))))
        if re.search(r"\bEventi\b", intest, re.I) and re.search(r"Data\s+Evento", intest, re.I):
            tab = t
            break
    if tab is None:
        return {"stato": "KO", "eventi": [], "errore": "tabella_assente",
                "motivo": "nessuna tabella con intestazioni «Eventi»/«Data Evento»: ISIN non "
                          "riconosciuto da Borsa (pagina diversa) o layout cambiato",
                "duplicati_rimossi": 0, "date_illeggibili": 0}
    righe_dati = [r for r in _RIGA.findall(tab)
                  if re.search(r"<td\b", r, re.I)]
    if not righe_dati:
        return {"stato": "vuoto_misurato", "eventi": [], "errore": None,
                "motivo": "tabella eventi presente e senza righe", "duplicati_rimossi": 0,
                "date_illeggibili": 0}
    eventi: List[Dict[str, Any]] = []
    visti = set()
    duplicati = illeggibili = scartate = 0
    for r in righe_dati:
        celle = [_testo_cella(c) for _, c in _CELLA.findall(r)]
        if len(celle) != 2 or not celle[0]:
            scartate += 1   # contata e dichiarata, mai saltata zitta (review RV-C 04/10)
            continue
        desc, grezza = celle
        chiave = (desc, grezza)
        if chiave in visti:
            duplicati += 1
            continue
        visti.add(chiave)
        d = data_it(grezza)
        if d is None:
            illeggibili += 1
        eventi.append({"data": d, "tipo": classe_evento(desc), "descrizione": desc,
                       "data_grezza": grezza})
    if not eventi:
        return {"stato": "KO", "eventi": [], "errore": "layout_cambiato",
                "motivo": "%d righe nella tabella eventi ma nessuna con le 2 celle attese "
                          "(evento, data): layout cambiato" % len(righe_dati),
                "duplicati_rimossi": 0, "date_illeggibili": 0}
    if illeggibili == len(eventi):
        return {"stato": "KO", "eventi": [], "errore": "date_illeggibili",
                "motivo": "nessuna data leggibile in %d righe (formato atteso dd/mm/yy o dd/mm/yyyy): "
                          "formato cambiato" % len(eventi),
                "duplicati_rimossi": duplicati, "date_illeggibili": illeggibili}
    note = []
    if illeggibili:
        note.append("%d righe con data illeggibile tenute con data=None" % illeggibili)
    if scartate:
        note.append("%d righe scartate perche' non hanno le 2 celle attese (evento, data)" % scartate)
    return {"stato": "ok", "eventi": eventi, "errore": None, "motivo": "; ".join(note) or None,
            "duplicati_rimossi": duplicati, "date_illeggibili": illeggibili, "righe_scartate": scartate}


def prossimo_evento(eventi: List[Dict[str, Any]], oggi: Optional[date] = None) -> Optional[Dict[str, Any]]:
    oggi_iso = (oggi or oggi_roma()).isoformat()
    futuri = [e for e in eventi if e.get("data") and e["data"] >= oggi_iso]
    return min(futuri, key=lambda e: e["data"]) if futuri else None


# ============================================================
# LA FUNZIONE PUBBLICA
# ============================================================
def _base(ticker: str, isin: Optional[str]) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper(), "isin": isin, "eventi": [], "prossimo": None,
            "fonte": FONTE, "url": URL_EVENTI.format(isin=isin) if isin else None,
            "stato": "KO", "errore": None, "motivo": None, "letto_il": None,
            "limiti": [LIMITE_PRIMA_PAGINA], "duplicati_rimossi": 0, "date_illeggibili": 0,
            "righe_scartate": 0}


def _leggi_eventi(ticker: str, isin: str) -> Dict[str, Any]:
    out = _base(ticker, isin)
    try:
        http, corpo, _finale = _scarica(out["url"])
    except URLVietato as e:  # testo controllato: il messaggio puo' contenere l'URL del redirect (regola 16)
        out.update(errore="url_vietato", motivo="URL rifiutato prima della rete (%s: robots.txt o host non "
                                                "previsto)" % type(e).__name__)
        return out
    except Exception as e:  # rete: solo il TIPO (regola: niente testo con URL nei log)
        out.update(errore="rete", motivo="richiesta fallita: %s" % type(e).__name__)
        return out
    if http != 200:
        out.update(errore="http", motivo="HTTP %s da Borsa Italiana" % http)
        return out
    p = parse_eventi(corpo.decode("utf-8", errors="replace"))
    out.update(stato=p["stato"], eventi=p["eventi"], errore=p["errore"], motivo=p["motivo"],
               duplicati_rimossi=p["duplicati_rimossi"], date_illeggibili=p["date_illeggibili"],
               righe_scartate=p.get("righe_scartate", 0))
    if p["stato"] in ("ok", "vuoto_misurato"):
        out["letto_il"] = adesso_utc().isoformat(timespec="seconds")
    return out


def get_eventi_societari(ticker: str, *, isin: Optional[str] = None) -> Dict[str, Any]:
    """Eventi societari di un titolo italiano (vedi CONTRATTO nel docstring del modulo).
    `isin` esplicito: se il negozio ha la voce del ticker deve COINCIDERE (altrimenti KO
    'isin_incoerente'); se non ce l'ha si legge e `limiti` lo dichiara. Senza `isin`, l'ISIN
    viene da data/isin_it.json. Nessun ISIN
    = KO dichiarato (Borsa senza ISIN risponde 500: niente richiesta a vuoto)."""
    if isin is not None:
        isin = isin.strip().upper()
        if not isin_valido(isin):
            out = _base(ticker, None)
            out.update(errore="isin_malformato", motivo="ISIN %r non valido (forma o cifra di controllo)" % isin)
            out["cache"] = {"stato": "nessuna", "eta_s": None}
            return out
        # review RV-C 04/10: l'ISIN esplicito si INCROCIA col negozio. Diverso dalla voce del
        # ticker = KO (gli eventi di un altro titolo uscirebbero firmati col ticker del book);
        # ticker senza voce = si legge, ma il limite si dichiara.
        voce, _err, mot = voce_ticker_o_auto(ticker)
        if voce is not None and voce["isin"] != isin:
            out = _base(ticker, None)
            out.update(errore="isin_incoerente",
                       motivo="ISIN esplicito %s diverso da quello del negozio per %s (%s)"
                              % (isin, (ticker or "").strip().upper(), voce["isin"]))
            out["cache"] = {"stato": "nessuna", "eta_s": None}
            return out
        verificato = voce is not None
        voce_da = voce["negozio"] if voce is not None else "isin_esplicito"
    else:
        verificato = True
        voce, err, mot = voce_ticker_o_auto(ticker)
        if voce is None:
            out = _base(ticker, None)
            out.update(errore=err, motivo="ISIN mancante: %s" % mot)
            out["cache"] = {"stato": "nessuna", "eta_s": None}
            return out
        isin = voce["isin"]
        voce_da = voce["negozio"]
    out = con_cache("eventi_%s" % isin, TTL_EVENTI_S, lambda: _leggi_eventi(ticker, isin))
    out["ticker"] = (ticker or "").strip().upper()
    out["voce_da"] = voce_da   # 'confermato' | 'automatico' | 'isin_esplicito'
    out["prossimo"] = prossimo_evento(out.get("eventi") or [])
    if not verificato:
        out["limiti"] = list(out.get("limiti") or []) + [
            "ISIN non verificato dal negozio: %s passato a mano (%s), che sia il titolo giusto "
            "non e' controllato" % (isin, mot)]
    return out


# ============================================================
# RISOLUZIONE AUTOMATICA DELL'ISIN (decisione PM 04/10, opzione A di main)
# ============================================================
# MISURE (04/10/2026, poche richieste): su Borsa Italiana NON c'e' una ricerca per simbolo
# permessa — tutte le ricerche passano da /borsa/searchengine/ (Disallow), «Cerca titolo» ha
# solo settore e mercato, la scheda per simbolo da' 404. Il Listino A-Z
# (/borsa/azioni/listino-a-z.html?initial=X, page= permesso) da' NOME + link alla scheda
# (ISIN-MIC), senza simbolo; la scheda da' «Codice Isin» e «Codice Alfanumerico».
# Quindi: il NOME dell'emittente trova il candidato nel listino, la SCHEDA e' la prova
# (criterio IT2: un solo Codice Alfanumerico uguale al simbolo, stesso ISIN, cifra di
# controllo valida). Il nome non prova niente.
URL_LISTINO = BASE + "/borsa/azioni/listino-a-z.html?initial={iniziale}&lang=it"
URL_MENU_EMARKET = "https://www.emarketstorage.it/it/comunicati-finanziari?categoria=110"
MAX_PAGINE_LISTINO = 10
MAX_SCHEDE = 6
MAX_LETTERE = 3     # lettere del listino provate: una per parola significativa del nome (v. sotto)
PAUSA_S = 1.0
# MISURA 05/10 (IT1b, 8 richieste): il Listino A-Z mescola Euronext Milan (MTAA), Euronext
# Growth Milan (EXGM) e il Global Equity Market (BGEM, azioni ESTERE), ordinato per nome;
# ~20 righe a pagina, le lettere piu' lunghe misurate (C, S) hanno 6 pagine. La classe
# dell'azione sta nel NOME della riga («... Ord», «... Rsp», «... Pref»). Gli ETF NON ci
# sono (stanno sotto /borsa/etf/): un nome da ETF si dichiara subito, senza rete.
# Il nome del fornitore puo' iniziare con un'altra parola rispetto alla riga del listino
# (nome lungo «Assicurazioni X» -> riga «X» sotto un'altra lettera): se la prima lettera non
# da' righe che combaciano si provano le iniziali delle altre parole significative (al
# massimo MAX_LETTERE, ognuna DICHIARATA in verifica.iniziali_lette).

_FORME_GIURIDICHE = re.compile(
    r"\b(?:S\.?\s?P\.?\s?A|S\.?\s?A\.?\s?P\.?\s?A|S\.?\s?R\.?\s?L|N\.?\s?V|S\.?\s?E|S\.?\s?A|A\.?\s?G|PLC|INC|CORP|"
    r"SOCIETA'?\s+PER\s+AZIONI)\.?(?=\s|$)", re.I)
# forme che non sono MAI un nome da sole (una riga «Plc» invece e' un emittente)
_SOLO_FORMA_GENERICA = re.compile(r"(?:S\.?\s?P\.?\s?A|S\.?\s?A\.?\s?P\.?\s?A|S\.?\s?R\.?\s?L|"
                                  r"SOCIETA'?\s+PER\s+AZIONI)\.?", re.I)
_PAROLE_VUOTE = {"DI", "DEI", "DEL", "DELLA", "DELLE", "DEGLI", "E", "THE", "OF", "GRUPPO", "GROUP"}
# classe dell'azione nel nome della riga: non distingue l'EMITTENTE (lo fa la scheda col
# Codice Alfanumerico), quindi non conta nell'abbinamento per parole
_CLASSI_AZIONE = {"ORD", "ORDINARIA", "ORDINARIE", "RSP", "RISP", "RISPARMIO", "PRIV", "PRIVILEGIATA",
                  "PRIVILEGIATE", "PREF", "AZ", "AZIONI"}
_PAROLE_FONDO = {"ETF", "ETC", "ETN", "UCITS"}


def normalizza_nome(nome: Any) -> str:
    """'Prysmian S.p.A.' -> 'PRYSMIAN'; accenti tolti, maiuscolo, forma giuridica tolta,
    punteggiatura -> spazio."""
    import unicodedata
    if not isinstance(nome, str):
        return ""
    s = unicodedata.normalize("NFKD", nome)
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    # forma giuridica PRIMA di '&' -> 'E': 'S&P' diventerebbe 'S E P' e la regola 'S.E.'
    # (Societas Europaea) gli mangerebbe le prime due lettere
    senza_forma = _FORME_GIURIDICHE.sub(" ", s)
    if not re.sub(r"[^A-Z0-9]+", "", senza_forma):
        # il nome E' una sigla da forma giuridica («PLC S.p.A.», rilievo P3 RV-IT1): si toglie
        # solo l'ULTIMA forma; se resta vuoto (solo «S.p.A.») il nome e' vuoto davvero
        ultime = list(_FORME_GIURIDICHE.finditer(s))
        senza_forma = s[:ultime[-1].start()] + " " + s[ultime[-1].end():] if ultime else s
        if not re.sub(r"[^A-Z0-9]+", "", senza_forma) and not _SOLO_FORMA_GENERICA.fullmatch(s.strip()):
            senza_forma = s   # riga del listino «Plc»: il nome e' la sigla, resta
    s = senza_forma
    s = s.replace("&", " E ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return " ".join(s.split())


def _significative(n: str) -> List[str]:
    """Parole del nome senza parole vuote e classi dell'azione (ORD, RSP...). Le lettere di
    classe A/B NON si tolgono qui: v. _nomi_di_confronto (servono le righe sorelle)."""
    return [p for p in n.split() if p not in _PAROLE_VUOTE and p not in _CLASSI_AZIONE]


def _nomi_di_confronto(righe: List[Dict[str, Any]]) -> None:
    """Aggiunge a ogni riga `nome_confronto`: il nome normalizzato, senza la LETTERA finale A/B
    solo se nel listino c'e' la riga SORELLA con l'altra lettera (misura M1b 05/10 su un emittente
    vero; qui sintetico: «Mfx A» e «Mfx B», menu eMarket «MFX-MEDIAFINTA»). Senza sorella la
    lettera e' parte del nome (rilievo RV-IT1: un nome vero che finisce con «B»)."""
    nomi = {r["nome_normalizzato"] for r in righe}
    for r in righe:
        n = r["nome_normalizzato"]
        parti = n.split()
        r["nome_confronto"] = n
        if len(parti) > 1 and parti[-1] in ("A", "B"):
            sorella = " ".join(parti[:-1] + ["B" if parti[-1] == "A" else "A"])
            if sorella in nomi:
                r["nome_confronto"] = " ".join(parti[:-1])


def _parole(n: str) -> set:
    return set(_significative(n))


def _compatto(n: str) -> str:
    """Le parole significative attaccate, nell'ordine: 'ZETAFIN BANK' e 'ZETAFINBANK' (o 'ZU VE'
    e 'ZUVE', trattino del menu eMarket) si equivalgono."""
    return "".join(_significative(n))


_RANGO_COMBACIA = {"esatto": 0, "compatto": 1, "parole": 2, "abbreviato": 3}


def _combacia(cercato: str, riga: str) -> Optional[str]:
    """'esatto' | 'compatto' (stesse parole significative, spazi a parte) | 'parole' (le parole
    significative di uno stanno tutte nell'altro) | None."""
    if not cercato or not riga:
        return None
    if cercato == riga:
        return "esatto"
    ca = _compatto(cercato)
    if ca and ca == _compatto(riga):
        return "compatto"
    a, b = _parole(cercato), _parole(riga)
    if a and b and (a <= b or b <= a):
        return "parole"
    return None


def _ordine_corrispondenza(cercato: str, riga: str, combacia: str) -> Tuple[int, int]:
    """Prima le esatte, poi le compatte, poi per parole con meno parole di scarto: con piu' di
    MAX_SCHEDE righe le schede controllate sono le piu' vicine al nome, non le prime a caso."""
    return _RANGO_COMBACIA[combacia], len(_parole(cercato) ^ _parole(riga))


def _lettere_da_provare(n: str) -> List[Tuple[str, str]]:
    """[(iniziale, parola_di_arresto)] nell'ordine: la prima parola del nome (anche se generica:
    «Gruppo X» sta sotto la G), poi le iniziali delle altre parole significative. Per lettera
    la parola di arresto e' la piu' alta in ordine alfabetico: il listino si legge finche' le
    righe non la superano. Le parole che non iniziano con una lettera si saltano."""
    parole = n.split()
    candidate = parole[:1] + [p for p in parole[1:] if p not in _PAROLE_VUOTE and p not in _CLASSI_AZIONE]
    lettere: Dict[str, str] = {}
    for p in candidate:
        if p[:1].isalpha() and p > lettere.get(p[0], ""):
            lettere[p[0]] = p
    return list(lettere.items())[:MAX_LETTERE]


def _oltre(nome_norm_riga: str, parola: str) -> bool:
    """La riga sta DOPO `parola` nell'ordine del listino. Confronto sul prefisso lungo quanto
    la parola: una riga che INIZIA con la parola ('ZETAFINBANK' per 'ZETAFIN') non e' mai oltre."""
    primo = (nome_norm_riga.split() or [""])[0]
    return primo[:len(parola)] > parola


_RIGA_LISTINO = re.compile(
    r'<a class="u-hidden -xs" href="(/borsa/azioni/(?:[a-z-]+/)?scheda/([A-Z]{2}[A-Z0-9]{9}[0-9])-([A-Z0-9]+)\.html[^"]*)"'
    r'\s+title="Accedi alla scheda strumento&nbsp;([^"]+)"')


def parse_listino(html: str, iniziale: str, pagina: int = 1) -> Dict[str, Any]:
    """{"stato": ok|KO, "righe": [{nome, nome_normalizzato, isin, mic, scheda_url}],
    "pagina_successiva": bool, "motivo"}. Nessuna riga sulla pagina = KO layout (una lettera
    del listino non e' mai vuota: e' la pagina che e' cambiata).
    MISURA 05/10 (bug trovato dalla copertura FTSE MIB): ogni pagina linka SE STESSA (anche in
    inglese) e oltre l'ultima pagina il sito RISERVE l'ultima. `pagina_successiva` e' quindi il
    link alla pagina `pagina+1`, non un qualunque `page=`."""
    righe, viste = [], set()
    for href, isin, mic, nome in _RIGA_LISTINO.findall(html or ""):
        if href in viste:
            continue
        viste.add(href)
        nome = unescape(nome).replace("\xa0", " ").strip()
        righe.append({"nome": nome, "nome_normalizzato": normalizza_nome(nome), "isin": isin,
                      "mic": mic, "scheda_url": BASE + href})
    if not righe:
        return {"stato": "KO", "righe": [], "pagina_successiva": False,
                "motivo": "listino A-Z senza righe riconoscibili (link alla scheda con ISIN): layout cambiato"}
    succ = re.search(r'listino-a-z\.html\?initial=%s(?:&amp;|&)[^"]*page=%d(?!\d)' % (re.escape(iniziale), pagina + 1),
                     html) is not None
    return {"stato": "ok", "righe": righe, "pagina_successiva": succ, "motivo": None}


def _valori_scheda(html: str, etichetta: str) -> List[str]:
    return [_testo_cella(v) for v in re.findall(
        r"<strong>\s*%s\s*</strong>.*?</td>\s*<td[^>]*>(.*?)</td>" % re.escape(etichetta), html or "", re.S)]


def verifica_scheda(html: str, isin: str, simbolo: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Criterio IT2. (accettata, motivo, letti)."""
    codici = _valori_scheda(html, "Codice Alfanumerico")
    isin_pagina = _valori_scheda(html, "Codice Isin")
    letti = {"codici_alfanumerici": codici, "isin_pagina": isin_pagina}
    if not isin_valido(isin):
        return False, "ISIN %s con cifra di controllo non valida" % isin, letti
    if isin_pagina != [isin]:
        return False, "la scheda mostra Codice Isin %r, atteso [%r]" % (isin_pagina, isin), letti
    if len(codici) != 1:
        return False, "la scheda ha %d Codici Alfanumerici (atteso 1)" % len(codici), letti
    if codici[0].upper() != simbolo:
        return False, "Codice Alfanumerico %r diverso dal simbolo %r" % (codici[0], simbolo), letti
    return True, "", letti


def _cerca_emarket(nome_norm: str, altro_nome: Optional[str] = None) -> Tuple[Optional[int], str, Optional[str]]:
    """(id, esito 'ok'|'non_trovato'|'ambiguo'|'da_confermare'|'KO', motivo) dal menu emittenti
    di eMarket. `nome_norm` e' il nome della RIGA del listino (la scheda l'ha verificata),
    `altro_nome` quello cercato (fornitore/DB, a volte piu' lungo: «Assicurazioni X»).
    Regola (rilievi P1 di RV-IT1: un candidato unico «per parole» dava l'id di un ALTRO
    emittente, es. sintetici dei casi veri: riga «Qqal Verde Potenza» -> GRUPPO VERDE POTENZA, «Res» -> ZZCOR RES):
    una voce del menu e' ACCETTABILE solo se contiene tutte le parole significative della riga.
    Gradini, ciascuno solo se UNIVOCO (un id):
      1. esatta/compatta sul nome della riga;
      2. esatta/compatta sul nome cercato, fra le accettabili (il nome cercato non scavalca la
         riga: «Calta» non porta a CALTA se la riga e' «Calta Edit»);
      3. per parole fra le accettabili con la STESSA prima parola significativa (sigla + classe:
         riga «Mfx A» -> «MFX-MEDIAFINTA»), ristrette col nome cercato se piu' d'una.
    Altrimenti: voci simili ma non accettabili -> 'da_confermare' (una) / 'ambiguo' (piu'),
    id None e la lista nel motivo; nessuna -> 'non_trovato'. Mai il primo a caso."""
    try:
        http, corpo, _ = _scarica(URL_MENU_EMARKET)
    except Exception as e:
        return None, "KO", "menu eMarket non letto: %s" % type(e).__name__
    if http != 200:
        return None, "KO", "menu eMarket: HTTP %s" % http
    menu = re.search(r'<select\b[^>]*name="azienda"[^>]*>(.*?)</select>',
                     corpo.decode("utf-8", errors="replace"), re.S | re.I)
    if menu is None:
        return None, "KO", "menu emittenti di eMarket non trovato (layout cambiato)"
    opzioni = [(int(v), unescape(t).strip()) for v, t in
               re.findall(r'<option\s+value="(\d+)"[^>]*>([^<]*)</option>', menu.group(1))]
    if not opzioni:
        return None, "KO", "menu emittenti di eMarket senza voci (layout cambiato)"
    cercato = normalizza_nome(altro_nome)
    norm = [(i, t, normalizza_nome(t)) for i, t in opzioni]
    riga_p = _parole(nome_norm)
    # accettabile = contiene tutte le parole della riga, o la riga ne e' l'abbreviazione
    # («Calta Edit» -> CALTA EDITORE)
    accettabili = [o for o in norm if riga_p and (riga_p <= _parole(o[2]) or _abbreviato(o[2], nome_norm))]

    def _uno(lista: List[Tuple[int, str, str]], come: str) -> Optional[Tuple[Optional[int], str, Optional[str]]]:
        ids = sorted({o[0] for o in lista})
        if len(ids) == 1:
            return lista[0][0], "ok", "menu eMarket: %r (id %d), %s" % (lista[0][1], lista[0][0], come)
        if len(ids) > 1:
            return None, "ambiguo", "piu' emittenti nel menu eMarket per %r (%s): %s" % (
                nome_norm, come, ", ".join("%s (%d)" % (o[1], o[0]) for o in lista[:6]))
        return None
    for gradino in ("esatto", "compatto"):
        esito = _uno([o for o in norm if _combacia(nome_norm, o[2]) == gradino], gradino + " sul nome della riga")
        if esito:
            return esito
    if cercato:
        for gradino in ("esatto", "compatto"):
            esito = _uno([o for o in accettabili if _combacia(cercato, o[2]) == gradino],
                         gradino + " sul nome cercato")
            if esito:
                return esito
    prima = (_significative(nome_norm) or [""])[0]
    stessi = [o for o in accettabili if (_significative(o[2]) or [""])[0] == prima]
    if len({o[0] for o in stessi}) > 1 and cercato:
        stessi = [o for o in stessi if _combacia(cercato, o[2])] or stessi
    esito = _uno(stessi, "per parole")
    if esito:
        return esito
    simili = [o for o in norm if _combacia(nome_norm, o[2])]
    if not simili:
        return None, "non_trovato", "nessun emittente %r nel menu eMarket (forse su un altro SDIR)" % nome_norm
    elenco = ", ".join("%s (%d)" % (o[1], o[0]) for o in simili[:6])
    if len({o[0] for o in simili}) > 1:
        return None, "ambiguo", "piu' emittenti simili a %r nel menu eMarket, nessuno sicuro: %s" % (nome_norm, elenco)
    return None, "da_confermare", ("voce del menu eMarket solo SIMILE a %r (non contiene tutte le parole della "
                                   "riga o inizia con un'altra parola): %s; id non salvato" % (nome_norm, elenco))


def _abbreviato(cercato: str, riga: str) -> Optional[str]:
    """'abbreviato' se la riga del listino abbrevia il nome (rilievo P2 di RV-IT1, qui sintetico: «Calta
    Edit» per «Calta Editore»): stessa prima parola e ogni altra parola della riga uguale
    o PREFISSO (almeno 3 lettere) della parola del nome nella stessa posizione. Solo per trovare
    i candidati del listino: la scheda resta la prova."""
    a, b = _significative(cercato), _significative(riga)
    if len(b) < 2 or len(b) > len(a) or a[0] != b[0]:
        return None
    for pa, pb in zip(a[1:], b[1:]):
        if not (pa == pb or (len(pb) >= 3 and pa.startswith(pb))):
            return None
    return "abbreviato"


def _controlla_schede(corr: List[Dict[str, Any]], simbolo: str, out: Dict[str, Any]
                      ) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], Optional[Tuple[str, str]]]:
    """Legge le schede dei candidati: (accettate, errore). Errore = una scheda non letta (rete,
    429, 5xx): poteva essere quella giusta, quindi niente verdetto parziale."""
    import hashlib
    accettate = []
    for r in corr:
        time.sleep(PAUSA_S)
        voce_sch = {"scheda_url": r["scheda_url"], "nome": r["nome"], "isin": r["isin"]}
        try:
            http, corpo, finale = _scarica(r["scheda_url"])
        except Exception as e:
            voce_sch.update(esito="KO", motivo="scheda non letta: %s" % type(e).__name__)
            out["verifica"]["schede_controllate"].append(voce_sch)
            return accettate, ("rete", "scheda %s non letta: verdetto sospeso" % r["isin"])
        voce_sch["http"] = http
        if http == 429 or http >= 500:
            # guasto del sito, non «scheda inesistente»: come una scheda non letta
            voce_sch.update(esito="KO", motivo="HTTP %s" % http)
            out["verifica"]["schede_controllate"].append(voce_sch)
            return accettate, ("http", "scheda %s: HTTP %s, verdetto sospeso" % (r["isin"], http))
        if http != 200:
            voce_sch.update(esito="scartata", motivo="HTTP %s" % http)
            out["verifica"]["schede_controllate"].append(voce_sch)
            continue
        ok, motivo, letti = verifica_scheda(corpo.decode("utf-8", errors="replace"), r["isin"], simbolo)
        voce_sch.update(letti, esito="accettata" if ok else "scartata", motivo=motivo or None,
                        sha256=hashlib.sha256(corpo).hexdigest(), url_finale=finale)
        out["verifica"]["schede_controllate"].append(voce_sch)
        if ok:
            accettate.append((r, voce_sch))
    return accettate, None


def _esito_risolvi(ticker: str, nome: Any) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper(), "stato": "KO", "errore": None, "motivo": None,
            "isin": None, "emarket": None, "emarket_motivo": None, "fonte_url": None,
            "letto_il": None, "negozio": None, "salvato": False,
            "verifica": {"nome_cercato": nome, "nome_normalizzato": None, "iniziale": None,
                         "iniziali_lette": [], "pagine_listino": [], "righe_corrispondenti": [], "riga_usata": None,
                         "schede_controllate": [], "scheda_sha256": None, "limiti": []}}


def _leggi_lettera(iniziale: str, parola: str, out: Dict[str, Any]
                   ) -> Tuple[List[Dict[str, Any]], bool, Optional[Tuple[str, str]]]:
    """Righe del Listino A-Z di una lettera, lette pagina dopo pagina finche' le righe non
    superano `parola` (ordine alfabetico del listino). Ritorna (righe, troncato, errore):
    errore = (codice, motivo) se una pagina non e' leggibile (niente verdetto su meta' lettera),
    troncato = lette MAX_PAGINE_LISTINO pagine senza superare la parola.
    Fine del listino: la pagina non linka la successiva, OPPURE il sito riserve una pagina gia'
    letta (oltre l'ultima pagina risponde con l'ultima): nessuna riga nuova = fine."""
    righe: List[Dict[str, Any]] = []
    viste = set()
    for pagina in range(1, MAX_PAGINE_LISTINO + 1):
        url = URL_LISTINO.format(iniziale=iniziale) + ("&page=%d" % pagina if pagina > 1 else "")
        if out["verifica"]["pagine_listino"]:
            time.sleep(PAUSA_S)   # anche fra due lettere: e' lo stesso host
        try:
            http, corpo, _ = _scarica(url)
        except URLVietato:
            return righe, False, ("url_vietato", "listino %s pagina %d: %s" % (iniziale, pagina, url_consentito(url)[1]))
        except Exception as e:
            return righe, False, ("rete", "listino %s pagina %d: %s" % (iniziale, pagina, type(e).__name__))
        if http != 200:
            return righe, False, ("http", "listino %s pagina %d: HTTP %s" % (iniziale, pagina, http))
        out["verifica"]["pagine_listino"].append(url)
        p = parse_listino(corpo.decode("utf-8", errors="replace"), iniziale, pagina)
        if p["stato"] != "ok":
            return righe, False, ("layout_cambiato", "listino %s pagina %d: %s" % (iniziale, pagina, p["motivo"]))
        # chiave = link alla scheda (ISIN-MIC): lo stesso ISIN su due mercati sono due righe
        nuove = [r for r in p["righe"] if r["scheda_url"] not in viste]
        if not nuove:
            out["verifica"]["limiti"].append("listino %s pagina %d senza righe nuove: fine" % (iniziale, pagina))
            return righe, False, None
        viste.update(r["scheda_url"] for r in nuove)
        righe.extend(nuove)
        if _oltre(p["righe"][-1]["nome_normalizzato"], parola) or not p["pagina_successiva"]:
            return righe, False, None
    out["verifica"]["limiti"].append("lette %d pagine del listino %s senza superare il nome"
                                     % (MAX_PAGINE_LISTINO, iniziale))
    return righe, True, None


def risolvi_isin(ticker: str, *, nome: Optional[str] = None) -> Dict[str, Any]:
    """ISIN (e id eMarket) di un titolo italiano NON nel negozio confermato, verificato su
    Borsa Italiana. Stati: ok | non_trovato | ambiguo | KO.
    - Voce gia' nel negozio confermato (precedenza) o nell'automatico: si restituisce quella,
      senza rete, con `negozio` che lo dice.
    - Altrimenti: il NOME trova i candidati nel Listino A-Z (lettera iniziale), la SCHEDA
      decide (criterio IT2). Una sola scheda valida = ok, salvata in data/isin_it_auto.json.
    - Nome assente = non_trovato «serve il nome dell'emittente»: mai un ISIN a memoria.
    - Verdetto SOSPESO (KO) quando non si e' guardato tutto: pagina del listino o scheda non
      lette (rete/http/5xx), listino oltre MAX_PAGINE_LISTINO (`listino_troncato`), piu' di
      MAX_SCHEDE righe candidate senza una scheda valida (`schede_troncate`).
    - Nome da ETF/ETC = non_trovato `non_azione` senza rete (il listino e' solo azioni)."""
    out = _esito_risolvi(ticker, nome)
    t = out["ticker"]
    m = re.fullmatch(r"([A-Z0-9]+)\.MI", t)
    if not m:
        out.update(errore="parametro", motivo="ticker %r: servono simboli di Borsa Italiana nella forma SIMBOLO.MI" % t)
        return out
    simbolo = m.group(1)
    voce, err, _mot = voce_ticker_o_auto(t)
    if voce is not None:
        out.update(stato="ok", isin=voce["isin"], emarket=voce.get("emarket"), negozio=voce["negozio"],
                   emarket_motivo=voce.get("emarket_motivo"),
                   motivo="voce gia' nel negozio %s: nessuna richiesta" % voce["negozio"])
        return out
    if err == "negozio_illeggibile":
        out.update(errore=err, motivo=_mot)
        return out
    n = normalizza_nome(nome)
    out["verifica"]["nome_normalizzato"] = n
    if not n:
        out.update(stato="non_trovato", errore="nome_assente",
                   motivo="serve il nome dell'emittente: senza nome non c'e' una ricerca permessa "
                          "su Borsa Italiana (il simbolo non e' cercabile)")
        return out
    out["verifica"]["iniziale"] = n[0]
    if _PAROLE_FONDO & set(n.split()):
        out.update(stato="non_trovato", errore="non_azione",
                   motivo="nome %r da ETF/ETC/ETN: il Listino A-Z di Borsa Italiana copre solo le azioni "
                          "(gli ETF stanno sotto /borsa/etf/, non cercati qui): ISIN non risolto" % n)
        return out
    lettere = _lettere_da_provare(n)
    if not lettere:
        out.update(stato="non_trovato", errore="iniziale_non_alfabetica",
                   motivo="nome %r: il Listino A-Z e' indicizzato per lettera e nessuna parola del "
                          "nome inizia con una lettera" % n)
        return out
    # 1. listino A-Z: la lettera della prima parola; se nessuna riga combacia, OPPURE combaciano
    #    ma nessuna scheda e' valida (rilievo P2 di RV-IT1), le iniziali delle altre parole
    #    significative (dichiarate in iniziali_lette). Il verdetto arriva dopo TUTTE le lettere.
    tutte_corr: List[Dict[str, Any]] = []
    troncate: List[str] = []
    schede_tagliate = 0
    accettate: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for iniziale, parola in lettere:
        righe, troncato, errore = _leggi_lettera(iniziale, parola, out)
        if errore is not None:
            out.update(errore=errore[0], motivo=errore[1])
            return out
        out["verifica"]["iniziali_lette"].append(iniziale)
        if troncato:
            troncate.append(iniziale)
        # 2. corrispondenze per nome: esatte, compatte, per parole, abbreviate (le piu' vicine prima)
        _nomi_di_confronto(righe)
        corr = []
        for r in righe:
            c = (_combacia(n, r["nome_normalizzato"]) or _combacia(n, r["nome_confronto"])
                 or _abbreviato(n, r["nome_confronto"]))
            if c:
                corr.append(dict(r, combacia=c))
        corr.sort(key=lambda r: _ordine_corrispondenza(n, r["nome_confronto"], r["combacia"]))
        tutte_corr.extend(corr)
        if not corr:
            continue
        if len(corr) > MAX_SCHEDE:
            schede_tagliate += len(corr) - MAX_SCHEDE
            out["verifica"]["limiti"].append("lettera %s: %d righe combaciano col nome, controllate le %d "
                                             "piu' vicine" % (iniziale, len(corr), MAX_SCHEDE))
        # 3. la scheda decide
        accettate, errore = _controlla_schede(corr[:MAX_SCHEDE], simbolo, out)
        if errore is not None:
            out.update(errore=errore[0], motivo=errore[1])
            return out
        if accettate:
            break
    out["verifica"]["righe_corrispondenti"] = [{k: r[k] for k in ("nome", "isin", "mic", "scheda_url", "combacia")}
                                               for r in tutte_corr]
    lette = "/".join(out["verifica"]["iniziali_lette"])
    if not accettate and troncate:
        # non tutte le righe utili sono state lette: «non c'e'» non e' una misura
        out.update(errore="listino_troncato",
                   motivo="nessuna scheda valida per %r fra le righe lette, ma il Listino A-Z (lettera %s) e' "
                          "stato letto solo per %d pagine: verdetto sospeso" % (n, "/".join(troncate),
                                                                                MAX_PAGINE_LISTINO))
        return out
    if not accettate and schede_tagliate:
        out.update(errore="schede_troncate",
                   motivo="%d righe combaciano col nome %r, %d schede NON lette (tetto %d per lettera) e nessuna "
                          "delle lette ha il Codice Alfanumerico %r: verdetto sospeso"
                          % (len(tutte_corr), n, schede_tagliate, MAX_SCHEDE, simbolo))
        return out
    if not tutte_corr:
        out.update(stato="non_trovato", errore="nome_non_nel_listino",
                   motivo="nessuna riga del Listino A-Z (lettere %s) combacia col nome %r (abbreviazioni del "
                          "listino riconosciute solo come prefisso di almeno 3 lettere della parola)" % (lette, n))
        return out
    if not accettate:
        out.update(stato="non_trovato", errore="nessuna_scheda_valida",
                   motivo="lettere %s: %d schede controllate, nessuna con Codice Alfanumerico %r e ISIN coerente"
                          % (lette, len(out["verifica"]["schede_controllate"]), simbolo))
        return out
    if len(accettate) > 1:
        out.update(stato="ambiguo", errore="piu_schede_valide",
                   motivo="%d schede valide per %s: %s" % (len(accettate), simbolo,
                                                           ", ".join(a[0]["isin"] for a in accettate)))
        return out
    r, sch = accettate[0]
    adesso = adesso_utc().isoformat(timespec="seconds")
    out["verifica"]["riga_usata"] = {k: r[k] for k in ("nome", "isin", "mic", "scheda_url", "combacia")}
    out["verifica"]["scheda_sha256"] = sch["sha256"]
    # 4. id eMarket per nome (della RIGA del listino: e' il nome ufficiale), solo se univoco.
    #    Riga del Global Equity Market (BGEM) = azione ESTERA: non sta su eMarket, non si cerca
    #    (rilievo RV-IT1, casi veri; qui sintetici: «Zeta» -> ZETA MEDIA GROUP, «Acme Energy» -> ENERGY).
    if r["mic"] == "BGEM":
        id_em, esito_em, motivo_em = (None, "non_cercato", "riga del Global Equity Market (azione estera, "
                                      "mic BGEM): emittente estero, eMarket non cercato")
    else:
        time.sleep(PAUSA_S)
        id_em, esito_em, motivo_em = _cerca_emarket(r["nome_confronto"], nome)
    out.update(stato="ok", isin=r["isin"], emarket=id_em, fonte_url=sch["url_finale"], letto_il=adesso,
               negozio="automatico", emarket_motivo=None if esito_em == "ok" else
               "id eMarket non risolto (%s): %s" % (esito_em, motivo_em))
    out["verifica"]["emarket"] = {"esito": esito_em, "motivo": motivo_em}
    salvato, motivo_salv = _scrivi_voce_auto(t, {
        "isin": r["isin"], "emarket": id_em, "emarket_motivo": out["emarket_motivo"],
        "origine": ORIGINE_AUTO % adesso, "verificato_il": adesso, "nome_cercato": nome,
        "riga_listino": r["nome"], "mic": r["mic"], "scheda_url": sch["url_finale"],
        "scheda_sha256": sch["sha256"]})
    out["salvato"] = salvato
    if not salvato:
        out["motivo"] = motivo_salv
    return out


if __name__ == "__main__":  # python -m bellomberg.market_data.borsa_italiana TICKER [ISIN]
    import sys
    a = sys.argv[1:]
    print(json.dumps(get_eventi_societari(a[0], isin=a[1] if len(a) > 1 else None),
                     ensure_ascii=False, indent=2))
