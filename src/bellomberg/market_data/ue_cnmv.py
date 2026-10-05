# -*- coding: utf-8 -*-
"""ue_cnmv.py — Spagna (.MC): data di pubblicazione delle relazioni finanziarie periodiche dai
registri ufficiali della CNMV (OAM spagnolo) (handoff-3, EU-NLES, 05/10/2026, Opus 5.5).

CONTRATTO: scratchpad INTERFACCIA_UE.md (firma comune dei moduli UE + AGGIUNTA 05/10).
    get_data_deposito(ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None) -> dict
    riverifica_ricevuta(ricevuta, *, ticker, tipo, periodo_fine) -> (ok, motivo)
Chiavi in piu' (sempre presenti): 'nif', 'identita', 'protocollo', 'stato_originale'.

MISURE (U1 05/10/2026 + sonda EU-NLES 05/10, poche richieste, pausa >= 2 s):
  - robots.txt di www.cnmv.es (riletto dal vivo il 05/10): vieta SOLO `*.shtml`.
  - ANNUALI (IFA): `portal/consultas/ifa/listadoifa?id=0&nif=<NIF>&lang=en`, tabella
    `gridInformes`: «Official Registration No.», «Financial Statements date» (= periodo ESPLICITO),
    «Publication date (1)», link ai documenti (`webservices/verdocumento/ver?e=...`). Nota (1) della
    CNMV: «Date on which the issuer has submitted its annual financial report or, if applicable,
    date of the latest submission in replacement of the initial one» -> natura 'deposito_autorita'
    e puo' essere la data di una SOSTITUZIONE (piu' tarda: dichiarato).
  - SEMESTRALI/TRIMESTRALI (IFI): `portal/consultas/ifi/listaifi?nif=<NIF>&lang=en`, tabella
    `gridEntidades`: data «Date of Publication» + «I half-year of 2026», «II half-year of 2025»,
    «I quarter of 2020», «III quarter of 2020»; ogni riga linka il dettaglio
    `aldia/detalleifialdia.aspx?nreg=<n>` con Inicio/Fin periodo, Semestre, Ejercicio, CIF,
    Publicación (span `lbl*`, indipendenti dalla lingua) e i PDF.
  - Solo la DATA, nessuna ora: ora_deposito None (dichiarato), fuso 'Europe/Madrid' per la data.
  - Il NIF col trattino e' obbligatorio («A48010615» -> nessun risultato su listadoifa).
  - IDENTITA' (accordo main 05/10): il NIF NON si ricava dall'ISIN. Catena verificabile:
    `portal/ANCV/Isin?isin=<ISIN>` (agenzia di codifica, CNMV) -> denominazione dell'emittente;
    ricerca CNMV per denominazione `portal/Consultas/Busqueda?id=6` (form ASP.NET: GET + POST) ->
    302 verso `ListaIFI.aspx?nif=A-...` se l'esito e' unico, altrimenti un <select> con i NIF (senza
    trattino) e le denominazioni. Si accetta SOLO la denominazione identica (normalizzata); poi si
    verifica che la lista letta porti la stessa denominazione e che il CIF del dettaglio sia il NIF.
    Mappa identita' -> NIF in cache TTL_IDENTITA_S.

REGOLE: periodo ESPLICITO dalla fonte (data di bilancio per l'IFA, Fin periodo del dettaglio per
l'IFI); piu' righe valide per lo stesso periodo (es. una sostituzione registrata a parte) = ambiguo;
non_trovato = «non trovato nel registro CNMV», mai «mai pubblicato». Guasto di rete con una lettura
in cache scaduta -> STALE dichiarato.
"""
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

from bellomberg.core.paths import DATA_DIR

FONTE = "CNMV registri ufficiali IFA/IFI (OAM Spagna)"
FONTE_MODULO = "bellomberg.market_data.ue_cnmv"
PAESE = "ES"
HOST = "www.cnmv.es"
BASE = "https://" + HOST
URL_ISIN = BASE + "/portal/ANCV/Isin?isin={isin}"
URL_RICERCA = BASE + "/portal/Consultas/Busqueda?id=6"
URL_IFA = BASE + "/portal/consultas/ifa/listadoifa?id=0&nif={nif}&lang=en"
URL_IFI = BASE + "/portal/consultas/ifi/listaifi?nif={nif}&lang=en"
URL_DETTAGLIO = BASE + "/portal/aldia/detalleifialdia?nreg={nreg}"   # .aspx -> 301 qui (misura 05/10)
USER_AGENT = "Bellomberg/1.0 (ricerca personale, basso volume)"
PAUSA_S = 2.0
TIMEOUT_S = 30
TTL_S = 12 * 3600
TTL_IDENTITA_S = 30 * 24 * 3600
MAX_DETTAGLI = 3              # dettagli IFI letti al massimo per chiamata (oltre: dichiarato)
MAX_RIGHE_IFI = 60            # la lista IFI mostra al massimo 60 relazioni (avviso della pagina, misura 05/10)
FINESTRA_GIORNI = {"annuale": 200, "semestrale": 150, "trimestrale": 120}
CACHE_DIR = str(DATA_DIR / "cache_fonti_ue")
TIPI = ("annuale", "semestrale", "trimestrale")
# NOMI pubblici dei documenti (AGGIUNTA 2.1, letti da depositi_ue.nomi_documento): ES + EN, regex con re.I.
# Il registro CNMV classifica gia' i documenti (IFA / IFI con semestre o trimestre): qui servono a chi
# riconosce il tipo dalla copertina; comprendono le etichette delle liste lette dal modulo (provato nei test).
NOMI_DOCUMENTO = {
    "annuale": [r"informe\s+financiero\s+anual", r"annual\s+financial\s+report", r"cuentas\s+anuales",
                r"informe\s+de\s+gesti[oó]n\s+consolidado"],
    "semestrale": [r"informe\s+(?:financiero\s+)?semestral", r"informaci[oó]n\s+financiera\s+(?:intermedia\s+)?semestral",
                   r"half[\s-]*year(?:ly)?\s+(?:financial\s+)?report", r"\b(?:I|II)\s+half-year\s+of\s+\d{4}"],
    "trimestrale": [r"declaraci[oó]n\s+intermedia", r"informaci[oó]n\s+financiera\s+trimestral",
                    r"interim\s+management\s+statement", r"\b(?:I|III)\s+quarter\s+of\s+\d{4}"],
}
# Regola ANNUNCIO DI MESSA A DISPOSIZIONE (main 05/10): non applicabile qui. I registri IFA/IFI della CNMV
# elencano i DOCUMENTI registrati, non i comunicati che ne annunciano la disponibilita' (dichiarato).

