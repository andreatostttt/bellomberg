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
# Nome del documento nella copertina -> tipo di relazione cercato su eMarket SDIR.
_DEPOSIT_DOCUMENT_TYPES = {
    "semestrale": (r"relazione\s+finanziaria\s+semestrale", r"half[\s-]*year(?:ly)?\s+financial\s+report"),
    "annuale": (r"relazione\s+finanziaria\s+annuale", r"annual\s+financial\s+report"),
    "trimestrale": (r"resoconto\s+intermedio\s+di\s+gestione",
                    r"interim\s+(?:financial|management)\s+(?:report|statement)"),
}
_DEPOSIT_FIELDS = ("ticker", "isin", "emarket_id", "tipo", "periodo_fine", "stato", "stato_originale",
                   "data_deposito", "ora_deposito",
                   "titolo", "url", "protocollo", "categoria", "lingua", "fonte", "categorie_cercate", "url_liste",
                   "sha256_liste", "letto_il", "limiti", "prova", "sha256_pdf", "voce_da", "isin_resolution")
_ISIN_RESOLUTION_FIELDS = ("stato", "isin", "emarket", "negozio", "fonte_url", "letto_il")


def _deposit_document_type(text):
    """Tipo della relazione dalla copertina (primi 2.000 caratteri); None se assente o non univoco."""
    header = " ".join(text[:2000].split())
    kinds = [kind for kind, patterns in _DEPOSIT_DOCUMENT_TYPES.items()
             if any(re.search(pattern, header, re.I) for pattern in patterns)]
    return kinds[0] if len(kinds) == 1 else None


def _deposit_declaration(value):
    day = date.fromisoformat(value["data_deposito"])
    text = ("data di deposito eMarket SDIR del %s, documento \u00ab%s\u00bb (protocollo %s)"
            % (day.strftime("%d/%m/%Y"), " ".join(str(value["titolo"]).split()), value["protocollo"]))
    if value.get("stato") == "STALE":
        read = datetime.fromisoformat(str(value["letto_il"]))
        text += "; data di deposito letta il %s, fonte ora non raggiungibile" % read.strftime("%d/%m")
    if value.get("prova") == "testo_pdf":
        text += "; nome e periodo del documento letti nel PDF del comunicato (titolo generico di deposito)"
    if value.get("voce_da") == "automatico":
        text += "; ISIN %s risolto automaticamente su Borsa Italiana" % value.get("isin")
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


def _check_deposit_value(value, ticker, tipo, report_date, as_of, root):
    """Riverifica PURA del deposito sigillato: stesso emittente, tipo e periodo; deposito dopo la fine
    del periodo e non dopo il cutoff; il titolo passa ancora la regola di eMarket (nome + periodo)."""
    from bellomberg.market_data.emarket_sdir import candidati_deposito
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
            or not isinstance(value.get("url_liste"), list) or not value["url_liste"]
            or not isinstance(value.get("sha256_liste"), dict)
            or set(value["sha256_liste"]) != set(value["url_liste"])):
        raise ValueError("eMarket SDIR deposit receipt conflicts with the document issuer, type or period; "
                         + _DEPOSIT_PM_HINT)
    if not date.fromisoformat(report_date) < date.fromisoformat(value["data_deposito"]) <= date.fromisoformat(as_of):
        raise ValueError("eMarket SDIR deposit date is not after the reporting period or is after the cutoff; "
                         + _DEPOSIT_PM_HINT)
    row = {"data": value["data_deposito"], "ora": value.get("ora_deposito"), "titolo": value["titolo"],
           "url_pdf": value["url"], "protocollo": value["protocollo"], "categoria": value.get("categoria")}
    texts = None
    if value.get("prova") == "testo_pdf":
        # Titolo generico: nome + periodo si ricontrollano nel PDF del comunicato archiviato.
        texts = {value["url"]: _deposit_pdf_text(root, (value.get("sha256_pdf") or {}).get(value["url"]))}
    elif value.get("prova") not in ("titolo", None):
        raise ValueError("eMarket SDIR deposit evidence kind is unknown; " + _DEPOSIT_PM_HINT)
    isin = value.get("isin_resolution")
    if isin is not None and (not isinstance(isin, dict) or isin.get("stato") != "ok" or isin.get("isin") != value.get("isin")):
        raise ValueError("Automatic ISIN resolution differs from the eMarket SDIR deposit issuer; " + _DEPOSIT_PM_HINT)
    verdict = candidati_deposito([row], tipo, date.fromisoformat(report_date), testi_pdf=texts)
    if (verdict["stato"] != "ok" or verdict["scelto"]["protocollo"] != value["protocollo"]
            or verdict.get("prova") != (value.get("prova") or "titolo")):
        raise ValueError("eMarket SDIR deposit does not name this document and period; " + _DEPOSIT_PM_HINT)


