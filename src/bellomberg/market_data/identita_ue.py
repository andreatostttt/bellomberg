# -*- coding: utf-8 -*-
"""identita_ue.py — ISIN e LEI VERIFICATI dell'emittente di un titolo europeo, per instradare le fonti
UE della data di diffusione (ue_amf vuole ISIN o LEI, ue_fsma e ue_cnmv l'ISIN, depositi_ue il prefisso
ISIN per lo Stato d'origine). Handoff-3, agente ID-UE, 05/10/2026, Opus 5.5.

PERCHE'. L'identita' confermata della Trade Idea ha il NOME (chart Yahoo esatto) ma ne' ISIN ne' LEI.
Questo modulo li conferma o li ricava SOLO da una fonte ufficiale gratuita che li LEGA all'emittente:
mai indovinati dal simbolo, mai il primo di una lista.

FONTE: GLEIF (Global Legal Entity Identifier Foundation), API pubblica https://api.gleif.org/api/v1/
senza chiave; robots.txt `Disallow:` vuoto (tutto ammesso, misurato 05/10/2026); dati LEI in licenza CC0.
  - `lei-records?filter[isin]=<ISIN>`: il LEI a cui la mappatura ufficiale GLEIF-ANNA lega l'ISIN;
  - `lei-records?filter[entity.names]=<nome>`: ricerca per nome in TUTTI i paesi (l'univocita' si
    misura fuori dal paese del listino: review RV-ID P1);
  - `entity-legal-forms/<ELF>`: il nome della forma giuridica (ISO 20275) quando il nome legale non la scrive.

ISIN SENZA FONTE (decisione PM 05/10/2026). Nessuna fonte ufficiale gratuita AMMESSA lega il simbolo di
borsa all'ISIN: la directory Euronext e' esclusa dai suoi Terms of Use (vietano l'accesso automatico),
l'API BME (Spagna) ha robots `Disallow: /`. Quindi:
  - l'ISIN arriva SOLO dal chiamante (`isin`: es. negozio ISIN italiano per i .MI) e qui si CONFERMA:
    prefisso nazionale (mai XS/EU), GLEIF lo lega a un solo LEI attivo e ISSUED con nome coincidente;
  - senza ISIN dal chiamante: ISIN 'non_coperto' DICHIARATO (MOTIVO_ISIN_SENZA_FONTE) e LEI da GLEIF per
    nome LEGALE solo se: entita' unica nel paese del listino col nome base identico E con la STESSA forma
    societaria del nome confermato (se questo ne porta una), E nessun'altra entita' attiva con lo stesso
    nome base in un ALTRO paese (altrimenti ambiguo: misura RV-ID su due emittenti veri, la SE olandese
    contro una BV belga omonima e la SE olandese contro una SAS francese omonima, es. «Acme SE» NL
    contro «ACME» BV).
  Conseguenza: le fonti che vogliono l'ISIN (ue_fsma Belgio, ue_cnmv dall'ISIN) non partono senza ISIN;
  ue_amf parte dal LEI; ue_cnmv lavora anche dal solo nome (denominazione esatta).

REGOLE (pure in `decidi`, usata dal vivo e dalla riverifica senza rete): registrazione diversa da ISSUED
(o PENDING_*, dichiarata) o entita' non ACTIVE = mai 'ok' (LAPSED compresa: misura RV-ID, una N.V.
olandese fusa nel 2020 ancora ACTIVE+LAPSED); piu' entita' valide = ambiguo con la lista; nome diverso dai nomi
GLEIF del LEI legato all'ISIN = KO identita_incoerente; isin/lei dati solo con stato 'ok' (o 'STALE',
dichiarato). Cache 30 giorni per risposta, solo risposte che si leggono; guasto della fonte con cache
scaduta = STALE. Ricevuta: url_liste, sha256_liste, risposte_salvate, salvato_ts (eta' di ogni risposta)
-> `riverifica_identita`.
"""
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode, urlsplit

from bellomberg.core.paths import DATA_DIR
from bellomberg.market_data import borsa_italiana as _bi
from bellomberg.market_data.depositi_ue import isin_valido, paese_da_ticker
from bellomberg.market_data.ue_amf import lei_valido
from bellomberg.market_data.ue_cnmv import nome_canonico, norm_nome

FONTE_MODULO = "bellomberg.market_data.identita_ue"
VERSIONE_REGOLA = 2
USER_AGENT = _bi.USER_AGENT
TIMEOUT_S = 25
PAUSA_S = 2.0                         # pausa minima fra due richieste allo stesso host
TTL_S = 30 * 24 * 3600                # 30 giorni: ISIN e LEI cambiano di rado; oltre = STALE se la fonte e' giu'
CACHE_DIR = str(DATA_DIR / "cache_fonti_ue" / "identita")
GLEIF_PAGINA = 200                    # record per pagina nella ricerca per nome
GLEIF_MAX_PAGINE = 3                  # oltre: KO ricerca_troncata (l'univocita' non si puo' dire)

