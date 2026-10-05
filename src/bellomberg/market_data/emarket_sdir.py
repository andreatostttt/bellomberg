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
        except _bi.URLVietato as e:
            out.update(errore="url_vietato", motivo=str(e))
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
URL_CATEGORIA = BASE + "/it/comunicati-finanziari?categoria={cat}&azienda={id}"
CATEGORIE_DEPOSITO = {"semestrale": (101, 150), "annuale": (150, 100, 109), "trimestrale": (150, 109)}
MAX_PAGINE_DEPOSITO = 4      # per categoria; ci si ferma prima appena le righe sono anteriori al periodo
MAX_PDF_DEPOSITO = 4         # PDF dei comunicati dal titolo generico letti al massimo (dichiarato)
TTL_DEPOSITO_S = 6 * 3600

_DOC = {
    "semestrale": (r"relazione\s+(?:finanziaria\s+)?semestrale|bilancio\s+(?:consolidato\s+)?semestrale",
                   r"half[\s-]*year(?:ly)?\s+(?:financial\s+)?report|semi[\s-]*annual\s+(?:financial\s+)?report"),
    "annuale": (r"relazione\s+finanziaria\s+annuale",
                r"annual\s+financial\s+report|(?:integrated\s+)?annual\s+report"),
    "trimestrale": (r"resoconto\s+intermedio\s+di\s+gestione|informazioni\s+finanziarie\s+periodiche\s+aggiuntive",
                    r"interim\s+(?:financial|management)\s+(?:report|statement)|additional\s+periodic\s+financial\s+information"),
}
# titoli GENERICI della messa a disposizione (senza nome del documento): candidati del secondo
# stadio, da confermare SOLO col testo del PDF
_GENERICO = re.compile(r"deposito\s+(?:di\s+|dei\s+)?document|documents?\s+filing|filing\s+of\s+documents?|"
                       r"messa\s+a\s+disposizione\s+(?:del(?:la)?\s+|dei\s+)?document|adempimenti\s+informativi|"
                       r"availability\s+of\s+(?:the\s+)?documents?", re.I)
# verbi della MESSA A DISPOSIZIONE: fra piu' candidati si preferiscono questi (un titolo
# «il CdA approva la relazione...» e' l'approvazione, non il deposito)
_DEPOSITO = re.compile(r"pubblicat|depositat|deposito|messa\s+a\s+disposizione|disponibil|"
                       r"published|filed|made\s+available|available", re.I)
_MESI_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
            "settembre", "ottobre", "novembre", "dicembre")
_MESI_EN = ("january", "february", "march", "april", "may", "june", "july", "august",
            "september", "october", "november", "december")


def _regex_periodo(fine: date, tipo: str) -> "re.Pattern":
    """Il periodo scritto nel titolo: «30 giugno 2026», «30/06/2026», «June 30th, 2026»,
    «30 June 2026». Per l'annuale vale anche «esercizio 2025» / «2025 financial year» / l'anno
    da solo dopo il nome del documento (lo controlla chi chiama)."""
    g, m, a = fine.day, fine.month, fine.year
    alt = [r"\b0?%d\s+%s\s+%d\b" % (g, _MESI_IT[m - 1], a),
           r"\b0?%d[./-]0?%d[./-]%d\b" % (g, m, a),
           r"\b%s\s+0?%d(?:st|nd|rd|th)?,?\s+%d\b" % (_MESI_EN[m - 1], g, a),
           r"\b0?%d(?:st|nd|rd|th)?\s+%s,?\s+%d\b" % (g, _MESI_EN[m - 1], a)]
    if tipo == "annuale":
        alt += [r"esercizio\s+%d\b" % a, r"\b%d\s+(?:financial\s+year|fiscal\s+year)" % a,
                r"(?:financial\s+year|fiscal\s+year|year)\s+%d\b" % a,
                r"(?:annuale|annual\s+financial\s+report)\D{0,12}%d\b" % a]
    return re.compile("|".join(alt), re.I)


def _lingua_doc(testo: str, it_rx, en_rx, per, vicini: bool = False) -> Optional[str]:
    """'it' | 'en' | None. Nel TITOLO basta che nome e periodo ci siano; nel TESTO di un PDF
    (vicini=True) il periodo deve seguire il nome entro 60 caratteri: un comunicato lungo puo'
    citare altrove un altro periodo."""
    t = " ".join((testo or "").split())
    if not per.search(t):
        return None
    for lingua, rx in (("it", it_rx), ("en", en_rx)):
        if vicini:
            if re.search(r"(?:%s).{0,60}?(?:%s)" % (rx.pattern, per.pattern), t, re.I):
                return lingua
        elif rx.search(t):
            return lingua
    return None


