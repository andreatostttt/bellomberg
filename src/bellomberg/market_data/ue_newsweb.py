# -*- coding: utf-8 -*-
"""ue_newsweb.py — data di DIFFUSIONE delle relazioni finanziarie degli emittenti norvegesi
(.OL) da Oslo Børs NewsWeb, l'OAM norvegese (handoff-3, contratto INTERFACCIA_UE, 05/10/2026,
Opus 5.5).

FONTE DICHIARATA. NewsWeb e' il meccanismo ufficiale di stoccaggio (OAM) della Norvegia. Il
modulo usa l'API JSON che il sito pubblico chiama dal browser,
`POST https://api3.oslo.oslobors.no/v1/newsreader/list?category=<id>&issuer=<sigla>&fromDate=&toDate=`:
**API NON DOCUMENTATA** (decisione PM 05/10: si' con cautela). Nessun robots.txt sull'host
(404, misurato 05/10), termini d'uso non trovati. Conseguenze dichiarate: un cambio di formato
da' stato KO 'formato_cambiato' (mai una lista vuota zitta); pausa minima PAUSA_S fra due
richieste allo stesso host; host e percorso ammessi in PERCORSI_AMMESSI.

MISURE (ricognizione U1, 05/10/2026, un emittente del listino di Oslo):
  - risposta: {"header": {"result.val": 0, ...}, "data": {"messages": [...], "overflow": bool}};
    ogni messaggio ha id/messageId, title, category [{id, category_en}], issuerSign,
    issuerName, publishedTime ('...Z', UTC al millisecondo), correctionForMessageId,
    correctedByMessageId, numbAttachments. NIENTE ISIN nella risposta.
  - categorie: 1001 ANNUAL FINANCIAL REPORT, 1002 HALF YEAR FINANCIAL REPORT.
  - TRAPPOLA: l'emittente mette anche Q1, Q3 e Q4 (risultati dell'anno) sotto «HALF YEAR».
    La categoria NON e' una prova: si decide col NOME del documento + il PERIODO nel titolo.
  - ogni relazione esce in coppia NORVEGESE/INGLESE a pochi secondi di distanza: e' UN
    deposito; la lingua scelta e' l'inglese (se c'e'), l'altra va fra le `conferme`.
  - le rettifiche sono messaggi nuovi («Correction: ...», correctionForMessageId != 0).

CONTRATTO (INTERFACCIA_UE.md):
    get_data_deposito(ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None) -> dict
    riverifica_ricevuta(ricevuta, *, ticker, tipo, periodo_fine) -> (ok, motivo)
  - la SIGLA di borsa e' il ticker senza «.OL» (dichiarato); l'IDENTITA' la conferma il NOME
    passato dal chiamante contro `issuerName` di OGNI messaggio letto (e `issuerSign` contro la
    sigla): diversi -> KO 'identita_incoerente'. Senza nome -> KO 'identita_mancante', senza
    rete (l'ISIN non e' nella risposta: da solo non conferma nulla).
"""
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bellomberg.core.paths import DATA_DIR
from bellomberg.market_data import borsa_italiana as _bi
from bellomberg.market_data import emarket_sdir as _em

FONTE = "Oslo Børs NewsWeb (OAM Norvegia) - API del sito, non documentata"
FONTE_MODULO = "bellomberg.market_data.ue_newsweb"
PAESE = "NO"
HOST = "api3.oslo.oslobors.no"
URL_LISTA = "https://" + HOST + "/v1/newsreader/list"
URL_PAGINA = "https://newsweb.oslobors.no/message/{id}"     # pagina pubblica (SPA) del messaggio
PERCORSI_AMMESSI = {HOST: ("/v1/newsreader/list",)}
CATEGORIE = (1001, 1002)      # ANNUAL / HALF YEAR FINANCIAL REPORT: entrambe per OGNI tipo (trappola)
PAUSA_S = 2.0                 # pausa minima fra due richieste a HOST (API non documentata: ritmo lento)
TIMEOUT_S = 25
TTL_S = 12 * 3600
FINESTRA_GIORNI = 200         # si cerca dal giorno dopo la fine del periodo a +200 giorni (termini 3-4 mesi)
CACHE_DIR = str(DATA_DIR / "cache_fonti_ue")
USER_AGENT = _bi.USER_AGENT
_TIPI = ("annuale", "semestrale", "trimestrale")

_ultima_richiesta: Dict[str, float] = {}     # host -> time.monotonic() dell'ultima richiesta
_dormi = time.sleep                          # punto di sostituzione per i test

