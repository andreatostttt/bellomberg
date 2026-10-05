"""Fase C, Task 7 (opzione B): variante IR infrannuale nei profili ESEF, aggiunta solo con conferma."""
import copy

import pytest

from bellomberg.market_data import esef, filing_pipeline
from bellomberg.market_data import filing_proposta_ai as fp
from bellomberg.market_data.filing_profili_auto import profilo_esef
from bellomberg.storage.filing_store import _validate_profile, unisci_variante
from tests.filing_esef_sintetici import LEI_NOVA, json_nova, riga_indice
from tests.filing_pdf_sintetici import semestrale_nova
from tests.test_filing_pipeline import rete

URL26 = "https://ir.nova.example/reports/h1-2026.pdf"
URL25 = "https://ir.nova.example/reports/h1-2025.pdf"
PROPOSTA = {"tipo": "semestrale", "lingua": "en", "scartate": [],
            "periodo": r"(?P<mesi>six) months ended (?P<fine>\d{1,2} [A-Za-z]+ \d{4})",
            "emittente": r"Nova\s+AG", "prova_tipo": "Half-Year Financial Report",
            "sezioni": {"prospettive": {"inizio": r"Outlook for the \d{4} fiscal year",
                                        "fine": "Risks and opportunities"},
                        "rischi": {"inizio": "Risks and opportunities", "fine": "Notes to the Financial Statements"}}}


@pytest.fixture(autouse=True)
def _contatto(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    monkeypatch.setattr(esef, "attendi_esef", lambda **k: None)


def _esef():
    return profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en")


def _ir(tipo="semestrale", urls=(URL26, URL25)):
    proposta = {**PROPOSTA, "tipo": tipo}
    return fp.profilo_ir("NOVA.MI", nome="Nova AG", ir_urls=list(urls), proposta=proposta, sha256="b" * 64,
                         modello="nova/flash", lingua_rilevata="en")


def _misto():
    return fp.variante_ir(_esef(), _ir())


def test_profilo_misto_valido_e_varianti_unite():
    p = _misto()
    _validate_profile("NOVA.MI", p, 24)
    assert p["esef_modo"] == "blocchi" and p["fonti"] == ["esef"]
    annuale, semestrale = (unisci_variante(p, v) for v in p["varianti"])
    assert annuale["esef_modo"] == "blocchi" and annuale["tipo"] == "annuale" and annuale["fonti"] == ["esef"]
    assert "esef_modo" not in semestrale
    assert semestrale["fonti"] == ["ir"] and semestrale["ir_urls"] == [URL26, URL25]
    assert semestrale["tipo"] == "semestrale" and set(semestrale["sezioni"]) == {"prospettive", "rischi"}
    assert semestrale["verifica"]["periodo"] == PROPOSTA["periodo"]
    assert semestrale["sezioni_salta_indice"] is True


def test_variante_dello_stesso_tipo_sostituita():
    p = fp.variante_ir(_misto(), _ir(urls=[URL26]))
    assert [v["tipo"] for v in p["varianti"]] == ["annuale", "semestrale"]
    assert p["varianti"][1]["ir_urls"] == [URL26]


def test_variante_ir_annuale_rifiutata():
    with pytest.raises(ValueError, match="annuale"):
        fp.variante_ir(_esef(), _ir(tipo="annuale"))


@pytest.mark.parametrize("guasto", ["due_esef", "ir_senza_sezioni", "fonti_sec", "esef_semestrale"])
def test_profili_misti_non_validi(guasto):
    p = _misto()
    if guasto == "due_esef":
        p["varianti"].append({"tipo": "trimestrale"})
    elif guasto == "ir_senza_sezioni":
        p["varianti"][1]["sezioni"] = {}
    elif guasto == "fonti_sec":
        p["varianti"][1]["fonti"] = ["sec"]
    else:
        p["varianti"][0]["tipo"] = "semestrale"
        p["varianti"][1]["tipo"] = "trimestrale"
    with pytest.raises(ValueError):
        _validate_profile("NOVA.MI", p, 24)


def test_esef_senza_varianti_invariato():
    p = _esef()
    _validate_profile("NOVA.MI", p, 24)
    assert unisci_variante(p, {"tipo": "annuale"})["esef_modo"] == "blocchi"


def test_pipeline_mista_annuale_esef_identico_e_numeri_dall_esef(monkeypatch, tmp_path):
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    monkeypatch.setattr(esef, "indice_depositi", lambda lei, **k: {"righe": righe, "origine": "rete", "motivo": None})
    pagine = {righe[0]["json_url"]: json_nova(2024), righe[1]["json_url"]: json_nova(2025),
              URL26: semestrale_nova(2026), URL25: semestrale_nova(2025, prosa_outlook=[
                  "We expect revenue to stay flat in the 2025 fiscal year.",
                  "The Segment Result Margin should reach around 20 percent."])}
    rete(monkeypatch, pagine)
    solo_esef = filing_pipeline.esegui_profilo(_esef(), archivio=tmp_path / "a1", oggi="2026-10-03")
    misto = filing_pipeline.esegui_profilo(_misto(), archivio=tmp_path / "a2", oggi="2026-10-03")
    assert misto["variante"] == "semestrale", misto["motivi"]
    assert misto["coppia"]["dopo"]["metadati"]["periodo_fine"] == "2026-03-31"
    assert (misto["confronto_corrente"] or misto["confronto_storico"])["cambiamenti"]
    annuale = next(v for v in misto["varianti"] if v["tipo"] == "annuale")
    assert annuale["confronto"]["cambiamenti"] == solo_esef["confronto_corrente"]["cambiamenti"]
    assert misto["numeri"]["stato"] == "ok" and misto["numeri"]["variante"] == "annuale"
    assert misto["numeri"]["voci"] == solo_esef["numeri"]["voci"]


def test_impronta_profilo_misto_none_esef_puro_invariato(monkeypatch):
    from bellomberg.market_data.filing_impronta import impronta_depositi
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    indice = lambda lei, **k: {"righe": righe, "origine": "rete", "motivo": None}
    assert impronta_depositi(_misto(), indice_fn=indice, oggi="2026-10-03") is None
    assert impronta_depositi(_esef(), indice_fn=indice, oggi="2026-10-03")["fonte"] == "esef"


def test_contesto_etichetta_la_variante_ir():
    from bellomberg.agents.filing_context import _fonte
    p = _misto()
    assert _fonte(p, "semestrale").startswith("IR ir.nova.example")
    assert _fonte(p, "annuale") == f"ESEF LEI {LEI_NOVA}"
    assert _fonte(_ir(), "semestrale").startswith("IR ir.nova.example")


# ---------------------------------------------------------------- accettazione

from tests.test_filing_routes_ai import SHA, env as rotte_env  # noqa: E402,F401  (fixture delle route)


@pytest.fixture
def rotte(rotte_env):
    return rotte_env


def _accetta(env, **extra):
    return env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA, **extra})


