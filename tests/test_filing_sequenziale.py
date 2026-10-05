"""Fase F: emittente SEC nuovo, trimestre su trimestre finche' manca l'omologo dell'anno prima.

Caso sintetico (esercizio chiuso a giugno, quotato a meta' 2025): 10-Q set 2025, dic 2025,
mar 2026, 10-K giu 2026. Il primo omologo anno su anno arriva col 10-Q di set 2026.
"""
from datetime import datetime, timezone

from bellomberg.agents import filing_context as fc
from bellomberg.core.language import language_context
from bellomberg.market_data import filing_pipeline, sec_edgar
from bellomberg.market_data.filing_profili_auto import profilo_sec
from tests.filing_sec_sintetici import CIK_NOVA, documento_10k, documento_10q
from tests.test_filing_pipeline import rete

BASE = "https://www.sec.gov/Archives/edgar/data/9990001/"
LUNGO = "Data center demand drove results. " * 90
# (form, anno, trimestre o None, fine periodo, depositato, rischio)
DEPOSITI = [("10-K", 2026, None, "2026-06-30", "2026-08-20", "Supply may tighten. "),
            ("10-Q", 2026, 1, "2026-03-31", "2026-05-10", "Export rules may tighten. "),
            ("10-Q", 2025, 4, "2025-12-31", "2026-02-10", "Demand may weaken. "),
            ("10-Q", 2025, 3, "2025-09-30", "2025-11-10", "Demand may weaken slightly. ")]
OMOLOGO = ("10-Q", 2026, 3, "2026-09-30", "2026-11-10", "Export rules tightened. ")


def _catalogo(monkeypatch, depositi):
    righe, pagine = [], {}
    for i, (form, anno, trim, fine, depositato, rischio) in enumerate(depositi):
        url = f"{BASE}{fine.replace('-', '')}/{form.lower()}.htm"
        righe.append({"ticker": "NOVA.DE", "form": form, "filed_date": depositato,
                      "accession": f"0009990001-{fine[2:4]}-{i:06d}", "url": url, "emittente_id": "CIK:" + CIK_NOVA,
                      "issuer": "Synthetic", "report_date": fine, "items": [], "fonte": "SEC EDGAR"})
        pagine[url] = (documento_10q(anno, trim, rischio=rischio * 80, gestione=LUNGO) if form == "10-Q"
                       else documento_10k(anno, rischio=rischio * 80, gestione=LUNGO, periodo=("2025-07-01", fine)))
    righe.sort(key=lambda r: r["filed_date"], reverse=True)
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})
    rete(monkeypatch, pagine)


def _profilo(origine="alias"):  # G1: con suffisso un profilo per «ticker»/«nome» resta da confermare
    return profilo_sec("NOVA.DE", cik=CIK_NOVA, sec_ticker="NOVA", nome="Nova Semiconductors Inc.",
                       origine=origine, forme={"10-K", "10-Q"})


def _numeri(cik, coppia):
    return {"stato": "ok", "valuta": "USD", "fonte": "SEC companyfacts",
            "periodi": [coppia[k]["metadati"]["periodo_fine"] for k in ("prima", "dopo")],
            "voci": [{"voce": "ricavi", "prima": 100.0, "dopo": 104.0, "delta_pct": 4.0}]}


def test_emittente_nuovo_trimestre_su_trimestre_dichiarato(monkeypatch, tmp_path):
    _catalogo(monkeypatch, DEPOSITI)
    out = filing_pipeline.esegui_profilo(_profilo(), archivio=tmp_path / "a", oggi="2026-09-01", numeri_fn=_numeri)
    assert out["variante"] == "trimestrale"
    coppia = out["coppia"]
    assert coppia["regola"] == "sequenziale" and coppia["ambito"] == "ultimo_verificato"
    assert [coppia[k]["metadati"]["periodo_fine"] for k in ("prima", "dopo")] == ["2025-12-31", "2026-03-31"]
    diff = out["confronto_corrente"]
    assert diff["regola"] == "sequenziale" and filing_pipeline.SEQUENZIALE_LIMITE in diff["limiti"]
    assert "trimestre su trimestre" in filing_pipeline.SEQUENZIALE_LIMITE
    assert any("export" in ((c.get("dopo") or {}).get("testo") or "").lower() for c in diff["cambiamenti"])
    # numeri della stessa coppia, etichettati: mai l'anno prima in silenzio
    assert out["numeri"]["confronto"] == "trimestre_precedente"
    assert out["numeri"]["periodi"] == ["2025-12-31", "2026-03-31"]
    trimestrale = next(v for v in out["varianti"] if v["tipo"] == "trimestrale")
    annuale = next(v for v in out["varianti"] if v["tipo"] == "annuale")
    assert trimestrale["regola"] == "sequenziale"
    # il 10-K non si confronta mai col 10-Q: la variante annuale resta senza coppia
    assert annuale["coppia_periodi"] is None and "regola" not in annuale