LIMITE_ORA = "la CNMV pubblica solo la DATA (nessuna ora): ora_deposito None, giorno di calendario spagnolo"
LIMITE_IFA = ("annuale: data di INVIO della relazione alla CNMV o, se c'e' stata, dell'ULTIMA versione "
              "sostitutiva (nota (1) della CNMV): puo' essere piu' tarda della prima diffusione")
LIMITE_IDENTITA = ("NIF ricavato dalla catena ISIN -> denominazione (ANCV) -> ricerca CNMV per denominazione; "
                   "verificato: denominazione identica (forma canonica, «S.A.» = «SA») sulla lista letta e CIF nel "
                   "dettaglio; le risposte della catena (ANCV, ricerca) sono nella ricevuta (identita.risposte) e la "
                   "riverifica le ricontrolla senza rete")
LIMITE_TRADUZIONE = "pagine lette in inglese (lang=en); la versione ufficiale dei documenti e' quella spagnola"

_ULTIMA = {"t": None}


class URLVietato(ValueError):
    """URL fuori dagli endpoint ammessi o vietato da robots: rifiutato PRIMA della rete."""


# ============================================================
# RETE
# ============================================================
_NIF = r"[A-Z]-?[0-9]{7}[0-9A-Z]"
_AMMESSI = {
    "GET": (re.compile(r"^/portal/ANCV/Isin\?isin=[A-Z]{2}[A-Z0-9]{9}[0-9]$"),
            re.compile(r"^/portal/Consultas/Busqueda\?id=6$"),
            re.compile(r"^/portal/consultas/ifa/listadoifa\?id=0&nif=%s&lang=en$" % _NIF),
            re.compile(r"^/portal/consultas/ifi/listaifi\?nif=%s&lang=en$" % _NIF),
            re.compile(r"^/portal/aldia/detalleifialdia\?nreg=[0-9]{6,12}$")),
    "POST": (re.compile(r"^/portal/Consultas/Busqueda\?id=6$"),),
}


def richiesta_consentita(metodo: str, url: str) -> Tuple[bool, str]:
    m = re.match(r"^https://([^/?#]+)(/[^#]*)$", url or "")
    if not m:
        return False, "URL non https o malformato"
    if m.group(1).lower() != HOST:
        return False, "host %s fuori dalla CNMV" % m.group(1).lower()
    if re.search(r"\.shtml", m.group(2), re.I):
        return False, "vietato da robots.txt di www.cnmv.es (*.shtml)"
    regole = _AMMESSI.get(metodo)
    if regole is None:
        return False, "metodo %s non ammesso" % metodo
    if not any(r.match(m.group(2)) for r in regole):
        return False, "%s %s fuori dagli endpoint ammessi" % (metodo, m.group(2).split("?")[0])
    return True, ""


def _richiesta(metodo: str, url: str, dati: Optional[Dict[str, str]] = None,
               cookie: Optional[Dict[str, str]] = None) -> Tuple[int, bytes, Dict[str, str]]:
    """(HTTP, corpo, intestazioni con chiavi minuscole + '_cookie'). Nessun redirect seguito: il 3xx torna col suo Location."""
    ok, motivo = richiesta_consentita(metodo, url)
    if not ok:
        raise URLVietato(motivo)
    import requests
    if _ULTIMA["t"] is not None:
        resto = PAUSA_S - (time.monotonic() - _ULTIMA["t"])
        if resto > 0:
            time.sleep(resto)
    try:
        r = requests.request(metodo, url, data=dati, cookies=cookie, timeout=TIMEOUT_S, allow_redirects=False,
                             headers={"User-Agent": USER_AGENT, "Accept-Language": "en,es;q=0.8"})
    finally:
        _ULTIMA["t"] = time.monotonic()
    intest = {k.lower(): v for k, v in (getattr(r, "headers", None) or {}).items()}   # chiavi minuscole
    intest["_cookie"] = json.dumps(dict(getattr(r, "cookies", None) or {}))
    return r.status_code, r.content, intest


# ============================================================
# CACHE (scrittura atomica)
# ============================================================
def _cache_file(chiave: str) -> str:
    return os.path.join(CACHE_DIR, "cnmv_" + hashlib.sha256(chiave.encode("utf-8")).hexdigest()[:24] + ".json")


def _cache_leggi(chiave: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_file(chiave), encoding="utf-8") as fh:
            c = json.load(fh)
        if isinstance(c, dict) and "valore" in c and isinstance(c.get("salvato_ts"), (int, float)):
            return c
    except (OSError, ValueError):
        pass
    return None


def _cache_scrivi(chiave: str, valore: Any) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"salvato_ts": time.time(), "chiave": chiave, "valore": valore}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_file(chiave))
    except OSError:
        pass  # la cache e' un'ottimizzazione


