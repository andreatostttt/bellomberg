# -*- coding: utf-8 -*-
"""ue_afm.py — Paesi Bassi (.AS): data di DEPOSITO presso l'AFM delle relazioni finanziarie
periodiche, dal registro pubblico «Financiële verslaggeving» (handoff-3, EU-NLES, 05/10/2026, Opus 5.5).

CONTRATTO: scratchpad INTERFACCIA_UE.md (firma comune dei moduli UE + AGGIUNTA 05/10).
    get_data_deposito(ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None) -> dict
    riverifica_ricevuta(ricevuta, *, ticker, tipo, periodo_fine) -> (ok, motivo)

DECISIONE PM 05/10: l'AFM pubblica SOLO la data di DEPOSITO presso l'autorita', non quella di
diffusione al mercato. Accettata con etichetta: `natura_data = 'deposito_autorita'` e, nel motivo e
nei `limiti`, «data di deposito AFM, non di diffusione: puo' essere settimane dopo la pubblicazione».
Va solo verso il rifiuto (data piu' tarda), mai verso la falsa ammissione.

MISURE (U1 05/10/2026 + sonda EU-NLES, poche richieste):
  - robots.txt di www.afm.nl: `Allow: /`, vietato solo `/sitecore` -> export e pagine registro ok.
  - export completo del registro: `export.aspx?type=e8825b05-...&format=xml` (~4 MB, ~9.700 righe,
    TUTTI gli emittenti dal 2008): <vermelding> con <id>, <datum> («10/1/2026 11:43:25 AM», formato
    USA, SENZA fuso), <uitgevende-instelling> (solo il NOME), <boekjaar>, <filename>, <objecttype>
    (NL) e <objecttype_eng>: Annual financial report | Half-yearly financial report | Interim
    management statement (+ tipi non periodici). NIENTE ISIN, LEI ne' periodo: c'e' solo il nome.
  - la pagina del registro linka ogni voce a `.../financiele-verslaggeving/details?id=<id>`: e' il
    nostro `url` (pattern misurato, la pagina non si scarica).
  - omonimi veri: una «X N.V.» e la sua «X Holding N.V.» depositano lo stesso giorno.

REGOLE:
  - IDENTITA': il nome arriva dal chiamante (identita' confermata). Confronto ESATTO sul nome
    normalizzato (maiuscole, accenti, punteggiatura); nessun nome esatto ma nomi che lo contengono
    = `ambiguo` (omonimi) con la lista; ISIN/LEI non servono a questa fonte (dichiarato).
  - NOME del documento = tipo del registro (`objecttype_eng`); PERIODO = il nome del FILE (il
    «titolo» di questa fonte): data ESEF «AAAA-MM-GG» uguale a periodo_fine, o l'anno del periodo
    (anche «1H25», «HY 2024»). Anno/data diversi -> scartato col motivo. Nessun anno nel nome ->
    prova='finestra' solo se il deposito cade dopo periodo_fine entro FINESTRA_GIORNI (AGGIUNTA
    05/10; la finestra parte dalla data di DEPOSITO, che puo' essere tarda: dichiarato).
  - il nome del file che dice il tipo OPPOSTO (es. «HY» per un annuale) -> scartato: la categoria
    la sceglie l'emittente.
  - `boekjaar` del registro diverso dall'esercizio del periodo -> scartato.
  - piu' candidati = `ambiguo`, mai il primo.
  - rete: un solo export condiviso da tutti gli emittenti, in cache TTL_EXPORT_S; la fonte NON offre
    filtri per emittente o data, quindi niente lettura incrementale: l'export si rilegge intero
    allo scadere della cache (dichiarato in `limiti`). Guasto con cache scaduta -> STALE.
"""
import base64
import gzip
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bellomberg.core.paths import DATA_DIR

