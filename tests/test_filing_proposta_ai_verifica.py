"""Fase C, Task 3: verifica locale della proposta con le regole della pipeline (nessuna AI)."""
import re

import pytest

from bellomberg.market_data import filing_proposta_ai as fp
from tests.filing_pdf_sintetici import semestrale_kore_it, semestrale_nova

URL = "https://ir.nova.example/reports/h1-2026.pdf"
PERIODO = r"(?P<mesi>six) months ended (?P<fine>\d{1,2} [A-Za-z]+ \d{4})"


def _proposta(**sezioni):
    base = {"tipo": "semestrale", "lingua": "en", "periodo": PERIODO, "emittente": r"Nova\s+AG",
            "prova_tipo": r"Half-Year Financial Report", "scartate": [],
            "sezioni": {"prospettive": {"inizio": r"Outlook for the \d{4} fiscal year",
                                        "fine": r"Risks and opportunities"},
                        "rischi": {"inizio": r"Risks and opportunities",
                                   "fine": r"Notes to the Financial Statements"}}}
    if sezioni:
        base["sezioni"] = sezioni
    return base


def _verifica(tmp_path, contenuto, proposta, nome="Nova AG"):
    path = tmp_path / "h1.pdf"
    path.write_bytes(contenuto)
    profilo = fp.profilo_ir("NOVA.DE", nome=nome, ir_urls=[URL], proposta=proposta,
                            sha256="0" * 64, modello="nova/flash", lingua_rilevata="en")
    return path, fp.verifica_proposta(path, url=URL, profilo=profilo, proposta=proposta)


def test_profilo_ir_valido_per_il_negozio():
    from bellomberg.storage.filing_store import _validate_profile
    p = fp.profilo_ir("NOVA.DE", nome="Nova AG", ir_urls=[URL], proposta=_proposta(),
                      sha256="a" * 64, modello="nova/flash", lingua_rilevata="en")
    _validate_profile("NOVA.DE", p, 168)
    assert p["fonti"] == ["ir"] and p["ir_urls"] == [URL]
    assert p["emittente_id"] == "EMITTENTE:Nova AG"
    assert p["sezioni_salta_indice"] is True and p["periodo_regola"] == "piu_recente"
    assert p["origine_collegamento"] == "proposta_ai"
    assert p["proposta_ai"]["sha256"] == "a" * 64
    assert p["verifica"]["periodo"] == PERIODO and p["tipo"] == "semestrale"


def test_proposta_buona_sezioni_verificate_con_testo_letterale(tmp_path):
    path, out = _verifica(tmp_path, semestrale_nova(), _proposta())
    assert out["salvabile"] is True, out
    assert {s["nome"] for s in out["verificate"]} == {"prospettive", "rischi"}
    assert out["scartate"] == []
    assert out["periodo"] == {"inizio": "2025-10-01", "fine": "2026-03-31"}
    rischi = next(s for s in out["verificate"] if s["nome"] == "rischi")
    assert rischi["pagine"][0] == 6  # fino alla riga di fine: puo' toccare la pagina dopo
    assert "Export restrictions" in rischi["anteprima"]
    assert set(out["profilo"]["sezioni"]) == {"prospettive", "rischi"}


def test_pagina_divisoria_e_indice_a_capo_saltati(tmp_path):
    _, out = _verifica(tmp_path, semestrale_nova(indice_con_numeri_a_capo=True, divisoria=True), _proposta())
    assert {s["nome"] for s in out["verificate"]} == {"prospettive", "rischi"}, out


def test_regex_sulla_testata_ripetuta_scartata(tmp_path):
    proposta = _proposta(gestione={"inizio": r"Review of results of operations",
                                   "fine": r"Interim Group Management Report"},
                         rischi={"inizio": r"Risks and opportunities", "fine": r"Notes to the Financial Statements"})
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    motivi = {s["nome"]: s["motivo"] for s in out["scartate"]}
    assert "ripetuta" in motivi["gestione"]
    assert [s["nome"] for s in out["verificate"]] == ["rischi"]
    assert "gestione" not in out["profilo"]["sezioni"]


