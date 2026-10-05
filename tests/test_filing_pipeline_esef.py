"""Pipeline ESEF a blocchi (fase B, task 5): catalogo, lingua, duplicati, cache, obsolescenza, numeri.

Indice e download finti, nessuna rete. Dati sintetici.
"""
import pytest

from bellomberg.market_data import esef, filing_pipeline
from bellomberg.market_data.filing_profili_auto import profilo_esef
from tests.filing_esef_sintetici import LEI_NOVA, blocchi_nova, json_nova, riga_indice, xbrl_json
from tests.test_filing_pipeline import rete


@pytest.fixture(autouse=True)
def _contatto(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    monkeypatch.setattr(esef, "attendi_esef", lambda **k: None)


@pytest.fixture
def profilo():
    return profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en")


def _indice(monkeypatch, righe, origine="rete"):
    chiamate = []

    def indice(lei, **k):
        chiamate.append(lei)
        return {"righe": righe, "origine": origine, "motivo": None if origine == "rete" else "rete giu'"}

    monkeypatch.setattr(esef, "indice_depositi", indice)
    return chiamate


def _esegui(profilo, tmp_path, oggi="2026-10-03"):
    return filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "archivio", oggi=oggi)


def test_coppia_in_inglese_con_italiano_presente(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(1, 2024, lingua="it"), riga_indice(2, 2024), riga_indice(3, 2025, lingua="it"), riga_indice(4, 2025)]
    _indice(monkeypatch, righe)
    chiamate = rete(monkeypatch, {righe[1]["json_url"]: json_nova(2024), righe[3]["json_url"]: json_nova(2025)})
    out = _esegui(profilo, tmp_path)
    assert sorted(chiamate) == sorted([righe[1]["json_url"], righe[3]["json_url"]])  # mai l'italiano
    assert out["stato"] == "ok", out["motivi"]
    diff = out["confronto_corrente"]
    assert diff["stato"] == "ok" and diff["cambiamenti"]
    assert [d["metadati"]["periodo_fine"] for d in (out["coppia"]["prima"], out["coppia"]["dopo"])] == ["2024-12-31", "2025-12-31"]
    assert out["numeri"]["stato"] == "ok" and out["numeri"]["fonte"].startswith("ESEF")
    assert any(v["voce"] == "ricavi" and v["delta_pct"] == 15.0 for v in out["numeri"]["voci"])


def test_lingua_preferita_assente_in_un_anno_usa_l_originale(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(1, 2024, lingua="it"), riga_indice(3, 2025, lingua="it"), riga_indice(4, 2025)]
    _indice(monkeypatch, righe)
    rete(monkeypatch, {righe[0]["json_url"]: json_nova(2024, lingua="it"),
                       righe[1]["json_url"]: json_nova(2025, lingua="it")})
    out = _esegui(profilo, tmp_path)
    assert out["coppia"]["dopo"]["metadati"]["lingua"] == "it", out["motivi"]
    assert any("lingua" in l and "it" in l for l in out["copertura"]["limiti"])


def test_ricaricamento_duplicato_vince_l_ultimo(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(2, 2024), riga_indice(5, 2025, aggiunto="2026-04-01 10:00:00"),
             riga_indice(6, 2025, aggiunto="2026-05-01 10:00:00")]
    righe[2]["json_url"] = righe[2]["json_url"].replace(".json", "-v2.json")
    _indice(monkeypatch, righe)
    chiamate = rete(monkeypatch, {righe[0]["json_url"]: json_nova(2024), righe[2]["json_url"]: json_nova(2025)})
    out = _esegui(profilo, tmp_path)
    assert righe[1]["json_url"] not in chiamate and out["stato"] == "ok", out["motivi"]
    assert any("duplicat" in l for l in out["copertura"]["limiti"])


def test_deposito_senza_json_dichiarato(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(1, 2023, json=False), riga_indice(2, 2024), riga_indice(4, 2025)]
    _indice(monkeypatch, righe)
    rete(monkeypatch, {righe[1]["json_url"]: json_nova(2024), righe[2]["json_url"]: json_nova(2025)})
    out = _esegui(profilo, tmp_path)
    assert out["stato"] == "ok"
    assert any("FY2023" in l and "senza xBRL-JSON" in l for l in out["copertura"]["limiti"])