def generici_deposito(righe: List[Dict[str, Any]], fine: date) -> List[Dict[str, Any]]:
    """Le righe dal titolo GENERICO di deposito pubblicate dopo la fine del periodo, dalla piu'
    vicina al periodo (quelle da leggere nel PDF, nell'ordine in cui leggerle)."""
    visti, out = set(), []
    for r in sorted(righe, key=lambda r: (r["data"], r.get("ora") or "")):
        if r["data"] <= fine.isoformat() or not _GENERICO.search(r.get("titolo") or ""):
            continue
        chiave = r.get("protocollo") or r.get("url_pdf")
        if chiave not in visti:
            visti.add(chiave)
            out.append(r)
    return out


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
    visti, cand = set(), []
    for r in righe:
        if r["data"] <= fine.isoformat():
            continue
        t = " ".join((r.get("titolo") or "").split())
        lingua = _lingua_doc(t, it_rx, en_rx, per)
        if lingua is None:
            continue
        chiave = r.get("protocollo") or r.get("url_pdf")
        if chiave in visti:          # la stessa riga in due categorie
            continue
        visti.add(chiave)
        cand.append(dict(r, lingua=lingua, verbo_deposito=bool(_DEPOSITO.search(t)), prova="titolo"))
    prova = "titolo"
    generici = [] if cand else generici_deposito(righe, fine)
    if not cand and generici and testi_pdf:
        prova = "testo_pdf"
        for r in generici:
            testo = testi_pdf.get(r["url_pdf"])
            lingua = _lingua_doc(testo, it_rx, en_rx, per, vicini=True) if testo else None
            if lingua is not None:
                cand.append(dict(r, lingua=lingua, verbo_deposito=True, prova="testo_pdf"))
    it = [c for c in cand if c["lingua"] == "it"]
    gruppo = it or [c for c in cand if c["lingua"] == "en"]
    conferme = [c for c in cand if c not in gruppo]
    con_verbo = [c for c in gruppo if c["verbo_deposito"]]
    scelti = con_verbo or gruppo
    if not scelti:
        letti = len([g for g in generici if (testi_pdf or {}).get(g["url_pdf"])])
        return {"stato": "non_trovato", "scelto": None, "candidati": [], "conferme": [], "prova": None,
                "generici": generici,
                "motivo": "nessun titolo col nome del documento (%s) e il periodo %s dopo la fine "
                          "del periodo; comunicati dal titolo generico: %d, PDF letti: %d, nessuno col "
                          "documento e il periodo nel testo" % (tipo, fine.isoformat(), len(generici), letti)}
    if len(scelti) > 1:
        return {"stato": "ambiguo", "scelto": None, "candidati": scelti, "conferme": conferme,
                "prova": prova, "generici": generici,
                "motivo": "%d comunicati candidati per lo stesso documento e periodo: nessuno scelto "
                          "(vedi candidati)" % len(scelti)}
    return {"stato": "ok", "scelto": scelti[0], "candidati": scelti, "conferme": conferme,
            "prova": prova, "generici": generici, "motivo": None}


_TIPI = tuple(CATEGORIE_DEPOSITO)


def _base_deposito(ticker: str, tipo: Any, periodo_fine: Any) -> Dict[str, Any]:
    return {"ticker": (ticker or "").strip().upper(), "isin": None, "emarket_id": None,
            "tipo": tipo, "periodo_fine": periodo_fine if isinstance(periodo_fine, str) else
            (periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine),
            "stato": "KO", "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "titolo": None, "url": None,
            "protocollo": None, "categoria": None, "lingua": None,
            "candidati": [], "conferme": [], "fonte": FONTE, "categorie_cercate": [],
            "url_liste": [], "sha256_liste": {}, "sha256_pdf": {}, "prova": None,
            "pagine_lette": 0, "letto_il": None,
            "cache": {"stato": "nessuna", "eta_s": None},
            "limiti": ["la data e' quella di DIFFUSIONE su eMarket del comunicato che annuncia la "
                       "messa a disposizione (il PDF linkato e' quel comunicato, non la relazione)",
                       "legame col documento per NOME + PERIODO nel titolo (prova='titolo') o, per i "
                       "titoli generici («deposito documenti»), nel testo del PDF del comunicato "
                       "(prova='testo_pdf', sha256 in sha256_pdf); non per hash della relazione",
                       "non_trovato = non trovato nelle categorie cercate, non «mai depositato»"]}


