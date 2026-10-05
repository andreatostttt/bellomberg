"""Bounded public document ingestion before a Trade Idea spending grant.

Browser inputs are source pointers and claims to verify, never filesystem paths,
source-plan approvals or instructions. Raw bytes and text are rechecked through
the existing primary-document collector before the worker can reuse them.
"""
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
import time
import unicodedata
from urllib.parse import parse_qsl, unquote, urldefrag, urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field

CONTRACT = "trade-idea-pm-documents/1"
RESEARCH_CONTRACT = "fundamentals-research-pm-documents/1"
MAX_DOCUMENTS = 4
MAX_BYTES = 16 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_TEXT = 800000
MAX_PAGES = 400
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15
DEADLINE_SECONDS = 45
CURATED_HOSTS = frozenset({"www.sec.gov", "sec.gov", "data.sec.gov", "www.ecb.europa.eu", "www.eba.europa.eu", "www.esma.europa.eu"})
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".test", ".invalid")
_CREDENTIAL_KEYS = re.compile(r"(?:token|secret|password|passwd|api[_-]?key|authorization|credential|signature|access[_-]?key|(?:^|[-_])(?:sig|jwt|bearer|auth|session|key)(?:$|[-_]))", re.I)


class PMDocumentSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=1, max_length=2048)
    published_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    publication_quote: str | None = Field(default=None, min_length=8, max_length=1600)
    report_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    report_date_quote: str | None = Field(default=None, min_length=8, max_length=1600)
    issuer_quote: str | None = Field(default=None, min_length=4, max_length=1600)
    title: str | None = Field(default=None, max_length=160)


def normalize_sources(values):
    if values is None:
        return []
    if not isinstance(values, (list, tuple)) or len(values) > MAX_DOCUMENTS:
        raise ValueError("At most four public document sources are permitted")
    sources = [PMDocumentSource.model_validate(value.model_dump() if isinstance(value, PMDocumentSource) else value).model_dump() for value in values]
    if len({row["url"] for row in sources}) != len(sources):
        raise ValueError("Duplicate public document source URL")
    return sources


class SourceIngestionError(ValueError):
    """Unverified claims remain visible, but cannot be admitted to a grant."""
    def __init__(self, reason, receipt):
        super().__init__(reason)
        self.receipt = receipt


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _archive_bytes(root, raw, suffix):
    root.mkdir(parents=True, exist_ok=True)
    digest = sha256(raw).hexdigest()
    path, staging = root / (digest + suffix), None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".document-", suffix=".tmp", delete=False) as handle:
            staging = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(staging, path)
        except FileExistsError:
            with path.open("rb") as handle:
                existing = handle.read(len(raw) + 1)
            if existing != raw:
                raise ValueError("Existing document archive bytes changed")
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return path, digest


def read_archived_document(path):
    """Bound raw reads by actual format; an HTML response cannot claim a PDF cap."""
    with Path(path).open('rb') as stream:
        prefix = stream.read(5)
        limit = MAX_PDF_BYTES if prefix == b'%PDF-' else MAX_BYTES
        raw = prefix + stream.read(limit + 1 - len(prefix))
    if len(raw) > limit:
        raise ValueError('Document archive exceeds the byte limit')
    return raw


def _host(url, *, website=False):
    if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("Public HTTPS document URL missing or invalid")
    if "\\" in url or not url.isascii():
        raise ValueError("Noncanonical public URL")
    parts = urlsplit(url)
    if (parts.scheme not in (("https", "http") if website else ("https",))
            or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443) and not (website and parts.scheme == "http" and parts.port == 80)
            or parts.fragment or not parts.hostname):
        raise ValueError("HTTPS without credentials, fragment or custom port required")
    host = parts.hostname
    if host != host.rstrip(".") or host in ("localhost",) or host.endswith(_LOCAL_SUFFIXES):
        raise ValueError("Local or noncanonical host forbidden")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or "." not in host or ".." in host:
        raise ValueError("A public DNS hostname is required")
    if any(not label or len(label) > 63 or label.startswith(("-", "xn--")) or label.endswith("-") for label in host.split(".")):
        raise ValueError("Noncanonical DNS labels forbidden")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Literal IP document URLs forbidden")
    if all(char.isdigit() or char == "." for char in host):
        raise ValueError("Numeric host document URLs forbidden")
    decoded = url
    for _ in range(3):
        decoded = unquote(decoded)
        if "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
            raise ValueError("Encoded control or backslash in public URL")
        if any(_CREDENTIAL_KEYS.search(key) for key, _value in parse_qsl(urlsplit(decoded).query, keep_blank_values=True)):
            raise ValueError("Credential or signed document URLs forbidden")
    if unquote(decoded) != decoded:
        raise ValueError("Excessive nested URL encoding forbidden")
    if any(segment in (".", "..") for segment in urlsplit(decoded).path.split("/")):
        raise ValueError("URL traversal segments forbidden")
    if any(_CREDENTIAL_KEYS.search(key) for key, _value in parse_qsl(parts.query, keep_blank_values=True)):
        raise ValueError("Credential or signed document URLs forbidden")
    return host


def allowed_publisher_host(url, issuer_website=None):
    host = _host(url)
    if host in CURATED_HOSTS:
        return {"host": host, "basis": "server_curated_public_authority"}
    if issuer_website:
        official = _host(issuer_website, website=True)
        root = official[4:] if official.startswith("www.") else official
        if root in {"com", "org", "net", "co.uk", "com.au", "co.jp", "github.io", "wordpress.com", "blogspot.com"}:
            raise ValueError("Issuer website does not identify an official publisher")
        if host == root or host.endswith("." + root):
            return {"host": host, "basis": "qualified_public_issuer_profile_website", "issuer_host": root}
    raise ValueError("Document host is neither a curated authority nor the verified issuer website")


_PUBLISHER_LINK_CONTRACT = "issuer-published-document-link/1"


class IssuerLinkNotObserved(ValueError):
    """The intact issuer page does not delegate this exact document URL."""


class _OfficialDocumentLinks(HTMLParser):
    """Only actual visible anchors delegate; scripts/base tags never do."""
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.urls, self.muted = base, set(), 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template", "ix:hidden"):
            self.muted += 1
        if tag == "a" and not self.muted:
            href = dict(attrs).get("href")
            if isinstance(href, str) and href.strip():
                self.urls.add(urldefrag(urljoin(self.base, href.strip()))[0])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template", "ix:hidden"):
            self.muted = max(0, self.muted - 1)


def publisher_link_proof(url, navigation, *, issuer_website, document_root, binding):
    """Server-only exact-URL delegation from an already journaled issuer page."""
    body = {"contract": _PUBLISHER_LINK_CONTRACT, "url": url,
            "issuer_website": issuer_website, "navigation": deepcopy(navigation),
            "binding": deepcopy(binding)}
    proof = {**body, "sha256": _digest(body)}
    _publisher_policy(url, issuer_website, publisher_proof=proof, document_root=document_root)
    return proof


def _publisher_policy(url, issuer_website, *, publisher_proof=None, document_root=None,
                      identity=None, as_of=None):
    if publisher_proof is None:
        return allowed_publisher_host(url, issuer_website)
    proof = publisher_proof
    host = _host(url)  # Delegation never relaxes URL, credentials or SSRF rules.
    if (not isinstance(proof, dict)
            or set(proof) != {"contract", "url", "issuer_website", "navigation", "binding", "sha256"}
            or proof.get("contract") != _PUBLISHER_LINK_CONTRACT or proof.get("url") != url
            or not issuer_website or proof.get("issuer_website") != issuer_website
            or proof.get("sha256") != _digest({key: value for key, value in proof.items() if key != "sha256"})
            or document_root is None):
        raise ValueError("Exact issuer-link delegation is missing, changed or for another URL")
    binding, navigation = proof.get("binding"), proof.get("navigation")
    required = {"run_id", "ticker", "as_of", "identity_sha256", "parent_grant_fingerprint",
                "navigation_event_sha256", "session_sha256"}
    if (not isinstance(binding, dict) or set(binding) != required
            or any(not isinstance(binding.get(key), str) or not binding[key] for key in required)
            or any(not re.fullmatch(r"[0-9a-f]{64}", binding[key]) for key in required - {"run_id", "ticker", "as_of"})
            or date.fromisoformat(binding["as_of"]).isoformat() != binding["as_of"]
            or (identity is not None and (binding["ticker"] != identity.get("ticker")
                                         or binding["identity_sha256"] != _digest(identity)))
            or (as_of is not None and binding["as_of"] != as_of)):
        raise ValueError("Issuer-link delegation differs from the original research identity")
    keys = {"stato", "url", "url_finale", "path", "sha256", "bytes", "content_type", "publisher"}
    if not isinstance(navigation, dict) or set(navigation) != keys or navigation.get("stato") != "ok":
        raise ValueError("Original issuer navigation receipt is missing or malformed")
    policy = allowed_publisher_host(navigation["url"], issuer_website)
    if (policy.get("basis") != "qualified_public_issuer_profile_website"
            or navigation["publisher"] != policy
            or allowed_publisher_host(navigation["url_finale"], issuer_website)["host"] != policy["host"]
            or navigation["content_type"] not in ("text/html", "application/xhtml+xml")):
        raise ValueError("Delegation requires the exact verified issuer's own HTML page")
    path, root = Path(navigation["path"]).resolve(), Path(document_root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Issuer navigation receipt escaped the document archive")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if (not raw or len(raw) > MAX_BYTES or len(raw) != navigation["bytes"]
            or sha256(raw).hexdigest() != navigation["sha256"] or raw.startswith(b"%PDF-")):
        raise ValueError("Issuer navigation bytes differ from the accepted receipt")
    parser = _OfficialDocumentLinks(navigation["url_finale"])
    parser.feed(raw.decode("utf-8", errors="replace"))
    if url not in parser.urls:
        raise IssuerLinkNotObserved("Exact document URL is not an observed link in the verified issuer page")
    return {"host": host, "basis": "verified_exact_link_on_issuer_page",
            "issuer_host": policy["issuer_host"], "delegation_sha256": proof["sha256"],
            "navigation_sha256": navigation["sha256"]}


