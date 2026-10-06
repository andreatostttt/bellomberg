"""Fonti dal sito dell'emittente (fase F): pacchetti ESEF ufficiali e PDF IR trovati da soli.

Rete gentile e limitata: solo HTTPS, solo il dominio registrabile del sito della società
(sottodomini ammessi), robots.txt rispettato, al massimo MAX_PAGINE pagine HTML per titolo e
profondita' MAX_PROFONDITA, pausa tra le richieste, pagine oltre MAX_PAGINA_BYTES scartate,
nessun JavaScript. L'esplorazione parte solo dal controllo giornaliero, dall'attivazione o da
una richiesta esplicita, mai dalla run del Consigliere: pipeline e impronta leggono la cache
(CACHE_GIORNI). Il pacchetto si scarica (max 80 MB) e si converte in locale (ixbrl_oim); LEI e
periodo si verificano sui fatti come per il repository (filing_esef.documento_esef).
"""
import calendar
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

MAX_PAGINE = 12
MAX_PROFONDITA = 2
MAX_PAGINA_BYTES = 3 * 1024 * 1024
MAX_REDIRECT = 4
PAUSA_S = 1.0
CACHE_GIORNI = 7
SITO_GIORNI = 30  # sito della societa' da yfinance
TIMEOUT_S = 20
TEMPO_PAGINA_S = 60      # tetto di orologio per pagina (il timeout di requests vale per lettura)
TEMPO_SITO_S = 180       # tetto di orologio per l'esplorazione di un titolo
TEMPO_GIRO_S = 900       # tetto del passo giornaliero (tutti i titoli)

_LEI = r"[A-Z0-9]{18}[0-9]{2}"
# Nome ufficiale dei pacchetti ESEF: LEI-AAAA-MM-GG-n[-lingua].xbri|zip (percorsi diversi ogni anno)
_PACCHETTO = re.compile(rf"^({_LEI})-(\d{{4}}-\d{{2}}-\d{{2}})-(\d+)(?:-([a-z]{{2}}))?\b[^/]*\.(xbri|zip)$", re.I)
# Pagine da visitare (prova reale 04/10/2026: parole larghe facevano visitare prodotti e archivi
# software): parole forti da investitori/bilanci, deboli, e negative che escludono la pagina.
_FORTI = re.compile(
    r"investor|investitor|investisseur|inversor|anleger|investoren|aktion[aä]r|azionist|shareholder"
    r"|bilanci|financial[\s_-]*(?:report|result|statement|information)|annual[\s_-]*report|gesch[aä]ftsbericht"
    r"|finanzbericht|rapport[\s_-]*annuel|results|risultati|r[ée]sultats|esef|publications?|pubblicazioni"
    r"|relazioni[\s_-]*finanziarie|reports?[\s_&-]*(?:and[\s_-]*)?presentations?|annual[\s_-]*reports|finanzberichte|filings|md\W?&?\W?a\b|regulated[\s_-]*information|informazione[\s_-]*regolamentata", re.I)
_DEBOLI = re.compile(r"report|relazion|bericht|documenti|documents|downloads|quarter|trimestr|semestr|interim|annual"
                     r"|financ|finanzi", re.I)
_NEGATIVE = re.compile(
    r"prodott|product|finanziament|mutu[oi]|prestit|conti-|carte|sostenib|sustainab|\besg\b|governance|career|carriere"
    r"|lavora|jobs?\b|press|news|notizie|avvisi|privacy|cookie|legal|contatt|contact|tools?\b|software|driver"
    r"|design-resources|support|shop|store|blog|equator|fornitor|supplier|club|landing|famiglie|business/prodotti"
    r"|login|accedi|eventi|events?\b|media\b|video|podcast|general-meeting|hauptversammlung|assemblea|\bagm\b"
    r"|proposals|guida|dialogo|politiche|policy|contacts|contatti|rating|dividend|calendar|calendario|glance"
    r"|fixed-income|anleihen|\bshare\b|-share|aktie\b|sedar|press-release", re.I)
# Sezioni intere da non visitare anche se il titolo parla di risultati (prova reale: comunicati stampa)
_NEGATIVE_PERCORSO = re.compile(r"/(?:media|press|stampa|news|notizie|comunicati|newsroom|blog|careers?|lavora|jobs)(?:/|$|-)", re.I)
_SUFFISSI_2 = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.jp", "co.nz", "com.br", "com.cn", "com.hk",
               "com.sg", "com.mx", "co.za", "com.tr", "co.kr", "co.in", "com.tw", "com.ar", "co.il"}


CARTELLA = None  # cache sotto DATA_DIR/filing_sito; i test la spostano in tmp


def _cache_dir(cache_dir=None):
    if cache_dir is not None:
        return Path(cache_dir)
    if CARTELLA is not None:
        return Path(CARTELLA)
    from bellomberg.core.paths import DATA_DIR
    return Path(DATA_DIR) / "filing_sito"


def _scrivi_json(path, dati):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(dati, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _leggi_json(path):
    try:
        dati = json.loads(Path(path).read_text(encoding="utf-8"))
        return dati if isinstance(dati, dict) else None
    except (OSError, ValueError):
        return None


def _nome_file(ticker):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(ticker).upper())[:40] + ".json"


def dominio_registrabile(host):
    """«group.esempio.example» -> «esempio.example»; suffissi a due livelli noti («co.uk»).
    Approssimazione dichiarata: niente Public Suffix List completa."""
    parti = str(host or "").lower().rstrip(".").split(".")
    if len(parti) < 2:
        return None
    n = 3 if ".".join(parti[-2:]) in _SUFFISSI_2 and len(parti) >= 3 else 2
    return ".".join(parti[-n:])


def stesso_dominio(url, dominio):
    parti = urlsplit(url)
    host = (parti.hostname or "").lower()
    return (parti.scheme == "https" and parti.username is None and parti.password is None
            and bool(host) and (host == dominio or host.endswith("." + dominio)))


# solo codici di lingua veri: «/ir» non e' una lingua (revisione finale: la sezione IR spariva)
_LINGUE = "en|it|de|fr|es|nl|pt|sv|da|fi|no|nb|pl|cs|el|hu|ro|bg|hr|sk|sl|et|lv|lt|ga|mt|ja|zh|ko|ru|tr"


def _normalizza(url):
    """Chiave di una pagina: senza barra finale e senza il prefisso di lingua («/de/…» e «/…»
    sono la stessa pagina per l'esplorazione: prova reale, versioni tedesche doppie)."""
    p = urlsplit(url)
    percorso = re.sub(rf"^/(?:{_LINGUE})(?:-[a-z]{{2}})?(?=/|$)", "", (p.path or "/").rstrip("/"), flags=re.I) or "/"
    return urlunsplit((p.scheme, p.netloc.lower(), percorso, p.query, ""))


def _info_yfinance(ticker):
    import yfinance
    try:  # «XXX.FRA» -> simbolo Yahoo dagli alias dell'app (prova reale: .FRA sconosciuto a Yahoo)
        from bellomberg.cli.price_updater import data_ticker
        simbolo = data_ticker(ticker)
    except Exception:
        simbolo = ticker
    return yfinance.Ticker(simbolo).info or {}


def sito_societa(ticker, *, info_fn=None, cache_dir=None, oggi=None):
    """Sito web della societa' (yfinance `website`), in cache SITO_GIORNI. None se ignoto.
    Solo https (un http si prova come https)."""
    oggi = oggi or date.today()
    path = _cache_dir(cache_dir) / "siti" / _nome_file(ticker)
    voce = _leggi_json(path)
    if voce and voce.get("at"):
        try:
            if (oggi - date.fromisoformat(voce["at"][:10])).days < SITO_GIORNI:
                return voce.get("sito")
        except ValueError:
            pass
    try:
        info = (info_fn or _info_yfinance)(ticker) or {}
        grezzo = str(info.get("website") or "").strip()
    except Exception:
        return (voce or {}).get("sito")  # yfinance assente: vale l'ultimo noto
    sito = None
    if grezzo:
        if "://" not in grezzo:
            grezzo = "https://" + grezzo
        p = urlsplit(grezzo)
        if p.hostname and p.scheme in ("http", "https"):
            sito = urlunsplit(("https", p.hostname.lower(), p.path or "/", "", ""))
    _scrivi_json(path, {"at": oggi.isoformat(), "sito": sito})
    return sito


