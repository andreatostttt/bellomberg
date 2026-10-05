# -*- coding: utf-8 -*-
"""VOCE 8, TERZO PEZZO — il LETTORE dei comunicati (18/08, Fable 5,
ok PM 13/08 "ok procedi tu").

La parte DETERMINISTICA dell'anello trimestrale-automatica: scaricare il
documento ufficiale dalla fonte per-ticker (fonti_guidance), estrarne il testo
con MISURE, e pescare dalla pagina IR i link-candidati al comunicato dei
risultati. La LETTURA integrale e la proposta guidance+addendum-tesi restano
della sessione al GATE del PM (default: propone — decisione PM aperta):
questo modulo NON chiama nessun LLM e NON scrive mai nel DB.

Anti-fallback-silenzioso (regola PM 14/07):
- download fallito -> stato 'errore' con causa, nessun file monco su disco;
- estrazione vuota -> 'illeggibile' dichiarato, mai stringa vuota con 'ok';
- ogni esito porta i numeri (bytes, pagine, caratteri): una garanzia e' una
  misura, non una frase.

Uso operativo:  python lettore_trimestrali.py TICKER
Scarica la pagina IR del ticker, stampa i candidati-comunicato e salva tutto
in data/trimestrali/<ticker>/ (runtime, non committato).
"""
import os
import re
import hashlib
import ipaddress
import socket
import tempfile
import threading
import time
from io import BytesIO
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests

from bellomberg.core.paths import DATA_DIR

TRIMESTRALI_DIR = str(DATA_DIR / "trimestrali")
HTML_TEXT_EXTRACTOR = 'visible_ixbrl/2'
LEGACY_HTML_TEXT_EXTRACTOR = 'legacy_ixbrl/1'
COMPANY_TEXT_EXTRACTOR = 'company_primary_text/1'

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Bellomberg/1.0 "
      "(lettore trimestrali; contatto PM)")

# Parole che segnalano un comunicato risultati (href + testo del link).
# Le brevi (h1, q2, 9m...) SOLO a confine di parola: 'storia.html' non deve
# scattare per un 'h1' annegato.
_PAROLE_LUNGHE = ("result", "risultati", "earnings", "quarter", "trimestral",
                  "semestral", "half-year", "halfyear", "interim", "relazione",
                  "resoconto", "comunicato", "press-release", "financial-report",
                  "guidance", "annual", "jahresbericht", "geschaeftsbericht",
                  "geschäftsbericht", "halbjahres", "finanzbericht")
_RE_BREVI = re.compile(r"(?<![a-z0-9])(h1|h2|q[1-4]|1h|9m)(?![a-z0-9])")


def estrai_testo(path: str, *, contenuto: bytes | None = None,
                 html_extractor: str = HTML_TEXT_EXTRACTOR) -> dict:
    """Testo con misure; contenuto permette di citare lo stesso snapshot hashato."""
    if html_extractor not in (HTML_TEXT_EXTRACTOR, LEGACY_HTML_TEXT_EXTRACTOR, COMPANY_TEXT_EXTRACTOR):
        raise ValueError('Unknown primary HTML text extraction contract')
    if contenuto is None and not os.path.isfile(path):
        return {"stato": "errore", "testo": "", "caratteri": 0, "pagine": None,
                "formato": None, "motivo": f"file non trovato: {path}"}
    if contenuto is None:
        with open(path, "rb") as fh:
            grezzo = fh.read()
    else:
        grezzo = contenuto

    est = os.path.splitext(path)[1].lower()
    if est == ".pdf" or grezzo[:5] == b"%PDF-":
        return _estrai_pdf(BytesIO(grezzo), verify_empty_pages=html_extractor == COMPANY_TEXT_EXTRACTOR)
    testa = grezzo[:2048].lower()
    if est in (".html", ".htm") or b"<html" in testa or b"<!doctype" in testa:
        return _estrai_html(grezzo, html_extractor=html_extractor)
    try:
        testo = grezzo.decode("utf-8").strip()
    except UnicodeDecodeError:
        testo = ""
    if testo and "\x00" not in testo:
        return {"stato": "ok", "testo": testo, "caratteri": len(testo),
                "pagine": None, "formato": "testo"}
    return {"stato": "illeggibile", "testo": "", "caratteri": 0, "pagine": None,
            "formato": None,
            "motivo": "binario non riconosciuto (ne' PDF ne' HTML ne' testo)"}


