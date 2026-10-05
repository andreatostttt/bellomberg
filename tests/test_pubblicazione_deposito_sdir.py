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


# AGGIUNTA 5: risposte salvate sintetiche; la riverifica completa (sdir.riverifica_deposito) e' finta in tutto il
# file e conferma SOLO queste (i moduli veri si provano nei loro test e nella prova di RV-D4).
SAVED = {LIST_URL: {"codifica": "gzip+base64", "corpo": "eA=="}}


@pytest.fixture(autouse=True)
def sdir_full_recheck(monkeypatch):
    from bellomberg.market_data import sdir
    calls = []

    def fake(ricevuta, *, ticker, tipo, periodo_fine, fine_esercizio=None):
        calls.append((ticker, tipo, periodo_fine, fine_esercizio, ricevuta.get("protocollo")))
        return ricevuta.get("risposte_salvate") == SAVED, "riverifica sintetica"
    monkeypatch.setattr(sdir, "riverifica_deposito", fake)
    return calls


def deposit(**changes):
    value = {"ticker": "ZZTEST.MI", "isin": "IT0000000000", "emarket_id": 9999, "tipo": "semestrale",
             "periodo_fine": "2026-06-30", "stato": "ok", "errore": None, "motivo": None,
             "data_deposito": "2026-08-06", "ora_deposito": "10:49", "titolo": TITLE,
             "url": "https://www.emarketstorage.it/sites/default/files/comunicati/zztest-123.pdf",
             "protocollo": "123456", "categoria": 101, "lingua": "it", "candidati": [], "conferme": [],
             "fonte": "eMarket Storage (SDIR Teleborsa)", "categorie_cercate": [101, 150],
             "url_liste": [LIST_URL], "sha256_liste": {LIST_URL: "a" * 64}, "pagine_lette": 1,
             "letto_il": "2026-09-28T08:00:00+00:00", "limiti": ["limite sintetico"],
             "cache": {"stato": "nessuna", "eta_s": None}, "risposte_salvate": deepcopy(SAVED)}
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
    value["ricevuta_fonte"] = {key: item for key, item in value.items() if key != "contract"}
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


# ---------------------------------------------------------------------------------------------
# D4b 05/10: tipo della relazione dalla copertina (nome + periodo entro 60 caratteri), trimestrali e
# resoconti italiani end-to-end, listini esteri senza fonte ufficiale (limite dichiarato), nome emittente.
@pytest.mark.parametrize("cover, period, tipo", [
    ("Relazione finanziaria semestrale consolidata al 30 giugno 2026", "2026-06-30", "semestrale"),
    ("Bilancio consolidato semestrale abbreviato al 30 giugno 2026", "2026-06-30", "semestrale"),
    ("Half-year financial report as at June 30, 2026", "2026-06-30", "semestrale"),
    ("Resoconto intermedio di gestione al 30 settembre 2026", "2026-09-30", "trimestrale"),
    ("Additional periodic financial information as at 31 March 2026", "2026-03-31", "trimestrale"),
    ("Relazione finanziaria annuale al 31 dicembre 2025", "2025-12-31", "annuale"),
    ("Annual Report 2025", "2025-12-31", "annuale"),
    ("Relazione finanziaria annuale 2025", "2025-12-31", "annuale"),
    ("Relazione finanziaria annuale per l'esercizio 2025", "2025-12-31", "annuale"),
    ("Interim financial report at 30 June 2026", "2026-06-30", "semestrale"),
    ("Interim financial statements at 30 September 2026", "2026-09-30", "trimestrale"),
    ("HALF-YEAR FINANCIAL REPORT - INTERIM FINANCIAL REPORT AT 30 JUNE 2026", "2026-06-30", "semestrale"),
])
def test_document_type_is_the_name_bound_to_the_period(cover, period, tipo):
    assert sources._deposit_document_type("Synthetic issuer\n" + cover + "\nSynthetic narrative.\n", period) == tipo


@pytest.mark.parametrize("cover, period", [
    ("Annual Report 2025", "2025-06-30"),                       # esercizio non solare: l'anno non basta
    ("Annual Report 2025", "2026-12-31"),                       # anno diverso dal periodo
    ("Annual Report 2025/2026", "2025-12-31"),                  # esercizio a cavallo
    ("Interim financial report at 31 December 2026", "2026-12-31"),   # IAS 34, mese non deciso
    ("Resoconto intermedio di gestione al 30 settembre", "2026-09-30"),  # periodo senza anno
    ("Half-year financial report at 30 June 2026", "2026-09-30"),     # data diversa dal periodo
    ("Relazione finanziaria semestrale e resoconto intermedio di gestione al 30 giugno 2026", "2026-06-30"),
])
def test_document_type_is_not_decided_without_a_unique_binding(cover, period):
    assert sources._deposit_document_type("Synthetic issuer\n" + cover + "\nSynthetic narrative.\n", period) is None


@pytest.mark.parametrize("gap, tipo", [(60, "semestrale"), (61, None)])
def test_period_window_is_sixty_characters(gap, tipo):
    filler = " " + "x" * (gap - 2) + " "     # il periodo comincia a `gap` caratteri dalla fine del nome
    text = "Relazione finanziaria semestrale" + filler + "30 giugno 2026\n"
    assert len(filler) == gap
    assert sources._deposit_document_type(text, "2026-06-30") == tipo


def test_annual_bare_year_is_bound_only_right_after_the_name():
    text = ("Annual Report and other documents of the group published during 2025 for shareholders\n"
            "Year ended 31 December 2025\n")
    assert sources._deposit_document_type(text, "2025-12-31") is None


def test_cover_period_accepts_a_consolidated_qualifier():
    rows = sources._cover_period_context("Synthetic issuer\nRelazione finanziaria semestrale consolidata al "
                                         "30 giugno 2026\nnarrativa\n")
    assert [row[0] for row in rows] == ["2026-06-30"]


@pytest.mark.parametrize("cover, period, tipo, title, filed", [
    ("RESOCONTO INTERMEDIO DI GESTIONE AL 31 MARZO 2026", "2026-03-31", "trimestrale",
     "Synthetic issuer: pubblicato il Resoconto intermedio di gestione al 31 marzo 2026", "2026-05-14"),
    ("Informazioni finanziarie periodiche aggiuntive al 31 marzo 2026", "2026-03-31", "trimestrale",
     "Synthetic issuer: informazioni finanziarie periodiche aggiuntive al 31 marzo 2026", "2026-05-15"),
    ("RELAZIONE FINANZIARIA ANNUALE AL 31 DICEMBRE 2025", "2025-12-31", "annuale",
     "Synthetic issuer: pubblicata la relazione finanziaria annuale al 31 dicembre 2025", "2026-04-02"),
    ("RELAZIONE FINANZIARIA ANNUALE 2025\nYear ended 31 December 2025", "2025-12-31", "annuale",
     "Synthetic issuer: pubblicata la relazione finanziaria annuale per l'esercizio 2025", "2026-04-02"),
    ("RELAZIONE FINANZIARIA SEMESTRALE CONSOLIDATA AL 30 GIUGNO 2026", "2026-06-30", "semestrale", TITLE,
     "2026-08-06"),
])
def test_italian_quarterly_interim_and_annual_reports_end_to_end(tmp_path, cover, period, tipo, title, filed):
    # copertina -> tipo -> get_data_deposito(tipo=...) -> ricevuta -> riverifica senza rete -> collettore
    text = "Synthetic issuer\n%s\nSynthetic narrative without any release header.\n" % cover
    lookup = Lookup(deposit(tipo=tipo, periodo_fine=period, titolo=title, data_deposito=filed))
    result = ingest(tmp_path, text=text, lookup=lookup)
    assert lookup.calls == [("ZZTEST.MI", tipo, period)]
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["filed_date"] == filed and row["metadati"]["report_date"] == period
    assert verification["deposit_receipt"]["tipo"] == tipo
    assert (verification.get("report_date_basis") == "cover_report_title_period/1") == ("Year ended" not in cover)
    again = sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    assert again["receipt"] == result["receipt"] and len(lookup.calls) == 1
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    # Il collettore ricalcola il tipo dal testo: una ricevuta di un altro tipo non passa.
    with pytest.raises(ValueError, match="issuer, type or period"):
        sources.verify_deposit_companion(verification["deposit_receipt"], tmp_path / "pm-public-documents",
            "ZZTEST.MI", "semestrale" if tipo != "semestrale" else "trimestrale", period, AS_OF)


def test_collector_recomputes_the_type_from_the_archived_text(tmp_path, monkeypatch):
    result = ingest(tmp_path, lookup=Lookup(deposit()))
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    monkeypatch.setattr(sources, "_deposit_document_type", lambda text, report_date, names=None: "trimestrale")
    report = _collect(tmp_path, candidate)
    assert report["documents"] == [] and "issuer, type or period" in report["issues"][0]["reason"]


def test_two_document_types_bound_to_the_same_period_are_not_looked_up(tmp_path):
    text = ("Synthetic issuer\nRELAZIONE FINANZIARIA SEMESTRALE E RESOCONTO INTERMEDIO DI GESTIONE AL 30 GIUGNO 2026\n"
            "Period ended 2026-06-30\n")
    lookup = Lookup(deposit())
    reason = rejection(tmp_path, text=text, lookup=lookup)
    assert lookup.calls == [] and "report type not unique" in reason
    assert "within 60 characters" in reason and reason.endswith("da fornire dal PM")


def test_document_name_far_from_the_period_does_not_decide_the_type(tmp_path):
    text = ("Synthetic issuer\nHALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026\n"
            "Contents: annual report of the previous year, published on 31 December 2025\n"
            "Period ended 2026-06-30\n")
    lookup = Lookup(deposit())
    ingest(tmp_path, text=text, lookup=lookup)
    assert lookup.calls == [("ZZTEST.MI", "semestrale", "2026-06-30")]


@pytest.mark.parametrize("suffix, words", [
    (".DE", "no official publication-date source (Xetra, .DE): Germany: no free official source"),
    (".L", "no official publication-date source (London SE, .L): United Kingdom:"),
    (".HK", "no official publication-date source (HKEX, .HK): official deposit sources are wired only"),
])
def test_uncovered_listings_declare_the_missing_official_source(tmp_path, suffix, words):
    lookup = EULookup(eu_deposit())
    reason = rejection(tmp_path, lookup=lookup, identity={**IDENTITY, "ticker": "ZZTEST" + suffix})
    assert lookup.calls == []
    assert words in reason and reason.endswith("da fornire dal PM")


@pytest.mark.parametrize("ticker", ["ZZTEST", "ZZTEST.B"])
def test_no_listing_claim_without_a_market_suffix(tmp_path, ticker):
    reason = rejection(tmp_path, lookup=Lookup(deposit()), identity={**IDENTITY, "ticker": ticker})
    assert "no official publication-date source" not in reason


def valid_isin(prefix):
    from bellomberg.market_data.depositi_ue import isin_valido
    return next(prefix + "000000012" + str(digit) for digit in range(10) if isin_valido(prefix + "000000012" + str(digit)))


