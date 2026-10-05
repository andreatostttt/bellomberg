# -*- coding: utf-8 -*-
"""emarket_documenti.py — sezione DOCUMENTI di eMarket Storage (SDIR Teleborsa): data di
STOCCAGGIO delle relazioni finanziarie (handoff-3, 05/10/2026, Opus 5.5, agente IT3).

PERCHE' ESISTE. `emarket_sdir.get_data_deposito` cerca la data nei COMUNICATI: il comunicato
che annuncia la messa a disposizione. Le misure M1a/M1b (FTSE MIB + Mid Cap) hanno trovato molti
«non_trovato» (titoli generici, nessun avviso, avvisi del dividendo settimane dopo). La sonda S2
(05/10, 98 richieste) ha misurato che la pagina `/it/documenti` e' il registro dei DOCUMENTI
STOCCATI, distinto dai comunicati (protocolli disgiunti): sui 12 emittenti provati trova
l'annuale 2025 in 12/12 casi e la semestrale al 30/06/2026 in 12/12 casi. Questo modulo e'
l'ANCORA PRIMARIA; il comunicato resta la conferma (lo integra IT2 in get_data_deposito).

DEFINIZIONE DELLA DATA (dichiarata nei `limiti`): e' la data/ora di STOCCAGGIO del documento su
eMarket Storage, non quella di diffusione del comunicato. Le due coincidono quasi sempre nel
giorno; dove no (S2, emittenti del FTSE MIB): un annuale -24 g rispetto all'avviso del dividendo, un
altro -1 g rispetto all'avviso stampa, una semestrale +1 g rispetto al comunicato.

TERMINI D'USO (disclaimer Teleborsa, letto da S2 il 05/10/2026, stesso regime di 1INFO,
decisione PM): i contenuti si usano SOLO per uso personale e non commerciale; la riproduzione e
la ridistribuzione sono vietate; i collegamenti diretti richiedono il consenso di Teleborsa.
Quindi: nessun contenuto vero nel repo (le fixture dei test sono SINTETICHE), nessun PDF/ZIP
ridistribuito, nessun link vivo nel README o nel pubblico. Il modulo legge solo le LISTE (HTML),
non scarica i documenti.

MISURE (S2, pagine salvate nello scratchpad della sessione):
  - URL `/it/documenti?azienda=<id>&data_from=AAAA-MM-GG&data_to=AAAA-MM-GG[&categoria=<cod>]
    [&page=N]`; HTML Drupal identico alla lista dei comunicati, 24 righe per pagina, pager con
    `pager__item--next`. Il modulo di ricerca ha id `...-blocco-ricerca-documenti` (quello dei
    comunicati `...-blocco-ricerca-comunicati`): e' la prova che la pagina e' la sezione giusta.
  - riga: `data-protocollo`, `<time>gg/mm/aaaa - hh:mm</time>` (l'attributo datetime e'
    spazzatura), link al file in `/sites/default/files/comunicati/AAAA-MM/*.pdf` oppure, per il
    formato ESEF, in `/sites/default/files/xbrl/AAAA-MM/*.zip|.xbri` con `<span class="icon-esef">`.
  - `data_to` del sito e' ESCLUSIVO (misura RV-IT3 05/10: data_from=data_to=06/08 -> 0 righe,
    data_to=07/08 -> le righe del 06/08): per leggere fino a data_a INCLUSO si manda data_a + 1 giorno
    (e l'eco atteso e' quello); le righe si verificano comunque <= data_a. data_from e' inclusivo.
  - ora = ora locale italiana (fuso 'Europe/Rome' DEDOTTO, non scritto dal sito: orari compatibili
    coi comunicati pre-apertura delle 07:00); `fuso` nel ritorno e in ogni riga.
  - TRAPPOLA DATE: con le date in formato italiano il filtro e' IGNORATO (tornano tutte le righe)
    o da' un falso vuoto. Si mandano SOLO date ISO e si VERIFICA: l'eco del modulo (data_from,
    data_to, categoria, azienda) deve essere quello chiesto e ogni riga deve cadere nella finestra,
    altrimenti KO 'filtro_ignorato'.
  - la categoria NON compare nella riga: si misura con una lista filtrata per categoria (default
    101 = 1.2, la semestrale) e si marca la riga se il suo protocollo vi compare. `categoria`
    None = NON misurata, non «altra categoria».
  - copertura non totale: due resoconti trimestrali misurati da S2 hanno SOLO il comunicato. Un
    `non_trovato` di candidati_documento non e' mai un verdetto finale da solo.

CONTRATTO (interfaccia dichiarata a IT2 e D4 il 05/10; chi la cambia li avvisa):
    leggi_documenti(id_emarket, *, data_da, data_a=None, categorie=(101,)) -> {
        "id_emarket", "data_da", "data_a", "fonte",
        "stato": "ok"|"vuoto_misurato"|"KO"|"non_coperto"|"STALE", "errore", "motivo", "fuso",
        "righe": [{"data": "AAAA-MM-GG", "ora": "HH:MM"|None, "fuso", "titolo", "url", "protocollo",
                   "categoria": int|None, "categorie": [int], "esef": bool, "lingua": "it"|"en"|None}],
        "url_liste", "sha256_liste": {url: sha256}, "pagine_lette", "categorie_lette",
        "righe_illeggibili", "letto_il", "limiti", "cache"}
    candidati_documento(righe, tipo, periodo_fine) -> {   # PURA, senza rete (riverifica)
        "stato": "ok"|"ambiguo"|"non_trovato", "scelto": riga|None, "candidati", "conferme",
        "scartati": [{"titolo", "data", "motivo"}], "prova": 'esef'|'categoria+nome'|'nome+periodo'|None,
        "motivo", "note"}
    parse_documenti(html, id_emarket, *, data_da=None, data_a=None, categoria=None) -> pagina
    unisci_righe(righe, {categoria: righe_della_lista_di_categoria}) -> righe marcate
    finestra_documenti(tipo, periodo_fine) -> (data_da, data_a) consigliata
"""
import base64
import gzip
import hashlib
import re
import time
from datetime import date, timedelta
from html import unescape
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bellomberg.market_data import borsa_italiana as _bi
from bellomberg.market_data.emarket_sdir import DOCUMENTI_DEPOSITO, coerenza_tipo_periodo

FONTE = "eMarket Storage (SDIR Teleborsa), sezione Documenti"
BASE = "https://www.emarketstorage.it"
URL_DOCUMENTI = BASE + "/it/documenti?azienda={id}&data_from={da}&data_to={a}"
TTL_DOCUMENTI_S = 12 * 3600
PAUSA_S = 1.5             # pausa fra due richieste: uso personale a basso volume
MAX_PAGINE = 8            # per lista; oltre = KO 'troncato' (le righe perse sarebbero le PIU' ANTICHE)
CATEGORIA_SEMESTRALE = 101
# finestra dopo la fine del periodo entro cui un documento conta (giorni). Termini di legge:
# annuale 4 mesi (+ ripubblicazioni misurate fino a +138 g), semestrale 3 mesi.
FINESTRA_GIORNI = {"annuale": 200, "semestrale": 120, "trimestrale": 120}
# finestra di LETTURA consigliata (finestra_documenti): piu' corta, 1-2 pagine (misura S2)
FINESTRA_LETTURA = {"annuale": 150, "semestrale": 120, "trimestrale": 120}
MAX_SCARTATI = 15
RIGHE_PER_PAGINA = 24   # misura S2: una pagina piena; piena e senza pager riconosciuto = si sonda la pagina dopo
FUSO = "Europe/Rome"
_TIPI = ("annuale", "semestrale", "trimestrale")
_FORM_DOCUMENTI = "views-exposed-form-search-news-blocco-ricerca-documenti"

