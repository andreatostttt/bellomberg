"""Dedicated research dossier from the accepted structured Trade Idea result.

The renderer neither invents missing research nor calls an LLM. Length and text
coverage are measured on the final PDF; insufficient research remains partial.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
import re
import unicodedata

from pypdf import PdfReader
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String, Rect, Line
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Spacer, Table, TableStyle, PageBreak, NextPageTemplate, Flowable, KeepTogether)

from bellomberg.core.trade_idea_contract import DOSSIER_KEYS
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V2, EXECUTION_POLICY_V4, RESEARCH_POLICIES
from bellomberg.reporting.pdf_institutional import (_register_fonts, _draw_lockup,
    OBSIDIAN, AMBER, AMBER_DEEP)

NAVY = OBSIDIAN  # compatibility with existing chart helpers; research identity is obsidian.
INK = colors.HexColor("#262626")
BRASS = colors.HexColor("#8D6A00")
MUTED = colors.HexColor("#606973")
PAPER = colors.white
PALE = colors.HexColor("#F2F4F7")
RULE = colors.HexColor("#D9DDE3")

LABELS = {
    "research": ("DOSSIER DI RICERCA", "RESEARCH DOSSIER"),
    "contents": ("Sommario", "Contents"),
    "judgment": ("Giudizio del comitato", "Committee judgment"),
    "pm_view": ("View del PM - tesi da verificare", "PM view - thesis to test"),
    "missing": ("Dato mancante / limite", "Missing data / limitation"),
    "sources": ("Fonti, copertura e metodologia", "Sources, coverage and methodology"),
    "models": ("Modelli e perimetro", "Models and scope"),
    "dcn": ("Proposta in DCN - da valutare", "DCN proposal - pending review"),
    "research_route": ("In ricerca", "In research"),
    "none": ("Nessuna destinazione", "No destination"),
    "partial": ("RICERCA PARZIALE", "PARTIAL RESEARCH"),
    "no_view": ("Nessuna view inserita dal PM.", "No view supplied by the PM."),
    "favorable": ("Favorevole", "Favorable"),
    "rejected": ("Respinta", "Rejected"),
    "watch": ("Da monitorare", "Watch"),
    "incomplete": ("Incompleta", "Incomplete"),
    "no_order": ("Ricerca per la valutazione del PM. Nessun ordine automatico.",
                 "Research for PM review. No automatic orders."),
    "scope": ("Il dossier riguarda soltanto il titolo selezionato. Peer e portafoglio sono contesto. "
              "Fatti, ipotesi e scenari restano attribuiti alle fonti dei report; la grafica non aggiunge dati.",
              "This dossier concerns only the selected stock. Peers and portfolio are context. "
              "Facts, assumptions and scenarios retain the reports' source attribution; the renderer adds no data."),
    "model_missing": ("Modello Excel incompleto o non disponibile", "Excel model incomplete or unavailable"),
    "model_ready": ("Workbook verificati collegati alla ricerca", "Verified workbooks linked to the research"),
    "model_state_ready": ("Modello Excel: verificato", "Excel model: verified"),
    "model_state_incomplete": ("Modello Excel: incompleto", "Excel model: incomplete"),
    "thesis": ("Tesi", "Thesis"),
    "dominant_risk": ("Rischio dominante", "Dominant risk"),
    "catalyst": ("Catalizzatore", "Catalyst"),
    "invalidation": ("Invalidazione", "Invalidation"),
    "full_text": ("Segue nel dossier integrale.", "Continued in the complete dossier."),
    "price": ("Prezzo del modello", "Model reference price"),
    "base_value": ("Valore - caso base", "Value - base case"),
    "observed_price": ("Prezzo osservato", "Observed price"),
    "not_applicable": ("Non applicabile", "Not applicable"),
    "exposure_scope": ("Analisi delle esposizioni osservate. Non e una stima del valore intrinseco.",
                       "Analysis of observed exposures. It is not an intrinsic-value estimate."),
    "model_exhibits": ("Prospetti del modello consegnato", "Delivered model exhibits"),
    "model_refs": ("Riferimenti Excel", "Excel references"),
    "unbound_chart": ("Grafico escluso: i valori non sono collegati alle celle del modello consegnato.",
                      "Chart excluded: its values are not linked to the delivered model cells."),
}


def _label(key, language):
    return LABELS[key][0 if language == "it" else 1]


_SYMBOL_FONT = []


def _symbol_font():
    """Fallback face for symbols Arial lacks (check marks, arrows); registered once."""
    if not _SYMBOL_FONT:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        face = None
        for path in ("C:/Windows/Fonts/seguisym.ttf", "C:/Windows/Fonts/DejaVuSans.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                     "/Library/Fonts/Arial Unicode.ttf",
                     "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"):
            if Path(path).is_file():
                try:
                    pdfmetrics.registerFont(TTFont("BBSYM", path))
                    face = "BBSYM"
                    break
                except Exception:
                    continue
        _SYMBOL_FONT.append(face)
    return _SYMBOL_FONT[0]


def _missing_glyphs(value, font="LS"):
    """Characters a registered face cannot draw (boxes in print, lost from PDF text)."""
    from reportlab.pdfbase import pdfmetrics
    try:
        cmap = pdfmetrics.getFont(font).face.charToGlyph
    except Exception:
        return set()
    # Astral characters (emoji) draw but do not survive PDF text extraction: never printable.
    return {ch for ch in str(value) if ord(ch) > 0x7E and ch != "\u00ad"
            and (ord(ch) not in cmap or ord(ch) > 0xFFFF)}


_MD_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
# A list marker is "- " or "* " not followed by a number: "- 3,2%" is a negative figure.
_MD_BULLET = re.compile(r"^\s*[-*]\s+(?![\d.,]+\s*%)(.*)$")
_INVISIBLE = dict.fromkeys(map(ord, "\ufe0f\ufe0e\u200d"), None)


def _printable(value):
    """Characters no registered face can draw become a declared code, never a box."""
    value = str(value).translate(_INVISIBLE)
    symbol = _SYMBOL_FONT[0] if _SYMBOL_FONT else None
    missing = _missing_glyphs(value) & (_missing_glyphs(value, symbol) if symbol else set(value))
    for ch in missing:
        value = value.replace(ch, f"[U+{ord(ch):04X}]")
    return value


def _bold_face():
    from bellomberg.reporting.pdf_institutional import _register_fonts
    return _register_fonts()[1]


def _md_rules_apply(value):
    """Separator rows belong to a multi-line pipe table; a lone "---" cell is content."""
    value = str(value)
    return "\n" in value and "|" in value


def _text(value):
    """Escape for reportlab and render the light markdown models actually emit."""
    lines = []
    symbol = _symbol_font()
    value = _printable(value)
    rules = _md_rules_apply(value)
    bold = _bold_face()
    for line in str(value).split("\n"):
        if rules and _MD_RULE.match(line):
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        bullet = _MD_BULLET.match(line)
        body = heading.group(1) if heading else bullet.group(1) if bullet else line
        parts = re.split(r"\*\*(.+?)\*\*", body)
        # The body family has no registered bold variant: name the bold face explicitly.
        out = "".join(escape(part) if i % 2 == 0 else f'<font name="{bold}">' + escape(part) + "</font>"
                      for i, part in enumerate(parts))
        if symbol:
            for ch in _missing_glyphs(out) - _missing_glyphs(out, symbol):
                out = out.replace(ch, f'<font name="{symbol}">{ch}</font>')
        lines.append(f'<font name="{bold}">' + out + "</font>" if heading else "\u2022 " + out if bullet else out)
    return "<br/>".join(lines)


def _excerpt(value, limit, language):
    """A cover excerpt is visibly continued; the complete text stays in the dossier."""
    value = str(value or "n.d.")
    if len(value) <= limit:
        return value
    head = value[:limit].rsplit(" ", 1)[0]
    return head + " ... " + _label("full_text", language)


class _SectionTitle(Flowable):
    def __init__(self, title, style, width):
        super().__init__()
        self.paragraph = Paragraph(_text(title), style)
        self.width = width
        self.keepWithNext = True

    def wrap(self, available_width, available_height):
        self.width, height = self.paragraph.wrap(min(available_width, self.width), available_height)
        self.height = height + 13
        return self.width, self.height

    def draw(self):
        self.paragraph.drawOn(self.canv, 0, 13)
        self.canv.setStrokeColor(AMBER)
        self.canv.setLineWidth(1.2)
        self.canv.line(0, 5, self.width, 5)


class _DecisionCover(Flowable):
    """Measured two-column cover; long fields visibly continue in the research."""
    def __init__(self, run, result, valuations, language, styles, partial_reasons):
        super().__init__()
        self.run, self.result, self.valuations = run, result, valuations
        self.language, self.styles, self.partial_reasons = language, styles, partial_reasons
        self.width, self.height = A4

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        w, h = A4
        band = w * .34
        left, column = 28, band - 56
        right, right_width = band + 24, w - band - 54
        language, result, run = self.language, self.result, self.run
        canvas.saveState()
        canvas.setFillColor(OBSIDIAN)
        canvas.rect(0, 0, band, h, fill=1, stroke=0)
        canvas.setFillColor(AMBER)
        canvas.rect(band-2, 0, 2, h, fill=1, stroke=0)
        reg, bold = self.styles["body"].fontName, self.styles["h1"].fontName
        _draw_lockup(canvas, left, h-60, 17.5, reg, bold, AMBER, AMBER_DEEP, AMBER, tagline=False)

        def paragraph(value, x, y, width, *, size=10.4, leading=14.4, color=INK, heavy=False):
            style = ParagraphStyle("coverField", fontName=bold if heavy else reg,
                                   fontSize=size, leading=leading, textColor=color)
            item = Paragraph(_text(value), style)
            _, height = item.wrap(width, h)
            item.drawOn(canvas, x, y-height)
            return y-height

        paragraph("TRADE IDEA", left, h-120, column, size=9, color=AMBER, heavy=True)
        paragraph(_label("research", language), left, h-149, column,
                  size=20, leading=25, color=colors.HexColor("#ECF1FA"), heavy=True)
        cutoff = str(run.get("cutoff") or result.get("cutoff") or run.get("started_at") or "n.d.")
        paragraph(cutoff[:10], left, h-233, column, size=9, color=colors.HexColor("#95A1BA"))
        y = h-293
        for title, value in (("judgment", _label(result["judgment"], language)),
                ("dominant_risk", next(iter(result.get("risks") or []), _label("missing", language))),
                ("catalyst", next(iter(result.get("catalysts") or []), _label("missing", language))),
                ("invalidation", next(iter(result.get("invalidation") or []), _label("missing", language)))):
            y = paragraph(_label(title, language).upper(), left, y, column,
                          size=8, leading=11, color=AMBER, heavy=True)-8
            y = paragraph(_excerpt(value, 125, language), left, y, column,
                          size=9.6, leading=13, color=colors.HexColor("#ECF1FA"))-20
        paragraph(_label("no_order", language), left, 62, column,
                  size=7.1, leading=9.5, color=colors.HexColor("#95A1BA"))

        paragraph(_label("research", language), right, h-43, right_width, size=8, color=MUTED)
        identity = run.get("identity") or result.get("identity") or {}
        name = identity.get("name") or run.get("ticker") or result["ticker"]
        y = paragraph(_excerpt(name, 140, language), right, h-76, right_width,
                      size=25, leading=30, heavy=True)-15
        y = paragraph(" | ".join(str(v) for v in (run.get("ticker") or result["ticker"],
                      identity.get("exchange"), identity.get("currency")) if v),
                      right, y, right_width, size=9.3, leading=13, color=MUTED)-25
        y = paragraph(_label("thesis", language), right, y, right_width,
                      size=13, leading=17, heavy=True)-10
        y = paragraph(_excerpt(result["summary"], 550, language), right, y, right_width,
                      size=10.2, leading=14)-22
        model = next((v for v in self.valuations if v.get("status") == "ready"), {})
        values = (model.get("model_exhibits") or {}).get("headline_values") or {}
        fields = (("price", "price"), ("base_value", "fair_value_base"))
        card_width = (right_width-10)/2
        research_only = run.get("analysis_mode") == "fundamentals_research_v1"
        if y > 185 and not research_only:
            for index, (label, field) in enumerate(fields):
                x = right + index * (card_width+10)
                canvas.setFillColor(PALE)
                canvas.rect(x, y-95, card_width, 95, fill=1, stroke=0)
                observational = model.get("model_kind") == "exposure" or model.get("method") == "exposure_analysis"
                paragraph(_label("observed_price" if observational and field == "price" else label, language), x+10, y-10, card_width-20,
                          size=8, leading=11, color=MUTED)
                record = values.get(field) or {}
                value = record.get("value")
                formatted = f"{value:,.2f} {record.get('currency') or 'n.d.'}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "n.d."
                if observational and field == "fair_value_base":
                    formatted = _label("not_applicable", language)
                paragraph(formatted, x+10, y-32, card_width-20, size=16, leading=19, heavy=True)
                if record:
                    paragraph(str(record.get("period") or "n.d.") + " | " + str(record.get("cell") or "n.d.") +
                              " [src: common_model]", x+10, y-58, card_width-20,
                              size=6.9, leading=9, color=MUTED)
            y -= 111
        state = (("Ricerca societaria: bilanci, ipotesi e fonti" if language == "it" else
                  "Company research: financial statements, assumptions and sources") if research_only else
                 _label("model_state_ready" if model else "model_state_incomplete", language))
        y = paragraph(state, right, y, right_width, size=9, leading=12, color=MUTED)-7
        if model:
            y = paragraph(str(model.get("valuation_date") or "n.d.") + " | " +
                          str(model.get("method") or "n.d."), right, y, right_width,
                          size=8, leading=11, color=MUTED)-16
        destination = result.get("destination") or run.get("destination") or {}
        route_key = {"dcn": "dcn", "research": "research_route"}.get(destination.get("kind"), "none")
        y = paragraph(_label(route_key, language), right, y, right_width, size=10.4, heavy=True)-14
        if self.partial_reasons:
            paragraph(_label("partial", language) + ": " + _excerpt("; ".join(self.partial_reasons), 230, language),
                      right, max(y, 90), right_width, size=8.3, leading=11.5, color=MUTED)
        # Footer is constrained to the light column; it never crosses the cover band.
        paragraph("BELLOMBERG RESEARCH", right, 35, right_width, size=7, color=MUTED)
        canvas.setFillColor(MUTED)
        canvas.setFont(reg, 7)
        canvas.drawRightString(w-30, 25, "1")
        canvas.restoreState()


class _Section(Flowable):
    def __init__(self, key, title):
        Flowable.__init__(self)
        self.key, self.title = key, title
        self.width = self.height = 0
        self.keepWithNext = True

    def draw(self):
        pass


class _ResearchDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, _Section):
            self.section = flowable.title
            self.section_pages[flowable.key] = self.page
            self.canv.bookmarkPage(flowable.key)
            self.canv.addOutlineEntry(flowable.title, flowable.key, level=0)
        elif isinstance(flowable, Paragraph):
            self.header_sections.setdefault(self.page, self.section)


def _chart(section, chart, width, font):
    """Read the exact table values; ambiguous/localized cells cannot form a chart."""
    table = section["tables"][chart["table_index"]]
    columns, rows = table["columns"], table["rows"]
    indices = chart["value_columns"]
    series = []
    for column in indices:
        values = []
        for row in rows:
            raw = row[column].strip()
            if re.fullmatch(r"[+-]?\d+,\d+", raw):  # "1250,2": one comma, no dot = decimal comma
                raw = raw.replace(",", ".")
            if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", raw):
                raise ValueError("Chart cells require unambiguous dot-decimal numeric values")
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("Chart values must be finite")
            values.append(value)
        series.append(values)
    drawing = Drawing(width, 205)
    labels = [row[chart["label_column"]] for row in rows]
    if len(rows) > 16 or any(len(label) > 32 for label in labels):
        raise ValueError("Chart labels exceed readable layout; use the source table")
    palette = [OBSIDIAN, AMBER_DEEP, colors.HexColor("#707A83"), colors.HexColor("#8D4435")]
    if chart["kind"] == "bar":
        graph = VerticalBarChart()
        graph.data = series
        graph.categoryAxis.categoryNames = labels
        graph.categoryAxis.labels.fontName = font
        graph.categoryAxis.labels.fontSize = 8
        graph.categoryAxis.labels.angle = 25 if len(labels) > 6 else 0
        for index in range(len(series)):
            graph.bars[index].fillColor = palette[index]
            graph.bars[index].strokeColor = None
    else:
        graph = LinePlot()
        graph.data = [list(enumerate(values)) for values in series]
        graph.xValueAxis.valueSteps = list(range(len(labels)))
        graph.xValueAxis.labelTextFormat = lambda value: labels[int(value)] if 0 <= int(value) < len(labels) else ""
        graph.xValueAxis.labels.fontName = font
        graph.xValueAxis.labels.fontSize = 8
        for index in range(len(series)):
            graph.lines[index].strokeColor = palette[index]
            graph.lines[index].strokeWidth = 1.5
    graph.x, graph.y, graph.width, graph.height = 50, 42, width - 65, 145
    axis = graph.valueAxis if chart["kind"] == "bar" else graph.yValueAxis
    if chart["kind"] == "bar":
        axis.valueMin = min(0, min(value for values in series for value in values))
        axis.valueMax = max(0, max(value for values in series for value in values))
        if axis.valueMin == axis.valueMax:
            axis.valueMax = axis.valueMin + 1
    axis.labels.fontName, axis.labels.fontSize = font, 8
    axis.visibleGrid, axis.gridStrokeColor = True, RULE
    drawing.add(graph)
    for index, column in enumerate(indices):
        drawing.add(String(50 + index * (width - 55) / len(indices), 8, columns[column][:35],
                           fontName=font, fontSize=8, fillColor=palette[index]))
    return drawing


_EXHIBIT_TITLES = {
    "scenarios": ("Scenari di valore per azione", "Per-share valuation scenarios"),
    "valuation_bridge": ("Ponte al valore azionario", "Bridge to equity value"),
    "drivers": ("Driver operativi e flussi", "Operating drivers and cash flows"),
    "sensitivity": ("Sensibilità WACC / crescita stabile", "WACC / terminal-growth sensitivity"),
    "implicit_expectations": ("Aspettative implicite nel prezzo", "Price-implied expectations"),
    "exposure_metrics": ("Esposizione e concentrazione osservate", "Observed exposure and concentration"),
    "holdings": ("Esposizioni osservate", "Observed holdings exposures"),
    "commodity_accounting": ("NAV contabile e riconciliazioni osservate", "Observed NAV accounting and reconciliations"),
    "commodity_market_costs": ("Mercato e costi storici: date separate", "Market and historical costs: separate dates"),
    "investment_leverage": ("Leva d'investimento", "Investment leverage"),
    "future_expenses": ("Costi futuri", "Future expenses"),
}

_EXHIBIT_COLUMNS = {
    "scenarios": (("Scenario", "Valore quotato", "Valore in valuta di bilancio"),
                  ("Scenario", "Quoted value", "Value in financial currency")),
    "valuation_bridge": (("Componente", "Valore"), ("Component", "Value")),
    "implicit_expectations": (("Misura", "EBIT"), ("Measure", "EBIT")),
    "exposure_metrics": (("Misura", "Valore"), ("Measure", "Value")),
    "holdings": (("Strumento", "Peso NAV"), ("Instrument", "NAV weight")),
    "commodity_accounting": (("Misura", "Valore", "Unita"), ("Measure", "Value", "Unit")),
    "commodity_market_costs": (("Misura", "Valore", "Unita", "Periodo"), ("Measure", "Value", "Unit", "Period")),
}
_EXHIBIT_EXPLANATIONS = {
    "scenarios": ("Medesimo motore, data e versione verificata. Non sono assegnate probabilita agli scenari.",
                  "Same engine, recorded valuation date and exact generation; no scenario probabilities."),
    "drivers": ("Percorsi calcolati dal motore comune e collegati alle celle del workbook.",
                "Calculated paths from the common engine, with linked worksheet cells."),
    "valuation_bridge": ("Componenti e totali del motore comune, con celle della medesima versione verificata.",
                         "Common-engine components and totals, with cells from the same verified generation."),
    "sensitivity": ("Stessi flussi, EBIT stabile, imposte, reinvestimento e passivita. Le combinazioni invalide restano n.d.",
                    "Same cash flows, terminal EBIT, taxes, reinvestment and claims; invalid combinations are missing."),
    "implicit_expectations": ("Modello inverso condizionato al caso base e alla quotazione datata verificata. Non e consenso.",
                              "Conditional on the base model and dated usable quote; an inverse model, never consensus."),
    "exposure_metrics": ("Esposizioni riconciliate con i dati osservati della versione Excel allegata. "
                         "Un rapporto pari a uno indica il totale NAV del perimetro osservato; non e un fair value.",
                         "Exposures reconciled against the observations in the attached Excel generation. "
                         "A ratio of one denotes total NAV in the observed scope; it is not fair value."),
    "holdings": ("Frazioni con segno del NAV osservato. Nessuna normalizzazione silenziosa o probabilita di scenario.",
                 "Signed fractions of observed NAV. No silent normalization or scenario probabilities."),
    "commodity_accounting": ("Saldi contabili riportati, riconciliati nella medesima versione Excel. "
                              "Il NAV per quota e storico e non e una stima di fair value. "
                              "I pesi dell'oro e della passivita per commissioni sono frazioni contabili del NAV, non leva d'investimento.",
                              "Reported accounting balances reconciled in the same Excel generation. "
                              "Historical NAV per share is not a fair-value estimate. "
                              "Gold and accrued fee weights are accounting fractions of NAV, not investment leverage."),
    "commodity_market_costs": ("Prezzo e liquidita conservano le proprie date, distinte dal bilancio. "
                                "Il rapporto spese e un dato storico annualizzato del periodo riportato, non un costo futuro promesso. "
                                "Lo spread mediano a 30 giorni non e uno spread istantaneo. Leva e costi futuri restano n.d.",
                                "Price and liquidity retain their own dates, separate from accounting. "
                                "The expense ratio is the reported period's annualized historical observation, not a promised future cost. "
                                "The trailing 30-day median spread is not a spot spread. Investment leverage and future costs remain unavailable."),
}

_NATIVE_EXHIBIT_TEXT = {
    'Patrimonio IFRS, NTA e rettifiche di scenario': (
        ('Patrimonio IFRS, NTA e rettifiche di scenario', 'IFRS equity, NTA and scenario adjustments'),
        ('Riconto completo di attivita, passivita e patrimonio ordinario; rettifiche EPRA NTA riportate. '
         'Le variazioni di scenario sono sensibilita di ricerca dichiarate.',
         'Complete reported asset, liability and parent-equity reconciliation with reported EPRA NTA adjustments. '
         'Scenario changes are declared research sensitivities.')),
    'Riconti primari e precisione pubblicata': (
        ('Riconti primari e precisione pubblicata', 'Primary reconciliations and published precision'),
        ('Residui e limiti derivati dalla precisione pubblicata, senza rettifiche di pareggio. '
         'Gli immobili JV sono memorandum e non una seconda attivita aggiunta al NAV.',
         'Residuals and bounds derived from published precision, without balancing adjustments. '
         'JV properties are memorandum and are not added to NAV a second time.')),
    'Sensibilita NTA: valori immobiliari e costo residuo': (
        ('Sensibilita NTA: valori immobiliari e costo eccedente', 'NTA sensitivity: property values and cost overruns'),
        ('Variazioni dichiarate dei valori patrimoniali e costo di sviluppo eccedente. '
         'Non sono metriche EPRA riportate, previsioni di canoni, FFO/AFFO o compliance futura.',
         'Declared asset-value changes and development cost overruns. '
         'These are not reported EPRA metrics, rent forecasts, FFO/AFFO or future covenant compliance.')),
}

_COMPONENT_LABELS = {
    "pv_explicit_cash_flows": ("Valore attuale dei flussi espliciti", "Present value of explicit cash flows"),
    "pv_terminal_value": ("Valore attuale del valore terminale", "Present value of terminal value"),
    "enterprise_value": ("Valore d'impresa", "Enterprise value"),
    "net_debt": ("Debito netto", "Net debt"),
    "equity_adjustments": ("Rettifiche azionarie", "Equity adjustments"),
    "equity_value": ("Valore azionario", "Equity value"),
    "revenue": ("Ricavi", "Revenue"), "ebitda": ("EBITDA", "EBITDA"),
    "ebit": ("EBIT", "EBIT"), "ufcf": ("Flusso di cassa operativo libero", "Unlevered free cash flow"),
    "Periodo": ("Periodo", "Period"),
    'Misura': ('Misura', 'Measure'), 'Valore': ('Valore', 'Value'),
    "bear": ("Ribasso", "Bear"), "base": ("Base", "Base"), "bull": ("Rialzo", "Bull"),
    "EBIT normalizzato del modello": ("EBIT normalizzato del modello", "Model normalized EBIT"),
    "EBIT stabile implicito": ("EBIT stabile implicito", "Price-implied terminal EBIT"),
    "gross_exposure": ("Esposizione lorda", "Gross exposure"), "net_exposure": ("Esposizione netta", "Net exposure"),
    "leverage": ("Leva osservata", "Observed leverage"),
    "largest_absolute_weight": ("Peso assoluto della maggiore posizione", "Largest absolute position weight"),
    "squared_weight_sum": ("Somma dei pesi al quadrato", "Sum of squared weights"),
    "annual_declared_cost_ratio": ("Costi annui dichiarati / NAV", "Declared annual costs / NAV"),
    "calculated_net_assets": ("NAV riportato ricostruito", "Reconstructed reported NAV"),
    "reported_net_assets": ("NAV riportato", "Reported NAV"),
    "nav_reconciliation": ("Residuo del riconto NAV", "NAV reconciliation residual"),
    "accounting_nav_per_share": ("NAV contabile storico per quota", "Historical accounting NAV per share"),
    "gold_nav_ratio": ("Oro fisico / NAV riportato", "Physical gold / reported NAV"),
    "liability_nav_ratio": ("Commissioni maturate da pagare / NAV", "Accrued sponsor fee payable / NAV"),
    "historical_annualized_expense_ratio": ("Spese storiche annualizzate", "Historical annualized expense ratio"),
    "median_spread_ratio": ("Spread mediano a 30 giorni", "Trailing 30-day median spread"),
    "daily_volume": ("Volume giornaliero osservato", "Observed daily volume"),
    "price": ("Prezzo osservato", "Observed price"),
    'ifrs_parent_equity_reconstructed': ('Patrimonio ordinario IFRS ricostruito', 'Reconstructed IFRS parent equity'),
    'epra_nta_reconstructed': ('EPRA NTA ricostruito', 'Reconstructed EPRA NTA'),
    'adjusted_nta': ('NTA rettificato di scenario', 'Scenario adjusted NTA'),
    'development_overrun_deduction': ('Deduzione costo eccedente di sviluppo', 'Development cost overrun deduction'),
    'assets': ('Attivita', 'Assets'), 'liabilities': ('Passivita', 'Liabilities'),
    'parent_equity': ('Patrimonio ordinario', 'Parent equity'), 'property': ('Immobili consolidati', 'Consolidated property'),
    'held_for_sale': ('Attivita destinate alla vendita', 'Assets held for sale'),
    'portfolio_memorandum': ('Portafoglio: memorandum', 'Portfolio memorandum'),
    'epra_nta': ('EPRA NTA', 'EPRA NTA'), 'pipeline': ('Sviluppo impegnato', 'Committed development'),
    'calculated': ('Ricostruito', 'Reconstructed'), 'reported': ('Riportato', 'Reported'),
    'residual': ('Residuo', 'Residual'), 'rounding_bound': ('Limite di arrotondamento', 'Rounding bound'),
    'standing': ('Immobili a reddito', 'Standing properties'),
    'development': ('Immobili in sviluppo', 'Properties under development'), 'land': ('Terreni', 'Land'),
    'participations': ('Partecipazioni', 'Participations'),
    'cost_overrun_deduction': ('Deduzione costo eccedente', 'Cost overrun deduction'),
    'value_per_share': ('Valore per azione', 'Value per share'),
}


def _component_label(value, language):
    return _COMPONENT_LABELS.get(str(value), (str(value), str(value)))[0 if language == "it" else 1]


def _unit(value, language):
    if language != "it":
        return str(value)
    return {"per share": "per azione", "million": "milioni", "method-native": "unita del metodo",
            "ratio": "rapporto", "per quoted unit": "per unita quotata", "per column": "unita per colonna",
            "mixed": "unita per riga", "fraction of reported NAV": "frazione del NAV riportato",
            "annualized historical fraction": "frazione storica annualizzata", "30-day median fraction": "frazione mediana a 30 giorni",
            "quoted units": "unita quotate", "USD per entitled share": "USD per quota avente diritto",
            "USD per quoted unit": "USD per unita quotata"}.get(str(value), str(value))


def _period(value, language):
    if value == "separately_dated":
        return "date separate" if language == "it" else "separate observation dates"
    if isinstance(value, list) and len(value) > 2 and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item)) for item in value):
        return f"{value[0]} - {value[-1]} ({len(value)} " + ("periodi)" if language == "it" else "periods)")
    return str(value)


def _valuation_basis(value, language):
    """Translate canonical engine notices; preserve arbitrary accepted prose."""
    if language != "it":
        return value
    return {
        "Documented scenario valuation at opening balances/quotation cutoff; regenerate to change assumptions.":
            "Scenari documentati sulla data dei saldi iniziali e della quotazione. "
            "Per cambiare le ipotesi occorre una nuova versione del modello.",
        "Observed exposure snapshot; corporate fair value is not applicable.":
            "Esposizioni osservate alla data indicata. Il valore intrinseco aziendale non e applicabile.",
        "Finite APV; expected financing inputs acquired, no engine default-risk estimate.":
            "APV a durata finita con effetti attesi del finanziamento documentati. "
            "Il motore non aggiunge una stima autonoma del rischio di insolvenza.",
        "Opening NAV; forward FY only reconciles income/cash. Closing cash is not added to NAV.":
            "NAV iniziale. L'esercizio prospettico riconcilia reddito e cassa; "
            "la cassa finale non viene aggiunta al NAV iniziale.",
    }.get(value, value)


def _compact_refs(refs):
    """Compress only contiguous recorded cells; never fill a missing reference."""
    groups, unparsed = {}, []
    for reference in dict.fromkeys(str(cell) for row in refs for cell in row if cell):
        match = re.fullmatch(r"(.+)!([A-Z]+)(\d+)", reference)
        if not match:
            unparsed.append(reference)
            continue
        sheet, column, row = match.groups()
        index = 0
        for char in column:
            index = index * 26 + ord(char) - ord("A") + 1
        groups.setdefault((sheet, row), {})[index] = column
    compact = []
    for (sheet, row), columns in groups.items():
        indices = sorted(columns)
        start = end = indices[0]
        for index in indices[1:] + [None]:
            if index is not None and index == end + 1:
                end = index
                continue
            compact.append(f"{sheet}!{columns[start]}{row}" + (f":{columns[end]}{row}" if end != start else ""))
            if index is not None:
                start = end = index
    return ", ".join(compact + unparsed)


def _number(value, *, rate=False):
    if value is None:
        return "n.d."
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if not math.isfinite(value):
        raise ValueError("Model exhibit contains a non-finite value")
    return f"{value:.2%}" if rate else f"{value:,.4f}".rstrip("0").rstrip(".")


def _bridge_chart(exhibit, width, font, language="en"):
    """Draw native engine totals; debt is an explicitly subtracted claim."""
    rows = exhibit["rows"]
    if len(rows) > 10 or any(len(row) != 2 or not isinstance(row[1], (int, float)) for row in rows):
        raise ValueError("Bridge requires a readable, homogeneous engine decomposition")
    totals = {"enterprise_value", "equity_value"}
    negatives = {"net_debt"}
    running, levels = 0., []
    for label, value in rows:
        total = label in totals
        contribution = -value if label in negatives else value
        start = 0. if total else running
        end = value if total else running + contribution
        levels.append((str(label), start, end, total, contribution))
        running = end
    low = min(0., *(min(start, end) for _, start, end, _, _ in levels))
    high = max(0., *(max(start, end) for _, start, end, _, _ in levels))
    if high == low:
        high = low+1.
    drawing = Drawing(width, 224)
    x0, y0, plot_h = 42, 53, 130
    step = (width-x0-12)/len(levels)
    scale = plot_h/(high-low)
    zero = y0-low*scale
    drawing.add(Line(x0, zero, width-8, zero, strokeColor=RULE, strokeWidth=.8))
    drawing.add(String(2, zero-3, "0", fontName=font, fontSize=8, fillColor=MUTED))
    for index, (label, start, end, total, contribution) in enumerate(levels):
        x = x0+index*step+5
        bottom = y0+(min(start, end)-low)*scale
        height = abs(end-start)*scale
        fill = AMBER_DEEP if total else OBSIDIAN if contribution >= 0 else colors.HexColor("#8D4435")
        drawing.add(Rect(x, bottom, max(8, step-13), max(.6, height), fillColor=fill, strokeColor=None))
        displayed = end if total else contribution
        drawing.add(String(x, y0+(max(start, end)-low)*scale+9, _number(displayed),
                           fontName=font, fontSize=7.5, fillColor=INK))
        short_labels = {
            "pv_explicit_cash_flows": ("Flussi espliciti", "Explicit cash flows"),
            "pv_terminal_value": ("Valore terminale", "Terminal value"),
            "enterprise_value": ("Valore d'impresa", "Enterprise value"),
            "net_debt": ("Debito netto", "Net debt"),
            "equity_adjustments": ("Rettifiche azionarie", "Equity adjustments"),
            "equity_value": ("Valore azionario", "Equity value"),
        }
        short = short_labels.get(label, (label, label))[0 if language == "it" else 1]
        # Full component names and cell references remain in the companion table.
        parts = short.split(" ")
        for line, word in enumerate(parts[:3]):
            drawing.add(String(x, y0-13-line*9, word[:17], fontName=font, fontSize=7, fillColor=MUTED))
    return drawing


def _exhibit_story(exhibit, width, styles, language):
    """Every value comes from a validated engine/workbook pair, with cell locators."""
    p = lambda value, style="body": Paragraph(_text(value), styles[style])
    ident = exhibit.get("id")
    title = _EXHIBIT_TITLES.get(ident, (exhibit["title"], exhibit["title"]))[0 if language == "it" else 1]
    columns, source_rows, refs = exhibit["columns"], exhibit["rows"], exhibit["cell_refs"]
    if len(source_rows) != len(refs) or any(len(row) != len(columns) for row in source_rows + refs):
        raise ValueError("Model table and workbook references differ")
    if len(columns) > 5 and exhibit.get("kind") != "sensitivity":
        story = []
        parts = math.ceil((len(columns)-1)/4)
        for part, start in enumerate(range(1, len(columns), 4), 1):
            selected = [0, *range(start, min(start+4, len(columns)))]
            panel = {**exhibit, "columns": [columns[index] for index in selected],
                "rows": [[row[index] for index in selected] for row in source_rows],
                "cell_refs": [[row[index] for index in selected] for row in refs],
                "display_suffix": ("Parte " if language == "it" else "Part ") + f"{part}/{parts}"}
            for key in ("column_units", "column_currencies"):
                if exhibit.get(key):
                    panel[key] = [exhibit[key][index] for index in selected]
            story.extend(_exhibit_story(panel, width, styles, language))
        return story
    if ident == "valuation_bridge" and exhibit.get("kind") == "table":
        title = "Riconciliazioni e pretese finanziarie del metodo" if language == "it" else "Method reconciliations and financial claims"
    elif ident == "drivers" and exhibit.get("kind") == "table":
        title = "Driver economici e patrimoniali" if language == "it" else "Economic and balance-sheet drivers"
    elif ident == 'sensitivity' and exhibit.get('kind') != 'sensitivity':
        title = 'Sensibilita del metodo' if language == 'it' else 'Method sensitivity'
    native = _NATIVE_EXHIBIT_TEXT.get(exhibit.get('title'))
    if native:
        title = native[0][0 if language == 'it' else 1]
    if exhibit.get("display_suffix"):
        title += " | " + exhibit["display_suffix"]
    numeric_header = exhibit.get("kind") == "sensitivity"
    display_columns = _EXHIBIT_COLUMNS.get(ident, (columns, columns))[0 if language == "it" else 1]
    if len(display_columns) != len(columns):
        display_columns = columns
    display_columns = [_component_label(value, language) if not isinstance(value, (int, float)) else value for value in display_columns]
    units, currencies = exhibit.get("column_units") or [], exhibit.get("column_currencies") or []
    headings = []
    for index, value in enumerate(display_columns):
        heading = _number(value, rate=isinstance(value, (int, float)) and numeric_header)
        detail = [str(items[index]) if items is currencies else _unit(items[index], language)
                  for items in (currencies, units) if index < len(items) and items[index]]
        headings.append(heading + (" | " + " / ".join(detail) if detail else ""))
    rendered = [[p(value, "headcell") for value in headings]]
    for source_row, references in zip(source_rows, refs):
        row = []
        for index, (value, cell) in enumerate(zip(source_row, references)):
            displayed = _component_label(value, language) if index == 0 and not isinstance(value, (int, float)) else _number(value, rate=numeric_header and index == 0)
            if ident in ("commodity_accounting", "commodity_market_costs"):
                fraction_units = {"fraction of reported NAV": ("% del NAV riportato", "% of reported NAV"),
                                  "annualized historical fraction": ("%, dato storico annualizzato", "%, annualized historical observation"),
                                  "30-day median fraction": ("%, mediana a 30 giorni", "%, trailing 30-day median")}
                if index == 1 and source_row[2] in fraction_units:
                    displayed = _number(value, rate=True)
                elif index == 2:
                    displayed = fraction_units.get(value, (_unit(value, language),) * 2)[0 if language == "it" else 1]
                elif index == 0 and value == "net_exposure":
                    displayed = "Totale dei pesi contabili netti" if language == "it" else "Total net accounting weights"
            label = _text(displayed)
            if cell:
                label += '<br/><font size="6.9" color="#606973">' + _text(cell) + '</font>'
            row.append(Paragraph(label, styles["numeric"] if isinstance(value, (int, float)) else styles["cell"]))
        rendered.append(row)
    count = len(columns)
    widths = [width*.30] + [width*.70/(count-1)]*(count-1) if count > 2 else [width*.61, width*.39]
    table = Table(rendered, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), OBSIDIAN),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PAPER, PALE]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), .8, AMBER)]))
    context = " | ".join(str(value) for value in (_unit(exhibit.get("unit"), language), exhibit.get("currency"),
                        _period(exhibit.get("period"), language), exhibit.get("scenario")) if value is not None)
    source = "[src: common_model] | " + context
    story = [p(title, "h2"), table, Spacer(1, 6), p(source, "small")]
    explanation = _EXHIBIT_EXPLANATIONS.get(ident, (exhibit.get("explanation") or "",) * 2)[0 if language == "it" else 1]
    if native:
        explanation = native[1][0 if language == 'it' else 1]
    elif ident == 'sensitivity' and exhibit.get('kind') != 'sensitivity':
        explanation = ('Variazioni esplicite degli input del metodo, collegate alle celle della medesima versione verificata.'
                       if language == 'it' else 'Explicit method-input variations linked to cells of the same verified generation.')
    if explanation:
        story.append(p(explanation, "small"))
    kind = exhibit.get("kind")
    if kind in ("bar", "line", "waterfall"):
        try:
            if kind == "waterfall" and set(str(row[0]) for row in source_rows) <= {
                    "pv_explicit_cash_flows", "pv_terminal_value", "enterprise_value", "net_debt", "equity_adjustments", "equity_value"}:
                graph = _bridge_chart(exhibit, width, styles["body"].fontName, language)
            else:
                chart_table = {"columns": [str(value) for value in display_columns],
                               "rows": [[_component_label(value, language) if index == 0 and not isinstance(value, (int, float))
                                         else _number(value) if not isinstance(value, (int, float)) else repr(value)
                                         for index, value in enumerate(row)] for row in source_rows]}
                graph = _chart({"tables": [chart_table]}, {"table_index": 0, "kind": "bar" if kind == "waterfall" else kind,
                    "label_column": 0, "value_columns": list(range(1, min(len(columns), 5))) if kind == "line" else [1]},
                    width, styles["body"].fontName)
        except ValueError as exc:
            story.append(p(_label("missing", language) + ": " + str(exc), "small"))
        else:
            locators = _compact_refs(refs)
            story.append(KeepTogether([graph, p(title + " | " + source + " | " +
                                               _label("model_refs", language) + ": " + locators, "small")]))
    return story


def _model_gaps(report, styles, language, wanted=None):
    story = []
    for gap in report.get("unavailable_exhibits") or []:
        if wanted is not None and gap.get("id") not in wanted:
            continue
        title = _EXHIBIT_TITLES.get(gap.get("id"), (gap.get("id"), gap.get("id")))[0 if language == "it" else 1]
        not_applicable = report.get("intrinsic_value_applicable") is False
        state = _label("not_applicable", language) if not_applicable else "n.d."
        reason = _label("exposure_scope", language) if not_applicable else gap.get("reason") or _label("missing", language)
        if gap.get("id") in ("investment_leverage", "future_expenses"):
            state = "n.d."
            reasons = {"investment_leverage": ("La leva d'investimento non e provata separatamente; non si deduce dai pesi contabili.",
                                               "Investment leverage is not separately established and cannot be inferred from accounting weights."),
                       "future_expenses": ("Costi futuri e contingenti non sono stimati; il dato storico annualizzato non li sostituisce.",
                                           "Future and contingent costs are not estimated; the annualized historical observation does not replace them.")}
            reason = reasons[gap["id"]][0 if language == "it" else 1]
        if (not not_applicable and language == "it" and gap.get("id") == "implicit_expectations"
                and str(reason).startswith("Inverse model unavailable: current dated quotation is not usable;")):
            reason = ("Modello inverso non disponibile: manca una quotazione verificata per la data "
                      "e il perimetro del modello. Nessuna quotazione alternativa viene sostituita.")
        elif language == "it" and reason == "No exact common-engine/workbook exhibit for this method":
            reason = "Questo metodo non dispone di un prospetto verificato collegato al motore comune e al workbook allegato."
        story.append(Paragraph(_text(f"{title}: {state} | {reason} [src: common_model]"), styles["small"]))
    return story


def _render(path, run, result, valuations, language, partial_reasons):
    reg, bold, italic = _register_fonts()
    page_w, page_h = A4
    margin, top, bottom = 48, 66, 48
    width = page_w - 2 * margin
    styles = {
        "body": ParagraphStyle("tiBody", fontName=reg, fontSize=10.4, leading=14.4,
                               textColor=INK, spaceAfter=8, allowWidows=0, allowOrphans=0),
        "h1": ParagraphStyle("tiH1", fontName=bold, fontSize=19, leading=23, textColor=OBSIDIAN,
                             spaceBefore=17, spaceAfter=13, keepWithNext=True),
        "h2": ParagraphStyle("tiH2", fontName=bold, fontSize=11.5, leading=16,
                             textColor=NAVY, spaceBefore=10, spaceAfter=6, keepWithNext=True),
        "small": ParagraphStyle("tiSmall", fontName=reg, fontSize=8.5, leading=12,
                                textColor=MUTED, spaceAfter=6),
        "cell": ParagraphStyle("tiCell", fontName=reg, fontSize=8.6, leading=11.8, textColor=INK),
        "headcell": ParagraphStyle("tiHeadCell", fontName=bold, fontSize=8.6, leading=11.8, textColor=AMBER),
        "numeric": ParagraphStyle("tiNumeric", fontName=reg, fontSize=8.6, leading=11.8,
                                  textColor=INK, alignment=2),
        "cover": ParagraphStyle("tiCover", fontName=bold, fontSize=30, leading=36, textColor=NAVY,
                                spaceAfter=16),
    }
    identity = run.get("identity") or result.get("identity") or {}
    ticker = run.get("ticker") or result["ticker"]
    run_id = str(run.get("id") or run.get("run_id") or result.get("run_id") or "")
    cutoff = str(run.get("cutoff") or result.get("cutoff") or run.get("started_at") or "n.d.")
    doc = _ResearchDoc(str(path), pagesize=A4, leftMargin=margin, rightMargin=margin,
                       topMargin=top, bottomMargin=bottom, title=f"Trade Idea | {ticker}",
                       author="Bellomberg Research", allowSplitting=1)
    doc.section, doc.section_pages, doc.header_sections = "Trade Idea", {}, {}

    def background(canvas, document, cover=False):
        canvas.saveState()
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, page_w, page_h, fill=1, stroke=0)
        if cover:
            canvas.restoreState()
            return
        canvas.setFont(reg, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, 26, f"Cutoff {cutoff[:25]}  |  {run_id[:18]}")
        canvas.drawRightString(page_w - margin, 26, str(document.page))
        canvas.restoreState()

    def header(canvas, document):
        canvas.saveState()
        _draw_lockup(canvas, margin, page_h-33, 11.5, reg, bold, OBSIDIAN, MUTED, AMBER, tagline=False)
        canvas.setFont(reg, 7)
        canvas.setFillColor(MUTED)
        title = document.header_sections.get(document.page, document.section)
        while stringWidth(f"{ticker} | {title}", reg, 7) > width-140 and len(title) > 4:
            title = title[:-4].rstrip(" .") + "..."
        canvas.drawRightString(page_w - margin, page_h - 33, f"{ticker} | {title}")
        canvas.setStrokeColor(RULE)
        canvas.line(margin, page_h - 43, page_w - margin, page_h - 43)
        canvas.restoreState()

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[Frame(0, 0, page_w, page_h,
                                               leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)],
                     onPage=lambda c, d: background(c, d, True)),
        PageTemplate(id="body", frames=[Frame(margin, bottom, width, page_h-top-bottom,
                                              leftPadding=0, rightPadding=0)], onPage=background, onPageEnd=header),
    ])
    p = lambda value, style="body": Paragraph(_text(value), styles[style])

    def box(title, text, *, split_in_row=True):
        contents = [p(title, "h2"), p(text)]
        table = Table([[contents]], colWidths=[width], hAlign="LEFT", splitByRow=1, splitInRow=split_in_row)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE),
            ("BOX", (0, 0), (-1, -1), .6, RULE), ("LEFTPADDING", (0, 0), (-1, -1), 13),
            ("RIGHTPADDING", (0, 0), (-1, -1), 13), ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        return table

    destination = result.get("destination") or run.get("destination") or {}
    route_key = {"dcn": "dcn", "research": "research_route"}.get(destination.get("kind"), "none")
    story = [_DecisionCover(run, result, valuations, language, styles, partial_reasons),
             NextPageTemplate("body"), PageBreak()]
    # Voce 9, impianto A: la copertina e' un disegno a misura fissa, quindi il blocco delle lacune
    # apre la prima pagina di testo, prima del sommario; qui non c'e' un altro elenco: tutte le righe.
    gap_rows = _gap_rows(result.get("data_gaps"))
    if gap_rows:
        story.extend(_limits_block(
            gap_rows, _text, ParagraphStyle("tiGapHead", parent=styles["cell"], fontName=bold, textColor=OBSIDIAN),
            ParagraphStyle("tiGapItem", parent=styles["cell"], leftIndent=20, bulletIndent=0, bulletFontName=reg,
                           bulletFontSize=8.6, spaceAfter=1.5), width, language))
    story += [_Section("contents", _label("contents", language)),
              _SectionTitle(_label("contents", language), styles["h1"], width), Spacer(1, 15)]
    for index, section in enumerate(result.get("dossier", []), 1):
        story.append(Paragraph(f'<link href="#{escape(section["key"], quote=True)}" color="#050608">'
                               f'{index:02}  {_text(section["title"])}</link>', styles["body"]))
        story.append(Spacer(1, 4))
    story.extend([Spacer(1, 16), p(_label("scope", language), "small"),
                  p(f"Run {run_id} | Cutoff {cutoff}", "small"), PageBreak()])

    for section in result.get("dossier", []):
        heading = [_Section(section["key"], section["title"]),
                   _SectionTitle(section["title"], styles["h1"], width), Spacer(1, 6)]
        if section["key"] == "decision":
            # This short, fixed-label decision box must stay with its title.
            story.append(KeepTogether(heading + [box(_label("judgment", language),
                _label(result["judgment"], language) + " | " + _label(route_key, language),
                split_in_row=False), Spacer(1, 10)]))
        else:
            story.extend(heading)
        if section["key"] == "executive":
            summary = _label(result["judgment"], language) if result["summary"] in section["paragraphs"] else result["summary"]
            story.extend([box(_label("judgment", language), summary), Spacer(1, 12)])
        elif section["key"] == "pm_view":
            story.append(p(_label("pm_view", language), "h2"))
            if result["pm_view_response"] not in section["paragraphs"]:
                story.append(p(result["pm_view_response"]))
        elif section["key"] == "valuation":
            for model in valuations:
                if model.get("status") != "ready":
                    story.append(p(model.get("reason") or _label("model_missing", language), "small"))
                    continue
                values = model.get("model_values") or {}
                story.extend([p(("Modello allegato verificato" if language == "it" else "Verified attached model"), "h2"),
                              p(model.get("interpretation", "")),
                              p(f"Snapshot {model['snapshot_id']} | Generation {model['generation_id']}", "small"),
                              p(("Data di valutazione: " if language == "it" else "Valuation date: ") +
                                f"{model.get('valuation_date')} | " +
                                ("Cutoff delle informazioni: " if language == "it" else "Information cutoff: ") +
                                str(model.get('information_cutoff')), "small")])
                if values.get("valuation_basis"):
                    story.append(p(_valuation_basis(values["valuation_basis"], language), "small"))
                if model.get("model_kind") == "exposure":
                    story.append(p(_label("exposure_scope", language), "small"))
        elif section["key"] == "red_team":
            for objection in result.get("objections", []):
                story.extend([p(objection["objection"], "h2"), p(objection["response"]),
                              p(("Risolta" if objection["resolved"] else "Obiezione aperta") if language == "it"
                                else ("Resolved" if objection["resolved"] else "Open objection"), "small")])
        elif section["key"] == "decision":
            for condition in result.get("review_conditions", []):
                story.append(p(condition))
        for paragraph in section["paragraphs"]:
            story.append(p(paragraph))
        exhibit_ids = {"valuation": {"valuation_bridge", "implicit_expectations", "exposure_metrics", "commodity_accounting"},
                       "scenarios": {"scenarios", "sensitivity"},
                       "financial_quality": {"drivers", "holdings", "commodity_market_costs", "investment_leverage", "future_expenses"}}.get(section["key"], set())
        for model in valuations:
            if model.get("status") != "ready":
                continue
            model_report = model.get("model_exhibits") or {}
            for exhibit in model_report.get("exhibits") or []:
                if exhibit.get("id") in exhibit_ids:
                    story.extend(_exhibit_story(exhibit, width, styles, language))
            story.extend(_model_gaps(model_report, styles, language, exhibit_ids))
        for index, table in enumerate(section.get("tables", [])):
            if section["key"] in ("valuation", "scenarios") and any(model.get("status") == "ready" for model in valuations):
                story.append(p(_label("unbound_chart", language), "small"))
                continue
            story.append(p(table["title"], "h2"))
            headers = [p(value, "headcell") for value in table["columns"]]
            rows = [headers]
            for row in table["rows"]:
                rows.append([p(value, "numeric" if re.fullmatch(r"[+\-\d., %]+", value) else "cell") for value in row])
            count = len(headers)
            widths = [width * .34] + [width * .66 / (count-1)] * (count-1)
            rendered = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
            rendered.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), OBSIDIAN),
                ("TEXTCOLOR", (0, 0), (-1, 0), AMBER),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PAPER, PALE]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (0, 0), (-1, 0), .8, AMBER)]))
            story.extend([rendered, Spacer(1, 5), p(f"{table['unit']} | {table['period']} | {table['source']}", "small")])
            for chart in section.get("charts", []):
                if chart["table_index"] == index:
                    # Invalid graph notation is explicit; the original table remains intact.
                    try:
                        drawing = _chart(section, chart, width, reg)
                    except ValueError as exc:
                        story.append(p(_label("missing", language) + ": " + str(exc), "small"))
                    else:
                        story.append(KeepTogether([drawing,
                            p(f"{table['title']} | {table['unit']} | {table['period']} | {table['source']}", "small")]))

    story.extend([PageBreak(), _Section("sources", _label("sources", language)),
                  _SectionTitle(_label("sources", language), styles["h1"], width),
                  p(_label("scope", language)), p(_label("pm_view", language), "h2"),
                  p(result.get("pm_view") or run.get("pm_view") or _label("no_view", language))])
    # User text, model receipts and bibliography are excluded from analytical page counts.
    if destination.get("reason"):
        story.extend([p(_label(route_key, language), "h2"), p(destination["reason"], "small")])
    if valuations:
        story.append(p(_label("model_ready" if any(row.get("status") == "ready" for row in valuations)
                              else "model_missing", language), "h2"))
        for row in valuations:
            row_reason = (_label("model_state_ready", language) if row.get("status") == "ready" else row.get("reason", ""))
            story.append(p(f"{row.get('ticker', ticker)} | {row_reason}", "small"))
            if row.get("status") == "ready":
                story.append(p(f"Snapshot {row.get('snapshot_id')}\nGeneration {row.get('generation_id')}\nSHA256 {row.get('workbook_sha256')}", "small"))
    elif run.get("analysis_mode") != "fundamentals_research_v1":
        story.append(p(_label("model_missing", language), "h2"))
    for evidence in result.get("evidence", []):
        story.extend([p(evidence["id"] + " | " + evidence["source"], "h2"),
                      p(evidence["as_of"] + " | " + evidence["summary"], "small")])
        if evidence.get("url"):
            url = escape(evidence["url"], quote=True)
            story.append(Paragraph(f'<link href="{url}" color="#172B46">{url}</link>', styles["small"]))
    story.append(p(_label("models", language), "h2"))
    models = run.get("models") or result.get("models") or {}
    if isinstance(models, dict):
        for role, model in models.items():
            story.append(p(f"{role}: {model}", "small"))
    else:
        for model in models:
            story.append(p(str(model), "small"))
    doc.build(story)
    return doc.section_pages


def _memo_styles():
    """The weekly note's typography and palette, with flowing research content."""
    reg, bold, italic = _register_fonts()
    return {
        "body": ParagraphStyle("memoBody", fontName=reg, fontSize=9.4, leading=13.7,
            textColor=INK, spaceAfter=7, allowWidows=0, allowOrphans=0),
        "h1": ParagraphStyle("memoH1", fontName=bold, fontSize=12.5, leading=16,
            textColor=OBSIDIAN, spaceBefore=12, spaceAfter=2, keepWithNext=True),
        "h2": ParagraphStyle("memoH2", fontName=bold, fontSize=9.8, leading=13,
            textColor=OBSIDIAN, spaceBefore=9, spaceAfter=4, keepWithNext=True),
        "small": ParagraphStyle("memoSmall", fontName=reg, fontSize=7.4, leading=10.3,
            textColor=MUTED, spaceAfter=6),
        "cell": ParagraphStyle("memoCell", fontName=reg, fontSize=8.4, leading=11.3, textColor=INK),
        "headcell": ParagraphStyle("memoHead", fontName=bold, fontSize=7.8, leading=10.6, textColor=AMBER),
        "italic": ParagraphStyle("memoItalic", fontName=italic, fontSize=8, leading=11, textColor=MUTED),
    }