LIMITI_FISSI = [
    "API del sito NewsWeb NON documentata: puo' cambiare senza preavviso (allora stato KO)",
    "data e ora = publishedTime di NewsWeb (UTC) convertito nel fuso Europe/Oslo (= Roma); l'istante UTC "
    "esatto sta in candidati[].pubblicato",
    "categorie cercate: 1001 ANNUAL e 1002 HALF YEAR FINANCIAL REPORT; non_trovato = non trovato in "
    "queste categorie fra le date cercate, non «mai pubblicato»",
    "legame col documento per NOME + PERIODO nel titolo (la categoria la sceglie l'emittente); "
    "PDF allegati non scaricati (url_documento None)",
    "identita' confermata per sigla (ticker senza .OL) + nome contro issuerName; l'ISIN non e' "
    "nella risposta NewsWeb",
]


# ============================================================
# REGOLE NOME + PERIODO (norvegese + inglese)
# ============================================================
_NO_DOC = {
    "annuale": r"(?<![^\W\d_])års(?:rapport|beretning|regnskap)",   # non dentro «halvårsrapport» (RV-UE1 NO-2)
    "semestrale": r"halvårs(?:rapport|regnskap|beretning)|delårsrapport(?=\D{0,25}30\.?\s+juni)",
    "trimestrale": r"kvartalsrapport",
}
# AGGIUNTA 2 (INTERFACCIA_UE): nomi PUBBLICI dei documenti nelle lingue della fonte (inglese e
# norvegese), regex in testo da compilare con re.I; li legge depositi_ue.nomi_documento.
# Trimestrale: in Norvegia il titolo e' «first quarter 2026 results» / «resultater for forste
# kvartal»: il nome E' il trimestre. Il secondo trimestre come semestrale NON sta qui: vale solo
# con la categoria 1002 (regola in `scegli`).
NOMI_DOCUMENTO = {
    # «semi-annual report» contiene «annual report» (RV-UE1 NO-2): escluso col lookbehind
    "annuale": [r"(?<!semi-)(?<!semi\s)(?<!semi)(?:%s)" % _em.DOCUMENTI_DEPOSITO["annuale"][1], _NO_DOC["annuale"]],
    "semestrale": [_em.DOCUMENTI_DEPOSITO["semestrale"][1], _NO_DOC["semestrale"]],
    "trimestrale": [_em.DOCUMENTI_DEPOSITO["trimestrale"][1], _NO_DOC["trimestrale"],
                    # trimestre + «results/report» (RV-UE1 P3: «Q1» nudo riconosceva anche le presentazioni)
                    r"\b(?:first|third)\s+quarter(?:\s+(?:of\s+)?\d{4})?\s+(?:results|report)\b|"
                    r"\b(?:Q[13]|[13]Q)\s*\d{4}\s+(?:results|report)\b|"
                    r"\bresultater\s+for\s+(?:første|tredje|[13]\.)\s*kvartal\b"],
}
# risultati del trimestre: «first quarter 2026 results», «resultater for første kvartal 2026»
_RISULTATI = re.compile(r"\bresults?\b|\breport\b|\bresultat(?:er)?\b|\brapport\b|\bstatement\b", re.I)
# annunci che CITANO la relazione ma non lo sono (inviti, presentazioni, webcast)
_NON_DOC = re.compile(r"\binvitation\b|\binvitasjon\b|\bwebcast\b|conference\s+call|\bpresentasjon\b|"
                      r"\bpresentation\b|\breminder\b|\bpåminnelse\b|\bnotice\s+of\b", re.I)
_ANNO = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# AGGIUNTA 05/10 (INTERFACCIA_UE): titolo col nome ma SENZA alcun anno -> prova 'finestra' solo
# entro questi giorni dalla fine del periodo
FINESTRE_SENZA_ANNO = {"annuale": 200, "semestrale": 150, "trimestrale": 120}
_NORVEGESE = re.compile(r"kvartal|resultater|rapport|halvår|(?<![^\W\d_])års|\bpublisert\b", re.I)   # RV-UE1 NO-3
# RV-UE1 NO-1: l'altra lingua e' lo STESSO deposito solo se esce entro questi minuti
CONFERMA_MAX_MINUTI = 60
_TRIMESTRI = {3: (r"first", r"første", "1"), 6: (r"second", r"andre", "2"), 9: (r"third", r"tredje", "3")}


def _rx_trimestre(mese: int, anno: int) -> str:
    en, no, n = _TRIMESTRI[mese]
    return (r"\b%s\s+quarter\s+(?:of\s+)?%d\b|\b(?:Q%s|%sQ)\s*%d\b|\b%s\s+kvartal\s+%d\b|\b%s\.\s*kvartal\s+%d\b|"
            r"\b%s\s+kvartal\D{0,6}%d\b" % (en, anno, n, n, anno, no, anno, n, anno, no, anno))


