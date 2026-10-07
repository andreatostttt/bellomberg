"""riscontro_numerico.py - RISCONTRO NUMERICO di un documento finanziario trovato fuori dal sito
dell'emittente (07/10/2026, Opus 5.5).

Richiesta PM: un PDF su una CDN o sul sito di gruppo non si prova per identita' dal testo; ma se i
suoi numeri chiave coincidono con una fonte INDIPENDENTE legata all'emittente per lo stesso periodo,
e' suo (la capogruppo o un partner hanno numeri diversi).

    riscontra(testo, *, ticker, cik=None, lei=None, isin=None, periodo_fine, periodo_inizio=None,
              valuta=None, fonti=None) -> dict

Esiti: «riscontrato» (almeno 2 voci DIVERSE coincidono entro tolleranza e nessuna contraddice),
«diverso» (una voce confrontata contraddice oltre il 2%), «senza_confronto» (fonte assente, periodo
assente, una sola voce, valuta diversa, errore: sempre col motivo). Mai i numeri del documento come
confronto; nessuna AI; ogni chiamata di rete col tetto di freschezza_trimestrale (thread con tempo
massimo). Nessuna eccezione esce.

Fonti, nell'ordine: SEC XBRL companyfacts (se cik), ESEF filings.xbrl.org (se lei, solo esercizi
annuali: l'estrazione ESEF tiene solo durate annuali e saldi), fornitore yfinance (per ticker, solo
trimestri: e' un fornitore, non un deposito, e l'etichetta lo dice). La prima fonte che decide
(riscontrato o diverso) chiude; le altre dicono perche' non hanno deciso.

`fonti` (iniettabile, i test non toccano la rete): oggetto o dict con `companyfacts(cik)` (forma
del JSON companyfacts SEC), `esef(lei)` (forma della cache di esef._refresh_entity_cache),
`fornitore(ticker)` (forma di freschezza_trimestrale.FontiVive.fornitore). Un metodo mancante =
fonte non fornita, dichiarata.

Limiti dichiarati: l'estrazione prende per ogni voce il PRIMO valore numerico dopo l'etichetta
(stessa frase o riga); una tabella con le colonne in ordine diverso (anno prima del trimestre) puo'
dare la colonna sbagliata, che diventa «diverso» o «senza_confronto», mai un falso «riscontrato»
(servono due voci coincidenti). Senza scala dichiarata (inline o in intestazione) una voce monetaria
non si confronta.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

CONTRATTO = "riscontro-numerico/1"
TOLLERANZA_PCT = 0.5          # coincide entro lo 0,5% (o entro l'arrotondamento all'unita' riportata)
CONTRADDIZIONE_PCT = 2.0      # oltre il 2% la voce contraddice
MIN_VOCI = 2

VOCI = ("ricavi", "utile_netto", "eps_diluito", "risultato_operativo", "totale_attivo")
NOMI_VOCI = {"ricavi": "ricavi", "utile_netto": "utile netto", "eps_diluito": "EPS diluito",
             "risultato_operativo": "risultato operativo", "totale_attivo": "totale attivo"}
# voce -> riga canonica di sec_xbrl.CANONICAL / esef._canonical_ifrs
CANONICHE = {"ricavi": "revenue", "utile_netto": "net_income", "eps_diluito": "eps_diluted",
             "risultato_operativo": "operating_income", "totale_attivo": "total_assets"}
SALDI = {"totale_attivo"}
PER_AZIONE = {"eps_diluito"}
RIGHE_FORNITORE = {"ricavi": ("Total Revenue",),
                   "utile_netto": ("Net Income Common Stockholders", "Net Income"),
                   "eps_diluito": ("Diluted EPS",),
                   "risultato_operativo": ("Operating Income",),
                   "totale_attivo": ("Total Assets",)}

FONTE_SEC = "SEC XBRL companyfacts"
FONTE_ESEF = "ESEF"
FONTE_FORNITORE = "yfinance (fornitore)"

# classi di durata (giorni): esercizi a 52/53 settimane compresi
DURATE = {"trimestre": (80, 100), "semestre": (170, 195), "nove_mesi": (260, 285), "anno": (350, 380)}


# ------------------------------------------------------------------ etichette nel testo

_E = re.IGNORECASE
_ETICHETTE = {
    "ricavi": re.compile(
        r"\b(?:total\s+)?(?:net\s+)?revenues?\b|\b(?:total\s+)?net\s+sales\b|\bricavi\b|"
        r"\bumsatz(?:erl(?:ö|oe)se)?\b|\bchiffre\s+d['’]affaires\b|\bingresos(?:\s+ordinarios|\s+totales)?\b|"
        r"\bimporte\s+neto\s+de\s+la\s+cifra\s+de\s+negocios\b", _E),
    "utile_netto": re.compile(
        r"\bnet\s+(?:income|profit|earnings)(?:\s+attributable\s+to\s+[\w\s']{0,40}?(?:parent|company|"
        r"shareholders|stockholders|owners))?\b(?!\s+per\b)|"
        r"\bprofit\s+(?:for\s+the\s+(?:period|year)\s+)?attributable\s+to\s+(?:the\s+)?(?:owners|shareholders|"
        r"equity\s+holders)\s+of\s+the\s+(?:parent|company)\b|"
        r"\butile\s+netto(?:\s+(?:di\s+gruppo|attribuibile\s+[\w\s']{0,30}?(?:gruppo|capogruppo|azionisti)))?\b|"
        r"\brisultato\s+netto\b(?!\s+per\b)|\bkonzernergebnis\b|\bjahres(?:ü|ue)berschuss\b|\br(?:é|e)sultat\s+net\b"
        r"(?!\s+(?:dilu|par\b))|\bbeneficio\s+neto\b|\bresultado\s+neto\b", _E),
    "eps_diluito": re.compile(
        r"\bdiluted\s+(?:net\s+)?(?:earnings|income|eps|profit)(?:\s+per\s+(?:common\s+)?(?:share|ads))?\b|"
        r"\b(?:earnings|net\s+income|profit)\s+per\s+(?:common\s+)?share\s*[-–,:(]?\s*diluted\b|"
        r"\beps\s*[-–,:(]?\s*diluted\b|"
        r"\butile\s+(?:netto\s+)?(?:per\s+azione\s+diluito|diluito\s+per\s+azione)\b|"
        r"\bverw(?:ä|ae)ssertes\s+ergebnis\s+je\s+aktie\b|\br(?:é|e)sultat\s+(?:net\s+)?dilu(?:é|e)\s+par\s+action\b|"
        r"\bbeneficio\s+(?:neto\s+)?diluido\s+por\s+acci(?:ó|o)n\b", _E),
    "risultato_operativo": re.compile(
        r"\boperating\s+(?:income|profit)\b|\bincome\s+from\s+operations\b|\bEBIT\b|"
        r"\brisultato\s+operativo\b|\breddito\s+operativo\b|\boperatives\s+ergebnis\b|\bbetriebsergebnis\b|"
        r"\br(?:é|e)sultat\s+op(?:é|e)rationnel\b|\bresultado\s+de\s+explotaci(?:ó|o)n\b|"
        r"\bbeneficio\s+de\s+explotaci(?:ó|o)n\b", _E),
    "totale_attivo": re.compile(
        r"\btotal\s+assets\b|\btotale\s+(?:dell['’]\s*)?attivo\b|\bbilanzsumme\b|\bsumme\s+aktiva\b|"
        r"\btotal\s+(?:de\s+l['’]\s*)?actif\b|\btotal\s+activo\b", _E),
}
# davanti all'etichetta: non e' la voce di bilancio (costo dei ricavi, ricavi differiti, rettificati...)
_PRIMA_ESCLUSE = re.compile(
    r"(?:cost\s+of|costs\s+of|deferred|unearned|adjusted|adj\.|non-gaap|non-ifrs|underlying|organic|core|"
    r"segment|pro[\s-]forma|diluted|costo\s+dei|rettificat[oi]|bereinigte[sr]?|ajust(?:é|e)|ajustado|"
    r"comparable)\s*$", _E)
_DOPO_ESCLUSE = re.compile(r"^\s*(?:\(?non-gaap|\(?adjusted|rettificat|adj\.|growth|margin|\bper\s+share)", _E)

_MESI = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|gennaio|febbraio|marzo|aprile|maggio|"
         r"giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|januar|februar|m(?:ä|ae)rz|juni|juli|"
         r"oktober|dezember|janvier|f(?:é|e)vrier|mars|avril|mai|juin|juillet|ao(?:û|u)t|septembre|octobre|"
         r"novembre|d(?:é|e)cembre|enero|febrero|abril|mayo|junio|julio|agosto|septiembre|octubre|"
         r"noviembre|diciembre")
_MESE_PRIMA = re.compile(r"(?:\b(?:%s)\.?)\s*$" % _MESI, _E)
_MESE_DOPO = re.compile(r"^\.?\s*(?:%s)\b" % _MESI, _E)

# un numero: segno o parentesi, valuta opzionale, cifre coi separatori
_NUMERO = re.compile(
    r"(?<![\w.,])(?P<par>\()?\s*(?P<seg>[-−–])?\s*(?P<val>US\$|C\$|A\$|\$|€|£|¥|EUR|USD|GBP|CHF|JPY)?\s*"
    r"(?P<num>\d{1,3}(?:[.,'   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\d])"
    r"(?P<chiusa>\s*\))?")
_SCALA_INLINE = re.compile(
    r"^\s*(?P<s>bn|billions?|mrd\.?|miliardi|milliards?|milliarden|md|millions?|mln|mio\.?|milioni|millionen|"
    r"millones|thousands?|migliaia|tsd\.?|tausend|milliers|miles|m|k)(?![\w])", _E)
_SCALA_PAROLA = {"bn": 1e9, "billion": 1e9, "billions": 1e9, "mrd": 1e9, "mrd.": 1e9, "miliardi": 1e9,
                 "milliard": 1e9, "milliards": 1e9, "milliarden": 1e9, "md": 1e9,
                 "million": 1e6, "millions": 1e6, "mln": 1e6, "mio": 1e6, "mio.": 1e6, "milioni": 1e6,
                 "millionen": 1e6, "millones": 1e6, "m": 1e6,
                 "thousand": 1e3, "thousands": 1e3, "migliaia": 1e3, "tsd": 1e3, "tsd.": 1e3, "tausend": 1e3,
                 "milliers": 1e3, "miles": 1e3, "k": 1e3}
# scala dichiarata in intestazione o sopra la tabella
_SCALA_TESTA = [
    (re.compile(r"\b(?:in\s+)?billions?\b|\bmiliardi\b|\bmrd\.?(?!\w)|\bmilliarden\b|\bmilliards?\b|"
                r"\bmil\s+millones\b|\b(?:eur|usd|€|\$)\s?bn\b", _E), 1e9),
    (re.compile(r"\b(?:in\s+)?millions?\b|\bmilioni\b|\bmln\b|\bmio\.?(?!\w)|\bmillionen\b|\bmillones\b|"
                r"\bMEUR\b|\bMUSD\b|\b(?:eur|usd|gbp|chf)\s?m\b|[€$£]\s?m\b|\bM€|\(m\)", _E), 1e6),
    (re.compile(r"\b(?:in\s+)?thousands?\b|\bmigliaia\b|\btsd\.?(?!\w)|\btausend\b|\bTEUR\b|\bkEUR\b|"
                r"\bT€|\bk€|\bmilliers\b|\bmiles\s+de\b", _E), 1e3),
]
_SIMBOLI_VALUTA = {"US$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "C$": "CAD", "A$": "AUD"}
_VALUTA_TESTO = re.compile(r"US\$|C\$|A\$|€|£|¥|\$|\b(?:USD|EUR|GBP|CHF|JPY|SEK|NOK|DKK|CAD|AUD|"
                           r"euro|dollars?|sterling|francs?\s+suisses?)\b", _E)
_PAROLE_VALUTA = {"euro": "EUR", "dollar": "USD", "dollars": "USD", "sterling": "GBP"}

_DURATA_TESTO = {
    "trimestre": re.compile(r"\bthree\s+months\b|\bquarter(?:ly)?\b|\bQ[1-4]\b|\btrimestr\w*|\bquartal\w*|"
                            r"\btres\s+meses\b|\btrois\s+mois\b", _E),
    "semestre": re.compile(r"\bsix\s+months\b|\bhalf[\s-]year\b|\bfirst\s+half\b|\bH1\b|\bsemestr\w*|"
                           r"\bhalbjahr\w*|\bsei\s+mesi\b|\bsix\s+mois\b|\bseis\s+meses\b", _E),
    "nove_mesi": re.compile(r"\bnine\s+months\b|\bnove\s+mesi\b|\bneun\s+monate\b|\bneuf\s+mois\b|"
                            r"\bnueve\s+meses\b|\b9M\b", _E),
    "anno": re.compile(r"\bfull[\s-]year\b|\bfiscal\s+year\b|\byear\s+ended\b|\btwelve\s+months\b|"
                       r"\bannual\s+report\b|\besercizio\b|\bbilancio\s+(?:annuale|consolidato\s+al)\b|"
                       r"\bgesch(?:ä|ae)ftsjahr\w*|\bjahresabschluss\b|\bexercice\b|\bejercicio\b|\bFY\b", _E),
}


# ------------------------------------------------------------------ numeri

def _stile_numeri(testo: str) -> Optional[str]:
    """'inglese' (1,234.5) o 'europeo' (1.234,5) dalla prevalenza nel documento; None se non si vede."""
    ing = len(re.findall(r"\d{1,3}(?:,\d{3})+\.\d+|\d{1,3}(?:,\d{3}){2,}(?![.,]?\d)", testo))
    eur = len(re.findall(r"\d{1,3}(?:\.\d{3})+,\d+|\d{1,3}(?:\.\d{3}){2,}(?![.,]?\d)", testo))
    ing += len(re.findall(r"(?<![\d.,])\d+\.\d{1,2}(?![\d.,])", testo))
    eur += len(re.findall(r"(?<![\d.,])\d+,\d{1,2}(?![\d.,])", testo))
    if ing > eur:
        return "inglese"
    if eur > ing:
        return "europeo"
    return None


def _gruppi_validi(parti: List[str]) -> bool:
    return 1 <= len(parti[0]) <= 3 and all(len(p) == 3 for p in parti[1:])


def parse_numero(grezzo: str, stile: Optional[str]) -> Optional[Tuple[float, int]]:
    """(valore, decimali) da '1,234.5' / '1.234,5' / '1 234,5' / '1234'. None se ambiguo o malformato."""
    s = re.sub(r"[   ']", "§", grezzo.strip())
    if "§" in s:
        parti_sp = s.split("§")
        coda = parti_sp[-1]
        dec = None
        for sep in (",", "."):
            if sep in coda:
                coda, dec = coda.split(sep, 1)
        parti_sp[-1] = coda
        if not _gruppi_validi(parti_sp) or any(not p.isdigit() for p in parti_sp):
            return None
        intero = "".join(parti_sp)
        return (float(intero + ("." + dec if dec else "")), len(dec or ""))
    ha_v, ha_p = "," in s, "." in s
    if ha_v and ha_p:
        dec_sep = "," if s.rfind(",") > s.rfind(".") else "."
        mil_sep = "." if dec_sep == "," else ","
        intero, dec = s.rsplit(dec_sep, 1)
        parti = intero.split(mil_sep)
        if not _gruppi_validi(parti) or dec_sep in intero:
            return None
        return (float("".join(parti) + "." + dec), len(dec))
    sep = "," if ha_v else "." if ha_p else None
    if sep is None:
        return (float(s), 0)
    parti = s.split(sep)
    if len(parti) > 2:
        return (float("".join(parti)), 0) if _gruppi_validi(parti) else None
    intero, coda = parti
    if len(coda) != 3:
        return (float(intero + "." + coda), len(coda))
    # un solo separatore e tre cifre dopo: migliaia o decimali? decide lo stile del documento
    if stile is None:
        return None
    migliaia = (sep == "," and stile == "inglese") or (sep == "." and stile == "europeo")
    if migliaia:
        return (float(intero + coda), 0) if _gruppi_validi(parti) else None
    return (float(intero + "." + coda), 3)


def _scala_in_testa(testo: str, pos: int) -> Optional[Tuple[float, str]]:
    """Scala dichiarata piu' vicina PRIMA della voce; altrimenti entro 400 caratteri dopo."""
    prima, dopo = None, None
    for rx, fattore in _SCALA_TESTA:
        for m in rx.finditer(testo):
            if m.start() < pos and (prima is None or m.start() > prima[0]):
                prima = (m.start(), fattore, m.group(0))
            elif m.start() >= pos and m.start() - pos <= 400 and (dopo is None or m.start() < dopo[0]):
                dopo = (m.start(), fattore, m.group(0))
    scelta = prima or dopo
    return (scelta[1], scelta[2].strip()) if scelta else None


