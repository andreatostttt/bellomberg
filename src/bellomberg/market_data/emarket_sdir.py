# -*- coding: utf-8 -*-
"""emarket_sdir.py — INTERNAL DEALING dei titoli italiani da eMarket Storage (SDIR di
Teleborsa), coi PDF letti con pypdf (voce 6, fonti italiane gratuite, 04/10/2026, Opus 5.5).

PERCHE' ESISTE. `agent_tools.tool_get_insider_trades` oggi va su Finnhub e poi SEC (Form 4):
per un `.MI` non c'e' copertura reale. Dopo MAR le comunicazioni di internal dealing passano
dagli SDIR; eMarket Storage le pubblica per emittente (categoria 110) con i PDF diretti.
Borsa Italiana ha l'elenco ma il PDF ricade in `Disallow: *?filename`: il contenuto si legge
SOLO da qui.

MISURE DELLA SONDA (04/10/2026, poche richieste vere):
  - lista `/it/comunicati-finanziari?categoria=110&azienda=<id>` -> 200, Drupal: un
    `<div class="view-content">` con N `<div class="views-row">`; in ogni riga
    `data-protocollo`, `<time ...>dd/mm/yyyy - HH:MM</time>`, link al PDF in
    `/sites/default/files/comunicati/AAAA-MM/*.pdf`, titolo.
  - lista VUOTA (id che non ha comunicazioni): il modulo di ricerca c'e', `view-content` NO.
  - il menu `<select name="azienda">` elenca gli emittenti coperti: un id assente dal menu =
    emittente non su eMarket -> `non_coperto` MISURATO (oltre a quello dichiarato nel negozio).
  - robots.txt: vietati solo /search/, /admin/, /user/, /core/, /profiles/: la paginazione
    (page=) e i PDF sono consentiti.
  - PDF = modello MAR (Reg. 2016/523) bilingue, istruzioni mescolate ai valori: si legge per
    ETICHETTE fisse (`First name:`/`Last name:`, `Role:`, `ISIN:`, `b) Natura dell'operazione`,
    `c) Prezzo/i e Volume/i`, `Volume aggregato:`, `Prezzo:`, `e) Data dell'operazione`).
    Un PDF puo' avere PIU' operazioni (`Operazione - N`): ognuna e' una voce di `operazioni`.

SECONDA FUNZIONE (decisione PM 04/10): `get_data_deposito(ticker, *, tipo, periodo_fine)`, la data
di DEPOSITO su eMarket di una relazione finanziaria (semestrale/annuale/trimestrale): contratto e
misure nel blocco «DATA DI DEPOSITO» in fondo al modulo.

CONTRATTO (per chi cabla i tool in un lotto successivo):
    get_internal_dealing(ticker, *, giorni=180) -> {
        "ticker", "isin", "emarket_id", "giorni",
        "comunicazioni": [{
            "data": "AAAA-MM-GG", "ora": "HH:MM" | None,   # pubblicazione su eMarket
            "protocollo": str | None, "titolo": str, "url_pdf": str,
            "soggetto": str | None, "ruolo": str | None,
            "operazioni": [{"tipo_operazione", "strumento", "isin", "prezzo": float|None,
                            "valuta", "quantita": int|None, "data_operazione": "AAAA-MM-GG"|None,
                            "ora_operazione_utc", "luogo", "righe_prezzo_volume": [[str, str]],
                            "parse_ok": bool, "motivo": str|None, "nota": str|None,
                            "isin_coerente": bool}],
            "parse_ok": bool, "motivo_parse": str | None,
            "errore": "isin_incoerente" | None}],
        "fonte", "url", "stato": "ok"|"vuoto_misurato"|"KO"|"non_coperto"|"STALE",
        "errore", "motivo", "letto_il", "limiti": [str], "pagine_lette", "troncato": bool,
        "pdf_letti", "pdf_non_letti", "pdf_falliti", "parse_falliti", "cache": {...}}
  - parse_ok=False = un'etichetta non trovata o un numero in formato ambiguo: i campi
    relativi sono None, il PDF e' nel link. MAI un valore indovinato.
  - ISIN incrociato col negozio: un'operazione su un altro ISIN (o senza ISIN) ha
    `isin_coerente` False, parse_ok False, prezzo/quantita None. Se NESSUNA operazione e' sul
    titolo del book -> KO 'id_emarket_incoerente' (id eMarket sbagliato nel negozio).
  - prezzo con un solo separatore e 3 cifre dopo («1.234») = ambiguo -> None.
  - prezzo 0 = assegnazione/attribuzione gratuita (piani LTI): `nota` lo dice, NON e' un
    acquisto a mercato.
  - `non_coperto` = l'emittente non e' su eMarket (dichiarato nel negozio con `emarket: null`
    o misurato sul menu): il contenuto va cercato altrove (1Info, sito IR), l'elenco con le
    sole date resta su Borsa Italiana.
"""
import io
import re
import time
from datetime import date, timedelta
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

from bellomberg.market_data import borsa_italiana as _bi

FONTE = "eMarket Storage (SDIR Teleborsa)"
BASE = "https://www.emarketstorage.it"
URL_LISTA = BASE + "/it/comunicati-finanziari?categoria=110&azienda={id}"
TTL_LISTA_S = 6 * 3600
MAX_PAGINE = 5            # pagine della lista lette al massimo per chiamata (troncato dichiarato)
MAX_PDF = 30              # PDF scaricati al massimo per chiamata (gli altri: pdf_non_letti)
PAUSA_S = 1.0             # pausa fra due richieste: uso personale a basso volume
PARSER_VERSIONE = 2       # cambia la chiave di cache dei PDF quando cambia il parser
GIORNI_MAX = 3650


# ============================================================
# LISTA (HTML Drupal)
# ============================================================
_RIGA = re.compile(r'<div class="views-row">(.*?)(?=<div class="views-row">|$)', re.S)
_PDF = re.compile(r'href="(/sites/default/files/comunicati/[^"]+\.pdf)"', re.I)
_DATA_ORA = re.compile(r"<time\b[^>]*>\s*(\d{1,2}/\d{1,2}/\d{4})(?:\s*-\s*(\d{1,2}:\d{2}))?\s*</time>", re.I)


def _testo(h: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", h)).split())


def parse_lista(html: str, id_emarket: int) -> Dict[str, Any]:
    """{"stato": ok|vuoto_misurato|KO|non_coperto, "righe", "pagina_successiva", "errore",
    "motivo", "righe_illeggibili"}. Distinzioni MISURATE:
      view-content con righe leggibili -> ok;
      view-content presente ma 0 righe leggibili -> KO layout_cambiato;
      view-content assente + modulo + id nel menu emittenti -> vuoto_misurato;
      view-content assente + modulo + id NON nel menu -> non_coperto;
      modulo/menu assenti -> KO pagina_diversa."""
    html = html or ""
    ha_contenuto = 'class="view-content"' in html
    ha_modulo = 'name="categoria"' in html
    menu = re.search(r'<select\b[^>]*name="azienda"[^>]*>(.*?)</select>', html, re.S | re.I)
    if not ha_contenuto:
        if not ha_modulo or menu is None:
            return {"stato": "KO", "righe": [], "pagina_successiva": False, "errore": "pagina_diversa",
                    "motivo": "ne' lista ne' modulo di ricerca/menu emittenti: pagina diversa o layout cambiato",
                    "righe_illeggibili": 0}
        if not re.search(r'<option\s+value="%d"' % int(id_emarket), menu.group(1)):
            return {"stato": "non_coperto", "righe": [], "pagina_successiva": False,
                    "errore": "id_non_nel_menu",
                    "motivo": "id eMarket %d assente dal menu emittenti: emittente non su eMarket "
                              "(o id sbagliato nel negozio)" % int(id_emarket), "righe_illeggibili": 0}
        return {"stato": "vuoto_misurato", "righe": [], "pagina_successiva": False, "errore": None,
                "motivo": "lista internal dealing dell'emittente presente e senza comunicazioni",
                "righe_illeggibili": 0}
    contenuto = html[html.index('class="view-content"'):]
    righe: List[Dict[str, Any]] = []
    illeggibili = 0
    blocchi = _RIGA.findall(contenuto)
    for b in blocchi:
        pdf = _PDF.search(b)
        do = _DATA_ORA.search(b)
        d = _bi.data_it(do.group(1)) if do else None
        if not pdf or d is None:
            illeggibili += 1
            continue
        prot = re.search(r'data-protocollo="(\d+)"', b)
        tit = re.search(r'<div class="news-title">(.*?)</div>', b, re.S)
        righe.append({"data": d, "ora": do.group(2), "protocollo": prot.group(1) if prot else None,
                      "titolo": _testo(tit.group(1)) if tit else "", "url_pdf": BASE + pdf.group(1)})
    if not righe:
        return {"stato": "KO", "righe": [], "pagina_successiva": False, "errore": "layout_cambiato",
                "motivo": "lista presente ma %d righe leggibili su %d blocchi (attese: link PDF + data "
                          "dd/mm/yyyy): layout cambiato" % (0, len(blocchi)),
                "righe_illeggibili": illeggibili}
    pager = re.search(r'<nav class="pager".*?</nav>', html, re.S)
    succ = bool(pager and re.search(r'title="Go to next page"|pager__item--next', pager.group(0)))
    return {"stato": "ok", "righe": righe, "pagina_successiva": succ, "errore": None,
            "motivo": ("%d righe illeggibili saltate" % illeggibili) if illeggibili else None,
            "righe_illeggibili": illeggibili}


# ============================================================
# PDF (modello MAR) -> campi
# ============================================================
def _num_prezzo(s: Optional[str]) -> Optional[float]:
    """'8.9957' o '8,9957' o '0' -> float. Forme con due separatori o migliaia -> None."""
    if s is None:
        return None
    s = s.strip()
    if re.fullmatch(r"\d+[.,]\d{3}", s):
        # «1.234» / «12,500»: migliaia o decimali? Il punto come separatore delle migliaia
        # e' stato visto nei volumi veri (sonda R7): stessa regola della quantita' -> None
        # (review RV-C 04/10). Costo dichiarato: un prezzo vero a 3 decimali non si legge.
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    if re.fullmatch(r"\d+,\d+", s):
        return float(s.replace(",", "."))
    return None


def _num_quantita(s: Optional[str]) -> Optional[int]:
    """Solo cifre -> int. '142.774' o '1,5' sono AMBIGUI (migliaia o decimali?) -> None."""
    if s is None:
        return None
    s = s.strip()
    return int(s) if re.fullmatch(r"\d+", s) else None


_SEP_OPERAZIONE = re.compile(r"Operazione\s*-\s*(\d+)\s*\n\s*Transaction\s*-\s*\d+")
_RIGA_PV = re.compile(r"^\s*([\d.,]+)\s*([A-Z]{3})?\s+([\d.,]+)\s*$", re.M)


def _parse_operazione(blocco: str) -> Dict[str, Any]:
    op: Dict[str, Any] = {"tipo_operazione": None, "strumento": None, "isin": None, "prezzo": None,
                          "valuta": None, "quantita": None, "data_operazione": None,
                          "ora_operazione_utc": None, "luogo": None, "righe_prezzo_volume": [],
                          "parse_ok": False, "motivo": None, "nota": None}
    mancano: List[str] = []
    m = re.search(r"ISIN:\s*([A-Z]{2}[A-Z0-9]{9}\d)", blocco)
    op["isin"] = m.group(1) if m else None
    m = re.search(r"emission allowance\.\s*\n(.+?)\nCodice di identificazione dello strumento", blocco, re.S)
    op["strumento"] = " ".join(m.group(1).split()) if m else None
    # tipo: il testo fra l'ULTIMA citazione «596/2014.» della lettera b) e la domanda sulle opzioni
    i_b = blocco.find("b) Natura dell'operazione")
    i_fine = blocco.find("A norma dell'articolo 19, paragrafo 6", i_b if i_b >= 0 else 0)
    if i_b >= 0 and i_fine > i_b:
        parte = blocco[i_b:i_fine]
        k = parte.rfind("596/2014.")
        tipo = " ".join(parte[k + len("596/2014."):].split()) if k >= 0 else ""
        op["tipo_operazione"] = tipo or None
    if not op["tipo_operazione"]:
        mancano.append("tipo operazione (b)")
    # c) righe prezzo/volume: fra l'intestazione delle colonne e l'istruzione «Se piu'...»
    i_c = blocco.find("c) Prezzo/i e Volume/i")
    righe: List[Tuple[str, Optional[str], str]] = []
    if i_c >= 0:
        i_col = blocco.find("Volume(s)", i_c)
        i_se = blocco.find("\nSe pi", i_col if i_col >= 0 else i_c)
        if i_col >= 0 and i_se > i_col:
            righe = _RIGA_PV.findall(blocco[i_col + len("Volume(s)"):i_se])
    op["righe_prezzo_volume"] = [[p, v] for p, _val, v in righe]
    # d) aggregati
    m_vol = re.search(r"Volume aggregato:\s*([\d.,]+)", blocco)
    m_prz = re.search(r"\nPrezzo:\s*([\d.,]+)\s*([A-Z]{3})?", blocco)
    problemi: List[str] = []
    if m_vol and m_prz:
        op["quantita"] = _num_quantita(m_vol.group(1))
        op["prezzo"] = _num_prezzo(m_prz.group(1))
        op["valuta"] = m_prz.group(2) or (righe[0][1] if righe else None)
        if op["quantita"] is None:
            problemi.append("volume aggregato %r in formato ambiguo" % m_vol.group(1))
        if op["prezzo"] is None:
            problemi.append("prezzo %r in formato non riconosciuto" % m_prz.group(1))
        vols = [_num_quantita(v) for _p, _val, v in righe]
        if op["quantita"] is not None and righe and None not in vols and sum(vols) != op["quantita"]:
            problemi.append("somma delle righe c) (%d) diversa dal volume aggregato (%d)" % (sum(vols), op["quantita"]))
            op["quantita"] = op["prezzo"] = None
    elif len(righe) == 1:
        p, val, v = righe[0]
        op["prezzo"], op["quantita"], op["valuta"] = _num_prezzo(p), _num_quantita(v), val
        if op["prezzo"] is None or op["quantita"] is None:
            problemi.append("riga prezzo/volume %r %r in formato ambiguo" % (p, v))
    else:
        mancano.append("prezzo e volume (d: Volume aggregato/Prezzo; c: %d righe)" % len(righe))
    i_e = blocco.find("e) Data dell'operazione")
    m = re.search(r"(\d{4}-\d{2}-\d{2})(?:\s*-\s*(\d{2}:\d{2}:\d{2}))?", blocco[i_e:]) if i_e >= 0 else None
    if m:
        try:
            op["data_operazione"] = date.fromisoformat(m.group(1)).isoformat()
            op["ora_operazione_utc"] = m.group(2)
        except ValueError:
            problemi.append("data operazione %r non valida" % m.group(1))
    else:
        mancano.append("data operazione (e)")
    i_f = blocco.find("f) Luogo dell'operazione")
    if i_f >= 0:
        k = blocco.rfind("600/2014", i_f)
        coda = blocco[k + len("600/2014"):].split("Fine Comunicato")[0] if k >= 0 else ""
        luogo = " ".join(coda.split()).lstrip(") ").strip()
        op["luogo"] = luogo or None
    if mancano:
        problemi.insert(0, "etichette non trovate: " + ", ".join(mancano))
    op["parse_ok"] = not problemi
    op["motivo"] = "; ".join(problemi) or None
    if op["prezzo"] == 0:
        op["nota"] = "prezzo 0: assegnazione/attribuzione gratuita (es. piano di incentivi), NON un acquisto a mercato"
    return op


def parse_testo_internal_dealing(testo: str) -> Dict[str, Any]:
    """Testo estratto dal PDF -> {"soggetto", "ruolo", "operazioni", "parse_ok", "motivo_parse"}."""
    testo = (testo or "").replace("\r\n", "\n").replace("\r", "\n")
    out: Dict[str, Any] = {"soggetto": None, "ruolo": None, "operazioni": [], "parse_ok": False,
                           "motivo_parse": None}
    problemi: List[str] = []
    i1 = testo.find("1 Dati relativi alla persona")
    i2 = testo.find("2 Motivo della notifica")
    if i1 >= 0 and i2 > i1:
        m = re.search(r"First name:\s*(.*?)\s*Cognome:\s*Last name:\s*([^\n]+)", testo[i1:i2], re.S)
        if m and m.group(1).strip() and m.group(2).strip():
            out["soggetto"] = " ".join((m.group(1) + " " + m.group(2)).split())
    if out["soggetto"] is None:
        problemi.append("soggetto (sezione 1: First name/Last name; persone giuridiche non lette)")
    m = re.search(r"Ruolo:\s*\n\s*Role:\s*\n([^\n]+)", testo)
    out["ruolo"] = m.group(1).strip() if m and m.group(1).strip() else None
    if out["ruolo"] is None:
        problemi.append("ruolo (sezione 2: Ruolo/Role)")
    i4 = testo.find("4 Dati relativi all'operazione")
    sez4 = testo[i4:] if i4 >= 0 else ""
    if not sez4:
        problemi.append("sezione 4 (operazioni) non trovata")
    else:
        tagli = [m.start() for m in _SEP_OPERAZIONE.finditer(sez4)]
        blocchi = [sez4[a:b] for a, b in zip(tagli, tagli[1:] + [len(sez4)])] if tagli else [sez4]
        out["operazioni"] = [_parse_operazione(b) for b in blocchi]
        cattive = [str(i + 1) for i, o in enumerate(out["operazioni"]) if not o["parse_ok"]]
        if cattive:
            problemi.append("operazioni non lette per intero: %s" % ", ".join(cattive))
    out["parse_ok"] = not problemi
    out["motivo_parse"] = "; ".join(problemi) or None
    return out


def parse_pdf_internal_dealing(dati: bytes) -> Dict[str, Any]:
    """PDF -> come parse_testo_internal_dealing; PDF illeggibile = parse_ok False dichiarato."""
    try:
        from pypdf import PdfReader
        lettore = PdfReader(io.BytesIO(dati))
        testo = "\n".join((p.extract_text() or "") for p in lettore.pages)
    except Exception as e:
        return {"soggetto": None, "ruolo": None, "operazioni": [], "parse_ok": False,
                "motivo_parse": "PDF illeggibile: %s" % type(e).__name__}
    if not testo.strip():
        return {"soggetto": None, "ruolo": None, "operazioni": [], "parse_ok": False,
                "motivo_parse": "PDF senza testo estraibile (scansione?)"}
    return parse_testo_internal_dealing(testo)


# ============================================================
# LA FUNZIONE PUBBLICA
# ============================================================
def _base(ticker: str, giorni: Any) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper(), "isin": None, "emarket_id": None,
            "giorni": giorni, "comunicazioni": [], "fonte": FONTE, "url": None, "stato": "KO",
            "errore": None, "motivo": None, "letto_il": None,
            "limiti": ["persone giuridiche: soggetto non letto (parse_ok False)",
                       "piu' righe prezzo/volume senza aggregati: prezzo/quantita None"],
            "pagine_lette": 0, "troncato": False, "pdf_letti": 0, "pdf_non_letti": 0,
            "pdf_falliti": 0, "parse_falliti": 0}


