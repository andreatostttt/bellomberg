# -*- coding: utf-8 -*-
"""ue_nasdaq_nordic.py — data e ora di DIFFUSIONE delle relazioni finanziarie periodiche
(annuale / semestrale / trimestrale) degli emittenti quotati a Helsinki (.HE), Stoccolma (.ST)
e Copenaghen (.CO), dal servizio «company news» di Nasdaq Nordic (handoff-3, contratto
INTERFACCIA_UE del 05/10/2026, Opus 5.5).

FONTE E SUO RUOLO (ricognizione U1, 05/10/2026):
  - FI: Nasdaq Helsinki e' il meccanismo ufficiale di stoccaggio (OAM) finlandese.
  - SE, DK: e' il CANALE DI BORSA (Nasdaq Stockholm / Copenhagen), NON l'OAM ufficiale
    (SE: l'OAM di Finansinspektionen non e' stato misurato; DK: l'OAM e' Finanstilsynet, portale
    non interrogato). Il ritorno lo dichiara in `fonte` e in `limiti`.
  - API JSON del sito, NON DOCUMENTATA (decisione PM 05/10: si usa con cautela): fonte dichiarata,
    formato diverso da quello misurato = KO 'formato_cambiato', pausa >= PAUSA_S fra le richieste,
    solo l'host e il percorso in HOST_AMMESSI. Nessun robots.txt (404, misurato da U1).

MISURE (U1 + sonda EU-NQ del 05/10/2026, 5 richieste):
  - `company=` vuole il nome ESATTO dell'emittente in Nasdaq, maiuscole comprese: «Acme, AB» ->
    risultati, «AB Acme» / «acme, ab» -> 0; «Zeta» (prefisso) -> 0. Il nome lo da' il CHIAMANTE
    (identita' confermata), il modulo non lo deduce MAI dal simbolo.
  - fromDate/toDate IGNORATI dalla fonte: l'elenco arriva dal piu' recente (dir=DESC) e si pagina
    con start= finche' si arriva a prima della fine del periodo (al massimo MAX_PAGINE).
  - timeZone=UTC accettato: `releaseTime` e' in UTC (con CET la stessa riga e' 2 h avanti d'estate).
  - senza `language` arrivano tutte le lingue (un emittente svedese: en + sv, stessa ora): la coppia e' UN
    comunicato; l'inglese comanda, le altre lingue vanno fra le `conferme`.
  - la CATEGORIA la sceglie l'emittente e sbaglia (un emittente misurato: Annual Report 2025 sotto «Other
    information disclosed according to the rules of the Exchange»; Q2 2021 sotto «Interim report
    (Q1 and Q3)»): il PERIODO si legge SEMPRE dal titolo.
  - titoli di relazioni periodiche SENZA il nome del documento («Acme Group - the second quarter
    2026», «Zeta Nordisk reports ... for Q2 2026»): per semestrale/trimestrale la natura di
    relazione periodica puo' venire da CATEGORIE_PERIODICHE (prova='categoria', dichiarata);
    per l'annuale il nome e' obbligatorio. Deroga APPROVATA da main il 05/10 (svuotare la costante
    = regola «nome sempre obbligatorio» alla lettera). Categoria periodica con titolo SENZA periodo
    = non candidato.

REGOLE DI SCELTA (AGGIUNTA 05/10 dell'interfaccia):
  1. nome del documento nel titolo (o, solo sem/trim, categoria periodica: v. sopra);
  2. titolo col periodo GIUSTO -> prova 'titolo'; titolo con un anno ma periodo DIVERSO -> scartato;
  3. titolo SENZA alcun anno -> prova 'finestra' se la data UTC cade dopo periodo_fine ed entro
     FINESTRA_GIORNI[tipo];
  4. data strettamente DOPO periodo_fine ed entro FINESTRA_GIORNI[tipo] per ogni candidato;
  5. piu' candidati nella stessa lingua = ambiguo con la lista, mai il primo.

CONTRATTO: get_data_deposito(...) e riverifica_ricevuta(...) come INTERFACCIA_UE.md.
"""
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from bellomberg.core.paths import DATA_DIR
from bellomberg.market_data.emarket_sdir import DOCUMENTI_DEPOSITO, coerenza_tipo_periodo

FONTE_MODULO = "bellomberg.market_data.ue_nasdaq_nordic"
HOST = "api.news.eu.nasdaq.com"
PERCORSO = "/news/query.action"
HOST_AMMESSI = {HOST: (PERCORSO,)}      # unico host e unico percorso interrogati
URL_BASE = "https://%s%s?" % (HOST, PERCORSO)
USER_AGENT = "Bellomberg/1.0 (ricerca personale, basso volume)"
TIMEOUT_S = 25
PAUSA_S = 2.0            # pausa minima fra due richieste all'host (API non documentata: ritmo lento)
LIMITE_PAGINA = 200      # righe per richiesta (misurato: 200 accettato)
MAX_PAGINE = 5           # oltre: finestra non raggiunta -> KO dichiarato, mai un non_trovato
TTL_S = 12 * 3600
CACHE_DIR = str(DATA_DIR / "cache_fonti_ue")
FINESTRA_GIORNI = {"annuale": 200, "semestrale": 150, "trimestrale": 120}
_TIPI = tuple(FINESTRA_GIORNI)

SUFFISSI = {".HE": "FI", ".ST": "SE", ".CO": "DK"}
# fuso del listino: i confronti con la fine del periodo e con la finestra si fanno sul GIORNO LOCALE
# (una relazione alle 00:30 del 01/07 a Helsinki e' il 30/06 21:30 UTC: e' DOPO la fine del periodo)
FUSI_LISTINO = {"FI": "Europe/Helsinki", "SE": "Europe/Stockholm", "DK": "Europe/Copenhagen"}
LINGUA_LOCALE = {"FI": "fi", "SE": "sv", "DK": "da"}
_MERCATO = {"FI": re.compile(r"helsinki|finland", re.I), "SE": re.compile(r"stockholm|sweden", re.I),
            "DK": re.compile(r"copenhagen|denmark", re.I)}
FONTE = {"FI": "Nasdaq Helsinki company news (OAM Finlandia; API del sito non documentata)",
         "SE": "Nasdaq Stockholm company news (CANALE DI BORSA, non l'OAM svedese; API del sito non documentata)",
         "DK": "Nasdaq Copenhagen company news (CANALE DI BORSA, non l'OAM danese Finanstilsynet; "
               "API del sito non documentata)"}
FONTE_GENERICA = "Nasdaq Nordic company news (API del sito non documentata)"

# categorie Nasdaq delle relazioni periodiche (id misurati, nomi come li scrive la fonte):
# valgono SOLO come natura del documento per semestrale/trimestrale, mai per il periodo
CATEGORIE_PERIODICHE = {78: "Half Year financial report", 153: "Interim report (Q1 and Q3)",
                        68: "Quarterly report"}