class _Link(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.link, self._aperto = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._aperto = [href, ""]
                self.link.append(self._aperto)

    def handle_data(self, data):
        if self._aperto is not None and len(self._aperto[1]) < 300:
            self._aperto[1] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self._aperto = None


_FILE_NEL_TESTO = re.compile(
    r"""["'(=]((?:https?:)?(?:\\?/)[^"'\s()<>]{1,300}?\.(?:pdf|xbri|zip))(?:\?[^"'\s<>]{0,200})?["')]""", re.I)
# Prova reale 05/10: dati della pagina in JSON con «/» al posto di «/» e percorsi con spazi
# («/Gruppo/Investor Relations/…pdf») tra virgolette: l'espressione sopra non li vedeva.
_FILE_TRA_VIRGOLETTE = re.compile(
    r"""(["'])((?:https?:)?/[^"'<>\r\n]{1,300}?\.(?:pdf|xbri|zip))(?:\?[^"'\s<>]{0,200})?\1""", re.I)


def estrai_link(html, base):
    """[(url assoluto senza frammento, testo)] dei link di una pagina.

    Prova reale: i PDF possono stare nei dati della pagina e non in <a href>; si
    raccolgono anche gli indirizzi di file nel testo statico (nessun JavaScript eseguito)."""
    p = _Link()
    try:
        p.feed(html)
    except Exception:
        pass
    noti = {h.strip() for h, _ in p.link}
    for m in _FILE_NEL_TESTO.finditer(html):
        grezzo = m.group(1).replace("\\/", "/")
        if grezzo not in noti:
            noti.add(grezzo)
            p.link.append([grezzo, ""])
    testo = re.sub(r"\\u002[fF]", "/", html).replace("\\/", "/")
    for m in _FILE_TRA_VIRGOLETTE.finditer(testo):
        grezzo = m.group(2).strip()
        if grezzo not in noti:
            noti.add(grezzo)
            p.link.append([grezzo, ""])
    out = []
    for href, testo in p.link:
        href = href.strip()
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        try:
            url = urljoin(base, href)
        except ValueError:
            continue
        out.append((url.split("#", 1)[0], " ".join(testo.split())))
    return out


STATI_BLOCCO = (401, 403, 429)
MAX_5XX_DI_FILA = 3  # pagine in errore del server di fila: il sito non sta bene, si riprova al giro dopo
TOKEN_BOT = "Bellomberg"  # nome del nostro agente per le regole «User-agent: Bellomberg» di robots.txt
_URL_NEL_TESTO = re.compile(r"""https?://[^\s'"<>]+|\?[^\s'"<>]*=[^\s'"<>]*""")


def motivo_eccezione(exc, n=160):
    """Tipo e testo dell'eccezione SENZA indirizzi ne' querystring (regola 6; revisione R-8 C7)."""
    return f"{type(exc).__name__}: {_URL_NEL_TESTO.sub('<url>', str(exc))[:n]}"


def frase_blocco(stato):
    """401/403: il sito rifiuta il bot; 429: limite di ritmo (revisione R-8: non e' un rifiuto)."""
    return f"sito limita il ritmo (HTTP {stato})" if stato == 429 else f"sito blocca i bot (HTTP {stato})"


class SitoBloccato(ValueError):
    """Il sito rifiuta il bot (HTTP 401/403) o limita il ritmo (429): esito dichiarato, ci si ferma."""

    def __init__(self, stato, dove=""):
        self.stato = stato
        super().__init__(frase_blocco(stato) + (f" su {dove}" if dove else ""))


class Navigatore:
    """GET di pagine HTML entro un dominio: redirect controllati, IP pubblici, tetto, pausa."""

    def __init__(self, dominio, *, get=None, dormi=None, pausa=PAUSA_S, tempo_max=TEMPO_SITO_S, orologio=None):
        self.dominio, self.pausa = dominio, pausa
        self._orologio = orologio or time.monotonic
        self._scadenza = self._orologio() + tempo_max
        self._get = get
        self._dormi = dormi or time.sleep
        self.richieste = 0
        self._robots = None
        self.bloccato = {}  # host -> stato HTTP con cui il sito rifiuta il bot (dichiarato nell'esito)
        self.robots_ignoto = {}  # host -> perche' robots.txt non si e' letto (RFC 9309: tutto vietato)

    def _http(self, url):
        import requests
        from bellomberg.market_data.lettore_trimestrali import UA, _richiedi_indirizzi_pubblici
        corrente = url
        for _ in range(MAX_REDIRECT + 1):
            if self._orologio() > self._scadenza:
                raise TimeoutError("tempo massimo dell'esplorazione superato")
            if not stesso_dominio(corrente, self.dominio):
                raise ValueError(f"fuori dal dominio {self.dominio}: {urlsplit(corrente).hostname}")
            p = urlsplit(corrente)
            if self._get is None:
                _richiedi_indirizzi_pubblici(p.hostname, p.port or 443)
            if self.richieste:
                self._dormi(self.pausa)
            self.richieste += 1
            r = (self._get or requests.get)(corrente, timeout=TIMEOUT_S, headers={"User-Agent": UA},
                                            allow_redirects=False, stream=True)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                if getattr(r, "raw", None) is not None:
                    r.close()
                corrente = urljoin(corrente, r.headers["Location"])
                continue
            return corrente, r
        raise ValueError("troppi redirect")

    def _corpo(self, r):
        return self._byte(r, MAX_PAGINA_BYTES, "pagina troppo grande").decode(r.encoding or "utf-8", errors="replace")

    def _byte(self, r, massimo, troppo):
        dichiarato = r.headers.get("Content-Length")
        if dichiarato and str(dichiarato).isdigit() and int(dichiarato) > massimo:
            raise ValueError(troppo)
        if getattr(r, "raw", None) is not None:
            pezzi, n = [], 0
            limite = min(self._orologio() + TEMPO_PAGINA_S, self._scadenza)
            for pezzo in r.iter_content(64 * 1024):
                n += len(pezzo)
                if n > massimo:
                    r.close()
                    raise ValueError(troppo)
                if self._orologio() > limite:  # server lento a gocce (revisione finale)
                    r.close()
                    raise TimeoutError("pagina troppo lenta")
                pezzi.append(pezzo)
            r.close()
            grezzo = b"".join(pezzi)
        else:
            grezzo = r.content or b""
            if len(grezzo) > massimo:
                raise ValueError(troppo)
        return grezzo

    def consentito(self, url):
        """robots.txt dell'host, letto una volta per host (RFC 9309, revisione R-8 C4): 200 = regole;
        404 e altri 4xx = consentito; 401/403 = sito che rifiuta i bot (tutto vietato); 5xx o
        irraggiungibile = tutto vietato col motivo in `robots_ignoto`. Le regole valgono sia per il
        nome del nostro agente («User-agent: Bellomberg») sia per lo UA completo."""
        host = urlsplit(url).hostname
        if self._robots is None:
            self._robots = {}
        if host not in self._robots:
            rp = RobotFileParser()
            try:
                _, r = self._http(f"https://{host}/robots.txt")
                if r.status_code in (401, 403):
                    # convenzione di urllib.robotparser: robots.txt negato = tutto vietato (e dichiarato)
                    self.bloccato[host] = r.status_code
                    rp.parse([])
                    rp.disallow_all = True
                elif r.status_code >= 500:
                    self.robots_ignoto[host] = f"HTTP {r.status_code}"
                    rp.parse([])
                    rp.disallow_all = True
                else:
                    rp.parse(self._corpo(r).splitlines() if r.status_code == 200 else [])
            except Exception as exc:
                self.robots_ignoto[host] = motivo_eccezione(exc, 100)
                rp.parse([])
                rp.disallow_all = True
            self._robots[host] = rp
        from bellomberg.market_data.lettore_trimestrali import UA
        return self._robots[host].can_fetch(TOKEN_BOT, url) and self._robots[host].can_fetch(UA, url)

    def pagina(self, url):
        """(url finale, html) di una pagina HTML; eccezione se non e' HTML o va fuori dominio."""
        finale, r = self._http(url)
        if r.status_code in STATI_BLOCCO:
            self.bloccato[urlsplit(finale).hostname] = r.status_code
            raise SitoBloccato(r.status_code, urlsplit(finale).path[:80] or "/")
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        tipo = (r.headers.get("Content-Type") or "").lower()
        if tipo and "html" not in tipo:
            raise ValueError(f"non HTML ({tipo[:40]})")
        return finale, self._corpo(r)

    def prima_pagina_pdf(self, url, max_bytes=None):
        """Testo della prima pagina di un PDF dello stesso dominio (robots.txt, pausa e redirect come
        le pagine; tetto MAX_PDF_PRIMA_PAGINA). Eccezione dichiarata se vietato, bloccato o illeggibile."""
        if not self.consentito(url):
            raise PermissionError(f"robots.txt vieta {urlsplit(url).path[:80]}")
        finale, r = self._http(url)
        if r.status_code in STATI_BLOCCO:
            self.bloccato[urlsplit(finale).hostname] = r.status_code
            raise SitoBloccato(r.status_code, urlsplit(finale).path[:80])
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        grezzo = self._byte(r, max_bytes or MAX_PDF_PRIMA_PAGINA, "PDF troppo grande")
        if not grezzo.startswith(b"%PDF-"):
            raise ValueError("non e' un PDF")
        import io
        from pypdf import PdfReader
        lettore = PdfReader(io.BytesIO(grezzo))
        if not lettore.pages:
            raise ValueError("PDF senza pagine")
        return (lettore.pages[0].extract_text() or "")[:4000]


def _anni(testo):
    return [int(a) for a in re.findall(r"(?<!\d)(20[0-4]\d)(?!\d)", testo)]


def _punteggio(url, testo, oggi=None):
    """Priorita' di una pagina da visitare, sull'ultimo tratto del percorso e sul testo del link
    (prova reale: «investor-relations» in ogni sotto-pagina le rendeva tutte uguali); 0 = no."""
    # tratti limitati: le espressioni restano lineari anche su link ostili (revisione finale)
    chiave = unquote(urlsplit(url).path).rstrip("/").rsplit("/", 1)[-1][:200] + " " + testo[:200]
    forti, deboli = len(_FORTI.findall(chiave)), len(_DEBOLI.findall(chiave))
    if _NEGATIVE.search(chiave) and not forti:
        return 0
    punti = 3 * forti + deboli
    anni = _anni(chiave)
    if punti and anni:  # archivi per anno: prima gli ultimi tre esercizi, i vecchi mai
        punti = punti + 2 if max(anni) >= (oggi or date.today()).year - 3 else 0
    return punti


def _sottopagina(href, padre):
    """Una pagina dentro la stessa sezione («…/bilanci-e-relazioni/2025») non e' un livello in piu'."""
    a, b = urlsplit(href), urlsplit(padre)
    return a.hostname == b.hostname and a.path.rstrip("/").startswith(b.path.rstrip("/") + "/") and len(b.path) > 1


def esplora(sito, *, navigatore=None, max_pagine=MAX_PAGINE, max_profondita=MAX_PROFONDITA, oggi=None):
    """Visita le pagine IR del sito, la piu' promettente prima: {"pagine", "link", "motivi"}.

    Si seguono solo i link dello stesso dominio con parole da investitori/bilanci (negative
    escluse), fino a `max_profondita` livelli dalla home; le sotto-pagine della stessa sezione
    (anni di un archivio) restano al livello della pagina. `link` raccoglie tutti i link di
    tutte le pagine visitate (anche verso file: pacchetti, PDF), una volta sola."""
    dominio = dominio_registrabile(urlsplit(sito).hostname)
    if not dominio:
        return {"pagine": [], "link": [], "motivi": ["sito della societa' non valido"]}
    nav = navigatore or Navigatore(dominio)
    coda, visti, pagine, link, motivi, noti = [(0, 99, sito)], set(), [], [], [], set()
    errori_server = 0
    while coda and len(pagine) < max_pagine:
        coda.sort(key=lambda x: (-x[1], x[0]))
        prof, punti_pagina, url = coda.pop(0)
        chiave = _normalizza(url)
        if chiave in visti:
            continue
        visti.add(chiave)
        try:
            if not nav.consentito(url):
                stato = getattr(nav, "bloccato", {}).get(urlsplit(url).hostname)
                if stato:  # robots.txt stesso negato: il sito rifiuta i bot, ci si ferma
                    motivi.append(f"{frase_blocco(stato)} su robots.txt")
                    break
                ignoto = getattr(nav, "robots_ignoto", {}).get(urlsplit(url).hostname)
                if ignoto:  # RFC 9309: robots.txt non leggibile = nessuna pagina, si riprova al giro dopo
                    motivi.append(f"robots.txt non leggibile ({ignoto}): esplorazione rinviata")
                    break
                motivi.append(f"robots.txt esclude {urlsplit(url).path[:80]}")
                continue
            finale, html = nav.pagina(url)
        except SitoBloccato as exc:
            motivi.append(str(exc))
            break  # mai insistere su un sito che rifiuta il bot
        except Exception as exc:
            motivi.append(f"{urlsplit(url).path[:80] or '/'}: {motivo_eccezione(exc, 120)}")
            errori_server = errori_server + 1 if str(exc).startswith("HTTP 5") else 0
            if errori_server >= MAX_5XX_DI_FILA:
                motivi.append(f"sito non disponibile (HTTP 5xx su {errori_server} pagine di fila): esplorazione "
                              "rinviata")
                break
            continue
        errori_server = 0
        if _normalizza(finale) != chiave and _normalizza(finale) in visti:
            continue  # redirect verso una pagina gia' letta (prova reale)
        visti.add(_normalizza(finale))
        pagine.append(finale)
        for href, testo in estrai_link(html, finale):
            if href not in noti:
                noti.add(href)
                link.append({"url": href, "testo": testo[:200], "pagina": finale})
            if (not stesso_dominio(href, dominio) or _normalizza(href) in visti
                    or re.search(r"\.(pdf|zip|xbri|xhtml|xlsx?|docx?|pptx?|jpe?g|png|gif|svg|mp4|mp3)$",
                                 urlsplit(href).path, re.I)):
                continue
            if _NEGATIVE_PERCORSO.search(unquote(urlsplit(href).path)):
                continue
            sotto = _sottopagina(href, finale)
            livello = prof if sotto else prof + 1
            punti = _punteggio(href, testo, oggi)
            coda_url = unquote(urlsplit(href).path).rstrip("/").rsplit("/", 1)[-1][:200] + " " + testo[:200]
            if sotto and prof and not _NEGATIVE.search(coda_url):
                # schede e anni di una pagina IR («…/bilanci-e-relazioni/2025»): valgono quanto la
                # pagina, i tre esercizi recenti un po' di piu', i vecchi mai
                anni = _anni(coda_url)
                if anni and max(anni) < (oggi or date.today()).year - 3:
                    punti = 0
                else:
                    punti = max(punti, min(punti_pagina, 6) + (2 if anni else 0))
            if livello <= max_profondita and punti:
                # i link di una pagina IR valgono un po' di piu' di quelli della home
                coda.append((livello, punti + (2 if punti_pagina >= 3 and prof else 0), href))
    if coda and len(pagine) >= max_pagine:
        motivi.append(f"limite di {max_pagine} pagine raggiunto")
    return {"pagine": pagine, "link": link, "motivi": motivi, "richieste": nav.richieste,
            "bloccato": next(iter(getattr(nav, "bloccato", {}).values()), None),
            "robots_ignoto": next(iter(getattr(nav, "robots_ignoto", {}).values()), None)}


def pacchetti(link):
    """Pacchetti ESEF tra i link: [{"url", "lei", "period_end", "versione", "lingua"}] (nome
    ufficiale LEI-data-n[-lingua]; la verifica vera e' sui fatti dopo la conversione)."""
    out, visti = [], set()
    for voce in link:
        url = voce["url"]
        nome = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        m = _PACCHETTO.match(nome)
        if not m or url in visti or urlsplit(url).scheme != "https":
            continue
        visti.add(url)
        try:
            date.fromisoformat(m.group(2))
        except ValueError:
            continue
        out.append({"url": url, "lei": m.group(1).upper(), "period_end": m.group(2), "versione": int(m.group(3)),
                    "lingua": (m.group(4) or "").lower() or None, "formato": m.group(5).lower()})
    return out


def scopri(ticker, *, lei=None, oggi=None, forza=False, cache_dir=None, sito_fn=None, navigatore_fn=None,
           prima_pagina_fn=None):
    """Esplora il sito di `ticker` (cache CACHE_GIORNI) e salva pacchetti e link utili.

    Esito: {"ticker", "at", "sito", "pagine", "pacchetti", "pdf", "prime_pagine", "accesso",
    "motivi"}; `pdf` = link a PDF con testo (per la ricerca dei PDF IR), `prime_pagine` = testo
    della prima pagina dei pochi PDF che senza non si decidono, `accesso` = {"stato", "motivo"}
    (bloccato / robots_vieta dichiarati). Chiamare solo fuori dalla run del Consigliere."""
    oggi = oggi or date.today()
    path = _cache_dir(cache_dir) / _nome_file(ticker)
    voce = _leggi_json(path)
    if voce:
        try:
            # scadenza sul giorno scritto con lo stesso orologio di `oggi` («giorno»); `at` e'
            # l'ora vera (limite di un'ora sotto). Voci vecchie senza «giorno»: data di `at`
            giorni = (oggi - date.fromisoformat(str(voce.get("giorno") or voce.get("at"))[:10])).days
            # esplorazione fallita (nessuna pagina letta): si riprova al giro successivo, non fra 7 giorni
            if not forza and giorni < (1 if voce.get("fallita") else CACHE_GIORNI):
                return voce
            # anche su richiesta, al massimo un'esplorazione l'ora per titolo
            if forza and datetime.now() - datetime.fromisoformat(str(voce.get("at"))) < timedelta(hours=1):
                return voce
        except ValueError:
            pass
    sito = (sito_fn or (lambda t: sito_societa(t, cache_dir=cache_dir, oggi=oggi)))(ticker)
    esito = {"ticker": ticker, "at": datetime.now().isoformat(timespec="seconds"), "giorno": oggi.isoformat(),
             "sito": sito,
             "pagine": [], "pacchetti": [], "pdf": [], "motivi": []}
    if not sito:
        esito["motivi"].append("sito della societa' non noto (yfinance)")
        esito["accesso"] = {"stato": "sito_ignoto", "motivo": "sito della societa' non noto (yfinance)"}
    else:
        dominio = dominio_registrabile(urlsplit(sito).hostname)
        nav = (navigatore_fn or (lambda d: Navigatore(d)))(dominio) if dominio else None
        visita = esplora(sito, navigatore=nav)
        esito.update(pagine=visita["pagine"], motivi=visita["motivi"])
        # solo pacchetti dal dominio esplorato (il LEI si riverifica comunque sui fatti)
        esito["pacchetti"] = [p for p in pacchetti(visita["link"]) if stesso_dominio(p["url"], dominio)]
        esito["fallita"] = not visita["pagine"]
        esito["pdf"] = [v for v in visita["link"] if urlsplit(v["url"]).scheme == "https"
                        and urlsplit(v["url"]).path.lower().endswith(".pdf")][:300]
        # PDF col nome generico o senza periodo: prima pagina letta (pochi, stesso dominio, robots.txt)
        prime = {}
        if nav is not None and not visita.get("bloccato"):
            leggi = prima_pagina_fn or getattr(nav, "prima_pagina_pdf", None)
            for url in (da_leggere_prima_pagina(esito["pdf"], dominio) if leggi else []):
                try:
                    prime[url] = {"testo": str(leggi(url))[:4000]}
                except Exception as exc:
                    prime[url] = {"errore": motivo_eccezione(exc)}
                    if isinstance(exc, SitoBloccato):
                        break
        esito["prime_pagine"] = prime
        esito["accesso"] = _accesso(visita)
        if lei and not any(p["lei"] == str(lei).upper() for p in esito["pacchetti"]):
            esito["motivi"].append(f"nessun pacchetto ESEF col LEI {str(lei).upper()} nelle pagine visitate")
        # PDF delle relazioni (decisione PM 05/10): esito in testa ai motivi, origine dichiarata
        scelta = scegli_pdf(esito["pdf"], oggi=oggi, prime_pagine=prime, dominio=dominio)
        if scelta:
            frase = (f"relazione {scelta['tipo']} al {scelta['ultimo']['periodo']} in PDF dal {ETICHETTA_SITO}"
                     f" ({scelta['candidati']} PDF ammessi, {scelta['scartati_totale']} scartati col motivo)")
        elif esito["pdf"]:
            frase = riepilogo_scarti(esito["pdf"], oggi=oggi, prime_pagine=prime, dominio=dominio)
        else:
            frase = None
        if frase:
            esito["motivi"].insert(1 if esito["accesso"]["stato"] != "ok" else 0, frase)
    _scrivi_json(path, esito)
    return esito


def _accesso(visita):
    """Esito dell'accesso al sito, dichiarato: ok | bloccato (HTTP 401/403/429) | robots_illeggibile |
    robots_vieta | non_raggiunto."""
    if visita.get("bloccato"):
        return {"stato": "bloccato", "motivo": frase_blocco(visita["bloccato"])}
    if visita.get("robots_ignoto") and not visita.get("pagine"):
        return {"stato": "robots_illeggibile",
                "motivo": f"robots.txt non leggibile ({visita['robots_ignoto']}): esplorazione rinviata"}
    motivi = visita.get("motivi") or []
    if not visita.get("pagine") and any(m.startswith("robots.txt esclude") for m in motivi):
        return {"stato": "robots_vieta", "motivo": "robots.txt vieta l'esplorazione delle pagine del sito"}
    if not visita.get("pagine"):
        return {"stato": "non_raggiunto", "motivo": "nessuna pagina letta" + (f": {motivi[0]}" if motivi else "")}
    return {"stato": "ok", "motivo": None}


def righe_da_cache(lei, *, cache_dir=None, preferita="en"):
    """Righe di catalogo dai pacchetti trovati sul sito (sola cache, nessuna rete): una per
    esercizio, lingua preferita se c'e', poi la versione piu' alta. Forma delle righe del
    repository con `origine: "sito"` e `json_url` = URL del pacchetto."""
    lei = str(lei or "").upper()
    cartella = _cache_dir(cache_dir)
    trovati = []
    try:
        file = sorted(cartella.glob("*.json"))
    except OSError:
        file = []
    for path in file:
        voce = _leggi_json(path) or {}
        for p in voce.get("pacchetti") or []:
            if isinstance(p, dict) and p.get("lei") == lei and p.get("period_end") and p.get("url"):
                trovati.append(p)
    per_periodo = {}
    for p in trovati:
        chiave = (p.get("lingua") == preferita, p.get("lingua") is None, int(p.get("versione") or 0))
        attuale = per_periodo.get(p["period_end"])
        if attuale is None or chiave > attuale[0]:
            per_periodo[p["period_end"]] = (chiave, p)
    return [{"id": p["url"], "period_end": p["period_end"], "json_url": p["url"], "report_url": p["url"],
             "language": None, "date_added": None, "origine": "sito", "lingua_nome": p.get("lingua")}
            for _, (_, p) in sorted(per_periodo.items(), reverse=True)]


INDICE_SITO = "esef_sito_indice.json"
# Revisione 04/10 (R8): pacchetti invalidi (zip/XHTML rotti) ricordati per URL, sha256 e data:
# prima si riscaricavano (fino a 80 MB) a ogni giro come se l'errore fosse transitorio.
INVALIDI_SITO = "esef_sito_invalidi.json"
GIORNI_PACCHETTO_INVALIDO = 30


class PacchettoInvalidoNoto(ValueError):
    """Pacchetto gia' trovato invalido entro GIORNI_PACCHETTO_INVALIDO: non riscaricato (dichiarato)."""
_LOCK_INDICE = threading.Lock()  # fino a 4 run in parallelo scrivono lo stesso indice (revisione finale)
_IN_CORSO, _LOCK_IN_CORSO = set(), threading.Lock()


def esplorazione_in_corso(ticker, inizio=True):
    """Una sola esplorazione per titolo alla volta (route su richiesta): False se gia' in corso."""
    with _LOCK_IN_CORSO:
        if inizio:
            if ticker in _IN_CORSO:
                return False
            _IN_CORSO.add(ticker)
        else:
            _IN_CORSO.discard(ticker)
        return True


_FIRMA_REMOTA = ("etag", "last_modified", "content_length")


def _intestazioni_remote(url, hosts):
    """HEAD di controllo (REV_G2a R-5): {"etag", "last_modified", "content_length"} del file
    remoto. Solo https, solo host ammessi, IP pubblico, NESSUN redirect seguito, nel ritmo
    condiviso (1/s). Eccezione se non verificabile: chi chiama tiene il blocco e lo dice."""
    import requests
    from bellomberg.market_data import esef
    from bellomberg.market_data.lettore_trimestrali import UA, _richiedi_indirizzi_pubblici
    p = urlsplit(str(url or ""))
    if p.scheme != "https" or not p.hostname or p.hostname.lower() not in {h.lower() for h in hosts}:
        raise ValueError(f"HEAD di controllo rifiutata: host non ammesso o non https ({p.hostname})")
    if p.username is not None or p.password is not None:
        raise ValueError("credenziali negli URL non consentite")
    _richiedi_indirizzi_pubblici(p.hostname, p.port or 443)
    esef.attendi_esef()
    r = requests.head(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=False)
    if r.status_code != 200:
        raise ValueError(f"HEAD di controllo: HTTP {r.status_code}")
    h = r.headers or {}
    return {"etag": h.get("ETag"), "last_modified": h.get("Last-Modified"), "content_length": h.get("Content-Length")}


def scarica_pacchetto_json(url, archivio, hosts, *, scarica_fn=None, forza=False):
    """Pacchetto dal sito -> xBRL-JSON nell'archivio: {"stato": "ok", "path", "sha256", ...}.

    Pacchetto max 80 MB (download_sicuro), XHTML max 200 MB, zip controllato (ixbrl_oim).
    Cache per URL e sha256 del pacchetto: lo stesso pacchetto non si riconverte.
    Pacchetto invalido: ricordato GIORNI_PACCHETTO_INVALIDO giorni, salvo `forza` o file
    remoto cambiato (ETag/Last-Modified/Content-Length via HEAD nel ritmo, REV_G2a R-5)."""
    from bellomberg.market_data import download_sicuro, ixbrl_oim
    archivio = Path(archivio)
    indice_path = archivio / INDICE_SITO
    indice = _leggi_json(indice_path) or {}
    voce = indice.get(url)
    if isinstance(voce, dict):
        try:
            if hashlib.sha256(Path(voce["path"]).read_bytes()).hexdigest() == voce["sha256"]:
                return {"stato": "ok", "url": url, "path": voce["path"], "sha256": voce["sha256"],
                        "pacchetto_sha256": voce.get("pacchetto_sha256"), "da_archivio": True}
        except (OSError, KeyError, TypeError):
            pass
    invalidi_path = archivio / INVALIDI_SITO
    noto = (_leggi_json(invalidi_path) or {}).get(url)
    if isinstance(noto, dict) and not forza:
        try:
            eta_giorni = (time.time() - float(noto["quando"])) / 86400
        except (KeyError, TypeError, ValueError):
            eta_giorni = None  # voce illeggibile: si riprova
        if eta_giorni is not None and 0 <= eta_giorni < GIORNI_PACCHETTO_INVALIDO:
            firma = {k: noto.get(k) for k in _FIRMA_REMOTA}
            cambiato, verifica = False, "file remoto senza ETag/Last-Modified: nessuna verifica possibile"
            if any(firma.values()):
                try:
                    remota = _intestazioni_remote(url, hosts)
                    cambiato = any(firma[k] and remota.get(k) != firma[k] for k in _FIRMA_REMOTA)
                    verifica = "file remoto invariato (ETag/Last-Modified)"
                except Exception as exc:
                    verifica = f"verifica del file remoto non riuscita ({type(exc).__name__}: {str(exc)[:120]})"
            if not cambiato:
                raise PacchettoInvalidoNoto(
                    f"pacchetto gia' trovato invalido il {date.fromtimestamp(float(noto['quando'])).isoformat()} "
                    f"(sha256 {str(noto.get('pacchetto_sha256'))[:12]}: {noto.get('motivo')}); {verifica}; "
                    f"non riscaricato fino a {GIORNI_PACCHETTO_INVALIDO} giorni dopo")
    pacchetto = (scarica_fn or download_sicuro.scarica_limitato)(
        url, archivio / "pacchetti", download_sicuro.MAX_PACCHETTO_ESEF, hosts)
    if pacchetto.get("stato") != "ok":
        raise ValueError(pacchetto.get("motivo") or "pacchetto non scaricato")
    try:
        dati = ixbrl_oim.converti_pacchetto(pacchetto["path"], max_xhtml_bytes=download_sicuro.MAX_XHTML_ESEF)
    except ixbrl_oim.PacchettoNonValido as exc:
        with _LOCK_INDICE:
            invalidi = _leggi_json(invalidi_path) or {}
            invalidi[url] = {"pacchetto_sha256": pacchetto.get("sha256"), "motivo": str(exc)[:300],
                             "quando": time.time(), **{k: pacchetto.get(k) for k in _FIRMA_REMOTA}}
            try:
                _scrivi_json(invalidi_path, invalidi)
            except OSError as errore_memoria:  # REV_G2a R-5: dichiarato, mai zitto
                raise ixbrl_oim.PacchettoNonValido(
                    f"{exc}; memoria del pacchetto invalido NON scritta ({type(errore_memoria).__name__}: "
                    f"{errore_memoria}): sara' riscaricato al prossimo giro") from exc
        raise
    finally:
        try:
            os.unlink(pacchetto["path"])  # serve solo il JSON convertito (lo sha del pacchetto resta)
        except OSError:
            pass
    grezzo = json.dumps(dati, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(grezzo).hexdigest()
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", unquote(urlsplit(url).path.rsplit("/", 1)[-1]))[:100]
    dest = archivio / f"{os.path.splitext(nome)[0]}-{digest}.json"
    archivio.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        fd, tmp = tempfile.mkstemp(dir=archivio, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(grezzo)
        os.replace(tmp, dest)
    with _LOCK_INDICE:  # si rilegge sotto lock: le voci scritte da altri run restano
        indice = _leggi_json(indice_path) or {}
        indice[url] = {"path": str(dest), "sha256": digest, "pacchetto_sha256": pacchetto["sha256"]}
        try:
            _scrivi_json(indice_path, indice)
        except OSError:
            pass  # cache best-effort
    return {"stato": "ok", "url": url, "path": str(dest), "sha256": digest,
            "pacchetto_sha256": pacchetto["sha256"], "conversione": dati.get("conversione")}


def atteso_piu_recente(ultimo, oggi):
    """True se dopo l'esercizio `ultimo` (data di chiusura) dovrebbe gia' esserci il successivo:
    le relazioni annuali escono entro ~4 mesi dalla chiusura (130 giorni di margine)."""
    if not ultimo:
        return True
    try:
        chiusura = date.fromisoformat(str(ultimo)[:10])
    except ValueError:
        return True
    return oggi > chiusura + timedelta(days=365 + 130)


GLEIF = "https://api.gleif.org/api/v1/lei-records"
GLEIF_PAGINA = 20


GLEIF_MAX_BYTES = 2 * 1024 * 1024  # una pagina di record LEI: poche decine di KB
_GLEIF_INTERVALLO_S = 1.0
_GLEIF_LOCK = threading.Lock()
_GLEIF_ULTIMA = [0.0]


def attendi_gleif(*, percorso=None):
    """Ritmo condiviso tra processi per api.gleif.org (1 richiesta/s), stesso lock di SEC/ESEF.
    Il file sta accanto a quello ESEF (i test lo reindirizzano insieme)."""
    from bellomberg.market_data import esef
    from bellomberg.market_data.sec_edgar import _attendi
    _attendi(Path(percorso) if percorso else esef._ritmo_path().with_name(".gleif_ritmo"),
             _GLEIF_INTERVALLO_S, _GLEIF_LOCK, _GLEIF_ULTIMA)


def _gleif_scarica(nome):
    """GET GLEIF nel ritmo, UA di esef._headers (contatto SEC_CONTACT_EMAIL se c'e', altrimenti UA generico del progetto) e tetto di byte
    letto a flusso (REV_G2a R-3/R14: prima nessun ritmo, UA senza contatto, corpo senza tetto)."""
    import requests
    from bellomberg.market_data import esef
    ua = esef._headers()["User-Agent"]  # mai ContattoMancante: senza contatto UA generico (decisione PM 05/10)
    attendi_gleif()
    r = requests.get(GLEIF, params={"filter[fulltext]": nome, "page[size]": GLEIF_PAGINA},
                     headers={"Accept": "application/vnd.api+json", "User-Agent": ua},
                     timeout=20, stream=True)
    try:
        r.raise_for_status()
        pezzi, letti = [], 0
        for pezzo in r.iter_content(64 * 1024):
            letti += len(pezzo)
            if letti > GLEIF_MAX_BYTES:
                raise ValueError(f"risposta GLEIF oltre {GLEIF_MAX_BYTES // (1024 * 1024)} MB: scartata")
            pezzi.append(pezzo)
    finally:
        r.close()
    dati = json.loads(b"".join(pezzi).decode("utf-8")).get("data")
    return dati if isinstance(dati, list) else []


def gleif_lei_records(nome):
    """Ricerca gratuita per nome sull'API GLEIF (nessuna chiave): record LEI grezzi."""
    return _gleif_scarica(nome)


def consigliere_in_corso():
    """True se una run del Consigliere e' in corso (processo consigliere_multi): l'esplorazione
    dei siti aspetta il giro successivo. Senza psutil: False (il backend lo dichiara nel log)."""
    try:
        import psutil
    except ImportError:
        return False
    from bellomberg.core.processi_run import e_run_consigliere
    for p in psutil.process_iter(["cmdline"]):
        try:
            # revisione R-8 C6: la FORMA dell'argv, non la parola (una shell che la cita non e' una run)
            if e_run_consigliere(p.info.get("cmdline") or []):
                return True
        except Exception:
            continue
    return False


_RINVIO = "run del Consigliere in corso: siti esplorati al prossimo giro"


def _fermati(consigliere_fn, scadenza, orologio=time.monotonic):
    """Motivo per smettere di esplorare prima del prossimo titolo, None per continuare: la run del
    Consigliere si ricontrolla a ogni titolo (revisione finale) e il giro ha un tetto di tempo."""
    if (consigliere_fn or consigliere_in_corso)():
        return _RINVIO
    if scadenza is not None and orologio() > scadenza:
        return f"tempo massimo del giro ({TEMPO_GIRO_S} s) superato: altri siti al prossimo giro"
    return None


def scopri_profili(store, *, oggi=None, consigliere_fn=None, indice_fn=None, scopri_fn=None, escludi=(), scadenza=None):
    """Controllo giornaliero: per i profili ESEF a blocchi col repository fermo o senza
    depositi, esplora il sito dell'emittente (cache CACHE_GIORNI: al piu' un giro a settimana).
    Mai durante una run del Consigliere. [{"ticker", "pacchetti", "motivi"}]."""
    from bellomberg.market_data import esef
    oggi = oggi or date.today()
    esiti = []
    for riga in store.list_profiles():
        profilo = riga.get("profile") or {}
        ticker = riga.get("ticker")
        if (not riga.get("enabled") or ticker in escludi or profilo.get("esef_modo") != "blocchi"
                or not profilo.get("lei")):
            continue
        try:
            indice = (indice_fn or esef.indice_depositi)(profilo["lei"])
            # ultimo esercizio CON xBRL-JSON: un deposito senza JSON non e' confrontabile (revisione finale)
            ultimo = max((r["period_end"] for r in indice.get("righe") or []
                          if isinstance(r, dict) and r.get("period_end") and r.get("json_url")), default=None)
            if not atteso_piu_recente(ultimo, oggi):
                continue
            motivo = _fermati(consigliere_fn, scadenza)
            if motivo:
                esiti.append({"ticker": None, "rinviata": True, "motivi": [motivo]})
                break
            trovato = (scopri_fn or scopri)(ticker, lei=profilo["lei"], oggi=oggi)
            esiti.append({"ticker": ticker, "pacchetti": sum(1 for p in trovato.get("pacchetti") or []
                                                            if p.get("lei") == str(profilo["lei"]).upper()),
                          "motivi": (trovato.get("motivi") or [])[:5]})
        except Exception as exc:  # un sito non ferma gli altri
            esiti.append({"ticker": ticker, "pacchetti": 0, "motivi": [motivo_eccezione(exc)]})
    return esiti


# ------------------------------------------------------------------ PDF IR (Task 5)
# Decisione PM 05/10 (opzione B): per QUALUNQUE emittente senza documenti dall'archivio ufficiale
# (OAM/ESEF/SEC) valgono le relazioni periodiche in PDF pubblicate sul suo sito, sempre con
# l'origine dichiarata: mai presentate come deposito ufficiale. Nessun nome di societa' qui.
ORIGINE_SITO = "sito_emittente"
ETICHETTA_SITO = "sito dell'emittente, non archivio ufficiale (OAM)"
ETICHETTA_SITO_EN = "issuer website, not the official archive (OAM)"
MAX_PRIME_PAGINE = 3                     # PDF col nome generico o senza periodo: prima pagina letta, per esplorazione
MAX_PDF_PRIMA_PAGINA = 40 * 1024 * 1024  # come download_sicuro.MAX_PDF
MAX_SCARTATI = 25                        # scarti riportati col motivo (il totale resta contato)
# Presentazioni, slide, conference call per investitori: mai una relazione (motivo dichiarato)
_PRESENTAZIONE = re.compile(
    r"presentation|pr[aä]e?sentation|pr[ée]sentation|presentazione|presentaci[oó]n|slides?\b|\bdeck\b|webcast"
    r"|conference[\s_-]*call|telefonkonferenz|\bcall\b|recap|trans\w{0,3}ipt|roadshow|capital[\s_-]*markets?[\s_-]*day"
    r"|\bcmd\b|analyst|investor[\s_-]*day|bilanzpressekonferenz", re.I)
_ESCLUDI_PDF = re.compile(
    r"presentation|presentazione|slides?\b|press|comunicato|governance|remunerat|compensation|sustainab|esg\b"
    r"|csr\b|non[\s_-]*financial|proxy|agenda|minutes|verbale|notice|avviso|convocazione|factsheet|fact[\s_-]*sheet"
    r"|transcript|webcast|dividend|bylaws|statuto|code[\s_-]*of|policy|procedura|prospectus|prospetto|tax\b"
    r"|pillar|green[\s_-]*bond|rating|letter|lettera|glance|sintesi|piano|business[\s_-]*plan|climate|sdgs?\b"
    r"|slavery|informativa|codice|directions|ivass|scissione|buy[\s_-]*back|acquisto[\s_-]*azioni"
    r"|trans\w{0,3}ipt|information[\s_-]*form|\baif\b|circular|labou?r[\s_-]*report"
    r"|\bnews\b|newsletter|ad[\s_-]*hoc|einladung|nutzungsbedingungen|terms[\s_-]*of[\s_-]*use|stimmrecht"
    r"|voting[\s_-]*rights|key[\s_-]*figures|kennzahlen|summary|errata|corrigendum|auditor|pr[uü]fungsvermerk"
    r"|pension", re.I)
# Cartelle della sezione stampa: i PDF li' sono comunicati SULLA relazione, non la relazione
# (prova reale 05/10: «/Presse/News/…-Half-Yearly-Financial-Report-H1.pdf»). Non «media»:
# molti CMS tengono tutti i file sotto «/-/media/».
_PERCORSO_STAMPA = re.compile(
    r"/(?:presse?|press[\s_-]*releases?|pressemitteilungen|news(?:room)?|comunicati(?:[\s_-]*stampa)?|stampa"
    r"|sala[\s_-]*stampa|notizie|actualit[eé]s|noticias|communiqu[eé]s)(?=/)", re.I)
_TIPI_PDF = (
    ("trimestrale", re.compile(r"md\W?&?\W?a\b|mda\b|management.?s[\s_-]*discussion|quarter|trimestr|quartal"
                               r"|resoconto[\s_-]*intermedio|zwischenmitteilung"
                               r"|\bq[1-4]\b|[\s_-]q[1-4][\s_-]", re.I)),
    ("semestrale", re.compile(r"half[\s_-]*year|semestr|halbjahr|first[\s_-]*half|\bh1\b|interim|semi[\s_-]*annual"
                              r"|halfjaar", re.I)),
    ("annuale", re.compile(r"annual|annuale|annuel|bilancio|gesch(?:a|ä|ae)ftsbericht|rapport[\s_-]*annuel|jahresfinanz"
                           r"|registration[\s_-]*document|document[\s_-]*d.enregistrement|integrated[\s_-]*report"
                           r"|relazione[\s_-]*finanziaria|informe[\s_-]*anual|cuentas[\s_-]*anuales|jaarverslag"
                           r"|konzernabschluss|[aå]rsredovisning|vuosikertomus", re.I)),
)
# Parola da documento: senza, il nome e' generico («First Half 2026 results») e serve la prima pagina
_NOME_DOCUMENTO = re.compile(
    r"report|bericht|mitteilung|statement|relazione|resoconto|rapport|informe|bilancio|md\W?&?\W?a\b|mda\b"
    r"|management.?s[\s_-]*discussion|registration[\s_-]*document|document[\s_-]*d.enregistrement|jaarverslag"
    r"|konzernabschluss|accounts|comptes|cuentas|[aå]rsredovisning|vuosikertomus|financial[\s_-]*statements", re.I)
# (non «abschluss» da solo: il «Jahresabschluss» della sola capogruppo non e' la relazione consolidata;
# prova reale 05/10: tre PDF scaricati per la prima pagina senza motivo)
_INGLESE = re.compile(r"(?:^|[\W_])(?:en|eng|english)(?:[\W_]|$)", re.I)
_TITOLO_INGLESE = re.compile(r"annual[\s_-]*report|half[\s_-]*year|quarterly|financial[\s_-]*report|interim[\s_-]*report"
                             r"|financial[\s_-]*statements", re.I)
_ANNO = re.compile(r"(?<!\d)(20[0-4]\d)(?!\d)")
_DATA_DMY = re.compile(r"(?<!\d)(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])(20[0-4]\d)(?!\d)")  # 30062026
_DATA_DMY_SEP = re.compile(r"(?<!\d)(0?[1-9]|[12]\d|3[01])[._-](0?[1-9]|1[0-2])[._-](20[0-4]\d)(?!\d)")  # 23.05.2025
_DATA = re.compile(r"(?<!\d)(20[0-4]\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
_MESI = {m: i + 1 for i, nomi in enumerate((
    "january gennaio januar janvier enero", "february febbraio februar fevrier febrero",
    "march marzo marz maerz mars", "april aprile avril abril", "may maggio mai mayo", "june giugno juni juin junio",
    "july luglio juli juillet julio", "august agosto aout", "september settembre septembre septiembre",
    "october ottobre oktober octobre octubre", "november novembre noviembre",
    "december dicembre dezember decembre diciembre")) for m in nomi.split()}
_ALTERNATIVA_MESI = "|".join(sorted(_MESI, key=len, reverse=True))
_DATA_MESE = re.compile(r"(?<!\d)([0-3]?\d)[\s_.-]*(?:de[\s_]+)?(" + _ALTERNATIVA_MESI
                        + r")[\s_.-]*(?:de[\s_]+)?(20[0-4]\d)(?!\d)", re.I)  # anche «31march2026» (prova reale)
_DATA_MESE_US = re.compile(r"(?<![a-z])(" + _ALTERNATIVA_MESI + r")[\s_.-]*([0-3]?\d)(?:st|nd|rd|th)?,?[\s_.-]*"
                           r"(20[0-4]\d)(?!\d)", re.I)  # «June 30, 2026»
_TRIMESTRE_AA = re.compile(r"(?<![a-z0-9])q([1-4])[\s_-]?(\d{2})(?!\d)", re.I)  # «Q226» = Q2 2026 (prova reale)
_TRIMESTRE = re.compile(r"\bq([1-4])\b|[\s_-]q([1-4])[\s_-]", re.I)
_FINE_TRIMESTRE = ("03-31", "06-30", "09-30", "12-31")


def _senza_accenti(testo):
    return (testo.lower().replace("ä", "a").replace("é", "e").replace("û", "u").replace("è", "e")
            .replace("ü", "u").replace("ö", "o"))


def _fine_mese(anno, mese, giorno):
    try:
        return int(giorno) == calendar.monthrange(int(anno), int(mese))[1]
    except (ValueError, calendar.IllegalMonthError):
        return False


def _date_nel_testo(testo):
    """[(anno, mese, giorno, inizio)] delle date scritte nel testo (mese a parole, AAAA-MM-GG,
    GG.MM.AAAA, GGMMAAAA), nell'ordine di priorita' della prova reale."""
    t = _senza_accenti(testo)
    out = []
    for m in _DATA_MESE.finditer(t):
        out.append((int(m.group(3)), _MESI[m.group(2).lower()], int(m.group(1)), m.start()))
    for m in _DATA_MESE_US.finditer(t):
        out.append((int(m.group(3)), _MESI[m.group(1).lower()], int(m.group(2)), m.start()))
    numeriche = list(_DATA.finditer(t))
    for m in reversed(numeriche):  # l'ultima AAAA-MM-GG prima (in testa c'e' di solito la pubblicazione)
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), m.start()))
    for m in _DATA_DMY_SEP.finditer(t):
        out.append((int(m.group(3)), int(m.group(2)), int(m.group(1)), m.start()))
    if not numeriche:
        for m in _DATA_DMY.finditer(t):
            out.append((int(m.group(3)), int(m.group(2)), int(m.group(1)), m.start()))
    return out


def _periodo_dal_nome(nome, testo, tipo):
    """(periodo, base, tipo) dal nome del file e dal testo del link; periodo None se non si capisce.

    Una data a fine TRIMESTRE e' la chiusura del periodo, purche' il suo anno non superi gli altri anni
    del nome; ogni altra data e' la pubblicazione (prova reale: «2026-07-02-…-Q2-…», «…_23.05.2025.pdf»
    sul bilancio 2024; revisione R-8 C5: «…-2026_31.07.2026», «Annual-Report-2025_2026-03-31») e il
    suo anno non conta come anno del periodo."""
    date_nome = _date_nel_testo(nome)
    anni_nome = [int(a) for a in _ANNO.findall(nome)]

    def chiusura(d):
        if not _fine_mese(*d[:3]) or d[1] not in (3, 6, 9, 12):
            return False
        altri = list(anni_nome)
        if d[0] in altri:
            altri.remove(d[0])
        for x in date_nome:  # gli anni delle altre date (pubblicazioni) non contano
            if x is not d and x[0] in altri:
                altri.remove(x[0])
        return not altri or d[0] <= max(altri)
    chiusure = [d for d in date_nome if chiusura(d)]
    if chiusure:
        a, m, g, _ = chiusure[0]
        return f"{a}-{m:02d}-{g:02d}", "data di chiusura nel nome del file", tipo
    nota = (" (data a fine mese nel nome trattata come pubblicazione)"
            if any(_fine_mese(*d[:3]) for d in date_nome) else "")
    if _TRIMESTRE_AA.search(nome) and not _ANNO.search(nome):
        q = _TRIMESTRE_AA.search(nome)
        tipo = "trimestrale" if tipo != "annuale" or q.group(1) != "4" else tipo
        return f"20{q.group(2)}-{_FINE_TRIMESTRE[int(q.group(1)) - 1]}", "trimestre e anno nel nome del file", tipo
    anni = [int(a) for a in _ANNO.findall(testo)]
    for a, _, _, _ in date_nome:  # anni delle date di pubblicazione: tolti una volta ciascuno
        if a in anni:
            anni.remove(a)
    if not anni:
        return None, None, tipo
    anno = max(anni)
    q = _TRIMESTRE.search(" " + testo + " ")
    if tipo == "trimestrale" and q:
        return (f"{anno}-{_FINE_TRIMESTRE[int(q.group(1) or q.group(2)) - 1]}",
                "trimestre e anno nel nome o nel titolo", tipo)
    if tipo == "trimestrale":
        return None, None, tipo  # trimestre ignoto: l'anno da solo non basta
    if tipo == "semestrale":
        return f"{anno}-06-30", "anno nel nome o nel titolo (chiusura del semestre al 30/06 presunta)" + nota, tipo
    return f"{anno}-12-31", "anno nel nome o nel titolo (chiusura dell'esercizio al 31/12 presunta)" + nota, tipo


def _periodo_dalla_pagina(testa, tipo):
    """(periodo, base) dal testo della prima pagina: la piu' recente data a fine mese, altrimenti
    «<titolo del tipo> AAAA» / «AAAA <titolo>»; (None, None) se non si capisce."""
    chiusure = [d for d in _date_nel_testo(testa) if _fine_mese(*d[:3])]
    if chiusure:
        a, m, g, _ = max(chiusure)
        return f"{a}-{m:02d}-{g:02d}", "data di chiusura nella prima pagina"
    rx = dict(_TIPI_PDF)[tipo]
    for m in rx.finditer(testa):
        dopo = _ANNO.search(testa[m.end():m.end() + 40])
        prima = _ANNO.search(testa[max(0, m.start() - 8):m.start()])
        trovato = dopo or prima
        if trovato and not re.search(r"\d", testa[m.end():m.end() + dopo.start()] if dopo else ""):
            anno = int(trovato.group(1))
            if tipo == "trimestrale":
                q = _TRIMESTRE.search(" " + testa + " ")
                if not q:
                    return None, None
                return f"{anno}-{_FINE_TRIMESTRE[int(q.group(1) or q.group(2)) - 1]}", "trimestre e anno nella prima pagina"
            fine = "06-30" if tipo == "semestrale" else "12-31"
            return f"{anno}-{fine}", f"titolo e anno nella prima pagina (chiusura al {fine[3:]}/{fine[:2]} presunta)"
    return None, None


def classifica_pdf(voce, *, prima_pagina=None, dominio=None):
    """Esito DICHIARATO di un link a PDF del sito: {"url", "testo", "ammesso", "tipo", "periodo",
    "base_periodo", "motivo", "serve_prima_pagina", "origine", "etichetta"}.

    Riconoscimento su nome del file, testo del link e (se letta) prima pagina: `prima_pagina` =
    testo, oppure la voce di cache {"testo"} / {"errore"}. Presentazioni e slide per investitori
    escluse col motivo; senza periodo di riferimento il documento non e' ammesso (motivo scritto)."""
    url = voce["url"]
    percorso = unquote(urlsplit(url).path)
    nome = percorso.rsplit("/", 1)[-1]
    testo = f"{nome} {voce.get('testo') or ''}".replace("_", " ")
    out = {"url": url, "testo": (voce.get("testo") or nome)[:160], "ammesso": False, "tipo": None, "periodo": None,
           "base_periodo": None, "motivo": None, "serve_prima_pagina": False,
           "origine": ORIGINE_SITO, "etichetta": ETICHETTA_SITO}

    def scarto(motivo, **altro):
        out.update(motivo=motivo, **altro)
        return out
    if dominio and not stesso_dominio(url, dominio):
        # revisione R-8 C2: solo PDF del dominio dell'emittente (sottodomini compresi)
        return scarto(f"PDF su un altro dominio ({urlsplit(url).hostname}): non e' il sito dell'emittente")
    m = _PRESENTAZIONE.search(testo)
    if m:
        return scarto(f"presentazione/slide per investitori («{m.group(0)}»): esclusa")
    m = _ESCLUDI_PDF.search(testo)
    if m:
        return scarto(f"non e' una relazione periodica («{m.group(0)}»)")
    m = _PERCORSO_STAMPA.search(percorso.rsplit("/", 1)[0] + "/")
    if m:
        return scarto(f"nella sezione stampa del sito («{m.group(0).strip('/')}»): comunicato, non la relazione")
    tipo = next((t for t, rx in _TIPI_PDF if rx.search(testo)), None)
    generico = not _NOME_DOCUMENTO.search(testo)
    if tipo is None and generico:
        return scarto("non riconosciuto come relazione periodica (nome del file e titolo)")
    pagina = prima_pagina.get("testo") if isinstance(prima_pagina, dict) else prima_pagina
    errore = prima_pagina.get("errore") if isinstance(prima_pagina, dict) else None
    testa = " ".join(str(pagina).split())[:2000] if pagina is not None else None
    if tipo is None:
        # «Consolidated Financial Report 2026» (prova reale): documento, ma di che periodo? lo dice la copertina
        perche = "nome da documento ma senza il tipo di relazione"
        if testa is None:
            if errore:
                return scarto(f"{perche}; prima pagina non letta ({errore}): non ammesso")
            return scarto(f"{perche}; prima pagina non letta: non ammesso", serve_prima_pagina=True)
        m = _PRESENTAZIONE.search(testa)
        if m:
            return scarto(f"prima pagina: presentazione/slide per investitori («{m.group(0)}»): esclusa")
        tipo = next((t for t, rx in _TIPI_PDF if rx.search(testa)), None)
        if tipo is None:
            return scarto("prima pagina senza il tipo di relazione periodica: non ammesso")
        periodo, base = _periodo_dalla_pagina(testa, tipo)
        if periodo is None:
            return scarto("periodo di riferimento non dichiarato nel nome, nel titolo ne' nella prima pagina: "
                          "non ammesso", tipo=tipo)
        generico = False  # tipo e periodo vengono dalla copertina
    else:
        periodo, base, tipo = _periodo_dal_nome(nome, testo, tipo)
    out["tipo"] = tipo
    if generico or periodo is None:
        perche = ("nome generico, senza una parola da relazione" if generico
                  else "periodo di riferimento non dichiarato nel nome ne' nel titolo")
        if testa is None:
            if errore:
                return scarto(f"{perche}; prima pagina non letta ({errore}): non ammesso")
            return scarto(f"{perche}; prima pagina non letta: non ammesso", serve_prima_pagina=True)
        m = _PRESENTAZIONE.search(testa)
        if m:
            return scarto(f"prima pagina: presentazione/slide per investitori («{m.group(0)}»): esclusa")
        if generico and not (_NOME_DOCUMENTO.search(testa) and dict(_TIPI_PDF)[tipo].search(testa)):
            return scarto("nome generico e prima pagina senza titolo da relazione periodica: non ammesso")
        if periodo is None:
            periodo, base = _periodo_dalla_pagina(testa, tipo)
            if periodo is None:
                return scarto("periodo di riferimento non dichiarato nel nome, nel titolo ne' nella prima pagina: "
                              "non ammesso")
        else:
            base += "; titolo da relazione confermato dalla prima pagina"
    try:
        date.fromisoformat(periodo)
    except ValueError:
        return scarto(f"periodo di riferimento non valido ({periodo}): non ammesso")
    if (tipo == "semestrale" and periodo[5:7] in ("03", "09")
            and not re.search(r"half|semestr|halbjahr|\bh1\b", testo, re.I)):
        tipo = "trimestrale"  # «interim report at 31 March»: un trimestre (prova reale)
    out.update(ammesso=True, tipo=tipo, periodo=periodo, base_periodo=base)
    return out


def _documento_pdf(voce, prima_pagina=None):
    """{"url", "testo", "tipo", "periodo", ...} di un link a PDF IR, None se non e' una relazione
    ammessa (il motivo dello scarto lo da' classifica_pdf)."""
    esito = classifica_pdf(voce, prima_pagina=prima_pagina)
    return esito if esito["ammesso"] else None


def _parole_nome(url):
    nome = unquote(urlsplit(url).path).rsplit("/", 1)[-1].lower()
    return {w for w in re.findall(r"[a-z]{2,}", nome) if w not in ("pdf", "final", "vf", "en", "de", "it", "fr", "v")}


def _in_inglese(url):
    nome = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    return bool(_INGLESE.search(nome) or _TITOLO_INGLESE.search(nome.replace("_", " ")))


def scegli_pdf(pdf, *, oggi=None, prime_pagine=None, dominio=None, dopo=None):
    """Dai link a PDF trovati: il documento periodico piu' recente e l'omologo dell'anno prima
    (stesso tipo, periodo un anno prima ±20 giorni). None se nessuno e' una relazione ammessa.
    `dominio`: solo PDF del sito dell'emittente (gli altri scartati col motivo); `dopo`: solo
    relazioni con periodo successivo a quella data come «ultimo» (None se non ce ne sono).

    Ogni documento e la scelta portano l'origine dichiarata (ETICHETTA_SITO); `scartati` dice
    perche' gli altri PDF non sono stati ammessi (presentazioni, periodo ignoto, ...)."""
    oggi = oggi or date.today()
    prime_pagine = prime_pagine if isinstance(prime_pagine, dict) else {}
    esiti = [classifica_pdf(v, prima_pagina=prime_pagine.get(v["url"]), dominio=dominio)
             for v in pdf or [] if isinstance(v, dict) and v.get("url")]
    docs, scartati = [], []
    for e in esiti:
        if e["ammesso"] and date.fromisoformat(e["periodo"]) > oggi:
            e = {**e, "ammesso": False, "motivo": f"periodo {e['periodo']} nel futuro: non ammesso"}
        (docs if e["ammesso"] else scartati).append(e)
    if not docs:
        return None
    ordine = {"annuale": 0, "semestrale": 1, "trimestrale": 2}  # a parita' di periodo: l'annuale

    def omologo(doc):
        fine = date.fromisoformat(doc["periodo"])
        try:
            attesa = fine.replace(year=fine.year - 1)
        except ValueError:  # 29 febbraio
            attesa = fine - timedelta(days=365)
        simili = [d for d in docs if d["tipo"] == doc["tipo"] and d["url"] != doc["url"]
                  and abs((date.fromisoformat(d["periodo"]) - attesa).days) <= 20]
        # stesso tipo di documento: il nome piu' simile (MD&A con MD&A, non col bilancio)
        return max(simili, key=lambda d: (len(_parole_nome(d["url"]) & _parole_nome(doc["url"])),
                                          _in_inglese(d["url"]), d["periodo"])) if simili else None

    chiave = lambda d: (d["periodo"], -ordine[d["tipo"]], _in_inglese(d["url"]))
    # prima un documento recente CON l'omologo dell'anno prima (serve la coppia), altrimenti il piu' recente
    scelti = [d for d in docs if not dopo or d["periodo"] > str(dopo)]
    if not scelti:
        return None  # nessuna relazione piu' recente di `dopo`: il chiamante lo dichiara
    con_coppia = [d for d in scelti if omologo(d) and date.fromisoformat(d["periodo"]) >= oggi - timedelta(days=550)]
    ultimo = max(con_coppia or scelti, key=chiave)
    precedente = omologo(ultimo)
    return {"tipo": ultimo["tipo"], "ultimo": ultimo, "precedente": precedente, "candidati": len(docs),
            "origine": ORIGINE_SITO, "etichetta": ETICHETTA_SITO, "etichetta_en": ETICHETTA_SITO_EN,
            "deposito_ufficiale": False,
            "scartati": [{"url": e["url"], "motivo": e["motivo"]} for e in scartati[:MAX_SCARTATI]],
            "scartati_totale": len(scartati)}


def riepilogo_scarti(pdf, *, oggi=None, prime_pagine=None, dominio=None):
    """Frase dichiarata sui PDF del sito quando nessuno e' ammesso: quanti e perche' (motivi raggruppati)."""
    prime_pagine = prime_pagine if isinstance(prime_pagine, dict) else {}
    esiti = [classifica_pdf(v, prima_pagina=prime_pagine.get(v["url"]), dominio=dominio)
             for v in pdf or [] if isinstance(v, dict) and v.get("url")]
    conta = {}
    for e in esiti:
        if not e["ammesso"]:
            gruppo = re.sub(r"\s*\(«[^»]*»\)|\s*\([^)]*\)", "", e["motivo"] or "")[:90]
            conta[gruppo] = conta.get(gruppo, 0) + 1
    gruppi = "; ".join(f"{n} {g}" for g, n in sorted(conta.items(), key=lambda x: -x[1])[:3])
    return (f"nessuna relazione periodica ammessa tra {len(esiti)} PDF del {ETICHETTA_SITO}"
            + (f": {gruppi}" if gruppi else ""))


def da_leggere_prima_pagina(pdf, dominio, *, limite=MAX_PRIME_PAGINE):
    """URL dei PDF (stesso dominio) a cui manca solo la prima pagina per decidere: i piu' recenti
    per anno nel nome, al massimo `limite` (gli altri restano scartati col motivo)."""
    candidati = []
    for v in pdf or []:
        if not isinstance(v, dict) or not v.get("url") or not stesso_dominio(v["url"], dominio):
            continue
        if classifica_pdf(v)["serve_prima_pagina"]:
            anni = [int(a) for a in _ANNO.findall(unquote(v["url"]) + " " + str(v.get("testo") or ""))]
            candidati.append((max(anni, default=0), v["url"]))
    candidati.sort(key=lambda x: -x[0])
    return [u for _, u in candidati[:limite]]


def dominio_sito(sito):
    """Dominio registrabile del sito dell'emittente, None se il sito non e' noto."""
    return dominio_registrabile(urlsplit(str(sito or "")).hostname) if sito else None


def pdf_trovati(ticker, *, cache_dir=None, oggi=None):
    """PDF IR scelti dalla cache dell'ultima esplorazione (nessuna rete), con la data e l'origine
    dichiarata («sito dell'emittente, non archivio ufficiale»)."""
    voce = _leggi_json(_cache_dir(cache_dir) / _nome_file(ticker))
    if not voce:
        return None
    scelta = scegli_pdf(voce.get("pdf"), oggi=oggi, prime_pagine=voce.get("prime_pagine"),
                        dominio=dominio_sito(voce.get("sito")))
    return ({**scelta, "at": voce.get("at"), "sito": voce.get("sito"), "accesso": voce.get("accesso")}
            if scelta else None)


def impronta_sito(profilo, *, nav=None, head_fn=None):
    """Impronta leggera dei PDF di un profilo «sito dell'emittente» (revisione R-8 C4): una HEAD per
    ir_url, robots.txt rispettato, nel ritmo del Navigatore, nessun redirect seguito, IP pubblico.
    Firma = ETag, Last-Modified, Content-Length. Se una HEAD e' vietata, bloccata, fallisce o il sito
    non da' nessuna delle tre intestazioni: impronta NON confrontabile col motivo (run completo, mai un
    controllo saltato in silenzio). Forma: {"fonte": "sito", "varianti": {tipo: {"relazione": firma}}}."""
    tipo = profilo.get("tipo") or "?"
    urls = [u for u in profilo.get("ir_urls") or [] if isinstance(u, str)]

    def non_confrontabile(motivo):
        return {"fonte": "sito", "non_confrontabile": True, "motivo": motivo, "varianti": {}}
    if not urls:
        return non_confrontabile("profilo senza documenti del sito")
    firme = []
    for url in urls:
        host = (urlsplit(url).hostname or "").lower()
        if nav is None:
            nav = Navigatore(dominio_registrabile(host) or host)
        try:
            if not nav.consentito(url):
                stato, ignoto = nav.bloccato.get(host), nav.robots_ignoto.get(host)
                return non_confrontabile(f"{frase_blocco(stato)} su robots.txt" if stato else
                                         f"robots.txt non leggibile ({ignoto})" if ignoto else
                                         f"robots.txt vieta {urlsplit(url).path[:80]}")
            if nav.richieste:
                nav._dormi(nav.pausa)
            nav.richieste += 1
            if head_fn is None:
                import requests
                from bellomberg.market_data.lettore_trimestrali import UA, _richiedi_indirizzi_pubblici
                if urlsplit(url).scheme != "https" or not stesso_dominio(url, nav.dominio):
                    return non_confrontabile(f"documento fuori dal dominio del sito ({host})")
                _richiedi_indirizzi_pubblici(host, urlsplit(url).port or 443)
                r = requests.head(url, headers={"User-Agent": UA}, timeout=TIMEOUT_S, allow_redirects=False)
            else:
                r = head_fn(url)
        except Exception as exc:
            return non_confrontabile(f"HEAD non riuscita ({motivo_eccezione(exc, 120)})")
        if r.status_code in STATI_BLOCCO:
            return non_confrontabile(f"{frase_blocco(r.status_code)} sulla HEAD")
        if r.status_code != 200:
            return non_confrontabile(f"HEAD: HTTP {r.status_code}")
        h = r.headers or {}
        firma = [h.get("ETag"), h.get("Last-Modified"), h.get("Content-Length")]
        if not any(firma):
            return non_confrontabile("il sito non da' ETag, Last-Modified ne' Content-Length: nessun controllo leggero")
        firme.append([url] + firma)
    digest = hashlib.sha256(json.dumps(firme, sort_keys=True).encode("utf-8")).hexdigest()
    return {"fonte": "sito", "varianti": {tipo: {"relazione": digest}}, "firme": firme}


def scopri_senza_fonte(tickers, *, oggi=None, consigliere_fn=None, scopri_fn=None, scadenza=None):
    """Controllo giornaliero e attivazione: esplora i siti dei titoli «senza fonte» (cache
    CACHE_GIORNI). Nessun PDF scaricato: la stima resta un clic dell'utente."""
    esiti = []
    for t in tickers:
        motivo = _fermati(consigliere_fn, scadenza)
        if motivo:
            esiti.append({"ticker": None, "rinviata": True, "motivi": [motivo]})
            break
        try:
            trovato = (scopri_fn or scopri)(t, oggi=oggi)
            scelta = scegli_pdf(trovato.get("pdf"), oggi=oggi, prime_pagine=trovato.get("prime_pagine"),
                                dominio=dominio_sito(trovato.get("sito")))
            esito = {"ticker": t, "pdf": bool(scelta), "motivi": (trovato.get("motivi") or [])[:5]}
            if trovato.get("accesso"):
                esito["accesso"] = trovato["accesso"]
            esiti.append(esito)
        except Exception as exc:
            esiti.append({"ticker": t, "pdf": False, "motivi": [motivo_eccezione(exc)]})
    return esiti


def scopri_giornaliero(store, *, escludi=(), oggi=None, consigliere_fn=None, pref_fn=None):
    """Passo del controllo giornaliero (e del giro dopo un'attivazione): siti dei profili ESEF
    fermi e dei titoli rimasti «senza fonte» all'ultima attivazione. Mai nella run del Consigliere."""
    from bellomberg.storage import filing_preferenze
    scadenza = time.monotonic() + TEMPO_GIRO_S
    esiti = scopri_profili(store, oggi=oggi, consigliere_fn=consigliere_fn, escludi=escludi, scadenza=scadenza)
    if any(e.get("rinviata") for e in esiti):
        return esiti
    # Profili creati dal sito dell'emittente (V8B 05/10): una relazione piu' recente sul sito (es. la
    # semestrale dopo l'annuale) diventa una nuova versione del profilo, verificata, prima dei run.
    for riga in store.list_profiles():
        if (not riga.get("enabled") or riga.get("ticker") in escludi
                or (riga.get("profile") or {}).get("origine_collegamento") != "sito_emittente"):
            continue
        motivo = _fermati(consigliere_fn, scadenza)
        if motivo:
            return esiti + [{"ticker": None, "rinviata": True, "motivi": [motivo]}]
        try:
            from bellomberg.market_data.filing_attivazione import aggiorna_dal_sito
            esiti.append(aggiorna_dal_sito(store, riga["ticker"]))
        except Exception as exc:  # un sito non ferma gli altri
            esiti.append({"ticker": riga["ticker"], "esito": "errore", "motivi": [motivo_eccezione(exc)]})
    try:
        pref = (pref_fn or filing_preferenze.carica)()
    except ValueError:
        return esiti  # preferenze illeggibili: solo i profili
    con_profilo = {r["ticker"] for r in store.list_profiles() if r.get("enabled")}
    senza = [t for t, e in sorted(filing_preferenze.esiti(pref).items())
             if (e or {}).get("esito") == "senza_fonte" and t not in con_profilo and t not in escludi]
    return esiti + scopri_senza_fonte(senza, oggi=oggi, consigliere_fn=consigliere_fn, scadenza=scadenza)