def _public_address(host, *, resolver=None):
    answers = (resolver or socket.getaddrinfo)(host, 443, type=socket.SOCK_STREAM)
    addresses = []
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise ValueError("Publisher DNS contains a nonpublic address")
        addresses.append(str(address))
    if not addresses:
        raise ValueError("Publisher DNS has no public addresses")
    return sorted(set(addresses), key=lambda value: (":" in value, value))[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        # Numeric address is fixed after DNS admission; TLS still verifies the
        # original hostname. No proxy, cookie jar, .netrc or implicit auth exists.
        raw = socket.create_connection((self._address, 443), timeout=self.timeout)
        try:
            peer = str(ipaddress.ip_address(raw.getpeername()[0].split("%", 1)[0]))
            if peer != self._address or not ipaddress.ip_address(peer).is_global:
                raise ValueError("Connected peer differs from the admitted public address")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def download_public_document(url, destination, *, issuer_website=None, resolver=None, connection_factory=None,
                             publisher_proof=None):
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    current, initial_host, visited = url, _host(url), set()
    started = time.monotonic()
    for redirect in range(MAX_REDIRECTS + 1):
        policy = _publisher_policy(current, issuer_website, publisher_proof=publisher_proof, document_root=root)
        if policy["host"] != initial_host or current in visited:
            raise ValueError("Cross-host or circular document redirect forbidden")
        visited.add(current)
        address = _public_address(policy["host"], resolver=resolver)
        headers = {"User-Agent": "Bellomberg-Public-Document/1.0", "Accept": "application/pdf,text/html,text/plain,application/xhtml+xml", "Accept-Encoding": "identity"}
        sec_edgar = None
        if policy["host"] in ("www.sec.gov", "sec.gov", "data.sec.gov"):
            from bellomberg.market_data import sec_edgar
            headers["User-Agent"] = sec_edgar._headers()["User-Agent"]
        connection = (connection_factory or _PinnedHTTPSConnection)(policy["host"], address, TIMEOUT_SECONDS)
        try:
            parts = urlsplit(current)
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            if sec_edgar is not None:
                # Ritmo SEC condiviso fra processi (<= 8 req/s in totale), a ogni GET e a ogni
                # salto di redirect: lo stesso di sec_edgar, non una copia. RitmoBloccato sale.
                sec_edgar.attendi_sec()
            connection.request("GET", target, headers=headers)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location or redirect == MAX_REDIRECTS:
                    raise ValueError("Document redirect missing or limit reached")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise ValueError("Public document HTTP status " + str(response.status) + "; no authentication attempted")
            if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ValueError("Compressed document responses forbidden")
            length = response.getheader("Content-Length")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            byte_limit = MAX_PDF_BYTES if content_type == 'application/pdf' else MAX_BYTES
            if length is not None and (not str(length).isdigit() or int(length) > byte_limit):
                raise ValueError("Document size exceeds the declared limit")
            chunks, size = [], 0
            while True:
                if time.monotonic() - started > DEADLINE_SECONDS:
                    raise ValueError("Public document acquisition deadline exceeded")
                chunk = response.read(min(65536, byte_limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if not chunks and byte_limit > MAX_BYTES and not chunk.startswith(b'%PDF-'):
                    raise ValueError('Declared PDF response has no PDF signature')
                if size > byte_limit:
                    raise ValueError("Document size exceeds the measured limit")
                chunks.append(chunk)
            raw = b"".join(chunks)
            if length is not None and len(raw) != int(length):
                raise ValueError("Document response differs from its declared byte length")
            if not raw:
                raise ValueError("Empty public document")
            if raw.startswith(b"%PDF-"):
                suffix = ".pdf"
            elif content_type in ("text/html", "application/xhtml+xml") and b"<" in raw[:2048]:
                suffix = ".html"
            elif content_type == "text/plain" and b"\x00" not in raw:
                suffix = ".txt"
            else:
                raise ValueError("Only extractable public PDF, HTML or plain text documents are supported")
            # Atomic immutable publication prevents a concurrent preflight from
            # reading a partially written file bearing the final content hash.
            path, digest = _archive_bytes(root, raw, suffix)
            return {"stato": "ok", "url": url, "url_finale": current, "path": str(path), "sha256": digest, "bytes": size, "content_type": content_type, "publisher": policy,
                    **({"publisher_proof": deepcopy(publisher_proof)} if publisher_proof is not None else {})}
        finally:
            connection.close()
    raise ValueError("Public document redirect limit exceeded")


def _normalized(value):
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()))


def _proof(text, quote):
    offset = text.find(quote)
    if offset < 0 or text.find(quote, offset + 1) >= 0:
        raise ValueError("Source quotation is absent or ambiguous in the acquired primary text")
    return {"quote": quote, "start": offset, "end": offset + len(quote), "sha256": sha256(quote.encode()).hexdigest()}


def _date_in_quote(value, quote):
    day = date.fromisoformat(value)
    formats = (day.isoformat(), day.strftime("%d/%m/%Y"), day.strftime("%d.%m.%Y"))
    if any(item in quote for item in formats):
        return True
    months = (("january", "gennaio"), ("february", "febbraio"), ("march", "marzo"), ("april", "aprile"), ("may", "maggio"), ("june", "giugno"), ("july", "luglio"), ("august", "agosto"), ("september", "settembre"), ("october", "ottobre"), ("november", "novembre"), ("december", "dicembre"))
    normal = _normalized(quote)
    return any(re.search(r"\b" + re.escape(candidate) + r"\b", normal) for month in months[day.month-1] for candidate in (f"{day.day} {month} {day.year}", f"{month} {day.day} {day.year}"))


_MONTHS = {name: index for index, pair in enumerate((
    ("january", "gennaio"), ("february", "febbraio"), ("march", "marzo"),
    ("april", "aprile"), ("may", "maggio"), ("june", "giugno"),
    ("july", "luglio"), ("august", "agosto"), ("september", "settembre"),
    ("october", "ottobre"), ("november", "novembre"), ("december", "dicembre")), 1)
    for name in pair}
_MONTH_PATTERN = "(?:" + "|".join(_MONTHS) + ")"
_DATE_PATTERN = (r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{4}|"
    r"\d{1,2}\s+" + _MONTH_PATTERN + r"\s+\d{4}|" + _MONTH_PATTERN + r"\s+\d{1,2},?\s+\d{4})")
_DATE_CONTEXT = {
    "publication": r"\b(?:published(?:\s+on)?|publication\s+date|released(?:\s+on)?|release\s+date|filing\s+date|pubblicat[oa](?:\s+il)?|data\s+di\s+pubblicazione|diffus[oa](?:\s+il)?)\b",
    "report_date": r"\b(?:year\s+ended|quarter\s+ended|period\s+ended|period\s+ending|as\s+at|as\s+of|report\s+date|holdings\s+date|esercizio\s+chiuso\s+al|periodo\s+concluso\s+al|bilancio\s+al|posizioni\s+al|data\s+della\s+relazione)\b",
}


def _parse_date_token(token):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        return date.fromisoformat(token).isoformat()
    if re.fullmatch(r"\d{1,2}[/.]\d{1,2}[/.]\d{4}", token):
        day, month, year = map(int, re.split(r"[/.]", token))
    else:
        words = token.lower().replace(",", "").split()
        if words[0] in _MONTHS:
            month, day, year = _MONTHS[words[0]], int(words[1]), int(words[2])
        else:
            day, month, year = int(words[0]), _MONTHS[words[1]], int(words[2])
    return date(year, month, day).isoformat()


def _dated_context(text, kind):
    """Only an explicit dated disclosure or reporting phrase is a locator.

    A PDF creation date, download date, bare cover date or a financial period
    does not establish when the report became public.
    """
    pattern = re.compile(_DATE_CONTEXT[kind] + r"\s*[:,\-]?\s*(?P<date>" + _DATE_PATTERN + r")\b", re.I)
    result = []
    for match in pattern.finditer(text):
        try:
            value = _parse_date_token(match.group("date"))
        except (ValueError, KeyError):
            continue
        quote = text[match.start():match.end()]
        # Repeated headers are ambiguous locators; the PM may give a longer
        # exact excerpt, but cannot supply a replacement fact.
        if text.count(quote) == 1:
            result.append((value, quote))
    return result


def _primary_publication_context(text):
    """Publication belongs to this document's bounded header or cover.

    A dated reference to another release in the narrative is not the release
    date of the acquired document. Only complete metadata/header lines in the
    first 2,000 extracted characters establish this fact; other layouts need a
    verified public companion or remain explicitly unverified.
    """
    header = text[:2000]
    if len(text) > 2000 and not header.endswith('\n'):
        # The bound is not a real end-of-line: a truncated narrative may begin
        # like metadata and continue with a reference to another document.
        header = header.rsplit('\n', 1)[0] if '\n' in header else ''
    horizontal = r"[^\S\r\n]*"
    separator = r"[^\S\r\n]*[:\-,\u2013\u2014]?[^\S\r\n]*"
    release = r"(?:press[ \t]+release|news[ \t]+release|comunicato[ \t]+stampa)"
    label = "(?:" + _DATE_CONTEXT["publication"] + "|" + release + ")"
    clock = r"(?:,[ \t]*\d{1,2}(?:[Hh:]\d{2})(?:[ \t]+(?:CEST|CET|UTC|GMT))?)?"
    patterns = (
        r"^" + horizontal + label + separator + r"(?P<date>" + _DATE_PATTERN + ")" + clock + horizontal + r"$",
        r"^" + horizontal + r"(?P<date>" + _DATE_PATTERN + ")" + clock + horizontal
        + r"(?:\r?\n" + horizontal + r"){1,6}" + release + horizontal + r"$",
    )
    result = []
    for pattern in patterns:
        for match in re.finditer(pattern, header, re.I | re.M):
            try:
                value = _parse_date_token(match.group('date'))
            except (ValueError, KeyError):
                continue
            quote = match.group(0)
            if text.count(quote) == 1:
                result.append((value, quote))
    return list(dict.fromkeys(result))


def _primary_financial_report_context(text):
    """Read an explicit dated current column, never infer a fiscal closing day.

    A standalone financial-statement title must identify the reporting year.
    The nearby standalone balance-sheet header must give a financial unit and
    two to four ordered date columns. Narrative references and quoted headings
    do not match this grammar. Conflicting current columns remain ambiguous.
    """
    horizontal=r'[^\S\r\n]*'
    titles=list(re.finditer(r'^'+horizontal+r'(?:\d{1,2}\.[ \t]+)?(?:condensed[ \t]+)?(?:consolidated[ \t]+)?'
        r'financial[ \t]+statements[ \t]+for[ \t]+(?:the[ \t]+)?'
        r'(?:(?:first|second)[ \t\r\n]{1,8}half[ \t]+of[ \t]+|(?:full[ \t]+)?year[ \t]+)'
        r'(?P<year>20\d{2})'+horizontal+r'$',text,re.I|re.M))
    heading=r'(?:balance[ \t]+sheet|(?:consolidated[ \t]+)?statement[ \t]+of[ \t]+financial[ \t]+position)'
    unit=r'\(?in[ \t]+(?:euros?|dollars?|pounds?|[A-Z]{3})[ \t]+(?:\(?x[ \t]+1[,.]000\)?|million|millions|thousand|thousands)\)?'
    date_line=r'(?:Note[s]?[ \t]+)?(?P<columns>'+_DATE_PATTERN+r'(?:[ \t]+'+_DATE_PATTERN+r'){1,3})'
    pattern=r'^'+horizontal+heading+horizontal+r'\r?\n(?:'+horizontal+r'\r?\n){0,3}'
    pattern+=horizontal+unit+r'(?:[ \t]+|\r?\n(?:'+horizontal+r'\r?\n){0,3})'
    pattern+=r'(?:'+horizontal+r'Note[s]?'+horizontal+r'\r?\n)?'+horizontal+date_line+horizontal+r'$'
    result=[]
    for match in re.finditer(pattern,text,re.I|re.M):
        preceding=[title for title in titles if 0 <= match.start()-title.end() <= 20000]
        if not preceding:
            continue
        title=preceding[-1]
        try:
            columns=[_parse_date_token(item.group(0)) for item in re.finditer(_DATE_PATTERN,match['columns'],re.I)]
        except (ValueError,KeyError):
            continue
        if (len(columns)<2 or len(set(columns))!=len(columns)
                or columns != sorted(columns,reverse=True) or columns[0][:4] != title['year']):
            continue
        quote=match.group(0)
        if text.count(quote)==1:
            result.append((columns[0],quote))
    return list(dict.fromkeys(result))


def _primary_sec_cover(text):
    """Bind an explicit SEC cover period to its declared registrant and form.

    Comparative statement dates and the later share-count date do not describe
    the filing period. A cover must have the Commission heading, a form and one
    explicit registrant declaration; multiple registrants/periods stay blocked.
    This reads printed evidence, never guesses a quarter end from the catalog.
    """
    forms = list(re.finditer(r'^[^\S\r\n]*FORM\s+(?P<form>10-K|10-Q|20-F|40-F)(?:/A)?[^\S\r\n]*$',
        text[:20000], re.I | re.M))
    forms = [item for item in forms if re.search(r'SECURITIES\s+AND\s+EXCHANGE\s+COMMISSION',
        text[max(0, item.start() - 2000):item.start()], re.I)]
    if not forms:
        return None
    registrants = list(re.finditer(r'^[^\S\r\n]*(?P<issuer>[^\r\n]{1,300}?)[^\S\r\n]*\r?\n'
        r'\s*\(Exact\s+name\s+of\s+(?:the\s+)?registrant\s+as\s+specified\s+in\s+its\s+charter\)',
        text, re.I | re.M))
    if len(registrants) != 1:
        raise ValueError('SEC cover requires one unambiguous registrant declaration; multiple issuers cannot be selected by an optional claim')
    registrant = registrants[0]
    if len(forms) != 1 or not 0 < registrant.start() - forms[0].end() <= 12000:
        raise ValueError('SEC cover form and registrant declarations are ambiguous or unbound')
    form = forms[0].group(0).strip().split(None, 1)[1].upper()
    period_pattern = (r'\bFor\s+the\s+(?P<kind>quarterly\s+period|(?:fiscal\s+)?year)\s+ended\s+'
        r'(?P<date>' + _DATE_PATTERN + r')\b')
    periods = list(re.finditer(period_pattern, text[forms[0].end():registrant.start()], re.I))
    if len(periods) != 1:
        raise ValueError('Reporting period requires one explicit SEC cover date; missing or contradictory SEC cover periods cannot be resolved by optional claims')
    period = periods[0]
    quarterly = period['kind'].lower().startswith('quarterly')
    if quarterly != form.startswith('10-Q'):
        raise ValueError('SEC cover reporting-period label conflicts with its form')
    return {'form': form, 'issuer': registrant['issuer'].strip(),
        'issuer_quote': registrant.group(0).strip(),
        'report_date': _parse_date_token(period['date']), 'report_date_quote': period.group(0)}


def _html_publication_context(raw, text):
    """Date of the unique visible article header, never dates from navigation.

    Keep full extracted source text and exact quote offsets. DOM context chooses
    the header, while visible text must corroborate every machine-readable date.
    A missing/ambiguous article or conflicting primary facts stays unqualified.
    """
    from bs4 import BeautifulSoup
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    soup = BeautifulSoup(raw, 'html.parser')
    for node in soup.select('script,style,noscript,template,nav,aside,footer,[hidden],[aria-hidden="true"]'):
        node.decompose()
    mains = soup.find_all('main')
    if mains:
        if len(mains) != 1 or len(mains[0].find_all('h1')) != 1:
            return []
        candidates = [mains[0]]
    else:
        candidates = [node for node in soup.find_all('article') if len(node.find_all('h1')) == 1]
    if not candidates:
        # Plain filings and text-like HTML retain the original strict locator.
        return None if not soup.find(['main', 'article']) else []
    if len(candidates) != 1:
        return []
    primary = candidates[0]
    heading = primary.find('h1')
    header = primary
    for parent in heading.parents:
        if parent is primary:
            break
        if parent.name in ('header', 'section'):
            header = parent
            break
    header_text = estrai_testo('header.html', contenuto=str(header).encode('utf-8')).get('testo', '')
    bounded = header_text[:2000]
    if len(header_text) > 2000 and not bounded.endswith('\n'):
        bounded = bounded.rsplit('\n', 1)[0] if '\n' in bounded else ''
    result = _primary_publication_context(header_text)
    for node in header.find_all('time'):
        if node.find_parent(['a', 'aside', 'nav', 'footer']):
            continue
        visible = node.get_text().strip()
        if not visible or visible not in bounded:
            continue
        stamp = node.get('datetime')
        if not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?', stamp):
            raise ValueError('Publication article time has no explicit ISO date')
        value = date.fromisoformat(stamp[:10])
        # US numeric display is accepted only against its explicit ISO value,
        # never inferred from the browser locale or an ambiguous bare date.
        numeric = {f'{value.month}/{value.day}/{value.year}', value.strftime('%m/%d/%Y')}
        if not _date_in_quote(value.isoformat(), visible) and visible not in numeric:
            raise ValueError('Publication visible date conflicts with the article time datetime')
        result.append((value.isoformat(), visible))
    standalone = (r'^[^\S\r\n]*(?P<date>' + _DATE_PATTERN
        + r')(?:[ \t]+at[ \t]+\d{1,2}:\d{2})?[^\S\r\n]*$')
    # A bare financial cover date never proves publication by itself.
    for match in re.finditer(standalone, bounded if result else '', re.I | re.M):
        try:
            result.append((_parse_date_token(match['date']), match.group(0).strip()))
        except (ValueError, KeyError):
            continue
    if len({value for value, _ in result}) > 1:
        raise ValueError('Publication has contradictory primary article header dates')
    return [(value, quote) for value, quote in dict.fromkeys(result) if text.count(quote) == 1]


_DEPOSIT_CONTRACT = "trade-idea-emarket-deposit-receipt/1"
_DEPOSIT_BASIS = "emarket_sdir_deposit_receipt"
_PUBLICATION_AMBIGUOUS = ('Publication requires one unambiguous primary header or cover date; optional claims cannot '
                          'resolve contradictory publication facts')
_DEPOSIT_PM_HINT = "data di pubblicazione da fornire dal PM"


def _deposit_document_types():
    """Nomi dei documenti per tipo: UNICA fonte = `emarket_sdir.DOCUMENTI_DEPOSITO` (stesse regole della
    ricerca del deposito; accordo D4/IT1/IT2 05/10). Copertina e deposito leggono gli stessi nomi."""
    from bellomberg.market_data.emarket_sdir import DOCUMENTI_DEPOSITO
    return DOCUMENTI_DEPOSITO


_DEPOSIT_FIELDS = ("ticker", "isin", "emarket_id", "tipo", "periodo_fine", "stato", "stato_originale",
                   "data_deposito", "ora_deposito",
                   "titolo", "url", "protocollo", "categoria", "lingua", "fonte", "categorie_cercate", "url_liste",
                   "sha256_liste", "letto_il", "limiti", "prova", "sha256_pdf", "voce_da", "isin_resolution")
_ISIN_RESOLUTION_FIELDS = ("stato", "isin", "emarket", "negozio", "fonte_url", "letto_il")
# Ricevute dal router sdir.get_data_deposito (accordo D4b/ON 05/10): quale SDIR ha dato la data e i campi
# 1INFO. Le ricevute vecchie (solo eMarket) non hanno «sdir» e restano valide byte per byte (il sigillo e'
# confrontato chiave per chiave con l'archivio). Dell'instradamento si sigilla la parte deterministica.
_SDIR_EMARKET, _SDIR_1INFO = "eMarket SDIR", "1INFO-SDIR"
_DEPOSIT_SDIR_FIELDS = ("sdir", "oneinfo_ndg", "esef", "consolidato", "tipo_data", "instradamento")
_ROUTING_FIELDS = ("regola", "scelta", "emarket_id", "oneinfo_ndg")
# eMarket Storage, sezione DOCUMENTI (IT2/IT3 05/10, ancora primaria): data di STOCCAGGIO del documento.
# Campi sigillati SOLO quando prova == 'documento' (le ricevute da comunicato restano identiche).
_DEPOSIT_DOCUMENT_FIELDS = ("prova_documento", "documento", "comunicato_conferma", "url_liste_documenti",
                            "sha256_liste_documenti", "stato_documenti", "tipo_data", "natura")
# Ambiguo UE (regola di main 05/10, caso reale DEU vs comptes consolides): si sceglie l'UNICO candidato il cui
# titolo porta un nome del documento uguale a uno di quelli letti in copertina legati al periodo.
_COVER_NAME_CHOICE = "cover_document_name/1"
# Scelta dell'instradamento ammessa per lo SDIR che ha dato la data (ON 05/10: con 'entrambi' la data servita
# puo' venire da UNA sola fonte; «eMarket SDIR + 1INFO-SDIR» non arriva mai con stato ok).
_SDIR_ROUTING_CHOICES = {_SDIR_EMARKET: ("emarket", "entrambi"), _SDIR_1INFO: ("oneinfo", "entrambi")}
# Listini UE non italiani (decisione PM 05/10, contratto scratchpad/INTERFACCIA_UE.md): instradatore
# market_data/depositi_ue.py di EU-R; la ricevuta si sigilla INTERA (risposte_salvate comprese) e si riverifica
# senza rete con depositi_ue.riverifica_ricevuta. natura_data (dal RITORNO, non dal registro) va nel testo al PM.
_EU_DEPOSIT_CONTRACT = "trade-idea-eu-deposit-receipt/1"
_EU_DEPOSIT_BASIS = "eu_official_deposit_receipt"
_EU_LOCAL_KEYS = ("contract", "scelta_copertina", "esito_sdir", "identita_ue")    # chiavi nostre, non del modulo
_EU_NATURE_LABELS = {"diffusione": "data di diffusione %s",
                     "deposito_autorita": "data di deposito %s, non di diffusione,",
                     "pubblicazione_dichiarata": "data di pubblicazione dichiarata da %s"}


# Finestra nome -> periodo (D4 05/10): fra il nome e la data in copertina stanno al massimo qualificatori
# e locuzioni («consolidata», «abbreviato», « - », «for the six months ended», «as at»: <= 30 caratteri);
# 60 li copre con margine e resta piu' corta di una riga di indice o di una frase che cita un ALTRO
# documento. Stessa ampiezza della conferma nel PDF di eMarket (`emarket_sdir._lingua_doc`, vicini=True).
_DOCUMENT_PERIOD_WINDOW = 60
# «Interim financial report» (IAS 34 = anche la SEMESTRALE): lo decide la regola di IT2 in DOCUMENTI_DEPOSITO
# (lookahead sul mese: giugno -> semestrale, marzo/settembre -> trimestrale, altri mesi -> nessun tipo).
# Annuale col SOLO anno («Annual Report 2025», «Relazione finanziaria annuale 2025»): ammesso soltanto se il
# periodo e' il 31/12 di quell'anno. Esercizi non solari («2024/2025», chiusura al 30/06) -> tipo non deciso.
_ANNUAL_BARE_YEAR = re.compile(r"\s*(?:(?:dell'|per\s+l')?esercizio\s+|for\s+(?:the\s+)?(?:financial\s+|fiscal\s+)?"
                               r"year\s+)?(?P<year>\d{4})\b(?!\s*[/-]\s*\d)", re.I)
_DOCUMENT_TYPE_RULE = ("name followed within %d characters by the period date; annual: year alone only for a "
                       "31 Dec year end; 'interim financial report': June = half-year, Mar/Sep = quarterly, else none"
                       % _DOCUMENT_PERIOD_WINDOW)


def _cover_bound_names(text, report_date, extra_names=None):
    """{(tipo, nome normalizzato)} dei nomi di documento in copertina (primi 2.000 caratteri) seguiti ENTRO
    `_DOCUMENT_PERIOD_WINDOW` caratteri da una data uguale al periodo (o, per l'annuale, dal solo anno con
    periodo al 31/12). Nomi = DOCUMENTI_DEPOSITO (IT/EN) + `extra_names` {tipo: [regex]} (nomi locali UE)."""
    header = " ".join(text[:2000].split())
    date_pattern = re.compile(_DATE_PATTERN, re.I)
    period = date.fromisoformat(report_date)
    found_names = set()
    for kind, patterns in _deposit_document_types().items():
        for pattern in list(patterns) + list((extra_names or {}).get(kind) or ()):
            for name in re.finditer(pattern, header, re.I):
                if not _normalized(name.group(0)):
                    continue
                window = header[name.end():name.end() + _DOCUMENT_PERIOD_WINDOW + 30]
                bound = False
                for found in date_pattern.finditer(window):
                    if found.start() > _DOCUMENT_PERIOD_WINDOW:
                        break
                    try:
                        if _parse_date_token(" ".join(found.group(0).split())) == report_date:
                            bound = True
                            break
                    except (ValueError, KeyError):
                        continue
                if not bound and kind == "annuale" and (period.month, period.day) == (12, 31):
                    year = _ANNUAL_BARE_YEAR.match(window)
                    bound = year is not None and int(year.group("year")) == period.year
                if bound:
                    found_names.add((kind, _normalized(name.group(0))))
    return found_names


def _deposit_document_type(text, report_date, extra_names=None):
    """Tipo della relazione dalla copertina: il tipo dei nomi legati al periodo (`_cover_bound_names`). None se
    nessun tipo, piu' tipi, o un «interim financial report» di un mese non deciso: il tipo sceglie quale deposito
    ufficiale cercare, quindi nel dubbio non si cerca."""
    kinds = {kind for kind, _name in _cover_bound_names(text, report_date, extra_names)}
    return kinds.pop() if len(kinds) == 1 else None


def _eu_document_names(country):
    """{tipo: [regex]} dei nomi locali del paese di instradamento (depositi_ue.nomi_documento, AGGIUNTA 2);
    tipi senza nomi esclusi. None se nessun paese."""
    if not country:
        return None
    from bellomberg.market_data.depositi_ue import nomi_documento
    names = {}
    for kind in ("annuale", "semestrale", "trimestrale"):
        found, _reason = nomi_documento(country, kind)
        if found:
            names[kind] = list(found)
    return names


def _deposit_type_names(value):
    """Nomi locali con cui ricalcolare il tipo per una ricevuta sigillata (UE: paese sigillato)."""
    return _eu_document_names(value.get("paese")) if value.get("contract") == _EU_DEPOSIT_CONTRACT else None


def _cover_named_candidate(text, report_date, tipo, candidates, extra_names=None):
    """Scelta PURA fra i candidati di un 'ambiguo': l'unico il cui titolo contiene un nome del documento uguale
    (normalizzato) a uno dei nomi di tipo `tipo` legati al periodo in copertina. None se nessuno o piu' d'uno."""
    cover = sorted(name for kind, name in _cover_bound_names(text, report_date, extra_names) if kind == tipo)
    if not cover or not isinstance(candidates, list) or not candidates:
        return None
    patterns = list(_deposit_document_types()[tipo]) + list((extra_names or {}).get(tipo) or ())
    matching = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("titolo"), str):
            return None
        names = {_normalized(found.group(0)) for pattern in patterns
                 for found in re.finditer(pattern, candidate["titolo"], re.I)}
        if names & set(cover):
            matching.append(candidate)
    if len(matching) != 1:
        return None
    return {"regola": _COVER_NAME_CHOICE, "nomi_copertina": cover, "candidato": deepcopy(matching[0])}