def _valuta_documento(testo: str) -> Optional[str]:
    conti: Dict[str, int] = {}
    for m in _VALUTA_TESTO.finditer(testo):
        g = m.group(0)
        cod = _SIMBOLI_VALUTA.get(g) or _PAROLE_VALUTA.get(g.lower()) or \
            ("CHF" if g.lower().startswith("franc") else g.upper())
        conti[cod] = conti.get(cod, 0) + 1
    if not conti:
        return None
    ordinati = sorted(conti.items(), key=lambda kv: -kv[1])
    if len(ordinati) > 1 and ordinati[1][1] * 2 > ordinati[0][1]:
        return None                       # due valute quasi alla pari: non si sceglie
    return ordinati[0][0]


def _finestra(testo: str, inizio: int) -> str:
    """Il seguito dell'etichetta nella stessa riga o frase (al massimo 160 caratteri)."""
    pezzo = testo[inizio:inizio + 160]
    tagli = [m.start() for m in (re.search(r"\n", pezzo), re.search(r"\.\s+(?=[A-Z])", pezzo)) if m]
    return pezzo[:min(tagli)] if tagli else pezzo


def _primo_valore(testo: str, fine_etichetta: int, voce: str, stile: Optional[str]) -> Optional[Dict[str, Any]]:
    finestra = _finestra(testo, fine_etichetta)
    for m in _NUMERO.finditer(finestra):
        grezzo = m.group("num")
        dopo = finestra[m.end():]
        prima = finestra[:m.start()]
        if re.match(r"^\s*%|^\s*per\s*cent", dopo, _E) or re.match(r"^\s*(?:bps|bp|x)\b", dopo, _E):
            continue                                  # percentuale, punti base, multiplo
        if _MESE_PRIMA.search(prima) or _MESE_DOPO.search(dopo):
            continue                                  # giorno di una data
        if re.fullmatch(r"(?:19|20)\d\d", grezzo) and not m.group("val"):
            continue                                  # anno
        if m.group("par") and m.group("chiusa") and re.fullmatch(r"\d", grezzo) and not m.group("val"):
            continue                                  # rimando a nota (1)
        letto = parse_numero(grezzo, stile)
        if letto is None:
            return {"errore": "numero ambiguo '%s' (stile del documento non determinato)" % grezzo}
        valore, decimali = letto
        negativo = bool(m.group("seg")) or bool(m.group("par") and m.group("chiusa"))
        scala, scala_da = 1.0, "per azione"
        if voce not in PER_AZIONE:
            inline = _SCALA_INLINE.match(finestra[m.end() - (len(m.group("chiusa") or "")):])
            if inline:
                scala = _SCALA_PAROLA.get(inline.group("s").lower(), None)
                scala_da = "accanto al numero (%s)" % inline.group("s")
            else:
                testa = _scala_in_testa(testo, fine_etichetta + m.start())
                if testa is None:
                    return {"errore": "scala non dichiarata per '%s'" % grezzo}
                scala, scala_da = testa[0], "dichiarata (%s)" % testa[1]
        if scala is None:
            return {"errore": "scala non riconosciuta per '%s'" % grezzo}
        v = -valore if negativo else valore
        return {"valore": v * scala, "grezzo": m.group(0).strip(), "unita_arrotondamento": (10 ** -decimali) * scala,
                "scala": scala, "scala_da": scala_da, "valuta_inline": _SIMBOLI_VALUTA.get(m.group("val") or "",
                                                                                         m.group("val"))}
    return None