_CAMPI_RIGA = ("disclosureId", "headline", "language", "releaseTime", "messageUrl", "company",
               "categoryId", "cnsCategory", "market", "attachment")


class URLVietato(ValueError):
    """URL fuori da HOST_AMMESSI (host, schema o percorso)."""


# ============================================================
# NOMI DEI DOCUMENTI E PERIODI
# ============================================================
_ANNUALE_NORD = (r"annual\s+(?:and\s+sustainability\s+)?report|års-?\s*och\s+hållbarhetsredovisning|"
                 r"årsredovisning|årsrapport|vuosikertomus|tilinpäätös(?!tiedote)|"
                 r"financial\s+statements\s+(?:and|&)\s+(?:the\s+)?(?:board\s+of\s+directors['’]?\s+|"
                 r"directors['’]?\s+)?report")
_PERIODICA_NORD = (r"interim\s+(?:financial\s+)?report|half[\s-]*year(?:ly)?\s+(?:financial\s+)?report|"
                   r"six[\s-]*months?\s+report|quarterly\s+(?:financial\s+)?report|"
                   r"report\s+for\s+(?:the\s+)?(?:Q[1-4]|first|second|third)\b|"
                   r"\bQ[1-4]\s+(?:20\d\d\s+)?(?:interim\s+)?report|business\s+review|"
                   r"delårsrapport|halvårsrapport|kvartalsrapport|osavuosikatsaus|puolivuosikatsaus|"
                   r"liiketoimintakatsaus|kvartalsmeddelelse|periodemeddelelse|interim\s+management\s+statement")
# Nome PUBBLICO uniforme (INTERFACCIA_UE, AGGIUNTA 2): {tipo: [regex_str]}, da compilare con re.I;
# letto da depositi_ue.nomi_documento. EN da emarket_sdir + SV/FI/DA locali.
NOMI_DOCUMENTO = {
    "annuale": [DOCUMENTI_DEPOSITO["annuale"][1], _ANNUALE_NORD],
    "semestrale": [DOCUMENTI_DEPOSITO["semestrale"][1], _PERIODICA_NORD],
    "trimestrale": [DOCUMENTI_DEPOSITO["trimestrale"][1], _PERIODICA_NORD],
}
DOCUMENTI = {t: re.compile("|".join(v), re.I) for t, v in NOMI_DOCUMENTO.items()}
# annunci SULLA relazione, non la relazione: inviti, webcast, date di calendario, preliminari
_ESCLUSI = re.compile(
    r"invitation|\binvites?\b|inbjudan|\bkutsu|webcast|conference\s+call|telefonkonferens|audiocast|"
    r"capital\s+markets\s+day|will\s+publish|to\s+publish|to\s+be\s+published|publiceras\s+den|"
    r"julkaistaan|offentliggøres|dates?\s+(?:of|for)\s+(?:the\s+)?(?:publication|financial)|"
    r"change\s+of\s+date|new\s+date|brings\s+forward|financial\s+calendar|finansiell\s+kalender|"
    r"preliminary|preliminär|ennakkotie|foreløbig|general\s+meeting|bolagsstämm|yhtiökokou|"
    r"generalforsamling", re.I)
_RETTIFICA = re.compile(r"correction|corrected|rättelse|korjaus|oikaisu|rettelse|amended|\breplaces?\b", re.I)
_ANNO = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")

_MESI_EN = ("january", "february", "march", "april", "may", "june", "july", "august",
            "september", "october", "november", "december")
_MESI_SV = ("januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
            "september", "oktober", "november", "december")
_MESI_DA = ("januar", "februar", "marts", "april", "maj", "juni", "juli", "august",
            "september", "oktober", "november", "december")
_MESI_FI = ("tammi", "helmi", "maalis", "huhti", "touko", "kesä", "heinä", "elo", "syys", "loka",
            "marras", "joulu")
_TRATTINO = r"\s*[-–—]\s*"


def regex_periodo(fine: date, tipo: str) -> "re.Pattern":
    """Il periodo scritto nel titolo (EN/SV/FI/DA). Date esplicite per ogni tipo; trimestri,
    semestri e mesi «gennaio-giugno» per i periodi di fine marzo/giugno/settembre; l'anno vicino
    al nome per l'annuale."""
    g, m, a = fine.day, fine.month, fine.year
    alt = [r"\b0?%d\s+%s,?\s+%d\b" % (g, _MESI_EN[m - 1], a),
           r"\b%s\s+0?%d(?:st|nd|rd|th)?,?\s+%d\b" % (_MESI_EN[m - 1], g, a),
           r"\b0?%d[./-]0?%d[./-]%d\b" % (g, m, a), r"\b%d-%02d-%02d\b" % (a, m, g)]
    if tipo == "annuale":
        alt += [r"(?:%s)\D{0,15}%d(?!\d)" % (DOCUMENTI["annuale"].pattern, a),
                r"\b%d\s+(?:integrated\s+)?(?:annual\s+(?:financial\s+)?report|årsredovisning|årsrapport|"
                r"vuosikertomus)" % a,
                r"(?:financial|fiscal)\s+year\s+%d\b|\b%d\s+(?:financial|fiscal)\s+year" % (a, a),
                r"räkenskapsåret\s+%d\b|verksamhetsåret\s+%d\b|regnskabsåret\s+%d\b|tilikau\w*\s+%d\b"
                % (a, a, a, a)]
        if m != 12:
            alt += [r"\b%d\s*[/-]\s*(?:%d|%02d)\b" % (a - 1, a, a % 100)]
        return re.compile("|".join(alt), re.I)
    n = {3: 1, 6: 2, 9: 3}.get(m)
    if n is not None:
        ord_en = ("first", "second", "third")[n - 1]
        ord_sv = ("första", "andra", "tredje")[n - 1]
        ord_da = ("første", "andet", "tredje")[n - 1]
        alt += [r"\bQ%d\s*[/-]?\s*%d\b" % (n, a), r"\b%dQ\s*%d\b" % (n, a), r"\b%d\s*Q%d\b" % (a, n),
                r"\b%s\s+quarter\s+(?:of\s+)?%d\b" % (ord_en, a),
                r"\b%s\s+kvartalet\s+%d\b" % (ord_sv, a), r"\b%s\s+kvartal\s+%d\b" % (ord_da, a),
                r"\b%d\.\s*kvartal\w*\s+%d\b" % (n, a),
                r"\bjanuary%s%s,?\s+%d\b" % (_TRATTINO, _MESI_EN[m - 1], a),
                r"\bjan%s%s,?\s+%d\b" % (_TRATTINO, _MESI_EN[m - 1][:3], a),
                r"\bjanuari%s%s\s+%d\b" % (_TRATTINO, _MESI_SV[m - 1], a),
                r"\bjanuar%s%s\s+%d\b" % (_TRATTINO, _MESI_DA[m - 1], a),
                r"\btammi%s%s\w*\s+%d\b" % (_TRATTINO, _MESI_FI[m - 1], a),
                r"\b(?:first\s+)?%s\s+months\s+(?:of\s+|ended\s+)?%d\b" % (("three", "six", "nine")[n - 1], a),
                r"\b%dM\s*%d\b" % (3 * n, a)]
        if n == 2:
            alt += [r"\bH1\s*[/-]?\s*%d\b|\b1H\s*%d\b|\b%d\s*H1\b" % (a, a, a),
                    r"\bhalf[\s-]*year(?:ly)?\s+(?:financial\s+)?(?:report\s+)?(?:of\s+)?%d\b" % a,
                    r"\bfirst\s+half(?:[\s-]+year)?\s+(?:of\s+)?%d\b" % a,
                    r"\b(?:första\s+halvåret|første\s+halvår|1\.\s*halvår)\s+%d\b" % a]
    return re.compile("|".join(alt), re.I)


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split())