def test_deposit_route_reads_the_shared_registries():
    assert sources._deposit_route("ZZTEST.MI") == "sdir_it"
    assert sources._deposit_route("zztest.pa") == "depositi_ue"
    assert sources._deposit_route("ZZTEST.MC") == "depositi_ue"          # supplemento di depositi_ue: ES
    assert sources._deposit_route("ZZTEST.DE") is None
    assert sources._deposit_route("ZZTEST") is None
    assert sources._deposit_route("ZZTEST.MI", valid_isin("NL")) == "sdir_it"       # .MI sempre SDIR (EU-R 05/10)
    assert sources._deposit_route("ZZTEST.DE", valid_isin("FR")) == "depositi_ue"   # Stato d'origine dall'ISIN
    assert sources._deposit_route("ZZTEST.PA", valid_isin("IT")) is None            # ISIN IT fuori da .MI: no
    assert sources._deposit_route("ZZTEST.PA", "NL0000000000") is None             # ISIN non valido: niente rete
    assert sources._publication_limit_note("ZZTEST.MI") is None
    assert sources._publication_limit_note("ZZTEST.PA") is None
    assert sources._publication_limit_note("ZZTEST.PA", "NL0000000000").startswith(
        "no official publication-date source (Euronext Paris, .PA): the identity ISIN cannot route")


def test_isin_resolution_needs_the_confirmed_name_not_the_symbol(tmp_path):
    resolver = isin_lookup({"stato": "ok", "isin": "IT0000000000", "emarket": 9999, "negozio": "automatico",
                            "fonte_url": "x", "letto_il": "2026-09-28T08:00:00+00:00"})
    download, _ = transport(HALF_YEAR)
    identity = {**IDENTITY, "name": "zztest"}
    with pytest.raises(sources.SourceIngestionError) as blocked:
        sources.ingest_document_sources(identity["ticker"], identity, AS_OF, [{"url": URL}],
            archive_root=tmp_path, issuer_website=WEBSITE, download=download,
            deposit_lookup=Lookup(deposit()), isin_lookup=resolver)
    reason = blocked.value.receipt["documents"][0]["reason"]
    assert resolver.calls == [] and "equals the ticker symbol" in reason and "da fornire dal PM" in reason


def test_isin_resolution_seals_the_confirmed_name_used(tmp_path):
    resolver = isin_lookup({"stato": "ok", "isin": "IT0000000000", "emarket": 9999, "negozio": "automatico",
                            "fonte_url": "x", "letto_il": "2026-09-28T08:00:00+00:00"})
    download, _ = transport(HALF_YEAR)
    result = sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download,
        deposit_lookup=Lookup(deposit(voce_da="automatico")), isin_lookup=resolver)
    sealed = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["deposit_receipt"]
    assert sealed["isin_resolution"]["nome_cercato"] == "Synthetic issuer"


# ---------------------------------------------------------------------------------------------
# Router SDIR (ON 05/10): la data puo' venire da 1INFO-SDIR (data di STOCCAGGIO del documento).
ONEINFO_LIST = ("POST https://www.1info.it/PORTALE1INFO/API/Documenti?dataStoccaggio.from=1&dataStoccaggio.to=2"
                "&emittente=99901&start=0")
ONEINFO_TITLE = "Relazione finanziaria semestrale al 30 giugno 2026"


def oneinfo_deposit(**changes):
    value = deposit(emarket_id=None, titolo=ONEINFO_TITLE, categoria="1.2", protocollo="900002_oneinfo",
                    data_deposito="2026-07-30", ora_deposito="14:05",
                    url="https://www.1info.it/PdfViewer/PdfShow.aspx?service=&type=documenti&year=2026"
                        "&file=900002_oneinfo.pdf&download=1",
                    fonte="1INFO-SDIR (Storage Computershare)", categorie_cercate=["1.2"],
                    url_liste=[ONEINFO_LIST], sha256_liste={ONEINFO_LIST: "b" * 64}, prova="titolo",
                    oneinfo_ndg=99901, esef=False, consolidato=None, tipo_data="stoccaggio_documento",
                    sdir="1INFO-SDIR", natura="documento",
                    instradamento={"regola": "negozio_confermato", "scelta": "oneinfo", "emarket_id": None,
                                   "oneinfo_ndg": 99901, "perche": "misura sintetica del 05/10",
                                   "emarket": {"ultimo": "2026-10-01"}, "oneinfo": {"ultimo": "2026-10-02"},
                                   "errore": None, "motivo": None})
    value.update(changes)
    return value


def test_oneinfo_storage_date_is_declared_sealed_and_reverified(tmp_path):
    lookup = Lookup(oneinfo_deposit())
    result = ingest(tmp_path, lookup=lookup)
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["filed_date"] == "2026-07-30"
    assert verification["publication_declaration"] == (
        "data di stoccaggio 1INFO-SDIR del 30/07/2026, documento «" + ONEINFO_TITLE + "» (protocollo 900002_oneinfo)")
    sealed = verification["deposit_receipt"]
    assert sealed["sdir"] == "1INFO-SDIR" and sealed["tipo_data"] == "stoccaggio_documento"
    # dell'instradamento si sigilla solo la parte deterministica (niente date dell'ultimo comunicato)
    assert sealed["instradamento"] == {"regola": "negozio_confermato", "scelta": "oneinfo", "emarket_id": None,
                                       "oneinfo_ndg": 99901}
    sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    assert len(lookup.calls) == 1
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    routed = deepcopy(candidate)
    routed["metadati"]["pm_source_verification"]["deposit_receipt"]["instradamento"]["oneinfo_ndg"] = 99902
    assert _collect(tmp_path, routed)["documents"] == []      # ogni campo sigillato e' confrontato con l'archivio
    candidate["metadati"]["pm_source_verification"]["deposit_receipt"]["sdir"] = "eMarket SDIR"
    assert _collect(tmp_path, candidate)["documents"] == []


@pytest.mark.parametrize("changes, words", [
    ({"categoria": "1.1"}, "1INFO-SDIR deposit does not name this document"),
    ({"tipo_data": None}, "1INFO-SDIR deposit does not name this document"),
    ({"prova": "testo_pdf"}, "1INFO-SDIR deposit does not name this document"),
    ({"titolo": "Il CdA approva la relazione finanziaria semestrale al 30 giugno 2026"}, "does not name this document"),
    ({"instradamento": {"regola": "negozio_confermato", "scelta": "emarket", "emarket_id": 1, "oneinfo_ndg": None}},
     "SDIR routing of the deposit receipt differs"),
    ({"sdir": "eMarket SDIR + 1INFO-SDIR"}, "SDIR routing of the deposit receipt differs"),
    ({"sdir": "altro SDIR", "instradamento": None}, "Deposit receipt SDIR is unknown"),
])
def test_inconsistent_oneinfo_deposit_is_rejected(tmp_path, changes, words):
    reason = rejection(tmp_path, lookup=Lookup(oneinfo_deposit(**changes)))
    assert words in reason and reason.endswith("da fornire dal PM")


def test_emarket_date_through_the_router_keeps_the_emarket_declaration(tmp_path):
    routed = deposit(sdir="eMarket SDIR", instradamento={"regola": "misura_automatica", "scelta": "emarket",
                     "emarket_id": 9999, "oneinfo_ndg": None, "perche": "x"})
    result = ingest(tmp_path, lookup=Lookup(routed))
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    assert verification["publication_declaration"].startswith("data di deposito eMarket SDIR del 06/08/2026")
    assert verification["deposit_receipt"]["sdir"] == "eMarket SDIR"


def test_router_without_sdir_names_the_failure(tmp_path):
    failed = deposit(stato="non_coperto", errore="nessuno_sdir", motivo="emittente non su eMarket SDIR ne' su 1INFO-SDIR",
                     data_deposito=None, sdir=None)
    reason = rejection(tmp_path, lookup=Lookup(failed))
    assert "SDIR deposit non_coperto (nessuno_sdir)" in reason and "da fornire dal PM" in reason


def test_production_lookup_is_the_sdir_router_with_the_confirmed_name(tmp_path, monkeypatch):
    from bellomberg.market_data import sdir
    calls = []

    def fake(ticker, *, tipo, periodo_fine, nome=None, fine_esercizio=None):
        calls.append((ticker, tipo, periodo_fine, nome))
        return deepcopy(oneinfo_deposit())
    monkeypatch.setattr(sdir, "get_data_deposito", fake)
    resolver = isin_lookup({"stato": "ok", "isin": "IT0000000000", "emarket": None, "negozio": "confermato",
                            "fonte_url": None, "letto_il": None})
    root = tmp_path / "pm-public-documents"
    sealed = sources._acquire_deposit_companion("ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, root,
                                                issuer_name="Synthetic issuer", isin_lookup=resolver)
    assert calls == [("ZZTEST.MI", "semestrale", "2026-06-30", "Synthetic issuer")]
    assert sealed["sdir"] == "1INFO-SDIR"
    sources.verify_deposit_companion(sealed, root, "ZZTEST.MI", "semestrale", "2026-06-30", AS_OF)


@pytest.mark.parametrize("sealed_prova, accepted", [("esef", True), ("titolo", False)])
def test_oneinfo_annual_esef_anchor_must_match_the_sealed_evidence(tmp_path, sealed_prova, accepted):
    text = "Synthetic issuer\nRELAZIONE FINANZIARIA ANNUALE AL 31 DICEMBRE 2025\nSynthetic narrative.\n"
    lookup = Lookup(oneinfo_deposit(tipo="annuale", periodo_fine="2025-12-31", categoria="1.1", esef=True,
                                    consolidato=1, prova=sealed_prova, data_deposito="2026-04-02",
                                    titolo="Relazione finanziaria annuale 2025 - ESEF"))
    if accepted:
        row = ingest(tmp_path, text=text, lookup=lookup)["receipt"]["documents"][0]
        assert row["metadati"]["pm_source_verification"]["deposit_receipt"]["prova"] == "esef"
    else:
        assert "1INFO-SDIR deposit does not name this document" in rejection(tmp_path, text=text, lookup=lookup)


# ---------------------------------------------------------------------------------------------
# Listini UE non italiani (decisione PM 05/10): depositi_ue di EU-R, ricevuta sigillata intera, riverifica
# senza rete col modulo del paese, natura della data nel testo al PM. Emittente e numeri inventati.
SYNTH_LEI = "ZZ00TESTLEI000000001"


def identity_eu_result(ticker, *, nome, isin=None, fonte_isin=None):
    ok = bool(isin)
    return {"stato": "ok" if ok else "non_trovato", "errore": None if ok else "nome_non_trovato",
            "motivo": None if ok else "nessun LEI univoco (sintetico)", "isin": isin if ok else None,
            "lei": SYNTH_LEI if ok else None, "nome_ufficiale": nome, "isin_chiamante": isin,
            "fonte_modulo": "bellomberg.market_data.identita_ue", "risposte_salvate": {}, "sha256_liste": {}}


@pytest.fixture(autouse=True)
def identity_eu_offline(monkeypatch):
    """Identita' UE (GLEIF) finta in TUTTO il file: questo file non prova identita_ue (tests/test_identita_ue.py)."""
    from bellomberg.market_data import identita_ue
    calls = {"risolvi": [], "riverifica": []}

    def risolvi(ticker, *, nome, isin=None, fonte_isin="x"):
        calls["risolvi"].append((ticker, nome, isin))
        return identity_eu_result(ticker, nome=nome, isin=isin)

    def riverifica(ricevuta, *, ticker, nome, isin=None):
        calls["riverifica"].append((ticker, nome, isin))
        return ricevuta == identity_eu_result(ticker, nome=nome, isin=isin), "sintetica"
    monkeypatch.setattr(identita_ue, "risolvi_identita_ue", risolvi)
    monkeypatch.setattr(identita_ue, "riverifica_identita", riverifica)
    return calls