FONTE_GLEIF = "GLEIF (api.gleif.org, LEI CC0)"
URL_GLEIF = "https://api.gleif.org/api/v1/lei-records"
URL_ELF = "https://api.gleif.org/api/v1/entity-legal-forms/"
# host -> percorsi AMMESSI (regex sull'intero percorso). Nient'altro parte da questo modulo.
AMMESSI = {"api.gleif.org": (re.compile(r"^/api/v1/lei-records$"),
                             re.compile(r"^/api/v1/entity-legal-forms/[A-Z0-9]{4}$"))}
# codici paese di depositi_ue -> ISO 3166 di GLEIF
_PAESE_GLEIF = {"UK": "GB"}
# registrazioni LEI ammesse a un 'ok' (le PENDING_* dichiarate). Mai LAPSED/ANNULLED/DUPLICATE/RETIRED/MERGED.
REGISTRAZIONI_OK = frozenset({"ISSUED", "PENDING_TRANSFER", "PENDING_ARCHIVAL"})
# prefissi ISIN NON nazionali: XS (Euroclear/Clearstream, obbligazioni internazionali), EU (emissioni UE)
PREFISSI_NON_NAZIONALI = frozenset({"XS", "EU", "XA", "XB", "XC", "XD", "QS", "QT"})

# forme societarie -> sigla canonica. Tolte IN CODA dal nome (forma base) e confrontate fra loro.
# Mai parole come «holding»: un emittente e la sua holding sono emittenti diversi.
FORME_SOCIETARIE = {
    "societe europeenne": "se", "societas europaea": "se", "se": "se",
    "naamloze vennootschap": "nv", "nv": "nv",
    "societe anonyme": "sa", "sociedad anonima": "sa", "sociedade anonima": "sa", "sa": "sa",
    "public limited company": "plc", "plc": "plc",
    "aktiengesellschaft": "ag", "ag": "ag",
    "societa per azioni": "spa", "spa": "spa",
    "allmennaksjeselskap": "asa", "asa": "asa",
    "julkinen osakeyhtio": "oyj", "oyj": "oyj",
    "besloten vennootschap met beperkte aansprakelijkheid": "bv", "besloten vennootschap": "bv", "bv": "bv",
    "societe par actions simplifiee": "sas", "sas": "sas",
    "sociedad limitada": "sl", "sociedad de responsabilidad limitada": "sl", "sl": "sl",
    "societe a responsabilite limitee": "sarl", "sarl": "sarl",
    "gesellschaft mit beschrankter haftung": "gmbh", "gmbh": "gmbh",
    "societe en commandite par actions": "sca", "sca": "sca", "sapa": "sapa",
    "aktiebolag": "ab", "ab": "ab", "limited": "ltd", "ltd": "ltd",
}
_FORME = sorted(FORME_SOCIETARIE, key=lambda f: -len(f))

MOTIVO_ISIN_SENZA_FONTE = ("nessuna fonte ufficiale gratuita ammessa per ticker->ISIN (Euronext: termini d'uso; "
                           "BME: robots); l'ISIN va passato dal chiamante")
LIMITI_BASE = [
    "ISIN e LEI solo da GLEIF (mappatura ufficiale ISIN->LEI, ricerca per nome legale); mai dedotti dal simbolo",
    "nome confrontato in forma canonica senza forme societarie in coda (SA, N.V., SE, ...); la forma "
    "societaria, se il nome confermato la porta, deve coincidere con quella del record (nome legale o ELF)",
    "cache 30 giorni per risposta; un cambio di ISIN/LEI piu' recente non e' visto fino alla scadenza",
]
LIMITE_ISIN_CHIAMANTE = ("ISIN dato dal chiamante (%s) e confermato da GLEIF (ISIN->LEI con nome coincidente); il "
                         "legame simbolo->ISIN e la natura azionaria dell'ISIN non sono riverificati qui")
LIMITE_NOME_PAESE = ("LEI per nome legale: entita' unica nel paese del listino (sede legale GLEIF) e nessun'omonima "
                     "attiva altrove; un emittente con sede fuori dal paese del listino non e' trovato (mai indovinato)")
LIMITE_SENZA_FORMA = ("il nome confermato non porta una forma societaria: forma del record non verificata (resta "
                      "l'univocita' del nome in tutti i paesi)")


class URLVietato(Exception):
    pass


