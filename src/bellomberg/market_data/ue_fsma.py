# -*- coding: utf-8 -*-
"""ue_fsma.py — data di PUBBLICAZIONE DICHIARATA delle relazioni finanziarie (annuale / semestrale /
trimestrale) degli emittenti che depositano alla FSMA belga, dall'OAM STORI (handoff-3, contratto
scratchpad INTERFACCIA_UE.md + AGGIUNTA 05/10, 05/10/2026, Opus 5.5).

FONTE E PERMESSO D'USO. STORI e' il meccanismo ufficiale di stoccaggio (OAM) belga, gestito dalla
FSMA. La pagina pubblica https://www.fsma.be/en/stori interroga un'API JSON NON DOCUMENTATA:
    POST https://webapi.fsma.be/api/v1/en/stori/result     (corpo JSON, filtro `isinCode`)
    GET  https://webapi.fsma.be/api/v1/en/stori/download?fileDataId=<id>   (il file depositato)
Nessun robots.txt su webapi.fsma.be (risponde una pagina HTML); www.fsma.be ha `Crawl-delay: 30`.
DECISIONE PM 05/10/2026: si usa con cautela: fonte dichiarata, KO se il formato cambia, PAUSA_S
(30 s) fra due richieste allo stesso host, 1 richiesta per chiamata, cache 12 ore.

MISURE (ricognizione U1 + sonda EU-BE, 05/10/2026, 5 richieste in tutto, un grande emittente belga):
  - risposta: {"resultCount": int, "storiResultItems": [...]}; ogni voce e' UN deposito con
    `reportingTopicName` (la categoria, la sceglie l'emittente), `datePublication`,
    `dateReceived`, `lei`, `isinCodes` [{code, ...}], `documentTitle` (quasi sempre ""),
    `mainDocuments` e `attachments` [{fileDataId, language, title, originalFileName, fileType}]
    (`title` puo' essere null: vale `originalFileName`). Le lingue (nl/fr/en) stanno DENTRO la
    stessa voce: un deposito, piu' file.
  - `datePublication` = data e ora di pubblicazione DICHIARATE dall'emittente nel deposito, SENZA
    fuso; `dateReceived` = ricezione FSMA (annuale 2025: pubblicazione 12/02, ricezione 19/02,
    pacchetto ESEF): si riporta a parte in data_ricezione/ora_ricezione.
  - FUSO verificato su 1 emittente: semestrale 2026 `datePublication` 07:00; il PDF depositato
    (comunicato EN) dice «Brussels – 30 July 2026 – 7:00am CET»: l'ora e' l'ora LOCALE di
    Bruxelles, non UTC. Su altri emittenti NON verificato (limite dichiarato).
  - i filtri `publicationStart`/`publicationEnd` (AAAA-MM-GG) sono rispettati dal server (misura:
    23 voci, tutte nella finestra); l'ordinamento chiesto NO (si ordina in locale).
  - i titoli sono NOMI DI FILE: «Acme HY26 EUR Financial Statements_vF.pdf», «1Q26_..._Press
    Release ENG.pdf», pacchetti ESEF «<LEI>-2025-12-31-1-en.xhtml», «QQS FY24 Annual report.pdf».

REGOLA DI SCELTA (contratto + AGGIUNTA 05/10, pura in `scegli`), per FILE depositato:
  1. il NOME del documento nel titolo del file e' obbligatorio (regex di emarket_sdir.DOCUMENTI_DEPOSITO
     + regex locali FR/NL/DE + abbreviazioni HY/H1/1Q/9M seguite da report/statements; il pacchetto
     ESEF «<LEI>-AAAA-MM-GG» vale come nome della sola ANNUALE). Senza nome nessun file conta, anche
     se la categoria dice «Half-yearly financial report»;
  2. titolo col periodo chiesto -> prova='titolo'; con un ALTRO periodo/anno -> scartato col motivo;
  3. titolo SENZA alcun anno -> prova='finestra' se la data cade dopo periodo_fine ed entro
     FINESTRA_GIORNI[tipo];
  4. piu' DEPOSITI candidati (voci diverse) = ambiguo, mai il primo; i file dello stesso deposito
     sono UN candidato (gli altri file validi vanno fra le conferme).
"""
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from bellomberg.core.paths import DATA_DIR
from bellomberg.market_data import borsa_italiana as _bi
from bellomberg.market_data import emarket_sdir as _em

FONTE = "FSMA STORI (OAM Belgio), API del sito non documentata"
PAESE = "BE"
FONTE_MODULO = "bellomberg.market_data.ue_fsma"
NATURA_DATA = "pubblicazione_dichiarata"
FUSO = "Europe/Brussels"
URL_RISULTATI = "https://webapi.fsma.be/api/v1/en/stori/result"
URL_DOWNLOAD = "https://webapi.fsma.be/api/v1/en/stori/download?fileDataId={id}"
AMMESSI = {"webapi.fsma.be": ("/api/v1/en/stori/result",)}
USER_AGENT = _bi.USER_AGENT
TIMEOUT_S = 30
PAUSA_S = 30.0                # Crawl-delay di www.fsma.be, applicato anche a webapi (decisione PM)
DIMENSIONE_PAGINA = 200       # una sola pagina: oltre -> KO 'troncato' (mai mezza lista)
TTL_S = 12 * 3600
CACHE_DIR = str(DATA_DIR / "cache_fonti_ue")
VERSIONE_REGOLA = 1           # entra nella chiave di cache
FINESTRA_GIORNI = {"annuale": 200, "semestrale": 150, "trimestrale": 120}
MAX_SCARTATI = 15