class EULookup:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None):
        self.calls.append((ticker, tipo, periodo_fine, isin, lei, nome))
        return dict(deepcopy(self.result), isin=isin, lei=lei)


EU_LIST = "https://www.example-oam.test/api/liste?emittente=zztest"


def eu_deposit(**changes):
    value = {"ticker": "ZZTEST.PA", "isin": None, "lei": None, "nome": "Synthetic issuer", "tipo": "semestrale",
             "periodo_fine": "2026-06-30", "stato": "ok", "errore": None, "motivo": None,
             "data_deposito": "2026-07-24", "ora_deposito": "13:05", "fuso": "UTC", "natura_data": "diffusione",
             "titolo": "Rapport financier semestriel 2026", "url": "https://www.example-oam.test/doc/zz.pdf",
             "url_documento": "https://www.example-oam.test/doc/zz.pdf", "sha256_documento": None,
             "categoria": "RFS", "lingua": "fr", "candidati": [], "conferme": [], "prova": "titolo", "scartati": [],
             "fonte": "AMF info-financiere (OAM Francia)", "paese": "FR", "url_liste": [EU_LIST],
             "sha256_liste": {EU_LIST: "c" * 64}, "risposte_salvate": {EU_LIST: "{\"righe\": []}"},
             "fonte_modulo": "bellomberg.market_data.ue_amf", "pagine_lette": 1,
             "letto_il": "2026-09-28T08:00:00+00:00", "limiti": ["limite sintetico"], "cache": "fresca",
             "instradamento": {"paese": "FR", "regola": "paese del listino", "suffisso": ".PA",
                               "listino": "Euronext Paris", "modulo": "bellomberg.market_data.ue_amf",
                               "canale": "OAM", "perche": "sintetico"}}
    value.update(changes)
    return value


EU_IDENTITY = {**IDENTITY, "ticker": "ZZTEST.PA"}


@pytest.fixture
def eu_recheck(monkeypatch):
    from bellomberg.market_data import depositi_ue
    calls = []

    def fake(ricevuta, *, ticker, tipo, periodo_fine, dopo_sdir_italiano=False):
        calls.append((ricevuta["fonte_modulo"], ticker, tipo, periodo_fine, "contract" in ricevuta or "scelta_copertina" in ricevuta))
        return True, "riverifica sintetica"
    monkeypatch.setattr(depositi_ue, "riverifica_ricevuta", fake)
    return calls


def test_eu_deposit_is_declared_sealed_and_reverified_offline(tmp_path, eu_recheck):
    lookup = EULookup(eu_deposit())
    result = ingest(tmp_path, lookup=lookup, identity=EU_IDENTITY)
    assert lookup.calls == [("ZZTEST.PA", "semestrale", "2026-06-30", None, None, "Synthetic issuer")]
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["filed_date"] == "2026-07-24"
    assert verification["publication_basis"] == "eu_official_deposit_receipt"
    assert verification["claim_origins"]["publication"] == "eu_official_deposit_receipt"
    assert verification["publication_declaration"] == (
        "data di diffusione AMF info-financiere (OAM Francia) del 24/07/2026 alle 15:05 ora di Europe/Paris, "
        "documento «Rapport financier semestriel 2026»; "
        "identità dell'emittente non confermata su GLEIF (non_trovato): ISIN e LEI non usati")
    sealed = verification["deposit_receipt"]
    archived = json.loads((tmp_path / "pm-public-documents" / "publication-receipts" / (sealed["sha256"] + ".json"))
                          .read_bytes())
    assert archived["risposte_salvate"] == {EU_LIST: "{\"righe\": []}"} and archived["contract"] == sealed["contract"]
    assert eu_recheck and all(call[:4] == ("bellomberg.market_data.ue_amf", "ZZTEST.PA", "semestrale", "2026-06-30")
                              and call[4] is False for call in eu_recheck)
    checks = len(eu_recheck)
    sources.verify_document_receipt(result["receipt"], "ZZTEST.PA", EU_IDENTITY, AS_OF, archive_root=tmp_path,
                                    issuer_website=WEBSITE)
    assert len(lookup.calls) == 1 and len(eu_recheck) > checks
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    collect = lambda c: collect_documents("ZZTEST.PA", as_of=AS_OF, archive_root=tmp_path,
        filing_results=[{"ticker": "ZZTEST.PA", "candidati": [c], "motivi": []}],
        catalog=lambda *_: {"stato": "ok", "documenti": [], "motivi": []})
    assert collect(deepcopy(candidate))["issues"] == []
    for tamper in ("basis", "natura", "declaration"):
        changed = deepcopy(candidate)
        target = changed["metadati"]["pm_source_verification"]
        if tamper == "basis":
            target["publication_basis"] = "emarket_sdir_deposit_receipt"
        elif tamper == "natura":
            target["deposit_receipt"]["natura_data"] = "deposito_autorita"
        else:
            target["publication_declaration"] = target["publication_declaration"].replace("diffusione", "deposito")
        assert collect(changed)["documents"] == [], tamper


@pytest.mark.parametrize("changes, words", [
    ({"natura_data": "deposito_autorita", "fonte": "AFM registro (deposito presso l'autorita')",
      "fonte_modulo": "bellomberg.market_data.ue_afm", "paese": "NL", "fuso": "Europe/Amsterdam"},
     "data di deposito AFM registro (deposito presso l'autorita'), non di diffusione, del 24/07/2026"),
    ({"natura_data": "pubblicazione_dichiarata", "fonte": "FSMA STORI (OAM Belgio)",
      "fonte_modulo": "bellomberg.market_data.ue_fsma", "paese": "BE", "fuso": "Europe/Brussels"},
     "data di pubblicazione dichiarata da FSMA STORI (OAM Belgio) del 24/07/2026"),
    ({"protocollo": "ZZ-77", "instradamento": {"canale": "borsa"}}, "(protocollo ZZ-77); canale di borsa, non l'OAM"),
])
def test_eu_declaration_names_the_nature_of_the_date(tmp_path, eu_recheck, changes, words):
    result = ingest(tmp_path, lookup=EULookup(eu_deposit(**changes)), identity=EU_IDENTITY)
    assert words in result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["publication_declaration"]


@pytest.mark.parametrize("changes, words", [
    ({"stato": "KO", "errore": "identita_mancante", "motivo": "serve ISIN o LEI", "data_deposito": None},
     "EU official deposit KO (identita_mancante): serve ISIN o LEI"),
    ({"stato": "STALE"}, "EU official deposit STALE"),
    ({"natura_data": "altro"}, "EU official deposit receipt conflicts"),
    ({"periodo_fine": "2026-03-31"}, "EU official deposit receipt conflicts"),
    ({"ticker": "OTHER.PA"}, "EU official deposit receipt conflicts"),
    ({"data_deposito": "2026-06-30"}, "not after the reporting period"),
    ({"data_deposito": "2026-10-01"}, "after the cutoff"),
])
def test_inconsistent_eu_deposit_is_rejected(tmp_path, eu_recheck, changes, words):
    reason = rejection(tmp_path, lookup=EULookup(eu_deposit(**changes)), identity=EU_IDENTITY)
    assert words in reason and reason.endswith("da fornire dal PM")


def test_eu_receipt_the_country_module_does_not_confirm_is_rejected(tmp_path, monkeypatch):
    from bellomberg.market_data import depositi_ue
    monkeypatch.setattr(depositi_ue, "riverifica_ricevuta", lambda *a, **k: (False, "corpo diverso dallo sha"))
    reason = rejection(tmp_path, lookup=EULookup(eu_deposit()), identity=EU_IDENTITY)
    assert "failed the offline re-verification (corpo diverso dallo sha)" in reason


def test_eu_receipt_from_an_unlisted_module_fails_the_real_recheck(tmp_path):
    # riverifica VERA di depositi_ue (nessuno stub): un modulo fuori elenco non conferma niente.
    reason = rejection(tmp_path, lookup=EULookup(eu_deposit(fonte_modulo="bellomberg.market_data.falso")),
                       identity=EU_IDENTITY)
    assert "failed the offline re-verification" in reason and "non in elenco" in reason


def test_eu_lookup_gets_the_identity_isin_and_the_confirmed_name(tmp_path, eu_recheck):
    isin = valid_isin("FR")
    lookup = EULookup(eu_deposit(isin=isin))
    ingest(tmp_path, lookup=lookup, identity={**EU_IDENTITY, "isin": isin})
    assert lookup.calls == [("ZZTEST.PA", "semestrale", "2026-06-30", isin, SYNTH_LEI, "Synthetic issuer")]


def test_eu_receipt_larger_than_the_archive_limit_is_rejected(tmp_path, eu_recheck, monkeypatch):
    monkeypatch.setattr(sources, "MAX_BYTES", 2000)
    big = eu_deposit(risposte_salvate={EU_LIST: "x" * 5000})
    with pytest.raises(ValueError, match="larger than the archive limit"):
        sources._acquire_eu_deposit("ZZTEST.PA", "semestrale", "2026-06-30", AS_OF, tmp_path, EULookup(big),
                                    issuer_name="Synthetic issuer")


# Memoria dei tentativi ISIN (W1): nel percorso di produzione, prima di risolvi_isin.
def production_stubs(monkeypatch, ensured):
    from bellomberg.market_data import borsa_italiana, isin_automatico, sdir
    calls = {"assicura": [], "risolvi": [], "sdir": []}

    def assicura(ticker, *, db_path=None, nome=None, fonte_nome=None):
        calls["assicura"].append((ticker, nome, fonte_nome))
        return deepcopy(ensured)

    def risolvi(ticker, *, nome=None):
        calls["risolvi"].append((ticker, nome))
        return {"stato": "ok", "isin": "IT0000000000", "emarket": 9999, "negozio": "automatico",
                "fonte_url": None, "letto_il": None}

    def router(ticker, *, tipo, periodo_fine, nome=None, fine_esercizio=None):
        calls["sdir"].append((ticker, nome))
        return deepcopy(deposit())
    monkeypatch.setattr(isin_automatico, "assicura_isin_it", assicura)
    monkeypatch.setattr(borsa_italiana, "risolvi_isin", risolvi)
    monkeypatch.setattr(sdir, "get_data_deposito", router)
    return calls


def test_production_path_uses_the_failed_attempt_memory_first(tmp_path, monkeypatch):
    calls = production_stubs(monkeypatch, {"stato": "non_trovato", "errore": "nome_assente",
                                           "motivo": "risoluzione gia' tentata il 2026-10-05, esito non_trovato"})
    with pytest.raises(ValueError) as blocked:
        sources._acquire_deposit_companion("ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, tmp_path,
                                           issuer_name="Synthetic issuer")
    assert calls["assicura"] == [("ZZTEST.MI", "Synthetic issuer", "identità confermata (identity resolver)")]
    assert calls["risolvi"] == [] and calls["sdir"] == []
    assert "ISIN resolution non_trovato (nome_assente): risoluzione gia' tentata" in str(blocked.value)


