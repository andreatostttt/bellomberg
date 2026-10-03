"""Offline, byte-bound qualification of a financial SEC 6-K.

A SEC catalog row locates a filing; it does not say that the filing contains
financial statements. This helper uses the existing Filing Diff verifier and
returns a collector candidate only when the original bytes prove the claim.
"""

from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit

from bellomberg.market_data.filing_verifica import verifica_documento
from bellomberg.market_data.lettore_trimestrali import estrai_testo


_STATEMENTS = (
    r"\b(?:unaudited\s+)?(?:consolidated\s+condensed|condensed\s+consolidated|"
    r"consolidated\s+interim|interim\s+consolidated)\s+"
    r"(?:interim\s+)?financial\s+statements\b"
)
_PERIOD = (
    r"(?P<mesi>three|3|six|6|nine|9)-month\s+period\s+ended\s+"
    r"(?P<fine>[A-Za-z]+\s+\d{1,2},\s+\d{4})"
)
_MONTHS = {"three": "trimestrale", "3": "trimestrale",
           "six": "semestrale", "6": "semestrale",
           "nine": "nove_mesi", "9": "nove_mesi"}
_INCOME = (
    r"(?:CONSOLIDATED\s+CONDENSED\s+INTERIM\s+INCOME\s+STATEMENTS?|"
    r"(?:CONDENSED\s+)?CONSOLIDATED\s+STATEMENTS?\s+OF\s+(?:INCOME|OPERATIONS))"
)
_NEXT_STATEMENT = (
    r"(?:CONSOLIDATED\s+CONDENSED\s+INTERIM\s+STATEMENTS?\s+OF\s+"
    r"OTHER\s+COMPREHENSIVE(?:\s+INCOME)?|"
    r"(?:CONDENSED\s+)?CONSOLIDATED\s+(?:BALANCE\s+SHEETS?|"
    r"STATEMENTS?\s+OF\s+(?:FINANCIAL\s+POSITION|COMPREHENSIVE\s+INCOME)))"
)
_NOTICE = re.compile(r"\b(?:announcement|announces?|notice|press release|conference call)\b", re.I)
_FINANCIAL_SIGNAL = re.compile(
    r"\b(?:financial\s+statements?|statements?\s+of\s+(?:income|operations|"
    r"financial\s+position|cash\s+flows?)|"
    r"(?:income|cash\s+flows?)\s+statements?|balance\s+sheets?)\b", re.I)


def _issuer_pattern(name):
    """Match a whole legal-name line, allowing punctuation within suffixes."""
    tokens = re.findall(r"[^\W_]+", name, re.UNICODE)
    if not tokens:
        raise ValueError("SEC issuer name missing")
    pieces = []
    for token in tokens:
        if token.upper() in {"SA", "NV", "AG", "SE", "PLC"}:
            pieces.append(r"[^\w\r\n]*".join(map(re.escape, token)))
        else:
            pieces.append(re.escape(token))
    return r"(?m)^[ \t]*" + r"[^\w\r\n]+".join(pieces) + r"[^\w\r\n]*[ \t]*$"


def _catalog_identity(candidate):
    if not isinstance(candidate, dict):
        raise ValueError("SEC candidate must be an object")
    issuer = candidate.get("issuer")
    if not isinstance(issuer, str) or not issuer.strip():
        raise ValueError("SEC issuer name missing")
    issuer_pattern = _issuer_pattern(issuer)
    issuer_id = candidate.get("emittente_id")
    match = re.fullmatch(r"CIK:(\d{10})", issuer_id or "")
    if not match or int(match[1]) == 0:
        raise ValueError("SEC CIK missing or invalid")
    cik = match[1]
    if candidate.get("cik") is not None:
        if not str(candidate["cik"]).isdigit() or str(int(candidate["cik"])).zfill(10) != cik:
            raise ValueError("SEC CIK claims disagree")
    if candidate.get("form") != "6-K":
        raise ValueError("only unamended SEC 6-K is supported")
    accession = candidate.get("accession")
    if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError("SEC accession missing or invalid")
    url = candidate.get("url")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov"
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment):
        raise ValueError("filing URL must use the public SEC HTTPS host")
    url_match = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d{18})/([^/]+\.html?)", parsed.path, re.I)
    if (not url_match or str(int(url_match[1])).zfill(10) != cik
            or url_match[2] != accession.replace("-", "")):
        raise ValueError("SEC URL, CIK and accession disagree")
    report_date = date.fromisoformat(candidate["report_date"])
    filed_date = date.fromisoformat(candidate["filed_date"])
    if filed_date < report_date:
        raise ValueError("filing date precedes accounting period")
    accepted_at = candidate.get("accepted_at")
    if accepted_at is not None and (not isinstance(accepted_at, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
                                accepted_at)):
        raise ValueError("SEC acceptance timestamp invalid")
    if accepted_at is not None:
        try:
            datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("SEC acceptance timestamp invalid") from exc
    expected = candidate.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("download receipt SHA256 missing or invalid")
    return issuer_pattern, cik, expected