LIMITI_BASE = [
    "API del sito FSMA non documentata (webapi.fsma.be, usata dalla pagina STORI): puo' cambiare senza "
    "preavviso; un formato diverso da quello misurato il 05/10/2026 da' KO, mai un valore",
    "data = datePublication: data e ora di pubblicazione DICHIARATE dall'emittente nel deposito, non un "
    "timbro dell'autorita'; la ricezione FSMA (dateReceived) e' in data_ricezione/ora_ricezione",
    "fuso: la fonte non lo scrive; ora locale di Bruxelles VERIFICATA su un solo emittente (05/10/2026: "
    "07:00 in STORI = «7:00am CET» nel comunicato depositato), per gli altri DEDOTTA",
    "legame col documento per NOME + PERIODO nel nome del file depositato (prova='titolo') o, per i nomi "
    "senza anno, per la finestra dopo la fine del periodo (prova='finestra'); la categoria scelta "
    "dall'emittente non basta; non per hash del documento",
    "abbreviazioni HY26 / FY25 / 1Q26 lette come esercizio SOLARE (HY = 30/06, 1Q = 31/03, 3Q/9M = 30/09)",
    "trimestrale: in Belgio l'informazione trimestrale e' spesso il comunicato stampa: un comunicato col "
    "trimestre nel nome del file vale come nome del documento",
    "filtro per ISIN (isinCode): depositi registrati sotto un altro ISIN non sono visti; un ISIN "
    "sconosciuto a STORI da' lo stesso esito di un deposito assente (non_trovato)",
    "non_trovato = non trovato in STORI nella finestra cercata, non «mai pubblicato»",
    "pausa di %d s fra richieste misurata nel processo, non fra processi diversi" % int(PAUSA_S),
]


class URLVietato(ValueError):
    """URL fuori dagli host/percorsi ammessi o non https: rifiutato PRIMA della rete."""


# ============================================================
# NOMI E PERIODI (regex)
# ============================================================
_MESI_FR = ("janvier", "f[ée]vrier", "mars", "avril", "mai", "juin", "juillet", "ao[uû]t",
            "septembre", "octobre", "novembre", "d[ée]cembre")
_MESI_NL = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
            "september", "oktober", "november", "december")
_REPORT = (r"(?:financial\s+statements|financial\s+report|interim\s+report|report|statements|rapport|verslag|"
           r"[ée]tats\s+financiers|jaarrekening|financi[eë]le\s+staten)")
_LOCALI = {
    "annuale": (r"rapport\s+financier\s+annuel|rapport\s+annuel|(?<![a-z])jaarverslag|"
                r"jaarlijks\s+financieel\s+verslag|(?<![a-z])jaarrapport|(?<![a-z])jaarrekening|"
                r"document\s+d['’]?\s*enregistrement\s+universel|universal\s+registration\s+document|"
                r"gesch[äa]ftsbericht|jahresfinanzbericht|annual\s+accounts|comptes\s+annuels|"
                # pacchetto ESEF <LEI>-AAAA-MM-GG: obbligatorio per la sola relazione annuale
                r"\b[A-Z0-9]{18}\d{2}-\d{4}-\d{2}-\d{2}\b"),
    "semestrale": (r"rapport\s+(?:financier\s+)?semestriel|halfjaarverslag|halfjaarlijks\s+financieel\s+verslag|"
                   r"financieel\s+halfjaarverslag|halfjaarrapport|halfjaarbericht|halbjahres(?:finanz)?bericht|"
                   r"(?:\b(?:HY|H1|1H|S1)(?:\s*[-']?\s*\d{2,4})?\b|half[\s-]*year(?:ly)?|semestriel(?:le)?|"
                   r"(?<![a-z])halfjaar(?:lijks)?)\D{0,40}?" + _REPORT),
    "trimestrale": (r"rapport\s+trimestriel|d[ée]claration\s+interm[ée]diaire|kwartaalverslag|kwartaalrapport|"
                    r"kwartaalbericht|tussentijdse\s+verklaring|quartalsmitteilung|quartalsbericht|trading\s+update|"
                    r"(?:\b(?:Q1|Q3|1Q|3Q|9M|T1|T3)(?:\s*[-']?\s*\d{2,4})?\b|first\s+quarter|third\s+quarter|"
                    r"nine\s+months|premier\s+trimestre|troisi[eè]me\s+trimestre|eerste\s+kwartaal|derde\s+kwartaal)"
                    r"\D{0,40}?(?:report|statement|results|update|press\s+release|rapport|verslag|"
                    r"communiqu[ée]|persbericht)|"
                    # ordine inverso: «Press Release_EN_1Q21»
                    r"(?:press\s+release|results|persbericht|communiqu[ée])\D{0,40}?\b(?:Q1|Q3|1Q|3Q|9M)\s*[-']?\s*\d{2,4}\b"),
}
# un anno o un periodo abbreviato QUALSIASI nel titolo (per la regola 2/3 dell'AGGIUNTA)
_ANNO = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)|\b(?:HY|H[12]|[12]H|FY|[1-4]Q|Q[1-4]|9M|S[12]|T[1-4])\s*[-']?\s*"
                   r"\d{2}(?:\d{2})?\b", re.I)
_LEI_IN_TITOLO = re.compile(r"\b[A-Z0-9]{18}\d{2}\b")
_RETTIFICA = re.compile(r"rectifi|erratum|corrigendum|correction|corrected|amended|amendment|replaces?\b|"
                        r"rechtzetting|verbetering|gecorrigeerd", re.I)
