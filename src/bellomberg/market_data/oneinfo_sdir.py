# -*- coding: utf-8 -*-
"""oneinfo_sdir.py — DEPOSITI delle relazioni finanziarie e INTERNAL DEALING dei titoli italiani
che usano lo SDIR 1INFO (Computershare) invece di eMarket (handoff-3, 05/10/2026, Opus 5.5).

PERCHE' ESISTE. `emarket_sdir` copre solo gli emittenti che diffondono via eMarket SDIR: per
gli altri (misura S1 05/10: decine di emittenti del FTSE MIB e del Mid Cap) internal dealing e
date di deposito risultavano `non_coperto`. 1INFO-SDIR espone un'API JSON pubblica (la stessa
che usa il suo sito), senza login: questo modulo la legge con la STESSA interfaccia, gli stessi
stati e le stesse chiavi di `emarket_sdir` (piu' `oneinfo_ndg`).

TERMINI D'USO (decisione PM 05/10): il sito consente download e stampa SOLO per uso personale
non commerciale; vieta riproduzione, ripubblicazione e link da domini di terzi. Quindi: nessun
contenuto vero nel repo (le fixture sono inventate), nessun link nella documentazione pubblica,
nessun PDF 1INFO allegato ai memo condivisi (si riportano data e titolo). La frase sta anche nei
`limiti` di ogni ritorno.

MISURE DELLA SONDA (S1, 05/10/2026, 49 richieste):
  - POST DataTables (form urlencoded) su `/API/Documenti` (le relazioni STOCCATE, campo
    `dataStoccaggio`) e `/API/Comunicati` (i comunicati DIFFUSI, `dataDiffusione`), filtri
    `SearchFilter[emittente|categoria|oggetto]` e intervallo di date in epoch.
  - DATE: gli epoch sono ORA DI ROMA travestita da UTC (il sito li formatta con getUTCHours):
    si leggono come UTC e NON si convertono. Convertire sposterebbe tutto di +1/+2 ore.
  - RIGHE-FANTASMA: con meno risultati di `length` l'API riempie con righe tutte null (con 0
    risultati ne manda `length`): si scartano le righe con `ndg` null e si conta
    `recordsFiltered`.
  - EMITTENTI STORICI: la lista emittenti contiene chi non usa piu' 1INFO da anni (ultimo
    comunicato nel 2015): essere in lista NON vuol dire essere coperti. Un emittente senza
    comunicati negli ultimi GIORNI_ATTIVITA giorni e' `non_coperto` misurato, mai
    `vuoto_misurato` / `non_trovato`.
  - INTERNAL DEALING: la categoria MANTRA da sola non basta (un emittente ne aveva 4 in MANTRA
    e 16 col testo «internal dealing» nel titolo): si UNISCONO le due ricerche.
  - DEPOSITO: la data e' lo STOCCAGGIO del documento stesso (la relazione), non il comunicato
    che lo annuncia: definizione DIVERSA da eMarket (diffusione del comunicato), scritta nei
    `limiti`. L'«Avviso di deposito» pubblicato sul quotidiano giorni dopo NON e' il deposito:
    sta fra i comunicati, non fra i documenti, e qui non si legge.
  - NESSUN ISIN ne' codice condiviso con Borsa nell'API (misura sulle risposte salvate da S1: campi
    ndg, descrizione, mittente, oggetto, categoria, date, pdf, protocolCode, sdir...): l'emittente
    si trova per NOME (con nomi storici diversi da Borsa, es. un ex nome sociale) e l'ISIN si
    verifica DOPO, nei PDF dell'internal dealing (isin_coerente). Nome non trovato = misura
    fallita dichiarata, mai «non coperto».
  - I PDF dell'internal dealing li compila l'emittente: formati diversi (date ISO e M/D/YYYY,
    volumi col punto delle migliaia): il parser legge per etichette del modello MAR e lascia
    None (parse_ok False, motivo scritto) tutto cio' che non e' univoco.

CONTRATTO: come `emarket_sdir` (vedi il suo docstring), con `emarket_id` sempre None e
`oneinfo_ndg` = id dell'emittente su 1INFO; `fonte` = FONTE; nel deposito `categoria` e' il
codice 1INFO (stringa, es. "1.2"), `protocollo` = protocolCode, `url` = PDF del documento,
`prova` = 'esef' | 'titolo', `tipo_data` = 'stoccaggio_documento', `esef`, `consolidato`.
`url_liste` / `sha256_liste` = ricevuta di ogni POST (chiave = endpoint + filtri, valore =
sha256 dei byte della risposta JSON).
"""
import base64
import calendar
import gzip
import hashlib
import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode

from bellomberg.market_data import borsa_italiana as _bi
from bellomberg.market_data import emarket_sdir as _em

FONTE = "1INFO-SDIR (Storage Computershare)"
HOST = "www.1info.it"
BASE = "https://" + HOST + "/PORTALE1INFO"
URL_EMITTENTI = BASE + "/API/companies/comunicatistoccati"
URL_COMUNICATI = BASE + "/API/Comunicati"
URL_DOCUMENTI = BASE + "/API/Documenti"
URL_PDF = ("https://" + HOST + "/PdfViewer/PdfShow.aspx?service=&type={tipo}&year={anno}"
           "&file={file}.pdf&download=1")
PAUSA_S = 1.5                 # pausa minima fra due richieste a 1INFO (uso personale, basso volume)
TIMEOUT_S = 25
TTL_LISTA_S = 6 * 3600        # internal dealing e depositi
TTL_ANAGRAFE_S = 24 * 3600    # lista emittenti e misura di attivita'
GIORNI_ATTIVITA = 180         # nessun comunicato da piu' di N giorni = emittente storico su 1INFO
GIORNI_MAX = 3650
LUNGHEZZA_ATTIVITA = 50   # righe lette per misurare l'attivita'
SDIR_PROPRIO = "SDIR 1INFO"   # valore del campo sdir dei comunicati diffusi da 1INFO (misura S1)
FINESTRA_DEPOSITO_GIORNI = 300  # documenti cercati dal giorno dopo la fine del periodo per N giorni
LUNGHEZZA_DOC = 150           # righe per pagina (documenti)
LUNGHEZZA_ID = 200            # righe per pagina (comunicati internal dealing)
MAX_PAGINE = 3
MAX_PDF = 30
MAX_SCARTATI = 15
PARSER_VERSIONE = 1
CATEGORIA_ID = "MANTRA"
TESTO_ID = "internal dealing"
# categorie dei DOCUMENTI per tipo (misura S1: annuale 1.1, semestrale 1.2, resoconti
# trimestrali in REGEM; un documento puo' avere piu' codici, es. "1.1,3.1,REGEM")
CATEGORIE_DEPOSITO = {"annuale": ("1.1",), "semestrale": ("1.2",), "trimestrale": ("REGEM", "2.2", "3.1")}

LIMITE_TERMINI = ("termini d'uso 1INFO: consentiti solo download e stampa per uso personale non "
                  "commerciale; vietate riproduzione, ripubblicazione e link da siti di terzi (nessun "
                  "PDF 1INFO nei memo condivisi: si riportano data e titolo)")
LIMITE_ORARI = ("date e ore come pubblicate da 1INFO, gia' in ora di Roma (nessuna conversione di fuso)")

_ASSENTE = object()


# ============================================================
# RETE: solo gli endpoint misurati, POST solo verso le due API
# ============================================================
_AMMESSI = {
    "GET": (re.compile(r"^/PORTALE1INFO/API/companies/comunicatistoccati$"),
            re.compile(r"^/PdfViewer/PdfShow\.aspx\?service=&type=(?:comunicati|documenti)&year=\d{4}"
                       r"&file=[A-Za-z0-9_]+\.pdf&download=1$")),
    "POST": (re.compile(r"^/PORTALE1INFO/API/(?:Comunicati|Documenti)$"),),
}
_ULTIMA = {"t": None}
# la pausa vale fra i thread di UN processo (lock); due processi distinti non si vedono: limite
# dichiarato (uso personale, un solo backend)
import threading as _threading  # noqa: E402
_LOCK = _threading.Lock()


def richiesta_consentita(metodo: str, url: str) -> Tuple[bool, str]:
    """(consentita, motivo). Solo https verso www.1info.it e solo i percorsi misurati da S1:
    tutto il resto si RIFIUTA prima della rete."""
    m = re.match(r"^https://([^/?#]+)(/[^#]*)$", url or "")
    if not m:
        return False, "URL non https o malformato"
    if m.group(1).lower() != HOST:
        return False, "host %s fuori da 1INFO" % m.group(1).lower()
    regole = _AMMESSI.get(metodo)
    if regole is None:
        return False, "metodo %s non ammesso" % metodo
    if not any(r.match(m.group(2)) for r in regole):
        return False, "%s %s fuori dagli endpoint ammessi" % (metodo, m.group(2).split("?")[0])
    return True, ""


def _attendi() -> None:
    """Pausa di almeno PAUSA_S fra due richieste a 1INFO (orologio monotonico)."""
    if _ULTIMA["t"] is not None:
        resto = PAUSA_S - (time.monotonic() - _ULTIMA["t"])
        if resto > 0:
            time.sleep(resto)