def _fetch_deposit_pdf(url):
    from bellomberg.market_data.borsa_italiana import _scarica
    status, raw, _final = _scarica(url)
    if status != 200:
        raise ValueError("eMarket SDIR deposit PDF HTTP %s; %s" % (status, _DEPOSIT_PM_HINT))
    return raw


def _acquire_deposit_companion(ticker, tipo, report_date, as_of, root, lookup=None, *, issuer_name=None,
                               isin_lookup=None, pdf_fetch=None):
    """Una lettura della fonte ufficiale; solo l'esito ok viene sigillato in archivio.

    In produzione (nessuno strumento iniettato) prima risolve l'ISIN (negozio confermato ->
    automatico -> Borsa Italiana col nome dell'emittente), poi legge il deposito."""
    resolution = None
    if isin_lookup is None and lookup is None:
        from bellomberg.market_data.borsa_italiana import risolvi_isin as isin_lookup
    if isin_lookup is not None:
        found = isin_lookup(ticker, nome=issuer_name)
        if not isinstance(found, dict) or found.get("stato") != "ok":
            detail = found if isinstance(found, dict) else {}
            raise ValueError("%s; ISIN resolution %s (%s): %s; %s" % (_PUBLICATION_AMBIGUOUS,
                detail.get("stato", "malformed"), detail.get("errore"), str(detail.get("motivo"))[:300], _DEPOSIT_PM_HINT))
        resolution = {key: deepcopy(found.get(key)) for key in _ISIN_RESOLUTION_FIELDS}
    if lookup is None:
        from bellomberg.market_data.emarket_sdir import get_data_deposito as lookup
    result = lookup(ticker, tipo=tipo, periodo_fine=report_date)
    if not isinstance(result, dict) or not (result.get("stato") == "ok"
            or (result.get("stato") == "STALE" and result.get("stato_originale") == "ok")):
        detail = result if isinstance(result, dict) else {}
        raise ValueError("%s; eMarket SDIR deposit %s (%s): %s; %s" % (_PUBLICATION_AMBIGUOUS,
            detail.get("stato", "malformed"), detail.get("errore"), str(detail.get("motivo"))[:300], _DEPOSIT_PM_HINT))
    value = {"contract": _DEPOSIT_CONTRACT, **{key: deepcopy(result.get(key)) for key in _DEPOSIT_FIELDS}}
    value["isin_resolution"] = resolution
    if value.get("prova") == "testo_pdf":
        # Si archiviano i byte del PDF del comunicato: devono essere quelli letti dalla fonte.
        expected = (value.get("sha256_pdf") or {}).get(value.get("url"))
        pdf = (pdf_fetch or _fetch_deposit_pdf)(value.get("url"))
        if not isinstance(pdf, bytes) or sha256(pdf).hexdigest() != expected:
            raise ValueError("eMarket SDIR deposit PDF differs from the bytes read by the source; " + _DEPOSIT_PM_HINT)
        _archive_bytes(root / "publication-receipts", pdf, ".pdf")
    _check_deposit_value(value, ticker, tipo, report_date, as_of, root)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    _path, digest = _archive_bytes(root / "publication-receipts", raw, ".json")
    return {**value, "sha256": digest}