def estrai_voci(testo: str) -> Dict[str, Any]:
    """{voci: {voce: {...}}, scartate: {voce: motivo}, stile, valuta}. Il primo valore valido per voce."""
    stile = _stile_numeri(testo)
    voci: Dict[str, Dict[str, Any]] = {}
    scartate: Dict[str, str] = {}
    for voce, rx in _ETICHETTE.items():
        for m in rx.finditer(testo):
            if _PRIMA_ESCLUSE.search(testo[max(0, m.start() - 30):m.start()]):
                continue
            if _DOPO_ESCLUSE.search(testo[m.end():m.end() + 25]):
                continue
            letto = _primo_valore(testo, m.end(), voce, stile)
            if letto is None:
                continue
            if "errore" in letto:
                scartate.setdefault(voce, letto["errore"])
                continue
            letto["etichetta"] = m.group(0)
            voci[voce] = letto
            scartate.pop(voce, None)
            break
    return {"voci": voci, "scartate": scartate, "stile": stile, "valuta": _valuta_documento(testo)}


def durate_dal_testo(testo: str) -> List[str]:
    return [k for k, rx in _DURATA_TESTO.items() if rx.search(testo)]


# ------------------------------------------------------------------ periodo

def _data(x: Any) -> Optional[date]:
    from bellomberg.market_data.freschezza_trimestrale import _data as _d
    return _d(x)