LIMITI_FISSI = [
    "data = STOCCAGGIO del documento su eMarket Storage (sezione Documenti), non la diffusione del "
    "comunicato: le due possono differire di giorni (misura S2: annuale -24 g e -1 g, semestrale +1 g)",
    "termini d'uso Teleborsa: solo uso personale e non commerciale; nessun contenuto vero nel repo, "
    "nessun documento ridistribuito, nessun link vivo nel pubblico",
    "la sezione Documenti non e' completa: un documento assente non vuol dire «non depositato» "
    "(misura S2: alcuni resoconti sono solo nei comunicati)",
    "categoria = misurata con la lista filtrata per categoria (riga marcata se il protocollo vi "
    "compare); None = non misurata",
    "data_to del sito e' ESCLUSIVO (misurato): si chiede data_a + 1 giorno per includere data_a",
    "fuso 'Europe/Rome' DEDOTTO (il sito scrive l'ora senza fuso; orari compatibili con l'ora italiana)",
    "una pagina piena (24 righe) senza pager riconosciuto si verifica leggendo la pagina dopo (una "
    "richiesta in piu', dichiarata): vuota = lista completa (caso vero misurato: 24 documenti esatti), "
    "righe nuove = si continua, stesse righe ripetute = KO layout_cambiato",
]


class ParametroDocumenti(ValueError):
    """tipo/periodo non validi in candidati_documento (errore di chi chiama, mai un verdetto)."""


# ============================================================
# DATE E FINESTRE
# ============================================================
def _data(x: Any) -> Optional[date]:
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x))
    except (TypeError, ValueError):
        return None


def finestra_documenti(tipo: str, periodo_fine: Any, oggi: Optional[date] = None, *,
                       fine_esercizio: Optional[str] = None) -> Tuple[str, str]:
    """(data_da, data_a) ISO consigliata: [fine+1, fine+N] con N da FINESTRA_LETTURA, data_a
    tagliata a oggi. ParametroDocumenti se tipo/data non validi o la finestra e' nel futuro."""
    fine = _data(periodo_fine)
    if tipo not in _TIPI or fine is None:
        raise ParametroDocumenti("tipo %r o periodo_fine %r non validi" % (tipo, periodo_fine))
    incoerente = coerenza_tipo_periodo(tipo, fine, fine_esercizio)
    if incoerente:
        raise ParametroDocumenti(incoerente)
    oggi = oggi or _bi.oggi_roma()
    da = fine + timedelta(days=1)
    a = min(fine + timedelta(days=FINESTRA_LETTURA[tipo]), oggi)
    if a < da:
        raise ParametroDocumenti("periodo che finisce il %s: nessun giorno trascorso dopo la fine" % fine)
    return da.isoformat(), a.isoformat()


# ============================================================
# PARSE DELLA PAGINA (HTML Drupal)
# ============================================================
_RIGA = re.compile(r'<div class="views-row">(.*?)(?=<div class="views-row">|$)', re.S)
_FILE = re.compile(r'href="(/sites/default/files/(comunicati|xbrl)/[^"]+\.(pdf|zip|xbri))"', re.I)
_DATA_ORA = re.compile(r"<time\b[^>]*>\s*(\d{1,2}/\d{1,2}/\d{4})(?:\s*-\s*(\d{1,2}:\d{2}))?\s*</time>", re.I)
_IT_ESPLICITO = re.compile(r"\bitaliano\b|versione\s+italiana|\(ita\)", re.I)
_EN_ESPLICITO = re.compile(r"versione\s+inglese|english\s+version|\benglish\b|\(eng\)", re.I)
_IT_PAROLE = re.compile(r"\b(?:relazion[ei]|resoconto|bilancio|semestrale|annuale|trimestral[ei]|giugno|marzo|"
                        r"settembre|dicembre|esercizio|gestione|bozza|intermedi[oa]|finanziari[ao]|consolidat[ao]|"
                        r"trimestre|documento)\b", re.I)
_EN_PAROLE = re.compile(r"\b(?:report|financial|statements?|annual|half|interim|june|march|september|december|"
                        r"year|quarter|management)\b", re.I)