def incrocia_isin(com: Dict[str, Any], isin_libro: str) -> Dict[str, Any]:
    """Review RV-C 04/10: l'ISIN letto nel PDF si CONFRONTA con quello del negozio. Un'operazione
    su un altro strumento (bond, derivato) o di un altro emittente (id eMarket sbagliato) uscirebbe
    firmata col ticker del book: parse_ok False, prezzo e quantita' None, motivo scritto. Lo
    stesso se l'ISIN nel PDF manca: il titolo non e' verificabile."""
    problemi = []
    for o in com.get("operazioni") or []:
        if o.get("isin") == isin_libro:
            o["isin_coerente"] = True
            continue
        o["isin_coerente"] = False
        msg = ("ISIN del PDF %s diverso dal titolo del book %s" % (o.get("isin"), isin_libro)
               if o.get("isin") else "ISIN assente nel PDF: titolo non verificabile (atteso %s)" % isin_libro)
        o["motivo"] = "; ".join(x for x in (o.get("motivo"), msg) if x)
        o["parse_ok"] = False
        o["prezzo"] = o["quantita"] = None
        problemi.append(msg)
    com["errore"] = None
    if problemi:
        com["errore"] = "isin_incoerente"   # marca sulla comunicazione (richiesta main 04/10)
        com["parse_ok"] = False
        com["motivo_parse"] = "; ".join(x for x in (com.get("motivo_parse"), problemi[0]) if x)
    return com


