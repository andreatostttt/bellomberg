"""Data di pubblicazione dal deposito eMarket SDIR (decisione PM 04/10/2026).

Una relazione europea senza data di pubblicazione univoca in copertina prende la
data di DEPOSITO ufficiale (eMarket SDIR), dichiarata nella ricevuta come fonte;
ogni altro esito della fonte lascia il documento rifiutato con il motivo e
«data di pubblicazione da fornire dal PM». Emittente, numeri e protocolli sono
inventati; la fonte e' sempre stubbata (nessuna rete).
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from bellomberg.agents import trade_idea_sources as sources
from bellomberg.valuation.valuation_sources import collect_documents
from test_trade_idea_pm_sources import (TEXT as COVER_DATED_TEXT, URL, WEBSITE, transport,
                                        measured_no_real_transports)  # noqa: F401 (guardia autouse)

AS_OF = "2026-09-28"
IDENTITY = {"ticker": "ZZTEST.MI", "name": "Synthetic issuer", "exchange": "TEST", "currency": "EUR",
            "status": "confirmed"}
HALF_YEAR = ("Synthetic issuer\nHALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026\n"
             "Period ended 2026-06-30\nSynthetic narrative without any release header.\n")
TITLE = "Synthetic issuer: pubblicata la Relazione finanziaria semestrale al 30 giugno 2026"
LIST_URL = "https://www.emarketstorage.it/it/comunicati-finanziari?categoria=101&azienda=9999"


def deposit(**changes):
    value = {"ticker": "ZZTEST.MI", "isin": "IT0000000000", "emarket_id": 9999, "tipo": "semestrale",
             "periodo_fine": "2026-06-30", "stato": "ok", "errore": None, "motivo": None,
             "data_deposito": "2026-08-06", "ora_deposito": "10:49", "titolo": TITLE,
             "url": "https://www.emarketstorage.it/sites/default/files/comunicati/zztest-123.pdf",
             "protocollo": "123456", "categoria": 101, "lingua": "it", "candidati": [], "conferme": [],
             "fonte": "eMarket Storage (SDIR Teleborsa)", "categorie_cercate": [101, 150],
             "url_liste": [LIST_URL], "sha256_liste": {LIST_URL: "a" * 64}, "pagine_lette": 1,
             "letto_il": "2026-09-28T08:00:00+00:00", "limiti": ["limite sintetico"],
             "cache": {"stato": "nessuna", "eta_s": None}}
    value.update(changes)
    return value


class Lookup:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, ticker, *, tipo, periodo_fine):
        self.calls.append((ticker, tipo, periodo_fine))
        return deepcopy(self.result)


def ingest(tmp_path, *, text=HALF_YEAR, lookup=None, identity=IDENTITY, claims=None):
    download, _ = transport(text)
    return sources.ingest_document_sources(identity["ticker"], identity, AS_OF, [{"url": URL, **(claims or {})}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download, deposit_lookup=lookup)


def rejection(tmp_path, **kwargs):
    with pytest.raises(sources.SourceIngestionError) as blocked:
        ingest(tmp_path, **kwargs)
    return blocked.value.receipt["documents"][0]["reason"]


def test_cover_without_release_date_uses_the_declared_sdir_deposit(tmp_path):
    lookup = Lookup(deposit())
    result = ingest(tmp_path, lookup=lookup)
    assert lookup.calls == [("ZZTEST.MI", "semestrale", "2026-06-30")]
    row = result["receipt"]["documents"][0]
    assert row["filed_date"] == "2026-08-06"
    verification = row["metadati"]["pm_source_verification"]
    assert verification["publication_basis"] == "emarket_sdir_deposit_receipt"
    assert verification["claim_origins"]["publication"] == "emarket_sdir_deposit_receipt"
    assert verification["publication_declaration"] == (
        "data di deposito eMarket SDIR del 06/08/2026, documento «" + TITLE + "» (protocollo 123456)")
    assert verification["proofs"]["publication"]["value"] == "2026-08-06"
    sealed = verification["deposit_receipt"]
    archived = tmp_path / "pm-public-documents" / "publication-receipts" / (sealed["sha256"] + ".json")
    assert json.loads(archived.read_bytes())["sha256_liste"] == {LIST_URL: "a" * 64}
    public = sources.document_receipt_summary(result["receipt"])
    assert public["documents"][0]["publication_declaration"] == verification["publication_declaration"]
    assert public["documents"][0]["publication_basis"] == "emarket_sdir_deposit_receipt"
    # Riverifica senza rete: la fonte non viene richiamata.
    again = sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    assert again["receipt"] == result["receipt"] and len(lookup.calls) == 1
    assert result["documents"][0]["published_at"] == "2026-08-06"


@pytest.mark.parametrize("stato, errore", [("non_trovato", None), ("ambiguo", None), ("KO", "rete"),
                                           ("non_coperto", "dichiarato_non_su_emarket"), ("STALE", "rete")])
def test_every_non_ok_deposit_keeps_the_document_rejected_with_the_pm_hint(tmp_path, stato, errore):
    reason = rejection(tmp_path, lookup=Lookup(deposit(stato=stato, errore=errore, motivo="motivo sintetico",
                                                       data_deposito=None)))
    assert "eMarket SDIR deposit " + stato in reason
    assert "data di pubblicazione da fornire dal PM" in reason and "contradictory publication facts" in reason


def test_document_with_dated_cover_never_asks_the_deposit_and_keeps_its_receipt_shape(tmp_path):
    lookup = Lookup(deposit())
    result = ingest(tmp_path, text=COVER_DATED_TEXT, lookup=lookup)
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    assert lookup.calls == []
    assert verification["publication_basis"] == "dated_primary_text_quote"
    assert not {"deposit_receipt", "publication_declaration", "pm_publication_quote_not_evidence"} & set(verification)
    assert "publication_declaration" not in sources.document_receipt_summary(result["receipt"])["documents"][0]


def test_non_italian_listing_is_not_looked_up_and_keeps_the_original_reason(tmp_path):
    lookup = Lookup(deposit())
    other = {**IDENTITY, "ticker": "ZZTEST-EXT"}
    reason = rejection(tmp_path, lookup=lookup, identity=other)
    assert lookup.calls == [] and "contradictory publication facts" in reason
    assert "eMarket" not in reason


@pytest.mark.parametrize("changes, words", [
    ({"tipo": "annuale"}, "issuer, type or period"),
    ({"periodo_fine": "2026-03-31"}, "issuer, type or period"),
    ({"ticker": "OTHER.MI"}, "issuer, type or period"),
    ({"data_deposito": "2026-06-30"}, "not after the reporting period"),
    ({"data_deposito": "2026-10-01"}, "after the cutoff"),
    ({"titolo": "Synthetic issuer: approvata la relazione trimestrale"}, "does not name this document"),
    ({"sha256_liste": {}}, "issuer, type or period"),
])
def test_inconsistent_deposit_is_rejected(tmp_path, changes, words):
    reason = rejection(tmp_path, lookup=Lookup(deposit(**changes)))
    assert words in reason and "data di pubblicazione da fornire dal PM" in reason


def test_pm_release_claim_must_match_the_deposit(tmp_path):
    reason = rejection(tmp_path, lookup=Lookup(deposit()), claims={"published_at": "2026-08-07"})
    assert "conflicts with the eMarket SDIR deposit date" in reason


def test_pm_cover_quote_is_declared_as_not_evidence(tmp_path):
    quote = "HALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026"
    result = ingest(tmp_path, lookup=Lookup(deposit()), claims={"publication_quote": quote})
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    assert verification["pm_publication_quote_not_evidence"] == quote
    assert "publication" not in {key for key in verification["proofs"] if verification["proofs"][key].get("quote")}


def test_cover_without_a_unique_report_type_is_not_looked_up(tmp_path):
    lookup = Lookup(deposit())
    text = HALF_YEAR.replace("HALF-YEAR FINANCIAL REPORT", "INTERIM DOCUMENT")
    reason = rejection(tmp_path, lookup=lookup, text=text)
    assert lookup.calls == [] and "report type not unique" in reason and "da fornire dal PM" in reason


def test_reverification_detects_changed_archive_or_sealed_date(tmp_path):
    result = ingest(tmp_path, lookup=Lookup(deposit()))
    sealed = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["deposit_receipt"]
    archived = tmp_path / "pm-public-documents" / "publication-receipts" / (sealed["sha256"] + ".json")
    original = archived.read_bytes()
    archived.write_bytes(original.replace(b"2026-08-06", b"2026-08-05"))
    with pytest.raises(ValueError, match="deposit receipt bytes changed"):
        sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
            archive_root=tmp_path, issuer_website=WEBSITE)
    archived.write_bytes(original)
    forged = deepcopy(result["receipt"])
    forged["documents"][0]["metadati"]["pm_source_verification"]["deposit_receipt"]["data_deposito"] = "2026-08-05"
    forged["fingerprint"] = sources._receipt_fingerprint(forged)
    with pytest.raises(ValueError, match="metadata changed"):
        sources.verify_document_receipt(forged, IDENTITY["ticker"], IDENTITY, AS_OF,
            archive_root=tmp_path, issuer_website=WEBSITE)


def _collect(tmp_path, candidate):
    return collect_documents(IDENTITY["ticker"], as_of=AS_OF, archive_root=tmp_path,
        filing_results=[{"ticker": IDENTITY["ticker"], "candidati": [candidate], "motivi": []}],
        catalog=lambda *_: {"stato": "ok", "documenti": [], "motivi": []})


@pytest.mark.parametrize("tamper", ["filed_date", "declaration", "missing_receipt", "basis", "sealed_date",
                                    "report_date", "archive"])
def test_collector_reverifies_the_deposit_without_network(tmp_path, tamper):
    result = ingest(tmp_path, lookup=Lookup(deposit()))
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    verification = candidate["metadati"]["pm_source_verification"]
    if tamper == "filed_date":
        candidate["filed_date"] = "2026-08-07"
    elif tamper == "declaration":
        verification["publication_declaration"] = "data di deposito eMarket SDIR del 07/08/2026"
    elif tamper == "missing_receipt":
        del verification["deposit_receipt"]
    elif tamper == "basis":
        verification["publication_basis"] = "dated_primary_text_quote"
    elif tamper == "sealed_date":
        verification["deposit_receipt"]["data_deposito"] = "2026-08-07"
    elif tamper == "report_date":
        candidate["metadati"]["report_date"] = "2026-03-31"
    else:
        archived = (tmp_path / "pm-public-documents" / "publication-receipts"
                    / (verification["deposit_receipt"]["sha256"] + ".json"))
        archived.write_bytes(archived.read_bytes() + b" ")
    report = _collect(tmp_path, candidate)
    assert report["documents"] == [] and report["issues"], tamper


# ---------------------------------------------------------------------------------------------
# Decisioni PM 04/10 sera: periodo dalla copertina, emittente nel testo intero, deposito STALE.
EUROPEAN = ("ACCELERATING\nGROWTH\nHALF-YEAR \nFINANCIAL REPORT \nAT 30 JUNE 2026\nSYNTH | CONTENTS\n"
            + "Balance as at 31 December 2025 and as at 30 June 2026.\n" * 2
            + "Narrative filler line.\n" * 700
            + "The report was approved by the Board of Directors of Synthetic issuer on July 29, 2026.\n")


def test_cover_period_and_full_text_issuer_are_accepted_and_declared(tmp_path):
    assert EUROPEAN.index("Synthetic issuer") > 12000
    result = ingest(tmp_path, text=EUROPEAN, lookup=Lookup(deposit()))
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["metadati"]["report_date"] == "2026-06-30" and row["filed_date"] == "2026-08-06"
    assert verification["report_date_basis"] == "cover_report_title_period/1"
    assert verification["report_date_locator"]["quote"] == "HALF-YEAR \nFINANCIAL REPORT \nAT 30 JUNE 2026"
    assert verification["report_date_locator"]["value"] == "2026-06-30"
    issuer = verification["issuer_locator"]
    assert verification["issuer_basis"] == "confirmed_issuer_name_in_full_text/1"
    assert issuer["name"] == "Synthetic issuer" and "Synthetic issuer" in issuer["quote"]
    assert issuer["offset"] == EUROPEAN.index(issuer["quote"]) and "no issuer-name field" in issuer["name_source"]
    assert verification["claim_origins"] == {"publication": "emarket_sdir_deposit_receipt",
        "report_date": "cover_report_title_period/1", "issuer": "confirmed_issuer_name_in_full_text/1"}
    again = sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    assert again["receipt"] == result["receipt"]


def test_two_periods_on_the_cover_are_rejected(tmp_path):
    text = EUROPEAN.replace("AT 30 JUNE 2026\n", "AT 30 JUNE 2026\nHALF-YEAR FINANCIAL REPORT AT 31 DECEMBER 2025\n")
    reason = rejection(tmp_path, text=text, lookup=Lookup(deposit()))
    assert "contradictory on the document cover" in reason


def test_pm_quotes_that_are_not_in_the_document_are_declared_not_used(tmp_path):
    result = ingest(tmp_path, text=EUROPEAN, lookup=Lookup(deposit()),
                    claims={"report_date_quote": "at 30 June 2026 (absent)", "issuer_quote": "Synthetic issuer IR page"})
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    assert verification["pm_report_date_quote_not_evidence"] == "at 30 June 2026 (absent)"
    assert verification["pm_issuer_quote_not_evidence"] == "Synthetic issuer IR page"


def test_issuer_name_absent_everywhere_is_declared_as_rejection(tmp_path):
    text = EUROPEAN.replace("Board of Directors of Synthetic issuer", "Board of Directors")
    reason = rejection(tmp_path, text=text, lookup=Lookup(deposit()))
    assert "Confirmed issuer name is absent" in reason


def test_non_italian_listing_keeps_the_old_strict_locators(tmp_path):
    other = {**IDENTITY, "ticker": "ZZTEST-EXT"}
    reason = rejection(tmp_path, text=EUROPEAN, lookup=Lookup(deposit()), identity=other)
    assert "contradictory publication facts" in reason


def test_stale_deposit_is_accepted_and_declared(tmp_path):
    stale = deposit(stato="STALE", stato_originale="ok", letto_il="2026-09-20T07:30:00+00:00",
                    motivo="rete: guasto sintetico")
    result = ingest(tmp_path, lookup=Lookup(stale))
    declaration = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["publication_declaration"]
    assert declaration.endswith("; data di deposito letta il 20/09, fonte ora non raggiungibile")
    sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)


@pytest.mark.parametrize("changes", [{"stato_originale": "non_trovato"}, {"stato_originale": None},
                                     {"stato_originale": "ok", "letto_il": "non una data"}])
def test_stale_without_an_original_ok_is_rejected(tmp_path, changes):
    reason = rejection(tmp_path, lookup=Lookup(deposit(stato="STALE", **changes)))
    assert "da fornire dal PM" in reason


@pytest.mark.parametrize("tamper", ["period_quote", "period_value", "period_missing", "issuer_offset",
                                    "issuer_page", "issuer_name", "issuer_missing"])
def test_collector_reverifies_cover_period_and_issuer_locators(tmp_path, tamper):
    result = ingest(tmp_path, text=EUROPEAN, lookup=Lookup(deposit()))
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    verification = candidate["metadati"]["pm_source_verification"]
    if tamper == "period_quote":
        verification["report_date_locator"]["quote"] = "AT 30 JUNE 2026"
    elif tamper == "period_value":
        verification["report_date_locator"]["value"] = "2025-12-31"
    elif tamper == "period_missing":
        del verification["report_date_locator"]
    elif tamper == "issuer_offset":
        verification["issuer_locator"]["offset"] += 1
    elif tamper == "issuer_page":
        verification["issuer_locator"]["page"] = 7
    elif tamper == "issuer_name":
        verification["issuer_locator"]["name"] = "Other issuer"
    else:
        del verification["issuer_locator"]
    report = _collect(tmp_path, candidate)
    assert report["documents"] == [] and report["issues"], tamper


# ---------------------------------------------------------------------------------------------
# Interfaccia IT1 aggiornata: titolo generico confermato dal PDF del comunicato, ISIN automatico.
def notice_pdf(sentence="e' stata messa a disposizione del pubblico la Relazione Finanziaria Semestrale al 30 giugno 2026"):
    from io import BytesIO
    from reportlab.pdfgen.canvas import Canvas
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(40, 780, "Synthetic issuer - comunicato")
    canvas.drawString(40, 760, sentence)
    canvas.save()
    return buffer.getvalue()


def generic_deposit(pdf_bytes, **changes):
    from hashlib import sha256 as digest
    url = "https://www.emarketstorage.it/sites/default/files/comunicati/zztest-generico.pdf"
    return deposit(titolo="Synthetic issuer: deposito documenti", url=url, prova="testo_pdf",
                   sha256_pdf={url: digest(pdf_bytes).hexdigest()}, **changes)


class Fetch:
    def __init__(self, raw):
        self.raw, self.calls = raw, []

    def __call__(self, url):
        self.calls.append(url)
        return self.raw


def test_generic_title_confirmed_by_the_notice_pdf_is_archived_declared_and_reverified(tmp_path):
    raw = notice_pdf()
    fetch = Fetch(raw)
    download, _ = transport(HALF_YEAR)
    result = sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download,
        deposit_lookup=Lookup(generic_deposit(raw)), deposit_pdf_fetch=fetch)
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    assert len(fetch.calls) == 1
    assert verification["deposit_receipt"]["prova"] == "testo_pdf"
    assert "letti nel PDF del comunicato" in verification["publication_declaration"]
    sha = verification["deposit_receipt"]["sha256_pdf"][verification["deposit_receipt"]["url"]]
    archived = tmp_path / "pm-public-documents" / "publication-receipts" / (sha + ".pdf")
    assert archived.read_bytes() == raw
    sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    archived.write_bytes(notice_pdf("altro testo senza il documento"))
    with pytest.raises(ValueError, match="deposit PDF bytes changed"):
        sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
            archive_root=tmp_path, issuer_website=WEBSITE)
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, candidate)["documents"] == []


@pytest.mark.parametrize("case", ["other_bytes", "pdf_without_document"])
def test_generic_title_without_matching_notice_pdf_is_rejected(tmp_path, case):
    # reportlab non e' deterministico: ogni PDF si genera UNA volta e si riusa.
    if case == "other_bytes":
        sealed, served = generic_deposit(notice_pdf()), notice_pdf("altro")
    else:
        served = notice_pdf("comunicato senza periodo")
        sealed = generic_deposit(served)
    download, _ = transport(HALF_YEAR)
    with pytest.raises(sources.SourceIngestionError) as blocked:
        sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
            archive_root=tmp_path, issuer_website=WEBSITE, download=download,
            deposit_lookup=Lookup(sealed), deposit_pdf_fetch=Fetch(served))
    reason = blocked.value.receipt["documents"][0]["reason"]
    assert "da fornire dal PM" in reason
    assert ("differs from the bytes" in reason) if case == "other_bytes" else ("does not name this document" in reason)


def isin_lookup(result):
    calls = []
    def lookup(ticker, *, nome):
        calls.append((ticker, nome))
        return deepcopy(result)
    lookup.calls = calls
    return lookup


def test_automatic_isin_resolution_uses_the_issuer_name_and_is_declared(tmp_path):
    resolver = isin_lookup({"stato": "ok", "isin": "IT0000000000", "emarket": 9999, "negozio": "automatico",
                            "fonte_url": "https://www.borsaitaliana.it/STUB", "letto_il": "2026-09-28T08:00:00+00:00"})
    download, _ = transport(HALF_YEAR)
    result = sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download,
        deposit_lookup=Lookup(deposit(voce_da="automatico")), isin_lookup=resolver)
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    assert resolver.calls == [("ZZTEST.MI", "Synthetic issuer")]
    assert verification["deposit_receipt"]["isin_resolution"]["negozio"] == "automatico"
    assert verification["publication_declaration"].endswith("ISIN IT0000000000 risolto automaticamente su Borsa Italiana")


@pytest.mark.parametrize("result, words", [
    ({"stato": "non_trovato", "motivo": "serve il nome dell'emittente"}, "ISIN resolution non_trovato"),
    ({"stato": "ok", "isin": "IT9999999999", "emarket": 1, "negozio": "automatico", "fonte_url": "x",
      "letto_il": "2026-09-28T08:00:00+00:00"}, "differs from the eMarket SDIR deposit issuer"),
])
def test_failed_or_inconsistent_isin_resolution_is_rejected(tmp_path, result, words):
    download, _ = transport(HALF_YEAR)
    with pytest.raises(sources.SourceIngestionError) as blocked:
        sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
            archive_root=tmp_path, issuer_website=WEBSITE, download=download,
            deposit_lookup=Lookup(deposit()), isin_lookup=isin_lookup(result))
    reason = blocked.value.receipt["documents"][0]["reason"]
    assert words in reason and "da fornire dal PM" in reason


HEADER_DATED_COVER_PERIOD = ("Synthetic issuer\nPublished on 2026-09-09\nHALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026\n"
                             "Balance as at 31 December 2025 and as at 30 June 2026.\n")


def test_cover_period_rule_applies_only_to_italian_listings(tmp_path):
    lookup = Lookup(deposit())
    result = ingest(tmp_path / "it", text=HEADER_DATED_COVER_PERIOD, lookup=lookup)
    row = result["receipt"]["documents"][0]
    assert row["metadati"]["report_date"] == "2026-06-30" and lookup.calls == []
    other = {**IDENTITY, "ticker": "ZZTEST-EXT"}
    reason = rejection(tmp_path / "ext", text=HEADER_DATED_COVER_PERIOD, identity=other)
    assert "Reporting period requires a unique explicit dated primary-text disclosure" in reason


@pytest.mark.parametrize("original", [None, "non_trovato"])
def test_offline_check_rejects_stale_without_an_original_ok(tmp_path, original):
    value = {"contract": sources._DEPOSIT_CONTRACT, **deposit(stato="STALE", stato_originale=original,
             letto_il="2026-09-20T07:30:00+00:00")}
    with pytest.raises(ValueError, match="issuer, type or period"):
        sources._check_deposit_value(value, "ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, tmp_path)


def test_sealed_evidence_kind_must_match_the_recomputed_one(tmp_path):
    raw = notice_pdf()
    sealed = {**generic_deposit(raw), "titolo": TITLE}  # il titolo basta (stadio 1) ma il sigillo dice testo_pdf
    download, _ = transport(HALF_YEAR)
    with pytest.raises(sources.SourceIngestionError) as blocked:
        sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
            archive_root=tmp_path, issuer_website=WEBSITE, download=download,
            deposit_lookup=Lookup(sealed), deposit_pdf_fetch=Fetch(raw))
    assert "does not name this document" in blocked.value.receipt["documents"][0]["reason"]
