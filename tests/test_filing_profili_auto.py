"""Modelli di profilo SEC: validi per lo store e capaci di confrontare documenti sintetici."""
import pytest

from bellomberg.market_data import filing_pipeline, sec_edgar
from bellomberg.market_data.filing_profili_auto import forme_presenti, profilo_sec, sonda_tipo_6k
from bellomberg.storage.filing_store import _validate_profile
from tests.filing_sec_sintetici import CIK_KORE, CIK_NOVA, allegato_6k, documento_10k, documento_10q, documento_20f
from tests.test_filing_pipeline import rete


def test_usa_annuale_e_trimestrale():
    p = profilo_sec("NOVA.DE", cik=CIK_NOVA, sec_ticker="NOVA", nome="Nova Semiconductors Inc.",
                    origine="nome", forme={"10-K", "10-Q", "8-K"})
    assert p["ticker"] == "NOVA.DE" and p["emittente_id"] == "CIK:" + CIK_NOVA and p["cik"] == CIK_NOVA
    assert [v["tipo"] for v in p["varianti"]] == ["annuale", "trimestrale"]
    assert p["origine_collegamento"] == "nome" and p["sec_ticker"] == "NOVA" and p["fonti"] == ["sec"]
    _validate_profile("NOVA.DE", p, 24)


def test_20f_con_6k_solo_se_sondato():
    senza = profilo_sec("KORE.DE", cik=CIK_KORE, sec_ticker="KORE", nome="Kore Mining plc",
                        origine="confermato_utente", forme={"20-F", "6-K"})
    assert [v["tipo"] for v in senza["varianti"]] == ["annuale"]
    con = profilo_sec("KORE.DE", cik=CIK_KORE, sec_ticker="KORE", nome="Kore Mining plc",
                      origine="confermato_utente", forme={"20-F", "6-K"}, tipo_6k="semestrale")
    assert [v["tipo"] for v in con["varianti"]] == ["annuale", "semestrale"]
    _validate_profile("KORE.DE", con, 24)


def test_solo_6k_diventa_profilo_infrannuale():
    p = profilo_sec("KORE.DE", cik=CIK_KORE, sec_ticker="KORE", nome="Kore Mining plc",
                    origine="nome", forme={"6-K"}, tipo_6k="trimestrale")
    assert p["tipo"] == "trimestrale" and p["sezioni_intero"] is True and p["forme_sec"] == ["6-K"]
    _validate_profile("KORE.DE", p, 24)


def test_nessuna_forma():
    with pytest.raises(ValueError):
        profilo_sec("X.DE", cik="1", sec_ticker="X", nome="X", origine="nome", forme={"8-K"})


def test_forme_presenti_e_sonda():
    assert forme_presenti({"documenti": [{"form": "10-Q"}, {"form": "10-K/A"}, {"form": "6-K"}]}) == {"10-Q", "6-K"}
    assert sonda_tipo_6k("Results for the three months ended Sep 30") == "trimestrale"
    assert sonda_tipo_6k("Second Quarter and Six Months ended June 27, 2026: three and six months ended") == "trimestrale"
    assert sonda_tipo_6k("Half-year results for the six months ended 30 June") == "semestrale"
    assert sonda_tipo_6k("Director dealing") is None


def _caso(form, anno, rischio):
    lungo = "Data center demand drove results. " * 90
    if form == "10-Q":
        return documento_10q(anno, 3, rischio=rischio, gestione=lungo), f"{anno}-09-30", CIK_NOVA
    if form == "10-K":
        return documento_10k(anno, rischio=rischio, gestione=lungo), f"{anno}-12-31", CIK_NOVA
    return documento_20f(anno, rischio=rischio, gestione=lungo, cik=CIK_KORE), f"{anno}-12-31", CIK_KORE