def _comunicazione(riga: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """(comunicazione, scaricato_ok). Il parse riuscito di un PDF si tiene in cache per sempre
    (il PDF di un protocollo non cambia); quello fallito no (si ritenta)."""
    chiave = "emarket_pdf_v%d_%s" % (PARSER_VERSIONE, riga.get("protocollo") or
                                      re.sub(r"\W", "_", riga["url_pdf"][-60:]))
    c = _bi.cache_leggi(chiave)
    if c is not None:
        return dict(riga, **c["risultato"]), True
    try:
        http, corpo, _ = _bi._scarica(riga["url_pdf"])
    except Exception as e:
        return dict(riga, soggetto=None, ruolo=None, operazioni=[], parse_ok=False,
                    motivo_parse="PDF non scaricato: %s" % type(e).__name__), False
    if http != 200:
        return dict(riga, soggetto=None, ruolo=None, operazioni=[], parse_ok=False,
                    motivo_parse="PDF non scaricato: HTTP %s" % http), False
    p = parse_pdf_internal_dealing(corpo)
    if p["parse_ok"]:
        _bi.cache_scrivi(chiave, p)
    return dict(riga, **p), True


def _leggi(ticker: str, isin: str, id_em: int, giorni: int) -> Dict[str, Any]:
    out = _base(ticker, giorni)
    out.update(isin=isin, emarket_id=id_em, url=URL_LISTA.format(id=id_em))
    soglia = (_bi.oggi_roma() - timedelta(days=giorni)).isoformat()
    righe: List[Dict[str, Any]] = []
    piu_vecchia_fuori: Optional[str] = None
    pagina = 0
    while True:
        url = out["url"] + ("&page=%d" % pagina if pagina else "")
        try:
            http, corpo, _ = _bi._scarica(url)
        except _bi.URLVietato as e:  # testo controllato: il messaggio puo' contenere l'URL (regola 16)
            out.update(errore="url_vietato", motivo="pagina %d: URL rifiutato prima della rete (%s: robots.txt o "
                                                    "host non previsto)" % (pagina + 1, type(e).__name__))
            return out
        except Exception as e:
            out.update(errore="rete", motivo="richiesta fallita (pagina %d): %s" % (pagina + 1, type(e).__name__))
            return out
        if http != 200:
            out.update(errore="http", motivo="HTTP %s da eMarket (pagina %d)" % (http, pagina + 1))
            return out
        p = parse_lista(corpo.decode("utf-8", errors="replace"), id_em)
        out["pagine_lette"] = pagina + 1
        if p["stato"] != "ok":
            if pagina == 0:
                out.update(stato=p["stato"], errore=p["errore"], motivo=p["motivo"])
                if p["stato"] in ("vuoto_misurato", "non_coperto"):
                    out["letto_il"] = _bi.adesso_utc().isoformat(timespec="seconds")
                return out
            out.update(errore=p["errore"], motivo="pagina %d: %s" % (pagina + 1, p["motivo"]))
            return out  # KO a meta' paginazione: mezza lista servita come intera sarebbe un ripiego muto
        dentro = [r for r in p["righe"] if r["data"] >= soglia]
        fuori = [r for r in p["righe"] if r["data"] < soglia]
        righe.extend(dentro)
        if fuori:
            piu_vecchia_fuori = max(r["data"] for r in fuori)
            break
        if not p["pagina_successiva"]:
            break
        if pagina + 1 >= MAX_PAGINE:
            out["troncato"] = True
            out["limiti"].append("lette %d pagine della lista: le comunicazioni piu' vecchie "
                                 "della finestra non sono state lette" % MAX_PAGINE)
            break
        pagina += 1
        time.sleep(PAUSA_S)
    out["letto_il"] = _bi.adesso_utc().isoformat(timespec="seconds")
    if not righe:
        out.update(stato="vuoto_misurato",
                   motivo="nessuna comunicazione negli ultimi %d giorni (dal %s); la piu' recente "
                          "fuori finestra: %s" % (giorni, soglia, piu_vecchia_fuori))
        return out
    comunicazioni = []
    for i, r in enumerate(righe):
        if i >= MAX_PDF:
            comunicazioni.append(dict(r, soggetto=None, ruolo=None, operazioni=[], parse_ok=False,
                                      motivo_parse="PDF non letto: oltre il tetto di %d PDF per chiamata" % MAX_PDF))
            continue
        if i:
            time.sleep(PAUSA_S)
        com, scaricato = _comunicazione(r)
        comunicazioni.append(incrocia_isin(com, isin))
        if scaricato:
            out["pdf_letti"] += 1
        else:
            out["pdf_falliti"] += 1
    out["pdf_non_letti"] = max(0, len(righe) - MAX_PDF)
    if out["pdf_non_letti"]:
        out["troncato"] = True
        out["limiti"].append("%d PDF non letti (tetto %d per chiamata): parse_ok False, link nel campo url_pdf"
                             % (out["pdf_non_letti"], MAX_PDF))
    isin_letti = sorted({o["isin"] for c in comunicazioni for o in c.get("operazioni") or [] if o.get("isin")})
    if isin_letti and isin not in isin_letti:
        # nessuna operazione sul titolo del book: l'id eMarket nel negozio porta a un altro
        # emittente (o tutte le operazioni sono su altri strumenti). Non si serve nulla.
        out.update(stato="KO", errore="id_emarket_incoerente", comunicazioni=[],
                   motivo="nessuna operazione sull'ISIN del book %s: nei PDF dell'id eMarket %d ci sono "
                          "%s. Controlla l'id eMarket nel negozio" % (isin, id_em, ", ".join(isin_letti)))
        return out
    out["parse_falliti"] = sum(1 for c in comunicazioni if not c["parse_ok"])
    out.update(stato="ok", comunicazioni=comunicazioni,
               motivo=("%d comunicazioni su %d non lette per intero (vedi motivo_parse)"
                       % (out["parse_falliti"], len(comunicazioni))) if out["parse_falliti"] else None)
    return out


def _errore_senza_emarket(voce: Dict[str, Any]) -> str:
    return "dichiarato_non_su_emarket" if voce.get("negozio") == "confermato" else "emarket_non_risolto"


def _motivo_senza_emarket(voce: Dict[str, Any]) -> str:
    """Il negozio CONFERMATO con emarket null e' una dichiarazione del PM; quello AUTOMATICO
    vuol dire che risolvi_isin non ha trovato un id univoco nel menu: sono due frasi diverse."""
    if voce.get("negozio") == "confermato":
        return "emittente dichiarato NON su eMarket SDIR nel negozio (emarket: null)"
    return ("id eMarket non risolto dal negozio automatico (%s)"
            % (voce.get("emarket_motivo") or "motivo non scritto"))


def get_internal_dealing(ticker: str, *, giorni: int = 180) -> Dict[str, Any]:
    """Internal dealing di un titolo italiano negli ultimi `giorni` (vedi CONTRATTO)."""
    if isinstance(giorni, bool) or not isinstance(giorni, int) or not 1 <= giorni <= GIORNI_MAX:
        out = _base(ticker, giorni)
        out.update(errore="parametro", motivo="giorni dev'essere un intero fra 1 e %d, non %r" % (GIORNI_MAX, giorni))
        out["cache"] = {"stato": "nessuna", "eta_s": None}
        return out
    voce, err, mot = _bi.voce_ticker_o_auto(ticker)
    if voce is None:
        out = _base(ticker, giorni)
        out.update(errore=err, motivo=mot)
        out["cache"] = {"stato": "nessuna", "eta_s": None}
        return out
    if voce["emarket"] is None:
        out = _base(ticker, giorni)
        out.update(isin=voce["isin"], stato="non_coperto", errore=_errore_senza_emarket(voce),
                   motivo=_motivo_senza_emarket(voce) + ": il "
                          "contenuto dell'internal dealing va cercato altrove (1Info, sito IR); "
                          "l'elenco con le sole date e' su Borsa Italiana", voce_da=voce["negozio"])
        out["cache"] = {"stato": "nessuna", "eta_s": None}
        return out
    # la lista si tiene in cache solo se ogni PDF tentato e' stato scaricato: un PDF caduto per
    # rete va ritentato alla prossima chiamata, non congelato per il TTL (review RV-C 04/10)
    voce_da = voce["negozio"]
    out = _bi.con_cache("emarket_lista_v%d_%d_%d_%s" % (PARSER_VERSIONE, voce["emarket"], giorni, voce["isin"]),
                        TTL_LISTA_S, lambda: _leggi(ticker, voce["isin"], voce["emarket"], giorni),
                        salva=lambda r: not r.get("pdf_falliti"))
    out["ticker"] = (ticker or "").strip().upper()
    out["voce_da"] = voce_da   # 'confermato' | 'automatico'
    return out


# ============================================================
# DATA DI DEPOSITO DELLE RELAZIONI FINANZIARIE (decisione PM 04/10/2026)
# ============================================================
# MISURE (04/10/2026, un emittente del FTSE MIB, 8 richieste): la messa a disposizione della
# relazione e' un COMUNICATO SDIR proprio, distinto dal comunicato dei risultati, in coppia
# IT/EN a pochi minuti di distanza:
#   semestrale  -> categoria 101 (1.2 art. 5 dir. 2004/109): «pubblicata la Relazione
#                  finanziaria semestrale al 30 giugno 2026» (il comunicato dei risultati del
#                  semestre sta nella stessa categoria, una settimana prima, con altro titolo);
#   annuale     -> categoria 150 (REGEM, art. 65-bis Reg. Emittenti): «pubblicata la relazione
#                  finanziaria annuale per l'esercizio 2025» (la 100 aveva solo i risultati, la
#                  111 MANRSS era vuota);
#   trimestrale -> categoria 150: «pubblicato il Resoconto intermedio di gestione al 31 marzo 2026».
# SECONDO EMITTENTE (misura 04/10 sera, su richiesta di main: get_data_deposito dava non_trovato):
# il comunicato di deposito ha un titolo GENERICO senza nome ne' periodo — «Prysmian S.p.A.:
# deposito documenti» / «documents filing» (cat. 101, 31/07/2026), il giorno dopo i «Risultati al
# 30 giugno 2026». Nome e periodo stanno nel TESTO del PDF: «e' stata messa a disposizione del
# pubblico la Relazione Finanziaria Semestrale al 30 giugno 2026». Quindi la regola ha un secondo
# stadio: se nessun TITOLO basta, si leggono i PDF dei comunicati dal titolo generico (i piu'
# vicini alla fine del periodo, al massimo MAX_PDF_DEPOSITO) e il nome + periodo si cercano nel
# testo. La prova resta la stessa (nome del documento + periodo), cambia solo dove la si legge.
# La categoria la sceglie l'EMITTENTE: si cercano piu' categorie per tipo (quelle misurate +
# quelle plausibili) e `non_trovato` vale «non trovato nelle categorie cercate», non «non depositato».
# REGOLA ALLARGATA (handoff-3 05/10, IT2b, misure M1a/M1b su FTSE MIB + Mid Cap):
#  - cosa si cerca: la data di DEPOSITO / messa a disposizione del documento. Il comunicato dei
#    RISULTATI e l'APPROVAZIONE (CdA o assemblea) non lo sono: vanno in `scartati` col motivo.
#    Eccezione dichiarata: le informazioni finanziarie periodiche aggiuntive (trimestrale), che si
#    diffondono col comunicato che le approva (natura 'informazioni_nel_comunicato');
#  - natura del candidato: messa_a_disposizione (verbo del deposito) > documento (solo nome +
#    periodo) > avviso_stampa (avviso sui quotidiani: conta solo se non c'e' altro, e lo si dice);
#  - piu' candidati: l'italiano comanda (la gemella EN, anche «(Versione Inglese)» col titolo
#    italiano, e' conferma); stesso GIORNO = data univoca (primo per ora, anche con la rettifica
#    «annulla e sostituisce»); giorni diversi = ambiguo con la lista, mai il primo a caso;
#  - stadio 2 (titoli generici, nome solo nel PDF): niente generici di ALTRI documenti (statuto,
#    verbale, parti correlate...), italiani prima degli inglesi, finestra FINESTRA_PDF_GIORNI,
#    tetto MAX_PDF_DEPOSITO, nome citato come punto all'ordine del giorno escluso; tagli in `limiti`;
#  - non_trovato = non trovato nelle categorie cercate (il motivo dice categorie, pagine e
#    comunicati letti); id nel menu ma liste vuote ovunque = non_coperto 'id_senza_comunicati'.
URL_CATEGORIA = BASE + "/it/comunicati-finanziari?categoria={cat}&azienda={id}"
CATEGORIE_DEPOSITO = {"semestrale": (101, 150, 109, 100), "annuale": (150, 100, 109), "trimestrale": (150, 109, 101)}
# trimestrale + 101 (sonda S2: depositi trimestrali in 101; serve anche alla diagnosi id_senza_comunicati)
# semestrale + 109/100 (misura M1 05/10 sulle pagine salvate di 59 emittenti FTSE MIB/Mid Cap: 4
# emittenti depositano la semestrale, o il suo avviso, in 109 «3.1 altre informazioni» o in 100)
MAX_PAGINE_DEPOSITO = 4      # per categoria; ci si ferma prima appena le righe sono anteriori al periodo
MAX_PDF_DEPOSITO = 4         # PDF dei comunicati dal titolo generico letti al massimo (dichiarato)
FINESTRA_PDF_GIORNI = 200    # stadio 2: si leggono solo i PDF generici entro N giorni dalla fine del periodo
#                              (i termini di legge sono 3-4 mesi; quelli oltre: contati e dichiarati)
MAX_SCARTATI = 15            # quasi-candidati riportati col motivo (il totale sta nel motivo)
# COSTO di una chiamata (dichiarato in `richieste`): liste = categorie x pagine (al massimo
# len(categorie) x MAX_PAGINE_DEPOSITO), PDF = al massimo MAX_PDF_DEPOSITO, solo allo stadio 2.
TTL_DEPOSITO_S = 6 * 3600
FUSO = "Europe/Rome"         # data/ora di eMarket (stoccaggio del documento o diffusione del comunicato): ora di Roma

# Nomi «intermedi» (interim financial report, relazione finanziaria intermedia): IAS 34 li usa
# sia per la semestrale sia per i trimestri. Li decide il MESE della data che segue entro 45
# caratteri senza cifre (misura M1 05/10: «... financial statements For the three-month period
# ended March 31»), con un LOOKAHEAD (il testo catturato resta il solo nome: accordo con D4 05/10):
# giugno -> semestrale; marzo/settembre -> trimestrale; altri mesi -> nessun tipo.
_SEGUE_GIUGNO = r"(?=\D{0,45}(?:30\s+giugno|30\s+june|june\s+30|30\s*[./-]\s*0?6\s*[./-]|30\s+0?6\s+\d{4}))"
_SEGUE_MAR_SET = (r"(?=\D{0,45}(?:31\s+marzo|30\s+settembre|31\s+march|march\s+31|30\s+september|september\s+30|"
                  r"31\s*[./-]\s*0?3\s*[./-]|30\s*[./-]\s*0?9\s*[./-]))")
_INTERIM_EN = (r"interim\s+(?:financial\s+|management\s+)?report|(?:condensed\s+)?(?:consolidated\s+)?interim\s+"
               r"(?:condensed\s+)?(?:consolidated\s+)?financial\s+statements?")
_INTERIM_IT = r"relazione\s+(?:finanziaria\s+)?(?:consolidata\s+)?intermedia"
_DOC = {
    "semestrale": (r"relazione\s+(?:finanziaria\s+)?(?:consolidata\s+)?semestrale|relazione\s+semestrale\s+consolidata|"
                   r"bilancio\s+(?:consolidato\s+)?semestrale|"
                   r"relazione\s+finanziaria(?=\s+(?:consolidata\s+)?al\s+30\s+giugno)|"
                   r"(?:%s)%s" % (_INTERIM_IT, _SEGUE_GIUGNO),
                   r"half[\s-]*year(?:ly)?\s+(?:financial\s+)?report|semi[\s-]*annual\s+(?:financial\s+)?report|"
                   r"first[\s-]+half(?:[\s-]+year)?\s+(?:consolidated\s+)?financial\s+report|"
                   r"half[\s-]*year(?:ly)?\s+(?:condensed\s+)?(?:consolidated\s+)?financial\s+statements|"
                   r"(?:%s)%s" % (_INTERIM_EN, _SEGUE_GIUGNO)),
    "annuale": (r"relazione\s+finanziaria\s+annuale|progetto\s+di\s+bilancio|relazione\s+annuale\s+integrata|"
                r"relazion[ei]\s+e\s+bilanci[o]|resocont[oi]\s+(?:annual[ei]|dell['’]\s*esercizio)|bilancio\s+annuale|"
                r"relazione\s+finanziaria(?=\s+\d{4}\b)|bilancio(?=\s+(?:chiuso\s+)?al\s+31[./\s-]+(?:12|dicembre))|"
                r"bozza\s+(?:di\s+)?bilancio|"
                r"bilancio\s+(?:consolidato|d['’]\s*esercizio|di\s+esercizio|separato|integrato)"
                r"(?!\s+(?:consolidato\s+)?(?:semestrale|intermedio|abbreviato|trimestrale))",
                r"annual\s+financial\s+report|(?:integrated\s+)?annual\s+(?:integrated\s+)?report(?:\s+and\s+accounts)?|"
                r"integrated\s+(?:financial\s+)?report|(?<=\d\d\d\d\s)financial\s+report|"
                r"(?<!condensed\s)(?<!interim\s)(?<!year\s)financial\s+statements(?=\s+(?:as\s+)?(?:at|of)\s+"
                r"(?:31\s+december|december\s+31|31[./-]12))|"
                r"draft\s+(?:separate\s+|consolidated\s+)?financial\s+statements|"
                r"(?<!condensed\s)(?<!interim\s)(?<!year\s)(?<!yearly\s)(?:consolidated|separate)\s+financial\s+statements"),
    "trimestrale": (r"resoconto\s+intermedio(?:\s+di\s+gestione)?|resoconto\s+trimestrale|"
                    r"informazioni\s+(?:finanziarie\s+)?periodiche\s+aggiuntive|informativa\s+(?:finanziaria\s+)?periodica\s+aggiuntiva|"
                    r"relazione\s+(?:finanziaria\s+)?(?:consolidata\s+)?trimestrale|\btrimestrale\b|"
                    r"relazione\s+(?:finanziaria\s+)?(?:del\s+)?(?:I|III|primo|terzo)\s+trimestre|"
                    r"relazione\s+intermedi[oa]\s+di\s+gestione" + _SEGUE_MAR_SET + r"|"
                    r"(?:%s|informazioni\s+finanziarie\s+periodiche(?:\s+consolidate)?(?:\s+non\s+revisionate)?)%s"
                    % (_INTERIM_IT, _SEGUE_MAR_SET),
                    r"interim\s+management\s+statement|additional\s+(?:quarterly\s+|periodic\s+)+financial\s+(?:information|report)|"
                    r"quarterly\s+(?:financial\s+)?(?:report|statement)|"
                    # casi veri MF 05/10: «Interim statement as at 31 March 2026» (legato al mese come gli altri
                    # «interim»), «First Quarter 2026 report», «Q3 2025 report» (il trimestre e' nel nome)
                    r"interim\s+statement" + _SEGUE_MAR_SET + r"|(?:first|third)[\s-]+quarter\s+(?:of\s+)?\d{4}\s+report|"
                    r"\b(?:Q1|Q3|1Q|3Q)\s*\d{4}\s+report|"
                    r"(?:%s)%s" % (_INTERIM_EN, _SEGUE_MAR_SET)),
}
# trimestrale: le INFORMAZIONI FINANZIARIE PERIODICHE AGGIUNTIVE (art. 82-ter Reg. Emittenti, dal
# 2016 il resoconto intermedio non e' piu' obbligatorio) si diffondono COL comunicato che le
# approva: per questi nomi l'approvazione E' la messa a disposizione (dichiarato in `limiti`).
_IFPA = re.compile(r"informazioni\s+(?:finanziarie\s+)?periodiche(?:\s+aggiuntive|\s+consolidate|\s+non|\s+al)|informativa\s+(?:finanziaria\s+)?"
                   r"periodica\s+aggiuntiva|additional\s+(?:quarterly\s+|periodic\s+)+financial\s+(?:information|report)",
                   re.I)
# titoli GENERICI della messa a disposizione (senza nome del documento): candidati del secondo
# stadio, da confermare SOLO col testo del PDF
# Nome PUBBLICO dello stesso oggetto (richiesta D4 05/10: trade_idea_sources lo importa per
# riconoscere il tipo dalla copertina). Chiavi fisse: 'semestrale', 'annuale', 'trimestrale';
# valori (regex IT, regex EN) in testo, da compilare con re.I. Non rinominare senza avvisare D4.
DOCUMENTI_DEPOSITO = _DOC

_GENERICO = re.compile(r"deposito\s+(?:di\s+|dei\s+|della\s+)?document|documents?\s+filing|"
                       r"filing\s+of\s+(?:the\s+)?(?:documents?|documentation)|"
                       r"messa\s+a\s+disposizione\s+(?:del\s+pubblico\s+)?(?:del(?:la)?\s+|dei\s+|di\s+)?document|"
                       r"documentazione\s+(?:messa\s+a\s+disposizione|a\s+disposizione|depositata)|"
                       r"pub+licazione\s+(?:della\s+|di\s+|dei\s+)?document|pub+lication\s+of\s+(?:the\s+)?document|(?:avviso|comunicazione)\s+di\s+(?:avvenuto\s+)?deposito|"
                       r"adempimenti\s+informativi|deposit\s+of\s+(?:the\s+)?documents?|notice\s+of\s+filing|"
                       r"^\W*(?:\w+\W+){0,4}?avviso\s*/\s*notice\W*$|filing\s+and\s+storage|"
                       r"availability\s+of\s+(?:the\s+)?(?:documents?|documentation)|"
                       r"(?:documentation|documents)\s+(?:made\s+available|available|filed)",
                       re.I)
# titoli generici che dicono di quale ALTRO documento si tratta (misura M1 05/10: statuto, verbale,
# parti correlate, deleghe, aumento di capitale...): non si leggono, sprecherebbero il tetto dei PDF
_GENERICO_ALTRO = re.compile(r"statut|bylaws|by-laws|verbal|minutes|parti\s+correlate|related[\s-]+part|"
                             r"sollecitazione|deleghe|proxy|aumento\s+di\s+capitale|capital\s+increase|prospett|"
                             r"prospectus|documento\s+informativo|documenti\s+informativi|information\s+document|"
                             r"offert|tender\s+offer|azioni\s+proprie|buy[\s-]*back|remunerazion|remuneration", re.I)
# un titolo generico con parole italiane (anche bilingue «deposito documentazione / filing notice»)
_GENERICO_IT = re.compile(r"deposit[oa]|document[ia]|pub+licazion|avviso|messa\s+a\s+disposizione|adempimenti|"
                          r"disponibilit", re.I)
# nel testo di un PDF di deposito: il nome preceduto da queste parole e' un punto all'ordine del
# giorno o un'approvazione, non il documento messo a disposizione (misura offline sui PDF di M1a)
_RIFERIMENTO_OdG = re.compile(r"approvazion|approvare|approvat|relativ|approval|approve|relating|related\s+to|punto|"
                              r"item|agenda|ordine\s+del\s+giorno|verbal|minutes|esame|esaminat", re.I)
# il PROGETTO di bilancio nominato senza verbo di deposito e' l'approvazione del CdA, non il deposito
_PROGETTO = re.compile(r"progetto\s+di\s+bilancio|bozza\s+(?:di\s+)?bilancio|draft\s+(?:separate\s+|consolidated\s+|statutory\s+)?"
                       r"(?:and\s+(?:the\s+)?consolidated\s+)?financial\s+statements", re.I)
_VERSIONE_EN = re.compile(r"versione\s+inglese|english\s+version|\(eng(?:lish)?\)", re.I)
# REGOLA (main 05/10 dopo la review RV-IT2, che ha trovato date ANTICIPATE: risultati senza la parola
# «risultati», convocazioni, annunci al futuro): un comunicato e' il DEPOSITO solo se ha un verbo POSITIVO
# della messa a disposizione al presente o al passato. Nome + periodo senza quel verbo NON bastano.
_VERBO_FORTE = re.compile(r"depositat[aoie]|\bdeposit[oi]\b|\bdeposited\b|\bdeposit\s+of\b|"
                          r"mess[aoie]\s+a\s+disposizione|(?:\b[eè]['’]?|\bsono|\bresta|\brestano|\brimane|\brimangono)\s+"
                          r"(?:stat[aoie]\s+)?a\s+disposizione|a\s+disposizione\s+del\s+pubblico|"
                          r"pub+licat[aoie]|pub+licazion[ei]|\bpublished\b|\bpub+lication\b|\bfiled\b|\bfiles\b|"
                          r"\bfiling\b|made\s+available|stoccaggio|\bstorage\b", re.I)
# «disponibile/available» da solo e' debole: non basta accanto a un'approvazione/esame/convocazione
# («il CdA approva...; disponibile la presentazione»)
_VERBO_DEBOLE = re.compile(r"disponibil|\bavailab(?:le|ility)\b", re.I)
# il deposito annunciato al FUTURO non e' il deposito («sara' messa a disposizione il 5 agosto»)
_FUTURO = re.compile(r"(?:\bsar(?:[aà]|a['’])|\bsaranno\b|\bverr(?:[aà]|a['’])|\bverranno\b|\bwill\s+be\b|\bto\s+be\b|"
                     r"\bshall\s+be\b)\s+(?:\w+\s+){0,2}?(?:mess[aoie]\b|pub+licat|depositat|res[aoie]\b|made\b|published|"
                     r"filed|available|disponibil|a\s+disposizione)", re.I)
# relazione della societa' di revisione / del collegio sindacale SUL documento: non e' il documento
_REVISIONE_SU = re.compile(r"(?:revisione|revisori|auditors?['’]?(?:\s+report)?|collegio\s+sindacale)\W+(?:\w+\W+){0,3}?"
                           r"(?:sul|sulla|sui|sulle|on)\b", re.I)
_CALENDARIO = re.compile(r"calendari|\bcalendar\b", re.I)
# AVVISO SUI QUOTIDIANI della messa a disposizione: esce lo stesso giorno o DOPO il deposito.
# Vale come candidato solo se non c'e' altro (natura 'avviso_stampa', dichiarato in `limiti`).
_STAMPA = re.compile(r"avviso\s+stampa|quotidian|il\s+sole|sole\s*24\s*ore|repubblica|milano\s+finanza|corriere|italia\s*oggi|"
                     r"il\s+giornale|la\s+stampa|newspaper|avviso\s+al\s+pubblico|estratto", re.I)
# comunicato STAMPA / dei RISULTATI che nomina il documento senza dirne il deposito
# («Comunicato stampa Relazione finanziaria semestrale al ...»): non e' il deposito
_COMUNICATO_RISULTATI = re.compile(r"comunicato\s+stampa|press\s+release|(?<![a-z])(?:cs|pr)(?![a-z])|risultat|\bresults?\b|ricavi|"
                                   r"revenues?|ebitda|\butile\b|profit|dividend|guidance|fatturato|\bsales\b", re.I)
# REGOLA (PM 04/10, chiarita 05/10): la data cercata e' quella del DEPOSITO / messa a disposizione
# del documento, NON quella del comunicato dei risultati o dell'approvazione del CdA. Un titolo
# col nome + periodo e un verbo di APPROVAZIONE senza verbo di deposito («il CdA approva la
# relazione semestrale al 30 giugno») e' l'approvazione: va fra gli `scartati` col motivo e fa
# partire lo stadio 2 (il deposito puo' avere un titolo generico). Eccezione dichiarata: le
# informazioni finanziarie periodiche aggiuntive della trimestrale (vedi _IFPA).
_APPROVAZIONE = re.compile(r"\bapprova|\bapprovat|\besam(?:e|ina|inat)|\bapprove[ds]?\b|\bapproval\b|\breviewed\b|"
                           r"\bexamin|\bconvoca|\bconvening\b|\breleases\b", re.I)
# rettifiche / versioni corrette: restano candidati (due depositi dello STESSO documento = ambiguo,
# mai l'uno o l'altro a caso), marcati `rettifica` perche' il motivo lo dica
_RETTIFICA = re.compile(r"rettific|errata\s+corrige|versione\s+corrett|correzione|correction|corrected|"
                        r"corrigendum|amended|amendment|replaces?\b|sostitui", re.I)
_MESI_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
            "settembre", "ottobre", "novembre", "dicembre")