def _eta(c: Dict[str, Any]) -> Optional[float]:
    e = time.time() - float(c["salvato_ts"])
    return e if e >= 0 else None


def _decodifica(corpo: bytes) -> str:
    try:
        return corpo.decode("utf-8")
    except UnicodeDecodeError:
        return corpo.decode("latin-1")


def sha_corpo(corpo: str) -> str:
    """sha256 del corpo SALVATO come testo, ricodificato (utf-8, o latin-1 se non codificabile in utf-8): coincide
    con lo sha dei byte ricevuti solo se la risposta era utf-8 valido (rilievo RV-UE2 P3-5)."""
    try:
        b = corpo.encode("utf-8")
    except UnicodeEncodeError:
        b = corpo.encode("latin-1")
    return hashlib.sha256(b).hexdigest()


def _get_pagina(url: str) -> Tuple[Optional[str], str, Optional[str], Optional[str]]:
    """(corpo, stato_cache 'fresca'|'scaduta'|'nessuna', errore, motivo). Cache scaduta + guasto ->
    corpo vecchio con stato 'scaduta' e il guasto dichiarato."""
    c = _cache_leggi(url)
    eta = _eta(c) if c is not None else None
    if c is not None and eta is not None and eta < TTL_S:
        return c["valore"], "fresca", None, None
    try:
        http, corpo, _ = _richiesta("GET", url)
        errore, motivo = (None, None) if http == 200 else ("http", "HTTP %s dalla CNMV" % http)
    except URLVietato as e:
        return None, "nessuna", "url_vietato", "URL rifiutato prima della rete (%s)" % type(e).__name__
    except Exception as e:
        errore, motivo, corpo = "rete", "CNMV non raggiungibile (%s)" % type(e).__name__, b""
    if errore is None:
        testo = _decodifica(corpo)
        _cache_scrivi(url, testo)
        return testo, "nessuna", None, None
    if c is not None:
        return c["valore"], "scaduta", errore, "%s; servita la pagina in cache (eta' %s)" % (
            motivo, "%.0f s" % eta if eta is not None else "non misurabile")
    return None, "nessuna", errore, motivo


