"""Offline, synthetic editorial fixtures: no issuer research or live side effects."""
from copy import deepcopy
import os
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from bellomberg.reporting.trade_idea_report import build_trade_idea_report, inspect_research_pdf
from trade_idea_fixtures import research_result, run_record

POLICY = "trade-idea-research/2"


def editorial_fixture(judgment="watch"):
    run = run_record()
    run.update(execution_policy=POLICY, analysis_mode="fundamentals_research_v1",
               preview=True, pm_view="La fidelizzazione dei clienti protegge i margini?")
    run["identity"]["name"] = "Officine Aurora"
    run["identity"]["exchange"] = "Società immaginaria"
    result = research_result(judgment)
    result["summary"] = ("Da monitorare: i rinnovi dei programmi industriali sostengono la visibilità, "
        "ma la crescita degli utili non dimostra ancora una migliore conversione in cassa. "
        "Prima di investire servono incassi verificati e ritorni sui nuovi contratti. "
        "Questa è un'anteprima grafica con dati interamente sintetici. [src: archivio_demo]")
    result["pm_view"] = run["pm_view"]
    result["pm_view_response"] = ("La tesi del PM è sostenuta dalla continuità dei programmi, ma non dalla "
        "capacità di trasferire i rincari. La fidelizzazione protegge i volumi, non necessariamente i margini. "
        "[src: archivio_demo]")
    sections = {
        "executive": ("Sintesi e giudizio", "Il comitato mantiene il titolo in osservazione. L'economia dei "
            "programmi esistenti appare solida, mentre quella dei rinnovi deve ancora essere dimostrata. "
            "La domanda decisiva è quanta crescita resti agli azionisti dopo capitale circolante e investimenti."),
        "pm_view": ("La tesi del PM alla prova", "La dipendenza tecnica del cliente è un vantaggio durante "
            "la produzione. Al rinnovo, però, i concorrenti possono qualificarsi e il cliente può chiedere "
            "concessioni. Non estendiamo quindi la protezione dei programmi attuali all'intera vita dell'impresa."),
        "business": ("Business e vantaggio competitivo", "Aurora progetta componenti industriali su specifica. "
            "Il lavoro di sviluppo precede i ricavi e richiede capitale. La riutilizzabilità della progettazione "
            "tra clienti è il fattore che può distinguere una buona nicchia da una fabbrica ad alta intensità di capitale."),
        "financial_quality": ("Bilanci e qualità degli utili", "Nel prospetto sintetico i ricavi aumentano da "
            "100 a 118 milioni di euro e il risultato operativo da 12 a 15 milioni. La conversione in cassa resta "
            "da verificare: non abbiamo il dettaglio degli incassi successivi alla chiusura. Il miglioramento "
            "del margine non basta per concludere che sia aumentato il rendimento del capitale."),
        "valuation": ("Valutazione e ipotesi", "Una valutazione richiede margini normalizzati, investimenti "
            "di mantenimento e fabbisogno di circolante. Mancando l'evidenza sugli incassi, non presentiamo "
            "un target price. Il prezzo osservato e il consensus di mercato non sono disponibili in questo esempio; "
            "non vengono sostituiti con stime del comitato o vecchi modelli."),
        "scenarios": ("Scenari e sensibilità", "Nel caso centrale i programmi proseguono, ma assorbono "
            "reinvestimento. Il caso negativo combina concessioni commerciali e incassi più lenti; quello "
            "positivo richiede rinnovi profittevoli e maggiore riuso dei progetti. Sono ipotesi condizionali, "
            "senza probabilità o rendimenti attribuiti artificialmente."),
        "portfolio_risk": ("Rischio e portafoglio", "Non è stato caricato alcun portafoglio personale "
            "nell'anteprima. Non sono quindi proposti importi, pesi o ordini. In una decisione reale servirebbero "
            "liquidità, esposizioni industriali correlate e tolleranza alla perdita, separati dalla qualità dell'impresa."),
        "catalysts": ("Catalizzatori e calendario", "Il prossimo rinnovo contrattuale può verificare il potere "
            "negoziale, mentre la rendicontazione degli incassi può confermare la qualità degli utili. Non "
            "assegniamo una data all'evento se la fonte non la fornisce. Il progresso utile è una prova economica, "
            "non un generico annuncio commerciale."),
        "positioning": ("Mercato e aspettative", "Prezzo, volumi e posizionamento degli investitori non sono "
            "disponibili nella fixture. Non è possibile dire che la prudenza sia già scontata dal mercato. "
            "L'analisi del business resta distinta dall'attrattiva del titolo a un prezzo non osservato."),
        "red_team": ("Contraddittorio e obiezioni", "Il principale dissenso riguarda la durata del vantaggio "
            "competitivo. I rinnovi potrebbero consumare il valore creato nei programmi esistenti. Il comitato "
            "accetta il rischio e chiede evidenze per coorte contrattuale: la risposta non elimina l'incertezza."),
        "decision": ("Decisione e condizioni di revisione", "La ricerca si conclude con un giudizio di "
            "monitoraggio, senza proposta operativa. Rivalutare la tesi quando gli incassi e i rinnovi "
            "confermino ritorni adeguati dopo lo sviluppo. Concessioni persistenti e capitale non recuperato "
            "costituirebbero invece una ragione per respingerla."),
    }
    for section in result["dossier"]:
        title, prose = sections[section["key"]]
        section.update(title=title, paragraphs=[prose + " [src: archivio_demo]"], tables=[], charts=[])
    financial = next(s for s in result["dossier"] if s["key"] == "financial_quality")
    financial["tables"] = [{"title": "Andamento operativo — dati sintetici", "columns": ["Esercizio", "Ricavi", "Risultato operativo"],
        "rows": [["2023", "100", "12"], ["2024", "110", "14"], ["2025", "118", "15"]],
        "source": "archivio_demo; numeri fittizi per prova grafica", "unit": "Milioni di EUR", "period": "2023–2025"}]
    financial["charts"] = [{"table_index": 0, "kind": "bar", "label_column": 0, "value_columns": [1]}]
    result.update(pros=["Relazioni industriali consolidate nei programmi in produzione. [src: archivio_demo]"],
        cons=["Il potere negoziale ai rinnovi non è dimostrato. [src: archivio_demo]"],
        risks=["Utili in crescita e cassa debole possono convivere. [src: archivio_demo]"],
        catalysts=["Rinnovo contrattuale con ritorni documentati; data non disponibile. [src: archivio_demo]"],
        invalidation=["Concessioni persistenti e sviluppo non recuperato. [src: archivio_demo]"],
        data_gaps=["Mancano incassi successivi alla chiusura, prezzo osservato e consensus di mercato; "
                   "non è formulato alcun prezzo obiettivo."],
        review_conditions=["Verificare gli incassi e il rendimento delle coorti contrattuali dopo lo sviluppo."],
        scenarios=[{"name": "Caso centrale", "analysis": "Programmi in continuità con reinvestimento necessario.", "evidence_ids": ["archive"]}],
        objections=[{"objection": "L'incumbency potrebbe essere solo temporanea.",
                     "response": "Richiedere evidenze sui rinnovi; il punto resta aperto.", "resolved": False, "evidence_ids": ["archive"]}],
        evidence=[{"id": "archive", "source": "archivio_demo", "as_of": "2026-09-10",
            "summary": "Società, fatti e numeri immaginari, usati esclusivamente per questa anteprima grafica offline.",
            "url": "https://example.org/anteprima-sintetica"}],
        destination={"kind": "research", "reason": "Anteprima offline; nessuna allocazione o ricerca reale."})
    return run, result