FONTE = "AFM registro Financiele verslaggeving (Paesi Bassi)"
FONTE_MODULO = "bellomberg.market_data.ue_afm"
PAESE = "NL"
HOST = "www.afm.nl"
URL_EXPORT = "https://www.afm.nl/export.aspx?type=e8825b05-4004-4301-b736-651e8c61053d&format=xml"
URL_DETTAGLIO = "https://www.afm.nl/nl-nl/sector/registers/meldingenregisters/financiele-verslaggeving/details?id={id}"
USER_AGENT = "Bellomberg/1.0 (ricerca personale, basso volume)"
PAUSA_S = 2.0                 # pausa minima fra due richieste ad afm.nl
TIMEOUT_S = 60
TTL_EXPORT_S = 12 * 3600
CACHE_DIR = str(DATA_DIR / "cache_fonti_ue")
FINESTRA_GIORNI = {"annuale": 200, "semestrale": 150, "trimestrale": 120}
TIPI = ("annuale", "semestrale", "trimestrale")
# tipo del registro AFM (= NOME del documento in questa fonte)
# NOMI pubblici dei documenti (AGGIUNTA 2.1, letti da depositi_ue.nomi_documento): EN + NL, regex con re.I.
# In questa fonte il nome del documento e' il TIPO del registro (TIPO_REGISTRO): le regex lo riconoscono
# (provato nei test) e coprono i nomi usuali che stanno in copertina.
NOMI_DOCUMENTO = {
    # (?<!half): «Halfjaarlijkse ...», «halfjaarverslag», «halfjaarbericht» CONTENGONO il nome annuale
    # (caso trovato dal test sui nomi olandesi del registro, 05/10)
    "annuale": [r"annual\s+financial\s+report", r"(?<!half)jaarlijkse\s+financi[eë]le\s+verslaggeving",
                r"(?<!semi-)(?<!semi\s)annual\s+report", r"(?<!half)jaarverslag", r"(?<!half)jaarrekening",
                r"(?<!half)jaarbericht"],
    "semestrale": [r"half[\s-]*year(?:ly)?\s+(?:financial\s+)?report", r"halfjaarlijkse\s+financi[eë]le\s+verslaggeving",
                   r"halfjaarbericht", r"halfjaarverslag", r"halfjaarcijfers", r"semi[\s-]*annual\s+(?:financial\s+)?report",
                   r"interim\s+(?:financial\s+)?report"],
    "trimestrale": [r"interim\s+management\s+statement", r"tussentijdse\s+verklaring", r"kwartaalbericht",
                    r"quarterly\s+(?:financial\s+)?(?:report|statement)", r"trading\s+update"],
}
# Regola ANNUNCIO DI MESSA A DISPOSIZIONE (main 05/10): non applicabile qui. Il registro AFM contiene i
# DOCUMENTI depositati, non i comunicati che ne annunciano la disponibilita' (dichiarato).
TIPO_REGISTRO = {"annuale": "annual financial report", "semestrale": "half-yearly financial report",
                 "trimestrale": "interim management statement"}

LIMITE_DEPOSITO = ("data di deposito AFM, non di diffusione: puo' essere settimane dopo la pubblicazione "
                   "(misura U1: una semestrale 2026 depositata all'AFM a fine settembre)")
# AGGIUNTA 4.2 (main 05/10): la fonte non dichiara il fuso -> ora_deposito None (l'ora resta in 'ora_registro'),
# fuso del paese dichiarato PROXY; il chiamante usa la fine del giorno locale (prudente)
FUSO = "Europe/Amsterdam"
LIMITE_FUSO = ("fuso dedotto (PROXY): la fonte non lo dichiara; fuso='Europe/Amsterdam' e ora_deposito None (l'ora "
               "del registro, senza fuso, e' in 'ora_registro' solo come informazione)")
LIMITE_EXPORT = ("export completo del registro (~4 MB, tutti gli emittenti) letto per intero al massimo "
                 "una volta ogni 12 h e condiviso: la fonte non offre filtri per emittente o data")
LIMITE_IDENTITA = ("registro solo per NOME emittente (niente ISIN/LEI): nome confrontato esatto in forma canonica "
                   "(accenti e punteggiatura tolti, sigle puntate unite: «N.V.» = «NV»; «AB C» resta diverso da «ABC»)")

_ULTIMA = {"t": None}


class URLVietato(ValueError):
    """URL fuori dagli endpoint ammessi: rifiutato PRIMA della rete."""


# ============================================================
# RETE (solo l'export XML, solo https, nessun redirect seguito)
# ============================================================
_AMMESSI = (re.compile(r"^/export\.aspx\?type=e8825b05-4004-4301-b736-651e8c61053d&format=xml$"),)


def richiesta_consentita(url: str) -> Tuple[bool, str]:
    m = re.match(r"^https://([^/?#]+)(/[^#]*)$", url or "")
    if not m:
        return False, "URL non https o malformato"
    if m.group(1).lower() != HOST:
        return False, "host %s fuori dall'AFM" % m.group(1).lower()
    if m.group(2).lower().startswith("/sitecore"):
        return False, "vietato da robots.txt di www.afm.nl (/sitecore)"
    if not any(r.match(m.group(2)) for r in _AMMESSI):
        return False, "percorso %s fuori dagli endpoint ammessi" % m.group(2).split("?")[0]
    return True, ""


