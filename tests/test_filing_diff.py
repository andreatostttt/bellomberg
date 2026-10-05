"""I-20: documenti sintetici USA/Europa; nessuna rete o scrittura runtime."""
import hashlib
from pathlib import Path

import pytest

from bellomberg.market_data import lettore_trimestrali as lettore


def documento(tmp_path, anno, testo, lingua="en", **override):
    from bellomberg.market_data.filing_diff import prepara_documento
    path = tmp_path / f"report-{anno}-{len(list(tmp_path.iterdir()))}.html"
    path.write_text(testo, encoding="utf-8")
    meta = {"emittente_id": "LEI:TESTISSUER", "periodo_inizio": f"{anno}-01-01",
            "periodo_fine": f"{anno}-12-31", "tipo": "annuale",
            "lingua": lingua, "perimetro": "consolidato"}
    meta.update(override)
    sezioni = {"rischi": {"inizio": "Risk factors", "fine": "Management discussion"},
               "gestione": {"inizio": "Management discussion", "fine": "Other information"}}
    return prepara_documento(str(path), url=f"https://issuer.example/{anno}.html",
                             metadati=meta, sezioni=sezioni)


def html(rischi, gestione="Revenue is stable."):
    return (f"<html><h1>Risk factors</h1><p>{rischi}</p>"
            f"<h1>Management discussion</h1><p>{gestione}</p>"
            "<h1>Other information</h1><p>End.</p></html>")