def _deposit_effective(value):
    """La ricevuta vista col deposito che conta: per un 'ambiguo' UE scelto col nome in copertina, i campi del
    candidato scelto (data, ora, titolo, url, protocollo); altrimenti la ricevuta com'e'."""
    choice = value.get("scelta_copertina") if isinstance(value, dict) else None
    if not isinstance(choice, dict) or not isinstance(choice.get("candidato"), dict):
        return value
    chosen = choice["candidato"]
    effective = {**value, "data_deposito": chosen.get("data"), "ora_deposito": chosen.get("ora"),
                 "titolo": chosen.get("titolo"), "url": chosen.get("url"), "protocollo": chosen.get("protocollo")}
    if value.get("contract") == _EU_DEPOSIT_CONTRACT:
        # Rilievo RV-D4 P1/P2: l'ora del candidato vale SOLO col fuso dichiarato dal candidato stesso (AMF/Nasdaq:
        # ore UTC con fuso del ritorno None; AFM: ora del registro senza fuso). Senza: ora ignorata = fine del
        # giorno nel fuso del RITORNO; se anche quello manca la scelta e' rifiutata (mai il fuso del listino).
        zone = chosen.get("fuso")
        effective.update(fuso=zone or value.get("fuso"), ora_deposito=chosen.get("ora") if zone else None,
                         protocollo=chosen.get("protocollo") or chosen.get("id"))
    if value.get("contract") == _DEPOSIT_CONTRACT:
        # SDIR italiani: la riga scelta e' quella che ripassa la regola della fonte (righe eMarket/1INFO con
        # url_pdf; righe della sezione Documenti con url e prova del documento).
        effective.update(url=chosen.get("url") or chosen.get("url_pdf"), categoria=chosen.get("categoria"))
        if value.get("prova") == "documento":
            # forma vera dell'ambiguo (emarket_sdir): tipo_data None; la riga scelta E' un documento stoccato
            effective.update(documento={key: item for key, item in chosen.items() if key != "prova"},
                             prova_documento=chosen.get("prova"), tipo_data="stoccaggio_documento")
        else:
            effective.update(prova=chosen.get("prova"))
        if _deposit_sdir(value) == _SDIR_1INFO:
            effective.update(esef=chosen.get("esef"), consolidato=chosen.get("consolidato"))
    return effective


# AGGIUNTA 3 di INTERFACCIA_UE (main 05/10, rilievo RV-UE1 AMF-5): si confrontano ISTANTI, non date.
# Istante del deposito = data + ora nel fuso della fonte; ora mancante = FINE del giorno locale (prudente).
# Cutoff = FINE del giorno as_of nel fuso del PM (Europe/Rome). SDIR italiani: ore di Roma (anche 1INFO, la cui
# ora e' gia' di Roma benche' marcata come UTC: misura S1). Fonti UE: il fuso dichiarato nel ritorno; senza fuso
# quello del listino (mercati.MERCATI); senza nessuno dei due la ricevuta e' rifiutata.
_PM_TIMEZONE = "Europe/Rome"


def _zone(name):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ValueError("Deposit timezone %r cannot be resolved (%s); %s"
                         % (str(name)[:40], type(exc).__name__, _DEPOSIT_PM_HINT)) from exc


def _listing_timezone(ticker):
    from bellomberg.market_data.mercati import MERCATI
    market = MERCATI.get(_listing_suffix(ticker) or "")
    return market[1] if market else None


def _deposit_timezone(value):
    if value.get("contract") != _EU_DEPOSIT_CONTRACT:
        return _PM_TIMEZONE
    if value.get("scelta_copertina") is not None:
        return value.get("fuso")        # scelta fra candidati: solo il fuso dichiarato dalla fonte (RV-D4 P1)
    return value.get("fuso") or _listing_timezone(value.get("ticker"))


def _deposit_instant(value):
    """Istante del deposito (datetime con fuso). Ora mancante = 23:59:59 locale della fonte."""
    value = _deposit_effective(value)
    zone_name = _deposit_timezone(value)
    if not zone_name:
        raise ValueError("Deposit time zone is unknown (no source time zone and no listing time zone); "
                         + _DEPOSIT_PM_HINT)
    day = date.fromisoformat(value["data_deposito"])
    clock = value.get("ora_deposito")
    if clock is None:
        hour, minute, second = 23, 59, 59
    elif isinstance(clock, str) and re.fullmatch(r"\d{2}:\d{2}", clock):
        hour, minute, second = int(clock[:2]), int(clock[3:]), 0
    else:
        raise ValueError("Deposit time is malformed; " + _DEPOSIT_PM_HINT)
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=_zone(zone_name))


def _cutoff_instant(as_of):
    day = date.fromisoformat(as_of)
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=_zone(_PM_TIMEZONE))


def _deposit_public_date(value):
    """Data di pubblicazione dichiarata = giorno dell'istante del deposito nel fuso del PM (come il cutoff)."""
    return _deposit_instant(value).astimezone(_zone(_PM_TIMEZONE)).date().isoformat()


def _deposit_sdir(value):
    """Quale SDIR ha dato la data. Ricevuta senza \u00absdir\u00bb (prima del router, solo eMarket) = eMarket."""
    return value.get("sdir") if "sdir" in value else _SDIR_EMARKET


def _deposit_basis(value):
    """Base della data di pubblicazione dichiarata nella ricevuta, dal contratto del deposito sigillato."""
    return _EU_DEPOSIT_BASIS if value.get("contract") == _EU_DEPOSIT_CONTRACT else _DEPOSIT_BASIS


def _deposit_declaration(value):
    value = _deposit_effective(value)
    day = date.fromisoformat(value["data_deposito"])
    if value.get("contract") == _EU_DEPOSIT_CONTRACT:
        # UE: l'etichetta dice la NATURA della data (diffusione / deposito presso l'autorita' / dichiarata).
        local_zone = _listing_timezone(value.get("ticker")) or _deposit_timezone(value)
        if value.get("ora_deposito") is None:
            moment = "%s (ora non indicata dalla fonte: fine del giorno)" % day.strftime("%d/%m/%Y")
            rome = date.fromisoformat(_deposit_public_date(value))
            if rome != day:
                moment += ", cio\u00e8 il %s a Roma" % rome.strftime("%d/%m/%Y")
        else:
            local = _deposit_instant(value).astimezone(_zone(local_zone))
            moment = "%s alle %s ora di %s" % (local.strftime("%d/%m/%Y"), local.strftime("%H:%M"), local_zone)
        text = ("%s del %s, documento «%s»" % (_EU_NATURE_LABELS[value["natura_data"]] % value["fonte"],
                moment, " ".join(str(value["titolo"]).split())))
        if value.get("esito_sdir") is not None:
            text = ("non trovato sugli SDIR italiani; data dallo Stato d'origine (%s, ISIN %s): %s"
                    % (value.get("paese"), value.get("isin_instradamento"), text))
        if value.get("protocollo"):
            text += " (protocollo %s)" % value["protocollo"]
        if (value.get("instradamento") or {}).get("canale") == "borsa":
            text += "; canale di borsa, non l'OAM nazionale"
        if value.get("scelta_copertina"):
            text += ("; fonte ambigua fra %d documenti: scelto quello col nome della copertina"
                     % len(value.get("candidati") or ()))
        identity_eu = value.get("identita_ue") or {}
        if identity_eu.get("stato") == "ok":
            text += "; emittente verificato su GLEIF (LEI %s)" % identity_eu.get("lei")
        elif identity_eu:
            text += ("; identità dell'emittente non confermata su GLEIF (%s): ISIN e LEI non usati"
                     % identity_eu.get("stato"))
        return text
    # 1INFO: la data e' quella di STOCCAGGIO del documento (definizione diversa dal comunicato eMarket).
    label = ("data di stoccaggio 1INFO-SDIR" if _deposit_sdir(value) == _SDIR_1INFO
             else "data di stoccaggio eMarket Storage (sezione Documenti)" if value.get("prova") == "documento"
             else "data di deposito eMarket SDIR")
    text = ("%s del %s, documento \u00ab%s\u00bb (protocollo %s)"
            % (label, day.strftime("%d/%m/%Y"), " ".join(str(value["titolo"]).split()), value["protocollo"]))
    if value.get("stato") == "STALE":
        read = datetime.fromisoformat(str(value["letto_il"]))
        text += "; data di deposito letta il %s, fonte ora non raggiungibile" % read.strftime("%d/%m")
    if value.get("prova") == "testo_pdf":
        text += "; nome e periodo del documento letti nel PDF del comunicato (titolo generico di deposito)"
    if value.get("voce_da") == "automatico":
        text += "; ISIN %s risolto automaticamente su Borsa Italiana" % value.get("isin")
    if value.get("scelta_copertina"):
        text += ("; fonte ambigua fra %d documenti: scelto quello col nome della copertina"
                 % len(value.get("candidati") or ()))
    if isinstance(value.get("fine_esercizio"), dict):
        fiscal = value["fine_esercizio"]
        text += "; esercizio non solare che chiude il %s (fonte: %s)" % (
            "/".join(reversed(str(fiscal.get("valore")).split("-"))),
            _FISCAL_LABELS.get(fiscal.get("fonte"), fiscal.get("fonte")))
    return text


def _deposit_pdf_text(root, sha):
    """Testo del PDF del comunicato archiviato (stadio 2 di eMarket), dai byte sigillati."""
    from io import BytesIO
    from pypdf import PdfReader
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("eMarket SDIR deposit PDF digest is missing; " + _DEPOSIT_PM_HINT)
    path = Path(root) / "publication-receipts" / (sha + ".pdf")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("Archived eMarket SDIR deposit PDF is missing or unreadable") from exc
    if sha256(raw).hexdigest() != sha:
        raise ValueError("Archived eMarket SDIR deposit PDF bytes changed")
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(raw)).pages)
    except Exception as exc:
        raise ValueError("Archived eMarket SDIR deposit PDF cannot be read (" + type(exc).__name__ + ")") from exc


def _check_full_sdir_receipt(value, ticker, tipo, report_date):
    """AGGIUNTA 5: la ricevuta INTERA della fonte si riverifica senza rete (sdir.riverifica_deposito rifa' la scelta
    dalle risposte salvate) e ogni campo sigillato a parte deve essere quello della ricevuta riverificata."""
    from bellomberg.market_data.sdir import riverifica_deposito
    source = value["ricevuta_fonte"]
    fiscal = value.get("fine_esercizio")
    if not isinstance(source, dict):
        raise ValueError("SDIR source receipt is malformed; " + _DEPOSIT_PM_HINT)
    try:
        ok, reason = riverifica_deposito(source, ticker=(ticker or "").strip().upper(), tipo=tipo,
                                         periodo_fine=report_date,
                                         fine_esercizio=fiscal.get("valore") if isinstance(fiscal, dict) else None)
    except Exception as exc:
        ok, reason = False, "riverifica sollevata (%s)" % type(exc).__name__
    if ok is not True:
        raise ValueError("SDIR source receipt failed the offline re-verification (%s); %s"
                         % (str(reason)[:200], _DEPOSIT_PM_HINT))
    keys = [key for key in _DEPOSIT_FIELDS if key != "isin_resolution"]
    if value.get("prova") == "documento":
        keys += list(_DEPOSIT_DOCUMENT_FIELDS)
    if "sdir" in value:
        keys += [key for key in _DEPOSIT_SDIR_FIELDS if key != "instradamento"]
    if value.get("scelta_copertina") is not None:
        keys.append("candidati")
    routing = source.get("instradamento")
    bound = ({key: routing.get(key) for key in _ROUTING_FIELDS} if isinstance(routing, dict) else routing)
    if (any(not same_exact_value(value.get(key), source.get(key)) for key in keys)
            or ("sdir" in value and not same_exact_value(value.get("instradamento"), bound))):
        raise ValueError("Sealed SDIR deposit fields differ from the re-verified source receipt; " + _DEPOSIT_PM_HINT)