# ---------------------------------------------------------------------------
# Impianto M (scelta PM 04/10/2026): memo d'investimento a sezioni numerate per il comitato.
# Il testo del Capo e' mostrato con UNA trasformazione deterministica e dichiarata,
# applicata identica dal renderer e dal controllo d'integrita':
#  - [src: x] / [evidence: id] -> numero della fonte (elenco numerato in fondo);
#  - impronte esadecimali e backtick tolti; gergo tecnico reso in lingua;
#  - nelle celle delle tabelle del Capo (punto decimale per contratto /2-/3)
#    i numeri vanno all'italiana.
# I numeri di pagina 1-2 e dei grafici NON vengono dal testo del modello: li legge
# Python dalle ricevute dei tool (run["facts"], trade_idea_facts.extract_facts).

_CITE = re.compile(r"\[\s*(src|evidence|fonte)\s*:\s*([^\]]*)\]", re.I)
_CITE_DATE = re.compile(r"^\s*(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}\S*)\s*$")
_HEX = r"(?=[0-9a-f]*[a-f])[0-9a-f]"
_HASH = re.compile(r"(?:\b(?:dossier|tesi|thesis|impronta|fingerprint|sha-?256|hash)\s+)?"
                   r"\b(?:" + _HEX + r"{32,}\b|" + _HEX + r"{8,}(?:\.\.\.|…))", re.I)
