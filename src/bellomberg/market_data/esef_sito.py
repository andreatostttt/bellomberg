"""Fonti dal sito dell'emittente (fase F): pacchetti ESEF ufficiali e PDF IR trovati da soli.

Rete gentile e limitata: solo HTTPS, solo il dominio registrabile del sito della società
(sottodomini ammessi), robots.txt rispettato, al massimo MAX_PAGINE pagine HTML per titolo e
profondita' MAX_PROFONDITA, pausa tra le richieste, pagine oltre MAX_PAGINA_BYTES scartate,
nessun JavaScript. L'esplorazione parte solo dal controllo giornaliero, dall'attivazione o da
una richiesta esplicita, mai dalla run del Consigliere: pipeline e impronta leggono la cache
(CACHE_GIORNI). Il pacchetto si scarica (max 80 MB) e si converte in locale (ixbrl_oim); LEI e
periodo si verificano sui fatti come per il repository (filing_esef.documento_esef).
"""
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
        dichiarato = r.headers.get("Content-Length")
        if dichiarato and str(dichiarato).isdigit() and int(dichiarato) > MAX_PAGINA_BYTES:
            raise ValueError("pagina troppo grande")
        if getattr(r, "raw", None) is not None:
            pezzi, n = [], 0
            limite = min(self._orologio() + TEMPO_PAGINA_S, self._scadenza)
            for pezzo in r.iter_content(64 * 1024):
                n += len(pezzo)
                if n > MAX_PAGINA_BYTES:
                    raise ValueError("pagina troppo grande")
                if self._orologio() > limite:  # server lento a gocce (revisione finale)
                    r.close()
                    raise TimeoutError("pagina troppo lenta")
                pezzi.append(pezzo)
            r.close()
            grezzo = b"".join(pezzi)
        else:
            grezzo = r.content or b""
            if len(grezzo) > MAX_PAGINA_BYTES:
                raise ValueError("pagina troppo grande")
        return grezzo.decode(r.encoding or "utf-8", errors="replace")

    def consentito(self, url):
        """robots.txt dell'host (letto una volta per host; assente o illeggibile: consentito)."""
        host = urlsplit(url).hostname
        if self._robots is None:
            self._robots = {}
        if host not in self._robots:
            rp = RobotFileParser()
            try:
                _, r = self._http(f"https://{host}/robots.txt")
                rp.parse(self._corpo(r).splitlines() if r.status_code == 200 else [])
            except Exception:
                rp.parse([])
            self._robots[host] = rp
        from bellomberg.market_data.lettore_trimestrali import UA
        return self._robots[host].can_fetch(UA, url)

    def pagina(self, url):
        """(url finale, html) di una pagina HTML; eccezione se non e' HTML o va fuori dominio."""
        finale, r = self._http(url)
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        tipo = (r.headers.get("Content-Type") or "").lower()
        if tipo and "html" not in tipo:
            raise ValueError(f"non HTML ({tipo[:40]})")
        return finale, self._corpo(r)


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
    while coda and len(pagine) < max_pagine:
        coda.sort(key=lambda x: (-x[1], x[0]))
        prof, punti_pagina, url = coda.pop(0)
        chiave = _normalizza(url)
        if chiave in visti:
            continue
        visti.add(chiave)
        try:
            if not nav.consentito(url):
                motivi.append(f"robots.txt esclude {urlsplit(url).path[:80]}")
                continue
            finale, html = nav.pagina(url)
        except Exception as exc:
            motivi.append(f"{urlsplit(url).path[:80] or '/'}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
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
    return {"pagine": pagine, "link": link, "motivi": motivi, "richieste": nav.richieste}


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


def scopri(ticker, *, lei=None, oggi=None, forza=False, cache_dir=None, sito_fn=None, navigatore_fn=None):
    """Esplora il sito di `ticker` (cache CACHE_GIORNI) e salva pacchetti e link utili.

    Esito: {"ticker", "at", "sito", "pagine", "pacchetti", "pdf", "motivi"}; `pdf` = link a
    PDF con testo (per la ricerca dei PDF IR). Chiamare solo fuori dalla run del Consigliere."""
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
        if lei and not any(p["lei"] == str(lei).upper() for p in esito["pacchetti"]):
            esito["motivi"].append(f"nessun pacchetto ESEF col LEI {str(lei).upper()} nelle pagine visitate")
    _scrivi_json(path, esito)
    return esito


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
    """GET GLEIF nel ritmo, UA con il contatto (SEC_CONTACT_EMAIL, come ESEF) e tetto di byte
    letto a flusso (REV_G2a R-3/R14: prima nessun ritmo, UA senza contatto, corpo senza tetto)."""
    import requests
    from bellomberg.market_data import esef
    ua = esef._headers()["User-Agent"]  # ContattoMancante se manca il contatto: dichiarato dal chiamante
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
    for p in psutil.process_iter(["cmdline"]):
        try:
            riga = " ".join(p.info.get("cmdline") or [])
            if "consigliere_multi" in riga or "bellomberg-committee" in riga:
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
            esiti.append({"ticker": ticker, "pacchetti": 0, "motivi": [f"{type(exc).__name__}: {str(exc)[:160]}"]})
    return esiti


# ------------------------------------------------------------------ PDF IR (Task 5)
_ESCLUDI_PDF = re.compile(
    r"presentation|presentazione|slides?\b|press|comunicato|governance|remunerat|compensation|sustainab|esg\b"
    r"|csr\b|non[\s_-]*financial|proxy|agenda|minutes|verbale|notice|avviso|convocazione|factsheet|fact[\s_-]*sheet"
    r"|transcript|webcast|dividend|bylaws|statuto|code[\s_-]*of|policy|procedura|prospectus|prospetto|tax\b"
    r"|pillar|green[\s_-]*bond|rating|letter|lettera|glance|sintesi|piano|business[\s_-]*plan|climate|sdgs?\b"
    r"|slavery|informativa|codice|directions|ivass|scissione|buy[\s_-]*back|acquisto[\s_-]*azioni"
    r"|trans\w{0,3}ipt|information[\s_-]*form|\baif\b|circular|labou?r[\s_-]*report", re.I)
_TIPI_PDF = (
    ("trimestrale", re.compile(r"md\W?&?\W?a\b|mda\b|management.?s[\s_-]*discussion|quarter|trimestr|quartal"
                               r"|resoconto[\s_-]*intermedio|zwischenmitteilung"
                               r"|\bq[1-4]\b|[\s_-]q[1-4][\s_-]", re.I)),
    ("semestrale", re.compile(r"half[\s_-]*year|semestr|halbjahr|first[\s_-]*half|\bh1\b|interim", re.I)),
    ("annuale", re.compile(r"annual|annuale|bilancio|gesch[aä]ftsbericht|rapport[\s_-]*annuel|jahresfinanz"
                           r"|registration[\s_-]*document|document[\s_-]*d.enregistrement|integrated[\s_-]*report"
                           r"|relazione[\s_-]*finanziaria", re.I)),
)
_ANNO = re.compile(r"(?<!\d)(20[0-4]\d)(?!\d)")
_DATA_DMY = re.compile(r"(?<!\d)(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])(20[0-4]\d)(?!\d)")  # 30062026
_DATA = re.compile(r"(?<!\d)(20[0-4]\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
_MESI = {m: i + 1 for i, nomi in enumerate((
    "january gennaio januar janvier", "february febbraio februar fevrier", "march marzo marz maerz mars",
    "april aprile avril", "may maggio mai", "june giugno juni juin", "july luglio juli juillet",
    "august agosto aout", "september settembre septembre", "october ottobre oktober octobre",
    "november novembre", "december dicembre dezember decembre")) for m in nomi.split()}
_DATA_MESE = re.compile(r"(?<!\d)([0-3]?\d)[\s_.-]*(" + "|".join(sorted(_MESI, key=len, reverse=True))
                        + r")[\s_.-]*(20[0-4]\d)(?!\d)", re.I)  # anche «31march2026» (prova reale)
_TRIMESTRE_AA = re.compile(r"(?<![a-z0-9])q([1-4])[\s_-]?(\d{2})(?!\d)", re.I)  # «Q226» = Q2 2026 (prova reale)
_TRIMESTRE = re.compile(r"\bq([1-4])\b|[\s_-]q([1-4])[\s_-]", re.I)


def _documento_pdf(voce):
    """{"url", "testo", "tipo", "periodo"} di un link a PDF IR, None se non e' una relazione."""
    nome = unquote(urlsplit(voce["url"]).path.rsplit("/", 1)[-1])
    testo = f"{nome} {voce.get('testo') or ''}".replace("_", " ")
    if _ESCLUDI_PDF.search(testo):
        return None
    tipo = next((t for t, rx in _TIPI_PDF if rx.search(testo)), None)
    if tipo is None:
        return None
    # Prova reale («2026-05-06-…-report-31-march-2026»): la data in testa e' la
    # pubblicazione; vale prima quella col mese scritto, poi GGMMAAAA, poi l'ultima AAAA-MM-GG.
    mese = _DATA_MESE.search(nome.lower().replace("ä", "a").replace("é", "e").replace("û", "u"))
    dmy, numeriche = _DATA_DMY.search(nome), list(_DATA.finditer(nome))
    if mese:
        periodo = f"{mese.group(3)}-{_MESI[mese.group(2)]:02d}-{int(mese.group(1)):02d}"
    elif dmy and not numeriche:
        periodo = f"{dmy.group(3)}-{dmy.group(2)}-{dmy.group(1)}"
    elif numeriche:
        data = numeriche[-1]
        periodo = f"{data.group(1)}-{data.group(2)}-{data.group(3)}"
    elif _TRIMESTRE_AA.search(nome) and not _ANNO.search(nome):
        q = _TRIMESTRE_AA.search(nome)
        periodo = f"20{q.group(2)}-{('03-31', '06-30', '09-30', '12-31')[int(q.group(1)) - 1]}"
        tipo = "trimestrale" if tipo != "annuale" or q.group(1) != "4" else tipo
    else:
        anni = [int(a) for a in _ANNO.findall(testo)]
        if not anni:
            return None
        anno = max(anni)
        q = _TRIMESTRE.search(" " + testo + " ")
        if tipo == "trimestrale" and q:
            periodo = f"{anno}-{('03-31', '06-30', '09-30', '12-31')[int(q.group(1) or q.group(2)) - 1]}"
        else:
            periodo = f"{anno}-06-30" if tipo == "semestrale" else f"{anno}-12-31"
    try:
        date.fromisoformat(periodo)
    except ValueError:
        return None
    if (tipo == "semestrale" and periodo[5:7] in ("03", "09")
            and not re.search(r"half|semestr|halbjahr|\bh1\b", testo, re.I)):
        tipo = "trimestrale"  # «interim report at 31 March»: un trimestre (prova reale)
    return {"url": voce["url"], "testo": (voce.get("testo") or nome)[:160], "tipo": tipo, "periodo": periodo}


def _parole_nome(url):
    nome = unquote(urlsplit(url).path).rsplit("/", 1)[-1].lower()
    return {w for w in re.findall(r"[a-z]{2,}", nome) if w not in ("pdf", "final", "vf", "en", "de", "it", "fr", "v")}


def scegli_pdf(pdf, *, oggi=None):
    """Dai link a PDF trovati: il documento periodico piu' recente e l'omologo dell'anno prima
    (stesso tipo, periodo un anno prima ±20 giorni). None se nessuno sembra una relazione."""
    oggi = oggi or date.today()
    docs = [d for d in (_documento_pdf(v) for v in pdf or [] if isinstance(v, dict) and v.get("url")) if d]
    docs = [d for d in docs if date.fromisoformat(d["periodo"]) <= oggi]
    if not docs:
        return None
    ordine = {"annuale": 0, "semestrale": 1, "trimestrale": 2}  # a parita' di periodo: l'annuale
    inglese = re.compile(r"(?:^|[\W_])(?:en|eng|english)(?:[\W_]|$)", re.I)

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
                                          bool(inglese.search(d["url"])), d["periodo"])) if simili else None

    chiave = lambda d: (d["periodo"], -ordine[d["tipo"]], bool(inglese.search(unquote(d["url"]).rsplit("/", 1)[-1])))
    # prima un documento recente CON l'omologo dell'anno prima (serve la coppia), altrimenti il piu' recente
    con_coppia = [d for d in docs if omologo(d) and date.fromisoformat(d["periodo"]) >= oggi - timedelta(days=550)]
    ultimo = max(con_coppia or docs, key=chiave)
    precedente = omologo(ultimo)
    return {"tipo": ultimo["tipo"], "ultimo": ultimo, "precedente": precedente, "candidati": len(docs)}