def _testo(h: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", h)).split())


def lingua_titolo(titolo: str) -> Optional[str]:
    """'it' | 'en' | None. Le dichiarazioni esplicite («ESEF Italiano», «English») prima delle
    parole; un titolo con parole italiane e' italiano anche se contiene «ESEF»/«Report»."""
    t = (titolo or "").replace("_", " ")   # «EMITTENTE_Relazione ...»: il trattino basso non e' un confine
    if _IT_ESPLICITO.search(t):
        return "it"
    if _EN_ESPLICITO.search(t):
        return "en"
    if _IT_PAROLE.search(t):
        return "it"
    if _EN_PAROLE.search(t):
        return "en"
    return None


def _eco(html: str, nome: str) -> Optional[str]:
    m = re.search(r'<input\b[^>]*\bname="%s"[^>]*>' % re.escape(nome), html)
    if not m:
        return None
    v = re.search(r'\bvalue="([^"]*)"', m.group(0))
    return unescape(v.group(1)) if v else ""


def _selezionato(html: str, nome: str) -> Optional[str]:
    menu = re.search(r'<select\b[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(nome), html, re.S | re.I)
    if not menu:
        return None
    m = re.search(r'<option\s+value="([^"]*)"\s+selected', menu.group(1))
    return m.group(1) if m else None


def data_to_sito(data_a: Any) -> str:
    """Il valore di data_to da mandare al sito per leggere fino a `data_a` INCLUSO (data_to e'
    esclusivo: misura RV-IT3 05/10)."""
    d = _data(data_a)
    if d is None:
        raise ParametroDocumenti("data_a %r non e' una data AAAA-MM-GG" % (data_a,))
    return (d + timedelta(days=1)).isoformat()


def parse_documenti(html: str, id_emarket: int, *, data_da: Optional[str] = None, data_a: Optional[str] = None,
                    categoria: Optional[int] = None) -> Dict[str, Any]:
    """Una pagina della sezione Documenti -> {"stato": ok|vuoto_misurato|KO|non_coperto, "righe",
    "pagina_successiva" (True | False | None = pagina piena senza pager: da verificare), "errore", "motivo",
    "righe_illeggibili"}. Con data_da/data_a si verifica
    il FILTRO: eco del modulo uguale a quanto chiesto e ogni riga dentro la finestra, altrimenti
    KO 'filtro_ignorato' (trappola misurata da S2). Puro: serve anche alla riverifica di D4."""
    html = html or ""
    vuota = {"righe": [], "pagina_successiva": False, "righe_illeggibili": 0}
    if _FORM_DOCUMENTI not in html:
        return dict(vuota, stato="KO", errore="pagina_diversa",
                    motivo="manca il modulo di ricerca della sezione Documenti: pagina diversa o layout cambiato")
    menu = re.search(r'<select\b[^>]*name="azienda"[^>]*>(.*?)</select>', html, re.S | re.I)
    if menu is None or 'name="categoria"' not in html:
        return dict(vuota, stato="KO", errore="pagina_diversa",
                    motivo="modulo della sezione Documenti senza menu emittenti/categorie: layout cambiato")
    if not re.search(r'<option\s+value="%d"' % int(id_emarket), menu.group(1)):
        return dict(vuota, stato="non_coperto", errore="id_non_nel_menu",
                    motivo="id eMarket %d assente dal menu emittenti: emittente non su eMarket "
                           "(o id sbagliato nel negozio)" % int(id_emarket))
    # eco del filtro: quello che il sito dice di aver applicato
    attese = []
    if data_da is not None:
        attese.append(("data_from", _eco(html, "data_from"), data_da))
    if data_a is not None:
        # data_to del sito e' ESCLUSIVO (misura RV-IT3): l'URL porta data_a + 1 giorno
        attese.append(("data_to", _eco(html, "data_to"), data_to_sito(data_a)))
    attese.append(("azienda", _selezionato(html, "azienda"), str(int(id_emarket))))
    attese.append(("categoria", _selezionato(html, "categoria"), str(categoria) if categoria is not None else "All"))
    for nome, visto, atteso in attese:
        if visto != atteso:
            return dict(vuota, stato="KO", errore="filtro_ignorato",
                        motivo="il modulo riporta %s=%r invece di %r: filtro non applicato dal sito"
                               % (nome, visto, atteso))
    lista = re.search(r'class="[^"]*\bview-content\b', html)
    if lista is None:
        if re.search(r"views-row|azienda-wrapper|pager__item|news-title", html):
            return dict(vuota, stato="KO", errore="layout_cambiato",
                        motivo="la pagina ha marcatori di righe/pager ma non la lista riconosciuta (view-content): "
                               "layout cambiato, non un vuoto")
        return dict(vuota, stato="vuoto_misurato", errore=None,
                    motivo="nessun documento nella finestra %s..%s (filtro verificato sull'eco del modulo)"
                           % (data_da, data_a))
    contenuto = html[lista.start():]
    righe: List[Dict[str, Any]] = []
    illeggibili = 0
    blocchi = _RIGA.findall(contenuto)
    for b in blocchi:
        f = _FILE.search(b)
        do = _DATA_ORA.search(b)
        d = _bi.data_it(do.group(1)) if do else None
        if not f or d is None:
            illeggibili += 1
            continue
        prot = re.search(r'data-protocollo="(\d+)"', b)
        tit = re.search(r'<div class="news-title">(.*?)</div>', b, re.S)
        titolo = _testo(tit.group(1)) if tit else ""
        righe.append({"data": d, "ora": do.group(2), "fuso": FUSO, "titolo": titolo, "url": BASE + f.group(1),
                      "protocollo": prot.group(1) if prot else None,
                      "categoria": categoria, "categorie": [categoria] if categoria is not None else [],
                      "esef": f.group(2).lower() == "xbrl" or 'class="icon-esef"' in b,
                      "lingua": lingua_titolo(titolo)})
    if not righe:
        return dict(vuota, stato="KO", errore="layout_cambiato", righe_illeggibili=illeggibili,
                    motivo="lista presente ma 0 righe leggibili su %d blocchi (attesi: link al file + data "
                           "gg/mm/aaaa): layout cambiato" % len(blocchi))
    # giorno di CONFINE data_a + 1 (= data_to mandato al sito): caso vero MF 05/10 (finestra ..29/07, data_to 30/07,
    # tornata una riga del 30/07 07:00). Con un intervallo normale il sito include data_to (RV-IT3 aveva misurato
    # 0 righe solo con intervallo di ampiezza zero): quelle righe si TOLGONO e si contano (dichiarate in `limiti`)
    confine = data_to_sito(data_a) if data_a is not None else None
    righe_confine = sum(1 for r in righe if r["data"] == confine)
    fuori = [r["data"] for r in righe
             if (data_da is not None and r["data"] < data_da) or (confine is not None and r["data"] > confine)]
    righe = [r for r in righe if r["data"] != confine]
    if fuori:
        return dict(vuota, stato="KO", errore="filtro_ignorato", righe_illeggibili=illeggibili,
                    motivo="la lista contiene %d righe fuori dalla finestra chiesta %s..%s (es. %s): filtro "
                           "data ignorato dal sito" % (len(fuori), data_da, data_a, fuori[0]))
    succ: Optional[bool] = "pager__item--next" in html
    if not succ and len(blocchi) >= RIGHE_PER_PAGINA and "pager__item" not in html:
        # pagina PIENA senza pager riconosciuto: non si sa se ci sono altre pagine (le righe perse sarebbero le
        # piu' antiche). None = «da verificare»: chi pagina legge la pagina dopo (decisione main 05/10: caso
        # vero misurato da MF, 24 documenti esatti nella finestra e nessun pager)
        succ = None
    return {"stato": "ok", "righe": righe, "pagina_successiva": succ, "errore": None,
            "motivo": ("%d righe illeggibili saltate (senza file o senza data)" % illeggibili) if illeggibili else None,
            "righe_illeggibili": illeggibili, "righe_confine": righe_confine}


def unisci_righe(righe: List[Dict[str, Any]], per_categoria: Dict[int, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Marca le righe della lista completa con le categorie misurate (protocollo, o url se manca).
    `categoria` = la prima categoria misurata in cui la riga compare (ordine di `per_categoria`).
    Una riga presente SOLO in una lista di categoria si aggiunge (con `solo_in_categoria` True)."""
    out = [dict(r, categorie=list(r.get("categorie") or [])) for r in righe]
    indice = {(r.get("protocollo") or r.get("url")): r for r in out}
    for cat, lista in per_categoria.items():
        for rc in lista:
            k = rc.get("protocollo") or rc.get("url")
            r = indice.get(k)
            if r is None:
                r = dict(rc, categorie=[], categoria=None, solo_in_categoria=True)
                out.append(r)
                indice[k] = r
            if cat not in r["categorie"]:
                r["categorie"].append(cat)
            if r.get("categoria") is None:
                r["categoria"] = cat
    return out


# ============================================================
# LETTURA DALLA RETE
# ============================================================
def _base(id_emarket: Any, data_da: Any, data_a: Any) -> Dict[str, Any]:
    return {"id_emarket": id_emarket, "data_da": data_da, "data_a": data_a, "fonte": FONTE,
            "stato": "KO", "errore": None, "motivo": None, "righe": [], "url_liste": [],
            "sha256_liste": {}, "pagine_lette": 0, "categorie_lette": [], "righe_illeggibili": 0,
            "letto_il": None, "fuso": FUSO, "limiti": list(LIMITI_FISSI), "cache": {"stato": "nessuna", "eta_s": None},
            "risposte_salvate": {}}


class RicevutaIncompleta(Exception):
    """rileggi_documenti: la lettura chiede un URL assente dalle risposte salvate o con sha diverso."""


def _leggi_lista(out: Dict[str, Any], id_em: int, da: str, a: str,
                 categoria: Optional[int], scarica=None, pausa: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Tutte le pagine di una lista. Ritorna {"stato", "righe"} oppure None se ha scritto un KO in `out`.
    `scarica`/`pausa`: None = rete vera (borsa_italiana._scarica, PAUSA_S); rileggi_documenti passa i corpi salvati."""
    scarica = scarica or _bi._scarica
    pausa = PAUSA_S if pausa is None else pausa
    base = URL_DOCUMENTI.format(id=id_em, da=da, a=data_to_sito(a)) + ("&categoria=%d" % categoria if categoria is not None else "")
    righe: List[Dict[str, Any]] = []
    sonda = False      # True = questa pagina e' la verifica dopo una pagina piena senza pager
    for pagina in range(MAX_PAGINE + 1):
        if pagina == MAX_PAGINE:
            out.update(errore="troncato",
                       motivo="lista %s: piu' di %d pagine; le righe non lette sono le PIU' ANTICHE (quelle che "
                              "decidono la data): nessuna lista parziale servita, restringere la finestra"
                              % ("completa" if categoria is None else "categoria %d" % categoria, MAX_PAGINE))
            return None
        url = base + ("&page=%d" % pagina if pagina else "")
        if out["pagine_lette"] and pausa:
            time.sleep(pausa)
        try:
            http, corpo, _ = scarica(url)
        except RicevutaIncompleta as e:
            out.update(errore="ricevuta", motivo="ricevuta: %s" % e.args[0])
            return None
        except _bi.URLVietato:
            out.update(errore="url_vietato", motivo="URL rifiutato prima della rete (robots.txt o host non previsto)")
            return None
        except Exception as e:
            out.update(errore="rete", motivo="richiesta fallita (%s, pagina %d): %s"
                       % ("lista completa" if categoria is None else "categoria %d" % categoria, pagina + 1,
                          type(e).__name__))
            return None
        if http != 200:
            out.update(errore="http", motivo="HTTP %s da eMarket (pagina %d)" % (http, pagina + 1))
            return None
        out["pagine_lette"] += 1
        out["url_liste"].append(url)
        out["sha256_liste"][url] = hashlib.sha256(corpo).hexdigest()   # sul corpo NON compresso
        out["risposte_salvate"][url] = {"codifica": "gzip+base64",
                                        "corpo": base64.b64encode(gzip.compress(corpo, mtime=0)).decode("ascii")}
        p = parse_documenti(corpo.decode("utf-8", errors="replace"), id_em, data_da=da, data_a=a, categoria=categoria)
        if p["stato"] in ("KO", "non_coperto"):
            if p["stato"] == "non_coperto":
                out["stato"] = "non_coperto"
                out["letto_il"] = _bi.adesso_utc().isoformat(timespec="seconds")
            out.update(errore=p["errore"], motivo="pagina %d: %s" % (pagina + 1, p["motivo"]) if pagina else p["motivo"])
            return None
        out["righe_illeggibili"] += p["righe_illeggibili"]
        out["righe_confine"] = out.get("righe_confine", 0) + p.get("righe_confine", 0)
        if p["stato"] == "vuoto_misurato":
            if sonda:
                out["limiti"].append("pagina %d piena senza pager: lette la pagina %d (vuota) per verificarlo, "
                                     "lista completa" % (pagina, pagina + 1))
                return {"stato": "ok", "righe": righe}
            if pagina:
                out.update(errore="layout_cambiato", motivo="pagina %d vuota dopo un «successiva»" % (pagina + 1))
                return None
            return {"stato": "vuoto_misurato", "righe": []}
        if sonda:
            visti = {r.get("protocollo") or r.get("url") for r in righe}
            nuove = [r for r in p["righe"] if (r.get("protocollo") or r.get("url")) not in visti]
            if not nuove:
                out.update(errore="layout_cambiato",
                           motivo="pagina %d piena senza pager e la pagina %d ripete le stesse righe: paginazione "
                                  "non riconoscibile" % (pagina, pagina + 1))
                return None
            out["limiti"].append("pagina %d piena senza pager riconosciuto ma la pagina %d ha righe nuove: pager "
                                 "non riconosciuto, paginazione proseguita" % (pagina, pagina + 1))
        righe.extend(p["righe"])
        sonda = p["pagina_successiva"] is None
        if p["pagina_successiva"] is False:
            return {"stato": "ok", "righe": righe}
    return None  # non raggiungibile: il ciclo esce sempre prima


MAX_RISPOSTE_BYTE = 1_000_000   # AGGIUNTA 5: oltre 1 MB compresso la dimensione si dichiara nei limiti


def _dichiara_dimensione(out: Dict[str, Any]) -> None:
    n = sum(len(v.get("corpo") or "") for v in (out.get("risposte_salvate") or {}).values())
    if n > MAX_RISPOSTE_BYTE:
        out["limiti"].append("risposte_salvate: %d byte compressi (oltre %d): ricevuta voluminosa" % (n, MAX_RISPOSTE_BYTE))


def _leggi(id_em: int, da: str, a: str, categorie: Tuple[int, ...], scarica=None,
           pausa: Optional[float] = None) -> Dict[str, Any]:
    out = _base(id_em, da, a)
    tutte = _leggi_lista(out, id_em, da, a, None, scarica, pausa)
    if tutte is None:
        return out
    per_cat: Dict[int, List[Dict[str, Any]]] = {}
    if tutte["stato"] == "ok":
        for cat in categorie:
            lc = _leggi_lista(out, id_em, da, a, cat, scarica, pausa)
            if lc is None:
                # senza la lista di categoria la marca 101 non e' misurabile: KO, non righe «senza categoria»
                out["motivo"] = "lista categoria %d: %s" % (cat, out["motivo"])
                return out
            per_cat[cat] = lc["righe"]
            out["categorie_lette"].append(cat)
    out["letto_il"] = _bi.adesso_utc().isoformat(timespec="seconds")
    if out.get("righe_confine"):
        out["limiti"].append("%d righe del giorno di confine %s (data_a + 1) escluse: il filtro del sito include "
                             "data_to" % (out["righe_confine"], data_to_sito(a)))
    if tutte["stato"] == "ok" and not tutte["righe"]:
        tutte = {"stato": "vuoto_misurato", "righe": []}   # solo righe del giorno di confine: nella finestra, nulla
    _dichiara_dimensione(out)
    if tutte["stato"] == "vuoto_misurato":
        out.update(stato="vuoto_misurato", motivo="nessun documento stoccato nella finestra %s..%s "
                                                  "(filtro verificato sull'eco del modulo)" % (da, a))
        return out
    righe = unisci_righe(tutte["righe"], per_cat)
    solo_cat = sum(1 for r in righe if r.get("solo_in_categoria"))
    if solo_cat:
        out["limiti"].append("%d righe presenti solo nella lista di categoria (aggiunte)" % solo_cat)
    if out["righe_illeggibili"]:
        out["limiti"].append("%d righe senza link al file o senza data, saltate (es. documenti linkati a una "
                             "pagina del sito): se fra queste c'e' la relazione, non e' vista" % out["righe_illeggibili"])
    out.update(stato="ok", righe=righe)
    return out


def leggi_documenti(id_emarket: int, *, data_da: Any, data_a: Any = None,
                    categorie: Iterable[int] = (CATEGORIA_SEMESTRALE,)) -> Dict[str, Any]:
    """Documenti stoccati dall'emittente fra data_da e data_a (inclusi; data_a None = oggi).
    Vedi CONTRATTO nel docstring del modulo. Cache 12 h solo per ok/vuoto_misurato; un KO con una
    lettura buona scaduta in cache diventa STALE dichiarato (borsa_italiana.con_cache)."""
    da, a = _data(data_da), _data(data_a) if data_a is not None else _bi.oggi_roma()
    cats = tuple(categorie or ())
    if isinstance(id_emarket, bool) or not isinstance(id_emarket, int) or id_emarket <= 0 or da is None or a is None \
            or a < da or any(isinstance(c, bool) or not isinstance(c, int) for c in cats):
        out = _base(id_emarket, data_da, data_a)
        out.update(errore="parametro", motivo="id_emarket intero positivo, data_da <= data_a in AAAA-MM-GG, "
                                              "categorie intere: ricevuti %r, %r, %r, %r"
                                              % (id_emarket, data_da, data_a, cats))
        return out
    chiave = "emarket_documenti_v1_%d_%s_%s_%s" % (id_emarket, da.isoformat(), a.isoformat(),
                                                   "-".join(str(c) for c in cats) or "nessuna")
    return _bi.con_cache(chiave, TTL_DOCUMENTI_S, lambda: _leggi(id_emarket, da.isoformat(), a.isoformat(), cats))


# ============================================================
# SCELTA DEL DOCUMENTO (pura)
# ============================================================
_ESCLUSIONI = (
    ("relazione della societa' di revisione / del collegio sindacale",
     re.compile(r"revision|revisore|auditor|collegio\s+sindacale", re.I)),
    ("relazione illustrativa / del CdA sui punti all'ordine del giorno",
     re.compile(r"illustrativ|explanatory|ordine\s+del\s+giorno|\bodg\b|agenda|\bpunt[oi]\b|\bitems?\b|"
                r"relazion[ei]\s+del\s+(?:cda|consiglio)|relazioni\s+degli\s+amministratori|report\s+(?:of|by)\s+the\s+bod|"
                r"directors.{0,3}\s+reports?|\brel\.\s*ill", re.I)),
    ("governance", re.compile(r"governance|governo\s+societario|assetti\s+proprietari", re.I)),
    ("remunerazione", re.compile(r"remunera|compensi|compensation", re.I)),
    ("dividendo", re.compile(r"dividend|acconto", re.I)),
    ("presentazione / risultati", re.compile(r"presentazion|presentation|risultat|\bresults?\b|ricavi|revenues", re.I)),
    ("green bond / sostenibilita' separata", re.compile(r"green\s+bond|rendicontazione", re.I)),
    ("assemblea, statuto, delega, avviso o altro documento societario",
     re.compile(r"verbal|minutes|statut|bylaws|by-laws|articles?\s+of\s+association|delega|proxy|"
                r"documento\s+informativo|information\s+(?:document|memorandum|circular)|informative\s+document|"
                r"\blista\b|\blist\b|avviso|notice|convocazione|prospetto\s+informativo|prospectus|offert|tender|scission|demerger|"
                r"fusione|merger|estratto|excerpt|\bpatto|piano\s+di|plan\b|strategic|azioni\s+proprie|treasury", re.I)),
)
# NOMI dei documenti: UNA sola fonte, emarket_sdir.DOCUMENTI_DEPOSITO (ordine main 05/10; la usa anche D4
# per il tipo in copertina). Qui restano solo le regole STRUTTURALI (ESEF, esclusioni, categorie, periodo).
# parola da «relazione» per la semestrale riconosciuta dalla categoria 101 (anche nome sbagliato:
# «Resoconto intermedio di gestione al 30 giugno», «XX - 2Q2026 relazione»: misure S2)
_PAROLA_RELAZIONE = re.compile(r"relazion|resocont|report|bilancio|financial\s+statements", re.I)
_MESI_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
            "settembre", "ottobre", "novembre", "dicembre")
_MESI_EN = ("january", "february", "march", "april", "may", "june", "july", "august",
            "september", "october", "november", "december")
_BOZZA = re.compile(r"bozza|progetto\s+di\s+bilancio|draft", re.I)
# allegato della revisione nel titolo di una ripubblicazione dello stesso documento (caso vero MF 05/10, FTSE MIB)
_CON_REVISIONE = re.compile(r"(?:\bcon\b|corredat\w*|comprensiv\w*|inclusiv\w*|including|\bwith\b|together\s+with)\s+"
                            r"(?:(?:da|dalla|dalle|dai|by)\s+)?(?:la\s+|le\s+|the\s+)?(?:relazion\w+\s+(?:della\s+)?societ\S*\s+di\s+revisione|"
                            r"(?:independent\s+)?auditor\S*\s+reports?)", re.I)
# Connettivi di ALLEGATO fra il nome forte e la parola esclusa (secondo giro RV-IT3: «Annual Report on
# Corporate Governance» e «Relazione semestrale ... - Relazione della societa' di revisione» restano esclusi)
_CONNETTIVO = re.compile(r"comprensiv|corredat|inclusiv|includ|unitamente|together\s+with|\bcon\b|\bcol\b|"
                         r"\bcorredo\b|\be\b|\bed\b|\band\b|\bwith\b", re.I)
# NOME FORTE della relazione: se compare PRIMA della parola che escluderebbe ED e' legato da un connettivo, il titolo e' la relazione
# con gli allegati citati dopo («... comprensiva della Rendicontazione di sostenibilita'», «... corredata
# dalle relazioni del Collegio Sindacale e della Societa' di Revisione», «... e risultati consolidati»,
# «- prospetti contabili»: rilievo RV-IT3). Se compare DOPO («Relazione della societa' di revisione
# sulla relazione finanziaria semestrale») l'esclusione resta.
_NOME_FORTE = re.compile(r"relazione\s+finanziaria\s+(?:consolidata\s+)?(?:annuale|semestrale)|"
                         r"relazione\s+annuale\s+integrata|resoconto\s+intermedio\s+di\s+gestione|"
                         r"(?:annual|half[\s-]*year(?:ly)?|semi[\s-]*annual|interim)\s+(?:financial\s+)?report|"
                         r"annual\s+report", re.I)
# date esplicite nel titolo (gg/mm/aaaa, gg.mm.aa, «30 giugno 2026», «June 30, 2026»)
_DATE_TITOLO = re.compile(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})(?!\d)|"
                          r"\b(\d{1,2})\s+(?:al\s+)?(%s)\s+(\d{4})\b|\b(%s)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"
                          % ("|".join(("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                                       "settembre", "ottobre", "novembre", "dicembre", "january", "february", "march",
                                       "april", "may", "june", "july", "august", "september", "october", "november",
                                       "december")),
                             "|".join(("january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"))), re.I)


_DATA_SPAZI = re.compile(r"\b(\d{1,2})\s+(\d{1,2})\s+(20\d\d)\b")   # «31 03 2026» (anno a 4 cifre)


def _date_nel_titolo(t: str) -> List[date]:
    out = []
    for m in _DATA_SPAZI.finditer(t):
        try:
            out.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            continue
    for m in _DATE_TITOLO.finditer(t):
        try:
            if m.group(1):
                a = int(m.group(3))
                out.append(date(a + 2000 if a < 100 else a, int(m.group(2)), int(m.group(1))))
            elif m.group(4):
                mese = m.group(5).lower()
                n = (_MESI_IT.index(mese) if mese in _MESI_IT else _MESI_EN.index(mese)) + 1
                out.append(date(int(m.group(6)), n, int(m.group(4))))
            else:
                out.append(date(int(m.group(9)), _MESI_EN.index(m.group(7).lower()) + 1, int(m.group(8))))
        except ValueError:
            continue
    return out


_PERIODO_ESPLICITO = re.compile(r"(?:\bal|\bas\s+(?:at|of)|\bended|\bchiuso\s+al|\bat)\s+((?:\d{1,2}|[a-z]+)[^;()]{0,25})",
                                re.I)


def altro_periodo(t: str, fine: date, tipo: str, fine_esercizio: Optional[str] = None) -> Optional[str]:
    """Motivo se il titolo nomina ESPLICITAMENTE un altro periodo (rilievo RV-IT3: ESEF 2024
    ripubblicato dopo la fine del 2025, semestrale 2025 «nuova versione» in categoria 101,
    «Relazione finanziaria al 30/09/2026» in 101). None se non nomina periodi o nomina il giusto."""
    # contano solo le date di FINE TRIMESTRE (31/03, 30/06, 30/09, 31/12): assemblea, CdA, pubblicazione si
    # ignorano (secondo giro RV-IT3: «Assemblea del 29.04.2026», «approvata dal CdA del 04.08.2026»)
    mesi_fine = {3, 6, 9, 12}
    if fine_esercizio is not None:
        # esercizio NON solare: anche le fine-trimestre dell'esercizio (es. chiusura 30/04 -> 31/07, 31/10, 31/01)
        fe = int(str(fine_esercizio)[:2])
        mesi_fine |= {(fe + 3 * k - 1) % 12 + 1 for k in range(4)}
    fini = [d for d in _date_nel_titolo(t) if d.month in mesi_fine and (d + timedelta(days=1)).day == 1]
    # P1 MF 05/10 (ok SBAGLIATO): «Resoconto intermedio di gestione consolidato al 31 luglio 2026» in cat. 101 preso
    # come semestrale al 30/06 di un esercizio solare. Una FINE MESE introdotta dalla preposizione del periodo
    # («al», «as at», «as of», «ended», «chiuso al») e' il periodo dichiarato, qualunque mese: conta anche lei.
    # Le date di assemblea/CdA («del 30.04.2026») restano ignorate.
    for m in _PERIODO_ESPLICITO.finditer(t):
        fini += [d for d in _date_nel_titolo(m.group(1))[:1] if (d + timedelta(days=1)).day == 1 and d not in fini]
    anni = {int(x) for x in re.findall(r"(?<![\d/.-])(20\d\d)(?![\d/.-])", t)}
    validi = {fine.year} | ({fine.year - 1} if tipo == "annuale" and fine.month != 12 else set())
    if fine_esercizio is not None:
        validi |= {fine.year - 1, fine.year + 1}   # «esercizio 2026/2027»: l'anno puo' essere quello d'inizio o di chiusura
    if fini and fine not in fini:
        # «annuale 2025 (dati comparativi al 31.12.2024)»: l'anno giusto nominato da solo, nessuna altra
        # fine trimestre di quell'anno -> e' il periodo giusto con un riferimento accessorio
        if not (anni & validi and not any(d.year in validi for d in fini)):
            return "il titolo nomina un altro periodo (%s), non il %s" % (fini[0].isoformat(), fine.isoformat())
    if anni and not anni & validi and not fini:
        return "il titolo nomina l'anno %s, non l'esercizio/periodo %d" % (", ".join(str(x) for x in sorted(anni)), fine.year)
    return None


def _esclusione(t: str, esef: bool) -> Optional[str]:
    """Il motivo d'esclusione, o None. Una riga ESEF non e' mai una relazione di revisione o
    illustrativa (e' il formato della relazione finanziaria): esente. Il NOME FORTE prima della
    parola esclusa salva il titolo."""
    if esef:
        return None
    forte = _NOME_FORTE.search(t)
    for motivo, rx in _ESCLUSIONI:
        m = rx.search(t)
        if m and not (forte and forte.end() <= m.start() and _CONNETTIVO.search(t[forte.end():m.start()])):
            return motivo
    return None


def _rx_nome(tipo: str) -> "re.Pattern":
    it, en = DOCUMENTI_DEPOSITO[tipo]
    return re.compile("|".join("(?:%s)" % p for p in (it, en)), re.I)


def _rx_periodo(fine: date, tipo: str, stretto: bool = False, solare: bool = True) -> "re.Pattern":
    """Il periodo scritto nel titolo di un documento («30 giugno 2026», «30/06/2026», «31.12.25»,
    «June 30, 2026», «2Q2026», «I trimestre 2026», refuso «30 al giugno 2026»)."""
    g, m, a = fine.day, fine.month, fine.year
    alt = [r"\b0?%d\s+(?:al\s+)?%s\s+%d\b" % (g, _MESI_IT[m - 1], a),
           r"\b0?%d\s+0?%d\s+%d\b" % (g, m, a),        # «Resoconto intermedio 31 03 2026» (caso vero MF)
           r"\b0?%d\s*[./-]\s*0?%d\s*[./-]\s*(?:%d|%02d)(?!\d)" % (g, m, a, a % 100),
           r"\b%s\s+0?%d(?:st|nd|rd|th)?,?\s+%d\b" % (_MESI_EN[m - 1], g, a),
           r"\b0?%d(?:st|nd|rd|th)?\s+%s,?\s+%d\b" % (g, _MESI_EN[m - 1], a)]
    if tipo == "annuale":
        alt += [r"(?<![\d/.-])%d(?![\d/.-])" % a]          # l'anno dell'esercizio («Annual Report 2025»)
        if m != 12:
            alt += [r"\b%d\s*[/-]\s*(?:%d|%02d)\b" % (a - 1, a, a % 100)]
    elif tipo == "semestrale" and not solare:
        # esercizio non solare: H1/Q2/«primo semestre» + anno sono ambigui (anno solare o fiscale?): solo le date
        if not stretto:
            alt += [r"(?<![\d/.-])%d(?![\d/.-])" % a]
    elif tipo == "trimestrale" and not solare:
        pass                                              # idem: niente «I trimestre»/Q1 + anno, solo le date
    elif tipo == "semestrale":
        alt += [r"\b(?:H1|1H|2Q|Q2)\s*[/&]?\s*(?:1H|H1)?\s*%d\b" % a, r"primo\s+semestre\s+%d" % a,
                r"first\s+half\s+(?:of\s+)?%d" % a]
        if not stretto:   # l'anno da solo («Relazione finanziaria semestrale 2026»): non basta per un trimestre in 101
            alt += [r"(?<![\d/.-])%d(?![\d/.-])" % a]
    elif tipo == "trimestrale":
        q = {3: ("I|primo", "Q1|1Q", "first"), 9: ("III|terzo", "Q3|3Q|9M", "third")}.get(m)
        if q:
            alt += [r"\b(?:%s)\s+trimestre\s+%d" % (q[0], a), r"\b(?:%s)\s*%d\b" % (q[1], a),
                    r"%s\s+quarter\s+(?:of\s+)?%d" % (q[2], a)]
            if m == 9:
                alt += [r"nine[\s-]+month", r"nove\s+mesi\s+%d" % a]
    return re.compile("|".join(alt), re.I)


_TRIMESTRE = re.compile(r"trimestral|\b(?:31|30)\s*[./-]\s*0?(?:3|9)(?!\d)|\b(?:I|III|primo|terzo)\s+trimestre|\b(?:Q1|1Q|Q3|3Q|9M)\b|31\s+marzo|30\s+settembre|"
                        r"march\s+31|31\s+march|september\s+30|30\s+september|nine[\s-]+month", re.I)


def candidati_documento(righe: List[Dict[str, Any]], tipo: str, periodo_fine: Any, *,
                        fine_esercizio: Optional[str] = None) -> Dict[str, Any]:
    """Dalle righe di leggi_documenti al documento della relazione `tipo` del periodo. PURA.
    Regole (misura S2 sui 12 emittenti):
      - contano solo le righe stoccate DOPO la fine del periodo ed entro FINESTRA_GIORNI[tipo];
      - esclusioni sempre (revisione, illustrative/CdA sui punti dell'OdG, governance,
        remunerazione, dividendo, presentazioni/risultati, green bond, documenti assembleari):
        in `scartati` col motivo;
      - annuale: le righe ESEF (link /xbrl/ .zip/.xbri) -> comanda la data PIU' ANTICA (prova
        'esef'), le successive (ripubblicazione, versione EN) in `conferme` con nota. Senza ESEF:
        nome + periodo nel titolo (prova 'nome+periodo');
      - semestrale: categoria 101 + parola da relazione (anche nome sbagliato o senza periodo,
        prova 'categoria+nome'), altrimenti nome della semestrale + periodo ('nome+periodo');
      - trimestrale: nome + periodo nel titolo ('nome+periodo'), categoria ignorata;
      - lingua (non ESEF): comanda l'italiano; l'italiano in piu' GIORNI = ambiguo (mai il primo a
        caso); se la versione inglese e' di un giorno PRECEDENTE vince la piu' antica (nota);
        stesso giorno = la prima per ora, le altre in conferme.
    `non_trovato` = nessun documento nella sezione: NON vuol dire «non depositato».
    `fine_esercizio` 'MM-GG' (decisione main 05/10) per un esercizio NON solare: coerenza tipo/periodo
    sull'esercizio vero (es. chiusura 30/04 -> trimestrali 31/07 e 31/01, semestrale 31/10), periodo nel
    titolo solo per data esplicita (le etichette H1/Q1/«primo semestre» + anno sono ambigue), anni validi
    anche quelli adiacenti («esercizio 2026/2027»). None = esercizio solare, comportamento invariato."""
    fine = _data(periodo_fine)
    if tipo not in _TIPI or fine is None:
        raise ParametroDocumenti("tipo %r (ammessi: %s) o periodo_fine %r non validi"
                                 % (tipo, ", ".join(_TIPI), periodo_fine))
    incoerente = coerenza_tipo_periodo(tipo, fine, fine_esercizio)
    if incoerente:
        raise ParametroDocumenti(incoerente)    # es. trimestrale al 30/06 (stessa regola di get_data_deposito)
    nome = _rx_nome(tipo)
    solare = fine_esercizio is None
    per = _rx_periodo(fine, tipo, solare=solare)
    per_stretto = _rx_periodo(fine, tipo, stretto=True, solare=solare)
    limite = (fine + timedelta(days=FINESTRA_GIORNI[tipo])).isoformat()
    scartati: List[Dict[str, Any]] = []
    note: List[str] = []
    cand: List[Dict[str, Any]] = []
    visti = set()

    def _scarta(r, motivo):
        scartati.append({"titolo": r.get("titolo"), "data": r.get("data"), "protocollo": r.get("protocollo"),
                         "motivo": motivo})

    for r in sorted(righe or [], key=lambda r: (r.get("data") or "", r.get("ora") or "", r.get("protocollo") or "")):
        k = r.get("protocollo") or r.get("url")
        if k in visti:
            continue
        visti.add(k)
        t = " ".join((r.get("titolo") or "").replace("_", " ").split())
        ha_nome = bool(nome.search(t))
        cat101 = CATEGORIA_SEMESTRALE in (r.get("categorie") or []) or r.get("categoria") == CATEGORIA_SEMESTRALE
        pertinente = (ha_nome or (tipo == "annuale" and r.get("esef")) or
                      (tipo == "semestrale" and cat101 and _PAROLA_RELAZIONE.search(t)))
        if not pertinente:
            continue
        if not r.get("data") or r["data"] <= fine.isoformat():
            continue                                # documento di un periodo precedente: silenzio voluto
        if r["data"] > limite:
            _scarta(r, "stoccato il %s, oltre %d giorni dalla fine del periodo" % (r["data"], FINESTRA_GIORNI[tipo]))
            continue
        escl = _esclusione(t, bool(r.get("esef")))
        if escl:
            _scarta(r, "esclusa: %s" % escl)
            continue
        altro = altro_periodo(t, fine, tipo, fine_esercizio)
        if altro:
            _scarta(r, altro)
            continue
        if tipo == "annuale" and r.get("esef"):
            cand.append(dict(r, prova="esef"))
            continue
        if tipo == "semestrale" and cat101 and _PAROLA_RELAZIONE.search(t):
            if _TRIMESTRE.search(t) and not per_stretto.search(t):
                _scarta(r, "categoria 101 ma titolo di un trimestre, non della semestrale")
                continue
            cand.append(dict(r, prova="categoria+nome"))
            continue
        if ha_nome and per.search(t):
            cand.append(dict(r, prova="nome+periodo"))
            continue
        _scarta(r, "nome del documento senza il periodo %s nel titolo" % fine.isoformat())

    scartati_n = len(scartati)
    scartati = scartati[:MAX_SCARTATI]
    esito = {"stato": "non_trovato", "scelto": None, "candidati": [], "conferme": [], "scartati": scartati,
             "prova": None, "motivo": None, "note": note}
    if not cand:
        esito["motivo"] = ("nessun documento della relazione %s al %s stoccato nella sezione Documenti fra il %s "
                           "e il %s (scartati: %d); NON vuol dire «non depositato»: guardare i comunicati"
                           % (tipo, fine.isoformat(), (fine + timedelta(days=1)).isoformat(), limite, scartati_n))
        return esito
    chiave_t = lambda c: (c["data"], c.get("ora") or "99:99", c.get("protocollo") or "")
    esef = sorted([c for c in cand if c["prova"] == "esef"], key=chiave_t)
    if esef:
        scelto = esef[0]
        conferme = []
        for c in sorted(cand, key=chiave_t):
            if c is scelto:
                continue
            if c["data"] < scelto["data"]:
                nota = "documento NON ESEF stoccato PRIMA dell'ESEF (%s): comanda l'ESEF" % scelto["data"]
                note.append("%s del %s (non ESEF) precede l'ESEF: data = quella dell'ESEF" % (c.get("titolo"), c["data"]))
            elif c["data"] == scelto["data"]:
                nota = "stesso giorno del documento scelto (%s)" % ("altro file ESEF" if c.get("esef") else "versione PDF")
            else:
                nota = "ripubblicazione / altra versione%s: piu' tarda della prima (%s)" % (
                    " (EN)" if c.get("lingua") == "en" else "", scelto["data"])
            conferme.append(dict(c, nota=nota))
        if any(c["data"] > scelto["data"] for c in conferme):
            note.append("piu' giorni di stoccaggio per lo stesso documento: data = la PIU' ANTICA (%s)" % scelto["data"])
        if _BOZZA.search(scelto.get("titolo") or ""):
            note.append("il documento ESEF e' la «bozza»/progetto messo a disposizione prima dell'assemblea: e' il "
                        "deposito della relazione, la versione approvata puo' seguire")
        esito.update(stato="ok", scelto=scelto, candidati=esef, conferme=conferme, prova="esef")
        return esito
    it = sorted([c for c in cand if c.get("lingua") == "it"], key=chiave_t)
    altri = sorted([c for c in cand if c.get("lingua") != "it"], key=chiave_t)
    gruppo = it or altri
    # caso vero MF 05/10 (FTSE MIB): «Relazione Finanziaria Semestrale al 30/06/26» (31/07) e la stessa «... con
    # Relazione della Societa' di Revisione» (03/08) sono lo STESSO documento ripubblicato con l'allegato della
    # revisione: vale la piu' antica (come l'ESEF ripubblicato), la ripubblicazione va nelle conferme dichiarata.
    # Solo se c'e' una versione SENZA l'allegato stoccata prima o lo stesso giorno.
    base = [c for c in gruppo if not _CON_REVISIONE.search(c.get("titolo") or "")]
    ripub = [c for c in gruppo if c not in base and base and c["data"] >= base[0]["data"]]
    if ripub:
        note.append("%d ripubblicazioni dello stesso documento con la relazione di revisione (%s): vale la piu' "
                    "antica senza allegato (%s)" % (len(ripub), ", ".join(c["data"] for c in ripub), base[0]["data"]))
        gruppo = [c for c in gruppo if c not in ripub]
    giorni = sorted({c["data"] for c in gruppo})
    if len(giorni) > 1:
        esito.update(stato="ambiguo", candidati=gruppo, prova=gruppo[0]["prova"],
                     motivo="%d documenti %sin %d giorni diversi (%s) senza ESEF: nessuno scelto (vedi candidati)"
                            % (len(gruppo), "italiani " if it else "", len(giorni), ", ".join(giorni)))
        return esito
    scelto = gruppo[0]
    if it and altri and altri[0]["data"] < scelto["data"]:
        note.append("la versione non italiana e' stoccata PRIMA (%s) dell'italiana (%s): vale la piu' antica"
                    % (altri[0]["data"], scelto["data"]))
        scelto = altri[0]
    conferme = [dict(c, nota="ripubblicazione con la relazione di revisione, stoccata il %s" % c["data"] if c in ripub else
                     "stesso giorno" if c["data"] == scelto["data"] else
                     "altra versione/lingua, stoccata il %s" % c["data"])
                for c in sorted(cand, key=chiave_t) if c is not scelto]
    esito.update(stato="ok", scelto=scelto, candidati=[scelto], conferme=conferme, prova=scelto["prova"])
    return esito


# ============================================================
# RIVERIFICA SENZA RETE (INTERFACCIA_UE AGGIUNTA 5, P1 di RV-D4)
# ============================================================
_CAMPI_RIGA = ("data", "ora", "titolo", "url", "protocollo", "categoria", "categorie", "esef", "lingua")


def _chiave_riga(r: Dict[str, Any]) -> Tuple:
    return tuple(tuple(r.get(k) or []) if k == "categorie" else r.get(k) for k in _CAMPI_RIGA)


def _scelta(v: Optional[Dict[str, Any]]) -> Tuple:
    if not v:
        return (None,)
    sc = v.get("scelto") or {}
    return (v.get("stato"), v.get("prova"), sc.get("data"), sc.get("ora"), sc.get("titolo"), sc.get("protocollo"),
            sc.get("url"), tuple(c.get("protocollo") for c in v.get("candidati") or []))


def rileggi_documenti(risposte_salvate: Dict[str, Any], sha256_liste: Dict[str, str], *, id_emarket: int,
                      data_da: str, data_a: str, categorie: Iterable[int] = (CATEGORIA_SEMESTRALE,)) -> Dict[str, Any]:
    """La STESSA lettura di leggi_documenti (stesse pagine, categoria, sonda della pagina piena, giorno di
    confine, filtro_ignorato, troncato) SENZA RETE: lo scaricamento e' sostituito dai corpi salvati. Ritorna
    lo stesso dict di leggi_documenti (senza cache), `url_liste` nell'ordine di lettura. Un URL chiesto dalla
    lettura che non e' fra le risposte salvate, illeggibile o con sha256 diverso da `sha256_liste` (corpo NON
    compresso) = KO 'ricevuta'. PURA rispetto alla rete (richiesta IT2 05/10, AGGIUNTA 5)."""
    salvate = risposte_salvate or {}
    sha = sha256_liste or {}

    def da_ricevuta(url: str):
        rec = salvate.get(url)
        if not isinstance(rec, dict) or rec.get("codifica") != "gzip+base64":
            raise RicevutaIncompleta("risposta salvata assente per %s" % url)
        try:
            corpo = gzip.decompress(base64.b64decode(rec["corpo"]))
        except Exception as e:
            raise RicevutaIncompleta("risposta salvata illeggibile per %s (%s)" % (url, type(e).__name__))
        if hashlib.sha256(corpo).hexdigest() != sha.get(url):
            raise RicevutaIncompleta("sha256 diverso per %s: byte manomessi o ricevuta incoerente" % url)
        return 200, corpo, url

    cats = tuple(categorie or ())
    da, a = _data(data_da), _data(data_a)
    if isinstance(id_emarket, bool) or not isinstance(id_emarket, int) or da is None or a is None or a < da:
        out = _base(id_emarket, data_da, data_a)
        out.update(errore="parametro", motivo="id_emarket/data_da/data_a non validi: %r, %r, %r" % (id_emarket, data_da, data_a))
        return out
    return _leggi(id_emarket, da.isoformat(), a.isoformat(), cats, scarica=da_ricevuta, pausa=0)


_CAMPI_RIGA = ("data", "ora", "titolo", "url", "protocollo", "categoria", "categorie", "esef", "lingua")


def _chiave_riga(r: Dict[str, Any]) -> Tuple:
    return tuple(tuple(r.get(k) or []) if k == "categorie" else r.get(k) for k in _CAMPI_RIGA)


def _scelta(v: Optional[Dict[str, Any]]) -> Tuple:
    if not v:
        return (None,)
    sc = v.get("scelto") or {}
    return (v.get("stato"), v.get("prova"), sc.get("data"), sc.get("ora"), sc.get("titolo"), sc.get("protocollo"),
            sc.get("url"), tuple(c.get("protocollo") for c in v.get("candidati") or []))


def riverifica_documenti(ricevuta: Dict[str, Any], *, tipo: str, periodo_fine: Any,
                         fine_esercizio: Optional[str] = None) -> Tuple[bool, str]:
    """(ok, motivo). `ricevuta` = il ritorno di leggi_documenti (+ `verdetto` = il ritorno di
    candidati_documento, se chi la conserva l'ha usato). Rifa' SENZA RETE la lettura con rileggi_documenti
    (stesse funzioni, corpi salvati, sha ricontrollati), confronta URL letti (stesso ordine: una pagina tolta
    o in piu' = False), stato e righe; poi rifa' candidati_documento e confronta stato/prova/data/ora/titolo/
    protocollo/url/candidati con `verdetto`. Ricevute senza `risposte_salvate` = False col motivo."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un dizionario"
    if not ricevuta.get("risposte_salvate"):
        return False, "ricevuta senza risposte_salvate (vecchia o vuota): non riverificabile senza rete"
    cats = tuple(ricevuta.get("categorie_lette") or ())
    r = rileggi_documenti(ricevuta["risposte_salvate"], ricevuta.get("sha256_liste") or {},
                          id_emarket=ricevuta.get("id_emarket"), data_da=ricevuta.get("data_da"),
                          data_a=ricevuta.get("data_a"), categorie=cats)
    if r["stato"] == "KO":
        return False, "rilettura senza rete KO (%s): %s" % (r["errore"], r["motivo"])
    if list(r["url_liste"]) != list(ricevuta.get("url_liste") or []):
        return False, "le pagine che la lettura chiede (%d) non coincidono con url_liste della ricevuta (%d)" % (
            len(r["url_liste"]), len(ricevuta.get("url_liste") or []))
    if r["stato"] != ricevuta.get("stato"):
        return False, "stato riletto %s diverso da quello della ricevuta %s" % (r["stato"], ricevuta.get("stato"))
    if sorted(map(_chiave_riga, r["righe"]), key=repr) != sorted(map(_chiave_riga, ricevuta.get("righe") or []), key=repr):
        return False, "le righe rilette dai byte (%d) non coincidono con quelle della ricevuta (%d)" % (
            len(r["righe"]), len(ricevuta.get("righe") or []))
    try:
        v = candidati_documento(r["righe"], tipo, periodo_fine, fine_esercizio=fine_esercizio) if r["righe"] else None
    except ParametroDocumenti as e:
        return False, "tipo/periodo non validi per la riverifica (%s)" % type(e).__name__
    if "verdetto" not in ricevuta:
        return True, "byte, pagine e righe riverificati (%d pagine, %d righe); nessun verdetto da confrontare" % (
            len(r["url_liste"]), len(r["righe"]))
    if _scelta(v) != _scelta(ricevuta.get("verdetto")):
        return False, "la scelta rifatta dai byte (%s) e' diversa da quella della ricevuta (%s)" % (
            _scelta(v)[:6], _scelta(ricevuta.get("verdetto"))[:6])
    return True, "riverificato senza rete: %d pagine, %d righe, scelta identica" % (len(r["url_liste"]), len(r["righe"]))


if __name__ == "__main__":  # python -m bellomberg.market_data.emarket_documenti ID TIPO AAAA-MM-GG
    import json
    import sys
    id_em, tipo_, fine_ = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    da_, a_ = finestra_documenti(tipo_, fine_)
    letti = leggi_documenti(id_em, data_da=da_, data_a=a_)
    print(json.dumps({"lettura": {k: v for k, v in letti.items() if k != "righe"},
                      "verdetto": candidati_documento(letti["righe"], tipo_, fine_) if letti["righe"] else None},
                     ensure_ascii=False, indent=2))