def _regole(tipo: str, fine: date) -> Dict[str, Any]:
    """{'nome': regex del nome del documento, 'periodo': regex del periodo, 'q2': regex del
    secondo trimestre (solo semestrale al 30/06) | None}."""
    nome = "|".join(NOMI_DOCUMENTO[tipo][:2])      # i nomi del documento (il trimestre col suo anno: sotto)
    a, m = fine.year, fine.month
    alt = [_em._regex_periodo(fine, tipo).pattern,
           r"\b%d\.?\s+%s\s+%d\b" % (fine.day, ("januar", "februar", "mars", "april", "mai", "juni", "juli",
                                                 "august", "september", "oktober", "november",
                                                 "desember")[m - 1], a)]
    q2 = None
    if tipo == "annuale":
        alt += [r"(?:%s)\D{0,15}%d\b" % (_NO_DOC["annuale"], a), r"\b%d\s*[-–]?\s*(?:%s)" % (a, _NO_DOC["annuale"])]
    elif tipo == "semestrale" and m == 6:
        alt += [r"(?:første|1\.)\s+halvår\s+%d\b" % a, r"(?:%s)\D{0,15}%d\b" % (nome, a)]
        q2 = _rx_trimestre(6, a)
    elif tipo == "trimestrale":
        alt += [_rx_trimestre(m, a)]
        nome = nome + "|" + _rx_trimestre(m, a)      # «first quarter 2026 results»: il periodo E' il nome
    return {"nome": re.compile(nome, re.I), "periodo": re.compile("|".join(alt), re.I),
            "nome_puro": re.compile("|".join(NOMI_DOCUMENTO[tipo][:2]), re.I),
            "q2": re.compile(q2, re.I) if q2 else None}


# ============================================================
# PARSER (puro) e IDENTITA'
# ============================================================
FUSO = "Europe/Oslo"


def _fuso_oslo():
    """ZoneInfo di Oslo; senza dati IANA solleva (KO dichiarato in _esito, mai il giorno UTC zitto)."""
    from zoneinfo import ZoneInfo
    return ZoneInfo(FUSO)


_ORA_UTC = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$")


class FormatoCambiato(ValueError):
    """La risposta non ha la forma misurata il 05/10/2026."""


def parse_lista(testo: str) -> Tuple[List[Dict[str, Any]], bool]:
    """(messaggi normalizzati, overflow). Forma diversa da quella misurata -> FormatoCambiato
    col campo che manca (mai una lista vuota al posto del guasto)."""
    try:
        j = json.loads(testo)
    except ValueError:
        raise FormatoCambiato("risposta non JSON")
    if not isinstance(j, dict) or not isinstance(j.get("header"), dict) or not isinstance(j.get("data"), dict):
        raise FormatoCambiato("mancano header/data")
    if j["header"].get("result.val") != 0:
        raise FormatoCambiato("header.result.val=%r (atteso 0)" % (j["header"].get("result.val"),))
    msgs = j["data"].get("messages")
    if not isinstance(msgs, list):
        raise FormatoCambiato("data.messages non e' una lista")
    out = []
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            raise FormatoCambiato("messaggio %d non e' un oggetto" % i)
        mid, tit, pub = m.get("messageId", m.get("id")), m.get("title"), m.get("publishedTime")
        sigla, nome, cat = m.get("issuerSign"), m.get("issuerName"), m.get("category")
        if not isinstance(mid, int) or isinstance(mid, bool):
            raise FormatoCambiato("messaggio %d: messageId assente o non intero" % i)
        for campo, v in (("title", tit), ("issuerSign", sigla), ("issuerName", nome), ("publishedTime", pub)):
            if not isinstance(v, str) or not v.strip():
                raise FormatoCambiato("messaggio %d: campo %s assente o vuoto" % (mid, campo))
        o = _ORA_UTC.match(pub)
        if not o:
            raise FormatoCambiato("messaggio %d: publishedTime %r fuori formato (atteso ISO UTC 'Z')" % (mid, pub))
        if not isinstance(cat, list):
            raise FormatoCambiato("messaggio %d: category non e' una lista" % mid)
        try:
            locale = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(_fuso_oslo())
        except ValueError:
            raise FormatoCambiato("messaggio %d: publishedTime %r non e' un istante valido" % (mid, pub))
        out.append({"id": mid, "titolo": " ".join(tit.split()), "data": locale.date().isoformat(),
                    "ora": locale.strftime("%H:%M"), "pubblicato": pub,
                    "sigla": sigla.strip().upper(), "emittente": nome.strip(),
                    "categoria": ", ".join(str(c.get("category_en")) for c in cat if isinstance(c, dict)),
                    "categorie_id": [c.get("id") for c in cat if isinstance(c, dict)],
                    "rettifica_di": m.get("correctionForMessageId") or 0,
                    "rettificato_da": m.get("correctedByMessageId") or 0,
                    "url": URL_PAGINA.format(id=mid)})
    return out, bool(j["data"].get("overflow"))


def _nome_norm(n: Any) -> str:
    """Nome normalizzato senza la forma giuridica norvegese (ASA/AS)."""
    return " ".join(p for p in _bi.normalizza_nome(n).split() if p not in ("ASA", "AS"))