_MESI_EN = ("january", "february", "march", "april", "may", "june", "july", "august",
            "september", "october", "november", "december")


def _regex_periodo(fine: date, tipo: str, pdf: bool = False) -> "re.Pattern":
    """Il periodo scritto nel titolo: «30 giugno 2026», «30/06/2026», «June 30th, 2026»,
    «30 June 2026». Per l'annuale vale anche «esercizio 2025» / «2025 financial year» / l'anno
    da solo dopo il nome del documento (lo controlla chi chiama)."""
    g, m, a = fine.day, fine.month, fine.year
    alt = [r"\b0?%d\s+%s\s+%d\b" % (g, _MESI_IT[m - 1], a),
           r"\b0?%d[./-]0?%d[./-](?:%d|%02d)(?!\d)" % (g, m, a, a % 100),
           r"\b0?%d\s+0?%d\s+%d\b" % (g, m, a),
           r"\b%s\s+0?%d(?:st|nd|rd|th)?,?\s+%d\b" % (_MESI_EN[m - 1], g, a),
           r"\b0?%d(?:st|nd|rd|th)?\s+%s,?\s+%d\b" % (g, _MESI_EN[m - 1], a)]
    if tipo == "annuale":
        nomi = "|".join(_DOC["annuale"])
        alt += [r"esercizio\s+%d\b" % a, r"\b%d\s+(?:financial\s+year|fiscal\s+year)" % a,
                r"(?:financial\s+year|fiscal\s+year|year)\s+%d\b" % a,
                # l'anno subito dopo il nome («Bilancio 2025», «Annual Report 2025», «RFA 2025»)
                r"(?:annuale|%s)\D{0,15}%d\b" % (nomi, a),
                r"\b%d\s+(?:integrated\s+)?(?:annual\s+)?(?:integrated\s+)?(?:financial\s+)?report" % a]
        if m != 12:      # esercizio NON solare: «esercizio 2025/2026», «2025/26»
            alt += [r"\b%d\s*[/-]\s*(?:%d|%02d)\b" % (a - 1, a, a % 100)]
    elif tipo == "semestrale" and m == 6:
        # il semestre scritto a parole (solo per l'esercizio solare: 30/06 = primo semestre)
        alt += [r"primo\s+semestre\s+(?:del(?:l['’]\s*(?:anno|esercizio))?\s+)?%d\b" % a,
                r"first\s+half(?:[\s-]+year)?\s+(?:of\s+)?(?:the\s+year\s+)?%d\b" % a,
                r"\b(?:H1|1H)\s*%d\b" % a,
                # l'anno subito dopo / prima del nome (misura M1 05/10: «Relazione finanziaria
                # semestrale 2026», «Filing of 2026 Half-Year Financial Report»)
                r"(?:%s)\D{0,15}%d\b" % ("|".join(_DOC["semestrale"]), a),
                r"\b%d\s+(?:half[\s-]*year|semi[\s-]*annual)" % a]
    elif tipo == "trimestrale" and m == 3:
        alt += [r"primo\s+trimestre\s+(?:del(?:l['’]\s*(?:anno|esercizio))?\s+)?%d\b" % a,
                r"first\s+quarter\s+(?:of\s+)?%d\b" % a, r"\b(?:Q1|1Q)\s*%d\b" % a,
                r"\bI\s+trimestre\s+(?:del\s+)?%d\b" % a]
    elif tipo == "trimestrale" and m == 9:
        alt += [r"(?:primi\s+)?nove\s+mesi\s+(?:del(?:l['’]\s*(?:anno|esercizio))?\s+)?%d\b" % a,
                r"(?:first\s+)?nine\s+months\s+(?:of\s+|ended\s+)?%d\b" % a, r"\b9M\s*%d\b" % a,
                r"terzo\s+trimestre\s+(?:del\s+)?%d\b" % a, r"third\s+quarter\s+(?:of\s+)?%d\b" % a,
                r"\b(?:Q3|3Q)\s*%d\b" % a, r"\bIII\s+trimestre\s+(?:del\s+)?%d\b" % a]
    if pdf and (tipo == "annuale" or (tipo == "semestrale" and m == 6)):
        # nel TESTO del PDF il nome e' gia' consumato dalla regex del nome: l'anno da solo, entro
        # 60 caratteri dopo il nome, vale come periodo («Annual Report 2025 of ...», misura M1a)
        alt += [r"\b%d\b" % a]
    return re.compile("|".join(alt), re.I)


