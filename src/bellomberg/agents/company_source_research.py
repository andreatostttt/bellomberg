"""Run-scoped public issuer research, with durable, immutable source receipts.

Navigation is not financial evidence. Documents become evidence only through
the existing public-document verifier; this module never changes a spending
grant, selects a financial model, reads the portfolio, or invokes an AI model.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from . import trade_idea_sources as sources
from bellomberg.reporting.exact_artifacts import _create_exact


CONTRACT = "company-source-research/1"
SOURCE_RECOVERY_INSTRUCTION = (
    'Identical source fields replay the retained decision; they do not re-verify it. '
    'Use open_company_source on the same URL to inspect already archived primary text. '
    'If the missing metadata is explicitly present, submit a new acquire_company_source request '
    'with its verified report_date/report_date_quote, published_at/publication_quote or issuer_quote. '
    'Never infer a date or issuer. Failed or unresolved transport/catalog requests are not retried automatically.'
)
MAX_LINKS = 60
MAX_LINK_CANDIDATES = 2000
_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


class SourceResearchUnavailable(ValueError):
    """A failed or interrupted public acquisition cannot be replayed blindly."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))


def _digest(value):
    return sha256(_json(value).encode("utf-8")).hexdigest()


def bounded_navigation_view(result, *, max_chars):
    """Fit the native tool's whole JSON frame, retaining links and exact offsets.

    ``open_page.max_chars`` bounds plain text. The specialist also bounds the
    serialized envelope, including escaped quotes, URLs and links. This view
    preserves that separate contract without changing the archived response.
    """
    if type(max_chars) is not int or max_chars < 512 or not isinstance(result, dict):
        raise ValueError("Invalid company navigation result bounds")
    view = deepcopy(result)
    # Match Specialist._run_loop's actual framing, including separator spaces.
    def size():
        return len(json.dumps(view, ensure_ascii=False, allow_nan=False))
    if size() <= max_chars:
        return view
    if view.get("status") != "navigation" or not isinstance(view.get("text"), str):
        raise ValueError("Company source result exceeds the complete tool response limit")
    text = view.pop("text")
    offset, total = view["offset"], view["text_chars"]
    if (type(offset) is not int or type(total) is not int or offset < 0 or total < 0
            or not isinstance(view.get("links"), list)):
        raise ValueError("Invalid company navigation pagination")
    view["view_truncated"] = True

    def portion(count):
        view["next_offset"] = offset + count if offset + count < total else None
        # Keep navigation metadata before potentially long text in all consumers.
        view["text"] = text[:count]

    portion(min(128, len(text)))
    while size() > max_chars and view["links"]:
        view["links"].pop()
        view["links_truncated"] = True
    portion(min(1, len(text)))
    if size() > max_chars:
        raise ValueError("Company navigation metadata exceeds the complete tool response limit")
    low, high = min(1, len(text)), len(text)
    while low < high:
        middle = (low + high + 1) // 2
        portion(middle)
        if size() <= max_chars:
            low = middle
        else:
            high = middle - 1
    portion(low)
    return view


def _load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate source journal field")
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError("Nonfinite source journal value")
    with path.open("rb") as stream:
        raw = stream.read(sources.MAX_BYTES + 1)
    if len(raw) > sources.MAX_BYTES:
        raise ValueError("Source journal record exceeds the byte limit")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def _reason(exc):
    # Transport exceptions can contain arbitrary response data; retain their
    # type without exposing credentials, headers or an unrelated local path.
    return (str(exc)[:700] if isinstance(exc, ValueError) else type(exc).__name__)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.current, self.muted, self.total = [], None, 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template", "ix:hidden"):
            self.muted += 1
        if not self.muted and tag == "a":
            self.current = [dict(attrs).get("href"), []]

    def handle_data(self, value):
        if self.current is not None and not self.muted:
            self.current[1].append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template", "ix:hidden"):
            self.muted = max(0, self.muted - 1)
        if tag == "a" and self.current is not None:
            self.total += 1
            if len(self.links) < MAX_LINK_CANDIDATES:
                self.links.append((self.current[0], " ".join(" ".join(self.current[1]).split())[:240]))
            self.current = None


