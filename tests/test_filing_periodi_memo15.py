"""P0-8: i numeri mantengono il periodo proprio, mai quello del contenitore."""
from copy import deepcopy

import pytest

from bellomberg.agents import filing_context as fc
from bellomberg.market_data import filing_pipeline as fp
from bellomberg.core.language import language_context


def coppia(anno, fine="12-31", inizio="01-01"):
    return {lato: {"metadati": {"periodo_inizio": f"{y}-{inizio}",
                               "periodo_fine": f"{y}-{fine}"},
                   "url": f"https://example.com/{y}-{fine}", "sha256": str(y) * 16}
            for lato, y in (("prima", anno - 1), ("dopo", anno))}


def numeri():
    return {"stato": "ok", "fonte": "ESEF xBRL-JSON (ifrs-full)", "voci": [
        {"voce": "ricavi", "delta_pct": 12.5, "valuta": "EUR", "tag": "ifrs-full:Revenue"},
        {"voce": "debito", "delta_pct": -2.5, "valuta": "USD", "tag": "ifrs-full:Borrowings"}]}


@pytest.mark.parametrize("fonte", ["esef", "sec"])
def test_annuali_sotto_primario_infrannuale(monkeypatch, tmp_path, fonte):
    annuale, trimestre = coppia(2025), coppia(2026, "06-30", "04-01")
    def singolo(profilo, **kwargs):
        return {"stato": "ok", "coppia": deepcopy(annuale if profilo["tipo"] == "annuale" else trimestre),
                "confronto_corrente": {"stato": "ok", "sezioni_confrontate": ["rischi"]}}
    monkeypatch.setattr(fp, "_esegui_singolo", singolo)
    profilo = {"ticker": "ZZTEST", "cik": "0009990011", "varianti": [
        {"tipo": "annuale", "fonti": ["esef"]}, {"tipo": "trimestrale", "fonti": ["ir"]}]}
    if fonte == "esef":
        from bellomberg.market_data import filing_esef
        monkeypatch.setattr(filing_esef, "numeri_esef", lambda c: numeri())
        profilo["esef_modo"] = "blocchi"
    def fn(cik, c):
        return {**numeri(), "fonte": "SEC companyfacts"} if c == annuale else {"stato": "vuoto"}
    out = fp.esegui_profilo(profilo, archivio=tmp_path, numeri_fn=fn)
    assert out["variante"] == "trimestrale"
    n = out["numeri"]
    assert n["variante"] == "annuale"
    assert n["voci"][0]["periodi"]["dopo"] == {"inizio": "2025-01-01", "fine": "2025-12-31"}
    assert n["voci"][1]["periodi"]["dopo"] == {"fine": "2025-12-31"}
    assert n["documenti"]["dopo"]["url"] == annuale["dopo"]["url"]
    with language_context("it"):
        s = fc.scheda_da_run("ZZTEST", profilo=profilo, run={"result": out}, ultimo=None,
                            freschezza=None, novita_dopo=None)
    assert "30/06/2026" in s["stato"]
    assert "2025-01-01/2025-12-31" in s["numeri"]
    assert "2024-01-01/2024-12-31" in s["numeri"]
    assert "2026" not in s["numeri"]
    assert "EUR" in s["numeri"] and "USD" in s["numeri"]
    assert n["fonte"] in s["numeri"]
    assert "istante" in s["numeri"]


def test_legacy_non_eredita_periodo():
    with language_context("it"):
        riga = fc._riga_numeri(numeri())
    assert "periodo non dichiarato" in riga


def test_periodo_ignoto_e_sequenziale_non_inventati():
    raw = numeri()
    raw["voci"].append({"voce": "metrica_nuova", "delta_pct": 3.0})
    c = coppia(2025)
    c["regola"] = "sequenziale"
    out = fp._numeri(lambda *_: raw, None, c)
    assert out["confronto"] == "trimestre_precedente"
    assert out["voci"][-1]["periodi"] is None
    assert "periodi" not in raw["voci"][0]  # il provider non viene mutato
    with language_context("en"):
        riga = fc._riga_numeri(out)
    assert "period not stated" in riga and "unit not stated" in riga
    assert "previous quarter" in riga


@pytest.mark.parametrize("con_coppia", [True, False])
def test_tool_variante_usa_solo_la_sua_coppia(tmp_path, con_coppia):
    from tests.test_filing_context_v2 import _archivio_n
    annuale = {"tipo": "annuale", "confronto": {"stato": "ok", "cambiamenti": []}}
    if con_coppia:
        annuale["coppia"] = coppia(2025)
    path, _ = _archivio_n(tmp_path, varianti=[annuale])
    out = fc.get_filing_changes("NOVA.DE", db_path=path, variante="annuale", pref_path=tmp_path / "pref.json")
    if con_coppia:
        assert out["pair"]["dopo"]["metadati"]["periodo_fine"] == "2025-12-31"
    else:
        assert out["pair"] is None
        assert out["pair_status"] == "non_disponibile"


def test_inizio_mancante_non_diventa_intero_esercizio():
    c = coppia(2025)
    del c["dopo"]["metadati"]["periodo_inizio"]
    out = fp._numeri(lambda *_: numeri(), None, c)
    with language_context("it"):
        riga = fc._riga_numeri(out)
    assert "ricavi +12,5% [periodo non dichiarato" in riga
    assert "istante 2025-12-31" in riga