def fuso_listino(paese: str):
    """ZoneInfo del listino o None (dati IANA assenti: chi chiama dichiara KO, mai il giorno UTC zitto)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(FUSI_LISTINO[paese])
    except Exception:
        return None


def giorno_locale(data: str, ora: str, fuso) -> str:
    dt = datetime.strptime("%s %s" % (data, ora), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return dt.astimezone(fuso).date().isoformat()


# ============================================================
# RETE (host e percorso ammessi, pausa, nessun redirect seguito)
# ============================================================
_ultima_richiesta = [0.0]


def url_consentito(url: str) -> Tuple[bool, str]:
    p = urlsplit(url or "")
    if p.scheme != "https":
        return False, "URL non https"
    percorsi = HOST_AMMESSI.get((p.hostname or "").lower())
    if percorsi is None:
        return False, "host %s fuori dalle fonti previste" % p.hostname
    if p.path not in percorsi:
        return False, "percorso %s non ammesso su %s" % (p.path, p.hostname)
    return True, ""


def url_pagina(nome: str, start: int, testo: str = "") -> str:
    """Pagina dei comunicati dell'emittente `nome` (company=, nome ESATTO) oppure, con `testo` e
    nome vuoto, la ricerca a testo libero (freeText=) usata per RISOLVERE il nome."""
    return URL_BASE + urlencode({
        "type": "json", "showAttachments": "true", "showCnsSpecific": "true", "showCompany": "true",
        "countResults": "true", "freeText": testo, "company": nome, "cnscategory": "", "market": "",
        "globalGroup": "", "globalName": "NordicAllMarkets", "displayLanguage": "en", "timeZone": "UTC",
        "dateMask": "yyyy-MM-dd HH:mm:ss", "limit": str(LIMITE_PAGINA), "start": str(start), "dir": "DESC"})


def _scarica(url: str) -> Tuple[int, bytes]:
    ok, motivo = url_consentito(url)
    if not ok:
        raise URLVietato(motivo)
    import requests
    attesa = PAUSA_S - (time.monotonic() - _ultima_richiesta[0])
    if attesa > 0:
        time.sleep(attesa)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                         timeout=TIMEOUT_S, allow_redirects=False)
    finally:
        _ultima_richiesta[0] = time.monotonic()
    return r.status_code, r.content


# ============================================================
# PARSE DELLA RISPOSTA (formato misurato; diverso = KO)
# ============================================================
def parse_risposta(corpo: Any) -> Dict[str, Any]:
    """{"stato": ok|KO, "righe", "totale", "errore", "motivo"}. Ogni riga deve avere i campi
    misurati coi tipi giusti e `releaseTime` 'AAAA-MM-GG HH:MM:SS': una sola riga diversa = KO
    'formato_cambiato' (nessuna riga saltata in silenzio)."""
    ko = {"stato": "KO", "righe": [], "totale": None, "errore": "formato_cambiato"}
    try:
        testo = corpo.decode("utf-8") if isinstance(corpo, bytes) else corpo
        d = json.loads(testo)
    except (UnicodeDecodeError, ValueError, TypeError) as e:
        return dict(ko, motivo="risposta non JSON UTF-8 (%s): formato dell'API cambiato" % type(e).__name__)
    voci = d.get("results", {}).get("item") if isinstance(d, dict) and isinstance(d.get("results"), dict) else None
    if not isinstance(voci, list):
        return dict(ko, motivo="manca results.item (lista): formato dell'API cambiato")
    totale = d.get("count")
    if totale is not None and (isinstance(totale, bool) or not isinstance(totale, int)):
        return dict(ko, motivo="count %r non intero: formato dell'API cambiato" % (totale,))
    righe = []
    for i, v in enumerate(voci):
        mancano = [c for c in _CAMPI_RIGA if not isinstance(v, dict) or c not in v]
        if mancano:
            return dict(ko, motivo="riga %d senza i campi %s: formato dell'API cambiato" % (i + 1, ", ".join(mancano)))
        try:
            quando = datetime.strptime(v["releaseTime"], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return dict(ko, motivo="riga %d: releaseTime %r non 'AAAA-MM-GG HH:MM:SS'" % (i + 1, v.get("releaseTime")))
        if not isinstance(v["headline"], str) or not isinstance(v["company"], str) or \
                not isinstance(v["attachment"], list):
            return dict(ko, motivo="riga %d: headline/company/attachment di tipo inatteso" % (i + 1))
        allegati = []
        for a in v["attachment"]:
            if not isinstance(a, dict) or not isinstance(a.get("attachmentUrl"), str):
                return dict(ko, motivo="riga %d: allegato senza attachmentUrl" % (i + 1))
            allegati.append({"nome": a.get("fileName"), "mime": a.get("mimetype"), "url": a["attachmentUrl"]})
        righe.append({"id": v["disclosureId"], "titolo": _norm(v["headline"]), "lingua": v["language"],
                      "data": quando.date().isoformat(), "ora": quando.strftime("%H:%M"),
                      "pubblicato": v.get("published"), "url": v["messageUrl"], "emittente": v["company"],
                      "categoria_id": v["categoryId"], "categoria": v["cnsCategory"], "mercato": v["market"],
                      "allegati": allegati})
    return {"stato": "ok", "righe": righe, "totale": totale, "errore": None, "motivo": None}


# ============================================================
# SCELTA (pura, senza rete)
# ============================================================
def pagine_complete(pagine: List[Dict[str, Any]], fine: date) -> bool:
    """True se le pagine lette coprono tutto cio' che e' stato pubblicato dopo `fine`: l'ultima
    pagina e' corta (o il totale e' raggiunto), oppure la riga piu' vecchia e' gia' <= fine."""
    if not pagine:
        return False
    ultima = pagine[-1]
    letti = sum(len(p["righe"]) for p in pagine)
    if len(ultima["righe"]) < LIMITE_PAGINA or (ultima.get("totale") is not None and letti >= ultima["totale"]):
        return True
    return min(r["data"] for r in ultima["righe"]) <= fine.isoformat()