# ============================================================
# PARSING (puro)
# ============================================================
def _testo(h: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", h or "")).split())


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


def stesso_nome(a: Optional[str], b: Optional[str]) -> bool:
    """Uguaglianza in forma CANONICA: «ZETA, S.A.» = «Zeta SA» (rilievo RV-UE2 P3-4), «AB C» != «ABC»."""
    x, y = nome_canonico(a), nome_canonico(b)
    return bool(x) and x == y


def norm_nome(s: Optional[str]) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def norm_nif(s: Optional[str]) -> Optional[str]:
    """Il NIF COSI' COME LO SCRIVE LA CNMV (validato, maiuscolo); forma non valida -> None.
    Misura dal vivo 05/10: la forma col trattino o senza dipende dall'ENTITA' (una lista risponde solo
    con «A-4...», un'altra solo con «A3...» e col trattino dice «No data available»): non si
    riscrive mai, si usa quella data dalla ricerca CNMV."""
    t = (s or "").strip().upper()
    return t if re.match(r"^[A-Z]-?[0-9]{7}[0-9A-Z]$", t) else None


def chiave_nif(s: Optional[str]) -> Optional[str]:
    """Forma di CONFRONTO (senza trattino): il CIF del dettaglio puo' essere scritto diversamente."""
    n = norm_nif(s)
    return n.replace("-", "") if n else None


def _data_es(s: str) -> Optional[str]:
    try:
        return datetime.strptime((s or "").strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def parse_isin(html: str, isin: str) -> Optional[str]:
    """Denominazione dell'emittente dalla pagina ANCV, SOLO se la pagina riporta l'ISIN cercato."""
    if isin.upper() not in (html or "").upper():
        return None
    m = re.search(r"<title>\s*(.*?)\s*</title>", html, re.S | re.I)
    t = _testo(m.group(1)) if m else ""
    # «CNMV - Información de códigos ISIN - <EMITTENTE>» (es) / «CNMV - ISIN code information - ...» (en)
    m2 = re.search(r"ISIN(?:\s+code\s+information)?\s*-\s*(.+)$", t, re.I)
    return m2.group(1).strip() if m2 else None


def parse_ricerca(stato_http: int, corpo: str, location: Optional[str]) -> Dict[str, Any]:
    """Esito della ricerca per denominazione: {'unico': nif|None, 'opzioni': [(nif, nome)]}."""
    if stato_http in (301, 302, 303) and location:
        m = re.search(r"nif=(%s)" % _NIF, location, re.I)
        return {"unico": norm_nif(m.group(1)) if m else None, "opzioni": []}
    opz = []
    sel = re.search(r'<select[^>]*lstSeleccion[^>]*>(.*?)</select>', corpo or "", re.S | re.I)
    if sel:
        for v, n in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', sel.group(1), re.S | re.I):
            opz.append((norm_nif(v), _testo(n)))
    return {"unico": None, "opzioni": opz}


def denominazione_lista(html: str) -> Optional[str]:
    """Denominazione nella pagina della lista: sottotitolo IFI o caption della tabella IFA."""
    m = re.search(r'id="ctl00_ContentPrincipal_lblSubtitulo"[^>]*>(.*?)</span>', html or "", re.S | re.I) or \
        re.search(r'id="ctl00_ContentPrincipal_gridInformes"[^>]*>\s*<caption>(.*?)</caption>', html or "", re.S | re.I)
    return _testo(m.group(1)) if m else None


def _celle(tr: str) -> Dict[str, str]:
    return {_testo(k): v for k, v in re.findall(r'<td[^>]*data-th="([^"]*)"[^>]*>(.*?)</td>', tr, re.S | re.I)}


def parse_ifa(html: str) -> Optional[List[Dict[str, Any]]]:
    """Righe della lista IFA; None se la tabella misurata non c'e' (formato cambiato o nessun dato)."""
    t = re.search(r'<table[^>]*id="ctl00_ContentPrincipal_gridInformes".*?</table>', html or "", re.S | re.I)
    if not t:
        return None
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", t.group(0), re.S | re.I):
        c = _celle(tr)
        if not c:
            continue
        chiave = lambda pref: next((v for k, v in c.items() if k.lower().startswith(pref)), "")
        tipo_html = chiave("type")
        link = dict((_testo(n).lower(), unescape(h)) for h, n in
                    re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', tipo_html, re.S | re.I))
        out.append({"protocollo": _testo(chiave("official registration")),
                    "fine_bilancio": _data_es(_testo(chiave("financial statements date"))),
                    "data": _data_es(_testo(chiave("publication date"))),
                    "url_documento": link.get("consolidated") or link.get("individual")})
    return out


_ETICHETTA = re.compile(r"^(I|II|III|IV)\s+(half-year|quarter)\s+of\s+(\d{4})", re.I)


def parse_ifi(html: str) -> Optional[List[Dict[str, Any]]]:
    t = re.search(r'<table[^>]*id="ctl00_ContentPrincipal_gridEntidades".*?</table>', html or "", re.S | re.I)
    if not t:
        return None
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", t.group(0), re.S | re.I):
        m = re.search(r'href="[^"]*detalleifialdia\.aspx\?nreg=(\d+)"[^>]*>\s*([0-9/]+)\s*</a>', tr, re.I)
        if not m:
            continue
        celle = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        etichetta = _testo(celle[1]) if len(celle) > 1 else ""
        e = _ETICHETTA.match(etichetta)
        out.append({"nreg": m.group(1), "data": _data_es(m.group(2)), "etichetta": etichetta,
                    "ordinale": e.group(1).upper() if e else None,
                    "genere": e.group(2).lower() if e else None,
                    "anno": int(e.group(3)) if e else None})
    return out


def parse_dettaglio(html: str) -> Optional[Dict[str, Any]]:
    def _span(nome):
        m = re.search(r'id="[^"]*_%s"[^>]*>(.*?)</span>' % nome, html or "", re.S | re.I)
        return _testo(m.group(1)) if m else None
    fine = _data_es(_span("lblFinPeriodo") or "")
    if fine is None:
        return None
    doc = re.search(r'id="ctl00_ContentPrincipal_hlInforme"[^>]*href="([^"]+)"', html or "", re.I) or \
        re.search(r'href="([^"]+)"[^>]*id="ctl00_ContentPrincipal_hlInforme"', html or "", re.I)
    return {"inizio": _data_es(_span("lblInicioPeriodo") or ""), "fine": fine,
            "cif": norm_nif(_span("lblNIF")), "pubblicazione": _data_es(_span("lblPublicacion") or ""),
            "nome_documento": _span("lblInforme"),
            "url_documento": unescape(doc.group(1)) if doc else None}


# ============================================================
# REGOLA (pura)
# ============================================================
def coerenza_tipo_periodo(tipo: str, fine: date) -> Optional[str]:
    if (fine + timedelta(days=1)).day != 1:
        return "periodo_fine %s non e' l'ultimo giorno di un mese" % fine
    if tipo == "trimestrale" and fine.month not in (3, 9):
        return "trimestrale al %s incoerente: la trimestrale e' al 31/03 o al 30/09" % fine.strftime("%d/%m")
    if tipo == "semestrale" and fine.month not in (6, 12):
        return "semestrale al %s incoerente: la semestrale e' al 30/06 (al 31/12 solo per esercizi non solari)" \
               % fine.strftime("%d/%m")
    return None


def _attesa_calendario(tipo: str, fine: date) -> Tuple[str, str, int]:
    """Etichetta IFI attesa per un esercizio SOLARE: 30/06 -> I half-year, 31/12 -> II half-year,
    31/03 -> I quarter, 30/09 -> III quarter (dell'anno del periodo)."""
    if tipo == "semestrale":
        return "half-year", ("I" if fine.month == 6 else "II"), fine.year
    return "quarter", ("I" if fine.month == 3 else "III"), fine.year


def preselezione_ifi(righe: List[Dict[str, Any]], tipo: str, fine: date) -> Tuple[List[Dict[str, Any]], str]:
    """Righe IFI di cui leggere il dettaglio: prima l'etichetta attesa per l'esercizio solare;
    se nessuna, le righe dello stesso genere pubblicate dopo il periodo entro la finestra (esercizio
    non solare, dichiarato)."""
    genere, ordinale, anno = _attesa_calendario(tipo, fine)
    dopo = [r for r in righe if r["data"] and r["data"] > fine.isoformat()]
    attese = [r for r in dopo if r["genere"] == genere and r["ordinale"] == ordinale and r["anno"] == anno]
    if attese:
        return attese, "etichetta"
    limite = (fine + timedelta(days=FINESTRA_GIORNI[tipo])).isoformat()
    vicine = sorted([r for r in dopo if r["genere"] == genere and r["data"] <= limite], key=lambda r: r["data"])
    return vicine, "finestra"


def scegli_ifa(righe: List[Dict[str, Any]], fine: date) -> Dict[str, Any]:
    cand, scartati = [], []
    for r in righe:
        if r["fine_bilancio"] != fine.isoformat():
            continue
        if not r["data"] or r["data"] <= fine.isoformat():
            scartati.append({"titolo": "IFA n. %s" % r["protocollo"], "data": r["data"],
                             "motivo": "data di pubblicazione assente o non dopo la fine del periodo"})
            continue
        cand.append(r)
    return _verdetto(cand, scartati, "IFA con data di bilancio %s" % fine.strftime("%d/%m/%Y"))


def scegli_ifi(righe: List[Dict[str, Any]], dettagli: Dict[str, str], tipo: str, fine: date,
               nif: str) -> Dict[str, Any]:
    """Verdetto IFI: candidate = righe preselezionate il cui DETTAGLIO ha Fin periodo = periodo_fine e
    CIF = NIF. `mancanti` = nreg da leggere (il verdetto non e' definitivo finche' non e' vuoto)."""
    pre, modo = preselezione_ifi(righe, tipo, fine)
    da_leggere = pre[:MAX_DETTAGLI]
    mancanti = [r["nreg"] for r in da_leggere if r["nreg"] not in dettagli]
    cand, scartati = [], []
    if len(pre) > MAX_DETTAGLI:
        scartati += [{"titolo": r["etichetta"], "data": r["data"],
                      "motivo": "oltre i %d dettagli letti per chiamata" % MAX_DETTAGLI} for r in pre[MAX_DETTAGLI:]]
    for r in da_leggere:
        if r["nreg"] not in dettagli:
            continue
        d = parse_dettaglio(dettagli[r["nreg"]])
        if d is None:
            return {"stato": "KO", "errore": "formato", "scelto": None, "candidati": [], "scartati": scartati,
                    "mancanti": [], "modo": modo, "prova": None,
                    "motivo": "dettaglio CNMV nreg %s senza i campi misurati (Fin periodo): formato cambiato" % r["nreg"]}
        if chiave_nif(d["cif"]) != chiave_nif(nif):
            scartati.append({"titolo": r["etichetta"], "data": r["data"],
                             "motivo": "CIF del dettaglio %s diverso dal NIF %s" % (d["cif"], nif)})
            continue
        if d["fine"] != fine.isoformat():
            scartati.append({"titolo": r["etichetta"], "data": r["data"],
                             "motivo": "Fin periodo del dettaglio %s diverso da %s" % (d["fine"], fine)})
            continue
        if d["pubblicazione"] and d["pubblicazione"] != r["data"]:
            scartati.append({"titolo": r["etichetta"], "data": r["data"],
                             "motivo": "data della lista %s diversa dal dettaglio %s" % (r["data"], d["pubblicazione"])})
            continue
        cand.append(dict(r, dettaglio=d))
    v = _verdetto(cand, scartati, "IFI %s al %s" % (tipo, fine.strftime("%d/%m/%Y")))
    v.update(mancanti=mancanti, modo=modo)
    return v


def chiavi_candidati(candidati: Any) -> set:
    """Insieme confrontabile dei candidati (AGGIUNTA 2.3): (titolo, data, ora, url)."""
    return {(c.get("titolo"), c.get("data"), c.get("ora"), c.get("url")) for c in (candidati or [])
            if isinstance(c, dict)}


def _verdetto(cand: List[Dict[str, Any]], scartati: List[Dict[str, Any]], cosa: str) -> Dict[str, Any]:
    if not cand:
        return {"stato": "non_trovato", "errore": None, "scelto": None, "candidati": [], "scartati": scartati,
                "prova": None, "motivo": "non trovato nel registro CNMV: nessuna %s pubblicata dopo la fine del "
                                         "periodo (scartate: %d)" % (cosa, len(scartati))}
    if len(cand) > 1:
        return {"stato": "ambiguo", "errore": None, "scelto": None, "candidati": cand, "scartati": scartati,
                "prova": None, "motivo": "%d righe del registro CNMV per la stessa %s (%s): nessuna scelta"
                                         % (len(cand), cosa, ", ".join(c["data"] for c in cand))}
    return {"stato": "ok", "errore": None, "scelto": cand[0], "candidati": cand, "scartati": scartati,
            "prova": "titolo", "motivo": None}


# ============================================================
# IDENTITA' (ISIN/nome -> NIF, verificabile)
# ============================================================
def _campi_nascosti(html: str) -> Dict[str, str]:
    return {n: unescape(v) for n, v in
            re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', html or "")}


CHIAVE_POST = "POST " + URL_RICERCA     # chiave della risposta alla ricerca in identita.risposte


def decidi_identita(isin: Optional[str], nome: Optional[str], risposte: Dict[str, Any]) -> Dict[str, Any]:
    """Verdetto PURO della catena dalle risposte salvate (usato dal vivo e dalla riverifica):
    risposte = {URL_ISIN: html} (se c'e' l'ISIN) + {CHIAVE_POST: {'http', 'location', 'corpo'}}.
    {'nif', 'denominazione', 'errore', 'motivo', 'metodo'}; un passo mancante = errore, mai un NIF indovinato."""
    out = {"nif": None, "denominazione": None, "errore": None, "motivo": None, "metodo": None}
    if isin:
        url = URL_ISIN.format(isin=isin.upper())
        denominazione = parse_isin(risposte.get(url) or "", isin)
        if not denominazione:
            out.update(errore="identita_mancante",
                       motivo="ISIN %s: la pagina ANCV della CNMV non riporta l'emittente" % isin)
            return out
        if nome and not stesso_nome(nome, denominazione):
            out.update(errore="identita_incoerente",
                       motivo="nome %r diverso dalla denominazione ANCV %r per l'ISIN %s" % (nome, denominazione, isin))
            return out
    else:
        denominazione = nome
    out["denominazione"] = denominazione
    post = risposte.get(CHIAVE_POST)
    if not isinstance(post, dict):
        out.update(errore="formato", motivo="risposta della ricerca CNMV assente")
        return out
    esito = parse_ricerca(post.get("http"), post.get("corpo") or "", post.get("location"))
    if esito["unico"]:
        out.update(nif=esito["unico"], metodo="ricerca CNMV: esito unico (denominazione da verificare sulla lista)")
        return out
    uguali = [n for n, d in esito["opzioni"] if n and stesso_nome(d, denominazione)]
    if len(uguali) == 1:
        out.update(nif=uguali[0], metodo="ricerca CNMV: denominazione identica fra %d esiti" % len(esito["opzioni"]))
    elif len(uguali) > 1:
        out.update(errore="nome_non_univoco", motivo="%d entita' CNMV con denominazione %r" % (len(uguali), denominazione))
    else:
        out.update(errore="identita_non_trovata",
                   motivo="nessuna entita' CNMV con denominazione identica a %r (esiti simili: %s)"
                          % (denominazione, "; ".join(d for _, d in esito["opzioni"][:8]) or "nessuno"))
    return out


def _sha_risposta(v: Any) -> str:
    return sha_corpo(v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False))


def risolvi_nif(isin: Optional[str], nome: Optional[str]) -> Dict[str, Any]:
    """{'nif', 'denominazione', 'errore', 'motivo', 'metodo', 'risposte': {chiave: risposta}, 'prove': {chiave:
    sha}, 'cache'}. Nessuna indovinata: denominazione ANCV (da ISIN) e/o nome del chiamante, poi ricerca CNMV con
    denominazione IDENTICA (decidi_identita). Le risposte della catena restano nella ricevuta (rilievo RV-UE2 P3-5:
    la riverifica non richiede rete). Cache TTL_IDENTITA_S solo per gli esiti risolti."""
    chiave = "identita|%s|%s" % ((isin or "").upper(), norm_nome(nome))
    c = _cache_leggi(chiave)
    eta = _eta(c) if c is not None else None
    if eta is not None and eta < TTL_IDENTITA_S:
        return dict(c["valore"], cache="fresca")
    out = {"nif": None, "denominazione": None, "errore": None, "motivo": None, "metodo": None,
           "risposte": {}, "prove": {}, "cache": None}
    if isin and not re.match(r"^ES[A-Z0-9]{9}[0-9]$", isin.upper()):
        out.update(errore="identita_mancante", motivo="ISIN %r non spagnolo (ES...): la CNMV non lo codifica" % isin)
        return out
    risposte: Dict[str, Any] = {}
    try:
        if isin:
            url = URL_ISIN.format(isin=isin.upper())
            http, corpo, _ = _richiesta("GET", url)
            risposte[url] = _decodifica(corpo) if http == 200 else ""
            parziale = decidi_identita(isin, nome, risposte)
            if parziale["errore"] in ("identita_mancante", "identita_incoerente"):
                out.update(errore=parziale["errore"], motivo=parziale["motivo"])
                if parziale["errore"] == "identita_mancante":
                    out["motivo"] += " (HTTP %s)" % http
                return out
            denominazione = parziale["denominazione"]
        else:
            denominazione = nome
        http, corpo, intest = _richiesta("GET", URL_RICERCA)
        modulo = _campi_nascosti(_decodifica(corpo))
        if http != 200 or "__VIEWSTATE" not in modulo:
            out.update(errore="formato", motivo="form di ricerca CNMV non nel formato misurato (HTTP %s)" % http)
            return out
        modulo.update({"ctl00$ContentPrincipal$wNombreEntidad$txtDenominacion": denominazione,
                       "ctl00$ContentPrincipal$btnOk": "Buscar"})
        http, corpo, intest2 = _richiesta("POST", URL_RICERCA, dati=modulo,
                                          cookie=json.loads(intest.get("_cookie") or "{}"))
        risposte[CHIAVE_POST] = {"http": http, "location": intest2.get("location"), "corpo": _decodifica(corpo)}
    except URLVietato as e:
        out.update(errore="url_vietato", motivo="URL rifiutato prima della rete (%s)" % type(e).__name__)
        return out
    except Exception as e:
        out.update(errore="rete", motivo="risoluzione del NIF alla CNMV fallita (%s)" % type(e).__name__)
        return out
    out.update(decidi_identita(isin, nome, risposte))
    out.update(risposte=risposte, prove={k: _sha_risposta(v) for k, v in risposte.items()})
    if out["nif"] is None:
        return out
    _cache_scrivi(chiave, {k: v for k, v in out.items() if k != "cache"})
    return out


def riverifica_identita(identita: Any, isin: Optional[str], nif: str) -> Tuple[bool, str]:
    """Senza rete: sha delle risposte salvate della catena = prove, e la catena ricalcolata da' lo stesso NIF."""
    if not isinstance(identita, dict) or not isinstance(identita.get("risposte"), dict):
        return False, "risposte della catena ISIN -> NIF assenti nella ricevuta"
    risposte, prove = identita["risposte"], identita.get("prove") or {}
    if set(risposte) != set(prove):
        return False, "risposte della catena e sha non coincidono"
    for k, v in risposte.items():
        if _sha_risposta(v) != prove[k]:
            return False, "sha256 della risposta salvata %s diverso dalla ricevuta" % k
    rif = decidi_identita(isin, None if isin else identita.get("denominazione"), risposte)
    if isin and not stesso_nome(rif["denominazione"], identita.get("denominazione")):
        return False, "denominazione ricalcolata %r diversa dalla ricevuta" % rif["denominazione"]
    if rif["nif"] != nif:
        return False, "NIF ricalcolato dalla catena %r diverso dalla ricevuta %r" % (rif["nif"], nif)
    return True, "catena ISIN -> NIF riverificata (%s)" % rif["metodo"]


# ============================================================
# RITORNO
# ============================================================
def _base(ticker: Any, tipo: Any, periodo_fine: Any, isin: Any, lei: Any, nome: Any) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper(), "isin": isin, "lei": lei, "nome": nome,
            "tipo": tipo, "periodo_fine": periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine,
            "stato": "KO", "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "fuso": "Europe/Madrid",
            "natura_data": "deposito_autorita" if tipo == "annuale" else "diffusione",
            "titolo": None, "url": None, "url_documento": None, "sha256_documento": None,
            "categoria": None, "lingua": None, "candidati": [], "conferme": [], "scartati": [], "prova": None,
            "fonte": FONTE, "paese": PAESE, "url_liste": [], "sha256_liste": {}, "risposte_salvate": {},
            "fonte_modulo": FONTE_MODULO, "pagine_lette": 0, "letto_il": None,
            "limiti": [LIMITE_ORA, LIMITE_IDENTITA, LIMITE_TRADUZIONE] + ([LIMITE_IFA] if tipo == "annuale" else []),
            "cache": None, "nif": None, "identita": None, "protocollo": None, "stato_originale": None}


def _parametri(tipo: Any, periodo_fine: Any) -> Tuple[Optional[date], Optional[str]]:
    if tipo not in TIPI:
        return None, "tipo %r non ammesso: %s" % (tipo, ", ".join(TIPI))
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return None, "periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,)
    inc = coerenza_tipo_periodo(tipo, fine)
    return (None, inc) if inc else (fine, None)


def valuta(out: Dict[str, Any], corpi: Dict[str, str], tipo: str, fine: date, nif: str,
           denominazione: Optional[str]) -> Dict[str, Any]:
    """Verdetto sui corpi gia' letti ({url: html}): lista + dettagli. Puro; lo usa anche la
    riverifica. Lascia in out['_mancanti'] i dettagli ancora da leggere."""
    url_lista = (URL_IFA if tipo == "annuale" else URL_IFI).format(nif=nif)
    lista = corpi.get(url_lista)
    out["_mancanti"] = []
    if lista is None:
        out.update(stato="KO", errore="formato", motivo="lista CNMV non letta")
        return out
    den = denominazione_lista(lista)
    if den is None and re.search(r"No se han encontrado|No hay datos|No data available|No records|not been found", lista, re.I):
        out.update(stato="non_trovato", motivo="non trovato nel registro CNMV: nessuna relazione per il NIF %s" % nif)
        return out
    if denominazione and (den is None or not stesso_nome(den, denominazione)):
        out.update(stato="KO", errore="identita_incoerente",
                   motivo="la lista CNMV del NIF %s e' di %r, non di %r" % (nif, den, denominazione))
        return out
    if tipo == "annuale":
        righe = parse_ifa(lista)
        if righe is None:
            out.update(stato="KO", errore="formato", motivo="lista IFA senza la tabella misurata: formato cambiato")
            return out
        v = scegli_ifa(righe, fine)
    else:
        righe = parse_ifi(lista)
        if righe is None:
            out.update(stato="KO", errore="formato", motivo="lista IFI senza la tabella misurata: formato cambiato")
            return out
        date_lette = [r["data"] for r in righe if r["data"]]
        if len(righe) >= MAX_RIGHE_IFI and date_lette and min(date_lette) > fine.isoformat():
            # AGGIUNTA 4.3: lista TRONCATA dalla fonte e il periodo e' piu' vecchio della riga piu' vecchia letta
            out.update(stato="KO", errore="ricerca_troncata",
                       motivo="lista IFI troncata dalla CNMV a %d righe (la piu' vecchia del %s): il periodo %s puo' "
                              "stare nelle righe non mostrate" % (len(righe), min(date_lette), fine))
            return out
        det = {}
        for u, corpo in corpi.items():
            m = re.search(r"nreg=(\d+)$", u)
            if m and "detalleifialdia" in u:
                det[m.group(1)] = corpo
        v = scegli_ifi(righe, det, tipo, fine, nif)
        out["_mancanti"] = v.get("mancanti", [])
        if v.get("modo") == "finestra" and v["stato"] in ("ok", "ambiguo"):
            out["limiti"].append("etichetta dell'esercizio solare assente: righe scelte per finestra (%d giorni) e "
                                 "confermate dal Fin periodo del dettaglio" % FINESTRA_GIORNI[tipo])
    out.update(stato=v["stato"], errore=v.get("errore"), motivo=v["motivo"], prova=v["prova"],
               scartati=v["scartati"])
    if tipo == "annuale":
        # formato UNIFORME dei candidati (AGGIUNTA 2.2); lingua = quella del documento ufficiale
        out["candidati"] = [{"titolo": "IFA n. %s, bilancio al %s" % (c["protocollo"], c["fine_bilancio"]),
                             "data": c["data"], "ora": None, "url": url_lista, "categoria": "IFA",
                             "lingua": "es", "prova": "titolo",
                             "protocollo": c["protocollo"]} for c in v["candidati"]]
    else:
        out["candidati"] = [{"titolo": c["etichetta"], "data": c["data"], "ora": None,
                             "url": URL_DETTAGLIO.format(nreg=c["nreg"]), "categoria": "IFI",
                             "lingua": "es", "prova": "titolo",
                             "protocollo": c["nreg"]} for c in v["candidati"]]
    s = v["scelto"]
    if s is not None:
        if tipo == "annuale":
            out.update(data_deposito=s["data"], titolo="Informe financiero anual n. %s (bilancio al %s)"
                       % (s["protocollo"], s["fine_bilancio"]), url=url_lista, url_documento=s["url_documento"],
                       categoria="IFA", protocollo=s["protocollo"], lingua="es")
        else:
            d = s["dettaglio"]
            out.update(data_deposito=s["data"], titolo="%s (%s - %s)" % (s["etichetta"], d["inizio"], d["fine"]),
                       url=URL_DETTAGLIO.format(nreg=s["nreg"]), url_documento=d["url_documento"],
                       categoria="IFI", protocollo=s["nreg"], lingua="es")
    return out


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str] = None,
                      lei: Optional[str] = None, nome: Optional[str] = None,
                      paese: Optional[str] = None) -> Dict[str, Any]:
    """Data di pubblicazione CNMV della relazione `tipo` al `periodo_fine`. Stati: ok | ambiguo |
    non_trovato | KO | STALE. `paese` (AGGIUNTA 4.1): dal router; fonte solo ES, non cambia la ricerca."""
    out = _base(ticker, tipo, periodo_fine, isin, lei, nome)
    fine, err = _parametri(tipo, periodo_fine)
    if err:
        out.update(errore="parametro", motivo=err)
        return out
    out["periodo_fine"] = fine.isoformat()
    if not (isin or "").strip() and not (nome or "").strip():
        out.update(errore="identita_mancante",
                   motivo="la CNMV si interroga per NIF, ricavato da `isin` (pagina ANCV) o da `nome` (denominazione "
                          "esatta): serve almeno uno dei due; il LEI non basta a questa fonte")
        return out
    ident = risolvi_nif((isin or "").strip() or None, (nome or "").strip() or None)
    out["identita"] = ident
    if ident["nif"] is None:
        out.update(errore=ident["errore"], motivo=ident["motivo"],
                   stato="ambiguo" if ident["errore"] == "nome_non_univoco" else "KO")
        return out
    nif, den = ident["nif"], ident["denominazione"]
    out["nif"] = nif
    corpi: Dict[str, str] = {}
    stati_cache, guasti = [], []

    def _leggi(url):
        corpo, sc, e, m = _get_pagina(url)
        if corpo is None:
            return e, m
        corpi[url] = corpo
        stati_cache.append(sc)
        if sc == "scaduta":
            guasti.append(m)
        out["url_liste"].append(url)
        out["sha256_liste"][url] = sha_corpo(corpo)
        out["risposte_salvate"][url] = corpo
        out["pagine_lette"] += 1
        return None, None

    e, m = _leggi((URL_IFA if tipo == "annuale" else URL_IFI).format(nif=nif))
    if e:
        out.update(errore=e, motivo=m)
        return out
    valuta(out, corpi, tipo, fine, nif, den)
    if out["_mancanti"]:
        for nreg in out["_mancanti"]:
            e, m = _leggi(URL_DETTAGLIO.format(nreg=nreg))
            if e:
                out.update(stato="KO", errore=e, motivo="dettaglio nreg %s non letto: %s (verdetto sospeso)" % (nreg, m))
                out.pop("_mancanti", None)
                return out
        out["limiti"] = _base(ticker, tipo, fine, isin, lei, nome)["limiti"]
        valuta(out, corpi, tipo, fine, nif, den)
    out.pop("_mancanti", None)
    out["letto_il"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["cache"] = "scaduta" if "scaduta" in stati_cache else ("fresca" if stati_cache and
                                                                 all(s == "fresca" for s in stati_cache) else None)
    if guasti:
        out.update(stato_originale=out["stato"], stato="STALE",
                   motivo="fonte in guasto (%s); verdetto su pagine in cache: %s" % ("; ".join(guasti), out["motivo"]))
    return out


def riverifica_ricevuta(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        paese: Optional[str] = None) -> Tuple[bool, str]:
    """Senza rete: sha256 di ogni corpo salvato = ricevuta; la scelta ricalcolata sui corpi salvati
    (lista + dettagli) da' gli stessi stato/data/titolo/candidati; e la catena ISIN -> denominazione -> NIF
    ricalcolata dalle risposte salvate in `identita.risposte` (sha in `identita.prove`)."""
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
    nif = norm_nif(ricevuta.get("nif"))
    if nif is None:
        return False, "NIF assente nella ricevuta"
    salvate = ricevuta.get("risposte_salvate") or {}
    shas = ricevuta.get("sha256_liste") or {}
    if not salvate or set(salvate) != set(shas):
        return False, "corpi salvati e ricevute sha256 non coincidono"
    for u, corpo in salvate.items():
        if not isinstance(corpo, str) or sha_corpo(corpo) != shas[u]:
            return False, "sha256 del corpo salvato di %s diverso dalla ricevuta" % u
    den = (ricevuta.get("identita") or {}).get("denominazione")
    rif = valuta(_base(ticker, tipo, fine, None, None, None), dict(salvate), tipo, fine, nif, den)
    if rif.get("_mancanti"):
        return False, "dettagli necessari non salvati nella ricevuta: %s" % ", ".join(rif["_mancanti"])
    stato_ric = ricevuta.get("stato_originale") if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    for k, atteso in (("stato", stato_ric), ("data_deposito", ricevuta.get("data_deposito")),
                      ("titolo", ricevuta.get("titolo"))):
        if rif[k] != atteso:
            return False, "%s ricalcolato %r diverso dalla ricevuta %r" % (k, rif[k], atteso)
    # AGGIUNTA 2.3: anche la LISTA dei candidati (decisiva per gli ambigui) si ricalcola e si confronta
    if chiavi_candidati(rif["candidati"]) != chiavi_candidati(ricevuta.get("candidati")):
        return False, "candidati ricalcolati diversi da quelli della ricevuta"
    ok_id, mot_id = riverifica_identita(ricevuta.get("identita"), ricevuta.get("isin"), nif)
    if not ok_id:
        return False, mot_id
    return True, ("ricevuta coerente: %s %s %s; %s" % (rif["stato"], rif["data_deposito"], rif["titolo"], mot_id))