def _richiesta(metodo: str, url: str, dati: Optional[Dict[str, str]] = None) -> Tuple[int, bytes]:
    """(HTTP, corpo). Rifiuta PRIMA della rete cio' che non e' ammesso (`URLVietato`). Nessun
    redirect seguito: un 3xx torna come codice HTTP (e chi chiama lo dichiara KO)."""
    ok, motivo = richiesta_consentita(metodo, url)
    if not ok:
        raise _bi.URLVietato(motivo)
    import requests
    intest = {"User-Agent": _bi.USER_AGENT, "Accept-Language": "it-IT,it;q=0.9"}
    if metodo == "POST":
        intest.update({"X-Requested-With": "XMLHttpRequest", "Referer": BASE,
                       "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    with _LOCK:
        _attendi()
        try:
            r = requests.request(metodo, url, data=dati, headers=intest, timeout=TIMEOUT_S, allow_redirects=False)
        finally:
            _ULTIMA["t"] = time.monotonic()
    return r.status_code, r.content


# ============================================================
# DATE (ora di Roma travestita da UTC: NON si converte)
# ============================================================
def data_ora(epoch: Any) -> Tuple[Optional[str], Optional[str]]:
    """Epoch 1INFO -> ('AAAA-MM-GG', 'HH:MM') letti come UTC SENZA conversione: e' gia' l'ora di
    Roma (misura S1). None se non e' un numero."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None, None
    try:
        d = datetime.fromtimestamp(epoch, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None, None
    return d.date().isoformat(), d.strftime("%H:%M")


def epoch_di(giorno: date) -> int:
    """Mezzanotte del giorno (ora di Roma) nell'epoch «finto UTC» dei filtri 1INFO."""
    return calendar.timegm(giorno.timetuple())


# ============================================================
# RICERCA DataTables
# ============================================================
_COLONNE = ("", "mittente", "{data}", "oggetto", "", "")


def corpo_ricerca(filtri: Dict[str, Any], colonna_data: str, start: int, length: int) -> Dict[str, str]:
    """Il corpo POST come lo manda il sito (ordinato per data decrescente)."""
    d = {"draw": "1", "start": str(start), "length": str(length),
         "order[0][column]": "2", "order[0][dir]": "desc", "search[value]": "", "search[regex]": "false"}
    for i, c in enumerate(_COLONNE):
        d["columns[%d][data]" % i] = c.format(data=colonna_data)
        d["columns[%d][name]" % i] = ""
        d["columns[%d][searchable]" % i] = "true"
        d["columns[%d][orderable]" % i] = "true" if i in (1, 2) else "false"
        d["columns[%d][search][value]" % i] = ""
        d["columns[%d][search][regex]" % i] = "false"
    for k, v in filtri.items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                d["SearchFilter[%s][%s]" % (k, kk)] = str(vv)
        else:
            d["SearchFilter[%s]" % k] = str(v)
    return d


def _chiave_ricevuta(url: str, filtri: Dict[str, Any], start: int) -> str:
    piatti = []
    for k in sorted(filtri):
        v = filtri[k]
        if isinstance(v, dict):
            piatti += [("%s.%s" % (k, kk), v[kk]) for kk in sorted(v)]
        else:
            piatti.append((k, v))
    return "POST %s?%s" % (url, urlencode(piatti + [("start", start)]))


def parse_risposta(corpo: bytes, ndg: Optional[int]) -> Dict[str, Any]:
    """Corpo JSON -> {"stato": ok|KO, "righe" (solo vere), "filtrati", "righe_null", "errore",
    "motivo"}. Righe con ndg null = riempitivo dell'API (scartate e contate). Una riga di un
    ALTRO emittente = filtro ignorato dall'API: KO, mai servita."""
    try:
        j = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"stato": "KO", "righe": [], "filtrati": None, "righe_null": 0, "errore": "layout_cambiato",
                "motivo": "risposta non JSON: API cambiata o pagina d'errore"}
    filtrati = j.get("recordsFiltered") if isinstance(j, dict) else None
    dati = j.get("data") if isinstance(j, dict) else None
    if isinstance(filtrati, bool) or not isinstance(filtrati, int) or not isinstance(dati, list):
        return {"stato": "KO", "righe": [], "filtrati": None, "righe_null": 0, "errore": "layout_cambiato",
                "motivo": "risposta senza recordsFiltered intero o senza lista data: API cambiata"}
    vere = [x for x in dati if isinstance(x, dict) and x.get("ndg") is not None]
    nulle = len(dati) - len(vere)
    if ndg is not None:
        altri = sorted({x.get("ndg") for x in vere if x.get("ndg") != ndg}, key=str)
        if altri:
            return {"stato": "KO", "righe": [], "filtrati": filtrati, "righe_null": nulle,
                    "errore": "filtro_ignorato",
                    "motivo": "chiesto l'emittente %s, arrivate righe di %s: filtro non applicato" % (
                        ndg, ", ".join(str(a) for a in altri[:5]))}
    return {"stato": "ok", "righe": vere, "filtrati": filtrati, "righe_null": nulle, "errore": None, "motivo": None}


def comprimi(corpo: bytes) -> Dict[str, str]:
    """Forma di `risposte_salvate[chiave]` (INTERFACCIA_UE «AGGIUNTA 5»): i BYTE interi della
    risposta, gzip (mtime 0, deterministico) + base64; lo sha256 resta sui byte NON compressi."""
    return {"codifica": "gzip+base64", "corpo": base64.b64encode(gzip.compress(corpo, mtime=0)).decode("ascii")}


def decomprimi(salvata: Any) -> bytes:
    """I byte da una voce di `risposte_salvate`; ValueError se la forma non e' riconosciuta."""
    if isinstance(salvata, dict) and salvata.get("codifica") == "gzip+base64" and isinstance(salvata.get("corpo"), str):
        return gzip.decompress(base64.b64decode(salvata["corpo"], validate=True))
    raise ValueError("voce di risposte_salvate in forma sconosciuta")


def cerca(url: str, filtri: Dict[str, Any], colonna_data: str, *, ndg: Optional[int],
          length: int, max_pagine: int = MAX_PAGINE) -> Dict[str, Any]:
    """POST paginata fino a `recordsFiltered` righe vere (al massimo `max_pagine`).
    {"stato": ok|KO, "righe", "filtrati", "troncato", "righe_null", "url_liste", "sha256_liste",
    "richieste", "errore", "motivo"}."""
    out: Dict[str, Any] = {"stato": "KO", "righe": [], "filtrati": None, "troncato": False, "righe_null": 0,
                           "url_liste": [], "sha256_liste": {}, "risposte_salvate": {}, "richieste": 0,
                           "errore": None, "motivo": None}
    for pagina in range(max_pagine):
        start = pagina * length
        chiave = _chiave_ricevuta(url, filtri, start)
        try:
            http, corpo = _richiesta("POST", url, corpo_ricerca(filtri, colonna_data, start, length))
        except _bi.URLVietato:
            out.update(errore="url_vietato",
                       motivo="richiesta rifiutata prima della rete: %s" % richiesta_consentita("POST", url)[1])
            return out
        except Exception as e:
            out.update(errore="rete", motivo="richiesta fallita (pagina %d): %s" % (pagina + 1, type(e).__name__))
            return out
        out["richieste"] += 1
        if http != 200:
            out.update(errore="http", motivo="HTTP %s da 1INFO (pagina %d)" % (http, pagina + 1))
            return out
        out["url_liste"].append(chiave)
        out["sha256_liste"][chiave] = hashlib.sha256(corpo).hexdigest()
        out["risposte_salvate"][chiave] = comprimi(corpo)
        p = parse_risposta(corpo, ndg)
        if p["stato"] != "ok":
            out.update(errore=p["errore"], motivo="pagina %d: %s" % (pagina + 1, p["motivo"]))
            return out
        out["filtrati"] = p["filtrati"]
        out["righe_null"] += p["righe_null"]
        if p["filtrati"] > len(out["righe"]) and not p["righe"]:
            out.update(errore="layout_cambiato",
                       motivo="recordsFiltered %d ma pagina %d senza righe vere: risposta incoerente"
                              % (p["filtrati"], pagina + 1))
            return out
        out["righe"].extend(p["righe"])
        if len(out["righe"]) >= p["filtrati"]:
            break
    else:
        out["troncato"] = len(out["righe"]) < (out["filtrati"] or 0)
    out["stato"] = "ok"
    return out


# ============================================================
# RIGHE NORMALIZZATE
# ============================================================
def url_pdf(file: Any, tipo: str, anno: Any) -> Optional[str]:
    if not isinstance(file, str) or not re.fullmatch(r"[A-Za-z0-9_]+", file) or anno is None:
        return None
    return URL_PDF.format(tipo=tipo, anno=anno, file=file)


def riga_documento(x: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Riga /API/Documenti -> {data, ora, titolo, url_pdf, protocollo, categoria, esef,
    consolidato}; None se manca un campo essenziale. `esef` = versione ESEF (protocollo XBRL
    presente), `consolidato` = bilancio_consolidato (1, 0 o None)."""
    d, o = data_ora(x.get("dataStoccaggio"))
    prot = x.get("protocolCode") or x.get("pdf")
    tit = x.get("oggetto")
    if d is None or not isinstance(prot, str) or not prot or not isinstance(tit, str) or not tit.strip():
        return None
    cons = x.get("bilancio_consolidato")
    return {"data": d, "ora": o, "titolo": " ".join(tit.split()), "protocollo": prot,
            "url_pdf": url_pdf(x.get("pdf") or prot, "documenti", d[:4]),
            "categoria": x.get("categoria") if isinstance(x.get("categoria"), str) else None,
            "esef": bool(x.get("protocolCodeXbrl")),
            "consolidato": cons if isinstance(cons, int) and not isinstance(cons, bool) else None}


def riga_comunicato(x: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Riga /API/Comunicati -> {data, ora, protocollo, titolo, url_pdf, categoria, sdir_diffusione}.
    Data = DIFFUSIONE. L'anno del PDF e' nel nome (ndg_protocollo_anno_oneinfo)."""
    d, o = data_ora(x.get("dataDiffusione"))
    pdf = x.get("pdf")
    tit = x.get("oggetto")
    if d is None or not isinstance(pdf, str) or not pdf or not isinstance(tit, str):
        return None
    m = re.fullmatch(r"\d+_\d+_(\d{4})_\w+", pdf)
    return {"data": d, "ora": o, "protocollo": pdf, "titolo": " ".join(tit.split()),
            "url_pdf": url_pdf(pdf, "comunicati", m.group(1) if m else d[:4]),
            "categoria": x.get("categoria") if isinstance(x.get("categoria"), str) else None,
            "sdir_diffusione": x.get("sdir")}


# ============================================================
# ANAGRAFE: lista emittenti e attivita' (per l'instradatore)
# ============================================================
def _leggi_anagrafe() -> Dict[str, Any]:
    out = {"stato": "KO", "emittenti": [], "errore": None, "motivo": None, "letto_il": None}
    try:
        http, corpo = _richiesta("GET", URL_EMITTENTI)
    except Exception as e:
        out.update(errore="rete", motivo="lista emittenti 1INFO non letta: %s" % type(e).__name__)
        return out
    if http != 200:
        out.update(errore="http", motivo="lista emittenti 1INFO: HTTP %s" % http)
        return out
    try:
        lista = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        lista = None
    if not isinstance(lista, list) or not lista:
        out.update(errore="layout_cambiato", motivo="lista emittenti 1INFO non e' una lista JSON non vuota")
        return out
    em = [{"ndg": x["ndg"], "nome": " ".join(x["descrizione"].split())} for x in lista
          if isinstance(x, dict) and isinstance(x.get("ndg"), int) and isinstance(x.get("descrizione"), str)]
    if not em:
        out.update(errore="layout_cambiato", motivo="lista emittenti 1INFO senza voci {ndg, descrizione}")
        return out
    out.update(stato="ok", emittenti=em, letto_il=_bi.adesso_utc().isoformat(timespec="seconds"))
    return out


def anagrafe() -> Dict[str, Any]:
    return _bi.con_cache("oneinfo_emittenti", TTL_ANAGRAFE_S, _leggi_anagrafe)


def _abbina_un_nome(n: str, norm: List[Tuple[int, str, str]]) -> List[Tuple[int, str, str]]:
    """Righe della lista 1INFO che combaciano col nome normalizzato `n`, al primo gradino non vuoto
    (esatto -> compatto -> per parole)."""
    return _abbina_con_gradino(n, norm)[0]


def _abbina_con_gradino(n: str, norm: List[Tuple[int, str, str]]) -> Tuple[List[Tuple[int, str, str]], Optional[str]]:
    for gradino in ("esatto", "compatto", "parole", "debole"):
        simili = [o for o in norm if _combacia_1info(n, o[2]) == gradino]
        if simili:
            return simili, gradino
    return [], None


# Parole che un nome del listino puo' avere IN PIU' rispetto al nome 1INFO senza indicare un altro
# emittente («Banca Zetafin» = «ZETAFIN»; «Zetafin R» = azioni di risparmio di «ZETAFIN»).
_EXTRA_GENERICHE = {"BANCA", "BANCO", "HOLDING", "HOLD", "R"}


def _combacia_1info(cercato: str, riga: str) -> Optional[str]:
    """Come borsa_italiana._combacia, ma il gradino «parole» e' piu' stretto (misura 05/10 sulle 1069
    righe salvate del Listino contro la lista 1INFO: quattro abbinamenti SBAGLIATI fra emittenti veri,
    del tipo «Mare Sintetica Group» -> «SINTETICA», «Zeta Tv France» -> «ZETA TV», «Calta Edit» ->
    «CALTA», «Reti Sintetiche» -> «ZZQ RETI SINTETICHE»):
      - il nome cercato contenuto nel nome 1INFO vale solo se comincia con la stessa parola
        significativa («Zetafin» -> «ZETAFIN CLEANPOWER» si'; «Reti» -> «ZZQ RETI» no);
      - il nome 1INFO contenuto nel nome cercato vale solo se le parole in piu' sono generiche
        (_EXTRA_GENERICHE)."""
    g = _bi._combacia(cercato, riga)
    if g != "parole":
        return g
    a, b = _bi._parole(cercato), _bi._parole(riga)
    prima = lambda s: next((p for p in s.split() if p in _bi._parole(s)), "")  # noqa: E731
    if a <= b:
        # stessa prima parola = abbinamento pieno; altrimenti DEBOLE: conta per l'ambiguita' ma
        # non basta mai da solo («Fittizie» -> ASSICURAZIONI FITTIZIE e BANCA FITTIZIE: ambiguo)
        return "parole" if prima(cercato) == prima(riga) else "debole"
    if b < a and (a - b) <= _EXTRA_GENERICHE:
        return "parole"
    return None


# Abbreviazioni del Listino A-Z di Borsa (misura 05/10 su 1069 righe salvate da IT1b/M1a: Bco, Bca,
# Ind, Fin, Int/Intl, Hold; le altre sono forme italiane note). Una sigla con piu' letture le prova
# TUTTE: l'abbinamento vale solo se l'unione porta a UN emittente. Le parole vuote (di, e, della,
# del, delle...) le ignora gia' il confronto per parole di borsa_italiana.
_ESPANSIONI = {
    "BCO": ("BANCO",), "BCA": ("BANCA",), "ASS": ("ASSICURAZIONI",), "ASSIC": ("ASSICURAZIONI",),
    "IND": ("INDUSTRIE", "INDUSTRIA", "INDUSTRIALE"), "FIN": ("FINANZIARIA", "FINANCIAL"),
    "GR": ("GRUPPO",), "SOC": ("SOCIETA",), "INT": ("INTERNATIONAL",), "INTL": ("INTERNATIONAL",),
    "HOLD": ("HOLDING",), "HLDG": ("HOLDING",),
    # (MF 05/10, qui sintetici) «Qqventus Fc», «Mondasint Edit». Gli alias (es. un nome commerciale corto diverso
    # dalla ragione sociale) NON sono abbreviazioni: restano non abbinati (limite dichiarato)
    "FC": ("FOOTBALL CLUB",), "EDIT": ("EDITORE", "EDITORIALE"),
}
MAX_VARIANTI = 9


def _varianti(n: str) -> List[str]:
    """Il nome con le abbreviazioni espanse (tutte le letture, al massimo MAX_VARIANTI); [] se
    non c'e' nessuna abbreviazione."""
    parole = n.split()
    if not any(p in _ESPANSIONI for p in parole):
        return []
    out = [[]]
    for p in parole:
        out = [v + [e] for v in out for e in _ESPANSIONI.get(p, (p,))][:MAX_VARIANTI]
    return [" ".join(v) for v in out]


def cerca_emittente(nomi: List[Optional[str]], etichette: Optional[List[str]] = None) -> Dict[str, Any]:
    """{"esito": ok|non_trovato|ambiguo|KO, "ndg", "nome_1info", "nome_abbinato", "per_nome",
    "motivo", "cache"} dal NOME. I nomi si provano UNO ALLA VOLTA nell'ordine dato (es. nome
    passato dal chiamante, poi nome del Listino A-Z di Borsa, poi nome cercato): un nome vale se
    abbina UN SOLO ndg. Esito ok solo se tutti i nomi che abbinano univocamente portano allo
    STESSO ndg (`nome_abbinato` = il primo); due nomi su ndg DIVERSI = ambiguo; nessun nome
    univoco ma qualcuno con piu' ndg = ambiguo; nessuno = non_trovato. Mai il primo a caso.
    Essere in lista NON prova l'attivita' (vedi attivita)."""
    etichette = list(etichette or [])
    coppie = []
    for k, x in enumerate(nomi):
        n = _bi.normalizza_nome(x) if x else ""
        if n and n not in [c[1] for c in coppie]:
            coppie.append((etichette[k] if k < len(etichette) else "nome %d" % (k + 1), n))
    if not coppie:
        return {"esito": "non_trovato", "ndg": None, "nome_1info": None, "nome_abbinato": None, "per_nome": [],
                "cache": None, "motivo": "nessun nome dell'emittente da cercare su 1INFO"}
    a = anagrafe()
    if a["stato"] not in ("ok", "STALE"):
        return {"esito": "KO", "ndg": None, "nome_1info": None, "nome_abbinato": None, "per_nome": [],
                "cache": a.get("cache"), "motivo": a["motivo"]}
    norm = [(e["ndg"], e["nome"], _bi.normalizza_nome(e["nome"])) for e in a["emittenti"]]
    per_nome = []
    for etichetta, n in coppie:
        simili, gradino = _abbina_con_gradino(n, norm)
        espanso = None
        if not simili or gradino == "debole":
            # nome del listino abbreviato («Bco X»): si ritenta con le abbreviazioni espanse; tutte le
            # letture insieme, quindi una sigla che porta a due emittenti resta AMBIGUA
            varianti = _varianti(n)
            forti, deboli = [], []
            for v in varianti:
                s_v, g_v = _abbina_con_gradino(v, norm)
                (deboli if g_v == "debole" else forti).extend(o for o in s_v if o not in forti + deboli)
            if forti:
                simili, gradino = forti, "espanso"
            espanso = varianti or None
        ndg = sorted({o[0] for o in simili})
        esito = ("ambiguo" if ndg and (len(ndg) > 1 or gradino == "debole") else "ok" if ndg else "non_trovato")
        per_nome.append({"origine": etichetta, "nome": n, "ndg": ndg, "espanso": espanso, "gradino": gradino,
                         "nomi_1info": sorted({o[1] for o in simili})[:6], "esito": esito})
    nota = (" (lista emittenti STALE: %s)" % a["motivo"]) if a["stato"] == "STALE" else ""
    base = {"per_nome": per_nome, "cache": a.get("cache")}
    univoci = [p for p in per_nome if p["esito"] == "ok"]
    diversi = sorted({p["ndg"][0] for p in univoci})
    # un nome con PIU' ndg non contraddice solo se fra i suoi c'e' quello scelto (es. un nome corto
    # comune a due societa'); se non lo contiene, quel nome indica un altro emittente
    contro = [p for p in per_nome if p["esito"] == "ambiguo" and diversi and diversi[0] not in p["ndg"]]
    if len(diversi) == 1 and contro:
        return dict(base, esito="ambiguo", ndg=None, nome_1info=None, nome_abbinato=None,
                    motivo="il %s %r abbina %s (ndg %d) ma %s: nomi in contraddizione%s" % (
                        univoci[0]["origine"], univoci[0]["nome"], univoci[0]["nomi_1info"][0], diversi[0],
                        "; ".join("il %s %r abbina altri emittenti (%s)" % (q["origine"], q["nome"],
                                                                            ", ".join(q["nomi_1info"]))
                                  for q in contro), nota))
    if len(diversi) > 1:
        return dict(base, esito="ambiguo", ndg=None, nome_1info=None, nome_abbinato=None,
                    motivo="nomi diversi abbinano emittenti 1INFO DIVERSI: %s%s" % ("; ".join(
                        "%s %r -> %s (%d)" % (p["origine"], p["nome"], p["nomi_1info"][0], p["ndg"][0])
                        for p in univoci), nota))
    if univoci:
        p = univoci[0]
        altri = [q for q in per_nome if q is not p]
        return dict(base, esito="ok", ndg=p["ndg"][0], nome_1info=p["nomi_1info"][0], nome_abbinato=p["nome"],
                    motivo="lista emittenti 1INFO: %r (ndg %d) abbinato col %s %r%s%s" % (
                        p["nomi_1info"][0], p["ndg"][0], p["origine"], p["nome"],
                        ("; altri nomi: %s" % ", ".join("%s %r %s" % (q["origine"], q["nome"], q["esito"])
                                                        for q in altri)) if altri else "", nota))
    amb = [p for p in per_nome if p["esito"] == "ambiguo"]
    if amb:
        return dict(base, esito="ambiguo", ndg=None, nome_1info=None, nome_abbinato=None,
                    motivo="piu' emittenti 1INFO per %s%s" % ("; ".join(
                        "%s %r: %s" % (p["origine"], p["nome"], ", ".join(p["nomi_1info"])) for p in amb), nota))
    return dict(base, esito="non_trovato", ndg=None, nome_1info=None, nome_abbinato=None,
                motivo="nessun emittente nella lista 1INFO per %s%s" % (
                    " / ".join("%s %r" % (p["origine"], p["nome"]) for p in per_nome), nota))


def _leggi_attivita(ndg: int) -> Dict[str, Any]:
    r = cerca(URL_COMUNICATI, {"emittente": ndg}, "dataDiffusione", ndg=ndg, length=LUNGHEZZA_ATTIVITA, max_pagine=1)
    out = {"stato": "KO", "ndg": ndg, "ultimo": None, "attivo": None, "errore": r["errore"],
           "motivo": r["motivo"], "letto_il": None}
    if r["stato"] != "ok":
        return out
    lette = [(data_ora(x.get("dataDiffusione"))[0], x.get("sdir")) for x in r["righe"]]
    if r["filtrati"] and not [d for d, _s in lette if d]:
        out.update(errore="layout_cambiato", motivo="comunicati dell'emittente senza data leggibile")
        return out
    # attivo = comunicati DIFFUSI da 1INFO («SDIR 1INFO»); quelli diffusi da un altro SDIR e solo
    # stoccati qui («SDIR TERZI») non fanno di 1INFO lo SDIR dell'emittente (review RV-ON P2-1)
    proprie = [d for d, s in lette if d and s == SDIR_PROPRIO]
    ultimo = max(proprie) if proprie else None
    ultimo_terzi = max((d for d, s in lette if d and s != SDIR_PROPRIO), default=None)
    soglia = (_bi.oggi_roma() - timedelta(days=GIORNI_ATTIVITA)).isoformat()
    piu_vecchia = min((d for d, _s in lette if d), default=None)
    if ultimo is None and (r["filtrati"] or 0) > len(lette) and piu_vecchia and piu_vecchia >= soglia:
        out.update(errore="attivita_non_misurata",
                   motivo="le ultime %d righe sono tutte di altri SDIR e ancora dentro la soglia: attivita' su "
                          "1INFO non misurabile" % len(lette))
        return out
    out.update(stato="ok", ultimo=ultimo, ultimo_terzi=ultimo_terzi, attivo=bool(ultimo and ultimo >= soglia),
               errore=None, letto_il=_bi.adesso_utc().isoformat(timespec="seconds"),
               motivo="ultimo comunicato diffuso da 1INFO: %s%s (soglia di attivita' %s)" % (
                   ultimo or "nessuno",
                   ("; ultimo stoccato da altro SDIR: %s" % ultimo_terzi) if ultimo_terzi else "", soglia))
    return out


def attivita(ndg: int) -> Dict[str, Any]:
    """Ultimo comunicato dell'emittente su 1INFO e se e' ATTIVO (entro GIORNI_ATTIVITA).
    {"stato": ok|KO|STALE, "ndg", "ultimo", "attivo", "motivo", "cache"}."""
    return _bi.con_cache("oneinfo_attivita_%d" % ndg, TTL_ANAGRAFE_S, lambda: _leggi_attivita(ndg))


def _storico(att: Dict[str, Any]) -> str:
    return ("emittente STORICO su 1INFO: %s, piu' vecchio di %d giorni: in lista ma non piu' attivo "
            "(usa un altro SDIR)" % (att.get("motivo") or "", GIORNI_ATTIVITA))


# ============================================================
# DEPOSITO: scelta del documento (puro, senza rete)
# ============================================================
# Nomi in piu' rispetto a emarket_sdir.DOCUMENTI_DEPOSITO, misurati sui titoli dei DOCUMENTI
# 1INFO: «Relazione finanziaria primo trimestre AAAA», «Interim report first quarter AAAA».
_NOMI_EXTRA = {
    "trimestrale": (r"relazione\s+finanziaria(?=\s+(?:consolidata\s+)?(?:al\s+(?:31\s+marzo|30\s+settembre)|"
                    r"(?:del\s+)?primo\s+trimestre|(?:dei\s+)?(?:primi\s+)?nove\s+mesi))",
                    r"interim\s+(?:financial\s+)?report(?=\s+(?:for\s+the\s+|on\s+the\s+)?(?:first\s+quarter|nine\s+months))"),
}


def _regex_nomi(tipo: str) -> Tuple["re.Pattern", "re.Pattern"]:
    it, en = _em.DOCUMENTI_DEPOSITO[tipo]
    extra = _NOMI_EXTRA.get(tipo)
    if extra:
        it, en = "%s|%s" % (it, extra[0]), "%s|%s" % (en, extra[1])
    return re.compile(it, re.I), re.compile(en, re.I)


def _regex_periodo(fine: date, tipo: str) -> "re.Pattern":
    """Il periodo come in emarket_sdir, piu' «Semestrale AAAA» / «half-year ... AAAA» (titoli
    dei documenti 1INFO, solo per la semestrale al 30/06)."""
    base = _em._regex_periodo(fine, tipo).pattern
    if tipo == "semestrale" and fine.month == 6:
        base += r"|semestral[ei]\D{0,15}%d\b|half[\s-]*year\D{0,25}%d\b" % (fine.year, fine.year)
    return re.compile(base, re.I)


# (MF 05/10) titoli che COMINCIANO come la relazione o attestazione del revisore sul documento: non
# sono il documento («Relazione della Societa' di Revisione sulla Relazione finanziaria semestrale...»,
# «Relazione di revisione contabile limitata...», «Review report on...»). Un titolo che contiene la
# relazione del revisore IN CODA («Relazione semestrale ... e relativa relazione della societa' di
# revisione») resta il documento.
_REVISIONE = re.compile(r"\s*(?:relazione\s+(?:della\s+)?(?:societ\S*\s+)?di\s+revisione|relazione\s+societ\S*\s+di\s+"
                        r"revisione|review\s+report|(?:independent\s+)?auditor\S*\s+report|report\s+of\s+the\s+"
                        r"(?:independent\s+)?(?:auditor|audit)|attestazione\b)", re.I)
# (MF 05/10) avviso del deposito stoccato fra i documenti («Deposito informazioni periodiche
# aggiuntive al 31 marzo»): conferma del documento, non il documento
_AVVISO = re.compile(r"\s*(?:avviso\s+(?:di\s+)?(?:avvenuto\s+)?deposito|deposito\b|notice\s+of\s+(?:filing|deposit))",
                     re.I)


def _categorie(r: Dict[str, Any]) -> List[str]:
    return [c.strip() for c in (r.get("categoria") or "").split(",") if c.strip()]


def candidati_deposito(righe: List[Dict[str, Any]], tipo: str, fine: date) -> Dict[str, Any]:
    """Righe normalizzate dei DOCUMENTI (riga_documento) -> verdetto. Puro, senza rete:
    {"stato": ok|non_trovato|ambiguo, "scelto", "candidati", "conferme", "prova", "scartati",
    "motivo"}. Righe ammesse: categoria del tipo (CATEGORIE_DEPOSITO), stoccate DOPO la fine del
    periodo ed entro FINESTRA_DEPOSITO_GIORNI.
    Annuale: ANCORA STRUTTURALE ESEF — il documento con protocollo XBRL, preferito il
    consolidato, con l'anno del periodo nel titolo; uno solo = ok (prova 'esef'); piu' d'uno =
    si sceglie per titolo fra quelli; nessuno = regola del titolo.
    Regola del titolo: NOME del documento + PERIODO, l'italiano comanda (l'inglese e' conferma),
    stesso titolo nello stesso giorno = un documento solo, piu' candidati = ambiguo."""
    limite = (fine + timedelta(days=FINESTRA_DEPOSITO_GIORNI)).isoformat()
    ammessi = set(CATEGORIE_DEPOSITO[tipo])
    righe = [r for r in righe if set(_categorie(r)) & ammessi
             and fine.isoformat() < r["data"] <= limite]
    scartati: List[Dict[str, Any]] = []

    def _scarta(r, motivo):
        scartati.append({k: r.get(k) for k in ("protocollo", "data", "ora", "titolo", "categoria")} | {"motivo": motivo})

    def _per_titolo(gruppo: List[Dict[str, Any]], prova: str) -> Dict[str, Any]:
        it_rx, en_rx = _regex_nomi(tipo)
        per = _regex_periodo(fine, tipo)
        cand = []
        for r in gruppo:
            t = r["titolo"]
            if not per.search(t):
                continue
            lingua = "it" if it_rx.search(t) else "en" if en_rx.search(t) else None
            if lingua is None:
                _scarta(r, "periodo senza il nome del documento (%s): non e' la relazione" % tipo)
                continue
            if _em._APPROVAZIONE.search(t):
                _scarta(r, "titolo di approvazione, non il documento")
                continue
            if _REVISIONE.match(t):
                _scarta(r, "relazione/attestazione della societa' di revisione SUL documento, non il documento")
                continue
            cand.append(dict(r, lingua=lingua, prova=prova, rettifica=bool(_em._RETTIFICA.search(t)),
                             avviso=bool(_AVVISO.match(t))))
        it = [c for c in cand if c["lingua"] == "it"]
        scelti = it or cand
        conferme = [c for c in cand if c not in scelti]
        uno: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for c in sorted(scelti, key=lambda c: (c["data"], c["ora"] or "")):
            k = (c["data"], c["titolo"].lower())
            if k in uno:
                conferme.append(c)
            else:
                uno[k] = c
        scelti = list(uno.values())
        nota, conferma = None, None
        if len(scelti) > 1:
            # (MF 05/10) errata corrige / rettifica di un documento gia' depositato: vale la data
            # dell'ORIGINALE (uno solo), la rettifica va fra le conferme, dichiarata
            orig = [c for c in scelti if not c["rettifica"]]
            rett = [c for c in scelti if c["rettifica"]]
            if len(orig) == 1 and rett and all((c["data"], c["ora"] or "") >= (orig[0]["data"], orig[0]["ora"] or "")
                                                 for c in rett):
                scelti, conferme = orig, conferme + rett
                nota = "%d rettifiche/errata corrige successive fra le conferme: vale la data dell'originale" % len(rett)
        if len(scelti) > 1:
            # (MF 05/10, come eMarket) avviso di deposito stoccato fra i documenti + documento: la data
            # e' lo STOCCAGGIO del documento; l'avviso diventa comunicato_conferma con lo scarto
            docs = [c for c in scelti if not c["avviso"]]
            avvisi = [c for c in scelti if c["avviso"]]
            if len(docs) == 1 and avvisi:
                a = min(avvisi, key=lambda c: abs((date.fromisoformat(c["data"]) - date.fromisoformat(docs[0]["data"])).days))
                conferma = {"data": a["data"], "ora": a["ora"], "titolo": a["titolo"], "protocollo": a["protocollo"],
                            "url": a["url_pdf"], "natura": "avviso_di_deposito",
                            "scarto_giorni": (date.fromisoformat(a["data"]) - date.fromisoformat(docs[0]["data"])).days}
                scelti, conferme = docs, conferme + avvisi
        return {"scelti": scelti, "conferme": conferme, "nota": nota, "comunicato_conferma": conferma}

    prova = "titolo"
    if tipo == "annuale":
        anno = re.compile(r"(?<!\d)%d(?!\d)" % fine.year)
        esef = [r for r in righe if r.get("esef") and anno.search(r["titolo"])]
        esef = [r for r in esef if r.get("consolidato") == 1] or esef
        if len(esef) == 1:
            v = {"scelti": [dict(esef[0], lingua=None, prova="esef", rettifica=False)], "conferme": []}
            prova = "esef"
        elif esef:
            v = _per_titolo(esef, "esef")
            prova = "esef"
        else:
            v = _per_titolo(righe, "titolo")
    else:
        v = _per_titolo(righe, "titolo")
    if prova == "esef":
        # le altre versioni dello stesso documento (cortesia, inglese) sono conferme, non rivali
        altri = _per_titolo([r for r in righe if r not in esef], "titolo")
        v["conferme"] = v["conferme"] + altri["scelti"] + altri["conferme"]
    scelti, conferme = v["scelti"], v["conferme"]
    n_scartati = len(scartati)
    base = {"conferme": conferme, "prova": prova, "scartati": scartati[:MAX_SCARTATI], "nota": v.get("nota"),
            "comunicato_conferma": v.get("comunicato_conferma") if len(scelti) == 1 else None}
    if not scelti:
        return dict(base, stato="non_trovato", scelto=None, candidati=[], prova=None,
                    motivo="nessun documento %s col nome e il periodo %s nelle categorie %s stoccato fra il %s e "
                           "il %s (%d righe ammesse, %d scartate: vedi scartati)" % (
                               tipo, fine.isoformat(), "/".join(CATEGORIE_DEPOSITO[tipo]),
                               (fine + timedelta(days=1)).isoformat(), limite, len(righe), n_scartati))
    if len(scelti) > 1:
        return dict(base, stato="ambiguo", scelto=None, candidati=scelti,
                    motivo="%d documenti candidati per lo stesso tipo e periodo: nessuno scelto (vedi candidati)"
                           % len(scelti))
    return dict(base, stato="ok", scelto=scelti[0], candidati=scelti, motivo=None)


# ============================================================
# PDF INTERNAL DEALING (formati 1INFO misurati) -> campi
# ============================================================
_ISIN_RX = re.compile(r"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{9}\d)(?![A-Z0-9])")
_RIGA_PV_1I = re.compile(r"^\s*(?:€|EUR|�)?\s*(\d[\d.,]*)\s*(EUR|USD|GBP|CHF|€)?\s+(\d[\d.,]*)\s*$", re.M)
_SEP_OP_1I = re.compile(r"Operazione\s*/\s*Operation\s*-\s*\d+")
_DESCR_1I = re.compile(r"a\)\s*Descrizione\s+dello\s+strumento")


def data_operazione(testo: str) -> Tuple[Optional[str], Optional[str]]:
    """(data ISO, problema). AAAA-MM-GG; oppure X/Y/AAAA (o X.Y.AAAA) letta SOLO se univoca:
    un numero > 12 decide quale e' il giorno (9/18/2026 = formato USA, 18/9/2026 = italiano);
    entrambi <= 12 e diversi = AMBIGUA -> None. Mai indovinata."""
    m = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", testo)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), None
        except ValueError:
            return None, "data %r non valida" % m.group(0)
    m = re.search(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)", testo)
    if not m:
        return None, "data dell'operazione non trovata"
    a, b, anno = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if a <= 12 and b <= 12 and a != b:
        return None, "data %r ambigua (giorno/mese o mese/giorno?)" % m.group(0)
    giorno, mese = (b, a) if b > 12 else (a, b)
    try:
        return date(anno, mese, giorno).isoformat(), None
    except ValueError:
        return None, "data %r non valida" % m.group(0)


def _dopo(testo: str, etichetta_rx: str, fino_rx: str) -> Optional[str]:
    m = re.search(r"(?:%s)(.*?)(?=%s)" % (etichetta_rx, fino_rx), testo, re.S | re.I)
    return " ".join(m.group(1).split()) if m else None


def _parse_operazione_1i(blocco: str) -> Dict[str, Any]:
    op: Dict[str, Any] = {"tipo_operazione": None, "strumento": None, "isin": None, "prezzo": None,
                          "valuta": None, "quantita": None, "data_operazione": None,
                          "ora_operazione_utc": None, "luogo": None, "righe_prezzo_volume": [],
                          "parse_ok": False, "motivo": None, "nota": None}
    problemi: List[str] = []
    mancano: List[str] = []
    i_b = blocco.find("b) Natura")
    sez_a = blocco[:i_b] if i_b >= 0 else blocco
    m = _ISIN_RX.search(sez_a.split("Identification code", 1)[-1])
    op["isin"] = m.group(1) if m and _bi.isin_valido(m.group(1)) else None
    if op["isin"] is None:
        mancano.append("ISIN (a)")
    k = sez_a.find("Identification code")
    if k >= 0:
        strum = " ".join(_ISIN_RX.sub(" ", sez_a[k + len("Identification code"):]).split())
        op["strumento"] = strum or None
    i_c = blocco.find("c) Prezzo")
    if i_b >= 0 and i_c > i_b:
        parte = blocco[i_b:i_c]
        j = parte.rfind("option programme")
        parte = parte[j + len("option programme"):] if j >= 0 else re.split(r"transaction\s*\(5\)", parte, maxsplit=1)[-1]
        tipo = " ".join(parte.split())
        tipo = re.sub(r"\s+(?:NO|SI|SÌ|YES)$", "", tipo).strip()
        op["tipo_operazione"] = tipo or None
    if not op["tipo_operazione"]:
        mancano.append("tipo operazione (b)")
    i_d = blocco.find("d) Informazioni aggregate")
    i_e = blocco.find("e) Data dell'operazione")
    righe_c = _RIGA_PV_1I.findall(blocco[i_c:i_d]) if i_c >= 0 and i_d > i_c else []
    righe_d = _RIGA_PV_1I.findall(blocco[i_d:i_e]) if i_d >= 0 and i_e > i_d else []
    op["righe_prezzo_volume"] = [[p, v] for p, _val, v in righe_c]
    fonte = righe_d or (righe_c if len(righe_c) == 1 else [])
    if len(fonte) == 1:
        p, val, v = fonte[0]
        op["prezzo"], op["quantita"] = _em._num_prezzo(p), _em._num_quantita(v)
        op["valuta"] = "EUR" if val in ("EUR", "€") else (val or ("EUR" if re.search(r"€|EUR", blocco[i_c:i_e]) else None))
        if op["prezzo"] is None:
            problemi.append("prezzo %r in formato ambiguo o non riconosciuto" % p)
        if op["quantita"] is None:
            problemi.append("volume %r in formato ambiguo (separatore delle migliaia o decimali?)" % v)
    else:
        mancano.append("prezzo e volume (d: %d righe; c: %d righe)" % (len(righe_d), len(righe_c)))
    if i_e >= 0:
        i_f = blocco.find("f) Luogo", i_e)
        d, problema = data_operazione(blocco[i_e:i_f if i_f > i_e else len(blocco)].split("transaction(8)", 1)[-1])
        op["data_operazione"] = d
        if problema:
            problemi.append(problema)
        if i_f >= 0:
            coda = blocco[i_f:].split("transaction(9)", 1)[-1].strip().splitlines()
            luogo = " ".join(coda[0].split()) if coda else ""
            op["luogo"] = luogo or None
    else:
        mancano.append("data operazione (e)")
    doppie = [e for e in ("b) Natura", "e) Data dell'operazione") if blocco.count(e) > 1]
    if doppie:
        problemi.append("piu' operazioni nello stesso blocco (%s ripetute): operazioni non separate, "
                        "lettura non affidabile" % ", ".join(doppie))
    if mancano:
        problemi.insert(0, "etichette non trovate: " + ", ".join(mancano))
    op["parse_ok"] = not problemi
    op["motivo"] = "; ".join(problemi) or None
    if op["prezzo"] == 0:
        op["nota"] = "prezzo 0: assegnazione/attribuzione gratuita (es. piano di incentivi), NON un acquisto a mercato"
    return op


def parse_testo_internal_dealing_1info(testo: str) -> Dict[str, Any]:
    """Testo del PDF (modello MAR compilato dall'emittente, formati 1INFO) -> {"soggetto",
    "ruolo", "operazioni", "parse_ok", "motivo_parse"}: stessa forma di emarket_sdir."""
    testo = (testo or "").replace("\r\n", "\n").replace("\r", "\n")
    out: Dict[str, Any] = {"soggetto": None, "ruolo": None, "operazioni": [], "parse_ok": False, "motivo_parse": None}
    problemi: List[str] = []
    sogg = _dopo(testo, r"Nome\s*/\s*First\s*Name", r"\n\s*2\s*\n|\n\s*2\s+Motivo|Motivo della notifica")
    sogg = re.sub(r"\s+2$", "", sogg or "").strip()
    out["soggetto"] = sogg if re.search(r"[A-Za-z]{2}", sogg) else None
    if out["soggetto"] is None:
        problemi.append("soggetto (sezione 1: Nome/First Name)")
    ruolo = _dopo(testo, r"Position\s*-\s*Status\s*\(1\)", r"b\)\s*Notifica")
    ruolo = (ruolo or "").lstrip("- ").strip()
    out["ruolo"] = ruolo or None
    if out["ruolo"] is None:
        problemi.append("ruolo (sezione 2: Posizione/Position)")
    i4 = testo.find("Dati relativi all'operazione")
    if i4 < 0:
        problemi.append("sezione 4 (operazioni) non trovata")
    else:
        sez4 = testo[i4:]
        tagli = [m.start() for m in _SEP_OP_1I.finditer(sez4)]
        if not tagli:
            # formato «Word»: la sezione 4 (o le sole voci a-f) ripetuta per ogni operazione SENZA il
            # separatore numerato: si taglia su ogni «a) Descrizione dello strumento» (review RV-ON P1-a)
            tagli = [m.start() for m in _DESCR_1I.finditer(sez4)]
        blocchi = [sez4[a:b] for a, b in zip(tagli, tagli[1:] + [len(sez4)])] if tagli else [sez4]
        out["operazioni"] = [_parse_operazione_1i(b) for b in blocchi]
        cattive = [str(i + 1) for i, o in enumerate(out["operazioni"]) if not o["parse_ok"]]
        if cattive:
            problemi.append("operazioni non lette per intero: %s" % ", ".join(cattive))
    out["parse_ok"] = not problemi
    out["motivo_parse"] = "; ".join(problemi) or None
    return out


def parse_pdf_internal_dealing_1info(dati: bytes) -> Dict[str, Any]:
    try:
        from pypdf import PdfReader
        testo = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dati)).pages)
    except Exception as e:
        return {"soggetto": None, "ruolo": None, "operazioni": [], "parse_ok": False,
                "motivo_parse": "PDF illeggibile: %s" % type(e).__name__}
    if not testo.strip():
        return {"soggetto": None, "ruolo": None, "operazioni": [], "parse_ok": False,
                "motivo_parse": "PDF senza testo estraibile (scansione?)"}
    return parse_testo_internal_dealing_1info(testo)


