"""Dedicated research dossier from the accepted structured Trade Idea result.

The renderer neither invents missing research nor calls an LLM. Length and text
coverage are measured on the final PDF; insufficient research remains partial.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from html import escape
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
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V2
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


def _text(value):
    return escape(str(value)).replace("\n", "<br/>")


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
             NextPageTemplate("body"), PageBreak(), _Section("contents", _label("contents", language)),
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


class _CompanyCover(Flowable):
    """Weekly-note identity; every bounded excerpt continues in the full body."""
    def __init__(self, run, result, language, styles, partial_reasons):
        super().__init__()
        self.run, self.result, self.language = run, result, language
        self.styles, self.partial_reasons = styles, partial_reasons
        self.width, self.height = A4

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        c, (w, h) = self.canv, A4
        run, result, language = self.run, self.result, self.language
        band, left = w * .38, 31
        rw, rx = w - band - 45, band + 18
        lw = band - 2 * left
        reg, bold = self.styles["body"].fontName, self.styles["h1"].fontName
        c.saveState()
        c.setFillColor(OBSIDIAN)
        c.rect(0, 0, band, h, fill=1, stroke=0)
        c.setFillColor(AMBER)
        c.rect(band-3, 0, 3, h, fill=1, stroke=0)
        _draw_lockup(c, left, h-65, 21, reg, bold, AMBER, AMBER_DEEP, AMBER, tagline=True)

        def field(value, x, top, width, height, *, size=9, leading=13, color=INK, heavy=False):
            style = ParagraphStyle("coverMeasured", fontName=bold if heavy else reg,
                                   fontSize=size, leading=leading, textColor=color)
            paragraph = Paragraph(_text(value), style)
            _, actual = paragraph.wrap(width, height)
            if actual > height:
                continuation = Paragraph(_text(_label("full_text", language)),
                    ParagraphStyle("coverContinuation", parent=style, fontSize=7, leading=10))
                _, note_height = continuation.wrap(width, height)
                fragments = paragraph.split(width, max(0, height-note_height-5))
                if fragments:
                    first = fragments[0]
                    _, used = first.wrap(width, height)
                    first.drawOn(c, x, top-used)
                    continuation.drawOn(c, x, top-used-note_height-5)
                return top-height
            paragraph.drawOn(c, x, top-actual)
            return top-actual

        light = colors.HexColor("#E1E4EA")
        dim = colors.HexColor("#9CA5B5")
        field("Trade Idea", left, h-116, lw, 25, size=15, leading=19, color=light, heavy=True)
        field("Ricerca societaria" if language == "it" else "Company research", left, h-143, lw, 20,
              size=9, leading=12, color=dim)
        cutoff = str(run.get("cutoff") or result.get("cutoff") or run.get("started_at") or "n.d.")
        field(cutoff[:10], left, h-161, lw, 16, size=8, color=dim)
        y = h-212
        for label, value, height in (
            ("judgment", _label(result["judgment"], language), 48),
            ("dominant_risk", next(iter(result.get("risks") or []), _label("missing", language)), 113),
            ("catalyst", next(iter(result.get("catalysts") or []), _label("missing", language)), 113),
            ("invalidation", next(iter(result.get("invalidation") or []), _label("missing", language)), 113)):
            field(_label(label, language).upper(), left, y, lw, 18, size=8, leading=10, color=AMBER, heavy=True)
            field(value, left, y-23, lw, height, size=10.5 if label == "judgment" else 8.7,
                  leading=13, color=light, heavy=label == "judgment")
            y -= height+42
        field(_label("no_order", language), left, 55, lw, 35, size=6.3, leading=8.4, color=dim)
        identity = run.get("identity") or result.get("identity") or {}
        title = identity.get("name") or result["ticker"]
        y = field(title, rx, h-42, rw, 88, size=25, leading=30, heavy=True)-12
        y = field(" | ".join(str(v) for v in (result["ticker"], identity.get("exchange"), identity.get("currency")) if v),
                  rx, y, rw, 32, size=8, leading=11, color=MUTED)-24
        field("LA TESI" if language == "it" else "THE THESIS", rx, y, rw, 16, size=8, color=BRASS, heavy=True)
        y = field(result.get("summary", ""), rx, y-23, rw, 172, size=10, leading=14)-25
        # A cover graph is an exact view of a cited table, never inferred metrics.
        selected = None
        for key in ("financial_quality", "scenarios", "valuation"):
            section = next((s for s in result.get("dossier", []) if s["key"] == key), {})
            for chart in section.get("charts", []):
                try:
                    drawing = _chart(section, chart, rw, reg)
                except (ValueError, IndexError, KeyError):
                    continue  # Its source table and explicit chart diagnostic remain in the body.
                table = section["tables"][chart["table_index"]]
                selected = drawing, table
                break
            if selected:
                break
        if selected and y > 310:
            drawing, table = selected
            c.setFillColor(OBSIDIAN)
            c.rect(rx, y-35, rw, 35, fill=1, stroke=0)
            field(table["title"], rx+7, y-7, rw-14, 24, size=8, leading=10, color=AMBER, heavy=True)
            drawing.drawOn(c, rx, y-247)
            field(f"{table['unit']} | {table['period']} | {table['source']}", rx, y-255, rw, 42,
                  size=6.8, leading=9, color=MUTED)
            if result.get("data_gaps") and y > 490:
                field("DATI MANCANTI" if language == "it" else "DATA GAPS", rx, y-318, rw, 18,
                      size=8, leading=10, color=BRASS, heavy=True)
                field("\n".join(result["data_gaps"]), rx, y-340, rw, min(83, y-470),
                      size=8.5, leading=12)
        else:
            field("Cosa verificare" if language == "it" else "What to verify", rx, y, rw, 20,
                  size=10, leading=13, heavy=True)
            field("\n".join(result.get("review_conditions", [])), rx, y-25, rw, min(150, max(25, y-135)),
                  size=9, leading=13)
        if self.partial_reasons:
            field(_label("partial", language) + ": " + "; ".join(self.partial_reasons), rx, 111, rw, 58,
                  size=7, leading=9, color=BRASS)
        if run.get("preview"):
            field("ANTEPRIMA · DATI SINTETICI" if language == "it" else "PREVIEW · SYNTHETIC DATA",
                  rx, 45, rw, 15, size=8, leading=10, color=BRASS, heavy=True)
        c.setFont(reg, 6.5)
        c.setFillColor(MUTED)
        c.drawRightString(w-27, 18, "Bellomberg Research · 1")
        c.restoreState()


def _render_company_memo(path, run, result, language, partial_reasons):
    """Versioned company note. The historical renderer and weekly note stay unchanged."""
    styles = _memo_styles()
    reg, bold = styles["body"].fontName, styles["h1"].fontName
    w, h = A4
    margin, bottom, top = 56.7, 51, 65.2
    width = w - 2*margin
    ticker = result["ticker"]
    cutoff = str(run.get("cutoff") or result.get("cutoff") or run.get("started_at") or "n.d.")
    doc = _ResearchDoc(str(path), pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=top, bottomMargin=bottom, title=f"Trade Idea | {ticker}", author="Bellomberg Research", allowSplitting=1)
    doc.section, doc.section_pages, doc.header_sections = "Trade Idea", {}, {}
    header_text = f"TRADE IDEA | {ticker} | {cutoff[:10]}"

    def body_page(canvas, document):
        canvas.saveState()
        _draw_lockup(canvas, margin, h-40, 12, reg, bold, OBSIDIAN, MUTED, AMBER, tagline=False)
        canvas.setFillColor(MUTED)
        canvas.setFont(reg, 7)
        canvas.drawRightString(w-margin, h-39, header_text)
        canvas.setStrokeColor(OBSIDIAN)
        canvas.setLineWidth(.7)
        canvas.line(margin, h-50, w-margin, h-50)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(.5)
        canvas.line(margin, 42.5, w-margin, 42.5)
        footer = "Ricerca Bellomberg · Documento interno" if language == "it" else "Bellomberg Research · Internal document"
        if run.get("preview"):
            footer = "ANTEPRIMA · DATI SINTETICI" if language == "it" else "PREVIEW · SYNTHETIC DATA"
        canvas.drawString(margin, 32, footer)
        canvas.drawRightString(w-margin, 32, ("Pagina " if language == "it" else "Page ") + str(document.page))
        canvas.restoreState()

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[Frame(0, 0, w, h, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)]),
        PageTemplate(id="body", frames=[Frame(margin, bottom, width, h-top-bottom,
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=body_page),
    ])
    p = lambda text, style="body": Paragraph(_text(text), styles[style])
    story = [_CompanyCover(run, result, language, styles, partial_reasons), NextPageTemplate("body"), PageBreak()]

    def label(it, en):
        return it if language == "it" else en

    def group(title, values):
        if values:
            story.append(p(title, "h2"))
            story.extend(p(value) for value in values)

    for section in result.get("dossier", []):
        key = section["key"]
        story.extend([_Section(key, section["title"]), _SectionTitle(section["title"], styles["h1"], width)])
        if key == "executive":
            story.extend([p(_label("judgment", language) + ": " + _label(result["judgment"], language), "h2"), p(result.get("summary", ""))])
            if partial_reasons:
                group(_label("partial", language), partial_reasons)
        elif key == "pm_view":
            story.extend([p(_label("pm_view", language), "h2"),
                          p(result.get("pm_view") or run.get("pm_view") or _label("no_view", language)),
                          p(label("Risposta del comitato", "Committee response"), "h2"), p(result.get("pm_view_response", ""))])
        for paragraph in section.get("paragraphs", []):
            story.append(p(paragraph))
        if key == "executive":
            group(label("Elementi a favore", "Supporting evidence"), result.get("pros"))
            group(label("Elementi contrari", "Counterarguments"), result.get("cons"))
        elif key == "portfolio_risk":
            group(label("Rischi da presidiare", "Risks to monitor"), result.get("risks"))
        elif key == "catalysts":
            group(label("Eventi da seguire", "Events to monitor"), result.get("catalysts"))
        elif key == "scenarios":
            for scenario in result.get("scenarios", []):
                group(scenario["name"], [scenario["analysis"]])
        elif key == "red_team":
            for index, objection in enumerate(result.get("objections", []), 1):
                group(label(f"Obiezione {index}", f"Objection {index}"), [objection["objection"], objection["response"]])
                story.append(p(label("Risolta" if objection["resolved"] else "Obiezione aperta",
                                     "Resolved" if objection["resolved"] else "Open objection"), "small"))
        elif key == "decision":
            group(label("Condizioni di revisione", "Review conditions"), result.get("review_conditions"))
            group(label("Cosa invalida la tesi", "Thesis invalidation"), result.get("invalidation"))
            group(label("Dati mancanti e limiti", "Data gaps and limitations"), result.get("data_gaps"))
            group(label("Domande decisive", "Decisive questions"), result.get("decisive_questions"))
            destination = result.get("destination") or run.get("destination") or {}
            if destination.get("reason"):
                group(label("Destinazione della ricerca", "Research disposition"), [destination["reason"]])
            proposal = result.get("proposal")
            if proposal:
                group(label("Proposta da valutare dal PM", "Proposal for PM review"), [
                    f"{proposal['action']} | {proposal['ticker']} | {proposal['confidence']}",
                    proposal["timing"], proposal["rationale"]])
                if proposal.get("eur_amount") is not None:
                    story.append(p(f"EUR {proposal['eur_amount']}"))
                if proposal.get("sizing_source"):
                    story.append(p(proposal["sizing_source"], "small"))
        for index, table in enumerate(section.get("tables", [])):
            story.append(p(table["title"], "h2"))
            rows = [[p(value, "headcell") for value in table["columns"]]]
            rows.extend([p(cell, "cell") for cell in row] for row in table["rows"])
            count = len(table["columns"])
            widths = [width] if count == 1 else [width*.32] + [width*.68/(count-1)]*(count-1)
            rendered = Table(rows, colWidths=widths, repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
            rendered.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), OBSIDIAN),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PAPER, PALE]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("LINEBELOW", (0, 0), (-1, 0), .8, AMBER)]))
            story.extend([rendered, Spacer(1, 4), p(f"{table['unit']} | {table['period']} | {table['source']}", "small")])
            for chart in section.get("charts", []):
                if chart["table_index"] == index:
                    try:
                        drawing = _chart(section, chart, width, reg)
                    except ValueError as exc:
                        story.append(p(_label("missing", language) + ": " + str(exc), "small"))
                    else:
                        story.append(KeepTogether([drawing, p(f"{table['title']} | {table['unit']} | {table['period']} | {table['source']}", "small")]))

    story.extend([PageBreak(), _Section("sources", _label("sources", language)),
        _SectionTitle(_label("sources", language), styles["h1"], width), p(_label("scope", language), "small")])
    for evidence in result.get("evidence", []):
        group(f"{evidence['source']} | {evidence['as_of']}", [evidence["summary"]])
        story.append(p(f"Riferimento: {evidence['id']}", "small"))
        if evidence.get("url"):
            url = escape(evidence["url"], quote=True)
            story.append(Paragraph(f'<link href="{url}" color="#606973">{url}</link>', styles["small"]))
    story.append(p(label("Tracciabilità delle evidenze", "Evidence references"), "h2"))
    for section in result.get("dossier", []):
        if section.get("evidence_ids"):
            story.append(p(section["title"] + ": " + ", ".join(section["evidence_ids"]), "small"))
    for scenario in result.get("scenarios", []):
        if scenario.get("evidence_ids"):
            story.append(p(label("Scenario: ", "Scenario: ") + scenario["name"] + " | " + ", ".join(scenario["evidence_ids"]), "small"))
    for index, objection in enumerate(result.get("objections", []), 1):
        if objection.get("evidence_ids"):
            story.append(p(label(f"Obiezione {index}: ", f"Objection {index}: ") + ", ".join(objection["evidence_ids"]), "small"))
    if result.get("history_review"):
        story.append(p(label("Confronto con le decisioni precedenti", "Review of earlier decisions"), "h2"))
        for review in result["history_review"]:
            story.extend([p(f"{review['kind']} {review['id']}", "small"), p(review["response"])])
    story.append(p(label("Provenienza del documento", "Document provenance"), "h2"))
    story.append(p(f"Cutoff: {cutoff}\nRun: {run.get('id') or run.get('run_id') or result.get('run_id') or 'n.d.'}", "small"))
    models = run.get("models") or result.get("models") or {}
    for role, model in (models.items() if isinstance(models, dict) else enumerate(models)):
        story.append(p(f"{role}: {model}", "small"))
    doc.build(story)
    return doc.section_pages


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
            yield f"{section['key']}.{index}", value
        for index, table in enumerate(section.get("tables", [])):
            for value in [table["title"], table["source"], table["unit"], table["period"], *table["columns"],
                          *(cell for row in table["rows"] for cell in row)]:
                yield f"{section['key']}.table.{index}", value
    for name, fields in (("scenarios", ("name", "analysis")), ("objections", ("objection", "response")),
                         ("evidence", ("id", "source", "as_of", "summary", "url")), ("history_review", ("response",))):
        for index, row in enumerate(result.get(name, [])):
            for field in fields:
                if row.get(field):
                    yield f"{name}.{index}.{field}", row[field]
    if result.get("destination", {}).get("reason"):
        yield "destination.reason", result["destination"]["reason"]
    for field in ("timing", "rationale", "sizing_source"):
        if (result.get("proposal") or {}).get(field):
            yield "proposal."+field, result["proposal"][field]


def _normalized_text(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).replace("\u00ad", "")).casefold()


def _inspect_company_memo(path, result, section_pages, language):
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    # Page furniture and repeated table headings can interrupt a split paragraph/cell.
    furniture = {"BELLOMBERG", "Ricerca Bellomberg · Documento interno", "Bellomberg Research · Internal document",
                 "ANTEPRIMA · DATI SINTETICI", "PREVIEW · SYNTHETIC DATA"}
    table_heads = {cell.strip() for section in result.get("dossier", []) for table in section.get("tables", []) for cell in table["columns"]}
    body = "\n".join(line for text in pages for line in text.splitlines()
        if line.strip() not in furniture | table_heads and not re.fullmatch(r"(?:Pagina|Page) \d+", line.strip())
        and not line.startswith("TRADE IDEA | "))
    # Headers themselves are also verified against the unfiltered document.
    normalized, raw = _normalized_text(body), _normalized_text("\n".join(pages))
    lost = sorted({name for name, value in _content_fragments(result)
                   if _normalized_text(value) not in normalized and _normalized_text(value) not in raw})
    sections = result.get("dossier", [])
    keys = [section["key"] for section in sections]
    missing = sorted(set(DOSSIER_KEYS) - set(keys))
    empty = [s["key"] for s in sections if not s.get("paragraphs") or any(not p.strip() for p in s["paragraphs"])]
    placeholder = re.compile(r"\s*(?:\[?todo\]?|tbd|n\.?\s?d\.?|da completare|analisi da completare|"
        r"dato non disponibile|dati non disponibili|analisi non disponibile|to be completed|analysis pending|"
        r"not available|placeholder|\.\.\.|…)[.!\s]*", re.I)
    def unfinished_text(value):
        value = re.sub(r"\[src:[^\]]*\]", "", str(value), flags=re.I).strip()
        return not value or bool(placeholder.fullmatch(value))
    unfinished = [s["key"] for s in sections if any(unfinished_text(p) for p in s.get("paragraphs", []))]
    unfinished.extend(field for field in ("summary", "pm_view_response") if unfinished_text(result.get(field, "")))
    for index, objection in enumerate(result.get("objections", [])):
        if unfinished_text(objection.get("objection", "")) or unfinished_text(objection.get("response", "")):
            unfinished.append(f"objections.{index}")
    paragraphs = [p for s in sections for p in s.get("paragraphs", [])]
    same_sections = Counter(_normalized_text("\n".join(s.get("paragraphs", []))) for s in sections)
    repeated = any(text and count > 1 for text, count in same_sections.items())
    reasons = []
    if result.get("judgment") == "incomplete":
        reasons.append("Giudizio incompleto" if language == "it" else "Incomplete judgment")
    for values, it, en in ((missing, "Sezioni mancanti", "Missing sections"),
                           (empty, "Sezioni vuote", "Empty sections"),
                           (unfinished, "Sezioni da completare", "Unfinished sections"),
                           (lost, "Testo originale non integro nel PDF", "Original text missing from PDF")):
        if values:
            reasons.append((it if language == "it" else en) + ": " + ", ".join(values))
    if repeated or len(keys) != len(set(keys)):
        reasons.append("Sezioni ripetute non costituiscono copertura analitica" if language == "it" else
                       "Repeated sections do not constitute analytical coverage")
    source_page = section_pages.get("sources", len(pages)+1)
    first = min((section_pages[k] for k in DOSSIER_KEYS if k in section_pages), default=source_page)
    counts = [{"page": i, "words": len(re.findall(r"\b\w+\b", text)), "analytical": first <= i < source_page}
              for i, text in enumerate(pages, 1)]
    return {"status": "partial" if reasons else "ready", "execution_policy": EXECUTION_POLICY_V2,
        "total_pages": len(pages), "analytical_pages": sum(page["analytical"] for page in counts),
        "analytical_words": sum(len(re.findall(r"\b\w+\b", p)) for p in paragraphs),
        "pages": counts, "section_pages": section_pages, "reasons": reasons,
        "content_integrity": "incomplete" if lost else "complete", "integrity_missing": lost}


def inspect_research_pdf(path, result, section_pages, *, language="it", execution_policy=None):
    """Measure actual selectable text; cover/bibliography/PM quotes do not qualify."""
    if execution_policy == EXECUTION_POLICY_V2:
        return _inspect_company_memo(path, result, section_pages, language)
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
    render = (lambda reasons: _render_company_memo(path, run, result, language, reasons)) if policy == EXECUTION_POLICY_V2 else (
        lambda reasons: _render(path, run, result, valuations, language, reasons))
    sections = render([])
    quality = inspect_research_pdf(path, result, sections, language=language, execution_policy=policy)
    if quality["status"] != "ready":
        sections = render(quality["reasons"])
        quality = inspect_research_pdf(path, result, sections, language=language, execution_policy=policy)
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