# ANNUNCIO della messa a disposizione (REGOLA ANNUNCIO del contratto): FR/EN/NL
_ANNUNCIO = re.compile(r"mise\s+[àa]\s+disposition|modalit[ée]s\s+de\s+mise|availability|made\s+available|"
                       r"now\s+available|ter\s+beschikking|beschikbaarstelling|beschikbaar", re.I)
# categorie STORI delle relazioni periodiche (solo per DIRE perche' un deposito non conta, mai per scegliere)
_CATEGORIA_PERIODICA = re.compile(r"annual\s+financial\s+report|half-yearly|quarterly|interim\s+statement", re.I)
_DATA_ORA = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d{1,7})?)?$")
_ID_FILE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


# Costante PUBBLICA uniforme dei moduli UE (contratto, AGGIUNTA 2): {tipo: [regex_str, ...]} in tutte le
# lingue usate dalla fonte, da compilare con re.I. La legge depositi_ue.nomi_documento (import pigro).
NOMI_DOCUMENTO = {t: [_LOCALI[t], _em.DOCUMENTI_DEPOSITO[t][0], _em.DOCUMENTI_DEPOSITO[t][1]] for t in _LOCALI}


def regex_nome(tipo: str) -> "re.Pattern":
    """Nome del documento: FR/NL/DE/abbreviazioni locali + IT/EN di emarket_sdir (NOMI_DOCUMENTO)."""
    return re.compile("|".join(NOMI_DOCUMENTO[tipo]), re.I)


def regex_periodo(fine: date, tipo: str) -> "re.Pattern":
    """Il periodo scritto nel titolo: forme di emarket_sdir (IT/EN) + data ISO (anche nel pacchetto
    ESEF) + date FR/NL a parole + abbreviazioni (HY26, FY25, 1Q26) + l'anno attaccato al nome."""
    g, m, a = fine.day, fine.month, fine.year
    aa = "(?:%d|%02d)" % (a, a % 100)
    nomi = regex_nome(tipo).pattern
    alt = [_em._regex_periodo(fine, tipo).pattern,
           r"(?<!\d)%04d-%02d-%02d(?!\d)" % (a, m, g),
           r"\b0?%d(?:er)?\s+%s\s+%d\b" % (g, _MESI_FR[m - 1], a),
           r"\b0?%d\s+%s\s+%d\b" % (g, _MESI_NL[m - 1], a)]
    if tipo == "annuale":
        alt += [r"\bFY\s*[-']?\s*%s\b" % aa,
                r"(?:%s)\D{0,20}(?<!\d)%d(?!\d)" % (nomi, a), r"(?<!\d)%d(?!\d)\D{0,20}(?:%s)" % (a, nomi),
                r"(?:exercice|boekjaar|gesch[äa]ftsjahr)\s+%d\b" % a]
        if m != 12:
            alt += [r"\b%d\s*[/-]\s*(?:%d|%02d)\b" % (a - 1, a, a % 100)]
    elif tipo == "semestrale" and m == 6:
        alt += [r"\b(?:HY|H1|1H|S1)\s*[-']?\s*%s\b" % aa,
                r"(?:\b(?:HY|H1|1H|S1)\b|half[\s-]*year(?:ly)?|(?<![a-z])halfjaar(?:lijks)?|semestriel(?:le)?)"
                r"\D{0,25}(?<!\d)%d(?!\d)" % a,
                r"(?:premier|1er)\s+semestre\s+%d\b" % a, r"eerste\s+(?:jaarhelft|halfjaar|semester)\s+%d\b" % a]
    elif tipo == "trimestrale" and m == 3:
        alt += [r"\b(?:Q1|1Q|T1)\s*[-']?\s*%s\b" % aa, r"(?:premier|1er)\s+trimestre\s+%d\b" % a,
                r"eerste\s+kwartaal\s+%d\b" % a]
    elif tipo == "trimestrale" and m == 9:
        alt += [r"\b(?:Q3|3Q|9M|T3)\s*[-']?\s*%s\b" % aa, r"troisi[eè]me\s+trimestre\s+%d\b" % a,
                r"neuf\s+(?:premiers\s+)?mois\s+%d\b" % a, r"derde\s+kwartaal\s+%d\b" % a,
                r"(?:eerste\s+)?negen\s+maanden\s+%d\b" % a]
    return re.compile("|".join("(?:%s)" % x for x in alt), re.I)


def normalizza_titolo(titolo: Any) -> str:
    """Nome di file -> testo: via l'estensione, '_' e spazi multipli -> uno spazio."""
    t = str(titolo or "")
    t = re.sub(r"\.(?:pdf|xhtml|html?|zip|docx?|xlsx?)\s*$", "", t.strip(), flags=re.I)
    return " ".join(t.replace("_", " ").split())


def tipi_nel_nome(titolo: str) -> List[str]:
    """I tipi il cui NOME compare nel titolo. Semestrale e trimestrale sono nomi SPECIFICI:
    se uno dei due c'e', i nomi generici dell'annuale («consolidated financial statements»,
    «semi-annual report» contiene «annual report») non contano."""
    specifici = [t for t in ("semestrale", "trimestrale") if regex_nome(t).search(titolo)]
    if specifici:
        return specifici
    return ["annuale"] if regex_nome("annuale").search(titolo) else []


def _ha_anno(titolo: str) -> bool:
    return bool(_ANNO.search(_LEI_IN_TITOLO.sub(" ", titolo)))


