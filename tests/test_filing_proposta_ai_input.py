"""Fase C, Task 1: input locale per la proposta AI da un PDF IR (nessuna rete, nessun modello)."""
import hashlib

import pytest

from tests.filing_pdf_sintetici import pdf, semestrale_kore_it, semestrale_nova


def _scrivi(tmp_path, contenuto, nome="report.pdf"):
    path = tmp_path / nome
    path.write_bytes(contenuto)
    return path


def _input(path):
    from bellomberg.market_data.filing_proposta_ai import estrai_input
    return estrai_input(path)


def _testi(righe):
    return [r["testo"] for r in righe]


def test_indice_e_righe_candidate_con_pagina(tmp_path):
    path = _scrivi(tmp_path, semestrale_nova())
    out = _input(path)
    assert out["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert out["pagine"] == 7
    assert [p["pagina"] for p in out["indice"]] == [2]
    assert "Outlook for the 2026 fiscal year 5" in out["indice"][0]["testo"]
    testi = _testi(out["righe"])
    assert "Outlook for the 2026 fiscal year" in testi
    assert "Risks and opportunities" in testi
    riga = next(r for r in out["righe"] if r["testo"] == "Risks and opportunities")
    assert riga["pagina"] == 6
    assert "p.6 | Risks and opportunities" in out["testo_input"]


def test_intestazioni_ripetute_righe_di_tabella_e_prosa_escluse(tmp_path):
    out = _input(_scrivi(tmp_path, semestrale_nova()))
    testi = _testi(out["righe"])
    assert not any(t.startswith("Nova AG Half-Year Financial Report 31 March") for t in testi)
    assert "Interim Group Management Report" not in testi  # ripetuta in testa a 5 pagine
    assert "Revenue 7,475 7,014 7" not in testi
    assert not any(t.startswith("We expect revenue") for t in testi)
    assert out["escluse_ripetute"] >= 1


def test_righe_uniche_e_in_ordine_di_documento(tmp_path):
    out = _input(_scrivi(tmp_path, semestrale_nova()))
    testi = _testi(out["righe"])
    assert len(testi) == len(set(testi))
    pagine = [r["pagina"] for r in out["righe"]]
    assert pagine == sorted(pagine)


def test_indice_senza_puntini_con_numero_a_capo(tmp_path):
    out = _input(_scrivi(tmp_path, semestrale_nova(indice_con_numeri_a_capo=True)))
    assert [p["pagina"] for p in out["indice"]] == [2]
    assert "Outlook for the 2026 fiscal year" in out["indice"][0]["testo"]


def test_italiano_con_puntini_e_lingua_rilevata(tmp_path):
    out = _input(_scrivi(tmp_path, semestrale_kore_it()))
    assert out["lingua_rilevata"] == "it"
    assert [p["pagina"] for p in out["indice"]] == [2]
    assert "EVOLUZIONE PREVEDIBILE DELLA GESTIONE" in _testi(out["righe"])
    assert _input(_scrivi(tmp_path, semestrale_nova(), "en.pdf"))["lingua_rilevata"] == "en"


def test_senza_parola_indice_si_usano_le_prime_pagine_con_testo(tmp_path):
    out = _input(_scrivi(tmp_path, pdf([["Acme plc", "Interim report"], ["Outlook", "We expect growth."],
                                        ["Risks", "Risks are unchanged."]])))
    assert [p["pagina"] for p in out["indice"]] == [1, 2]


def test_tetto_di_caratteri_con_troncamento_dichiarato(tmp_path, monkeypatch):
    from bellomberg.market_data import filing_proposta_ai as fp
    pagine = [["Contents"] + [f"Section heading number {i:03d} of Nova" for i in range(40)]]
    pagine += [[f"Chapter Title {chr(65 + i)}{chr(65 + j)} Kore"] for i in range(10) for j in range(10)]
    monkeypatch.setattr(fp, "MAX_CARATTERI_INPUT", 2_000)
    out = fp.estrai_input(_scrivi(tmp_path, pdf(pagine)))
    assert out["caratteri_input"] <= 2_000
    assert out["troncato"] > 0
    assert "Chapter Title AA Kore" in _testi(out["righe"])  # si tolgono righe dal fondo


def test_pdf_senza_testo_chiede_ocr(tmp_path):
    with pytest.raises(ValueError, match="OCR"):
        _input(_scrivi(tmp_path, pdf([[], [], [], [], [], ["Nova AG"]])))


def test_documento_non_pdf_rifiutato(tmp_path):
    path = _scrivi(tmp_path, b"<html><body><h1>Outlook</h1><p>Text.</p></body></html>", "report.html")
    with pytest.raises(ValueError, match="PDF"):
        _input(path)