def _richiesta(url: str) -> Tuple[int, bytes]:
    """(HTTP, corpo). Un 3xx torna come codice (chi chiama lo dichiara KO)."""
    ok, motivo = richiesta_consentita(url)
    if not ok:
        raise URLVietato(motivo)
    import requests
    if _ULTIMA["t"] is not None:
        resto = PAUSA_S - (time.monotonic() - _ULTIMA["t"])
        if resto > 0:
            time.sleep(resto)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, allow_redirects=False)
    finally:
        _ULTIMA["t"] = time.monotonic()
    return r.status_code, r.content


# ============================================================
# CACHE DEL CORPO (scrittura atomica, eta' su orologio di parete con controllo)
# ============================================================
def _cache_file(url: str) -> str:
    return os.path.join(CACHE_DIR, "afm_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json")


def _cache_leggi(url: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_file(url), encoding="utf-8") as fh:
            c = json.load(fh)
        if isinstance(c, dict) and isinstance(c.get("corpo"), str) and isinstance(c.get("salvato_ts"), (int, float)):
            return c
    except (OSError, ValueError):
        pass
    return None


def _cache_scrivi(url: str, corpo: str) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"salvato_ts": time.time(), "url": url, "corpo": corpo}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_file(url))
    except OSError:
        pass  # la cache e' un'ottimizzazione: senza, la prossima chiamata rilegge la fonte


def _decodifica(corpo: bytes) -> str:
    try:
        return corpo.decode("utf-8")
    except UnicodeDecodeError:
        return corpo.decode("latin-1")


def sha_corpo(corpo: str) -> str:
    """sha256 del corpo SALVATO come testo, ricodificato (utf-8, o latin-1 se non e' codificabile in utf-8).
    Coincide con lo sha dei byte ricevuti quando la risposta era utf-8 valido (il caso misurato); se la
    risposta era latin-1 e' lo sha della ricodifica, non dei byte originali (rilievo RV-UE2 P3-5)."""
    try:
        b = corpo.encode("utf-8")
    except UnicodeEncodeError:
        b = corpo.encode("latin-1")
    return hashlib.sha256(b).hexdigest()


SOGLIA_LIMITE_COMPRESSO = 1_000_000


def comprimi(corpo: str) -> Dict[str, str]:
    """Forma di `risposte_salvate[url]` per l'export (accordo main 05/10): il corpo INTERO, gzip+base64.
    `sha256_liste` resta sul corpo NON compresso (sha_corpo)."""
    return {"codifica": "gzip+base64",
            "corpo": base64.b64encode(gzip.compress(corpo.encode("utf-8"), mtime=0)).decode("ascii")}


def decomprimi(salvata: Any) -> str:
    """Il corpo testuale da una voce di `risposte_salvate` (str semplice o {'codifica','corpo'}).
    Solleva ValueError se la forma non e' riconosciuta."""
    if isinstance(salvata, str):
        return salvata
    if isinstance(salvata, dict) and salvata.get("codifica") == "gzip+base64" and isinstance(salvata.get("corpo"), str):
        return gzip.decompress(base64.b64decode(salvata["corpo"])).decode("utf-8")
    raise ValueError("voce di risposte_salvate in forma sconosciuta")


def _scarica_export() -> Tuple[Optional[str], str, Optional[str], Optional[str]]:
    """(corpo, stato_cache 'fresca'|'scaduta'|'nessuna', errore, motivo). Con cache scaduta e
    fonte in guasto torna il corpo vecchio con stato 'scaduta' e il guasto in errore/motivo."""
    c = _cache_leggi(URL_EXPORT)
    eta = None
    if c is not None:
        eta = time.time() - float(c["salvato_ts"])
        if 0 <= eta < TTL_EXPORT_S:
            return c["corpo"], "fresca", None, None
    try:
        http, corpo = _richiesta(URL_EXPORT)
        errore, motivo = (None, None) if http == 200 else ("http", "HTTP %s dall'export AFM" % http)
    except URLVietato as e:
        return None, "nessuna", "url_vietato", "%s" % type(e).__name__
    except Exception as e:
        http, corpo, errore, motivo = None, b"", "rete", "export AFM non letto (%s)" % type(e).__name__
    if errore is None:
        testo = _decodifica(corpo)
        if "<register" not in testo[:200]:
            errore, motivo = "formato", "l'export AFM non e' piu' l'XML <register> misurato"
        else:
            _cache_scrivi(URL_EXPORT, testo)
            return testo, "nessuna", None, None
    if c is not None:
        return c["corpo"], "scaduta", errore, "%s; servito l'export in cache di %s fa" % (
            motivo, ("%.0f s" % eta) if eta is not None and eta >= 0 else "eta' non misurabile")
    return None, "nessuna", errore, motivo


