"""Contratto ATTUALE di get_valuation: archiviato (cantiere ZERO ROSSI, Z1, 05/10/2026).

Dal commit 1326312 (03/10, Fundamentals/Trade Idea solo ricerca, decisione PM «zero Excel»)
`chat_tools.dispatch('get_valuation', ...)` risponde subito
{'ok': False, 'status': 'archived', 'code': 'excel_archived', 'error': <motivo>} PRIMA del ramo che
acquisiva, calcolava, salvava la tesi e pubblicava (chat_tools.py, ramo dopo il `try`). Il censimento
di raggiungibilita' (scratchpad ZR, rapporti/Z4_raggiungibilita.md) dice che nessun percorso vivo
raggiunge piu' quel ramo: i test che lo provavano sono in archive/private/attic/tests_excel_archiviato_20261005/.

Qui si prova cio' che resta vero: la risposta e' ESATTAMENTE quella dichiarata (niente 'data', niente
numeri, niente riuso), e nessun ingresso del ramo archiviato viene toccato — ne' motore, ne'
acquisizione, ne' provider/preparatore passati dal chiamante, ne' DB valutazioni, ne' file su disco.
"""
from pathlib import Path

ARCHIVIATO = {
    "ok": False, "status": "archived", "code": "excel_archived",
    "error": ("Generazione Excel archiviata. Usa analisi societaria, bilanci, guidance e consensus; "
              "i modelli storici sono consultabili in Archivio Excel."),
}


def blinda_ramo_archiviato(monkeypatch):
    """Sostituisce ogni ingresso del ramo archiviato con una spia; torna la lista delle chiamate.

    Se il dispatch tornasse a entrare nel ramo, la lista non sarebbe vuota (e la spia solleva, cosi'
    il ramo non puo' nemmeno proseguire in silenzio)."""
    import sys
    from bellomberg.valuation import dcf_engine, sector_analysis
    chiamate = []

    def spia(nome):
        def chiamata(*args, **kwargs):
            chiamate.append(nome)
            raise AssertionError("ramo get_valuation archiviato raggiunto: " + nome)
        return chiamata

    for modulo, nome in ((dcf_engine, "generate_valuation"), (dcf_engine, "_generate_valuation_legacy"),
                         (sector_analysis, "prepare_sector_analysis"),
                         (sector_analysis, "revise_sector_analysis"),
                         (sector_analysis, "validate_bundle"),
                         (sector_analysis, "default_sector_providers")):
        monkeypatch.setattr(modulo, nome, spia(nome))
    # Il DB valutazioni si raggiunge via `from bellomberg.storage.memory_db import MemoryDB` dentro il
    # ramo: la spia va sul modulo che quell'import risolve ADESSO (alcune fixture lo sostituiscono).
    monkeypatch.setattr(sys.modules["bellomberg.storage.memory_db"], "MemoryDB", spia("MemoryDB"),
                        raising=False)
    return chiamate


def spia_chiamante(chiamate, nome):
    """Provider/preparatore passato dal chiamante: registra l'uso e lo rifiuta."""
    def chiamata(*args, **kwargs):
        chiamate.append(nome)
        raise AssertionError("ingresso del chiamante usato dal ramo archiviato: " + nome)
    return chiamata


def file_in(cartella):
    cartella = Path(cartella)
    return sorted(str(p.relative_to(cartella)) for p in cartella.rglob("*")) if cartella.exists() else []


def verifica_archiviato(risposta, chiamate, *, cartella=None, prima=None):
    """La risposta e' il contratto dichiarato e nulla del ramo archiviato e' stato toccato."""
    assert risposta == ARCHIVIATO, risposta
    assert chiamate == [], chiamate
    if cartella is not None:
        assert file_in(cartella) == (prima or []), "il ramo archiviato ha scritto file"