def nome_coincide(cercato: Any, letto: Any) -> bool:
    """Stesso nome a meno di forma giuridica, accenti, punteggiatura e spazi ('esatto' o
    'compatto' di borsa_italiana): «Acme» = «Acme ASA», ma «Acme Energy» no (un
    sottoinsieme di parole non conferma l'identita')."""
    a, b = _nome_norm(cercato), _nome_norm(letto)
    return bool(a and b and _bi._combacia(a, b) in ("esatto", "compatto"))


def verifica_identita(messaggi: List[Dict[str, Any]], sigla: str, nome: str) -> Optional[str]:
    """None se OGNI messaggio e' della sigla e del nome attesi, altrimenti il motivo."""
    for m in messaggi:
        if m["sigla"] != sigla:
            return "messaggio %d con sigla %s, attesa %s" % (m["id"], m["sigla"], sigla)
        if not nome_coincide(nome, m["emittente"]):
            return ("messaggio %d dell'emittente %r, il chiamante ha confermato %r"
                    % (m["id"], m["emittente"], nome))
    return None


# ============================================================
# SCELTA (pura): NOME + PERIODO, ambiguo dichiarato
# ============================================================
def scegli(messaggi: List[Dict[str, Any]], tipo: str, fine: date) -> Dict[str, Any]:
    """{'stato': ok|ambiguo|non_trovato, 'scelto', 'candidati', 'conferme', 'scartati', 'motivo'}.
    Candidato = titolo col NOME del documento + il PERIODO, pubblicato dopo la fine del periodo e
    entro FINESTRA_GIORNI. Semestrale al 30/06: vale anche il SECONDO TRIMESTRE nel titolo, ma solo
    nella categoria 1002 (la relazione del Q2 e' la semestrale; dichiarato in `natura`)."""
    r = _regole(tipo, fine)
    lim = (fine + timedelta(days=FINESTRA_GIORNI)).isoformat()
    visti, cand, scartati = set(), [], []

    def _scarta(m, motivo):
        scartati.append({"id": m["id"], "titolo": m["titolo"], "data": m["data"], "ora": m["ora"],
                         "categoria": m["categoria"], "motivo": motivo})

    for m in sorted(messaggi, key=lambda m: m["pubblicato"]):
        if m["id"] in visti:              # lo stesso messaggio in due categorie
            continue
        visti.add(m["id"])
        t = m["titolo"]
        ha_per = bool(r["periodo"].search(t))
        ha_nome = bool(r["nome"].search(t))
        natura, prova = None, "titolo"
        if ha_per and ha_nome and (tipo != "trimestrale" or r["nome_puro"].search(t) or _RISULTATI.search(t)):
            natura = "relazione"
        elif r["q2"] is not None and r["q2"].search(t) and 1002 in m["categorie_id"] and _RISULTATI.search(t):
            natura = "secondo_trimestre_come_semestrale"
        elif ha_nome and not _ANNO.search(t):
            natura, prova = "relazione", "finestra"      # AGGIUNTA 05/10: titolo senza alcun anno
        if natura is None:
            if ha_nome:
                _scarta(m, "nome del documento con un anno/periodo diverso da %s" % fine.isoformat())
            elif ha_per:
                _scarta(m, "periodo senza il nome del documento (risultati o altro annuncio)")
            continue
        if _NON_DOC.search(t):
            _scarta(m, "invito/presentazione/webcast: cita la relazione, non e' la relazione")
            continue
        if m["data"] <= fine.isoformat():
            _scarta(m, "pubblicato il %s, non dopo la fine del periodo" % m["data"])
            continue
        lim_m = lim if prova == "titolo" else (fine + timedelta(days=FINESTRE_SENZA_ANNO[tipo])).isoformat()
        if m["data"] > lim_m:
            _scarta(m, "pubblicato il %s, oltre %d giorni dalla fine del periodo%s"
                    % (m["data"], FINESTRA_GIORNI if prova == "titolo" else FINESTRE_SENZA_ANNO[tipo],
                       "" if prova == "titolo" else " (titolo senza anno: finestra del tipo)"))
            continue
        cand.append(dict(m, lingua="no" if _NORVEGESE.search(t) else "en", natura=natura, prova=prova,
                         rettifica=bool(m["rettifica_di"])))
    # AGGIUNTA 05/10 punto 4: un candidato 'titolo' non batte un 'finestra' in silenzio
    da_titolo = [c for c in cand if c["prova"] == "titolo"]
    da_finestra = [c for c in cand if c["prova"] == "finestra"]
    conferme_finestra: List[Dict[str, Any]] = []
    if da_titolo and da_finestra:
        date_titolo = {c["data"] for c in da_titolo}
        if any(c["data"] not in date_titolo for c in da_finestra):
            return {"stato": "ambiguo", "scelto": None, "candidati": cand, "conferme": [], "scartati": scartati,
                    "motivo": "%d candidati col periodo nel titolo e %d senza anno (prova 'finestra') con date "
                              "diverse: nessuno scelto (vedi candidati)" % (len(da_titolo), len(da_finestra))}
        cand, conferme_finestra = da_titolo, da_finestra
    if not cand:
        return {"stato": "non_trovato", "scelto": None, "candidati": [], "conferme": [], "scartati": scartati,
                "motivo": "non trovato in NewsWeb (categorie %s) fra il %s e il %s: nessun titolo col nome "
                          "del documento (%s) e il periodo %s; scartati %d (vedi scartati)"
                          % ("/".join(map(str, CATEGORIE)), (fine + timedelta(days=1)).isoformat(), lim,
                             tipo, fine.isoformat(), len(scartati))}
    gruppi = {lg: [c for c in cand if c["lingua"] == lg] for lg in ("en", "no")}
    doppi = {lg: g for lg, g in gruppi.items() if len(g) > 1}
    if doppi:
        rett = sum(1 for c in cand if c["rettifica"])
        return {"stato": "ambiguo", "scelto": None, "candidati": cand, "conferme": [], "scartati": scartati,
                "motivo": "%d messaggi candidati nella stessa lingua (%s) per lo stesso documento e periodo%s: "
                          "nessuno scelto (vedi candidati)"
                          % (max(len(g) for g in doppi.values()), "/".join(doppi),
                             (", %d rettifiche" % rett) if rett else "")}
    principale = gruppi["en"] or gruppi["no"]
    altra = gruppi["no"] if gruppi["en"] else []
    if altra:
        def _istante(c):
            return datetime.fromisoformat(c["pubblicato"].replace("Z", "+00:00"))
        scarto = abs((_istante(altra[0]) - _istante(principale[0])).total_seconds()) / 60
        if scarto > CONFERMA_MAX_MINUTI:
            return {"stato": "ambiguo", "scelto": None, "candidati": cand, "conferme": [], "scartati": scartati,
                    "motivo": "versioni inglese e norvegese a %.0f minuti l'una dall'altra (oltre %d): non sono "
                              "lo stesso deposito, nessuno scelto (vedi candidati)" % (scarto, CONFERMA_MAX_MINUTI)}
    return {"stato": "ok", "scelto": principale[0], "candidati": principale,
            "conferme": [c for c in cand if c is not principale[0]] + conferme_finestra, "scartati": scartati,
            "motivo": None}


