"""Voce 4: PDF con poche pagine senza testo (disegni vettoriali, nessuna immagine).

Regola (approvata dal PM): pagine senza testo <= 5% delle pagine (aritmetica
intera) E ognuna con 0 immagini (anche annidate in Form XObject e inline) ->
documento ammesso, pagine DICHIARATE nella ricevuta e nel riepilogo; altrimenti
rifiuto. PDF sintetici generati qui, nessuna rete, nessun dato vero.
"""
from copy import deepcopy
from io import BytesIO
import json

import pytest

from bellomberg.agents import trade_idea_sources as sources
from bellomberg.valuation.valuation_sources import collect_documents
from test_trade_idea_pm_sources import (IDENTITY, TEXT, URL, WEBSITE, Response, transport,
                                        measured_no_real_transports)  # noqa: F401 (guardia autouse)

AS_OF = "2026-09-28"
IMAGE_KINDS = ("image", "form_image", "inline_image", "form_inline_image", "smask_image")


def _pdf(text_pages, extra=()):
    """`text_pages` pagine con testo, poi le pagine `extra` senza testo:
    'vector' = solo disegni; le IMAGE_KINDS portano un'immagine 1x1."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                               NumberObject)
    from reportlab.pdfgen.canvas import Canvas
    buffer = BytesIO()
    canvas = Canvas(buffer)
    for number in range(text_pages):
        lines = TEXT.splitlines() if number == 0 else ["Synthetic narrative page %d" % (number + 1)]
        for index, line in enumerate(lines):
            canvas.drawString(40, 780 - index * 20, line)
        canvas.showPage()
    for kind in extra:
        if kind == "vector":
            canvas.rect(40, 40, 200, 120, fill=1)
            canvas.line(10, 10, 300, 300)
            canvas.showPage()
    canvas.save()
    writer = PdfWriter(clone_from=PdfReader(BytesIO(buffer.getvalue())))

    def stream(data, **entries):
        value = DecodedStreamObject()
        value.set_data(data)
        for key, item in entries.items():
            value[NameObject("/" + key)] = item
        return writer._add_object(value)

    def image():
        return stream(b"\x80", Type=NameObject("/XObject"), Subtype=NameObject("/Image"),
                      Width=NumberObject(1), Height=NumberObject(1),
                      ColorSpace=NameObject("/DeviceGray"), BitsPerComponent=NumberObject(8))

    inline = b"q 100 0 0 100 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI Q"
    for kind in extra:
        if kind not in IMAGE_KINDS:
            continue
        page = writer.add_blank_page(width=595, height=842)
        xobjects = DictionaryObject()
        if kind == "image":
            xobjects[NameObject("/Im0")] = image()
            content = b"q 100 0 0 100 0 0 cm /Im0 Do Q"
        elif kind == "inline_image":
            content = inline
        elif kind == "smask_image":
            # Scansione dipinta come soft mask: ExtGState /SMask /G -> Form con immagine.
            group = stream(b"q 595 0 0 842 0 0 cm /Im0 Do Q", Type=NameObject("/XObject"),
                Subtype=NameObject("/Form"),
                Resources=DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): image()})}),
                BBox=ArrayObject([NumberObject(0), NumberObject(0), NumberObject(595), NumberObject(842)]))
            smask = DictionaryObject({NameObject("/Type"): NameObject("/Mask"),
                NameObject("/S"): NameObject("/Luminosity"), NameObject("/G"): group})
            state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"), NameObject("/SMask"): smask})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/ExtGState"):
                DictionaryObject({NameObject("/GS0"): state})})
            page[NameObject("/Contents")] = stream(b"q /GS0 gs 0 g 0 0 595 842 re f Q")
            continue
        else:
            inner = DictionaryObject()
            if kind == "form_image":
                inner[NameObject("/Im0")] = image()
                form_content = b"q 100 0 0 100 0 0 cm /Im0 Do Q"
            else:
                form_content = inline
            resources = DictionaryObject({NameObject("/XObject"): inner})
            xobjects[NameObject("/Fm0")] = stream(form_content, Type=NameObject("/XObject"),
                Subtype=NameObject("/Form"), Resources=resources,
                BBox=ArrayObject([NumberObject(0), NumberObject(0), NumberObject(595), NumberObject(842)]))
            content = b"/Fm0 Do"
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
        page[NameObject("/Contents")] = stream(content)
    target = BytesIO()
    writer.write(target)
    return target.getvalue()


def _ingest(tmp_path, raw):
    download, _ = transport(responses=[Response(raw, headers={"Content-Type": "application/pdf"})])
    return sources.ingest_document_sources(IDENTITY["ticker"], IDENTITY, AS_OF, [{"url": URL}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download)


def _rejection(tmp_path, raw):
    with pytest.raises(sources.SourceIngestionError) as blocked:
        _ingest(tmp_path, raw)
    row = blocked.value.receipt["documents"][0]
    assert row["status"] == "needs_verification"
    return row["reason"]


def test_one_vector_page_in_forty_one_is_accepted_and_declared_everywhere(tmp_path):
    result = _ingest(tmp_path, _pdf(40, ["vector"]))
    row = result["receipt"]["documents"][0]
    declared = row["metadati"]["pm_source_verification"]["textless_pages"]
    assert declared == {"pages": [41], "pages_total": 41, "images_on_pages": 0,
        "policy": sources.TEXTLESS_PAGES_POLICY,
        "limitation": declared["limitation"]}
    assert "OCR" in declared["limitation"]
    # Il collettore comune ha accettato il documento e ne conserva la copertura.
    assert result["documents"][0]["extraction_coverage"]["pagine_senza_testo"] == [41]
    # Riepilogo per il PM/dossier: le pagine sono dichiarate, nessun percorso.
    public = sources.document_receipt_summary(result["receipt"])
    assert public["status"] == "verified"
    assert public["documents"][0]["pages_without_text"] == [41]
    assert "OCR" in public["documents"][0]["pages_without_text_limitation"]
    new_fields = json.dumps({key: public["documents"][0][key] for key in
        ("pages_without_text", "pages_without_text_limitation")})
    assert "path" not in new_fields and "quote" not in new_fields
    # La ricevuta resta riverificabile dai byte sigillati (dichiarazione compresa).
    again = sources.verify_document_receipt(result["receipt"], IDENTITY["ticker"], IDENTITY, AS_OF,
        archive_root=tmp_path, issuer_website=WEBSITE)
    assert again["receipt"] == result["receipt"]


def test_receipt_without_textless_pages_has_no_new_key(tmp_path):
    result = _ingest(tmp_path, _pdf(3))
    row = result["receipt"]["documents"][0]
    assert "textless_pages" not in row["metadati"]["pm_source_verification"]
    public = sources.document_receipt_summary(result["receipt"])
    assert public["documents"][0]["pages_without_text"] == []
    assert "pages_without_text_limitation" not in public["documents"][0]


def test_exactly_five_percent_is_accepted(tmp_path):
    result = _ingest(tmp_path, _pdf(38, ["vector", "vector"]))
    declared = result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["textless_pages"]
    assert declared["pages"] == [39, 40] and declared["pages_total"] == 40


@pytest.mark.parametrize("text_pages, vectors", [(1, 1), (37, 3)])
def test_more_than_five_percent_is_rejected_with_the_threshold(tmp_path, text_pages, vectors):
    reason = _rejection(tmp_path, _pdf(text_pages, ["vector"] * vectors))
    assert "exceed the 5% limit" in reason
    assert "%d of %d" % (vectors, text_pages + vectors) in reason


@pytest.mark.parametrize("kind", IMAGE_KINDS)
def test_textless_page_with_any_image_is_rejected_as_possible_scan(tmp_path, kind):
    reason = _rejection(tmp_path, _pdf(40, [kind]))
    assert "contain images" in reason and "[41]" in reason


def test_image_count_reaches_nested_forms_and_inline_images():
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(_pdf(1, ["vector", *IMAGE_KINDS])))
    counts = [sources._pdf_page_image_count(page, reader) for page in reader.pages]
    assert counts == [0, 0, 1, 1, 1, 1, 1]


def test_fully_scanned_document_stays_rejected(tmp_path):
    reason = _rejection(tmp_path, _pdf(0, ["image", "image"]))
    assert "incomplete" in reason


def _checked(tmp_path):
    result = _ingest(tmp_path, _pdf(40, ["vector"]))
    return deepcopy(result["filing_results"][0]["candidati"][0])


def _collect(tmp_path, candidate):
    return collect_documents(IDENTITY["ticker"], as_of=AS_OF, archive_root=tmp_path,
        filing_results=[{"ticker": IDENTITY["ticker"], "candidati": [candidate], "motivi": []}],
        catalog=lambda *_: {"stato": "ok", "documenti": [], "motivi": []})


@pytest.mark.parametrize("tamper", ["missing", "other_pages", "other_contract", "other_total",
                                    "float_images", "bool_images", "float_page", "float_total"])
def test_collector_accepts_textless_pages_only_when_declared_exactly(tmp_path, tamper):
    candidate = _checked(tmp_path)
    assert _collect(tmp_path, deepcopy(candidate))["issues"] == []
    verification = candidate["metadati"]["pm_source_verification"]
    if tamper == "missing":
        del verification["textless_pages"]
    elif tamper == "other_pages":
        verification["textless_pages"]["pages"] = [40]
    elif tamper == "other_contract":
        verification["contract"] = "other-documents/1"
    elif tamper == "other_total":
        verification["textless_pages"]["pages_total"] = 400
    elif tamper == "float_images":
        verification["textless_pages"]["images_on_pages"] = 0.0
    elif tamper == "bool_images":
        verification["textless_pages"]["images_on_pages"] = False
    elif tamper == "float_page":
        verification["textless_pages"]["pages"] = [41.0]
    else:
        verification["textless_pages"]["pages_total"] = 41.0
    report = _collect(tmp_path, candidate)
    assert report["documents"] == []
    assert "without extractable text" in report["issues"][0]["reason"]


def test_collector_rejects_a_declaration_on_a_document_with_text_on_every_page(tmp_path):
    result = _ingest(tmp_path, _pdf(3))
    candidate = deepcopy(result["filing_results"][0]["candidati"][0])
    candidate["metadati"]["pm_source_verification"]["textless_pages"] = {"pages": [3]}
    report = _collect(tmp_path, candidate)
    assert report["documents"] == [] and "absent from the document" in report["issues"][0]["reason"]


def test_company_research_acquire_uses_the_same_rule(tmp_path):
    from test_company_document_stabilization import URL as RESEARCH_URL
    from test_company_source_research import FrozenTransport, session
    raw = _pdf(20, ["vector"])  # 1 pagina disegnata su 21 = 4,8%
    current = session(tmp_path, FrozenTransport({"/report.pdf": (raw, "application/pdf")}))
    result = current.acquire({"url": RESEARCH_URL})
    assert result["ok"], result["source"]["documents"][0]["reason"]
    assert result["source"]["documents"][0]["pages_without_text"] == [21]