# ============================================================
# RISPOSTA DELL'API -> depositi
# ============================================================
def _data_ora(testo: Any) -> Optional[Tuple[str, str]]:
    """'AAAA-MM-GGTHH:MM[:SS[.f]]' SENZA fuso -> ('AAAA-MM-GG', 'HH:MM'). Un fuso esplicito
    (Z, +01:00) o un'altra forma = formato cambiato -> None (il significato dell'ora cambierebbe)."""
    if not isinstance(testo, str):
        return None
    m = _DATA_ORA.match(testo.strip())
    if not m:
        return None
    try:
        date.fromisoformat(m.group(1))
    except ValueError:
        return None
    if int(m.group(2)) > 23 or int(m.group(3)) > 59:
        return None
    return m.group(1), "%s:%s" % (m.group(2), m.group(3))


def parse_risposta(corpo: str, *, isin: str, lei: Optional[str] = None) -> Dict[str, Any]:
    """{"stato": ok|KO, "errore", "motivo", "totale", "depositi": [...]}. KO se la FORMA non e'
    quella misurata (chiavi, tipi, data senza fuso, id dei file), se una voce non porta l'ISIN
    chiesto (filtro ignorato dal server) o un LEI diverso da quello dato."""
    def _ko(errore, motivo):
        return {"stato": "KO", "errore": errore, "motivo": motivo, "totale": 0, "depositi": []}
    try:
        d = json.loads(corpo)
    except ValueError:
        return _ko("formato_cambiato", "risposta non JSON")
    if not isinstance(d, dict) or not isinstance(d.get("storiResultItems"), list) or \
            isinstance(d.get("resultCount"), bool) or not isinstance(d.get("resultCount"), int):
        return _ko("formato_cambiato", "attesi resultCount (intero) e storiResultItems (lista)")
    voci = d["storiResultItems"]
    if len(voci) > d["resultCount"]:
        return _ko("formato_cambiato", "%d voci ma resultCount %d" % (len(voci), d["resultCount"]))
    depositi = []
    for i, v in enumerate(voci, 1):
        if not isinstance(v, dict) or not isinstance(v.get("reportingTopicName"), str) or \
                not isinstance(v.get("mainDocuments"), list) or not isinstance(v.get("isinCodes"), list) or \
                not isinstance(v.get("attachments") if v.get("attachments") is not None else [], list):
            return _ko("formato_cambiato", "voce %d: chiavi attese reportingTopicName, mainDocuments, isinCodes" % i)
        pub = _data_ora(v.get("datePublication"))
        if pub is None:
            return _ko("formato_cambiato", "voce %d: datePublication %r non nella forma misurata "
                                           "(AAAA-MM-GGTHH:MM:SS senza fuso)" % (i, v.get("datePublication")))
        ric = None
        if v.get("dateReceived") is not None:
            ric = _data_ora(v.get("dateReceived"))
            if ric is None:
                return _ko("formato_cambiato", "voce %d: dateReceived %r non nella forma misurata" % (i, v.get("dateReceived")))
        codici = [c.get("code") for c in v["isinCodes"] if isinstance(c, dict)]
        if isin not in codici:
            return _ko("filtro_ignorato", "voce %d senza l'ISIN chiesto %s (porta %s): il filtro isinCode non e' "
                                          "stato applicato, lista inutilizzabile" % (i, isin, ", ".join(map(str, codici)) or "nessuno"))
        if lei and v.get("lei") and v.get("lei") != lei:
            return _ko("identita_incoerente", "voce %d: LEI %s diverso da quello dell'identita' confermata %s"
                                              % (i, v.get("lei"), lei))
        files = []
        titolo_voce = normalizza_titolo(v.get("documentTitle"))
        if titolo_voce:
            files.append({"titolo": titolo_voce, "titolo_originale": v.get("documentTitle"), "id": None,
                          "lingua": None, "formato": None, "ruolo": "titolo_deposito"})
        for ruolo in ("mainDocuments", "attachments"):
            for f in v.get(ruolo) or []:
                nome = (f.get("title") or f.get("originalFileName")) if isinstance(f, dict) else None
                if not isinstance(nome, str) or not isinstance(f.get("fileDataId"), str) or \
                        not _ID_FILE.match(f["fileDataId"]):
                    return _ko("formato_cambiato", "voce %d: file senza nome o con fileDataId non nella forma "
                                                   "misurata" % i)
                files.append({"titolo": normalizza_titolo(nome), "titolo_originale": nome, "id": f["fileDataId"],
                              "lingua": f.get("language") if isinstance(f.get("language"), str) else None,
                              "formato": f.get("fileType") if isinstance(f.get("fileType"), str) else None,
                              "ruolo": "principale" if ruolo == "mainDocuments" else "allegato"})
        depositi.append({"data": pub[0], "ora": pub[1],
                         "data_ricezione": ric[0] if ric else None, "ora_ricezione": ric[1] if ric else None,
                         "categoria": v["reportingTopicName"], "lei": v.get("lei"), "file": files,
                         "n": i})
    return {"stato": "ok", "errore": None, "motivo": None, "totale": d["resultCount"], "depositi": depositi}


# ============================================================
# SCELTA (pura)
# ============================================================
_ORDINE_FORMATO = {"pdf": 0, "xhtml": 1, "zip": 2}
_ORDINE_LINGUA = {"en": 0, "fr": 1, "nl": 2, "de": 3}


def _url_file(f: Dict[str, Any]) -> Optional[str]:
    return URL_DOWNLOAD.format(id=f["id"]) if f.get("id") else None