def _check_deposit_value(value, ticker, tipo, report_date, as_of, root, text=None):
    """Riverifica PURA del deposito sigillato: stesso emittente, tipo e periodo; deposito dopo la fine
    del periodo e non dopo il cutoff; il titolo passa ancora la regola di eMarket (nome + periodo).
    Un 'ambiguo' vale solo con la scelta col nome in copertina, RIFATTA sul testo del documento archiviato;
    poi la riga scelta ripassa DA SOLA la regola della fonte (come un ok)."""
    from bellomberg.market_data.emarket_sdir import candidati_deposito
    if not isinstance(value, dict) or value.get("ricevuta_fonte") is None:
        raise ValueError("SDIR deposit receipt lacks the full source receipt (saved responses); " + _DEPOSIT_PM_HINT)
    _check_full_sdir_receipt(value, ticker, tipo, report_date)
    choice = value.get("scelta_copertina") if isinstance(value, dict) else None
    if choice is not None:
        if value.get("stato") != "ambiguo" or text is None:
            raise ValueError("SDIR deposit choice by cover name needs an ambiguous source and the document text; "
                             + _DEPOSIT_PM_HINT)
        again = _cover_named_candidate(text, report_date, tipo, value.get("candidati"))
        if not same_exact_value(again, choice):
            raise ValueError("SDIR deposit choice by cover name differs from the document cover; " + _DEPOSIT_PM_HINT)
        value = {**_deposit_effective(value), "stato": "ok"}
        if value.get("prova") == "testo_pdf":
            raise ValueError("SDIR deposit choice among generic deposit notices (PDF text) is not supported; "
                             + _DEPOSIT_PM_HINT)
    stale = isinstance(value, dict) and value.get("stato") == "STALE" and value.get("stato_originale") == "ok"
    if stale:
        try:
            datetime.fromisoformat(str(value.get("letto_il")))
        except ValueError:
            stale = False
    if (not isinstance(value, dict) or value.get("contract") != _DEPOSIT_CONTRACT
            or not (value.get("stato") == "ok" or stale)
            or value.get("ticker") != (ticker or "").strip().upper() or value.get("tipo") != tipo
            or value.get("periodo_fine") != report_date
            or any(not isinstance(value.get(key), str) or not value[key].strip()
                   for key in ("data_deposito", "titolo", "url", "protocollo"))
            or not isinstance(value.get("url_liste"), list)
            or not (value["url_liste"] or value.get("prova") == "documento")
            or not isinstance(value.get("sha256_liste"), dict)
            or set(value["sha256_liste"]) != set(value["url_liste"])):
        raise ValueError("eMarket SDIR deposit receipt conflicts with the document issuer, type or period; "
                         + _DEPOSIT_PM_HINT)
    if not (date.fromisoformat(report_date) < date.fromisoformat(value["data_deposito"])
            and _deposit_instant(value) <= _cutoff_instant(as_of)):
        raise ValueError("eMarket SDIR deposit date is not after the reporting period or is after the cutoff; "
                         + _DEPOSIT_PM_HINT)
    row = {"data": value["data_deposito"], "ora": value.get("ora_deposito"), "titolo": value["titolo"],
           "url_pdf": value["url"], "protocollo": value["protocollo"], "categoria": value.get("categoria")}
    _check_fiscal_seal(value, tipo, report_date, text)
    isin = value.get("isin_resolution")
    if isin is not None and (not isinstance(isin, dict) or isin.get("stato") != "ok" or isin.get("isin") != value.get("isin")):
        raise ValueError("Automatic ISIN resolution differs from the eMarket SDIR deposit issuer; " + _DEPOSIT_PM_HINT)
    sdir = _deposit_sdir(value)
    routing = value.get("instradamento")
    if routing is not None and (not isinstance(routing, dict)
                                or routing.get("scelta") not in _SDIR_ROUTING_CHOICES.get(sdir, ())):
        raise ValueError("SDIR routing of the deposit receipt differs from the SDIR that gave the date; "
                         + _DEPOSIT_PM_HINT)
    if sdir == _SDIR_1INFO:
        # Regola PURA di 1INFO (ON): categoria del tipo, finestra dopo il periodo, ancora ESEF o nome + periodo.
        from bellomberg.market_data.oneinfo_sdir import candidati_deposito as oneinfo_candidates
        verdict = oneinfo_candidates([{**row, "esef": value.get("esef"), "consolidato": value.get("consolidato")}],
                                     tipo, date.fromisoformat(report_date))
        if (value.get("tipo_data") != "stoccaggio_documento" or value.get("prova") not in ("titolo", "esef")
                or verdict["stato"] != "ok" or verdict["scelto"]["protocollo"] != value["protocollo"]
                or verdict.get("prova") != value["prova"]):
            raise ValueError("1INFO-SDIR deposit does not name this document and period; " + _DEPOSIT_PM_HINT)
        return
    if sdir != _SDIR_EMARKET:
        raise ValueError("Deposit receipt SDIR is unknown (%s); %s" % (str(sdir)[:60], _DEPOSIT_PM_HINT))
    if value.get("prova") == "documento":
        _check_storage_document(value, tipo, report_date)
        return
    texts = None
    if value.get("prova") == "testo_pdf":
        # Titolo generico: nome + periodo si ricontrollano nel PDF del comunicato archiviato.
        texts = {value["url"]: _deposit_pdf_text(root, (value.get("sha256_pdf") or {}).get(value["url"]))}
    elif value.get("prova") not in ("titolo", None):
        raise ValueError("eMarket SDIR deposit evidence kind is unknown; " + _DEPOSIT_PM_HINT)
    verdict = candidati_deposito([row], tipo, date.fromisoformat(report_date), testi_pdf=texts)
    if (verdict["stato"] != "ok" or verdict["scelto"]["protocollo"] != value["protocollo"]
            or verdict.get("prova") != (value.get("prova") or "titolo")):
        raise ValueError("eMarket SDIR deposit does not name this document and period; " + _DEPOSIT_PM_HINT)


def _fiscal_year_end(tipo, report_date):
    """'MM-GG' della fine dell'esercizio quando il periodo NON e' solare e la si DEDUCE dal tipo: annuale = il
    periodo stesso; semestrale = sei mesi dopo (fine mese). Trimestrale non solare: non deducibile -> None (la
    fonte risponde KO 'parametro', rifiuto dichiarato). Periodo solare -> None (default delle fonti)."""
    period = date.fromisoformat(report_date)
    if tipo == "annuale" and (period.month, period.day) != (12, 31):
        return period.strftime("%m-%d")
    if tipo == "semestrale" and (period.month, period.day) != (6, 30):
        month = (period.month + 5) % 12 + 1
        year = period.year + (1 if month < period.month else 0)
        last = (date(year + (month == 12), month % 12 + 1, 1) - date.resolution).day
        return "%02d-%02d" % (month, last)
    return None


# Fine esercizio di una TRIMESTRALE non solare (main 05/10): non si deduce dal periodo (31/07 puo' essere il Q1 di
# un esercizio al 30/04 o il Q3 di uno al 31/10). Fonti DICHIARATE, in quest'ordine: (a) l'ultima relazione
# ANNUALE dello stesso emittente gia' ammessa (in questa richiesta o passata dal chiamante); (b) la copertina
# stessa se cita l'esercizio; (c) il fornitore prezzi passato dal chiamante, dichiarato come tale. Nessuna fonte =
# limite dichiarato (la fonte del deposito risponde KO 'parametro'). Valore + fonte si sigillano nella ricevuta.
_FISCAL_DEDUCED = "dedotta dal tipo e dal periodo"
_FISCAL_KINDS = ("relazione_annuale", "copertina", "fornitore_prezzi")
_FISCAL_LABELS = {"relazione_annuale": "relazione annuale ammessa", "copertina": "esercizio citato in copertina",
                  "fornitore_prezzi": "fornitore prezzi, PROXY non ufficiale",
                  "dedotta dal tipo e dal periodo": "dedotta dal tipo e dal periodo"}
_FISCAL_COVER = re.compile(r"(?:esercizio\s+(?:sociale\s+)?(?:che\s+)?(?:si\s+)?(?:chiude|chiuder\u00e0|chiudera')\s+al|"
                           r"(?:financial|fiscal)\s+year\s+(?:ending|ended|ends)(?:\s+on)?)\s+(?P<date>"
                           + _DATE_PATTERN + r")", re.I)


def _fiscal_entry_ok(entry, tipo, report_date):
    """Una voce {valore 'MM-GG', fonte, dettaglio} e' coerente con una trimestrale al `report_date`?"""
    if (not isinstance(entry, dict) or entry.get("fonte") not in _FISCAL_KINDS
            or not isinstance(entry.get("valore"), str) or not re.fullmatch(r"\d{2}-\d{2}", entry["valore"])):
        return False
    try:
        end = date.fromisoformat("2000-" + entry["valore"])     # 2000 bisestile: '02-29' si legge
    except ValueError:
        return False
    period = date.fromisoformat(report_date)
    last_day = (date(period.year + (period.month == 12), period.month % 12 + 1, 1) - date.resolution).day
    # fine mese: in un anno NON bisestile (2001), piu' il 29/02 (RV-D4 P3-1: '02-28' dei retail era rifiutato)
    month_end = (end.day == (date(2001 + (end.month == 12), end.month % 12 + 1, 1) - date.resolution).day
                 or (end.month, end.day) == (2, 29))
    return (tipo == "trimestrale" and period.day == last_day
            and (period.month - end.month) % 12 in (3, 6, 9) and month_end)


def _fiscal_year_end_declared(tipo, report_date, text=None, declared=()):
    """{valore, fonte, dettaglio} della fine esercizio da passare alla fonte del deposito, o None (periodo solare,
    o trimestrale non solare senza fonte dichiarata coerente)."""
    deduced = _fiscal_year_end(tipo, report_date)
    if deduced:
        return {"valore": deduced, "fonte": _FISCAL_DEDUCED, "dettaglio": None}
    period = date.fromisoformat(report_date)
    if tipo != "trimestrale" or (period.month, period.day) in ((3, 31), (9, 30)):
        return None
    entries = [entry for entry in declared or () if isinstance(entry, dict)]
    cover = set()
    for match in _FISCAL_COVER.finditer(" ".join((text or "")[:2000].split())):
        try:
            cover.add(_parse_date_token(" ".join(match.group("date").split()))[5:])
        except (ValueError, KeyError):
            continue
    ordered = ([entry for entry in entries if entry.get("fonte") == "relazione_annuale"]
               + ([{"valore": cover.pop(), "fonte": "copertina", "dettaglio": "esercizio citato in copertina"}]
                  if len(cover) == 1 else [])
               + [entry for entry in entries if entry.get("fonte") == "fornitore_prezzi"])
    for entry in ordered:
        if _fiscal_entry_ok(entry, tipo, report_date):
            return {"valore": entry["valore"], "fonte": entry["fonte"], "dettaglio": entry.get("dettaglio")}
    return None


