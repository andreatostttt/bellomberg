"""Righe delle tabelle scritte dai modelli: mai spezzate fra due pagine, e l'ispettore
ricompone una riga spezzata per forza (piu' alta di una pagina) senza allentare l'integrita'.

Difetto del 06/10/2026: la riga 8 del registro obiezioni e' stata tagliata dal salto pagina;
nel testo estratto si sono interposti pie' di pagina, intestazione ripetuta e l'altra colonna,
e il PDF e' uscito «non qualificato: Testo originale non integro». Dati interamente sintetici.
"""
from __future__ import annotations

import pytest
from pypdf import PdfReader

from bellomberg.reporting import trade_idea_report as report
from bellomberg.reporting.trade_idea_report import build_trade_idea_report
from test_trade_idea_report_v2 import editorial_fixture

_PAROLE = ("margine", "rinnovo", "contratto", "cassa", "cliente", "programma", "ritorno", "capitale",
           "domanda", "prezzo", "verifica", "coorte", "calendario", "esposizione", "fornitura", "volume")


def _testo(seme, parole):
    """Prosa sintetica deterministica; ogni testo ha un marcatore suo (ZZOBJ...)."""
    corpo = " ".join(_PAROLE[(seme * 7 + i * 3) % len(_PAROLE)] for i in range(parole))
    return f"ZZOBJ{seme:02d} {corpo}. Fine della nota sintetica {seme}."


def _pagine(path):
    return [page.extract_text() or "" for page in PdfReader(str(path)).pages]


def _intera_su_una_pagina(testo, pagine):
    stampato = report._normalized_text(testo)
    return any(stampato in report._normalized_text(pagina, rendered=True) for pagina in pagine)


def test_le_righe_delle_obiezioni_non_si_spezzano_fra_pagine(tmp_path):
    run, result = editorial_fixture()
    result["objections"] = [{"objection": _testo(i, 60), "response": _testo(i + 50, 95), "resolved": i % 2 == 0}
                            for i in range(14)]
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "righe.pdf")
    pagine = _pagine(artifact["path"])
    spezzate = [f"objections.{i}.{campo}" for i, voce in enumerate(result["objections"])
                for campo in ("objection", "response") if not _intera_su_una_pagina(voce[campo], pagine)]
    assert spezzate == []
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"