def verify_sec_interim_candidate(candidate, raw_path):
    """Return a Filing Diff candidate or an explicit rejection, without network."""
    url = candidate.get("url") if isinstance(candidate, dict) else None
    rejected = {"stato": "unverifiable", "url": url, "motivi": []}
    try:
        issuer_pattern, cik, digest = _catalog_identity(candidate)
        path = Path(raw_path).resolve(strict=True)
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError("source bytes differ from download receipt SHA256")
        extraction = estrai_testo(str(path), contenuto=raw)
        if extraction.get("stato") != "ok" or extraction.get("formato") != "html":
            raise ValueError("SEC interim HTML text unavailable")
        text = extraction["testo"]
        if not re.search(_STATEMENTS, text, re.I):
            if _NOTICE.search(text[:20_000]) and not _FINANCIAL_SIGNAL.search(text):
                return {"stato": "not_financial", "url": url,
                        "motivi": ["explicit notice without consolidated interim financial statements"]}
            raise ValueError("consolidated interim financial statement header missing")
        matches = list(re.finditer(_PERIOD, text, re.I))
        kinds = {_MONTHS[m["mesi"].lower()] for m in matches}
        if len(kinds) != 1:
            raise ValueError("accounting duration missing or ambiguous")
        profile = {
            "emittente_id": "CIK:" + cik, "lingua": "en",
            "tipo": kinds.pop(), "perimetro": "consolidato",
            "verifica": {"emittente": issuer_pattern, "lingua": _STATEMENTS,
                         "tipo": _STATEMENTS, "perimetro": _STATEMENTS,
                         "periodo": _PERIOD},
            "sezioni": {"conto_economico": {"inizio": _INCOME, "fine": _NEXT_STATEMENT}},
        }
        verified = verifica_documento(path, url=url, profilo=profile, catalogo={
            "emittente_id": "CIK:" + cik, "form": "6-K",
            "report_date": candidate["report_date"], "language": "en",
        })
        if verified["stato"] != "ok":
            rejected["motivi"] = verified["motivi"]
            return rejected
        document = verified["documento"]
        if document["sezioni"]["conto_economico"]["stato"] != "ok":
            raise ValueError("financial statement section absent or ambiguous")
        if (sha256(path.read_bytes()).hexdigest() != digest
                or document["sha256"] != digest
                or any(proof["sha256"] != digest for proof in document["prove_verifica"].values())):
            raise ValueError("source changed during verification")
        metadata = dict(document["metadati"])
        metadata.update(issuer=candidate["issuer"], form="6-K",
                        report_date=candidate["report_date"], accession=candidate["accession"])
        result = {"stato": "verificato", "url": url, "path": str(path),
                  "sha256": digest, "filed_date": candidate["filed_date"],
                  "metadati": metadata, "sezioni": document["sezioni"],
                  "prove_verifica": document["prove_verifica"]}
        if candidate.get("accepted_at") is not None:
            result["accepted_at"] = candidate["accepted_at"]
            metadata["accepted_at"] = candidate["accepted_at"]
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        rejected["motivi"].append(f"{type(exc).__name__}: {exc}")
        return rejected