# ============================================================
# VOCE DEL NEGOZIO -> ndg
# ============================================================
def _ndg_dalla_voce(ticker: str, ndg: Optional[int]) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str], Optional[str], Optional[str]]:
    """(voce, ndg, stato_se_fermo, errore, motivo). `ndg` passato dall'instradatore vince; poi
    il campo 'oneinfo' del negozio (int = dichiarato, null = dichiarato NON su 1INFO, assente =
    non dichiarato: lo misura sdir.py)."""
    voce, err, mot = _bi.voce_ticker_o_auto(ticker)
    if voce is None:
        return None, None, "KO", err, mot
    if ndg is not None:
        if isinstance(ndg, bool) or not isinstance(ndg, int) or ndg <= 0:
            return voce, None, "KO", "parametro", "ndg %r non e' un intero positivo" % (ndg,)
        return voce, ndg, None, None, None
    v = voce.get("oneinfo", _ASSENTE)
    if v is _ASSENTE:
        return voce, None, "non_coperto", "oneinfo_non_dichiarato", (
            "ndg 1INFO non nel negozio (campo 'oneinfo' assente): la misura automatica la fa sdir.py")
    if v is None:
        return voce, None, "non_coperto", "dichiarato_non_su_oneinfo", (
            "emittente dichiarato NON su 1INFO-SDIR nel negozio (oneinfo: null)")
    return voce, v, None, None, None


