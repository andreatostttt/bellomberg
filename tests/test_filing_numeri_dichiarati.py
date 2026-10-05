"""Revisione 04/10 (R7): i numeri chiave dichiarano ogni voce scartata e non cancellano la cache
companyfacts condivisa col DCF. Solo numeri e CIK inventati, nessuna rete."""
import json
import os

from bellomberg.market_data import filing_numeri, sec_xbrl
from bellomberg.market_data.filing_numeri import numeri_per_coppia, variazioni

PRIMA, DOPO = ("2025-07-01", "2025-09-30"), ("2026-07-01", "2026-09-30")


def _f(start, end, val):
    return {"start": start, "end": end, "val": val, "form": "10-Q"}


def test_valuta_diversa_scartata_e_dichiarata():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"EUR": [_f(*PRIMA, 100.0)], "USD": [_f(*DOPO, 120.0)]}},
        "InventoryNet": {"units": {"USD": [{"end": PRIMA[1], "val": 5.0}, {"end": DOPO[1], "val": 6.0}]}},
    }}}
    out = variazioni(facts, PRIMA, DOPO)
    assert [v["voce"] for v in out["voci"]] == ["scorte"]
    scarto = next(s for s in out["scarti"] if s["voce"] == "ricavi")
    assert "valut" in scarto["motivo"] and "EUR" in scarto["motivo"] and "USD" in scarto["motivo"]


def test_voci_assenti_dichiarate_con_il_periodo():
    facts = {"facts": {"us-gaap": {"OperatingIncomeLoss": {"units": {"USD": [_f(*DOPO, 20.0)]}}}}}
    out = variazioni(facts, PRIMA, DOPO)
    motivi = {s["voce"]: s["motivo"] for s in out["scarti"]}
    assert set(motivi) == {v for v, _, _ in filing_numeri.VOCI}
    assert PRIMA[1] in motivi["utile_operativo"]  # manca il periodo prima, dichiarato
    assert out["stato"] == "vuoto"


def test_ogni_voce_porta_valuta_e_tag():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_f(*PRIMA, 100.0), _f(*DOPO, 110.0)]}}}}}
    voce = variazioni(facts, PRIMA, DOPO)["voci"][0]
    assert voce["valuta"] == "USD" and voce["tag"] == "us-gaap:Revenues"


def test_nessun_taglio_silenzioso_delle_voci(monkeypatch):
    extra = tuple(("extra%d" % i, "revenue", "durata") for i in range(6))
    monkeypatch.setattr(filing_numeri, "VOCI", filing_numeri.VOCI + extra)
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_f(*PRIMA, 100.0), _f(*DOPO, 110.0)]}}}}}
    out = variazioni(facts, PRIMA, DOPO)
    assert len(out["voci"]) + len(out["scarti"]) == len(filing_numeri.VOCI)


def _coppia():
    return {"prima": {"metadati": {"periodo_inizio": PRIMA[0], "periodo_fine": PRIMA[1]}},
            "dopo": {"metadati": {"periodo_inizio": DOPO[0], "periodo_fine": DOPO[1]}}}


def test_cache_companyfacts_del_dcf_mai_cancellata(monkeypatch, tmp_path):
    cache = tmp_path / "CIK0009990001.json"
    vecchi = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_f(*PRIMA, 1.0)]}}}}}
    cache.write_text(json.dumps(vecchi))
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sec_xbrl, "_cache_path", lambda cik: str(tmp_path / f"CIK{cik}.json"))
    monkeypatch.setattr("bellomberg.market_data.sec_edgar.attendi_sec", lambda **k: None)
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")

    class Giu:
        status_code = 503

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Giu())
    out = numeri_per_coppia("9990001", _coppia())
    assert cache.exists() and json.loads(cache.read_text()) == vecchi  # il DCF tiene la sua cache
    assert out["stato"] == "non_aggiornato" and "riletto" in out["motivo"]


def test_rilettura_riuscita_aggiorna_la_cache_e_lo_dichiara(monkeypatch, tmp_path):
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sec_xbrl, "_cache_path", lambda cik: str(tmp_path / f"CIK{cik}.json"))
    (tmp_path / "CIK0009990001.json").write_text(json.dumps({"facts": {"us-gaap": {}}}))
    monkeypatch.setattr("bellomberg.market_data.sec_edgar.attendi_sec", lambda **k: None)
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    nuovi = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_f(*PRIMA, 100.0), _f(*DOPO, 110.0)]}}}}}

    class Ok:
        status_code = 200

        def json(self):
            return nuovi

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Ok())
    out = numeri_per_coppia("9990001", _coppia())
    assert out["stato"] == "ok" and "riletto" in (out.get("nota_fonte") or "")
    assert json.loads((tmp_path / "CIK0009990001.json").read_text()) == nuovi


# ---------------------------------------------------------------- contesto del comitato


def test_riga_numeri_dichiara_le_voci_non_confrontate():
    from bellomberg.agents import filing_context as fc
    riga = fc._riga_numeri({"stato": "ok", "voci": [{"voce": "scorte", "delta_pct": 20.0}],
                            "scarti": [{"voce": "ricavi", "motivo": "valute diverse fra i periodi (EUR -> USD)"}]})
    assert "non confrontate: ricavi (valute diverse fra i periodi (EUR -> USD))" in riga


def test_righe_dei_numeri_tolte_dal_budget_dichiarate_nel_piede():
    from bellomberg.agents import filing_context as fc

    def scheda(t):
        return {"ticker": t, "gruppo": 1, "peso": 1.0, "limite": 2, "stato": f"{t} · stato",
                "numeri": "numeri " + "x" * 300, "altra_variante": None, "totale_cambiamenti": 0,
                "cambiamenti": []}

    out = fc.impagina([scheda("AAA.DE"), scheda("BBB.DE")], max_caratteri=380, intestazione="CONTESTO")
    assert "numeri xxx" not in out["testo"]
    assert "Righe dei numeri omesse: AAA.DE, BBB.DE." in out["testo"]
    assert out["caratteri"] == len(out["testo"])


# ---------------------------------------------------------------- REV_G2a R-6


def test_base_zero_dichiarata():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_f(*PRIMA, 0.0), _f(*DOPO, 5.0)]}}}}}
    out = variazioni(facts, PRIMA, DOPO)
    scarto = next(s for s in out["scarti"] if s["voce"] == "ricavi")
    assert "base zero" in scarto["motivo"]
    from bellomberg.agents import filing_context as fc
    assert "ricavi (" in fc._riga_numeri(out) or "non disponibili" in fc._riga_numeri(out)


def test_motivi_degli_scarti_in_inglese_nel_contesto_inglese():
    from bellomberg.core.language import language_context
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"EUR": [_f(*PRIMA, 100.0)], "USD": [_f(*DOPO, 120.0)]}},
        "InventoryNet": {"units": {"USD": [{"end": DOPO[1], "val": 6.0}]}},
        "OperatingIncomeLoss": {"units": {"USD": [_f(*PRIMA, 0.0), _f(*DOPO, 2.0)]}}}}}
    with language_context("en"):
        motivi = {s["voce"]: s["motivo"] for s in variazioni(facts, PRIMA, DOPO)["scarti"]}
    assert "different currencies" in motivi["ricavi"]
    assert "missing in period" in motivi["scorte"]
    assert "zero base" in motivi["utile_operativo"]