def _verbo_deposito(t: str) -> Tuple[Optional[str], bool]:
    """('positivo' | 'futuro' | None, forte). Un verbo dentro una frase al FUTURO non conta; il futuro
    seguito da un quotidiano («che sara' pubblicato su Il Sole 24 Ore») e' l'avviso stampa, non il deposito."""
    spans = [(m.start(), m.end()) for m in _FUTURO.finditer(t)]
    futuri = [sp for sp in spans if not _STAMPA.search(t[sp[1]:sp[1] + 40])]

    def fuori(m):
        return not any(a <= m.start() < b for a, b in spans)
    forte = any(fuori(m) for m in _VERBO_FORTE.finditer(t))
    debole = any(fuori(m) for m in _VERBO_DEBOLE.finditer(t))
    if forte or debole:
        return "positivo", forte
    return ("futuro" if futuri else None), False


def _ricuci_cifre(t: str) -> str:
    """pypdf spezza a volte le cifre («31 dicembre 202 5», «Annual Report 202 5», «3 0 giugno 20 26»:
    misure M1a/M1b). Si ricuce un anno 20xx spezzato in due, TRANNE dopo un riferimento di pagina /
    numero / articolo («pagine 20 26» resta com'e': review RV-IT2 S9), e un giorno davanti al nome del mese."""
    mesi = "|".join(_MESI_IT + _MESI_EN)

    def anno(x):
        prima = x.string[max(0, x.start() - 25):x.start()]
        unito = x.group(1) + x.group(2)
        if re.fullmatch(r"20\d\d", unito) and not re.search(r"(?:\bpag\w*|\bpp?\.|\bpages?|\bn\.|\bnr\.?|\bnumer\w*|"
                                                            r"\bart\.?|\bcomma|\bitem|\bpunto)\W*$", prima, re.I):
            return unito
        return x.group(0)
    t = re.sub(r"\b(\d{1,3})\s(\d{1,3})\b", anno, t)
    return re.sub(r"\b([0-3])\s([0-9])(?=\s+(?:%s)\b)" % mesi, r"\1\2", t, flags=re.I)


def _altro_anno_dopo(t: str, pos: int, anno: Optional[int]) -> bool:
    """Il nome del documento e' seguito subito (15 caratteri) da un ANNO diverso dal periodo:
    «Relazione finanziaria annuale 2024 - rif. 31/12/25» e' il documento del 2024 (review RV-IT2 S10).
    «2025/2026» (esercizio non solare) non e' un altro anno."""
    if anno is None:
        return False
    m = re.match(r"\W{0,3}(?:\w+\W+){0,1}?((?:19|20)\d\d)\b(?!\s*[/-]\s*\d)", t[pos:pos + 18])
    return bool(m) and int(m.group(1)) != anno


def _lingua_doc(testo: str, it_rx, en_rx, per, vicini: bool = False, anno: Optional[int] = None) -> Optional[str]:
    """'it' | 'en' | None. Nel TITOLO nome e periodo devono stare entro 80 caratteri l'uno dall'altro (in
    qualunque ordine: «Q1 2026 Interim Management Statement»); nel TESTO di un PDF (vicini=True) il periodo
    deve SEGUIRE il nome entro 60 caratteri, il nome non deve essere citato come punto all'ordine del
    giorno / approvato / verbale, e attorno (200 prima, dopo fino a fine frase) ci vuole un verbo POSITIVO di deposito
    non al futuro. Un nome seguito da un altro anno non vale."""
    t = " ".join((testo or "").split())
    if vicini:
        t = _ricuci_cifre(t)
    if not per.search(t):
        return None
    periodi = [(p.start(), p.end()) for p in per.finditer(t)]
    for lingua, rx in (("it", it_rx), ("en", en_rx)):
        if vicini:
            for m in re.finditer(r"(?:%s).{0,60}?(?:%s)" % (rx.pattern, per.pattern), t, re.I):
                if _RIFERIMENTO_OdG.search(t[max(0, m.start() - 35):m.start()]):
                    continue
                nome = rx.match(t, m.start())
                if nome and _altro_anno_dopo(t, nome.end(), anno):
                    continue
                # il verbo puo' venire dopo un elenco di documenti («la seguente documentazione: - la Relazione
                # ... - le Relazioni ... e' a disposizione del pubblico»: misure M1b su tre emittenti veri): dopo il
                # periodo si guarda fino alla FINE DELLA FRASE (gli elenchi usano «;»), al massimo 1500 caratteri
                coda = t[m.end():m.end() + 1500]
                fine_frase = re.search(r"\.\s+[A-Z]", coda)
                coda = coda[:fine_frase.start()] if fine_frase else coda
                if _verbo_deposito(t[max(0, m.start() - 200):m.end()] + coda)[0] == "positivo":
                    return lingua
            continue
        for m in rx.finditer(t):
            if _altro_anno_dopo(t, m.end(), anno):
                continue
            if any((a - m.end() if a >= m.end() else m.start() - b) <= 80 for a, b in periodi):
                return lingua
    return None