def _classe_durata(giorni: Optional[int]) -> Optional[str]:
    if giorni is None:
        return None
    for nome, (lo, hi) in DURATE.items():
        if lo <= giorni <= hi:
            return nome
    return None


def _stessa_fine(a: Any, b: date) -> bool:
    from bellomberg.market_data.freschezza_trimestrale import TOLLERANZA_GIORNI
    d = _data(a)
    return d is not None and abs((d - b).days) <= TOLLERANZA_GIORNI


# ------------------------------------------------------------------ fonti

def _valuta_unita(unita: Any) -> Optional[str]:
    u = str(unita or "").replace("iso4217:", "").split("/")[0].strip().upper()
    return u if re.fullmatch(r"[A-Z]{3}", u) else None


def _candidati_sec(facts: Dict[str, Any], fine: date, classi: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    from bellomberg.market_data.sec_xbrl import CANONICAL, _osservazioni, _giorni
    out: Dict[str, List[Dict[str, Any]]] = {}
    for voce in VOCI:
        tg, ti = CANONICAL[CANONICHE[voce]]
        for tag, unita, obs in _osservazioni(facts.get("facts") or {}, tg, ti):
            for ob in obs:
                if not _stessa_fine(ob.get("end"), fine) or not isinstance(ob.get("val"), (int, float)):
                    continue
                if voce in SALDI:
                    if ob.get("start"):
                        continue
                    durata = None
                else:
                    durata = _classe_durata(_giorni(ob))
                    if durata is None or (classi and durata not in classi):
                        continue
                out.setdefault(voce, []).append({"valore": float(ob["val"]), "valuta": _valuta_unita(unita),
                                                 "durata": durata, "tag": tag, "form": ob.get("form")})
    return out


def _candidati_esef(cache: Dict[str, Any], fine: date, classi: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    from bellomberg.market_data.esef import _canonical_ifrs
    mappa = _canonical_ifrs()
    out: Dict[str, List[Dict[str, Any]]] = {}
    if classi and "anno" not in classi:
        flussi_ok = False                 # l'ESEF estratto ha solo esercizi: un trimestre non si confronta
    else:
        flussi_ok = True
    for f in (cache.get("filings") or {}).values():
        if not isinstance(f, dict) or not _stessa_fine(f.get("period_end"), fine):
            continue
        fy = _data(f.get("period_end")).year
        for voce in VOCI:
            if voce not in SALDI and not flussi_ok:
                continue
            for tag in mappa.get(CANONICHE[voce], []):
                for riga in (f.get("facts") or {}).get(tag) or []:
                    try:
                        anno, val, unita = int(riga[0]), float(riga[1]), riga[2]
                    except (TypeError, ValueError, IndexError):
                        continue
                    if anno != fy:
                        continue          # comparativo dell'anno prima
                    out.setdefault(voce, []).append({"valore": val, "valuta": _valuta_unita(unita),
                                                     "durata": None if voce in SALDI else "anno",
                                                     "tag": "ifrs-full:" + tag})
    return out


def _candidati_fornitore(forn: Dict[str, Any], fine: date, classi: List[str],
                         durata_certa: bool) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    valuta = _valuta_unita(forn.get("valuta"))
    # i bilanci del fornitore sono trimestrali: i flussi si confrontano solo se il documento e' un trimestre
    flussi_ok = durata_certa and classi == ["trimestre"]
    for tab in (forn.get("trimestrali") or {}).values():
        for fine_f, riga in (tab or {}).items():
            if not _stessa_fine(fine_f, fine) or not isinstance(riga, dict):
                continue
            for voce, nomi in RIGHE_FORNITORE.items():
                if voce not in SALDI and not flussi_ok:
                    continue
                for nome in nomi:
                    v = riga.get(nome)
                    if isinstance(v, (int, float)) and v == v:
                        out.setdefault(voce, []).append({"valore": float(v), "valuta": valuta,
                                                         "durata": None if voce in SALDI else "trimestre",
                                                         "tag": nome})
    return out


def _chiama(fonti: Any, nome: str, arg: str) -> Tuple[Any, Optional[str]]:
    """(dati, motivo). Ogni chiamata col tetto di tempo di freschezza_trimestrale; mai un'eccezione."""
    from bellomberg.market_data import freschezza_trimestrale as ft
    fn = fonti.get(nome) if isinstance(fonti, dict) else getattr(fonti, nome, None)
    if not callable(fn):
        return None, "fonte non fornita"
    try:
        return ft._entro(lambda: fn(arg), time.monotonic() + ft.TEMPO_TOOL_S), None
    except Exception as exc:
        return None, "errore della fonte (%s)" % ft._motivo(exc)


class FontiVive:
    """Le fonti vere, riusate: companyfacts SEC (cache 7 gg), cache ESEF, fornitore della cascata."""

    def companyfacts(self, cik: str):
        from bellomberg.market_data import sec_xbrl
        return sec_xbrl._fetch_companyfacts(cik)

    def esef(self, lei: str):
        from bellomberg.market_data import esef
        return esef._refresh_entity_cache(lei)

    def fornitore(self, ticker: str):
        from bellomberg.market_data.freschezza_trimestrale import FontiVive as _FV
        return _FV().fornitore(ticker)


# ------------------------------------------------------------------ confronto

def _confronta_voce(doc: Dict[str, Any], candidati: List[Dict[str, Any]]) -> Dict[str, Any]:
    vd = doc["valore"]
    meglio = None
    for c in candidati:
        vs = c["valore"]
        diff = abs(vd - vs)
        scarto = (diff / abs(vs) * 100.0) if vs else (0.0 if vd == 0 else float("inf"))
        arrot = diff <= doc["unita_arrotondamento"] / 2.0 * (1 + 1e-9) + 1e-12
        voto = (0 if (scarto <= TOLLERANZA_PCT or arrot) else 1, scarto)
        if meglio is None or voto < meglio[0]:
            meglio = (voto, c, scarto, arrot)
    (_, c, scarto, arrot) = meglio
    if scarto <= TOLLERANZA_PCT or arrot:
        esito = "coincide"
    elif scarto > CONTRADDIZIONE_PCT:
        esito = "contraddice"
    else:
        esito = "incerto"
    return {"valore_fonte": c["valore"], "scarto_pct": (round(scarto, 4) if scarto != float("inf") else None),
            "esito": esito, "tag_fonte": c.get("tag"), "durata_fonte": c.get("durata")}


def _valuta_esclude(doc_valuta: Optional[str], candidati: Dict[str, List[Dict[str, Any]]]
                    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
    """Filtra i candidati sulla valuta del documento. Motivo se la fonte e' in un'altra valuta."""
    if not doc_valuta:
        return candidati, None
    valute = {c["valuta"] for cs in candidati.values() for c in cs if c.get("valuta")}
    if valute and doc_valuta not in valute:
        return {}, "valuta diversa (documento %s, fonte %s): fonte non confrontata" % (
            doc_valuta, "/".join(sorted(valute)))
    filtrati = {v: [c for c in cs if c.get("valuta") in (None, doc_valuta)] for v, cs in candidati.items()}
    return {v: cs for v, cs in filtrati.items() if cs}, None


def _giudica(fonte: str, estratte: Dict[str, Dict[str, Any]], candidati: Dict[str, List[Dict[str, Any]]]
             ) -> Dict[str, Any]:
    voci = []
    for voce in VOCI:
        if voce not in estratte or not candidati.get(voce):
            continue
        r = _confronta_voce(estratte[voce], candidati[voce])
        voci.append({"voce": voce, "valore_documento": estratte[voce]["valore"], **r})
    coincidenti = [v["voce"] for v in voci if v["esito"] == "coincide"]
    contrarie = [v for v in voci if v["esito"] == "contraddice"]
    if contrarie:
        c = contrarie[0]
        motivo = "%s: documento %s, %s %s (scarto %s%%)" % (
            NOMI_VOCI[c["voce"]], _fmt(c["valore_documento"]), fonte, _fmt(c["valore_fonte"]),
            "n.d." if c["scarto_pct"] is None else ("%.2f" % c["scarto_pct"]))
        return {"esito": "diverso", "voci": voci, "motivo": motivo}
    if len(set(coincidenti)) >= MIN_VOCI:
        return {"esito": "riscontrato", "voci": voci, "motivo": None, "coincidenti": coincidenti}
    if not voci:
        return {"esito": "senza_confronto", "voci": voci,
                "motivo": "nessuna voce del documento ha un valore della fonte per lo stesso periodo"}
    return {"esito": "senza_confronto", "voci": voci,
            "motivo": "%d voce/i coincidente/i su %d confrontate: ne servono almeno %d" % (
                len(coincidenti), len(voci), MIN_VOCI)}


def _fmt(x: Any) -> str:
    try:
        return "{:,.4g}".format(float(x)) if abs(float(x)) < 1000 else "{:,.0f}".format(float(x))
    except (TypeError, ValueError):
        return str(x)


def _esito(esito: str, *, voci=None, fonte=None, motivo=None, etichetta=None, **extra) -> Dict[str, Any]:
    out = {"contratto": CONTRATTO, "esito": esito, "voci": voci or [], "fonte": fonte, "motivo": motivo,
           "etichetta": etichetta}
    out.update(extra)
    return out


# ------------------------------------------------------------------ ingresso

def riscontra(testo, *, ticker, cik=None, lei=None, isin=None, periodo_fine, periodo_inizio=None,
              valuta=None, fonti=None) -> dict:
    try:
        return _riscontra(testo, ticker=ticker, cik=cik, lei=lei, isin=isin, periodo_fine=periodo_fine,
                          periodo_inizio=periodo_inizio, valuta=valuta, fonti=fonti)
    except Exception as exc:                      # nessuna eccezione esce: dichiarata col tipo
        motivo = "errore interno del riscontro (%s)" % type(exc).__name__
        return _esito("senza_confronto", motivo=motivo, etichetta="non verificato per riscontro numerico: " + motivo)


def _riscontra(testo, *, ticker, cik, lei, isin, periodo_fine, periodo_inizio, valuta, fonti) -> dict:
    from bellomberg.market_data.freschezza_trimestrale import _testo
    grezzo = testo if isinstance(testo, str) else ""
    if not grezzo.strip():
        motivo = "testo del documento vuoto"
        return _esito("senza_confronto", motivo=motivo, etichetta="non verificato per riscontro numerico: " + motivo)
    if re.search(r"(?is)<(?:html|body|table|div|p|td)\b", grezzo):
        # le celle di tabella diventano righe prima di togliere i tag
        grezzo = _testo(re.sub(r"(?i)</(?:tr|p|div|h\d|li)>|<br\s*/?>", "\n__RIGA__", grezzo)).replace(
            "__RIGA__", "\n")
    fine = _data(periodo_fine)
    if fine is None:
        motivo = "periodo_fine non leggibile (%r)" % (periodo_fine,)
        return _esito("senza_confronto", motivo=motivo, etichetta="non verificato per riscontro numerico: " + motivo)
    inizio = _data(periodo_inizio) if periodo_inizio else None
    if inizio is not None:
        classe = _classe_durata((fine - inizio).days)
        if classe is None:
            motivo = "durata del periodo %s-%s non riconosciuta (trimestre, semestre, nove mesi, anno)" % (
                inizio.isoformat(), fine.isoformat())
            return _esito("senza_confronto", motivo=motivo,
                          etichetta="non verificato per riscontro numerico: " + motivo)
        classi, durata_certa = [classe], True
    else:
        classi = durate_dal_testo(grezzo)
        durata_certa = len(classi) == 1
    estrazione = estrai_voci(grezzo)
    estratte = estrazione["voci"]
    doc_valuta = (str(valuta).strip().upper() or None) if valuta else estrazione["valuta"]
    base = {"estratte": {v: {k: d[k] for k in ("valore", "grezzo", "scala_da")} for v, d in estratte.items()},
            "scartate": estrazione["scartate"], "valuta_documento": doc_valuta,
            "durata": classi or "non determinata", "periodo_fine": fine.isoformat(), "ticker": ticker,
            "identificativi": {"cik": cik, "lei": lei, "isin": isin}}
    if len(estratte) < MIN_VOCI:
        motivo = "dal documento si leggono %d voci (%s): ne servono almeno %d per un riscontro" % (
            len(estratte), ", ".join(NOMI_VOCI[v] for v in estratte) or "nessuna", MIN_VOCI)
        if estrazione["scartate"]:
            motivo += "; scartate: " + "; ".join("%s: %s" % (NOMI_VOCI[k], m) for k, m in estrazione["scartate"].items())
        return _esito("senza_confronto", motivo=motivo, etichetta="non verificato per riscontro numerico: " + motivo,
                      fonti_provate=[], **base)
    fonti = FontiVive() if fonti is None else fonti

    piano = []
    if cik:
        cik_s = str(cik).strip()
        piano.append((FONTE_SEC, "companyfacts", cik_s.zfill(10) if cik_s.isdigit() else cik_s))
    if lei:
        piano.append((FONTE_ESEF, "esef", str(lei).strip()))
    if ticker:
        piano.append((FONTE_FORNITORE, "fornitore", str(ticker).strip()))
    provate: List[Dict[str, Any]] = []
    if not piano:
        provate.append({"fonte": None, "motivo": "nessun identificativo (cik, lei, ticker) per legare la fonte"})
    for nome_fonte, metodo, arg in piano:
        dati, errore = _chiama(fonti, metodo, arg)
        if errore:
            provate.append({"fonte": nome_fonte, "motivo": errore})
            continue
        if metodo == "companyfacts":
            if not isinstance(dati, dict) or not isinstance(dati.get("facts"), dict):
                provate.append({"fonte": nome_fonte, "motivo": "companyfacts assente per CIK %s" % arg})
                continue
            candidati = _candidati_sec(dati, fine, classi)
        elif metodo == "esef":
            if not isinstance(dati, dict) or not (dati.get("filings") or {}):
                provate.append({"fonte": nome_fonte, "motivo": "nessun deposito ESEF per LEI %s%s" % (
                    arg, " (%s)" % dati.get("index_error") if isinstance(dati, dict) and dati.get("index_error")
                    else "")})
                continue
            candidati = _candidati_esef(dati, fine, classi)
        else:
            if not isinstance(dati, dict):
                provate.append({"fonte": nome_fonte, "motivo": "fornitore senza risposta"})
                continue
            candidati = _candidati_fornitore(dati, fine, classi, durata_certa)
        if not candidati:
            provate.append({"fonte": nome_fonte, "motivo": "nessun dato della fonte per il periodo con fine %s%s"
                            % (fine.isoformat(), " (durata %s)" % "/".join(classi) if classi else "")})
            continue
        candidati, motivo_valuta = _valuta_esclude(doc_valuta, candidati)
        if motivo_valuta:
            provate.append({"fonte": nome_fonte, "motivo": motivo_valuta})
            continue
        giudizio = _giudica(nome_fonte, estratte, candidati)
        provate.append({"fonte": nome_fonte, "esito": giudizio["esito"], "motivo": giudizio["motivo"]})
        note = []
        if not doc_valuta:
            note.append("valuta del documento non rilevata")
        if doc_valuta and any(c.get("valuta") is None for cs in candidati.values() for c in cs):
            note.append("valuta della fonte non dichiarata per alcune voci")
        if not classi:
            note.append("durata del periodo non determinata (confronto su tutte le durate con la stessa fine)")
        if giudizio["esito"] == "riscontrato":
            nomi = ", ".join(NOMI_VOCI[v] for v in VOCI if v in giudizio["coincidenti"])
            etichetta = "verificato per riscontro numerico con %s (%s)" % (nome_fonte, nomi)
            return _esito("riscontrato", voci=giudizio["voci"], fonte=nome_fonte,
                          motivo="; ".join(note) or None, etichetta=etichetta, fonti_provate=provate, **base)
        if giudizio["esito"] == "diverso":
            etichetta = "numeri diversi da %s per lo stesso periodo (%s): non attribuibile all'emittente" % (
                nome_fonte, giudizio["motivo"])
            return _esito("diverso", voci=giudizio["voci"], fonte=nome_fonte,
                          motivo="; ".join([giudizio["motivo"]] + note), etichetta=etichetta,
                          fonti_provate=provate, **base)
        ultime_voci = giudizio["voci"]
        provate[-1]["voci"] = ultime_voci
    motivo = "; ".join("%s: %s" % (p["fonte"] or "fonti", p["motivo"]) for p in provate) or "nessuna fonte"
    voci_ultime = next((p.get("voci") for p in reversed(provate) if p.get("voci")), [])
    return _esito("senza_confronto", voci=voci_ultime, fonte=None, motivo=motivo,
                  etichetta="non verificato per riscontro numerico: " + motivo, fonti_provate=provate, **base)