@pytest.mark.parametrize("form", ["10-Q", "10-K", "20-F"])
def test_modello_verifica_e_confronta_documenti_sintetici(monkeypatch, tmp_path, form):
    cik = CIK_NOVA if form != "20-F" else CIK_KORE
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
    righe, pagine = [], {}
    for anno, rischio in ((2025, "New export rules apply to our products. " * 80),
                          (2024, "Demand may weaken in some markets. " * 80)):
        contenuto, report, _ = _caso(form, anno, rischio)
        url = f"{base}{anno}/doc.htm"
        righe.append({"ticker": "NOVA.DE", "form": form, "filed_date": f"{anno + 1}-02-01",
                      "accession": f"{cik}-{anno % 100}-000001", "url": url, "emittente_id": "CIK:" + cik,
                      "issuer": "Synthetic", "report_date": report, "items": [], "fonte": "SEC EDGAR"})
        pagine[url] = contenuto
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})
    rete(monkeypatch, pagine)
    nome = "Kore Mining plc" if form == "20-F" else "Nova Semiconductors Inc."
    p = profilo_sec("NOVA.DE", cik=cik, sec_ticker="NOVA", nome=nome, origine="alias", forme={form})  # G1: v. sotto
    out = filing_pipeline.esegui_profilo(p, archivio=tmp_path / "a", oggi="2026-10-31")
    diff = out["confronto_corrente"]
    assert diff is not None, out["motivi"]
    assert "rischi" in diff["sezioni_confrontate"], diff
    assert any("export" in ((c.get("dopo") or {}).get("testo") or "").lower() for c in diff["cambiamenti"])
    assert "gestione" in diff["sezioni_confrontate"]


def test_modello_6k_trimestre_52_settimane(monkeypatch, tmp_path):
    base = "https://www.sec.gov/Archives/edgar/data/9990011/"
    righe, pagine, allegati = [], {}, {}
    for anno, testo in ((2026, "Outlook lowered on weaker industrial demand."), (2025, "Outlook is unchanged.")):
        acc = f"0009990011-{anno % 100}-000058"
        url = f"{base}{acc.replace('-', '')}/kore-6k_q2x{anno}.htm"
        righe.append({"ticker": "KORE.DE", "form": "6-K", "filed_date": f"{anno}-07-30", "accession": acc,
                      "url": url, "emittente_id": "CIK:" + CIK_KORE, "issuer": "Kore", "report_date": "",
                      "items": [], "fonte": "SEC EDGAR"})
        allegati[acc] = [{"seq": 1, "descrizione": "6-K", "tipo": "6-K", "ixbrl": False, "url": url}]
        pagine[url] = allegato_6k(anno, mesi="three", testo=testo, fine=f"June {27 if anno == 2026 else 28}, {anno}")
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})
    monkeypatch.setattr(sec_edgar, "allegati_filing", lambda cik, acc, **k: allegati[acc])
    rete(monkeypatch, pagine)
    p = profilo_sec("KORE.DE", cik=CIK_KORE, sec_ticker="KORE", nome="Kore Mining plc", origine="alias",
                    forme={"6-K"}, tipo_6k="trimestrale")
    out = filing_pipeline.esegui_profilo(p, archivio=tmp_path / "a", oggi="2026-10-31")
    assert out["confronto_corrente"] is not None, out["motivi"]
    assert out["coppia"]["dopo"]["metadati"]["periodo_fine"] == "2026-06-27"
    assert any("lowered" in ((c.get("dopo") or {}).get("testo") or "") for c in out["confronto_corrente"]["cambiamenti"])


def test_profilo_automatico_per_nome_con_suffisso_resta_da_confermare(tmp_path, monkeypatch):
    # Seguito revisione G1: i profili creati prima della correzione (un .L collegato «per nome» a un
    # altro emittente SEC) non scaricano nulla e lo dichiarano; alias e conferme dell'utente si eseguono.
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", lambda *a, **k: pytest.fail("nessuna rete attesa"))
    for origine in ("nome", "ticker"):
        p = profilo_sec("ACME.L", cik=CIK_NOVA, sec_ticker="ACMI", nome="ACME Holdings plc", origine=origine,
                        forme={"10-Q"})
        out = filing_pipeline.esegui_profilo(p, archivio=tmp_path / origine, oggi="2026-10-31")
        assert out["stato"] == "non_disponibile" and "da confermare" in out["motivi"][0]
    assert filing_pipeline.collegamento_da_confermare(
        profilo_sec("ACME", cik=CIK_NOVA, sec_ticker="ACME", nome="ACME Inc.", origine="ticker", forme={"10-Q"})) is None
    for origine in ("alias", "confermato_utente"):
        assert filing_pipeline.collegamento_da_confermare(
            profilo_sec("ACME.L", cik=CIK_NOVA, sec_ticker="ACMI", nome="ACME", origine=origine, forme={"10-Q"})) is None