# ============================================================
# NOMI E FORME
# ============================================================
def nome_e_forme(s: Optional[str]) -> Tuple[str, Set[str]]:
    """(forma base, sigle delle forme societarie tolte IN CODA). Forma canonica di ue_cnmv.nome_canonico
    (accenti, punteggiatura, sigle puntate unite), poi via le forme in coda anche ripetute («SA/NV» ->
    {sa, nv}). Mai tolte in testa o in mezzo; un nome fatto SOLO di una forma resta com'e'."""
    t = nome_canonico(s)
    forme: Set[str] = set()
    cambiato = True
    while cambiato:
        cambiato = False
        for f in _FORME:
            if t.endswith(" " + f):
                t = t[: -len(f) - 1].strip()
                forme.add(FORME_SOCIETARIE[f])
                cambiato = True
                break
    return t, forme


def nome_base(s: Optional[str]) -> str:
    return nome_e_forme(s)[0]


def stesso_nome(a: Optional[str], b: Optional[str]) -> bool:
    x, y = nome_base(a), nome_base(b)
    return bool(x) and x == y


def forme_elf(nomi_elf: List[str]) -> Set[str]:
    """Sigle delle forme dai nomi ISO 20275 di un codice ELF (es. «société européenne» -> se)."""
    out = set()
    for n in nomi_elf:
        k = norm_nome(n)
        if k in FORME_SOCIETARIE:
            out.add(FORME_SOCIETARIE[k])
    return out


# ============================================================
# URL E RETE
# ============================================================
def url_ammesso(url: str) -> Tuple[bool, str]:
    p = urlsplit(url)
    if p.scheme != "https":
        return False, "URL non https"
    percorsi = AMMESSI.get((p.hostname or "").lower())
    if percorsi is None:
        return False, "host %s fuori dalle fonti ammesse" % p.hostname
    if not any(r.match(p.path) for r in percorsi):
        return False, "percorso %s non ammesso su %s" % (p.path, p.hostname)
    return True, ""


def url_gleif_isin(isin: str) -> str:
    return URL_GLEIF + "?" + urlencode([("filter[isin]", isin)])


def url_gleif_nome(nome: str, pagina: int) -> str:
    """Ricerca per nome in TUTTI i paesi: il filtro del paese si applica in locale."""
    return URL_GLEIF + "?" + urlencode([("filter[entity.names]", nome), ("page[size]", str(GLEIF_PAGINA)),
                                        ("page[number]", str(pagina))])


def url_elf(codice: str) -> str:
    return URL_ELF + codice


_ultima_richiesta: Dict[str, float] = {}


def _scarica(url: str) -> Tuple[int, bytes]:
    """GET senza redirect (un redirect torna come HTTP 3xx = KO), User-Agent del modulo, pausa per host."""
    ok, motivo = url_ammesso(url)
    if not ok:
        raise URLVietato(motivo)
    import requests
    host = urlsplit(url).hostname
    attesa = PAUSA_S - (time.monotonic() - _ultima_richiesta.get(host, -1e9))
    if attesa > 0:
        time.sleep(attesa)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.api+json"},
                         timeout=TIMEOUT_S, allow_redirects=False)
    finally:
        _ultima_richiesta[host] = time.monotonic()
    return r.status_code, r.content


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")


def _cache_leggi(url: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_path(url), encoding="utf-8") as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(c, dict) and c.get("url") == url and isinstance(c.get("corpo"), str) and \
            isinstance(c.get("salvato_ts"), (int, float)) and not isinstance(c.get("salvato_ts"), bool):
        return c
    return None