def scegli(righe: List[Dict[str, Any]], *, nome: str, tipo: str, fine: date, paese: str) -> Dict[str, Any]:
    """Verdetto dalle righe gia' lette: {"stato": ok|ambiguo|non_trovato, "scelto", "candidati",
    "conferme", "scartati", "motivo", "prova", "note"}."""
    cercato = _norm(nome)
    note: List[str] = []
    if not righe:
        return {"stato": "non_trovato", "errore": "nome_non_trovato", "scelto": None, "candidati": [],
                "conferme": [], "scartati": [], "prova": None, "note": note,
                "motivo": "nessun comunicato in Nasdaq Nordic per l'emittente %r: la fonte vuole il nome "
                          "ESATTO in Nasdaq, maiuscole comprese (es. «Acme, AB», non «AB Acme»)" % cercato}
    esatte = [r for r in righe if _norm(r["emittente"]) == cercato]
    altri = sorted({_norm(r["emittente"]) for r in righe} - {cercato})
    if not esatte:
        return {"stato": "non_trovato", "errore": "nome_non_trovato", "scelto": None, "candidati": [],
                "conferme": [], "scartati": [], "prova": None, "note": note,
                "motivo": "la fonte ha risposto con emittenti di nome diverso da %r (%s): nessun nome "
                          "coincide esattamente, identita' non confermata" % (cercato, "; ".join(altri[:5]))}
    if altri:
        note.append("righe di emittenti di nome diverso scartate: %s" % "; ".join(altri[:5]))
    mercati = sorted({_norm(r["mercato"]) for r in esatte})
    if not any(_MERCATO[paese].search(m) for m in mercati):
        return {"stato": "ambiguo", "errore": "mercato_diverso", "scelto": None, "candidati": [],
                "conferme": [], "scartati": [], "prova": None, "note": note,
                "motivo": "l'emittente %r in Nasdaq pubblica su %s, non sul mercato del suffisso (%s): "
                          "omonimo o simbolo sbagliato, identita' da confermare" % (cercato, ", ".join(mercati), paese)}
    if len(mercati) > 1:
        note.append("emittente su piu' mercati Nasdaq: %s" % ", ".join(mercati))

    fuso = fuso_listino(paese)
    if fuso is None:
        return {"stato": "KO", "errore": "fuso_non_disponibile", "scelto": None, "candidati": [],
                "conferme": [], "scartati": [], "prova": None, "note": note,
                "motivo": "fuso %s non disponibile (dati IANA): il giorno locale non si calcola" % FUSI_LISTINO[paese]}
    nome_rx = DOCUMENTI[tipo]
    per = regex_periodo(fine, tipo)
    limite = fine + timedelta(days=FINESTRA_GIORNI[tipo])
    cand, scartati, visti = [], [], set()

    def _scarta(r, motivo):
        scartati.append({"titolo": r["titolo"], "data": r["data"], "ora": r["ora"], "lingua": r["lingua"],
                         "categoria": r["categoria"], "url": r["url"], "motivo": motivo})

    for r in esatte:
        if (r["id"], r["lingua"]) in visti:
            continue
        visti.add((r["id"], r["lingua"]))
        t = r["titolo"]
        dl = giorno_locale(r["data"], r["ora"], fuso)
        ha_nome = bool(nome_rx.search(t))
        da_categoria = (not ha_nome and tipo != "annuale" and r["categoria_id"] in CATEGORIE_PERIODICHE)
        if not ha_nome and not da_categoria:
            continue
        if per.search(t):
            prova = "categoria" if da_categoria else "titolo"
        elif _ANNO.search(t):
            if dl > fine.isoformat() and dl <= limite.isoformat():
                _scarta(r, "anno/periodo nel titolo diverso da %s" % fine.isoformat())
            continue
        elif ha_nome:
            prova = "finestra"
        else:
            continue          # solo categoria e nessun periodo nel titolo: niente prova del periodo
        if _ESCLUSI.search(t):
            _scarta(r, "annuncio/invito/calendario/preliminare, non la relazione")
            continue
        if dl <= fine.isoformat():
            _scarta(r, "pubblicato il %s (giorno locale %s), non dopo la fine del periodo" % (dl, FUSI_LISTINO[paese]))
            continue
        if dl > limite.isoformat():
            _scarta(r, "pubblicato il %s, oltre la finestra di %d giorni dalla fine del periodo"
                    % (dl, FINESTRA_GIORNI[tipo]))
            continue
        cand.append({"titolo": t, "data": r["data"], "ora": r["ora"], "url": r["url"],
                     "categoria": r["categoria"], "lingua": r["lingua"], "id": r["id"], "prova": prova,
                     "rettifica": bool(_RETTIFICA.search(t)), "allegati": r["allegati"],
                     "mercato": r["mercato"], "data_locale": dl})
    lingue = [x for x in ("en", LINGUA_LOCALE[paese]) if any(c["lingua"] == x for c in cand)]
    lingue += sorted({c["lingua"] for c in cand} - set(lingue))
    gruppo = [c for c in cand if lingue and c["lingua"] == lingue[0]]
    conferme = [c for c in cand if c not in gruppo]
    base = {"candidati": gruppo, "conferme": conferme, "scartati": scartati, "note": note}
    if not gruppo:
        return dict(base, stato="non_trovato", errore=None, scelto=None, prova=None,
                    motivo="non trovato nella fonte %s fra i comunicati pubblicati dal %s al %s: nessun titolo "
                           "col nome del documento (%s) e il periodo; scartati: %d (vedi scartati)"
                           % (FONTE_GENERICA, (fine + timedelta(days=1)).isoformat(), limite.isoformat(),
                              tipo, len(scartati)))
    if len(gruppo) > 1:
        prove = sorted({c["prova"] for c in gruppo})
        return dict(base, stato="ambiguo", errore=None, scelto=None, prova=None,
                    motivo="%d comunicati candidati in lingua %s (prove: %s%s): nessuno scelto, vedi candidati"
                           % (len(gruppo), lingue[0], ", ".join(prove),
                              ", %d rettifiche" % sum(c["rettifica"] for c in gruppo)
                              if any(c["rettifica"] for c in gruppo) else ""))
    s = gruppo[0]
    # regola 4 dell'AGGIUNTA anche FRA lingue: una conferma con un'altra data non si tace; se una delle
    # due prove e' 'finestra' (titolo senza anno) e il giorno differisce = ambiguo
    diverse = [c for c in conferme if (c["data"], c["ora"]) != (s["data"], s["ora"])]
    if any(c["data"] != s["data"] and "finestra" in (s["prova"], c["prova"]) for c in diverse):
        return dict(base, candidati=[s] + diverse, conferme=[c for c in conferme if c not in diverse],
                    stato="ambiguo", errore=None, scelto=None, prova=None,
                    motivo="la scelta in %s (%s %s, prova %s) e %d comunicati in altra lingua hanno date diverse, "
                           "con una prova 'finestra': nessuno scelto, vedi candidati"
                           % (s["lingua"], s["data"], s["ora"], s["prova"], len(diverse)))
    if diverse:
        note.append("conferme in altra lingua con data/ora diversa dalla scelta: %s"
                    % "; ".join("%s %s %s" % (c["lingua"], c["data"], c["ora"]) for c in diverse))
    return dict(base, stato="ok", errore=None, scelto=s, prova=s["prova"], motivo=None)