# Inline code: a backtick pair whose opening is not glued to a word ("in `research_required`,").
_CODE = re.compile(r"(?<![\w`])`+([^`\n]+?)`+(?![\w`])")
# Left-over backticks used as apostrophes or accents: "l`azienda", "e` solida", "perche`".
_BACKTICK_APOSTROPHE = re.compile(r"(?<=[A-Za-zÀ-ÿ])`(?=[A-Za-zÀ-ÿ])|(?<=[aeiouAEIOU])`(?=[\s.,;:!?)]|$)")
_CELL_NUMBER = re.compile(r"(?<![\w.,])(-?)(\d+)(?:\.(\d+))?(?![.,]?\d)(?![A-Za-z]|\.[A-Za-z])")
# A dot number in Italian prose: 1-2 or 4+ decimals can never be a thousands group;
# exactly 3 decimals is ambiguous and is converted only when it IS a tool value read by
# Python (or its integer part is zero). Tokens of a URL are never touched.
_PROSE_NUMBER = re.compile(r"(?<![\w.,/])(-?)(\d+)\.(\d+)(?![.,]?\d)")
_JARGON = {
    "it": [(re.compile(r"\bproposal\s*=\s*null\b", re.I), "nessuna proposta operativa"),
           (re.compile(r"\bWATCH\b(?=\s*(?:[,.;:)]|$))", re.M), "OSSERVARE"),
           (re.compile(r"\bneeds_verification\b"), "da verificare"),
           (re.compile(r"\bresearch_required\b"), "ricerca richiesta"),
           (re.compile(r"\bObjection(?=\s+[A-Z0-9][A-Z0-9_.-]*\b)"), "Obiezione"),
           (re.compile(r"\bAnswer\s*:\s*conceded\b"), "Risposta: concessa"),
           (re.compile(r"\bAnswer\s*:"), "Risposta:"),
           (re.compile(r"\bStatus\s*:\s*conceded\b"), "Stato: concessa"),
           (re.compile(r"\bStatus\s*:\s*answered\b"), "Stato: risposta"),
           (re.compile(r"\bStatus\s*:\s*open\b"), "Stato: aperta"),
           (re.compile(r"\bStatus\s*:"), "Stato:")],
    "en": [(re.compile(r"\bproposal\s*=\s*null\b", re.I), "no actionable proposal"),
           (re.compile(r"\bneeds_verification\b"), "to be verified"),
           (re.compile(r"\bresearch_required\b"), "research required")],
}

# Etichette leggibili delle fonti piu' frequenti; un tool non censito si mostra col suo nome.
_TOOL_LABELS = {
    "get_financial_history": ("Bilanci storici (XBRL/ESEF)", "Historical financial statements (XBRL/ESEF)"),
    "get_fundamentals": ("Fondamentali e multipli di mercato", "Market fundamentals and multiples"),
    "get_consensus_estimates": ("Consensus degli analisti", "Analyst consensus"),
    "get_price_live": ("Prezzo e indicatori tecnici giornalieri", "Daily price and technical indicators"),
    "quant_compute": ("Calcoli quantitativi (rischio, correlazioni)", "Quantitative computations (risk, correlations)"),
    "compare_assets": ("Confronto di rendimento e volatilita'", "Return and volatility comparison"),
    "get_portfolio_live": ("Portafoglio reale (NAV e cassa)", "Live portfolio (NAV and cash)"),
    "get_portfolio_risk": ("Rischio del portafoglio", "Portfolio risk"),
    "get_sector_exposure": ("Esposizione settoriale del portafoglio", "Portfolio sector exposure"),
    "get_macro_dashboard": ("Quadro macro (tassi, cambi, materie prime)", "Macro dashboard (rates, FX, commodities)"),
    "get_yield_curves": ("Curve dei rendimenti", "Yield curves"),
    "tavily_search": ("Ricerca web (fonte secondaria)", "Web search (secondary source)"),
    "search_news": ("Notizie (fonte secondaria)", "News (secondary source)"),
    "read_company_dossier": ("Dossier documentale della societa'", "Company document dossier"),
    "search_company_sources": ("Fonti ufficiali dell'emittente (navigazione)", "Issuer official sources (navigation)"),
    "open_company_source": ("Documento dell'emittente", "Issuer document"),
    "get_guidance": ("Registro guidance", "Guidance register"),
    "get_earnings_calendar": ("Calendario risultati", "Earnings calendar"),
    "get_portfolio_montecarlo": ("Simulazione Monte Carlo del portafoglio", "Portfolio Monte Carlo simulation"),
    "get_var_backtest": ("Backtest del VaR", "VaR backtest"),
    "get_vix_term_structure": ("Struttura a termine del VIX", "VIX term structure"),
    "get_position_doctor": ("Diagnosi tecnica della posizione", "Position technical diagnosis"),
    "get_insider_trades": ("Operazioni degli insider", "Insider transactions"),
    "get_corporate_events_for_ticker": ("Eventi societari", "Corporate events"),
    "get_hyperliquid_intel": ("Mercati Hyperliquid", "Hyperliquid markets"),
    "get_advanced_metrics": ("Metriche avanzate del portafoglio", "Advanced portfolio metrics"),
    "get_portfolio_factors": ("Fattori di rischio del portafoglio", "Portfolio risk factors"),
    "get_portfolio_garch": ("Volatilita' GARCH del portafoglio", "Portfolio GARCH volatility"),
    "get_tearsheet": ("Scheda di performance del portafoglio", "Portfolio tearsheet"),
    "get_edge_scan": ("Scansione dei segnali", "Signal scan"),
    "get_cot_positioning": ("Posizionamento COT (CFTC)", "COT positioning (CFTC)"),
    "get_polymarket_events": ("Mercati predittivi Polymarket", "Polymarket prediction markets"),
    "get_macro_news_by_topic": ("Notizie macro per tema", "Macro news by topic"),
    "get_news_briefing": ("Rassegna notizie", "News briefing"),
    "get_filing_changes": ("Variazioni nei documenti depositati", "Filing changes"),
    "get_lobbying": ("Attivita' di lobbying", "Lobbying activity"),
    "get_gov_contracts": ("Contratti pubblici", "Government contracts"),
    "get_dat_metrics": ("Metriche delle tesorerie in cripto", "Digital asset treasury metrics"),
    "get_vol_surface_summary": ("Superficie di volatilita' implicita", "Implied volatility surface"),
    "get_options_data": ("Dati opzioni", "Options data"),
    "get_option_expirations_polygon": ("Scadenze opzioni (Polygon)", "Option expirations (Polygon)"),
    "acquire_company_source": ("Acquisizione di documento dell'emittente", "Issuer document acquisition"),
    "read_blackboard": ("Lavagna condivisa dei desk", "Shared desk blackboard"),
    "ask_specialist": ("Consultazione fra desk", "Desk consultation"),
}