def _cache_scrivi(url: str, corpo: str, ts: float) -> None:
    """Temporaneo + os.replace: un file a meta' non si serve mai."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"url": url, "salvato_ts": ts, "corpo": corpo}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(url))
    except OSError:
        pass  # la cache e' un'ottimizzazione: senza, la prossima chiamata rilegge la fonte


class _Lettore:
    """Legge una risposta (cache fresca -> rete -> cache scaduta = STALE) e la registra per la ricevuta col
    suo istante (salvato_ts). Ritorna (corpo str | None, errore | None, motivo | None). In cache va SOLO una
    risposta che il suo parser legge (review RV-ID P3: un 200 malformato non resta 30 giorni). L'eta' e' su
    orologio di parete (persistente fra processi): un'eta' negativa vale come scaduta."""

    def __init__(self):
        self.risposte: Dict[str, str] = {}
        self.salvato_ts: Dict[str, float] = {}
        self.stale: List[str] = []
        self.da_rete = 0

    def __call__(self, url: str, parser: Callable) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if url in self.risposte:
            return self.risposte[url], None, None
        c = _cache_leggi(url)
        if c is not None and 0 <= time.time() - c["salvato_ts"] < TTL_S:
            self.risposte[url], self.salvato_ts[url] = c["corpo"], c["salvato_ts"]
            return c["corpo"], None, None
        try:
            http, corpo = _scarica(url)
            if http != 200:
                errore, motivo = "http", "HTTP %s da GLEIF" % http
            else:
                testo = corpo.decode("utf-8")
                ts = time.time()
                self.da_rete += 1
                self.risposte[url], self.salvato_ts[url] = testo, ts
                if parser(testo)["stato"] == "ok":
                    _cache_scrivi(url, testo, ts)
                return testo, None, None
        except URLVietato as e:
            return None, "url_vietato", "URL rifiutato prima della rete: %s" % e.args[0]
        except UnicodeDecodeError:
            errore, motivo = "formato_cambiato", "risposta GLEIF non UTF-8"
        except Exception as e:
            errore, motivo = "rete", "richiesta a GLEIF fallita (%s)" % type(e).__name__
        if c is not None:
            # guasto della fonte, ma c'e' una risposta scaduta: si usa DICHIARATA (STALE)
            self.stale.append("%s (%s): usata la risposta in cache del %s, scaduta"
                              % (url, motivo, datetime.fromtimestamp(c["salvato_ts"], timezone.utc)
                                 .isoformat(timespec="seconds")))
            self.risposte[url], self.salvato_ts[url] = c["corpo"], c["salvato_ts"]
            return c["corpo"], None, None
        return None, errore, motivo


# ============================================================
# PARSER (puri)
# ============================================================
def parse_gleif(corpo: str) -> Dict[str, Any]:
    """{"stato": ok|KO, "errore", "motivo", "record": [{lei, nome_legale, altri_nomi, paese, elf,
    stato_entita, stato_registrazione}], "totale"}. Forma misurata: JSON:API {"meta": {"pagination":
    {"total"}}, "data": []}."""
    ko = {"stato": "KO", "errore": "formato_cambiato", "record": [], "totale": None}
    try:
        d = json.loads(corpo)
        totale = d["meta"]["pagination"]["total"]
        dati = d["data"]
    except (ValueError, KeyError, TypeError):
        return dict(ko, motivo="risposta GLEIF senza meta.pagination.total e data: formato cambiato")
    if not isinstance(totale, int) or isinstance(totale, bool) or not isinstance(dati, list):
        return dict(ko, motivo="risposta GLEIF con total/data di tipo inatteso")
    rec = []
    for x in dati:
        try:
            a = x["attributes"]
            e = a["entity"]
            lei = a["lei"]
            nome = e["legalName"]["name"]
            altri = [n.get("name") for n in (e.get("otherNames") or []) + (e.get("transliteratedOtherNames") or [])
                     if isinstance(n, dict) and isinstance(n.get("name"), str)]
            elf = (e.get("legalForm") or {}).get("id")
            r = {"lei": lei, "nome_legale": nome, "altri_nomi": altri, "paese": e["legalAddress"]["country"],
                 "elf": elf if isinstance(elf, str) else None,
                 "stato_entita": e.get("status"), "stato_registrazione": (a.get("registration") or {}).get("status")}
        except (KeyError, TypeError, AttributeError):
            return dict(ko, totale=totale, motivo="record GLEIF senza lei/legalName/legalAddress: formato cambiato")
        if x.get("id") != lei or not lei_valido(lei) or not isinstance(nome, str):
            return dict(ko, totale=totale, motivo="record GLEIF con LEI non valido (ISO 17442) o diverso dall'id")
        rec.append(r)
    return {"stato": "ok", "errore": None, "motivo": None, "record": rec, "totale": totale}


def parse_elf(corpo: str) -> Dict[str, Any]:
    """{"stato": ok|KO, "errore", "motivo", "codice", "nomi": [str]} da entity-legal-forms/<codice>."""
    try:
        a = json.loads(corpo)["data"]["attributes"]
        nomi = [n.get("localName") for n in a["names"] if isinstance(n, dict)] + \
               [n.get("transliteratedName") for n in a["names"] if isinstance(n, dict)]
        codice = a["code"]
    except (ValueError, KeyError, TypeError):
        return {"stato": "KO", "errore": "formato_cambiato", "codice": None, "nomi": [],
                "motivo": "risposta ELF GLEIF senza data.attributes.code/names: formato cambiato"}
    return {"stato": "ok", "errore": None, "motivo": None, "codice": codice,
            "nomi": [n for n in nomi if isinstance(n, str) and n]}


def _cand(r: Dict[str, Any], motivo: Optional[str] = None) -> Dict[str, Any]:
    c = {"lei": r["lei"], "nome": r["nome_legale"], "paese": r["paese"], "elf": r["elf"],
         "stato_entita": r["stato_entita"], "stato_registrazione": r["stato_registrazione"]}
    if motivo:
        c["motivo"] = motivo
    return c


def _non_ammessa(r: Dict[str, Any]) -> Optional[str]:
    """Motivo per cui il record non puo' dare un 'ok' (entita' non ACTIVE o registrazione non ammessa)."""
    if r["stato_entita"] != "ACTIVE":
        return "entita' %s (non ACTIVE)" % r["stato_entita"]
    if r["stato_registrazione"] not in REGISTRAZIONI_OK:
        return "registrazione LEI %s (ammesse: %s)" % (r["stato_registrazione"], ", ".join(sorted(REGISTRAZIONI_OK)))
    return None


# ============================================================
# DECISIONE (pura: `leggi(url, parser)` -> (corpo|None, errore|None, motivo|None))
# ============================================================
def _ko(err, motivo) -> Dict[str, Any]:
    return {"stato": "KO", "errore": err, "motivo": motivo, "lei": None, "record": None}


def _lei_da_isin(leggi: Callable, isin: str) -> Dict[str, Any]:
    """Il LEI a cui GLEIF lega l'ISIN: {stato ok|non_trovato|ambiguo|KO, errore, motivo, lei, record}."""
    corpo, err, mot = leggi(url_gleif_isin(isin), parse_gleif)
    if corpo is None:
        return _ko(err, "GLEIF ISIN %s: %s" % (isin, mot))
    p = parse_gleif(corpo)
    if p["stato"] != "ok":
        return _ko(p["errore"], p["motivo"])
    if p["totale"] != len(p["record"]):
        return _ko("ricerca_troncata", "GLEIF dichiara %d LEI per l'ISIN %s ma ne ha mandati %d"
                   % (p["totale"], isin, len(p["record"])))
    if not p["record"]:
        return {"stato": "non_trovato", "errore": None, "lei": None, "record": None,
                "motivo": "GLEIF non lega l'ISIN %s a nessun LEI" % isin}
    if len(p["record"]) > 1:
        return {"stato": "ambiguo", "errore": None, "lei": None, "record": None,
                "candidati": [_cand(r) for r in p["record"]],
                "motivo": "GLEIF lega l'ISIN %s a %d LEI" % (isin, len(p["record"]))}
    r = p["record"][0]
    no = _non_ammessa(r)
    if no:
        return {"stato": "non_trovato", "errore": None, "lei": None, "record": None, "candidati": [_cand(r, no)],
                "motivo": "GLEIF lega l'ISIN %s al LEI %s (%s), ma %s: nessun LEI valido" % (isin, r["lei"], r["nome_legale"], no)}
    return {"stato": "ok", "errore": None, "lei": r["lei"], "record": r,
            "motivo": "GLEIF lega l'ISIN %s al LEI %s (%s)" % (isin, r["lei"], r["nome_legale"])}


