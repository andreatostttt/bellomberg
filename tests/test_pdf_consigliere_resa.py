# -*- coding: utf-8 -*-
"""Resa del memo del Consigliere nel PDF istituzionale (KC, 04/10, Opus 5.5).

Si misura il PDF VERO (font e colori degli span estratti con PyMuPDF), non il markdown:
1. l'intestazione delle tabelle markdown e' testo BIANCO in grassetto su fondo NAVY
   (prima: blu scuro su blu scuro, illeggibile);
2. **grassetto** esce col font bold (prima: tondo, i Paragraph su "LS" non avevano la
   famiglia registrata e il tag <b> restava senza effetto);
3. *corsivo* esce col font italic e senza asterischi (prima: asterischi letterali), anche
   per il piede di freshness.format_for_memo "*(...)*";
4. gli asterischi che NON sono enfasi ("3 * 4", "* isolato") restano;
5. la copertina (drawString) non stampa i marcatori ** del BLUF.
Ticker e numeri inventati. Nessuna scrittura fuori da tmp_path (grafici di cover spenti).
"""
import pytest

pymupdf = pytest.importorskip("pymupdf")

from bellomberg.core.freshness import format_for_memo
from bellomberg.core.language import language_context
from bellomberg.reporting import pdf_institutional as pi

NAVY_HEX = 0x1F3864

MEMO = """## BLUF
- Esempio sintetico con ticker inventati (ZZTEST, QQSYN.MI).
- **Esito del comitato.** Incompleto: vedi tabella.

## 1. Regime Macro
**Red Team.** Assente. Un passaggio *in corsivo semplice* nel testo.
Testo con un prodotto 3 * 4 e un asterisco isolato * che deve restare.

| Desk | Round | Causa |
|---|---|---|
| Crypto | R1 | **sovraccarico** |
| Options | R1 | troncata |

"""


@pytest.fixture(scope="module")
def pdf_spans(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(pi, "_gen_charts", lambda *a, **k: (None, None, None))
    mp.setattr(pi, "REPORT_DIR", str(tmp_path_factory.mktemp("report")))
    fresh = format_for_memo({"stale": ["ZZSERIE: osservazione vecchia"], "fresh": 3, "checked": 4})
    out = tmp_path_factory.mktemp("pdf") / "memo_kc.pdf"
    try:
        with language_context("it"):
            path = pi.build_institutional_memo(MEMO + fresh, output_path=str(out),
                                               title_date="4 ottobre 2026")
    finally:
        mp.undo()
    assert path, "il renderer ha restituito None"
    doc = pymupdf.open(str(out))
    spans = []
    for n, page in enumerate(doc, start=1):
        fills = [(d["rect"], d.get("fill")) for d in page.get_drawings() if d.get("fill")]
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    if s["text"].strip():
                        spans.append({"page": n, "text": s["text"], "font": s["font"],
                                      "color": s["color"], "bbox": pymupdf.Rect(s["bbox"]),
                                      "fills": fills})
    return spans


def _span(spans, testo, page=None):
    trovati = [s for s in spans if s["text"].strip() == testo and (page is None or s["page"] == page)]
    assert trovati, "span %r non trovato nel PDF; span vicini: %r" % (
        testo, [s["text"] for s in spans if testo.split()[0] in s["text"]][:5])
    return trovati[0]


def _rgb_int(fill):
    r, g, b = (round(c * 255) for c in fill[:3])
    return (r << 16) | (g << 8) | b


@pytest.mark.parametrize("testo", ["Desk", "Round", "Causa"])
def test_intestazione_tabella_bianca_grassetto_su_navy(pdf_spans, testo):
    s = _span(pdf_spans, testo)
    assert s["color"] == 0xFFFFFF, "intestazione %r colore #%06x, atteso bianco" % (testo, s["color"])
    assert "bold" in s["font"].lower(), "intestazione %r font %s, atteso bold" % (testo, s["font"])
    centro = (s["bbox"].tl + s["bbox"].br) / 2
    sotto = [_rgb_int(f) for r, f in s["fills"] if r.contains(centro)]
    assert NAVY_HEX in sotto, "fondo sotto %r = %s, atteso NAVY #%06x" % (
        testo, ["#%06x" % c for c in sotto], NAVY_HEX)


@pytest.mark.parametrize("testo", ["Esito del comitato.", "Red Team.", "sovraccarico"])
def test_grassetto_usa_il_font_bold(pdf_spans, testo):
    s = _span(pdf_spans, testo, page=2)
    assert "bold" in s["font"].lower(), "%r reso in %s, atteso bold" % (testo, s["font"])


@pytest.mark.parametrize("testo", ["in corsivo semplice"])
def test_corsivo_usa_il_font_italic(pdf_spans, testo):
    s = _span(pdf_spans, testo, page=2)
    f = s["font"].lower()
    assert "italic" in f or "oblique" in f, "%r reso in %s, atteso italic" % (testo, s["font"])


def test_piede_freshness_in_corsivo_senza_asterischi(pdf_spans):
    piede = [s for s in pdf_spans if "serie esterne controllate" in s["text"]]
    assert piede, "piede freshness assente dal PDF"
    for s in piede:
        assert "*" not in s["text"], "asterisco letterale nel piede: %r" % s["text"]
        f = s["font"].lower()
        assert "italic" in f or "oblique" in f, "piede in %s, atteso italic" % s["font"]


def test_nessun_marcatore_di_enfasi_letterale(pdf_spans):
    sporchi = [(s["page"], s["text"]) for s in pdf_spans if "**" in s["text"]
               or "*in corsivo" in s["text"] or "semplice*" in s["text"] or "*(" in s["text"]]
    assert not sporchi, "marcatori markdown stampati nel PDF: %r" % sporchi


def test_asterischi_non_di_enfasi_restano(pdf_spans):
    righe = [s["text"] for s in pdf_spans if "prodotto 3" in s["text"]]
    assert righe and "3 * 4" in righe[0] and "isolato * che" in righe[0], righe


def test_copertina_senza_asterischi_nel_bluf(pdf_spans):
    cover = [s["text"] for s in pdf_spans if s["page"] == 1 and "Esito del comitato" in s["text"]]
    assert cover, "il BLUF non e' in copertina"
    assert all("*" not in t for t in cover), cover