# ============================================================
# RETE (allowlist, pausa per host, delle eccezioni solo il tipo)
# ============================================================
class URLVietato(ValueError):
    pass


def url_ammesso(url: str) -> Tuple[bool, str]:
    m = re.match(r"^https://([^/?#]+)(/[^?#]*)(\?[^#]*)?$", url or "")
    if not m:
        return False, "URL non https o malformato"
    percorsi = PERCORSI_AMMESSI.get(m.group(1).lower())
    if percorsi is None:
        return False, "host %s fuori dalle fonti ammesse" % m.group(1)
    if m.group(2) not in percorsi:
        return False, "percorso %s non ammesso su %s" % (m.group(2), m.group(1))
    return True, ""


def _attendi_turno(host: str) -> None:
    prima = _ultima_richiesta.get(host)
    if prima is not None:
        resta = PAUSA_S - (time.monotonic() - prima)
        if resta > 0:
            _dormi(resta)
    _ultima_richiesta[host] = time.monotonic()


def _post_http(url: str) -> Tuple[int, bytes]:
    import requests
    r = requests.post(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                      timeout=TIMEOUT_S, allow_redirects=False)
    return r.status_code, r.content


def _scarica(url: str) -> Tuple[int, bytes]:
    """POST verso un URL AMMESSO (controllo PRIMA della rete), con la pausa per host. Niente
    redirect seguiti: un 3xx e' un HTTP non 200, quindi KO."""
    ok, motivo = url_ammesso(url)
    if not ok:
        raise URLVietato(motivo)
    _attendi_turno(HOST)
    return _post_http(url)


def url_lista(sigla: str, categoria: int, dal: date, al: date) -> str:
    return "%s?category=%d&issuer=%s&fromDate=%s&toDate=%s" % (URL_LISTA, categoria, sigla, dal.isoformat(),
                                                                al.isoformat())


# ============================================================
# CACHE (TTL 12 h, scrittura atomica, STALE dichiarato)
# ============================================================
def _cache_path(chiave: str) -> str:
    return os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", chiave) + ".json")


def _cache_leggi(chiave: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_path(chiave), encoding="utf-8") as fh:
            c = json.load(fh)
        if isinstance(c, dict) and isinstance(c.get("risultato"), dict) and isinstance(c.get("salvato_ts"), (int, float)):
            return c
    except (OSError, ValueError):
        pass
    return None