def extract(path):
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


@pytest.mark.parametrize("judgment", ["watch", "rejected"])
def test_honest_short_research_is_complete_without_page_or_word_target(tmp_path, judgment):
    run, result = editorial_fixture(judgment)
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "short.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["analytical_words"] < 4000
    assert artifact["quality"]["total_pages"] < 10
    text = extract(artifact["path"])
    assert "Mancano incassi successivi" in text
    assert "L'incumbency potrebbe essere solo temporanea." in text
    assert "Non sono" in text
    assert artifact["quality"]["content_integrity"] == "complete"


def test_legacy_short_report_keeps_its_original_gate(tmp_path):
    run, result = editorial_fixture()
    run.pop("execution_policy")
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "legacy.pdf")
    assert artifact["status"] == "partial"
    assert "4000" in artifact["reason"] and "10" in artifact["reason"]


@pytest.mark.parametrize("broken", ["missing", "empty", "placeholder", "cited_placeholder", "repeated", "incomplete", "empty_summary", "empty_reply"])
def test_incomplete_content_is_blocked_without_length_proxy(tmp_path, broken):
    run, result = editorial_fixture()
    if broken == "missing":
        result["dossier"].pop()
    elif broken == "empty":
        result["dossier"][1]["paragraphs"] = []
    elif broken == "placeholder":
        result["dossier"][1]["paragraphs"] = ["Analisi da completare."]
    elif broken == "cited_placeholder":
        result["dossier"][1]["paragraphs"] = ["Dati non disponibili. [src: archivio_demo]"]
    elif broken == "empty_summary":
        result["summary"] = ""
    elif broken == "empty_reply":
        result["objections"][0]["response"] = ""
    elif broken == "repeated":
        for section in result["dossier"]:
            section["paragraphs"] = ["La società richiede ulteriori approfondimenti prima di assumere una decisione."]
    else:
        result["judgment"] = "incomplete"
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / (broken + ".pdf"))
    assert artifact["status"] == "partial"
    assert artifact["reason"]