def scegli(depositi: List[Dict[str, Any]], tipo: str, fine: date) -> Dict[str, Any]:
    """{"stato": ok|ambiguo|non_trovato, "scelto", "candidati", "conferme", "prova", "scartati",
    "motivo"}. Vedi REGOLA DI SCELTA nel docstring del modulo."""
    per = regex_periodo(fine, tipo)
    limite = (fine + timedelta(days=FINESTRA_GIORNI[tipo])).isoformat()
    candidati, scartati = [], []

    def _scarta(dep, f, motivo):
        scartati.append({"titolo": f["titolo_originale"], "data": dep["data"], "categoria": dep["categoria"],
                         "motivo": motivo})

    for dep in sorted(depositi, key=lambda x: (x["data"], x["ora"], x["n"])):
        validi, nominati = [], 0
        for f in dep["file"]:
            t = f["titolo"]
            if tipo not in tipi_nel_nome(t):
                continue
            nominati += 1
            if len(tipi_nel_nome(t)) > 1:
                _scarta(dep, f, "nome conteso fra %s: il tipo non e' univoco" % " e ".join(tipi_nel_nome(t)))
                continue
            if dep["data"] <= fine.isoformat():
                _scarta(dep, f, "pubblicato il %s, non dopo la fine del periodo %s" % (dep["data"], fine.isoformat()))
                continue
            if dep["data"] > limite:
                _scarta(dep, f, "pubblicato il %s, oltre la finestra di %d giorni" % (dep["data"], FINESTRA_GIORNI[tipo]))
                continue
            if per.search(t):
                validi.append(dict(f, prova="titolo"))
            elif _ha_anno(t):
                _scarta(dep, f, "nome del documento con un periodo/anno diverso da %s" % fine.isoformat())
            else:
                validi.append(dict(f, prova="finestra"))
        if not nominati and _CATEGORIA_PERIODICA.search(dep["categoria"]) and fine.isoformat() < dep["data"] <= limite:
            _scarta(dep, {"titolo_originale": "; ".join(f["titolo_originale"] for f in dep["file"])[:200]},
                    "categoria %r ma nessun file col nome del documento %s: la categoria da sola non basta"
                    % (dep["categoria"], tipo))
        if not validi:
            continue
        for f in validi:
            f["annuncio"] = bool(_ANNUNCIO.search(f["titolo"]))
        # nello stesso deposito: prima il periodo nel titolo, poi il DOCUMENTO (non l'annuncio), poi un FILE
        # scaricabile (il titolo del deposito non ha url: rilievo RV-UE1 FSMA-4), poi principale/formato/lingua
        validi.sort(key=lambda f: (f["prova"] != "titolo", f["annuncio"], f["id"] is None, f["ruolo"] == "allegato",
                                   _ORDINE_FORMATO.get((f["formato"] or "").lower(), 9),
                                   _ORDINE_LINGUA.get(f["lingua"] or "", 9)))
        primo = validi[0]
        url, url_da = _url_file(primo), "file"
        if url is None:
            # solo il titolo del deposito porta nome+periodo: il documento e' il file principale del deposito
            principali = sorted((f for f in dep["file"] if f.get("id") and f["ruolo"] == "principale"),
                                key=lambda f: (_ORDINE_FORMATO.get((f["formato"] or "").lower(), 9),
                                               _ORDINE_LINGUA.get(f["lingua"] or "", 9)))
            url = _url_file(principali[0]) if principali else None
            url_da = "file_principale_del_deposito" if principali else None
        candidati.append({"titolo": primo["titolo_originale"], "data": dep["data"], "ora": dep["ora"],
                          "url": url, "url_da": url_da, "categoria": dep["categoria"], "lingua": primo["lingua"],
                          "prova": primo["prova"], "annuncio": primo["annuncio"],
                          "data_ricezione": dep["data_ricezione"], "ora_ricezione": dep["ora_ricezione"],
                          "rettifica": any(_RETTIFICA.search(f["titolo"]) for f in validi),
                          # formato UNIFORME (AGGIUNTA 2): le conferme hanno le stesse chiavi dei candidati
                          "altri_file": [{"titolo": f["titolo_originale"], "data": dep["data"], "ora": dep["ora"],
                                          "url": _url_file(f), "categoria": dep["categoria"], "lingua": f["lingua"],
                                          "prova": f["prova"]} for f in validi[1:]]})
    # REGOLA ANNUNCIO (contratto, da EU-FR): il DOCUMENTO comanda sul deposito che ne annuncia la
    # disponibilita'; l'annuncio vale solo se il documento manca (limite dichiarato da chi applica la scelta)
    if any(not c["annuncio"] for c in candidati):
        for c in [c for c in candidati if c["annuncio"]]:
            scartati.append({"titolo": c["titolo"], "data": c["data"], "categoria": c["categoria"],
                             "motivo": "annuncio di messa a disposizione: il documento stesso e' fra i candidati"})
        candidati = [c for c in candidati if not c["annuncio"]]
    n_scartati = len(scartati)
    scartati = scartati[:MAX_SCARTATI]
    if not candidati:
        return {"stato": "non_trovato", "scelto": None, "candidati": [], "conferme": [], "prova": None,
                "scartati": scartati,
                "motivo": "non trovato in FSMA STORI fra il %s e il %s: nessun file depositato col nome del "
                          "documento (%s) e il periodo %s (o senza anno, nella finestra); scartati: %d (vedi "
                          "scartati)" % ((fine + timedelta(days=1)).isoformat(), limite, tipo, fine.isoformat(),
                                         n_scartati)}
    if len(candidati) > 1:
        prove = sorted({c["prova"] for c in candidati})
        return {"stato": "ambiguo", "scelto": None, "candidati": candidati, "conferme": [], "prova": None,
                "scartati": scartati,
                "motivo": "%d depositi distinti candidati per lo stesso documento e periodo (prova: %s, date: %s): "
                          "nessuno scelto" % (len(candidati), ", ".join(prove),
                                              ", ".join(sorted({c["data"] for c in candidati})))}
    c = candidati[0]
    return {"stato": "ok", "scelto": c, "candidati": candidati, "conferme": c["altri_file"], "prova": c["prova"],
            "scartati": scartati, "motivo": None}


