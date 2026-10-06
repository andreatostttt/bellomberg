"""Memo PDF for trade-idea-research/4 (impianto M): conviction, horizon, engine band,
review triggers, pillars, variant view, risk exits and bear/base/bull scenarios.

All data is synthetic: ticker ZZTEST.MI, an invented company and invented numbers.
The /2-/3 output is frozen against the renderer measured BEFORE the /4 branches.
"""
import hashlib
import re

import pytest
from pypdf import PdfReader
from reportlab.platypus import Spacer

from bellomberg.core.trade_idea_contract import DOSSIER_KEYS, validate_result
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V4
from bellomberg.reporting import trade_idea_report as report
from bellomberg.reporting.trade_idea_report import build_trade_idea_report
from trade_idea_fixtures import run_record

TICKER = "ZZTEST.MI"

_PROSE = {
    "executive": "Il comitato giudica favorevole l'ingresso in Fonderie Zeta Sintetiche (ZZTEST.MI), societa' inventata "
                 "per questa prova. La tesi poggia sulla tenuta dei contratti pluriennali e sulla disciplina degli investimenti.",
    "pm_view": "La tesi del PM sulla resilienza dei margini trova un sostegno parziale: i contratti indicizzati proteggono "
               "il prezzo medio, mentre il costo dell'energia resta una variabile non coperta del tutto.",
    "business": "La societa' fittizia fonde componenti in lega leggera per clienti industriali europei. Il valore nasce "
                "dalla qualifica tecnica dei pezzi, che rende costoso cambiare fornitore durante un programma.",
    "financial_quality": "I ricavi inventati crescono a una cifra media e il flusso di cassa operativo segue gli utili con "
                         "un ritardo di un trimestre. La leva resta contenuta e non emergono diluizioni rilevanti.",
    "valuation": "Il consenso sintetico sulla crescita dei ricavi e' inferiore alla stima del comitato. Sul margine non "
                 "esiste un consenso con fonte: lo dichiariamo e non lo sostituiamo con altre grandezze.",
    "scenarios": "I tre scenari descrivono percorsi alternativi e mutuamente esclusivi per volumi, prezzi e costo "
                 "dell'energia; le probabilita' sono giudizi del comitato e vengono rese esplicite.",
    "portfolio_risk": "Nel libro sintetico la posizione aggiunge un'esposizione ciclica moderata. La liquidita' del titolo "
                      "inventato consente di entrare per gradi senza muovere il prezzo.",
    "catalysts": "La prossima trimestrale fittizia misurera' la conversione in cassa; il rinnovo del contratto principale "
                 "verifichera' il potere negoziale verso il cliente piu' grande.",
    "positioning": "Il posizionamento degli investitori sul titolo immaginario appare neutrale; mancano dati sulle opzioni "
                   "e lo dichiariamo come limite dell'analisi.",
    "red_team": "Il Red Team ha contestato la durata del vantaggio competitivo. Il comitato accetta l'obiezione come rischio "
                "da monitorare e la collega ai criteri di uscita della sezione 3.",
    "decision": "Proponiamo un ingresso graduale entro la fascia del motore comune, con revisione alla prossima "
                "trimestrale e uscita se il margine scende sotto la soglia indicata.",
}


_NOMI = {"bear": "pessimistico", "base": "centrale", "bull": "ottimistico"}