_TOOL_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def _cite_items(kind, body):
    """Keys of one citation. A tool-like name is a source; dates and qualifiers after a
    comma belong to the tool before them; a descriptive citation with no tool
    ("fatti verificati") is a source in its own words."""
    keys, dates = [], []
    for item in re.split(r"[,;]", body):
        item = re.sub(r"\s+", " ", item.strip())
        if not item:
            continue
        if kind.lower() == "evidence":
            keys.append("evidence:" + item)
            continue
        if _CITE_DATE.match(item) and keys:
            dates.append((keys[-1], item))
            continue
        first = item.split()[0].strip(".:")
        if _TOOL_NAME.match(first):
            keys.append(first)
            rest = item[len(item.split()[0]):].strip()
            if rest:  # "open_company_source 10-K p. 47": the page reference stays with its source
                dates.append((first, rest))
        elif not keys:
            keys.append(item[:160])
        else:
            dates.append((keys[-1], item))  # a qualifier after a comma belongs to the source before it
    return keys, dates


def _known_values(value, out=None):
    """Every finite number in the facts tree, for the 3-decimal prose rule."""
    out = set() if out is None else out
    if isinstance(value, dict):
        for item in value.values():
            _known_values(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _known_values(item, out)
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        # 2.5 or 2500 must never turn "2.500 dipendenti" into 2,5: only true 3-decimal values count.
        if abs(float(value) * 100 - round(float(value) * 100)) > 1e-6:
            out.add(round(float(value), 6))
    return out


class _Sources:
    """One numbering shared by renderer and integrity check (first appearance order)."""
    def __init__(self, result, annex, language, facts=None):
        self.language = language
        self.known = _known_values(facts or {})
        self.order, self.dates = [], {}
        self.evidence = {str(row.get("id")): row for row in result.get("evidence") or []}
        for _, value in _content_fragments(result):
            self.scan(value)
        for _, _, text in _annex_blocks(annex, language):
            self.scan(text)
        for key in self.evidence:
            self.add("evidence:" + key)

    def add(self, key):
        if key not in self.order:
            self.order.append(key)
        return self.order.index(key) + 1

    def scan(self, value):
        for kind, body in _CITE.findall(str(value or "")):
            self.numbers(kind, body)

    def numbers(self, kind, body):
        keys, dates = _cite_items(kind, body)
        found = sorted({self.add(key) for key in keys})
        for key, date in dates:
            if key and date not in self.dates.setdefault(key, []):
                self.dates[key].append(date)
        return found

    def label(self, key):
        if key.startswith("evidence:"):
            row = self.evidence.get(key[9:])
            if not row:
                return key[9:]
            # Evidence.source is "[src: tool] description" by contract: show the tool by name.
            source = str(row.get("source") or "")
            tools = [k for kind, body in _CITE.findall(source) for k in _cite_items(kind, body)[0]]
            rest = re.sub(r"\s+", " ", _CITE.sub(" ", source)).strip()
            return "; ".join([*(self.label(t) for t in tools), *([rest] if rest else [])]) or key[9:]
        pair = _TOOL_LABELS.get(key)
        return pair[0 if self.language == "it" else 1] if pair else key


def _localize_cell(value, language):
    """Contract /2-/3 cells use dot decimals: print them in the reader's locale."""
    if language != "it":
        return value

    def swap(match):
        sign, whole, decimals = match.groups()
        if decimals is None and len(whole) == 4 and 1900 <= int(whole) <= 2100:
            return match.group(0)  # a year, not a quantity
        if len(whole) > 1 and whole.startswith("0") and decimals is None:
            return match.group(0)  # a code (0700, CUSIP), not a quantity
        grouped = f"{int(whole):,}".replace(",", ".") if len(whole) > 3 else whole
        return sign + grouped + ("," + decimals if decimals is not None else "")
    return _CELL_NUMBER.sub(swap, value)


def _localize_prose(value, known):
    def swap(match):
        sign, whole, decimals = match.groups()
        start, end = match.span()
        token = value[value.rfind(" ", 0, start) + 1:value.find(" ", end) if value.find(" ", end) >= 0 else len(value)]
        if "://" in token or "www." in token or "@" in token:
            return match.group(0)
        before = value[max(0, start - 14):start].lower()
        if re.search(r"(?:\bnota|\bnote|\bitem|\bcap\.?|\bcapitolo|\bsezione|\bsection|\bore|\bh|\bversione|"
                     r"\bversion|\bv|§|\bart\.?|\barticolo|\bparagrafo|\bpar\.?)\s*$", before):
            return match.group(0)
        number = float(whole + "." + decimals)
        if len(decimals) == 3 and whole != "0" and round(number, 6) not in known and round(-number, 6) not in known:
            return match.group(0)
        return sign + whole + "," + decimals
    return _PROSE_NUMBER.sub(swap, value)


def _clean(value, sources, language, *, cell=False, plain=False, localize_cells=True):
    """The single display transform. plain=True gives the text a PDF reader extracts.
    localize_cells=False (policy /4): cells already arrive in the reader's convention
    and are printed as written, neither swapped as cells nor as prose."""
    value = str(value or "")

    def cite(match):
        # Impianto M (PM 04/10/2026): no superscripts; the source is registered for the
        # section's "Fonti" line and the numbered list in Annex B, and leaves the prose.
        sources.numbers(match.group(1), match.group(2))
        return " "
    value = _CITE.sub(cite, value)
    value = _HASH.sub("", value)
    value = _CODE.sub(r"\1", value)
    value = _BACKTICK_APOSTROPHE.sub("'", value)
    for pattern, replacement in _JARGON.get(language, ()):
        value = pattern.sub(replacement, value)
    if cell:
        if localize_cells:
            value = _localize_cell(value, language)
    elif language == "it":
        value = _localize_prose(value, sources.known)
    value = re.sub(r"\(\s*[,;]?\s*(?:[,;]\s*)*\)", "", value)
    value = re.sub(r"[ \t]+([,.;:])", r"\1", value)
    return re.sub(r"[ \t]{2,}", " ", value).strip()


def _shown(value, sources, language, *, cell=False, localize_cells=True):
    """Reportlab markup of the display transform."""
    return _text(_clean(value, sources, language, cell=cell, localize_cells=localize_cells))


# Voce 9, impianto A (05/10/2026): le lacune dichiarate (data_gaps) si leggono in prima pagina.
# Il Capo spesso le numera da se' («1. Semestrale ...»): accanto alla lettera (a) diventava
# «(a) 1. Semestrale». La numerazione si toglie qui, UNA trasformazione usata dal renderer
# e dall'ispettore d'integrita' (che altrimenti cercherebbe «1.» nel PDF e non lo troverebbe).
_GAP_NUMBER = re.compile(r"^\s*(\d{1,2})[.)]\s+")
_GAPS_FIRST_PAGE = 15  # oltre, le prime 15 + una riga dichiarata che rinvia all'elenco completo
_GAPS_FIRST_PAGE_CHARS = 2000  # e non oltre ~2.000 caratteri: «1. Raccomandazione» resta a pagina 1


def _gap_texts(values):
    """Le lacune come le stampa il memo. La numerazione iniziale («1. », «2) ») si toglie SOLO se le
    voci numerate lo sono in sequenza 1, 2, 3...: «12. dicembre 2025: ...» o «3) trimestre» da soli
    sono testo e restano interi."""
    texts = [str(value if value is not None else "") for value in values or []]
    numbers = [int(m.group(1)) for m in map(_GAP_NUMBER.match, texts) if m]
    if numbers and numbers == list(range(1, len(numbers) + 1)):
        texts = [_GAP_NUMBER.sub("", text, count=1) for text in texts]
    return [text.strip() for text in texts]


def _gap_rows(values):
    """(testo, volte) in ordine di prima comparsa: la stessa frase ripetuta si accorpa e si
    dichiara con «(×n)», non si perde."""
    rows = {}
    for text in _gap_texts(values):
        rows[text] = rows.get(text, 0) + 1
    return list(rows.items())


def _gaps_on_first_page(rows):
    """Quante righe entrano nel blocco di prima pagina: al massimo 15 e ~2.000 caratteri (almeno una)."""
    count = chars = 0
    for text, _ in rows:
        if count == _GAPS_FIRST_PAGE or (count and chars + len(text) > _GAPS_FIRST_PAGE_CHARS):
            break
        count, chars = count + 1, chars + len(text)
    return count


def _gap_items(rows, show, style, language="it"):
    """Paragrafi (a), (b)... delle lacune, con «(×n)» per le ripetute; una voce vuota e' DICHIARATA."""
    empty = _text("(lacuna senza testo nel risultato)" if language == "it" else "(gap with no text in the result)")
    return [Paragraph((show(text) if text else empty) + (_text(f" (×{count})") if count > 1 else ""), style,
                      bulletText=f"({chr(97 + i) if i < 26 else i + 1})")
            for i, (text, count) in enumerate(rows)]


def _limits_block(rows, show, head_style, item_style, width, language, *, limit=None, overflow_ref=None,
                  pipeline=0, pipeline_ref=None):
    """Blocco «LIMITI DI QUESTA ANALISI — N lacune dichiarate» (impianto A): filetti navy, niente
    fondo colorato. ``limit``: righe mostrate; le altre sono DICHIARATE con il rinvio all'elenco
    completo (mai un taglio silenzioso). ``pipeline``: lacune della pipeline dati, solo contate qui."""
    it = language == "it"
    total = sum(count for _, count in rows)
    head = ("LIMITI DI QUESTA ANALISI" if it else "LIMITS OF THIS ANALYSIS") + f" — {len(rows)} " + (
        ("lacuna dichiarata" if len(rows) == 1 else "lacune dichiarate") if it else
        ("declared gap" if len(rows) == 1 else "declared gaps"))
    if total != len(rows):
        head += (f" ({total} voci; le ripetute sono accorpate e contate con ×n)" if it else
                 f" ({total} entries; repeats are merged and counted with ×n)")
    shown = rows if limit is None else rows[:limit]
    cells = [[Paragraph(_text(head), head_style)]]
    cells += [[item] for item in _gap_items(shown, show, item_style, language)]
    rest = len(rows) - len(shown)
    if rest:
        cells.append([Paragraph(_text(((f"… e altre {rest} lacune" if rest > 1 else "… e un'altra lacuna") + f": elenco completo in §{overflow_ref}." if it else
                                       f"… and {rest} more gaps: full list in §{overflow_ref}.")), item_style)])
    if pipeline:
        cells.append([Paragraph(_text((f"+ {pipeline} " + ("lacune" if pipeline > 1 else "lacuna") + " della pipeline dati (dati che la run non ha "
                                       f"procurato): §{pipeline_ref}." if it else
                                       f"+ {pipeline} data-pipeline gaps (data the run did not obtain): "
                                       f"§{pipeline_ref}.")), item_style)])
    box = Table(cells, colWidths=[width])
    box.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), .8, _M_NAVY), ("LINEBELOW", (0, 0), (-1, 0), .3, _M_RULE),
                             ("LINEBELOW", (0, -1), (-1, -1), .8, _M_NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, 0), 4)]))
    return [Spacer(1, 10), box]


def _fmt(value, decimals=0, language="it", *, suffix="", scale=1.0):
    """A missing or non-finite value prints n.d.; never a substitute number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "n.d."
    from decimal import Decimal, ROUND_HALF_UP
    # Commercial rounding of the printed decimal (1.565 -> 1,57), not of its binary float.
    exact = Decimal(repr(value)) / Decimal(repr(scale))
    rounded = exact.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if rounded == 0 and exact != 0:
        # A non-zero value never prints as 0: three significant digits instead (0.0012 -> 0,00120).
        decimals = max(decimals, -exact.adjusted() + 2)
        rounded = exact.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    rendered = f"{rounded:,.{decimals}f}"
    if language == "it":
        rendered = rendered.translate(str.maketrans(",.", ".,"))
    return rendered + suffix


def _card_rows(facts, language):
    it = language == "it"
    quote, fund = facts.get("quote") or {}, facts.get("fundamentals") or {}
    cons, risk = facts.get("consensus") or {}, (facts.get("risk") or {}).get("candidate") or {}
    cur = facts.get("currency") or ""
    rec = cons.get("recommendations") or {}
    # One denominator: the recommendation classes themselves (target counts are another sample).
    classes = [rec.get(k) for k in ("strong_buy", "buy", "hold", "sell", "strong_sell")]
    known = [v for v in classes if isinstance(v, (int, float)) and not isinstance(v, bool)]
    positive = (classes[0] + classes[1] if all(isinstance(v, (int, float)) for v in classes[:2])
                and len(known) == 5 else None)
    rec_total = sum(known) if len(known) == 5 else None
    date = str(quote.get("date") or "")[:10]
    return [
        (("Prezzo" if it else "Price") + (f" ({date})" if date else ""), _fmt(quote.get("price"), 2, language, suffix=" " + cur)),
        ("Target medio consensus" if it else "Mean consensus target", _fmt(cons.get("target_mean"), 2, language, suffix=" " + cur)),
        ("Upside implicito" if it else "Implied upside", _fmt(cons.get("upside_pct"), 1, language, suffix="%")),
        ("Range target (min-max)" if it else "Target range (min-max)",
         _fmt(cons.get("target_low"), 0, language) + " - " + _fmt(cons.get("target_high"), 0, language)
         + (" " + cur if cur else "")),
        ("Raccomandazioni positive/totale" if it else "Positive/total recommendations",
         f"{_fmt(positive, 0, language)}/{_fmt(rec_total, 0, language)}" if positive is not None and rec_total
         else "n.d."),
        ("Capitalizzazione" if it else "Market cap", _fmt(fund.get("market_cap"), 2, language, scale=1e9,
         suffix=(" mld " if it else " bn ") + (fund.get("currency") or ("(valuta non dichiarata)" if it
                                                                        else "(currency not stated)")))),
        ("EV/EBITDA", _fmt(fund.get("ev_to_ebitda"), 1, language, suffix="x")),
        ("P/E storico / atteso" if it else "P/E trailing / forward",
         _fmt(fund.get("pe_trailing"), 1, language, suffix="x") + " / " + _fmt(fund.get("pe_forward"), 1, language, suffix="x")),
        ("P/B · dividend yield", _fmt(fund.get("price_to_book"), 1, language, suffix="x") + " · "
         + _fmt(fund.get("dividend_yield"), 2, language, suffix="%")),
        ("Range 52 settimane" if it else "52-week range",
         _fmt(quote.get("low_52w"), 2, language) + " - " + _fmt(quote.get("high_52w"), 2, language)),
        ("Volatilità 1a · beta" if it else "1y volatility · beta",
         _fmt(risk.get("vol_pct"), 1, language, suffix="%") + " · " + _fmt(risk.get("beta"), 2, language)),
    ]


def _period(period, language):
    """Provider period codes (0y, +1q) in words, shared with the charts."""
    from bellomberg.reporting.trade_idea_charts import period_label
    return period_label(str(period), language)


def _annual(period):
    """Quarterly consensus rows (0q, +1q) belong to the detailed table, not to page one."""
    return "q" not in str(period).lower()


def _consensus_rows(facts, language, *, annual_only=False):
    it = language == "it"
    cons = facts.get("consensus") or {}
    cur = facts.get("currency") or ""
    rows = [("Target price " + cur, _fmt(cons.get("target_mean"), 2, language) + (" medio · " if it else " mean · ")
             + _fmt(cons.get("target_median"), 2, language) + (" mediano" if it else " median"))]
    for item in cons.get("eps") or []:
        if annual_only and not _annual(item.get("period")):
            continue
        rows.append((f"EPS {_period(item.get('period'), language)}", _fmt(item.get("value"), 2, language)
                     + (f" ({item['analysts']} {'analisti' if it else 'analysts'})" if item.get("analysts") else "")))
    for item in cons.get("revenue") or []:
        if annual_only and not _annual(item.get("period")):
            continue
        rows.append(((f"Ricavi {_period(item.get('period'), language)} (mld)" if it else
                      f"Revenue {_period(item.get('period'), language)} (bn)"),
                     _fmt(item.get("value"), 2, language, scale=1e9)
                     + (f" ({item['analysts']} {'analisti' if it else 'analysts'})" if item.get("analysts") else "")))
    return rows


_M_NAVY = colors.HexColor("#1F3864")
_M_GREY = colors.HexColor("#595959")
_M_RULE = colors.HexColor("#BFC5D2")

_M_TABLE = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (0, 0), (-1, 0), .8, _M_NAVY),
            ("LINEBELOW", (0, 0), (-1, 0), .6, _M_NAVY), ("LINEBELOW", (0, 1), (-1, -1), .3, _M_RULE),
            ("LINEBELOW", (0, -1), (-1, -1), .8, _M_NAVY),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]

_M_RUNNING = ("Memo d'investimento | ", "Investment memo | ")
_M_FOOTER = ("Bellomberg | Documento interno riservato al Comitato d'investimento",
             "Bellomberg | Internal document reserved to the Investment Committee")


_SERIF = []


def _serif_faces():
    """Georgia for the memo prose (PM 04/10/2026, chosen over Arial and Cambria on a real memo).
    Where Georgia is not installed the prose stays in the sans face, with a warning."""
    if not _SERIF:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        found = None
        for folder in ("C:/Windows/Fonts/", "/System/Library/Fonts/Supplemental/", "/Library/Fonts/",
                       "/usr/share/fonts/truetype/msttcorefonts/"):
            names = (("georgia.ttf", "georgiab.ttf", "georgiai.ttf"),
                     ("Georgia.ttf", "Georgia Bold.ttf", "Georgia Italic.ttf"))
            for regular, bold, italic in names:
                if all(Path(folder + n).is_file() for n in (regular, bold, italic)):
                    try:
                        for alias, name in (("BBSerif", regular), ("BBSerifB", bold), ("BBSerifI", italic)):
                            pdfmetrics.registerFont(TTFont(alias, folder + name))
                        found = ("BBSerif", "BBSerifB", "BBSerifI")
                    except Exception:
                        found = None
                    break
            if found:
                break
        if not found:
            import sys
            print("[trade_idea_report] Georgia non disponibile: testo del memo in carattere sans.", file=sys.stderr)
        _SERIF.append(found)
    return _SERIF[0]


def _m_styles(styles):
    """Impianto M (PM 04/10/2026): one column, Georgia prose, Arial tables and headings, navy only."""
    reg, bold = styles["body"].fontName, styles["h1"].fontName
    italic = styles["italic"].fontName
    serif, serif_bold, serif_italic = _serif_faces() or (reg, bold, italic)
    make = lambda name, **kw: ParagraphStyle(name, **{"fontName": reg, "fontSize": 10, "leading": 14,
                                                      "textColor": INK, **kw})
    prose = lambda name, **kw: make(name, **{"fontName": serif, "fontSize": 10.5, "leading": 15.2, **kw})
    return {
        "serif_bold": serif_bold,
        "title": make("mTitle", fontName=bold, fontSize=16, leading=20, textColor=_M_NAVY, spaceAfter=8),
        "metak": make("mMetaK", fontName=bold, fontSize=9.5, leading=13),
        "metav": make("mMetaV", fontSize=9.5, leading=13),
        "h1": make("mH1", fontName=bold, fontSize=13, leading=17, textColor=_M_NAVY, spaceBefore=16,
                   spaceAfter=6, keepWithNext=True),
        "h2": make("mH2", fontName=bold, fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=3, keepWithNext=True),
        "body": prose("mBody", spaceAfter=6, allowWidows=0, allowOrphans=0),
        "num": prose("mNum", leftIndent=30, bulletIndent=0, bulletFontName=reg, bulletFontSize=9.5,
                     spaceAfter=6, allowWidows=0, allowOrphans=0),
        "item": prose("mItem", leftIndent=22, bulletIndent=4, bulletFontName=reg, bulletFontSize=9.5, spaceAfter=3),
        "quote": prose("mQuote", fontName=serif_italic, leftIndent=22, rightIndent=22, textColor=_M_GREY, spaceAfter=6),
        "cell": make("mCell", fontSize=9, leading=12),
        "cellb": make("mCellB", fontName=bold, fontSize=9, leading=12),
        "cellr": make("mCellR", fontSize=9, leading=12, alignment=2),
        "head": make("mHead", fontName=bold, fontSize=9, leading=12, textColor=_M_NAVY),
        "headr": make("mHeadR", fontName=bold, fontSize=9, leading=12, textColor=_M_NAVY, alignment=2),
        "caption": make("mCaption", fontName=bold, fontSize=9.5, leading=13, spaceBefore=8, spaceAfter=1,
                        keepWithNext=True),
        "unit": make("mUnit", fontSize=8, leading=10.5, textColor=_M_GREY, spaceAfter=3, keepWithNext=True),
        "note": make("mNote", fontSize=8, leading=10.5, textColor=_M_GREY, spaceAfter=4),
        "reading": prose("mReading", fontSize=9.5, leading=13.5, spaceAfter=8),
        "small": make("mSmall", fontSize=8, leading=10.5, textColor=_M_GREY, spaceAfter=3),
        # Annex prose keeps the legacy names used by _rich_flowables.
        "headcell": make("mHeadCell", fontName=bold, fontSize=8.5, leading=11, textColor=_M_NAVY),
        "annexcell": make("mAnnexCell", fontSize=8.5, leading=11),
    }


def _m_lines(run, result, facts, language):
    """Rows of section 1 (Raccomandazione); nothing here is summarised or invented."""
    it = language == "it"
    proposal = result.get("proposal") or {}
    judgment = result.get("judgment")
    names = {"watch": ("Osservare: nessun acquisto in questa run", "Watch: no purchase in this run"),
             "rejected": ("Respinta: nessuna posizione", "Rejected: no position"),
             "incomplete": ("Incompleta: ricerca da completare", "Incomplete: research to complete"),
             "favorable": ("Favorevole", "Favourable")}
    action = names.get(judgment, (str(judgment), str(judgment)))[0 if it else 1]
    if proposal:
        action = f"{str(proposal.get('action', '')).capitalize()} · {action}"
    portfolio = (facts or {}).get("portfolio") or {}
    cash = (f"{'Cassa del book' if it else 'Book cash'} {_fmt(portfolio.get('cash'), 2, language)} EUR "
            f"({_fmt(portfolio.get('cash_pct'), 2, language, suffix='%')} {'del NAV' if it else 'of NAV'})"
            if portfolio.get("cash") is not None else ("Cassa del book: n.d." if it else "Book cash: n.d."))
    amount = (f"{_fmt(proposal['eur_amount'], 0, language)} EUR. {cash}" if proposal.get("eur_amount") is not None
              else ("0 EUR: nessun importo proposto. " if it else "0 EUR: no amount proposed. ") + cash)
    conviction = str(proposal["confidence"]) if proposal.get("confidence") else (
        "n.d. (nessuna proposta operativa)" if it else "n.d. (no actionable proposal)")
    horizon = str(proposal["timing"]) if proposal.get("timing") else (
        "n.d. (nessuna posizione)" if it else "n.d. (no position)")
    # Policy /4: conviction, horizon and the engine band are their own fields.
    if result.get("conviction"):
        conviction = _M_CONVICTION.get(result["conviction"], (result["conviction"],) * 2)[0 if it else 1]
    if isinstance(result.get("horizon"), dict):
        months = result["horizon"].get("months")
        horizon = (f"{result['horizon'].get('label') or 'n.d.'} ({_fmt(months, 0, language)} "
                   + (("mese" if months == 1 else "mesi") if it else ("month" if months == 1 else "months")) + ")")
    sizing = proposal.get("sizing") if isinstance(proposal.get("sizing"), dict) else None
    extra = []
    if sizing:
        band = (f"{_m_sig(sizing.get('band_min_eur'), language, 0)}–{_m_sig(sizing.get('band_max_eur'), language, 0)} EUR")
        if sizing.get("below_starter"):
            amount = (f"{_m_sig(sizing.get('amount_eur'), language, 0)} EUR: "
                      + ("sotto lo starter del motore: fascia vuota" if it else "below the engine starter: empty band")
                      + f" ({'minimo' if it else 'minimum'} {_m_sig(sizing.get('band_min_eur'), language, 0)}, "
                      + f"{'massimo' if it else 'maximum'} {_m_sig(sizing.get('band_max_eur'), language, 0)} EUR). {cash}")
        else:
            amount = (f"{_m_sig(sizing.get('amount_eur'), language, 0)} EUR "
                      f"({'fascia del motore' if it else 'engine band'} {band}). {cash}")
        extra.append(("Base del dimensionamento" if it else "Sizing basis", str(sizing.get("basis") or "n.d.")))
    destination = result.get("destination") or run.get("destination") or {}
    route = {"dcn": ("Proposta in DCN, da approvare dal PM", "DCN proposal pending PM approval"),
             "research": ("In ricerca: nessuna proposta in DCN", "In research: no DCN proposal")}.get(
        destination.get("kind"), ("Nessuna destinazione", "No destination"))[0 if it else 1]
    return action, [("Azione" if it else "Action", action), ("Importo" if it else "Amount", amount), *extra,
                    ("Convinzione" if it else "Conviction", conviction), ("Orizzonte" if it else "Horizon", horizon),
                    ("Destinazione" if it else "Routing", route)]


# Policy /4 wire values printed in the reader's language.
_M_CONVICTION = {"ALTA": ("Alta", "High"), "MEDIA": ("Media", "Medium"), "BASSA": ("Bassa", "Low")}
_M_SCENARIO = {"bear": ("Pessimistico", "Bear"), "base": ("Base", "Base"), "bull": ("Ottimistico", "Bull")}
_M_EXIT_ACTION = {"exit": ("Uscire", "Exit"), "reduce": ("Ridurre", "Reduce"), "review": ("Rivedere", "Review")}
_M_TRIGGER_KIND = {"date": ("Data", "Date"), "price": ("Prezzo", "Price"), "condition": ("Condizione", "Condition")}


def _is_memo_v4(result):
    """Policy /4 branches open only when its fields exist; /2-/3 results never carry them."""
    return isinstance(result.get("pillars"), list) and bool(result.get("conviction"))


def _m_sig(value, language, decimals, *, suffix="", digits=3):
    """/4 structured number: `decimals` places, but a non-zero value keeps at least `digits`
    significant digits (0.0012 -> 0,0012; 1.4 EUR -> 1,4), so it is never printed as 0."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "n.d."
    if value != 0 and abs(value) < 10 ** (digits - 1 - decimals):
        need = digits - 1 - math.floor(math.log10(abs(value)))
        if need > decimals:
            whole, _, frac = _fmt(value, need, language).partition("," if language == "it" else ".")
            frac = frac.rstrip("0").ljust(decimals, "0")
            return whole + (("," if language == "it" else ".") + frac if frac else "") + suffix
    return _fmt(value, decimals, language, suffix=suffix)