def fiscal_year_end_from_provider(info, source_id="yahoo public issuer profile"):
    """Voce (c) per `fiscal_year_end_sources`: la fine esercizio del FORNITORE PREZZI (profilo Yahoo,
    `lastFiscalYearEnd` = epoch in secondi), DICHIARATA come PROXY. [] se il campo manca o non e' leggibile:
    nessun valore inventato, la trimestrale non solare resta senza fonte (limite dichiarato)."""
    raw = (info or {}).get("lastFiscalYearEnd") if isinstance(info, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return []
    try:
        from datetime import timezone
        end = datetime.fromtimestamp(raw, tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return []
    return [{"valore": end.strftime("%m-%d"), "fonte": "fornitore_prezzi",
             "dettaglio": "PROXY: lastFiscalYearEnd %s del fornitore prezzi (%s), non una fonte ufficiale"
                          % (end.isoformat(), source_id)}]


def _check_fiscal_seal(value, tipo, report_date, text=None, *, cover_check=True):
    """La fine esercizio sigillata (se c'e') e' coerente: dedotta = ricalcolata; dichiarata = coerente col trimestre;
    letta in copertina = RILETTA dal testo archiviato (senza testo: rifiuto). Ritorna il valore 'MM-GG'."""
    sealed = value.get("fine_esercizio")
    if sealed is None:
        return _fiscal_year_end(tipo, report_date)
    if not isinstance(sealed, dict) or (
            sealed.get("valore") != _fiscal_year_end(tipo, report_date) if sealed.get("fonte") == _FISCAL_DEDUCED
            else not _fiscal_entry_ok(sealed, tipo, report_date)):
        raise ValueError("Sealed fiscal year end is malformed or incoherent with the reporting period; "
                         + _DEPOSIT_PM_HINT)
    if cover_check and sealed.get("fonte") == "copertina" and (
            text is None or _fiscal_year_end_declared(tipo, report_date, text, ()) != sealed):
        raise ValueError("Sealed fiscal year end read on the cover differs from the document cover; "
                         + _DEPOSIT_PM_HINT)
    return sealed["valore"]


def _check_storage_document(value, tipo, report_date):
    """eMarket Storage, sezione DOCUMENTI: la riga sigillata e' quella della ricevuta (data, ora, titolo, url,
    protocollo), le liste dei documenti hanno i loro sha, e la regola PURA di IT3 (candidati_documento) ripassa
    sulla riga con lo stesso protocollo e la stessa prova. Le pagine HTML non sono sigillate (solo gli sha)."""
    from bellomberg.market_data.emarket_documenti import candidati_documento
    row = value.get("documento")
    pages = value.get("url_liste_documenti")
    digests = value.get("sha256_liste_documenti")
    if (not isinstance(row, dict) or value.get("tipo_data") != "stoccaggio_documento"
            or row.get("data") != value["data_deposito"] or row.get("ora") != value.get("ora_deposito")
            or row.get("titolo") != value["titolo"] or row.get("url") != value["url"]
            or row.get("protocollo") != value["protocollo"]
            or not isinstance(pages, list) or not pages or not isinstance(digests, dict) or set(digests) != set(pages)):
        raise ValueError("eMarket Storage document receipt is incomplete or differs from the deposit; "
                         + _DEPOSIT_PM_HINT)
    try:
        verdict = candidati_documento([deepcopy(row)], tipo, report_date,
                                      fine_esercizio=_check_fiscal_seal(value, tipo, report_date, cover_check=False))
    except Exception as exc:
        raise ValueError("eMarket Storage document rule cannot be re-applied (%s); %s"
                         % (type(exc).__name__, _DEPOSIT_PM_HINT)) from exc
    if (verdict.get("stato") != "ok" or (verdict.get("scelto") or {}).get("protocollo") != value["protocollo"]
            or verdict.get("prova") != value.get("prova_documento")):
        raise ValueError("eMarket Storage document does not name this report and period; " + _DEPOSIT_PM_HINT)


def _fetch_deposit_pdf(url):
    from bellomberg.market_data.borsa_italiana import _scarica
    status, raw, _final = _scarica(url)
    if status != 200:
        raise ValueError("eMarket SDIR deposit PDF HTTP %s; %s" % (status, _DEPOSIT_PM_HINT))
    return raw


# RIPIEGO .MI -> STATO D'ORIGINE (main 05/10; es. emittenti olandesi quotati a Milano): se gli SDIR italiani
# rispondono non_coperto o KO 'instradamento_nome_non_trovato' e l'ISIN (identita' confermata, o risolto e
# sigillato) e' di un altro paese, la data viene da depositi_ue con dopo_sdir_italiano=True. Si sigillano
# ENTRAMBI gli esiti; la riverifica rifa' senza rete la scelta del ripiego e quella della fonte UE.
_SDIR_FALLBACK_STATES = (("non_coperto", None), ("KO", "instradamento_nome_non_trovato"))
_SDIR_OUTCOME_FIELDS = ("stato", "errore", "motivo", "sdir")


def _sdir_allows_origin_fallback(outcome):
    return (isinstance(outcome, dict) and (outcome.get("stato"), None if outcome.get("stato") == "non_coperto"
            else outcome.get("errore")) in _SDIR_FALLBACK_STATES)


def _sdir_fallback_reason(outcome):
    return "sdir: %s %s (%s)" % (outcome.get("stato"), outcome.get("errore") or "", str(outcome.get("motivo"))[:200])


def _check_origin_fallback(value):
    """Riverifica PURA della scelta del ripiego: esito SDIR ammesso, motivo passato alla fonte UE identico,
    ISIN di instradamento non italiano, chiave dopo_sdir_italiano sigillata."""
    outcome = value.get("esito_sdir")
    routing = value.get("instradamento") if isinstance(value.get("instradamento"), dict) else {}
    isin = value.get("isin_instradamento")
    if (not _sdir_allows_origin_fallback(outcome) or value.get("dopo_sdir_italiano") is not True
            or routing.get("motivo_sdir") != _sdir_fallback_reason(outcome)
            or not isinstance(isin, str) or isin[:2] == "IT" or _listing_suffix(value.get("ticker")) != ".MI"):
        raise ValueError("Origin-state fallback after the Italian SDIRs is incoherent with the sealed SDIR outcome; "
                         + _DEPOSIT_PM_HINT)


def _acquire_origin_fallback(ticker, tipo, report_date, as_of, root, outcome, isin, *, issuer_name=None,
                             text=None, eu_lookup=None):
    """None se il ripiego non si applica; altrimenti la ricevuta UE sigillata con l'esito SDIR (o ValueError)."""
    if not _sdir_allows_origin_fallback(outcome) or not isinstance(isin, str) or not isin or isin[:2] == "IT":
        return None
    if eu_lookup is None:
        from bellomberg.market_data.depositi_ue import get_data_deposito as eu_lookup
    reason = _sdir_fallback_reason(outcome)

    def lookup(ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None):
        return eu_lookup(ticker, tipo=tipo, periodo_fine=periodo_fine, isin=isin, lei=lei, nome=nome,
                         dopo_sdir_italiano=True, motivo_sdir=reason)
    # Il contesto del ripiego entra nel motivo come PREFISSO costruito qui (testo controllato, mai il testo
    # di un'eccezione): i rifiuti successivi del controllo della ricevuta restano i loro, specifici.
    return _acquire_eu_deposit(ticker, tipo, report_date, as_of, root, lookup, issuer_name=issuer_name,
                               isin=isin, text=text,
                               sdir_outcome={key: deepcopy(outcome.get(key)) for key in _SDIR_OUTCOME_FIELDS},
                               reason_prefix="not found on the Italian SDIRs (%s); origin-state source: " % reason)


def _acquire_deposit_companion(ticker, tipo, report_date, as_of, root, lookup=None, *, issuer_name=None,
                               isin_lookup=None, pdf_fetch=None, text=None, fiscal_sources=(),
                               identity_isin=None, eu_lookup=None):
    """Una lettura della fonte ufficiale; solo l'esito ok viene sigillato in archivio.

    In produzione (nessuno strumento iniettato) prima risolve l'ISIN (negozio confermato ->
    automatico -> Borsa Italiana col nome dell'emittente), poi legge il deposito."""
    resolution = None
    fiscal = _fiscal_year_end_declared(tipo, report_date, text, fiscal_sources)
    production = isin_lookup is None and lookup is None
    if production:
        from bellomberg.market_data.borsa_italiana import risolvi_isin as isin_lookup
    if isin_lookup is not None:
        # Il nome viene SOLO dall'identita' confermata (ingest_document_sources: status confirmed, nome del
        # chart esatto); un nome uguale al simbolo non e' un nome: la ricerca per nome non parte (D4 05/10).
        symbol = str(ticker or "").strip().upper().rsplit(".", 1)[0]
        if not isinstance(issuer_name, str) or _normalized(issuer_name) in ("", _normalized(symbol)):
            raise ValueError("%s; ISIN resolution not attempted: the confirmed issuer name is missing or equals the "
                             "ticker symbol; %s" % (_PUBLICATION_AMBIGUOUS, _DEPOSIT_PM_HINT))
        if production:
            # Memoria dei tentativi falliti (W1 05/10: 72 h non_trovato/ambiguo, 6 h KO) e negozio confermato con
            # precedenza: un .MI non risolvibile non rifa' la rete a ogni run. Poi risolvi_isin rilegge la voce
            # dal negozio, senza rete, coi campi da sigillare.
            from bellomberg.market_data.isin_automatico import assicura_isin_it
            ensured = assicura_isin_it(ticker, nome=issuer_name, fonte_nome=_ISSUER_NAME_ORIGIN)
            if isinstance(ensured, dict) and ensured.get("stato") != "ok":
                raise ValueError("%s; ISIN resolution %s (%s): %s; %s" % (_PUBLICATION_AMBIGUOUS,
                    ensured.get("stato"), ensured.get("errore"), str(ensured.get("motivo"))[:300], _DEPOSIT_PM_HINT))
        found = isin_lookup(ticker, nome=issuer_name)
        if not isinstance(found, dict) or found.get("stato") != "ok":
            detail = found if isinstance(found, dict) else {}
            raise ValueError("%s; ISIN resolution %s (%s): %s; %s" % (_PUBLICATION_AMBIGUOUS,
                detail.get("stato", "malformed"), detail.get("errore"), str(detail.get("motivo"))[:300], _DEPOSIT_PM_HINT))
        resolution = {key: deepcopy(found.get(key)) for key in _ISIN_RESOLUTION_FIELDS}
        resolution["nome_cercato"] = issuer_name    # sigillato: con quale nome confermato si e' cercato
    if lookup is None:
        # Router SDIR (ON 05/10): eMarket o 1INFO secondo l'instradamento; il nome e' quello confermato.
        from bellomberg.market_data.sdir import get_data_deposito as _sdir_lookup
        def lookup(ticker, *, tipo, periodo_fine):
            # fine_esercizio solo per i periodi non solari, dedotta dal tipo (ON 05/10: kwarg facoltativo)
            return _sdir_lookup(ticker, tipo=tipo, periodo_fine=periodo_fine, nome=issuer_name,
                                fine_esercizio=fiscal["valore"] if fiscal else None)
    result = lookup(ticker, tipo=tipo, periodo_fine=report_date)
    choice = None
    if (isinstance(result, dict) and result.get("stato") == "ambiguo" and text is not None
            and result.get("sdir") in (None, _SDIR_EMARKET, _SDIR_1INFO)):
        # Documenti DIVERSI per lo stesso periodo (stessa regola dell'UE, main 05/10): conta quello col nome
        # della copertina. Date discordi FRA i due SDIR («eMarket SDIR + 1INFO-SDIR») non si scelgono mai.
        choice = _cover_named_candidate(text, report_date, tipo, result.get("candidati"))
    if not isinstance(result, dict) or not (result.get("stato") == "ok" or choice is not None
            or (result.get("stato") == "STALE" and result.get("stato_originale") == "ok")):
        detail = result if isinstance(result, dict) else {}
        origin_isin = identity_isin or (resolution or {}).get("isin")
        fallback = _acquire_origin_fallback(ticker, tipo, report_date, as_of, root, detail, origin_isin,
                                            issuer_name=issuer_name, text=text, eu_lookup=eu_lookup)
        if fallback is not None:
            return fallback
        label = (detail.get("sdir") or "SDIR") if "sdir" in detail else _SDIR_EMARKET
        extra = ("; no single candidate carries the document name read on the cover"
                 if detail.get("stato") == "ambiguo" else "")
        raise ValueError("%s; %s deposit %s (%s): %s%s; %s" % (_PUBLICATION_AMBIGUOUS, label,
            detail.get("stato", "malformed"), detail.get("errore"), str(detail.get("motivo"))[:300], extra,
            _DEPOSIT_PM_HINT))
    value = {"contract": _DEPOSIT_CONTRACT, **{key: deepcopy(result.get(key)) for key in _DEPOSIT_FIELDS}}
    if result.get("prova") == "documento":
        value.update({key: deepcopy(result.get(key)) for key in _DEPOSIT_DOCUMENT_FIELDS})
    if "sdir" in result:
        value.update({key: deepcopy(result.get(key)) for key in _DEPOSIT_SDIR_FIELDS})
        routing = result.get("instradamento")
        value["instradamento"] = ({key: deepcopy(routing.get(key)) for key in _ROUTING_FIELDS}
                                  if isinstance(routing, dict) else routing)
    value["isin_resolution"] = resolution
    if fiscal is not None:
        value["fine_esercizio"] = fiscal        # solo esercizi non solari: le ricevute solari restano identiche
    if choice is not None:
        value["candidati"] = deepcopy(result.get("candidati"))
        value["scelta_copertina"] = choice
    if not result.get("risposte_salvate"):
        # RV-D4 P1: senza risposte salvate la scelta non si rifa' senza rete -> nessuna ricevuta (rifiuto dichiarato).
        raise ValueError("%s; SDIR deposit receipt without saved source responses cannot be re-verified offline; %s"
                         % (_PUBLICATION_AMBIGUOUS, _DEPOSIT_PM_HINT))
    # AGGIUNTA 5: il ritorno INTERO (risposte salvate comprese) si sigilla e si riverifica con
    # sdir.riverifica_deposito, che rifa' la scelta completa dalle risposte. OBBLIGATORIO (RV-D4 P1).
    value["ricevuta_fonte"] = deepcopy(result)
    if value.get("prova") == "testo_pdf" and _deposit_sdir(value) == _SDIR_EMARKET and choice is None:
        # Si archiviano i byte del PDF del comunicato: devono essere quelli letti dalla fonte.
        expected = (value.get("sha256_pdf") or {}).get(value.get("url"))
        pdf = (pdf_fetch or _fetch_deposit_pdf)(value.get("url"))
        if not isinstance(pdf, bytes) or sha256(pdf).hexdigest() != expected:
            raise ValueError("eMarket SDIR deposit PDF differs from the bytes read by the source; " + _DEPOSIT_PM_HINT)
        _archive_bytes(root / "publication-receipts", pdf, ".pdf")
    _check_deposit_value(value, ticker, tipo, report_date, as_of, root, text)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("SDIR deposit receipt is larger than the archive limit (%d bytes); %s"
                         % (len(raw), _DEPOSIT_PM_HINT))
    _path, digest = _archive_bytes(root / "publication-receipts", raw, ".json")
    return {**value, "sha256": digest}


def verify_deposit_companion(sealed, root, ticker, tipo, report_date, as_of, text=None, identity=None):
    """Senza rete: byte archiviati == sigillo, contenuto == sigillo, regola ripassata."""
    if not isinstance(sealed, dict) or not isinstance(sealed.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", sealed["sha256"]):
        raise ValueError("eMarket SDIR deposit receipt is malformed")
    path = (Path(root) / "publication-receipts" / (sealed["sha256"] + ".json")).resolve()
    if not path.is_relative_to((Path(root) / "publication-receipts").resolve()):
        raise ValueError("eMarket SDIR deposit receipt escaped the server archive")
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ValueError("eMarket SDIR deposit receipt archive is missing or unreadable") from exc
    if len(raw) > MAX_BYTES or sha256(raw).hexdigest() != sealed["sha256"]:
        raise ValueError("Archived eMarket SDIR deposit receipt bytes changed")
    value = json.loads(raw)
    # Il sigillo e' {archivio + sha256}: stesse CHIAVI e stessi valori (anche «sdir» presente/assente).
    if not same_exact_value(value, {key: item for key, item in sealed.items() if key != "sha256"}):
        raise ValueError("Archived eMarket SDIR deposit receipt metadata changed")
    if isinstance(value, dict) and value.get("contract") == _EU_DEPOSIT_CONTRACT:
        _check_eu_deposit_value(value, ticker, tipo, report_date, as_of, text,
                                (identity or {}).get("isin"), identity is not None,
                                identity_name=(identity or {}).get("name"))
    else:
        _check_deposit_value(value, ticker, tipo, report_date, as_of, root, text)
    return deepcopy(sealed)


def _check_eu_deposit_value(value, ticker, tipo, report_date, as_of, text=None, identity_isin=None,
                            check_identity_isin=False, *, identity_name=None):
    """Riverifica PURA della ricevuta UE sigillata: emittente, tipo, periodo, natura della data, data dopo il
    periodo e non dopo il cutoff, poi la riverifica senza rete del modulo del paese (depositi_ue). Un 'ambiguo'
    vale solo con la scelta col nome in copertina, RIFATTA qui sul testo del documento archiviato."""
    from bellomberg.market_data.depositi_ue import riverifica_ricevuta
    choice = value.get("scelta_copertina") if isinstance(value, dict) else None
    if isinstance(value, dict) and value.get("stato") == "ambiguo" and choice is not None:
        if text is None:
            raise ValueError("EU deposit choice by cover name cannot be re-verified without the document text; "
                             + _DEPOSIT_PM_HINT)
        again = _cover_named_candidate(text, report_date, tipo, value.get("candidati"),
                                       _eu_document_names(value.get("paese")))
        if not same_exact_value(again, choice):
            raise ValueError("EU deposit choice by cover name differs from the document cover; " + _DEPOSIT_PM_HINT)
    elif isinstance(value, dict) and choice is not None:
        raise ValueError("EU deposit receipt carries a cover-name choice without ambiguity; " + _DEPOSIT_PM_HINT)
    sealed_value, value = value, _deposit_effective(value)
    if (not isinstance(value, dict) or value.get("contract") != _EU_DEPOSIT_CONTRACT
            or value.get("stato") not in (("ambiguo",) if choice is not None else ("ok",))
            or value.get("ticker") != (ticker or "").strip().upper() or value.get("tipo") != tipo
            or value.get("periodo_fine") != report_date or value.get("natura_data") not in _EU_NATURE_LABELS
            or any(not isinstance(value.get(key), str) or not value[key].strip()
                   for key in ("data_deposito", "titolo", "fonte", "fonte_modulo"))):
        raise ValueError("EU official deposit receipt conflicts with the document issuer, type or period; "
                         + _DEPOSIT_PM_HINT)
    if not (date.fromisoformat(report_date) < date.fromisoformat(value["data_deposito"])
            and _deposit_instant(value) <= _cutoff_instant(as_of)):
        raise ValueError("EU official deposit date is not after the reporting period or is after the cutoff; "
                         + _DEPOSIT_PM_HINT)
    fallback = sealed_value.get("esito_sdir") is not None
    if fallback:
        _check_origin_fallback(sealed_value)
    _check_eu_identity(sealed_value, ticker, identity_isin, check_identity_isin, identity_name=identity_name)
    receipt = {key: item for key, item in sealed_value.items()
               if key not in _EU_LOCAL_KEYS}
    ok, reason = riverifica_ricevuta(receipt, ticker=value["ticker"], tipo=tipo, periodo_fine=report_date,
                                     dopo_sdir_italiano=fallback)
    if ok is not True:
        raise ValueError("EU official deposit receipt failed the offline re-verification (%s); %s"
                         % (str(reason)[:200], _DEPOSIT_PM_HINT))
    _check_eu_source_declarations(value, tipo)


# Fuso con cui ogni modulo UE dichiara l'ora di un esito 'ok' (RV-D4 P2-a: il 'fuso' sigillato non lo ricalcola
# nessuno). Costante del modulo dove esiste; letterale dove il modulo lo scrive a mano (dichiarato: da allineare se
# il modulo cambia). Un modulo fuori elenco = rifiuto.
_EU_MODULE_TIMEZONES = {"bellomberg.market_data.ue_afm": ("FUSO", None),
                        "bellomberg.market_data.ue_fsma": ("FUSO", None),
                        "bellomberg.market_data.ue_newsweb": ("FUSO", None),
                        "bellomberg.market_data.ue_amf": (None, "UTC"),
                        "bellomberg.market_data.ue_nasdaq_nordic": (None, "UTC"),
                        "bellomberg.market_data.ue_cnmv": (None, "Europe/Madrid")}


def _eu_expected_timezone(module_name):
    import importlib
    entry = _EU_MODULE_TIMEZONES.get(module_name)
    if entry is None:
        return None
    constant, literal = entry
    return getattr(importlib.import_module(module_name), constant, None) if constant else literal


def _check_eu_source_declarations(value, tipo):
    """Fuso e natura della data DICHIARATI dalla ricevuta = quelli del modulo / del registro del paese."""
    from bellomberg.market_data.depositi_ue import COPERTURA_UE
    country = COPERTURA_UE.get(value.get("paese") or "") or {}
    nature = (country.get("natura_data_per_tipo") or {}).get(tipo, country.get("natura_data"))
    zone = _eu_expected_timezone(value.get("fonte_modulo"))
    if not zone or value.get("fuso") != zone:
        raise ValueError("EU deposit time zone differs from the one its source module declares; " + _DEPOSIT_PM_HINT)
    if not nature or value.get("natura_data") != nature:
        raise ValueError("EU deposit date nature differs from the country registry; " + _DEPOSIT_PM_HINT)


def _check_eu_identity(value, ticker, identity_isin=None, check_identity_isin=False, *, identity_name=None):
    """Identita' UE sigillata: riverifica senza rete del modulo (ID-UE) con lo stesso ISIN chiesto, poi ISIN e LEI
    passati alla fonte del deposito = quelli dell'identita' (solo se 'ok'; altrimenti nessuno dei due)."""
    from bellomberg.market_data.identita_ue import riverifica_identita
    identity_eu = value.get("identita_ue")
    if not isinstance(identity_eu, dict):
        raise ValueError("EU issuer identity (GLEIF) is missing from the deposit receipt; " + _DEPOSIT_PM_HINT)
    if check_identity_isin and (identity_isin is not None or value.get("esito_sdir") is None) and (
            identity_eu.get("isin_chiamante") != identity_isin):
        # RV-D4 P3-5: l'ISIN chiesto a GLEIF e' quello dell'identita' confermata (non solo quello della ricevuta)
        raise ValueError("EU issuer identity (GLEIF) was resolved with an ISIN other than the confirmed identity's; "
                         + _DEPOSIT_PM_HINT)
    # RV-ID: il nome con cui si riverifica e' quello dell'IDENTITA' CONFERMATA (chi chiama), non quello della
    # ricevuta (controllo circolare); la ricevuta deve riportare lo stesso nome. Senza identita' (collettore,
    # che gira DOPO verify_document_receipt) resta il nome della ricevuta: limite dichiarato.
    if identity_name is not None and value.get("nome") != identity_name:
        raise ValueError("EU deposit receipt issuer name differs from the confirmed identity; " + _DEPOSIT_PM_HINT)
    name = identity_name if identity_name is not None else value.get("nome")
    ok, reason = riverifica_identita(identity_eu, ticker=(ticker or "").strip().upper(), nome=name,
                                     isin=identity_eu.get("isin_chiamante"))
    expected = ((identity_eu.get("isin"), identity_eu.get("lei")) if identity_eu.get("stato") == "ok"
                else (None, None))
    if ok is not True or (value.get("isin"), value.get("lei")) != expected:
        raise ValueError("EU issuer identity (GLEIF) failed the offline re-verification or differs from the "
                         "deposit lookup (%s); %s" % (str(reason)[:160], _DEPOSIT_PM_HINT))


def _acquire_eu_deposit(ticker, tipo, report_date, as_of, root, lookup=None, *, issuer_name=None, isin=None,
                        text=None, sdir_outcome=None, reason_prefix=""):
    """Una lettura dell'instradatore UE con l'IDENTITA' CONFERMATA (nome; ISIN solo se l'identita' lo ha, mai dal
    simbolo); solo l'esito ok, riverificato senza rete, viene sigillato INTERO in archivio."""
    if lookup is None:
        from bellomberg.market_data.depositi_ue import get_data_deposito as lookup
    # Identita' UE (ID-UE 05/10, solo GLEIF): ISIN e LEI si usano SOLO con stato 'ok'; l'esito si sigilla intero.
    from bellomberg.market_data.identita_ue import risolvi_identita_ue
    identity_eu = risolvi_identita_ue(ticker, nome=issuer_name, isin=isin)
    if not isinstance(identity_eu, dict):
        raise ValueError("%s; EU issuer identity (GLEIF) is malformed; %s" % (_PUBLICATION_AMBIGUOUS, _DEPOSIT_PM_HINT))
    if identity_eu.get("stato") == "STALE":
        # ID-UE/main 05/10: un'identita' STALE non si sigilla (va riletta): rifiuto dichiarato
        raise ValueError("%s; %sEU issuer identity (GLEIF) is STALE and cannot be sealed: %s; %s"
                         % (_PUBLICATION_AMBIGUOUS, reason_prefix, str(identity_eu.get("motivo"))[:200], _DEPOSIT_PM_HINT))
    identity_ok = identity_eu.get("stato") == "ok"
    if not identity_ok:
        reason_prefix += ("EU issuer identity (GLEIF) %s (%s): %s; "
                          % (identity_eu.get("stato"), identity_eu.get("errore"), str(identity_eu.get("motivo"))[:200]))
    result = lookup(ticker, tipo=tipo, periodo_fine=report_date, isin=identity_eu.get("isin") if identity_ok else None,
                    lei=identity_eu.get("lei") if identity_ok else None, nome=issuer_name)
    choice = None
    if (isinstance(result, dict) and result.get("stato") == "ambiguo" and text is not None
            and result.get("errore") != "nome_non_univoco" and not result.get("omonimi")):
        # Documenti DIVERSI per lo stesso periodo: conta quello che la Trade Idea usa (nome in copertina).
        # Un ambiguo d'IDENTITA' (omonimi AFM) non si sceglie mai.
        choice = _cover_named_candidate(text, report_date, tipo, result.get("candidati"),
                                        _eu_document_names(result.get("paese")))
    if not isinstance(result, dict) or (result.get("stato") != "ok" and choice is None):
        detail = result if isinstance(result, dict) else {}
        extra = ("; no single candidate carries the document name read on the cover"
                 if detail.get("stato") == "ambiguo" else "")
        raise ValueError("%s; %sEU official deposit %s (%s): %s%s; %s" % (_PUBLICATION_AMBIGUOUS, reason_prefix,
            detail.get("stato", "malformed"), detail.get("errore"), str(detail.get("motivo"))[:300], extra,
            _DEPOSIT_PM_HINT))
    value = {"contract": _EU_DEPOSIT_CONTRACT, **deepcopy(result)}
    if choice is not None:
        value["scelta_copertina"] = choice
    if sdir_outcome is not None:
        value["esito_sdir"] = sdir_outcome
    value["identita_ue"] = deepcopy(identity_eu)
    _check_eu_deposit_value(value, ticker, tipo, report_date, as_of, text, identity_name=issuer_name)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("EU official deposit receipt is larger than the archive limit (%d bytes); %s"
                         % (len(raw), _DEPOSIT_PM_HINT))
    _path, digest = _archive_bytes(root / "publication-receipts", raw, ".json")
    return {**value, "sha256": digest}


_COVER_PERIOD_LOCATOR = "cover_report_title_period/1"
# INSTRADAMENTO PER LISTINO (D4 05/10, aggiornato D4b): da dove viene la data ufficiale di deposito.
#   .MI (paese IT)                 -> router SDIR italiani `sdir.get_data_deposito` (eMarket SDIR / 1INFO-SDIR);
#   paese UE coperto (depositi_ue) -> `depositi_ue.get_data_deposito` (paese dall'ISIN se l'identita' lo ha,
#                                     altrimenti dal listino: lo decide `paese_di_instradamento`, registro unico);
#   altrimenti                     -> nessuna fonte: limite DICHIARATO (`_publication_limit_note`).
# filings.xbrl.org NON e' agganciato: `date_added` e' la data di inserimento nel repository, non la data di
# pubblicazione (misura della bozza D4 05/10, non rifatta qui).
_DEPOSIT_ROUTE_IT, _DEPOSIT_ROUTE_EU = "sdir_it", "depositi_ue"


def _listing_suffix(ticker):
    ticker = str(ticker or "").strip().upper()
    return "." + ticker.rsplit(".", 1)[1] if "." in ticker else None


def _deposit_route(ticker, isin=None):
    """'sdir_it' | 'depositi_ue' | None. Nessuna rete: registri di depositi_ue e mercati.MERCATI."""
    if _listing_suffix(ticker) is None:
        return None             # USA: la data viene dal catalogo SEC, non da un deposito
    from bellomberg.market_data.depositi_ue import COPERTURA_UE, paese_di_instradamento
    route = paese_di_instradamento(ticker, isin)
    if route["errore"]:
        return None
    if route["paese"] == "IT":
        return _DEPOSIT_ROUTE_IT if _listing_suffix(ticker) == ".MI" else None
    return _DEPOSIT_ROUTE_EU if (COPERTURA_UE.get(route["paese"] or "") or {}).get("modulo") else None


def _route_document_names(ticker, isin=None):
    """Nomi locali per il tipo in copertina: solo per i listini instradati a depositi_ue."""
    if _deposit_route(ticker, isin) != _DEPOSIT_ROUTE_EU:
        return None
    from bellomberg.market_data.depositi_ue import paese_di_instradamento
    return _eu_document_names(paese_di_instradamento(ticker, isin)["paese"])


def _publication_limit_note(ticker, isin=None):
    """Limite DICHIARATO per un listino estero senza fonte ufficiale agganciata; None per i listini con fonte e per
    quelli senza suffisso (USA: catalogo SEC). Mercato e paese dai registri unici (`copertura.copertura_usa` ->
    `mercati.MERCATI`, `depositi_ue.COPERTURA_UE`), mai da una lista di suffissi copiata qui."""
    suffix = _listing_suffix(ticker)
    if suffix is None or _deposit_route(ticker, isin) is not None:
        return None
    from bellomberg.market_data.copertura import copertura_usa
    from bellomberg.market_data.depositi_ue import COPERTURA_UE, paese_di_instradamento
    market = copertura_usa(ticker, "official publication-date source").get("mercato")
    route = paese_di_instradamento(ticker, isin)
    country = COPERTURA_UE.get(route["paese"] or "") or {}
    if route["errore"]:
        reason = "the identity ISIN cannot route the listing (%s)" % str(route["perche"])[:120]
    elif country.get("motivo_limite_en"):
        reason = country["motivo_limite_en"]
    elif market:
        reason = "official deposit sources are wired only for Italian SDIR and covered EU countries"
    else:
        return None     # suffisso non da listino (classe di azioni?): nessuna affermazione sul mercato
    return "no official publication-date source (%s, %s): %s; %s" % (market or "?", suffix, reason, _DEPOSIT_PM_HINT)


_ISSUER_FULL_TEXT_LOCATOR = "confirmed_issuer_name_in_full_text/1"
_ISSUER_NAME_ORIGIN = "identit\u00e0 confermata (identity resolver)"
_ISSUER_NAME_SOURCE = ("confirmed issuer identity (identity resolver); the isin_it store holds ISIN and eMarket id "
                       "only, no issuer-name field")


def _cover_period_context(text):
    """Nome della relazione + periodo nella copertina (primi 2.000 caratteri), es. «Relazione finanziaria
    semestrale al 30 giugno 2026» / «HALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026». Ritorna [(data, citazione)];
    la citazione e' unica nel testo intero (altrimenti il prefisso dall'inizio del documento)."""
    names = "|".join(pattern for patterns in _deposit_document_types().values() for pattern in patterns)
    # Qualificatori ammessi fra nome e «al» (D4 05/10): «Relazione finanziaria semestrale consolidata al ...».
    pattern = re.compile(r"(?:" + names + r")(?:\s+(?:consolidat[ao]|consolidated|condensed|abbreviat[ao])){0,2}"
                         r"\s+(?:al|at|as\s+at|as\s+of)\s+(?P<date>" + _DATE_PATTERN + r")", re.I)
    result = []
    for match in pattern.finditer(text[:2000]):
        try:
            value = _parse_date_token(" ".join(match.group("date").split()))
        except (ValueError, KeyError):
            continue
        quote = match.group(0)
        result.append((value, quote if text.count(quote) == 1 else text[:match.end()]))
    return result


def _issuer_full_text_locator(text, name):
    """Prima occorrenza del nome confermato (tutti i token, in ordine) in TUTTO il testo; None se assente."""
    tokens = re.findall(r"[^\W_]+", name or "")
    if not tokens:
        return None
    pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(token) for token in tokens) + r"(?!\w)"
    for match in re.finditer(pattern, text, re.I):
        for width in (60, 200, 1000):
            start, end = max(0, match.start() - width), min(len(text), match.end() + width)
            if text.count(text[start:end]) == 1:
                return text[start:end]
    return None


def _page_of_offset(extracted, offset):
    for row in extracted.get("riferimenti") or []:
        if row["inizio"] <= offset <= row["fine"]:
            return row["pagina"]
    return None


def verify_european_locators(text, extracted, verification, metadata):
    """Riverifica senza rete delle prove dichiarate per periodo (copertina) ed emittente (testo intero)."""
    period = verification.get("report_date_locator")
    if period is not None:
        rows = _cover_period_context(text)
        if (not isinstance(period, dict) or period.get("basis") != _COVER_PERIOD_LOCATOR
                or len({row[0] for row in rows}) != 1 or rows[0][0] != metadata.get("report_date")
                or period.get("value") != metadata.get("report_date") or period.get("quote") != rows[0][1]):
            raise ValueError("declared cover reporting period differs from the document cover")
    issuer = verification.get("issuer_locator")
    if issuer is not None:
        quote = issuer.get("quote") if isinstance(issuer, dict) else None
        name = issuer.get("name") if isinstance(issuer, dict) else None
        if (issuer.get("basis") != _ISSUER_FULL_TEXT_LOCATOR or not isinstance(quote, str) or not isinstance(name, str)
                or text.count(quote) != 1 or _issuer_full_text_locator(text, name) != quote
                or text.find(quote) != issuer.get("offset")
                or _page_of_offset(extracted, issuer["offset"]) != issuer.get("page")
                or name != metadata.get("issuer")):
            raise ValueError("declared issuer full-text locator differs from the document text")


def _resolve_metadata_claims(source, text, identity, publication_receipt=None, legal_identity_resolver=None,
                             *, legacy_locators=False, publication_matches=None, deposit_resolver=None,
                             european_locators=False, publication_limit_note=None, document_names=None):
    resolved = deepcopy(source)
    origins = {}
    deposit_pending = False
    sec_cover = None if legacy_locators else _primary_sec_cover(text)
    if sec_cover:
        if _normalized(sec_cover['issuer']) != _normalized(identity.get('name') or ''):
            raise ValueError('SEC cover registrant differs from the confirmed issuer identity')
        if publication_receipt and sec_cover['form'] != publication_receipt['entry']['form']:
            raise ValueError('SEC cover form differs from the primary SEC publication companion')
    for kind, field in (("publication", "published_at"), ("report_date", "report_date")):
        quote = source.get(kind + "_quote")
        claimed = source.get(field)
        if kind == "publication" and publication_receipt is not None and not quote:
            value = publication_receipt["entry"]["filed_date"]
            if claimed is not None and claimed != value:
                raise ValueError("PM publication date conflicts with the primary SEC catalog entry")
            resolved[field], resolved["publication_quote"] = value, None
            origins[kind] = "verified_pm_date_against_sec_catalog" if claimed else "automatic_sec_catalog_receipt"
            continue
        if kind == 'publication':
            matches = _primary_publication_context(text) if publication_matches is None else publication_matches
            if len({item[0] for item in matches}) != 1:
                if deposit_resolver is None or legacy_locators:
                    raise ValueError('Publication requires one unambiguous primary header or cover date; optional claims cannot resolve contradictory publication facts'
                                     + (('; ' + publication_limit_note) if publication_limit_note else ''))
                # Copertina senza data univoca: la data la da' il deposito ufficiale (dopo il periodo).
                deposit_pending = True
                continue
            if quote:
                _proof(text, quote)
                matches = [item for item in matches if item[1].strip() in quote]
        else:
            financial_headers = ([(sec_cover['report_date'], sec_cover['report_date_quote'])] if sec_cover
                else _primary_financial_report_context(text))
            if financial_headers:
                if len({item[0] for item in financial_headers}) != 1:
                    raise ValueError('Reporting period has contradictory primary financial headers')
                matches = (_dated_context(quote, kind) + [item for item in financial_headers if item[1].strip() in quote]) if quote else financial_headers
                matches = [item for item in matches if item[0] == financial_headers[0][0]]
            else:
                matches = _dated_context(quote or text, kind)
        if claimed:
            date.fromisoformat(claimed)
            matches = [item for item in matches if item[0] == claimed]
        dates = {item[0] for item in matches}
        if kind == 'report_date' and european_locators and (len(dates) != 1 or not matches):
            # Decisione PM 04/10: nome della relazione + periodo IN COPERTINA, solo se univoco.
            cover = _cover_period_context(text)
            if len({item[0] for item in cover}) > 1:
                raise ValueError('Reporting period is contradictory on the document cover')
            if cover and (not claimed or cover[0][0] == claimed):
                resolved[field], resolved[kind + '_quote'] = cover[0]
                if quote:
                    resolved['request_report_date_quote'] = quote
                _proof(text, resolved[kind + '_quote'])
                origins[kind] = _COVER_PERIOD_LOCATOR
                continue
        if len(dates) != 1 or not matches:
            raise ValueError(("Publication" if kind == "publication" else "Reporting period")
                + " requires a unique explicit dated primary-text disclosure; optional claims must be verified")
        value, extracted_quote = matches[0]
        resolved[field] = value
        resolved[kind + "_quote"] = quote or extracted_quote
        _proof(text, resolved[kind + "_quote"])
        origins[kind] = "verified_pm_locator" if quote or claimed else "automatic_primary_text_locator"
    if deposit_pending:
        tipo = _deposit_document_type(text, resolved["report_date"], document_names)
        if tipo is None:
            raise ValueError(_PUBLICATION_AMBIGUOUS + "; eMarket SDIR deposit not searched: report type not unique on "
                             "the cover (" + _DOCUMENT_TYPE_RULE + "); " + _DEPOSIT_PM_HINT)
        sealed = deposit_resolver(resolved["report_date"], tipo, text)
        if source.get("published_at") and source["published_at"] != _deposit_public_date(sealed):
            raise ValueError("PM publication date conflicts with the eMarket SDIR deposit date")
        resolved["published_at"], resolved["publication_quote"] = _deposit_public_date(sealed), None
        resolved["deposit_receipt"] = sealed
        if source.get("publication_quote"):
            # La citazione del PM non e' una data di pubblicazione: dichiarata, non usata come prova.
            resolved["request_publication_quote"] = source["publication_quote"]
        origins["publication"] = _deposit_basis(sealed)
    if resolved.get("publication_quote") and resolved["publication_quote"] == resolved["report_date_quote"]:
        raise ValueError("Publication and reporting period require distinct contextual evidence")
    issuer_quote = source.get("issuer_quote") or (sec_cover['issuer_quote'] if sec_cover else None)
    name = identity.get("name") or ""
    legal_identity = None
    def legal_quote(existing=None):
        nonlocal legal_identity
        binding = legal_identity_resolver(resolved) if legal_identity_resolver else None
        if (not isinstance(binding,dict) or binding.get('contract') != 'primary_legal_identity/1'
                or name not in binding.get('names',[]) or binding.get('entity') not in binding.get('names',[])):
            if existing is not None:
                raise ValueError("Primary issuer quotation differs from the confirmed issuer identity")
            raise ValueError("Confirmed issuer name is absent from the primary document header")
        explicit = binding.get('explicit_name')
        if not isinstance(explicit,str) or text.count(explicit)!=1:
            raise ValueError('Primary legal-name binding lacks a unique explicit document locator')
        if existing is not None:
            if explicit not in existing and binding['entity'] not in existing:
                raise ValueError('Optional issuer quotation differs from the recompiled primary legal-name binding')
            quote = existing
        else:
            offset=text.index(explicit)
            quote=text[max(0,offset-30):min(len(text),offset+len(explicit)+80)]
        legal_identity=deepcopy(binding)
        return quote
    if european_locators and issuer_quote and text.count(issuer_quote) != 1:
        # Citazione facoltativa non trovata: dichiarata, non usata come prova.
        resolved['request_issuer_quote'] = issuer_quote
        issuer_quote = None
    if not issuer_quote:
        # SEC catalog/display names differ in commas and periods. Match every
        # name token in order; never remove a legal suffix or insert an alias.
        tokens = re.findall(r"[^\W_]+", name)
        pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(token) for token in tokens) + r"(?!\w)"
        matches = list(re.finditer(pattern, text[:12000], re.I)) if tokens else []
        if matches:
            match = matches[0]
            start, end = max(0, match.start() - 60), min(len(text), match.end() + 60)
            issuer_quote = text[start:end]
        elif european_locators and _issuer_full_text_locator(text, name):
            # Decisione PM 04/10: il nome confermato si cerca in TUTTO il documento.
            issuer_quote = _issuer_full_text_locator(text, name)
            origins['issuer'] = _ISSUER_FULL_TEXT_LOCATOR
        else:
            issuer_quote=legal_quote()
    _proof(text, issuer_quote)
    if not name or _normalized(name) not in _normalized(issuer_quote):
        if legal_identity is None:
            issuer_quote=legal_quote(issuer_quote)
    _proof(text,issuer_quote)
    resolved["issuer_quote"] = issuer_quote
    if legal_identity is not None:
        resolved['primary_legal_identity']=legal_identity
        origins['issuer']='verified_primary_legal_identity'
    elif origins.get('issuer') != _ISSUER_FULL_TEXT_LOCATOR:
        origins["issuer"] = "verified_pm_locator" if source.get("issuer_quote") else "automatic_primary_text_locator"
    return resolved, origins


TEXTLESS_PAGES_MAX_PERCENT = 5
TEXTLESS_PAGES_POLICY = "pdf_textless_pages_max_5_percent_no_images/1"


def _pdf_page_image_count(page, reader):
    """Images a physical page can paint: Image XObjects, also nested in Form
    XObjects, tiling patterns, Type3 glyphs and annotation appearances, plus
    inline images (BI) in every content stream reached. Conservative: an image
    declared in a reachable resource counts even if never painted."""
    from pypdf.generic import ContentStream, StreamObject
    seen, count = set(), 0

    def resolved(value):
        value = value.get_object() if hasattr(value, "get_object") else value
        return value if value is not None else {}

    def inline(stream):
        nonlocal count
        if isinstance(stream, StreamObject) and stream.get_data().strip():
            count += sum(1 for _operands, operator in ContentStream(stream, reader).operations
                         if operator == b"INLINE IMAGE")

    def stream_and_resources(obj, depth):
        obj = obj.get_object() if obj is not None else None
        if obj is None or not hasattr(obj, "get"):
            return
        if depth > 32:
            raise ValueError("PDF resource nesting is too deep to verify images")
        key = getattr(getattr(obj, "indirect_reference", None), "idnum", None) or id(obj)
        if key in seen:
            return
        seen.add(key)
        inline(obj)
        resources(obj.get("/Resources"), depth + 1)

    def resources(res, depth):
        nonlocal count
        res = resolved(res)
        if not res:
            return
        if depth > 32:
            raise ValueError("PDF resource nesting is too deep to verify images")
        for group in ("/XObject", "/Pattern"):
            for value in resolved(res.get(group)).values():
                value = resolved(value)
                if value.get("/Subtype") == "/Image":
                    count += 1
                else:
                    stream_and_resources(value, depth + 1)
        for state in resolved(res.get("/ExtGState")).values():
            # Una scansione puo' essere dipinta come soft mask (/SMask /G).
            state = resolved(state)
            smask = resolved(state.get("/SMask"))
            if hasattr(smask, "get"):
                stream_and_resources(smask.get("/G"), depth + 1)
            font_entry = state.get("/Font")  # [font size]: anche un Type3 puo' arrivare da qui
            if font_entry is not None:
                resources({"/Font": {"/F": resolved(font_entry)[0]}}, depth + 1)
        for font in resolved(res.get("/Font")).values():
            font = resolved(font)
            if font.get("/Subtype") == "/Type3":
                resources(font.get("/Resources"), depth + 1)
                for glyph in resolved(font.get("/CharProcs")).values():
                    stream_and_resources(glyph, depth + 1)

    contents = page.get_contents()
    if contents is not None:
        count += sum(1 for _operands, operator in ContentStream(contents, reader).operations
                     if operator == b"INLINE IMAGE")
    resources(page.get("/Resources"), 0)
    for annotation in resolved(page.get("/Annots")) or []:
        for appearance in resolved(resolved(annotation).get("/AP")).values():
            appearance = resolved(appearance)
            if isinstance(appearance, StreamObject):
                stream_and_resources(appearance, 1)
            else:
                for state in appearance.values():
                    stream_and_resources(state, 1)
    return count


def same_exact_value(left, right):
    """Uguaglianza con TIPI esatti: 0 != 0.0 != False, 41 != 41.0 (== di Python li confonde)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_exact_value(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(same_exact_value(a, b) for a, b in zip(left, right))
    return left == right


def textless_pages_declaration(raw, extracted):
    """None when every page has text; else the exact declaration of accepted
    PDF pages without extractable text. Raises when a threshold is crossed.

    Accepted only for PDFs whose pages without text are <= 5% of all pages
    (integer arithmetic) and each paints no image: a textless page with an
    image may be a scan and still requires OCR, so it remains a rejection.
    """
    pages = extracted.get("pagine_senza_testo") or []
    if not pages:
        return None
    total = extracted.get("pagine")
    if extracted.get("formato") != "pdf" or type(total) is not int or total < 1:
        raise ValueError("Document contains pages without extractable text outside a measured PDF")
    if len(pages) * 100 > TEXTLESS_PAGES_MAX_PERCENT * total:
        raise ValueError("PDF pages without extractable text exceed the 5%% limit: %d of %d pages %s"
                         % (len(pages), total, pages))
    from io import BytesIO
    from pypdf import PdfReader
    try:
        reader = PdfReader(BytesIO(raw))
        images = {number: _pdf_page_image_count(reader.pages[number - 1], reader) for number in pages}
    except Exception as exc:
        raise ValueError("Images on PDF pages without extractable text cannot be verified ("
                         + type(exc).__name__ + ")") from exc
    with_images = [number for number, value in images.items() if value]
    if with_images:
        raise ValueError("PDF pages without extractable text contain images (possible scan, OCR required): pages %s"
                         % with_images)
    return {"pages": list(pages), "pages_total": total, "images_on_pages": 0,
            "policy": TEXTLESS_PAGES_POLICY,
            "limitation": "Content of these pages is not verified without OCR; accepted only because they are "
                          "at most 5% of the pages and contain no image."}


def _verified_candidate(source, fetched, identity, as_of, root, publication_receipt=None,
                        *, text_extraction=None, deposit_resolver=None):
    from bellomberg.market_data.lettore_trimestrali import (estrai_testo, HTML_TEXT_EXTRACTOR,
        LEGACY_HTML_TEXT_EXTRACTOR, COMPANY_TEXT_EXTRACTOR)
    if text_extraction is None:
        text_extraction = COMPANY_TEXT_EXTRACTOR
    legacy = text_extraction == LEGACY_HTML_TEXT_EXTRACTOR
    path = Path(fetched["path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Document archive escapes the server root or size limit")
    try:
        raw = read_archived_document(path)
    except OSError as exc:
        raise ValueError("Archived primary document is missing or unreadable") from exc
    if sha256(raw).hexdigest() != fetched.get("sha256") or len(raw) != fetched.get("bytes"):
        raise ValueError("Downloaded document SHA256 differs from archived bytes")
    if raw.startswith(b"%PDF-"):
        from io import BytesIO
        from pypdf import PdfReader
        try:
            pdf = PdfReader(BytesIO(raw), strict=True)
            if pdf.is_encrypted or len(pdf.pages) > MAX_PAGES:
                raise ValueError("Encrypted or excessive-page PDF document is unsupported")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Primary PDF structure cannot be read safely") from exc
    extracted = estrai_testo(str(path), contenuto=raw, html_extractor=text_extraction)
    text = extracted.get("testo", "")
    if (extracted.get("stato") != "ok" or not text.strip() or len(text) > MAX_TEXT
            or (extracted.get("pagine") or 0) > MAX_PAGES):
        raise ValueError("Document extraction is incomplete or exceeds the text/page limit")
    # Pagine senza testo: ammesse solo <=5% e senza immagini, poi DICHIARATE.
    textless_pages = textless_pages_declaration(raw, extracted)
    publication_matches = (_html_publication_context(raw, text)
        if text_extraction == COMPANY_TEXT_EXTRACTOR and extracted.get('formato') == 'html' else None)
    def legal_identity_resolver(located):
        from bellomberg.valuation.property_reported_sources import normalize_reported_property, ENTITY, ON, RAW_SHA, SOURCE_URL
        if source['url'] != SOURCE_URL or fetched.get('sha256') != RAW_SHA:
            return None
        primary={'id':RAW_SHA,'url':SOURCE_URL,'published_at':located['published_at'],
            'document_sha256':RAW_SHA,'sha256':sha256(text.encode()).hexdigest(),
            'text':text,'archive_path':str(path),'metadata':{'report_date':located['report_date']}}
        compiled=normalize_reported_property(primary,entity=ENTITY,on=ON,as_of=as_of)
        if compiled.get('status') != 'ready':
            return None
        body=json.loads(compiled['documents'][0]['text'])
        return body.get('legal_identity')
    source, origins = _resolve_metadata_claims(source, text, identity, publication_receipt,legal_identity_resolver,
        legacy_locators=legacy, publication_matches=publication_matches,
        deposit_resolver=deposit_resolver if publication_receipt is None else None,
        european_locators=(not legacy and str(identity.get('ticker', '')).upper().endswith('.MI')),
        document_names=_route_document_names(identity.get('ticker'), identity.get('isin')),
        publication_limit_note=None if legacy else _publication_limit_note(identity.get('ticker'),
                                                                           identity.get('isin')))
    unused_report_quote = source.pop('request_report_date_quote', None)
    unused_issuer_quote = source.pop('request_issuer_quote', None)
    deposit = source.pop("deposit_receipt", None)
    unused_publication_quote = source.pop("request_publication_quote", None)
    published = date.fromisoformat(source["published_at"])
    report = date.fromisoformat(source["report_date"])
    cutoff = date.fromisoformat(as_of)
    if not report <= published <= cutoff:
        raise ValueError("Publication/report date is after cutoff or internally inconsistent")
    proofs = {key: _proof(text, source[key + "_quote"]) for key in ("publication", "report_date", "issuer") if source.get(key + "_quote")}
    if source.get("publication_quote") and (not any(
            row[0] == source["published_at"] and row[1].strip() in source["publication_quote"]
            for row in (_primary_publication_context(text) if publication_matches is None else publication_matches))):
        raise ValueError("Publication claim lacks this document's dated primary header or cover evidence")
    report_proofs = _dated_context(source["report_date_quote"], "report_date")
    report_proofs += [row for row in _primary_financial_report_context(text) if row[1] == source["report_date_quote"]]
    if origins.get("report_date") == _COVER_PERIOD_LOCATOR:
        report_proofs += [row for row in _cover_period_context(text) if row[1] == source["report_date_quote"]]
    if not _date_in_quote(source["report_date"], source["report_date_quote"]) or not any(row[0] == source["report_date"] for row in report_proofs):
        raise ValueError("Economic reporting date lacks dated primary text evidence")
    issuer = _normalized(identity.get("name") or "")
    legal_identity=source.get('primary_legal_identity')
    if not issuer or (issuer not in _normalized(source["issuer_quote"]) and
            (not legal_identity or identity['name'] not in legal_identity['names'])):
        raise ValueError("Primary issuer quotation differs from the confirmed issuer identity")
    form = next((name for name in ("10-K", "10-Q", "20-F", "6-K") if re.search(r"\bFORM\s+" + re.escape(name) + r"\b", text[:20000], re.I)), "public_document")
    metadata = {"issuer": identity["name"], "form": form, "report_date": source["report_date"], "pm_source_verification": {"contract": CONTRACT, "ticker": identity["ticker"], "publisher": fetched["publisher"], "publication_basis": "dated_primary_text_quote", "report_date_basis": "dated_primary_text_quote", "issuer_basis": "confirmed_issuer_name_in_primary_text", "claim_origins": origins, "proofs": proofs, "security_identity_verified": False, "limitation": "Document publisher, bytes and quoted metadata are checked; economic sufficiency and the selected security remain separate gates."}}
    if not legacy:
        metadata['pm_source_verification']['text_extraction'] = text_extraction
    if textless_pages is not None:
        # Solo se non vuoto: le ricevute senza pagine mute restano identiche.
        metadata['pm_source_verification']['textless_pages'] = textless_pages
    if publication_matches is not None:
        metadata['pm_source_verification']['publication_locator'] = 'html_article_header/1'
    if legal_identity is not None:
        metadata['issuer']=legal_identity['entity']
        metadata['pm_source_verification'].update({'issuer_basis':'recompiled_primary_legal_identity',
            'accepted_profile_name':identity['name'],'primary_legal_identity':legal_identity})
    if publication_receipt is not None:
        entry = publication_receipt["entry"]
        if entry["filed_date"] != source["published_at"] or entry["report_date"] != source["report_date"]:
            raise ValueError("Primary document dates disagree with the SEC publication companion")
        metadata.update({key: entry[key] for key in ("issuer", "form", "emittente_id", "accession")})
        verification = metadata["pm_source_verification"]
        verification["publication_basis"] = "server_sec_catalog_receipt"
        verification["publication_receipt"] = {key: publication_receipt[key] for key in
            ("contract", "source_url", "sha256", "entry", "catalog_status")}
        verification["proofs"].setdefault("publication", {"source_url": publication_receipt["source_url"],
            "sha256": publication_receipt["sha256"], "locator": "/entry/filed_date", "value": source["published_at"]})
    if origins.get("report_date") == _COVER_PERIOD_LOCATOR:
        # Dichiarazioni solo quando usate: le ricevute vecchie restano identiche.
        metadata["pm_source_verification"]["report_date_basis"] = _COVER_PERIOD_LOCATOR
        metadata["pm_source_verification"]["report_date_locator"] = {"basis": _COVER_PERIOD_LOCATOR,
            "value": source["report_date"], "quote": source["report_date_quote"],
            "page": _page_of_offset(extracted, text.find(source["report_date_quote"]))}
        if unused_report_quote:
            metadata["pm_source_verification"]["pm_report_date_quote_not_evidence"] = unused_report_quote
    if origins.get("issuer") == _ISSUER_FULL_TEXT_LOCATOR:
        offset = text.find(source["issuer_quote"])
        metadata["pm_source_verification"]["issuer_basis"] = _ISSUER_FULL_TEXT_LOCATOR
        metadata["pm_source_verification"]["issuer_locator"] = {"basis": _ISSUER_FULL_TEXT_LOCATOR,
            "name": identity["name"], "name_source": _ISSUER_NAME_SOURCE, "quote": source["issuer_quote"],
            "offset": offset, "page": _page_of_offset(extracted, offset)}
    if unused_issuer_quote:
        metadata["pm_source_verification"]["pm_issuer_quote_not_evidence"] = unused_issuer_quote
    if deposit is not None:
        # Solo quando usato: le ricevute con data in copertina restano identiche.
        verification = metadata["pm_source_verification"]
        verification["publication_basis"] = _deposit_basis(deposit)
        verification["deposit_receipt"] = deepcopy(deposit)
        verification["publication_declaration"] = _deposit_declaration(deposit)
        if unused_publication_quote:
            verification["pm_publication_quote_not_evidence"] = unused_publication_quote
        effective = _deposit_effective(deposit)
        verification["proofs"]["publication"] = {"source_url": effective.get("url"), "sha256": deposit["sha256"],
            "locator": "/scelta_copertina/candidato/data" if deposit.get("scelta_copertina") else "/data_deposito",
            "value": effective["data_deposito"]}
    candidate = {"stato": "verificato", "url": source["url"], "path": str(path), "sha256": fetched["sha256"], "filed_date": source["published_at"], "metadati": metadata}
    return candidate, text


def _receipt_fingerprint(receipt):
    rows = [{**{key: row.get(key) for key in ("url", "url_finale", "sha256", "text_sha256", "bytes", "content_type", "filed_date", "metadati", "status", "reason")},
             **({"request": row.get("request")} if receipt.get("contract") == RESEARCH_CONTRACT else {}),
             **({"publisher_proof": row["publisher_proof"]} if row.get("publisher_proof") is not None else {})}
            for row in receipt.get("documents", [])]
    return _digest({"contract": receipt.get("contract"), "ticker": receipt.get("ticker"), "as_of": receipt.get("as_of"),
                    "documents": sorted(rows, key=lambda row: row.get("url") or "")})


_SEC_PUBLICATION_CONTRACT = "trade-idea-sec-publication-receipt/1"


def _sec_document_identity(url):
    parts = urlsplit(url)
    if parts.hostname != "www.sec.gov" or parts.query:
        return None
    match = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d{18})/[^/]+", parts.path)
    return ("CIK:" + str(int(match[1])).zfill(10), match[2]) if match else None


def _check_publication_companion(value, url, ticker, identity, as_of):
    sec_identity = _sec_document_identity(url)
    entry = value.get("entry") if isinstance(value, dict) else None
    if not sec_identity or not isinstance(entry, dict):
        raise ValueError("SEC publication companion does not identify a primary EDGAR document")
    if any(not isinstance(entry.get(key), str) or not entry[key] for key in
           ("url", "ticker", "issuer", "emittente_id", "accession", "form", "filed_date", "report_date")):
        raise ValueError("Primary SEC publication metadata is missing or malformed")
    cik, accession = sec_identity
    expected_source = "https://data.sec.gov/submissions/" + cik.replace(":", "") + ".json"
    if (value.get("contract") != _SEC_PUBLICATION_CONTRACT or value.get("source_url") != expected_source
            or value.get("catalog_status") not in ("ok", "parziale")
            or entry.get("url") != url or entry.get("ticker") != ticker
            or entry.get("emittente_id") != cik
            or str(entry.get("accession", "")).replace("-", "") != accession
            or entry.get("form") not in ("10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A")
            or not _normalized(identity["name"]) in _normalized(entry.get("issuer") or "")):
        raise ValueError("SEC publication companion conflicts with confirmed issuer/CIK/accession identity")
    if not date.fromisoformat(entry["report_date"]) <= date.fromisoformat(entry["filed_date"]) <= date.fromisoformat(as_of):
        raise ValueError("SEC publication companion dates are missing, future or inconsistent")


def _acquire_publication_companion(url, ticker, identity, as_of, root, catalog=None):
    if _sec_document_identity(url) is None:
        return None
    if catalog is None:
        from bellomberg.market_data.sec_edgar import get_filing_catalog
        catalog = get_filing_catalog
    result = catalog(ticker, days=1100, max_pages=2)
    if not isinstance(result, dict) or result.get("fonte") != "SEC EDGAR":
        raise ValueError("Primary SEC catalog receipt unavailable")
    documents = result.get("documenti")
    if not isinstance(documents, list) or any(not isinstance(entry, dict) for entry in documents):
        raise ValueError("Primary SEC catalog document list is malformed")
    matches = [entry for entry in documents if entry.get("url") == url]
    if len(matches) != 1:
        raise ValueError("Exact requested document is absent or ambiguous in the primary SEC catalog")
    entry = {key: matches[0].get(key) for key in ("ticker", "url", "issuer", "emittente_id", "form", "accession", "filed_date", "report_date")}
    if not isinstance(entry.get("emittente_id"), str):
        raise ValueError("Primary SEC catalog issuer identity is absent")
    value = {"contract": _SEC_PUBLICATION_CONTRACT,
        "source_url": "https://data.sec.gov/submissions/" + entry["emittente_id"].replace(":", "") + ".json",
        "catalog_status": result["stato"], "entry": entry}
    _check_publication_companion(value, url, ticker, identity, as_of)
    # This is the normalized result of the existing primary SEC client, labelled
    # as a tool receipt. It is not represented as the original SEC JSON body.
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    path, digest = _archive_bytes(root / "publication-receipts", raw, ".json")
    return {**value, "path": str(path), "sha256": digest}


def _verify_publication_companion(receipt, url, ticker, identity, as_of, root):
    if receipt is None:
        return None
    path = Path(receipt["path"]).resolve()
    if not path.is_relative_to((root / "publication-receipts").resolve()):
        raise ValueError("SEC publication receipt escaped the server archive")
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ValueError("SEC publication receipt archive is missing or unreadable") from exc
    if len(raw) > MAX_BYTES or sha256(raw).hexdigest() != receipt.get("sha256"):
        raise ValueError("Archived SEC publication receipt bytes changed")
    value = json.loads(raw)
    if value != {key: receipt[key] for key in ("contract", "source_url", "catalog_status", "entry")}:
        raise ValueError("Archived SEC publication receipt metadata changed")
    _check_publication_companion(value, url, ticker, identity, as_of)
    return deepcopy(receipt)


def ingest_document_sources(ticker, identity, as_of, sources, *, archive_root, issuer_website=None, download=None,
                            publication_catalog=None, publisher_proofs=None, allow_partial=False,
                            deposit_lookup=None, isin_lookup=None, deposit_pdf_fetch=None, fiscal_year_end_sources=(),
                            deposit_eu_lookup=None):
    if (not isinstance(identity, dict) or identity.get("status") != "confirmed"
            or identity.get("ticker") != ticker or not identity.get("name")
            or not identity.get("exchange") or not identity.get("currency")):
        raise ValueError("Exact confirmed issuer, listing and currency identity required before source acquisition")
    date.fromisoformat(as_of)
    sources = normalize_sources(sources)
    if publisher_proofs is not None and (not isinstance(publisher_proofs, dict)
            or not set(publisher_proofs) <= {source["url"] for source in sources}):
        raise ValueError("Server publisher proofs must match the requested document URLs")
    root = Path(archive_root).resolve() / "pm-public-documents"
    candidates = []
    # Fine esercizio (trimestrali non solari): annuali ammesse PRIMA in questa richiesta, poi quelle del chiamante.
    fiscal_sources = []
    for entry in fiscal_year_end_sources or ():
        if isinstance(entry, dict) and entry.get("fonte") in ("relazione_annuale", "fornitore_prezzi"):
            fiscal_sources.append(deepcopy(entry))
    for source in sources:
        safe_url, fetched = None, {}
        try:
            proof = (publisher_proofs or {}).get(source["url"])
            publisher = _publisher_policy(source["url"], issuer_website, publisher_proof=proof,
                document_root=root, identity=identity, as_of=as_of)
            safe_url = source["url"]
            fetched = (download or download_public_document)(source["url"], root, issuer_website=issuer_website,
                **({"publisher_proof": proof} if proof is not None else {}))
            if fetched.get("stato") != "ok" or fetched.get("url") != source["url"] or fetched.get("publisher") != publisher:
                raise ValueError("Public acquisition did not attest the requested publisher/source")
            _publisher_policy(fetched.get("url_finale"), issuer_website, publisher_proof=proof,
                document_root=root, identity=identity, as_of=as_of)
            if proof is not None and fetched.get("publisher_proof") != proof:
                raise ValueError("Public acquisition did not attest the original issuer-link delegation")
            if _host(fetched["url_finale"]) != _host(source["url"]):
                raise ValueError("Public acquisition changed the publisher host")
            publication = _acquire_publication_companion(source["url"], ticker, identity, as_of, root, publication_catalog)
            route = _deposit_route(ticker, identity.get("isin"))
            if route == _DEPOSIT_ROUTE_IT:
                deposit_resolver = (lambda report_date, tipo, text: _acquire_deposit_companion(
                    ticker, tipo, report_date, as_of, root, deposit_lookup, issuer_name=identity["name"],
                    isin_lookup=isin_lookup, pdf_fetch=deposit_pdf_fetch, text=text,
                    identity_isin=identity.get("isin"), eu_lookup=deposit_eu_lookup,
                    fiscal_sources=sorted(fiscal_sources, key=lambda entry: entry["fonte"] != "relazione_annuale")))
            elif route == _DEPOSIT_ROUTE_EU:
                deposit_resolver = (lambda report_date, tipo, text: _acquire_eu_deposit(
                    ticker, tipo, report_date, as_of, root, deposit_lookup, issuer_name=identity["name"],
                    isin=identity.get("isin"), text=text))
            else:
                deposit_resolver = None
            candidate, text = _verified_candidate(source, fetched, identity, as_of, root, publication,
                deposit_resolver=deposit_resolver)
            report = candidate["metadati"].get("report_date")
            if (isinstance(report, str) and report[5:] != "12-31"
                    and _deposit_document_type(text, report, _route_document_names(ticker, identity.get("isin")))
                    == "annuale"):
                fiscal_sources.insert(0, {"valore": report[5:], "fonte": "relazione_annuale",
                    "dettaglio": "relazione annuale al %s ammessa in questa richiesta (sha256 %s)"
                                 % (report, candidate["sha256"][:12])})
            candidates.append({**candidate, "publication_receipt": publication, "status": "verified", "request": source, "url_finale": fetched["url_finale"], "bytes": fetched["bytes"], "content_type": fetched["content_type"], "text_sha256": sha256(text.encode()).hexdigest(),
                **({"publisher_proof": deepcopy(proof)} if proof is not None else {})})
        except (ValueError, OSError, http.client.HTTPException, KeyError) as exc:
            candidates.append({"url": safe_url, "status": "needs_verification", "request": source,
                "sha256": fetched.get("sha256"), "bytes": fetched.get("bytes"),
                "reason": str(exc)[:500] if isinstance(exc, ValueError) else "Public document acquisition or extraction failed (" + type(exc).__name__ + ")"})
    receipt = {"contract": RESEARCH_CONTRACT if allow_partial else CONTRACT,
        "ticker": ticker, "as_of": as_of, "documents": candidates}
    receipt["fingerprint"] = _receipt_fingerprint(receipt)
    if not allow_partial and any(row["status"] != "verified" for row in candidates):
        raise SourceIngestionError("One or more PM documentary sources require verification before any spending grant", receipt)
    return verify_document_receipt(receipt, ticker, identity, as_of, archive_root=archive_root,
        issuer_website=issuer_website, allow_partial=allow_partial)


def verify_document_receipt(receipt, ticker, identity, as_of, *, archive_root, issuer_website=None,
                            allow_partial=False):
    """Re-read only server-owned archive paths, never any browser path."""
    contracts = (CONTRACT, RESEARCH_CONTRACT) if allow_partial else (CONTRACT,)
    if not isinstance(receipt, dict) or receipt.get("contract") not in contracts or receipt.get("ticker") != ticker or receipt.get("as_of") != as_of or receipt.get("fingerprint") != _receipt_fingerprint(receipt):
        raise ValueError("Accepted public document receipt identity/fingerprint changed")
    rows = receipt.get("documents")
    if not isinstance(rows, list) or len(rows) > MAX_DOCUMENTS:
        raise ValueError("Accepted document list is malformed")
    if receipt['contract'] == RESEARCH_CONTRACT:
        normalize_sources([row.get('request') for row in rows])
    root = Path(archive_root).resolve() / "pm-public-documents"
    checked = []
    for row in rows:
        if row.get("status") != "verified":
            if (allow_partial and receipt['contract'] == RESEARCH_CONTRACT
                    and row.get('status') == 'needs_verification'
                    and isinstance(row.get('reason'), str) and row['reason'].strip()):
                # Failed acquisition is a sealed limitation, never source evidence.
                # It is not retried or upgraded while accepting/recovering a run.
                continue
            raise ValueError("Unverified PM document cannot be accepted by the worker")
        metadata = row["metadati"]["pm_source_verification"]
        proof = row.get("publisher_proof")
        policy = _publisher_policy(row["url"], issuer_website, publisher_proof=proof,
            document_root=root, identity=identity, as_of=as_of)
        if metadata["publisher"] != policy or _publisher_policy(row["url_finale"], issuer_website,
                publisher_proof=proof, document_root=root, identity=identity, as_of=as_of)["host"] != policy["host"]:
            raise ValueError("Accepted public document publisher changed")
        source = normalize_sources([row["request"]])[0]
        if source["url"] != row["url"]:
            raise ValueError("Accepted document pointer changed")
        publication = _verify_publication_companion(row.get("publication_receipt"), row["url"], ticker, identity, as_of, root)
        # Old receipts retain their exact text, locator offsets and metadata.
        # Re-extract and reprove the original contract from the sealed raw bytes;
        # never silently rewrite the accepted fingerprint after a parser change.
        from bellomberg.market_data.lettore_trimestrali import LEGACY_HTML_TEXT_EXTRACTOR
        sealed_deposit = metadata.get("deposit_receipt")
        candidate, text = _verified_candidate(source, {**row, "publisher": policy}, identity, as_of, root, publication,
            text_extraction=metadata.get('text_extraction', LEGACY_HTML_TEXT_EXTRACTOR),
            deposit_resolver=(None if sealed_deposit is None else (lambda report_date, tipo, text:
                verify_deposit_companion(sealed_deposit, root, ticker, tipo, report_date, as_of, text=text,
                                         identity=identity))))
        if candidate != {key: row[key] for key in candidate} or sha256(text.encode()).hexdigest() != row["text_sha256"]:
            raise ValueError("Accepted primary document text, metadata or locators changed")
        checked.append(candidate)
    # Existing collector independently checks raw SHA, archive containment,
    # publication cutoff, extraction and metadata before returning documents.
    from bellomberg.valuation.valuation_sources import collect_documents
    normalized = collect_documents(ticker, as_of=as_of, archive_root=archive_root,
        filing_results=[{"ticker": ticker, "candidati": checked, "motivi": []}],
        catalog=lambda *_: {"stato": "ok", "documenti": [], "motivi": []}, max_documents=MAX_DOCUMENTS)
    if normalized["issues"] or {row["document_sha256"] for row in normalized["documents"]} != {row["sha256"] for row in checked}:
        raise ValueError("Common source collector rejected the ingested public documents: " + "; ".join(row["reason"] for row in normalized["issues"]))
    return {"receipt": deepcopy(receipt), "filing_results": [{"ticker": ticker, "candidati": checked, "motivi": []}], "documents": normalized["documents"]}


def document_receipt_summary(receipt):
    if not receipt:
        return {"status": "not_supplied", "documents": [], "limits": {"max_documents": MAX_DOCUMENTS,
            "max_bytes_per_document": MAX_BYTES, "max_pdf_bytes_per_document": MAX_PDF_BYTES, "max_pages": MAX_PAGES}}
    documents = []
    for row in receipt["documents"]:
        verification = (row.get("metadati") or {}).get("pm_source_verification") or {}
        documents.append({"id": row.get("sha256"), "url": row.get("url"), "status": row.get("status"),
            "reason": row.get("reason"), "sha256": row.get("sha256"), "text_sha256": row.get("text_sha256"),
            "published_at": row.get("filed_date"), "report_date": (row.get("metadati") or {}).get("report_date"),
            "bytes": row.get("bytes"), "publisher_basis": (verification.get("publisher") or {}).get("basis"),
            "publication_basis": verification.get("publication_basis"),
            "publication_source_url": (verification.get("publication_receipt") or {}).get("source_url", row.get("url")),
            "metadata_origins": verification.get("claim_origins"), "classification": row.get("classification", "unqualified"),
            **({"publication_declaration": verification["publication_declaration"]}
               if verification.get("publication_declaration") else {}),
            "pages_without_text": ((verification.get("textless_pages") or {}).get("pages", [])
                                   if row.get("status") == "verified" else None),
            **({"pages_without_text_limitation": verification["textless_pages"]["limitation"]}
               if verification.get("textless_pages") else {})})
    return {"status": "verified" if all(row.get("status") == "verified" for row in receipt["documents"]) else "needs_verification",
            "contract": receipt['contract'], "fingerprint": receipt["fingerprint"], "documents": documents,
            "limitation": "Verified public documents are evidence for method-specific qualification, not approval of the investment thesis."}


def qualify_with_document_sources(ticker, identity, as_of, *, archive_root,
                                  sources=(), accepted_receipt=None, download=None,
                                  providers=None, collector=None, historical_seed_loader=None,
                                  publication_catalog=None):
    """Use the method qualifier after admitting every PM source for free.

    A worker receives the accepted receipt from storage. It rechecks those bytes
    and never downloads PM documents again. Other common public freshness gates
    remain the responsibility of the economic qualifier.
    """
    from bellomberg.valuation.trade_idea_model import qualification, source_fingerprint
    from bellomberg.valuation.preparation_historical_sources import collect_trade_idea_sources
    requested = normalize_sources(sources)
    holder = {}
    if accepted_receipt is not None:
        accepted_requests = [row.get("request") for row in accepted_receipt.get("documents", [])]
        if requested and requested != normalize_sources(accepted_requests):
            raise ValueError("Requested PM documents differ from the accepted receipt")

    def admitted_collector(ticker, *, as_of, archive_root, financial_currency,
                           method_id, issuer_website=None):
        if accepted_receipt is not None:
            holder["receipt"] = deepcopy(accepted_receipt)
        try:
            admitted = (verify_document_receipt(accepted_receipt, ticker, identity, as_of,
                        archive_root=archive_root, issuer_website=issuer_website)
                if accepted_receipt is not None else
                ingest_document_sources(ticker, identity, as_of, requested,
                        archive_root=archive_root, issuer_website=issuer_website,
                        download=download, publication_catalog=publication_catalog))
            holder["receipt"] = admitted["receipt"]
        except SourceIngestionError as exc:
            holder["receipt"] = exc.receipt
            raise
        except (ValueError, OSError, KeyError) as exc:
            if accepted_receipt is not None:
                for row in holder["receipt"].get("documents", []):
                    row["status"] = "needs_verification"
                    row["reason"] = "Accepted document receipt could not be reverified against server archive bytes"
                holder["receipt"]["fingerprint"] = _receipt_fingerprint(holder["receipt"])
            raise ValueError("Accepted PM documentary sources could not be reverified before spending") from exc
        options = {}
        if accepted_receipt is not None:
            protected = {pointer: row
                for row in accepted_receipt.get("documents", [])
                for pointer in (row.get("url"), row.get("url_finale")) if pointer}
            def common_download(url, *args, **kwargs):
                accepted = protected.get(url)
                if accepted is not None:
                    _host(url)
                    # Historical reconfirmation and catalog reconciliation may
                    # ask for these bytes again. Always return the exact sealed
                    # archive. Changed catalog metadata still changes the common
                    # source fingerprint, so the spending grant cannot survive.
                    try:
                        raw = read_archived_document(accepted["path"])
                        if len(raw) != accepted["bytes"] or sha256(raw).hexdigest() != accepted["sha256"]:
                            raise ValueError("Accepted PM archive bytes changed during source reconciliation")
                    except (ValueError, OSError) as exc:
                        holder.setdefault("redownload_attempts", []).append(url)
                        raise ValueError("Accepted PM archive cannot be reverified; no redownload attempted") from exc
                    return {"stato": "ok", "path": accepted["path"], "sha256": accepted["sha256"],
                            "url": accepted["url"], "url_finale": accepted["url_finale"]}
                from bellomberg.market_data.lettore_trimestrali import scarica_documento
                return scarica_documento(url, *args, **kwargs)
            options["download"] = common_download
        return (collector or collect_trade_idea_sources)(ticker, as_of=as_of,
            archive_root=archive_root, financial_currency=financial_currency,
            method_id=method_id, seed_loader=historical_seed_loader,
            filing_results=admitted["filing_results"], **options)

    result = qualification(ticker, identity, as_of, archive_root=archive_root,
        providers=providers, collector=admitted_collector,
        historical_seed_loader=historical_seed_loader)
    if holder.get("redownload_attempts"):
        result["status"] = "blocked"
        result["reasons"].append("A live source/catalog diverged from an accepted PM document; exact archived bytes were preserved and paid work is blocked")
    receipt = holder.get("receipt")
    if receipt is None:
        # A failed issuer/method gate must not silently omit requested sources.
        receipt = deepcopy(accepted_receipt) if accepted_receipt is not None else {
            "contract": CONTRACT, "ticker": ticker, "as_of": as_of,
            "documents": [{"url": None, "request": row, "status": "needs_verification",
                "reason": "Issuer or method qualification failed before document admission"} for row in requested]}
        if accepted_receipt is not None:
            for row in receipt.get("documents", []):
                row["status"] = "needs_verification"
                row["reason"] = "Issuer or method qualification failed before document revalidation"
        receipt["fingerprint"] = _receipt_fingerprint(receipt)
    included = {doc.get("document_sha256", doc.get("id"))
                for doc in (result.get("source_report") or {}).get("documents", [])}
    receipt = deepcopy(receipt)
    for row in receipt["documents"]:
        row["classification"] = "included" if row.get("sha256") in included else "excluded"
    selection = sorted([{"url": row.get("url"), "sha256": row.get("sha256"),
                         "text_sha256": row.get("text_sha256"), "classification": row["classification"]}
                        for row in receipt["documents"]], key=lambda row: row.get("url") or "")
    result["document_receipt"] = receipt
    result.setdefault("coverage", {}).setdefault("sources", {})["pm_document_receipt"] = {
        "contract": CONTRACT, "fingerprint": receipt["fingerprint"], "selection": selection}
    result["fingerprint"] = source_fingerprint(result)
    return result