def v4_result(*, below_starter=False):
    sections = [{"key": key, "title": key.replace("_", " ").capitalize(), "paragraphs": [_PROSE[key]],
                 "evidence_ids": ["ev-filing"], "tables": [], "charts": []} for key in DOSSIER_KEYS]
    sections[3]["tables"] = [{"title": "Dati operativi inventati", "columns": ["Voce", "Valore"],
                              "rows": [["Pezzi consegnati", "1.250"], ["Margine operativo", "12,5%"]],
                              "source": "synthetic_filing", "unit": "Pezzi; percentuale", "period": "Esercizio fittizio"}]
    scenario = lambda name, prob, target, driver, falsifier: {
        "name": name, "probability_pct": prob, "price_target": target, "currency": "EUR",
        "analysis": f"Analisi dello scenario {_NOMI[name]} per la societa' inventata: volumi, prezzi e cassa "
                    f"evolvono secondo ipotesi dichiarate del comitato.",
        "evidence_ids": ["ev-filing"], "drivers": [driver], "falsifiers": [falsifier],
        "method": f"Multiplo sintetico sugli utili inventati, scenario {_NOMI[name]}."}
    data = {
        "ticker": TICKER, "judgment": "favorable",
        "summary": "Tesi favorevole su una societa' inventata: contratti pluriennali e cassa in miglioramento.",
        "pm_view_response": "La resilienza dei margini e' sostenuta solo in parte dalle evidenze sintetiche.",
        "pros": [], "cons": [], "risks": [], "invalidation": [],
        "catalysts": ["Trimestrale fittizia del 15/11/2026."], "data_gaps": [],
        "review_conditions": [],
        "scenarios": [scenario("bear", 25, 8.4, "Volumi in calo presso il cliente principale",
                               "Ordini stabili per due trimestri"),
                      scenario("base", 50, 11.2, "Rinnovi a prezzi invariati", "Margine sotto la soglia di uscita"),
                      scenario("bull", 25, 14.6, "Nuovo programma aggiudicato", "Gara persa contro un concorrente")],
        "objections": [{"objection": "Il vantaggio competitivo potrebbe essere temporaneo.",
                        "response": "Rischio accettato e collegato ai criteri di uscita.", "resolved": True,
                        "evidence_ids": ["ev-filing"]}],
        "history_review": [], "valuation_refs": [],
        "evidence": [{"id": "ev-filing", "source": "[src: synthetic_filing] bilancio inventato", "as_of": "2026-09-01",
                      "summary": "Bilancio di una societa' inventata, usato solo per questa prova.", "url": None},
                     {"id": "ev-quote", "source": "[src: synthetic_quote] quotazione inventata", "as_of": "2026-09-30",
                      "summary": "Quotazione inventata per la prova del memo.", "url": None}],
        "dossier": sections, "decisive_questions": ["La cassa seguira' gli utili?"], "model_review": None,
        "proposal": {"ticker": TICKER, "action": "BUY", "eur_amount": 2400.0, "timing": "Ingresso graduale in due tranche",
                     "confidence": "MEDIA", "rationale": "Tesi favorevole con rischi dichiarati.",
                     "sizing_source": "Motore comune sintetico",
                     "sizing": {"amount_eur": 2400.0, "band_min_eur": 1800.0, "band_max_eur": 3100.0,
                                "basis": "Importo centrale nella fascia sintetica del motore.", "below_starter": False}},
        "conviction": "MEDIA", "horizon": {"months": 18, "label": "Diciotto mesi di prova"},
        "summary_evidence_ids": ["ev-filing"], "pm_view_evidence_ids": ["ev-filing"],
        "pillars": [{"title": "Contratti pluriennali indicizzati", "thesis": "I contratti inventati trasferiscono i rincari.",
                     "evidence": "Il bilancio fittizio mostra prezzi medi in linea con i costi.",
                     "risk": "Un rinnovo senza indicizzazione romperebbe il pilastro.", "evidence_ids": ["ev-filing"]},
                    {"title": "Cassa che segue gli utili", "thesis": "La conversione in cassa migliora con un trimestre di ritardo.",
                     "evidence": "Il flusso operativo inventato cresce con gli utili.",
                     "risk": "Un aumento del magazzino annullerebbe il miglioramento.", "evidence_ids": ["ev-quote"]}],
        "variant_view": [{"metric": "Crescita dei ricavi", "period": "2027", "unit": "%", "consensus": 4.2,
                          "committee": 6.5, "rationale": "Stima del comitato: nuovi programmi gia' qualificati.",
                          "evidence_ids": ["ev-filing"]},
                         {"metric": "Margine operativo", "period": "2027", "unit": "%", "consensus": None,
                          "committee": 18.25, "rationale": "Stima del comitato senza consenso con fonte.",
                          "evidence_ids": ["ev-filing"]}],
        "risk_exits": [{"risk": "Perdita del cliente principale", "threshold": "Quota del cliente sotto il 20%",
                        "action": "exit", "evidence_ids": ["ev-filing"]},
                       {"risk": "Compressione dei margini", "threshold": "Margine operativo sotto il 9% per due trimestri",
                        "action": "reduce", "evidence_ids": ["ev-quote"]},
                       {"risk": "Ritardo del nuovo programma", "threshold": "Avvio oltre giugno 2027",
                        "action": "review", "evidence_ids": []}],
        "review_triggers": [{"kind": "date", "date": "2026-11-15", "price_level": None, "condition": None,
                             "what": "Trimestrale: verifica della conversione in cassa."},
                            {"kind": "price", "date": None, "price_level": 9.1, "condition": None,
                             "what": "Rivedere la tesi sotto questo livello."},
                            {"kind": "condition", "date": None, "price_level": None,
                             "condition": "Rinnovo del contratto principale", "what": "Conferma del potere negoziale."}],
    }
    if below_starter:
        data["proposal"]["eur_amount"] = 900.0
        data["proposal"]["sizing"] = {"amount_eur": 900.0, "band_min_eur": 1800.0, "band_max_eur": 900.0,
                                      "basis": "Fascia vuota: il massimo ammesso e' sotto lo starter sintetico.",
                                      "below_starter": True}
    result = validate_result(data, run_id="run-zz", ticker=TICKER, pm_view="I margini reggono?",
                             execution_policy=EXECUTION_POLICY_V4)
    result["destination"] = {"kind": "dcn", "reason": "Prova sintetica senza allocazione reale."}
    return result