def _m_number(value, language, *, suffix=""):
    """A committee figure with the decimals it was written with (at most two, more below 1)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "n.d."
    decimals = 0 if float(value).is_integer() else 1 if round(value, 1) == value else 2
    return _m_sig(value, language, decimals, suffix=suffix)


def _m_pillar_flowables(pillars, number, first_sub, show, st, language):
    """Section 2 pillars: '2.k Title', then Thesis / Evidence / Risk as labelled prose."""
    it = language == "it"
    bold = st["serif_bold"]
    out = []
    for offset, pillar in enumerate(pillars or []):
        out.append(Paragraph(f"{number}.{first_sub + offset} " + show(pillar.get("title")), st["h2"]))
        for key, labels in (("thesis", ("Tesi.", "Thesis.")), ("evidence", ("Prova.", "Evidence.")),
                            ("risk", ("Rischio.", "Risk."))):
            out.append(Paragraph(f'<font name="{bold}">{escape(labels[0 if it else 1])}</font> '
                                 + show(pillar.get(key)), st["body"]))
    return out


def _m_variant_table(rows, show, st, width, language):
    """'Our estimates against consensus': the committee value is an estimate, a missing
    consensus prints n.d. and is never filled."""
    it = language == "it"
    head = (("Grandezza", "Periodo", "Consenso", "Comitato (stima)", "Motivazione") if it else
            ("Metric", "Period", "Consensus", "Committee (estimate)", "Rationale"))
    data = [[Paragraph(_text(v), st["headr"] if i in (2, 3) else st["head"]) for i, v in enumerate(head)]]
    for row in rows or []:
        data.append([Paragraph(show(row.get("metric")) + " (" + show(row.get("unit")) + ")", st["cell"]),
                     Paragraph(show(row.get("period")), st["cell"]),
                     Paragraph(_text(_m_number(row.get("consensus"), language)), st["cellr"]),
                     Paragraph(_text(_m_number(row.get("committee"), language)), st["cellr"]),
                     Paragraph(show(row.get("rationale")), st["cell"])])
    table = Table(data, colWidths=[width * .21, width * .10, width * .14, width * .18, width * .37],
                  repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
    table.setStyle(TableStyle(_M_TABLE))
    return table


def _m_scenario_change(target, price, scenario_currency, quote_currency):
    """Percent change of a committee target against the run price, computed here; a missing
    price or a target in another currency gives None (printed n.d.), never a proxy."""
    finite = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if not (finite(price) and price > 0 and finite(target)):
        return None
    if quote_currency and scenario_currency and quote_currency != scenario_currency:
        return None
    return (target / price - 1) * 100


def _m_scenario_table(scenarios, facts, show, st, width, language):
    """Section 3 scenario table (bear/base/bull); probabilities and targets are committee estimates."""
    it = language == "it"
    quote = (facts or {}).get("quote") or {}
    price, quote_currency = quote.get("price"), (facts or {}).get("currency")
    head = (("Scenario", "Probabilità %", "Prezzo obiettivo", "Variazione %", "Metodo") if it else
            ("Scenario", "Probability %", "Price target", "Change %", "Method"))
    data = [[Paragraph(_text(v), st["headr"] if 0 < i < 4 else st["head"]) for i, v in enumerate(head)]]
    order = {name: index for index, name in enumerate(_M_SCENARIO)}
    other_currency = False
    for scenario in sorted(scenarios, key=lambda s: order.get(s.get("name"), 99)):
        target = scenario.get("price_target")
        change = _m_scenario_change(target, price, scenario.get("currency"), quote_currency)
        other_currency |= bool(quote_currency and scenario.get("currency") and quote_currency != scenario.get("currency"))
        change_text = _m_sig(change, language, 1, suffix="%", digits=2)
        if change is not None and round(change, 1) > 0:
            change_text = "+" + change_text
        data.append([Paragraph(_text(_M_SCENARIO.get(scenario.get("name"), (str(scenario.get("name")),) * 2)
                                     [0 if it else 1]), st["cellb"]),
                     Paragraph(_text(_m_number(scenario.get("probability_pct"), language, suffix="%")), st["cellr"]),
                     Paragraph(_text(_m_sig(target, language, 2) + " " + str(scenario.get("currency") or "")), st["cellr"]),
                     Paragraph(_text(change_text), st["cellr"]),
                     Paragraph(show(scenario.get("method")), st["cell"])])
    table = Table(data, colWidths=[width * .14, width * .14, width * .17, width * .16, width * .39],
                  repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
    table.setStyle(TableStyle(_M_TABLE))
    if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
        date = quote.get("date")
        basis = ((("Variazione calcolata sul prezzo di " if it else "Change computed on the price of ")
                  + _m_sig(price, language, 2) + (" " + quote_currency if quote_currency else ""))
                 + (((" del " if it else " on ") + (_it_date(date) if it else str(date)[:10])) if date else ""))
        if other_currency:
            basis += ("; n.d. dove il prezzo obiettivo è in un'altra valuta" if it else
                      "; n.d. where the target is in another currency")
    else:
        basis = ("Variazione n.d.: prezzo di mercato non disponibile nella run" if it else
                 "Change n.d.: market price not available in the run")
    note = (("Probabilità e prezzi obiettivo sono stime del comitato, non dati osservati. " if it else
             "Probabilities and price targets are committee estimates, not observed data. ") + basis + ".")
    return [table, Paragraph(_text(note), st["note"])]


def _m_market_rows(facts, language):
    """Market, multiples and consensus table rows read from the tool receipts."""
    return _card_rows(facts, language)


def _history_table(facts, language, st, width):
    """Financial history in millions, exactly as extracted; missing cells print n.d."""
    it = language == "it"
    history = facts.get("history") or {}
    series = history.get("series") or {}
    revenue = series.get("revenue") or {}
    # A year with no revenue (e.g. equity only, as an opening balance) is not a fiscal column.
    years = [y for y in (history.get("years") or []) if revenue.get(y, revenue.get(str(y))) is not None]
    cons = facts.get("consensus") or {}
    est_rev = {str(i.get("period")): i.get("value") for i in cons.get("revenue") or [] if _annual(i.get("period"))}
    est_eps = {str(i.get("period")): i.get("value") for i in cons.get("eps") or [] if _annual(i.get("period"))}
    periods = [p for p in dict.fromkeys([*est_rev, *est_eps])]
    if not years and not periods:
        return None, None
    rows_def = [("revenue", "Ricavi", "Revenue", 1e6, 0), ("operating_income", "Utile operativo", "Operating income", 1e6, 0),
                ("net_income", "Utile netto", "Net income", 1e6, 0), ("cfo", "Flusso di cassa operativo", "Operating cash flow", 1e6, 0),
                ("capex", "Investimenti (capex)", "Capex", 1e6, 0), ("fcf", "Flusso di cassa libero", "Free cash flow", 1e6, 0),
                ("cash", "Cassa", "Cash", 1e6, 0), ("long_term_debt", "Debito a lungo termine", "Long-term debt", 1e6, 0),
                ("goodwill", "Avviamento", "Goodwill", 1e6, 0), ("equity", "Patrimonio netto", "Equity", 1e6, 0),
                ("eps_diluted", "Utile per azione diluito", "Diluted EPS", 1, 2)]
    head = [Paragraph(("Milioni di " if it else "Millions of ") + (facts.get("currency") or "n.d."), st["head"])]
    head += [Paragraph(str(y), st["headr"]) for y in years]
    head += [Paragraph(_text(_period(p, language) + (" (stima)" if it else " (est.)")), st["headr"]) for p in periods]
    rows = [head]

    def get(name, year):
        values = series.get(name) or {}
        return values.get(year, values.get(str(year)))
    for key, it_label, en_label, scale, dec in rows_def:
        if not any(get(key, y) is not None for y in years) and not (
                (key == "revenue" and est_rev) or (key == "eps_diluted" and est_eps)):
            continue
        label = (it_label if it else en_label) + (" (per azione)" if key == "eps_diluted" and it else
                                                  " (per share)" if key == "eps_diluted" else "")
        row = [Paragraph(_text(label), st["cell"])]
        row += [Paragraph(_fmt(get(key, y), dec, language, scale=scale), st["cellr"]) for y in years]
        estimates = est_rev if key == "revenue" else est_eps if key == "eps_diluted" else {}
        row += [Paragraph(_fmt(estimates.get(p), dec, language, scale=scale), st["cellr"]) for p in periods]
        rows.append(row)
        if key == "operating_income":
            margin = [Paragraph(_text("  " + ("Margine operativo" if it else "Operating margin")), st["cell"])]
            for y in years:
                rev, op = get("revenue", y), get("operating_income", y)
                margin.append(Paragraph(_fmt(op / rev * 100 if rev and op is not None else None, 1, language,
                                             suffix="%"), st["cellr"]))
            margin += [Paragraph("", st["cellr"]) for _ in periods]
            rows.append(margin)
    count = len(years) + len(periods)
    first = width * .3
    table = Table(rows, colWidths=[first] + [(width - first) / max(count, 1)] * count, repeatRows=1)
    table.setStyle(TableStyle(_M_TABLE))
    note = ((f"Fonti: storico {history.get('source') or 'n.d.'}" + (f"; stime {cons.get('source') or 'n.d.'}, "
             "periodo come dichiarato dal fornitore" if periods else "") + ".") if it else
            (f"Sources: history {history.get('source') or 'n.d.'}" + (f"; estimates {cons.get('source') or 'n.d.'}"
             if periods else "") + "."))
    return table, note


def _consensus_table(facts, language, st, width):
    it = language == "it"
    cons = facts.get("consensus") or {}
    if not cons:
        return None, None
    rows = [[Paragraph(_text(h), st["head"]) for h in (("Voce", "Valore") if it else ("Item", "Value"))]]
    rows += [[Paragraph(_text(a), st["cell"]), Paragraph(_text(b), st["cell"])] for a, b in _consensus_rows(facts, language)]
    for item in cons.get("eps_revisions") or []:
        rows.append([Paragraph(_text(("Revisioni EPS, " if it else "EPS revisions, ") + _period(item.get("period"), language)),
                               st["cell"]),
                     Paragraph(_text(("30 giorni " if it else "30 days ") + _fmt(item.get("change_30d_pct"), 2, language, suffix="%")
                                     + (" · 90 giorni " if it else " · 90 days ")
                                     + _fmt(item.get("change_90d_pct"), 2, language, suffix="%")), st["cell"])])
    names = (("strong_buy", "acquisto forte"), ("buy", "acquisto"), ("hold", "mantenere"), ("sell", "vendita"),
             ("strong_sell", "vendita forte"))
    for key, title in (("recommendations", ("Raccomandazioni, mese corrente", "Recommendations, current month")),
                       ("recommendations_prev", ("Raccomandazioni, rilevazione precedente", "Recommendations, earlier"))):
        rec = cons.get(key)
        if rec:
            rows.append([Paragraph(_text(title[0 if it else 1]), st["cell"]),
                         Paragraph(_text(" · ".join(f"{(name if it else k.replace('_', ' '))} {_fmt(rec.get(k), 0, language)}"
                                                    for k, name in names)), st["cell"])])
    table = Table(rows, colWidths=[width * .38, width * .62], repeatRows=1)
    table.setStyle(TableStyle(_M_TABLE))
    note = (("Fonte: " if it else "Source: ") + str(cons.get("source") or "n.d.")
            + ((" · data di osservazione non dichiarata dal fornitore" if it else " · observation date not stated")
               if not cons.get("as_of") else "") + ".")
    return table, note


# Where each deterministic exhibit is discussed: the figures sit inside that section.
_M_EXHIBITS = {
    "financial_quality": ["table:history", "revenue_margin", "revenue_path", "margins", "cash", "capital_allocation", "balance"],
    "valuation": ["table:market", "table:consensus", "price_targets", "eps_path", "recommendations", "multiples"],
    "portfolio_risk": ["risk", "tail_risk", "fat_tails", "vol_cone", "drawdown", "acf_abs", "rolling_beta",
                       "vix_sensitivity"],
    "positioning": ["price_history", "returns"],
}


# Policy /4 memo: fixed section titles from the section key (PM 04/10/2026); the Capo writes the text,
# not the headings. A key without an entry keeps the Capo's title, declared in the data note.
_M_SECTION_TITLES = {
    "executive": ("Sintesi e giudizio", "Executive summary and judgment"),
    "pm_view": ("La tesi del PM alla prova", "The PM thesis tested"),
    "business": ("Il business", "The business"),
    "financial_quality": ("Qualità finanziaria", "Financial quality"),
    "valuation": ("Valutazione", "Valuation"),
    "scenarios": ("Scenari", "Scenarios"),
    "portfolio_risk": ("Rischio di portafoglio", "Portfolio risk"),
    "catalysts": ("Catalizzatori", "Catalysts"),
    "positioning": ("Posizionamento", "Positioning"),
    "red_team": ("Red Team", "Red Team"),
    "decision": ("Decisione", "Decision"),
}


def _m_section_title(key, capo_title, language):
    """(printed title, declared line or None) of a /4 dossier section."""
    fixed = _M_SECTION_TITLES.get(key)
    if fixed:
        return fixed[0 if language == "it" else 1], None
    return capo_title, (f"Sezione «{capo_title}»: titolo scritto dal Capo, nessun titolo fisso per questa sezione"
                        if language == "it" else
                        f"Section «{capo_title}»: title written by the Capo, no fixed title for this section")


def _market_absent_line(pack, language):
    """The declared line printed when the run carries no usable market data pack."""
    it = language == "it"
    why = (("la run non lo contiene" if it else "the run does not carry it") if pack is None else
           ("formato non riconosciuto" if it else "unrecognised format"))
    return ("Pacchetto dati di mercato non disponibile: " if it else "Market data pack not available: ") + why + "."


def _market_missing_lines(missing, language):
    """Market figures not drawn, grouped by their declared reason (one line per reason)."""
    groups = {}
    for chart in missing:
        groups.setdefault(str(chart["missing"]), []).append(str(chart["title"]))
    head = "Figure di mercato non prodotte" if language == "it" else "Market figures not produced"
    return [f"{head} ({'; '.join(titles)}): {reason}" for reason, titles in groups.items()]


def _market_exhibits(pack, chart_dir, language, ticker):
    """(drawn charts, missing charts, gap lines) of the market data pack (Lotto 3).

    The pack lives in run["market_pack"], never inside facts: its daily series would flood
    the 3-decimal rule of the Capo prose (_known_values). No pack = no figure, one line."""
    if not isinstance(pack, dict):
        return [], [], [_market_absent_line(pack, language)]
    try:
        from bellomberg.reporting.trade_idea_market_charts import build_market_charts
        from bellomberg.reporting.trade_idea_market_stats import market_stats
        stats = market_stats(pack, language=language)
        charts = build_market_charts(stats, pack, chart_dir, language=language, ticker=ticker)
    except Exception as exc:  # declared, never silent: the integrity check flags it too
        return [], [], [("Figure di mercato non prodotte: errore " if language == "it" else
                         "Market figures not produced: error ") + type(exc).__name__ + "."]
    drawn = [c for c in charts if not c.get("missing")]
    missing = [c for c in charts if c.get("missing")]
    return drawn, missing, [*(stats.get("gaps") or []), *_market_missing_lines(missing, language)]


def _it_date(value):
    """2026-10-02 -> 02/10/2026; anything else is printed as given."""
    text = str(value or "")
    return f"{text[8:10]}/{text[5:7]}/{text[:4]}" if re.match(r"\d{4}-\d{2}-\d{2}", text) else text[:10]


def _render_company_memo(path, run, result, language, partial_reasons, *, localize_cells=True):
    """Impianto M (scelta PM 04/10/2026): memo d'investimento a sezioni numerate, lungo ed esaustivo.
    Figures and tables live inside the section that discusses them; sources close each section."""
    import tempfile
    from reportlab.platypus import Image
    styles = _memo_styles()
    st = _m_styles(styles)
    reg, bold = styles["body"].fontName, styles["h1"].fontName
    w, h = A4
    margin, bottom, top = 64, 56, 58
    width = w - 2*margin
    ticker = result["ticker"]
    it = language == "it"
    label = lambda it_text, en_text: it_text if it else en_text
    cutoff = str(run.get("cutoff") or result.get("cutoff") or run.get("started_at") or "n.d.")
    identity = run.get("identity") or {}
    name = identity.get("name") or ticker
    annex_data = run.get("desk_annex")
    facts = run.get("facts") or None
    sources = _Sources(result, annex_data, language, facts)
    # localize_cells=False (policy /4) is passed explicitly; the /2-/3 call keeps its exact form.
    show_sans = ((lambda value, cell=False: _shown(value, sources, language, cell=cell)) if localize_cells else
                 (lambda value, cell=False: _shown(value, sources, language, cell=cell, localize_cells=False)))
    v4 = _is_memo_v4(result)
    # Inline bold in Georgia prose is Georgia bold; table cells (cell=True) keep the sans bold.
    sans_bold_tag = f'<font name="{_bold_face()}">'
    show = lambda value, cell=False: (show_sans(value, cell) if cell else
                                      show_sans(value).replace(sans_bold_tag, f'<font name="{st["serif_bold"]}">'))
    date_label = cutoff[8:10] + "/" + cutoff[5:7] + "/" + cutoff[:4] if re.match(r"\d{4}-\d{2}-\d{2}", cutoff) else cutoff[:10]
    doc = _ResearchDoc(str(path), pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=top, bottomMargin=bottom, title=f"Memo d'investimento | {name}", author="Bellomberg", allowSplitting=1)
    doc.section, doc.section_pages, doc.header_sections = "Memo", {}, {}
    running = _M_RUNNING[0 if it else 1] + f"{name} ({ticker}) | {date_label}"

    def body_page(canvas, document):
        canvas.saveState()
        if document.page > 1:
            canvas.setFont(reg, 7.5)
            canvas.setFillColor(_M_GREY)
            canvas.drawString(margin, h-38, running)
            canvas.setStrokeColor(_M_NAVY)
            canvas.setLineWidth(.6)
            canvas.line(margin, h-43, w-margin, h-43)
        canvas.setStrokeColor(_M_RULE)
        canvas.setLineWidth(.5)
        canvas.line(margin, 44, w-margin, 44)
        canvas.setFillColor(_M_GREY)
        canvas.setFont(reg, 7.5)
        footer = _M_FOOTER[0 if it else 1]
        if run.get("preview"):
            footer = "ANTEPRIMA · DATI SINTETICI" if it else "PREVIEW · SYNTHETIC DATA"
        canvas.drawString(margin, 32, footer)
        canvas.drawRightString(w-margin, 32, ("Pagina " if it else "Page ") + str(document.page))
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="body", frames=[Frame(margin, bottom, width, h-top-bottom,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=body_page)])
    story = []
    counters = {"section": 0, "figure": 0, "table": 0}

    def section(key, title):
        counters["section"] += 1
        heading = f"{counters['section']}. {title}"
        story.extend([_Section(key, heading), Paragraph(_text(heading), st["h1"])])
        return counters["section"]

    def numbered(number, sub, value, style="num"):
        """One Capo paragraph (or list item) with its hanging number; tables inside stay tables."""
        flows = _rich_flowables(value, {**styles, "body": st[style], "cell": st["annexcell"],
                                        "headcell": st["headcell"]}, width, show=show)
        if flows and isinstance(flows[0], Paragraph):
            flows[0] = Paragraph(flows[0].text, st[style], bulletText=f"{number}.{sub}")
        story.extend(flows)

    def items(values, style="item"):
        for index, value in enumerate(values or []):
            story.append(Paragraph(show(value), st[style], bulletText=f"({chr(97 + index) if index < 26 else index + 1})"))

    def sources_line(texts, evidence_ids=()):
        keys = []
        for text in texts:
            for kind, body in _CITE.findall(str(text or "")):
                for key in _cite_items(kind, body)[0]:
                    if key not in keys:
                        keys.append(key)
        for evidence_id in evidence_ids:  # policy /4: traceability lives in evidence_ids, not in the prose
            if "evidence:" + str(evidence_id) not in keys:
                keys.append("evidence:" + str(evidence_id))
        if keys:
            story.append(Paragraph(_text(("Fonti della sezione: " if it else "Section sources: ")
                                         + "; ".join(sources.label(k) for k in keys) + "."), st["note"]))

    def caption(kind, title, unit=None):
        counters[kind] += 1
        word = {"figure": ("Figura", "Figure"), "table": ("Tabella", "Table")}[kind][0 if it else 1]
        story.append(Paragraph(_text(f"{word} {counters[kind]}. {title}"), st["caption"]))
        if unit:
            story.append(Paragraph(_text(unit), st["unit"]))

    charts, missing = {}, []

    def exhibit(key, chart_dir):
        if key == "table:history":
            table, note = _history_table(facts, language, st, width)
            if table is not None:
                caption("table", label("Storico di bilancio e stime del consenso", "Financial history and consensus estimates"))
                story.extend([table, Paragraph(_text(note), st["note"])])
        elif key == "table:market":
            rows = [[Paragraph(_text(h), st["head"]), Paragraph(_text(v), st["headr"])]
                    for h, v in [(label("Voce", "Item"), label("Valore", "Value"))]]
            rows += [[Paragraph(_text(a), st["cell"]), Paragraph(_text(b), st["cellr"])] for a, b in _m_market_rows(facts, language)]
            table = Table(rows, colWidths=[width * .55, width * .45], repeatRows=1)
            table.setStyle(TableStyle(_M_TABLE))
            caption("table", label("Prezzo, multipli e consenso degli analisti", "Price, multiples and analyst consensus"))
            tools = [sources.label(t) for t in dict.fromkeys(
                (facts.get(b) or {}).get("tool") for b in ("quote", "fundamentals", "consensus")) if t]
            risk_tool = ((facts.get("risk") or {}).get("candidate") or {}).get("tool")
            if risk_tool:
                tools.append(sources.label(risk_tool))
            story.extend([table, Paragraph(_text(label("Fonti: ", "Sources: ") + "; ".join(dict.fromkeys(tools)) + "."),
                                           st["note"])])
        elif key == "table:consensus":
            table, note = _consensus_table(facts, language, st, width)
            if table is not None:
                caption("table", label("Consenso degli analisti in dettaglio", "Analyst consensus in detail"))
                story.extend([table, Paragraph(_text(note), st["note"])])
        elif key in charts and charts[key].get("table"):  # market multiples: a table (PM 04/10), not a PNG
            chart = charts.pop(key)
            spec = chart["table"]
            caption("table", chart["title"], chart.get("subtitle"))
            bold_tag = f'<font name="{_bold_face()}">'
            rows = [[Paragraph(_text(v), st["head"] if i == 0 else st["headr"]) for i, v in enumerate(spec["columns"])]]
            for index, row in enumerate(spec["rows"]):
                wrap = (lambda s: bold_tag + s + "</font>") if index in spec.get("bold_rows", ()) else (lambda s: s)
                rows.append([Paragraph(wrap(_text(v)), st["cell"] if i == 0 else st["cellr"]) for i, v in enumerate(row)])
            count = len(spec["columns"])
            table = Table(rows, colWidths=[width * .34] + [width * .66 / (count - 1)] * (count - 1), repeatRows=1,
                          hAlign="LEFT")
            table.setStyle(TableStyle(_M_TABLE))
            story.extend([table, Paragraph(_text(spec.get("foot") or ""), st["note"]),
                          Paragraph(_text(label("Fonte: ", "Source: ") + str(chart.get("source") or "n.d.")), st["note"])])
            if chart.get("notes"):
                story.append(Paragraph("<b>" + escape(label("Lettura.", "Reading.")) + "</b> "
                                       + " ".join(_text(n) for n in chart["notes"]), st["reading"]))
        elif key in charts:
            chart = charts.pop(key)
            caption("figure", chart["title"], chart.get("subtitle"))
            aspect = float(chart.get("aspect") or .55)
            image = Image(chart["path"], width=width, height=width * aspect)
            image.hAlign = "LEFT"
            story.extend([image, Paragraph(_text(label("Fonte: ", "Source: ") + str(chart.get("source") or "n.d.")), st["note"])])
            if chart.get("notes"):
                story.append(Paragraph("<b>" + escape(label("Lettura.", "Reading.")) + "</b> "
                                       + " ".join(_text(n) for n in chart["notes"]), st["reading"]))

    with tempfile.TemporaryDirectory(prefix="bb-ti-charts-") as chart_dir:
        if facts:
            from bellomberg.reporting.trade_idea_charts import build_charts
            for chart in build_charts(facts, chart_dir, language=language, ticker=ticker):
                if chart.get("missing"):
                    missing.append(chart)
                else:
                    charts[chart["key"]] = chart
        # Lotto 3 market figures belong to the /4 memo; the /2-/3 output stays frozen byte for byte.
        market_drawn, _market_missing, market_gaps = (_market_exhibits(run.get("market_pack"), chart_dir, language, ticker)
                                                       if v4 else ([], [], []))
        charts.update((chart["key"], chart) for chart in market_drawn)

        # Intestazione del memo.
        story.append(Paragraph(_text(label("MEMO D'INVESTIMENTO", "INVESTMENT MEMO") + f" — {name} ({ticker})"), st["title"]))
        action, rows = _m_lines(run, result, facts, language)
        quote = (facts or {}).get("quote") or {}
        meta = [(label("Destinatario", "To"), label("Comitato d'investimento", "Investment Committee")),
                (label("Data", "Date"), date_label + (label("; prezzi di chiusura al ", "; closing prices as of ")
                                                       + _it_date(quote.get("date")) if quote.get("date") else "")),
                (label("Oggetto", "Subject"), label("Valutazione di un investimento in ", "Assessment of an investment in ")
                 + name + " (" + ", ".join(str(v) for v in (identity.get("exchange"), identity.get("currency")) if v) + ")"),
                (label("Raccomandazione", "Recommendation"), action)]
        table = Table([[Paragraph(_text(k), st["metak"]), Paragraph(show_sans(v), st["metav"] if k != meta[-1][0] else st["metak"])]
                       for k, v in meta], colWidths=[width * .24, width * .76])
        table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), .8, _M_NAVY), ("LINEBELOW", (0, -1), (-1, -1), .8, _M_NAVY),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(table)
        if partial_reasons:
            story.append(Paragraph(_text(_label("partial", language) + ": " + "; ".join(partial_reasons)), st["note"]))
        if run.get("preview"):
            story.append(Paragraph(_text("ANTEPRIMA · DATI SINTETICI" if it else "PREVIEW · SYNTHETIC DATA"), st["note"]))

        # Voce 9, impianto A (05/10/2026): le lacune dichiarate PRIMA della raccomandazione, SEMPRE
        # (anche nel risultato incompleto del Capo caduto, che non ha la sezione «decision»).
        # Numeri di paragrafo calcolati come li assegna il ciclo qui sotto: sezioni 1-3 fisse, poi
        # il dossier in ordine; in «decision» il paragrafo delle lacune segue quelli del Capo.
        gap_rows = _gap_rows(result.get("data_gaps"))
        dossier_keys = [d["key"] for d in result.get("dossier", [])]
        notes_number = 4 + len(dossier_keys)  # «Nota sui dati della run», dopo l'ultima sezione del dossier
        # Le lacune della pipeline (Nota sui dati) si costruiscono QUI, una volta: la prima pagina ne stampa
        # il conteggio e la Nota stampa la stessa lista. I titoli /4 scritti dal Capo passano da un _Sources
        # usa-e-getta: la numerazione delle fonti del documento resta quella della prima comparsa.
        scratch = _Sources(result, annex_data, language, facts)
        title_gaps = [declared for d in (result.get("dossier", []) if v4 else [])
                      for declared in [_m_section_title(d["key"], _clean(d["title"], scratch, language, plain=True),
                                                        language)[1]] if declared]
        gaps = [*((facts or {}).get("gaps") or []), *(f"{c['title']}: {c['missing']}" for c in missing), *market_gaps,
                *title_gaps]
        pipeline_count = len(gaps)
        first_page = _gaps_on_first_page(gap_rows)
        gaps_overflow = first_page < len(gap_rows)
        if "decision" in dossier_keys:
            index = dossier_keys.index("decision")
            gaps_ref = f"{4 + index}.{len(result['dossier'][index].get('paragraphs', [])) + 1}"
        else:  # nessuna Decisione: l'elenco completo va nella Nota sui dati, dopo le lacune della pipeline
            gaps_ref = f"{notes_number}.{2 if pipeline_count else 1}"
        gap_item = ParagraphStyle("mGapItem", parent=st["cell"], fontSize=8.8, leading=11.6, leftIndent=20,
                                  bulletIndent=0, bulletFontName=reg, bulletFontSize=8.8, spaceAfter=1.5)
        if gap_rows:
            story.extend(_limits_block(gap_rows, show_sans, st["head"], gap_item, width, language,
                                       limit=first_page, overflow_ref=gaps_ref,
                                       pipeline=pipeline_count, pipeline_ref=f"{notes_number}.1"))

        # 1. Raccomandazione.
        section("recommendation", label("Raccomandazione", "Recommendation"))
        reviews = result.get("review_conditions") or []
        body = [[Paragraph(_text(k), st["cellb"]), Paragraph(show_sans(v), st["cell"])] for k, v in rows]
        if reviews:
            body.append([Paragraph(_text(label("Condizioni per rivedere", "Conditions to revisit")), st["cellb"]),
                         [Paragraph(show_sans(v), st["cell"], bulletText=f"({chr(97 + i) if i < 26 else i + 1})")
                          for i, v in enumerate(reviews)]])
        table = Table(body, colWidths=[width * .26, width * .74])
        table.setStyle(TableStyle(_M_TABLE[:1] + _M_TABLE[2:]))
        story.append(table)
        triggers = (result.get("review_triggers") or []) if v4 else []
        if triggers:
            caption("table", label("Trigger di revisione", "Review triggers"))
            currency = next((str(s.get("currency")) for s in result.get("scenarios") or [] if s.get("currency")),
                            (facts or {}).get("currency") or "")
            rows_t = [[Paragraph(_text(v), st["head"]) for v in (
                ("Tipo", "Data / livello / condizione", "Cosa avviene") if it else
                ("Type", "Date / level / condition", "What happens"))]]
            for trigger in triggers:
                kind = trigger.get("kind")
                if kind == "date":
                    where = Paragraph(_text(_it_date(trigger.get("date")) if it else str(trigger.get("date") or "n.d.")),
                                      st["cell"])
                elif kind == "price":
                    where = Paragraph(_text(_m_sig(trigger.get("price_level"), language, 2)
                                            + (" " + currency if currency else "")), st["cell"])
                else:
                    where = Paragraph(show_sans(trigger.get("condition")), st["cell"])
                rows_t.append([Paragraph(_text(_M_TRIGGER_KIND.get(kind, (str(kind),) * 2)[0 if it else 1]), st["cellb"]),
                               where, Paragraph(show_sans(trigger.get("what")), st["cell"])])
            table = Table(rows_t, colWidths=[width * .16, width * .34, width * .50], repeatRows=1,
                          splitByRow=1, splitInRow=1, hAlign="LEFT")
            table.setStyle(TableStyle(_M_TABLE))
            story.append(table)
        sources_line([*reviews, *(t.get("condition") for t in triggers), *(t.get("what") for t in triggers)])

        # 2. Tesi in sintesi: the Capo's summary in full, then the evidence for and against.
        number = section("thesis", label("Tesi in sintesi", "Thesis in brief"))
        sub = 0
        for block in [b for b in re.split(r"\n\s*\n", str(result.get("summary") or "")) if b.strip()] or [""]:
            sub += 1
            numbered(number, sub, block)
        pillars = (result.get("pillars") or []) if v4 else []
        story.extend(_m_pillar_flowables(pillars, number, sub + 1, show, st, language))
        sub += len(pillars)
        for title, values in ((label("Elementi a favore", "Supporting evidence"), result.get("pros")),
                              (label("Elementi contrari", "Counterarguments"), result.get("cons"))):
            if values:
                sub += 1
                story.append(Paragraph(_text(f"{number}.{sub} {title}"), st["h2"]))
                items(values)
        variant = (result.get("variant_view") or []) if v4 else []
        if variant:
            caption("table", label("Le nostre stime contro il consensus", "Our estimates against consensus"))
            story.append(_m_variant_table(variant, show_sans, st, width, language))
            story.append(Paragraph(_text(label(
                "Comitato: stime del comitato, non dati osservati. Consenso n.d.: nessun consenso con fonte.",
                "Committee: committee estimates, not observed data. Consensus n.d.: no sourced consensus.")), st["note"]))
        sources_line([result.get("summary"), *(result.get("pros") or []), *(result.get("cons") or [])],
                     [*(result.get("summary_evidence_ids") or []),
                      *(i for p in pillars for i in p.get("evidence_ids") or []),
                      *(i for r in variant for i in r.get("evidence_ids") or [])] if v4 else ())

        # 3. Rischi, criteri di uscita e catalizzatori.
        number = section("risks", label("Rischi, criteri di uscita e catalizzatori", "Risks, exit criteria and catalysts"))
        sub = 0
        exits = (result.get("risk_exits") or []) if v4 else []
        if exits:
            caption("table", label("Rischi e criteri di uscita", "Risks and exit criteria"))
            rows_x = [[Paragraph(_text(v), st["head"]) for v in (
                ("Rischio", "Soglia", "Azione") if it else ("Risk", "Threshold", "Action"))]]
            rows_x += [[Paragraph(show_sans(x.get("risk")), st["cell"]), Paragraph(show_sans(x.get("threshold")), st["cell"]),
                        Paragraph(_text(_M_EXIT_ACTION.get(x.get("action"), (str(x.get("action")),) * 2)[0 if it else 1]),
                                  st["cellb"])] for x in exits]
            table = Table(rows_x, colWidths=[width * .46, width * .38, width * .16], repeatRows=1,
                          splitByRow=1, splitInRow=1, hAlign="LEFT")
            table.setStyle(TableStyle(_M_TABLE))
            story.append(table)
        for title, values in ((label("Rischi principali", "Main risks"), result.get("risks")),
                              (label("Criteri di invalidazione", "Invalidation criteria"), result.get("invalidation")),
                              (label("Catalizzatori datati", "Dated catalysts"), result.get("catalysts"))):
            if values:
                sub += 1
                story.append(Paragraph(_text(f"{number}.{sub} {title}"), st["h2"]))
                items(values)
        scenarios_v4 = [s for s in result.get("scenarios") or [] if "probability_pct" in s] if v4 else []
        if scenarios_v4:
            caption("table", label("Scenari", "Scenarios"))
            story.extend(_m_scenario_table(scenarios_v4, facts, show_sans, st, width, language))
        sources_line([*(result.get("risks") or []), *(result.get("invalidation") or []), *(result.get("catalysts") or [])],
                     [*(i for x in exits for i in x.get("evidence_ids") or []),
                      *(i for s in scenarios_v4 for i in s.get("evidence_ids") or [])] if v4 else ())

        # 4..N. The Capo's dossier, each section with its own exhibits.
        for dossier in result.get("dossier", []):
            key = dossier["key"]
            capo_title = _clean(dossier["title"], sources, language, plain=True)
            if v4:  # fixed heading from the key (PM 04/10); /2-/3 keep the Capo's title (frozen output)
                capo_title, _ = _m_section_title(key, capo_title, language)  # la riga dichiarata e' gia' in «gaps»
            number = section(key, capo_title)
            sub, texts = 0, list(dossier.get("paragraphs", []))
            if key == "pm_view":
                story.append(Paragraph(_text(_label("pm_view", language)), st["h2"]))
                story.append(Paragraph(show(result.get("pm_view") or run.get("pm_view") or _label("no_view", language)),
                                       st["quote"]))
                story.append(Paragraph(_text(label("Risposta del comitato", "Committee response")), st["h2"]))
                story.append(Paragraph(show(result.get("pm_view_response", "")), st["body"]))
                texts.append(result.get("pm_view_response"))
            for paragraph in dossier.get("paragraphs", []):
                sub += 1
                numbered(number, sub, paragraph)
            if key == "scenarios":
                for scenario in result.get("scenarios", []):
                    sub += 1
                    scenario_v4 = v4 and "probability_pct" in scenario
                    name = (_M_SCENARIO.get(scenario["name"], (scenario["name"],) * 2)[0 if it else 1]
                            if scenario_v4 else scenario["name"])
                    story.append(Paragraph(_text(f"{number}.{sub} ") + show(name), st["h2"]))  # the number is not a decimal
                    story.extend(_rich_flowables(scenario["analysis"], {**styles, "body": st["body"],
                                 "cell": st["annexcell"], "headcell": st["headcell"]}, width, show=show))
                    texts.append(scenario["analysis"])
                    if scenario_v4:
                        for title, values in ((label("Fattori", "Drivers"), scenario.get("drivers")),
                                              (label("Cosa la falsifica", "What would falsify it"),
                                               scenario.get("falsifiers"))):
                            if values:
                                story.append(Paragraph(_text(title), st["caption"]))
                                items(values)
                        texts.extend([*(scenario.get("drivers") or []), *(scenario.get("falsifiers") or [])])
            elif key == "red_team" and result.get("objections"):
                caption("table", label("Registro delle obiezioni del Red Team", "Red Team objection register"))
                rows = [[Paragraph(_text(v), st["head"]) for v in (
                    ("N.", "Obiezione", "Risposta del comitato", "Esito") if it else
                    ("No.", "Objection", "Committee response", "Outcome"))]]
                for index, objection in enumerate(result["objections"], 1):
                    rows.append([Paragraph(str(index), st["cell"]), Paragraph(show_sans(objection["objection"]), st["cell"]),
                                 Paragraph(show_sans(objection["response"]), st["cell"]),
                                 Paragraph(_text(label("Risolta", "Resolved") if objection["resolved"]
                                                 else label("Aperta", "Open")), st["cellb"])])
                    texts.extend([objection["objection"], objection["response"]])
                table = Table(rows, colWidths=[22, (width - 80) * .5, (width - 80) * .5, 58], repeatRows=1,
                              splitByRow=1, splitInRow=1)
                table.setStyle(TableStyle(_M_TABLE))
                story.append(table)
            elif key == "decision":
                if gap_rows:  # impianto A: rinvio alla prima pagina; oltre il tetto, l'elenco completo
                    sub += 1
                    story.append(Paragraph(_text(f"{number}.{sub} " + label("Dati mancanti e limiti",
                                                                            "Data gaps and limitations")), st["h2"]))
                    if gaps_overflow:
                        story.extend(_gap_items(gap_rows, show, st["item"], language))
                    else:
                        story.append(Paragraph(_text(label("Elencate in prima pagina, «Limiti di questa analisi».",
                                                           "Listed on page one, «Limits of this analysis».")),
                                               st["body"]))
                    texts.extend(result["data_gaps"])
                values = result.get("decisive_questions")
                if values:
                    sub += 1
                    story.append(Paragraph(_text(f"{number}.{sub} " + label("Domande decisive", "Decisive questions")),
                                           st["h2"]))
                    items(values)
                    texts.extend(values)
                destination = result.get("destination") or run.get("destination") or {}
                if destination.get("reason"):
                    sub += 1
                    story.append(Paragraph(_text(f"{number}.{sub} " + label("Destinazione della ricerca", "Research disposition")),
                                           st["h2"]))
                    story.append(Paragraph(show(destination["reason"]), st["body"]))
                proposal = result.get("proposal")
                if proposal:
                    sub += 1
                    story.append(Paragraph(_text(f"{number}.{sub} " + label("Proposta da valutare dal PM", "Proposal for PM review")),
                                           st["h2"]))
                    for value in (f"{proposal['action']} | {proposal['ticker']} | {proposal['confidence']}",
                                  proposal["timing"], proposal["rationale"]):
                        story.append(Paragraph(show(value), st["body"]))
                    if proposal.get("eur_amount") is not None:
                        story.append(Paragraph(_text("EUR " + _fmt(proposal["eur_amount"], 2, language)), st["body"]))
                    if proposal.get("sizing_source"):
                        story.append(Paragraph(show(proposal["sizing_source"]), st["small"]))
                    texts.extend([proposal.get("rationale"), proposal.get("sizing_source")])
            for table_data in dossier.get("tables", []):
                caption("table", _clean(table_data["title"], sources, language, cell=True, plain=True,
                                        localize_cells=localize_cells))
                rows = [[Paragraph(show(value, cell=True), st["head"]) for value in table_data["columns"]]]
                rows.extend([Paragraph(show(cell, cell=True), st["cell"]) for cell in row] for row in table_data["rows"])
                count = len(table_data["columns"])
                widths = [width] if count == 1 else [width*.3] + [width*.7/(count-1)]*(count-1)
                rendered = Table(rows, colWidths=widths, repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
                rendered.setStyle(TableStyle(_M_TABLE))
                story.extend([rendered, Paragraph(show(f"{table_data['unit']} | {table_data['period']} | "
                                                       f"{table_data['source']}", cell=True), st["note"])])
                texts.append(table_data["source"])
            for exhibit_key in _M_EXHIBITS.get(key, []):
                if facts or not exhibit_key.startswith("table:"):  # market figures do not need facts
                    exhibit(exhibit_key, chart_dir)
            sources_line(texts, [*(dossier.get("evidence_ids") or []),
                                 *((result.get("pm_view_evidence_ids") or []) if key == "pm_view" else []),
                                 *((i for s in result.get("scenarios") or [] for i in s.get("evidence_ids") or [])
                                   if key == "scenarios" else [])] if v4 else ())

        # Exhibits whose section the Capo did not write, then the declared data gaps.
        leftovers = [k for keys in _M_EXHIBITS.values() for k in keys
                     if k in charts or (k.startswith("table:") and not any(
                         d["key"] in [s for s, ks in _M_EXHIBITS.items() if k in ks] for d in result.get("dossier", [])))]
        full_list_here = gaps_overflow and "decision" not in dossier_keys
        if leftovers or gaps or not facts or full_list_here:
            number = section("data_notes", label("Nota sui dati della run", "Note on the run data"))
            for exhibit_key in leftovers:
                if facts or not exhibit_key.startswith("table:"):  # come nel dossier: senza facts lo dice la riga sotto
                    exhibit(exhibit_key, chart_dir)
            if not facts:
                story.append(Paragraph(_text(label("Dati numerici della run non disponibili: tabelle e figure deterministiche "
                                                   "non prodotte.", "Run numeric data not available: no deterministic "
                                                   "tables or figures.")), st["body"]))
            if gaps:
                story.append(Paragraph(_text(f"{number}.1 " + label("Dati che la run non ha procurato",
                                                                    "Data the run did not obtain")), st["h2"]))
                for index, gap in enumerate(gaps):
                    story.append(Paragraph(_text(gap), st["item"], bulletText=f"({chr(97 + index) if index < 26 else index + 1})"))
            if full_list_here:  # Capo caduto con piu' lacune del tetto: l'elenco completo cui rinvia la prima pagina
                story.append(Paragraph(_text(f"{gaps_ref} " + label("Dati mancanti e limiti — elenco completo",
                                                                    "Data gaps and limitations — full list")), st["h2"]))
                story.extend(_gap_items(gap_rows, show, st["item"], language))
                sources_line(result["data_gaps"])

        # Allegati.
        annex = _annex_blocks(annex_data, language)
        if annex:
            title = label("Allegato A — Analisi integrale dei desk e del Red Team",
                          "Annex A — Complete desk and Red Team analysis")
            story.extend([PageBreak(), _Section("annex", title), Paragraph(_text(title), st["h1"]),
                Paragraph(_text(label("Testo integrale dei report finali su cui ha deliberato il Capo; nessun taglio.",
                                      "Full final reports the Capo deliberated on; nothing is cut.")), st["small"])])
            for _, heading, block in annex:
                if heading:
                    story.append(Paragraph(_text(heading), st["h2"]))
                story.extend(_rich_flowables(block, {**styles, "body": st["body"], "cell": st["annexcell"],
                                                     "headcell": st["headcell"]}, width, show=show))
        title = label("Allegato B — Fonti, copertura e metodologia", "Annex B — Sources, coverage and methodology")
        story.extend([PageBreak(), _Section("sources", title), Paragraph(_text(title), st["h1"]),
                      Paragraph(show(_label("scope", language)), st["small"])])
        for index, key in enumerate(sources.order, 1):
            if key.startswith("evidence:"):
                row = sources.evidence.get(key[9:])
                if row:
                    story.append(Paragraph(f"{index}. " + show(f"{row['source']} | {row['as_of']}"), st["body"]))
                    story.append(Paragraph(show(row["summary"]), st["item"]))
                    story.append(Paragraph(show(label("Riferimento: ", "Reference: ") + str(row["id"])), st["small"]))
                    if row.get("url"):
                        url = escape(row["url"], quote=True)
                        story.append(Paragraph(f'<link href="{url}" color="#595959">'
                                               f'{escape(_clean(row["url"], sources, language))}</link>', st["small"]))
                    continue
            dates = sources.dates.get(key) or []
            story.append(Paragraph(f"{index}. " + _text(sources.label(key) + (" · " + ", ".join(dates) if dates else "")),
                                   st["item"]))
        if result.get("history_review"):
            story.append(Paragraph(_text(label("Confronto con le decisioni precedenti", "Review of earlier decisions")), st["h2"]))
            for review in result["history_review"]:
                story.extend([Paragraph(_text(f"{review['kind']} {review['id']}"), st["small"]),
                              Paragraph(show(review["response"]), st["body"])])
        story.append(Paragraph(_text(label("Provenienza del documento", "Document provenance")), st["h2"]))
        story.append(Paragraph(_text(f"Cutoff: {cutoff}\nRun: {run.get('id') or run.get('run_id') or result.get('run_id') or 'n.d.'}"),
                               st["small"]))
        models = run.get("models") or result.get("models") or {}
        for role, model in (models.items() if isinstance(models, dict) else enumerate(models)):
            if isinstance(model, dict):
                model = " · ".join(str(model[k]) for k in ("model", "effort", "max_tokens") if model.get(k) is not None) or "n.d."
            story.append(Paragraph(_text(f"{role}: {model}"), st["small"]))
        doc.build(story)
    return doc.section_pages


def _rich_flowables(text, styles, width, show=None):
    """Paragraphs for prose, a real table for a run of markdown pipe rows."""
    show = show or _text
    lines, out, prose, rows = str(text).split("\n"), [], [], []

    def flush_prose():
        if prose and "\n".join(prose).strip():
            out.append(Paragraph(show("\n".join(prose)), styles["body"]))
        prose.clear()

    def flush_rows():
        cells = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows
                 if not _MD_RULE.match(row)]
        if len(cells) >= 2:
            count = max(len(row) for row in cells)
            cells = [row + [""] * (count - len(row)) for row in cells]
            data = [[Paragraph(show(cell), styles["headcell" if index == 0 else "cell"]) for cell in row]
                    for index, row in enumerate(cells)]
            table = Table(data, colWidths=[width / count] * count, repeatRows=1, splitByRow=1, hAlign="LEFT")
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), OBSIDIAN),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PAPER, PALE]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, 0), .8, AMBER)]))
            out.extend([table, Spacer(1, 4)])
        elif rows:
            prose.extend(row for row in rows if not _MD_RULE.match(row))  # a lone separator is markup
            flush_prose()
        rows.clear()

    for line in lines:
        if line.strip().startswith("|") and line.strip().count("|") >= 2:
            flush_prose()
            rows.append(line)
        else:
            if rows:
                flush_rows()
            prose.append(line)
    if rows:
        flush_rows()
    flush_prose()
    return out


def _integrity_pieces(text):
    """Prose runs and individual table cells: wrapped cells interleave in extracted text."""
    pieces, prose = [], []
    for line in str(text).split("\n"):
        if line.strip().startswith("|") and line.strip().count("|") >= 2:
            if prose and "\n".join(prose).strip():
                pieces.append("\n".join(prose))
            prose = []
            if not _MD_RULE.match(line):
                pieces.extend(cell.strip() for cell in line.strip().strip("|").split("|") if cell.strip())
        else:
            prose.append(line)
    if prose and "\n".join(prose).strip():
        pieces.append("\n".join(prose))
    return pieces or [str(text)]


def _annex_blocks(annex, language):
    """(fragment name, heading or None, text) in print order; the same list feeds the
    renderer and the integrity check, so every printed annex block is verified."""
    if not isinstance(annex, dict):
        return []
    english = language != "it"
    if annex.get("unavailable"):
        return [("annex.unavailable", "Annex" if english else "Appendice",
                 ("[KO] Desk annex not available: " if english else "[KO] Appendice dei desk non disponibile: ")
                 + str(annex["unavailable"]))]
    names = {"macro": "Macro", "eventdesk": "Event desk", "crypto": "Crypto", "fundamentals": "Fundamentals",
             "quant": "Quant", "options": "Options" if english else "Opzioni"}
    states = {"answered": "answered" if english else "risposta", "conceded": "conceded" if english else "concessa",
              "open": "open" if english else "aperta"}
    out = []
    for desk in annex.get("desks") or []:
        name = str(desk.get("desk"))
        if desk.get("status") != "ready":
            out.append((f"annex.{name}.missing", "Desk " + names.get(name, name),
                        ("[KO] Final report not available for this desk." if english else
                         "[KO] Report finale non disponibile per questo desk.")))
            continue
        heading = "Desk " + names.get(name, name) + " — " + (
            ("final round" if english else "round finale") if str(desk.get("round")) == "2"
            else ("round " + str(desk.get("round"))))
        blocks = [block.strip() for block in re.split(r"\n\s*\n", str(desk.get("text") or "")) if block.strip()]
        for index, block in enumerate(blocks):
            out.append((f"annex.{name}.{index}", heading if index == 0 else None, block))
    red = str(annex.get("red_team") or "").strip()
    red_lines = []
    if red:
        try:
            parsed = json.loads(red)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            # Objections and decisive questions are printed once, in the ledger and question
            # list below; the remaining review fields are printed as readable labelled prose.
            def walk(value, label=""):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if not label and key in ("objections", "decisive_questions"):
                            continue
                        walk(item, f"{label} / {key}" if label else str(key))
                elif isinstance(value, list):
                    for item in value:
                        walk(item, label)
                elif value not in (None, "") and not isinstance(value, bool):
                    red_lines.append(f"{label.replace('_', ' ').capitalize()}: {value}")
            walk(parsed)
            if isinstance(parsed, dict) and not annex.get("ledger") and parsed.get("objections"):
                walk({"objection": parsed["objections"]})
        else:
            red_lines = [block.strip() for block in re.split(r"\n\s*\n", red) if block.strip()]
    for index, line in enumerate(red_lines):
        out.append((f"annex.red_team.{index}", "Red Team" if index == 0 else None, line))
    for index, row in enumerate(annex.get("ledger") or []):
        head = " · ".join([*(str(row.get(key)) for key in ("id", "desk", "category") if row.get(key)),
                           *((("material" if english else "materiale"),) if row.get("material") else ())])
        text = (head + ("\n" if head else "") + str(row.get("objection") or "")
                + (("\nRequested change: " if english else "\nModifica richiesta: ") + row["requested_change"]
                   if row.get("requested_change") else "")
                + ("\n" + ("Desk reply" if english else "Risposta del desk")
                   + f" ({states.get(row.get('state'), row.get('state') or 'n.d.')}): "
                   + (row.get("response") or "n.d.")))
        out.append((f"annex.ledger.{index}",
                    ("Objection ledger" if english else "Registro obiezioni e risposte") if index == 0 else None, text))
    for index, question in enumerate(annex.get("decisive_questions") or []):
        out.append((f"annex.question.{index}",
                    ("Decisive questions" if english else "Domande decisive") if index == 0 else None, question))
    return out


def _content_fragments(result):
    """Original analytic claims and citations which must survive PDF pagination."""
    for name in ("summary", "pm_view_response", "pm_view"):
        if result.get(name):
            yield name, str(result[name])
    for name in ("pros", "cons", "risks", "catalysts", "invalidation", "data_gaps", "review_conditions", "decisive_questions"):
        for index, value in enumerate(result.get(name, [])):
            yield f"{name}.{index}", value
    for section in result.get("dossier", []):
        for index, value in enumerate(section.get("paragraphs", [])):
            pieces = _integrity_pieces(value)
            for piece_index, piece in enumerate(pieces):
                yield (f"{section['key']}.{index}" if len(pieces) == 1
                       else f"{section['key']}.{index}.{piece_index}"), piece
        for index, table in enumerate(section.get("tables", [])):
            for value in [table["title"], table["source"], table["unit"], table["period"], *table["columns"],
                          *(cell for row in table["rows"] for cell in row)]:
                yield f"{section['key']}.table.{index}", value
    for name, fields in (("scenarios", ("name", "analysis")), ("objections", ("objection", "response")),
                         ("evidence", ("id", "source", "as_of", "summary", "url")), ("history_review", ("response",))):
        for index, row in enumerate(result.get(name, [])):
            for field in fields:
                # A /4 scenario name is a wire key (bear/base/bull) printed as a translated label.
                if field == "name" and name == "scenarios" and "probability_pct" in row:
                    continue
                if row.get(field):
                    yield f"{name}.{index}.{field}", row[field]
    if result.get("destination", {}).get("reason"):
        yield "destination.reason", result["destination"]["reason"]
    for field in ("timing", "rationale", "sizing_source"):
        if (result.get("proposal") or {}).get(field):
            yield "proposal."+field, result["proposal"][field]
    # Policy /4 memo fields: every printed text is verified like the prose (read with .get,
    # absent from /2-/3 results). Computed numbers (variation, formatted estimates) are not
    # original text and are covered by the renderer tests.
    if not _is_memo_v4(result):
        return
    if (result.get("horizon") or {}).get("label"):
        yield "horizon.label", result["horizon"]["label"]
    for index, pillar in enumerate(result.get("pillars") or []):
        for field in ("title", "thesis", "evidence", "risk"):
            if pillar.get(field):
                yield f"pillars.{index}.{field}", pillar[field]
    for index, row in enumerate(result.get("variant_view") or []):
        for field in ("metric", "period", "unit", "rationale"):
            if row.get(field):
                yield f"variant_view.{index}.{field}", row[field]
    for index, scenario in enumerate(result.get("scenarios") or []):
        for field in ("drivers", "falsifiers"):
            for item_index, value in enumerate(scenario.get(field) or []):
                yield f"scenarios.{index}.{field}.{item_index}", value
        if scenario.get("method"):
            yield f"scenarios.{index}.method", scenario["method"]
    for index, row in enumerate(result.get("risk_exits") or []):
        for field in ("risk", "threshold"):
            if row.get(field):
                yield f"risk_exits.{index}.{field}", row[field]
    for index, trigger in enumerate(result.get("review_triggers") or []):
        for field in ("condition", "what"):
            if trigger.get(field):
                yield f"review_triggers.{index}.{field}", trigger[field]
    sizing = (result.get("proposal") or {}).get("sizing") or {}
    if sizing.get("basis"):
        yield "proposal.sizing.basis", sizing["basis"]


def _m_structured_numbers(result):
    """(name, value) of every /4 structured number printed in sections 1-3."""
    for index, scenario in enumerate(result.get("scenarios") or []):
        for field in ("price_target", "probability_pct"):
            if field in scenario:
                yield f"scenarios.{index}.{field}", scenario[field]
    for index, row in enumerate(result.get("variant_view") or []):
        for field in ("committee", "consensus"):
            if row.get(field) is not None:
                yield f"variant_view.{index}.{field}", row[field]
    for index, trigger in enumerate(result.get("review_triggers") or []):
        if trigger.get("price_level") is not None:
            yield f"review_triggers.{index}.price_level", trigger["price_level"]
    sizing = (result.get("proposal") or {}).get("sizing") or {}
    for field in ("amount_eur", "band_min_eur", "band_max_eur"):
        if sizing.get(field) is not None:
            yield f"proposal.sizing.{field}", sizing[field]


def _m_numbers_not_reread(result, pages, section_pages, language):
    """Names of /4 structured numbers that the PDF text does not carry at their shown rounding.

    Independent of the formatter: every number token of sections 1-3 is parsed back, and
    each value needs its own token within half a unit of the printed last digit AND within
    0.5% of the value (a non-zero value printed as 0, or with one significant digit, fails).
    """
    start = section_pages.get("recommendation", 1)
    end = min((section_pages[k] for k in DOSSIER_KEYS if k in section_pages), default=len(pages))
    text = "\n".join(pages[start - 1:end])
    if language == "it":
        pattern, group, point = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?", ".", ","
    else:
        pattern, group, point = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", ",", "."
    tokens = []
    for raw in re.findall(pattern, text):
        clean = raw.replace(group, "")
        decimals = len(clean.partition(point)[2])
        tokens.append((float(clean.replace(point, ".")), decimals))
    used, missing = set(), []
    for name, value in sorted(_m_structured_numbers(result), key=lambda item: abs(item[1])):
        found = None
        for index, (parsed, decimals) in enumerate(tokens):
            if index in used:
                continue
            error = abs(parsed - abs(value))
            if error <= .5 * 10 ** -decimals + 1e-9 and error <= .005 * abs(value) * (1 + 1e-9) + 1e-12:
                found = index
                break
        if found is None:
            missing.append(name)
        else:
            used.add(found)
    return missing


def _normalized_text(value, rendered=False):
    # Markdown markers in the ORIGINAL become typography (bold, headings, bullets, tables).
    # The extracted PDF text only loses the drawn bullet: a prose dash that wraps to the
    # start of a printed line is content, not a marker. Pipes never carry meaning here.
    value = _printable(value)
    if rendered:
        value = re.sub(r"(?m)^\s*\u2022\s+", "", value)
        # Impianto M paragraph numbers ("2.1", "(a)") are drawn bullets, not content.
        value = re.sub(r"(?m)^\s*(?:\d{1,2}\.\d{1,2}|\([a-z]\))\s+", "", value)
    else:
        rules = _md_rules_apply(value)
        value = "\n".join(line for line in value.split("\n") if not (rules and _MD_RULE.match(line)))
        value = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*\u2022]\s+(?![\d.,]+\s*%))", "", value).replace("**", "")
    value = value.replace("|", "")
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).replace("\u00ad", "")).casefold()


_NO_MARKET = object()  # inspect called without the run (direct calls): no market check


def _market_not_reread(pack, normalized, raw, language, ticker):
    """(lost fragments, unreconciled numbers) of the market figures (Lotto 3).

    The statistics are RECOMPUTED here from the pack (never taken from the renderer), the
    readings regenerated without drawing, then: every title, reading, missing-figure line and
    statistics gap must be in the PDF text, and every number of every reading must map one to
    one onto the value read back from the statistics at its printed rounding
    (trade_idea_market_charts.verify_readings)."""
    present = lambda text: _normalized_text(text) in normalized or _normalized_text(text) in raw
    if not isinstance(pack, dict):
        return ([] if present(_market_absent_line(pack, language)) else ["market.absent_line"]), []
    try:
        from bellomberg.reporting.trade_idea_market_charts import market_readings, verify_readings
        from bellomberg.reporting.trade_idea_market_stats import market_stats
        stats = market_stats(pack, language=language)
        readings = market_readings(stats, pack, language=language, ticker=ticker)
    except Exception as exc:
        return [f"market.recompute.{type(exc).__name__}"], []
    lost = []
    for item in readings:
        if item.get("missing"):
            continue
        if not present(item["title"]):
            lost.append(f"market.{item['key']}.title")
        if not present(" ".join(item["notes"])):
            lost.append(f"market.{item['key']}.reading")
        table = item.get("table") or {}
        for index, row in enumerate(table.get("rows") or []):  # every printed row, its numbers verified below
            if not present(" ".join(row)):
                lost.append(f"market.{item['key']}.row.{index}")
        if table.get("foot") and not present(table["foot"]):
            lost.append(f"market.{item['key']}.foot")
    missing = [item for item in readings if item.get("missing")]
    for index, line in enumerate([*(stats.get("gaps") or []), *_market_missing_lines(missing, language)]):
        if not present(line):
            lost.append(f"market.gap.{index}")
    return lost, verify_readings(stats, readings, language, ticker)


def _inspect_company_memo(path, result, section_pages, language, execution_policy=EXECUTION_POLICY_V2,
                          annex=None, facts=None, market=_NO_MARKET):
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    # Page furniture and repeated table headings can interrupt a split paragraph/cell.
    sources = _Sources(result, annex, language, facts)
    # The PDF shows the single display transform (numbered sources, no hashes, localized
    # cells): the original is compared through the SAME transform, never loosened.
    localize_cells = execution_policy != EXECUTION_POLICY_V4
    # Le lacune si stampano senza la numerazione del Capo in sequenza (_gap_texts): stessa trasformazione qui.
    gap_texts = _gap_texts(result.get("data_gaps"))
    shown = lambda name, value: _clean(gap_texts[int(name.split(".")[1])] if name.startswith("data_gaps.") else value,
                                       sources, language,
                                       cell=".table." in name, plain=True, localize_cells=localize_cells)
    furniture = {"BELLOMBERG", "Ricerca Bellomberg · Documento interno", "Bellomberg Research · Internal document",
                 "ANTEPRIMA · DATI SINTETICI", "PREVIEW · SYNTHETIC DATA", *_M_FOOTER}
    table_heads = {shown(".table.", cell).strip() for section in result.get("dossier", [])
                   for table in section.get("tables", []) for cell in table["columns"]}
    body = "\n".join(line for text in pages for line in text.splitlines()
        if line.strip() not in furniture | table_heads and not re.fullmatch(r"(?:Pagina|Page) \d+", line.strip())
        and "TRADE IDEA | " not in line and not line.strip().startswith(_M_RUNNING))
    # Headers themselves are also verified against the unfiltered document.
    normalized, raw = _normalized_text(body, rendered=True), _normalized_text("\n".join(pages), rendered=True)
    annex_fragments = [(name if len(pieces) == 1 else f"{name}.{index}", piece)
                       for name, _, text in _annex_blocks(annex, language)
                       for pieces in [_integrity_pieces(text)] for index, piece in enumerate(pieces)]
    lost = sorted({name for name, value in [*_content_fragments(result), *annex_fragments]
                   for printed in [_normalized_text(shown(name, value))]
                   if printed not in normalized and printed not in raw})
    if _is_memo_v4(result):  # policy /4: structured numbers are read back at their printed rounding
        lost = sorted({*lost, *_m_numbers_not_reread(result, pages, section_pages, language)})
    market_unreconciled = []
    if market is not _NO_MARKET and _is_memo_v4(result):  # Lotto 3 (/4 memo): market figures re-read from recomputed stats
        market_lost, market_unreconciled = _market_not_reread(market, normalized, raw, language, result.get("ticker"))
        lost = sorted({*lost, *market_lost})
    sections = result.get("dossier", [])
    keys = [section["key"] for section in sections]
    missing = sorted(set(DOSSIER_KEYS) - set(keys))
    empty = [s["key"] for s in sections if not s.get("paragraphs") or any(not p.strip() for p in s["paragraphs"])]
    placeholder = re.compile(r"\s*(?:\[?todo\]?|tbd|n\.?\s?d\.?|da completare|analisi da completare|"
        r"dato non disponibile|dati non disponibili|analisi non disponibile|to be completed|analysis pending|"
        r"not available|placeholder|\.\.\.|…)[.!\s]*", re.I)
    # Authoring markers are never analysis, wherever they appear in a sentence.
    # ("da completare" alone stays a full-paragraph placeholder: "lavori da completare" is business prose.)
    # Only unambiguous authoring forms: "data TBD dalla societa'" is legitimate prose.
    marker = re.compile(r"\bTODO\b|\bFIXME\b|(?i:lorem ipsum)|(?i:\[(?:todo|tbd|inserire|insert)[^\]]*\])"
        r"|(?i:<(?:inserire|insert)[^>]*>)|(?im:^\s*(?:todo|tbd)\s*:)")
    # An unpunctuated paragraph ending on a conjunction/article/preposition is a cut.
    # Single letters are excluded: "Serie A", "classe E" are legitimate endings.
    # Lower-case and not after a dot: "SHOP.TO", "MA" or "LE" tickers are not cut words.
    dangling = re.compile(r"(?<![.\w])(?:ed|ma|il|lo|la|gli|le|un|una|di|del|della|che|per|con|tra|fra|"
        r"and|or|but|the|an|of|to|for|with|which)\s*$")
    def unfinished_text(value):
        value = re.sub(r"\[src:[^\]]*\]", "", str(value), flags=re.I).strip()
        return not value or bool(placeholder.fullmatch(value)) or bool(marker.search(value))
    def cut_text(value):
        return bool(dangling.search(re.sub(r"\[src:[^\]]*\]", "", str(value), flags=re.I).strip()))
    unfinished = [s["key"] for s in sections if any(unfinished_text(p) for p in s.get("paragraphs", []))]
    unfinished.extend(field for field in ("summary", "pm_view_response") if unfinished_text(result.get(field, "")))
    for index, objection in enumerate(result.get("objections", [])):
        if unfinished_text(objection.get("objection", "")) or unfinished_text(objection.get("response", "")):
            unfinished.append(f"objections.{index}")
    # Lists, scenarios and proposal are printed (cover included): same rule as the prose.
    prose = {"summary", "pm_view_response", "pm_view", "destination.reason", "proposal.rationale"}
    seen_fields = set(unfinished)
    for name, value in _content_fragments(result):
        root = name.split(".")[0]
        # Dossier paragraphs were checked above; "catalysts"/"scenarios" are also list fields.
        if root in {"evidence", "objections"} or ".table." in name:
            continue
        if name not in prose and not re.fullmatch(r"(?:pros|cons|risks|catalysts|invalidation|data_gaps|"
                r"review_conditions|decisive_questions)\.\d+|scenarios\.\d+\.analysis|"
                r"history_review\.\d+\.response|proposal\.timing|"
                # Policy /4 printed fields follow the same rule.
                r"horizon\.label|pillars\.\d+\.(?:title|thesis|evidence|risk)|variant_view\.\d+\.rationale|"
                r"scenarios\.\d+\.(?:method|drivers\.\d+|falsifiers\.\d+)|risk_exits\.\d+\.(?:risk|threshold)|"
                r"review_triggers\.\d+\.(?:condition|what)|proposal\.sizing\.basis", name):
            continue
        if unfinished_text(value) and name not in seen_fields:
            unfinished.append(name)
            seen_fields.add(name)
    truncated = [f"{s['key']}.{i}" for s in sections for i, p in enumerate(s.get("paragraphs", [])) if cut_text(p)]
    truncated.extend(field for field in ("summary",) if cut_text(result.get(field, "")))
    sentences = Counter(
        _normalized_text(sentence) for p in [*(p for s in sections for p in s.get("paragraphs", [])),
                                              str(result.get("summary") or "")]
        for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\[src:[^\]]*\]", "", p, flags=re.I))
        if len(_normalized_text(sentence)) >= 80)
    recycled = sum(count - 1 for count in sentences.values() if count > 2)
    unprintable = sorted({f"U+{ord(ch):04X}" for _, value in [*_content_fragments(result), *annex_fragments]
                          for ch in _missing_glyphs(value) & (_missing_glyphs(value, _symbol_font())
                                                               if _symbol_font() else set(str(value)))})
    paragraphs = [p for s in sections for p in s.get("paragraphs", [])]
    same_sections = Counter(_normalized_text("\n".join(s.get("paragraphs", []))) for s in sections)
    repeated = any(text and count > 1 for text, count in same_sections.items())
    reasons = []
    if result.get("judgment") == "incomplete":
        reasons.append("Giudizio incompleto" if language == "it" else "Incomplete judgment")
    for values, it, en in ((missing, "Sezioni mancanti", "Missing sections"),
                           (empty, "Sezioni vuote", "Empty sections"),
                           (unfinished, "Sezioni da completare", "Unfinished sections"),
                           (truncated, "Testo interrotto a meta' frase", "Text cut mid-sentence"),
                           (lost, "Testo originale non integro nel PDF", "Original text missing from PDF")):
        if values:
            reasons.append((it if language == "it" else en) + ": " + ", ".join(values))
    if market_unreconciled:
        reasons.append(("Numeri delle letture di mercato non riconciliati con le statistiche: " if language == "it" else
                        "Market reading numbers not reconciled with the statistics: ") + "; ".join(market_unreconciled))
    # Extra text is not lost text: a raw source marker in print is caught on its own.
    raw_markers = len(re.findall(r"\[\s*(?:src|evidence)\s*:", "\n".join(pages), re.I))
    if raw_markers:
        reasons.append(f"Marcatori di fonte crudi nel PDF: {raw_markers}" if language == "it" else
                       f"Raw source markers in the PDF: {raw_markers}")
    if recycled:
        reasons.append(f"Frasi lunghe ripetute piu' di due volte: {recycled}" if language == "it" else
                       f"Long sentences repeated more than twice: {recycled}")
    if repeated or len(keys) != len(set(keys)):
        reasons.append("Sezioni ripetute non costituiscono copertura analitica" if language == "it" else
                       "Repeated sections do not constitute analytical coverage")
    # The verbatim annex is desk material, not the Capo's analytical pages.
    source_page = min(section_pages.get("annex", len(pages)+1), section_pages.get("sources", len(pages)+1))
    first = min((section_pages[k] for k in DOSSIER_KEYS if k in section_pages), default=source_page)
    counts = [{"page": i, "words": len(re.findall(r"\b\w+\b", text)), "analytical": first <= i < source_page}
              for i, text in enumerate(pages, 1)]
    return {"status": "partial" if reasons else "ready", "execution_policy": execution_policy,
        "total_pages": len(pages), "analytical_pages": sum(page["analytical"] for page in counts),
        "analytical_words": sum(len(re.findall(r"\b\w+\b", p)) for p in paragraphs),
        "pages": counts, "section_pages": section_pages, "reasons": reasons,
        "content_integrity": "incomplete" if lost else "complete", "integrity_missing": lost,
        "notices": ([("Caratteri non stampabili resi come codice: " if language == "it" else
                      "Unprintable characters printed as code points: ") + ", ".join(unprintable)]
                    if unprintable else [])}


def inspect_research_pdf(path, result, section_pages, *, language="it", execution_policy=None, annex=None,
                         facts=None, market=_NO_MARKET):
    """Measure actual selectable text; cover/bibliography/PM quotes do not qualify.
    ``market`` = run["market_pack"] (None included): its figures and readings are re-read."""
    if execution_policy in RESEARCH_POLICIES:
        return _inspect_company_memo(path, result, section_pages, language, execution_policy, annex, facts, market)
    if execution_policy is not None:
        raise ValueError("Unknown Trade Idea report execution policy")
    reader = PdfReader(str(path))
    source_start = section_pages.get("sources", len(reader.pages) + 1)
    analysis_start = min((section_pages[key] for key in DOSSIER_KEYS if key in section_pages),
                         default=len(reader.pages)+1)
    page_counts = []
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        body = "\n".join(line for line in text.splitlines() if not line.startswith(("BELLOMBERG RESEARCH", "Cutoff ")))
        words = re.findall(r"\b[\wÀ-ÿ]+\b", body)
        analytical = analysis_start <= number < source_start and len(words) >= 180 and len(set(words)) >= 65
        page_counts.append({"page": number, "words": len(words), "analytical": analytical})
    paragraphs = [re.sub(r"\s+", " ", p).strip() for s in result.get("dossier", []) for p in s["paragraphs"]]
    duplicates = [p for p, n in Counter(paragraphs).items() if n > 1 and len(p) >= 100]
    word_count = sum(len(re.findall(r"\b\w+\b", p)) for p in set(paragraphs))
    missing = sorted(set(DOSSIER_KEYS) - {s["key"] for s in result.get("dossier", [])})
    count = sum(page["analytical"] for page in page_counts)
    reasons = []
    if count < 10:
        reasons.append(f"Pagine analitiche sostanziali: {count}; richieste: 10" if language == "it" else
                       f"Substantive analytical pages: {count}; required: 10")
    if word_count < 4000:
        reasons.append(f"Parole analitiche distinte: {word_count}; copertura minima: 4000" if language == "it" else
                       f"Distinct analytical words: {word_count}; minimum coverage: 4000")
    if missing:
        reasons.append(("Sezioni analitiche mancanti: " if language == "it" else "Missing analytical sections: ") + ", ".join(missing))
    if duplicates:
        reasons.append("I paragrafi ripetuti non costituiscono ricerca sostanziale" if language == "it" else
                       "Repeated analytical paragraphs are not substantive research")
    return {"status": "ready" if not reasons else "partial", "total_pages": len(reader.pages),
            "analytical_pages": count, "analytical_words": word_count, "pages": page_counts,
            "section_pages": section_pages, "reasons": reasons}


def build_trade_idea_report(run, result, *, output_path, valuations=(), language="it"):
    """Return a measured artifact; a partial dossier is never promoted by padding."""
    if language not in ("it", "en"):
        raise ValueError("Unsupported report language")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    policy = run.get("execution_policy")
    # Policy /4 cells arrive in the reader's number convention: printed as written.
    localize_cells = policy != EXECUTION_POLICY_V4
    render = (lambda reasons: _render_company_memo(path, run, result, language, reasons,
                                                   localize_cells=localize_cells)) if policy in RESEARCH_POLICIES else (
        lambda reasons: _render(path, run, result, valuations, language, reasons))
    sections = render([])
    quality = inspect_research_pdf(path, result, sections, language=language, execution_policy=policy,
                                   annex=run.get("desk_annex"), facts=run.get("facts"), market=run.get("market_pack"))
    if quality["status"] != "ready":
        sections = render(quality["reasons"])
        quality = inspect_research_pdf(path, result, sections, language=language, execution_policy=policy,
                                   annex=run.get("desk_annex"), facts=run.get("facts"), market=run.get("market_pack"))
    return {"path": str(path.resolve()), "sha256": sha256(path.read_bytes()).hexdigest(),
            "quality": quality, "status": quality["status"], "reason": "; ".join(quality["reasons"])}


def build_model_extract(ticker, models, sources, *, output_path, language="it"):
    """A public model extract has no committee prose or personal research context."""
    if language not in ("it", "en") or not models:
        raise ValueError("Public model extract requires models and a supported language")
    reg, bold, _ = _register_fonts()
    w, h = A4
    margin, width = 48, w-96
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    it = language == "it"
    title = "Estratto condivisibile del modello" if it else "Shareable model extract"
    styles = {
        "body": ParagraphStyle("publicBody", fontName=reg, fontSize=10.4, leading=14.4, textColor=INK, spaceAfter=8),
        "h1": ParagraphStyle("publicH1", fontName=bold, fontSize=20, leading=25, textColor=OBSIDIAN, keepWithNext=True, spaceAfter=12),
        "h2": ParagraphStyle("publicH2", fontName=bold, fontSize=12, leading=16, textColor=OBSIDIAN, keepWithNext=True, spaceBefore=12, spaceAfter=8),
        "small": ParagraphStyle("publicSmall", fontName=reg, fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=8),
        "cell": ParagraphStyle("publicCell", fontName=reg, fontSize=8.6, leading=11.8, textColor=INK),
        "numeric": ParagraphStyle("publicNumber", fontName=reg, fontSize=8.6, leading=11.8, textColor=INK, alignment=2),
        "headcell": ParagraphStyle("publicHeadCell", fontName=bold, fontSize=8.6, leading=11.8, textColor=AMBER),
    }
    def p(value, style="body"):
        return Paragraph(_text(value), styles[style])
    def background(canvas, document):
        canvas.saveState()
        _draw_lockup(canvas, margin, h-33, 11.5, reg, bold, OBSIDIAN, MUTED, AMBER, tagline=False)
        canvas.setFont(reg, 7)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(w-margin, h-33, ticker + " | " + title)
        canvas.setStrokeColor(RULE)
        canvas.line(margin, h-47, w-margin, h-47)
        canvas.drawString(margin, 26, "BELLOMBERG RESEARCH | " + ("Estratto del modello" if it else "Model extract"))
        canvas.drawRightString(w-margin, 26, str(document.page))
        canvas.restoreState()
    document = _ResearchDoc(str(path), pagesize=A4, title=f"{title} | {ticker}",
        author="Bellomberg Research", leftMargin=margin, rightMargin=margin, topMargin=69, bottomMargin=48)
    document.section, document.section_pages, document.header_sections = title, {}, {}
    document.addPageTemplates([PageTemplate(id="extract", frames=[Frame(margin, 48, width, h-117,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=background)])
    story = [_Section("public-model", title), _SectionTitle(title, styles["h1"], width),
             p(ticker, "h2"), p("Numeri, formule, ipotesi e fonti pubbliche della medesima versione economica. "
                 "La prosa originale del comitato e la compatibilità personale sono escluse. "
                 "Questo estratto non è il dossier di ricerca completo." if it else
                 "Numbers, formulas, assumptions and public sources from the same economic version. "
                 "Original committee prose and personal suitability are excluded. "
                 "This extract is not the complete research dossier."), Spacer(1, 10)]
    for index, model in enumerate(models, 1):
        report = model.get("model_exhibits") or {}
        if report.get("status") != "complete":
            raise ValueError("Shared model exhibits unavailable: " + "; ".join(report.get("reasons") or []))
        story.extend([p(f"{model.get('method') or 'n.d.'} | {model.get('valuation_date') or 'n.d.'}", "h2"),
            p(f"Snapshot {model.get('snapshot_id')} | Generation {model.get('generation_id')}", "small"),
            p(f"Excel SHA256 {model.get('workbook_sha256')}", "small")])
        if report.get("intrinsic_value_applicable") is False:
            story.append(p(_label("exposure_scope", language), "small"))
        for exhibit in report.get("exhibits") or []:
            story.extend(_exhibit_story(exhibit, width, styles, language))
        story.extend(_model_gaps(report, styles, language))
    story.extend([PageBreak(), _Section("public-sources", _label("sources", language)),
                  _SectionTitle(_label("sources", language), styles["h1"], width)])
    seen = set()
    for source in sources:
        identity = (source.get("id"), source.get("sha256"), source.get("url"))
        if identity in seen:
            continue
        seen.add(identity)
        source_title = source.get("title") or source.get("id") or "n.d."
        if it and str(source_title).endswith(" public source"):
            source_title = str(source_title).removesuffix(" public source") + " - fonte pubblica"
        story.append(p(source_title, "h2"))
        for key in ("period", "published_at", "locator", "sha256"):
            if source.get(key) is not None:
                labels = {'period':('Periodo','Period'), 'published_at':('Data di pubblicazione','Publication date'),
                          'locator':('Riferimento estratto','Extract reference'), 'sha256':('SHA256 estratto pubblico','Public extract SHA256')}
                label = labels[key][0 if it else 1]
                story.append(p(f"{label}: {source[key]}", "small"))
        if source.get("url"):
            url = escape(str(source["url"]), quote=True)
            story.append(Paragraph(f'<link href="{url}" color="#050608">{url}</link>', styles["small"]))
    document.build(story)
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
            "status": "ready", "report_kind": "shareable_model_extract", "complete_research": False}