def generici_deposito(righe: List[Dict[str, Any]], fine: date, tipo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Le righe dal titolo GENERICO di deposito pubblicate dopo la fine del periodo, dalla piu'
    vicina al periodo (quelle da leggere nel PDF, nell'ordine in cui leggerle). Con `tipo`
    contano come generici anche i titoli col NOME del documento e il verbo del deposito ma
    SENZA periodo («Messa a disposizione della Relazione finanziaria semestrale»): il periodo
    si cerca nel PDF."""
    nome = re.compile("|".join(_DOC[tipo]), re.I) if tipo in _DOC else None
    visti, out = set(), []
    for r in sorted(righe, key=lambda r: (r["data"], r.get("ora") or "")):
        t = r.get("titolo") or ""
        generico = bool(_GENERICO.search(t)) or bool(nome and nome.search(t) and _verbo_deposito(t)[0] == "positivo")
        if r["data"] <= fine.isoformat() or not generico or _GENERICO_ALTRO.search(t):
            continue
        chiave = r.get("protocollo") or r.get("url_pdf")
        if chiave not in visti:
            visti.add(chiave)
            out.append(r)
    # i titoli SOLO inglesi (la gemella EN del comunicato italiano) si leggono per ultimi: con il
    # tetto dei PDF il budget va prima ai comunicati italiani (l'italiano comanda)
    return [r for r in out if _GENERICO_IT.search(r.get("titolo") or "")] + \
        [r for r in out if not _GENERICO_IT.search(r.get("titolo") or "")]


def candidati_deposito(righe: List[Dict[str, Any]], tipo: str, fine: date,
                       testi_pdf: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Dalle righe della lista (gia' lette, con `categoria`) al verdetto. Puro, senza rete:
    {"stato": ok|non_trovato|ambiguo, "scelto": riga|None, "candidati", "conferme", "motivo",
    "prova": 'titolo'|'testo_pdf'|None, "generici": [righe da leggere nel PDF]}.
    Stadio 1 (titolo): candidata = titolo col NOME del documento + il PERIODO, pubblicata DOPO
    la fine del periodo. Stadio 2 (solo se lo stadio 1 non trova nulla): i comunicati dal titolo
    GENERICO di deposito, confermati dal TESTO del loro PDF (`testi_pdf` {url_pdf: testo}) con
    lo stesso nome + periodo; un PDF non fornito non conferma niente. Lingua: l'italiano
    comanda, l'inglese e' la conferma (la coppia IT/EN e' UN deposito). Fra le candidate si
    tengono quelle col verbo della messa a disposizione, se ce ne sono. Una sola -> ok; piu'
    di una -> ambiguo (mai la prima a caso)."""
    it_rx, en_rx = (re.compile(x, re.I) for x in _DOC[tipo])
    per = _regex_periodo(fine, tipo)
    per_pdf = _regex_periodo(fine, tipo, pdf=True)
    visti, cand, scartati, note = set(), [], [], []

    def _scarta(r, motivo):
        scartati.append({k: r.get(k) for k in ("protocollo", "data", "ora", "titolo", "categoria", "url_pdf")}
                        | {"motivo": motivo})

    for r in righe:
        t = " ".join((r.get("titolo") or "").split())
        chiave = r.get("protocollo") or r.get("url_pdf")
        if chiave in visti:          # la stessa riga in due categorie
            continue
        visti.add(chiave)
        lingua = _lingua_doc(t, it_rx, en_rx, per, anno=fine.year)
        if lingua == "it" and _VERSIONE_EN.search(t):
            lingua = "en"            # gemella inglese col titolo italiano (misura M1a)
        if r["data"] <= fine.isoformat():
            if lingua is not None:
                _scarta(r, "pubblicato il %s, non dopo la fine del periodo" % r["data"])
            continue
        if lingua is None:
            if per.search(t):
                _scarta(r, "periodo senza il nome del documento: comunicato dei RISULTATI o altro, "
                           "non il deposito")
            continue
        if _REVISIONE_SU.search(t):
            _scarta(r, "relazione della societa' di revisione / del collegio sindacale SUL documento: non e' il documento")
            continue
        if _CALENDARIO.search(t):
            _scarta(r, "calendario degli eventi societari: annuncia una data, non e' il deposito")
            continue
        verbo, forte = _verbo_deposito(t)
        contesto = _APPROVAZIONE.search(t) or _PROGETTO.search(t)
        if verbo == "positivo" and (forte or not contesto):
            natura = "avviso_stampa" if _STAMPA.search(t) else "messa_a_disposizione"
        elif tipo == "trimestrale" and _IFPA.search(t) and verbo != "futuro":
            natura = "informazioni_nel_comunicato"
        else:
            if verbo == "futuro":
                motivo = "deposito annunciato al FUTURO («sara' messa a disposizione»): non e' ancora il deposito"
            elif verbo == "positivo":
                motivo = ("approvazione/esame/convocazione col solo «disponibile/available» (riferito ad altro): "
                          "non e' la messa a disposizione")
            elif contesto:
                motivo = "approvazione (CdA o assemblea), esame o convocazione senza verbo di deposito"
            elif _COMUNICATO_RISULTATI.search(t):
                motivo = "comunicato stampa / dei risultati (CS, PR, ricavi, utile...) senza verbo di deposito"
            else:
                motivo = "nome e periodo senza verbo POSITIVO di deposito / messa a disposizione: non basta"
            _scarta(r, motivo)
            continue
        cand.append(dict(r, lingua=lingua, verbo_deposito=True, prova="titolo", natura=natura,
                         rettifica=bool(_RETTIFICA.search(t))))
    prova = "titolo"
    generici = [] if cand else generici_deposito(righe, fine, tipo)
    if not cand and generici and testi_pdf:
        prova = "testo_pdf"
        for r in generici:
            testo = testi_pdf.get(r["url_pdf"])
            lingua = _lingua_doc(testo, it_rx, en_rx, per_pdf, vicini=True, anno=fine.year) if testo else None
            if lingua is not None:
                cand.append(dict(r, lingua=lingua, verbo_deposito=True, prova="testo_pdf",
                                 natura="messa_a_disposizione",
                                 rettifica=bool(_RETTIFICA.search(r.get("titolo") or ""))))
            elif r["url_pdf"] in testi_pdf and not (testo or "").strip():
                _scarta(r, "PDF senza testo estraibile (scansione?): non conferma nulla")
            elif testo:
                _scarta(r, "titolo generico: nel testo del PDF manca il nome del documento seguito dal periodo "
                           "con un verbo POSITIVO di deposito (non al futuro, non come punto all'ordine del giorno)")
    it = [c for c in cand if c["lingua"] == "it"]
    gruppo = it or [c for c in cand if c["lingua"] == "en"]
    # gli avvisi sui quotidiani contano solo se non c'e' il comunicato del deposito
    primari = [c for c in gruppo if c["natura"] != "avviso_stampa"] or gruppo
    conferme = [c for c in cand if c not in primari]
    con_verbo = [c for c in primari if c["verbo_deposito"]]
    scelti = con_verbo or primari
    # piu' comunicati dello STESSO GIORNO (lo stesso diffuso in due categorie, comunicato + avviso
    # di deposito, due PDF dello stesso deposito) senza rettifiche: la DATA e' univoca. Si tiene il
    # primo per ora (regola fissa, non a caso), gli altri vanno fra le conferme; vale anche per la
    # rettifica «annulla e sostituisce» dello stesso giorno. Giorni diversi -> ambiguo.
    if len(scelti) > 1 and len({c["data"] for c in scelti}) == 1:
        ordinati = sorted(scelti, key=lambda c: (c.get("ora") or "99:99", c.get("protocollo") or ""))
        rett = [c for c in ordinati if c.get("rettifica")]
        primo = rett[-1] if rett else ordinati[0]   # la rettifica piu' recente sostituisce le altre (RV-IT2 S12)
        note.append("%d comunicati candidati lo stesso giorno %s: data univoca, scelto %s (gli altri in conferme)"
                    % (len(ordinati), primo["data"], "la rettifica/sostituzione piu' recente" if rett
                       else "il primo per ora"))
        scelti, conferme = [primo], conferme + [c for c in ordinati if c is not primo]
    scartati_n = len(scartati)
    scartati = scartati[:MAX_SCARTATI]
    if not scelti:
        letti = len([g for g in generici if (testi_pdf or {}).get(g["url_pdf"])])
        return {"stato": "non_trovato", "scelto": None, "candidati": [], "conferme": [], "prova": None,
                "generici": generici, "scartati": scartati, "note": note,
                "motivo": "nessun comunicato col nome del documento (%s) e il periodo %s dopo la fine del "
                          "periodo con un verbo POSITIVO di deposito (approvazioni, risultati, annunci al futuro "
                          "non contano); comunicati dal "
                          "titolo generico: %d, PDF letti: %d, nessuno col documento e il periodo nel testo; "
                          "scartati: %d (vedi scartati)" % (tipo, fine.isoformat(), len(generici), letti, scartati_n)}
    if len(scelti) > 1:
        rett = sum(1 for c in scelti if c.get("rettifica"))
        return {"stato": "ambiguo", "scelto": None, "candidati": scelti, "conferme": conferme,
                "prova": prova, "generici": generici, "scartati": scartati, "note": note,
                "motivo": "%d comunicati candidati per lo stesso documento e periodo%s: nessuno scelto "
                          "(vedi candidati)" % (len(scelti), (", %d rettifiche/versioni corrette" % rett) if rett else "")}
    return {"stato": "ok", "scelto": scelti[0], "candidati": scelti, "conferme": conferme,
            "prova": prova, "generici": generici, "scartati": scartati, "note": note, "motivo": None}


_TIPI = tuple(CATEGORIE_DEPOSITO)


def coerenza_tipo_periodo(tipo: str, fine: date, fine_esercizio: Optional[str] = None) -> Optional[str]:
    """None se `tipo` e `periodo_fine` stanno insieme, altrimenti il motivo dell'errore 'parametro'.
    Significati (PM 05/10):
      trimestrale = resoconto intermedio di gestione / informazioni finanziarie periodiche
                    aggiuntive al 31/03 (primo trimestre) o al 30/09 (nove mesi); al 30/06 c'e' la
                    SEMESTRALE, al 31/12 l'ANNUALE;
      semestrale  = relazione finanziaria semestrale al 30/06 (al 31/12 solo per gli esercizi non
                    solari, es. chiusura al 30/06);
      annuale     = relazione finanziaria annuale alla chiusura dell'esercizio (qualunque fine mese).
    Il periodo finisce sempre l'ultimo giorno di un mese.
    `fine_esercizio` 'MM-GG' (es. '04-30') per un esercizio NON solare: annuale = quel mese, semestrale =
    6 mesi dopo, trimestrale = 3 e 9 mesi dopo (es. '04-30': 31/07, 31/10, 31/01). None = esercizio solare."""
    if (fine + timedelta(days=1)).day != 1:
        return "periodo_fine %s non e' l'ultimo giorno di un mese: un periodo contabile finisce a fine mese" % fine
    if fine_esercizio is not None:
        m = re.fullmatch(r"(\d\d)-(\d\d)", str(fine_esercizio))
        if not m or not 1 <= int(m.group(1)) <= 12:
            return "fine_esercizio %r non e' 'MM-GG'" % (fine_esercizio,)
        fe = int(m.group(1))
        attesi = {"annuale": [fe], "semestrale": [(fe + 5) % 12 + 1], "trimestrale": [(fe + 2) % 12 + 1, (fe + 8) % 12 + 1]}
        if fine.month not in attesi[tipo]:
            return ("%s al %s incoerente con l'esercizio che chiude il %s: mesi ammessi %s"
                    % (tipo, fine.strftime("%d/%m"), fine_esercizio, ", ".join("%02d" % x for x in attesi[tipo])))
        return None
    if tipo == "trimestrale" and fine.month not in (3, 9):
        return ("trimestrale al %s incoerente: la trimestrale (resoconto intermedio / informazioni finanziarie "
                "periodiche aggiuntive) e' al 31/03 o al 30/09; al 30/06 c'e' la semestrale, al 31/12 l'annuale"
                % fine.strftime("%d/%m"))
    if tipo == "semestrale" and fine.month not in (6, 12):
        return ("semestrale al %s incoerente: la semestrale e' al 30/06 (al 31/12 solo per un esercizio non "
                "solare)" % fine.strftime("%d/%m"))
    return None


def _base_deposito(ticker: str, tipo: Any, periodo_fine: Any) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper(), "isin": None, "emarket_id": None,
            "tipo": tipo, "periodo_fine": periodo_fine if isinstance(periodo_fine, str) else
            (periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine),
            "stato": "KO", "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "titolo": None, "url": None,
            "protocollo": None, "categoria": None, "lingua": None,
            "candidati": [], "conferme": [], "fonte": FONTE, "categorie_cercate": [],
            "url_liste": [], "sha256_liste": {}, "sha256_pdf": {}, "prova": None,
            "natura": None, "scartati": [], "richieste": {"liste": 0, "pdf": 0, "documenti": 0},
            "tipo_data": None, "fuso": None, "prova_documento": None, "documento": None, "comunicato_conferma": None,
            "url_liste_documenti": [], "sha256_liste_documenti": {}, "stato_documenti": None,
            "risposte_salvate": {}, "oggi_lettura": None,
            "pagine_lette": 0, "letto_il": None,
            "cache": {"stato": "nessuna", "eta_s": None},
            "limiti": ["data: STOCCAGGIO del documento nella sezione Documenti di eMarket "
                       "(tipo_data='stoccaggio_documento', prova='documento') oppure, se il documento li' non "
                       "c'e', DIFFUSIONE del comunicato che annuncia la messa a disposizione "
                       "(tipo_data='diffusione_comunicato'; il PDF linkato e' quel comunicato)",
                       "termini d'uso eMarket (Teleborsa): uso personale non commerciale",
                       "legame col documento per NOME + PERIODO nel titolo (prova='titolo') o, per i "
                       "titoli generici («deposito documenti»), nel testo del PDF del comunicato "
                       "(prova='testo_pdf', sha256 in sha256_pdf); non per hash della relazione",
                       "non_trovato = non trovato nelle categorie cercate, non «mai depositato»",
                       "conta il DEPOSITO / messa a disposizione del documento: il comunicato dei "
                       "risultati e l'approvazione del CdA non sono il deposito (sono in `scartati`)"]}


class _Fermo(Exception):
    """Lettura interrotta: (errore, motivo) da dichiarare. In riverifica: la ricevuta non basta."""

    def __init__(self, errore: str, motivo: str):
        super().__init__(errore)
        self.errore, self.motivo = errore, motivo


def _comprimi(dati: bytes) -> Dict[str, str]:
    import base64
    import gzip
    return {"codifica": "gzip+base64", "corpo": base64.b64encode(gzip.compress(dati, mtime=0)).decode("ascii")}


def _decomprimi(voce: Any) -> bytes:
    import base64
    import gzip
    if not isinstance(voce, dict) or voce.get("codifica") != "gzip+base64" or not isinstance(voce.get("corpo"), str):
        raise ValueError("risposta salvata malformata")
    return gzip.decompress(base64.b64decode(voce["corpo"], validate=True))


def _leggi_deposito(ticker: str, isin: str, id_em: int, tipo: str, fine: date,
                    stadio2: bool = True, *, lista=None, pdf=None) -> Dict[str, Any]:
    """Liste dei comunicati (e PDF dello stadio 2) -> verdetto. `lista(url, cat, pagina)` -> corpo e
    `pdf(riga)` -> (sha256 del PDF, testo | None, nome dell'errore | None) sono la RETE in produzione e le
    RISPOSTE SALVATE in riverifica (stesso percorso, stessa scelta); sollevano _Fermo per fermare."""
    import hashlib
    out = _base_deposito(ticker, tipo, fine)
    out.update(isin=isin, emarket_id=id_em, categorie_cercate=list(CATEGORIE_DEPOSITO[tipo]))

    def lista_rete(url, cat, pagina):
        if out["pagine_lette"]:
            time.sleep(PAUSA_S)
        try:
            http, corpo, _ = _bi._scarica(url)
        except _bi.URLVietato as e:
            # testo controllato: il messaggio dell'eccezione puo' contenere l'URL (regola 16, VF 05/10)
            raise _Fermo("url_vietato", "categoria %d pagina %d: URL rifiutato prima della rete (%s: robots.txt "
                                        "o host non previsto)" % (cat, pagina + 1, type(e).__name__))
        except Exception as e:
            raise _Fermo("rete", "categoria %d pagina %d: %s" % (cat, pagina + 1, type(e).__name__))
        if http != 200:
            raise _Fermo("http", "HTTP %s da eMarket (categoria %d pagina %d)" % (http, cat, pagina + 1))
        return corpo

    def pdf_rete(g):
        time.sleep(PAUSA_S)
        try:
            http, corpo, _ = _bi._scarica(g["url_pdf"])
        except Exception as e:
            raise _Fermo("rete", "PDF %s non letto (%s): verdetto sospeso, poteva essere il deposito"
                         % (g.get("protocollo"), type(e).__name__))
        if http != 200:
            raise _Fermo("http", "PDF %s: HTTP %s, verdetto sospeso" % (g.get("protocollo"), http))
        sha = hashlib.sha256(corpo).hexdigest()
        try:
            from pypdf import PdfReader
            return sha, "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(corpo)).pages), None
        except Exception as e:
            return sha, None, type(e).__name__
    try:
        return _decidi_comunicati(out, id_em, tipo, fine, stadio2, lista or lista_rete, pdf or pdf_rete)
    except _Fermo as f:
        out.update(errore=f.errore, motivo=f.motivo)
        return out


def _decidi_comunicati(out, id_em, tipo, fine, stadio2, lista, pdf) -> Dict[str, Any]:
    import hashlib
    righe: List[Dict[str, Any]] = []
    vuote: List[int] = []
    for cat in CATEGORIE_DEPOSITO[tipo]:
        base = URL_CATEGORIA.format(cat=cat, id=id_em)
        for pagina in range(MAX_PAGINE_DEPOSITO):
            url = base + ("&page=%d" % pagina if pagina else "")
            corpo = lista(url, cat, pagina)
            out["pagine_lette"] += 1
            out["richieste"]["liste"] += 1
            out["url_liste"].append(url)
            out["sha256_liste"][url] = hashlib.sha256(corpo).hexdigest()
            out["risposte_salvate"][url] = _comprimi(corpo)
            p = parse_lista(corpo.decode("utf-8", errors="replace"), id_em)
            if p["stato"] == "non_coperto":
                out.update(stato="non_coperto", errore=p["errore"], motivo=p["motivo"],
                           letto_il=_bi.adesso_utc().isoformat(timespec="seconds"))
                return out
            if p["stato"] == "KO":
                out.update(errore=p["errore"], motivo="categoria %d pagina %d: %s" % (cat, pagina + 1, p["motivo"]))
                return out
            if p["stato"] == "vuoto_misurato":
                vuote.append(cat)
                break
            righe.extend(dict(r, categoria=cat) for r in p["righe"])
            if min(r["data"] for r in p["righe"]) <= fine.isoformat() or not p["pagina_successiva"]:
                break
            if pagina + 1 == MAX_PAGINE_DEPOSITO:
                out["limiti"].append("categoria %d: lette %d pagine senza arrivare alla fine del periodo"
                                     % (cat, MAX_PAGINE_DEPOSITO))
    v = candidati_deposito(righe, tipo, fine)
    if v["stato"] == "non_trovato" and v["generici"] and not stadio2:
        out["limiti"].append("comunicati: %d dal titolo generico NON letti nel PDF (il documento e' gia' "
                             "nella sezione Documenti: il comunicato serve solo da conferma)" % len(v["generici"]))
    if v["stato"] == "non_trovato" and v["generici"] and stadio2:
        # stadio 2: i PDF dei comunicati dal titolo generico, dal piu' vicino al periodo
        testi: Dict[str, str] = {}
        limite_finestra = (fine + timedelta(days=FINESTRA_PDF_GIORNI)).isoformat()
        nella_finestra = [g for g in v["generici"] if g["data"] <= limite_finestra]
        if len(nella_finestra) < len(v["generici"]):
            out["limiti"].append("%d comunicati dal titolo generico oltre %d giorni dalla fine del periodo: "
                                 "PDF non letti" % (len(v["generici"]) - len(nella_finestra), FINESTRA_PDF_GIORNI))
        da_leggere = nella_finestra[:MAX_PDF_DEPOSITO]
        if len(nella_finestra) > MAX_PDF_DEPOSITO:
            out["limiti"].append("%d comunicati dal titolo generico: letti i PDF dei primi %d (prima i titoli "
                                 "italiani, poi quelli solo inglesi; in ogni gruppo dal piu' vicino al periodo)"
                                 % (len(nella_finestra), MAX_PDF_DEPOSITO))
        for g in da_leggere:
            out["richieste"]["pdf"] += 1
            sha, testo, errore = pdf(g)
            out["sha256_pdf"][g["url_pdf"]] = sha
            # AGGIUNTA 5: del PDF si salva il TESTO estratto usato per decidere (i byte li sigilla chi chiama)
            salvato = _comprimi((testo or "").encode("utf-8"))
            salvato.update(tipo="testo_pdf", sha256_pdf=sha, illeggibile=errore)
            out["risposte_salvate"][g["url_pdf"]] = salvato
            if errore is None:
                testi[g["url_pdf"]] = testo
                if not testo.strip():
                    out["limiti"].append("PDF %s senza testo estraibile (scansione?): non conferma nulla"
                                         % g.get("protocollo"))
            else:
                out["limiti"].append("PDF %s illeggibile (%s): non conferma nulla" % (g.get("protocollo"), errore))
        v = candidati_deposito(righe, tipo, fine, testi_pdf=testi)
        out["limiti"].append("stadio 2 (titoli generici): letti %d PDF su %d comunicati generici"
                             % (out["richieste"]["pdf"], len(v["generici"])))
    out.update(stato=v["stato"], motivo=v["motivo"], candidati=v["candidati"], conferme=v["conferme"],
               prova=v["prova"], scartati=v["scartati"], letto_il=_bi.adesso_utc().isoformat(timespec="seconds"))
    if len(vuote) == len(CATEGORIE_DEPOSITO[tipo]):
        # misura M1a: un id presente nel menu ma senza NESSUN comunicato in nessuna categoria cercata
        # (es. emittente estero che pubblica con un altro id): lo si dice, non e' un «non trovato» qualsiasi
        out.update(stato="non_coperto", errore="id_senza_comunicati",
                   motivo="l'id eMarket %d ha le liste VUOTE in tutte le categorie cercate (%s): l'emittente "
                          "potrebbe pubblicare con un altro id o su un altro SDIR; verificare il negozio"
                          % (id_em, ", ".join(str(c) for c in vuote)))
    if v["stato"] == "non_trovato":
        dopo = len({r.get("protocollo") or r.get("url_pdf") for r in righe if r["data"] > fine.isoformat()})
        out["motivo"] += ("; letto: categorie %s, %d pagine, %d comunicati dopo il %s"
                          % (", ".join(str(c) for c in CATEGORIE_DEPOSITO[tipo]), out["pagine_lette"], dopo,
                             fine.isoformat()))
    s = v["scelto"]
    if s is not None:
        out.update(fuso=FUSO, data_deposito=s["data"], ora_deposito=s["ora"], titolo=s["titolo"], url=s["url_pdf"],
                   protocollo=s["protocollo"], categoria=s["categoria"], lingua=s["lingua"], natura=s["natura"])
        out["limiti"].extend(v["note"])
        if s["natura"] == "avviso_stampa":
            out["limiti"].append("la data e' quella dell'AVVISO della messa a disposizione (nessun altro comunicato "
                                 "del deposito trovato): il deposito puo' essere anteriore")
        if s["natura"] == "informazioni_nel_comunicato":
            out["limiti"].append("trimestrale: le informazioni finanziarie periodiche aggiuntive si diffondono "
                                 "col comunicato che le approva; la data e' quella di quel comunicato")
    return out


def _leggi_documento(id_em: int, tipo: str, fine: date, fine_esercizio: Optional[str] = None, *,
                     lettura=None, oggi: Optional[date] = None) -> Dict[str, Any]:
    """Sezione Documenti (modulo emarket_documenti di IT3): {"stato": ok|ambiguo|non_trovato|KO, "errore",
    "motivo", "scelto", "candidati", "conferme", "scartati", "prova", "note", "url_liste", "sha256_liste",
    "finestra", "pagine_lette", "limiti"}. KO = la sezione non e' stata letta: nessun verdetto."""
    from bellomberg.market_data import emarket_documenti as ed   # import pigro: ed importa questo modulo
    out = {"stato": "KO", "errore": None, "motivo": None, "scelto": None, "candidati": [], "conferme": [],
           "scartati": [], "prova": None, "note": [], "url_liste": [], "sha256_liste": {}, "finestra": None,
           "pagine_lette": 0, "limiti": [], "risposte_salvate": {}}
    oggi = oggi or _bi.oggi_roma()
    if fine >= oggi:
        # il periodo non e' ancora finito: nessun documento puo' esserci, lo si dice
        out.update(stato="non_trovato", motivo="sezione Documenti non letta: il periodo che finisce il %s non e' "
                                                "ancora trascorso" % fine.isoformat())
        return out
    try:
        da, a = ed.finestra_documenti(tipo, fine, oggi, fine_esercizio=fine_esercizio)
    except ed.ParametroDocumenti as e:
        # tipo/periodo rifiutati dalla sezione Documenti (es. esercizio non solare: emarket_documenti conosce
        # solo l'esercizio solare): KO 'parametro' dichiarato, mai un crash del tool, mai un ripiego zitto
        out.update(errore="parametro", motivo="sezione Documenti: tipo %s / periodo %s non accettati (%s)"
                                              % (tipo, fine.isoformat(), type(e).__name__))
        return out
    out["finestra"] = [da, a]
    d = (lettura or ed.leggi_documenti)(id_em, data_da=da, data_a=a,
                                        categorie=(ed.CATEGORIA_SEMESTRALE,) if tipo == "semestrale" else ())
    out.update(url_liste=list(d.get("url_liste") or []), sha256_liste=dict(d.get("sha256_liste") or {}),
               pagine_lette=d.get("pagine_lette") or 0, limiti=list(d.get("limiti") or []),
               risposte_salvate=dict(d.get("risposte_salvate") or {}))
    if d.get("stato") == "vuoto_misurato":
        out.update(stato="non_trovato", motivo="sezione Documenti %s..%s: nessun documento" % (da, a))
        return out
    if d.get("stato") != "ok":
        # KO, STALE (lettura vecchia) o non_coperto: la sezione primaria non e' affidabile ora
        out.update(errore="documenti_%s" % (d.get("errore") or str(d.get("stato")).lower()),
                   motivo="sezione Documenti %s (%s): %s" % (d.get("stato"), d.get("errore"), d.get("motivo")))
        return out
    try:
        c = ed.candidati_documento(d["righe"], tipo, fine, fine_esercizio=fine_esercizio)
    except ed.ParametroDocumenti as e:
        out.update(stato="KO", errore="parametro", motivo="sezione Documenti: tipo %s / periodo %s non accettati (%s)"
                                                          % (tipo, fine.isoformat(), type(e).__name__))
        return out
    out.update(stato=c["stato"], scelto=c.get("scelto"), candidati=c.get("candidati") or [],
               conferme=c.get("conferme") or [], scartati=c.get("scartati") or [], prova=c.get("prova"),
               note=c.get("note") or [], motivo=c.get("motivo"))
    return out


def _unisci_documento_comunicato(com: Dict[str, Any], doc: Dict[str, Any], fine: date) -> Dict[str, Any]:
    """Documento = ancora primaria, comunicato = conferma o ripiego. Mai un verdetto se una delle due
    sezioni e' in KO (ci pensa chi chiama)."""
    out = dict(com)
    out.update(url_liste_documenti=doc["url_liste"], sha256_liste_documenti=doc["sha256_liste"],
               stato_documenti=doc["stato"], limiti=list(com["limiti"]) + doc["limiti"],
               risposte_salvate=dict(com["risposte_salvate"], **doc["risposte_salvate"]))
    out["richieste"] = dict(com["richieste"], documenti=doc["pagine_lette"])
    conferma = {"stato": com["stato"], "data": com["data_deposito"], "ora": com["ora_deposito"],
                "protocollo": com["protocollo"], "titolo": com["titolo"], "url": com["url"], "prova": com["prova"],
                "natura": com["natura"], "motivo": com["motivo"], "scarto_giorni": None}
    s = doc["scelto"]
    if doc["stato"] == "ok":
        if com["data_deposito"]:
            conferma["scarto_giorni"] = (date.fromisoformat(com["data_deposito"]) - date.fromisoformat(s["data"])).days
        out.update(stato="ok", errore=None, motivo=None, fuso=s.get("fuso") or FUSO, data_deposito=s["data"], ora_deposito=s.get("ora"),
                   titolo=s.get("titolo"), url=s.get("url"), protocollo=s.get("protocollo"),
                   categoria=s.get("categoria"), lingua=s.get("lingua"), natura="stoccaggio_documento",
                   prova="documento", prova_documento=doc["prova"], tipo_data="stoccaggio_documento",
                   documento=s, candidati=doc["candidati"], conferme=doc["conferme"],
                   scartati=doc["scartati"], comunicato_conferma=conferma)
        out["limiti"] += doc["note"]
        prima = sorted(c.get("ora") or "" for c in doc["conferme"]
                       if c.get("data") == s["data"] and (c.get("ora") or "99:99") < (s.get("ora") or ""))
        if prima:
            out["limiti"].append("ora del documento scelto %s; un'altra versione dello stesso giorno e' piu' antica (%s): "
                                 "vale l'ora piu' tarda (prudente)" % (s.get("ora"), prima[0]))
        if conferma["scarto_giorni"] is not None:
            out["limiti"].append("comunicato di conferma del %s: scarto %+d giorni dal documento"
                                 % (com["data_deposito"], conferma["scarto_giorni"]))
        else:
            out["limiti"].append("nessun comunicato di conferma (%s): vale il documento" % com["stato"])
        return out
    if doc["stato"] == "ambiguo":
        out.update(stato="ambiguo", errore=None, data_deposito=None, ora_deposito=None, titolo=None, url=None,
                   protocollo=None, categoria=None, lingua=None, natura=None, prova="documento",
                   prova_documento=doc["prova"], candidati=doc["candidati"], conferme=doc["conferme"],
                   scartati=doc["scartati"], comunicato_conferma=conferma,
                   motivo="sezione Documenti: %s (comunicato: %s %s)"
                          % (doc["motivo"], com["stato"], com["data_deposito"] or ""))
        return out
    # documento non trovato: vale il comunicato, dichiarato
    if com["stato"] in ("ok", "ambiguo"):
        out["tipo_data"] = "diffusione_comunicato" if com["stato"] == "ok" else None
        out["limiti"].append("documento non stoccato nella sezione Documenti (%s): %s"
                             % ("..".join(doc["finestra"] or []) or "periodo non concluso",
                                "vale la data del comunicato" if com["stato"] == "ok" else "comunicati ambigui"))
        return out
    out["motivo"] = ("non trovato ne' nella sezione Documenti (%s) ne' fra i comunicati: %s"
                     % (doc["motivo"], com["motivo"]))
    return out


MAX_SALVATE_BYTES = 1_000_000   # oltre: dimensione dichiarata nei limiti (AGGIUNTA 5)


def _componi_deposito(ticker, isin, id_em, tipo, fine, fine_esercizio, *, lettura_doc=None, lista=None, pdf=None,
                      oggi=None) -> Dict[str, Any]:
    """1) sezione Documenti (ancora primaria); 2) comunicati (conferma o ripiego). Una sezione in KO = KO: mai
    un verdetto su meta' delle fonti. Le letture sono iniettabili: rete in produzione, risposte salvate in
    riverifica_deposito (stessa scelta)."""
    oggi = oggi or _bi.oggi_roma()
    doc = _leggi_documento(id_em, tipo, fine, fine_esercizio, lettura=lettura_doc, oggi=oggi)
    if doc["stato"] == "KO":
        r = _base_deposito(ticker, tipo, fine)
        r.update(isin=isin, emarket_id=id_em, errore=doc["errore"], motivo=doc["motivo"],
                 url_liste_documenti=doc["url_liste"], sha256_liste_documenti=doc["sha256_liste"],
                 stato_documenti="KO", risposte_salvate=doc["risposte_salvate"])
        r["limiti"] += doc["limiti"]
        return r
    com = _leggi_deposito(ticker, isin, id_em, tipo, fine, stadio2=doc["stato"] != "ok", lista=lista, pdf=pdf)
    if com["stato"] == "KO" or (com["stato"] == "non_coperto" and com["errore"] != "id_senza_comunicati"):
        com.update(url_liste_documenti=doc["url_liste"], sha256_liste_documenti=doc["sha256_liste"],
                   stato_documenti=doc["stato"],
                   risposte_salvate=dict(com["risposte_salvate"], **doc["risposte_salvate"]))
        return com
    out = _unisci_documento_comunicato(com, doc, fine)
    out["oggi_lettura"] = oggi.isoformat()   # la finestra dei documenti dipende dal giorno di lettura (riverifica)
    peso = sum(len(v.get("corpo") or "") for v in out["risposte_salvate"].values() if isinstance(v, dict))
    if peso > MAX_SALVATE_BYTES:
        out["limiti"].append("risposte salvate per la riverifica: %d byte compressi (oltre %d)" % (peso, MAX_SALVATE_BYTES))
    return out


# campi confrontati dalla riverifica (AGGIUNTA 5)
_CAMPI_RIVERIFICA = ("stato", "errore", "data_deposito", "ora_deposito", "titolo", "protocollo", "url", "categoria",
                     "lingua", "natura", "prova", "prova_documento", "tipo_data", "stato_documenti", "url_liste",
                     "sha256_liste", "url_liste_documenti", "sha256_liste_documenti", "sha256_pdf")


def _chiave_cand(c: Dict[str, Any]) -> Tuple:
    return (c.get("protocollo"), c.get("data"), c.get("ora"), c.get("titolo"), c.get("url_pdf") or c.get("url"))


def riverifica_deposito(ricevuta: Any, *, ticker: str, tipo: str, periodo_fine: Any,
                        fine_esercizio: Optional[str] = None) -> Tuple[bool, str]:
    """AGGIUNTA 5: rifa' la scelta COMPLETA (documento primario + comunicato di conferma) SENZA RETE dalle
    `risposte_salvate` della ricevuta: decomprime, ricontrolla gli sha, ripercorre le stesse liste con la stessa
    logica (una lista tolta, aggiunta o manomessa ferma la riverifica) e confronta stato, data, ora, titolo,
    protocollo, url, candidati e conferma. (True, '...') solo se tutto coincide. I PDF dello stadio 2 si
    ricontrollano dal TESTO salvato (lo sha dei byte e' in sha256_pdf: i byte li sigilla chi chiama)."""
    import hashlib
    if not isinstance(ricevuta, dict):
        return False, "ricevuta assente o malformata"
    stato = ricevuta.get("stato_originale") if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    if stato not in ("ok", "ambiguo", "non_trovato"):
        return False, "ricevuta in stato %s: nessuna scelta da riverificare" % stato
    salvate = ricevuta.get("risposte_salvate")
    if not isinstance(salvate, dict) or not salvate:
        return False, "ricevuta senza risposte_salvate (forma precedente all'AGGIUNTA 5): non riverificabile"
    if (ricevuta.get("ticker") != (ticker or "").strip().upper() or ricevuta.get("tipo") != tipo
            or str(ricevuta.get("periodo_fine")) != str(periodo_fine)):
        return False, "ticker, tipo o periodo diversi dalla ricevuta"
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
        letto = date.fromisoformat(str(ricevuta.get("oggi_lettura")))
        id_em = int(ricevuta["emarket_id"])
    except (TypeError, ValueError, KeyError):
        return False, "ricevuta senza periodo, giorno di lettura (oggi_lettura) o id eMarket leggibili"
    sha_liste = ricevuta.get("sha256_liste") or {}
    sha_pdf = ricevuta.get("sha256_pdf") or {}

    def lista(url, cat, pagina):
        if url not in salvate or url not in sha_liste:
            raise _Fermo("ricevuta", "lista %s richiesta dalla scelta e assente dalla ricevuta" % url)
        try:
            corpo = _decomprimi(salvate[url])
        except Exception as e:
            raise _Fermo("ricevuta", "lista %s illeggibile (%s)" % (url, type(e).__name__))
        if hashlib.sha256(corpo).hexdigest() != sha_liste[url]:
            raise _Fermo("ricevuta", "lista %s: sha diverso dalla ricevuta" % url)
        return corpo

    def pdf(g):
        v = salvate.get(g["url_pdf"])
        if not isinstance(v, dict) or v.get("tipo") != "testo_pdf" or v.get("sha256_pdf") != sha_pdf.get(g["url_pdf"]):
            raise _Fermo("ricevuta", "testo del PDF %s assente o non coerente con sha256_pdf" % g.get("protocollo"))
        try:
            testo = _decomprimi(v).decode("utf-8")
        except Exception as e:
            raise _Fermo("ricevuta", "testo del PDF %s illeggibile (%s)" % (g.get("protocollo"), type(e).__name__))
        return v["sha256_pdf"], (None if v.get("illeggibile") else testo), v.get("illeggibile")

    if ricevuta.get("stato_documenti") not in ("ok", "ambiguo", "non_trovato"):
        return False, "ricevuta senza l'esito della sezione Documenti: non riverificabile"
    from bellomberg.market_data import emarket_documenti as ed
    rileggi = getattr(ed, "rileggi_documenti", None)
    if rileggi is None:
        return False, "riverifica della sezione Documenti non disponibile (emarket_documenti.rileggi_documenti)"

    def lettura_doc(id_doc, **k):
        # MAI la rete: la sezione Documenti si rilegge solo dalle risposte salvate
        return rileggi(salvate, ricevuta.get("sha256_liste_documenti") or {}, id_emarket=id_doc, **k)
    rifatto = _componi_deposito(ricevuta.get("ticker"), ricevuta.get("isin"), id_em, tipo, fine, fine_esercizio,
                                lettura_doc=lettura_doc, lista=lista, pdf=pdf, oggi=letto)
    if rifatto["errore"] == "ricevuta" or (rifatto["stato"] == "KO" and stato != "KO"):
        return False, "riverifica fermata: %s" % rifatto["motivo"]
    if rifatto["stato_documenti"] == "KO" or str(rifatto.get("errore") or "").startswith("documenti_"):
        return False, "riverifica fermata nella sezione Documenti: %s" % rifatto["motivo"]
    rif = dict(rifatto, stato=rifatto["stato"])
    ric = dict(ricevuta, stato=stato)
    for k in _CAMPI_RIVERIFICA:
        if rif.get(k) != ric.get(k):
            return False, "campo %s diverso dalla scelta rifatta: ricevuta %r, rifatto %r" % (
                k, str(ric.get(k))[:80], str(rif.get(k))[:80])
    for k in ("candidati", "conferme"):
        if sorted(map(_chiave_cand, rif.get(k) or [])) != sorted(map(_chiave_cand, ric.get(k) or [])):
            return False, "%s diversi dalla scelta rifatta" % k
    cr, cc = rif.get("comunicato_conferma") or {}, ric.get("comunicato_conferma") or {}
    if any(cr.get(k) != cc.get(k) for k in ("stato", "data", "ora", "protocollo", "titolo", "url", "scarto_giorni")):
        return False, "comunicato di conferma diverso dalla scelta rifatta"
    return True, "scelta rifatta senza rete dalle risposte salvate: coincide (stato %s)" % stato


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any,
                      fine_esercizio: Optional[str] = None) -> Dict[str, Any]:
    """Data di deposito su eMarket della relazione finanziaria `tipo` (prima la sezione DOCUMENTI:
    stoccaggio del documento; poi i COMUNICATI: conferma, o ripiego se il documento non c'e')
    (semestrale | annuale | trimestrale) del periodo che finisce il `periodo_fine`
    ('AAAA-MM-GG' o date). Stati: ok | non_trovato | ambiguo | KO | non_coperto | STALE.
    Fa rete: le liste delle categorie e, solo allo stadio 2, al massimo MAX_PDF_DEPOSITO PDF dei
    comunicati dal titolo generico (conteggio in `richieste`); cache 6 h solo per gli `ok`.
    KO 'parametro' anche per tipo/periodo incoerenti (vedi coerenza_tipo_periodo)."""
    if tipo not in _TIPI:
        out = _base_deposito(ticker, tipo, periodo_fine)
        out.update(errore="parametro", motivo="tipo %r non ammesso: %s" % (tipo, ", ".join(_TIPI)))
        return out
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        out = _base_deposito(ticker, tipo, periodo_fine)
        out.update(errore="parametro", motivo="periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,))
        return out
    incoerente = coerenza_tipo_periodo(tipo, fine, fine_esercizio)
    if incoerente:
        out = _base_deposito(ticker, tipo, fine)
        out.update(errore="parametro", motivo=incoerente)
        return out
    voce, err, mot = _bi.voce_ticker_o_auto(ticker)
    if voce is None:
        out = _base_deposito(ticker, tipo, fine)
        out.update(errore=err, motivo=mot)
        return out
    if voce["emarket"] is None:
        out = _base_deposito(ticker, tipo, fine)
        out.update(isin=voce["isin"], stato="non_coperto", errore=_errore_senza_emarket(voce),
                   motivo=_motivo_senza_emarket(voce), voce_da=voce["negozio"])
        return out

    def _leggi():
        # con_cache salva solo ok/vuoto_misurato: non_trovato e ambiguo si rileggono sempre
        return _componi_deposito(ticker, voce["isin"], voce["emarket"], tipo, fine, fine_esercizio)
    voce_da = voce["negozio"]
    out = _bi.con_cache("emarket_deposito_v2_%d_%s_%s%s" % (voce["emarket"], tipo, fine.isoformat(),
                                                           "_fe%s" % fine_esercizio if fine_esercizio else ""),
                        TTL_DEPOSITO_S, _leggi)
    out["ticker"] = (ticker or "").strip().upper()
    out["voce_da"] = voce_da   # 'confermato' | 'automatico'
    return out


if __name__ == "__main__":  # python -m bellomberg.market_data.emarket_sdir TICKER [GIORNI]
    import json
    import sys
    a = sys.argv[1:]
    print(json.dumps(get_internal_dealing(a[0], giorni=int(a[1]) if len(a) > 1 else 180),
                     ensure_ascii=False, indent=2))