def v4_run(*, price=10.4):
    run = run_record()
    run.update(ticker=TICKER, execution_policy=EXECUTION_POLICY_V4, analysis_mode="fundamentals_research_v1",
               preview=True, pm_view="I margini reggono?")
    run["identity"] = {"name": "Fonderie Zeta Sintetiche", "exchange": "Borsa immaginaria", "currency": "EUR"}
    run["facts"] = {"currency": "EUR", "quote": {"price": price, "date": "2026-09-30", "tool": "synthetic_quote"}}
    if price is None:
        run["facts"]["quote"] = {}
    return run


def _text(path, pages=None):
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in (reader.pages if pages is None else reader.pages[:pages]))


def _flat(text):
    return "".join(text.split())


def _has(text, *needles):
    flat = _flat(text)
    missing = [n for n in needles if _flat(n) not in flat]
    assert not missing, missing


def _build(tmp_path, name, *, language="it", result=None, run=None):
    return build_trade_idea_report(run or v4_run(), result or v4_result(), output_path=tmp_path / name,
                                   language=language)


def test_v4_memo_is_ready_complete_and_prints_every_new_field(tmp_path):
    artifact = _build(tmp_path, "v4.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"
    text = _text(artifact["path"])
    _has(text,
         # 1. Raccomandazione
         "Convinzione Media", "Diciotto mesi di prova (18 mesi)",
         "2.400 EUR (fascia del motore 1.800–3.100 EUR)", "Base del dimensionamento",
         "Importo centrale nella fascia sintetica del motore.",
         "Trigger di revisione", "Tipo", "Cosa avviene", "15/11/2026", "9,10 EUR", "Rinnovo del contratto principale",
         "Trimestrale: verifica della conversione in cassa.",
         # 2. Tesi
         "2.2 Contratti pluriennali indicizzati", "2.3 Cassa che segue gli utili", "Tesi. I contratti inventati",
         "Prova. Il bilancio fittizio", "Rischio. Un rinnovo senza indicizzazione",
         "Le nostre stime contro il consensus", "Comitato (stima)", "Crescita dei ricavi (%)", "4,2", "6,5", "18,25",
         "Stima del comitato senza consenso con fonte.",
         # 3. Rischi, uscite, scenari
         "Rischi e criteri di uscita", "Perdita del cliente principale", "Uscire", "Ridurre", "Rivedere",
         "Pessimistico", "Ottimistico", "8,40 EUR", "-19,2%", "+7,7%", "+40,4%",
         "Probabilità e prezzi obiettivo sono stime del comitato, non dati osservati.",
         "Variazione calcolata sul prezzo di 10,40 EUR del 30/09/2026",
         "Fonti della sezione: synthetic_filing",
         # Dossier scenari
         "Fattori", "Cosa la falsifica", "Volumi in calo presso il cliente principale", "Gara persa contro un concorrente",
         "Multiplo sintetico sugli utili inventati, scenario centrale.")
    # The variant-view row without a sourced consensus prints n.d., never a substitute.
    row = _flat(text)[_flat(text).find("Margineoperativo(%)"):][:40]
    assert row.startswith("Margineoperativo(%)2027n.d.18,25"), row
    # A /4 cell is printed as written: "1.250" stays 1.250 (the /3 cell rule would make it 1,250).
    assert "1.250" in text and "1,250" not in text
    assert "[src:" not in text and "bear" not in text.lower() and "bull" not in text.lower()


def test_v4_section_sources_come_from_evidence_ids(tmp_path):
    text = " ".join(_text(_build(tmp_path, "src.pdf")["path"]).split())
    thesis = text[text.find("2. Tesi in sintesi"):text.find("3. Rischi")]
    end = text.find("4. Sintesi e giudizio")  # fixed /4 section title (PM 04/10)
    assert end >= 0
    risks = text[text.find("3. Rischi"):end]
    # Section 2 cites ev-filing and ev-quote (second pillar); section 3 cites both through the exits.
    for block in (thesis, risks):
        assert "synthetic_filing" in block and "synthetic_quote" in block, block[-300:]


def test_v4_english_labels(tmp_path):
    artifact = _build(tmp_path, "en.pdf", language="en")
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"
    _has(_text(artifact["path"]), "Conviction Medium", "(18 months)", "2,400 EUR (engine band 1,800–3,100 EUR)",
         "Sizing basis", "Review triggers", "What happens", "2026-11-15", "9.10 EUR",
         "Our estimates against consensus", "Committee (estimate)", "Risks and exit criteria", "Exit", "Reduce",
         "Bear", "Bull", "+7.7%", "-19.2%", "Probabilities and price targets are committee estimates",
         "Drivers", "What would falsify it", "Thesis.", "Evidence.", "Risk.")


def test_v4_below_starter_declares_the_empty_band(tmp_path):
    artifact = _build(tmp_path, "starter.pdf", result=v4_result(below_starter=True))
    assert artifact["status"] == "ready", artifact["reason"]
    _has(_text(artifact["path"], pages=2), "900 EUR: sotto lo starter del motore: fascia vuota",
         "(minimo 1.800, massimo 900 EUR)", "Fascia vuota: il massimo ammesso e' sotto lo starter sintetico.")


def test_v4_missing_price_prints_nd_change_and_says_why(tmp_path):
    artifact = _build(tmp_path, "noprice.pdf", run=v4_run(price=None))
    assert artifact["status"] == "ready", artifact["reason"]
    text = _text(artifact["path"])
    _has(text, "Variazione n.d.: prezzo di mercato non disponibile nella run", "8,40 EUR")
    assert "-19,2%" not in text and "+7,7%" not in text


def test_v4_target_in_another_currency_is_not_compared():
    assert report._m_scenario_change(11.2, 10.4, "EUR", "EUR") == pytest.approx(7.6923, abs=1e-4)
    assert report._m_scenario_change(11.2, 10.4, "USD", "EUR") is None
    assert report._m_scenario_change(11.2, None, "EUR", "EUR") is None
    assert report._m_scenario_change(11.2, 0, "EUR", "EUR") is None


def test_integrity_fails_when_pillars_are_not_printed(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_m_pillar_flowables", lambda *args, **kwargs: [])
    artifact = _build(tmp_path, "nopillars.pdf")
    assert artifact["quality"]["content_integrity"] == "incomplete"
    assert artifact["status"] == "partial"
    assert any(name.startswith("pillars.") for name in artifact["quality"]["integrity_missing"])


def test_integrity_fails_when_variant_view_is_not_printed(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_m_variant_table", lambda *args, **kwargs: Spacer(1, 1))
    artifact = _build(tmp_path, "novariant.pdf")
    assert artifact["quality"]["content_integrity"] == "incomplete"
    assert any(name.startswith("variant_view.") for name in artifact["quality"]["integrity_missing"])


def test_every_new_text_field_is_an_integrity_fragment():
    names = {name for name, _ in report._content_fragments(v4_result())}
    for expected in ("horizon.label", "pillars.1.thesis", "pillars.1.evidence", "pillars.1.risk", "pillars.0.title",
                     "variant_view.0.metric", "variant_view.1.rationale", "scenarios.0.drivers.0",
                     "scenarios.2.falsifiers.0", "scenarios.1.method", "risk_exits.2.threshold", "risk_exits.0.risk",
                     "review_triggers.2.condition", "review_triggers.0.what", "proposal.sizing.basis"):
        assert expected in names, expected
    # The wire key is a translated label, not original text.
    assert not any(name.endswith(".name") and name.startswith("scenarios.") for name in names)


def test_v4_placeholder_in_a_pillar_blocks_delivery(tmp_path):
    result = v4_result()
    result["pillars"][0]["thesis"] = "TODO: completare la tesi."
    artifact = _build(tmp_path, "todo.pdf", result=result)
    assert artifact["status"] == "partial"
    assert any("pillars.0.thesis" in reason for reason in artifact["quality"]["reasons"])


# ------------------------------------------------------------------ /2-/3 frozen output
# Measured on the renderer BEFORE the /4 branches (HEAD e3d2622 + working tree of 04/10/2026),
# with tests/test_trade_idea_report_v2.editorial_fixture. Whitespace-insensitive.
# Rimisurati il 05/10/2026 (voce 9, impianto A): la fixture ha UNA lacuna, che ora apre la prima pagina
# («LIMITI DI QUESTA ANALISI») e in §14 lascia un rinvio. Confronto vecchio/nuovo codice sulla stessa
# fixture: le sole differenze sono il blocco, il rinvio e le righe di pagina spostate (sezioni 1-3 invariate,
# v. FROZEN_SECTIONS_1_3 qui sotto, che resta identico).
FROZEN_FULL_TEXT_SHA256 = {
    ("it", "watch"): "20a4e90c4427560b796c515438b97a1dbd9dedfea2bdad62c53e3b249949dc61",
    ("it", "favorable"): "c8142eecfee22994cd5aeef22eb2595a78ae7e76b30db4f73b864e18da158b6a",
    ("en", "watch"): "3dbac1ca07a6c9860ae880d8e148dffa771d97ab94f184779f9d091657349cc1",
    ("en", "favorable"): "96939fc26ab539420d2f2250d67fbe6e41cc1e04d0fea05ee9d5f4658f731aff",
}
FROZEN_SECTIONS_1_3 = {
    ("it", "favorable"): (
        "1. Raccomandazione Azione Buy \u00b7 Favorevole Importo 0 EUR: nessun importo proposto. Cassa del book: "
        "n.d. Convinzione MEDIA Orizzonte After independent checks Destinazione In ricerca: nessuna proposta "
        "in DCN Condizioni per rivedere (a) Verificare gli incassi e il rendimento delle coorti contrattuali "
        "dopo lo sviluppo. 2. Tesi in sintesi 2.1 Da monitorare: i rinnovi dei programmi industriali sostengono "
        "la visibilit\u00e0, ma la crescita degli utili non dimostra ancora una migliore conversione in cassa. "
        "Prima di investire servono incassi verificati e ritorni sui nuovi contratti. Questa \u00e8 un'anteprima "
        "grafica con dati interamente sintetici. 2.2 Elementi a favore (a) Relazioni industriali consolidate "
        "nei programmi in produzione. 2.3 Elementi contrari (a) Il potere negoziale ai rinnovi non \u00e8 dimostrato. "
        "Fonti della sezione: archivio_demo. 3. Rischi, criteri di uscita e catalizzatori 3.1 Rischi principali "
        "(a) Utili in crescita e cassa debole possono convivere. 3.2 Criteri di invalidazione (a) Concessioni "
        "persistenti e sviluppo non recuperato. 3.3 Catalizzatori datati (a) Rinnovo contrattuale con ritorni "
        "documentati; data non disponibile. Fonti della sezione: archivio_demo."),
    ("en", "watch"): (
        "1. Recommendation Action Watch: no purchase in this run Amount 0 EUR: no amount proposed. Book cash: "
        "n.d. Conviction n.d. (no actionable proposal) Horizon n.d. (no position) Routing In research: no "
        "DCN proposal Conditions to revisit (a) Verificare gli incassi e il rendimento delle coorti contrattuali "
        "dopo lo sviluppo. 2. Thesis in brief 2.1 Da monitorare: i rinnovi dei programmi industriali sostengono "
        "la visibilit\u00e0, ma la crescita degli utili non dimostra ancora una migliore conversione in cassa. "
        "Prima di investire servono incassi verificati e ritorni sui nuovi contratti. Questa \u00e8 un'anteprima "
        "grafica con dati interamente sintetici. 2.2 Supporting evidence (a) Relazioni industriali consolidate "
        "nei programmi in produzione. 2.3 Counterarguments (a) Il potere negoziale ai rinnovi non \u00e8 dimostrato. "
        "Section sources: archivio_demo. 3. Risks, exit criteria and catalysts 3.1 Main risks (a) Utili in "
        "crescita e cassa debole possono convivere. 3.2 Invalidation criteria (a) Concessioni persistenti "
        "e sviluppo non recuperato. 3.3 Dated catalysts (a) Rinnovo contrattuale con ritorni documentati; "
        "data non disponibile. Section sources: archivio_demo."),
}


def _legacy(tmp_path, policy, language, judgment):
    from test_trade_idea_report_v2 import editorial_fixture
    run, result = editorial_fixture(judgment)
    run["execution_policy"] = policy
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / f"{policy[-1]}{language}{judgment}.pdf",
                                       language=language)
    assert artifact["status"] == "ready", artifact["reason"]
    return _text(artifact["path"])


# Page furniture drawn on every page by the canvas (running header from page 2, footer,
# page number): where the page break falls depends on the installed fonts (Georgia/Arial
# on Windows, DejaVu on a Linux runner), so it is not part of the frozen section text.
# Only WHOLE lines equal to the furniture are dropped; the section prose is compared intact.
_PAGE_FURNITURE = {"ANTEPRIMA · DATI SINTETICI", "PREVIEW · SYNTHETIC DATA",
                   "Bellomberg | Documento interno riservato al Comitato d'investimento",
                   "Bellomberg | Internal document reserved to the Investment Committee"}


def _without_page_furniture(text):
    return "\n".join(line for line in text.splitlines()
                     if line.strip() not in _PAGE_FURNITURE
                     and not re.fullmatch(r"(?:Pagina|Page) \d+", line.strip())
                     and not re.fullmatch(r"(?:Memo d'investimento|Investment memo) \| .+ \| \d{2}/\d{2}/\d{4}",
                                          line.strip()))


@pytest.mark.parametrize("policy", ["trade-idea-research/2", "trade-idea-research/3"])
@pytest.mark.parametrize("language, judgment", sorted(FROZEN_SECTIONS_1_3))
def test_legacy_sections_1_to_3_are_unchanged(tmp_path, policy, language, judgment):
    text = " ".join(_without_page_furniture(_legacy(tmp_path, policy, language, judgment)).split())
    start = text.find("1. Raccomandazione" if language == "it" else "1. Recommendation")
    assert text[start:text.find("4. Sintesi e giudizio")].strip() == FROZEN_SECTIONS_1_3[(language, judgment)]


@pytest.mark.parametrize("policy", ["trade-idea-research/2", "trade-idea-research/3"])
@pytest.mark.parametrize("language, judgment", sorted(FROZEN_FULL_TEXT_SHA256))
def test_legacy_full_text_is_unchanged(tmp_path, policy, language, judgment):
    from bellomberg.reporting.pdf_institutional import _register_fonts
    _register_fonts()
    if report._serif_faces() is None:
        pytest.skip("Georgia not installed: page breaks differ from the frozen measurement")
    text = _legacy(tmp_path, policy, language, judgment)
    assert hashlib.sha256(_flat(text).encode()).hexdigest() == FROZEN_FULL_TEXT_SHA256[(language, judgment)]


def test_v4_branches_stay_closed_for_a_legacy_result():
    from test_trade_idea_report_v2 import editorial_fixture
    _, result = editorial_fixture("favorable")
    assert not report._is_memo_v4(result)
    assert not any(name.startswith(("pillars.", "variant_view.", "risk_exits.", "review_triggers.", "horizon."))
                   for name, _ in report._content_fragments(result))


def test_legacy_cells_are_still_localized(tmp_path):
    """The /4 switch is explicit: a /3 dot-decimal cell still prints in the Italian convention."""
    from test_trade_idea_report_v2 import editorial_fixture
    run, result = editorial_fixture("watch")
    run["execution_policy"] = "trade-idea-research/3"
    financial = next(s for s in result["dossier"] if s["key"] == "financial_quality")
    financial["tables"][0]["rows"].append(["2026", "121.5", "15.75"])
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "cells.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    text = _text(artifact["path"])
    assert "121,5" in text and "15,75" in text and "121.5" not in text


def _tiny_result():
    result = v4_result()
    result["scenarios"][0]["price_target"] = 0.0012
    result["variant_view"][0].update(committee=0.004, consensus=0.0031)
    result["review_triggers"][1]["price_level"] = 0.0009
    return result


def test_tiny_structured_numbers_keep_significant_digits(tmp_path):
    artifact = _build(tmp_path, "tiny.pdf", result=_tiny_result())
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"
    text = _text(artifact["path"])
    _has(text, "0,0012 EUR", "0,0009 EUR", "0,004", "0,0031")
    assert "0,00 EUR" not in text


def test_integrity_rereads_structured_numbers_at_their_rounding(tmp_path, monkeypatch):
    # A renderer that rounds to fixed decimals prints the tiny values as 0: delivery is refused.
    # (_fmt itself no longer prints a non-zero value as 0, so the mutant formats with fixed decimals.)
    monkeypatch.setattr(report, "_m_sig", lambda value, language, decimals, *, suffix="", digits=3:
                        f"{value:.{decimals}f}".replace(".", ",") + suffix)
    artifact = _build(tmp_path, "rounded.pdf", result=_tiny_result())
    assert artifact["quality"]["content_integrity"] == "incomplete"
    missing = set(artifact["quality"]["integrity_missing"])
    assert {"scenarios.0.price_target", "variant_view.0.committee", "variant_view.0.consensus",
            "review_triggers.1.price_level"} <= missing, missing


def test_integrity_catches_a_lost_scenario_table(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_m_scenario_table", lambda *args, **kwargs: [])
    artifact = _build(tmp_path, "noscenarios.pdf")
    assert artifact["quality"]["content_integrity"] == "incomplete"
    missing = set(artifact["quality"]["integrity_missing"])
    assert {"scenarios.0.price_target", "scenarios.2.price_target", "scenarios.1.probability_pct"} <= missing, missing


def test_integrity_catches_a_lost_engine_band(tmp_path, monkeypatch):
    original = report._m_lines

    def no_band(run, result, facts, language):
        action, rows = original(run, result, facts, language)
        return action, [(k, v.split(" (")[0] if k == "Importo" else v) for k, v in rows]
    monkeypatch.setattr(report, "_m_lines", no_band)
    artifact = _build(tmp_path, "noband.pdf")
    missing = set(artifact["quality"]["integrity_missing"])
    assert {"proposal.sizing.band_min_eur", "proposal.sizing.band_max_eur"} <= missing, missing