# ============================================================
# RITORNO, CACHE, FUNZIONE PUBBLICA
# ============================================================
def _limiti(paese: Optional[str]) -> List[str]:
    out = ["API JSON del sito Nasdaq (api.news.eu.nasdaq.com) NON documentata: puo' cambiare senza "
           "preavviso; un formato diverso da quello misurato da' KO, mai un dato indovinato",
           "identita' dal NOME dato dal chiamante, mai dal simbolo: prima si prova come nome ESATTO Nasdaq; "
           "se la fonte non lo conosce, si risolve per confronto NORMALIZZATO (forme societarie AB/A/S/Oyj/"
           "ASA..., maiuscole, punteggiatura, mai prefissi) coi nomi della ricerca a testo libero sul mercato "
           "del suffisso: solo una corrispondenza UNICA, dichiarata in 'instradamento'",
           "nessun legame ISIN/ticker -> nome Nasdaq: l'unica ricerca per ISIN misurata (api.nasdaq.com) e' "
           "vietata dal suo robots.txt (Disallow: /); isin/lei non usati da questa fonte",
           "scelta per NOME del documento + PERIODO nel titolo (la categoria la sceglie l'emittente); "
           "semestrale/trimestrale: la natura di relazione periodica puo' venire dalla categoria Nasdaq "
           "(prova='categoria'), il periodo sempre dal titolo",
           "prova='finestra' = titolo col nome del documento ma senza anno, pubblicato entro %s giorni "
           "(annuale/semestrale/trimestrale) dalla fine del periodo" % "/".join(str(FINESTRA_GIORNI[t]) for t in _TIPI),
           "data e ora = releaseTime della fonte richiesto in UTC; e' la DIFFUSIONE del comunicato (url) "
           "che porta la relazione in allegato",
           "non_trovato = non trovato fra i comunicati letti nella fonte, non «mai pubblicato»"]
    if paese in ("SE", "DK"):
        out.insert(0, "%s: CANALE DI BORSA Nasdaq, NON il meccanismo ufficiale di stoccaggio (OAM) del paese"
                   % paese)
    elif paese == "FI":
        out.insert(0, "FI: Nasdaq Helsinki e' l'OAM finlandese (ricognizione U1 05/10/2026)")
    return out


def _base(ticker: str, tipo: Any, periodo_fine: Any, isin: Any, lei: Any, nome: Any,
          paese: Optional[str] = None) -> Dict[str, Any]:
    pf = periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine
    return {"ticker": (ticker or "").strip().upper(), "isin": isin, "lei": lei, "nome": nome,
            "tipo": tipo, "periodo_fine": pf, "stato": "KO", "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "fuso": None, "natura_data": "diffusione",
            "titolo": None, "url": None, "url_documento": None, "sha256_documento": None,
            "categoria": None, "lingua": None, "candidati": [], "conferme": [], "prova": None, "scartati": [],
            "fonte": FONTE.get(paese, FONTE_GENERICA), "paese": paese, "url_liste": [], "sha256_liste": {},
            "risposte_salvate": {}, "fonte_modulo": FONTE_MODULO, "pagine_lette": 0, "letto_il": None,
            "limiti": _limiti(paese), "cache": None, "nome_nasdaq": None, "instradamento": None,
            "data_deposito_locale": None, "ora_deposito_locale": None, "fuso_locale": None}


def paese_da_ticker(ticker: Any) -> Optional[str]:
    t = (ticker or "").strip().upper() if isinstance(ticker, str) else ""
    for suff, p in SUFFISSI.items():
        if t.endswith(suff) and len(t) > len(suff):
            return p
    return None