def test_long_paragraph_and_long_table_cell_keep_the_tail_and_all_claims(tmp_path):
    run, result = editorial_fixture()
    long_prose = " ".join("Osservazione economica " + str(i) + ": verificare margini, incassi e capitale investito." for i in range(190))
    result["dossier"][2]["paragraphs"].append(long_prose + " CODA-PARAGRAFO-VERIFICATA.")
    result["dossier"][3]["tables"].append({"title": "Nota completa", "columns": ["Voce", "Osservazione"],
        "rows": [["Copertura", long_prose + " CODA-CELLA-VERIFICATA."]], "source": "archivio_demo",
        "period": "Anteprima", "unit": "Testo"})
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "long.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    text = extract(artifact["path"])
    for expected in ("CODA-PARAGRAFO-VERIFICATA", "CODA-CELLA-VERIFICATA", result["cons"][0], result["invalidation"][0]):
        assert "".join(expected.split()) in "".join(text.split())


def test_real_pdf_text_loss_is_rejected_even_with_all_source_sections(tmp_path):
    run, result = editorial_fixture()
    path = tmp_path / "missing-text.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with path.open("wb") as handle:
        writer.write(handle)
    quality = inspect_research_pdf(path, result, {s["key"]: 1 for s in result["dossier"]}, execution_policy=POLICY)
    assert quality["status"] == "partial"
    assert quality["content_integrity"] == "incomplete"


def test_preview_artifact_and_source_chart_are_explicitly_synthetic():
    run, result = editorial_fixture()
    path = Path(os.environ["BELLOMBERG_REPORT_DIR"]) / "trade-idea-v2-preview.pdf"
    artifact = build_trade_idea_report(run, result, output_path=path)
    assert artifact["status"] == "ready", artifact["reason"]
    text = extract(path)
    assert "ANTEPRIMA" in text and "DATI SINTETICI" in text
    assert "118" in text and "archivio_demo" in text
    first = PdfReader(path).pages[0].extract_text()
    assert run["id"] not in first and "simulated" not in first


@pytest.mark.parametrize("broken", ["inline_todo", "risk_todo", "catalyst_nd", "scenario_marker", "cut", "recycled"])
def test_placeholders_cuts_and_recycling_anywhere_block_delivery(tmp_path, broken):
    run, result = editorial_fixture()
    if broken == "inline_todo":
        result["dossier"][2]["paragraphs"][0] += " TODO: aggiungere i multipli dei peer."
    elif broken == "risk_todo":
        result["risks"] = ["TODO"]
    elif broken == "catalyst_nd":
        result["catalysts"] = ["n.d."]
    elif broken == "scenario_marker":
        result["scenarios"][0]["analysis"] = "[inserire analisi dello scenario]"
    elif broken == "cut":
        result["dossier"][3]["paragraphs"][0] = "Il multiplo resta inferiore ai peer, ma il"
    else:
        sentence = ("La domanda finale resta stabile e il portafoglio ordini copre i prossimi trimestri "
                    "secondo la documentazione societaria disponibile.")
        for section in result["dossier"][:4]:
            section["paragraphs"].append(sentence)
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / (broken + ".pdf"))
    assert artifact["status"] == "partial", broken
    assert artifact["quality"]["reasons"], broken


def test_markdown_renders_as_typography_and_symbols_print(tmp_path):
    from bellomberg.reporting.pdf_institutional import _register_fonts
    from bellomberg.reporting.trade_idea_report import _symbol_font
    _register_fonts()
    if _symbol_font() is None:
        pytest.skip("no symbol fallback font on this machine: the check mark is then reported as unprintable")
    run, result = editorial_fixture()
    result["dossier"][1]["paragraphs"][0] = (
        "### Punto chiave\n**Margine** in tenuta \u2713 rispetto ai peer.\n- primo fattore\n|---|---|\n"
        + result["dossier"][1]["paragraphs"][0])
    result["dossier"][2]["paragraphs"].append("La quota della clientela di Serie A resta concentrata in Italia.")
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "md.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    text = extract(artifact["path"])
    assert "**" not in text and "###" not in text and "|---" not in text
    assert "Margine" in text and "\u2022 primo fattore" in text
    assert artifact["quality"]["content_integrity"] == "complete"


def test_unprintable_character_is_printed_as_a_declared_code_without_blocking(tmp_path):
    run, result = editorial_fixture()
    result["dossier"][2]["paragraphs"][0] += " \U0001F680 ⚠️"
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "emoji.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    assert "[U+1F680]" in extract(artifact["path"])
    assert any("U+1F680" in notice for notice in artifact["quality"]["notices"])


def test_legitimate_prose_is_not_blocked_and_negative_figures_keep_their_sign(tmp_path):
    run, result = editorial_fixture()
    result["catalysts"] = ["Capital Markets Day 2027 (data TBD dalla societa')."]
    result["dossier"][3]["paragraphs"].append("Il confronto principale e' con Shopify, quotata anche come SHOP.TO")
    result["dossier"][4]["paragraphs"].append("Variazioni trimestrali:\n- 3,2% a/a nel trimestre.\n- volumi stabili.")
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "legit.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    text = extract(artifact["path"])
    assert "- 3,2% a/a" in text and "• volumi stabili" in text