def _forme_record(leggi: Callable, r: Dict[str, Any]) -> Tuple[Optional[Set[str]], Optional[Dict[str, Any]], str]:
    """(sigle della forma del record | None se illeggibile, KO della fonte | None, da dove). Prima dal nome
    legale in coda, poi dal codice ELF (una richiesta, in cache)."""
    _, f = nome_e_forme(r["nome_legale"])
    if f:
        return f, None, "nome legale"
    if not r["elf"] or not re.fullmatch(r"[A-Z0-9]{4}", r["elf"]):
        return None, None, "nessun codice ELF"
    corpo, err, mot = leggi(url_elf(r["elf"]), parse_elf)
    if corpo is None:
        return None, _ko(err, "GLEIF ELF %s: %s" % (r["elf"], mot)), "ELF"
    p = parse_elf(corpo)
    if p["stato"] != "ok" or p["codice"] != r["elf"]:
        return None, _ko("formato_cambiato", p["motivo"] or "ELF %s: codice diverso nella risposta" % r["elf"]), "ELF"
    return forme_elf(p["nomi"]), None, "ELF %s %s" % (r["elf"], p["nomi"][:2])


def _lei_da_nome(leggi: Callable, nome: str, paese: str) -> Dict[str, Any]:
    """LEI per nome LEGALE (non commerciale/precedente: review RV-ID R3). Ricerca in tutti i paesi; ok solo
    se UN'entita' del paese del listino ha nome base identico e forma compatibile, nessun'altra entita'
    ATTIVA con lo stesso nome base sta in un altro paese, e la registrazione e' ammessa."""
    base, forme_nome = nome_e_forme(nome)
    stessi: List[Dict[str, Any]] = []
    letti = 0
    for pagina in range(1, GLEIF_MAX_PAGINE + 1):
        corpo, err, mot = leggi(url_gleif_nome(base, pagina), parse_gleif)
        if corpo is None:
            return _ko(err, "GLEIF nome %r: %s" % (base, mot))
        p = parse_gleif(corpo)
        if p["stato"] != "ok":
            return _ko(p["errore"], p["motivo"])
        letti += len(p["record"])
        stessi += [r for r in p["record"] if nome_base(r["nome_legale"]) == base]
        if letti >= p["totale"]:
            break
        if not p["record"]:
            return _ko("ricerca_troncata", "GLEIF dichiara %d entita' per %r ma ne ha mandate %d" % (p["totale"], base, letti))
    else:
        return _ko("ricerca_troncata", "piu' di %d entita' GLEIF per %r: l'univocita' del nome non si puo' dire"
                   % (GLEIF_PAGINA * GLEIF_MAX_PAGINE, base))
    altrove = [r for r in stessi if r["paese"] != paese and r["stato_entita"] == "ACTIVE"]
    qui = [r for r in stessi if r["paese"] == paese]
    scartati, validi = [], []
    for r in qui:
        if forme_nome:
            fr, ko, da = _forme_record(leggi, r)
            if ko:
                return ko
            if not fr or not (fr & forme_nome):
                scartati.append(_cand(r, "forma societaria %s (%s) diversa da %s del nome confermato"
                                      % (sorted(fr) if fr else "non leggibile", da, sorted(forme_nome))))
                continue
        validi.append(r)
    attivi = [r for r in validi if r["stato_entita"] == "ACTIVE"]
    scartati += [_cand(r, "entita' %s" % r["stato_entita"]) for r in validi if r["stato_entita"] != "ACTIVE"]
    if altrove and attivi:
        return {"stato": "ambiguo", "errore": None, "lei": None, "record": None, "scartati": scartati,
                "candidati": [_cand(r) for r in attivi + altrove],
                "motivo": "%d entita' attive col nome base %r fuori da %s (%s): l'emittente non e' univoco per nome"
                          % (len(altrove), base, paese, ", ".join("%s %s" % (r["nome_legale"], r["paese"]) for r in altrove[:5]))}
    if not attivi:
        return {"stato": "non_trovato", "errore": None, "lei": None, "record": None, "scartati": scartati,
                "candidati": [_cand(r, "fuori dal paese del listino %s" % paese) for r in altrove],
                "motivo": "nessuna entita' GLEIF attiva in %s col nome legale %r e forma compatibile (%d lette, %d "
                          "scartate%s)" % (paese, nome, letti, len(scartati),
                                           "; omonime fuori paese: " + ", ".join("%s %s" % (r["nome_legale"], r["paese"])
                                                                                for r in altrove[:5]) if altrove else "")}
    if len(attivi) > 1:
        return {"stato": "ambiguo", "errore": None, "lei": None, "record": None, "scartati": scartati,
                "candidati": [_cand(r) for r in attivi],
                "motivo": "%d entita' GLEIF in %s col nome legale %r" % (len(attivi), paese, nome)}
    r = attivi[0]
    no = _non_ammessa(r)
    if no:
        return {"stato": "non_trovato", "errore": None, "lei": None, "record": None, "scartati": scartati,
                "candidati": [_cand(r, no)],
                "motivo": "unica entita' in %s col nome %r (LEI %s), ma %s: nessun LEI valido" % (paese, r["nome_legale"], r["lei"], no)}
    return {"stato": "ok", "errore": None, "lei": r["lei"], "record": r, "scartati": scartati,
            "motivo": "GLEIF: unica entita' attiva in %s col nome legale %r e nessun'omonima altrove -> LEI %s"
                      % (paese, r["nome_legale"], r["lei"])}