def test_changed_numbers_and_negation_have_exact_citations(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    prima = documento(tmp_path, 2024, html("Costs rose by 10%. We do not face litigation."))
    dopo = documento(tmp_path, 2025, html("Costs rose by 20%. We do face litigation."))
    result = confronta_documenti(prima, dopo)
    assert result["stato"] == "ok"
    assert len(result["cambiamenti"]) == 2
    for change in result["cambiamenti"]:
        assert change["tipo"] == "modificato"
        for side, doc in (("prima", prima), ("dopo", dopo)):
            cite = change[side]
            assert cite["testo"] == doc["estrazione"]["testo"][cite["inizio"]:cite["fine"]]
            assert cite["sha256"] == doc["sha256"]
            assert cite["url"] == doc["url"]


def test_inline_tags_and_linewrap_are_not_changes(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    a = documento(tmp_path, 2024, html("Memory prices are rising."))
    b = documento(tmp_path, 2025, html("Memory <b>prices</b> are\nrising."))
    assert confronta_documenti(a, b)["cambiamenti"] == []


def test_text_moved_between_sections_is_not_a_new_or_removed_risk(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    a = documento(tmp_path, 2024, html("Tariffs may increase costs. Demand is uncertain."))
    b = documento(tmp_path, 2025, html("Demand is uncertain.",
                                     "Revenue is stable. Tariffs may increase costs."))
    changes = confronta_documenti(a, b)["cambiamenti"]
    assert len(changes) == 1
    assert changes[0]["tipo"] == "spostato"
    assert changes[0]["prima"]["sezione"] == "rischi"
    assert changes[0]["dopo"]["sezione"] == "gestione"


@pytest.mark.parametrize("override", [
    {"emittente_id": "LEI:OTHER"}, {"lingua": "it"}, {"perimetro": "individuale"},
    {"tipo": "semestrale", "periodo_fine": "2025-06-30"},
    {"periodo_inizio": "2025-07-01"}, {"periodo_fine": "not-a-date"},
    {"lingua": ""}, {"periodo_fine": "2024-12-31"},
])
def test_incompatible_or_missing_metadata_declared(tmp_path, override):
    from bellomberg.market_data.filing_diff import confronta_documenti
    a = documento(tmp_path, 2024, html("Risk unchanged."))
    b = documento(tmp_path, 2025, html("Risk unchanged."), **override)
    r = confronta_documenti(a, b)
    assert r["stato"] == "non_confrontabile"
    assert r["motivi"] and not r["cambiamenti"]


def test_missing_section_is_a_gap_not_removal(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    a = documento(tmp_path, 2024, html("A material risk."))
    b = documento(tmp_path, 2025, html("A material risk.").replace("Risk factors", "Unknown heading"))
    r = confronta_documenti(a, b)
    assert r["stato"] == "parziale"
    assert r["motivi"]
    assert not r["cambiamenti"]


def test_duplicate_heading_requires_explicit_occurrence(tmp_path):
    a = documento(tmp_path, 2024, "<h1>Risk factors</h1>" + html("Risk."))
    assert a["sezioni"]["rischi"]["stato"] == "non_disponibile"
    assert "ambigu" in a["sezioni"]["rischi"]["motivo"].lower()


@pytest.mark.parametrize("lingua,titolo,testo", [
    ("it", "Fattori di rischio", "Il costo delle memorie è aumentato."),
    ("de", "Risiken", "Die Kosten für Speicher steigen."),
    ("en", "Principal risks", "Memory costs have increased."),
])
def test_european_headings_are_selectable_without_sec_labels(tmp_path, lingua, titolo, testo):
    from bellomberg.market_data.filing_diff import prepara_documento, confronta_documenti
    docs = []
    for anno, corpo in ((2024, "Stable."), (2025, testo)):
        base = documento(tmp_path, anno, html(corpo), lingua=lingua)
        path = tmp_path / f"local-{anno}.html"
        path.write_text(f"<h1>{titolo}</h1><p>{corpo}</p><h1>Fine</h1>", encoding="utf-8")
        docs.append(prepara_documento(str(path), url=base["url"], metadati=base["metadati"],
                    sezioni={"rischi": {"inizio": titolo, "fine": "Fine"}}))
    r = confronta_documenti(*docs)
    assert r["stato"] == "ok"
    assert any(c.get("dopo", {}).get("testo") == testo for c in r["cambiamenti"])


def test_pdf_page_offsets_and_empty_pages_declared(tmp_path):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from test_lettore_trimestrali import _pdf_minimo
    writer = PdfWriter()
    writer.add_page(PdfReader(BytesIO(_pdf_minimo())).pages[0])
    writer.add_blank_page(width=612, height=792)
    writer.add_page(PdfReader(BytesIO(_pdf_minimo())).pages[0])
    path = tmp_path / "report.pdf"
    writer.write(path)
    r = lettore.estrai_testo(str(path))
    assert r["pagine_senza_testo"] == [2]
    assert [p["pagina"] for p in r["riferimenti"]] == [1, 2, 3]
    for ref in r["riferimenti"]:
        assert r["testo"][ref["inizio"]:ref["fine"]] == ref["testo"]


def test_same_download_url_never_overwrites_previous_version(tmp_path, monkeypatch):
    from test_lettore_trimestrali import _RispostaFinta
    old = tmp_path / "report.pdf"
    old.write_bytes(b"preserve me")
    for payload in (b"first version", b"second version"):
        monkeypatch.setattr(lettore.requests, "get", lambda *a, **k: _RispostaFinta(payload))
        r = lettore.scarica_documento("https://issuer.example/report.pdf", str(tmp_path))
        assert r["stato"] == "ok"
        assert Path(r["path"]).read_bytes() == payload
        assert r["sha256"] == hashlib.sha256(payload).hexdigest()
    assert old.read_bytes() == b"preserve me"
    assert len(list(tmp_path.iterdir())) == 3


def test_plain_generic_download_is_candidate_but_not_confirmed_report():
    page = "<div>Annual Report 2025</div><a href='/reports/44/document'>Download</a>"
    candidates = lettore.candidati_comunicato(page, "https://issuer.example/reports")
    assert candidates[0]["url"] == "https://issuer.example/reports/44/document"


def test_reordering_inside_section_is_visible(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    a = documento(tmp_path, 2024, html("First priority. Second priority. Third priority."))
    b = documento(tmp_path, 2025, html("Third priority. First priority. Second priority."))
    changes = confronta_documenti(a, b)["cambiamenti"]
    assert changes and all(c["tipo"] == "spostato" for c in changes)


def test_move_to_section_missing_before_is_still_recognized(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    a = documento(tmp_path, 2024, html("Demand stable. Litigation continues."))
    b = documento(tmp_path, 2025, html("Demand stable.", "Litigation continues."))
    a["sezioni"]["gestione"] = {"stato": "non_disponibile", "motivo": "missing"}
    r = confronta_documenti(a, b)
    assert r["stato"] == "parziale"
    assert len(r["cambiamenti"]) == 1
    assert r["cambiamenti"][0]["tipo"] == "spostato"


def test_page_gap_is_not_available_section(tmp_path):
    from bellomberg.market_data.filing_diff import prepara_documento
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    path = tmp_path / "blank.pdf"
    writer.write(path)
    result = prepara_documento(str(path), url="https://example.test/blank.pdf",
                               metadati={}, sezioni={"rischi": {"da_pagina": 1, "a_pagina": 1}})
    assert result["stato"] == "errore"


def test_manifest_pipeline_uses_actual_downloads_and_exact_pdf_pages(tmp_path, monkeypatch):
    from test_lettore_trimestrali import _pdf_minimo, _RispostaFinta
    from bellomberg.market_data.filing_diff import esegui_manifest
    specs = {}
    for lato, anno in (("prima", 2024), ("dopo", 2025)):
        base = documento(tmp_path, anno, html("Ignore."))
        specs[lato] = {"url": f"https://issuer.example/{anno}.pdf", "metadati": base["metadati"],
                       "sezioni": {"rischi": {"da_pagina": 1, "a_pagina": 1}}}
    calls = []
    def get(url, **kw):
        calls.append(url)
        return _RispostaFinta(_pdf_minimo())
    monkeypatch.setattr(lettore.requests, "get", get)
    r = esegui_manifest(specs, base_dir=tmp_path, archivio=tmp_path / "downloads")
    assert len(calls) == 2 and r["stato"] == "ok"
    assert r["misure"]["segmenti_prima"] > 0
    assert not r["cambiamenti"]


def test_similarity_is_computed_from_section_tokens(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    a = documento(tmp_path, 2024, html("alpha beta."))
    b = documento(tmp_path, 2025, html("alpha gamma."))
    score = confronta_documenti(a, b)["similarita_sezioni"]["rischi"]
    assert score["jaccard"] == pytest.approx(1/3)
    assert score["coseno"] == pytest.approx(0.5)


def test_html_european_encoding_declared_or_error(tmp_path):
    path = tmp_path / "italiano.html"
    path.write_bytes('<html><meta charset="windows-1252"><p>Liquidità è stabile.</p></html>'.encode('cp1252'))
    result = lettore.estrai_testo(str(path))
    assert result["stato"] == "ok"
    assert "Liquidità è stabile." in result["testo"]
    path.write_bytes(b'<html><p>Invalid UTF8: \xff</p></html>')
    result = lettore.estrai_testo(str(path))
    assert result["stato"] == "illeggibile" and result["motivo"]


def test_pdf_citations_refer_to_same_snapshot_as_hash(tmp_path, monkeypatch):
    from test_lettore_trimestrali import _pdf_minimo
    from bellomberg.market_data import filing_diff
    path = tmp_path / "snapshot.pdf"
    original = _pdf_minimo()
    path.write_bytes(original)
    extract = filing_diff.estrai_testo
    def changed_on_disk(path, **kw):
        Path(path).write_bytes(original.replace(b'2026', b'2027'))
        return extract(path, **kw)
    monkeypatch.setattr(filing_diff, "estrai_testo", changed_on_disk)
    doc = filing_diff.prepara_documento(str(path), url="https://example.test/report.pdf",
           metadati={}, sezioni={"rischi": {"da_pagina": 1, "a_pagina": 1}})
    assert doc["sha256"] == hashlib.sha256(original).hexdigest()
    assert "2026" in doc["estrazione"]["testo"] and "2027" not in doc["estrazione"]["testo"]


# --- prova reale fase D: riformulati sparsi in documenti lunghi ---

def _frase(i, variante=False):
    base = (f"Nova risk {i} concerns supply of component {i} from third parties and may affect "
            f"margins in segment {i % 7} over the coming periods")
    return (base.replace("may affect", "could materially affect") + " and beyond") if variante else base


def _riformulati(tmp_path, n=600, ogni=4):
    prima = " ".join(_frase(i) + "." for i in range(n))
    dopo = " ".join(_frase(i, variante=(i % ogni == 0)) + "." for i in range(n))
    return documento(tmp_path, 2025, html(prima)), documento(tmp_path, 2026, html(dopo))


def test_riformulati_sparsi_oltre_la_vecchia_soglia_sono_modificati(tmp_path):
    import time
    from bellomberg.market_data.filing_diff import LIMITE_COPPIE, confronta_documenti
    prima, dopo = _riformulati(tmp_path)
    t0 = time.perf_counter()
    r = confronta_documenti(prima, dopo)
    durata = time.perf_counter() - t0
    tipi = [c["tipo"] for c in r["cambiamenti"]]
    assert tipi.count("modificato") == 150 and "aggiunto" not in tipi and "rimosso" not in tipi
    assert LIMITE_COPPIE not in r["limiti"]
    # ogni modificato abbina lo stesso paragrafo (stesso indice i)
    for c in r["cambiamenti"]:
        assert c["prima"]["testo"].split()[2] == c["dopo"]["testo"].split()[2]
    assert durata < 3.0, durata


def test_riformulati_deterministici(tmp_path):
    from bellomberg.market_data.filing_diff import confronta_documenti
    prima, dopo = _riformulati(tmp_path, n=300, ogni=3)
    a, b = confronta_documenti(prima, dopo), confronta_documenti(prima, dopo)
    assert [(c["tipo"], c["prima"]["inizio"], c["dopo"]["inizio"]) for c in a["cambiamenti"]] == \
           [(c["tipo"], c["prima"]["inizio"], c["dopo"]["inizio"]) for c in b["cambiamenti"]]


def test_buco_enorme_usa_la_finestra_locale_dichiarata(tmp_path):
    # Un unico blocco di 150 frasi tutte riformulate per lato (22.500 coppie nel buco): finestra locale.
    from bellomberg.market_data.filing_diff import LIMITE_FINESTRA, confronta_documenti
    prima = " ".join(_frase(i) + "." for i in range(150))
    dopo = " ".join(_frase(i, variante=True) + "." for i in range(150))
    r = confronta_documenti(documento(tmp_path, 2025, html(prima)), documento(tmp_path, 2026, html(dopo)))
    tipi = [c["tipo"] for c in r["cambiamenti"]]
    assert tipi.count("modificato") == 150
    assert LIMITE_FINESTRA in r["limiti"]


def test_piccoli_documenti_abbinano_come_prima(tmp_path):
    # Sotto la soglia nulla cambia: un riformulato spostato lontano resta "modificato".
    from bellomberg.market_data.filing_diff import LIMITE_FINESTRA, confronta_documenti
    frasi = [_frase(i) + "." for i in range(20)]
    dopo = frasi[1:] + [_frase(0, variante=True) + "."]
    r = confronta_documenti(documento(tmp_path, 2025, html(" ".join(frasi))),
                            documento(tmp_path, 2026, html(" ".join(dopo))))
    assert [c["tipo"] for c in r["cambiamenti"]] == ["modificato"]
    assert LIMITE_FINESTRA not in r["limiti"]


# --- prova reale fase D: intestazioni di pagina incollate nei segmenti ---

def _doc_pdf(pagine):
    riferimenti, pos = [], 0
    for n, p in enumerate(pagine, 1):
        riferimenti.append({"pagina": n, "inizio": pos, "fine": pos + len(p), "testo": p})
        pos += len(p) + 1
    testo = "\n".join(pagine)
    return {"estrazione": {"testo": testo, "formato": "pdf", "riferimenti": riferimenti},
            "sezioni": {"rischi": {"stato": "ok", "inizio": 0, "fine": len(testo)}}}


def test_intestazione_ripetuta_su_quattro_pagine_tolta_dai_segmenti():
    from bellomberg.market_data.filing_diff import _unita
    pagine = [f"Acme Group | Interim report 2026 | page {n}\nAcme risk {n} is described here in full.\n{n + 10}"
              for n in range(1, 5)]
    doc = _doc_pdf(pagine)
    segmenti = _unita(doc, {"rischi"})
    testi = [s["testo"] for s in segmenti]
    assert testi == [f"Acme risk {n} is described here in full." for n in range(1, 5)]
    # citazioni letterali: offset sul testo originale
    for s in segmenti:
        assert doc["estrazione"]["testo"][s["inizio"]:s["fine"]] == s["testo"]


def test_riga_su_due_pagine_non_e_intestazione():
    from bellomberg.market_data.filing_diff import _unita
    pagine = ["Acme header line\nFirst risk.", "Acme header line\nSecond risk.", "Other\nThird risk."]
    testi = [s["testo"] for s in _unita(_doc_pdf(pagine), {"rischi"})]
    assert testi[0].startswith("Acme header line")


def test_table_of_contents_con_numero_di_pagina_nell_html(tmp_path):
    # HTML SEC: nessun confine di pagina; solo "Table of Contents" con il numero di pagina accanto.
    from bellomberg.market_data.filing_diff import confronta_documenti
    p = "Kore risk one is stable. Kore risk two is stable."
    a = html(f"{p}</p><p>45 | 2025 Q3 10-Q</p><p>Table of Contents</p><p>Kore risk three is stable.")
    b = html(f"{p}</p><p>Kore risk three is stable.")
    r = confronta_documenti(documento(tmp_path, 2025, a), documento(tmp_path, 2026, b))
    assert r["cambiamenti"] == []
    c = html(f"{p}</p><p>18</p><p>Table of Contents</p><p>Kore risk three is stable.")
    assert confronta_documenti(documento(tmp_path, 2025, c), documento(tmp_path, 2026, b))["cambiamenti"] == []
    # senza numero di pagina il testo resta (regola conservativa)
    d = html(f"{p} See the Table of Contents for details.</p><p>Kore risk three is stable.")
    assert confronta_documenti(documento(tmp_path, 2025, d), documento(tmp_path, 2026, b))["cambiamenti"]


def test_costo_dell_abbinamento_limitato_per_documento(tmp_path):
    # Caso peggiore della revisione: 20 buchi da 100×100 frasi tutte simili (200.000 confronti).
    import time
    from bellomberg.market_data.filing_diff import BUDGET_CONFRONTI, LIMITE_FINESTRA, confronta_documenti

    def f(i, v):
        return (f"Nova risk concerns supply of component {i:05d} from third parties "
                f"{'and could materially affect' if v else 'and may affect'} margins over periods.")
    pr, do, k = [], [], 0
    for g in range(20):
        pr.append(f"Anchor paragraph number {g} stays identical in both documents.")
        do.append(pr[-1])
        for _ in range(100):
            pr.append(f(k, False))
            do.append(f(k + 50000, True))
            k += 1
    a, b = documento(tmp_path, 2025, html(" ".join(pr))), documento(tmp_path, 2026, html(" ".join(do)))
    t0 = time.perf_counter()
    r = confronta_documenti(a, b)
    durata = time.perf_counter() - t0
    # Limite deterministico: confronti contati (prima 200.000); il tempo solo come rete larga.
    assert LIMITE_FINESTRA in r["limiti"]
    assert r["misure"]["confronti_abbinamento"] <= BUDGET_CONFRONTI + 7 * 2000
    assert durata < 30.0, durata
    assert sum(c["tipo"] == "modificato" for c in r["cambiamenti"]) >= 1500


def test_riga_di_totale_ripetuta_non_e_intestazione():
    # Revisione: una riga di totale di tabella in coda a 3+ pagine resta nel testo.
    from bellomberg.market_data.filing_diff import _unita
    pagine = [f"Acme segment {n} revenue table.\nTotal revenue {n},234 {n + 4},678" for n in range(1, 5)]
    testi = [s["testo"] for s in _unita(_doc_pdf(pagine), {"rischi"})]
    assert sum("Total revenue" in t for t in testi) == 4