def _leggi_deposito(ticker: str, isin: str, id_em: int, tipo: str, fine: date) -> Dict[str, Any]:
    import hashlib
    out = _base_deposito(ticker, tipo, fine)
    out.update(isin=isin, emarket_id=id_em, categorie_cercate=list(CATEGORIE_DEPOSITO[tipo]))
    righe: List[Dict[str, Any]] = []
    for cat in CATEGORIE_DEPOSITO[tipo]:
        base = URL_CATEGORIA.format(cat=cat, id=id_em)
        for pagina in range(MAX_PAGINE_DEPOSITO):
            url = base + ("&page=%d" % pagina if pagina else "")
            if out["pagine_lette"]:
                time.sleep(PAUSA_S)
            try:
                http, corpo, _ = _bi._scarica(url)
            except _bi.URLVietato as e:
                out.update(errore="url_vietato", motivo=str(e))
                return out
            except Exception as e:
                out.update(errore="rete", motivo="categoria %d pagina %d: %s" % (cat, pagina + 1, type(e).__name__))
                return out
            if http != 200:
                out.update(errore="http", motivo="HTTP %s da eMarket (categoria %d pagina %d)" % (http, cat, pagina + 1))
                return out
            out["pagine_lette"] += 1
            out["url_liste"].append(url)
            out["sha256_liste"][url] = hashlib.sha256(corpo).hexdigest()
            p = parse_lista(corpo.decode("utf-8", errors="replace"), id_em)
            if p["stato"] == "non_coperto":
                out.update(stato="non_coperto", errore=p["errore"], motivo=p["motivo"],
                           letto_il=_bi.adesso_utc().isoformat(timespec="seconds"))
                return out
            if p["stato"] == "KO":
                out.update(errore=p["errore"], motivo="categoria %d pagina %d: %s" % (cat, pagina + 1, p["motivo"]))
                return out
            if p["stato"] == "vuoto_misurato":
                break
            righe.extend(dict(r, categoria=cat) for r in p["righe"])
            if min(r["data"] for r in p["righe"]) <= fine.isoformat() or not p["pagina_successiva"]:
                break
            if pagina + 1 == MAX_PAGINE_DEPOSITO:
                out["limiti"].append("categoria %d: lette %d pagine senza arrivare alla fine del periodo"
                                     % (cat, MAX_PAGINE_DEPOSITO))
    v = candidati_deposito(righe, tipo, fine)
    if v["stato"] == "non_trovato" and v["generici"]:
        # stadio 2: i PDF dei comunicati dal titolo generico, dal piu' vicino al periodo
        testi: Dict[str, str] = {}
        da_leggere = v["generici"][:MAX_PDF_DEPOSITO]
        if len(v["generici"]) > MAX_PDF_DEPOSITO:
            out["limiti"].append("%d comunicati dal titolo generico: letti i PDF dei %d piu' vicini "
                                 "al periodo" % (len(v["generici"]), MAX_PDF_DEPOSITO))
        for g in da_leggere:
            time.sleep(PAUSA_S)
            try:
                http, corpo, _ = _bi._scarica(g["url_pdf"])
            except Exception as e:
                out.update(errore="rete", motivo="PDF %s non letto (%s): verdetto sospeso, poteva essere "
                                                 "il deposito" % (g.get("protocollo"), type(e).__name__))
                return out
            if http != 200:
                out.update(errore="http", motivo="PDF %s: HTTP %s, verdetto sospeso" % (g.get("protocollo"), http))
                return out
            out["sha256_pdf"][g["url_pdf"]] = hashlib.sha256(corpo).hexdigest()
            try:
                from pypdf import PdfReader
                testi[g["url_pdf"]] = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(corpo)).pages)
            except Exception as e:
                out["limiti"].append("PDF %s illeggibile (%s): non conferma nulla" % (g.get("protocollo"), type(e).__name__))
        v = candidati_deposito(righe, tipo, fine, testi_pdf=testi)
    out.update(stato=v["stato"], motivo=v["motivo"], candidati=v["candidati"], conferme=v["conferme"],
               prova=v["prova"], letto_il=_bi.adesso_utc().isoformat(timespec="seconds"))
    s = v["scelto"]
    if s is not None:
        out.update(data_deposito=s["data"], ora_deposito=s["ora"], titolo=s["titolo"], url=s["url_pdf"],
                   protocollo=s["protocollo"], categoria=s["categoria"], lingua=s["lingua"])
    return out


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any) -> Dict[str, Any]:
    """Data di deposito (diffusione SDIR su eMarket) della relazione finanziaria `tipo`
    (semestrale | annuale | trimestrale) del periodo che finisce il `periodo_fine`
    ('AAAA-MM-GG' o date). Stati: ok | non_trovato | ambiguo | KO | non_coperto | STALE.
    Fa rete (lista eMarket, mai i PDF); cache 6 h solo per gli `ok`."""
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
        r = _leggi_deposito(ticker, voce["isin"], voce["emarket"], tipo, fine)
        # con_cache salva solo ok/vuoto_misurato: non_trovato e ambiguo si rileggono sempre
        return r
    voce_da = voce["negozio"]
    out = _bi.con_cache("emarket_deposito_%d_%s_%s" % (voce["emarket"], tipo, fine.isoformat()),
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