# ============================================================
# INTERNAL DEALING
# ============================================================
def _base(ticker: str, giorni: Any) -> Dict[str, Any]:
    out = _em._base(ticker, giorni)
    out.update(fonte=FONTE, oneinfo_ndg=None, url=URL_COMUNICATI,
               limiti=out["limiti"] + [
                   "elenco = comunicati in categoria %s UNITI a quelli con %r nel titolo: titoli senza "
                   "quelle parole e fuori categoria non sono visti" % (CATEGORIA_ID, TESTO_ID),
                   LIMITE_ORARI, "ora_operazione_utc sempre None: il PDF 1INFO non dichiara il fuso",
                   "date M/D/YYYY lette solo se univoche; volumi col separatore delle migliaia = None",
                   LIMITE_TERMINI],
               url_liste=[], sha256_liste={}, richieste={"liste": 0, "pdf": 0})
    return out


def _comunicazione(riga: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    chiave = "oneinfo_pdf_v%d_%s" % (PARSER_VERSIONE, riga["protocollo"])
    c = _bi.cache_leggi(chiave)
    if c is not None:
        return dict(riga, **c["risultato"]), True
    if not riga.get("url_pdf"):
        return dict(riga, soggetto=None, ruolo=None, operazioni=[], parse_ok=False,
                    motivo_parse="nome del PDF non riconosciuto: PDF non scaricato"), False
    try:
        http, corpo = _richiesta("GET", riga["url_pdf"])
    except Exception as e:
        return dict(riga, soggetto=None, ruolo=None, operazioni=[], parse_ok=False,
                    motivo_parse="PDF non scaricato: %s" % type(e).__name__), False
    if http != 200:
        return dict(riga, soggetto=None, ruolo=None, operazioni=[], parse_ok=False,
                    motivo_parse="PDF non scaricato: HTTP %s" % http), False
    p = parse_pdf_internal_dealing_1info(corpo)
    if p["parse_ok"]:
        _bi.cache_scrivi(chiave, p)
    return dict(riga, **p), True


def _leggi_id(ticker: str, isin: str, ndg: int, giorni: int) -> Dict[str, Any]:
    out = _base(ticker, giorni)
    out.update(isin=isin, oneinfo_ndg=ndg)
    oggi = _bi.oggi_roma()
    soglia = oggi - timedelta(days=giorni)
    finestra = {"from": epoch_di(soglia), "to": epoch_di(oggi + timedelta(days=1))}
    righe: Dict[str, Dict[str, Any]] = {}
    illeggibili = 0
    for filtri in ({"emittente": ndg, "categoria": CATEGORIA_ID, "dataDiffusione": finestra},
                   {"emittente": ndg, "oggetto": TESTO_ID, "dataDiffusione": finestra}):
        r = cerca(URL_COMUNICATI, filtri, "dataDiffusione", ndg=ndg, length=LUNGHEZZA_ID, max_pagine=2)
        out["richieste"]["liste"] += r["richieste"]
        out["url_liste"] += r["url_liste"]
        out["sha256_liste"].update(r["sha256_liste"])
        if r["stato"] != "ok":
            out.update(errore=r["errore"], motivo="ricerca %s: %s" % (
                "categoria" if "categoria" in filtri else "testo", r["motivo"]))
            return out
        if r["troncato"]:
            out["troncato"] = True
            out["limiti"].append("ricerca %s: lette %d righe su %d" % (
                "categoria" if "categoria" in filtri else "testo", len(r["righe"]), r["filtrati"]))
        for x in r["righe"]:
            n = riga_comunicato(x)
            if n is None:
                illeggibili += 1
                continue
            if n["data"] >= soglia.isoformat():
                righe.setdefault(n["protocollo"], n)
    out["pagine_lette"] = out["richieste"]["liste"]
    if illeggibili:
        # una riga vera che non si legge (campo rinominato?) poteva essere una comunicazione nella
        # finestra: niente «vuoto» ne' lista parziale servita come intera (review RV-ON P1-b)
        out.update(errore="layout_cambiato",
                   motivo="%d righe dell'API senza data di diffusione/PDF leggibili: campi cambiati, verdetto "
                          "sospeso" % illeggibili)
        return out
    out["letto_il"] = _bi.adesso_utc().isoformat(timespec="seconds")
    if not righe:
        att = attivita(ndg)
        if att.get("stato") not in ("ok", "STALE"):
            out.update(errore="attivita_non_misurata",
                       motivo="nessuna comunicazione negli ultimi %d giorni, ma l'attivita' dell'emittente su 1INFO "
                              "non e' misurata (%s): vuoto non dichiarabile" % (giorni, att.get("motivo")))
            return out
        if not att["attivo"]:
            out.update(stato="non_coperto", errore="emittente_storico", motivo=_storico(att))
            return out
        out.update(stato="vuoto_misurato",
                   motivo="nessuna comunicazione di internal dealing negli ultimi %d giorni (dal %s); %s"
                          % (giorni, soglia.isoformat(), att["motivo"]))
        return out
    ordinate = sorted(righe.values(), key=lambda r: (r["data"], r["ora"] or ""), reverse=True)
    comunicazioni = []
    for i, r in enumerate(ordinate):
        if i >= MAX_PDF:
            comunicazioni.append(dict(r, soggetto=None, ruolo=None, operazioni=[], parse_ok=False,
                                      motivo_parse="PDF non letto: oltre il tetto di %d PDF per chiamata" % MAX_PDF))
            continue
        in_cache = _bi.cache_leggi("oneinfo_pdf_v%d_%s" % (PARSER_VERSIONE, r["protocollo"])) is not None
        com, scaricato = _comunicazione(r)
        if not in_cache and r.get("url_pdf"):
            out["richieste"]["pdf"] += 1
        comunicazioni.append(_em.incrocia_isin(com, isin))
        out["pdf_letti" if scaricato else "pdf_falliti"] += 1
    out["pdf_non_letti"] = max(0, len(ordinate) - MAX_PDF)
    if out["pdf_non_letti"]:
        out["troncato"] = True
        out["limiti"].append("%d PDF non letti (tetto %d per chiamata): parse_ok False" % (out["pdf_non_letti"], MAX_PDF))
    isin_letti = sorted({o["isin"] for c in comunicazioni for o in c.get("operazioni") or [] if o.get("isin")})
    out["parse_falliti"] = sum(1 for c in comunicazioni if not c["parse_ok"])
    motivi = []
    if isin_letti and isin not in isin_letti:
        # come eMarket per la singola operazione: ognuna resta marcata isin_incoerente (incrocia_isin),
        # niente si cancella; il sospetto sull'ndg si DICHIARA (review RV-ON P2-2)
        motivi.append("nessuna operazione sull'ISIN del book %s nella finestra: nei PDF dell'ndg 1INFO %d ci "
                      "sono solo %s (altri strumenti dell'emittente, oppure ndg sbagliato: controlla)"
                      % (isin, ndg, ", ".join(isin_letti)))
        out["limiti"].append("ndg da verificare: nessuna operazione sul titolo del book (vedi motivo)")
    if out["parse_falliti"]:
        motivi.append("%d comunicazioni su %d non lette per intero (vedi motivo_parse)"
                      % (out["parse_falliti"], len(comunicazioni)))
    out.update(stato="ok", comunicazioni=comunicazioni, motivo="; ".join(motivi) or None)
    return out


def get_internal_dealing(ticker: str, *, giorni: int = 180, ndg: Optional[int] = None) -> Dict[str, Any]:
    """Internal dealing da 1INFO negli ultimi `giorni` (contratto di emarket_sdir). `ndg`:
    passato dall'instradatore (sdir.py) quando l'ha misurato; altrimenti dal negozio."""
    if isinstance(giorni, bool) or not isinstance(giorni, int) or not 1 <= giorni <= GIORNI_MAX:
        out = _base(ticker, giorni)
        out.update(errore="parametro", motivo="giorni dev'essere un intero fra 1 e %d, non %r" % (GIORNI_MAX, giorni))
        out["cache"] = {"stato": "nessuna", "eta_s": None}
        return out
    voce, n, fermo, err, mot = _ndg_dalla_voce(ticker, ndg)
    if fermo is not None:
        out = _base(ticker, giorni)
        out.update(stato=fermo, errore=err, motivo=mot, isin=voce["isin"] if voce else None)
        if voce:
            out["voce_da"] = voce["negozio"]
        out["cache"] = {"stato": "nessuna", "eta_s": None}
        return out
    out = _bi.con_cache("oneinfo_id_v%d_%d_%d_%s" % (PARSER_VERSIONE, n, giorni, voce["isin"]), TTL_LISTA_S,
                        lambda: _leggi_id(ticker, voce["isin"], n, giorni),
                        salva=lambda r: not r.get("pdf_falliti"))
    out["ticker"] = (ticker or "").strip().upper()
    out["voce_da"] = voce["negozio"]
    return out


# ============================================================
# DATA DI DEPOSITO (= stoccaggio del documento)
# ============================================================
def _base_deposito(ticker: str, tipo: Any, periodo_fine: Any) -> Dict[str, Any]:
    out = _em._base_deposito(ticker, tipo, periodo_fine)
    out.update(fonte=FONTE, oneinfo_ndg=None, tipo_data="stoccaggio_documento", esef=None, consolidato=None,
               limiti=["la data e' quella di STOCCAGGIO su 1INFO del DOCUMENTO stesso (la relazione), non la "
                       "diffusione di un comunicato: definizione diversa da eMarket SDIR (diffusione del "
                       "comunicato di messa a disposizione); l'avviso di deposito sul quotidiano non conta",
                       "legame col documento: versione ESEF (protocollo XBRL) per l'annuale (prova='esef'), "
                       "altrimenti NOME + PERIODO nel titolo (prova='titolo'); non per hash della relazione",
                       "non_trovato = non trovato fra i documenti delle categorie cercate entro %d giorni dalla "
                       "fine del periodo, non «mai depositato»" % FINESTRA_DEPOSITO_GIORNI,
                       "`consolidato` = campo bilancio_consolidato di 1INFO (il sito lo usa per il badge «Bilancio "
                       "ESEF ufficiale»): NON prova che il documento sia il bilancio consolidato",
                       LIMITE_ORARI, LIMITE_TERMINI])
    return out


def verdetto_documenti(grezze: List[Dict[str, Any]], filtrati: Any, troncato: bool, tipo: str,
                       fine: date) -> Dict[str, Any]:
    """PURA: righe grezze dell'API Documenti (gia' senza le righe null) -> verdetto di
    candidati_deposito, oppure {"stato": "KO", "errore", "motivo"} per righe illeggibili o lista
    troncata senza scelta. La usano la lettura e la riverifica (stessa logica)."""
    righe = [n for n in (riga_documento(x) for x in grezze) if n is not None]
    if len(righe) < len(grezze):
        # una riga illeggibile poteva essere il deposito: verdetto sospeso (review RV-ON P1-b)
        return {"stato": "KO", "errore": "layout_cambiato",
                "motivo": "%d righe su %d senza data di stoccaggio/protocollo/titolo leggibili: campi cambiati, "
                          "verdetto sospeso" % (len(grezze) - len(righe), len(grezze))}
    v = candidati_deposito(righe, tipo, fine)
    if troncato and v["stato"] != "ok":
        return {"stato": "KO", "errore": "troncato",
                "motivo": "lista documenti troncata (%d su %d) e nessun documento scelto: verdetto sospeso"
                          % (len(grezze), filtrati)}
    return v


_CONFRONTO_RIVERIFICA = ("stato", "data_deposito", "ora_deposito", "titolo", "protocollo", "url", "categoria", "prova")


def riverifica_deposito(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        fine_esercizio: Optional[str] = None) -> Tuple[bool, str]:
    """Riverifica SENZA rete di una ricevuta di get_data_deposito (INTERFACCIA_UE «AGGIUNTA 5»):
    decomprime `risposte_salvate`, ricontrolla gli sha256 (sui byte non compressi), controlla che le
    risposte siano quelle della richiesta giusta (emittente = oneinfo_ndg, finestra che parte dal
    giorno dopo `periodo_fine`), RIFA' parse + scelta con le stesse funzioni pure
    (parse_risposta -> verdetto_documenti) e confronta stato/data/ora/titolo/protocollo/url/
    categoria/prova e i candidati. (ok, motivo). Riverificabili solo gli esiti decisi dalla lista
    documenti (ok, ambiguo, non_trovato); ricevute senza `risposte_salvate` = False col motivo."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un oggetto"
    for campo, atteso in (("ticker", (ticker or "").strip().upper()), ("tipo", tipo)):
        if ricevuta.get(campo) != atteso:
            return False, "%s della ricevuta %r diverso da %r" % (campo, ricevuta.get(campo), atteso)
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return False, "periodo_fine %r non e' una data" % (periodo_fine,)
    if ricevuta.get("periodo_fine") != fine.isoformat():
        return False, "periodo_fine della ricevuta %r diverso da %s" % (ricevuta.get("periodo_fine"), fine.isoformat())
    if fine_esercizio is not None:
        incoerente = _em.coerenza_tipo_periodo(tipo, fine, fine_esercizio)
        if incoerente:
            return False, "parametri incoerenti: %s" % incoerente
    if ricevuta.get("stato") not in ("ok", "ambiguo", "non_trovato"):
        return False, "stato %r non riverificabile (solo ok/ambiguo/non_trovato decisi dalla lista documenti)" % (
            ricevuta.get("stato"),)
    salvate, sha, liste = ricevuta.get("risposte_salvate"), ricevuta.get("sha256_liste"), ricevuta.get("url_liste")
    if not salvate or not isinstance(salvate, dict):
        return False, "ricevuta senza risposte_salvate (vecchia): riverifica impossibile"
    if not isinstance(liste, list) or not isinstance(sha, dict) or set(liste) != set(sha) or set(liste) != set(salvate) \
            or len(liste) != len(set(liste)):
        return False, "url_liste, sha256_liste e risposte_salvate non hanno le stesse chiavi"
    ndg = ricevuta.get("oneinfo_ndg")
    if isinstance(ndg, bool) or not isinstance(ndg, int):
        return False, "oneinfo_ndg assente o non intero"
    dal = str(epoch_di(fine + timedelta(days=1)))
    grezze: List[Dict[str, Any]] = []
    filtrati = None
    for k, chiave in enumerate(liste):
        q = parse_qs(chiave.split("?", 1)[1]) if chiave.startswith("POST " + URL_DOCUMENTI + "?") else {}
        if q.get("emittente") != [str(ndg)] or q.get("dataStoccaggio.from") != [dal] or \
                q.get("start") != [str(k * LUNGHEZZA_DOC)]:
            return False, "risposta %r non e' la richiesta dei documenti dell'ndg %d dal %s (pagina %d)" % (
                chiave[:120], ndg, (fine + timedelta(days=1)).isoformat(), k + 1)
        try:
            corpo = decomprimi(salvate[chiave])
        except (ValueError, OSError, EOFError) as e:
            return False, "risposta salvata illeggibile (%s)" % type(e).__name__
        if hashlib.sha256(corpo).hexdigest() != sha[chiave]:
            return False, "sha256 della risposta salvata diverso da quello della ricevuta (pagina %d)" % (k + 1)
        p = parse_risposta(corpo, ndg)
        if p["stato"] != "ok":
            return False, "risposta salvata non valida: %s" % p["motivo"]
        filtrati = p["filtrati"]
        grezze.extend(p["righe"])
    troncato = len(grezze) < (filtrati or 0)
    v = verdetto_documenti(grezze, filtrati, troncato, tipo, fine)
    s = v.get("scelto") or {}
    rifatto = {"stato": v["stato"], "data_deposito": s.get("data"), "ora_deposito": s.get("ora"),
               "titolo": s.get("titolo"), "protocollo": s.get("protocollo"), "url": s.get("url_pdf"),
               "categoria": s.get("categoria"), "prova": v.get("prova")}
    diversi = [c for c in _CONFRONTO_RIVERIFICA if rifatto[c] != ricevuta.get(c)]
    if diversi:
        return False, "la scelta rifatta dalle risposte salvate differisce in: %s (%s)" % (
            ", ".join(diversi), "; ".join("%s %r != %r" % (c, rifatto[c], ricevuta.get(c)) for c in diversi[:3]))
    cc_r = (v.get("comunicato_conferma") or {}).get("protocollo")
    cc_ric = (ricevuta.get("comunicato_conferma") or {}).get("protocollo")
    if cc_r != cc_ric:
        return False, "comunicato_conferma diverso: rifatto %r, nella ricevuta %r" % (cc_r, cc_ric)
    cand_r = [c.get("protocollo") for c in v.get("candidati") or []]
    cand_ric = [c.get("protocollo") for c in ricevuta.get("candidati") or [] if isinstance(c, dict)]
    if cand_r != cand_ric:
        return False, "candidati diversi: rifatti %s, nella ricevuta %s" % (cand_r, cand_ric)
    return True, "scelta rifatta da %d risposte salvate: %s %s" % (len(liste), v["stato"], s.get("protocollo") or "")


def _leggi_deposito(ticker: str, isin: str, ndg: int, tipo: str, fine: date) -> Dict[str, Any]:
    out = _base_deposito(ticker, tipo, fine)
    out.update(isin=isin, oneinfo_ndg=ndg, categorie_cercate=list(CATEGORIE_DEPOSITO[tipo]))
    dal = fine + timedelta(days=1)
    al = min(fine + timedelta(days=FINESTRA_DEPOSITO_GIORNI), _bi.oggi_roma()) + timedelta(days=1)
    r = cerca(URL_DOCUMENTI, {"emittente": ndg, "dataStoccaggio": {"from": epoch_di(dal), "to": epoch_di(al)}},
              "dataStoccaggio", ndg=ndg, length=LUNGHEZZA_DOC)
    out["richieste"]["liste"] = r["richieste"]
    out["pagine_lette"] = r["richieste"]
    out.update(url_liste=r["url_liste"], sha256_liste=r["sha256_liste"], risposte_salvate=r["risposte_salvate"])
    dim = sum(len(x["corpo"]) for x in r["risposte_salvate"].values())
    if dim > 1_000_000:
        out["limiti"].append("risposte_salvate: %d byte compressi (oltre 1 MB)" % dim)
    if r["stato"] != "ok":
        out.update(errore=r["errore"], motivo=r["motivo"])
        return out
    v = verdetto_documenti(r["righe"], r["filtrati"], r["troncato"], tipo, fine)
    if v["stato"] == "KO":
        out.update(errore=v["errore"], motivo=v["motivo"])
        return out
    if r["troncato"]:
        out["limiti"].append("documenti letti %d su %d (tetto %d pagine)" % (len(r["righe"]), r["filtrati"], MAX_PAGINE))
    out.update(stato=v["stato"], motivo=v["motivo"], candidati=v["candidati"], conferme=v["conferme"],
               prova=v["prova"], scartati=v["scartati"], letto_il=_bi.adesso_utc().isoformat(timespec="seconds"))
    if v["stato"] == "non_trovato":
        att = attivita(ndg)
        if att.get("stato") in ("ok", "STALE") and not att["attivo"]:
            out.update(stato="non_coperto", errore="emittente_storico", motivo=_storico(att))
            return out
        if att.get("stato") not in ("ok", "STALE"):
            out["limiti"].append("attivita' dell'emittente su 1INFO non misurata (%s): non_trovato non esclude "
                                 "un emittente storico" % att.get("motivo"))
    s = v["scelto"]
    if s is not None:
        out.update(data_deposito=s["data"], ora_deposito=s["ora"], titolo=s["titolo"], url=s["url_pdf"],
                   protocollo=s["protocollo"], categoria=s["categoria"], lingua=s.get("lingua"),
                   natura="documento", esef=s.get("esef"), consolidato=s.get("consolidato"),
                   comunicato_conferma=v.get("comunicato_conferma"))
        if v.get("nota"):
            out["limiti"].append(v["nota"])
        if v.get("comunicato_conferma"):
            out["limiti"].append("avviso di deposito del %s fra i documenti: scarto %+d giorni dal documento (vale lo "
                                 "stoccaggio del documento)" % (v["comunicato_conferma"]["data"],
                                                                v["comunicato_conferma"]["scarto_giorni"]))
        if not s.get("url_pdf"):
            out["limiti"].append("URL del PDF del documento non costruito (nome del file non riconosciuto)")
        else:
            out["limiti"].append("URL del PDF del documento costruito dal protocollo (formato dei comunicati misurato, "
                                 "dei documenti non scaricato dalla sonda)")
    return out


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, ndg: Optional[int] = None,
                      fine_esercizio: Optional[str] = None) -> Dict[str, Any]:
    """Data di STOCCAGGIO su 1INFO della relazione `tipo` (semestrale | annuale | trimestrale)
    del periodo che finisce il `periodo_fine`. Stati: ok | non_trovato | ambiguo | KO |
    non_coperto | STALE. Una richiesta (lista documenti dell'emittente); cache 6 h per gli ok.
    `fine_esercizio` 'MM-GG' (None o '12-31' = solare): coerenza controllata come eMarket. Un
    esercizio NON solare e' supportato solo per l'ANNUALE (il periodo «esercizio AAAA-1/AAAA» e'
    nelle regole del titolo); semestrale/trimestrale non solari = KO 'parametro' dichiarato
    («esercizio non solare non supportato da 1INFO», `parametro_non_supportato` True): i nomi dei
    documenti intermedi sono legati ai mesi dell'esercizio solare, mai un periodo forzato."""
    if fine_esercizio is not None:
        try:
            fine_d = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
        except ValueError:
            fine_d = None
        incoerente = _em.coerenza_tipo_periodo(tipo, fine_d, fine_esercizio) if (
            fine_d is not None and tipo in CATEGORIE_DEPOSITO) else None
        if incoerente:
            out = _base_deposito(ticker, tipo, periodo_fine)
            out.update(errore="parametro", motivo=incoerente)
            return out
        if fine_d is not None and tipo in ("semestrale", "trimestrale") and fine_esercizio != "12-31":
            out = _base_deposito(ticker, tipo, fine_d)
            out.update(errore="parametro", parametro_non_supportato=True,
                       motivo="esercizio non solare non supportato da 1INFO (fine_esercizio %s, %s al %s): "
                              "nessun periodo forzato" % (fine_esercizio, tipo, fine_d.isoformat()))
            return out
    if tipo not in CATEGORIE_DEPOSITO:
        out = _base_deposito(ticker, tipo, periodo_fine)
        out.update(errore="parametro", motivo="tipo %r non ammesso: %s" % (tipo, ", ".join(CATEGORIE_DEPOSITO)))
        return out
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        out = _base_deposito(ticker, tipo, periodo_fine)
        out.update(errore="parametro", motivo="periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,))
        return out
    incoerente = None if fine_esercizio is not None else _em.coerenza_tipo_periodo(tipo, fine)
    if incoerente:
        out = _base_deposito(ticker, tipo, fine)
        out.update(errore="parametro", motivo=incoerente)
        return out
    voce, n, fermo, err, mot = _ndg_dalla_voce(ticker, ndg)
    if fermo is not None:
        out = _base_deposito(ticker, tipo, fine)
        out.update(stato=fermo, errore=err, motivo=mot, isin=voce["isin"] if voce else None)
        if voce:
            out["voce_da"] = voce["negozio"]
        return out
    out = _bi.con_cache("oneinfo_deposito_%d_%s_%s_%s" % (n, tipo, fine.isoformat(), voce["isin"]), TTL_LISTA_S,
                        lambda: _leggi_deposito(ticker, voce["isin"], n, tipo, fine))
    out["ticker"] = (ticker or "").strip().upper()
    out["voce_da"] = voce["negozio"]
    return out