def test_omologo_presente_torna_anno_su_anno(monkeypatch, tmp_path):
    _catalogo(monkeypatch, [OMOLOGO] + DEPOSITI)
    out = filing_pipeline.esegui_profilo(_profilo(), archivio=tmp_path / "a", oggi="2026-11-15", numeri_fn=_numeri)
    coppia = out["coppia"]
    assert "regola" not in coppia
    assert [coppia[k]["metadati"]["periodo_fine"] for k in ("prima", "dopo")] == ["2025-09-30", "2026-09-30"]
    assert "regola" not in out["confronto_corrente"]
    assert filing_pipeline.SEQUENZIALE_LIMITE not in out["confronto_corrente"]["limiti"]
    assert "confronto" not in out["numeri"]


def test_omologo_atteso_ma_non_verificato_niente_scorciatoia(monkeypatch, tmp_path):
    # Il catalogo ha il 10-Q di mar 2025 (omologo di mar 2026) ma non e' verificabile:
    # l'emittente non e' nuovo, quindi nessun confronto col trimestre di dicembre.
    vecchio = ("10-Q", 2025, 1, "2025-03-31", "2025-05-10", "Demand may weaken. ")
    _catalogo(monkeypatch, [d for d in DEPOSITI if d[0] == "10-Q"] + [vecchio])
    rete(monkeypatch, {**{f"{BASE}{d[3].replace('-', '')}/10-q.htm": documento_10q(d[1], d[2], rischio=d[5] * 80, gestione=LUNGO)
                          for d in DEPOSITI if d[0] == "10-Q"},
                       f"{BASE}20250331/10-q.htm": b"<html><body>illeggibile</body></html>"})
    out = filing_pipeline.esegui_profilo(_profilo(), archivio=tmp_path / "a", oggi="2026-09-01")
    trimestrale = next(v for v in out["varianti"] if v["tipo"] == "trimestrale")
    assert out["coppia"] is None and trimestrale["coppia_periodi"] is None
    assert any("omologo dell'anno immediatamente precedente non disponibile" in m for m in trimestrale["motivi"])


def test_profilo_manuale_mai_sequenziale(monkeypatch, tmp_path):
    _catalogo(monkeypatch, DEPOSITI)
    for origine in ("manuale", "proposta_ai"):
        out = filing_pipeline.esegui_profilo(_profilo(origine), archivio=tmp_path / origine, oggi="2026-09-01")
        assert out["coppia"] is None
        assert any("omologo dell'anno immediatamente precedente non disponibile" in m for m in out["motivi"])
    senza_origine = {k: v for k, v in _profilo().items() if k != "origine_collegamento"}
    out = filing_pipeline.esegui_profilo(senza_origine, archivio=tmp_path / "x", oggi="2026-09-01")
    assert out["coppia"] is None


def test_trimestri_non_adiacenti_niente_confronto(monkeypatch, tmp_path):
    # Manca il 10-Q di dicembre: set 2025 e mar 2026 non sono trimestri consecutivi.
    _catalogo(monkeypatch, [d for d in DEPOSITI if d[3] != "2025-12-31"])
    out = filing_pipeline.esegui_profilo(_profilo(), archivio=tmp_path / "a", oggi="2026-09-01")
    assert out["coppia"] is None


def _run(regola=True):
    meta = lambda d: {"metadati": {"periodo_fine": d}}
    numeri = {"stato": "ok", "voci": [{"voce": "ricavi", "delta_pct": 4.0}]}
    coppia = {"prima": meta("2025-12-31"), "dopo": meta("2026-03-31")}
    if regola:
        coppia["regola"] = "sequenziale"
        numeri["confronto"] = "trimestre_precedente"
    return {"id": 9, "status": "ok", "finished_at": "2026-09-01T08:00:00+00:00", "reason": None,
            "result": {"stato": "ok", "variante": "trimestrale", "coppia": coppia, "numeri": numeri,
                       "confronto_corrente": {"stato": "ok", "cambiamenti": [], "regola": "sequenziale"}}}


def test_contesto_dichiara_trimestre_su_trimestre():
    profilo = {"cik": CIK_NOVA, "varianti": [{"tipo": "trimestrale", "forme_sec": ["10-Q"]}]}
    kw = dict(profilo=profilo, ultimo=None, escluso=False, freschezza={"stato": "aggiornato"},
              novita_dopo=datetime(2026, 10, 1, tzinfo=timezone.utc))
    s = fc.scheda_da_run("NOVA.DE", run=_run(), **kw)
    assert "trimestre al 31/03/2026 vs 31/12/2025 · trimestre su trimestre (manca l'anno prima)" in s["stato"]
    assert s["numeri"] == "numeri vs trimestre precedente: ricavi +4,0%"
    with language_context("en"):
        s = fc.scheda_da_run("NOVA.DE", run=_run(), **kw)
    assert "quarter to 31/03/2026 vs 31/12/2025 · quarter on quarter (no prior year)" in s["stato"]
    assert s["numeri"] == "figures vs previous quarter: ricavi +4.0%"
    s = fc.scheda_da_run("NOVA.DE", run=_run(regola=False), **kw)
    assert "trimestre su trimestre" not in s["stato"] and s["numeri"] == "numeri ricavi +4,0%"