# ============================================================
# RETE E CACHE
# ============================================================
_ultima_richiesta: Dict[str, float] = {}
_dormi = time.sleep           # sostituibile nei test (la pausa vera e' 30 s)


def url_ammesso(url: str) -> Tuple[bool, str]:
    p = urlsplit(url or "")
    if p.scheme != "https":
        return False, "URL non https"
    percorsi = AMMESSI.get((p.hostname or "").lower())
    if percorsi is None:
        return False, "host %s fuori dalle fonti ammesse" % p.hostname
    if p.path not in percorsi or p.query or p.fragment:
        return False, "percorso %s non ammesso su %s" % (p.path, p.hostname)
    return True, ""


def _invia(url: str, corpo: Dict[str, Any]) -> Tuple[int, bytes]:
    """POST JSON senza redirect (un 3xx torna come HTTP = KO), User-Agent del modulo, pausa per host."""
    ok, motivo = url_ammesso(url)
    if not ok:
        raise URLVietato(motivo)
    import requests
    host = urlsplit(url).hostname
    attesa = PAUSA_S - (time.monotonic() - _ultima_richiesta.get(host, -1e12))
    if attesa > 0:
        _dormi(attesa)
    try:
        r = requests.post(url, json=corpo, timeout=TIMEOUT_S, allow_redirects=False,
                          headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    finally:
        _ultima_richiesta[host] = time.monotonic()
    return r.status_code, r.content


def corpo_query(isin: str, tipo: str, fine: date) -> Dict[str, Any]:
    """Corpo del POST: finestra di pubblicazione [fine, fine + FINESTRA + 1] (il giorno della fine
    si legge per poterlo SCARTARE dichiarato; l'ultimo giorno in piu' copre un estremo esclusivo)."""
    return {"startRowIndex": 0, "pageSize": DIMENSIONE_PAGINA, "sortDirection": "Ascending",
            "sortColumn": "datePublication", "isinCode": isin, "publicationStart": fine.isoformat(),
            "publicationEnd": (fine + timedelta(days=FINESTRA_GIORNI[tipo] + 1)).isoformat()}


def chiave_richiesta(corpo: Dict[str, Any]) -> str:
    """La «URL» della ricevuta: l'API e' un POST, quindi URL + '#' + corpo JSON canonico."""
    return URL_RISULTATI + "#" + json.dumps(corpo, sort_keys=True, separators=(",", ":"))


def _cache_path(chiave: str) -> str:
    return os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", chiave) + ".json")


def _cache_leggi(chiave: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_path(chiave), encoding="utf-8") as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(c, dict) and isinstance(c.get("risultato"), dict) and \
            isinstance(c.get("salvato_ts"), (int, float)) and not isinstance(c.get("salvato_ts"), bool):
        return c
    return None


def _cache_scrivi(chiave: str, risultato: Dict[str, Any]) -> None:
    """Temporaneo + os.replace: un file a meta' non si serve mai."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"salvato_ts": time.time(), "risultato": risultato}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(chiave))
    except OSError:
        pass  # la cache e' un'ottimizzazione: senza, la prossima chiamata rilegge la fonte


# ============================================================
# FUNZIONE PUBBLICA
# ============================================================
def _base(ticker: Any, tipo: Any, periodo_fine: Any, isin: Any, lei: Any, nome: Any) -> Dict[str, Any]:
    return {"ticker": str(ticker or "").strip().upper(), "isin": isin, "lei": lei, "nome": nome, "tipo": tipo,
            "periodo_fine": periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine,
            "stato": "KO", "stato_originale": None, "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "fuso": None, "natura_data": NATURA_DATA,
            "data_ricezione": None, "ora_ricezione": None,
            "titolo": None, "url": None, "url_documento": None, "sha256_documento": None,
            "categoria": None, "lingua": None, "candidati": [], "conferme": [], "prova": None, "scartati": [],
            "fonte": FONTE, "paese": PAESE, "url_liste": [], "sha256_liste": {}, "risposte_salvate": {},
            "fonte_modulo": FONTE_MODULO, "pagine_lette": 0, "letto_il": None, "limiti": list(LIMITI_BASE),
            "cache": None}


def lei_valido(lei: Any) -> bool:
    """ISO 17442: 18 alfanumerici + 2 cifre di controllo, ISO 7064 MOD 97-10 (resto 1)."""
    if not isinstance(lei, str) or not re.fullmatch(r"[A-Z0-9]{18}\d{2}", lei):
        return False
    return int("".join(str(int(c, 36)) for c in lei)) % 97 == 1


def _riassunto(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: c.get(k) for k in ("titolo", "data", "ora", "url", "categoria", "lingua", "prova",
                                  "data_ricezione", "ora_ricezione", "rettifica", "annuncio", "url_da")}


def _applica_scelta(out: Dict[str, Any], v: Dict[str, Any]) -> None:
    out.update(stato=v["stato"], motivo=v["motivo"], prova=v["prova"], scartati=v["scartati"],
               candidati=[_riassunto(c) for c in v["candidati"]], conferme=list(v["conferme"]))
    s = v["scelto"]
    if s is not None:
        out.update(data_deposito=s["data"], ora_deposito=s["ora"], fuso=FUSO, titolo=s["titolo"], url=s["url"],
                   url_documento=s["url"], categoria=s["categoria"], lingua=s["lingua"],
                   data_ricezione=s["data_ricezione"], ora_ricezione=s["ora_ricezione"])
        if s["prova"] == "finestra":
            out["limiti"].append("nome del file senza anno: il periodo e' dedotto dalla data (dopo il %s, entro %d "
                                 "giorni), non letto nel titolo" % (out["periodo_fine"], FINESTRA_GIORNI[out["tipo"]]))
        if s.get("rettifica"):
            out["limiti"].append("il deposito scelto e' marcato come rettifica/versione corretta")
        if s.get("annuncio"):
            out["limiti"].append("il deposito scelto ANNUNCIA la messa a disposizione del documento (il documento "
                                 "stesso non e' fra i depositi letti): la data e' quella dell'annuncio")
        if s.get("url_da") == "file_principale_del_deposito":
            out["limiti"].append("nome e periodo letti nel titolo del DEPOSITO, non in un file: url = file principale "
                                 "del deposito")


def _leggi(out: Dict[str, Any], isin: str, lei: Optional[str], tipo: str, fine: date) -> Dict[str, Any]:
    corpo = corpo_query(isin, tipo, fine)
    chiave = chiave_richiesta(corpo)
    try:
        http, grezzo = _invia(URL_RISULTATI, corpo)
    except URLVietato as e:
        out.update(errore="url_vietato", motivo="URL rifiutato prima della rete: %s" % e.args[0])
        return out
    except Exception as e:
        out.update(errore="rete", motivo="richiesta all'API STORI fallita: %s" % type(e).__name__)
        return out
    if http != 200:
        out.update(errore="http", motivo="HTTP %s dall'API STORI" % http)
        return out
    try:
        testo = grezzo.decode("utf-8")
    except UnicodeDecodeError:
        out.update(errore="formato_cambiato", motivo="risposta non UTF-8")
        return out
    out["pagine_lette"] = 1
    out["url_liste"] = [chiave]
    out["sha256_liste"] = {chiave: hashlib.sha256(grezzo).hexdigest()}
    out["risposte_salvate"] = {chiave: testo}
    p = parse_risposta(testo, isin=isin, lei=lei)
    if p["stato"] != "ok":
        out.update(errore=p["errore"], motivo=p["motivo"])
        return out
    if p["totale"] > len(p["depositi"]):
        out.update(errore="troncato", motivo="%d depositi nella finestra, letti %d (pagina da %d): lista incompleta, "
                                             "nessun verdetto" % (p["totale"], len(p["depositi"]), DIMENSIONE_PAGINA))
        return out
    out["letto_il"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _applica_scelta(out, scegli(p["depositi"], tipo, fine))
    return out


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str] = None,
                      lei: Optional[str] = None, nome: Optional[str] = None,
                      paese: Optional[str] = None) -> Dict[str, Any]:
    """Data e ora di pubblicazione DICHIARATA (ora locale di Bruxelles) su FSMA STORI della relazione
    `tipo` al `periodo_fine`. Identita' dal chiamante: ISIN obbligatorio (filtro della fonte), LEI
    facoltativo come controllo; mai indovinata dal simbolo. Stati: ok | ambiguo | non_trovato | KO |
    STALE. Cache 12 h per ok/ambiguo; KO con cache = STALE.
    `paese` (AGGIUNTA 4 del contratto): lo passa l'instradatore; STORI si interroga per ISIN, quindi
    qui non cambia la richiesta ne' la scelta (accettato e ignorato, l'esito e' identico)."""
    out = _base(ticker, tipo, periodo_fine, isin, lei, nome)
    if tipo not in FINESTRA_GIORNI:
        out.update(errore="parametro", motivo="tipo %r non ammesso: %s" % (tipo, ", ".join(FINESTRA_GIORNI)))
        return out
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        out.update(errore="parametro", motivo="periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,))
        return out
    out["periodo_fine"] = fine.isoformat()
    incoerente = _em.coerenza_tipo_periodo(tipo, fine)
    if incoerente:
        out.update(errore="parametro", motivo=incoerente)
        return out
    if not isin:
        out.update(errore="identita_mancante", motivo="FSMA STORI si interroga per ISIN (filtro isinCode): serve "
                                                      "isin= dall'identita' confermata")
        return out
    if not _bi.isin_valido(isin):
        out.update(errore="identita_non_valida", motivo="ISIN %r non valido (forma o cifra di controllo)" % (isin,))
        return out
    if lei is not None and not lei_valido(lei):
        out.update(errore="identita_non_valida", motivo="LEI %r non valido (forma o cifre di controllo)" % (lei,))
        return out
    chiave = "fsma_v%d_%s_%s_%s" % (VERSIONE_REGOLA, isin, tipo, fine.isoformat())
    c = _cache_leggi(chiave)
    eta = (time.time() - float(c["salvato_ts"])) if c is not None else None
    if c is not None and eta is not None and 0 <= eta < TTL_S and c["risultato"].get("lei") == lei:
        r = dict(c["risultato"])
        r.update(ticker=out["ticker"], nome=nome, cache="fresca")
        return r
    nuovo = _leggi(out, isin, lei, tipo, fine)
    if nuovo["stato"] in ("ok", "ambiguo"):
        _cache_scrivi(chiave, {k: v for k, v in nuovo.items() if k != "cache"})
        return nuovo
    if nuovo["stato"] == "KO" and c is not None and c["risultato"].get("lei") == lei:
        vecchio = dict(c["risultato"])
        vecchio.update(ticker=out["ticker"], nome=nome, stato_originale=vecchio.get("stato"), stato="STALE",
                       errore=nuovo["errore"], cache="scaduta",
                       motivo="fonte STORI in guasto (%s): servita l'ultima lettura buona del %s, eta' %s"
                              % (nuovo["motivo"], vecchio.get("letto_il"),
                                 "%.0f s" % eta if eta is not None and eta >= 0 else "non misurabile"))
        vecchio["limiti"] = list(vecchio.get("limiti") or []) + ["STALE: lettura vecchia servita per guasto della fonte"]
        return vecchio
    return nuovo


# ============================================================
# RIVERIFICA SENZA RETE
# ============================================================
def riverifica_ricevuta(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        paese: Optional[str] = None) -> Tuple[bool, str]:
    """Ricalcola la scelta dalle `risposte_salvate`, senza rete: la richiesta salvata deve essere
    quella che il modulo costruisce per questo ISIN/tipo/periodo, lo sha256 deve combaciare col
    corpo, e stato/data/ora/titolo/url ricalcolati devono coincidere con quelli della ricevuta.
    `paese` (dall'instradatore) se dato deve coincidere col paese sigillato nella ricevuta."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un dict"
    if ricevuta.get("fonte_modulo") != FONTE_MODULO:
        return False, "fonte_modulo %r non e' %s" % (ricevuta.get("fonte_modulo"), FONTE_MODULO)
    if paese is not None and (paese != ricevuta.get("paese") or ricevuta.get("paese") != PAESE):
        return False, "paese chiesto %r diverso da quello sigillato nella ricevuta %r (modulo %s)" % (
            paese, ricevuta.get("paese"), PAESE)
    if str(ticker or "").strip().upper() != ricevuta.get("ticker"):
        return False, "ticker %r diverso da quello della ricevuta %r" % (ticker, ricevuta.get("ticker"))
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return False, "periodo_fine %r non e' una data" % (periodo_fine,)
    if tipo not in FINESTRA_GIORNI or tipo != ricevuta.get("tipo") or fine.isoformat() != ricevuta.get("periodo_fine"):
        return False, "tipo/periodo chiesti (%s %s) diversi da quelli della ricevuta (%s %s)" % (
            tipo, fine.isoformat(), ricevuta.get("tipo"), ricevuta.get("periodo_fine"))
    stato = ricevuta.get("stato_originale") if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    if stato not in ("ok", "ambiguo", "non_trovato"):
        return False, "ricevuta in stato %r: nessuna lettura della fonte da riverificare" % (stato,)
    isin = ricevuta.get("isin")
    if not isinstance(isin, str):
        return False, "ricevuta senza ISIN"
    attesa = chiave_richiesta(corpo_query(isin, tipo, fine))
    urls = ricevuta.get("url_liste") or []
    if urls != [attesa]:
        return False, "la richiesta della ricevuta non e' la query di questo modulo per %s %s %s" % (
            isin, tipo, fine.isoformat())
    corpo = (ricevuta.get("risposte_salvate") or {}).get(attesa)
    if not isinstance(corpo, str):
        return False, "risposta salvata mancante"
    if hashlib.sha256(corpo.encode("utf-8")).hexdigest() != (ricevuta.get("sha256_liste") or {}).get(attesa):
        return False, "sha256 della risposta diverso da quello della ricevuta: contenuto alterato"
    p = parse_risposta(corpo, isin=isin, lei=ricevuta.get("lei"))
    if p["stato"] != "ok":
        return False, "risposta illeggibile alla riverifica: %s" % p["motivo"]
    if p["totale"] > len(p["depositi"]):
        return False, "la risposta salvata non copre tutti i %d depositi della finestra" % p["totale"]
    v = scegli(p["depositi"], tipo, fine)
    s = v["scelto"] or {}
    # rilievo RV-UE1 FSMA-2: si confronta OGNI campo che arriva al PM, non solo data/ora/titolo/url
    ricalcolo = {"stato": v["stato"], "data_deposito": s.get("data"), "ora_deposito": s.get("ora"),
                 "titolo": s.get("titolo"), "url": s.get("url"), "url_documento": s.get("url"),
                 "prova": v["prova"], "categoria": s.get("categoria"), "lingua": s.get("lingua"),
                 "data_ricezione": s.get("data_ricezione"), "ora_ricezione": s.get("ora_ricezione"),
                 "fuso": FUSO if v["scelto"] else None}
    for k, val in ricalcolo.items():
        atteso = stato if k == "stato" else ricevuta.get(k)
        if val != atteso:
            return False, "%s ricalcolato %r diverso dalla ricevuta %r" % (k, val, atteso)
    # AGGIUNTA 2: per un ambiguo conta la LISTA dei candidati (qui per ogni stato: anche l'ok ne ha una)
    def _insieme(lista):
        return {(c.get("titolo"), c.get("data"), c.get("ora"), c.get("url")) for c in lista or [] if isinstance(c, dict)}
    if _insieme(v["candidati"]) != _insieme(ricevuta.get("candidati")) or \
            len(v["candidati"]) != len(ricevuta.get("candidati") or []):
        return False, "candidati ricalcolati (%d) diversi da quelli della ricevuta (%d)" % (
            len(v["candidati"]), len(ricevuta.get("candidati") or []))
    return True, "ricevuta riverificata senza rete: %s, sha256 della risposta coerente" % v["stato"]


if __name__ == "__main__":  # python -m bellomberg.market_data.ue_fsma TICKER TIPO AAAA-MM-GG ISIN
    import sys
    a = sys.argv[1:]
    print(json.dumps({k: v for k, v in get_data_deposito(a[0], tipo=a[1], periodo_fine=a[2], isin=a[3]).items()
                      if k != "risposte_salvate"}, ensure_ascii=False, indent=2))