def pdf_trovati(ticker, *, cache_dir=None, oggi=None):
    """PDF IR scelti dalla cache dell'ultima esplorazione (nessuna rete), con la data."""
    voce = _leggi_json(_cache_dir(cache_dir) / _nome_file(ticker))
    if not voce:
        return None
    scelta = scegli_pdf(voce.get("pdf"), oggi=oggi)
    return {**scelta, "at": voce.get("at"), "sito": voce.get("sito")} if scelta else None


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
            scelta = scegli_pdf(trovato.get("pdf"), oggi=oggi)
            esiti.append({"ticker": t, "pdf": bool(scelta), "motivi": (trovato.get("motivi") or [])[:5]})
        except Exception as exc:
            esiti.append({"ticker": t, "pdf": False, "motivi": [f"{type(exc).__name__}: {str(exc)[:160]}"]})
    return esiti


def scopri_giornaliero(store, *, escludi=(), oggi=None, consigliere_fn=None, pref_fn=None):
    """Passo del controllo giornaliero (e del giro dopo un'attivazione): siti dei profili ESEF
    fermi e dei titoli rimasti «senza fonte» all'ultima attivazione. Mai nella run del Consigliere."""
    from bellomberg.storage import filing_preferenze
    scadenza = time.monotonic() + TEMPO_GIRO_S
    esiti = scopri_profili(store, oggi=oggi, consigliere_fn=consigliere_fn, escludi=escludi, scadenza=scadenza)
    if any(e.get("rinviata") for e in esiti):
        return esiti
    try:
        pref = (pref_fn or filing_preferenze.carica)()
    except ValueError:
        return esiti  # preferenze illeggibili: solo i profili
    con_profilo = {r["ticker"] for r in store.list_profiles() if r.get("enabled")}
    senza = [t for t, e in sorted(filing_preferenze.esiti(pref).items())
             if (e or {}).get("esito") == "senza_fonte" and t not in con_profilo and t not in escludi]
    return esiti + scopri_senza_fonte(senza, oggi=oggi, consigliere_fn=consigliere_fn, scadenza=scadenza)