def test_inizio_ambiguo_fine_assente_regex_non_valida(tmp_path):
    proposta = _proposta(
        ambigua={"inizio": r".*", "fine": r"Risks and opportunities"},
        senza_fine={"inizio": r"Risks and opportunities", "fine": r"Capitolo inesistente"},
        rotta={"inizio": r"Outlook (", "fine": r"Risks"},
        backref={"inizio": r"(Outlook)\1", "fine": r"Risks"},
        prospettive={"inizio": r"Outlook for the \d{4} fiscal year", "fine": r"Risks and opportunities"})
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    motivi = {s["nome"]: s["motivo"] for s in out["scartate"]}
    assert set(motivi) == {"ambigua", "senza_fine", "rotta", "backref"}
    assert "ambigua" in motivi["ambigua"] or "ripetuta" in motivi["ambigua"]
    assert "finale assente" in motivi["senza_fine"]
    assert "non valida" in motivi["rotta"] and "non valida" in motivi["backref"]
    assert [s["nome"] for s in out["verificate"]] == ["prospettive"]


def test_scarti_del_parsing_riportati(tmp_path):
    proposta = _proposta()
    proposta["scartate"] = [{"nome": "lunga", "motivo": "regex oltre 300 caratteri"}]
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert {"nome": "lunga", "motivo": "regex oltre 300 caratteri"} in out["scartate"]


@pytest.mark.parametrize("periodo,frammento", [
    (r"(?P<mesi>three) months ended (?P<fine>\d{1,2} [A-Za-z]+ \d{4})", "period"),
    (r"for the year (?P<fine>\d{4})", "period"),
])
def test_periodo_sbagliato_ripiego_dichiarato(tmp_path, periodo, frammento):
    """La regola del modello che non regge (comparativo, durata incoerente, assente) cede il posto
    a una regola di ripiego dichiarata; senza ripiego valido nulla e' salvabile (test della prova reale)."""
    proposta = _proposta()
    proposta["periodo"] = periodo
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True
    assert out["regole"]["periodo"]["origine"] == "ripiego"
    assert any(frammento in m.lower() for m in out["avvisi"]), out["avvisi"]


def test_periodo_dal_codice_senza_avviso(tmp_path):
    """REV_G2b A1: il modello non propone piu' il periodo; le regole del codice non sono un ripiego."""
    proposta = _proposta()
    proposta["periodo"] = None
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True and out["regole"]["periodo"]["origine"] == "ripiego"
    assert not any(m.startswith("periodo") for m in out["avvisi"]), out["avvisi"]


def test_emittente_di_ripiego_dal_nome(tmp_path):
    proposta = _proposta()
    proposta["emittente"] = None
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True, out
    assert out["profilo"]["verifica"]["emittente"]


def test_regola_di_verifica_non_valida_sostituita_dal_ripiego(tmp_path):
    proposta = _proposta()
    proposta["prova_tipo"] = "Half-Year ("
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True, out
    re.compile(out["profilo"]["verifica"]["tipo"])


def test_italiano_con_puntini_nell_indice(tmp_path):
    proposta = {"tipo": "semestrale", "lingua": "it", "scartate": [],
                "periodo": r"dal (?P<inizio>\d{1,2} \w+ \d{4}) al (?P<fine>\d{1,2} \w+ \d{4})",
                "emittente": r"Kore", "prova_tipo": r"relazione finanziaria semestrale",
                "sezioni": {"prospettive": {"inizio": "EVOLUZIONE PREVEDIBILE DELLA GESTIONE",
                                            "fine": "OPERAZIONI CON PARTI CORRELATE"}}}
    path = tmp_path / "kore.pdf"
    path.write_bytes(semestrale_kore_it())
    profilo = fp.profilo_ir("KORE.MI", nome="Kore S.p.A.", ir_urls=[URL], proposta=proposta,
                            sha256="0" * 64, modello="m", lingua_rilevata="it")
    out = fp.verifica_proposta(path, url=URL, profilo=profilo, proposta=proposta)
    assert out["salvabile"] is True, out
    assert out["periodo"] == {"inizio": "2026-01-01", "fine": "2026-06-30"}


@pytest.mark.parametrize("testo", ["NOVA & KORE-TECH reports", "Nova and Kore Tech reports",
                                   "Nova Kore-Tech reports", "nova  &  kore tech"])
def test_regex_nome_tollera_e_commerciale_e_trattini(testo):
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    assert re.search(_regex_nome("Nova & Kore-Tech S.p.A."), testo, re.I)


def test_regex_nome_resta_un_nome():
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    assert not re.search(_regex_nome("Nova & Kore-Tech S.p.A."), "Nova reports; Kore separately", re.I)