@pytest.mark.parametrize("ensured", [None, {"stato": "ok", "isin": "IT0000000000"}])
def test_production_path_continues_after_the_memory_says_ok(tmp_path, monkeypatch, ensured):
    calls = production_stubs(monkeypatch, ensured)
    sealed = sources._acquire_deposit_companion("ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, tmp_path,
                                                issuer_name="Synthetic issuer")
    assert calls["risolvi"] == [("ZZTEST.MI", "Synthetic issuer")] and calls["sdir"] == [("ZZTEST.MI", "Synthetic issuer")]
    assert sealed["isin_resolution"]["nome_cercato"] == "Synthetic issuer"


@pytest.mark.parametrize("served", ["1INFO-SDIR", "eMarket SDIR"])
def test_routing_on_both_sdirs_accepts_the_date_served_by_one(tmp_path, served):
    both = {"regola": "misura_automatica", "scelta": "entrambi", "emarket_id": 9999, "oneinfo_ndg": 99901}
    value = (oneinfo_deposit(instradamento=both) if served == "1INFO-SDIR"
             else deposit(sdir="eMarket SDIR", instradamento=both))
    row = ingest(tmp_path, lookup=Lookup(value))["receipt"]["documents"][0]
    assert row["metadati"]["pm_source_verification"]["deposit_receipt"]["sdir"] == served


# ---------------------------------------------------------------------------------------------
# AGGIUNTA 3 (main 05/10, rilievo RV-UE1 AMF-5): ISTANTI, non date. Cutoff = fine del giorno as_of (2026-09-28)
# a Roma; ora mancante = fine del giorno locale della fonte. Bordi di mezzanotte per ogni ramo.
@pytest.mark.parametrize("changes", [
    {"data_deposito": "2026-09-28", "ora_deposito": "23:59"},
    {"data_deposito": "2026-09-28", "ora_deposito": None},
    {"data_deposito": "2026-09-28", "ora_deposito": "23:59", "sdir": "eMarket SDIR", "fuso": "UTC"},
])
def test_emarket_deposit_late_on_the_cutoff_day_in_rome_is_public(tmp_path, changes):
    row = ingest(tmp_path, lookup=Lookup(deposit(**changes)))["receipt"]["documents"][0]
    assert row["filed_date"] == "2026-09-28"


def test_emarket_deposit_after_midnight_in_rome_is_not_public(tmp_path):
    reason = rejection(tmp_path, lookup=Lookup(deposit(data_deposito="2026-09-29", ora_deposito="00:01")))
    assert "after the cutoff" in reason


def test_oneinfo_hour_marked_utc_is_read_as_rome(tmp_path):
    # 23:30 «UTC» di 1INFO e' gia' ora di Roma (misura S1): come UTC vero sarebbe il 29/09 alle 01:30 a Roma.
    row = ingest(tmp_path, lookup=Lookup(oneinfo_deposit(data_deposito="2026-09-28", ora_deposito="23:30",
                                                          fuso="UTC")))["receipt"]["documents"][0]
    assert row["filed_date"] == "2026-09-28"


@pytest.mark.parametrize("day, clock, accepted, filed, words", [
    ("2026-09-28", "21:30", True, "2026-09-28", "del 28/09/2026 alle 23:30 ora di Europe/Paris"),
    ("2026-09-28", "22:30", False, None, "after the cutoff"),       # 00:30 del 29/09 a Roma e a Parigi
    ("2026-09-27", "22:30", True, "2026-09-28", "del 28/09/2026 alle 00:30 ora di Europe/Paris"),
])
def test_eu_deposit_instant_in_utc_against_the_rome_cutoff(tmp_path, eu_recheck, day, clock, accepted, filed, words):
    lookup = EULookup(eu_deposit(data_deposito=day, ora_deposito=clock, fuso="UTC"))
    if accepted:
        row = ingest(tmp_path, lookup=lookup, identity=EU_IDENTITY)["receipt"]["documents"][0]
        assert row["filed_date"] == filed
        assert words in row["metadati"]["pm_source_verification"]["publication_declaration"]
    else:
        assert words in rejection(tmp_path, lookup=lookup, identity=EU_IDENTITY)


AFM_FIELDS = {"fonte_modulo": "bellomberg.market_data.ue_afm", "paese": "NL", "natura_data": "deposito_autorita",
              "fonte": "AFM registro (deposito presso l'autorita')"}


@pytest.mark.parametrize("day, zone, fields, filed, words", [
    # fine del giorno ad Amsterdam = 23:59:59 a Roma: pubblico entro il cutoff
    ("2026-09-28", "Europe/Amsterdam", AFM_FIELDS, "2026-09-28", "del 28/09/2026 (ora non indicata dalla fonte: fine del giorno)"),
    # fine del giorno UTC = 01:59:59 del giorno dopo a Roma: dopo il cutoff
    ("2026-09-28", "UTC", {}, None, "after the cutoff"),
    # il giorno a Roma e' dichiarato quando differisce (RV-D4 P3-3)
    ("2026-09-27", "UTC", {}, "2026-09-28",
     "del 27/09/2026 (ora non indicata dalla fonte: fine del giorno), cioè il 28/09/2026 a Roma"),
    # fuso diverso da quello del modulo (RV-D4 P2-a): nessun fuso, o un fuso riscritto
    ("2026-09-20", None, {}, None, "time zone differs from the one its source module declares"),
    ("2026-09-20", "Asia/Tokyo", {}, None, "time zone differs from the one its source module declares"),
    # natura della data diversa dal registro del paese (RV-D4 P3)
    ("2026-09-20", "UTC", {"natura_data": "deposito_autorita"}, None, "date nature differs from the country registry"),
])
def test_eu_deposit_without_hour_is_the_end_of_the_local_day(tmp_path, eu_recheck, day, zone, fields, filed, words):
    lookup = EULookup(eu_deposit(data_deposito=day, ora_deposito=None, fuso=zone, **fields))
    if filed:
        row = ingest(tmp_path, lookup=lookup, identity=EU_IDENTITY)["receipt"]["documents"][0]
        assert row["filed_date"] == filed
        assert words in row["metadati"]["pm_source_verification"]["publication_declaration"]
    else:
        assert words in rejection(tmp_path, lookup=lookup, identity=EU_IDENTITY)


def test_eu_deposit_without_any_time_zone_is_rejected(tmp_path, eu_recheck):
    identity = {**EU_IDENTITY, "ticker": "ZZTEST.MC"}       # .MC: supplemento di depositi_ue, non in mercati.MERCATI
    reason = rejection(tmp_path, lookup=EULookup(eu_deposit(ticker="ZZTEST.MC", paese="ES", fuso=None)),
                       identity=identity)
    assert "time zone is unknown" in reason and reason.endswith("da fornire dal PM")


# ---------------------------------------------------------------------------------------------
# eMarket Storage, sezione DOCUMENTI (IT2/IT3 05/10): prova='documento', data di STOCCAGGIO del documento.
DOC_LIST = ("https://www.emarketstorage.it/it/documenti?azienda=9999&data_from=2026-07-01&data_to=2026-10-01")
DOC_ROW = {"data": "2026-08-06", "ora": "10:49", "titolo": "Relazione finanziaria semestrale al 30 giugno 2026",
           "url": "https://www.emarketstorage.it/sites/default/files/documenti/zztest-777001.pdf",
           "protocollo": "777001", "categoria": 101, "categorie": [101], "esef": False, "lingua": "it",
           "fuso": "Europe/Rome"}


def storage_deposit(row=None, **changes):
    row = deepcopy(row or DOC_ROW)
    value = deposit(prova="documento", prova_documento="categoria+nome", documento=row,
                    tipo_data="stoccaggio_documento", natura="stoccaggio_documento", stato_documenti="ok",
                    data_deposito=row["data"], ora_deposito=row["ora"], titolo=row["titolo"], url=row["url"],
                    protocollo=row["protocollo"], categoria=101, fuso="Europe/Rome",
                    url_liste=[], sha256_liste={}, url_liste_documenti=[DOC_LIST],
                    sha256_liste_documenti={DOC_LIST: "d" * 64},
                    comunicato_conferma={"stato": "ok", "data": "2026-08-06", "ora": "10:52", "protocollo": "123456",
                                         "titolo": TITLE, "url": "https://www.emarketstorage.it/c.pdf", "prova": "titolo",
                                         "natura": "messa_a_disposizione", "motivo": None, "scarto_giorni": 0},
                    sdir="eMarket SDIR", instradamento={"regola": "negozio_confermato", "scelta": "emarket",
                                                        "emarket_id": 9999, "oneinfo_ndg": None})
    value.update(changes)
    return value


def test_storage_document_date_is_declared_sealed_and_reverified(tmp_path):
    lookup = Lookup(storage_deposit())
    result = ingest(tmp_path, lookup=lookup)
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["filed_date"] == "2026-08-06"
    assert verification["publication_declaration"] == (
        "data di stoccaggio eMarket Storage (sezione Documenti) del 06/08/2026, documento «"
        + DOC_ROW["titolo"] + "» (protocollo 777001)")
    sealed = verification["deposit_receipt"]
    assert sealed["prova"] == "documento" and sealed["documento"] == DOC_ROW
    assert sealed["sha256_liste_documenti"] == {DOC_LIST: "d" * 64} and sealed["comunicato_conferma"]["protocollo"] == "123456"
    sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    assert len(lookup.calls) == 1
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []


@pytest.mark.parametrize("changes, words", [
    ({"prova_documento": "nome+periodo"}, "eMarket Storage document does not name this report"),
    ({"titolo": "Relazione finanziaria semestrale al 30 giugno 2026 (versione)"}, "receipt is incomplete or differs"),
    ({"url_liste_documenti": [], "sha256_liste_documenti": {}}, "receipt is incomplete or differs"),
    ({"tipo_data": "diffusione_comunicato"}, "receipt is incomplete or differs"),
    ({"documento": None}, "receipt is incomplete or differs"),
])
def test_inconsistent_storage_document_is_rejected(tmp_path, changes, words):
    reason = rejection(tmp_path, lookup=Lookup(storage_deposit(**changes)))
    assert words in reason and reason.endswith("da fornire dal PM")


def test_storage_row_that_is_not_the_report_is_rejected(tmp_path):
    other = dict(DOC_ROW, titolo="Relazione illustrativa del Consiglio di Amministrazione")
    reason = rejection(tmp_path, lookup=Lookup(storage_deposit(row=other)))
    assert "eMarket Storage document does not name this report" in reason


def test_storage_document_without_communique_lists_needs_the_document_lists(tmp_path):
    # liste dei comunicati vuote: ammesse SOLO col documento; un comunicato senza liste resta rifiutato
    reason = rejection(tmp_path, lookup=Lookup(deposit(url_liste=[], sha256_liste={})))
    assert "issuer, type or period" in reason