def _cache_scrivi(chiave: str, risultato: Dict[str, Any]) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"salvato_ts": time.time(), "risultato": risultato}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(chiave))
    except OSError:
        pass   # senza cache la prossima chiamata rilegge la fonte


# ============================================================
# FUNZIONE PUBBLICA
# ============================================================
def _base(ticker: Any, tipo: Any, periodo_fine: Any, isin: Any, lei: Any, nome: Any) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper() if isinstance(ticker, str) else ticker,
            "isin": isin, "lei": lei, "nome": nome, "tipo": tipo,
            "periodo_fine": periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine,
            "stato": "KO", "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "fuso": None, "natura_data": "diffusione",
            "titolo": None, "url": None, "url_documento": None, "sha256_documento": None,
            "categoria": None, "lingua": None, "prova": None, "candidati": [], "conferme": [], "scartati": [],
            "fonte": FONTE, "paese": PAESE, "url_liste": [], "sha256_liste": {}, "risposte_salvate": {},
            "fonte_modulo": FONTE_MODULO, "pagine_lette": 0, "letto_il": None,
            "limiti": list(LIMITI_FISSI), "cache": None}


def _sigla(ticker: Any, paese: Any = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(sigla, errore, motivo). La sigla di Oslo e' il ticker senza «.OL». AGGIUNTA 4 punto 1: un
    ticker NON .OL che il router instrada in Norvegia (paese='NO', es. per ISIN) non ha una sigla
    ricavabile -> 'identita_mancante' (mai 'parametro'). La risoluzione sigla-dal-nome NON e'
    implementata: servirebbe un elenco emittenti di NewsWeb, non misurato (dichiarato nel motivo)."""
    t = ticker.strip().upper() if isinstance(ticker, str) else ""
    if paese is not None and str(paese).strip().upper() != PAESE:
        return None, "parametro", "paese %r instradato su NewsWeb, che copre solo la Norvegia (NO)" % (paese,)
    if not t.endswith(".OL"):
        if paese is not None:
            return None, "identita_mancante", (
                "ticker %r instradato in Norvegia ma senza suffisso .OL: la sigla di Oslo Børs (chiave di "
                "NewsWeb) non e' ricavabile, e la risoluzione dal nome non e' disponibile (elenco emittenti "
                "NewsWeb non misurato). Serve il ticker .OL confermato dell'emittente" % (ticker,))
        return None, "parametro", "ticker %r non norvegese: NewsWeb copre solo i ticker .OL" % (ticker,)
    s = t[:-3]
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,11}", s):
        return None, "parametro", "sigla %r non valida (ticker %r)" % (s, ticker)
    return s, None, None


def _valida(ticker, tipo, periodo_fine, paese=None) -> Tuple[Optional[str], Optional[date], Optional[str], Optional[str]]:
    """(sigla, fine, errore, motivo). Prima tipo e periodo (errore 'parametro'), poi la sigla."""
    if tipo not in _TIPI:
        return None, None, "parametro", "tipo %r non ammesso: %s" % (tipo, ", ".join(_TIPI))
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return None, None, "parametro", "periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,)
    incoerente = _em.coerenza_tipo_periodo(tipo, fine)
    if incoerente:
        return None, None, "parametro", incoerente
    sigla, err, mot = _sigla(ticker, paese)
    if sigla is None:
        return None, None, err, mot
    return sigla, fine, None, None


def _esito(out: Dict[str, Any], corpi: Dict[str, str], sigla: str, nome: str, tipo: str, fine: date) -> Dict[str, Any]:
    """Dal contenuto delle risposte (url -> testo) al verdetto, nello `out`. Puro: lo usa anche
    la riverifica senza rete."""
    messaggi: List[Dict[str, Any]] = []
    try:
        _fuso_oslo()
    except Exception as e:
        out.update(stato="KO", errore="fuso_non_disponibile",
                   motivo="fuso %s non disponibile (%s): data locale non calcolabile" % (FUSO, type(e).__name__))
        return out
    for url in out["url_liste"]:
        try:
            msgs, overflow = parse_lista(corpi[url])
        except FormatoCambiato as e:
            out.update(stato="KO", errore="formato_cambiato", motivo="risposta NewsWeb %s: %s" % (url, e))
            return out
        if overflow:
            out.update(stato="KO", errore="lista_troncata",
                       motivo="NewsWeb segnala overflow (lista troncata) per %s: verdetto sospeso" % url)
            return out
        messaggi.extend(msgs)
    sbaglio = verifica_identita(messaggi, sigla, nome)
    if sbaglio:
        out.update(stato="KO", errore="identita_incoerente", motivo="identita' non confermata: " + sbaglio)
        return out
    v = scegli(messaggi, tipo, fine)
    out.update(stato=v["stato"], motivo=v["motivo"], candidati=v["candidati"], conferme=v["conferme"],
               scartati=v["scartati"])
    s = v["scelto"]
    if s is not None:
        out.update(data_deposito=s["data"], ora_deposito=s["ora"], fuso=FUSO, titolo=s["titolo"], url=s["url"],
                   categoria=s["categoria"], lingua=s["lingua"])
        out["prova"] = s["prova"]
        if s["prova"] == "finestra":
            out["limiti"].append("titolo senza anno: periodo dedotto dalla data (entro %d giorni dalla fine "
                                 "del periodo), prova='finestra'" % FINESTRE_SENZA_ANNO[tipo])
        if s["natura"] == "secondo_trimestre_come_semestrale":
            out["limiti"].append("semestrale riconosciuta dal titolo del SECONDO TRIMESTRE nella categoria "
                                 "HALF YEAR (1002): la relazione del Q2 vale come semestrale")
    if not messaggi:
        out["limiti"].append("nessun messaggio nelle categorie cercate: identita' non confermabile "
                             "(sigla %s forse non quotata a Oslo)" % sigla)
    return out


def _leggi(out: Dict[str, Any], sigla: str, nome: str, tipo: str, fine: date) -> Dict[str, Any]:
    oggi = datetime.now(timezone.utc).date()
    dal, al = fine + timedelta(days=1), min(fine + timedelta(days=FINESTRA_GIORNI), oggi)
    corpi: Dict[str, str] = {}
    for cat in CATEGORIE:
        url = url_lista(sigla, cat, dal, al)
        try:
            http, corpo = _scarica(url)
        except URLVietato as e:
            out.update(errore="url_vietato", motivo="%s" % e.args[0])
            return out
        except Exception as e:
            out.update(errore="rete", motivo="categoria %d: richiesta fallita (%s)" % (cat, type(e).__name__))
            return out
        if http != 200:
            out.update(errore="http", motivo="HTTP %s da NewsWeb (categoria %d)" % (http, cat))
            return out
        try:
            testo = corpo.decode("utf-8")
        except UnicodeDecodeError:
            out.update(errore="formato_cambiato", motivo="categoria %d: risposta non UTF-8" % cat)
            return out
        out["pagine_lette"] += 1
        out["url_liste"].append(url)
        out["sha256_liste"][url] = hashlib.sha256(corpo).hexdigest()
        out["risposte_salvate"][url] = testo
        corpi[url] = testo
    out["letto_il"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _esito(out, corpi, sigla, nome, tipo, fine)


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str] = None,
                      lei: Optional[str] = None, nome: Optional[str] = None,
                      paese: Optional[str] = None) -> Dict[str, Any]:
    """Data di diffusione su NewsWeb della relazione `tipo` del periodo che finisce il
    `periodo_fine`. Stati: ok | ambiguo | non_trovato | KO | STALE (vedi CONTRATTO).
    `paese`: quello su cui instrada il router (AGGIUNTA 4); None = dedotto dal suffisso .OL."""
    out = _base(ticker, tipo, periodo_fine, isin, lei, nome)
    sigla, fine, err, mot = _valida(ticker, tipo, periodo_fine, paese)
    if sigla is None:
        out.update(errore=err, motivo=mot)
        return out
    out["periodo_fine"] = fine.isoformat()
    if not isinstance(nome, str) or not _nome_norm(nome):
        out.update(errore="identita_mancante",
                   motivo="serve il NOME confermato dell'emittente (confrontato con issuerName di NewsWeb): "
                          "l'ISIN non e' nella risposta NewsWeb e la sigla da sola non conferma l'identita'")
        return out
    if isin:
        out["limiti"].append("ISIN %s ricevuto ma non verificabile su NewsWeb (non e' nella risposta)" % isin)
    if fine >= datetime.now(timezone.utc).date():
        out.update(errore="parametro", motivo="periodo_fine %s non ancora concluso" % fine.isoformat())
        return out
    chiave = "newsweb_%s_%s_%s_%s" % (sigla, tipo, fine.isoformat(),
                                      hashlib.sha256(_nome_norm(nome).encode("utf-8")).hexdigest()[:12])
    c = _cache_leggi(chiave)
    eta = (time.time() - float(c["salvato_ts"])) if c is not None else None
    if c is not None and eta is not None and 0 <= eta < TTL_S:
        servito = dict(c["risultato"])
        servito["cache"] = "fresca"
        return servito
    nuovo = _leggi(out, sigla, nome, tipo, fine)
    if nuovo["stato"] == "ok":
        _cache_scrivi(chiave, {k: v for k, v in nuovo.items() if k != "cache"})
        return nuovo
    if nuovo["stato"] == "KO" and nuovo["errore"] in ("rete", "http") and c is not None:
        vecchio = dict(c["risultato"])
        vecchio.update(stato_originale=vecchio.get("stato"), stato="STALE", errore=nuovo["errore"], cache="scaduta",
                       motivo="NewsWeb in guasto (%s): servita l'ultima lettura buona del %s, eta' %s"
                              % (nuovo["motivo"], vecchio.get("letto_il"),
                                 "%.0f s" % eta if eta is not None and eta >= 0 else "non misurabile"))
        return vecchio
    return nuovo


def riverifica_ricevuta(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        paese: Optional[str] = None) -> Tuple[bool, str]:
    """Senza rete: ricontrolla gli sha256 delle risposte salvate, rifa' la scelta dal loro
    contenuto e confronta stato, data, ora e titolo con la ricevuta."""
    if not isinstance(ricevuta, dict) or ricevuta.get("fonte_modulo") != FONTE_MODULO:
        return False, "ricevuta non di %s" % FONTE_MODULO
    sigla, fine, err, mot = _valida(ticker, tipo, periodo_fine, paese)   # stessa regola di get_data_deposito
    if sigla is None:
        return False, "%s: %s" % (err, mot)
    if ricevuta.get("ticker") != ticker.strip().upper() or ricevuta.get("tipo") != tipo or \
            ricevuta.get("periodo_fine") != fine.isoformat():
        return False, "la ricevuta e' di un altro ticker/tipo/periodo"
    urls = ricevuta.get("url_liste") or []
    salvate, sha = ricevuta.get("risposte_salvate") or {}, ricevuta.get("sha256_liste") or {}
    if not urls:
        return False, "ricevuta senza risposte salvate: niente da riverificare"
    if len(urls) != len(CATEGORIE):
        return False, "%d liste nella ricevuta, attese %d (categorie %s)" % (len(urls), len(CATEGORIE), CATEGORIE)
    dal_atteso, al_max = fine + timedelta(days=1), fine + timedelta(days=FINESTRA_GIORNI)
    for u, cat in zip(urls, CATEGORIE):
        ok_u, mot_u = url_ammesso(u)
        q = re.fullmatch(re.escape(URL_LISTA) + r"\?category=(\d+)&issuer=([^&]+)&fromDate=(\d{4}-\d{2}-\d{2})"
                         r"&toDate=(\d{4}-\d{2}-\d{2})", u or "")
        if not ok_u or q is None:
            return False, "URL della ricevuta non ammesso o fuori forma: %s (%s)" % (u, mot_u or "query diversa")
        if (int(q.group(1)), q.group(2), q.group(3)) != (cat, sigla, dal_atteso.isoformat()) or \
                not dal_atteso.isoformat() <= q.group(4) <= al_max.isoformat():
            return False, ("URL della ricevuta diverso da quello atteso (categoria %d, sigla %s, dal %s, al <= %s): %s"
                           % (cat, sigla, dal_atteso.isoformat(), al_max.isoformat(), u))
    for u in urls:
        if not isinstance(salvate.get(u), str):
            return False, "risposta salvata mancante per %s" % u
        if hashlib.sha256(salvate[u].encode("utf-8")).hexdigest() != sha.get(u):
            return False, "sha256 della risposta salvata diverso dalla ricevuta per %s" % u
    rifatto = _base(ticker, tipo, fine, ricevuta.get("isin"), ricevuta.get("lei"), ricevuta.get("nome"))
    rifatto["url_liste"] = list(urls)
    rifatto = _esito(rifatto, salvate, sigla, ricevuta.get("nome") or "", tipo, fine)
    stato_ric = ricevuta.get("stato_originale") if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    for campo, atteso in (("stato", stato_ric), ("data_deposito", ricevuta.get("data_deposito")),
                          ("ora_deposito", ricevuta.get("ora_deposito")), ("titolo", ricevuta.get("titolo")),
                          ("prova", ricevuta.get("prova"))):
        if rifatto.get(campo) != atteso:
            return False, "%s ricalcolato %r diverso dalla ricevuta %r" % (campo, rifatto.get(campo), atteso)
    # AGGIUNTA 2 punto 3: anche la LISTA dei candidati (decisiva per un 'ambiguo') si ricalcola
    def _insieme(lista):
        return sorted((c.get("titolo"), c.get("data"), c.get("ora"), c.get("url")) for c in lista or [])
    if _insieme(rifatto["candidati"]) != _insieme(ricevuta.get("candidati")):
        return False, ("candidati ricalcolati (%d) diversi da quelli della ricevuta (%d)"
                       % (len(rifatto["candidati"]), len(ricevuta.get("candidati") or [])))
    return True, "riverificata senza rete: %d risposte, sha256 coincidenti, scelta identica" % len(urls)


if __name__ == "__main__":  # python -m bellomberg.market_data.ue_newsweb TICKER.OL tipo AAAA-MM-GG "Nome"
    import sys
    a = sys.argv[1:]
    r = get_data_deposito(a[0], tipo=a[1], periodo_fine=a[2], nome=a[3] if len(a) > 3 else None)
    r.pop("risposte_salvate", None)
    print(json.dumps(r, ensure_ascii=False, indent=2))