def verify_deposit_companion(sealed, root, ticker, tipo, report_date, as_of):
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
    if not same_exact_value(value, {key: sealed.get(key) for key in ("contract", *_DEPOSIT_FIELDS)}):
        raise ValueError("Archived eMarket SDIR deposit receipt metadata changed")
    _check_deposit_value(value, ticker, tipo, report_date, as_of, root)
    return deepcopy(sealed)


_COVER_PERIOD_LOCATOR = "cover_report_title_period/1"
_ISSUER_FULL_TEXT_LOCATOR = "confirmed_issuer_name_in_full_text/1"
_ISSUER_NAME_SOURCE = ("confirmed issuer identity (identity resolver); the isin_it store holds ISIN and eMarket id "
                       "only, no issuer-name field")


def _cover_period_context(text):
    """Nome della relazione + periodo nella copertina (primi 2.000 caratteri), es. «Relazione finanziaria
    semestrale al 30 giugno 2026» / «HALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026». Ritorna [(data, citazione)];
    la citazione e' unica nel testo intero (altrimenti il prefisso dall'inizio del documento)."""
    names = "|".join(pattern for patterns in _DEPOSIT_DOCUMENT_TYPES.values() for pattern in patterns)
    pattern = re.compile(r"(?:" + names + r")\s+(?:al|at|as\s+at|as\s+of)\s+(?P<date>" + _DATE_PATTERN + r")", re.I)
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
                             european_locators=False):
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
                    raise ValueError('Publication requires one unambiguous primary header or cover date; optional claims cannot resolve contradictory publication facts')
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
        tipo = _deposit_document_type(text)
        if tipo is None:
            raise ValueError(_PUBLICATION_AMBIGUOUS + "; eMarket SDIR deposit not searched: report type not unique on "
                             "the cover; " + _DEPOSIT_PM_HINT)
        sealed = deposit_resolver(resolved["report_date"], tipo)
        if source.get("published_at") and source["published_at"] != sealed["data_deposito"]:
            raise ValueError("PM publication date conflicts with the eMarket SDIR deposit date")
        resolved["published_at"], resolved["publication_quote"] = sealed["data_deposito"], None
        resolved["deposit_receipt"] = sealed
        if source.get("publication_quote"):
            # La citazione del PM non e' una data di pubblicazione: dichiarata, non usata come prova.
            resolved["request_publication_quote"] = source["publication_quote"]
        origins["publication"] = _DEPOSIT_BASIS
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
        european_locators=(not legacy and str(identity.get('ticker', '')).upper().endswith('.MI')))
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
        verification["publication_basis"] = _DEPOSIT_BASIS
        verification["deposit_receipt"] = deepcopy(deposit)
        verification["publication_declaration"] = _deposit_declaration(deposit)
        if unused_publication_quote:
            verification["pm_publication_quote_not_evidence"] = unused_publication_quote
        verification["proofs"]["publication"] = {"source_url": deposit["url"], "sha256": deposit["sha256"],
            "locator": "/data_deposito", "value": deposit["data_deposito"]}
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
                            deposit_lookup=None, isin_lookup=None, deposit_pdf_fetch=None):
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
            deposit_resolver = ((lambda report_date, tipo: _acquire_deposit_companion(
                ticker, tipo, report_date, as_of, root, deposit_lookup, issuer_name=identity["name"],
                isin_lookup=isin_lookup, pdf_fetch=deposit_pdf_fetch))
                if str(ticker).upper().endswith(".MI") else None)
            candidate, text = _verified_candidate(source, fetched, identity, as_of, root, publication,
                deposit_resolver=deposit_resolver)
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
            deposit_resolver=(None if sealed_deposit is None else (lambda report_date, tipo:
                verify_deposit_companion(sealed_deposit, root, ticker, tipo, report_date, as_of))))
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