def test_solo_tre_periodi_piu_recenti(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(i, a) for i, a in ((1, 2021), (2, 2022), (3, 2023), (4, 2024), (5, 2025))]
    _indice(monkeypatch, righe)
    pagine = {r["json_url"]: json_nova(a) for r, a in zip(righe, (2021, 2022, 2023, 2024, 2025)) if a >= 2023}
    chiamate = rete(monkeypatch, pagine)
    out = _esegui(profilo, tmp_path)
    assert len(out["candidati"]) == 3 and set(chiamate) <= set(pagine)
    assert any("3 esercizi" in l for l in out["copertura"]["limiti"])


def test_repository_fermo_non_e_corrente(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(1, 2021), riga_indice(2, 2022)]
    _indice(monkeypatch, righe)
    rete(monkeypatch, {righe[0]["json_url"]: xbrl_json(2021, blocchi=blocchi_nova(2024)),
                       righe[1]["json_url"]: xbrl_json(2022, blocchi=blocchi_nova(2025))})
    out = _esegui(profilo, tmp_path)
    assert out["stato"] == "parziale"
    assert any("fermo all'esercizio FY2022" in m for m in out["motivi"]), out["motivi"]


def test_secondo_run_riusa_i_json_archiviati(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    _indice(monkeypatch, righe)
    pagine = {righe[0]["json_url"]: json_nova(2024), righe[1]["json_url"]: json_nova(2025)}
    chiamate = rete(monkeypatch, pagine)
    _esegui(profilo, tmp_path)
    assert len(chiamate) == 2
    out = _esegui(profilo, tmp_path)
    assert len(chiamate) == 2 and out["stato"] == "ok"


def test_archivio_alterato_si_riscarica(monkeypatch, tmp_path, profilo):
    import json as _json
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    _indice(monkeypatch, righe)
    chiamate = rete(monkeypatch, {righe[0]["json_url"]: json_nova(2024), righe[1]["json_url"]: json_nova(2025)})
    _esegui(profilo, tmp_path)
    indice = _json.loads((tmp_path / "archivio" / filing_pipeline.INDICE_ESEF).read_text())
    from pathlib import Path
    Path(indice[righe[0]["json_url"]]["path"]).unlink()
    out = _esegui(profilo, tmp_path)
    assert chiamate.count(righe[0]["json_url"]) == 2 and out["stato"] == "ok"


def test_indice_da_cache_scaduta_dichiarato(monkeypatch, tmp_path, profilo):
    righe = [riga_indice(2, 2024), riga_indice(4, 2025)]
    _indice(monkeypatch, righe, origine="cache_scaduta")
    rete(monkeypatch, {righe[0]["json_url"]: json_nova(2024), righe[1]["json_url"]: json_nova(2025)})
    out = _esegui(profilo, tmp_path)
    assert out["stato"] == "parziale" and any("rete giu'" in m for m in out["motivi"])
    assert not filing_pipeline.esito_completo(out)


def test_indice_illeggibile_e_errore_della_fonte(monkeypatch, tmp_path, profilo):
    def giu(lei, **k):
        raise RuntimeError("indice ESEF non disponibile")

    monkeypatch.setattr(esef, "indice_depositi", giu)
    out = _esegui(profilo, tmp_path)
    assert out["fonti"][0]["stato"] == "errore" and filing_pipeline.incompleto_transitorio(out)


def test_profilo_esef_manuale_resta_sul_report(monkeypatch, tmp_path):
    """Senza esef_modo il ramo storico (report HTML da _list_filings) e' invariato."""
    visti = []
    monkeypatch.setattr(esef, "_list_filings", lambda lei, **k: visti.append(lei) or [])
    monkeypatch.setattr(esef, "indice_depositi", lambda *a, **k: pytest.fail("indice a blocchi non atteso"))
    p = {"ticker": "NOVA.MI", "emittente_id": f"LEI:{LEI_NOVA}", "lingua": "en", "tipo": "annuale",
         "perimetro": "consolidato", "fonti": ["esef"], "verifica": {"lingua": "the", "tipo": "annual", "perimetro": "consolidated"},
         "sezioni": {"rischi": {"inizio": "risks", "fine": "outlook"}}}
    filing_pipeline.esegui_profilo(p, archivio=tmp_path / "a", oggi="2026-10-03")
    assert visti == [LEI_NOVA]