def decidi(ticker: str, nome: str, isin: Optional[str], leggi: Callable, *, fonte_isin: str) -> Dict[str, Any]:
    """Verdetto PURO dati i corpi che `leggi` restituisce. {stato, errore, motivo, isin, lei, nome_ufficiale,
    fonte_isin, fonte_lei, paese_isin, verifica: {isin, lei, nome}, limiti}."""
    out = {"stato": "KO", "errore": None, "motivo": None, "isin": None, "lei": None, "nome_ufficiale": None,
           "fonte_isin": None, "fonte_lei": None, "paese_isin": None,
           "verifica": {"isin": None, "lei": None, "nome": None}, "limiti": list(LIMITI_BASE)}
    t = (ticker or "").strip().upper()
    simbolo = t.rsplit(".", 1)[0] if "." in t else t
    if not t:
        out.update(errore="parametro", motivo="ticker assente")
        return out
    if not (nome or "").strip() or nome_base(nome) == nome_base(simbolo):
        out.update(errore="identita_mancante",
                   motivo="nome dell'emittente assente o uguale al simbolo: serve il nome confermato, mai il ticker")
        return out
    if isin is not None and not isin_valido(isin):
        out.update(errore="parametro", motivo="ISIN %r non valido (ISO 6166, cifra di controllo)" % isin)
        return out
    isin = isin.strip().upper() if isin else None
    if isin and isin[:2] in PREFISSI_NON_NAZIONALI:
        out.update(errore="parametro",
                   motivo="ISIN %s con prefisso non nazionale %s (obbligazioni internazionali/emissioni UE): non e' "
                          "l'ISIN di un'azione quotata" % (isin, isin[:2]))
        return out

    if isin:
        v_isin = {"stato": "dato", "fonte": fonte_isin, "motivo": "ISIN %s dato dal chiamante (%s)" % (isin, fonte_isin)}
        v_lei = dict(_lei_da_isin(leggi, isin), via="isin")
    else:
        v_isin = {"stato": "non_coperto", "fonte": None, "motivo": MOTIVO_ISIN_SENZA_FONTE}
        paese, motivo_paese = paese_da_ticker(t)
        if paese:
            out["limiti"].append(LIMITE_NOME_PAESE)
            if not nome_e_forme(nome)[1]:
                out["limiti"].append(LIMITE_SENZA_FORMA)
            v_lei = dict(_lei_da_nome(leggi, nome, _PAESE_GLEIF.get(paese, paese)), via="nome_legale")
        else:
            v_lei = {"stato": "non_coperto", "errore": None, "lei": None, "record": None, "via": None,
                     "motivo": "paese del listino non determinato: %s" % motivo_paese}
    rec = v_lei.get("record")
    # ramo ISIN: l'ISIN lega gia' l'emittente, valgono anche gli altri nomi GLEIF; ramo nome: solo il legale
    ufficiali = ([rec["nome_legale"]] + (list(rec["altri_nomi"]) if isin else [])) if rec else []
    coincide = [n for n in ufficiali if stesso_nome(nome, n)]
    out["verifica"] = {"isin": v_isin,
                       "lei": dict({k: v for k, v in v_lei.items() if k != "record"}, fonte=FONTE_GLEIF,
                                   stato_registrazione=(rec or {}).get("stato_registrazione")),
                       "nome": {"nome": nome, "forma_base": nome_base(nome), "ufficiali_gleif": ufficiali,
                                "coincide_con": coincide}}

    if v_lei["stato"] == "KO":
        out.update(stato="KO", errore=v_lei["errore"], motivo=v_lei["motivo"])
        return out
    if v_lei["stato"] == "ok" and not coincide:
        out.update(stato="KO", errore="identita_incoerente",
                   motivo="nome %r diverso dai nomi GLEIF del LEI %s legato %s: %s"
                          % (nome, v_lei["lei"], "all'ISIN %s" % isin if isin else "al nome",
                             "; ".join(repr(n) for n in ufficiali)))
        return out
    if v_lei["stato"] == "ambiguo":
        out.update(stato="ambiguo", motivo=v_lei["motivo"])
        return out
    if v_lei["stato"] == "ok":
        out.update(stato="ok", lei=rec["lei"], fonte_lei=FONTE_GLEIF, nome_ufficiale=rec["nome_legale"], motivo=v_lei["motivo"])
        if isin:
            out.update(isin=isin, fonte_isin=fonte_isin, paese_isin=isin[:2])
            out["limiti"].append(LIMITE_ISIN_CHIAMANTE % fonte_isin)
        else:
            out["limiti"].append("ISIN non dato: %s" % MOTIVO_ISIN_SENZA_FONTE)
        if rec.get("stato_registrazione") != "ISSUED":
            out["limiti"].append("registrazione LEI %s: %s (in corso di trasferimento/archiviazione)"
                                 % (rec["lei"], rec.get("stato_registrazione")))
        return out
    if v_lei["stato"] == "non_trovato":
        out.update(stato="non_trovato",
                   motivo=("ISIN %s del chiamante non confermato: %s" % (isin, v_lei["motivo"])) if isin
                   else "ISIN: %s; LEI: %s" % (MOTIVO_ISIN_SENZA_FONTE, v_lei["motivo"]))
        return out
    out.update(stato="non_coperto", motivo="ISIN: %s; LEI: %s" % (MOTIVO_ISIN_SENZA_FONTE, v_lei["motivo"]))
    return out