class ResearchSession:
    """Explicit server identity and roots; reconstructable after every tool turn.

    The journal is append-only and hash chained. A durable dispatch intent with
    no receipt is left uncertain, never redownloaded. An admitted receipt can be
    reverified offline repeatedly, including after an application restart.
    """

    def __init__(self, *, run_id, ticker, identity, as_of, admission_fingerprint,
                 archive_root, run_dir, issuer_website=None, download=None,
                 publication_catalog=None):
        if (not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 160
                or any(ord(char) < 32 for char in run_id)):
            raise ValueError("Explicit run identity required for company research")
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_-]{0,39}", ticker):
            raise ValueError("Canonical ticker required for company research")
        if (not isinstance(identity, dict) or identity.get("status") != "confirmed"
                or identity.get("ticker") != ticker or not identity.get("name")
                or not identity.get("exchange") or not identity.get("currency")):
            raise ValueError("Exact confirmed issuer, listing and currency identity required")
        if (not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of
                or not isinstance(admission_fingerprint, str)
                or not re.fullmatch(r"[0-9a-f]{64}", admission_fingerprint)):
            raise ValueError("Exact cutoff and original admission fingerprint required")
        if issuer_website is not None:
            sources._host(issuer_website, website=True)
        self.archive_root, self.run_dir = Path(archive_root).resolve(), Path(run_dir).resolve()
        self.identity, self.ticker, self.as_of = deepcopy(identity), ticker, as_of
        self.issuer_website = issuer_website
        self.download = download or sources.download_public_document
        self.publication_catalog = publication_catalog
        self.contract = {"contract": CONTRACT, "run_id": run_id, "ticker": ticker,
            "identity": deepcopy(identity), "as_of": as_of,
            "parent_grant_fingerprint": admission_fingerprint, "issuer_website": issuer_website,
            "archive_root": str(self.archive_root), "run_dir": str(self.run_dir)}
        self.contract_sha256 = _digest(self.contract)
        # A changed grant/issuer cannot select a new journal under the same run.
        self.session_dir = self.run_dir / "company-source-research" / _digest({"run_id": run_id, "ticker": ticker})
        self.document_root = self.archive_root / "pm-public-documents"

    def _contained(self, path):
        if not path.resolve().is_relative_to(self.run_dir):
            raise ValueError("Company research journal escaped the explicit run root")
        return path

    @contextmanager
    def _locked(self):
        directory = self._contained(self.session_dir)
        with _LOCKS_GUARD:
            guard = _LOCKS.setdefault(str(directory), threading.RLock())
        with guard:
            directory.mkdir(parents=True, exist_ok=True)
            with self._contained(directory / ".lock").open("a+b") as handle:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise SourceResearchUnavailable("Company source research is already active for this issuer/run") from exc
                self._events = self._read_events()
                yield

    def _read_events(self):
        path = self._contained(self.session_dir / "contract.json")
        sealed = {**self.contract, "sha256": self.contract_sha256}
        if path.exists():
            if _load_json(path) != sealed:
                raise ValueError("Original company research admission or identity changed")
        else:
            raw = _json(sealed).encode("utf-8")
            _create_exact(path, raw, sha256(raw).hexdigest())
        directory = self._contained(self.session_dir / "events")
        directory.mkdir(exist_ok=True)
        events, parent = [], self.contract_sha256
        for index, path in enumerate(sorted(directory.glob("*.json")), 1):
            item = _load_json(self._contained(path))
            if (not isinstance(item, dict) or set(item) != {"index", "parent_sha256", "session_sha256", "kind", "payload", "sha256"}
                    or item["index"] != index or item["parent_sha256"] != parent
                    or item["session_sha256"] != self.contract_sha256
                    or not isinstance(item["payload"], dict)):
                raise ValueError("Company source journal lineage is incomplete or changed")
            body = {key: value for key, value in item.items() if key != "sha256"}
            if item["sha256"] != _digest(body) or path.name != f"{index:08d}-{item['sha256']}.json":
                raise ValueError("Company source journal seal changed")
            events.append(item)
            parent = item["sha256"]
        return events

    def _append(self, kind, payload):
        body = {"index": len(self._events) + 1,
            "parent_sha256": self._events[-1]["sha256"] if self._events else self.contract_sha256,
            "session_sha256": self.contract_sha256, "kind": kind, "payload": deepcopy(payload)}
        item = {**body, "sha256": _digest(body)}
        raw = _json(item).encode("utf-8")
        if len(raw) > sources.MAX_BYTES:
            raise ValueError("Source journal record exceeds the byte limit")
        path = self._contained(self.session_dir / "events" / f"{item['index']:08d}-{item['sha256']}.json")
        _create_exact(path, raw, sha256(raw).hexdigest())
        self._events.append(item)
        return item

    def _latest(self, key, kinds):
        return next((event for event in reversed(self._events)
                     if event["kind"] in kinds and event["payload"].get("key") == key), None)

    def _publisher_proof(self, url):
        sources._host(url)
        try:
            sources.allowed_publisher_host(url, self.issuer_website)
            return None
        except ValueError as unavailable:
            original = unavailable
        for event in self._events:
            if event["kind"] != "download_received":
                continue
            navigation = event["payload"]["receipt"]
            if (navigation.get("publisher", {}).get("basis") != "qualified_public_issuer_profile_website"
                    or navigation.get("content_type") not in ("text/html", "application/xhtml+xml")):
                continue
            binding = {"run_id": self.contract["run_id"], "ticker": self.ticker, "as_of": self.as_of,
                "identity_sha256": _digest(self.identity),
                "parent_grant_fingerprint": self.contract["parent_grant_fingerprint"],
                "navigation_event_sha256": event["sha256"], "session_sha256": self.contract_sha256}
            try:
                return sources.publisher_link_proof(url, navigation, issuer_website=self.issuer_website,
                    document_root=self.document_root, binding=binding)
            except sources.IssuerLinkNotObserved:
                continue
        raise original

    def _checked_download(self, url, fetched):
        proof = self._publisher_proof(url)
        policy = sources._publisher_policy(url, self.issuer_website, publisher_proof=proof,
            document_root=self.document_root, identity=self.identity, as_of=self.as_of)
        if (not isinstance(fetched, dict) or fetched.get("stato") != "ok"
                or fetched.get("url") != url or fetched.get("publisher") != policy
                or fetched.get("publisher_proof") != proof
                or sources._publisher_policy(fetched.get("url_finale"), self.issuer_website,
                    publisher_proof=proof, document_root=self.document_root,
                    identity=self.identity, as_of=self.as_of)["host"] != policy["host"]):
            raise ValueError("Company source receipt differs from the requested publisher")
        path = Path(fetched["path"]).resolve()
        if (not self.document_root.resolve().is_relative_to(self.archive_root)
                or not path.is_relative_to(self.document_root.resolve())):
            raise ValueError("Company source bytes escaped the explicit document archive")
        raw = sources.read_archived_document(path)
        if (not raw or len(raw) != fetched.get("bytes")
                or sha256(raw).hexdigest() != fetched.get("sha256")):
            raise ValueError("Company source archived bytes differ from the received SHA256")
        return raw

    def _fetch(self, url):
        proof = self._publisher_proof(url)
        if not self.document_root.resolve().is_relative_to(self.archive_root):
            raise ValueError("Company source archive escaped its explicit root")
        key = _digest({"url": url})
        previous = self._latest(key, ("download_started", "download_received", "download_failed"))
        if previous:
            if previous["kind"] != "download_received":
                detail = previous["payload"].get("reason", "Dispatch recorded without a durable response")
                raise SourceResearchUnavailable("Public acquisition retained; no automatic redownload: " + detail)
            fetched = previous["payload"]["receipt"]
            return deepcopy(fetched), self._checked_download(url, fetched), True
        self._append("download_started", {"key": key, "url": url})
        try:
            fetched = self.download(url, self.document_root, issuer_website=self.issuer_website,
                **({"publisher_proof": proof} if proof is not None else {}))
            raw = self._checked_download(url, fetched)
            # Whitelist the existing transport receipt; no response headers.
            fetched = {**{name: fetched[name] for name in
                ("stato", "url", "url_finale", "path", "sha256", "bytes", "content_type", "publisher")},
                **({"publisher_proof": deepcopy(proof)} if proof is not None else {})}
            self._append("download_received", {"key": key, "url": url, "receipt": fetched})
            return fetched, raw, False
        except (ValueError, OSError, http.client.HTTPException, KeyError, TypeError) as exc:
            self._append("download_failed", {"key": key, "url": url, "reason": _reason(exc)})
            raise SourceResearchUnavailable("Public acquisition failed; receipt retained: " + _reason(exc)) from exc

    def _catalog(self, ticker, **options):
        key = _digest({"ticker": ticker, "options": options})
        prior = self._latest(key, ("catalog_started", "catalog_received", "catalog_failed"))
        if prior:
            if prior["kind"] != "catalog_received":
                raise SourceResearchUnavailable("SEC catalog acquisition is unresolved; no automatic repeat")
            return deepcopy(prior["payload"]["result"])
        self._append("catalog_started", {"key": key, "ticker": ticker, "options": options})
        try:
            catalog = self.publication_catalog
            if catalog is None:
                from bellomberg.market_data.sec_edgar import get_filing_catalog
                catalog = get_filing_catalog
            result = catalog(ticker, **options)
            self._append("catalog_received", {"key": key, "result": result})
            return deepcopy(result)
        except (ValueError, OSError, http.client.HTTPException, KeyError, TypeError) as exc:
            self._append("catalog_failed", {"key": key, "reason": _reason(exc)})
            raise SourceResearchUnavailable("SEC publication catalog failed: " + _reason(exc)) from exc

    def search(self, query="", url=None):
        """Search links on the official site/page, without a paid search engine."""
        if url is None:
            if not self.issuer_website:
                return {"ok": False, "status": "unavailable", "reason": "verified_issuer_website_missing",
                        "financial_evidence": False, "links": []}
            parts = urlsplit(self.issuer_website)
            url = urlunsplit(("https", parts.netloc, parts.path or "/", parts.query, ""))
        return self.open_page(url, query=query)

    def open_page(self, url, *, query="", offset=0, max_chars=12000, page=None):
        if (not isinstance(query, str) or len(query) > 300 or type(offset) is not int or offset < 0
                or type(max_chars) is not int or not 1 <= max_chars <= 12000):
            raise ValueError("Invalid navigation query or page bounds")
        if page is not None and (type(page) is not int or page < 1):
            raise ValueError("Physical PDF page must be a positive integer")
        with self._locked():
            fetched, raw, reused = self._fetch(url)
            from bellomberg.market_data.lettore_trimestrali import estrai_testo
            if raw.startswith(b"%PDF-"):
                from io import BytesIO
                from pypdf import PdfReader
                pdf = PdfReader(BytesIO(raw), strict=True)
                if pdf.is_encrypted or len(pdf.pages) > sources.MAX_PAGES:
                    raise ValueError("Encrypted or excessive-page PDF is unsupported")
                number = page or 1
                if number > len(pdf.pages):
                    raise ValueError("Physical PDF page is outside the acquired document")
                current = pdf.pages[number - 1]
                text = current.extract_text() or ''
                from bellomberg.market_data.lettore_trimestrali import _pagina_vuota_verificata
                blank = not text.strip() and _pagina_vuota_verificata(current)
                measured = {'financial_evidence': False, 'url': url, 'url_finale': fetched['url_finale'],
                    'sha256': fetched['sha256'], 'replayed': reused, 'page': number, 'pages': len(pdf.pages),
                    'page_text_sha256': sha256(text.encode()).hexdigest(), 'verified_empty_page': blank,
                    'extraction_scope': 'one_physical_page', 'document_extraction_complete': False,
                    'observed_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'observation_basis': 'exact_archived_page_read', 'published_at': None,
                    'publication_status': 'not_verified_by_page_read'}
                if not text.strip() and not blank:
                    return {'ok': False, 'status': 'unavailable', **measured,
                        'reason': 'page_requires_ocr_or_visual_verification'}
                if len(text) > sources.MAX_TEXT:
                    raise ValueError('Physical PDF page exceeds the bounded text limit')
                if offset > len(text):
                    raise ValueError('PDF offset is outside the selected page')
                end = min(len(text), offset + max_chars)
                return {'ok': True, 'status': 'navigation', **measured, 'untrusted_content': True,
                    'ticker': self.ticker, 'search_mode': 'physical_pdf_page', 'text': text[offset:end],
                    'text_chars': len(text), 'offset': offset, 'next_offset': end if end < len(text) else None,
                    'next_page': number + 1 if number < len(pdf.pages) else None,
                    'links': [], 'matching_links': 0, 'links_truncated': False,
                    'limitation': 'Exact physical-page text only; not a complete extraction or admitted financial evidence. '
                        'Continue next_offset on this page, then next_page with offset zero. Missing text requires visual/OCR verification.'}
            if page is not None:
                raise ValueError('Physical page navigation requires a PDF document')
            extracted = estrai_testo(fetched["path"], contenuto=raw)
            text = extracted.get("testo", "")
            if (extracted.get("stato") != "ok" or not text.strip()
                    or len(text) > sources.MAX_TEXT or extracted.get("pagine_senza_testo")):
                return {"ok": False, "status": "unavailable", "reason": "incomplete_or_unsupported_text_extraction",
                    "financial_evidence": False, "url": url, "sha256": fetched["sha256"], "replayed": reused}
            parser = _Links()
            if extracted.get("formato") == "html":
                parser.feed(raw.decode("utf-8", errors="replace"))
            links, seen = [], set()
            for href, label in parser.links:
                if not isinstance(href, str) or not href.strip():
                    continue
                candidate = urldefrag(urljoin(fetched["url_finale"], href.strip()))[0]
                if candidate in seen:
                    continue
                seen.add(candidate)
                if query and not any(term in (label + " " + candidate).casefold() for term in query.casefold().split()):
                    continue
                try:
                    sources._host(candidate)
                except ValueError:
                    links.append({"url": None, "text": label, "eligible": False, "reason": "unsafe_or_nonpublic_link"})
                    continue
                try:
                    sources.allowed_publisher_host(candidate, self.issuer_website)
                    links.append({"url": candidate, "text": label, "eligible": True})
                except ValueError as exc:
                    if fetched["publisher"].get("basis") == "qualified_public_issuer_profile_website":
                        # This anchor was just read from the checked official
                        # page. Seal/reverify the delegation only on acquisition,
                        # avoiding a full HTML reparse for every displayed link.
                        links.append({"url": candidate, "text": label, "eligible": True,
                            "publisher_basis": "exact_link_from_verified_issuer_page",
                            "navigation_sha256": fetched["sha256"]})
                    else:
                        links.append({"url": candidate, "text": label, "eligible": False, "reason": str(exc)})
            shown, link_chars = [], 0
            for link in links[:MAX_LINKS]:
                size = len(_json(link))
                if link_chars + size > 6000:
                    break
                shown.append(link)
                link_chars += size
            return {"ok": True, "status": "navigation", "financial_evidence": False, "untrusted_content": True,
                "search_mode": "official_page_links", "ticker": self.ticker, "url": url,
                "url_finale": fetched["url_finale"], "sha256": fetched["sha256"], "replayed": reused,
                "text": text[offset:offset + max_chars], "text_chars": len(text), "offset": offset,
                "next_offset": offset + max_chars if offset + max_chars < len(text) else None,
                "links": shown, "matching_links": len(links),
                "links_truncated": len(links) > len(shown) or parser.total > MAX_LINK_CANDIDATES,
                "limitation": "Navigation is not financial evidence. An external document is eligible only for the exact link sealed from this issuer's page, never for its entire host. Acquire it to verify issuer and financial dates."}

    def acquire(self, source):
        request = sources.normalize_sources([source])[0]
        sources._host(request["url"])
        key = _digest(request)
        with self._locked():
            proof = self._publisher_proof(request["url"])
            prior = self._latest(key, ("source_accepted", "source_rejected"))
            if prior is not None:
                if prior["kind"] == "source_accepted":
                    self._verify(prior["payload"]["receipt"])
                return self._acquire_result(prior, replayed=True)
            self._append("acquisition_started", {"key": key, "source": request})
            try:
                def cached_download(url, destination, *, issuer_website, publisher_proof=None):
                    if (Path(destination).resolve() != self.document_root.resolve() or issuer_website != self.issuer_website
                            or publisher_proof != proof):
                        raise ValueError("Document ingestion changed the explicit acquisition scope")
                    return self._fetch(url)[0]
                admitted = sources.ingest_document_sources(self.ticker, self.identity, self.as_of, [request],
                    archive_root=self.archive_root, issuer_website=self.issuer_website,
                    download=cached_download, publication_catalog=self._catalog,
                    publisher_proofs={request["url"]: proof} if proof is not None else None)
                event = self._append("source_accepted", {"key": key, "source": request, "receipt": admitted["receipt"]})
            except sources.SourceIngestionError as exc:
                event = self._append("source_rejected", {"key": key, "source": request,
                    "receipt": exc.receipt, "reason": str(exc)})
            return self._acquire_result(event, replayed=False)

    def _acquire_result(self, event, *, replayed):
        accepted = event["kind"] == "source_accepted"
        summary = sources.document_receipt_summary(event["payload"]["receipt"])
        return {"ok": accepted, "status": "verified" if accepted else "needs_verification", "financial_evidence": accepted,
            "ticker": self.ticker, "as_of": self.as_of, "identity": deepcopy(self.identity),
            "parent_grant_fingerprint": self.contract["parent_grant_fingerprint"],
            "revision_id": event["sha256"] if accepted else None,
            "revision_sha256": event["sha256"] if accepted else None,
            "replayed": replayed, "source": summary,
            "reason": event["payload"].get("reason"),
            **({"recovery_instruction": SOURCE_RECOVERY_INSTRUCTION} if not accepted else {}),
            "limitation": "Verified source metadata does not attest financial sufficiency or approve a model."}

    def _verify(self, receipt):
        if not self.document_root.resolve().is_relative_to(self.archive_root):
            raise ValueError("Company source archive escaped its explicit root")
        for row in receipt.get("documents", []):
            if row.get("publisher_proof") != self._publisher_proof(row["url"]):
                raise ValueError("Document publisher delegation differs from the original journal lineage")
        return sources.verify_document_receipt(receipt, self.ticker, self.identity, self.as_of,
            archive_root=self.archive_root, issuer_website=self.issuer_website)

    def snapshot(self, *, expected_revision_id=None):
        """Server-only full evidence, reverified without a network or DB read."""
        with self._locked():
            accepted = [event for event in self._events if event["kind"] == "source_accepted"]
            if expected_revision_id is not None and expected_revision_id not in {item["sha256"] for item in accepted}:
                raise ValueError("Expected source revision is absent from the original run lineage")
            documents, candidates, receipts, seen = [], [], [], set()
            for event in accepted:
                receipt = event["payload"]["receipt"]
                checked = self._verify(receipt)
                receipts.append(deepcopy(receipt))
                for document in checked["documents"]:
                    identity = (document["id"], document["url"])
                    if identity not in seen:
                        seen.add(identity)
                        documents.append(document)
                candidates.extend(checked["filing_results"][0]["candidati"])
            latest = accepted[-1]["sha256"] if accepted else None
            pending = []
            for prefix in ("download", "catalog"):
                requests = {}
                for event in self._events:
                    if event["kind"] in (prefix + "_started", prefix + "_received", prefix + "_failed"):
                        requests[event["payload"]["key"]] = event
                pending.extend({"kind": prefix, "key": key, "url": event["payload"].get("url"),
                                "reason": "Dispatch recorded without a durable response; no automatic repeat"}
                               for key, event in requests.items() if event["kind"] == prefix + "_started")
            return {"contract": CONTRACT, "run_id": self.contract["run_id"], "ticker": self.ticker,
                "identity": deepcopy(self.identity), "as_of": self.as_of,
                "parent_grant_fingerprint": self.contract["parent_grant_fingerprint"],
                "session_sha256": self.contract_sha256, "revision_id": latest, "revision_sha256": latest,
                "status": "verified" if documents else "empty", "documents": documents,
                "filing_results": [{"ticker": self.ticker, "candidati": candidates, "motivi": []}],
                "document_receipts": receipts,
                "pending_requests": pending,
                "revisions": [{"revision_id": event["sha256"], "parent_sha256": event["parent_sha256"]}
                              for event in accepted],
                "failures": [deepcopy(event["payload"]) for event in self._events
                             if event["kind"] in ("source_rejected", "download_failed", "catalog_failed")]}