# ============================================================
# PARSING E REGOLA (puri, senza rete)
# ============================================================
def norm_nome(s: Optional[str]) -> str:
    """Nome confrontabile: senza accenti, minuscolo, punteggiatura -> spazio («N.V.» = «NV» no:
    «n v»; il confronto e' fra nomi normalizzati allo stesso modo)."""
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def _data_ora(datum: str) -> Tuple[Optional[str], Optional[str]]:
    """«10/1/2026 11:43:25 AM» (mese/giorno USA) -> ('2026-10-01', '11:43'). Formato diverso -> (None, None)."""
    try:
        d = datetime.strptime((datum or "").strip(), "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return None, None
    return d.date().isoformat(), d.strftime("%H:%M")


def parse_export(xml: str) -> List[Dict[str, Any]]:
    """Righe del registro. Solleva ValueError se l'XML non e' quello misurato. DOCTYPE/ENTITY
    rifiutati prima del parser (l'export misurato non ne ha: niente espansione di entita')."""
    if re.search(r"<!(?:DOCTYPE|ENTITY)", xml, re.I):
        raise ValueError("DOCTYPE/ENTITY nell'export: rifiutato")
    root = ET.fromstring(xml)
    if root.tag != "register":
        raise ValueError("radice %s, attesa register" % root.tag)
    out = []
    for v in root.findall("vermelding"):
        d, o = _data_ora(v.findtext("datum") or "")
        out.append({"id": (v.findtext("id") or "").strip(), "data": d, "ora": o,
                    "datum_grezzo": v.findtext("datum"),
                    "emittente": (v.findtext("uitgevende-instelling") or "").strip(),
                    "boekjaar": (v.findtext("boekjaar") or "").strip(),
                    "titolo": (v.findtext("filename") or "").strip(),
                    "categoria": (v.findtext("objecttype_eng") or "").strip(),
                    "categoria_nl": (v.findtext("objecttype") or "").strip()})
    return out


def _nome_file_pulito(f: str) -> str:
    """Il nome del file senza estensione ne' suffisso d'archivio AFM («-A2510-03905»), in minuscolo,
    con la punteggiatura a spazio e cifre/lettere separate («1H2025» -> «1 h 2025»)."""
    t = re.sub(r"\.[a-z0-9]{2,4}$", "", (f or "").lower())
    t = re.sub(r"-a\d{4}-\d{5}$", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", t)
    return " ".join(t.split())


_SEGNO = {
    "semestrale": re.compile(r"\b(?:h 1|1 h|hy|hj|half|semi|halfjaar\w*|halfjaarbericht\w*|six months)\b"),
    "annuale": re.compile(r"(?<!semi )\b(?:annual|jaarverslag\w*|jaarrekening\w*|jaarbericht\w*|fy)\b"),
    "trimestrale": re.compile(r"\b(?:q [1-4]|[1-4] q|quarter\w*|kwartaal\w*|tussentijdse|trading update)\b"),
}
# trimestre NUMERATO nel nome del file: «Q2», «2Q», «second quarter»: il Q2 finisce il 30/06 (= la
# semestrale), il Q4 il 31/12. Misura sull'export vero 05/10: diversi emittenti depositano la
# semestrale col nome «Q2 2026»: non e' un altro documento.
_TRIMESTRE_N = re.compile(r"\bq ([1-4])\b|\b([1-4]) q\b|\b(first|second|third|fourth|1st|2nd|3rd|4th) quarter")
_ORDINALI = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}


def periodo_dal_nome(nome_file: str) -> Dict[str, Any]:
    """{'date': [AAAA-MM-GG di FINE MESE], 'anni': {int}, 'segni': {tipo}, 'trimestri': {1..4}} letti
    dal nome del file. Una data che non e' fine mese non e' un periodo (es. «2026-07-28 - ... Q2
    results»: data del comunicato, misurata sull'export vero) e non conta come data."""
    grezzo = (nome_file or "").lower()
    date_esef = []
    for a, m, g in re.findall(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)", grezzo):
        try:
            d = date(int(a), int(m), int(g))
        except ValueError:
            continue
        if (d + timedelta(days=1)).day == 1:
            date_esef.append(d.isoformat())
    t = _nome_file_pulito(nome_file)
    trimestri = set()
    for a, b, c in _TRIMESTRE_N.findall(t):
        trimestri.add(int(a or b) if (a or b) else _ORDINALI[c])
    anni = {int(a) for a in re.findall(r"\b(20\d{2})\b", t)}
    anni |= {2000 + int(a) for a in re.findall(r"\b(?:h [12]|[12] h|hy|fy|q [1-4])\s?(\d{2})\b", t)}
    segni = {k for k, rx in _SEGNO.items() if rx.search(t)}
    return {"date": date_esef, "anni": anni, "segni": segni, "trimestri": trimestri}


def _esercizi_ammessi(tipo: str, fine: date) -> set:
    """`boekjaar` compatibili col periodo (esercizio NON solare: anche l'anno vicino, dichiarato)."""
    if tipo == "annuale" and fine.month != 12:
        return {fine.year, fine.year - 1}
    if tipo == "semestrale" and fine.month != 6:
        return {fine.year, fine.year + 1}
    if tipo == "trimestrale" and fine.month not in (3, 9):
        return {fine.year, fine.year + 1}
    return {fine.year}


def coerenza_tipo_periodo(tipo: str, fine: date) -> Optional[str]:
    """Stessa regola di emarket_sdir.coerenza_tipo_periodo (fine mese; trimestrale 31/03-30/09;
    semestrale 30/06 o 31/12)."""
    if (fine + timedelta(days=1)).day != 1:
        return "periodo_fine %s non e' l'ultimo giorno di un mese" % fine
    if tipo == "trimestrale" and fine.month not in (3, 9):
        return "trimestrale al %s incoerente: la trimestrale e' al 31/03 o al 30/09" % fine.strftime("%d/%m")
    if tipo == "semestrale" and fine.month not in (6, 12):
        return "semestrale al %s incoerente: la semestrale e' al 30/06 (al 31/12 solo per esercizi non solari)" \
               % fine.strftime("%d/%m")
    return None


def nome_canonico(s: Optional[str]) -> str:
    """Nome confrontabile (rilievo RV-UE2 P3-4): normalizzato, poi le SEQUENZE di 2+ lettere singole si
    uniscono («N.V.» -> «n v» -> «nv» = «NV»; «S.A.» = «SA»). Una lettera ISOLATA resta parola: «AB C» non
    diventa «ABC» (nomi diversi non si fondono). Limite (RV-UE2): una lettera isolata subito prima di una sigla
    si unisce alla sigla («AB C N.V.» -> «ab cnv»): l'errore va verso il NON match (sicuro), dichiarato."""
    out, corsa = [], []
    for t in norm_nome(s).split():
        if len(t) == 1:
            corsa.append(t)
            continue
        if corsa:
            out.append("".join(corsa))
            corsa = []
        out.append(t)
    if corsa:
        out.append("".join(corsa))
    return " ".join(out)


def righe_emittente(righe: List[Dict[str, Any]], nome: str) -> Dict[str, Any]:
    """{'stato': 'ok'|'ambiguo'|'non_trovato', 'righe', 'nomi', 'motivo'}: nome ESATTO normalizzato;
    senza esatti, i nomi che contengono (o sono contenuti in) quello cercato sono omonimi -> ambiguo."""
    n = nome_canonico(nome)
    # uguaglianza sulla forma CANONICA («N.V.» = «NV», ma «AB C» != «ABC»), contenimento sulle parole
    esatte = [r for r in righe if n and nome_canonico(r["emittente"]) == n]
    if esatte:
        return {"stato": "ok", "righe": esatte, "nomi": [esatte[0]["emittente"]], "motivo": None}
    vicini = sorted({r["emittente"] for r in righe
                     if n and (re.search(r"\b%s\b" % re.escape(n), nome_canonico(r["emittente"]))
                               or re.search(r"\b%s\b" % re.escape(nome_canonico(r["emittente"])), n))
                     and nome_canonico(r["emittente"])})
    if vicini:
        return {"stato": "ambiguo", "righe": [], "nomi": vicini,
                "motivo": "nome %r non presente identico nel registro AFM; emittenti dal nome simile (omonimi): "
                          "%s. Serve il nome esatto confermato" % (nome, "; ".join(vicini[:10]))}
    return {"stato": "non_trovato", "righe": [], "nomi": [],
            "motivo": "nessun emittente col nome %r nel registro AFM Financiele verslaggeving" % nome}


def scegli(righe: List[Dict[str, Any]], nome: str, tipo: str, fine: date) -> Dict[str, Any]:
    """Verdetto puro: {'stato', 'scelto', 'candidati', 'scartati', 'motivo', 'prova', 'nomi'}."""
    em = righe_emittente(righe, nome)
    if em["stato"] != "ok":
        return {"stato": em["stato"], "scelto": None, "candidati": [], "scartati": [], "prova": None,
                "motivo": em["motivo"], "nomi": em["nomi"]}
    cat = TIPO_REGISTRO[tipo]
    altri = {k for k in _SEGNO if k != tipo}
    esercizi = _esercizi_ammessi(tipo, fine)
    finestra = (fine + timedelta(days=FINESTRA_GIORNI[tipo])).isoformat()
    cand, scartati = [], []

    def _scarta(r, motivo):
        scartati.append({"titolo": r["titolo"], "data": r["data"], "motivo": motivo})

    for r in em["righe"]:
        if r["categoria"].lower() != cat:
            continue
        if r["data"] is None:
            _scarta(r, "data del registro illeggibile (%r)" % r["datum_grezzo"])
            continue
        if r["data"] <= fine.isoformat():
            _scarta(r, "depositato il %s, non dopo la fine del periodo" % r["data"])
            continue
        p = periodo_dal_nome(r["titolo"])
        opposti = p["segni"] & altri
        if tipo != "trimestrale" and "trimestrale" in opposti and p["trimestri"] \
                and all(3 * q == fine.month for q in p["trimestri"]):
            # un trimestre che finisce nel mese del periodo (Q2 = 30/06, Q4 = 31/12) non e' un altro documento
            opposti = opposti - {"trimestrale"}
        if tipo == "trimestrale" and p["trimestri"] and fine.month // 3 not in p["trimestri"]:
            # rilievo RV-UE2 P1-1: «Q3» non e' il trimestre al 31/03 (il numero del trimestre e' il PERIODO)
            _scarta(r, "trimestre nel nome del file (Q%s) diverso dal trimestre del periodo (Q%d)"
                       % ("/Q".join(str(q) for q in sorted(p["trimestri"])), fine.month // 3))
            continue
        if opposti and tipo not in p["segni"]:
            _scarta(r, "il nome del file indica un altro documento (%s), non la %s" % (", ".join(sorted(opposti)), tipo))
            continue
        try:
            bj = int(r["boekjaar"])
        except ValueError:
            bj = None
        if bj not in esercizi:
            _scarta(r, "boekjaar del registro %r diverso dall'esercizio del periodo %s" % (r["boekjaar"], fine))
            continue
        if p["date"]:
            if fine.isoformat() in p["date"]:
                prova = "titolo"
            else:
                _scarta(r, "data nel nome del file %s diversa da %s" % (", ".join(p["date"]), fine))
                continue
        elif p["anni"]:
            if (fine.year in p["anni"]) or (esercizi - {fine.year} and p["anni"] & esercizi):
                prova = "titolo"
            else:
                _scarta(r, "anno nel nome del file %s diverso dal periodo %s" % (sorted(p["anni"]), fine))
                continue
        else:
            if r["data"] > finestra:
                _scarta(r, "nome del file senza anno e deposito oltre %d giorni dalla fine del periodo"
                           % FINESTRA_GIORNI[tipo])
                continue
            prova = "finestra"
        lingua = "en" if re.search(r"(?:^|[^a-z])en(?:[^a-z]|$)", r["titolo"].lower()) else \
            ("nl" if re.search(r"(?:^|[^a-z])nl(?:[^a-z]|$)", r["titolo"].lower()) else None)
        cand.append(dict(r, prova=prova, lingua=lingua))
    if not cand:
        return {"stato": "non_trovato", "scelto": None, "candidati": [], "scartati": scartati, "prova": None,
                "nomi": em["nomi"],
                "motivo": "non trovato nel registro AFM: nessun deposito %r di %s con il periodo %s dopo la fine "
                          "del periodo (righe dell'emittente: %d, scartate: %d)"
                          % (cat, em["nomi"][0], fine, len(em["righe"]), len(scartati))}
    if len(cand) > 1:
        return {"stato": "ambiguo", "scelto": None, "candidati": cand, "scartati": scartati, "prova": None,
                "nomi": em["nomi"],
                "motivo": "%d depositi AFM candidati per lo stesso documento e periodo (%s): nessuno scelto"
                          % (len(cand), ", ".join("%s %s" % (c["data"], c["prova"]) for c in cand))}
    return {"stato": "ok", "scelto": cand[0], "candidati": cand, "scartati": scartati,
            "prova": cand[0]["prova"], "motivo": None, "nomi": em["nomi"]}


# ============================================================
# RITORNO
# ============================================================
def _base(ticker: Any, tipo: Any, periodo_fine: Any, isin: Any, lei: Any, nome: Any) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper(), "isin": isin, "lei": lei, "nome": nome,
            "tipo": tipo, "periodo_fine": periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine,
            "stato": "KO", "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "fuso": FUSO, "natura_data": "deposito_autorita",
            "ora_registro": None,
            "titolo": None, "url": None, "url_documento": None, "sha256_documento": None,
            "categoria": None, "lingua": None, "candidati": [], "conferme": [], "scartati": [], "prova": None,
            "fonte": FONTE, "paese": PAESE, "url_liste": [], "sha256_liste": {}, "risposte_salvate": {},
            "fonte_modulo": FONTE_MODULO, "pagine_lette": 0, "letto_il": None,
            "limiti": [LIMITE_DEPOSITO, LIMITE_FUSO, LIMITE_EXPORT, LIMITE_IDENTITA],
            "cache": None, "stato_originale": None, "protocollo": None, "omonimi": []}


def _candidato_pubblico(c: Dict[str, Any]) -> Dict[str, Any]:
    """Formato UNIFORME dei candidati (AGGIUNTA 2): titolo, data, ora, url, categoria, lingua, prova (+ extra)."""
    return {"titolo": c["titolo"], "data": c["data"], "ora": c["ora"], "url": URL_DETTAGLIO.format(id=c["id"]),
            "categoria": c["categoria"], "lingua": c.get("lingua"), "prova": c.get("prova"),
            "id": c["id"], "boekjaar": c["boekjaar"]}


def chiavi_candidati(candidati: Any) -> set:
    """Insieme confrontabile dei candidati (AGGIUNTA 2.3): (titolo, data, ora, url)."""
    return {(c.get("titolo"), c.get("data"), c.get("ora"), c.get("url")) for c in (candidati or [])
            if isinstance(c, dict)}


def _parametri(tipo: Any, periodo_fine: Any) -> Tuple[Optional[date], Optional[str]]:
    if tipo not in TIPI:
        return None, "tipo %r non ammesso: %s" % (tipo, ", ".join(TIPI))
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return None, "periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,)
    inc = coerenza_tipo_periodo(tipo, fine)
    return (None, inc) if inc else (fine, None)


def _applica(out: Dict[str, Any], corpo: str, nome: str, tipo: str, fine: date) -> Dict[str, Any]:
    """Verdetto sul corpo dell'export -> campi del ritorno (usata anche dalla riverifica)."""
    try:
        righe = parse_export(corpo)
    except (ET.ParseError, ValueError) as e:
        out.update(stato="KO", errore="formato", motivo="export AFM illeggibile (%s)" % type(e).__name__)
        return out
    v = scegli(righe, nome, tipo, fine)
    out.update(stato=v["stato"], motivo=v["motivo"], prova=v["prova"], scartati=v["scartati"],
               candidati=[_candidato_pubblico(c) for c in v["candidati"]])
    if v["stato"] == "ambiguo" and not v["candidati"]:
        # omonimi: i NOMI vanno in `omonimi`, non fra i candidati (che sono solo documenti, formato uniforme)
        out.update(errore="nome_non_univoco", omonimi=list(v["nomi"]))
    s = v["scelto"]
    if s is not None:
        out.update(data_deposito=s["data"], ora_deposito=None, ora_registro=s["ora"], titolo=s["titolo"],
                   url=URL_DETTAGLIO.format(id=s["id"]), categoria=s["categoria"], lingua=s["lingua"],
                   protocollo=s["id"],
                   motivo="%s; deposito del %s alle %s (ora del registro senza fuso: non usata)" % (LIMITE_DEPOSITO, s["data"], s["ora"]))
        if s["prova"] == "finestra":
            out["limiti"].append("periodo NON scritto nel nome del file: candidato per finestra (deposito entro %d "
                                 "giorni dalla fine del periodo); la finestra parte dalla data di DEPOSITO AFM"
                                 % FINESTRA_GIORNI[tipo])
    return out


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str] = None,
                      lei: Optional[str] = None, nome: Optional[str] = None,
                      paese: Optional[str] = None) -> Dict[str, Any]:
    """`paese` (AGGIUNTA 4.1): dal router; questo registro e' solo NL, il valore non cambia la ricerca.
    Data di DEPOSITO AFM della relazione `tipo` al `periodo_fine` dell'emittente di nome `nome`.
    Stati: ok | ambiguo | non_trovato | KO | STALE (non_coperto non serve: il registro e' nazionale)."""
    out = _base(ticker, tipo, periodo_fine, isin, lei, nome)
    fine, err = _parametri(tipo, periodo_fine)
    if err:
        out.update(errore="parametro", motivo=err)
        return out
    out["periodo_fine"] = fine.isoformat()
    if not (nome or "").strip():
        out.update(errore="identita_mancante",
                   motivo="il registro AFM e' interrogabile solo per NOME dell'emittente: serve `nome` "
                          "(nome esatto confermato); ISIN e LEI non sono nel registro")
        return out
    corpo, stato_cache, errore, motivo = _scarica_export()
    if corpo is None:
        out.update(errore=errore, motivo=motivo, letto_il=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return out
    out.update(url_liste=[URL_EXPORT], sha256_liste={URL_EXPORT: sha_corpo(corpo)},
               risposte_salvate={URL_EXPORT: comprimi(corpo)}, pagine_lette=1,
               cache=None if stato_cache == "nessuna" else stato_cache,
               letto_il=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    dim = len(out["risposte_salvate"][URL_EXPORT]["corpo"])
    if dim > SOGLIA_LIMITE_COMPRESSO:
        out["limiti"].append("ricevuta pesante: export intero salvato compresso in risposte_salvate (%d byte "
                             "gzip+base64)" % dim)
    _applica(out, corpo, nome, tipo, fine)
    if stato_cache == "scaduta":
        out.update(stato_originale=out["stato"], stato="STALE", errore=errore,
                   motivo="fonte in guasto (%s); verdetto sull'export in cache: %s" % (motivo, out["motivo"]))
    return out


def riverifica_ricevuta(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        paese: Optional[str] = None) -> Tuple[bool, str]:
    """Senza rete: sha256 dell'export salvato = ricevuta, e la scelta ricalcolata sul corpo salvato
    da' la stessa data/ora/titolo."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un dizionario"
    if ricevuta.get("fonte_modulo") != FONTE_MODULO:
        return False, "ricevuta di un altro modulo (%r)" % ricevuta.get("fonte_modulo")
    # `paese` dal router (AGGIUNTA 4.1, seguito main 05/10): se dato deve coincidere con quello sigillato
    if paese is not None and (paese or "").strip().upper() != (ricevuta.get("paese") or ""):
        return False, "paese %r diverso da quello della ricevuta %r" % (paese, ricevuta.get("paese"))
    fine, err = _parametri(tipo, periodo_fine)
    if err:
        return False, err
    if (ricevuta.get("ticker") or "") != (ticker or "").strip().upper() or ricevuta.get("tipo") != tipo \
            or ricevuta.get("periodo_fine") != fine.isoformat():
        return False, "ticker/tipo/periodo della ricevuta diversi da quelli chiesti"
    salvate = ricevuta.get("risposte_salvate") or {}
    shas = ricevuta.get("sha256_liste") or {}
    if URL_EXPORT not in salvate or URL_EXPORT not in shas:
        return False, "export AFM non salvato nella ricevuta"
    try:
        corpo = decomprimi(salvate[URL_EXPORT])
    except Exception as e:
        return False, "export salvato non decomprimibile (%s)" % type(e).__name__
    if sha_corpo(corpo) != shas[URL_EXPORT]:
        return False, "sha256 dell'export salvato diverso dalla ricevuta"
    rif = _applica(_base(ticker, tipo, fine, ricevuta.get("isin"), ricevuta.get("lei"), ricevuta.get("nome")),
                   corpo, ricevuta.get("nome") or "", tipo, fine)
    stato_ric = ricevuta.get("stato_originale") if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    for k, atteso in (("stato", stato_ric), ("data_deposito", ricevuta.get("data_deposito")),
                      ("ora_deposito", ricevuta.get("ora_deposito")), ("ora_registro", ricevuta.get("ora_registro")),
                      ("titolo", ricevuta.get("titolo"))):
        if rif[k] != atteso:
            return False, "%s ricalcolato %r diverso dalla ricevuta %r" % (k, rif[k], atteso)
    # AGGIUNTA 2.3: anche la LISTA dei candidati (decisiva per gli ambigui) e gli omonimi si ricalcolano
    if chiavi_candidati(rif["candidati"]) != chiavi_candidati(ricevuta.get("candidati")):
        return False, "candidati ricalcolati diversi da quelli della ricevuta"
    if sorted(rif["omonimi"]) != sorted(ricevuta.get("omonimi") or []):
        return False, "omonimi ricalcolati diversi da quelli della ricevuta"
    return True, "ricevuta coerente: %s %s %s" % (rif["stato"], rif["data_deposito"], rif["titolo"])