# ---------------------------------------------------------------------------------------------
# Ambiguo UE (regola di main 05/10): conta il documento che la Trade Idea usa, riconosciuto dal NOME in copertina.
DEU_COVER = "Synthetic issuer\nDocument d'enregistrement universel 2025\nYear ended 31 December 2025\nNarrative.\n"
AMBIGUOUS = [
    {"titolo": "Comptes consolidés 2025", "data": "2026-02-12", "ora": "18:00",
     "url": "https://www.example-oam.test/doc/cc.pdf", "categoria": "RFA", "lingua": "fr", "prova": "titolo"},
    {"titolo": "Document d'enregistrement universel 2025", "data": "2026-03-31", "ora": "16:45",
     "url": "https://www.example-oam.test/doc/deu.pdf", "categoria": "RFA", "lingua": "fr", "prova": "titolo"},
]


def ambiguous_eu(**changes):
    # forma vera dei moduli (RV-D4 P1): ritorno ambiguo SENZA fuso, candidati con ora UTC dichiarata
    fields = dict(stato="ambiguo", tipo="annuale", periodo_fine="2025-12-31", data_deposito=None,
                  ora_deposito=None, titolo=None, url=None, fuso=None,
                  candidati=[dict(row, fuso="UTC") for row in deepcopy(AMBIGUOUS)])
    fields.update(changes)
    return eu_deposit(**fields)


def test_local_names_decide_the_type_of_a_french_cover():
    from bellomberg.market_data.depositi_ue import nomi_documento
    assert nomi_documento("FR", "annuale")[0]
    assert sources._deposit_document_type(DEU_COVER, "2025-12-31") is None
    assert sources._deposit_document_type(DEU_COVER, "2025-12-31", sources._eu_document_names("FR")) == "annuale"


def test_eu_ambiguity_is_resolved_by_the_cover_document_name(tmp_path, eu_recheck):
    lookup = EULookup(ambiguous_eu())
    result = ingest(tmp_path, text=DEU_COVER, lookup=lookup, identity=EU_IDENTITY)
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert lookup.calls == [("ZZTEST.PA", "annuale", "2025-12-31", None, None, "Synthetic issuer")]
    assert row["filed_date"] == "2026-03-31"
    declaration = verification["publication_declaration"]
    assert "del 31/03/2026 alle 18:45 ora di Europe/Paris" in declaration
    assert "documento «Document d'enregistrement universel 2025»" in declaration
    assert "fonte ambigua fra 2 documenti: scelto quello col nome della copertina" in declaration
    choice = verification["deposit_receipt"]["scelta_copertina"]
    assert choice["regola"] == "cover_document_name/1" and choice["candidato"] == dict(AMBIGUOUS[1], fuso="UTC")
    assert verification["proofs"]["publication"]["locator"] == "/scelta_copertina/candidato/data"
    # la riverifica del modulo riceve la ricevuta del modulo, senza la scelta ne' il contratto
    assert eu_recheck and all(call[4] is False for call in eu_recheck)
    sources.verify_document_receipt(result["receipt"], "ZZTEST.PA", EU_IDENTITY, AS_OF, archive_root=tmp_path,
                                    issuer_website=WEBSITE)
    assert len(lookup.calls) == 1
    sealed = verification["deposit_receipt"]
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    with pytest.raises(ValueError, match="differs from the document cover"):
        sources._check_eu_deposit_value(value, "ZZTEST.PA", "annuale", "2025-12-31", AS_OF,
                                        DEU_COVER.replace("Document d'enregistrement universel", "Comptes consolidés"))
    with pytest.raises(ValueError, match="without the document text"):
        sources._check_eu_deposit_value(value, "ZZTEST.PA", "annuale", "2025-12-31", AS_OF)


@pytest.mark.parametrize("text, changes, words", [
    ("Synthetic issuer\nDocument d'enregistrement universel 2025 - Comptes consolidés 2025\n"
     "Year ended 31 December 2025\n", {}, "no single candidate carries the document name read on the cover"),
    (DEU_COVER, {"errore": "nome_non_univoco", "omonimi": ["ZZ UNO", "ZZ DUE"]},
     "EU official deposit ambiguo (nome_non_univoco)"),
    (DEU_COVER, {"candidati": [dict(AMBIGUOUS[1]), dict(AMBIGUOUS[1], data="2026-04-01", url="https://x.test/2.pdf")]},
     "no single candidate carries the document name read on the cover"),
])
def test_eu_ambiguity_without_a_single_cover_named_candidate_is_rejected(tmp_path, eu_recheck, text, changes, words):
    reason = rejection(tmp_path, text=text, lookup=EULookup(ambiguous_eu(**changes)), identity=EU_IDENTITY)
    assert words in reason and reason.endswith("da fornire dal PM")


def test_cover_name_choice_on_an_unambiguous_receipt_is_rejected(tmp_path):
    value = {"contract": sources._EU_DEPOSIT_CONTRACT, **eu_deposit(),
             "scelta_copertina": {"regola": "cover_document_name/1", "nomi_copertina": [], "candidato": AMBIGUOUS[1]}}
    with pytest.raises(ValueError, match="without ambiguity"):
        sources._check_eu_deposit_value(value, "ZZTEST.PA", "semestrale", "2026-06-30", AS_OF, HALF_YEAR)


# Esercizio non solare (ON/IT2 05/10): fine_esercizio dedotta dal tipo, passata al router solo se serve.
@pytest.mark.parametrize("tipo, period, expected", [
    ("annuale", "2025-12-31", None), ("annuale", "2025-06-30", "06-30"), ("semestrale", "2026-06-30", None),
    ("semestrale", "2025-12-31", "06-30"), ("semestrale", "2026-03-31", "09-30"), ("semestrale", "2026-09-30", "03-31"),
    ("trimestrale", "2026-03-31", None), ("trimestrale", "2026-01-31", None),
])
def test_fiscal_year_end_is_deduced_only_when_the_period_is_not_calendar(tipo, period, expected):
    assert sources._fiscal_year_end(tipo, period) == expected


def test_production_router_gets_the_fiscal_year_end_for_a_non_calendar_period(tmp_path, monkeypatch):
    from bellomberg.market_data import borsa_italiana, isin_automatico, sdir
    seen = []
    monkeypatch.setattr(isin_automatico, "assicura_isin_it", lambda *a, **k: None)
    monkeypatch.setattr(borsa_italiana, "risolvi_isin", lambda ticker, *, nome=None: {
        "stato": "ok", "isin": "IT0000000000", "emarket": 9999, "negozio": "confermato", "fonte_url": None,
        "letto_il": None})

    def router(ticker, *, tipo, periodo_fine, nome=None, fine_esercizio=None):
        seen.append((tipo, periodo_fine, fine_esercizio))
        return {"stato": "KO", "errore": "parametro", "motivo": "sintetico", "sdir": None}
    monkeypatch.setattr(sdir, "get_data_deposito", router)
    with pytest.raises(ValueError, match="SDIR deposit KO"):
        sources._acquire_deposit_companion("ZZTEST.MI", "annuale", "2025-06-30", AS_OF, tmp_path,
                                           issuer_name="Synthetic issuer")
    assert seen == [("annuale", "2025-06-30", "06-30")]


def test_storage_document_of_a_non_calendar_half_year_is_reverified_with_the_fiscal_year_end(tmp_path):
    row = dict(DOC_ROW, data="2026-05-20", titolo="Relazione finanziaria semestrale al 31 marzo 2026",
               url="https://www.emarketstorage.it/sites/default/files/documenti/zztest-777003.pdf", protocollo="777003")
    text = "Synthetic issuer\nRELAZIONE FINANZIARIA SEMESTRALE AL 31 MARZO 2026\nSynthetic narrative.\n"
    lookup = Lookup(storage_deposit(row=row, periodo_fine="2026-03-31"))
    result = ingest(tmp_path, text=text, lookup=lookup)
    assert lookup.calls == [("ZZTEST.MI", "semestrale", "2026-03-31")]
    assert result["receipt"]["documents"][0]["filed_date"] == "2026-05-20"


# Trimestrale non solare (main 05/10): fine esercizio SOLO da fonte dichiarata: (a) relazione annuale ammessa,
# (b) copertina, (c) fornitore prezzi passato dal chiamante; nessuna fonte = limite dichiarato.
Q_TEXT = "Synthetic issuer\nRESOCONTO INTERMEDIO DI GESTIONE AL 31 LUGLIO 2026\nSynthetic narrative.\n"
Q_TITLE = "Synthetic issuer: pubblicato il Resoconto intermedio di gestione al 31 luglio 2026"
PROVIDER = {"valore": "04-30", "fonte": "fornitore_prezzi", "dettaglio": "lastFiscalYearEnd del fornitore prezzi (PROXY)"}


def quarterly_deposit(**changes):
    return deposit(tipo="trimestrale", periodo_fine="2026-07-31", titolo=Q_TITLE, data_deposito="2026-09-10", **changes)


@pytest.mark.parametrize("text, declared, expected", [
    (Q_TEXT + "Esercizio che chiude al 30 aprile 2027\n", (), {"valore": "04-30", "fonte": "copertina",
                                                              "dettaglio": "esercizio citato in copertina"}),
    (Q_TEXT, (PROVIDER,), PROVIDER),
    (Q_TEXT + "Financial year ending 31 October 2026\n", (PROVIDER,),       # la copertina viene prima del fornitore
     {"valore": "10-31", "fonte": "copertina", "dettaglio": "esercizio citato in copertina"}),
    (Q_TEXT + "Esercizio che chiude al 30 aprile 2027\n",
     ({"valore": "10-31", "fonte": "relazione_annuale", "dettaglio": "annuale sintetica"},),   # l'annuale viene prima
     {"valore": "10-31", "fonte": "relazione_annuale", "dettaglio": "annuale sintetica"}),
    (Q_TEXT, (), None),                                                     # nessuna fonte
    (Q_TEXT, ({"valore": "06-30", "fonte": "fornitore_prezzi", "dettaglio": "x"},), None),   # incoerente col trimestre
    (Q_TEXT, ({"valore": "04-30", "fonte": "indovinata", "dettaglio": "x"},), None),         # fonte non ammessa
])
def test_quarterly_fiscal_year_end_comes_only_from_a_declared_source(text, declared, expected):
    assert sources._fiscal_year_end_declared("trimestrale", "2026-07-31", text, declared) == expected


def test_quarterly_fiscal_year_end_from_the_cover_is_sealed_declared_and_reverified(tmp_path):
    text = Q_TEXT + "Esercizio che chiude al 30 aprile 2027\n"
    result = ingest(tmp_path, text=text, lookup=Lookup(quarterly_deposit()))
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    sealed = verification["deposit_receipt"]
    assert sealed["fine_esercizio"] == {"valore": "04-30", "fonte": "copertina", "dettaglio": "esercizio citato in copertina"}
    assert verification["publication_declaration"].endswith("; esercizio non solare che chiude il 30/04 (fonte: esercizio citato in copertina)")
    sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    for broken in ({"valore": "06-30", "fonte": "copertina", "dettaglio": None},
                   {"valore": "04-30", "fonte": "dedotta dal tipo e dal periodo", "dettaglio": None},
                   {"valore": "04-30", "fonte": "inventata", "dettaglio": None}, "04-30"):
        with pytest.raises(ValueError, match="fiscal year end is malformed or incoherent"):
            sources._check_deposit_value({**value, "fine_esercizio": broken}, "ZZTEST.MI", "trimestrale",
                                         "2026-07-31", AS_OF, tmp_path / "pm-public-documents")