def _estrai_pdf(path: str, *, verify_empty_pages=False) -> dict:
    try:
        from pypdf import PdfReader
        lettore = PdfReader(path)
        pagine = [pg.extract_text() or "" for pg in lettore.pages]
        verified_empty = ([number for number, page in enumerate(lettore.pages, 1)
            if not pagine[number - 1].strip() and _pagina_vuota_verificata(page)]
            if verify_empty_pages else [])
    except Exception as e:  # PDF rotto/cifrato/scansionato senza xref sano
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": None, "formato": "pdf",
                "motivo": f"pypdf: {type(e).__name__}: {e}"}
    riferimenti = []
    posizione = 0
    for numero, pagina in enumerate(pagine, 1):
        riferimenti.append({"pagina": numero, "inizio": posizione,
                            "fine": posizione + len(pagina), "testo": pagina})
        posizione += len(pagina) + 1
    testo = "\n".join(pagine)
    if not testo.strip():
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": len(pagine), "formato": "pdf",
                "motivo": ("estrazione VUOTA su %d pagine: probabile PDF "
                           "scansionato (serve OCR)" % len(pagine))}
    vuote = [r["pagina"] for r in riferimenti if not r["testo"].strip() and r["pagina"] not in verified_empty]
    return {"stato": "ok", "testo": testo, "caratteri": len(testo),
            "pagine": len(pagine), "formato": "pdf", "riferimenti": riferimenti,
            "pagine_senza_testo": vuote,
            **({"pagine_vuote_verificate": verified_empty} if verify_empty_pages else {}),
            "avvisi": ([f"Pagine senza testo: {vuote}; contenuto non verificabile senza OCR"]
                       if vuote else [])}


def _pagina_vuota_verificata(page):
    """Only absence of page content and annotations proves a blank physical page.

    An image, a drawing, hidden text or an extraction failure is not a blank.
    Keep this separate from legacy receipts, whose extraction remains exact.
    """
    if page.get('/Annots'):
        return False
    contents = page.get_contents()
    return contents is None or not contents.get_data().strip()


class _TestoHTML(HTMLParser):
    # Inline-XBRL resources/contexts are machine metadata, not visible report
    # text. Keep ordinary ix:nonNumeric/nonFraction facts outside this header.
    _MUTI = ("script", "style", "noscript", "template", "ix:header", "ix:hidden")
    _BLOCCHI = ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "li",
                "section", "article", "br", "hr")

    def __init__(self, *, legacy_inline_resources=False):
        super().__init__()
        self.pezzi = []
        self._muto = 0
        self._muti = tuple(tag for tag in self._MUTI if tag != 'ix:header') if legacy_inline_resources else self._MUTI

    def handle_starttag(self, tag, attrs):
        if tag in self._muti:
            self._muto += 1
        if not self._muto and tag in self._BLOCCHI:
            self.pezzi.append("\n")
        if not self._muto and tag in ("td", "th"):
            self.pezzi.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self._muti and self._muto:
            self._muto -= 1
        if not self._muto and tag in self._BLOCCHI:
            self.pezzi.append("\n")

    def handle_data(self, data):
        if not self._muto:
            self.pezzi.append(data)


def _estrai_html(grezzo: bytes, *, html_extractor=HTML_TEXT_EXTRACTOR) -> dict:
    parser = _TestoHTML(legacy_inline_resources=html_extractor == LEGACY_HTML_TEXT_EXTRACTOR)
    dichiarata = re.search(br"(?:charset\s*=\s*[\"']?|encoding\s*=\s*[\"'])([A-Za-z0-9._-]+)",
                          grezzo[:4096], re.I)
    codifica = dichiarata[1].decode("ascii") if dichiarata else "utf-8-sig"
    if grezzo.startswith((b"\xff\xfe", b"\xfe\xff")):
        codifica = "utf-16"
    try:
        parser.feed(grezzo.decode(codifica))
    except (UnicodeError, LookupError) as exc:
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": None, "formato": "html",
                "motivo": f"codifica HTML {codifica} non leggibile: {exc}"}
    testo = "".join(parser.pezzi).strip()
    if not testo:
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": None, "formato": "html",
                "motivo": "HTML senza testo estraibile"}
    return {"stato": "ok", "testo": testo, "caratteri": len(testo),
            "pagine": None, "formato": "html", "codifica": codifica}


def _richiedi_indirizzi_pubblici(host: str, port: int) -> None:
    """Fail closed on local/private DNS answers before an I-20 HTTP request.

    Requests performs its own resolution later; this is a preflight, not peer pinning.
    """
    canonical = host.rstrip(".").lower()
    if canonical == "localhost" or canonical.endswith(".localhost"):
        raise ValueError("host locale non consentito")
    try:
        literal = ipaddress.ip_address(canonical)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError("indirizzo IP non pubblico")
        return
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError("DNS senza indirizzi: documento non scaricato")
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValueError("DNS risolve a indirizzo IP non pubblico")


