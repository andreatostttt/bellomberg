"""One analytical result for Trade Idea UI, report, delivery and routing.

This validates a research statement, not its economic truth. Operational checks
are supplied independently by the service/store; an LLM cannot approve itself.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DOSSIER_KEYS = (
    "executive", "pm_view", "business", "financial_quality", "valuation",
    "scenarios", "portfolio_risk", "catalysts", "positioning", "red_team", "decision",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False, allow_inf_nan=False)


class RunAuthorization(StrictModel):
    """A single candidate/run grant; it never extends an installation policy."""
    accepted: Literal[True]
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    activities: list[Literal["model_preparation", "committee", "model_revision"]] = Field(min_length=1, max_length=3)
    max_revision_rounds: int = Field(ge=0, le=2)

    @model_validator(mode="after")
    def bounded_activities(self):
        if len(set(self.activities)) != len(self.activities) or "committee" not in self.activities:
            raise ValueError("authorization requires distinct activities and committee")
        if self.max_revision_rounds and "model_revision" not in self.activities:
            raise ValueError("authorization does not include model_revision")
        return self


def validate_run_authorization(authorization, source_qualification):
    try:
        grant = RunAuthorization.model_validate(authorization)
    except ValueError as exc:
        raise ValueError("authorization invalid: " + str(exc)) from exc
    if not isinstance(source_qualification, dict) or source_qualification.get("status") not in ("qualified", "preparation_required", "research_required"):
        raise ValueError("sources are not qualified before authorization")
    if source_qualification.get('status') == 'research_required':
        from bellomberg.valuation.trade_idea_model import validate_research_admission
        from bellomberg.core.research_analysis import is_research_mode
        if is_research_mode(source_qualification):
            if grant.activities != ['committee'] or grant.max_revision_rounds != 0:
                raise ValueError('Research-only authorization permits committee research, not Excel preparation/revision')
        elif 'model_preparation' not in grant.activities:
            raise ValueError('authorization must include model_preparation for primary research and authoring')
        validate_research_admission(source_qualification)
    if source_qualification.get('status') == 'preparation_required':
        from bellomberg.valuation.trade_idea_model import source_fingerprint
        if 'model_preparation' not in grant.activities:
            raise ValueError('authorization must include model_preparation for unqualified historical balances')
        if (source_qualification.get('method_id') != 'operating_fcff'
                or (source_qualification.get('coverage', {}).get('sources') or {}).get('basis') != 'verified_documents_for_historical_preparation'
                or source_qualification.get('fingerprint') != source_fingerprint(source_qualification)):
            raise ValueError('historical preparation admission differs from verified documents')
    if source_qualification.get("fingerprint") != grant.source_fingerprint:
        raise ValueError("authorization differs from the qualified sources fingerprint")
    return grant.model_dump()


class Evidence(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=300)
    as_of: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=6000)
    url: str | None = Field(default=None, max_length=2000)

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        if value is not None and not re.match(r"^https?://[^\s<>]+$", value):
            raise ValueError("Evidence URL must be HTTP(S)")
        return value


class ResearchTable(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    columns: list[str] = Field(min_length=2, max_length=8)
    rows: list[list[str]] = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=500)
    unit: str = Field(min_length=1, max_length=150)
    period: str = Field(min_length=1, max_length=150)

    @model_validator(mode="after")
    def rectangular(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Research table rows must match columns")
        if any(len(cell) > 2500 for row in self.rows for cell in row):
            raise ValueError("Research table cell exceeds limit")
        return self


class ResearchChart(StrictModel):
    """Charts reference table cells, never a second copy of economic numbers."""
    table_index: int = Field(ge=0)
    kind: Literal["bar", "line"]
    label_column: int = Field(ge=0)
    value_columns: list[int] = Field(min_length=1, max_length=4)


class DossierSection(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=250)
    paragraphs: list[str] = Field(min_length=1, max_length=45)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    tables: list[ResearchTable] = Field(default_factory=list, max_length=6)
    charts: list[ResearchChart] = Field(default_factory=list, max_length=4)

    @field_validator("paragraphs")
    @classmethod
    def substantive_text(cls, value):
        if any(not p.strip() or len(p) > 15000 for p in value):
            raise ValueError("Empty or oversized dossier paragraph")
        return value

    @model_validator(mode="after")
    def chart_references(self):
        for chart in self.charts:
            if chart.table_index >= len(self.tables):
                raise ValueError("Chart table is absent")
            width = len(self.tables[chart.table_index].columns)
            if (chart.label_column >= width or any(i < 0 or i >= width for i in chart.value_columns)
                    or chart.label_column in chart.value_columns):
                raise ValueError("Invalid chart column reference")
        return self


class Scenario(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    analysis: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)


class Objection(StrictModel):
    objection: str = Field(min_length=1, max_length=6000)
    response: str = Field(min_length=1, max_length=6000)
    resolved: bool
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)


class Proposal(StrictModel):
    ticker: str = Field(min_length=1, max_length=40)
    action: Literal["BUY", "ADD", "TRIM", "SELL", "HOLD", "HEDGE"]
    eur_amount: float | None = Field(default=None, gt=0)
    timing: str = Field(min_length=1, max_length=2000)
    confidence: Literal["ALTA", "MEDIA", "BASSA"]
    rationale: str = Field(min_length=1, max_length=10000)
    sizing_source: str | None = Field(default=None, max_length=2000)


class HistoryReview(StrictModel):
    kind: Literal["decision", "trade"]
    id: int = Field(gt=0)
    response: str = Field(min_length=1, max_length=6000)


class ValuationReference(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=128)
    generation_id: str = Field(min_length=1, max_length=128)
    valuation_date: str = Field(min_length=1, max_length=40)
    interpretation: str = Field(min_length=1, max_length=6000)


class CommitteeChallenge(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    desk: Literal["macro", "eventdesk", "crypto", "fundamentals", "quant", "options"]
    category: Literal["driver", "source", "arithmetic", "interpretation", "timing", "pm_thesis", "risk"]
    material: bool
    objection: str = Field(min_length=1, max_length=6000)
    evidence_refs: list[str] = Field(max_length=80)
    requested_change: str | None = Field(default=None, max_length=6000)


class CommitteeReview(StrictModel):
    decisive_questions: list[str] = Field(min_length=1, max_length=20)
    objections: list[CommitteeChallenge] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({row.id for row in self.objections}) != len(self.objections):
            raise ValueError("Duplicate committee objection ID")
        return self


class ModelReference(StrictModel):
    snapshot_id: str
    generation_id: str
    valuation_date: str
    workbook_sha256: str


class ModelRevision(StrictModel):
    id: str
    actor: str
    action: Literal["retain", "revise"]
    rationale: str
    before_generation_id: str
    after_generation_id: str | None = None
    status: Literal["proposed", "retained", "applied", "rejected", "blocked"]


class CommitteeReply(StrictModel):
    objection: CommitteeChallenge
    response: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=80)
    state: Literal["open", "answered", "conceded"] = "open"
    model_revision_id: str | None = None


class ModelAudit(StrictModel):
    initial_refs: list[ModelReference]
    final_refs: list[ModelReference]
    revision_log: list[ModelRevision]
    objections: list[CommitteeReply]


class TradeIdeaResult(StrictModel):
    ticker: str = Field(min_length=1, max_length=40)
    judgment: Literal["favorable", "rejected", "watch", "incomplete"]
    summary: str = Field(min_length=1, max_length=12000)
    pm_view_response: str = Field(min_length=1, max_length=12000)
    pros: list[str] = Field(max_length=40)
    cons: list[str] = Field(max_length=40)
    risks: list[str] = Field(max_length=40)
    catalysts: list[str] = Field(max_length=40)
    invalidation: list[str] = Field(max_length=40)
    data_gaps: list[str] = Field(max_length=80)
    review_conditions: list[str] = Field(max_length=40)
    scenarios: list[Scenario] = Field(max_length=12)
    objections: list[Objection] = Field(min_length=1, max_length=40)
    history_review: list[HistoryReview] = Field(default_factory=list, max_length=250)
    valuation_refs: list[ValuationReference] = Field(default_factory=list, max_length=80)
    evidence: list[Evidence] = Field(max_length=250)
    dossier: list[DossierSection] = Field(min_length=1, max_length=25)
    proposal: Proposal | None
    decisive_questions: list[str] = Field(default_factory=list, max_length=30)
    model_review: ModelAudit | None = None

    @model_validator(mode="after")
    def integrity(self):
        ids = [e.id for e in self.evidence]
        keys = [s.key for s in self.dossier]
        if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
            raise ValueError("Duplicate evidence or dossier keys")
        generations = [reference.generation_id for reference in self.valuation_refs]
        if len(set(generations)) != len(generations):
            raise ValueError("Duplicate valuation generation reference")
        for item in [*self.dossier, *self.scenarios, *self.objections]:
            if set(item.evidence_ids) - set(ids):
                raise ValueError("Unresolved evidence reference")
        if self.proposal and self.proposal.ticker != self.ticker:
            raise ValueError("Proposal must refer to the selected ticker")
        if self.judgment == "favorable" and not self.evidence:
            raise ValueError("Favorable judgment requires evidence")
        if self.judgment != "favorable" and self.proposal is not None:
            raise ValueError("Only a favorable judgment may contain an operational proposal")
        return self


def _strict_wire_schema(node):
    """Require every declared field on the wire; nullable fields retain null.

    Reader defaults support archived results, while structured-output providers
    require full object keys even for optional semantic values.
    """
    if isinstance(node, dict):
        normalized = {key: _strict_wire_schema(value) for key, value in node.items()
                      if key != "default"}
        if normalized.get("type") == "object" and "properties" in normalized:
            normalized["required"] = list(normalized["properties"])
            normalized["additionalProperties"] = False
        return normalized
    if isinstance(node, list):
        return [_strict_wire_schema(value) for value in node]
    return node


TRADE_IDEA_RESULT_SCHEMA = _strict_wire_schema(TradeIdeaResult.model_json_schema())
TRADE_IDEA_REVIEW_SCHEMA = _strict_wire_schema(CommitteeReview.model_json_schema())


def validate_committee_review(data):
    if isinstance(data, str):
        data = json.loads(data)
    return CommitteeReview.model_validate(data).model_dump()


def trade_idea_specialist_instructions(desk, analysis_mode=None):
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    if analysis_mode == RESEARCH_ANALYSIS_MODE:
        responsibilities = {
            'macro': 'Explain rates, currencies, inflation and sector transmission into business cash flows and risks.',
            'eventdesk': 'Verify dated official catalysts, management guidance, legal/policy events and timing.',
            'crypto': 'Assess only evidenced digital-asset exposure; explain non-relevance without inventing links.',
            'fundamentals': 'Read official company statements and investor documents. Explain business quality, earnings/cash quality, debt, dilution, explicit assumptions and what would falsify them. Distinguish management guidance, analyst consensus and your independent judgment. Develop bear/base/bull thesis scenarios and uncertainty, without requiring a fair value or DCF driver schema.',
            'quant': 'Measure portfolio risk, liquidity, correlations and common-engine sizing with traceable tools.',
            'options': 'Assess sourced implied expectations, volatility, expiry and permitted structures; declare missing chains.'}
        return ('Research the one accepted Trade Idea candidate as the ' + desk + ' desk.\n'
            + responsibilities.get(desk, 'State the limits of your role and verified evidence.') + '\n'
            'Use official documents and actual tools; all numerical observations carry [src: tool], date, currency and units. '
            'Keep observations, hypotheses and analyst consensus distinct; no mandatory AI price target. '
            'Do not reuse archived Excel/model targets as current estimates or consensus. Any new run estimate must be labeled independent AI, with its date, sources, method and assumptions. '
            'R0/R1 build a documented thesis; share useful peer findings and retain reasoned disagreement. '
            'In R2 review the exact supplied research_ref dossier/thesis hashes and the Red Team objections. '
            'Respond to each material objection addressed to you via respond_trade_idea_objection with its exact ID. '
            'Missing consensus or sources can justify remaining in research; declare limitations and do not fabricate data. '
            'A technical failure or empty response is not a completed analysis. '
            'Do not author, compile, verify, repair or regenerate an Excel; no model generation or driver revision is part of this run. '
            'Only the selected ticker can have an operational proposal, subject to unchanged mandate, quote, risk and sizing checks.')
    responsibilities = {
        "macro": "Assess cash-flow transmission from rates, inflation, currency and sector regime; identify the model drivers affected.",
        "eventdesk": "Verify catalysts, policy/legal events, management commitments and timing from dated primary sources; combine news and politics.",
        "crypto": "Assess relevant digital-asset, liquidity and treasury linkages. A non-relevant crypto domain must be explained; invent no exposure.",
        "fundamentals": "Own and author economic assumptions, accounting classifications, competitive advantage and variant view. Test earnings/cash quality, segments and management history. During R1 build the common model after consulting every other desk on the draft. Read get_candidate_model_inputs, submit complete native driver groups with submit_candidate_model_plan, then compile via get_valuation(ticker). Never delegate numerical assumptions to an automatic preparer. Review the resulting exact workbook with review_candidate_model.",
        "quant": "Assess incremental portfolio risk, correlations, liquidity and common-engine sizing. Distinguish security merit from personal book compatibility.",
        "options": "Assess sourced implied expectations, volatility, skew, expiry and permitted structures. Never assume an option chain or implied probability exists.",
    }
    if desk not in responsibilities:
        # A base-class test or explicitly unsupported role cannot acquire a false doctrine.
        responsibility = "State that this role has no specialized domain and collect only verifiable candidate evidence."
    else:
        responsibility = responsibilities[desk]
    return ("You are the " + desk + " desk researching one accepted Trade Idea candidate.\n"
        + responsibility + "\n"
        "Start with decisive questions: which evidence or economic driver could change your judgment?\n"
        "R0 starts without a workbook: research verifiable facts and decisive economic questions. The Excel is authored inside this run by Fundamentals after the other five R1 analyses.\n"
        "Observed facts, analyst assumptions and software derivations must remain distinct. The PM view is a testable thesis.\n"
        "R0 and the five non-Fundamentals R1 analyses are independent. During Fundamentals model construction, ask_specialist sends an actual question to a peer about explicit draft assumptions; it is not a substitute for a primary source. Consult Macro, EventDesk, Crypto, Quant and Options and record incorporation, disagreement or non-relevance with reasons.\n"
        "Challenge market expectations, loss mechanisms, cash economics and the strongest contrary case.\n"
        "Use only tools actually available, dated sources and [src: tool] for numbers; declare gaps.\n"
        "Only the selected ticker can have an investment proposal. Existing decisions, veti and PM objections retain their IDs.\n"
        "Only Fundamentals can write the common input plan during construction. Every value must be explicit or an explicitly adopted hashed basis driver; missing values are errors. Qualified historical facts remain unchanged. Other desks cannot create or replace an Excel.\n"
        "Once compiled, all six desks discuss its exact snapshot/generation in R2, even if no Red Team objection is addressed to them. Explain concrete drivers, dissent, evidence gaps and domain relevance. Queue motivated revisions with review_candidate_model; a changed generation requires a renewed critique and discussion by every desk before Capo.\n"
        "When a material objection is addressed to your desk, use respond_trade_idea_objection with its exact ID, evidence and unresolved state.\n"
        "A rejected investment can have a valid model. Explain conditions for waiting, invalidation and reassessment.\n"
        "For a qualified exposure_analysis model, assess observed holdings, leverage, terms, costs and liquidity. "
        "Intrinsic fair value is not applicable: never replace it with price, NAV or an invented target.\n")


TRADE_IDEA_RED_TEAM_INSTRUCTIONS = """Research one accepted candidate, its exact common model and the independent desk analyses.
Return a JSON object matching the committee-review schema. Start with decisive questions.
Each material objection has a unique stable ID and the responsible desk among all six domains.
Attack driver assumptions, source identity/freshness, arithmetic, interpretation, timing,
portfolio risk and the PM thesis. Identify evidence_refs from the supplied documents/tools.
State a concrete requested_change when relevant. Retain evidence limits; invent no sources,
market consensus, probabilities or numbers. Do not recommend other ticker candidates.
A generic critique does not replace an objection to a specific driver or conclusion.
For an exposure_analysis model, critique observed holdings, leverage, terms, costs and
liquidity; intrinsic fair value is not applicable and cannot be inferred from price.
"""

RESEARCH_RED_TEAM_INSTRUCTIONS = """Critique the accepted candidate using the exact research_ref dossier/thesis hashes and independent desk analyses supplied.
Return the complete committee-review JSON schema with decisive questions and specific objections, each with a stable ID and responsible desk.
Challenge source identity/freshness, accounting quality, explicit assumptions, thesis scenarios, timing, risk and the PM view.
Use only literal evidence_refs from the supplied catalog. Analyst consensus is a reference, not a mandatory conclusion or fair value.
Missing documents or consensus must remain visible; do not invent numbers, probabilities, source dates or targets.
There is no required workbook, model generation or DCF driver set. Discuss business evidence and the independent thesis.
"""


def parse_capo_json(content):
    """Parse a complete public response; only a single outer fence is optional."""
    if not isinstance(content, str) or len(content) > 900000:
        raise ValueError("Trade Idea result exceeds input limit or is not text")
    body = content.strip()
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", body,
                          flags=re.DOTALL | re.IGNORECASE)
    if fenced is not None:
        body = fenced.group(1)
    return json.loads(body)


def validate_result(data, *, run_id: str, ticker: str, pm_view: str = "") -> dict:
    """Reject cross-ticker output and attach authoritative run identity/view."""
    if isinstance(data, str):
        if len(data) > 900000:
            raise ValueError("Trade Idea result exceeds input limit")
        data = json.loads(data)
    result = TradeIdeaResult.model_validate(data)
    if result.ticker != ticker:
        raise ValueError("Result ticker differs from the accepted run")
    return {**result.model_dump(), "run_id": run_id, "run_type": "trade_idea", "pm_view": pm_view}


CAPO_TRADE_IDEA_INSTRUCTIONS = """Produce one JSON object matching the supplied schema.
The PM view is a thesis to verify, never an instruction or a financial source.
Only the selected instrument may have a proposal; peers and the real portfolio are
context. Reply to every material Red Team objection, retaining unresolved issues.
Separate evidence, assumptions, scenarios and judgment. Every financial number
must preserve its [src: tool] citation, unit, currency and date from the reports.
Do not invent sources, probabilities, price targets, personal sizing or facts.
An investment rejection does not imply an unusable valuation workbook.
An exact qualified exposure_analysis workbook is an observational instrument model.
Analyze holdings, leverage, terms, costs, liquidity and portfolio fit; intrinsic fair
value is not applicable. Never invent it or substitute price, NAV or a corporate model.
Use judgment favorable/rejected/watch/incomplete, independently of technical
delivery. Propose an amount only when the real book and mandate support it;
otherwise proposal is null and explain the information required to decide.