def test_quarterly_fiscal_year_end_from_the_provider_passed_by_the_caller(tmp_path):
    download, _ = transport(Q_TEXT)
    result = sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download, deposit_lookup=Lookup(quarterly_deposit()),
        fiscal_year_end_sources=[PROVIDER, {"valore": "04-30", "fonte": "inventata", "dettaglio": "x"}])
    sealed = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["deposit_receipt"]
    assert sealed["fine_esercizio"] == PROVIDER


def test_quarterly_without_fiscal_source_seals_nothing(tmp_path):
    sealed = ingest(tmp_path, text=Q_TEXT, lookup=Lookup(quarterly_deposit()))["receipt"]["documents"][0][
        "metadati"]["pm_source_verification"]["deposit_receipt"]
    assert "fine_esercizio" not in sealed


def test_annual_admitted_earlier_in_the_request_gives_the_fiscal_year_end(tmp_path):
    annual = ("Synthetic issuer\nPublished on 2026-07-20\nRELAZIONE FINANZIARIA ANNUALE AL 30 APRILE 2026\n"
              "Year ended 30 April 2026\n")
    url_q = "https://issuer.example.org/investors/quarterly.html"
    downloads = {URL: transport(annual)[0], url_q: transport(Q_TEXT)[0]}
    result = sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}, {"url": url_q}],
        archive_root=tmp_path, issuer_website=WEBSITE, deposit_lookup=Lookup(quarterly_deposit()),
        download=lambda url, root, **kwargs: downloads[url](url, root, **kwargs))
    rows = result["receipt"]["documents"]
    fiscal = rows[1]["metadati"]["pm_source_verification"]["deposit_receipt"]["fine_esercizio"]
    assert fiscal["valore"] == "04-30" and fiscal["fonte"] == "relazione_annuale"
    assert "relazione annuale al 2026-04-30 ammessa in questa richiesta" in fiscal["dettaglio"]


def test_production_router_gets_the_declared_quarterly_fiscal_year_end(tmp_path, monkeypatch):
    from bellomberg.market_data import borsa_italiana, isin_automatico, sdir
    seen = []
    monkeypatch.setattr(isin_automatico, "assicura_isin_it", lambda *a, **k: None)
    monkeypatch.setattr(borsa_italiana, "risolvi_isin", lambda ticker, *, nome=None: {
        "stato": "ok", "isin": "IT0000000000", "emarket": 9999, "negozio": "confermato", "fonte_url": None,
        "letto_il": None})

    def router(ticker, *, tipo, periodo_fine, nome=None, fine_esercizio=None):
        seen.append(fine_esercizio)
        return {"stato": "KO", "errore": "parametro", "motivo": "sintetico", "sdir": None}
    monkeypatch.setattr(sdir, "get_data_deposito", router)
    for declared in ((PROVIDER,), ()):
        with pytest.raises(ValueError):
            sources._acquire_deposit_companion("ZZTEST.MI", "trimestrale", "2026-07-31", AS_OF, tmp_path,
                                               issuer_name="Synthetic issuer", text=Q_TEXT, fiscal_sources=declared)
    assert seen == ["04-30", None]


# Ripiego .MI -> Stato d'origine (main 05/10): SDIR italiani non_coperto / KO instradamento_nome_non_trovato e ISIN
# di un altro paese -> depositi_ue con dopo_sdir_italiano=True; entrambi gli esiti sigillati e riverificati.
class OriginLookup:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None, dopo_sdir_italiano=False,
                 motivo_sdir=None):
        self.calls.append((ticker, tipo, periodo_fine, isin, nome, dopo_sdir_italiano, motivo_sdir))
        value = dict(deepcopy(self.result), isin=isin, lei=lei)
        value["instradamento"] = dict(value.get("instradamento") or {}, motivo_sdir=motivo_sdir,
                                      dopo_sdir_italiano=dopo_sdir_italiano)
        return value


def origin_deposit(isin, **changes):
    fields = dict(ticker="ZZTEST.MI", paese="NL", isin=isin, isin_instradamento=isin, dopo_sdir_italiano=True,
                  natura_data="deposito_autorita", fonte="AFM registro (deposito presso l'autorita')",
                  fonte_modulo="bellomberg.market_data.ue_afm", fuso="Europe/Amsterdam", ora_deposito=None,
                  titolo="Halfjaarbericht 2026", data_deposito="2026-08-06",
                  instradamento={"paese": "NL", "regola": "paese dall'ISIN dopo gli SDIR italiani", "suffisso": ".MI",
                                 "modulo": "bellomberg.market_data.ue_afm", "canale": "OAM"})
    fields.update(changes)
    return eu_deposit(**fields)


@pytest.fixture
def origin_recheck(monkeypatch):
    from bellomberg.market_data import depositi_ue
    calls = []

    def fake(ricevuta, *, ticker, tipo, periodo_fine, dopo_sdir_italiano=False):
        calls.append((ticker, dopo_sdir_italiano, "esito_sdir" in ricevuta or "contract" in ricevuta))
        return True, "riverifica sintetica"
    monkeypatch.setattr(depositi_ue, "riverifica_ricevuta", fake)
    return calls