def _pdf(allegati: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [a for a in allegati or [] if (a.get("mime") or "").lower() == "application/pdf"
            or (a.get("nome") or "").lower().endswith(".pdf")]


def url_documento(allegati: List[Dict[str, Any]]) -> Optional[str]:
    """L'URL del PDF se il comunicato ne ha UNO solo; con piu' PDF non si sceglie (None)."""
    pdf = _pdf(allegati)
    return pdf[0]["url"] if len(pdf) == 1 else None


def _applica_verdetto(out: Dict[str, Any], v: Dict[str, Any]) -> None:
    out.update(stato=v["stato"], errore=v.get("errore"), motivo=v["motivo"], candidati=v["candidati"],
               conferme=v["conferme"], scartati=v["scartati"], prova=v["prova"])
    out["limiti"] = out["limiti"] + v["note"]
    s = v["scelto"]
    if s is None:
        return
    pdf = _pdf(s["allegati"])
    out.update(data_deposito=s["data"], ora_deposito=s["ora"], fuso="UTC", titolo=s["titolo"], url=s["url"],
               categoria=s["categoria"], lingua=s["lingua"], url_documento=url_documento(s["allegati"]))
    fuso = fuso_listino(out["paese"]) if out.get("paese") in FUSI_LISTINO else None
    if fuso is not None:
        loc = datetime.strptime("%s %s" % (s["data"], s["ora"]), "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc).astimezone(fuso)
        out.update(data_deposito_locale=loc.date().isoformat(), ora_deposito_locale=loc.strftime("%H:%M"),
                   fuso_locale=FUSI_LISTINO[out["paese"]])
    if len(pdf) > 1:
        out["limiti"].append("%d allegati PDF nel comunicato: url_documento non scelto (vedi candidati.allegati)"
                             % len(pdf))


def _leggi(out: Dict[str, Any], nome: str, tipo: str, fine: date, paese: str) -> Dict[str, Any]:
    pagine: List[Dict[str, Any]] = []
    for n in range(MAX_PAGINE):
        p = _prendi(out, url_pagina(nome, n * LIMITE_PAGINA), "pagina %d" % (n + 1))
        if p is None:
            return out
        out["pagine_lette"] += 1
        pagine.append(p)
        if pagine_complete(pagine, fine):
            break
    out["letto_il"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not pagine_complete(pagine, fine):
        out.update(stato="KO", errore="finestra_non_raggiunta",
                   motivo="lette %d pagine da %d comunicati senza arrivare alla fine del periodo %s: "
                          "verdetto sospeso (periodo troppo vecchio per questa fonte)"
                          % (MAX_PAGINE, LIMITE_PAGINA, fine.isoformat()))
        return out
    righe = [r for p in pagine for r in p["righe"]]
    _applica_verdetto(out, scegli(righe, nome=nome, tipo=tipo, fine=fine, paese=paese))
    return out


def _cache_path(chiave: str) -> str:
    return os.path.join(CACHE_DIR, "nasdaq_" + hashlib.sha256(chiave.encode("utf-8")).hexdigest()[:32] + ".json")


def _cache_leggi(chiave: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_path(chiave), encoding="utf-8") as fh:
            c = json.load(fh)
        if isinstance(c, dict) and c.get("chiave") == chiave and isinstance(c.get("risultato"), dict) \
                and isinstance(c.get("salvato_ts"), (int, float)):
            return c
    except (OSError, ValueError):
        pass
    return None


def _cache_scrivi(chiave: str, risultato: Dict[str, Any]) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"chiave": chiave, "salvato_ts": time.time(), "risultato": risultato}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(chiave))
    except OSError:
        pass  # la cache e' un'ottimizzazione: senza, la prossima chiamata rilegge la fonte


# ============================================================
# RISOLUZIONE DEL NOME NASDAQ (seguito main 05/10)
# ============================================================
# MISURA 05/10: il chiamante ha il nome LEGALE («AB Acme», «Zeta Oyj»), Nasdaq ne usa un altro
# («Acme, AB», «Zeta»). Ricerca per ISIN: solo api.nasdaq.com/api/nordic/search (1 richiesta: da'
# «Acme B», non il nome delle news) e il suo robots.txt vieta tutto -> non usata. Resta la ricerca a
# testo libero della stessa API news (freeText=): i nomi degli emittenti nelle righe si confrontano
# NORMALIZZATI col nome dato. Unica corrispondenza sul mercato del suffisso -> nome risolto (cache
# TTL_RISOLUZIONE_S); piu' d'una -> ambiguo; nessuna -> non_trovato. Mai prefissi.
TTL_RISOLUZIONE_S = 30 * 24 * 3600
_FORME_SOCIETARIE = {"ab", "publ", "aktiebolag", "aktiebolaget", "as", "asa", "oyj", "abp", "oy", "plc",
                     "corporation", "corp", "aps", "hf", "hf.", "se", "nv", "sa", "ltd", "limited", "inc"}
# in TESTA solo le forme svedesi che vi si scrivono («AB Acme»): «SE Banken» resta «se banken» (RV-UE2 P3-5)
_FORME_IN_TESTA = {"ab", "aktiebolaget"}


def normalizza_nome(nome: Any) -> str:
    """Nome confrontabile: minuscole, «A/S» -> forma, punteggiatura via, forme societarie via,
    spazi compattati. «Acme, AB» e «AB Acme» -> «acme»; «Zeta Oyj» -> «zeta». L'ordine delle
    altre parole resta: «Acme Car AB» -> «acme car» (diverso). Le forme si tolgono SOLO in coda
    (e AB/Aktiebolaget in testa), mai in mezzo. Limite: «X AB» e «X ASA» coincidono (il filtro di
    mercato del suffisso li separa quasi sempre)."""
    s = _norm(nome).lower().replace("a/s", " as ")
    parole = re.sub(r"[^\w\s]", " ", s).split()
    while parole and parole[0] in _FORME_IN_TESTA:
        parole.pop(0)
    while parole and parole[-1] in _FORME_SOCIETARIE:
        parole.pop()
    return " ".join(parole)


def risolvi_nome(righe: List[Dict[str, Any]], nome_dato: str, paese: str,
                 totale: Optional[int] = None) -> Dict[str, Any]:
    """Puro: {"stato": ok|ambiguo|non_trovato|troncata, "nome_nasdaq", "motivo"} dai nomi degli
    emittenti nelle righe della ricerca a testo libero, solo sul mercato del suffisso. `totale` =
    count della fonte: se supera le righe lette la ricerca e' TRONCATA e lo si scrive; nessuna
    corrispondenza in una ricerca troncata = 'troncata' (KO dichiarato), non non_trovato."""
    tronca = ("ricerca troncata: %d comunicati letti di %d" % (len(righe), totale)
              if totale is not None and totale > len(righe) else None)
    chiave = normalizza_nome(nome_dato)
    sul_mercato = sorted({_norm(r["emittente"]) for r in righe if _MERCATO[paese].search(r["mercato"] or "")})
    uguali = [n for n in sul_mercato if chiave and normalizza_nome(n) == chiave]
    if len(uguali) == 1:
        return {"stato": "ok", "nome_nasdaq": uguali[0],
                "motivo": "nome Nasdaq %r risolto dal nome dato %r (confronto normalizzato %r, unica "
                          "corrispondenza su %d emittenti del mercato %s nella ricerca%s)"
                          % (uguali[0], nome_dato, chiave, len(sul_mercato), paese,
                             "; " + tronca if tronca else "")}
    if len(uguali) > 1:
        return {"stato": "ambiguo", "nome_nasdaq": None,
                "motivo": "nome dato %r: %d emittenti Nasdaq con lo stesso nome normalizzato (%s): "
                          "identita' da confermare" % (nome_dato, len(uguali), "; ".join(uguali))}
    if tronca:
        return {"stato": "troncata", "nome_nasdaq": None,
                "motivo": "nome dato %r (normalizzato %r) non trovato fra gli emittenti del mercato %s, ma la "
                          "%s: verdetto sospeso, serve il nome ESATTO Nasdaq" % (nome_dato, chiave, paese, tronca)}
    return {"stato": "non_trovato", "nome_nasdaq": None,
            "motivo": "nome dato %r non coincide (normalizzato %r) con nessun emittente del mercato %s "
                      "trovato dalla ricerca a testo libero (visti: %s); serve il nome ESATTO Nasdaq"
                      % (nome_dato, chiave, paese, "; ".join(sul_mercato[:8]) or "nessuno")}


def _prendi(out: Dict[str, Any], url: str, etichetta: str) -> Optional[Dict[str, Any]]:
    """Una richiesta: risposta parsata (salvata con sha256 in out) o None con l'errore in out."""
    try:
        http, corpo = _scarica(url)
    except URLVietato as e:
        out.update(stato="KO", errore="url_vietato", motivo="URL rifiutato prima della rete: %s" % e)
        return None
    except Exception as e:
        out.update(stato="KO", errore="rete", motivo="richiesta a Nasdaq fallita (%s): %s" % (etichetta, type(e).__name__))
        return None
    if http != 200:
        out.update(stato="KO", errore="http", motivo="HTTP %s da Nasdaq (%s; i redirect non si seguono)" % (http, etichetta))
        return None
    p = parse_risposta(corpo)
    if p["stato"] != "ok":
        out.update(stato="KO", errore=p["errore"], motivo="%s: %s" % (etichetta, p["motivo"]))
        return None
    out["url_liste"].append(url)
    out["sha256_liste"][url] = hashlib.sha256(corpo).hexdigest()
    out["risposte_salvate"][url] = corpo.decode("utf-8")
    return p


def _risolvi_e_leggi(out: Dict[str, Any], nome_dato: str, tipo: str, fine: date, paese: str) -> Dict[str, Any]:
    """Nome dato -> nome Nasdaq -> comunicati. 1) risoluzione in cache (30 gg); 2) il nome dato come
    nome ESATTO; 3) se la fonte non lo conosce, ricerca a testo libero + confronto normalizzato."""
    chiave_ris = "|".join(("risoluzione", FONTE_MODULO, paese, normalizza_nome(nome_dato)))
    c = _cache_leggi(chiave_ris)
    if c is not None and 0 <= time.time() - c["salvato_ts"] < TTL_RISOLUZIONE_S:
        r = c["risultato"]
        out["url_liste"].append(r["url"])
        out["sha256_liste"][r["url"]] = r["sha256"]
        out["risposte_salvate"][r["url"]] = r["corpo"]
        out.update(nome_nasdaq=r["nome_nasdaq"],
                   instradamento="%s; ricerca del %s (cache %d gg)" % (r["motivo"], r["letto_il"],
                                                                    TTL_RISOLUZIONE_S // 86400))
        return _leggi(out, r["nome_nasdaq"], tipo, fine, paese)
    _leggi(out, nome_dato, tipo, fine, paese)
    if not (out["stato"] == "non_trovato" and out["errore"] == "nome_non_trovato"):
        if out["stato"] != "KO":
            out.update(nome_nasdaq=nome_dato, instradamento="nome dato = nome esatto Nasdaq (riscontrato nella fonte)")
        return out
    # il nome dato non e' un nome Nasdaq: si azzera il verdetto e si risolve
    out.update(stato="KO", errore=None, motivo=None, candidati=[], conferme=[], scartati=[], prova=None)
    testo = normalizza_nome(nome_dato)
    if not testo:
        out.update(stato="non_trovato", errore="nome_non_trovato",
                   motivo="nome dato %r fatto solo di forme societarie: niente da cercare" % nome_dato)
        return out
    url = url_pagina("", 0, testo)
    p = _prendi(out, url, "ricerca a testo libero")
    if p is None:
        return out
    out["pagine_lette"] += 1
    ris = risolvi_nome(p["righe"], nome_dato, paese, p["totale"])
    out["limiti"] = out["limiti"] + ["ricerca a testo libero: solo i %d comunicati piu' recenti che citano %r "
                                     "(un emittente che pubblica poco puo' non comparire: non_trovato dichiarato)"
                                     % (LIMITE_PAGINA, testo)]
    if ris["stato"] == "ambiguo":
        out.update(stato="ambiguo", errore="nome_ambiguo", motivo=ris["motivo"])
        return out
    if ris["stato"] == "troncata":
        out.update(stato="KO", errore="ricerca_troncata", motivo=ris["motivo"])
        return out
    if ris["stato"] != "ok" or ris["nome_nasdaq"] == nome_dato:
        out.update(stato="non_trovato", errore="nome_non_trovato", motivo=ris["motivo"])
        return out
    _cache_scrivi(chiave_ris, {"nome_nasdaq": ris["nome_nasdaq"], "motivo": ris["motivo"], "url": url,
                               "sha256": out["sha256_liste"][url], "corpo": out["risposte_salvate"][url],
                               "letto_il": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    out.update(nome_nasdaq=ris["nome_nasdaq"], instradamento=ris["motivo"])
    return _leggi(out, ris["nome_nasdaq"], tipo, fine, paese)


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str] = None,
                      lei: Optional[str] = None, nome: Optional[str] = None,
                      paese: Optional[str] = None) -> Dict[str, Any]:
    """Data/ora UTC di diffusione su Nasdaq Nordic della relazione `tipo` del periodo che finisce
    il `periodo_fine`. `nome` = nome dell'emittente dall'identita' confermata del chiamante (nome
    esatto Nasdaq o nome legale: v. _risolvi_e_leggi): assente -> KO 'identita_mancante' senza rete.
    Cache 12 h solo per gli ok; KO con una lettura ok scaduta -> STALE dichiarato.
    `paese` (AGGIUNTA 4): il paese su cui instrada il router (FI/SE/DK) comanda sul suffisso; senza
    paese si usa il suffisso. Paese non servito da Nasdaq Nordic o non ricavabile = KO 'identita_mancante'."""
    paese_chiesto = paese
    paese = paese if paese is not None else paese_da_ticker(ticker)
    paese_errato = paese
    if paese not in FUSI_LISTINO:
        paese = None
    if tipo not in _TIPI:
        out = _base(ticker, tipo, periodo_fine, isin, lei, nome, paese)
        out.update(errore="parametro", motivo="tipo %r non ammesso: %s" % (tipo, ", ".join(_TIPI)))
        return out
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        out = _base(ticker, tipo, periodo_fine, isin, lei, nome, paese)
        out.update(errore="parametro", motivo="periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,))
        return out
    out = _base(ticker, tipo, fine, isin, lei, nome, paese)
    incoerente = coerenza_tipo_periodo(tipo, fine)
    if incoerente:
        out.update(errore="parametro", motivo=incoerente)
        return out
    if paese is None:
        out.update(errore="identita_mancante",
                   motivo=("paese %r non servito da Nasdaq Nordic (FI, SE, DK): mercato dell'emittente non "
                           "ricavabile" % (paese_errato,)) if paese_chiesto is not None else
                          ("mercato Nasdaq non ricavabile: il ticker %r non ha un suffisso %s e il chiamante non "
                           "ha passato 'paese'" % (out["ticker"], "/".join(SUFFISSI))))
        return out
    if not isinstance(nome, str) or not nome.strip():
        out.update(errore="identita_mancante",
                   motivo="serve il campo 'nome': il nome ESATTO dell'emittente in Nasdaq Nordic (es. "
                          "«Acme, AB»), confermato dal chiamante; non si deduce dal simbolo")
        return out
    nome = _norm(nome)
    out["nome"] = nome
    chiave = "|".join((FONTE_MODULO, paese, nome, tipo, fine.isoformat(), str(isin), str(lei)))
    c = _cache_leggi(chiave)
    eta = (time.time() - c["salvato_ts"]) if c is not None else None
    if c is not None and eta is not None and 0 <= eta < TTL_S:
        r = dict(c["risultato"])
        r.update(ticker=out["ticker"], isin=isin, lei=lei, cache="fresca")   # identita' di QUESTA chiamata
        return r
    nuovo = _risolvi_e_leggi(out, nome, tipo, fine, paese)
    if nuovo["stato"] == "ok":
        _cache_scrivi(chiave, {k: v for k, v in nuovo.items() if k != "cache"})
        return nuovo
    if nuovo["stato"] == "KO" and c is not None:
        vecchio = dict(c["risultato"])
        vecchio.update(ticker=out["ticker"], isin=isin, lei=lei, stato="STALE", errore=nuovo["errore"], cache="scaduta",
                       motivo="fonte in guasto (%s): servita l'ultima lettura ok del %s, eta' %s"
                              % (nuovo["motivo"], vecchio.get("letto_il"),
                                 "%.0f s" % eta if eta is not None and eta >= 0 else "non misurabile"))
        return vecchio
    return nuovo


# ============================================================
# RIVERIFICA SENZA RETE
# ============================================================
def riverifica_ricevuta(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        paese: Optional[str] = None) -> Tuple[bool, str]:
    """Ricalcola la scelta dalle `risposte_salvate` (nessuna rete): sha256 di ogni corpo contro
    `sha256_liste`, stesso emittente/tipo/periodo, stesso stato e stessi data/ora/titolo/url."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un dizionario"
    if ricevuta.get("fonte_modulo") != FONTE_MODULO:
        return False, "ricevuta di un altro modulo (%r)" % ricevuta.get("fonte_modulo")
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return False, "periodo_fine %r non e' una data" % (periodo_fine,)
    if (ticker or "").strip().upper() != ricevuta.get("ticker") or tipo != ricevuta.get("tipo") or \
            fine.isoformat() != ricevuta.get("periodo_fine"):
        return False, "ticker/tipo/periodo della ricevuta diversi da quelli chiesti"
    if paese is not None and paese != ricevuta.get("paese"):
        return False, "paese chiesto %r diverso da quello della ricevuta %r" % (paese, ricevuta.get("paese"))
    paese = ricevuta.get("paese") if ricevuta.get("paese") in FUSI_LISTINO else None
    if paese is None:
        return False, "paese della ricevuta %r non servito da Nasdaq Nordic" % ricevuta.get("paese")
    nome = ricevuta.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return False, "ricevuta senza paese Nasdaq o senza nome dell'emittente"
    if ricevuta.get("stato") not in ("ok", "STALE", "ambiguo", "non_trovato"):
        return False, "stato %r: nessuna scelta da riverificare" % ricevuta.get("stato")
    url_liste = ricevuta.get("url_liste") or []
    salvate = ricevuta.get("risposte_salvate") or {}
    impronte = ricevuta.get("sha256_liste") or {}
    if not url_liste:
        return False, "ricevuta senza url_liste"
    nome_dato = _norm(nome)
    nome_nq = ricevuta.get("nome_nasdaq") or nome_dato
    lette: Dict[str, Dict[str, Any]] = {}
    for i, url in enumerate(url_liste):
        ok, mot = url_consentito(url)
        if not ok:
            return False, "url_liste[%d] non ammesso: %s" % (i, mot)
        corpo = salvate.get(url)
        if not isinstance(corpo, str):
            return False, "risposta salvata mancante per %s" % url
        if hashlib.sha256(corpo.encode("utf-8")).hexdigest() != impronte.get(url):
            return False, "sha256 della risposta salvata diverso dalla ricevuta (%s)" % url
        p = parse_risposta(corpo)
        if p["stato"] != "ok":
            return False, "risposta salvata illeggibile: %s" % p["motivo"]
        lette[url] = p
    pagine = []
    while url_pagina(nome_nq, len(pagine) * LIMITE_PAGINA) in lette:
        pagine.append(lette[url_pagina(nome_nq, len(pagine) * LIMITE_PAGINA)])
    if nome_nq != nome_dato:
        # la risoluzione si rifa' dalla ricerca salvata e deve dare lo stesso nome, in modo univoco;
        # il tentativo col nome dato (se c'e') non deve contenere righe di quel nome esatto
        ricerca = url_pagina("", 0, normalizza_nome(nome_dato))
        if ricerca not in lette:
            return False, "nome Nasdaq %r diverso dal nome dato senza la ricerca che lo risolve" % nome_nq
        ris = risolvi_nome(lette[ricerca]["righe"], nome_dato, paese, lette[ricerca]["totale"])
        if ris["stato"] != "ok" or ris["nome_nasdaq"] != nome_nq:
            return False, "risoluzione ricalcolata %s/%r diversa da %r" % (ris["stato"], ris["nome_nasdaq"], nome_nq)
        prima = url_pagina(nome_dato, 0)
        if prima in lette and any(_norm(r["emittente"]) == nome_dato for r in lette[prima]["righe"]):
            return False, "il nome dato %r era gia' un nome esatto Nasdaq: risoluzione non dovuta" % nome_dato
    ricerca_dato = url_pagina("", 0, normalizza_nome(nome_dato))
    if not ricevuta.get("nome_nasdaq") and ricerca_dato in lette:
        # risoluzione fallita (ambiguo / non_trovato): si rifa' e si confronta lo stato
        ris = risolvi_nome(lette[ricerca_dato]["righe"], nome_dato, paese, lette[ricerca_dato]["totale"])
        if ris["stato"] != ricevuta.get("stato"):
            return False, "risoluzione ricalcolata %r diversa dallo stato della ricevuta %r" % (
                ris["stato"], ricevuta.get("stato"))
        return True, "stessa risoluzione del nome (%s) ricalcolata dalle risposte salvate" % ris["stato"]
    if not pagine:
        return False, "nessuna pagina dei comunicati di %r fra le risposte salvate" % nome_nq
    if not pagine_complete(pagine, fine):
        return False, "le pagine salvate non arrivano alla fine del periodo"
    v = scegli([r for p in pagine for r in p["righe"]], nome=nome_nq, tipo=tipo, fine=fine, paese=paese)
    # in cache vanno solo gli ok: una ricevuta STALE e' una lettura ok servita dopo un guasto
    stato_ric = "ok" if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    if v["stato"] != stato_ric:
        return False, "stato ricalcolato %r diverso da quello della ricevuta %r" % (v["stato"], stato_ric)
    if v["stato"] == "ambiguo":
        # AGGIUNTA 2: per gli ambigui si ricalcola anche la LISTA dei candidati
        def _insieme(cc):
            return {(c.get("titolo"), c.get("data"), c.get("ora"), c.get("url")) for c in cc or []}
        if _insieme(v["candidati"]) != _insieme(ricevuta.get("candidati")):
            return False, "candidati ricalcolati (%d) diversi da quelli della ricevuta (%d)" % (
                len(v["candidati"]), len(ricevuta.get("candidati") or []))
        return True, "stesso verdetto ambiguo e stessi %d candidati ricalcolati" % len(v["candidati"])
    if v["stato"] != "ok":
        return True, "stesso verdetto (%s) ricalcolato dalle risposte salvate" % v["stato"]
    s = v["scelto"]
    for campo, mio in (("data_deposito", s["data"]), ("ora_deposito", s["ora"]), ("titolo", s["titolo"]),
                       ("url", s["url"]), ("fuso", "UTC"), ("natura_data", "diffusione"), ("prova", s["prova"]),
                       ("url_documento", url_documento(s["allegati"]))):
        if ricevuta.get(campo) != mio:
            return False, "%s della ricevuta %r diverso dal ricalcolo %r" % (campo, ricevuta.get(campo), mio)
    return True, "scelta ricalcolata e identica (sha256 delle %d risposte verificati)" % len(lette)


if __name__ == "__main__":  # python -m bellomberg.market_data.ue_nasdaq_nordic TICKER TIPO AAAA-MM-GG "NOME"
    import sys
    a = sys.argv[1:]
    r = get_data_deposito(a[0], tipo=a[1], periodo_fine=a[2], nome=a[3])
    r.pop("risposte_salvate", None)
    print(json.dumps(r, ensure_ascii=False, indent=2))