def test_accept_su_esef_chiede_conferma_poi_aggiunge_la_variante(rotte):
    rotte.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL26})
    esistente = profilo_esef("NOVA.DE", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en")
    rotte.service.store.set_profile("NOVA.DE", esistente, interval_hours=24)
    r = _accetta(rotte)
    assert r.status_code == 409 and "variante" in r.json()["detail"]
    r = _accetta(rotte, aggiungi_variante=True)
    assert r.status_code == 200, r.text
    salvato = rotte.service.store.get_profile("NOVA.DE")
    p = salvato["profile"]
    assert p["esef_modo"] == "blocchi" and [v["tipo"] for v in p["varianti"]] == ["annuale", "semestrale"]
    assert salvato["interval_hours"] == 24  # il controllo ESEF resta giornaliero
    assert r.json()["esito"] == "variante_aggiunta"


def test_aggiungi_variante_solo_su_profili_esef(rotte):
    rotte.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL26})
    assert _accetta(rotte, aggiungi_variante=True).status_code == 422  # nessun profilo
    from bellomberg.market_data.filing_profili_auto import profilo_sec
    rotte.service.store.set_profile("NOVA.DE", profilo_sec("NOVA.DE", cik="0009990001", sec_ticker="NOVA",
                                                           nome="Nova AG", origine="manuale", forme={"10-K"}))
    assert _accetta(rotte, aggiungi_variante=True).status_code == 422
    assert _accetta(rotte, aggiungi_variante=True, sostituisci=True).status_code == 422