# ============================================================
# FUNZIONI PUBBLICHE
# ============================================================
def _sha(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def risolvi_identita_ue(ticker: str, *, nome: Optional[str], isin: Optional[str] = None,
                        fonte_isin: str = "negozio ISIN italiano (chiamante)") -> Dict[str, Any]:
    """ISIN e LEI verificati dell'emittente di `ticker` (simbolo Yahoo con suffisso) col NOME confermato.
    `isin`: ISIN gia' noto al chiamante (es. negozio ISIN italiano per i .MI), da CONFERMARE con GLEIF;
    `fonte_isin`: da dove viene (entra nel ritorno e nei limiti).
    Stati: ok | ambiguo | non_trovato | KO | non_coperto | STALE. isin/lei si usano SOLO con stato 'ok'
    (con 'ok' il LEI c'e' sempre; l'ISIN c'e' solo se dato dal chiamante). STALE = calcolato da risposte in
    cache scadute perche' GLEIF non risponde: 'stato_originale' dice il verdetto, i limiti lo dichiarano.
    Ricevuta: url_liste, sha256_liste, risposte_salvate, salvato_ts (riverifica: `riverifica_identita`)."""
    leggi = _Lettore()
    out = decidi(ticker, nome or "", isin, leggi, fonte_isin=fonte_isin)
    out.update(ticker=(ticker or "").strip().upper(), nome=nome, isin_chiamante=isin, fonte_isin_chiamante=fonte_isin,
               regola=VERSIONE_REGOLA, url_liste=list(leggi.risposte), risposte_salvate=dict(leggi.risposte),
               sha256_liste={k: _sha(v) for k, v in leggi.risposte.items()}, salvato_ts=dict(leggi.salvato_ts),
               fonte_modulo=FONTE_MODULO, letto_il=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               stato_originale=None,
               cache=("scaduta" if leggi.stale else ("fresca" if leggi.risposte and not leggi.da_rete else None)))
    if leggi.stale and out["stato"] != "KO":
        out["stato_originale"] = out["stato"]
        out["stato"] = "STALE"
        out["limiti"] = out["limiti"] + ["STALE: " + s for s in leggi.stale]
    return out


def riverifica_identita(ricevuta: Any, *, ticker: str, nome: Optional[str],
                        isin: Optional[str] = None) -> Tuple[bool, str]:
    """Senza rete: sha256 delle risposte salvate = ricevuta; eta' sigillata di ogni risposta coerente con lo
    stato (oltre il TTL a letto_il = deve essere STALE); poi la decisione ricalcolata dalle SOLE risposte
    salvate deve dare stesso stato, ISIN, LEI, nome ufficiale e fonti, e usare tutte le risposte."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta assente"
    if ricevuta.get("fonte_modulo") != FONTE_MODULO:
        return False, "fonte_modulo %r non e' %s" % (ricevuta.get("fonte_modulo"), FONTE_MODULO)
    if ricevuta.get("regola") != VERSIONE_REGOLA:
        return False, "regola %r della ricevuta diversa da quella del modulo (%d)" % (ricevuta.get("regola"), VERSIONE_REGOLA)
    risposte, sha, liste = ricevuta.get("risposte_salvate"), ricevuta.get("sha256_liste"), ricevuta.get("url_liste")
    ts = ricevuta.get("salvato_ts")
    if not isinstance(risposte, dict) or not isinstance(sha, dict) or not isinstance(liste, list) or not isinstance(ts, dict):
        return False, "risposte_salvate / sha256_liste / url_liste / salvato_ts assenti"
    if set(risposte) != set(sha) or set(liste) != set(risposte) or len(liste) != len(risposte) or set(ts) != set(risposte):
        return False, "risposte salvate, sha, url_liste e salvato_ts non coincidono"
    for k, v in risposte.items():
        if not isinstance(v, str) or _sha(v) != sha[k]:
            return False, "sha256 della risposta salvata %s diverso dalla ricevuta" % k
    try:
        letto = datetime.fromisoformat(ricevuta.get("letto_il")).timestamp()
    except (TypeError, ValueError):
        return False, "letto_il illeggibile"
    scadute = [k for k, v in ts.items() if isinstance(v, bool) or not isinstance(v, (int, float))
               or not 0 <= letto - v + 1 < TTL_S]
    if scadute and ricevuta.get("stato") not in ("STALE", "KO"):
        return False, "%d risposte oltre il TTL (o con eta' illeggibile) alla lettura: la ricevuta doveva essere STALE" % len(scadute)
    if ricevuta.get("stato") == "STALE" and not scadute:
        return False, "ricevuta STALE senza risposte scadute"
    if ricevuta.get("ticker") != (ticker or "").strip().upper() or ricevuta.get("nome") != nome or \
            ricevuta.get("isin_chiamante") != isin:
        return False, "ticker/nome/ISIN della ricevuta diversi da quelli chiesti"
    usate: List[str] = []

    def _leggi(url, parser):
        usate.append(url)
        if url in risposte:
            return risposte[url], None, None
        return None, "assente", "risposta %s non salvata nella ricevuta" % url

    r = decidi(ticker, nome or "", isin, _leggi, fonte_isin=ricevuta.get("fonte_isin_chiamante") or "")
    stato = ricevuta.get("stato_originale") if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    if r["stato"] != stato:
        return False, "stato ricalcolato %r diverso dalla ricevuta %r (%s)" % (r["stato"], stato, r["motivo"])
    for k in ("isin", "lei", "nome_ufficiale", "fonte_isin", "fonte_lei", "paese_isin"):
        if ricevuta.get(k) != r[k]:
            return False, "%s ricalcolato %r diverso dalla ricevuta %r" % (k, r[k], ricevuta.get(k))
    if set(usate) != set(risposte):
        return False, "la ricevuta contiene risposte non usate dalla decisione: %s" % sorted(set(risposte) - set(usate))
    return True, "identita' riverificata senza rete (%s, %d risposte)" % (r["stato"], len(risposte))