def origin_ingest(tmp_path, sdir_result, eu_lookup, isin):
    download, _ = transport(HALF_YEAR)
    identity = {**IDENTITY, "isin": isin}
    return sources.ingest_document_sources(identity["ticker"], identity, AS_OF, [{"url": URL}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download, deposit_lookup=Lookup(sdir_result),
        deposit_eu_lookup=eu_lookup)


@pytest.mark.parametrize("stato, errore", [("non_coperto", "nessuno_sdir"), ("KO", "instradamento_nome_non_trovato")])
def test_italian_listing_not_on_the_sdirs_takes_the_origin_state_date(tmp_path, origin_recheck, stato, errore):
    isin = valid_isin("NL")
    sdir_result = deposit(stato=stato, errore=errore, motivo="motivo sintetico", data_deposito=None, sdir=None)
    eu = OriginLookup(origin_deposit(isin))
    result = origin_ingest(tmp_path, sdir_result, eu, isin)
    reason = "sdir: %s %s (motivo sintetico)" % (stato, errore)
    assert eu.calls == [("ZZTEST.MI", "semestrale", "2026-06-30", isin, "Synthetic issuer", True, reason)]
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["filed_date"] == "2026-08-06" and verification["publication_basis"] == "eu_official_deposit_receipt"
    assert verification["publication_declaration"].startswith(
        "non trovato sugli SDIR italiani; data dallo Stato d'origine (NL, ISIN %s): data di deposito AFM" % isin)
    assert "non di diffusione" in verification["publication_declaration"]
    sealed = verification["deposit_receipt"]
    assert sealed["esito_sdir"] == {"stato": stato, "errore": errore, "motivo": "motivo sintetico", "sdir": None}
    assert origin_recheck and all(call == ("ZZTEST.MI", True, False) for call in origin_recheck)
    sources.verify_document_receipt(result["receipt"], "ZZTEST.MI", {**IDENTITY, "isin": isin}, AS_OF,
                                    archive_root=tmp_path, issuer_website=WEBSITE)
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    for tampered in ({**value, "esito_sdir": dict(value["esito_sdir"], stato="non_trovato", errore=None)},
                     {**value, "esito_sdir": dict(value["esito_sdir"], motivo="altro")},
                     {**value, "dopo_sdir_italiano": False}):
        with pytest.raises(ValueError, match="Origin-state fallback after the Italian SDIRs is incoherent"):
            sources._check_eu_deposit_value(tampered, "ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, HALF_YEAR)


@pytest.mark.parametrize("stato, errore, prefix", [
    ("non_trovato", None, "NL"),               # SDIR raggiunto ma documento non trovato: nessun ripiego
    ("KO", "rete", "NL"),                      # guasto della fonte: nessun ripiego
    ("non_coperto", "nessuno_sdir", "IT"),     # ISIN italiano: nessun ripiego
    ("non_coperto", "nessuno_sdir", None),     # nessun ISIN: nessun ripiego
])
def test_origin_state_fallback_only_when_allowed(tmp_path, origin_recheck, stato, errore, prefix):
    isin = valid_isin(prefix) if prefix else None
    eu = OriginLookup(origin_deposit(isin))
    sdir_result = deposit(stato=stato, errore=errore, motivo="motivo sintetico", data_deposito=None, sdir=None)
    with pytest.raises(sources.SourceIngestionError) as blocked:
        origin_ingest(tmp_path, sdir_result, eu, isin)
    assert eu.calls == []
    assert "SDIR deposit %s" % stato in blocked.value.receipt["documents"][0]["reason"]


def test_origin_state_source_failure_declares_both_outcomes(tmp_path, origin_recheck):
    isin = valid_isin("NL")
    eu = OriginLookup(origin_deposit(isin, stato="non_trovato", data_deposito=None, motivo="non trovato in AFM"))
    sdir_result = deposit(stato="non_coperto", errore="nessuno_sdir", motivo="motivo sintetico", data_deposito=None,
                          sdir=None)
    with pytest.raises(sources.SourceIngestionError) as blocked:
        origin_ingest(tmp_path, sdir_result, eu, isin)
    reason = blocked.value.receipt["documents"][0]["reason"]
    assert "not found on the Italian SDIRs (sdir: non_coperto nessuno_sdir (motivo sintetico))" in reason
    assert "EU official deposit non_trovato" in reason and reason.endswith("da fornire dal PM")


# Ambiguo degli SDIR italiani (main 05/10): stessa regola dell'UE, unico candidato col nome della copertina.
IT_COVER = "Synthetic issuer\nRELAZIONE FINANZIARIA SEMESTRALE AL 30 GIUGNO 2026\nSynthetic narrative.\n"
IT_CANDIDATES = [
    {"data": "2026-08-06", "ora": "10:49", "titolo": TITLE, "url_pdf": "https://www.emarketstorage.it/c/zz-1.pdf",
     "protocollo": "123456", "categoria": 101, "lingua": "it", "prova": "titolo"},
    {"data": "2026-08-07", "ora": "09:00",
     "titolo": "Synthetic issuer: depositato il Bilancio consolidato semestrale abbreviato al 30 giugno 2026",
     "url_pdf": "https://www.emarketstorage.it/c/zz-2.pdf", "protocollo": "123457", "categoria": 101, "lingua": "it",
     "prova": "titolo"},
]


def ambiguous_sdir(**changes):
    fields = dict(stato="ambiguo", data_deposito=None, ora_deposito=None, titolo=None, url=None, protocollo=None,
                  candidati=deepcopy(IT_CANDIDATES), sdir="eMarket SDIR",
                  instradamento={"regola": "negozio_confermato", "scelta": "emarket", "emarket_id": 9999,
                                 "oneinfo_ndg": None})
    fields.update(changes)
    return deposit(**fields)


def test_italian_ambiguity_is_resolved_by_the_cover_document_name(tmp_path):
    lookup = Lookup(ambiguous_sdir())
    result = ingest(tmp_path, text=IT_COVER, lookup=lookup)
    row = result["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["filed_date"] == "2026-08-06"
    assert verification["publication_declaration"] == (
        "data di deposito eMarket SDIR del 06/08/2026, documento «" + TITLE + "» (protocollo 123456); "
        "fonte ambigua fra 2 documenti: scelto quello col nome della copertina")
    sealed = verification["deposit_receipt"]
    assert sealed["stato"] == "ambiguo" and sealed["scelta_copertina"]["candidato"] == IT_CANDIDATES[0]
    sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    assert len(lookup.calls) == 1
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    root = tmp_path / "pm-public-documents"
    with pytest.raises(ValueError, match="differs from the document cover"):
        sources._check_deposit_value(value, "ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, root,
            IT_COVER.replace("RELAZIONE FINANZIARIA SEMESTRALE", "BILANCIO CONSOLIDATO SEMESTRALE ABBREVIATO"))
    with pytest.raises(ValueError, match="needs an ambiguous source and the document text"):
        sources._check_deposit_value(value, "ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, root)
    with pytest.raises(ValueError, match="needs an ambiguous source"):
        sources._check_deposit_value({**value, "stato": "ok", "ricevuta_fonte": dict(value["ricevuta_fonte"], stato="ok")},
                                     "ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, root, IT_COVER)


def test_italian_ambiguity_among_storage_documents_uses_the_document_rule(tmp_path):
    other = dict(DOC_ROW, data="2026-08-07", protocollo="777009",
                 titolo="Bilancio consolidato semestrale abbreviato al 30 giugno 2026",
                 url="https://www.emarketstorage.it/sites/default/files/documenti/zztest-777009.pdf")
    rows = [dict(DOC_ROW, prova="categoria+nome"), dict(other, prova="categoria+nome")]
    value = storage_deposit(stato="ambiguo", documento=None, data_deposito=None, ora_deposito=None, titolo=None,
                            url=None, protocollo=None, candidati=rows)
    row = ingest(tmp_path, text=IT_COVER, lookup=Lookup(value))["receipt"]["documents"][0]
    verification = row["metadati"]["pm_source_verification"]
    assert row["filed_date"] == "2026-08-06"
    assert verification["publication_declaration"].startswith(
        "data di stoccaggio eMarket Storage (sezione Documenti) del 06/08/2026")


@pytest.mark.parametrize("text, changes, words", [
    ("Synthetic issuer\nHALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026\n", {},
     "no single candidate carries the document name read on the cover"),
    (IT_COVER, {"candidati": [IT_CANDIDATES[0], dict(IT_CANDIDATES[0], protocollo="123458", data="2026-08-08")]},
     "no single candidate carries the document name read on the cover"),
    (IT_COVER, {"sdir": "eMarket SDIR + 1INFO-SDIR"}, "SDIR deposit ambiguo"),
    (IT_COVER, {"candidati": [dict(IT_CANDIDATES[0], prova="testo_pdf"), IT_CANDIDATES[1]]},
     "generic deposit notices (PDF text) is not supported"),
])
def test_italian_ambiguity_without_a_safe_single_choice_is_rejected(tmp_path, text, changes, words):
    reason = rejection(tmp_path, text=text, lookup=Lookup(ambiguous_sdir(**changes)))
    assert words in reason and reason.endswith("da fornire dal PM")


# Chiamante (trade_idea_model): fine esercizio del fornitore prezzi passata come PROXY dichiarato.
def test_provider_fiscal_year_end_is_a_declared_proxy():
    entries = sources.fiscal_year_end_from_provider({"lastFiscalYearEnd": 1777507200})     # 2026-04-30 UTC
    assert entries == [{"valore": "04-30", "fonte": "fornitore_prezzi",
                        "dettaglio": "PROXY: lastFiscalYearEnd 2026-04-30 del fornitore prezzi "
                                     "(yahoo public issuer profile), non una fonte ufficiale"}]
    assert sources._fiscal_year_end_declared("trimestrale", "2026-07-31", Q_TEXT, entries)["fonte"] == "fornitore_prezzi"
    for info in ({}, None, {"lastFiscalYearEnd": None}, {"lastFiscalYearEnd": "2026-04-30"},
                 {"lastFiscalYearEnd": True}, {"lastFiscalYearEnd": -5}):
        assert sources.fiscal_year_end_from_provider(info) == []


def test_trade_idea_model_passes_the_provider_fiscal_year_end_to_the_ingest():
    import inspect
    from bellomberg.valuation import trade_idea_model
    source = inspect.getsource(trade_idea_model)
    assert "fiscal_year_end_sources=fiscal_year_end_from_provider(bundle['case'].get('info'))" in source



# RV-D4 P1/P2: l'ora di un candidato scelto vale solo col SUO fuso; mai il fuso del listino al suo posto.
def rv_ambiguous(candidate_changes, **changes):
    rows = [dict(row, **candidate_changes) for row in deepcopy(AMBIGUOUS)]
    rows[1].update(data="2026-09-28", ora="22:30")
    return ambiguous_eu(candidati=rows, **changes)


def test_eu_choice_after_midnight_utc_on_the_cutoff_day_is_not_public(tmp_path, eu_recheck):
    reason = rejection(tmp_path, text=DEU_COVER, lookup=EULookup(rv_ambiguous({"fuso": "UTC"})), identity=EU_IDENTITY)
    assert "after the cutoff" in reason


def test_eu_choice_without_any_declared_time_zone_is_rejected(tmp_path, eu_recheck):
    # forma AMF/Nasdaq: ore UTC nei candidati, fuso del ritorno None -> mai il fuso del listino
    reason = rejection(tmp_path, text=DEU_COVER, lookup=EULookup(rv_ambiguous({})), identity=EU_IDENTITY)
    assert "time zone is unknown" in reason


def test_eu_choice_ignores_a_registry_hour_without_time_zone(tmp_path, eu_recheck):
    # forma AFM: fuso del ritorno Europe/Amsterdam (PROXY), ora del candidato senza fuso, 'id' invece di protocollo
    rows = [dict(row, id="AFM-%d" % index) for index, row in enumerate(deepcopy(AMBIGUOUS))]
    rows[0].update(titolo="Consolidated financial statements 2025")
    rows[1].update(titolo="Annual Report 2025", data="2026-09-27", ora="23:30")
    cover = "Synthetic issuer\nAnnual Report 2025\nYear ended 31 December 2025\nNarrative.\n"
    lookup = EULookup(ambiguous_eu(candidati=rows, fuso="Europe/Amsterdam", **AFM_FIELDS))
    row = ingest(tmp_path, text=cover, lookup=lookup, identity=EU_IDENTITY)["receipt"]["documents"][0]
    declaration = row["metadati"]["pm_source_verification"]["publication_declaration"]
    assert row["filed_date"] == "2026-09-27"
    assert "del 27/09/2026 (ora non indicata dalla fonte: fine del giorno)" in declaration
    assert "(protocollo AFM-1)" in declaration


# Identita' UE (ID-UE, GLEIF): risolta prima di depositi_ue, sigillata, riverificata; ISIN/LEI solo se 'ok'.
def test_eu_identity_gives_the_lei_and_is_sealed_and_reverified(tmp_path, eu_recheck, identity_eu_offline):
    isin = valid_isin("FR")
    lookup = EULookup(eu_deposit())
    result = ingest(tmp_path, lookup=lookup, identity={**EU_IDENTITY, "isin": isin})
    assert identity_eu_offline["risolvi"] == [("ZZTEST.PA", "Synthetic issuer", isin)]
    assert lookup.calls == [("ZZTEST.PA", "semestrale", "2026-06-30", isin, SYNTH_LEI, "Synthetic issuer")]
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    sealed = verification["deposit_receipt"]
    assert sealed["identita_ue"]["lei"] == SYNTH_LEI and sealed["lei"] == SYNTH_LEI
    assert verification["publication_declaration"].endswith("; emittente verificato su GLEIF (LEI %s)" % SYNTH_LEI)
    assert identity_eu_offline["riverifica"][-1] == ("ZZTEST.PA", "Synthetic issuer", isin)
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    for tampered in ({**value, "lei": "ZZ00OTHERLEI00000002"}, {**value, "isin": None},
                     {**value, "identita_ue": dict(value["identita_ue"], lei="ZZ00OTHERLEI00000002")},
                     {key: item for key, item in value.items() if key != "identita_ue"}):
        with pytest.raises(ValueError, match="EU issuer identity \\(GLEIF\\)"):
            sources._check_eu_deposit_value(tampered, "ZZTEST.PA", "semestrale", "2026-06-30", AS_OF, HALF_YEAR)


def test_eu_identity_not_ok_passes_neither_isin_nor_lei(tmp_path, eu_recheck, identity_eu_offline):
    lookup = EULookup(eu_deposit())
    result = ingest(tmp_path, lookup=lookup, identity=EU_IDENTITY)
    assert lookup.calls == [("ZZTEST.PA", "semestrale", "2026-06-30", None, None, "Synthetic issuer")]
    verification = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]
    assert verification["deposit_receipt"]["identita_ue"]["stato"] == "non_trovato"
    assert verification["publication_declaration"].endswith(
        "; identità dell'emittente non confermata su GLEIF (non_trovato): ISIN e LEI non usati")


def test_eu_identity_failure_is_named_when_the_deposit_fails(tmp_path, eu_recheck):
    lookup = EULookup(eu_deposit(stato="KO", errore="identita_mancante", motivo="serve ISIN o LEI", data_deposito=None))
    reason = rejection(tmp_path, lookup=lookup, identity=EU_IDENTITY)
    assert "EU issuer identity (GLEIF) non_trovato (nome_non_trovato): nessun LEI univoco (sintetico); " in reason
    assert "EU official deposit KO (identita_mancante)" in reason


def test_stale_eu_identity_values_are_never_used(tmp_path, eu_recheck, monkeypatch):
    from bellomberg.market_data import identita_ue
    isin = valid_isin("FR")
    stale = dict(identity_eu_result("ZZTEST.PA", nome="Synthetic issuer", isin=isin), stato="STALE",
                 stato_originale="ok")
    monkeypatch.setattr(identita_ue, "risolvi_identita_ue", lambda ticker, **kwargs: deepcopy(stale))
    monkeypatch.setattr(identita_ue, "riverifica_identita", lambda ricevuta, **kwargs: (ricevuta == stale, "x"))
    lookup = EULookup(eu_deposit())
    reason = rejection(tmp_path, lookup=lookup, identity={**EU_IDENTITY, "isin": isin})
    assert lookup.calls == [] and "EU issuer identity (GLEIF) is STALE and cannot be sealed" in reason


# RV-D4 secondo giro.
def test_storage_ambiguity_in_the_real_module_shape_is_resolved_by_the_cover(tmp_path):
    # forma VERA dell'ambiguo della sezione Documenti (emarket_sdir._unisci_documento_comunicato): tipo_data None
    from datetime import date as day
    from bellomberg.market_data import emarket_sdir as em
    com = em._base_deposito("ZZTEST.MI", "semestrale", "2026-06-30")
    com.update(stato="non_trovato", ticker="ZZTEST.MI", isin="IT0000000000", emarket_id=9999)
    other = dict(DOC_ROW, data="2026-08-07", protocollo="777009",
                 titolo="Bilancio consolidato semestrale abbreviato al 30 giugno 2026",
                 url="https://www.emarketstorage.it/sites/default/files/documenti/zztest-777009.pdf")
    rows = [dict(DOC_ROW, prova="categoria+nome"), dict(other, prova="categoria+nome")]
    doc = {"stato": "ambiguo", "errore": None, "motivo": "due documenti", "scelto": None, "candidati": rows,
           "conferme": [], "scartati": [], "prova": None, "note": [], "url_liste": [DOC_LIST],
           "sha256_liste": {DOC_LIST: "d" * 64}, "finestra": ["2026-07-01", "2026-10-01"], "pagine_lette": 1,
           "limiti": [], "risposte_salvate": {}}
    out = em._unisci_documento_comunicato(com, doc, day(2026, 6, 30))
    out.update(sdir="eMarket SDIR", instradamento={"regola": "negozio_confermato", "scelta": "emarket",
                                                   "emarket_id": 9999, "oneinfo_ndg": None})
    assert out["stato"] == "ambiguo" and out.get("tipo_data") is None
    out["risposte_salvate"] = deepcopy(SAVED)          # AGGIUNTA 5 (la riverifica completa e' finta qui)
    row = ingest(tmp_path, text=IT_COVER, lookup=Lookup(out))["receipt"]["documents"][0]
    assert row["filed_date"] == "2026-08-06"


@pytest.fixture
def full_sdir_recheck(sdir_full_recheck):
    return sdir_full_recheck


def test_full_sdir_receipt_is_sealed_and_reverified_offline(tmp_path, full_sdir_recheck):
    saved = SAVED
    result = ingest(tmp_path, lookup=Lookup(deposit(risposte_salvate=saved, sdir="eMarket SDIR",
        instradamento={"regola": "negozio_confermato", "scelta": "emarket", "emarket_id": 9999, "oneinfo_ndg": None})))
    sealed = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["deposit_receipt"]
    assert sealed["ricevuta_fonte"]["risposte_salvate"] == saved
    assert full_sdir_recheck[0] == ("ZZTEST.MI", "semestrale", "2026-06-30", None, "123456")
    sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    root = tmp_path / "pm-public-documents"
    for tampered, words in (({**value, "titolo": TITLE + " "}, "differ from the re-verified source receipt"),
                            ({**value, "ricevuta_fonte": dict(value["ricevuta_fonte"], risposte_salvate={})},
                             "failed the offline re-verification")):
        with pytest.raises(ValueError, match=words):
            sources._check_deposit_value(tampered, "ZZTEST.MI", "semestrale", "2026-06-30", AS_OF, root, HALF_YEAR)


def test_eu_identity_isin_must_be_the_confirmed_identity_isin(tmp_path, eu_recheck):
    isin = valid_isin("FR")
    identity = {**EU_IDENTITY, "isin": isin}
    result = ingest(tmp_path, lookup=EULookup(eu_deposit()), identity=identity)
    sources.verify_document_receipt(result["receipt"], "ZZTEST.PA", identity, AS_OF, archive_root=tmp_path,
                                    issuer_website=WEBSITE)
    with pytest.raises(ValueError, match="other than the confirmed identity"):
        sources.verify_document_receipt(result["receipt"], "ZZTEST.PA", {**identity, "isin": valid_isin("BE")},
                                        AS_OF, archive_root=tmp_path, issuer_website=WEBSITE)


def test_eu_identity_recheck_failure_rejects(tmp_path, eu_recheck, monkeypatch):
    from bellomberg.market_data import identita_ue
    monkeypatch.setattr(identita_ue, "riverifica_identita", lambda ricevuta, **kwargs: (False, "sha diverso"))
    reason = rejection(tmp_path, lookup=EULookup(eu_deposit()), identity=EU_IDENTITY)
    assert "EU issuer identity (GLEIF) failed the offline re-verification" in reason and "sha diverso" in reason


@pytest.mark.parametrize("value, accepted", [("02-28", True), ("02-29", True), ("02-27", False)])
def test_february_fiscal_year_end_is_a_month_end(value, accepted):
    entry = {"valore": value, "fonte": "fornitore_prezzi", "dettaglio": "x"}
    assert sources._fiscal_entry_ok(entry, "trimestrale", "2026-05-31") is accepted


def test_cover_fiscal_year_end_is_reread_from_the_cover(tmp_path):
    text = Q_TEXT + "Esercizio che chiude al 30 aprile 2027\n"
    sealed = ingest(tmp_path, text=text, lookup=Lookup(quarterly_deposit()))["receipt"]["documents"][0][
        "metadati"]["pm_source_verification"]["deposit_receipt"]
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    root = tmp_path / "pm-public-documents"
    sources._check_deposit_value(value, "ZZTEST.MI", "trimestrale", "2026-07-31", AS_OF, root, text)
    for other in (Q_TEXT + "Esercizio che chiude al 31 ottobre 2026\n", Q_TEXT, None):
        with pytest.raises(ValueError, match="read on the cover differs"):
            sources._check_deposit_value(value, "ZZTEST.MI", "trimestrale", "2026-07-31", AS_OF, root, other)


# RV-D4 P1: la ricevuta completa della fonte e' OBBLIGATORIA, in acquisizione e in riverifica.
def test_sdir_return_without_saved_responses_is_rejected(tmp_path):
    reason = rejection(tmp_path, lookup=Lookup(deposit(risposte_salvate={})))
    assert "without saved source responses cannot be re-verified offline" in reason and reason.endswith("da fornire dal PM")


def test_sealed_sdir_receipt_without_the_source_receipt_is_rejected(tmp_path):
    sealed = ingest(tmp_path, lookup=Lookup(deposit()))["receipt"]["documents"][0]["metadati"][
        "pm_source_verification"]["deposit_receipt"]
    value = {key: item for key, item in sealed.items() if key not in ("sha256", "ricevuta_fonte")}
    with pytest.raises(ValueError, match="lacks the full source receipt"):
        sources._check_deposit_value(dict(value, data_deposito="2026-07-02"), "ZZTEST.MI", "semestrale", "2026-06-30",
                                     AS_OF, tmp_path / "pm-public-documents", HALF_YEAR)


def test_origin_fallback_with_an_italian_isin_is_rejected_offline(tmp_path, origin_recheck):
    isin = valid_isin("NL")
    sdir_result = deposit(stato="non_coperto", errore="nessuno_sdir", motivo="motivo sintetico", data_deposito=None,
                          sdir=None)
    sealed = origin_ingest(tmp_path, sdir_result, OriginLookup(origin_deposit(isin)), isin)["receipt"]["documents"][0][
        "metadati"]["pm_source_verification"]["deposit_receipt"]
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    italian = valid_isin("IT")
    with pytest.raises(ValueError, match="Origin-state fallback after the Italian SDIRs is incoherent"):
        sources._check_eu_deposit_value(dict(value, isin_instradamento=italian), "ZZTEST.MI", "semestrale",
                                        "2026-06-30", AS_OF, HALF_YEAR)


def test_eu_identity_is_reverified_with_the_confirmed_name_not_the_receipt_name(tmp_path, eu_recheck,
                                                                                identity_eu_offline):
    # RV-ID: nome manomesso nella ricevuta E nel sigillo dell'identita' (coerenti fra loro) -> rifiuto
    isin = valid_isin("FR")
    identity = {**EU_IDENTITY, "isin": isin}
    sealed = ingest(tmp_path, lookup=EULookup(eu_deposit()), identity=identity)["receipt"]["documents"][0][
        "metadati"]["pm_source_verification"]["deposit_receipt"]
    root = tmp_path / "pm-public-documents"
    sources.verify_deposit_companion(sealed, root, "ZZTEST.PA", "semestrale", "2026-06-30", AS_OF, text=HALF_YEAR,
                                     identity=identity)
    other = "Other synthetic issuer"
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    value.update(nome=other, identita_ue=identity_eu_result("ZZTEST.PA", nome=other, isin=isin))
    with pytest.raises(ValueError, match="issuer name differs from the confirmed identity"):
        sources._check_eu_deposit_value(value, "ZZTEST.PA", "semestrale", "2026-06-30", AS_OF, HALF_YEAR,
                                        isin, True, identity_name=identity["name"])
    # il nome della ricevuta coincide ma l'identita' sigillata e' di un altro nome: la riverifica (col nome
    # confermato) la rifiuta
    value.update(nome=identity["name"])
    with pytest.raises(ValueError, match=r"EU issuer identity \(GLEIF\) failed"):
        sources._check_eu_deposit_value(value, "ZZTEST.PA", "semestrale", "2026-06-30", AS_OF, HALF_YEAR,
                                        isin, True, identity_name=identity["name"])


def test_eu_receipt_with_a_coherently_rewritten_name_fails_the_receipt_reverification(tmp_path, eu_recheck):
    # manomissione coerente (archivio + sigillo) del nome e dell'identita' sigillata: la riverifica con l'identita'
    # confermata della run la rifiuta
    import json as _json
    from hashlib import sha256 as _sha
    isin = valid_isin("FR")
    identity = {**EU_IDENTITY, "isin": isin}
    sealed = ingest(tmp_path, lookup=EULookup(eu_deposit()), identity=identity)["receipt"]["documents"][0][
        "metadati"]["pm_source_verification"]["deposit_receipt"]
    other = "Other synthetic issuer"
    value = {key: item for key, item in sealed.items() if key != "sha256"}
    value.update(nome=other, identita_ue=identity_eu_result("ZZTEST.PA", nome=other, isin=isin))
    raw = _json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    root = tmp_path / "pm-public-documents"
    (root / "publication-receipts" / (_sha(raw).hexdigest() + ".json")).write_bytes(raw)
    with pytest.raises(ValueError, match="issuer name differs from the confirmed identity"):
        sources.verify_deposit_companion({**value, "sha256": _sha(raw).hexdigest()}, root, "ZZTEST.PA", "semestrale",
                                         "2026-06-30", AS_OF, text=HALF_YEAR, identity=identity)


def test_eu_source_echoing_another_issuer_name_is_rejected_at_acquisition(tmp_path, eu_recheck):
    reason = rejection(tmp_path, lookup=EULookup(eu_deposit(nome="Other synthetic issuer")), identity=EU_IDENTITY)
    assert "issuer name differs from the confirmed identity" in reason


def test_non_ok_eu_identity_values_are_never_used(tmp_path, eu_recheck, monkeypatch):
    # un esito non 'ok' che porta comunque ISIN e LEI (es. ambiguo fra omonimi): nessuno dei due arriva alla fonte
    from bellomberg.market_data import identita_ue
    isin = valid_isin("FR")
    ambiguous = dict(identity_eu_result("ZZTEST.PA", nome="Synthetic issuer", isin=isin), stato="ambiguo")
    monkeypatch.setattr(identita_ue, "risolvi_identita_ue", lambda ticker, **kwargs: deepcopy(ambiguous))
    monkeypatch.setattr(identita_ue, "riverifica_identita", lambda ricevuta, **kwargs: (ricevuta == ambiguous, "x"))
    lookup = EULookup(eu_deposit())
    result = ingest(tmp_path, lookup=lookup, identity={**EU_IDENTITY, "isin": isin})
    assert lookup.calls == [("ZZTEST.PA", "semestrale", "2026-06-30", None, None, "Synthetic issuer")]
    declaration = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["publication_declaration"]
    assert declaration.endswith("non confermata su GLEIF (ambiguo): ISIN e LEI non usati")