Write a dedicated institutional research dossier, not a concatenation of desk
reports. Target ten or more substantive analytical A4 pages, naturally about
five to seven thousand words when the evidence supports them. Never pad, repeat,
inflate text, fill missing facts or quote the PM to reach a length. Insufficient
research must remain explicitly incomplete and available as partial material.
Use these distinct section keys: executive, pm_view, business, financial_quality,
valuation, scenarios, portfolio_risk, catalysts, positioning, red_team, decision.
Explain non-applicable domains briefly and spend space on material analysis.
Each section needs complete analytical paragraphs, evidence_ids and, where
useful, sourced tables. Table rows contain strings; numerical chart cells use
unambiguous dot-decimal notation. A chart references an existing table only.
Every table must specify title, unit, period and source. Evidence has id, source,
as_of, summary and optional official URL. Include data gaps, disagreements,
catalysts, thesis invalidation and explicit conditions for revisiting the idea.
Review each supplied prior decision/PM feedback and recent trade explicitly in
history_review (kind decision/trade, exact ID, and a reasoned response). Do not
invent IDs or claim to have reviewed unavailable history.
For every usable candidate workbook supplied in context, copy its exact snapshot_id,
generation_id and valuation_date into valuation_refs and explain its assumptions,
cutoff and investment implication in interpretation. Never invent a model reference.
Use only those exact model values in the dossier; distinguish historical model
prices and fair values from current market observations. Empty valuation_refs
means no workbook was analytically reviewed, and that limitation must be explicit.
No extra LLM rewriting or paid follow-up is implied by an incomplete dossier.
"""

CAPO_RESEARCH_INSTRUCTIONS = """Produce the complete investment-research JSON object matching the supplied schema.
Assess the accepted candidate independently; the PM thesis is a hypothesis, not a financial source.
Use the exact sealed research dossier and thesis hashes, all final desk reports, and the Red Team objection/reply ledger.
Distinguish observed business/financial facts, management guidance, analyst consensus, explicit assumptions and your conclusion.
Explain earnings/cash quality, capital structure, dilution, competitive position, bear/base/bull thesis scenarios, risks, catalysts and falsification conditions.
No workbook, model generation, DCF driver set or AI fair value is required. valuation_refs must be [] and model_review null.
Consensus is a reference only: dissent with evidence, never substitute it for a verified AI valuation or invent a target.
Archived Excel/model targets are excluded from current valuation evidence and must not be recycled as consensus or a new estimate.
Any new estimate produced in this run must remain labeled independent AI, with its date, sources, method, assumptions and uncertainty; it never fills a missing market-consensus field.
Preserve [src: tool] citations, source dates, currencies and units for numbers; calculations must originate in reproducible tools.
Write a dedicated institutional research dossier, not a concatenation of desk reports. Target at least ten substantive analytical A4 pages, naturally 5000-7000 words when supported by the available evidence. The report-quality check requires at least 4000 analytical words; develop the evidence, competing interpretations, limitations and decision conditions, never repeat or invent facts to meet it.
Write substantive sections with keys executive, pm_view, business, financial_quality, valuation, scenarios, portfolio_risk, catalysts, positioning, red_team, decision.
The valuation section discusses analyst consensus and expectations with independent interpretation and explicit gaps.
Use tables/charts only when supported by evidence, with source, unit and period. Do not pad or invent data to reach a length.
Missing consensus or documents may justify a completed judgment watch/rejected and proposal=null, with clear research limitations.
Timeouts, empty required reports and corrupt state remain technical incompleteness, not a successful analysis.
Respond to every material objection, preserving dissent and unanswered questions. Explain every supplied historical decision/trade ID in history_review.
A favorable operational proposal still requires the actual mandate, prices, risk and common-engine sizing; otherwise proposal=null.
This is research and a proposal for review, never an automatic trade or evidence of PM approval.
"""

# A new accepted policy selects this prompt; historical requests keep the
# original byte-exact instructions above for paid-response verification.
CAPO_RESEARCH_INSTRUCTIONS_V2 = CAPO_RESEARCH_INSTRUCTIONS.replace(
    "Write a dedicated institutional research dossier, not a concatenation of desk reports. Target at least ten substantive analytical A4 pages, naturally 5000-7000 words when supported by the available evidence. The report-quality check requires at least 4000 analytical words; develop the evidence, competing interpretations, limitations and decision conditions, never repeat or invent facts to meet it.",
    "Write a thorough institutional research memo in coherent analytical prose. Its length follows the evidence and complexity, with no page or word quota. Develop each material question fully: explain the evidence, what it means, competing interpretations, your assumptions, what could falsify them and how that changes the decision. Do not substitute a checklist, terse bullet summary or concatenated desk reports for the analysis. Keep summary and decision concise while developing the business, financial quality, expectations and scenarios in the body. Preserve material dissent and specific evidence gaps. Avoid duplicating whole passages across sections. Use the available output primarily for the complete public memo rather than redoing the already completed committee research.")

# trade-idea-research/3 (PM 03/10/2026): a Capo synthesis comparable to the weekly
# Capo (doctrine, desk conflicts, Red Team answers by ID, per-section grid). Frozen
# once the first /3 run is paid: a text change then requires a new policy version.
CAPO_RESEARCH_INSTRUCTIONS_V3 = CAPO_RESEARCH_INSTRUCTIONS.replace(
    "Write a dedicated institutional research dossier, not a concatenation of desk reports. Target at least ten substantive analytical A4 pages, naturally 5000-7000 words when supported by the available evidence. The report-quality check requires at least 4000 analytical words; develop the evidence, competing interpretations, limitations and decision conditions, never repeat or invent facts to meet it.",
    'Write the committee\'s institutional research memo as the Capo: not a summary and not a concatenation of desk reports. The six final desk reports and the Red Team review are printed in full in the memo annex, so your job is to DECIDE and to make the PM understand why, without losing any material point of context.\nCAPO DOCTRINE: (1) Decide, do not summarize: every section converges on the judgment and on what would change it; an analysis that changes nothing gets one sentence. (2) Source chain: every number carries an exact citation [src: tool_name] (closing bracket right after the tool name) or [evidence: id], plus date, currency and unit; a number without such a citation does not enter. Use dot decimals inside table cells. (3) Name conflicts: when desks disagree, state both positions, which evidence is stronger and why you side with one; never average them away. (4) Answer the Red Team: for every material objection write "Objection <ID> (<desk>): ... Answer: ... Status: answered/conceded/open", consistent with the authoritative ledger; in objections[] set resolved=true only for answered or conceded. Group minor objections so that no dossier section exceeds 30 paragraphs. (5) Calibrated conviction: ALTA only if you would defend it against the Red Team; otherwise MEDIA/BASSA with named unknowns (proposal.confidence uses exactly ALTA/MEDIA/BASSA). (6) Plain language: first mention of a company = full name and ticker; explain every technical metric in the same sentence; no telegraphic bullets in the body.\nSECTION GRID (indicative length; develop more only when evidence supports it, never pad):\n- executive (300-500 words): judgment in two sentences; the decisive question; a table "Desk | Final view | Decisive evidence | Source" with one row for each desk (a missing desk is stated as missing); what would change the judgment.\n- pm_view (250-400): the PM thesis tested point by point: supported / not supported / not testable, with evidence.\n- business (500-800): what the company sells, to whom, unit economics, competitive advantage and its durability, management track record.\n- financial_quality (600-900): revenue and margin trajectory, earnings versus cash conversion, working capital, debt and dilution, accounting flags; a sourced table where data exists.\n- valuation (500-800): market expectations and analyst consensus as reference, what the price implies; VARIANT VIEW: where and why the committee disagrees with consensus, or an explicit statement that no evidenced variant view exists. No fair value or target unless produced and labeled in this run.\n- scenarios (400-700): bear/base/bull as mutually exclusive narratives with drivers, observable signals and falsifiers; probabilities only if sourced, otherwise qualitative and declared as committee judgment.\n- portfolio_risk (400-600): fit with the real book and mandate (correlation, concentration, liquidity, stress), separating security merit from book compatibility; sizing only from the common engine.\n- catalysts (250-400): dated events with source and what each would confirm or falsify; undated events stated as such.\n- positioning (250-400): price action, positioning, options or implied expectations if sourced; declare missing data.\n- red_team (400-1200): the objection/answer ledger above, plus the strongest unresolved dissent and its consequence for the judgment.\n- decision (300-500): judgment, proposal or null with reason, review conditions, invalidation, data gaps ranked by decision value, decisive questions.\nDo not repeat the same analysis in two sections. Keep summary concise and spend the output on the body. Use the available output for the complete memo rather than redoing the already completed committee research.')
assert CAPO_RESEARCH_INSTRUCTIONS_V3 != CAPO_RESEARCH_INSTRUCTIONS