def _riga_alta(result):
    # Una risposta piu' alta di un'intera pagina: non puo' andare intera da nessuna parte.
    parole = _testo(77, 2200).split(" ")
    lunga = " ".join([*parole[:len(parole) // 2], "QQCENTRALE", *parole[len(parole) // 2:]])
    assert lunga.count("QQCENTRALE") == 1
    result["objections"] = [{"objection": _testo(3, 40), "response": lunga, "resolved": False}]
    return lunga


def test_una_riga_piu_alta_di_una_pagina_si_stampa_intera_e_qualifica(tmp_path):
    run, result = editorial_fixture()
    lunga = _riga_alta(result)
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "alta.pdf")
    pagine = _pagine(artifact["path"])
    # La riga attraversa davvero piu' pagine (altrimenti il test non prova nulla).
    assert not _intera_su_una_pagina(lunga, pagine)
    assert artifact["quality"]["content_integrity"] == "complete", artifact["quality"]["integrity_missing"]
    assert artifact["status"] == "ready", artifact["reason"]


@pytest.mark.parametrize("tolta", ["QQCENTRALE", "sintetica 77."])
def test_una_parola_davvero_mancante_in_una_riga_spezzata_fa_ancora_fallire(tmp_path, monkeypatch, tolta):
    run, result = editorial_fixture()
    _riga_alta(result)
    originale = report._shown

    def perde_una_parola(value, sources, language, cell=False, localize_cells=True):
        testo = originale(value, sources, language, cell=cell, localize_cells=localize_cells)
        return testo.replace(tolta, "", 1) if "ZZOBJ77" in str(value) else testo
    monkeypatch.setattr(report, "_shown", perde_una_parola)
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "persa.pdf")
    assert artifact["quality"]["content_integrity"] == "incomplete"
    assert "objections.0.response" in artifact["quality"]["integrity_missing"]
    assert artifact["status"] == "partial"


# --- ricomposizione nell'ispettore: unita' sul testo estratto ---------------------------------

def _righe(*testi):
    return [report._normalized_text(t, rendered=True) for t in testi]


def test_ricompone_una_cella_spezzata_con_piede_intestazione_e_altra_colonna_in_mezzo():
    pagine = [_righe("Testo precedente", "QQ-08: primo turno,", "tre finestre datate", "che vietano il",
                     "Concesso. Le date", "giustificano la", "Risolta"),
              _righe("N.", "Obiezione", "Risposta", "mercato di previsione", "scaduto.", "mancata messa", "al lavoro.")]
    obiezione = report._normalized_text("QQ-08: primo turno, tre finestre datate che vietano il mercato di "
                                        "previsione scaduto.")
    risposta = report._normalized_text("Concesso. Le date giustificano la mancata messa al lavoro.")
    assert report._ricomposto_fra_pagine(obiezione, pagine)
    assert report._ricomposto_fra_pagine(risposta, pagine)


def test_una_parola_mancante_al_taglio_non_si_ricompone():
    pagine = [_righe("QQ-08: primo turno,", "tre finestre datate", "Risolta"),
              _righe("N.", "mercato di previsione", "scaduto.")]
    assert not report._ricomposto_fra_pagine(
        report._normalized_text("QQ-08: primo turno, tre finestre datate PERSA mercato di previsione scaduto."), pagine)
    # la parola finale persa
    assert not report._ricomposto_fra_pagine(
        report._normalized_text("QQ-08: primo turno, tre finestre datate mercato di previsione scaduto. Coda."), pagine)


def test_i_pezzi_devono_stare_su_pagine_consecutive_e_a_righe_intere():
    a, b, c = _righe("alfa beta gamma"), _righe("altro testo"), _righe("delta epsilon")
    testo = report._normalized_text("alfa beta gamma delta epsilon")
    assert not report._ricomposto_fra_pagine(testo, [a, b, c])  # pagina 1 e 3: non consecutive
    assert report._ricomposto_fra_pagine(testo, [a, c])
    # meta' riga non e' un taglio di pagina (reportlab spezza fra righe)
    assert not report._ricomposto_fra_pagine(report._normalized_text("beta gamma delta epsilon"),
                                             [_righe("alfa beta gamma"), _righe("delta epsilon")])
    assert not report._ricomposto_fra_pagine(report._normalized_text("alfa beta delta epsilon"),
                                             [_righe("alfa beta gamma"), _righe("delta epsilon")])
    assert not report._ricomposto_fra_pagine(report._normalized_text("gamma delta"),
                                             [_righe("alfa beta gamma"), _righe("delta epsilon")])
    # tre pagine: una riga piu' alta di una pagina intera
    assert report._ricomposto_fra_pagine(report._normalized_text("uno due tre quattro cinque"),
                                         [_righe("x", "uno"), _righe("due tre"), _righe("quattro cinque", "y")])


# --- strumento di rigenerazione: --apply si ferma dove la consegna normale non riscrive ---------

def _strumento():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "tools" / "ops" / "rigenera_pdf_trade_idea.py"
    spec = importlib.util.spec_from_file_location("rigenera_pdf_trade_idea_sotto_test", path)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_apply_dichiara_manifest_sigillato_pacchetto_salvato_e_pdf_non_qualificato():
    ostacoli = _strumento().ostacoli_apply
    libero = {"manifest_sigillato": False, "delivery_json_presente": False, "email": "ready"}
    assert ostacoli(libero, True) == []
    assert any("NON e' qualificato" in o for o in ostacoli(libero, False))
    assert any("sigillato" in o for o in ostacoli({**libero, "manifest_sigillato": True}, True))
    assert any("delivery.json" in o for o in ostacoli({**libero, "delivery_json_presente": True}, True))
    for stato in ("accepted", "uncertain", "sending"):
        assert any("non si rimanda" in o for o in ostacoli({**libero, "email": stato}, True))
