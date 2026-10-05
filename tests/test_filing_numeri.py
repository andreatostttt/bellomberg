from bellomberg.market_data import filing_numeri
from bellomberg.market_data.filing_numeri import numeri_per_coppia, variazioni


def _fatto(start, end, val):
    return {"start": start, "end": end, "val": val, "form": "10-Q"}


FACTS = {"facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [_fatto("2025-07-01", "2025-09-30", 100.0), _fatto("2026-07-01", "2026-09-30", 115.0),
                                   _fatto("2026-01-01", "2026-09-30", 300.0)]}},
    "InventoryNet": {"units": {"USD": [{"end": "2025-09-30", "val": 50.0}, {"end": "2026-09-30", "val": 60.0}]}},
    "OperatingIncomeLoss": {"units": {"USD": [_fatto("2026-07-01", "2026-09-30", 20.0)]}},
}}}
PRIMA, DOPO = ("2025-07-01", "2025-09-30"), ("2026-07-01", "2026-09-30")


def test_variazioni_durata_e_istante():
    out = variazioni(FACTS, PRIMA, DOPO)
    assert out["stato"] == "ok" and out["valuta"] == "USD"
    assert out["voci"] == [{"voce": "ricavi", "prima": 100.0, "dopo": 115.0, "delta_pct": 15.0,
                            "valuta": "USD", "tag": "us-gaap:Revenues"},
                           {"voce": "scorte", "prima": 50.0, "dopo": 60.0, "delta_pct": 20.0,
                            "valuta": "USD", "tag": "us-gaap:InventoryNet"}]


def test_durata_cumulata_non_scambiata_per_trimestre():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fatto("2026-01-01", "2026-09-30", 300.0), _fatto("2025-01-01", "2025-09-30", 250.0)]}}}}}
    assert variazioni(facts, PRIMA, DOPO)["stato"] == "vuoto"


def test_voce_presente_in_un_solo_periodo_esclusa():
    assert "utile_operativo" not in [v["voce"] for v in variazioni(FACTS, PRIMA, DOPO)["voci"]]


def test_ifrs_full_per_i_20f():
    facts = {"facts": {"ifrs-full": {"Revenue": {"units": {"EUR": [_fatto("2024-01-01", "2024-12-31", 80.0),
                                                                    _fatto("2025-01-01", "2025-12-31", 88.0)]}}}}}
    out = variazioni(facts, ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31"))
    assert out["valuta"] == "EUR" and out["voci"][0]["delta_pct"] == 10.0


def test_nessuna_voce():
    assert variazioni({"facts": {}}, ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-12-31"))["stato"] == "vuoto"


def test_base_zero_niente_divisione():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_fatto("a", "b", 0.0), _fatto("c", "d", 5.0)]}}}}}
    assert variazioni(facts, ("a", "b"), ("c", "d"))["voci"][0]["delta_pct"] is None


def test_numeri_per_coppia_usa_companyfacts(monkeypatch):
    visti = []
    monkeypatch.setattr("bellomberg.market_data.sec_xbrl._fetch_companyfacts", lambda cik: visti.append(cik) or FACTS)
    coppia = {"prima": {"metadati": {"periodo_inizio": PRIMA[0], "periodo_fine": PRIMA[1]}},
              "dopo": {"metadati": {"periodo_inizio": DOPO[0], "periodo_fine": DOPO[1]}}}
    out = numeri_per_coppia("9990001", coppia)
    assert visti == ["0009990001"] and out["fonte"] == "SEC companyfacts" and len(out["voci"]) == 2


def test_numeri_per_coppia_senza_companyfacts(monkeypatch):
    monkeypatch.setattr("bellomberg.market_data.sec_xbrl._fetch_companyfacts", lambda cik: None)
    coppia = {"prima": {"metadati": {"periodo_inizio": "a", "periodo_fine": "b"}},
              "dopo": {"metadati": {"periodo_inizio": "c", "periodo_fine": "d"}}}
    assert numeri_per_coppia("1", coppia)["stato"] == "non_disponibile"


def test_servizio_di_default_aggiunge_i_numeri(tmp_path):
    import sqlite3
    from bellomberg.market_data.filing_service import FilingService
    from bellomberg.storage.filing_store import FilingStore, ensure_schema
    path = tmp_path / "f.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    svc = FilingService(FilingStore(path), tmp_path / "archive", indexer=lambda *a: {"status": "skipped"})
    assert svc.pipeline.keywords["numeri_fn"] is filing_numeri.numeri_per_coppia


def test_cache_senza_il_periodo_nuovo_si_riscarica_poi_si_dichiara(monkeypatch, tmp_path):
    # Review finale: companyfacts in cache (7 giorni) puo' non avere ancora il periodo appena depositato.
    from bellomberg.market_data import sec_xbrl
    cache = tmp_path / "CIK0009990001.json"
    cache.write_text("{}")
    monkeypatch.setattr(sec_xbrl, "_cache_path", lambda cik: str(cache))
    chiamate = []

    def fetch(cik, forza=False):
        chiamate.append(forza)
        return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_fatto("2025-07-01", "2025-09-30", 1.0)]}}}}}

    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", fetch)
    coppia = {"prima": {"metadati": {"periodo_inizio": PRIMA[0], "periodo_fine": PRIMA[1]}},
              "dopo": {"metadati": {"periodo_inizio": DOPO[0], "periodo_fine": DOPO[1]}}}
    out = numeri_per_coppia("9990001", coppia)
    # seconda lettura forzata dalla rete; la cache condivisa col DCF resta (revisione 04/10)
    assert chiamate == [False, True] and cache.exists()
    assert out["stato"] == "non_aggiornato" and "2026-09-30" in out["motivo"]