MAX_DOCUMENTO = 200 * 1024 * 1024  # tetto di ogni download (fase F; prima nessuno)
# REV_G2a R-1: tempo TOTALE di un download (il `timeout` di requests vale per ogni lettura:
# un server «a gocce» teneva fermo il worker senza limite). Valori da confermare dal PM.
TEMPO_MAX_DOWNLOAD_S = 120
TEMPO_MAX_DOWNLOAD_ESEF_S = 600  # xBRL-JSON delle banche su filings.xbrl.org: decine di MB


class DocumentoTroppoGrande(ValueError):
    pass


def _contenuto_limitato(risposta, max_bytes, fh, scadenza=None):
    """Scrive il corpo in `fh` a flusso, fermandosi oltre `max_bytes`; restituisce
    (sha256, byte). Content-Length dichiarato oltre il tetto: nessuna lettura.

    `scadenza` (time.monotonic): tempo TOTALE. Un timer chiude la risposta allo scadere
    (sblocca la lettura in corso, anche se il server manda un byte ogni tanto) e si
    solleva TimeoutError dichiarato (REV_G2a R-1)."""
    dichiarato = (getattr(risposta, "headers", None) or {}).get("Content-Length")
    if dichiarato and str(dichiarato).isdigit() and int(dichiarato) > max_bytes:
        raise DocumentoTroppoGrande(f"documento oltre il limite di {max_bytes // (1024 * 1024)} MB "
                                    f"({int(dichiarato) // (1024 * 1024)} MB dichiarati)")
    digest, letti = hashlib.sha256(), 0
    a_flusso = getattr(risposta, "raw", None) is not None and hasattr(risposta, "iter_content")
    scaduto, timer = threading.Event(), None

    def oltre():
        return TimeoutError("download oltre il tempo massimo di "
                            f"{(scadenza - inizio):.0f} s (server lento): documento non scaricato")
    inizio = time.monotonic()
    if a_flusso and scadenza is not None:
        def taglia():
            scaduto.set()
            # Ne' close() della risposta ne' shutdown() sbloccano una recv gia' in corso su Windows
            # (misurato: si resta fermi fino al byte dopo). Serve chiudere DAVVERO il socket:
            # socket.close() aspetta i riferimenti di makefile, _real_close() no.
            sock = getattr(getattr(getattr(risposta, "raw", None), "_connection", None), "sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    getattr(sock, "_real_close", sock.close)()
                except OSError:
                    pass
            try:
                risposta.close()
            except Exception:
                pass
        timer = threading.Timer(max(0.0, scadenza - inizio), taglia)
        timer.daemon = True
        timer.start()
    try:
        if a_flusso and hasattr(risposta.raw, "read1"):
            # read1: torna appena arriva qualcosa (iter_content aspetta il blocco intero, e a gocce
            # il controllo della scadenza non arrivava mai); decode_content come iter_content.
            def _pezzi():
                while True:
                    pezzo = risposta.raw.read1(64 * 1024, decode_content=True)
                    if not pezzo:
                        return
                    yield pezzo
            pezzi = _pezzi()
        else:
            pezzi = risposta.iter_content(chunk_size=64 * 1024) if a_flusso else [risposta.content or b""]
        try:
            for pezzo in pezzi:
                if scaduto.is_set() or (scadenza is not None and time.monotonic() > scadenza):
                    raise oltre()
                letti += len(pezzo)
                if letti > max_bytes:
                    raise DocumentoTroppoGrande(f"documento oltre il limite di {max_bytes // (1024 * 1024)} MB")
                digest.update(pezzo)
                fh.write(pezzo)
        except (TimeoutError, DocumentoTroppoGrande):
            raise
        except Exception as exc:
            if scaduto.is_set():  # la lettura e' caduta perche' il timer ha chiuso la risposta
                raise oltre() from exc
            raise
        if scaduto.is_set():  # chiusura a meta': il corpo letto e' troncato, mai uno snapshot buono
            raise oltre()
    finally:
        if timer is not None:
            timer.cancel()
    return digest.hexdigest(), letti


def _chiudi(risposta):
    """Libera la connessione di una risposta a flusso (le risposte finte non hanno `raw`)."""
    if risposta is not None and getattr(risposta, "raw", None) is not None:
        risposta.close()


def scarica_documento(url: str, dest_dir: str, timeout: int = 30, *,
                      host_consentiti=None, public_only: bool = False,
                      max_bytes: int = MAX_DOCUMENTO, solo_https: bool = False,
                      tempo_max_s: float = None) -> dict:
    """Snapshot immutabili; host curati opzionali, controllati prima di ogni GET.

    Senza host_consentiti resta il download legacy con redirect automatici.
    Con allowlist: massimo 6 redirect HTTP(S), nessuna credenziale negli URL.
    Download a flusso con tetto `max_bytes` (oltre: errore, niente file parziali).
    `tempo_max_s`: tempo TOTALE dall'inizio (default TEMPO_MAX_DOWNLOAD_S, ESEF piu' lungo);
    oltre, TimeoutError dichiarato nel motivo (REV_G2a R-1).
    """
    temporaneo = None
    inizio = time.monotonic()
    risposta = None
    creata = False
    try:
        if public_only and host_consentiti is None:
            raise ValueError("public_only richiede host_consentiti espliciti")
        if host_consentiti is None:
            if urlsplit(url).scheme not in ("http", "https"):
                raise ValueError("URL HTTP(S) richiesto")
            headers = {"User-Agent": UA}
            if urlsplit(url).hostname in ("www.sec.gov", "sec.gov", "data.sec.gov"):
                from bellomberg.market_data import sec_edgar
                from bellomberg.market_data.sec_edgar import _headers
                sec_edgar.attendi_sec()  # ritmo SEC condiviso anche sui download
                headers = {**_headers(), "Accept": "*/*"}
            risposta = requests.get(url, timeout=timeout, headers=headers, stream=True)
        else:
            if not isinstance(host_consentiti, (list, tuple, set, frozenset)):
                raise ValueError("host_consentiti richiede una lista o un insieme di hostname")
            if any(not isinstance(host, str) or not host for host in host_consentiti):
                raise ValueError("hostname consentiti non validi")
            consentiti = {host.lower() for host in host_consentiti}
            corrente = url
            visitati = set()
            limite_redirect = 6
            for salto in range(limite_redirect + 1):
                parti = urlsplit(corrente)
                if parti.scheme not in ("http", "https"):
                    raise ValueError("URL HTTP(S) richiesto anche nei redirect")
                if solo_https and parti.scheme != "https":
                    raise ValueError("solo HTTPS, anche nei redirect")
                if parti.username is not None or parti.password is not None:
                    raise ValueError("credenziali negli URL non consentite")
                if not parti.hostname or parti.hostname not in consentiti:
                    raise ValueError(f"host non consentito: {parti.hostname}")
                if public_only:
                    _richiedi_indirizzi_pubblici(parti.hostname, parti.port or (443 if parti.scheme == "https" else 80))
                if corrente in visitati:
                    raise ValueError("redirect circolare: URL gia' visitato")
                visitati.add(corrente)
                headers = {"User-Agent": UA}
                attesa_max = timeout
                if parti.hostname in ("www.sec.gov", "sec.gov", "data.sec.gov"):
                    from bellomberg.market_data import sec_edgar
                    from bellomberg.market_data.sec_edgar import _headers
                    sec_edgar.attendi_sec()  # ritmo SEC condiviso, a ogni salto di redirect
                    headers = {**_headers(), "Accept": "*/*"}
                elif parti.hostname == "filings.xbrl.org":
                    from bellomberg.market_data import esef
                    esef.attendi_esef()  # ritmo prudente condiviso tra processi
                    headers = {**esef._headers(), "Accept": "*/*"}
                    attesa_max = max(timeout, 300)  # xBRL-JSON delle banche: decine di MB
                _chiudi(risposta)  # redirect precedente: connessione liberata
                risposta = requests.get(corrente, timeout=attesa_max, headers=headers,
                                        allow_redirects=False, stream=True)
                if risposta.status_code not in (301, 302, 303, 307, 308):
                    break
                posizione = risposta.headers.get("Location")
                if not posizione or not posizione.strip():
                    raise ValueError("redirect senza Location: documento non scaricato")
                if salto == limite_redirect:
                    raise ValueError(f"limite di {limite_redirect} redirect superato")
                corrente = urljoin(corrente, posizione)
        risposta.raise_for_status()
        nome = re.sub(r"[^A-Za-z0-9._-]", "_",
                      urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or "documento"
        radice, est = os.path.splitext(nome[:100])
        if tempo_max_s is None:
            ospite = urlsplit(getattr(risposta, "url", None) or url).hostname
            tempo_max_s = TEMPO_MAX_DOWNLOAD_ESEF_S if ospite == "filings.xbrl.org" else TEMPO_MAX_DOWNLOAD_S
        scadenza = inizio + tempo_max_s
        creata = not os.path.isdir(dest_dir)
        os.makedirs(dest_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest_dir, delete=False) as fh:
            temporaneo = fh.name
            digest, n_bytes = _contenuto_limitato(risposta, max_bytes, fh, scadenza=scadenza)
        if not n_bytes:
            raise ValueError("documento vuoto")
        percorso = os.path.join(dest_dir, f"{radice}-{digest}{est}")
        try:
            os.link(temporaneo, percorso)  # pubblicazione atomica, MAI sovrascrivere
        except FileExistsError:
            with open(percorso, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != digest:
                    raise ValueError("archivio alterato: hash del file esistente incoerente")
        return {"stato": "ok", "url": url, "url_finale": getattr(risposta, "url", None),
                "path": percorso, "sha256": digest, "bytes": n_bytes,
                "content_type": risposta.headers.get("Content-Type"),
                # firma del file remoto (REV_G2a R-5: pacchetti invalidi riverificati senza riscaricare)
                "etag": risposta.headers.get("ETag"), "last_modified": risposta.headers.get("Last-Modified"),
                "content_length": risposta.headers.get("Content-Length")}
    except Exception as e:
        return {"stato": "errore", "url": url,
                "motivo": f"{type(e).__name__}: {e}"}
    finally:
        _chiudi(risposta)
        if temporaneo and os.path.isfile(temporaneo):
            os.unlink(temporaneo)
        if creata:  # download fallito (vuoto, oltre il tetto): nessuna cartella lasciata
            try:
                os.rmdir(dest_dir)
            except OSError:
                pass  # non vuota: c'e' lo snapshot


class _Ancore(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ancore = []       # [href, testo]
        self._aperta = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            self._aperta = [href, ""]

    def handle_data(self, data):
        if self._aperta is not None:
            self._aperta[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._aperta is not None:
            self.ancore.append(self._aperta)
            self._aperta = None


def candidati_comunicato(html_testo: str, base_url: str) -> list:
    """Link-candidati al comunicato risultati da una pagina IR: parole chiave
    su href+testo, URL assolutizzati, .pdf in testa. Pagina senza candidati =
    lista vuota (esito legittimo, non errore)."""
    parser = _Ancore()
    parser.feed(html_testo)
    candidati = []
    for href, testo in parser.ancore:
        if not href or href.startswith(("mailto:", "javascript:", "#")):
            continue
        blob = (href + " " + testo).lower()
        if not (any(p in blob for p in _PAROLE_LUNGHE) or _RE_BREVI.search(blob)
                or re.search(r"/reports/\d+/document(?:[?#]|$)", href)):
            continue
        url = urljoin(base_url.rstrip("/") + "/", href)
        candidati.append({"url": url, "testo": " ".join(testo.split()),
                          "pdf": url.lower().split("?")[0].endswith(".pdf")})
    candidati.sort(key=lambda c: not c["pdf"])
    return candidati


if __name__ == "__main__":
    import json
    import sys
    from datetime import date

    from bellomberg.market_data.fonti_guidance import fonte_per

    if len(sys.argv) < 2:
        print("uso: python lettore_trimestrali.py TICKER")
        sys.exit(2)
    ticker = sys.argv[1].upper()
    fonte = fonte_per(ticker)
    if fonte["stato"] != "ok":
        print(json.dumps(fonte, indent=2, ensure_ascii=False))
        sys.exit(1)
    base = os.path.join(TRIMESTRALI_DIR, ticker, date.today().isoformat())
    pagina = scarica_documento(fonte["ir_url"], base)
    esito = {"ticker": ticker, "fonte": fonte, "pagina_ir": pagina}
    if pagina["stato"] == "ok":
        estratto = estrai_testo(pagina["path"])
        esito["estrazione"] = {k: v for k, v in estratto.items() if k != "testo"}
        if estratto["formato"] == "html" and estratto["stato"] == "ok":
            with open(pagina["path"], "rb") as fh:
                esito["candidati"] = candidati_comunicato(
                    fh.read().decode("utf-8", errors="replace"),
                    base_url=fonte["ir_url"])
    with open(os.path.join(base, "dossier.json"), "w", encoding="utf-8") as fh:
        json.dump(esito, fh, indent=2, ensure_ascii=False)
    print(json.dumps(esito, indent=2, ensure_ascii=False))
