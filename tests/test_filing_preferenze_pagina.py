"""Fase E: chiavi nuove (opzionali) di filing_preferenze.json."""
import json

import pytest

from bellomberg.storage import filing_preferenze as fpref


def test_file_vecchio_senza_chiavi_nuove(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"esclusi": ["NOVA"], "rifiutati": {}}))
    dati = fpref.carica(p)
    assert dati["esclusi"] == ["NOVA"]
    assert fpref.esiti(dati) == {} and fpref.rifiutati_lei(dati, "NOVA") == frozenset()
    assert fpref.controllo_giornaliero(p) is None
    assert fpref.proposte_scartate(dati) == frozenset() and fpref.scollegati(dati) == {}


def test_file_assente(tmp_path):
    assert fpref.controllo_giornaliero(tmp_path / "manca.json") is None


def test_registra_esito(tmp_path):
    p = tmp_path / "p.json"
    fpref.registra_esito("KORE.MI", "da_confermare", motivo="2 emittenti", candidati=2, path=p)
    fpref.registra_esito("NOVA", "attivato", path=p)
    e = fpref.esiti(fpref.carica(p))
    assert e["KORE.MI"]["esito"] == "da_confermare" and e["KORE.MI"]["motivo"] == "2 emittenti"
    assert e["KORE.MI"]["candidati"] == 2 and e["KORE.MI"]["at"]
    assert e["NOVA"] == {**e["NOVA"], "esito": "attivato", "motivo": None, "candidati": None}


def test_rifiuta_lei_e_cik(tmp_path):
    p = tmp_path / "p.json"
    fpref.rifiuta_lei("NOVA.MI", "999900NOVA0000000001", path=p)
    fpref.rifiuta_lei("NOVA.MI", "999900nova0000000002", path=p)
    fpref.rifiuta_lei("NOVA.MI", "999900NOVA0000000001", path=p)
    fpref.rifiuta("NOVA.MI", "9990001", path=p)
    dati = fpref.carica(p)
    assert fpref.rifiutati_lei(dati, "NOVA.MI") == {"999900NOVA0000000001", "999900NOVA0000000002"}
    assert dati["rifiutati"]["NOVA.MI"] == ["0009990001"]


def test_controllo_giornaliero(tmp_path):
    p = tmp_path / "p.json"
    fpref.imposta_controllo_giornaliero(False, path=p)
    assert fpref.controllo_giornaliero(p) is False
    fpref.imposta_controllo_giornaliero(True, path=p)
    assert fpref.controllo_giornaliero(p) is True
    with pytest.raises(ValueError):
        fpref.imposta_controllo_giornaliero("si", path=p)


def test_proposte_scartate_con_tetto(tmp_path):
    p = tmp_path / "p.json"
    for i in range(205):
        fpref.scarta_proposta(f"{i:064x}", path=p)
    fpref.scarta_proposta(f"{204:064x}", path=p)  # gia' presente: nessun doppione
    lista = fpref.carica(p)["proposte_scartate"]
    assert len(lista) == 200 and lista[-1] == f"{204:064x}" and f"{4:064x}" not in lista
    assert f"{5:064x}" in fpref.proposte_scartate(fpref.carica(p))


def test_scollegati(tmp_path):
    p = tmp_path / "p.json"
    fpref.imposta_scollegato("NOVA", 3, path=p)
    assert fpref.scollegati(fpref.carica(p)) == {"NOVA": 3}
    fpref.annulla_scollegato("NOVA", path=p)
    fpref.annulla_scollegato("ACME", path=p)
    assert fpref.scollegati(fpref.carica(p)) == {}


def test_illeggibile_mai_sovrascritto(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("{rotto")
    for fn in (lambda: fpref.registra_esito("NOVA", "attivato", path=p),
               lambda: fpref.rifiuta_lei("NOVA", "999900NOVA0000000001", path=p),
               lambda: fpref.imposta_controllo_giornaliero(True, path=p),
               lambda: fpref.scarta_proposta("a" * 64, path=p),
               lambda: fpref.imposta_scollegato("NOVA", 1, path=p),
               lambda: fpref.controllo_giornaliero(p)):
        with pytest.raises(ValueError):
            fn()
    assert p.read_text() == "{rotto"


def test_chiave_nuova_di_tipo_sbagliato_e_illeggibile(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"esclusi": [], "rifiutati": {}, "esiti": []}))
    with pytest.raises(ValueError):
        fpref.carica(p)
